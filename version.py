"""Versionsangaben von Saga (eine Quelle: Datei VERSION im Repo bzw. im Image).

Release-Builds (main): VERSION = "2.2.0". Testing-Builds (Release Candidates, tools/rc-version.sh):
VERSION = "2.2.0-rc.7+1cc210f" (7 Commits seit dem letzten Release-Tag, Build-Kennung = Kurz-SHA) bzw.
"2.2.0-rc+1cc210f", wenn der Build keine Tags kannte. Nach SemVer gilt 2.1.0 < 2.2.0-rc.7 < 2.2.0.
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    with open(os.path.join(_HERE, "VERSION"), encoding="utf-8") as _f:
        _RAW = _f.read().strip() or "0.0.0-dev"
except OSError:
    _RAW = "0.0.0-dev"

# Build-Kennung (nach "+") abtrennen: VERSION bleibt eine schlichte SemVer-Angabe (fuer Apps, Foederation, User-Agent)
VERSION, _, BUILD = _RAW.partition("+")
VERSION = VERSION.strip() or "0.0.0-dev"
BUILD = re.sub(r"[^0-9A-Za-z.-]", "", BUILD)[:40]
# Zur Anzeige (Admin, run.py version): "2.2.0-rc.7 (1cc210f)" bzw. "2.2.0"
VERSION_LABEL = f"{VERSION} ({BUILD})" if BUILD else VERSION

# Schnittstelle fuer die Apps: API wird nur bei inkompatiblen Aenderungen erhoeht.
API_LEVEL = 1
# Aeltere Apps bekommen den Hinweis "bitte aktualisieren" (alles darunter kann wichtige Funktionen nicht).
MIN_APP = "1.0.0"
# Funktionen dieses Servers fuer die Apps (/api/version "features"): Editoren/Anzeigen nur zeigen, wenn gemeldet.
# formula1 = Formel-Eigenschaften (Werte fertig in props, rtype im Schema, fx_err je Zeile, POST .../formula/check),
# rollup1 = Rollup-Eigenschaften (wie Formeln), dbgroup1 = Gruppieren nach Formel/Rollup (groups mit value),
# person1 = Personen-Eigenschaft (Werte als Handle/Prinzipal + people in /rows, GET .../people, Filter "me", db.assigned),
# file1 = Datei-Eigenschaft (Werte [{url, name, size, mime, image}], Hochladen ueber POST /api/files?note=<zeile>).
FEATURES = ("formula1", "rollup1", "dbgroup1", "person1", "file1",
            "relfilter1", "datetime1", "status1", "subtasks1", "tableopts1",      # 2.5 Paket 5 (docs/APP-API.md)
            "weather1", "habit1", "upcoming1", "chart1",                          # 2.5 Paket 4: Widgets Etappe 2, Diagramme
            "timeline1", "api1", "webhook1", "mcp1")                             # 2.5 Paket 1: Zeitleiste, /api/v1, Webhooks, MCP
REPO = os.environ.get("SAGA_UPDATE_REPO", "yggdrasil-sh/saga")


def parse(v):
    """'1.2.3' / 'v1.2.3' / '1.2.3-rc.7+abc' -> (1, 2, 3); Unlesbares -> (0, 0, 0)"""
    out = []
    for part in re.split(r"[-+]", str(v or "").strip().lstrip("vV"), maxsplit=1)[0].split(".")[:3]:
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple((out + [0, 0, 0])[:3])


def prerelease(v):
    """'2.2.0-rc.7+abc' -> 'rc.7', '2.2.0' -> '' (Build-Kennung zaehlt nicht)"""
    core = str(v or "").strip().split("+", 1)[0]
    return core.split("-", 1)[1] if "-" in core else ""


def channel(v=None):
    """'stable' fuer Releases, 'rc' fuer Release Candidates, sonst 'testing' (auch alte '-testing.<sha>'-Images)"""
    pre = prerelease(VERSION if v is None else v)
    if not pre:
        return "stable"
    return "rc" if pre == "rc" or pre.startswith("rc.") else "testing"


def _key(v):
    """Sortierschluessel nach SemVer: Vorabversion < Release; Vorab-Teile numerisch < Text, Zahlen als Zahlen."""
    pre = prerelease(v)
    if not pre:
        return parse(v) + (1, ())
    parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return parse(v) + (0, parts)


def newer(a, b):
    """ist a neuer als b? (2.2.0 > 2.2.0-rc.7 > 2.2.0-rc.6 > 2.1.0; Build-Kennung egal)"""
    return _key(a) > _key(b)
