"""Trilium-Import (2.5, saga/import_trilium.py, saga/html2blocks.py) mit erfundenen Testdaten (tests/trilium_fixture.py).

Prueft: Erkennung (ZIP mit !!!meta.json, SQLite-Datei am Inhalt), Analyse (Zahlen, Passwortfeld nur bei geschuetzten
Notizen in der .db, Passwort nie in der Antwort), STRUKTUR exakt (erwarteter Baum mit Reihenfolge, Titeln, Icons und
Blocktypen je Seite) fuer .db neu (mit/ohne Passwort), .db alt (note_contents), ZIP HTML und ZIP Markdown; Code-Notizen
(auch .dat) genau ein Code-Block mit Sprache, Anhang an der Stelle bzw. unten, Anhaenge genau einmal, interne Links und
Include -> Seitenlinks auf die neuen IDs, Labels -> Tags, Rest -> Eigenschaften, Klon -> Verweisseite, Prefix im Titel,
geloeschte/versteckte Notizen und Revisionen weg, Systemattribute weg, keine leeren Absaetze, kein doppelter Titel;
geschuetzt mit/ohne/falschem Passwort (falsch: 400, nichts angelegt), Rate-Limit (429), Passwort nirgends (Server-Log,
Bericht, Antworten, pg_dump der Datenbank), unbekanntes Schema, Datei mit View/Trigger, riesiger Inhalt, keine
Serverfehler. Wegwerf-Konten tr-*."""
import base64
import http.client
import json
import os
import subprocess
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner

import auth
from db import connect

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trilium_fixture as F  # noqa: E402

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
A, BU, CU = "tr-a", "tr-b", "tr-c"
TMP = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "trilium-test")
LOG = os.environ.get("SAGA_SERVER_LOG") or os.path.join(os.environ.get("STATE_DIR", "/tmp"), "server.log")
res = []
seen_text = []                      # alle Antworten (Passwort darf nirgends auftauchen)


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:2500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, body=None, u=A, raw=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=180)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", "Cookie": "saga=" + ck(u)}
    data = None
    if raw is not None:
        data, h["Content-Type"] = raw, "application/octet-stream"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    seen_text.append(d.decode("utf-8", "replace"))
    try:
        return r.status, json.loads(d) if d[:1] in (b"[", b"{") else d
    except ValueError:
        return r.status, d


def upload(name, data, u=A):
    return req("POST", "/api/import/upload?name=" + urllib.parse.quote(name) + "&replace=1", raw=data, u=u)


def wait(rid, u=A):
    t0 = time.time()
    d = {}
    while time.time() - t0 < 180:
        s, d = req("GET", f"/api/import/runs/{rid}", u=u)
        if s == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.25)
    return d


def run_import(data, name, body, u=A):
    s, d = upload(name, data, u)
    if s != 200:
        return s, d, {}
    s, r = req("POST", f"/api/import/{d['token']}/run", body, u=u)
    if s != 202:
        return s, r, {}
    return s, r, wait(r["run_id"], u)


def rows(root):
    with connect() as c:
        got = c.execute("WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id "
                        "WHERE n.deleted_at IS NULL) SELECT n.id, n.title, n.parent_id, n.body, n.tags, n.icon, n.sort FROM note n "
                        "JOIN t ON n.id=t.id", (root,)).fetchall()
    return [{"id": r[0], "title": r[1], "parent": r[2], "body": json.loads(r[3] or '{"blocks": []}')["blocks"],
             "tags": json.loads(r[4] or "[]"), "icon": r[5], "sort": r[6]} for r in got]


def tree_lines(root):
    """Baum ab root -> Zeilen "  "*tiefe + titel [blocktypen] {icon}, Geschwister nach sort."""
    rs = rows(root)
    kids = {}
    for r in rs:
        kids.setdefault(r["parent"], []).append(r)
    out = []

    def walk(r, d):
        icon = f" {r['icon']}" if r["icon"] and r["icon"] != "📥" else ""
        if r["icon"] == "📋" and r["title"].startswith("Importbericht"):      # Datum/Inhalt wechseln: nur Lage pruefen
            out.append("  " * d + "Importbericht 📋")
            return
        out.append("  " * d + r["title"] + " [" + ",".join(b["type"] for b in r["body"]) + "]" + icon)
        for k in sorted(kids.get(r["id"], []), key=lambda x: (x["sort"] or 0, x["id"])):
            walk(k, d + 1)
    top = next(r for r in rs if r["id"] == root)
    walk(top, 0)
    return out, {r["title"] + ("#clone" if r["icon"] == "🔗" else ""): r for r in rs}


