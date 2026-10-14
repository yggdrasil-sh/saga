"""Anmeldung im Browser: Login, SSO, Registrierung, Abmelden und das eigene Konto."""
import re
import secrets
import time
from urllib.parse import urlencode
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
import auth
from db import connect as _db
from fastapi import APIRouter
from saga import i18n
from saga.core import (ADMIN_URL, ASSET_V, BASE_URL, SEC_HEADERS, _api_guard, _bad, _body, _session_user, _set_session, set_user_lang, t,
                       title, tmpl, user_info, user_lang)
from saga.dashboard import source_label

router = APIRouter()


# --- Anmeldung, Registrierung, Abmeldung -------------------------------------------------------------
# Erlaubte Ruecksprungziele nach dem Login - feste Positivliste statt beliebiger Pfade
# (verhindert Open-Redirect ueber praeparierte Login-Links)
NEXT_ALLOWED = {"/", "/notes", "/app/handoff"}
EMAIL_RE = re.compile(r"[^@\s]{1,100}@[^@\s]{1,150}\.[^@\s]{2,40}")


def _next(v):
    return v if v in NEXT_ALLOWED else "/"


PROXY_WARN = {"at": 0.0, "ip": ""}      # zuletzt erkannter, nicht vertrauter Reverse Proxy (Hinweis im Admin-Bereich)


def _ip(request):
    ip = (request.client.host if request.client else "") or "?"
    if request.headers.get("x-forwarded-for"):
        _check_proxy(ip, request.headers["x-forwarded-for"])
    return ip


def _check_proxy(ip, xff):
    """X-Forwarded-For von einer privaten Adresse, die nicht in TRUSTED_PROXIES steht: dann sehen alle Besucher
    wie dieser Proxy aus - Fehlversuch-Sperre und Registrierungsgrenze gelten fuer alle zusammen. Melden (Log,
    hoechstens stuendlich, und im Admin-Bereich), damit der Admin TRUSTED_PROXIES richtig setzt."""
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return
    first = xff.split(",")[0].strip()
    if not (a.is_private and not a.is_loopback) or not first or first == ip:
        return
    now = time.time()
    if now - PROXY_WARN["at"] > 3600:
        print(f"saga: WARNUNG - Anfragen kommen ueber den Proxy {ip} (X-Forwarded-For), der nicht in TRUSTED_PROXIES steht. "
              "Alle Besucher teilen sich dann eine Adresse (Fehlversuch-Sperre!). TRUSTED_PROXIES auf die Proxy-Adresse setzen.",
              flush=True)
    PROXY_WARN.update(at=now, ip=ip)


def _csrf(request):
    tok = request.session.get("csrf")
    if not tok:
        tok = request.session["csrf"] = secrets.token_urlsafe(24)
    return tok


def _csrf_ok(request, form):
    return secrets.compare_digest(str(form.get("csrf") or ""), str(request.session.get("csrf") or "-"))


def _auth_opts():
    st = auth.settings()
    o = st["oidc"]
    return {"pw_login": bool(st.get("local_login", True) or (st["ldap"].get("enabled") and st["ldap"].get("password_login", True))),
            "sso": bool(o.get("enabled") and o.get("issuer") and o.get("client_id")), "sso_label": _sso_label(o.get("label")),
            "registration": bool(st.get("registration")), "ldap": bool(st["ldap"].get("enabled"))}


def _sso_label(lbl):
    """Eigener Text aus dem Admin-Bereich bleibt; der Standardtext wird uebersetzt."""
    return t("Mit SSO anmelden") if not lbl or lbl == "Mit SSO anmelden" else lbl


def _auth_page(request, name, status=200, **kw):
    html = tmpl.get_template(name).render(title=title(), v=ASSET_V, csrf=_csrf(request), opts=_auth_opts(), **kw)
    return HTMLResponse(html, status_code=status, headers=SEC_HEADERS)


def _start_session(request, user, id_token=None):
    nxt = _next(request.session.get("next", "/"))
    auth._ui_cache.pop(user["username"], None)
    keep = {"login_at": int(time.time())}            # frische Anmeldung (Formular/SSO): /app/handoff ohne Rueckfrage
    if id_token and len(id_token) < 2500:
        keep["id_token"] = id_token
    _set_session(request, user["username"], user["display_name"] or user["username"], keep)
    return RedirectResponse(nxt, status_code=303)


@router.get("/login")
def login(request: Request):
    nxt = _next(request.query_params.get("next", request.session.get("next", "/")))
    if _session_user(request):
        return RedirectResponse(nxt, status_code=303)
    request.session["next"] = nxt
    o = _auth_opts()
    if o["sso"] and not o["pw_login"] and not request.query_params.get("bye"):
        return RedirectResponse("/login/sso", status_code=303)       # nur SSO eingerichtet: direkt weiter
    return _auth_page(request, "login.html", bye=bool(request.query_params.get("bye")))


