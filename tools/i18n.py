#!/usr/bin/env python3
"""Uebersetzungen pflegen (saga/i18n/{de,en,es,ru,th}.json, Schluessel = deutscher Text).

  python3 tools/i18n.py check          Luecken, ueberzaehlige Schluessel, abweichende Platzhalter (Exit 1 bei Fehlern)
  python3 tools/i18n.py sync           de.json = alle benutzten Schluessel; unbenutzte Schluessel aus en/es entfernen
  python3 tools/i18n.py todo [lang]    fehlende Schluessel ausgeben (Vorlage fuer add)
  python3 tools/i18n.py add <datei>    Zeilen "deutsch ||| english ||| español" in en.json/es.json uebernehmen
  python3 tools/i18n.py merge <lang> <datei.json>   {"deutsch": "Uebersetzung"} in <lang>.json uebernehmen (ru, th …)
  python3 tools/i18n.py scan <datei>   deutsch aussehende, noch nicht uebersetzte Texte in einer Datei zeigen

Benutzte Schluessel: t("...") in Python, _("...") in templates/, T('...') in static/*.js.
"""
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAT = os.path.join(ROOT, "saga", "i18n")
LANGS = ("de", "en", "es", "ru", "th")
# Pluralformen {n:a|b|c} je Sprache (wie PLURAL_FORMS in saga/i18n)
PLURAL_N = {"de": 2, "en": 2, "es": 2, "ru": 3, "th": 1}
PL = re.compile(r"\{(\w+):([^{}]*)\}")
TAG = re.compile(r"</?(a|b|i|u|s|em|strong|code|kbd|br|span|small|p|ul|ol|li|mark|div|pre|sup|sub)\b")
STR = r"""(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)")"""
PY_CALL = re.compile(r"(?<![\w.])t\(\s*" + STR)
TPL_CALL = re.compile(r"(?<![\w.])_\(\s*" + STR)
JS_CALL = re.compile(r"\bT\(\s*" + STR)
PH = re.compile(r"\{(\w+)\}")


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _scan(paths, rx):
    keys = {}
    for p in paths:
        with open(p, encoding="utf-8") as f:
            src = f.read()
        for m in rx.finditer(src):
            k = _unescape(m.group(1) if m.group(1) is not None else m.group(2))
            keys.setdefault(k, os.path.relpath(p, ROOT) + ":" + str(src.count("\n", 0, m.start()) + 1))
    return keys


def used_keys():
    py = [os.path.join(ROOT, f) for f in ("app.py", "admin.py", "auth.py", "hkcal.py")] + sorted(glob.glob(os.path.join(ROOT, "saga", "*.py")))
    keys = _scan(py, PY_CALL)
    for k, v in _scan(sorted(glob.glob(os.path.join(ROOT, "templates", "**", "*.html"), recursive=True)), TPL_CALL).items():
        keys.setdefault(k, v)
    for k, v in _scan(sorted(glob.glob(os.path.join(ROOT, "static", "*.js"))), JS_CALL).items():
        keys.setdefault(k, v)
    return keys


def load(lang):
    with open(os.path.join(CAT, lang + ".json"), encoding="utf-8") as f:
        return json.load(f)


def save(lang, d):
    with open(os.path.join(CAT, lang + ".json"), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(d.items(), key=lambda kv: kv[0].lower())), f, ensure_ascii=False, indent=1)
        f.write("\n")


def problems():
    keys, out = used_keys(), []
    for lang in LANGS:
        cat = load(lang)
        for k in keys:
            if k not in cat or not str(cat[k]).strip():
                out.append(f"{lang}: fehlt: {k!r} ({keys[k]})")
            elif sorted(PH.findall(k)) != sorted(PH.findall(cat[k])):
                out.append(f"{lang}: Platzhalter passen nicht: {k!r} -> {cat[k]!r}")
            elif sorted(TAG.findall(k)) != sorted(TAG.findall(cat[k])):
                out.append(f"{lang}: HTML-Tags passen nicht: {k!r} -> {cat[k]!r}")
            else:
                for name, forms in PL.findall(cat[k]):
                    if name not in PH.findall(k) or len(forms.split("|")) != PLURAL_N[lang]:
                        out.append(f"{lang}: Pluralformen falsch ({PLURAL_N[lang]} Formen fuer {{{name}}}): {k!r} -> {cat[k]!r}")
        for k in cat:
            if k not in keys:
                out.append(f"{lang}: unbenutzt: {k!r}")
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "check":
        p = problems()
        print("\n".join(p) if p else f"OK: {len(used_keys())} Schluessel, alle Sprachen vollstaendig")
        sys.exit(1 if p else 0)
    if cmd == "sync":
        keys = used_keys()
        save("de", {k: k for k in keys})
        for lang in LANGS[1:]:
            cat = load(lang)
            save(lang, {k: v for k, v in cat.items() if k in keys})
        print(f"{len(keys)} Schluessel")
    elif cmd == "todo":
        lang = sys.argv[2] if len(sys.argv) > 2 else "en"
        cat = load(lang)
        for k, where in used_keys().items():
            if k not in cat:
                print(f"{k} |||  ||| ")
    elif cmd == "add":
        cats = {lang: load(lang) for lang in LANGS[1:]}
        n = 0
        with open(sys.argv[2], encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.strip() or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split("|||")]
                if len(parts) != 3 or not all(parts):
                    print("Zeile uebersprungen:", line)
                    continue
                de, en, es = (_unescape(x) for x in parts)
                cats["en"][de], cats["es"][de] = en, es
                n += 1
        for lang, cat in cats.items():
            save(lang, cat)
        print(f"{n} Eintraege uebernommen")
    elif cmd == "merge":
        lang = sys.argv[2]
        cat = load(lang)
        with open(sys.argv[3], encoding="utf-8") as f:
            new = json.load(f)
        keys = used_keys()
        bad = [k for k in new if k not in keys]
        for k in bad:
            print("unbekannter Schluessel:", repr(k))
        cat.update({k: v for k, v in new.items() if k in keys and str(v).strip()})
        order = list(load("de"))                       # Reihenfolge wie de.json (kleine Diffs)
        with open(os.path.join(CAT, lang + ".json"), "w", encoding="utf-8") as f:
            json.dump({k: cat[k] for k in order if k in cat}, f, ensure_ascii=False, indent=1)
            f.write("\n")
        print(f"{len(new) - len(bad)} Eintraege in {lang}.json, {sum(k in cat for k in keys)}/{len(keys)} uebersetzt")
    elif cmd == "scan":
        rx = re.compile(r"""(['"])((?:\\.|(?!\1).)*?[A-Za-zÄÖÜäöüß]{2,}(?:\\.|(?!\1).)*?)\1""")
        german = re.compile(r"[äöüÄÖÜß]|\b(?:der|die|das|und|oder|nicht|kein|keine|mit|von|zu|ein|eine|ist|für|auf|bei|noch|neu|neue|alle|"
                            r"Seite|Aufgabe|Termin|Datei|Text|Name|Bitte|Fehler|Speichern|Abbrechen|Löschen|Eintrag|Tabelle|Spalte)\b|^[A-ZÄÖÜ][a-zäöüß]+$")
        with open(sys.argv[2], encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                s = line.strip()
                if s.startswith(("//", "#")):
                    continue
                for m in rx.finditer(line):
                    txt = m.group(2)
                    pre = line[max(0, m.start() - 3):m.start()]
                    if pre.endswith(("T(", "t(", "_(")) or not german.search(txt):
                        continue
                    print(f"{i}: {txt}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
