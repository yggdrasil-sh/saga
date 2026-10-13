"""Immich-Anbindung gegen ein nachgemachtes Immich 3.2 (Probe-Instanz, IMMICH_URL=http://127.0.0.1:18283)."""
import json, os, base64, sqlite3, sys, threading, urllib.request, urllib.parse, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from itsdangerous import TimestampSigner
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"); DB = os.environ.get("HK_TEST_DB", "/data/saga.db")
KEY = "TestKey_abc123"
A1, X1, X2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200; GIF = b"GIF89a" + b"\x00" * 200
SEEN = []
class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, body, ct="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.end_headers(); self.wfile.write(b)
    def auth(self):
        if self.headers.get("x-api-key") != KEY: self.send(401, {"message": "Invalid API key"}); return False
        return True
    def do_GET(self):
        if not self.auth(): return
        p = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(p.query); SEEN.append(("GET", p.path, q))
        if p.path == "/api/users/me": return self.send(200, {"name": "Alex", "email": "alex@example.org"})
        if p.path == "/api/albums": return self.send(200, [{"id": A1, "albumName": "Urlaub", "assetCount": 1, "albumThumbnailAssetId": X1}])
        if p.path in (f"/api/assets/{X1}", f"/api/assets/{X2}"):
            gif = X2 in p.path
            return self.send(200, {"id": X2 if gif else X1, "originalMimeType": "image/gif" if gif else "image/heic", "originalFileName": "tanz.gif" if gif else "IMG_0001.HEIC"})
        if p.path.endswith("/thumbnail"): return self.send(200, JPEG, "image/jpeg")
        if p.path.endswith("/original"): return self.send(200, GIF, "image/gif")
        self.send(404, {})
    def do_POST(self):
        if not self.auth(): return
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}"); SEEN.append(("POST", self.path, body))
        if self.path == "/api/search/metadata":
            items = [{"id": X1, "originalFileName": "IMG_0001.HEIC", "originalMimeType": "image/heic"}] if body.get("albumIds") else \
                    [{"id": X1, "originalFileName": "IMG_0001.HEIC", "originalMimeType": "image/heic"}, {"id": X2, "originalFileName": "tanz.gif", "originalMimeType": "image/gif"}]
            return self.send(200, {"albums": {}, "assets": {"items": items, "nextPage": "2" if body.get("page") == 1 and not body.get("albumIds") else None}})
        if self.path == "/api/search/smart":
            return self.send(200, {"albums": {}, "assets": {"items": [{"id": X2, "originalFileName": "tanz.gif"}], "nextPage": None}})
        self.send(404, {})
srv = ThreadingHTTPServer(("127.0.0.1", 18283), Fake); threading.Thread(target=srv.serve_forever, daemon=True).start()

