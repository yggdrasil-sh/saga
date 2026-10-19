"""Dateien und Bilder in Seiten (Hochladen, Vorschaubilder, Aufraeumen)."""
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from db import connect as _db
from fastapi import APIRouter
from saga.core import STATE_DIR, _api_guard, _bad, _body, _session_user, t
from saga.notes import FILES_DIR, FILE_MAX, FILE_QUOTA, _disposition
from saga.sharing import Access, access

router = APIRouter()


_IMG_MAGIC = [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
              (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif")]


def _sniff_image(head):
    for sig, mime in _IMG_MAGIC:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


UPLOAD_TMP = os.path.join(STATE_DIR, "tmp")


def files_init():
    os.makedirs(FILES_DIR, mode=0o700, exist_ok=True)
    # Zwischendateien (Formular-Uploads ueber 1 MB, ZIP-Export) auf das Daten-Volume statt nach /tmp:
    # /tmp ist im Container ein tmpfs und belegt damit Arbeitsspeicher.
    try:
        os.makedirs(UPLOAD_TMP, mode=0o700, exist_ok=True)
        tmp_cleanup()                                    # Reste nach einem Absturz/abgebrochenem Download
        tempfile.tempdir = UPLOAD_TMP
    except OSError as ex:
        print(f"saga: {UPLOAD_TMP} nicht nutzbar ({ex}) - Zwischendateien bleiben in {tempfile.gettempdir()}", flush=True)
    with _db() as c, c:
        c.execute("""CREATE TABLE IF NOT EXISTS file (
            id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
            size INTEGER NOT NULL, is_image INTEGER NOT NULL, created REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS file_uid ON file (uid)")


def tmp_cleanup(max_age=3600):
    """Zwischendateien in UPLOAD_TMP loeschen, die aelter als max_age Sekunden sind (beim Start und stuendlich)
    -> Anzahl. Laufende Uploads/Exporte sind juenger; abgebrochene ZIP-Downloads raeumt die Antwort selbst auf."""
    n = 0
    try:
        names = os.listdir(UPLOAD_TMP)
    except OSError:
        return 0
    for f in names:
        p = os.path.join(UPLOAD_TMP, f)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < time.time() - max_age:
                os.remove(p)
                n += 1
        except OSError:
            pass
    return n


def files_gc(min_age=86400):
    """Dateien loeschen, die in keiner Seite und keiner Version ihres Besitzers mehr vorkommen
    (erst nach min_age Sekunden - frisch hochgeladene Bilder sind evtl. noch nicht gespeichert)."""
    removed = 0
    with _db() as c, c:
        for fid, uid in c.execute("SELECT id, uid FROM file WHERE created < ?", (time.time() - min_age,)).fetchall():
            ref = f"/files/{fid}"
            used = c.execute("SELECT 1 FROM note WHERE uid=? AND (instr(body, ?) > 0 OR icon=? OR cover=?) LIMIT 1",
                             (uid, ref, ref, ref)).fetchone() or \
                c.execute("SELECT 1 FROM note_version WHERE uid=? AND instr(body, ?) > 0 LIMIT 1", (uid, ref)).fetchone()
            if not used:
                c.execute("DELETE FROM file WHERE id=?", (fid,))
                try:
                    os.remove(os.path.join(FILES_DIR, fid))
                except FileNotFoundError:
                    pass
                _drop_thumbs(fid)
                removed += 1
    return removed


# 2.3 (saga.fed_pages): Upload in eine Seite einer anderen Saga-Instanz - fn(uid, nid, name, data, image) -> Antwort
FED_UPLOAD = None
FED_COPY = None              # fn(uid, url, note) -> Antwort: Bild/Datei einer fremden Seite in dieselbe Seite kopieren


def store_upload(owner, name, data, img, fed_by=None, fed_note=None, fed_quota=None):
    """Datei speichern (Quote des Eigentuemers; fed_by: fremde Person, die hochlaedt - zusaetzlich ihre eigene Grenze
    fed_quota). -> (antwort-dict, None) oder (None, fehlerantwort)."""
    with _db() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("file:" + owner,))
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (owner,)).fetchone()[0]
        if used + len(data) > FILE_QUOTA:
            return None, JSONResponse({"success": 0, "error": t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576)}, status_code=507)
        if fed_by is not None and fed_quota is not None:
            mine = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=? AND fed_by=?", (owner, fed_by)).fetchone()[0]
            if mine + len(data) > fed_quota:
                return None, JSONResponse({"success": 0, "error": t("Speicherplatz für Personen anderer Saga-Server voll (max. {n} MB)",
                                                                    n=max(1, fed_quota // 1048576))}, status_code=507)
        fid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created, fed_by, fed_note) VALUES (?,?,?,?,?,?,?,?,?)",
                  (fid, owner, name, img or "application/octet-stream", len(data), 1 if img else 0, time.time(), fed_by, fed_note))
    ext = name.rsplit(".", 1)[-1].lower()[:10] if "." in name else ""
    return {"success": 1, "file": {"url": f"/files/{fid}", "name": name, "size": len(data), "extension": ext, "title": name}}, None


def upload_owner(uid, note):
    """Wem gehoert eine neue Datei? Ohne Seite: mir. Mit Seite: deren Eigentuemer, wenn ich sie bearbeiten darf.
    -> (eigentuemer, fehlerantwort)"""
    if note in (None, ""):
        return uid, None
    try:
        nid = int(note)
    except (TypeError, ValueError):
        return None, JSONResponse({"success": 0, "error": t("ungültige Angabe")}, status_code=400)
    with _db() as c:
        owner, _, err = access(c, uid, nid, "edit")
    if err is not None:
        return None, JSONResponse({"success": 0, "error": "not found" if err.status_code == 404 else t("Dafür fehlt dir die Berechtigung.")},
                                  status_code=err.status_code)
    return owner, None


def _file_visible(c, uid, owner, fid):
    """Datei eines anderen Kontos: sichtbar, wenn eine Seite des Besitzers sie benutzt (Inhalt, Symbol, Titelbild)
    und ich diese Seite sehen darf (Freigabe). Erst die sichtbaren Seiten (eine Abfrage ohne Inhalte), dann nur in DIESEN
    nach der Datei suchen - nicht alle Inhalte des Besitzers je Bild durchsuchen (und kein LIMIT, das Treffer abschneidet)."""
    vis = list(Access(c, uid).tree(owner))
    if not vis:
        return False
    ref = f"/files/{fid}"
    for k in range(0, len(vis), 500):
        part = vis[k:k + 500]
        if c.execute(f"SELECT 1 FROM note WHERE uid=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(part))}) "
                     "AND (instr(body, ?) > 0 OR icon=? OR cover=?) LIMIT 1", (owner, *part, ref, ref, ref)).fetchone():
            return True
    return False


@router.post("/api/files")
async def api_file_upload(request: Request):
    """Upload aus den Editor.js-Werkzeugen (Formularfeld 'image' oder 'file').
    Fuer eine fremde Seite (Kopfzeile X-Saga-Note oder ?note=<id>, Rolle 'edit'): die Datei gehoert dem Eigentuemer
    der Seite und zaehlt bei ihm - nur so darf sie in seiner Seite stehen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    note = request.headers.get("x-saga-note") or request.query_params.get("note")
    fed = FED_UPLOAD is not None and isinstance(note, str) and re.fullmatch(r"-[1-9][0-9]{0,17}", note.strip() or "")
    owner, err = (None, None) if fed else upload_owner(uid, note)
    if err is not None:
        return err
    if int(request.headers.get("content-length") or 0) > FILE_MAX + 64 * 1024:
        return JSONResponse({"success": 0, "error": t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576)}, status_code=413)
    form = await request.form()
    up = form.get("image") or form.get("file")
    if up is None or not hasattr(up, "read"):
        return JSONResponse({"success": 0, "error": t("keine Datei")}, status_code=400)
    data = await up.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        return JSONResponse({"success": 0, "error": t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576)}, status_code=413)
    if not data:
        return JSONResponse({"success": 0, "error": t("leere Datei")}, status_code=400)
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", os.path.basename(up.filename or "datei"))[:120] or "datei"
    img = _sniff_image(data[:16])
    if "image" in form and not img:
        return JSONResponse({"success": 0, "error": t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP)")}, status_code=415)
    if fed:                                  # Seite einer anderen Saga-Instanz: Datei liegt beim Eigentuemer (saga.fed_pages)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(FED_UPLOAD, uid, int(note), name, data, "image" in form)
    out, err = store_upload(owner, name, data, img)
    return err if err is not None else JSONResponse(out)


@router.post("/api/files/copy")
async def api_file_copy(request: Request):
    """Kopieren/Ausschneiden + Einfuegen von Bild- und Dateibloecken (2.1): {url: "/files/<id>", note?: <ziel-seite>} -> {url}.
    Gehoert die Datei schon dem Eigentuemer der Zielseite, bleibt die Referenz gleich. Sonst (fremde Seite, anderes Konto)
    bekommt der Eigentuemer der Zielseite eine eigene Kopie - nur so sehen alle mit Zugriff auf die Zielseite das Bild,
    zaehlt es bei ihm und ueberlebt das Aufraeumen. Ich muss die Quelle sehen und die Zielseite bearbeiten duerfen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    note = (b or {}).get("note")
    if FED_COPY is not None and isinstance(note, int) and not isinstance(note, bool) and note < 0:
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(FED_COPY, uid, str((b or {}).get("url") or ""), note)
    return file_copy(uid, str((b or {}).get("url") or ""), (b or {}).get("note"))


def file_copy(uid, url, note):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person, note = Seite hier, die sie bearbeiten darf)."""
    m = re.fullmatch(r"/files/([0-9a-f]{24})", url)
    if not m:
        return _bad(t("ungültige Angabe"))
    fid = m.group(1)
    owner, err = upload_owner(uid, note)
    if err is not None:
        return err
    with _db() as c, c:
        r = c.execute("SELECT name, mime, size, is_image, uid FROM file WHERE id=?", (fid,)).fetchone()
        if not r or (r[4] != uid and not _file_visible(c, uid, r[4], fid)) or not os.path.isfile(os.path.join(FILES_DIR, fid)):
            return _bad("not found", 404)
        if r[4] == owner:
            return JSONResponse({"url": f"/files/{fid}", "copied": False})
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (owner,)).fetchone()[0]
        if used + r[2] > FILE_QUOTA:
            return JSONResponse({"error": t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576)}, status_code=507)
        nid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "rb") as src, open(os.path.join(FILES_DIR, nid), "wb") as dst:
            dst.write(src.read())
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (nid, owner, r[0], r[1], r[2], r[3], time.time()))
    print(f"files: Kopie {fid} -> {nid} fuer Seite eines anderen Kontos", flush=True)
    return JSONResponse({"url": f"/files/{nid}", "copied": True})


