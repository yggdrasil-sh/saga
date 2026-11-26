"""Eigene Lesezeichen und Websuche (SearXNG)."""
import re
import time
from urllib.parse import urlparse
import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from db import connect as _db
from fastapi import APIRouter
from saga.core import RESTORE_TEST, SEARCH_LANGUAGE, SEARCH_SAFESEARCH, SEARXNG_URL, _api_guard, _bad, _body, _session_user, t
from saga.dashboard import BOOKMARK_MAX, LOGO_MIME, LOGO_RE, bookmarks

router = APIRouter()


# --- Eigene Lesezeichen ---------------------------------------------------------------------------
_ICON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


LOGO_MAX = 2 * 1024 * 1024            # 2.6 (Bugs-DB 1530): eigenes Logo hoechstens 2 MB (Upload selbst: ATTACH_MAX_MB)


def logo_error(c, uid, icon):
    """2.6 (Bugs-DB 1530): Logo /files/<id> - wie bei Seiten-Symbolen (pages._own_image) nur ein EIGENES, am Inhalt als
    Bild erkanntes Upload, dazu nur Rasterbilder (PNG, JPEG, GIF, WebP - kein SVG) bis LOGO_MAX. -> Fehlertext oder None."""
    m = LOGO_RE.fullmatch(icon)
    r = m and c.execute("SELECT is_image, mime, size FROM file WHERE id=? AND uid=?", (m.group(1), uid)).fetchone()
    if not r or not r[0] or r[1] not in LOGO_MIME:
        return t("Logo: nur ein eigenes hochgeladenes Bild (PNG, JPEG, GIF oder WebP).")
    if r[2] > LOGO_MAX:
        return t("Logo zu groß (max. {n} MB).", n=LOGO_MAX // 1048576)
    return None


def _bm_input(b, cur=None, uid=None):
    """-> (werte, fehler). cur = bisherige Werte (PATCH). Symbol: Name aus selfh.st oder (2.6) eigenes Logo /files/<id>
    - unveraendert zurueckgeschickt wird es nicht erneut geprueft (Apps schicken beim Bearbeiten das Feld icon mit)."""
    cur = cur or {}
    name = str(b.get("name", cur.get("name", "")) or "").strip()[:80]
    url = str(b.get("url", cur.get("url", "")) or "").strip()[:500]
    icon = str(b.get("icon", cur.get("icon", "")) or "").strip().lower()[:64]
    cat = re.sub(r"\s+", " ", str(b.get("category", cur.get("category", "")) or "").strip())[:40] or "Lesezeichen"
    if url and "://" not in url:
        url = "https://" + url
    pu = urlparse(url)
    try:
        pu.port                                   # "https://javascript:alert(1)" -> ungueltiger Port
        host_ok = bool(pu.hostname) and re.fullmatch(r"[a-z0-9.\-]+|[0-9a-f:.]+", pu.hostname or "")
    except ValueError:
        host_ok = False
    if pu.scheme not in ("http", "https") or not host_ok or pu.username or pu.password:
        return None, t("Bitte eine Adresse mit http:// oder https:// angeben.")
    if not name:
        name = pu.netloc
    if icon.startswith("/files/"):
        if icon != cur.get("icon"):
            with _db() as c:
                err = logo_error(c, uid, icon)
            if err:
                return None, err
    elif icon and not _ICON_RE.fullmatch(icon):
        return None, t("Symbol: nur der Name aus selfh.st/icons (z. B. nextcloud).")
    return {"name": name, "url": url, "icon": icon, "category": cat}, None


@router.get("/api/bookmarks")
def api_bookmarks(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(bookmarks(uid), headers={"Cache-Control": "no-store"})


@router.post("/api/bookmarks")
async def api_bookmark_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return add_bookmark(uid, await _body(request) or {})


def add_bookmark(uid, b):
    """Lesezeichen anlegen (Web/Apps per Sitzung, Teilen-Erweiterung per App-Schluessel: POST /api/app/bookmarks)."""
    v, err = _bm_input(b, uid=uid)
    if err:
        return _bad(err)
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM bookmark WHERE uid=?", (uid,)).fetchone()[0] >= BOOKMARK_MAX:
            return _bad(t("Höchstens {n} Lesezeichen.", n=BOOKMARK_MAX))
        srt = (c.execute("SELECT MAX(sort) FROM bookmark WHERE uid=?", (uid,)).fetchone()[0] or 0) + 1
        bid = c.execute("INSERT INTO bookmark (uid, name, url, icon, category, sort, created) VALUES (?,?,?,?,?,?,?)",
                        (uid, v["name"], v["url"], v["icon"], v["category"], srt, time.time())).lastrowid
    return JSONResponse({"id": bid, "key": f"bm{bid}", **v})


@router.patch("/api/bookmarks/{bid}")
async def api_bookmark_update(bid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = c.execute("SELECT name, url, icon, category FROM bookmark WHERE id=? AND uid=?", (bid, uid)).fetchone()
    if not r:
        return _bad("not found", 404)
    v, err = _bm_input(await _body(request) or {}, dict(zip(("name", "url", "icon", "category"), r)), uid)
    if err:
        return _bad(err)
    with _db() as c, c:
        c.execute("UPDATE bookmark SET name=?, url=?, icon=?, category=? WHERE id=? AND uid=?",
                  (v["name"], v["url"], v["icon"], v["category"], bid, uid))
    return JSONResponse({"id": bid, "key": f"bm{bid}", **v})


@router.delete("/api/bookmarks/{bid}")
def api_bookmark_delete(bid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM bookmark WHERE id=? AND uid=?", (bid, uid)).rowcount
        c.execute("DELETE FROM fav WHERE uid=? AND grp=?", (uid, f"bm{bid}"))
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


@router.get("/api/bookmarks/{bid}/logo")
def api_bookmark_logo(bid: int, request: Request):
    """2.6 (Bugs-DB 1530): eigenes Logo eines Lesezeichens als PNG (laengste Kante files.LOGO_EDGE) - nur fuer das
    eigene Konto (Sitzung/Cookie wie /api/services/icon, auch die Apps). Adresse aus icon_url (?v= wechselt mit dem Bild).
    404: kein Logo, nicht das eigene Lesezeichen, Datei weg oder nicht lesbar (dann Anfangsbuchstabe zeigen)."""
    uid = _session_user(request)
    with _db() as c:
        r = uid and c.execute("SELECT icon FROM bookmark WHERE id=? AND uid=?", (bid, uid)).fetchone()
        m = r and LOGO_RE.fullmatch(r[0] or "")
        f = m and c.execute("SELECT is_image, mime FROM file WHERE id=? AND uid=?", (m.group(1), uid)).fetchone()
    from saga.files import logo_png
    path = logo_png(m.group(1)) if f and f[0] and f[1] in LOGO_MIME else None
    if not path:
        return PlainTextResponse("not found", status_code=404, headers={"Cache-Control": "no-store"})
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=604800",
                                                                "X-Content-Type-Options": "nosniff"})


# --- Websuche (SearXNG) -----------------------------------------------------
_search_client = httpx.AsyncClient(timeout=10, follow_redirects=False,
                                   headers={"User-Agent": "Saga/1"})
_last_search = {}  # uid -> monotonic timestamp (einfaches Ratelimit)


def _http_url(u):
    return isinstance(u, str) and urlparse(u).scheme in ("http", "https")


@router.post("/api/search")
async def api_search(request: Request):
    # POST statt GET: Suchbegriffe landen so nicht im Access-Log (Datenschutz)
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not SEARXNG_URL:
        return JSONResponse({"error": t("Websuche ist nicht eingerichtet")}, status_code=404)
    if RESTORE_TEST:                                     # Testwiederherstellung: nichts nach aussen (saga/backups.py)
        return JSONResponse({"error": t("Suche gerade nicht erreichbar")}, status_code=503)
    try:
        body = await request.json()
        q = str(body.get("q", "")).strip()[:200]
        page = max(1, min(int(body.get("page", 1)), 10))
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if not q:
        return JSONResponse({"error": t("leere Suche")}, status_code=400)
    now = time.monotonic()
    if now - _last_search.get(uid, 0) < 0.7:
        return JSONResponse({"error": t("zu schnell")}, status_code=429)
    _last_search[uid] = now
    try:
        r = await _search_client.post(SEARXNG_URL + "/search", data={
            "q": q, "format": "json", "pageno": page,
            "language": SEARCH_LANGUAGE, "safesearch": SEARCH_SAFESEARCH})
        r.raise_for_status()
        d = r.json()
    except Exception as ex:
        print(f"search: {type(ex).__name__}: {ex}", flush=True)
        return JSONResponse({"error": t("Suche gerade nicht erreichbar")}, status_code=502)

    results = []
    for x in d.get("results") or []:
        if not _http_url(x.get("url")):
            continue  # keine javascript:/data:-Links ins Dashboard
        results.append({
            "title": (x.get("title") or x["url"])[:200],
            "url": x["url"],
            "host": urlparse(x["url"]).netloc,
            "content": (x.get("content") or "")[:400],
            "engines": (x.get("engines") or [x.get("engine")])[:3],
        })
    answers = []
    for a in d.get("answers") or []:
        ans = a if isinstance(a, str) else (a.get("answer") if isinstance(a, dict) else None)
        if ans:
            answers.append(str(ans)[:300])
    return JSONResponse({
        "query": q, "page": page, "results": results[:20],
        "answers": answers[:3],
        "suggestions": [str(s)[:100] for s in (d.get("suggestions") or [])][:6],
        "corrections": [str(s)[:100] for s in (d.get("corrections") or [])][:3],
        "unresponsive": [u[0] for u in (d.get("unresponsive_engines") or []) if u][:5],
    }, headers={"Cache-Control": "no-store"})
