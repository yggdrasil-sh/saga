"""2.5 Fix-Runde 27.10. (Server): Vertraege E1-E4 und Fehler #3/#7 - lokal gegen den laufenden Server (HK_TEST_BASE),
danach die Foederation mit zwei eigenen Instanzen (Geruest tests/foederation2.py, FIX27_FED_PORTS, ohne FIX27_FED=0).

- E1: Eintrags-Vorlagen fuer Bearbeitende (rows.templates, Anlegen ohne via, Standard nur durch den Eigentuemer),
  Lesen/Kommentieren ohne Vorlagen, fremde Vorlage/anderes Ziel nicht; foederiert ebenso, aeltere Gegenstelle ohne Feld.
- E2: Startseite (start_page in /api/account), "/" -> Startseite, /dashboard bleibt, weg/ohne Zugriff = null.
- E3: Widgets metric/progress mit src "tasks" (Aufgaben der betrachtenden Person).
- E4: leerer Titel aus Vorlage, placeholder, discard nur durch die anlegende Person, nur unveraendert, nur in der Frist.
- #3: Widget-Werte nach Aenderungen an der Quelle sofort neu (DB und Aufgaben). #7: rows?limit=1 liefert Bearbeitenden
  default_tpl + templates der Ziel-DB (das Web legt damit ueber Relationen an).
Wegwerf-Konten fx27-*; raeumt auf."""
import base64, http.client, json, os, sys, time, urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
USERS = ("fx27-a", "fx27-b", "fx27-c", "fx27-d")
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, cookie, body=None, raw=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Cookie": "saga=" + cookie, "X-Saga": "1", "Content-Type": "application/json"}
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse(); d = r.read()
    if raw:
        c.close()
        return r.status, r.getheader("Location"), d
    try:
        d = json.loads(d)
    except ValueError:
        pass
    c.close()
    return r.status, d


