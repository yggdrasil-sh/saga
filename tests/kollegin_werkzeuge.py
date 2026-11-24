"""Werkzeug-Registry fuer MCP und Kollegin + interner Auftrags-Tok (2.6, Bugs-DB 2266 K-A3, Konzept K5/G2/G3, M12).

Prueft: MCP tools/list byteweise wie die Goldvorlage (tests/mcp_gold.py, alle Bereichs-Kombinationen); mcp.TOOLS/BY_NAME/
DESTRUCTIVE wie bisher; Kollegin-Liste genau laut Plan, Kollegin ∩ DESTRUCTIVE = nur update_row (Befund 4); kein Werkzeug
zum Teilen/Einladen/Rechte/Anrufen (RT-01); Attrappe mit mcp=False nie in tools/list und mit API-Token nicht aufrufbar
(RT-42); JobTok nur fuer lokale Konten, Bereiche nur der Plan-Werkzeuge, nie mcp/webhooks; RT-27 komplett (ausserhalb
roots 404, nur lesbar geteilt 403, roots=[] liest/schreibt nichts, create_page ohne Eltern 403, Lesen ausserhalb reads
404, kein Admin-Werkzeug, negative ID 404); update_row nur mit rev (veraltet 409); Akteur svc:kollegin an der Seite;
Konto gesperrt / ueberall abgemeldet / KI aus -> job_tok ungueltig, laufender Tok sofort ungueltig (RT-07); Webhook mit
roots=[] wird nie angelegt; create_event mit fester iCal-UID: zweimal derselbe Schritt = ein Termin (CalDAV im Prozess
nachgestellt, If-None-Match: *); check_plan und Perf "Kollegin: Plan pruefen" (limits.json).
OFFEN (K-A2/K-A6): job_change-Stempel je own-Werkzeug, /api/ai/jobs/* fuer sga_-Tokens gesperrt (Endpunkte gibt es noch nicht).
Laeuft im Prozess gegen die Datenbank aus DATABASE_URL (Wegwerf-Konten kw-*), stellt die KI-Einstellung wieder her.
Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> kollegin_werkzeuge.py"""
import json
import os
import re
import sys
import time

import db

db.wait_ready()
import auth  # noqa: E402
import hkcal  # noqa: E402
from saga import actor, api_calendar, api_struct, api_v1, apitok, mcp, sharing, tools, webhooks  # noqa: E402
from saga.actor import Actor, acting  # noqa: E402
from saga.api_v1 import Fail  # noqa: E402
from saga.userdata import delete_user_data  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools", "perf"))
import kollegin_plan  # noqa: E402
import mcp_gold  # noqa: E402

A, B = "kw-anna", "kw-bert"
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def offen(n):
    print("OFFEN", n, flush=True)


def status(fn, *a, **kw):
    """-> HTTP-Status eines Werkzeug-Aufrufs (200 = ohne Fehler) und Ergebnis/Fehlertext."""
    try:
        return 200, fn(*a, **kw)
    except Fail as f:
        return f.status, f.msg


def pg(sql, args=()):
    with db.connect() as c, c:
        return c.execute(sql, args).fetchall()


FULL_A = apitok.Tok(10 ** 9, A, "voll", "sga_test", apitok.SCOPES, None, None)
FULL_B = apitok.Tok(10 ** 9 + 1, B, "voll", "sga_test", apitok.SCOPES, None, None)
JID = [700000]


def jt(tools_, targets=(), reads=(), uid=A):
    JID[0] += 1
    return tools.job_tok(uid, JID[0], tools_, targets, reads)


def page(tok, title, parent=None, md="Text"):
    return api_v1.page_create(tok, {"title": title, "markdown": md, "parent_id": parent})[1]["id"]


KOLLEGIN = {"search_pages", "get_page", "list_databases", "get_database_schema", "query_database", "get_widgets", "list_tasks",
            "list_calendars", "list_events", "create_page", "append_to_page", "update_page_title", "set_checkbox", "create_row",
            "update_row", "create_task", "complete_task", "create_event"}
