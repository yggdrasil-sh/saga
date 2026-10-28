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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

import app as hk
import auth
from db import clean_line, connect as _db
from saga import ai, avatars, backups, calls, chat, cloudflare, federation, horn, horn_ops, i18n, live, mfa, publish, push, push_stream, sharing, userbackup, weather
from saga.core import HSTS, RESTORE_TEST, TZ, BodyLimit, LangMiddleware, SecHeaders, install_lang_ui, lang_switch_response
from saga.i18n import t
from version import REPO, VERSION, VERSION_LABEL, channel, newer

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
admin.add_middleware(BodyLimit, default=1024 * 1024,      # nur Formulare: 1 MB reicht; zuletzt registriert = laeuft zuerst
                     rules=[("POST", re.compile(r"/settings/ai/persona"), avatars.UPLOAD_MAX + 256 * 1024)])   # Profilbild der KI
# Bughunt Sicherheit V2/V3: dieselben Kopfzeilen wie die App (HSTS nur, wenn auch der Admin-Bereich per https laeuft)
admin.add_middleware(SecHeaders, hsts=HSTS and ADMIN_URL.startswith("https://"))
admin.mount("/static", StaticFiles(directory=os.path.join(hk.HERE, "static")), name="static")
tmpl = Environment(loader=FileSystemLoader(os.path.join(hk.HERE, "templates", "admin")), autoescape=select_autoescape(["html"]))
i18n.install(tmpl)
install_lang_ui(tmpl)
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
    if RESTORE_TEST or not auth.settings().get("update_check", True):
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
    # SemVer-Vergleich: ein RC 2.2.0-rc.7 zeigt keinen Hinweis auf 2.1.0, aber auf 2.2.0, sobald es erschienen ist
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
                                          version_label=VERSION_LABEL, channel=channel(),
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


@admin.get("/lang")
def lang_switch(request: Request):
    """Sprachumschalter (Kopfzeile bzw. unter der Anmeldung): Cookie; angemeldet mit fester Konto-Sprache auch diese."""
    me = _me(request)
    return lang_switch_response(request, me["username"] if me else None, ADMIN_URL.startswith("https://"))


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
    if mfa.supported(user) and mfa.has_mfa(user["username"]):
        # zweiter Faktor wie auf der Hauptseite: Zwischenschritt (5 Minuten, an dieses Admin-Sitzungs-Cookie gebunden)
        request.session.clear()
        request.session.update(amfa_u=user["username"], amfa_at=int(time.time()))
        return _go("/login/2fa")
    if mfa.required(dict(user, is_admin=bool(user["is_admin"]))):
        # Richtlinie "2FA verlangen" (Admins/alle), das Konto hat noch keinen Faktor: nicht aussperren, sondern hier
        # die Authenticator-App einrichten lassen (Passkeys gehen am Admin-Port oft nicht - andere Origin)
        request.session.clear()
        request.session.update(amfs_u=user["username"], amfs_at=int(time.time()))
        return _go("/login/2fa/setup")
    return _admin_session(request, user)


def _admin_session(request, user, go=True):
    info = auth.user_info(user["username"], fresh=True) or {}
    request.session.clear()
    request.session.update(aid=user["username"], ep=info.get("epoch") or 0)
    print(f"admin: Anmeldung {user['username']} von {hk._ip(request)}", flush=True)
    return _go("/") if go else None


# Einrichtung des zweiten Faktors im Admin-Bereich (Richtlinie verlangt ihn, das Konto hat keinen): Authenticator-App
# ohne JavaScript (QR-Code + Schluessel, dann Code). Der Zwischenschritt gilt 15 Minuten und ist keine Admin-Sitzung.
SETUP_TTL = 900


def _admin_setup_page(request, uid, status=200, error=None):
    try:
        view = mfa.totp_begin(uid, reuse=True)
    except mfa.MfaError:                              # inzwischen eingerichtet (z. B. auf der Hauptseite)
        return None
    return _page(request, "login2fa_setup.html", status=status, error=error, totp=view, security_url=hk.BASE_URL + "/security")


def _setup_pending(request):
    """Benutzername im Einrichtungs-Schritt - None, wenn abgelaufen oder das Konto kein Admin (mehr) ist."""
    uid = mfa.pending(request, "amfs", ttl=SETUP_TTL)
    u = auth.get_user(uid) if uid else None
    return uid if u and u["is_admin"] else None


def _setup_done_elsewhere(request, uid):
    """Faktor gibt es schon (z. B. auf der Hauptseite eingerichtet): normaler Zwischenschritt mit Code/Passkey."""
    request.session.clear()
    request.session.update(amfa_u=uid, amfa_at=int(time.time()))
    return _go("/login/2fa")


@admin.get("/login/2fa/setup")
def login_2fa_setup(request: Request):
    uid = _setup_pending(request)
    if not uid:
        return _go("/login")
    if mfa.has_mfa(uid):
        return _setup_done_elsewhere(request, uid)
    return _admin_setup_page(request, uid) or _setup_done_elsewhere(request, uid)


