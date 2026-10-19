"""Nextcloud-Dateien in Seiten (2.3, "B"): Konto verknuepfen, Dateien auswaehlen, Block "nextcloud", Vorschau/Download.

Entscheidung "Bequem": Vorschau und Download laufen mit den Rechten des AUTORS des Blocks (die Person, die ihn eingefuegt
hat) - alle, die die Seite sehen duerfen, sehen auch die Datei, ohne eigenes Nextcloud. Regeln (Sicherheit):
- Jede Person verknuepft IHRE Nextcloud (beliebige eigene Adresse, Login Flow v2, App-Passwort verschluesselt mit eigener
  Ableitung). Ohne Admin-Freigabe nur https und oeffentliche Adressen (netguard prueft die IP der tatsaechlichen
  Verbindung); interne Hosts nur ueber die Freigabe im Admin-Bereich ("Verbindungen der Benutzer"). Der Admin kann die
  Funktion abschalten (Einstellung "nc_files"). "Trennen" widerruft das App-Passwort in Nextcloud.
- "author" setzt NUR der Server (beim Speichern = die speichernde Person). Neue Bloecke mit fremdem Autor werden
  abgewiesen; bestehende Bloecke anderer bleiben unveraendert (der Server setzt ihre gespeicherten Daten wieder ein).
- Jeder akzeptierte Block bekommt eine Signatur "sig" = HMAC(Seite, Autor, Nextcloud, Datei-ID, Schalter). Der Proxy
  liefert nur Bloecke mit gueltiger Signatur fuer GENAU diese Seite - Bloecke, die auf anderem Weg in eine Seite kommen
  (Import, Duplikat, Kopie in eine andere Seite), zeigen nur eine Karte ohne Inhalt.
- Proxy: Leser braucht Leserecht auf die Seite; der Autor braucht weiterhin Bearbeiten-Recht auf die Seite und eine
  Verknuepfung mit genau dieser Nextcloud; Nextcloud prueft die Datei mit dem Konto des Autors. Getrennt, kein Zugriff
  mehr, Datei weg -> 404 (Karte ohne Inhalt).
- Veroeffentlichte Seiten ("public") und verbundene Saga-Server ("fed"): nur, wenn der Autor es je Block einschaltet.
- Vorschaubilder auf Platte (STATE_DIR/nc-cache, nicht im Backup, LRU mit Obergrenze NC_CACHE_MB); Metadaten 5 min im
  Speicher. Die Rechtepruefung laeuft trotzdem bei jedem Abruf.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote, unquote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

import auth
from db import connect as _db
from saga import backups, netguard
from saga.backups import BackupError
from saga.calendars import _admin_hosts
from saga.core import E, RESTORE_TEST, SECRET, STATE_DIR, _api_guard, _bad, _body, _json_load, _session_user, t
from saga.files import FILE_MAX, _sniff_image, store_upload, upload_owner
from saga.notes import _iter_blocks
from saga.sharing import RANK, Access, access, is_remote

router = APIRouter()

NEXTCLOUD_URL = (E.get("NEXTCLOUD_URL") or "").rstrip("/")         # Vorschlag fuer das Adressfeld (Host gilt als freigegeben)
CACHE_DIR = os.path.join(STATE_DIR, "nc-cache")
CACHE_MAX = max(1, int(E.get("NC_CACHE_MB", "300"))) * 1024 * 1024
DOWNLOAD_MAX = max(1, int(E.get("NC_DOWNLOAD_MAX_MB", "2048"))) * 1024 * 1024
FED_MAX = max(1, int(E.get("NC_FED_MAX_MB", "50"))) * 1024 * 1024    # Download ueber verbundene Server (gepuffert)
META_TTL = 300                                    # Metadaten/Rechte der Datei beim Autor (Sekunden)
BLOCKS_MAX = 200                                  # Nextcloud-Bloecke je Seite
PREVIEW_W = (64, 160, 320, 640, 1280, 2000)
PREVIEW_MAX = 12 * 1024 * 1024
LIST_MAX = 500
FILEID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
KINDS = ("file", "folder", "image")
DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
PROPS = ("<d:displayname/><d:getcontenttype/><d:getcontentlength/><d:getetag/><d:getlastmodified/><d:resourcetype/>"
         "<oc:fileid/><oc:size/><nc:has-preview/>")
XMLNS = 'xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns"'
UA = backups.UA.replace("(Backup)", "(Nextcloud)")


# =====================================================================================================
# Admin-Regeln, Adressen (SSRF)
# =====================================================================================================
def enabled():
    return bool(auth.settings().get("nc_files", True))


def _allowed_hosts():
    hosts = set(_admin_hosts())
    if NEXTCLOUD_URL:
        hosts.add(urlparse(NEXTCLOUD_URL).netloc.lower())
        hosts.add((urlparse(NEXTCLOUD_URL).hostname or "").lower())
    return hosts - {""}


def _host_free(host, port=None):
    hs = _allowed_hosts()
    host = str(host or "").lower().strip("[]")
    return host in hs or (port is not None and f"{host}:{port}" in hs)


def ip_ok(ip, host, port):
    """Verbindung erlaubt: oeffentliche Adresse, "interne Adressen fuer alle" oder freigegebener Host."""
    return netguard.is_public(ip) or bool(auth.settings().get("allow_private_hosts")) or _host_free(host, port)


def norm_server(url):
    """Eingabe -> (adresse, fehler). https, ohne Zugangsdaten/Abfrage; http nur fuer freigegebene Hosts."""
    u = str(url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    for cut in ("/index.php", "/remote.php", "/apps/", "/login"):
        if cut in u:
            u = u.split(cut)[0]
    u = backups.norm_url(u)
    if not u or len(u) > 300:
        return None, t("Adresse ungültig (z. B. https://cloud.example.org)")
    pu = urlparse(u)
    try:
        port = pu.port
    except ValueError:
        return None, t("Adresse ungültig (z. B. https://cloud.example.org)")
    if pu.scheme != "https" and not _host_free(pu.hostname, port or 80):
        return None, t("Nur https-Adressen erlaubt (http nur für vom Admin freigegebene interne Server)")
    return u, None


# =====================================================================================================
# Konto je Person
# =====================================================================================================
def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("hk-nextcloud|" + SECRET).encode()).digest()))


def account(uid):
    """-> {uid, server, login, dav_user, password, created, used} oder None."""
    if not uid or is_remote(uid):
        return None
    with _db() as c:
        r = c.execute("SELECT server, login, dav_user, secret, created, used FROM nc_account WHERE uid=?", (uid,)).fetchone()
    if not r:
        return None
    try:
        pw = _fernet().decrypt(r[3].encode()).decode()
    except Exception:  # noqa: BLE001 - Schluessel gewechselt: wie nicht verbunden
        return None
    return {"uid": uid, "server": r[0], "login": r[1], "dav_user": r[2], "password": pw, "created": r[4], "used": r[5]}


def _save_account(uid, server, login, dav_user, pw):
    with _db() as c, c:
        c.execute("INSERT INTO nc_account (uid, server, login, dav_user, secret, created, used) VALUES (?,?,?,?,?,?,NULL) "
                  "ON CONFLICT (uid) DO UPDATE SET server=EXCLUDED.server, login=EXCLUDED.login, dav_user=EXCLUDED.dav_user, "
                  "secret=EXCLUDED.secret, created=EXCLUDED.created, used=NULL",
                  (uid, server, login, dav_user, _fernet().encrypt(pw.encode()).decode(), time.time()))
    forget(uid)


_used = {}


def _touch(uid):
    """"zuletzt genutzt" (hoechstens einmal je Minute schreiben)."""
    now = time.time()
    if now - _used.get(uid, 0) < 60:
        return
    _used[uid] = now
    try:
        with _db() as c, c:
            c.execute("UPDATE nc_account SET used=? WHERE uid=?", (now, uid))
    except Exception:  # noqa: BLE001 - nur Anzeige
        pass


def disconnect(uid, revoke=True):
    """Verknuepfung loesen: App-Passwort widerrufen (bestmoeglich), Zeile, Speicher und Vorschauen des Kontos weg."""
    acct = account(uid)
    revoked = None
    if acct and revoke and not RESTORE_TEST:
        revoked = backups.nc_revoke(acct["server"], acct["login"], acct["password"], ip_ok=ip_ok)
    with _db() as c, c:
        c.execute("DELETE FROM nc_account WHERE uid=?", (uid,))
    forget(uid)
    flow_cancel(uid)
    return revoked


def revoke_on_delete(uid):
    """Vor der Kontoloeschung (saga/userdata.py): App-Passwort widerrufen, Vorschauen loeschen. Fehler egal."""
    try:
        disconnect(uid)
    except Exception:  # noqa: BLE001 - Loeschen geht immer vor
        pass


# =====================================================================================================
# Login Flow v2 (Teile aus saga/backups.py: detect, nc_user_id, nc_revoke, _client_for)
# =====================================================================================================
_flows = {}
_flow_lock = threading.Lock()


def flow_state(uid):
    with _flow_lock:
        f = dict(_flows.get(uid) or {"state": ""})
    if f.get("state") == "wait" and time.time() > f.get("until", 0):
        f.update(state="err", code="flow_expired")
    return f


def flow_cancel(uid):
    with _flow_lock:
        _flows.pop(uid, None)


def flow_start(uid, server):
    host = urlparse(server).hostname

    def go():
        with backups._client_for(host, timeout=20.0, ip_ok=ip_ok) as cl:
            return cl.post(server + "/index.php/login/v2", headers={"Accept": "application/json", "User-Agent": UA})
    r = backups._net_call(go)
    if r.status_code != 200:
        raise BackupError("http", status=r.status_code, what="login/v2")
    try:
        j = r.json()
        login, token, endpoint = str(j["login"]), str(j["poll"]["token"]), str(j["poll"]["endpoint"])
    except (ValueError, KeyError, TypeError):
        raise BackupError("http", status=r.status_code, what="login/v2")
    # Poll nur bei derselben Nextcloud, Login-Link nur http(s) auf demselben Host (kein javascript: o. ae.)
    if (urlparse(endpoint).hostname or "").lower() != (host or "").lower() or urlparse(endpoint).scheme != urlparse(server).scheme:
        endpoint = server + "/login/v2/poll"
    if urlparse(login).scheme not in ("https", "http") or (urlparse(login).hostname or "").lower() != (host or "").lower():
        raise BackupError("http", status=r.status_code, what="login/v2")
    fid = uuid.uuid4().hex
    with _flow_lock:
        _flows[uid] = {"state": "wait", "login": login, "until": time.time() + 20 * 60, "code": "", "p": {}, "id": fid}
    threading.Thread(target=_flow_poll, args=(uid, fid, server, endpoint, token), name="nextcloud-login", daemon=True).start()
    return login


def _flow_set(uid, fid, **kw):
    with _flow_lock:
        if (_flows.get(uid) or {}).get("id") == fid:
            _flows[uid].update(kw)


def _flow_poll(uid, fid, server, endpoint, token):
    host = urlparse(server).hostname
    while True:
        with _flow_lock:
            f = _flows.get(uid)
            if not f or f["id"] != fid:
                return
            if time.time() > f["until"]:
                f.update(state="err", code="flow_expired")
                return
        time.sleep(2)
        try:
            def go():
                with backups._client_for(host, timeout=15.0, ip_ok=ip_ok) as cl:
                    return cl.post(endpoint, data={"token": token}, headers={"Accept": "application/json", "User-Agent": UA})
            r = backups._net_call(go)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                raise BackupError("http", status=r.status_code, what="login/v2/poll")
            j = r.json()
            login, app_pw = str(j["loginName"]), str(j["appPassword"])
            dav_user = backups.nc_user_id(server, login, app_pw, ip_ok=ip_ok)
            with _flow_lock:                                  # inzwischen abgebrochen: neues App-Passwort gleich widerrufen
                still = (_flows.get(uid) or {}).get("id") == fid
            if not still:
                backups.nc_revoke(server, login, app_pw, ip_ok=ip_ok)
                return
            old = account(uid)
            if old and not RESTORE_TEST:
                backups.nc_revoke(old["server"], old["login"], old["password"], ip_ok=ip_ok)
            _save_account(uid, server, login, dav_user, app_pw)
            _flow_set(uid, fid, state="ok", code="")
            return
        except BackupError as ex:
            _flow_set(uid, fid, state="err", code=ex.code, p=ex.p)
            return
        except (ValueError, KeyError, TypeError):
            _flow_set(uid, fid, state="err", code="http", p={"status": 200, "what": "login/v2/poll"})
            return


# =====================================================================================================
# WebDAV: PROPFIND, SEARCH, Vorschau, Download (immer mit dem Konto acct)
# =====================================================================================================
def _client(acct, timeout=30.0):
    return backups._client_for(urlparse(acct["server"]).hostname, timeout=timeout, auth_=(acct["login"], acct["password"]), ip_ok=ip_ok)


def _files_root(acct):
    """Pfad-Praefix der Dateien des Kontos (auch bei Nextcloud unter einem Unterpfad)."""
    return urlparse(acct["server"]).path.rstrip("/") + "/remote.php/dav/files/" + acct["dav_user"].replace("/", "_") + "/"


def _origin(acct):
    pu = urlparse(acct["server"])
    return f"{pu.scheme}://{pu.netloc}"


def _check(r, what):
    if r.status_code in (401, 403):
        raise BackupError("auth")
    if r.status_code == 404:
        raise BackupError("http", status=404, what=what)
    if r.status_code not in (200, 207):
        raise BackupError("http", status=r.status_code, what=what)


def _multistatus(acct, raw):
    """207-Antwort -> Eintraege {fileid, name, path, dir, mime, size, etag, mtime, preview} (nur unterhalb der eigenen Dateien)."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise BackupError("http", status=207, what="XML")
    prefix = _files_root(acct)
    out = []
    for resp in root.findall("d:response", DAV_NS):
        href = unquote(urlparse(resp.findtext("d:href", "", DAV_NS)).path)
        if not href.startswith(prefix.rstrip("/")):
            continue
        rel = href[len(prefix):].strip("/") if href.startswith(prefix) else ""
        e = {"path": "/" + rel if rel else "/", "name": rel.rsplit("/", 1)[-1] if rel else "", "dir": False, "mime": "",
             "size": None, "etag": "", "mtime": None, "fileid": "", "preview": False}
        for ps in resp.findall("d:propstat", DAV_NS):
            if " 200" not in (ps.findtext("d:status", "", DAV_NS) or ""):
                continue
            p = ps.find("d:prop", DAV_NS)
            if p is None:
                continue
            if p.find("d:resourcetype/d:collection", DAV_NS) is not None:
                e["dir"] = True
            fid = (p.findtext("oc:fileid", "", DAV_NS) or "").strip()
            if FILEID_RE.match(fid):
                e["fileid"] = fid
            dn = p.findtext("d:displayname", None, DAV_NS)
            if dn:
                e["name"] = dn.strip()[:250]
            e["mime"] = (p.findtext("d:getcontenttype", "", DAV_NS) or e["mime"]).strip()[:120]
            for tag in ("d:getcontentlength", "oc:size"):
                v = (p.findtext(tag, "", DAV_NS) or "").strip()
                if v.isdigit() and e["size"] is None:
                    e["size"] = int(v)
            e["etag"] = (p.findtext("d:getetag", "", DAV_NS) or e["etag"]).strip().strip('"')[:80]
            lm = p.findtext("d:getlastmodified", "", DAV_NS)
            if lm:
                try:
                    e["mtime"] = parsedate_to_datetime(lm).timestamp()
                except (TypeError, ValueError):
                    pass
            if (p.findtext("nc:has-preview", "", DAV_NS) or "").strip().lower() == "true":
                e["preview"] = True
        if e["fileid"]:
            if e["dir"]:
                e["mime"] = "httpd/unix-directory"
            out.append(e)
    return out


