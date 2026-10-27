"""API-Tokens mit Bereichen (2.5, Paket 1 E5; Konzept P1-E5/E6): Anmeldung fuer /api/v1, Webhooks und den MCP-Server.

- Format "sga_" + 43 Zeichen (32 Byte), gespeichert nur als SHA-256 (core._tok_hash); der Klartext erscheint genau einmal
  in der Antwort beim Anlegen. Getrennt von den App-Schluesseln (app_token, "hk_"), die nie Bereiche bekommen.
- Bereiche (SCOPES): read:/write: fuer pages, db, tasks, files, comments; dazu webhooks und mcp. write:x schliesst read:x ein.
- roots (optional, P1-E6): nur diese Seitenbaeume (Seite + alles darunter). Rechte = Schnittmenge aus Bereich, roots und
  der normalen Rolle des Kontos (sharing.Access) - ein Token kann nie mehr als das Konto.
- Ablauf Pflicht (Standard 90 Tage, Admin-Hoechstwert; "unbegrenzt" nur mit Admin-Freigabe). Ungueltig bei Widerruf,
  Ablauf, gesperrtem/geloeschtem Konto, Admin "ueberall abmelden"/Passwortwechsel mit Abmelden (userdata.revoke_user_logins)
  und wenn der Admin die API abschaltet bzw. das Konto nicht (mehr) in der freigegebenen Gruppe ist.
- Anlegen nur in der Web-Sitzung mit frischer Bestaetigung (mfa.confirmed; SSO-Konten: Anmeldung hoechstens 5 min alt).
- Ratenbegrenzung im Speicher (Saga laeuft als EIN Prozess): je Token RATE Anfragen und RATE_WRITE Schreibzugriffe je
  Minute, je Konto das Dreifache (Summe aller Tokens) -> 429 + Retry-After.

Web (Sitzung + X-Saga): GET/POST /api/account/api-tokens, DELETE /api/account/api-tokens/{id}.
Fuer andere Module: authed(request, write=False) -> (Tok, fehlerantwort), Tok.has(scope), node_role(c, tok, nid, minimum)."""
import json
import re
import secrets
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
from db import clean_line, connect as _db
from saga import mfa
from saga.core import _api_guard, _bad, _body, _json_load, _tok_hash, mark_authed, t, user_info
from saga.notes import note_chain
from saga.sharing import RANK, Access

router = APIRouter()

PREFIX = "sga_"
SCOPES = ("read:pages", "write:pages", "read:db", "write:db", "read:tasks", "write:tasks", "read:files", "write:files",
          "read:comments", "write:comments", "webhooks", "mcp")
ROOTS_MAX = 20
NAME_MAX = 60
DEFAULT_DAYS = 90
TOKEN_RE = re.compile(r"sga_[A-Za-z0-9_-]{40,60}")


def conf():
    """Admin-Einstellungen der API (auth.DEFAULTS['api'])."""
    c = dict(auth.DEFAULTS.get("api") or {})
    c.update(auth.settings().get("api") or {})
    return c


def allowed_for(info, cf=None):
    """Darf dieses Konto die API nutzen? (API an, ggf. Mitglied der freigegebenen Gruppe)"""
    cf = cf or conf()
    if not cf.get("enabled") or not info:
        return False
    g = str(cf.get("group") or "").strip().lower()
    return not g or g in {x.lower() for x in info.get("groups") or ()}


def roots_load(raw):
    """Gespeicherte roots (JSON-Liste von Seiten-IDs) -> Liste oder None (= alles, was das Konto sehen darf)."""
    v = _json_load(raw, [])
    return [int(x) for x in v if isinstance(x, int) and not isinstance(x, bool)] or None


