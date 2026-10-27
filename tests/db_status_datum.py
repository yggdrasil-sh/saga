"""Datenbanken 2.5, Paket 5 (Server): Status-Eigenschaft, relative Datumsfilter, Datum mit Uhrzeit/Zeitraum, "diese Seite".

Prueft gegen einen laufenden Server (HK_TEST_BASE, BASE_URL, SESSION_SECRET):
- /api/version meldet relfilter1, datetime1, status1, subtasks1, tableopts1.
- Status (P5-E2): neue Datenbank hat status:true + Gruppen todo/doing/done; Schema bereinigt group (ohne/kaputt -> todo,
  ohne status:true keine group); Filter status_group (todo/doing/done/open) ueber /rows; Board nach Status-Gruppe
  (view.group_status) -> groups mit den Gruppen-IDs; alte Clients schicken das Schema ohne group -> todo.
- Relative Filter (relfilter1): yesterday/tomorrow/last_week/next_week/last_month/next_month/this_year/overdue,
  next_days/past_days N (inkl. heute), on_or_before/on_or_after mit Datum oder today+-N - mit festem today/tz;
  unvollstaendiger Wert zaehlt nicht; Zeitraum beruehrt das Fenster; Grenzen (Monatsende, Jahreswechsel, Schaltjahr).
- Datum mit Uhrzeit/Zeitraum (P5-E1, datetime1): Speichern normalisiert (Sekunden weg, Z -> +00:00), ungueltig -> 400
  (Offset fehlt, 25:00, 30.02., Ende vor Beginn); ALTE APP schreibt nur den Tag zurueck: gleicher Tag -> Uhrzeit/Zeitraum
  bleibt, anderer Tag -> nur der Tag; Sortierung nach Zeitpunkt in tz; Filter eq/before/after nach Kalendertag in tz;
  Kalendertag haengt von tz ab (23:30+02:00 ist in New York noch derselbe, in Bangkok der naechste Tag).
- Formeln: hour()/minute()/dateStart()/dateEnd()/dateBetween() mit Uhrzeit und Zeitraum.
- CSV: ISO-Wert unveraendert; Markdown-Export lesbar ("27.10.2026 14:30 – 16:00").
- Veroeffentlichen: neue Filter in Block-Ansichten wirken.
- "Diese Seite" (P5-E3): @self in Filtern eingebetteter Datenbank-Bloecke einer Eintrags-Vorlage wird beim Anlegen
  (duplicate mit parent_id) und beim Duplizieren einer Zeile auf die neue Seite gesetzt, self:true bleibt; verschachtelt
  (Toggle/Spalten); Schema-Ansichten der DB bleiben unveraendert.
Wegwerf-Konten p5-*, raeumt auf; am Ende: keine neuen Serverfehler im Log."""
import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "server.log")
A, BU = "p5-a", "p5-b"
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, raw=False):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    b = r.read()
    if raw:
        return r.status, b
    try:
        return r.status, json.loads(b) if b[:1] in (b"[", b"{") else b
    except ValueError:
        return r.status, b


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


def errs():
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            return f.read().count("Traceback")
    except OSError:
        return 0


def new_db(title, props, views=None):
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": title})
    db = d["id"]
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": {"props": props, "views": views or [{"id": "t", "name": "T", "type": "table"}]}})
    assert s == 200, d
    return db


def row(db, title, props=None):
    s, d = req("POST", "/api/notes", body={"parent_id": db, "title": title, "props": props or {}})
    assert s == 200, (s, d)
    return d["id"]


def titles(db, **kw):
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(**kw))
    return (sorted(r["title"] for r in d["rows"]) if s == 200 else (s, d)), d


