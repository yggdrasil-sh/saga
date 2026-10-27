"""Markdown-/Obsidian-Import (2.5, Paket 2 E4, saga/import_md.py, saga/md2blocks.py) mit selbst gebautem Tresor.

Prueft: ZIP eines Tresors (gemeinsamer Wurzelordner = Sammelseite), .obsidian/ und versteckte Dateien ignoriert,
Ordner = Seite (Ordnernotiz Ordner/Ordner.md bzw. index.md liefert den Inhalt), Wikilinks (Alias, Abschnitt, Pfad,
mehrdeutig, kaputt -> Bericht), Markdown-Links mit %20, Bilder ![[bild.png|200]] und ![](pfad) -> image (Datei im
Konto), PDF -> attaches, HTML mit .png-Endung -> Anhang statt Bild, externe Bilder als Link, Frontmatter (title, tags
-> Saga-Tags, aliases ignoriert, Rest -> Toggle "Eigenschaften"), Optionen tags/props aus, Callouts aller Arten inkl.
faltbar, ==mark==, %%Kommentar%%, Tabelle, Code, Aufgaben, javascript:-Links nur Text, <script> als Text, Verweise
zeigen auf neue IDs (Rueckverweise), Einzeldateien ohne ZIP (mehrere Teile eines Tokens), kein Serverfehler.
Wegwerf-Konten im-*."""
import base64
import http.client
import io
import json
import os
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
A, BU = "im-a", "im-b"
res = []
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\r"
       b"IDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")


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
    return req("POST", "/api/import/upload?name=" + urllib.parse.quote(name) + ("&token=" + tok if tok else ""), raw=data, u=u)


def wait(rid):
    t0 = time.time()
    while time.time() - t0 < 120:
        s, d = req("GET", f"/api/import/runs/{rid}")
        if s == 200 and d["state"] in ("done", "failed", "cancelled"):
            return d
        time.sleep(0.25)
    return d


VAULT = {
    "Tresor/.obsidian/app.json": b"{}",
    "Tresor/.trash/alt.md": b"# alt",
    "Tresor/.versteckt.md": b"geheim",
    "Tresor/Start.md": ("---\ntitle: Startseite\ntags: [privat, projekt/alpha]\naliases: [Home]\nstatus: offen\nprio: 2\n---\n"
                        "# Startseite\n\nSiehe [[Alpha|das Projekt]], [[Alpha#Ziele]], [[Projekte/Beta]], [[Mehrdeutig]] und [[Gibt es nicht]].\n"
                        "Markdown-Link [Beta](Projekte/Beta.md) und [Notiz mit Leerzeichen](Notiz%20mit%20Leerzeichen.md).\n"
                        "Böse: [klick](javascript:alert(1)) <script>alert(2)</script> ==markiert== %%versteckt%%\n"
                        "Formel $E=mc^2$ kostet $5 und $10. <span class=\"hk-c-red\">rot</span> <span class=\"hk-mention\" data-u=\"x\">@x</span>\n\n"
                        "![[bild.png|200]]\n\n![Diagramm](Anhänge/diagramm.png)\n\n![extern](https://example.org/x.png)\n\n"
                        "![[handbuch.pdf]]\n\n![[falsch.png]]\n\n![[Alpha]]\n\n"
                        "> [!note] Hinweis\n> Inhalt\n\n> [!danger] Gefahr\n> Vorsicht\n\n> [!tip]- Faltbar\n> Innen\n\n"
                        "> [!quote] Jemand\n> Ein Zitat\n\n"
                        "- [ ] offen\n- [x] erledigt\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```js\nlet x = 1;\n```\n").encode(),
    "Tresor/Notiz mit Leerzeichen.md": b"Einfach.\n",
    "Tresor/Projekte/Projekte.md": "Ordnernotiz für Projekte, zurück zu [[Start]].\n".encode(),
    "Tresor/Projekte/Alpha.md": "## Ziele\n\nAlpha-Text.\n".encode(),
    "Tresor/Projekte/Beta.md": b"Beta-Text.\n",
    "Tresor/A/Mehrdeutig.md": b"A\n",
    "Tresor/B/Mehrdeutig.md": b"B\n",
    "Tresor/Anhänge/diagramm.png": PNG,
    "Tresor/bild.png": PNG,
    "Tresor/handbuch.pdf": b"%PDF-1.4\n%Test\n",
    "Tresor/falsch.png": b"<html><script>alert(1)</script></html>",
    "Tresor/unbenutzt.png": PNG,
    "Tresor/Zeichnung.canvas": b"{}",
}


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def tree(root):
    with connect() as c:
        rows = c.execute("WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id "
                         "WHERE n.deleted_at IS NULL) SELECT n.id, n.title, n.parent_id, n.body, n.tags, n.icon FROM note n JOIN t ON n.id=t.id",
                         (root,)).fetchall()
    return {r[1]: {"id": r[0], "parent": r[2], "body": json.loads(r[3] or '{"blocks": []}'), "tags": json.loads(r[4] or "[]"), "icon": r[5]}
            for r in rows}


