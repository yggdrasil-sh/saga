"""Erfundene Trilium-Testdaten fuer tests/import_trilium.py: kleine Datenbanken (Schema neu mit blobs/attachments und alt
mit note_contents, mit geschuetzten Notizen - verschluesselt nach dem Trilium-Verfahren mit einem Testpasswort) und
ZIP-Exporte (Format HTML und Markdown) mit derselben Struktur wie der offizielle Export (!!!meta.json, dataFileName,
dirFileName, Klone, Anhaenge, noImport-Dateien). Keine echten Daten.

Verschluesselung hier eigenstaendig nachgebaut (Trilium: services/encryption/data_encryption.ts, password_encryption.ts,
scrypt.ts), nicht aus saga importiert."""
import base64
import hashlib
import io
import json
import os
import sqlite3
import zipfile

PW = "Probe-Kennwort-7319"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\r"
       b"IDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'
D = "2026-01-02 03:04:05.000+0100"
U = "2026-01-02 02:04:05.000Z"


def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=32)


def _pad16(b):
    return (bytes(b[:16]) + b"\0" * 16)[:16]


def encrypt(key, plain):
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    plain = plain.encode() if isinstance(plain, str) else plain
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    data = p.update(hashlib.sha1(plain).digest()[:4] + plain) + p.finalize()
    e = Cipher(algorithms.AES(_pad16(key)), modes.CBC(_pad16(iv))).encryptor()
    return base64.b64encode(iv + e.update(data) + e.finalize()).decode()


def server_html(img, inc_link, brot_link):
    return f"""<h2>Server-Setup</h2>
<p>Ein <strong>fetter</strong> und <i>kursiver</i> Text mit <code>inline</code> und einem <a href="https://example.org">Link</a>.</p>
<h3>Schritte</h3>
<ol><li>Erst<ul><li>Unterpunkt</li></ul></li><li>Dann</li></ol>
<ul class="todo-list"><li><label class="todo-list__label"><input type="checkbox" checked="checked" disabled="disabled"><span class="todo-list__label__description">Erledigt</span></label></li><li><label class="todo-list__label"><input type="checkbox" disabled="disabled"><span class="todo-list__label__description">Offen</span></label></li></ul>
<figure class="table"><table><thead><tr><th>Dienst</th><th>Port</th></tr></thead><tbody><tr><td>Web</td><td>8080</td></tr></tbody></table></figure>
<blockquote><p>Zitat</p><p>zweite Zeile</p></blockquote>
<aside class="admonition warning"><p>Achtung!</p></aside>
<pre><code class="language-text-x-yaml">a: 1
b: 2</code></pre>
<hr>
<p>Vorher <img src="{img}"> nachher</p>
<p>&nbsp;</p>
<section class="include-note" data-note-id="dcomp" data-box-size="small">&nbsp;</section>
<p>Siehe <a class="reference-link" href="{inc_link}">docker-compose.yml</a> und <a href="{brot_link}">Brot</a>.</p>
<p><span class="math-tex">\\[E=mc^2\\]</span></p>
<details><summary>Mehr</summary><p>Innen</p></details>"""


# nid, parent, pos, prefix, title, type, mime, content, protected
def notes(old=False):
    img = "api/images/foto/Foto.png" if old else "api/attachments/att1/image/plan.png"
    return [
        ("root", "none", 0, None, "root", "text", "text/html", "", False),
        ("diag", "proj", 30, None, "Diagramm", "canvas", "application/json", '{"elements":[]}', False),   # Reihenfolge != Position
        ("proj", "root", 10, None, "Projekte", "text", "text/html", "<p>Übersicht der Projekte.</p>", False),
        ("srv", "proj", 10, None, "Server-Setup", "text", "text/html", server_html(img, "#root/proj/srv/dcomp", "#root/rez/brot"), False),
        ("dcomp", "srv", 10, None, "docker-compose.yml", "code", "text/x-yaml", "services:\n  web:\n    image: nginx\n", False),
        ("konf", "srv", 20, None, "Konfig", "code", "text/x-properties", "[main]\nport = 8080\n", False),
        ("depl", "proj", 20, "Skript", "deploy.sh", "code", "application/x-sh", "#!/bin/sh\necho hallo\n", False),
        ("rez", "root", 20, None, "Rezepte", "book", "", "", False),
        ("brot", "rez", 10, None, "Brot", "text", "text/html", "<p>Mehl, Wasser, Salz.</p>", False),
        ("foto", "root", 30, None, "Foto.png", "image", "image/png", PNG, False),
        ("tres", "root", 40, None, "Tresor", "text", "text/html", "<p>Geheimer Inhalt</p>", True),
        ("offen", "tres", 10, None, "Offen darunter", "text", "text/html", "<p>offen</p>", False),
        ("geh", "root", 50, None, "Geheimnis", "text", "text/html", "<p>Nur mit Passwort</p>", True),
        ("_hidden", "root", 999999, None, "Hidden Notes", "book", "", "", False),
        ("_options", "_hidden", 10, None, "Options", "book", "", "SYSTEM-NOTIZ", False),
        ("_lbRoot", "_hidden", 20, None, "Launch Bar", "launcher", "", "SYSTEM-NOTIZ", False),
    ]


