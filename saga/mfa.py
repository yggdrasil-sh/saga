"""Zweiter Faktor bei der Anmeldung (2.1): Passkeys (WebAuthn/FIDO2), Authenticator-App (TOTP, RFC 6238) und
Wiederherstellungscodes.

- Gilt fuer lokale und LDAP-Konten. SSO-Konten (OIDC) machen das beim Identity Provider (Admin-Schalter
  "bei SSO Mehr-Faktor verlangen" prueft amr/acr, siehe oidc_mfa_ok).
- Passkeys: Anmeldung ohne Passwort (auffindbare Passkeys, Benutzerpruefung am Geraet) UND zweiter Faktor nach dem
  Passwort. Pruefung mit py_webauthn (keine eigene Krypto). RP-ID und Origin kommen aus BASE_URL (PASSKEY_RP_ID kann
  die RP-ID auf eine uebergeordnete Domain setzen).
- TOTP: 6 Ziffern, 30 s, +-1 Schritt; ein angenommener Code gilt nur einmal (last_step). Geheimnis verschluesselt
  wie die Geheimnisse der Einstellungen (auth._enc).
- Wiederherstellungscodes: 10 Einmal-Codes, nur als Hash gespeichert, entstehen mit dem ersten Faktor.

Bausteine fuer web_auth (Anmeldung), chat (Neu beginnen), admin (Admin-Anmeldung, Zuruecksetzen), dashboard
(Einrichtung verlangt) und die Endpunkte der Einstellungen (Konto -> Anmeldung & Sicherheit).
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import struct
import time
from urllib.parse import quote, urlparse
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
import auth
from db import clean_line, connect as _db
from saga.core import ADMIN_URL, ASSET_V, BASE_URL, E, SEC_HEADERS, _api_guard, _bad, _body, _session_user, t, title, tmpl, user_info

router = APIRouter()

PASSKEY_MAX = 20                  # Passkeys je Konto
CHAL_TTL = 300                    # WebAuthn-Challenge gilt 5 Minuten (und nur einmal)
PENDING_TTL = 300                 # Zwischenschritt nach dem Passwort: 5 Minuten
FRESH_SEC = 300                   # Aenderungen an den Faktoren: Anmeldung/Bestaetigung hoechstens 5 Minuten alt
TOTP_STEP, TOTP_DIGITS, TOTP_WINDOW = 30, 6, 1
REC_N = 10
REC_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"      # ohne 0/o, 1/l/i (abschreibbar)


class MfaError(Exception):
    """Fehler mit uebersetzter Meldung fuer die Antwort. unknown=True: Passkey ist hier nicht (mehr) bekannt."""

    def __init__(self, msg, unknown=False, status=400):
        super().__init__(msg)
        self.unknown = unknown
        self.status = status


def b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- Relying Party (RP-ID und Origin aus BASE_URL) -----------------------------------------------------
def origin_of(url):
    """Origin wie ihn der Browser meldet: Schema://host[:port] (Standard-Port weggelassen, klein geschrieben)."""
    u = urlparse(url or "")
    host = (u.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = u.port
    if port and not ((u.scheme == "https" and port == 443) or (u.scheme == "http" and port == 80)):
        host += f":{port}"
    return f"{(u.scheme or '').lower()}://{host}"


ORIGIN = origin_of(BASE_URL)
_HOST = (urlparse(BASE_URL).hostname or "").lower()
RP_ID = (E.get("PASSKEY_RP_ID") or _HOST).strip().strip(".").lower()


def _is_ip(h):
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def rp_problem(origin=ORIGIN):
    """None, wenn Passkeys mit dieser Adresse gehen - sonst eine verstaendliche Meldung (BASE_URL falsch eingerichtet)."""
    u = urlparse(origin)
    host = (u.hostname or "").lower()
    if not RP_ID or _is_ip(RP_ID) or _is_ip(host):
        return t("Passkeys brauchen eine Adresse mit Rechnernamen (keine IP-Adresse) in BASE_URL, z. B. https://saga.example.org.")
    local = host == "localhost" or host.endswith(".localhost")
    if u.scheme != "https" and not local:
        return t("Passkeys gehen nur über https:// – BASE_URL beginnt mit {scheme}://.", scheme=u.scheme or "?")
    if host != RP_ID and not host.endswith("." + RP_ID):
        return t("PASSKEY_RP_ID ({rp}) passt nicht zur Adresse {host}.", rp=RP_ID, host=host)
    return None


def admin_origin():
    return origin_of(ADMIN_URL) if ADMIN_URL else ""


def _explain(ex, origin=ORIGIN):
    """Meldung der Bibliothek -> verstaendlicher Text. Falsche Adresse (BASE_URL) bekommt eine eigene Meldung."""
    msg = str(ex)
    m = re.search(r'origin "([^"]*)"', msg)
    if m:
        return t("Der Browser meldet die Adresse {got}, Saga ist aber für {base} eingerichtet (BASE_URL). Bitte Saga über {base} öffnen – oder BASE_URL anpassen.",
                 got=m.group(1) or "?", base=origin)
    if "RP ID" in msg:
        return t("Der Passkey gehört zu einer anderen Adresse (RP-ID {rp}). Wurde BASE_URL geändert?", rp=RP_ID)
    if "sign count" in msg:
        return t("Dieser Passkey wurde möglicherweise kopiert (Zähler passt nicht) – bitte anders anmelden und den Passkey neu anlegen.")
    if "User verification" in msg:
        return t("Für die Anmeldung ohne Passwort muss das Gerät dich prüfen (Fingerabdruck, Gesicht oder PIN).")
    if "challenge" in msg:
        return t("Die Anfrage ist abgelaufen – bitte noch einmal versuchen.")
    return t("Der Passkey konnte nicht geprüft werden.")


# --- Status und Richtlinie --------------------------------------------------------------------------------
def supported(u):
    """Saga-2FA fuer dieses Konto? (dict aus auth.get_user oder user_info) - nicht fuer SSO-Konten."""
    return bool(u) and u.get("source") in ("local", "ldap")


def factors(uid):
    with _db() as c:
        pk = c.execute("SELECT COUNT(*) FROM mfa_passkey WHERE uid=?", (uid,)).fetchone()[0]
        tp = c.execute("SELECT confirmed FROM mfa_totp WHERE uid=?", (uid,)).fetchone()
        rc = c.execute("SELECT COUNT(*) FROM mfa_recovery WHERE uid=? AND used_at IS NULL", (uid,)).fetchone()[0]
    return {"passkeys": pk, "totp": bool(tp and tp[0]), "recovery": rc}


def has_mfa(uid, f=None):
    f = f or factors(uid)
    return bool(f["passkeys"] or f["totp"])


def policy():
    p = auth.settings().get("mfa") or {}
    return p if p.get("require") in ("none", "admins", "all") else dict(p, require="none")


def required(info):
    """Verlangt die Richtlinie einen zweiten Faktor fuer dieses Konto? (SSO-Konten: nie - das macht der Anbieter)"""
    if not supported(info):
        return False
    r = policy().get("require")
    return r == "all" or (r == "admins" and bool(info.get("is_admin")))


def setup_needed(uid):
    """Verlangt die Richtlinie einen Faktor, den das Konto noch nicht hat? (neue App-Anmeldungen, Admin-Bereich)"""
    info = user_info(uid)
    return bool(info) and required(info) and not has_mfa(uid)


def oidc_mfa_ok(claims, pol=None):
    """SSO-Anmeldung mit Mehr-Faktor? amr (RFC 8176) mit 'mfa' oder einem zweiten Faktor, oder ein erlaubter acr-Wert."""
    pol = pol or policy()
    amr = claims.get("amr")
    amr = {str(x).lower() for x in (amr if isinstance(amr, list) else [amr] if amr else [])}
    if "mfa" in amr or amr & {"otp", "hwk", "swk", "sms", "fpt", "face", "iris", "retina", "vbm", "sc"}:
        return True
    acr = str(claims.get("acr") or "").strip()
    allowed = {a.strip() for a in str(pol.get("oidc_acr") or "").split(",") if a.strip()}
    return bool(acr and acr in allowed)


def oidc_acr_values(pol=None):
    pol = pol or policy()
    return " ".join(a.strip() for a in str(pol.get("oidc_acr") or "").split(",") if a.strip())


# --- Wiederherstellungscodes ---------------------------------------------------------------------------
def _rec_norm(code):
    return re.sub(r"[^a-z0-9]", "", str(code or "").lower())[:40]


def _rec_hash(uid, code):
    return hashlib.sha256(f"saga-recovery|{uid}|{_rec_norm(code)}".encode()).hexdigest()


def recovery_new(uid, c=None):
    """10 neue Codes (die alten gelten nicht mehr). Nur jetzt im Klartext."""
    codes = ["-".join("".join(secrets.choice(REC_ALPHABET) for _ in range(4)) for _ in range(4)) for _ in range(REC_N)]

    def write(c):
        c.execute("DELETE FROM mfa_recovery WHERE uid=?", (uid,))
        for code in codes:
            c.execute("INSERT INTO mfa_recovery (uid, hash, created) VALUES (?,?,?)", (uid, _rec_hash(uid, code), time.time()))
    if c is not None:
        write(c)
    else:
        with _db() as c2, c2:
            write(c2)
    return codes


def recovery_use(uid, code):
    n = _rec_norm(code)
    if len(n) != 16:
        return False
    with _db() as c, c:
        return c.execute("UPDATE mfa_recovery SET used_at=? WHERE uid=? AND hash=? AND used_at IS NULL",
                         (time.time(), uid, _rec_hash(uid, n))).rowcount == 1


# --- TOTP (RFC 6238) --------------------------------------------------------------------------------------
def totp_at(secret, counter, digits=TOTP_DIGITS):
    """Code fuer einen Zeitschritt (HMAC-SHA1, dynamische Kuerzung nach RFC 4226)."""
    h = hmac.new(secret, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)).zfill(digits)


