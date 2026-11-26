"""MCP mit OAuth-Anmeldung (2.6, Bugs-DB 2264; Konzept K5, Architektur §1/§3, Weiche B.1 #14, RT-42).

MCP-Clients wie der claude.ai-Connector verbinden sich mit Saga, ohne einen API-Token zu kopieren - nach der
MCP-Autorisierung (OAuth 2.1). Alles unter der normalen Saga-Adresse (BASE_URL, eine Domain, nur 443):

- Metadaten: GET /.well-known/oauth-protected-resource[/mcp] (RFC 9728, resource = BASE_URL/mcp) und
  GET /.well-known/oauth-authorization-server (RFC 8414, issuer = BASE_URL). 401 an /mcp nennt sie in
  WWW-Authenticate: Bearer resource_metadata="..." (www_authenticate()).
- POST /oauth/register: dynamische Client-Registrierung (RFC 7591) mit Grenzen - je IP REG_PER_IP je Stunde, insgesamt
  REG_PER_HOUR, hoechstens CLIENTS_MAX Clients (davon UNUSED_MAX ohne Zustimmung), Name hoechstens NAME_MAX Zeichen,
  1-5 redirect_uris (https; http nur fuer localhost/127.0.0.1/[::1]; kein Fragment, keine Zugangsdaten). Clients ohne
  Zustimmung verfallen nach UNUSED_TTL.
- GET /oauth/authorize: prueft Client, exakte redirect_uri, response_type=code, PKCE S256 (Pflicht), resource (RFC 8707,
  nur BASE_URL/mcp), scope (nie tel - Weiche B.1 #14), merkt die Anfrage (im Speicher, PENDING_TTL) und leitet auf
  /oauth/authorize?r=<id> um - ohne Sitzung ueber die Anmeldung (/login?next=...). Die Zustimmungsseite nennt Client,
  Rechte, Rueckkehr-Adresse und Server; POST /oauth/authorize (CSRF-Token der Sitzung, Origin/Sec-Fetch-Site, CSP
  frame-ancestors 'none') -> Code (einmalig, CODE_TTL) an die redirect_uri mit state und iss (RFC 9207).
- POST /oauth/token: authorization_code (Verifier, gleiche redirect_uri und client_id; Code doppelt -> Kette gesperrt)
  und refresh_token (Rotation: jeder Refresh-Token gilt genau einmal; ein zweites Mal sperrt die ganze Verbindung).
- POST /oauth/revoke (RFC 7009): Refresh-Token -> Verbindung getrennt, Zugriffstoken -> nur dieser.
- Zugriffstoken (sgo_, ACCESS_TTL) sind apitok-Tokens (Tabelle api_token, Spalte grant_id) mit Bereichen: gleiche
  Rechtepruefung und Ratenbegrenzung wie sga_, aber nur an /mcp gueltig (apitok.authed(oauth=True)) - also nie /api/v1,
  Webhooks oder /api/ai/jobs/* (RT-42); Werkzeuge nur aus tools.for_mcp() (keine Chat-Werkzeuge). Alle Token und Codes
  nur als SHA-256 gespeichert.
- Web (Sitzung + X-Saga): GET /api/account/oauth-apps, DELETE /api/account/oauth-apps/{id} ("Verbundene Apps").
- Admin: Schalter api.oauth (auth.DEFAULTS); aus = Metadaten 404, keine Registrierung/Zustimmung, alle Zugriffstoken
  sofort ungueltig, Refresh verweigert. Bestehende sga_-Tokens bleiben unveraendert."""
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from db import clean_line, connect as _db
from saga import apitok, mfa
from saga.core import ASSET_V, BASE_URL, SEC_HEADERS, _api_guard, _bad, _json_load, _session_user, _tok_hash, t, title, tmpl, user_info

router = APIRouter()

BASE = BASE_URL.rstrip("/")
RESOURCE = BASE + "/mcp"
PRM_URL = BASE + "/.well-known/oauth-protected-resource/mcp"
# Bereiche fuer verbundene Apps: MCP + Datenbereiche. Nie tel* (Weiche B.1 #14: ein Connector darf nie waehlen), nie
# webhooks (kein MCP-Werkzeug). "mcp" bekommt jede Verbindung, die Datenbereiche waehlt die Person auf der Zustimmungsseite.
SCOPES = ("mcp", "read:pages", "write:pages", "read:db", "write:db", "read:tasks", "write:tasks", "read:files", "write:files",
          "read:comments", "write:comments", "read:calendar", "write:calendar")
