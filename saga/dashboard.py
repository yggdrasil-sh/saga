"""Startseite: Dienste (Admin + LDAP), Status-Pruefung, Favoriten, Konto-Infos, Lesezeichen-Daten."""
import asyncio
import json
import os
import ssl
import time
from urllib.parse import urlparse
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import auth
from db import connect as _db
from fastapi import APIRouter
from saga import i18n
from saga.core import ADMIN_URL, ASSET_V, CACHE_TTL, CA_FILE, SEARXNG_URL, SEC_HEADERS, STATUS_INTERVAL, TZ, _api_guard, _session_user, t, title, tmpl, user_info

router = APIRouter()


# --- Dienste (Admin) und Lesezeichen (je Benutzer) ---------------------------------------------------
_svc_cache = {"t": 0.0, "data": {}}


def services_changed():
    """Vom Admin-Bereich aufgerufen: Dienste sofort neu laden."""
    _svc_cache["t"] = 0.0


def all_services():
    """Alle Dienste (Schluessel -> Dienst), CACHE_TTL Sekunden zwischengespeichert.
    Schluessel = Zugangsgruppe (Kennung + slug), damit bleiben Favoriten beim Umbenennen erhalten."""
    if time.time() - _svc_cache["t"] < CACHE_TTL:
        return _svc_cache["data"]
    st = auth.settings()
    prefix = (st.get("group_prefix") or "").strip().lower()
    data = {}
    if st["ldap"].get("enabled") and st["ldap"].get("services_from_description"):
        try:
            data.update(auth.ldap_services(prefix or "s_"))
        except Exception as ex:  # noqa: BLE001 - LDAP gerade weg: bisherige LDAP-Dienste behalten
            print(f"ldap-services: {type(ex).__name__}: {ex}", flush=True)
            data.update({k: v for k, v in _svc_cache["data"].items() if v.get("source") == "ldap"})
    with _db() as c:
        rows = c.execute("SELECT id, slug, name, url, icon, category, extra_groups, everyone, check_status "
                         "FROM service ORDER BY sort, name").fetchall()
    for r in rows:
        key = prefix + r[1]
        try:
            extra = [str(g).lower() for g in json.loads(r[6] or "[]") if str(g).strip()]
        except ValueError:
            extra = []
        data[key] = {"key": key, "id": r[0], "slug": r[1], "groups": [key] + extra, "everyone": bool(r[7]),
                     "name": r[2], "url": r[3], "host": urlparse(r[3]).netloc, "icon": r[4] or "",
                     "category": r[5] or "Dienste", "source": "local", "check": bool(r[8])}
    _svc_cache.update(t=time.time(), data=data)
    return data


def visible_services(info):
    g = info["groups"]
    return {k: s for k, s in all_services().items() if s.get("everyone") or g.intersection(s["groups"])}


BOOKMARK_MAX = 500


def bookmarks(uid):
    with _db() as c:
        rows = c.execute("SELECT id, name, url, icon, category FROM bookmark WHERE uid=? ORDER BY sort, id", (uid,)).fetchall()
    return [{"key": f"bm{r[0]}", "id": r[0], "name": r[1], "url": r[2], "host": urlparse(r[2]).netloc, "icon": r[3] or "",
             "category": r[4] or "Lesezeichen", "own": True, "check": False} for r in rows]


def visible_tiles(uid, info=None):
    """Dienste + eigene Lesezeichen (Schluessel -> Kachel)."""
    info = info or user_info(uid)
    if info is None:
        return {}
    out = dict(visible_services(info))
    for b in bookmarks(uid):
        out[b["key"]] = b
    return out


# --- Status-Checks (nur Admin-Dienste mit Statuspruefung - nie Lesezeichen: der Server ruft keine
#     Adressen auf, die Benutzer selbst eintragen) ------------------------------------------------------
_status = {}  # key -> {"state": up|warn|down, "code": int|None, "ms": int|None}


