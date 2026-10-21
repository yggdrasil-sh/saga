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
import ipaddress
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
    "nc_files": True,                      # Nextcloud-Dateien in Seiten (2.3, saga/nextcloud.py): eigene Nextcloud verknuepfen
    "default_lang": "",                    # Standardsprache (de/en/es), leer = Browsersprache
    "publish_enabled": True,               # Benutzer duerfen eigene Seiten veroeffentlichen (/@name/<slug>)
    "public_home": 0,                      # veroeffentlichte Seite (note_id) als Startseite "/" fuer Besucher, 0 = keine
    "share_enabled": True,                 # Seiten mit anderen Konten und Gruppen teilen (1.3)
    "fed_enabled": False,                  # Foederation: mit verbundenen Saga-Instanzen teilen (2.0, saga/federation.py)
    "share_group_scope": False,            # nur mit Konten teilen, mit denen man eine Gruppe gemeinsam hat
    "search_username": True,               # 2.4: Personensuche/Eingaben auch per genauem Anmeldenamen (nie Praefix)
    "chat_enabled": True,                  # Chats mit Ende-zu-Ende-Verschluesselung (1.5)
    "chat_retention_max_days": 0,          # Chat-Nachrichten spaetestens nach so vielen Tagen loeschen (0 = unbegrenzt)
    "chat_v3": False,                      # 2.5 (in Arbeit): neue lokale Chats in Stufe 3 (Handles statt Anmeldenamen, saga.chat.make_v3)
    "chat_v3_days": 90,                    # 2.5: bestehende Chats spaetestens so viele Tage danach umstellen (saga.chat.convert_v3)
    "chat_quota_mb": 0,                    # 2.4: Speicher fuer Chat-Anhaenge je Konto in MB (0 = CHAT_QUOTA_MB, Standard 4096)
    "chat_video_max_mb": 0,                # 2.4: groesstes Video im Chat in MB (0 = VIDEO_MAX_MB, Standard 500)
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
           "quota": 50, "context": 8000, "history": True, "pinned": [], "chat": False},
    # Persona der KI (2.1): Name (leer = "Saga KI"), Tonfall als Stil-Zusatz im System-Prompt; das Bild liegt in avatar_img
    "ai_persona": {"name": "", "tone": ""},
    # Zweiter Faktor (2.1, saga/mfa.py): require = none | admins | all (wer verlangt wird und keinen hat, wird nach der
    # Anmeldung zur Einrichtung gefuehrt); oidc_mfa = SSO-Anmeldung nur mit Mehr-Faktor beim Anbieter (amr/acr),
    # oidc_acr = zusaetzlich akzeptierte acr-Werte (Komma-getrennt, werden auch als acr_values angefragt)
    "mfa": {"require": "none", "oidc_mfa": False, "oidc_acr": ""},
    # Profilbilder (2.1, saga/avatars.py): Gravatar (vom Server abgeholt und zwischengespeichert, je Person abschaltbar),
    # Bild aus dem Anmeldedienst (OIDC-Claim "picture", LDAP jpegPhoto/thumbnailPhoto)
    "avatar": {"gravatar": True, "oidc_picture": False, "ldap_photo": False},
    # Anrufe (2.1, saga/calls.py): 1:1 aus dem Direkt-Chat, WebRTC, Signalisierung ueber den Live-Kanal (kein eigener Port).
    # stun: Leerzeichen-/Komma-getrennte stun:-Adressen (leer = nur Direktverbindung im selben Netz); turn_urls: turn:/turns:-
    # Adressen eines eigenen TURN-Servers (z. B. coturn hinter dem Reverse Proxy auf 443); turn_mode rest = zeitlich begrenzte
    # Zugangsdaten aus turn_secret (coturn use-auth-secret), static = turn_user/turn_password; relay_only = nur ueber TURN
    # (IP-Adressen bleiben vor dem Gegenueber verborgen); ring_s = Klingeldauer in Sekunden
    "calls": {"enabled": True, "stun": "stun:stun.nextcloud.com:443", "turn_urls": "", "turn_mode": "rest", "turn_secret": "",
              "turn_user": "", "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45},
    # Verschluesselte Backups auf ein WebDAV-Ziel (2.3, saga/backups.py): mode nextcloud|webdav, server (Nextcloud) bzw.
    # url (WebDAV-Ordner), user/password (Nextcloud: Anmeldename + App-Passwort aus dem Login Flow v2, flow=True ->
    # beim Trennen widerrufen), dav_user = Nextcloud-Benutzer-ID im WebDAV-Pfad, folder = Ordner fuer die Laeufe,
    # recipients = oeffentliche age-Schluessel, secrets = SECRET_KEY/CAL_SECRET_KEY mitsichern, enabled = Zeitplan
    # (taeglich um time, Zeitzone TIMEZONE; since = eingeschaltet am), keep_* = Aufbewahrung (GFS), webhook = optionale URL.
    # mode s3: s3_endpoint, s3_region, s3_bucket, s3_prefix, s3_key/s3_secret (verschluesselt), s3_style auto|path|virtual;
    # mode folder: path (Ordner im Container unterhalb von SAGA_BACKUP_DIRS)
    "backup": {"mode": "", "server": "", "url": "", "user": "", "password": "", "flow": False, "dav_user": "",
               "folder": "Saga-Backups", "recipients": [], "secrets": True, "enabled": False, "time": "03:20", "since": 0,
               "keep_d": 7, "keep_w": 4, "keep_m": 6, "webhook": "", "s3_endpoint": "", "s3_region": "", "s3_bucket": "",
               "s3_prefix": "", "s3_key": "", "s3_secret": "", "s3_style": "auto", "path": ""},
    # Persoenliche Backups je Konto (2.3, saga/userbackup.py): enabled = erlaubt, max_mb = Groesse je Backup, min_h = Abstand
    # zwischen "Jetzt sichern" (Stunden), max_schedule = daily|weekly (haeufigster Zeitplan), hosts = freigegebene interne
    # Hosts (Komma-getrennt, optional :Port) - sonst nur https und oeffentliche Adressen
    "user_backup": {"enabled": True, "max_mb": 1024, "min_h": 1, "max_schedule": "daily", "hosts": ""},
    # Gjallarhorn (2.5, saga/horn.py): Saga-Signaturschluessel fuer Medien-Token (Ed25519; sign_sk = Seed, verschluesselt,
    # erzeugt beim ersten Gebrauch; sign_pk = oeffentlicher Schluessel fuer die Knoten)
    "horn": {"sign_sk": "", "sign_pk": ""},
    # Cloudflare-DNS fuer Gjallarhorn-Knoten (2.5 P7, saga/cloudflare.py): API-Token nur "Zone -> DNS -> Bearbeiten" fuer
    # EINE Zone (verschluesselt), zone_id/zone = gepruefte Zone, check = letztes Pruefergebnis (ohne Token)
    "horn_cf": {"token": "", "zone_id": "", "zone": "", "check": {}}
}
SECRET_FIELDS = {("oidc", "client_secret"), ("ldap", "bind_password"), ("apns", "p8"), ("ai", "api_key"), ("calls", "turn_secret"),
                 ("calls", "turn_password"), ("backup", "password"), ("backup", "webhook"), ("backup", "s3_key"),
                 ("backup", "s3_secret"), ("horn", "sign_sk"), ("horn_cf", "token")}
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


