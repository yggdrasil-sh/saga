"""CSV-Export einer Datenbank (2.5, Paket 2 E1, saga/csv_export.py): GET /api/notes/{nid}/export.csv.

Prueft: UTF-8 mit BOM, Komma + Dezimalpunkt; Excel (Deutsch) mit Semikolon + Dezimalkomma; Kopfzeile mit Titelspalte;
Werte aller Typen maschinenlesbar (Datum ISO, Zahl ohne Einheit, Haekchen true/false, Auswahl/Mehrfach/Relation als
Namen, Berechnet, Erstellt/Bearbeitet), unbekannter Typ als Text (Erweiterungsstelle proptext); Formel-Injektion
(an/aus; Zahlen bleiben); Ansicht mit Filter/Sortierung = Reihenfolge von /rows?view=, sichtbare Spalten in
Ansichtsreihenfolge; Suche q; cols; alles (all=1); Rechte (fremd 404, Freigabe Lesen: ohne archivierte Zeilen,
Relationen-Titel nur sichtbar); falsche Parameter -> 400; Zeilenumbrueche/Anfuehrungszeichen; Rundreise mit csv-Modul.
Wegwerf-Konten ce-*, raeumt auf."""
import base64
import csv
import http.client
import io
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
A, BU, CU = "ce-a", "ce-b", "ce-c"
res = []
errs_before = 0


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=A, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw) if raw[:1] in (b"[", b"{") else raw, r
    except ValueError:
        return r.status, raw, r


def qs(**kw):
    return "?" + urllib.parse.urlencode({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v) for k, v in kw.items()})


def parse(raw, sep=","):
    txt = raw.decode("utf-8")
    return txt, list(csv.reader(io.StringIO(txt.lstrip("﻿")), delimiter=sep))


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    prev = auth.settings(fresh=True).get("share_enabled", True)
    auth.save_setting("share_enabled", True)
    for u in (A, BU, CU):
        auth.create_user(u, "Export-Pass-1234", u.upper())
    time.sleep(5.6)
    try:
        run()
    finally:
        auth.save_setting("share_enabled", prev)
        cleanup()


