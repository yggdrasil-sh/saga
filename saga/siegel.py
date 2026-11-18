"""Saga-Siegel (2.5, Bugs-DB 2156): mitgelieferte Seitensymbole - 36 Motive in drei Farben, auf jedem Saga-Server gleich
(wie die Titelbild-Galerie, saga/covers.py).

Als Seitensymbol gilt genau der Pfad /siegel/<farbe>/<motiv>.svg. Ausgeliefert ohne Anmeldung (veroeffentlichte Seiten):
  /siegel/<farbe>/<motiv>.svg          das SVG (Web)
  /siegel/<farbe>/<motiv>.svg?w=<px>   PNG in 64/128/256 px (naechstgroessere Stufe, darueber 256) - fuer Apps ohne SVG,
                                       wie /files/<id>?w=
  /siegel/<farbe>/<motiv>.png[?w=]     dasselbe PNG (ohne w: 256 px)
GET /api/siegel: Farben und Motive mit Namen in der Kontosprache (Auswahl im Web, spaeter in den Apps).
Dateien: static/siegel/<farbe>/<motiv>.svg und <motiv>.png (256 px, mit Chromium gerendert - tools/siegel.py); 64/128 px
verkleinert der Server daraus (einmal je Groesse, im Speicher). Herkunft: static/siegel/README.md.
"""
import hashlib
import io
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from saga.core import HERE, _api_guard, _bad, t

router = APIRouter()

DIR = os.path.join(HERE, "static", "siegel")
FARBEN = ("weinrot", "smaragd", "elfenbein")
# Reihenfolge der Auswahl (thematisch, wie die Vorschau der Vorlage)
MOTIV_IDS = ("yggdrasil", "rabe", "drachenboot", "horn", "rune", "buch", "feder", "schriftrolle", "kerze", "laterne", "mond", "sonne",
             "nordlicht", "berg", "wald", "welle", "kompass", "karte", "schluessel", "schild", "hammer", "axt", "krone", "kristall",
             "trank", "feuer", "stern", "herz", "brief", "sanduhr", "zahnrad", "truhe", "server", "terminal", "katze", "teetasse")
_MOTIVE = frozenset(MOTIV_IDS)
SIZES = (64, 128, 256)
PATH_RE = re.compile(r"/siegel/(weinrot|smaragd|elfenbein)/([a-z]{2,20})\.svg")
_NAME_RE = re.compile(r"([a-z]{2,20})\.(svg|png)")
CACHE = "public, max-age=2592000"     # 30 Tage (wie Titelbilder); ETag aendert sich mit der Datei
# SVG nie als Dokument mit Skripten - die Dateien enthalten keine (tools/siegel.py check), das ist die zweite Sicherung
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


def _farben():
    return {"weinrot": t("Weinrot"), "smaragd": t("Smaragd"), "elfenbein": t("Elfenbein")}


def _namen():
    return {"yggdrasil": t("Yggdrasil"), "rabe": t("Rabe"), "drachenboot": t("Drachenboot"), "horn": t("Horn"), "rune": t("Rune"),
            "buch": t("Buch"), "feder": t("Feder"), "schriftrolle": t("Schriftrolle"), "kerze": t("Kerze"), "laterne": t("Laterne"),
            "mond": t("Mond"), "sonne": t("Sonne"), "nordlicht": t("Nordlicht"), "berg": t("Berg"), "wald": t("Wald"),
            "welle": t("Welle"), "kompass": t("Kompass"), "karte": t("Karte##Siegel"), "schluessel": t("Schlüssel##Siegel"), "schild": t("Schild"),
            "hammer": t("Hammer"), "axt": t("Axt"), "krone": t("Krone"), "kristall": t("Kristall"), "trank": t("Trank"),
            "feuer": t("Feuer"), "stern": t("Stern"), "herz": t("Herz"), "brief": t("Brief"), "sanduhr": t("Sanduhr"),
            "zahnrad": t("Zahnrad"), "truhe": t("Truhe"), "server": t("Server"), "terminal": t("Terminal"), "katze": t("Katze"),
            "teetasse": t("Teetasse")}


def path_of(farbe, motiv):
    return f"/siegel/{farbe}/{motiv}.svg"


