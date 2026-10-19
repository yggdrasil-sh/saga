"""Nachgemachter S3-Server fuer tests/backup_extern.py (nur Python-Standardbibliothek).

Prueft jede Anfrage wie ein echter Anbieter: AWS-Signatur v4 (eigene, von saga/backups.py unabhaengige Umsetzung -
kanonische Anfrage aus dem, was wirklich ankommt), Zeitstempel, x-amz-content-sha256 gegen den Inhalt. Kann:
ListObjectsV2 (Praefix, Trenner, Seiten mit continuation-token), HEAD/GET/PUT/DELETE von Objekten, Multipart-Upload
(Create, UploadPart, Complete mit ETag-Pruefung und Mindestgroesse 5 MB je Teil ausser dem letzten, Abort).
Path-style (Host = Server, /bucket/key) und virtual-hosted (Host = bucket.server).
Steuerung: /_stats, /_ctl?down=1|corrupt=1|page=N, /_get?b=<bucket>&k=<key> (Rohdaten).
"""
import hashlib
import hmac
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

KEY_ID = "SAGATESTKEY0000000001"
SECRET = "saga-test-secret/0123456789abcdefghijklmn"
REGION = "eu-test-1"
BUCKET = "saga-bkx"
NS = "http://s3.amazonaws.com/doc/2006-03-01/"
MIN_PART = 5 * 1024 * 1024


class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.objects = {BUCKET: {}}
        self.uploads = {}
        self.down = False
        self.corrupt = False
        self.page = 1000
        self.st = {"requests": 0, "puts": 0, "parts": 0, "completes": 0, "aborts": 0, "sigfail": 0, "hashfail": 0,
                   "lists": 0, "virtual": 0, "path": 0, "deletes": 0}


def _enc(s):
    return quote(s, safe="-_.~")


def expected_auth(method, raw_path, raw_query, headers, signed, amz_date, payload_hash, secret, region):
    """Unabhaengige SigV4-Pruefung: kanonische Anfrage aus den Rohdaten der Anfrage."""
    pairs = sorted((_enc(unquote(k)), _enc(unquote(v))) for k, v in
                   (p.split("=", 1) if "=" in p else (p, "") for p in raw_query.split("&") if p))
    cq = "&".join(f"{k}={v}" for k, v in pairs)
    ch = "".join(f"{n}:{' '.join(str(headers.get(n, '')).strip().split())}\n" for n in signed)
    creq = "\n".join([method, raw_path, cq, ch, ";".join(signed), payload_hash])
    scope = f"{amz_date[:8]}/{region}/s3/aws4_request"
    sts = "AWS4-HMAC-SHA256\n" + amz_date + "\n" + scope + "\n" + hashlib.sha256(creq.encode()).hexdigest()
    k = ("AWS4" + secret).encode()
    for p in (amz_date[:8], region, "s3", "aws4_request"):
        k = hmac.new(k, p.encode(), hashlib.sha256).digest()
    return hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()


