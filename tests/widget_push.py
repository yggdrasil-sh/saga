"""Widgets sofort aktuell (2.6, Bugs-DB 2188): stiller Widget-Push, Meldung der Widgets, /api/app/page/{id}, cover_v.

Wie tests/fakerelay.py: Saga-Server unter HK_TEST_BASE mit SAGA_PUSH_RELAY_URL=http://127.0.0.1:<FR_PORT>,
SAGA_PUSH_RELAY_HOST=saga.example.org und SAGA_WIDGET_PUSH_GAP=6 (Drosselung kurz fuer den Test). Dieser Prozess startet
den mitschneidenden Durchreicher (FR_PORT), das echte Relais (+1) mit Fake-APNs (+2) und das Fake-APNs des eigenen Wegs (+3).
Geprueft: sub_caps "widgets"; /api/app/pages mit cover_v/updated (Titelbild, erstes Bild im Inhalt, keins);
/api/app/page/{id} = Objekt der Liste, nur eigene (fremd, Vorlage, archiviert, Papierkorb -> 404, ohne/falscher Schluessel
-> 401); PUT/GET /api/app/widgets (Geraete-Anmeldung noetig, Pruefung, fremde Seiten weg, Kontogrenze); Ausloeser (Titel,
Unterseite, Zeile per Widget-Haekchen auch an das ausloesende Geraet, Aufgabe -> nur Geraete mit Heute-Widget, fremde
Seite/Konto -> nichts); Wege (Relais: push_type background, priority 5, expiration 0, aps nur content-available, Fake-APNs
sieht apns-push-type background; eigener APNs-Weg ebenso; Android ueber stream als Daten-Nachricht update); nie VoIP, nie
Abo ohne caps, nie ntfy; Nutzlast ohne Titel/Namen/Inhalte (Kanarien weder beim Relais noch bei Apple); Drosselung und
Buendeln (Abstand GAP, ein wartender Auftrag je Geraet, hoechstens HOUR je Stunde - plan_at); Geraet abgemeldet -> nichts.
Wegwerf-Konten wp-*."""
import json, os, secrets, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakerelay as F  # noqa: E402  (Durchreicher, Fake-APNs, echtes Relais)
import push as T  # noqa: E402  (tests/push.py: req, ck, UA, PW)
import auth  # noqa: E402
from db import connect  # noqa: E402
from saga import push, push_relay, widget_push  # noqa: E402
from saga.core import _tok_hash  # noqa: E402

GAP = int(os.environ.get("SAGA_WIDGET_PUSH_GAP") or 60)
CANARY = ("Kanarie-Widgettitel-4K", "Kanarie-Unterseite-8M", "Kanarie-Zeile-2P", "Kanarie-Aufgabe-6T", "Kanarie-Name-1Q")
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'wp-%'").fetchall()]
    for u in users:
        delete_user_data(u)
    with connect() as c, c:
        for t in ("note", "user_kv", "notification", "device_login", "push_sub", "push_queue", "task", "app_token"):
            c.execute(f"DELETE FROM {t} WHERE uid LIKE 'wp-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'wp-%'")


def device(uid, name):
    tok = "hkd_" + secrets.token_urlsafe(32)
    with connect() as c, c:
        did = c.execute("INSERT INTO device_login (uid, uname, name, hash, prefix, created) VALUES (?,?,?,?,?,?)",
                        (uid, uid, name, _tok_hash(tok), tok[:8], time.time())).lastrowid
    return did, {"Authorization": "Bearer " + tok}


def tap_pushes(since):
    with F.LOCK:
        return [json.loads(x[3]) for x in F.TAP[since:] if x[0] == "POST" and x[1] == "/v1/push"]


def stream_msgs(sid, since_id=0):
    with connect() as c:
        return c.execute("SELECT id, body FROM push_stream_msg WHERE sub_id=? AND id>? ORDER BY id", (sid, since_id)).fetchall()


def last_stream(sid):
    m = stream_msgs(sid)
    return m[-1][0] if m else 0


def queue(sid):
    with connect() as c:
        return c.execute("SELECT next_at, tries, created FROM push_queue WHERE sub_id=? AND kind=?", (sid, push.WIDGET_KIND)).fetchall()


def wait(pred, timeout=GAP + 12):
    return F.wait(pred, timeout)


def quiet(pred, secs):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return False
        time.sleep(0.3)
    return True