# --- Fehlversuche: Bremse statt Sperre (2.4) -----------------------------------------------------------
# Bis 2.3 sperrten 8 Fehlversuche (Benutzer+Adresse) bzw. 40 (Benutzer, alle Adressen) das Konto 15 Minuten - ein Fremder
# konnte so ein Konto gezielt aussperren (Testrunde 2.4). Jetzt:
#  - Bremse je Benutzer+Adresse: nach FREE_USER_IP Fehlversuchen muss diese Adresse fuer dieses Konto 2, 4, 8 ... s
#    (hoechstens DELAY_MAX) nach dem letzten Fehlversuch warten. Andere Adressen merken davon nichts.
#  - Bremse je Adresse (alle Konten): ab FREE_IP Fehlversuchen ebenso (gegen Durchprobieren vieler Konten).
#  - Harte Sperre des Kontos nur bei sehr vielen Fehlversuchen (LOCK_FAILS) von vielen Adressen (LOCK_IPS) binnen
#    LOCK_WINDOW - verteiltes Raten. Sie gilt LOCK_TTL, die Person wird benachrichtigt, der Admin sieht sie in der
#    Benutzerliste und hebt sie auf ("Sperre aufheben", "Passwort setzen" oder Notfall-Reset).
# Abgewiesene Versuche waehrend einer Wartezeit zaehlen nicht (die Wartezeit waechst nicht endlos) und pruefen kein
# Passwort (auch kein LDAP-Bind - Verzeichnis-Sperren im AD werden so eher seltener). area="admin": eigene Zaehler fuer
# den Admin-Port. Zweiter Faktor (mfa, admin-mfa): das Passwort ist dort schon richtig - weiter feste Grenzen.
FAIL_WINDOW = 900
FREE_USER_IP, FREE_IP, DELAY_MAX = 8, 30, 900
LOCK_FAILS, LOCK_IPS, LOCK_WINDOW, LOCK_TTL = 100, 10, 3600, 3600
# Eigene Zaehler je Bereich: Admin-Port, zweiter Faktor (Hauptseite / Admin-Port). Beim zweiten Faktor ist das Passwort
# schon richtig - dort strengere Grenzen (6-stellige Codes: wenige Versuche je Viertelstunde).
AREA_PREFIX = {"admin": "adm:", "mfa": "mfa:", "admin-mfa": "amfa:"}
AREA_LIMITS = {"mfa": (5, 10, 30), "admin-mfa": (5, 10, 30)}