async def _status_loop():
    tls = ssl.create_default_context(cafile=CA_FILE) if os.path.isfile(CA_FILE) else ssl.create_default_context()
    async with httpx.AsyncClient(timeout=6, follow_redirects=False, verify=tls,
                                 headers={"User-Agent": "Saga-Status/1"}) as cl:
        while True:
            try:
                svcs = {k: s for k, s in (await asyncio.to_thread(all_services)).items() if s.get("check")}

                async def one(key, url):
                    t0 = time.monotonic()
                    try:
                        r = await cl.get(url)
                        ms = int((time.monotonic() - t0) * 1000)
                        state = "down" if r.status_code >= 500 else ("warn" if ms > 2500 else "up")
                        _status[key] = {"state": state, "code": r.status_code, "ms": ms}
                    except Exception:  # Timeout, TLS, DNS ...
                        _status[key] = {"state": "down", "code": None, "ms": None}

                await asyncio.gather(*(one(k, s["url"]) for k, s in svcs.items()))
                for k in list(_status):
                    if k not in svcs:
                        _status.pop(k, None)
            except Exception as ex:  # DB/LDAP weg o.ae. - naechste Runde
                print(f"status-loop: {type(ex).__name__}: {ex}", flush=True)
            await asyncio.sleep(STATUS_INTERVAL)


# --- Favoriten --------------------------------------------------------------
def favs(uid):
    with _db() as c:
        return [r[0] for r in c.execute("SELECT grp FROM fav WHERE uid=? ORDER BY ts", (uid,))]


def toggle_fav(uid, grp):
    with _db() as c, c:
        if c.execute("DELETE FROM fav WHERE uid=? AND grp=?", (uid, grp)).rowcount:
            return False
        c.execute("INSERT INTO fav VALUES (?,?,?)", (uid, grp, time.time()))
        return True


# --- Account ----------------------------------------------------------------
def source_label(source):
    return {"local": t("Lokales Konto"), "ldap": t("Verzeichnis (LDAP/AD)"), "oidc": t("Single Sign-On (OIDC)")}.get(source, source)


def _fmt_day(ts):
    return i18n.fmt_date(ts, TZ) if ts else None


def account_ctx(info):
    return {
        "mail": info["mail"], "source": source_label(info["source"]),
        "local": info["source"] == "local", "pw_changed": _fmt_day(info["pw_changed"]),
        "last_login": _fmt_day(info["last_login"]), "groups": sorted(info["groups"]),
        "admin_url": ADMIN_URL if info["is_admin"] else "",
    }


@router.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request):
    """Eigene Seite fuer die Notizen (Seitenbaum + Editor im ganzen Fenster)."""
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login?next=/notes", status_code=303)
    # ?embed=1: schlanke Ansicht fuer das Peek-Fenster - NUR diese Variante darf eingebettet werden,
    # und nur von Saga selbst (frame-ancestors 'self'); alles andere bleibt 'none'.
    embed = request.query_params.get("embed") == "1"
    html = tmpl.get_template("notes.html").render(title=title(), name=request.session.get("name", uid), v=ASSET_V, embed=embed,
                                                  admin_url=ADMIN_URL if user_info(uid)["is_admin"] else "")
    headers = dict(SEC_HEADERS)
    if embed:
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return HTMLResponse(html, headers=headers)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    info = user_info(uid)
    mine = visible_tiles(uid, info)
    fav_keys = [k for k in favs(uid) if k in mine]
    cats = {}
    for s in sorted(mine.values(), key=lambda s: (s["category"].lower(), s["name"].lower())):
        if s["key"] not in fav_keys:
            cats.setdefault(s["category"], []).append(s)
    html = tmpl.get_template("index.html").render(
        title=title(), name=request.session.get("name", uid), uid=uid,
        favorites=[mine[k] for k in fav_keys], categories=cats, acct=account_ctx(info),
        search_enabled=bool(SEARXNG_URL), v=ASSET_V,
    )
    return HTMLResponse(html, headers=SEC_HEADERS)


@router.get("/api/status")
def api_status(request: Request):
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    vis = visible_services(user_info(uid))
    return JSONResponse({k: v for k, v in _status.items() if k in vis},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/fav")
async def api_fav(request: Request):
    uid = _api_guard(request)
    if not uid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        grp = str((await request.json()).get("group", "")).lower()
    except Exception:
        return JSONResponse({"error": "bad request"}, status_code=400)
    if grp not in await asyncio.to_thread(visible_tiles, uid):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"fav": await asyncio.to_thread(toggle_fav, uid, grp)})
