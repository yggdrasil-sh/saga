"""Trilium importieren (2.5). Zwei Quellen, eine Abbildung: beide werden erst in ein gemeinsames Zwischenmodell (Model:
Notizen mit Kindern in Trilium-Reihenfolge, Attributen, Anhaengen, Lader fuer Inhalte) gelesen, daraus entsteht EIN Plan
fuer import_common.apply_plan.

Quellen
  1. Offizieller ZIP-Export (!!!meta.json, Format "HTML" oder "Markdown"): Baum, Titel, Typen, Mime, Attribute und
     Anhaenge kommen aus der Meta-Datei, Inhalte aus den Dateien (dataFileName). navigation.html/index.html/style.css
     (noImport) und Klon-Dateien (*.clone.*) werden nicht gelesen. Geschuetzte Notizen schreibt Trilium ohne offene
     Sitzung nur als "[protected]" ohne Inhalt -> im Bericht "geschuetzt"; mit offener Sitzung sind sie Klartext.
  2. Trilium-Sicherung bzw. document.db (SQLite): nur lesen (mode=ro&immutable=1, DEFENSIVE, trusted_schema aus, ohne
     Trigger/Views, Erweiterungen aus, query_only, Zeitlimit ueber den Fortschritts-Handler, nur SELECT). Die Datei ist
     die Upload-Kopie im Import-Ordner (nie die Originaldatei der Person). Schema neu (blobs + notes.blobId,
     attachments) und alt (note_contents); anderes -> klare Meldung. Trigger, Views oder virtuelle Tabellen (hat Trilium
     nie) -> abgelehnt. Nicht uebernommen: geloeschte Notizen/Zweige/Attribute/Anhaenge, Revisionen, der Systembaum
     (_hidden, _options, _lbRoot ... - jede Notiz mit "_"-Kennung samt Teilbaum).
     Geschuetzte Notizen: mit Trilium-Passwort (nur im Lauf, nie gespeichert/geloggt) entschluesselt - Verfahren wie
     Trilium (services/encryption): scrypt(N=16384, r=8, p=1, 32 Byte) mit passwordVerificationSalt -> Base64 ==
     passwordVerificationHash; scrypt mit passwordDerivedKeySalt -> Schluessel fuer encryptedDataKey; Titel und Inhalte
     mit dem Datenschluessel: Base64(IV 16 bzw. alt 13 Byte + AES-128-CBC(SHA1(klar)[:4] + klar)), Schluessel/IV auf
     16 Byte gekuerzt bzw. mit Nullen aufgefuellt. Ohne Passwort: uebersprungen (Bericht nennt nur die Position).

Abbildung (fuer beide Quellen gleich)
  - Eine Sammelseite oben: exportierte Wurzel (ZIP eines Teilbaums: diese Notiz mit Inhalt) bzw. "Trilium-Import
    <Datum>" fuer die ganze Datenbank (Trilium-"root"). Darunter der Baum 1:1 in Trilium-Reihenfolge (notePosition),
    ohne Zwischenseiten; Notiz mit Kindern = EINE Seite mit Inhalt und Unterseiten.
  - Titel aus Meta/DB (keine Dateiendungen). Zweig-Prefix (Trilium zeigt "Prefix - Titel" im Baum): in den Titel
    der jeweiligen Seite uebernommen, genau so.
  - Klone (Notiz an mehreren Stellen): eine echte Seite (erste Stelle in Baumreihenfolge), an jeder weiteren Stelle
    eine Verweisseite (🔗) mit Link darauf.
  - text: HTML (saga/html2blocks.py) bzw. Markdown (md2blocks, ZIP im Format Markdown); erste Ueberschrift gleich dem
    Titel faellt weg. code/mermaid: genau ein Code-Block, Sprache aus dem Mime-Typ. book: Seite. image/file: Datei
    (Bild-/Datei-Block). Anhaenge: Saga-Dateien, an ihrer Stelle im Text; nicht im Text verwendete Anhaenge unten auf
    der Seite der Besitzer-Notiz. Nicht Abbildbares (canvas, mindMap, relationMap, render, webView, ...): Seite mit
    Hinweis (+ Anhaenge, z. B. die SVG-Vorschau), im Bericht.
  - Interne Links (#root/.../id, Export-Pfade), Bilder (api/images, api/attachments), Include-Notizen -> Saga.
  - Labels ohne Wert -> Saga-Tags (Option tags), Labels mit Wert und Relationen -> ausklappbarer Block "Eigenschaften"
    (Option props); System-Attribute von Trilium (iconClass, cssClass, label:/relation:-Definitionen ...) fallen weg."""
import base64
import hashlib
import hmac
import io
import json
import os
import posixpath
import re
import sqlite3
import threading
import time
import urllib.parse
from html import escape

from saga import html2blocks, i18n, md2blocks
from saga import import_common as ic
from saga.core import TZ, t
from saga.files import _sniff_image
from saga.notes import FILE_MAX

