"""MCP-Nachrunde (2.5, mcp2) gegen einen laufenden Test-Server.

Prueft: (1) Antworten, Fehler und gespeicherte Vorgaben (Status-Optionen, Ansichtsnamen) in der Sprache des Kontos, dem
der Token gehoert - auch bei fremdem/fehlendem Accept-Language; Konto "automatisch" wie bisher; /api/v1 ebenso.
(2) Ansichten mit Filter/Sortierung (create_database views, update_database_schema add_views/update_views) im Format des
Webs; Widget "Fortschritt" auf eine gefilterte Ansicht zaehlt nur deren Zeilen; klare 400 bei Unbekanntem.
(3) update_block replace_section mit leerem Markdown nur mit allow_empty. (4) update_event Termin <-> ganztaegig
(Radicale unter HK_TEST_CALDAV). (5) destructiveHint in tools/list."""
import base64, json, os, sqlite3, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
S = TimestampSigner(os.environ["SESSION_SECRET"]); HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
UA, UB = "hk-test-a", "hk-test-b"
A = S.sign(base64.b64encode(json.dumps({"uid": UA, "sec_at": int(time.time())}).encode())).decode()
X = S.sign(base64.b64encode(json.dumps({"uid": UB, "sec_at": int(time.time())}).encode())).decode()
RAD = os.environ.get("HK_TEST_CALDAV", "http://127.0.0.1:5232/"); RAD_HP = urllib.parse.urlparse(RAD).netloc


def call(m, p, cookie=None, b=None, bearer=None, headers=None, raw=None):
    h = {"Host": HOST, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if cookie:
        h["Cookie"] = "saga=" + cookie; h["X-Saga"] = "1"; h["Accept-Language"] = "de-DE"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    h.update(headers or {})
    data = raw if raw is not None else (json.dumps(b).encode() if b is not None else None)
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=data, method=m, headers=h), timeout=30) as r:
            body = r.read(); return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else {})
    except urllib.error.HTTPError as e:
        body = e.read(); return e.code, (json.loads(body) if body[:1] in (b"{", b"[") else {})


N = [0]


def rpc(tok, method, params=None, lang=None):
    N[0] += 1
    h = {"MCP-Protocol-Version": "2025-06-18"}
    if lang:
        h["Accept-Language"] = lang
    return call("POST", "/mcp", b={"jsonrpc": "2.0", "id": N[0], "method": method, "params": params or {}}, bearer=tok, headers=h)


def tool(tok, name, lang=None, **args):
    st, d = rpc(tok, "tools/call", {"name": name, "arguments": args}, lang)
    r = d.get("result") or {}
    return r.get("isError"), "".join(c.get("text", "") for c in r.get("content") or [])


def jbody(tx):
    try:
        return json.loads(tx.split('">\n', 1)[1].rsplit("\n</saga-data", 1)[0])
    except (IndexError, ValueError):
        return {}


def db():
    return sqlite3.connect(DB)


def setting(k, v):
    cn = db()
    if v is None:
        cn.execute("DELETE FROM setting WHERE k=?", (k,))
    else:
        cn.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?,?)", (k, json.dumps(v)))
    cn.commit()


def lang_of(uid):
    r = db().execute("SELECT v FROM user_kv WHERE uid=? AND k='lang'", (uid,)).fetchone()
    return r[0] if r else ""


def set_lang(cookie, lang):
    return call("PUT", "/api/account/lang", cookie, {"lang": lang})[0] == 200


def schema(dbid):
    return json.loads(db().execute("SELECT schema FROM note WHERE id=?", (dbid,)).fetchone()[0])


def nid_of(txt):
    return int(txt.split("ID ")[1].split(")")[0])


res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:500]}")