def totp_match(secret, code, now=None, after=0):
    """Zeitschritt, zu dem der Code passt (+-1 Schritt, nur Schritte nach 'after') - sonst None."""
    code = re.sub(r"\s+", "", str(code or ""))
    if not re.fullmatch(r"[0-9]{%d}" % TOTP_DIGITS, code):
        return None
    cur = int((time.time() if now is None else now) // TOTP_STEP)
    for st in range(cur - TOTP_WINDOW, cur + TOTP_WINDOW + 1):
        if st > after and hmac.compare_digest(totp_at(secret, st), code):
            return st
    return None


def _totp_secret(row_secret):
    s = auth._dec(row_secret)
    return base64.b32decode(s) if s else b""


def totp_use(uid, code):
    """Code einer eingerichteten Authenticator-App pruefen und verbrauchen."""
    with _db() as c:
        r = c.execute("SELECT secret, last_step FROM mfa_totp WHERE uid=? AND confirmed IS NOT NULL", (uid,)).fetchone()
    if not r:
        return False
    sec = _totp_secret(r[0])
    st = totp_match(sec, code, after=r[1] or 0) if sec else None
    if st is None:
        return False
    with _db() as c, c:                        # gleichzeitige Anfragen: nur eine bekommt den Schritt
        return c.execute("UPDATE mfa_totp SET last_step=? WHERE uid=? AND last_step < ?", (st, uid, st)).rowcount == 1


def check_code(uid, code, recovery=True):
    """Code nach dem Passwort: 6 Ziffern = Authenticator-App, sonst Wiederherstellungscode. -> 'totp' | 'recovery' | None"""
    raw = re.sub(r"\s+", "", str(code or ""))
    if re.fullmatch(r"[0-9]{6}", raw):
        return "totp" if totp_use(uid, raw) else None
    if recovery and recovery_use(uid, raw):
        print(f"mfa: {uid} meldet sich mit einem Wiederherstellungscode an", flush=True)
        return "recovery"
    return None


# --- Passkeys (WebAuthn, py_webauthn) ---------------------------------------------------------------------
def _user_handle(uid):
    """Zufaellige, feste Kennung des Kontos fuer Passkeys (nicht der Benutzername)."""
    with _db() as c, c:
        r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='mfa_uh'", (uid,)).fetchone()
        if r and r[0]:
            return r[0]
        c.execute("INSERT OR IGNORE INTO user_kv (uid, k, v) VALUES (?, 'mfa_uh', ?)", (uid, b64e(secrets.token_bytes(16))))
        return c.execute("SELECT v FROM user_kv WHERE uid=? AND k='mfa_uh'", (uid,)).fetchone()[0]


def _chal_new(request, kind, uid, challenge, key="wa"):
    cid = secrets.token_urlsafe(18)
    with _db() as c, c:
        c.execute("DELETE FROM mfa_challenge WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO mfa_challenge (id, uid, kind, challenge, expires) VALUES (?,?,?,?,?)",
                  (cid, uid or "", kind, b64e(challenge), time.time() + CHAL_TTL))
    request.session[key] = cid


def _chal_take(request, kind, key="wa"):
    """Challenge dieser Sitzung holen und verbrauchen -> (uid, challenge) oder MfaError."""
    cid = request.session.pop(key, None)
    r = None
    if isinstance(cid, str) and len(cid) < 60:
        with _db() as c, c:
            r = c.execute("SELECT uid, kind, challenge, expires FROM mfa_challenge WHERE id=?", (cid,)).fetchone()
            c.execute("DELETE FROM mfa_challenge WHERE id=?", (cid,))
    if not r or r[1] != kind or r[3] < time.time():
        raise MfaError(t("Die Anfrage ist abgelaufen – bitte noch einmal versuchen."))
    return r[0], b64d(r[2])


def _opts_json(opts):
    from webauthn.helpers import options_to_json_dict
    return options_to_json_dict(opts)


def passkey_list(uid):
    with _db() as c:
        rows = c.execute("SELECT id, name, created, last_used, backed_up, transports FROM mfa_passkey WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return [{"id": r[0], "name": r[1], "created": r[2], "last_used": r[3], "synced": bool(r[4]),
             "transports": json.loads(r[5] or "[]")} for r in rows]


