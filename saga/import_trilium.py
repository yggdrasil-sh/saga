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

from saga import html2blocks, i18n, md2blocks, svg_clean
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
DEPTH_MAX = 60                            # tiefer verschachtelte Notizen haengen auf dieser Ebene (Bericht)
FAILS, FAILS_PER = 5, 600                 # Fehlversuche Passwort je Konto und Zeitraum
_ID = re.compile(r"[A-Za-z0-9_]{1,64}")
_VIRTUAL = re.compile(r"(?<![A-Za-z0-9_$])virtual(?![A-Za-z0-9_$])", re.I)
BRANCHES_MAX, ATTRS_MAX = NOTES_MAX * 4, NOTES_MAX * 20     # Zeilen, die ganz in den Speicher gelesen werden
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
    "webviewsrc", "renderdisplay", "textrepresentation", "ocrtext", "filesize", "bookzoomlevel", "desktoponly", "mobileonly",
    "bookmarkfolder", "hidehighlightwidget", "printscale"}
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
        self.heads, self.orphans = {}, 0                  # orphans: verwaiste Anhaenge (zur Loeschung vorgemerkt)

    def head(self, key, load):
        """Erste Bytes eines Inhalts (Bild? auch SVG - ob es eins bleibt, entscheidet die Bereinigung beim Speichern) -
        einmal je Datei."""
        if key not in self.heads:
            try:
                b = load(1024) if load is not None else b""
            except (ic.ImportFail, OSError, sqlite3.Error, ValueError):
                b = b""
            self.heads[key] = bool(_sniff_image((b or b"")[:16])) or svg_clean.looks_like_svg(b or b"")
        return self.heads[key]

    def is_svg(self, key, load):
        try:
            b = load(1024) if load is not None else b""
        except (ic.ImportFail, OSError, sqlite3.Error, ValueError):
            return False
        return not _sniff_image((b or b"")[:16]) and svg_clean.looks_like_svg(b or b"")


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
            cap = FILE_MAX if typ in ("image", "file") else TEXT_MAX
            note.load = (lambda limit=None, p=path, cap=cap: src.read(p, cap) if limit is None else _head(src, p, limit))
            m.by_path[path] = ("n", nid)
        elif title == "[protected]":
            note.locked = True
        for k, a in enumerate(x.get("attributes") if isinstance(x.get("attributes"), list) else []):
            if isinstance(a, dict) and a.get("type") in ("label", "relation") and isinstance(a.get("name"), str):
                note.attrs.append((a["type"], a["name"][:100], str(a.get("value") or "")[:2000], a.get("position") or k,
                                   a.get("isInheritable") is True))
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
        rows = self.q("SELECT type, name, sql, rootpage FROM sqlite_master")
        if any(r[0] in ("trigger", "view") or (r[0] == "table" and (not r[3] or _VIRTUAL.search(r[2] or "")))
               for r in rows):                      # virtuell: rootpage 0 bzw. VIRTUAL irgendwo (auch hinter Kommentaren)
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
        note = TNote(nid, "", str(r[3] or "text")[:40], str(r[4] or "")[:120], "html" if (r[3] or "text") == "text" else None)
        if prot:
            plain = decrypt(key, title) if key else None
            if plain is None:
                note.locked = True
            else:
                title = plain.decode("utf-8", "replace")
        note.title = "" if note.locked else title[:500]
        bid = r[5] if db.schema == "blobs" else nid
        note.load = _db_loader(db, FILE_MAX if note.type in ("image", "file") else TEXT_MAX, bid, prot and not note.locked, key)
        if note.type in ("image", "file") and not note.locked:
            sel = "FROM blobs WHERE blobId=?" if db.schema == "blobs" else "FROM note_contents WHERE noteId=?"
            note.size = (db.q(f"SELECT {db.olen}(content) {sel}", (bid,)) or [[0]])[0][0] or 0
            if prot:
                note.size = note.size * 3 // 4
        m.notes[nid] = note
    m.root = "root"
    pre = "substr(prefix, 1, 200)" if "prefix" in db.cols["branches"] else "''"
    if db.q("SELECT COUNT(*) FROM branches WHERE isDeleted=0")[0][0] > BRANCHES_MAX:
        raise ic.ImportFail("trilium_many", n=NOTES_MAX)
    for cid, pid, pos, prefix in db.q(f"SELECT noteId, parentNoteId, notePosition, {pre} FROM branches WHERE isDeleted=0"):
        if pid in m.notes and cid in m.notes and cid != pid:
            m.notes[pid].kids.append((cid, prefix if isinstance(prefix, str) else "", pos if isinstance(pos, (int, float)) else 0, False))
    for note in m.notes.values():
        note.kids.sort(key=lambda k: (k[2], k[0]))
    ac = db.cols.get("attributes", set())
    if {"noteId", "type", "name", "value", "isDeleted"} <= ac:
        posc = "position" if "position" in ac else "0"
        inhc = "isInheritable" if "isInheritable" in ac else "0"
        if db.q("SELECT COUNT(*) FROM attributes WHERE isDeleted=0")[0][0] > ATTRS_MAX:
            raise ic.ImportFail("trilium_many", n=NOTES_MAX)
        for nid, typ, name, val, pos, inh in db.q(f"SELECT noteId, type, substr(name, 1, 100), substr(value, 1, 2000), {posc}, "
                                                  f"{inhc} FROM attributes WHERE isDeleted=0"):
            if nid in m.notes and typ in ("label", "relation") and isinstance(name, str):
                m.notes[nid].attrs.append((typ, name[:100], str(val or "")[:2000], pos or 0, bool(inh)))
    tc = db.cols.get("attachments", set())
    if db.schema == "blobs" and {"attachmentId", "ownerId", "role", "mime", "title", "blobId", "isDeleted"} <= tc:
        protc = "isProtected" if "isProtected" in tc else "0"
        posc = "position" if "position" in tc else "0"
        # zur Loeschung vorgemerkt (in Trilium unsichtbar, nicht mehr im Text): nicht uebernehmen; aeltere Schemata ohne
        # die Spalte: alle
        erc = "utcDateScheduledForErasureSince" if "utcDateScheduledForErasureSince" in tc else "NULL"
        for aid, owner, role, mime, title, bid, prot, pos, erase in db.q(
                f"SELECT attachmentId, ownerId, role, mime, title, blobId, {protc}, {posc}, {erc} FROM attachments WHERE isDeleted=0"):
            if owner not in m.notes or not isinstance(aid, str) or not _ID.fullmatch(aid):
                continue
            if erase:
                if not owner.startswith("_"):
                    m.orphans += 1
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
                                  _db_loader(db, FILE_MAX, bid, bool(prot), key), "", pos or 0))
            m.att_owner[aid] = owner
    for note in m.notes.values():
        note.attrs.sort(key=lambda a: a[3])
        note.atts.sort(key=lambda a: a.pos)
    return m


