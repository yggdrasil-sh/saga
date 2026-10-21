"""2.5 "Alle Anrufe ueber Knoten (auch 1:1)" (calls.horn_all, saga/calls.py _dm_horn_start): 1:1-Anrufe im Direkt-Chat
als Raum-Anruf ueber einen Knoten, mit Rueckfall auf direkt.

Ohne echten Knoten: ein gekoppelter Knoten als Registereintrag (Lastmeldung frisch, Zertifikat "self") gilt als bereit -
geprueft wird die Steuerung in Saga (Entscheidung, Rueckfall, Klingeln, Zustaende, Chat-Eintrag, Push), nicht die Medien.
Geprueft: config.horn_dm nur mit Einstellung; 409 fallback (off, caps, node, peer) ohne angelegten Anruf, danach direkter
Anruf mit derselben id; Erfolg: kind group, topo horn, dm, Kandidaten, Gegenueber als peer; call.invite mit dm; Anruf-
liste beim Gegenueber; Push-Text wie 1:1 mit dm; besetzt (direkter Anruf an die angerufene Person); Annehmen per join ->
call.state active mit dm; Auflegen -> Ende, Chat-Eintrag wie 1:1 mit Dauer; Ablehnen -> Ende "declined"; Abbrechen ->
verpasst + Benachrichtigung; besetzt beim Start; 2.4-Geraet des Gegenuebers: Beitritt 409 old; alter Client ohne via:
direkter Anruf wie bisher. Wegwerf-Konten ca-hd*."""
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

ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
CAPS24 = ["reneg", "want", "bye", "screen2", "multi"]
CALLS = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
         "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True}
NODE = "hd-knoten.example.org"
USERS = [("ca-hda", "Anna Eins"), ("ca-hdb", "Bert Zwei"), ("ca-hdc", "Cora Drei"), ("ca-hdd", "Dora Vier"), ("ca-hde", "Emil Fuenf"),
         ("ca-hdf", "Fritz Sechs"), ("ca-hdg", "Gabi Sieben"), ("ca-hdh", "Hans Alt")]


def setting(**kw):
    auth.save_setting("calls", dict(CALLS, **kw))
    auth._settings_cache["v"] = None
    time.sleep(5.5)                                   # Einstellungen im Server-Prozess (Zwischenspeicher 5 s)


def node(on=True):
    with connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE name=?", (NODE,))
        if on:
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, load, created, seen, cert) "
                      "VALUES (?,?,?,0,?,?,?,'ok',CAST(? AS JSONB),?,?,CAST(? AS JSONB))",
                      (secrets.token_hex(8), NODE, "dGVzdC1rbm90ZW4taGQtMDAwMDAwMDAwMDAwMDAwMDA=", f"https://{NODE}:8443", False, HN.VERSION,
                       json.dumps({"cpu": 0.05, "legs": 0, "max_legs": 48, "state": "ok"}), time.time(), time.time(),
                       json.dumps({"mode": "alpn", "state": "self"})))


def fresh():
    with connect() as c, c:
        c.execute("UPDATE horn_node SET seen=? WHERE name=?", (time.time(), NODE))


def caps(p, cs, dev=None):
    s, _ = req("PUT", "/api/calls/caps", p.k, {"device": (dev or p.dev)["id"], "caps": cs})
    return s == 200


def hcall(a, chat, cid=None, cs=CAPS, media="audio"):
    cid = cid or secrets.token_hex(16)
    fresh()
    s, d = req("POST", "/api/calls", a.k, {"id": cid, "chat_id": chat, "media": media, "device": a.dev["id"], "caps": cs, "via": "horn"})
    return cid, s, d


def row(cid):
    with connect() as c:
        r = c.execute("SELECT state, reason, kind, topo, callee FROM chat_call WHERE id=?", (cid,)).fetchone()
    return dict(zip(("state", "reason", "kind", "topo", "callee"), r)) if r else None


