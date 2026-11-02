"""Client-Fehlerberichte und "Diagnose senden" (2.5, Feature clientlog1, Spezifikation docs/clientlog-spec.md).

POST /api/client/log  - automatische Fehlerberichte (Web: static/clientlog.js; Apps: gleiche API, eigene Runde)
POST /api/client/diag - "Diagnose senden" aus den Einstellungen: Ringpuffer der letzten Ereignisse + optionaler Freitext
                        des Nutzers -> kurze Kennung D-XXXXX (steht auch in der Log-Zeile)

Vertrag: docs/APP-API.md "Fehlerberichte und Diagnose". Keine Datenbank: je angenommenem POST genau EINE Zeile im
Server-Log ("clientlog {...}" bzw. "clientdiag {...}"), Konto nur als kurzer HMAC-Hash, keine IP-Adressen, keine Namen.
Nur angemeldet (Sitzung bzw. Geraete-Sitzung der Apps, Kopf X-Saga). Der Server bereinigt alles noch einmal selbst
(Platzhalter fuer Zahlen/IDs/UUIDs/Hex/E-Mail/URLs/Pfade/Zitate, Zeichenfilter, Laengen) - Clients tun das auch, aber
verlassen darf sich der Server darauf nicht. Unbekannte Felder werden verworfen. Admin-Schalter diag.clientlog
(Standard an): aus -> 404 und kein Feature clientlog1 in /api/version."""
import hashlib
import hmac
import json
import math
import re
import secrets
import time

from fastapi import APIRouter, Request

import auth
from saga.core import SECRET, _api_guard, _bad, parse_json, t
from fastapi.responses import JSONResponse

router = APIRouter()

FEATURE = "clientlog1"
LOG_MAX, DIAG_MAX = 16 * 1024, 64 * 1024            # Body-Grenzen (413)
ITEMS_MAX, EVENTS_MAX = 20, 200
MSG_MAX, STACK_LINES, STACK_MAX, LINE_MAX = 300, 20, 2000, 200
NOTE_MAX = 1000
T_MAX = 7 * 24 * 3600 * 1000                        # ms seit Start der Seite/App
# Ratenbegrenzung (gleitendes Fenster, nur im Speicher): (Fenster s, hoechstens) je Konto bzw. je IP. Die IP-Grenzen sind
# grosszuegig: hinter einem nicht eingetragenen Reverse Proxy (TRUSTED_PROXIES) teilen sich alle Konten eine Adresse
RATE_LOG = ((60, 10), (86400, 200))
RATE_LOG_IP = ((60, 120),)
RATE_DIAG = ((3600, 5),)
RATE_DIAG_IP = ((3600, 60),)
APPS = ("web", "ios", "mac", "android", "linux")
DEVS = ("phone", "tablet", "desktop")
KINDS = ("error", "unhandled", "crash", "anr", "warn")
EV_KINDS = ("err", "net", "view", "conn", "save", "life")
CONN_CH = ("sse", "poll", "ws", "collab", "push", "call")
CONN_ST = ("open", "closed", "error", "poll", "retry")
SAVE_ST = ("ok", "conflict", "offline", "error", "merged")
LIFE_ST = ("start", "hidden", "visible", "online", "offline", "resume")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
_VER = re.compile(r"[A-Za-z0-9._+\-]{0,40}")
_OS = re.compile(r"[A-Za-z0-9._ ()+;/\-]{0,60}")
_SID = re.compile(r"[A-Za-z0-9_\-]{8,40}")
_SCREEN = re.compile(r"[a-z][a-z0-9\-]{0,23}")
_DIAG_ALPHA = "23456789ABCDEFGHJKMNPQRSTVWXYZ"     # ohne 0/O/1/I/L/U (vorlesbar)

_hits = {}                                          # (art, schluessel) -> [zeitpunkte]


def cfg():
    return dict(auth.settings().get("diag") or {})


def enabled():
    return bool(cfg().get("clientlog", True))