def _db_loader(db, cap, bid, prot, key):
    """Lader fuer einen Inhalt (hoechstens cap Bytes, sonst ImportFail too_big): load(limit=None) -> Bytes (limit: nur
    die ersten Bytes, Kopf fuers Erkennen)."""
    if db.schema == "blobs":
        sel, args = "FROM blobs WHERE blobId=?", (bid,)
    else:
        sel, args = "FROM note_contents WHERE noteId=?", (bid,)

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
    """Baum ab der Wurzel in Trilium-Reihenfolge -> Eintraege (art, nid, parent_ref, titel, pfad, ref); kids_of: ref der
    Elternseite -> [(ref, nid, titel, art)] in Reihenfolge (Unterseiten-Uebersicht); parent: nid -> nid der Elternnotiz
    an der ersten Stelle (Vererbung von Attributen)."""

    def __init__(self, m, rep=None):
        self.m, self.rep = m, rep
        self.refs, self.entries, self.kids_of, self.parent = {}, [], {}, {}
        self.stats = {"pages": 0, "code": 0, "files": 0, "protected": 0, "clones": 0, "unsupported": 0, "hidden": 0}
        seen = {m.root}
        self.refs[m.root] = "root"
        stack = [(c, "root", "", k, 1, m.root) for k, c in reversed(list(enumerate(m.notes[m.root].kids)))]
        n = 0
        while stack:
            (cid, prefix, _pos, clone), pref, ppath, k, depth, pnid = stack.pop()
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
                self.kids_of.setdefault(pref, []).append((f"k{n}", cid, title, "clone"))
                continue
            self.parent.setdefault(cid, pnid)
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
                self.kids_of.setdefault(pref, []).append((ref, cid, t("Geschützte Notiz"), "locked"))
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
                self.stats["files"] += len(content_atts(note))
                self.entries.append((kind, cid, pref, title, path, ref))
                self.kids_of.setdefault(pref, []).append((ref, cid, title, kind))
            if depth == DEPTH_MAX and note.kids and rep is not None:
                rep.note(path, "trilium_depth")
            for j, c in reversed(list(enumerate(note.kids))):     # zu tief: Kinder auf derselben Ebene wie die Notiz
                stack.append((c, ref if depth < DEPTH_MAX else pref, path if depth < DEPTH_MAX else ppath, j, depth + 1, cid))
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
        try:
            db = _DB(path, TIME_ANALYZE)
        except sqlite3.Error:
            raise ic.ImportFail("bad_file")
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
        except (sqlite3.Error, ValueError, MemoryError):
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
            if len(plan["items"]) <= 1 and (plan.get("synthetic") or not plan["items"][0].get("body")):
                raise ic.ImportFail("nothing")
            root_id = ic.apply_plan(run, plan, parent)
            finish(run, plan, root_id)                 # Bericht unter die Sammelseite, Info-Kasten mit Link
            return root_id, parent
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
        rep.note("", "trilium_hidden", n=w.stats["hidden"])
    if m.orphans:
        rep.note("", "trilium_orphans", n=m.orphans)
    files, fkeys = {}, {}

    def add_file(k, name, size, load, path):
        if size > FILE_MAX:
            rep.skip(path or name, "too_big", mb=FILE_MAX // MB)
            return
        # svg: darf (nur bereinigt, saga/svg_clean.py) als Bild gespeichert werden - Rasterbilder/Dateien bleiben, wie sie sind
        files[k] = {"name": name, "size": size, "path": path or name, "open": (lambda: io.BytesIO(load())), "svg": True}

    nf, dropped, colors = 0, 0, 0
    icons = {}
    for kind, nid, _p, ttl, path, ref in w.entries:
        if kind not in ("page", "other"):
            continue
        note = m.notes[nid]
        icons[ref] = _icon(m, w, nid)
        colors += sum(1 for a in note.attrs if a[0] == "label" and a[1] == "color" and a[2])
        if note.type in ("image", "file") and note.load is not None:
            nf += 1
            fkeys[("n", nid)] = f"f{nf}"
            add_file(f"f{nf}", _file_name(note), note.size or _size(note), note.load, path)
        dropped += sum(1 for a in note.atts if a.role not in ("image", "file"))
        for a in content_atts(note):
            nf += 1
            fkeys[("a", a.aid)] = f"f{nf}"
            add_file(f"f{nf}", a.title, a.size, a.load, f"{path}/{a.title}")
    if dropped:
        rep.note("", "trilium_viewcfg", n=dropped)
    if colors:
        rep.note("", "trilium_color", n=colors)
    root = m.notes[m.root]
    synthetic = m.root in ("root", "#zip-root")
    rtitle = title or (root.title if not synthetic and root.title else
                       t("Trilium-Import {date}", date=i18n.fmt_date(time.time(), TZ)))
    items = [{"ref": "root", "kind": "page", "parent": None, "title": rtitle, "icon": "📥", "path": rtitle}]
    if synthetic:                                 # ganze Datenbank: Liste der obersten Seiten (Info-Kasten folgt nach dem Lauf)
        items[0]["body"] = lambda: _overview(m, w, None, "root", icons, rep, rtitle)
    else:
        _fill(items[0], m, w, root, rtitle, fkeys, files, rep, want_tags, want_props, ref="root", icons=icons)
        if _label(m, w, m.root, "fullContentWidth") is not None:
            items[0]["wide"] = True
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
            if icons.get(ref):
                it["icon"] = icons[ref]
            if _label(m, w, nid, "fullContentWidth") is not None:
                it["wide"] = True
            _fill(it, m, w, note, ttl, fkeys, files, rep, want_tags, want_props, other=kind == "other", ref=ref, icons=icons)
        items.append(it)
    return {"items": items, "files": files, "synthetic": synthetic}


def content_atts(note):
    """Anhaenge, die Inhalt sind (Bild/Datei). Nicht: viewConfig u. ae. (Ansichtseinstellungen einer Sammlung), bei
    Mermaid die gespeicherte Vorschau (Saga zeichnet das Diagramm selbst)."""
    if note.type == "mermaid":
        return []
    return [a for a in note.atts if a.role in ("image", "file")]


# =====================================================================================================
# Icons, Attribute ueber Vorlagen/Vererbung
# =====================================================================================================
# Boxicons-Klasse (ohne bx/bxs/bxl-Praefix) -> Emoji (Saga-Seitensymbol); unbekannt -> Typ-Standard bzw. keins
BOXICONS = {
    "dashboard": "📊", "list-ul": "📋", "list-ol": "📋", "list-check": "✅", "folder": "📁", "folder-open": "📂",
    "note": "📝", "notepad": "📝", "edit": "📝", "edit-alt": "📝", "pencil": "✏️", "book": "📖", "book-open": "📖",
    "book-bookmark": "📖", "book-content": "📖", "library": "📚", "bookmark": "🔖", "bookmarks": "🔖", "code": "💻",
    "code-alt": "💻", "code-block": "💻", "code-curly": "💻", "terminal": "💻", "calendar": "📅", "calendar-star": "📅",
    "calendar-event": "📅", "calendar-check": "📅", "calendar-alt": "📅", "star": "⭐", "bug": "🐛", "server": "🖥️",
    "desktop": "🖥️", "laptop": "💻", "chip": "🔌", "plug": "🔌", "cloud": "☁️", "lock": "🔒", "lock-alt": "🔒", "key": "🔑",
    "link": "🔗", "link-alt": "🔗", "image": "🖼️", "image-alt": "🖼️", "photo-album": "🖼️", "bulb": "💡",
    "check-square": "✅", "check-circle": "✅", "task": "✅", "home": "🏠", "home-alt": "🏠", "shield": "🛡️",
    "shield-quarter": "🛡️", "shield-alt": "🛡️", "shield-alt-2": "🛡️", "wrench": "🔧", "cog": "⚙️", "user": "👤",
    "group": "👥", "envelope": "✉️", "data": "🗄️", "network-chart": "🕸️", "wifi": "📶", "globe": "🌐", "world": "🌐",
    "map": "🗺️", "map-alt": "🗺️", "table": "📊", "grid": "🔲", "grid-alt": "🔲", "search": "🔎", "file": "📄",
    "file-blank": "📄", "detail": "📄", "trash": "🗑️", "trash-alt": "🗑️", "history": "🕓", "time": "🕓", "send": "📨",
    "slideshow": "🎞️", "bar-chart": "📊", "bar-chart-alt-2": "📊", "pie-chart": "📊", "line-chart": "📈", "music": "🎵",
    "camera": "📷", "video": "🎬", "heart": "❤️", "flag": "🚩", "rocket": "🚀", "game": "🎮", "joystick": "🕹️",
    "car": "🚗", "cart": "🛒", "dollar": "💲", "wallet": "👛", "chat": "💬", "message": "💬", "message-square-dots": "💬",
    "phone": "📞", "printer": "🖨️", "hdd": "💾", "save": "💾", "box": "📦", "package": "📦", "archive": "🗃️",
    "brain": "🧠", "palette": "🎨", "paint": "🎨", "docker": "🐳", "tux": "🐧", "linux": "🐧", "windows": "🪟",
    "github": "🐙", "apple": "🍎", "android": "🤖", "python": "🐍"}
TYPE_ICON = {"book": "📚", "code": "💻", "mermaid": "📈", "canvas": "🎨", "relationMap": "🕸️", "search": "🔎",
             "mindMap": "🧠", "webView": "🌐", "geoMap": "🗺️"}


def _boxicon(v):
    """'bx bx-calendar' / 'bx bxs-dashboard' / 'bx bxl-docker' -> Emoji oder None."""
    for tok in reversed(str(v or "").split()):
        mm = re.fullmatch(r"bx[sl]?-([a-z0-9-]{1,60})", tok.strip().lower())
        if mm:
            return BOXICONS.get(mm.group(1))
    return None


def _attr_tpl(m, nid, name, inh_only, depth=0):
    """Label name an der Notiz (nur vererbbare, wenn inh_only) oder an ihren Vorlagen (relation:template, auch im
    versteckten Baum) -> Wert oder None."""
    note = m.notes.get(nid)
    if note is None or depth > 4:
        return None
    for a in note.attrs:
        if a[0] == "label" and a[1] == name and (a[4] or not inh_only):
            return a[2]
    for a in note.attrs:
        if a[0] == "relation" and a[1] == "template" and a[2] != nid:
            v = _attr_tpl(m, a[2], name, inh_only, depth + 1)
            if v is not None:
                return v
    return None


def _label(m, w, nid, name):
    """Wie Trilium: eigenes Label -> Vorlage -> vererbbares Label eines Vorfahren (erste Stelle im Baum)."""
    v = _attr_tpl(m, nid, name, False)
    if v is not None:
        return v
    p, k = w.parent.get(nid), 0
    while p is not None and p in m.notes and k < DEPTH_MAX:
        v = _attr_tpl(m, p, name, True)
        if v is not None:
            return v
        p, k = w.parent.get(p), k + 1
    return None


def _icon(m, w, nid):
    return _boxicon(_label(m, w, nid, "iconClass")) or TYPE_ICON.get(m.notes[nid].type)


# =====================================================================================================
# Unterseiten-Uebersicht (Sammlungen, leere Ordner, Sammelseite) - statisch beim Import erzeugt
# =====================================================================================================
EXCERPT = 140
LIST_VIEWS = ("list",)
CARD_VIEWS = ("grid", "dashboard", "")


def _excerpt(m, cid):
    """Erste Zeichen Klartext einer Kindseite (Code: Sprache) fuer die Karten."""
    n = m.notes.get(cid)
    if n is None or n.locked:
        return ""
    if n.type in CODE_TYPES:
        return "Mermaid" if n.type == "mermaid" else (html2blocks.lang_of_mime(n.mime) or "").upper() or t("Code")
    if n.type != "text" or n.load is None:
        return ""
    try:
        txt = n.load().decode("utf-8", "replace").lstrip("﻿")
    except (ic.ImportFail, sqlite3.Error, OSError, ValueError):
        return ""
    if n.fmt == "markdown" and m.kind == "zip":
        plain = re.sub(r"!\[[^\]]*\]\([^)]*\)|[#*_`>|]+|\[([^\]]*)\]\([^)]*\)", lambda x: x.group(1) or " ", txt)
    else:
        plain = (html2blocks.content_root(txt) if m.kind == "zip" else html2blocks.parse_tree(txt)).text()
    plain = " ".join(plain.split())
    if n.title and plain.startswith(n.title):
        plain = plain[len(n.title):].lstrip(" :–-")
    return plain[:EXCERPT].rstrip() + ("…" if len(plain) > EXCERPT else "")


def _dash_order(m, note):
    """Dashboard: Position der Kinder aus der viewConfig (widgets: {noteId: {x, y}}) -> {nid: (y, x)}."""
    a = next((x for x in note.atts if x.role == "viewConfig"), None) if note is not None else None
    if a is None or a.size > MB:
        return {}
    try:
        d = json.loads(a.load().decode("utf-8", "replace"))
    except (ValueError, UnicodeError, RecursionError, ic.ImportFail, sqlite3.Error, OSError):
        return {}
    ws = d.get("widgets") if isinstance(d, dict) else None
    out = {}
    for k, v in (ws.items() if isinstance(ws, dict) else []):
        if isinstance(v, dict) and isinstance(v.get("y"), (int, float)) and isinstance(v.get("x"), (int, float)):
            out[k] = (v["y"], v["x"])
    return out


def _overview(m, w, note, ref, icons, rep, path):
    """Unterseiten als Bloecke: Sammlung (book) nach viewType - grid/dashboard -> Karten (columns + callout je Seite,
    Dashboard in der Anordnung der viewConfig), list -> Liste mit Auszug, andere Ansichten (calendar, table, board ...)
    -> Liste + Hinweis; leere Ordnerseite / Sammelseite (note None oder kein book) -> Liste der Seitenlinks."""
    kids = w.kids_of.get(ref) or []
    if not kids:
        return []
    book = note is not None and note.type == "book"
    view = (_label(m, w, note.nid, "viewType") or "grid").strip() if book else "list"
    entries = []
    for i, (kref, cid, ttl, kind) in enumerate(kids):
        target = w.refs.get(cid) if kind == "clone" else kref
        label = _prot_text(ttl or "…")
        link = f'<a href="/notes#$ref:{target}">{label}</a>' if target else label
        icon = ("🔒" if kind == "locked" else icons.get(target)) or "📄"
        ex = _excerpt(m, cid) if book and kind != "locked" else ""
        entries.append((i, cid, link, icon, ex))
    if book and view == "dashboard":
        pos = _dash_order(m, note)
        entries.sort(key=lambda e: (0, *pos[e[1]], e[0]) if e[1] in pos else (1, 0, 0, e[0]))
    if book and view in CARD_VIEWS:
        cards = [{"id": f"tv{k}", "type": "callout", "data": {"emoji": icon, "text": f"<b>{link}</b>" + (
            "<br>" + _prot_text(ex) if ex else "")}} for k, (_i, _c, link, icon, ex) in enumerate(entries)]
        if len(cards) == 1:
            return cards
        ncol = min(3, len(cards))
        cols = [{"blocks": []} for _ in range(ncol)]
        for k, c in enumerate(cards):                       # reihum: Lesereihenfolge zeilenweise wie im Raster
            cols[k % ncol]["blocks"].append(c)
        return [{"id": "tvc", "type": "columns", "data": {"cols": cols}}]
    out = []
    if book and view not in LIST_VIEWS:
        rep.note(path, "trilium_view", view=view[:40])
        out.append({"id": "tvh", "type": "callout", "data": {"emoji": "ℹ️", "text": escape(t(
            "Trilium-Ansicht „{view}“ – hier als Liste der Unterseiten übernommen.", view=view[:40]), quote=False)}})
    out.append({"id": "tvl", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
        {"content": link + (" – " + _prot_text(ex) if ex else ""), "meta": {}, "items": []} for _i, _c, link, _ic, ex in entries]}})
    return out