# Admin-Port: zusaetzlich eine feste Grenze je Konto ueber alle Adressen (wie bis 2.3): ab ADMIN_MAX_USER Fehlversuchen
# binnen FAIL_WINDOW wartet jede Adresse, bis die aeltesten aus dem Fenster fallen - gegen verteiltes Raten (viele
# Adressen, IPv6). Auf der Hauptseite uebernimmt das die harte Sperre (LOCK_FAILS/LOCK_IPS) mit Benachrichtigung.
ADMIN_MAX_USER = 40


def ip_bucket(ip):
    """Adresse fuer Zaehler und Bremsen: IPv6 auf das /64-Netz gekuerzt (ein Anschluss bekommt meist ein ganzes /64 -
    jede Adresse darin einzeln zu zaehlen hiesse praktisch keine Grenze), IPv4-mapped als IPv4; sonst unveraendert."""
    s = str(ip or "")
    if ":" not in s:
        return s
    try:
        a = ipaddress.ip_address(s.split("%")[0])
    except ValueError:
        return s
    if a.version == 6 and a.ipv4_mapped:
        return str(a.ipv4_mapped)
    return str(ipaddress.ip_network(f"{a}/64", strict=False)) if a.version == 6 else str(a)


def _fail_keys(username, ip, area=""):
    p, ip = AREA_PREFIX.get(area, ""), ip_bucket(ip)
    return p + "ui:" + username + "|" + ip, p + "u:" + username, p + "ip:" + ip


def _delay(n, free):
    return 0 if n < free else min(DELAY_MAX, 2 ** (n - free + 1))


def lock_of(username):
    """Harte Sperre -> {since, until, fails, ips} oder None (abgelaufene werden entfernt)."""
    with _db() as c:
        r = c.execute("SELECT since, until, fails, ips FROM login_lock WHERE username=?", ((username or "").lower(),)).fetchone()
    if not r:
        return None
    if r[1] <= time.time():
        with _db() as c, c:
            c.execute("DELETE FROM login_lock WHERE username=? AND until<=?", ((username or "").lower(), time.time()))
        return None
    return {"since": r[0], "until": r[1], "fails": r[2], "ips": r[3]}


def locks():
    """Alle aktiven harten Sperren (Admin-Bereich): [{username, since, until, fails, ips}]"""
    with _db() as c:
        return [{"username": r[0], "since": r[1], "until": r[2], "fails": r[3], "ips": r[4]} for r in
                c.execute("SELECT username, since, until, fails, ips FROM login_lock WHERE until>? ORDER BY since DESC", (time.time(),))]


def fail_stats(username):
    """(Fehlversuche, Adressen) der letzten LOCK_WINDOW Sekunden (Admin-Hinweis) oder None."""
    now = time.time()
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", ("u:" + username, now - LOCK_WINDOW)).fetchone()[0]
        if not n:
            return None
        ips = c.execute("SELECT COUNT(DISTINCT key) FROM login_fail WHERE instr(key, ?) = 1 AND ts>?", ("ui:" + username + "|", now - LOCK_WINDOW)).fetchone()[0]
    return n, ips


def unlock(username):
    """Harte Sperre aufheben (Admin) - auch die Zaehler des Kontos leeren."""
    clear_fails(username)
    return True


