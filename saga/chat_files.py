"""Videos im Chat (2.4 E2): fortsetzbarer Upload im Format saga-file/v2 und Medien-Token fuer lange Wiedergaben.

Uploads: Die App legt mit POST /api/chats/{cid}/uploads {size, fmt:2} einen Upload an (Kontingent, Grenze, hoechstens
UPLOADS_PAR gleichzeitig) und schickt die Bytes in Teilen mit PUT /api/chat/uploads/{id} (Content-Range: bytes a-b/size,
nur fortlaufend anhaengend). HEAD liefert den Stand (Upload-Offset) zum Fortsetzen nach Abbruch. Im Container nimmt
saga-media (media/, Go) PUT/HEAD selbst an und schreibt direkt auf die Platte - Python sieht keine Medienbytes, nur die
Berechtigung (/internal/media/upload) und den Abschluss (/internal/media/upload-done: Kopf pruefen, chat_file mit fmt=2).
Ohne saga-media nimmt Python die Teile an (langsamer, gleiche Regeln).

Medien-Token: POST /api/chat/files/{fid}/token -> kurzlebiges Token (Kopf X-Saga-Media-Token) fuer genau diese Datei und
diese Sitzung. Die Wiedergabe haengt so nicht am wechselnden Cookie (Service Worker, AVAssetResourceLoader, Media3,
GStreamer). Jede Abfrage prueft das Konto neu; saga-media merkt sich eine Erlaubnis hoechstens 60 s - Entzug (Datei
geloescht, Mitglied entfernt, Passwort geaendert, Geraet abgemeldet) wirkt also spaetestens nach 60 s."""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from db import connect as _db
from saga import chat as CH, file_v2
from saga.core import MEDIA_KEY, SECRET, _api_guard, _bad, _body, device_alive, t, user_info
from saga.notes import FILE_MAX

router = APIRouter()