def make_handler(st, port):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/xml", extra=None):
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

        def _err(self, code, s3code):
            self._send(code, f'<?xml version="1.0" encoding="UTF-8"?><Error><Code>{s3code}</Code><Message>x</Message></Error>')

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def handle_one(self):
            u = urlsplit(self.path)
            if u.path.startswith("/_"):
                q = dict(parse_qsl(u.query))
                if u.path == "/_stats":
                    with st.lock:
                        d = dict(st.st, objects={b: {k: len(v) for k, v in o.items()} for b, o in st.objects.items()},
                                 uploads=len(st.uploads))
                    return self._send(200, json.dumps(d), "application/json")
                if u.path == "/_ctl":
                    for k in ("down", "corrupt"):
                        if k in q:
                            setattr(st, k, q[k] == "1")
                    if "page" in q:
                        st.page = int(q["page"])
                    return self._send(200, "ok", "text/plain")
                if u.path == "/_get":
                    data = st.objects.get(q.get("b"), {}).get(q.get("k"))
                    return self._send(200 if data is not None else 404, data or b"", "application/octet-stream")
                return self._send(404, "", "text/plain")
            body = self._body()
            with st.lock:
                st.st["requests"] += 1
            if st.down:
                return self._err(503, "SlowDown")
            # --- Signatur ---
            auth = self.headers.get("Authorization") or ""
            hdr = {k.lower(): v for k, v in self.headers.items()}
            try:
                assert auth.startswith("AWS4-HMAC-SHA256 ")
                parts = dict(x.strip().split("=", 1) for x in auth[len("AWS4-HMAC-SHA256 "):].split(","))
                cred = parts["Credential"].split("/")
                signed = parts["SignedHeaders"].split(";")
                amz = hdr["x-amz-date"]
                ph = hdr["x-amz-content-sha256"]
                assert cred[0] == KEY_ID and cred[1] == amz[:8] and cred[2] == REGION and cred[3] == "s3"
                assert "host" in signed and "x-amz-date" in signed and "x-amz-content-sha256" in signed
                skew = abs(datetime.strptime(amz, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp() - time.time())
                assert skew < 900
                good = expected_auth(self.command, u.path, u.query, hdr, signed, amz, ph, SECRET, REGION)
                assert hmac.compare_digest(good, parts["Signature"])
            except (AssertionError, KeyError, ValueError, IndexError):
                with st.lock:
                    st.st["sigfail"] += 1
                return self._err(403, "SignatureDoesNotMatch")
            if ph != hashlib.sha256(body).hexdigest():         # UNSIGNED-PAYLOAD gilt hier nicht: Saga signiert immer
                with st.lock:
                    st.st["hashfail"] += 1
                return self._err(400, "XAmzContentSHA256Mismatch")
            # --- Bucket bestimmen (virtual-hosted oder path-style) ---
            host = hdr.get("host", "").split(":")[0]
            segs = u.path.split("/", 2)
            if host.endswith(".localhost") and host != "localhost":
                bucket, key = host[:-len(".localhost")], unquote(u.path[1:])
                st.st["virtual"] += 1
            else:
                bucket, key = unquote(segs[1]) if len(segs) > 1 else "", unquote(segs[2]) if len(segs) > 2 else ""
                st.st["path"] += 1
            objs = st.objects.get(bucket)
            if objs is None:
                return self._err(404, "NoSuchBucket")
            q = dict(parse_qsl(u.query, keep_blank_values=True))
            m = self.command
            if not key and m == "GET" and q.get("list-type") == "2":
                return self._list(objs, q)
            if not key:
                return self._err(400, "InvalidRequest")
            if m == "POST" and "uploads" in q:
                uid = uuid.uuid4().hex
                with st.lock:
                    st.uploads[uid] = {"bucket": bucket, "key": key, "parts": {}}
                return self._send(200, f'<?xml version="1.0"?><InitiateMultipartUploadResult xmlns="{NS}"><Bucket>{bucket}</Bucket>'
                                       f'<Key>{escape(key)}</Key><UploadId>{uid}</UploadId></InitiateMultipartUploadResult>')
            if m == "PUT" and "uploadId" in q:
                up = st.uploads.get(q["uploadId"])
                if not up or up["key"] != key:
                    return self._err(404, "NoSuchUpload")
                et = '"' + hashlib.md5(body).hexdigest() + '"'
                with st.lock:
                    up["parts"][int(q["partNumber"])] = (body, et)
                    st.st["parts"] += 1
                return self._send(200, "", extra={"ETag": et})
            if m == "POST" and "uploadId" in q:
                up = st.uploads.get(q["uploadId"])
                if not up or up["key"] != key:
                    return self._err(404, "NoSuchUpload")
                try:
                    root = ET.fromstring(body)
                    want = [(int(p.findtext("PartNumber")), p.findtext("ETag")) for p in root.findall("Part")]
                except (ET.ParseError, TypeError, ValueError):
                    return self._err(400, "MalformedXML")
                nums = [n for n, _ in want]
                if not want or nums != sorted(nums) or any(up["parts"].get(n, (b"", ""))[1] != e for n, e in want):
                    return self._err(400, "InvalidPart")
                datas = [up["parts"][n][0] for n in nums]
                if any(len(d) < MIN_PART for d in datas[:-1]):
                    return self._err(400, "EntityTooSmall")
                data = b"".join(datas)
                if st.corrupt:
                    data = data[:-1] + bytes([data[-1] ^ 1])
                with st.lock:
                    objs[key] = data
                    del st.uploads[q["uploadId"]]
                    st.st["completes"] += 1
                return self._send(200, f'<?xml version="1.0"?><CompleteMultipartUploadResult xmlns="{NS}"><Key>{escape(key)}</Key>'
                                       f'<ETag>"x-{len(datas)}"</ETag></CompleteMultipartUploadResult>')
            if m == "DELETE" and "uploadId" in q:
                with st.lock:
                    st.uploads.pop(q["uploadId"], None)
                    st.st["aborts"] += 1
                return self._send(204)
            if m == "PUT":
                if st.corrupt and len(body) > 64:
                    body = body[:-1] + bytes([body[-1] ^ 1])
                with st.lock:
                    objs[key] = body
                    st.st["puts"] += 1
                return self._send(200, "", extra={"ETag": '"' + hashlib.md5(body).hexdigest() + '"'})
            if m in ("GET", "HEAD"):
                data = objs.get(key)
                if data is None:
                    return self._err(404, "NoSuchKey")
                return self._send(200, data, "application/octet-stream")
            if m == "DELETE":
                with st.lock:
                    objs.pop(key, None)
                    st.st["deletes"] += 1
                return self._send(204)
            return self._err(405, "MethodNotAllowed")

        def _list(self, objs, q):
            with st.lock:
                st.st["lists"] += 1
                keys = sorted(objs)
            prefix, delim = q.get("prefix", ""), q.get("delimiter", "")
            maxk = min(int(q.get("max-keys") or 1000), st.page)
            start = q.get("continuation-token", "")
            entries = []                                   # (sortierschluessel, art, wert)
            seen = set()
            for k in keys:
                if not k.startswith(prefix):
                    continue
                rest = k[len(prefix):]
                if delim and delim in rest:
                    cp = prefix + rest.split(delim, 1)[0] + delim
                    if cp not in seen:
                        seen.add(cp)
                        entries.append((cp, "p", cp))
                else:
                    entries.append((k, "k", k))
            entries = [e for e in entries if e[0] > start]
            page, more = entries[:maxk], len(entries) > maxk
            x = [f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{NS}"><Name>{BUCKET}</Name>'
                 f'<Prefix>{escape(prefix)}</Prefix><KeyCount>{len(page)}</KeyCount><MaxKeys>{maxk}</MaxKeys>'
                 f'<IsTruncated>{"true" if more else "false"}</IsTruncated>']
            for _, kind, v in page:
                if kind == "p":
                    x.append(f"<CommonPrefixes><Prefix>{escape(v)}</Prefix></CommonPrefixes>")
                else:
                    x.append(f"<Contents><Key>{escape(v)}</Key><Size>{len(objs.get(v, b''))}</Size><StorageClass>STANDARD</StorageClass></Contents>")
            if more:
                x.append(f"<NextContinuationToken>{escape(page[-1][0])}</NextContinuationToken>")
            x.append("</ListBucketResult>")
            return self._send(200, "".join(x))

        do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = handle_one
    return H


def start(port):
    st = Store()
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(st, port))
    srv.daemon_threads = True
    srv.store = st
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
