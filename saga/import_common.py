"""Gemeinsamer Unterbau der Importe (2.5, Paket 2 E2): Upload-Ablage mit Token, Laeufe (Tabelle import_run) im
Hintergrund mit Fortschritt, ZIP-Pruefer, Bericht "nicht uebernommen" und Einspielen des Zwischenformats.

Zwischenformat (wie das Galerie-Bundle, intern): Plan = {"items": [...], "files": {...}}. Ein Item:
  {ref, kind: "page"|"database", parent: ref|None, title, icon, tags, body: callable() -> Bloecke | Liste | None,
   schema (nur Datenbank; Relation: target "$ref:x"), rows: callable() -> Iterator[{title, props, ref?}] (Datenbank),
   row_count: int}
Bloecke duerfen "$ref:x" (Seitenverweis /notes#$ref:x, Datenbank-Block db) und "$file:k" (Datei aus files) enthalten.
files: {k: {name, size, open: callable() -> Datei-Objekt}} - Inhalte werden erst beim Einspielen gelesen (Speicher).
Einspielen in Etappen (CHUNK Seiten je Transaktion); Fehler/Abbruch: alles, was der Lauf angelegt hat, wandert in den
Papierkorb, geschriebene Dateien werden geloescht. Validierung immer ueber clean_blocks/_clean_schema/_clean_props."""
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
import unicodedata
import zipfile

from db import connect as _db
from saga import i18n
from saga.core import E, STATE_DIR, _json_load, account_lang, t
from saga.notes import (FILE_MAX, FILE_QUOTA, FILES_DIR, NOTE_MAX_COUNT, NOTE_ROW_MAX_COUNT, OPT_COLORS, _clean_props,
                        _clean_schema, _schema_stamp, tree_changed)

MB = 1024 * 1024
IMPORT_MAX = max(1, int(E.get("IMPORT_MAX_MB", "512"))) * MB                 # Upload je Token (alle Teile zusammen)
UNPACKED_MAX = max(1, int(E.get("IMPORT_UNPACKED_MAX_MB", "2048"))) * MB      # Summe entpackt (ZIP)
ENTRIES_MAX = 50_000                                                          # Eintraege in einem ZIP
RATIO_MAX = 200                                                               # entpackt/gepackt je Eintrag (ZIP-Bombe)
PARTS_MAX = 2000                                                              # Einzeldateien je Upload (Ordner ohne ZIP)
PARALLEL = max(1, int(E.get("IMPORT_PARALLEL", "2")))                         # gleichzeitige Laeufe je Instanz
UPLOAD_TTL = 1800                                                             # Upload ohne Lauf: so lange
CHUNK = 500                                                                   # Seiten je Transaktion
REPORT_MAX = 2000
RUNS_KEEP = 20
TMP_DIR = os.path.join(STATE_DIR, "import-tmp")
STEP_EVERY = 0.4

_uploads = {}                     # token -> {uid, dir, files: [{name, path, size}], size, until, busy}
_lock = threading.Lock()
_sem = threading.BoundedSemaphore(PARALLEL)
_cancel = set()                   # run_ids mit Abbruchwunsch


class ImportFail(Exception):
    """Fehler, der den ganzen Lauf beendet (Text fuer die Person: code + Parameter, siehe reason_text)."""

    def __init__(self, code, **p):
        super().__init__(code)
        self.code, self.p = code, p


class Cancelled(Exception):
    pass


