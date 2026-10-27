"""Vorlagengalerie (2.5): Pruefskript, Liste/Vorschau (5 Sprachen), Anlegen mit/ohne Beispiele, refs ersetzt
(Relationen zweiseitig, Widget cfg.db, Datenbank-Block, Schaltflaechen signiert und benutzbar, default_tpl, Seitenverweise),
Alles-oder-nichts, Rechte (fremd, nur Lesen, Bearbeiten, Datenbank, foederiert), Ratenbegrenzung, Admin-Schalter.

Server mit SAGA_GALLERY_DIR=tests/gallery_fixtures und SAGA_GALLERY_TEST_FAIL=crash (Abbruch-Vorlage), GALLERY_RATE
wie beim Server (Standard 10). Gegen gallery/ (echte Vorlagen): jede Vorlage wird zusaetzlich angelegt (de mit, en ohne
Beispiele, Rest nur Liste/Vorschau)."""
import base64
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
RATE = int(os.environ.get("GALLERY_RATE", "10"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
LANGS = ("de", "en", "es", "ru", "th")


def cookie(u):
    return "saga=" + TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": u}).encode())).decode()


def call(m, p, b=None, u="hk-test-a", hdr=True):
    h = {"Host": HOST, "Content-Type": "application/json"}
    if u:
        h["Cookie"] = cookie(u)
    if hdr:
        h["X-Saga"] = "1"
    rq = urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode()[:200]


res = []


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n)


def sql(q, a=()):
    cn = sqlite3.connect(DB)
    try:
        r = cn.execute(q, a).fetchall()
        cn.commit()
        return r
    finally:
        cn.close()


def note(i, u="hk-test-a"):
    return call("GET", f"/api/notes/{i}", u=u)[1]


def blocks(n):
    return json.loads(n["body"])["blocks"]


def walk(bl):
    for b in bl:
        yield b
        d = b.get("data") or {}
        for c in d.get("cols") or []:
            yield from walk(c.get("blocks") or [])
        if b.get("type") == "toggle":
            yield from walk(d.get("blocks") or [])


def count(u):
    return sql("SELECT COUNT(*) FROM note WHERE uid=?", (u,))[0][0]


def use(gid, lang="de", u="hk-test-a", **b):
    return call("POST", f"/api/gallery/{gid}/use?lang={lang}", b, u=u)


try:
    # --- Pruefskript -----------------------------------------------------------------------------------------
    fx = os.path.join(ROOT, "tests", "gallery_fixtures")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gallery_check.py"), fx], capture_output=True, text=True)
    ok(f"Pruefskript: Test-Bundles gueltig (rc {r.returncode})", r.returncode == 0 and "GALERIE OK" in r.stdout)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gallery_check.py"), os.path.join(fx, "kaputt"), "--no-i18n"],
                       capture_output=True, text=True)
    want = {"ref-unbekannt.json": "unbekannter ref 'nix'", "typ-unbekannt.json": "'person'", "zu-gross.json": "mehr als 30",
            "id-falsch.json": "Dateinamen", "beispiel-verweis.json": "Beispiel", "json-kaputt.json": "JSON",
            "wert-falsch.json": "unbekannte Option 'b'"}
    miss = [f for f, s in want.items() if f"FEHLER {f}" not in r.stdout or s not in r.stdout]
    ok(f"Pruefskript: kaputte Bundles erkannt, rc 1 (fehlt: {miss})", r.returncode == 1 and not miss)
    ok("Pruefskript: Typen und Blockverbote gemeldet", "'timeline'" in r.stdout and "'image' nicht erlaubt" in r.stdout
       and "Datum JJJJ-MM-TT" in r.stdout and "auch_nix" in r.stdout)
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory() as td:
        shutil.copy(os.path.join(fx, "klein.json"), td)
        os.makedirs(os.path.join(td, "i18n"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gallery_check.py"), td], capture_output=True, text=True)
        ok("Pruefskript: fehlende Uebersetzungen = Fehler", r.returncode == 1 and "Uebersetzungen fehlen" in r.stdout)
        json.dump({"Kleine Seite": "Small {x} page"}, open(os.path.join(td, "i18n", "en.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gallery_check.py"), td, "--no-i18n"], capture_output=True, text=True)
        ok("Pruefskript: abweichende Platzhalter = Fehler (auch mit --no-i18n)", r.returncode == 1 and "Platzhalter" in r.stdout)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "gallery_check.py"), td, "--todo", "es"], capture_output=True, text=True)
        ok("Pruefskript: --todo gibt JSON-Geruest", r.returncode == 0 and json.loads(r.stdout).get("Hallo Welt.") == "")
    sys.path.insert(0, ROOT)
    from saga import gallery_format as gf, notes, page_widgets
    ok("Typ-Listen wie saga.notes/page_widgets", gf.PROP_TYPES == notes.PROP_TYPES and gf.VIEW_TYPES == notes.VIEW_TYPES
       and gf.WIDGET_KINDS == page_widgets.KINDS and gf.OPT_COLORS == notes.OPT_COLORS and gf.PID.pattern == notes._PID.pattern)

    # --- Anmeldung, Liste, Vorschau --------------------------------------------------------------------------
    ok("Ohne Anmeldung 401", call("GET", "/api/gallery", u=None)[0] == 401 and call("GET", "/api/gallery", hdr=False)[0] == 401
       and call("POST", "/api/gallery/klein/use", {}, u=None)[0] == 401)
    s, lst = call("GET", "/api/gallery?lang=de")
    ids = [x["id"] for x in lst.get("items", [])] if s == 200 else []
    fixtures = "alles" in ids
    ok(f"Liste: {ids}", s == 200 and ids and all(set(x) >= {"id", "title", "desc", "icon", "category", "order", "needs", "contents"} for x in lst["items"]))
    ok("Liste nach order sortiert", [x["order"] for x in lst["items"]] == sorted(x["order"] for x in lst["items"]))
    ok("Liste ohne Bloecke", "body" not in json.dumps(lst) and "$ref:" not in json.dumps(lst))
    for lang in LANGS:
        s, l2 = call("GET", f"/api/gallery?lang={lang}")
        s2, pv = call("GET", f"/api/gallery/{ids[0]}?lang={lang}")
        ok(f"Liste und Vorschau {lang}", s == 200 and s2 == 200 and l2.get("lang") == lang and pv.get("preview", {}).get("blocks") is not None
           and "$ref:" not in json.dumps(pv))
    ok("Unbekannte Vorlage 404", call("GET", "/api/gallery/gibts-nicht")[0] == 404 and use("gibts-nicht")[0] == 404)

    if fixtures:
        al = next(x for x in lst["items"] if x["id"] == "alles")
        ok(f"Inhalt der Liste: {[c['title'] for c in al['contents']]}", [c["kind"] for c in al["contents"]] == ["page", "database", "database", "page", "page"]
           and al["contents"][4]["example"] is True and al["category"] == "work")
        ok("needs", next(x for x in lst["items"] if x["id"] == "klein")["needs"] == ["calendar"])
        en = {x["id"]: x for x in call("GET", "/api/gallery?lang=en")[1]["items"]}
        ok(f"Liste uebersetzt (en: {en['alles']['title']!r})", en["alles"]["title"] == "Everything inside" and en["alles"]["contents"][2]["title"] == "Tasks")
        s, pv = call("GET", "/api/gallery/alles?lang=es")
        pb = json.dumps(pv["preview"]["blocks"], ensure_ascii=False)
        ok("Vorschau es: Bloecke uebersetzt", "Resumen" in pb and "Nueva tarea" in pb and pv["preview"]["title"] == "Todo incluido")
        dbs = {d["title"]: d for d in pv["databases"]}
        ok(f"Vorschau: Datenbanken {list(dbs)}", set(dbs) == {"Proyectos", "Tareas"} and "Prioridad" in [p["name"] for p in dbs["Tareas"]["props"]]
           and [v["name"] for v in dbs["Tareas"]["views"]] == ["Tablero", "Abierto", "Calendario"] and dbs["Tareas"]["templates"] == ["Nueva tarea"]
           and dbs["Tareas"]["examples"] == 2 and dbs["Tareas"]["rows"] == 1)
        ok("Vorschau: refs als Titel", '"preview_ref": "tasks"' in pb)

        # --- Anlegen mit Beispielen (de) -------------------------------------------------------------------
        before = count("hk-test-a")
        s, d = use("alles", examples=True)
        ok(f"Anlegen: {s} {d if s != 200 else ''}", s == 200 and isinstance(d.get("root"), int) and len(d.get("created", [])) == 11)
        ok("11 Seiten mehr", count("hk-test-a") == before + 11)
        root = note(d["root"])
        tree = {x["title"]: x for x in call("GET", "/api/notes")[1] if x["id"] in d["created"]}
        ok(f"Startseite: {root['title']!r}, breit, oberste Ebene", root["title"] == "Alles drin" and root["wide"] and root["parent_id"] is None and root["icon"] == "🧪")
        ok(f"Baum: {sorted(tree)}", {"Projekte", "Aufgaben", "Notizen", "Beispielseite", "Neue Aufgabe", "Website", "Umzug",
                                     "Entwurf schreiben", "Kisten packen", "Vorlage ausprobieren"} <= set(tree))
        P, T, N = tree["Projekte"]["id"], tree["Aufgaben"]["id"], tree["Notizen"]["id"]
        tpl, p1, p2, t1 = tree["Neue Aufgabe"]["id"], tree["Website"]["id"], tree["Umzug"]["id"], tree["Entwurf schreiben"]["id"]
        ok("Unterseiten unter der Startseite", tree["Projekte"]["parent_id"] == root["id"] and tree["Notizen"]["parent_id"] == root["id"])
        ok("Notizen: Schrift serif", note(N)["font"] == "serif")
        ts, ps = note(T)["schema"], note(P)["schema"]
        rel = next(p for p in ts["props"] if p["id"] == "projekt")
        mirror = next((p for p in ps["props"] if p["type"] == "relation"), {})
        ok(f"Relation: Ziel = Projekte, zweiseitig ({rel.get('pair')})", rel["target"] == P and rel.get("pair") == mirror.get("id")
           and mirror.get("target") == T and mirror.get("pair") == "projekt" and mirror.get("name") == "Aufgaben")
        ok(f"default_tpl = Vorlage ({ts.get('default_tpl')})", ts.get("default_tpl") == tpl and note(tpl)["template"] is True)
        ok("Schema: Ansichten, Filter, Summe, title_name, srev", [v["id"] for v in ts["views"]] == ["board", "offen", "kalender"]
           and ts["views"][1]["filters"][0]["op"] == "unchecked" and ts["views"][1]["calcs"] == {"punkte": "sum"}
           and ts["title_name"] == "Aufgabe" and isinstance(ts.get("srev"), int))
        ok("Zeile: Werte und Relation", note(t1)["props"] == {"status": "doing", "prio": "hoch", "faellig": "2026-11-03", "punkte": 3, "projekt": [p1]})
        ok("Gegenseite nachgezogen", note(p1)["props"].get(mirror.get("id")) == [t1] and note(p1)["props"].get("status") == "aktiv")
        ok("Vorlage: Werte und Inhalt", note(tpl)["props"] == {"status": "todo", "prio": "normal"} and "Ziel" in note(tpl)["body"])
        rows = call("GET", f"/api/notes/{T}/rows")[1]
        ok("Datenbank-Zeilen ohne Vorlage", len(rows["rows"]) == 3 and [x["id"] for x in rows["templates"]] == [tpl])
        kb = note(tree["Kisten packen"]["id"])
        ok("Seitenverweis im Text ersetzt", f'href=\\"/notes#{p2}\\"' in kb["body"])
        bl = list(walk(blocks(root)))
        by = {b["type"]: b for b in bl}
        wm = next(b for b in bl if b["type"] == "widget" and b["data"]["kind"] == "metric")
        wp = next(b for b in bl if b["type"] == "widget" and b["data"]["kind"] == "progress")
        ok("Widget cfg.db ersetzt (Zahl)", wm["data"]["cfg"]["db"] == T and wp["data"]["cfg"]["db"] == T and wm["id"] == "wmet")
        ok("Datenbank-Block ersetzt", by["database"]["data"]["db"] == T and by["database"]["data"]["view"] == "board")
        bt = by["buttons"]["data"]["buttons"]
        ok(f"Schaltflaechen: Ziel/Vorlage ersetzt, signiert ({bool(bt[0].get('sig'))})", bt[0]["target"] == T and bt[0]["tpl"] == tpl
           and len(bt[0].get("sig") or "") == 32 and bt[1]["target"] == N and "sig" not in bt[1])
        ok("Verweis im Absatz ersetzt", f'/notes#{P}"' in next(b for b in bl if b["type"] == "paragraph")["data"]["text"])
        ok("Spalten und Farben bleiben", by["columns"]["data"]["cols"][0]["w"] == 0.6 and by["header"]["tunes"] == {"hkstyle": {"c": "blue"}}
           and by["callout"]["tunes"] == {"hkstyle": {"c": "yellow", "bg": True}})
        wv = call("GET", f"/api/notes/{root['id']}/widgets")[1]
        met = ((wv or {}).get("widgets") or {}).get("wmet") or {}
        ok(f"Widget rechnet: offene Aufgaben = {met.get('value')}", met.get("value") == 2 and not met.get("error"))
        # Schaltflaeche auf geteilter Seite: B (Bearbeiten an der Startseite) legt per via eine Aufgabe aus der Vorlage an
        s, _ = call("PUT", f"/api/notes/{root['id']}/shares", {"type": "user", "target": "hk-test-b", "role": "edit"})
        s2, dd = call("POST", f"/api/notes/{tpl}/duplicate", {"parent_id": T, "via": root["id"], "title": "Von B"}, u="hk-test-b")
        ok(f"Schaltflaeche signiert und benutzbar (Freigabe {s}, via {s2})", s == 200 and s2 == 200 and note(dd["id"])["props"].get("status") == "todo")
        s3, _ = call("POST", f"/api/notes/{tpl}/duplicate", {"parent_id": T, "via": N}, u="hk-test-b")
        ok(f"ohne passende Schaltflaeche kein via ({s3})", s3 == 404)

        # --- ohne Beispiele, englisch ------------------------------------------------------------------------
        s, d2 = use("alles", "en", examples=False)
        tr = {x["title"]: x for x in call("GET", "/api/notes")[1] if x["id"] in d2.get("created", [])}
        ok(f"ohne Beispiele: {sorted(tr)}", s == 200 and len(d2["created"]) == 6 and set(tr) == {"Everything inside", "Projects", "Tasks", "Notes",
                                                                                               "New task", "Try the template"})
        ts2 = note(tr["Tasks"]["id"])["schema"]
        ok("en: Eigenschaften/Optionen/Ansichten uebersetzt", [p["name"] for p in ts2["props"]][:3] == ["Status", "Priority", "Due"]
           and [o["name"] for o in ts2["props"][0]["options"]] == ["Open", "In progress", "Done"] and ts2["views"][0]["name"] == "Board"
           and ts2["title_name"] == "Task")
        rb = list(walk(blocks(note(d2["root"]))))
        b2 = next(b for b in rb if b["type"] == "buttons")["data"]["buttons"]
        ok("en: Schaltflaeche/Widget/Zitat uebersetzt", b2[0]["label"] == "New task" and b2[0]["title"] == "Task {datum}"
           and any(b["type"] == "widget" and b["data"].get("title") == "Open tasks" for b in rb)
           and any(b["type"] == "widget" and b["data"]["cfg"].get("items") == ["Start small.", "Keep going."] for b in rb))
        ok("ohne Beispiele: Projekte leer, Relation ohne Werte", call("GET", f"/api/notes/{tr['Projects']['id']}/rows")[1]["rows"] == []
           and note(tr["Try the template"]["id"])["props"] == {"status": "done", "erledigt": True, "punkte": 1})
        for lang, word in (("es", "Tareas"), ("ru", "Задачи"), ("th", "งาน")):
            s, d3 = use("alles", lang)
            names = {x["title"] for x in call("GET", "/api/notes")[1] if x["id"] in d3.get("created", [])}
            ok(f"Anlegen {lang}: {word in names}", s == 200 and word in names)

        # --- unter einer Elternseite, Rechte -----------------------------------------------------------------
        par = call("POST", "/api/notes", {"title": "Elternseite"})[1]["id"]
        s, d4 = use("klein", parent_id=par)
        ok("unter eigener Elternseite", s == 200 and note(d4["root"])["parent_id"] == par)
        fremd = call("POST", "/api/notes", {"title": "B privat"}, u="hk-test-b")[1]["id"]
        ok("fremde Seite: 404", use("klein", parent_id=fremd)[0] == 404)
        nurlesen = call("POST", "/api/notes", {"title": "B lesen"}, u="hk-test-b")[1]["id"]
        call("PUT", f"/api/notes/{nurlesen}/shares", {"type": "user", "target": "hk-test-a", "role": "view"}, u="hk-test-b")
        ok("nur Lesen: 403", use("klein", parent_id=nurlesen)[0] == 403)
        bear = call("POST", "/api/notes", {"title": "B bearbeiten"}, u="hk-test-b")[1]["id"]
        call("PUT", f"/api/notes/{bear}/shares", {"type": "user", "target": "hk-test-a", "role": "edit"}, u="hk-test-b")
        nb = count("hk-test-b")
        s, d5 = use("alles", parent_id=bear)
        ok(f"Bearbeiten: angelegt im Konto von B ({s})", s == 200 and count("hk-test-b") == nb + 11 and note(d5["root"], "hk-test-b")["parent_id"] == bear)
        b5 = next(b for b in walk(blocks(note(d5["root"], "hk-test-b"))) if b["type"] == "buttons")["data"]["buttons"][0]
        ok("unter fremder Seite: Schaltflaeche fuer den Eigentuemer signiert", len(b5.get("sig") or "") == 32 and b5["tpl"])
        ok("Elternseite Datenbank: 400", use("klein", parent_id=T)[0] == 400)
        s, e = use("klein", parent_id=-5)
        ok(f"foederierte Elternseite: 400 ({e})", s == 400 and "Saga" in str(e))
        ok("ungueltige parent_id: 400", use("klein", parent_id="abc")[0] == 400 and use("klein", parent_id=True)[0] == 400)
        ok("Papierkorb-Elternseite: 404", call("DELETE", f"/api/notes/{par}")[0] == 200 and use("klein", parent_id=par)[0] == 404)

        # --- Alles oder nichts ---------------------------------------------------------------------------------
        before = count("hk-test-a")
        s, e = use("abbruch")
        ok(f"Abbruch: 400, nichts angelegt ({s}, {count('hk-test-a') - before} neu)", s == 400 and count("hk-test-a") == before
           and not sql("SELECT 1 FROM note WHERE uid='hk-test-a' AND title='Abbruch'"))

    # --- Ratenbegrenzung (Konto B, ungueltige Anfragen zaehlen mit) -------------------------------------------------
    codes = [use(ids[0], u="hk-test-b", parent_id="x")[0] for _ in range(RATE + 1)]
    ok(f"Ratenbegrenzung nach {RATE} je Minute: {codes[-2:]}", codes[-1] == 429 and 429 not in codes[:RATE - 2])

    # --- echte Vorlagen (gallery/): jede einmal anlegen --------------------------------------------------------
    if not fixtures:
        for x in lst["items"]:
            for lang, ex in (("de", True), ("en", False)):
                s, d6 = use(x["id"], lang, examples=ex)
                ok(f"{x['id']} {lang} {'mit' if ex else 'ohne'} Beispiele: {s}", s == 200 and d6["root"] in d6["created"])
                n6 = note(d6["root"])
                ok(f"{x['id']}: keine $ref uebrig", all("$ref:" not in json.dumps(note(i)) for i in d6["created"][:40]) and n6["title"])

    # --- Admin-Schalter ---------------------------------------------------------------------------------------------
    sql("INSERT INTO setting (k, v) VALUES ('gallery_enabled', 'false') ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v")
    time.sleep(5.5)
    off = [call("GET", "/api/gallery"), call("GET", f"/api/gallery/{ids[0]}"), use(ids[0], u="hk-test-a")]
    ok(f"Admin aus: 404 {{\"error\": \"not found\"}} (Liste, Vorschau, Anlegen): {off}", all(x == (404, {"error": "not found"}) for x in off))
    sql("DELETE FROM setting WHERE k='gallery_enabled'")
    time.sleep(5.5)
    ok("Admin wieder an", call("GET", "/api/gallery")[0] == 200)
finally:
    sql("DELETE FROM setting WHERE k='gallery_enabled'")
    for u in ("hk-test-a", "hk-test-b"):
        sql("DELETE FROM note_share WHERE owner_uid=?", (u,))
        sql("DELETE FROM note WHERE uid=?", (u,))
print(f"\nGALERIE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