def norm_scopes(raw):
    """Liste -> sortierte, gueltige Bereiche (write:x bringt read:x mit). ValueError bei Unbekanntem."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(t("Mindestens einen Bereich wählen."))
    out = set()
    for s in raw:
        if s not in SCOPES:
            raise ValueError(t("Unbekannter Bereich: {s}", s=str(s)[:40]))
        out.add(s)
        if s.startswith("write:"):
            out.add("read:" + s[6:])
    return [s for s in SCOPES if s in out]


class Tok:
    """Ein gueltiger Token einer Anfrage."""

    def __init__(self, tid, uid, name, prefix, scopes, roots, expires):
        self.id, self.uid, self.name, self.prefix = tid, uid, name, prefix
        self.scopes = frozenset(scopes)
        self.roots = [int(x) for x in roots] if roots else None
        self.expires = expires

    def has(self, scope):
        return scope in self.scopes

    def any(self, *scopes):
        return any(s in self.scopes for s in scopes)


# --- Ratenbegrenzung (gleitendes Fenster je Minute, im Speicher) ---------------------------------------------------
_hits = {}
_lock = threading.Lock()


def _take(key, limit, now):
    """-> 0 (erlaubt, gezaehlt) oder Sekunden bis zum naechsten freien Platz."""
    h = [x for x in _hits.get(key, ()) if now - x < 60]
    if len(h) >= limit:
        _hits[key] = h
        return max(1, int(60 - (now - h[0])) + 1)
    h.append(now)
    _hits[key] = h
    return 0


def rate_check(tok, write):
    cf = conf()
    rate, wrate = max(1, int(cf.get("rate") or 120)), max(1, int(cf.get("rate_write") or 30))
    now = time.time()
    with _lock:
        if len(_hits) > 20000:
            _hits.clear()
        checks = [(("t", tok.id), rate), (("u", tok.uid), rate * 3)]
        if write:
            checks += [(("tw", tok.id), wrate), (("uw", tok.uid), wrate * 3)]
        # erst pruefen, dann zaehlen: eine abgelehnte Anfrage verbraucht nichts
        for key, lim in checks:
            h = [x for x in _hits.get(key, ()) if now - x < 60]
            if len(h) >= lim:
                return max(1, int(60 - (now - h[0])) + 1)
        for key, lim in checks:
            _take(key, lim, now)
    return 0


def _err(code, msg, status, headers=None):
    return JSONResponse({"error": msg, "code": code}, status_code=status, headers=headers)


def _unauth(msg=None):
    return _err("unauthorized", msg or t("API-Token fehlt oder ist ungültig."), 401, {"WWW-Authenticate": 'Bearer realm="saga"'})


def lookup(raw):
    """Klartext -> Tok oder (None, code). Prueft Widerruf, Ablauf, Konto und Admin-Schalter."""
    raw = (raw or "").strip()
    if not TOKEN_RE.fullmatch(raw):
        return None, "unauthorized"
    with _db() as c, c:
        r = c.execute("SELECT id, uid, name, prefix, scopes, roots, created, expires, revoked, last_used FROM api_token WHERE hash=?",
                      (_tok_hash(raw),)).fetchone()
        if not r or r[8] is not None:
            return None, "unauthorized"
        now = time.time()
        if r[7] is not None and r[7] <= now:
            return None, "expired"
        if not r[9] or now - r[9] > 60:                       # "zuletzt benutzt" grob mitfuehren
            c.execute("UPDATE api_token SET last_used=? WHERE id=?", (now, r[0]))
    info = user_info(r[1])
    if info is None:
        return None, "unauthorized"
    if not allowed_for(info):
        return None, "disabled"
    return Tok(r[0], r[1], r[2], r[3], _json_load(r[4], []), roots_load(r[5]), r[7]), None


def authed(request, write=False, scope=None):
    """Bearer-Token der Anfrage -> (Tok, None) oder (None, Fehlerantwort 401/403/429). scope: noetiger Bereich."""
    hdr = request.headers.get("authorization") or ""
    if not hdr.lower().startswith("bearer ") or len(hdr) > 200:
        return None, _unauth()
    tok, code = lookup(hdr[7:])
    if tok is None:
        if code == "expired":
            return None, _err("expired", t("Der API-Token ist abgelaufen."), 401, {"WWW-Authenticate": 'Bearer error="invalid_token"'})
        if code == "disabled":
            return None, _err("api_disabled", t("Die API ist auf diesem Server für dein Konto nicht freigegeben."), 403)
        return None, _unauth()
    if scope and not tok.has(scope):
        return None, _err("insufficient_scope", t("Dem Token fehlt der Bereich „{s}“.", s=scope), 403,
                          {"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'})
    wait = rate_check(tok, write)
    if wait:
        return None, _err("rate_limited", t("Zu viele Anfragen – bitte kurz warten."), 429, {"Retry-After": str(wait)})
    mark_authed(request)
    return tok, None


# --- Rechte je Seite ------------------------------------------------------------------------------------------------
def in_roots(c, tok, owner, nid):
    if not tok.roots:
        return True
    chain = note_chain(c, owner, nid)
    return any(r in chain for r in tok.roots)


def node_role(c, tok, nid, minimum="view", acc=None):
    """-> (eigentuemer, rolle, None) oder (None, None, Fehlerantwort). 404 ohne Zugriff bzw. ausserhalb der roots."""
    acc = acc or Access(c, tok.uid)
    try:
        nid = int(nid)
    except (TypeError, ValueError, OverflowError):
        return None, None, _err("not_found", "not found", 404)
    if not 0 < nid < 2 ** 63:
        return None, None, _err("not_found", "not found", 404)
    r = acc.role(nid)
    owner = acc.owner(nid) if r else None
    if not r or not in_roots(c, tok, owner, nid):
        return None, None, _err("not_found", "not found", 404)
    if RANK[r] < RANK[minimum]:
        return owner, r, _err("forbidden", t("Dafür fehlt dir die Berechtigung."), 403)
    return owner, r, None


def visible_ids_filter(c, tok, owner, ids):
    """Teilmenge von ids (Seiten von owner) innerhalb der roots."""
    if not tok.roots:
        return set(ids)
    return {i for i in ids if in_roots(c, tok, owner, i)}


# --- Konto-Einstellungen (Web) -----------------------------------------------------------------------------------
def _confirmed(request, uid):
    """Frische Bestaetigung: lokale/LDAP-Konten ueber /api/account/security/confirm, SSO-Konten ueber eine frische Anmeldung."""
    info = user_info(uid)
    if mfa.supported(info):
        return mfa.confirmed(request)
    return mfa.fresh(request)


def _need_confirm(uid):
    info = user_info(uid)
    return JSONResponse({"error": t("Zur Sicherheit bitte zuerst bestätigen."), "reauth": True, "sso": not mfa.supported(info)},
                        status_code=403, headers={"Cache-Control": "no-store"})


def _titles(c, uid, ids):
    acc = Access(c, uid)
    out = []
    for i in ids or []:
        r = acc.role(i)
        o = acc.owner(i) if r else None
        row = c.execute("SELECT title FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (i, o)).fetchone() if o else None
        out.append({"id": i, "title": (row[0] or t("Unbenannt")) if row else t("Seite ohne Zugriff"), "ok": bool(row)})
    return out


def list_tokens(uid):
    with _db() as c:
        rows = c.execute("SELECT id, name, prefix, scopes, roots, created, expires, last_used, revoked FROM api_token WHERE uid=? "
                         "ORDER BY id", (uid,)).fetchall()
        now = time.time()
        out = [{"id": r[0], "name": r[1], "prefix": r[2], "scopes": _json_load(r[3], []),
                "roots": _titles(c, uid, roots_load(r[4])) if r[4] else None, "created": r[5], "expires": r[6],
                "last_used": r[7], "revoked": r[8], "expired": r[6] is not None and r[6] <= now} for r in rows]
    return out


@router.get("/api/account/api-tokens")
def api_tokens(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    cf = conf()
    info = user_info(uid)
    return JSONResponse({"enabled": allowed_for(info, cf), "tokens": list_tokens(uid), "scopes": list(SCOPES),
                         "max": int(cf.get("max_tokens") or 10), "max_days": int(cf.get("max_days") or 0),
                         "unlimited": bool(cf.get("unlimited")), "default_days": DEFAULT_DAYS,
                         "webhooks": bool(cf.get("webhooks")), "mcp": bool(cf.get("mcp")),
                         "sso": not mfa.supported(info)}, headers={"Cache-Control": "no-store"})


def create_token(uid, b):
    """-> (antwort-dict, None) oder (None, fehlerantwort). Prueft Name, Bereiche, roots, Laufzeit und Hoechstzahl."""
    cf = conf()
    if not allowed_for(user_info(uid), cf):
        return None, _bad(t("Die API ist auf diesem Server für dein Konto nicht freigegeben."), 403)
    name = re.sub(r"\s+", " ", clean_line(str(b.get("name") or ""))).strip()[:NAME_MAX]
    if not name:
        return None, _bad(t("Bitte einen Namen angeben."))
    try:
        scopes = norm_scopes(b.get("scopes"))
    except ValueError as ex:
        return None, _bad(str(ex))
    if "webhooks" in scopes and not cf.get("webhooks"):
        return None, _bad(t("Webhooks sind auf diesem Server abgeschaltet."))
    if "mcp" in scopes and not cf.get("mcp"):
        return None, _bad(t("Der MCP-Server ist auf diesem Server abgeschaltet."))
    days = b.get("days", DEFAULT_DAYS)
    max_days = int(cf.get("max_days") or 0)
    if days in (0, None, "never"):
        if not cf.get("unlimited"):
            return None, _bad(t("Unbegrenzte Tokens sind auf diesem Server nicht erlaubt."))
        expires = None
    else:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return None, _bad(t("ungültige Angabe"))
        if days < 1 or (max_days and days > max_days) or days > 3650:
            return None, _bad(t("Laufzeit: 1 bis {n} Tage.", n=max_days or 3650))
        expires = time.time() + days * 86400
    roots = b.get("roots")
    with _db() as c, c:
        if roots not in (None, []):
            if not isinstance(roots, list) or len(roots) > ROOTS_MAX:
                return None, _bad(t("Höchstens {n} Seitenbäume.", n=ROOTS_MAX))
            acc, ok = Access(c, uid), []
            for x in roots:
                try:
                    i = int(x)
                except (TypeError, ValueError, OverflowError):
                    return None, _bad(t("ungültige Angabe"))
                if not 0 < i < 2 ** 63 or not acc.role(i):
                    return None, _bad(t("Seite {id} nicht gefunden.", id=i), 404)
                ok.append(i)
            roots = list(dict.fromkeys(ok))
        else:
            roots = None
        n = c.execute("SELECT COUNT(*) FROM api_token WHERE uid=? AND revoked IS NULL AND (expires IS NULL OR expires>?)",
                      (uid, time.time())).fetchone()[0]
        mx = int(cf.get("max_tokens") or 10)
        if n >= mx:
            return None, _bad(t("Höchstens {n} Tokens je Konto.", n=mx), 409)
        tok = PREFIX + secrets.token_urlsafe(32)
        c.execute("DELETE FROM api_token WHERE uid=? AND (revoked IS NOT NULL OR expires<=?) AND COALESCE(revoked, expires) < ?",
                  (uid, time.time(), time.time() - 30 * 86400))               # alte widerrufene/abgelaufene aufraeumen
        cur = c.execute("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, name, _tok_hash(tok), tok[:12], json.dumps(scopes), json.dumps(roots) if roots else None, time.time(), expires))
    print(f"api: Token {cur.lastrowid} angelegt (Bereiche {','.join(scopes)})", flush=True)
    return {"id": cur.lastrowid, "name": name, "token": tok, "prefix": tok[:12], "scopes": scopes, "roots": roots, "expires": expires}, None


@router.post("/api/account/api-tokens")
async def api_token_add(request: Request):
    """Neuen Token anlegen (frische Bestaetigung noetig) - der Klartext steht NUR in dieser Antwort."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    if not _confirmed(request, uid):
        return _need_confirm(uid)
    out, err = await run_in_threadpool(create_token, uid, b)
    return err if err is not None else JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.delete("/api/account/api-tokens/{tid}")
