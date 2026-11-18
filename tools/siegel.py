#!/usr/bin/env python3
"""Saga-Siegel (2.5, Bugs-DB 2156) pflegen: SVG-Quellen uebernehmen und PNG-Fassungen fuer die Apps erzeugen.

  python3 tools/siegel.py import <quelle>   <quelle>/<farbe>/<motiv>.svg -> static/siegel/<farbe>/<motiv>.svg
                                            (Positivliste pruefen, ids je Datei eindeutig machen - sonst unveraendert)
  python3 tools/siegel.py png               static/siegel/<farbe>/<motiv>.png (256 px) mit Playwright-Chromium rendern
                                            (tools/siegel-png.js), verlustfrei verdichten (Pillow); 64/128 px verkleinert
                                            der Server daraus (saga/siegel.py)
  python3 tools/siegel.py check             alle Dateien da und sauber (Exit 1 bei Fehlern)

Farben und Motive: saga/siegel.py (FARBEN, MOTIVE).
"""
import glob
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(ROOT, "static", "siegel")
PX = 256                     # gespeicherte PNG-Groesse

# Positivliste: nur Formen und Verlaeufe - keine Skripte, Stile, Verweise, Bilder, Schriften
TAGS = {"svg", "defs", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "linearGradient", "radialGradient", "stop"}
ATTRS = {"xmlns", "viewBox", "width", "height", "id", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "fx", "fy",
         "points", "transform", "fill", "fill-opacity", "fill-rule", "opacity", "stroke", "stroke-width", "stroke-linecap",
         "stroke-linejoin", "stroke-opacity", "stroke-dasharray", "stroke-miterlimit", "offset", "stop-color", "stop-opacity",
         "gradientUnits", "gradientTransform", "spreadMethod"}
URL = re.compile(r"url\(#([A-Za-z0-9_-]+)\)")


def _motive():
    """FARBEN, MOTIV_IDS aus saga/siegel.py (ohne Import - der braucht die Server-Konfiguration)."""
    import ast
    with open(os.path.join(ROOT, "saga", "siegel.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    v = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
         if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id in ("FARBEN", "MOTIV_IDS")}
    return v["FARBEN"], v["MOTIV_IDS"]


def problems(svg, prefix=None):
    """Fehlerliste einer SVG-Datei (leer = sauber). prefix: ids muessen damit beginnen."""
    out = []
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as e:
        return [f"kein gueltiges XML: {e}"]
    ids = set()
    for e in root.iter():
        tag = e.tag.split("}", 1)[-1]
        if tag not in TAGS:
            out.append(f"Element {tag}")
        for k, v in e.attrib.items():
            if k.split("}", 1)[-1] not in ATTRS:
                out.append(f"Attribut {k}")
            if k == "id":
                ids.add(v)
                if prefix and not v.startswith(prefix):
                    out.append(f"id {v} ohne Praefix {prefix}")
            refs = URL.findall(v)
            if "url(" in v and (not refs or len(refs) != v.count("url(")):
                out.append(f"Verweis {v}")
            out += [f"unbekannter Verweis #{r}" for r in refs if r not in ids and not re.search(rf'id="{re.escape(r)}"', svg)]
    if root.get("viewBox") != "0 0 64 64":
        out.append("viewBox nicht 0 0 64 64")
    if re.search(r"<!|<\?(?!xml)|javascript:|data:|https?:", svg.replace('xmlns="http://www.w3.org/2000/svg"', "", 1)):
        out.append("Doctype/Entity/Verweis nach aussen")
    return out


def cmd_import(src):
    farben, motive = _motive()
    n = 0
    for f in farben:
        os.makedirs(os.path.join(DST, f), exist_ok=True)
        for m in motive:
            with open(os.path.join(src, f, m + ".svg"), encoding="utf-8") as fh:
                s = fh.read().strip()
            bad = problems(s)
            if bad:
                sys.exit(f"{f}/{m}: {', '.join(bad)}")
            pre = f"s{f[0]}-{m}-"
            ids = re.findall(r'\bid="([A-Za-z0-9_-]+)"', s)
            for i in sorted(set(ids), key=len, reverse=True):
                s = re.sub(rf'\bid="{re.escape(i)}"', f'id="{pre}{i}"', s)
                s = s.replace(f"url(#{i})", f"url(#{pre}{i})")
            bad = problems(s, pre)
            if bad:
                sys.exit(f"{f}/{m} nach dem Umbenennen: {', '.join(bad)}")
            with open(os.path.join(DST, f, m + ".svg"), "w", encoding="utf-8") as fh:
                fh.write(s + "\n")
            n += 1
    print(f"{n} Siegel uebernommen")


def cmd_png():
    from PIL import Image
    farben, motive = _motive()
    jobs = [(os.path.join(DST, f, m + ".svg"), os.path.join(DST, f, m + ".png"), PX) for f in farben for m in motive]
    lst = os.path.join(os.environ.get("TMPDIR", "/tmp"), "siegel-png-jobs.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{a}\t{b}\t{px}" for a, b, px in jobs))
    subprocess.run(["node", os.path.join(ROOT, "tools", "siegel-png.js"), lst], check=True)
    for _, p, px in jobs:
        with Image.open(p) as im:
            im.load()
            if im.size != (px, px) or im.mode != "RGBA":
                sys.exit(f"{p}: {im.size} {im.mode}")
            im.save(p, "PNG", optimize=True)
    print(f"{len(jobs)} PNG erzeugt")


def cmd_check():
    farben, motive = _motive()
    bad = 0
    for f in farben:
        for m in motive:
            p = os.path.join(DST, f, m + ".svg")
            if not os.path.isfile(p):
                print("fehlt:", p); bad += 1; continue
            with open(p, encoding="utf-8") as fh:
                for x in problems(fh.read(), f"s{f[0]}-{m}-"):
                    print(f"{f}/{m}: {x}"); bad += 1
            if not os.path.isfile(os.path.join(DST, f, m + ".png")):
                print("fehlt:", f"{f}/{m}.png"); bad += 1
    want = {os.path.join(DST, f, m + x) for f in farben for m in motive for x in (".svg", ".png")}
    extra = [p for p in glob.glob(os.path.join(DST, "*", "*")) if p not in want]
    for p in extra:
        print("ueberzaehlig:", p); bad += 1
    print("ok" if not bad else f"{bad} Fehler")
    return 1 if bad else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["import"] and len(a) == 2:
        cmd_import(a[1])
    elif a == ["png"]:
        cmd_png()
    elif a == ["check"]:
        sys.exit(cmd_check())
    else:
        sys.exit(__doc__)
