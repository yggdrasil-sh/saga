"""Foederation 2.3 E5: freie Gruppen und Seiten-Chats mit Mitgliedern mehrerer Server - DREI eigene Saga-Instanzen A, B, C
(eigene Datenbanken und Ports). A ist mit B und mit C verbunden, B und C NICHT miteinander: Schluessel und Anhaenge von
Personen dritter Server laufen ueber das Heim. Apps sind In-Process-Clients mit PyNaCl (tests/chatlib.py, saga-chat/v2).

Geprueft: Gruppe ueber drei Server (Heim = Server der Erstellerin), Einladung als Nachrichtenanfrage, Annehmen, Nachrichten in
alle Richtungen (B -> Heim A -> C), Schluessel dritter Server ueber das Heim (geprueft + zwischengespeichert), Anhang einer
Person eines dritten Servers ueber das Heim, Systemzeilen mit Herkunft (hinzugefuegt/entfernt/verlassen), Rechte (nur Admins
aendern Mitglieder - auch von einem anderen Server aus), Gruppenname (verschluesselt, kind title), Verlassen, Tippen;
Seiten-Chat einer geteilten Seite mit fremder Gruppe (deren Server meldet die Mitglieder nur als Ziel einer Freigabe) und
fremder Person, Oeffnen von der fremden Seite aus, Entzug der Freigabe, Umstellung eines bestehenden Seiten-Chats auf v2
(alte Nachrichten bleiben v1 lesbar); Trennen (Mitglieder der getrennten Instanz raus, dort schreibgeschuetzt).
Kompatibilitaet: FED_B_APP=<Saga 2.1/2.2> -> B kann keine Gruppen ueber Servergrenzen: verstaendliche Meldung, Seiten-Chat
bleibt lokal (v1), keine Serverfehler. Raeumt auf (Datenbanken geloescht)."""
import json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
import foederation_chat as FC  # noqa: E402  (App-Client User)
import chatlib as C  # noqa: E402

T.I["a"].update(port=8495, admin=8494, db="saga_fga", url="http://127.0.0.1:8495", host="127.0.0.1:8495", state="/tmp/fedgrp-a",
                allow="localhost:8497, localhost:8499")
T.I["b"].update(port=8497, admin=8496, db="saga_fgb", url="http://localhost:8497", host="localhost:8497", state="/tmp/fedgrp-b", allow="127.0.0.1:8495")
T.I["c"] = dict(T.I["b"], port=8499, admin=8498, db="saga_fgc", url="http://localhost:8499", host="localhost:8499", state="/tmp/fedgrp-c",
                allow="127.0.0.1:8495", secret=T.I["b"]["secret"][::-1])
HA, HB, HC = T.I["a"]["host"], T.I["b"]["host"], T.I["c"]["host"]
ok, hreq, wait, jd, sql = T.ok, T.hreq, T.wait, T.jd, T.sql
api = FC.api
COMPAT = F2.COMPAT
User = FC.User


def connect(k1, k2, A1, A2):
    """k2 verbindet sich mit k1 (Einladung von k1)."""
    _, html = A1.post({"action": "invite"})
    code = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html).group(1)
    fp = jd(hreq(k1, "GET", "/.well-known/saga")[2])["fingerprint"]
    A2.post({"action": "preview", "url": T.I[k1]["url"], "code": code})
    A2.post({"action": "connect", "url": T.I[k1]["url"], "code": code, "fingerprint": fp})
    p1 = sql(k1, "SELECT id FROM fed_peer WHERE host=?", (T.I[k2]["host"],))[0][0]
    _, html = A1.post({"action": "confirm", "id": str(p1)})
    ok(f"{k1.upper()} <-> {k2.upper()} verbunden ({T.flash(html)[:30]})", "bestätigt" in T.flash(html))
    p2 = sql(k2, "SELECT id FROM fed_peer WHERE host=?", (T.I[k1]["host"],))[0][0]
    return p1, p2


def sys_lines(u, cid):
    out = []
    for m in u.msgs(cid):
        if m["kind"] == "sys":
            try:
                out.append(json.loads(m["body_ct"]))
            except ValueError:
                pass
    return out


