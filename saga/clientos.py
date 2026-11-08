"""Grobe Klasse eines Clients (Betriebssystem bzw. App) aus dem User-Agent - fuer die Netz-Karte im Admin (2.5).

Gespeichert wird nur die Klasse (z. B. "ios-app", "web-windows"), nie der User-Agent selbst, kein Konto, keine IP.
Die Klassen sind bewusst grob: genauer als "Betriebssystem + App oder Browser" braucht die Karte nicht, und feinere
Angaben (Versionen, Geraetemodelle) waeren ein Fingerabdruck.

Quellen der Apps (saga-apps): iOS/iPadOS-App per URLSession ("<Name>/<build> CFNetwork/... Darwin/..."), Mac-Huelle
("... Macintosh ... SagaApp/<v> (mac-shell)"), Tauri-Huelle ("SagaApp/<v> (windows|linux)"), Android-App
("Saga-Android/<v>"), Linux-App ("Hlidskjalf-Linux/<v>"). Aeltere Apps mit eingebetteter Web-Ansicht: "HlidskjalfApp/".
Anruf-Teilnehmer: das Geraet meldet sich beim Anruf (Start, Annehmen, Beitreten, Lebenszeichen) - hier nur
Geraete-Kennung -> Klasse im Speicher (verfaellt nach 10 Minuten ohne Meldung)."""
import re
import time

CLASSES = ("ios-app", "android-app", "mac-app", "windows-app", "linux-app",
           "web-windows", "web-mac", "web-linux", "web-android", "web-ios", "web-other", "other")

_SHELL = re.compile(r"(?:SagaApp|HlidskjalfApp)/[^\s]*(?:\s*\(([^)]*)\))?", re.I)


def classify(ua):
    """User-Agent -> eine der CLASSES (nie der User-Agent selbst)."""
    u = str(ua or "")[:512]
    if not u:
        return "other"
    lo = u.lower()
    if lo.startswith("saga-android/") or " saga-android/" in lo:
        return "android-app"
    if "hlidskjalf-linux/" in lo or "saga-linux/" in lo:
        return "linux-app"
    m = _SHELL.search(u)
    if m:
        hint = (m.group(1) or "").lower()
        if "mac" in hint:
            return "mac-app"
        if "windows" in hint:
            return "windows-app"
        if "linux" in hint:
            return "linux-app"
        if "android" in lo:
            return "android-app"
        if "iphone" in lo or "ipad" in lo:
            return "ios-app"
        if "macintosh" in lo:
            return "mac-app"
        if "windows" in lo:
            return "windows-app"
        return "ios-app" if "mobile" in lo else "other"
    if "cfnetwork/" in lo and "darwin/" in lo:          # native Apple-App (URLSession): iPhone/iPad
        return "ios-app"
    if not lo.startswith("mozilla/"):
        return "other"
    if "android" in lo:
        return "web-android"
    if "iphone" in lo or "ipad" in lo:
        return "web-ios"
    if "windows" in lo:
        return "web-windows"
    if "macintosh" in lo or "mac os x" in lo:
        return "web-mac"
    if "linux" in lo or "x11" in lo or "cros" in lo:
        return "web-linux"
    return "web-other"


def of_request(request):
    try:
        return classify(request.headers.get("user-agent"))
    except Exception:  # noqa: BLE001 - nur Anzeige
        return "other"


# --- Anruf-Teilnehmer: Geraet -> Klasse (nur im Speicher) ---------------------------------------------------------
CALL_TTL = 600
CALL_MAX = 5000
_calls = {}                       # Geraete-Kennung -> (klasse, zuletzt)


def note_call(dev, request):
    """Ein Geraet meldet sich in einem Anruf (Start/Annehmen/Beitreten/Lebenszeichen)."""
    if not dev or not isinstance(dev, str) or len(dev) > 64:
        return
    now = time.time()
    _calls[dev] = (of_request(request), now)
    if len(_calls) > CALL_MAX:
        for k, (_, ts) in sorted(_calls.items(), key=lambda x: x[1][1])[:len(_calls) - CALL_MAX + 100]:
            _calls.pop(k, None)


def call_class(dev, now=None):
    v = _calls.get(dev)
    if not v or (now or time.time()) - v[1] > CALL_TTL:
        return "other"
    return v[0]
