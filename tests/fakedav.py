"""Fake-WebDAV-Ziel fuer tests/backup_extern.py: wahlweise wie Nextcloud (status.php, Login Flow v2, OCS, Chunking v2
unter /remote.php/dav/...) oder als schlichtes WebDAV (/dav/...). Alles im Speicher, nur 127.0.0.1, nur fuer Tests.

Nextcloud-Teil: POST /index.php/login/v2 -> login + poll; der Test "meldet sich an" mit GET /_grant (dann liefert der Poll
einmalig server/loginName/appPassword); GET /ocs/v2.php/cloud/user -> Benutzer-ID (weicht absichtlich vom Anmeldenamen
ab); DELETE /ocs/v2.php/core/apppassword widerruft das App-Passwort. Chunking v2 wie Nextcloud: Teile 1..10000,
mindestens 5 MB ausser dem letzten, Destination + OC-Total-Length bei jedem Schritt, MOVE .file setzt zusammen.
Steuerung: GET /_ctl?down=1|0&quota=<bytes>|-3&corrupt=1|0&reset=1, GET /_stats (JSON), POST /_hook (Webhook-Empfang).
Aufruf auch als Programm: python fakedav.py <port> [nc|dav]"""
import base64
import json
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

MB = 1024 * 1024
LOGIN, UID = "saga-login@example.org", "saga-bk"