def main():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna"), ("fa-carl", "Carl Ohne", None)], groups=[]))
    F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert"), ("fb-cleo", "Cleo Team", "cleo"),
                                                              ("fb-emil", "Emil Aus", None)], groups=[("fbteam", ["fb-bert", "fb-cleo"])]))
    F2.py("c", T.SETUP.format(allow=T.I["c"]["allow"], users=[("fc-dora", "Dora Dritt", "dora")], groups=[]))
    for k in T.I:
        F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    pab, pba = connect("a", "b", adm["a"], adm["b"])
    pac, pca = connect("a", "c", adm["a"], adm["c"])
    return adm, pab, pba, pac, pca


def caps_ok(k, host, cap="chatgroups"):
    r = sql(k, "SELECT caps FROM fed_peer WHERE host=?", (host,))
    return bool(r) and cap in json.loads(r[0][0])


# =====================================================================================================
def part_group(adm, pab, pba, pac, pca):
    ok("Faehigkeit chatgroups ausgehandelt (A-B, A-C)", wait(lambda: caps_ok("a", HB) and caps_ok("a", HC) and caps_ok("b", HA) and caps_ok("c", HA), 25))
    anna, carl = User("a", "fa-anna"), User("a", "fa-carl")
    bert, cleo, dora = User("b", "fb-bert"), User("b", "fb-cleo"), User("c", "fc-dora")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": ["fa-carl"], "handles": [bert.handle, dora.handle], "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"A: Gruppe ueber drei Server angelegt ({s}, v{ch.get('v')}, {len(ch.get('members', []))} Mitglieder)", s == 200 and ch.get("v") == 2
       and ch.get("home_self") and ch.get("kind") == "free" and len(ch["members"]) == 4 and ch.get("role") == "admin")
    hosts = sorted({m.get("host") for m in ch.get("members", [])})
    ok(f"A: Mitglieder mit Herkunft ({hosts})", hosts == sorted([HA, HB, HC]))
    bc = wait(lambda: bert.chat(ch["ref"]), 15)
    dc = wait(lambda: dora.chat(ch["ref"]), 15)
    ok("B, C: Gruppe erscheint als Nachrichtenanfrage (eingeladen von Anna, Herkunft)", bool(bc) and bc.get("request") and bool(dc) and dc.get("request")
       and (bc.get("invited_by") or {}).get("host") == HA and not bc.get("home_self"))
    ok("B, C: alle vier Mitglieder in der Kopie", len(bc["members"]) == 4 and len(dc["members"]) == 4)
    sysd = sys_lines(dora, dc["id"])
    ok(f"C: Systemzeile 'hinzugefuegt' mit Herkunft ({sysd[:1]})", any(x.get("type") == "members" and (x.get("by") or {}).get("host") == HA
                                                                       and {p["host"] for p in x.get("added", [])} >= {HB, HC} for x in sysd))
    ok("A: Carl (lokal) ist sofort Mitglied", bool(wait(lambda: carl.chat(ch["ref"]), 5)))
    for u, cc in ((bert, bc), (dora, dc)):
        s, d = api(u.k, u.uid, "POST", f"/api/chats/{cc['id']}/request", {"action": "accept"})
        ok(f"{u.k.upper()}: Einladung angenommen ({s})", s == 200 and d["chat"]["state"] == "ok")
    s, d, m1 = anna.send(ch, "Hallo zusammen")
    ok(f"A: Nachricht in die Gruppe ({s}, fseq {d.get('fseq')})", s == 200 and d.get("fseq"))
    ok("B und C: Nachricht angekommen und lesbar", wait(lambda: "Hallo zusammen" in bert.texts(bert.chat(ch["ref"])) and
                                                           "Hallo zusammen" in dora.texts(dora.chat(ch["ref"])), 20))
    # Bert (B) schreibt: B -> Heim A -> C; C ist mit B NICHT verbunden
    bc = bert.chat(ch["ref"])
    s, d, m2 = bert.send(bc, "Gruesse von B")
    ok(f"B: Nachricht ueber das Heim ({s})", s == 200 and not d.get("pending"))
    ok("C: Nachricht einer Person eines dritten Servers angekommen (Signatur ueber Schluessel vom Heim)",
       wait(lambda: "Gruesse von B" in dora.texts(dora.chat(ch["ref"])), 20))
    ok("C: Schluessel von Bert ueber das Heim zwischengespeichert", wait(lambda: bool(sql("c", "SELECT 1 FROM fed_chat_keys WHERE handle=?", (bert.handle,))), 5))
    ents = dora.keys([bert.handle], fresh=True)
    ok(f"C: GET /api/chat/v2/keys liefert Bert ueber das Heim ({(ents.get(bert.handle) or {}).get('source')})", bool(ents.get(bert.handle))
       and ents[bert.handle]["source"] in ("home", "cache"))
    dc = dora.chat(ch["ref"])
    s, d, m3 = dora.send(dc, "Und von C")
    ok(f"C: Nachricht (Schluessel fuer B ueber das Heim) ({s}, {d})", s == 200)
    ok("B: Nachricht von C angekommen", wait(lambda: "Und von C" in bert.texts(bert.chat(ch["ref"])), 20))
    ok("A: alle drei Nachrichten in fseq-Reihenfolge", wait(lambda: anna.texts(ch)[-3:] == ["Hallo zusammen", "Gruesse von B", "Und von C"], 10))
    # Anhang von C, abgerufen auf B (B nicht mit C verbunden) - ueber das Heim
    blob = C.encrypt_file_v2(ch["ref"], b"Datei von Dora " * 40)
    s, h, raw = FC.raw("c", "fc-dora", "POST", f"/api/chats/{dc['id']}/files", blob[0] if isinstance(blob, tuple) else blob)
    fid = (jd(raw) or {}).get("id") if s == 200 else None
    ok(f"C: Anhang hochgeladen ({s})", bool(fid))
    if fid:
        s, d, m4 = dora.send(dc, "mit Anhang", files=[{"id": fid}])
        ok(f"C: Nachricht mit Anhang ({s})", s == 200)
        bcc = wait(lambda: next((m for m in bert.msgs(bert.chat(ch["ref"])["id"]) if m["id"] == m4["id"]), None), 20)
        s, h, data = hreq("b", "GET", f"/api/chat/files/{fid}", hdr={"X-Saga": "1", "X-Saga-Chat": "2"}, cookie=T.ck("b", "fb-bert"))
        ok(f"B: Anhang einer Person von C ueber das Heim geholt ({s}, {len(data)} Bytes)", bool(bcc) and s == 200 and len(data) > 100)
    # Mitglieder aendern: nur Admins (Anna), auch von B aus nicht
    s, d = api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/members", {"handle": cleo.handle})
    ok(f"B: Bert (kein Admin) kann niemanden hinzufuegen ({s})", s == 403)
    s, d = api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/members", {"handle": cleo.handle})
    ok(f"A: Anna fuegt Cleo (B) hinzu ({s})", s == 200 and len(d["chat"]["members"]) == 5)
    cc = wait(lambda: cleo.chat(ch["ref"]), 15)
    ok("B: Cleo bekommt die Gruppe als Anfrage", bool(cc) and cc.get("request"))
    ok("C: Systemzeile 'Cleo (B) hinzugefuegt von Anna (A)'", wait(lambda: any(
        x.get("type") == "members" and any(p.get("host") == HB and "Cleo" in p.get("name", "") for p in x.get("added", []))
        and (x.get("by") or {}).get("host") == HA for x in sys_lines(dora, dc["id"])), 15))
    s, d = api("a", "fa-anna", "DELETE", f"/api/chats/{ch['id']}/members/fa-carl")
    ok(f"A: Anna entfernt Carl ({s})", s == 200)
    ok("B: Carl nicht mehr in der Kopie", wait(lambda: not any(m.get("host") == HA and "Carl" in m.get("name", "") for m in bert.chat(ch["ref"])["members"]), 15))
    # Gruppenname (verschluesselt, kind title)
    ch = anna.chat(ch["ref"])
    m = anna.msg(ch, "", body={"v": 1, "type": "title", "text": "Drei Server"})
    m["kind"] = "title"
    s, d = api("a", "fa-anna", "POST", f"/api/chats/{ch['id']}/messages", m)
    ok(f"A: Gruppenname gesetzt ({s})", s == 200)
    tm = wait(lambda: (bert.chat(ch["ref"]) or {}).get("title_msg"), 15)
    ok("B: Gruppenname (title_msg) kommt an und ist lesbar", bool(tm) and bert.read(bert.chat(ch["ref"]), tm).get("text") == "Drei Server")
    # Tippen in der Gruppe (B -> Heim -> C, verwerfbar)
    ok("B: Tippen ok", api("b", "fb-bert", "POST", f"/api/chats/{bc['id']}/typing")[0] == 200)
    # Verlassen von B aus
    s, d = api("b", "fb-bert", "DELETE", f"/api/chats/{bc['id']}/members/fb-bert")
    ok(f"B: Bert verlaesst die Gruppe ({s})", s == 200)
    ok("A: Bert ist raus, Systemzeile 'verlassen'", wait(lambda: not any(x.get("handle") == bert.handle for x in api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"]["members"]), 15)
       and any(x.get("type") == "members" and any(p.get("handle") == bert.handle for p in x.get("removed", [])) for x in sys_lines(anna, ch["id"])))
    ok("B: Bert sieht die Gruppe nicht mehr", wait(lambda: bert.chat(ch["ref"]) is None, 10))
    ch = anna.chat(ch["ref"])
    s, d, m5 = anna.send(ch, "ohne Bert")
    ok(f"A: Senden an neue Mitgliederliste ({s})", s == 200)
    ok("C: kommt an", wait(lambda: "ohne Bert" in dora.texts(dora.chat(ch["ref"])), 20))
    return anna, carl, bert, cleo, dora, ch


def part_page(adm, pab, pba, pac, pca, anna, carl, bert, cleo, dora):
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Gemeinsame Seite"})
    pg = d["id"]
    for tg, typ, role in ((f"fbteam@{HB}", "remote_group", "view"), (f"@dora@{HC}", "remote", "comment")):
        s, _ = api("a", "fa-anna", "PUT", f"/api/notes/{pg}/shares", {"type": typ, "target": tg, "role": role})
        ok(f"A: Seite geteilt mit {tg} ({s})", s == 200)
    ok("B, C: Freigaben angekommen", wait(lambda: sql("b", "SELECT 1 FROM fed_inbound") and sql("c", "SELECT 1 FROM fed_inbound"), 15))
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "page", "note_id": pg, "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    names = sorted(m.get("host") for m in ch.get("members", []))
    ok(f"A: Seiten-Chat mit Gruppe fbteam (B) und Dora (C) - v2 ({s}, {names})", s == 200 and ch.get("v") == 2 and names.count(HB) == 2 and names.count(HC) == 1)
    ok("B: fbteam-Mitglieder nur, weil die Gruppe Ziel einer Freigabe ist (Pruefprotokoll resolve)",
       bool(sql("b", "SELECT 1 FROM fed_log WHERE event='chat.resolve'")))
    rb = -(pg * 1024 + pba)
    s, d = api("b", "fb-bert", "POST", "/api/chats", {"kind": "page", "note_id": rb, "device": bert.dev["id"]})
    bc = d.get("chat") or {}
    ok(f"B: Seiten-Chat von der fremden Seite aus geoeffnet ({s}, {bc.get('name')})", s == 200 and bc.get("ref") == ch["ref"] and bc.get("kind") == "page"
       and bc.get("name") == "Gemeinsame Seite" and bc.get("note_id") == rb)
    cc = wait(lambda: cleo.chat(ch["ref"]), 15)
    ok("B: Cleo (fbteam) hat den Seiten-Chat ohne Anfrage", bool(cc) and not cc.get("request"))
    s, d, m1 = anna.send(ch, "Zur Seite")
    ok("B, C: Nachricht im Seiten-Chat", wait(lambda: "Zur Seite" in bert.texts(bert.chat(ch["ref"])) and "Zur Seite" in dora.texts(dora.chat(ch["ref"])), 20))
    dc = dora.chat(ch["ref"])
    s, d, m2 = dora.send(dc, "Antwort von C")
    ok(f"C -> Heim -> B ({s})", s == 200 and wait(lambda: "Antwort von C" in cleo.texts(cleo.chat(ch["ref"])), 20))
    api("a", "fa-anna", "DELETE", f"/api/notes/{pg}/shares?type=remote&target=%40dora%40" + HC)
    api("a", "fa-anna", "GET", "/api/chats")
    ok("A: Freigabe entzogen -> Dora aus dem Seiten-Chat", wait(lambda: not any(m.get("host") == HC for m in api("a", "fa-anna", "GET", f"/api/chats/{ch['id']}")[1]["chat"]["members"]), 15))
    ok("C: Kopie weg (keine eigenen Mitglieder mehr)", wait(lambda: dora.chat(ch["ref"]) is None, 15))
    # Umstellung eines bestehenden (lokalen) Seiten-Chats auf v2
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Erst lokal"})
    p2 = d["id"]
    api("a", "fa-anna", "PUT", f"/api/notes/{p2}/shares", {"type": "user", "target": "fa-carl", "role": "edit"})
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "page", "note_id": p2}, v2=False)
    lc = d["chat"]
    ok(f"A: lokaler Seiten-Chat (v1) ({s})", s == 200 and lc.get("v") is None)
    s, rc = api("a", "fa-anna", "GET", f"/api/chats/{lc['id']}/recipients", v2=False)
    m = C.encrypt(lc["id"], "fa-anna", anna.dev, {"v": 1, "type": "text", "text": "alt (v1)"}, C.recipients_of(rc["recipients"]))
    m.pop("_key", None)
    s, d = api("a", "fa-anna", "POST", f"/api/chats/{lc['id']}/messages", m, v2=False)
    ok(f"A: v1-Nachricht ({s})", s == 200)
    api("a", "fa-anna", "PUT", f"/api/notes/{p2}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok("B: Freigabe angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert'")) >= 1, 15))
    api("a", "fa-anna", "POST", "/api/chats", {"kind": "page", "note_id": p2})
    up = wait(lambda: (api("a", "fa-anna", "GET", f"/api/chats/{lc['id']}")[1].get("chat") or {}).get("v") == 2 and
              any(mm.get("host") == HB for mm in api("a", "fa-anna", "GET", f"/api/chats/{lc['id']}")[1]["chat"]["members"]), 20)
    ok("A: Seiten-Chat auf v2 umgestellt (gleicher Chat), Bert (B) Mitglied", up)
    old = [x for x in carl.msgs(lc["id"]) if x["kind"] == "msg"]
    ok("A: alte Nachricht bleibt v1 (ohne sender_handle) und fuer Carl lesbar",
       len(old) == 1 and not old[0].get("sender_handle") and C.decrypt(lc["id"], old[0], *((carl.dev["enc_pk"], carl.dev["enc_sk"]) if old[0]["key_for"] == "device"
                                                                                              else (carl.ident["backup_pk"], carl.ident["backup_sk"])))["text"] == "alt (v1)")
    ok("A: Systemzeile 'ab jetzt ueber Servergrenzen'", any(x.get("type") == "fed" for x in sys_lines(anna, lc["id"])))
    # Seiten-Chat geoeffnet, bevor die Freigabe beim anderen Server ankam: "kein Handle" gilt nur kurz (nicht 10 Minuten) und
    # eine Aenderung der Freigabe verwirft es sofort
    pr = f"@bert@{HB}"
    for title, age, reshare in (("Zu frueh geoeffnet", 40, False), ("Freigabe geaendert", 0, True)):
        s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": title})
        p3 = d["id"]
        n0 = len(sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert'"))
        api("a", "fa-anna", "PUT", f"/api/notes/{p3}/shares", {"type": "remote", "target": pr, "role": "view"})
        wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound WHERE target='fb-bert'")) > n0, 15)
        sql("a", "INSERT INTO fed_name_handle (principal, peer_id, handle, name, fetched) VALUES (?,?,NULL,'',?) ON CONFLICT (principal) "
                 "DO UPDATE SET handle=NULL, fetched=EXCLUDED.fetched", (pr, pab, time.time() - age))
        if reshare:
            api("a", "fa-anna", "PUT", f"/api/notes/{p3}/shares", {"type": "remote", "target": pr, "role": "comment"})
        s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "page", "note_id": p3, "device": anna.dev["id"]})
        c3 = d.get("chat") or {}
        ok(f"A: {title}: frueheres 'kein Handle' haelt den Seiten-Chat nicht auf v1 ({s}, v={c3.get('v')})", s == 200 and wait(
            lambda: (lambda x: x.get("v") == 2 and any(mm.get("host") == HB for mm in x.get("members", [])))(
                api("a", "fa-anna", "GET", f"/api/chats/{c3.get('id')}")[1].get("chat") or {}), 10))
    return ch


