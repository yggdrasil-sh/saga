"""Chat-Mehrfachauswahl (2.5, saga/chat.py) und Hinweis, warum Reaktionen fehlen (saga/chat_x3.py: x3_why) - Server-Seite,
PyNaCl als Client.

Geprueft: POST /api/chats/{id}/messages/delete (mehrere eigene Nachrichten fuer alle loeschen: deleted/failed, fremde ->
not_own, unbekannte -> not_found, Grenze 100, ungueltige Angaben, Nicht-Mitglied 404, Live chat.deleted je Nachricht,
Anhaenge weg; Stufe 3 ueber FED.delete_msg); POST /api/chats/{id}/hide (nur fuer mich: Liste und Abgleich ?after= lassen
die Nachricht weg, die anderen sehen sie weiter, Ungelesen zaehlt sie nicht, Live chat.hidden nur an mich, Grenze 500,
unbekannte ids zaehlen nicht, doppelt ausblenden ist harmlos); Aufraeumen ("fuer mich loeschen", Austritt aus einer Gruppe,
Nachricht fuer alle geloescht, Chat geloescht, Kontoloeschung); x3_why im Chat-Objekt: 'setting', 'devices' mit x3_wait
(Namen nur eigener Geraete, andere nur als Anzahl, Stichtag), 'pending', bei x3_ok kein x3_why; Format v2: 'server' mit
x3_hosts, 'pending' (Anfrage), 'readonly'. Wegwerf-Konten cs-*; schaltet chat_v3 nur fuer den Test ein."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, Person, Stream)
import chat_v3_umstellung as U  # noqa: E402  (Konto mit Zertifikaten v2: P)
from saga import chat as CH, chat_x3 as X3, fed_chat as FC, federation as F  # noqa: E402

req, res = CT.req, CT.res
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cs-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'cs-%' OR created_by LIKE 'cs-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cs-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'cs-%'")


def setv3(on, days=None):
    auth.save_setting("chat_v3", on)
    if days is not None:
        auth.save_setting("chat_v3_days", days)
    time.sleep(WAIT)


def hidden_rows(uid, cid=None):
    with connect() as c:
        if cid is None:
            return c.execute("SELECT COUNT(*) FROM chat_hidden WHERE uid=?", (uid,)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM chat_hidden WHERE uid=? AND chat_id=?", (uid, cid)).fetchone()[0]


def ids_of(d):
    return [m["id"] for m in (d or {}).get("messages", [])]


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3", "chat_v3_days", "chat_v3_since")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("chat_v3", False), ("chat_v3_days", 90),
                 ("chat_v3_since", time.time())):
        auth.save_setting(k, v)
    time.sleep(WAIT)
    try:
        run()
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nCHAT-AUSWAHL {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    A, B = U.P("cs-anna", "Anna Sel"), U.P("cs-bert", "Bert Sel")
    Cn, E = U.P("cs-cora", "Cora Sel"), U.P("cs-emil", "Emil Sel", lv=2)
    X = U.P("cs-xaver", "Xaver Sel")
    for p in (A, B, Cn, X):
        p.seen(lv=3)
    E.seen(lv=2)
    sa, sb = CT.Stream(A.k), CT.Stream(B.k)
    time.sleep(0.5)
    dm = A.api("POST", "/api/chats", {"kind": "dm", "user": "cs-bert"})[1]["chat"]
    cid = dm["id"]
    ok("Ausgang: Direkt-Chat v1 (Stufe 3 aus)", U.v_of(cid)["v"] == 1)

    # --- Mehrere eigene Nachrichten fuer alle loeschen ------------------------------------------------------------------
    sent = [A.send(cid, f"Anna {i}") for i in range(4)]
    _, rb, mb = B.send(cid, "Bert 1")
    a = [m["id"] for _, _, m in sent]
    unknown = C.hexid()
    mk = sb.mark()
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [a[0], a[1], mb["id"], unknown, a[0]]})
    ok("Bulk-Loeschen: eigene geloescht, fremde not_own, unbekannte not_found (Doppelte einmal)", s == 200 and d.get("deleted") == [a[0], a[1]]
       and d.get("failed") == {mb["id"]: "not_own", unknown: "not_found"}, (s, d))
    ok("Live chat.deleted je Nachricht an die anderen", all(sb.wait("chat.deleted", lambda e, i=i: e.get("id") == i, mk) for i in (a[0], a[1])))
    left = ids_of(B.read(cid)[1])
    ok("Bei Bert weg, Rest und Berts Nachricht bleiben", a[0] not in left and a[1] not in left and a[2] in left and mb["id"] in left, left)
    s1, d1 = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [C.hexid() for _ in range(101)]})
    s2, _ = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": []})
    s3, _ = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": ["x' OR 1=1"]})
    s4, _ = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": a[2]})
    ok(f"Grenze 100 und ungueltige Angaben -> 400 ({s1}, {s2}, {s3}, {s4})", (s1, s2, s3, s4) == (400, 400, 400, 400), d1)
    s, _ = X.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [a[2]]})
    ok(f"Nicht-Mitglied -> 404 ({s})", s == 404 and a[2] in ids_of(B.read(cid)[1]))
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [C.hexid() for _ in range(100)]})
    ok("100 ids sind erlaubt", s == 200 and len(d.get("failed", {})) == 100 and d.get("deleted") == [], (s, str(d)[:200]))
    # Anhang geht mit
    raw = os.urandom(200)
    s, fu = req("POST", f"/api/chats/{cid}/files", A.k, raw=raw)
    _, _, mf = A.send(cid, "mit Anhang", files=[{"id": fu.get("id"), "k": "x", "n": "a.bin", "m": "application/octet-stream", "s": 200}])
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [mf["id"]]})
    with connect() as c:
        fl = c.execute("SELECT COUNT(*) FROM chat_file WHERE id=?", (fu.get("id"),)).fetchone()[0]
    ok("Anhang der geloeschten Nachricht weg", s == 200 and d["deleted"] == [mf["id"]] and fl == 0, (s, d, fl))

    # --- Nur fuer mich ausblenden -----------------------------------------------------------------------------------
    mk, mka = sb.mark(), sa.mark()
    s, d = B.api("POST", f"/api/chats/{cid}/hide", {"ids": [a[2], unknown]})
    ok("Ausblenden: Antwort hidden=1 (unbekannte zaehlen nicht)", s == 200 and d == {"hidden": 1}, (s, d))
    ev = sb.wait("chat.hidden", lambda e: e.get("chat_id") == cid, mk)
    ok("Live chat.hidden {chat_id, ids} an meine Geraete", ev == {"chat_id": cid, "ids": [a[2]]}, ev)
    time.sleep(0.4)
    ok("... nicht an die anderen", "chat.hidden" not in sa.types(mka))
    bl, al = ids_of(B.read(cid)[1]), ids_of(A.read(cid)[1])
    ok("Liste: bei Bert weg, bei Anna noch da", a[2] not in bl and a[3] in bl and a[2] in al, (bl, al))
    seq2 = next(m["seq"] for m in A.read(cid)[1]["messages"] if m["id"] == a[2])
    ok("Abgleich (?after=) laesst sie weg", a[2] not in ids_of(B.read(cid, after=seq2 - 1)[1]) and a[3] in ids_of(B.read(cid, after=seq2 - 1)[1]))
    s, d = B.api("POST", f"/api/chats/{cid}/hide", {"ids": [a[2]]})
    ok("Nochmals ausblenden ist harmlos", s == 200 and d == {"hidden": 1} and hidden_rows("cs-bert", cid) == 1, (s, d))
    s1, _ = B.api("POST", f"/api/chats/{cid}/hide", {"ids": [C.hexid() for _ in range(501)]})
    s2, d2 = B.api("POST", f"/api/chats/{cid}/hide", {"ids": [C.hexid() for _ in range(500)]})
    s3, _ = X.api("POST", f"/api/chats/{cid}/hide", {"ids": [a[3]]})
    ok(f"Grenze 500 ({s1}/{s2}), Nicht-Mitglied 404 ({s3})", s1 == 400 and s2 == 200 and d2 == {"hidden": 0} and s3 == 404)
    _, _, mn = A.send(cid, "neu und gleich ausgeblendet")
    B.api("POST", f"/api/chats/{cid}/hide", {"ids": [mn["id"]]})
    ub = B.chat(cid).get("unread")
    with connect() as c:
        raw_unread = c.execute("SELECT COUNT(*) FROM chat_msg m JOIN chat_member x ON x.chat_id=m.chat_id AND x.uid='cs-bert' "
                               "WHERE m.chat_id=? AND m.kind='msg' AND m.seq>x.last_read AND m.sender_uid<>'cs-bert'", (cid,)).fetchone()[0]
        hid_unread = c.execute("SELECT COUNT(*) FROM chat_msg m JOIN chat_member x ON x.chat_id=m.chat_id AND x.uid='cs-bert' JOIN chat_hidden h "
                               "ON h.msg_seq=m.seq AND h.uid='cs-bert' WHERE m.chat_id=? AND m.seq>x.last_read", (cid,)).fetchone()[0]
    ok(f"Ungelesen zaehlt ausgeblendete nicht ({ub} von {raw_unread})", hid_unread >= 1 and ub == raw_unread - hid_unread, (ub, raw_unread, hid_unread))
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [mn["id"]]})
    ok("Fuer alle geloescht -> Ausblendung geht mit", s == 200 and hidden_rows("cs-bert", cid) == 1)
    s, d = B.api("DELETE", f"/api/chats/{cid}?scope=me")
    ok("'Fuer mich loeschen' (Chat) raeumt die Ausblendungen auf", s == 200 and hidden_rows("cs-bert", cid) == 0, (s, d))

    # Gruppe: Austritt, Chat geloescht
    fg = A.api("POST", "/api/chats", {"kind": "free", "members": ["cs-bert", "cs-cora"]})[1]["chat"]
    _, _, g1 = A.send(fg["id"], "Gruppe 1")
    Cn.api("POST", f"/api/chats/{fg['id']}/hide", {"ids": [g1["id"]]})
    B.api("POST", f"/api/chats/{fg['id']}/hide", {"ids": [g1["id"]]})
    n0 = hidden_rows("cs-cora", fg["id"])
    s, _ = Cn.api("DELETE", f"/api/chats/{fg['id']}/members/cs-cora")
    ok("Austritt aus der Gruppe raeumt auf", n0 == 1 and s == 200 and hidden_rows("cs-cora", fg["id"]) == 0, (n0, s))
    s, _ = A.api("DELETE", f"/api/chats/{fg['id']}?scope=all")
    ok("Chat fuer alle geloescht -> Ausblendungen weg", s == 200 and hidden_rows("cs-bert", fg["id"]) == 0)

    # --- x3_why ----------------------------------------------------------------------------------------------------
    d = A.chat(cid)
    ok("x3_why 'setting' (Stufe 3 aus)", d.get("x3_ok") is False and d.get("x3_why") == "setting" and "x3_wait" not in d, d.get("x3_why"))
    de = A.api("POST", "/api/chats", {"kind": "dm", "user": "cs-emil"})[1]["chat"]
    A.send(de["id"], "Hallo Emil")
    with connect() as c, c:
        c.execute("UPDATE chat_device SET name='Emils Altgeraet' WHERE uid='cs-emil'")
    old = C.device()
    A.api("POST", "/api/chat/devices", C.device_api(A.uid, A.ident, old, name="Altes Tablet"))
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=?, caps='[]' WHERE id=?", (time.time(), old["id"]))
    since = time.time() - 86400
    auth.save_setting("chat_v3_since", since)
    setv3(True, days=90)
    d = A.chat(de["id"])
    w = d.get("x3_wait") or {}
    ok("x3_why 'devices': eigene Geraete mit Namen, andere nur als Anzahl, Stichtag", d.get("x3_ok") is False and d.get("x3_why") == "devices"
       and w.get("mine") == ["Altes Tablet"] and w.get("others") == 1 and abs(w.get("until", 0) - (since + 90 * 86400)) < 2, (d.get("x3_why"), w))
    ok("Keine fremden Geraetenamen im Chat-Objekt", "Emils Altgeraet" not in json.dumps(d) and "Emils Altgeraet" not in json.dumps(A.seen()))
    w2 = E.chat(de["id"], lv=2).get("x3_wait") or {}
    ok("Emil sieht sein eigenes Geraet, Anna nur als Anzahl", w2.get("mine") == ["Emils Altgeraet"] and w2.get("others") == 1
       and "Altes Tablet" not in json.dumps(E.chat(de["id"], lv=2)), w2)
    in_list = next((x for x in A.seen()["chats"] if x["id"] == de["id"]), {})
    ok("x3_why auch in der Chat-Liste", in_list.get("x3_why") == "devices", in_list.get("x3_why"))
    d = A.chat(cid)
    ok("Eigenes altes Geraet haelt auch den Chat mit Bert auf (others 0)",
       d.get("x3_why") == "devices" and (d.get("x3_wait") or {}).get("mine") == ["Altes Tablet"] and d["x3_wait"]["others"] == 0, d.get("x3_wait"))
    web = C.device()                                     # Browser-Geraet ohne chat3: laedt beim Oeffnen den aktuellen Client
    A.api("POST", "/api/chat/devices", C.device_api(A.uid, A.ident, web, name="Safari · iOS"))
    with connect() as c, c:
        c.execute("UPDATE chat_device SET last_seen=?, caps='[]' WHERE id=?", (time.time(), web["id"]))
    d = A.chat(cid)
    ok("Browser-Geraet haelt die Umstellung nicht auf (nur App-Geraete)", (d.get("x3_wait") or {}).get("mine") == ["Altes Tablet"], d.get("x3_wait"))
    with connect() as c:
        ok("convert_check: Browser-Geraet allein blockiert nicht",
           CH.blocking_devices(c, [A.uid], time.time()) == [(A.uid, "Altes Tablet")], CH.blocking_devices(c, [A.uid], time.time()))
    A.api("DELETE", f"/api/chat/devices/{old['id']}")
    d = A.chat(cid)
    ok("Nichts wartet mehr -> 'devices' ohne Geraete (Umstellung beim naechsten Lauf; 'pending' nur fuer Anfragen)", d.get("x3_why") == "devices"
       and d.get("x3_wait", {}).get("mine") == [] and d["x3_wait"].get("others") == 0, (d.get("x3_why"), d.get("x3_wait")))
    setv3(True, days=0)
    w3 = A.chat(de["id"]).get("x3_wait") or {}
    ok("Stichtag erreicht -> 'devices' ohne Geraete (wird beim naechsten Lauf umgestellt)", A.chat(de["id"]).get("x3_why") == "devices"
       and w3.get("mine") == [] and w3.get("others") == 0, w3)
    setv3(True, days=90)
    CH.convert_v3(limit=100000)
    d = A.chat(cid)
    ok("Nach der Umstellung: x3_ok ohne x3_why", U.v_of(cid)["v"] == 3 and d.get("x3_ok") is True and "x3_why" not in d, (U.v_of(cid), d.get("x3_why")))

    # --- Stufe 3: Bulk-Loeschen ueber FED.delete_msg ------------------------------------------------------------------
    ch = A.chat(cid)
    s, r1, m1 = A.send_v2(ch, "v2 eins")
    s, r2, m2 = A.send_v2(ch, "v2 zwei")
    s, r3, m3 = B.send_v2(B.chat(cid), "v2 von Bert")
    mk = sb.mark()
    s, d = A.api("POST", f"/api/chats/{cid}/messages/delete", {"ids": [m1["id"], m2["id"], m3["id"]]})
    ok("Stufe 3: eigene geloescht, fremde not_own", s == 200 and d.get("deleted") == [m1["id"], m2["id"]] and d.get("failed") == {m3["id"]: "not_own"}, (s, d))
    ok("Stufe 3: Live chat.deleted", all(sb.wait("chat.deleted", lambda e, i=i: e.get("id") == i, mk) for i in (m1["id"], m2["id"])))
    bl = ids_of(B.msgs(cid))
    ok("Stufe 3: bei Bert weg", m1["id"] not in bl and m3["id"] in bl, bl)
    s, d = B.api("POST", f"/api/chats/{cid}/hide", {"ids": [m3["id"]]})
    ok("Stufe 3: ausblenden wirkt", s == 200 and d == {"hidden": 1} and m3["id"] not in ids_of(B.msgs(cid)) and m3["id"] in ids_of(A.msgs(cid)))

    # --- Format v2: Gruende fuer x3_ok false (direkt, ohne zweiten Server) ---------------------------------------------
    peer = {"id": 99901, "host": "peer-a.example", "status": "active"}
    orig = (FC.participants, F.feature, F.peer_by)
    try:
        FC.participants = lambda c, ch, declined=False: [peer]
        F.feature = lambda p, name: name != "chat3"
        F.peer_by = lambda c, k, v: peer
        with connect() as c:
            fch = {"id": 1, "v": 2, "home_peer": None, "fstate": None, "ref": "x@y", "kind": "dm"}
            r_srv = X3._why_v2(c, fch, {"state": "ok"})
            r_home = X3._why_v2(c, dict(fch, home_peer=99901), {"state": "ok"})
            r_req = X3._why_v2(c, fch, {"state": "request"})
            r_ro = X3._why_v2(c, fch, {"state": "ok", "readonly": True})
            dd = {"state": "ok"}
            X3.chat_flags(c, fch, dd)
    finally:
        FC.participants, F.feature, F.peer_by = orig
    ok("Format v2: 'server' mit x3_hosts (Teilnehmer bzw. Heim)", r_srv == {"x3_why": "server", "x3_hosts": ["peer-a.example"]}
       and r_home == {"x3_why": "server", "x3_hosts": ["peer-a.example"]}, (r_srv, r_home))
    ok("Format v2: 'pending' (Anfrage) und 'readonly'", r_req == {"x3_why": "pending"} and r_ro == {"x3_why": "readonly"}, (r_req, r_ro))
    ok("chat_flags setzt x3_ok false + x3_why", dd.get("x3_ok") is False and dd.get("x3_why") == "server", dd)

    # --- Kontoloeschung -------------------------------------------------------------------------------------------------
    ok("Vor der Kontoloeschung: Ausblendungen von Bert da", hidden_rows("cs-bert") >= 1)
    from saga.userdata import delete_user_data
    delete_user_data("cs-bert")
    ok("Kontoloeschung raeumt chat_hidden auf", hidden_rows("cs-bert") == 0)


if __name__ == "__main__":
    main()
