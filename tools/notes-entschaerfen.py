#!/usr/bin/env python3
"""Release-Notizen entschaerfen: @Wort ausserhalb von Code wird zu `@Wort`.

GitHub verlinkt jedes @Wort in Release-Notizen als Erwaehnung eines GitHub-Kontos (und fuehrt
es unter "Contributors" auf). In Saga-Texten meint @ aber KI-Namen oder oeffentliche Namen.
Nutzung: python3 tools/notes-entschaerfen.py < notes.md > notes.safe.md
"""
import re
import sys

_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
_AT = re.compile(r"(?<![`\w./@-])@([A-Za-z][A-Za-z0-9-]*)")


def entschaerfen(text: str) -> str:
    teile = _CODE.split(text)
    for i in range(0, len(teile), 2):  # gerade Indizes = Text ausserhalb von Code
        teile[i] = _AT.sub(lambda m: "`@" + m.group(1) + "`", teile[i])
    return "".join(teile)


if __name__ == "__main__":
    sys.stdout.write(entschaerfen(sys.stdin.read()))