def q(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchall()


def cleanup():
    from saga.userdata import delete_user_data
    for u in USERS:
        delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'fx27-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'fx27-%' OR target LIKE 'fx27-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'fx27-%'")


def blocks(*bl):
    return json.dumps({"blocks": list(bl)})


SCHEMA = {"props": [{"id": "status", "name": "Status", "type": "select",
                     "options": [{"id": "offen", "name": "Offen", "color": "blue"}, {"id": "fertig", "name": "Fertig", "color": "green"}]}],
          "views": [{"id": "alle", "name": "Alle", "type": "table"},
                    {"id": "offen", "name": "Offen", "type": "table", "filters": [{"pid": "status", "op": "is", "value": "offen"}]}]}


def e1_e4(A, Bk, C, D):
    db = req("POST", "/api/notes", A, {"kind": "database", "title": "Buecher"})[1]["id"]
    req("PATCH", f"/api/notes/{db}", A, {"schema": SCHEMA})
    tpl = req("POST", "/api/notes", A, {"parent_id": db, "title": "Neues Buch", "icon": "📚", "props": {"status": "offen"}})[1]["id"]
    req("PATCH", f"/api/notes/{tpl}", A, {"template": True, "format": "blocks",
                                          "body": blocks({"id": "x1", "type": "paragraph", "data": {"text": "Worum geht es?"}})})
    tpl2 = req("POST", "/api/notes", A, {"parent_id": db, "title": "Hoerbuch"})[1]["id"]
    req("PATCH", f"/api/notes/{tpl2}", A, {"template": True})
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": dict(SCHEMA, default_tpl=tpl)})
    ok(f"A: Datenbank mit zwei Eintrags-Vorlagen, Standard gesetzt ({s})", s == 200)
    db2 = req("POST", "/api/notes", A, {"kind": "database", "title": "Andere"})[1]["id"]
    for u, role in (("fx27-b", "edit"), ("fx27-c", "view"), ("fx27-d", "comment")):
        req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": u, "role": role})
    req("PUT", f"/api/notes/{db2}/shares", A, {"type": "user", "target": "fx27-b", "role": "edit"})

    # --- E1 ---
    s, d = req("GET", f"/api/notes/{db}/rows", Bk)
    ok(f"E1: Bearbeiten -> templates beide, default_tpl ({s})", s == 200 and {t["id"] for t in d.get("templates", [])} == {tpl, tpl2}
       and d["schema"].get("default_tpl") == tpl and d.get("role") == "edit"
       and next(t for t in d["templates"] if t["id"] == tpl) == {"id": tpl, "title": "Neues Buch", "icon": "📚"})
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1", Bk)
    ok(f"#7: rows?limit=1 (Relation '+ Neu') liefert default_tpl und templates ({s})",
       s == 200 and d["schema"].get("default_tpl") == tpl and len(d.get("templates", [])) == 2)
    for who, ckx in (("Lesen", C), ("Kommentieren", D)):
        s, d = req("GET", f"/api/notes/{db}/rows", ckx)
        ok(f"E1: {who} -> keine templates ({s})", s == 200 and d.get("templates") == [] and not d.get("rows"))
    s, d = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": db})
    rid = d.get("id") if s == 200 else None
    ok(f"E1/E4: Bearbeitende legt aus Vorlage an, fresh + placeholder ({s}, {str(d)[:90]})",
       s == 200 and isinstance(d.get("fresh"), str) and len(d["fresh"]) >= 16 and d.get("placeholder") == "Neues Buch")
    r = q("SELECT uid, parent_id, title, icon, props, body, is_template, created_by FROM note WHERE id=?", (rid,))
    ok("E1/E4: Eintrag gehoert A, Titel leer, Symbol/Werte/Inhalt aus der Vorlage, angelegt von B",
       bool(r) and r[0][0] == "fx27-a" and r[0][1] == db and r[0][2] == "" and r[0][3] == "📚"
       and json.loads(r[0][4] or "{}").get("status") == "offen" and "Worum geht es" in r[0][5] and r[0][6] == 0 and r[0][7] == "fx27-b")
    s, d = req("GET", f"/api/notes/{rid}", Bk)
    ok(f"E1: neuer Eintrag fuer B lesbar ({s})", s == 200 and d.get("title") == "")
    for who, ckx in (("Lesen", C), ("Kommentieren", D)):
        s, _ = req("POST", f"/api/notes/{tpl}/duplicate", ckx, {"parent_id": db})
        ok(f"E1: {who} darf nicht aus Vorlage anlegen ({s})", s == 403)
    s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": db2})
    ok(f"E1: Vorlage in eine andere Datenbank -> nicht ({s})", s == 404)
    s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {})
    ok(f"E1: ohne Ziel -> nicht ({s})", s == 404)
    s, _ = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": db, "template": True})
    ok(f"E1: 'template' fuer Bearbeitende wirkt nicht ({s})", s == 200 and q("SELECT count(*) FROM note WHERE parent_id=? AND is_template=1", (db,))[0][0] == 2)
    s1, _ = req("GET", f"/api/notes/{tpl}", Bk)
    s2, _ = req("PATCH", f"/api/notes/{tpl}", Bk, {"title": "Gekapert"})
    s3, _ = req("DELETE", f"/api/notes/{tpl}", Bk)
    ok(f"E1: Vorlage selbst lesen/aendern/loeschen nur Eigentuemer ({s1}, {s2}, {s3})", s1 == 404 and s2 == 404 and s3 in (403, 404)
       and q("SELECT title, deleted_at FROM note WHERE id=?", (tpl,))[0] == ("Neues Buch", None))
    s, _ = req("PATCH", f"/api/notes/{db}", Bk, {"schema": dict(SCHEMA, default_tpl=tpl2)})
    s2, _ = req("PATCH", f"/api/notes/{db}", Bk, {"schema": dict(SCHEMA, default_tpl=None)})
    ok(f"E1: Standard setzen/aufheben nur Eigentuemer ({s}, {s2})",
       json.loads(q("SELECT schema FROM note WHERE id=?", (db,))[0][0]).get("default_tpl") == tpl)
    s, d = req("POST", f"/api/notes/{tpl}/duplicate", A, {"parent_id": db, "title": "Dune"})
    ok(f"E4: mit title bleibt der Titel ({s})", s == 200 and q("SELECT title FROM note WHERE id=?", (d.get("id"),))[0][0] == "Dune"
       and d.get("fresh"))
    s, d = req("POST", f"/api/notes/{tpl}/duplicate", A, {"parent_id": db, "template": True})
    ok(f"E4: Vorlage duplizieren (Vorlage bleibt Vorlage) behaelt Namen, kein fresh ({s})",
       s == 200 and q("SELECT title, is_template FROM note WHERE id=?", (d.get("id"),))[0] == ("Neues Buch", 1) and "fresh" not in d)
    seite = req("POST", "/api/notes", A, {"title": "Seitenvorlage"})[1]["id"]
    req("PATCH", f"/api/notes/{seite}", A, {"template": True})
    s, d = req("POST", f"/api/notes/{seite}/duplicate", A, {})
    ok(f"E4: Seiten-Vorlage (nicht in einer DB) behaelt Namen, kein fresh ({s})",
       s == 200 and q("SELECT title FROM note WHERE id=?", (d.get("id"),))[0][0] == "Seitenvorlage" and "fresh" not in d)

    # --- E4: verwerfen ---
    def fresh():
        s, d = req("POST", f"/api/notes/{tpl}/duplicate", Bk, {"parent_id": db})
        return d["id"], d["fresh"]
    rid, tok = fresh()
    for who, ckx in (("Eigentuemer", A), ("Lesende", C)):
        s, _ = req("POST", f"/api/notes/{rid}/discard", ckx, {"fresh": tok})
        ok(f"E4: {who} darf fremden neuen Eintrag nicht verwerfen ({s})", s == 404 and q("SELECT 1 FROM note WHERE id=?", (rid,)))
    s, _ = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": "falsch-falsch-falsch"})
    ok(f"E4: falscher Schluessel -> 404 ({s})", s == 404)
    s, _ = req("POST", f"/api/notes/{rid}/discard", Bk, {})
    ok(f"E4: ohne Schluessel -> 400 ({s})", s == 400)
    # Editor speichert denselben Inhalt mit neuen Block-IDs/Zeitstempel: zaehlt nicht als Aenderung
    req("PATCH", f"/api/notes/{rid}", Bk, {"format": "blocks", "body": json.dumps(
        {"time": 1, "version": "2.31", "blocks": [{"id": "neu1", "type": "paragraph", "data": {"text": "Worum geht es?"}}]})})
    s, d = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: unveraendert (nur neu gespeichert) -> endgueltig weg, nicht im Papierkorb ({s})",
       s == 200 and d.get("deleted") is True and not q("SELECT 1 FROM note WHERE id=?", (rid,)))
    s, _ = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: zweites Verwerfen -> 404 ({s})", s == 404)
    for name, change in (("Titel", {"title": "Mein Buch"}), ("Eigenschaft", {"props": {"status": "fertig"}}),
                         ("Inhalt", {"format": "blocks", "body": blocks({"type": "paragraph", "data": {"text": "Notiz"}})}),
                         ("Symbol", {"icon": "📕"})):
        rid, tok = fresh()
        req("PATCH", f"/api/notes/{rid}", Bk, change)
        s, d = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
        ok(f"E4: {name} geaendert -> bleibt (409 changed, {s})", s == 409 and d.get("reason") == "changed" and d.get("deleted") is False
           and q("SELECT deleted_at FROM note WHERE id=?", (rid,)) == [(None,)])
    rid, tok = fresh()
    req("PATCH", f"/api/notes/{rid}", A, {"props": {"status": "fertig"}})
    s, d = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: jemand anderes hat geaendert -> bleibt ({s})", s == 409)
    rid, tok = fresh()
    s0, _ = req("POST", f"/api/notes/{rid}/comments", A, {"body": "Gute Wahl"})
    s, d = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: mit Kommentar -> bleibt ({s0}, {s})", s == 409)
    rid, tok = fresh()
    with connect() as c, c:
        c.execute("UPDATE note_fresh SET created=created-1000 WHERE note_id=?", (rid,))
    s, d = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: nach der Frist -> bleibt (409 expired, {s})", s == 409 and d.get("reason") == "expired")
    rid, tok = fresh()
    req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "fx27-b", "role": "view"})
    s, _ = req("POST", f"/api/notes/{rid}/discard", Bk, {"fresh": tok})
    ok(f"E4: Recht inzwischen nur Lesen -> nicht verwerfen ({s})", s == 404 and q("SELECT 1 FROM note WHERE id=?", (rid,)))
    req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": "fx27-b", "role": "edit"})
    rid, tok = (lambda d: (d["id"], d["fresh"]))(req("POST", f"/api/notes/{tpl}/duplicate", A, {"parent_id": db})[1])
    s, _ = req("POST", f"/api/notes/{rid}/discard", A, {"fresh": tok})
    ok(f"E4: Eigentuemer verwirft eigenen neuen Eintrag ({s})", s == 200 and not q("SELECT 1 FROM note WHERE id=?", (rid,)))
    s, _ = req("POST", f"/api/notes/{db}/discard", A, {"fresh": tok})
    ok(f"E4: beliebige Seite (nicht frisch) -> 404 ({s})", s == 404 and q("SELECT 1 FROM note WHERE id=?", (db,)))
    return db