assert not any(s.startswith("tel") for s in SCOPES) and set(SCOPES) <= set(apitok.SCOPES)
IGNORED_SCOPES = ("offline_access",)        # manche Clients fragen danach - Refresh-Token gibt es ohnehin
ACCESS_TTL = 3600
CODE_TTL = 120
REFRESH_TTL = 30 * 86400                   # ohne Benutzung verfaellt die Verbindung nach 30 Tagen
PENDING_TTL = 600
UNUSED_TTL = 86400
NAME_MAX = 80
URI_MAX = 500
REDIRECTS_MAX = 5
REG_PER_IP = int(os.environ.get("SAGA_OAUTH_REG_PER_IP") or 10)        # Testreihen brauchen mehr (tests/mcp_oauth.py)
REG_PER_HOUR = max(100, REG_PER_IP)
CLIENTS_MAX = 2000
UNUSED_MAX = 300
PENDING_MAX = 2000
VERIFIER_RE = re.compile(r"[A-Za-z0-9._~-]{43,128}")
CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
CLIENT_RE = re.compile(r"sgc_[A-Za-z0-9_-]{20,40}")
LOOPBACK = ("localhost", "127.0.0.1", "[::1]", "::1")

_lock = threading.Lock()
_pending = {}                               # r -> Anfrage (Saga laeuft als EIN Prozess)
_reg_hits = {}                              # Registrierungen je IP bzw. "*" (gleitende Stunde)
_last_gc = [0.0]


def enabled():
    cf = apitok.conf()
    return bool(cf.get("enabled") and cf.get("mcp") and cf.get("oauth"))


def www_authenticate(error=None, scope=None):
    """Kopf WWW-Authenticate fuer 401/403 an /mcp (MCP-Autorisierung: resource_metadata zeigt auf RFC 9728)."""
    parts = ['realm="saga-mcp"']
    if error:
        parts.append(f'error="{error}"')
    if scope:
        parts.append(f'scope="{scope}"')
    if enabled():
        parts.append(f'resource_metadata="{PRM_URL}"')
    return "Bearer " + ", ".join(parts)


def _j(obj, status=200, headers=None):
    h = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    h.update(headers or {})
    return JSONResponse(obj, status_code=status, headers=h)


def _oerr(code, desc, status=400, headers=None):
    return _j({"error": code, "error_description": desc}, status, headers)


# --- Metadaten ---------------------------------------------------------------------------------------------------
def _scopes_supported():
    return list(SCOPES)


def prm():
    return {"resource": RESOURCE, "authorization_servers": [BASE], "scopes_supported": _scopes_supported(),
            "bearer_methods_supported": ["header"], "resource_name": title()}


