"""Verschluesselte Backups auf ein WebDAV-Ziel (2.3, Admin-Bereich "Backups").

- Ziel: generisch WebDAV (Nextcloud, ownCloud, Storage-Boxen ...). Nextcloud erkennt Saga ueber /status.php: Anmeldung per
  Login Flow v2 (gespeichert werden nur Anmeldename + App-Passwort, verschluesselt wie die anderen Admin-Geheimnisse;
  "Trennen" widerruft das App-Passwort), grosse Dateien mit Chunking v2. Sonst Benutzer/Passwort + PUT.
  Abrufe ueber netguard: der Host der vom Admin eingetragenen Adresse gilt als freigegeben (auch intern, typisch im
  Homelab), alles andere nur mit oeffentlicher Adresse; keine Weiterleitungen.
- Verschluesselung mit age (X25519, oeffentliche Schluessel, mehrere Empfaenger): Saga kennt nie einen privaten
  Schluessel - ein uebernommener Server oder ein mitgelesenes Ziel kann alte Backups nicht entschluesseln.
  Wiederherstellung auch ohne Saga: age -d -i schluessel.txt datei.age > datei.
- Ein Lauf = Ordner saga-<JJJJMMTT-hhmmss>/ im Ziel: db.dump.age (pg_dump, vorher mit pg_restore --list geprueft),
  dateien.tar.age (files ohne thumbs, chat, svc-icons), geheimnisse.env.age (SECRET_KEY, CAL_SECRET_KEY; abschaltbar),
  manifest.json.age (Pruefsummen der Klartexte) und ZULETZT manifest.json (unverschluesselt, ohne Personendaten und
  Hostnamen: Version, Schema, Groessen und SHA-256 der verschluesselten Dateien, Empfaenger-Fingerabdruecke).
  Ohne manifest.json gilt ein Lauf als unvollstaendig (wird nach einem Tag entfernt).
- Nach jedem Upload: Groesse per PROPFIND, bis BACKUP_VERIFY_MB (und sonntags immer) erneut herunterladen + SHA-256.
- Zeitplan: Hintergrund-Thread, genau ein Laeufer je Instanz (pg_try_advisory_lock), nice/ionice; Aufbewahrung GFS
  (taeglich/woechentlich/monatlich). Vor dem Hochladen Platz im Ziel pruefen (quota-available-bytes).
- Fehler und "seit 48 h kein Erfolg": Benachrichtigung an alle Admins (Glocke + Push, Art backup.failed), optional Webhook.
- SAGA_RESTORE_TEST=1 (Testwiederherstellung auf einem zweiten Server): kein Zeitplan, keine ausgehenden Verbindungen.
- Das lokale db.backup()/db.restore() (Backups vor Migrationen, run.py backup/restore) bleibt davon unberuehrt.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urlparse

import httpx
import psycopg

import auth
import db
from saga import netguard, notify
from saga.core import ADMIN_URL, CA_FILE, E, RESTORE_TEST, SECRET, STATE_DIR, TZ, t
from version import VERSION

LOCK_ID = 424250                                   # pg_advisory_lock: genau ein Backup-Laeufer je Datenbank
MB = 1024 * 1024
CHUNK_MIN = int(float(E.get("BACKUP_CHUNK_MIN_MB", "10")) * MB)      # Nextcloud: ab dieser Groesse Chunking v2
CHUNK_SIZE = max(5 * MB, int(float(E.get("BACKUP_CHUNK_MB", "32")) * MB))   # Teile 5 MB ... 5 GB (Nextcloud)
VERIFY_MAX = int(float(E.get("BACKUP_VERIFY_MB", "512")) * MB)      # bis hierhin nach dem Upload ganz nachpruefen
TICK = max(1.0, float(E.get("BACKUP_TICK", "60")))                  # Zeitplan: so oft nachsehen (Sekunden)
STALE_H = 48                                       # so lange ohne Erfolg -> Benachrichtigung
HISTORY_KEEP = 200                                 # Zeilen in backup_run
RUN_RE = re.compile(r"^saga-(\d{8})-(\d{6})$")
PARTS = ("db.dump.age", "dateien.tar.age", "geheimnisse.env.age", "manifest.json.age")
DATA_DIRS = ("files", "chat", "svc-icons")        # unter STATE_DIR; files/thumbs entsteht neu
UA = f"Saga/{VERSION} (Backup)"
FORMAT = "saga-backup/1"
SECRET_KEYS = ("password", "webhook")              # in auth.SECRET_FIELDS (Abschnitt "backup")
RECIP_RE = re.compile(r"^(age1[0-9a-z]{58}|ssh-ed25519 [A-Za-z0-9+/=]{20,200}|ssh-rsa [A-Za-z0-9+/=]{100,1200})$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAV_NS = {"d": "DAV:", "oc": "http://owncloud.org/ns"}
PROPFIND = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:resourcetype/>'
            b'<d:getcontentlength/><d:quota-available-bytes/><oc:size/></d:prop></d:propfind>')


def tmp_dir():
    return os.path.join(db.BACKUP_DIR, "extern-tmp")


# =====================================================================================================
# Meldungen (Kennung + Werte gespeichert, Text erst beim Anzeigen in der Sprache des Lesers)
# =====================================================================================================
class BackupError(RuntimeError):
    def __init__(self, code, **p):
        super().__init__(code)
        self.code, self.p = code, p

    def as_json(self):
        return json.dumps({"code": self.code, "p": self.p}, ensure_ascii=False)


def text(code, p=None):
    """Kennung -> Satz in der Sprache der Anfrage (Fehler, Schritte)."""
    p = dict(p or {})
    if code == "down":
        return t("Ziel nicht erreichbar.")
    if code == "timeout":
        return t("Ziel antwortet nicht (Zeitüberschreitung).")
    if code == "blocked":
        return t("Adresse nicht erlaubt.")
    if code == "auth":
        return t("Anmeldung am Ziel abgelehnt – Zugangsdaten prüfen oder neu verbinden.")
    if code == "full":
        return t("Ziel voll: {need} nötig, {free} frei.", need=size_text(p.get("need")), free=size_text(p.get("free")))
    if code == "http":
        return t("Ziel meldet HTTP {status} ({what}).", status=p.get("status", "?"), what=p.get("what", ""))
    if code == "notarget":
        return t("Kein Ziel eingerichtet.")
    if code == "nokey":
        return t("Kein öffentlicher Schlüssel eingetragen – ohne Schlüssel sichert Saga nicht.")
    if code == "dump":
        return t("Datenbank-Sicherung fehlgeschlagen: {detail}", detail=p.get("detail", ""))
    if code == "files":
        return t("Dateien konnten nicht gepackt werden: {detail}", detail=p.get("detail", ""))
    if code == "age":
        return t("Verschlüsselung fehlgeschlagen: {detail}", detail=p.get("detail", ""))
    if code == "checksum":
        return t("Prüfsumme stimmt nicht: {name}", name=p.get("name", ""))
    if code == "size":
        return t("Größe im Ziel stimmt nicht: {name}", name=p.get("name", ""))
    if code == "aborted":
        return t("Abgebrochen (Saga wurde beendet).")
    if code == "busy":
        return t("Es läuft schon ein Backup.")
    if code == "stale":
        return t("Seit {h} Stunden kein erfolgreiches Backup.", h=p.get("h", STALE_H))
    if code == "failed":
        return t("Backup fehlgeschlagen: {detail}", detail=text(p.get("code", "other"), p.get("p")))
    if code == "step.db":
        return t("Datenbank sichern")
    if code == "step.files":
        return t("Dateien packen")
    if code == "step.secrets":
        return t("Geheimnisse verschlüsseln")
    if code == "step.upload":
        return t("Hochladen: {name} ({pct} %)", name=p.get("name", ""), pct=p.get("pct", 0))
    if code == "step.verify":
        return t("Prüfen: {name}", name=p.get("name", ""))
    if code == "step.prune":
        return t("Alte Backups aufräumen")
    return t("Unerwarteter Fehler: {detail}", detail=p.get("detail", code))


def text_json(raw):
    try:
        d = json.loads(raw or "{}")
    except ValueError:
        return str(raw or "")
    return text(d.get("code", ""), d.get("p")) if d.get("code") else ""


def size_text(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return "?"


# =====================================================================================================
# Einstellungen und Zustand
# =====================================================================================================
def config():
    """Abschnitt "backup" (Standardwerte aus auth.DEFAULTS, Geheimnisse entschluesselt)."""
    return dict(auth.settings(fresh=True).get("backup") or {})


def save_config(cfg):
    """Ganzen Abschnitt speichern - Geheimnisse verschluesselt, leer = wirklich leer (anders als auth.save_setting)."""
    v = dict(cfg)
    for f in SECRET_KEYS:
        v[f] = auth._enc(v.get(f) or "")
    with db.connect() as c, c:
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", ("backup", json.dumps(v, ensure_ascii=False)))
    auth._settings_cache["v"] = None


def state():
    with db.connect() as c:
        r = c.execute("SELECT v FROM setting WHERE k='backup_state'").fetchone()
    try:
        return json.loads(r[0]) if r else {}
    except ValueError:
        return {}


def set_state(**kw):
    with db.connect() as c, c:
        r = c.execute("SELECT v FROM setting WHERE k='backup_state'").fetchone()
        try:
            cur = json.loads(r[0]) if r else {}
        except ValueError:
            cur = {}
        cur.update(kw)
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES (?, ?)", ("backup_state", json.dumps(cur)))


def has_target(cfg):
    return bool(cfg.get("mode") and (cfg.get("server") if cfg.get("mode") == "nextcloud" else cfg.get("url")))


def recipients(cfg):
    return [r for r in (cfg.get("recipients") or []) if isinstance(r, str) and RECIP_RE.match(r)]


def fingerprint(r):
    """Kurzer Fingerabdruck eines Empfaengers (steht im Manifest und in der Liste)."""
    return hashlib.sha256(" ".join(str(r).split()[:2]).encode()).hexdigest()[:16]


def short_key(r):
    r = str(r)
    return r if len(r) <= 24 else r[:14] + "…" + r[-6:]


# =====================================================================================================
# age
# =====================================================================================================
def age_ok():
    return bool(shutil.which("age")) and bool(shutil.which("age-keygen"))


def norm_recipient(s):
    """Eingabe -> Empfaenger (age1... oder ssh-ed25519/ssh-rsa ohne Kommentar) oder None."""
    s = " ".join(str(s or "").strip().split())
    parts = s.split(" ")
    if parts and parts[0].startswith("ssh-") and len(parts) >= 2:
        s = parts[0] + " " + parts[1]
    if not RECIP_RE.match(s):
        return None
    try:
        r = subprocess.run(["age", "-e", "-r", s], input=b"", capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return s if r.returncode == 0 else None


def keygen():
    """Neues Schluesselpaar -> (privat als Text, oeffentlich). Der private Schluessel wird nirgends gespeichert."""
    r = subprocess.run(["age-keygen"], capture_output=True, timeout=20, check=True)
    priv = r.stdout.decode()
    m = re.search(r"public key: (age1[0-9a-z]{58})", priv + r.stderr.decode())
    if not m:
        line = next(x for x in priv.splitlines() if x.startswith("AGE-SECRET-KEY-"))
        m = re.match(r"(age1[0-9a-z]{58})", subprocess.run(["age-keygen", "-y"], input=line.encode() + b"\n", capture_output=True,
                                                            timeout=20, check=True).stdout.decode().strip())
    return priv, m.group(1)


def _age_enc_cmd(recips, out):
    cmd = _nice() + ["age", "-e"]
    for r in recips:
        cmd += ["-r", r]
    return cmd + ["-o", out]


def _nice():
    return ["ionice", "-c", "2", "-n", "7"] if shutil.which("ionice") else []


def _encrypt_file(src, dst, recips):
    with open(src, "rb") as f:
        r = subprocess.run(_age_enc_cmd(recips, dst), stdin=f, capture_output=True, timeout=6 * 3600)
    if r.returncode:
        raise BackupError("age", detail=r.stderr.decode("utf-8", "replace").strip()[:200])


def _encrypt_bytes(data, dst, recips):
    r = subprocess.run(_age_enc_cmd(recips, dst), input=data, capture_output=True, timeout=600)
    if r.returncode:
        raise BackupError("age", detail=r.stderr.decode("utf-8", "replace").strip()[:200])


def decrypt(src, dst, keyfile):
    """age -d -i keyfile src > dst (dst '-' = Rueckgabe als bytes)."""
    if dst == "-":
        r = subprocess.run(["age", "-d", "-i", keyfile, src], capture_output=True, timeout=600)
        if r.returncode:
            raise RuntimeError("age: " + r.stderr.decode("utf-8", "replace").strip()[:300])
        return r.stdout
    r = subprocess.run(["age", "-d", "-i", keyfile, "-o", dst, src], capture_output=True, timeout=6 * 3600)
    if r.returncode:
        raise RuntimeError("age: " + r.stderr.decode("utf-8", "replace").strip()[:300])
    return dst


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(MB), b""):
            h.update(b)
    return h.hexdigest()


# =====================================================================================================
# WebDAV-Ziel (Nextcloud mit Chunking v2, sonst PUT). Kleine Schnittstelle: weitere Ziele (S3) spaeter daneben.
# =====================================================================================================
def _tls():
    import ssl
    return ssl.create_default_context(cafile=CA_FILE) if os.path.isfile(CA_FILE) else ssl.create_default_context()


def _client_for(host, timeout=60.0, auth_=None, ip_ok=None):
    """ip_ok(ip, host, port): eigene Freigabe-Regel (persoenliche Backups, saga/userbackup.py); Standard (Admin-Ziel):
    der eingetragene Host ist frei, alles andere nur mit oeffentlicher Adresse."""
    host = (host or "").lower()

    def admin_ok(ip, h, port):
        return str(h).lower() == host or netguard.is_public(ip)
    return netguard.guarded_client(ip_ok or admin_ok, transport_kw={"verify": _tls()}, timeout=httpx.Timeout(timeout, connect=15.0),
                                   follow_redirects=False, auth=auth_, headers={"User-Agent": UA})


def _net_call(fn):
    """httpx-Fehler -> BackupError (ohne Adressen und Zugangsdaten im Text)."""
    try:
        return fn()
    except BackupError:
        raise
    except httpx.TimeoutException:
        raise BackupError("timeout")
    except (httpx.HTTPError, netguard.Blocked) as ex:
        if isinstance(ex, netguard.Blocked) or isinstance(getattr(ex, "__cause__", None), netguard.Blocked) or "nicht freigegeben" in str(ex):
            raise BackupError("blocked")
        raise BackupError("down")


def _q(path):
    return "/".join(quote(p, safe="") for p in str(path).strip("/").split("/") if p)


class Dav:
    """WebDAV-Ziel. cfg: mode (nextcloud|webdav), server/url, user, password, dav_user, folder.
    ip_ok: Freigabe-Regel fuer Verbindungen (siehe _client_for; persoenliche Backups: strenger)."""

    def __init__(self, cfg, ip_ok=None):
        self.mode = cfg.get("mode") or "webdav"
        if self.mode == "nextcloud":
            self.server = str(cfg.get("server") or "").rstrip("/")
            self.base = f"{self.server}/remote.php/dav/files/{quote(cfg.get('dav_user') or cfg.get('user') or '', safe='')}/"
            self.dav_user = cfg.get("dav_user") or cfg.get("user") or ""
        else:
            self.server = ""
            self.base = str(cfg.get("url") or "").rstrip("/") + "/"
            self.dav_user = ""
        folder = _q(cfg.get("folder") or "")
        self.root = self.base + (folder + "/" if folder else "")
        self.folder = folder
        self.auth = (cfg.get("user") or "", cfg.get("password") or "") if cfg.get("user") else None
        self.host = (urlparse(self.base).hostname or "").lower()
        self._cl = None
        self.ip_ok = ip_ok
        self.chunked = 0                          # Anzahl Uploads mit Chunking v2 (Tests, Anzeige)

    def client(self):
        if self._cl is None:
            self._cl = _client_for(self.host, auth_=self.auth, ip_ok=self.ip_ok)
        return self._cl

    def close(self):
        if self._cl is not None:
            self._cl.close()
            self._cl = None

    def url(self, *parts):
        return self.root + "/".join(_q(p) for p in parts if p)

    def _check(self, r, what, ok=(200, 201, 204, 207)):
        if r.status_code in ok:
            return r
        if r.status_code in (401, 403):
            raise BackupError("auth")
        if r.status_code == 507:
            raise BackupError("full", need=None, free=0)
        raise BackupError("http", status=r.status_code, what=what)

    def req(self, method, url, what=None, ok=(200, 201, 204, 207), timeout=None, **kw):
        def go():
            if timeout:
                kw["timeout"] = httpx.Timeout(timeout, connect=15.0)
            r = self.client().request(method, url, **kw)
            return self._check(r, what or method, ok)
        return _net_call(go)

    # --- Lesen ---
    def propfind(self, url, depth=1):
        r = self.req("PROPFIND", url, "PROPFIND", ok=(207,), content=PROPFIND,
                     headers={"Depth": str(depth), "Content-Type": "application/xml; charset=utf-8"})
        if len(r.content) > 8 * MB:
            raise BackupError("http", status=r.status_code, what="PROPFIND")
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            raise BackupError("http", status=r.status_code, what="PROPFIND")
        me = unquote(urlparse(url).path).rstrip("/")
        out = []
        for resp in root.findall("d:response", DAV_NS):
            href = unquote(urlparse(resp.findtext("d:href", "", DAV_NS)).path)
            ent = {"path": href.rstrip("/"), "name": href.rstrip("/").rsplit("/", 1)[-1], "dir": False, "size": None, "quota": None,
                   "self": href.rstrip("/") == me}
            for ps in resp.findall("d:propstat", DAV_NS):
                if " 200" not in (ps.findtext("d:status", "", DAV_NS) or ""):
                    continue
                p = ps.find("d:prop", DAV_NS)
                if p is None:
                    continue
                if p.find("d:resourcetype/d:collection", DAV_NS) is not None:
                    ent["dir"] = True
                for tag in ("d:getcontentlength", "oc:size"):
                    v = p.findtext(tag, None, DAV_NS)
                    if v and v.strip().lstrip("-").isdigit() and ent["size"] is None:
                        ent["size"] = int(v)
                q = p.findtext("d:quota-available-bytes", None, DAV_NS)
                if q and q.strip().lstrip("-").isdigit():
                    ent["quota"] = int(q)
            out.append(ent)
        return out

    def stat(self, url):
        """-> Eintrag oder None (fehlt)."""
        try:
            got = self.propfind(url, 0)
        except BackupError as ex:
            if ex.code == "http" and ex.p.get("status") == 404:
                return None
            raise
        return got[0] if got else None

    def listdir(self, url):
        return [e for e in self.propfind(url, 1) if not e["self"]]

    def free_space(self):
        """Freier Platz im Backup-Ordner (Bytes) oder None (unbekannt/unbegrenzt; Nextcloud meldet dann negative Werte)."""
        e = self.stat(self.root) or self.stat(self.base)
        q = e.get("quota") if e else None
        return q if isinstance(q, int) and q >= 0 else None

    def get_to(self, url, sink):
        """Datei streamen; sink(bytes) je Stueck."""
        def go():
            with self.client().stream("GET", url, timeout=httpx.Timeout(300.0, connect=15.0)) as r:
                self._check(r, "GET", ok=(200,))
                for b in r.iter_bytes(MB):
                    sink(b)
        _net_call(go)

    def get_bytes(self, url, limit=8 * MB):
        buf = bytearray()

        def sink(b):
            buf.extend(b)
            if len(buf) > limit:
                raise BackupError("http", status=413, what="GET")
        self.get_to(url, sink)
        return bytes(buf)

    # --- Schreiben ---
    def mkcol(self, url):
        r = self.req("MKCOL", url, "MKCOL", ok=(201, 405))
        return r.status_code == 201

    def ensure_root(self):
        """Backup-Ordner (mit Eltern) anlegen; vorhandene Ordner sind in Ordnung."""
        if not self.folder:
            return
        cur = self.base
        for seg in self.folder.split("/"):
            cur += seg + "/"
            self.mkcol(cur)

    def delete(self, url):
        self.req("DELETE", url, "DELETE", ok=(200, 204, 404))

    def put_file(self, path, url, progress=None):
        size = os.path.getsize(path)
        if self.mode == "nextcloud" and size >= CHUNK_MIN and self.dav_user:
            return self._put_chunked(path, url, size, progress)

        def body():
            sent = 0
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(MB), b""):
                    sent += len(b)
                    if progress:
                        progress(sent, size)
                    yield b
        self.req("PUT", url, "PUT", ok=(200, 201, 204), timeout=600.0, content=body(),
                 headers={"Content-Length": str(size), "Content-Type": "application/octet-stream"})

    def _put_chunked(self, path, url, size, progress=None):
        """Nextcloud Chunking v2: MKCOL uploads/<user>/<id>, Teile 00001 ..., MOVE .file -> Ziel."""
        up = f"{self.server}/remote.php/dav/uploads/{quote(self.dav_user, safe='')}/saga-{uuid.uuid4().hex}"
        h = {"Destination": url, "OC-Total-Length": str(size)}
        self.req("MKCOL", up, "MKCOL (Upload)", ok=(201,), headers=h)
        try:
            sent = 0
            with open(path, "rb") as f:
                n = 0
                while sent < size:
                    n += 1
                    part = min(CHUNK_SIZE, size - sent)

                    def body(left=part):
                        nonlocal sent
                        while left > 0:
                            b = f.read(min(MB, left))
                            if not b:
                                break
                            left -= len(b)
                            sent += len(b)
                            if progress:
                                progress(sent, size)
                            yield b
                    self.req("PUT", f"{up}/{n:05d}", "PUT (Teil)", ok=(200, 201, 204), timeout=600.0, content=body(),
                             headers=dict(h, **{"Content-Length": str(part), "Content-Type": "application/octet-stream"}))
            self.req("MOVE", f"{up}/.file", "MOVE", ok=(200, 201, 204), timeout=1800.0, headers=dict(h, Overwrite="T"))
            self.chunked += 1
        except BaseException:
            try:
                self.req("DELETE", up, "DELETE", ok=(200, 204, 404))
            except Exception:  # noqa: BLE001 - Nextcloud raeumt verwaiste Uploads nach 24 h selbst weg
                pass
            raise


# =====================================================================================================
# Nextcloud: Erkennung, Login Flow v2, Benutzer-ID, App-Passwort widerrufen
# =====================================================================================================
def norm_url(url):
    """Admin-Eingabe -> 'https://host[:port][/pfad]' ohne Abfrage/Anker/Zugangsdaten oder None."""
    u = urlparse(str(url or "").strip())
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query or u.fragment:
        return None
    return f"{u.scheme}://{u.netloc.lower()}{u.path}".rstrip("/")


def detect(url, ip_ok=None):
    """-> {'nextcloud': bool, 'server': ..., 'version': ...}. Nextcloud = /status.php mit installed=true.
    Eine WebDAV-Adresse von Nextcloud (.../remote.php/...) zaehlt ebenfalls (Server = Teil davor)."""
    server = url.split("/remote.php/")[0] if "/remote.php/" in url else url
    host = urlparse(server).hostname or ""

    def go():
        with _client_for(host, timeout=15.0, ip_ok=ip_ok) as cl:
            return cl.get(server + "/status.php", headers={"Accept": "application/json"})
    try:
        r = _net_call(go)
    except BackupError:
        if "/remote.php/" in url:
            raise
        return {"nextcloud": False, "server": url, "version": ""}
    try:
        j = r.json() if r.status_code == 200 else {}
    except ValueError:
        j = {}
    if isinstance(j, dict) and j.get("installed") is True and ("versionstring" in j or "productname" in j):
        return {"nextcloud": True, "server": server, "version": str(j.get("versionstring") or "")[:20],
                "product": str(j.get("productname") or "Nextcloud")[:40]}
    return {"nextcloud": False, "server": url, "version": ""}


def nc_user_id(server, login, password, ip_ok=None):
    """Benutzer-ID fuer den WebDAV-Pfad (kann vom Anmeldenamen abweichen, z. B. Anmeldung per E-Mail)."""
    def go():
        with _client_for(urlparse(server).hostname, timeout=20.0, auth_=(login, password), ip_ok=ip_ok) as cl:
            return cl.get(server + "/ocs/v2.php/cloud/user?format=json", headers={"OCS-APIRequest": "true", "Accept": "application/json"})
    r = _net_call(go)
    if r.status_code in (401, 403):
        raise BackupError("auth")
    try:
        uid = r.json()["ocs"]["data"]["id"]
    except (ValueError, KeyError, TypeError):
        return login
    return str(uid or login)


def nc_revoke(server, login, password, ip_ok=None):
    """App-Passwort widerrufen (Trennen). -> True, wenn Nextcloud es bestaetigt."""
    def go():
        with _client_for(urlparse(server).hostname, timeout=20.0, auth_=(login, password), ip_ok=ip_ok) as cl:
            return cl.delete(server + "/ocs/v2.php/core/apppassword", headers={"OCS-APIRequest": "true", "Accept": "application/json"})
    try:
        return _net_call(go).status_code == 200
    except BackupError:
        return False


_flow = {"state": "", "login": "", "until": 0.0, "code": "", "p": {}, "id": ""}
_flow_lock = threading.Lock()


def flow_state():
    with _flow_lock:
        f = dict(_flow)
    if f["state"] == "wait" and time.time() > f["until"]:
        f.update(state="err", code="flow_expired")
    return f


def flow_start(server, folder):
    """Login Flow v2 starten: Saga holt Login-Link + Poll-Token und fragt im Hintergrund nach (bis 20 Minuten)."""
    host = urlparse(server).hostname

    def go():
        with _client_for(host, timeout=20.0) as cl:
            return cl.post(server + "/index.php/login/v2", headers={"Accept": "application/json"})
    r = _net_call(go)
    if r.status_code != 200:
        raise BackupError("http", status=r.status_code, what="login/v2")
    try:
        j = r.json()
        login, token, endpoint = j["login"], j["poll"]["token"], j["poll"]["endpoint"]
    except (ValueError, KeyError, TypeError):
        raise BackupError("http", status=r.status_code, what="login/v2")
    # Poll-Adresse nur auf demselben Host (sonst die bekannte Adresse) - keine Zugangsdaten an fremde Server
    if (urlparse(endpoint).hostname or "").lower() != (host or "").lower() or urlparse(endpoint).scheme != urlparse(server).scheme:
        endpoint = server + "/login/v2/poll"
    fid = uuid.uuid4().hex
    with _flow_lock:
        _flow.update(state="wait", login=str(login), until=time.time() + 20 * 60, code="", p={}, id=fid)
    threading.Thread(target=_flow_poll, args=(fid, server, endpoint, token, folder), name="backup-login", daemon=True).start()
    return str(login)


def flow_cancel():
    with _flow_lock:
        _flow.update(state="", login="", id="", code="")


def _flow_poll(fid, server, endpoint, token, folder):
    host = urlparse(server).hostname
    while True:
        with _flow_lock:
            if _flow["id"] != fid:
                return
            if time.time() > _flow["until"]:
                _flow.update(state="err", code="flow_expired")
                return
        time.sleep(2)
        try:
            def go():
                with _client_for(host, timeout=15.0) as cl:
                    return cl.post(endpoint, data={"token": token}, headers={"Accept": "application/json"})
            r = _net_call(go)
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                raise BackupError("http", status=r.status_code, what="login/v2/poll")
            j = r.json()
            login, app_pw = str(j["loginName"]), str(j["appPassword"])
            uid = nc_user_id(server, login, app_pw)
            cfg = config()
            if cfg.get("flow") and cfg.get("password") and cfg.get("server"):
                nc_revoke(cfg["server"], cfg.get("user") or "", cfg["password"])      # altes App-Passwort nicht liegen lassen
            cfg.update(mode="nextcloud", server=server, url=server, user=login, password=app_pw, dav_user=uid, flow=True,
                       folder=folder or cfg.get("folder") or "Saga-Backups")
            save_config(cfg)
            try:
                test_target(cfg)
                code = ""
            except BackupError as ex:
                code = "test:" + ex.code
                with _flow_lock:
                    _flow["p"] = ex.p
            with _flow_lock:
                if _flow["id"] == fid:
                    _flow.update(state="ok", code=code)
            print("backup: Nextcloud verbunden (Login Flow v2)", flush=True)
            return
        except BackupError as ex:
            with _flow_lock:
                if _flow["id"] == fid:
                    _flow.update(state="err", code=ex.code, p=ex.p)
            return
        except (ValueError, KeyError, TypeError):
            with _flow_lock:
                if _flow["id"] == fid:
                    _flow.update(state="err", code="http", p={"status": 200, "what": "login/v2/poll"})
            return


def test_target(cfg, ip_ok=None):
    """Ordner anlegen, kleine Datei schreiben/lesen/loeschen -> freier Platz (Bytes oder None)."""
    dav = Dav(cfg, ip_ok=ip_ok)
    try:
        dav.ensure_root()
        url = dav.url(".saga-test")
        dav.req("PUT", url, "PUT", ok=(200, 201, 204), content=b"saga", headers={"Content-Type": "application/octet-stream"})
        if dav.get_bytes(url) != b"saga":
            raise BackupError("checksum", name=".saga-test")
        dav.delete(url)
        return dav.free_space()
    finally:
        dav.close()


# =====================================================================================================
# Ein Lauf
# =====================================================================================================
def _stamp(now=None):
    return datetime.fromtimestamp(now or time.time(), TZ).strftime("%Y%m%d-%H%M%S")


def run_time(name):
    """saga-JJJJMMTT-hhmmss -> datetime (Zeitzone des Servers) oder None."""
    m = RUN_RE.match(name or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=TZ)
    except ValueError:
        return None


class _Progress:
    def __init__(self, rid):
        self.rid, self.last = rid, 0.0

    def __call__(self, code, force=False, **p):
        now = time.time()
        if not force and now - self.last < 2:
            return
        self.last = now
        try:
            with db.connect() as c, c:
                c.execute("UPDATE backup_run SET step=? WHERE id=?", (json.dumps({"code": code, "p": p}), self.rid))
        except Exception:  # noqa: BLE001 - Fortschritt ist nur Anzeige
            pass


def _dump_db(work, prog):
    prog("step.db", force=True)
    plain = os.path.join(work, "db.dump")
    try:
        r = subprocess.run(_nice() + ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", plain],
                           env=db._pg_env(), capture_output=True, timeout=6 * 3600)
    except FileNotFoundError:
        raise BackupError("dump", detail="pg_dump")
    if r.returncode:
        raise BackupError("dump", detail=r.stderr.decode("utf-8", "replace").strip()[:200])
    # vor dem Verschluesseln pruefen: liest pg_restore das Archiv vollstaendig?
    lr = subprocess.run(["pg_restore", "--list", plain], capture_output=True, timeout=600)
    if lr.returncode or b"TABLE DATA" not in lr.stdout:
        raise BackupError("dump", detail="pg_restore --list: " + lr.stderr.decode("utf-8", "replace").strip()[:160])
    return plain


def _tar_files(dst, recips, prog):
    """files (ohne thumbs), chat, svc-icons -> tar -> age; SHA-256 und Groesse des Klartexts nebenbei."""
    prog("step.files", force=True)
    dirs = [d for d in DATA_DIRS if os.path.isdir(os.path.join(STATE_DIR, d))]
    cmd = _nice() + ["tar", "-C", STATE_DIR, "--exclude=files/thumbs", "-cf", "-"] + (dirs or ["--files-from", "/dev/null"])
    import tempfile
    h, n = hashlib.sha256(), 0
    with open(os.devnull, "wb") as dn, tempfile.TemporaryFile() as te, tempfile.TemporaryFile() as ae:
        tp = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=te)
        ap = subprocess.Popen(_age_enc_cmd(recips, dst), stdin=subprocess.PIPE, stdout=dn, stderr=ae)
        try:
            for b in iter(lambda: tp.stdout.read(MB), b""):
                h.update(b)
                n += len(b)
                ap.stdin.write(b)
            ap.stdin.close()
        except BrokenPipeError:
            pass
        finally:
            tp.stdout.close()
            if tp.poll() is None and ap.poll() is not None:
                tp.kill()
            trc = tp.wait()
            arc = ap.wait()
            te.seek(0)
            ae.seek(0)
            terr, aerr = te.read(), ae.read()
    # tar: 1 = "Datei hat sich waehrend des Lesens geaendert" (laufender Betrieb) - Archiv ist trotzdem gueltig
    if trc not in (0, 1):
        raise BackupError("files", detail=terr.decode("utf-8", "replace").strip()[:200])
    if arc:
        raise BackupError("age", detail=aerr.decode("utf-8", "replace").strip()[:200])
    return {"size": n, "sha256": h.hexdigest(), "dirs": dirs}


def _secrets_text():
    lines = ["# Saga - Geheimnisse dieser Instanz. In die .env des neuen Stacks uebernehmen (siehe docs/ANLEITUNG.md, Backups).",
             f"SECRET_KEY={SECRET}"]
    if E.get("CAL_SECRET_KEY"):
        lines.append(f"CAL_SECRET_KEY={E['CAL_SECRET_KEY']}")
    return ("\n".join(lines) + "\n").encode()


def _do_run(cfg, rid, now):
    if not has_target(cfg):
        raise BackupError("notarget")
    recips = recipients(cfg)
    if not recips:
        raise BackupError("nokey")
    prog = _Progress(rid)
    name = "saga-" + _stamp(now)
    work = os.path.join(tmp_dir(), name)
    shutil.rmtree(tmp_dir(), ignore_errors=True)          # Reste frueherer Laeufe (nur ein Laeufer gleichzeitig)
    os.makedirs(work, mode=0o700)
    dav = Dav(cfg)
    try:
        dav.ensure_root()                                  # Ziel frueh pruefen (Anmeldung, Erreichbarkeit)
        with db.connect() as c, c:
            c.execute("UPDATE backup_run SET name=? WHERE id=?", (name, rid))
        plain = {}
        dump = _dump_db(work, prog)
        plain["db.dump.age"] = {"size": os.path.getsize(dump), "sha256": sha256_file(dump)}
        _encrypt_file(dump, os.path.join(work, "db.dump.age"), recips)
        os.remove(dump)
        plain["dateien.tar.age"] = _tar_files(os.path.join(work, "dateien.tar.age"), recips, prog)
        if cfg.get("secrets", True):
            prog("step.secrets", force=True)
            s = _secrets_text()
            plain["geheimnisse.env.age"] = {"size": len(s), "sha256": hashlib.sha256(s).hexdigest()}
            _encrypt_bytes(s, os.path.join(work, "geheimnisse.env.age"), recips)
        inner = {"format": FORMAT, "name": name, "plain": plain}
        _encrypt_bytes(json.dumps(inner, indent=1).encode(), os.path.join(work, "manifest.json.age"), recips)
        parts = [p for p in PARTS if os.path.isfile(os.path.join(work, p))]
        files = {p: {"size": os.path.getsize(os.path.join(work, p)), "sha256": sha256_file(os.path.join(work, p))} for p in parts}
        total = sum(f["size"] for f in files.values())
        free = dav.free_space()
        if free is not None and free < total + total // 50 + MB:
            raise BackupError("full", need=total, free=free)
        dav.mkcol(dav.url(name))
        full = datetime.fromtimestamp(now, TZ).weekday() == 6         # sonntags alles erneut herunterladen
        for p in parts:
            src = os.path.join(work, p)
            dav.put_file(src, dav.url(name, p), lambda s, n, p=p: prog("step.upload", name=p, pct=int(100 * s / max(1, n))))
            _verify(dav, dav.url(name, p), p, files[p], full, prog)
        manifest = {"format": FORMAT, "saga": VERSION, "schema": db.SCHEMA_VERSION, "name": name, "created": int(now),
                    "created_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "recipients": [fingerprint(r) for r in recips], "secrets": "geheimnisse.env.age" in files,
                    "data_dirs": plain["dateien.tar.age"]["dirs"], "files": files}
        mpath = os.path.join(work, "manifest.json")
        with open(mpath, "w") as f:
            json.dump(manifest, f, indent=1)
        dav.put_file(mpath, dav.url(name, "manifest.json"))          # zuletzt: erst jetzt gilt der Lauf als fertig
        _verify(dav, dav.url(name, "manifest.json"), "manifest.json", {"size": os.path.getsize(mpath), "sha256": sha256_file(mpath)}, True, prog)
        prog("step.prune", force=True)
        pruned = prune(dav, cfg, keep={name}, now=now)
        return {"name": name, "files": {k: v["size"] for k, v in files.items()}, "total": total, "chunked": dav.chunked,
                "recipients": manifest["recipients"], "pruned": len(pruned), "free": free}
    finally:
        dav.close()
        shutil.rmtree(work, ignore_errors=True)


def _verify(dav, url, name, want, full, prog):
    prog("step.verify", force=True, name=name)
    e = dav.stat(url)
    if not e or (e.get("size") is not None and e["size"] != want["size"]):
        raise BackupError("size", name=name)
    if full or want["size"] <= VERIFY_MAX:
        h = hashlib.sha256()
        dav.get_to(url, h.update)
        if h.hexdigest() != want["sha256"]:
            raise BackupError("checksum", name=name)


# =====================================================================================================
# Aufbewahrung (GFS): je Tag/Woche/Monat der neueste Lauf
# =====================================================================================================
def gfs_keep(names, d=7, w=4, m=6):
    """-> Menge der Laeufe, die bleiben: die neuesten d Tage, w Wochen (ISO), m Monate - je Zeitraum der neueste Lauf.
    Der neueste Lauf bleibt immer."""
    runs = sorted(((run_time(n), n) for n in names if run_time(n)), reverse=True)
    keep, days, weeks, months = set(), [], [], []
    for dt, n in runs:
        dk, wk, mk = dt.date(), tuple(dt.isocalendar())[:2], (dt.year, dt.month)
        if dk not in days and len(days) < d:
            days.append(dk)
            keep.add(n)
        if wk not in weeks and len(weeks) < w:
            weeks.append(wk)
            keep.add(n)
        if mk not in months and len(months) < m:
            months.append(mk)
            keep.add(n)
    if runs:
        keep.add(runs[0][1])
    return keep


def list_runs(dav, manifests=False):
    """Laeufe im Ziel (neueste zuerst): {name, time, complete, manifest?}. Fremde Ordner bleiben unberuehrt."""
    out = []
    for e in dav.listdir(dav.root):
        if not e["dir"] or not run_time(e["name"]):
            continue
        man = None
        if manifests:
            try:
                man = json.loads(dav.get_bytes(dav.url(e["name"], "manifest.json"), 1 * MB))
            except (BackupError, ValueError):
                man = None
            complete = man is not None
        else:
            complete = dav.stat(dav.url(e["name"], "manifest.json")) is not None
        out.append({"name": e["name"], "time": run_time(e["name"]).timestamp(), "complete": complete, "manifest": man})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def prune(dav, cfg, keep=(), now=None):
    """Alte Laeufe loeschen (GFS), unvollstaendige nach einem Tag. Laeufe der letzten 24 Stunden bleiben immer (ein
    zweiter Lauf am selben Tag, z. B. von Hand, verdraengt den vorigen erst am naechsten Tag). -> geloeschte Namen."""
    now = now or time.time()
    runs = list_runs(dav)
    complete = [r["name"] for r in runs if r["complete"]]
    stay = gfs_keep(complete, int(cfg.get("keep_d", 7)), int(cfg.get("keep_w", 4)), int(cfg.get("keep_m", 6))) | set(keep)
    gone = []
    for r in runs:
        if r["name"] in stay or now - r["time"] < 86400:   # unvollstaendig: laeuft vielleicht noch - erst nach einem Tag
            continue
        dav.delete(dav.url(r["name"]) + "/")
        gone.append(r["name"])
    if gone:
        print(f"backup: {len(gone)} alte(s) Backup(s) im Ziel entfernt", flush=True)
    return gone


# =====================================================================================================
# Lauf starten (Zeitplan, Admin, CLI), Benachrichtigungen
# =====================================================================================================
def running():
    """Laeuft gerade ein Backup (irgendein Prozess dieser Datenbank)?"""
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:
        got = c.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]
        if got:
            c.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        return not got


def run(trigger="manuell", slot=None, now=None):
    """Ein Lauf -> (ok, Ergebnis | BackupError). slot: nur, wenn fuer diesen Zeitpunkt noch kein Versuch lief (Zeitplan)."""
    lc = psycopg.connect(db.DATABASE_URL, autocommit=True)
    try:
        if not lc.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            return False, BackupError("busy")
        st = state()
        if slot is not None and float(st.get("last_attempt") or 0) >= slot:
            return False, None                             # ein anderer Prozess war schneller
        now = now or time.time()
        set_state(last_attempt=now)
        with db.connect() as c, c:
            c.execute("UPDATE backup_run SET finished=?, error=? WHERE finished IS NULL", (now, BackupError("aborted").as_json()))
            rid = c.execute("INSERT INTO backup_run (started, trigger) VALUES (?, ?)", (now, trigger)).lastrowid
            c.execute("DELETE FROM backup_run WHERE id <= ?", (rid - HISTORY_KEEP,))
        _low_priority()
        cfg = config()
        try:
            res = _do_run(cfg, rid, now)
            with db.connect() as c, c:
                c.execute("UPDATE backup_run SET finished=?, ok=1, size=?, detail=?, step='' WHERE id=?",
                          (time.time(), res["total"], json.dumps(res), rid))
            set_state(last_ok=time.time(), stale_note=0)
            print(f"backup: {res['name']} gesichert ({size_text(res['total'])}, {trigger})", flush=True)
            return True, res
        except Exception as ex:  # noqa: BLE001 - jeder Fehler wird gemeldet, Saga laeuft weiter
            err = ex if isinstance(ex, BackupError) else BackupError("other", detail=type(ex).__name__)
            if not isinstance(ex, BackupError):
                print(f"backup: {type(ex).__name__}: {ex}", flush=True)
            with db.connect() as c, c:
                c.execute("UPDATE backup_run SET finished=?, ok=0, error=?, step='' WHERE id=?", (time.time(), err.as_json(), rid))
            print(f"backup: fehlgeschlagen ({trigger}): {err.code} {err.p}", flush=True)
            alert("failed", code=err.code, p=err.p)
            return False, err
    finally:
        try:
            lc.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        except Exception:  # noqa: BLE001
            pass
        lc.close()


def _low_priority():
    """Niedrige CPU-Prioritaet fuer den Backup-Thread (Linux: nur dieser Thread und seine Kindprozesse, nicht der
    Webserver); pg_dump, tar und age laufen zusaetzlich mit ionice. Mehrfacher Aufruf erhoeht nicht weiter."""
    try:
        if os.getpriority(os.PRIO_PROCESS, 0) < 10:
            os.setpriority(os.PRIO_PROCESS, 0, 10)
    except (OSError, AttributeError):
        pass


def run_async(trigger="manuell"):
    threading.Thread(target=run, args=(trigger,), name="backup-run", daemon=True).start()


def admins(c):
    return [r[0] for r in c.execute("SELECT username FROM users WHERE is_admin=1 AND disabled=0 ORDER BY username")]


def alert(what, **p):
    """Alle Admin-Konten benachrichtigen (Glocke + Push) und optional den Webhook rufen. what: Kennung fuer text()."""
    try:
        with db.connect() as c, c:
            for u in admins(c):
                notify.create(c, u, "backup.failed", None, data={"code": what, "p": p})
    except Exception as ex:  # noqa: BLE001
        print(f"backup: Benachrichtigung: {type(ex).__name__}: {ex}", flush=True)
    hook = (config().get("webhook") or "").strip()
    if hook:
        threading.Thread(target=_webhook, args=(hook, what, p), daemon=True).start()


def _webhook(url, code, p):
    """ntfy/beliebig: Text als Inhalt + Title-Kopfzeile; Gotify (.../message): JSON {title, message}."""
    from saga import i18n
    tok = i18n.set_lang(auth.settings().get("default_lang") or "en")
    try:
        msg, title = text(code, p), t("Saga-Backup")
    finally:
        i18n._cur.reset(tok)
    try:
        with _client_for(urlparse(url).hostname, timeout=15.0) as cl:
            if urlparse(url).path.rstrip("/").endswith("/message"):
                cl.post(url, json={"title": title, "message": msg, "priority": 7})
            else:
                cl.post(url, content=msg.encode(), headers={"Title": title.encode("ascii", "replace").decode(), "Tags": "warning",
                                                            "Content-Type": "text/plain; charset=utf-8"})
    except Exception as ex:  # noqa: BLE001
        print(f"backup: Webhook: {type(ex).__name__}", flush=True)


def _notif(c, uid, item, d):
    """Benachrichtigung backup.failed: Text erst beim Lesen (Sprache des Admins), Link in den Admin-Bereich."""
    item["access"] = True
    item["system"] = True
    item["text"] = text(d.get("code", ""), d.get("p"))
    item["title"] = t("Backups")
    item["url"] = (ADMIN_URL + "/backups") if ADMIN_URL else None


notify.RENDER["backup.failed"] = _notif


# =====================================================================================================
# Zeitplan
# =====================================================================================================
def slot_for(cfg, now):
    """Letzter geplanter Zeitpunkt <= now (Unix-Zeit), Uhrzeit in der Zeitzone des Servers."""
    m = TIME_RE.match(str(cfg.get("time") or "03:20"))
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (3, 20)
    d = datetime.fromtimestamp(now, TZ).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if d.timestamp() > now:
        d -= timedelta(days=1)
        d = d.replace(hour=hh, minute=mm)                  # Zeitumstellung: Uhrzeit bleibt
    return d.timestamp()


def due(cfg, st, now):
    """-> geplanter Zeitpunkt, wenn jetzt ein Lauf faellig ist, sonst None. Erst nach dem Einschalten (since)."""
    if not cfg.get("enabled") or not has_target(cfg) or not recipients(cfg):
        return None
    slot = slot_for(cfg, now)
    if slot <= float(cfg.get("since") or 0) or float(st.get("last_attempt") or 0) >= slot:
        return None
    return slot


def next_run(cfg, st, now=None):
    now = now or time.time()
    if not cfg.get("enabled") or not has_target(cfg) or not recipients(cfg):
        return None
    if due(cfg, st, now):
        return now
    nxt = slot_for(cfg, now)
    while nxt <= now or nxt <= float(cfg.get("since") or 0):
        nxt = slot_for(cfg, nxt + 86400 + 3600)
    return nxt


def last_ok():
    with db.connect() as c:
        r = c.execute("SELECT max(finished) FROM backup_run WHERE ok=1").fetchone()
    return float(r[0]) if r and r[0] else 0.0


def check_stale(now=None):
    """Seit STALE_H Stunden kein Erfolg (Zeitplan an) -> hoechstens einmal am Tag melden. -> True, wenn gemeldet."""
    now = now or time.time()
    cfg = config()
    if not cfg.get("enabled") or not has_target(cfg):
        return False
    st = state()
    ref = max(last_ok(), float(cfg.get("since") or 0))
    if not ref or now - ref < STALE_H * 3600 or now - float(st.get("stale_note") or 0) < 86400:
        return False
    set_state(stale_note=now)
    alert("stale", h=int((now - ref) // 3600))
    return True


_worker = {"thread": None, "stop": False}
_wake = threading.Event()


def _loop():
    _wake.wait(min(30.0, TICK))
    while not _worker["stop"]:
        try:
            now = time.time()
            slot = due(config(), state(), now)
            if slot:
                run("zeitplan", slot=slot)
            check_stale()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg o. ae.: naechste Runde
            print(f"backup: {type(ex).__name__}: {ex}", flush=True)
        _wake.wait(TICK)
        _wake.clear()


def start():
    if RESTORE_TEST:
        print("backup: SAGA_RESTORE_TEST=1 - kein Zeitplan, keine Uploads", flush=True)
        return
    if _worker["thread"] is None:
        _worker["stop"] = False
        th = threading.Thread(target=_loop, name="backup", daemon=True)
        _worker["thread"] = th
        th.start()


def stop():
    _worker["stop"] = True
    _wake.set()
    _worker["thread"] = None


# =====================================================================================================
# Kommandozeile: holen, zurueckspielen, pruefen
# =====================================================================================================
def cli_dav():
    """Ziel fuer die Kommandozeile: eingerichtetes Ziel - oder (frischer Stack) SAGA_BACKUP_URL (WebDAV-Adresse des
    Backup-Ordners), SAGA_BACKUP_USER, SAGA_BACKUP_PASSWORD."""
    url = E.get("SAGA_BACKUP_URL", "").strip()
    if url:
        return Dav({"mode": "webdav", "url": url, "user": E.get("SAGA_BACKUP_USER", ""), "password": E.get("SAGA_BACKUP_PASSWORD", ""),
                    "folder": ""})
    cfg = config()
    if not has_target(cfg):
        raise SystemExit("Kein Backup-Ziel eingerichtet (Admin -> Backups) und SAGA_BACKUP_URL nicht gesetzt.")
    return Dav(cfg)


def verify_dir(path):
    """Lauf-Ordner gegen manifest.json pruefen -> Manifest (SystemExit bei Abweichung)."""
    try:
        with open(os.path.join(path, "manifest.json")) as f:
            man = json.load(f)
    except (OSError, ValueError):
        raise SystemExit(f"{path}: manifest.json fehlt oder ist kaputt (Lauf unvollstaendig?)")
    for name, want in (man.get("files") or {}).items():
        p = os.path.join(path, name)
        if not os.path.isfile(p):
            raise SystemExit(f"{name}: fehlt")
        if os.path.getsize(p) != want["size"] or sha256_file(p) != want["sha256"]:
            raise SystemExit(f"{name}: Pruefsumme stimmt nicht")
    return man


def fetch(name=None, dest=None, out=print):
    """Lauf herunterladen (ohne Namen: den neuesten vollstaendigen) -> lokaler Ordner (Pruefsummen geprueft)."""
    dav = cli_dav()
    try:
        runs = [r for r in list_runs(dav) if r["complete"]]
        if not runs:
            raise SystemExit("Im Ziel gibt es keinen vollstaendigen Lauf.")
        pick = next((r for r in runs if r["name"] == name), None) if name else runs[0]
        if not pick:
            raise SystemExit(f"Lauf {name} nicht gefunden. Vorhanden: " + ", ".join(r["name"] for r in runs[:20]))
        name = pick["name"]
        dest = dest or os.path.join(db.BACKUP_DIR, "extern", name)
        os.makedirs(dest, mode=0o700, exist_ok=True)
        man = json.loads(dav.get_bytes(dav.url(name, "manifest.json"), MB))
        with open(os.path.join(dest, "manifest.json"), "w") as f:
            json.dump(man, f, indent=1)
        for p in man.get("files") or {}:
            if p not in PARTS:
                continue
            out(f"  {p} ({size_text(man['files'][p]['size'])}) ...")
            tmp = os.path.join(dest, p + ".part")
            with open(tmp, "wb") as f:
                dav.get_to(dav.url(name, p), f.write)
            os.replace(tmp, os.path.join(dest, p))
    except BackupError as ex:
        raise SystemExit("Ziel: " + text(ex.code, ex.p))
    finally:
        dav.close()
    verify_dir(dest)
    return dest


def _safe_members(tar):
    for m in tar:
        top = m.name.split("/", 1)[0]
        if top not in DATA_DIRS or m.name.startswith("/") or ".." in m.name.split("/"):
            continue
        if not (m.isfile() or m.isdir()):
            continue
        yield m


def restore_files(path, keyfile):
    """dateien.tar.age entschluesseln und nach STATE_DIR entpacken (nur files/, chat/, svc-icons/) -> Anzahl Dateien."""
    import tarfile
    ap = subprocess.Popen(["age", "-d", "-i", keyfile, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    try:
        with tarfile.open(fileobj=ap.stdout, mode="r|") as tar:
            for m in _safe_members(tar):
                tar.extract(m, STATE_DIR, filter="data")
                n += m.isfile()
    finally:
        ap.stdout.close()
        err = ap.stderr.read()
        ap.stderr.close()
        rc = ap.wait()
    if rc:
        raise SystemExit("age: " + err.decode("utf-8", "replace").strip()[:300])
    return n


def _resolve(ref, out):
    if ref and os.path.isdir(ref):
        verify_dir(ref)
        return ref
    out("Lade Lauf aus dem Ziel ...")
    return fetch(ref or None, out=out)


def restore_cmd(keyfile, ref, out=print):
    """run.py restore --schluessel <datei> <lauf>: holen, pruefen, entschluesseln, Datenbank (db.restore) + Dateien +
    Geheimnisse. Saga vorher stoppen."""
    if not os.path.isfile(keyfile):
        raise SystemExit(f"Schluesseldatei nicht gefunden: {keyfile}")
    d = _resolve(ref, out)
    man = verify_dir(d)
    name = man.get("name") or os.path.basename(d.rstrip("/"))
    os.makedirs(db.BACKUP_DIR, exist_ok=True)
    dump = os.path.join(db.BACKUP_DIR, f"{name}-extern.dump")
    out("Entschluessele Datenbank ...")
    try:
        decrypt(os.path.join(d, "db.dump.age"), dump, keyfile)
    except RuntimeError as ex:
        raise SystemExit(str(ex))
    out("Spiele Datenbank zurueck ...")
    db.restore(dump)
    out(f"Datenbank zurueckgespielt (Schema-Stand {db.schema_version()}).")
    if os.path.isfile(os.path.join(d, "dateien.tar.age")):
        out(f"Dateien: {restore_files(os.path.join(d, 'dateien.tar.age'), keyfile)} nach {STATE_DIR} zurueckgeholt.")
    sec = os.path.join(d, "geheimnisse.env.age")
    if os.path.isfile(sec):
        try:
            raw = decrypt(sec, "-", keyfile).decode()
        except RuntimeError as ex:
            raise SystemExit(str(ex))
        path = os.path.join(db.BACKUP_DIR, f"{name}-geheimnisse.env")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(raw)
        vals = dict(re.findall(r"^([A-Z_]+)=(.*)$", raw, re.M))
        differ = [k for k, v in vals.items() if E.get(k, SECRET if k == "SECRET_KEY" else "") != v]
        out(f"Geheimnisse: {path}")
        if differ:
            out("ACHTUNG: " + ", ".join(differ) + " in der .env weicht vom Backup ab. Werte aus der Datei oben in die .env "
                "uebernehmen und Saga neu starten - sonst sind gespeicherte Passwoerter/Schluessel nicht lesbar.")
    out("Fertig. Hinweis: Das Backup enthaelt den Foederations-Schluessel - eine TESTwiederherstellung auf einem zweiten "
        "Server nur mit SAGA_RESTORE_TEST=1 starten.")
    return d


def check_cmd(keyfile, ref=None, out=print):
    """run.py backup-pruefen --schluessel <datei> [<lauf>]: holen, Pruefsummen, entschluesseln, Datenbank in eine
    Wegwerf-Datenbank einspielen (danach DROP), tar-Inhalt und Geheimnisse pruefen. -> True = in Ordnung."""
    import secrets as _sec
    import tarfile
    from psycopg.conninfo import make_conninfo
    if not os.path.isfile(keyfile):
        raise SystemExit(f"Schluesseldatei nicht gefunden: {keyfile}")
    keep_dir = ref and os.path.isdir(ref)
    d = _resolve(ref, out)
    man = verify_dir(d)
    out(f"Lauf {man.get('name')}: Pruefsummen in Ordnung (Saga {man.get('saga')}, Schema {man.get('schema')}).")
    work = os.path.join(tmp_dir(), "pruefung")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, mode=0o700)
    dbname = "saga_pruefung_" + _sec.token_hex(4)
    real = db.DATABASE_URL
    try:
        inner = json.loads(decrypt(os.path.join(d, "manifest.json.age"), "-", keyfile))
        dump = decrypt(os.path.join(d, "db.dump.age"), os.path.join(work, "db.dump"), keyfile)
        want = inner["plain"]["db.dump.age"]
        if sha256_file(dump) != want["sha256"]:
            raise SystemExit("db.dump: Pruefsumme des Klartexts stimmt nicht")
        lr = subprocess.run(["pg_restore", "--list", dump], capture_output=True, timeout=600)
        if lr.returncode:
            raise SystemExit("pg_restore --list: " + lr.stderr.decode("utf-8", "replace")[:300])
        with psycopg.connect(real, autocommit=True) as c:
            c.execute(f"CREATE DATABASE {dbname}")
        try:
            db.DATABASE_URL = make_conninfo(real, dbname=dbname)
            db.restore(dump)
            with psycopg.connect(db.DATABASE_URL) as c:
                tables = c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0]
                notes = c.execute("SELECT count(*) FROM note").fetchone()[0] if c.execute(
                    "SELECT to_regclass('public.note')").fetchone()[0] else 0
            out(f"Datenbank: in Wegwerf-Datenbank eingespielt ({tables} Tabellen, {notes} Seiten).")
        finally:
            db.DATABASE_URL = real
            with psycopg.connect(real, autocommit=True) as c:
                c.execute(f"DROP DATABASE IF EXISTS {dbname}")
        if os.path.isfile(os.path.join(d, "dateien.tar.age")):
            ap = subprocess.Popen(["age", "-d", "-i", keyfile, os.path.join(d, "dateien.tar.age")], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            n = 0
            h = hashlib.sha256()

            class _Tee:
                def read(self, k=-1):
                    b = ap.stdout.read(k)
                    h.update(b)
                    return b
            tee = _Tee()
            try:
                with tarfile.open(fileobj=tee, mode="r|") as tar:
                    for m in tar:
                        n += m.isfile()
                while tee.read(MB):                       # Rest (Fuellbloecke am Ende) fuer die Pruefsumme
                    pass
            finally:
                ap.stdout.close()
                ap.stderr.close()
                rc = ap.wait()
            if rc or h.hexdigest() != inner["plain"]["dateien.tar.age"]["sha256"]:
                raise SystemExit("dateien.tar: Entschluesseln oder Pruefsumme fehlgeschlagen")
            out(f"Dateien: {n} Dateien im Archiv, Pruefsumme in Ordnung.")
        if os.path.isfile(os.path.join(d, "geheimnisse.env.age")):
            raw = decrypt(os.path.join(d, "geheimnisse.env.age"), "-", keyfile).decode()
            if not re.search(r"^SECRET_KEY=.{32,}$", raw, re.M):
                raise SystemExit("geheimnisse.env: SECRET_KEY fehlt")
            out("Geheimnisse: lesbar (SECRET_KEY vorhanden).")
    except RuntimeError as ex:
        raise SystemExit(str(ex))
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if not keep_dir:
            shutil.rmtree(d, ignore_errors=True)
    set_state(last_check=time.time(), last_check_name=man.get("name"))
    out("Testwiederherstellung in Ordnung.")
    return True