def reg_options(request, uid):
    from webauthn import generate_registration_options
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                          UserVerificationRequirement)
    info = user_info(uid) or {}
    with _db() as c:
        have = [r[0] for r in c.execute("SELECT cred_id FROM mfa_passkey WHERE uid=?", (uid,)).fetchall()]
    if len(have) >= PASSKEY_MAX:
        raise MfaError(t("Höchstens {n} Passkeys je Konto – bitte zuerst einen alten löschen.", n=PASSKEY_MAX))
    opts = generate_registration_options(
        rp_id=RP_ID, rp_name=title(), user_name=uid, user_id=b64d(_user_handle(uid)), user_display_name=info.get("name") or uid,
        timeout=CHAL_TTL * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=b64d(x)) for x in have])
    _chal_new(request, "reg", uid, opts.challenge)
    return _opts_json(opts)


def reg_verify(request, uid, cred, name=""):
    """Neuen Passkey pruefen und speichern -> id"""
    from webauthn import verify_registration_response
    cuid, chal = _chal_take(request, "reg")
    if cuid != uid or not isinstance(cred, dict):
        raise MfaError(t("Die Anfrage ist abgelaufen – bitte noch einmal versuchen."))
    try:
        v = verify_registration_response(credential=cred, expected_challenge=chal, expected_rp_id=RP_ID, expected_origin=ORIGIN)
    except Exception as ex:  # noqa: BLE001 - jede Pruef-Ausnahme der Bibliothek
        print(f"mfa: {uid} Passkey abgewiesen: {type(ex).__name__}: {ex}", flush=True)
        raise MfaError(_explain(ex))
    tr = (cred.get("response") or {}).get("transports")
    tr = [str(x)[:20] for x in tr[:8]] if isinstance(tr, list) else []
    name = re.sub(r"\s+", " ", clean_line(name if isinstance(name, str) else "")).strip()[:60] or t("Passkey")
    uh = _user_handle(uid)
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM mfa_passkey WHERE uid=?", (uid,)).fetchone()[0] >= PASSKEY_MAX:
            raise MfaError(t("Höchstens {n} Passkeys je Konto – bitte zuerst einen alten löschen.", n=PASSKEY_MAX))
        if c.execute("SELECT 1 FROM mfa_passkey WHERE cred_id=?", (b64e(v.credential_id),)).fetchone():
            raise MfaError(t("Diesen Passkey gibt es hier schon."))
        pid = c.execute("INSERT INTO mfa_passkey (uid, cred_id, public_key, sign_count, user_handle, transports, name, aaguid, backed_up, created) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (uid, b64e(v.credential_id), b64e(v.credential_public_key), v.sign_count, uh, json.dumps(tr),
                         name, str(v.aaguid or ""), 1 if v.credential_backed_up else 0, time.time())).lastrowid
    print(f"mfa: {uid} neuer Passkey ({pid})", flush=True)
    return pid


