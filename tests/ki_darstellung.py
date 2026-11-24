"""Gemeinsame KI-Darstellung (Bugs-DB 2248) - ohne Server: saga/ai_md.py gegen docs/ki-markdown-vektoren.json (die Apps
spielen dieselben Vektoren nach), Chat-Umschreibung in chatfmt1 und die Reihenfolge im Chat (erst Ausgabefilter, dann
Formatierung, dann Quellen und Beleg).

Aufruf: python tests/ki_darstellung.py   (nur Standardbibliothek + saga/ai_md.py)
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from saga import ai_md as M  # noqa: E402

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:300], flush=True)


def main():
    vec = json.load(open(os.path.join(ROOT, "docs", "ki-markdown-vektoren.json"), encoding="utf-8"))
    gen = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ki_md_vectors.py")], capture_output=True, text=True,
                         check=True).stdout
    ok("Vektoren aktuell (tools/ki_md_vectors.py erzeugt dieselbe Datei)", json.loads(gen) == vec)
    cites = vec["cites"]
    for v in vec["assistant"]:
        ok(f"Assistent: {v['in'][:40]!r}", M.lines(v["in"], cites) == v["lines"], M.lines(v["in"], cites))
    for v in vec["chat"]:
        ok(f"Chat: {v['in'][:40]!r}", M.to_chat(v["in"]) == v["out"], M.to_chat(v["in"]))
    ok("Chat: **fett** -> *fett*", M.to_chat("**Getränke**: Wasser") == "*Getränke*: Wasser")
    ok("Chat: Ueberschrift -> fette Zeile, Sternliste -> Strichliste",
       M.to_chat("## Plan\n* Kohle\n* Senf") == "*Plan*\n- Kohle\n- Senf")
    ok("Chat: Codebloecke bleiben", M.to_chat("```\n**x**\n```") == "```\n**x**\n```")
    ok("Chat: 2*3*4 und a_b_c bleiben", M.to_chat("2*3*4 a_b_c") == "2*3*4 a_b_c")
    ok("Assistent: Quellen nur fuer bekannte Nummern", M.spans("[1] [9]", [1]) == [["n", 1], ["t", " [9]"]])
    ok("Assistent: Link bleibt Text", M.lines("[x](https://e.org)") == [{"k": "p", "s": [["t", "[x](https://e.org)"]]}])
    src = open(os.path.join(ROOT, "saga", "ai_chat.py"), encoding="utf-8").read()
    i_links, i_md, i_src = src.find("ai_frame.no_links(answer"), src.find("ai_md.to_chat(answer)"), src.find("answer = with_sources(answer")
    ok("Chat: erst Ausgabefilter, dann Formatierung, dann Quellen (Beleg ueber das Ergebnis)", 0 < i_links < i_md < i_src)
    print(f"\n{sum(res)}/{len(res)} OK", flush=True)
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