CLONES = [("srv", "rez", 20, None)]                      # Server-Setup zusaetzlich unter Rezepte
ATTRS = [("proj", "label", "wichtig", "", 0), ("proj", "label", "status", "aktiv", 0), ("proj", "relation", "verwandt", "rez", 0),
         ("proj", "label", "iconClass", "bx bx-folder", 0), ("proj", "label", "alt", "x", 1), ("brot", "label", "label:menge", "promoted", 0)]
ATTS = [("att1", "srv", "image", "image/png", "plan.png", PNG), ("att2", "brot", "file", "application/pdf", "rezept.pdf", PDF),
        ("att3", "diag", "image", "image/svg+xml", "canvas-export.svg", SVG)]

SCHEMA_NEW = """
CREATE TABLE notes (noteId TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'note', isProtected INT NOT NULL DEFAULT 0,
  type TEXT NOT NULL DEFAULT 'text', mime TEXT NOT NULL DEFAULT 'text/html', blobId TEXT DEFAULT NULL, isDeleted INT NOT NULL DEFAULT 0,
  deleteId TEXT DEFAULT NULL, dateCreated TEXT NOT NULL, dateModified TEXT NOT NULL, utcDateCreated TEXT NOT NULL,
  utcDateModified TEXT NOT NULL, PRIMARY KEY(noteId));
CREATE TABLE branches (branchId TEXT NOT NULL, noteId TEXT NOT NULL, parentNoteId TEXT NOT NULL, notePosition INTEGER NOT NULL,
  prefix TEXT, isExpanded INTEGER NOT NULL DEFAULT 0, isDeleted INTEGER NOT NULL DEFAULT 0, deleteId TEXT DEFAULT NULL,
  utcDateModified TEXT NOT NULL, PRIMARY KEY(branchId));
CREATE TABLE attributes (attributeId TEXT not null primary key, noteId TEXT not null, type TEXT not null, name TEXT not null,
  value TEXT default '' not null, position INT default 0 not null, utcDateModified TEXT not null, isDeleted INT not null,
  deleteId TEXT DEFAULT NULL, isInheritable int DEFAULT 0 NULL);
CREATE TABLE options (name TEXT not null PRIMARY KEY, value TEXT not null, isSynced INTEGER default 0 not null, utcDateModified TEXT NOT NULL);
CREATE TABLE blobs (blobId TEXT NOT NULL, content TEXT NULL DEFAULT NULL, textRepresentation TEXT DEFAULT NULL,
  dateModified TEXT NOT NULL, utcDateModified TEXT NOT NULL, PRIMARY KEY(blobId));
CREATE TABLE attachments (attachmentId TEXT not null primary key, ownerId TEXT not null, role TEXT not null, mime TEXT not null,
  title TEXT not null, isProtected INT not null DEFAULT 0, position INT default 0 not null, blobId TEXT DEFAULT null,
  dateModified TEXT NOT NULL, utcDateModified TEXT not null, utcDateScheduledForErasureSince TEXT DEFAULT NULL,
  isDeleted INT not null, deleteId TEXT DEFAULT NULL);
CREATE TABLE revisions (revisionId TEXT NOT NULL PRIMARY KEY, noteId TEXT NOT NULL, type TEXT DEFAULT '' NOT NULL,
  mime TEXT DEFAULT '' NOT NULL, title TEXT NOT NULL, isProtected INT NOT NULL DEFAULT 0, blobId TEXT DEFAULT NULL,
  utcDateLastEdited TEXT NOT NULL, utcDateCreated TEXT NOT NULL, utcDateModified TEXT NOT NULL, dateLastEdited TEXT NOT NULL,
  dateCreated TEXT NOT NULL);
CREATE INDEX IDX_branches_noteId_parentNoteId ON branches (noteId, parentNoteId);
"""
SCHEMA_OLD = """
CREATE TABLE notes (noteId TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'note', isProtected INT NOT NULL DEFAULT 0,
  type TEXT NOT NULL DEFAULT 'text', mime TEXT NOT NULL DEFAULT 'text/html', isDeleted INT NOT NULL DEFAULT 0,
  deleteId TEXT DEFAULT NULL, dateCreated TEXT NOT NULL, dateModified TEXT NOT NULL, utcDateCreated TEXT NOT NULL,
  utcDateModified TEXT NOT NULL, PRIMARY KEY(noteId));
CREATE TABLE note_contents (noteId TEXT NOT NULL, content TEXT NULL DEFAULT NULL, dateModified TEXT NOT NULL,
  utcDateModified TEXT NOT NULL, PRIMARY KEY(noteId));
CREATE TABLE branches (branchId TEXT NOT NULL, noteId TEXT NOT NULL, parentNoteId TEXT NOT NULL, notePosition INTEGER NOT NULL,
  prefix TEXT, isExpanded INTEGER NOT NULL DEFAULT 0, isDeleted INTEGER NOT NULL DEFAULT 0, deleteId TEXT DEFAULT NULL,
  utcDateModified TEXT NOT NULL, PRIMARY KEY(branchId));
CREATE TABLE attributes (attributeId TEXT not null primary key, noteId TEXT not null, type TEXT not null, name TEXT not null,
  value TEXT default '' not null, position INT default 0 not null, utcDateModified TEXT not null, isDeleted INT not null,
  deleteId TEXT DEFAULT NULL, isInheritable int DEFAULT 0 NULL);
CREATE TABLE options (name TEXT not null PRIMARY KEY, value TEXT not null, isSynced INTEGER default 0 not null, utcDateModified TEXT NOT NULL);
"""