SRV_BLOCKS = "paragraph,header,list,list,table,quote,callout,code,delimiter,paragraph,image,paragraph,paragraph,paragraph,math,toggle"
# Sammelseite: Info-Kasten + Liste der obersten Seiten; Bericht als letzte Unterseite. Sammlungen (book) -> Karten bzw.
# Liste, Icons aus iconClass (eigen/Vorlage/vererbt) bzw. Typ-Standard.
FULL = f"""Trilium-Test [callout,list]
  Projekte [toggle,paragraph] 📁
    Server-Setup [{SRV_BLOCKS}]
      docker-compose.yml [code] 💻
      Konfig [code] 💻
    Skript - deploy.sh [code] 💻
    Diagramm [image,callout] 🎨
    Mermaid [code] 📈
    Leer [callout] 🎨
  Rezepte [columns] 📚
    Brot [paragraph,attaches]
    Server-Setup [paragraph] 🔗
  Sammlung [columns] 📊
    Eins [paragraph,paragraph]
    Zwei [code] 💻
  Liste [list] ⭐
    Listenkind [paragraph] ⭐
  Kalender [callout,list] 📚
    Termin [paragraph]
  Ordner [list]
    Unterseite [paragraph]
  Foto.png [image]
  Tresor [paragraph]
    Offen darunter [paragraph]
  Geheimnis [paragraph]
  SVG-Text [paragraph,image,image,attaches]
  Anhang [paragraph,list,code,paragraph,code,table]
  Breit [paragraph]
  Suche [callout] 🔎
  Importbericht 📋"""
LOCKED = FULL.replace("""  Tresor [paragraph]
    Offen darunter [paragraph]
  Geheimnis [paragraph]
""", """  Geschützte Notiz [callout] 🔒
    Offen darunter [paragraph]
""")
ZIP = f"""Trilium-Test [callout,list]
  Projekte [toggle,paragraph] 📁
    Server-Setup [{SRV_BLOCKS}]
      docker-compose.yml [code] 💻
      Konfig [code] 💻
    Skript - deploy.sh [code] 💻
    Diagramm [image,callout] 🎨
  Rezepte [columns] 📚
    Brot [paragraph,attaches]
    Server-Setup [paragraph] 🔗
  Foto.png [image]
  Geschützte Notiz [callout] 🔒
    Offen darunter [paragraph]
  Importbericht 📋"""
OLD = ZIP.replace("    Diagramm [image,callout] 🎨\n", "").replace("Brot [paragraph,attaches]", "Brot [paragraph]")
MD = ZIP.replace(SRV_BLOCKS, "paragraph,header,list,list,table,quote,callout,code,delimiter,paragraph,image,paragraph,paragraph,math")


def all_blocks(blocks):
    for b in blocks:
        yield b
        d = b.get("data") or {}
        for x in d.get("blocks") or []:
            yield from all_blocks([x])


def check_tree(label, st, expect):
    ok(f"{label}: Lauf fertig", st.get("state") == "done", st)
    if st.get("state") != "done":
        return {}
    lines, t = tree_lines(st["root"])
    ok(f"{label}: Struktur exakt (Baum, Reihenfolge, Titel, Icons, Blocktypen)", lines == expect.split("\n"),
       "\n".join(lines) + "\n--- erwartet ---\n" + expect)
    empty = [r["title"] for r in t.values() for b in all_blocks(r["body"]) if b["type"] == "paragraph" and not b["data"].get("text")]
    ok(f"{label}: keine leeren Absaetze", not empty, empty)
    dup = [r["title"] for r in t.values() if r["body"] and r["body"][0]["type"] == "header"
           and r["body"][0]["data"].get("text") == r["title"]]
    ok(f"{label}: kein doppelter Titel als erste Ueberschrift", not dup, dup)
    return t