MB = ic.MB
TEXT_MAX = 4 * MB                         # Text-/Code-Inhalt einer Notiz
META_MAX = 64 * MB                        # !!!meta.json
NOTES_MAX = ic.ENTRIES_MAX                # Notizen einer Datenbank
TIME_ANALYZE, TIME_RUN, TIME_KEY = 60, 1800, 30
MAGIC = b"SQLite format 3\x00"
META = "!!!meta.json"
PW_MAX = 1024
FAILS, FAILS_PER = 5, 600                 # Fehlversuche Passwort je Konto und Zeitraum
_ID = re.compile(r"[A-Za-z0-9_]{1,64}")
CODE_TYPES = ("code", "mermaid")
PAGE_TYPES = ("text", "book", "code", "mermaid", "image", "file")
SYS_LABELS = {
    "iconclass", "cssclass", "workspace", "workspaceiconclass", "workspacetabbackgroundcolor", "workspacecalendarroot",
    "workspacetemplate", "workspacesearchhome", "workspaceinbox", "archived", "sorted", "sortdirection", "sortfoldersfirst",
    "sortnatural", "sortlocale", "top", "bottom", "readonly", "autoreadonlydisabled", "hidepromotedattributes", "viewtype",
    "expanded", "pagesize", "run", "runoninstance", "runathour", "customrequesthandler", "customresourceprovider", "widget",
    "appcss", "apptheme", "appthemebase", "excludefromexport", "excludefromnotemap", "shareroot", "sharehiddenfromtree",
    "sharealias", "shareomitdefaultcss", "shareraw", "sharedisallowrobotindexing", "sharecredentials", "shareindex",
    "displayrelations", "hiderelations", "titletemplate", "toc", "color", "keyboardshortcut", "keepcurrenthoisting",
    "executebutton", "executedescription", "newnotesontop", "hidechildrenoverview", "printlandscape", "printpagesize",
    "maptype", "originalfilename", "calendarroot", "datenote", "monthnote", "yearnote", "weeknote", "quarternote", "inbox",
    "sqlconsolehome", "bookmarked", "bookmarkfolder", "searchhome", "hoistedsearchhome", "hoistedinbox", "docname",
    "launchertype", "builtinwidget", "basesize", "growthfactor", "command", "datepattern", "template", "tabwidth",
    "language", "mapincludestypes", "geolocation", "fullcontentwidth", "ancestordepth", "includearchived", "orderby",
    "orderdirection", "limit", "debug", "searchstring", "cliponly", "clipperinbox", "board:groupby", "calendar:title",
    "webviewsrc", "renderdisplay", "textrepresentation", "ocrtext"}
SYS_RELATIONS = {"internallink", "imagelink", "includenotelink", "relationmaplink", "template", "inherit", "rendernote",
                 "widget", "sharecss", "sharejs", "sharefavicon", "sharetemplate", "searchscript", "targetnote", "ancestor",
                 "widget_relation", "child:template", "child:child:template"}
TYPE_NAMES = {"canvas": "Canvas", "mindMap": "Mind Map", "relationMap": "Relation Map", "noteMap": "Note Map",
              "render": "Render HTML", "webView": "Web View", "search": "Saved Search", "launcher": "Launcher",
              "doc": "Doc", "contentWidget": "Widget", "geoMap": "Geo Map", "aiChat": "AI Chat", "shortcut": "Shortcut",
              "spreadsheet": "Spreadsheet"}

_fails, _fail_lock = {}, threading.Lock()


# =====================================================================================================
# Erkennen
# =====================================================================================================
def looks_like(paths):
    """ZIP-Export: !!!meta.json oben bzw. in genau einem Ordner."""
    return any(p == META or (p.endswith("/" + META) and p.count("/") == 1) for p in paths)


def is_db(path):
    try:
        with open(path, "rb") as f:
            return f.read(16) == MAGIC
    except OSError:
        return False


def db_path(u):
    """Upload ist eine einzelne SQLite-Datei -> Pfad, sonst None."""
    files = u.get("files") or []
    return files[0]["path"] if len(files) == 1 and is_db(files[0]["path"]) else None


# =====================================================================================================
# Verschluesselung (wie Trilium, siehe Moduldoc)
# =====================================================================================================
def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode("utf-8"), salt=str(salt).encode("utf-8"), n=16384, r=8, p=1, dklen=32, maxmem=64 * MB)


def _pad16(b):
    return (bytes(b[:16]) + b"\0" * 16)[:16]