def asm():
    return {"issuer": BASE, "authorization_endpoint": BASE + "/oauth/authorize", "token_endpoint": BASE + "/oauth/token",
            "registration_endpoint": BASE + "/oauth/register", "revocation_endpoint": BASE + "/oauth/revoke",
            "scopes_supported": _scopes_supported(), "response_types_supported": ["code"],
            "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "authorization_response_iss_parameter_supported": True}


_META_H = {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def well_known_prm():
    if not enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(prm(), headers=_META_H)


@router.get("/.well-known/oauth-authorization-server")
def well_known_asm():
    if not enabled():
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(asm(), headers=_META_H)


# --- Aufraeumen ----------------------------------------------------------------------------------------------------
def gc(force=False):
    """Abgelaufene Codes und Refresh-Token, alte getrennte Verbindungen (30 Tage), unbenutzte Clients (UNUSED_TTL),
    abgelaufene Zugriffstoken verbundener Apps. Hoechstens alle 10 Minuten (ausser force)."""
    now = time.time()
    with _lock:
        if not force and now - _last_gc[0] < 600:
            return
        _last_gc[0] = now
        for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL]:
            _pending.pop(k, None)
    with _db() as c, c:
        c.execute("DELETE FROM oauth_code WHERE expires < ?", (now - 3600,))
        c.execute("DELETE FROM oauth_refresh WHERE expires < ?", (now,))
        c.execute("DELETE FROM api_token WHERE grant_id IS NOT NULL AND (expires < ? OR revoked < ?)", (now - 86400, now - 86400))
        c.execute("DELETE FROM oauth_grant WHERE (revoked IS NOT NULL AND revoked < ?) OR (expires IS NOT NULL AND expires < ?)",
                  (now - 30 * 86400, now - 30 * 86400))
        c.execute("DELETE FROM oauth_grant WHERE created < ? AND NOT EXISTS (SELECT 1 FROM oauth_refresh r WHERE r.grant_id = oauth_grant.id)",
                  (now - 86400,))                     # nie eingeloeste bzw. verfallene Verbindungen
        c.execute("DELETE FROM oauth_client WHERE created < ? AND NOT EXISTS (SELECT 1 FROM oauth_grant g WHERE g.client_id = oauth_client.id)",
                  (now - UNUSED_TTL,))


# --- Registrierung (RFC 7591) -------------------------------------------------------------------------------------
def _host_of(uri):
    try:
        return urlsplit(uri).hostname or ""
    except ValueError:
        return ""


def redirect_ok(uri):
    """https (ohne Fragment und Zugangsdaten) oder http nur fuer localhost/127.0.0.1/[::1] (RFC 8252)."""
    if not isinstance(uri, str) or not uri or len(uri) > URI_MAX or any(ch.isspace() or ord(ch) < 32 for ch in uri):
        return False
    try:
        u = urlsplit(uri)
        port = u.port                        # wirft bei kaputtem Port
    except ValueError:
        return False
    del port
    if u.fragment or "#" in uri or u.username is not None or u.password is not None or not u.hostname:
        return False
    if u.scheme == "https":
        return True
    if u.scheme == "http":
        h = u.hostname.lower()
        if h == "localhost":
            return True
        try:
            return ipaddress.ip_address(h).is_loopback
        except ValueError:
            return False
    return False


def _rate_reg(ip):
    now = time.time()
    with _lock:
        if len(_reg_hits) > 5000:
            _reg_hits.clear()
        for key, lim in ((ip, REG_PER_IP), ("*", REG_PER_HOUR)):
            h = [x for x in _reg_hits.get(key, ()) if now - x < 3600]
            _reg_hits[key] = h
            if len(h) >= lim:
                return max(1, int(3600 - (now - h[0])) + 1)
        for key in (ip, "*"):
            _reg_hits[key].append(now)
    return 0


def register_client(b):
    """-> (antwort, None) oder (None, (code, beschreibung, status))."""
    uris = b.get("redirect_uris")
    if not isinstance(uris, list) or not uris or len(uris) > REDIRECTS_MAX or not all(redirect_ok(u) for u in uris):
        return None, ("invalid_redirect_uri", f"redirect_uris: 1-{REDIRECTS_MAX} https URLs (http only for localhost)", 400)
    uris = list(dict.fromkeys(uris))
    method = b.get("token_endpoint_auth_method", "client_secret_basic")
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        return None, ("invalid_client_metadata", "token_endpoint_auth_method: none, client_secret_post or client_secret_basic", 400)
    gts = b.get("grant_types", ["authorization_code"])
    if not isinstance(gts, list) or "authorization_code" not in gts or any(g not in ("authorization_code", "refresh_token") for g in gts):
        return None, ("invalid_client_metadata", "grant_types: authorization_code, refresh_token", 400)
    gts = ["authorization_code", "refresh_token"]     # Refresh-Token gibt es immer (kurzlebige Zugriffstoken)
    rts = b.get("response_types", ["code"])
    if not isinstance(rts, list) or rts != ["code"]:
        return None, ("invalid_client_metadata", "response_types: code", 400)
    sc = b.get("scope")
    if sc is not None:
        if not isinstance(sc, str) or len(sc) > 1000:
            return None, ("invalid_client_metadata", "scope", 400)
        bad = [s for s in sc.split() if s not in SCOPES and s not in IGNORED_SCOPES]
        if bad:
            return None, ("invalid_client_metadata", "unsupported scope: " + bad[0][:40], 400)
    raw_name = b.get("client_name")
    if raw_name is not None and not isinstance(raw_name, str):
        return None, ("invalid_client_metadata", "client_name", 400)
    if raw_name and len(raw_name) > 200:
        return None, ("invalid_client_metadata", f"client_name: at most {NAME_MAX} characters", 400)
    name = re.sub(r"\s+", " ", clean_line(raw_name or "")).strip()[:NAME_MAX] or _host_of(uris[0])[:NAME_MAX] or "MCP"
    now = time.time()
    cid = "sgc_" + secrets.token_urlsafe(18)
    secret = None if method == "none" else "sgs_" + secrets.token_urlsafe(32)
    gc()
    with _db() as c, c:
        total = c.execute("SELECT COUNT(*) FROM oauth_client").fetchone()[0]
        unused = c.execute("SELECT COUNT(*) FROM oauth_client WHERE NOT EXISTS (SELECT 1 FROM oauth_grant g WHERE g.client_id = oauth_client.id)").fetchone()[0]
        if total >= CLIENTS_MAX or unused >= UNUSED_MAX:
            return None, ("temporarily_unavailable", "too many registered clients", 429)
        c.execute("INSERT INTO oauth_client (id, name, redirect_uris, auth_method, secret_hash, grant_types, created) VALUES (?,?,?,?,?,?,?)",
                  (cid, name, json.dumps(uris), method, _tok_hash(secret) if secret else None, json.dumps(gts), now))
    print(f"oauth: Client {cid[:10]}… registriert ({method})", flush=True)
    out = {"client_id": cid, "client_id_issued_at": int(now), "client_name": name, "redirect_uris": uris, "grant_types": gts,
           "response_types": ["code"], "token_endpoint_auth_method": method}
    if secret:
        out.update(client_secret=secret, client_secret_expires_at=0)
    return out, None


@router.post("/oauth/register")
async def oauth_register(request: Request):
    if not enabled():
        return _oerr("access_denied", "OAuth is disabled on this server", 403)
    from saga.web_auth import _ip
    wait = _rate_reg(_ip(request))
    if wait:
        return _oerr("slow_down", "too many registrations", 429, {"Retry-After": str(wait)})
    raw = await request.body()
    if len(raw) > 16384:
        return _oerr("invalid_client_metadata", "too large", 413)
    try:
        b = json.loads(raw or b"null")
    except ValueError:
        b = None
    if not isinstance(b, dict):
        return _oerr("invalid_client_metadata", "JSON object expected")
    out, err = await run_in_threadpool(register_client, b)
    if err:
        return _oerr(*err)
    return _j(out, 201)


def get_client(cid):
    if not isinstance(cid, str) or not CLIENT_RE.fullmatch(cid):
        return None
    with _db() as c:
        r = c.execute("SELECT id, name, redirect_uris, auth_method, secret_hash, grant_types FROM oauth_client WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    return {"id": r[0], "name": r[1], "redirect_uris": _json_load(r[2], []), "auth_method": r[3], "secret_hash": r[4],
            "grant_types": _json_load(r[5], ["authorization_code"])}


# --- Autorisierung -------------------------------------------------------------------------------------------------
def resource_ok(v):
    if v is None:
        return True                          # aeltere Clients ohne RFC 8707: an /mcp gebunden
    return isinstance(v, str) and v.rstrip("/").lower() == RESOURCE.lower()


def parse_scope(raw):
    """scope-Parameter -> (Liste, None) oder (None, Fehlertext). Leer = alles, was Saga anbietet (die Person waehlt)."""
    if raw is None or not raw.strip():
        return list(SCOPES), None
    if len(raw) > 1000:
        return None, "scope too long"
    want = []
    for s in raw.split():
        if s.lower().startswith("tel"):
            return None, "scope not available: " + s[:40]
        if s in IGNORED_SCOPES:
            continue
        if s not in SCOPES:
            return None, "unknown scope: " + s[:40]
        want.append(s)
    if not want:
        want = list(SCOPES)
    out = set(want) | {"mcp"}
    return [s for s in SCOPES if s in out], None


def _redirect(uri, **params):
    p = {k: v for k, v in params.items() if v is not None}
    sep = "&" if urlsplit(uri).query else "?"
    return RedirectResponse(uri + sep + urlencode(p), status_code=303, headers={"Cache-Control": "no-store"})


def _page(request, status=200, csp_form=None, **kw):
    from saga.web_auth import _csrf
    html = tmpl.get_template("oauth.html").render(title=title(), v=ASSET_V, csrf=_csrf(request), base=BASE, **kw)
    h = dict(SEC_HEADERS)
    if csp_form:                             # Rueckkehr nach dem Absenden: Chrome prueft form-action auch fuer die Umleitung
        h["Content-Security-Policy"] = h["Content-Security-Policy"].replace("form-action 'self'", f"form-action 'self' {csp_form}")
    return HTMLResponse(html, status_code=status, headers=h)


def _error_page(request, msg, back=None, status=400):
    return _page(request, status, mode="error", msg=msg, back=back)


def _origin(uri):
    u = urlsplit(uri)
    return f"{u.scheme}://{u.netloc}"


SCOPE_TXT = {"read:pages": "Seiten lesen", "write:pages": "Seiten schreiben", "read:db": "Datenbanken lesen",
             "write:db": "Datenbanken schreiben", "read:tasks": "Aufgaben lesen", "write:tasks": "Aufgaben schreiben",
             "read:files": "Dateien lesen", "write:files": "Dateien hochladen", "read:comments": "Kommentare lesen",
             "write:comments": "Kommentare schreiben", "read:calendar": "Kalender lesen", "write:calendar": "Kalender schreiben"}


def _scope_label(s):
    return t(SCOPE_TXT[s]) if s in SCOPE_TXT else s


@router.get("/oauth/authorize")
def oauth_authorize(request: Request):
    q = request.query_params
    rid = q.get("r")
    if rid is not None and len(q) == 1:
        return _consent(request, rid)
    if not enabled():
        return _error_page(request, t("Die Anmeldung per OAuth ist auf diesem Server abgeschaltet."), status=403)
    # doppelte Parameter sind ungueltig (RFC 6749 3.1)
    if len(q.multi_items()) != len(q):
        return _error_page(request, t("Diese Anfrage ist ungültig. Bitte starte die Verbindung in der App neu."))
    cl = get_client(q.get("client_id"))
    ru = q.get("redirect_uri")
    if cl is None or ru is None or ru not in cl["redirect_uris"]:
        # nie an eine ungepruefte Adresse zurueckleiten (offene Weiterleitung)
        return _error_page(request, t("Die App ist unbekannt oder ihre Rücksprung-Adresse passt nicht. Bitte starte die Verbindung in der App neu."))
    state = q.get("state")
    if state is not None and len(state) > 1000:
        return _redirect(ru, error="invalid_request", error_description="state too long", iss=BASE)

    def fail(code, desc):
        return _redirect(ru, error=code, error_description=desc, state=state, iss=BASE)
    if q.get("response_type") != "code":
        return fail("unsupported_response_type", "response_type must be code")
    ch, m = q.get("code_challenge"), q.get("code_challenge_method")
    if not ch or m != "S256" or not CHALLENGE_RE.fullmatch(ch):
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
    if not resource_ok(q.get("resource")):
        return fail("invalid_target", "resource must be " + RESOURCE)
    scopes, why = parse_scope(q.get("scope"))
    if why:
        return fail("invalid_scope", why)
    rid = secrets.token_urlsafe(18)
    with _lock:
        if len(_pending) >= PENDING_MAX:
            now = time.time()
            for k in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL] or list(_pending)[:100]:
                _pending.pop(k, None)
        _pending[rid] = {"at": time.time(), "client_id": cl["id"], "redirect_uri": ru, "challenge": ch, "scopes": scopes,
                         "state": state}
    nxt = "/oauth/authorize?r=" + rid
    if not _session_user(request):
        return RedirectResponse("/login?" + urlencode({"next": nxt}), status_code=303, headers={"Cache-Control": "no-store"})
    return RedirectResponse(nxt, status_code=303, headers={"Cache-Control": "no-store"})


