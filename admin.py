"""Admin-Bereich - eigener Port (Standard 8081), eigene Sitzung, nur fuer Admin-Konten.

Benutzer und Gruppen, Dienste (Kacheln mit automatischer Zuweisung ueber die Gruppen-Kennung),
Registrierung, SSO/OIDC, LDAP/Active Directory, freigegebene interne Hosts.

Sicherheit: eigenes Sitzungs-Cookie (SameSite=strict), CSRF-Token in jedem Formular, kein JavaScript
noetig (nur Rueckfragen), Admin-Rechte werden bei jedem Aufruf neu geprueft. Den Port nicht ins
Internet stellen (docker-compose bindet ihn standardmaessig nur an 127.0.0.1).
"""
import hashlib
import json
import os
import re
import secrets
import ssl
import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

import app as hk
import auth
from db import connect as _db
from saga import i18n, publish, sharing
from saga.core import TZ, BodyLimit, LangMiddleware
from saga.i18n import t
from version import REPO, VERSION, newer

E = os.environ
ADMIN_URL = hk.ADMIN_URL
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
ICON_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
HOST_RE = re.compile(r"[a-z0-9.-]{1,253}(:[0-9]{1,5})?")
PREFIX_RE = re.compile(r"[a-z0-9_.-]{0,20}")

admin = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
admin.add_middleware(LangMiddleware, session_key="aid")      # vor SessionMiddleware = laeuft danach (kennt die Sitzung)
admin.add_middleware(SessionMiddleware, secret_key=hashlib.sha256(("hk-admin|" + hk.SECRET).encode()).hexdigest(),
                     session_cookie="hk_admin", https_only=ADMIN_URL.startswith("https://"), same_site="strict",
                     max_age=int(E.get("ADMIN_SESSION_HOURS", "4")) * 3600)
admin.add_middleware(BodyLimit, default=1024 * 1024)       # nur Formulare: 1 MB reicht; zuletzt registriert = laeuft zuerst
admin.mount("/static", StaticFiles(directory=os.path.join(hk.HERE, "static")), name="static")
tmpl = Environment(loader=FileSystemLoader(os.path.join(hk.HERE, "templates", "admin")), autoescape=select_autoescape(["html"]))
i18n.install(tmpl)
HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
}


# --- Sitzung, CSRF, Seiten ----------------------------------------------------------------------------
def _me(request):
    """Angemeldeter Admin (frisch geprueft) oder None."""
    uid = request.session.get("aid")
    if not uid:
        return None
    info = auth.user_info(uid)
    if not info or not info["is_admin"] or info["epoch"] > float(request.session.get("ep") or 0):
        request.session.clear()
        return None
    return info


def _csrf(request):
    t = request.session.get("csrf")
    if not t:
        t = request.session["csrf"] = secrets.token_urlsafe(24)
    return t


async def _form(request):
    """Formular lesen - None, wenn das CSRF-Token nicht passt."""
    f = await request.form()
    if not secrets.compare_digest(str(f.get("csrf") or ""), str(request.session.get("csrf") or "-")):
        return None
    return f


def _flash(request, msg, kind="ok"):
    request.session["flash"] = [str(msg)[:500], kind]


def _go(url):
    return RedirectResponse(url, status_code=303)


# --- Version + Hinweis auf neue Versionen (GitHub-Releases, hoechstens alle 12 Stunden, im Hintergrund) ---
_upd = {"at": 0.0, "latest": None, "url": "", "busy": False}


def _update_info():
    """-> {'version': '1.2.0', 'url': ...} wenn es eine neuere Version gibt, sonst None. Blockiert nie."""
    if not auth.settings().get("update_check", True):
        return None
    if time.time() - _upd["at"] > 12 * 3600 and not _upd["busy"]:
        _upd["busy"] = True

        def fetch():
            try:
                r = httpx.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=6,
                              headers={"Accept": "application/vnd.github+json", "User-Agent": f"Saga/{VERSION}"})
                if r.status_code == 200:
                    j = r.json()
                    _upd.update(latest=str(j.get("tag_name") or "").lstrip("vV") or None, url=str(j.get("html_url") or ""))
            except Exception as ex:                       # offline / gesperrt: einfach keinen Hinweis zeigen
                print("update-check:", type(ex).__name__, flush=True)
            finally:
                _upd.update(at=time.time(), busy=False)
        threading.Thread(target=fetch, daemon=True).start()
    if _upd["latest"] and newer(_upd["latest"], VERSION):
        return {"version": _upd["latest"], "url": _upd["url"] or f"https://github.com/{REPO}/releases"}
    return None