OLD_DESTRUCTIVE = {"replace_page_content", "update_block", "trash_page", "update_event", "delete_event", "update_widget",
                   "update_row", "update_database_schema"}


def part_registry():
    with open(mcp_gold.GOLD, encoding="utf-8") as f:
        gold = json.load(f)
    mcp_gold.compare(gold, ok)
    names = [x["name"] for x in gold["full"]]
    ok("mcp.TOOLS: gleiche Namen, Reihenfolge, Tupel (name, Bereich, Text, Schema, Pflicht, schreibend)",
       [x[0] for x in mcp.TOOLS] == names and all(len(x) == 6 for x in mcp.TOOLS)
       and all(mcp.BY_NAME[x["name"]][2] == x["description"] for x in gold["full"]))
    ok("mcp.DESTRUCTIVE wie bisher", mcp.DESTRUCTIVE == OLD_DESTRUCTIVE == tools.destructive_names(), mcp.DESTRUCTIVE)
    ok("mcp._data / Helfer weiter erreichbar (datenrahmen.py)", mcp._data is tools._data and mcp.call_tool is tools.call)
    kol = {x.name for x in tools.for_kollegin()}
    ok("Kollegin-Liste genau laut Plan K-A3", kol == KOLLEGIN, sorted(kol ^ KOLLEGIN))
    ok("Kollegin ∩ DESTRUCTIVE = nur update_row (Befund 4)", kol & mcp.DESTRUCTIVE == {"update_row"}, kol & mcp.DESTRUCTIVE)
    ok("gesperrt: Papierkorb, Archiv, Ersetzen, Bloecke, Widget, Schema, Termine aendern/loeschen, Kommentar, Bild",
       not kol & {"trash_page", "archive_page", "replace_page_content", "update_block", "update_widget", "update_database_schema",
                  "update_event", "delete_event", "add_comment", "get_image"})
    bad = [x.name for x in tools.REGISTRY if re.search(r"share|invite|role|call|tel", x.name)]
    ok("RT-01: kein Werkzeug share|invite|role|call|tel", not bad, bad)
    ok("Klassen: Lesen = nicht schreibend, Kollegin nur read/own (nach aussen in K-A: keine)",
       all((x.klass == "read") == (not x.write) for x in tools.REGISTRY) and all(x.klass in ("read", "own") for x in tools.for_kollegin()))
    ok("Rueckgaengig-Klasse je schreibendem Werkzeug (undo|delete_only|final), Lesen ohne",
       all((x.undo in tools.UNDOS) if x.write else x.undo is None for x in tools.REGISTRY))
    ok("kein Werkzeug mit Admin-/Server-Bereich (nur Datenbereiche)",
       all(s in apitok.SCOPES and s not in ("mcp", "webhooks") for x in tools.REGISTRY for s in x.scopes()))
    try:
        tools.Tool("x", "read:pages", "", {}, [], True)            # schreibend, aber Klasse read
        ok("Tool prueft Klasse/schreibend", False)
    except ValueError:
        ok("Tool prueft Klasse/schreibend", True)

    # RT-42: Attrappe mit mcp=False (wie spaeter der Chat-Schritt, K-E)
    dummy = tools.Tool("kollegin_chat_attrappe", "write:pages", "Attrappe", {}, [], True, klass="out", undo="final", mcp=False,
                       kollegin=True)
    tools.register(dummy)
    try:
        lst = mcp.handle(FULL_A, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        ok("RT-42: mcp=False nie in tools/list", dummy.name not in {x["name"] for x in lst} and len(lst) == len(gold["full"]))
        r = mcp.handle(FULL_A, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": dummy.name, "arguments": {}}})
        ok("RT-42: tools/call mit sga_-Token -> Unknown tool", (r.get("error") or {}).get("code") == -32602, r)
        ok("RT-42: tools.call mit API-Token -> 404", status(tools.call, FULL_A, dummy.name, {})[0] == 404)
        ok("RT-42: Attrappe nicht in for_mcp, aber in for_kollegin", dummy not in tools.for_mcp() and dummy in tools.for_kollegin())
    finally:
        tools.unregister(dummy.name)
    ok("nach unregister: Registry wie vorher", [x.name for x in tools.for_mcp()] == names)
    offen("RT-42: /api/ai/jobs/* (approve/outbox/inbox) fuer sga_-Tokens gesperrt - Endpunkte kommen mit K-A2/K-B")


def part_tok():
    for bad in ("anna@fremd.example", "svc:kollegin", ""):
        try:
            tools.job_tok(bad, 1, ["get_page"], [1])
            ok(f"job_tok {bad!r} abgelehnt", False)
        except ValueError:
            ok(f"job_tok {bad!r} abgelehnt (nur lokale Konten, Auflage K-A1)", True)
    try:
        apitok.JobTok(1, "x@y", [], [])
        ok("JobTok mit @ abgelehnt", False)
    except ValueError:
        ok("JobTok mit @ abgelehnt", True)
    for bad in (["trash_page"], ["add_comment"], ["share_page"], ["get_image"]):
        try:
            jt(bad, [1])
            ok(f"job_tok mit {bad[0]} abgelehnt", False)
        except ValueError:
            ok(f"job_tok mit gesperrtem/unbekanntem Werkzeug {bad[0]} abgelehnt", True)
    for sc in (["mcp"], ["webhooks"], ["write:files"]):
        try:
            apitok.JobTok(5, A, sc, [])
            ok(f"JobTok mit Bereich {sc[0]} abgelehnt", False)
        except ValueError:
            ok(f"JobTok mit Bereich {sc[0]} abgelehnt (nie Admin/Server)", True)
    t = jt(["get_page", "append_to_page"], [11], [12])
    ok("Bereiche nur der Plan-Werkzeuge", t is not None and t.scopes == {"read:pages", "write:pages"}, t and t.scopes)
    ok("JobTok: id = -job_id, prefix job, roots = Ziele, read_roots = Ziele + gelesen",
       t.id == -t.job_id and t.prefix == "job" and t.roots == [11] and t.read_roots == [11, 12] and t.tools == {"get_page", "append_to_page"})
    ok("may: own nur Ziele, read Ziele + gelesen, out nie",
       t.may("note:11", "own") and not t.may("note:12", "own") and t.may("note:12", "read") and not t.may("note:13", "read")
       and not t.may("note:11", "out") and not t.may("note:-11", "read") and not t.may("tasks", "own"))
    e = jt(["get_page"])
    ok("roots=[] bleibt leer (nie None, M12)", e.roots == [] and e.read_roots == [])
    plain = apitok.Tok(1, A, "", "", ["read:pages"], [], None)
    ok("apitok.Tok: [] bleibt [] (nichts), None bleibt None (alles)", plain.roots == [] and FULL_A.roots is None)


def part_rt27():
    a_root = page(FULL_A, "KW Sommerfest")
    a_child = page(FULL_A, "KW Sommerfest Unterseite", a_root)
    a_other = page(FULL_A, "KW Finanzen geheim")
    a_read = page(FULL_A, "KW Einkaufsliste")
    b_view = page(FULL_B, "KW Bert nur lesen")
    b_edit = page(FULL_B, "KW Bert bearbeiten")
    with db.connect() as c, c:
        e1 = sharing.set_share(c, B, b_view, "user", A, "view")
        e2 = sharing.set_share(c, B, b_edit, "user", A, "edit")
    ok("Freigaben B -> A angelegt", e1 is None and e2 is None, (e1 and e1.body, e2 and e2.body))
    dbid = api_struct.database_create(FULL_A, {"title": "KW Mitbringliste", "parent_id": a_root,
                                               "properties": [{"name": "Erledigt", "type": "checkbox"}]})[1]["id"]
    row = api_v1.row_create(FULL_A, dbid, {"title": "Grillkohle"})[1]["id"]
    W = ["get_page", "append_to_page", "update_page_title", "create_page", "search_pages", "list_databases", "query_database",
         "update_row", "create_row", "create_task"]

    t = jt(W, [a_root], [a_read])
    st, _ = status(tools.call, t, "append_to_page", {"id": a_root, "markdown": "- Kohle"})
    ok("Ziel-Seite: anfuegen erlaubt", st == 200, _)
    ok("Unterseite des Ziels: erlaubt (Seitenbaum)", status(tools.call, t, "update_page_title", {"id": a_child, "title": "KW Unter neu"})[0] == 200)
    ok("RT-27: Schreiben ausserhalb roots -> 404", status(tools.call, t, "append_to_page", {"id": a_other, "markdown": "x"})[0] == 404)
    ok("RT-27: Lesen ausserhalb reads -> 404", status(tools.call, t, "get_page", {"id": a_other})[0] == 404)
    ok("Lesen in reads: erlaubt", status(tools.call, t, "get_page", {"id": a_read})[0] == 200)
    ok("RT-27: Schreiben auf nur Gelesenes (reads) -> 404", status(tools.call, t, "append_to_page", {"id": a_read, "markdown": "x"})[0] == 404)
    ok("RT-27: negative ID -> 404", status(tools.call, t, "get_page", {"id": -5})[0] == 404
       and status(tools.call, t, "append_to_page", {"id": -a_root, "markdown": "x"})[0] == 404
       and status(tools.call, t, "get_page", {"id": 0})[0] == 404)
    ok("RT-27: create_page ohne Eltern -> 403", status(tools.call, t, "create_page", {"title": "KW oben"})[0] == 403)
    ok("create_page unter dem Ziel: erlaubt", status(tools.call, t, "create_page", {"title": "KW neu", "parent_id": a_root})[0] == 200)
    ok("RT-27: create_page unter fremdem Baum -> 404", status(tools.call, t, "create_page", {"title": "KW x", "parent_id": a_other})[0] == 404)
    srch = status(tools.call, t, "search_pages", {"query": "KW"})
    ok("Suche: nur Ziele und Gelesenes", srch[0] == 200 and "KW Finanzen geheim" not in srch[1] and "KW Einkaufsliste" in srch[1], srch)
    ok("RT-27: Werkzeug ausserhalb des Plans -> 404", status(tools.call, jt(["get_page"], [a_root]), "append_to_page",
                                                            {"id": a_root, "markdown": "x"})[0] == 404)
    ok("RT-27: kein Admin-Werkzeug (unbekannt -> 404)", status(tools.call, t, "admin_users", {})[0] == 404
       and status(tools.call, t, "trash_page", {"id": a_root})[0] == 404)

    tb = jt(["get_page", "append_to_page"], [b_view, b_edit])
    ok("RT-27: nur lesbar geteilt -> 403", status(tools.call, tb, "append_to_page", {"id": b_view, "markdown": "x"})[0] == 403)
    ok("lesbar geteilt: lesen erlaubt", status(tools.call, tb, "get_page", {"id": b_view})[0] == 200)
    ok("bearbeitbar geteilt: anfuegen erlaubt (Person ∩ Plan)", status(tools.call, tb, "append_to_page", {"id": b_edit, "markdown": "x"})[0] == 200)
    b_priv = page(FULL_B, "KW Bert privat")
    ok("fremde Seite ohne Freigabe trotz Ziel -> 404 (nie mehr als die Person)",
       status(tools.call, jt(["get_page", "append_to_page"], [b_priv]), "get_page", {"id": b_priv})[0] == 404
       and status(tools.call, jt(["get_page", "append_to_page"], [b_priv]), "append_to_page", {"id": b_priv, "markdown": "x"})[0] == 404)

    e = jt(W, [], [])
    ok("RT-27: roots=[] liest nichts (get_page, Suche, Datenbanken)",
       status(tools.call, e, "get_page", {"id": a_root})[0] == 404 and status(tools.call, e, "query_database", {"id": dbid})[0] == 404
       and '"id"' not in status(tools.call, e, "search_pages", {"query": "KW"})[1]
       and "KW Mitbringliste" not in status(tools.call, e, "list_databases", {})[1])
    ok("Kontrolle: derselbe Inhalt ist fuer die Person sichtbar", "KW Mitbringliste" in tools.call(FULL_A, "list_databases", {}))
    ok("RT-27: roots=[] schreibt nichts (anfuegen, Titel, Unterseite, Zeile)",
       status(tools.call, e, "append_to_page", {"id": a_root, "markdown": "x"})[0] == 404
       and status(tools.call, e, "update_page_title", {"id": a_root, "title": "x"})[0] == 404
       and status(tools.call, e, "create_page", {"title": "x", "parent_id": a_root})[0] == 404
       and status(tools.call, e, "create_page", {"title": "x"})[0] == 403
       and status(tools.call, e, "create_row", {"database_id": dbid, "title": "x"})[0] == 404)

    # update_row: nur mit rev (Vorpruefung)
    rev = api_v1.page_read(FULL_A, row)[1]["rev"]
    ok("update_row ohne rev -> 400", status(tools.call, t, "update_row", {"id": row, "properties": {"Erledigt": True}})[0] == 400)
    ok("update_row mit veraltetem rev -> 409", status(tools.call, t, "update_row", {"id": row, "rev": rev - 1, "properties": {"Erledigt": True}})[0] == 409)
    st, out = status(tools.call, t, "update_row", {"id": row, "rev": rev, "properties": {"Erledigt": True}})
    ok("update_row mit aktuellem rev: erlaubt", st == 200, out)
    rev = api_v1.page_read(FULL_A, row)[1]["rev"]
    api_v1.page_update(FULL_A, row, {"title": "Grillkohle (2 Saecke)"})          # Person aendert dazwischen den Titel
    ok("update_row: Person hat dazwischen den Titel geaendert -> 409",
       status(tools.call, t, "update_row", {"id": row, "rev": rev, "properties": {"Erledigt": False}})[0] == 409)
    offen("update_row: dazwischen nur Eigenschaften geaendert erhoeht rev nicht - erkennt erst der Vorher-Stand (K-B)")
    ok("MCP update_row (API-Token) weiter ohne rev", status(tools.call, FULL_A, "update_row", {"id": row, "properties": {"Erledigt": False}})[0] == 200)

    # Akteur: Schreiben ueber den JobTok traegt svc:kollegin (K-A1-Spalten), MCP nicht
    tools.call(t, "append_to_page", {"id": a_root, "markdown": "- Kollegin"})
    k = pg("SELECT updated_by, updated_actor FROM note WHERE id=?", (a_root,))[0]
    ok("JobTok schreibt als svc:kollegin im Auftrag der Person", tuple(k) == (A, "svc:kollegin"), k)
    tools.call(FULL_A, "append_to_page", {"id": a_root, "markdown": "- selbst"})
    ok("API-Token (MCP) schreibt ohne Dienst-Akteur", tuple(pg("SELECT updated_by, updated_actor FROM note WHERE id=?", (a_root,))[0]) == (A, None))
    ok("nach dem Aufruf kein Akteur mehr gesetzt", actor.current() is None)
    offen("jedes own-Werkzeug unter acting() = genau ein job_change-Stempel - Stempel in bus.fire kommt mit K-A2")

    # RT-01: geteilte Seite will "Finanzen" teilen
    tk, probs = tools.check_plan(A, 4242, [{"tool": "get_page", "args": {"id": a_root}},
                                           {"tool": "share_page", "args": {"id": a_other, "with": "ben@x.org"}}], [a_root])
    ok("RT-01: check_plan meldet Teilen als unbekanntes Werkzeug", [(p["n"], p["status"]) for p in probs] == [(1, 404)], probs)
    ok("RT-01: tools.call share_page -> 404", status(tools.call, tk, "share_page", {"id": a_other})[0] == 404)

    # check_plan
    good = kollegin_plan.plan(a_read, dbid, a_root, row, api_v1.page_read(FULL_A, row)[1]["rev"])
    tk, probs = tools.check_plan(A, 4243, good, [a_read])
    ok("check_plan: gueltiger 5-Schritte-Plan ohne Probleme", not probs and tk.roots == [a_root, row] and tk.read_roots == [a_root, row, a_read],
       (probs, tk and tk.roots))
    bad = [{"tool": "append_to_page", "args": {"id": b_view}}, {"tool": "get_page", "args": {"id": a_other}},
           {"tool": "create_page", "args": {"title": "x"}}, {"tool": "update_row", "args": {"id": row}},
           {"tool": "trash_page", "args": {"id": a_root}}, {"tool": "get_page", "args": "kaputt"},
           {"tool": "get_page", "args": {"id": True}}]
    _t, probs = tools.check_plan(A, 4244, bad, [])
    ok("check_plan: nur lesbar 403, ausserhalb 404, ohne Eltern 403, ohne rev 400, gesperrt 404, kaputt 400",
       sorted((p["n"], p["status"]) for p in probs) == [(0, 403), (1, 404), (2, 403), (3, 400), (4, 404), (5, 400), (6, 400)], probs)

    # Perf "Kollegin: Plan pruefen" (im Prozess)
    r = kollegin_plan.measure(A, good, [a_read], 40)
    lim = kollegin_plan.limit("S")
    print(f"PERF {json.dumps(r, ensure_ascii=False)}", flush=True)
    ok(f"Perf {kollegin_plan.NAME}: p95 {r['p95_ms']} ms <= {lim} ms", lim and r["p95_ms"] <= lim and not r["problems"], r)

    # Webhook mit roots=[] nie anlegen (gespeichert wuerde daraus "alles")
    out, err = webhooks.create_hook(A, {"url": "https://hook.example/x", "events": ["page.updated"]},
                                    tok=apitok.Tok(5, A, "", "", ["webhooks", "read:pages"], [], None))
    ok("Webhook mit roots=[] -> 403", out is None and err and err[0] == 403, (out, err))
    return a_root, a_other


def part_rt07(a_root):
    t = jt(["get_page"], [a_root])
    ok("vorher: Tok gueltig", t is not None and status(tools.call, t, "get_page", {"id": a_root})[0] == 200)
    pg("UPDATE users SET disabled=1 WHERE username=?", (A,))
    try:
        ok("RT-07: Konto gesperrt -> job_tok ungueltig (None)", jt(["get_page"], [a_root]) is None)
        ok("RT-07: laufender Tok sofort ungueltig (401)", status(tools.call, t, "get_page", {"id": a_root})[0] == 401)
        _tk, probs = tools.check_plan(A, 4245, [{"tool": "get_page", "args": {"id": a_root}}], [a_root])
        ok("RT-07: check_plan -> ungueltig", _tk is None and probs and probs[-1]["status"] == 401, probs)
    finally:
        pg("UPDATE users SET disabled=0 WHERE username=?", (A,))
    t = jt(["get_page"], [a_root])
    time.sleep(0.01)
    pg("UPDATE users SET logout_at=? WHERE username=?", (time.time(), A))
    ok("RT-07: ueberall abmelden nach Ausgabe -> Tok ungueltig", status(tools.call, t, "get_page", {"id": a_root})[0] == 401)
    ok("neu ausgestellt: wieder gueltig", status(tools.call, jt(["get_page"], [a_root]), "get_page", {"id": a_root})[0] == 200)
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    try:
        auth.save_setting("ai", dict(prev, enabled=False)); auth.settings(fresh=True)
        ok("RT-07: KI aus (Faehigkeit ai weg) -> job_tok None", jt(["get_page"], [a_root]) is None)
        auth.save_setting("ai", dict(prev, groups=["kw-niemand"])); auth.settings(fresh=True)
        ok("RT-07: Gruppe ohne KI -> job_tok None", jt(["get_page"], [a_root]) is None)
    finally:
        auth.save_setting("ai", prev); auth.settings(fresh=True)
    exp = tools.job_tok(A, 4246, ["get_page"], [a_root], expires=time.time() - 1)
    ok("abgelaufen -> None", exp is None)


def part_event():
    u1, u2 = tools.event_uid("12:3:1"), tools.event_uid("12:3:1")
    ok("event_uid: fest je Schluessel", u1 == u2 and u1 != tools.event_uid("12:4:1") and u1 != tools.event_uid("13:3:1"))
    ok("event_uid: nur [a-z0-9-], 8-80 Zeichen (Dateiname <uid>.ics)", re.fullmatch(r"[a-z0-9-]{8,80}", u1) is not None, u1)
    try:
        tools.event_uid("")
        ok("event_uid ohne Schluessel abgelehnt", False)
    except ValueError:
        ok("event_uid ohne Schluessel abgelehnt", True)
    store, puts = {}, []

    def fake_req(method, url, auth_, body=None, headers=None):          # CalDAV-Server im Prozess (If-None-Match: *)
        puts.append((method, url, dict(headers or {})))
        if method == "PUT":
            if (headers or {}).get("If-None-Match") == "*" and url in store:
                return 412, b"", {}, url
            store[url] = body
            return 201, b"", {"etag": f'"e{len(store)}"'}, url
        return 404, b"", {}, url
    saved = (hkcal._req, hkcal.check_url, api_calendar._find_cal, api_calendar._target)
    cal = {"href": "https://cal.test/dav/kw/kalender/"}
    hkcal._req, hkcal.check_url = fake_req, (lambda u: u)
    api_calendar._find_cal = lambda tok, key: (1, cal)
    api_calendar._target = lambda tok, aid, c: ({"username": "kw"}, "pw", c)
    try:
        t = jt(["create_event"])
        ok("create_event: Bereich write:calendar", t is not None and t.has("write:calendar"))
        args = {"calendar": "1-abc", "summary": "Sommerfest", "start": "2026-07-04T15:00"}
        k = Actor("service", "svc:kollegin", on_behalf_of=A, job_id=t.job_id, step=2, idem=f"{t.job_id}:2:1")
        with acting(k):
            r1 = status(tools.call, t, "create_event", dict(args))
            r2 = status(tools.call, t, "create_event", dict(args))
        ok("create_event 2x im selben Schritt -> beide ok", r1[0] == 200 and r2[0] == 200, (r1, r2))
        ok("create_event 2x mit gleichem Schluessel = 1 Termin (Befund 5)", len(store) == 1, list(store))
        href = next(iter(store), "")
        body = store.get(href) or b""
        body = body.decode() if isinstance(body, bytes) else str(body)
        ok("Termin-Datei = feste UID, If-None-Match: *", href.endswith(tools.event_uid(k.idem) + ".ics")
           and all(h.get("If-None-Match") == "*" for _m, _u, h in puts) and f"UID:{tools.event_uid(k.idem)}" in body, (href, puts[:1]))
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=t.job_id, step=3, idem=f"{t.job_id}:3:1")):
            tools.call(t, "create_event", dict(args))
        ok("anderer Schritt -> eigener Termin", len(store) == 2)
        n = len(store)
        tools.call(FULL_A, "create_event", dict(args))
        tools.call(FULL_A, "create_event", dict(args))
        ok("MCP (API-Token): weiter zufaellige UID je Aufruf", len(store) == n + 2)
        tools.call(t, "create_event", dict(args))
        ok("JobTok ohne Schluessel (kein Schritt): zufaellige UID", len(store) == n + 3)
    finally:
        hkcal._req, hkcal.check_url, api_calendar._find_cal, api_calendar._target = saved
    try:
        hkcal.save_event("https://cal.test/x/", "u", "p", {"summary": "x", "start": "2026-07-04", "allday": True}, ical_uid="../boese")
        ok("hkcal: unsichere UID abgelehnt", False)
    except hkcal.CalError:
        ok("hkcal: unsichere UID abgelehnt", True)
    offen("create_event 2x gegen echtes CalDAV (Radicale) - auf saga-test ohne Radicale (Bugs-DB 2347); hier im Prozess nachgestellt")


def cleanup():
    for u in (A, B):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)


def main():
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    cleanup()
    try:
        for u in (A, B):
            auth.create_user(u, "Kw-Pass-12345", u)
        auth.save_setting("ai", dict(prev, enabled=True, provider="openai", url="http://127.0.0.1:18290/v1", model="fake", groups=[], caps={}))
        auth.settings(fresh=True)
        part_registry()
        part_tok()
        a_root, _ = part_rt27()
        part_event()
        part_rt07(a_root)
    finally:
        auth.save_setting("ai", prev)
        auth.settings(fresh=True)
        cleanup()
    print(f"\nKollegin-Werkzeuge {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER", flush=True)
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