@admin.post("/login/2fa/setup")
async def login_2fa_setup_post(request: Request):
    f = await _form(request)
    uid = _setup_pending(request)
    if not uid:
        return _page(request, "login.html", status=400, error=t("Die Anmeldung hat zu lange gedauert – bitte noch einmal."))
    if mfa.has_mfa(uid):
        return _setup_done_elsewhere(request, uid)
    if f is None:
        return _admin_setup_page(request, uid, 400, t("Die Seite war zu lange offen – bitte noch einmal."))
    ip = hk._ip(request)
    if auth.too_many(uid, ip, "admin-mfa"):
        request.session.clear()
        return _page(request, "login.html", status=429, error=t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."))
    try:
        codes = await run_in_threadpool(mfa.totp_finish, uid, _s(f, "code", 20))
    except mfa.MfaError as ex:
        if ex.status == 400:
            auth.note_fail(uid, ip, "admin-mfa")
        return _admin_setup_page(request, uid, 401 if ex.status == 400 else 409, str(ex)) or _setup_done_elsewhere(request, uid)
    auth.clear_fails(uid, "admin-mfa")
    print(f"admin: {uid} hat im Admin-Bereich eine Authenticator-App eingerichtet (Richtlinie)", flush=True)
    user = auth.get_user(uid)
    _admin_session(request, user, go=False)
    me = _me(request)
    return _page(request, "login2fa_done.html", me, codes=codes or [], account=uid, security_url=hk.BASE_URL + "/security")


# Passkeys im Admin-Bereich: nur, wenn er unter der Domain von BASE_URL laeuft (RP-ID) - sonst Code/Wiederherstellungscode
def _admin_passkey_problem():
    o = mfa.admin_origin()
    return mfa.rp_problem(o) if o else t("ADMIN_URL ist nicht gesetzt.")


def _admin_mfa_page(request, uid, status=200, error=None):
    f = mfa.factors(uid)
    prob = _admin_passkey_problem() if f["passkeys"] else None
    return _page(request, "login2fa.html", status=status, error=error, passkey=bool(f["passkeys"]) and not prob, passkey_problem=prob,
                 totp=f["totp"], recovery=f["recovery"], origin=mfa.admin_origin())


@admin.get("/login/2fa")
def login_2fa(request: Request):
    uid = mfa.pending(request, "amfa")
    if not uid:
        return _go("/login")
    return _admin_mfa_page(request, uid)


@admin.post("/login/2fa")
async def login_2fa_post(request: Request):
    f = await _form(request)
    uid = mfa.pending(request, "amfa")
    if not uid:
        return _page(request, "login.html", status=400, error=t("Die Anmeldung hat zu lange gedauert – bitte noch einmal."))
    if f is None:
        return _admin_mfa_page(request, uid, 400, t("Die Seite war zu lange offen – bitte noch einmal."))
    ip = hk._ip(request)
    if auth.too_many(uid, ip, "admin-mfa"):
        request.session.clear()
        return _page(request, "login.html", status=429, error=t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."))
    kind = await run_in_threadpool(mfa.check_code, uid, _s(f, "code", 60))
    user = auth.get_user(uid)
    if not kind or not user or not user["is_admin"]:
        auth.note_fail(uid, ip, "admin-mfa")
        return _admin_mfa_page(request, uid, 401, t("Der Code stimmt nicht."))
    auth.clear_fails(uid, "admin-mfa")
    return _admin_session(request, user)


@admin.post("/login/passkey/options")
async def login_passkey_options(request: Request):
    uid = mfa.pending(request, "amfa")
    if request.headers.get("x-saga") != "1" or not uid:
        return JSONResponse({"error": t("Die Anmeldung hat zu lange gedauert – bitte noch einmal.")}, status_code=401)
    prob = _admin_passkey_problem()
    if prob:
        return JSONResponse({"error": prob}, status_code=409)
    try:
        opts = await run_in_threadpool(mfa.auth_options, request, uid, "admin")
    except mfa.MfaError as ex:
        return JSONResponse({"error": str(ex)}, status_code=409)
    return JSONResponse({"options": opts, "origin": mfa.admin_origin()}, headers={"Cache-Control": "no-store"})


@admin.post("/login/passkey")
async def login_passkey(request: Request):
    uid = mfa.pending(request, "amfa")
    if request.headers.get("x-saga") != "1" or not uid:
        return JSONResponse({"error": t("Die Anmeldung hat zu lange gedauert – bitte noch einmal.")}, status_code=401)
    ip = hk._ip(request)
    if auth.too_many(uid, ip, "admin-mfa"):
        request.session.clear()
        return JSONResponse({"error": t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.")}, status_code=429)
    try:
        b = json.loads(await request.body() or b"{}")
        await run_in_threadpool(mfa.auth_verify, request, b.get("credential") if isinstance(b, dict) else None, uid, "admin",
                                "wa", mfa.admin_origin())
    except (ValueError, mfa.MfaError) as ex:
        auth.note_fail(uid, ip, "admin-mfa")
        return JSONResponse({"error": str(ex) if isinstance(ex, mfa.MfaError) else t("ungültige Anfrage")}, status_code=401)
    user = auth.get_user(uid)
    if not user or not user["is_admin"]:
        return JSONResponse({"error": t("Dieses Konto hat keine Admin-Rechte.")}, status_code=403)
    auth.clear_fails(uid, "admin-mfa")
    _admin_session(request, user)
    return JSONResponse({"ok": True, "next": "/"}, headers={"Cache-Control": "no-store"})


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
        locks = auth.locks()
        ids = {r[0]: r[1] for r in c.execute("SELECT username, id FROM users WHERE username IN (" + ",".join("?" * len(locks)) + ")",
                                             tuple(lk["username"] for lk in locks)).fetchall()} if locks else {}
    for lk in locks:
        lk["id"] = ids.get(lk["username"])
    return _page(request, "home.html", me, stats=stats, st=auth.settings(fresh=True), recent=recent, media=chat.media_mode(), locks=locks)


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
    return _page(request, "users.html", me, rows=rows, gm=gm, q=q, allg=allg, locked={lk["username"] for lk in auth.locks()})


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
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='mfa_reset'", (u["username"],)).fetchone()
    try:
        last_reset = json.loads(r[0]) if r else None
    except ValueError:
        last_reset = None
    tiles = hk.visible_services({"groups": loc | ext})
    mf = dict(mfa.factors(u["username"]), supported=mfa.supported(u), required=mfa.required(dict(u, is_admin=bool(u["is_admin"]))),
              last_reset=last_reset)
    return _page(request, "user.html", me, u=u, loc=sorted(loc), ext=sorted(ext), allg=allg, usage=hk.user_usage(u["username"]),
                 devs=devs, tiles=sorted(tiles.values(), key=lambda s: s["name"].lower()), is_self=u["username"] == me["username"], mf=mf,
                 lock=auth.lock_of(u["username"]), fails=auth.fail_stats(u["username"]))


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
                      (clean_line(_s(f, "name", 120)).strip(), email, 1 if is_admin else 0, 1 if disabled else 0, u["id"]))
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
    elif act == "api_revoke":                         # 2.5 (Paket 1): alle API-Tokens des Kontos widerrufen
        from saga import apitok
        n = apitok.revoke(u["username"])
        print(f"{who} API-Tokens widerrufen ({n})", flush=True)
        _flash(request, t("{n} API-Tokens widerrufen.", n=n))
    elif act == "unlock":
        auth.unlock(u["username"])
        print(f"{who} Anmelde-Sperre aufgehoben", flush=True)
        _flash(request, t("Sperre aufgehoben – die Person kann sich wieder mit ihrem Passwort anmelden."))
    elif act == "mfa_reset":
        # Person hat Passkey/Handy und Wiederherstellungscodes verloren: alle Faktoren entfernen (Protokoll: Log + Konto)
        n = await run_in_threadpool(auth.clear_mfa, u["username"], me["username"])
        auth.clear_fails(u["username"])
        print(f"{who} zweiter Faktor zurueckgesetzt ({n} Faktoren entfernt)", flush=True)
        _flash(request, t("Zweiter Faktor zurückgesetzt ({n} entfernt). Die Person meldet sich jetzt nur mit dem Passwort an und kann neu einrichten.", n=n))
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
        # Erst sperren (wirkt sofort: keine neuen Daten mehr), dann Daten und Konto in EINER Transaktion loeschen (BUG-042) -
        # scheitert das, bleibt ein gesperrtes Konto mit seinen Daten statt eines Kontos ohne Daten
        with _db() as c, c:
            c.execute("UPDATE users SET disabled=1 WHERE id=?", (u["id"],))
        auth._ui_cache.pop(u["username"], None)
        counts = await run_in_threadpool(hk.delete_user_data, u["username"], True)
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
                if g:                                # Live: lokale Mitglieder verlieren die Gruppe (vor dem Loeschen der Freigaben)
                    auth.groups_changed(c, [r[0] for r in c.execute(
                        "SELECT u.username FROM user_group ug JOIN users u ON u.id=ug.user_id WHERE ug.group_id=? "
                        "AND NOT EXISTS (SELECT 1 FROM user_ext_group e WHERE e.user_id=u.id AND e.name=?)", (gid, g[0])).fetchall()], [g[0]])
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
    return _page(request, "settings.html", me, st=auth.settings(fresh=True), langs=i18n.NAMES, published=publish.all_published(),
                 live_n=live.count(), chat_quota_default=chat.QUOTA_MB_DEFAULT, chat_video_default=chat.VIDEO_MAX_MB_DEFAULT)


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
                 ("search_username", _on(f, "search_username")),
                 ("chat_enabled", _on(f, "chat_enabled")), ("chat_retention_max_days", _days(f.get("chat_retention_max_days"))),
                 ("chat_quota_mb", _mb(f.get("chat_quota_mb"), 1 << 20)), ("chat_video_max_mb", _mb(f.get("chat_video_max_mb"), 1 << 16)),
                 ("chat_v3", _on(f, "chat_v3")),                  # 2.5 (Testrunde Web Nr. 007): Schalter fuer Stufe 3
                 ("chat_v3_days", _days(f.get("chat_v3_days") if f.get("chat_v3_days") not in (None, "") else 90)),
                 ("local_login", _on(f, "local_login")), ("group_prefix", prefix),
                 ("default_groups", _groups_list(_s(f, "default_groups", 1000))),
                 ("allow_private_hosts", _on(f, "allow_private_hosts")), ("allowed_hosts", ", ".join(hosts)),
                 ("nc_files", _on(f, "nc_files")), ("favicons", _on(f, "favicons")),
                 ("gallery_enabled", _on(f, "gallery_enabled")),
                 ("update_check", _on(f, "update_check")), ("default_lang", i18n.norm(f.get("default_lang")))):
        auth.save_setting(k, v)
    wx = {}                                                 # 2.5 Paket 4: Wetter-Widget (Open-Meteo)
    for k in ("base_url", "geo_url"):
        wx[k], err = weather.check_url(_s(f, "weather_" + k, 300))
        if err:
            _flash(request, t("Wetter: {err}", err=err), "err")
            return _go("/settings")
    auth.save_setting("weather", {"enabled": _on(f, "weather_enabled"), **wx, "api_key": _s(f, "weather_api_key", 200)})
    def _n(k, lo, hi, dflt):
        try:
            return max(lo, min(hi, int(f.get(k) or dflt)))
        except (TypeError, ValueError):
            return dflt
    auth.save_setting("api", {"enabled": _on(f, "api_enabled"), "group": _s(f, "api_group", 120).strip(),     # 2.5: oeffentliche API
                              "max_tokens": _n("api_max_tokens", 1, 100, 10), "max_days": _n("api_max_days", 0, 3650, 365),
                              "unlimited": _on(f, "api_unlimited"), "rate": _n("api_rate", 1, 10000, 120),
                              "rate_write": _n("api_rate_write", 1, 10000, 30), "webhooks": _on(f, "api_webhooks"),
                              "mcp": _on(f, "api_mcp"), "webhook_http": _on(f, "api_webhook_http")})
    weather.reset_cache()
    req = f.get("mfa_require") if f.get("mfa_require") in ("none", "admins", "all") else "none"
    acr = ", ".join(a for a in (x.strip() for x in _s(f, "mfa_oidc_acr", 300).split(",")) if re.fullmatch(r"[\w.:/#-]{1,100}", a))
    auth.save_setting("mfa", {"require": req, "oidc_mfa": _on(f, "mfa_oidc"), "oidc_acr": acr})
    av_old = dict(auth.settings(fresh=True).get("avatar") or {})
    av_new = {"gravatar": _on(f, "avatar_gravatar"), "oidc_picture": _on(f, "avatar_oidc"), "ldap_photo": _on(f, "avatar_ldap")}
    auth.save_setting("avatar", av_new)
    if any(bool(av_old.get(k, k == "gravatar")) != bool(v) for k, v in av_new.items()):
        # Quelle kann wechseln (z. B. SSO-Bild aus -> Gravatar): neue Version, sonst behalten Clients das alte Bild bis 24 h
        with _db() as c, c:
            c.execute("UPDATE avatar SET ver=ver+1 WHERE uid NOT LIKE '@%'")
    hk.services_changed()
    print(f"admin: {me['username']} speichert Allgemein (2FA verlangt: {req})", flush=True)
    _flash(request, t("Gespeichert.") + (" " + t("Hinweis: Mit der neuen Gruppen-Kennung gelten neue Zugangsgruppen – Favoriten der Dienste müssen neu gesetzt werden.")
                                         if old_prefix != prefix else ""))
    return _go("/settings")


def _calls_save(f):
    """Anrufe (2.1): STUN/TURN pruefen und speichern -> Fehlertext oder None. Leere Geheimnisse = bisherige behalten."""
    stun, bad1 = calls.urls(_s(f, "calls_stun", 600), ("stun", "stuns"))
    turn, bad2 = calls.urls(_s(f, "calls_turn_urls", 600), ("turn", "turns"))
    if bad1 or bad2:
        return t("Ungültige STUN/TURN-Adresse: {list} (Beispiel: stun:stun.example.org:3478, turns:turn.example.org:443?transport=tcp)",
                 list=", ".join((bad1 + bad2)[:3]))
    try:
        ring = max(15, min(120, int(_s(f, "calls_ring_s", 4) or 45)))
    except ValueError:
        ring = 45
    lim = {}                                               # 2.4: Geraete je Anruf (Mesh) - nur senken, mindestens 2
    for k, top in (("endpoints_video", calls.ENDPOINTS_VIDEO), ("endpoints_audio", calls.ENDPOINTS_AUDIO)):
        try:
            lim[k] = max(2, min(top, int(_s(f, "calls_" + k, 2) or top)))
        except ValueError:
            lim[k] = top
    cur = auth.settings(fresh=True).get("calls") or {}
    clear = _on(f, "calls_turn_clear")
    v = {**cur, "enabled": _on(f, "calls_enabled"), "stun": " ".join(stun), "turn_urls": " ".join(turn),
         "turn_mode": "static" if f.get("calls_turn_mode") == "static" else "rest", "turn_user": "" if clear else _s(f, "calls_turn_user", 200),
         "turn_secret": _s(f, "calls_turn_secret", 300), "turn_password": _s(f, "calls_turn_password", 300),
         "turn_ttl": cur.get("turn_ttl") or 43200, "relay_only": _on(f, "calls_relay_only"), "ring_s": ring, **lim}
    auth.save_setting("calls", v)
    if clear:                                              # leere Geheimnisse wuerden sonst die alten behalten
        with _db() as c, c:
            raw = c.execute("SELECT v FROM setting WHERE k='calls'").fetchone()
            d = json.loads(raw[0]) if raw else {}
            d.update(turn_secret="", turn_password="", turn_user="")
            c.execute("UPDATE setting SET v=? WHERE k='calls'", (json.dumps(d),))
        auth._settings_cache["v"] = None
    return None


# --- Anrufe (2.5): Gjallarhorn-Knoten, DNS, Grenzen, 1:1-Anrufe (TURN) -------------------------------------------------
def _pair_err(code):
    return {"name": t("Ungültiger Name – z. B. horn-2 oder horn-2.example.org."),
            "name_saga": t("Der Knoten braucht einen eigenen Namen (nicht den von Saga)."),
            "name_used": t("Diesen Namen hat schon ein Knoten oder ein offener Kopplungs-Code."),
            "too_many": t("Zu viele offene Kopplungs-Codes – erst einen abbrechen oder ablaufen lassen.")}.get(code, t("ungültige Angabe"))


def _int(f, k, lo, hi):
    """Zahl aus dem Formular in [lo, hi]; leer/ungueltig -> 0 (= Standard)."""
    try:
        v = int(_s(f, k, 8) or 0)
    except ValueError:
        return 0
    return max(lo, min(hi, v)) if v else 0


def _int_noted(f, k, lo, hi, label, notes, zero=None):
    """Wie _int, sagt aber, wenn ein Wert korrigiert wurde (Testrunde Web Nr. 020): notes bekommt den Hinweis.
    zero: Standardwert, auf den eine 0 faellt (sonst bedeutet 0 wie leer "Standard")."""
    raw, v = _s(f, k, 8), _int(f, k, lo, hi)
    try:
        n = int(raw) if raw else None
    except ValueError:
        notes.append(t("{label}: „{raw}“ ist keine Zahl – es gilt der Standard.", label=label, raw=raw))
        return v
    if n == 0 and zero is not None:
        notes.append(t("{label}: 0 geht nicht – es gilt der Standard {n}.", label=label, n=zero))
    elif n and v != n:
        notes.append(t("{label}: auf {n} begrenzt (erlaubt {lo}–{hi}).", label=label, n=v, lo=lo, hi=hi))
    return v


def _calls_ctx(request, me, **kw):
    st = auth.settings(fresh=True)
    with _db() as c:
        sug = horn_ops.suggest(c)
    remote = []
    if calls.horn_on():                              # Knoten, die verbundene Server mit uns teilen (Nr. 6 der 2.5-Liste)
        from saga import fed_horn
        with _db() as c:
            remote = fed_horn.remote_shared(c)
    return _page(request, "calls.html", me, st=st, hs=horn_ops.admin_state(), limits=horn.LIMITS, suggest=sug, remote=remote,
                 mesh_from=calls.limit("video") + 1, mesh_from_a=calls.limit("audio") + 1, **kw)


@admin.get("/calls")
def calls_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    return _calls_ctx(request, me)


@admin.post("/calls")
async def calls_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/calls")
    act, who = f.get("action"), me["username"]
    nid = _s(f, "id", 40)
    cur = auth.settings(fresh=True).get("calls") or {}
    if act == "horn":
        auth.save_setting("calls", {**cur, "horn": _on(f, "horn"), "horn_builtin": _on(f, "horn_builtin"),
                                      "horn_all": _on(f, "horn_all")})
        print(f"admin: {who} Gjallarhorn {'an' if _on(f, 'horn') else 'aus'}, eingebauter Knoten {'an' if _on(f, 'horn_builtin') else 'aus'}, "
              f"alle Anrufe ueber Knoten {'an' if _on(f, 'horn_all') else 'aus'}", flush=True)
        _flash(request, t("Gespeichert."))
    elif act == "limits":
        notes = []
        auth.save_setting("calls", {**cur, "horn_video": _int_noted(f, "horn_video", 2, horn.LIMITS["udp"][0], t("Video je Anruf"), notes),
                                    "horn_audio": _int_noted(f, "horn_audio", 2, horn.LIMITS["udp"][1], t("Nur Ton je Anruf"), notes),
                                    "share_legs": _int_noted(f, "share_legs", 1, 1000, t("Teilnehmende"), notes, 10) or 10,
                                    "share_mbit": _int_noted(f, "share_mbit", 1, 10000, "Mbit/s", notes, 20) or 20,
                                    # ab wie vielen Chat-Mitgliedern ein Gruppenanruf ueber einen Knoten laeuft (leer = Mesh-Grenze + 1)
                                    "horn_from": _int_noted(f, "horn_from", 3, 1000, t("Über Knoten ab"), notes)})
        _flash(request, t("Gespeichert.") + (" " + " ".join(notes) if notes else ""), "warn" if notes else "ok")
    elif act == "turn":
        err = _calls_save(f)
        _flash(request, err or t("Gespeichert."), "err" if err else "ok")
    elif act == "cf_save":
        tok = _s(f, "cf_token", 200)
        tok = tok or (cloudflare.conf().get("token") or "")
        if not tok:
            _flash(request, t("Bitte einen Cloudflare-API-Token eingeben."), "err")
            return _go("/calls#dns")
        r = await run_in_threadpool(cloudflare.save, tok, _s(f, "cf_zone", 64))
        if r["ok"]:
            print(f"admin: {who} speichert Cloudflare-Token (Zone {r['zone']})", flush=True)
            _flash(request, t("Token gültig · Zone {zone} · Rechte: DNS bearbeiten ✓", zone=r["zone"]) +
                   (" " + t("Token hat zu viele Rechte – bitte auf eine Zone beschränken.") if r["warn"] else ""))
            for n in horn_ops.admin_state()["nodes"]:
                if not n["builtin"]:
                    await run_in_threadpool(horn_ops.dns_sync, n["id"])
                    horn.send(n["id"], {"t": "cert", "cert": "dns01"})
        elif r["err"] == "zone_waehlen":
            return _calls_ctx(request, me, cf_zones=r["zones"], cf_token=tok)
        else:
            _flash(request, t("Cloudflare-Token nicht gespeichert: {err}", err=r["err"] or "–"), "err")
        return _go("/calls#dns")
    elif act == "cf_clear":
        cloudflare.clear()
        print(f"admin: {who} entfernt den Cloudflare-Token", flush=True)
        for n in horn_ops.admin_state()["nodes"]:
            if not n["builtin"]:
                horn.send(n["id"], {"t": "cert", "cert": "alpn"})
        _flash(request, t("Cloudflare-Token entfernt. Vorhandene DNS-Einträge bleiben bestehen."))
        return _go("/calls#dns")
    elif act == "pair":
        try:
            p = await run_in_threadpool(horn_ops.pair_create, _s(f, "name", 253), _on(f, "share"), _on(f, "udp"), who)
        except horn_ops.PairError as ex:
            _flash(request, _pair_err(str(ex)), "err")
            return _go("/calls")
        return _calls_ctx(request, me, pair=p)
    elif act == "pair_cancel":
        horn_ops.pair_cancel(nid)
    elif act in ("drain", "undrain"):
        if await run_in_threadpool(horn_ops.set_drain, nid, act == "drain", who):
            _flash(request, t("Knoten wird geleert – laufende Anrufe ziehen auf andere Knoten um.") if act == "drain" else t("Gespeichert."))
    elif act in ("share", "unshare"):
        if await run_in_threadpool(horn_ops.set_share, nid, act == "share", who):
            _flash(request, t("Gespeichert."))
    elif act == "remove":
        r = await run_in_threadpool(horn_ops.remove, nid, who)
        if r is not None:
            _flash(request, t("Knoten entfernt. Sein Schlüssel ist gesperrt, laufende Anrufe ziehen um.") +
                   (" " + t("DNS: {n} Einträge gelöscht.", n=r["dns"]) if "dns" in r else "") +
                   (" " + t("DNS-Einträge konnten nicht gelöscht werden: {err}", err=r["dns_err"]) if "dns_err" in r else ""),
                   "err" if "dns_err" in r else "ok")
    elif act == "check":
        await run_in_threadpool(horn_ops.check, nid)
        return _go(f"/calls#n-{nid}")
    elif act == "caps":
        n = await run_in_threadpool(horn_ops.caps_sync)
        _flash(request, t("Fähigkeiten werden mit {n} verbundenen {n:Server|Servern} abgeglichen.", n=n))
    return _go("/calls")


@admin.get("/api/calls/state")
def api_calls_state(request: Request):
    me, err = _api_admin(request)
    if err:
        return err
    return JSONResponse(horn_ops.admin_state(), headers={"Cache-Control": "no-store"})


@admin.post("/api/calls/pair")
async def api_calls_pair(request: Request):
    """{name, share, udp} -> Kopplungs-Code + Deploy-Skripte (Dialog "Erweitern")."""
    me, err = _api_admin(request)
    if err:
        return err
    try:
        b = await request.json()
    except ValueError:
        b = None
    if not isinstance(b, dict):
        return JSONResponse({"error": "bad request"}, status_code=400)
    try:
        p = await run_in_threadpool(horn_ops.pair_create, str(b.get("name") or "")[:253], bool(b.get("share")), b.get("udp") is not False,
                                    me["username"])
    except horn_ops.PairError as ex:
        return JSONResponse({"error": _pair_err(str(ex)), "code": str(ex)}, status_code=400)
    return JSONResponse(p, headers={"Cache-Control": "no-store"})


@admin.get("/api/calls/pair/{pid}")
def api_calls_pair_status(pid: str, request: Request):
    me, err = _api_admin(request)
    if err:
        return err
    st = horn_ops.pair_status(pid[:20])
    if st is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


@admin.post("/api/calls/nodes/{nid}/check")
async def api_calls_check(nid: str, request: Request):
    me, err = _api_admin(request)
    if err:
        return err
    r = await run_in_threadpool(horn_ops.check, nid[:40])
    if r is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(r, headers={"Cache-Control": "no-store"})


def _mb(v, hi):
    """Chat (2.4): Grenze in MB (0 = Standard aus der Umgebung)."""
    try:
        return max(0, min(hi, int(str(v or "0").strip() or 0)))
    except ValueError:
        return 0


def _days(v):
    """Chat: Aufbewahrungs-Hoechstwert in Tagen (0 = unbegrenzt, hoechstens 10 Jahre)."""
    try:
        return max(0, min(3650, int(str(v or "0").strip() or 0)))
    except ValueError:
        return 0


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


# --- Foederation: andere Saga-Instanzen verbinden -----------------------------------------------------------
def _fed_events():
    """Ereignisse im Pruefprotokoll -> Text (in der Sprache der Anfrage)."""
    return {"invite.created": t("Einladung erzeugt"),
            "connect.request": t("Verbindungsanfrage erhalten"),
            "connect.sent": t("Verbindungsanfrage gesendet"),
            "connect.bad_code": t("Anfrage mit ungültigem Code"),
            "peer.confirmed": t("Bestätigt"),
            "peer.confirmed_remote": t("Von der Gegenseite bestätigt"),
            "peer.disconnected": t("Getrennt"),
            "peer.disconnected_remote": t("Von der Gegenseite getrennt"),
            "peer.removed": t("Entfernt"),
            "sig.rejected": t("Signatur abgewiesen"),
            "sig.replay": t("Wiederholte Anfrage abgewiesen"),
            "share.set": t("Freigabe"),
            "share.removed": t("Freigabe entfernt"),
            "person.gone": t("Person entfernt"),
            "event.dropped": t("Ereignis verworfen"),
            "peer.capabilities": t("Fähigkeiten der Gegenseite"),
            "peer.options": t("Schalter geändert"),
            "chain.gap": t("Lücke in einer Ereignis-Kette"),
            "peer.downgrade": t("Rückfall auf Protokoll 1"),
            "peer.busy": t("Gegenseite bremst (zu viele Anfragen)")}


def _fed_page(request, me, **kw):
    st = federation.admin_state()
    if calls.horn_on():                              # 2.5 P10: Teilen der Knoten je Server (saga/fed_horn.py)
        from saga import fed_horn
        with _db() as c:
            st["horn"] = fed_horn.admin_state(c)
    return _page(request, "federation.html", me, fed=st, events=_fed_events(),
                 code=request.session.pop("fed_code", None), **kw)


@admin.get("/federation")
def federation_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    return _fed_page(request, me)


@admin.post("/federation")
async def federation_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/federation")
    act, who = f.get("action"), me["username"]
    pid = int(f.get("id")) if str(f.get("id") or "").isdigit() else 0
    try:
        if act == "enable":
            await run_in_threadpool(federation.set_enabled, _on(f, "on"))
            print(f"admin: {who} schaltet Foederation {'ein' if _on(f, 'on') else 'aus'}", flush=True)
            _flash(request, t("Gespeichert."))
        elif not federation.enabled():
            _flash(request, t("Die Föderation ist ausgeschaltet."), "err")
        elif act == "invite":
            request.session["fed_code"] = await run_in_threadpool(federation.make_invite, who)
        elif act == "drop_invite":
            await run_in_threadpool(federation.drop_invite, pid)
        elif act == "preview":
            info = await run_in_threadpool(federation.preview, _s(f, "url", 300))
            return _fed_page(request, me, preview=info, form_code=_s(f, "code", 100))
        elif act == "connect":
            await run_in_threadpool(federation.connect, _s(f, "url", 300), _s(f, "code", 100), _s(f, "fingerprint", 100))
            print(f"admin: {who} sendet Verbindungsanfrage an {_s(f, 'url', 300)}", flush=True)
            _flash(request, t("Anfrage gesendet. Sobald der Admin der anderen Instanz bestätigt, ist die Verbindung aktiv."))
        elif act == "confirm":
            ok, err = await run_in_threadpool(federation.confirm, pid, who)
            if ok:
                _flash(request, t("Verbindung bestätigt.") + (" " + t("Die andere Instanz war nicht erreichbar ({err}) – dort „Status prüfen“.", err=err) if err else ""),
                       "ok" if not err else "err")
        elif act == "refresh":
            st = await run_in_threadpool(federation.refresh, pid)
            _flash(request, t("Status der Gegenseite: {st}", st=st or "–"))
        elif act in ("disconnect", "reject"):
            if await run_in_threadpool(federation.disconnect, pid, who):
                print(f"admin: {who} trennt Foederation {pid}", flush=True)
                _flash(request, t("Verbindung getrennt."))
        elif act == "remove":
            await run_in_threadpool(federation.remove, pid)
        elif act == "opts":                  # 2.3: Admin-Schalter je verbundener Instanz
            groups = [g for g in re.split(r"[,\s]+", _s(f, "directory_groups", 2000)) if g]
            new = await run_in_threadpool(federation.set_opts, pid, {"chat": _on(f, "chat"), "calls": _on(f, "calls"), "directory": _on(f, "directory"),
                                                                  "scope_exception": _on(f, "scope_exception"), "directory_groups": groups}, who)
            if new is not None:
                print(f"admin: {who} aendert Schalter der Foederation {pid}", flush=True)
                _flash(request, t("Gespeichert."))
        elif act == "horn_share":            # 2.5 P10: Gjallarhorn-Knoten teilen, Kontingent, Notaus
            from saga import fed_horn

            def num(k, hi):
                v = str(f.get(k) or "").strip()
                return int(v) if v.isdigit() and 0 < int(v) <= hi else 0
            await run_in_threadpool(fed_horn.set_share, pid, _on(f, "allowed"), _on(f, "off"), num("legs", 1000), num("mbit", 10000), who)
            _flash(request, t("Gespeichert."))
        elif act == "hello":
            caps = await run_in_threadpool(federation.hello, pid)
            _flash(request, t("Fähigkeiten abgefragt.") if caps is not None else t("Die andere Saga-Instanz ist gerade nicht erreichbar."),
                   "ok" if caps is not None else "err")
    except federation.FedError as ex:
        _flash(request, t("Fehler: {err}", err=str(ex)), "err")
    return _go("/federation")


# JSON-Schnittstelle (2.3) fuer Skripte/Apps im Admin-Bereich: Sitzung des Admins + Kopfzeile X-CSRF-Token (= Token der
# Sitzung, steht in jedem Admin-Formular). GET liefert den Stand der Foederation inkl. Faehigkeiten und Schalter je Instanz.
def _api_admin(request):
    me = _me(request)
    if not me:
        return None, JSONResponse({"error": "unauthorized"}, status_code=401)
    if request.method != "GET" and not secrets.compare_digest(str(request.headers.get("x-csrf-token") or ""), str(request.session.get("csrf") or "-")):
        return None, JSONResponse({"error": "csrf"}, status_code=403)
    return me, None


@admin.get("/api/federation")
def api_federation(request: Request):
    me, err = _api_admin(request)
    if err:
        return err
    st = federation.admin_state()
    st.pop("log", None)
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


@admin.put("/api/federation/peers/{pid}")
async def api_federation_peer(pid: int, request: Request):
    """{chat?, calls?, directory?, scope_exception?, directory_groups?: [..]} - Admin-Schalter einer Instanz."""
    me, err = _api_admin(request)
    if err:
        return err
    try:
        b = await request.json()
    except ValueError:
        b = None
    if not isinstance(b, dict):
        return JSONResponse({"error": "bad request"}, status_code=400)
    new = await run_in_threadpool(federation.set_opts, pid, b, me["username"])
    if new is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"id": pid, "opts": new}, headers={"Cache-Control": "no-store"})