@router.post("/login")
async def login_post(request: Request):
    form = await request.form()
    username = str(form.get("username") or "").strip()[:100]
    if not _csrf_ok(request, form):
        return _auth_page(request, "login.html", 400, error=t("Die Seite war zu lange offen – bitte noch einmal anmelden."), username=username)
    if not _auth_opts()["pw_login"]:
        return _auth_page(request, "login.html", 403, error=t("Anmeldung mit Passwort ist abgeschaltet."))
    allow_local = bool(auth.settings().get("local_login", True))
    user, err = await run_in_threadpool(auth.authenticate, username, str(form.get("password") or ""), _ip(request), allow_local)
    if not user:
        return _auth_page(request, "login.html", 401, error=err, username=username)
    return _start_session(request, user)


@router.get("/login/sso")
async def login_sso(request: Request):
    cl = await run_in_threadpool(auth.oidc_client)
    if cl is None:
        return RedirectResponse("/login", status_code=303)
    request.session.setdefault("next", "/")
    try:
        return await cl.authorize_redirect(request, BASE_URL + "/auth/callback")
    except Exception as ex:  # noqa: BLE001 - Anbieter nicht erreichbar / falsch eingerichtet
        print(f"sso: {type(ex).__name__}: {ex}", flush=True)
        return _auth_page(request, "login.html", 502, error=t("Der Anmeldedienst (SSO) ist gerade nicht erreichbar."))


@router.get("/auth/callback")
async def callback(request: Request):
    cl = await run_in_threadpool(auth.oidc_client)
    if cl is None:
        return RedirectResponse("/login", status_code=303)
    try:
        token = await cl.authorize_access_token(request)
    except Exception as ex:  # noqa: BLE001 - state passt nicht, Code abgelaufen, Anbieter weg ...
        print(f"sso-callback: {type(ex).__name__}: {ex}", flush=True)
        return _auth_page(request, "login.html", 400, error=t("SSO-Anmeldung fehlgeschlagen – bitte noch einmal versuchen."))
    claims = dict(token.get("userinfo") or {})
    gclaim = auth.settings()["oidc"].get("groups_claim")
    if gclaim and auth._claim(claims, gclaim) is None:          # Gruppen stehen bei manchen Anbietern nur im userinfo-Endpunkt
        try:
            extra = dict(await cl.userinfo(token=token))
            extra.pop("sub", None)
            claims.update(extra)
        except Exception:  # noqa: BLE001
            pass
    user, err = await run_in_threadpool(auth.oidc_user, claims)
    if not user:
        return _auth_page(request, "login.html", 403, error=err)
    auth.touch_login(user["username"])
    return _start_session(request, user, token.get("id_token"))


REG_MAX_PER_IP = 5      # Registrierungen je Stunde und Adresse


@router.get("/register")
def register(request: Request):
    if not _auth_opts()["registration"]:
        return RedirectResponse("/login", status_code=303)
    return _auth_page(request, "register.html", f={})


@router.post("/register")
async def register_post(request: Request):
    if not _auth_opts()["registration"]:
        return RedirectResponse("/login", status_code=303)
    form = await request.form()
    f = {k: str(form.get(k) or "").strip()[:200] for k in ("username", "name", "email")}

    def fail(msg, code=400):
        return _auth_page(request, "register.html", code, error=msg, f=f)
    if not _csrf_ok(request, form):
        return fail(t("Die Seite war zu lange offen – bitte noch einmal versuchen."))
    pw, pw2 = str(form.get("password") or ""), str(form.get("password2") or "")
    if pw != pw2:
        return fail(t("Die Passwörter stimmen nicht überein."))
    if f["email"] and not EMAIL_RE.fullmatch(f["email"]):
        return fail(t("Die E-Mail-Adresse sieht nicht richtig aus."))
    ip = _ip(request)
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", ("reg:" + ip, time.time() - 3600)).fetchone()[0]
    if n >= REG_MAX_PER_IP:
        return fail(t("Zu viele Registrierungen von dieser Adresse – bitte später erneut versuchen."), 429)
    u = auth.norm_username(f["username"])
    if u and _auth_opts()["ldap"]:
        try:
            taken = await run_in_threadpool(auth.ldap_exists, u)
        except Exception:  # noqa: BLE001 - Verzeichnis weg: lieber nicht registrieren
            return fail(t("Registrierung gerade nicht möglich (Verzeichnisdienst nicht erreichbar)."), 503)
        if taken:
            return fail(t("Diesen Benutzernamen gibt es schon."))
    try:
        user, err = await run_in_threadpool(auth.create_user, f["username"], pw, f["name"], f["email"])
    except auth.Busy:
        return fail(t("Gerade sehr viele Anmeldungen – bitte gleich noch einmal versuchen."), 503)
    if err:
        return fail(err)
    with _db() as c, c:
        c.execute("INSERT INTO login_fail (key, ts) VALUES (?,?)", ("reg:" + ip, time.time()))
    auth.touch_login(user["username"])
    print(f"register: neues Konto {user['username']}", flush=True)
    return _start_session(request, user)