def check_content(label, t, md=False):
    srv, dc, kf, dp = t["Server-Setup"], t["docker-compose.yml"], t["Konfig"], t["Skript - deploy.sh"]
    ok(f"{label}: Code-Notizen genau ein Code-Block mit Sprache (yaml, .dat -> ini, sh -> bash)",
       [b["data"]["language"] for b in dc["body"] + kf["body"] + dp["body"]] == ["yaml", "ini", "bash"]
       and dc["body"][0]["data"]["code"].startswith("services:") and len(dc["body"]) == 1, (dc["body"], kf["body"], dp["body"]))
    sb = srv["body"]
    code = next(b for b in sb if b["type"] == "code")
    ok(f"{label}: Code-Block im Text mit Sprache", code["data"] == {"code": "a: 1\nb: 2", "language": "yaml"}, code)
    lst = [b for b in sb if b["type"] == "list"]
    ok(f"{label}: verschachtelte Liste + Aufgabenliste", lst[0]["data"]["style"] == "ordered"
       and lst[0]["data"]["items"][0]["items"][0]["content"] == "Unterpunkt"
       and lst[1]["data"]["style"] == "checklist" and [i["meta"].get("checked") for i in lst[1]["data"]["items"]] == [True, False], lst)
    tb = next(b for b in sb if b["type"] == "table")
    ok(f"{label}: Tabelle mit Kopfzeile", tb["data"]["withHeadings"] and tb["data"]["content"] == [["Dienst", "Port"], ["Web", "8080"]], tb)
    p0 = sb[0]["data"]["text"]
    ok(f"{label}: Inline-Formatierung", "<b>fetter</b>" in p0 and "<i>kursiver</i>" in p0 and 'class="inline-code">inline' in p0
       and 'href="https://example.org"' in p0, p0)
    ok(f"{label}: Hinweisbox -> Callout", any(b["type"] == "callout" and "Achtung" in b["data"]["text"] for b in sb))
    i = next(k for k, b in enumerate(sb) if b["type"] == "image")
    ok(f"{label}: Bild an seiner Stelle (zwischen Vorher/nachher)", sb[i - 1]["data"]["text"] == "Vorher"
       and sb[i + 1]["data"]["text"] == "nachher" and sb[i]["data"]["file"]["url"].startswith("/files/"), sb[i - 1:i + 2])
    links = json.dumps(sb)
    ok(f"{label}: interne Links -> neue Seiten-IDs", f'/notes#{dc["id"]}' in links and f'/notes#{t["Brot"]["id"]}' in links, links[:600])
    if not md:
        inc = [b for b in sb if b["type"] == "paragraph" and b["data"]["text"].startswith("↪")]
        ok(f"{label}: Include-Notiz -> Seitenlink", inc and f'/notes#{dc["id"]}' in inc[0]["data"]["text"], inc)
    pj = t["Projekte"]
    props = json.dumps(pj["body"][0])
    ok(f"{label}: Labels ohne Wert -> Tags, Systemattribute weg", pj["tags"] == ["wichtig"] and "iconClass" not in props
       and "bx-folder" not in props, (pj["tags"], props))
    ok(f"{label}: Eigenschaften (Wert-Label, Relation als Seitenlink)", "status:</b> aktiv" in props
       and f'/notes#{t["Rezepte"]["id"]}' in props, props)
    cl = t["Server-Setup#clone"]
    ok(f"{label}: Klon -> Verweisseite auf die echte Seite", f'/notes#{srv["id"]}' in json.dumps(cl["body"]) and cl["parent"] == t["Rezepte"]["id"])
    br = t["Brot"]["body"]
    ok(f"{label}: nicht im Text verwendeter Anhang unten auf der Seite", br[-1]["type"] == "attaches"
       and br[-1]["data"]["file"]["name"] == "rezept.pdf", br)
    ok(f"{label}: Bild-Notiz -> Bild-Block", t["Foto.png"]["body"][0]["type"] == "image")


def note_row(nid):
    with connect() as c:
        return c.execute("SELECT parent_id, wide, icon FROM note WHERE id=?", (nid,)).fetchone()


def file_row(url):
    with connect() as c:
        return c.execute("SELECT name, mime, is_image FROM file WHERE id=?", (url.rsplit("/", 1)[-1],)).fetchone()


