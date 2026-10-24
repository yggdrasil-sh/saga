"""Profilbilder (2.1): eigenes Bild, Gravatar (2.4: nur nach Einwilligung), optional aus dem Anmeldedienst (OIDC-Claim "picture",
LDAP jpegPhoto/thumbnailPhoto) und Personen anderer Saga-Instanzen ueber die Foederation. Ohne Bild zeigt der Client
wie bisher die farbigen Initialen.

Datenschutz und Sicherheit:
- Browser und Apps fragen NIE bei Gravatar, beim Anmeldedienst oder bei fremden Instanzen nach, sondern immer hier
  (/api/avatar/<schluessel>). Der Server holt ein Bild einmal ab (saga.netguard: DNS einmal aufgeloest, interne Adressen
  nur mit Freigabe im Admin-Bereich, Zeit- und Groessengrenze) und speichert es einen Tag zwischen - auch "kein Bild"
  (Gravatar mit d=404), damit nicht bei jedem Aufruf nachgefragt wird.
- Jedes Bild (Upload, Gravatar, SSO, fremde Instanz) wird neu kodiert: in einem eigenen, speicherbegrenzten Prozess
  (Pillow), quadratisch zugeschnitten, 64/128/256 px, WebP und PNG/JPEG (Apps ohne WebP), ohne EXIF/Metadaten.
- Die Adresse enthaelt einen nicht erratbaren Schluessel (HMAC mit SECRET_KEY): Konten lassen sich nicht durchzaehlen,
  ein Profilbild sieht nur, wer die Adresse von Saga bekommen hat. Abruf nur angemeldet (Sitzung oder
  Geraete-Schluessel); ohne Anmeldung nur, wenn die Person es erlaubt (veroeffentlichte Seiten, Standard aus).
- Konto loeschen: eigene Bilder und alle Zwischenspeicher weg (delete_account).

Reihenfolge: eigenes Bild > Bild aus dem Anmeldedienst (wenn der Admin es eingeschaltet hat) > Gravatar (Admin-Schalter
erlaubt es, die Person schaltet es selbst ein - 2.4: ab Werk aus, weil dabei ein Hash der E-Mail-Adresse an Gravatar geht;
nur mit E-Mail-Adresse) > Initialen."""
import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import bus, netguard
from saga.core import E, SECRET, _api_guard, _bad, _body, _session_user, _tok_hash, t, user_info
from version import VERSION

router = APIRouter()

SIZES = (64, 128, 256)
TTL = 86400                                   # Gravatar/SSO/Foederation: einen Tag zwischenspeichern (auch "kein Bild")
RETRY = 3600                                  # Netzfehler: nach einer Stunde erneut versuchen
UPLOAD_MAX = max(1, int(E.get("AVATAR_MAX_MB", "5"))) * 1024 * 1024
FETCH_MAX = 2 * 1024 * 1024                   # abgeholte Bilder (Gravatar, SSO, fremde Instanz)
FETCH_TIMEOUT = float(E.get("AVATAR_FETCH_TIMEOUT", "5"))
GRAVATAR_URL = (E.get("GRAVATAR_URL") or "https://gravatar.com/avatar/").rstrip("/") + "/"   # feste Adresse (Tests: Attrappe)
UPLOADS_PER_HOUR = 30
# Von saga.federation gesetzt: fetch(prinzipal) -> bytes (Bild) | None (kein Bild); Ausnahme = gerade nicht erreichbar
REMOTE_FETCH = None