# =====================================================================================================
# Texte (Codes werden gespeichert, Text erst bei der Ausgabe in der Sprache der Anfrage)
# =====================================================================================================
def reason_text(code, p=None):
    p = dict(p or {})
    m = {
        "too_big": lambda: t("Datei zu groß (max. {n} MB)", n=p.get("mb", FILE_MAX // MB)),
        "unsupported": lambda: t("Dateityp nicht unterstützt"),
        "bad_path": lambda: t("Pfad unzulässig (absolut, „..“ oder Steuerzeichen)"),
        "symlink": lambda: t("Verknüpfung (Symlink) – nicht übernommen"),
        "bomb": lambda: t("Verdächtig stark komprimiert – nicht übernommen"),
        "nested_zip": lambda: t("ZIP im ZIP – nicht entpackt"),
        "link_missing": lambda: t("Verweis-Ziel nicht im Export"),
        "bad_value": lambda: t("Wert passt nicht zum Typ ({type}) – Feld leer gelassen", type=p.get("type", "")),
        "too_many_cols": lambda: t("Mehr als 30 Spalten – Rest nicht übernommen"),
        "too_many_opts": lambda: t("Mehr als 50 Optionen – als Text übernommen"),
        "truncated": lambda: t("Zu lang – gekürzt"),
        "blocks_limit": lambda: t("Blockgrenze erreicht – Seite gekürzt"),
        "rows_limit": lambda: t("Mehr als {n} Zeilen – Rest nicht übernommen", n=p.get("n", "")),
        "page_limit": lambda: t("Seitengrenze des Kontos erreicht"),
        "quota": lambda: t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // MB),
        "rel_missing": lambda: t("Verknüpfter Eintrag „{title}“ nicht gefunden", title=p.get("title", "")),
        "ext_image": lambda: t("Externes Bild nicht heruntergeladen (als Link behalten)"),
        "embed_note": lambda: t("Einbettung einer Notiz als Link übernommen"),
        "ignored": lambda: t("Ignoriert"),
        "bad_file": lambda: t("Datei nicht lesbar"),
        "csv_error": lambda: t("CSV fehlerhaft ab Zeile {n}", n=p.get("n", "")),
        "empty": lambda: t("Leere Datei"),
        "not_zip": lambda: t("Keine gültige ZIP-Datei."),
        "zip_entries": lambda: t("Zu viele Einträge im ZIP (höchstens {n}).", n=ENTRIES_MAX),
        "zip_size": lambda: t("Entpackt zu groß (höchstens {n} MB).", n=UNPACKED_MAX // MB),
        "busy": lambda: t("Es läuft schon ein Import."),
        "restart": lambda: t("Server wurde neu gestartet – Import abgebrochen."),
        "cancelled": lambda: t("Abgebrochen."),
        "failed": lambda: t("Import fehlgeschlagen."),
        "no_target": lambda: t("Ziel nicht gefunden."),
        "nothing": lambda: t("Nichts zu importieren."),
        "notes_limit": lambda: t("Zu viele Seiten für dieses Konto (höchstens {n}).", n=p.get("n", NOTE_MAX_COUNT)),
        "rows_account": lambda: t("Zu viele Datenbank-Einträge für dieses Konto (höchstens {n}).", n=p.get("n", NOTE_ROW_MAX_COUNT)),
        "formula": lambda: t("Formel als Text übernommen"),
    }
    f = m.get(code)
    return f() if f else code


# =====================================================================================================
# Bericht
# =====================================================================================================
class Report:
    """Zaehler und Liste "nicht uebernommen" (hoechstens REPORT_MAX Eintraege, Rest nur gezaehlt)."""

    def __init__(self):
        self.counts = {"pages": 0, "databases": 0, "rows": 0, "files": 0, "skipped": 0}
        self.items, self.more, self.page = [], 0, None

    def skip(self, path, code, **p):
        self.counts["skipped"] += 1
        self.note(path, code, **p)

    def note(self, path, code, **p):
        """Hinweis ohne "uebersprungen" (z. B. Wert leer gelassen)."""
        if len(self.items) >= REPORT_MAX:
            self.more += 1
            return
        self.items.append({"path": str(path or "")[:300], "code": code, "p": {k: str(v)[:200] for k, v in p.items()}})

    def to_json(self):
        return {"counts": self.counts, "items": self.items, "more": self.more, "page": self.page}


def report_out(raw):
    """Gespeicherter Bericht -> fuer die Antwort (Texte in der Sprache der Anfrage, gruppiert nach Grund)."""
    d = _json_load(raw, {}) if isinstance(raw, str) else (raw or {})
    groups = {}
    for it in d.get("items") or []:
        txt = reason_text(it.get("code"), it.get("p"))
        g = groups.setdefault((it.get("code"), txt), {"code": it.get("code"), "text": txt, "items": []})
        g["items"].append({"path": it.get("path", ""), "text": txt})
    return {"counts": d.get("counts") or {}, "groups": list(groups.values()), "more": d.get("more", 0), "page": d.get("page")}


# =====================================================================================================
# Uploads (Token je Konto, Ablage auf dem Daten-Volume)
# =====================================================================================================
def _now():
    return time.time()


def upload_dir():
    os.makedirs(TMP_DIR, mode=0o700, exist_ok=True)
    return TMP_DIR


def safe_part_name(name):
    """Dateiname eines Teils (Ordner-Upload: relativer Pfad) - normalisiert; None bei unzulaessigem Pfad."""
    return norm_path(name)


def new_upload(uid):
    tok = secrets.token_urlsafe(18)
    d = os.path.join(upload_dir(), tok)
    os.makedirs(d, mode=0o700)
    with _lock:
        _uploads[tok] = {"uid": uid, "dir": d, "files": [], "size": 0, "until": _now() + UPLOAD_TTL, "busy": False}
    return tok


def get_upload(uid, tok):
    with _lock:
        u = _uploads.get(tok) if isinstance(tok, str) else None
        if not u or u["uid"] != uid or u["until"] < _now():
            return None
        u["until"] = max(u["until"], _now() + UPLOAD_TTL)
        return u


def drop_upload(tok):
    with _lock:
        u = _uploads.pop(tok, None)
    if u:
        shutil.rmtree(u["dir"], ignore_errors=True)


def cleanup():
    """Abgelaufene Uploads und verwaiste Ordner (nach Neustart) entfernen -> Anzahl."""
    n = 0
    with _lock:
        old = [k for k, v in _uploads.items() if v["until"] < _now() and not v["busy"]]
        live = {v["dir"] for v in _uploads.values()}
    for k in old:
        drop_upload(k)
        n += 1
    try:
        for f in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, f)
            if p not in live and os.path.getmtime(p) < _now() - UPLOAD_TTL:
                shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                n += 1
    except OSError:
        pass
    return n


# =====================================================================================================
# Pfade und ZIP-Pruefer
# =====================================================================================================
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
CD_MAX = ENTRIES_MAX * 400        # Zentralverzeichnis eines ZIP (~19 MB): ENTRIES_MAX Eintraege mit im Mittel ~350 Zeichen Pfad


def zip_too_big_cd(path):
    """True, wenn das Zentralverzeichnis groesser als CD_MAX ist - VOR zipfile.ZipFile pruefen: das legt je Eintrag ein
    Objekt an (1 Mio. Mini-Eintraege in 53 MB = ~600 MB Speicher), bevor ENTRIES_MAX greifen kann."""
    try:
        with open(path, "rb") as f:
            end = zipfile._EndRecData(f)
    except OSError:
        return False
    return bool(end) and end[zipfile._ECD_SIZE] > CD_MAX


def norm_path(name):
    """ZIP-/Upload-Pfad -> normalisierter relativer Pfad (NFC, "/") oder None, wenn unzulaessig."""
    if not isinstance(name, str) or not name or len(name) > 1000:
        return None
    s = unicodedata.normalize("NFC", name.replace("\\", "/"))
    if _CTRL.search(s) or s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        return None
    parts = [x for x in s.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        return None
    return "/".join(parts)


class Entry:
    __slots__ = ("path", "size", "info", "file")

    def __init__(self, path, size, info=None, file=None):
        self.path, self.size, self.info, self.file = path, size, info, file


class Source:
    """Gepruefte Eintraege eines Uploads (ZIP oder Einzeldateien). read()/open() zaehlen beim Lesen mit."""

    def __init__(self, upload, report):
        self.report, self.entries, self.zip, self.read_total = report, {}, None, 0
        files = upload["files"]
        if len(files) == 1 and zipfile.is_zipfile(files[0]["path"]):
            self._open_zip(files[0]["path"])
        else:
            for f in files:
                p = norm_path(f["name"])
                if p is None:
                    report.skip(f["name"], "bad_path")
                    continue
                self.entries[p] = Entry(p, f["size"], file=f["path"])
        self.root = self._common_root()

    def _open_zip(self, path):
        if zip_too_big_cd(path):
            raise ImportFail("zip_entries")
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError):
            raise ImportFail("not_zip")
        infos = self.zip.infolist()
        if len(infos) > ENTRIES_MAX:
            raise ImportFail("zip_entries")
        total = 0
        for i in infos:
            if i.is_dir():
                continue
            p = norm_path(i.filename)
            mode = (i.external_attr >> 16) & 0xFFFF
            if p is None:
                self.report.skip(i.filename, "bad_path")
                continue
            if stat.S_ISLNK(mode):
                self.report.skip(p, "symlink")
                continue
            if i.file_size > MB and i.file_size > RATIO_MAX * max(i.compress_size, 1):
                self.report.skip(p, "bomb")
                continue
            if p.lower().endswith(".zip"):
                self.report.skip(p, "nested_zip")
                continue
            total += i.file_size
            if total > UNPACKED_MAX:
                raise ImportFail("zip_size")
            if p in self.entries:                 # doppelter Name (nach Normalisierung): erster gewinnt
                continue
            self.entries[p] = Entry(p, i.file_size, info=i)

    def _common_root(self):
        """Gemeinsamer oberster Ordner aller Eintraege (ZIP eines Ordners) - wird beim Aufbau weggelassen."""
        tops = {p.split("/", 1)[0] for p in self.entries}
        if len(tops) == 1 and all("/" in p for p in self.entries):
            return next(iter(tops))
        return ""

    def paths(self):
        return list(self.entries)

    def read(self, path, limit):
        """Inhalt eines Eintrags (bytes), hoechstens limit Bytes - sonst ImportFail("too_big") fuer diesen Eintrag.
        Zaehlt die wirklich gelesenen Bytes (Kopfangaben im ZIP koennen luegen)."""
        e = self.entries[path]
        if e.size > limit:
            raise ImportFail("too_big", mb=limit // MB)
        buf = bytearray()
        with self.open(path) as f:
            while True:
                b = f.read(min(MB, limit + 1 - len(buf)))
                if not b:
                    break
                buf.extend(b)
                if len(buf) > limit:
                    raise ImportFail("too_big", mb=limit // MB)
        return bytes(buf)

    def open(self, path):
        e = self.entries[path]
        f = self.zip.open(e.info) if e.info is not None else open(e.file, "rb")
        return _Counted(f, self, e.size)

    def close(self):
        if self.zip is not None:
            self.zip.close()


class _Counted:
    """Datei-Objekt, das gelesene Bytes zaehlt: mehr als angegeben oder ueber UNPACKED_MAX -> Abbruch (ZIP-Bombe)."""

    def __init__(self, f, src, declared):
        self.f, self.src, self.declared, self.n = f, src, declared, 0

    def read(self, n=-1):
        b = self.f.read(n if n is not None and n >= 0 else MB)
        self.n += len(b)
        self.src.read_total += len(b)
        if self.n > self.declared or self.src.read_total > UNPACKED_MAX:
            raise ImportFail("bomb")
        return b

    def readable(self):
        return True

    def readinto(self, buf):
        b = self.read(len(buf))
        buf[:len(b)] = b
        return len(b)

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# =====================================================================================================
# Laeufe (Tabelle import_run) und Hintergrund-Thread
# =====================================================================================================
def busy(uid):
    with _db() as c:
        return c.execute("SELECT 1 FROM import_run WHERE uid=? AND state IN ('queued','running') LIMIT 1", (uid,)).fetchone() is not None


def start_run(uid, kind, name, work, tok=None, lang=None):
    """Lauf anlegen und im Hintergrund starten. work(run) -> (root, report). -> run_id oder None (schon einer aktiv)."""
    with _db() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("import:" + uid,))
        if c.execute("SELECT 1 FROM import_run WHERE uid=? AND state IN ('queued','running') LIMIT 1", (uid,)).fetchone():
            return None
        rid = c.execute("INSERT INTO import_run (uid, kind, name, state, created) VALUES (?,?,?,?,?)",
                        (uid, kind, str(name or "")[:200], "queued", _now())).lastrowid
        old = [r[0] for r in c.execute("SELECT id FROM import_run WHERE uid=? ORDER BY id DESC OFFSET ?", (uid, RUNS_KEEP)).fetchall()]
        if old:
            c.execute("DELETE FROM import_run WHERE id = ANY(?)", (old,))
    if tok:
        with _lock:
            if tok in _uploads:
                _uploads[tok]["busy"] = True
    lang = lang or i18n.get_lang()                    # Sprache der Anfrage (Bericht, Namen) - der Thread erbt sie nicht
    threading.Thread(target=_thread, args=(uid, rid, work, tok, lang), name=f"import-{rid}", daemon=True).start()
    return rid


class Run:
    """Handle fuer die Arbeit eines Laufs: Fortschritt, Abbruch, angelegte Seiten/Dateien (zum Zuruecknehmen)."""

    def __init__(self, uid, rid):
        self.uid, self.id = uid, rid
        self.report = Report()
        self.created, self.files, self._last = [], [], 0.0

    def step(self, phase, done=0, total=0, force=False):
        if self.id in _cancel:
            raise Cancelled()
        now = _now()
        if not force and now - self._last < STEP_EVERY:
            return
        self._last = now
        with _db() as c, c:
            c.execute("UPDATE import_run SET step=? WHERE id=?", (json.dumps({"phase": phase, "done": done, "total": total}), self.id))


def _set(rid, **kw):
    cols = ", ".join(f"{k}=?" for k in kw)
    with _db() as c, c:
        c.execute(f"UPDATE import_run SET {cols} WHERE id=?", (*kw.values(), rid))


def _thread(uid, rid, work, tok, lang=None):
    i18n.set_lang(lang or account_lang(uid))
    run = Run(uid, rid)
    try:
        with _sem:                                    # hoechstens PARALLEL Laeufe gleichzeitig, Rest wartet ("queued")
            if rid in _cancel:
                raise Cancelled()
            _set(rid, state="running", started=_now())
            try:
                root = work(run)
            except (zipfile.BadZipFile, EOFError, UnicodeError) as ex:     # kaputtes Archiv / Datei mitten im Lesen
                raise ImportFail("bad_file") from ex
            _set(rid, state="done", finished=_now(), root=root, report=json.dumps(run.report.to_json(), ensure_ascii=False),
                 step=json.dumps({"phase": "done", "done": 1, "total": 1}))
    except BaseException as ex:  # noqa: BLE001 - jeder Fehler beendet den Lauf sauber
        code = ex.code if isinstance(ex, ImportFail) else "cancelled" if isinstance(ex, Cancelled) else "failed"
        p = ex.p if isinstance(ex, ImportFail) else {}
        if not isinstance(ex, (ImportFail, Cancelled)):
            print(f"import {rid}: {type(ex).__name__}: {ex}", flush=True)
        try:
            rollback(run)
        except Exception as e2:  # noqa: BLE001
            print(f"import {rid}: Zuruecknehmen fehlgeschlagen ({type(e2).__name__})", flush=True)
        _set(rid, state="cancelled" if code == "cancelled" else "failed", finished=_now(),
             error=json.dumps({"code": code, "p": p}), report=json.dumps(run.report.to_json(), ensure_ascii=False))
    finally:
        _cancel.discard(rid)
        if tok:
            drop_upload(tok)


def rollback(run):
    """Was der Lauf angelegt hat: in den Papierkorb (gemeinsamer Zeitstempel); geschriebene Dateien loeschen."""
    now = _now()
    if run.created:
        with _db() as c, c:
            for k in range(0, len(run.created), 5000):
                c.execute("UPDATE note SET deleted_at=? WHERE uid=? AND id = ANY(?) AND deleted_at IS NULL",
                          (now, run.uid, run.created[k:k + 5000]))
            tree_changed(c, run.uid, run.created[:1], "trashed")
    if run.files:
        with _db() as c, c:
            c.execute("DELETE FROM file WHERE uid=? AND id = ANY(?)", (run.uid, run.files))
        for fid in run.files:
            try:
                os.remove(os.path.join(FILES_DIR, fid))
            except OSError:
                pass


def cancel(uid, rid):
    with _db() as c:
        ok = c.execute("SELECT 1 FROM import_run WHERE id=? AND uid=? AND state IN ('queued','running')", (rid, uid)).fetchone()
    if ok:
        _cancel.add(rid)
    return bool(ok)


def run_out(r):
    """Zeile (id, kind, name, state, created, started, finished, step, report, root, error) -> Antwort."""
    err = _json_load(r[10], {}) if r[10] else {}
    return {"id": r[0], "kind": r[1], "name": r[2], "state": r[3], "created": r[4], "started": r[5], "finished": r[6],
            "step": _json_load(r[7], {}), "report": report_out(r[8]), "root": r[9],
            "error": reason_text(err.get("code"), err.get("p")) if isinstance(err, dict) and err.get("code") else ""}


RUN_COLS = "id, kind, name, state, created, started, finished, step, report, root, error"


def on_start():
    """Beim Start: Laeufe, die noch liefen, gelten als abgebrochen (Thread ist weg); alte Upload-Ordner weg."""
    try:
        with _db() as c, c:
            c.execute("UPDATE import_run SET state='failed', finished=?, error=? WHERE state IN ('queued','running')",
                      (_now(), json.dumps({"code": "restart", "p": {}})))
    except Exception as ex:  # noqa: BLE001 - Tabelle evtl. noch nicht da
        print(f"import: {type(ex).__name__}", flush=True)
    shutil.rmtree(TMP_DIR, ignore_errors=True)


# =====================================================================================================
# Einspielen des Zwischenformats
# =====================================================================================================
REF_RX = re.compile(r"\$ref:([A-Za-z0-9_.-]{1,80})")
FILE_RX = re.compile(r"\$file:([A-Za-z0-9_.-]{1,80})")


def resolve(v, ids, fids=None):
    """$ref:x -> ID (ganzer String: Zahl; im Text: Ziffern bzw. 0), $file:k -> /files/<id> bzw. ''."""
    if isinstance(v, str):
        m = REF_RX.fullmatch(v)
        if m:
            return ids.get(m.group(1))
        if "$ref:" in v:
            v = REF_RX.sub(lambda m: str(ids.get(m.group(1)) or 0), v)
        if fids is not None and "$file:" in v:
            v = FILE_RX.sub(lambda m: ("/files/" + fids[m.group(1)]) if m.group(1) in fids else "", v)
        return v
    if isinstance(v, list):
        return [resolve(x, ids, fids) for x in v]
    if isinstance(v, dict):
        return {k: (x if k == "code" else resolve(x, ids, fids)) for k, x in v.items()}    # Code-Bloecke: Klartext
    return v


def slug_id(name, used):
    """Eigenschafts-/Options-ID [a-z0-9_]{1,24} aus einem Namen, eindeutig in used (wird ergaenzt)."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:20] or "c"
    base, k = s, 1
    while s in used:
        k += 1
        s = f"{base[:20]}_{k}"
    used.add(s)
    return s


def opt_color(i):
    cols = [c for c in ("blue", "green", "orange", "purple", "pink", "yellow", "red", "brown", "gray") if c in OPT_COLORS]
    return cols[i % len(cols)]


def counts(c, uid):
    """(Seiten ohne Datenbank-Eintraege, Datenbank-Eintraege) - ohne Papierkorb."""
    dbs = "SELECT id FROM note WHERE uid=? AND kind='database'"
    rows = c.execute(f"SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL AND parent_id IN ({dbs})", (uid, uid)).fetchone()[0]
    pages = c.execute(f"SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL AND (parent_id IS NULL OR parent_id NOT IN ({dbs}))",
                      (uid, uid)).fetchone()[0]
    return pages, rows


def check_limits(uid, n_pages, n_rows):
    with _db() as c:
        pages, rows = counts(c, uid)
    if pages + n_pages > NOTE_MAX_COUNT:
        raise ImportFail("notes_limit", n=NOTE_MAX_COUNT)
    if rows + n_rows > NOTE_ROW_MAX_COUNT:
        raise ImportFail("rows_account", n=NOTE_ROW_MAX_COUNT)


def new_ids(c, n):
    if not n:
        return []
    return [r[0] for r in c.execute("SELECT nextval(pg_get_serial_sequence('note', 'id')) FROM generate_series(1, ?)", (n,)).fetchall()]


def insert_page(c, uid, nid, title, parent, sort, kind="page", icon=None, tags=None, schema=None, body=None, props=None, now=None):
    from saga.pages import _title_in
    now = now or _now()
    c.execute("INSERT INTO note (id, uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated, "
              "updated_by, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (nid, uid, _title_in(str(title or "")), body or json.dumps({"blocks": []}), "blocks", icon, parent, sort,
               json.dumps(tags or [], ensure_ascii=False), kind,
               schema if schema is None or isinstance(schema, str) else json.dumps(schema, ensure_ascii=False),
               props, now, now, uid, uid))


def clean_body(c, uid, blocks, report, path):
    """Bloecke -> gespeicherter Inhalt (clean_blocks; zu gross: hinten gekuerzt + Bericht)."""
    from saga.notes import _check_file_refs
    from saga.pages import NOTE_BLOCKS_MAX, clean_blocks
    blocks = [b for b in blocks if isinstance(b, dict)]
    raw = json.dumps({"blocks": blocks}, ensure_ascii=False)
    if len(raw) > NOTE_BLOCKS_MAX:
        report.note(path, "blocks_limit")
        while blocks and len(raw) > NOTE_BLOCKS_MAX - 2000:
            blocks = blocks[: max(1, len(blocks) * 3 // 4)] if len(blocks) > 1 else []
            raw = json.dumps({"blocks": blocks}, ensure_ascii=False)
    body, _ = clean_blocks(raw)
    _check_file_refs(uid, json.loads(body)["blocks"])
    return body


def store_file(run, c, name, src, size):
    """Datei in FILES_DIR schreiben + Tabelle file. -> (fid, is_image)."""
    from saga.files import _sniff_image
    fid = os.urandom(12).hex()
    dst = os.path.join(FILES_DIR, fid)
    run.files.append(fid)
    n, head = 0, b""
    with src as f, open(dst, "wb") as out:
        while True:
            b = f.read(MB)
            if not b:
                break
            if not head:
                head = b[:16]
            n += len(b)
            if n > FILE_MAX:
                raise ImportFail("too_big", mb=FILE_MAX // MB)
            out.write(b)
    img = _sniff_image(head)
    clean = re.sub(r"[\x00-\x1f/\\]", "_", str(name or "datei"))[:120] or "datei"
    c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
              (fid, run.uid, clean, img or "application/octet-stream", n, 1 if img else 0, _now()))
    return fid, bool(img)


def apply_plan(run, plan, parent):
    """Plan einspielen (unter parent oder oberste Ebene) -> ID des ersten Items (Wurzel)."""
    uid, rep = run.uid, run.report
    items = plan["items"]
    files = plan.get("files") or {}
    n_pages = sum(1 for it in items if it.get("parent") is None or not _is_db_ref(items, it.get("parent")))
    n_rows = sum(int(it.get("row_count") or 0) for it in items if it["kind"] == "database")
    check_limits(uid, n_pages, n_rows)
    need = sum(int(f.get("size") or 0) for f in files.values())
    if need:
        with _db() as c:
            used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if used + need > FILE_QUOTA:
            raise ImportFail("quota")
    now = _now()
    total = len(items) + n_rows + len(files)
    done = 0
    run.step("pages", 0, total, force=True)
    # 1) Seiten und Datenbanken leer anlegen (IDs vorab: Verweise zeigen sofort auf die neuen Seiten), Tiefenreihenfolge
    by_ref = {it["ref"]: it for it in items}

    def depth(it, seen=()):
        p = it.get("parent")
        return 0 if p not in by_ref or p in seen else 1 + depth(by_ref[p], seen + (it["ref"],))
    order = sorted(items, key=depth)
    ids = {}
    for k in range(0, len(order), CHUNK):
        part = order[k:k + CHUNK]
        with _db() as c, c:
            for it, nid in zip(part, new_ids(c, len(part))):
                ids[it["ref"]] = nid
                par = ids.get(it.get("parent")) if it.get("parent") else parent
                kind = "database" if it["kind"] == "database" else "page"
                sc = {"props": [], "views": [{"id": "tabelle", "name": t("Tabelle"), "type": "table"}]} if kind == "database" else None
                icon = it.get("icon") if isinstance(it.get("icon"), str) and 0 < len(it["icon"]) <= 8 else None
                insert_page(c, uid, nid, it.get("title"), par, now + done * 1e-3, kind, icon, _tags_ok(it.get("tags")), sc, now=now)
                run.created.append(nid)
                rep.counts["databases" if kind == "database" else "pages"] += 1
                done += 1
            tree_changed(c, uid, [ids[it["ref"]] for it in part], "created")
        run.step("pages", done, total)
    # 2) Dateien
    fids = {}
    for key, f in files.items():
        try:
            with _db() as c, c:
                fid, img = store_file(run, c, f.get("name"), f["open"](), int(f.get("size") or 0))
            fids[key] = fid
            rep.counts["files"] += 1
        except ImportFail as ex:
            rep.skip(f.get("path") or f.get("name"), ex.code, **ex.p)
        except (OSError, zipfile.BadZipFile, KeyError):
            rep.skip(f.get("path") or f.get("name"), "bad_file")
        done += 1
        run.step("files", done, total)
    plan["file_ids"] = fids
    # 3) Schemas (Relationen ueber refs), dann Zeilen in Etappen
    for it in items:
        if it["kind"] != "database":
            continue
        nid = ids[it["ref"]]
        raw = resolve(it.get("schema") or {"props": []}, ids)
        raw["props"] = [p for p in raw.get("props") or [] if p.get("type") != "relation" or isinstance(p.get("target"), int)]
        sc = _schema_stamp({"props": []}, _clean_schema(raw), uid)
        with _db() as c, c:
            c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(sc, ensure_ascii=False), nid, uid))
        done = _insert_rows(run, nid, sc, it, ids, done, total)
    # 4) Inhalte der Seiten (refs/Dateien aufloesen, Bericht bei fehlenden Zielen)
    for k in range(0, len(items), CHUNK):
        part = [it for it in items[k:k + CHUNK] if it.get("body")]
        if not part:
            continue
        with _db() as c, c:
            for it in part:
                blocks = it["body"]() if callable(it["body"]) else it["body"]
                blocks = _fix_files(resolve(blocks or [], ids, fids), fids)
                body = clean_body(c, uid, blocks, rep, it.get("path") or it.get("title"))
                c.execute("UPDATE note SET body=? WHERE id=? AND uid=?", (body, ids[it["ref"]], uid))
        run.step("content", done, total)
    return ids[items[0]["ref"]] if items else None


def _tags_ok(tags):
    """Tags einzeln pruefen (ein ungueltiger Tag kostet nicht alle)."""
    from saga.pages import _tags
    out = []
    for x in tags if isinstance(tags, list) else []:
        try:
            out += _tags([str(x)])
        except ValueError:
            pass
    try:
        return _tags(out[:20])
    except ValueError:
        return []


def _is_db_ref(items, ref):
    return any(it["ref"] == ref and it["kind"] == "database" for it in items)


def _fix_files(blocks, fids):
    """Bild-/Datei-Bloecke ohne aufgeloeste Datei -> Absatz mit dem Namen; Bild, das keins ist -> Anhang."""
    from html import escape
    from saga.notes import _iter_blocks
    for b in _iter_blocks(blocks):
        if b.get("type") not in ("image", "attaches"):
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        f = d.get("file") if isinstance(d.get("file"), dict) else {}
        url = str(f.get("url") or "")
        if not url.startswith("/files/"):
            label = str(f.get("name") or d.get("caption") or d.get("title") or "")
            b["type"], b["data"] = "paragraph", {"text": escape(label, quote=False)[:300]}
            continue
        if b["type"] == "image" and f.get("is_image") is False:
            b["type"], b["data"] = "attaches", {"file": {"url": url, "name": f.get("name") or "", "size": f.get("size") or 0,
                                                         "extension": str(f.get("name") or "").rsplit(".", 1)[-1][:10]},
                                                "title": f.get("name") or ""}
        f.pop("is_image", None)
    return blocks


def _insert_rows(run, nid, schema, it, ids, done, total):
    """Zeilen einer Datenbank in Etappen anlegen (Werte geprueft mit _clean_props, Relationen ueber refs)."""
    rows_fn = it.get("rows")
    if not rows_fn:
        return done
    uid, rep = run.uid, run.report
    by_id = {p["id"]: p for p in schema["props"]}
    now = _now()
    batch, i = [], 0

    from saga.notes import _rel_sync
    paired = any(p.get("type") == "relation" and p.get("pair") for p in schema["props"])

    def flush():
        if not batch:
            return
        with _db() as c, c:
            got = new_ids(c, len(batch))
            c.executemany("INSERT INTO note (id, uid, title, body, format, parent_id, sort, tags, kind, props, created, updated, updated_by, created_by) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          [(rid, uid, title, json.dumps({"blocks": []}), "blocks", nid, s, "[]", "page", props, now, now, uid, uid)
                           for rid, (title, props, s, ref) in zip(got, batch)])
            for rid, (_, props, _, ref) in zip(got, batch):
                if ref:
                    ids[ref] = rid
                if paired and props:                        # zweiseitige Relationen: Gegenseite nachziehen
                    _rel_sync(c, uid, schema, rid, {}, json.loads(props))
            run.created.extend(got)
            tree_changed(c, uid, [nid], "updated")
        rep.counts["rows"] += len(batch)
        batch.clear()

    from saga.pages import _title_in
    rel = {p["id"] for p in schema["props"] if p["type"] == "relation"}
    for row in rows_fn():
        raw = {k: (resolve(v, ids) if k in rel else v) for k, v in (row.get("props") or {}).items()}   # nur Relationen haben refs
        clean = {}
        for k, v in raw.items():
            if k not in by_id:
                continue
            try:
                clean.update(_clean_props(schema, {k: v}))
            except (ValueError, TypeError):
                rep.note(row.get("path") or row.get("title"), "bad_value", type=by_id[k]["type"])
        batch.append((_title_in(str(row.get("title") or ""))[:200], json.dumps(clean, ensure_ascii=False) if clean else None,
                      now + i * 1e-3, row.get("ref")))
        i += 1
        done += 1
        if len(batch) >= CHUNK:
            flush()
            run.step("rows", done, total)
    flush()
    run.step("rows", done, total, force=True)
    return done
