"""Gedaechtnis Stufe 3 - Schattenmodus Phase 1 (2.6, Bugs-DB 2603, docs/MEMORY.md 12/13).

Prueft: Vektoren tests/vectors/memory_shadow.json (Startgewicht, w_eff, Verstaerkung, Hub, Ausbreitung auf festem Graph mit
sensiblem Knoten/Schnappschuss/Hub, Gleichstand, Rueckweg, Obergrenze, neu/neu_limit/verdraengt, gemeinsam); aus -> keine
Spur, keine Links; Einschalten ohne Bindung 409; Einschalten zieht nach (K, Schwelle, a < b, ohne Schnappschuesse/sensible)
+ CLI verknuepfen; 20 Fragen ein/aus: treffer byteweise gleich (ms.schatten nur an); Spur ohne Fragetext (keine Spalte),
Stichworte, HMAC stabil, Server-Log ohne Frage; recall_used (Vektorfolge, ignoriert, genutzt, neues Paar, 2-h-Fenster);
10 recalls ohne recall_used -> Gewichte unveraendert; Auftauchen 3x legt Link "gemeinsam" an (2x nicht); sensibel; lazy
ohne Schreiben (now); 30-Tage-Aufraeumen + Links zu geloeschten Zeilen; forget_traces; aus behaelt Links/Spuren;
Konto loeschen; persoenliches Backup (memory.shadow kommt an, Links/Spuren nicht); Fehler eingeschleust -> recall ok;
Bericht (Schluessel, Zahlen, deterministische Stichproben, Grenzen, "(gelöscht)", aus = Nullen) ueber REST/MCP/Rate;
Leistung 5 000 Zeilen / 25 000 Kanten: ms.schatten p90 <= 25 ms, Suche <= 150 ms.
Wegwerf-Konten gx-*, erfundene Testdaten, Fake-Embedding tests/fakeai.py (MEM_FAKE_PORT, Standard 8905).
Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> gedaechtnis_schatten.py"""
import base64
import json
import os
import secrets
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import db

db.wait_ready()
import auth  # noqa: E402
import numpy as np  # noqa: E402
from saga import api_struct, api_v1, apitok, memory, memory_shadow as msh  # noqa: E402
from saga.api_v1 import Fail  # noqa: E402
from saga.core import _tok_hash, set_user_lang  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
B_URL = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc if "BASE_URL" in os.environ else None
FP = int(os.environ.get("MEM_FAKE_PORT", "8905"))
SUF = secrets.token_hex(3)
U, R, Z = f"gx-uli-{SUF}", f"gx-rita-{SUF}", f"gx-zoe-{SUF}"
res, INFO, S = [], [], {}
D = 86400


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:500], flush=True)


def note(s):
    INFO.append(s)
    print("INFO", s, flush=True)


def pg(sql, args=()):
    with db.connect() as c, c:
        return c.execute(sql, args).fetchall()