def _get_pending(rid):
    with _lock:
        p = _pending.get(rid) if isinstance(rid, str) else None
        if p and time.time() - p["at"] > PENDING_TTL:
            _pending.pop(rid, None)
            p = None
    return p


def _consent(request, rid, err=None):
    p = _get_pending(rid)
    if p is None:
        return _error_page(request, t("Diese Anfrage ist abgelaufen oder ungültig. Bitte starte die Verbindung in der App neu."))
    uid = _session_user(request)
    if not uid:
        return RedirectResponse("/login?" + urlencode({"next": "/oauth/authorize?r=" + rid}), status_code=303)
    cl = get_client(p["client_id"])
    back = p["redirect_uri"]
    if cl is None or not enabled():
        return _error_page(request, t("Die Anmeldung per OAuth ist auf diesem Server abgeschaltet."), status=403)
    deny_url = back + ("&" if urlsplit(back).query else "?") + urlencode(
        {k: v for k, v in {"error": "access_denied", "state": p["state"], "iss": BASE}.items() if v is not None})
    info = user_info(uid)
    if not apitok.allowed_for(info):
        return _error_page(request, t("Die API ist auf diesem Server für dein Konto nicht freigegeben."), back=deny_url, status=403)
    if mfa.setup_needed(uid):
        return _error_page(request, t("Dein Admin verlangt einen zweiten Faktor – bitte richte zuerst im Browser unter „Anmeldung & Sicherheit“ einen Passkey oder eine Authenticator-App ein."),
                           back=deny_url, status=403)
    if not apitok._confirmed(request, uid):
        # wie beim Anlegen eines sga_-Tokens (apitok._confirmed): frische Bestaetigung (Passwort oder Code, hoechstens
        # mfa.FRESH_SEC alt); SSO-Konten: Anmeldung hoechstens so alt -> erst bestaetigen, dann die Rechte
        return _page(request, csp_form=_origin(back), mode="confirm", rid=rid, client=cl["name"], user=info.get("name") or uid,
                     uid=uid, sso=not mfa.supported(info), err=err)
    rights = [{"scope": s, "label": _scope_label(s)} for s in p["scopes"] if s != "mcp"]
    return _page(request, csp_form=_origin(back), mode="consent", rid=rid, client=cl["name"], back_host=_host_of(back),
                 rights=rights, user=info.get("name") or uid, uid=uid, err=err)


