#!/usr/bin/env python3
"""Vorlagengalerie pruefen (Format: docs/GALERIE.md). Ohne Datenbank und Konfiguration lauffaehig.

  python3 tools/gallery_check.py                 gallery/ pruefen
  python3 tools/gallery_check.py <ordner>        anderen Ordner pruefen (z. B. tests/gallery_fixtures)
  python3 tools/gallery_check.py --todo <lang>   fehlende Uebersetzungen als JSON {"deutsch": ""} ausgeben
  Optionen: --no-i18n (fehlende Uebersetzungen nicht als Fehler werten), -q (nur Fehler)

Prueft: JSON, Pflichtfelder, refs (unbekannt, falsche Art, Beispiele), Block-/Eigenschafts-/Ansichts-/Widget-Typen,
Grenzen (30 items, 200 Zeilen, 200 000 Zeichen), Uebersetzungen in gallery/i18n/{en,es,ru,th}.json (fehlend,
Platzhalter, $ref) und ueberzaehlige Uebersetzungs-Schluessel. Rueckgabe 0 = alles in Ordnung, 1 = Fehler."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from saga import gallery_format as gf  # noqa: E402


def main(argv):
    args = [a for a in argv if not a.startswith("-")]
    flags = {a for a in argv if a.startswith("-")}
    todo = None
    if "--todo" in flags:
        if not args or args[-1] not in gf.LANGS:
            print("--todo <lang> (en, es, ru, th)", file=sys.stderr)
            return 2
        todo = args.pop()
    path = args[0] if args else os.path.join(ROOT, "gallery")
    if not os.path.isdir(path):
        print(f"Ordner fehlt: {path}", file=sys.stderr)
        return 1
    bundles, tables, problems = gf.load_dir(path)
    if todo:
        tb = tables.get(todo) or {}
        out = {}
        for b in bundles.values():
            for s in gf.texts(b):
                if not tb.get(s):
                    out.setdefault(s, "")
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    fail = bool(problems)
    for fn, errs in sorted(problems.items()):
        print(f"FEHLER {fn}:")
        for e in errs:
            print(f"  - {e}")
    used = set()
    for bid, b in sorted(bundles.items(), key=lambda kv: (kv[1].get("order", 0), kv[0])):
        src = gf.texts(b)
        used.update(src)
        miss, bad = gf.missing_translations(b, tables)
        size = len(json.dumps(b, ensure_ascii=False))
        rows = sum(len(it.get("rows") or []) for it in b["items"])
        status = "OK"
        if bad or (miss and "--no-i18n" not in flags):
            status = "FEHLER"
            fail = True
        if status != "OK" or "-q" not in flags:
            print(f"{status:6} {bid}: {len(b['items'])} items, {rows} Zeilen, {size} Zeichen, {len(src)} Texte")
        for lang, m in sorted(miss.items()):
            print(f"  - {lang}: {len(m)} Uebersetzungen fehlen, z. B. {m[0][:60]!r}")
        for lang, lst in sorted(bad.items()):
            for s, why in lst[:10]:
                print(f"  - {lang}: {why}: {s[:60]!r}")
    for lang in gf.LANGS:
        extra = sorted(set(tables.get(lang) or {}) - used)
        if extra and bundles and not problems:
            print(f"Hinweis {lang}: {len(extra)} unbenutzte Uebersetzungen, z. B. {extra[0][:60]!r}")
    if not bundles and not problems:
        print(f"keine Vorlagen in {path}")
    print("GALERIE " + ("FEHLER" if fail else "OK"))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