def auth_options(request, uid=None, kind="auth", key="wa"):
    """Optionen fuer navigator.credentials.get: uid=None -> auffindbare Passkeys (Anmeldung ohne Passwort)."""
    from webauthn import generate_authentication_options
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement
    allow = None
    if uid:
        with _db() as c:
            rows = c.execute("SELECT cred_id, transports FROM mfa_passkey WHERE uid=?", (uid,)).fetchall()
        if not rows:
            raise MfaError(t("Für dieses Konto ist kein Passkey eingerichtet."))
        allow = [PublicKeyCredentialDescriptor(id=b64d(r[0]), transports=_transports(r[1])) for r in rows]
    opts = generate_authentication_options(rp_id=RP_ID, allow_credentials=allow, timeout=CHAL_TTL * 1000,
                                           user_verification=UserVerificationRequirement.REQUIRED if uid is None
                                           else UserVerificationRequirement.PREFERRED)
    _chal_new(request, kind, uid, opts.challenge, key)
    return _opts_json(opts)


def _transports(raw):
    from webauthn.helpers.structs import AuthenticatorTransport
    out = []
    for x in json.loads(raw or "[]"):
        try:
            out.append(AuthenticatorTransport(x))
        except ValueError:
            pass
    return out or None


def auth_verify(request, cred, uid=None, kind="auth", key="wa", origin=ORIGIN):
    """Passkey-Antwort pruefen -> Benutzername. uid=None: Anmeldung ohne Passwort (Benutzerpruefung am Geraet Pflicht)."""
    from webauthn import verify_authentication_response
    cuid, chal = _chal_take(request, kind, key)
    if not isinstance(cred, dict):
        raise MfaError(t("ungültige Anfrage"))
    cid = str(cred.get("id") or "")[:400]
    with _db() as c:
        r = c.execute("SELECT id, uid, public_key, sign_count, user_handle FROM mfa_passkey WHERE cred_id=?", (cid,)).fetchone()
    want = uid or cuid or None
    if not r or (want and r[1] != want):
        raise MfaError(t("Dieser Passkey ist hier nicht (mehr) hinterlegt."), unknown=not r)
    uh = (cred.get("response") or {}).get("userHandle")
    if uh and uh != r[4]:
        raise MfaError(t("Dieser Passkey ist hier nicht (mehr) hinterlegt."))
    try:
        v = verify_authentication_response(credential=cred, expected_challenge=chal, expected_rp_id=RP_ID, expected_origin=origin,
                                           credential_public_key=b64d(r[2]), credential_current_sign_count=r[3] or 0,
                                           require_user_verification=want is None)
    except Exception as ex:  # noqa: BLE001
        print(f"mfa: Passkey-Anmeldung {r[1]} abgewiesen: {type(ex).__name__}: {ex}", flush=True)
        raise MfaError(_explain(ex, origin))
    with _db() as c, c:
        c.execute("UPDATE mfa_passkey SET sign_count=?, last_used=?, backed_up=? WHERE id=?",
                  (v.new_sign_count, time.time(), 1 if v.credential_backed_up else 0, r[0]))
    return r[1]