def cookie(uid): return "saga=" + TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def call(m, p, b=None, uid="hk-test-a", raw=False):
    h = {"Host": HOST, "Cookie": cookie(uid), "X-Saga": "1", "Content-Type": "application/json"}
    req = urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, (r.read() if raw else json.loads(r.read())), r.headers.get("Content-Type")
    except urllib.error.HTTPError as e: return e.code, e.read(), None
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
try:
    U = "http://127.0.0.1:18283"          # vom Admin freigegeben (IMMICH_URL der Probe)
    st, s, _ = call("GET", "/api/settings/immich"); ok(f"Einstellungen: nicht verbunden, Vorschlag {s.get('suggest')}", st == 200 and not s["connected"] and s["suggest"] == U)
    # SSRF-Schutz: benutzerdefinierte Adressen
    for bad, why in [("http://example.com", "http ohne Freigabe"), ("https://localhost:18283", "interner Name (localhost)"),
                     ("https://169.254.169.254", "Metadaten-Adresse"), ("https://10.0.0.1", "private IP"), ("ftp://x.y", "falsches Schema"),
                     ("https://user:pw@immich.example.de", "Zugangsdaten in der Adresse"), ("javascript:alert(1)", "Unsinn")]:
        st, r, _ = call("POST", "/api/settings/immich", {"url": bad, "apikey": KEY}); ok(f"Abgelehnt: {why} ({st})", st == 400)
    st, _, _ = call("POST", "/api/settings/immich", {"url": U, "apikey": "falsch_123"}); ok(f"Falscher Schluessel abgelehnt ({st})", st == 400)
    st, _, _ = call("POST", "/api/settings/immich", {"url": U, "apikey": "<script>"}); ok(f"Ungueltiges Format abgelehnt ({st})", st == 400)
    st, r, _ = call("POST", "/api/settings/immich", {"url": U + "/api/", "apikey": KEY}); ok(f"Eigene Adresse + Schluessel verbunden, Adresse vereinheitlicht ({r})", st == 200 and r.get("name") == "Alex" and r.get("url") == U)
    st, s, _ = call("GET", "/api/settings/immich"); ok(f"Gespeicherte Adresse ({s.get('url')})", s["connected"] and s["url"] == U)
    sec = sqlite3.connect(DB).execute("SELECT secret FROM immich_key WHERE uid='hk-test-a'").fetchone()[0]
    ok("Schluessel verschluesselt gespeichert", KEY not in sec and len(sec) > 40)
    st, r, _ = call("GET", "/api/immich/albums"); ok(f"Alben ({r})", st == 200 and r["albums"][0]["name"] == "Urlaub")
    st, r, _ = call("POST", "/api/immich/assets", {}); ok(f"Zeitleiste: 2 Fotos, weitere Seite {r.get('next')}", st == 200 and len(r["items"]) == 2 and r["next"] == 2)
    meta = [x for x in SEEN if x[0] == "POST" and x[1] == "/api/search/metadata"][-1][2]
    ok(f"Zeitleiste fragt nur Bilder, neueste zuerst ({meta})", meta.get("type") == "IMAGE" and meta.get("order") == "desc")
    st, r, _ = call("POST", "/api/immich/assets", {"album": A1}); ok("Album-Filter", st == 200 and [x["id"] for x in r["items"]] == [X1])
    st, r, _ = call("POST", "/api/immich/assets", {"q": "Tanz"}); ok("Suche nach Bildinhalt", st == 200 and r["items"][0]["id"] == X2)
    st, r, ct = call("GET", f"/api/immich/thumb/{X1}", raw=True); ok(f"Vorschau ({ct})", st == 200 and ct == "image/jpeg")
    st, r, _ = call("POST", "/api/immich/import", {"id": X1}); ok(f"Foto uebernommen als Vorschau-JPEG ({r.get('name')}, {r.get('mime')})", st == 200 and r["url"].startswith("/files/") and r["mime"] == "image/jpeg" and r["name"] == "IMG_0001.jpg")
    st, f, ct = call("GET", r["url"], raw=True); ok(f"Datei abrufbar ({ct})", st == 200 and f[:3] == b"\xff\xd8\xff")
    st, r, _ = call("POST", "/api/immich/import", {"id": X2}); ok(f"GIF im Original uebernommen ({r.get('mime')})", st == 200 and r["mime"] == "image/gif")
    st, _, _ = call("POST", "/api/immich/import", {"id": "../../etc"}); ok(f"Ungueltige ID abgelehnt ({st})", st == 400)
    st, _, _ = call("GET", "/api/immich/thumb/../users"); ok(f"Pfad-Trick bei Vorschau abgelehnt ({st})", st in (400, 404))
    st, _, _ = call("GET", "/api/immich/albums", uid="hk-test-b"); ok(f"Andere Person ohne Schluessel: kein Zugriff ({st})", st == 409)
    st, _, _ = call("DELETE", "/api/settings/immich"); st2, s, _ = call("GET", "/api/settings/immich"); ok("Trennen", st == 200 and not s["connected"])
finally:
    cn = sqlite3.connect(DB)
    for tbl in ("note", "file", "immich_key"):
        try: cn.execute(f"DELETE FROM {tbl} WHERE uid IN ('hk-test-a','hk-test-b')")
        except sqlite3.OperationalError: pass
    cn.commit(); srv.shutdown()
print(f"\nIMMICH {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER"); sys.exit(0 if res and all(res) else 1)