def fail_wait(username, ip, area="", lock=True):
    """Sekunden, die (Konto, Adresse) noch warten muss; 0 = Anmeldung pruefen. Bei harter Sperre: Restzeit.
    lock=False: nur die Bremse (z. B. Passwort aendern in der eigenen Sitzung - die Sperre gilt der Anmeldung)."""
    now = time.time()
    if area in AREA_LIMITS:
        return FAIL_WINDOW if _too_many_fixed(username, ip, area) else 0
    lk = lock_of(username) if area == "" and lock else None
    if lk:
        return max(1, int(lk["until"] - now))
    kui, ku, ki = _fail_keys(username, ip, area)
    with _db() as c, c:
        c.execute("DELETE FROM login_fail WHERE ts < ?", (now - FAIL_WINDOW - LOCK_WINDOW,))
        out = 0
        for k, free in ((kui, FREE_USER_IP), (ki, FREE_IP)):
            n, last = c.execute("SELECT COUNT(*), MAX(ts) FROM login_fail WHERE key=? AND ts>?", (k, now - FAIL_WINDOW)).fetchone()
            d = _delay(n, free)
            if d and last:
                out = max(out, int(last + d - now + 0.999))
        if area == "admin":                               # feste Kontogrenze am Admin-Port (alle Adressen)
            r = c.execute("SELECT ts FROM login_fail WHERE key=? AND ts>? ORDER BY ts DESC LIMIT 1 OFFSET ?",
                          (ku, now - FAIL_WINDOW, ADMIN_MAX_USER - 1)).fetchone()
            if r:                                         # erst wenn der ADMIN_MAX_USER-juengste Versuch aus dem Fenster faellt
                out = max(out, int(r[0] + FAIL_WINDOW - now + 0.999))
    return max(0, out)


def is_locked_msg(username, area=""):
    return area == "" and lock_of(username) is not None


def fail_text(wait, locked=False):
    if locked:
        return t("Für dieses Konto gab es sehr viele falsche Anmeldeversuche – es ist vorübergehend gesperrt. Die Person und die Verwaltung sind informiert; ein Admin kann die Sperre aufheben.")
    if wait < 90:
        return t("Zu viele Fehlversuche – bitte in {n} Sekunden erneut versuchen.", n=max(1, int(wait)))
    return t("Zu viele Fehlversuche – bitte in {n} Minuten erneut versuchen.", n=max(2, int(round(wait / 60))))


