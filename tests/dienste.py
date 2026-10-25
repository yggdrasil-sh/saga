"""Dienst-Symbole fuer die Apps: /api/services liefert den Symbolnamen, /api/services/icon/<name> das PNG
(einmal vom CDN geholt, dann aus /data); ohne Sitzung oder mit unsinnigem Namen nichts. Zeigt ICON_CDN auf
127.0.0.1 (Test-Stack), laeuft dort tests/fakecdn.py."""
import base64, io, json, os, sys, urllib.request, urllib.parse
from itsdangerous import TimestampSigner
from PIL import Image
B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
def ck(uid): return TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": uid}).encode())).decode()
H = {"Host": HOST, "Cookie": "saga=" + ck("hk-test-a"), "X-Saga": "1"}
res = []
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakecdn  # noqa: E402
fakecdn.start(os.environ.get("ICON_CDN"))     # ICON_CDN=http://127.0.0.1:18310 (wie im Test-Server): ohne Internet
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)
def raw(p, h=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, headers=H if h is None else h), timeout=60) as r: return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e: return e.code, e.headers, e.read()
st, h, d = raw("/api/services/icon/nextcloud")
im = Image.open(io.BytesIO(d)) if st == 200 else None
ok(f"Symbol nextcloud -> {h.get('Content-Type')} {im and im.size}", st == 200 and h.get("Content-Type") == "image/png" and im is not None)
st2, _, d2 = raw("/api/services/icon/nextcloud")
ok("Zweiter Abruf aus dem Zwischenspeicher (gleiche Bytes)", st2 == 200 and d2 == d)
ok("Ohne Sitzung -> 404", raw("/api/services/icon/nextcloud", {"Host": HOST})[0] == 404)
ok("Unsinniger Name -> 404", raw("/api/services/icon/..%2Fetc")[0] == 404 and raw("/api/services/icon/Nextcloud!")[0] == 404)
def cdn_down():
    """Ist das Symbol-CDN gerade nicht erreichbar (Zeitueberschreitung, Netzfehler, 5xx)? Gleicher Abruf wie der Server."""
    cdn = (os.environ.get("ICON_CDN", "https://cdn.jsdelivr.net/gh/selfhst/icons")).rstrip("/")
    if not cdn:
        return False
    try:
        with urllib.request.urlopen(cdn + "/png/gibt-es-nicht-hk-test.png", timeout=10) as r:
            return r.status >= 500
    except urllib.error.HTTPError as e:
        return e.code >= 500
    except Exception:
        return True
# Unbekanntes Symbol: 404 (und einen Tag gemerkt). 502 heisst "CDN kurz weg" - nur dann gilt der Fall als uebersprungen,
# wenn das CDN nachweislich nicht erreichbar ist; antwortet es wieder, muss ein zweiter Versuch 404 liefern.
s = raw("/api/services/icon/gibt-es-nicht-hk-test")[0]
if s in (502, 504):
    if cdn_down():
        print(f"SKIP Unbekanntes Symbol: Server {s}, Symbol-CDN nicht erreichbar - uebersprungen")
    else:
        s = raw("/api/services/icon/gibt-es-nicht-hk-test")[0]
        ok(f"Unbekanntes Symbol -> 404 (zweiter Versuch nach {s})", s == 404)
else:
    ok(f"Unbekanntes Symbol -> 404 ({s})", s == 404)
print(f"\nDIENSTE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER"); sys.exit(0 if all(res) else 1)