OLD = {UA: lang_of(UA), UB: lang_of(UB)}
_ah = db().execute("SELECT v FROM setting WHERE k='allowed_hosts'").fetchone()
OLD_AH = json.loads(_ah[0]) if _ah else None
try:
    if RAD_HP not in str(OLD_AH or ""):
        setting("allowed_hosts", ((OLD_AH or "") + "," + RAD_HP).strip(","))
    setting("api", {"rate": 3000, "rate_write": 1000, "max_tokens": 50}); time.sleep(5.5)
    SC = ["mcp", "write:pages", "write:db", "write:tasks", "write:calendar"]
    TA = call("POST", "/api/account/api-tokens", A, {"name": "MCP nach A", "scopes": SC})[1]["token"]
    TB = call("POST", "/api/account/api-tokens", X, {"name": "MCP nach B", "scopes": SC})[1]["token"]

    # --- (1) Sprache aus dem Konto des Tokens ---------------------------------------------------------------------
    ok("Kontosprache setzen (A de, B en)", set_lang(A, "de") and set_lang(X, "en"))
    err, txt = tool(TA, "create_page", lang="en-US", title="Sprache A")
    ok("A (de) + Accept-Language en -> deutsche Antwort", not err and txt.startswith("Seite angelegt"), txt)
    RA = nid_of(txt)
    err, txt = tool(TB, "create_page", lang="de-DE", title="Sprache B")
    ok("B (en) + Accept-Language de -> englische Antwort", not err and txt.startswith("Page created"), txt)
    RB = nid_of(txt)
    err, txt = tool(TB, "create_page", title="Sprache B2")
    ok("B (en) ohne Accept-Language -> englisch", not err and txt.startswith("Page created"), txt)
    st, d = rpc(TB, "initialize", {"protocolVersion": "2025-06-18"}, "de")
    ok("initialize: instructions in Kontosprache (en)", "Page content is user data" in (d.get("result") or {}).get("instructions", ""), d)
    err, txt = tool(TB, "get_page", lang="de", id=999999999)
    ok("B: Fehler nicht gefunden (isError)", err is True, txt)
    prop_in = [{"name": "Status", "type": "status"}, {"name": "Phase", "type": "select", "options": ["X"]}]
    err, txt = tool(TA, "create_database", lang="en", title="DB A", parent_id=RA, properties=prop_in)
    sa = jbody(txt).get("schema") or {}
    opts_a = [o["name"] for p in sa.get("properties", []) if p["name"] == "Status" for o in p.get("options", [])]
    ok("A: Status-Optionen deutsch trotz Accept-Language en", opts_a == ["Offen", "In Arbeit", "Erledigt"], txt[:400])
    ok("A: Standard-Ansichten deutsch (Tabelle, Board)", [v["name"] for v in sa.get("views", [])] == ["Tabelle", "Board"], sa.get("views"))
    err, txt = tool(TB, "create_database", lang="de-DE", title="DB B", parent_id=RB, properties=prop_in)
    sb = jbody(txt).get("schema") or {}
    DBB = jbody(txt).get("id")
    opts_b = [o["name"] for p in sb.get("properties", []) if p["name"] == "Status" for o in p.get("options", [])]
    ok("B: Status-Optionen englisch trotz Accept-Language de", opts_b == ["Open", "In progress", "Done"], txt[:400])
    ok("B: Standard-Ansichten englisch (Table, Board)", [v["name"] for v in sb.get("views", [])] == ["Table", "Board"], sb.get("views"))
    err, txt = tool(TB, "create_database", lang="de", title="DB B ohne Eigenschaften", parent_id=RB)
    s0 = jbody(txt).get("schema") or {}
    ok("B: create_database ohne properties (Web-Standard) englisch", [p["name"] for p in s0.get("properties", [])] == ["Status", "Date"]
       and [v["name"] for v in s0.get("views", [])] == ["Table", "Board"], s0)
    err, txt = tool(TB, "update_database_schema", lang="de", id=DBB, add_properties=[{"name": "Stand", "type": "status"}],
                    add_views=[{"type": "list"}])
    sb2 = jbody(txt).get("schema") or {}
    ok("B: update_database_schema: Vorgaben und Meldungen englisch", any(p["name"] == "Stand" and [o["name"] for o in p["options"]] == ["Open", "In progress", "Done"]
                                                                       for p in sb2.get("properties", []))
       and any(v["name"] == "List" for v in sb2.get("views", [])) and "created" in json.dumps(jbody(txt).get("done")), txt[:500])
    err, txt = tool(TB, "query_database", lang="de", id=DBB, filters=[{"property": "Gibtsnicht", "op": "is", "value": "x"}])
    ok("B: Fehlermeldung englisch (Unknown property)", err is True and "Unknown property" in txt, txt)
    err, txt = tool(TA, "query_database", lang="en", id=jbody(tool(TA, "list_databases", query="DB A")[1])[0]["id"],
                    filters=[{"property": "Gibtsnicht", "op": "is", "value": "x"}])
    ok("A: Fehlermeldung deutsch (Unbekannte Eigenschaft)", err is True and "Unbekannte Eigenschaft" in txt, txt)
    # /api/v1 mit demselben Token
    qbad = {"filters": [{"property": "Gibtsnicht", "op": "is", "value": "x"}]}
    st, d = call("POST", f"/api/v1/databases/{DBB}/query", bearer=TB, b=qbad, headers={"Accept-Language": "de"})
    ok("/api/v1 (B en, Accept-Language de): Fehlertext englisch", st == 400 and d.get("error") == "Unknown property: Gibtsnicht", d)
    st, d = call("POST", f"/api/v1/databases/{DBB}/rows", bearer=TB, b={"title": "x", "properties": {"Gibtsnicht": 1}}, headers={"Accept-Language": "es"})
    ok("/api/v1 Zeile anlegen (B en, Accept-Language es): Fehlertext englisch", st == 400 and "Gibtsnicht" in d.get("error", "")
       and d.get("error", "").isascii(), d)
    DBA = jbody(tool(TA, "list_databases", query="DB A")[1])[0]["id"]
    st, d = call("POST", f"/api/v1/databases/{DBA}/query", bearer=TA, b=qbad, headers={"Accept-Language": "en"})
    ok("/api/v1 (A de, Accept-Language en): Fehlertext deutsch", st == 400 and d.get("error") == "Unbekannte Eigenschaft: Gibtsnicht", d)
    TBN = call("POST", "/api/account/api-tokens", X, {"name": "B ohne Bereich", "scopes": ["read:pages"]})[1]["token"]
    st, d = call("POST", "/api/v1/pages", bearer=TBN, b={"title": "x"}, headers={"Accept-Language": "de"})
    ok("/api/v1: Bereich fehlt -> Meldung englisch", st == 403 and "lacks the scope" in d.get("error", ""), d)
    # Konto "automatisch": wie bisher (Accept-Language)
    ok("Kontosprache B automatisch", set_lang(X, ""))
    err, txt = tool(TB, "create_page", lang="es", title="Sprache B auto")
    ok("B automatisch + Accept-Language es -> spanisch (bisheriges Verhalten)", not err and txt.startswith("Página creada"), txt)
    err, txt = tool(TB, "create_page", lang="de", title="Sprache B auto2")
    ok("B automatisch + Accept-Language de -> deutsch", not err and txt.startswith("Seite angelegt"), txt)
    set_lang(X, "en")

    # --- (2) Ansichten mit Filter und Sortierung --------------------------------------------------------------
    err, txt = tool(TA, "create_database", title="Meilensteine", parent_id=RA,
                    properties=[{"name": "Meilenstein", "type": "select", "options": ["2.4", "2.5"]}, {"name": "Fertig", "type": "checkbox"},
                                {"name": "Status", "type": "status"}, {"name": "Fällig", "type": "date"}, {"name": "Punkte", "type": "number"}],
                    views=[{"type": "table", "name": "Alle"},
                           {"type": "table", "name": "2.5", "filters": [{"property": "Meilenstein", "op": "is", "value": "2.5"}],
                            "sorts": [{"property": "Fällig", "dir": "desc"}]}])
    D = jbody(txt).get("id")
    ok("create_database mit gefilterter Ansicht", not err and D, txt[:500])
    sch = schema(D)
    pm = next(p for p in sch["props"] if p["name"] == "Meilenstein"); pf = next(p for p in sch["props"] if p["name"] == "Fällig")
    o25 = next(o["id"] for o in pm["options"] if o["name"] == "2.5"); o24 = next(o["id"] for o in pm["options"] if o["name"] == "2.4")
    v25 = next(v for v in sch["views"] if v["name"] == "2.5")
    ok("Gespeichert wie im Web: {pid, op, value: Options-ID}, sorts {pid, dir}",
       v25.get("filters") == [{"pid": pm["id"], "op": "is", "value": o25}] and v25.get("sorts") == [{"pid": pf["id"], "dir": "desc"}], v25)
    vs = {v["name"]: v for v in (jbody(tool(TA, "get_database_schema", id=D)[1]).get("schema") or {}).get("views", [])}
    ok("get_database_schema zeigt Filter/Sortierung mit Namen", vs.get("2.5", {}).get("filters") == [{"property": "Meilenstein", "op": "is", "value": "2.5"}]
       and vs["2.5"].get("sorts") == [{"property": "Fällig", "dir": "desc"}] and "filters" not in vs.get("Alle", {}), vs)
    rows = [("A1", "2.5", True), ("A2", "2.5", True), ("A3", "2.5", False), ("B1", "2.4", True), ("B2", "2.4", True)]
    for i, (ti, ms, fe) in enumerate(rows):
        e_, t_ = tool(TA, "create_row", database_id=D, title=ti, properties={"Meilenstein": ms, "Fertig": fe, "Fällig": f"2026-11-0{i + 1}"})
        if e_:
            print("  create_row:", t_)
    err, txt = tool(TA, "query_database", id=D, view=v25["id"])
    qd = jbody(txt)
    ok("query_database mit der Ansicht: nur 2.5, sortiert absteigend", qd.get("matched") == 3 and [r["title"] for r in qd.get("rows", [])] == ["A3", "A2", "A1"], txt[:400])
    st, d = call("GET", f"/api/notes/{D}/rows?view={v25['id']}", A)
    ok("Web-Weg /rows?view=: dieselben 3 Zeilen", st == 200 and sorted(r["title"] for r in d.get("rows", [])) == ["A1", "A2", "A3"], str(d)[:300])
    err, txt = tool(TA, "create_page", title="Dashboard", parent_id=RA)
    P = nid_of(txt)
    err, txt = tool(TA, "add_widget", page_id=P, kind="progress", title="2.5 fertig", cfg={"src": "db", "db": D, "view": "2.5", "mode": "checked", "prop": "Fertig"})
    ok("add_widget Fortschritt auf Ansicht per Name", not err, txt)
    W = jbody(txt).get("block_id")

    def prog():
        w = next((x for x in jbody(tool(TA, "get_widgets", page_id=P)[1]).get("widgets", []) if x.get("block_id") == W), {})
        return (w.get("value") or {}).get("value"), (w.get("value") or {}).get("max"), w
    dn, tt, w = prog()
    ok("Widget zaehlt nur Zeilen der Ansicht 2.5 (2 von 3)", (dn, tt) == (2, 3), w)
    # Web speichert das Schema zurueck (wie beim Bearbeiten im Web): Filter bleiben unveraendert
    s_web = call("GET", f"/api/notes/{D}", A)[1]["schema"]
    st, _ = call("PATCH", f"/api/notes/{D}", A, {"schema": s_web})
    ok("Web speichert das Schema unveraendert zurueck (Filter bleiben)", st == 200 and next(v for v in schema(D)["views"] if v["id"] == v25["id"]).get("filters") == v25.get("filters") != None)  # noqa: E711
    # klare 400
    bad = [([{"property": "Gibtsnicht", "op": "is", "value": "2.5"}], "Unbekannte Eigenschaft"),
           ([{"property": "Meilenstein", "op": "is", "value": "9.9"}], "Option „9.9“"),
           ([{"property": "Meilenstein", "op": "gt", "value": "2"}], "Operator „gt“"),
           ([{"property": "Fertig", "op": "is", "value": "x"}], "Operator „is“"),
           ([{"property": "Meilenstein", "op": "is"}], "Wert fehlt"),
           ([{"property": "Fällig", "op": "eq", "value": "morgen"}], "JJJJ-MM-TT"),
           ([{"property": "Punkte", "op": "gt", "value": "viel"}], "Zahl"),
           ([{"property": "Status", "op": "status_group", "value": "fast"}], "status_group")]
    for flt, frag in bad:
        err, txt = tool(TA, "update_database_schema", id=D, add_views=[{"type": "table", "name": "kaputt", "filters": flt}])
        ok(f"add_views ungueltig -> 400 ({frag})", err is True and txt.startswith("bad_request") and frag in txt, txt)
    # Pruefrunde 1: Operatoren wie die Filterzeile im Web - pages.js gibt die relativen Filter nur "Erstellt am", nicht
    # "Zuletzt bearbeitet" (OPS.edited behaelt die 6 Grund-Operatoren)
    from saga import api_struct as AS
    ok("Operatoren wie im Web: edited ohne relative Filter, created mit", "yesterday" not in AS.ops_of({"type": "edited"})
       and len(AS.ops_of({"type": "edited"})) == 6 and "yesterday" in AS.ops_of({"type": "created"}) and "overdue" not in AS.ops_of({"type": "created"}))
    err, txt = tool(TA, "update_database_schema", id=D, add_views=[{"type": "table", "name": "x", "sorts": [{"property": "Nix"}]}])
    ok("sorts mit unbekannter Eigenschaft -> 400", err is True and "Unbekannte Eigenschaft" in txt, txt)
    ok("Fehler legen keine Ansicht an", len(schema(D)["views"]) == 2, [v["name"] for v in schema(D)["views"]])
    err, txt = tool(TA, "update_database_schema", id=D, add_views=[
        {"type": "board", "name": "Offen 2.4", "group_by": "Status", "filters": [{"property": "Meilenstein", "op": "is", "value": o24},
                                                                                {"property": "Status", "op": "status_group", "value": "open"},
                                                                                {"property": "Fertig", "op": "checked"},
                                                                                {"property": "Fällig", "op": "next_days", "value": 7},
                                                                                {"property": "title", "op": "contains", "value": "B"}]}])
    vo = next((v for v in schema(D)["views"] if v["name"] == "Offen 2.4"), {})
    ok("add_views: Option per ID, status_group, ohne Wert, Tage, Titel", not err and [f["op"] for f in vo.get("filters", [])] ==
       ["is", "status_group", "checked", "next_days", "contains"] and vo["filters"][0]["value"] == o24 and vo["filters"][2]["value"] == ""
       and vo["filters"][3]["value"] == "7" and vo["filters"][4]["pid"] == "title", vo)
    # bestehende Ansicht aendern
    err, txt = tool(TA, "update_database_schema", id=D, update_views=[{"view": "2.5", "name": "Nur 2.4",
                                                                        "filters": [{"property": "Meilenstein", "op": "is", "value": "2.4"}], "sorts": []}])
    v2 = next(v for v in schema(D)["views"] if v["id"] == v25["id"])
    ok("update_views: Name, Filter ersetzt, Sortierung entfernt", not err and v2["name"] == "Nur 2.4" and v2.get("filters") == [{"pid": pm["id"], "op": "is", "value": o24}]
       and "sorts" not in v2, (txt[:300], v2))
    ok("Widget folgt der geaenderten Ansicht (2 von 2)", prog()[:2] == (2, 2), prog()[2])
    err, txt = tool(TA, "update_database_schema", id=D, update_views=[{"id": v25["id"], "filters": []}])
    ok("update_views filters [] -> Filter weg, Widget 4 von 5", not err and "filters" not in next(v for v in schema(D)["views"] if v["id"] == v25["id"])
       and prog()[:2] == (4, 5), prog()[2])
    err, txt = tool(TA, "update_database_schema", id=D, update_views=[{"view": "Gibtsnicht", "name": "x"}])
    ok("update_views unbekannte Ansicht -> 400", err is True and "nicht gefunden" in txt, txt)
    err, txt = tool(TA, "update_database_schema", id=D, update_views=[{"view": "Alle"}])
    ok("update_views ohne Aenderung -> 400", err is True and "nichts zu ändern" in txt, txt)
    err, txt = tool(TA, "update_database_schema", id=D, update_views=[{"view": "Offen 2.4", "group_by": None}])
    ok("update_views: Board ohne group_by -> 400", err is True and "Board" in txt, txt)
    ok("update_views loescht keine Ansicht", len(schema(D)["views"]) == 3)
    # query_database: status_group-Wert bleibt (nicht auf Option "Done"/"Open" abgebildet)
    err, txt = tool(TB, "create_row", database_id=DBB, title="offen", properties={"Status": "Open"})
    err, txt = tool(TB, "create_row", database_id=DBB, title="fertig", properties={"Status": "Done"})
    err, txt = tool(TB, "query_database", id=DBB, filters=[{"property": "Status", "op": "status_group", "value": "done"}])
    ok("query_database status_group done (Option heisst Done) -> 1 Zeile", not err and jbody(txt).get("matched") == 1, txt[:300])

    # --- (3) F5: replace_section mit leerem Markdown --------------------------------------------------------------
    err, txt = tool(TA, "create_page", title="Abschnitte", parent_id=RA, markdown="## Plan\n\nPunkt eins\n\n## Danach\n\nrest")
    PG = nid_of(txt)

    def md(nid):
        return tool(TA, "get_page", id=nid)[1]
    for args, name in (({"markdown": ""}, "leer"), ({}, "fehlt"), ({"markdown": "   \n"}, "nur Leerraum")):
        err, txt = tool(TA, "update_block", id=PG, heading="Plan", mode="replace_section", **args)
        ok(f"replace_section Markdown {name} -> 400 mit Hinweis allow_empty", err is True and "allow_empty" in txt, txt)
    err, txt = tool(TA, "update_block", id=PG, heading="Plan", markdown="")
    ok("heading ohne mode (Standard replace_section) leer -> 400", err is True and "allow_empty" in txt, txt)
    ok("Abschnitt unveraendert", "Punkt eins" in md(PG))
    err, txt = tool(TA, "update_block", id=PG, heading="Plan", mode="replace_section", markdown="", allow_empty="ja")
    ok("allow_empty kein bool -> 400", err is True and "allow_empty" in txt, txt)
    err, txt = tool(TA, "update_block", id=PG, heading="Plan", mode="replace_section", markdown="", allow_empty=True)
    m = md(PG)
    ok("allow_empty: true leert den Abschnitt, Ueberschrift und Rest bleiben", not err and "Punkt eins" not in m and "## Plan" in m and "rest" in m, m[-200:])
    st, d = call("POST", f"/api/v1/pages/{PG}/blocks", bearer=TA, b={"heading": "Danach", "mode": "replace_section", "markdown": ""})
    ok("/api/v1 blocks: leer ohne allow_empty -> 400", st == 400 and "allow_empty" in d.get("error", ""), d)
    st, d = call("POST", f"/api/v1/pages/{PG}/blocks", bearer=TA, b={"heading": "Danach", "mode": "replace_section", "markdown": "", "allow_empty": True})
    ok("/api/v1 blocks: mit allow_empty -> 200", st == 200 and "rest" not in md(PG), d)

    # --- (5) destructiveHint ----------------------------------------------------------------------------------
    tl = {x["name"]: x["annotations"] for x in rpc(TA, "tools/list")[1]["result"]["tools"]}
    ok("tools/list: update_widget, update_row, update_database_schema destruktiv", all(tl[n]["destructiveHint"] is True for n in
                                                                                      ("update_widget", "update_row", "update_database_schema")), tl.get("update_widget"))
    ok("tools/list: anlegen/anfuegen/Titel/Kaestchen nicht destruktiv", not any(tl[n]["destructiveHint"] for n in
                                                                                 ("create_page", "append_to_page", "add_widget", "update_page_title", "set_checkbox", "create_row")))

    # --- (4) F6: update_event Termin <-> ganztaegig -----------------------------------------------------------
    try:
        import httpx
        cal_up = httpx.request("PROPFIND", RAD + "test/", auth=("test", "geheim-app-pw-123"), headers={"Depth": "0"}, timeout=5).status_code in (207, 200)
    except Exception:
        cal_up = False
    if not cal_up:
        print("SKIP Kalender (kein CalDAV unter", RAD, ")"); res.append(False)
    else:
        AUTH = ("test", "geheim-app-pw-123")
        MK = '<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop><d:displayname>MCP Nach</d:displayname></d:prop></d:set></c:mkcalendar>'
        httpx.request("DELETE", f"{RAD}test/mcpnach/", auth=AUTH)
        httpx.request("MKCALENDAR", f"{RAD}test/mcpnach/", auth=AUTH, content=MK)
        found = call("POST", "/api/settings/calendars/test", A, {"base_url": RAD, "username": "test", "password": AUTH[1]})[1].get("calendars") or []
        sel = [dict(c, show=True) for c in found if "mcpnach" in c["href"]]
        call("POST", "/api/settings/calendars", A, {"base_url": RAD, "username": "test", "password": AUTH[1], "label": "Nach", "calendars": sel})
        K = next(c["calendar"] for c in jbody(tool(TA, "list_calendars")[1]).get("calendars", []) if c["name"] == "MCP Nach")
        TZ = "Europe/Berlin"

        def ev(summary):
            return next((e for e in jbody(tool(TA, "list_events", start="2026-10-20", end="2026-11-10", calendar=K, timezone=TZ, fresh=True)[1]).get("events", [])
                         if e["summary"] == summary), None)

        def mk(summary, **kw):
            e_, t_ = tool(TA, "create_event", calendar=K, summary=summary, timezone=TZ, **kw)
            return ev(summary)
        e = mk("Kurz", start="2026-10-30T10:00", end="2026-10-30T11:00")
        err, txt = tool(TA, "update_event", event=e["event"], allday=True, start="2026-10-27")
        e2 = ev("Kurz")
        ok("Termin -> ganztaegig mit neuem start: 1 Tag (altes Ende nicht behalten)", not err and e2 and e2["allday"] and (e2["start"], e2["end"]) == ("2026-10-27", "2026-10-28"), (txt, e2))
        e = mk("Nacht", start="2026-10-30T22:00", end="2026-10-31T01:00")
        err, txt = tool(TA, "update_event", event=e["event"], allday=True, timezone=TZ)
        e2 = ev("Nacht")
        ok("Termin ueber Mitternacht -> ganztaegig ohne start: beide Tage", not err and e2 and (e2["start"], e2["end"]) == ("2026-10-30", "2026-11-01"), (txt, e2))
        err, txt = tool(TA, "update_event", event=e2["event"], allday=True, start="2026-11-03")
        e3 = ev("Nacht")
        ok("ganztaegig verschieben behaelt 2 Tage", not err and (e3["start"], e3["end"]) == ("2026-11-03", "2026-11-05"), (txt, e3))
        e = mk("Tag", start="2026-10-28", allday=True)
        err, txt = tool(TA, "update_event", event=e["event"], start="2026-10-28T10:00", timezone=TZ)
        e2 = ev("Tag")
        ok("ganztaegig + start mit Uhrzeit (ohne allday) -> Termin 10:00-11:00", not err and e2 and not e2["allday"]
           and e2["start"].startswith("2026-10-28T10:00") and e2["end"].startswith("2026-10-28T11:00"), (txt, e2))
        e = mk("Tag2", start="2026-10-29", allday=True)
        err, txt = tool(TA, "update_event", event=e["event"], allday=False)
        ok("ganztaegig -> allday:false ohne start -> 400", err is True and "start" in txt and ev("Tag2")["allday"], txt)
        err, txt = tool(TA, "update_event", event=e["event"], allday=False, start="2026-10-29")
        ok("allday:false mit Datum ohne Uhrzeit -> 400", err is True and "Uhrzeit" in txt, txt)
        err, txt = tool(TA, "update_event", event=e["event"], allday=False, start="2026-10-29T08:30", end="2026-10-29T09:15", timezone="America/New_York")
        e2 = next((x for x in jbody(tool(TA, "list_events", start="2026-10-28", end="2026-10-31", calendar=K, timezone="America/New_York", fresh=True)[1]).get("events", [])
                   if x["summary"] == "Tag2"), None)
        ok("ganztaegig -> Termin mit Zeitzone wie create_event", not err and e2 and not e2["allday"] and e2["start"].startswith("2026-10-29T08:30")
           and e2["end"].startswith("2026-10-29T09:15"), (txt, e2))
        e = mk("Zeit", start="2026-10-26T09:00", end="2026-10-26T09:30")
        err, txt = tool(TA, "update_event", event=e["event"], start="2026-10-27")
        ok("Termin + start nur Datum (ohne allday) -> 400, Uhrzeit nicht still verworfen", err is True and "allday" in txt and not ev("Zeit")["allday"], txt)
        err, txt = tool(TA, "update_event", event=e["event"], start="2026-10-27T09:00", end="2026-10-27")
        ok("start mit Uhrzeit, end nur Datum -> 400", err is True, txt)
        err, txt = tool(TA, "update_event", event=e["event"], start="2026-10-27T14:00")
        e2 = ev("Zeit")
        ok("Termin verschieben behaelt Dauer (unveraendert)", not err and e2["start"].startswith("2026-10-27T14:00") and e2["end"].startswith("2026-10-27T14:30"), (txt, e2))
        httpx.request("DELETE", f"{RAD}test/mcpnach/", auth=AUTH)
finally:
    for u, c_ in ((UA, A), (UB, X)):
        set_lang(c_, OLD[u])
    cn = db()
    for q in ("DELETE FROM note WHERE uid LIKE ?", "DELETE FROM api_token WHERE uid LIKE ?", "DELETE FROM cal_account WHERE uid LIKE ?"):
        try:
            cn.execute(q, ("hk-test-%",))
        except sqlite3.OperationalError:
            pass
    cn.execute("DELETE FROM setting WHERE k='api'")
    if OLD_AH is None:
        cn.execute("DELETE FROM setting WHERE k='allowed_hosts'")
    else:
        cn.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?,?)", ("allowed_hosts", json.dumps(OLD_AH)))
    cn.commit()
print(f"\nMCP-Nach {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