def check_new(label, t, st):
    """Fixrunde: Uebersicht, Sammelseite/Bericht, SVG, Datei-Link, Listen/Code/Tabelle, Sondertypen (nur Schema neu)."""
    from saga.files import FILES_DIR
    rep = st["report"]
    root = next(r for r in t.values() if r["id"] == st["root"])
    info, lst = root["body"][0], root["body"][1]
    ok(f"{label}: Sammelseite - Info-Kasten mit Zahlen und Link zum Bericht, Bericht ist ihre Unterseite",
       info["type"] == "callout" and "Aus Trilium importiert" in info["data"]["text"] and f'/notes#{rep["page"]}' in info["data"]["text"]
       and note_row(rep["page"])[0] == st["root"], (info, rep.get("page")))
    tops = [r for r in t.values() if r["parent"] == st["root"] and r["icon"] != "📋"]
    ok(f"{label}: Sammelseite - Liste verlinkt alle obersten Seiten in Reihenfolge",
       [i["content"] for i in lst["data"]["items"]] == [f'<a href="/notes#{r["id"]}">{r["title"]}</a>'
                                                         for r in sorted(tops, key=lambda x: (x["sort"], x["id"]))], lst)
    paths = [x["path"] for g in rep["groups"] for x in g["items"]]
    codes = {g["code"]: g["text"] for g in rep["groups"]}
    ok(f"{label}: Bericht - verwaiste Anhaenge (2), viewConfig (1), Farben (1) gezaehlt, kein _hidden-Pfad", "_hidden" not in paths
       and codes.get("trilium_orphans", "").endswith(": 2") and codes.get("trilium_viewcfg", "").endswith(": 1")
       and codes.get("trilium_color", "").endswith(": 1"), (codes, paths))
    sm = t["Sammlung"]["body"][0]["data"]["cols"]
    ok(f"{label}: Dashboard-Sammlung -> Karten in der Anordnung der viewConfig (Zwei vor Eins), Auszug, Ordner-/Seitensymbol",
       len(sm) == 2 and sm[0]["blocks"][0]["data"]["text"] == f'<b><a href="/notes#{t["Zwei"]["id"]}">Zwei</a></b><br>YAML'
       and sm[1]["blocks"][0]["data"]["text"] == f'<b><a href="/notes#{t["Eins"]["id"]}">Eins</a></b><br>Erster Inhalt hier zweiter'
       and [c["blocks"][0]["data"]["emoji"] for c in sm] == ["📄", "📄"], sm)
    rz = t["Rezepte"]["body"][0]["data"]["cols"]
    ok(f"{label}: Sammlung ohne viewType -> Karten (Klon verlinkt die echte Seite)", len(rz) == 2
       and f'/notes#{t["Brot"]["id"]}' in rz[0]["blocks"][0]["data"]["text"]
       and f'/notes#{t["Server-Setup"]["id"]}' in rz[1]["blocks"][0]["data"]["text"]
       and rz[1]["blocks"][0]["data"]["emoji"] == "📁", rz)
    li = t["Liste"]["body"][0]["data"]["items"]
    ok(f"{label}: Listen-Sammlung -> Liste mit Auszug", [i["content"] for i in li] == [f'<a href="/notes#{t["Listenkind"]["id"]}">Listenkind</a> – Kurz'], li)
    kb = t["Kalender"]["body"]
    ok(f"{label}: Kalender-Ansicht -> Hinweis + Liste + Bericht", "calendar" in kb[0]["data"]["text"]
       and f'/notes#{t["Termin"]["id"]}' in kb[1]["data"]["items"][0]["content"] and "trilium_view" in codes, (kb, codes))
    ob = t["Ordner"]["body"]
    ok(f"{label}: leere Ordnerseite (verwaister Anhang weg) -> Liste der Unterseiten ohne Auszug",
       ob[0]["data"]["items"][0]["content"] == f'<a href="/notes#{t["Unterseite"]["id"]}">Unterseite</a>', ob)
    sv = t["SVG-Text"]["body"]
    f1, f2, f3 = (file_row(b["data"]["file"]["url"]) for b in sv[1:4])
    ok(f"{label}: SVG als Bild an der Stelle (Breite/Ausrichtung), zweifelhaftes SVG (foreignObject) als Datei",
       sv[1]["data"].get("width") == 40 and sv[1]["data"].get("align") == "right" and sv[2]["data"].get("width") == 39
       and f1[1:] == ("image/svg+xml", 1) and f2[1:] == ("image/svg+xml", 1) and f3[1:] == ("application/octet-stream", 0),
       (sv, f1, f2, f3))
    with open(os.path.join(FILES_DIR, sv[2]["data"]["file"]["url"].rsplit("/", 1)[-1]), "rb") as f:
        bad = f.read().lower()
    ok(f"{label}: SVG bereinigt gespeichert (kein script/onload/javascript:/fremde Adresse/DOCTYPE)",
       not any(x in bad for x in (b"<script", b"onload", b"javascript", b"x.invalid", b"<!doctype", b"behavior")) and b"<rect" in bad, bad[:500])
    s, h, _ = raw_get(sv[2]["data"]["file"]["url"])
    ok(f"{label}: SVG-Auslieferung mit CSP sandbox + nosniff", s == 200 and h.get("content-type", "").startswith("image/svg+xml")
       and "sandbox" in h.get("content-security-policy", "") and h.get("x-content-type-options") == "nosniff", (s, h))
    ab = t["Anhang"]["body"]
    furl = next((u for u in [file_url_of(A, "notiz.txt")] if u), "")
    ok(f"{label}: Anhang-Link im Text als /files/-Verweis, Datei nicht zusaetzlich unten", furl and f'href="{furl}"' in ab[0]["data"]["text"]
       and not any(b["type"] == "attaches" for b in ab), ab[0])
    ok(f"{label}: ol start/Zaehlart -> meta", ab[1]["data"]["meta"] == {"start": 3, "counterType": "lower-alpha"}, ab[1])
    ok(f"{label}: Trilium-automatisch: erkannt (bash) bzw. Sprachname im Absatz davor (yaml), Absatz bleibt",
       ab[2]["data"]["language"] == "bash" and ab[3]["data"]["text"] == "yaml" and ab[4]["data"]["language"] == "yaml"
       and "_auto" not in ab[2]["data"], ab[2:5])
    ok(f"{label}: Tabellen-Spaltenbreiten", ab[5]["data"].get("colWidths") == [20, 80], ab[5])
    ok(f"{label}: fullContentWidth -> volle Breite, Farbe verworfen", note_row(t["Breit"]["id"])[1] == 1
       and "ff0000" not in json.dumps(t["Breit"]["body"]))
    ok(f"{label}: gespeicherte Suche -> Hinweis mit Suchtext", "#wichtig" in t["Suche"]["body"][0]["data"]["text"], t["Suche"]["body"])
    mm = t["Mermaid"]["body"]
    ok(f"{label}: Mermaid -> Diagramm (Ansicht Vorschau) ohne zusaetzliche SVG-Karte", len(mm) == 1
       and mm[0]["data"].get("language") == "mermaid" and mm[0]["data"].get("view") == "preview", mm)
    ok(f"{label}: leere Zeichenflaeche -> eigener Hinweis", "Leere Zeichenfläche" in t["Leer"]["body"][0]["data"]["text"])
    ok(f"{label}: keine Datei-Bloecke ohne Datei, keine leeren Bloecke", not [r["title"] for r in t.values() for b in all_blocks(r["body"])
       if b["type"] in ("image", "attaches") and not (b["data"].get("file") or {}).get("url")])