def features():
    return [FEATURE] if enabled() else []


def acct_hash(uid):
    """Kurzer, nicht umkehrbarer Hash des Kontos (stabil je Server; derselbe fuer clientlog und clientdiag)."""
    return hmac.new(SECRET.encode(), ("clientlog|" + str(uid)).encode(), hashlib.sha256).hexdigest()[:10]


# --- Bereinigung -----------------------------------------------------------------------------------------------------
# Reihenfolge zaehlt: erst Dinge mit Struktur (URL, E-Mail, Zitat), dann IDs/Zahlen, zum Schluss der Zeichenfilter.
_CTRL = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")
_URL = re.compile(r"\b[a-z][a-z0-9+.\-]{1,15}://[^\s\"'<>()]*|\bwww\.[^\s\"'<>()]+|\b(?:blob|data|javascript|mailto|tel):[^\s\"'<>()]*", re.I)
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_SECRET = re.compile(r"\b(?:hkd?|sga|hk_test)_[A-Za-z0-9_\-]{6,}|\bBearer\s+\S+", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX = re.compile(r"\b(?:0x)?[0-9a-f]{8,}\b", re.I)
_TOKEN = re.compile(r"(?<![\w~])(?=[A-Za-z_\-+=]*\d)[A-Za-z0-9_\-+=]{20,}")   # lange Zufallsfolgen mit Ziffern (Base64, Schluessel)
_HANDLE = re.compile(r"[~@][\w.\-]{2,}")                       # Handles (~abc...), @erwaehnungen, @name/slug
_PATH = re.compile(r"(?<![\w.:<])/[^\s\"'<>()]+")                # absolute Pfade (auch /@name/seite)
_NUM = re.compile(r"(?<![\w$])\d+(?:[.,]\d+)*|(?<=[A-Za-z_$])\d{3,}")   # auch "note4711", nicht "v2"/"h264" teilweise
# Zitate: nur kurze Bezeichner (Eigenschaften, Funktionen) bleiben, sonst kann ein Titel/Inhalt darin stehen
_QUOTE = re.compile("'([^'\\n]{0,200})'|\"([^\"\\n]{0,200})\"|`([^`\\n]{0,200})`|\u201e([^\u201c\u201d\\n]{0,200})[\u201c\u201d]"
                    "|\u201c([^\u201d\\n]{0,200})\u201d|\u00ab([^\u00bb\\n]{0,200})\u00bb|\u2018([^\u2019\\n]{0,200})\u2019")
_IDENT = re.compile(r"[A-Za-z_$][\w$.]{0,39}")
_ALLOW = re.compile(r"[^A-Za-z0-9_\s.,:;()\[\]{}<>#'\"=+\-*/!?&|%$~^À-ÖØ-öø-ÿ]")   # sonst -> ?
_SPACES = re.compile(r"\s{2,}")


def _route_words(app):
    """Feste Bestandteile aller registrierten Pfade (api, notes, files, ...) - nur diese bleiben in Endpunkt-Mustern stehen."""
    words, todo, seen = set(), list(getattr(app, "routes", []) or []), 0
    while todo and seen < 20000:
        r = todo.pop()
        seen += 1
        sub = getattr(r, "original_router", None)         # eingebundene Router (neuere FastAPI: _IncludedRouter)
        if sub is not None:
            todo.extend(getattr(sub, "routes", []) or [])
        for seg in str(getattr(r, "path", "") or "").split("/"):
            if seg and "{" not in seg and re.fullmatch(r"[a-z][a-z0-9_.\-]{0,31}", seg):
                words.add(seg)
    return frozenset(words)


_WORDS = {"v": None}


def route_words():
    if _WORDS["v"] is None:
        try:
            import app as _app                      # erst zur Laufzeit (kein Zyklus beim Import)
            _WORDS["v"] = _route_words(_app.app)
        except Exception:  # noqa: BLE001 - ohne Liste: alle Abschnitte werden Platzhalter
            return frozenset()
    return _WORDS["v"]


def norm_path(p):
    """Endpunkt-Muster: Query/Fragment weg, jeder Abschnitt, der kein fester Pfadteil eines Saga-Endpunkts ist, wird
    :id (nur Ziffern, auch negativ) bzw. :x. Ergebnis z. B. /api/notes/:id/comments."""
    p = str(p or "").split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/"):
        return "/:x"
    words, out = route_words(), []
    for seg in p.split("/")[1:12]:
        if seg in ("", ":id", ":x"):
            out.append(seg)
        elif re.fullmatch(r"-?\d+", seg):
            out.append(":id")
        elif seg in words:
            out.append(seg)
        else:
            out.append(":x")
    return ("/" + "/".join(out))[:100]


def _static_path(m):
    """/static/<datei>.js (auch mit Host und ?v=) -> static/<datei>.js; andere Pfade -> <path>."""
    s = m.group(0)
    sm = re.match(r"/static/([\w.\-/]{1,80}?\.(?:js|mjs|css|html))(?:[?#]\S*)?$", s)
    return "static/" + sm.group(1) if sm else "<path>"


_STATIC = re.compile(r"/static/([\w.\-/]{1,80}?\.(?:js|mjs|css|html))(?:\?[^\s:#]*)?(:\d{1,7}:\d{1,8})?(?=$|[?#:])")


def _url(m):
    """Adresse -> <url>; nur eigene Skripte bleiben als static/<datei> (ohne Host, ohne ?v=)."""
    sm = _STATIC.search(m.group(0))
    return "static/" + sm.group(1) + (sm.group(2) or "") if sm else "<url>"


def _quote(m):
    inner = next((g for g in m.groups() if g is not None), "")
    q = m.group(0)[0]
    return m.group(0) if _IDENT.fullmatch(inner or "x") and inner else q + "<str>" + (q if q in "'\"`" else "")


def scrub(s, limit=MSG_MAX):
    """Fehlertext ohne Inhalte: URLs, E-Mail-Adressen, Pfade, Handles, Zitate mit mehr als einem Bezeichner, UUIDs,
    Hex-/Zufallsfolgen und Zahlen werden Platzhalter; erlaubt sind danach nur Buchstaben, Ziffern und einfache Satzzeichen."""
    if not isinstance(s, str):
        return ""
    s = _CTRL.sub(" ", s[:4000])
    s = _URL.sub(_url, s)
    s = _EMAIL.sub("<email>", s)
    s = _SECRET.sub("<secret>", s)
    s = _QUOTE.sub(_quote, s)
    s = _PATH.sub(_static_path, s)
    s = _HANDLE.sub("<id>", s)
    s = _UUID.sub("<uuid>", s)
    s = _HEX.sub("<hex>", s)
    s = _TOKEN.sub("<tok>", s)
    s = re.sub(r"(?<![\w.])static/[\w.\-/]+\.(?:js|mjs|css|html)(?::\d{1,7}:\d{1,8})?|" + _NUM.pattern,
               lambda m: m.group(0) if m.group(0).startswith("static/") else "<n>", s)
    s = _ALLOW.sub("?", s)
    s = _SPACES.sub(" ", s).strip()
    return s[:limit]


_LOC = re.compile(r"(?:[a-z][a-z0-9+.\-]*://[^/\s]*)?(/static/[\w.\-/]{1,80}?\.m?js)(?:\?[^\s:)]*)?:(\d{1,7}):(\d{1,8})", re.I)
_FN = re.compile(r"^\s*(?:at\s+)?(?:async\s+)?([A-Za-z_$<][\w$.<>\[\] ]{0,60}?)\s*(?:\(|@)")


_KEEP = re.compile(r"\b\w{1,60}\.(?:swift|kt|java|py|m|mm|c|cpp):\d{1,6}\b|\bline \d{1,6}\b")


def _scrub_ws(s):
    """scrub fuer ein Stueck einer Zeile - Leerzeichen am Rand bleiben (Zusammensetzen)."""
    if not s:
        return ""
    x = scrub(s, LINE_MAX)
    return (" " if s[:1].isspace() else "") + x + (" " if s[-1:].isspace() and x else "")


def scrub_stack(st):
    """Stack: je Zeile nur Funktionsname + static/<datei>.js:Zeile:Spalte (Web) bzw. bereinigte Kurzform (Apps)."""
    if not isinstance(st, str):
        return None
    out, total = [], 0
    for ln in st[:8000].replace("\r", "").split("\n"):
        ln = _CTRL.sub(" ", ln).strip()
        if not ln:
            continue
        loc = _LOC.search(ln)
        if loc:
            fm = _FN.match(ln)
            fn = re.sub(r"[^\w$.<>]", "", fm.group(1))[:60] if fm else ""
            x = (fn + " " if fn else "") + f"static/{loc.group(1)[8:]}:{loc.group(2)}:{loc.group(3)}"
        elif "://" in ln or re.search(r"\.(?:js|mjs)\b", ln):
            # fremde Quelle (Erweiterung, Einbettung) bzw. Datei ausserhalb von static/: nur die Zeilenangabe
            pos = re.search(r":(\d{1,7}):(\d{1,8})\)?\s*$", ln)
            fm = _FN.match(ln)
            fn = re.sub(r"[^\w$.<>]", "", fm.group(1))[:60] if fm else ""
            x = (fn + " " if fn else "") + "<ext>" + (f":{pos.group(1)}:{pos.group(2)}" if pos else "")
        else:
            # Apps (Swift/Kotlin/Python): Symbolnamen und Zeilen bleiben, alles andere wie scrub - Zeilennummern
            # hinter ":" bzw. "line " und Offsets "+ 123" bleiben als Zahl, laengere Ziffernfolgen nicht
            parts, pos = [], 0
            for km in _KEEP.finditer(ln):
                parts.append(_scrub_ws(ln[pos:km.start()]))
                parts.append(km.group(0))
                pos = km.end()
            parts.append(_scrub_ws(ln[pos:]))
            x = "".join(parts).strip()
        x = x[:LINE_MAX]
        if total + len(x) + 1 > STACK_MAX:
            break
        out.append(x)
        total += len(x) + 1
        if len(out) >= STACK_LINES:
            break
    return "\n".join(out) or None


def scrub_src(s):
    """Quelldatei: nur static/<datei>.js (Web) bzw. ein kurzer Dateiname ohne Pfad (Apps)."""
    if not isinstance(s, str) or not s:
        return None
    m = re.search(r"/static/([\w.\-/]{1,80}?\.m?js)(?:[?#].*)?$", s)
    if m:
        return "static/" + m.group(1)
    if "/" in s or ":" in s:
        return "<ext>"
    return s if re.fullmatch(r"[\w.\-]{1,60}", s) else None


def scrub_note(s):
    """Freitext des Nutzers (nur bei der Diagnose): Steuerzeichen weg, Geheimnisse (Schluessel, Tokens) maskiert, gekuerzt.
    Sonst unveraendert - die Person schreibt ihn bewusst fuer den Admin."""
    if not isinstance(s, str):
        return ""
    s = _CTRL.sub(" ", s.replace("\r\n", "\n").replace("\n", " \u23ce "))
    s = _SECRET.sub("<secret>", s)
    s = _URL.sub("<url>", s)
    s = _EMAIL.sub("<email>", s)
    s = re.sub(r"(?<![\w-])[A-Za-z0-9_\-+/=]{32,}", "<tok>", s)
    return _SPACES.sub(" ", s).strip()[:NOTE_MAX]


# --- Pruefung ----------------------------------------------------------------------------------------------------------
class Bad(ValueError):
    pass


def _str(d, k, rx=None, enum=None, need=True, maxlen=4000):
    v = d.get(k)
    if v is None and not need:
        return None
    if not isinstance(v, str) or len(v) > maxlen:
        raise Bad(k)
    if enum is not None and v not in enum:
        raise Bad(k)
    if rx is not None and not rx.fullmatch(v):
        raise Bad(k)
    return v


def _int(v, k, lo, hi, need=True):
    if v is None and not need:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v) or not lo <= v <= hi:
        raise Bad(k)
    return int(v)