# --- Mitteilungen aufs Handy/Desktop (Web Push, ntfy/UnifiedPush, APNs) ------------------------------------
def _push_stats():
    with _db() as c:
        by = c.execute("SELECT kind, COUNT(*) FROM push_sub GROUP BY kind ORDER BY kind").fetchall()
        q = c.execute("SELECT COUNT(*) FROM push_queue").fetchone()[0]
    return {"subs": sum(r[1] for r in by), "queue": q, "by": [(r[0], r[1]) for r in by], "streams": push_stream.count()}


@admin.get("/settings/push")
def push_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    st = auth.settings(fresh=True)
    try:
        vapid = push.vapid()[1]
    except Exception as ex:  # noqa: BLE001
        print(f"admin: VAPID: {type(ex).__name__}", flush=True)
        vapid = ""
    a = dict(st.get("apns") or {})
    return _page(request, "push.html", me, p=st.get("push") or {}, a=a, has_p8=bool(a.get("p8")), apns_ready=push.apns_cfg()["ready"],
                 vapid=vapid, stats=_push_stats())


@admin.post("/settings/push")
async def push_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/settings/push")
    auth.save_setting("push", {k: _on(f, k) for k in ("enabled", "webpush", "ntfy", "unifiedpush", "stream")})
    cur = auth.settings(fresh=True).get("apns") or {}
    a = {"enabled": _on(f, "apns_enabled"), "team_id": _s(f, "team_id", 20).upper(), "key_id": _s(f, "key_id", 20).upper(),
         "bundle_id": _s(f, "bundle_id", 200), "p8": "", "url": cur.get("url", "")}
    for k, rx in (("team_id", r"[A-Z0-9]{10}"), ("key_id", r"[A-Z0-9]{10}"), ("bundle_id", r"[A-Za-z0-9.-]{3,200}")):
        if a[k] and not re.fullmatch(rx, a[k]):
            _flash(request, t("APNs: {field} ungültig.", field={"team_id": "Team-ID", "key_id": "Key-ID", "bundle_id": "Bundle-ID"}[k]), "err")
            return _go("/settings/push")
    up = f.get("p8")
    if up is not None and hasattr(up, "read"):
        raw = (await up.read(16384)).decode("utf-8", "replace").strip()
        if raw:
            if not push.apns_key_ok(raw):
                _flash(request, t("APNs: Die Datei ist kein gültiger .p8-Schlüssel (privater EC-Schlüssel P-256)."), "err")
                return _go("/settings/push")
            a["p8"] = raw
    if a["enabled"] and not (a["team_id"] and a["key_id"] and a["bundle_id"] and (a["p8"] or cur.get("p8"))):
        _flash(request, t("Für APNs Team-ID, Key-ID, Bundle-ID und Schlüssel angeben."), "err")
        return _go("/settings/push")
    auth.save_setting("apns", a)                 # leerer Schluessel = bisherigen behalten (verschluesselt gespeichert)
    if _on(f, "clear_p8"):
        with _db() as c, c:
            st = json.loads(c.execute("SELECT v FROM setting WHERE k='apns'").fetchone()[0])
            st["p8"], st["enabled"] = "", False
            c.execute("UPDATE setting SET v=? WHERE k='apns'", (json.dumps(st, ensure_ascii=False),))
        auth._settings_cache["v"] = None
    print(f"admin: {me['username']} speichert Mitteilungen (APNs aktiv={a['enabled']})", flush=True)
    _flash(request, t("Gespeichert."))
    return _go("/settings/push")


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