def e2(A, Bk):
    s, d = req("GET", "/api/account", A)
    ok(f"E2: start_page anfangs null ({s})", s == 200 and "start_page" in d and d["start_page"] is None)
    s, loc, _ = req("GET", "/", A, raw=True)
    ok(f"E2: / ohne Startseite = Dashboard ({s})", s == 200)
    p = req("POST", "/api/notes", A, {"title": "Mein Tag"})[1]["id"]
    s, d = req("PATCH", "/api/account", A, {"start_page": p})
    ok(f"E2: start_page setzen ({s})", s == 200 and d.get("start_page") == p and req("GET", "/api/account", A)[1]["start_page"] == p)
    s, loc, _ = req("GET", "/", A, raw=True)
    ok(f"E2: / -> /notes#<id> ({s}, {loc})", s == 303 and loc == f"/notes#{p}")
    s, loc, body = req("GET", "/dashboard", A, raw=True)
    ok(f"E2: /dashboard bleibt erreichbar ({s})", s == 200 and b"<html" in body[:400].lower())
    s, loc, body = req("GET", "/notes", A, raw=True)
    ok("E2: Seitenleiste verlinkt das Dashboard (/dashboard)", s == 200 and b'href="/dashboard"' in body)
    fremd = req("POST", "/api/notes", Bk, {"title": "Privat B"})[1]["id"]
    s, _ = req("PATCH", "/api/account", A, {"start_page": fremd})
    ok(f"E2: fremde, nicht geteilte Seite -> 404 ({s})", s == 404 and req("GET", "/api/account", A)[1]["start_page"] == p)
    for bad in ("12", True, 1.5, [1]):
        s, _ = req("PATCH", "/api/account", A, {"start_page": bad})
        ok(f"E2: ungueltig {bad!r} -> 400 ({s})", s == 400)
    s, _ = req("PATCH", "/api/account", A, {"start_page": -1025})
    ok(f"E2: foederierte (negative) ID -> 404 ({s})", s == 404)
    geteilt = req("POST", "/api/notes", Bk, {"title": "Geteilt B"})[1]["id"]
    req("PUT", f"/api/notes/{geteilt}/shares", Bk, {"type": "user", "target": "fx27-a", "role": "view"})
    s, _ = req("PATCH", "/api/account", A, {"start_page": geteilt})
    ok(f"E2: geteilte Seite (Lesen) als Startseite ({s})", s == 200 and req("GET", "/api/account", A)[1]["start_page"] == geteilt)
    req("DELETE", f"/api/notes/{geteilt}/shares", Bk, {"type": "user", "target": "fx27-a"})
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE note_id=?", (geteilt,))
    s, d = req("GET", "/api/account", A)
    s2, loc, _ = req("GET", "/", A, raw=True)
    ok(f"E2: Freigabe entzogen -> null, / = Dashboard ({d.get('start_page')}, {s2})", d.get("start_page") is None and s2 == 200)
    req("PATCH", "/api/account", A, {"start_page": p})
    req("DELETE", f"/api/notes/{p}", A)
    s, d = req("GET", "/api/account", A)
    ok(f"E2: Seite im Papierkorb -> null ({d.get('start_page')})", d.get("start_page") is None)
    req("POST", f"/api/trash/{p}/restore", A)
    ok("E2: wiederhergestellt -> wieder gesetzt", req("GET", "/api/account", A)[1]["start_page"] == p)
    s, d = req("PATCH", "/api/account", A, {"start_page": None})
    ok(f"E2: zuruecksetzen ({s})", s == 200 and req("GET", "/api/account", A)[1]["start_page"] is None)
    with connect() as c, c:
        c.execute("UPDATE users SET source='ldap' WHERE username='fx27-a'")
    auth._ui_cache.pop("fx27-a", None)
    s, d = req("PATCH", "/api/account", A, {"start_page": p})
    ok(f"E2: start_page geht vor der Pruefung 'nur lokale Konten' ({s})", s == 200 and req("GET", "/api/account", A)[1]["start_page"] == p)
    with connect() as c, c:
        c.execute("UPDATE users SET source='local' WHERE username='fx27-a'")
    req("PATCH", "/api/account", A, {"start_page": None})


