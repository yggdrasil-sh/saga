"""Gedaechtnis Stufe 3 - Nickerchen und Aufwachen (2.6, Bugs-DB 2601, docs/MEMORY.md 11).

Prueft: Vektoren tests/vectors/memory_nap.json (Schnappschuss-Text de/en, Grenzfaelle 400 mit Feldname, Zeitangaben,
Aufwach-Satz, Titel); nap (201, Art = Option "Schnappschuss", Titel in Konto-Zeitzone, Inhalt = Text, Valenz/Erregung/
Datum/Abrufe in ihren Spalten, ignoriert, unknown_option ohne Anlage, missing_column, Projekt, nichts gekuerzt, Rate im
recall-Topf); Ausschluss der Schnappschuesse (Embeddings vor CAND_MAX: 60 sehr aehnliche Schnappschuesse + 1 echte
Erinnerung -> die echte wird gefunden; Trigramme; aehnlich bei remember; ohne Option nichts ausgeschlossen; Art spaeter
geaendert); wake (neuester nach created, Gleichstand groessere id, unabhaengig von "Datum", null ohne Schnappschuss,
Hinweis ohne Option, geaenderte Seiten positiv/negativ, heute mit/ohne Faehigkeit "ai", Kontosprache en);
GET /api/v1/memory/core mit wake + kontext (Aufwachen), GET /api/v1/memory/wake (409 ohne Bindung, nur Kern), REST nap,
MCP nap/wake (Rahmen memory:core), Einstellungen snapshot-Status, 8 Schnappschuesse bleiben alle; Log ohne Inhalte.
Wegwerf-Konten gs-*, erfundene Testdaten, Fake-Embedding tests/fakeai.py (MEM_FAKE_PORT, Standard 8905).
Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> gedaechtnis_schlaf.py"""
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import db

db.wait_ready()
import auth  # noqa: E402
from saga import api_struct, api_v1, apitok, core as score_, memory  # noqa: E402
from saga.api_v1 import Fail  # noqa: E402
from saga.core import _tok_hash  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
B_URL = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc if "BASE_URL" in os.environ else None
FP = int(os.environ.get("MEM_FAKE_PORT", "8905"))
SUF = secrets.token_hex(3)
A, B, C, E = f"gs-anna-{SUF}", f"gs-bert-{SUF}", f"gs-cleo-{SUF}", f"gs-emil-{SUF}"
GROUP = f"gs-ki-{SUF}"
res = []
INFO = []
S = {}


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
    return s.sign(base64.b64encode(json.dumps({"uid": uid, "sec_at": int(time.time())}).encode())).decode()


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
                                                           method=m, headers=h), timeout=30) as r:
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
       (uid, "gs-test", _tok_hash(raw), raw[:12], json.dumps(apitok.norm_scopes(scopes)), None, time.time(), time.time() + 3600))
    return raw


def tok_obj(uid, n=0):
    return apitok.Tok(10 ** 9 + 26010 + n, uid, "gs", "sga_gs", apitok.SCOPES, None, None)


def set_embed(enabled=True, wait=False):
    v = {"enabled": enabled, "provider": "openai", "url": f"http://127.0.0.1:{FP}/v1", "model": "fake-embed", "api_key": "",
         "keep_alive": "30m", "timeout": 15, "query_timeout_ms": 1200, "pinned": ["127.0.0.1"]}
    auth.save_setting("ai_embed", v)
    memory.forget()
    memory._qcache.clear()
    if wait:
        time.sleep(5.4)


PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Projekt", "type": "select", "options": ["Saga", "Garten"]},
         {"name": "Art", "type": "select", "options": ["Fakt", "Episode", "Schnappschuss"]}, {"name": "Wichtigkeit", "type": "number"},
         {"name": "Valenz", "type": "number"}, {"name": "Erregung", "type": "number"}, {"name": "Datum", "type": "date"},
         {"name": "Abrufe", "type": "number"}, {"name": "Sensibel", "type": "checkbox"}]