# --- Zwischenschritt nach dem Passwort -------------------------------------------------------------------
def pending(request, prefix="mfa", ttl=PENDING_TTL):
    """Benutzername im Zwischenschritt (Passwort richtig, zweiter Faktor fehlt) - None, wenn abgelaufen/ungueltig."""
    uid, at = request.session.get(prefix + "_u"), request.session.get(prefix + "_at")
    try:
        if not uid or not (0 <= time.time() - float(at) <= ttl):
            return None
    except (TypeError, ValueError):
        return None
    u = auth.get_user(uid)
    return uid if u and not u["disabled"] else None


def ip_of(request):
    return (request.client.host if request.client else "") or "?"


def passkey_login_allowed(u):
    """Anmeldung ohne Passwort mit Passkey: gleiche Regeln wie die Anmeldung mit Passwort (lokal bzw. LDAP)."""
    st = auth.settings()
    if not u or u["disabled"] or not supported(u):
        return False
    if u["source"] == "local":
        return bool(st.get("local_login", True))
    return bool(st["ldap"].get("enabled") and st["ldap"].get("password_login", True))


def note_ip_fail(ip, area="pk"):
    with _db() as c, c:
        c.execute("INSERT INTO login_fail (key, ts) VALUES (?,?)", (f"{area}:ip:{auth.ip_bucket(ip)}", time.time()))


def ip_blocked(ip, area="pk", limit=30):
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?",
                         (f"{area}:ip:{auth.ip_bucket(ip)}", time.time() - auth.FAIL_WINDOW)).fetchone()[0] >= limit


# --- Einrichtung verlangt (Richtlinie) -----------------------------------------------------------------
def setup_redirect(request, uid):
    """Nach der Anmeldung: verlangt die Richtlinie einen Faktor und es gibt keinen -> zur Einrichtung (sonst None)."""
    if not request.session.get("mfa_setup"):
        return None
    info = user_info(uid)
    if info and required(info) and not has_mfa(uid):
        return RedirectResponse("/security", status_code=303)
    request.session.pop("mfa_setup", None)
    return None


