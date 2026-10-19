"""Anmeldung im Browser: Login, SSO, Registrierung, Abmelden und das eigene Konto."""
import re
import secrets
import time
from urllib.parse import urlencode
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
import auth
from db import clean_line, connect as _db
from fastapi import APIRouter
from saga import avatars, i18n, mfa
from saga.core import (ADMIN_URL, ASSET_V, BASE_URL, MEDIA_FRONT, SEC_HEADERS, _api_guard, _bad, _body, _session_user, _set_session, lang_switch_response,
                       set_user_lang, t, title, tmpl, user_info, user_lang)
from saga.dashboard import source_label

router = APIRouter()


# --- Anmeldung, Registrierung, Abmeldung -------------------------------------------------------------
# Erlaubte Ruecksprungziele nach dem Login - feste Positivliste statt beliebiger Pfade
# (verhindert Open-Redirect ueber praeparierte Login-Links)
NEXT_ALLOWED = {"/", "/notes", "/app/handoff", "/security"}
EMAIL_RE = re.compile(r"[^@\s]{1,100}@[^@\s]{1,150}\.[^@\s]{2,40}")


def _next(v):
    return v if v in NEXT_ALLOWED else "/"


PROXY_WARN = {"at": 0.0, "ip": ""}      # zuletzt erkannter, nicht vertrauter Reverse Proxy (Hinweis im Admin-Bereich)


def _ip(request):
    ip = (request.client.host if request.client else "") or "?"
    # hinter saga-media (2.4) kommt der vom nicht vertrauten Proxy mitgeschickte X-Forwarded-For in X-Saga-Orig-For
    # (saga-media setzt den Kopf immer selbst; X-Forwarded-For enthaelt dort nur noch den ermittelten Client)
    xff = request.headers.get("x-saga-orig-for") if MEDIA_FRONT else request.headers.get("x-forwarded-for")
    if xff:
        _check_proxy(ip, xff)
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
            "registration": bool(st.get("registration")), "ldap": bool(st["ldap"].get("enabled")),
            "passkey": not mfa.rp_problem(), "origin": mfa.ORIGIN}


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
    info = user_info(user["username"])
    if info and mfa.required(info) and not mfa.has_mfa(user["username"]):
        # Richtlinie verlangt einen zweiten Faktor, das Konto hat keinen: angemeldet (nicht ausgesperrt), aber zuerst
        # zur Einrichtung; danach weiter zum eigentlichen Ziel
        keep.update(mfa_setup=1, mfa_next=nxt)
        nxt = "/security"
    _set_session(request, user["username"], user["display_name"] or user["username"], keep)
    return RedirectResponse(nxt, status_code=303)


def _password_ok(request, user):
    """Passwort stimmt: mit zweitem Faktor erst der Zwischenschritt (kurzlebig, an dieses Sitzungs-Cookie gebunden)."""
    if not (mfa.supported(user) and mfa.has_mfa(user["username"])):
        return _start_session(request, user)
    nxt = _next(request.session.get("next", "/"))
    request.session.clear()
    request.session.update(mfa_u=user["username"], mfa_at=int(time.time()), next=nxt)
    return RedirectResponse("/login/2fa", status_code=303)


@router.get("/login")
def login(request: Request):
    nxt = _next(request.query_params.get("next", request.session.get("next", "/")))
    # prompt=login (App: Konto wechseln, /app/login?...&prompt=login): trotz Sitzung die Anmeldemaske zeigen
    if _session_user(request) and request.query_params.get("prompt") != "login":
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
    return _password_ok(request, user)


# --- Zweiter Faktor nach dem Passwort, Passkey-Anmeldung ohne Passwort (saga.mfa) ------------------------------
def _mfa_page(request, uid, status=200, error=None):
    f = mfa.factors(uid)
    prob = mfa.rp_problem() if f["passkeys"] else None
    return _auth_page(request, "login2fa.html", status, error=error, passkey=bool(f["passkeys"]) and not prob, passkey_problem=prob,
                      totp=f["totp"], recovery=f["recovery"], origin=mfa.ORIGIN)


