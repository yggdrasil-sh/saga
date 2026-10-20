"""Foederation 2.4: Gelesen-Bestaetigungen und Online-Status ueber Servergrenzen (saga/chat_status.py) mit zwei eigenen
Saga-Instanzen A und B (wie tests/foederation_chat.py; Apps = In-Process-Clients mit PyNaCl, saga-chat/v2).

Geprueft: Faehigkeiten receipts/online (CAPS2), Direkt-Chat mit Heim A und mit Heim B (Bestaetigung versiegelt fuer den
Backup-Schluessel, signiert mit Geraet + Handle + chat_ref, ueber das Heim), Nachrichtenanfrage (nichts, bis angenommen),
gegenseitig aus (Zuruecknahme ueber das Heim, nichts gespeichert), freie Gruppe ueber Servergrenzen ("gelesen von" je
Person, Weitergabe vom Heim an die anderen Server), Online-Status (Abo beim Server der Person, online/offline-Wechsel,
nur mit gemeinsamem Chat, aus = nichts), Faelschung (fremder Handle) wird verworfen, kein Zeiger im Klartext.
Kompatibilitaet: FED_B_23=1 mit FED_B_APP = Saga 2.3 -> A schickt nichts davon, keine Serverfehler. Raeumt auf."""
import json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_chat as FT  # noqa: E402  (Ports/Datenbanken, api, User, Events)
import chatlib as C  # noqa: E402

T, F2 = FT.T, FT.F2
ok, wait, sql, api = T.ok, T.wait, T.sql, FT.api
HA, HB = FT.HA, FT.HB
B23 = FT.B23
GRACE = int(os.environ.get("CHAT_OFFLINE_GRACE", "30"))


def rcpt_keys(u, ch, upto, n, handles, ts=None):
    """Bestaetigung von u fuer die Backup-Schluessel dieser Handles (saga-chat/v2: chat_ref + eigener Handle)."""
    ents = u.keys(handles)
    out = {}
    for h in handles:
        e = ents.get(h)
        if e:
            out["backup:" + h] = C.seal_receipt(ch["ref"], u.handle, u.dev, {"id": upto, "n": n, "ts": ts or int(time.time() * 1000)},
                                                C.unb64(e["backup_pub"], 32), v2=True)
    return out


def read_rc(u, ch, keys, seq=0):
    return api(u.k, u.uid, "POST", f"/api/chats/{ch['id']}/read", {"seq": seq, "receipt": {"keys": keys}})


def receipts(u, ch):
    return api(u.k, u.uid, "GET", f"/api/chats/{ch['id']}/receipts")[1]


def opened(u, ch, x, reader_handle):
    """Bestaetigung oeffnen und wie eine App pruefen (Geraeteschluessel aus /api/chat/v2/keys) -> body oder None."""
    dev, body, sig = C.open_receipt(x["sealed"], u.ident["backup_pk"], u.ident["backup_sk"])
    e = u.keys([reader_handle]).get(reader_handle) or {}
    d = next((d for d in e.get("devices") or [] if d["id"] == dev), None)
    if not d or not C.verify_receipt(ch["ref"], reader_handle, dev, body, sig, C.unb64(d["sign_pub"], 32), v2=True):
        return None
    return json.loads(body)


def other_mid(u, ch):
    """Kennung der neuesten Nachricht der anderen im Chat (oder None)."""
    ms = [m["id"] for m in u.msgs(ch["id"]) if m["kind"] == "msg" and m.get("sender_handle") != u.handle]
    return ms[-1] if ms else None


def accept(u, ch):
    return api(u.k, u.uid, "POST", f"/api/chats/{ch['id']}/request", {"action": "accept"})