def api_token_delete(tid: int, request: Request):
    """Widerrufen (sofort ungueltig). Webhooks, die ueber diesen Token angelegt wurden, werden abgeschaltet."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not 0 < tid < 2 ** 63:
        return _bad("not found", 404)
    return JSONResponse({"ok": True}) if revoke(uid, tid) else _bad("not found", 404)


def revoke(uid, tid=None):
    """Einen (tid) bzw. alle Tokens eines Kontos widerrufen -> Anzahl."""
    now = time.time()
    with _db() as c, c:
        if tid is None:
            n = c.execute("UPDATE api_token SET revoked=? WHERE uid=? AND revoked IS NULL", (now, uid)).rowcount
            c.execute("UPDATE webhook SET active=0, last_error=? WHERE uid=? AND token_id IS NOT NULL AND active=1",
                      ("token revoked", uid))
        else:
            n = c.execute("UPDATE api_token SET revoked=? WHERE id=? AND uid=? AND revoked IS NULL", (now, tid, uid)).rowcount
            if n:
                c.execute("UPDATE webhook SET active=0, last_error=? WHERE uid=? AND token_id=? AND active=1", ("token revoked", uid, tid))
    return n


def counts(c, uid):
    """Fuer den Admin-Bereich: aktive Tokens und Webhooks eines Kontos."""
    now = time.time()
    return {"api_tokens": c.execute("SELECT COUNT(*) FROM api_token WHERE uid=? AND revoked IS NULL AND (expires IS NULL OR expires>?)",
                                    (uid, now)).fetchone()[0],
            "webhooks": c.execute("SELECT COUNT(*) FROM webhook WHERE uid=? AND active=1", (uid,)).fetchone()[0]}