@router.get("/login/2fa")
def login_2fa(request: Request):
    uid = mfa.pending(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    return _mfa_page(request, uid)


@router.post("/login/2fa")
async def login_2fa_post(request: Request):
    form = await request.form()
    uid = mfa.pending(request)
    if not uid:
        return _auth_page(request, "login.html", 400, error=t("Die Anmeldung hat zu lange gedauert – bitte noch einmal."))
    if not _csrf_ok(request, form):
        return _mfa_page(request, uid, 400, t("Die Seite war zu lange offen – bitte noch einmal versuchen."))
    ip = _ip(request)
    if auth.too_many(uid, ip, "mfa"):
        request.session.clear()
        return _auth_page(request, "login.html", 429, error=t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."))
    kind = await run_in_threadpool(mfa.check_code, uid, str(form.get("code") or "")[:60])
    if not kind:
        auth.note_fail(uid, ip, "mfa")
        return _mfa_page(request, uid, 401, t("Der Code stimmt nicht."))
    auth.clear_fails(uid, "mfa")
    return _start_session(request, auth.get_user(uid))


def _xhr_ok(request):
    """Passkey-Endpunkte der Anmeldeseite: nur mit Kopfzeile X-Saga (fremde Seiten koennen sie nicht ohne Preflight setzen)."""
    return request.headers.get("x-saga") == "1"


@router.post("/login/passkey/options")
async def login_passkey_options(request: Request):
    """{mode: 'login' (ohne Passwort) | '2fa' (nach dem Passwort)} -> Optionen fuer navigator.credentials.get"""
    if not _xhr_ok(request):
        return _bad(t("ungültige Anfrage"), 400)
    b = await _body(request) or {}
    p = mfa.rp_problem()
    if p:
        return _bad(p, 409)
    if b.get("mode") == "2fa":
        uid = mfa.pending(request)
        if not uid:
            return _bad(t("Die Anmeldung hat zu lange gedauert – bitte noch einmal."), 401)
        kind = "2fa"
    else:
        if not _auth_opts()["pw_login"]:
            return _bad(t("Anmeldung mit Passwort ist abgeschaltet."), 403)
        uid, kind = None, "login"
    try:
        opts = await run_in_threadpool(mfa.auth_options, request, uid, kind)
    except mfa.MfaError as ex:
        return _bad(str(ex), 409)
    return JSONResponse({"options": opts, "origin": mfa.ORIGIN}, headers={"Cache-Control": "no-store"})


@router.post("/login/passkey")
async def login_passkey(request: Request):
    """Passkey-Antwort -> Sitzung. Im Zwischenschritt als zweiter Faktor, sonst Anmeldung ohne Passwort."""
    if not _xhr_ok(request):
        return _bad(t("ungültige Anfrage"), 400)
    b = await _body(request) or {}
    ip = _ip(request)
    uid = mfa.pending(request)
    if uid:
        if auth.too_many(uid, ip, "mfa"):
            request.session.clear()
            return _bad(t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."), 429)
        try:
            await run_in_threadpool(mfa.auth_verify, request, b.get("credential"), uid, "2fa")
        except mfa.MfaError as ex:
            auth.note_fail(uid, ip, "mfa")
            return JSONResponse({"error": str(ex), "unknown": ex.unknown}, status_code=401)
        auth.clear_fails(uid, "mfa")
        user = auth.get_user(uid)
    else:
        if mfa.ip_blocked(ip):
            return _bad(t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."), 429)
        try:
            uid = await run_in_threadpool(mfa.auth_verify, request, b.get("credential"), None, "login")
        except mfa.MfaError as ex:
            mfa.note_ip_fail(ip)
            return JSONResponse({"error": str(ex), "unknown": ex.unknown}, status_code=401)
        user = auth.get_user(uid)
        if not mfa.passkey_login_allowed(user) or await run_in_threadpool(user_info, uid, True) is None:
            mfa.note_ip_fail(ip)
            return _bad(t("Kein Zugang mit diesem Konto."), 403)
        auth.touch_login(uid)
        print(f"login: {uid} mit Passkey (ohne Passwort)", flush=True)
    resp = _start_session(request, user)
    return JSONResponse({"ok": True, "next": resp.headers.get("location") or "/"}, headers={"Cache-Control": "no-store"})


@router.get("/login/sso")
async def login_sso(request: Request):
    cl = await run_in_threadpool(auth.oidc_client)
    if cl is None:
        return RedirectResponse("/login", status_code=303)
    request.session.setdefault("next", "/")
    kw = {}
    if request.query_params.get("reauth"):            # erneute Anmeldung (z. B. Chats neu beginnen): Anbieter soll neu fragen
        request.session["next"] = _next(request.query_params.get("next", "/"))
        kw["prompt"] = "login"
    pol = mfa.policy()
    if pol.get("oidc_mfa") and mfa.oidc_acr_values(pol):
        kw["acr_values"] = mfa.oidc_acr_values(pol)      # Anbieter um eine Anmeldung mit diesem Niveau bitten
    try:
        return await cl.authorize_redirect(request, BASE_URL + "/auth/callback", **kw)
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
    pol = mfa.policy()
    if pol.get("oidc_mfa") and not mfa.oidc_mfa_ok(claims, pol):
        print(f"sso: Anmeldung ohne zweiten Faktor abgewiesen (amr={claims.get('amr')!r}, acr={claims.get('acr')!r})", flush=True)
        return _auth_page(request, "login.html", 403, error=t("Für Saga ist eine Anmeldung mit zweitem Faktor nötig. Dein Anmeldedienst hat keinen bestätigt – bitte dort einen zweiten Faktor einrichten oder an den Admin wenden."))
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


# Abmelden leert den HTTP-Cache dieses Browsers (u. a. Chat-Anhaenge, die als "immutable" zwischengespeichert werden -
# nur Ciphertext, aber nach dem Abmelden soll nichts liegen bleiben). Speicher (IndexedDB mit den Chat-Schluesseln)
# bleibt - die leert static/settings.js nur fuer den Bild-Cache. Browser beachten den Kopf nur ueber https.
_BYE = {"Clear-Site-Data": '"cache"'}


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
                return RedirectResponse(f"{end}?{urlencode(q)}", status_code=303, headers=_BYE)
    nxt = _next(request.query_params.get("next"))           # z. B. /security: "als anderes Konto anmelden"
    return RedirectResponse("/login?bye=1" + ("&next=" + nxt if nxt != "/" else ""), status_code=303, headers=_BYE)


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
                         "avatar": avatars.my_url(uid),
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
    name, email = clean_line(b.get("name") if isinstance(b.get("name"), str) else "").strip()[:120], str(b.get("email") or "").strip()[:200]
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


@router.get("/lang")
def lang_switch(request: Request):
    """Sprachumschalter unten auf der Anmeldeseite (auch 2FA, Registrierung, App-Anmeldung, oeffentliche Fehlerseiten)."""
    return lang_switch_response(request, _session_user(request), BASE_URL.startswith("https://"))


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
