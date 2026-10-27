"""API-Tokens + oeffentliche API /api/v1 (2.5, Paket 1 E5) gegen einen laufenden Test-Server.

Prueft: Anlegen nur mit Bestaetigung, Klartext einmal, nur Hash in der DB, Bereiche je Endpunkt (403 insufficient_scope),
roots (Seitenbaeume: ausserhalb = 404, Suche gefiltert), Rolle des Kontos (geteilte Seite nur lesen), Ablauf, Widerruf,
"ueberall abmelden", Admin-Schalter (API aus, nur Gruppe), Hoechstzahl, Ratenbegrenzung (429 + Retry-After), keine Cookies/
CORS, Seiten lesen/anlegen/aendern (Markdown und Bloecke), Datenbank-Schema/Abfrage/Zeilen per Eigenschaftsname,
Aufgaben, Kommentare, Dateien, Personen nur als Handle (Anmeldename taucht nie auf)."""
import base64, json, os, sqlite3, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
UA, UB = "hk-test-a", "hk-test-b"


def ck(u, confirmed=True):
    d = {"uid": u}
    if confirmed:
        d["sec_at"] = int(time.time())
    return S.sign(base64.b64encode(json.dumps(d).encode())).decode()


A, X, A0 = ck(UA), ck(UB), ck(UA, False)


def call(m, p, cookie=None, b=None, bearer=None, headers=None, raw_body=None, ctype="application/json"):
    h = {"Host": HOST, "Content-Type": ctype}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    h.update(headers or {})
    data = raw_body if raw_body is not None else (json.dumps(b).encode() if b is not None else None)
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=data, method=m, headers=h), timeout=20) as r:
            raw = r.read(); hd = dict(r.headers)
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else {}), raw.decode(errors="replace"), hd
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, (json.loads(raw) if raw[:1] in (b"{", b"[") else {}), raw.decode(errors="replace"), dict(e.headers)


def db():
    return sqlite3.connect(DB)


def setting(k, v):
    cn = db()
    if v is None:
        cn.execute("DELETE FROM setting WHERE k=?", (k,))
    else:
        cn.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?,?)", (k, json.dumps(v)))
    cn.commit()


res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:400]}")


def mk(scopes, name="Test", roots=None, days=None, cookie=A):
    b = {"name": name, "scopes": scopes}
    if roots is not None:
        b["roots"] = roots
    if days is not None:
        b["days"] = days
    return call("POST", "/api/account/api-tokens", cookie, b)


