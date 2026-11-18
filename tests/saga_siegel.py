"""Saga-Siegel (2.5, Bugs-DB 2156, saga/siegel.py): mitgelieferte Seitensymbole /siegel/<farbe>/<motiv>.svg.

Geprueft: Auslieferung ohne Anmeldung (SVG mit CSP/nosniff/Cache, ETag/304; ?w= und .png -> PNG 64/128/256 px), 404 fuer
unbekannte Farben/Motive/Endungen/Pfade, alle 108 Dateien da und sauber (Positivliste, ids je Datei eindeutig),
GET /api/siegel (Anmeldung, Namen in der Sprache), Symbol setzen ueber PATCH/POST (Seite, Datenbank, Zeile) mit
Normalisierung und Ablehnung fremder/verbogener Pfade, API v1 (anlegen/aendern), MCP (Beschreibung, create_page, create_row),
App-Widget /api/app/page-image/<id>/icon (PNG), Unterseiten-Widget, veroeffentlichte Seite, Foederations-Helfer
(_safe_pic, _pic_out, siegel1 in den Faehigkeiten). Bestehendes bleibt: Emoji, eigenes Bild, "/files/" fremder Konten.

Wegwerf-Konten sg-*; raeumt am Ende auf (Seiten, Tokens, oeffentlicher Name)."""
import base64
import http.client
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

from itsdangerous import TimestampSigner
from PIL import Image

import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA, UB = "sg-anna", "sg-bert"
res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c or info is None else f"-> {str(info)[:400]}", flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, cookie=None, body=None, hdr=None, bearer=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Accept-Language": "de"}
    if cookie:
        h.update({"Cookie": "saga=" + cookie, "X-Saga": "1"})
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    data = None
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    h.update(hdr or {})
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    d = r.read()
    heads = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    try:
        d = json.loads(d) if d[:1] in (b"[", b"{") else d
    except ValueError:
        pass
    return r.status, heads, d