def _page(request, name, me=None, status=200, **kw):
    flash = request.session.pop("flash", None)
    if me and not flash and time.time() - hk.PROXY_WARN["at"] < 86400:
        flash = [t("Hinweis: Anfragen kommen über einen Reverse Proxy ({ip}), der nicht in TRUSTED_PROXIES steht. Dann teilen sich alle Besucher eine Adresse (Sperre nach Fehlversuchen gilt für alle). Bitte TRUSTED_PROXIES auf diese Adresse setzen.",
                   ip=hk.PROXY_WARN["ip"]), "err"]
    html = tmpl.get_template(name).render(title=hk.title(), v=hk.ASSET_V, csrf=_csrf(request), me=me, flash=flash,
                                          base_url=hk.BASE_URL, path=request.url.path, version=VERSION,
                                          update=_update_info() if me else None, **kw)
    return HTMLResponse(html, status_code=status, headers=HEADERS)


def _on(f, k):
    return f.get(k) in ("1", "on", "true", "yes")


def _s(f, k, n=200):
    return str(f.get(k) or "").strip()[:n]


def _groups_list(text):
    out = []
    for g in re.split(r"[,\n]+", text or ""):
        g = auth.norm_group(g)
        if g and g not in out:
            out.append(g)
    return out


def _fmt(ts):
    """Zeit in der eingestellten Zeitzone (TIMEZONE, nicht die des Containers) - mit Kuerzel, z. B. 'CEST'."""
    if not ts:
        return i18n.fmt_date(ts)
    return i18n.fmt_date(ts, TZ, with_time=True) + " " + datetime.fromtimestamp(ts, TZ).strftime("%Z")


tmpl.filters["dt"] = _fmt


@admin.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@admin.get("/icon/{name}")
def icon(name: str, request: Request):
    if not _me(request) or not ICON_RE.fullmatch(name):
        return PlainTextResponse("not found", status_code=404)
    path, code = hk.svc_icon_path(name)
    if not path:
        return PlainTextResponse("not found", status_code=code)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=604800"})


# --- Anmeldung ------------------------------------------------------------------------------------
@admin.get("/login")
def login(request: Request):
    if _me(request):
        return _go("/")
    return _page(request, "login.html")


@admin.post("/login")
async def login_post(request: Request):
    f = await _form(request)
    if f is None:
        return _page(request, "login.html", status=400, error=t("Die Seite war zu lange offen – bitte noch einmal."))
    username = _s(f, "username", 100)
    user, err = await run_in_threadpool(auth.authenticate, username, str(f.get("password") or ""), hk._ip(request), True, "admin")
    if user and not user["is_admin"]:
        user, err = None, t("Dieses Konto hat keine Admin-Rechte.")
    if not user:
        return _page(request, "login.html", status=401, error=err, username=username)
    info = auth.user_info(user["username"], fresh=True) or {}
    request.session.clear()
    request.session.update(aid=user["username"], ep=info.get("epoch") or 0)
    print(f"admin: Anmeldung {user['username']} von {hk._ip(request)}", flush=True)
    return _go("/")


@admin.get("/logout")
def logout(request: Request):
    request.session.clear()
    return _go("/login")


