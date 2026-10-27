"""CSV -> Datenbank (2.5, Paket 2 E2/E3, saga/importer.py, saga/import_csv.py) mit selbst gebauten Testdaten.

Prueft: Upload/Token (fremdes Token 404, ohne Anmeldung 401), Analyse (UTF-8 mit/ohne BOM, cp1252 mit Umlauten, UTF-16,
Trennzeichen , ; Tab |, Dezimalkomma, Datumsformate DE/US/ISO/Notion-Englisch, Haekchen, URL, Auswahl, Mehrfach,
Kopfzeile aus), Vorschau (20 Zeilen), Typ-Aenderung im Lauf, Spalte "nicht uebernehmen", Titelspalte, Anfuehrungszeichen
mit Zeilenumbruch, Werte passen nicht -> leer + Bericht, 51 Auswahlwerte -> Text, 31 Spalten -> 400 (abwaehlen noetig),
Zeilen-Grenze (IMPORT_CSV_ROWS_MAX) mit Bericht, Seite "Importbericht", Zeilen an bestehende Datenbank (Spalten
zuordnen, neue Optionen, Relation ueber Titel), zweiter Lauf parallel -> 409, Abbruch, Laufliste, Ziel fremd -> 404,
Rundreise Export -> Import ergibt gleiche Werte, keine Serverfehler. Wegwerf-Konten ic-*, raeumt auf.
Server mit IMPORT_CSV_ROWS_MAX=2000 (sonst Grenze 5000: Test passt die Groesse an)."""
import base64
import codecs
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
A, BU = "ic-a", "ic-b"
ROWS_MAX = int(os.environ.get("IMPORT_CSV_ROWS_MAX", "5000"))
res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, body=None, u=A, raw=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=120)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1"}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    try:
        return r.status, json.loads(d) if d[:1] in (b"[", b"{") else d
    except ValueError:
        return r.status, d


def upload(name, data, u=A, tok=None):
    q = "name=" + urllib.parse.quote(name) + ("&token=" + tok if tok else "")
    return req("POST", "/api/import/upload?" + q, raw=data, u=u)


def wait(rid, u=A, ms=60000):
    t0 = time.time()
    while time.time() - t0 < ms / 1000:
        s, d = req("GET", f"/api/import/runs/{rid}", u=u)
        if s == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.25)
    return d


def full(name, data, cfg=None, u=A, edit=None):
    """Upload + Analyse + Lauf mit erkannten Typen -> (analyse, lauf, rows)."""
    s, d = upload(name, data, u=u)
    if s != 200:
        return None, {"state": "upload", "s": s, "d": d}, None
    tok = d["token"]
    s, a = req("POST", f"/api/import/{tok}/analyze", cfg or {}, u=u)
    if s != 200:
        return a, {"state": "analyze", "s": s}, None
    cols = [{"index": c["index"], "type": c["guess"]} for c in a["columns"]]
    body = dict(cfg or {}, kind="csv", columns=cols, title_col=a["title_col"])
    if edit:
        edit(body, a)
    s, r = req("POST", f"/api/import/{tok}/run", body, u=u)
    if s != 202:
        return a, {"state": "run", "s": s, "d": r}, None
    st = wait(r["run_id"], u=u)
    rows = req("GET", f"/api/notes/{st['root']}/rows", u=u)[1] if st.get("root") else None
    return a, st, rows


def guess(a):
    return {c["name"]: c["guess"] for c in a["columns"]}


def vals(rows):
    sc = {p["id"]: p for p in rows["schema"]["props"]}
    out = {}
    for r in rows["rows"]:
        d = {}
        for k, v in r["props"].items():
            p = sc.get(k)
            if p and p["type"] in ("select", "multi_select"):
                names = {o["id"]: o["name"] for o in p["options"]}
                v = names.get(v) if isinstance(v, str) else [names.get(x) for x in v]
            d[p["name"] if p else k] = v
        out[r["title"]] = d
    return out


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Import-Pass-1234", u.upper())
    try:
        run()
    finally:
        cleanup()