def mkdb(uid, title, props=PROPS, n=0):
    return api_struct.database_create(tok_obj(uid, n), {"title": title, "properties": props})[1]["id"]


def pids(dbid):
    sch = json.loads(pg("SELECT schema FROM note WHERE id=?", (dbid,))[0][0])
    return {p["name"]: p for p in sch["props"]}


def nap(uid, a, n=0):
    return memory.nap(uid, a, tok_obj(uid, n))


def excl(uid, dbid):
    with db.connect() as c:
        b = memory.binding(uid, c)
        cols, _ = memory.columns(memory._db_state(c, uid, dbid)[0], b["map"])
        return memory.excluded(c, uid, dbid, cols, b["map"])


def fail_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return None
    except Fail as f:
        return f


# --- 1. Vektoren -----------------------------------------------------------------------------------------------------
def part_vectors():
    with open(os.path.join(HERE, "vectors", "memory_nap.json"), encoding="utf-8") as f:
        V = json.load(f)
    ok("Vektoren: Grenzen gleich", {k: memory.NAP_LIM[k] for k in V["grenzen"]} == V["grenzen"], memory.NAP_LIM)
    bad = []
    for x in V["texte"]:
        got = memory.nap_text(memory.nap_fields(x["in"]), x["lang"])
        if got != x["text"]:
            bad.append((x["name"], x["lang"], got, x["text"]))
    ok(f"Vektoren: {len(V['texte'])} Schnappschuss-Texte de/en exakt", not bad, bad[:2])
    bad = []
    for x in V["fehler"]:
        f = fail_of(memory.nap_fields, x["in"])
        if f is None or f.status != 400 or x["feld"] not in f.msg:
            bad.append((x["name"], f and (f.status, f.msg)))
    ok(f"Vektoren: {len(V['fehler'])} Grenzfaelle -> 400 mit Feldname", not bad, bad[:3])
    bad = [(x["name"], memory.ago_text(x["sekunden"], lg), x[lg]) for x in V["wann"] for lg in ("de", "en")
           if memory.ago_text(x["sekunden"], lg) != x[lg]]
    ok(f"Vektoren: {len(V['wann'])} Zeitangaben de/en", not bad, bad[:3])
    bad = [(x["name"], memory.wake_text(x["sekunden"], x["n"], lg), x[lg]) for x in V["satz"] for lg in ("de", "en")
           if memory.wake_text(x["sekunden"], x["n"], lg) != x[lg]]
    ok(f"Vektoren: {len(V['satz'])} Aufwach-Saetze de/en", not bad, bad[:3])
    from zoneinfo import ZoneInfo
    bad = [(x, memory.nap_title(x["option"], x["ts"], ZoneInfo(x["zone"]))) for x in V["titel"]
           if memory.nap_title(x["option"], x["ts"], ZoneInfo(x["zone"])) != x["titel"]]
    ok("Vektoren: Titel in Konto-Zeitzone", not bad, bad)
    f5 = memory.nap_fields({"stimmung": "x", "gedanke": "g" * 240, "faeden": ["f" * 130] * 5})
    ok("Grenzwerte genau: nichts gekuerzt", len(f5["gedanke"]) == 240 and f5["faeden"] == ["f" * 130] * 5)


