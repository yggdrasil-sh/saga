"""SVG-Bereinigung (2.5, saga/svg_clean.py - Trilium-Import): boesartige SVGs werden entschaerft oder abgelehnt, harmlose
bleiben erhalten. Dazu (mit Server): allgemeines Hochladen nimmt SVG weiterhin NICHT als Bild an (nur der Import
bereinigt), und /files/<id> liefert ein Import-SVG mit CSP sandbox + nosniff aus (tests/import_trilium.py prueft das am
Import). Erfundene Testdaten."""
import base64
import http.client
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import svg_clean as S  # noqa: E402

res = []


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def svg(inner, attrs=""):
    return f'<svg {NS} width="10" height="10" {attrs}>{inner}</svg>'.encode()


FORBIDDEN = (b"<script", b"onload", b"onclick", b"onerror", b"javascript", b"vbscript", b"x.invalid", b"<!doctype", b"<!entity",
             b"foreignobject", b"<iframe", b"<animate", b"<set", b"behavior", b"expression(", b"@import", b"data:text", b"<a ",
             b"-moz-binding", b"<handler", b"<listener")


def clean_ok(name, data, keep=b""):
    out = S.clean(data)
    low = (out or b"").lower()
    bad = [x for x in FORBIDDEN if x in low]
    ok(f"entschaerft: {name}", out is not None and not bad and (not keep or keep in out), (bad, out))