def widgets_page(ck_, blocks_):
    nid = req("POST", "/api/notes", ck_, {"title": "Widgets"})[1]["id"]
    req("PATCH", f"/api/notes/{nid}", ck_, {"format": "blocks", "body": json.dumps({"blocks": blocks_})})
    return nid


def wg(bid, kind, cfg):
    return {"id": bid, "type": "widget", "data": {"v": 1, "kind": kind, "title": bid, "cfg": cfg}}


def e3_3(A, Bk, db):
    tz = ZoneInfo("Europe/Berlin")
    today = datetime.now(tz).date()
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)
    later = today + timedelta(days=1) if today < sun else None          # Sonntag: kein "spaeter diese Woche"
    nextw = sun + timedelta(days=3)
    for text, due in (("ohne Datum", None), ("heute", today), ("gestern", today - timedelta(days=1)), ("naechste Woche", nextw),
                      ("erledigt", today), ("erledigt 2", None)):
        tid = req("POST", "/api/tasks", Bk, {"text": text, "due": due.isoformat() if due else None})[1]["id"]
        if text.startswith("erledigt"):
            req("PATCH", f"/api/tasks/{tid}", Bk, {"done": True})
    if later:
        req("POST", "/api/tasks", Bk, {"text": "spaeter", "due": later.isoformat()})
    page = widgets_page(Bk, [wg("o", "metric", {"src": "tasks", "agg": "open"}), wg("t", "metric", {"src": "tasks", "agg": "due_today"}),
                             wg("u", "metric", {"src": "tasks", "agg": "overdue"}), wg("w", "metric", {"src": "tasks", "agg": "done_week", "goal": 5}),
                             wg("p", "progress", {"src": "tasks"}), wg("x", "metric", {"src": "tasks", "agg": "quatsch", "db": 5})])
    body = json.loads(req("GET", f"/api/notes/{page}", Bk)[1]["body"])
    x = next(b for b in body["blocks"] if b["id"] == "x")["data"]["cfg"]
    p = next(b for b in body["blocks"] if b["id"] == "p")["data"]["cfg"]
    ok(f"E3: Bereinigung src tasks ({x}, {p})", x == {"src": "tasks", "agg": "open", "fmt": "num"} and p == {"src": "tasks"})
    s, d = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", Bk)
    w = d.get("widgets", {}) if s == 200 else {}
    n_open = 4 + (1 if later else 0)
    open_week = 1 + (1 if later else 0) + (1 if (today - timedelta(days=1)) >= mon else 0)
    v = {k: (w.get(k) or {}).get("value") for k in "otuwpx"}
    ok(f"E3: Werte offen/heute/ueberfaellig/erledigt ({v})", v["o"] == n_open and v["t"] == 1 and v["u"] == 1 and v["w"] == 2 and v["x"] == n_open
       and (w.get("w") or {}).get("max") == 5)
    ok(f"E3: Fortschritt erledigt diese Woche / (erledigt + offen faellig diese Woche) ({w.get('p')})",
       (w.get("p") or {}).get("value") == 2 and (w.get("p") or {}).get("max") == 2 + open_week and not (w.get("p") or {}).get("error"))
    s, d = req("POST", f"/api/notes/{page}/widgets/preview", Bk, {"block": wg("v", "metric", {"src": "tasks", "agg": "due_today"})})
    ok(f"E3: Vorschau ({s})", s == 200 and d["widget"]["value"] == 1)
    req("PUT", f"/api/notes/{page}/shares", Bk, {"type": "user", "target": "fx27-a", "role": "view"})
    s, d = req("GET", f"/api/notes/{page}/widgets", A)
    ok(f"E3: geteilte Seite zeigt die Aufgaben der betrachtenden Person (A: keine) ({s})",
       s == 200 and d["widgets"]["o"]["value"] == 0 and d["widgets"]["p"]["value"] == 0 and d["widgets"]["p"]["max"] == 0)
    # #3: Aufgaben sofort
    tid = req("POST", "/api/tasks", Bk, {"text": "neu", "due": today.isoformat()})[1]["id"]
    w = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", Bk)[1]["widgets"]
    ok(f"#3: neue Aufgabe sofort gezaehlt ({w['o']['value']}, {w['t']['value']})", w["o"]["value"] == n_open + 1 and w["t"]["value"] == 2)
    req("PATCH", f"/api/tasks/{tid}", Bk, {"done": True})
    w = req("GET", f"/api/notes/{page}/widgets?tz=Europe/Berlin", Bk)[1]["widgets"]
    ok(f"#3: erledigt sofort ({w['w']['value']})", w["w"]["value"] == 3 and w["o"]["value"] == n_open)
    # #3: Datenbank-Quelle sofort
    dp = widgets_page(A, [wg("n", "metric", {"db": db, "view": "offen", "agg": "count"}), wg("a", "metric", {"db": db, "view": "alle", "agg": "count"})])

    def vals():
        w = req("GET", f"/api/notes/{dp}/widgets", A)[1]["widgets"]
        return w["n"]["value"], w["a"]["value"]
    n0, a0 = vals()
    rid = req("POST", "/api/notes", A, {"parent_id": db, "title": "Neu", "props": {"status": "offen"}})[1]["id"]
    n1, a1 = vals()
    ok(f"#3: neue Zeile sofort ({n0},{a0} -> {n1},{a1})", n1 == n0 + 1 and a1 == a0 + 1)
    req("PATCH", f"/api/notes/{rid}", A, {"props": {"status": "fertig"}})
    n2, a2 = vals()
    ok(f"#3: Statuswechsel sofort ({n2},{a2})", n2 == n0 and a2 == a1)
    req("DELETE", f"/api/notes/{rid}", A)
    ok(f"#3: Papierkorb sofort ({vals()})", vals() == (n0, a0))
    req("POST", f"/api/trash/{rid}/restore", A)
    ok("#3: wiederhergestellt sofort", vals() == (n0, a0 + 1))
    sc = json.loads(q("SELECT schema FROM note WHERE id=?", (db,))[0][0])
    sc["views"][1]["filters"][0]["value"] = "fertig"
    req("PATCH", f"/api/notes/{db}", A, {"schema": sc})
    want = sum(1 for (pr,) in q("SELECT props FROM note WHERE parent_id=? AND is_template=0 AND deleted_at IS NULL", (db,))
               if json.loads(pr or "{}").get("status") == "fertig")
    ok(f"#3: Ansicht (Filter) geaendert sofort ({vals()}, {want})", vals()[0] == want and want != n0)