# --- 2. nap ----------------------------------------------------------------------------------------------------------
def part_nap():
    set_embed()
    S["db"] = mkdb(A, "Erinnerungen (Schlaf-Test)")
    S["core"] = api_v1.page_create(tok_obj(A), {"title": "Ich-Kern (Schlaf-Test)", "markdown": "Ich bin die Test-Assistentin."})[1]["id"]
    st, d = memory.account_put(A, {"db": S["db"], "core": S["core"]})
    ok("Binden: 200, snapshot-Status (Option gefunden, 0 Schnappschuesse), map mit sensibel/schnappschuss",
       st == 200 and d["snapshot"] == {"option": "Schnappschuss", "found": True, "count": 0, "latest": None}
       and d["map"]["schnappschuss"] == "Schnappschuss" and d["map"]["sensibel"] == "Sensibel" and d["columns"]["sensibel"]["ok"]
       and d["shadow"]["on"] is False, d)
    p = pids(S["db"])
    S["p"] = p
    snap_oid = next(o["id"] for o in p["Art"]["options"] if o["name"] == "Schnappschuss")
    S["snap_oid"] = snap_oid
    n0 = pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0]
    a = {"stimmung": "neugierig", "gedanke": "Morgen zuerst die Vektoren.", "valenz": 0.4, "erregung": 0.6,
         "faeden": ["Migration 43 prüfen", "Gold neu erzeugen"], "ort": "Saga – Gedächtnis Stufe 3", "projekt": "saga"}
    t0 = time.time()
    st, d = nap(A, a)
    with open(os.path.join(HERE, "vectors", "memory_nap.json"), encoding="utf-8") as f:
        V = json.load(f)
    exp = next(x["text"] for x in V["texte"] if x["name"] == "alle Zeilen" and x["lang"] == "de")
    row = pg("SELECT title, props FROM note WHERE id=?", (d["id"],))[0]
    pv = json.loads(row[1])
    from saga import ai_today
    with db.connect() as c:
        z = ai_today._zone(c, A, None)
    want_t = {memory.nap_title("Schnappschuss", t0 + dt, z) for dt in (0, 60)}
    ok("nap: 201, Titel „Schnappschuss JJJJ-MM-TT HH:MM“ (Konto-Zeitzone), zeilen/zeichen, nichts ignoriert",
       st == 201 and re.fullmatch(r"Schnappschuss \d{4}-\d\d-\d\d \d\d:\d\d", d["titel"]) and d["titel"] in want_t
       and row[0] == d["titel"] and d["zeilen"] == 6 and d["zeichen"] == len(exp) and "ignoriert" not in d, (st, d, want_t))
    sid = d["id"]
    S["snaps"] = [sid]
    ok("nap: Art = Schnappschuss, Inhalt = Text (Vektor), Valenz/Erregung/Projekt/Datum/Abrufe in den Spalten",
       pv.get(p["Art"]["id"]) == snap_oid and pv.get(p["Inhalt"]["id"]) == exp and pv.get(p["Valenz"]["id"]) == 0.4
       and pv.get(p["Erregung"]["id"]) == 0.6 and pv.get(p["Projekt"]["id"]) == next(o["id"] for o in p["Projekt"]["options"] if o["name"] == "Saga")
       and pv.get(p["Datum"]["id"]) == datetime.now(score_.TZ).date().isoformat() and pv.get(p["Abrufe"]["id"]) == 0
       and p["Sensibel"]["id"] not in pv, pv)
    ok("nap: genau eine Zeile angelegt", pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0] == n0 + 1)
    # Grenzen: nichts angelegt
    n1 = pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0]
    bad = []
    for x in V["fehler"]:
        f = fail_of(nap, A, x["in"])
        if f is None or f.status != 400 or f.code != "bad_request":
            bad.append((x["name"], f and (f.status, f.code)))
    ok("nap: Grenzfaelle 400 bad_request", not bad, bad[:3])
    f = fail_of(nap, A, {"stimmung": "x", "gedanke": "y", "projekt": "Unbekannt"})
    ok("nap: Projekt unbekannt -> 400 unknown_option", f and f.code == "unknown_option" and f.extra["unbekannt"] == {"projekt": ["Unbekannt"]}
       and "Saga" in f.extra["optionen"]["projekt"], f and (f.code, f.extra))
    ok("nap: Fehler legen nichts an", pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0] == n1)
    # DB ohne Option "Schnappschuss" und ohne Valenz
    pr2 = [x for x in PROPS if x["name"] not in ("Valenz", "Art")] + [{"name": "Art", "type": "select", "options": ["Fakt"]}]
    S["db_b"] = mkdb(B, "Ohne Option", pr2, 1)
    memory.account_put(B, {"db": S["db_b"]})
    nb = pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db_b"],))[0][0]
    f = fail_of(nap, B, {"stimmung": "x", "gedanke": "y", "valenz": 0.2}, 1)
    ok("nap ohne Option: 400 unknown_option {art: [Schnappschuss]} mit Optionen, nichts angelegt",
       f and f.status == 400 and f.code == "unknown_option" and f.extra == {"unbekannt": {"art": ["Schnappschuss"]}, "optionen": {"art": ["Fakt"]}}
       and pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db_b"],))[0][0] == nb, f and (f.code, f.extra))
    st, d = memory.account_put(B, {"map": {"schnappschuss": "fakt"}})
    st, d = nap(B, {"stimmung": "x", "gedanke": "y", "valenz": 0.2}, 1)
    ok("map schnappschuss -> andere Option (Gross/Klein egal): 201, Titel mit Optionsname, Valenz ignoriert",
       st == 201 and d["titel"].startswith("Fakt ") and d.get("ignoriert") == ["valenz"], d)
    memory.account_put(B, {"map": None})
    st, dv = memory.account_put(B, {})
    ok("Einstellungen ohne Option: snapshot.found false", dv["snapshot"]["found"] is False and dv["snapshot"]["count"] == 0, dv["snapshot"])
    S["db_c"] = mkdb(C, "Ohne Art", [{"name": "Inhalt", "type": "text"}], 2)
    memory.account_put(C, {"db": S["db_c"]})
    f = fail_of(nap, C, {"stimmung": "x", "gedanke": "y"}, 2)
    ok("nap ohne Spalte Art -> 400 missing_column", f and f.status == 400 and f.code == "missing_column", f and (f.code, f.msg))
    f = fail_of(memory.nap, E, {"stimmung": "x", "gedanke": "y"})
    ok("nap ohne Bindung -> 409 not_bound", f and f.status == 409 and f.code == "not_bound", f and f.code)
    # Rate: recall-Topf (60/min je Token)
    tr = apitok.Tok(10 ** 9 + 26099, A, "gs", "sga_gs", apitok.SCOPES, None, None)
    for _ in range(memory.RATE):
        memory._rate(tr)
    f = fail_of(memory.mcp_nap, tr, {"stimmung": "x", "gedanke": "y"})
    f2 = fail_of(memory.mcp_recall_used, tr, {"ids": [1]})
    ok("Rate: nap und recall_used im recall-Topf (60/min) -> 429", f and f.status == 429 and f2 and f2.status == 429, (f and f.status, f2 and f2.status))


# --- 3. Ausschluss ---------------------------------------------------------------------------------------------------
Q = "Proxmox Cluster Ceph Speicher Knoten Migration Wartung"


def part_exclusion():
    tk = tok_obj(A)
    real = memory.remember(A, {"titel": "Cluster-Wartung", "inhalt": "Proxmox Cluster Ceph Wartung am Wochenende mit dem Team planen",
                               "art": "Fakt", "wichtigkeit": 7}, tk)[1]["id"]
    S["real"] = real
    for i in range(60):
        st, d = nap(A, {"stimmung": "ruhig", "gedanke": Q + f" Nummer {i}"})
        S["snaps"].append(d["id"])
    r = memory.sync(A)
    snaps = excl(A, S["db"])[0]
    ok("61 Schnappschuesse eingebettet und als Schnappschuss erkannt (n von m zaehlt sie mit)", len(snaps) == 61
       and memory.status(A)["embedded"] == memory.status(A)["rows"] == 62, (len(snaps), memory.status(A), r))
    with db.connect() as c:
        v = memory.vectors(c, A, "fake-embed")
        qv = memory.embed([Q])[0]
        rel = v.mat @ qv
        ids = [int(i) for i in v.ids]
        rs = {i: float(x) for i, x in zip(ids, rel)}
    above = sum(1 for i in S["snaps"][1:] if rs[i] > rs[real])
    ok("Testaufbau: 60 Schnappschuesse aehnlicher als die echte Erinnerung (> CAND_MAX ohne Ausschluss)",
       above == 60 and rs[real] >= memory.WEIGHTS["rel_min"], (above, rs[real]))
    r = memory.recall(A, Q, count=False)
    hit = [x["id"] for x in r["treffer"]]
    ok("Embeddings: die echte Erinnerung wird gefunden, kein Schnappschuss unter den Treffern",
       r["quelle"] == "embeddings" and real in hit and not set(hit) & set(S["snaps"]), (r["quelle"], hit))
    st, d = memory.remember(A, {"titel": "Gedanke", "inhalt": f"Stimmung: ruhig\nLetzter Gedanke: {Q} Nummer 3", "art": "Fakt"}, tk)
    ok("remember: aehnlich nennt keine Schnappschuesse (sonst rel >= 0,90)", st == 201 and not {x["id"] for x in d["aehnlich"]} & set(S["snaps"]),
       d["aehnlich"])
    S["gedanke"] = d["id"]
    # Trigramme
    set_embed(enabled=False)
    r = memory.recall(A, Q, count=False)
    hit = [x["id"] for x in r["treffer"]]
    with db.connect() as c:
        b = memory.binding(A, c)
        cols, _ = memory.columns(memory._db_state(c, A, S["db"])[0], b["map"])
        x = memory.word_index(c, A, S["db"], cols, memory.snap_option(cols, b["map"]))
    ok("Trigramme: Schnappschuesse nicht im Wortindex, echte Erinnerung gefunden",
       r["quelle"] == "trigramm" and real in hit and not set(hit) & set(S["snaps"]) and not set(x.ids) & set(S["snaps"])
       and real in x.ids, (hit, len(x.ids)))
    st, d = memory.remember(A, {"titel": "Gedanke 2", "inhalt": f"Stimmung: ruhig Letzter Gedanke: {Q} Nummer 5", "art": "Fakt"}, tk)
    ok("Trigramme: aehnlich ohne Schnappschuesse", st == 201 and not {y["id"] for y in d["aehnlich"]} & set(S["snaps"]), d["aehnlich"])
    api_v1.page_delete(tk, d["id"])
    set_embed(enabled=True)
    # Art spaeter geaendert: aus Schnappschuss wird Fakt -> wieder auffindbar (Stempel)
    p = S["p"]
    fakt = next(o["id"] for o in p["Art"]["options"] if o["name"] == "Fakt")
    one = S["snaps"][7]
    api_v1.row_update(tk, one, {"properties": {"Art": "Fakt"}})
    memory.sync(A, [one])
    sn = excl(A, S["db"])[0]
    ok("Art geaendert (Schnappschuss -> Fakt): nicht mehr ausgeschlossen", one not in sn and len(sn) == 60
       and json.loads(pg("SELECT props FROM note WHERE id=?", (one,))[0][0]).get(p["Art"]["id"]) == fakt, len(sn))
    api_v1.row_update(tk, one, {"properties": {"Art": "Schnappschuss"}})
    # ohne Option: nichts ausgeschlossen
    ok("DB ohne Option: nichts ausgeschlossen", excl(B, S["db_b"]) == (frozenset(), frozenset()))


# --- 4. wake ---------------------------------------------------------------------------------------------------------
def part_wake():
    tk = tok_obj(A)
    now = float(int(time.time()))                       # ganzzahlig: seit_s exakt
    snaps = S["snaps"]
    # Zeitpunkte setzen: alle Schnappschuesse alt, einer (nicht der zuletzt angelegte) am neuesten; Datum egal
    pg("UPDATE note SET created=?, updated=? WHERE id = ANY(?)", (now - 10 * 86400, now - 10 * 86400, snaps))
    newest = snaps[5]
    pg("UPDATE note SET created=?, updated=? WHERE id=?", (now - (2 * 86400 + 5 * 3600 + 30), now - 3 * 86400, newest))
    since = now - (2 * 86400 + 5 * 3600 + 30)
    # Seiten: alles von A vor since, dann gezielt neuer
    pg("UPDATE note SET updated=? WHERE uid=? AND updated > ?", (since - 100, A, since - 100))
    mk = lambda t: api_v1.page_create(tk, {"title": t, "markdown": "x"})[1]["id"]  # noqa: E731
    p_new = [mk(f"Neu {i}") for i in range(6)]
    p_trash, p_arch, p_old = mk("Papierkorb"), mk("Archiv"), mk("Alt")
    p_tpl = mk("Vorlage")
    api_v1.page_delete(tk, p_trash)
    pg("UPDATE note SET archived=? WHERE id=?", (now, p_arch))
    pg("UPDATE note SET is_template=1 WHERE id=?", (p_tpl,))
    pg("UPDATE note SET updated=? WHERE id=?", (since - 60, p_old))
    for i, pid in enumerate(p_new):
        pg("UPDATE note SET updated=? WHERE id=?", (now - 600 + i, pid))
    pg("UPDATE note SET updated=? WHERE id=?", (now - 300, S["core"]))                 # Ich-Kern zaehlt mit
    pg("UPDATE note SET updated=? WHERE id IN (?, ?)", (now - 200, S["db"], S["real"]))  # DB und Zeilen nie
    other = api_v1.page_create(tok_obj(B, 1), {"title": "Fremd", "markdown": "x"})[1]["id"]
    w = memory.wake(A, tk, now=now)
    exp_ids = [S["core"], p_new[5], p_new[4], p_new[3], p_new[2]]
    ok("wake: neuester Schnappschuss nach created (nicht zuletzt angelegt, unabhaengig von Datum)",
       w["schnappschuss"]["id"] == newest and w["anzahl"] == 61 and w["seit_s"] == 2 * 86400 + 5 * 3600 + 30 and "hinweis" not in w, w)
    ok("wake: Text de „vor 2 Tagen, 5 Stunden. Seitdem 7 Seiten geändert.“",
       w["text"] == "Letztes Nickerchen: vor 2 Tagen, 5 Stunden. Seitdem 7 Seiten geändert.", w["text"])
    ok("wake: geaendert n 7 (6 neue + Ich-Kern; ohne Papierkorb/Archiv/Vorlage/alt/Gedaechtnis-DB/Zeilen/fremd), 5 zuletzt geaenderte",
       w["geaendert"]["n"] == 7 and [x["id"] for x in w["geaendert"]["seiten"]] == exp_ids, w["geaendert"])
    ok("wake: since in Konto-Zeitzone (Minuten), schnappschuss mit Valenz/Erregung/Datum-Feldern",
       re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d[+-]\d\d:\d\d", w["since"] or "") and set(w["schnappschuss"]) ==
       {"id", "titel", "inhalt", "valenz", "erregung", "datum"}, w)
    pg("UPDATE note SET created=? WHERE id=?", (since, snaps[9]))
    w2 = memory.wake(A, tk, now=now)
    ok("wake: Gleichstand created -> groessere id", w2["schnappschuss"]["id"] == max(newest, snaps[9]), w2["schnappschuss"]["id"])
    # heute mit/ohne Faehigkeit "ai"
    ok("wake: heute ohne Faehigkeit ai -> null", w["heute"] is None, w["heute"])
    ai0 = auth.settings(fresh=True).get("ai") or {}
    try:
        auth.set_user_groups(auth.get_user(A)["id"], [GROUP])
        auth.save_setting("ai", dict(ai0, enabled=True, provider="openai", url=f"http://127.0.0.1:{FP}/v1", model="fake",
                                     groups=[GROUP], pinned=["127.0.0.1"]))
        auth.settings(fresh=True)
        t0 = time.perf_counter()
        w3 = memory.wake(A, tk, now=now)
        ms = (time.perf_counter() - t0) * 1000
        note(f"wake (61 Schnappschuesse, mit Tagessatz): {ms:.1f} ms")
        ok("wake: heute mit Faehigkeit ai = Satz aus /api/ai/today (ohne Modell)", isinstance(w3["heute"], str) and w3["heute"], w3["heute"])
        ok("Leistung: wake <= 50 ms", ms <= 50, ms)
        wb = memory.wake(B, tok_obj(B, 1), now=now)
        ok("wake Konto ohne Faehigkeit: heute null", wb is not None and wb["heute"] is None, wb)
    finally:
        auth.save_setting("ai", ai0)
    # ohne Schnappschuss / ohne Option
    wb = memory.wake(B, tok_obj(B, 1), now=now)
    ok("wake ohne Option: hinweis no_snapshot_option, null-Teile", wb["hinweis"] == "no_snapshot_option" and wb["schnappschuss"] is None
       and wb["since"] is None and wb["geaendert"] is None and wb["anzahl"] == 0, wb)
    U = f"gs-leer-{SUF}"
    auth.create_user(U, None, U)
    S["leer"] = U
    dbl = mkdb(U, "Leer", PROPS, 3)
    memory.account_put(U, {"db": dbl})
    wl = memory.wake(U, tok_obj(U, 3), now=now)
    ok("wake ohne Schnappschuss: Text „Noch kein Nickerchen.“, since/geaendert null", wl["text"] == "Noch kein Nickerchen."
       and wl["since"] is None and wl["geaendert"] is None and wl["anzahl"] == 0 and "hinweis" not in wl, wl)
    ok("wake ohne Bindung: None", memory.wake(E) is None)
    f = fail_of(memory.wake_all, E)
    ok("wake_all ohne memory.db und memory.core -> 409 not_bound", f and f.status == 409 and f.code == "not_bound", f and f.code)
    pg("DELETE FROM note WHERE id=?", (other,))
    # Kontosprache en
    score_.set_user_lang(U, "en")
    st, d = memory.nap(U, {"stimmung": "curious", "gedanke": "Vectors first.", "valenz": 0.4}, tok_obj(U, 3))
    txt = json.loads(pg("SELECT props FROM note WHERE id=?", (d["id"],))[0][0])[pids(dbl)["Inhalt"]["id"]]
    wl = memory.wake(U, tok_obj(U, 3))
    ok("Kontosprache en: Schnappschuss-Text und Aufwach-Satz englisch",
       txt == "Mood: curious (valence +0.4)\nLast thought: Vectors first." and wl["text"].startswith("Last nap: just now.")
       and "changed since" in wl["text"], (txt, wl["text"]))


# --- 5. REST / MCP / 8 Schnappschuesse --------------------------------------------------------------------------------
def part_http():
    score_.set_user_lang(A, "de")
    ta = token(A, ["memory"])
    st, d = req("GET", "/api/v1/memory/core", bearer=ta)
    ok("GET /core: wie bisher + wake, kontext mit „## Aufwachen“, Aufwach-Text und Schnappschuss-Inhalt (Rahmen memory:core)",
       st == 200 and d["id"] == S["core"] and isinstance(d.get("wake"), dict) and "## Aufwachen" in d["kontext"]
       and d["wake"]["text"] in d["kontext"] and d["wake"]["schnappschuss"]["inhalt"].splitlines()[0] in d["kontext"]
       and 'source="memory:core"' in d["kontext"], (st, str(d)[:400]))
    st, d = req("GET", "/api/v1/memory/wake", bearer=ta)
    ok("GET /wake: kern + wake", st == 200 and d["kern"]["id"] == S["core"] and d["wake"]["anzahl"] >= 61, (st, str(d)[:300]))
    te = token(E, ["memory"])
    st, d = req("GET", "/api/v1/memory/wake", bearer=te)
    ok("GET /wake ohne Bindung -> 409 not_bound", st == 409 and d.get("code") == "not_bound", (st, d))
    U = S["leer"]
    memory.account_put(U, {"db": None, "core": api_v1.page_create(tok_obj(U, 3), {"title": "Kern", "markdown": "k"})[1]["id"]})
    st, d = req("GET", "/api/v1/memory/wake", bearer=token(U, ["memory"]))
    ok("GET /wake nur mit Ich-Kern: kern da, wake null", st == 200 and d["kern"] and d["wake"] is None, (st, d))
    n0 = pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0]
    st, d = req("POST", "/api/v1/memory/nap", {"stimmung": "wach", "gedanke": "REST geht."}, bearer=ta)
    ok("REST nap: 201", st == 201 and d.get("titel", "").startswith("Schnappschuss ") and d["zeilen"] == 2, (st, d))
    st, d = req("POST", "/api/v1/memory/nap", {"stimmung": "wach"}, bearer=ta)
    ok("REST nap ohne gedanke: 400 bad_request", st == 400 and d.get("code") == "bad_request" and "gedanke" in d.get("error", ""), (st, d))
    st, d = req("POST", "/api/v1/memory/nap", {"stimmung": "wach", "gedanke": "x"}, bearer=token(A, ["read:pages"]))
    ok("REST nap ohne Bereich memory: 403", st == 403, st)
    tm = token(A, ["mcp", "memory"])
    st, d = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "wake", "arguments": {}}}, bearer=tm)
    txt = (((d or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
    ok("MCP wake: Rahmen memory:core, kern + wake", st == 200 and not d["result"].get("isError") and 'source="memory:core"' in txt
       and '"kern"' in txt and '"wake"' in txt, (st, txt[:300]))
    st, d = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nap", "arguments":
                                 {"stimmung": "froh", "gedanke": "MCP geht.", "faeden": ["eins"]}}}, bearer=tm)
    txt = (((d or {}).get("result") or {}).get("content") or [{}])[0].get("text", "")
    ok("MCP nap: angelegt, Rahmen memory", st == 200 and not d["result"].get("isError") and 'source="memory"' in txt and '"titel"' in txt, (st, txt[:300]))
    st, d = req("POST", "/mcp", {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nap", "arguments":
                                 {"stimmung": "x" * 41, "gedanke": "y"}}}, bearer=tm)
    ok("MCP nap zu lang: isError", st == 200 and d["result"].get("isError"), d)
    n1 = pg("SELECT count(*) FROM note WHERE parent_id=?", (S["db"],))[0][0]
    ok("REST/MCP nap: genau 2 Zeilen mehr", n1 == n0 + 2, (n0, n1))
    st, d = req("GET", "/api/account/memory", uid=A)
    ok("GET /api/account/memory: snapshot count/latest", st == 200 and d["snapshot"]["found"] and d["snapshot"]["count"] >= 63
       and d["snapshot"]["latest"], d.get("snapshot"))
    # 8 Schnappschuesse bleiben alle (nichts geloescht)
    U2 = f"gs-acht-{SUF}"
    auth.create_user(U2, None, U2)
    S["acht"] = U2
    d8 = mkdb(U2, "Acht", PROPS, 4)
    memory.account_put(U2, {"db": d8})
    ids = [memory.nap(U2, {"stimmung": "s", "gedanke": f"g {i}"}, tok_obj(U2, 4))[1]["id"] for i in range(8)]
    left = {r[0] for r in pg("SELECT id FROM note WHERE parent_id=? AND deleted_at IS NULL", (d8,))}
    ok("8 Schnappschuesse: alle 8 bleiben (nichts geloescht/verdichtet)", left == set(ids), (len(left), len(ids)))


def part_log():
    """Server-Log ohne Inhalte (Stimmung/Gedanke/Fragen)."""
    logs = [os.path.join(os.environ.get("STATE_DIR", "/data"), "server.log")]
    txt = ""
    for p in logs:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                txt += f.read()
        except OSError:
            pass
    if not txt:
        note("Server-Log nicht lesbar - Pruefung uebersprungen")
        return
    ok("Server-Log: keine Schnappschuss-Inhalte/Fragen", "REST geht" not in txt and "MCP geht" not in txt and "Ceph Speicher" not in txt
       and "Nickerchen (" in txt, "Inhalt im Log")


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, B, C, E, S.get("leer"), S.get("acht")):
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
        for u in (A, B, C, E):
            auth.create_user(u, "Gs-Pass-12345", u)
        score_.set_user_lang(A, "de")
        set_embed(wait=True)
        for p in (part_vectors, part_nap, part_exclusion, part_wake, part_http, part_log):
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
    print(f"\nGedaechtnis-Schlaf {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
