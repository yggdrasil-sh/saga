"""Benutzer, Gruppen und Anmeldung: lokale Konten, LDAP/Active Directory, OIDC (SSO) - alles im Admin-Bereich einstellbar.

Grundsaetze
- uid in allen Daten-Tabellen = Benutzername (klein). Benutzernamen werden nie umbenannt.
- Gruppen eines Benutzers = lokale Gruppen (Admin) + bei der Anmeldung aus LDAP/OIDC uebernommene Gruppen.
- Ein externer Login (LDAP/OIDC) uebernimmt NIE ein bestehendes lokales Konto mit gleichem Namen
  (Schutz vor Kontouebernahme ueber den Identitaetsanbieter). Vom Admin vorab angelegte Konten mit
  Quelle "ldap"/"oidc" werden beim ersten Login verknuepft.
"""
import base64
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import threading
import time
from urllib.parse import urlparse

from db import clean_line, connect as _db
from saga.i18n import t

E = os.environ
CACHE_TTL = int(E.get("CACHE_TTL", "60"))
CA_FILE = E.get("CA_FILE", "/etc/ssl/certs/ca-certificates.crt")
SECRET = E.get("SECRET_KEY") or E.get("SESSION_SECRET") or ""
USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._@-]{0,63}")
GROUP_RE = re.compile(r"[a-z0-9][a-z0-9._ -]{0,99}")

# --- Einstellungen ---------------------------------------------------------------------------
DEFAULTS = {
    "title": E.get("DASHBOARD_TITLE", "Saga"),
    "registration": False,                 # Selbst-Registrierung erlaubt
    "local_login": True,                   # Anmeldung mit lokalem Konto / LDAP-Passwort auf der Login-Seite
    "group_prefix": "s_",                  # Gruppen-Kennung: <prefix><dienst> gibt den Dienst frei
    "default_groups": [],                  # neue Konten (Registrierung, Admin, Erst-Login) bekommen diese Gruppen
    "allow_private_hosts": False,          # Kalender/Immich auf internen Adressen erlauben (fuer alle)
    "allowed_hosts": "",                   # oder nur diese internen Hosts (Komma-getrennt)
    "default_lang": "",                    # Standardsprache (de/en/es), leer = Browsersprache
    "publish_enabled": True,               # Benutzer duerfen eigene Seiten veroeffentlichen (/@name/<slug>)
    "public_home": 0,                      # veroeffentlichte Seite (note_id) als Startseite "/" fuer Besucher, 0 = keine
    "share_enabled": True,                 # Seiten mit anderen Konten und Gruppen teilen (1.3)
    "fed_enabled": False,                  # Foederation: mit verbundenen Saga-Instanzen teilen (2.0, saga/federation.py)
    "share_group_scope": False,            # nur mit Konten teilen, mit denen man eine Gruppe gemeinsam hat
    "chat_enabled": True,                  # Chats mit Ende-zu-Ende-Verschluesselung (1.5)
    "chat_retention_max_days": 0,          # Chat-Nachrichten spaetestens nach so vielen Tagen loeschen (0 = unbegrenzt)
    "push": {"enabled": True, "webpush": True, "ntfy": True, "unifiedpush": True},   # Mitteilungen aufs Handy/Desktop (1.7)
    "apns": {"enabled": False, "team_id": "", "key_id": "", "bundle_id": "", "p8": "", "url": ""},  # iOS/macOS-App (url: nur Tests)
    "update_check": E.get("SAGA_UPDATE_CHECK", "1") not in ("0", "false", "no", "off"),   # im Admin-Bereich neue Versionen melden
    "oidc": {"enabled": False, "issuer": "", "client_id": "", "client_secret": "", "scopes": "openid profile email",
             "username_claim": "preferred_username", "name_claim": "name", "email_claim": "email",
             "groups_claim": "groups", "label": "Mit SSO anmelden", "auto_create": True,
             "allowed_group": "", "admin_group": "", "verify_tls": True},
    "ldap": {"enabled": False, "preset": "freeipa", "url": "", "starttls": True, "verify_tls": True,
             "bind_dn": "", "bind_password": "", "user_base": "", "user_filter": "(&(objectClass=person)(uid={username}))",
             "attr_username": "uid", "attr_name": "cn", "attr_mail": "mail",
             "group_mode": "memberof", "group_base": "", "group_filter": "(member={dn})", "group_name_attr": "cn",
             "auto_create": True, "allowed_group": "", "admin_group": "", "services_from_description": False,
             "password_login": True,          # Anmeldung mit Verzeichnis-Passwort auf der Anmeldeseite
             "sso_groups": False},            # Gruppen von SSO-Konten aus dem Verzeichnis lesen (z. B. Keycloak mit FreeIPA)
    # KI-Assistent (1.6, saga/ai.py): aus, bis der Admin einen Endpunkt eintraegt. pinned = beim Speichern aufgeloeste IPs
    # (interne Adressen nur diese), groups leer = alle, quota = Anfragen je Person und Tag (0 = unbegrenzt),
    # context = Zeichen Seiteninhalt je Anfrage, history = Personen duerfen einen Verlauf einschalten
    "ai": {"enabled": False, "provider": "openai", "url": "", "model": "", "api_key": "", "groups": [], "timeout": 60,
           "quota": 50, "context": 8000, "history": True, "pinned": []},
}
SECRET_FIELDS = {("oidc", "client_secret"), ("ldap", "bind_password"), ("apns", "p8"), ("ai", "api_key")}
LDAP_PRESETS = {
    "freeipa": {"user_filter": "(&(objectClass=person)(uid={username})(!(nsAccountLock=TRUE)))", "attr_username": "uid", "attr_name": "cn",
                "attr_mail": "mail", "group_mode": "memberof", "group_filter": "(member={dn})", "group_name_attr": "cn"},
    "ad": {"user_filter": "(&(objectClass=user)(objectCategory=person)(sAMAccountName={username}))", "attr_username": "sAMAccountName",
           "attr_name": "displayName", "attr_mail": "mail", "group_mode": "ad_nested", "group_filter": "(member={dn})",
           "group_name_attr": "cn"},
    "openldap": {"user_filter": "(&(objectClass=inetOrgPerson)(uid={username}))", "attr_username": "uid", "attr_name": "cn",
                 "attr_mail": "mail", "group_mode": "search", "group_filter": "(|(member={dn})(memberUid={username}))",
                 "group_name_attr": "cn"},
}