def parse(v):
    """'/siegel/<farbe>/<motiv>.svg' eines mitgelieferten Siegels -> (farbe, motiv), sonst None."""
    m = PATH_RE.fullmatch(v.strip()) if isinstance(v, str) else None
    return (m.group(1), m.group(2)) if m and m.group(2) in _MOTIVE else None


def is_siegel(v):
    """Genau der Pfad eines mitgelieferten Siegels (ohne Leerzeichen, ohne ?w=) - so wird er als Symbol gespeichert."""
    return isinstance(v, str) and v == v.strip() and parse(v) is not None


def label(v):
    """Name eines Siegels in der Kontosprache ('Rabe (Weinrot)'), sonst ''."""
    p = parse(v)
    return f"{_namen()[p[1]]} ({_farben()[p[0]]})" if p else ""


def listing():
    nm = _namen()
    return {"colors": [{"id": f, "label": lb} for f, lb in _farben().items()],
            "motifs": [{"id": m, "label": nm[m]} for m in MOTIV_IDS],
            "path": "/siegel/{color}/{motif}.svg", "sizes": list(SIZES)}


def _size(w):
    w = int(w) if re.fullmatch(r"[0-9]{1,5}", w or "") else 0       # nur ASCII-Ziffern (wie covers.serve)
    return next((s for s in SIZES if w <= s), SIZES[-1]) if w > 0 else 0


def file_for(farbe, name, w=""):
    """-> (Pfad, Medientyp, px) oder None. SVG ohne w (px 0), sonst das PNG und die Stufe, in der es ausgeliefert wird."""
    m = _NAME_RE.fullmatch(name or "")
    if farbe not in FARBEN or not m or m.group(1) not in _MOTIVE:
        return None
    px = _size(w)
    if m.group(2) == "png" or px:
        p = os.path.join(DIR, farbe, m.group(1) + ".png")
        return (p, "image/png", px or SIZES[-1]) if os.path.isfile(p) else None
    p = os.path.join(DIR, farbe, m.group(1) + ".svg")
    return (p, "image/svg+xml", 0) if os.path.isfile(p) else None


_small = {}      # (Pfad, mtime, px) -> PNG-Bytes; hoechstens 3 Farben x 36 Motive x 2 Stufen


def _png(p, px, mtime):
    """PNG in px (aus der 256-px-Fassung, Lanczos) - 256 px unveraendert."""
    if px >= SIZES[-1]:
        with open(p, "rb") as fh:
            return fh.read()
    k = (p, mtime, px)
    if k not in _small:
        from PIL import Image
        with Image.open(p) as im:
            out = io.BytesIO()
            im.convert("RGBA").resize((px, px), Image.LANCZOS).save(out, "PNG", optimize=True)
        if len(_small) >= 2 * len(FARBEN) * len(MOTIV_IDS):
            _small.clear()
        _small[k] = out.getvalue()
    return _small[k]


def serve(farbe, name, request):
    f = file_for(farbe, name, request.query_params.get("w") or "")
    if not f:
        return Response(status_code=404, headers={"Cache-Control": "no-cache"})
    st = os.stat(f[0])
    etag = '"' + hashlib.sha1(f"{f[0]}|{st.st_size}|{int(st.st_mtime)}|{f[2]}".encode()).hexdigest()[:20] + '"'
    head = {"Cache-Control": CACHE, "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if f[1] == "image/svg+xml":
        head["Content-Security-Policy"] = SVG_CSP
    inm = request.headers.get("if-none-match") or ""
    if etag in [x.strip() for x in inm.split(",")] or inm.strip() == "*":
        return Response(status_code=304, headers=head)
    if f[1] == "image/png":
        return Response(_png(f[0], f[2], st.st_mtime), media_type="image/png", headers=head)
    with open(f[0], "rb") as fh:
        return Response(fh.read(), media_type=f[1], headers=head)


@router.get("/siegel/{farbe}/{name}")
def siegel_file(farbe: str, name: str, request: Request):
    return serve(farbe, name, request)


@router.get("/api/siegel")
def api_siegel(request: Request):
    if not _api_guard(request):
        return _bad("unauthorized", 401)
    return JSONResponse(listing())
