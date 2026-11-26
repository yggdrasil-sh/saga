"""2.5 Grundsatz Y fuer Gruppen (Entscheidung L1, 18.11.): Mit Gjallarhorn an und "Alle Anrufe ueber Knoten"
(calls.horn_all, ab Werk an) laufen auch Gruppenanrufe mit 3 und 4 Mitgliedern ueber den Knoten - direkt (Mesh) nur als
Rueckfall wie im 1:1. calls.horn_from bleibt die Schwelle fuer den Fall horn_all aus; ohne Gjallarhorn (ab Werk) bleibt
alles Mesh bis zur Mesh-Grenze.

Ohne echten Knoten: ein gekoppelter Knoten als Registereintrag (Lastmeldung frisch, Zertifikat "self") gilt als bereit -
geprueft wird die Wegwahl in Saga (POST /api/calls in freien Gruppen mit 2, 3 und 4 Mitgliedern). Faelle: horn_all an
(Standard ohne Merker und mit Merker) -> topo horn mit Kandidaten, auch mit horn_from 10 (wirkt nicht); horn_all bewusst
aus -> 3er/4er Mesh, 5er (ueber der Mesh-Grenze) Knoten; horn_all aus + horn_from 3 -> 3er Knoten; Rueckfaelle bei horn_all
an: Geraet ohne horn, kein Knoten bereit -> Mesh; Gjallarhorn aus -> Mesh. L1-Nacharbeit (Regel "Gegenstelle zu alt ->
direkt mit Hinweis" wie im 1:1): ein Mitglied nur mit 2.4-Geraet (multi ohne horn) -> 3er Mesh mit Hinweis (horn_mesh nennt
die Person), Einladung ringing; 5er (ueber der Mesh-Grenze) bleibt Knoten, die Person 'old'; altes Geraet ohne
Lebenszeichen seit 40 Tagen neben einem aktiven horn-Geraet zaehlt nicht -> Knoten; nur alte Geraete zaehlen alle. Gegenprobe: vor L1 (bb9fc69) liefen die
3er/4er-Faelle mit horn_all an als Mesh (Meldung FAIL in den ersten Pruefungen).
Gegen die laufende Instanz (HK_TEST_BASE, env.sh), Wegwerf-Konten ca-yg*:
    PYTHONPATH=tests/pgshim:. python tests/anrufe_y_gruppe.py"""
import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import horn as HN  # noqa: E402

req, res = A_.req, A_.res


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else " -> " + json.dumps(info, default=str)[:3000]), flush=True)
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True, "horn_on_y": True}
NODE = "yg-knoten.example.org"
# Konten je Lauf mit eigenem Anhang (Bugs-DB 2453): die Instanz zaehlt neue Anrufe je Konto und Stunde (CALL_RATE_H 40) im
# Speicher - mit festen Namen lief schon der zweite Lauf in derselben Stunde in 429.
SUF = secrets.token_hex(2)
USERS = [(f"ca-yga{SUF}", "Anna Eins"), (f"ca-ygb{SUF}", "Bert Zwei"), (f"ca-ygc{SUF}", "Cora Drei"), (f"ca-ygd{SUF}", "Dora Vier"),
         (f"ca-yge{SUF}", "Emil Fuenf"), (f"ca-ygf{SUF}", "Fritz Alt")]


def setting(**kw):
    auth.save_setting("calls", dict(CALLS, **kw))
    auth._settings_cache["v"] = None
    time.sleep(6.5)                                   # Einstellungen im Server-Prozess (Zwischenspeicher 5 s, mit Reserve)


def node(on=True):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (NODE,))
        if on:
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                      "VALUES (?,?,?,0,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                      (secrets.token_hex(8), NODE, "dGVzdC1rbm90ZW4teWctMDAwMDAwMDAwMDAwMDAwMDA=", f"https://{NODE}:8443", False, HN.VERSION,
                       json.dumps({"cpu": 0.05, "legs": 0, "max_legs": 48, "state": "ok"}), time.time(), time.time(),
                       json.dumps({"mode": "alpn", "state": "self"})))


ROT = {}