def cookie(uid):
    from itsdangerous import TimestampSigner
    s = TimestampSigner(os.environ["SESSION_SECRET"])
    return s.sign(base64.b64encode(json.dumps({"uid": uid, "name": uid, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, b=None, uid=None, bearer=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if HOST:
        h["Host"] = HOST
    if uid:
        h["Cookie"] = "saga=" + cookie(uid)
        h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
        h["MCP-Protocol-Version"] = "2025-06-18"
    try:
        with urllib.request.urlopen(urllib.request.Request(B_URL + p, data=json.dumps(b).encode() if b is not None else None,
                                                           method=m, headers=h), timeout=60) as r:
            body, st = r.read(), r.status
    except urllib.error.HTTPError as e:
        body, st = e.read(), e.code
    try:
        return st, json.loads(body or b"null")
    except ValueError:
        return st, {"_raw": body[:300].decode("utf-8", "replace")}


def token(uid, scopes):
    raw = apitok.PREFIX + secrets.token_urlsafe(32)
    pg("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires) VALUES (?,?,?,?,?,?,?,?)",
       (uid, "gx-test", _tok_hash(raw), raw[:12], json.dumps(apitok.norm_scopes(scopes)), None, time.time(), time.time() + 3600))
    return raw


def tok_obj(uid, n=0):
    return apitok.Tok(10 ** 9 + 26030 + n, uid, "gx", "sga_gx", apitok.SCOPES, None, None)


def set_embed(model="fake-embed", enabled=True, wait=False):
    auth.save_setting("ai_embed", {"enabled": enabled, "provider": "openai", "url": f"http://127.0.0.1:{FP}/v1", "model": model,
                                   "api_key": "", "keep_alive": "30m", "timeout": 15, "query_timeout_ms": 1200, "pinned": ["127.0.0.1"]})
    memory.forget()
    memory._qcache.clear()
    if wait:
        time.sleep(5.4)


PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Art", "type": "select", "options": ["Fakt", "Schnappschuss"]},
         {"name": "Wichtigkeit", "type": "number"}, {"name": "Datum", "type": "date"}, {"name": "Sensibel", "type": "checkbox"}]


def fail_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except Fail as f:
        return f


def links(uid):
    return {(a, b): (round(g, 9), round(u, 9), t, round(z, 3), her) for a, b, g, u, t, z, her in
            pg("SELECT a, b, gewicht, untergrenze, treffer, zuletzt, herkunft FROM mem_link WHERE uid=?", (uid,))}


def traces(uid):
    return pg("SELECT id, at, quelle, frage_hash, stichworte, lim, treffer, neu, genutzt, gekappt, zeichen, daten FROM mem_trace "
              "WHERE uid=? ORDER BY id", (uid,))


def excl(uid):
    with db.connect() as c:
        b = memory.binding(uid, c)
        cols, _ = memory.columns(memory._db_state(c, uid, b["db"])[0], b["map"])
        return memory.excluded(c, uid, b["db"], cols, b["map"])


# --- 1. Vektoren ---------------------------------------------------------------------------------------------------
def part_vectors():
    with open(os.path.join(HERE, "vectors", "memory_shadow.json"), encoding="utf-8") as f:
        V = json.load(f)
    K = V["konst"]
    ok("Vektoren: Konstanten gleich", (msh.K, msh.COS_MIN, msh.TRGM_MIN, msh.U0, msh.HL, msh.DELTA, msh.A_MIN, msh.CAP, msh.SEEDS_MAX,
                                       msh.G_GEM, msh.U_GEM, msh.GEM_N, msh.GEM_DAYS, msh.G_USED, msh.U_USED, msh.STRENGTH, msh.U_STEP, msh.U_MAX)
       == (K["k"], K["cos_min"], K["trgm_min"], K["u0"], K["hl_tage"] * D, K["delta"], K["a_min"], K["cap"], K["seeds_max"],
           K["gemeinsam_g"], K["gemeinsam_u"], K["gemeinsam_spuren"], K["gemeinsam_tage"], K["genutzt_g"], K["genutzt_u"], K["staerken"],
           K["u_schritt"], K["u_max"]))
    close = lambda a, b: (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1e-6)  # noqa: E731
    bad = [x for x in V["start"] if not close((msh.g0_cos if x["quelle"] == "embeddings" else msh.g0_trgm)(x["rel"]), x["g0"])]
    ok(f"Vektoren: {len(V['start'])} Startgewichte (0,55/0,775/1,0/Trigramme)", not bad, bad)
    bad = [x["name"] for x in V["w_eff"] if not close(msh.w_eff(**x["in"]), x["out"])]
    arr = msh.w_eff(np.array([x["in"]["g"] for x in V["w_eff"]]), np.array([x["in"]["u"] for x in V["w_eff"]]),
                    np.array([x["in"]["zuletzt"] for x in V["w_eff"]]), np.array([x["in"]["gepinnt"] for x in V["w_eff"]]), V["now"])
    ok("Vektoren: w_eff (0, H_L, 2 H_L, Untergrenze, gepinnt, rueckwaerts) - Skalar und Array", not bad
       and all(close(float(a), x["out"]) for a, x in zip(arr, V["w_eff"])), bad)
    cur, bad = dict(V["verstaerken"]["start"]), []
    for st in V["verstaerken"]["schritte"]:
        nx = msh.reinforce(cur, st["t"])
        if not all(close(nx[k], st["out"][k]) for k in ("g", "u")) or nx["treffer"] != st["out"]["treffer"] or nx["zuletzt"] != st["out"]["zuletzt"]:
            bad.append((st, nx))
        cur = dict(nx, gepinnt=False)
    nw = msh.reinforce(None, V["verstaerken"]["neu"]["t"])
    ok("Vektoren: Verstaerkung 3 Schritte mit Pausen + neues Paar", not bad and all(close(nw[k], V["verstaerken"]["neu"]["out"][k]) for k in ("g", "u"))
       and nw["treffer"] == 1, bad)
    bad = [x for x in V["hub"] if not close(msh.hub(x["N"], x["deg"]), x["h"])]
    ok("Vektoren: Hub-Daempfung", not bad, bad)
    for sc in V["ausbreitung"]:
        o = sc["out"]
        got = spread_case(sc)
        diff = {k: (got[k], o[k]) for k in o if k in got and got[k] != o[k]}
        ok(f"Vektoren Ausbreitung „{sc['name'][:60]}“: w_eff, Grad, h, A/via/hop, Basis, Elemente, Schatten-Menge, Obergrenze, "
           "neu/neu_limit/verdraengt", not diff, diff)
    bad = []
    for x in V["gemeinsam"]:
        got = [list(p) for p in msh.gemeinsam_pairs(x["aktuell"], [s["treffer"] for s in x["spuren"] if s["at"] >= x["now"] - 30 * D]
                                                     + [x["aktuell"]], x["vorhanden"])]
        if got != x["out"]:
            bad.append((x["name"], got))
    ok("Vektoren: gemeinsam 2 vs. 3 Spuren, 30 Tage, vorhandener Link", not bad, bad)
    ok("Stichproben-Indizes: round(i(m-1)/(n-1)), deterministisch", msh.sample_idx(10, 4) == [0, 3, 6, 9] and msh.sample_idx(3, 8) == [0, 1, 2]
       and msh.sample_idx(5, 1) == [0] and msh.sample_idx(0, 8) == [])


def r6(x):
    return round(float(x), 6)


def spread_case(sc):
    """Szenario mit den Bausteinen von saga.memory_shadow (wie _evaluate) nachrechnen."""
    nodes = {x["id"]: x for x in sc["knoten"]}
    w = memory.WEIGHTS
    gate = w["rel_min_trgm"] if sc["quelle"] == "trigramm" else w["rel_min"]
    snap = {i for i, x in nodes.items() if x["schnappschuss"]}
    sens = {i for i, x in nodes.items() if x["sensibel"]}
    bad = snap | sens
    G = msh.Graph(sorted((a, b, g, u, z, p) for a, b, g, u, z, p in sc["links"]))
    allids = sorted(nodes)
    valid = np.array([int(i) not in bad and int(i) in nodes for i in G.ids], dtype=bool)
    weff = msh.w_eff(G.g, G.u, G.zul, G.pin, sc["now"])
    deg = msh.degrees(G, valid)
    h = msh.hub(sc["N"], deg)
    t = msh.transfer(G, weff, h)
    seeds = sorted((i for i, x in nodes.items() if x["rel"] >= gate and i not in bad), key=lambda i: (-nodes[i]["rel"], i))[:msh.SEEDS_MAX]
    seed_rel = np.zeros(len(G.ids))
    for i in seeds:
        k = int(G.idx([i])[0])
        if k >= 0:
            seed_rel[k] = nodes[i]["rel"]
    A, via, hop = msh.spread(G, t, valid, seed_rel)
    akt = {i: [0.0, None, 0] for i in allids}
    gi = {int(i): k for k, i in enumerate(G.ids)}
    for i in allids:
        k = gi.get(i)
        if k is not None and A[k] > 0:
            akt[i] = [r6(A[k]), int(via[k]), int(hop[k])]
    hh = {str(i): r6(h[gi[i]]) if i in gi else r6(msh.hub(sc["N"], 0)) for i in allids}
    dd = {str(i): int(deg[gi[i]]) if i in gi else 0 for i in allids}

    def fs(i, rel):
        x = nodes[i]
        fa = memory.factors(rel, sc["now"], x["created"], x["hits"], x["last_hit"], None, x["datum"], x["wichtigkeit"], None, None, w)
        return fa, memory.score(fa, w)
    items = []
    for i, x in nodes.items():
        if i in snap or x["rel"] < gate:
            continue
        fa, s = fs(i, x["rel"])
        if memory.is_hit(fa, s, w, sc["quelle"]):
            items.append((s, fa["rel"], i))
    basis = [z[2] for z in memory.rank(items, sc["lim"])]
    elems, plain, lengths = [], {}, {}
    for i, x in nodes.items():
        if i in snap:
            continue
        a = akt[i][0] if i not in sens else 0.0
        a = float(A[gi[i]]) if i in gi and a else 0.0
        if x["rel"] < gate and a <= 0:
            continue
        rs = min(1.0, max(0.0, x["rel"]) + a)
        _fa, g = fs(i, rs)
        fp, sp = fs(i, x["rel"])
        plain[i] = memory.is_hit(fp, sp, w, sc["quelle"])
        lengths[i] = x["titel_len"] + min(x["inhalt_len"], 2000)
        if rs >= gate and g >= w["score_min"]:
            elems.append((g, rs, i))
    elems.sort(key=lambda z: (-z[0], -z[1], z[2]))
    kept, cut, total = msh.cap(elems, lengths)
    neu, lim, verd = msh.classify(kept, basis, plain)
    return {"w_eff": [[int(G.ids[G.ai[k]]), int(G.ids[G.bi[k]]), r6(weff[k])] for k in range(len(G.g))], "grad": dd, "h": hh,
            "seeds": seeds, "akt": {str(i): v for i, v in akt.items()}, "basis": basis, "elemente": [[i, r6(rs), r6(g)] for g, rs, i in elems],
            "schatten": kept, "gekappt": cut, "zeichen": total, "neu": neu, "neu_limit": lim, "verdraengt": verd,
            "sensibel": sorted(sens), "schnappschuss": sorted(snap)}


# --- 2. Integration -------------------------------------------------------------------------------------------------
# Schluessel, Inhalt (je 5 Woerter; verwandte Zeilen teilen 3-4 -> Kosinus 0,6-0,8 im Fake-Embedding). Titel = kurzer Code
# aus 2 Zeichen (zaehlt im Fake-Embedding nicht mit)
ROWS = [("Tomaten", "tomaten sonne wasser garten beet"), ("Basilikum", "tomaten sonne garten beet basilikum"),
        ("Kompost", "kompost garten beet erde sonne"), ("Hochbeet", "hochbeet erde garten herbst laub"),
        ("Nachtzug", "nachtzug wien reise bett schlafwagen"), ("Wien", "wien reise strassenbahn kaffee museum"),
        ("Packliste", "packliste reise kabel adapter koffer"), ("Fahrrad", "fahrrad kette reise oel kilometer"),
        ("Kaffee", "kaffee bohnen grad wasser muehle"), ("Sauerteig", "sauerteig brot mehl wasser ruhe"),
        ("Brot", "brot mehl ofen grad sauerteig"), ("Gewuerze", "gewuerze dunkel luftdicht kueche glas"),
        ("Proxmox", "proxmox cluster ceph knoten speicher"), ("Ceph", "ceph speicher osd cluster knoten"),
        ("Backup", "backup restic speicher cluster ceph"), ("Netz", "netz vlan switch router knoten"),
        ("Geheim", "geheim tomaten garten beet sonne"), ("Lied", "lied gitarre akkord musik abend"),
        ("Gitarre", "gitarre saiten akkord musik lied"), ("Japan", "japan reise tokio zug museum")]
QS = ["tomaten garten beet", "reise wien museum", "kaffee wasser grad", "brot mehl wasser", "proxmox ceph cluster",
      "speicher cluster backup", "gitarre akkord musik", "netz knoten router", "reise zug japan", "erde garten herbst laub",
      "fahrrad reise kette", "packliste koffer reise", "kompost erde beet", "sauerteig ruhe brot", "gewuerze kueche glas",
      "ceph osd platte", "lied musik abend", "wien strassenbahn kaffee", "basilikum schatten garten", "nachtzug schlafwagen bett"]


def part_setup_off():
    set_embed()
    tk = tok_obj(U)
    S["db"] = dbid = api_struct.database_create(tk, {"title": "Erinnerungen (Schatten-Test)", "properties": PROPS})[1]["id"]
    st, d = req("PUT", "/api/account/memory", {"shadow": True}, uid=U)
    ok("Schattenmodus ohne Bindung -> 409 not_bound", st == 409 and d.get("code") == "not_bound", (st, d))
    memory.account_put(U, {"db": dbid})
    ids = {}
    for k, (t_, inh) in enumerate(ROWS):
        st, d = memory.remember(U, {"titel": "Z" + "abcdefghijklmnopqrstuvwxyz"[k], "inhalt": inh, "art": "Fakt", "wichtigkeit": 6}, tk)
        ids[t_] = d["id"]
    S["ids"] = ids
    S["snap"] = memory.nap(U, {"stimmung": "ruhig", "gedanke": "tomaten garten beet sonne wasser"}, tk)[1]["id"]
    memory.sync(U)
    api_v1.row_update(tk, ids["Geheim"], {"properties": {"Sensibel": True}})
    snaps, sens = excl(U)
    ok("Aufbau: 1 Schnappschuss, 1 sensibel", snaps == {S["snap"]} and sens == {ids["Geheim"]}, (snaps, sens))
    for q in QS[:3]:
        memory.recall(U, q)
    ok("aus: recall/remember schreiben keine Spur und keine Links", not traces(U) and not links(U), (len(traces(U)), len(links(U))))
    r = memory.recall(U, QS[0])
    ok("aus: ms ohne schatten", "schatten" not in r["ms"], r["ms"])


def part_on():
    st, d = req("PUT", "/api/account/memory", {"shadow": True}, uid=U)
    ok("Einschalten (PUT shadow true): 200, on, since", st == 200 and d["shadow"]["on"] and d["shadow"]["since"], (st, d.get("shadow")))
    for _ in range(80):
        st, d = req("GET", "/api/account/memory", uid=U)
        if d["shadow"]["links"] > 0 and not d["shadow"]["running"]:
            break
        time.sleep(0.25)
    ok("Nachziehen im Hintergrund: Links da, running wieder false", d["shadow"]["links"] > 0 and not d["shadow"]["running"], d["shadow"])
    L = links(U)
    snaps, sens = excl(U)
    bad = snaps | sens
    with db.connect() as c:
        v = memory.vectors(c, U, "fake-embed")
    pos = {int(i): k for k, i in enumerate(v.ids)}
    cos = lambda a, b: float(v.mat[pos[a]] @ v.mat[pos[b]])  # noqa: E731
    wrong = [(k, x) for k, x in L.items() if not k[0] < k[1] or k[0] in bad or k[1] in bad or x[4] != "aehnlich"
             or cos(*k) < msh.COS_MIN or abs(x[0] - msh.g0_cos(cos(*k))) > 1e-6 or x[1] != msh.U0 or x[2] != 0]
    ok("Links: a < b, aehnlich, cos >= 0,55, g = g0(cos), u = 0,05, treffer 0, nie Schnappschuss/sensibel", not wrong and L, wrong[:3])
    miss = []
    for i in pos:
        if i in bad:
            continue
        nb = sorted(((cos(i, j), j) for j in pos if j != i and j not in bad), reverse=True)[:msh.K]
        for cs, j in nb:
            if cs >= msh.COS_MIN and tuple(sorted((i, j))) not in L:
                miss.append((i, j, cs))
    ok("Links: je Zeile die besten K = 5 Nachbarn ueber der Schwelle", not miss, miss[:3])
    S["L0"] = L
    p = subprocess.run([sys.executable, "run.py", "gedaechtnis", "verknuepfen", "--konto", U], capture_output=True, text=True, timeout=120,
                       cwd=os.path.dirname(HERE), env=dict(os.environ, PYTHONPATH=os.path.dirname(HERE)))
    ok("CLI verknuepfen --konto: rc 0, nichts doppelt (0 neu)", p.returncode == 0 and "0 Verknuepfungen neu" in p.stdout and links(U) == L,
       (p.returncode, p.stdout[-300:], p.stderr[-300:]))
    p = subprocess.run([sys.executable, "run.py", "gedaechtnis", "verknuepfen", "--konto", Z], capture_output=True, text=True, timeout=60,
                       cwd=os.path.dirname(HERE), env=dict(os.environ, PYTHONPATH=os.path.dirname(HERE)))
    ok("CLI verknuepfen Konto ohne Gedaechtnis: Hinweis, rc 0", p.returncode == 0 and "kein Gedaechtnis" in p.stdout, p.stdout[-200:])
    # remember bei an: neue Zeile bekommt Links; sensible/Schnappschuss nie
    st, d = memory.remember(U, {"titel": "Y1", "inhalt": "tomaten sorten garten beet sonne", "art": "Fakt"}, tok_obj(U))
    new = [k for k in links(U) if d["id"] in k]
    ok("remember bei an: Links „aehnlich“ zur neuen Zeile (nie zu sensibel/Schnappschuss)", new and not any(set(k) & bad for k in new), new)
    S["toms"] = d["id"]
    st, d = memory.remember(U, {"titel": "Y2", "inhalt": "tomaten garten beet sonne geheim", "sensibel": True}, tok_obj(U))
    ok("remember sensibel: keine Links", not [k for k in links(U) if d["id"] in k] and d["id"] in excl(U)[1], d)


def recall_state(uid):
    return pg("SELECT note_id, hits, last_hit, mirrored FROM mem_vec WHERE uid=?", (uid,))


def restore(uid, rows):
    with db.connect() as c, c:
        for nid, h, lh, mi in rows:
            c.execute("UPDATE mem_vec SET hits=?, last_hit=?, mirrored=? WHERE note_id=? AND uid=?", (h, lh, mi, nid, uid))


def part_same():
    now = time.time()
    sh = pg("SELECT v FROM user_kv WHERE uid=? AND k='memory.shadow'", (U,))[0][0]
    bad, ms_on, n_tr0 = [], [], len(traces(U))
    for q in QS:
        st0 = recall_state(U)
        pg("DELETE FROM user_kv WHERE uid=? AND k='memory.shadow'", (U,))
        r_off = memory.recall(U, q, now=now)
        restore(U, st0)
        pg("INSERT INTO user_kv (uid, k, v) VALUES (?, 'memory.shadow', ?)", (U, sh))
        r_on = memory.recall(U, q, now=now)
        restore(U, st0)
        a = json.dumps({k: v for k, v in r_off.items() if k != "ms"}, ensure_ascii=False, sort_keys=True)
        b = json.dumps({k: v for k, v in r_on.items() if k != "ms"}, ensure_ascii=False, sort_keys=True)
        if a != b or "schatten" in r_off["ms"] or "schatten" not in r_on["ms"]:
            bad.append((q, a[:200], b[:200]))
        ms_on.append(r_on["ms"]["schatten"])
    tr = traces(U)
    ok("20 Fragen ein/aus: treffer/quelle/hinweis byteweise gleich, ms.schatten nur an", not bad, bad[:2])
    ok("20 Fragen: genau 20 Spuren (nur an)", len(tr) == n_tr0 + 20, len(tr) - n_tr0)
    note(f"ms.schatten bei 20 Fragen (20 Zeilen): Median {statistics.median(ms_on):.2f} ms, max {max(ms_on):.2f} ms")
    with_hits = [x for x in tr if x[6]]
    ok("Spuren mit Basis-Treffern und Schatten-Menge", with_hits and all(x[11].get("schatten") for x in with_hits), len(with_hits))
    # Spur ohne Fragetext
    q = "  Wien   REISE museum  "
    memory.recall(U, q)
    x = traces(U)[-1]
    dump = json.dumps([str(v) for v in x], ensure_ascii=False).casefold()
    ok("Spur: kein Fragetext in irgendeiner Spalte, Stichworte = keywords[:8], quelle/lim",
       "wien   reise" not in dump and "wien reise museum" not in dump and x[4] == ", ".join(memory.keywords(q)[:8]) and x[2] == "embeddings"
       and x[5] == 5, x[4])
    ok("HMAC: 32 Hex, stabil (Leerraum/Gross-Klein egal), je Konto verschieden",
       len(x[3]) == 32 and x[3] == msh.frage_hash(U, "wien reise museum") == msh.frage_hash(U, "WIEN reise\nMuseum")
       and msh.frage_hash(R, "wien reise museum") != x[3] and int(x[3], 16) >= 0)
    long_q = "kaffee " + " ".join(f"wort{i}" for i in range(30))
    memory.recall(U, long_q)
    ok("Stichworte hoechstens 8 und 160 Zeichen", len(traces(U)[-1][4].split(", ")) <= 8 and len(traces(U)[-1][4]) <= 160, traces(U)[-1][4])


def part_used():
    ids = S["ids"]
    r = memory.recall(U, "tomaten garten beet sonne")
    hit = [x["id"] for x in r["treffer"]]
    sens = excl(U)[1]
    ok("Testaufbau: recall mit >= 3 nicht sensiblen Basis-Treffern und einem sensiblen", len([i for i in hit if i not in sens]) >= 3
       and ids["Geheim"] in hit, hit)
    a, b, c3 = [i for i in hit if i not in sens][:3]
    L0 = links(U)
    pg("DELETE FROM mem_link WHERE uid=? AND a=? AND b=?", (U, *sorted((b, c3))))
    pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) VALUES (?,?,?,0.4,0.05,0,?,?,'aehnlich') "
       "ON CONFLICT (a, b) DO UPDATE SET gewicht=0.4, untergrenze=0.05, treffer=0, zuletzt=EXCLUDED.zuletzt", (*sorted((a, b)), U, time.time() - 10 * D, time.time() - 10 * D))
    pg("DELETE FROM mem_link WHERE uid=? AND a=? AND b=?", (U, *sorted((a, c3))))
    old = links(U)[tuple(sorted((a, b)))]
    msh.forget(U)
    with db.connect() as c:
        msh.graph_of(c, U)                             # Zwischenspeicher geladen (Stand vor recall_used)
    t0 = time.time()
    d = msh.used(U, [a, b, c3, 999999999, ids["Geheim"]], now=t0)
    L = links(U)
    with db.connect() as c:
        G1 = msh.graph_of(c, U)
        st_db = msh._lstamp(c, U)
        G2 = msh.Graph(c.execute("SELECT a, b, gewicht, untergrenze, zuletzt, gepinnt, herkunft, treffer FROM mem_link WHERE uid=? "
                                 "ORDER BY a, b", (U,)).fetchall())
    asdict = lambda G: {(int(G.a[k]), int(G.b[k])): (round(float(G.g[k]), 9), round(float(G.u[k]), 9), int(G.tr[k]), G.her[k])  # noqa: E731
                        for k in range(len(G.g))}
    ok("Link-Zwischenspeicher nach recall_used ohne Neuladen gleich dem Datenbank-Stand (Stempel stimmt)",
       asdict(G1) == asdict(G2) and G1.stamp == st_db, (G1.stamp, st_db))
    exp = msh.reinforce({"g": 0.4, "u": 0.05, "treffer": 0, "zuletzt": old[3]}, t0)
    nab, nac, nbc = L.get(tuple(sorted((a, b)))), L.get(tuple(sorted((a, c3)))), L.get(tuple(sorted((b, c3))))
    ok("recall_used: vorhandener Link nach Formel verstaerkt (g' = w + 0,2(1-w), u' = min(0,5, u+0,05, g'), treffer+1, zuletzt)",
       nab and abs(nab[0] - exp["g"]) < 1e-9 and abs(nab[1] - exp["u"]) < 1e-9 and nab[2] == 1 and abs(nab[3] - t0) < 0.01, (nab, exp))
    ok("recall_used: neue Paare Herkunft gemeinsam, g 0,30, u 0,10, treffer 1",
       nac and nbc and nac[:3] == (0.3, 0.1, 1) and nbc[:3] == (0.3, 0.1, 1) and nac[4] == "gemeinsam", (nac, nbc))
    ok("recall_used: Antwort schatten true, verstaerkt 1, neu 2, ignoriert unbekannte id; sensibel ohne Link",
       d == {"ok": True, "schatten": True, "verstaerkt": 1, "neu": 2, "ignoriert": [999999999]}
       and not [k for k in L if ids["Geheim"] in k and k not in L0], d)
    x = traces(U)[-1]
    ok("recall_used: in der juengsten passenden Spur unter genutzt vermerkt", set(x[8]) >= {a, b, c3}, x[8])
    pg("UPDATE mem_trace SET at = at - 3 * 3600 WHERE uid=?", (U,))
    d = msh.used(U, [a, b])
    ok("recall_used: Spur aelter als 2 h -> ignoriert, keine Wirkung", d["ignoriert"] == [a, b] and d["verstaerkt"] == 0 and links(U) == L, d)
    st, d = req("POST", "/api/v1/memory/recall_used", {"ids": [a]}, bearer=token(U, ["memory"]))
    ok("REST recall_used: 200 schatten true", st == 200 and d.get("schatten") is True and d["ignoriert"] == [a], (st, d))
    st, d = req("POST", "/api/v1/memory/recall_used", {"ids": []}, bearer=token(U, ["memory"]))
    ok("REST recall_used ohne ids -> 400", st == 400 and d.get("code") == "bad_request", (st, d))
    f = fail_of(memory.recall_used, U, [1] * 11)
    ok("recall_used mehr als 10 / keine Zahl -> 400", f and f.status == 400 and fail_of(memory.recall_used, U, ["x"]), f and f.msg)
    # 10 recalls ohne recall_used: vorhandene Links unveraendert
    L = links(U)
    for q in QS[:10]:
        memory.recall(U, q)
    L2 = links(U)
    ok("10 recalls ohne recall_used: vorhandene Gewichte/Untergrenzen/treffer/zuletzt unveraendert", all(L2.get(k) == v for k, v in L.items()),
       [(k, v, L2.get(k)) for k, v in L.items() if L2.get(k) != v][:3])
    S["Lg"] = {k: v for k, v in L2.items() if k not in L}
    # Auftauchen 3x: Paar ohne Link -> erst beim dritten Mal "gemeinsam"
    q = "brot mehl wasser"
    r = memory.recall(U, q)
    hit = [x["id"] for x in r["treffer"] if x["id"] not in excl(U)[1]]
    ok("Testaufbau: >= 2 Basis-Treffer", len(hit) >= 2, hit)
    p = tuple(sorted(hit[:2]))
    pg("DELETE FROM mem_trace WHERE uid=?", (U,))
    pg("DELETE FROM mem_link WHERE uid=? AND a=? AND b=?", (U, *p))
    memory.recall(U, q)
    memory.recall(U, q)
    two = p in links(U)
    memory.recall(U, q)
    L3 = links(U)
    ok("Auftauchen: 2x kein Link, 3x Link „gemeinsam“ (g 0,15, u 0,05)", not two and p in L3 and L3[p][4] == "gemeinsam"
       and L3[p][:2] == (0.15, 0.05), (two, L3.get(p)))
    # Pruefer (pruef-mem3): Token mit roots ohne die Gedaechtnis-Datenbank - wie recall 404, keine Titel, keine Wirkung
    other = api_v1.page_create(tok_obj(U), {"title": "Anderswo", "markdown": "x"})[1]["id"]
    tro = apitok.Tok(10 ** 9 + 26091, U, "gx", "sga_gx", apitok.SCOPES, [other], None)
    f1 = fail_of(memory.mcp_shadow_report, tro, {})
    L4, n4 = links(U), len(traces(U))
    f2 = fail_of(memory.mcp_recall_used, tro, {"ids": list(p)})
    f3 = fail_of(memory.recall, U, q, tok=tro)
    ok("roots ohne Gedaechtnis-DB: shadow_report und recall_used 404 not_found wie recall, nichts veraendert",
       f1 and f1.status == 404 and f2 and f2.status == 404 and f3 and f3.status == 404 and links(U) == L4 and len(traces(U)) == n4,
       (f1 and f1.status, f2 and f2.status, f3 and f3.status))
    tri = apitok.Tok(10 ** 9 + 26092, U, "gx", "sga_gx", apitok.SCOPES, [S["db"]], None)
    ok("roots mit Gedaechtnis-DB: shadow_report geht", memory.mcp_shadow_report(tri, {}).count("saga-data") >= 1)
    api_v1.page_delete(tok_obj(U), other)


def part_sens_lazy_err():
    ids = S["ids"]
    # sensibel: nie Ziel/Durchgang
    memory.recall(U, "tomaten garten beet sonne")
    x = traces(U)[-1]
    ent = {e[0]: e for e in x[11].get("schatten") or []}
    g = ids["Geheim"]
    ok("sensibel: in der Schatten-Menge nur mit direkter Relevanz (akt 0), nie via", (g not in ent or ent[g][2] == 0)
       and not any(e[4] == g for e in ent.values()), ent.get(g))
    # Link zu einer spaeter sensiblen Zeile bleibt, wird ignoriert
    some = next(k for k in links(U) if ids["Basilikum"] in k)
    api_v1.row_update(tok_obj(U), ids["Basilikum"], {"properties": {"Sensibel": True}})
    memory.recall(U, "tomaten garten beet sonne")
    ent = {e[0]: e for e in traces(U)[-1][11].get("schatten") or []}
    ok("spaeter sensibel: Links bleiben, werden ignoriert", some in links(U) and (ids["Basilikum"] not in ent or ent[ids["Basilikum"]][2] == 0)
       and not any(e[4] == ids["Basilikum"] for e in ent.values()), ent.get(ids["Basilikum"]))
    api_v1.row_update(tok_obj(U), ids["Basilikum"], {"properties": {"Sensibel": False}})
    # lazy: spaeterer Zeitpunkt schreibt keine Gewichte
    L = links(U)
    memory.recall(U, "reise wien museum", now=time.time() + 120 * D)
    L2 = links(U)
    ok("lazy: recall 120 Tage spaeter aendert kein vorhandenes Gewicht", all(L2.get(k) == v for k, v in L.items()))
    # Fehler einschleusen
    orig = msh._evaluate
    msh._evaluate = lambda *a, **k: 1 / 0
    try:
        n0 = len(traces(U))
        r = memory.recall(U, "reise wien museum")
        ok("Fehler im Schattenteil: recall ok, Treffer da, keine Spur", r["treffer"] and len(traces(U)) == n0 and "schatten" in r["ms"], r["ms"])
    finally:
        msh._evaluate = orig


def part_cleanup_forget():
    ids = S["ids"]
    now = time.time()
    pg("INSERT INTO mem_trace (uid, at, quelle, frage_hash, lim) VALUES (?, ?, 'embeddings', 'x', 5)", (U, now - 31 * D))
    pg("INSERT INTO mem_trace (uid, at, quelle, frage_hash, lim) VALUES (?, ?, 'embeddings', 'y', 5)", (U, now - 29 * D))
    pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, zuletzt, angelegt, herkunft) VALUES (?, ?, ?, 0.3, 0.05, ?, ?, 'hand')",
       (ids["Lied"], 9 * 10 ** 15, U, now, now))
    api_v1.page_delete(tok_obj(U), ids["Japan"])
    jl = [k for k in links(U) if ids["Japan"] in k]
    nt, nl = msh.cleanup(now=now, links=True)
    hs = {r[0] for r in pg("SELECT frage_hash FROM mem_trace WHERE uid=?", (U,))}
    ok("Aufraeumen: Spur > 30 Tage weg, 29 Tage bleibt; Link zu fehlender Zeile weg, Papierkorb-Zeile bleibt verknuepft",
       "x" not in hs and "y" in hs and (ids["Lied"], 9 * 10 ** 15) not in links(U) and all(k in links(U) for k in jl) and nl >= 1, (nt, nl, jl))
    st, d = req("PUT", "/api/account/memory", {"forget_traces": True}, uid=U)
    ok("forget_traces: alle Spuren weg, Links bleiben", st == 200 and not traces(U) and links(U) and d["shadow"]["traces"] == 0, d.get("shadow"))
    L = links(U)
    memory.recall(U, "reise wien museum")
    st, d = req("PUT", "/api/account/memory", {"shadow": False}, uid=U)
    n = len(traces(U))
    memory.recall(U, "reise wien museum")
    ok("aus: Links/Spuren bleiben, keine neue Spur", st == 200 and not d["shadow"]["on"] and links(U) == L and n == 1 and len(traces(U)) == 1,
       (d.get("shadow"), n))
    st, d = req("PUT", "/api/account/memory", {"shadow": "ja"}, uid=U)
    ok("PUT shadow kein bool -> 400", st == 400, st)
    req("PUT", "/api/account/memory", {"shadow": True}, uid=U)


