"""Notion-Import (2.5, Paket 2 E5, saga/import_notion.py) mit selbst gebauten Notion-Exporten (tests/fixtures/notion/a).

Varianten: A = Export "Markdown & CSV" wie geliefert (Seiten, Unterordner, zwei Datenbanken mit Relationen in beide
Richtungen, Zeilen-Seiten, Unterseite einer Zeile, Waise), B = derselbe Export als Export-...zip mit zwei Teil-ZIPs
(Part-1/Part-2) und zusaetzlich "<db>_all.csv" mit einer gefilterten Zeile, C = andere Schreibweisen (UUID mit
Bindestrichen, Ordner ohne ID, CRLF, Datenbank mit .md daneben, Relation nur als Titel, deutsches Datum, Waehrung $,
Datum mit GMT-Angabe, mehr als 30 Spalten), D = Sicherheit/Fehler (ZIP in einem Teil-ZIP, Bombe und boeser Pfad in
einem Teil, Teil kein ZIP, HTML-Export, Typen aus dem Dialog, Fremdzugriff).
Prueft Seitenbaum, Titel, Bloecke (Callout/Toggle/Spalten/Code/Mathe-Block/Inline-Mathe/Farben/Blockfarben, Tabelle,
Aufgaben), Verweise auf neue IDs (relativ und notion.so), Datenbank-Block, Bilder/Anhaenge (lokal), externe Bilder
als Link, Schema-Typen (Status mit Gruppen, Mehrfachauswahl, Datum mit Uhrzeit/Zeitraum, Haekchen, Zahl %, URL, E-Mail,
Telefon, Relation, Person -> Text, Dateien, Formel/Rollup -> Text mit fx_hint), Werte, Bericht, kein Serverfehler.
Wegwerf-Konten in-*."""
import base64
import csv
import http.client
import io
import json
import os
import stat
import sys
import time
import urllib.parse
import zipfile

from itsdangerous import TimestampSigner

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU = "in-a", "in-b"
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "notion", "a")
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
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
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


def upload(name, data, tok=None, u=A):
    return req("POST", "/api/import/upload?name=" + urllib.parse.quote(name) + ("&token=" + tok if tok else "&replace=1"), raw=data, u=u)


def wait(rid, u=A):
    t0 = time.time()
    d = {}
    while time.time() - t0 < 120:
        s, d = req("GET", f"/api/import/runs/{rid}", u=u)
        if s == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.25)
    return d


def fixture():
    out = {}
    for dp, _, fs in os.walk(FIX):
        for f in fs:
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, FIX).replace(os.sep, "/")] = fh.read()
    return out


def zbytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in (files.items() if isinstance(files, dict) else files):
            if isinstance(k, zipfile.ZipInfo):
                z.writestr(k, v)
            else:
                z.writestr(k, v)
    return buf.getvalue()


def tree(root):
    with connect() as c:
        rows = c.execute("WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id "
                         "WHERE n.deleted_at IS NULL) SELECT n.id, n.title, n.parent_id, n.body, n.kind, n.schema, n.props FROM note n "
                         "JOIN t ON n.id=t.id ORDER BY n.id", (root,)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r[1], []).append({"id": r[0], "parent": r[2], "body": json.loads(r[3] or '{"blocks": []}'), "kind": r[4],
                                         "schema": json.loads(r[5]) if r[5] else None, "props": json.loads(r[6]) if r[6] else {}})
    return out


def one(t, k):
    return (t.get(k) or [{}])[0]


def all_blocks(blocks):
    for b in blocks:
        yield b
        d = b.get("data") or {}
        for x in d.get("blocks") or []:
            yield from all_blocks([x])
        for c in d.get("cols") or []:
            yield from all_blocks(c.get("blocks") or [])


def codes(rep):
    return {g["code"]: [x["path"] for x in g["items"]] for g in rep["groups"]}


