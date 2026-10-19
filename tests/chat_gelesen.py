"""Gelesen-Bestaetigungen und Online-Status (2.4, saga/chat_status.py) - Server-Seite, PyNaCl als Client (tests/chatlib.py).

Geprueft: Einstellungen (Standard an, getrennt, gegenseitig), Bestaetigung im Direkt-Chat (versiegelt fuer den Backup-
Schluessel, vom Geraet signiert, Live-Ereignis chat.receipt, GET /receipts), "aus" heisst gegenseitig nichts (senden ->
'off', sehen -> enabled false, eigene Bestaetigungen zurueckgezogen), Gruppen ("gelesen von" je Mitglied), Buendelung
(hoechstens ein Ereignis je Chat und 2 s), Grenzen (Form, Groesse, fremde Empfaenger, Rate), Server sieht keinen Zeiger,
Online-Status (nur Chat-Partner, online/offline aus der Live-Verbindung, "zuletzt online" im 5-Minuten-Raster, Abo,
gegenseitig aus, Live-Ereignis chat.presence), Ausgetretene, Kontoloeschung. Wegwerf-Konten cg-*, Gruppe cg-team."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402  (tests/chat.py: req, ck, Person, Stream)

req, ck, ok, res = CT.req, CT.ck, CT.ok, CT.res
PW = "Gelesen-Pass-1234"
GRACE = int(os.environ.get("CHAT_OFFLINE_GRACE", "30"))


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cg-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE group_name LIKE 'cg-%' OR dm_key LIKE 'cg-%' OR created_by LIKE 'cg-%' "
                  "OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cg-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'cg-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'cg-%'")


class P(CT.Person):
    def rcpt(self, cid, upto, n, others, chat=None, extra=None, body=None):
        """Bestaetigung "gelesen bis upto (Position n)" fuer die Backup-Schluessel von others -> {'backup:<u>': versiegelt}"""
        rc = C.recipients_of(req("GET", f"/api/chats/{cid}/recipients", self.k)[1]["recipients"])
        b = body or {"id": upto, "n": n, "ts": int(time.time() * 1000)}
        keys = {f"backup:{u}": C.seal_receipt(str(chat or cid), self.uid, self.dev, b, rc[f"backup:{u}"]) for u in others}
        keys.update(extra or {})
        return keys

    def read_rc(self, cid, seq, keys):
        return req("POST", f"/api/chats/{cid}/read", self.k, {"seq": seq, "receipt": {"keys": keys}})

    def receipts(self, cid):
        return req("GET", f"/api/chats/{cid}/receipts", self.k)

    def open_rc(self, sealed):
        return C.open_receipt(sealed, self.ident["backup_pk"], self.ident["backup_sk"])

    def privacy(self, **kw):
        return req("PUT", "/api/chat/privacy", self.k, kw)


def sign_pk_of(viewer, cid, uid, did):
    rc = req("GET", f"/api/chats/{cid}/recipients", viewer.k)[1]["recipients"]
    p = next(r for r in rc if r["username"] == uid)
    d = next(x for x in p["devices"] if x["id"] == did)
    return C.unb64(d["sign_pub"], 32)


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False)):
        auth.save_setting(k, v)
    for u, n in (("cg-a", "Anna Gelesen"), ("cg-b", "Bert Gelesen"), ("cg-c", "Cora Gelesen"), ("cg-d", "Dora Gelesen")):
        auth.create_user(u, PW, n)
    with connect() as c, c:
        auth.ensure_group(c, "cg-team")
    for u in ("cg-a", "cg-b", "cg-c"):
        auth.set_user_groups(auth.get_user(u)["id"], ["cg-team"])
    try:
        run()
    finally:
        for s in CT.STREAMS:
            s.close()
        for k, v in keep.items():
            auth.save_setting(k, v)
        cleanup()
    print(f"\nGELESEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    A, B, Cc, D = P("cg-a"), P("cg-b"), P("cg-c"), P("cg-d")
    # --- Einstellungen ---------------------------------------------------------------------------------------------
    s, d = req("GET", "/api/chat/privacy", A.k)
    ok(f"Einstellungen: Standard an ({d})", s == 200 and d == {"receipts": True, "online": True})
    s, me = req("GET", "/api/chat/me", A.k)
    ok("GET /api/chat/me: privacy + features", me.get("privacy") == {"receipts": True, "online": True} and "receipts" in me.get("features", [])
       and "online" in me["features"] and me["status_limits"]["presence_ttl"] == 90)
    ok("PUT /api/chat/privacy: ungueltig -> 400", req("PUT", "/api/chat/privacy", A.k, {"receipts": "nein"})[0] == 400
       and req("PUT", "/api/chat/privacy", A.k, {})[0] == 400)
    ok("ohne Anmeldung -> 401", req("GET", "/api/chat/privacy")[0] == 401 and req("POST", "/api/chat/presence", None, {"users": []})[0] == 401)

    # --- Direkt-Chat ---------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "dm", "user": "cg-b"})
    cid = d["chat"]["id"]
    s, d, m1 = A.send(cid, "Hallo Bert")
    seq1 = d["seq"]
    evA = CT.Stream(A.k)
    time.sleep(0.6)
    mk = evA.mark()
    keys = B.rcpt(cid, m1["id"], seq1, ["cg-a"])
    s, d = B.read_rc(cid, seq1, keys)
    ok(f"B: gelesen + Bestaetigung ({s}, {d.get('receipt')})", s == 200 and d.get("receipt") == "ok" and d.get("last_read") == seq1)
    e = evA.wait("chat.receipt", lambda x: x.get("chat_id") == cid, mk)
    ok("A: Live-Ereignis chat.receipt (versiegelt, ohne Zeiger)", bool(e) and len(e["receipts"]) == 1 and e["receipts"][0]["from"] == "cg-b"
       and set(e["receipts"][0]) == {"from", "sealed"})
    dev, body, sig = A.open_rc(e["receipts"][0]["sealed"])
    spk = sign_pk_of(A, cid, "cg-b", dev)
    ok("A: oeffnet die Bestaetigung, Signatur des Geraets von B gueltig, gelesen bis m1",
       dev == B.dev["id"] and C.verify_receipt(str(cid), "cg-b", dev, body, sig, spk) and json.loads(body)["id"] == m1["id"]
       and json.loads(body)["n"] == seq1)
    ok("A: Signatur gilt nicht fuer anderen Chat/Leser (gebunden)", not C.verify_receipt(str(cid + 1), "cg-b", dev, body, sig, spk)
       and not C.verify_receipt(str(cid), "cg-a", dev, body, sig, spk))
    s, d = A.receipts(cid)
    ok("A: GET /receipts liefert die neueste", s == 200 and d["enabled"] and [x["from"] for x in d["receipts"]] == ["cg-b"])
    s, d = B.receipts(cid)
    ok("B: sieht keine eigene Bestaetigung (A hat noch keine geschickt)", s == 200 and d["receipts"] == [])
    with connect() as c:
        rows = c.execute("SELECT reader, viewer, sealed FROM chat_receipt WHERE chat_id=?", (cid,)).fetchall()
    ok("Server: nur versiegelt gespeichert (keine Nachrichten-Kennung, keine Position im Klartext)",
       len(rows) == 1 and m1["id"] not in rows[0][2] and '"n"' not in rows[0][2])
    ok("Server: Nachrichten-Kennung nirgends in chat_receipt", all(h[0] != "chat_receipt" for h in CT.plaintext_hits(m1["id"])))
    ok("D (kein Mitglied): GET /receipts -> 404", D.receipts(cid)[0] == 404)
    # Neueste gewinnt (eine Zeile je Leser und Konto)
    s, d, m2 = A.send(cid, "Noch was")
    time.sleep(2.1)
    mk = evA.mark()
    B.read_rc(cid, d["seq"], B.rcpt(cid, m2["id"], d["seq"], ["cg-a"]))
    e = evA.wait("chat.receipt", lambda x: x.get("chat_id") == cid, mk)
    ok("A: neue Bestaetigung ersetzt die alte", bool(e) and json.loads(A.open_rc(e["receipts"][0]["sealed"])[1])["id"] == m2["id"]
       and len(A.receipts(cid)[1]["receipts"]) == 1)

    # --- Grenzen -------------------------------------------------------------------------------------------------
    ok("Form: keys fehlt/leer/kein backup: -> bad", B.read_rc(cid, 0, {})[1].get("receipt") == "bad"
       and req("POST", f"/api/chats/{cid}/read", B.k, {"seq": 0, "receipt": "x"})[1].get("receipt") == "bad"
       and B.read_rc(cid, 0, {"cg-a": keys["backup:cg-a"]})[1].get("receipt") == "bad")
    ok("Groesse: zu kurz/zu lang/kein Base64 -> bad", B.read_rc(cid, 0, {"backup:cg-a": "QUFB"})[1].get("receipt") == "bad"
       and B.read_rc(cid, 0, {"backup:cg-a": "A" * 3000})[1].get("receipt") == "bad"
       and B.read_rc(cid, 0, {"backup:cg-a": "!" * 200})[1].get("receipt") == "bad")
    ok("Nur fremde Empfaenger (kein Mitglied, ich selbst) -> none, nichts gespeichert",
       B.read_rc(cid, 0, {"backup:cg-d": keys["backup:cg-a"], "backup:cg-b": keys["backup:cg-a"]})[1].get("receipt") == "none")
    with connect() as c:
        ok("... keine Zeile fuer cg-d/cg-b", not c.execute("SELECT 1 FROM chat_receipt WHERE viewer IN ('cg-d','cg-b')").fetchone())
    ok("Ohne receipt: /read wie bisher (kein Feld receipt)", "receipt" not in req("POST", f"/api/chats/{cid}/read", B.k, {"seq": 0})[1])
    ok("D: Bestaetigung in fremdem Chat -> 404", D.read_rc(cid, 0, keys)[0] == 404)

    # --- Gegenseitig: Leser schaltet ab --------------------------------------------------------------------------
    evB = CT.Stream(B.k)
    time.sleep(0.5)
    mk = evB.mark()
    s, d = B.privacy(receipts=False)
    ok(f"B: Gelesen-Bestaetigungen aus ({d})", s == 200 and d == {"receipts": False, "online": True})
    ok("B: andere Tabs erfahren es (chat.privacy)", bool(evB.wait("chat.privacy", lambda x: x.get("receipts") is False, mk)))
    ok("B aus: eigene Bestaetigungen zurueckgezogen (A sieht keine mehr)", A.receipts(cid)[1]["receipts"] == [])
    ok("B aus: senden -> off", B.read_rc(cid, 0, B.rcpt(cid, m2["id"], 1, ["cg-a"]))[1].get("receipt") == "off")
    s, d, m3 = B.send(cid, "Antwort von Bert")
    A.read_rc(cid, d["seq"], A.rcpt(cid, m3["id"], d["seq"], ["cg-b"]))
    s, d = B.receipts(cid)
    ok("B aus: sieht auch die Bestaetigung von A nicht (gegenseitig)", s == 200 and d["enabled"] is False and d["receipts"] == [])
    with connect() as c:
        ok("B aus: fuer B wird nichts gespeichert", not c.execute("SELECT 1 FROM chat_receipt WHERE viewer='cg-b'").fetchone())
    mk = evB.mark()
    time.sleep(2.2)
    A.read_rc(cid, 0, A.rcpt(cid, m3["id"], 1, ["cg-b"]))
    time.sleep(2.5)
    ok("B aus: kein Live-Ereignis chat.receipt", "chat.receipt" not in evB.types(mk))
    B.privacy(receipts=True)
    time.sleep(2.1)
    A.read_rc(cid, 0, A.rcpt(cid, m3["id"], 1, ["cg-b"]))
    ok("B wieder an: sieht die neue Bestaetigung von A", CT.req("GET", f"/api/chats/{cid}/receipts", B.k)[1]["receipts"][0]["from"] == "cg-a")

    # --- Gruppe: "gelesen von" ---------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", A.k, {"kind": "group", "group": "cg-team"})
    gid = d["chat"]["id"]
    s, d, g1 = A.send(gid, "Hallo Team")
    gs = d["seq"]
    mk = evA.mark()
    B.read_rc(gid, gs, B.rcpt(gid, g1["id"], gs, ["cg-a", "cg-c"]))
    Cc.read_rc(gid, gs, Cc.rcpt(gid, g1["id"], gs, ["cg-a", "cg-b"]))
    ok("Gruppe: A bekommt Bestaetigungen von B und C (gebuendelt)",
       CT.wait_for(lambda: sorted(x["from"] for x in A.receipts(gid)[1]["receipts"]) == ["cg-b", "cg-c"], 6))
    time.sleep(2.5)
    evs = [x for t_, x in evA.ev[mk:] if t_ == "chat.receipt" and x.get("chat_id") == gid]
    got = sorted(r["from"] for x in evs for r in x["receipts"])
    ok(f"Gruppe: hoechstens zwei Ereignisse fuer zwei Leser kurz nacheinander ({len(evs)}, {got})", 1 <= len(evs) <= 2 and set(got) == {"cg-b", "cg-c"})
    s, d = Cc.receipts(gid)
    ok("Gruppe: C sieht B (nicht sich selbst)", [x["from"] for x in d["receipts"]] == ["cg-b"])
    for x in A.receipts(gid)[1]["receipts"]:
        dv, bd, sg = A.open_rc(x["sealed"])
        if not C.verify_receipt(str(gid), x["from"], dv, bd, sg, sign_pk_of(A, gid, x["from"], dv)):
            ok(f"Gruppe: Signatur von {x['from']}", False)
    # Buendelung: 6 schnelle Bestaetigungen von B -> hoechstens 3 Ereignisse in ~3 s, das letzte ist die neueste
    time.sleep(2.2)
    mk = evA.mark()
    for i in range(6):
        B.read_rc(gid, gs, B.rcpt(gid, g1["id"], gs, ["cg-a", "cg-c"], body={"id": g1["id"], "n": gs, "ts": 1000 + i}))
        time.sleep(0.4)
    time.sleep(2.5)
    evs = [x for t_, x in evA.ev[mk:] if t_ == "chat.receipt" and x.get("chat_id") == gid]
    last = json.loads(A.open_rc(evs[-1]["receipts"][0]["sealed"])[1]) if evs else {}
    ok(f"Buendelung: 6 Bestaetigungen in 2,4 s -> {len(evs)} Ereignisse (<= 3), das letzte ist die neueste", 1 <= len(evs) <= 3 and last.get("ts") == 1005)
    # Ausgetreten: Bestaetigung bleibt nicht sichtbar
    auth.set_user_groups(auth.get_user("cg-c")["id"], [])
    req("GET", f"/api/chats/{gid}?device={A.dev['id']}", A.k)                  # Abgleich der Gruppe
    ok("Gruppe: C ausgetreten -> keine Bestaetigung von C mehr", CT.wait_for(lambda: [x["from"] for x in A.receipts(gid)[1]["receipts"]] == ["cg-b"], 5))
    with connect() as c:
        ok("... und aufgeraeumt", not c.execute("SELECT 1 FROM chat_receipt WHERE chat_id=? AND reader='cg-c' AND viewer='cg-a'", (gid,)).fetchone())

    # --- Rate -------------------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chats", D.k, {"kind": "dm", "user": "cg-a"})
    did = d["chat"]["id"]
    k1 = D.rcpt(did, "0" * 32, 0, ["cg-a"])
    rs = [D.read_rc(did, 0, k1)[1].get("receipt") for _ in range(125)]
    ok(f"Rate: mehr als 120 Bestaetigungen je Minute -> rate ({rs.count('ok')} ok, {rs.count('rate')} rate)", rs.count("rate") >= 1 and rs.count("ok") <= 120)

    # --- Online-Status -------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/chat/presence", A.k, {"users": ["cg-b", "cg-c", "cg-d", "cg-nobody"]})
    ok(f"Online: B (Live verbunden) online, D (Chat) ohne Live offline, ohne gemeinsamen Chat nichts ({d.get('users')})",
       s == 200 and d["enabled"] and d["ttl"] == 90 and d["users"].get("cg-b", {}).get("online") is True
       and "cg-nobody" not in d["users"] and d["users"].get("cg-d", {}).get("online") is False)
    s, d = req("POST", "/api/chat/presence", D.k, {"users": ["cg-b"]})
    ok("Online: ohne gemeinsamen Chat sieht D den Status von B nicht", s == 200 and d["users"] == {})
    ok("Online: zu viele / ungueltig -> 400", req("POST", "/api/chat/presence", A.k, {"users": ["x"] * 51})[0] == 400
       and req("POST", "/api/chat/presence", A.k, {"users": "cg-b"})[0] == 400)
    mk = evA.mark()
    evB.close()
    t0 = time.time()
    e = evA.wait("chat.presence", lambda x: x.get("username") == "cg-b" and x.get("online") is False, mk, timeout=GRACE + 20)
    ok(f"Online: B trennt -> nach der Karenz offline gemeldet ({round(time.time() - t0)} s), zuletzt im 5-Minuten-Raster",
       bool(e) and e["last"] and e["last"] % 300 == 0 and time.time() - e["last"] < 600)
    with connect() as c:
        r = c.execute("SELECT at FROM chat_seen WHERE uid='cg-b'").fetchone()
    ok("chat_seen: nur gerundet gespeichert", bool(r) and r[0] % 300 == 0)
    mk = evA.mark()
    evB = CT.Stream(B.k)
    e = evA.wait("chat.presence", lambda x: x.get("username") == "cg-b" and x.get("online") is True, mk, timeout=8)
    ok("Online: B verbindet -> online gemeldet (Abo laeuft)", bool(e) and e["last"] is None)
    s, d = req("POST", "/api/chat/presence", A.k, {"users": ["cg-b"], "leave": True})
    mk = evA.mark()
    evB.close()
    time.sleep(GRACE + 8)
    ok("Online: Abo beendet (leave) -> kein Ereignis mehr", "chat.presence" not in evA.types(mk))
    # Gegenseitig: Person schaltet ab -> niemand sieht sie; wer abschaltet, sieht niemanden
    evB = CT.Stream(B.k)
    time.sleep(0.5)
    req("POST", "/api/chat/presence", A.k, {"users": ["cg-b"]})
    mk = evA.mark()
    s, d = B.privacy(online=False)
    ok("B: Online-Status aus", s == 200 and d["online"] is False and d["receipts"] is True)
    e = evA.wait("chat.presence", lambda x: x.get("username") == "cg-b", mk, timeout=5)
    ok("A: Abo bekommt 'kein Status' (online false, last null)", bool(e) and e["online"] is False and e["last"] is None)
    ok("A: B erscheint nicht mehr", "cg-b" not in req("POST", "/api/chat/presence", A.k, {"users": ["cg-b"]})[1]["users"])
    s, d = req("POST", "/api/chat/presence", B.k, {"users": ["cg-a"]})
    ok("B aus: sieht auch A nicht (gegenseitig)", s == 200 and d["enabled"] is False and d["users"] == {})
    B.privacy(online=True)
    ok("B wieder an: sichtbar", req("POST", "/api/chat/presence", A.k, {"users": ["cg-b"]})[1]["users"].get("cg-b", {}).get("online") is True)

    # --- Admin sieht nichts, Kontoloeschung ---------------------------------------------------------------------------
    with connect() as c:
        cols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='chat_seen'")]
    ok("chat_seen: nur uid und gerundete Zeit (kein Verlauf)", sorted(cols) == ["at", "uid"])
    from saga.userdata import delete_user_data
    evB.close()
    delete_user_data("cg-b")
    with connect() as c:
        left = (c.execute("SELECT COUNT(*) FROM chat_receipt WHERE reader='cg-b' OR viewer='cg-b'").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM chat_seen WHERE uid='cg-b'").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM user_kv WHERE uid='cg-b'").fetchone()[0])
    ok(f"Kontoloeschung: Bestaetigungen, zuletzt online, Einstellungen weg ({left})", left == (0, 0, 0))


if not hasattr(CT, "wait_for"):
    def _wait_for(fn, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if fn():
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.2)
        return False
    CT.wait_for = _wait_for


if __name__ == "__main__":
    main()
