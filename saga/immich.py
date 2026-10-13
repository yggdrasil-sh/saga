"""Immich: eigene Fotos als Bilder, Titelbilder und Symbole."""
import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import JSONResponse, Response
import auth
from db import connect as _db
from fastapi import APIRouter
from saga.calendars import _admin_hosts, _cal_fernet
from saga.core import E, _api_guard, _bad, _body, t
from saga.files import _sniff_image
from saga.notes import FILES_DIR, FILE_MAX, FILE_QUOTA

router = APIRouter()


# --- Immich (eigene Fotos als Bilder, Titelbilder, Icons) ----------------------------
# Jede Person traegt IHRE Immich-Adresse + IHREN API-Schluessel ein (verschluesselt wie Kalender-Passwoerter).
# Schutz gegen SSRF (Server ruft benutzerdefinierte Adressen auf):
#  - oeffentliche Adressen: nur https; interne Adressen (privat/loopback/link-local ...) nur, wenn der Admin
#    den Host freigibt (IMMICH_URL = Vorschlag und automatisch frei, weitere in IMMICH_ALLOWED_HOSTS)
#  - geprueft wird die IP der TATSAECHLICHEN Verbindung (nicht nur der Name) -> auch DNS-Tricks werden abgefangen
#  - keine Weiterleitungen, Groessen- und Zeitlimits
# Ausgewaehlte Bilder werden nach Saga KOPIERT (Titelbilder muessen eigene Dateien sein; Seiten bleiben heil).
IMMICH_URL = (E.get("IMMICH_URL") or "").rstrip("/")        # Vorschlag fuer das Adressfeld
_IMMICH_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_immich_local = __import__("threading").local()


def _immich_allowed_hosts():
    hosts = set(_admin_hosts())
    if IMMICH_URL:
        hosts.add(urlparse(IMMICH_URL).netloc.lower())
    return hosts


def _immich_norm(url):
    """Adresse vereinheitlichen -> (basis, fehler). Erlaubt: https://host[:port][/pfad], http nur fuer freigegebene Hosts."""
    u = str(url or "").strip().rstrip("/")
    if u.endswith("/api"):
        u = u[:-4]
    if len(u) > 300:
        return None, t("Adresse zu lang")
    try:
        pu = urlparse(u)
    except ValueError:
        return None, t("Adresse ungueltig")
    if pu.scheme not in ("http", "https") or not pu.hostname or pu.username or pu.password or pu.query or pu.fragment:
        return None, t("Adresse ungueltig (z. B. https://immich.example.de)")
    freigegeben = pu.netloc.lower() in _immich_allowed_hosts()
    if pu.scheme == "http" and not freigegeben:
        return None, t("Nur https-Adressen erlaubt (http nur fuer vom Admin freigegebene interne Server)")
    return f"{pu.scheme}://{pu.netloc}{pu.path}", None