def settle_all(sids):
    """Wartende Widget-Pushes abwarten (Drosselung), damit der naechste Abschnitt frisch beginnt."""
    F.wait(lambda: not any(queue(s) for s in sids), GAP + 15)
    time.sleep(GAP + 0.5)


def main():
    cleanup()
    F.serve(F.FR, F.Tap)
    F.serve(F.AP1, F.FakeAPNs)
    F.serve(F.AP2, F.FakeAPNs)
    rel = F.RelayHarness()
    prev = {k: auth.settings(fresh=True).get(k) for k in ("push", "apns")}
    with connect() as c:
        prev_rows = {k: (c.execute("SELECT v FROM setting WHERE k=?", (k,)).fetchone() or [None])[0]
                     for k in ("apns", "push_relay", "push_relay_state")}
    try:
        run(rel)
    finally:
        rel.stop()
        with connect() as c, c:
            for k, v in prev_rows.items():
                if v is None:
                    c.execute("DELETE FROM setting WHERE k=?", (k,))
                else:
                    c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, v))
        auth.save_setting("push", prev["push"] or {})
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nWIDGET_PUSH {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def unit():
    """Drosselung rein (plan_at): Abstand, Stundengrenze, alte Eintraege fallen heraus."""
    at, s = widget_push.plan_at([], 1000.0, gap=60, hour=3, settle=3)
    ok(f"plan_at: erster Push nach settle ({at})", at == 1003.0 and s == [1003.0])
    at2, s = widget_push.plan_at(s, 1010.0, gap=60, hour=3, settle=3)
    ok(f"plan_at: zweiter fruehestens gap nach dem ersten ({at2})", at2 == 1063.0)
    at3, s = widget_push.plan_at(s, 1200.0, gap=60, hour=3, settle=3)
    at4, s4 = widget_push.plan_at(s, 1300.0, gap=60, hour=3, settle=3)
    ok(f"plan_at: hoechstens hour je Stunde ({at3}, {at4})", at3 == 1203.0 and at4 == 1003.0 + 3600 and len(s4) == 3)
    at5, _ = widget_push.plan_at([1.0, 2.0, 3.0], 5000.0, gap=60, hour=3, settle=3)
    ok("plan_at: Eintraege aelter als 1 h zaehlen nicht", at5 == 5003.0)
    msg = {"title": "", "body": "", "url": "/", "tag": "widgets", "kind": push.WIDGET_KIND, "update": True, "ttl": push.WIDGET_TTL}
    pt, prio, _, ctag, rate = push._apns_plan(msg, False)
    try:
        push._apns_plan(msg, True)
        voip = True
    except ValueError:
        voip = False
    ok(f"_apns_plan: Widget-Push still (background/5/bg, collapse u+tag), nie VoIP ({pt}, {prio}, {rate}, {ctag})",
       (pt, prio, rate, ctag) == ("background", 5, "bg", "uwidgets") and not voip)


def run(rel):
    unit()
    ok(f"Relais laeuft (Port {F.RP}), Adresse fuer den Server: {push_relay.url()}", rel.p.poll() is None and push_relay.url() == f"http://127.0.0.1:{F.FR}")
    k2, pem2 = F.p8()
    F.FakeAPNs.pubs[F.AP2] = F.raw_pub(k2)
    with connect() as c, c:
        c.execute("DELETE FROM setting WHERE k IN ('push_relay', 'push_relay_state', 'push_relay_old')")
    push_relay.forget_key_cache()
    push_relay.reset()
    auth.save_setting("push", {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True, "stream": True, "relay": True,
                               "apns_e2e_direct": True})
    auth.save_setting("apns", {"enabled": True, "team_id": "OWNTEAM001", "key_id": "OWNKEY0001", "bundle_id": F.B1, "p8": pem2.decode(),
                               "url": f"http://127.0.0.1:{F.AP2}"})
    time.sleep(F.WAIT)
    auth.create_user("wp-a", T.PW, "Kanarie-Name-1Q")
    auth.create_user("wp-b", T.PW, "Berta Widget")
    A, Bc = T.ck("wp-a"), T.ck("wp-b")
    T.req("PUT", "/api/account/pages", A, {"random_cover": False})
    T.req("PUT", "/api/account/pages", Bc, {"random_cover": False})
    hk = T.req("POST", "/api/settings/tokens", A, {"name": "Widget"})[1]["token"]
    hkb = T.req("POST", "/api/settings/tokens", Bc, {"name": "Widget"})[1]["token"]
    HK, HKB = {"Authorization": "Bearer " + hk}, {"Authorization": "Bearer " + hkb}

    # --- Daten ---------------------------------------------------------------------------------------------------------
    pg = T.req("POST", "/api/notes", A, {"title": "Kanarie-Widgettitel-4K", "icon": "🏠"})[1]["id"]
    sub = T.req("POST", "/api/notes", A, {"title": "Kanarie-Unterseite-8M", "parent_id": pg})[1]["id"]
    other = T.req("POST", "/api/notes", A, {"title": "Nicht im Widget"})[1]["id"]
    pimg = T.req("POST", "/api/notes", A, {"title": "Mit Bild im Inhalt"})[1]["id"]
    db = T.req("POST", "/api/notes", A, {"kind": "database", "title": "Liste"})[1]["id"]
    sch = T.req("GET", f"/api/notes/{db}", A)[1]["schema"]
    sch["props"] = [{"id": "ok", "name": "Häkchen", "type": "checkbox"}]
    T.req("PATCH", f"/api/notes/{db}", A, {"schema": sch})
    row = T.req("POST", "/api/notes", A, {"parent_id": db, "title": "Kanarie-Zeile-2P"})[1]["id"]
    tpl = T.req("POST", "/api/notes", A, {"title": "Vorlage"})[1]["id"]
    T.req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    arch = T.req("POST", "/api/notes", A, {"title": "Archiv"})[1]["id"]
    trash = T.req("POST", "/api/notes", A, {"title": "Papierkorb"})[1]["id"]
    bpg = T.req("POST", "/api/notes", Bc, {"title": "Seite von B"})[1]["id"]
    with connect() as c, c:
        c.execute("UPDATE note SET archived=? WHERE id=?", (time.time(), arch))
        c.execute("UPDATE note SET deleted_at=? WHERE id=?", (time.time(), trash))
        c.execute("UPDATE note SET cover=? WHERE id=?", ("/files/" + "a1" * 12, pg))
        c.execute("UPDATE note SET body=?, format='blocks' WHERE id=?",
                  (json.dumps({"blocks": [{"type": "image", "data": {"file": {"url": "/files/" + "b2" * 12}}}]}), pimg))
        c.execute("INSERT INTO note_stale (note_id, uid) VALUES (?, 'wp-a') ON CONFLICT DO NOTHING", (pimg,))

    # --- /api/app/pages und /api/app/page/{id} ---------------------------------------------------------------------------
    s, lst = T.req("GET", "/api/app/pages", None, hdr=HK)
    m = {p["id"]: p for p in lst} if s == 200 else {}
    ok(f"/api/app/pages: cover_v und updated je Seite ({len(m)})", s == 200 and all("cover_v" in p and isinstance(p.get("updated"), (int, float)) for p in lst))
    cv = m.get(pg, {}).get("cover_v")
    ok(f"cover_v: Titelbild -> Schluessel, erstes Bild im Inhalt -> Schluessel, ohne Bild leer ({cv}, {m.get(pimg, {}).get('cover_v')}, {m.get(other, {}).get('cover_v')!r})",
       cv and len(cv) == 12 and m.get(pimg, {}).get("cover_v") and m[pimg]["cover_v"] != cv and m.get(other, {}).get("cover_v") == "")
    with connect() as c, c:
        c.execute("UPDATE note SET cover=? WHERE id=?", ("/files/" + "c3" * 12, pg))
    s, one = T.req("GET", f"/api/app/page/{pg}", None, hdr=HK)
    ok(f"/api/app/page/{{id}}: eigenes Objekt wie in der Liste, cover_v neu nach anderem Titelbild ({one.get('cover_v') if s == 200 else s})",
       s == 200 and set(one) == set(m[pg]) and one["title"] == m[pg]["title"] and one["cover_v"] and one["cover_v"] != cv)
    s2, one2 = T.req("GET", f"/api/app/page/{db}", None, hdr=HK)
    ok("/api/app/page/{id}: Datenbank mit Ansichten", s2 == 200 and one2.get("kind") == "database" and isinstance(one2.get("views"), list))
    codes = [T.req("GET", f"/api/app/page/{x}", None, hdr=HK)[0] for x in (bpg, tpl, arch, trash, 2 ** 40)]
    ok(f"/api/app/page/{{id}}: fremd, Vorlage, archiviert, Papierkorb, unbekannt -> 404 ({codes})", codes == [404] * 5)
    s404, d404 = T.req("GET", f"/api/app/page/{bpg}", None, hdr=HK)
    ok("... 404 mit {error} (Apps unterscheiden das von einem aelteren Server)", isinstance(d404, dict) and d404.get("error"))
    ok("/api/app/page/{id}: B sieht die Seite von A nicht", T.req("GET", f"/api/app/page/{pg}", None, hdr=HKB)[0] == 404)
    d1, H1 = device("wp-a", "iPhone")
    codes = [T.req("GET", f"/api/app/page/{pg}")[0], T.req("GET", f"/api/app/page/{pg}", A)[0], T.req("GET", f"/api/app/page/{pg}", None, hdr=H1)[0]]
    ok(f"/api/app/page/{{id}}: nur App-Schluessel (ohne, Sitzung, Geraete-Schluessel -> 401) ({codes})", codes == [401, 401, 401])

    # --- Abos ------------------------------------------------------------------------------------------------------------
    s, st = T.req("GET", "/api/push", A)
    ok(f"/api/push: sub_caps mit widgets ({st['server'].get('sub_caps')})", "widgets" in (st["server"].get("sub_caps") or []))
    d4, H4 = device("wp-a", "iPad")
    d2, H2 = device("wp-a", "Pixel")
    d5, H5 = device("wp-a", "Altes iPhone")
    d3, H3 = device("wp-b", "Pixel B")
    ua_r, ua_d, ua_s, ua_b = T.UA(), T.UA(), T.UA(), T.UA()
    TR, TV, TD, TOLD = "8a" * 32, "8b" * 32, "8c" * 32, "8d" * 32
    r1 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TR, "env": "sandbox", "keys": ua_r.keys(), "topic": F.B2,
                                                         "caps": ["widgets"]}, hdr=H1)
    rv = T.req("POST", "/api/push/subscriptions", None, {"type": "apns-voip", "token": TV, "env": "sandbox", "keys": ua_r.keys(), "topic": F.B2,
                                                         "caps": ["widgets"]}, hdr=H1)
    r4 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TD, "env": "sandbox", "keys": ua_d.keys(), "topic": F.B1,
                                                         "caps": ["widgets"]}, hdr=H4)
    r2 = T.req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua_s.keys(), "caps": ["update", "widgets"]}, hdr=H2)
    r5 = T.req("POST", "/api/push/subscriptions", None, {"type": "apns", "token": TOLD, "env": "sandbox"}, hdr=H5)
    r3 = T.req("POST", "/api/push/subscriptions", None, {"type": "stream", "keys": ua_b.keys(), "caps": ["widgets"]}, hdr=H3)
    ok(f"Abos angelegt ({[x[0] for x in (r1, rv, r4, r2, r5, r3)]}; Wege {r1[1]['device'].get('via')}, {r4[1]['device'].get('via')})",
       all(x[0] in (200, 201) for x in (r1, rv, r4, r2, r5, r3)) and r1[1]["device"]["via"] == "relay" and r4[1]["device"]["via"] == "direct")
    sR, sV, sD, sS, sO, sB = (x[1]["id"] for x in (r1, rv, r4, r2, r5, r3))
    with connect() as c, c:
        caps = dict(c.execute("SELECT id, caps FROM push_sub WHERE id = ANY(?)", ([sR, sS, sO],)).fetchall())
        # ntfy-Abo mit "widgets" (von Hand) am iPhone: darf trotzdem nie einen Widget-Push bekommen
        sN = c.execute("INSERT INTO push_sub (uid, kind, endpoint, name, device_id, created, caps) VALUES ('wp-a','ntfy',?,?,?,?,'widgets')",
                       ("https://ntfy.example.org/wp-" + secrets.token_hex(6), "ntfy", d1, time.time())).lastrowid
    ok(f"caps gespeichert: apns widgets, stream update+widgets, altes Abo ohne ({caps})", caps.get(sR) == "widgets" and caps.get(sS) == "update,widgets" and not caps.get(sO))
    wait(lambda: push_relay.state().get("state") == "ok", 15)

    # --- Meldung der Widgets -------------------------------------------------------------------------------------------------
    s, d = T.req("PUT", "/api/app/widgets", A, {"pages": [pg]})
    ok(f"PUT /api/app/widgets ohne Geraete-Anmeldung -> 400 ({s})", s == 400)
    bad = [T.req("PUT", "/api/app/widgets", None, b, hdr=H1)[0] for b in ({"pages": "x"}, {"pages": [True]}, {"pages": [1.5]}, {"today": "ja"},
                                                                        {"pages": list(range(1, 300))}, [1, 2])]
    ok(f"PUT /api/app/widgets: ungueltige Angaben -> 400 ({bad})", bad == [400] * 6)
    ok("PUT /api/app/widgets ohne Anmeldung -> 401", T.req("PUT", "/api/app/widgets", None, {"pages": [pg]})[0] == 401)
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [pg, db, bpg, trash, pg], "today": False}, hdr=H1)
    ok(f"PUT (iPhone): eigene Seiten gemerkt, fremde/Papierkorb/doppelte weg, push true ({d})",
       s == 200 and d.get("pages") == [pg, db] and d.get("today") is False and d.get("push") is True)
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [pg], "today": True}, hdr=H4)
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [], "today": True}, hdr=H2)
    ok(f"PUT (Pixel): nur Heute-Widget ({d})", s == 200 and d == {"pages": [], "today": True, "push": True, "ok": True})
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [pg]}, hdr=H5)
    ok(f"PUT (altes iPhone ohne caps): gemerkt, aber push false ({d.get('push')})", s == 200 and d.get("push") is False)
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [pg, bpg]}, hdr=H3)
    ok(f"PUT (Konto B): Seite von A wird nicht gemerkt ({d.get('pages')})", s == 200 and d.get("pages") == [bpg])
    s, d = T.req("GET", "/api/app/widgets", None, hdr=H1)
    ok(f"GET /api/app/widgets: Stand dieses Geraets ({d})", s == 200 and d.get("pages") == [pg, db] and d.get("push") is True)
    with connect() as c:
        reg = json.loads(c.execute("SELECT v FROM user_kv WHERE uid='wp-a' AND k='app_widgets'").fetchone()[0])
    ok(f"user_kv app_widgets: je Geraet nur Kennungen ({sorted(reg)})", sorted(reg) == sorted(str(x) for x in (d1, d4, d2, d5))
       and not any(cn in json.dumps(reg) for cn in CANARY))

    allsubs = (sR, sV, sD, sS, sO, sB, sN)
    settle_all(allsubs)

    # --- 1. Titel einer Widget-Seite (im Web geaendert) ------------------------------------------------------------------
    t0, a1, a2, s0, b0 = len(F.TAP), len(F.APNS[F.AP1]), len(F.APNS[F.AP2]), last_stream(sS), last_stream(sB)
    T.req("PATCH", f"/api/notes/{pg}", A, {"title": "Kanarie-Widgettitel-4K neu"})
    g = wait(lambda: [p for p in tap_pushes(t0) if p.get("token") == TR])
    p0 = g[0] if g else {}
    inner = ua_r.decrypt(push.b64u_d(p0["saga"]["c"])) if p0.get("saga") else {}
    ok(f"Titel geaendert -> Relais: push_type background, priority 5, expiration 0, rate bg, Topic {F.B2} "
       f"({p0.get('push_type')}, {p0.get('priority')}, {p0.get('expiration')}, {p0.get('rate')}, {p0.get('topic')})",
       p0.get("push_type") == "background" and p0.get("priority") == 5 and p0.get("expiration") == 0 and p0.get("rate") == "bg"
       and p0.get("topic") == F.B2)
    ok(f"... sichtbares aps nur content-available ({p0.get('aps')})", p0.get("aps") == {"content-available": 1})
    ok(f"... entschluesselt: kind widgets.update, update, ohne Titel/Text ({inner})",
       inner.get("kind") == push.WIDGET_KIND and inner.get("update") is True and inner.get("title") == "" and inner.get("body") == ""
       and set(inner) <= {"title", "body", "url", "tag", "kind", "ts", "ttl", "v", "lang", "update"})
    ga = wait(lambda: [x for x in F.APNS[F.AP1][a1:] if x["tok"] == TR], 10)
    h = ga[0]["headers"] if ga else {}
    ok(f"... Fake-APNs hinter dem Relais: apns-push-type background, apns-priority 5, apns-expiration 0 ({h.get('apns-push-type')}, "
       f"{h.get('apns-priority')}, {h.get('apns-expiration')})",
       h.get("apns-push-type") == "background" and h.get("apns-priority") == "5" and h.get("apns-expiration") == "0" and h.get("apns-topic") == F.B2)
    gd = wait(lambda: [x for x in F.APNS[F.AP2][a2:] if x["tok"] == TD], 10)
    hd = gd[0]["headers"] if gd else {}
    bd = json.loads(gd[0]["body"]) if gd else {}
    ok(f"eigener APNs-Weg (iPad): background, 5, expiration 0, Topic {F.B1}, Ende-zu-Ende ({hd.get('apns-push-type')}, {hd.get('apns-expiration')})",
       hd.get("apns-push-type") == "background" and hd.get("apns-priority") == "5" and hd.get("apns-expiration") == "0"
       and hd.get("apns-topic") == F.B1 and bd.get("aps") == {"content-available": 1}
       and ua_d.decrypt(push.b64u_d(bd["saga"]["c"])).get("kind") == push.WIDGET_KIND)
    time.sleep(4)
    ok("Pixel (nur Heute-Widget) bekommt nichts zum Seitentitel", not stream_msgs(sS, s0))
    with F.LOCK:
        blob = b"".join(x[3] or b"" for x in F.TAP[t0:]) + b"".join(x["body"] for x in F.APNS[F.AP1][a1:] + F.APNS[F.AP2][a2:])
    leak = [cn for cn in CANARY if any(v in blob for v in F.variants(cn))]
    ok(f"keine Kanarie beim Relais oder bei Apple ({leak})", not leak)
    ok("nie VoIP, nie altes Abo ohne caps, nie Konto B",
       not [p for p in tap_pushes(t0) if p.get("token") in (TV, TOLD)] and not [x for x in F.APNS[F.AP2][a2:] if x["tok"] == TOLD]
       and not stream_msgs(sB, b0))
    with connect() as c:
        nq = c.execute("SELECT COUNT(*) FROM push_queue WHERE sub_id=?", (sN,)).fetchone()[0]
        last_err = c.execute("SELECT last_err, fails FROM push_sub WHERE id=?", (sN,)).fetchone()
    ok(f"ntfy-Abo mit caps widgets: kein Auftrag, kein Fehler ({nq}, {last_err})", nq == 0 and last_err and not last_err[0] and not last_err[1])

    # --- 2. Drosselung und Buendeln (direkt nach dem Push aus 1.) ------------------------------------------------------------
    with connect() as c:
        reg = json.loads(c.execute("SELECT v FROM user_kv WHERE uid='wp-a' AND k='app_widgets'").fetchone()[0])
    last = max(reg[str(d1)].get("s") or [0])
    t1 = len(F.TAP)
    for i in range(4):
        T.req("PATCH", f"/api/notes/{pg}", A, {"title": f"Kanarie-Widgettitel-4K {i}"})
        time.sleep(0.2)
    q = queue(sR)
    ok(f"vier Aenderungen kurz nacheinander: genau ein wartender Auftrag je Abo ({len(q)})", len(q) == 1 and q[0][1] == 0)
    ok(f"... geplant GAP nach dem letzten Push ({round(q[0][0] - last, 2) if q else None} s, GAP {GAP})",
       q and abs(q[0][0] - (last + GAP)) < 0.01 and q[0][0] > time.time())
    ok("... noch nichts gesendet (Drosselung)", not [p for p in tap_pushes(t1) if p.get("token") == TR])
    g = wait(lambda: [p for p in tap_pushes(t1) if p.get("token") == TR])
    time.sleep(3)
    ok(f"... genau ein Push nach der Wartezeit ({len([p for p in tap_pushes(t1) if p.get('token') == TR])})",
       g and len([p for p in tap_pushes(t1) if p.get("token") == TR]) == 1)
    settle_all(allsubs)

    # --- 3. Unterseite, fremde Seite, Widget-Haekchen (ausloesendes Geraet), Aufgabe -------------------------------------------
    t2 = len(F.TAP)
    T.req("PATCH", f"/api/notes/{other}", A, {"title": "Andere Seite neu"})
    ok("Seite ohne Widget geaendert -> kein Widget-Push", quiet(lambda: [p for p in tap_pushes(t2) if p.get("token") == TR], GAP + 6))
    t3 = len(F.TAP)
    T.req("PATCH", f"/api/notes/{sub}", A, {"title": "Kanarie-Unterseite-8M neu"})
    ok("Unterseite einer Widget-Seite umbenannt -> Push (Elternseite)", wait(lambda: [p for p in tap_pushes(t3) if p.get("token") == TR]))
    settle_all(allsubs)
    t4, a4 = len(F.TAP), len(F.APNS[F.AP2])
    s, _ = T.req("POST", f"/api/app/rows/{row}/check", None, {"pid": "ok", "value": True}, hdr=HK)
    ok(f"Haekchen im Widget gesetzt ({s}) -> Push auch an das ausloesende iPhone (Datenbank-Widget)",
       s == 200 and wait(lambda: [p for p in tap_pushes(t4) if p.get("token") == TR]))
    ok("... und nicht an das iPad (Datenbank nicht im Widget)", quiet(lambda: [x for x in F.APNS[F.AP2][a4:] if x["tok"] == TD], 3))
    settle_all(allsubs)
    t5, s5, a5 = len(F.TAP), last_stream(sS), len(F.APNS[F.AP2])
    tid = T.req("POST", "/api/tasks", A, {"text": "Kanarie-Aufgabe-6T"})[1].get("id")
    got_s = wait(lambda: stream_msgs(sS, s5))
    msg = ua_s.decrypt(bytes(got_s[0][1])) if got_s else {}
    ok(f"Aufgabe angelegt -> Pixel mit Heute-Widget: Daten-Nachricht ueber stream ({msg.get('kind')}, update {msg.get('update')}, Titel {msg.get('title')!r})",
       msg.get("kind") == push.WIDGET_KIND and msg.get("update") is True and msg.get("title") == "" and msg.get("body") == ""
       and not any(cn in json.dumps(msg, ensure_ascii=False) for cn in CANARY))
    ok("... iPad mit Heute-Widget ebenso (eigener Weg)", wait(lambda: [x for x in F.APNS[F.AP2][a5:] if x["tok"] == TD], 10))
    ok("... iPhone ohne Heute-Widget nicht", quiet(lambda: [p for p in tap_pushes(t5) if p.get("token") == TR], 3))
    settle_all(allsubs)
    s6 = last_stream(sS)
    T.req("PATCH", f"/api/tasks/{tid}", A, {"text": "Kanarie-Aufgabe-6T neu"})
    ok("Aufgabe geaendert (task.changed) -> Heute-Widget", tid and wait(lambda: stream_msgs(sS, s6)))
    settle_all(allsubs)
    s7 = last_stream(sS)
    T.req("DELETE", f"/api/tasks/{tid}", A)
    ok("Aufgabe geloescht -> Heute-Widget", wait(lambda: stream_msgs(sS, s7)))
    settle_all(allsubs)

    # --- 4. Konto B, Geraet abgemeldet ------------------------------------------------------------------------------------------
    b1, t6 = last_stream(sB), len(F.TAP)
    T.req("PATCH", f"/api/notes/{bpg}", Bc, {"title": "B neu"})
    ok("Konto B: eigenes Widget -> eigener Push; Konto A nichts", wait(lambda: stream_msgs(sB, b1))
       and not [p for p in tap_pushes(t6) if p.get("token") in (TR, TD)])
    with connect() as c, c:
        c.execute("DELETE FROM device_login WHERE id=?", (d1,))
    t7 = len(F.TAP)
    T.req("PATCH", f"/api/notes/{pg}", A, {"title": "Kanarie-Widgettitel-4K abgemeldet"})
    ok("iPhone abgemeldet -> kein Widget-Push mehr (Abo endet mit der Geraete-Anmeldung)",
       quiet(lambda: [p for p in tap_pushes(t7) if p.get("token") == TR], GAP + 6))
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [pg]}, hdr=H4)
    with connect() as c:
        reg = json.loads(c.execute("SELECT v FROM user_kv WHERE uid='wp-a' AND k='app_widgets'").fetchone()[0])
    ok(f"naechste Meldung raeumt das abgemeldete Geraet aus app_widgets ({sorted(reg)})", str(d1) not in reg and str(d4) in reg)
    s, d = T.req("PUT", "/api/app/widgets", None, {"pages": [], "today": False}, hdr=H4)
    with connect() as c:
        reg = json.loads(c.execute("SELECT v FROM user_kv WHERE uid='wp-a' AND k='app_widgets'").fetchone()[0])
    ok(f"leere Meldung entfernt den Eintrag des Geraets ({sorted(reg)})", s == 200 and str(d4) not in reg)


if __name__ == "__main__":
    main()
