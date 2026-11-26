"""2.6 (Bugs-DB 2263-4): Grenzen der Anrufe ueber Knoten - beim Start festgehalten (Migration 40 chat_call.lim), voll,
ausgelastet (busy), Admin-Warnung max_legs.

Ohne echten Knoten: gekoppelte Knoten als Registereintrag (Lastmeldung frisch, Zertifikat "self") gelten als bereit (wie
tests/anrufe_y_gruppe.py) - "hl63-tcp" ohne UDP, "hl63-udp" mit UDP. Geprueft:
- horn.legs_need (2 Beine je Geraet ueber TCP/Tunnel, UDP-Grenze, Reserve bis FULL) und horn.capacity (ohne Messwerte None,
  mit Werten begrenzt durch Kerne, max_legs und link_mbit);
- Gruppenanruf startet auf dem TCP-Knoten -> limit 16 und chat_call.lim 16; alle Endpunkte ziehen auf den UDP-Knoten
  (Umzug/Kaskade) -> limit bleibt 16; Gegenprobe ohne lim (Anruf von vor Migration 40): 25 wie bisher; fix_limit setzt
  bei so einem Anruf nachtraeglich nichts;
- Admin senkt (horn_video 3): festgehaltene Grenze wird mitgesenkt -> 4. Person 409 join_block full mit limit 3;
- Knoten ausgelastet (Beine = max_legs) -> 409 join_block node, busy: true, Text "ausgelastet"; Knoten weg (Lastmeldung
  alt) -> 409 node ohne busy, Text "nicht erreichbar";
- Admin -> Anrufe: Warnung "HORN_MAX_LEGS (48) ist zu knapp" mit legs_need; mit max_legs 200 keine Warnung; ohne
  Tunnel-Knoten kein Hinweis HORN_MAX_PER_IP; ohne gemeldete Kerne keine Zeile "Geschaetzte Kapazitaet" (Messwerte 2263-9 in CAP_*);
- Standard an: Altwert horn false ohne Merker = an, mit Merker horn_on_y gilt aus.
- Testhaken des Messwerkzeugs (Bugs-DB 2438, im Testprozess): SAGA_TEST_HORN_LIMITS hebt die Obergrenzen nur mit
  SAGA_TEST_HOOKS=1 UND SAGA_TEST_TOKEN >= 32 Zeichen (an; aus; Token zu kurz; Unsinn im JSON -> wie heute), der Admin
  senkt weiter; SAGA_TEST_HORN_PIN legt die Kandidaten eines Kontos auf einen Knoten (nur mit Testhaken).
Gegen die laufende Instanz (HK_TEST_BASE/HK_TEST_ADMIN, env.sh), Wegwerf-Konten hl63-*:
    PYTHONPATH=tests/pgshim:. python tests/horn_grenzen_2263.py"""
import base64
import hashlib
import http.client
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from saga import calls as CL, horn as HN  # noqa: E402

req, res = A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_on_y": True}
NT, NU = "hl63-tcp.example.org", "hl63-udp.example.org"
USERS = [("hl63-a", "Anna Eins"), ("hl63-b", "Bert Zwei"), ("hl63-c", "Cora Drei"), ("hl63-d", "Dora Vier"), ("hl63-e", "Emil Fuenf")]
ADMIN_ID = "hl63-adm"


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:2000]), flush=True)


def setting(**kw):
    auth.save_setting("calls", dict(CALLS, **kw))
    auth._settings_cache["v"] = None
    time.sleep(6.5)                                   # Einstellungen im Server-Prozess (Zwischenspeicher 5 s, mit Reserve)


def node(name, udp, legs=0, max_legs=48, seen=None):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (name,))
        if legs is None:
            return None
        nid = secrets.token_hex(8)
        c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                  "VALUES (?,?,?,0,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                  (nid, name, base64.b64encode(secrets.token_bytes(32)).decode(), f"https://{name}:8443", udp, HN.VERSION,
                   json.dumps({"cpu": 0.05, "legs": legs, "max_legs": max_legs, "state": "ok"}), time.time(),
                   time.time() if seen is None else seen, json.dumps({"mode": "alpn", "state": "self"})))
        return nid