def part_backup_delete():
    from saga import userbackup
    from saga.userdata import delete_user_data
    import inspect
    ok("userbackup: memory.shadow in KV_KEYS, Links/Spuren nicht im Backup", "memory.shadow" in userbackup.KV_KEYS
       and "mem_link" not in inspect.getsource(userbackup) and "mem_trace" not in inspect.getsource(userbackup))
    W = f"gx-wieder-{SUF}"
    auth.create_user(W, None, W)
    try:
        tw = tok_obj(W, 5)
        dbw = api_struct.database_create(tw, {"title": "Eingespielt", "properties": PROPS})[1]["id"]
        with db.connect() as c, c:
            r = userbackup._import_memory(c, W, {"settings": {"memory.db": "5", "memory.shadow": "1795000000"}}, {5: dbw}, {dbw})
        kv = dict(pg("SELECT k, v FROM user_kv WHERE uid=? AND k LIKE 'memory.%'", (W,)))
        ok("Einspielen: memory.shadow kommt mit der abgebildeten Datenbank an", r is True and kv.get("memory.shadow") == "1795000000", kv)
        W2 = f"gx-wieder2-{SUF}"
        auth.create_user(W2, None, W2)
        with db.connect() as c, c:
            r = userbackup._import_memory(c, W2, {"settings": {"memory.db": "6", "memory.shadow": "1795000000"}}, {}, set())
        ok("Einspielen ohne abgebildete Datenbank: memory.shadow nicht", r is False
           and not pg("SELECT 1 FROM user_kv WHERE uid=? AND k LIKE 'memory.%'", (W2,)))
        delete_user_data(W2, drop_user=True)
    finally:
        delete_user_data(W, drop_user=True)
    n = [pg(f"SELECT count(*) FROM {t} WHERE uid=?", (U,))[0][0] for t in ("mem_link", "mem_trace")]
    pg("INSERT INTO mem_link_log (uid, at, a, b, aktion, wer) VALUES (?, ?, 1, 2, 'pinnen', 'test')", (U, time.time()))
    S["del_before"] = n