def run_import(data, name="Export.zip", body=None, u=A):
    s, d = upload(name, data, u=u)
    if s != 200:
        return s, d, None, None
    tok = d["token"]
    s, a = req("POST", f"/api/import/{tok}/analyze", {}, u=u)
    b = {"kind": "notion"}
    b.update(body or {})
    s2, r = req("POST", f"/api/import/{tok}/run", b, u=u)
    st = wait(r["run_id"], u=u) if s2 == 202 else {"state": "http", "error": (s2, r)}
    return s, a, st, d


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Notion-Pass-1234", u.upper())
    try:
        variant_a()
        variant_b()
        variant_c()
        variant_d()
    finally:
        cleanup()


def prop_by_name(sc, name):
    return next((p for p in sc["props"] if p["name"] == name), None)


def variant_a():
    print("--- Variante A: Markdown & CSV wie geliefert")
    s, a, st, up = run_import(zbytes(fixture()))
    ok("Upload erkennt Notion", up and up.get("kind") == "notion", up)
    n = (a or {}).get("notion") or {}
    ok("Analyse: Seiten, 2 Datenbanken, 7 Eintraege, Dateien", s == 200 and n.get("databases") == 2 and n.get("rows") == 7 and n.get("pages") == 4
       and n.get("files") == 5, a)
    g = {db["title"]: {c["name"]: c["guess"] for c in db["columns"]} for db in n.get("dbs", [])}
    want = {"Status": "status", "Tags": "multi_select", "Fällig": "date", "Zeitraum": "date", "Termin": "date", "Fertig": "checkbox",
            "Budget": "number", "Anteil": "number", "Website": "url", "E-Mail": "email", "Telefon": "phone", "Aufgaben": "relation",
            "Weitere Aufgaben": "relation", "Verantwortlich": "person", "Anhänge": "files", "Summe (Formel)": "fx",
            "Anzahl Aufgaben (Rollup)": "fx", "Kategorie": "select", "Erstellt": "date"}
    ok("Analyse: Typen der Projekte geraten", g.get("Projekte") == want, g.get("Projekte"))
    ok("Analyse: Aufgaben (Relation ueber Titel, Haekchen, Auswahl)", g.get("Aufgaben") == {"Projekt": "relation", "Erledigt": "checkbox",
                                                                                         "Priorität": "select"}, g.get("Aufgaben"))
    ok("Lauf fertig", st.get("state") == "done", st)
    if st.get("state") != "done":
        return
    t = tree(st["root"])
    root = one(t, st["root"] and next(k for k, v in t.items() if v[0]["id"] == st["root"]))
    ok("Sammelseite 'Notion-Import <Datum>'", any(k.startswith("Notion-Import") for k in t) and root.get("parent") is None, sorted(t))
    ws, ue, tief = one(t, "Wiki Start"), one(t, "Unterseite Eins"), one(t, "Tief Drin")
    ok("Seitenbaum: Wiki Start > Unterseite Eins > Tief Drin", ws.get("parent") == st["root"] and ue.get("parent") == ws.get("id")
       and tief.get("parent") == ue.get("id"), (ws.get("parent"), ue.get("parent"), tief.get("parent")))
    pj, au = one(t, "Projekte"), one(t, "Aufgaben")
    ok("Datenbanken: Projekte unter Wiki Start, Aufgaben oben", pj.get("kind") == "database" and pj.get("parent") == ws.get("id")
       and au.get("kind") == "database" and au.get("parent") == st["root"])
    bl = ws["body"]["blocks"]
    j = json.dumps(bl, ensure_ascii=False).replace('\\"', '"')
    ok("Titelzeile '# Wiki Start' nicht doppelt", not (bl and bl[0]["type"] == "header" and "Wiki Start" in bl[0]["data"]["text"]), bl[:1])
    ok("Link relativ -> neue Seite", f'/notes#{ue["id"]}' in j, j[:600])
    a1 = one(t, "Aufgabe Eins")
    ok("Link notion.so/...<id> -> neue Seite (Zeile)", f'/notes#{a1.get("id")}' in j, j[:800])
    ok("Externer Link bleibt, javascript: nur Text, <script> als Text", 'href="https://example.org/doku"' in j and "javascript" not in j
       and "<script" not in j and "&lt;script&gt;" in j)
    types = [b["type"] for b in all_blocks(bl)]
    dbb = [b for b in bl if b["type"] == "database"]
    ok("Zeile mit Link auf CSV -> Datenbank-Block", dbb and dbb[0]["data"]["db"] == pj["id"], dbb)
    imgs = [b for b in bl if b["type"] == "image"]
    att = [b for b in bl if b["type"] == "attaches"]
    ok("Bild aus dem ZIP -> image, PDF -> attaches", len(imgs) == 1 and imgs[0]["data"]["file"]["url"].startswith("/files/")
       and len(att) == 1 and att[0]["data"]["file"]["url"].startswith("/files/"), (imgs, att))
    ok("Externes Bild bleibt Link", "images.unsplash.com" in j and not any("unsplash" in json.dumps(b) for b in imgs))
    co = [b for b in bl if b["type"] == "callout"]
    ok("Callouts: <aside> mit ⚠️ und 🚨 -> ❗, <callout icon=📝> -> 📌 mit Farbe", [b["data"]["emoji"] for b in co] == ["⚠️", "❗", "📌"]
       and "<b>wichtig</b>" in co[0]["data"]["text"] and (co[2].get("tunes") or {}).get("hkstyle") == {"c": "blue", "bg": True}, co)
    ok("Toggle aus <details>", any(b["type"] == "toggle" and "Aufklappen" in b["data"]["title"] and b["data"]["blocks"] for b in bl))
    ok("Mathe-Block aus $$", any(b["type"] == "math" and b["data"]["tex"] == "E = mc^2" for b in bl), [b for b in bl if b["type"] == "math"])
    ok("Inline-Mathe -> code.hk-math, $5 bleibt Text", '<code class="hk-math">a^2+b^2</code>' in j and "Preis $5" in j)
    ok("Farben: span color, mark highlight_background, highlight-purple", '<span class="hk-c-red">rot</span>' in j
       and '<mark class="hk-bg-yellow">gelb</mark>' in j and '<span class="hk-c-purple">lila</span>' in j, j[:2000])
    ok("Blockfarbe {color=...} an Absatz und Ueberschrift", any(b["type"] == "paragraph" and (b.get("tunes") or {}).get("hkstyle") == {"c": "blue"}
                                                             and "Blaue Zeile" in b["data"]["text"] for b in bl)
       and any(b["type"] == "header" and (b.get("tunes") or {}).get("hkstyle") == {"c": "green", "bg": True} for b in bl))
    cols = [b for b in bl if b["type"] == "columns"]
    ok("Spalten aus <columns>", cols and len(cols[0]["data"]["cols"]) == 2
       and "Links" in json.dumps(cols[0]["data"]["cols"][0]) and "Rechts" in json.dumps(cols[0]["data"]["cols"][1]), cols)
    ok("Code, Aufgaben, Tabelle", "code" in types and any(b["type"] == "list" and b["data"]["style"] == "checklist" for b in bl)
       and any(b["type"] == "table" for b in bl), types)
    # --- Datenbank Projekte ---
    sc = pj["schema"]
    tp = {p["name"]: p["type"] for p in sc["props"]}
    ok("Schema Projekte: Typen", tp == {"Status": "select", "Tags": "multi_select", "Fällig": "date", "Zeitraum": "date", "Termin": "date",
                                        "Fertig": "checkbox", "Budget": "number", "Anteil": "number", "Website": "url", "E-Mail": "url",
                                        "Telefon": "text", "Aufgaben": "relation", "Weitere Aufgaben": "relation", "Verantwortlich": "text",
                                        "Anhänge": "files", "Summe (Formel)": "text", "Anzahl Aufgaben (Rollup)": "text",
                                        "Kategorie": "select", "Erstellt": "date"}, tp)
    stp = prop_by_name(sc, "Status")
    grp = {o["name"]: o.get("group") for o in stp.get("options", [])}
    ok("Status-Eigenschaft mit Gruppen", stp.get("status") is True and grp == {"In progress": "doing", "Done": "done", "Not started": "todo"}, stp)
    ok("Datum: Uhrzeit/Zeitraum am Schema", prop_by_name(sc, "Termin").get("time") is True and prop_by_name(sc, "Zeitraum").get("range") is True
       and prop_by_name(sc, "Termin").get("range") is True)
    ok("Zahl %: fmt percent", prop_by_name(sc, "Anteil").get("fmt") == "percent")
    ok("Formel/Rollup: fx_hint (In Formel umwandeln)", prop_by_name(sc, "Summe (Formel)").get("fx_hint") is True
       and prop_by_name(sc, "Anzahl Aufgaben (Rollup)").get("fx_hint") is True)
    ok("Relation zeigt auf Aufgaben", prop_by_name(sc, "Aufgaben").get("target") == au["id"] and prop_by_name(sc, "Weitere Aufgaben").get("target") == au["id"])
    s, rows = req("GET", f"/api/notes/{pj['id']}/rows")
    by = {r["title"]: r for r in rows.get("rows", [])}
    pid = {p["name"]: p["id"] for p in sc["props"]}
    al = by.get("Projekt Alpha", {}).get("props", {})
    ok("Eintraege: Alpha, Beta, Gamma + Waise", set(by) == {"Projekt Alpha", "Projekt Beta", "Projekt Gamma", "Waise"}, sorted(by))
    sopt = {o["id"]: o["name"] for o in prop_by_name(sc, "Status")["options"]}
    topt = {o["id"]: o["name"] for o in prop_by_name(sc, "Tags")["options"]}
    ok("Werte Alpha: Status, Tags, Datum, Haekchen, Zahl", sopt.get(al.get(pid["Status"])) == "In progress"
       and sorted(topt.get(x) for x in al.get(pid["Tags"], [])) == ["intern", "wichtig"] and al.get(pid["Fällig"]) == "2026-10-27"
       and al.get(pid["Fertig"]) is True and al.get(pid["Budget"]) == 1234.5 and al.get(pid["Anteil"]) == 12, al)
    ok("Werte Alpha: Zeitpunkt, Zeitraum", str(al.get(pid["Termin"], "")).startswith("2026-10-27T15:30")
       and al.get(pid["Zeitraum"]) == "2026-10-01/2026-10-05", al)
    be = by.get("Projekt Beta", {}).get("props", {})
    ok("Werte Beta: Zeitraum mit Uhrzeiten an einem Tag", str(be.get(pid["Termin"], "")).startswith("2026-10-28T10:00")
       and "/2026-10-28T11:30" in str(be.get(pid["Termin"], "")), be)
    ok("Werte: URL, E-Mail -> mailto:, Telefon Text", al.get(pid["Website"]) == "https://example.org/alpha"
       and al.get(pid["E-Mail"]) == "mailto:anna@example.org" and al.get(pid["Telefon"]) == "+49 30 1234567", al)
    ok("Person -> Text, Formel/Rollup -> Ergebniswert", al.get(pid["Verantwortlich"]) == "Anna Beispiel, Bert Muster"
       and al.get(pid["Summe (Formel)"]) == "1234.5" and al.get(pid["Anzahl Aufgaben (Rollup)"]) == "2", al)
    a2 = one(t, "Aufgabe Zwei")
    dop = sorted(x["id"] for x in t.get("Doppelt", []))
    ok("Relation ueber Links (relativ und notion.so)", sorted(al.get(pid["Aufgaben"], [])) == sorted([a1["id"], a2["id"]]), al.get(pid["Aufgaben"]))
    ok("Relation ueber Titel, mehrdeutig -> erster Treffer", sorted(al.get(pid["Weitere Aufgaben"], [])) == sorted([a1["id"], dop[0]]) if dop else False,
       (al.get(pid["Weitere Aufgaben"]), dop))
    fv = al.get(pid["Anhänge"]) or []
    ok("Datei-Eigenschaft aus dem ZIP", len(fv) == 1 and fv[0]["name"] == "plan.pdf" and fv[0]["url"].startswith("/files/"), fv)
    bv = be.get(pid["Anhänge"]) or []
    ok("Datei-Eigenschaft: externe URL nicht uebernommen", len(bv) == 1 and bv[0]["name"] == "plan.pdf", bv)
    # Zeilen-Inhalte
    alp = one(t, "Projekt Alpha")
    aj = json.dumps(alp.get("body"), ensure_ascii=False)
    ok("Zeilen-Inhalt aus der .md (Eigenschaften-Block entfernt)", "Inhalt der Zeile" in aj and "Status: In progress" not in aj
       and "Verantwortlich:" not in aj, aj[:600])
    ok("Zeilen-Inhalt: Link und Bild", f'/notes#{ue["id"]}' in aj and any(b["type"] == "image" for b in alp["body"]["blocks"]), aj[:800])
    ok("Unterseite einer Zeile haengt am Eintrag", one(t, "Notizen").get("parent") == alp.get("id"), one(t, "Notizen"))
    ok("Waise: Eintrag mit Inhalt, ohne Werte", one(t, "Waise").get("parent") == pj["id"] and "Keine Zeile" in json.dumps(one(t, "Waise").get("body")))
    # Aufgaben -> Projekte (Relation auf spaeter angelegte Zeilen)
    sa = au["schema"]
    s, ar = req("GET", f"/api/notes/{au['id']}/rows")
    ab = {r["title"] + str(i): r for i, r in enumerate(ar.get("rows", []))}
    ppid = prop_by_name(sa, "Projekt")["id"]
    zw = next((r for r in ab.values() if r["title"] == "Aufgabe Zwei"), {})
    ok("Relation auf spaeter angelegte Datenbank (nachgetragen)", prop_by_name(sa, "Projekt").get("target") == pj["id"]
       and sorted(zw.get("props", {}).get(ppid, [])) == sorted([alp["id"], one(t, "Projekt Beta")["id"]]), zw)
    ok("Doppelte Titel: beide Eintraege mit eigenem Inhalt", len(t.get("Doppelt", [])) == 2
       and {"Erstes Doppel" in json.dumps(x["body"], ensure_ascii=False) for x in t["Doppelt"]} == {True, False})
    # Bericht
    c = codes(st["report"])
    ok("Bericht: fehlender Link, externes Bild, Waise, mehrdeutig, Personen, Formel, unbenutzt",
       any("Fehlt" in x for x in c.get("link_missing", [])) and c.get("ext_image") and c.get("orphan") and c.get("rel_ambiguous")
       and c.get("person_text") and len(c.get("fx_text", [])) == 2 and any("unbenutzt.png" in x for x in c.get("ignored", [])), c)
    ok("Zaehler", st["report"]["counts"].get("databases") == 2 and st["report"]["counts"].get("rows") == 8
       and st["report"]["counts"].get("files") == 4, st["report"]["counts"])
    ok("Importbericht-Seite angelegt", st["report"].get("page"))
    # "In Formel umwandeln": Schema-PATCH type formula -> fx_hint weg, Wert gerechnet
    s, cur = req("GET", f"/api/notes/{pj['id']}")
    sc2 = cur["schema"]
    fx = prop_by_name(sc2, "Summe (Formel)")
    fx.update(type="formula", expr=f'prop("{pid["Budget"]}") * 2')
    s, r = req("PATCH", f"/api/notes/{pj['id']}", {"schema": sc2})
    s2, rows2 = req("GET", f"/api/notes/{pj['id']}/rows")
    nfx = prop_by_name(rows2.get("schema", {}), "Summe (Formel)") or {}
    alv = next((x["props"] for x in rows2.get("rows", []) if x["title"] == "Projekt Alpha"), {})
    ok("In Formel umwandeln: Typ formula ohne fx_hint, Wert gerechnet", s == 200 and nfx.get("type") == "formula" and "fx_hint" not in nfx
       and alv.get(nfx.get("id")) == 2469, (s, r, nfx, alv.get(nfx.get("id"))))
    time.sleep(2.5)
    s, bk = req("GET", f"/api/notes/{ue['id']}/backlinks")
    lst = bk if isinstance(bk, list) else (bk.get("backlinks") or bk.get("items") or []) if isinstance(bk, dict) else []
    ok("Rueckverweise zeigen auf neue Seiten", s == 200 and any(x.get("id") == ws["id"] for x in lst), bk)