def finish(run, plan, root_id):
    """Nach dem Einspielen: Importbericht als letzte Unterseite der Sammelseite und ein Info-Kasten auf der Sammelseite
    (ganze Datenbank: oben, exportierter Teilbaum mit eigenem Inhalt: unten) mit Zahlen und Link zum Bericht."""
    from db import connect
    from saga import importer
    if root_id is None:
        return
    rid = importer._report_page(run, root_id) if run.report.items else None
    cnt = run.report.counts
    txt = escape(t("Aus Trilium importiert am {date} – {pages} Seiten, {files} Dateien.", date=i18n.fmt_date(time.time(), TZ),
                   pages=max(0, cnt.get("pages", 0) - 1), files=cnt.get("files", 0)), quote=False)
    if rid:
        txt += f' <a href="/notes#{int(rid)}">{escape(t("Importbericht"), quote=False)}</a>'
    blk = {"id": "trinfo", "type": "callout", "data": {"emoji": "ℹ️", "text": txt}}
    with connect() as c, c:
        row = c.execute("SELECT body FROM note WHERE id=? AND uid=?", (root_id, run.uid)).fetchone()
        try:
            blocks = json.loads(row[0])["blocks"] if row and row[0] else []
        except (ValueError, KeyError, TypeError):
            blocks = []
        blocks = [blk] + blocks if plan.get("synthetic") else blocks + [blk]
        c.execute("UPDATE note SET body=? WHERE id=? AND uid=?", (ic.clean_body(c, run.uid, blocks, ic.Report(), ""), root_id, run.uid))