def part_report():
    """Bericht auf festen Spuren (Konto R)."""
    tk = tok_obj(R, 1)
    dbr = api_struct.database_create(tk, {"title": "Bericht", "properties": PROPS})[1]["id"]
    memory.account_put(R, {"db": dbr})
    d0 = msh.report(R, 14, 8)
    ok("Bericht aus, ohne Spuren: schatten false, Nullen, kein Fehler", d0["schatten"] is False and d0["abrufe"]["n"] == 0
       and d0["kanten"]["n"] == 0 and d0["stichproben"] == [] and d0["hubs"] == [] and d0["schatten_treffer"]["gekappt"] == 0, d0)
    pg("INSERT INTO user_kv (uid, k, v) VALUES (?, 'memory.shadow', ?)", (R, str(int(time.time()))))
    ids = [memory.remember(R, {"titel": f"Zeile {i}", "inhalt": f"inhalt nummer {i} " + "x" * 150, "art": "Fakt"}, tk)[1]["id"]
           for i in range(8)]
    gone = memory.remember(R, {"titel": "Weg", "inhalt": "wird geloescht", "art": "Fakt"}, tk)[1]["id"]
    api_v1.row_update(tk, ids[7], {"properties": {"Sensibel": True}})
    pg("DELETE FROM mem_link WHERE uid=?", (R,))
    now = time.time()
    L = [(ids[0], ids[1], 0.95, 0.05, 0, False, "aehnlich"), (ids[0], ids[2], 0.4, 0.05, 2, False, "gemeinsam"),
         (ids[0], ids[3], 0.3, 0.05, 0, False, "aehnlich"), (ids[1], ids[2], 0.25, 0.05, 0, True, "hand"),
         (ids[2], ids[3], 0.15, 0.05, 0, False, "aehnlich")]
    for a, b, g, u, t_, pin, her in L:
        pg("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft, gepinnt) VALUES "
           "(?,?,?,?,?,?,?,?,?,? <> 0)", (a, b, R, g, u, t_, now, now, her, int(pin)))
    TR = []
    for k in range(12):
        neu = [ids[4]] if k % 2 == 0 else []
        if k == 10:
            neu = [gone]
        sch = [[ids[0], 0.8, 0.0, 0.7, None, 0]] + [[i, 0.3, 0.2, 0.5, ids[0], 1] for i in neu]
        TR.append((R, now - (12 - k) * 3600, "embeddings", "h", f"wort{k}", 5, [ids[0]] if k != 3 else [], neu,
                   [ids[0]] if k < 5 else [], 2 if k == 7 else 0, 1000 + 50 * k,
                   {"w": {"tor": 0.45, "score_min": 0.375}, "projekt": False, "basis": [[ids[0], 0.8, 0.7]] if k != 3 else [],
                    "schatten": sch, "neu_limit": [ids[5]] if k == 1 else [], "verdraengt": [ids[6]] if k == 7 else [], "ms": 1.0}))
    TR.append((R, now - 20 * D, "embeddings", "h", "alt", 5, [], [], [], 0, 0, {}))
    for x in TR:
        pg("INSERT INTO mem_trace (uid, at, quelle, frage_hash, stichworte, lim, treffer, neu, genutzt, gekappt, zeichen, daten) "
           "VALUES (?,?,?,?,?,?,CAST(? AS JSONB),CAST(? AS JSONB),CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
           (*x[:6], json.dumps(x[6]), json.dumps(x[7]), json.dumps(x[8]), x[9], x[10], json.dumps(x[11])))
    api_v1.page_delete(tk, gone)
    d = msh.report(R, 14, 4)
    d2 = msh.report(R, 14, 4)
    ok("Bericht: alle Schluessel", set(d) == {"schatten", "seit", "tage", "abrufe", "schatten_treffer", "kanten", "hubs", "oft_feuernd",
                                               "ausreisser", "sensibel", "stichproben"}, sorted(d))
    ok("Bericht: deterministisch (zweimal gleich)", d == d2)
    ok("Bericht: abrufe n 12 (20 Tage alt fehlt), mit_treffern 11, genutzt 5, quote", d["abrufe"] == {"n": 12, "mit_treffern": 11,
       "genutzt_gemeldet": 5, "quote_genutzt": round(5 / 11, 4)}, d["abrufe"])
    st_ = d["schatten_treffer"]
    zs = sorted(1000 + 50 * k for k in range(12))
    ok("Bericht: schatten_treffer (mit neu 6, je Abruf, verdraengt 1, gekappt 1 Abruf, Zeichen-Median/p90)",
       st_["abrufe_mit_neu"] == 6 and st_["neu_je_abruf"] == round(6 / 12, 4) and st_["neu_limit_je_abruf"] == round(1 / 12, 4)
       and st_["verdraengt"] == 1 and st_["gekappt"] == 1 and st_["zeichen_median"] == statistics.median(zs) and st_["zeichen_p90"] == zs[10], st_)
    kk = d["kanten"]
    ok("Bericht: kanten n 5, Herkunft, gepinnt 1, Faecher summieren, verstaerkt 1",
       kk["n"] == 5 and kk["herkunft"] == {"aehnlich": 3, "gemeinsam": 1, "hand": 1} and kk["gepinnt"] == 1
       and sum(f[2] for f in kk["gewicht"]["faecher"]) == 5 and len(kk["gewicht"]["faecher"]) == 10 and kk["verstaerkt"] == 1, kk)
    ok("Bericht: Hubs nach Grad (Zeile 0: Grad 3), mit Daempfung", d["hubs"] and d["hubs"][0]["id"] == ids[0] and d["hubs"][0]["grad"] == 3
       and 0 < d["hubs"][0]["daempfung"] < 1 and len(d["hubs"]) <= 5, d["hubs"])
    of = d["oft_feuernd"]
    ok("Bericht: oft_feuernd (Zeile 0 -> Zeile 4, 5 von 12)", of and of[0]["a"]["id"] == ids[0] and of[0]["b"]["id"] == ids[4]
       and of[0]["anteil"] == round(5 / 12, 4) and of[0]["gewicht"] == 0.0, of)
    arts = [a["art"] for a in d["ausreisser"]]
    ok("Bericht: Ausreisser dauerbrenner, starke_kante (0,95 ohne Pin), gekappt; <= 5",
       "dauerbrenner" in arts and "starke_kante" in arts and "gekappt" in arts and len(arts) <= 5, d["ausreisser"])
    ok("Bericht: sensibel 1", d["sensibel"] == 1, d["sensibel"])
    sp = d["stichproben"]
    with_neu = [x for x in TR if x[7]]
    want = [with_neu[i] for i in msh.sample_idx(len(with_neu), 4)]
    ok("Bericht: 4 Stichproben gleichmaessig (Index round(i(m-1)/(n-1))), aelteste zuerst", [x["stichworte"] for x in sp]
       == [x[4] for x in want], [x["stichworte"] for x in sp])
    allw = [w for x in sp for w in x["waere"]]
    ok("Bericht: waere mit titel/vorschau (<= 120)/rel/akt/gesamt/ueber; geloeschte „(gelöscht)“",
       allw and all(set(w) == {"id", "titel", "vorschau", "rel", "akt", "gesamt", "ueber"} and len(w["vorschau"]) <= 120 for w in allw)
       and any(w["id"] == gone and w["titel"] == "(gelöscht)" for w in allw) and all(w["ueber"]["id"] == ids[0] for w in allw), allw[:2])
    ok("Bericht: <= 15 000 Zeichen", len(json.dumps(msh.report(R, 30, 20), ensure_ascii=False)) <= 15000)
    for a, kw in (("tage", {"tage": 0}), ("tage", {"tage": 31}), ("stichproben", {"stichproben": 21}), ("tage", {"tage": "x"})):
        f = fail_of(memory.shadow_report, R, kw.get("tage"), kw.get("stichproben"))
        ok(f"Bericht Grenze {kw} -> 400", f and f.status == 400, f and f.msg)
    tm = token(R, ["mcp", "memory"])
    st, dd = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "shadow_report", "arguments": {"tage": 14}}},
                 bearer=tm)
    txt = (((dd or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
    ok("MCP shadow_report: Rahmen memory:report", st == 200 and not dd["result"].get("isError") and 'source="memory:report"' in txt
       and '"kanten"' in txt, (st, txt[:200]))
    st, dd = req("GET", "/api/v1/memory/shadow?tage=7&stichproben=2", bearer=token(R, ["memory"]))
    ok("REST shadow: 200, tage 7, <= 2 Stichproben", st == 200 and dd["tage"] == 7 and len(dd["stichproben"]) <= 2, (st, str(dd)[:200]))
    st, dd = req("GET", "/api/v1/memory/shadow?tage=99", bearer=token(R, ["memory"]))
    ok("REST shadow tage 99 -> 400", st == 400, st)
    tr = apitok.Tok(10 ** 9 + 26097, R, "gx", "sga_gx", apitok.SCOPES, None, None)
    for _ in range(memory.REPORT_RATE):
        memory.mcp_shadow_report(tr, {})
    f = fail_of(memory.mcp_shadow_report, tr, {})
    ok("Rate shadow_report: 6/min je Token -> 429", f and f.status == 429, f and f.status)


def part_rest_log():
    tk = token(U, ["memory"])
    q = "Gitarrenstimmung Leuchtturm Zeppelin"
    st, d = req("POST", "/api/v1/memory/recall", {"text": q}, bearer=tk)
    ok("REST recall bei an: 200, ms.schatten", st == 200 and "schatten" in d["ms"], (st, d.get("ms")))
    p = os.path.join(os.environ.get("STATE_DIR", "/data"), "server.log")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        txt = ""
    if not txt:
        note("Server-Log nicht lesbar - Pruefung uebersprungen")
        return
    low = txt.casefold()
    ok("Server-Log: weder Frage noch Stichworte", q not in txt and "leuchtturm" not in low and "zeppelin" not in low)
    ok("Server-Log: Nachziehen nur mit Zahlen", "Verknuepfungen nachgezogen" in txt)


def part_delete():
    from saga.userdata import delete_user_data
    n = S.get("del_before") or [0, 0]
    delete_user_data(U, drop_user=True)
    left = [pg(f"SELECT count(*) FROM {t} WHERE uid=?", (U,))[0][0] for t in ("mem_link", "mem_trace", "mem_link_log")]
    ok("Konto loeschen: mem_link, mem_trace, mem_link_log weg", n[0] > 0 and left == [0, 0, 0], (n, left))


def part_perf():
    """5 000 Zeilen (1024 Dim.) / 25 000 Kanten: ms.schatten p90 <= 25 ms, Suche + Bewertung <= 150 ms."""
    P = f"gx-perf-{SUF}"
    auth.create_user(P, None, P)
    S["perf"] = P
    tp = tok_obj(P, 9)
    dbp = api_struct.database_create(tp, {"title": "Leistung", "properties": PROPS})[1]["id"]
    memory.account_put(P, {"db": dbp})
    set_embed(model="fake-embed-1024")
    _st, info = api_v1.database_get(tp, dbp)
    pid = {p["name"]: p["id"] for p in info["schema"]["properties"]}
    topics = ["proxmox", "ceph", "python", "fastapi", "anime", "frieren", "garten", "tomaten", "kochen", "pasta", "linux", "kernel",
              "netzwerk", "vlan", "backup", "restic", "musik", "gitarre", "reise", "japan"]
    rnd = np.random.default_rng(2603)
    now = time.time()
    rows = []
    for i in range(5000):
        ws = [topics[j] for j in rnd.choice(len(topics), 4, replace=False)] + [f"wort{int(x)}" for x in rnd.integers(0, 3000, 6)]
        rows.append((P, f"Erinnerung {i}", "", now - i * 600, now - i * 600, dbp, json.dumps({pid["Inhalt"]: " ".join(ws), pid["Wichtigkeit"]: int(i % 11)})))
    with db.connect() as c, c:
        c.executemany("INSERT INTO note (uid, title, body, created, updated, parent_id, props) VALUES (?,?,?,?,?,?,?)", rows)
    r = memory.sync(P)
    ok("Leistung: 5000 eingebettet", r["embedded"] == 5000, r)
    nids = [x[0] for x in pg("SELECT id FROM note WHERE uid=? AND parent_id=? ORDER BY id", (P, dbp))]
    pairs = set()
    while len(pairs) < 25000:
        a, b = sorted(int(x) for x in rnd.choice(nids, 2, replace=False))
        pairs.add((a, b))
    with db.connect() as c, c:
        c.executemany("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) VALUES (?,?,?,?,?,0,?,?,'aehnlich')",
                      [(a, b, P, float(0.2 + 0.4 * rnd.random()), 0.05, now - float(rnd.random()) * 90 * D, now) for a, b in sorted(pairs)])
    pg("INSERT INTO user_kv (uid, k, v) VALUES (?, 'memory.shadow', ?)", (P, str(int(now))))
    qs = [" ".join(rnd.choice(topics, 3, replace=False)) + " erinnerung" for _ in range(26)]
    memory.recall(P, qs[0])
    sh, se, ok_n = [], [], 0
    for q in qs[1:]:
        r = memory.recall(P, q)
        sh.append(r["ms"]["schatten"])
        se.append(r["ms"]["suche"])
        ok_n += bool(r["treffer"])
    p90 = sorted(sh)[int(len(sh) * 0.9)]
    note(f"Leistung Schatten 5000 Zeilen / 25000 Kanten: ms.schatten Median {statistics.median(sh):.1f}, p90 {p90:.1f}, max {max(sh):.1f} ms; "
         f"Suche Median {statistics.median(se):.1f}, max {max(se):.1f} ms; Link-Cache {msh.cache_bytes() / 1048576:.1f} MB")
    ok("Leistung: ms.schatten p90 <= 25 ms", p90 <= 25, sh)
    ok("Leistung: Suche + Bewertung <= 150 ms (90 %)", sorted(se)[int(len(se) * 0.9)] <= 150, se)
    ok("Leistung: Treffer und Spuren", ok_n > 20 and pg("SELECT count(*) FROM mem_trace WHERE uid=?", (P,))[0][0] == 26)
    set_embed()


def cleanup():
    from saga.userdata import delete_user_data
    for u in (U, R, Z, S.get("perf")):
        if u and auth.get_user(u):
            delete_user_data(u, drop_user=True)


def _alive():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{FP}/_count", timeout=2):
            return True
    except OSError:
        return False


def main():
    fk = subprocess.Popen([sys.executable, os.path.join(HERE, "fakeai.py"), str(FP)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    emb0 = auth.settings(fresh=True).get("ai_embed")
    try:
        for _ in range(100):
            if _alive():
                break
            time.sleep(0.1)
        for u in (U, R, Z):
            auth.create_user(u, "Gx-Pass-12345", u)
            set_user_lang(u, "de")
        set_embed(wait=True)
        for p in (part_vectors, part_setup_off, part_on, part_same, part_used, part_sens_lazy_err, part_cleanup_forget,
                  part_backup_delete, part_report, part_rest_log, part_delete, part_perf):
            try:
                p()
            except Exception as ex:  # noqa: BLE001
                import traceback
                traceback.print_exc()
                ok(f"{p.__name__}: Ausnahme {type(ex).__name__}: {ex}", False)
    finally:
        fk.terminate()
        try:
            cleanup()
            auth.save_setting("ai_embed", emb0 if emb0 is not None else dict(auth.DEFAULTS["ai_embed"]))
        except Exception as ex:  # noqa: BLE001
            print("Aufraeumen:", ex)
    for s in INFO:
        print("INFO", s)
    print(f"\nGedaechtnis-Schatten {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