THUMB_W = (160, 320, 640, 1280, 2000)           # erlaubte Breiten fuer ?w=
THUMB_DIR = os.path.join(FILES_DIR, "thumbs")
_thumb_sem = threading.Semaphore(1)
_FILE_H = {"Cache-Control": "private, max-age=31536000, immutable",   # Datei-IDs sind zufaellig, Inhalt aendert sich nie
           "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer"}


def _thumb_w(request):
    try:
        w = int(request.query_params.get("w") or 0)
    except ValueError:
        return 0
    return min((x for x in THUMB_W if x >= w), default=THUMB_W[-1]) if w > 0 else 0


def _thumb(fid, path, mime, w):
    """Verkleinerte Fassung, einmal erzeugt und auf der Platte zwischengespeichert: Fotos/Grafiken als WebP,
    animierte GIFs wieder als (kleineres) animiertes GIF - das spielen auch die Apps ab.
    None = Original ausliefern (schon klein genug, kein Rasterbild, kein Gewinn, Fehler)."""
    if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
        return None
    base = os.path.join(THUMB_DIR, f"{fid}-{w}")
    for ext in (".webp", ".gif"):
        if os.path.isfile(base + ext):
            return base + ext
    if os.path.isfile(base + ".orig"):               # schon geprueft: Original ist besser
        return None
    with _thumb_sem:                                 # immer nur EIN Bild auf einmal (Speicher: Container hat 1 GB)
        for ext in (".webp", ".gif"):                # hat ein anderer Aufruf es inzwischen erzeugt?
            if os.path.isfile(base + ext):
                return base + ext
        return _make_thumb(path, base, w)