def _form(raw):
    try:
        return parse_qsl(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False, max_num_fields=60)
    except (UnicodeDecodeError, ValueError):
        return None


@router.post("/oauth/authorize")
async def oauth_authorize_post(request: Request):
    """Zustimmung bzw. Ablehnung (Formular der Zustimmungsseite)."""
    raw = await request.body()
    pairs = _form(raw) if len(raw) <= 16384 else None
    if pairs is None:
        return _error_page(request, t("Diese Anfrage ist ungültig. Bitte starte die Verbindung in der App neu."))
    f = {}
    for k, v in pairs:
        f.setdefault(k, []).append(v)
    one = {k: v[0] for k, v in f.items()}
    # Schutz gegen fremde Seiten: CSRF-Token der Sitzung, Origin/Sec-Fetch-Site (frame-ancestors 'none' gegen Einrahmen)
    o = request.headers.get("origin")
    sfs = request.headers.get("sec-fetch-site")
    # Origin "null": Chrome bei Referrer-Policy no-referrer (SEC_HEADERS) - dann entscheiden Sec-Fetch-Site und das CSRF-Token
    if (o and o != "null" and o.rstrip("/").lower() != BASE.lower()) or (sfs and sfs not in ("same-origin", "none")):
        return _error_page(request, t("Diese Anfrage ist ungültig. Bitte starte die Verbindung in der App neu."), status=403)
    uid = _session_user(request)
    from saga.web_auth import _csrf_ok
    if not uid or not _csrf_ok(request, one):
        return _error_page(request, t("Diese Anfrage ist abgelaufen oder ungültig. Bitte starte die Verbindung in der App neu."), status=403)
    rid = one.get("r") or ""
    p = _get_pending(rid)
    if p is None:
        return _error_page(request, t("Diese Anfrage ist abgelaufen oder ungültig. Bitte starte die Verbindung in der App neu."))
    if one.get("action") == "confirm":
        # dieselbe Bestaetigung wie POST /api/account/security/confirm (mfa.confirm, setzt sec_at) - Passwort oder Code
        v = (one.get("secret") or "").strip()
        b = {"code": v} if re.fullmatch(r"\d{6}", v) else {"password": one.get("secret") or ""}
        msg = await run_in_threadpool(mfa.confirm, request, uid, b) if mfa.supported(user_info(uid)) else t("Bitte bestätigen: Passwort, Code oder Passkey.")
        if msg:
            return _consent(request, rid, err=msg)
        request.session["sec_at"] = int(time.time())
        print("oauth: Zustimmung bestaetigt", flush=True)
        return RedirectResponse("/oauth/authorize?r=" + rid, status_code=303, headers={"Cache-Control": "no-store"})
    if one.get("action") != "allow":              # "deny" und alles Unbekannte: ablehnen (ohne Bestaetigung)
        with _lock:
            _pending.pop(rid, None)
        print("oauth: Zustimmung abgelehnt", flush=True)
        return _redirect(p["redirect_uri"], error="access_denied", error_description="the person declined", state=p["state"], iss=BASE)
    if not enabled() or not apitok.allowed_for(user_info(uid)) or mfa.setup_needed(uid) or not apitok._confirmed(request, uid):
        return _consent(request, rid)
    chosen = set(f.get("scope", [])) & set(p["scopes"])
    if not chosen - {"mcp"}:
        return _consent(request, rid, err=t("Mindestens einen Bereich wählen."))
    granted = apitok.norm_scopes(sorted(chosen | {"mcp"}))
    granted = [s for s in granted if s in SCOPES]
    with _lock:
        if _pending.pop(rid, None) is None:  # gleichzeitig zweimal abgeschickt
            return _error_page(request, t("Diese Anfrage ist abgelaufen oder ungültig. Bitte starte die Verbindung in der App neu."))
    code = await run_in_threadpool(_new_grant, uid, p, granted)
    print(f"oauth: Verbindung fuer Client {p['client_id'][:10]}… erlaubt ({','.join(granted)})", flush=True)
    return _redirect(p["redirect_uri"], code=code, state=p["state"], iss=BASE)