def run():
    # Ziel-Datenbank fuer Relationen
    s, d, _ = req("POST", "/api/notes", body={"kind": "database", "title": "Kunden"})
    tgt = d["id"]
    k1 = req("POST", "/api/notes", body={"parent_id": tgt, "title": "Kunde Alpha"})[1]["id"]
    k2 = req("POST", "/api/notes", body={"parent_id": tgt, "title": "Kunde Beta"})[1]["id"]
    s, d, _ = req("POST", "/api/notes", body={"kind": "database", "title": "Projekte/Übersicht"})
    db = d["id"]
    schema = {"props": [
        {"id": "status", "name": "Status", "type": "select", "options": [{"id": "o1", "name": "Offen", "color": "gray"}, {"id": "o2", "name": "Fertig", "color": "green"}]},
        {"id": "tags", "name": "Tags", "type": "multi_select", "options": [{"id": "a", "name": "rot"}, {"id": "b", "name": "blau"}]},
        {"id": "betrag", "name": "Betrag", "type": "number", "fmt": "euro"},
        {"id": "anteil", "name": "Anteil", "type": "number", "fmt": "percent"},
        {"id": "summe", "name": "Summe", "type": "calc", "op": "add", "a": "betrag", "b": "anteil"},
        {"id": "datum", "name": "Datum", "type": "date"},
        {"id": "fertig", "name": "Fertig", "type": "checkbox"},
        {"id": "link", "name": "Link", "type": "url"},
        {"id": "notiz", "name": "Notiz", "type": "text"},
        {"id": "kunde", "name": "Kunde", "type": "relation", "target": tgt},
        {"id": "erst", "name": "Erstellt", "type": "created"}],
        "views": [{"id": "alle", "name": "Alle", "type": "table"},
                  {"id": "offen", "name": "Offen", "type": "table", "filters": [{"pid": "status", "op": "is", "value": "o1"}],
                   "sorts": [{"pid": "betrag", "dir": "desc"}], "hidden": ["notiz", "link"], "order": ["betrag", "status"]}],
        "title_name": "Projekt"}
    s, d, _ = req("PATCH", f"/api/notes/{db}", body={"schema": schema})
    ok("Schema gesetzt", s == 200, d)
    rows = [
        ("Alpha", {"status": "o1", "tags": ["a", "b"], "betrag": 1234.5, "anteil": 12, "datum": "2026-10-27", "fertig": True,
                   "link": "https://example.org/x", "notiz": "Zeile 1\nZeile 2, mit \"Zitat\"", "kunde": [k1, k2]}),
        ("=SUMME(A1)", {"status": "o1", "betrag": 99.25, "notiz": "@boese", "kunde": [k2]}),
        ("Gamma", {"status": "o2", "betrag": -3, "notiz": "+1", "anteil": 0.1}),
        ("Delta", {"status": "o1", "betrag": 5, "notiz": "-2"}),
        ("Archiviert", {"status": "o1", "betrag": 1}),
    ]
    ids = {}
    for title, pv in rows:
        s, d, _ = req("POST", "/api/notes", body={"parent_id": db, "title": title, "props": pv})
        ids[title] = d["id"]
    ok("Zeilen angelegt", len(ids) == 5)
    req("POST", f"/api/notes/{ids['Archiviert']}/archive", body={"archived": True})
    with connect() as c, c:
        arch = c.execute("SELECT archived FROM note WHERE id=?", (ids["Archiviert"],)).fetchone()[0]
    ok("Zeile archiviert", arch is not None)

    # --- alles, Standardformat ---------------------------------------------------------------------------------------
    s, raw, r = req("GET", f"/api/notes/{db}/export.csv?all=1&tz=Europe/Berlin")
    ok("all=1: 200, text/csv, Download-Name", s == 200 and r.getheader("Content-Type").startswith("text/csv")
       and "Projekte_" in (r.getheader("Content-Disposition") or "") and ".csv" in r.getheader("Content-Disposition"), (s, raw[:200]))
    txt, tab = parse(raw)
    ok("UTF-8 mit BOM, CRLF", raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw)
    head = tab[0]
    ok("Kopf: Titelspalte + alle Spalten in Schema-Reihenfolge", head == ["Projekt", "Status", "Tags", "Betrag", "Anteil", "Summe", "Datum", "Fertig",
                                                                         "Link", "Notiz", "Kunde", "Erstellt"], head)
    by = {x[0]: dict(zip(head, x)) for x in tab[1:]}
    a = by.get("Alpha", {})
    ok("Werte maschinenlesbar (Auswahl, Mehrfach, Zahl, Prozent, Berechnet, Datum, Haekchen, URL)",
       a.get("Status") == "Offen" and a.get("Tags") == "rot, blau" and a.get("Betrag") == "1234.5" and a.get("Anteil") == "12"
       and a.get("Summe") == "1246.5" and a.get("Datum") == "2026-10-27" and a.get("Fertig") == "true"
       and a.get("Link") == "https://example.org/x", a)
    ok("Text mit Zeilenumbruch, Komma und Anfuehrungszeichen rundreisefest", a.get("Notiz") == "Zeile 1\nZeile 2, mit \"Zitat\"", a.get("Notiz"))
    ok("Relation: Titel der Ziele", a.get("Kunde") == "Kunde Alpha, Kunde Beta", a.get("Kunde"))
    ok("Erstellt als ISO-Zeit", len(a.get("Erstellt", "")) == 16 and a["Erstellt"][4] == "-" and a["Erstellt"][10] == " ", a.get("Erstellt"))
    ok("Haekchen leer -> false", by.get("Gamma", {}).get("Fertig") == "false")
    ok("Eigentuemer: archivierte Zeile dabei", "Archiviert" in by)
    ok("Formel-Injektion: Titel und Text mit '", "'=SUMME(A1)" in by and by["'=SUMME(A1)"]["Notiz"] == "'@boese"
       and by["Gamma"]["Notiz"] == "'+1" and by["Delta"]["Notiz"] == "'-2", list(by))
    ok("Formel-Injektion: negative Zahl bleibt Zahl", by["Gamma"]["Betrag"] == "-3" and by["Gamma"]["Anteil"] == "0.1", by["Gamma"])

    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv?all=1&safe=0")
    _, tab0 = parse(raw)
    ok("safe=0: ohne Schutz", any(x[0] == "=SUMME(A1)" for x in tab0))

    # --- Excel (Deutsch) -------------------------------------------------------------------------------------------
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv?all=1&sep=%3B")
    txt, tab = parse(raw, ";")
    head = tab[0]
    by = {x[0]: dict(zip(head, x)) for x in tab[1:]}
    ok("Excel: Semikolon, Dezimalkomma", s == 200 and by["Alpha"]["Betrag"] == "1234,5" and by["Gamma"]["Anteil"] == "0,1"
       and txt.splitlines()[0].count(";") == 11, txt[:200])

    # --- Ansicht: Filter, Sortierung, sichtbare Spalten in Ansichtsreihenfolge ---------------------------------------
    s, rr, _ = req("GET", f"/api/notes/{db}/rows" + qs(view="offen"))
    want = [x["title"] for x in rr["rows"]]
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv" + qs(view="offen"))
    _, tab = parse(raw)
    got = [x[0].lstrip("'") for x in tab[1:]]
    ok("Ansicht 'Offen': Reihenfolge = /rows?view=", s == 200 and got == want and want[0] == "Alpha", (got, want))
    ok("Ansicht: Spalten nach view.order, ohne ausgeblendete", tab[0][:3] == ["Projekt", "Betrag", "Status"]
       and "Notiz" not in tab[0] and "Link" not in tab[0], tab[0])
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv")
    _, tab = parse(raw)
    ok("ohne view: erste Ansicht (alle Zeilen)", s == 200 and len(tab) == 6, len(tab))
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv" + qs(view="offen", q="delta"))
    _, tab = parse(raw)
    ok("Suche q", [x[0] for x in tab[1:]] == ["Delta"], tab)
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv" + qs(filters=[{"pid": "betrag", "op": "gt", "value": "50"}], sorts=[{"pid": "title", "dir": "asc"}],
                                                          cols="notiz,betrag,gibtsnicht"))
    _, tab = parse(raw)
    ok("filters/sorts/cols ueberschreiben die Ansicht", s == 200 and tab[0] == ["Projekt", "Notiz", "Betrag"]
       and [x[0] for x in tab[1:]] == ["'=SUMME(A1)", "Alpha"], tab)

    # --- unbekannter Typ (Erweiterungsstelle) ------------------------------------------------------------------------
    from saga import proptext
    p = {"id": "x", "type": "kuenftig"}
    ok("proptext: unbekannter Typ als Text", proptext.prop_text(p, ["a", {"name": "b"}, 3], proptext.Ctx("csv")) == "a, b, 3"
       and proptext.prop_text(p, 2.5, proptext.Ctx("csv")) == "2.5" and proptext.prop_text(p, None, proptext.Ctx("csv")) == "")
    proptext.register("kuenftig", lambda p, v, ctx: "K:" + str(v))
    ok("proptext: register() fuer neue Typen", proptext.prop_text(p, 7, proptext.Ctx("csv")) == "K:7")
    ok("proptext: Zahlen ohne Exponent", proptext.num_text(1e15) == "1000000000000000" and proptext.num_text(0.1, ",") == "0,1"
       and proptext.num_text(3.0) == "3" and proptext.num_text(-0.0) == "0")

    # --- Rechte ------------------------------------------------------------------------------------------------------
    s, _, _ = req("GET", f"/api/notes/{db}/export.csv", u=BU)
    ok("fremd ohne Freigabe: 404", s == 404)
    with connect() as c, c:
        c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                  (db, A, "user", BU, "view", time.time(), A))
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv?all=1", u=BU)
    _, tab = parse(raw)
    by = {x[0]: dict(zip(tab[0], x)) for x in tab[1:]}
    ok("Freigabe Lesen: ohne archivierte Zeilen", s == 200 and "Archiviert" not in by and "Alpha" in by, (s, list(by)))
    ok("Freigabe Lesen: Relationen ohne Zugriff aufs Ziel leer", by.get("Alpha", {}).get("Kunde") == "", by.get("Alpha"))
    with connect() as c, c:
        c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created, created_by) VALUES (?,?,?,?,?,?,?)",
                  (tgt, A, "user", BU, "view", time.time(), A))
    s, raw, _ = req("GET", f"/api/notes/{db}/export.csv?all=1", u=BU)
    _, tab = parse(raw)
    by = {x[0]: dict(zip(tab[0], x)) for x in tab[1:]}
    ok("Freigabe Lesen + Ziel geteilt: Relationen-Titel", by.get("Alpha", {}).get("Kunde") == "Kunde Alpha, Kunde Beta", by.get("Alpha"))
    s, _, _ = req("GET", f"/api/notes/{db}/export.csv", u=CU)
    ok("drittes Konto: 404", s == 404)
    s, _, _ = req("GET", f"/api/notes/{k1}/export.csv")
    ok("keine Datenbank: 404", s == 404)
    s, d, _ = req("GET", f"/api/notes/{db}/export.csv?filters=kaputt")
    ok("falscher Parameter: 400", s == 400, d)
    s, d, _ = req("GET", f"/api/notes/{db}/export.csv?tz=Mond/Basis")
    ok("falsche Zeitzone: 400", s == 400, d)
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    c.request("GET", f"/api/notes/{db}/export.csv", headers={"Host": HOST, "X-Saga": "1"})
    ok("ohne Anmeldung: 401", c.getresponse().status == 401)

    # --- leere Datenbank, Sprache ---------------------------------------------------------------------------------
    s, d, _ = req("POST", "/api/notes", body={"kind": "database", "title": "Leer"})
    s, raw, _ = req("GET", f"/api/notes/{d['id']}/export.csv?all=1")
    txt, tab = parse(raw)
    ok("leere Datenbank: nur Kopfzeile", s == 200 and len(tab) == 1 and tab[0][0] == "Name", tab)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'ce-%'")
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'ce-%' OR target LIKE 'ce-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ce-%'")


if __name__ == "__main__":
    main()
    print(f"\nCSV-EXPORT {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