def _head(b):
    if not isinstance(b, dict):
        raise Bad("body")
    _int(b.get("v"), "v", 1, 1)
    out = {"app": _str(b, "app", enum=APPS), "ver": _str(b, "ver", _VER), "os": _str(b, "os", _OS, need=False) or "",
           "sid": _str(b, "sid", _SID)[:16]}
    dv = _str(b, "dev", enum=DEVS, need=False)
    if dv:
        out["dev"] = dv
    return out


def _item(x, i, pre="items"):
    if not isinstance(x, dict):
        raise Bad(f"{pre}[{i}]")
    k = _str(x, "k", enum=KINDS)
    msg = x.get("msg")
    if not isinstance(msg, str) or len(msg) > 4000:
        raise Bad(f"{pre}[{i}].msg")
    it = {"k": k, "msg": scrub(msg)}
    st = x.get("stack")
    if st is not None:
        if not isinstance(st, str) or len(st) > 8000:
            raise Bad(f"{pre}[{i}].stack")
        st = scrub_stack(st)
        if st:
            it["stack"] = st
    src = x.get("src")
    if src is not None:
        if not isinstance(src, str) or len(src) > 500:
            raise Bad(f"{pre}[{i}].src")
        src = scrub_src(src)
        if src:
            it["src"] = src
    for f, hi in (("line", 10 ** 7), ("col", 10 ** 8)):
        v = _int(x.get(f), f"{pre}[{i}].{f}", 0, hi, need=False)
        if v is not None:
            it[f] = v
    sc = _str(x, "screen", _SCREEN, need=False)
    if sc:
        it["screen"] = sc
    n = _int(x.get("n"), f"{pre}[{i}].n", 1, 100000, need=False)
    if n and n > 1:
        it["n"] = n
    it["t"] = _int(x.get("t"), f"{pre}[{i}].t", 0, T_MAX)
    return it