def _dav(acct, method, url, body=None, headers=None, what=None):
    def go():
        with _client(acct) as cl:
            with cl.stream(method, url, content=body, headers=dict({"Content-Type": "application/xml; charset=utf-8",
                                                                    "User-Agent": UA}, **(headers or {}))) as r:
                _check(r, what or method)
                return backups._read_limited(r, 8 * 1024 * 1024, what or method)
    raw = backups._net_call(go)
    _touch(acct["uid"])
    return _multistatus(acct, raw)


def listdir(acct, path):
    path = "/" + "/".join(p for p in str(path or "").split("/") if p and p not in (".", ".."))
    url = _origin(acct) + quote(_files_root(acct) + path.strip("/")) + ("/" if path != "/" else "")
    body = f'<?xml version="1.0"?><d:propfind {XMLNS}><d:prop>{PROPS}</d:prop></d:propfind>'.encode()
    items = _dav(acct, "PROPFIND", url, body, {"Depth": "1"}, "PROPFIND")
    me = path.rstrip("/") or "/"
    return [e for e in items if (e["path"].rstrip("/") or "/") != me][:LIST_MAX]


def _xml_lit(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def search(acct, where, order=None, limit=50):
    scope = "/files/" + _xml_lit(acct["dav_user"])
    ob = (f"<d:orderby><d:order><d:prop>{order[0]}</d:prop><d:{order[1]}/></d:order></d:orderby>" if order else "<d:orderby/>")
    body = (f'<?xml version="1.0" encoding="UTF-8"?><d:searchrequest {XMLNS}><d:basicsearch>'
            f"<d:select><d:prop>{PROPS}</d:prop></d:select>"
            f"<d:from><d:scope><d:href>{scope}</d:href><d:depth>infinity</d:depth></d:scope></d:from>"
            f"<d:where>{where}</d:where>{ob}<d:limit><d:nresults>{int(limit)}</d:nresults></d:limit>"
            "</d:basicsearch></d:searchrequest>").encode()
    url = _origin(acct) + urlparse(acct["server"]).path.rstrip("/") + "/remote.php/dav/"
    return _dav(acct, "SEARCH", url, body, {"Content-Type": "text/xml; charset=utf-8"}, "SEARCH")


def _where_ids(ids):
    eqs = [f"<d:eq><d:prop><oc:fileid/></d:prop><d:literal>{int(i)}</d:literal></d:eq>" for i in ids]
    return eqs[0] if len(eqs) == 1 else "<d:or>" + "".join(eqs) + "</d:or>"


# Metadaten je (Konto, Nextcloud, Datei): (zeit, eintrag|None)
_meta = {}
_meta_lock = threading.Lock()


def forget(uid):
    """Speicher und Vorschauen eines Kontos vergessen (Trennen, neu verbinden, Kontoloeschung)."""
    with _meta_lock:
        for k in [k for k in _meta if k[0] == uid]:
            _meta.pop(k, None)
    pre = _uid_tag(uid)
    try:
        for n in os.listdir(CACHE_DIR):
            if n.startswith(pre):
                try:
                    os.remove(os.path.join(CACHE_DIR, n))
                except OSError:
                    pass
    except OSError:
        pass


def resolve(acct, fileids, fresh=False):
    """{fileid: eintrag|None} - EINE SEARCH-Anfrage fuer alle unbekannten (je Konto, 5 min im Speicher)."""
    now = time.time()
    out, need = {}, []
    with _meta_lock:
        if len(_meta) > 20000:
            _meta.clear()
        for f in dict.fromkeys(fileids):
            hit = _meta.get((acct["uid"], acct["server"], f))
            if hit and not fresh and now - hit[0] < META_TTL:
                out[f] = hit[1]
            else:
                need.append(f)
    for k in range(0, len(need), 50):
        part = need[k:k + 50]
        got = {e["fileid"]: e for e in search(acct, _where_ids(part), limit=len(part) + 5)}
        with _meta_lock:
            for f in part:
                out[f] = got.get(f)
                _meta[(acct["uid"], acct["server"], f)] = (now, got.get(f))
    return out


def resolve_one(acct, fileid):
    return resolve(acct, [fileid]).get(fileid)


def _uid_tag(uid):
    return hashlib.sha256(("nc|" + str(uid)).encode()).hexdigest()[:10] + "-"


def _cache_path(acct, meta, w):
    k = hashlib.sha256(f"{acct['server']}|{meta['fileid']}|{meta['etag']}|{meta['size']}|{w}".encode()).hexdigest()[:40]
    return os.path.join(CACHE_DIR, _uid_tag(acct["uid"]) + k)


_prune_lock = threading.Lock()
_last_prune = [0.0]


def prune_cache(limit=None):
    """LRU: aelteste (zuletzt benutzte) Vorschauen loeschen, bis die Grenze eingehalten ist."""
    limit = CACHE_MAX if limit is None else limit
    with _prune_lock:
        try:
            ents = []
            for n in os.listdir(CACHE_DIR):
                p = os.path.join(CACHE_DIR, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                ents.append((st.st_mtime, st.st_size, p))
        except OSError:
            return 0
        total, gone = sum(e[1] for e in ents), 0
        for _, size, p in sorted(ents):
            if total <= limit:
                break
            try:
                os.remove(p)
                total -= size
                gone += 1
            except OSError:
                pass
        _last_prune[0] = time.time()
        return gone


def preview(acct, meta, w):
    """-> (bytes, mime) oder None. Erst Platte, sonst Nextcloud (core/preview), bei Bildern ohne Vorschau das Original."""
    path = _cache_path(acct, meta, w)
    try:
        with open(path, "rb") as f:
            data = f.read()
        os.utime(path)                                  # zuletzt benutzt (LRU)
        img = _sniff_image(data[:16])
        if img:
            return data, img
    except OSError:
        pass
    url = (_origin(acct) + urlparse(acct["server"]).path.rstrip("/") + "/index.php/core/preview?"
           + f"fileId={int(meta['fileid'])}&x={w}&y={w}&a=1&forceIcon=0")

    def get(u, limit):
        def go():
            with _client(acct) as cl:
                with cl.stream("GET", u, headers={"User-Agent": UA, "Accept": "image/*"}) as r:
                    if r.status_code != 200:
                        return r.status_code, b""
                    return 200, backups._read_limited(r, limit, "preview")
        try:
            return backups._net_call(go)
        except BackupError:
            return 0, b""
    st, data = get(url, PREVIEW_MAX)
    img = _sniff_image(data[:16]) if st == 200 else None
    if not img and (meta.get("mime") or "").startswith("image/") and (meta.get("size") or 0) <= FILE_MAX:
        st, data = get(_origin(acct) + quote(_files_root(acct) + meta["path"].strip("/")), FILE_MAX + 1)
        img = _sniff_image(data[:16]) if st == 200 else None
    if not img:
        return None
    _touch(acct["uid"])
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = path + ".tmp" + uuid.uuid4().hex[:6]
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        if time.time() - _last_prune[0] > 30:
            threading.Thread(target=prune_cache, daemon=True).start()
    except OSError:
        pass
    return data, img


def _download_url(acct, meta):
    return _origin(acct) + quote(_files_root(acct) + meta["path"].strip("/"))


def download_stream(acct, meta):
    """Generator mit dem Dateiinhalt (Streaming, hoechstens DOWNLOAD_MAX)."""
    url = _download_url(acct, meta)
    cl = _client(acct, timeout=300.0)
    try:
        cm = cl.stream("GET", url, headers={"User-Agent": UA})
        r = cm.__enter__()
    except Exception:
        cl.close()
        raise
    if r.status_code != 200:
        cm.__exit__(None, None, None)
        cl.close()
        return None

    def gen():
        sent = 0
        try:
            for b in r.iter_bytes(256 * 1024):
                sent += len(b)
                if sent > DOWNLOAD_MAX:
                    break
                yield b
        finally:
            cm.__exit__(None, None, None)
            cl.close()
    _touch(acct["uid"])
    return gen()


def download_bytes(acct, meta, limit):
    def go():
        with _client(acct, timeout=300.0) as cl:
            with cl.stream("GET", _download_url(acct, meta), headers={"User-Agent": UA}) as r:
                if r.status_code != 200:
                    return None
                return backups._read_limited(r, limit, "GET")
    try:
        return backups._net_call(go)
    except BackupError:
        return None


# =====================================================================================================
# Block "nextcloud": Signatur, Pruefung beim Speichern, Suche im Seiteninhalt
# =====================================================================================================
def _sig_key():
    return hashlib.sha256(("hk-nextcloud-block|" + SECRET).encode()).digest()


def sign(nid, d):
    msg = f"{int(nid)}|{d.get('author') or ''}|{d.get('inst') or ''}|{d.get('fileid') or ''}|{1 if d.get('public') is True else 0}|{1 if d.get('fed') is True else 0}"
    return hmac.new(_sig_key(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def sig_ok(nid, d):
    s = d.get("sig")
    return isinstance(s, str) and bool(d.get("author")) and hmac.compare_digest(s, sign(nid, d))


def _key(d):
    return (str(d.get("author") or ""), str(d.get("inst") or ""), str(d.get("fileid") or ""), d.get("public") is True,
            d.get("fed") is True, str(d.get("sig") or ""))


def nc_blocks(body, fmt="blocks"):
    """Alle Nextcloud-Bloecke (auch in Spalten/Ausklappbar) -> Liste der Block-Dicts."""
    if fmt != "blocks":
        return []
    doc = _json_load(body, {}) if isinstance(body, str) else body
    try:
        return [b for b in _iter_blocks(doc.get("blocks") if isinstance(doc, dict) else [])
                if b.get("type") == "nextcloud" and isinstance(b.get("data"), dict)]
    except ValueError:
        return []


class Rejected(ValueError):
    def __init__(self, msg, status=403):
        super().__init__(msg)
        self.status = status


def check_body(uid, nid, old_body, old_fmt, new_body):
    """Beim Speichern (pages._note_update): Nextcloud-Bloecke pruefen -> neuer Inhalt (JSON). Rejected bei Faelschung.
    - gueltig signiert fuer diese Seite: bleibt (jede Person darf speichern; Schalter nur der Autor - sonst ungueltig)
    - bestehender Block (gleiche Daten wie im gespeicherten Stand): bleibt unveraendert (gespeicherte Daten werden
      wieder eingesetzt) - auch ohne Signatur (dann nur Karte)
    - neuer Block ohne Autor oder mit Autor = speichernde Person: nur mit eigener Verknuepfung zu genau dieser Nextcloud;
      der Server setzt author und sig
    - alles andere (fremder Autor, nicht im gespeicherten Stand): abgewiesen"""
    doc = json.loads(new_body)
    blocks = nc_blocks(doc)
    if not blocks:
        return new_body
    if len(blocks) > BLOCKS_MAX:
        raise Rejected(t("Höchstens {n} Nextcloud-Dateien je Seite.", n=BLOCKS_MAX), 400)
    old = {}                                            # Schluessel -> gespeicherte Fassungen (gleiche Datei mehrfach: der Reihe nach)
    for b in nc_blocks(old_body, old_fmt):
        od = b["data"]
        keys = [_key(od), _key(dict(od, sig=""))]       # ohne sig: Client kennt die Signatur noch nicht (eben eingefuegt)
        if is_remote(uid) and od.get("author"):         # fremde Instanz kennt den Autor nur als Platzhalter
            keys += [_key(dict(od, author=pseudo(od["author"]))), _key(dict(od, author=pseudo(od["author"]), sig=""))]
        for k in dict.fromkeys(keys):
            old.setdefault(k, []).append(od)
    used = {}
    acct = None
    for b in blocks:
        d = b["data"]
        k = _key(d)
        if k not in old:
            k = _key(dict(d, sig=""))
        if k in old:                                     # bestehender Block: gespeicherte Fassung bleibt
            i = used.get(k, 0)
            used[k] = i + 1
            b["data"] = json.loads(json.dumps(old[k][min(i, len(old[k]) - 1)]))
            continue
        if sig_ok(nid, d) and not ({"author", "inst", "fileid", "sig"} - set(d)):
            continue
        author = d.get("author")
        if author not in (None, uid) or is_remote(uid):
            raise Rejected(t("Nextcloud-Dateien anderer Personen lassen sich nicht einfügen oder ändern – nur Dateien aus deinem eigenen Nextcloud."))
        if not enabled():
            raise Rejected(t("Nextcloud-Dateien sind auf diesem Server abgeschaltet."))
        acct = acct or account(uid)
        if not acct:
            raise Rejected(t("Nextcloud nicht verbunden (Einstellungen → Nextcloud)."), 409)
        fid = str(d.get("fileid") or "")
        if not FILEID_RE.match(fid) or (str(d.get("inst") or "").rstrip("/") != acct["server"]):
            raise Rejected(t("Diese Datei liegt nicht in deinem verbundenen Nextcloud."), 400)
        clean = {"inst": acct["server"], "fileid": fid, "kind": d.get("kind") if d.get("kind") in KINDS else "file",
                 "name": str(d.get("name") or "")[:250], "mime": str(d.get("mime") or "")[:120],
                 "size": d.get("size") if isinstance(d.get("size"), int) and not isinstance(d.get("size"), bool) else None,
                 "mtime": d.get("mtime") if isinstance(d.get("mtime"), (int, float)) and not isinstance(d.get("mtime"), bool) else None,
                 "author": uid, "public": d.get("public") is True, "fed": d.get("fed") is True}
        clean["sig"] = sign(nid, clean)
        b["data"] = clean
    return json.dumps(doc, ensure_ascii=False)


def adopt(body, fmt, uid, nid):
    """Seite kopiert (Duplikat/Vorlage): eigene Bloecke der kopierenden Person fuer die neue Seite signieren, fremde
    entwerten (bleiben als Karte). -> neuer Inhalt oder der alte, wenn nichts zu tun war."""
    if fmt != "blocks" or '"nextcloud"' not in str(body or ""):
        return body
    doc = _json_load(body, {})
    changed = False
    for b in nc_blocks(doc):
        d = b["data"]
        if d.get("author") == uid and not is_remote(uid):
            s = sign(nid, d)
            changed = changed or d.get("sig") != s
            d["sig"] = s
        elif d.get("sig"):
            d["sig"] = ""
            changed = True
    return json.dumps(doc, ensure_ascii=False) if changed else body


def pseudo(uid):
    """Autor fuer andere Saga-Instanzen: nie der Anmeldename, sondern ein fester, nicht umkehrbarer Platzhalter."""
    return "~" + hmac.new(_sig_key(), ("author|" + str(uid)).encode(), hashlib.sha256).hexdigest()[:16]


def mask_authors(body, fmt):
    """Seiteninhalt fuer eine fremde Instanz: author -> pseudo(author) (die Signatur verraet nichts)."""
    if fmt != "blocks" or '"nextcloud"' not in str(body or ""):
        return body
    doc = _json_load(body, {})
    bl = nc_blocks(doc)
    for b in bl:
        if b["data"].get("author"):
            b["data"]["author"] = pseudo(b["data"]["author"])
    return json.dumps(doc, ensure_ascii=False) if bl else body


def find_block(body, fmt, nid, fileid, author):
    """Block mit dieser Datei und diesem Autor (Anmeldename oder - von anderen Instanzen - Platzhalter) und gueltiger
    Signatur fuer genau diese Seite."""
    for b in nc_blocks(body, fmt):
        d = b["data"]
        if str(d.get("fileid")) == fileid and d.get("author") and author in (d.get("author"), pseudo(d.get("author"))) and sig_ok(nid, d):
            return d
    return None


# =====================================================================================================
# Ausliefern (lokal, veroeffentlicht, foederiert)
# =====================================================================================================
def _thumb_w(w):
    try:
        w = int(w or 0)
    except (TypeError, ValueError):
        return 320
    return min((x for x in PREVIEW_W if x >= w), default=PREVIEW_W[-1]) if w > 0 else 320


def _author_acct(c, nid, d):
    """Konto des Autors, wenn er die Seite (noch) bearbeiten darf und mit genau dieser Nextcloud verbunden ist."""
    author = d.get("author")
    if not author or is_remote(author) or not enabled():
        return None
    if RANK.get(Access(c, author).role(nid), 0) < RANK["edit"]:
        return None
    acct = account(author)
    if not acct or acct["server"] != str(d.get("inst") or "").rstrip("/"):
        return None
    return acct


def locate(c, nid, owner, fileid, author, flag=None):
    """Block einer Seite -> (block, konto des autors) oder (None, None). flag: 'public' | 'fed' muss eingeschaltet sein."""
    if not FILEID_RE.match(fileid or "") or not author:
        return None, None
    r = c.execute("SELECT body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
    if not r:
        return None, None
    d = find_block(r[0], r[1], nid, fileid, author)
    if not d or (flag and d.get(flag) is not True):
        return None, None
    return d, _author_acct(c, nid, d)


_HDR = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox", "Referrer-Policy": "no-referrer"}


def _fname(name):
    s = re.sub(r"[^\w äöüÄÖÜß.()\-]+", "_", str(name or "datei")).strip(" ._")[:150] or "datei"
    return s


def _disposition(name):
    n = _fname(name)
    ascii_ = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", n) or "datei"
    return f"attachment; filename=\"{ascii_}\"; filename*=UTF-8''{quote(n)}"


def deliver(acct, d, mode, w, cache="private, max-age=300", buffered_max=None):
    """Vorschau (mode 'preview') oder Datei ('download') mit dem Konto des Autors -> Response (404 ohne Inhalt)."""
    try:
        meta = resolve_one(acct, str(d["fileid"]))
    except BackupError:
        meta = None
    if not meta:
        return Response(b"not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"})
    h = dict(_HDR, **{"Cache-Control": cache})
    if mode == "download":
        if meta["dir"]:
            return Response(b"not found", status_code=404, media_type="text/plain")
        h["Content-Disposition"] = _disposition(meta["name"] or d.get("name"))
        if buffered_max:
            data = download_bytes(acct, meta, buffered_max)
            if data is None:
                return Response(b"not found", status_code=404, media_type="text/plain")
            return Response(data, media_type="application/octet-stream", headers=h)
        try:
            gen = download_stream(acct, meta)
        except Exception:  # noqa: BLE001 - nicht erreichbar
            gen = None
        if gen is None:
            return Response(b"not found", status_code=404, media_type="text/plain")
        return StreamingResponse(gen, media_type="application/octet-stream", headers=h)
    if meta["dir"]:
        return Response(b"not found", status_code=404, media_type="text/plain")
    got = preview(acct, meta, _thumb_w(w))
    if not got:
        return Response(b"not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "private, max-age=60"})
    h["Content-Disposition"] = "inline"
    return Response(got[0], media_type=got[1], headers=h)


def status_plan(c, nid, owner, flag=None):
    """Teil 1 (mit Datenbank): Bloecke der Seite und Konten der Autoren. flag: nur Bloecke mit diesem Schalter.
    -> Plan fuer status_run() oder None (Seite fehlt)."""
    r = c.execute("SELECT body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
    if not r:
        return None
    groups, bad = {}, []
    for b in nc_blocks(r[0], r[1]):
        d = b["data"]
        if not sig_ok(nid, d) or (flag and d.get(flag) is not True):
            bad.append({"fileid": str(d.get("fileid") or ""), "author": str(d.get("author") or ""), "ok": False})
            continue
        groups.setdefault((d["author"], str(d.get("inst") or "")), []).append(d)
    return {"bad": bad, "groups": [(_author_acct(c, nid, ds[0]), ds) for ds in groups.values()]}


def status_run(plan):
    """Teil 2 (Netz, ohne Datenbank): je Autor EINE Suche. -> {"items": [{fileid, author, ok, name?, size?, ...}]}"""
    items = list(plan["bad"])
    for acct, ds in plan["groups"]:
        metas = {}
        if acct:
            try:
                metas = resolve(acct, [str(d["fileid"]) for d in ds])
            except BackupError:
                metas = {}
        for d in ds:
            m = metas.get(str(d["fileid"]))
            it = {"fileid": str(d["fileid"]), "author": d["author"], "ok": bool(m)}
            if m:
                it.update(name=m["name"], size=m["size"], mtime=m["mtime"], mime=m["mime"], dir=m["dir"], preview=m["preview"])
                if m["dir"]:
                    try:
                        ents = listdir(acct, m["path"])[:20]
                        it["entries"] = [{"name": e["name"], "dir": e["dir"], "size": e["size"]} for e in ents]
                    except BackupError:
                        it["entries"] = []
            items.append(it)
    return {"items": items}


# =====================================================================================================
# HTTP: Einstellungen -> Nextcloud
# =====================================================================================================
def _guard(request, need_enabled=True):
    uid = _api_guard(request)
    if not uid:
        return None, _bad("unauthorized", 401)
    if need_enabled and not enabled():
        return None, _bad(t("Nextcloud-Dateien sind auf diesem Server abgeschaltet."), 403)
    return uid, None


def _err(ex):
    if ex.code == "blocked":
        return _bad(t("Diese Adresse zeigt auf einen internen Server, der nicht freigegeben ist"), 400)
    if ex.code == "auth":
        return _bad(t("Nextcloud lehnt die Anmeldung ab – bitte neu verbinden."), 409)
    return _bad(backups.text(ex.code, ex.p), 502)


@router.get("/api/settings/nextcloud")
def api_settings(request: Request):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    acct = account(uid)
    fl = flow_state(uid)
    code = fl.get("code") or ""
    msg = (t("Anmeldung abgelaufen – bitte neu verbinden.") if code == "flow_expired" else backups.text(code, fl.get("p")) if code else "")
    if fl.get("state") in ("ok", "err"):
        flow_cancel(uid)
    return JSONResponse({"enabled": enabled(), "connected": bool(acct), "me": uid, "server": acct["server"] if acct else None,
                         "user": acct["login"] if acct else None, "since": acct["created"] if acct else None,
                         "used": acct["used"] if acct else None, "suggest": NEXTCLOUD_URL or None,
                         "flow": {"state": fl.get("state") or "", "login": fl.get("login") if fl.get("state") == "wait" else None,
                                  "message": msg}}, headers={"Cache-Control": "no-store"})


@router.post("/api/settings/nextcloud")
async def api_connect(request: Request):
    """{url}: Nextcloud erkennen -> Login Flow v2 ({flow: login-adresse}); die Anmeldung laeuft im neuen Tab."""
    uid, err = _guard(request)
    if err:
        return err
    if RESTORE_TEST:
        return _bad(t("Testwiederherstellung (SAGA_RESTORE_TEST=1): keine Verbindungen nach außen."), 409)
    b = await _body(request) or {}
    url, msg = norm_server(b.get("url") or NEXTCLOUD_URL)
    if not url:
        return _bad(msg)

    def work():
        pu = urlparse(url)
        try:                                   # interne Ziele ohne Freigabe: klare Meldung statt "keine Nextcloud"
            infos = socket.getaddrinfo(pu.hostname, pu.port or (443 if pu.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except (OSError, UnicodeError):
            raise BackupError("down")
        if not all(ip_ok(i[4][0], pu.hostname, pu.port or (443 if pu.scheme == "https" else 80)) for i in infos):
            raise BackupError("blocked")
        info = backups.detect(url, ip_ok=ip_ok)
        if not info["nextcloud"]:
            return _bad(t("Unter dieser Adresse antwortet keine Nextcloud."), 400)
        server, m2 = norm_server(info["server"])
        if not server:
            return _bad(m2)
        return JSONResponse({"flow": flow_start(uid, server)})
    try:
        return await asyncio.to_thread(work)
    except BackupError as ex:
        return _err(ex)


@router.post("/api/settings/nextcloud/cancel")
def api_cancel(request: Request):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    flow_cancel(uid)
    return JSONResponse({"ok": True})


@router.delete("/api/settings/nextcloud")
def api_disconnect(request: Request):
    uid, err = _guard(request, need_enabled=False)
    if err:
        return err
    return JSONResponse({"ok": True, "revoked": disconnect(uid)})


# =====================================================================================================
# HTTP: Dateiauswahl (immer das EIGENE Konto)
# =====================================================================================================
def _own(request):
    uid, err = _guard(request)
    if err:
        return None, None, err
    acct = account(uid)
    if not acct:
        return uid, None, _bad(t("Nextcloud nicht verbunden (Einstellungen → Nextcloud)."), 409)
    return uid, acct, None


def _pub(e, acct):
    return {"fileid": e["fileid"], "name": e["name"], "path": e["path"], "dir": e["dir"], "mime": e["mime"], "size": e["size"],
            "mtime": e["mtime"], "preview": e["preview"], "kind": block_kind(e), "inst": acct["server"]}


def block_kind(e):
    if e["dir"]:
        return "folder"
    return "image" if (e.get("mime") or "") in ("image/png", "image/jpeg", "image/gif", "image/webp") else "file"


@router.get("/api/nextcloud/list")
def api_list(request: Request, path: str = "/"):
    uid, acct, err = _own(request)
    if err:
        return err
    if len(path) > 2000:
        return _bad(t("ungültige Angabe"))
    try:
        items = listdir(acct, path)
    except BackupError as ex:
        if ex.code == "http" and ex.p.get("status") == 404:
            return _bad(t("Ordner nicht gefunden"), 404)
        return _err(ex)
    items.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return JSONResponse({"items": [_pub(e, acct) for e in items], "path": path}, headers={"Cache-Control": "no-store"})


@router.get("/api/nextcloud/search")
def api_search(request: Request, q: str = ""):
    """q leer: zuletzt geaenderte Dateien; sonst Name enthaelt q."""
    uid, acct, err = _own(request)
    if err:
        return err
    q = q.strip()[:100]
    try:
        if q:
            lit = _xml_lit("%" + q.replace("%", "") + "%")
            items = search(acct, f"<d:like><d:prop><d:displayname/></d:prop><d:literal>{lit}</d:literal></d:like>", limit=60)
        else:
            items = search(acct, "<d:not><d:is-collection/></d:not>", ("<d:getlastmodified/>", "descending"), limit=40)
    except BackupError as ex:
        return _err(ex)
    return JSONResponse({"items": [_pub(e, acct) for e in items]}, headers={"Cache-Control": "no-store"})


_LINK_RES = (re.compile(r"/(?:index\.php/)?f/([1-9][0-9]{0,19})(?:[/?#]|$)"),
             re.compile(r"[?&#]fileid=([1-9][0-9]{0,19})(?:&|$)"),
             re.compile(r"/apps/files/files/([1-9][0-9]{0,19})(?:[/?#]|$)"))


def link_fileid(acct, url):
    """Nextcloud-Link derselben Instanz -> Datei-ID oder None."""
    try:
        pu = urlparse(str(url or "").strip())
    except ValueError:
        return None
    su = urlparse(acct["server"])
    if pu.scheme not in ("http", "https") or (pu.netloc or "").lower() != su.netloc.lower():
        return None
    rest = pu.path
    if su.path and not rest.startswith(su.path.rstrip("/") + "/"):
        return None
    rest = rest[len(su.path.rstrip("/")):] + ("?" + pu.query if pu.query else "") + ("#" + pu.fragment if pu.fragment else "")
    for rx in _LINK_RES:
        m = rx.search(rest)
        if m:
            return m.group(1)
    q = parse_qs(pu.query).get("fileid")
    return q[0] if q and FILEID_RE.match(q[0]) else None


@router.post("/api/nextcloud/resolve")
async def api_resolve(request: Request):
    """{fileid} oder {url} (Link aus der eigenen Nextcloud) -> Daten fuer einen neuen Block (ohne author/sig - die setzt
    der Server beim Speichern der Seite). {match: false}, wenn der Link nicht zur verbundenen Nextcloud gehoert."""
    uid, acct, err = _own(request)
    if err:
        return err
    b = await _body(request) or {}
    fid = str(b.get("fileid") or "")
    if not fid and b.get("url"):
        fid = link_fileid(acct, b.get("url")) or ""
        if not fid:
            return JSONResponse({"match": False})
    if not FILEID_RE.match(fid):
        return _bad(t("ungültige Angabe"))
    try:
        m = await asyncio.to_thread(resolve, acct, [fid], True)
    except BackupError as ex:
        return _err(ex)
    e = m.get(fid)
    if not e:
        return _bad(t("Datei nicht gefunden oder kein Zugriff."), 404)
    p = _pub(e, acct)
    return JSONResponse({"match": True, "block": {"inst": acct["server"], "fileid": fid, "kind": p["kind"], "name": e["name"],
                                                  "mime": e["mime"], "size": e["size"], "mtime": e["mtime"]}, "item": p})


@router.get("/api/nextcloud/thumb/{fileid}")
def api_thumb(fileid: str, request: Request, w: int = 160):
    """Vorschau in der Dateiauswahl (eigenes Konto, X-Saga noetig)."""
    uid, acct, err = _own(request)
    if err:
        return err
    if not FILEID_RE.match(fileid):
        return _bad(t("ungültig"))
    try:
        meta = resolve_one(acct, fileid)
    except BackupError:
        meta = None
    got = preview(acct, meta, _thumb_w(w)) if meta and not meta["dir"] else None
    if not got:
        return _bad(t("Vorschau nicht verfügbar"), 404)
    return Response(got[0], media_type=got[1], headers=dict(_HDR, **{"Cache-Control": "private, max-age=300"}))


@router.post("/api/nextcloud/import")
async def api_import(request: Request):
    """"Als Kopie uebernehmen": Datei aus dem EIGENEN Nextcloud wird Saga-Datei ({fileid, note?}) - Antwort wie /api/files."""
    uid, acct, err = _own(request)
    if err:
        return err
    b = await _body(request) or {}
    fid = str(b.get("fileid") or "")
    if not FILEID_RE.match(fid):
        return _bad(t("ungültige Angabe"))
    note = b.get("note")
    try:
        meta = await asyncio.to_thread(resolve, acct, [fid], True)
    except BackupError as ex:
        return _err(ex)
    meta = meta.get(fid)
    if not meta or meta["dir"]:
        return _bad(t("Datei nicht gefunden oder kein Zugriff."), 404)
    if (meta.get("size") or 0) > FILE_MAX:
        return _bad(t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576), 413)
    data = await asyncio.to_thread(download_bytes, acct, meta, FILE_MAX)
    if data is None:
        return _bad(t("Datei nicht gefunden oder kein Zugriff."), 404)
    name = re.sub(r"[\x00-\x1f/\\]", "_", meta["name"] or "datei")[:120] or "datei"
    img = _sniff_image(data[:16])
    try:
        nid = int(note) if note not in (None, "") else None
    except (TypeError, ValueError):
        return _bad(t("ungültige Angabe"))
    if nid is not None and nid < 0:                     # Seite einer anderen Saga-Instanz: dort hochladen (saga.fed_pages)
        from saga import files
        if files.FED_UPLOAD is None:
            return _bad("not found", 404)
        return await asyncio.to_thread(files.FED_UPLOAD, uid, nid, name, data, bool(img))
    owner, oerr = upload_owner(uid, note)
    if oerr is not None:
        return oerr
    out, serr = await asyncio.to_thread(store_upload, owner, name, data, img)
    if serr is not None:
        return serr
    out["file"]["mime"] = img or "application/octet-stream"
    return JSONResponse(out)


# =====================================================================================================
# HTTP: Vorschau/Download fuer Leser einer Seite (Konto des Autors)
# =====================================================================================================
@router.get("/api/notes/{nid}/nc")
def api_page_status(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        owner, _, err = access(c, uid, nid, "view")
        if err is not None:
            return err
        plan = status_plan(c, nid, owner)
    if plan is None:
        return _bad("not found", 404)
    return JSONResponse(dict(status_run(plan), enabled=enabled()), headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}/nc/{fileid}")
def api_page_file(nid: int, fileid: str, request: Request, a: str = "", w: int = 0, dl: int = 0):
    """Vorschau (?w=) bzw. Download (?dl=1) einer Nextcloud-Datei der Seite - Sitzung reicht (<img>), Leserecht noetig."""
    uid = _session_user(request)
    if not uid:
        return Response(b"not found", status_code=404, media_type="text/plain")
    with _db() as c:
        owner, _, err = access(c, uid, nid, "view")
        if err is not None:
            return Response(b"not found", status_code=404, media_type="text/plain")
        d, acct = locate(c, nid, owner, fileid, a)
    if not d or not acct:
        return Response(b"not found", status_code=404, media_type="text/plain", headers={"Cache-Control": "no-store"})
    return deliver(acct, d, "download" if dl else "preview", w)


def fed_status_plan(c, principal, nid):
    """Fuer saga.fed_pages (Eigentuemer-Server): Status nur fuer Bloecke mit Schalter "fed"."""
    owner, _, err = access(c, principal, nid, "view")
    if err is not None:
        return None
    return status_plan(c, nid, owner, "fed")


def fed_locate(c, principal, nid, fileid, author):
    """Fuer saga.fed_pages: Block mit Schalter "fed" einer Seite, die principal sehen darf -> (block, konto) oder (None, None)."""
    owner, _, err = access(c, principal, nid, "view")
    if err is not None:
        return None, None
    return locate(c, nid, owner, fileid, author, "fed")


def block_md(d):
    """Export: Link statt Kopie."""
    name = str(d.get("name") or "").replace("&amp;", "&") or t("Nextcloud-Datei")
    inst, fid = str(d.get("inst") or ""), str(d.get("fileid") or "")
    url = f"{inst}/f/{fid}" if inst.startswith(("https://", "http://")) and FILEID_RE.match(fid) else ""
    icon = "📁" if d.get("kind") == "folder" else "🖼" if d.get("kind") == "image" else "📄"
    return f"[{icon} {name}]({url})" if url else f"{icon} {name}"