def file_url_of(u, name):
    with connect() as c:
        r = c.execute("SELECT id FROM file WHERE uid=? AND name=?", (u, name)).fetchone()
    return f"/files/{r[0]}" if r else ""


def raw_get(path, u=A):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    c.request("GET", path, headers={"Host": HOST, "Cookie": "saga=" + ck(u)})
    r = c.getresponse()
    d = r.read()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, d


def files_of(u):
    with connect() as c:
        return sorted(r[0] for r in c.execute("SELECT name FROM file WHERE uid=?", (u,)).fetchall())


def main():
    from saga.i18n import set_lang
    set_lang("de")
    os.makedirs(TMP, exist_ok=True)
    cleanup()
    for u in (A, BU, CU):
        auth.create_user(u, "Tr-Pass-1234", u.upper())
    log0 = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    try:
        run()
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8", errors="replace") as f:
                f.seek(log0)
                log = f.read()
            ok("Server-Log: Passwort taucht nicht auf", F.PW not in log)
            ok("Server-Log: keine Fehler aus Import-Laeufen / Tracebacks",
               "Traceback" not in log and not any(ln.startswith("import ") for ln in log.splitlines()), log[-3000:])
            ok("Server-Log: keine 5xx", " 500 " not in log and " 502 " not in log, log[-2000:])
        ok("Antworten: Passwort taucht nirgends auf", not any(F.PW in x for x in seen_text))
        dump = subprocess.run(["/usr/lib/postgresql/16/bin/pg_dump", os.environ["DATABASE_URL"]], capture_output=True, text=True)
        ok("Datenbank (pg_dump): Passwort taucht nicht auf", dump.returncode == 0 and F.PW not in dump.stdout, dump.stderr[:300])
        ok("Datenbank: Revisionen/geloeschte/System-Notizen nicht uebernommen", dump.returncode == 0 and "REVISION-ALT" not in dump.stdout
           and "GELOESCHT-INHALT" not in dump.stdout and "SYSTEM-NOTIZ" not in dump.stdout)
    finally:
        cleanup()