A_KEYS = ("ver", "upload", "gravatar", "public", "g_hash", "g_checked", "g_found", "sso_kind", "sso_ref", "sso_checked", "sso_found")
A_COLS = ", ".join(A_KEYS)
_KEY = re.compile(r"u([1-9][0-9]{0,17})\.([A-Za-z0-9_-]{16})|r([A-Za-z0-9_-]{6,400})\.([A-Za-z0-9_-]{16})")
_PRINCIPAL = re.compile(r"@(?:[a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260}")     # 2.3: auch pseudonyme Handles
_MAGIC = [(b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF87a", "gif"), (b"GIF89a", "gif")]
_HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1")


class FetchError(Exception):
    """Bild gerade nicht abholbar (Netz, Zeit, Groesse, gesperrte Adresse) - spaeter erneut versuchen."""


def conf():
    a = auth.settings().get("avatar") or {}
    return {"gravatar": bool(a.get("gravatar", True)), "oidc": bool(a.get("oidc_picture")), "ldap": bool(a.get("ldap_photo"))}


def fed_on():
    return bool(auth.settings().get("fed_enabled", False))


def is_image(head):
    for sig, kind in _MAGIC:
        if head.startswith(sig):
            return kind
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:     # HEIC/HEIF (iPhone-Fotos), Pillow mit pillow-heif
        return "heic"
    return None


def gravatar_hash(email):
    """SHA-256 der E-Mail (klein, ohne Leerzeichen) - so wie Gravatar es erwartet; None ohne Adresse."""
    e = (email or "").strip().lower()
    return hashlib.sha256(e.encode()).hexdigest() if "@" in e else None


# --- Schluessel in der Adresse ----------------------------------------------------------------------------------
def _mac(subject):
    d = hmac.new(SECRET.encode(), ("saga-avatar|" + subject).encode(), hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(d).decode()


def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def key_local(user_id, username):
    return f"u{user_id}.{_mac(f'u{user_id}|{username}')}"


def key_remote(principal):
    return f"r{_b64(principal)}.{_mac('r|' + principal)}"


def _url(key, ver):
    return f"/api/avatar/{key}?v={int(ver or 0)}"


def _parse(key):
    """-> ('u', id, mac) | ('r', prinzipal) | None. Der MAC wird hier fuer fremde Personen schon geprueft."""
    m = _KEY.fullmatch(key or "")
    if not m:
        return None
    if m.group(1):
        return "u", int(m.group(1)), m.group(2)
    try:
        p = base64.urlsafe_b64decode(m.group(3) + "=" * (-len(m.group(3)) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not _PRINCIPAL.fullmatch(p) or not hmac.compare_digest(_mac("r|" + p), m.group(4)):
        return None
    return "r", p


# --- Zustand je Person ----------------------------------------------------------------------------------------------
def _row(c, uid):
    r = c.execute(f"SELECT {A_COLS} FROM avatar WHERE uid=?", (uid,)).fetchone()
    return dict(zip(A_KEYS, r)) if r else None


def _ensure(c, uid):
    c.execute("INSERT INTO avatar (uid, updated) VALUES (?, ?) ON CONFLICT (uid) DO NOTHING", (uid, time.time()))


def announce(c, uid):
    """Live-Ereignis avatar.changed {user, avatar, path} nach dem Commit: an die Person selbst (andere Tabs/Geraete) und alle,
    mit denen sie einen Chat teilt - deren Clients tauschen das Bild aus, ohne Listen neu laden zu muessen.
    Nur lokale Konten; avatar = neue Adresse (anderes v) oder null (Initialen)."""
    if not uid or uid.startswith("@"):
        return
    uids = {uid} | {r[0] for r in c.execute(
        "SELECT DISTINCT m2.uid FROM chat_member m1 JOIN chat_member m2 ON m2.chat_id=m1.chat_id "
        "WHERE m1.uid=? AND m1.left_at IS NULL AND m2.left_at IS NULL LIMIT 5000", (uid,))}
    r = c.execute("SELECT id FROM users WHERE username=?", (uid,)).fetchone()
    # path: Adresse ohne Version - bleibt je Person gleich, damit Clients auch bei avatar=null ihre Bilder finden
    data = {"user": uid, "avatar": url_of(c, uid), "path": "/api/avatar/" + key_local(r[0], uid) if r else None}
    bus.emit_after(c, "direct", {"uids": sorted(uids), "typ": "avatar.changed", "data": data})


def _fresh(ts, now):
    return bool(ts) and now - ts < TTL


def _plan(row, email, cf, remote=False, fed=False):
    """Moegliche Quellen in Reihenfolge -> [(quelle, 'have' | 'fetch')]. Leer = kein Bild (Initialen).
    'fetch' = (noch) nicht oder nicht mehr frisch abgeholt: der Abruf holt es zuerst."""
    now, row = time.time(), row or {}
    out = []
    if remote:
        if fed:
            if not _fresh(row.get("g_checked"), now):
                out.append(("fed", "fetch"))
            elif row.get("g_found"):
                out.append(("fed", "have"))
        return out
    if row.get("upload"):
        out.append(("upload", "have"))
    kind = row.get("sso_kind")
    if kind and cf.get(kind):
        if kind == "ldap":
            if row.get("sso_found"):
                out.append(("sso", "have"))
        elif row.get("sso_ref"):
            if not _fresh(row.get("sso_checked"), now):
                out.append(("sso", "fetch"))
            elif row.get("sso_found"):
                out.append(("sso", "have"))
    h = gravatar_hash(email)
    if cf["gravatar"] and h and row.get("gravatar", 0):
        if row.get("g_hash") != h or not _fresh(row.get("g_checked"), now):
            out.append(("gravatar", "fetch"))
        elif row.get("g_found"):
            out.append(("gravatar", "have"))
    return out


def urls(c, uids, known_only=False):
    """{benutzername oder '@name@host': adresse} fuer alle, die ein Bild haben (koennten). known_only: nur schon
    vorhandene Bilder (oeffentliche Seiten - dort kein Abruf ohne Anmeldung)."""
    uids = [u for u in dict.fromkeys(uids) if isinstance(u, str) and u]
    out = {}
    if not uids:
        return out
    cf = conf()
    loc = [u for u in uids if not u.startswith("@")]
    rem = [u for u in uids if u.startswith("@") and _PRINCIPAL.fullmatch(u)]
    cols = ", ".join("a." + k for k in A_KEYS)
    for k in range(0, len(loc), 500):
        part = loc[k:k + 500]
        for r in c.execute(f"SELECT u.username, u.id, u.email, {cols} FROM users u LEFT JOIN avatar a ON a.uid=u.username "
                           f"WHERE u.disabled=0 AND u.username IN ({','.join('?' * len(part))})", tuple(part)).fetchall():
            row = dict(zip(A_KEYS, r[3:])) if r[3] is not None else None
            plan = _plan(row, r[2], cf)
            if plan and (not known_only or any(s == "have" for _, s in plan)):
                out[r[0]] = _url(key_local(r[1], r[0]), (row or {}).get("ver"))
    if rem and fed_on() and REMOTE_FETCH is not None and not known_only:
        rows = {}
        for k in range(0, len(rem), 500):
            part = rem[k:k + 500]
            rows.update({r[0]: dict(zip(A_KEYS, r[1:])) for r in c.execute(
                f"SELECT uid, {A_COLS} FROM avatar WHERE uid IN ({','.join('?' * len(part))})", tuple(part)).fetchall()})
        for u in rem:
            if _plan(rows.get(u), None, cf, remote=True, fed=True):
                out[u] = _url(key_remote(u), (rows.get(u) or {}).get("ver"))
    return out


def url_of(c, uid, known_only=False):
    return urls(c, [uid], known_only).get(uid) if uid else None


def public_url(c, uid):
    """Fuer veroeffentlichte Seiten: Adresse nur, wenn die Person es erlaubt und schon ein Bild da ist (sonst None)."""
    row = _row(c, uid)
    if not row or not row["public"]:
        return None
    return url_of(c, uid, known_only=True)


def my_url(uid):
    """Eigenes Profilbild fuer die Kopfzeile der Seiten (None = Initialen)."""
    try:
        with _db() as c:
            return url_of(c, uid)
    except Exception:  # noqa: BLE001 - nur Anzeige
        return None


# --- Neu kodieren (eigener Prozess, wie die Vorschaubilder in saga.files) ------------------------------------------
_ENC_CODE = r"""
import base64, io, json, sys, warnings
from PIL import Image, ImageOps
warnings.simplefilter("error", Image.DecompressionBombWarning)
Image.MAX_IMAGE_PIXELS = 25_000_000
try:
    from pillow_heif import register_heif_opener      # HEIC/HEIF (iPhone); ohne das Paket nur PNG/JPEG/GIF/WebP
    register_heif_opener(thumbnails=False)
except Exception:
    pass
try:
    im = Image.open(io.BytesIO(sys.stdin.buffer.read()))
    if im.format not in ("PNG", "JPEG", "MPO", "GIF", "WEBP", "HEIF"):
        raise ValueError("format")
    im.seek(0)
    im.load()
    im = ImageOps.exif_transpose(im) or im          # Ausrichtung aus EXIF anwenden, danach gibt es kein EXIF mehr
    w, h = im.size
    if min(w, h) < 16:
        raise ValueError("small")
    alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if alpha else "RGB")
    s = min(w, h)
    x0, y0 = (w - s) // 2, (h - s) // 2
    im = im.crop((x0, y0, x0 + s, y0 + s))
    out = {"std_mime": "image/png" if alpha else "image/jpeg"}
    for n in (256, 128, 64):
        x = im.resize((n, n), Image.LANCZOS)
        x.info = {}                                   # keine Metadaten (EXIF, ICC, Text) mitnehmen
        b = io.BytesIO(); x.save(b, "WEBP", quality=86, method=4)
        out[f"{n}/webp"] = base64.b64encode(b.getvalue()).decode()
        b = io.BytesIO()
        if alpha:
            x.save(b, "PNG", optimize=True)
        else:
            x.save(b, "JPEG", quality=88, optimize=True, progressive=True)
        out[f"{n}/std"] = base64.b64encode(b.getvalue()).decode()
    print(json.dumps(out))
except Exception as ex:
    print(json.dumps({"error": type(ex).__name__}))
"""
_enc_sem = threading.BoundedSemaphore(2)


def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (500 << 20, 500 << 20))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))


def encode(data):
    """Bild -> {(groesse, 'webp'|'std'): (mime, bytes)}; None = kein brauchbares Bild."""
    if not data or len(data) > max(UPLOAD_MAX, FETCH_MAX) or not is_image(data[:16]):
        return None
    with _enc_sem:
        try:
            r = subprocess.run([sys.executable, "-c", _ENC_CODE], input=data, capture_output=True, timeout=45,
                               preexec_fn=_limits if sys.platform.startswith("linux") else None)
        except (OSError, subprocess.SubprocessError):
            return None
    try:
        d = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return None
    if r.returncode != 0 or d.get("error") or "256/webp" not in d:
        return None
    out = {}
    for n in SIZES:
        out[(n, "webp")] = ("image/webp", base64.b64decode(d[f"{n}/webp"]))
        out[(n, "std")] = (d["std_mime"], base64.b64decode(d[f"{n}/std"]))
    return out


def _etag(enc):
    return hashlib.sha256(enc[(256, "webp")][1]).hexdigest()[:20]


def _put(c, uid, src, enc):
    """Bilder einer Quelle ersetzen -> True, wenn sich etwas geaendert hat."""
    old = c.execute("SELECT etag FROM avatar_img WHERE uid=? AND src=? AND size=256 AND fmt='webp'", (uid, src)).fetchone()
    new = _etag(enc) if enc else None
    if (old[0] if old else None) == new:
        return False
    c.execute("DELETE FROM avatar_img WHERE uid=? AND src=?", (uid, src))
    for (n, fmt), (mime, data) in (enc or {}).items():
        c.execute("INSERT INTO avatar_img (uid, src, size, fmt, mime, data, etag) VALUES (?,?,?,?,?,?,?)", (uid, src, n, fmt, mime, data, new))
    return True


# --- Abholen (Gravatar, SSO-Adresse, fremde Instanz) ---------------------------------------------------------------
def _allowed_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _ip_ok(ip, host, port):
    """Oeffentliche Adressen immer; interne nur mit Freigabe im Admin-Bereich (wie Kalender/Immich/Foederation)."""
    if netguard.is_public(ip):
        return True
    if auth.settings().get("allow_private_hosts"):
        return True
    hs = _allowed_hosts()
    return f"{host}:{port}".lower() in hs or str(host).lower() in hs


def download(url):
    """-> bytes (HTTP 200) oder None (404/410: es gibt kein Bild). FetchError bei allem anderen."""
    u = urlparse(url or "")
    if u.scheme not in ("https", "http") or not u.hostname or len(url) > 2000:
        raise FetchError("Adresse")
    deadline = time.monotonic() + FETCH_TIMEOUT * 2          # Gesamtfrist (das Zeitlimit von httpx gilt je Lesevorgang)
    try:
        with netguard.guarded_client(_ip_ok, timeout=FETCH_TIMEOUT, follow_redirects=True, max_redirects=3) as cl:
            with cl.stream("GET", url, headers={"Accept": "image/*", "User-Agent": f"Saga/{VERSION}"}) as r:
                if r.status_code in (404, 410):
                    return None
                if r.status_code != 200:
                    raise FetchError(f"HTTP {r.status_code}")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > FETCH_MAX:
                        raise FetchError("zu gross")
                    if time.monotonic() > deadline:
                        raise FetchError("Zeitueberschreitung")
                return bytes(buf)
    except httpx.HTTPError as ex:
        blocked = isinstance(ex.__cause__, netguard.Blocked) or "nicht freigegeben" in str(ex)
        raise FetchError("Adresse nicht erlaubt" if blocked else type(ex).__name__)
    except netguard.Blocked:
        raise FetchError("Adresse nicht erlaubt")


_locks, _locks_guard = {}, threading.Lock()


def _lock(uid, src):
    with _locks_guard:
        if len(_locks) > 2000:
            _locks.clear()
        return _locks.setdefault((uid, src), threading.Lock())


def _fetch(uid, src, email):
    """Eine Quelle (neu) abholen und speichern. Mehrere gleichzeitige Abrufe derselben Person: nur einer holt."""
    with _lock(uid, src):
        with _db() as c:
            row = _row(c, uid) or {}
        now = time.time()
        if src == "gravatar":
            h = gravatar_hash(email)
            if not h or (row.get("g_hash") == h and _fresh(row.get("g_checked"), now)):
                return
            target = GRAVATAR_URL + h + "?s=256&d=404"
        elif src == "sso":
            if row.get("sso_kind") != "oidc" or not row.get("sso_ref") or _fresh(row.get("sso_checked"), now):
                return
            target, h = row["sso_ref"], None
        else:                                                  # 'fed'
            if _fresh(row.get("g_checked"), now) or REMOTE_FETCH is None:
                return
            target, h = None, None
        try:
            data = REMOTE_FETCH(uid) if src == "fed" else download(target)
            enc = encode(data) if data else None
            err = None
        except Exception as ex:  # noqa: BLE001 - FetchError, Foederation nicht erreichbar: spaeter erneut
            enc, err = None, ex
        with _db() as c, c:
            _ensure(c, uid)
            if err is not None:
                print(f"avatar: {src} fuer {uid if src == 'fed' else 'Konto'} nicht abholbar ({type(err).__name__}: {str(err)[:80]})", flush=True)
                retry = now - TTL + RETRY                      # gilt eine Stunde als frisch, dann neuer Versuch
                if src == "sso":                               # bisheriges Bild (falls da) bleibt
                    c.execute("UPDATE avatar SET sso_checked=?, updated=? WHERE uid=?", (retry, now, uid))
                elif src == "fed":
                    c.execute("UPDATE avatar SET g_checked=?, updated=? WHERE uid=?", (retry, now, uid))
                else:                                          # Gravatar: altes Bild nur, wenn es zur selben Adresse gehoert
                    same = row.get("g_hash") == h
                    changed = False if same else _put(c, uid, "gravatar", None)
                    c.execute("UPDATE avatar SET g_hash=?, g_checked=?, g_found=?, ver=ver+?, updated=? WHERE uid=?",
                              (h, retry, (row.get("g_found") or 0) if same else 0, 1 if changed else 0, now, uid))
                return
            changed = _put(c, uid, src, enc)
            if src == "sso":
                c.execute("UPDATE avatar SET sso_checked=?, sso_found=?, ver=ver+?, updated=? WHERE uid=?",
                          (now, 1 if enc else 0, 1 if changed else 0, now, uid))
            else:
                c.execute("UPDATE avatar SET g_hash=?, g_checked=?, g_found=?, ver=ver+?, updated=? WHERE uid=?",
                          (h, now, 1 if enc else 0, 1 if changed else 0, now, uid))
            if changed:
                announce(c, uid)


def resolve(uid, email, remote=False):
    """Welche Quelle gilt jetzt? Holt fehlende/veraltete Bilder zuerst ab. -> 'upload'|'sso'|'gravatar'|'fed'|None"""
    for _ in range(4):
        with _db() as c:
            row = _row(c, uid)
        plan = _plan(row, email, conf(), remote, fed_on())
        if not plan:
            return None
        src, st = plan[0]
        if st == "have":
            return src
        _fetch(uid, src, email)
        with _db() as c:
            again = _plan(_row(c, uid), email, conf(), remote, fed_on())
        if again and again[0] == (src, "fetch"):             # trotz Abruf nicht frisch (z. B. Sperre): naechste Quelle
            rest = [x for x in again if x[0] != src]
            return next((s for s, st2 in rest if st2 == "have"), None)
    return None


def image(uid, src, size, fmt):
    with _db() as c:
        return c.execute("SELECT mime, data, etag FROM avatar_img WHERE uid=? AND src=? AND size=? AND fmt=?", (uid, src, size, fmt)).fetchone()


def local_image(uid, size=256, fmt="webp"):
    """Bild einer lokalen Person (fuer andere Instanzen) -> (mime, bytes) oder None."""
    with _db() as c:
        r = c.execute("SELECT email FROM users WHERE username=? AND disabled=0", (uid,)).fetchone()
    if not r:
        return None
    src = resolve(uid, r[0])
    got = image(uid, src, size, fmt) if src else None
    return (got[0], bytes(got[1])) if got else None


# --- Anmeldedienst (von auth bei der Anmeldung aufgerufen) --------------------------------------------------------
def _clean_url(v):
    if not isinstance(v, str) or len(v) > 2000:
        return None
    u = urlparse(v.strip())
    return v.strip() if u.scheme in ("http", "https") and u.hostname else None


def sso_photo(username, kind, value):
    """OIDC: value = Adresse aus dem Claim "picture" (abgeholt wird erst beim ersten Abruf); LDAP: value = Bilddaten.
    Nur bei eingeschaltetem Admin-Schalter; ohne Wert wird ein frueheres Bild dieser Art entfernt."""
    if not conf().get(kind):
        return
    now = time.time()
    with _db() as c:
        row = _row(c, username) or {}
    if kind == "oidc":
        url = _clean_url(value)
        if (row.get("sso_kind") == "oidc" and row.get("sso_ref") == url) or (not url and row.get("sso_kind") != "oidc"):
            return
        with _db() as c, c:
            _ensure(c, username)
            _put(c, username, "sso", None)
            c.execute("UPDATE avatar SET sso_kind=?, sso_ref=?, sso_checked=NULL, sso_found=0, ver=ver+1, updated=? WHERE uid=?",
                      ("oidc" if url else None, url, now, username))
            announce(c, username)
        return
    data = value if isinstance(value, (bytes, bytearray)) and 0 < len(value) <= FETCH_MAX else None
    h = hashlib.sha256(data).hexdigest() if data else None
    if (row.get("sso_kind") == "ldap" and row.get("sso_ref") == h) or (not data and row.get("sso_kind") != "ldap"):
        return
    enc = encode(bytes(data)) if data else None
    with _db() as c, c:
        _ensure(c, username)
        _put(c, username, "sso", enc)
        c.execute("UPDATE avatar SET sso_kind=?, sso_ref=?, sso_checked=?, sso_found=?, ver=ver+1, updated=? WHERE uid=?",
                  ("ldap" if data else None, h, now, 1 if enc else 0, now, username))
        announce(c, username)


auth.photo_hook = sso_photo


# --- Aufraeumen -------------------------------------------------------------------------------------------------
def delete_account(c, uid):
    """Konto geloescht: eigenes Bild, Gravatar-/SSO-Zwischenspeicher und Einstellungen weg."""
    c.execute("DELETE FROM avatar_img WHERE uid=?", (uid,))
    return c.execute("DELETE FROM avatar WHERE uid=?", (uid,)).rowcount


def forget_remote(c, principal=None, host=None):
    """Zwischengespeicherte Bilder fremder Personen weg (Person geht, Verbindung getrennt)."""
    if principal:
        c.execute("DELETE FROM avatar_img WHERE uid=?", (principal,))
        c.execute("DELETE FROM avatar WHERE uid=?", (principal,))
    if host:
        tail = "@" + host
        c.execute("DELETE FROM avatar_img WHERE uid LIKE '@%' AND right(uid, ?)=?", (len(tail), tail))
        c.execute("DELETE FROM avatar WHERE uid LIKE '@%' AND right(uid, ?)=?", (len(tail), tail))


# --- Ausliefern ----------------------------------------------------------------------------------------------------
_IMG_H = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
          "Referrer-Policy": "no-referrer", "Vary": "Accept"}


def _nf():
    return Response(b"not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"})