def _size(note):
    try:
        return len(note.load())
    except (ic.ImportFail, sqlite3.Error):
        return FILE_MAX + 1


def _file_name(note):
    name = note.title or "datei"
    if "." not in name[-6:]:
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "application/pdf": ".pdf",
               "image/svg+xml": ".svg"}
        name += ext.get(note.mime, "")
    return name


def _prot_text(s):
    return md2blocks._prot(escape(str(s or ""), quote=False))


def _attr_blocks(note, m, w, want_tags, want_props):
    """-> (tags, eigenschaften-block | None)."""
    tags, rows = [], []
    for typ, name, val, _pos, _inh in note.attrs:
        low = name.lower()
        if low.startswith(("label:", "relation:", "child:", "runon", "share")):
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


_FILE_KEY = re.compile(r"\$file:([A-Za-z0-9_.-]{1,80})")


def _fill(it, m, w, note, title, fkeys, files, rep, want_tags, want_props, other=False, ref=None, icons=None):
    tags, props = _attr_blocks(note, m, w, want_tags, want_props)
    if tags:
        it["tags"] = tags[:20]
    path = it.get("path") or title

    def body():
        out = _content(m, w, note, title, fkeys, files, rep, path, other)
        atts = content_atts(note)
        if ref is not None and (note.type == "book" or (not out and not atts and note.type == "text")):
            out = out + _overview(m, w, note, ref, icons or {}, rep, path)     # Sammlung bzw. leere Ordnerseite
        used = set(_FILE_KEY.findall(json.dumps(out)))       # Bild/Datei-Block oder Datei-Link im Text: steht schon da
        for k, a in enumerate(atts):                 # Anhaenge, die nicht im Text stehen: unten auf der Seite
            key = fkeys.get(("a", a.aid))
            if key is None or key not in files or key in used:
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
        return _other(m, note, fkeys, files, rep, path)
    if note.type == "book" or note.load is None:      # Sammlung: Inhalt ist keine Seite (JSON o. ae.) -> Uebersicht (_fill)
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
            blk["data"]["view"] = "preview"          # Diagramm zeigen (wie Trilium); die Vorschau-SVG faellt weg
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
        out = md2blocks.parse(_md_images_apart(txt), ctx)
    else:
        ctx = html2blocks.Ctx(link, image, include, report)
        out = html2blocks.convert(txt, ctx, full_page=m.kind == "zip")
    if out and out[0]["type"] == "header" and _plain(out[0]["data"].get("text", "")) == (note.title or "").strip():
        out = out[1:]
    return out