# Wird in einem eigenen, speicherbegrenzten Prozess ausgefuehrt: ein riesiges Foto oder GIF kann so
# hoechstens diesen Prozess sprengen, nie den Server (der Container hat nur 1 GB).
_THUMB_CODE = r"""
import sys
from PIL import Image, ImageSequence
path, tmp, w = sys.argv[1], sys.argv[2], int(sys.argv[3])
Image.MAX_IMAGE_PIXELS = 50_000_000
with Image.open(path) as im:
    if im.width <= w:
        print("orig"); sys.exit()
    anim = getattr(im, "is_animated", False) and im.format in ("GIF", "WEBP", "PNG")
    size = (w, max(1, round(im.height * w / im.width)))
    if anim:
        if getattr(im, "n_frames", 1) * size[0] * size[1] > 20_000_000:
            print("orig"); sys.exit()
        frames, durs = [], []
        for fr in ImageSequence.Iterator(im):
            frames.append(fr.convert("RGBA").resize(size, Image.LANCZOS)); durs.append(fr.info.get("duration", im.info.get("duration", 100)) or 100)
        frames[0].save(tmp, "GIF", save_all=True, append_images=frames[1:], duration=durs, loop=im.info.get("loop", 0), disposal=2)
        print(".gif")
    else:
        if im.format in ("JPEG", "MPO"):
            im.draft("RGB", size)                    # gleich verkleinert entpacken: schnell und sparsam
        alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im.convert("RGBA" if alpha else "RGB").resize(size, Image.LANCZOS).save(tmp, "WEBP", quality=82, method=4)
        print(".webp")
"""