@router.get("/security", response_class=HTMLResponse)
def security_page(request: Request):
    """Eigene Seite "Anmeldung & Sicherheit" - Ziel nach der Anmeldung, wenn ein zweiter Faktor verlangt wird."""
    uid = _session_user(request)
    if not uid:                                       # z. B. aus den App-Einstellungen geoeffnet - ohne Sitzung kein Name
        return RedirectResponse("/login?next=/security", status_code=303)
    info = user_info(uid)
    nxt = request.session.get("mfa_next") or "/"
    # ?user=<Anmeldename> (App "Im Browser verwalten"): ist hier ein ANDERES Konto angemeldet, deutlich darauf hinweisen
    want = (request.query_params.get("user") or "").strip()[:100]
    other = want if want and want.lower() != uid.lower() else ""
    html = tmpl.get_template("security.html").render(title=title(), v=ASSET_V, name=info["name"], nxt=nxt if nxt in ("/", "/notes", "/app/handoff") else "/",
                                                     need=bool(request.session.get("mfa_setup")) and required(info) and not has_mfa(uid),
                                                     me=uid, other=other)
    return HTMLResponse(html, headers=SEC_HEADERS)


# --- Bestaetigen vor Aenderungen (Passwort, Code oder Passkey) -----------------------------------------------
def fresh(request):
    now = time.time()
    at = max(int(request.session.get("login_at") or 0), int(request.session.get("sec_at") or 0))
    return 0 <= now - at <= FRESH_SEC


def confirm(request, uid, b, area="", kind="confirm", key="wa_c"):
    """Identitaet bestaetigen: {password} | {code} (Authenticator-App) | {passkey: Antwort}. -> Fehlertext oder None."""
    ip = ip_of(request)
    if isinstance(b.get("passkey"), dict):
        try:
            got = auth_verify(request, b["passkey"], uid=uid, kind=kind, key=key)
        except MfaError as ex:
            return str(ex)
        return None if got == uid else t("Passkey passt nicht zu diesem Konto.")
    if b.get("code"):
        if auth.too_many(uid, ip, "mfa"):
            return t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.")
        if totp_use(uid, b.get("code")):
            auth.clear_fails(uid, "mfa")
            return None
        auth.note_fail(uid, ip, "mfa")
        return t("Der Code stimmt nicht.")
    pw = b.get("password")
    if not isinstance(pw, str) or not pw or len(pw) > 200:
        return t("Bitte bestätigen: Passwort, Code oder Passkey.")
    user, _err = auth.authenticate(uid, pw, ip, True, area=area)
    return None if user and user["username"] == uid else t("Passwort falsch.")


def methods(uid):
    """Womit sich das Konto bestaetigen kann (Chat "Neu beginnen", Aenderungen an den Faktoren)."""
    u = auth.get_user(uid)
    out = []
    if u and (u["password_hash"] or u["source"] == "ldap"):
        out.append("password")
    if u and supported(u):
        f = factors(uid)
        if f["passkeys"] and not rp_problem():
            out.append("passkey")
        if f["totp"]:
            out.append("totp")
    return out


# --- Endpunkte: Einstellungen -> Konto -> Anmeldung & Sicherheit -------------------------------------------
def _me(request):
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    return uid, None


def _need_fresh():
    return JSONResponse({"error": t("Zur Sicherheit bitte zuerst bestätigen."), "reauth": True}, status_code=403,
                        headers={"Cache-Control": "no-store"})


def _no_store(d, status=200):
    return JSONResponse(d, status_code=status, headers={"Cache-Control": "no-store"})


def _state(request, uid):
    info = user_info(uid)
    f = factors(uid)
    with _db() as c:
        tp = c.execute("SELECT confirmed FROM mfa_totp WHERE uid=? AND confirmed IS NOT NULL", (uid,)).fetchone()
    return {"supported": supported(info), "source": info["source"], "enabled": has_mfa(uid, f), "required": required(info),
            "passkeys": passkey_list(uid), "totp": f["totp"], "totp_since": tp[0] if tp else None, "recovery_left": f["recovery"],
            "recovery_total": REC_N, "passkey_problem": rp_problem(), "origin": ORIGIN, "rp_id": RP_ID, "fresh": fresh(request),
            "methods": methods(uid), "user": uid}


@router.get("/api/account/security")
def api_security(request: Request):
    uid, err = _me(request)
    if err:
        return err
    return _no_store(_state(request, uid))