def _unauth():
    """Nicht angemeldet (oder Sitzung abgelaufen): Apps erneuern die Sitzung und fragen noch einmal"""
    return Response(b"unauthorized", status_code=401, media_type="text/plain", headers={"Cache-Control": "no-store"})


def _viewer(request):
    """Angemeldet? Sitzung (Web, Apps nach /app/resume) oder Geraete-Schluessel 'Authorization: Bearer hkd_...'."""
    hdr = request.headers.get("authorization", "")
    if hdr.startswith("Bearer hkd_") and len(hdr) <= 130:
        with _db() as c:
            r = c.execute("SELECT uid FROM device_login WHERE hash=?", (_tok_hash(hdr[7:].strip()),)).fetchone()
        return r[0] if r and user_info(r[0]) is not None else None
    return _session_user(request)


def _size(s):
    try:
        n = int(s or 128)
    except ValueError:
        n = 128
    return next((x for x in SIZES if x >= n), SIZES[-1])


@router.get("/api/avatar/{key}")
def api_avatar(key: str, request: Request):
    """Profilbild: ?s=64|128|256 (naechstgroessere Stufe), WebP fuer Clients mit 'Accept: image/webp' (oder ?fmt=webp),
    sonst PNG (mit Transparenz) bzw. JPEG. 404 = kein Bild (Initialen zeigen) - auch bei falschem Schluessel."""
    # Ohne (gueltige) Anmeldung: 401 statt 404, ausser das Bild ist fuer veroeffentlichte Seiten freigegeben. Bei 404
    # merkten sich die Apps "kein Bild" (Android bis zum Neustart, iOS 10 min) und erneuerten die abgelaufene Sitzung
    # nicht - Profilbilder fehlten nach dem Start oder einer Pause. 401 verraet nichts: es kommt fuer jeden nicht
    # freigegebenen, falschen oder unbekannten Schluessel gleich.
    subj = _parse(key)
    viewer = _viewer(request)
    if not subj:
        return _nf() if viewer else _unauth()
    if subj[0] == "u":
        with _db() as c:
            r = c.execute("SELECT username, email, disabled FROM users WHERE id=?", (subj[1],)).fetchone()
            if not r or r[2] or not hmac.compare_digest(key_local(subj[1], r[0]).split(".", 1)[1], subj[2]):
                return _nf() if viewer else _unauth()
            uid, email, remote = r[0], r[1], False
            if not viewer:                                   # ohne Anmeldung nur mit Erlaubnis der Person
                row = _row(c, uid)
                if not (row and row["public"]):
                    return _unauth()
    else:
        if not viewer:
            return _unauth()
        if not fed_on():
            return _nf()
        uid, email, remote = subj[1], None, True
    src = resolve(uid, email, remote) if viewer else _public_src(uid, email)
    if not src:
        return _nf()
    size = _size(request.query_params.get("s"))
    fmt = "webp" if "image/webp" in request.headers.get("accept", "") or request.query_params.get("fmt") == "webp" else "std"
    got = image(uid, src, size, fmt)
    if not got:
        return _nf()
    with _db() as c:
        ver = (_row(c, uid) or {}).get("ver") or 0
    etag = f'"{got[2]}-{size}{fmt[0]}"'
    v = request.query_params.get("v")
    h = dict(_IMG_H, ETag=etag, **{"Cache-Control": ("private, max-age=86400" if v == str(ver) else "private, max-age=600")
                                   if viewer else "public, max-age=3600"})
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=h)
    return Response(bytes(got[1]), media_type=got[0], headers=h)