TOKEN_TTL = 15 * 60                    # Medien-Token: so lange gueltig (die App holt rechtzeitig ein neues)
_TOKEN_KEY = hashlib.sha256(b"saga-media-token/v1\0" + SECRET.encode()).digest()
_CRANGE = re.compile(r"bytes (\d{1,15})-(\d{1,15})/(\d{1,15})")
_locks = {}                            # Upload-Kennung -> asyncio.Lock (nur Python-Weg)


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64u(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- Medien-Token ---------------------------------------------------------------------------------------------------
def make_token(request, uid, fid, now=None):
    """Token fuer (Datei, Sitzung): Konto, Passwort-Stand, Anmeldung (lo) und Geraete-Anmeldung (did) gehen ein."""
    exp = int((now or time.time()) + TOKEN_TTL)
    s = request.session
    payload = json.dumps([fid, uid, float(s.get("pwc") or 0), str(s.get("lo") or ""), s.get("did"), exp], separators=(",", ":")).encode()
    mac = hmac.new(_TOKEN_KEY, payload, hashlib.sha256).digest()[:24]
    return "m1." + _b64u(payload) + "." + _b64u(mac), exp


def token_user(tok, fid):
    """Token pruefen -> Benutzername oder None. Prueft Signatur, Datei, Ablauf und den aktuellen Stand des Kontos."""
    try:
        v, p, m = str(tok).split(".")
        if v != "m1" or len(tok) > 600:
            return None
        payload, mac = _unb64u(p), _unb64u(m)
        if not hmac.compare_digest(hmac.new(_TOKEN_KEY, payload, hashlib.sha256).digest()[:24], mac):
            return None
        f, uid, pwc, _lo, did, exp = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if f != fid or not isinstance(exp, int) or exp < time.time() or not isinstance(uid, str):
        return None
    info = user_info(uid)
    if info is None or info["epoch"] > float(pwc or 0):        # gesperrt/geloescht oder Passwort seitdem geaendert
        return None
    if did is not None and not device_alive(uid, did):          # Geraet abgemeldet
        return None
    return uid


CH.TOKEN_USER = token_user


@router.post("/api/chat/files/{fid}/token")
def api_file_token(fid: str, request: Request):
    """-> {token, expires, ttl}: Medien-Token fuer lange Wiedergaben (Kopf X-Saga-Media-Token beim Abruf)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if request.headers.get("x-saga-media-token"):
        return _bad("not found", 404)
    err, _, _ = CH._file_access(fid, request, {})
    if err:
        return err
    tok, exp = make_token(request, uid, fid)
    return CH._no_store({"token": tok, "expires": exp, "ttl": TOKEN_TTL})


# --- Uploads anlegen --------------------------------------------------------------------------------------------------
def _cipher_max():
    return file_v2.cipher_size(CH.video_max())


@router.post("/api/chats/{cid}/uploads")
async def api_upload_new(cid: int, request: Request):
    """{size: Bytes des Blobs (Kopf + Bloecke), fmt: 2} -> {upload, offset, size, part_max, expires, url}."""
    uid, err = CH._me(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    size, fmt = b.get("size"), b.get("fmt")
    if fmt != 2 or not isinstance(size, int) or isinstance(size, bool) or size < file_v2.cipher_size(0):
        return _bad(t("ungültige Angabe"))
    if file_v2.cipher_size(file_v2.plain_max_for(size)) != size:
        return _bad(t("ungültige Angabe"))                     # keine moegliche Groesse eines saga-file/v2-Blobs
    if size > _cipher_max():
        return _bad(t("Video zu groß (höchstens {n} MB).", n=CH.video_max() // 1048576), 413)
    if not CH._rate("file", uid, CH.FILE_RATE):
        return _bad(t("zu schnell"), 429)
    now = time.time()
    with _db() as c, c:
        ch, _m, _mem, err = CH._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if CH.is_fed(ch) and size > FILE_MAX + 64 and not CH.FED.big_files_ok(c, ch):
            return _bad(t("Der Server des Gegenübers kann große Videos noch nicht (höchstens {n} MB).", n=FILE_MAX // 1048576), 409)
        c.execute("SELECT 1 FROM users WHERE username=? FOR UPDATE", (uid,))     # parallele Anlagen je Konto nacheinander
        if c.execute("SELECT COUNT(*) FROM chat_upload WHERE uid=? AND expires>?", (uid, now)).fetchone()[0] >= CH.UPLOADS_PAR:
            return _bad(t("Höchstens {n} Uploads gleichzeitig – bitte warten, bis einer fertig ist.", n=CH.UPLOADS_PAR), 429)
        if CH.used_bytes(c, uid) + size > CH.quota():
            return _bad(t("Speicher für Chat-Anhänge voll ({n} MB).", n=CH.quota() // 1048576), 413)
        upid = secrets.token_urlsafe(18)
        c.execute("INSERT INTO chat_upload (id, uid, chat_id, size, received, fmt, created, expires) VALUES (?,?,?,?,0,2,?,?)",
                  (upid, uid, ch["id"], size, now, now + CH.UPLOAD_TTL))
    print(f"chat: Upload angelegt (Chat {ch['id']}, {size} Bytes)", flush=True)
    return CH._no_store({"upload": upid, "offset": 0, "size": size, "part_max": CH.UPLOAD_PART, "expires": now + CH.UPLOAD_TTL,
                         "url": "/api/chat/uploads/" + upid})


def _upload_of(upid, uid):
    """Eigenen, noch offenen Upload laden -> dict oder None."""
    if not CH._FID.fullmatch(upid or ""):
        return None
    with _db() as c:
        r = c.execute("SELECT id, uid, chat_id, size, expires FROM chat_upload WHERE id=?", (upid,)).fetchone()
        if not r or r[1] != uid or r[4] < time.time() or not CH._member(c, r[2], uid):
            return None
    return {"id": r[0], "uid": r[1], "chat_id": r[2], "size": r[3], "expires": r[4]}


def _offset(upid):
    try:
        return os.stat(os.path.join(CH.UP_DIR, upid)).st_size
    except OSError:
        return 0


def finish(upid, uid):
    """Upload abschliessen (Datei vollstaendig): Kopf pruefen, als Anhang (fmt=2) eintragen -> (status, dict)."""
    path = os.path.join(CH.UP_DIR, upid)
    with _db() as c, c:
        r = c.execute("SELECT uid, chat_id, size FROM chat_upload WHERE id=? FOR UPDATE", (upid,)).fetchone()
        if not r:
            f = c.execute("SELECT size FROM chat_file WHERE id=? AND uid=? AND fmt=2", (upid, uid)).fetchone()
            return (200, {"id": upid, "size": f[0], "fmt": 2, "offset": f[0], "done": True}) if f else (404, {"error": "not found"})
        if r[0] != uid:
            return 404, {"error": "not found"}
        size = r[2]
        try:
            with open(path, "rb") as fh:
                head = fh.read(file_v2.HEADER)
                got = os.fstat(fh.fileno()).st_size
        except OSError:
            head, got = b"", 0
        if got != size:
            return 409, {"error": t("Upload unvollständig."), "offset": got}
        if file_v2.check_blob(head, size) is None:
            c.execute("DELETE FROM chat_upload WHERE id=?", (upid,))
            CH._rm_uploads(c, [upid])
            return 400, {"error": t("Ungültige Datei (saga-file/v2).")}
        os.makedirs(CH.CHAT_DIR, mode=0o700, exist_ok=True)
        os.replace(path, os.path.join(CH.CHAT_DIR, upid))
        c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created, fmt) VALUES (?,?,?,?,?,2)", (upid, r[1], uid, size, time.time()))
        c.execute("DELETE FROM chat_upload WHERE id=?", (upid,))
    print(f"chat: Upload fertig (Chat {r[1]}, {size} Bytes, saga-file/v2)", flush=True)
    return 200, {"id": upid, "size": size, "fmt": 2, "offset": size, "done": True}


@router.delete("/api/chat/uploads/{upid}")
def api_upload_del(upid: str, request: Request):
    uid, err = CH._me(request)
    if err:
        return err
    up = _upload_of(upid, uid)
    if not up:
        return _bad("not found", 404)
    with _db() as c, c:
        c.execute("DELETE FROM chat_upload WHERE id=?", (upid,))
        CH._rm_uploads(c, [upid])
    return CH._no_store({"ok": True})


# --- Fuer saga-media (gleicher Container, Geheimnis im Kopf; /internal/ ist von aussen gesperrt) -----------------------
def _internal_ok(request):
    return bool(MEDIA_KEY) and secrets.compare_digest(request.headers.get("x-saga-media-key", ""), MEDIA_KEY)


@router.get("/internal/media/upload")
def api_media_upload(request: Request, id: str = ""):
    """Darf diese Anmeldung in den Upload schreiben? -> {ok, size, part_max, ttl} (saga-media merkt es sich hoechstens 60 s)."""
    if not _internal_ok(request):
        return _bad("not found", 404)
    uid = _api_guard(request)
    if not uid or not CH.enabled():
        return _bad("unauthorized", 401)
    up = _upload_of(id, uid)
    if not up:
        return _bad("not found", 404)
    return CH._no_store({"ok": True, "size": up["size"], "part_max": CH.UPLOAD_PART, "ttl": min(CH.MEDIA_AUTHZ_TTL, max(0, int(up["expires"] - time.time())))})


@router.post("/internal/media/upload-done")
def api_media_upload_done(request: Request, id: str = ""):
    """saga-media hat das letzte Byte geschrieben -> Ergebnis wie beim Abschluss (wird an die App weitergereicht)."""
    if not _internal_ok(request):
        return _bad("not found", 404)
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    status, d = finish(id, uid)
    return CH._no_store(d, status)


# --- Ohne saga-media: Python nimmt die Teile an (gleiche Regeln) ------------------------------------------------------
def _up_headers(up, off):
    return {"Upload-Offset": str(off), "Upload-Length": str(up["size"]), "Cache-Control": "no-store"}


@router.head("/api/chat/uploads/{upid}")
def api_upload_head(upid: str, request: Request):
    uid, err = CH._me(request)
    if err:
        return Response(status_code=err.status_code, headers={"Cache-Control": "no-store"})
    up = _upload_of(upid, uid)
    if not up:
        # schon abgeschlossen (App nach dem letzten Teil beendet, Antwort verpasst): Anhang-Kennung = Upload-Kennung
        with _db() as c:
            f = c.execute("SELECT size FROM chat_file WHERE id=? AND uid=? AND fmt=2", (upid, uid)).fetchone() if CH._FID.fullmatch(upid) else None
        if f:
            return Response(status_code=204, headers={"Upload-Offset": str(f[0]), "Upload-Length": str(f[0]), "Upload-Done": "1",
                                                      "Cache-Control": "no-store"})
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(status_code=204, headers=_up_headers(up, _offset(upid)))


def parse_crange(v):
    """'bytes a-b/size' -> (a, b, size) oder None."""
    m = _CRANGE.fullmatch((v or "").strip())
    if not m:
        return None
    a, b, n = (int(x) for x in m.groups())
    return (a, b, n) if a <= b < n else None


@router.put("/api/chat/uploads/{upid}")
async def api_upload_put(upid: str, request: Request):
    uid, err = CH._me(request)
    if err:
        return err
    up = await run_in_threadpool(_upload_of, upid, uid)
    if not up:
        return _bad("not found", 404)
    cr = parse_crange(request.headers.get("content-range"))
    if not cr or cr[2] != up["size"]:
        return _bad(t("ungültige Angabe") + " (Content-Range)", 400)
    a, b, _ = cr
    n = b - a + 1
    if n > CH.UPLOAD_PART:
        return _bad(t("Teil zu groß (höchstens {n} MB).", n=CH.UPLOAD_PART // 1048576), 413)
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.isdigit() or int(cl) != n):
        return _bad(t("ungültige Angabe") + " (Content-Length)", 400)
    lock = _locks.setdefault(upid, asyncio.Lock())
    if lock.locked():
        return CH._no_store({"error": t("Dieser Upload läuft schon."), "offset": _offset(upid)}, 409)
    try:
        async with lock:
            off = _offset(upid)
            if a != off:
                return _json({"error": t("Falsche Stelle – weiter ab {n}.", n=off), "offset": off}, 409, _up_headers(up, off))
            os.makedirs(CH.UP_DIR, mode=0o700, exist_ok=True)
            fd = os.open(os.path.join(CH.UP_DIR, upid), os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            got = 0
            try:
                async for chunk in request.stream():
                    if got + len(chunk) > n:
                        chunk = chunk[:n - got]
                    if chunk:
                        os.write(fd, chunk)
                        got += len(chunk)
                    if got >= n:
                        break
            except ClientDisconnect:
                pass                                   # Abbruch: Geschriebenes bleibt, HEAD liefert den Stand
            finally:
                os.close(fd)
            off = _offset(upid)
            if got < n:
                return _json({"error": t("Upload unvollständig."), "offset": off}, 400, _up_headers(up, off))
            if off < up["size"]:
                return _json({"offset": off, "done": False}, 200, _up_headers(up, off))
            status, d = await run_in_threadpool(finish, upid, uid)
            return _json(d, status, {"Upload-Offset": str(d.get("offset", off)), "Cache-Control": "no-store"})
    finally:
        if not lock.locked():
            _locks.pop(upid, None)


def _json(d, status, headers):
    return JSONResponse(d, status_code=status, headers=headers)