def _immich_ip_ok(ip, netloc):
    """Interne Ziele nur fuer vom Admin freigegebene Hosts."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return a.is_global or auth.settings().get("allow_private_hosts") or netloc.lower() in _immich_allowed_hosts()


def _immich_db(c):
    c.execute("CREATE TABLE IF NOT EXISTS immich_key (uid TEXT PRIMARY KEY, secret TEXT NOT NULL, name TEXT, created REAL)")
    if "base_url" not in [r[1] for r in c.execute("PRAGMA table_info(immich_key)")]:
        c.execute("ALTER TABLE immich_key ADD COLUMN base_url TEXT")


def _immich_key(uid):
    """-> (adresse, schluessel) oder None"""
    with _db() as c:
        _immich_db(c)
        r = c.execute("SELECT secret, base_url FROM immich_key WHERE uid=?", (uid,)).fetchone()
    if not r:
        return None
    try:
        return (r[1] or IMMICH_URL, _cal_fernet().decrypt(r[0].encode()).decode())
    except Exception:
        return None


def _immich(conn, method, path, body=None, params=None, max_bytes=2 * 1024 * 1024):
    """Ein Aufruf an Immich -> (status, content_type, bytes). conn = (adresse, schluessel)."""
    import httpx
    base, key = conn
    netloc = urlparse(base).netloc
    if not base or not _immich_norm(base)[0]:
        return 400, "", b""
    # 1) VOR dem Verbinden: alle Adressen des Namens pruefen - interne Ziele ohne Freigabe gar nicht erst anfassen
    import socket
    pu = urlparse(base)
    try:
        infos = socket.getaddrinfo(pu.hostname, pu.port or (443 if pu.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return 502, "", b""
    if not infos or not all(_immich_ip_ok(i[4][0], netloc) for i in infos):
        return 403, "", b""
    cl = getattr(_immich_local, "client", None)
    if cl is None:
        cl = _immich_local.client = httpx.Client(timeout=15, follow_redirects=False)
    try:
        # 2) NACH dem Verbinden: IP der tatsaechlichen Verbindung (gegen Umstellen des Namens dazwischen)
        with cl.stream(method, base + "/api" + path, params=params, json=body,
                       headers={"x-api-key": key, "Accept": "application/json, image/*"}) as r:
            ns = r.extensions.get("network_stream")                 # IP der tatsaechlichen Verbindung pruefen
            peer = ns.get_extra_info("server_addr") if ns else None
            if not peer or not _immich_ip_ok(peer[0], netloc):
                _immich_local.client = None                          # Verbindung nicht weiterverwenden
                return 403, "", b""
            if r.status_code in (301, 302, 303, 307, 308):
                return 502, "", b""
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    return 413, "", b""
            return r.status_code, r.headers.get("content-type", ""), bytes(buf)
    except httpx.HTTPError:
        _immich_local.client = None
        return 502, "", b""


def _immich_guard(request):
    uid = _api_guard(request)
    if not uid:
        return None, None, _bad("unauthorized", 401)
    conn = _immich_key(uid)
    if not conn:
        return uid, None, _bad(t("Immich nicht verbunden (Einstellungen → Immich)"), 409)
    return uid, conn, None


@router.get("/api/settings/immich")
def api_immich_settings(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _immich_db(c)
        r = c.execute("SELECT name, base_url FROM immich_key WHERE uid=?", (uid,)).fetchone()
    return JSONResponse({"available": True, "connected": bool(r), "name": r[0] if r else None,
                         "url": (r[1] or IMMICH_URL) if r else None, "suggest": IMMICH_URL or None})


@router.post("/api/settings/immich")
async def api_immich_connect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    base, err = _immich_norm(b.get("url") or IMMICH_URL)
    if err:
        return _bad(err)
    key = str(b.get("apikey") or "").strip()
    if not key or len(key) > 200 or not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        return _bad(t("API-Schluessel fehlt oder ist ungueltig"))
    st, _, data = await asyncio.to_thread(_immich, (base, key), "GET", "/users/me")
    if st == 403 and not data:
        return _bad(t("Diese Adresse zeigt auf einen internen Server, der nicht freigegeben ist"), 400)
    if st in (401, 403):
        return _bad(t("Immich lehnt den Schluessel ab - Rechte: album.read, asset.read, asset.view, asset.download, user.read"), 400)
    if st != 200:
        return _bad(t("Immich unter dieser Adresse nicht erreichbar"), 502)
    try:
        me = json.loads(data)
        name = str(me.get("name") or me.get("email") or "")[:120]
    except ValueError:
        return _bad(t("Unter dieser Adresse antwortet kein Immich"), 502)
    with _db() as c, c:
        _immich_db(c)
        c.execute("INSERT OR REPLACE INTO immich_key (uid, secret, name, created, base_url) VALUES (?,?,?,?,?)",
                  (uid, _cal_fernet().encrypt(key.encode()).decode(), name, time.time(), base))
    return JSONResponse({"ok": True, "name": name, "url": base})


@router.delete("/api/settings/immich")
def api_immich_disconnect(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _immich_db(c)
        c.execute("DELETE FROM immich_key WHERE uid=?", (uid,))
    return JSONResponse({"ok": True})


@router.get("/api/immich/albums")
def api_immich_albums(request: Request):
    uid, key, err = _immich_guard(request)
    if err:
        return err
    st, _, data = _immich(key, "GET", "/albums")
    if st != 200:
        return _bad(t("Immich: Alben nicht lesbar"), 502)
    out = [{"id": a.get("id"), "name": str(a.get("albumName") or "")[:200], "count": a.get("assetCount") or 0,
            "thumb": a.get("albumThumbnailAssetId")} for a in json.loads(data) if isinstance(a, dict)]
    out.sort(key=lambda a: a["name"].lower())
    return JSONResponse({"albums": out})


@router.post("/api/immich/assets")
async def api_immich_assets(request: Request):
    """Fotos: Zeitleiste (neueste zuerst), ein Album, oder Suche nach Bildinhalt (q)."""
    uid, key, err = _immich_guard(request)
    if err:
        return err
    b = await _body(request) or {}
    page = max(1, min(500, int(b.get("page") or 1)))
    q = str(b.get("q") or "").strip()[:200]
    album = str(b.get("album") or "")
    body = {"page": page, "size": 60, "type": "IMAGE"}
    if album:
        if not _IMMICH_ID.match(album):
            return _bad(t("ungueltiges Album"))
        body["albumIds"] = [album]
    if q:
        body["query"] = q
        st, _, data = await asyncio.to_thread(_immich, key, "POST", "/search/smart", body, None, 8 * 1024 * 1024)
    else:
        body["order"] = "desc"
        st, _, data = await asyncio.to_thread(_immich, key, "POST", "/search/metadata", body, None, 8 * 1024 * 1024)
    if st != 200:
        return _bad(t("Immich: Suche fehlgeschlagen"), 502)
    a = (json.loads(data).get("assets") or {})
    items = [{"id": x.get("id"), "name": str(x.get("originalFileName") or "")[:200], "mime": x.get("originalMimeType"),
              "date": x.get("fileCreatedAt")} for x in a.get("items") or [] if isinstance(x, dict) and _IMMICH_ID.match(str(x.get("id") or ""))]
    return JSONResponse({"items": items, "next": page + 1 if a.get("nextPage") else None})


@router.get("/api/immich/thumb/{aid}")
def api_immich_thumb(aid: str, request: Request):
    uid, key, err = _immich_guard(request)
    if err:
        return err
    if not _IMMICH_ID.match(aid):
        return _bad(t("ungueltig"))
    st, ct, data = _immich(key, "GET", f"/assets/{aid}/thumbnail", params={"size": "thumbnail"})
    img = _sniff_image(data[:16]) if st == 200 else None
    if not img:
        return _bad(t("Vorschau nicht verfuegbar"), 404)
    return Response(content=data, media_type=img, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/api/immich/import")
async def api_immich_import(request: Request):
    """Foto aus Immich als eigene Datei uebernehmen -> /files/<id>. GIFs im Original (Animation), sonst Vorschaugroesse."""
    uid, key, err = _immich_guard(request)
    if err:
        return err
    b = await _body(request) or {}
    aid = str(b.get("id") or "")
    if not _IMMICH_ID.match(aid):
        return _bad(t("ungueltig"))
    st, _, info = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}")
    if st != 200:
        return _bad(t("Foto nicht gefunden"), 404)
    meta = json.loads(info)
    mime = str(meta.get("originalMimeType") or "")
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", str(meta.get("originalFileName") or "immich"))[:120] or "immich"
    if mime == "image/gif" or b.get("original"):
        st, _, data = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}/original", None, None, FILE_MAX)
    else:
        st, _, data = await asyncio.to_thread(_immich, key, "GET", f"/assets/{aid}/thumbnail", None, {"size": "preview"}, FILE_MAX)
        name = name.rsplit(".", 1)[0] + ".jpg"
    if st == 413:
        return _bad(t("Bild zu gross (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    img = _sniff_image(data[:16]) if st == 200 else None
    if not img:
        return _bad(t("kein unterstuetztes Bild (PNG, JPEG, GIF, WebP)"), 415)
    if img != "image/gif" and name.endswith(".jpg") and img == "image/webp":
        name = name[:-4] + ".webp"
    with _db() as c, c:
        used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (uid,)).fetchone()[0]
        if used + len(data) > FILE_QUOTA:
            return _bad(t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576), 507)
        fid = os.urandom(12).hex()
        with open(os.path.join(FILES_DIR, fid), "wb") as f:
            f.write(data)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (fid, uid, name, img, len(data), 1, time.time()))
    return JSONResponse({"url": f"/files/{fid}", "name": name, "size": len(data), "mime": img})