def run():
    db_new = F.build_db(os.path.join(TMP, "neu.db"))
    # --- Erkennung, Analyse ------------------------------------------------------------------------------------------
    s, d = upload("sicherung.db", db_new)
    ok("Upload .db: Art trilium (am Inhalt erkannt)", s == 200 and d.get("kind") == "trilium", d)
    s, d2 = upload("irgendwas.bin", db_new)
    ok("Upload SQLite ohne Endung: auch trilium", s == 200 and d2.get("kind") == "trilium", d2)
    s, a = req("POST", f"/api/import/{d2['token']}/analyze", {})
    tr = a.get("trilium") or {}
    ok("Analyse .db: Zahlen, Quelle, Passwortfeld noetig", s == 200 and tr.get("source") == "db" and tr.get("schema") == "blobs"
       and tr.get("protected") == 2 and tr.get("password") is True and tr.get("clones") == 1 and tr.get("code") == 5 and tr.get("files") == 8
       and tr.get("unsupported") == 3 and tr.get("hidden") >= 4, a)
    s, d = upload("export.zip", F.build_zip("html"))
    ok("Upload ZIP mit !!!meta.json: Art trilium", s == 200 and d.get("kind") == "trilium", d)
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
    tr = a.get("trilium") or {}
    ok("Analyse ZIP: kein Passwortfeld, Hinweis auf geschuetzte", s == 200 and tr.get("source") == "zip" and tr.get("format") == "html"
       and tr.get("password") is False and tr.get("protected") == 2 and any(".db" in w for w in a.get("warnings", [])), a)
    s, d = upload("kaputt.db", b"keine Datenbank" * 10)
    ok("Datei ohne SQLite-Kopf: keine Trilium-Art", s == 200 and d.get("kind") is None, d)

    # --- geschuetzt: falsches Passwort -> nichts angelegt ------------------------------------------------------------
    with connect() as c:
        n0 = c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (A,)).fetchone()[0]
    s, d = upload("sicherung.db", db_new)
    s, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "trilium", "title": "Trilium-Test", "password": "falsch-123"})
    with connect() as c:
        n1 = c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (A,)).fetchone()[0]
        runs = c.execute("SELECT COUNT(*) FROM import_run WHERE uid=?", (A,)).fetchone()[0]
    ok("Falsches Passwort: 400 mit klarer Meldung, nichts angelegt, kein Lauf", s == 400 and "falsch" in (r.get("error") or "")
       and n1 == n0 and runs == 0, (s, r, n0, n1, runs))

    # --- ohne Passwort ---------------------------------------------------------------------------------------------------
    s, r, st = run_import(db_new, "sicherung.db", {"kind": "trilium", "title": "Trilium-Test"})
    t = check_tree(".db ohne Passwort", st, LOCKED)
    if t:
        rep = st["report"]
        prot = [x for g in rep["groups"] if g["code"] == "protected" for x in g["items"]]
        ok("ohne Passwort: geschuetzte im Bericht nur mit Position", len(prot) == 2 and all(x["path"].startswith("🔒") for x in prot), rep)
        ok("ohne Passwort: geschuetzte Inhalte nicht in Saga", "Geheimer Inhalt" not in json.dumps([x["body"] for x in t.values()]))
        check_content(".db", t)
        ok(".db: Anhaenge genau einmal (ohne verwaiste, viewConfig, Mermaid-Vorschau)", files_of(A) == sorted(
            ["Foto.png", "plan.png", "rezept.pdf", "canvas-export.svg", "gut.svg", "boese.svg", "fremd.svg", "notiz.txt"]), files_of(A))
        ok(".db: Canvas mit Vorschaubild ueber dem Hinweis + Bericht", t["Diagramm"]["body"][0]["type"] == "image"
           and "Vorschaubild" in t["Diagramm"]["body"][1]["data"]["text"]
           and any(g["code"] == "trilium_preview" for g in rep["groups"]), (t["Diagramm"]["body"], rep))
        ok("Bericht-Seite angelegt", rep.get("page") and req("GET", f"/api/notes/{rep['page']}")[0] == 200)
        check_new(".db", t, st)

    # --- mit Passwort --------------------------------------------------------------------------------------------------
    s, r, st = run_import(db_new, "sicherung.db", {"kind": "trilium", "title": "Trilium-Test", "password": F.PW}, u=BU)
    t = check_tree(".db mit Passwort", st, FULL)
    if t:
        ok("mit Passwort: Titel und Inhalt entschluesselt", t["Tresor"]["body"][0]["data"]["text"] == "Geheimer Inhalt"
           and t["Geheimnis"]["body"][0]["data"]["text"] == "Nur mit Passwort")
        ok("mit Passwort: Bericht ohne Passwort, ohne 'geschuetzt'", F.PW not in json.dumps(st)
           and not any(g["code"] == "protected" for g in st["report"]["groups"]), st["report"])

    # --- Rate-Limit fuer Fehlversuche ------------------------------------------------------------------------------------
    codes = []
    for _ in range(6):
        s, d = upload("sicherung.db", db_new, u=CU)
        s, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "trilium", "password": "nein-nein"}, u=CU)
        codes.append(s)
    s, d = upload("sicherung.db", db_new, u=CU)
    s, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "trilium", "password": F.PW}, u=CU)
    ok("Rate-Limit: nach 5 Fehlversuchen 429 (auch mit richtigem Passwort)", codes == [400] * 5 + [429] and s == 429, (codes, s))
    req("DELETE", f"/api/import/{d['token']}", u=CU)

    # --- altes Schema (note_contents) ---------------------------------------------------------------------------------
    s, r, st = run_import(F.build_db(os.path.join(TMP, "alt.db"), old=True), "alt.db", {"kind": "trilium", "title": "Trilium-Test"}, u=CU)
    t = check_tree(".db altes Schema", st, OLD)
    if t:
        img = next(b for b in t["Server-Setup"]["body"] if b["type"] == "image")
        ok("altes Schema: Bild aus api/images -> Datei der Bild-Notiz (einmal)", img["data"]["file"]["url"]
           == t["Foto.png"]["body"][0]["data"]["file"]["url"] and files_of(CU) == ["Foto.png"], (img, files_of(CU)))

    # --- ZIP HTML / Markdown -------------------------------------------------------------------------------------------
    cleanup_user(A)
    s, r, st = run_import(F.build_zip("html"), "export.zip", {"kind": "trilium", "title": "Trilium-Test"})
    t = check_tree("ZIP HTML", st, ZIP)
    if t:
        check_content("ZIP HTML", t)
        ok("ZIP HTML: Anhaenge genau einmal", files_of(A) == sorted(["Foto.png", "plan.png", "rezept.pdf", "canvas-export.svg"]), files_of(A))
        ign = [x["path"] for g in st["report"]["groups"] if g["code"] == "ignored" for x in g["items"]]
        ok("ZIP HTML: Klon-Datei und Navigation nicht als Seite, nichts unerwartet ignoriert", ign == [], ign)
    cleanup_user(A)
    s, r, st = run_import(F.build_zip("markdown"), "export.zip", {"kind": "trilium", "title": "Trilium-Test", "tags": False, "props": False})
    t = check_tree("ZIP Markdown (Optionen tags/props aus)", st, MD.replace("Projekte [toggle,paragraph]", "Projekte [paragraph]"))
    if t:
        ok("Optionen aus: keine Tags, kein Eigenschaften-Block", t["Projekte"]["tags"] == [] and "Eigenschaften" not in json.dumps(t["Projekte"]["body"]))
        sb = t["Server-Setup"]["body"]
        ok("ZIP Markdown: Code-Sprachen, Links, Bild", [b["data"]["language"] for b in t["docker-compose.yml"]["body"] + t["Konfig"]["body"]] == ["yaml", "ini"]
           and f'/notes#{t["docker-compose.yml"]["id"]}' in json.dumps(sb) and any(b["type"] == "image" for b in sb), sb)

    # --- unbekannt / unsicher / riesig ---------------------------------------------------------------------------------
    s, d = upload("x.db", F.build_bad_schema(os.path.join(TMP, "bad.db")))
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {"kind": "trilium"})
    ok("unbekanntes Schema: klare Meldung", s == 400 and "Datenbankschema" in (a.get("error") or ""), (s, a))
    for label, sql in (("View", "CREATE VIEW v AS SELECT * FROM notes"),
                       ("Trigger", "CREATE TRIGGER tg AFTER INSERT ON notes BEGIN DELETE FROM notes; END")):
        s, d = upload("x.db", F.build_db(os.path.join(TMP, "unsafe.db"), extra_sql=[sql]))
        s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
        s2, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "trilium"})
        st = wait(r["run_id"]) if s2 == 202 else {}
        ok(f"Datei mit {label}: Analyse und Lauf sauber abgelehnt", s == 400 and "Trigger" in (a.get("error") or "")
           and st.get("state") == "failed" and "Trigger" in st.get("error", ""), (s, a, st))
    # virtuelle Tabelle, deren Schema-Text "VIRTUAL" hinter einem Kommentar versteckt (Pruefer-Fund)
    import _sqlite3
    vp = os.path.join(TMP, "vtab.db")
    F.build_db(vp)
    vc = _sqlite3.connect(vp)
    vc.execute("CREATE VIRTUAL TABLE vt USING fts5(a)")
    vc.execute("PRAGMA writable_schema=ON")
    vc.execute("UPDATE sqlite_master SET sql='CREATE/**/VIRTUAL TABLE vt USING fts5(a)' WHERE name='vt'")
    vc.commit()
    vc.close()
    with open(vp, "rb") as f:
        s, d = upload("x.db", f.read())
    s, a = req("POST", f"/api/import/{d['token']}/analyze", {})
    ok("Datei mit versteckter virtueller Tabelle: abgelehnt", s == 400 and "Trigger" in (a.get("error") or ""), (s, a))
    req("DELETE", f"/api/import/{d['token']}")
    cleanup_user(A)
    s, r, st = run_import(F.build_db(os.path.join(TMP, "riesig.db"), huge=True), "riesig.db", {"kind": "trilium", "title": "Riesig"})
    big = [x["path"] for g in (st.get("report") or {}).get("groups", []) if g["code"] == "too_big" for x in g["items"]]
    ok("riesiger Inhalt: Lauf fertig, Notiz im Bericht (zu gross), Rest da", st.get("state") == "done" and big == ["Riesig"]
       and "Projekte" in tree_lines(st["root"])[1], (st.get("state"), big))
    s, r = req("POST", "/api/import/upload?name=x.db&replace=1", raw=b"SQLite format 3\x00" + b"\x00" * 200)
    s, a = req("POST", f"/api/import/{r['token']}/analyze", {})
    ok("kaputte SQLite-Datei: sauber abgelehnt", s == 400 and a.get("error"), (s, a))
    req("DELETE", f"/api/import/{r['token']}")


def cleanup_user(u):
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid=?", (u,))
        c.execute("DELETE FROM file WHERE uid=?", (u,))


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU, CU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'tr-%'")
        c.execute("DELETE FROM import_run WHERE uid LIKE 'tr-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'tr-%'")


if __name__ == "__main__":
    main()
    print(f"\nIMPORT-TRILIUM {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