@router.post("/api/account/security/options")
def api_security_options(request: Request):
    """Passkey-Optionen zum Bestaetigen (vor Aenderungen)."""
    uid, err = _me(request)
    if err:
        return err
    try:
        return _no_store({"options": auth_options(request, uid, kind="confirm", key="wa_c"), "origin": ORIGIN})
    except MfaError as ex:
        return _bad(str(ex), 409)


@router.post("/api/account/security/confirm")
async def api_security_confirm(request: Request):
    uid, err = _me(request)
    if err:
        return err
    b = await _body(request) or {}
    msg = await run_in_threadpool(confirm, request, uid, b)
    if msg:
        return _bad(msg, 403)
    request.session["sec_at"] = int(time.time())
    return _no_store({"ok": True, "valid": FRESH_SEC})


def _guard(request, need_fresh=True):
    """-> (uid, info, fehlerantwort)"""
    uid, err = _me(request)
    if err:
        return None, None, err
    info = user_info(uid)
    if not supported(info):
        return None, None, _bad(t("Zwei-Faktor-Anmeldung regelt dein Anmeldedienst (SSO)."), 403)
    if need_fresh and not fresh(request):
        return None, None, _need_fresh()
    return uid, info, None


def _first_factor(uid):
    """Erster Faktor eingerichtet -> Wiederherstellungscodes erzeugen (sonst None)."""
    with _db() as c:
        if c.execute("SELECT COUNT(*) FROM mfa_recovery WHERE uid=?", (uid,)).fetchone()[0]:
            return None
    return recovery_new(uid)


def _last_factor_blocked(uid, info):
    f = factors(uid)
    return required(info) and f["passkeys"] + (1 if f["totp"] else 0) <= 1


def _maybe_off(uid):
    """Kein Faktor mehr -> 2FA aus: Wiederherstellungscodes weg."""
    if not has_mfa(uid):
        with _db() as c, c:
            c.execute("DELETE FROM mfa_recovery WHERE uid=?", (uid,))
        print(f"mfa: {uid} hat keinen zweiten Faktor mehr", flush=True)


