"""2.5 Paket 7 Teil A: Aufgabenquellen (saga/tasksrc.py) - Datenbank-Zeilen in der persoenlichen Aufgabenliste.

Konten mit Markern als Anmeldenamen ('aq-<16 hex>', Anzeigenamen ohne Marker). Geprueft:
1. Schema-Zuordnung "tasks": falsche Typen/unbekannte Eigenschaften fallen weg, Auswahl braucht done_values, Status ohne,
   Clients ohne den Schluessel (aeltere Apps) behalten ihn beim Speichern des Schemas.
2. Abo: eigene DB -> 'all', geteilte DB mit Personen-Spalte -> 'assigned' (nur mir zugewiesene Zeilen), umschaltbar auf
   'all'; geteilte ohne Personen-Spalte nur 'all'; ohne Zugriff 404; Grenze 20 Quellen; Entzug der Freigabe -> leer und
   Abo entfernt; Kontoloeschung raeumt task_source.
3. GET /api/tasks: ohne with_sources unveraendert (alte Clients: nur open/done), mit with_sources=1 items (sortiert:
   ueberfaellig, heute, mit Datum, ohne), sources (map), people (Handles, Anzeigenamen); Grenze 500 offene Zeilen
   (truncated).
4. Abhaken: PATCH /api/tasks/src/{row} ueber Status-Gruppe (Erledigt), Haekchen und Auswahl (done_values) - Wieder-Oeffnen
   setzt open_value; Rechte: 'view' -> 403, ohne Zugriff 404; due/prio/text; row_done (Board-Aenderung per Props-PATCH
   zaehlt ebenso), Widget src:"tasks" (include_sources Standard an, aus = nur Inbox), /api/app/today?sources=1, App-Schluessel.
5. Neue Aufgabe in einer Quelle (POST /api/tasks {src}): Standardvorlage, Datum/Prioritaet gemappt, 'assigned' -> mir
   zugewiesen; ohne Bearbeiten-Recht 403.
6. Galerie "Aufgaben & Projekte": Zuordnung gesetzt und abonniert, Hinweis fuer alte Instanzen (hint).
7. Leck-Scan: keine Anmeldenamen anderer Konten in allen Antworten.
Wegwerf-Konten aq-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64
import http.client
import json
import os
import secrets
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
SEEN = []
MK = lambda: "aq-" + secrets.token_hex(8)        # noqa: E731
A, B, C = MK(), MK(), MK()                        # Anna (Eigentuemerin), Bert (Bearbeiten), Cora (Lesen)
NAMES = {A: "Anna Quelle", B: "Bert Quelle", C: "Cora Quelle"}
TZN = "Europe/Berlin"
TODAY = datetime.now(ZoneInfo(TZN)).date()
D = lambda n: (TODAY + timedelta(days=n)).isoformat()      # noqa: E731


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, bearer=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    else:
        h["Cookie"] = "saga=" + ck(u)
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    SEEN.append((u, f"{m} {p}", raw.decode("utf-8", "replace")))
    try:
        return r.status, json.loads(raw) if raw[:1] in (b"[", b"{") else raw
    except ValueError:
        return r.status, raw


def handle(u):
    from saga.core import local_ref
    return local_ref(u)


def tasks(u=A, src=True):
    q = f"?with_sources=1&tz={TZN}&today={TODAY.isoformat()}" if src else ""
    return req("GET", "/api/tasks" + q, u)[1]


def mkdb(u, title, schema):
    db = req("POST", "/api/notes", u, {"kind": "database", "title": title})[1]["id"]
    s, d = req("PATCH", f"/api/notes/{db}", u, {"schema": schema})
    assert s == 200, d
    return db


def row(u, db, title, props=None):
    s, d = req("POST", "/api/notes", u, {"parent_id": db, "title": title, "props": props or {}})
    assert s == 200, d
    return d["id"]


def schema_of(db, u=A):
    return req("GET", f"/api/notes/{db}/rows?limit=1", u)[1]["schema"]


STATUS = {"id": "st", "name": "Status", "type": "select", "status": True, "options": [
    {"id": "neu", "name": "Neu", "group": "todo"}, {"id": "lauf", "name": "Läuft", "group": "doing"},
    {"id": "fertig", "name": "Fertig", "group": "done"}]}
PRIO = {"id": "pr", "name": "Priorität", "type": "select", "options": [
    {"id": "h", "name": "Hoch"}, {"id": "m", "name": "Mittel"}, {"id": "n", "name": "Niedrig"}]}


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    keep = auth.settings(fresh=True).get("share_enabled")
    auth.save_setting("share_enabled", True)
    for u in (A, B, C):
        auth.create_user(u, "Quellen-Pass-1234", NAMES[u])
    time.sleep(5.6)
    try:
        run()
        scan()
    finally:
        if keep is not None:
            auth.save_setting("share_enabled", keep)
        cleanup()


def run():
    # --- 1. Schema-Zuordnung ---------------------------------------------------------------------------------------
    sch = {"props": [STATUS, PRIO, {"id": "fl", "name": "Fällig", "type": "date"}, {"id": "wer", "name": "Wer", "type": "person"},
                     {"id": "tx", "name": "Text", "type": "text"}],
           "views": [{"id": "alle", "name": "Alle", "type": "table"}, {"id": "b", "name": "Board", "type": "board", "group_by": "st"}],
           "tasks": {"done": "st", "due": "fl", "prio": "pr", "prio_map": {"h": 1, "m": 2, "n": 3}, "assignee": "wer", "open_value": "neu"}}
    db1 = mkdb(A, "Projekt Alpha", sch)
    sc = schema_of(db1)
    ok("Zuordnung gespeichert (Status ohne done_values)", sc.get("tasks") == sch["tasks"], sc.get("tasks"))
    bad = dict(sch, tasks={"done": "tx", "due": "st"})
    s, _ = req("PATCH", f"/api/notes/{db1}", A, {"schema": bad})
    ok(f"falscher Typ fuer done -> Zuordnung faellt weg ({s})", s == 200 and "tasks" not in schema_of(db1))
    s, _ = req("PATCH", f"/api/notes/{db1}", A, {"schema": dict(sch, tasks={**sch["tasks"], "due": "tx", "assignee": "nix"})})
    sc = schema_of(db1)
    ok("falscher Typ fuer due/unbekannte Person-Spalte -> nur diese Schluessel fallen weg",
       sc["tasks"].get("done") == "st" and "due" not in sc["tasks"] and "assignee" not in sc["tasks"], sc.get("tasks"))
    req("PATCH", f"/api/notes/{db1}", A, {"schema": sch})
    old = {k: v for k, v in schema_of(db1).items() if k != "tasks"}
    s, _ = req("PATCH", f"/api/notes/{db1}", A, {"schema": old})
    ok(f"Client ohne 'tasks' (alte App) behaelt die Zuordnung ({s})", schema_of(db1).get("tasks") == sch["tasks"], schema_of(db1).get("tasks"))
    sel = {"props": [{"id": "s", "name": "Stand", "type": "select", "options": [{"id": "o", "name": "offen"}, {"id": "x", "name": "erledigt"}]}],
           "views": [{"id": "a", "name": "A", "type": "table"}], "tasks": {"done": "s"}}
    db_sel = mkdb(A, "Auswahl", sel)
    ok("Auswahl ohne done_values -> keine Zuordnung", "tasks" not in schema_of(db_sel))
    sel["tasks"] = {"done": "s", "done_values": ["x"]}
    req("PATCH", f"/api/notes/{db_sel}", A, {"schema": sel})
    ok("Auswahl mit done_values -> Zuordnung", schema_of(db_sel).get("tasks", {}).get("done_values") == ["x"], schema_of(db_sel).get("tasks"))
    cbs = {"props": [{"id": "ok", "name": "Erledigt", "type": "checkbox"}, {"id": "d", "name": "Datum", "type": "date"}],
           "views": [{"id": "a", "name": "A", "type": "table"}]}
    db_cb = mkdb(A, "Häkchen", cbs)
    s, d = req("GET", f"/api/notes/{db_cb}/tasksource", A)
    ok(f"ohne Zuordnung: Vorschlag aus Typen, kein Hinweis (nur alte Auswahl-Instanzen) ({s})", s == 200 and d["map"] is None
       and d["suggest"] == {"done": "ok", "due": "d"} and d["hint"] is False and d["can_map"] is True, d)
    old_inst = mkdb(A, "Alte Instanz", {"props": [{"id": "status", "name": "Status", "type": "select", "options": [
        {"id": "offen", "name": "Offen"}, {"id": "erledigt", "name": "Erledigt"}]}, {"id": "faellig", "name": "Fällig", "type": "date"}],
        "views": [{"id": "a", "name": "A", "type": "table"}]})
    s, d2 = req("GET", f"/api/notes/{old_inst}/tasksource", A)
    ok(f"alte Vorlagen-Instanz (Auswahl 'erledigt' + Datum): Hinweis + Vorschlag ({s})", d2["hint"] is True
       and d2["suggest"] == {"done": "status", "done_values": ["erledigt"], "due": "faellig"}, d2)
    s, d = req("PUT", f"/api/notes/{db_cb}/tasksource", A, {"map": d["suggest"]})
    ok(f"Als Aufgabenquelle verwenden: Zuordnung + Abo 'all' ({s})", s == 200 and d["map"] == {"done": "ok", "due": "d"} and d["scope"] == "all", d)

    # --- 2. Abo und Lesen ------------------------------------------------------------------------------------------
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", A, {})
    ok(f"eigene DB abonniert: Standard 'all' ({s})", s == 200 and d["scope"] == "all", d)
    req("PUT", f"/api/notes/{db_sel}/tasksource", A, {})
    r_over = row(A, db1, "Überfällig", {"st": "neu", "fl": D(-2), "pr": "n"})
    r_today = row(A, db1, "Heute hoch", {"st": "lauf", "fl": D(0), "pr": "h"})
    r_later = row(A, db1, "Später", {"st": "neu", "fl": D(5), "wer": [handle(A)]})
    r_none = row(A, db1, "Ohne Datum", {"st": "neu"})
    r_done = row(A, db1, "Schon fertig", {"st": "fertig", "fl": D(-1)})
    r_time = row(A, db1, "Mit Uhrzeit", {"st": "neu", "fl": D(1) + "T09:30+01:00"})
    s_open = row(A, db_sel, "Auswahl offen", {"s": "o"})
    row(A, db_sel, "Auswahl erledigt", {"s": "x"})
    c_open = row(A, db_cb, "Haken offen", {"d": D(0)})
    row(A, db_cb, "Haken erledigt", {"ok": True})
    tid = req("POST", "/api/tasks", A, {"text": "Inbox heute", "due": D(0)})[1]["id"]
    s, old = req("GET", "/api/tasks", A)
    ok(f"ohne with_sources: Antwort wie bisher (nur open/done) ({s})", set(old) == {"open", "done"}
       and [x["text"] for x in old["open"]] == ["Inbox heute"], old)
    d = tasks()
    keys = [x["key"] for x in d["items"]]
    ok("with_sources: items aus Inbox und drei Quellen, erledigte fehlen", set(keys) == {f"t:{tid}", f"r:{r_over}", f"r:{r_today}", f"r:{r_later}",
       f"r:{r_none}", f"r:{r_time}", f"r:{s_open}", f"r:{c_open}"}, keys)
    pos = {k: i for i, k in enumerate(keys)}
    ok("Sortierung: ueberfaellig, heute (Prio), mit Datum, ohne Datum", pos[f"r:{r_over}"] == 0 and pos[f"r:{r_today}"] < pos[f"t:{tid}"]
       and pos[f"r:{c_open}"] < pos[f"r:{r_time}"] < pos[f"r:{r_later}"] < pos[f"r:{r_none}"], keys)
    it = {x["key"]: x for x in d["items"]}
    ok("Felder einer Quell-Zeile (src, due, prio, overdue, edit)", it[f"r:{r_over}"]["src"] == db1 and it[f"r:{r_over}"]["overdue"] is True
       and it[f"r:{r_over}"]["prio"] == 3 and it[f"r:{r_today}"]["prio"] == 1 and it[f"r:{r_over}"]["edit"] is True
       and it[f"r:{r_over}"]["text"] == "Überfällig", it[f"r:{r_over}"])
    ok("Datum mit Uhrzeit: due + time in der Zeitzone der Anfrage", it[f"r:{r_time}"]["due"] == D(1) and it[f"r:{r_time}"].get("time") == "09:30",
       it[f"r:{r_time}"])
    ok("Personen als Handle + people mit Anzeigenamen", it[f"r:{r_later}"]["assignees"] == [handle(A)]
       and d["people"][handle(A)] == {"name": "Anna Quelle", "avatar": None, "me": True, "remote": False}, d.get("people"))
    srcs = {x["db"]: x for x in d["sources"]}
    ok("sources mit map (Apps: offline abhaken)", srcs[db1]["map"] == {"done": "st", "done_type": "status", "done_value": "fertig", "open_value": "neu",
       "due": "fl", "prio": "pr", "prio_values": {"1": "h", "2": "m", "3": "n"}, "assignee": "wer"} and srcs[db_cb]["map"]["done_value"] is True
       and srcs[db_sel]["map"]["done_value"] == "x" and srcs[db_sel]["map"]["open_value"] == "o", d["sources"])

    # geteilte Datenbank: Bert (Bearbeiten), Cora (Lesen)
    req("PUT", f"/api/notes/{db1}/shares", A, {"type": "user", "target": handle(B), "role": "edit"})
    req("PUT", f"/api/notes/{db1}/shares", A, {"type": "user", "target": handle(C), "role": "view"})
    r_b = row(A, db1, "Für Bert", {"st": "neu", "wer": [handle(B)]})
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", B, {})
    ok(f"geteilte DB mit Personen-Spalte: Standard 'assigned' ({s})", s == 200 and d["scope"] == "assigned" and d["own"] is False, d)
    d = tasks(B)
    ok("assigned: nur die Bert zugewiesene Zeile", [x["key"] for x in d["items"]] == [f"r:{r_b}"], [x["key"] for x in d["items"]])
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", B, {"scope": "all"})
    ok(f"umschalten auf 'all' ({s})", s == 200 and d["scope"] == "all")
    ok("all: alle offenen Zeilen", len([x for x in tasks(B)["items"] if x.get("src") == db1]) == 6)
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", B, {"scope": "irgendwas"})
    ok(f"unbekannter Umfang -> 400 ({s})", s == 400)
    s, d = req("PUT", f"/api/notes/{db_cb}/tasksource", B, {})
    ok(f"ohne Zugriff -> 404 ({s})", s == 404)
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", B, {"map": {"done": "st"}})
    ok(f"Bert (Bearbeiten) darf die Zuordnung aendern ({s})", s == 200 and d["map"] == {"done": "st"}, d)
    req("PUT", f"/api/notes/{db1}/tasksource", A, {"map": sch["tasks"]})
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", C, {"map": {"done": "st"}})
    ok(f"Cora (Lesen) darf die Zuordnung nicht aendern ({s})", s == 403)
    s, d = req("PUT", f"/api/notes/{db1}/tasksource", C, {})
    ok(f"Cora (Lesen) darf abonnieren ({s})", s == 200 and d["scope"] == "assigned")
    req("PATCH", f"/api/notes/{r_b}", A, {"props": {"wer": [handle(B), handle(C)]}})
    d = tasks(C)
    ok("Cora sieht die zugewiesene Zeile, edit=false", [x["key"] for x in d["items"]] == [f"r:{r_b}"] and d["items"][0]["edit"] is False, d["items"])
    s, _ = req("PATCH", f"/api/tasks/src/{r_b}", C, {"done": True})
    ok(f"Cora (Lesen) hakt nicht ab: 403 ({s})", s == 403)
    s, _ = req("PATCH", f"/api/tasks/src/{r_over}", C, {"done": True})
    ok(f"Cora: andere Zeile (Lesen) ebenso 403 ({s})", s == 403)
    s, _ = req("PATCH", f"/api/tasks/src/{c_open}", B, {"done": True})
    ok(f"Bert: Zeile ohne Zugriff -> 404 ({s})", s == 404)

    # --- 4. Abhaken -------------------------------------------------------------------------------------------------
    s, d = req("PATCH", f"/api/tasks/src/{r_b}", B, {"done": True, "today": TODAY.isoformat()})
    ok(f"Bert hakt ab ueber Status-Gruppe -> 'fertig' ({s})", s == 200 and d == {"ok": True, "src": db1, "done": True, "done_value": "fertig"}, d)
    pv = req("GET", f"/api/notes/{r_b}", A)[1]["props"]
    ok("Zeile hat Status 'fertig', Personen bleiben (Merge-PATCH)", pv.get("st") == "fertig" and pv.get("wer") == [handle(B), handle(C)], pv)
    with connect() as c:
        rd = c.execute("SELECT db_id FROM row_done WHERE note_id=?", (r_b,)).fetchone()
    ok("row_done beim Abhaken", rd and rd[0] == db1, rd)
    s, d = req("PATCH", f"/api/tasks/src/{r_b}", B, {"done": False})
    pv = req("GET", f"/api/notes/{r_b}", A)[1]["props"]
    with connect() as c:
        rd = c.execute("SELECT 1 FROM row_done WHERE note_id=?", (r_b,)).fetchone()
    ok(f"Wieder oeffnen -> open_value 'neu', row_done weg ({s})", s == 200 and pv.get("st") == "neu" and rd is None, pv)
    s, d = req("PATCH", f"/api/tasks/src/{c_open}", A, {"done": True})
    pv = req("GET", f"/api/notes/{c_open}", A)[1]["props"]
    ok(f"Haekchen abhaken ({s})", s == 200 and pv.get("ok") is True and d["done_value"] is True, pv)
    req("PATCH", f"/api/tasks/src/{c_open}", A, {"done": False})
    ok("Haekchen wieder oeffnen", "ok" not in req("GET", f"/api/notes/{c_open}", A)[1]["props"])
    s, d = req("PATCH", f"/api/tasks/src/{s_open}", A, {"done": True})
    ok(f"Auswahl abhaken -> done_values[0] ({s})", s == 200 and req("GET", f"/api/notes/{s_open}", A)[1]["props"].get("s") == "x")
    req("PATCH", f"/api/tasks/src/{s_open}", A, {"done": False})
    ok("Auswahl wieder oeffnen -> erste offene Option", req("GET", f"/api/notes/{s_open}", A)[1]["props"].get("s") == "o")
    s, d = req("PATCH", f"/api/tasks/src/{r_none}", A, {"due": D(3), "prio": 1, "text": "Umbenannt"})
    n = req("GET", f"/api/notes/{r_none}", A)[1]
    ok(f"due/prio/text ueber die Huelle ({s})", s == 200 and n["props"].get("fl") == D(3) and n["props"].get("pr") == "h" and n["title"] == "Umbenannt", n.get("props"))
    for body in ({"done": "ja"}, {"prio": 7}, {"due": "morgen"}, {}):
        s, _ = req("PATCH", f"/api/tasks/src/{r_none}", A, body)
        ok(f"ungueltig {body} -> 400 ({s})", s == 400)
    s, _ = req("PATCH", f"/api/tasks/src/{tid}", A, {"done": True})
    ok(f"Inbox-ID ist keine Zeile -> 404 ({s})", s == 404)
    s, _ = req("PATCH", f"/api/tasks/src/-5", A, {"done": True})
    ok(f"fremde Instanz (negativ) -> 400 ({s})", s == 400)
    # Board ziehen = Props-PATCH der Seite: row_done ebenso
    req("PATCH", f"/api/notes/{r_today}", A, {"props": {"st": "fertig"}})
    with connect() as c:
        rd = c.execute("SELECT 1 FROM row_done WHERE note_id=?", (r_today,)).fetchone()
    ok("Props-PATCH (Board) auf erledigt -> row_done", rd is not None)

    # Widget src:"tasks" (include_sources)
    blocks = [{"id": "w1", "type": "widget", "data": {"v": 1, "kind": "metric", "title": "o", "cfg": {"src": "tasks", "agg": "open"}}},
              {"id": "w2", "type": "widget", "data": {"v": 1, "kind": "metric", "title": "w", "cfg": {"src": "tasks", "agg": "done_week"}}},
              {"id": "w3", "type": "widget", "data": {"v": 1, "kind": "metric", "title": "i", "cfg": {"src": "tasks", "agg": "open", "include_sources": False}}},
              {"id": "w4", "type": "widget", "data": {"v": 1, "kind": "progress", "title": "p", "cfg": {"src": "tasks"}}}]
    pg = req("POST", "/api/notes", A, {"title": "Mein Tag"})[1]["id"]
    req("PATCH", f"/api/notes/{pg}", A, {"format": "blocks", "body": json.dumps({"blocks": blocks})})
    w = req("GET", f"/api/notes/{pg}/widgets?tz={TZN}", A)[1]["widgets"]
    n_open = len(tasks()["items"])
    ok(f"Widget: offen = Inbox + Quellen ({w['w1'].get('value')}/{n_open})", w["w1"]["value"] == n_open and n_open == 8, w["w1"])
    # row_done: r_today (Board) + drei schon erledigt angelegte Zeilen (erstmals erledigt gesehen); wieder geoeffnete zaehlen nicht
    ok(f"Widget: diese Woche erledigt zaehlt row_done ({w['w2'].get('value')})", w["w2"]["value"] == 4, w["w2"])
    ok(f"Widget include_sources:false = nur Inbox ({w['w3'].get('value')})", w["w3"]["value"] == 1, w["w3"])
    body = req("GET", f"/api/notes/{pg}", A)[1]["body"]
    ok("include_sources:false gespeichert, Standard ohne Schluessel", '"include_sources": false' in body.replace('":false', '": false')
       and body.count("include_sources") == 1, body[:400])
    # App-Schluessel
    tok = req("POST", "/api/settings/tokens", A, {"name": "Test"})[1]["token"]
    s, d = req("GET", "/api/app/today?sources=1", bearer=tok)
    ok(f"/api/app/today?sources=1: items gemischt ({s})", s == 200 and any(x["kind"] == "row" for x in d.get("items", [])) and "tasks" in d, d)
    s, d = req("GET", "/api/app/today", bearer=tok)
    ok("/api/app/today ohne sources: wie bisher", s == 200 and "items" not in d)
    s, d = req("POST", f"/api/app/tasks/src/{r_later}/done", bearer=tok, body={"done": True})
    ok(f"App-Schluessel hakt Quell-Zeile ab ({s})", s == 200 and req("GET", f"/api/notes/{r_later}", A)[1]["props"].get("st") == "fertig", d)
    req("POST", f"/api/app/tasks/src/{r_later}/done", bearer=tok, body={"done": False})

    # --- 5. Neue Aufgabe in einer Quelle -----------------------------------------------------------------------------
    tpl = req("POST", "/api/notes", A, {"parent_id": db1, "title": "Vorlage", "props": {"st": "lauf"}})[1]["id"]
    req("PATCH", f"/api/notes/{tpl}", A, {"template": True})
    sc = schema_of(db1)
    sc["default_tpl"] = tpl
    req("PATCH", f"/api/notes/{db1}", A, {"schema": sc})
    s, d = req("POST", "/api/tasks", A, {"text": "Neu aus Liste", "src": db1, "due": D(2), "prio": 1})
    n = req("GET", f"/api/notes/{d.get('id')}", A)[1] if s == 200 else {}
    ok(f"POST /api/tasks {{src}}: Zeile mit Standardvorlage, Datum, Prio ({s})", s == 200 and d.get("kind") == "row" and n.get("title") == "Neu aus Liste"
       and n["props"].get("st") == "lauf" and n["props"].get("fl") == D(2) and n["props"].get("pr") == "h", n.get("props"))
    req("PUT", f"/api/notes/{db1}/tasksource", B, {"scope": "assigned"})
    s, d = req("POST", "/api/tasks", B, {"text": "Bert legt an", "src": db1})
    n = req("GET", f"/api/notes/{d.get('id')}", A)[1] if s == 200 else {}
    ok(f"Bert (assigned): neue Zeile ist ihm zugewiesen ({s})", s == 200 and n["props"].get("wer") == [handle(B)], n.get("props"))
    ok("... und erscheint in seiner Liste", f"r:{d.get('id')}" in [x["key"] for x in tasks(B)["items"]])
    s, _ = req("POST", "/api/tasks", C, {"text": "Cora", "src": db1})
    ok(f"Cora (Lesen) legt nicht an: 403 ({s})", s == 403)
    s, _ = req("POST", "/api/tasks", B, {"text": "x", "src": db_cb})
    ok(f"ohne Zugriff: 404 ({s})", s == 404)
    s, _ = req("POST", "/api/tasks", A, {"text": "", "src": db1})
    ok(f"leerer Text: 400 ({s})", s == 400)
    s, _ = req("POST", "/api/tasks", A, {"text": "x", "src": True})
    ok(f"src kein Integer: 400 ({s})", s == 400)

    # Entzug der Freigabe: leer, Abo entfernt
    req("DELETE", f"/api/notes/{db1}/shares", A, {"type": "user", "target": handle(C)})
    d = tasks(C)
    with connect() as c:
        left = c.execute("SELECT COUNT(*) FROM task_source WHERE uid=?", (C,)).fetchone()[0]
    ok("Freigabe entzogen: nichts mehr, Abo entfernt", d["items"] == [] and d["sources"] == [] and left == 0, d)

    # --- Grenzen -----------------------------------------------------------------------------------------------------
    with connect() as c, c:
        now = time.time()
        for i in range(520):
            c.execute("INSERT INTO note (uid, title, body, format, parent_id, sort, kind, props, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (A, f"Massen {i}", '{"blocks": []}', "blocks", db_cb, now + i, "page", "{}", now, now))
    d = tasks()
    src = {x["db"]: x for x in d["sources"]}[db_cb]
    ok(f"500 offene Zeilen je Quelle, truncated ({src['count']})", src["count"] == 500 and src["truncated"] is True
       and len([x for x in d["items"] if x.get("src") == db_cb]) == 500, {k: src[k] for k in ("count", "truncated")})
    ids = []
    for i in range(17):
        ids.append(mkdb(A, f"Q{i}", cbs))
        s, d = req("PUT", f"/api/notes/{ids[-1]}/tasksource", A, {"map": {"done": "ok"}})
    ok(f"20 Quellen erlaubt ({s})", s == 200)
    x = mkdb(A, "Q21", cbs)
    s, d = req("PUT", f"/api/notes/{x}/tasksource", A, {"map": {"done": "ok"}})
    ok(f"21. Quelle -> 409 ({s})", s == 409, d)
    s, d = req("GET", "/api/tasks/sources", A)
    ok(f"GET /api/tasks/sources ({s})", s == 200 and len(d["sources"]) == 20 and d["max"] == 20)
    s, _ = req("DELETE", f"/api/notes/{ids[0]}/tasksource", A)
    ok(f"Abo entfernen ({s})", s == 200 and len(req("GET", "/api/tasks/sources", A)[1]["sources"]) == 19)

    # --- 6. Galerie --------------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/gallery/aufgaben-projekte/use", B, {"examples": True})
    ok(f"Galerie 'Aufgaben & Projekte' angelegt ({s})", s == 200, d)
    srcs = req("GET", "/api/tasks/sources", B)[1]["sources"]
    g = [x for x in srcs if x["title"] == "Aufgaben"]
    ok("Galerie: Aufgaben-DB abonniert ('all', eigene) mit Status-Zuordnung", g and g[0]["scope"] == "all" and g[0]["map"]["done_type"] == "status"
       and g[0]["map"]["done_value"] == "erledigt" and g[0]["map"]["assignee"] == "zugewiesen", srcs)
    items = [x for x in tasks(B)["items"] if g and x.get("src") == g[0]["db"]]
    ok(f"Galerie: offene Beispiel-Aufgaben in der Liste ({len(items)})", len(items) == 6, [x["text"] for x in items])

    # --- Kontoloeschung -----------------------------------------------------------------------------------------------
    from saga.userdata import delete_user_data
    delete_user_data(C, drop_user=True)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM task_source WHERE uid=?", (C,)).fetchone()[0]
    ok("Kontoloeschung raeumt task_source", n == 0)


def scan():
    hits = []
    for who, what, txt in SEEN:
        for u in (A, B, C):
            if u != who and u in txt:
                hits.append((who, what, u, txt[max(0, txt.find(u) - 80):txt.find(u) + 40]))
    ok(f"Leck-Scan: {len(SEEN)} Antworten ohne Anmeldenamen anderer Konten", not hits, hits[:3])
    ok("Gegenprobe: eigener Marker kommt vor (Scan wirkt)", any(who == A and A in txt for who, _, txt in SEEN))


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        us = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'aq-%'").fetchall()]
    for u in us:
        delete_user_data(u, drop_user=True)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'aq-%'")
        c.execute("DELETE FROM task_source WHERE uid LIKE 'aq-%'")


if __name__ == "__main__":
    main()
    print(f"\nAUFGABENQUELLEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