def build_db(path, old=False, extra_sql=None, huge=False):
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript(SCHEMA_OLD if old else SCHEMA_NEW)
    vs, ds = base64.b64encode(os.urandom(32)).decode(), base64.b64encode(os.urandom(32)).decode()
    dk = os.urandom(16)
    for k, v in (("passwordVerificationSalt", vs), ("passwordDerivedKeySalt", ds),
                 ("passwordVerificationHash", base64.b64encode(_scrypt(PW, vs)).decode()),
                 ("encryptedDataKey", encrypt(_scrypt(PW, ds), dk))):
        c.execute("INSERT INTO options VALUES (?,?,1,?)", (k, v, U))
    rows = notes(old) + [("gone", "root", 60, None, "Gelöscht", "text", "text/html", "<p>GELOESCHT-INHALT</p>", False)]
    if huge:
        rows.append(("riesig", "root", 70, None, "Riesig", "text", "text/html", "<p>" + "x" * (5 * 1024 * 1024) + "</p>", False))
    if old:                                            # alt: keine Anhaenge; Bild im Text verweist auf die Bild-Notiz
        rows = [r for r in rows if r[0] not in ("diag",)]
    for nid, par, pos, prefix, title, typ, mime, content, prot in rows:
        deleted = 1 if nid == "gone" else 0
        body = content if isinstance(content, bytes) else content.encode()
        stored = encrypt(dk, body) if prot else (content if isinstance(content, str) else content)
        t = encrypt(dk, title) if prot else title
        if old:
            c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,NULL,?,?,?,?)", (nid, t, int(prot), typ, mime, deleted, D, D, U, U))
            c.execute("INSERT INTO note_contents VALUES (?,?,?,?)", (nid, stored, D, U))
        else:
            bid = "b" + nid
            c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?)", (nid, t, int(prot), typ, mime, bid, deleted, D, D, U, U))
            c.execute("INSERT INTO blobs VALUES (?,?,NULL,?,?)", (bid, stored, D, U))
        if nid != "root":
            c.execute("INSERT INTO branches VALUES (?,?,?,?,?,0,?,NULL,?)", (f"{par}_{nid}", nid, par, pos, prefix, deleted, U))
        else:
            c.execute("INSERT INTO branches VALUES ('none_root','root','none',0,NULL,0,0,NULL,?)", (U,))
    for nid, par, pos, prefix in CLONES:
        c.execute("INSERT INTO branches VALUES (?,?,?,?,?,0,0,NULL,?)", (f"{par}_{nid}", nid, par, pos, prefix, U))
    for k, (nid, typ, name, val, deleted) in enumerate(ATTRS):
        c.execute("INSERT INTO attributes VALUES (?,?,?,?,?,?,?,?,NULL,0)", (f"a{k}", nid, typ, name, val, k * 10, U, deleted))
    if not old:
        for aid, owner, role, mime, title, data in ATTS:
            c.execute("INSERT INTO blobs VALUES (?,?,NULL,?,?)", ("b" + aid, data, D, U))
            c.execute("INSERT INTO attachments VALUES (?,?,?,?,?,0,10,?,?,?,NULL,0,NULL)", (aid, owner, role, mime, title, "b" + aid, D, U))
        c.execute("INSERT INTO blobs VALUES ('brev','<p>REVISION-ALT</p>',NULL,?,?)", (D, U))
        c.execute("INSERT INTO revisions VALUES ('r1','brot','text','text/html','Brot alt',0,'brev',?,?,?,?,?)", (U, U, U, D, D))
    for sql in extra_sql or []:
        c.execute(sql)
    c.commit()
    c.close()
    with open(path, "rb") as f:
        return f.read()