ALL = ["write:pages", "write:db", "write:tasks", "write:files", "write:comments", "webhooks", "mcp"]
made = []
try:
    setting("api", None)
    cn = db()                                            # Anzeigenamen != Anmeldename (Marker: Anmeldename darf nie auftauchen)
    cn.execute("UPDATE users SET display_name='Anna Api' WHERE username=?", (UA,))
    cn.execute("UPDATE users SET display_name='Bert Api' WHERE username=?", (UB,)); cn.commit()
    # --- Anlegen ---------------------------------------------------------------------------------------------
    st, d, _, _ = call("POST", "/api/account/api-tokens", A0, {"name": "x", "scopes": ["read:pages"]})
    ok("Anlegen ohne frische Bestaetigung -> 403 reauth", st == 403 and d.get("reauth") is True, d)
    ok("Anlegen ohne X-Saga (nur Cookie) -> 401", call("POST", "/api/account/api-tokens", None, {"name": "x", "scopes": ["read:pages"]},
                                                     headers={"Cookie": "saga=" + A})[0] == 401)
    ok("Unbekannter Bereich -> 400", mk(["admin"])[0] == 400)
    ok("Ohne Bereich -> 400", mk([])[0] == 400)
    ok("Laufzeit ueber Hoechstwert -> 400", mk(["read:pages"], days=400)[0] == 400)
    ok("Unbegrenzt ohne Admin-Freigabe -> 400", mk(["read:pages"], days=0)[0] == 400)
    st, d, raw, hd = mk(ALL, "Alles")
    T = d.get("token", "")
    ok("Token angelegt: sga_ + Praefix, Ablauf ~90 Tage, write bringt read mit",
       st == 200 and T.startswith("sga_") and len(T) >= 44 and d["prefix"] == T[:12] and 89 * 86400 < d["expires"] - time.time() <= 90 * 86400
       and "read:pages" in d["scopes"] and "read:db" in d["scopes"] and {k.lower(): v for k, v in hd.items()}.get("cache-control") == "no-store", d)
    st, d, raw, _ = call("GET", "/api/account/api-tokens", A)
    ok("Liste: ohne Klartext/Hash, mit Praefix", st == 200 and T not in raw and '"hash"' not in raw and any(x["prefix"] == T[:12] for x in d["tokens"]), raw[:300])
    h = db().execute("SELECT hash FROM api_token WHERE prefix=?", (T[:12],)).fetchone()[0]
    ok("In der DB nur der SHA-256-Hash", T not in h and len(h) == 64)

    # --- Anmeldung -------------------------------------------------------------------------------------------
    st, d, raw, hd = call("GET", "/api/v1/me", bearer=T)
    ok("/api/v1/me: Handle statt Anmeldename", st == 200 and d["handle"].startswith("~") and "username" not in d and UA not in d["handle"], raw)
    ok("Keine Sitzungs-Cookies, kein CORS", "set-cookie" not in {k.lower() for k in hd} and "access-control-allow-origin" not in {k.lower() for k in hd})
    st, _, _, hd = call("GET", "/api/v1/me")
    ok("Ohne Token -> 401 + WWW-Authenticate", st == 401 and "Bearer" in (hd.get("www-authenticate") or hd.get("WWW-Authenticate") or ""))
    ok("Falscher Token -> 401", call("GET", "/api/v1/me", bearer="sga_" + "x" * 43)[0] == 401)
    ok("App-Schluessel/Sitzung gilt nicht an /api/v1", call("GET", "/api/v1/me", A)[0] == 401)
    ok("API-Token gilt nicht an internen Endpunkten", call("GET", "/api/notes", bearer=T)[0] == 401)
    ok("OPTIONS (CORS-Preflight) liefert keine Freigabe", "access-control-allow-origin" not in
       {k.lower() for k in call("OPTIONS", "/api/v1/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})[3]})

    # --- Seiten ----------------------------------------------------------------------------------------------
    st, d, _, _ = call("POST", "/api/v1/pages", bearer=T, b={"title": "API Wurzel", "markdown": "# Kopf\n\nText **fett** und [Link](https://example.org).\n\n- a\n- [ ] b\n"})
    ok("Seite mit Markdown angelegt (201)", st == 201 and d.get("id"), d); R = d["id"]; made.append(R)
    st, d, _, _ = call("GET", f"/api/v1/pages/{R}", bearer=T)
    bl = d.get("blocks") or []
    ok("Blöcke aus Markdown: Ueberschrift, Absatz mit <b>, Liste", st == 200 and bl[0]["type"] == "header" and "<b>fett</b>" in json.dumps(bl)
       and any(x["type"] in ("list", "checklist") for x in bl), bl)
    st, d, _, _ = call("GET", f"/api/v1/pages/{R}?format=markdown", bearer=T)
    ok("Lesen als Markdown", st == 200 and "**fett**" in d.get("markdown", "") and "# Kopf" in d["markdown"], d)
    st, d, _, _ = call("POST", "/api/v1/pages", bearer=T, b={"title": "Kind", "parent_id": R, "markdown": "Kind-Text"})
    K = d["id"]
    st, d, _, _ = call("PATCH", f"/api/v1/pages/{K}", bearer=T, b={"append_markdown": "Nachtrag"})
    ok("append_markdown", st == 200 and "Nachtrag" in call("GET", f"/api/v1/pages/{K}?format=markdown", bearer=T)[1]["markdown"], d)
    rv = d.get("rev")
    st, d, _, _ = call("PATCH", f"/api/v1/pages/{K}", bearer=T, b={"title": "Kind neu", "rev": (rv or 1) - 1, "markdown": "x"})
    ok("PATCH mit alter rev -> 409", st == 409, d)
    ok("PATCH ohne Inhalt -> 400", call("PATCH", f"/api/v1/pages/{K}", bearer=T, b={})[0] == 400)
    ok("Markdown: javascript:-Link wird Text", "javascript" not in call("GET", f"/api/v1/pages/{call('POST', '/api/v1/pages', bearer=T, b={'title': 'js', 'parent_id': R, 'markdown': '[x](javascript:alert(1))'})[1]['id']}", bearer=T)[2])
    st, d, _, _ = call("GET", "/api/v1/search?q=kind-text", bearer=T)
    ok("Suche findet Inhalt", st == 200 and any(x["id"] == K for x in d["results"]), d)
    ok("Fremde Seite (Konto B) -> 404", call("GET", f"/api/v1/pages/{call('POST', '/api/notes', X, {'title': 'B privat'})[1]['id']}", bearer=T)[0] == 404)
    ok("Unsinnige ID -> 404/422 ohne Serverfehler", call("GET", "/api/v1/pages/99999999999999999999", bearer=T)[0] in (404, 422))

    # --- Bereiche --------------------------------------------------------------------------------------------
    st, d, _, _ = mk(["read:pages"], "Nur lesen"); TR = d["token"]
    st, d, _, hd = call("POST", "/api/v1/pages", bearer=TR, b={"title": "x"})
    ok("read:pages darf nicht schreiben -> 403 insufficient_scope", st == 403 and d.get("code") == "insufficient_scope", d)
    ok("read:pages darf keine DB/Aufgaben", call("GET", "/api/v1/databases", bearer=TR)[0] == 403 and call("GET", "/api/v1/tasks", bearer=TR)[0] == 403)
    ok("read:pages darf lesen", call("GET", f"/api/v1/pages/{R}", bearer=TR)[0] == 200)

    # --- roots -----------------------------------------------------------------------------------------------
    O = call("POST", "/api/notes", A, {"title": "Ausserhalb geheimwort"})[1]["id"]; made.append(O)
    st, d, _, _ = mk(["write:pages", "write:db"], "Nur Wurzel", roots=[R]); TT = d["token"]
    ok("roots: Seite darunter lesbar", call("GET", f"/api/v1/pages/{K}", bearer=TT)[0] == 200)
    ok("roots: Seite ausserhalb -> 404", call("GET", f"/api/v1/pages/{O}", bearer=TT)[0] == 404)
    ok("roots: Suche ohne Treffer ausserhalb", call("GET", "/api/v1/search?q=geheimwort", bearer=TT)[1]["results"] == [] and
       call("GET", "/api/v1/search?q=geheimwort", bearer=T)[1]["results"] != [])
    ok("roots: Anlegen ohne parent_id -> 403", call("POST", "/api/v1/pages", bearer=TT, b={"title": "oben"})[0] == 403)
    ok("roots: Anlegen ausserhalb -> 404", call("POST", "/api/v1/pages", bearer=TT, b={"title": "x", "parent_id": O})[0] == 404)
    ok("roots: Wurzel selbst hat kein parent_id-Leck", call("GET", f"/api/v1/pages/{R}", bearer=TT)[1].get("parent_id") is None)
    ok("roots: fremde Wurzel beim Anlegen -> 404", mk(["read:pages"], roots=[call("POST", "/api/notes", X, {"title": "B2"})[1]["id"]])[0] == 404)

    # --- Rolle (geteilte Seite, nur Lesen) ------------------------------------------------------------------
    BP = call("POST", "/api/notes", X, {"title": "B geteilt"})[1]["id"]
    call("PUT", f"/api/notes/{BP}/shares", X, {"type": "user", "target": UA, "role": "view"})
    st, d, raw, _ = call("GET", f"/api/v1/pages/{BP}", bearer=T)
    ok("Geteilte Seite lesbar, Eigentuemer nur als Handle", st == 200 and d["role"] == "view" and d["owner"]["handle"].startswith("~") and UB not in raw, raw[:300])
    ok("Nur Lesen: PATCH -> 403", call("PATCH", f"/api/v1/pages/{BP}", bearer=T, b={"title": "x"})[0] == 403)
    ok("Nur Lesen: DELETE -> 403", call("DELETE", f"/api/v1/pages/{BP}", bearer=T)[0] == 403)

    # --- Datenbanken -----------------------------------------------------------------------------------------
    st, d, _, _ = call("POST", "/api/v1/pages", bearer=T, b={"title": "Projekte", "kind": "database", "parent_id": R}); D = d["id"]
    s = call("GET", f"/api/notes/{D}", A)[1]["schema"]
    s["props"].append({"id": "wer", "name": "Wer", "type": "person"})
    s["props"].append({"id": "pkt", "name": "Punkte", "type": "number"})
    s["props"].append({"id": "fx", "name": "Doppelt", "type": "formula", "expr": 'prop("pkt") * 2'})
    sp = call("PATCH", f"/api/notes/{D}", A, {"schema": s})
    ok("Schema mit Person/Zahl/Formel gespeichert", sp[0] == 200, sp[1])
    call("PUT", f"/api/notes/{D}/shares", A, {"type": "user", "target": UB, "role": "edit"})
    st, d, _, _ = call("GET", f"/api/v1/databases/{D}", bearer=T)
    ok("Schema: Eigenschaften mit Namen und Optionen", st == 200 and any(p["name"] == "Status" and p.get("options") for p in d["schema"]["properties"]), d)
    ok("Datenbanken auflisten", any(x["id"] == D for x in call("GET", "/api/v1/databases?q=projekt", bearer=T)[1]["databases"]))
    DONE = next(o["name"] for p in d["schema"]["properties"] if p["id"] == "status" for o in p["options"] if o["id"] == "done")
    hb = call("GET", f"/api/notes/{D}/people", A)[1]
    bref = next((p["ref"] for p in hb.get("people", []) if p.get("ref") and not p.get("me")), None)
    st, d, _, _ = call("POST", f"/api/v1/databases/{D}/rows", bearer=T, b={"title": "Zeile 1", "properties": {"Status": DONE, "Punkte": 5, "Wer": [bref]}})
    ok("Zeile per Eigenschaftsname, Auswahl per Optionsname, Person per Handle", st == 201, d); Z1 = d.get("id")
    call("POST", f"/api/v1/databases/{D}/rows", bearer=T, b={"title": "Zeile 2", "properties": {"status": "todo", "Punkte": 1}})
    ok("Unbekannte Eigenschaft -> 400", call("POST", f"/api/v1/databases/{D}/rows", bearer=T, b={"title": "x", "properties": {"Gibtsnicht": 1}})[0] == 400)
    ok("Unbekannte Option -> 400", call("POST", f"/api/v1/databases/{D}/rows", bearer=T, b={"title": "x", "properties": {"Status": "Quatsch"}})[0] == 400)
    st, d, raw, _ = call("POST", f"/api/v1/databases/{D}/query", bearer=T, b={"filters": [{"property": "Status", "op": "is", "value": DONE}],
                                                                             "sorts": [{"property": "Punkte", "dir": "desc"}]})
    r1 = (d.get("rows") or [{}])[0]
    ok("Abfrage mit Filter/Sortierung per Name, Formel gerechnet", st == 200 and d["matched"] == 1 and r1.get("title") == "Zeile 1"
       and r1["properties"].get("Status") == DONE and r1["properties"].get("Doppelt") == 10, d)
    ok("Personen nur als Handle (kein Anmeldename)", UB not in raw and (bref or "~") in raw and "people" in d, raw[:400])
    st, d, _, _ = call("PATCH", f"/api/v1/rows/{Z1}", bearer=T, b={"properties": {"Punkte": 7}})
    ok("Zeile aendern: Formel in calc", st == 200 and (d.get("calc") or {}).get("fx") == 14, d)
    ok("PATCH /rows auf normaler Seite -> 400", call("PATCH", f"/api/v1/rows/{K}", bearer=T, b={"properties": {"x": 1}})[0] == 400)
    ok("Abfrage: limit > 100 wird begrenzt", call("POST", f"/api/v1/databases/{D}/query", bearer=T, b={"limit": 5000})[1].get("limit") == 100)
    ok("Abfrage: kaputter Filter -> 400", call("POST", f"/api/v1/databases/{D}/query", bearer=T, b={"filters": "x"})[0] == 400)

    # --- Aufgaben, Kommentare --------------------------------------------------------------------------------
    st, d, _, _ = call("POST", "/api/v1/tasks", bearer=T, b={"text": "API-Aufgabe", "due": "2026-12-24"}); TK = d.get("id")
    ok("Aufgabe anlegen/lesen", st == 201 and any(x["id"] == TK for x in call("GET", "/api/v1/tasks", bearer=T)[1]["tasks"]))
    ok("Aufgabe abhaken", call("PATCH", f"/api/v1/tasks/{TK}", bearer=T, b={"done": True})[0] == 200 and
       any(x["id"] == TK for x in call("GET", "/api/v1/tasks?done=1", bearer=T)[1]["tasks"]))
    ok("Fremde Aufgabe -> 404", call("PATCH", "/api/v1/tasks/999999999", bearer=T, b={"done": True})[0] == 404)
    st, d, _, _ = call("POST", f"/api/v1/pages/{BP}/comments", bearer=T, b={"text": "Nur Lesen"})
    ok("Kommentar mit Rolle 'Lesen' -> 403", st == 403, d)
    st, d, _, _ = call("POST", f"/api/v1/pages/{K}/comments", bearer=T, b={"text": "Hallo API"})
    st2, d2, raw2, _ = call("GET", f"/api/v1/pages/{K}/comments", bearer=T)
    ok("Kommentar schreiben/lesen, Autor als Handle", st == 201 and st2 == 200 and d2["threads"][0]["comments"][0]["text"] == "Hallo API"
       and d2["threads"][0]["comments"][0]["author"]["handle"].startswith("~") and UA not in raw2, raw2[:300])

    # --- Dateien ---------------------------------------------------------------------------------------------
    bnd = "----sagatest"
    mp = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"notiz.txt\"\r\nContent-Type: text/plain\r\n\r\nHallo Datei\r\n--{bnd}--\r\n").encode()
    st, d, _, _ = call("POST", f"/api/v1/files?page={K}", bearer=T, raw_body=mp, ctype=f"multipart/form-data; boundary={bnd}")
    ok("Datei hochladen", st == 201 and d.get("url", "").startswith("/files/"), d)
    fid = d.get("url", "/files/x")[7:]
    st, _, raw, hd = call("GET", f"/api/v1/files/{fid}", bearer=T)
    ok("Datei herunterladen (eigene)", st == 200 and raw == "Hallo Datei" and "attachment" in (hd.get("content-disposition") or hd.get("Content-Disposition") or ""))
    ok("Datei mit roots-Token ohne Seite -> 404", call("GET", f"/api/v1/files/{fid}", bearer=TT)[0] in (403, 404))
    ok("Datei-Upload ohne Bereich -> 403", call("POST", "/api/v1/files", bearer=TR, raw_body=mp, ctype=f"multipart/form-data; boundary={bnd}")[0] == 403)

    # --- Loeschen --------------------------------------------------------------------------------------------
    st, d, _, _ = call("DELETE", f"/api/v1/pages/{K}", bearer=T)
    ok("Seite in den Papierkorb", st == 200 and call("GET", f"/api/v1/pages/{K}", bearer=T)[0] == 404, d)

    # --- Ratenbegrenzung -------------------------------------------------------------------------------------
    setting("api", {"enabled": True, "group": "", "max_tokens": 10, "max_days": 365, "unlimited": False, "rate": 5, "rate_write": 2,
                    "webhooks": True, "mcp": True, "webhook_http": False})
    time.sleep(5.5)                                      # Einstellungen sind 5 s zwischengespeichert
    codes = []
    st, d, _, _ = mk(["read:tasks"], "Rate2"); TQ = d["token"]
    time.sleep(61)                                       # Fenster des Kontos (Summe aller Tokens) frei
    for i in range(7):
        c_, _, _, hd = call("GET", "/api/v1/tasks", bearer=TQ)
        codes.append(c_)
    ok("Ratenbegrenzung: nach 5 Anfragen 429 + Retry-After", codes[:5] == [200] * 5 and codes[5] == 429 and int(hd.get("Retry-After") or hd.get("retry-after") or 0) >= 1, codes)
    setting("api", None); time.sleep(5.5)

    # --- Ablauf, Widerruf, Hoechstzahl -----------------------------------------------------------------------
    st, d, _, _ = mk(["read:pages"], "Ablauf", days=1); TE = d["token"]
    cn = db(); cn.execute("UPDATE api_token SET expires=? WHERE prefix=?", (time.time() - 1, TE[:12])); cn.commit()
    st, d, _, _ = call("GET", "/api/v1/me", bearer=TE)
    ok("Abgelaufener Token -> 401 expired", st == 401 and d.get("code") == "expired", d)
    tid = next(x["id"] for x in call("GET", "/api/account/api-tokens", A)[1]["tokens"] if x["prefix"] == TR[:12])
    ok("Widerruf durch anderes Konto -> 404", call("DELETE", f"/api/account/api-tokens/{tid}", X)[0] == 404)
    ok("Widerrufen: sofort ungueltig", call("DELETE", f"/api/account/api-tokens/{tid}", A)[0] == 200 and call("GET", "/api/v1/me", bearer=TR)[0] == 401)
    n_act = sum(1 for x in call("GET", "/api/account/api-tokens", A)[1]["tokens"] if not x["revoked"] and not x["expired"])
    for i in range(10 - n_act):
        mk(["read:pages"], f"f{i}")
    ok("Hoechstens 10 aktive Tokens -> 409", mk(["read:pages"], "zuviel")[0] == 409)

    # --- Admin-Schalter --------------------------------------------------------------------------------------
    setting("api", {"enabled": False}); time.sleep(5.5)
    st, d, _, _ = call("GET", "/api/v1/me", bearer=T)
    ok("API aus -> 403 api_disabled", st == 403 and d.get("code") == "api_disabled", d)
    setting("api", {"enabled": True, "group": "nur-diese-gruppe"}); time.sleep(5.5)
    ok("Nur Gruppe X: Konto ausserhalb -> 403", call("GET", "/api/v1/me", bearer=T)[0] == 403 and call("GET", "/api/account/api-tokens", A)[1]["enabled"] is False)
    setting("api", None); time.sleep(5.5)
    ok("Wieder frei", call("GET", "/api/v1/me", bearer=T)[0] == 200)

    # --- Ueberall abmelden (Admin) / Konto gesperrt --------------------------------------------------------------
    st, d, _, _ = mk(["read:pages"], "Konto B", cookie=X); TB = d.get("token", "")
    ok("Konto B hat einen Token", call("GET", "/api/v1/me", bearer=TB)[0] == 200)
    cn = db(); cn.execute("UPDATE api_token SET revoked=? WHERE uid=? AND revoked IS NULL", (time.time(), UB)); cn.commit()   # = revoke_user_logins
    ok("Nach 'ueberall abmelden' ungueltig", call("GET", "/api/v1/me", bearer=TB)[0] == 401)

    # --- Admin-Bereich: Einstellungen (Anzeige + Speichern aller Felder der Seite), Konto-Seite, Widerruf ---------
    AD = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", ""))
    if AD.hostname:
        import hashlib, html as _html, http.client, re as _re, secrets
        sys.path.insert(0, os.getcwd())
        import auth as _auth
        SA = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest())
        if not _auth.get_user("api-adm"):
            _auth.create_user("api-adm", "Api-Admin-Pass-" + secrets.token_hex(6), "Api Admin", is_admin=True)
        jar = {"csrf": "api-csrf-" + secrets.token_hex(6)}
        jar["cookie"] = "hk_admin=" + SA.sign(base64.b64encode(json.dumps({"aid": "api-adm", "ep": time.time() + 5, "csrf": jar["csrf"]}).encode())).decode()

        def admin(path, form=None):
            c = http.client.HTTPConnection(AD.hostname, AD.port, timeout=60)
            h = {"Cookie": jar["cookie"], "Accept-Language": "de"}
            body = None
            if form is not None:
                body = urllib.parse.urlencode(list(form) + [("csrf", jar["csrf"])])
                h["Content-Type"] = "application/x-www-form-urlencoded"
            c.request("POST" if form is not None else "GET", path, body=body, headers=h)
            r = c.getresponse(); data = r.read().decode("utf-8", "replace"); loc = r.getheader("location")
            sc = r.getheader("set-cookie")
            if sc:
                jar["cookie"] = sc.split(";")[0]
            c.close()
            if r.status == 303 and loc:
                return admin(loc.split("#")[0])
            return r.status, data
        st, page = admin("/settings")
        ok("Admin: Einstellungen zeigen API-Abschnitt", st == 200 and 'name="api_enabled"' in page and 'name="api_mcp"' in page, st)
        # alle Felder der Einstellungs-Form (die mit api_enabled) wie ein Browser zusammenstellen
        fm = next(m.group(0) for m in _re.finditer(r'<form[^>]*method="post"[^>]*>.*?</form>', page, _re.S) if 'name="api_enabled"' in m.group(0))
        fields = []
        for m in _re.finditer(r'<input([^>]*)>', fm):
            a = dict(_re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            if not a.get("name") or a["name"] == "csrf" or a.get("type") == "password":
                continue
            if a.get("type") == "checkbox":
                if "checked" in m.group(1) or a["name"] == "api_unlimited":
                    fields.append((a["name"], a.get("value", "1")))
            else:
                v = _html.unescape(a.get("value", ""))
                fields.append((a["name"], "42" if a["name"] == "api_max_days" else v))
        for m in _re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', fm, _re.S):
            sel = _re.search(r'<option value="([^"]*)"[^>]*selected', m.group(2))
            if sel:
                fields.append((m.group(1), _html.unescape(sel.group(1))))
        for m in _re.finditer(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', fm, _re.S):
            fields.append((m.group(1), _html.unescape(m.group(2))))
        before = {k: v for k, v in json.loads(json.dumps(_auth.settings(fresh=True), default=str)).items() if k not in ("api",)}
        st, _p = admin("/settings", fields)
        after = {k: v for k, v in json.loads(json.dumps(_auth.settings(fresh=True), default=str)).items() if k not in ("api",)}
        sa = _auth.settings(fresh=True)["api"]
        ok("Admin: API-Einstellungen gespeichert (Laufzeit 42, unbegrenzt erlaubt), Rest unveraendert",
           st == 200 and sa["max_days"] == 42 and sa["unlimited"] is True and sa["enabled"] is True and before == after,
           (st, sa, [k for k in before if before[k] != after.get(k)]))
        setting("api", None)
        cn = db(); cn.execute("DELETE FROM api_token WHERE uid=?", (UA,)); cn.commit()
        time.sleep(5.5)
        T2 = mk(["read:pages"], "Admin-Widerruf")[1]["token"]
        uidA = _auth.get_user(UA)["id"]
        st, page = admin(f"/users/{uidA}")
        ok("Admin: Konto-Seite zeigt API-Tokens", st == 200 and "api_revoke" in page and "API-Tokens" in page, st)
        st, page = admin(f"/users/{uidA}", [("action", "api_revoke")])
        ok("Admin: alle API-Tokens des Kontos widerrufen", st == 200 and call("GET", "/api/v1/me", bearer=T2)[0] == 401)
        cn = db(); cn.execute("DELETE FROM users WHERE username='api-adm'"); cn.commit()
finally:
    cn = db()
    for q in ("DELETE FROM note_share WHERE owner_uid LIKE ?", "DELETE FROM note_comment WHERE uid LIKE ?", "DELETE FROM note WHERE uid LIKE ?",
              "DELETE FROM task WHERE uid LIKE ?", "DELETE FROM api_token WHERE uid LIKE ?", "DELETE FROM webhook WHERE uid LIKE ?",
              "DELETE FROM notification WHERE uid LIKE ?", "DELETE FROM file WHERE uid LIKE ?"):
        try:
            cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError:
            pass
    cn.execute("DELETE FROM setting WHERE k='api'")
    cn.execute("UPDATE users SET display_name=username WHERE username IN (?, ?)", (UA, UB))
    cn.commit()
print(f"\nAPI V1 {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