def _grant_days():
    md = int(apitok.conf().get("max_days") or 0)
    return md if md > 0 else 365


def _new_grant(uid, p, scopes):
    now = time.time()
    code = "sgk_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        # dieselbe App noch einmal verbunden: die alte Verbindung endet (eine Zeile je App unter "Verbundene Apps")
        old = [r[0] for r in c.execute("SELECT id FROM oauth_grant WHERE uid=? AND client_id=? AND revoked IS NULL",
                                       (uid, p["client_id"])).fetchall()]
        for g in old:
            _revoke_grant(c, g, "replaced", now)
        gid = c.execute("INSERT INTO oauth_grant (uid, client_id, scopes, resource, created, expires) VALUES (?,?,?,?,?,?)",
                        (uid, p["client_id"], json.dumps(scopes), RESOURCE, now, now + _grant_days() * 86400)).lastrowid
        c.execute("INSERT INTO oauth_code (hash, grant_id, client_id, redirect_uri, challenge, expires) VALUES (?,?,?,?,?,?)",
                  (_tok_hash(code), gid, p["client_id"], p["redirect_uri"], p["challenge"], now + CODE_TTL))
        c.execute("UPDATE oauth_client SET last_used=? WHERE id=?", (now, p["client_id"]))
    return code


def _revoke_grant(c, gid, why, now=None):
    now = now or time.time()
    n = c.execute("UPDATE oauth_grant SET revoked=?, why=? WHERE id=? AND revoked IS NULL", (now, why, gid)).rowcount
    c.execute("UPDATE api_token SET revoked=? WHERE grant_id=? AND revoked IS NULL", (now, gid))
    c.execute("UPDATE oauth_refresh SET used=COALESCE(used, ?) WHERE grant_id=?", (now, gid))
    return n


# --- Token ---------------------------------------------------------------------------------------------------------
def _client_auth(request, f):
    """-> (client, None) oder (None, Fehlerantwort). Basic, client_secret_post oder none (oeffentlicher Client)."""
    cid, secret = f.get("client_id"), f.get("client_secret")
    hdr = request.headers.get("authorization") or ""
    if hdr.lower().startswith("basic "):
        try:
            from urllib.parse import unquote
            a, _, b = base64.b64decode(hdr[6:].strip(), validate=True).decode("utf-8").partition(":")
            a, b = unquote(a), unquote(b)
        except (ValueError, UnicodeDecodeError):
            return None, _oerr("invalid_client", "bad client authentication", 401, {"WWW-Authenticate": 'Basic realm="saga"'})
        if cid and cid != a:
            return None, _oerr("invalid_request", "client_id mismatch")
        cid, secret = a, b
    cl = get_client(cid)
    if cl is None:
        return None, _oerr("invalid_client", "unknown client", 401)
    if cl["secret_hash"]:
        if not secret or not secrets.compare_digest(_tok_hash(secret), cl["secret_hash"]):
            return None, _oerr("invalid_client", "bad client authentication", 401)
    elif secret:
        return None, _oerr("invalid_client", "this client has no secret", 401)
    return cl, None


