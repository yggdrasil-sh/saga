"""2.5 Paket 1 Etappe 1: Formel- und Rollup-Eigenschaften (saga/formula.py, saga/dbcalc.py).

1. Parser/Auswertung gegen tests/vectors/formula.json (Notion-Beispiele, Typinferenz, Zeitzone, Fehlerpositionen) und
   Sicherheitsfaelle: Tiefe, Knotenzahl, Laenge, 9^9^9, repeat-Bombe, Ketten/Zyklen, Schrittbudget, Zeitbudget, kein
   eval (Python-Namen wie __import__ sind unbekannt).
2. Rechenkern: dbcalc.aggregate = bisheriges page_widgets.aggregate (widget_vectors), reduce fuer alle Rollup-Rechnungen,
   Gruppen nach Formel (value_groups) gleich in Python und static/pages.js (Node, aus dem Web-Code geloest), Wochentage.
3. HTTP gegen den Server: Schema mit Formel speichern (rtype vom Server, 400 mit Position bei Fehlern, Werte nie
   gespeichert), POST .../formula/check (Namen <-> IDs, Vorschau, Rechte), /rows mit Werten, fx_err, Filter/Sortierung/
   Gruppen nach Formel (identisch zum Web: Referenz ueber dbquery mit denselben Zeilen), PATCH-Antwort calc,
   Einzelzeile, Export (Markdown), Rollups mit allen Rechnungen.
4. Rollup-Rechte (Leck-Test): geteilte Datenbank mit Rollup auf eine NICHT geteilte Ziel-Datenbank -> kein Wert, kein
   Titel; Ziel-Zeilen nur, soweit sichtbar (archiviert/fremd). Veroeffentlichen: Rollup nur ueber oeffentliche Seiten.
5. Foederation (ohne zweite Instanz): Antwort fuer aeltere Gegenstellen (dbcalc.legacy_*), fx_orig kommt beim Speichern
   zurueck (_clean_schema), fx_err/Gruppen-Filter des Heims (federation.fx_err_in, fed_pages._group_in).
Wegwerf-Konten fx-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64, http.client, json, os, subprocess, sys, time, urllib.parse
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from itsdangerous import TimestampSigner
import auth
from db import connect
from saga import dbcalc, dbquery, formula as fx
from saga.i18n import set_lang

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
A, BU, CU = "fx-anna", "fx-bert", "fx-carl"
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=None, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    raw = None
    if body is not None:
        h["Content-Type"] = "application/json"
        raw = json.dumps(body)
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse()
    data = r.read()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data.decode("utf-8", "replace")


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


# =====================================================================================================
# 1. Parser und Auswertung
# =====================================================================================================
NOW = datetime(2026, 10, 27, 9, 30, tzinfo=timezone.utc)
BER = ZoneInfo("Europe/Berlin")


def eval_case(v, c):
    sch = {"props": [dict(p) for p in v["schema"]] + [{"id": "fx", "name": "F", "type": "formula", "expr": ""}]}
    try:
        cm, ids = fx.check(sch, "fx", c["expr"], names=c.get("names", False))
    except fx.FxSyntax as e:
        return {"error": e.code, "pos": e.pos}
    sch["props"][-1]["expr"] = ids
    row = json.loads(json.dumps(v["row"]))
    row["id"] = 1
    dbcalc.fill_core(sch, [row], tz=BER, now=NOW, lang="de")
    return {"rtype": cm.rtype, "value": row["props"].get("fx"), "runtime": (row.get("fx_err") or {}).get("fx"), "ids": ids}


def vectors():
    set_lang("de")
    v = json.load(open(os.path.join(HERE, "vectors", "formula.json"), encoding="utf-8"))
    bad = []
    for c in v["cases"]:
        got = eval_case(v, c)
        if "error" in c:
            good = got.get("error") == c["error"] and got.get("pos") == c.get("pos")
        elif "error" in got:
            good = False
        elif "runtime" in c:
            good = got["rtype"] == c["rtype"] and got["runtime"] == c["runtime"] and got["value"] is None
        else:
            good = got["rtype"] == c["rtype"] and got["runtime"] is None and got["value"] == c["value"] \
                and type(got["value"]) is type(c["value"]) and (not c.get("ids") or got["ids"] == c["ids"])
        if not good:
            bad.append((c, got))
    ok(f"Vektoren: {len(v['cases'])} Faelle (Notion-Beispiele, Typen, Zeitzone, Fehler) = saga.formula", not bad, bad[:3])
    ok("Vektoren decken Syntaxfehler, Laufzeitfehler und alle Ergebnistypen ab",
       sum("error" in c for c in v["cases"]) >= 8 and sum("runtime" in c for c in v["cases"]) >= 8
       and {c.get("rtype") for c in v["cases"]} >= set(fx.RTYPES))


def budgets():
    sch = {"props": [{"id": "n", "name": "N", "type": "number"}, {"id": "t", "name": "T", "type": "text"}]}

    def err(expr):
        try:
            fx.check(sch, "f", expr)
        except fx.FxSyntax as e:
            return e
        return None
    e = err("(" * 40 + "1" + ")" * 40)
    ok("Tiefe > 32 -> Fehler beim Speichern", e is not None and "verschachtelt" in e.msg, e and e.msg)
    e = err("+".join(["1"] * 450))
    ok("> 400 Knoten -> Fehler", e is not None and "groß" in e.msg, e and e.msg)
    e = err("1+" * 1000 + "1")
    ok("> 2000 Zeichen -> Fehler", e is not None and "lang" in e.msg, e and e.msg)
    for bad in ("__import__(\"os\")", "eval(\"1\")", "prop(\"n\").__class__", "exec(\"1\")", "open(\"x\")", "[].__len__()", "lambda: 1"):
        ok(f"kein Python: {bad!r} -> Syntaxfehler", err(bad) is not None)

    def run(expr, props=None):
        s = {"props": sch["props"] + [{"id": "f", "name": "F", "type": "formula", "expr": expr}]}
        rows = [{"id": 1, "title": "x", "props": dict(props or {})}]
        t0 = time.monotonic()
        dbcalc.fill_core(s, rows, tz=BER, now=NOW)
        return rows[0], time.monotonic() - t0
    r, dt = run("9^9^9")
    ok(f"9^9^9 -> num, schnell ({dt:.3f} s)", r.get("fx_err", {}).get("f") == "num" and dt < 0.5)
    r, dt = run("repeat(repeat(\"x\", 5000), 5000)")
    ok(f"repeat-Bombe -> len ({dt:.3f} s)", r.get("fx_err", {}).get("f") == "len" and dt < 0.5)
    r, dt = run("length(join(split(repeat(\"a,\", 4999), \",\"), \",\"))")
    ok(f"Listen bis 1000 Eintraege, sonst len ({dt:.3f} s)", r.get("fx_err", {}).get("f") == "len", r)
    big = "concat(" + ",".join(["repeat(\"ab\", 4000)"] * 3) + ")"
    r, dt = run(big)
    ok("Text > 10 000 Zeichen -> len bzw. budget (Schritte nach Laenge)", r.get("fx_err", {}).get("f") in ("len", "budget") and "f" not in r["props"], r)
    small = "+".join(["length(split(repeat(\"a,\", 999), \",\"))"] * 5)
    r, dt = run(small)
    ok("Schrittbudget: normale Formel mit Listen rechnet (5 x 1000 Eintraege)", r["props"].get("f") == 5000, r)
    heavy = "+".join(["length(split(repeat(\"a,\", 999), \",\"))"] * 30)
    r, dt = run(heavy)
    ok(f"Schrittbudget je Zeile ueberschritten -> budget ({len(heavy)} Zeichen, {dt:.3f} s)", r.get("fx_err", {}).get("f") == "budget" and dt < 0.5, r)
    # Ketten und Zyklen
    chain = [{"id": f"f{i}", "name": f"F{i}", "type": "formula", "expr": f"prop(\"f{i - 1}\") + 1" if i else "1"} for i in range(12)]
    fx.annotate(chain)
    rows = [{"id": 1, "title": "", "props": {}}]
    dbcalc.fill_core({"props": chain}, rows, tz=BER, now=NOW)
    p = rows[0]["props"]
    ok("Formel->Formel-Kette: bis 8 Glieder gerechnet, danach Fehler 'cycle'",
       p.get("f7") == 8 and "f8" not in p and rows[0]["fx_err"].get("f8") == "cycle" and rows[0]["fx_err"].get("f11") in ("cycle", "ref"), rows[0])
    cyc = [{"id": "a", "name": "A", "type": "formula", "expr": "prop(\"b\") + 1"}, {"id": "b", "name": "B", "type": "formula", "expr": "prop(\"a\") + 1"},
           {"id": "c", "name": "C", "type": "formula", "expr": "2"}]
    rows = [{"id": 1, "title": "", "props": {}}]
    dbcalc.fill_core({"props": cyc}, rows, tz=BER, now=NOW)
    ok("Zyklus a<->b: beide Fehler 'cycle', unabhaengige Formel rechnet", rows[0]["fx_err"] == {"a": "cycle", "b": "cycle"} and rows[0]["props"]["c"] == 2, rows[0])
    try:
        fx.check({"props": cyc[:1] + [{"id": "b", "name": "B", "type": "number"}]}, "b", "prop(\"a\")")
        e = None
    except fx.FxSyntax as ex:
        e = ex
    ok("Editor: neue Formel, die einen Kreis schliesst -> Fehler", e is not None and e.code == "cycle", e)
    try:
        fx.check({"props": [{"id": "s", "name": "S", "type": "formula", "expr": ""}]}, "s", "prop(\"s\") + 1")
        e = None
    except fx.FxSyntax as ex:
        e = ex
    ok("Editor: Formel auf sich selbst -> Fehler", e is not None and e.code == "cycle", e)
    # Zeitbudget je Anfrage: 5000 Zeilen x Formeln, abgelaufene Frist -> restliche Werte leer + time
    s = {"props": sch["props"] + [{"id": "f", "name": "F", "type": "formula", "expr": "prop(\"n\") * 2"}]}
    fx.annotate(s["props"])
    rows = [{"id": i, "title": "", "props": {"n": i}} for i in range(5000)]
    t0 = time.monotonic()
    dbcalc.fill_core(s, rows, tz=BER, now=NOW)
    dt = time.monotonic() - t0
    ok(f"5000 Zeilen in {dt:.2f} s gerechnet", rows[-1]["props"]["f"] == 9998 and dt < 2.0)
    rows = [{"id": i, "title": "", "props": {"n": i}} for i in range(50)]
    dbcalc.fill_core(s, rows, tz=BER, now=NOW, deadline=time.monotonic() - 1)
    ok("Zeitbudget abgelaufen -> Werte leer, fx_err 'time'", all("f" not in r["props"] and r["fx_err"] == {"f": "time"} for r in rows))
    # Gespeicherte Werte werden nie uebernommen (Faelschen ueber props)
    rows = [{"id": 1, "title": "", "props": {"n": 2, "f": 999}}]
    dbcalc.fill_core(s, rows, tz=BER, now=NOW)
    ok("Wert in props wird beim Lesen immer neu gerechnet (nicht faelschbar)", rows[0]["props"]["f"] == 4)


# =====================================================================================================
# 2. Rechenkern
# =====================================================================================================
def core():
    v = json.load(open(os.path.join(HERE, "widget_vectors.json"), encoding="utf-8"))
    bad = []
    for cse in v["cases"]:
        try:
            got = dbcalc.aggregate(cse["op"], cse["prop"], v["sets"][cse["set"]], v["schema"])
        except dbcalc.Fail as f:
            got = {"error": f.code}
        if got != cse["want"]:
            bad.append((cse, got))
    ok(f"dbcalc.aggregate = Fusszeile/Widgets ({len(v['cases'])} Vektoren aus static/pages.js)", not bad, bad[:3])
    from saga import page_widgets
    ok("page_widgets.aggregate ruft den Rechenkern (gleiches Ergebnis)",
       page_widgets.aggregate("sum", "n", [{"props": {"n": 1}}, {"props": {"n": 2.555}}], {"props": [{"id": "n", "type": "number"}]}) == 3.56)
    vals = [3, "4", None, "", True, ["a", "b"], "2026-10-01", "2026-09-30", 1.005]
    got = {op: dbcalc.reduce(op, vals) for op in fx.ROLLUP_FNS if op != "show"}
    exp = {"count": 9, "filled": 7, "empty": 2, "pct_filled": 78, "unique": 8, "sum": 8, "avg": 2.67, "median": 3, "min": 1, "max": 4,
           "range": 3, "checked": 1, "unchecked": 8, "pct_checked": 11, "earliest": "2026-09-30", "latest": "2026-10-01"}
    ok("reduce: alle Rollup-Rechnungen (wie Fusszeile calcValue)", got == exp, {k: (got[k], exp[k]) for k in exp if got[k] != exp[k]})
    ok("reduce leer: count 0, sum/min/earliest leer", dbcalc.reduce("count", []) == 0 and dbcalc.reduce("sum", []) is None
       and dbcalc.reduce("earliest", []) is None and dbcalc.reduce("pct_checked", []) is None)
    # Gruppen nach Formel: Python = Web (Funktionen aus static/pages.js)
    cases = [
        ("text", ["Mittwoch", "Montag", None, "Sonntag", "Montag", ""]),
        ("text", ["b", "a", "B", "ä", "a"]),
        ("text", ["Montag", "Feierabend"]),
        ("number", [3, 1, 2.5, None, 1, "x", 10]),
        ("checkbox", [True, None, False, True]),
        ("date", ["2026-10-02", "2026-01-01", None, "2026-10-02"]),
        ("text", ["Mon", "Tue", "Sun", "Wed"]),
        ("text", ["toString", "constructor", "x"]),
    ]
    py_out = []
    for rt, vals in cases:
        rows = [{"id": i, "props": {} if x is None else {"g": x}} for i, x in enumerate(vals)]
        gs = dbquery.value_groups(rows, {"id": "g", "rtype": rt}, lambda r: r["props"].get("g", dbquery.UNDEF))
        py_out.append([[None if v is dbquery.UNDEF else v, [r["id"] for r in rs]] for v, rs in gs])
    js = node_value_groups(cases)
    ok("Gruppen nach Formel: Python (dbquery.value_groups) = Web (valueGroups) in 8 Faellen", js == py_out, (js, py_out))
    ok("Gruppen nach Wochentag in fester Reihenfolge (Mo..So), Leere zuerst",
       py_out[0] == [[None, [2, 5]], ["Montag", [1, 4]], ["Mittwoch", [0]], ["Sonntag", [3]]], py_out[0])
    # Wochentage in Python und Web gleich
    src = open(os.path.join(ROOT, "static", "pages.js"), encoding="utf-8").read()
    names = [n for lang in fx.WEEKDAYS.values() for part in lang for n in part]
    ok("Wochentagsnamen aller Sprachen auch in static/pages.js (weekdayIndex)", all(("'" + n + "'") in src for n in names))


def node_value_groups(cases):
    src = open(os.path.join(ROOT, "static", "pages.js"), encoding="utf-8").read()

    def fn(name):
        i = src.index("function " + name + "(")
        j, depth, q = src.index("{", i), 0, None
        while True:
            ch = src[j]
            if q:
                if ch == "\\":
                    j += 1
                elif ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[i:j + 1]
            j += 1
    code = fn("weekdayIndex") + "\n" + fn("valueGroups") + "\n" + """