def run():
    # --- Upload/Rechte ----------------------------------------------------------------------------------------------
    s, d = req("POST", "/api/import/upload?name=x.csv", raw=b"a,b\n1,2\n", u=None)
    ok("Upload ohne Anmeldung: 401", s == 401)
    s, d = upload("../x.csv", b"a\n")
    ok("Upload mit ../ im Namen: 400", s == 400, d)
    s, d = upload("x.csv", b"a,b\n1,2\n")
    tok = d["token"]
    ok("Upload: Token, Art csv", s == 200 and d["kind"] == "csv", d)
    s, _ = req("POST", f"/api/import/{tok}/analyze", {}, u=BU)
    ok("fremdes Token: 404 (Analyse)", s == 404)
    s, _ = req("POST", f"/api/import/{tok}/run", {"kind": "csv", "columns": []}, u=BU)
    ok("fremdes Token: 404 (Lauf)", s == 404)
    s, _ = upload("y.csv", b"x", u=BU, tok=tok)
    ok("fremdes Token: 404 (weiterer Teil)", s == 404)
    s, _ = req("DELETE", f"/api/import/{tok}")
    s2, _ = req("POST", f"/api/import/{tok}/analyze", {})
    ok("Upload verwerfen", s == 200 and s2 == 404)

    # --- Excel DE: cp1252, Semikolon, Dezimalkomma, Datum DE -------------------------------------------------------
    data = ("Name;Betrag;Datum;Status;Fertig;Web;Notiz\n"
            "Äpfel;1.234,50;27.10.2026;Offen;ja;https://example.org;\"mehrzeilig\nzweite Zeile; mit Semikolon\"\n"
            "Birnen;3,5;01.02.2026;Fertig;nein;;\n"
            "Kirschen;12;15.03.2026;Offen;ja;www.example.com;Ölig\n").encode("cp1252")
    a, st, rows = full("obst.csv", data)
    ok("cp1252 erkannt, Semikolon, 3 Zeilen", a["encoding"] == "cp1252" and a["sep"] == ";" and a["rows"] == 3, a)
    ok("Typen erraten (Zahl, Datum, Auswahl, Haekchen, URL, Text)", guess(a) == {"Name": "text", "Betrag": "number", "Datum": "date",
                                                                          "Status": "select", "Fertig": "checkbox", "Web": "url", "Notiz": "text"}, guess(a))
    ok("Vorschau mit Umlauten", a["preview"][0][0] == "Äpfel" and len(a["preview"]) == 3, a["preview"][:1])
    ok("Lauf fertig, neue Datenbank", st["state"] == "done" and st["root"] and st["report"]["counts"]["rows"] == 3, st)
    v = vals(rows)
    ok("Werte: Dezimalkomma, Datum DE, Auswahl, Haekchen, URL", v["Äpfel"] == {"Betrag": 1234.5, "Datum": "2026-10-27", "Status": "Offen", "Fertig": True,
                                                                          "Web": "https://example.org", "Notiz": "mehrzeilig\nzweite Zeile; mit Semikolon"}
       and v["Birnen"].get("Fertig") is None and v["Kirschen"]["Web"] == "https://www.example.com", v)
    ok("Titelspalte: Name 'Name' = Standard (kein title_name)", rows["schema"].get("title_name") is None, rows["schema"])

    # --- UTF-8 mit BOM, Komma, US-Daten, Mehrfach, Notion-Datum ------------------------------------------------------
    data = codecs.BOM_UTF8 + ("Titel,Wann,Tags,Anteil,Erledigt,Notion\n"
                              "A,10/27/2026,\"rot, blau\",12%,Yes,\"October 27, 2026\"\n"
                              "B,02/13/2026,rot,5 %,No,\"March 1, 2026 → March 3, 2026\"\n"
                              "C,01/02/2026,\"blau, grün\",7%,Yes,\"Jan 5, 2026 10:00 AM\"\n").encode()
    a, st, rows = full("utf8.csv", data)
    g = guess(a)
    ok("BOM, Komma, Mehrfach, Datum US (Mehrheit), Prozent, Yes/No", a["encoding"] == "utf-8-sig" and a["sep"] == ","
       and g["Wann"] == "date" and g["Tags"] == "multi_select" and g["Anteil"] == "number" and g["Erledigt"] == "checkbox"
       and g["Notion"] == "date", (a["encoding"], g))
    v = vals(rows)
    ok("Titelspalte mit eigenem Namen -> title_name", rows["schema"].get("title_name") == "Titel", rows["schema"].get("title_name"))
    ok("Werte: US-Datum, Mehrfach, Prozent, Notion-Datum/Bereich", v["A"]["Wann"] == "2026-10-27" and v["C"]["Wann"] == "2026-01-02"
       and sorted(v["A"]["Tags"]) == ["blau", "rot"] and v["A"]["Anteil"] == 12 and v["B"]["Notion"] == "2026-03-01"
       and v["C"]["Notion"] == "2026-01-05", v)
    sc = {p["name"]: p for p in rows["schema"]["props"]}
    ok("Zahl mit % -> fmt percent", sc["Anteil"].get("fmt") == "percent", sc["Anteil"])

    # --- Tab, Pipe, UTF-16, ohne Kopfzeile ---------------------------------------------------------------------------
    a, st, rows = full("tab.tsv", "x\ty\n1\t2\n3\t4\n".encode())
    ok("Tabulator erkannt", a["sep"] == "\t" and st["state"] == "done", a.get("sep"))
    a, st, rows = full("pipe.csv", "x|y\na|1\nb|2\n".encode())
    ok("Pipe erkannt", a["sep"] == "|", a.get("sep"))
    a, st, rows = full("u16.csv", "Name,Zahl\nÜber,1\nUnter,2\n".encode("utf-16"))
    ok("UTF-16 mit BOM", a["encoding"] == "utf-16" and vals(rows).get("Über") == {"Zahl": 1}, (a.get("encoding"), rows and vals(rows)))
    a, st, rows = full("ohnekopf.csv", "Eins,1\nZwei,2\n".encode(), {"header": False})
    ok("ohne Kopfzeile: Spalte 1/2, 2 Zeilen", a["rows"] == 2 and [c["name"] for c in a["columns"]] == ["Spalte 1", "Spalte 2"]
       and sorted(r["title"] for r in rows["rows"]) == ["Eins", "Zwei"], a.get("columns"))

    # --- Typ aendern, Spalte weglassen, falsche Werte -> Bericht + Seite ---------------------------------------------
    data = "Name,Zahl,Weg,Datum\nA,1,x,2026-01-01\nB,zwei,y,kein Datum\nC,3,z,2026-01-03\n".encode()

    def edit(body, a):
        for c in body["columns"]:
            if c["index"] == 1:
                c["type"] = "number"
            if c["index"] == 2:
                c["type"] = "skip"
            if c["index"] == 3:
                c["type"] = "date"
    a, st, rows = full("typen.csv", data, edit=edit)
    v = vals(rows)
    ok("Typ erzwungen: falsche Werte bleiben leer", v["A"] == {"Zahl": 1, "Datum": "2026-01-01"} and v["B"] == {} and "Weg" not in
       {p["name"] for p in rows["schema"]["props"]}, v)
    grp = sum(len(g["items"]) for g in st["report"]["groups"] if g["code"] == "bad_value")
    ok("Bericht: 2 Werte passen nicht zum Typ (je Typ eine Gruppe)", grp == 2 and sum(g["code"] == "bad_value" for g in st["report"]["groups"]) == 2,
       st["report"])
    page = st["report"].get("page")
    s, pg = req("GET", f"/api/notes/{page}") if page else (0, {})
    ok("Seite Importbericht angelegt", s == 200 and pg["title"].startswith("Importbericht") and "Zeile 2" in pg["body"], (s, pg))

    # --- 51 Auswahlwerte -> Text; 31 Spalten -> 400 ----------------------------------------------------------------
    lines = ["Name,Farbe"] + [f"N{i},F{i % 51}" for i in range(120)]
    a, st, rows = full("viele.csv", "\n".join(lines).encode(), edit=lambda b, a: [c.update(type="select") for c in b["columns"] if c["index"] == 1])
    sc = {p["name"]: p for p in rows["schema"]["props"]}
    ok("51 Auswahlwerte: als Text + Bericht", guess(a)["Farbe"] == "text" and sc["Farbe"]["type"] == "text"
       and any(g["code"] == "too_many_opts" for g in st["report"]["groups"]), (guess(a), sc["Farbe"]))
    head = ",".join(["Titel"] + [f"S{i}" for i in range(31)])
    data = (head + "\n" + ",".join(["a"] + ["1"] * 31) + "\n").encode()
    s, d = upload("breit.csv", data)
    tok = d["token"]
    s, a = req("POST", f"/api/import/{tok}/analyze", {})
    ok("31 Spalten: Warnung in der Analyse", s == 200 and a["warnings"], a.get("warnings"))
    cols = [{"index": c["index"], "type": c["guess"]} for c in a["columns"]]
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "csv", "columns": cols, "title_col": 0})
    ok("31 Spalten: Lauf abgelehnt (400)", s == 400, (s, r))
    cols[-1]["type"] = "skip"
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "csv", "columns": cols, "title_col": 0})
    st = wait(r["run_id"]) if s == 202 else {}
    ok("30 Spalten nach Abwaehlen: geht", s == 202 and st.get("state") == "done", (s, st))

    # --- Zeilen-Grenze ------------------------------------------------------------------------------------------------
    n = ROWS_MAX + 7
    data = ("Name,Nr\n" + "".join(f"Zeile {i},{i}\n" for i in range(n))).encode()
    a, st, rows = full("lang.csv", data)
    ok(f"Grenze {ROWS_MAX} Zeilen: Rest im Bericht", a["warnings"] and st["state"] == "done" and st["report"]["counts"]["rows"] == ROWS_MAX
       and any(g["code"] == "rows_limit" for g in st["report"]["groups"]) and len(rows["rows"]) == ROWS_MAX, (st.get("report"), a.get("warnings")))

    # --- parallel: zweiter Lauf 409; Abbruch ---------------------------------------------------------------------------
    big = ("Name,Nr\n" + "".join(f"P{i},{i}\n" for i in range(ROWS_MAX))).encode()
    s, d1 = upload("p1.csv", big)
    s, d2 = upload("p2.csv", b"a,b\n1,2\n")
    cols = [{"index": 0, "type": "text"}, {"index": 1, "type": "number"}]
    s1, r1 = req("POST", f"/api/import/{d1['token']}/run", {"kind": "csv", "columns": cols, "title_col": 0})
    s2, r2 = req("POST", f"/api/import/{d2['token']}/run", {"kind": "csv", "columns": cols, "title_col": 0})
    ok("zweiter Lauf parallel: 409", s1 == 202 and s2 == 409, (s1, s2, r2))
    s, _ = req("POST", f"/api/import/runs/{r1['run_id']}/cancel", u=BU)
    ok("fremder Abbruch: 404", s == 404)
    s, _ = req("POST", f"/api/import/runs/{r1['run_id']}/cancel")
    st = wait(r1["run_id"])
    ok("Abbruch bzw. fertig", s in (202, 404) and st["state"] in ("cancelled", "done"), st)
    if st["state"] == "cancelled":
        with connect() as c:
            left = c.execute("SELECT COUNT(*) FROM note WHERE uid=? AND title LIKE 'P%' AND deleted_at IS NULL", (A,)).fetchone()[0]
        ok("Abbruch: Angelegtes im Papierkorb", left == 0, left)
    s, d = req("GET", "/api/import/runs")
    ok("Laufliste (eigene)", s == 200 and len(d["runs"]) >= 5 and all(x["id"] for x in d["runs"]))
    s, d = req("GET", "/api/import/runs", u=BU)
    ok("Laufliste fremd: leer", s == 200 and d["runs"] == [])
    s, _ = req("GET", f"/api/import/runs/{r1['run_id']}", u=BU)
    ok("fremder Lauf: 404", s == 404)

    # --- Ziel fremd / ungueltig ----------------------------------------------------------------------------------------
    s, d = req("POST", "/api/notes", {"title": "Fremd"}, u=BU)
    s, d2 = upload("z.csv", b"a,b\n1,2\n")
    s, r = req("POST", f"/api/import/{d2['token']}/run", {"kind": "csv", "columns": [{"index": 0, "type": "text"}], "parent_id": d["id"]})
    ok("Ziel fremde Seite: 404", s == 404, r)
    s, r = req("POST", f"/api/import/{d2['token']}/run", {"kind": "csv", "columns": [{"index": 9, "type": "text"}]})
    st = wait(r["run_id"]) if s == 202 else {"state": s}
    ok("ungueltige Spalte: Lauf scheitert sauber", st["state"] in ("failed", 400), st)

    # --- Zeilen an bestehende Datenbank (Spalten zuordnen, Relation ueber Titel) --------------------------------------
    s, d = req("POST", "/api/notes", {"kind": "database", "title": "Kunden"})
    kdb = d["id"]
    k1 = req("POST", "/api/notes", {"parent_id": kdb, "title": "Firma Eins"})[1]["id"]
    s, d = req("POST", "/api/notes", {"kind": "database", "title": "Auftraege"})
    adb = d["id"]
    req("PATCH", f"/api/notes/{adb}", {"schema": {"props": [
        {"id": "betrag", "name": "Betrag", "type": "number"},
        {"id": "st", "name": "Stand", "type": "select", "options": [{"id": "neu", "name": "Neu", "color": "gray"}]},
        {"id": "kunde", "name": "Kunde", "type": "relation", "target": kdb}], "views": [{"id": "t", "name": "T", "type": "table"}]}})
    req("POST", "/api/notes", {"parent_id": adb, "title": "Alt", "props": {"betrag": 1}})
    data = "Auftrag;Summe;Status;Firma;Egal\nX1;10,5;Neu;firma eins;q\nX2;20;Erledigt;Unbekannt GmbH;q\n".encode()
    s, d = upload("auftraege.csv", data)
    tok = d["token"]
    s, a = req("POST", f"/api/import/{tok}/analyze", {})
    cols = [{"index": 0, "target": "title"}, {"index": 1, "target": "betrag"}, {"index": 2, "target": "st"},
            {"index": 3, "target": "kunde"}, {"index": 4, "type": "skip"}]
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "csv", "columns": cols, "target_db": adb})
    st = wait(r["run_id"]) if s == 202 else {}
    rows = req("GET", f"/api/notes/{adb}/rows")[1]
    v = vals(rows)
    ok("an bestehende DB: Zeilen angehaengt, alte bleibt", st.get("state") == "done" and set(v) == {"Alt", "X1", "X2"}, (st, v))
    ok("Werte zugeordnet, neue Option ergaenzt, Relation ueber Titel", v["X1"] == {"Betrag": 10.5, "Stand": "Neu", "Kunde": [k1]}
       and v["X2"].get("Stand") == "Erledigt" and "Kunde" not in v["X2"], v)
    ok("Bericht: Relationsziel nicht gefunden", any(g["code"] == "rel_missing" for g in st["report"]["groups"]), st.get("report"))
    s, d = upload("q.csv", b"a\n1\n")
    s, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "csv", "columns": [{"index": 0, "target": "title"}], "target_db": kdb}, u=BU)
    ok("fremdes Token + fremde DB: 404", s == 404)

    # --- Rundreise: Export -> Import ---------------------------------------------------------------------------------
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    c.request("GET", f"/api/notes/{adb}/export.csv?all=1&sep=%3B", headers={"Host": HOST, "X-Saga": "1", "Cookie": "saga=" + ck(A)})
    exp = c.getresponse().read()
    a, st, rows2 = full("rund.csv", exp, edit=lambda b, a: [x.update(type="text") for x in b["columns"] if a["columns"][x["index"]]["name"] == "Kunde"])
    v2 = vals(rows2)
    ok("Rundreise Export (Excel) -> Import: gleiche Werte", a["sep"] == ";" and v2["X1"]["Betrag"] == 10.5 and v2["X1"]["Stand"] == "Neu"
       and v2["X1"]["Kunde"] == "Firma Eins" and v2["Alt"] == {"Betrag": 1}, v2)

    # --- leer / kaputt ---------------------------------------------------------------------------------------------------
    s, d = upload("leer.csv", b"")
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
    ok("leere Datei: Analyse ohne Zeilen, kein 500", s in (200, 400) and (s == 400 or a["rows"] == 0), (s, a))
    s, d = upload("bin.csv", bytes(range(256)) * 50)
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
    ok("Binaermuell: kein 500", s in (200, 400), s)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'ic-%'")
        c.execute("DELETE FROM import_run WHERE uid LIKE 'ic-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'ic-%'")


if __name__ == "__main__":
    main()
    print(f"\nIMPORT-CSV {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