def _issue(c, gid, uid, name, scopes, now):
    acc = apitok.OAUTH_PREFIX + secrets.token_urlsafe(32)
    ref = "sgr_" + secrets.token_urlsafe(32)
    c.execute("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires, grant_id) VALUES (?,?,?,?,?,?,?,?,?)",
              (uid, name, _tok_hash(acc), acc[:12], json.dumps(scopes), None, now, now + ACCESS_TTL, gid))
    c.execute("INSERT INTO oauth_refresh (hash, grant_id, created, expires) VALUES (?,?,?,?)", (_tok_hash(ref), gid, now, now + REFRESH_TTL))
    c.execute("UPDATE oauth_grant SET last_used=? WHERE id=?", (now, gid))
    return {"access_token": acc, "token_type": "Bearer", "expires_in": ACCESS_TTL, "refresh_token": ref, "scope": " ".join(scopes)}


def _user_ok(uid):
    info = user_info(uid)
    return bool(info) and apitok.allowed_for(info)


def token_code(cl, f):
    code, ru, ver = f.get("code") or "", f.get("redirect_uri"), f.get("code_verifier") or ""
    now = time.time()
    with _db() as c, c:
        r = c.execute("SELECT grant_id, client_id, redirect_uri, challenge, expires, used FROM oauth_code WHERE hash=? FOR UPDATE",
                      (_tok_hash(code),)).fetchone() if code.startswith("sgk_") and len(code) < 100 else None
        if not r:
            return _oerr("invalid_grant", "unknown code")
        gid, cid, cru, ch, exp, used = r
        if used is not None:
            # Code ein zweites Mal: alles aus diesem Code sperren (RFC 6749 4.1.2, OAuth 2.1 4.1.3)
            _revoke_grant(c, gid, "code_reuse", now)
            print(f"oauth: Code doppelt eingeloest - Verbindung {gid} gesperrt", flush=True)
            return _oerr("invalid_grant", "code already used")
        c.execute("UPDATE oauth_code SET used=? WHERE hash=?", (now, _tok_hash(code)))
        g = c.execute("SELECT uid, scopes, revoked FROM oauth_grant WHERE id=? FOR UPDATE", (gid,)).fetchone()
        # Jeder Fehler ab hier verbraucht den Code und beendet die (noch leere) Verbindung
        why = None
        if cid != cl["id"]:
            why = ("invalid_grant", "code was issued to another client")
        elif not g or g[2] is not None:
            why = ("invalid_grant", "connection revoked")
        elif exp < now:
            why = ("invalid_grant", "code expired")
        elif ru != cru:
            why = ("invalid_grant", "redirect_uri does not match")
        elif not VERIFIER_RE.fullmatch(ver) or not secrets.compare_digest(
                base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode(), ch):
            why = ("invalid_grant", "PKCE verification failed")
        elif not resource_ok(f.get("resource")):
            why = ("invalid_target", "resource must be " + RESOURCE)
        elif not _user_ok(g[0]):
            why = ("invalid_grant", "account not allowed")
        if why:
            _revoke_grant(c, gid, "code_failed", now)
            return _oerr(*why)
        out = _issue(c, gid, g[0], cl["name"], _json_load(g[1], []), now)
    return _j(out)


def token_refresh(cl, f):
    ref = f.get("refresh_token") or ""
    now = time.time()
    with _db() as c, c:
        r = c.execute("SELECT grant_id, expires, used FROM oauth_refresh WHERE hash=? FOR UPDATE", (_tok_hash(ref),)).fetchone() \
            if ref.startswith("sgr_") and len(ref) < 100 else None
        if not r:
            return _oerr("invalid_grant", "unknown refresh token")
        gid, exp, used = r
        g = c.execute("SELECT uid, scopes, revoked, expires, client_id FROM oauth_grant WHERE id=? FOR UPDATE", (gid,)).fetchone()
        if not g or g[4] != cl["id"]:
            return _oerr("invalid_grant", "refresh token was issued to another client")
        if g[2] is not None:
            return _oerr("invalid_grant", "connection revoked")
        if used is not None:
            # Wiederverwendung: jemand hat einen alten Refresh-Token - ganze Kette sperren (OAuth 2.1 4.3.1)
            _revoke_grant(c, gid, "refresh_reuse", now)
            print(f"oauth: Refresh-Token wiederverwendet - Verbindung {gid} gesperrt", flush=True)
            return _oerr("invalid_grant", "refresh token already used - connection revoked")
        if exp < now or (g[3] is not None and g[3] < now):
            return _oerr("invalid_grant", "refresh token expired")
        if not _user_ok(g[0]):
            return _oerr("invalid_grant", "account not allowed")
        scopes = _json_load(g[1], [])
        want = f.get("scope")
        if want:
            w = want.split()
            if any(s not in scopes for s in w):
                return _oerr("invalid_scope", "scope exceeds the connection")
            scopes = apitok.norm_scopes(sorted(set(w) | {"mcp"}))
        c.execute("UPDATE oauth_refresh SET used=? WHERE hash=?", (now, _tok_hash(ref)))
        out = _issue(c, gid, g[0], cl["name"], scopes, now)
    return _j(out)