def clean_log(b):
    out = _head(b)
    items = b.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= ITEMS_MAX:
        raise Bad("items")
    out["items"] = [_item(x, i) for i, x in enumerate(items)]
    return out


def _event(e, i):
    if not isinstance(e, dict):
        raise Bad(f"ev[{i}]")
    k = _str(e, "k", enum=EV_KINDS)
    x = {"t": _int(e.get("t"), f"ev[{i}].t", 0, T_MAX), "k": k}
    p = f"ev[{i}]"
    if k == "err":
        it = _item({**e, "k": e.get("kind") or "error"}, i, "ev")
        it.pop("t", None)
        it["kind"] = it.pop("k")
        x.update(it)
    elif k == "net":
        x["m"] = _str(e, "m", enum=METHODS)
        x["p"] = norm_path(_str(e, "p", maxlen=500))
        x["s"] = _int(e.get("s"), p + ".s", 0, 999)
        ms = _int(e.get("ms"), p + ".ms", 0, 3600 * 1000, need=False)
        if ms is not None:
            x["ms"] = ms
    elif k == "view":
        x["screen"] = _str(e, "screen", _SCREEN)
    elif k == "conn":
        x["ch"] = _str(e, "ch", enum=CONN_CH)
        x["st"] = _str(e, "st", enum=CONN_ST)
    elif k == "save":
        x["st"] = _str(e, "st", enum=SAVE_ST)
        s = _int(e.get("s"), p + ".s", 0, 999, need=False)
        if s is not None:
            x["s"] = s
    elif k == "life":
        x["st"] = _str(e, "st", enum=LIFE_ST)
    return x