def gallery8(A):
    """#8 (nur gegen die echte gallery/): Mein Tag ohne Beispiele ohne Beispiel-Notizen, mit Kennzahl aus den
    Saga-Aufgaben; Reiseplaner mit Beispielen unter dem Budget."""
    s, d = req("GET", "/api/gallery", A)
    ids = {x.get("id") for x in (d.get("items") or [])} if s == 200 else set()
    if not {"mein-tag", "reise"} <= ids:
        print("(Galerie-Inhalte uebersprungen: Server ohne gallery/)")
        return
    for ex in (False, True):
        s, d = req("POST", "/api/gallery/mein-tag/use?lang=de", A, {"examples": ex})
        body = req("GET", f"/api/notes/{d.get('root')}", A)[1].get("body", "") if s == 200 else ""
        ok(f"#8: Mein Tag {'mit' if ex else 'ohne'} Beispiele: Notizen {'da' if ex else 'weg'}, 'example' nie gespeichert ({s})",
           s == 200 and ("Geschenkidee" in body) == ex and '"example"' not in body and "Schnellnotizen" in body)
        ok("E3: Mein Tag mit Kennzahl 'Offene Aufgaben' aus den Saga-Aufgaben + Hinweis auf Aufgaben & Projekte",
           '"src": "tasks"' in body.replace('":"', '": "') and ("Aufgaben & Projekte" in body or "Aufgaben &amp; Projekte" in body))
        if ex:
            w = req("GET", f"/api/notes/{d['root']}/widgets", A)[1].get("widgets", {})
            ok(f"E3: Mein Tag rechnet die Aufgaben-Kennzahl ({w.get('wmeinta5')})", (w.get("wmeinta5") or {}).get("value") == 0)
    s, d = req("POST", "/api/gallery/reise/use?lang=de", A, {"examples": True})
    w = req("GET", f"/api/notes/{d.get('root')}/widgets", A)[1].get("widgets", {}) if s == 200 else {}
    b = w.get("wreise2") or {}
    ok(f"#8: Reiseplaner-Beispiel unter dem Budget ({b.get('value')} / {b.get('max')})",
       isinstance(b.get("value"), (int, float)) and b.get("max") == 2000 and 0 < b["value"] < b["max"])


