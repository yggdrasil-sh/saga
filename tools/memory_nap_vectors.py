"""Erzeugt tests/vectors/memory_nap.json (Bugs-DB 2601, docs/MEMORY.md 11) - Referenz direkt aus dem Vertrag, unabhaengig
von saga/ (wie tools/memory_score_vectors.py). Aufruf: python3 tools/memory_nap_vectors.py > tests/vectors/memory_nap.json

Inhalt: Schnappschuss-Text de/en exakt (alle Zeilen, ohne Ort, ohne Faeden, ohne Zahlen, Zahlenformat, Vorzeichen),
Leerraum-Regeln, Grenzfaelle (400 mit Feldname), Titel in Konto-Zeitzone, Zeitangaben ("vor 2 Tagen, 5 Stunden") und
Aufwach-Satz de/en."""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

LIM = {"stimmung": 40, "gedanke": 240, "ort": 100, "faden": 130, "faeden": 5, "zeichen": 1200, "zeilen": 10}
L = {"de": {"stimmung": "Stimmung: {s}", "valenz": "Valenz {v}", "erregung": "Erregung {v}", "ort": "Ort: {s}",
            "faeden": "Offene Fäden:", "gedanke": "Letzter Gedanke: {s}"},
     "en": {"stimmung": "Mood: {s}", "valenz": "valence {v}", "erregung": "arousal {v}", "ort": "Place: {s}",
            "faeden": "Open threads:", "gedanke": "Last thought: {s}"}}
COMMA = {"de", "es", "ru"}


def norm(s):
    return " ".join(str(s).split())


def num(v, lang, sign=False):
    r = round(float(v), 1)
    s = f"{abs(r):.1f}"
    if lang in COMMA:
        s = s.replace(".", ",")
    if r == 0:
        return s
    if r < 0:
        return "-" + s
    return ("+" if sign else "") + s


def text(a, lang):
    """-> (text | None, fehlerfeld | None)."""
    st = norm(a.get("stimmung") or "") if isinstance(a.get("stimmung"), (str, type(None))) else None
    ge = norm(a.get("gedanke") or "") if isinstance(a.get("gedanke"), (str, type(None))) else None
    if not st or len(st) > LIM["stimmung"]:
        return None, "stimmung"
    if not ge or len(ge) > LIM["gedanke"]:
        return None, "gedanke"
    ort = a.get("ort")
    if ort is not None and not isinstance(ort, str):
        return None, "ort"
    ort = norm(ort or "")
    if len(ort) > LIM["ort"]:
        return None, "ort"
    fd = a.get("faeden")
    if fd is None:
        fd = []
    if not isinstance(fd, list) or not all(isinstance(x, str) for x in fd):
        return None, "faeden"
    fd = [norm(x) for x in fd if norm(x)]
    if len(fd) > LIM["faeden"] or any(len(x) > LIM["faden"] for x in fd):
        return None, "faeden"
    nums = []
    for k, lo, hi in (("valenz", -1, 1), ("erregung", 0, 1)):
        v = a.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            return None, k
        nums.append(L[lang][k].format(v=num(v, lang, sign=k == "valenz")))
    lines = [L[lang]["stimmung"].format(s=st) + (" (" + " · ".join(nums) + ")" if nums else "")]
    if ort:
        lines.append(L[lang]["ort"].format(s=ort))
    if fd:
        lines.append(L[lang]["faeden"])
        lines += ["- " + x for x in fd]
    lines.append(L[lang]["gedanke"].format(s=ge))
    out = "\n".join(lines)
    if len(out) > LIM["zeichen"] or len(lines) > LIM["zeilen"]:
        return None, "text"
    return out, None


def plural(lang, n, one, many):
    return one if n == 1 else many


def wann(sec, lang):
    s = max(0, int(sec))
    de = lang == "de"
    if s < 60:
        return "gerade eben" if de else "just now"
    if s < 3600:
        m = s // 60
        return f"vor {m} {plural(lang, m, 'Minute', 'Minuten')}" if de else f"{m} {plural(lang, m, 'minute', 'minutes')} ago"
    if s < 86400:
        h, m = s // 3600, (s % 3600) // 60
        if m:
            return (f"vor {h} {plural(lang, h, 'Stunde', 'Stunden')}, {m} {plural(lang, m, 'Minute', 'Minuten')}" if de else
                    f"{h} {plural(lang, h, 'hour', 'hours')}, {m} {plural(lang, m, 'minute', 'minutes')} ago")
        return f"vor {h} {plural(lang, h, 'Stunde', 'Stunden')}" if de else f"{h} {plural(lang, h, 'hour', 'hours')} ago"
    d, h = s // 86400, (s % 86400) // 3600
    if s < 7 * 86400 and h:
        return (f"vor {d} {plural(lang, d, 'Tag', 'Tagen')}, {h} {plural(lang, h, 'Stunde', 'Stunden')}" if de else
                f"{d} {plural(lang, d, 'day', 'days')}, {h} {plural(lang, h, 'hour', 'hours')} ago")
    return f"vor {d} {plural(lang, d, 'Tag', 'Tagen')}" if de else f"{d} {plural(lang, d, 'day', 'days')} ago"


def satz(sec, n, lang):
    """Aufwach-Satz: sec None = kein Schnappschuss; n None = geaenderte Seiten unbekannt (Fehler)."""
    de = lang == "de"
    if sec is None:
        return "Noch kein Nickerchen." if de else "No nap yet."
    s = ("Letztes Nickerchen: " if de else "Last nap: ") + wann(sec, lang) + "."
    if n is not None:
        s += (f" Seitdem {n} {plural(lang, n, 'Seite', 'Seiten')} geändert." if de else
              f" {n} {plural(lang, n, 'page', 'pages')} changed since.")
    return s