def clean_diag(b):
    out = _head(b)
    ev = b.get("ev", [])
    if not isinstance(ev, list) or len(ev) > EVENTS_MAX:
        raise Bad("ev")
    out["ev"] = [_event(e, i) for i, e in enumerate(ev)]
    note = b.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note) > NOTE_MAX:
            raise Bad("note")
        note = scrub_note(note)
        if note:
            out["user_note"] = note
    return out


# --- Raten -------------------------------------------------------------------------------------------------------------
def _rate_ok(kind, key, rules, now):
    k = (kind, key)
    lst = [x for x in _hits.get(k, ()) if now - x < max(w for w, _ in rules)]
    for w, n in rules:
        if sum(1 for x in lst if now - x < w) >= n:
            _hits[k] = lst
            return False, w
    lst.append(now)
    _hits[k] = lst
    if len(_hits) > 50000:                            # Speicher begrenzen: alte Eintraege weg
        for kk in [kk for kk, v in _hits.items() if not v or now - v[-1] > 86400]:
            _hits.pop(kk, None)
    return True, 0


def _no_store(d, code=200):
    return JSONResponse(d, status_code=code, headers={"Cache-Control": "no-store"})


def _too_many(w):
    return _no_store({"error": t("Zu viele Anfragen – bitte kurz warten."), "retry_s": int(min(w, 3600))}, 429)