def decrypt(key, data):
    """Trilium-Chiffrat (Base64-Text bzw. -Bytes) -> Klartext-Bytes oder None (falscher Schluessel/kaputt)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if data is None:
        return None
    try:
        raw = base64.b64decode(data.encode("ascii") if isinstance(data, str) else bytes(data))
    except (ValueError, UnicodeError):
        return None
    iv_len = 16 if len(raw) % 16 == 0 else 13
    iv, ct = raw[:iv_len], raw[iv_len:]
    if not ct or len(ct) % 16:
        return None
    d = Cipher(algorithms.AES(_pad16(key)), modes.CBC(_pad16(iv))).decryptor()
    try:
        dec = d.update(ct) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        dec = u.update(dec) + u.finalize()
    except ValueError:
        return None
    digest, payload = dec[:4], dec[4:]
    if len(digest) != 4 or hashlib.sha1(payload).digest()[:4] != digest:
        return None
    return payload


def encrypt(key, plain):
    """Gegenstueck zu decrypt (fuer Test-Daten, tests/import_trilium.py)."""
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    plain = plain.encode("utf-8") if isinstance(plain, str) else bytes(plain)
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    data = p.update(hashlib.sha1(plain).digest()[:4] + plain) + p.finalize()
    e = Cipher(algorithms.AES(_pad16(key)), modes.CBC(iv)).encryptor()
    return base64.b64encode(iv + e.update(data) + e.finalize()).decode("ascii")


def _rate_ok(uid, count=False):
    now = time.time()
    with _fail_lock:
        xs = [x for x in _fails.get(uid, []) if now - x < FAILS_PER]
        if count:
            xs.append(now)
        _fails[uid] = xs
        if len(_fails) > 5000:
            _fails.clear()
        return len(xs) < FAILS


def data_key(uid, path, pw):
    """Trilium-Passwort pruefen und Datenschluessel holen. ImportFail: trilium_password (falsch), trilium_rate,
    trilium_nokey (Datenbank ohne Passwort-Daten)."""
    if not _rate_ok(uid):
        raise ic.ImportFail("trilium_rate")
    db = _DB(path, TIME_KEY)
    try:
        opts = db.options()
    finally:
        db.close()
    vh, vs = opts.get("passwordVerificationHash"), opts.get("passwordVerificationSalt")
    ds, edk = opts.get("passwordDerivedKeySalt"), opts.get("encryptedDataKey")
    if not (vh and vs and ds and edk):
        raise ic.ImportFail("trilium_nokey")
    given = base64.b64encode(_scrypt(pw, vs)).decode("ascii")
    if not hmac.compare_digest(given.encode(), str(vh).encode()):
        _rate_ok(uid, count=True)
        raise ic.ImportFail("trilium_password")
    key = decrypt(_scrypt(pw, ds), edk)
    if not key:
        _rate_ok(uid, count=True)
        raise ic.ImportFail("trilium_password")
    return key


# =====================================================================================================
# Zwischenmodell
# =====================================================================================================
class TNote:
    __slots__ = ("nid", "title", "type", "mime", "fmt", "locked", "attrs", "atts", "kids", "load", "size", "path", "pos")

    def __init__(self, nid, title="", typ="text", mime="", fmt=None):
        self.nid, self.title, self.type, self.mime, self.fmt = nid, title, typ, mime, fmt
        self.locked = False                # geschuetzt und nicht lesbar
        self.attrs, self.atts, self.kids = [], [], []      # kids: [(nid, prefix, pos, is_clone)]
        self.load, self.size, self.path, self.pos = None, 0, "", 0


class TAtt:
    __slots__ = ("aid", "title", "role", "mime", "size", "load", "path", "pos")

    def __init__(self, aid, title, role, mime, size, load, path="", pos=0):
        self.aid, self.title, self.role, self.mime, self.size, self.load, self.path, self.pos = \
            aid, title, role, mime, size, load, path, pos


class Model:
    """notes {nid: TNote}, root (nid); Quelle "zip" (by_path: Pfad -> ("n", nid) | ("a", aid)) oder "db"."""

    def __init__(self, kind):
        self.kind, self.notes, self.root, self.by_path, self.att_owner = kind, {}, None, {}, {}
        self.fmt, self.hidden, self.dirs, self.schema = None, 0, {}, ""
        self.heads = {}

    def head(self, key, load):
        """Erste Bytes eines Inhalts (Bild?) - einmal je Datei."""
        if key not in self.heads:
            try:
                b = load(16) if load is not None else b""
            except (ic.ImportFail, OSError, sqlite3.Error, ValueError):
                b = b""
            self.heads[key] = bool(_sniff_image(b or b""))
        return self.heads[key]


# ---------------------------------------------------------------------------------------------------- ZIP
def _meta_base(paths):
    for p in paths:
        if p == META:
            return ""
    for p in paths:
        if p.endswith("/" + META) and p.count("/") == 1:
            return p.split("/", 1)[0] + "/"
    return None


def zip_model(src, rep):
    base = _meta_base(src.paths())
    if base is None:
        raise ic.ImportFail("trilium_meta")
    try:
        meta = json.loads(src.read(base + META, META_MAX).decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        raise ic.ImportFail("trilium_meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("files"), list):
        raise ic.ImportFail("trilium_meta")
    m = Model("zip")
    used = {base + META}
    files = [x for x in meta["files"] if isinstance(x, dict)]
    for x in files:
        if x.get("noImport") and isinstance(x.get("dataFileName"), str):
            used.add(base + x["dataFileName"])
    tops = [x for x in files if not x.get("noImport")]
    if not tops:
        raise ic.ImportFail("nothing")
    syn = [0]

    def nid_of(x):
        v = x.get("noteId")
        if isinstance(v, str) and _ID.fullmatch(v):
            return v
        syn[0] += 1
        return f"zip{syn[0]}"

    def fname(v):
        return v if isinstance(v, str) and v and "/" not in v and v not in (".", "..") else None

    root = TNote("#zip-root")
    m.notes[root.nid] = root
    stack = [(x, root.nid, base, i) for i, x in reversed(list(enumerate(tops)))]
    n = 0
    while stack:
        x, parent, pre, i = stack.pop()
        n += 1
        if n > NOTES_MAX:
            raise ic.ImportFail("trilium_many", n=NOTES_MAX)
        nid = nid_of(x)
        prefix = x.get("prefix") if isinstance(x.get("prefix"), str) else ""
        pos = x.get("notePosition") if isinstance(x.get("notePosition"), (int, float)) else i * 10
        df = fname(x.get("dataFileName"))
        if df:
            used.add(pre + df)
        if x.get("isClone") or nid in m.notes:
            m.notes[parent].kids.append((nid, prefix, pos, True))
            continue
        title = x.get("title") if isinstance(x.get("title"), str) else ""
        typ = x.get("type") if isinstance(x.get("type"), str) else "text"
        mime = x.get("mime") if isinstance(x.get("mime"), str) else ""
        note = TNote(nid, title[:500], typ[:40], mime[:120], x.get("format") if x.get("format") in ("html", "markdown") else None)
        note.pos = pos
        if note.fmt and not m.fmt:
            m.fmt = note.fmt
        path = pre + df if df else None
        if path and path in src.entries:
            note.size = src.entries[path].size
            note.path = path
            note.load = (lambda limit=None, p=path: src.read(p, max(TEXT_MAX, FILE_MAX)) if limit is None else _head(src, p, limit))
            m.by_path[path] = ("n", nid)
        elif title == "[protected]":
            note.locked = True
        for k, a in enumerate(x.get("attributes") if isinstance(x.get("attributes"), list) else []):
            if isinstance(a, dict) and a.get("type") in ("label", "relation") and isinstance(a.get("name"), str):
                note.attrs.append((a["type"], a["name"][:100], str(a.get("value") or "")[:2000], a.get("position") or k))
        for k, a in enumerate(x.get("attachments") if isinstance(x.get("attachments"), list) else []):
            if not isinstance(a, dict):
                continue
            af = fname(a.get("dataFileName"))
            ap = pre + af if af else None
            if not ap or ap not in src.entries:
                continue
            used.add(ap)
            aid = a.get("attachmentId") if isinstance(a.get("attachmentId"), str) and _ID.fullmatch(a["attachmentId"]) else f"{nid}-a{k}"
            at = TAtt(aid, str(a.get("title") or af)[:200], str(a.get("role") or "file"), str(a.get("mime") or ""),
                      src.entries[ap].size, (lambda limit=None, p=ap: src.read(p, FILE_MAX) if limit is None else _head(src, p, limit)),
                      ap, a.get("position") if isinstance(a.get("position"), (int, float)) else k)
            note.atts.append(at)
            m.by_path[ap] = ("a", aid)
            m.att_owner[aid] = nid
        m.notes[nid] = note
        m.notes[parent].kids.append((nid, prefix, pos, False))
        kids = [c for c in (x.get("children") if isinstance(x.get("children"), list) else []) if isinstance(c, dict)]
        d = fname(x.get("dirFileName"))
        sub = pre + d + "/" if d else pre
        for j, c in reversed(list(enumerate(kids))):
            stack.append((c, nid, sub, j))
    for p in src.paths():
        if p not in used:
            rep.note(p, "ignored")
    if len(root.kids) == 1 and not root.kids[0][3]:
        m.root = root.kids[0][0]                       # Export eines Teilbaums: diese Notiz ist die Sammelseite
        m.notes[m.root].kids.sort(key=lambda k: k[2])
    else:
        m.root = root.nid
    for note in m.notes.values():
        note.kids.sort(key=lambda k: k[2])
    return m


def _head(src, p, n):
    with src.open(p) as f:
        return f.read(n)


# ---------------------------------------------------------------------------------------------------- SQLite
class _DB:
    """Trilium-Datenbank, nur lesend und abgesichert geoeffnet (siehe Moduldoc)."""

    def __init__(self, path, seconds, cancelled=None):
        if not is_db(path):
            raise ic.ImportFail("bad_file")
        self.deadline = time.monotonic() + seconds
        self.timed_out = False
        self.cancelled = cancelled or (lambda: False)
        uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro&immutable=1"
        con = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
        self.con = con
        try:
            con.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            con.setconfig(sqlite3.SQLITE_DBCONFIG_TRUSTED_SCHEMA, False)
            con.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_TRIGGER, False)
            con.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_VIEW, False)
            con.setconfig(sqlite3.SQLITE_DBCONFIG_ENABLE_LOAD_EXTENSION, False)
            con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, max(FILE_MAX, TEXT_MAX) * 2 + MB)
            con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000)
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            con.text_factory = lambda b: b.decode("utf-8", "replace")
            con.set_progress_handler(self._tick, 20_000)
            con.execute("PRAGMA trusted_schema=OFF")
            con.execute("PRAGMA query_only=ON")
            self._check()
        except BaseException:
            con.close()
            raise

    def _tick(self):
        if self.cancelled():
            return 1
        if time.monotonic() > self.deadline:
            self.timed_out = True
            return 1
        return 0

    def q(self, sql, args=()):
        return self.con.execute(sql, args).fetchall()

    def _check(self):
        rows = self.q("SELECT type, name, sql FROM sqlite_master")
        if any(r[0] in ("trigger", "view") or (r[0] == "table" and re.match(r"\s*create\s+virtual", r[2] or "", re.I))
               for r in rows):
            raise ic.ImportFail("trilium_unsafe")
        tabs = {r[1] for r in rows if r[0] == "table"}
        self.cols = {}
        for tb in ("notes", "branches", "attributes", "attachments", "blobs", "note_contents", "options"):
            if tb in tabs:
                self.cols[tb] = {r[1] for r in self.q(f'PRAGMA table_info("{tb}")')}
        need = {"notes": {"noteId", "title", "type", "mime", "isProtected", "isDeleted"},
                "branches": {"noteId", "parentNoteId", "notePosition", "isDeleted"}}
        for tb, cs in need.items():
            if not cs <= self.cols.get(tb, set()):
                raise ic.ImportFail("trilium_schema")
        if "blobId" in self.cols["notes"] and {"blobId", "content"} <= self.cols.get("blobs", set()):
            self.schema = "blobs"
        elif {"noteId", "content"} <= self.cols.get("note_contents", set()):
            self.schema = "note_contents"
        else:
            raise ic.ImportFail("trilium_schema")
        if not self.q("SELECT 1 FROM notes WHERE noteId='root'"):
            raise ic.ImportFail("trilium_schema")
        try:
            self.q("SELECT octet_length(x'00')")
            self.olen = "octet_length"
        except sqlite3.Error:
            self.olen = "length"

    def options(self):
        if {"name", "value"} <= self.cols.get("options", set()):
            return {r[0]: r[1] for r in self.q("SELECT name, value FROM options WHERE name IN "
                                                 "('passwordVerificationHash','passwordVerificationSalt','passwordDerivedKeySalt','encryptedDataKey')")}
        return {}

    def close(self):
        try:
            self.con.close()
        except sqlite3.Error:
            pass


def db_model(db, key, rep):
    """Zwischenmodell aus der Datenbank (Inhalte werden erst beim Einspielen gelesen)."""
    m = Model("db")
    m.schema = db.schema
    nc = db.q("SELECT COUNT(*) FROM notes WHERE isDeleted=0")[0][0]
    if nc > NOTES_MAX:
        raise ic.ImportFail("trilium_many", n=NOTES_MAX)
    blob = ", blobId" if db.schema == "blobs" else ""
    for r in db.q(f"SELECT noteId, title, isProtected, type, mime{blob} FROM notes WHERE isDeleted=0"):
        nid = r[0] if isinstance(r[0], str) else str(r[0])
        if not _ID.fullmatch(nid):
            continue
        title, prot = r[1] if isinstance(r[1], str) else "", bool(r[2])
        note = TNote(nid, "", str(r[3] or "text")[:40], str(r[4] or "")[:120], "html" if (r[3] or "text") in ("text", "book") else None)
        if prot:
            plain = decrypt(key, title) if key else None
            if plain is None:
                note.locked = True
            else:
                title = plain.decode("utf-8", "replace")
        note.title = "" if note.locked else title[:500]
        bid = r[5] if db.schema == "blobs" else nid
        note.load = _db_loader(db, "notes", bid, prot and not note.locked, key)
        if note.type in ("image", "file") and not note.locked:
            sel = "FROM blobs WHERE blobId=?" if db.schema == "blobs" else "FROM note_contents WHERE noteId=?"
            note.size = (db.q(f"SELECT {db.olen}(content) {sel}", (bid,)) or [[0]])[0][0] or 0
            if prot:
                note.size = note.size * 3 // 4
        m.notes[nid] = note
    m.root = "root"
    pre = "prefix" if "prefix" in db.cols["branches"] else "''"
    for cid, pid, pos, prefix in db.q(f"SELECT noteId, parentNoteId, notePosition, {pre} FROM branches WHERE isDeleted=0"):
        if pid in m.notes and cid in m.notes and cid != pid:
            m.notes[pid].kids.append((cid, prefix if isinstance(prefix, str) else "", pos if isinstance(pos, (int, float)) else 0, False))
    for note in m.notes.values():
        note.kids.sort(key=lambda k: (k[2], k[0]))
    ac = db.cols.get("attributes", set())
    if {"noteId", "type", "name", "value", "isDeleted"} <= ac:
        posc = "position" if "position" in ac else "0"
        for nid, typ, name, val, pos in db.q(f"SELECT noteId, type, name, value, {posc} FROM attributes WHERE isDeleted=0"):
            if nid in m.notes and typ in ("label", "relation") and isinstance(name, str):
                m.notes[nid].attrs.append((typ, name[:100], str(val or "")[:2000], pos or 0))
    tc = db.cols.get("attachments", set())
    if db.schema == "blobs" and {"attachmentId", "ownerId", "role", "mime", "title", "blobId", "isDeleted"} <= tc:
        protc = "isProtected" if "isProtected" in tc else "0"
        posc = "position" if "position" in tc else "0"
        for aid, owner, role, mime, title, bid, prot, pos in db.q(
                f"SELECT attachmentId, ownerId, role, mime, title, blobId, {protc}, {posc} FROM attachments WHERE isDeleted=0"):
            if owner not in m.notes or not isinstance(aid, str) or not _ID.fullmatch(aid):
                continue
            title = title if isinstance(title, str) else ""
            if prot:
                plain = decrypt(key, title) if key else None
                if plain is None:
                    continue                           # geschuetzter Anhang ohne Schluessel: mit der Notiz uebersprungen
                title = plain.decode("utf-8", "replace")
            size = (db.q(f"SELECT {db.olen}(content) FROM blobs WHERE blobId=?", (bid,)) or [[0]])[0][0] or 0
            if prot:
                size = size * 3 // 4
            note = m.notes[owner]
            note.atts.append(TAtt(aid, title[:200] or aid, str(role or "file"), str(mime or ""), size,
                                  _db_loader(db, "attachments", bid, bool(prot), key), "", pos or 0))
            m.att_owner[aid] = owner
    for note in m.notes.values():
        note.attrs.sort(key=lambda a: a[3])
        note.atts.sort(key=lambda a: a.pos)
    return m


def _db_loader(db, kind, bid, prot, key):
    """Lader fuer einen Inhalt: load(limit=None) -> Bytes (limit: nur die ersten Bytes, Kopf fuers Erkennen)."""
    if db.schema == "blobs":
        sel, args = "FROM blobs WHERE blobId=?", (bid,)
    else:
        sel, args = "FROM note_contents WHERE noteId=?", (bid,)
    cap = (FILE_MAX if kind == "attachments" else max(FILE_MAX, TEXT_MAX))

    def load(limit=None):
        if bid is None:
            return b""
        if limit is not None and not prot:
            r = db.q(f"SELECT substr(content, 1, {int(limit)}) {sel}", args)
            v = r[0][0] if r else b""
            return v.encode("utf-8") if isinstance(v, str) else (v or b"")
        n = (db.q(f"SELECT {db.olen}(content) {sel}", args) or [[0]])[0][0] or 0
        if n > (cap * 4 // 3 + 64 if prot else cap):
            raise ic.ImportFail("too_big", mb=cap // MB)
        r = db.q(f"SELECT content {sel}", args)
        v = r[0][0] if r else None
        v = v.encode("utf-8") if isinstance(v, str) else (bytes(v) if v is not None else b"")
        if prot:
            v = decrypt(key, v) if v else b""
            if v is None:
                raise ic.ImportFail("bad_file")
        return v[:limit] if limit is not None else v
    return load


# =====================================================================================================
# Plan (gleich fuer beide Quellen)
# =====================================================================================================
class Walk:
    """Baum ab der Wurzel in Trilium-Reihenfolge -> Eintraege (art, nid, parent_ref, titel, pfad, ref)."""

    def __init__(self, m, rep=None):
        self.m, self.rep = m, rep
        self.refs, self.entries = {}, []
        self.stats = {"pages": 0, "code": 0, "files": 0, "protected": 0, "clones": 0, "unsupported": 0, "hidden": 0}
        seen = {m.root}
        self.refs[m.root] = "root"
        stack = [(c, "root", "", k) for k, c in reversed(list(enumerate(m.notes[m.root].kids)))]
        n = 0
        while stack:
            (cid, prefix, _pos, clone), pref, ppath, k = stack.pop()
            note = m.notes.get(cid)
            if note is None:
                continue
            if cid.startswith("_"):
                continue                                    # Systembaum von Trilium (_hidden, _options, _lbRoot ...)
            title = (f"{prefix} - " if prefix else "") + (note.title or "")
            if clone or cid in seen:
                n += 1
                self.stats["clones"] += 1
                self.entries.append(("clone", cid, pref, title, ppath, f"k{n}"))
                continue
            seen.add(cid)
            if note.locked:
                self.stats["protected"] += 1
                pos = f"{ppath + '/' if ppath else ''}🔒 {k + 1}"
                open_kids = self._open_below(cid)
                if rep is not None:
                    rep.skip(pos, "protected")
                if not open_kids:
                    below = self._below(cid) - {cid}
                    seen |= below
                    self.stats["protected"] += len(below)
                    continue
                n += 1
                ref = f"p{n}"
                self.refs[cid] = ref
                self.entries.append(("locked", cid, pref, t("Geschützte Notiz"), pos, ref))
                path = pos
            else:
                n += 1
                ref = f"t{n}"
                self.refs[cid] = ref
                path = (ppath + "/" if ppath else "") + (title or "…")[:80]
                kind = "page"
                if note.type in CODE_TYPES:
                    self.stats["code"] += 1
                elif note.type in ("image", "file"):
                    self.stats["files"] += 1
                elif note.type not in PAGE_TYPES:
                    self.stats["unsupported"] += 1
                    kind = "other"
                self.stats["pages"] += 1
                self.stats["files"] += len(note.atts)
                self.entries.append((kind, cid, pref, title, path, ref))
            for j, c in reversed(list(enumerate(note.kids))):
                stack.append((c, ref, path, j))
        self.stats["hidden"] = sum(1 for x in m.notes if x not in seen and x != m.root and not x.startswith("#"))

    def _below(self, nid):
        out, st = set(), [nid]
        while st:
            x = st.pop()
            if x in out or x not in self.m.notes or x.startswith("_"):
                continue
            out.add(x)
            st.extend(c[0] for c in self.m.notes[x].kids)
        return out

    def _open_below(self, nid):
        return any(not self.m.notes[x].locked for x in self._below(nid) if x != nid)


def analyze(src, rep, u=None):
    path = db_path(u) if u else None
    if path:
        db = _DB(path, TIME_ANALYZE)
        try:
            m = db_model(db, None, rep)
        except sqlite3.Error:
            raise ic.ImportFail("trilium_timeout" if db.timed_out else "bad_file")
        finally:
            db.close()
    else:
        m = zip_model(src, rep)
    w = Walk(m)
    s = w.stats
    warn = []
    if s["protected"] and m.kind == "zip":
        warn.append(t("{n} geschützte Notizen sind nicht im ZIP-Export enthalten – für sie die Trilium-Sicherung (.db) importieren.",
                      n=s["protected"]))
    if m.kind == "zip" and m.fmt == "markdown":
        warn.append(t("Markdown-Export: Tabellen mit verbundenen Zellen und Farben gehen verloren – der HTML-Export ist genauer."))
    big = sum(1 for n in m.notes.values() for a in n.atts if a.size > FILE_MAX) + \
        sum(1 for n in m.notes.values() if n.type in ("image", "file") and n.size > FILE_MAX)
    if big:
        warn.append(t("{n} Dateien sind größer als {mb} MB und werden nicht übernommen.", n=big, mb=FILE_MAX // MB))
    root = m.notes.get(m.root)
    title = root.title if root is not None and m.root not in ("root", "#zip-root") else ""
    return {"trilium": {"source": m.kind, "format": m.fmt or ("html" if m.kind == "db" else None), "schema": m.schema,
                        "pages": s["pages"], "code": s["code"], "files": s["files"], "protected": s["protected"],
                        "clones": s["clones"], "unsupported": s["unsupported"], "hidden": s["hidden"], "title": title,
                        "password": bool(s["protected"]) and m.kind == "db"}, "warnings": warn}


def work(uid, u, b, parent):
    """Lauf fuer importer.start: work(run, src) -> (root, parent). Prueft ein angegebenes Passwort SOFORT (falsch ->
    ImportFail, nichts wird angelegt); danach ist nur noch der Datenschluessel im Speicher, nie das Passwort."""
    want_tags, want_props = b.get("tags", True) is not False, b.get("props", True) is not False
    title = str(b.get("title") or "").strip()[:200]
    pw = b.pop("password", None)
    path = db_path(u)
    key = None
    if pw is not None and not (isinstance(pw, str) and len(pw) <= PW_MAX):
        raise ic.ImportFail("trilium_password")
    if pw and path:
        try:
            key = data_key(uid, path, pw)
        except sqlite3.Error:
            raise ic.ImportFail("bad_file")
    pw = None  # noqa: F841 - Passwort nicht laenger halten als noetig

    def go(run, src):
        rep = run.report
        run.step("analyze", 0, 0, force=True)
        db = None
        try:
            if path:
                db = _DB(path, TIME_RUN, cancelled=lambda: run.id in ic._cancel)
                m = db_model(db, key, rep)
            else:
                m = zip_model(src, rep)
            plan = build_plan(m, rep, title, want_tags, want_props)
            if len(plan["items"]) <= 1 and not plan["items"][0].get("body"):
                raise ic.ImportFail("nothing")
            return ic.apply_plan(run, plan, parent), parent
        except sqlite3.Error:
            if run.id in ic._cancel:
                raise ic.Cancelled()
            raise ic.ImportFail("trilium_timeout" if db is not None and db.timed_out else "bad_file")
        finally:
            if db is not None:
                db.close()
    return go


def build_plan(m, rep, title, want_tags, want_props):
    w = Walk(m, rep)
    if w.stats["hidden"]:
        rep.note("_hidden", "trilium_hidden", n=w.stats["hidden"])
    files, fkeys = {}, {}

    def add_file(k, name, size, load, path):
        if size > FILE_MAX:
            rep.skip(path or name, "too_big", mb=FILE_MAX // MB)
            return
        files[k] = {"name": name, "size": size, "path": path or name, "open": (lambda: io.BytesIO(load()))}

    nf = 0
    for kind, nid, _p, ttl, path, _r in w.entries:
        if kind not in ("page", "other"):
            continue
        note = m.notes[nid]
        if note.type in ("image", "file") and note.load is not None:
            nf += 1
            fkeys[("n", nid)] = f"f{nf}"
            add_file(f"f{nf}", _file_name(note), note.size or _size(note), note.load, path)
        for a in note.atts:
            nf += 1
            fkeys[("a", a.aid)] = f"f{nf}"
            add_file(f"f{nf}", a.title, a.size, a.load, f"{path}/{a.title}")
    root = m.notes[m.root]
    rtitle = title or (root.title if m.root not in ("root", "#zip-root") and root.title else
                       t("Trilium-Import {date}", date=i18n.fmt_date(time.time(), TZ)))
    items = [{"ref": "root", "kind": "page", "parent": None, "title": rtitle, "icon": "📥", "path": rtitle}]
    if m.root not in ("root", "#zip-root"):
        _fill(items[0], m, w, root, rtitle, fkeys, files, rep, want_tags, want_props)
    for kind, nid, pref, ttl, path, ref in w.entries:
        it = {"ref": ref, "kind": "page", "parent": pref, "title": ttl, "path": path}
        note = m.notes[nid]
        if kind == "clone":
            target = w.refs.get(nid)
            it["icon"] = "🔗"
            label = _prot_text(note.title or ttl)
            it["body"] = [{"id": "tc", "type": "paragraph", "data": {"text": f'↪ <a href="/notes#$ref:{target}">{label}</a>'}}] \
                if target else [{"id": "tc", "type": "paragraph", "data": {"text": label}}]
        elif kind == "locked":
            it["icon"] = "🔒"
            it["body"] = [{"id": "tl", "type": "callout", "data": {"emoji": "ℹ️", "text": escape(t(
                "Geschützte Trilium-Notiz – ohne Trilium-Passwort nicht übernommen."), quote=False)}}]
        else:
            _fill(it, m, w, note, ttl, fkeys, files, rep, want_tags, want_props, other=kind == "other")
        items.append(it)
    return {"items": items, "files": files}


def _size(note):
    try:
        return len(note.load())
    except (ic.ImportFail, sqlite3.Error):
        return FILE_MAX + 1


def _file_name(note):
    name = note.title or "datei"
    if "." not in name[-6:]:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "application/pdf": ".pdf"}
        name += ext.get(note.mime, "")
    return name


def _prot_text(s):
    return md2blocks._prot(escape(str(s or ""), quote=False))


def _attr_blocks(note, m, w, want_tags, want_props):
    """-> (tags, eigenschaften-block | None)."""
    tags, rows = [], []
    for typ, name, val, _pos in note.attrs:
        low = name.lower()
        if low.startswith(("label:", "relation:")) or low.startswith(("runon", "share")):
            continue
        if typ == "label":
            if low in SYS_LABELS:
                continue
            if not val:
                if want_tags:
                    tags.append(name.replace("/", "-").replace("_", " ")[:30])
                continue
            rows.append("<b>" + _prot_text(name) + ":</b> " + _prot_text(val[:500]))
        else:
            if low in SYS_RELATIONS:
                continue
            ref = w.refs.get(val)
            tgt = m.notes.get(val)
            if ref and tgt is not None:
                rows.append("<b>" + _prot_text(name) + ":</b> " + f'<a href="/notes#$ref:{ref}">{_prot_text(tgt.title or val)}</a>')
            else:
                rows.append("<b>" + _prot_text(name) + ":</b> " + _prot_text(val))
    if not want_props or not rows:
        return tags, None
    return tags, {"id": "tp", "type": "toggle", "data": {"title": escape(t("Eigenschaften"), quote=False), "open": False, "blocks": [
        {"id": "tpl", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
            {"content": r, "meta": {}, "items": []} for r in rows[:100]]}}]}}


def _fill(it, m, w, note, title, fkeys, files, rep, want_tags, want_props, other=False):
    tags, props = _attr_blocks(note, m, w, want_tags, want_props)
    if tags:
        it["tags"] = tags[:20]
    path = it.get("path") or title
    if other:
        rep.note(path, "trilium_type", type=TYPE_NAMES.get(note.type, note.type))

    def body():
        out = _content(m, w, note, title, fkeys, files, rep, path, other)
        used = {b["data"]["file"].get("url") for b in _iter(out) if b.get("type") in ("image", "attaches")
                and isinstance(b.get("data"), dict) and isinstance(b["data"].get("file"), dict)}
        for k, a in enumerate(note.atts):            # Anhaenge, die nicht im Text stehen: unten auf der Seite
            key = fkeys.get(("a", a.aid))
            if key is None or key not in files or "$file:" + key in used:
                continue
            f = {"url": "$file:" + key, "name": a.title, "is_image": m.head(key, a.load)}
            out.append({"id": f"ta{k}", "type": "image", "data": {"file": f, "caption": "", "withBorder": False, "stretched": False,
                                                                  "withBackground": False}} if f["is_image"] else
                       {"id": f"ta{k}", "type": "attaches", "data": {"file": f, "title": escape(a.title, quote=False)}})
        return ([props] if props else []) + out
    it["body"] = body


def _iter(blocks):
    for b in blocks:
        if not isinstance(b, dict):
            continue
        yield b
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        yield from _iter(d.get("blocks") or [])
        for c in d.get("cols") or []:
            if isinstance(c, dict):
                yield from _iter(c.get("blocks") or [])


def _content(m, w, note, title, fkeys, files, rep, path, other):
    def report(code, **p):
        rep.note(f"{path}: {p.get('target', '')}" if p.get("target") else path, code)

    def page(nid):
        ref = w.refs.get(nid)
        return ("page", "$ref:" + ref, m.notes[nid].title) if ref and nid in m.notes and not m.notes[nid].locked else None

    def fileref(key, name, load):
        if key is None or key not in files:
            return None
        return ("file", "$file:" + key, name, m.head(key, load))

    def att(aid):
        owner = m.att_owner.get(aid)
        a = next((x for x in m.notes[owner].atts if x.aid == aid), None) if owner else None
        return fileref(fkeys.get(("a", aid)), a.title, a.load) if a else None

    def note_file(nid):
        n = m.notes.get(nid)
        return fileref(fkeys.get(("n", nid)), _file_name(n), n.load) if n is not None else None

    def by_path(target):
        if m.kind != "zip" or not note.path:
            return None
        tgt = urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not tgt or re.match(r"^[a-z][a-z0-9+.-]*:", tgt, re.I):
            return None
        p = posixpath.normpath(posixpath.join(posixpath.dirname(note.path), tgt))
        return m.by_path.get(p)

    def link(href):
        low = href.lower()
        mm = re.search(r"#root(?:/[A-Za-z0-9_]+)*/([A-Za-z0-9_]+)", href) or re.fullmatch(r"#([A-Za-z0-9_]{4,64})", href)
        if mm:
            ma = re.search(r"attachmentId=([A-Za-z0-9_]+)", href)
            if ma:
                return att(ma.group(1))
            r = page(mm.group(1))
            if r is None:
                report("link_missing", target=href[:200])
            return r
        if low.startswith(("http://", "https://", "mailto:")):
            return ("url", href)
        hit = by_path(href)
        if hit:
            return page(hit[1]) if hit[0] == "n" else att(hit[1])
        if href and not href.startswith("#") and not re.match(r"^[a-z][a-z0-9+.-]*:", low):
            report("link_missing", target=href[:200])
        return None

    def image(src):
        ma = re.search(r"api/attachments/([A-Za-z0-9_]+)", src)
        if ma:
            return att(ma.group(1))
        mi = re.search(r"api/images/([A-Za-z0-9_]+)", src)
        if mi:
            return note_file(mi.group(1))
        hit = by_path(src)
        if hit:
            return note_file(hit[1]) if hit[0] == "n" else att(hit[1])
        return None

    def include(nid):
        return page(nid)

    if note.type in ("image", "file"):
        r = note_file(note.nid)
        if not r:
            return []
        f = {"url": r[1], "name": r[2], "is_image": r[3]}
        if r[3]:
            return [{"id": "ti", "type": "image", "data": {"file": f, "caption": "", "withBorder": False, "stretched": False,
                                                           "withBackground": False}}]
        return [{"id": "ti", "type": "attaches", "data": {"file": f, "title": escape(r[2], quote=False)}}]
    out = []
    if other:
        hint = t("Trilium-Notiztyp „{type}“ – lässt sich in Saga nicht darstellen.", type=TYPE_NAMES.get(note.type, note.type))
        src_url = next((v for typ, n, v, _ in note.attrs if typ == "label" and n == "webViewSrc"), "")
        txt = escape(hint, quote=False)
        if src_url.lower().startswith(("http://", "https://")):
            txt += f'<br><a href="{escape(src_url, quote=True)}">{escape(src_url, quote=False)}</a>'
        return [{"id": "tx", "type": "callout", "data": {"emoji": "ℹ️", "text": txt}}]
    if note.load is None:
        return out
    try:
        raw = note.load()
    except ic.ImportFail as ex:
        rep.skip(path, ex.code, **ex.p)
        return out
    txt = raw.decode("utf-8", "replace").lstrip("﻿")
    if note.type in CODE_TYPES:
        if not txt.strip():
            return out
        ctx = html2blocks.Ctx(report=report)
        blk = html2blocks.code_block(txt, note.mime or ("text/x-mermaid" if note.type == "mermaid" else ""), ctx)
        if note.type == "mermaid":
            blk["data"]["language"] = "mermaid"
        return [blk]
    if not txt.strip():
        return out
    if note.fmt == "markdown" and m.kind == "zip":
        def resolve(target, wiki):
            if target.lower().startswith(("http://", "https://", "mailto:")):
                return ("url", target)
            hit = by_path(target)
            if hit and hit[0] == "n":
                return note_file(hit[1]) if m.notes[hit[1]].type in ("image",) and fkeys.get(("n", hit[1])) else page(hit[1])
            if hit:
                return att(hit[1])
            r = link(target) if target.startswith("#") else None
            return r
        ctx = md2blocks.Ctx(resolve, lambda code, **p: report(code, **p))
        out = md2blocks.parse(txt, ctx)
    else:
        ctx = html2blocks.Ctx(link, image, include, report)
        out = html2blocks.convert(txt, ctx, full_page=m.kind == "zip")
    if out and out[0]["type"] == "header" and _plain(out[0]["data"].get("text", "")) == (note.title or "").strip():
        out = out[1:]
    return out


def _plain(h):
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", h or "")).strip()