def logs(chat, cid):
    return [x for x in A_.call_rows(chat) if x.get("call") == cid]


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
    print(f"\nANRUFE-HORN-DM {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    P = {u: A_.Person(u) for u, _ in USERS}
    A, B, Cc, D, E, F, G, H = (P[u] for u, _ in USERS)
    for u, p in P.items():
        caps(p, CAPS24 if u == "ca-hdh" else CAPS)
    ab, cb, db_, eb, fb, gb, ah = A_.dm(A, B), A_.dm(Cc, B), A_.dm(D, B), A_.dm(E, B), A_.dm(F, B), A_.dm(G, B), A_.dm(A, H)
    node(False)

    # --- Einstellung und Rueckfaelle --------------------------------------------------------------------------------
    setting(horn_all=False)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"config: horn_dm aus ohne Einstellung ({cf.get('horn_dm')})", s == 200 and cf.get("horn_dm") is False)
    cid, s, d = hcall(A, ab)
    ok(f"via horn ohne Einstellung: 409 fallback direct, horn_block off ({s}, {d.get('horn_block')})",
       s == 409 and d.get("fallback") == "direct" and d.get("horn_block") == "off" and row(cid) is None)
    setting(horn_all=True)
    s, cf = req("GET", "/api/calls/config", A.k)
    ok(f"config: horn_dm mit Einstellung ({cf.get('horn_dm')})", cf.get("horn_dm") is True)
    cid, s, d = hcall(A, ab)
    ok(f"kein Knoten bereit: fallback, horn_block node ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "node" and row(cid) is None)
    node(True)
    cid, s, d = hcall(A, ab, cs=CAPS24)
    ok(f"Geraet ohne horn: fallback caps ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "caps" and row(cid) is None)
    cid, s, d = hcall(A, ah)
    ok(f"Gegenueber ohne Geraet mit horn: fallback peer ({s}, {d.get('horn_block')})", s == 409 and d.get("horn_block") == "peer" and row(cid) is None)
    cid2, s, d = A_.call(A, H, ah, cid=cid)
    ok(f"... danach direkter Anruf mit derselben id ({s}, kind {(d.get('call') or {}).get('kind')})", s == 200 and d["call"]["kind"] == "dm")
    req("POST", f"/api/calls/{cid}/end", A.k, {"reason": "hangup"})

    # --- Anruf ueber den Knoten -------------------------------------------------------------------------------------
    sb, sa = A_.Stream(B.k), A_.Stream(A.k)
    time.sleep(0.8)
    mb, ma = sb.mark(), sa.mark()
    cid, s, d = hcall(A, ab, media="video")
    k = d.get("call") or {}
    cands = (k.get("horn") or {}).get("candidates") or []
    ok(f"1:1 ueber Knoten: kind group, topo horn, dm, Kandidat ({s}, {k.get('kind')}, {k.get('topo')}, {[x.get('node') for x in cands]})",
       s == 200 and k.get("kind") == "group" and k.get("topo") == "horn" and k.get("dm") is True and cands and cands[0]["node"] == NODE)
    ok(f"Anrufobjekt: peer = Gegenueber, from = anrufende Person ({(k.get('peer') or {}).get('name')}, {(k.get('from') or {}).get('name')})",
       (k.get("peer") or {}).get("name") == "Bert Zwei" and (k.get("from") or {}).get("name") == "Anna Eins")
    inv = sb.wait("call.invite", lambda x: x.get("call_id") == cid, since=mb)
    ok(f"call.invite beim Gegenueber mit dm und Name ({inv and inv.get('dm')}, {inv and inv['from'].get('name')})",
       inv and inv.get("dm") is True and inv["from"]["name"] == "Anna Eins" and not inv.get("silent"))
    s, act = req("GET", "/api/calls/active", B.k)
    x = next((y for y in act.get("calls", []) if y["id"] == cid), {})
    ok(f"Anrufliste beim Gegenueber: dm, invite ringing, peer/from = anrufende Person ({x.get('invite')})",
       x.get("dm") is True and x.get("invite") == "ringing" and x["peer"]["name"] == "Anna Eins" and x["from"]["name"] == "Anna Eins")
    from saga import push
    with connect() as c:
        pm = push.message(c, B.uid, "call.group", actor=A.uid, data={"chat": ab, "call": cid})
    ok(f"Push: call.group mit dm, Text wie 1:1 ({pm and pm.get('body')})",
       pm and pm.get("dm") is True and pm.get("group") is True and "Gruppen" not in pm["body"] and pm["call"] == cid)
    cx, s, d = A_.call(Cc, B, cb)
    ok(f"direkter Anruf an die angerufene Person: besetzt ({s}, {d.get('busy')})", s == 409 and d.get("busy") is True)
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS24})
    ok(f"2.4-Geraet: Beitritt 409 old ({s}, {d.get('join_block')})", s == 409 and d.get("join_block") == "old")
    fresh()
    s, d = req("POST", f"/api/calls/{cid}/join", B.k, {"device": B.dev["id"], "role": "member", "caps": CAPS})
    ok(f"Annehmen per join (ohne Angebote): aktiv, Kandidaten ({s}, {(d.get('call') or {}).get('state')})",
       s == 200 and d["call"]["state"] == "active" and (d["call"].get("horn") or {}).get("candidates"))
    st = sa.wait("call.state", lambda y: y.get("call_id") == cid and y.get("state") == "active", since=ma)
    ok(f"call.state active mit dm beim Anrufer ({st})", st and st.get("dm") is True)
    time.sleep(1.2)
    s, d = req("POST", f"/api/calls/{cid}/end", B.k, {"reason": "hangup"})
    st = sa.wait("call.state", lambda y: y.get("call_id") == cid and y.get("state") == "ended", since=ma)
    lg = logs(ab, cid)
    ok(f"Auflegen: beendet, Eintrag wie 1:1 mit Dauer ({st and st.get('status')}, {lg})",
       st and st.get("status") == "ended" and len(lg) == 1 and lg[0]["status"] == "ended" and lg[0]["dur"] is not None
       and "group" not in lg[0] and lg[0]["from"] == A.uid and lg[0]["to"] == B.uid)

    # --- Ablehnen, Abbrechen, besetzt -------------------------------------------------------------------------------
    cid, s, d = hcall(D, db_)
    s2, _ = req("POST", f"/api/calls/{cid}/decline", B.k, {"reason": "declined"})
    r = row(cid)
    ok(f"Ablehnen beendet den Anruf ({s}, {s2}, {r})", s == 200 and s2 == 200 and r["state"] == "ended" and r["reason"] == "declined")
    lg = logs(db_, cid)
    ok(f"... Eintrag declined, keine Benachrichtigung verpasst ({lg})",
       lg and lg[0]["status"] == "declined" and not [n for n in A_.notifs(B.uid) if n.get("call") == cid])
    cid, s, d = hcall(E, eb)
    s2, _ = req("POST", f"/api/calls/{cid}/end", E.k, {"reason": "hangup"})
    r = row(cid)
    lg = logs(eb, cid)
    ok(f"Abbrechen beim Klingeln: verpasst + Benachrichtigung ({r and r['reason']}, {lg and lg[0]['status']})",
       r["state"] == "ended" and lg and lg[0]["status"] == "missed" and [n for n in A_.notifs(B.uid) if n.get("call") == cid])
    cg, s, d = A_.call(G, B, gb)
    ok(f"G ruft B direkt an ({s})", s == 200)
    cid, s, d = hcall(F, fb)
    lg = logs(fb, cid)
    ok(f"besetzt beim Start: 409 busy, Eintrag busy ({s}, {d.get('busy')}, {lg and lg[0]['status']})",
       s == 409 and d.get("busy") is True and lg and lg[0]["status"] == "busy")
    req("POST", f"/api/calls/{cg}/end", G.k, {"reason": "hangup"})

    # --- Alter Client: ohne via weiter direkt -------------------------------------------------------------------------
    cid, s, d = A_.call(G, B, gb)
    ok(f"alter Client ohne via: direkter Anruf wie bisher ({s}, {(d.get('call') or {}).get('kind')}, {(d.get('call') or {}).get('topo')})",
       s == 200 and d["call"]["kind"] == "dm" and d["call"]["topo"] == "p2p")
    req("POST", f"/api/calls/{cid}/end", G.k, {"reason": "hangup"})


if __name__ == "__main__":
    main()