const cases = JSON.parse(process.argv[1]);
const out = cases.map(function (c) {
  const rows = c[1].map(function (x, i) { const p = {}; if (x !== null) p.g = x; return { id: i, props: p }; });
  return valueGroups(rows, { id: 'g', rtype: c[0] }).map(function (g) { return [g.none ? null : g.v, g.rows.map(function (r) { return r.id; })]; });
});
console.log(JSON.stringify(out));"""
    r = subprocess.run(["node", "-e", code, json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if r.returncode:
        print(r.stderr[-2000:])
        return None
    return json.loads(r.stdout)


# =====================================================================================================
# 3./4. HTTP
# =====================================================================================================
def mkdb(u, title, props):
    s, d = req("POST", "/api/notes", u, {"title": title, "kind": "database"})
    nid = d["id"]
    s, d = req("PATCH", f"/api/notes/{nid}", u, {"schema": {"props": props}})
    return nid, s, d


def row(u, db, title, props=None):
    return req("POST", "/api/notes", u, {"title": title, "parent_id": db, "props": props or {}})[1]["id"]


def http_part():
    # Projekte (Ziel) und Aufgaben (Quelle mit Relation, Formeln, Rollup)
    proj, s, _ = mkdb(A, "Projekte", [{"id": "budget", "name": "Budget", "type": "number"}, {"id": "fertig", "name": "Fertig", "type": "checkbox"},
                                     {"id": "start", "name": "Start", "type": "date"}])
    p1 = row(A, proj, "Alpha", {"budget": 100, "fertig": True, "start": "2026-10-01"})
    p2 = row(A, proj, "Beta", {"budget": 50, "start": "2026-09-15"})
    p3 = row(A, proj, "Archiviert", {"budget": 1000})
    req("PATCH", f"/api/notes/{p3}", A, {"archived": True})
    with connect() as c, c:
        c.execute("UPDATE note SET archived=? WHERE id=?", (time.time(), p3))
    props = [{"id": "menge", "name": "Menge", "type": "number"}, {"id": "preis", "name": "Preis", "type": "number"},
             {"id": "d", "name": "Datum", "type": "date"},
             {"id": "proj", "name": "Projekt", "type": "relation", "target": proj},
             {"id": "summe", "name": "Summe", "type": "formula", "expr": "if(prop(\"menge\") > 0, prop(\"preis\") * prop(\"menge\"), 0)"},
             {"id": "tag", "name": "Tag", "type": "formula", "expr": "formatDate(prop(\"d\"), \"dddd\")"},
             {"id": "gross", "name": "Groß", "type": "formula", "expr": "prop(\"summe\") > 10"},
             {"id": "div", "name": "Div", "type": "formula", "expr": "prop(\"preis\") / prop(\"menge\")"},
             {"id": "pbud", "name": "Projektbudget", "type": "rollup", "rel": "proj", "target": "budget", "fn": "sum"},
             {"id": "pnam", "name": "Projektnamen", "type": "rollup", "rel": "proj", "target": "title", "fn": "show"},
             {"id": "pfer", "name": "Projekte fertig", "type": "rollup", "rel": "proj", "target": "fertig", "fn": "pct_checked"},
             {"id": "pst", "name": "Frühester Start", "type": "rollup", "rel": "proj", "target": "start", "fn": "earliest"}]
    db, s, d = mkdb(A, "Aufgaben", props)
    ok(f"Schema mit Formeln und Rollups gespeichert ({s})", s == 200, d)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1", A)
    rt = {p["id"]: p.get("rtype") for p in d["schema"]["props"]}
    ok("rtype setzt der Server (Typinferenz)", rt["summe"] == "number" and rt["tag"] == "text" and rt["gross"] == "checkbox"
       and rt["pbud"] == "number" and rt["pnam"] == "list" and rt["pst"] == "date", rt)
    r1 = row(A, db, "Mo-Aufgabe", {"menge": 3, "preis": 2.5, "d": "2026-10-26", "proj": [p1, p2, p3], "summe": 999})
    r2 = row(A, db, "Mi-Aufgabe", {"menge": 0, "preis": 4, "d": "2026-10-28", "proj": [p2]})
    r3 = row(A, db, "Ohne", {"preis": 20, "menge": 2})
    with connect() as c:
        stored = json.loads(c.execute("SELECT props FROM note WHERE id=?", (r1,)).fetchone()[0])
    ok("Formel-/Rollup-Werte werden nie gespeichert (auch nicht, wenn mitgeschickt)", "summe" not in stored and "pbud" not in stored, stored)
    s, d = req("GET", f"/api/notes/{db}/rows?tz=Europe/Berlin", A)
    by = {r["id"]: r for r in d["rows"]}
    a1 = by[r1]["props"]
    ok("Werte in /rows: Formel, Wochentag, Formel->Formel", a1.get("summe") == 7.5 and a1.get("tag") == "Montag" and a1.get("gross") is False
       and by[r3]["props"].get("summe") == 40 and by[r3]["props"].get("gross") is True, a1)
    ok("Rollups: Summe/Original/% angehakt/Fruehestes - archiviertes Ziel zaehlt nicht",
       a1.get("pbud") == 150 and a1.get("pnam") == ["Alpha", "Beta"] and a1.get("pfer") == 50 and a1.get("pst") == "2026-09-15", a1)
    ok("Laufzeitfehler je Zelle: fx_err {pid: code}, Wert leer", by[r2].get("fx_err") == {"div": "num"} and "div" not in by[r2]["props"], by[r2])
    ok("Zeile ohne Fehler hat kein fx_err", "fx_err" not in by[r1])
    # Einzelzeile
    s, d = req("GET", f"/api/notes/{r1}", A)
    ok(f"Einzelzeile (GET /api/notes/{{zeile}}) mit Formel-/Rollup-Werten ({s})", s == 200 and d["props"].get("summe") == 7.5 and d["props"].get("pbud") == 150, d.get("props"))
    # PATCH-Antwort
    s, d = req("PATCH", f"/api/notes/{r1}", A, {"props": {"menge": 4}})
    ok(f"PATCH einer Zeile antwortet mit calc (alle Formeln/Rollups) und fx_err ({s})",
       s == 200 and d.get("calc", {}).get("summe") == 10 and d["calc"].get("pbud") == 150 and d.get("fx_err") == {}, d)
    s, d = req("PATCH", f"/api/notes/{r2}", A, {"title": "Mi-Aufgabe!"})
    ok("PATCH (Titel) einer Zeile mit Fehler: fx_err im calc-Teil", s == 200 and d.get("fx_err") == {"div": "num"}, d)
    # Filter / Sortierung / Gruppen auf Formeln - Referenz: dbquery ueber dieselben Zeilen (wie das Web)
    s, full = req("GET", f"/api/notes/{db}/rows", A)
    for name, args in (("Filter Formel > 5", {"filters": [{"pid": "summe", "op": "gt", "value": "5"}]}),
                       ("Filter Formel-Text enthaelt", {"filters": [{"pid": "tag", "op": "contains", "value": "mitt"}]}),
                       ("Filter Formel-Haekchen", {"filters": [{"pid": "gross", "op": "checked", "value": ""}]}),
                       ("Filter Rollup-Liste enthaelt", {"filters": [{"pid": "pnam", "op": "contains", "value": "Alpha"}]}),
                       ("Sortierung Formel absteigend", {"sorts": [{"pid": "summe", "dir": "desc"}]}),
                       ("Sortierung Rollup-Datum", {"sorts": [{"pid": "pst", "dir": "asc"}]}),
                       ("Gruppen nach Formel (Wochentag)", {"group": "tag"}),
                       ("Gruppen nach Formel-Zahl", {"group": "summe"}),
                       ("Suche findet Formel-Wert", {"q": "dienstag"})):
        s, d = req("GET", f"/api/notes/{db}/rows" + qs(**args), A)
        exp, matched, groups = dbquery.run(full["schema"], full["rows"], {k: v for k, v in args.items()}, labels=lambda ids: {})
        good = s == 200 and [r["id"] for r in d["rows"]] == [r["id"] for r in exp] and d.get("matched") == matched and d.get("groups") == groups
        ok(f"/rows {name}: Server = Referenz (Web-Semantik)", good, (s, d.get("groups"), groups, [r["id"] for r in d.get("rows", [])], [r["id"] for r in exp]))
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(group="tag"), A)
    ok("Gruppen nach Formel: id = Wert als Text, value = Wert, Leere zuerst (id null)",
       s == 200 and d["groups"][0]["id"] is None and [g.get("value") for g in d["groups"][1:]] == ["Montag", "Mittwoch"], d.get("groups"))
    # Ansicht: Gruppieren nach Formel speichern (Board + Tabelle)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1", A)
    sch = d["schema"]
    sch["views"] = [{"id": "b", "name": "Board", "type": "board", "group_by": "tag"}, {"id": "t", "name": "T", "type": "table", "group": "gross"},
                    {"id": "x", "name": "X", "type": "table", "group": "pnam"}]
    s, d = req("PATCH", f"/api/notes/{db}", A, {"schema": sch})
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1", A)
    vs = {v["id"]: v for v in d["schema"]["views"]}
    ok("Ansicht: Board/Tabelle nach Formel gruppieren gespeichert; Liste (rtype list) nicht",
       vs["b"].get("group_by") == "tag" and vs["t"].get("group") == "gross" and "group" not in vs["x"], vs)
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(view="b"), A)
    ok("Board nach Formel: Gruppen aus dem Server", s == 200 and [g.get("value") for g in d["groups"]][1:] == ["Montag", "Mittwoch"], d.get("groups"))
    # Formel-Fehler beim Speichern: 400 mit Position; fremde Aenderung (Spalte geloescht) bleibt speicherbar
    bad = json.loads(json.dumps(sch))
    next(p for p in bad["props"] if p["id"] == "summe")["expr"] = "prop(\"menge\") * "
    s, d = req("PATCH", f"/api/notes/{db}", A, {"schema": bad})
    ok(f"Schema mit fehlerhafter Formel -> 400 mit Name und Position ({s})", s == 400 and "Summe" in str(d.get("error")) and "Zeichen" in str(d.get("error")), d)
    old_app = json.loads(json.dumps(sch))
    old_app["props"] = [p for p in old_app["props"] if p["id"] != "preis"]
    s, d = req("PATCH", f"/api/notes/{db}", A, {"schema": old_app})
    s2, d2 = req("GET", f"/api/notes/{db}/rows", A)
    e1 = next(r for r in d2["rows"] if r["id"] == r1)
    ok(f"Spalte geloescht, die eine Formel nutzt: Speichern geht ({s}), Zelle zeigt fx_err 'ref'", s == 200 and e1.get("fx_err", {}).get("summe") == "ref", e1)
    s, _ = req("PATCH", f"/api/notes/{db}", A, {"schema": d2["schema"] | {"props": d2["schema"]["props"] + [{"id": "preis", "name": "Preis", "type": "number"}]}})
    s2, d2 = req("PATCH", f"/api/notes/{r1}", A, {"props": {"preis": 2.5}})
    ok(f"Spalte wieder da: Formel rechnet wieder ({s}, {s2})", s == 200 and d2.get("calc", {}).get("summe") == 10 and d2.get("fx_err") == {}, d2)
    # Pruef-Endpunkt
    s, d = req("POST", f"/api/notes/{db}/formula/check", A, {"expr": "prop(\"Menge\") * 2 + prop(\"Name\").length()", "pid": "neu"})
    ok(f"formula/check: Namen -> IDs, rtype, Vorschau ({s})", s == 200 and d["ok"] and d["rtype"] == "number"
       and d["expr"] == "prop(\"menge\") * 2 + prop(\"title\").length()" and len(d["preview"]) == 3, d)
    ok("formula/check: display mit Namen", d.get("display") == "prop(\"Menge\") * 2 + prop(\"Name\").length()", d.get("display"))
    s, d = req("POST", f"/api/notes/{db}/formula/check", A, {"expr": "prop(\"Menge\") *", "pid": "neu"})
    ok("formula/check: Fehler mit Position (200, ok false)", s == 200 and d["ok"] is False and d["error"]["pos"] == 15, d)
    s, d = req("POST", f"/api/notes/{db}/formula/check", A, {"expr": "prop(\"menge\")", "pid": "x", "names": False})
    ok("formula/check names=false: IDs bleiben", s == 200 and d["ok"] and d["expr"] == "prop(\"menge\")" and d["display"] == "prop(\"Menge\")", d)
    # Export
    s, md = req("GET", f"/api/notes/{db}/export", A)
    ok("Export (Markdown): Formel- und Rollup-Werte in der Tabelle", isinstance(md, str) and "Alpha, Beta" in md and "Montag" in md, str(md)[:500])
    return db, proj, r1, p1, p2


def leak_part(db, proj, r1, p1, p2):
    """Bert bekommt nur die Aufgaben-Datenbank geteilt, NICHT die Projekte."""
    s, _ = req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": BU, "role": "edit"})
    ok(f"Aufgaben mit Bert geteilt (Bearbeiten) ({s})", s == 200)
    s, d = req("GET", f"/api/notes/{db}/rows", BU)
    e = next((r for r in d.get("rows", []) if r["id"] == r1), {})
    raw = json.dumps(d, ensure_ascii=False)
    ok("Leck-Test: Rollup auf nicht geteilte Projekte -> kein Wert (Summe/Namen/Start/%)",
       s == 200 and not any(k in e["props"] for k in ("pbud", "pnam", "pst", "pfer")), e.get("props"))
    ok("Leck-Test: kein Projekttitel, kein Budget in der Antwort", "Alpha" not in raw and "Beta" not in raw and "150" not in raw.replace('"id"', ""), raw[:400])
    ok("Formeln ohne Rollup rechnen fuer Bert normal", e["props"].get("summe") == 10, e.get("props"))
    for args in (qs(filters=[{"pid": "pbud", "op": "gt", "value": "1"}]), qs(sorts=[{"pid": "pbud", "dir": "desc"}]), qs(q="alpha")):
        s, d = req("GET", f"/api/notes/{db}/rows" + args, BU)
        ok(f"Leck-Test: Filter/Sortierung/Suche ueber Rollup verraten nichts ({urllib.parse.unquote(args)[:40]})",
           s == 200 and (d.get("matched") == 0 if "filters" in args or "q=" in args else True) and "Alpha" not in json.dumps(d, ensure_ascii=False), d.get("matched"))
    s, d = req("GET", f"/api/notes/{r1}", BU)
    ok("Leck-Test: Einzelzeile fuer Bert ohne Rollup-Werte", s == 200 and "pbud" not in d.get("props", {}) and "pnam" not in d.get("props", {}), d.get("props"))
    s, d = req("PATCH", f"/api/notes/{r1}", BU, {"props": {"menge": 4}})
    ok("Leck-Test: PATCH-Antwort fuer Bert ohne Rollup-Werte", s == 200 and d.get("calc", {}).get("pbud") is None and d["calc"].get("pnam") is None, d)
    s, d = req("POST", f"/api/notes/{db}/formula/check", BU, {"expr": "prop(\"Projektnamen\")", "pid": "x"})
    ok("Leck-Test: Formel-Vorschau ueber Rollup fuer Bert leer", s == 200 and all(x["value"] is None for x in d.get("preview", [])), d)
    s, md = req("GET", f"/api/notes/{db}/export", BU)
    ok("Leck-Test: Export fuer Bert ohne Projektnamen", isinstance(md, str) and "Alpha" not in md, str(md)[:300])
    # Nur EIN Projekt geteilt -> nur dieses zaehlt
    s, _ = req("PUT", f"/api/notes/{p1}/shares", A, {"type": "user", "target": BU, "role": "view"})
    s, d = req("GET", f"/api/notes/{db}/rows", BU)
    e = next((r for r in d.get("rows", []) if r["id"] == r1), {})
    ok("Teilfreigabe: nur das sichtbare Projekt fliesst ein? (Ziel-Datenbank selbst nicht sichtbar -> weiter nichts)",
       not any(k in e["props"] for k in ("pbud", "pnam")), e.get("props"))
    s, _ = req("PUT", f"/api/notes/{proj}/shares", A, {"type": "user", "target": BU, "role": "view"})
    s, d = req("GET", f"/api/notes/{db}/rows", BU)
    e = next((r for r in d.get("rows", []) if r["id"] == r1), {})
    ok("Projekte geteilt -> Rollup fuer Bert wie fuer Anna (ohne Archiv)", e["props"].get("pbud") == 150 and e["props"].get("pnam") == ["Alpha", "Beta"], e.get("props"))
    # Carl ohne Zugriff
    s, d = req("POST", f"/api/notes/{db}/formula/check", CU, {"expr": "1"})
    ok(f"formula/check ohne Zugriff -> 404 ({s})", s == 404)
    s, _ = req("PUT", f"/api/notes/{db}/shares", A, {"type": "user", "target": CU, "role": "view"})
    s, d = req("POST", f"/api/notes/{db}/formula/check", CU, {"expr": "1"})
    ok(f"formula/check nur Lesen -> 403 ({s})", s == 403)


def legacy_part():
    sch = {"props": [{"id": "n", "name": "N", "type": "number"},
                     {"id": "f", "name": "F", "type": "formula", "expr": "prop(\"n\") * 2", "rtype": "number", "fmt": "euro"},
                     {"id": "r", "name": "R", "type": "rollup", "rel": "x", "target": "title", "fn": "show", "rtype": "list"}]}
    leg = dbcalc.legacy_schema(sch)
    ok("Aeltere Gegenstelle: Formel/Rollup als Text-Spalte mit fx_orig", [p["type"] for p in leg["props"]] == ["number", "text", "text"]
       and leg["props"][1]["fx_orig"]["expr"] == "prop(\"n\") * 2", leg)
    rows = [{"id": 1, "props": {"n": 2, "f": 4, "r": ["a", "b"]}, "fx_err": {"x": "num"}}, {"id": 2, "props": {"f": True}}]
    dbcalc.legacy_rows(sch, rows)
    ok("Aeltere Gegenstelle: Werte als Text, ohne fx_err", rows[0]["props"] == {"n": 2, "f": "4", "r": "a, b"} and "fx_err" not in rows[0]
       and rows[1]["props"] == {"f": "✓"}, rows)
    from saga.notes import _clean_schema
    back = _clean_schema(json.loads(json.dumps(leg)) | {"props": leg["props"] + [{"id": "x", "name": "X", "type": "relation", "target": 5}]})
    ok("Zurueckgeschrieben (altes Web speichert Schema): Formel/Rollup unveraendert wiederhergestellt",
       [p["type"] for p in back["props"][:3]] == ["number", "formula", "rollup"] and back["props"][1]["expr"] == "prop(\"n\") * 2"
       and back["props"][1].get("fmt") == "euro" and back["props"][2]["rel"] == "x" and "fx_orig" not in back["props"][1], back["props"])
    from saga import federation as F, fed_pages
    ok("Heim: fx_err der Gegenstelle nur {pid: code}", F.fx_err_in({"a": "num", "b": "<x>", 3: "num", "c": {"x": 1}}) == {"a": "num"})
    ok("Heim: Gruppen mit value gefiltert", fed_pages._group_in({"id": "Mo", "value": "Mo", "count": 2, "x": 1}) == {"id": "Mo", "value": "Mo", "count": 2}
       and fed_pages._group_in({"id": None, "count": 1}) == {"id": None, "count": 1}
       and fed_pages._group_in({"id": "1", "value": {"x": 1}, "count": 1}) == {"id": "1", "count": 1})
    ok("Faehigkeit formula1 gemeldet (CAPS2, PAGE_CAPS)", "formula1" in F.CAPS2 and "formula1" in F.PAGE_CAPS)


def version_part():
    s, d = req("GET", "/api/version")
    ok(f"/api/version meldet features formula1/rollup1/dbgroup1 ({s})", s == 200 and {"formula1", "rollup1", "dbgroup1"} <= set(d.get("features") or []), d)


def cleanup():
    with connect() as c, c:
        for u in (A, BU, CU):
            for t in ("note_share",):
                c.execute(f"DELETE FROM {t} WHERE owner_uid=?", (u,))
            c.execute("DELETE FROM note WHERE uid=?", (u,))
            c.execute("DELETE FROM users WHERE username=?", (u,))


def main():
    vectors()
    budgets()
    core()
    legacy_part()
    cleanup()
    for u in (A, BU, CU):
        auth.get_user(u) or auth.create_user(u, None, u)
    try:
        version_part()
        db, proj, r1, p1, p2 = http_part()
        leak_part(db, proj, r1, p1, p2)
    finally:
        cleanup()
    n = len(res)
    print(f"\nFORMELN {sum(res)}/{n} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