def start(a, chat, cs=CAPS, media="video"):
    """Gruppenanruf beginnen -> (topo, mit Kandidaten), danach sofort beenden. a: Liste der Mitglieder - die Anrufenden
    wechseln sich ab (Grenze CALL_RATE je Konto und Minute)."""
    i = ROT[chat] = ROT.get(chat, -1) + 1
    a = a[i % len(a)]
    cid = secrets.token_hex(16)
    for _ in range(6):                              # 429: Grenze je Konto und Minute - kurz warten
        with connect() as c, c:                     # Lastmeldung frisch (auch nach dem Warten)
            c.execute("UPDATE horn_node SET seen=? WHERE name=?", (time.time(), NODE))
        s, d = req("POST", "/api/calls", a.k, {"id": cid, "chat_id": chat, "media": media, "device": a.dev["id"], "caps": cs})
        if s != 429:
            break
        time.sleep(12)
    call = (d or {}).get("call") or {} if s == 200 else {}
    req("POST", f"/api/calls/{cid}/end", a.k, {"device": a.dev["id"]})
    LAST[0] = call
    return (call.get("topo"), bool((call.get("horn") or {}).get("candidates"))) if s == 200 else (f"HTTP {s}", False)


LAST = [None]


def caps(p, cs, dev=None):
    """Faehigkeiten melden, bis die Instanz sie annimmt -> HTTP-Status. Bugs-DB 2453: Lief vorher eine Reihe, die Anrufe
    abschaltet (anrufe_horn_y, enabled=False), antwortet die Instanz bis zu 5 s lang (Einstellungs-Zwischenspeicher) mit
    403 "Anrufe abgeschaltet" - die Meldung ging verloren und alle Mitglieder galten als alt (Mesh statt Knoten)."""
    s = None
    for _ in range(30):
        s, _d = req("PUT", "/api/calls/caps", p.k, {"device": (dev or p.dev)["id"], "caps": cs})
        if s == 200:
            break
        time.sleep(0.5)
    return s


def age(dev, days):
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=? WHERE id=?", (time.time() - days * 86400, dev["id"]))


def inv(call, who):
    return next((x.get("state") for x in (call or {}).get("invites") or [] if x.get("who") == who or x.get("ref") == who), None)


