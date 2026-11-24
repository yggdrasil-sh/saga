"""Gemeinsame KI-Darstellung (Bugs-DB 2248, Konzept 2.6 K20): eine feste Markdown-Teilmenge fuer KI-Antworten.

Kleine und grosse Modelle schreiben Markdown (`**fett**`, `# Ueberschrift`, `* Punkt`), obwohl der System-Prompt Klartext
verlangt. Damit es ueberall gleich aussieht, gilt fuer KI-Antworten genau diese Teilmenge (docs/APP-API-KI.md, Abschnitt
„KI-Darstellung“; Vektoren docs/ki-markdown-vektoren.json):

- Absaetze (Leerzeile), Zeilenumbrueche bleiben
- Listen: Zeilen mit "- ", "* ", "+ ", "• " (Punkt) oder "1. ", "1) " (Nummer)
- **fett** / __fett__, `code`; "# Ueberschrift" (1-6 #) = fette Zeile
- Quellen [n] / [1, 2]
- alles andere (Links, Bilder, HTML, Tabellen, *kursiv*) ist Text

Im **Chat** schreibt der Server die Antwort vor dem Beleg in die Chat-Formatierung chatfmt1 um (to_chat): dann zeigen
alle Clients sie richtig, auch aeltere, ohne eigenen Renderer - `*fett*`, `_kursiv_`, `~durch~`, `` `code` ``, ```Block```,
"- " Liste, "> " Zitat. Im Assistenten (Frage, Seiten-Hilfen) zeigen Web und Apps die Teilmenge selbst (pieces/lines).
"""
import re

_FENCE = re.compile(r"^\s*```")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[*+•]\s+(?=\S)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)([^\n]*?\S)\1")
_ITAL = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])")
_STRIKE = re.compile(r"~~(?=\S)([^~\n]*?\S)~~")
_CODE = re.compile(r"`[^`\n]+`")
_B = "\x01"                                                   # Platzhalter fuer fett (kommt in Modelltext nicht vor)


def _inline_chat(s):
    """Eine Zeile ausserhalb von Codebloecken: Markdown-Inline -> chatfmt1; `code` bleibt unberuehrt."""
    out, pos = [], 0
    for m in _CODE.finditer(s):
        out.append(_conv(s[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_conv(s[pos:]))
    return "".join(out)


def _conv(s):
    s = s.replace(_B, "")
    s = _BOLD.sub(lambda m: _B + m.group(2) + _B, s)
    s = _ITAL.sub(lambda m: "_" + m.group(1) + "_", s)
    s = _STRIKE.sub(lambda m: "~" + m.group(1) + "~", s)
    return s.replace(_B, "*")


def to_chat(text):
    """KI-Antwort (Markdown-Teilmenge) -> Chat-Formatierung chatfmt1. Codebloecke bleiben, Ueberschrift -> *fett*,
    "* "/"+ "/"• " -> "- ", **fett** -> *fett*, *kursiv* -> _kursiv_, ~~durch~~ -> ~durch~."""
    lines, code = [], False
    for ln in str(text or "").split("\n"):
        if _FENCE.match(ln):
            code = not code
            lines.append(ln.strip())
            continue
        if code:
            lines.append(ln)
            continue
        m = _HEAD.match(ln)
        if m:
            h = _BOLD.sub(lambda x: x.group(2), m.group(1)).strip("*_ ")
            lines.append("*" + h + "*" if h else "")
            continue
        ln = _BULLET.sub(lambda x: x.group(1) + "- ", ln)
        lines.append(_inline_chat(ln))
    return "\n".join(lines)


# --- Assistent: Zeilen und Stuecke (wie static/ai.js answerNode; Vektoren docs/ki-markdown-vektoren.json) -----------------
_LI = re.compile(r"^\s*(?:[-*•]|(\d{1,2})[.)])\s+(.*)$")
_H = re.compile(r"^\s*#{1,6}\s+(.*)$")
_INL = re.compile(r"(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[\d{1,2}(?:\s*[,;]\s*\d{1,2})*\])")


def spans(s, cites=()):
    """Eine Zeile -> [[art, text]]: "t" Text, "b" fett, "c" Code, "n" Quellenverweis (nur Nummern aus cites, sonst Text)."""
    out, pos = [], 0
    for m in _INL.finditer(s):
        if m.start() > pos:
            out.append(["t", s[pos:m.start()]])
        x = m.group(0)
        if x[0] == "[":
            for k, n in enumerate(re.split(r"\s*[,;]\s*", x[1:-1])):
                if k:
                    out.append(["t", " "])
                out.append(["n", int(n)] if int(n) in cites else ["t", f"[{n}]"])
        elif x[0] == "`":
            out.append(["c", x[1:-1]])
        else:
            out.append(["b", x[2:-2]])
        pos = m.end()
    if pos < len(s):
        out.append(["t", s[pos:]])
    merged = []                                              # benachbarte Textstuecke zusammenfassen
    for sp in out:
        if merged and sp[0] == "t" and merged[-1][0] == "t":
            merged[-1][1] += sp[1]
        else:
            merged.append(sp)
    return merged


def lines(text, cites=()):
    """Antwort -> [{"k": "p"|"h"|"ul"|"ol", "s": spans}] - leere Zeilen fallen weg, Listen je Zeile (ul/ol)."""
    out = []
    for ln in str(text or "").split("\n"):
        m = _LI.match(ln)
        if m:
            out.append({"k": "ol" if m.group(1) else "ul", "s": spans(m.group(2), cites)})
            continue
        if not ln.strip():
            continue
        h = _H.match(ln)
        out.append({"k": "h" if h else "p", "s": spans(h.group(1) if h else ln, cites)})
    return out