_settings_cache = {"t": 0.0, "v": None}


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("hk-settings|" + SECRET).encode()).digest()))


def _enc(v):
    return "enc:" + _fernet().encrypt(v.encode()).decode() if v else ""


def _dec(v):
    if isinstance(v, str) and v.startswith("enc:"):
        try:
            return _fernet().decrypt(v[4:].encode()).decode()
        except Exception:  # noqa: BLE001 - Schluessel gewechselt
            return ""
    return v or ""


def settings(fresh=False):
    """Alle Einstellungen (Standardwerte + gespeicherte), Geheimnisse entschluesselt. 5 s zwischengespeichert."""
    if not fresh and _settings_cache["v"] is not None and time.time() - _settings_cache["t"] < 5:
        return _settings_cache["v"]
    out = copy.deepcopy(DEFAULTS)
    with _db() as c:
        for k, v in c.execute("SELECT k, v FROM setting").fetchall():
            try:
                val = json.loads(v)
            except ValueError:
                continue
            if isinstance(out.get(k), dict) and isinstance(val, dict):
                out[k].update(val)
            else:
                out[k] = val
    for sec, f in SECRET_FIELDS:
        out[sec][f] = _dec(out[sec].get(f))
    _settings_cache.update(t=time.time(), v=out)
    return out


def save_setting(k, v):
    """Einen Bereich speichern. Leeres Geheimnis = bisheriges behalten."""
    if isinstance(v, dict):
        cur = settings(fresh=True).get(k, {})
        v = dict(v)
        for sec, f in SECRET_FIELDS:
            if sec == k and f in v:
                v[f] = _enc(v[f]) if v[f] else _enc(cur.get(f, ""))
    with _db() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))
    _settings_cache["v"] = None
    _ui_cache.clear()


# --- Passwoerter -----------------------------------------------------------------------------
# scrypt braucht ca. 32 MiB je Aufruf. Ohne Begrenzung koennten viele gleichzeitige Anmeldeversuche (ohne Konto)
# den Speicher fuellen -> hoechstens KDF_PARALLEL Berechnungen gleichzeitig, weitere warten kurz bzw. bekommen "Busy".
_KDF_SLOTS = threading.BoundedSemaphore(max(1, int(E.get("KDF_PARALLEL", "4"))))
KDF_WAIT = 2.0                        # Sekunden auf einen freien Platz warten, dann Busy (kurz: Threadpool nicht blockieren)


class Busy(RuntimeError):
    """Gerade zu viele Passwort-Pruefungen gleichzeitig - spaeter erneut versuchen (kein Fehlversuch)."""


def _scrypt(pw, salt, n, r, p, dklen, wait=None):
    if not _KDF_SLOTS.acquire(timeout=KDF_WAIT if wait is None else wait):
        raise Busy()
    try:
        return hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=dklen)
    finally:
        _KDF_SLOTS.release()