def all_blocks(blocks):
    for b in blocks:
        yield b
        d = b.get("data") or {}
        for x in d.get("blocks") or []:
            yield from all_blocks([x])


def _strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)


def main():
    from saga.i18n import set_lang
    set_lang("de")
    cleanup()
    for u in (A, BU):
        auth.create_user(u, "Md-Pass-1234", u.upper())
    try:
        run()
    finally:
        cleanup()


def run():
    s, d = upload("Tresor.zip", zipped(VAULT))
    ok("Upload: Art md", s == 200 and d["kind"] == "md", d)
    tok = d["token"]
    s, a = req("POST", f"/api/import/{tok}/analyze", {})
    ok("Analyse: Seiten/Ordner/Dateien, Sammelname", s == 200 and a["md"]["folders"] == 3 and a["md"]["files"] == 5
       and a["md"]["title"] == "Tresor" and a["md"]["unresolved"] >= 1, a)
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "md"})
    st = wait(r["run_id"]) if s == 202 else {}
    ok("Lauf fertig", st.get("state") == "done", st)
    t = tree(st["root"])
    ok("Sammelseite = Ordnername", "Tresor" in t and t["Tresor"]["id"] == st["root"], list(t))
    ok("Ordner mit Notizen sind Seiten (nur Bilder: keine), .obsidian/.trash/versteckt ignoriert", {"Projekte", "A", "B"} <= set(t)
       and "Anhänge" not in t and "alt" not in t and ".versteckt" not in t and "app" not in t, sorted(t))
    ok("Ordnernotiz liefert Inhalt des Ordners", "Projekte" in t and "Ordnernotiz" in json.dumps(t["Projekte"]["body"])
       and "Projekte" not in [k for k in t if t[k]["parent"] == t["Projekte"]["id"]], t.get("Projekte"))
    ok("Unterseiten im Ordner", t["Alpha"]["parent"] == t["Projekte"]["id"] and t["Beta"]["parent"] == t["Projekte"]["id"])
    sp = t.get("Startseite")
    ok("Frontmatter title -> Titel, Tags (/ -> -)", sp and sp["tags"] == ["privat", "projekt-alpha"], sp and sp["tags"])
    bl = sp["body"]["blocks"]
    j = " ".join(str(v) for b in all_blocks(bl) for v in _strings(b))
    ok("doppelte Titel-Ueberschrift entfernt", not (bl[1]["type"] == "header" and "Startseite" in bl[1]["data"]["text"]), bl[:2])
    ok("Eigenschaften-Toggle (status, prio; ohne aliases)", bl[0]["type"] == "toggle" and "status" in json.dumps(bl[0]) and "prio" in json.dumps(bl[0])
       and "aliases" not in json.dumps(bl[0]) and "Home" not in json.dumps(bl[0]), bl[0])
    ok("Wikilink mit Alias -> neue ID", f'href="/notes#{t["Alpha"]["id"]}">das Projekt' in j, j[:600])
    ok("Wikilink mit Pfad und Markdown-Link (%20)", f'/notes#{t["Beta"]["id"]}' in j and f'/notes#{t["Notiz mit Leerzeichen"]["id"]}' in j)
    with connect() as c:
        mids = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND title='Mehrdeutig' AND deleted_at IS NULL", (A,)).fetchall()]
    ok("mehrdeutig: eine der Seiten, kaputt: nur Text", len(mids) == 2 and sum(f"/notes#{i}\"" in j for i in mids) == 1
       and "Gibt es nicht" in j, (mids, j[:400]))
    ok("javascript:-Link nur Text, <script> als Text", "javascript" not in j and "&lt;script&gt;" in j, j)
    ok("==markiert==, %%Kommentar%% entfernt", "cdx-marker" in j and "versteckt" not in j)
    ok("$Mathe$ -> code.hk-math (Inline-Vertrag), $5 und $10 bleibt Text", '<code class="hk-math">E=mc^2</code>' in j and "$5 und $10" in j, j[:800])
    ok("HTML mit hk-Klassen im Markdown nur als Text (keine Farbe/Erwaehnung durchgeschleust)",
       '<span' not in j and "&lt;span class=" in j, j[:800])
    bids = [b.get("id") for b in all_blocks(bl)]
    ok(f"Block-IDs eindeutig und gueltig ({len(bids)})", all(isinstance(x, str) and x for x in bids) and len(set(bids)) == len(bids), bids)
    types = [b["type"] for b in all_blocks(bl)]
    imgs = [b for b in bl if b["type"] == "image"]
    ok("Bilder -> image mit eigener Datei", len(imgs) == 2 and all(i["data"]["file"]["url"].startswith("/files/") for i in imgs), imgs)
    att = [b for b in bl if b["type"] == "attaches"]
    ok("PDF und HTML-als-PNG -> Anhang", len(att) == 2 and {x["data"]["file"]["name"] for x in att} == {"handbuch.pdf", "falsch.png"}, att)
    with connect() as c:
        fl = c.execute("SELECT name, is_image FROM file WHERE uid=? ORDER BY name", (A,)).fetchall()
    ok("Dateien im Konto: genutzte, falsch.png kein Bild, unbenutzt.png nicht", [tuple(x) for x in fl] ==
       [("bild.png", 1), ("diagramm.png", 1), ("falsch.png", 0), ("handbuch.pdf", 0)], fl)
    ok("externes Bild als Link", "https://example.org/x.png" in j)
    ok("Notiz-Einbettung als ↪-Link", "↪" in j)
    ok("Callouts: note ℹ️, danger ❗, faltbar -> Toggle, quote -> Zitat",
       any(b["type"] == "callout" and b["data"]["emoji"] == "ℹ️" for b in bl) and any(b["type"] == "callout" and b["data"]["emoji"] == "❗" for b in bl)
       and any(b["type"] == "toggle" and "Faltbar" in b["data"]["title"] for b in bl) and any(b["type"] == "quote" and "Zitat" in b["data"]["text"] for b in bl))
    ok("Aufgaben, Tabelle, Code", "list" in types and "table" in types and "code" in types
       and any(b["type"] == "list" and b["data"]["style"] == "checklist" and b["data"]["items"][1]["meta"]["checked"] for b in bl))
    rep = st["report"]
    codes = {g["code"]: [x["path"] for x in g["items"]] for g in rep["groups"]}
    ok("Bericht: kaputter Link, externes Bild, Canvas, aliases", any("Gibt es nicht" in x for x in codes.get("link_missing", []))
       and codes.get("ext_image") and any("canvas" in x for x in codes.get("unsupported", [])), codes)
    ok("Bericht-Seite neben der Sammelseite", rep.get("page") and req("GET", f"/api/notes/{rep['page']}")[0] == 200)
    time.sleep(2.5)
    s, bk = req("GET", f"/api/notes/{t['Alpha']['id']}/backlinks")
    ok("Rueckverweise zeigen auf neue Seiten", s == 200 and sp["id"] in [x.get("id") for x in (bk if isinstance(bk, list) else bk.get("items", []))], bk)

    # --- Einzeldateien ohne ZIP, Optionen aus ------------------------------------------------------------------------
    s, d = upload("Notizen/Eins.md", b"---\ntags: x\nfarbe: rot\n---\nVerweis [[Zwei]]\n")
    tok = d["token"]
    s, d2 = upload("Notizen/Zwei.md", b"# Zwei\n\nText\n", tok=tok)
    s3, _ = upload("Notizen/Drei.md", b"x", tok=tok, u=BU)
    ok("weiterer Teil: fremdes Konto 404", s3 == 404)
    s, r = req("POST", f"/api/import/{tok}/run", {"kind": "md", "tags": False, "props": False, "title": "Lose Notizen"})
    st = wait(r["run_id"]) if s == 202 else {}
    t = tree(st.get("root") or 0)
    ok("Einzeldateien: Sammelseite mit eigenem Namen, 2 Seiten", st.get("state") == "done" and "Lose Notizen" in t and "Eins" in t and "Zwei" in t, (st, list(t)))
    ok("Optionen aus: keine Tags, kein Eigenschaften-Block", t["Eins"]["tags"] == [] and "Eigenschaften" not in json.dumps(t["Eins"]["body"]))
    ok("Verweis zwischen Einzeldateien", f'/notes#{t["Zwei"]["id"]}' in json.dumps(t["Eins"]["body"]))

    # --- leer / nur Bilder ---------------------------------------------------------------------------------------------
    s, d = upload("nur.zip", zipped({"x/bild.png": PNG}))
    s, r = req("POST", f"/api/import/{d['token']}/run", {"kind": "md"})
    st = wait(r["run_id"]) if s == 202 else {}
    ok("ohne Notizen: Lauf scheitert sauber (nichts zu importieren)", st.get("state") == "failed" and "Nichts" in st.get("error", ""), st)


def cleanup():
    from saga.userdata import delete_user_data
    for u in (A, BU):
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'im-%'")
        c.execute("DELETE FROM import_run WHERE uid LIKE 'im-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'im-%'")


if __name__ == "__main__":
    main()
    print(f"\nIMPORT-MD {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