def build_bad_schema(path):
    """SQLite-Datei mit notes, aber ohne branches -> unbekanntes Schema."""
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE notes (noteId TEXT, title TEXT)")
    c.execute("INSERT INTO notes VALUES ('root', 'root')")
    c.commit()
    c.close()
    with open(path, "rb") as f:
        return f.read()


# =====================================================================================================
# ZIP-Export
# =====================================================================================================
def _page(title, inner, depth):
    css = "../" * depth + "style.css"
    return f"""<html>
<head>
    <meta charset="utf-8">
    <link rel="stylesheet" href="{css}">
    <title data-trilium-title>{title}</title>
</head>
<body>
  <div class="content">
    <h1 data-trilium-h1>{title}</h1>
    <div class="ck-content">
      {inner}
    </div>
  </div>
</body>
</html>"""


MD = {
    "proj": "# Projekte\r\nÜbersicht der Projekte.\n",
    "srv": """# Server-Setup\r
Ein **fetter** und _kursiver_ Text mit `inline` und einem [Link](https://example.org).

### Schritte

1.  Erst
    *   Unterpunkt
2.  Dann

- [x] Erledigt
- [ ] Offen

| Dienst | Port |
| --- | --- |
| Web | 8080 |

> Zitat
>
> zweite Zeile

> [!WARNING]
> Achtung!

```yaml
a: 1
b: 2
```

---

Vorher ![](Server-Setup_plan.png) nachher

Siehe [docker-compose.yml](Server-Setup/docker-compose.yml) und [Brot](../Rezepte/Brot.md).

$$E=mc^2$$
""",
    "brot": "# Brot\r\nMehl, Wasser, Salz.\n",
    "offen": "# Offen darunter\r\noffen\n",
}