def run():
    s, v = req("GET", "/api/version", u=None)
    ok("/api/version: features relfilter1, datetime1, status1, subtasks1, tableopts1",
       all(f in (v.get("features") or []) for f in ("relfilter1", "datetime1", "status1", "subtasks1", "tableopts1")), v)

    # --- Status --------------------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": "Neu"})
    s, g = req("GET", f"/api/notes/{d['id']}")
    st = next(p for p in g["schema"]["props"] if p["id"] == "status")
    ok("Standard-Schema: Status mit status:true und Gruppen todo/doing/done",
       st.get("status") is True and [o.get("group") for o in st["options"]] == ["todo", "doing", "done"], st)
    sel = [{"id": "o1", "name": "Idee", "color": "gray"}, {"id": "o2", "name": "Läuft", "color": "blue", "group": "doing"},
           {"id": "o3", "name": "Fertig", "color": "green", "group": "done"}, {"id": "o4", "name": "Verworfen", "color": "red", "group": "kaputt"}]
    db = new_db("Status", [{"id": "st", "name": "Status", "type": "select", "status": True, "options": sel},
                           {"id": "pr", "name": "Prio", "type": "select", "options": [dict(sel[2], group="done")]}],
                [{"id": "b", "name": "Board", "type": "board", "group_by": "st", "group_status": True},
                 {"id": "t", "name": "T", "type": "table", "group": "st", "group_status": "ja"}])
    s, g = req("GET", f"/api/notes/{db}")
    st = g["schema"]["props"][0]
    ok("Status: ohne/kaputte Gruppe -> todo, gueltige bleiben", [o.get("group") for o in st["options"]] == ["todo", "doing", "done", "todo"], st)
    ok("Auswahl ohne status:true: keine group", "group" not in g["schema"]["props"][1]["options"][0] and "status" not in g["schema"]["props"][1],
       g["schema"]["props"][1])
    vs = {v["id"]: v for v in g["schema"]["views"]}
    ok("Ansicht: group_status nur bei true", vs["b"].get("group_status") is True and "group_status" not in vs["t"], vs)
    for t_, o in (("A", "o1"), ("B", "o2"), ("C", "o3"), ("D", "o4"), ("E", None)):
        row(db, t_, {"st": o} if o else {})
    for grp, want in (("todo", ["A", "D"]), ("doing", ["B"]), ("done", ["C"]), ("open", ["A", "B", "D"]), ("", ["A", "B", "C", "D", "E"]), ("xyz", ["A", "B", "C", "D", "E"])):
        got, _ = titles(db, filters=[{"pid": "st", "op": "status_group", "value": grp}])
        ok(f"Filter status_group={grp or '(leer)'} -> {want}", got == want, got)
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(view="b"))
    ok("Board nach Status-Gruppe: groups Ohne/todo/doing/done mit Anzahl",
       d.get("groups") == [{"id": None, "count": 1}, {"id": "todo", "count": 2}, {"id": "doing", "count": 1}, {"id": "done", "count": 1}], d.get("groups"))
    # alte App schickt das Schema ohne group/status zurueck -> Status geht verloren? (srev/Rundreise): Gruppe fehlt -> todo
    old = json.loads(json.dumps(g["schema"]))
    for o in old["props"][0]["options"]:
        o.pop("group", None)
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": old})
    s, g2 = req("GET", f"/api/notes/{db}")
    ok("Schema ohne group (alter Client): status bleibt, Gruppen -> todo (Hinweis Doku)",
       g2["schema"]["props"][0].get("status") is True and all(o["group"] == "todo" for o in g2["schema"]["props"][0]["options"]), g2["schema"]["props"][0])

    # --- relative Filter -------------------------------------------------------------------------------------------------
    db = new_db("Relativ", [{"id": "d", "name": "Datum", "type": "date", "time": True, "range": True}])
    days = {"heute": "2026-10-27", "gestern": "2026-10-26", "morgen": "2026-10-28", "letzteWo": "2026-10-19", "naechsteWo": "2026-11-08",
            "letzterMo": "2026-09-30", "naechsterMo": "2026-11-30", "vorjahr": "2025-12-31", "spaeter": "2027-01-01",
            "zr": "2026-10-20/2026-10-26", "zrHeute": "2026-10-25/2026-10-29", "uhr": "2026-10-27T23:30+02:00", "in7": "2026-11-02", "in8": "2026-11-03",
            "vor6": "2026-10-21", "vor7": "2026-10-20"}
    for k, v_ in days.items():
        row(db, k, {"d": v_})
    row(db, "leer")
    T0 = {"today": "2026-10-27", "tz": "Europe/Berlin"}
    cases = [
        ("today", "", {"heute", "zrHeute", "uhr"}),
        ("yesterday", "", {"gestern", "zrHeute", "zr"}),
        ("tomorrow", "", {"morgen", "zrHeute"}),
        ("this_week", "", {"heute", "gestern", "morgen", "zrHeute", "zr", "uhr"}),          # Di 27.10.: Mo 26. - So 1.11.
        ("last_week", "", {"letzteWo", "zr", "zrHeute", "vor6", "vor7"}),   # 19.-25.: Zeitraum ab 25. beruehrt
        ("next_week", "", {"naechsteWo", "in7", "in8"}),
        ("last_month", "", {"letzterMo"}),
        ("next_month", "", {"naechsteWo", "naechsterMo", "in7", "in8"}),
        ("this_year", "", set(days) - {"vorjahr", "spaeter"}),
        ("overdue", "", {"gestern", "letzteWo", "letzterMo", "vorjahr", "zr", "vor6", "vor7"}),
        ("next_days", "7", {"heute", "morgen", "zrHeute", "uhr", "in7"}),
        ("past_days", "7", {"heute", "gestern", "zrHeute", "zr", "uhr", "vor6"}),
        ("on_or_before", "today-7", {"letzteWo", "letzterMo", "vorjahr", "zr", "vor7"}),
        ("on_or_after", "2026-11-03", {"naechsteWo", "naechsterMo", "spaeter", "in8"}),
        ("on_or_after", "today+6", {"naechsteWo", "naechsterMo", "spaeter", "in7", "in8"}),
    ]
    for op, val, want in cases:
        got, _ = titles(db, filters=[{"pid": "d", "op": op, "value": val}], **T0)
        ok(f"Filter {op} {val}".strip(), set(got) == want if isinstance(got, list) else False, (sorted(want), got))
    allrows = sorted(set(days) | {"leer"})
    for op, val in (("next_days", "0"), ("next_days", "400"), ("next_days", "abc"), ("on_or_before", "gestern"), ("on_or_after", "naechste Woche")):
        got, _ = titles(db, filters=[{"pid": "d", "op": op, "value": val}], **T0)
        ok(f"unvollstaendiger Wert zaehlt nicht: {op} {val!r}", got == allrows, got)
    # Zeitzone: 23:30+02:00 am 27. ist in Bangkok schon der 28., in New York noch der 27.
    got, _ = titles(db, filters=[{"pid": "d", "op": "today", "value": ""}], today="2026-10-28", tz="Asia/Bangkok")
    ok("tz Bangkok: 27.10. 23:30+02:00 zaehlt als 28.10. (heute)", "uhr" in got and "heute" not in got, got)
    got, _ = titles(db, filters=[{"pid": "d", "op": "today", "value": ""}], today="2026-10-27", tz="America/New_York")
    ok("tz New York: derselbe Wert ist der 27.10.", "uhr" in got, got)
    # Grenzen: Jahreswechsel, Monatsende, Schaltjahr
    db2 = new_db("Grenzen", [{"id": "d", "name": "Datum", "type": "date"}])
    for t_, v_ in (("silvester", "2025-12-31"), ("neujahr", "2026-01-01"), ("29feb", "2028-02-29"), ("1mrz", "2028-03-01"), ("kw1", "2025-12-29")):
        row(db2, t_, {"d": v_})
    got, _ = titles(db2, filters=[{"pid": "d", "op": "last_month", "value": ""}], today="2026-01-15", tz="UTC")
    ok("letzter Monat ueber den Jahreswechsel (Dez. 2025)", got == ["kw1", "silvester"], got)
    got, _ = titles(db2, filters=[{"pid": "d", "op": "this_week", "value": ""}], today="2026-01-01", tz="UTC")
    ok("diese Woche ueber den Jahreswechsel (Mo 29.12.)", got == ["kw1", "neujahr", "silvester"], got)
    got, _ = titles(db2, filters=[{"pid": "d", "op": "tomorrow", "value": ""}], today="2028-02-28", tz="UTC")
    ok("morgen im Schaltjahr = 29.02.", got == ["29feb"], got)
    got, _ = titles(db2, filters=[{"pid": "d", "op": "next_month", "value": ""}], today="2028-01-31", tz="UTC")
    ok("naechster Monat ab 31.01. = Februar", got == ["29feb"], got)

    # --- Datum mit Uhrzeit / Zeitraum -------------------------------------------------------------------------------------
    db = new_db("Termine", [{"id": "d", "name": "Wann", "type": "date", "time": True, "range": True}, {"id": "n", "name": "Notiz", "type": "text"}])
    s, g = req("GET", f"/api/notes/{db}")
    ok("Schema: time/range bleiben", g["schema"]["props"][0].get("time") is True and g["schema"]["props"][0].get("range") is True, g["schema"]["props"][0])
    r1 = row(db, "Meeting", {"d": "2026-10-27T14:30:59Z"})
    s, d = req("GET", f"/api/notes/{r1}")
    ok("normalisiert: Sekunden weg, Z -> +00:00", d["props"]["d"] == "2026-10-27T14:30+00:00", d["props"])
    for bad in ("2026-10-27T14:30", "2026-10-27T25:00+01:00", "2026-02-30", "2026-10-29/2026-10-27", "2026-10-27T10:00+01:00/2026-10-27T09:00+01:00",
                "x", "2026-10-27/2026-10-28/2026-10-29", "2026-10-27T10:00+19:00"):
        s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"d": bad}})
        ok(f"ungueltig -> 400: {bad}", s == 400, (s, d))
    s, d = req("GET", f"/api/notes/{r1}")
    ok("nach Fehlern unveraendert", d["props"]["d"] == "2026-10-27T14:30+00:00", d["props"])
    # alte App: nur den Tag zurueckschreiben
    s, _ = req("PATCH", f"/api/notes/{r1}", body={"props": {"d": "2026-10-27T14:30+01:00"}})
    s, _ = req("PATCH", f"/api/notes/{r1}", body={"props": {"d": "2026-10-27", "n": "von alter App"}})
    s, d = req("GET", f"/api/notes/{r1}")
    ok("ALTE APP schreibt gleichen Tag zurueck -> Uhrzeit bleibt", d["props"]["d"] == "2026-10-27T14:30+01:00" and d["props"]["n"] == "von alter App", d["props"])
    s, _ = req("PATCH", f"/api/notes/{r1}", body={"props": {"d": "2026-10-28"}})
    s, d = req("GET", f"/api/notes/{r1}")
    ok("ALTE APP aendert den Tag -> nur der Tag (Uhrzeit weg, dokumentiert)", d["props"]["d"] == "2026-10-28", d["props"])
    r2 = row(db, "Urlaub", {"d": "2026-10-27/2026-10-30"})
    s, _ = req("PATCH", f"/api/notes/{r2}", body={"props": {"d": "2026-10-27"}})
    s, d = req("GET", f"/api/notes/{r2}")
    ok("ALTE APP: Zeitraum bleibt, wenn der Start gleich bleibt", d["props"]["d"] == "2026-10-27/2026-10-30", d["props"])
    s, _ = req("PATCH", f"/api/notes/{r2}", body={"props": {"d": "2026-10-27/2026-10-27"}})
    s, d = req("GET", f"/api/notes/{r2}")
    ok("Zeitraum mit gleichem Start und Ende -> ein Tag", d["props"]["d"] == "2026-10-27", d["props"])
    s, _ = req("PATCH", f"/api/notes/{r2}", body={"props": {"d": "2026-10-27/2026-10-30"}})
    # App-Schreibweg (PATCH /api/app/rows/...): nicht vorhanden -> gleicher Weg ueber /api/notes reicht (eine Stelle _clean_props)
    r3 = row(db, "Frueh", {"d": "2026-10-27T08:00+01:00"})
    r4 = row(db, "Spaet NY", {"d": "2026-10-27T09:00-05:00"})            # = 15:00 in Berlin
    r5 = row(db, "Tag", {"d": "2026-10-27"})
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(sorts=[{"pid": "d", "dir": "asc"}], tz="Europe/Berlin", today="2026-10-27"))
    order = [r["title"] for r in d["rows"]]
    ok("Sortierung nach Zeitpunkt in tz (Tag = Start 00:00, NY 09:00 = Berlin 15:00)",
       order == ["Urlaub", "Tag", "Frueh", "Spaet NY", "Meeting"], order)          # gleicher Start: stabil (Anlegereihenfolge)
    got, _ = titles(db, filters=[{"pid": "d", "op": "eq", "value": "2026-10-29"}], tz="Europe/Berlin", today="2026-10-27")
    ok("eq Tag: Zeitraum 27.-30. enthaelt den 29.", got == ["Urlaub"], got)
    got, _ = titles(db, filters=[{"pid": "d", "op": "after", "value": "2026-10-27"}], tz="Europe/Berlin", today="2026-10-27")
    ok("after 27.: Meeting (28.) und Urlaub (Ende 30.)", got == ["Meeting", "Urlaub"], got)
    got, _ = titles(db, filters=[{"pid": "d", "op": "before", "value": "2026-10-28"}], tz="Europe/Berlin", today="2026-10-27")
    ok("before 28.: alle mit Start am 27.", got == ["Frueh", "Spaet NY", "Tag", "Urlaub"], got)
    _ = (r3, r4, r5)

    # Formeln mit Uhrzeit/Zeitraum
    db = new_db("Formeln", [{"id": "d", "name": "Wann", "type": "date", "time": True, "range": True},
                            {"id": "h", "name": "Stunde", "type": "formula", "expr": 'hour(prop("d"))'},
                            {"id": "m", "name": "Minute", "type": "formula", "expr": 'minute(prop("d"))'},
                            {"id": "dur", "name": "Dauer", "type": "formula", "expr": 'dateBetween(dateEnd(prop("d")), dateStart(prop("d")), "hours")'},
                            {"id": "tg", "name": "Tage", "type": "formula", "expr": 'dateBetween(dateEnd(prop("d")), dateStart(prop("d")), "days")'}])
    row(db, "Termin", {"d": "2026-10-27T09:15+01:00/2026-10-27T11:45+01:00"})
    row(db, "Reise", {"d": "2026-10-27/2026-10-30"})
    row(db, "Tag", {"d": "2026-10-27"})
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(tz="Europe/Berlin"))
    by = {r["title"]: r["props"] for r in d.get("rows", [])}
    ok("Formel hour/minute aus Zeitpunkt (tz Berlin)", by.get("Termin", {}).get("h") == 9 and by["Termin"].get("m") == 15, by.get("Termin"))
    ok("Formel dateBetween(dateEnd, dateStart, hours) = 2", by.get("Termin", {}).get("dur") == 2, by.get("Termin"))
    ok("Formel Zeitraum in Tagen = 3", by.get("Reise", {}).get("tg") == 3, by.get("Reise"))
    ok("Formel reiner Tag: Dauer 0", by.get("Tag", {}).get("tg") == 0, by.get("Tag"))
    s, d = req("GET", f"/api/notes/{db}/rows" + qs(tz="Asia/Bangkok"))
    by = {r["title"]: r["props"] for r in d.get("rows", [])}
    ok("Formel hour in tz Bangkok = 15", by.get("Termin", {}).get("h") == 15, by.get("Termin"))

    # CSV + Markdown
    db = new_db("Export", [{"id": "d", "name": "Wann", "type": "date", "time": True, "range": True}])
    row(db, "Z", {"d": "2026-10-27T14:30+01:00/2026-10-27T16:00+01:00"})
    s, raw = req("GET", f"/api/notes/{db}/export.csv?all=1&tz=Europe/Berlin", raw=True)
    ok("CSV: ISO-Wert unveraendert", s == 200 and "2026-10-27T14:30+01:00/2026-10-27T16:00+01:00" in raw.decode("utf-8"), raw[:300])
    s, raw = req("GET", f"/api/notes/{db}/export", raw=True)
    txt = raw.decode("utf-8", "replace")
    ok("Markdown: lesbar mit Uhrzeit und Zeitraum", s == 200 and "27.10.2026 14:30 – 16:00" in txt, txt[:500])

    # --- Diese Seite (@self) --------------------------------------------------------------------------------------------
    proj = new_db("Projekte", [{"id": "st", "name": "Status", "type": "select", "options": [{"id": "a", "name": "A"}]}])
    tasks = new_db("Aufgaben", [{"id": "pj", "name": "Projekt", "type": "relation", "target": proj}])
    tpl = row(proj, "Vorlage")
    req("PATCH", f"/api/notes/{tpl}", body={"template": True})
    blk = {"type": "database", "data": {"db": tasks, "view": "v1", "views": [
        {"id": "v1", "name": "Meine", "type": "table", "filters": [{"pid": "pj", "op": "contains", "value": "@self", "self": True}]}]}}
    body = {"blocks": [{"type": "paragraph", "data": {"text": "x"}}, blk,
                       {"type": "toggle", "data": {"text": "T", "blocks": [json.loads(json.dumps(blk))]}}]}
    s, d = req("PATCH", f"/api/notes/{tpl}", body={"body": json.dumps(body), "format": "blocks"})
    ok("Vorlage mit @self gespeichert", s == 200, d)
    s, g = req("GET", f"/api/notes/{tpl}")
    fl = json.loads(g["body"])["blocks"][1]["data"]["views"][0]["filters"][0]
    ok("Vorlage behaelt @self", fl.get("value") == "@self", fl)
    s, d = req("POST", f"/api/notes/{tpl}/duplicate", body={"parent_id": proj})
    new = d.get("id")
    s, g = req("GET", f"/api/notes/{new}")
    bl = json.loads(g["body"])["blocks"]
    f1, f2 = bl[1]["data"]["views"][0]["filters"][0], bl[2]["data"]["blocks"][0]["data"]["views"][0]["filters"][0]
    ok("Anlegen aus Vorlage: @self -> neue Seiten-ID, self:true bleibt", f1 == {"pid": "pj", "op": "contains", "value": str(new), "self": True}, f1)
    ok("verschachtelt (Toggle) ebenso", f2.get("value") == str(new), f2)
    s, d = req("POST", f"/api/notes/{new}/duplicate", body={"parent_id": proj})
    s, g = req("GET", f"/api/notes/{d['id']}")
    f3 = json.loads(g["body"])["blocks"][1]["data"]["views"][0]["filters"][0]
    ok("Duplizieren einer Zeile: Filter zeigt auf die Kopie", f3.get("value") == str(d["id"]) and f3.get("self") is True, f3)
    # Filter ohne self bleibt fest
    body2 = {"blocks": [{"type": "database", "data": {"db": tasks, "views": [{"id": "v", "name": "x", "type": "table",
                                                                                  "filters": [{"pid": "pj", "op": "contains", "value": str(tpl)}]}]}}]}
    p2 = row(proj, "Fest")
    req("PATCH", f"/api/notes/{p2}", body={"body": json.dumps(body2), "format": "blocks"})
    s, d = req("POST", f"/api/notes/{p2}/duplicate", body={"parent_id": proj})
    s, g = req("GET", f"/api/notes/{d['id']}")
    f4 = json.loads(g["body"])["blocks"][0]["data"]["views"][0]["filters"][0]
    ok("Filter ohne self bleibt unveraendert", f4.get("value") == str(tpl) and "self" not in f4, f4)
    s, d = req("PATCH", f"/api/notes/{tasks}", body={"schema": {"props": [{"id": "pj", "name": "Projekt", "type": "relation", "target": proj}],
                                                               "views": [{"id": "x", "name": "x", "type": "table", "filters": [{"pid": "pj", "op": "contains", "value": "@self", "self": True}]}]}})
    s, g = req("GET", f"/api/notes/{tasks}")
    ok("Schema-Ansicht: self-Merker erlaubt, Wert bleibt (nur Block-Ansichten werden umgeschrieben)",
       g["schema"]["views"][0]["filters"][0] == {"pid": "pj", "op": "contains", "value": "@self", "self": True}, g["schema"]["views"][0])


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Status-Pass-1234", u.upper())
    e0 = errs()
    try:
        run()
    finally:
        cleanup()
    n = errs() - e0
    ok(f"Serverfehler: {n}", n == 0)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'p5-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'p5-%'")


if __name__ == "__main__":
    main()
    print(f"\nSTATUS/DATUM {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
