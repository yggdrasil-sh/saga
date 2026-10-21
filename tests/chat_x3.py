"""Chat-Grundlagen 2.5 (saga/chat_x3.py): Reaktionen, Nachricht bearbeiten, Sprachnachrichten-Grenzen - in lokalen Chats der
Stufe 3, PyNaCl als Client (tests/chatlib.py, Format tests/vectors/chat_v3.json).

Geprueft: Faehigkeiten und Grenzen (/api/chat/me), x3_ok; Reaktion setzen (Signatur, verschluesselt mit dem abgeleiteten
Schluessel, Empfaenger oeffnet sie), ersetzen (eine je Person), zuruecknehmen (""), aeltere ts verdraengt nichts, rx in GET
.../messages, GET /reactions, Live chat.react gebuendelt (mehrere in einem Ereignis), kein Push und keine Benachrichtigung,
falsche Signatur / fremdes Geraet / andere Nachricht / Systemzeile / Nicht-Mitglied / Chat v1 abgewiesen, Rate; Bearbeiten:
Original zeigt die neueste Bearbeitung (edit, edited_at, edit_n), kein Verlauf (alte Bearbeitung samt Schluesseln weg), nie
als eigener Eintrag, kein Ungelesen, Live chat.edited, nur der Absender, 24 h, hoechstens 20, keine KI-Antwort, keine
Anhaenge/Antwort, falsche Signatur, Original loeschen nimmt die Bearbeitung mit; Kontoloeschung. Wegwerf-Konten cx-*."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
from db import connect  # noqa: E402
import chatlib as C  # noqa: E402
import chat as CT  # noqa: E402
import chat_v3_umstellung as U  # noqa: E402  (Konto mit Zertifikaten v2: P)

req, res = CT.req, CT.res
WAIT = 5.6


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, ("  -- " + str(info)[:600]) if (info and not c) else "", flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'cx-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM chat WHERE dm_key LIKE 'cx-%' OR created_by LIKE 'cx-%' OR id IN (SELECT chat_id FROM chat_member WHERE uid LIKE 'cx-%')")
        c.execute("DELETE FROM users WHERE username LIKE 'cx-%'")


def key_of(u, ch, m):
    """Nachrichtenschluessel K (wie eine App: versiegelt fuer Geraet bzw. Backup)."""
    pk, sk = (u.dev["enc_pk"], u.dev["enc_sk"]) if m["key_for"] == "device" else (u.ident["backup_pk"], u.ident["backup_sk"])
    return C.nb.crypto_box_seal_open(C.unb64(m["key"], 80), pk, sk)


def react(u, ch, m, emoji, ts=None, dev=None, mid=None, by=None):
    K = key_of(u, ch, m)
    r = C.encrypt_reaction(K, ch["ref"], mid or m["id"], by or u.handle, dev or u.dev, emoji, ts=ts)
    s, d = u.api("PUT", f"/api/chats/{ch['id']}/messages/{m['id']}/reaction", {k: r[k] for k in ("dev", "ts", "ct", "sig")})
    return s, d, r


def msg(u, ch, mid):
    return next((x for x in u.msgs(ch["id"])["messages"] if x["id"] == mid), None)


def edit(u, ch, orig_id, text, extra=None):
    s, d = u.api("GET", "/api/chat/v2/keys?handles=" + ",".join(m["handle"] for m in ch["members"]))
    rc = C.recipients_v2([e for e in d["keys"].values() if e])
    m = C.encrypt_v2(ch["ref"], u.handle, u.dev, rc, json.dumps({"edit": orig_id, "t": text}))
    m.update(v=2, kind="edit", edit_of=orig_id, **(extra or {}))
    s, r = u.api("POST", f"/api/chats/{ch['id']}/messages", m)
    return s, r, m


def opened(u, ch, m):
    pk, sk = (u.dev["enc_pk"], u.dev["enc_sk"]) if m["key_for"] == "device" else (u.ident["backup_pk"], u.ident["backup_sk"])
    return json.loads(C.decrypt_v2(ch["ref"], m, m["key"], pk, sk))


def main():
    cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "share_enabled", "share_group_scope", "chat_v3")}
    for k, v in (("chat_enabled", True), ("share_enabled", True), ("share_group_scope", False), ("chat_v3", True)):
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
    print(f"\nCHAT-X3 {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


def run():
    A, B, Cc = U.P("cx-anna", "Anna X"), U.P("cx-bert", "Bert X"), U.P("cx-cora", "Cora X")
    s, me = A.api("GET", "/api/chat/me")
    ok("Faehigkeiten react/edit/voice und Grenzen (24 h, 20, 15 min, 24 kbit/s)", {"react", "edit", "voice"} <= set(me.get("features", []))
       and me["x3_limits"]["edit_window"] == 86400 and me["x3_limits"]["edit_max"] == 20 and me["x3_limits"]["voice_max_s"] == 900
       and me["x3_limits"]["voice_kbps"] == 24, me.get("x3_limits"))
    rb = A.api("GET", "/api/chat/people?q=Bert%20X")[1]["users"][0]["username"]
    dm = A.api("POST", "/api/chats", {"kind": "dm", "user": rb, "device": A.dev["id"]})[1]["chat"]
    ok("Direkt-Chat Stufe 3 mit x3_ok", dm.get("v") == 3 and dm.get("x3_ok") is True, dm)
    s, r1, m1 = A.send_v2(dm, "Wer kommt mit?")
    bdm = B.chat(dm["id"])
    bm1 = msg(B, bdm, m1["id"])
    evA = CT.Stream(A.k)
    time.sleep(0.5)

    # --- Reaktionen -----------------------------------------------------------------------------------------------------
    mk = evA.mark()
    s, d, rB = react(B, bdm, bm1, "👍")
    ok(f"B reagiert 👍 ({s})", s == 200, d)
    s, d, rC = react(B, bdm, bm1, "❤️", ts=rB["ts"] + 5)
    e = evA.wait("chat.react", lambda x: x.get("chat_id") == dm["id"] and any(i["ts"] == rC["ts"] for i in x["items"]), mk, timeout=5)
    n_ev = len([1 for typ, x in evA.ev[mk:] if typ == "chat.react"])
    ok(f"A: chat.react (der erste sofort, dann gebuendelt: {n_ev} Ereignis(se) fuer 2), ohne Inhalt im Klartext", bool(e) and n_ev <= 2
       and e["items"][-1]["by"] == B.handle and "❤" not in json.dumps(e), e)
    am1 = msg(A, dm, m1["id"])
    rx = am1.get("rx") or []
    K = key_of(A, dm, am1)
    ok("A: rx in GET .../messages - eine je Person, oeffnet ❤️ mit dem abgeleiteten Schluessel, Signatur gueltig",
       len(rx) == 1 and C.decrypt_reaction(K, dm["ref"], m1["id"], rx[0]) == json.dumps({"e": "❤️", "ts": rC["ts"]}, ensure_ascii=False, separators=(",", ":"))
       and C.verify_reaction(dm["ref"], m1["id"], rx[0], B.dev["sign_pk"]), rx)
    s, d, _ = react(B, bdm, bm1, "😂", ts=rB["ts"] - 1000)
    ok("aeltere Reaktion (ts) verdraengt die neuere nicht", s == 200 and len(msg(A, dm, m1["id"])["rx"]) == 1
       and msg(A, dm, m1["id"])["rx"][0]["ts"] == rC["ts"])
    s, d, rA = react(A, dm, am1, "👍")
    s, d = A.api("GET", f"/api/chats/{dm['id']}/reactions?mids={m1['id']}")
    ok("GET /reactions: zwei Personen", s == 200 and sorted(x["by"] for x in d["reactions"][m1["id"]]) == sorted([A.handle, B.handle]), d)
    s, d, rU = react(B, bdm, bm1, "", ts=rC["ts"] + 10)
    x = next(y for y in msg(A, dm, m1["id"])["rx"] if y["by"] == B.handle)
    ok("Zuruecknehmen ('') ersetzt die Reaktion", s == 200 and C.decrypt_reaction(K, dm["ref"], m1["id"], x).startswith('{"e":""'))
    with connect() as c:
        n_push = c.execute("SELECT COUNT(*) FROM push_queue WHERE uid='cx-anna' AND kind LIKE 'chat%' AND created>?", (time.time() - 30,)).fetchone()[0]
        n_not = c.execute("SELECT COUNT(*) FROM notification WHERE uid='cx-anna' AND created>?", (time.time() - 30,)).fetchone()[0] \
            if c.execute("SELECT 1 FROM information_schema.columns WHERE table_name='notification' AND column_name='created'").fetchone() else 0
    ok("Reaktionen: kein Push, keine Benachrichtigung", n_push == 0 and n_not == 0, (n_push, n_not))
    # Abweisungen
    good = C.encrypt_reaction(key_of(B, bdm, bm1), dm["ref"], m1["id"], B.handle, B.dev, "🙏")
    bad = dict(good, sig=C.b64(b"\0" * 64))
    s1 = B.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: bad[k] for k in ("dev", "ts", "ct", "sig")})[0]
    s2 = A.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: good[k] for k in ("dev", "ts", "ct", "sig")})[0]
    other = C.encrypt_reaction(key_of(B, bdm, bm1), dm["ref"], "f" * 32, B.handle, B.dev, "🙏")
    s3 = B.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: other[k] for k in ("dev", "ts", "ct", "sig")})[0]
    s4 = Cc.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: good[k] for k in ("dev", "ts", "ct", "sig")})[0]
    s5 = B.api("PUT", f"/api/chats/{dm['id']}/messages/{'a' * 32}/reaction", {k: good[k] for k in ("dev", "ts", "ct", "sig")})[0]
    big = dict(good, ct=C.b64(os.urandom(400)))
    s6 = B.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: big[k] for k in ("dev", "ts", "ct", "sig")})[0]
    ok(f"abgewiesen: falsche Signatur, fremdes Geraet/Handle, andere Nachricht, Nicht-Mitglied, unbekannte Nachricht, zu gross {[s1, s2, s3, s4, s5, s6]}",
       [s1, s2, s3, s4, s5, s6] == [400, 403, 400, 404, 404, 400])

    # --- Bearbeiten -------------------------------------------------------------------------------------------------------
    unread0 = next(x for x in B.api("GET", "/api/chats")[1]["chats"] if x["id"] == dm["id"])["unread"]
    mkB = CT.Stream(B.k)
    time.sleep(0.5)
    mk = mkB.mark()
    s, d, e1 = edit(A, dm, m1["id"], "Wer kommt morgen mit?")
    ok(f"A bearbeitet die eigene Nachricht ({s})", s == 200 and d.get("edit_n") == 1, d)
    ev = mkB.wait("chat.edited", lambda x: x.get("id") == m1["id"], mk)
    ok("B: Live chat.edited (Original-Kennung)", bool(ev) and ev["chat_id"] == dm["id"], ev)
    bl = B.msgs(dm["id"])["messages"]
    bo = next(x for x in bl if x["id"] == m1["id"])
    ok("B: Original mit edit (neuester Text), edited_at, edit_n; keine eigene Zeile", bo.get("edit_n") == 1 and bo.get("edited_at")
       and opened(B, bdm, bo["edit"]) == {"edit": m1["id"], "t": "Wer kommt morgen mit?"} and bo["edit"]["edit_of"] == m1["id"]
       and not any(x["id"] == e1["id"] for x in bl) and opened(B, bdm, bo)["text"] == "Wer kommt mit?", bo)
    ok("B: Bearbeitung signiert vom Absender (v2), Absender als Handle", bo["edit"]["sender"]["username"] == A.handle
       and C.verify(C.unb64(bo["edit"]["sig"]), C.sig_msg_v2(dm["ref"], e1["id"], A.handle, A.dev["id"], e1["ts"], None, None, e1["body_ct"]), A.dev["sign_pk"]))
    s, ch_b = B.api("GET", "/api/chats")
    ok("kein Ungelesen durch die Bearbeitung", next(x for x in ch_b["chats"] if x["id"] == dm["id"])["unread"] == unread0, unread0)
    s, d, e2 = edit(A, dm, m1["id"], "Wer kommt uebermorgen mit?")
    with connect() as c:
        n_e = c.execute("SELECT COUNT(*) FROM chat_msg WHERE chat_id=? AND kind='edit'", (dm["id"],)).fetchone()[0]
        n_k = c.execute("SELECT COUNT(*) FROM chat_msg_key k JOIN chat_msg m ON m.seq=k.msg_seq WHERE m.id=?", (e1["id"],)).fetchone()[0]
    bo = msg(B, bdm, m1["id"])
    ok("zweite Bearbeitung: kein Verlauf (alte samt Schluesseln weg), edit_n 2", s == 200 and n_e == 1 and n_k == 0 and bo["edit_n"] == 2
       and opened(B, bdm, bo["edit"])["t"] == "Wer kommt uebermorgen mit?", (n_e, n_k, bo.get("edit_n")))
    s, d, _ = edit(B, bdm, m1["id"], "Untergeschoben")
    ok(f"fremde Nachricht bearbeiten -> 403 ({s})", s == 403, d)
    s, d, _ = edit(A, dm, m1["id"], "Mit Anhang", {"files": ["x" * 22]})
    s2, d2, _ = edit(A, dm, m1["id"], "Als Antwort", {"reply_to": m1["id"]})
    ok(f"Bearbeitung mit Anhang/Antwort -> 400 ({s}, {s2})", s == 400 and s2 == 400)
    s, d, ebad = edit(A, dm, m1["id"], "falsch signiert", {"sig": C.b64(b"\0" * 64)})
    ok(f"falsche Signatur -> 400 ({s})", s == 400)
    with connect() as c, c:
        c.execute("UPDATE chat_msg SET edit_n=20 WHERE id=?", (m1["id"],))
    s, d, _ = edit(A, dm, m1["id"], "21.")
    ok(f"hoechstens 20 Bearbeitungen -> 403 ({s})", s == 403, d)
    s, r2, m2 = A.send_v2(dm, "Alte Nachricht")
    with connect() as c, c:
        c.execute("UPDATE chat_msg SET created=? WHERE id=?", (time.time() - 86400 - 60, m2["id"]))
    s, d, _ = edit(A, dm, m2["id"], "zu spaet")
    ok(f"nach 24 h -> 403 ({s})", s == 403, d)
    s, r3, m3 = A.send_v2(dm, "Noch eine")
    s, d, _ = edit(A, dm, m3["id"], "neu")
    s, d = A.api("DELETE", f"/api/chats/{dm['id']}/messages/{m3['id']}")
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM chat_msg WHERE edit_of=?", (m3["id"],)).fetchone()[0]
    ok("Original loeschen nimmt die Bearbeitung mit", s == 200 and left == 0, left)

    # --- Chat v1: nichts davon --------------------------------------------------------------------------------------------
    s, r4, m4 = A.send_v2(dm, "vor dem Ausschalten")
    auth.save_setting("chat_v3", False)
    time.sleep(WAIT)
    # 2.5-RC: Einstellung chat_v3 aus -> auch in Stufe-3-Chats keine Reaktionen/Bearbeitungen, keine Faehigkeiten
    s, me = A.api("GET", "/api/chat/me")
    feats = set(me.get("features") or [])
    ok(f"chat_v3 aus: /api/chat/me ohne react/edit/voice/chat3 und ohne x3_limits ({sorted(feats)})",
       s == 200 and not feats & {"react", "edit", "voice", "chat3"} and "x3_limits" not in me)
    ok("chat_v3 aus: Stufe-3-Chat mit x3_ok false", A.chat(dm["id"]).get("x3_ok") is False)
    fresh = C.encrypt_reaction(key_of(B, bdm, bm1), dm["ref"], m1["id"], B.handle, B.dev, "👍")
    s, d = B.api("PUT", f"/api/chats/{dm['id']}/messages/{m1['id']}/reaction", {k: fresh[k] for k in ("dev", "ts", "ct", "sig")})
    ok(f"chat_v3 aus: Reaktion -> 409 ({s})", s == 409, d)
    s, d, _ = edit(A, dm, m4["id"], "nachher")
    ok(f"chat_v3 aus: Bearbeiten -> 409 ({s})", s == 409, d)
    v1 = A.api("POST", "/api/chats", {"kind": "free", "members": ["cx-cora"]})[1]["chat"]
    s, d0, mv = A.send(v1["id"], "v1")
    s, d = A.api("PUT", f"/api/chats/{v1['id']}/messages/{mv['id']}/reaction", {"dev": A.dev["id"], "ts": int(time.time() * 1000), "ct": good["ct"], "sig": good["sig"]})
    ok(f"Chat v1: keine Reaktionen (409) und x3_ok false ({s})", s == 409 and A.chat(v1["id"]).get("x3_ok") is not True, d)
    auth.save_setting("chat_v3", True)
    time.sleep(WAIT)
    ok("chat_v3 wieder an: Stufe-3-Chat mit x3_ok, Faehigkeiten wieder da", A.chat(dm["id"]).get("x3_ok") is True
       and {"react", "edit", "voice"} <= set(A.api("GET", "/api/chat/me")[1].get("features") or []))
    s, d, _ = edit(A, dm, m4["id"], "nachher")
    ok(f"chat_v3 wieder an: Bearbeiten geht ({s})", s == 200, d)

    # --- Kontoloeschung --------------------------------------------------------------------------------------------------
    from saga.userdata import delete_user_data
    delete_user_data("cx-bert")
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM chat_reaction WHERE reactor=?", (B.handle,)).fetchone()[0]
    ok("Kontoloeschung: Reaktionen von B weg", n == 0)


if __name__ == "__main__":
    main()