def main():
    A_.cleanup()
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
            if v is not None:
                auth.save_setting(k, v)
        auth._settings_cache["v"] = None
        node(False)
        A_.cleanup()
    print(f"\nANRUFE-Y-GRUPPE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    P = [A_.Person(u) for u, _ in USERS]
    Fz = P.pop()                                       # Fritz: nur ein 2.4-Geraet (multi ohne horn)
    st = [caps(p, CAPS) for p in P] + [caps(Fz, CAPS24)]
    ok(f"Faehigkeiten gemeldet ({st})", all(x == 200 for x in st), st)
    A = P[0]
    g, M = {}, {}
    for n in (2, 3, 4, 5):
        s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [u for u, _ in USERS[1:n]]})
        g[n] = d["chat"]["id"] if s == 200 else None
        M[g[n]] = P[:n]
    ok(f"freie Gruppen mit 2, 3, 4, 5 Mitgliedern ({list(g.values())})", all(g.values()))
    node(True)
    H = ("horn", True)

    # --- L1: horn_all an (ab Werk ohne Merker, und bewusst an) -> jede Gruppe ueber den Knoten -------------------------
    setting()
    r = {n: start(M[g[n]], g[n]) for n in (2, 3, 4, 5)}
    ok(f"Y ab Werk (horn_all ohne Merker): Gruppen 2/3/4/5 ueber den Knoten mit Kandidaten ({r})", all(v == H for v in r.values()), r)
    setting(horn_all=True, horn_all_y=True, horn_from=10)
    r = {n: start(M[g[n]], g[n]) for n in (3, 4)}
    ok(f"horn_all an + horn_from 10: 3er/4er trotzdem ueber den Knoten (horn_from wirkt nur ohne horn_all) ({r})", all(v == H for v in r.values()), r)
    r = start(M[g[4]], g[4], media="audio")
    ok(f"nur Ton, 4er: ueber den Knoten ({r})", r == H, r)
    # Rueckfaelle (direkt nur als Rueckfall): Geraet ohne horn, kein Knoten bereit
    r = start(M[g[3]], g[3], cs=CAPS24)
    ok(f"Rueckfall: anrufendes Geraet ohne horn -> Mesh ({r})", r == ("mesh", False), r)
    node(False)
    r = start(M[g[3]], g[3])
    ok(f"Rueckfall: kein Knoten bereit -> Mesh ({r})", r == ("mesh", False), r)
    node(True)

    # --- horn_all bewusst aus: Schwelle wie vor L1 (Mesh-Grenze + 1 bzw. horn_from) -------------------------------------
    setting(horn_all=False, horn_all_y=True)
    r = {n: start(M[g[n]], g[n]) for n in (3, 4, 5)}
    ok(f"horn_all aus: 3er/4er Mesh, 5er (ueber der Mesh-Grenze 4) Knoten ({r})", r[3] == ("mesh", False) and r[4] == ("mesh", False) and r[5] == H, r)
    setting(horn_all=False, horn_all_y=True, horn_from=3)
    r = {n: start(M[g[n]], g[n]) for n in (2, 3)}
    ok(f"horn_all aus + horn_from 3: 3er ueber den Knoten, 2er Mesh ({r})", r[3] == H and r[2] == ("mesh", False), r)

    # --- L1-Nacharbeit: Mitglied nur mit alter App -------------------------------------------------------------------
    setting()
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [f"ca-ygb{SUF}", f"ca-ygf{SUF}"]})
    g3a = d["chat"]["id"]
    s, d = req("POST", "/api/chats", A.k, {"kind": "free", "members": [f"ca-ygb{SUF}", f"ca-ygc{SUF}", f"ca-ygd{SUF}", f"ca-ygf{SUF}"]})
    g5a = d["chat"]["id"]
    M[g3a], M[g5a] = P[:2], P[:4]
    r = start(M[g3a], g3a)
    hm = (LAST[0] or {}).get("horn_mesh") or ""
    ok(f"3er mit Fritz (nur 2.4-Geraet): direkt mit Hinweis, Fritz klingelt ({r}, {hm[:90]}, {inv(LAST[0], 'ca-ygf' + SUF)})",
       r == ("mesh", False) and "Fritz Alt" in hm and "ältere Saga-Version" in hm and inv(LAST[0], f"ca-ygf{SUF}") == "ringing", LAST[0])
    r = start(M[g5a], g5a)
    ok(f"5er mit Fritz (ueber der Mesh-Grenze): Knoten, Fritz 'old' ({r}, {inv(LAST[0], 'ca-ygf' + SUF)})",
       r == H and inv(LAST[0], f"ca-ygf{SUF}") == "old" and not (LAST[0] or {}).get("horn_mesh"), LAST[0])
    f2 = Fz.add_device()
    ok("zweites Geraet von Fritz meldet horn", caps(Fz, CAPS, f2) == 200)
    age(Fz.dev, 40)
    r = start(M[g3a], g3a)
    ok(f"Fritz mit aktivem horn-Geraet, altes seit 40 Tagen still: 3er ueber den Knoten ({r})", r == H, LAST[0])
    age(Fz.dev, 2)
    r = start(M[g3a], g3a)
    ok(f"altes Geraet wieder aktiv (2 Tage) neben dem neuen: Fritz kann es (ein Geraet reicht) -> Knoten ({r})", r == H, LAST[0])
    age(Fz.dev, 40)
    age(f2, 50)
    ok("zweites Geraet von Fritz meldet wieder nur 2.4", caps(Fz, CAPS24, f2) == 200)
    age(f2, 50)
    r = start(M[g3a], g3a)
    ok(f"nur alte Geraete ohne Lebenszeichen (alle 2.4): zaehlen alle -> direkt ({r})", r == ("mesh", False), LAST[0])
    setting(horn_all=False, horn_all_y=True, horn_from=3)
    r = start(M[g3a], g3a)
    ok(f"horn_all aus + horn_from 3: 3er mit Fritz ueber den Knoten wie bisher, Fritz 'old' ({r}, {inv(LAST[0], 'ca-ygf' + SUF)})",
       r == H and inv(LAST[0], f"ca-ygf{SUF}") == "old", LAST[0])

    # --- ohne Gjallarhorn (ab Werk): alles Mesh --------------------------------------------------------------------------
    setting(horn=False)
    r = {n: start(M[g[n]], g[n]) for n in (3, 4)}
    ok(f"Gjallarhorn aus: 3er/4er Mesh ({r})", all(v == ("mesh", False) for v in r.values()), r)


if __name__ == "__main__":
    main()
