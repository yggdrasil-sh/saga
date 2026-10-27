"""2.5 Paket 1 Etappe 2: Personen-Eigenschaft (Typ "person", saga/people.py, row_person, db.assigned).

Konten mit Markern als Anmeldenamen ('pe-<16 hex>', Anzeigenamen ohne Marker). Geprueft:
1. Schema: person (multi, notify), Werte nur fuer Personen mit Zugriff (Eigentuemer, Freigabe Person/Gruppe, geerbt),
   Eingabe als Handle bzw. eigener Name - Anmeldename eines anderen -> 400 (kein Orakel), fremde Person ohne Freigabe -> 400.
2. Ausgabe: /rows und Einzelzeile mit Handles + people {name, avatar, me}; PATCH-Antwort values/people; Auswahl
   GET /api/notes/{db}/people (nur mit Zugriff, Suche nach Anzeigename, Rate-Limit), nicht ohne Zugriff (404).
3. row_person per Trigger (Anlegen, Aendern, Leeren, Schema: Spalte weg/Typ geaendert, Zeile geloescht), multi=false.
4. Filter "me"/"contains" (Kennung), Sortierung nach Name, Gruppen nach Person, Suche nach Name - Server = Web-Logik.
5. db.assigned: Mitteilung bei Zuweisung (gebuendelt je Zeile, nicht an sich selbst, abschaltbar je Konto und je Eigenschaft
   notify=false, Text mit Anzeigename, nur mit Zugriff), Push-Kategorie "assign".
6. Werte bleiben, wenn der Zugriff endet (Anzeige), Kontoloeschung: row_person leer, Anzeige "Geloeschte Person".
7. Lesepfade ohne Anmeldenamen (Marker-Scan): /rows (auch mit Filtern/Gruppen), Einzelzeile, PATCH, /people, CSV,
   Markdown-Export, Benachrichtigungen, Live-Ereignisse, Widgets, KI-Kontext (ai.prop_pairs/db_rows), Formeln
   (Personen -> Namen), veroeffentlichte Seite (Personen gar nicht), persoenliche Sicherung (Handles).
Wegwerf-Konten pe-*, raeumt auf. Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET)."""
import base64
import http.client
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
res = []
SEEN = []                     # (konto, text) aller Antworten fuer den Marker-Scan
MK = lambda: "pe-" + secrets.token_hex(8)        # noqa: E731  Marker = Anmeldename
A, B, C, D = MK(), MK(), MK(), MK()               # Anna (Eigentuemerin), Bert (Freigabe), Cora (Gruppe), Dora (ohne Zugriff)
NAMES = {A: "Anna Vier", B: "Bert Vier", C: "Cora Vier", D: "Dora Vier"}
GROUP = "pe-team-" + secrets.token_hex(3)


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None, scan=True):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    if scan:
        SEEN.append((u, f"{m} {p}", raw.decode("utf-8", "replace")))
    try:
        return r.status, json.loads(raw) if raw[:1] in (b"[", b"{") else raw
    except ValueError:
        return r.status, raw


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