# --- Uebersicht -----------------------------------------------------------------------------------
@admin.get("/")
def home(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    with _db() as c:
        n = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        stats = {"users": n("SELECT COUNT(*) FROM users"), "active": n("SELECT COUNT(*) FROM users WHERE disabled=0"),
                 "admins": n("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0"), "groups": n("SELECT COUNT(*) FROM groups"),
                 "services": n("SELECT COUNT(*) FROM service"), "bookmarks": n("SELECT COUNT(*) FROM bookmark"),
                 "pages": n("SELECT COUNT(*) FROM note WHERE deleted_at IS NULL")}
        recent = c.execute("SELECT username, display_name, source, last_login FROM users WHERE last_login IS NOT NULL "
                           "ORDER BY last_login DESC LIMIT 8").fetchall()
    return _page(request, "home.html", me, stats=stats, st=auth.settings(fresh=True), recent=recent)


# --- Benutzer -------------------------------------------------------------------------------------
@admin.get("/users")
def users(request: Request, q: str = ""):
    me = _me(request)
    if not me:
        return _go("/login")
    q = q.strip().lower()[:64]
    with _db() as c:
        rows = c.execute("SELECT id, username, display_name, email, source, is_admin, disabled, last_login FROM users "
                         "WHERE (? = '' OR strpos(lower(username), ?) > 0 OR strpos(lower(display_name), ?) > 0 OR strpos(lower(email), ?) > 0) "
                         "ORDER BY username LIMIT 500", (q, q, q, q)).fetchall()
        gm = {}
        for uid_, g in c.execute("SELECT ug.user_id, g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id").fetchall():
            gm.setdefault(uid_, []).append(g)
        for uid_, g in c.execute("SELECT user_id, name FROM user_ext_group").fetchall():
            gm.setdefault(uid_, []).append(g + " ⇣")
        allg = [r[0] for r in c.execute("SELECT name FROM groups ORDER BY name").fetchall()]
    return _page(request, "users.html", me, rows=rows, gm=gm, q=q, allg=allg)


@admin.post("/users/new")
async def user_new(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/users")
    source = f.get("source") if f.get("source") in ("local", "ldap", "oidc") else "local"
    pw = str(f.get("password") or "")
    if source == "local" and not pw:
        _flash(request, t("Lokale Konten brauchen ein Passwort."), "err")
        return _go("/users")
    user, err = await run_in_threadpool(auth.create_user, _s(f, "username", 64), pw if source == "local" else None,
                                        _s(f, "name", 120), _s(f, "email", 200), source, _on(f, "is_admin"),
                                        _groups_list(_s(f, "groups", 2000)))
    if err:
        _flash(request, err, "err")
        return _go("/users")
    print(f"admin: {me['username']} legt Konto {user['username']} an ({source})", flush=True)
    _flash(request, t("Konto „{name}“ angelegt.", name=user["username"]))
    return _go(f"/users/{user['id']}")


def _admin_count(c, exclude_id=None):
    return c.execute("SELECT COUNT(*) FROM users WHERE is_admin=1 AND disabled=0 AND id<>?", (exclude_id or 0,)).fetchone()[0]


@admin.get("/users/{uid_}")
def user_edit(uid_: int, request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    u = auth.get_user(uid=uid_)
    if not u:
        return _go("/users")
    loc, ext = auth.groups_of(u["id"])
    with _db() as c:
        allg = [r[0] for r in c.execute("SELECT name FROM groups ORDER BY name").fetchall()]
        devs = c.execute("SELECT name, created, last_used FROM device_login WHERE uid=? ORDER BY id", (u["username"],)).fetchall()
    tiles = hk.visible_services({"groups": loc | ext})
    return _page(request, "user.html", me, u=u, loc=sorted(loc), ext=sorted(ext), allg=allg, usage=hk.user_usage(u["username"]),
                 devs=devs, tiles=sorted(tiles.values(), key=lambda s: s["name"].lower()), is_self=u["username"] == me["username"])


@admin.post("/users/{uid_}")
async def user_post(uid_: int, request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    u = auth.get_user(uid=uid_)
    if f is None or not u:
        return _go("/users")
    back = f"/users/{uid_}"
    act, self_ = f.get("action"), u["username"] == me["username"]
    who = f"admin: {me['username']} -> {u['username']}:"
    if act == "save":
        is_admin, disabled = _on(f, "is_admin"), _on(f, "disabled")
        if self_ and (not is_admin or disabled):
            _flash(request, t("Das eigene Konto kann man sich nicht selbst sperren oder die Admin-Rechte nehmen."), "err")
            return _go(back)
        with _db() as c:
            if (u["is_admin"] and not u["disabled"]) and (not is_admin or disabled) and _admin_count(c, u["id"]) == 0:
                _flash(request, t("Das ist der letzte aktive Admin – zuerst einen anderen Admin anlegen."), "err")
                return _go(back)
        email = _s(f, "email", 200)
        if email and not hk.EMAIL_RE.fullmatch(email):
            _flash(request, t("Die E-Mail-Adresse sieht nicht richtig aus."), "err")
            return _go(back)
        with _db() as c, c:
            c.execute("UPDATE users SET display_name=?, email=?, is_admin=?, disabled=? WHERE id=?",
                      (_s(f, "name", 120), email, 1 if is_admin else 0, 1 if disabled else 0, u["id"]))
        auth.set_user_groups(u["id"], list(f.getlist("groups")) + _groups_list(_s(f, "new_groups", 2000)))
        if disabled and not u["disabled"]:
            hk.revoke_user_logins(u["username"])
        print(f"{who} gespeichert (admin={is_admin}, gesperrt={disabled})", flush=True)
        _flash(request, t("Gespeichert.") + (" " + t("Konto gesperrt – Sitzungen und App-Anmeldungen sind beendet.") if disabled and not u["disabled"] else ""))
    elif act == "password":
        if u["source"] != "local":
            _flash(request, t("Externe Konten haben hier kein Passwort."), "err")
            return _go(back)
        pw = str(f.get("password") or "")
        p = auth.password_problem(pw)
        if p:
            _flash(request, p, "err")
            return _go(back)
        await run_in_threadpool(auth.set_password, u["username"], pw)
        if _on(f, "revoke"):
            hk.revoke_user_logins(u["username"])
        if self_:
            request.session["ep"] = time.time()
        print(f"{who} Passwort gesetzt", flush=True)
        _flash(request, t("Neues Passwort gesetzt – bestehende Browser-Sitzungen sind beendet."))
    elif act == "logout":
        with _db() as c, c:
            c.execute("UPDATE users SET logout_at=? WHERE id=?", (time.time(), u["id"]))
        n = hk.revoke_user_logins(u["username"])
        auth._ui_cache.pop(u["username"], None)
        print(f"{who} ueberall abgemeldet", flush=True)
        if self_:
            return _go("/login")
        _flash(request, t("Überall abgemeldet ({n} App-Anmeldungen/Schlüssel widerrufen).", n=n))
    elif act == "unlink":
        # SSO-Verknuepfung loesen (z. B. nach Wechsel des Anbieters, neue sub-Werte): die naechste Anmeldung
        # ueber SSO mit diesem Benutzernamen verknuepft das Konto neu - Daten bleiben erhalten
        if u["source"] != "oidc" or not u["ext_id"]:
            _flash(request, t("Dieses Konto ist mit keiner SSO-Identität verknüpft."), "err")
            return _go(back)
        with _db() as c, c:
            c.execute("UPDATE users SET ext_id=NULL WHERE id=?", (u["id"],))
        auth._ui_cache.pop(u["username"], None)
        print(f"{who} SSO-Verknuepfung geloest", flush=True)
        _flash(request, t("SSO-Verknüpfung gelöst – die nächste SSO-Anmeldung mit diesem Benutzernamen verknüpft das Konto neu."))
    elif act == "delete":
        if self_:
            _flash(request, t("Das eigene Konto kann man nicht löschen."), "err")
            return _go(back)
        if _s(f, "confirm", 64).lower() != u["username"]:
            _flash(request, t("Zum Löschen den Benutzernamen eintippen."), "err")
            return _go(back)
        with _db() as c:
            if u["is_admin"] and not u["disabled"] and _admin_count(c, u["id"]) == 0:
                _flash(request, t("Das ist der letzte aktive Admin."), "err")
                return _go(back)
        counts = await run_in_threadpool(hk.delete_user_data, u["username"])
        with _db() as c, c:
            c.execute("DELETE FROM users WHERE id=?", (u["id"],))
        auth._ui_cache.pop(u["username"], None)
        print(f"{who} GELOESCHT ({counts})", flush=True)
        _flash(request, t("Konto „{name}“ und alle Daten gelöscht ({pages} Seiten, {files} Dateien).", name=u["username"], pages=counts.get("note", 0), files=counts.get("file", 0)))
        return _go("/users")
    return _go(back)


# --- Gruppen --------------------------------------------------------------------------------------
@admin.get("/groups")
def groups(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    prefix = (auth.settings().get("group_prefix") or "").lower()
    with _db() as c:
        rows = c.execute("SELECT g.id, g.name, g.description, (SELECT COUNT(*) FROM user_group ug WHERE ug.group_id=g.id) "
                         "FROM groups g ORDER BY g.name").fetchall()
        members = {}
        for gid, name in c.execute("SELECT ug.group_id, u.username FROM user_group ug JOIN users u ON u.id=ug.user_id ORDER BY u.username").fetchall():
            members.setdefault(gid, []).append(name)
        ext = c.execute("SELECT name, COUNT(*) FROM user_ext_group GROUP BY name ORDER BY name").fetchall()
    used = {}
    for s in hk.all_services().values():
        for g in s["groups"]:
            used.setdefault(g, []).append(s["name"])
    return _page(request, "groups.html", me, rows=rows, members=members, used=used, ext=ext, prefix=prefix)


@admin.post("/groups")
async def groups_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/groups")
    act = f.get("action")
    if act == "new":
        name = auth.norm_group(_s(f, "name", 100))
        if not name:
            _flash(request, t("Ungültiger Gruppenname (a–z, 0–9, Leerzeichen . _ -)."), "err")
            return _go("/groups")
        with _db() as c, c:
            if c.execute("SELECT 1 FROM groups WHERE name=?", (name,)).fetchone():
                _flash(request, t("Die Gruppe gibt es schon."), "err")
                return _go("/groups")
            auth.ensure_group(c, name, _s(f, "description", 300))
        _flash(request, t("Gruppe „{name}“ angelegt.", name=name))
    elif act in ("save", "delete"):
        try:
            gid = int(f.get("id") or 0)
        except ValueError:
            return _go("/groups")
        with _db() as c, c:
            if act == "save":
                c.execute("UPDATE groups SET description=? WHERE id=?", (_s(f, "description", 300), gid))
            else:
                g = c.execute("SELECT name FROM groups WHERE id=?", (gid,)).fetchone()
                c.execute("DELETE FROM groups WHERE id=?", (gid,))
                if g:
                    sharing.drop_group(c, g[0])        # Freigaben an diese Gruppe enden mit ihr
        auth._ui_cache.clear()
        _flash(request, t("Gespeichert.") if act == "save" else t("Gruppe gelöscht."))
    return _go("/groups")


# --- Dienste --------------------------------------------------------------------------------------
SVC_COLS = "id, slug, name, url, icon, category, extra_groups, everyone, check_status, sort"


def _svc_dict(r):
    d = dict(zip([k.strip() for k in SVC_COLS.split(",")], r))
    try:
        d["extra"] = ", ".join(json.loads(d["extra_groups"] or "[]"))
    except ValueError:
        d["extra"] = ""
    return d


@admin.get("/services")
def services(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    with _db() as c:
        rows = [_svc_dict(r) for r in c.execute(f"SELECT {SVC_COLS} FROM service ORDER BY category, sort, name").fetchall()]
    ldap = sorted([s for s in hk.all_services().values() if s.get("source") == "ldap"], key=lambda s: s["name"].lower())
    st = auth.settings()
    return _page(request, "services.html", me, rows=rows, ldap=ldap, prefix=st.get("group_prefix") or "",
                 ldap_on=st["ldap"].get("enabled") and st["ldap"].get("services_from_description"), s=None)


@admin.get("/services/{sid}")
def service_edit(sid: int, request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    with _db() as c:
        r = c.execute(f"SELECT {SVC_COLS} FROM service WHERE id=?", (sid,)).fetchone()
    if not r:
        return _go("/services")
    return _page(request, "service.html", me, s=_svc_dict(r), prefix=auth.settings().get("group_prefix") or "")


def _svc_input(f):
    slug = _s(f, "slug", 41).lower()
    if not SLUG_RE.fullmatch(slug):
        return None, t("Kurzname (slug): a–z, 0–9, _ und -, z. B. nextcloud.")
    url = _s(f, "url", 500)
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        return None, t("Adresse mit http:// oder https:// angeben.")
    icon = _s(f, "icon", 64).lower()
    if icon and not ICON_RE.fullmatch(icon):
        return None, t("Symbol: Name aus selfh.st/icons, z. B. nextcloud.")
    try:
        sort = int(f.get("sort") or 0)
    except ValueError:
        sort = 0
    return {"slug": slug, "name": _s(f, "name", 80) or slug, "url": url, "icon": icon,
            "category": re.sub(r"\s+", " ", _s(f, "category", 40)) or "Dienste",
            "extra_groups": json.dumps(_groups_list(_s(f, "extra", 2000))), "everyone": 1 if _on(f, "everyone") else 0,
            "check_status": 1 if _on(f, "check_status") else 0, "sort": max(-9999, min(sort, 9999))}, None


@admin.post("/services")
async def services_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/services")
    act = f.get("action")
    sid = int(f.get("id") or 0) if str(f.get("id") or "0").isdigit() else 0
    back = f"/services/{sid}" if sid else "/services"
    if act == "delete" and sid:
        with _db() as c, c:
            c.execute("DELETE FROM service WHERE id=?", (sid,))
        hk.services_changed()
        _flash(request, t("Dienst gelöscht."))
        return _go("/services")
    v, err = _svc_input(f)
    if err:
        _flash(request, err, "err")
        return _go(back)
    prefix = (auth.settings().get("group_prefix") or "").lower()
    with _db() as c, c:
        other = c.execute("SELECT id FROM service WHERE slug=?", (v["slug"],)).fetchone()
        if other and other[0] != sid:
            _flash(request, t("Den Kurznamen „{slug}“ hat schon ein anderer Dienst.", slug=v["slug"]), "err")
            return _go(back)
        if sid:
            c.execute("UPDATE service SET slug=?, name=?, url=?, icon=?, category=?, extra_groups=?, everyone=?, check_status=?, sort=? "
                      "WHERE id=?", (v["slug"], v["name"], v["url"], v["icon"], v["category"], v["extra_groups"], v["everyone"],
                                     v["check_status"], v["sort"], sid))
        else:
            sid = c.execute("INSERT INTO service (slug, name, url, icon, category, extra_groups, everyone, check_status, sort, created) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (v["slug"], v["name"], v["url"], v["icon"], v["category"], v["extra_groups"],
                                                            v["everyone"], v["check_status"], v["sort"], time.time())).lastrowid
        auth.ensure_group(c, prefix + v["slug"], t("Zugang zu {name}", name=v["name"]))      # Zugangsgruppe gleich anlegen
    hk.services_changed()
    print(f"admin: {me['username']} speichert Dienst {v['slug']}", flush=True)
    _flash(request, (t("Dienst „{name}“ gespeichert – Zugang über die Gruppe „{group}“ und für alle angemeldeten Benutzer.", name=v["name"], group=prefix + v["slug"])
                     if v["everyone"] else t("Dienst „{name}“ gespeichert – Zugang über die Gruppe „{group}“.", name=v["name"], group=prefix + v["slug"])))
    return _go("/services")


# --- Einstellungen: Allgemein ------------------------------------------------------------------------
@admin.get("/settings")
def settings_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    return _page(request, "settings.html", me, st=auth.settings(fresh=True), langs=i18n.NAMES, published=publish.all_published())


@admin.post("/settings")
async def settings_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/settings")
    prefix = _s(f, "group_prefix", 20).lower()
    if not PREFIX_RE.fullmatch(prefix):
        _flash(request, t("Gruppen-Kennung: nur a–z, 0–9, _ . - (höchstens 20 Zeichen)."), "err")
        return _go("/settings")
    hosts = [h for h in re.split(r"[,\s]+", _s(f, "allowed_hosts", 2000).lower()) if h]
    bad = [h for h in hosts if not HOST_RE.fullmatch(h)]
    if bad:
        _flash(request, t("Ungültige Hosts: {list}", list=", ".join(bad[:5])), "err")
        return _go("/settings")
    old_prefix = auth.settings().get("group_prefix")
    home = int(f.get("public_home") or 0) if str(f.get("public_home") or "0").isdigit() else 0
    if home and not any(p["note_id"] == home and p["live"] for p in publish.all_published()):
        home = 0                                          # nur veroeffentlichte, erreichbare Seiten
    for k, v in (("title", _s(f, "title", 60) or "Saga"), ("registration", _on(f, "registration")),
                 ("publish_enabled", _on(f, "publish_enabled")), ("public_home", home),
                 ("share_enabled", _on(f, "share_enabled")), ("share_group_scope", _on(f, "share_group_scope")),
                 ("local_login", _on(f, "local_login")), ("group_prefix", prefix),
                 ("default_groups", _groups_list(_s(f, "default_groups", 1000))),
                 ("allow_private_hosts", _on(f, "allow_private_hosts")), ("allowed_hosts", ", ".join(hosts)),
                 ("update_check", _on(f, "update_check")), ("default_lang", i18n.norm(f.get("default_lang")))):
        auth.save_setting(k, v)
    hk.services_changed()
    print(f"admin: {me['username']} speichert Allgemein", flush=True)
    _flash(request, t("Gespeichert.") + (" " + t("Hinweis: Mit der neuen Gruppen-Kennung gelten neue Zugangsgruppen – Favoriten der Dienste müssen neu gesetzt werden.")
                                         if old_prefix != prefix else ""))
    return _go("/settings")


# --- Oeffentliche Seiten (Veroeffentlichen) -----------------------------------------------------------------
@admin.get("/published")
def published_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    st = auth.settings(fresh=True)
    return _page(request, "published.html", me, rows=publish.all_published(), enabled=bool(st.get("publish_enabled", True)),
                 home=publish.public_home())


@admin.post("/published")
async def published_post(request: Request):
    """Zurueckziehen: die Veroeffentlichung entfernen (die Seite selbst bleibt unveraendert beim Benutzer)."""
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None or f.get("action") != "withdraw" or not str(f.get("id") or "").isdigit():
        return _go("/published")
    nid = int(f.get("id"))
    if publish.unpublish(nid, by="admin"):
        print(f"admin: {me['username']} zieht Veroeffentlichung {nid} zurueck", flush=True)
        _flash(request, t("Veröffentlichung zurückgezogen."))
    return _go("/published")


# --- Einstellungen: SSO / OIDC -------------------------------------------------------------------------
OIDC_TEXT = ("issuer", "client_id", "scopes", "username_claim", "name_claim", "email_claim", "groups_claim", "label",
             "allowed_group", "admin_group")
OIDC_BOOL = ("enabled", "auto_create", "verify_tls")


@admin.get("/settings/oidc")
def oidc_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    o = auth.settings(fresh=True)["oidc"]
    return _page(request, "oidc.html", me, o=o, has_secret=bool(o.get("client_secret")), test=request.session.pop("oidc_test", None))


def _oidc_from(f):
    cur = auth.settings(fresh=True)["oidc"]
    v = {k: _s(f, k, 500) for k in OIDC_TEXT}
    v.update({k: _on(f, k) for k in OIDC_BOOL})
    v["issuer"] = v["issuer"].rstrip("/")
    v["client_secret"] = str(f.get("client_secret") or "")
    v["scopes"] = v["scopes"] or "openid profile email"
    v["username_claim"] = v["username_claim"] or "preferred_username"
    if _on(f, "clear_secret"):
        v["client_secret"], v["_clear"] = "", True
    return v, cur


async def _oidc_test(v, cur):
    if not v["issuer"].startswith(("https://", "http://")):
        return [t("✗ Aussteller-Adresse (Issuer) fehlt oder ist ungültig.")]
    verify = (ssl.create_default_context(cafile=hk.CA_FILE) if os.path.isfile(hk.CA_FILE) else True) if v["verify_tls"] else False
    out = []
    try:
        async with httpx.AsyncClient(timeout=8, verify=verify, follow_redirects=True) as cl:
            r = await cl.get(v["issuer"] + "/.well-known/openid-configuration")
        if r.status_code != 200:
            return [t("✗ Discovery: HTTP {code} von {url}", code=r.status_code, url=v["issuer"] + "/.well-known/openid-configuration")]
        d = r.json()
    except Exception as ex:  # noqa: BLE001
        return [t("✗ Nicht erreichbar: {why}", why=f"{type(ex).__name__}: {ex}")]
    out.append(t("✓ Discovery-Dokument gelesen"))
    out.append(("✓" if d.get("issuer", "").rstrip("/") == v["issuer"] else "✗") + " " + t("Issuer im Dokument: {issuer}", issuer=d.get("issuer")))
    for k in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "end_session_endpoint"):
        out.append(("✓ " if d.get(k) else "– ") + f"{k}: {d.get(k) or t('nicht angegeben')}")
    if "S256" not in (d.get("code_challenge_methods_supported") or ["S256"]):
        out.append(t("✗ Anbieter unterstützt kein PKCE S256"))
    out.append(t("Weiterleitungs-Adresse (beim Anbieter eintragen): {url}", url=hk.BASE_URL + "/auth/callback"))
    if not (v["client_secret"] or cur.get("client_secret")):
        out.append(t("Hinweis: kein Client-Secret – nur für öffentliche Clients (PKCE) geeignet."))
    return out


@admin.post("/settings/oidc")
async def oidc_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/settings/oidc")
    v, cur = _oidc_from(f)
    if f.get("action") == "test":
        request.session["oidc_test"] = await _oidc_test(v, cur)
        return _go("/settings/oidc")
    if v["enabled"] and (not v["issuer"] or not v["client_id"]):
        _flash(request, t("Zum Einschalten Issuer und Client-ID angeben."), "err")
        return _go("/settings/oidc")
    clear = v.pop("_clear", False)
    auth.save_setting("oidc", v)
    if clear:
        with _db() as c, c:
            st = json.loads(c.execute("SELECT v FROM setting WHERE k='oidc'").fetchone()[0])
            st["client_secret"] = ""
            c.execute("UPDATE setting SET v=? WHERE k='oidc'", (json.dumps(st, ensure_ascii=False),))
        auth._settings_cache["v"] = None
    print(f"admin: {me['username']} speichert OIDC (aktiv={v['enabled']})", flush=True)
    _flash(request, t("SSO-Einstellungen gespeichert."))
    return _go("/settings/oidc")


# --- Einstellungen: LDAP / Active Directory -------------------------------------------------------------
LDAP_TEXT = ("url", "bind_dn", "user_base", "user_filter", "attr_username", "attr_name", "attr_mail", "group_base",
             "group_filter", "group_name_attr", "allowed_group", "admin_group")
LDAP_BOOL = ("enabled", "starttls", "verify_tls", "auto_create", "services_from_description", "password_login", "sso_groups")


@admin.get("/settings/ldap")
def ldap_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    lc = auth.settings(fresh=True)["ldap"]
    return _page(request, "ldap.html", me, l=lc, has_secret=bool(lc.get("bind_password")), presets=list(auth.LDAP_PRESETS),
                 test=request.session.pop("ldap_test", None), test_user=request.session.pop("ldap_test_user", ""))


def _ldap_from(f):
    v = {k: _s(f, k, 1000) for k in LDAP_TEXT}
    v.update({k: _on(f, k) for k in LDAP_BOOL})
    v["preset"] = f.get("preset") if f.get("preset") in auth.LDAP_PRESETS else "freeipa"
    v["group_mode"] = f.get("group_mode") if f.get("group_mode") in ("memberof", "search", "ad_nested") else "memberof"
    v["bind_password"] = str(f.get("bind_password") or "")
    return v


@admin.post("/settings/ldap")
async def ldap_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/settings/ldap")
    v, act = _ldap_from(f), f.get("action")
    if act == "preset":
        v.update(auth.LDAP_PRESETS[v["preset"]])
        auth.save_setting("ldap", v)
        _flash(request, t("Vorlage „{name}“ übernommen – Adresse, Dienstkonto und Suchbasis prüfen.", name=v["preset"]))
        return _go("/settings/ldap")
    if act == "test":
        cfg = dict(v)
        if not cfg["bind_password"]:
            cfg["bind_password"] = auth.settings(fresh=True)["ldap"].get("bind_password", "")
        user = _s(f, "test_user", 64)
        try:
            lines = await run_in_threadpool(auth.ldap_test, cfg, user)
        except Exception as ex:  # noqa: BLE001
            lines = [f"✗ {type(ex).__name__}: {ex}"]
        request.session["ldap_test"] = lines
        request.session["ldap_test_user"] = user
        return _go("/settings/ldap")
    if v["enabled"] and (not v["url"].lower().startswith(("ldap://", "ldaps://")) or not v["user_base"]):
        _flash(request, t("Zum Einschalten Server-Adresse (ldap:// oder ldaps://) und Benutzer-Suchbasis angeben."), "err")
        return _go("/settings/ldap")
    if "{username}" not in v["user_filter"]:
        _flash(request, t("Der Benutzerfilter muss {username} enthalten."), "err")
        return _go("/settings/ldap")
    if v["url"].lower().startswith("ldap://") and not v["starttls"]:
        _flash(request, t("Achtung: ldap:// ohne StartTLS überträgt Passwörter unverschlüsselt."), "err")
    auth.save_setting("ldap", v)
    hk.services_changed()
    print(f"admin: {me['username']} speichert LDAP (aktiv={v['enabled']})", flush=True)
    if not request.session.get("flash"):
        _flash(request, t("LDAP-Einstellungen gespeichert."))
    return _go("/settings/ldap")
