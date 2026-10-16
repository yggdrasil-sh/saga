"""Foederation (2.0): Seiten zwischen Saga-Instanzen teilen - eigenes, schlankes Protokoll ueber den normalen Web-Port.

Grundsaetze (Einzelheiten: docs/ANLEITUNG.md, Abschnitt Foederation):
- Vertrauen nur zwischen ausdruecklich verbundenen Instanzen (Einladungscode + beidseitige Bestaetigung mit Fingerprints).
  Jede Server-zu-Server-Anfrage und jede Antwort ist signiert (RFC 9421, Ed25519, saga.fedsig): Uhrzeit +-5 min, Nonce
  gegen Wiederholung, Content-Digest, Host des Empfaengers in der Signatur.
- Daten bleiben beim Eigentuemer: der Heimatserver der lesenden Person ruft im Namen dieser Person ab (Proxy). Die Rolle
  prueft immer der Eigentuemer-Server (sharing.Access mit dem Prinzipal '@name@host'); der Heimatserver bestaetigt Person
  und Gruppen signiert (Saga-Actor).
- Beim Heimatserver haben fremde Seiten und Kommentare negative IDs: -(id_dort * 1024 + peer_id). Eigene Routen (Konverter
  'fedid') fangen genau diese ab - Editor, Kommentare und Seitenleiste arbeiten unveraendert ueber /api/notes/{id}.
- Ausgehende Verbindungen nur ueber netguard (DNS einmal aufgeloest, IP gepinnt; interne Adressen nur, wenn der Host unter
  "Freigegebene interne Hosts" steht), nur https (http nur mit FED_ALLOW_HTTP=1 fuer Tests), ohne Weiterleitungen.
- Keine Inhalte in Logs; Pruefprotokoll (fed_log) nur mit Kennungen."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.convertors import Convertor, register_url_convertor

import auth
from db import clean_line, connect as _db
from saga import avatars, bus, comments, fedsig, i18n, netguard, notify, pages, sharing
from saga.core import BASE_URL, E, SECRET, _api_guard, _bad, _body, _session_user, parse_json, t, title
from saga.files import _thumb, _thumb_mime, _thumb_w
from saga.notes import FILE_MAX, FILES_DIR, _disposition, _iter_blocks
from saga.publish import NAME_RE, get_name
from saga.sharing import ROLES, is_remote, remote_label
from version import VERSION

router = APIRouter()

PROTOCOL = 1
ALLOW_HTTP = E.get("FED_ALLOW_HTTP", "").lower() in ("1", "true", "yes", "on")
SELF_URL = BASE_URL.rstrip("/")
SELF_HOST = fedsig.authority(urlparse(SELF_URL).netloc)
REF = 1024                                   # negative ID = -(id_dort * REF + peer_id)
PEER_MAX = REF - 1
REMOTE_ID_MAX = 2 ** 43                      # JS-sichere Zahlen nach der Umrechnung
RATE = max(60, int(E.get("FED_RATE", "600")))          # Anfragen je Minute und verbundener Instanz
TIMEOUT = float(E.get("FED_TIMEOUT", "10"))
LIST_TIMEOUT = 4.0                           # "Mit mir geteilt": je Instanz hoechstens so lange warten
RESP_MAX = 4 * 1024 * 1024
INVITE_TTL = 86400
OUTBOX_KEEP = 3 * 86400
EVENTS_MAX = 20
DISPLAY_MAX = 120
STATUSES = ("pending_in", "pending_out", "active", "disconnected")
CAPS = ["share", "read", "edit", "comment", "files", "notify"]
_FID = re.compile(r"[0-9a-f]{24}")
_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*(:[0-9]{1,5})?")


class FedError(Exception):
    """Fehler bei der Verbindung zu einer anderen Instanz. code: HTTP-Status der Gegenseite (oder None)."""

    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def enabled():
    return sharing.fed_enabled()


# =====================================================================================================
# Kleinkram: Adressen, Verweise, Protokoll, Raten
# =====================================================================================================
def norm_base(url):
    """Basis-Adresse einer Instanz -> 'https://host[:port]' (klein, ohne Pfad) oder None."""
    u = urlparse(str(url or "").strip())
    if u.scheme not in (("https", "http") if ALLOW_HTTP else ("https",)) or u.username or u.password or u.query or u.fragment:
        return None
    if u.path not in ("", "/"):
        return None
    host = fedsig.authority(u.netloc)
    if not _HOST_RE.fullmatch(host) or len(host) > 253:
        return None
    return f"{u.scheme}://{host}"


def host_of(base):
    return fedsig.authority(urlparse(base).netloc)


def ref(peer_id, rid):
    return -(int(rid) * REF + int(peer_id))


def unref(x):
    """negative ID -> (peer_id, id_dort)"""
    x = -int(x)
    return x % REF, x // REF


def _ok_rid(v):
    return isinstance(v, int) and not isinstance(v, bool) and 0 < v < REMOTE_ID_MAX


def _clean_text(s, n):
    """Text der Gegenstelle: nur Zeichenketten/Zahlen, ohne Steuer-, Bidi- und unsichtbare Zeichen (BUG-038)."""
    if isinstance(s, bool) or not isinstance(s, (str, int, float)):
        return ""
    return clean_line(s).strip()[:n]


_log_seen = {}


def log(c, event, peer=None, detail="", host=None, throttle=None):
    """Pruefprotokoll. throttle: Schluessel - hoechstens ein Eintrag je Minute (abgewiesene Signaturen)."""
    if throttle is not None:
        now = time.time()
        if now - _log_seen.get(throttle, 0) < 60:
            return
        if len(_log_seen) > 5000:
            _log_seen.clear()
        _log_seen[throttle] = now
    c.execute("INSERT INTO fed_log (peer_id, host, ts, event, detail) VALUES (?,?,?,?,?)",
              (peer["id"] if peer else None, (peer["host"] if peer else host) or "", time.time(), event, str(detail)[:300]))


class _Rate:
    def __init__(self):
        self.hits, self.lock = {}, threading.Lock()

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self.lock:
            start, n = self.hits.get(key, (now, 0))
            if now - start >= window:
                start, n = now, 0
            if len(self.hits) > 5000:
                self.hits.clear()
            self.hits[key] = (start, n + 1)
            return n + 1 <= limit


_rate = _Rate()


# =====================================================================================================
# Eigener Schluessel
# =====================================================================================================
_key = {}


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("saga-fed-key|" + SECRET).encode()).digest()))


def own_key():
    """-> (privat_roh, oeffentlich_roh). Beim ersten Aufruf erzeugt (verschluesselt gespeichert)."""
    if "k" in _key:
        return _key["k"]
    with _db() as c, c:
        r = c.execute("SELECT public_key, private_key FROM fed_self WHERE id=1").fetchone()
        if not r:
            priv, pub = fedsig.new_key()
            c.execute("INSERT OR IGNORE INTO fed_self (id, public_key, private_key, created) VALUES (1,?,?,?)",
                      (fedsig.b64u(pub), fedsig.seal_key(_fernet(), priv), time.time()))
            r = c.execute("SELECT public_key, private_key FROM fed_self WHERE id=1").fetchone()
    try:
        priv = fedsig.open_key(_fernet(), r[1])
    except InvalidToken:
        raise FedError("Instanz-Schluessel nicht lesbar (SECRET_KEY geaendert?)")
    pub = fedsig.unb64u(r[0])
    if fedsig.public_of(priv) != pub:
        raise FedError("Instanz-Schluessel beschaedigt")
    _key["k"] = (priv, pub)
    return _key["k"]


def own_fingerprint():
    return fedsig.fingerprint(own_key()[1])


def has_key(c):
    return bool(c.execute("SELECT 1 FROM fed_self WHERE id=1").fetchone())


# =====================================================================================================
# Gegenstellen (Peers)
# =====================================================================================================
P_COLS = "id, host, base_url, name, public_key, fingerprint, status, created, confirmed_at, last_seen, last_error"


def _peer(r):
    if not r:
        return None
    return dict(zip(("id", "host", "base_url", "name", "public_key", "fingerprint", "status", "created", "confirmed_at", "last_seen",
                     "last_error"), r))


def peer_by(c, col, v):
    assert col in ("id", "host", "base_url")
    return _peer(c.execute(f"SELECT {P_COLS} FROM fed_peer WHERE {col}=?", (v,)).fetchone())


def peers(c):
    return [_peer(r) for r in c.execute(f"SELECT {P_COLS} FROM fed_peer ORDER BY host").fetchall()]


def _free_id(c):
    used = {r[0] for r in c.execute("SELECT id FROM fed_peer")}
    for i in range(1, PEER_MAX + 1):
        if i not in used:
            return i
    return None


def _person_upsert(c, principal, peer_id, display):
    display = _clean_text(display, DISPLAY_MAX)
    r = c.execute("SELECT display FROM fed_person WHERE principal=?", (principal,)).fetchone()
    if r is None or r[0] != display:
        c.execute("INSERT OR REPLACE INTO fed_person (principal, peer_id, display, updated) VALUES (?,?,?,?)",
                  (principal, peer_id, display, time.time()))


def _host_tail(host):
    return "@" + host


def cleanup_peer(c, p):
    """Verbindung getrennt: Freigaben an Personen/Gruppen dieser Instanz, Eingangs-Verweise, Gelesen-Marken und offene
    Ereignisse weg. (Anzeigenamen bleiben fuer alte Kommentare.)"""
    tail = _host_tail(p["host"])
    rows = c.execute("SELECT note_id, owner_uid, target_type, target FROM note_share WHERE target_type IN ('remote','remote_group') "
                     "AND right(target, ?)=?", (len(tail), tail)).fetchall()
    for nid, owner, typ, tg in rows:
        c.execute("DELETE FROM note_share WHERE note_id=? AND target_type=? AND target=?", (nid, typ, tg))
    c.execute("DELETE FROM fed_inbound WHERE peer_id=?", (p["id"],))
    c.execute("DELETE FROM note_comment_seen WHERE uid LIKE '@%' AND right(uid, ?)=?", (len(tail), tail))
    c.execute("DELETE FROM fed_outbox WHERE peer_id=?", (p["id"],))
    avatars.forget_remote(c, host=p["host"])
    return len(rows)


# =====================================================================================================
# Ausgehende Verbindungen (netguard, signiert)
# =====================================================================================================
def _allowed_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", auth.settings().get("allowed_hosts") or "") if h.strip()}


def _ip_ok(ip, host, port):
    """Oeffentliche Adressen immer; interne nur mit Freigabe im Admin (wie Kalender/Immich)."""
    if netguard.is_public(ip):
        return True
    if auth.settings().get("allow_private_hosts"):
        return True
    hs = _allowed_hosts()
    return f"{host}:{port}".lower() in hs or str(host).lower() in hs


def _client(timeout=TIMEOUT):
    return netguard.guarded_client(_ip_ok, timeout=timeout, follow_redirects=False)


def _send(method, url, headers, body, limit, timeout=TIMEOUT):
    """-> (status, kopfzeilen (klein), bytes). Bricht bei mehr als limit Bytes ab. FedError bei Netzfehlern."""
    # Zeitlimit von httpx gilt je Lesevorgang - eine Gegenseite, die Byte fuer Byte traeufelt, haette sonst unbegrenzt
    # einen Arbeits-Thread. Daher zusaetzlich eine Gesamtfrist (Dateien bekommen mehr Zeit).
    deadline = time.monotonic() + (timeout * 3 if limit <= RESP_MAX else max(60.0, timeout * 6))
    try:
        with _client(timeout) as cl:
            with cl.stream(method, url, headers=headers, content=body) as r:
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        raise FedError("Antwort zu gross")
                    if time.monotonic() > deadline:
                        raise FedError("Zeitueberschreitung")
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, bytes(buf)
    except httpx.TimeoutException:
        raise FedError("Zeitueberschreitung")
    except (httpx.HTTPError, netguard.Blocked) as ex:
        if isinstance(ex, netguard.Blocked) or isinstance(ex.__cause__, netguard.Blocked) or "nicht freigegeben" in str(ex):
            raise FedError("Adresse nicht erlaubt (interne Adresse – im Admin unter „Freigegebene interne Hosts“ eintragen)")
        raise FedError(f"nicht erreichbar ({type(ex).__name__})")


def fetch_info(base):
    """Oeffentliche Angaben einer Instanz (/.well-known/saga) -> dict mit Schluessel und Fingerprint. FedError."""
    status, _, data = _send("GET", base + "/.well-known/saga", {"Accept": "application/json", "User-Agent": f"Saga/{VERSION}"}, None, 65536)
    if status != 200:
        raise FedError(f"keine Saga-Instanz mit Foederation (HTTP {status})", status)
    try:
        d = parse_json(data)
        pub = fedsig.load_public(d["key"]["public"])
    except (ValueError, KeyError, TypeError, fedsig.SigError):
        raise FedError("ungueltige Angaben der Instanz")
    if d.get("software") != "saga" or norm_base(d.get("base_url")) != base:
        raise FedError("Instanz meldet eine andere Adresse")
    return {"base_url": base, "host": host_of(base), "name": _clean_text(d.get("name"), 80), "version": _clean_text(d.get("version"), 40),
            "public": fedsig.b64u(pub), "fingerprint": fedsig.fingerprint(pub)}


def call(peer, method, path, query="", body=None, actor=None, limit=RESP_MAX, timeout=TIMEOUT):
    """Signierte Anfrage an eine verbundene Instanz -> (status, kopfzeilen, bytes). Die Antwort muss mit dem gepinnten
    Schluessel signiert sein (sonst FedError). query: schon kodiert ('a=1&b=2')."""
    priv, _ = own_key()
    raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode())
    hdr, nonce = fedsig.sign_request(priv, SELF_URL, method, urlparse(peer["base_url"]).netloc, path, query, raw, actor)
    # Sprache der anfragenden Person (Platzhalter wie "Seite ohne Zugriff", Fehlertexte) - unsigniert, nur Darstellung
    hdr.update({"Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"Saga/{VERSION}",
                "Accept-Language": i18n.get_lang() or "en"})
    status, rh, data = _send(method, peer["base_url"] + path + ("?" + query if query else ""), hdr, raw, limit, timeout)
    try:
        fedsig.verify_response(fedsig.unb64u(peer["public_key"]), peer["base_url"], status, rh, data, nonce)
    except fedsig.SigError as ex:
        raise FedError(f"Antwort nicht signiert/ungueltig ({ex}; HTTP {status})", status)
    return status, rh, data


def call_json(peer, method, path, query="", body=None, actor=None, timeout=TIMEOUT):
    status, _, data = call(peer, method, path, query, body, actor, timeout=timeout)
    try:
        d = parse_json(data) if data else {}         # Surrogate/NUL der Gegenseite bereinigt (BUG-008)
    except (ValueError, RecursionError):
        raise FedError("ungueltige Antwort", status)
    if not pages._json_depth_ok(d, 32):             # Antworten sind flach; tief Verschachteltes nur von boesartiger Gegenseite
        raise FedError("ungueltige Antwort", status)
    return status, d


def _touch(c, peer, err=None):
    now = time.time()
    if err is not None:
        c.execute("UPDATE fed_peer SET last_error=? WHERE id=?", (str(err)[:300], peer["id"]))
    elif not peer.get("last_seen") or now - peer["last_seen"] > 60 or peer.get("last_error"):
        c.execute("UPDATE fed_peer SET last_seen=?, last_error=NULL WHERE id=?", (now, peer["id"]))
        if peer.get("last_error"):           # wieder erreichbar: Wartendes gleich zustellen statt beim naechsten Termin (BUG-089)
            if c.execute("UPDATE fed_outbox SET next_try=0 WHERE peer_id=? AND next_try>?", (peer["id"], now)).rowcount:
                c.after_commit(_wake.set)


# =====================================================================================================
# Eingehende Anfragen pruefen
# =====================================================================================================
class _In:
    """Gepruefte Anfrage einer verbundenen Instanz."""
    __slots__ = ("peer", "nonce", "body", "data", "principal", "name", "groups")


def _unauth(reason="unauthorized", code=401):
    return JSONResponse({"error": reason}, status_code=code, headers={"Cache-Control": "no-store"})


def _not_found():
    return JSONResponse({"error": "not found"}, status_code=404, headers={"Cache-Control": "no-store"})


def _raw_target(request):
    path = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
    return path, (request.scope.get("query_string") or b"").decode("latin-1")


def _verify_sync(method, path, query, headers, body, statuses, connect_key=None, connect_base=None):
    """-> (_In, None) oder (None, fehlerantwort). Laeuft im Thread (Datenbank)."""
    holder = {}

    def lookup(keyid):
        if connect_key is not None:                    # Verbindungsanfrage: Schluessel kommt im Koerper mit
            if keyid != connect_base:
                raise fedsig.SigError("keyid")
            return connect_key
        with _db() as c:
            p = peer_by(c, "base_url", keyid)
        if not p or p["status"] not in statuses:
            raise fedsig.SigError("unknown peer")
        holder["peer"] = p
        return fedsig.unb64u(p["public_key"])

    try:
        keyid, nonce, actor = fedsig.verify_request(lookup, method, SELF_HOST, path, query, headers, body)
    except fedsig.SigError as ex:
        with _db() as c, c:
            p = holder.get("peer")
            log(c, "sig.rejected", p, str(ex), host=None if p else _clean_text(headers.get("signature-input", "")[-80:], 80),
                throttle=(p["id"] if p else "?", str(ex)))
        return None, _unauth()
    peer = holder.get("peer") or {"id": None, "host": host_of(keyid), "base_url": keyid}
    if not _rate.allow(peer["base_url"], RATE):
        return None, _unauth("too many requests", 429)
    inp = _In()
    inp.peer, inp.nonce, inp.body, inp.data = peer, nonce, body, None
    inp.principal, inp.name, inp.groups = None, None, set()
    with _db() as c, c:
        if c.execute("INSERT OR IGNORE INTO fed_nonce (peer, nonce, created) VALUES (?,?,?)", (keyid, nonce, time.time())).rowcount == 0:
            log(c, "sig.replay", peer if peer.get("id") else None, "", host=peer["host"], throttle=(keyid, "replay"))
            return None, _unauth("replay")
        if actor is not None:
            name = actor.get("name")
            groups = actor.get("groups") or []
            if not isinstance(name, str) or not NAME_RE.fullmatch(name) or not isinstance(groups, list) or len(groups) > 50 \
                    or any(not isinstance(g, str) or auth.norm_group(g) != g for g in groups) or peer.get("id") is None:
                return None, _unauth("bad actor", 400)
            inp.name = name
            inp.principal = f"@{name}@{peer['host']}"
            inp.groups = {f"{g}@{peer['host']}" for g in groups}
            _person_upsert(c, inp.principal, peer["id"], actor.get("display") or name)
        if peer.get("id"):
            _touch(c, peer)
    if body:
        try:
            inp.data = parse_json(body)              # Surrogate/NUL der Gegenseite bereinigt (BUG-008)
        except (ValueError, RecursionError):
            return None, _unauth("bad json", 400)
    return inp, None


async def _verify(request, statuses=("active",), need_actor=False):
    """Signatur einer verbundenen Instanz pruefen -> (_In, None) oder (None, antwort)."""
    if not enabled():
        return None, _not_found()
    body = await request.body()
    path, query = _raw_target(request)
    inp, err = await run_in_threadpool(_verify_sync, request.method, path, query, request.headers, body, statuses)
    if err is None and need_actor and inp.principal is None:
        return None, _unauth("actor required", 400)
    return inp, err


def _signed(inp, status, data, media="application/json", extra=None):
    """Signierte Antwort (Content-Digest, Saga-Nonce = Nonce der Anfrage)."""
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
    priv, _ = own_key()
    h = fedsig.sign_response(priv, SELF_URL, status, body, inp.nonce)
    h.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    h.update(extra or {})
    return Response(body, status_code=status, media_type=media, headers=h)


def _as(inp, fn, *args):
    """fn(prinzipal, *args) mit den signiert bestaetigten Gruppen der fremden Person (im Thread)."""
    tok = sharing.REMOTE_GROUPS.set({inp.principal: frozenset(inp.groups)})
    try:
        return fn(inp.principal, *args)
    except pages._Reply as r:
        return r.resp
    finally:
        sharing.REMOTE_GROUPS.reset(tok)


PERSON_KEYS = frozenset(("name", "username", "me", "remote", "avatar", "handle"))


def _is_person(obj):
    """Personen-Objekt (sharing.person & Co.): name, username, me - dazu hoechstens remote/avatar/handle."""
    return {"name", "username", "me"} <= set(obj) and set(obj) <= PERSON_KEYS


def _ext_persons(c, obj, cache=None):
    """Antwort an eine andere Instanz: Personen nur mit oeffentlichem Namen ('@name@host') - nie der Anmeldename.
    Fremde Personen behalten ihre Adresse, der Name kommt ohne Herkunfts-Zusatz (setzt der Empfaenger)."""
    cache = {} if cache is None else cache
    if isinstance(obj, list):
        return [_ext_persons(c, x, cache) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if _is_person(obj):                      # Profilbild-Adressen gelten nur hier - die andere Instanz setzt eigene
        u = obj.get("username")
        if not u:
            return {"name": obj.get("name"), "username": None, "me": False}
        if u not in cache:
            if is_remote(u):
                r = c.execute("SELECT display FROM fed_person WHERE principal=?", (u,)).fetchone()
                cache[u] = {"name": (r[0] if r else "") or u[1:].split("@")[0], "username": u}
            else:
                r = c.execute("SELECT display_name FROM users WHERE username=?", (u,)).fetchone()
                pub = get_name(c, u)
                cache[u] = {"name": (r[0] if r else "") or pub or "?", "username": f"@{pub}@{SELF_HOST}" if pub else None}
        return dict(cache[u], me=bool(obj.get("me")))
    return {k: _ext_persons(c, v, cache) for k, v in obj.items()}


def _relay(inp, resp):
    """Antwort eines lokalen Endpunkts (JSONResponse) signiert an die anfragende Instanz - Personen umgeschrieben."""
    try:
        data = json.loads(resp.body or b"null")
    except ValueError:
        data = None
    with _db() as c:
        data = _ext_persons(c, data)
    return _signed(inp, resp.status_code, data)


# =====================================================================================================
# Server-zu-Server: Kennung, Nachschlagen, Verbinden
# =====================================================================================================
@router.get("/.well-known/saga")
def well_known():
    """Oeffentliche Angaben dieser Instanz (nur bei eingeschalteter Foederation)."""
    if not enabled():
        return _not_found()
    try:
        _, pub = own_key()
    except FedError:
        return _not_found()
    return JSONResponse({"software": "saga", "protocol": PROTOCOL, "version": VERSION, "base_url": SELF_URL, "host": SELF_HOST,
                         "name": title(), "key": {"id": SELF_URL, "alg": "ed25519", "public": fedsig.b64u(pub)},
                         "fingerprint": fedsig.fingerprint(pub), "capabilities": CAPS},
                        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"})


def _local_user_by_name(c, name):
    r = c.execute("SELECT p.uid, u.display_name FROM public_name p JOIN users u ON u.username=p.uid WHERE p.name=? AND u.disabled=0",
                  (name,)).fetchone()
    return (r[0], r[1] or name) if r else (None, None)


def _local_group(c, name):
    return bool(c.execute("SELECT 1 FROM groups WHERE name=? UNION SELECT 1 FROM user_ext_group WHERE name=? LIMIT 1", (name, name)).fetchone())


@router.get("/.well-known/webfinger")
async def webfinger(request: Request, resource: str = "", kind: str = "user"):
    """Person (oeffentlicher Name) oder Gruppe nachschlagen - NUR fuer verbundene Instanzen (signiert), sonst 404."""
    if not request.headers.get("signature"):
        return _not_found()
    inp, err = await _verify(request)
    if err is not None:
        return err
    m = re.fullmatch(r"acct:([^@\s]{1,100})@([^@\s]{1,260})", resource or "")
    if not m or fedsig.authority(m.group(2)) != SELF_HOST:
        return _signed(inp, 404, {"error": "not found"})

    def run():
        with _db() as c:
            if kind == "group":
                g = auth.norm_group(m.group(1))
                if g and g == m.group(1) and _local_group(c, g):
                    return {"subject": f"acct:{g}@{SELF_HOST}", "kind": "group", "name": g}
                return None
            if not NAME_RE.fullmatch(m.group(1)):
                return None
            uid, disp = _local_user_by_name(c, m.group(1))
            return {"subject": f"acct:{m.group(1)}@{SELF_HOST}", "kind": "user", "name": disp} if uid else None
    d = await run_in_threadpool(run)
    return _signed(inp, 200 if d else 404, d or {"error": "not found"})


@router.post("/fed/v1/connect")
async def fed_connect(request: Request):
    """Verbindungsanfrage einer anderen Instanz mit Einladungscode. Der Schluessel kommt im Koerper (Besitznachweis ueber
    die Signatur); zusaetzlich holen wir /.well-known/saga der anfragenden Adresse - der Schluessel muss dort derselbe sein."""
    if not enabled():
        return _not_found()
    ip = request.client.host if request.client else "?"
    if not _rate.allow("connect:" + ip, 20, 3600):
        return _unauth("too many requests", 429)
    body = await request.body()
    try:
        d = parse_json(body)
        base = norm_base(d.get("base_url"))
        key = fedsig.load_public(d.get("key"))
        code = str(d.get("code") or "")
    except (ValueError, TypeError, AttributeError, fedsig.SigError):
        return _unauth("bad request", 400)
    if not base or not code or len(code) > 100:
        return _unauth("bad request", 400)
    path, query = _raw_target(request)
    inp, err = await run_in_threadpool(_verify_sync, "POST", path, query, request.headers, body, (), key, base)
    if err is not None:
        return err
    status, out = await run_in_threadpool(_accept_connect, base, key, code, _clean_text(d.get("name"), 80))
    return _signed(inp, status, out)


def _accept_connect(base, key, code, name):
    host = host_of(base)
    if host == SELF_HOST:
        return 400, {"error": "self"}
    with _db() as c:
        inv = c.execute("SELECT id FROM fed_invite WHERE code_hash=? AND used_at IS NULL AND expires>?",
                        (hashlib.sha256(code.encode()).hexdigest(), time.time())).fetchone()
        old = peer_by(c, "host", host)
    if not inv:
        with _db() as c, c:
            log(c, "connect.bad_code", None, "", host=host, throttle=("code", host))
        return 403, {"error": "invalid code"}
    if old and old["status"] == "active":
        return 409, {"error": "already connected"}
    try:
        info = fetch_info(base)                 # bindet den Schluessel an die Adresse
    except FedError as ex:
        return 400, {"error": f"well-known: {ex}"}
    if fedsig.unb64u(info["public"]) != key:
        return 403, {"error": "key mismatch"}
    now = time.time()
    with _db() as c, c:
        if c.execute("UPDATE fed_invite SET used_at=? WHERE id=? AND used_at IS NULL", (now, inv[0])).rowcount == 0:
            return 403, {"error": "invalid code"}
        old = peer_by(c, "host", host)
        pk, fp = fedsig.b64u(key), fedsig.fingerprint(key)
        if old:
            if old["status"] == "active":
                return 409, {"error": "already connected"}
            c.execute("UPDATE fed_peer SET base_url=?, name=?, public_key=?, fingerprint=?, status='pending_in', created=?, confirmed_at=NULL, "
                      "last_error=NULL WHERE id=?", (base, name or info["name"], pk, fp, now, old["id"]))
            pid = old["id"]
        else:
            pid = _free_id(c)
            if pid is None:
                return 409, {"error": "too many peers"}
            c.execute("INSERT INTO fed_peer (id, host, base_url, name, public_key, fingerprint, status, created) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, host, base, name or info["name"], pk, fp, "pending_in", now))
        c.execute("UPDATE fed_invite SET peer_id=? WHERE id=?", (pid, inv[0]))
        log(c, "connect.request", peer_by(c, "id", pid), fp)
    _, pub = own_key()
    return 200, {"status": "pending", "base_url": SELF_URL, "name": title(), "key": fedsig.b64u(pub)}


@router.post("/fed/v1/confirm")
async def fed_confirm(request: Request):
    """Die Gegenseite hat unsere Anfrage bestaetigt -> aktiv."""
    inp, err = await _verify(request, ("pending_out", "active"))
    if err is not None:
        return err

    def run():
        with _db() as c, c:
            if inp.peer["status"] != "active":
                c.execute("UPDATE fed_peer SET status='active', confirmed_at=?, last_error=NULL WHERE id=?", (time.time(), inp.peer["id"]))
                log(c, "peer.confirmed_remote", inp.peer)
    await run_in_threadpool(run)
    return _signed(inp, 200, {"status": "active"})


@router.post("/fed/v1/disconnect")
async def fed_disconnect(request: Request):
    """Die Gegenseite hat getrennt oder abgelehnt -> sofort getrennt, Freigaben weg."""
    inp, err = await _verify(request, ("pending_in", "pending_out", "active"))
    if err is not None:
        return err

    def run():
        with _db() as c, c:
            c.execute("UPDATE fed_peer SET status='disconnected', last_error=NULL WHERE id=?", (inp.peer["id"],))
            n = cleanup_peer(c, inp.peer)
            log(c, "peer.disconnected_remote", inp.peer, f"shares={n}")
    await run_in_threadpool(run)
    return _signed(inp, 200, {"status": "disconnected"})


@router.get("/fed/v1/status")
async def fed_status(request: Request):
    """Wie sehen wir die anfragende Instanz? (Admin der Gegenseite: "Status pruefen")"""
    inp, err = await _verify(request, ("pending_in", "active"))
    if err is not None:
        return err
    return _signed(inp, 200, {"status": "active" if inp.peer["status"] == "active" else "pending"})


# =====================================================================================================
# Server-zu-Server: Ereignisse (Teilen, Kommentare, Personen)
# =====================================================================================================
def _handle(v):
    """'name@host' -> (name, host) oder (None, None)."""
    m = re.fullmatch(r"@?([a-z0-9][a-z0-9_-]{1,31})@([a-z0-9.:-]{1,260})", str(v or ""))
    return (m.group(1), fedsig.authority(m.group(2))) if m else (None, None)


def _actor_of(c, peer, a):
    """Ausloeser eines Ereignisses -> lokaler Benutzername (eigene Person) bzw. Prinzipal '@name@host' oder None."""
    if not isinstance(a, dict):
        return None
    name, host = _handle(a.get("handle"))
    if not name:
        return None
    if host == SELF_HOST:
        # Eigene Person (hat ueber diese Instanz kommentiert): nur, wenn von dort ueberhaupt etwas mit ihr geteilt ist -
        # sonst koennte die Gegenstelle Benachrichtigungen im Namen beliebiger lokaler Konten erzeugen (BUG-035)
        lu = _local_user_by_name(c, name)[0]
        if not lu:
            return None
        gs = sorted(sharing.groups_of(c, lu))
        sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
        return lu if c.execute(sql, (peer["id"], lu, *gs)).fetchone() else None
    p = f"@{name}@{host}"
    # Anzeigename: nur die eigene Instanz der Person darf ihn aendern - eine andere verbundene Instanz kann ihn hoechstens
    # erstmals setzen (sonst koennte sie Personen dritter Instanzen hier umbenennen)
    if host == peer["host"] or not c.execute("SELECT 1 FROM fed_person WHERE principal=?", (p,)).fetchone():
        _person_upsert(c, p, peer["id"], a.get("display") or name)
    return p


def _ev(c, peer, ev):
    """Ein Ereignis einer verbundenen Instanz verarbeiten (in der Transaktion). -> 'ok' | 'ignored'"""
    typ = ev.get("type")
    to = ev.get("to") if isinstance(ev.get("to"), dict) else {}
    note = ev.get("note") if isinstance(ev.get("note"), dict) else {}
    rid = note.get("id")
    if typ == "person.gone":
        name = ev.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            return "ignored"
        p = f"@{name}@{peer['host']}"
        n = c.execute("DELETE FROM note_share WHERE target_type='remote' AND target=?", (p,)).rowcount
        c.execute("UPDATE note_comment SET uid=NULL WHERE uid=?", (p,))
        c.execute("UPDATE note_comment SET resolved_by=NULL WHERE resolved_by=?", (p,))
        c.execute("DELETE FROM note_comment_seen WHERE uid=?", (p,))
        c.execute("UPDATE note SET updated_by=NULL WHERE updated_by=?", (p,))
        c.execute("UPDATE note SET created_by=NULL WHERE created_by=?", (p,))
        c.execute("UPDATE note_version SET author=NULL WHERE author=?", (p,))
        c.execute("UPDATE notification SET actor_uid=NULL WHERE actor_uid=?", (p,))
        c.execute("DELETE FROM fed_person WHERE principal=?", (p,))
        avatars.forget_remote(c, principal=p)
        if ev.get("deleted"):
            c.execute("DELETE FROM fed_inbound WHERE peer_id=? AND owner=?", (peer["id"], p))
        log(c, "person.gone", peer, f"{name} shares={n}")
        return "ok"
    if not _ok_rid(rid):
        return "ignored"
    tt, tname = to.get("type"), to.get("name")
    if tt == "user":
        if not isinstance(tname, str) or not NAME_RE.fullmatch(tname):
            return "ignored"
        target = _local_user_by_name(c, tname)[0]
        if not target:
            return "ignored"
    elif tt == "group" and typ in ("share.added", "share.role", "share.removed"):
        target = auth.norm_group(tname) if isinstance(tname, str) else None
        if not target or target != tname or not _local_group(c, target):
            return "ignored"
    else:
        return "ignored"
    title_ = _clean_text(note.get("title"), 300)
    fed = {"peer": peer["id"], "note": rid, "title": title_}
    if typ in ("share.added", "share.role"):
        role = ev.get("role")
        owner = ev.get("owner") if isinstance(ev.get("owner"), dict) else {}
        oname = owner.get("name")
        if role not in ROLES or not isinstance(oname, str) or not NAME_RE.fullmatch(oname):
            return "ignored"
        op = f"@{oname}@{peer['host']}"
        _person_upsert(c, op, peer["id"], owner.get("display") or oname)
        old = c.execute("SELECT role, owner FROM fed_inbound WHERE peer_id=? AND note_id=? AND target_type=? AND target=?",
                        (peer["id"], rid, tt, target)).fetchone()
        if old and old[0] == role and old[1] == op:
            return "ok"                      # nichts geaendert: keine neue Benachrichtigung (Wiederholung/Flut)
        c.execute("INSERT OR REPLACE INTO fed_inbound (peer_id, note_id, owner, target_type, target, role, created) VALUES (?,?,?,?,?,?,?)",
                  (peer["id"], rid, op, tt, target, role, time.time()))
        who = [target] if tt == "user" else notify.group_members(c, target)
        for u in who:
            notify.create(c, u, typ, op, None, data={"role": role, "fed": fed})
        bus.emit_after(c, "shared.changed", {"owner": op, "note_id": None, "target_type": tt, "target": target})
        return "ok"
    if typ == "share.removed":
        c.execute("DELETE FROM fed_inbound WHERE peer_id=? AND note_id=? AND target_type=? AND target=?", (peer["id"], rid, tt, target))
        bus.emit_after(c, "shared.changed", {"owner": None, "note_id": None, "target_type": tt, "target": target})
        return "ok"
    if typ in ("comment.new", "comment.reply") and tt == "user":
        cm = ev.get("comment") if isinstance(ev.get("comment"), dict) else {}
        cid, thread = cm.get("id"), cm.get("thread")
        if not _ok_rid(cid) or (thread is not None and not _ok_rid(thread)):
            return "ignored"
        block = cm.get("block") if isinstance(cm.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{0,40}", cm.get("block")) else ""
        fed.update(comment=ref(peer["id"], cid), thread=ref(peer["id"], thread or cid), block=block)
        notify.create(c, target, typ, _actor_of(c, peer, ev.get("actor")), None, data={"fed": fed})
        return "ok"
    return "ignored"


@router.post("/fed/v1/events")
async def fed_events(request: Request):
    inp, err = await _verify(request)
    if err is not None:
        return err
    evs = (inp.data or {}).get("events") if isinstance(inp.data, dict) else None
    if not isinstance(evs, list) or len(evs) > EVENTS_MAX:
        return _signed(inp, 400, {"error": "bad events"})
    if not _rate.allow("ev:" + inp.peer["base_url"], 120):
        return _signed(inp, 429, {"error": "too many events"})

    def run():
        out = []
        with _db() as c, c:
            for ev in evs:
                out.append(_ev(c, inp.peer, ev) if isinstance(ev, dict) else "ignored")
        return out
    return _signed(inp, 200, {"results": await run_in_threadpool(run)})


# =====================================================================================================
# Server-zu-Server: Inhalte im Namen einer Person (Eigentuemer-Seite)
# =====================================================================================================
EDIT_KEYS = ("title", "body", "format", "rev", "overwrite")


@router.get("/fed/v1/shared")
async def fed_shared(request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err

    def run():
        items = _as(inp, sharing.shared_list)
        with _db() as c:
            return _ext_persons(c, items)
    return _signed(inp, 200, await run_in_threadpool(run))


@router.get("/fed/v1/notes/{nid}")
async def fed_note_get(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, pages.note_get, nid))


@router.patch("/fed/v1/notes/{nid}")
async def fed_note_patch(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    b = inp.data if isinstance(inp.data, dict) else None
    if not b:
        return _signed(inp, 400, {"error": "bad request"})
    b = {k: v for k, v in b.items() if k in EDIT_KEYS}           # ueber Instanzen: nur Titel und Inhalt
    if not b:
        return _signed(inp, 400, {"error": t("nichts zu ändern")})
    return _relay(inp, await run_in_threadpool(_as, inp, pages._note_update, nid, b))


@router.get("/fed/v1/notes/{nid}/comments")
async def fed_comments(nid: int, request: Request, resolved: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comments_list, nid, 1 if resolved else 0))


@router.post("/fed/v1/notes/{nid}/comments")
async def fed_comment_add(nid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    b = inp.data if isinstance(inp.data, dict) else None
    if b is None:
        return _signed(inp, 400, {"error": "bad request"})
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_add, nid, b))


@router.patch("/fed/v1/comments/{cid}")
async def fed_comment_edit(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_edit, cid, inp.data if isinstance(inp.data, dict) else {}))


@router.delete("/fed/v1/comments/{cid}")
async def fed_comment_delete(cid: int, request: Request, all: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_delete, cid, 1 if all else 0))


@router.post("/fed/v1/comments/{cid}/resolve")
async def fed_comment_resolve(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_resolve, cid, inp.data if isinstance(inp.data, dict) else {}))


@router.post("/fed/v1/comments/{cid}/seen")
async def fed_comment_seen(cid: int, request: Request):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    return _relay(inp, await run_in_threadpool(_as, inp, comments.comment_seen, cid))


def _file_for(principal, fid, nid, request):
    """Datei fid, wenn die fremde Person die Seite nid sehen darf und GENAU diese Seite die Datei benutzt
    (Inhalt, Symbol, Titelbild) und sie dem Eigentuemer der Seite gehoert. -> (bytes, mime, name, is_image) oder None."""
    if not _FID.fullmatch(fid or "") or not (0 < nid < 2 ** 63):
        return None
    with _db() as c:
        acc = sharing.Access(c, principal)
        if not acc.role(nid):
            return None
        n = c.execute("SELECT uid, body, icon, cover FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
        f = c.execute("SELECT name, mime, is_image, uid FROM file WHERE id=?", (fid,)).fetchone()
    ref_ = f"/files/{fid}"
    if not n or not f or f[3] != n[0] or not (ref_ in (n[1] or "") or n[2] == ref_ or n[3] == ref_):
        return None
    path = os.path.join(FILES_DIR, fid)
    if not os.path.isfile(path) or os.path.getsize(path) > FILE_MAX + 1024:
        return None
    mime = f[1]
    if f[2]:
        w = _thumb_w(request)
        tp = _thumb(fid, path, mime, w) if w else None
        if tp:
            path, mime = tp, _thumb_mime(tp)
    with open(path, "rb") as fh:
        return fh.read(), mime, f[0], bool(f[2])


@router.get("/fed/v1/files/{fid}")
async def fed_file(fid: str, request: Request, note: int = 0):
    inp, err = await _verify(request, need_actor=True)
    if err is not None:
        return err
    r = await run_in_threadpool(_as, inp, _file_for, fid, note, request)
    if not r:
        return _signed(inp, 404, b"not found", "text/plain")
    data, mime, name, img = r
    return _signed(inp, 200, data, mime if img else "application/octet-stream",
                   {"Content-Disposition": "inline" if img else _disposition(name)})


# Profilbilder (2.1): eine verbundene Instanz holt das Bild einer Person (oeffentlicher Name) - signiert, neu kodiert (WebP,
# 256 px). Die andere Instanz kodiert es noch einmal neu und speichert es einen Tag zwischen (saga.avatars).
AVATAR_MAX = 512 * 1024


def _avatar_for(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return None
    with _db() as c:
        uid, _ = _local_user_by_name(c, name)
    return avatars.local_image(uid, 256, "webp") if uid else None


@router.get("/fed/v1/avatar/{name}")
async def fed_avatar(name: str, request: Request):
    inp, err = await _verify(request)
    if err is not None:
        return err
    got = await run_in_threadpool(_avatar_for, name)
    if not got:
        return _signed(inp, 404, b"not found", "text/plain")
    return _signed(inp, 200, got[1], got[0])


def fetch_avatar(principal):
    """Fuer saga.avatars: Bild einer fremden Person bei ihrer Instanz holen -> bytes | None (kein Bild). FedError = spaeter."""
    n, host = _handle(principal)
    if not n:
        return None
    with _db() as c:
        p = peer_by(c, "host", host)
    if not enabled() or not p or p["status"] != "active":
        return None
    status, rh, data = call(p, "GET", "/fed/v1/avatar/" + quote(n, safe=""), limit=AVATAR_MAX, timeout=6)
    if status == 404:
        return None
    if status != 200:
        raise FedError(f"HTTP {status}", status)
    return data


avatars.REMOTE_FETCH = fetch_avatar


# =====================================================================================================
# Ausgehende Ereignisse: Ausgangskorb + Arbeiter
# =====================================================================================================
_wake = threading.Event()
_worker = {"thread": None, "stop": False, "house": 0.0}


def _enqueue(c, peer_id, ev):
    now = time.time()
    c.execute("INSERT INTO fed_outbox (peer_id, payload, created, next_try) VALUES (?,?,?,?)",
              (peer_id, json.dumps(ev, ensure_ascii=False), now, now))
    after = getattr(c, "after_commit", None)
    if after:
        after(_wake.set)


def _target_peer(c, target):
    """'@name@host' bzw. 'gruppe@host' -> (aktiver peer, name) oder (None, None)."""
    name, _, host = target.lstrip("@").rpartition("@")
    p = peer_by(c, "host", host) if name and host else None
    return (p, name) if p and p["status"] == "active" else (None, None)


def _owner_info(c, uid):
    pub = get_name(c, uid)
    r = c.execute("SELECT display_name FROM users WHERE username=?", (uid,)).fetchone()
    return {"name": pub, "display": (r[0] if r else "") or pub}


def _note_title(c, nid):
    r = c.execute("SELECT title FROM note WHERE id=?", (nid,)).fetchone()
    return (r[0] or "") if r else ""


def _on_share_set(c, owner, note_id, target_type, target, role, old_role=None, **_):
    if target_type not in sharing.REMOTE_TYPES or old_role == role or not enabled():
        return
    p, name = _target_peer(c, target)
    if not p:
        return
    o = _owner_info(c, owner)
    if not o["name"]:
        return
    _enqueue(c, p["id"], {"type": "share.role" if old_role else "share.added", "to": {"type": "user" if target_type == "remote" else "group", "name": name},
                          "owner": o, "note": {"id": note_id, "title": _note_title(c, note_id)}, "role": role})
    log(c, "share.set", p, f"note={note_id} {target_type} role={role}")


def _on_share_removed(c, owner, note_id, target_type, target, **_):
    if target_type not in sharing.REMOTE_TYPES:
        return
    p, name = _target_peer(c, target)
    if not p:
        return
    _enqueue(c, p["id"], {"type": "share.removed", "to": {"type": "user" if target_type == "remote" else "group", "name": name},
                          "note": {"id": note_id}})
    log(c, "share.removed", p, f"note={note_id} {target_type}")


def _handle_for(c, uid):
    """Ausloeser fuer andere Instanzen: {handle: 'name@host', display} - lokale Konten nur mit oeffentlichem Namen."""
    if not uid:
        return None
    if is_remote(uid):
        r = c.execute("SELECT display FROM fed_person WHERE principal=?", (uid,)).fetchone()
        return {"handle": uid[1:], "display": (r[0] if r else "") or uid[1:]}
    pub = get_name(c, uid)
    if not pub:
        return None
    r = c.execute("SELECT display_name FROM users WHERE username=?", (uid,)).fetchone()
    return {"handle": f"{pub}@{SELF_HOST}", "display": (r[0] if r else "") or pub}


def _on_notify(c, uid, kind, actor=None, note_id=None, comment_id=None, data=None, **_):
    """Benachrichtigung fuer eine fremde Person (saga.notify) -> signiertes Ereignis an ihre Instanz."""
    if kind not in ("comment.new", "comment.reply") or not note_id or not enabled():
        return
    p, name = _target_peer(c, uid)
    if not p or not sharing.Access(c, uid).role(note_id):
        return
    cm = c.execute("SELECT thread_id, block_id FROM note_comment WHERE id=?", (comment_id,)).fetchone() if comment_id else None
    _enqueue(c, p["id"], {"type": kind, "to": {"type": "user", "name": name}, "actor": _handle_for(c, actor),
                          "note": {"id": note_id, "title": _note_title(c, note_id)},
                          "comment": {"id": comment_id, "thread": (cm[0] or comment_id) if cm else comment_id, "block": (cm[1] if cm else "") or ""}})


def _on_name_released(c, uid, pub=None, deleted=False, **_):
    """Oeffentlicher Name geaendert oder Konto geloescht: Eingangs-Verweise weg; war die Foederation je aktiv, Name sperren
    (nie an ein anderes Konto) und alle verbundenen Instanzen informieren (Freigaben an den alten Namen enden dort)."""
    c.execute("DELETE FROM fed_inbound WHERE target_type='user' AND target=?", (uid,))
    if not pub or not has_key(c):
        return
    c.execute("INSERT OR REPLACE INTO fed_name_retired (name, uid, retired) VALUES (?,?,?)", (pub, None if deleted else uid, time.time()))
    for p in peers(c):
        if p["status"] == "active":
            _enqueue(c, p["id"], {"type": "person.gone", "name": pub, "deleted": bool(deleted)})


bus.on("share.set", _on_share_set)
bus.on("share.removed", _on_share_removed)
bus.on("fed.notify", _on_notify)
bus.on("public_name.released", _on_name_released)


def deliver_due():
    """Faellige Ereignisse senden (mehrere Prozesse: Zeilen werden reserviert). -> Anzahl zugestellter."""
    now = time.time()
    with _db() as c, c:
        rows = c.execute("UPDATE fed_outbox SET locked_until=? WHERE id IN (SELECT id FROM fed_outbox WHERE next_try<=? AND locked_until<? "
                         "ORDER BY id LIMIT 60 FOR UPDATE SKIP LOCKED) RETURNING id, peer_id, payload, attempts, created",
                         (now + 120, now, now)).fetchall()
    if not rows:
        return 0
    by = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    sent = 0
    for pid, lst in by.items():
        with _db() as c:
            p = peer_by(c, "id", pid)
        if not p or p["status"] != "active":
            with _db() as c, c:
                c.execute(f"DELETE FROM fed_outbox WHERE id IN ({','.join('?' * len(lst))})", tuple(r[0] for r in lst))
            continue
        for k in range(0, len(lst), EVENTS_MAX):
            part = lst[k:k + EVENTS_MAX]
            ids = tuple(r[0] for r in part)
            err = None
            try:
                status, _ = call_json(p, "POST", "/fed/v1/events", body={"events": [json.loads(r[2]) for r in part]})
                if status != 200:
                    err = f"HTTP {status}"
            except FedError as ex:
                err = str(ex)
            with _db() as c, c:
                if err is None:
                    c.execute(f"DELETE FROM fed_outbox WHERE id IN ({','.join('?' * len(ids))})", ids)
                    _touch(c, p)
                    sent += len(ids)
                    continue
                _touch(c, p, err)
                for r in part:
                    if r[4] < now - OUTBOX_KEEP:
                        c.execute("DELETE FROM fed_outbox WHERE id=?", (r[0],))
                        log(c, "event.dropped", p, err)
                    else:
                        c.execute("UPDATE fed_outbox SET attempts=attempts+1, next_try=?, locked_until=0, last_error=? WHERE id=?",
                                  (now + min(3600, 15 * 2 ** min(r[3], 12)), err[:300], r[0]))
    return sent


def housekeeping():
    now = time.time()
    with _db() as c, c:
        c.execute("DELETE FROM fed_nonce WHERE created < ?", (now - 2 * fedsig.SKEW - 60,))
        c.execute("DELETE FROM fed_invite WHERE expires < ? AND used_at IS NULL", (now - 7 * 86400,))
        c.execute("DELETE FROM fed_log WHERE ts < ?", (now - 180 * 86400,))


def _loop():
    while not _worker["stop"]:
        _wake.wait(5)
        _wake.clear()
        if _worker["stop"]:
            break
        try:
            if not enabled():
                continue
            deliver_due()
            if time.time() - _worker["house"] > 600:
                _worker["house"] = time.time()
                housekeeping()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg o. ae.: naechster Durchlauf
            print(f"federation: {type(ex).__name__}: {ex}", flush=True)


def start():
    if _worker["thread"] is None:
        _worker["stop"] = False
        th = threading.Thread(target=_loop, name="saga-federation", daemon=True)
        _worker["thread"] = th
        th.start()


def stop():
    _worker["stop"] = True
    _wake.set()
    _worker["thread"] = None


# =====================================================================================================
# Heimatserver: Proxy fuer die eigenen Konten (Weboberflaeche, Apps)
# =====================================================================================================
class FedId(Convertor):
    """Nur negative IDs (Seiten/Kommentare anderer Instanzen) - positive gehen an die normalen Endpunkte."""
    regex = "-[1-9][0-9]{0,17}"

    def convert(self, value):
        return int(value)

    def to_string(self, value):
        return str(value)


register_url_convertor("fedid", FedId())


class _Fail(Exception):
    def __init__(self, resp):
        super().__init__()
        self.resp = resp


def _unavailable():
    return _bad(t("Die andere Saga-Instanz ist gerade nicht erreichbar."), 502)


def _home_actor(c, uid, peer_id):
    """Wer fragt: oeffentlicher Name, Anzeigename und die Gruppen, die die andere Instanz als Ziel genannt hat."""
    pub = get_name(c, uid)
    if not pub:
        raise _Fail(_bad(t("Für Seiten anderer Saga-Instanzen brauchst du einen öffentlichen Namen (Einstellungen)."), 403))
    r = c.execute("SELECT display_name FROM users WHERE username=?", (uid,)).fetchone()
    named = {x[0] for x in c.execute("SELECT DISTINCT target FROM fed_inbound WHERE peer_id=? AND target_type='group'", (peer_id,))}
    return {"name": pub, "display": _clean_text((r[0] if r else "") or pub, DISPLAY_MAX),
            "groups": sorted(sharing.groups_of(c, uid) & named)}


def _home_peer(c, peer_id):
    p = peer_by(c, "id", peer_id)
    if not enabled() or not p or p["status"] != "active":
        raise _Fail(_bad("not found", 404))
    return p


def _proxy(uid, peer_id, method, path, query="", body=None):
    """-> (peer, status, json). _Fail bei fehlender Verbindung/Namen; nicht erreichbar -> 502."""
    with _db() as c:
        p = _home_peer(c, peer_id)
        actor = _home_actor(c, uid, peer_id)
    try:
        status, d = call_json(p, method, path, query, body, actor)
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        raise _Fail(_unavailable())
    return p, status, d


def _in_person(c, p, peer, uid, cache):
    """Person aus der Antwort einer anderen Instanz -> Anzeige hier (fremde immer mit Herkunft)."""
    u, name = p.get("username"), _clean_text(p.get("name"), DISPLAY_MAX)
    if u and is_remote(u) and _handle(u)[0]:
        n, host = _handle(u)
        if host == SELF_HOST:                               # eigene Person (z. B. ich selbst)
            if u not in cache:
                cache[u] = _local_user_by_name(c, n)
            lu, disp = cache[u]
            if lu:
                return {"name": disp, "username": lu, "me": lu == uid, "avatar": _avatar_cached(c, lu, cache)}
        pr = f"@{n}@{host}"
        return {"name": remote_label(pr, name), "username": pr, "me": bool(p.get("me")), "remote": True,
                "handle": f"{n}@{host}", "avatar": _avatar_cached(c, pr, cache)}
    return {"name": f"{name} ({peer['host']})" if name else peer["host"], "username": None, "me": False, "remote": True, "avatar": None}


def _avatar_cached(c, who, cache):
    k = ("avatar", who)
    if k not in cache:
        cache[k] = avatars.url_of(c, who)
    return cache[k]


def _in_persons(c, obj, peer, uid, cache=None):
    cache = {} if cache is None else cache
    if isinstance(obj, list):
        return [_in_persons(c, x, peer, uid, cache) for x in obj]
    if not isinstance(obj, dict):
        return obj
    if _is_person(obj):
        return _in_person(c, obj, peer, uid, cache)
    return {k: _in_persons(c, v, peer, uid, cache) for k, v in obj.items()}


_FILES_IN = re.compile(r"/files/([0-9a-f]{24})")
_LINKS_IN = re.compile(r"/notes#([1-9][0-9]{0,17})")
_FILES_OUT = re.compile(r"/api/fed/files/(-[1-9][0-9]{0,17})/([0-9a-f]{24})")
_LINKS_OUT = re.compile(r"/notes#(-?[0-9]{1,19})")


def _rw_in(s, pid, page_ref):
    """Inhalt einer fremden Seite fuer die Anzeige hier: Bilder ueber den Proxy, Seitenverweise auf negative IDs."""
    if not isinstance(s, str):
        return s
    s = _FILES_IN.sub(lambda m: f"/api/fed/files/{page_ref}/{m.group(1)}", s)
    return _LINKS_IN.sub(lambda m: f"/notes#{ref(pid, int(m.group(1)))}" if int(m.group(1)) < REMOTE_ID_MAX else "#", s)


def _rw_out(s, pid):
    """Beim Speichern zurueck: Bilder/Verweise derselben Instanz wieder in deren Form; Verweise auf hiesige oder dritte
    Seiten werden entschaerft ('#' - der Server dort macht daraus Text)."""
    if not isinstance(s, str):
        return s

    def f(m):
        p, _ = unref(int(m.group(1)))
        return f"/files/{m.group(2)}" if p == pid else "/files/0"

    def ln(m):
        v = int(m.group(1))
        if v < 0:
            p, rid = unref(v)
            if p == pid:
                return f"/notes#{rid}"
        return "#"
    return _LINKS_OUT.sub(ln, _FILES_OUT.sub(f, s))


_PIC = re.compile(r"/files/[0-9a-f]{24}")


def _safe_pic(v, icon):
    """Symbol/Titelbild einer fremden Seite: nur ein Bild dieser Seite (/files/<id>, wird zum Proxy) oder - beim Symbol -
    ein kurzes Emoji wie bei eigenen Seiten. Alles andere (fremde oder hiesige Adressen wie /logout) faellt weg."""
    s = v.strip() if isinstance(v, str) else ""
    if _PIC.fullmatch(s):
        return s
    if icon and len(s) <= 8 and not any(ch in s for ch in "<>&\"'/\\:") and not re.search(r"[\x00-\x1f\x7f]", s):
        return s
    return ""


def _clean_remote_body(s, fmt):
    """Inhalt einer fremden Seite: dieselbe Bereinigung wie beim Speichern eigener Seiten (Positivliste fuer Inline-HTML),
    Bild-/Datei-Bloecke nur mit Dateien dieser Seite (/files/<id>) - eine boesartige Instanz kann so weder HTML noch
    Adressen dieses Servers (GET auf /logout o. ae.) in die Oberflaeche bringen."""
    if not isinstance(s, str):
        return ""
    if fmt != "blocks":
        return s
    try:
        body, _ = pages.clean_blocks(s, max(len(s), 1))
        doc = json.loads(body)
        for b in _iter_blocks(doc["blocks"]):
            if b.get("type") in ("image", "attaches"):
                f = b["data"].get("file") if isinstance(b.get("data"), dict) else None
                if not isinstance(f, dict) or not _PIC.fullmatch(str(f.get("url") or "")):
                    b["data"] = dict(b.get("data") or {}, file={"url": ""})
    except (ValueError, TypeError, KeyError, RecursionError):
        return json.dumps({"blocks": []})
    return json.dumps(doc, ensure_ascii=False)


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else default


def _typed(d):
    """Felder der Gegenstelle auf die Typen bringen, die Web und Apps erwarten (BUG-034) - kaputte Werte fallen weg."""
    tags = d.get("tags")
    d["tags"] = [_clean_text(x, 40) for x in tags[:50] if isinstance(x, str) and _clean_text(x, 40)] if isinstance(tags, list) else []
    d["kind"] = "database" if d.get("kind") == "database" else "page"
    d["icon"] = d.get("icon") if isinstance(d.get("icon"), str) else ""
    for k in ("updated", "created"):
        if k in d:
            d[k] = _num(d.get(k))
    for k in ("template", "root", "unread", "wide", "archived"):
        if k in d:
            d[k] = d.get(k) is True
    if "rev" in d:
        d["rev"] = d["rev"] if _ok_rid(d.get("rev")) or d.get("rev") == 0 else 0
    if "cover_pos" in d:
        d["cover_pos"] = max(0.0, min(100.0, float(_num(d.get("cover_pos"), 50.0))))
    if "props" in d and not isinstance(d.get("props"), dict):
        d["props"] = {}
    for k in ("owner", "updated_by", "created_by"):
        if k in d and not isinstance(d.get(k), dict):
            d[k] = None
    return d


SHARED_KEYS = ("id", "title", "icon", "parent_id", "updated", "tags", "pinned", "template", "kind", "role", "root", "shared", "owner", "unread")


def _note_in(c, peer, d, uid):
    pid = peer["id"]
    rid = d.get("id")
    if not _ok_rid(rid):
        raise _Fail(_unavailable())
    pr = ref(pid, rid)
    d = _typed(_in_persons(c, d, peer, uid))
    d["id"] = pr
    d["parent_id"] = ref(pid, d["parent_id"]) if _ok_rid(d.get("parent_id")) else None
    d["title"] = _clean_text(d.get("title"), 500)
    d["format"] = "blocks" if d.get("format") == "blocks" else "text"
    # Inhalt der anderen Instanz wie eigenen behandeln: erst bereinigen (IDs noch in deren Form), dann umschreiben
    d["body"] = _rw_in(_clean_remote_body(d.get("body"), d["format"]), pid, pr)
    d["icon"] = _rw_in(_safe_pic(d.get("icon"), True), pid, pr)
    d["cover"] = _rw_in(_safe_pic(d.get("cover"), False), pid, pr)
    if d.get("role") not in ROLES:
        d["role"] = "view"
    d.update(pinned=False, published=False, public_url=None, published_via=None, shared=True, locked=bool(d.get("locked")),
             remote={"peer": pid, "host": peer["host"], "name": peer["name"]})
    if d.get("kind") == "database":          # Datenbanken anderer Instanzen: in dieser Runde nur ein Hinweis
        d.pop("schema", None)
        d.update(kind="page", role="view", format="blocks", comment_counts={}, comments_open=0, comments_unread=0,
                 body=json.dumps({"blocks": [{"id": "fed-db", "type": "paragraph", "data": {"text": _esc_text(
                     t("Diese Datenbank liegt auf {host}. Datenbanken anderer Saga-Instanzen lassen sich hier noch nicht öffnen.",
                       host=peer["host"]))}}]}, ensure_ascii=False))
    return d


def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _cref(pid, v):
    return ref(pid, v) if _ok_rid(v) else None


def _comments_in(c, peer, d, uid):
    pid = peer["id"]
    d = _in_persons(c, d, peer, uid)
    for th in d.get("threads") or []:
        th["id"] = _cref(pid, th.get("id"))
        for cm in th.get("comments") or []:
            cm["id"], cm["thread_id"] = _cref(pid, cm.get("id")), _cref(pid, cm.get("thread_id"))
    d["is_owner"] = False
    return d


def _run(fn, *args):
    """Proxy-Arbeit (im Thread): _Fail in die Fehlerantwort umwandeln."""
    try:
        return fn(*args)
    except _Fail as f:
        return f.resp


def _json(status, d):
    return JSONResponse(d, status_code=status, headers={"Cache-Control": "no-store"})


def _pass_err(peer, status, d):
    """Fehler der anderen Instanz durchreichen (Text kommt in deren Sprache - besser als nichts)."""
    if status in (401, 407) or not 400 <= status < 500:
        # 401/407 hiesse fuer die Apps "neu anmelden" (Sitzung HIER ist aber gueltig), Weiterleitungen/5xx sind keine
        # verwertbare Antwort - nie Fremdtext durchreichen (BUG-036)
        return _unavailable()
    out = {"error": _clean_text((d or {}).get("error"), 300) or f"HTTP {status}"} if isinstance(d, dict) else {"error": f"HTTP {status}"}
    for k in ("rev", "by"):
        if isinstance(d, dict) and k in d:
            out[k] = d[k] if k == "rev" and isinstance(d[k], int) else _clean_text(d[k], 200) if d[k] else None
    return _json(status if 400 <= status < 500 else 502, out)


def _w_note_get(uid, nid):
    pid, rid = unref(nid)
    p, status, d = _proxy(uid, pid, "GET", f"/fed/v1/notes/{rid}")
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        return _json(200, _note_in(c, p, d, uid))


def _w_note_patch(uid, nid, b):
    pid, rid = unref(nid)
    b = {k: v for k, v in b.items() if k in EDIT_KEYS}
    if not b:
        return _bad(t("Bei Seiten anderer Saga-Instanzen lassen sich nur Titel und Inhalt ändern."), 403)
    if "body" in b:
        b["body"] = _rw_out(b["body"], pid)
    p, status, d = _proxy(uid, pid, "PATCH", f"/fed/v1/notes/{rid}", body=b)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    return _json(200, {k: d.get(k) for k in ("ok", "updated", "rev")})


def _w_comments(uid, nid, resolved):
    pid, rid = unref(nid)
    p, status, d = _proxy(uid, pid, "GET", f"/fed/v1/notes/{rid}/comments", "resolved=1" if resolved else "")
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        return _json(200, _comments_in(c, p, d, uid))


def _w_comment_add(uid, nid, b):
    pid, rid = unref(nid)
    out = {"body": b.get("body"), "block_id": b.get("block_id")}
    if b.get("thread_id") is not None:
        try:
            tp, trid = unref(int(b["thread_id"]))
        except (TypeError, ValueError, OverflowError):
            return _bad(t("ungültige Angabe"))
        if tp != pid or int(b["thread_id"]) >= 0:
            return _bad("not found", 404)
        out["thread_id"] = trid
    p, status, d = _proxy(uid, pid, "POST", f"/fed/v1/notes/{rid}/comments", body=out)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        d = _in_persons(c, d, p, uid)
    cm = d.get("comment") or {}
    cm["id"], cm["thread_id"] = _cref(pid, cm.get("id")), _cref(pid, cm.get("thread_id"))
    return _json(200, {"comment": cm, "thread_id": _cref(pid, d.get("thread_id"))})


def _w_comment(uid, cid, method, suffix="", body=None, query=""):
    pid, rcid = unref(cid)
    p, status, d = _proxy(uid, pid, method, f"/fed/v1/comments/{rcid}{suffix}", query, body)
    if status != 200 or not isinstance(d, dict):
        return _pass_err(p, status, d)
    with _db() as c:
        d = _in_persons(c, d, p, uid)
    if isinstance(d.get("comment"), dict):
        d["comment"]["id"], d["comment"]["thread_id"] = _cref(pid, d["comment"].get("id")), _cref(pid, d["comment"].get("thread_id"))
    if "thread_id" in d:
        d["thread_id"] = _cref(pid, d.get("thread_id"))
    return _json(200, d)


def _uid_or_401(request):
    uid = _api_guard(request)
    return uid, (None if uid else _bad("unauthorized", 401))


@router.get("/api/notes/{nid:fedid}")
def home_note_get(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    return err or _run(_w_note_get, uid, nid)


@router.patch("/api/notes/{nid:fedid}")
async def home_note_patch(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    return await run_in_threadpool(_run, _w_note_patch, uid, nid, b)


@router.get("/api/notes/{nid:fedid}/comments")
def home_comments(nid: int, request: Request, resolved: int = 0):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comments, uid, nid, resolved)


@router.post("/api/notes/{nid:fedid}/comments")
async def home_comment_add(nid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    return await run_in_threadpool(_run, _w_comment_add, uid, nid, b)


@router.patch("/api/comments/{cid:fedid}")
async def home_comment_edit(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request) or {}
    return await run_in_threadpool(_run, _w_comment, uid, cid, "PATCH", "", {"body": b.get("body")})


@router.delete("/api/comments/{cid:fedid}")
def home_comment_delete(cid: int, request: Request, all: int = 0):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comment, uid, cid, "DELETE", "", None, "all=1" if all else "")


@router.post("/api/comments/{cid:fedid}/resolve")
async def home_comment_resolve(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    if err:
        return err
    b = await _body(request) or {}
    return await run_in_threadpool(_run, _w_comment, uid, cid, "POST", "/resolve", {"resolved": b.get("resolved", True) is not False})


@router.post("/api/comments/{cid:fedid}/seen")
def home_comment_seen(cid: int, request: Request):
    uid, err = _uid_or_401(request)
    return err or _run(_w_comment, uid, cid, "POST", "/seen", {})


@router.api_route("/api/notes/{nid:fedid}/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def home_other(nid: int, rest: str, request: Request):
    """Alles andere (Versionen, Export, Freigaben, Verschieben ...) gibt es fuer Seiten anderer Instanzen nicht."""
    return _bad(t("Für Seiten anderer Saga-Instanzen nicht verfügbar."), 404)


_IMG_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")


@router.get("/api/fed/files/{nid:fedid}/{fid}")
def home_file(nid: int, fid: str, request: Request):
    """Bild/Datei einer fremden Seite ueber den Eigentuemer-Server (Sitzung reicht - <img> sendet keine Kopfzeilen)."""
    uid = _session_user(request)
    if not uid or not _FID.fullmatch(fid):
        return _bad("not found", 404)
    pid, rid = unref(nid)
    q = f"note={rid}"
    w = _thumb_w(request)
    if w:
        q += f"&w={w}"
    try:
        with _db() as c:
            p = _home_peer(c, pid)
            actor = _home_actor(c, uid, pid)
        status, rh, data = call(p, "GET", f"/fed/v1/files/{fid}", q, None, actor, limit=FILE_MAX + 1024)
    except _Fail as f:
        return f.resp
    except FedError:
        return _unavailable()
    if status != 200:
        return _bad("not found", 404)
    mime = (rh.get("content-type") or "").split(";")[0].strip().lower()
    h = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer",
         "Cache-Control": "private, max-age=300"}
    if mime in _IMG_TYPES:
        h["Content-Disposition"] = "inline"
        return Response(data, media_type=mime, headers=h)
    disp = rh.get("content-disposition") or ""
    m = re.search(r'filename="([A-Za-z0-9 ._()\-]{1,200})"', disp)
    h["Content-Disposition"] = f'attachment; filename="{m.group(1) if m else "datei"}"'
    return Response(data, media_type="application/octet-stream", headers=h)


def _shared_from(uid, p, actor):
    try:
        status, d = call_json(p, "GET", "/fed/v1/shared", actor=actor, timeout=LIST_TIMEOUT)
    except FedError as ex:
        return p, None, str(ex)
    return p, d if status == 200 and isinstance(d, list) else None, None if status == 200 else f"HTTP {status}"


@router.get("/api/fed/shared")
def home_shared(request: Request):
    """"Mit mir geteilt" von anderen Instanzen (parallel, je Instanz hoechstens LIST_TIMEOUT s)."""
    uid, err = _uid_or_401(request)
    if err:
        return err
    if not enabled():
        return _json(200, {"items": [], "unreachable": []})
    with _db() as c:
        gs = sorted(sharing.groups_of(c, uid))
        sql = "SELECT DISTINCT peer_id FROM fed_inbound WHERE (target_type='user' AND target=?)" + \
              (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "")
        pids = [r[0] for r in c.execute(sql, (uid, *gs))]
        jobs = []
        for pid in pids:
            p = peer_by(c, "id", pid)
            if p and p["status"] == "active" and get_name(c, uid):
                jobs.append((p, _home_actor(c, uid, pid)))
    items, bad = [], []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            res = list(ex.map(lambda j: _shared_from(uid, *j), jobs))
        with _db() as c:
            for p, lst, e in res:
                if lst is None:
                    bad.append(p["host"])
                    continue
                for d in lst[:2000]:
                    if not isinstance(d, dict) or not _ok_rid(d.get("id")):
                        continue
                    d = _typed(_in_persons(c, {k: d[k] for k in SHARED_KEYS if k in d}, p, uid))   # nur bekannte Felder (BUG-034)
                    d["id"] = ref(p["id"], d["id"])
                    d["parent_id"] = ref(p["id"], d["parent_id"]) if _ok_rid(d.get("parent_id")) and not d.get("root") else None
                    d["icon"] = _rw_in(_safe_pic(d.get("icon"), True), p["id"], d["id"])
                    d["title"] = _clean_text(d.get("title"), 500)
                    if d.get("role") not in ROLES:
                        d["role"] = "view"
                    d.update(shared=True, pinned=False, remote={"peer": p["id"], "host": p["host"], "name": p["name"]})
                    items.append(d)
    return _json(200, {"items": items, "unreachable": bad})


# =====================================================================================================
# Teilen an Personen/Gruppen anderer Instanzen (Haken in saga.sharing und saga.notify)
# =====================================================================================================
def _parse_target(typ, tg):
    """'@bert@b.example' / 'bert@b.example' (Person) bzw. 'team@b.example' (Gruppe) -> (name, host) oder (None, None)."""
    name, _, host = str(tg or "").strip().lower().lstrip("@").rpartition("@")
    host = fedsig.authority(host)
    if not name or not _HOST_RE.fullmatch(host or "-"):
        return None, None
    if typ == "remote" and not NAME_RE.fullmatch(name):
        return None, None
    if typ == "remote_group" and auth.norm_group(name) != name:
        return None, None
    return name, host


def _lookup(p, name, kind):
    """Person/Gruppe bei der anderen Instanz nachschlagen -> Anzeigename oder None (gibt es nicht). FedError."""
    q = "resource=" + quote(f"acct:{name}@{p['host']}", safe="") + ("&kind=group" if kind == "group" else "")
    status, d = call_json(p, "GET", "/.well-known/webfinger", q, timeout=6)
    if status == 404:
        return None
    if status != 200 or not isinstance(d, dict) or d.get("kind") != kind:
        raise FedError(f"HTTP {status}", status)
    return _clean_text(d.get("name"), DISPLAY_MAX) or name


def remote_target(uid, typ, tg, nid):
    """Fuer PUT /api/notes/{nid}/shares -> (ziel, fehlertext, status). Netzwerk - ausserhalb von Transaktionen."""
    name, host = _parse_target(typ, tg)
    if not name:
        return None, t("Adresse bitte als name@server angeben."), 400
    with _db() as c:
        if not get_name(c, uid):
            return None, t("Zum Teilen mit anderen Saga-Instanzen brauchst du einen öffentlichen Namen (Einstellungen)."), 400
        p = peer_by(c, "host", host)
        if host == SELF_HOST:
            return None, t("Das ist dieser Server – bitte ohne @server teilen."), 400
        if not p or p["status"] != "active":
            return None, t("Mit {host} ist dieser Server nicht verbunden.", host=host), 400
        target = f"@{name}@{host}" if typ == "remote" else f"{name}@{host}"
        if c.execute("SELECT 1 FROM note_share WHERE note_id=? AND target_type=? AND target=?", (nid, typ, target)).fetchone():
            return target, None, 200                       # nur Rolle aendern: keine Nachfrage noetig
    try:
        disp = _lookup(p, name, "user" if typ == "remote" else "group")
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        return None, t("Die andere Saga-Instanz ist gerade nicht erreichbar."), 502
    if disp is None:
        return None, (t("Diese Person gibt es auf {host} nicht (oder sie hat keinen öffentlichen Namen).", host=host) if typ == "remote"
                      else t("Diese Gruppe gibt es auf {host} nicht.", host=host)), 400
    if typ == "remote":
        with _db() as c, c:
            _person_upsert(c, target, p["id"], disp)
    return target, None, 200


def remote_search(uid, q):
    """Personensuche: genaue Adresse name@host einer verbundenen Instanz -> Treffer (Person und/oder Gruppe)."""
    out = []
    for typ, kind in (("remote", "user"), ("remote_group", "group")):
        name, host = _parse_target(typ, q)
        if not name:
            continue
        with _db() as c:
            p = peer_by(c, "host", host)
        if not p or p["status"] != "active":
            return []
        try:
            disp = _lookup(p, name, kind)
        except FedError:
            return out
        if disp is None:
            continue
        target = f"@{name}@{host}" if typ == "remote" else f"{name}@{host}"
        if typ == "remote":
            with _db() as c, c:
                _person_upsert(c, target, p["id"], disp)
        out.append({"type": typ, "target": target, "name": remote_label(target if typ == "remote" else "@" + target, disp if typ == "remote" else ""),
                    "host": host})
    return out


def fed_render(c, uid, fed):
    """Benachrichtigung zu einer Seite einer anderen Instanz: Titel nur, solange die Verbindung besteht und von dort noch
    etwas mit mir (oder meinen Gruppen) geteilt ist."""
    try:
        pid, rid = int(fed.get("peer")), int(fed.get("note"))
    except (TypeError, ValueError):
        return False, None, None
    p = peer_by(c, "id", pid)
    if not enabled() or not p or p["status"] != "active" or not _ok_rid(rid):
        return False, None, None
    gs = sorted(sharing.groups_of(c, uid))
    sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
          (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ")"
    # Freigabe-Benachrichtigung: genau diese Seite muss noch geteilt sein (BUG-037). Kommentare koennen auch an
    # Unterseiten haengen (deren IDs kennen wir nicht) - dort reicht irgendeine Freigabe dieser Instanz.
    exact = c.execute(sql + " AND note_id=? LIMIT 1", (pid, uid, *gs, rid)).fetchone()
    if not exact and (not fed.get("comment") or not c.execute(sql + " LIMIT 1", (pid, uid, *gs)).fetchone()):
        return False, None, None
    r = ref(pid, rid)
    block = fed.get("block") if isinstance(fed.get("block"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", fed.get("block")) else ""
    return True, {"id": r, "title": _clean_text(fed.get("title"), 300), "icon": "", "host": p["host"]}, f"/notes#{r}" + (f":{block}" if block else "")


sharing.REMOTE_TARGET = remote_target
sharing.REMOTE_SEARCH = remote_search
notify.FED_RENDER = fed_render


# =====================================================================================================
# Admin-Bereich (admin.py): Einladen, Verbinden, Bestaetigen, Trennen
# =====================================================================================================
def set_enabled(on):
    auth.save_setting("fed_enabled", bool(on))
    if on:
        own_key()


def admin_state():
    with _db() as c:
        ps = peers(c)
        logs = c.execute("SELECT ts, host, event, detail FROM fed_log ORDER BY id DESC LIMIT 200").fetchall()
        invites = c.execute("SELECT id, created, expires FROM fed_invite WHERE used_at IS NULL AND expires>? ORDER BY id DESC",
                            (time.time(),)).fetchall()
        counts = {r[0]: r[1] for r in c.execute(
            "SELECT p.id, (SELECT COUNT(*) FROM note_share s WHERE s.target_type IN ('remote','remote_group') AND right(s.target, length(p.host)+1)='@'||p.host) "
            "FROM fed_peer p")}
        inbound = {r[0]: r[1] for r in c.execute("SELECT peer_id, COUNT(*) FROM fed_inbound GROUP BY peer_id")}
        queued = {r[0]: r[1] for r in c.execute("SELECT peer_id, COUNT(*) FROM fed_outbox GROUP BY peer_id")}
    fp, err = None, None
    try:
        fp = own_fingerprint() if enabled() else None
    except FedError as ex:
        err = str(ex)
    for p in ps:
        p.update(shares=counts.get(p["id"], 0), inbound=inbound.get(p["id"], 0), queued=queued.get(p["id"], 0))
    return {"enabled": enabled(), "base_url": SELF_URL, "host": SELF_HOST, "fingerprint": fp, "key_error": err, "peers": ps,
            "log": [{"ts": r[0], "host": r[1], "event": r[2], "detail": r[3]} for r in logs],
            "invites": [{"id": r[0], "created": r[1], "expires": r[2]} for r in invites],
            "https": SELF_URL.startswith("https://") or ALLOW_HTTP}


def make_invite(by):
    """Neuer Einladungscode (einmalig, 24 h). Gespeichert nur als Hash."""
    code = secrets.token_urlsafe(24)
    with _db() as c, c:
        c.execute("INSERT INTO fed_invite (code_hash, created, expires, created_by) VALUES (?,?,?,?)",
                  (hashlib.sha256(code.encode()).hexdigest(), time.time(), time.time() + INVITE_TTL, by))
        log(c, "invite.created", None, by or "", host=SELF_HOST)
    return code


def drop_invite(iid):
    with _db() as c, c:
        return c.execute("DELETE FROM fed_invite WHERE id=? AND used_at IS NULL", (iid,)).rowcount


def preview(url):
    """Schritt 1 beim Verbinden: Angaben der anderen Instanz holen (Fingerprint zum Vergleichen). FedError."""
    base = norm_base(url)
    if not base:
        raise FedError(t("Adresse bitte als https://server angeben (ohne Pfad)."))
    if host_of(base) == SELF_HOST:
        raise FedError(t("Das ist dieser Server."))
    return fetch_info(base)


def connect(url, code, fingerprint_):
    """Schritt 2: Verbindungsanfrage (signiert, mit Code). fingerprint_: der vom Admin bestaetigte Fingerprint."""
    info = preview(url)
    if info["fingerprint"] != fingerprint_:
        raise FedError(t("Der Schlüssel der anderen Instanz hat sich seit der Anzeige geändert – bitte neu prüfen."))
    priv, pub = own_key()
    peer_tmp = {"base_url": info["base_url"], "public_key": info["public"]}
    body = {"base_url": SELF_URL, "name": title(), "key": fedsig.b64u(pub), "code": str(code or "").strip()}
    status, d = call_json(peer_tmp, "POST", "/fed/v1/connect", body=body)
    if status != 200:
        msg = {403: t("Einladungscode ungültig oder abgelaufen (oder Schlüssel passt nicht)."),
               409: t("Die Instanzen sind schon verbunden.")}.get(status) or f"HTTP {status}: {(d or {}).get('error', '')}"
        raise FedError(msg, status)
    now = time.time()
    with _db() as c, c:
        old = peer_by(c, "host", info["host"])
        if old and old["status"] == "active":
            raise FedError(t("Die Instanzen sind schon verbunden."))
        if old:
            c.execute("UPDATE fed_peer SET base_url=?, name=?, public_key=?, fingerprint=?, status='pending_out', created=?, confirmed_at=NULL, "
                      "last_error=NULL WHERE id=?", (info["base_url"], info["name"], info["public"], info["fingerprint"], now, old["id"]))
            pid = old["id"]
        else:
            pid = _free_id(c)
            if pid is None:
                raise FedError(t("Zu viele verbundene Instanzen."))
            c.execute("INSERT INTO fed_peer (id, host, base_url, name, public_key, fingerprint, status, created) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, info["host"], info["base_url"], info["name"], info["public"], info["fingerprint"], "pending_out", now))
        log(c, "connect.sent", peer_by(c, "id", pid), info["fingerprint"])
    return pid


def confirm(pid, by=""):
    """Admin bestaetigt eine eingegangene Anfrage -> aktiv; Gegenseite wird informiert. -> (ok, fehler der Zustellung)"""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] != "pending_in":
            return False, None
        c.execute("UPDATE fed_peer SET status='active', confirmed_at=? WHERE id=?", (time.time(), pid))
        log(c, "peer.confirmed", p, by)
    p["status"] = "active"
    try:
        status, _ = call_json(p, "POST", "/fed/v1/confirm", body={})
        err = None if status == 200 else f"HTTP {status}"
    except FedError as ex:
        err = str(ex)
    with _db() as c, c:
        _touch(c, p, err)
    return True, err


def refresh(pid):
    """Status bei der Gegenseite nachfragen (wartet sie noch / sind wir dort bestaetigt?)."""
    with _db() as c:
        p = peer_by(c, "id", pid)
    if not p or p["status"] not in ("pending_out", "active"):
        return None
    try:
        status, d = call_json(p, "GET", "/fed/v1/status")
    except FedError as ex:
        with _db() as c, c:
            _touch(c, p, ex)
        return f"error: {ex}"
    st = (d or {}).get("status") if status == 200 else None
    with _db() as c, c:
        _touch(c, p)
        if st == "active" and p["status"] == "pending_out":
            c.execute("UPDATE fed_peer SET status='active', confirmed_at=? WHERE id=?", (time.time(), pid))
            log(c, "peer.confirmed_remote", p, "status")
    return st or f"HTTP {status}"


def disconnect(pid, by=""):
    """Trennen (oder Anfrage ablehnen): sofort wirksam, Freigaben weg, Gegenseite best effort informieren."""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] == "disconnected":
            return False
        c.execute("UPDATE fed_peer SET status='disconnected' WHERE id=?", (pid,))
        n = cleanup_peer(c, p)
        log(c, "peer.disconnected", p, f"{by} shares={n}")
    try:
        call_json(p, "POST", "/fed/v1/disconnect", body={})
    except FedError:
        pass
    return True


def remove(pid):
    """Getrennte Gegenstelle ganz aus der Liste entfernen (Protokoll bleibt)."""
    with _db() as c, c:
        p = peer_by(c, "id", pid)
        if not p or p["status"] != "disconnected":
            return False
        cleanup_peer(c, p)
        c.execute("DELETE FROM fed_peer WHERE id=?", (pid,))
        log(c, "peer.removed", None, "", host=p["host"])
    return True