async def _read_form(request):
    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    raw = await request.body()
    if ct != "application/x-www-form-urlencoded" or len(raw) > 16384:
        return None
    pairs = _form(raw)
    if pairs is None:
        return None
    f = {}
    for k, v in pairs:
        if k in f:
            return None                      # doppelte Parameter (RFC 6749 3.2)
        f[k] = v
    return f


@router.post("/oauth/token")
async def oauth_token(request: Request):
    if not enabled():
        return _oerr("access_denied", "OAuth is disabled on this server", 403)
    f = await _read_form(request)
    if f is None:
        return _oerr("invalid_request", "application/x-www-form-urlencoded body without duplicate parameters expected")
    cl, err = await run_in_threadpool(_client_auth, request, f)
    if err:
        return err
    gt = f.get("grant_type")
    await run_in_threadpool(gc)
    if gt == "authorization_code":
        return await run_in_threadpool(token_code, cl, f)
    if gt == "refresh_token":
        return await run_in_threadpool(token_refresh, cl, f)
    return _oerr("unsupported_grant_type", "authorization_code or refresh_token")


def revoke_token(cl, tok):
    now = time.time()
    with _db() as c, c:
        if tok.startswith("sgr_"):
            r = c.execute("SELECT r.grant_id FROM oauth_refresh r JOIN oauth_grant g ON g.id = r.grant_id WHERE r.hash=? AND g.client_id=?",
                          (_tok_hash(tok), cl["id"])).fetchone()
            if r:
                _revoke_grant(c, r[0], "client", now)
        elif tok.startswith(apitok.OAUTH_PREFIX):
            c.execute("UPDATE api_token SET revoked=? WHERE hash=? AND revoked IS NULL AND grant_id IN "
                      "(SELECT id FROM oauth_grant WHERE client_id=?)", (now, _tok_hash(tok), cl["id"]))


@router.post("/oauth/revoke")
async def oauth_revoke(request: Request):
    f = await _read_form(request)
    if f is None or not f.get("token"):
        return _oerr("invalid_request", "token missing")
    cl, err = await run_in_threadpool(_client_auth, request, f)
    if err:
        return err
    tok = f["token"]
    if len(tok) < 100:
        await run_in_threadpool(revoke_token, cl, tok)
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


# --- Verbundene Apps (Einstellungen der Person) ----------------------------------------------------------------
def list_apps(uid):
    now = time.time()
    with _db() as c:
        rows = c.execute("SELECT g.id, c.name, g.scopes, g.created, g.last_used, c.redirect_uris FROM oauth_grant g "
                         "JOIN oauth_client c ON c.id = g.client_id WHERE g.uid=? AND g.revoked IS NULL AND (g.expires IS NULL OR g.expires>?) "
                         "AND EXISTS (SELECT 1 FROM oauth_refresh r WHERE r.grant_id = g.id AND r.used IS NULL) ORDER BY g.created DESC", (uid, now)).fetchall()
    return [{"id": r[0], "name": r[1], "scopes": [s for s in _json_load(r[2], []) if s != "mcp"], "created": r[3],
             "last_used": r[4], "host": _host_of((_json_load(r[5], []) or [""])[0])} for r in rows]


def revoke_app(uid, gid):
    with _db() as c, c:
        r = c.execute("SELECT id FROM oauth_grant WHERE id=? AND uid=? AND revoked IS NULL", (gid, uid)).fetchone()
        if not r:
            return 0
        return _revoke_grant(c, gid, "person")


def admin_counts():
    now = time.time()
    with _db() as c:
        apps = c.execute("SELECT COUNT(*) FROM oauth_grant g WHERE revoked IS NULL AND (expires IS NULL OR expires>?) AND "
                         "EXISTS (SELECT 1 FROM oauth_refresh r WHERE r.grant_id = g.id AND r.used IS NULL)", (now,)).fetchone()[0]
        clients = c.execute("SELECT COUNT(*) FROM oauth_client").fetchone()[0]
    return {"apps": apps, "clients": clients}


@router.get("/api/account/oauth-apps")
def api_oauth_apps(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse({"enabled": enabled() and apitok.allowed_for(user_info(uid)), "apps": list_apps(uid),
                         "mcp_url": RESOURCE}, headers={"Cache-Control": "no-store"})


@router.delete("/api/account/oauth-apps/{gid}")
def api_oauth_app_delete(gid: int, request: Request):
    """Verbindung trennen: Zugriffstoken sofort ungueltig, Refresh-Token verweigert."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not 0 < gid < 2 ** 63:
        return _bad("not found", 404)
    if not revoke_app(uid, gid):
        return _bad("not found", 404)
    print(f"oauth: Verbindung {gid} getrennt", flush=True)
    return JSONResponse({"ok": True})