def part_main():
    anna, bert, carl = FT.User("a", "fa-anna"), FT.User("b", "fb-bert"), FT.User("a", "fa-carl")
    ok("A: Faehigkeiten receipts + online mit B ausgehandelt",
       wait(lambda: {"receipts", "online"} <= set(json.loads(sql("a", "SELECT caps FROM fed_peer")[0][0] or "[]")), 20))
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d["chat"]
    ok("A: Chat-Kopf meldet receipts_ok/online_ok", ch.get("receipts_ok") is True and ch.get("online_ok") is True)
    s, d, m1 = anna.send(ch, "Hallo Bert")
    bc = wait(lambda: bert.chat(ch["ref"]), 10)
    mid = wait(lambda: other_mid(bert, bc), 10)
    # --- Nachrichtenanfrage: nichts, bis angenommen --------------------------------------------------------------------
    s, d = read_rc(bert, bc, rcpt_keys(bert, bc, mid, 1, [anna.handle]))
    ok(f"B: Anfrage noch offen -> keine Bestaetigung ({d.get('receipt')})", s == 200 and d.get("receipt") == "none")
    accept(bert, bc)
    bc = bert.chat(ch["ref"])
    evA = FT.Events("a", "fa-anna")
    time.sleep(0.5)
    s, d = read_rc(bert, bc, rcpt_keys(bert, bc, mid, 1, [anna.handle]))
    ok(f"B: Bestaetigung an das Heim A ({d.get('receipt')})", s == 200 and d.get("receipt") == "ok")
    got = wait(lambda: receipts(anna, ch).get("receipts"), 15)
    ok("A: Bestaetigung von Bert angekommen (Prinzipal + Handle)", bool(got) and got[0]["from"] == "@" + bert.handle and got[0]["handle"] == bert.handle)
    b1 = opened(anna, ch, got[0], bert.handle) if got else None
    ok("A: geoeffnet und Signatur (Geraet von Bert, chat_ref, Handle) gueltig, gelesen bis m1", bool(b1) and b1["id"] == m1["id"] and b1["n"] == 1)
    ok("A: Live-Ereignis chat.receipt", wait(lambda: any(t_ == "chat.receipt" and x.get("chat_id") == ch["id"] for t_, x in list(evA.ev)), 5))
    ok("A: Server A speichert nur versiegelt", all(m1["id"] not in r[0] for r in sql("a", "SELECT sealed FROM chat_receipt")))
    # Heim A -> B
    s, d, m2 = bert.send(bc, "Antwort von Bert")
    am = wait(lambda: [x for x in anna.msgs(ch["id"]) if x["id"] == m2["id"]], 15)
    fs = am[0]["fseq"] if am else 0
    s, d = read_rc(anna, ch, rcpt_keys(anna, ch, m2["id"], fs, [bert.handle]))
    ok(f"A (Heim): Bestaetigung an Bert ({d.get('receipt')})", d.get("receipt") == "ok")
    got = wait(lambda: receipts(bert, bc).get("receipts"), 15)
    b2 = opened(bert, bc, got[0], anna.handle) if got else None
    ok("B: Bestaetigung von Anna (vom Heim) gueltig", bool(b2) and b2["id"] == m2["id"] and got[0]["handle"] == anna.handle)
    ok("B: Server B speichert nur versiegelt", all(m2["id"] not in r[0] for r in sql("b", "SELECT sealed FROM chat_receipt")))
    # --- Heim B (Bert beginnt mit Carl) ---------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "POST", "/api/chats", {"kind": "dm", "handle": carl.handle, "device": bert.dev["id"]})
    hb = d["chat"]
    bert.send(hb, "Hallo Carl")
    cc = wait(lambda: carl.chat(hb["ref"]), 10)
    accept(carl, cc)
    cc = carl.chat(hb["ref"])
    cm = wait(lambda: other_mid(carl, cc), 10)
    s, d = read_rc(carl, cc, rcpt_keys(carl, cc, cm, 1, [bert.handle]))
    ok(f"A (Teilnehmer): Bestaetigung an das Heim B ({d.get('receipt')})", d.get("receipt") == "ok" and cc.get("receipts_ok") is True)
    got = wait(lambda: receipts(bert, hb).get("receipts"), 15)
    ok("B (Heim): Bestaetigung von Carl gueltig", bool(got) and (opened(bert, hb, got[0], carl.handle) or {}).get("id") == cm)
    # --- Gegenseitig aus ----------------------------------------------------------------------------------------------
    s, d = api("b", "fb-bert", "PUT", "/api/chat/privacy", {"receipts": False})
    ok("B: Bestaetigungen aus", s == 200 and d["receipts"] is False)
    ok("A: Berts Bestaetigung zurueckgezogen (ueber das Heim)", wait(lambda: receipts(anna, ch).get("receipts") == [], 15))
    ok("B aus: senden -> off", read_rc(bert, bc, rcpt_keys(bert, bc, mid, 1, [anna.handle]))[1].get("receipt") == "off")
    time.sleep(2.1)
    read_rc(anna, ch, rcpt_keys(anna, ch, m2["id"], fs, [bert.handle], ts=12345))
    time.sleep(3)
    ok("B aus: sieht Annas Bestaetigung nicht, Server B speichert keine neue fuer Bert",
       receipts(bert, bc).get("enabled") is False and
       all((opened(bert, bc, {"sealed": r[0]}, anna.handle) or {}).get("ts") != 12345 for r in sql("b", "SELECT sealed FROM chat_receipt WHERE viewer='fb-bert'")))
    api("b", "fb-bert", "PUT", "/api/chat/privacy", {"receipts": True})
    # --- Gruppe ueber Servergrenzen (Heim A: Anna, Carl; Teilnehmer B: Bert) ------------------------------------------
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": ["fa-carl"], "handles": [bert.handle], "device": anna.dev["id"]})
    g = d.get("chat") or {}
    ok(f"A: Gruppe ueber Servergrenzen ({s})", s == 200 and g.get("v") == 2)
    s, d, gm = anna.send(g, "Hallo Gruppe")
    gb = wait(lambda: bert.chat(g["ref"]), 10)
    accept(bert, gb)
    gc = carl.chat(g["ref"])
    if gc and gc.get("request"):
        accept(carl, gc)
    gb, gc = bert.chat(g["ref"]), carl.chat(g["ref"])
    hs = [anna.handle, carl.handle, bert.handle]
    read_rc(bert, gb, rcpt_keys(bert, gb, gm["id"], 1, [anna.handle, carl.handle]))
    read_rc(carl, gc, rcpt_keys(carl, gc, gm["id"], 1, [anna.handle, bert.handle]))
    ok("Gruppe: Anna sieht 'gelesen von' Bert und Carl",
       wait(lambda: sorted(x.get("handle") for x in receipts(anna, g).get("receipts") or []) == sorted([bert.handle, carl.handle]), 15))
    ok("Gruppe: Bert (B) sieht Carl (vom Heim A weitergegeben), Carl sieht Bert",
       wait(lambda: [x.get("handle") for x in receipts(bert, gb).get("receipts") or []] == [carl.handle], 15)
       and wait(lambda: [x.get("handle") for x in receipts(carl, gc).get("receipts") or []] == [bert.handle], 15))
    x = next(x for x in receipts(bert, gb)["receipts"])
    ok("Gruppe: Signatur von Carl bei Bert gueltig", (opened(bert, gb, x, carl.handle) or {}).get("id") == gm["id"])
    del hs
    # --- Faelschung: A meldet Online-Status einer Person von B -> verworfen --------------------------------------------
    priv = FT.own_priv("a")
    s, d = FT.signed_post("b", priv, "a", "/fed/v2/chat", {"op": "online.ev", "items": [{"h": bert.handle, "online": True}]})
    ok(f"B: 'online.ev' fuer eine Person von B (von A) wird ignoriert ({s})", s == 200)
    s, d = FT.signed_post("b", priv, "a", "/fed/v2/chat", {"op": "receipt", "ref": hb["ref"], "actor": bert.handle, "keys": {}})
    ok(f"B: Bestaetigung mit fremdem Handle (Person von B) wird ignoriert ({s})", s == 200 and receipts(bert, hb).get("receipts", [None])[0]["from"] == "@" + carl.handle)
    # --- Online-Status --------------------------------------------------------------------------------------------------
    evB = FT.Events("b", "fb-bert")
    time.sleep(1.0)
    me = "@" + bert.handle
    mk = len(evA.ev)
    s, d = api("a", "fa-anna", "POST", "/api/chat/presence", {"users": [me]})
    ok(f"A: Abo fuer Bert ({s})", s == 200 and d.get("enabled") is True)
    e = wait(lambda: next((x for t_, x in list(evA.ev)[mk:] if t_ == "chat.presence" and x.get("username") == me), None), 10)
    ok(f"A: Bert online (vom Server B gemeldet) ({e})", bool(e) and e["online"] is True)
    ok("A: zweite Abfrage liefert den Stand direkt", api("a", "fa-anna", "POST", "/api/chat/presence", {"users": [me]})[1]["users"].get(me, {}).get("online") is True)
    olga = FT.User("b", "fb-olga")
    ok("A: Person ohne gemeinsamen Chat -> nichts", "@" + olga.handle not in api("a", "fa-anna", "POST", "/api/chat/presence", {"users": ["@" + olga.handle]})[1]["users"])
    s, d = api("b", "fb-bert", "POST", "/api/chat/presence", {"users": ["@" + anna.handle]})
    ok("B: Abo fuer Anna", s == 200)
    mk = len(evA.ev)
    evB.close()
    e = wait(lambda: next((x for t_, x in list(evA.ev)[mk:] if t_ == "chat.presence" and x.get("username") == me and x.get("online") is False), None), GRACE + 20)
    ok(f"A: Bert offline gemeldet, zuletzt im 5-Minuten-Raster ({e})", bool(e) and e["last"] and e["last"] % 300 == 0)
    mk = len(evA.ev)
    s, d = api("b", "fb-bert", "PUT", "/api/chat/privacy", {"online": False})
    e = wait(lambda: next((x for t_, x in list(evA.ev)[mk:] if t_ == "chat.presence" and x.get("username") == me), None), 10)
    ok(f"B aus: A bekommt 'kein Status' ({e})", bool(e) and e["online"] is False and e["last"] is None)
    time.sleep(0.5)
    ok("B aus: A sieht Bert nicht mehr", me not in api("a", "fa-anna", "POST", "/api/chat/presence", {"users": [me]})[1]["users"])
    ok("B aus: Bert sieht Anna nicht (gegenseitig)", api("b", "fb-bert", "POST", "/api/chat/presence", {"users": ["@" + anna.handle]})[1]["enabled"] is False)
    api("b", "fb-bert", "PUT", "/api/chat/privacy", {"online": True})
    evA.close()
    ok("Gelesen-Zeiger bleibt lokal: last_read nicht im Ausgangskorb",
       not sql("a", "SELECT 1 FROM fed_outbox WHERE payload LIKE '%last_read%'") and not sql("b", "SELECT 1 FROM fed_outbox WHERE payload LIKE '%last_read%'"))


