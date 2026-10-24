"""Bughunt D11: unbekannte Adressen im Browser -> gestaltete 404 (HTML), API/Apps weiter JSON."""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080")
HOST = urllib.parse.urlparse(os.environ.get("BASE_URL", B)).netloc
ok = bad = 0


def get(path, accept):
    rq = urllib.request.Request(B + path, headers={"Accept": accept, "Host": HOST})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = op.open(rq, timeout=10)
        return r.status, r.headers.get("content-type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()


def check(name, cond, info=""):
    global ok, bad
    if cond:
        ok += 1; print("OK  ", name)
    else:
        bad += 1; print("FAIL", name, info)


s, ct, b = get("/gibtesnicht", "text/html,application/xhtml+xml")
check("Browser: unbekannte Adresse -> HTML-404", s == 404 and ct.startswith("text/html") and b"<html" in b, (s, ct))
check("HTML-404 mit Viewport (Handy)", b"viewport" in b)
s, ct, b = get("/gibtesnicht", "*/*")
check("Ohne Browser-Accept: JSON wie bisher", s == 404 and "json" in ct, (s, ct))
s, ct, b = get("/api/gibtesnicht", "text/html")
check("API bleibt JSON", s == 404 and "json" in ct, (s, ct))
s, ct, b = get("/static/gibtesnicht.js", "text/html")
check("Statische Dateien bleiben ohne HTML-Seite", s == 404 and not ct.startswith("text/html"), (s, ct))
print()
print("FEHLERSEITEN %d/%d - %s" % (ok, ok + bad, "ALLES GRUEN" if not bad else "FEHLER"))
sys.exit(1 if bad else 0)
