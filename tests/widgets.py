"""Widget-Endpunkte (/api/app/pages, rows, tasks/done, rows/check) - Probe-Instanz.
Schwerpunkt: der App-Schluessel sieht/aendert NUR eigene Daten und NUR das Erlaubte."""
import json, os, base64, sqlite3, sys, urllib.request, urllib.parse
from datetime import date
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); ck = lambda u: S.sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()
A, X = ck("hk-test-a"), ck("hk-test-b"); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, cookie=None, b=None, bearer=None):
    h = {"Host": HOST, "Content-Type": "application/json"}
    if cookie: h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer: h["Authorization"] = "Bearer " + bearer
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h), timeout=20) as r:
            raw = r.read(); return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read(); return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {})
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    T = call("POST", "/api/settings/tokens", A, {"name": "Widget"})[1]["token"]
    TX = call("POST", "/api/settings/tokens", X, {"name": "Fremd"})[1]["token"]
    # Daten von A: Seite mit Unterseite, Datenbank mit Haekchen + Status, Vorlage
    pg = call("POST", "/api/notes", A, {"title": "Startseite", "icon": "🏠"})[1]["id"]
    sub = call("POST", "/api/notes", A, {"title": "Unterseite", "parent_id": pg})[1]["id"]
    db = call("POST", "/api/notes", A, {"kind": "database", "title": "ToDos"})[1]["id"]
    sch = call("GET", f"/api/notes/{db}", A)[1]["schema"]
    sch["props"] = [{"id": "st", "name": "Status", "type": "select", "options": [{"id": "s1", "name": "Offen", "color": "gray"}]},
                    {"id": "ok", "name": "Häkchen", "type": "checkbox"}]
    sch["views"] = [{"id": "tab", "name": "Tabelle", "type": "table"}, {"id": "arch", "name": "Archiv", "type": "list", "filters": [{"pid": "ok", "op": "checked"}]}]
    call("PATCH", f"/api/notes/{db}", A, {"schema": sch})
    row = call("POST", "/api/notes", A, {"parent_id": db, "title": "Renne", "props": {"st": "s1"}})[1]["id"]
    tpl = call("POST", "/api/notes", A, {"parent_id": db, "title": "Vorlage X"})[1]["id"]; call("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    xdb = call("POST", "/api/notes", X, {"kind": "database", "title": "Fremd-DB"})[1]["id"]
    xs = call("GET", f"/api/notes/{xdb}", X)[1]["schema"]; xs["props"] = [{"id": "ok", "name": "H", "type": "checkbox"}]; call("PATCH", f"/api/notes/{xdb}", X, {"schema": xs})
    xrow = call("POST", "/api/notes", X, {"parent_id": xdb, "title": "Fremd-Zeile"})[1]["id"]
    xtask = call("POST", "/api/tasks", X, {"text": "Fremd-Aufgabe"})[1]["id"]

    # --- Seiten-Liste
    c, d = call("GET", "/api/app/pages", bearer=T); ids = {p["id"]: p for p in d} if c == 200 else {}
    ok(f"Seitenliste: {len(ids)} eigene Eintraege", c == 200 and {pg, sub, db, row} <= set(ids))
    ok("Keine fremden Seiten", xdb not in ids and xrow not in ids)
    ok("Vorlagen nicht in der Auswahl", tpl not in ids)
    ok(f"Datenbank mit Ansichten {[v['name'] for v in ids.get(db, {}).get('views', [])]}", [v["id"] for v in ids.get(db, {}).get("views", [])] == ["tab", "arch"])
    ok("Nur Titel/Symbol/Eltern (kein Inhalt)", all(set(p) <= {"id", "title", "icon", "kind", "parent_id", "views", "cover", "cover_pos"} and all(isinstance(p.get("cover", False), bool) for p in d) for p in d))
    ok("Unterseite kennt ihre Eltern", ids.get(sub, {}).get("parent_id") == pg)

    # --- Zeilen
    c, d = call("GET", f"/api/app/rows/{db}", bearer=T)
    ok("Zeilen der eigenen Datenbank", c == 200 and any(r["id"] == row and r["props"].get("st") == "s1" for r in d["rows"]) and "views" in d["schema"])
    ok("Fremde Datenbank -> 404", call("GET", f"/api/app/rows/{xdb}", bearer=T)[0] == 404)
    ok("Normale Seite ist keine Datenbank -> 404", call("GET", f"/api/app/rows/{pg}", bearer=T)[0] == 404)

    # --- Haekchen-Feld
    c, _ = call("POST", f"/api/app/rows/{row}/check", bearer=T, b={"pid": "ok", "value": True})
    ok("Häkchen gesetzt", c == 200 and call("GET", f"/api/notes/{row}", A)[1]["props"].get("ok") is True)
    ok("Andere Werte bleiben erhalten", call("GET", f"/api/notes/{row}", A)[1]["props"].get("st") == "s1")
    ok("Status (kein Häkchen) wird abgelehnt", call("POST", f"/api/app/rows/{row}/check", bearer=T, b={"pid": "st", "value": True})[0] == 400)
    ok("Fremde Zeile -> 404 und unverändert", call("POST", f"/api/app/rows/{xrow}/check", bearer=T, b={"pid": "ok", "value": True})[0] == 404
       and not call("GET", f"/api/notes/{xrow}", X)[1]["props"].get("ok"))
    call("POST", f"/api/app/rows/{row}/check", bearer=T, b={"pid": "ok", "value": False})
    ok("Häkchen wieder entfernt (leer = nicht gespeichert, wie im Web)", "ok" not in call("GET", f"/api/notes/{row}", A)[1]["props"])

    # --- Aufgaben abhaken
    t1 = call("POST", "/api/tasks", A, {"text": "Klo putzen"})[1]["id"]
    c, _ = call("POST", f"/api/app/tasks/{t1}/done", bearer=T, b={"done": True})
    st = call("GET", "/api/tasks", A)[1]
    ok("Aufgabe abgehakt", c == 200 and any(t["id"] == t1 for t in st["done"]) and not any(t["id"] == t1 for t in st["open"]))
    call("POST", f"/api/app/tasks/{t1}/done", bearer=T, b={"done": False})
    ok("Wieder geöffnet", any(t["id"] == t1 for t in call("GET", "/api/tasks", A)[1]["open"]))
    t2 = call("POST", "/api/tasks", A, {"text": "Müll raus", "due": date.today().isoformat(), "repeat": "weekly"})[1]["id"]
    c, d = call("POST", f"/api/app/tasks/{t2}/done", bearer=T, b={"done": True, "today": date.today().isoformat()})
    nxt = [t for t in call("GET", "/api/tasks", A)[1]["open"] if t["text"] == "Müll raus"]
    ok(f"Wiederkehrend: Folgeaufgabe am {d.get('next_due')}", c == 200 and len(nxt) == 1 and nxt[0]["due"] == d.get("next_due") and nxt[0]["id"] != t2)
    call("POST", f"/api/app/tasks/{t2}/done", bearer=T, b={"done": False})
    ok("Haken zurück -> Folgeaufgabe wieder weg", [t["id"] for t in call("GET", "/api/tasks", A)[1]["open"] if t["text"] == "Müll raus"] == [t2])
    ok("Fremde Aufgabe -> 404 und offen", call("POST", f"/api/app/tasks/{xtask}/done", bearer=T, b={"done": True})[0] == 404
       and any(t["id"] == xtask for t in call("GET", "/api/tasks", X)[1]["open"]))

    # --- Seiten-Bilder (Widget "Seite"): nur Titelbild/Symbol eigener Seiten
    import secrets, time
    FD = os.path.join(os.path.dirname(DB), "files")
    PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    def img(uid):
        fid = secrets.token_hex(12); open(os.path.join(FD, fid), "wb").write(PNG)
        cn = sqlite3.connect(DB); cn.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                                             (fid, uid, "x.png", "image/png", len(PNG), 1, time.time())); cn.commit(); return "/files/" + fid
    cov, ico, other = img("hk-test-a"), img("hk-test-a"), img("hk-test-a")
    call("PATCH", f"/api/notes/{pg}", A, {"cover": cov, "icon": ico, "cover_pos": 30})
    meta = {p["id"]: p for p in call("GET", "/api/app/pages", bearer=T)[1]}[pg]
    ok(f"Seitenliste kennt Titelbild + Position ({meta.get('cover')}, {meta.get('cover_pos')})", meta.get("cover") is True and meta.get("cover_pos") == 30)
    def raw(p, bearer):
        try:
            with urllib.request.urlopen(urllib.request.Request(B + p, headers={"Host": HOST, "Authorization": "Bearer " + bearer}), timeout=20) as r:
                return r.status, r.read(), r.headers.get("Content-Type")
        except urllib.error.HTTPError as e: return e.code, b"", ""
    c, data, ct = raw(f"/api/app/page-image/{pg}/cover", T); ok(f"Titelbild ({ct})", c == 200 and data == PNG and ct == "image/png")
    ok("Symbol-Bild", raw(f"/api/app/page-image/{pg}/icon", T)[1] == PNG)
    ok("Seite ohne Bild -> 404", raw(f"/api/app/page-image/{sub}/cover", T)[0] == 404)
    ok("Fremde Seite -> 404", raw(f"/api/app/page-image/{xdb}/cover", T)[0] == 404 and raw(f"/api/app/page-image/{pg}/cover", TX)[0] == 404)
    ok("Nur cover/icon", raw(f"/api/app/page-image/{pg}/body", T)[0] == 404)
    ok("Andere eigene Dateien NICHT über den Schlüssel", raw(other, T)[0] in (401, 403, 404) and raw("/api/files", T)[0] in (401, 403, 404, 405))

    # --- Zugang
    ok("Browser-Anmeldung an Widget-Endpunkten -> 401", all(call(m, p, A, {})[0] == 401 for m, p in
       (("GET", "/api/app/pages"), ("GET", f"/api/app/rows/{db}"), ("POST", f"/api/app/tasks/{t1}/done"), ("POST", f"/api/app/rows/{row}/check"))))
    ok("Ohne Schlüssel -> 401", call("GET", "/api/app/pages")[0] == 401)
    ok("Schlüssel von B sieht A nicht", call("GET", f"/api/app/rows/{db}", bearer=TX)[0] == 404 and row not in {p["id"] for p in call("GET", "/api/app/pages", bearer=TX)[1]})
    ok("Schlüssel weiterhin nicht an normalen Endpunkten", call("GET", f"/api/notes/{db}/rows", bearer=T)[0] == 401 and call("GET", "/api/tasks", bearer=T)[0] == 401)
finally:
    cn = sqlite3.connect(DB)
    for q in ("DELETE FROM file WHERE uid LIKE ?", "DELETE FROM note WHERE uid LIKE ?", "DELETE FROM task WHERE uid LIKE ?", "DELETE FROM app_token WHERE uid LIKE ?", "DELETE FROM user_kv WHERE uid LIKE ?"):
        try: cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError: pass
    cn.commit()
print(f"\nWIDGETS {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