def main():
    # --- harmlos bleibt erhalten -----------------------------------------------------------------------------------------
    good = svg('<defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs>'
               '<rect width="10" height="10" fill="url(#g)"/><use xlink:href="#g"/><text x="1" y="5">Hallo &amp; Welt</text>'
               '<style>.a{fill:blue} @media (min-width:1px){.b{stroke:red}}</style>')
    out = S.clean(good) or b""
    ok("harmloses SVG: Gradient, url(#id), use #id, Text (escaped), <style> bleiben", b'fill="url(#g)"' in out and b"#g" in out
       and b"Hallo &amp; Welt" in out and b".a{fill:blue}" in out and out.startswith(b"<?xml"), out)
    ok("Inkscape-Elemente/Metadaten fallen weg, Grafik bleibt", b"sodipodi" not in (S.clean(svg(
        '<sodipodi:namedview xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"/><circle r="3"/>')) or b"x")
       and b"<circle" in (S.clean(svg('<metadata/><circle r="3"/>')) or b""))
    exp = (b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
           b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">\n' + svg('<rect width="1" height="1"/>'))
    ok("SVG mit Standard-DOCTYPE (ohne internen Teil): angenommen, DOCTYPE entfernt", (S.clean(exp) or b"").count(b"<rect") == 1
       and b"DOCTYPE" not in (S.clean(exp) or b""))
    ok("eingebettetes Rasterbild (data:image/png;base64) bleibt", b"data:image/png" in (S.clean(svg(
        '<image width="1" height="1" xlink:href="data:image/png;base64,iVBORw0KGgo="/>')) or b""))
    ok("SVG ohne Namensraum: angenommen und mit SVG-Namensraum geschrieben",
       b'xmlns="http://www.w3.org/2000/svg"' in (S.clean(b'<svg width="2" height="2"><rect width="1" height="1"/></svg>') or b""))

    # --- boesartig: entschaerft --------------------------------------------------------------------------------------------
    clean_ok("script-Element", svg('<script>alert(1)</script><rect width="1" height="1"/>'), b"<rect")
    clean_ok("script mit CDATA", svg('<script><![CDATA[alert(1)]]></script><circle r="1"/>'), b"<circle")
    clean_ok("on*-Attribute (onload, onclick, onerror)", svg('<rect width="1" height="1" onclick="alert(1)" onerror="x"/>', 'onload="alert(1)"'), b"<rect")
    clean_ok("a mit javascript:-Verweis (Inhalt bleibt)", svg('<a xlink:href="javascript:alert(1)"><rect width="1" height="1"/></a>'), b"<rect")
    clean_ok("a mit href (SVG2) auf fremde Seite", svg('<a href="http://x.invalid/"><circle r="1"/></a>'), b"<circle")
    clean_ok("use auf fremde Datei", svg('<use xlink:href="http://x.invalid/s.svg#a"/><use href="data:image/svg+xml;base64,AA"/><rect width="1" height="1"/>'), b"<rect")
    clean_ok("image auf fremde Adresse / data:text/html", svg('<image xlink:href="http://x.invalid/a.png" width="1" height="1"/>'
                                                            '<image href="data:text/html;base64,PHNjcmlwdD4=" width="1" height="1"/><rect width="1" height="1"/>'), b"<rect")
    clean_ok("Animation setzt href auf javascript:", svg('<a><animate attributeName="href" values="javascript:alert(1)"/>'
                                                         '<set attributeName="onmouseover" to="alert(1)"/><text>x</text></a>'), b"<text")
    clean_ok("fill/filter/mask mit url() nach aussen", svg('<rect width="1" height="1" fill="url(http://x.invalid/#a)" '
                                                          'filter="url(\'http://x.invalid/f\')" mask="url(javascript:alert(1))"/>'), b"<rect")
    clean_ok("style-Attribut mit behavior/expression/url()", svg('<rect width="1" height="1" style="fill:red;behavior:url(x.htc);'
                                                                 'width:expression(alert(1));background:url(http://x.invalid/)"/>'), b"fill:red")
    clean_ok("<style> mit @import, @font-face, fremder url(), -moz-binding",
             svg('<style>@import url(http://x.invalid/a.css); @font-face{src:url(http://x.invalid/f.woff)} .a{fill:url(http://x.invalid/x)}</style><rect width="1" height="1"/>'),
             b"<rect")
    clean_ok("<style> mit Backslash-Escapes (versteckte url/expression) faellt weg",
             svg('<style>.a{background:u\\72l(http://x.invalid/)}</style><rect width="1" height="1"/>'), b"<rect")
    clean_ok("iframe/embed/object/handler im SVG", svg('<iframe src="http://x.invalid/"/><embed src="x"/><object data="x"/>'
                                                       '<handler xmlns:ev="http://www.w3.org/2001/xml-events" ev:event="load">alert(1)</handler><rect width="1" height="1"/>'), b"<rect")
    clean_ok("foreignObject in switch: Alternative bleibt", svg('<switch><foreignObject width="5" height="5"><div xmlns="http://www.w3.org/1999/xhtml">'
                                                                '<script>alert(1)</script></div></foreignObject><text>Ersatz</text></switch>'), b"Ersatz")
    clean_ok("xml:base/xlink:show/xlink:actuate fallen weg", svg('<g xml:base="http://x.invalid/" xlink:show="new" xlink:actuate="onLoad"><rect width="1" height="1"/></g>'), b"<rect")
    clean_ok("Verarbeitungsanweisung/Kommentar", b'<?xml version="1.0"?><?xml-stylesheet href="http://x.invalid/a.xsl"?><!-- x -->' +
             svg('<rect width="1" height="1"/>'), b"<rect")
    out = S.clean(svg('<rect width="1" height="1"/>', 'onload="alert(1)"')) or b""
    ok("ergebnis wird erneut sauber geparst (stabil)", S.clean(out) is not None and S.clean(out).count(b"<rect") == 1)

    # --- zweifelhaft: abgelehnt (bleibt Datei) --------------------------------------------------------------------------
    for name, data in (
        ("foreignObject (HTML im SVG)", svg('<foreignObject width="5" height="5"><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>')),
        ("DOCTYPE mit internem Teil / Entitaeten (Billion Laughs)", b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa">'
         b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'),
        ("externe Entitaet (XXE)", b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
         b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'),
        ("unbekannte Entitaet ohne DOCTYPE", b'<svg xmlns="http://www.w3.org/2000/svg"><text>&x;</text></svg>'),
        ("Wurzel ist kein svg (HTML)", b'<html><body><script>alert(1)</script></body></html>'),
        ("Wurzel svg in fremdem Namensraum", b'<svg xmlns="http://x.invalid/ns"><rect/></svg>'),
        ("kaputtes XML", b'<svg xmlns="http://www.w3.org/2000/svg"><rect'),
        ("kein UTF-8", "<svg xmlns='http://www.w3.org/2000/svg'><text>ä</text></svg>".encode("latin-1")),
        ("zu tief verschachtelt", svg("<g>" * (S.MAX_DEPTH + 5) + "</g>" * (S.MAX_DEPTH + 5))),
        ("zu viele Elemente", svg("<rect/>" * (S.MAX_NODES + 5))),
        ("zu gross", b"<svg>" + b" " * (S.MAX_BYTES + 1) + b"</svg>"),
        ("leer", b""),
    ):
        ok(f"abgelehnt: {name}", S.clean(data) is None)
    ok("Erkennung: SVG-Kopf (mit BOM, XML-Kopf, Kommentar); PNG/Text nicht", S.looks_like_svg(b"\xef\xbb\xbf  <svg x>")
       and S.looks_like_svg(b'<?xml version="1.0"?>\n<!-- c --><svg>') and not S.looks_like_svg(b"\x89PNG\r\n")
       and not S.looks_like_svg(b"<html><svg>") is True)

    # --- mit Server: allgemeines Hochladen bleibt ohne SVG-Bilder --------------------------------------------------------
    base = os.environ.get("HK_TEST_BASE")
    if base and os.environ.get("SESSION_SECRET"):
        from itsdangerous import TimestampSigner
        import auth
        u = "svg-t1"
        auth.get_user(u) or auth.create_user(u, "Svg-Pass-1234", u)
        ck = TimestampSigner(os.environ["SESSION_SECRET"]).sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()
        b_ = urllib.parse.urlparse(base)
        host = urllib.parse.urlparse(os.environ.get("BASE_URL", base)).netloc

        def upload(field, name, data):
            bd = "----svgtest"
            body = (f'--{bd}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\nContent-Type: image/svg+xml\r\n\r\n').encode() \
                + data + f"\r\n--{bd}--\r\n".encode()
            c = http.client.HTTPConnection(b_.hostname, b_.port, timeout=30)
            c.request("POST", "/api/files", body=body, headers={"Host": host, "Cookie": "saga=" + ck, "X-Saga": "1",
                                                                          "Content-Type": f"multipart/form-data; boundary={bd}"})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        s, d = upload("image", "a.svg", svg('<script>alert(1)</script>'))
        ok("Hochladen als Bild: SVG abgelehnt (415, wie bisher)", s == 415, (s, d))
        s, d = upload("file", "a.svg", svg('<script>alert(1)</script>'))
        url = (d.get("file") or {}).get("url", "")
        c = http.client.HTTPConnection(b_.hostname, b_.port, timeout=30)
        c.request("GET", url or "/files/x", headers={"Host": host, "Cookie": "saga=" + ck})
        r = c.getresponse()
        r.read()
        h = {k.lower(): v for k, v in r.getheaders()}
        ok("Hochladen als Datei: nur Download (octet-stream, attachment, CSP sandbox)", s == 200 and r.status == 200
           and h.get("content-type") == "application/octet-stream" and "attachment" in h.get("content-disposition", "")
           and "sandbox" in h.get("content-security-policy", ""), (s, d, r.status, h))
        from saga.userdata import delete_user_data
        delete_user_data(u)
        from db import connect
        with connect() as c2, c2:
            c2.execute("DELETE FROM users WHERE username=?", (u,))


if __name__ == "__main__":
    main()
    print(f"\nSVG-SICHERHEIT {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)
