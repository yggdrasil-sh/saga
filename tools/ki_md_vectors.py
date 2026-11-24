#!/usr/bin/env python3
"""Testvektoren der gemeinsamen KI-Darstellung (Bugs-DB 2248) aus saga/ai_md.py -> docs/ki-markdown-vektoren.json.
Die Apps spielen sie nach (Assistent: lines/spans mit Quellen 1-3; Chat: to_chat). Nur Standardbibliothek.
  python3 tools/ki_md_vectors.py > docs/ki-markdown-vektoren.json"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ai_md", os.path.join(HERE, "..", "saga", "ai_md.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

CITES = [1, 2, 3]
INPUTS = [
    "Hallo", "", "**Getränke**: Wasser, Saft", "__auch fett__", "`code` und **fett** [1]", "Siehe [1, 2] und [4]",
    "[2][3]", "## Plan für Samstag\n- Kohle\n- Würstchen", "# **Titel**", "1. Erst\n2) Dann\n10. Zuletzt",
    "* Stern-Liste\n• Punkt-Liste", "Absatz eins\n\nAbsatz zwei", "*kursiv bleibt Text*", "2*3*4 = 24", "a_b_c",
    "**offen", "`a**b**`", "[Link](https://example.org)", "<b>kein HTML</b>", "  - eingerückt", "-ohne Leerzeichen",
    "### H3 mit `code`", "Zeile mit **fett** und **noch mehr** [3]", "Tabelle | a | b |", "> Zitat",
    "```\n**nicht** ändern\n```\n**doch**", "Das ist *wichtig* und ~~alt~~", "+ Plus-Liste", "- **Ben**: bringt *Kohle* mit [1]",
]


def main():
    out = {"format": 1, "cites": CITES,
           "note": "Erzeugt von tools/ki_md_vectors.py aus saga/ai_md.py - nicht von Hand aendern.",
           "assistant": [{"in": s, "lines": M.lines(s, CITES)} for s in INPUTS],
           "chat": [{"in": s, "out": M.to_chat(s)} for s in INPUTS]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