def load(name, legs=None, max_legs=None, seen=None):
    with connect() as c, c:
        ld = c.execute("SELECT load FROM horn_node WHERE name=?", (name,)).fetchone()[0] or {}
        if legs is not None:
            ld["legs"] = legs
        if max_legs is not None:
            ld["max_legs"] = max_legs
        c.execute("UPDATE horn_node SET load=CAST(? AS JSONB), seen=? WHERE name=?", (json.dumps(ld), time.time() if seen is None else seen, name))


def fresh():
    with connect() as c, c:
        c.execute("UPDATE horn_node SET seen=? WHERE name IN (?,?)", (time.time(), NT, NU))


def lim_db(cid):
    with connect() as c:
        r = c.execute("SELECT lim FROM chat_call WHERE id=?", (cid,)).fetchone()
    return r[0] if r else "fehlt"


def climit(cid):
    with connect() as c:
        return CL.climit(c, CL._load(c, cid))


def api_limit(p, cid):
    s, d = req("GET", f"/api/calls/{cid}?device={p.dev['id']}", p.k)
    return (d.get("call") or d).get("limit") if s == 200 else f"HTTP {s}"


def join(p, cid, tries=3):
    for _ in range(tries):
        fresh()
        s, d = req("POST", f"/api/calls/{cid}/join", p.k, {"device": p.dev["id"], "role": "member", "caps": CAPS})
        if s != 429:
            return s, d
        time.sleep(12)
    return s, d