def _thumb_limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (450 << 20, 450 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))


def _make_thumb(path, base, w):
    os.makedirs(THUMB_DIR, mode=0o700, exist_ok=True)
    tmp = f"{base}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        r = subprocess.run([sys.executable, "-c", _THUMB_CODE, path, tmp, str(w)], capture_output=True, text=True,
                           timeout=90, preexec_fn=_thumb_limits)
        ext = r.stdout.strip()
        if r.returncode != 0 or ext not in (".webp", ".gif") or os.path.getsize(tmp) >= os.path.getsize(path):
            open(base + ".orig", "w").close()            # zu gross/kaputt/kein Gewinn -> Original (nicht nochmal versuchen)
            return None
        os.replace(tmp, base + ext)
        return base + ext
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _thumb_mime(tp):
    return "image/gif" if tp.endswith(".gif") else "image/webp"


def _drop_thumbs(fid):
    for w in THUMB_W:
        for ext in (".webp", ".gif", ".orig"):
            try:
                os.remove(os.path.join(THUMB_DIR, f"{fid}-{w}{ext}"))
            except OSError:
                pass


def _image_response(fid, path, mime, request):
    """Bild ausliefern - mit ?w=<Breite> verkleinert (Galerie, Symbole, Widgets, Bilder im Text)."""
    w = _thumb_w(request)
    tp = _thumb(fid, path, mime, w) if w else None
    h = dict(_FILE_H, **{"Content-Disposition": "inline"})
    return FileResponse(tp, media_type=_thumb_mime(tp), headers=h) if tp else FileResponse(path, media_type=mime, headers=h)


@router.get("/files/{fid}")
def file_get(fid: str, request: Request):
    """Fuer den Besitzer (Session-Cookie) - und fuer Konten, die eine Seite des Besitzers sehen duerfen, in der die Datei
    steht (Teilen). Bilder inline (auf Wunsch verkleinert), alles andere nur als Download."""
    uid = _session_user(request)
    if not uid or not re.fullmatch(r"[0-9a-f]{24}", fid):
        return PlainTextResponse("not found", status_code=404)
    with _db() as c:
        r = c.execute("SELECT name, mime, is_image, uid FROM file WHERE id=?", (fid,)).fetchone()
        foreign = bool(r) and r[3] != uid
        if foreign and not _file_visible(c, uid, r[3], fid):
            r = None
    path = os.path.join(FILES_DIR, fid)
    if not r or not os.path.isfile(path):
        return PlainTextResponse("not found", status_code=404)
    resp = _image_response(fid, path, r[1], request) if r[2] else \
        FileResponse(path, media_type="application/octet-stream", headers=dict(_FILE_H, **{"Content-Disposition": _disposition(r[0])}))
    if foreign:                                # Freigabe kann enden: nicht ein Jahr lang im Browser behalten
        resp.headers["Cache-Control"] = "private, max-age=300"
    return resp