def build_zip(fmt="html"):
    """Export der ganzen Datenbank (Wurzel "root") ohne geschuetzte Sitzung - wie Trilium: geschuetzte Notizen als
    "[protected]" ohne Inhalt."""
    ext = "html" if fmt == "html" else "md"
    files = {}

    def text(nid, title, depth, html):
        if fmt == "html":
            return _page(title, html, depth).encode()
        return MD[nid].encode()

    srv_html = server_html("Server-Setup_plan.png", "Server-Setup/docker-compose.yml", "../Rezepte/Brot.html")
    meta_root = {"isClone": False, "noteId": "root", "notePath": ["root"], "title": "root", "notePosition": 0, "prefix": None,
                 "isExpanded": True, "type": "text", "mime": "text/html", "attributes": [], "format": fmt, "attachments": [],
                 "dirFileName": "root", "children": []}

    def meta(nid, path, title, typ, mime, pos, data=None, prefix=None, attrs=None, atts=None, kids=None, dirname=None):
        m = {"isClone": False, "noteId": nid, "notePath": path, "title": title, "notePosition": pos, "prefix": prefix,
             "isExpanded": False, "type": typ, "mime": mime, "attributes": attrs or [], "attachments": atts or []}
        if typ == "text":
            m["format"] = fmt
        if data:
            m["dataFileName"] = data
        if kids is not None:
            m["dirFileName"] = dirname or title
            m["children"] = kids
        return m

    R = ["root"]
    # root/Projekte(.html) mit Kindern
    srv_kids = [meta("dcomp", R + ["proj", "srv", "dcomp"], "docker-compose.yml", "code", "text/x-yaml", 10, "docker-compose.yml"),
                meta("konf", R + ["proj", "srv", "konf"], "Konfig", "code", "text/x-properties", 20, "Konfig.dat")]
    proj_kids = [meta("srv", R + ["proj", "srv"], "Server-Setup", "text", "text/html", 10, f"Server-Setup.{ext}",
                      atts=[{"attachmentId": "att1", "title": "plan.png", "role": "image", "mime": "image/png", "position": 10,
                             "dataFileName": "Server-Setup_plan.png"}], kids=srv_kids),
                 meta("depl", R + ["proj", "depl"], "deploy.sh", "code", "application/x-sh", 20, "Skript - deploy.sh", prefix="Skript"),
                 meta("diag", R + ["proj", "diag"], "Diagramm", "canvas", "application/json", 30, "Diagramm.json",
                      atts=[{"attachmentId": "att3", "title": "canvas-export.svg", "role": "image", "mime": "image/svg+xml",
                             "position": 10, "dataFileName": "Diagramm_canvas-export.svg"}])]
    attrs = [{"type": "label", "name": "wichtig", "value": "", "isInheritable": False, "position": 0},
             {"type": "label", "name": "status", "value": "aktiv", "isInheritable": False, "position": 10},
             {"type": "relation", "name": "verwandt", "value": "rez", "isInheritable": False, "position": 20},
             {"type": "label", "name": "iconClass", "value": "bx bx-folder", "isInheritable": False, "position": 30}]
    proj = meta("proj", R + ["proj"], "Projekte", "text", "text/html", 10, f"Projekte.{ext}", attrs=attrs, kids=proj_kids)
    rez_kids = [meta("brot", R + ["rez", "brot"], "Brot", "text", "text/html", 10, f"Brot.{ext}",
                     atts=[{"attachmentId": "att2", "title": "rezept.pdf", "role": "file", "mime": "application/pdf", "position": 10,
                            "dataFileName": "Brot_rezept.pdf"}]),
                {"isClone": True, "noteId": "srv", "notePath": R + ["rez", "srv"], "title": "Server-Setup", "prefix": None,
                 "dataFileName": f"Server-Setup.clone.{ext}", "type": "text", "format": fmt}]
    rez = meta("rez", R + ["rez"], "Rezepte", "book", "", 20, None, kids=rez_kids)
    foto = meta("foto", R + ["foto"], "Foto.png", "image", "image/png", 30, "Foto.png")
    tres = meta("tres", R + ["tres"], "[protected]", "text", "text/html", 40, None, dirname="[protected]",
                kids=[meta("offen", R + ["tres", "offen"], "Offen darunter", "text", "text/html", 10, f"Offen darunter.{ext}")])
    geh = meta("geh", R + ["geh"], "[protected]", "text", "text/html", 50, None)
    meta_root["children"] = [proj, rez, foto, tres, geh]
    top = {"formatVersion": 2, "appVersion": "0.99.0", "files": [meta_root]}
    if fmt == "html":
        top["files"] += [{"noImport": True, "dataFileName": "navigation.html"}, {"noImport": True, "dataFileName": "index.html"},
                         {"noImport": True, "dataFileName": "style.css"}]
        files["navigation.html"] = b"<html><body><ul><li>root</li></ul></body></html>"
        files["index.html"] = b"<html><frameset></frameset></html>"
        files["style.css"] = b"body{}"
    files["!!!meta.json"] = json.dumps(top, indent="\t").encode()
    files[f"root/Projekte.{ext}"] = text("proj", "Projekte", 2, "<p>Übersicht der Projekte.</p>")
    files[f"root/Projekte/Server-Setup.{ext}"] = text("srv", "Server-Setup", 3, srv_html)
    files["root/Projekte/Server-Setup_plan.png"] = PNG
    files["root/Projekte/Server-Setup/docker-compose.yml"] = b"services:\n  web:\n    image: nginx\n"
    files["root/Projekte/Server-Setup/Konfig.dat"] = b"[main]\nport = 8080\n"
    files["root/Projekte/Skript - deploy.sh"] = b"#!/bin/sh\necho hallo\n"
    files["root/Projekte/Diagramm.json"] = b'{"elements":[]}'
    files["root/Projekte/Diagramm_canvas-export.svg"] = SVG
    files[f"root/Rezepte/Brot.{ext}"] = text("brot", "Brot", 3, "<p>Mehl, Wasser, Salz.</p>")
    files["root/Rezepte/Brot_rezept.pdf"] = PDF
    files[f"root/Rezepte/Server-Setup.clone.{ext}"] = b'<p>This is a clone of a note. Go to its <a href="../Projekte/Server-Setup.html">primary location</a>.</p>'
    files["root/Foto.png"] = PNG
    files[f"root/[protected]/Offen darunter.{ext}"] = text("offen", "Offen darunter", 3, "<p>offen</p>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()