# --- Einstellungen: KI-Assistent (1.6, saga/ai.py) --------------------------------------------------------
@admin.get("/settings/ai")
def ai_page(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    a = auth.settings(fresh=True)["ai"]
    n, people = ai.usage_today()
    return _page(request, "ai.html", me, a=a, has_key=bool(a.get("api_key")), groups_text=", ".join(a.get("groups") or []),
                 test=request.session.pop("ai_test", None), used=n, people=people, parallel=ai.PARALLEL,
                 models=ai.LAST_MODELS["list"][:300], web_ok=ai.ai_web.available(), **_persona_ctx())


def _persona_ctx():
    p = dict(auth.settings(fresh=True).get("ai_persona") or {})
    url, _etag = ai.persona_avatar()
    return {"p": {"name": ai.clean_name(p.get("name")), "tone": ai.clean_tone(p.get("tone"))}, "persona_name": ai.persona_name(p),
            "default_name": ai.default_name(), "has_av": bool(url), "av_v": (url or "").rsplit("=", 1)[-1],
            "av_max": avatars.UPLOAD_MAX // 1048576}


def _ai_from(f, cur):
    """Formular -> (werte, fehler). Leerer Schluessel = bisherigen behalten (auth.save_setting)."""
    prov = f.get("provider") if f.get("provider") in ai.PROVIDERS else "openai"
    url, err = ai.norm_url(_s(f, "url", 300), prov) if _s(f, "url", 300) else ("", None)

    def num(k, lo, hi, dflt):
        try:
            return max(lo, min(hi, int(str(f.get(k) or dflt).strip())))
        except ValueError:
            return dflt
    v = {"enabled": _on(f, "enabled"), "provider": prov, "url": url or "", "model": _s(f, "model", 200),
         "api_key": str(f.get("api_key") or "").strip()[:500], "groups": _groups_list(_s(f, "groups", 1000)),
         "timeout": num("timeout", 5, 600, 60), "quota": num("quota", 0, 100000, 50), "context": num("context", 1000, 50000, 8000),
         "history": _on(f, "history"), "pinned": list(cur.get("pinned") or []), "chat": _on(f, "chat"),
         "web": _on(f, "web") and ai.ai_web.available()}
    return v, err


@admin.post("/settings/ai")
async def ai_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/settings/ai")
    cur = auth.settings(fresh=True)["ai"]
    v, err = _ai_from(f, cur)
    if err:
        _flash(request, err, "err")
        return _go("/settings/ai")
    if f.get("action") == "test":
        cfg = dict(v, api_key=v["api_key"] or cur.get("api_key", ""))
        if not cfg["url"] or not cfg["model"]:
            request.session["ai_test"] = [t("✗ Adresse und Modell angeben.")]
        else:
            try:
                request.session["ai_test"] = await run_in_threadpool(ai.test_connection, cfg)
            except Exception as ex:  # noqa: BLE001
                request.session["ai_test"] = [f"✗ {type(ex).__name__}"]
        return _go("/settings/ai")
    if v["enabled"] and (not v["url"] or not v["model"]):
        _flash(request, t("Zum Einschalten Adresse und Modell angeben."), "err")
        return _go("/settings/ai")
    if v["url"]:
        # DNS festhalten: interne Adressen sind nur mit genau diesen IPs erlaubt (gegen spaetere Umleitung per DNS)
        ips, err = await run_in_threadpool(ai.resolve, v["url"])
        if err:
            _flash(request, err, "err")
            return _go("/settings/ai")
        v["pinned"] = ips
    else:
        v["pinned"] = []
    clear = _on(f, "clear_key")
    if clear:
        v["api_key"] = ""
    auth.save_setting("ai", v)
    if clear:
        with _db() as c, c:
            st = json.loads(c.execute("SELECT v FROM setting WHERE k='ai'").fetchone()[0])
            st["api_key"] = ""
            c.execute("UPDATE setting SET v=? WHERE k='ai'", (json.dumps(st, ensure_ascii=False),))
        auth._settings_cache["v"] = None
    print(f"admin: {me['username']} speichert KI-Assistent (aktiv={v['enabled']}, Chats={v['chat']}, Websuche={v['web']})", flush=True)
    # Clients blenden die KI ein/aus (Live-Ereignis) - an alle, die sie jetzt oder bisher nutzen durften
    if any(bool(cur.get(k)) != bool(v[k]) for k in ("enabled", "chat", "web")) or sorted(cur.get("groups") or []) != sorted(v["groups"]):
        with _db() as c, c:
            ai.announce_persona(c, before=cur)
    _flash(request, t("Einstellungen für den KI-Assistenten gespeichert."))
    return _go("/settings/ai")


# --- Persona der KI (2.1): Name, Profilbild, Tonfall ---------------------------------------------------------------
@admin.post("/settings/ai/persona")
async def ai_persona_post(request: Request):
    """Formular (multipart): name, tone, image (Datei, optional), action = save | remove_image."""
    me = _me(request)
    if not me:
        return _go("/login")
    try:
        if int(request.headers.get("content-length") or 0) > avatars.UPLOAD_MAX + 64 * 1024:
            _flash(request, t("Das Bild ist zu groß (höchstens {n} MB).", n=avatars.UPLOAD_MAX // 1048576), "err")
            return _go("/settings/ai#persona")
    except ValueError:
        return _go("/settings/ai#persona")
    f = await _form(request)
    if f is None:
        return _go("/settings/ai#persona")
    if f.get("action") == "remove_image":
        if await run_in_threadpool(ai.set_avatar, None):
            print(f"admin: {me['username']} entfernt das Profilbild der KI", flush=True)
        _flash(request, t("Profilbild der KI entfernt."))
        return _go("/settings/ai#persona")
    raw_name = str(f.get("name") or "")
    name, tone = ai.clean_name(raw_name), ai.clean_tone(f.get("tone"))
    if raw_name.strip() and not name:
        _flash(request, t("Der Name enthält keine erlaubten Zeichen. Erlaubt: Buchstaben, Ziffern, Leerzeichen, Punkt, Bindestrich, Apostroph."), "err")
        return _go("/settings/ai#persona")
    up = f.get("image")
    enc = None
    if up is not None and hasattr(up, "read") and getattr(up, "filename", ""):
        data = await up.read(avatars.UPLOAD_MAX + 1)
        if data:
            if len(data) > avatars.UPLOAD_MAX:
                _flash(request, t("Das Bild ist zu groß (höchstens {n} MB).", n=avatars.UPLOAD_MAX // 1048576), "err")
                return _go("/settings/ai#persona")
            if not avatars.is_image(data[:16]):
                _flash(request, t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP, HEIC)"), "err")
                return _go("/settings/ai#persona")
            enc = await run_in_threadpool(avatars.encode, data)
            if not enc:
                _flash(request, t("Das Bild ließ sich nicht lesen (beschädigt oder kleiner als 16 × 16 Pixel)."), "err")
                return _go("/settings/ai#persona")
    old = dict(auth.settings(fresh=True).get("ai_persona") or {})
    auth.save_setting("ai_persona", {"name": name, "tone": tone})
    img = await run_in_threadpool(ai.set_avatar, enc) if enc else False
    if not img and (old.get("name") or "") != name:       # Bild geaendert: set_avatar hat das Ereignis schon geschickt
        with _db() as c, c:
            ai.announce_persona(c)
    print(f"admin: {me['username']} speichert Persona der KI (Name={'eigener' if name else 'Standard'}, "
          f"Tonfall={'ja' if tone else 'nein'}, Bild={'neu' if img else 'unveraendert'})", flush=True)
    _flash(request, t("Persona der KI gespeichert."))
    return _go("/settings/ai#persona")


@admin.get("/settings/ai/avatar")
def ai_avatar_admin(request: Request):
    """Vorschau im Admin-Bereich (eigener Port, eigene Sitzung - /api/ai/avatar kennt die Admin-Sitzung nicht)."""
    if not _me(request):
        return Response(status_code=404)
    got = avatars.image(ai.AV_UID, "upload", 128, "std")
    if not got:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(bytes(got[1]), media_type=got[0], headers=dict(avatars._IMG_H, **{"Cache-Control": "no-store"}))


# --- Backups (2.3, saga/backups.py): verschluesselt auf WebDAV/Nextcloud ----------------------------------------------
_bk_keys = {}                 # Token -> {priv, pub, aid, until} - privater Schluessel NUR im Speicher, bis bestaetigt (15 Min.)
_bk_lock = threading.Lock()
BK_KEY_TTL = 900


def _bk_pending(request):
    tok = request.session.get("bk_key")
    with _bk_lock:
        for k in [k for k, v in _bk_keys.items() if v["until"] < time.time()]:
            _bk_keys.pop(k, None)
        p = _bk_keys.get(tok) if tok else None
    if p and p["aid"] != request.session.get("aid"):
        return None, None
    return tok, p


def _bk_runs(limit=12):
    with _db() as c:
        rows = c.execute("SELECT id, started, finished, trigger, name, ok, size, step, detail, error FROM backup_run "
                         "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            det = json.loads(r[8] or "{}")
        except ValueError:
            det = {}
        out.append({"id": r[0], "started": r[1], "finished": r[2], "trigger": r[3], "name": r[4], "ok": bool(r[5]), "size": r[6],
                    "step": backups.text_json(r[7]) if r[7] else "", "detail": det,
                    "error": backups.text_json(r[9]) if r[9] else "",
                    "dur": int(r[2] - r[1]) if r[2] else None})
    return out


def _bk_ctx(request, cfg, show_remote=False):
    st = backups.state()
    runs = _bk_runs()
    busy = backups.running()
    remote, remote_err = None, ""
    if show_remote and backups.has_target(cfg):
        dav = backups.target(cfg)
        try:
            remote = backups.list_runs(dav, manifests=True)
        except backups.BackupError as ex:
            remote_err = backups.text(ex.code, ex.p)
        finally:
            dav.close()
    tok, pend = _bk_pending(request)
    lc = float(st.get("last_check") or 0)
    fl = backups.flow_state()
    code = fl.get("code") or ""
    fl_msg = (t("Anmeldung abgelaufen – bitte neu verbinden.") if code == "flow_expired"
              else backups.text(code.split(":", 1)[-1], fl.get("p")) if code else "")
    if fl.get("state") in ("ok", "err"):
        backups.flow_cancel()                     # Ergebnis der Nextcloud-Anmeldung nur einmal zeigen
    return {"cfg": cfg, "st": st, "runs": runs, "busy": busy, "last_ok": backups.last_ok(), "next": backups.next_run(cfg, st),
            "recips": [{"key": r, "short": backups.short_key(r), "fp": backups.fingerprint(r)} for r in backups.recipients(cfg)],
            "flow": fl, "flow_msg": fl_msg, "age_ok": backups.age_ok(), "remote": remote, "remote_err": remote_err,
            "pending": pend, "pending_tok": tok, "restore_test": hk_restore_test(), "size": backups.size_text,
            "ready": backups.has_target(cfg) and bool(backups.recipients(cfg)) and backups.age_ok(),
            "check_due": lc < time.time() - 31 * 86400, "last_check": lc, "roots": backups.backup_roots(),
            "ub": userbackup.admin_cfg(), "ub_users": _ub_users(),
            "s3_key_short": backups.short_key(cfg.get("s3_key") or "") if cfg.get("mode") == "s3" else ""}


def _ub_users():
    """Anzahl Konten mit persoenlichem Backup-Ziel (nur die Zahl, keine Namen/Ziele)."""
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM user_backup WHERE COALESCE(cfg::jsonb->>'mode', '') <> ''").fetchone()[0]


def hk_restore_test():
    return RESTORE_TEST


@admin.get("/backups")
def backups_page(request: Request, ziel: int = 0):
    me = _me(request)
    if not me:
        return _go("/login")
    cfg = backups.config()
    ctx = _bk_ctx(request, cfg, show_remote=bool(ziel))
    return _page(request, "backups.html", me, bk=ctx, show_remote=bool(ziel))


@admin.get("/backups/schluessel/{tok}")
def backups_key_download(tok: str, request: Request):
    """Privaten Schluessel herunterladen (nur waehrend der Bestaetigung, nur fuer diesen Admin, nie gespeichert)."""
    if not _me(request):
        return Response(status_code=404)
    cur, p = _bk_pending(request)
    if not p or cur != tok:
        return PlainTextResponse(t("Abgelaufen – bitte neu erzeugen."), status_code=404, headers=HEADERS)
    host = re.sub(r"[^a-z0-9.-]+", "-", (urlparse(hk.BASE_URL).hostname or "saga").lower())[:60]
    return Response(p["priv"], media_type="text/plain; charset=utf-8",
                    headers=dict(HEADERS, **{"Content-Disposition": f'attachment; filename="saga-backup-{host}.key"'}))


_BK_CLEAR = backups.TARGET_CLEAR


def _bk_finish(new, cfg, kind):
    """Neues Ziel pruefen und speichern (vorheriges Nextcloud-App-Passwort widerrufen) -> (art, meldung)."""
    try:
        free = backups.test_target(new)
    except backups.BackupError as ex:
        return "err", backups.text(ex.code, ex.p)
    if cfg.get("flow") and cfg.get("password") and cfg.get("server"):
        backups.nc_revoke(cfg["server"], cfg.get("user") or "", cfg["password"])
    backups.save_config(new)
    return "ok", t("Verbunden ({kind}). Frei im Ziel: {free}.", kind=kind,
                   free=backups.size_text(free) if free is not None else t("unbekannt"))


def _bk_connect_s3(f, cfg):
    ep = backups.norm_url(_s(f, "s3_endpoint", 300))
    if not ep or urlparse(ep).path not in ("", "/"):
        return "err", t("Endpunkt ungültig (https://host[:port], ohne Pfad).")
    bucket, region = _s(f, "s3_bucket", 63), _s(f, "s3_region", 40) or "us-east-1"
    prefix = backups.s3_norm_prefix(_s(f, "s3_prefix", 200))
    style = _s(f, "s3_style", 10) or "auto"
    if not backups.S3_BUCKET_RE.match(bucket):
        return "err", t("Bucket-Name ungültig.")
    if not backups.S3_REGION_RE.match(region) or prefix is None or style not in backups.S3_STYLES:
        return "err", t("Region oder Präfix ungültig (Präfix: Buchstaben, Ziffern, . _ - /).")
    key, secret = _s(f, "s3_key", 200), str(f.get("s3_secret") or "").strip()[:500]
    if not key or not secret:
        return "err", t("Access-Key und Secret-Key angeben.")
    new = dict(cfg, **_BK_CLEAR)
    new.update(mode="s3", s3_endpoint=ep, s3_region=region, s3_bucket=bucket, s3_prefix=prefix, s3_key=key, s3_secret=secret,
               s3_style=style)
    return _bk_finish(new, cfg, "S3")


def _bk_connect_folder(f, cfg):
    path = backups.norm_folder(_s(f, "path", 400))
    if not path:
        return "err", backups.text("folder_path")
    new = dict(cfg, **_BK_CLEAR)
    new.update(mode="folder", path=path)
    return _bk_finish(new, cfg, t("Ordner"))


def _bk_connect(f, cfg):
    """Ziel verbinden: Nextcloud ohne Zugangsdaten -> Login Flow v2, sonst Benutzer/Passwort pruefen + speichern;
    kind=s3 bzw. folder: S3 oder Ordner im Container. -> (art, meldung) mit art ok | flow | err."""
    kind = _s(f, "kind", 10)
    if kind == "s3":
        return _bk_connect_s3(f, cfg)
    if kind == "folder":
        return _bk_connect_folder(f, cfg)
    url = backups.norm_url(_s(f, "url", 500))
    folder = _s(f, "folder", 200).strip("/") or cfg.get("folder") or "Saga-Backups"
    if not url:
        return "err", t("Adresse ungültig (http:// oder https://, ohne Zugangsdaten in der Adresse).")
    if ".." in folder.split("/") or not re.fullmatch(r"[^\x00-\x1f\\]{1,200}", folder):
        return "err", t("Ordnername ungültig.")
    user, pw = _s(f, "user", 200), str(f.get("password") or "")[:500]
    try:
        info = backups.detect(url)
        if info["nextcloud"] and not user:
            backups.flow_start(info["server"], folder)
            return "flow", ""
        new = dict(cfg, **_BK_CLEAR)
        new.update(folder=folder, flow=False, user=user, password=pw)
        if info["nextcloud"]:
            new.update(mode="nextcloud", server=info["server"], url=info["server"], dav_user=backups.nc_user_id(info["server"], user, pw))
        else:
            new.update(mode="webdav", server="", url=url, dav_user="")
    except backups.BackupError as ex:
        return "err", backups.text(ex.code, ex.p)
    return _bk_finish(new, cfg, "Nextcloud" if info["nextcloud"] else "WebDAV")


@admin.post("/backups")
async def backups_post(request: Request):
    me = _me(request)
    if not me:
        return _go("/login")
    f = await _form(request)
    if f is None:
        return _go("/backups")
    act = _s(f, "action", 40)
    cfg = backups.config()
    who = me["username"]
    if act == "connect":
        kind, msg = await run_in_threadpool(_bk_connect, f, cfg)
        print(f"admin: {who} verbindet Backup-Ziel ({kind})", flush=True)
        if kind != "flow":
            _flash(request, msg, "ok" if kind == "ok" else "err")
        return _go("/backups#ziel")
    if act == "flow_cancel":
        backups.flow_cancel()
        return _go("/backups#ziel")
    if act == "disconnect":
        revoked = None
        if cfg.get("flow") and cfg.get("password") and cfg.get("server"):
            revoked = await run_in_threadpool(backups.nc_revoke, cfg["server"], cfg.get("user") or "", cfg["password"])
        cfg.update(mode="", **_BK_CLEAR)
        backups.save_config(cfg)
        backups.flow_cancel()
        print(f"admin: {who} trennt das Backup-Ziel (App-Passwort widerrufen: {revoked})", flush=True)
        _flash(request, t("Ziel getrennt, App-Passwort in Nextcloud widerrufen.") if revoked else
               (t("Ziel getrennt. Das App-Passwort konnte nicht widerrufen werden – bitte in Nextcloud unter Einstellungen → Sicherheit löschen.")
                if revoked is False else t("Ziel getrennt.")), "ok" if revoked is not False else "err")
        return _go("/backups#ziel")
    if act == "test":
        try:
            free = await run_in_threadpool(backups.test_target, cfg)
            _flash(request, t("Verbindung in Ordnung. Frei im Ziel: {free}.", free=backups.size_text(free) if free is not None else t("unbekannt")))
        except backups.BackupError as ex:
            _flash(request, backups.text(ex.code, ex.p), "err")
        return _go("/backups#ziel")
    if act == "run":
        if hk_restore_test():
            _flash(request, t("Testwiederherstellung (SAGA_RESTORE_TEST=1): keine Backups."), "err")
        elif await run_in_threadpool(backups.running):
            _flash(request, backups.text("busy"), "err")
        else:
            backups.run_async("manuell")
            print(f"admin: {who} startet ein Backup", flush=True)
            _flash(request, t("Backup gestartet."))
        return _go("/backups")
    if act == "add_key":
        keys = [k for k in re.split(r"[\r\n]+", str(f.get("key") or "")[:5000]) if k.strip() and not k.strip().startswith("#")]
        got = [await run_in_threadpool(backups.norm_recipient, k) for k in keys[:10]]
        if not keys or None in got:
            _flash(request, t("Kein gültiger öffentlicher Schlüssel (age1… oder ssh-ed25519 …). Private Schlüssel (AGE-SECRET-KEY-…) gehören nicht hierher."), "err")
            return _go("/backups#schluessel")
        rs = backups.recipients(cfg)
        for r in got:
            if r not in rs:
                rs.append(r)
        if len(rs) > 10:
            _flash(request, t("Höchstens {n} Schlüssel.", n=10), "err")
            return _go("/backups#schluessel")
        cfg["recipients"] = rs
        backups.save_config(cfg)
        print(f"admin: {who} fuegt einen Backup-Schluessel hinzu", flush=True)
        _flash(request, t("Schlüssel hinzugefügt. Neue Backups sind ab jetzt (auch) mit diesem Schlüssel lesbar."))
        return _go("/backups#schluessel")
    if act == "del_key":
        fp = _s(f, "fp", 40)
        cfg["recipients"] = [r for r in backups.recipients(cfg) if backups.fingerprint(r) != fp]
        backups.save_config(cfg)
        print(f"admin: {who} entfernt einen Backup-Schluessel", flush=True)
        _flash(request, t("Schlüssel entfernt. Ältere Backups bleiben mit ihm lesbar."))
        return _go("/backups#schluessel")
    if act == "genkey":
        if not backups.age_ok():
            _flash(request, t("age fehlt auf dem Server."), "err")
            return _go("/backups#schluessel")
        priv, pub = await run_in_threadpool(backups.keygen)
        tok = secrets.token_urlsafe(18)
        with _bk_lock:
            _bk_keys[tok] = {"priv": priv, "pub": pub, "aid": request.session.get("aid"), "until": time.time() + BK_KEY_TTL}
        request.session["bk_key"] = tok
        print(f"admin: {who} erzeugt ein Backup-Schluesselpaar (privater Schluessel wird nicht gespeichert)", flush=True)
        return _go("/backups#neuer-schluessel")
    if act in ("confirm_key", "drop_key"):
        tok, p = _bk_pending(request)
        if act == "drop_key" or not p:
            with _bk_lock:
                _bk_keys.pop(tok, None)
            request.session.pop("bk_key", None)
            if not p and act == "confirm_key":
                _flash(request, t("Abgelaufen – bitte neu erzeugen."), "err")
            return _go("/backups#schluessel")
        last = p["priv"].strip().splitlines()[-1].strip()[-6:]
        if _s(f, "last6", 20).upper() != last.upper():
            _flash(request, t("Die letzten 6 Zeichen stimmen nicht – bitte den privaten Schlüssel noch einmal prüfen."), "err")
            return _go("/backups#neuer-schluessel")
        rs = backups.recipients(cfg)
        if p["pub"] not in rs:
            rs.append(p["pub"])
        cfg["recipients"] = rs[-10:]
        backups.save_config(cfg)
        with _bk_lock:
            _bk_keys.pop(tok, None)
        request.session.pop("bk_key", None)
        print(f"admin: {who} bestaetigt einen neuen Backup-Schluessel", flush=True)
        _flash(request, t("Schlüssel eingetragen. Der private Schlüssel ist jetzt nur noch bei dir – Saga hat ihn vergessen."))
        return _go("/backups#schluessel")
    if act == "ub_save":
        try:
            mx, mh = int(f.get("ub_max_mb") or 0), int(f.get("ub_min_h") or 0)
        except ValueError:
            _flash(request, t("Nur ganze Zahlen."), "err")
            return _go("/backups#persoenlich")
        hosts = [h for h in re.split(r"[,\s]+", _s(f, "ub_hosts", 2000).lower()) if h]
        if any(not re.fullmatch(r"[a-z0-9.\-]+(:\d{1,5})?|\[[0-9a-f:]+\](:\d{1,5})?", h) for h in hosts):
            _flash(request, t("Host ungültig (Name oder IP, optional mit :Port)."), "err")
            return _go("/backups#persoenlich")
        cur = dict(auth.settings(fresh=True).get("user_backup") or {})
        cur.update(enabled=_on(f, "ub_enabled"), max_mb=max(1, min(userbackup.HARD_MAX_MB, mx or 1)), min_h=max(0, min(168, mh)),
                   max_schedule="weekly" if _s(f, "ub_max_schedule", 10) == "weekly" else "daily", hosts=", ".join(hosts))
        auth.save_setting("user_backup", cur)
        print(f"admin: {who} speichert persoenliche Backups (an={cur['enabled']}, {cur['max_mb']} MB, {cur['min_h']} h, "
              f"{cur['max_schedule']}, {len(hosts)} Host(s))", flush=True)
        _flash(request, t("Gespeichert."))
        return _go("/backups#persoenlich")
    if act == "save":
        tm = _s(f, "time", 5)
        if not backups.TIME_RE.match(tm):
            _flash(request, t("Uhrzeit ungültig (hh:mm)."), "err")
            return _go("/backups#plan")
        try:
            keep = {k: max(0, min(365, int(f.get(k) or 0))) for k in ("keep_d", "keep_w", "keep_m")}
        except ValueError:
            _flash(request, t("Aufbewahrung: nur ganze Zahlen."), "err")
            return _go("/backups#plan")
        if not any(keep.values()):
            keep["keep_d"] = 1
        hook = _s(f, "webhook", 500)
        if hook and not backups.norm_url(hook.split("?")[0]):
            _flash(request, t("Webhook-Adresse ungültig."), "err")
            return _go("/backups#plan")
        on = _on(f, "enabled")
        cfg.update(time=tm, secrets=_on(f, "secrets"), **keep)
        if on and not cfg.get("enabled"):
            cfg["since"] = time.time()
        cfg["enabled"] = on
        if hook:
            cfg["webhook"] = hook
        elif _on(f, "clear_webhook"):
            cfg["webhook"] = ""
        backups.save_config(cfg)
        print(f"admin: {who} speichert Backup-Zeitplan (an={on}, {tm}, {keep})", flush=True)
        _flash(request, t("Gespeichert."))
        return _go("/backups#plan")
    return _go("/backups")
