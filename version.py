"""Versionsangaben von Saga (eine Quelle: Datei VERSION im Repo bzw. im Image)."""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(_HERE, "VERSION"), encoding="utf-8") as _f:
        VERSION = _f.read().strip() or "0.0.0-dev"
except OSError:
    VERSION = "0.0.0-dev"

# Schnittstelle fuer die Apps: API wird nur bei inkompatiblen Aenderungen erhoeht.
API_LEVEL = 1
# Aeltere Apps bekommen den Hinweis "bitte aktualisieren" (alles darunter kann wichtige Funktionen nicht).
MIN_APP = "1.0.0"
REPO = os.environ.get("SAGA_UPDATE_REPO", "yggdrasil-sh/saga")


def parse(v):
    """'1.2.3' / 'v1.2.3' / '1.2.3-beta' -> (1, 2, 3); Unlesbares -> (0, 0, 0)"""
    out = []
    for part in str(v or "").lstrip("vV").split("-", 1)[0].split(".")[:3]:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple((out + [0, 0, 0])[:3])


def newer(a, b):
    """ist a neuer als b?"""
    return parse(a) > parse(b)