PREVIEW_TYPES = ("canvas", "relationMap", "mindMap")


def _other(m, note, fkeys, files, rep, path):
    """Nicht abbildbarer Notiztyp -> Hinweis. Gespeicherte Suche: Suchtext; Zeichenflaeche/Relationskarte/Mind-Map:
    gespeichertes Vorschaubild (Anhang role image) ueber dem Hinweis; leere Zeichenflaeche: eigener Hinweis."""
    name = TYPE_NAMES.get(note.type, note.type)
    if note.type == "search":
        q = next((v for typ, n, v, _p, _i in note.attrs if typ == "label" and n == "searchString"), "")
        txt = escape(t("Gespeicherte Suche aus Trilium"), quote=False)
        if q:
            txt += ': <code class="inline-code">' + _prot_text(q[:500]) + "</code>"
        rep.note(path, "trilium_type", type=name)
        return [{"id": "tx", "type": "callout", "data": {"emoji": "🔎", "text": txt}}]
    out = []
    if note.type in PREVIEW_TYPES:
        for k, a in enumerate(x for x in content_atts(note) if x.role == "image"):
            key = fkeys.get(("a", a.aid))
            if key is None or key not in files:
                continue
            f = {"url": "$file:" + key, "name": a.title, "is_image": m.head(key, a.load)}
            if f["is_image"]:
                out.append({"id": f"tv{k}", "type": "image", "data": {"file": f, "caption": "", "withBorder": False,
                                                                      "stretched": False, "withBackground": False}})
    if out:
        hint = t("{type} aus Trilium – in Saga nur als Vorschaubild verfügbar.", type=name)
        rep.note(path, "trilium_preview", type=name)
    elif note.type == "canvas" and _empty_canvas(note):
        hint = t("Leere Zeichenfläche aus Trilium.")
        rep.note(path, "trilium_type", type=name)
    else:
        hint = t("Trilium-Notiztyp „{type}“ – lässt sich in Saga nicht darstellen.", type=name)
        rep.note(path, "trilium_type", type=name)
    src_url = next((v for typ, n, v, _p, _i in note.attrs if typ == "label" and n == "webViewSrc"), "")
    txt = escape(hint, quote=False)
    if src_url.lower().startswith(("http://", "https://")):
        txt += f'<br><a href="{escape(src_url, quote=True)}">{escape(src_url, quote=False)}</a>'
    return out + [{"id": "tx", "type": "callout", "data": {"emoji": "ℹ️", "text": txt}}]


