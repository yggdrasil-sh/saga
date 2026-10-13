"""Dateien und Bilder in Seiten (Hochladen, Vorschaubilder, Aufraeumen)."""
import os
import re
import subprocess
import sys
import threading
import time
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from db import connect as _db
from fastapi import APIRouter
from saga.core import _api_guard, _bad, _session_user, t
from saga.notes import FILES_DIR, FILE_MAX, FILE_QUOTA, _disposition

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


def files_init():
    os.makedirs(FILES_DIR, mode=0o700, exist_ok=True)
    with _db() as c, c:
        c.execute("""CREATE TABLE IF NOT EXISTS file (
            id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
            size INTEGER NOT NULL, is_image INTEGER NOT NULL, created REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS file_uid ON file (uid)")


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


@router.post("/api/files")
async def api_file_upload(request: Request):
    """Upload aus den Editor.js-Werkzeugen (Formularfeld 'image' oder 'file')."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if int(request.headers.get("content-length") or 0) > FILE_MAX + 64 * 1024:
        return JSONResponse({"success": 0, "error": t("Datei zu gross (max. {n} MB)", n=FILE_MAX // 1048576)}, status_code=413)
    form = await request.form()
    up = form.get("image") or form.get("file")
    if up is None or not hasattr(up, "read"):
        return JSONResponse({"success": 0, "error": t("keine Datei")}, status_code=400)
    data = await up.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        return JSONResponse({"success": 0, "error": t("Datei zu gross (max. {n} MB)", n=FILE_MAX // 1048576)}, status_code=413)
    if not data:
        return JSONResponse({"success": 0, "error": t("leere Datei")}, status_code=400)
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", os.path.basename(up.filename or "datei"))[:120] or "datei"
    img = _sniff_image(data[:16])
    if "image" in form and not img:
        return JSONResponse({"success": 0, "error": t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP)")}, status_code=415)
    with _db() as c, c:
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if used + len(data) > FILE_QUOTA:
            return JSONResponse({"success": 0, "error": t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576)}, status_code=507)
        fid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fid, uid, name, img or "application/octet-stream", len(data), 1 if img else 0, time.time()))
    ext = name.rsplit(".", 1)[-1].lower()[:10] if "." in name else ""
    return JSONResponse({"success": 1, "file": {"url": f"/files/{fid}", "name": name, "size": len(data),
                                               "extension": ext, "title": name}})


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
    """Nur fuer den Besitzer (Session-Cookie). Bilder inline (auf Wunsch verkleinert), alles andere nur als Download."""
    uid = _session_user(request)
    if not uid or not re.fullmatch(r"[0-9a-f]{24}", fid):
        return PlainTextResponse("not found", status_code=404)
    with _db() as c:
        r = c.execute("SELECT name, mime, is_image FROM file WHERE id=? AND uid=?", (fid, uid)).fetchone()
    path = os.path.join(FILES_DIR, fid)
    if not r or not os.path.isfile(path):
        return PlainTextResponse("not found", status_code=404)
    if r[2]:
        return _image_response(fid, path, r[1], request)
    return FileResponse(path, media_type="application/octet-stream", headers=dict(_FILE_H, **{"Content-Disposition": _disposition(r[0])}))