@router.get("/logout")
async def logout(request: Request):
    id_token = request.session.get("id_token")
    psub, puid = request.session.get("push_sub"), request.session.get("uid")
    if psub and puid:                     # Mitteilungen an diesen Browser enden mit dem Abmelden (saga.push)
        with _db() as c, c:
            c.execute("DELETE FROM push_queue WHERE sub_id=?", (psub,))
            c.execute("DELETE FROM push_sub WHERE id=? AND uid=? AND kind='webpush'", (psub, puid))
    request.session.clear()
    if id_token:
        cl = await run_in_threadpool(auth.oidc_client)
        if cl is not None:
            try:
                end = (await cl.load_server_metadata()).get("end_session_endpoint")
            except Exception:  # noqa: BLE001
                end = None
            if end:
                q = {"post_logout_redirect_uri": BASE_URL + "/login?bye=1", "client_id": auth.settings()["oidc"]["client_id"],
                     "id_token_hint": id_token}
                return RedirectResponse(f"{end}?{urlencode(q)}", status_code=303)
    return RedirectResponse("/login?bye=1", status_code=303)


# --- Eigenes Konto -----------------------------------------------------------------------------------
@router.get("/api/account")
def api_account(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    i = user_info(uid)
    return JSONResponse({"username": i["username"], "name": i["name"], "email": i["mail"], "source": i["source"],
                         "source_label": source_label(i["source"]), "groups": sorted(i["groups"]),
                         "local_groups": sorted(i["local_groups"]), "is_admin": i["is_admin"],
                         "admin_url": ADMIN_URL if i["is_admin"] else "", "pw_changed": i["pw_changed"],
                         "can_edit": i["source"] == "local", "lang": user_lang(uid), "lang_active": i18n.get_lang(),
                         "langs": [{"code": "", "name": t("Automatisch")}] + [{"code": k, "name": v} for k, v in i18n.NAMES.items()]})


@router.patch("/api/account")
async def api_account_update(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    i = user_info(uid)
    if i["source"] != "local":
        return _bad(t("Name und E-Mail kommen von deinem Anmeldedienst und werden dort geändert."), 403)
    b = await _body(request) or {}
    name, email = str(b.get("name") or "").strip()[:120], str(b.get("email") or "").strip()[:200]
    if email and not EMAIL_RE.fullmatch(email):
        return _bad(t("Die E-Mail-Adresse sieht nicht richtig aus."))
    with _db() as c, c:
        c.execute("UPDATE users SET display_name=?, email=? WHERE username=?", (name, email, uid))
    auth._ui_cache.pop(uid, None)
    request.session["name"] = name or uid
    return JSONResponse({"ok": True, "name": name or uid})


@router.post("/api/account/password")
async def api_account_password(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    old, new = str(b.get("old") or ""), str(b.get("new") or "")
    u = auth.get_user(uid)
    if u["source"] != "local":
        return _bad(t("Dein Passwort verwaltet dein Anmeldedienst."), 403)
    if auth.too_many(uid, _ip(request)):
        return _bad(t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."), 429)
    try:
        ok = bool(u["password_hash"]) and await run_in_threadpool(auth.verify_password, old, u["password_hash"])
    except auth.Busy:
        return _bad(t("Gerade sehr viele Anmeldungen – bitte gleich noch einmal versuchen."), 503)
    if not ok:
        auth.note_fail(uid, _ip(request))
        return _bad(t("Das bisherige Passwort stimmt nicht."), 403)
    p = auth.password_problem(new)
    if p:
        return _bad(p)
    try:
        await run_in_threadpool(auth.set_password, uid, new)
    except auth.Busy:
        return _bad(t("Gerade sehr viele Anmeldungen – bitte gleich noch einmal versuchen."), 503)
    request.session["pwc"] = time.time()              # diese Sitzung bleibt, alle anderen Browser-Sitzungen enden
    # Standard: auch alle Apps/Geraete und App-Schluessel abmelden (wer sein Passwort wegen eines Verdachts aendert,
    # soll niemanden mit einem alten Geraete-Schluessel drin lassen). Ausnahme: das Geraet dieser Sitzung (Aenderung
    # in der App). Mit "logout_devices": false bleiben Apps angemeldet.
    n = 0
    if b.get("logout_devices", True) is not False:
        from saga.userdata import revoke_user_logins
        n = await run_in_threadpool(revoke_user_logins, uid, request.session.get("did"))
    return JSONResponse({"ok": True, "devices_revoked": n})


@router.put("/api/account/lang")
async def api_account_lang(request: Request):
    """Sprache der Web-Oberflaeche: 'de' / 'en' / 'es' oder '' (automatisch: Server-Standard bzw. Browser)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    lang = str(b.get("lang") or "")
    if lang and not i18n.norm(lang):
        return _bad(t("Unbekannte Sprache."))
    return JSONResponse({"ok": True, "lang": set_user_lang(uid, lang)})