def _empty_canvas(note):
    """Canvas ohne Inhalt (leer bzw. {"elements": []} ohne sichtbare Elemente)."""
    if note.load is None:
        return True
    try:
        raw = note.load()
    except (ic.ImportFail, sqlite3.Error, OSError):
        return False
    if not raw.strip():
        return True
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return False
    els = d.get("elements") if isinstance(d, dict) else None
    return isinstance(els, list) and not any(isinstance(e, dict) and not e.get("isDeleted") for e in els)


_MD_IMG = re.compile(r"[ \t]*(!\[[^\]\n]{0,500}\]\([^)\s]{1,1000}\))[ \t]*")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _md_images_apart(txt):
    """Bild mitten in einer Zeile (Trilium-Export: "Vorher ![](bild.png) nachher") -> eigene Zeile, damit es an seiner
    Stelle ein Bild-Block wird (md2blocks kennt Bilder nur als eigene Zeile). Codebloecke bleiben unberuehrt."""
    out, fence = [], None
    for ln in txt.split("\n"):
        m = _FENCE.match(ln)
        if fence is None and m:
            fence = m.group(1)[0]
        elif fence is not None and m and m.group(1)[0] == fence:
            fence = None
        elif fence is None and "![" in ln and not _MD_IMG.fullmatch(ln) and not ln.lstrip().startswith(("|", ">", "-", "*", "+")) \
                and not re.match(r"^\s*\d+[.)]\s", ln):
            ln = _MD_IMG.sub(lambda x: "\n\n" + x.group(1) + "\n\n", ln).strip("\n")
        out.append(ln)
    return "\n".join(out)


def _plain(h):
    from html import unescape
    return unescape(re.sub(r"<[^>]+>", "", h or "")).strip()