async def _read(request, limit):
    try:
        n = int(request.headers.get("content-length") or 0)
    except ValueError:
        n = 0
    if n > limit:
        return None, _bad("too large", 413)
    raw = await request.body()
    if len(raw) > limit:
        return None, _bad("too large", 413)
    return raw, None


def _ip(request):
    return (request.client.host if request.client else "") or "?"


def _line(prefix, rec):
    return prefix + " " + json.dumps(rec, separators=(",", ":"), ensure_ascii=False)


def new_diag_id():
    return "D-" + "".join(secrets.choice(_DIAG_ALPHA) for _ in range(5))


@router.post("/api/client/log")
async def api_log(request: Request):
    """Fehlerbericht (Vertrag docs/APP-API.md "Fehlerberichte und Diagnose") -> {ok: true}. 404 aus, 401, 413, 429, 400."""
    if not enabled():
        return _bad("not found", 404)
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    raw, err = await _read(request, LOG_MAX)
    if err:
        return err
    try:
        d = clean_log(parse_json(raw))
    except Bad as e:
        return _bad(t("ungültige Angabe") + f" ({e})")
    except Exception:  # noqa: BLE001 - kein JSON
        return _bad(t("ungültige Angabe"))
    now = time.time()
    good, w = _rate_ok("log", uid, RATE_LOG, now)
    if good:
        good, w = _rate_ok("log-ip", _ip(request), RATE_LOG_IP, now)
    if not good:
        return _too_many(w)
    print(_line("clientlog", {"acct": acct_hash(uid), **d}), flush=True)
    return _no_store({"ok": True})


@router.post("/api/client/diag")
async def api_diag(request: Request):
    """Diagnose senden -> {ok: true, id: "D-XXXXX"}; dieselbe Kennung steht in der Log-Zeile clientdiag."""
    if not enabled():
        return _bad("not found", 404)
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    raw, err = await _read(request, DIAG_MAX)
    if err:
        return err
    try:
        d = clean_diag(parse_json(raw))
    except Bad as e:
        return _bad(t("ungültige Angabe") + f" ({e})")
    except Exception:  # noqa: BLE001 - kein JSON
        return _bad(t("ungültige Angabe"))
    now = time.time()
    good, w = _rate_ok("diag", uid, RATE_DIAG, now)
    if good:
        good, w = _rate_ok("diag-ip", _ip(request), RATE_DIAG_IP, now)
    if not good:
        return _too_many(w)
    did = new_diag_id()
    print(_line("clientdiag", {"id": did, "acct": acct_hash(uid), **d, "n": len(d["ev"])}), flush=True)
    return _no_store({"ok": True, "id": did})