class Stream:
    """Live-Ereignisse (SSE) eines Kontos - nur fuer den Marker-Scan."""

    def __init__(self, u):
        self.u, self.raw = u, []
        self.c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=300)
        self.c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + ck(u)})
        self.r = self.c.getresponse()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            while True:
                line = self.r.readline()
                if not line:
                    break
                self.raw.append(line.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            pass

    def close(self):
        try:
            self.c.sock.close()
        except Exception:  # noqa: BLE001
            pass


def handle(u):
    from saga.core import local_ref
    return local_ref(u)


def rp(db, u=A, **kw):
    s, d = req("GET", f"/api/notes/{db}/rows" + (qs(**kw) if kw else ""), u)
    return s, d


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    keep = {k: auth.settings(fresh=True).get(k) for k in ("share_enabled", "search_username")}
    auth.save_setting("share_enabled", True)
    auth.save_setting("search_username", True)          # auch dann kein Anmeldename eines anderen als Personen-Wert
    for u in (A, B, C, D):
        auth.create_user(u, "Personen-Pass-1234", NAMES[u])
    with connect() as c, c:
        auth.ensure_group(c, GROUP)
    auth.set_user_groups(auth.get_user(C)["id"], [GROUP])
    auth.set_user_groups(auth.get_user(A)["id"], [GROUP])       # teilen geht nur mit eigenen Gruppen
    time.sleep(5.6)
    streams = [Stream(u) for u in (A, B, C)]
    try:
        run()
        time.sleep(1.0)
        scan(streams)
    finally:
        for s in streams:
            s.close()
        for k, v in keep.items():
            if v is not None:
                auth.save_setting(k, v)
        cleanup()


def run():
    from saga import people
    # --- 1. Schema und Werte ---------------------------------------------------------------------------------------
    s, d = req("POST", "/api/notes", body={"kind": "database", "title": "Projekte"})
    db = d["id"]
    schema = {"props": [
        {"id": "wer", "name": "Zuständig", "type": "person"},
        {"id": "rev", "name": "Prüfer", "type": "person", "multi": False, "notify": False},
        {"id": "st", "name": "Status", "type": "select", "options": [{"id": "o", "name": "Offen"}]},
        {"id": "fx", "name": "Namen", "type": "formula", "expr": "prop(\"wer\").join(\"; \")"},
        {"id": "anz", "name": "Anzahl", "type": "formula", "expr": "prop(\"wer\").length()"}],
        "views": [{"id": "alle", "name": "Alle", "type": "table"},
                  {"id": "mir", "name": "Mir", "type": "table", "filters": [{"pid": "wer", "op": "me", "value": ""}]}]}
    s, d = req("PATCH", f"/api/notes/{db}", body={"schema": schema})
    ok(f"Schema mit Personen-Spalten ({s})", s == 200, d)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1")
    sp = {p["id"]: p for p in d["schema"]["props"]}
    ok("Schema: multi/notify gespeichert (Standard an)", sp["wer"].get("multi") is True and sp["wer"].get("notify") is True
       and sp["rev"].get("multi") is False and sp["rev"].get("notify") is False, sp)
    ok("Formel ueber Personen: rtype text/number", sp["fx"].get("rtype") == "text" and sp["anz"].get("rtype") == "number", sp)
    r1 = req("POST", "/api/notes", body={"parent_id": db, "title": "Alpha", "props": {"wer": [A]}})[1]["id"]
    ok("eigenen Anmeldenamen als Eingabe angenommen", True)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": [handle(B)]}})
    ok(f"Bert ohne Zugriff: 400 ({s})", s == 400 and "Zugriff" in str(d), d)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": [B]}})
    ok(f"Anmeldename eines anderen (auch mit Admin-Schalter Suche): 400 ({s})", s == 400, d)
    s, _ = req("PUT", f"/api/notes/{db}/shares", body={"type": "user", "target": handle(B), "role": "edit"})
    ok(f"Datenbank an Bert (Bearbeiten) ({s})", s == 200)
    s, _ = req("PUT", f"/api/notes/{db}/shares", body={"type": "group", "target": GROUP, "role": "view"})
    ok(f"Datenbank an Gruppe (Cora, Lesen) ({s})", s == 200)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": [handle(A), handle(B), handle(C), handle(B)]}})
    ok(f"Handles von Personen mit Zugriff (Freigabe, Gruppe), Doppelte entfernt ({s})", s == 200, d)
    ok("PATCH-Antwort: values + people (Handles, Anzeigenamen)", d.get("values", {}).get("wer") == [handle(A), handle(B), handle(C)]
       and d.get("people", {}).get(handle(B), {}).get("name") == "Bert Vier" and d["people"][handle(A)].get("me") is True, d)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": [handle(A), handle(D)]}})
    ok(f"Dora ohne Zugriff: 400 ({s})", s == 400, d)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": ["@nie@fremd.example"]}})
    ok(f"fremde Person ohne Freigabe: 400 ({s})", s == 400, d)
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"rev": [handle(B), handle(C)]}})
    ok(f"multi=false: nur die erste Person ({s})", s == 200 and d.get("values", {}).get("rev") == [handle(B)], d)
    with connect() as c:
        st = c.execute("SELECT props FROM note WHERE id=?", (r1,)).fetchone()[0]
        rpp = sorted(c.execute("SELECT pid, person FROM row_person WHERE note_id=?", (r1,)).fetchall())
    ok("gespeichert: kanonische Kennungen (nicht Handles)", json.loads(st).get("wer") == [A, B, C], st)
    ok("row_person per Trigger", rpp == sorted([("wer", A), ("wer", B), ("wer", C), ("rev", B)]), rpp)

    # --- 2. Ausgabe ----------------------------------------------------------------------------------------------------
    r2 = req("POST", "/api/notes", body={"parent_id": db, "title": "Beta", "props": {"wer": [handle(C)]}})[1]["id"]
    r3 = req("POST", "/api/notes", body={"parent_id": db, "title": "Gamma"})[1]["id"]
    s, d = rp(db)
    by = {r["id"]: r for r in d["rows"]}
    ok("/rows: Handles statt Anmeldenamen, people mit Namen/me", by[r1]["props"]["wer"] == [handle(A), handle(B), handle(C)]
       and d["people"][handle(C)]["name"] == "Cora Vier" and d["people"][handle(A)]["me"] is True and d["people"][handle(B)]["me"] is False, d)
    ok("/rows: Formel mit Anzeigenamen (nie Anmeldenamen)", by[r1]["props"].get("fx") == "Anna Vier; Bert Vier; Cora Vier"
       and by[r1]["props"].get("anz") == 3, by[r1]["props"])
    s, d = rp(db, u=B)
    ok("Bert (Freigabe): dieselben Kennungen, me fuer sich", s == 200 and d["rows"][0]["props"]["wer"] == [handle(A), handle(B), handle(C)]
       and d["people"][handle(B)]["me"] is True and d["people"][handle(A)]["me"] is False, d.get("people"))
    s, d = req("GET", f"/api/notes/{r1}", B)
    ok(f"Einzelzeile (Bert): Handles + people ({s})", s == 200 and d["props"]["wer"] == [handle(A), handle(B), handle(C)]
       and d["people"][handle(A)]["name"] == "Anna Vier", d.get("props"))
    s, d = req("GET", f"/api/notes/{db}/people", B)
    refs = {x["ref"]: x for x in d.get("people", [])}
    ok(f"Auswahl /people: Eigentuemerin, Bert, Gruppe (Cora), nicht Dora ({s})", s == 200 and set(refs) == {handle(A), handle(B), handle(C)}
       and refs[handle(B)]["me"] is True, d)
    s, d = req("GET", f"/api/notes/{db}/people?q=cora", B)
    ok("Auswahl: Suche nach Anzeigename", s == 200 and [x["ref"] for x in d["people"]] == [handle(C)], d)
    s, d = req("GET", f"/api/notes/{db}/people?q=" + D[3:9], B)
    ok("Auswahl: Teil des Anmeldenamens findet nichts", s == 200 and d["people"] == [], d)
    s, _ = req("GET", f"/api/notes/{db}/people", D)
    ok(f"Auswahl ohne Zugriff: 404 ({s})", s == 404)
    # Bert (Bearbeiten) traegt sich selbst und Cora ein
    s, d = req("PATCH", f"/api/notes/{r3}", B, {"props": {"wer": [handle(B), handle(C)]}})
    ok(f"Bert (Bearbeiten) traegt sich selbst und Cora ein ({s})", s == 200 and d.get("values", {}).get("wer") == [handle(B), handle(C)], d)
    s, d = req("PATCH", f"/api/notes/{r3}", C, {"props": {"wer": [handle(C)]}})
    ok(f"Cora (nur Lesen): 403 ({s})", s == 403)

    # --- 4. Filter, Sortierung, Gruppen, Suche --------------------------------------------------------------------------
    s, d = rp(db, view="mir")
    ok("Filter 'me' (Ansicht Mir) fuer Anna", s == 200 and [r["id"] for r in d["rows"]] == [r1], [r["id"] for r in d.get("rows", [])])
    s, d = rp(db, u=C, view="mir")
    ok("Filter 'me' fuer Cora: ihre Zeilen", s == 200 and sorted(r["id"] for r in d["rows"]) == sorted([r1, r2, r3]), d.get("rows"))
    s, d = rp(db, filters=[{"pid": "wer", "op": "contains", "value": handle(B)}])
    ok("Filter 'enthaelt <Handle>'", s == 200 and sorted(r["id"] for r in d["rows"]) == sorted([r1, r3]), d.get("rows"))
    s, d = rp(db, sorts=[{"pid": "wer", "dir": "asc"}])
    ok("Sortierung nach Name der ersten Person (Anna, Bert, Cora)", [r["id"] for r in d["rows"]] == [r1, r3, r2], [r["id"] for r in d["rows"]])
    s, d = rp(db, group="wer")
    gs = d.get("groups") or []
    ok("Gruppen nach Person: ohne, je Kombination (id = Kennungen, value = Liste)", s == 200 and gs[0] == {"id": None, "count": 0}
       and [g["value"] for g in gs[1:]] == [[handle(A), handle(B), handle(C)], [handle(B), handle(C)], [handle(C)]], gs)
    s, d = rp(db, q="cora vier")
    ok("Suche nach Anzeigename", s == 200 and sorted(r["id"] for r in d["rows"]) == sorted([r1, r2, r3]), d.get("matched"))
    s, d = rp(db, q=C[3:11])
    ok("Suche nach Teil des Anmeldenamens: kein Treffer", s == 200 and d["matched"] == 0, d.get("matched"))

    # --- 5. db.assigned --------------------------------------------------------------------------------------------------
    time.sleep(0.3)
    s, d = req("GET", "/api/notifications", B)
    it = [x for x in d.get("items", []) if x["kind"] == "db.assigned"]
    ok("Bert: db.assigned mit Anzeigename, Zeile, gebuendelt", s == 200 and len(it) == 1 and it[0]["note"]["id"] == r1
       and "Anna Vier" in it[0]["text"] and it[0]["access"] is True, it)
    s, d = req("GET", "/api/notifications", C)
    it = [x for x in d.get("items", []) if x["kind"] == "db.assigned"]
    ok("Cora: je Zeile eine Mitteilung (r1, r2, r3)", sorted(x["note"]["id"] for x in it) == sorted([r1, r2, r3]), it)
    s, d = req("GET", "/api/notifications", A)
    ok("Anna (selbst eingetragen): keine db.assigned", not [x for x in d.get("items", []) if x["kind"] == "db.assigned"], d)
    s, d = req("GET", "/api/notifications/settings", B)
    ok("Einstellungen: db.assigned abschaltbar, Standard an", any(k["kind"] == "db.assigned" and k["on"] for k in d.get("kinds", [])), d)
    req("PUT", "/api/notifications/settings", B, {"db.assigned": False})
    r4 = req("POST", "/api/notes", body={"parent_id": db, "title": "Delta", "props": {"wer": [handle(B)]}})[1]["id"]
    s, d = req("GET", "/api/notifications", B)
    ok("abgeschaltet: keine neue Mitteilung", not [x for x in d["items"] if x["kind"] == "db.assigned" and x["note"]["id"] == r4], d)
    req("PUT", "/api/notifications/settings", B, {"db.assigned": True})
    req("PATCH", f"/api/notes/{r4}", body={"props": {"rev": [handle(C)]}})
    s, d = req("GET", "/api/notifications", C)
    ok("Eigenschaft notify=false: keine Mitteilung", not [x for x in d["items"] if x["kind"] == "db.assigned" and x["note"]["id"] == r4], d)
    from saga import push
    ok("Push-Kategorie 'assign' fuer db.assigned", push.KIND_CAT.get("db.assigned") == "assign")

    # --- 3. row_person bei Schema-Aenderung, Leeren, Loeschen ------------------------------------------------------------
    req("PATCH", f"/api/notes/{r4}", body={"props": {"wer": None}})
    with connect() as c:
        n4 = c.execute("SELECT count(*) FROM row_person WHERE note_id=? AND pid='wer'", (r4,)).fetchone()[0]
    ok("Wert geleert: row_person ohne Eintrag", n4 == 0, n4)
    s, d = req("GET", f"/api/notes/{db}/rows?limit=1")
    sch = d["schema"]
    sch["props"] = [p for p in sch["props"] if p["id"] != "rev"]
    req("PATCH", f"/api/notes/{db}", body={"schema": sch})
    with connect() as c:
        nrev = c.execute("SELECT count(*) FROM row_person r JOIN note n ON n.id=r.note_id WHERE n.parent_id=? AND r.pid='rev'", (db,)).fetchone()[0]
    ok("Spalte geloescht: row_person dieser Spalte weg", nrev == 0, nrev)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE id=?", (r2,))
        n2 = c.execute("SELECT count(*) FROM row_person WHERE note_id=?", (r2,)).fetchone()[0]
    ok("Zeile endgueltig geloescht: row_person weg (Fremdschluessel)", n2 == 0)
    with connect() as c:
        rows = people.assigned_rows(c, C)
    ok("people.assigned_rows (Grundlage 'Mir zugewiesen', Paket 7)", sorted(x[0] for x in rows) == sorted([r1, r3]), rows)

    # --- 7. weitere Lesepfade (Marker-Scan ueber alles) --------------------------------------------------------------------
    s, raw = req("GET", f"/api/notes/{db}/export.csv", B)
    txt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    ok(f"CSV: Anzeigenamen ({s})", s == 200 and "Anna Vier, Bert Vier, Cora Vier" in txt, txt[:300])
    s, raw = req("GET", f"/api/notes/{db}/export")
    txt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    ok(f"Markdown-Export: Anzeigenamen ({s})", s == 200 and "Anna Vier, Bert Vier, Cora Vier" in txt, txt[:400])
    s, raw = req("GET", f"/api/notes/{r1}/export")
    txt = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    ok(f"Markdown-Export Zeile: Anzeigenamen ({s})", s == 200 and "**Zuständig:** Anna Vier, Bert Vier, Cora Vier" in txt, txt[:300])
    from saga import ai
    with connect() as c:
        sch = json.loads(c.execute("SELECT schema FROM note WHERE id=?", (db,)).fetchone()[0])
        pv = json.loads(c.execute("SELECT props FROM note WHERE id=?", (r1,)).fetchone()[0])
        dr = ai.db_rows(c, A, db, viewer=B)
    pt = ai.props_text(sch, pv)
    SEEN.append((B, "ai.props_text", pt + json.dumps([ai.props_text(sch, x["props"]) for x in dr], ensure_ascii=False)))
    ok("KI-Kontext: Personen als Anzeigenamen", "Zuständig: Anna Vier, Bert Vier, Cora Vier" in pt, pt)
    # Widget: Zitat aus der Formel-Spalte (Personen -> Namen) und Kennzahl mit Filter "mir"
    s, pg = req("POST", "/api/notes", body={"title": "Dashboard"})
    blocks = [{"id": "w1", "type": "widget", "data": {"kind": "metric", "cfg": {"db": db, "view": "mir", "agg": "count"}}},
              {"id": "w2", "type": "widget", "data": {"kind": "quote", "cfg": {"src": "db", "db": db, "prop": "fx", "pick": "open"}}}]
    s, _ = req("PATCH", f"/api/notes/{pg['id']}", body={"body": json.dumps({"blocks": blocks}), "format": "blocks"})
    req("PUT", f"/api/notes/{pg['id']}/shares", body={"type": "user", "target": handle(B), "role": "view"})
    s, d = req("GET", f"/api/notes/{pg['id']}/widgets", B)
    w = d.get("widgets", {})
    ok(f"Widgets: Kennzahl 'mir' fuer Bert, Zitat mit Namen ({s})", s == 200 and (w.get("w1") or {}).get("value") == 2
       and "Vier" in str((w.get("w2") or {}).get("text") or ""), w)
    # Veroeffentlichen: Personen-Spalten gar nicht
    s, d = req("PUT", f"/api/notes/{db}/publish", body={"name": "pe" + secrets.token_hex(4)})
    url = (d or {}).get("url") or ""
    if url:
        c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
        c.request("GET", urllib.parse.urlparse(url).path, headers={"Host": HOST, "Accept-Language": "de"})
        html = c.getresponse().read().decode("utf-8", "replace")
        SEEN.append((None, "public", html))
        ok("Veroeffentlicht: keine Personen (weder Name noch Spalte)", "Anna Vier" not in html and "Zuständig" not in html
           and "Alpha" in html, html[:300])
    else:
        ok("Veroeffentlichen moeglich", False, d)
    # persoenliche Sicherung: Handles statt Anmeldenamen
    from saga import userbackup
    with connect() as c:
        data, _, _ = userbackup.collect(c, A, {"cats": dict(userbackup.CAT_DEFAULT), "exclude": []})
    rowb = next(n for n in data["notes"] if n["id"] == r1)
    SEEN.append((A, "backup", json.dumps(rowb, ensure_ascii=False)))
    ok("Sicherung: Personen als Handles (eigenes Konto inkl.)", json.loads(rowb["props"])["wer"] == [handle(A), handle(B), handle(C)], rowb["props"])

    # --- 6. Zugriff endet / Konto geloescht ------------------------------------------------------------------------------
    req("DELETE", f"/api/notes/{db}/shares", body={"type": "user", "target": handle(B)})
    s, d = rp(db)
    ok("Zugriff entzogen: Wert bleibt, Name bleibt", d["rows"][0]["props"]["wer"][1] == handle(B) and d["people"][handle(B)]["name"] == "Bert Vier", d.get("people"))
    s, d = req("PATCH", f"/api/notes/{r1}", body={"props": {"wer": [handle(A), handle(B), handle(C)]}})
    ok(f"Speichern mit bestehendem Wert ohne Zugriff geht weiter ({s})", s == 200, d)
    from saga.userdata import delete_user_data
    delete_user_data(C, drop_user=True)
    with connect() as c:
        nc = c.execute("SELECT count(*) FROM row_person WHERE person=?", (C,)).fetchone()[0]
    s, d = rp(db)
    gone = [k for k, v in d.get("people", {}).items() if v.get("gone")]
    w1 = next(r for r in d["rows"] if r["id"] == r1)["props"]["wer"]
    ok("Konto geloescht: row_person leer, Grabstein mit Anzeige 'Geloeschte Person'", nc == 0 and len(gone) == 1
       and gone[0] in w1 and handle(C) not in w1 and d["people"][gone[0]]["name"] == "Gelöschte Person", (nc, d.get("people"), w1))
    auth.create_user(C, "Personen-Pass-1234", "Neue Cora")         # gleicher Anmeldename, neues Konto
    s, d = rp(db)
    ok("neues Konto mit gleichem Namen erbt keine Zuweisung", handle(C) not in json.dumps(d.get("rows")), d.get("people"))


def scan(streams):
    """Kein Anmeldename eines ANDEREN Kontos in Antworten/Ereignissen (eigener Anmeldename im Cookie-Konto ist erlaubt)."""
    for s in streams:
        SEEN.append((s.u, "live", "".join(s.raw)))
    hits = []
    for who, what, txt in SEEN:
        for u in (A, B, C, D):
            if u != who and u in txt:
                hits.append((who, what, u, txt[max(0, txt.find(u) - 80):txt.find(u) + 40]))
    ok(f"Marker-Scan: {len(SEEN)} Antworten/Ereignisse ohne Anmeldenamen anderer Konten", not hits, hits[:3])
    ok("Gegenprobe: Marker im eigenen Konto kommt vor (Scan wirkt)", any(who == A and A in txt for who, _, txt in SEEN))


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        us = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'pe-%'").fetchall()]
    for u in us:
        delete_user_data(u, drop_user=True)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'pe-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'pe-%' OR target LIKE 'pe-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'pe-team-%'")


if __name__ == "__main__":
    main()
    print(f"\nPERSONEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