def hash_password(pw):
    salt = secrets.token_bytes(16)
    h = _scrypt(pw, salt, 2 ** 15, 8, 1, 32, wait=60)
    return "scrypt$32768$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(pw, stored):
    """True/False; Busy, wenn gerade zu viele Pruefungen laufen."""
    try:
        alg, n, r, p, salt, h = (stored or "").split("$")
        if alg != "scrypt" or int(n) > 2 ** 17 or int(r) > 16 or int(p) > 4:
            return False
        calc = _scrypt(pw, base64.b64decode(salt), int(n), int(r), int(p), len(base64.b64decode(h)))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def password_problem(pw):
    if len(pw or "") < 10:
        return t("Das Passwort muss mindestens 10 Zeichen lang sein.")
    if len(pw) > 200:
        return t("Das Passwort ist zu lang.")
    return None


_DUMMY = None


def _burn(pw):
    """Gleiche Rechenzeit bei unbekanntem Benutzer (keine Rueckschluesse ueber Antwortzeit)."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("dummy-password")
    verify_password(pw, _DUMMY)


# --- Sperre bei Fehlversuchen -------------------------------------------------------------------
# Zaehler (15 Minuten): Benutzer+Adresse (8), Benutzer insgesamt (40, gegen verteiltes Raten), Adresse (30).
# So kann ein Fremder ein Konto nicht mit 8 Fehlversuchen fuer alle sperren. area="admin": eigene Zaehler fuer
# den Admin-Port - Fehlversuche auf der oeffentlichen Seite sperren den Admin-Bereich nicht.
FAIL_WINDOW, FAIL_MAX_USER_IP, FAIL_MAX_USER, FAIL_MAX_IP = 900, 8, 40, 30


def _fail_keys(username, ip, area=""):
    p = "adm:" if area == "admin" else ""
    return p + "ui:" + username + "|" + ip, p + "u:" + username, p + "ip:" + ip


def too_many(username, ip, area=""):
    t = time.time() - FAIL_WINDOW
    kui, ku, ki = _fail_keys(username, ip, area)
    with _db() as c, c:
        c.execute("DELETE FROM login_fail WHERE ts < ?", (t - 3600,))
        n = [c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", (k, t)).fetchone()[0] for k in (kui, ku, ki)]
    return n[0] >= FAIL_MAX_USER_IP or n[1] >= FAIL_MAX_USER or n[2] >= FAIL_MAX_IP


def note_fail(username, ip, area=""):
    with _db() as c, c:
        for k in _fail_keys(username, ip, area):
            c.execute("INSERT INTO login_fail (key, ts) VALUES (?,?)", (k, time.time()))


def clear_fails(username, area=None):
    """Nach erfolgreicher Anmeldung (bzw. Notfall-Reset, area=None: alle Bereiche) die Benutzer-Zaehler leeren."""
    with _db() as c, c:
        for p in (("", "adm:") if area is None else ("adm:" if area == "admin" else "",)):
            c.execute("DELETE FROM login_fail WHERE key=? OR instr(key, ?) = 1", (p + "u:" + username, p + "ui:" + username + "|"))


# --- Benutzer und Gruppen --------------------------------------------------------------------------
USER_COLS = "id, username, display_name, email, password_hash, source, ext_id, is_admin, disabled, created, last_login, pw_changed, logout_at"


def _user_dict(r):
    if not r:
        return None
    keys = [k.strip() for k in USER_COLS.split(",")]
    return dict(zip(keys, r))


def get_user(username=None, uid=None):
    with _db() as c:
        if uid is not None:
            r = c.execute(f"SELECT {USER_COLS} FROM users WHERE id=?", (uid,)).fetchone()
        else:
            r = c.execute(f"SELECT {USER_COLS} FROM users WHERE username=?", ((username or "").lower(),)).fetchone()
    return _user_dict(r)


def norm_username(u):
    u = (u or "").strip().lower()
    return u if USERNAME_RE.fullmatch(u) else None


def norm_group(g):
    g = re.sub(r"\s+", " ", (g or "").strip().lower())
    return g if GROUP_RE.fullmatch(g) else None


def ensure_group(c, name, description=""):
    name = norm_group(name)
    if not name:
        return None
    r = c.execute("SELECT id FROM groups WHERE name=?", (name,)).fetchone()
    if r:
        return r[0]
    return c.execute("INSERT INTO groups (name, description, created) VALUES (?,?,?)", (name, description, time.time())).lastrowid


def create_user(username, password=None, display_name="", email="", source="local", is_admin=False, groups=None, ext_id=None):
    """-> (user, fehler)"""
    u = norm_username(username)
    if not u:
        return None, t("Ungültiger Benutzername (a–z, 0–9, . _ - @; höchstens 64 Zeichen).")
    if source == "local" and password is not None:
        p = password_problem(password)
        if p:
            return None, p
    if get_user(u):
        return None, t("Diesen Benutzernamen gibt es schon.")
    with _db() as c, c:
        uid = c.execute("INSERT INTO users (username, display_name, email, password_hash, source, ext_id, is_admin, created, pw_changed) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (u, clean_line(display_name or "").strip()[:120], (email or "").strip()[:200],
                         hash_password(password) if password else None, source, ext_id, 1 if is_admin else 0,
                         time.time(), time.time() if password else None)).lastrowid
        for g in list(groups or []) + list(settings().get("default_groups") or []):
            gid = ensure_group(c, g)
            if gid:
                c.execute("INSERT OR IGNORE INTO user_group (user_id, group_id) VALUES (?,?)", (uid, gid))
    return get_user(u), None


def set_password(username, pw):
    with _db() as c, c:
        c.execute("UPDATE users SET password_hash=?, pw_changed=? WHERE username=?", (hash_password(pw), time.time(), username))
    _ui_cache.pop(username, None)
    drop_pw_file(username)                 # erzeugtes Start-Passwort gilt nicht mehr


def set_user_groups(user_id, names):
    with _db() as c, c:
        c.execute("DELETE FROM user_group WHERE user_id=?", (user_id,))
        for g in names:
            gid = ensure_group(c, g)
            if gid:
                c.execute("INSERT OR IGNORE INTO user_group (user_id, group_id) VALUES (?,?)", (user_id, gid))
    _ui_cache.clear()


def set_ext_groups(user_id, names):
    with _db() as c, c:
        c.execute("DELETE FROM user_ext_group WHERE user_id=?", (user_id,))
        for g in {norm_group(n) for n in names}:
            if g:
                c.execute("INSERT OR IGNORE INTO user_ext_group (user_id, name) VALUES (?,?)", (user_id, g))
    _ui_cache.clear()


def groups_of(user_id):
    with _db() as c:
        loc = [r[0] for r in c.execute("SELECT g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id WHERE ug.user_id=?", (user_id,))]
        ext = [r[0] for r in c.execute("SELECT name FROM user_ext_group WHERE user_id=?", (user_id,))]
    return set(loc), set(ext)


_ui_cache = {}


def user_info(uid, fresh=False):
    """Benutzer + Gruppen; None = unbekannt oder gesperrt. fresh=True (Anmeldung, App-Erneuerung): LDAP-Gruppen neu
    lesen und gesperrte LDAP-Konten sofort abweisen. Sonst CACHE_TTL Sekunden zwischengespeichert."""
    hit = _ui_cache.get(uid)
    if not fresh and hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    u = get_user(uid)
    if not u or u["disabled"]:
        _ui_cache.pop(uid, None)
        return None
    lc = settings()["ldap"]
    if fresh and lc["enabled"] and (u["source"] == "ldap" or (u["source"] == "oidc" and lc.get("sso_groups"))):
        try:
            ok = ldap_refresh(u)
        except Exception as ex:  # noqa: BLE001 - LDAP gerade weg: bisherigen Stand behalten
            print(f"ldap-refresh {uid}: {type(ex).__name__}: {ex}", flush=True)
            ok = True
        if not ok:
            _ui_cache.pop(uid, None)
            return None
        u = get_user(uid)
    loc, ext = groups_of(u["id"])
    info = {"id": u["id"], "username": u["username"], "name": u["display_name"] or u["username"], "mail": u["email"],
            "source": u["source"], "is_admin": bool(u["is_admin"]), "groups": loc | ext, "local_groups": loc,
            "ext_groups": ext, "pw_changed": u["pw_changed"], "last_login": u["last_login"],
            "epoch": max(u["pw_changed"] or 0, u["logout_at"] or 0)}         # aeltere Sitzungen sind ungueltig
    if len(_ui_cache) > 1000:
        _ui_cache.clear()
    _ui_cache[uid] = (time.time(), info)
    return info


def touch_login(username):
    with _db() as c, c:
        c.execute("UPDATE users SET last_login=? WHERE username=?", (time.time(), username))


# --- Anmeldung mit Passwort (lokal, sonst LDAP) ------------------------------------------------------
def authenticate(username, password, ip, allow_local=True, area=""):
    """-> (user, fehlertext). Fehlertexte verraten nicht, ob es den Benutzer gibt.
    allow_local=False: lokale Konten werden abgewiesen (Einstellung "Anmeldung mit lokalem Konto" aus).
    area="admin": Fehlversuche getrennt von der Hauptseite zaehlen."""
    try:
        return _authenticate(username, password, ip, allow_local, area)
    except Busy:
        return None, t("Gerade sehr viele Anmeldungen – bitte gleich noch einmal versuchen.")


def _authenticate(username, password, ip, allow_local, area):
    bad = t("Benutzername oder Passwort falsch.")
    u = norm_username(username)
    if not u or not password or len(password) > 200:
        return None, bad
    if too_many(u, ip, area):
        return None, t("Zu viele Fehlversuche – bitte in 15 Minuten erneut versuchen.")
    st = settings()
    user = get_user(u)
    if user and user["disabled"]:
        _burn(password)
        note_fail(u, ip, area)
        return None, bad
    if user and user["source"] == "local":
        if not allow_local:
            _burn(password)
            note_fail(u, ip, area)
            return None, bad
        if user["password_hash"] and verify_password(password, user["password_hash"]):
            clear_fails(u, area)
            touch_login(u)
            return user, None
        note_fail(u, ip, area)
        return None, bad
    if st["ldap"]["enabled"] and st["ldap"].get("password_login", True) and (user is None or user["source"] == "ldap"):
        try:
            res, err = ldap_login(u, password)
        except Busy:
            raise
        except Exception as ex:  # noqa: BLE001
            print(f"ldap-login {u}: {type(ex).__name__}: {ex}", flush=True)
            return None, t("Verzeichnisdienst (LDAP) gerade nicht erreichbar.")
        if res:
            clear_fails(u, area)
            touch_login(res["username"])
            return res, None
        note_fail(u, ip, area)
        return None, err or bad
    _burn(password)
    note_fail(u, ip, area)
    return None, bad


# --- LDAP / Active Directory ------------------------------------------------------------------------
def _ldap_server(cfg):
    from ldap3 import Server, Tls
    url = cfg["url"].strip()
    use_ssl = url.lower().startswith("ldaps://")
    tls = Tls(validate=ssl.CERT_REQUIRED if cfg.get("verify_tls", True) else ssl.CERT_NONE,
              ca_certs_file=CA_FILE if os.path.isfile(CA_FILE) else None)
    return Server(url, use_ssl=use_ssl, tls=tls, connect_timeout=6, get_info="NO_INFO")


def _ldap_bind(cfg, dn, pw):
    from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, Connection
    srv = _ldap_server(cfg)
    starttls = cfg.get("starttls") and not cfg["url"].lower().startswith("ldaps://")
    return Connection(srv, dn, pw, auto_bind=AUTO_BIND_TLS_BEFORE_BIND if starttls else AUTO_BIND_NO_TLS,
                      receive_timeout=10, raise_exceptions=False)


def _service_conn(cfg):
    c = _ldap_bind(cfg, cfg["bind_dn"] or None, cfg["bind_password"] or None)
    if not c.bound:
        raise RuntimeError(t("Dienstkonto-Anmeldung am LDAP fehlgeschlagen: {why}", why=c.result.get("description")))
    return c


def _esc(v):
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(v)


def _attr(entry, name):
    if not name:
        return ""
    try:
        v = entry[name].value
    except (KeyError, IndexError, Exception):  # noqa: BLE001
        return ""
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v or "")


def _cn_of(dn):
    first = (dn or "").split(",", 1)[0]
    return first.split("=", 1)[1] if "=" in first else first


def _ldap_find(c, cfg, username):
    from ldap3 import SUBTREE
    flt = cfg["user_filter"].replace("{username}", _esc(username))
    attrs = [a for a in {cfg["attr_username"], cfg["attr_name"], cfg["attr_mail"], "memberOf"} if a]
    c.search(cfg["user_base"], flt, SUBTREE, attributes=attrs, size_limit=2)
    ents = [e for e in c.entries]
    return ents[0] if len(ents) == 1 else None


def _ldap_groups(c, cfg, entry, username):
    from ldap3 import SUBTREE
    dn = entry.entry_dn
    mode = cfg.get("group_mode") or "memberof"
    names = set()
    if mode == "memberof":
        try:
            for g in entry["memberOf"].values:
                names.add(_cn_of(str(g)))
        except (KeyError, Exception):  # noqa: BLE001
            pass
    else:
        base = cfg.get("group_base") or cfg["user_base"]
        if mode == "ad_nested":                       # AD: alle Gruppen inkl. verschachtelter (LDAP_MATCHING_RULE_IN_CHAIN)
            flt = f"(member:1.2.840.113556.1.4.1941:={_esc(dn)})"
        else:
            flt = cfg["group_filter"].replace("{dn}", _esc(dn)).replace("{username}", _esc(username))
        c.search(base, flt, SUBTREE, attributes=[cfg.get("group_name_attr") or "cn"], size_limit=1000)
        for e in c.entries:
            names.add(_attr(e, cfg.get("group_name_attr") or "cn") or _cn_of(e.entry_dn))
    return {g.lower() for g in names if g}


def ldap_login(username, password):
    """Mit Passwort am LDAP anmelden -> (benutzer, fehler). Legt das Konto an bzw. aktualisiert es."""
    cfg = settings()["ldap"]
    with _service_conn(cfg) as c:
        e = _ldap_find(c, cfg, username)
        if e is None:
            _burn(password)
            return None, None
        groups = _ldap_groups(c, cfg, e, username)
        dn = e.entry_dn
        name, mail = _attr(e, cfg["attr_name"]), _attr(e, cfg["attr_mail"])
    uc = _ldap_bind(cfg, dn, password)
    ok = uc.bound
    uc.unbind()
    if not ok:
        return None, None
    return _ext_user(username, "ldap", dn, name, mail, groups, cfg)


def ldap_refresh(user):
    """Gruppen eines LDAP-Kontos neu lesen. False = im Verzeichnis nicht mehr vorhanden/nicht berechtigt."""
    cfg = settings()["ldap"]
    with _service_conn(cfg) as c:
        e = _ldap_find(c, cfg, user["username"])
        if e is None:
            return False
        groups = _ldap_groups(c, cfg, e, user["username"])
    if cfg.get("allowed_group") and cfg["allowed_group"].lower() not in groups:
        return False
    set_ext_groups(user["id"], groups)
    if cfg.get("admin_group"):
        with _db() as c2, c2:
            c2.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if cfg["admin_group"].lower() in groups else 0, user["id"]))
    return True


def ldap_user_groups(username):
    """Gruppen eines Benutzers aus dem Verzeichnis (fuer SSO-Konten) - None, wenn nicht gefunden/gesperrt."""
    cfg = settings()["ldap"]
    with _service_conn(cfg) as c:
        e = _ldap_find(c, cfg, username)
        return None if e is None else _ldap_groups(c, cfg, e, username)


def ldap_exists(username):
    """Gibt es den Namen im Verzeichnis? (Registrierung: lokale Konten duerfen LDAP-Benutzern den Namen nicht wegnehmen)"""
    cfg = settings()["ldap"]
    with _service_conn(cfg) as c:
        return _ldap_find(c, cfg, username) is not None


def ldap_test(cfg, username=""):
    """Admin-Test: Verbindung + Dienstkonto, optional einen Benutzer suchen (ohne Passwort)."""
    out = []
    with _service_conn(cfg) as c:
        out.append(t("✓ Verbindung und Dienstkonto-Anmeldung erfolgreich"))
        if username:
            e = _ldap_find(c, cfg, username.lower())
            if e is None:
                out.append(t("✗ Benutzer „{username}“ nicht gefunden (Filter: {flt})", username=username, flt=cfg['user_filter']))
            else:
                gs = sorted(_ldap_groups(c, cfg, e, username.lower()))
                out.append(t("✓ Benutzer gefunden: {dn}", dn=e.entry_dn))
                out.append("  " + t("Name: {name} · E-Mail: {mail}", name=_attr(e, cfg['attr_name']) or '–', mail=_attr(e, cfg['attr_mail']) or '–'))
                out.append("  " + t("Gruppen ({n}):", n=len(gs)) + " " + (", ".join(gs[:40]) or "–"))
    return out


def ldap_services(prefix):
    """FreeIPA-Stil: Dienste aus Gruppenbeschreibungen 'Text || Name|URL|icon|Kategorie' (Gruppen mit Kennung)."""
    from ldap3 import SUBTREE
    cfg = settings()["ldap"]
    out = {}
    with _service_conn(cfg) as c:
        base = cfg.get("group_base") or cfg["user_base"]
        c.search(base, f"(&(cn={_esc(prefix)}*)(description=*))", SUBTREE, attributes=["cn", "description"], size_limit=2000)
        for e in c.entries:
            for s in parse_description(_attr(e, "cn"), _attr(e, "description")):
                out[s["key"]] = s
    return out


def parse_description(cn, desc):
    if not desc or "||" not in desc:
        return []
    out, grp = [], cn.lower()
    for i, raw in enumerate(desc.split("||")[1:]):
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) < 2 or not parts[0] or urlparse(parts[1]).scheme not in ("http", "https"):
            continue
        icon = parts[2] if len(parts) > 2 else ""
        out.append({"key": grp if i == 0 else f"{grp}~{i + 1}", "groups": [grp], "name": parts[0], "url": parts[1],
                    "host": urlparse(parts[1]).netloc, "icon": icon if icon.replace("-", "").isalnum() else "",
                    "category": parts[3] if len(parts) > 3 and parts[3] else "Dienste", "source": "ldap", "check": True})
    return out


# --- Externe Konten (LDAP, OIDC) ------------------------------------------------------------------
def _ext_user(username, source, ext_id, name, mail, groups, cfg):
    """Konto fuer einen erfolgreichen externen Login holen/anlegen -> (user, fehler)"""
    username = norm_username(username)
    if not username:
        return None, t("Der Benutzername vom Anmeldedienst ist hier nicht zulässig.")
    groups = {g.lower() for g in groups if g}
    if cfg.get("allowed_group") and cfg["allowed_group"].lower() not in groups:
        return None, t("Kein Zugang – dein Konto ist nicht für Saga freigeschaltet.")
    user = get_user(username)
    if user and user["source"] != source:
        return None, t("Dieser Benutzername ist bereits als anderes Konto vergeben – bitte an den Admin wenden.")
    if user and user["disabled"]:
        return None, t("Dein Konto ist gesperrt.")
    if user and source == "oidc" and user["ext_id"] and ext_id and user["ext_id"] != ext_id:
        # Konto ist schon mit einer anderen SSO-Identitaet (sub) verknuepft: gleicher Name, aber eine andere Person
        # (z. B. Name/E-Mail beim Anbieter selbst geaendert) -> nicht uebernehmen
        print(f"sso: {username}: andere Kennung (sub) als bei der ersten Anmeldung - abgewiesen", flush=True)
        return None, t("Dieses Konto ist mit einer anderen SSO-Identität verknüpft – bitte an den Admin wenden.")
    if not user:
        if not cfg.get("auto_create", True):
            return None, t("Für dich gibt es hier noch kein Konto – bitte an den Admin wenden.")
        user, err = create_user(username, None, name, mail, source=source, ext_id=ext_id)
        if err:
            return None, err
    with _db() as c, c:
        c.execute("UPDATE users SET display_name=?, email=?, ext_id=? WHERE id=?",
                  (clean_line(name or user["display_name"] or "").strip()[:120], (mail or user["email"] or "")[:200], ext_id, user["id"]))
        if cfg.get("admin_group"):
            c.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if cfg["admin_group"].lower() in groups else 0, user["id"]))
    set_ext_groups(user["id"], groups)
    return get_user(username), None


# --- OIDC (SSO) -----------------------------------------------------------------------------------
_oidc = {"key": None, "client": None}


def oidc_client():
    """authlib-Client passend zu den aktuellen Einstellungen (bei Aenderung neu angelegt)."""
    cfg = settings()["oidc"]
    if not cfg.get("enabled") or not cfg.get("issuer") or not cfg.get("client_id"):
        return None
    key = json.dumps([cfg["issuer"], cfg["client_id"], cfg["client_secret"], cfg["scopes"], cfg.get("verify_tls", True)])
    if _oidc["key"] != key:
        from authlib.integrations.starlette_client import OAuth
        oauth = OAuth()
        verify = ssl.create_default_context(cafile=CA_FILE) if cfg.get("verify_tls", True) and os.path.isfile(CA_FILE) else cfg.get("verify_tls", True)
        oauth.register("sso", server_metadata_url=cfg["issuer"].rstrip("/") + "/.well-known/openid-configuration",
                       client_id=cfg["client_id"], client_secret=cfg["client_secret"] or None,
                       client_kwargs={"scope": cfg["scopes"] or "openid profile email", "verify": verify,
                                      "code_challenge_method": "S256"})
        _oidc.update(key=key, client=oauth.sso)
    return _oidc["client"]


def _claim(d, path):
    """'groups' oder verschachtelt 'realm_access.roles'"""
    cur = d
    for p in (path or "").split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def oidc_user(claims):
    cfg = settings()["oidc"]
    username = str(_claim(claims, cfg["username_claim"]) or "").strip()
    if not str(claims.get("sub") or "").strip():
        return None, t("SSO-Anmeldung fehlgeschlagen – bitte noch einmal versuchen.")
    if "email" in (cfg["username_claim"] or "").lower() and str(claims.get("email_verified", "")).lower() in ("false", "0"):
        # Benutzername kommt aus der E-Mail: nur bestaetigte Adressen (sonst koennte sich jemand eine fremde Adresse eintragen)
        return None, t("Deine E-Mail-Adresse ist beim Anmeldedienst nicht bestätigt.")
    if cfg["username_claim"] == "email" and "@" in username:
        username = username.split("@", 1)[0] if not USERNAME_RE.fullmatch(username.lower()) else username
    raw = _claim(claims, cfg["groups_claim"]) if cfg.get("groups_claim") else []
    groups = [str(g).strip("/").split("/")[-1] for g in (raw if isinstance(raw, list) else [raw] if raw else [])]
    lc = settings()["ldap"]
    if lc.get("enabled") and lc.get("sso_groups") and norm_username(username):
        try:
            lg = ldap_user_groups(norm_username(username))
        except Exception as ex:  # noqa: BLE001 - Verzeichnis gerade weg: bisherige Gruppen behalten
            print(f"sso-ldap-gruppen {username}: {type(ex).__name__}: {ex}", flush=True)
            u = get_user(norm_username(username))
            lg = groups_of(u["id"])[1] if u else set()
        if lg is None:
            return None, t("Dein Konto wurde im Verzeichnis nicht gefunden oder ist gesperrt.")
        groups = list(set(groups) | set(lg))
    return _ext_user(username, "oidc", str(claims.get("sub") or ""), str(_claim(claims, cfg["name_claim"]) or ""),
                     str(_claim(claims, cfg["email_claim"]) or ""), groups, cfg)


# --- Erst-Admin ----------------------------------------------------------------------------------
def bootstrap_admin():
    """Beim Start: gibt es keinen Admin, wird einer angelegt (ADMIN_USERNAME/ADMIN_PASSWORD, sonst Zufallspasswort im Log)."""
    with _db() as c:
        if c.execute("SELECT 1 FROM users WHERE is_admin=1 AND disabled=0 LIMIT 1").fetchone():
            return
    name = norm_username(E.get("ADMIN_USERNAME") or "admin") or "admin"
    pw = E.get("ADMIN_PASSWORD") or ""
    generated = False
    if password_problem(pw):
        pw, generated = secrets.token_urlsafe(14), True
    u = get_user(name)
    if u:
        with _db() as c, c:
            c.execute("UPDATE users SET is_admin=1, disabled=0 WHERE id=?", (u["id"],))
        if u["source"] == "local":
            set_password(name, pw)
    else:
        create_user(name, pw, "Administrator", "", is_admin=True)
    if generated:
        _pw_file(name, pw)
    msg = f"Admin-Konto „{name}“ " + (f"mit Passwort: {pw}  (auch in {PW_FILE}; bitte im Admin-Bereich ändern)"
                                       if generated else "angelegt (Passwort aus ADMIN_PASSWORD)")
    print("saga: " + msg, flush=True)


PW_FILE = os.path.join(E.get("STATE_DIR", "/var/lib/saga"), "admin-passwort.txt")


def _pw_file(name, pw):
    """Erzeugtes Admin-Passwort zusaetzlich im Daten-Volume ablegen (Container-Logs gehen beim Neuaufbau verloren)."""
    try:
        fd = os.open(PW_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"Benutzer: {name}\nPasswort: {pw}\n(Datei loeschen, sobald das Passwort geaendert ist)\n")
    except OSError as ex:
        print(f"saga: {PW_FILE} nicht schreibbar: {ex}", flush=True)


def drop_pw_file(name):
    """Nach einer Passwortaenderung des Kontos die Datei mit dem erzeugten Passwort entfernen."""
    try:
        with open(PW_FILE) as f:
            if f.readline().strip() == f"Benutzer: {name}":
                os.remove(PW_FILE)
    except OSError:
        pass


def reset_admin(username="admin"):
    """Notfall (Kommandozeile): Admin-Konto mit neuem Zufallspasswort - legt es an, entsperrt es, macht es zum Admin."""
    name = norm_username(username)
    if not name:
        raise SystemExit("Ungueltiger Benutzername")
    pw = secrets.token_urlsafe(14)
    u = get_user(name)
    if u and u["source"] != "local":
        raise SystemExit(f"„{name}“ ist ein {u['source']}-Konto – Passwort dort aendern oder einen anderen Namen nehmen.")
    if not u:
        create_user(name, pw, "Administrator", "", is_admin=True)
    else:
        with _db() as c, c:
            c.execute("UPDATE users SET is_admin=1, disabled=0 WHERE id=?", (u["id"],))
        set_password(name, pw)
    clear_fails(name)
    _pw_file(name, pw)
    return name, pw