def part_disconnect(adm, pab, pba, pac, pca, anna, bert, cleo, dora, gch, pch):
    _, html = adm["a"].post({"action": "disconnect", "id": str(pac)})
    ok("A trennt die Verbindung zu C", "getrennt" in T.flash(html))
    s, d = api("a", "fa-anna", "GET", f"/api/chats/{gch['id']}")
    ok(f"A: Doras Mitgliedschaft endet (Gruppe bleibt schreibbar) ({[m.get('host') for m in d['chat']['members']]})",
       not any(m.get("host") == HC for m in d["chat"]["members"]) and not d["chat"].get("readonly"))
    ok("A: Systemzeile 'getrennt'", any(x.get("type") == "members" and x.get("reason") == "disconnected" for x in sys_lines(anna, gch["id"])))
    ok("B: Kopie ohne Dora", wait(lambda: not any(m.get("host") == HC for m in (cleo.chat(gch["ref"]) or {"members": []})["members"]), 15))
    dc = dora.chat(gch["ref"])
    ok("C: Gruppe mit getrenntem Heim schreibgeschuetzt, Verlauf bleibt", bool(dc) and dc.get("readonly") and dora.texts(dc))
    s, d, _ = dora.send(dc, "geht nicht mehr")
    ok(f"C: Senden abgewiesen ({s})", s in (403, 409))


