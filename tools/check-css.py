#!/usr/bin/env python3
"""Prueft CSS-Dateien auf unausgeglichene Klammern. Eine verwaiste '}' laesst den Browser
still die NAECHSTE Regel verwerfen (so geschehen 2026-10: rote Prioritaetskreise fehlten)."""
import re, sys
bad = 0
for f in sys.argv[1:] or ["static/style.css"]:
    t = re.sub(r"/\*.*?\*/", "", open(f).read(), flags=re.S)
    depth, line = 0, 1
    for ch in t:
        line += ch == "\n"
        depth += (ch == "{") - (ch == "}")
        if depth < 0:
            print(f"{f}:{line}: verwaiste '}}'"); bad += 1; depth = 0
    if depth:
        print(f"{f}: {depth} offene '{{' am Dateiende"); bad += 1
    # Hinweis (kein Fehler): einfache Klassen, die auf oberster Ebene mehrfach definiert sind.
    # Oft gewollte Ergaenzungen - aber genau so kam es 2026-10 zum Konflikt .num (Kachel-Nummer,
    # position:absolute) vs. .num (Zahlenanzeige in Datenbanken).
    depth, buf, count = 0, "", {}
    for ch in t:
        if ch == "{":
            if depth == 0:
                for s in buf.split(","):
                    s = s.strip()
                    if re.fullmatch(r"\.[A-Za-z0-9_-]+", s):
                        count[s] = count.get(s, 0) + 1
            depth += 1; buf = ""
        elif ch == "}":
            depth -= 1; buf = ""
        elif depth == 0:
            buf += ch
    multi = sorted(k for k, v in count.items() if v > 1)
    if multi:
        print(f"{f}: Hinweis - mehrfach definiert (bewusst?): {', '.join(multi)}")
print("CSS ok" if not bad else f"{bad} Fehler"); sys.exit(1 if bad else 0)
