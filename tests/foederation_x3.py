"""Foederation 2.5: Reaktionen und Bearbeiten ueber Servergrenzen (saga/chat_x3.py) mit zwei eigenen Saga-Instanzen A und B
(wie tests/foederation_chat.py; Apps = In-Process-Clients mit PyNaCl, saga-chat/v2 + v3-Ereignisse).

Geprueft: Faehigkeit chat3 (CAPS2) und x3_ok auf beiden Seiten; Reaktion vom Teilnehmer (Auftrag 'react' an das Heim) und
vom Heim (Ereignis 'chat.react'), je Person eine, Signatur von der anderen Seite pruefbar; Bearbeitung durch das Heim
(Ereignis 'chat.msg.edit') und durch den Teilnehmer (Auftrag 'edit'), kein Verlauf, fremde Nachricht wird abgewiesen.
Kompatibilitaet: FED_B_APP = Saga 2.4 (ohne chat3) -> x3_ok false, Reaktion/Bearbeiten 409, keine Serverfehler.
2.5-RC: chat3 haengt an der Einstellung chat_v3 (beide Instanzen schalten sie hier ein). Schaltet B sie ab: B meldet chat3
nicht mehr, x3_ok false, eigene Reaktionen 409, eingehende Reaktionen von A werden ignoriert (ohne Serverfehler), nach dem
Abgleich der Faehigkeiten (hello) auch bei A x3_ok false. Raeumt auf."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_chat as FT  # noqa: E402
import chatlib as C  # noqa: E402

T, F2 = FT.T, FT.F2
wait, sql, api = T.wait, T.sql, FT.api


def ok(n, c, info=""):
    T.ok(n + ("" if c or not info else "  -- " + str(info)[:400]), c)
OLD = bool(os.environ.get("FED_B_APP"))            # B ist eine aeltere Saga (2.4): kein chat3


def key_of(u, m):
    pk, sk = (u.dev["enc_pk"], u.dev["enc_sk"]) if m["key_for"] == "device" else (u.ident["backup_pk"], u.ident["backup_sk"])
    return C.nb.crypto_box_seal_open(C.unb64(m["key"], 80), pk, sk)


def find(u, ch, mid):
    return next((x for x in u.msgs(ch["id"]) if x["id"] == mid), None)


def react(u, ch, m, emoji, ts=None):
    r = C.encrypt_reaction(key_of(u, m), ch["ref"], m["id"], u.handle, u.dev, emoji, ts=ts)
    return api(u.k, u.uid, "PUT", f"/api/chats/{ch['id']}/messages/{m['id']}/reaction", {k: r[k] for k in ("dev", "ts", "ct", "sig")}), r


def edit(u, ch, orig, text):
    ents = u.keys([m["handle"] for m in ch["members"]])
    rc = C.recipients_v2([e for e in ents.values() if e])
    m = C.encrypt_v2(ch["ref"], u.handle, u.dev, rc, json.dumps({"edit": orig, "t": text}))
    m.update(v=2, kind="edit", edit_of=orig, files=[])
    return api(u.k, u.uid, "POST", f"/api/chats/{ch['id']}/messages", m), m


def opened(u, ch, m):
    return json.loads(C.decrypt_v2(ch["ref"], m, m["key"], *((u.dev["enc_pk"], u.dev["enc_sk"]) if m["key_for"] == "device"
                                                            else (u.ident["backup_pk"], u.ident["backup_sk"]))))


def caps(k):
    return set(json.loads(sql(k, "SELECT caps FROM fed_peer")[0][0] or "[]"))


V3_ON = 'auth.save_setting("chat_v3", True)\nprint(json.dumps(True))'
for _mod in (T, F2):                                # chat3 nur mit der Einstellung chat_v3 (2.5-RC)
    for _n in ("SETUP", "SETUP_B"):
        if isinstance(getattr(_mod, _n, None), str) and "chat_v3" not in getattr(_mod, _n):
            setattr(_mod, _n, getattr(_mod, _n).replace("print(json.dumps(True))", V3_ON))


def part_main(adm=None):
    anna, bert = FT.User("a", "fa-anna"), FT.User("b", "fb-bert")
    ok("A und B: Faehigkeit chat3 ausgehandelt", wait(lambda: "chat3" in caps("a") and "chat3" in caps("b"), 20))
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d["chat"]
    s, d, m1 = anna.send(ch, "Hallo Bert")
    bc = wait(lambda: bert.chat(ch["ref"]), 10)
    api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/request", {"action": "accept"})
    bc = bert.chat(ch["ref"])
    ch = api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"]
    ok("x3_ok auf beiden Seiten", ch.get("x3_ok") is True and bc.get("x3_ok") is True, (ch.get("x3_ok"), bc.get("x3_ok")))
    bm = wait(lambda: find(bert, bc, m1["id"]), 10)
    (s, d), rB = react(bert, bc, bm, "👍")
    ok(f"B (Teilnehmer) reagiert ({s})", s == 200, d)
    am = wait(lambda: (lambda x: x if x and x.get("rx") else None)(find(anna, ch, m1["id"])), 15)
    rx = (am or {}).get("rx") or []
    ok("A (Heim): Reaktion von B angekommen, oeffnet und prueft sie", len(rx) == 1 and rx[0]["by"] == bert.handle
       and json.loads(C.decrypt_reaction(key_of(anna, am), ch["ref"], m1["id"], rx[0]))["e"] == "👍"
       and C.verify_reaction(ch["ref"], m1["id"], rx[0], bert.dev["sign_pk"]), rx)
    (s, d), rA = react(anna, ch, am, "❤️")
    ok("B: Reaktion von A (Heim) angekommen, je Person eine", wait(lambda: len((find(bert, bc, m1["id"]) or {}).get("rx") or []) == 2, 15))
    (s, d), _ = react(bert, bc, bm, "😮", ts=rB["ts"] + 50)
    ok("A: B ersetzt die Reaktion (weiterhin zwei)", wait(lambda: any(json.loads(C.decrypt_reaction(key_of(anna, am), ch["ref"], m1["id"], x))["e"] == "😮"
                                                                     for x in (find(anna, ch, m1["id"]) or {}).get("rx") or []), 15)
       and len(find(anna, ch, m1["id"])["rx"]) == 2)
    # Bearbeiten durch das Heim
    (s, d), e1 = edit(anna, ch, m1["id"], "Hallo Bert!")
    ok(f"A (Heim) bearbeitet ({s})", s == 200, d)
    bo = wait(lambda: (lambda x: x if x and x.get("edit") else None)(find(bert, bc, m1["id"])), 15)
    ok("B: Bearbeitung von A angekommen (neuester Text)", bool(bo) and opened(bert, bc, bo["edit"])["t"] == "Hallo Bert!" and bo["edit_n"] == 1)
    # Bearbeiten durch den Teilnehmer
    s, d, m2 = bert.send(bc, "Hallo Anna")
    am2 = wait(lambda: find(anna, ch, m2["id"]), 15)
    (s, d), e2 = edit(bert, bc, m2["id"], "Hallo Anna :)")
    ok(f"B (Teilnehmer) bearbeitet ({s})", s == 200, d)
    ao = wait(lambda: (lambda x: x if x and x.get("edit") else None)(find(anna, ch, m2["id"])), 15)
    ok("A: Bearbeitung von B angekommen", bool(am2) and bool(ao) and opened(anna, ch, ao["edit"])["t"] == "Hallo Anna :)")
    (s, d), _ = edit(bert, bc, m2["id"], "Hallo Anna :D")
    ok("kein Verlauf: beim Heim nur die neueste Bearbeitung", wait(lambda: (find(anna, ch, m2["id"]) or {}).get("edit_n") == 2, 15)
       and sql("a", "SELECT COUNT(*) FROM chat_msg WHERE kind='edit' AND edit_of=?", (m2["id"],))[0][0] == 1)
    (s, d), _ = edit(bert, bc, m1["id"], "fremd")
    ok(f"B: fremde Nachricht bearbeiten -> 403 ({s})", s == 403, d)

    # --- B schaltet chat_v3 ab -------------------------------------------------------------------------------------------
    sql("b", "INSERT INTO setting (k, v) VALUES ('chat_v3', 'false') ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v")
    time.sleep(6)                                       # Einstellungen 5 s zwischengespeichert
    bc = bert.chat(ch["ref"])
    ok("B: chat_v3 aus -> x3_ok false", bc.get("x3_ok") is False, bc.get("x3_ok"))
    bm = find(bert, bc, m1["id"])
    (s, d), _ = react(bert, bc, bm, "🙏")
    ok(f"B: Reaktion bei chat_v3 aus -> 409 ({s})", s == 409, d)
    nb = sql("b", "SELECT COUNT(*) FROM chat_reaction")[0][0]
    am = find(anna, ch, m1["id"])
    (s, d), _ = react(anna, ch, am, "🎉")                 # A kennt den neuen Stand von B noch nicht (bis zum naechsten hello)
    ok(f"A (Heim) reagiert weiter ({s}) - B ignoriert das Ereignis", s == 200
       and not wait(lambda: sql("b", "SELECT COUNT(*) FROM chat_reaction")[0][0] != nb, 6), d)
    if adm:
        A, pa = adm
        A.post({"action": "hello", "id": str(pa)})
        ok("A: nach dem Abgleich meldet B kein chat3 mehr, x3_ok false",
           wait(lambda: "chat3" not in caps("a"), 15)
           and api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"].get("x3_ok") is False)
        (s, d), _ = react(anna, ch, am, "🎈")
        ok(f"A: Reaktion danach -> 409 ({s})", s == 409, d)


def part_old():
    anna, bert = FT.User("a", "fa-anna"), FT.User("b", "fb-bert")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d["chat"]
    s, d, m1 = anna.send(ch, "Hallo alt")
    ch = api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"]
    ok("A: B ohne chat3 -> x3_ok false", "chat3" not in caps("a") and ch.get("x3_ok") is False, ch.get("x3_ok"))
    am = find(anna, ch, m1["id"])
    (s, d), _ = react(anna, ch, am, "👍")
    (s2, d2), _ = edit(anna, ch, m1["id"], "neu")
    ok(f"Reaktion und Bearbeiten -> 409 ({s}, {s2})", s == 409 and s2 == 409)


def run():
    try:
        A, B, pa, pb = F2.main()
        part_old() if OLD else part_main((A, pa))
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\nFOEDERATION-X3 {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