def admin_html():
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + auth.SECRET).encode()).hexdigest())
    ck = sa.sign(base64.b64encode(json.dumps({"aid": ADMIN_ID, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    c = http.client.HTTPConnection(A_.ADM.hostname, A_.ADM.port, timeout=30)
    c.request("GET", "/calls", headers={"Cookie": "hk_admin=" + ck, "Accept-Language": "de-DE"})
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, data


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'hl63-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "chat_identity", "chat_device", "chat_link", "chat_file", "push_sub", "push_queue", "device_login"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'hl63-%'")
        c.execute("DELETE FROM chat_call WHERE caller LIKE 'hl63-%' OR callee LIKE 'hl63-%'")
        c.execute("DELETE FROM chat WHERE created_by LIKE 'hl63-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'hl63-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'hl63-%'")
        c.execute("DELETE FROM horn_node WHERE name IN (?,?)", (NT, NU))


def units():
    ok(f"legs_need: TCP 2 x 50 bzw. UDP 50, Reserve bis FULL -> 112 ({HN.legs_need()}, {HN.legs_need(True)})",
       HN.legs_need() == 112 and HN.legs_need(True) == 112)
    m = HN.capacity({"link_mbit": None}, 4, 200)              # Messwerte 2263-9: 4*0,9/0,016=225 -> max_legs 200: 90
    t = HN.capacity({"link_mbit": 100}, 16, 400)              # Upload 100 Mbit: 100*0,9/2,1=42 Video, /0,13=692 -> Kerne 3130/max_legs 180
    ok(f"capacity mit den Messwerten 2263-9 (CAP_CORE {HN.CAP_CORE}): {m}, {t}",
       HN.CAP_CORE == {"video": 0.016, "audio": 0.0046} and m == {"video": 90, "audio": 90} and t == {"video": 42, "audio": 180})
    HN._LIVE["hl63-tun"] = {"tunnel": True}                 # eingebauter im Tunnel-Betrieb: CAP_TUNNEL (inkl. saga-media)
    try:
        tu = HN.capacity({"id": "hl63-tun", "builtin": True, "link_mbit": 100}, 16, 400)   # 100*0,9/1,1=81 Video
    finally:
        HN._LIVE.pop("hl63-tun", None)
    ok(f"capacity Tunnel-Betrieb mit CAP_TUNNEL ({tu})", tu == {"video": 81, "audio": 180})
    keep = dict(HN.CAP_CORE), dict(HN.CAP_MBIT)
    try:
        HN.CAP_CORE.update(video=None, audio=None)
        ok("capacity: ohne Messwerte (CAP_CORE None) -> None", HN.capacity({"link_mbit": None}, 8, 200) is None)
        HN.CAP_CORE.update(video=0.5, audio=0.125)
        HN.CAP_MBIT.update(video=2.0, audio=0.1)
        a = HN.capacity({"link_mbit": None}, 4, 200)          # 4*0.9/0.5=7, 4*0.9/0.125=28 (max_legs: 90)
        b = HN.capacity({"link_mbit": None}, 16, 48)          # Kerne reichen, max_legs 48 -> 21 Geraete
        d = HN.capacity({"link_mbit": 10}, 16, 200)           # link 10 Mbit: 10*0.9/2=4 Video
        ok(f"capacity mit Messwerten: Kerne, max_legs und link_mbit begrenzen ({a}, {b}, {d})",
           a == {"video": 7, "audio": 28} and b == {"video": 21, "audio": 21} and d["video"] == 4 and HN.capacity({}, 0, 200) is None)
    finally:
        HN.CAP_CORE.clear(), HN.CAP_CORE.update(keep[0])
        HN.CAP_MBIT.clear(), HN.CAP_MBIT.update(keep[1])


HOOK_ENV = ("SAGA_TEST_HOOKS", "SAGA_TEST_TOKEN", "SAGA_TEST_HORN_LIMITS", "SAGA_TEST_HORN_PIN")


def hooks(on=True, token=None, **kw):
    """Testhaken-Umgebung im Testprozess setzen (horn.test_limits/test_pins lesen sie bei jedem Aufruf)."""
    for k in HOOK_ENV:
        os.environ.pop(k, None)
    if on:
        os.environ["SAGA_TEST_HOOKS"] = "1"
    if token is not False:
        os.environ["SAGA_TEST_TOKEN"] = token or "hl63-" + secrets.token_hex(16)
    for k, v in kw.items():
        os.environ["SAGA_TEST_HORN_" + k.upper()] = v


def lims():
    return HN.limits(None), HN.limits(None, udp=True)


def testhaken():
    keep = {k: os.environ.get(k) for k in HOOK_ENV}
    today = ((16, 50), (25, 50))
    want = '{"tcp":[20,50],"udp":[30,100]}'
    try:
        hooks(on=False, token=False)
        ok(f"Testhaken: ohne Umgebung wie heute {lims()}", lims() == today and HN.test_limits() is None)
        hooks(limits=want)
        ok(f"Testhaken an (HOOKS=1, Token 37 Zeichen): TCP 20/50, UDP 30/100 {lims()}", lims() == ((20, 50), (30, 100)))
        ok(f"Testhaken an: legs_need folgt (TCP 2 x 50, UDP 100 -> 112) ({HN.legs_need(True)})", HN.legs_need(True) == 112)
        hooks(limits='{"tcp":[20,50]}')
        ok(f"Testhaken an, nur tcp: UDP bleibt 25/50 {lims()}", lims() == ((20, 50), (25, 50)))
        hooks(on=False, limits=want)
        ok(f"Testhaken aus (ohne SAGA_TEST_HOOKS, gueltiges Token): wie heute {lims()}", lims() == today)
        os.environ["SAGA_TEST_HOOKS"] = "0"
        ok(f"Testhaken aus (SAGA_TEST_HOOKS=0): wie heute {lims()}", lims() == today)
        hooks(token=False, limits=want)
        ok(f"Testhaken ohne Token: wie heute {lims()}", lims() == today)
        hooks(token="x" * 31, limits=want)
        ok(f"Token zu kurz (31 Zeichen): wie heute {lims()}", lims() == today)
        hooks(token="x" * 32, limits=want)
        ok(f"Token genau 32 Zeichen: wirksam {lims()}", lims() == ((20, 50), (30, 100)))
        bad = ["kaputt{", "[]", "{}", '{"tcp":[16]}', '{"tcp":[60,50]}', '{"tcp":[16,500]}', '{"tcp":[1,50]}', '{"x":[16,50]}',
               '{"tcp":["16",50]}', '{"tcp":[true,50]}', '{"tcp":[16.5,50]}', '{"tcp":[16,50],"udp":null}', '"16"', "null", "[" * 100000]
        got = {}
        for b in bad:
            hooks(limits=b)
            got[b] = lims()
        ok("Unsinn im JSON (Syntax, Laenge, Video > Ton, > 100, < 2, fremder Weg, Text/Bool/Komma, null, tief verschachtelt) -> wie heute",
           all(v == today for v in got.values()), {k[:40]: v for k, v in got.items() if v != today})
        hooks(limits=want)
        auth.save_setting("calls", dict(CALLS, horn_video=3, horn_audio=20))
        auth._settings_cache["v"] = None
        ok(f"Testhaken an, Admin senkt (3/20): Admin gewinnt {lims()}", lims() == ((3, 20), (3, 20)))
    finally:
        auth.save_setting("calls", CALLS)
        auth._settings_cache["v"] = None
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def pin_check(cid, A):
    """Testhaken SAGA_TEST_HORN_PIN im Testprozess: Kandidaten von A (Anruf laeuft auf NU) nur auf NT."""
    from saga import horn_web as HW
    keep = {k: os.environ.get(k) for k in HOOK_ENV}

    def names():
        fresh()
        with connect() as c:
            return [x["node"] for x in HW.candidates(c, CL._load(c, cid), A.dev["id"])]
    try:
        hooks(on=False, token=False)
        base = names()
        hooks(pin=json.dumps({A.uid: NT}))
        pinned = names()
        hooks(pin=json.dumps({"hl63-niemand": NT}))
        other = names()
        hooks(token="x" * 31, pin=json.dumps({A.uid: NT}))
        short = names()
        hooks(pin=json.dumps({A.uid: 5}))
        junk = names()
        ok(f"Testhaken PIN: ohne {base}, A -> NT {pinned}, anderes Konto {other}, Token zu kurz {short}, Unsinn {junk}",
           base == [NU] and pinned == [NT] and other == base and short == base and junk == base)
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def start_udp(A, B):
    """Bugs-DB 2447 (Entscheidung 26.11., Frage D): beide Knoten bereit -> der Anruf startet auf dem UDP-Knoten (vorher gewann bei
    gleicher Last der ohne UDP), Grenze nach UDP festgehalten; ab PENALTY zaehlt UDP nicht mehr. Raum auf einem
    Folgeknoten: erstes Bein dort -> "open" mit max_peers (im Testprozess, horn.send aufgezeichnet)."""
    lv, _ = HN.limits(None)
    uv, _ = HN.limits(None, udp=True)

    with connect() as c, c:                            # fruehere Anrufe der Reihe (C/D noch drin) hart beenden
        c.execute("UPDATE chat_call_leg SET state='left' WHERE call_id IN (SELECT id FROM chat_call WHERE caller LIKE 'hl63-%')")
        c.execute("UPDATE chat_call SET state='ended' WHERE caller LIKE 'hl63-%'")

    def new_chat():                                    # je Anruf eine eigene Gruppe (der vorige laeuft mit C/D weiter)
        s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [B.uid, "hl63-c"]})
        return d["chat"]["id"] if s == 200 else None
    chat = new_chat()
    load(NT, legs=0)
    load(NU, legs=0)
    cid = secrets.token_hex(16)
    fresh()
    with connect() as c:
        order = [n["name"] for n in HN.ready_nodes(c) if n["name"] in (NT, NU)]
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": chat, "media": "video", "device": A.dev["id"], "caps": CAPS})
    call = (d or {}).get("call") or {}
    with connect() as c:
        nodes = {r[0] for r in c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND state='active'", (cid,)).fetchall()}
        nu, nt = (c.execute("SELECT id FROM horn_node WHERE name=?", (x,)).fetchone()[0] for x in (NU, NT))
    ok(f"2447: beide bereit -> UDP-Knoten zuerst {order}, Start dort, limit {uv} (UDP), lim {uv} ({s}, {call.get('limit')}, {lim_db(cid)})",
       s == 200 and order == [NU, NT] and nodes == {nu} and call.get("limit") == uv and lim_db(cid) == uv, d)
    s, d = join(B, cid)
    ok(f"2447: B tritt bei ({s})", s == 200, d)
    sent, keep = [], HN.send
    HN.send = lambda nid, msg: sent.append((nid, msg)) or True
    try:                                               # je Schritt eine Transaktion: room_open geht erst nach dem Commit raus
        with connect() as c, c:
            HN.set_leg_node(c, cid, B.dev["id"], nt)          # Kaskade/Ueberlauf: erstes Bein auf dem Folgeknoten
        first = list(sent)
        with connect() as c, c:
            HN.set_leg_node(c, cid, B.dev["id"], nt)          # dasselbe Bein noch einmal (Erneuerung /horn mit node)
        with connect() as c, c:
            HN.set_leg_node(c, cid, A.dev["id"], nt)          # zweites Bein dort
        more = sent[len(first):]
    finally:
        HN.send = keep
    ok(f"2447: Folgeknoten -> genau ein open mit max_peers {uv} {first}, danach keins {more}",
       len(first) == 1 and first[0][0] == nt and first[0][1].get("t") == "open" and first[0][1].get("max_peers") == uv and not more)
    for p in (A, B):
        req("POST", f"/api/calls/{cid}/end", p.k, {"device": p.dev["id"]})
    with connect() as c, c:
        c.execute("UPDATE chat_call SET state='ended' WHERE id=?", (cid,))
    chat = new_chat()
    load(NU, legs=36)                                  # 75 % >= PENALTY (0,7), < FULL
    fresh()
    with connect() as c:
        order = [n["name"] for n in HN.ready_nodes(c) if n["name"] in (NT, NU)]
    cid = secrets.token_hex(16)
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": chat, "media": "video", "device": A.dev["id"], "caps": CAPS})
    call = (d or {}).get("call") or {}
    ok(f"2447: UDP-Knoten ueber PENALTY -> der freie ohne UDP zuerst {order}, limit {lv} ({s}, {call.get('limit')}, {lim_db(cid)})",
       s == 200 and order == [NT, NU] and call.get("limit") == lv and lim_db(cid) == lv, d)
    req("POST", f"/api/calls/{cid}/end", A.k, {"device": A.dev["id"]})
    load(NU, legs=0)


def run():
    units()
    testhaken()
    P = [A_.Person(u) for u, _ in USERS]
    for p in P:
        req("PUT", "/api/calls/caps", p.k, {"device": p.dev["id"], "caps": CAPS})
    A, B, C, D, E = P
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:]]})
    ok(f"freie Gruppe mit 5 ({s})", s == 200)
    chat = d["chat"]["id"]
    setting()

    # --- Start auf dem TCP-Knoten: Grenze 16 festgehalten ------------------------------------------------------------
    node(NT, False)
    node(NU, True, legs=None)                          # der UDP-Knoten kommt erst spaeter dazu
    cid = secrets.token_hex(16)
    fresh()
    s, d = req("POST", "/api/calls", A.k, {"id": cid, "chat_id": chat, "media": "video", "device": A.dev["id"], "caps": CAPS})
    call = (d or {}).get("call") or {}
    ok(f"Start: ueber Knoten, limit 16 (TCP), lim 16 in der Datenbank ({s}, {call.get('topo')}, {call.get('limit')}, {lim_db(cid)})",
       s == 200 and call.get("topo") == "horn" and call.get("limit") == 16 and lim_db(cid) == 16, d)
    s, d = join(B, cid)
    ok(f"B tritt bei ({s})", s == 200, d)

    # --- Umzug/Kaskade auf den UDP-Knoten: Grenze bleibt ------------------------------------------------------------
    nu = node(NU, True)
    with connect() as c, c:
        c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND state='active'", (nu, cid))
    ok(f"alle Endpunkte auf dem UDP-Knoten: limit bleibt 16 (API {api_limit(A, cid)}, climit {climit(cid)})",
       api_limit(A, cid) == 16 and climit(cid) == 16)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET lim=NULL WHERE id=?", (cid,))
    ok(f"Gegenprobe ohne lim (Anruf von vor Migration 40): wie bisher 25 nach dem UDP-Knoten (API {api_limit(A, cid)})",
       api_limit(A, cid) == 25 and climit(cid) == 25)
    with connect() as c, c:
        HN.fix_limit(c, cid, C.dev["id"], nu)
    ok(f"fix_limit setzt bei einem Anruf, der schon auf Knoten lief, nichts nachtraeglich ({lim_db(cid)})", lim_db(cid) is None)
    pin_check(cid, A)
    with connect() as c, c:
        c.execute("UPDATE chat_call SET lim=16 WHERE id=?", (cid,))

    # --- Admin senkt, voll -> 409 full -------------------------------------------------------------------------------
    setting(horn_video=3)
    ok(f"Admin senkt auf 3: festgehaltene Grenze wird mitgesenkt (API {api_limit(A, cid)}, lim bleibt {lim_db(cid)})",
       api_limit(A, cid) == 3 and lim_db(cid) == 16)
    s, d = join(C, cid)
    ok(f"C tritt bei (3/3) ({s})", s == 200, d)
    s, d = join(D, cid)
    ok(f"D: voll -> 409 join_block full, limit 3 ({s}, {d.get('join_block')}, {d.get('limit')})",
       s == 409 and d.get("join_block") == "full" and d.get("limit") == 3 and "busy" not in d, d)
    setting()

    # --- ausgelastet -> node + busy; weg -> node ohne busy ----------------------------------------------------------
    load(NT, legs=48, max_legs=48)
    load(NU, legs=47, max_legs=48)                     # 98 % > FULL
    with connect() as c:
        busy = HN.busy(c)
    s, d = join(D, cid)
    ok(f"Knoten ausgelastet: 409 join_block node, busy true, Text 'ausgelastet' ({s}, {d.get('join_block')}, {d.get('busy')}, {busy})",
       s == 409 and d.get("join_block") == "node" and d.get("busy") is True and "ausgelastet" in (d.get("error") or "") and busy, d)
    load(NT, legs=0, seen=time.time() - 600)
    load(NU, legs=0, seen=time.time() - 600)
    with connect() as c:
        busy = HN.busy(c)
    s, d = req("POST", f"/api/calls/{cid}/join", D.k, {"device": D.dev["id"], "role": "member", "caps": CAPS})
    ok(f"Knoten weg (Lastmeldung alt): 409 node ohne busy, Text 'nicht erreichbar' ({s}, {d.get('join_block')}, {d.get('busy')}, {busy})",
       s == 409 and d.get("join_block") == "node" and "busy" not in d and "erreichbar" in (d.get("error") or "") and not busy, d)
    load(NT, legs=0)
    load(NU, legs=0)
    s, d = join(D, cid)
    ok(f"wieder frei: D tritt bei ({s})", s == 200, d)
    req("POST", f"/api/calls/{cid}/end", A.k, {"device": A.dev["id"]})
    start_udp(A, B)

    # --- Standard an (2263-4, letzter Commit): Altwert von vor 2.6 ohne Merker = an -----------------------------------
    auth.save_setting("calls", {k: v for k, v in CALLS.items() if k not in ("horn", "horn_on_y")} | {"horn": False})
    auth._settings_cache["v"] = None
    time.sleep(6.5)
    s, d = req("GET", "/api/calls/config", A.k)
    ok(f"Standard an: Altwert horn false ohne Merker -> Gjallarhorn an (features horn, e2e) ({s})",
       s == 200 and "horn" in d.get("features", []) and d.get("e2e") == "saga-horn/v1", d.get("features"))
    setting(horn=False)                                # mit Merker: bewusst aus
    s, d = req("GET", "/api/calls/config", A.k)
    ok(f"... mit Merker horn_on_y gilt aus ({s})", s == 200 and "horn" not in d.get("features", []), d.get("features"))
    setting()

    # --- Admin: Warnung max_legs --------------------------------------------------------------------------------------
    auth.get_user(ADMIN_ID) or auth.create_user(ADMIN_ID, secrets.token_urlsafe(12), "Admin HL63", is_admin=True)
    s, h = admin_html()
    w = "HORN_MAX_LEGS (48) ist zu knapp: Ein Anruf an der Grenze braucht an diesem Knoten bis zu 112 Beine"
    ok(f"Admin: Warnung max_legs 48 < 112 an beiden Knoten ({s}, {h.count(w)})", s == 200 and h.count(w) == 2)
    ok("Admin: ohne Tunnel-Knoten kein Hinweis HORN_MAX_PER_IP, ohne gemeldete Kerne (kein Steuerkanal) keine Kapazitaet",
       "horn-perip" not in h and "Geschätzte Kapazität" not in h)
    load(NT, max_legs=200)
    load(NU, max_legs=112)
    s, h = admin_html()
    ok(f"Admin: max_legs 200 bzw. 112 -> keine Warnung ({s})", s == 200 and "ist zu knapp" not in h and NT in h and NU in h)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_group_scope", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.save_setting("share_group_scope", False)
    for u, n in USERS:
        auth.create_user(u, A_.PW, n)
    try:
        run()
    finally:
        for x in A_.STREAMS:
            x.close()
        for k, v in keep.items():
            auth.save_setting(k, v if v is not None else ({} if k == "calls" else True))
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nHORN-GRENZEN-2263 {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