def part_b23():
    """B ist Saga 2.3: keine Faehigkeiten receipts/online -> A sendet nichts davon, alles andere laeuft."""
    anna, bert = FT.User("a", "fa-anna"), FT.User("b", "fb-bert")
    ok("A: Gegenstelle 2.3 ohne receipts/online",
       wait(lambda: sql("a", "SELECT caps_at FROM fed_peer")[0][0] is not None, 20)
       and "receipts" not in sql("a", "SELECT caps FROM fed_peer")[0][0] and "chat2" in sql("a", "SELECT caps FROM fed_peer")[0][0])
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "dm", "handle": bert.handle, "device": anna.dev["id"]})
    ch = d["chat"]
    ok("A: Chat-Kopf meldet receipts_ok/online_ok false", ch.get("receipts_ok") is False and ch.get("online_ok") is False)
    s, d, m1 = anna.send(ch, "Hallo 2.3")
    bc = wait(lambda: bert.chat(ch["ref"]), 10)
    accept(bert, bc)
    s, d, m2 = bert.send(bert.chat(ch["ref"]), "Antwort aus 2.3")
    am = wait(lambda: [x for x in anna.msgs(ch["id"]) if x["id"] == m2["id"]], 15)
    s, d = read_rc(anna, ch, rcpt_keys(anna, ch, m2["id"], am[0]["fseq"] if am else 0, [bert.handle]))
    ok(f"A: Bestaetigung angenommen, aber nicht an 2.3 gesendet ({d.get('receipt')})", s == 200 and d.get("receipt") == "ok")
    time.sleep(3)
    ok("A: nichts im Ausgangskorb an B", not sql("a", "SELECT 1 FROM fed_outbox WHERE payload LIKE '%receipt%'"))
    s, d = api("a", "fa-anna", "POST", "/api/chat/presence", {"users": ["@" + bert.handle]})
    ok("A: Online-Status von B-Personen nicht verfuegbar (kein Fehler)", s == 200 and d["users"] == {})
    ok("A: Nachrichten laufen weiter", wait(lambda: "Antwort aus 2.3" in anna.texts(ch), 10))


def run():
    try:
        F2.main()
        if B23:
            part_b23()
        elif FT.COMPAT:
            ok("Kompatibilitaet 2.1/2.2: nichts zu pruefen (kein chat2)", True)
        else:
            part_main()
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
    print(f"\nFOEDERATION-GELESEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