def part_compat(adm, pab, pba, pac, pca):
    ok("A: B (alt) ohne chatgroups", wait(lambda: sql("a", "SELECT caps_at FROM fed_peer WHERE host=?", (HB,))[0][0] is not None, 20)
       and not caps_ok("a", HB))
    anna = User("a", "fa-anna")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": ["fa-carl"], "handles": ["~abcdefghijklmnop@" + HB]})
    ok(f"A -> B (alt): Gruppe nicht moeglich, verstaendlich ({s}, {str(d.get('error'))[:50]})", s == 403 and HB in d.get("error", ""))
    s, d = api("a", "fa-anna", "POST", "/api/notes", {"title": "Seite fuer alt"})
    pg = d["id"]
    api("a", "fa-anna", "PUT", f"/api/notes/{pg}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    api("a", "fa-anna", "PUT", f"/api/notes/{pg}/shares", {"type": "user", "target": "fa-carl", "role": "view"})
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "page", "note_id": pg, "device": anna.dev["id"]})
    ok(f"A: Seiten-Chat bleibt lokal (v1), ohne Personen der alten Instanz ({s})", s == 200 and d["chat"].get("v") is None
       and all(not m.get("remote") for m in d["chat"]["members"]))
    # Drei Server gemischt (E8): A und C sprechen 2.3, B ist alt - Gruppe A + C geht, B bleibt bei Protokoll 1 (Teilen/Lesen)
    dora = User("c", "fc-dora")
    s, d = api("a", "fa-anna", "POST", "/api/chats", {"kind": "free", "members": ["fa-carl"], "handles": [dora.handle], "device": anna.dev["id"]})
    ch = d.get("chat") or {}
    ok(f"gemischt: Gruppe A + C (2.3) geht, waehrend B alt ist ({s})", s == 200 and ch.get("v") == 2)
    dc = wait(lambda: dora.chat(ch.get("ref")), 15)
    if dc:
        api("c", "fc-dora", "POST", f"/api/chats/{dc['id']}/request", {"action": "accept"})
    anna.send(ch, "Hallo C trotz altem B")
    ok("gemischt: C hat die Nachricht", wait(lambda: "Hallo C trotz altem B" in dora.texts(dora.chat(ch["ref"])), 20))
    dora.send(dora.chat(ch["ref"]), "Antwort von C")
    ok("gemischt: A hat die Antwort von C", wait(lambda: "Antwort von C" in anna.texts(ch), 20))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{-(pg * 1024 + pba)}")
    ok(f"gemischt: B (alt) liest die mit Bert geteilte Seite (Protokoll 1) ({s})", s == 200 and d.get("title") == "Seite fuer alt")


def run():
    try:
        adm, pab, pba, pac, pca = main()
        if COMPAT:
            part_compat(adm, pab, pba, pac, pca)
        else:
            anna, carl, bert, cleo, dora, gch = part_group(adm, pab, pba, pac, pca)
            pch = part_page(adm, pab, pba, pac, pca, anna, carl, bert, cleo, dora)
            part_disconnect(adm, pab, pba, pac, pca, anna, bert, cleo, dora, gch, pch)
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
    print(f"\nFOEDERATION-GRUPPEN {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