def fed_unit():
    """Heim-Seite (saga.fed_pages): Vorlagen aus der Antwort einer anderen Instanz - aeltere ohne Feld, kaputte Eintraege."""
    from saga import fed_pages as FP, federation as F
    p = {"id": 3}
    got = FP._tpl_in(p, {"id": 7, "title": "<b>X</b>" * 200, "icon": "☑️", "extra": 1})
    ok("fed: Vorlage -> negative ID, nur id/title/icon, Titel gekuerzt",
       got and got["id"] == F.ref(3, 7) and set(got) == {"id", "title", "icon"} and len(got["title"]) <= 500)
    ok("fed: kaputte Vorlagen fallen weg", FP._tpl_in(p, {"id": "7"}) is None and FP._tpl_in(p, {"id": -1}) is None and FP._tpl_in(p, "x") is None)
    sc = F.schema_in(3, {"props": [], "default_tpl": 7})
    ok("fed: default_tpl als negative ID (aeltere Gegenstelle ohne Feld: bleibt weg)",
       sc.get("default_tpl") == F.ref(3, 7) and "default_tpl" not in F.schema_in(3, {"props": []})
       and F.schema_in(3, {"props": [], "default_tpl": "x"}).get("default_tpl") is None)
    ok("fed: default_tpl geht nicht zurueck (setzt nur der Eigentuemer)", "default_tpl" not in F.schema_out(3, {"props": [], "default_tpl": F.ref(3, 7)}))


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in USERS:
        auth.create_user(u, "Fix27-Pass-1234", u)
    A, Bk, C, D = (ck(u) for u in USERS)
    time.sleep(5.6)
    try:
        db = e1_e4(A, Bk, C, D)
        e2(A, Bk)
        e3_3(A, Bk, db)
        gallery8(A)
        fed_unit()
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        cleanup()
        auth.save_setting("share_enabled", prev)
    if os.environ.get("FIX27_FED", "1") != "0":
        import fix27_fed
        res.extend(fix27_fed.run())
    print(f"\nFIX27-SERVER {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