FULL = {"stimmung": "neugierig", "gedanke": "Morgen zuerst die Vektoren.", "valenz": 0.4, "erregung": 0.6,
        "faeden": ["Migration 43 prüfen", "Gold neu erzeugen"], "ort": "Saga – Gedächtnis Stufe 3"}
TEXTS = [
    ("alle Zeilen", FULL),
    ("ohne Ort", {k: v for k, v in FULL.items() if k != "ort"}),
    ("ohne Faeden", {k: v for k, v in FULL.items() if k != "faeden"}),
    ("ohne Zahlen", {"stimmung": "ruhig", "gedanke": "Gute Nacht."}),
    ("nur Valenz negativ", {"stimmung": "müde", "gedanke": "Zu lang gearbeitet.", "valenz": -0.3}),
    ("nur Erregung", {"stimmung": "wach", "gedanke": "Weiter.", "erregung": 1}),
    ("Valenz 0 ohne Vorzeichen, -0,04 -> 0,0", {"stimmung": "neutral", "gedanke": "Mal sehen.", "valenz": -0.04, "erregung": 0}),
    ("Leerraum zusammengefasst, Zeilenumbrueche -> Leerzeichen, leere Faeden weg",
     {"stimmung": "  sehr\nfroh ", "gedanke": "Erst\n\ndas,\tdann das.", "faeden": ["  a  b ", "", "   ", "c"], "ort": " \n "}),
    ("5 Faeden, Grenzen genau", {"stimmung": "x" * 40, "gedanke": "g" * 240, "ort": "o" * 100, "faeden": ["f" * 130] * 5,
                                 "valenz": 1, "erregung": 1}),
]
BAD = [
    ("stimmung fehlt", {"gedanke": "x"}),
    ("stimmung leer (nur Leerraum)", {"stimmung": " \n ", "gedanke": "x"}),
    ("stimmung 41", {"stimmung": "x" * 41, "gedanke": "x"}),
    ("stimmung keine Zeichenkette", {"stimmung": 5, "gedanke": "x"}),
    ("gedanke fehlt", {"stimmung": "x"}),
    ("gedanke 241", {"stimmung": "x", "gedanke": "g" * 241}),
    ("ort 101", {"stimmung": "x", "gedanke": "x", "ort": "o" * 101}),
    ("ort keine Zeichenkette", {"stimmung": "x", "gedanke": "x", "ort": ["a"]}),
    ("6 Faeden", {"stimmung": "x", "gedanke": "x", "faeden": ["a", "b", "c", "d", "e", "f"]}),
    ("Faden 131", {"stimmung": "x", "gedanke": "x", "faeden": ["f" * 131]}),
    ("faeden keine Liste", {"stimmung": "x", "gedanke": "x", "faeden": "a, b"}),
    ("faeden mit Zahl", {"stimmung": "x", "gedanke": "x", "faeden": ["a", 3]}),
    ("valenz 1,5", {"stimmung": "x", "gedanke": "x", "valenz": 1.5}),
    ("valenz Text", {"stimmung": "x", "gedanke": "x", "valenz": "gut"}),
    ("valenz true", {"stimmung": "x", "gedanke": "x", "valenz": True}),
    ("erregung -0,1", {"stimmung": "x", "gedanke": "x", "erregung": -0.1}),
]
D, H, M = 86400, 3600, 60
WANN = [("30 s", 30), ("61 s", 61), ("59 min", 59 * M), ("1 h", H), ("1 h 1 min", H + M), ("5 h 7 min", 5 * H + 7 * M),
        ("23 h 59 min 59 s", D - 1), ("1 d", D), ("2 d 5 h", 2 * D + 5 * H), ("6 d 23 h 59 min", 7 * D - 60),
        ("7 d 3 h", 7 * D + 3 * H), ("40 d", 40 * D), ("negativ -> gerade eben", -5)]


def main():
    out = {"note": "Nickerchen/Aufwachen (Bugs-DB 2601, docs/MEMORY.md 11) - Referenz unabhaengig von saga/", "grenzen": LIM,
           "texte": [], "fehler": [], "wann": [], "satz": [], "titel": []}
    for name, a in TEXTS:
        for lang in ("de", "en"):
            txt, err = text(a, lang)
            assert err is None, (name, err)
            out["texte"].append({"name": name, "lang": lang, "in": a, "text": txt, "zeilen": txt.count("\n") + 1,
                                 "zeichen": len(txt)})
    for name, a in BAD:
        txt, err = text(a, "de")
        assert txt is None, name
        out["fehler"].append({"name": name, "in": a, "feld": err})
    for name, s in WANN:
        out["wann"].append({"name": name, "sekunden": s, "de": wann(s, "de"), "en": wann(s, "en")})
    for name, s, n in (("mit geaenderten Seiten", 2 * D + 5 * H, 7), ("eine Seite", 3 * M, 1), ("keine Seite", 30, 0),
                       ("Seiten unbekannt (Fehler)", 5 * H, None), ("kein Schnappschuss", None, None)):
        out["satz"].append({"name": name, "sekunden": s, "n": n, "de": satz(s, n, "de"), "en": satz(s, n, "en")})
    for zone, iso in (("Europe/Berlin", "2026-11-28T08:12:30+00:00"), ("America/New_York", "2026-11-28T03:05:00+00:00"),
                      ("UTC", "2026-12-31T23:59:59+00:00")):
        ts_ = datetime.fromisoformat(iso).timestamp()
        loc = datetime.fromtimestamp(ts_, ZoneInfo(zone))
        out["titel"].append({"zone": zone, "ts": ts_, "option": "Schnappschuss", "titel": "Schnappschuss " + loc.strftime("%Y-%m-%d %H:%M")})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