class Store:
    def __init__(self, nextcloud, port):
        self.nc, self.port = nextcloud, port
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.files, self.dirs = {}, set()
        self.down = self.corrupt = False
        self.quota = -3
        self.pw = {"plain-user": "plain-pass-123"} if not self.nc else {}
        self.granted = False
        self.flow_token = None
        self.stats = {"chunks": 0, "moves": 0, "puts": 0, "revoked": 0, "hooks": [], "requests": 0, "small_chunk": 0}
        self.uploads = {}
        root = f"/remote.php/dav/files/{UID}" if self.nc else "/dav"
        self.root = root
        self.dirs.add(root)
        if self.nc:
            self.dirs.add(f"/remote.php/dav/uploads/{UID}")

    def used(self):
        return sum(len(v) for v in self.files.values())


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                out = bytearray()
                while True:
                    size = int(self.rfile.readline().strip() or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    out += self.rfile.read(size)
                    self.rfile.readline()
                return bytes(out)
            return self.rfile.read(n) if n else b""

        def _send(self, code, body=b"", ctype="text/plain", extra=None):
            if isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _authed(self):
            h = self.headers.get("Authorization", "")
            if not h.startswith("Basic "):
                return False
            try:
                u, _, p = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:  # noqa: BLE001
                return False
            return st.pw.get(u) == p and p != ""

        def _path(self):
            return unquote(urlparse(self.path).path).rstrip("/") or "/"

        def handle_one(self):
            u = urlparse(self.path)
            p = self._path()
            q = parse_qs(u.query)
            with st.lock:
                st.stats["requests"] += 1
                if not p.startswith("/_"):
                    st.stats.setdefault("paths", []).append(f"{self.command} {p}")
            if p == "/_ctl":
                with st.lock:
                    if q.get("reset"):
                        st.reset()
                    if "down" in q:
                        st.down = q["down"][0] == "1"
                    if "quota" in q:
                        st.quota = int(q["quota"][0])
                    if "corrupt" in q:
                        st.corrupt = q["corrupt"][0] == "1"
                    if "pw" in q:                                  # schlichtes WebDAV: Zugang setzen
                        user, _, pw = q["pw"][0].partition(":")
                        st.pw[user] = pw
                return self._send(200, "ok")
            if p == "/_stats":
                with st.lock:
                    d = dict(st.stats, files={k: len(v) for k, v in st.files.items()}, dirs=sorted(st.dirs), used=st.used(),
                             uploads=len(st.uploads))
                return self._send(200, json.dumps(d), "application/json")
            if p == "/_get":                                       # Test liest eine Datei ohne Anmeldung
                f = q.get("f", [""])[0]
                with st.lock:
                    b = st.files.get(f)
                return self._send(200 if b is not None else 404, b or b"", "application/octet-stream")
            if p == "/_hook":
                body = self._body()
                with st.lock:
                    st.stats["hooks"].append({"body": body.decode("utf-8", "replace"), "title": self.headers.get("Title", ""),
                                              "type": self.headers.get("Content-Type", "")})
                return self._send(200, "ok")
            if p == "/_grant":
                with st.lock:
                    st.granted = True
                return self._send(200, "ok")
            if st.down:
                self._body()
                return self._send(503, "down")
            if st.nc and p == "/status.php" and self.command == "GET":
                return self._send(200, json.dumps({"installed": True, "maintenance": False, "needsDbUpgrade": False,
                                                   "version": "30.0.1.2", "versionstring": "30.0.1", "productname": "Nextcloud"}),
                                  "application/json")
            if st.nc and p == "/index.php/login/v2" and self.command == "POST":
                self._body()
                with st.lock:
                    st.flow_token, st.granted = secrets.token_hex(16), False
                base = f"http://127.0.0.1:{st.port}"
                return self._send(200, json.dumps({"poll": {"token": st.flow_token, "endpoint": base + "/login/v2/poll"},
                                                   "login": base + "/login/v2/flow/" + secrets.token_hex(8)}), "application/json")
            if st.nc and p == "/login/v2/poll" and self.command == "POST":
                tok = parse_qs(self._body().decode()).get("token", [""])[0]
                with st.lock:
                    if not st.flow_token or tok != st.flow_token or not st.granted:
                        return self._send(404, "[]", "application/json")
                    st.flow_token = None
                    pw = "app-" + secrets.token_hex(12)
                    st.pw[LOGIN] = pw
                return self._send(200, json.dumps({"server": f"http://127.0.0.1:{st.port}", "loginName": LOGIN, "appPassword": pw}),
                                  "application/json")
            if st.nc and p.startswith("/ocs/"):
                self._body()
                if not self._authed():
                    return self._send(401, "{}", "application/json")
                if p == "/ocs/v2.php/cloud/user" and self.command == "GET":
                    return self._send(200, json.dumps({"ocs": {"meta": {"status": "ok"}, "data": {"id": UID, "display-name": "Saga"}}}),
                                      "application/json")
                if p == "/ocs/v2.php/core/apppassword" and self.command == "DELETE":
                    if self.headers.get("OCS-APIRequest", "").lower() != "true":
                        return self._send(400, "{}", "application/json")
                    h = self.headers.get("Authorization", "")
                    user = base64.b64decode(h[6:]).decode().partition(":")[0]
                    with st.lock:
                        st.pw.pop(user, None)
                        st.stats["revoked"] += 1
                    return self._send(200, json.dumps({"ocs": {"meta": {"status": "ok"}, "data": []}}), "application/json")
                return self._send(404, "{}", "application/json")
            if not (p.startswith(st.root) or (st.nc and p.startswith("/remote.php/dav/uploads/"))):
                self._body()
                return self._send(404, "not found")
            if not self._authed():
                self._body()
                return self._send(401, "unauthorized", extra={"WWW-Authenticate": 'Basic realm="fake"'})
            return self.dav(p)

        # --- WebDAV ---
        def dav(self, p):
            m = self.command
            parent = p.rsplit("/", 1)[0] or "/"
            if m == "PROPFIND":
                self._body()
                depth = self.headers.get("Depth", "1")
                with st.lock:
                    if p not in st.dirs and p not in st.files:
                        return self._send(404, "")
                    items = [p]
                    if p in st.dirs and depth != "0":
                        items += sorted(x for x in list(st.dirs) + list(st.files) if x.rsplit("/", 1)[0] == p and x != p)
                    free = -3 if st.quota < 0 else max(0, st.quota - st.used())
                    out = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">']
                    for x in items:
                        isdir = x in st.dirs
                        href = quote(x + ("/" if isdir else ""))
                        props = "<d:resourcetype><d:collection/></d:resourcetype>" if isdir else "<d:resourcetype/>"
                        if isdir:
                            props += f"<d:quota-available-bytes>{free}</d:quota-available-bytes>"
                            props += f"<oc:size>{sum(len(v) for k, v in st.files.items() if k.startswith(x + '/'))}</oc:size>"
                        else:
                            props += f"<d:getcontentlength>{len(st.files[x])}</d:getcontentlength>"
                        out.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop>"
                                   "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
                    out.append("</d:multistatus>")
                return self._send(207, "".join(out), "application/xml; charset=utf-8")
            if m == "MKCOL":
                self._body()
                with st.lock:
                    if p in st.dirs or p in st.files:
                        return self._send(405, "exists")
                    if parent not in st.dirs:
                        return self._send(409, "parent missing")
                    st.dirs.add(p)
                    if p.startswith("/remote.php/dav/uploads/"):
                        st.uploads[p] = {"dest": self.headers.get("Destination"), "total": self.headers.get("OC-Total-Length")}
                return self._send(201, "")
            if m == "PUT":
                body = self._body()
                with st.lock:
                    if parent not in st.dirs:
                        return self._send(409, "parent missing")
                    if parent in st.uploads:                       # Teil eines Chunking-v2-Uploads
                        name = p.rsplit("/", 1)[-1]
                        if not re.fullmatch(r"\d{1,5}", name) or not 1 <= int(name) <= 10000:
                            return self._send(400, "bad chunk name")
                        if self.headers.get("Destination") != st.uploads[parent]["dest"]:
                            return self._send(400, "destination differs")
                        st.stats["chunks"] += 1
                        if len(body) < 5 * MB:
                            st.stats["small_chunk"] += 1
                    elif st.quota >= 0 and st.used() - len(st.files.get(p, b"")) + len(body) > st.quota:
                        return self._send(507, "insufficient storage")
                    else:
                        st.stats["puts"] += 1
                    st.files[p] = body
                return self._send(201, "")
            if m == "GET":
                self._body()
                with st.lock:
                    b = st.files.get(p)
                    if b is not None and st.corrupt and len(b) > 10:
                        b = b[:5] + bytes([b[5] ^ 0xFF]) + b[6:]
                if b is None:
                    return self._send(404, "")
                return self._send(200, b, "application/octet-stream")
            if m == "DELETE":
                self._body()
                with st.lock:
                    if p not in st.dirs and p not in st.files:
                        return self._send(404, "")
                    for k in [k for k in st.files if k == p or k.startswith(p + "/")]:
                        del st.files[k]
                    for k in [k for k in st.dirs if k == p or k.startswith(p + "/")]:
                        st.dirs.discard(k)
                    st.uploads.pop(p, None)
                return self._send(204, "")
            if m == "MOVE":
                self._body()
                dest = unquote(urlparse(self.headers.get("Destination", "")).path).rstrip("/")
                with st.lock:
                    if p.endswith("/.file") and parent in st.uploads:
                        up = st.uploads[parent]
                        if self.headers.get("Destination") != up["dest"] or self.headers.get("OC-Total-Length") != up["total"]:
                            return self._send(400, "header mismatch")
                        parts = sorted((k for k in st.files if k.rsplit("/", 1)[0] == parent), key=lambda k: int(k.rsplit("/", 1)[-1]))
                        sizes = [len(st.files[k]) for k in parts]
                        if any(s < 5 * MB for s in sizes[:-1]):
                            return self._send(400, "chunk too small")
                        data = b"".join(st.files[k] for k in parts)
                        if str(len(data)) != up["total"]:
                            return self._send(400, "length mismatch")
                        if dest.rsplit("/", 1)[0] not in st.dirs:
                            return self._send(409, "parent missing")
                        if st.quota >= 0 and st.used() - sum(sizes) + len(data) > st.quota:
                            return self._send(507, "insufficient storage")
                        for k in parts:
                            del st.files[k]
                        st.dirs.discard(parent)
                        st.uploads.pop(parent, None)
                        st.files[dest] = data
                        st.stats["moves"] += 1
                        return self._send(201, "")
                    if p in st.files:
                        st.files[dest] = st.files.pop(p)
                        return self._send(201, "")
                return self._send(404, "")
            self._body()
            return self._send(405, "")

        do_GET = do_POST = do_PUT = do_DELETE = do_PROPFIND = do_MKCOL = do_MOVE = do_HEAD = handle_one
    return H


def start(port, nextcloud=True):
    st = Store(nextcloud, port)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(st))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.store = st
    return srv


if __name__ == "__main__":
    s = start(int(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "nc") == "nc")
    threading.Event().wait()