@router.post("/api/account/passkeys/options")
def api_passkey_options(request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    p = rp_problem()
    if p:
        return _bad(p, 409)
    try:
        return _no_store({"options": reg_options(request, uid), "origin": ORIGIN})
    except MfaError as ex:
        return _bad(str(ex), 409)


@router.post("/api/account/passkeys")
async def api_passkey_add(request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    b = await _body(request) or {}
    try:
        pid = await run_in_threadpool(reg_verify, request, uid, b.get("credential"), b.get("name"))
    except MfaError as ex:
        return _bad(str(ex))
    return _no_store({"ok": True, "id": pid, "recovery": _first_factor(uid)})


@router.patch("/api/account/passkeys/{pid}")
async def api_passkey_rename(pid: int, request: Request):
    uid, info, err = _guard(request, need_fresh=False)
    if err:
        return err
    b = await _body(request) or {}
    name = re.sub(r"\s+", " ", clean_line(b.get("name") if isinstance(b.get("name"), str) else "")).strip()[:60]
    if not name:
        return _bad(t("Bitte einen Namen eingeben."))
    with _db() as c, c:
        n = c.execute("UPDATE mfa_passkey SET name=? WHERE id=? AND uid=?", (name, pid, uid)).rowcount
    return _no_store({"ok": True, "name": name}) if n else _bad("not found", 404)


@router.delete("/api/account/passkeys/{pid}")
def api_passkey_delete(pid: int, request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    with _db() as c:
        if not c.execute("SELECT 1 FROM mfa_passkey WHERE id=? AND uid=?", (pid, uid)).fetchone():
            return _bad("not found", 404)
    if _last_factor_blocked(uid, info):
        return _bad(t("Für dein Konto ist ein zweiter Faktor vorgeschrieben – richte zuerst einen anderen ein."), 409)
    with _db() as c, c:
        c.execute("DELETE FROM mfa_passkey WHERE id=? AND uid=?", (pid, uid))
    print(f"mfa: {uid} Passkey {pid} geloescht", flush=True)
    _maybe_off(uid)
    return _no_store(_state(request, uid))


def totp_begin(uid, reuse=False):
    """Authenticator-App einrichten: neues Geheimnis (noch nicht aktiv) -> QR-Code (SVG, im Server erzeugt) + Schluessel
    zum Abtippen. reuse=True: ein schon begonnenes (unbestaetigtes) Geheimnis weiter anzeigen (Admin-Seite nach falschem
    Code). Auch fuer den Admin-Bereich (Einrichtung ohne JavaScript)."""
    import segno
    with _db() as c:
        if c.execute("SELECT 1 FROM mfa_totp WHERE uid=? AND confirmed IS NOT NULL", (uid,)).fetchone():
            raise MfaError(t("Eine Authenticator-App ist schon eingerichtet – zum Wechseln zuerst entfernen."), status=409)
        r = c.execute("SELECT secret FROM mfa_totp WHERE uid=? AND confirmed IS NULL", (uid,)).fetchone() if reuse else None
    key = auth._dec(r[0]) if r else ""
    if not key:
        key = base64.b32encode(secrets.token_bytes(20)).decode()
        with _db() as c, c:
            c.execute("DELETE FROM mfa_totp WHERE uid=?", (uid,))
            c.execute("INSERT INTO mfa_totp (uid, secret, confirmed, last_step, created) VALUES (?,?,NULL,0,?)", (uid, auth._enc(key), time.time()))
    issuer = title()
    uri = (f"otpauth://totp/{quote(issuer, safe='')}:{quote(uid, safe='')}?secret={key}&issuer={quote(issuer, safe='')}"
           f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}")
    qr = segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#000000", light="#ffffff")
    return {"secret": " ".join(key[i:i + 4] for i in range(0, len(key), 4)), "uri": uri, "qr": qr,
            "issuer": issuer, "account": uid, "digits": TOTP_DIGITS, "period": TOTP_STEP}


def totp_finish(uid, code):
    """Einrichtung mit einem Code bestaetigen -> Wiederherstellungscodes (beim ersten Faktor, sonst None).
    MfaError: falscher Code (400) bzw. keine Einrichtung begonnen (409). Fehlversuche zaehlt der Aufrufer."""
    with _db() as c:
        r = c.execute("SELECT secret FROM mfa_totp WHERE uid=? AND confirmed IS NULL", (uid,)).fetchone()
    if not r:
        raise MfaError(t("Bitte die Einrichtung neu starten."), status=409)
    st = totp_match(_totp_secret(r[0]), code)
    if st is None:
        raise MfaError(t("Der Code stimmt nicht. Stimmt die Uhrzeit auf dem Handy?"))
    with _db() as c, c:
        if not c.execute("UPDATE mfa_totp SET confirmed=?, last_step=? WHERE uid=? AND confirmed IS NULL", (time.time(), st, uid)).rowcount:
            raise MfaError(t("Bitte die Einrichtung neu starten."), status=409)
    print(f"mfa: {uid} Authenticator-App eingerichtet", flush=True)
    return _first_factor(uid)


@router.post("/api/account/totp/setup")
def api_totp_setup(request: Request):
    """Neues Geheimnis (noch nicht aktiv) -> QR-Code (SVG, im Server erzeugt) + Schluessel zum Abtippen."""
    uid, info, err = _guard(request)
    if err:
        return err
    try:
        return _no_store(totp_begin(uid))
    except MfaError as ex:
        return _bad(str(ex), ex.status)


@router.post("/api/account/totp/confirm")
async def api_totp_confirm(request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    b = await _body(request) or {}
    ip = ip_of(request)
    if auth.too_many(uid, ip, "mfa"):
        return _bad(t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen."), 429)
    try:
        rec = await run_in_threadpool(totp_finish, uid, b.get("code"))
    except MfaError as ex:
        if ex.status == 400:
            auth.note_fail(uid, ip, "mfa")
        return _bad(str(ex), ex.status)
    return _no_store({"ok": True, "recovery": rec})


@router.delete("/api/account/totp")
def api_totp_delete(request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    if factors(uid)["totp"] and _last_factor_blocked(uid, info):
        return _bad(t("Für dein Konto ist ein zweiter Faktor vorgeschrieben – richte zuerst einen anderen ein."), 409)
    with _db() as c, c:
        c.execute("DELETE FROM mfa_totp WHERE uid=?", (uid,))
    print(f"mfa: {uid} Authenticator-App entfernt", flush=True)
    _maybe_off(uid)
    return _no_store(_state(request, uid))


@router.post("/api/account/recovery")
def api_recovery_new(request: Request):
    uid, info, err = _guard(request)
    if err:
        return err
    if not has_mfa(uid):
        return _bad(t("Wiederherstellungscodes gibt es, sobald ein Passkey oder eine Authenticator-App eingerichtet ist."), 409)
    print(f"mfa: {uid} neue Wiederherstellungscodes", flush=True)
    return _no_store({"ok": True, "recovery": recovery_new(uid)})