def _public_src(uid, email):
    """Ohne Anmeldung (veroeffentlichte Seiten): nur schon vorhandene Bilder, nichts abholen."""
    with _db() as c:
        row = _row(c, uid)
    return next((s for s, st in _plan(row, email, conf()) if st == "have"), None)


# --- Eigenes Konto: Einstellungen -> Konto -> Profilbild -----------------------------------------------------------
_up_hits = {}


def state(uid, resolve_now=False):
    with _db() as c:
        row = _row(c, uid) or {}
        u = c.execute("SELECT email FROM users WHERE username=?", (uid,)).fetchone()
    email = u[0] if u else ""
    cf = conf()
    src = resolve(uid, email) if resolve_now else next((s for s, _ in _plan(row, email, cf)), None)
    with _db() as c:
        link = url_of(c, uid)
    kind = row.get("sso_kind")
    return {"url": link if src else None, "source": src, "upload": bool(row.get("upload")),
            "gravatar": bool(row.get("gravatar", 0)), "gravatar_enabled": cf["gravatar"], "has_email": bool(gravatar_hash(email)),
            "public": bool(row.get("public")), "sso": kind if kind and cf.get(kind) else None, "max_mb": UPLOAD_MAX // 1048576}


def _guard(request):
    uid = _api_guard(request)
    return uid, (None if uid else _bad("unauthorized", 401))


@router.get("/api/account/avatar")
def api_my_avatar(request: Request):
    uid, err = _guard(request)
    if err:
        return err
    return JSONResponse(state(uid, resolve_now=True), headers={"Cache-Control": "no-store"})


def _store_upload(uid, enc):
    with _db() as c, c:
        _ensure(c, uid)
        changed = _put(c, uid, "upload", enc) or not (_row(c, uid) or {}).get("upload")
        # dasselbe Bild noch einmal: Version (Cache-Buster) bleibt, Clients laden nichts neu
        c.execute("UPDATE avatar SET upload=1, ver=ver+?, updated=? WHERE uid=?", (1 if changed else 0, time.time(), uid))
        if changed:
            announce(c, uid)


@router.put("/api/account/avatar")
async def api_my_avatar_put(request: Request):
    """Eigenes Bild hochladen: multipart (Feld 'image') oder der Rohinhalt mit Content-Type image/*. Im Browser schon
    quadratisch zugeschnitten; der Server schneidet sicherheitshalber noch einmal mittig zu und kodiert neu."""
    uid, err = _guard(request)
    if err:
        return err
    now = time.time()
    hits = [x for x in _up_hits.get(uid, []) if now - x < 3600]
    if len(hits) >= UPLOADS_PER_HOUR:
        return _bad(t("Zu viele Versuche – bitte später erneut."), 429)
    _up_hits[uid] = hits + [now]
    if len(_up_hits) > 5000:
        _up_hits.clear()
    too_big = _bad(t("Das Bild ist zu groß (höchstens {n} MB).", n=UPLOAD_MAX // 1048576), 413)
    try:
        if int(request.headers.get("content-length") or 0) > UPLOAD_MAX + 64 * 1024:
            return too_big
    except ValueError:
        return _bad(t("ungültige Anfrage"))
    ct = request.headers.get("content-type", "").lower()
    if ct.startswith("multipart/form-data"):
        form = await request.form()
        up = form.get("image") or form.get("file")
        if up is None or not hasattr(up, "read"):
            return _bad(t("keine Datei"))
        data = await up.read(UPLOAD_MAX + 1)
    elif ct.startswith("image/"):
        data = await request.body()
    else:
        return _bad(t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP, HEIC)"), 415)
    if len(data) > UPLOAD_MAX:
        return too_big
    if not data or not is_image(data[:16]):
        return _bad(t("kein unterstütztes Bild (PNG, JPEG, GIF, WebP, HEIC)"), 415)
    enc = await run_in_threadpool(encode, data)
    if not enc:
        return _bad(t("Das Bild ließ sich nicht lesen (beschädigt oder kleiner als 16 × 16 Pixel)."), 415)
    await run_in_threadpool(_store_upload, uid, enc)
    return JSONResponse(await run_in_threadpool(state, uid), headers={"Cache-Control": "no-store"})


@router.delete("/api/account/avatar")
def api_my_avatar_delete(request: Request):
    """Eigenes Bild entfernen (danach gilt SSO-Bild, Gravatar bzw. Initialen)."""
    uid, err = _guard(request)
    if err:
        return err
    with _db() as c, c:
        _ensure(c, uid)
        had = bool((_row(c, uid) or {}).get("upload"))
        _put(c, uid, "upload", None)
        c.execute("UPDATE avatar SET upload=0, ver=ver+?, updated=? WHERE uid=?", (1 if had else 0, time.time(), uid))
        if had:
            announce(c, uid)
    return JSONResponse(state(uid, resolve_now=True), headers={"Cache-Control": "no-store"})


@router.patch("/api/account/avatar")
async def api_my_avatar_patch(request: Request):
    """{gravatar: bool (Gravatar fuer mich verwenden), public: bool (auch auf veroeffentlichten Seiten zeigen)}"""
    uid, err = _guard(request)
    if err:
        return err
    b = await _body(request)
    if b is None or any(k in b and not isinstance(b[k], bool) for k in ("gravatar", "public")):
        return _bad(t("ungültige Angabe"))

    def save():
        with _db() as c, c:
            _ensure(c, uid)
            if "gravatar" in b:
                c.execute("UPDATE avatar SET gravatar=? WHERE uid=?", (1 if b["gravatar"] else 0, uid))
                if not b["gravatar"]:                        # abgeschaltet: Zwischenspeicher sofort weg
                    _put(c, uid, "gravatar", None)
                    c.execute("UPDATE avatar SET g_hash=NULL, g_checked=NULL, g_found=0 WHERE uid=?", (uid,))
            if "public" in b:                                # aendert das Bild nicht - Adresse (Version) bleibt
                c.execute("UPDATE avatar SET public=? WHERE uid=?", (1 if b["public"] else 0, uid))
            c.execute("UPDATE avatar SET ver=ver+?, updated=? WHERE uid=?", (1 if "gravatar" in b else 0, time.time(), uid))
            if "gravatar" in b:
                announce(c, uid)
        return state(uid, resolve_now=True)
    return JSONResponse(await run_in_threadpool(save), headers={"Cache-Control": "no-store"})
