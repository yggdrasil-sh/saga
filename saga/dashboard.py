"""Startseite: Dienste (Admin + LDAP), Status-Pruefung, Favoriten, Konto-Infos, Lesezeichen-Daten."""
import asyncio
import hashlib
import json
import os
import ssl
import time
from urllib.parse import quote, urlparse
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import auth
from db import connect as _db
from fastapi import APIRouter
from saga import avatars, chat, i18n, mfa, publish
from saga.sharing import unread_count
from saga.core import (ADMIN_URL, ASSET_V, CACHE_TTL, CA_FILE, FAVICONS_ENV, ICON_CDN, direct_icons_on, icon_csp, icon_direct, SEARXNG_URL, SEC_HEADERS, STATUS_INTERVAL, TZ, _api_guard, _session_user,
                       remember_ui_lang, std_name, t, title, tmpl, user_info)

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


def favicons_on():
    """Favicons der Seiten laden (saga/siteicons.py): Umgebung FAVICONS und Admin-Einstellung "Favicons laden"."""
    return FAVICONS_ENV and bool(auth.settings().get("favicons", True))


def tile_icon_url(s):
    """Fertige Bildadresse einer Kachel fuer Web und Apps: das gesetzte selfh.st-Symbol, sonst - nur bei Lesezeichen
    (own) - das automatische Symbol (/api/services/favicon/<schluessel>: erst selfh.st nach dem Domainnamen raten, dann
    das Favicon der Seite). Dienste ohne Symbol bekommen nichts (Anfangsbuchstabe, 2.5).
    h = Kurz-Hash des Hosts: aendert sich die Adresse, laden Browser und Apps das Bild neu. Leer = Anfangsbuchstabe."""
    if s.get("icon"):
        return "/api/services/icon/" + s["icon"]
    if not s.get("own"):
        return ""
    try:
        pu = urlparse(s.get("url") or "")
        host = (pu.hostname or "").lower()
        port = pu.port
    except ValueError:
        return ""
    if pu.scheme not in ("http", "https") or not host or not (ICON_CDN or favicons_on()):
        return ""
    h = hashlib.sha256(f"{pu.scheme}://{host}:{port or ''}".encode()).hexdigest()[:10]
    return f"/api/services/favicon/{quote(s['key'], safe='')}?h={h}"


def bookmarks(uid):
    with _db() as c:
        rows = c.execute("SELECT id, name, url, icon, category FROM bookmark WHERE uid=? ORDER BY sort, id", (uid,)).fetchall()
    out = [{"key": f"bm{r[0]}", "id": r[0], "name": r[1], "url": r[2], "host": urlparse(r[2]).netloc, "icon": r[3] or "",
            "category": std_name(r[4] or "Lesezeichen"), "own": True, "check": False} for r in rows]
    on = direct_icons_on()
    for b in out:
        b["icon_url"] = tile_icon_url(b)
        b["icon_direct"] = icon_direct(b["url"], on)     # Rueckfall: Geraet laedt das Favicon direkt (saga.core)
    return out


def visible_tiles(uid, info=None):
    """Dienste + eigene Lesezeichen (Schluessel -> Kachel). Standard-Kategorien in der Sprache der Anfrage
    (Kopien - all_services() ist fuer alle Sprachen gemeinsam zwischengespeichert). icon_url: siehe tile_icon_url."""
    info = info or user_info(uid)
    if info is None:
        return {}
    out = {k: dict(s, category=std_name(s.get("category") or "Dienste")) for k, s in visible_services(info).items()}
    for s in out.values():
        s["icon_url"] = tile_icon_url(s)
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
    if (go := mfa.setup_redirect(request, uid)) is not None:     # Richtlinie: erst den zweiten Faktor einrichten
        return go
    # ?embed=1: schlanke Ansicht fuer das Peek-Fenster - NUR diese Variante darf eingebettet werden,
    # und nur von Saga selbst (frame-ancestors 'self'); alles andere bleibt 'none'.
    embed = request.query_params.get("embed") == "1"
    remember_ui_lang(uid, request)        # Standardnamen auch bei API-Anfragen ohne Browser-Sprache in dieser Sprache
    with _db() as c:
        n_chat = chat.unread_total(c, uid)
    html = tmpl.get_template("notes.html").render(title=title(), name=request.session.get("name", uid), v=ASSET_V, embed=embed,
                                                  chat_enabled=chat.enabled(), chat_unread=n_chat, me_avatar=avatars.my_url(uid),
                                                  admin_url=ADMIN_URL if user_info(uid)["is_admin"] else "")
    headers = icon_csp(SEC_HEADERS, uid)             # Einstellungen -> Lesezeichen: Symbole direkt von der Seite
    if embed:
        headers["Content-Security-Policy"] = headers["Content-Security-Policy"].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return HTMLResponse(html, headers=headers)


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)    # HEAD: Startseite fuer Besucher (Vorschauen, Monitoring)
def index(request: Request):
    uid = _session_user(request)
    if not uid:
        # Besucher ohne Anmeldung: vom Admin gewaehlte oeffentliche Startseite (sonst wie bisher zur Anmeldung)
        return publish.home_response(request) or RedirectResponse("/login", status_code=303)
    if (go := mfa.setup_redirect(request, uid)) is not None:
        return go
    info = user_info(uid)
    remember_ui_lang(uid, request)
    mine = visible_tiles(uid, info)
    fav_keys = [k for k in favs(uid) if k in mine]
    cats = {}
    for s in sorted(mine.values(), key=lambda s: (s["category"].lower(), s["name"].lower())):
        if s["key"] not in fav_keys:
            cats.setdefault(s["category"], []).append(s)
    with _db() as c:
        n_cm = unread_count(c, uid)                  # "Neue Kommentare (n)" am Knopf "Seiten"
        n_chat = chat.unread_total(c, uid)           # Knopf "Chats": ungelesene Nachrichten
    html = tmpl.get_template("index.html").render(
        new_comments=n_cm, chat_enabled=chat.enabled(), chat_unread=n_chat,
        title=title(), name=request.session.get("name", uid), uid=uid,
        favorites=[mine[k] for k in fav_keys], categories=cats, acct=account_ctx(info),
        search_enabled=bool(SEARXNG_URL), v=ASSET_V, me_avatar=avatars.my_url(uid),
    )
    return HTMLResponse(html, headers=icon_csp(SEC_HEADERS, uid))      # img-src: Origins der eigenen Lesezeichen


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
