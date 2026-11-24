#!/usr/bin/env python3
"""Release-Notizen entschaerfen: @Wort ausserhalb von Code wird zu `@Wort`.

GitHub verlinkt jedes @Wort in Release-Notizen als Erwaehnung eines GitHub-Kontos (und fuehrt
es unter "Contributors" auf). In Saga-Texten meint @ aber KI-Namen oder oeffentliche Namen.
Zusaetzlich gekuerzt: GitHub lehnt Release-Texte ueber 125 000 Zeichen mit HTTP 422 ab (16.11.2026: Vorabversion
rc.650 scheiterte so). Laengere Notizen werden an einer Zeilengrenze gekuerzt, mit Hinweis auf CHANGELOG.md.
Nutzung: python3 tools/notes-entschaerfen.py < notes.md > notes.safe.md
"""
import re
import sys

_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
MAX_ZEICHEN = 100_000  # Rest des Release-Texts (Kopf, Fuss) passt sicher unter die Grenze von 125 000
HINWEIS = "\n\n… (gekürzt – die vollständige Liste steht in `CHANGELOG.md` im Repository)\n"

_AT = re.compile(r"(?<![`\w./@-])@([A-Za-z][A-Za-z0-9-]*)")


def entschaerfen(text: str) -> str:
    teile = _CODE.split(text)
    for i in range(0, len(teile), 2):  # gerade Indizes = Text ausserhalb von Code
        teile[i] = _AT.sub(lambda m: "`@" + m.group(1) + "`", teile[i])
    return "".join(teile)


def kuerzen(text: str, grenze: int = MAX_ZEICHEN) -> str:
    if len(text) <= grenze:
        return text
    # moeglichst an einer Absatz- bzw. Punktgrenze schneiden, sonst an einer Zeilengrenze
    schnitt = max(text.rfind("\n\n", 0, grenze), text.rfind("\n- ", 0, grenze))
    if schnitt < grenze // 2:
        schnitt = text.rfind("\n", 0, grenze)
    if schnitt <= 0:
        schnitt = grenze
    return text[:schnitt].rstrip() + HINWEIS


if __name__ == "__main__":
    sys.stdout.write(kuerzen(entschaerfen(sys.stdin.read())))