def png_size(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.size, im.mode, im.format


for u in (UA, UB):
    auth.get_user(u) or auth.create_user(u, None, u)
A, B = ck(UA), ck(UB)
made = []
R = "/siegel/weinrot/rabe.svg"
FARBEN = ("weinrot", "smaragd", "elfenbein")
try:
    # --- Auslieferung -------------------------------------------------------------------------------------------
    s, h, d = req("GET", R)
    ok(f"SVG ohne Anmeldung ({s}, {h.get('content-type')})", s == 200 and h.get("content-type", "").startswith("image/svg+xml")
       and isinstance(d, bytes) and d.startswith(b"<svg"))
    ok("SVG: CSP ohne Skripte + sandbox, nosniff, 30 Tage Cache, ETag", "script-src" not in h.get("content-security-policy", "x")
       and "default-src 'none'" in h.get("content-security-policy", "") and "sandbox" in h.get("content-security-policy", "")
       and h.get("x-content-type-options") == "nosniff" and "max-age=2592000" in h.get("cache-control", "") and h.get("etag"), h)
    s2, h2, _ = req("GET", R, hdr={"If-None-Match": h.get("etag", "")})
    ok(f"ETag -> 304 ({s2})", s2 == 304)
    for w, px in (("64", 64), ("72", 128), ("128", 128), ("160", 256), ("256", 256), ("2000", 256)):
        s, h, d = req("GET", R + "?w=" + w)
        ok(f"?w={w} -> PNG {px} px ({s}, {h.get('content-type')})", s == 200 and h.get("content-type") == "image/png"
           and png_size(d) == ((px, px), "RGBA", "PNG") and "content-security-policy" not in h, png_size(d) if s == 200 else d)
    s, h, d = req("GET", "/siegel/smaragd/kompass.png")
    ok(f".png ohne w -> 256 px ({s})", s == 200 and png_size(d)[0] == (256, 256))
    s, h, d = req("GET", "/siegel/smaragd/kompass.png?w=64")
    ok(".png?w=64 -> 64 px", s == 200 and png_size(d)[0] == (64, 64))
    ok("?w=64 und ?w=128: verschiedene ETags", req("GET", R + "?w=64")[1].get("etag") != req("GET", R + "?w=128")[1].get("etag"))
    s, h, d = req("GET", R + "?w=%C2%B2")
    ok(f"?w=² (keine ASCII-Ziffer) -> SVG, kein 500 ({s})", s == 200 and h.get("content-type", "").startswith("image/svg+xml"))
    s, h, d = req("GET", R + "?w=0")
    ok("?w=0 -> SVG", s == 200 and h.get("content-type", "").startswith("image/svg+xml"))
    bad = ["/siegel/blau/rabe.svg", "/siegel/weinrot/gibtsnicht.svg", "/siegel/weinrot/rabe.jpg", "/siegel/weinrot/rabe.SVG",
           "/siegel/weinrot/README.md", "/siegel/weinrot/rabe", "/siegel/weinrot/%2e%2e%2fsmaragd%2frabe.svg", "/siegel/weinrot/rabe.svg.png",
           "/siegel/Weinrot/rabe.svg", "/siegel/weinrot/rabe-256.png"]
    st = {p: req("GET", p)[0] for p in bad}
    ok("Unbekannte Farbe/Motiv/Endung/Pfad -> 404", all(v == 404 for v in st.values()), st)
    ok("/siegel/README.md (Verzeichnis ohne Farbe) -> 404", req("GET", "/siegel/README.md")[0] == 404)

    s, _, d = req("GET", "/api/version")
    ok(f"/api/version meldet siegel1 ({s})", s == 200 and "siegel1" in (d.get("features") or []))

    # --- Dateien vollstaendig und sauber ------------------------------------------------------------------------
    s, _, L = req("GET", "/api/siegel")
    ok(f"/api/siegel ohne Anmeldung -> 401 ({s})", s == 401)
    s, _, L = req("GET", "/api/siegel", A)
    ok(f"/api/siegel: 3 Farben, 36 Motive, Pfadmuster, Stufen ({s})", s == 200 and [c["id"] for c in L["colors"]] == list(FARBEN)
       and len(L["motifs"]) == 36 and len({m["id"] for m in L["motifs"]}) == 36 and L["path"] == "/siegel/{color}/{motif}.svg"
       and L["sizes"] == [64, 128, 256], L)
    lab = {m["id"]: m["label"] for m in L["motifs"]}
    ok("Namen deutsch (Rabe, Schlüssel, Karte - ohne ##Kontext)", lab.get("rabe") == "Rabe" and lab.get("schluessel") == "Schlüssel"
       and lab.get("karte") == "Karte" and not any("##" in v for v in lab.values()), lab)
    s, _, Le = req("GET", "/api/siegel", A, hdr={"Accept-Language": "en"})
    labe = {m["id"]: m["label"] for m in Le["motifs"]} if s == 200 else {}
    cole = {c["id"]: c["label"] for c in Le.get("colors", [])} if s == 200 else {}
    ok("Namen englisch (Raven, Key, Map, Emerald)", labe.get("rabe") == "Raven" and labe.get("schluessel") == "Key" and labe.get("karte") == "Map"
       and cole.get("smaragd") == "Emerald", (labe, cole))
    miss, ids_all, dirty = [], set(), []
    for f in FARBEN:
        for m in lab:
            for ext in (".svg", ".png"):
                if req("GET", f"/siegel/{f}/{m}{ext}")[0] != 200:
                    miss.append(f"{f}/{m}{ext}")
            with open(os.path.join(ROOT, "static", "siegel", f, m + ".svg"), encoding="utf-8") as fh:
                src = fh.read()
            root = ET.fromstring(src)
            for e in root.iter():
                tag = e.tag.split("}", 1)[-1]
                if tag in ("script", "style", "image", "use", "foreignObject", "a", "text") or any(k.startswith("on") or "href" in k for k in e.attrib):
                    dirty.append(f"{f}/{m}: {tag}")
                if e.get("id"):
                    if e.get("id") in ids_all or not e.get("id").startswith(f"s{f[0]}-{m}-"):
                        dirty.append(f"{f}/{m}: id {e.get('id')}")
                    ids_all.add(e.get("id"))
            if root.get("viewBox") != "0 0 64 64" or re.search(r"https?:(?!//www\.w3\.org/2000/svg\")", src):
                dirty.append(f"{f}/{m}: viewBox/Verweis")
    ok("Alle 108 SVG + 108 PNG werden ausgeliefert", not miss, miss)
    ok(f"SVG sauber: keine Skripte/Stile/Verweise, ids je Datei eindeutig ({len(ids_all)} ids)", not dirty and len(ids_all) == 108 * 4, dirty[:5])
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "siegel.py"), "check"], capture_output=True, text=True)
    ok(f"tools/siegel.py check ({r.stdout.strip()[-40:]})", r.returncode == 0 and r.stdout.strip().endswith("ok"))

    # --- Symbol setzen (Web-API) ------------------------------------------------------------------------------
    s, _, d = req("POST", "/api/notes", A, {"title": "Siegel-Seite", "icon": R})
    pg = d.get("id") if isinstance(d, dict) else None
    made.append(pg)
    ok(f"Seite mit Siegel anlegen ({s})", s == 200 and req("GET", f"/api/notes/{pg}", A)[2].get("icon") == R)
    s, _, d = req("PATCH", f"/api/notes/{pg}", A, {"icon": "  /siegel/elfenbein/yggdrasil.svg "})
    ok(f"PATCH: Leerzeichen werden entfernt ({s})", s == 200 and req("GET", f"/api/notes/{pg}", A)[2].get("icon") == "/siegel/elfenbein/yggdrasil.svg")
    lst = req("GET", "/api/notes", A)[2]
    lst = lst if isinstance(lst, list) else lst.get("notes", [])
    ok("Liste /api/notes traegt das Siegel", any(n.get("id") == pg and n.get("icon") == "/siegel/elfenbein/yggdrasil.svg" for n in lst))
    rej = {}
    for v in ("/siegel/weinrot/gibtsnicht.svg", "/siegel/blau/rabe.svg", "/siegel/weinrot/rabe.svg?w=64", "/siegel/weinrot/rabe.png",
              "/siegel/", "/siegel/weinrot/../smaragd/rabe.svg", "/siegel/weinrot/rabe.svg#x", "/siegel/WEINROT/rabe.svg"):
        rej[v] = req("PATCH", f"/api/notes/{pg}", A, {"icon": v})[0]
    ok("PATCH: unbekannte/verbogene Siegel-Pfade -> 400", all(x == 400 for x in rej.values()), rej)
    ok("... Symbol unveraendert", req("GET", f"/api/notes/{pg}", A)[2].get("icon") == "/siegel/elfenbein/yggdrasil.svg")
    s, _, d = req("POST", "/api/notes", A, {"title": "x", "icon": "/siegel/weinrot/nix.svg"})
    ok(f"POST mit unbekanntem Siegel -> 400 ({s})", s == 400)
    ok("Emoji weiter erlaubt", req("PATCH", f"/api/notes/{pg}", A, {"icon": "🦉"})[0] == 200 and req("GET", f"/api/notes/{pg}", A)[2].get("icon") == "🦉")
    ok("Fremdes /files/ weiter abgelehnt", req("PATCH", f"/api/notes/{pg}", A, {"icon": "/files/" + "a" * 24})[0] == 400)
    ok("Symbol entfernen", req("PATCH", f"/api/notes/{pg}", A, {"icon": ""})[0] == 200 and not req("GET", f"/api/notes/{pg}", A)[2].get("icon"))
    req("PATCH", f"/api/notes/{pg}", A, {"icon": R})
    s, _, d = req("POST", "/api/notes", A, {"title": "Siegel-DB", "kind": "database", "icon": "/siegel/smaragd/truhe.svg"})
    db = d.get("id") if isinstance(d, dict) else None
    made.append(db)
    ok(f"Datenbank mit Siegel ({s})", s == 200 and req("GET", f"/api/notes/{db}", A)[2].get("icon") == "/siegel/smaragd/truhe.svg")
    s, _, d = req("POST", "/api/notes", A, {"title": "Zeile", "parent_id": db, "icon": "/siegel/elfenbein/teetasse.svg"})
    row = d.get("id") if isinstance(d, dict) else None
    s2, _, rows = req("GET", f"/api/notes/{db}/rows", A)
    items = rows.get("rows", rows) if isinstance(rows, dict) else rows
    ok(f"Datenbank-Zeile mit Siegel, in den Zeilen sichtbar ({s}, {s2})", s == 200 and any(r.get("id") == row and r.get("icon") == "/siegel/elfenbein/teetasse.svg"
                                                                          for r in (items or []) if isinstance(r, dict)), str(rows)[:300])
    s, _, d = req("PATCH", f"/api/notes/{pg}", B, {"icon": "/siegel/smaragd/rabe.svg"})
    ok(f"Fremde Seite ohne Freigabe: kein Setzen ({s})", s in (403, 404) and req("GET", f"/api/notes/{pg}", A)[2].get("icon") == R)

    # --- Unterseiten-Widget, veroeffentlichte Seite -----------------------------------------------------------
    s, _, d = req("POST", "/api/notes", A, {"title": "Kind", "parent_id": pg, "icon": "/siegel/weinrot/kerze.svg"})
    kid = d.get("id") if isinstance(d, dict) else None
    blk = {"id": "sp1", "type": "widget", "data": {"v": 1, "kind": "subpages", "cfg": {}}}
    s, _, d = req("POST", f"/api/notes/{pg}/widgets/preview", A, {"block": blk})
    its = ((((d or {}).get("widget") or {}).get("data") or {}).get("items") or []) if isinstance(d, dict) else []
    ok(f"Unterseiten-Widget liefert das Siegel ({s})", any(x.get("id") == kid and x.get("icon") == "/siegel/weinrot/kerze.svg" for x in its), d)
    req("PUT", "/api/account/public-name", A, {"name": "sg-anna-pub"})
    s, _, d = req("PUT", f"/api/notes/{pg}/publish", A, {"slug": "siegel-seite", "children": True})
    url = urllib.parse.urlparse((d or {}).get("url", "")).path if isinstance(d, dict) else ""
    s2, h2, html = req("GET", url or "/@sg-anna-pub/siegel-seite")
    html = html.decode() if isinstance(html, bytes) else str(html)
    ok(f"Veroeffentlicht: Siegel als <img> im Kopf ({s}, {s2})", s2 == 200 and f'<img class="pub-icon" src="{R}" alt="">' in html, html[:300])
    ok("Veroeffentlicht: Unterseite mit Siegel", 'src="/siegel/weinrot/kerze.svg"' in html)
    s3, h3, _ = req("GET", R, hdr={"Cookie": ""})
    ok("Siegel ohne Sitzung abrufbar (Besucher)", s3 == 200)
    req("DELETE", f"/api/notes/{pg}/publish", A)

    # --- App: Widget-Bild, API v1, MCP ------------------------------------------------------------------------
    T = req("POST", "/api/settings/tokens", A, {"name": "Siegel-Widget"})[2].get("token")
    s, h, d = req("GET", f"/api/app/page-image/{pg}/icon", bearer=T)
    ok(f"App-Widget: Symbolbild = PNG 256 ({s}, {h.get('content-type')})", s == 200 and h.get("content-type") == "image/png" and png_size(d)[0] == (256, 256))
    s, h, d = req("GET", f"/api/app/page-image/{pg}/icon?w=64", bearer=T)
    ok("App-Widget ?w=64 -> 64 px", s == 200 and png_size(d)[0] == (64, 64))
    s, _, d = req("POST", "/api/account/api-tokens", A, {"name": "Siegel-API", "scopes": ["write:pages", "write:db", "mcp"]})
    V = d.get("token") if isinstance(d, dict) else None
    s, _, d = req("POST", "/api/v1/pages", bearer=V, body={"title": "API-Siegel", "icon": "/siegel/smaragd/horn.svg"})
    ap = d.get("id") if isinstance(d, dict) else None
    made.append(ap)
    ok(f"API v1: Seite mit Siegel anlegen ({s})", s == 201 and req("GET", f"/api/v1/pages/{ap}", bearer=V)[2].get("icon") == "/siegel/smaragd/horn.svg")
    s, _, d = req("PATCH", f"/api/v1/pages/{ap}", bearer=V, body={"icon": "/siegel/smaragd/nix.svg"})
    ok(f"API v1: unbekanntes Siegel -> 400 ({s})", s == 400)
    s, _, d = req("PATCH", f"/api/v1/pages/{ap}", bearer=V, body={"icon": "/siegel/elfenbein/stern.svg"})
    ok(f"API v1: Siegel aendern ({s})", s == 200 and req("GET", f"/api/v1/pages/{ap}", bearer=V)[2].get("icon") == "/siegel/elfenbein/stern.svg")

    def rpc(method, params):
        return req("POST", "/mcp", bearer=V, body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                   hdr={"MCP-Protocol-Version": "2025-06-18"})[2]
    tl = {x["name"]: x for x in (rpc("tools/list", {}).get("result") or {}).get("tools", [])}
    desc = json.dumps({k: tl[k]["inputSchema"]["properties"]["icon"]["description"] for k in ("create_page", "create_database", "create_row") if k in tl},
                      ensure_ascii=False)
    ok("MCP: icon-Beschreibung nennt Siegel-Pfad, Farben und Motive", desc.count("/siegel/<farbe>/<motiv>.svg") == 3 and "smaragd" in desc
       and "teetasse" in desc, desc[:300])
    d = rpc("tools/call", {"name": "create_page", "arguments": {"title": "MCP-Siegel", "icon": "/siegel/weinrot/laterne.svg"}})
    txt = "".join(c.get("text", "") for c in (d.get("result") or {}).get("content") or [])
    mid = int(re.search(r'ID (\d+)', txt).group(1)) if re.search(r'ID (\d+)', txt) else None
    made.append(mid)
    ok("MCP: create_page mit Siegel", mid and req("GET", f"/api/notes/{mid}", A)[2].get("icon") == "/siegel/weinrot/laterne.svg", txt[:200])
    d = rpc("tools/call", {"name": "create_page", "arguments": {"title": "MCP-falsch", "icon": "/siegel/weinrot/nix.svg"}})
    ok("MCP: unbekanntes Siegel -> Fehler", (d.get("result") or {}).get("isError") is True or "error" in d, d)
    d = rpc("tools/call", {"name": "create_row", "arguments": {"database_id": db, "title": "MCP-Zeile", "icon": "/siegel/smaragd/katze.svg"}})
    txt = "".join(c.get("text", "") for c in (d.get("result") or {}).get("content") or [])
    rid = int(re.search(r'ID (\d+)', txt).group(1)) if re.search(r'ID (\d+)', txt) else None
    ok("MCP: create_row mit Siegel", rid and req("GET", f"/api/notes/{rid}", A)[2].get("icon") == "/siegel/smaragd/katze.svg", txt[:200])

    # --- Foederation, Veroeffentlichen, Widgets (Helfer im Prozess) -------------------------------------------
    from saga import federation as F, page_widgets as PW, publish as PB, siegel as SG
    ok("siegel1 in CAPS2 und PAGE_CAPS", "siegel1" in F.CAPS2 and "siegel1" in F.PAGE_CAPS)
    ok("_safe_pic: Siegel als Symbol ja, als Titelbild nein, verbogen nein", F._safe_pic(R, True) == R and F._safe_pic(R, False) == ""
       and F._safe_pic("/siegel/weinrot/nix.svg", True) == "" and F._safe_pic(R + "?w=64", True) == "" and F._safe_pic("/siegel/", True) == "")
    ok("_pic_out: Siegel bleibt, andere Pfade fallen weg", F._pic_out(R, 1) == R and F._pic_out("/siegel/weinrot/nix.svg", 1) == ""
       and F._pic_out("🦉", 1) == "🦉")
    ok("Unterseiten-Widget _sp_icon: Siegel ja, verbogen nein", PW._sp_icon(R) == R and PW._sp_icon("/siegel/x/rabe.svg") == "")
    ok("Veroeffentlichen _icon_html: Siegel als img, verbogen nichts", PB._icon_html(None, R, "c") == f'<img class="c" src="{R}" alt="">'
       and PB._icon_html(None, "/siegel/x/y.svg", "c") == "")
    ok("siegel.is_siegel/parse", SG.is_siegel(R) and not SG.is_siegel(" " + R) and SG.parse(" " + R) == ("weinrot", "rabe")
       and SG.parse("/siegel/weinrot/nix.svg") is None and SG.parse(None) is None)
finally:
    for n in made:
        if n:
            req("DELETE", f"/api/notes/{n}", A)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid IN (?, ?)", (UA, UB))
        c.execute("DELETE FROM api_token WHERE uid=?", (UA,))
        c.execute("DELETE FROM app_token WHERE uid=?", (UA,))
        c.execute("DELETE FROM note_publish WHERE uid=?", (UA,))
        c.execute("DELETE FROM public_name WHERE uid=?", (UA,))
    srv = os.path.join(os.environ.get("STATE_DIR", "/tmp"), "server.log")
    try:
        with open(srv, encoding="utf-8", errors="replace") as fh:
            n500 = len(re.findall(r'" 500\b|Traceback', fh.read()))
    except OSError:
        n500 = 0
    print(f"Serverfehler: {n500}")
    res.append(n500 == 0)
    print(f"\nSIEGEL {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)