def _too_many_fixed(username, ip, area):
    t0 = time.time() - FAIL_WINDOW
    kui, ku, ki = _fail_keys(username, ip, area)
    lui, lu, li = AREA_LIMITS[area]
    with _db() as c, c:
        n = [c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", (k, t0)).fetchone()[0] for k in (kui, ku, ki)]
    return n[0] >= lui or n[1] >= lu or n[2] >= li


def too_many(username, ip, area=""):
    """Gerade keine Pruefung erlaubt? (Wartezeit, harte Sperre bzw. feste Grenze beim zweiten Faktor) -> Sekunden (0 = nein)."""
    return fail_wait(username, ip, area)


def note_fail(username, ip, area=""):
    now = time.time()
    with _db() as c, c:
        for k in _fail_keys(username, ip, area):
            c.execute("INSERT INTO login_fail (key, ts) VALUES (?,?)", (k, now))
        if area != "":
            return
        p = AREA_PREFIX.get(area, "")
        n = c.execute("SELECT COUNT(*) FROM login_fail WHERE key=? AND ts>?", (p + "u:" + username, now - LOCK_WINDOW)).fetchone()[0]
        if n < LOCK_FAILS:
            return
        ips = c.execute("SELECT COUNT(DISTINCT key) FROM login_fail WHERE instr(key, ?) = 1 AND ts>?",
                        (p + "ui:" + username + "|", now - LOCK_WINDOW)).fetchone()[0]
        if ips < LOCK_IPS:
            return
        had = c.execute("SELECT 1 FROM login_lock WHERE username=? AND until>?", (username, now)).fetchone()
        c.execute("INSERT INTO login_lock (username, since, until, fails, ips) VALUES (?,?,?,?,?) ON CONFLICT (username) DO UPDATE "
                  "SET until=excluded.until, fails=excluded.fails, ips=excluded.ips" + ("" if had else ", since=excluded.since"),
                  (username, now, now + LOCK_TTL, n, ips))
        if not had and c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            print(f"saga: Konto „{username}“ nach {n} Fehlversuchen von {ips} Adressen fuer {LOCK_TTL // 60} min gesperrt "
                  "(Admin: Benutzer -> Sperre aufheben)", flush=True)
            try:
                from saga import notify             # spaet: saga.notify haengt von auth ab
                notify.create(c, username, "login.locked", data={"fails": n, "ips": ips, "until": now + LOCK_TTL})
            except Exception as ex:  # noqa: BLE001 - Benachrichtigung darf die Anmeldung nicht stoeren
                print(f"saga: Benachrichtigung zur Kontosperre fehlgeschlagen: {type(ex).__name__}", flush=True)


def clear_fails(username, area=None, ip=None):
    """Nach erfolgreicher Anmeldung (bzw. Notfall-Reset/Passwort setzen/Sperre aufheben, area=None: alle Bereiche und die
    harte Sperre) die Benutzer-Zaehler leeren. ip: nur die Bremse dieser Adresse (2.4 - meldet sich die Person von
    zuhause an, bleibt eine fremde, ratende Adresse gebremst)."""
    with _db() as c, c:
        for p in (("", *AREA_PREFIX.values()) if area is None else (AREA_PREFIX.get(area, ""),)):
            if ip is None:
                c.execute("DELETE FROM login_fail WHERE key=? OR instr(key, ?) = 1", (p + "u:" + username, p + "ui:" + username + "|"))
            else:
                c.execute("DELETE FROM login_fail WHERE key=? OR key=?", (p + "u:" + username, p + "ui:" + username + "|" + ip_bucket(ip)))
        if area is None:
            c.execute("DELETE FROM login_lock WHERE username=?", (username,))


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
    clear_fails(username)                  # 2.4: neues Passwort hebt Wartezeiten und eine harte Sperre auf


def _all_groups(c, user_id):
    return {r[0] for r in c.execute(
        "SELECT g.name FROM user_group ug JOIN groups g ON g.id=ug.group_id WHERE ug.user_id=? "
        "UNION SELECT name FROM user_ext_group WHERE user_id=?", (user_id, user_id)).fetchall()}


def groups_changed(c, usernames, groups):
    """Live-Kanal: Gruppen dieser Konten haben sich geaendert (groups = betroffene Gruppennamen). Ist eine davon Ziel
    einer Freigabe, koennen geteilte Seiten sichtbar/unsichtbar werden -> shared.changed + notes.tree {full} an die
    Konten (entprellt in saga.live). Nach dem Commit; vor dem Loeschen von Freigaben aufrufen."""
    groups, usernames = sorted({g for g in groups if g}), sorted({u for u in usernames if u})
    if not groups or not usernames:
        return
    if not c.execute(f"SELECT 1 FROM note_share WHERE target_type='group' AND target IN ({','.join('?' * len(groups))}) LIMIT 1",
                     tuple(groups)).fetchone():
        return
    from saga import bus
    bus.emit_after(c, "groups.changed", {"uids": usernames, "groups": groups})


def _set_groups(user_id, fill):
    """Gruppen eines Kontos neu setzen (fill(c) schreibt) und Aenderungen dem Live-Kanal melden."""
    with _db() as c, c:
        before = _all_groups(c, user_id)
        fill(c)
        diff = before ^ _all_groups(c, user_id)
        if diff:
            r = c.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
            if r:
                groups_changed(c, [r[0]], diff)
    _ui_cache.clear()


def set_user_groups(user_id, names):
    def fill(c):
        c.execute("DELETE FROM user_group WHERE user_id=?", (user_id,))
        for g in names:
            gid = ensure_group(c, g)
            if gid:
                c.execute("INSERT OR IGNORE INTO user_group (user_id, group_id) VALUES (?,?)", (user_id, gid))
    _set_groups(user_id, fill)


def set_ext_groups(user_id, names):
    def fill(c):
        c.execute("DELETE FROM user_ext_group WHERE user_id=?", (user_id,))
        for g in {norm_group(n) for n in names}:
            if g:
                c.execute("INSERT OR IGNORE INTO user_ext_group (user_id, name) VALUES (?,?)", (user_id, g))
    _set_groups(user_id, fill)


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
    wait = too_many(u, ip, area)
    if wait:
        return None, fail_text(wait, is_locked_msg(u, area))
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
            clear_fails(u, area, ip)
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
            clear_fails(u, area, ip)
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


PHOTO_ATTRS = ("thumbnailPhoto", "jpegPhoto")         # Active Directory bzw. inetOrgPerson (FreeIPA, OpenLDAP)
PHOTO_MAX = 2 * 1024 * 1024
photo_hook = None      # saga.avatars: (benutzername, 'oidc' | 'ldap', adresse bzw. bilddaten) - Profilbild vom Anmeldedienst


def _photo(username, kind, value):
    if photo_hook is None:
        return
    try:
        photo_hook(username, kind, value)
    except Exception as ex:  # noqa: BLE001 - ein Profilbild darf die Anmeldung nie verhindern
        print(f"avatar ({kind}) {username}: {type(ex).__name__}: {ex}", flush=True)


def _ldap_photo_on():
    return bool((settings().get("avatar") or {}).get("ldap_photo"))


def _ldap_photo(entry):
    """Profilbild aus dem Verzeichnis (erstes vorhandene Attribut) -> bytes oder None."""
    for a in PHOTO_ATTRS:
        try:
            v = entry[a].value
        except (KeyError, IndexError, Exception):  # noqa: BLE001
            continue
        if isinstance(v, list):
            v = v[0] if v else None
        if isinstance(v, (bytes, bytearray)) and 0 < len(v) <= PHOTO_MAX:
            return bytes(v)
    return None


def _ldap_find(c, cfg, username):
    from ldap3 import SUBTREE
    flt = cfg["user_filter"].replace("{username}", _esc(username))
    attrs = [a for a in {cfg["attr_username"], cfg["attr_name"], cfg["attr_mail"], "memberOf"} if a]
    if _ldap_photo_on():
        attrs += list(PHOTO_ATTRS)
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
        photo = _ldap_photo(e) if _ldap_photo_on() else None
    uc = _ldap_bind(cfg, dn, password)
    ok = uc.bound
    uc.unbind()
    if not ok:
        return None, None
    user, err = _ext_user(username, "ldap", dn, name, mail, groups, cfg)
    if user and _ldap_photo_on():
        _photo(user["username"], "ldap", photo)
    return user, err


def ldap_refresh(user):
    """Gruppen eines LDAP-Kontos neu lesen. False = im Verzeichnis nicht mehr vorhanden/nicht berechtigt."""
    cfg = settings()["ldap"]
    with _service_conn(cfg) as c:
        e = _ldap_find(c, cfg, user["username"])
        if e is None:
            return False
        groups = _ldap_groups(c, cfg, e, user["username"])
        photo = _ldap_photo(e) if _ldap_photo_on() else None
    if cfg.get("allowed_group") and cfg["allowed_group"].lower() not in groups:
        return False
    set_ext_groups(user["id"], groups)
    if user["source"] == "ldap" and _ldap_photo_on():
        _photo(user["username"], "ldap", photo)          # Bild im Verzeichnis geaendert: bei der naechsten Erneuerung uebernommen
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
    user, err = _ext_user(username, "oidc", str(claims.get("sub") or ""), str(_claim(claims, cfg["name_claim"]) or ""),
                          str(_claim(claims, cfg["email_claim"]) or ""), groups, cfg)
    if user and (settings().get("avatar") or {}).get("oidc_picture"):
        _photo(user["username"], "oidc", claims.get("picture"))   # nur die Adresse merken - abgeholt wird beim ersten Abruf
    return user, err


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
    if clear_mfa(name):                       # Notfall: auch der zweite Faktor koennte verloren sein
        print(f"saga: zweiter Faktor von „{name}“ zurückgesetzt (Notfall-Reset)", flush=True)
    _pw_file(name, pw)
    return name, pw


# --- Zweiter Faktor (Daten; die Logik steht in saga/mfa.py) -----------------------------------------
MFA_TABLES = ("mfa_passkey", "mfa_totp", "mfa_recovery", "mfa_challenge")


def clear_mfa(username, by=None):
    """Alle Faktoren eines Kontos entfernen (Admin-Zuruecksetzen, Notfall-Reset). -> Anzahl entfernter Faktoren.
    by: wer zurueckgesetzt hat (Protokoll im Konto, user_kv 'mfa_reset')."""
    with _db() as c, c:
        n = c.execute("DELETE FROM mfa_passkey WHERE uid=?", (username,)).rowcount
        n += c.execute("DELETE FROM mfa_totp WHERE uid=? AND confirmed IS NOT NULL", (username,)).rowcount
        for tb in MFA_TABLES[1:]:
            c.execute(f"DELETE FROM {tb} WHERE uid=?", (username,))
        if n:
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'mfa_reset', ?)",
                      (username, json.dumps({"by": by or "cli", "at": time.time(), "n": n})))
    _ui_cache.pop(username, None)
    return n