def variant_b():
    print("--- Variante B: Teil-ZIPs + _all.csv")
    f = fixture()
    keys = sorted(f)
    au = next(k for k in keys if k.startswith("Aufgaben") and k.endswith(".csv"))
    f[au[:-4] + "_all.csv"] = f[au] + "Gefiltert,,No,Hoch\n".encode()
    keys = sorted(f)
    p1 = {k: f[k] for k in keys[: len(keys) // 2]}
    p2 = {k: f[k] for k in keys[len(keys) // 2:]}
    outer = zbytes({"Export-1234abcd-Part-1.zip": zbytes(p1), "Export-1234abcd-Part-2.zip": zbytes(p2)})
    s, d = upload("Export-1234abcd.zip", outer)
    ok("Upload: aeusseres ZIP nur mit Teil-ZIPs -> notion", s == 200 and d.get("kind") == "notion", d)
    s, a, st, _ = run_import(outer)
    ok("Analyse ueber beide Teile: 2 Datenbanken, _all.csv (8 Eintraege)", s == 200 and a["notion"]["databases"] == 2 and a["notion"]["rows"] == 8, a)
    ok("Lauf fertig", st.get("state") == "done", st)
    if st.get("state") != "done":
        return
    t = tree(st["root"])
    ok("Gefilterte Zeile aus _all.csv da", "Gefiltert" in t and one(t, "Gefiltert").get("parent") == one(t, "Aufgaben").get("id"), sorted(t))
    ok("Teil-uebergreifende Verweise", f'/notes#{one(t, "Unterseite Eins").get("id")}' in json.dumps(one(t, "Wiki Start").get("body")))
    with connect() as c:
        left = [x for x in os.listdir(os.path.join(os.environ["STATE_DIR"], "import-tmp"))] if os.path.isdir(os.path.join(os.environ["STATE_DIR"], "import-tmp")) else []
    ok("Kopien der Teil-ZIPs aufgeraeumt", not left, left)


def variant_c():
    print("--- Variante C: andere Schreibweisen")
    u1, u2, u3 = "1a2b3c4d-1111-2222-3333-444455556666", "9f8e7d6c-aaaa-bbbb-cccc-ddddeeeeffff", "0f0f0f0f-0000-1111-2222-333344445555"
    rows = ["Name,Kunde,Betrag,Datum,Termin,Status,Tags"] + \
           ["Rechnung 1,Kunde A,$1200.50,27. Oktober 2026,\"October 27, 2026 9:00 AM (GMT+2)\",Offen,a",
            "Rechnung 2,Kunde B,$99,28. Oktober 2026,\"October 28, 2026\",Bezahlt,\"a, b\"",
            "Rechnung 3,\"Kunde A, Kunde B\",$5,29. Oktober 2026,,Offen,b"]
    wide = ["Name," + ",".join(f"Feld {i}" for i in range(1, 34)), "Breit," + ",".join(f"w{i}" for i in range(1, 34))]
    files = {
        f"Kunden {u1}.md": "# Kunden\r\n\r\nBeschreibung der Datenbank.\r\n".encode(),
        f"Kunden {u1}.csv": "Name,Ort\r\nKunde A,Nord\r\nKunde B,Süd\r\n".encode("utf-8-sig"),
        "Kunden/Kunde A 11112222333344445555666677778888.md": "# Kunde A\r\n\r\nOrt: Nord\r\n\r\nGuter Kunde.\r\n".encode(),
        f"Rechnungen {u2}.csv": "\r\n".join(rows).encode("utf-8-sig"),
        f"Breit {u3}.csv": "\n".join(wide).encode(),
        "Start 22223333444455556666777788889999.md": (f"# Start\n\n[Kunden](Kunden%20{u1}.csv)\n\n"
                                                       f"Siehe [Kunde A](Kunden/Kunde%20A%2011112222333344445555666677778888.md)\n").encode(),
    }
    s, a, st, _ = run_import(zbytes(files), name="notion-export.zip")
    g = {db["title"]: {c["name"]: c["guess"] for c in db["columns"]} for db in a.get("notion", {}).get("dbs", [])}
    ok("Analyse: UUID-Namen, 3 Datenbanken", s == 200 and a["notion"]["databases"] == 3, a)
    ok("Typen C: Relation ueber Titel, Zahl $, Datum (deutsch), Datum mit GMT, Status, Mehrfach",
       g.get("Rechnungen") == {"Kunde": "relation", "Betrag": "number", "Datum": "date", "Termin": "date", "Status": "status", "Tags": "multi_select"},
       g.get("Rechnungen"))
    ok("Warnung > 30 Spalten", any("30" in w for w in a.get("warnings", [])), a.get("warnings"))
    ok("Lauf fertig", st.get("state") == "done", st)
    if st.get("state") != "done":
        return
    t = tree(st["root"])
    ku, re_ = one(t, "Kunden"), one(t, "Rechnungen")
    ok("Datenbank mit .md daneben ist Datenbank, Ordner ohne ID haengt dran", ku.get("kind") == "database"
       and one(t, "Kunde A").get("parent") == ku.get("id"), (ku.get("kind"), one(t, "Kunde A")))
    ok("Zeilen-Inhalt mit CRLF, Eigenschaft entfernt", "Guter Kunde" in json.dumps(one(t, "Kunde A").get("body"), ensure_ascii=False)
       and "Ort: Nord" not in json.dumps(one(t, "Kunde A").get("body"), ensure_ascii=False))
    sb = json.dumps(one(t, "Start").get("body"))
    ok("Start: Datenbank-Block und Zeilen-Link", f'"db": {ku.get("id")}' in sb and f'/notes#{one(t, "Kunde A").get("id")}' in sb, sb[:500])
    sc = re_["schema"]
    pid = {p["name"]: p["id"] for p in sc["props"]}
    s, rows = req("GET", f"/api/notes/{re_['id']}/rows")
    by = {r["title"]: r["props"] for r in rows.get("rows", [])}
    ka, kb = [x["id"] for x in t.get("Kunde A", [])], [x["id"] for x in t.get("Kunde B", [])]
    ok("Relation ueber Titel", by.get("Rechnung 3", {}).get(pid["Kunde"]) and sorted(by["Rechnung 3"][pid["Kunde"]]) == sorted(ka + kb), (by, ka, kb))
    ok("Zahl mit $", by.get("Rechnung 1", {}).get(pid["Betrag"]) == 1200.5, by.get("Rechnung 1"))
    ok("Deutsches Datum, Zeitpunkt mit GMT+2", by.get("Rechnung 1", {}).get(pid["Datum"]) == "2026-10-27"
       and str(by.get("Rechnung 1", {}).get(pid["Termin"])).startswith("2026-10-27T09:00+02:00"), by.get("Rechnung 1"))
    stp = prop_by_name(sc, "Status")
    ok("Status-Gruppen (Offen -> todo, Bezahlt -> todo als Standard)", stp.get("status") and {o["name"]: o["group"] for o in stp["options"]}.get("Offen") == "todo", stp)
    br = one(t, "Breit")
    ok("Mehr als 30 Spalten: 30 Eigenschaften, Rest als Liste in der Zeile", len(br["schema"]["props"]) == 30
       and "Feld 33" in json.dumps(one(t, "Breit").get("body") if one(t, "Breit").get("kind") == "page" else
                                   next(x for x in t["Breit"] if x["kind"] == "page")["body"], ensure_ascii=False), br["schema"]["props"][-1:])
    ok("Bericht: zu viele Spalten", "too_many_cols" in codes(st["report"]), codes(st["report"]))


def variant_d():
    print("--- Variante D: Sicherheit, Fehler, Dialog-Typen, Fremdzugriff")
    good = {"Seite aaaabbbbccccddddeeeeffff00001111.md": b"# Seite\n\nText\n"}
    li = zipfile.ZipInfo("Link bbbbccccddddeeeeffff000011112222.md")
    li.create_system = 3
    li.external_attr = (stat.S_IFLNK | 0o777) << 16
    bomb = b"0" * (40 * 1024 * 1024)
    part1 = zbytes([("Seite aaaabbbbccccddddeeeeffff00001111.md", good["Seite aaaabbbbccccddddeeeeffff00001111.md"]), (li, b"/etc/passwd"),
                    ("innen.zip", zbytes({"x.md": b"x"})), ("bombe.png", bomb), ("../../boese ccccddddeeeeffff0000111122223333.md", b"# boese")])
    outer = zbytes({"Export-x-Part-1.zip": part1, "Export-x-Part-2.zip": b"kein zip"})
    s, a, st, _ = run_import(outer)
    c = codes(st.get("report") or {"groups": []})
    ok("Teil-ZIP: ZIP im Teil nicht entpackt", c.get("nested_zip") == ["innen.zip"], c)
    ok("Teil-ZIP: Symlink, Bombe, boeser Pfad verworfen", c.get("symlink") and c.get("bomb") and c.get("bad_path"), c)
    ok("Teil ohne gueltiges ZIP -> Bericht, Rest laeuft", any("Part-2" in x for x in c.get("not_zip", [])) and st.get("state") == "done", (c, st.get("state")))
    ok("Nichts ausserhalb geschrieben", not os.path.exists(os.path.join(os.environ["STATE_DIR"], "..", "boese.md")))
    # HTML-Export
    s, d = upload("html.zip", zbytes({"Seite aaaabbbbccccddddeeeeffff00001111.html": b"<html></html>"}))
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
    ok("HTML-Export: Hinweis auf Markdown & CSV", s == 200 and "Markdown" in str(a.get("error", "")), a)
    req("DELETE", f"/api/import/{d['token']}")
    # Typen aus dem Dialog
    f = fixture()
    pj = next(k for k in f if k.endswith(".csv") and "Projekte" in k)
    s, a, st, _ = run_import(zbytes(f), body={"types": {pj: {"1": "text", "18": "skip", "6": "text"}}})
    ok("Dialog-Typen: Lauf fertig", st.get("state") == "done", st)
    if st.get("state") == "done":
        sc = one(tree(st["root"]), "Projekte")["schema"]
        tp = {p["name"]: p["type"] for p in sc["props"]}
        ok("Dialog-Typen: Status -> Text, Kategorie weggelassen, Fertig -> Text", tp.get("Status") == "text" and "Kategorie" not in tp
           and tp.get("Fertig") == "text", tp)
    s, r = req("POST", "/api/import/xyz/run", {"kind": "notion", "types": [1, 2]})
    ok("Fremdes/unbekanntes Token -> 404", s == 404, (s, r))
    s, d = upload("e.zip", zbytes(good))
    s2, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "notion"}, u=BU)
    ok("Token eines anderen Kontos -> 404", s2 == 404, (s2, r))
    s3, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "notion", "types": "x"})
    ok("types kein Objekt -> 400", s3 == 400, (s3, r))
    req("DELETE", f"/api/import/{d['token']}")


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'in-%'")
        c.execute("DELETE FROM import_run WHERE uid LIKE 'in-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'in-%'")


if __name__ == "__main__":
    main()
    print(f"\nIMPORT-NOTION {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
