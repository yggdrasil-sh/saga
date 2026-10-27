"""Datenbank-Ansichten serverseitig: Filter, Suche, Sortierung, Gruppen und Blaettern fuer GET .../rows (opt-in).

Bildet die Client-Logik des Webs GENAU nach (static/pages.js: matchFilter, relDate, rowText, filterRows, sortKey,
sortedRows, eachGrouped/renderBoard, calcValue2) - samt JavaScript-Eigenheiten: Vergleiche (<, ===), Number(),
String(), toLowerCase, Vergleich von Zeichenketten nach UTF-16-Codeeinheiten (nicht locale), stabile Sortierung.
Bewusst NICHT in SQL: diese Semantik (gemischte Typen, Number()-Umwandlung, Ortszeit) laesst sich in Postgres nicht
1:1 abbilden - der Server rechnet stattdessen in Python ueber die Zeilen der EINEN Datenbank. SQL uebernimmt nur das
reine Blaettern ohne Filter/Sortierung (LIMIT/OFFSET in app_api._db_rows).

Datum: 'diese Woche/heute' und das Tagesdatum von 'Erstellt'/'Zuletzt bearbeitet' rechnet das Web in der Ortszeit des
Browsers - hier in der Zeitzone aus dem Parameter tz (IANA, Standard UTC) bzw. mit dem Tag aus today (YYYY-MM-DD)."""
import json
import math
import re
from datetime import date, datetime, timezone
from functools import cmp_to_key
from zoneinfo import ZoneInfo

LIMIT_MAX = 5000
AUTO = {"created": "created", "edited": "updated"}
NOVALUE = {"is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month",
           "yesterday", "tomorrow", "last_week", "next_week", "last_month", "next_month", "this_year", "overdue"}
REL_OPS = NOVALUE - {"is_empty", "not_empty", "checked", "unchecked"}         # relative Datumsfilter ohne Wert
STATUS_GROUPS = ("todo", "doing", "done")


class JsError(Exception):
    """Wo das Web mit einem TypeError abbricht (kaputte Daten/Filter) - der Server antwortet dann mit 400."""


class _Undef:
    def __repr__(self):
        return "undefined"


UNDEF = _Undef()          # JavaScript undefined (fehlende Eigenschaft) - im Unterschied zu null (None)

# --- JavaScript-Grundlagen -----------------------------------------------------------------------------------------
_JS_WS = "\t\n\x0b\x0c\r                  　﻿"
_DEC = re.compile(r"[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)")
_RADIX = re.compile(r"0([xXoObB])([0-9a-zA-Z]+)")
# Eigenschaften von Object.prototype: known[x] ist fuer diese Schluessel im Web immer "wahr" (eachGrouped)
_PROTO = {"constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty", "__lookupGetter__", "__lookupSetter__",
          "isPrototypeOf", "propertyIsEnumerable", "toString", "valueOf", "__proto__", "toLocaleString"}


def _isnum(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def js_trim(s):
    return s.strip(_JS_WS)


def js_num_str(x):
    """String(zahl) wie JavaScript (kuerzeste Darstellung, Exponent ab 1e21 bzw. unter 1e-6)."""
    x = float(x)
    if x != x:
        return "NaN"
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    s = repr(abs(x))
    mant, _, ex = s.partition("e")
    ip, _, fp = mant.partition(".")
    d = ip + fp
    n = len(ip) + (int(ex) if ex else 0)
    lz = len(d) - len(d.lstrip("0"))
    d = d.lstrip("0")
    n -= lz
    d = d.rstrip("0") or "0"
    k = len(d)
    if k <= n <= 21:
        out = d + "0" * (n - k)
    elif 0 < n <= 21:
        out = d[:n] + "." + d[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + d
    else:
        e = n - 1
        out = d[0] + ("." + d[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def js_str(v):
    """String(v)"""
    if v is None:
        return "null"
    if v is UNDEF:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    if _isnum(v):
        return js_num_str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return ",".join("" if x is None or x is UNDEF else js_str(x) for x in v)
    return "[object Object]"


def _str_num(s):
    s = js_trim(s)
    if not s:
        return 0.0
    if s in ("Infinity", "+Infinity"):
        return math.inf
    if s == "-Infinity":
        return -math.inf
    if _DEC.fullmatch(s):
        return float(s)
    m = _RADIX.fullmatch(s)
    if m:
        try:
            return float(int(m.group(2), {"x": 16, "o": 8, "b": 2}[m.group(1).lower()]))
        except (ValueError, OverflowError):
            return math.nan
    return math.nan


def js_num(v):
    """Number(v)"""
    if v is None:
        return 0.0
    if v is UNDEF:
        return math.nan
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if _isnum(v):
        try:
            return float(v)
        except OverflowError:
            return math.inf if v > 0 else -math.inf
    if isinstance(v, str):
        return _str_num(v)
    return _str_num(js_str(v))                     # Array/Objekt: ueber ToPrimitive (String)


def js_truthy(v):
    if v is None or v is UNDEF:
        return False
    if isinstance(v, bool):
        return v
    if _isnum(v):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ""
    return True                                    # [] und {} sind in JavaScript wahr


def js_nullish(v):
    return v is None or v is UNDEF


def js_seq(a, b):
    """a === b"""
    if js_nullish(a) or js_nullish(b):
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _isnum(a) and _isnum(b):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return a is b                                  # Arrays/Objekte: nur dasselbe Objekt


def _u16(s):
    return s.encode("utf-16-be", "surrogatepass")


def js_lt(a, b):
    """a < b (abstrakter Vergleich): Zeichenketten nach UTF-16-Codeeinheiten, sonst als Zahl (NaN -> false)."""
    if isinstance(a, (list, dict)):
        a = js_str(a)
    if isinstance(b, (list, dict)):
        b = js_str(b)
    if isinstance(a, str) and isinstance(b, str):
        return _u16(a) < _u16(b)
    x, y = js_num(a), js_num(b)
    if x != x or y != y:
        return False
    return x < y


def js_len(v):
    """v.length fuer Zeichenkette (UTF-16-Einheiten) bzw. Array, sonst undefined."""
    if isinstance(v, str):
        return len(_u16(v)) // 2
    if isinstance(v, list):
        return len(v)
    return UNDEF


def js_slice(s, a, b):
    """s.slice(a, b) nach UTF-16-Einheiten (a, b >= 0)."""
    if s.isascii():
        return s[a:b]
    u = _u16(s)
    return u[2 * a:2 * b].decode("utf-16-be", "surrogatepass")


def js_lower(s):
    return s.lower()


def js_round(x):
    """Math.round: naechste ganze Zahl, bei .5 Richtung +unendlich."""
    if x != x or math.isinf(x):
        return x
    f = math.floor(x)
    return float(f + 1 if x - f >= 0.5 else f)


def _key(v):
    """Eigenschaftsschluessel wie obj[v] in JavaScript."""
    return js_str(v)


def _tkey(v):
    """Hashbarer Schluessel mit derselben Gleichheit wie js_seq (fuer Nachschlage-Tabellen) - None: nicht moeglich
    (Array/Objekt: === nur bei demselben Objekt; dann wie bisher Eintrag fuer Eintrag vergleichen)."""
    if v is None:
        return ("null",)
    if v is UNDEF:
        return ("undef",)
    if isinstance(v, bool):
        return ("b", v)
    if _isnum(v):
        try:
            x = float(v)
        except OverflowError:
            return None
        return None if x != x else ("n", x)            # NaN: nie gleich - Vergleich Eintrag fuer Eintrag
    if isinstance(v, str):
        return ("s", v)
    return None


def _first_match(items, field):
    """{_tkey(item[field]): erstes item} - wie items.find(x => x[field] === v) ueber eine Tabelle."""
    m = {}
    for it in items:
        k = _tkey(it.get(field, UNDEF))
        if k is not None and k not in m:
            m[k] = it
    return m


def _days_from_civil(y, m, d):
    """Tage seit 1970-01-01 im proleptischen gregorianischen Kalender (beliebige Jahre)."""
    y -= m <= 2
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _rel_date_day(s):
    """new Date(+s.slice(0,4), +s.slice(5,7) - 1, +s.slice(8,10)) als Tagesnummer (lokal) oder None (Invalid Date)."""
    y, m, d = js_num(js_slice(s, 0, 4)), js_num(js_slice(s, 5, 7)) - 1, js_num(js_slice(s, 8, 10))
    if any(x != x or math.isinf(x) for x in (y, m, d)):
        return None
    y, m, d = math.trunc(y), math.trunc(m), math.trunc(d)
    if 0 <= y <= 99:
        y += 1900
    y += m // 12
    m %= 12
    if abs(y) > 400000:
        return None
    day = _days_from_civil(y, m + 1, 1) + d - 1
    return day if abs(day) <= 100_000_000 else None          # TimeClip: +-8.64e15 ms


# --- 2.5 (datetime1): Datum mit Uhrzeit (lokale Wandzeit mit Offset) und Zeitraum "start/end" ----------------------
# Bildet static/pages.js dtParts/dayRange/dateKey GENAU nach. Nur "neue" Werte (mit Uhrzeit+Offset oder mit "/") werden
# so gelesen - alles andere wie bisher (erste 10 Zeichen), damit alte Daten und Vektoren unveraendert bleiben.
_DT_PART = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:T([0-9]{2}):([0-9]{2})(Z|[+-][0-9]{2}:[0-9]{2}))?")


def _dt_part(s, tz):
    """'JJJJ-MM-TT' oder 'JJJJ-MM-TTTHH:MM+hh:mm' -> (tagesnummer lokal in tz, 'JJJJ-MM-TT[THH:MM]' lokal) oder None."""
    m = _DT_PART.fullmatch(s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= y <= 9999 and 1 <= mo <= 12 and 1 <= d <= 31) or (m.group(4) is not None and not 1900 <= y <= 9998):
        return None
    try:
        date(y, mo, d)
    except ValueError:
        return None
    if m.group(4) is None:
        return _days_from_civil(y, mo, d), s
    hh, mi, off = int(m.group(4)), int(m.group(5)), m.group(6)
    oh, om = (0, 0) if off == "Z" else (int(off[1:3]), int(off[4:6]))
    if hh > 23 or mi > 59 or oh > 18 or om > 59:
        return None
    sg = -1 if off[0] == "-" else 1
    utc_min = (_days_from_civil(y, mo, d) * 1440 + hh * 60 + mi) - sg * (oh * 60 + om)
    loc = datetime.fromtimestamp(utc_min * 60, tz)
    return _days_from_civil(loc.year, loc.month, loc.day), f"{loc.year:04d}-{loc.month:02d}-{loc.day:02d}T{loc.hour:02d}:{loc.minute:02d}"


def date_parts(v, tz):
    """Neuer Datumswert -> (starttag, endtag, startschluessel, endschluessel) in tz; None = alter/kein neuer Wert."""
    if not isinstance(v, str) or len(v) <= 10:
        return None
    ps = v.split("/")
    if len(ps) > 2 or (len(ps) == 1 and "T" not in v):
        return None
    a = _dt_part(ps[0], tz)
    b = _dt_part(ps[1], tz) if len(ps) == 2 else a
    if a is None or b is None:
        return None
    return a[0], b[0], a[1], b[1]


def _day_iso(n):
    d = date.fromordinal(n + 719163)
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _month_start(y, m):
    """Tagesnummer des 1. im Monat m (beliebig, auch <1 / >12) - wie Date.UTC(y, m - 1, 1)."""
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return _days_from_civil(y, m, 1)


_TOKEN = re.compile(r"today(?:([+-])([0-9]{1,4}))?")
_NDAYS = re.compile(r"[0-9]{1,3}")


# --- Abfrage ueber den Zeilen einer Datenbank ------------------------------------------------------------------------
class Query:
    """Filter/Suche/Sortierung/Gruppen einer Ansicht. rows: [{'id','title','props','created','updated', ...}];
    labels(ids) -> {id: titel} fuer Relationen (nur Seiten, die der Betrachter im Web 'kennt', siehe app_api)."""

    def __init__(self, schema, filters=None, sorts=None, q="", group=None, tz=None, today=None, labels=None, untitled="Unbenannt"):
        self.schema = schema if isinstance(schema, dict) else {}
        props = self.schema.get("props")
        self.props = [p for p in props if isinstance(p, dict)] if isinstance(props, list) else []
        self.filters = filters or []
        self.sorts = sorts or []
        self.q = js_lower(js_trim(q or ""))
        self.group = group
        self.tz = tz or timezone.utc
        if today is None:
            today = datetime.now(self.tz).date()
        self.today = _days_from_civil(today.year, today.month, today.day)
        self.labels_fn = labels
        self._labels = None
        self.untitled = untitled
        self._pmap = _first_match(self.props, "id")
        self._fcache = {}
        self._rtcols = None

    # Spalten
    def prop_by_id(self, pid):
        k = _tkey(pid)
        if k is not None:
            return self._pmap.get(k, UNDEF)
        for p in self.props:
            if js_seq(p.get("id", UNDEF), pid):
                return p
        return UNDEF

    def fprop(self, pid):
        return {"id": "title", "name": "", "type": "text"} if js_seq(pid, "title") else self.prop_by_id(pid)

    @staticmethod
    def _props(r):
        p = r.get("props")
        return p if isinstance(p, dict) else {}

    def _get(self, r, k):
        return self._props(r).get(_key(k), UNDEF)

    def calc_value(self, p, r):
        pv = self._props(r)

        def num(k):
            v = pv.get(_key(k), UNDEF)
            if js_nullish(v) or v == "" and isinstance(v, str):
                return None
            n = js_num(v)
            return None if n != n else n
        a, b = num(p.get("a", UNDEF)), num(p.get("b", UNDEF))
        op = p.get("op") if js_truthy(p.get("op")) else "div"
        if js_seq(op, "add") or js_seq(op, "sub"):
            if a is None and b is None:
                return None
            a, b = a or 0.0, b or 0.0
        elif a is None or b is None:
            return None
        if js_seq(op, "div") or js_seq(op, "pct"):
            if not b:
                return None
            v = a / b * (100 if js_seq(op, "pct") else 1)
        else:
            v = a * b if js_seq(op, "mul") else a + b if js_seq(op, "add") else a - b
        return js_round(v * 10000) / 10000

    def prop_value(self, p, r):
        t = p.get("type")
        if js_seq(t, "calc"):
            return self.calc_value(p, r)
        if isinstance(t, str) and t in AUTO:
            return r.get(AUTO[t], UNDEF)
        return self._get(r, p.get("id", UNDEF))

    def iso_day(self, ts):
        try:
            d = datetime.fromtimestamp(js_num(ts), self.tz)
            return f"{d.year}-{d.month:02d}-{d.day:02d}"
        except (ValueError, OverflowError, OSError):
            return "NaN-NaN-NaN"

    @staticmethod
    def num_like(p):
        return isinstance(p, dict) and (js_seq(p.get("type"), "number") or js_seq(p.get("type"), "calc")
                                        or (is_fx(p) and js_seq(p.get("rtype"), "number")))

    # Filter (matchFilter)
    def day_span(self, v):
        """(starttag, endtag) eines Werts (static/pages.js daySpan) oder None (Invalid Date)."""
        dp = date_parts(v, self.tz)
        if dp is not None:
            return dp[0], dp[1]
        x = _rel_date_day(js_str(v) if js_truthy(v) else "")
        return None if x is None else (x, x)

    def rel_date(self, op, iso, fv=None):
        """Relative Datumsfilter (static/pages.js relDate): Zeitraum [a, b) in Tagen; ein Zeitraum-Wert passt, wenn er das
        Fenster beruehrt. overdue: Ende vor heute. next_days/past_days N (1-366, inkl. heute), on_or_before/on_or_after
        mit Datum oder today, today+N, today-N. Unvollstaendiger Wert -> True (zaehlt nicht)."""
        t0 = self.today
        sp = self.day_span(iso)
        if op in ("next_days", "past_days", "on_or_before", "on_or_after"):
            s = fv if isinstance(fv, str) else ""
            if op in ("next_days", "past_days"):
                if not _NDAYS.fullmatch(s) or not 1 <= int(s) <= 366:
                    return True
                n = int(s)
                a, b = (t0, t0 + n) if op == "next_days" else (t0 - n + 1, t0 + 1)
                return sp is not None and sp[0] < b and sp[1] >= a
            m = _TOKEN.fullmatch(s)
            if m:
                ref = t0 + (0 if m.group(1) is None else (1 if m.group(1) == "+" else -1) * int(m.group(2)))
            elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
                ref = _rel_date_day(s)
                if ref is None:
                    return True
            else:
                return True
            return sp is not None and (sp[0] <= ref if op == "on_or_before" else sp[1] >= ref)
        if op == "overdue":
            return sp is not None and sp[1] < t0
        d = date.fromordinal(t0 + 719163)
        if op in ("this_month", "last_month", "next_month"):
            k = {"this_month": 0, "last_month": -1, "next_month": 1}[op]
            a, b = _month_start(d.year, d.month + k), _month_start(d.year, d.month + k + 1)
        elif op == "this_year":
            a, b = _days_from_civil(d.year, 1, 1), _days_from_civil(d.year + 1, 1, 1)
        elif op in ("this_week", "last_week", "next_week"):
            a = t0 - d.weekday() + {"this_week": 0, "last_week": -7, "next_week": 7}[op]   # (getDay() + 6) % 7: Montag = 0
            b = a + 7
        else:
            a = t0 + {"today": 0, "yesterday": -1, "tomorrow": 1}.get(op, 0)
            b = a + 1
        return sp is not None and sp[0] < b and sp[1] >= a

    def _fc(self, f):
        """Feste Teile eines Filters (Spalte, Operator, Wert) - einmal je Filter statt je Zeile (Ergebnis wie matchFilter)."""
        c = self._fcache.get(id(f))
        if c is not None and c[0] is f:
            return c[1]
        p = self.fprop(f.get("pid", UNDEF))
        if p is UNDEF:
            out = None
        else:
            op = f.get("op", UNDEF)
            fv = f.get("value", UNDEF)
            novalue = js_nullish(fv) or (isinstance(fv, str) and fv == "")
            if not novalue and js_seq(p.get("type"), "relation"):
                fv = js_num(fv)
            out = (p, js_seq(f.get("pid", UNDEF), "title"), isinstance(p.get("type"), str) and p["type"] in AUTO,
                   op if isinstance(op, str) else None, fv, novalue,          # js_seq(op, "x") <=> op ist Text "x"
                   js_lower(fv) if isinstance(fv, str) else None, js_num(fv) if not novalue else None, self.num_like(p))
        self._fcache[id(f)] = (f, out)
        return out

    def match(self, r, f):
        if not isinstance(f, dict):
            if f is None:
                raise JsError("filter")
            return True
        c = self._fc(f)
        if c is None:
            return True
        p, title, auto, op, fv, novalue, fv_low, fv_num, numlike = c
        v = r.get("title", UNDEF) if title else self.prop_value(p, r)
        if auto and js_truthy(v):
            v = self.iso_day(v)
        empty = js_nullish(v) or (isinstance(v, str) and v == "") or v is False or (isinstance(v, list) and not v)
        if op == "is_empty":
            return empty
        if op == "not_empty":
            return not empty
        if op == "checked":
            return js_truthy(v)
        if op == "unchecked":
            return not js_truthy(v)
        if op in REL_OPS:
            return not empty and self.rel_date(op, v)
        if novalue:
            return True
        if op in ("next_days", "past_days", "on_or_before", "on_or_after"):
            return not empty and self.rel_date(op, v, fv)
        if op == "status_group":                               # 2.5: Gruppe der Option (ohne Angabe: todo); "open" = nicht erledigt
            if not isinstance(fv, str) or fv not in STATUS_GROUPS + ("open",):
                return True
            if empty:
                return False
            grp = status_group(p, v)
            return grp is not None and (grp != "done" if fv == "open" else grp == fv)
        dspan = date_parts(v, self.tz) if js_seq(p.get("type"), "date") and op in ("eq", "before", "after") else None
        if dspan is not None:                                  # Datum mit Uhrzeit/Zeitraum: nach Tag in tz (fv als Text)
            if not isinstance(fv, str):
                return False
            a, b = _day_iso(dspan[0]), _day_iso(dspan[1])
            return (a <= fv <= b) if op == "eq" else (a < fv) if op == "before" else (b > fv)
        if op == "contains" or op == "not_contains":
            if not isinstance(v, list) and isinstance(p, dict) and js_seq(p.get("type"), "relation"):
                v = []                                         # Relation ohne Verknuepfung = leere Liste (wie static/pages.js)
            if isinstance(v, list):
                hit = any(js_seq(x, fv) for x in v)
            else:
                if fv_low is None:
                    raise JsError("fv.toLowerCase")
                hit = js_lower(js_str(v) if js_truthy(v) else "").find(fv_low) >= 0
            return hit if op == "contains" else not hit
        if op == "is":
            return js_seq(v, fv)
        if op == "is_not":
            return not js_seq(v, fv)
        if op == "eq":
            if numlike:
                if js_nullish(v):
                    return False
                return js_num(v) == fv_num                     # NaN == NaN ist auch hier falsch
            return js_seq(v, fv)
        if op == "gt":
            return not js_nullish(v) and js_lt(fv_num, js_num(v))
        if op == "lt":
            return not js_nullish(v) and js_lt(js_num(v), fv_num)
        if op == "before":
            return js_truthy(v) and js_lt(v, fv)
        if js_seq(op, "after"):
            return js_truthy(v) and js_lt(fv, v)
        return True

    # Suche (rowText)
    def _label(self, i):
        if self._labels is None:
            self._labels = {}
        if i not in self._labels:
            got = self.labels_fn([i]) if self.labels_fn else {}
            self._labels[i] = got.get(i, UNDEF)
        t = self._labels[i]
        return UNDEF if t is UNDEF else (t if t else self.untitled)

    def prefetch_labels(self, rows):
        """Titel aller verknuepften Eintraege mit EINER Abfrage holen (nur wenn gesucht wird)."""
        if not self.q or not self.labels_fn:
            return
        ids = set()
        rels = [p for p in self.props if js_seq(p.get("type"), "relation")]
        for r in rows:
            pr = self._props(r)
            for p in rels:
                v = pr.get(_key(p.get("id", UNDEF)))
                if isinstance(v, list):
                    ids.update(int(x) for x in v if _isnum(x) and float(x).is_integer())
        got = self.labels_fn(sorted(ids)) if ids else {}
        self._labels = {i: got.get(i, UNDEF) for i in ids}

    def _row_text_cols(self):
        """Spalten fuer rowText einmal vorbereiten: (Schluessel, Art, {Option-ID: Option}, Optionen)."""
        if self._rtcols is None:
            cols = []
            for p in self.props:
                t = p.get("type")
                if js_seq(t, "relation"):
                    kind, opts = "rel", []
                elif js_seq(t, "select") or js_seq(t, "multi_select"):
                    kind = "sel"
                    opts = [x for x in p.get("options") if isinstance(x, dict)] if isinstance(p.get("options"), list) else []
                else:
                    kind, opts = "", []
                cols.append((_key(p.get("id", UNDEF)), kind, _first_match(opts, "id"), opts))
            self._rtcols = cols
        return self._rtcols

    def row_text(self, r):
        parts = [r.get("title") if js_truthy(r.get("title")) else ""]
        pr = self._props(r)
        for key, kind, omap, opts in self._row_text_cols():
            v = pr.get(key, UNDEF)
            if js_nullish(v):
                continue
            if kind == "rel":
                if not js_truthy(v):
                    continue
                if not isinstance(v, list):
                    raise JsError("forEach")
                for i in v:
                    if _isnum(i) and float(i).is_integer():           # Map.get: nur Zahlen sind Seiten-IDs
                        lab = self._label(int(i))
                        if lab is not UNDEF:
                            parts.append(lab)
            elif kind == "sel":
                for i in (v if isinstance(v, list) else [v]):
                    k = _tkey(i)                                       # options.find(o => o.id === i)
                    o = omap.get(k) if k is not None else next((x for x in opts if js_seq(x.get("id", UNDEF), i)), None)
                    if o is not None:
                        parts.append(o.get("name", UNDEF))
            else:
                parts.append(js_str(v))
        return js_lower(" ".join("" if js_nullish(x) else js_str(x) for x in parts))

    # Sortierung (sortKey / sortedRows)
    def sort_key(self, p, r):
        if p is None:
            t = r.get("title")
            return js_lower(t if js_truthy(t) else "")
        v = self.prop_value(p, r)
        t = p.get("type")
        if js_seq(t, "select"):
            opts = p.get("options") if isinstance(p.get("options"), list) else []
            for i, o in enumerate(opts):
                if isinstance(o, dict) and js_seq(o.get("id", UNDEF), v):
                    return i
            return 1e9
        if js_seq(t, "multi_select") or js_seq(t, "relation") or (is_fx(p) and js_seq(p.get("rtype"), "list")):
            return js_len(v if js_truthy(v) else [])
        if js_seq(t, "checkbox"):
            return 1 if js_truthy(v) else 0
        if js_nullish(v):
            return None
        if js_seq(t, "date"):                                  # Datum mit Uhrzeit/Zeitraum: Start, lokal in tz
            dp = date_parts(v, self.tz)
            if dp is not None:
                return dp[2]
        return js_lower(v) if isinstance(v, str) else v

    def sort_keys(self):
        out = []
        for s in self.sorts:
            if s is None:
                raise JsError("sort")
            pid = s.get("pid", UNDEF) if isinstance(s, dict) else UNDEF
            p = None if js_seq(pid, "title") else self.prop_by_id(pid)
            if p is UNDEF:
                continue
            out.append((p, -1 if isinstance(s, dict) and js_seq(s.get("dir"), "desc") else 1))
        return out

    def sorted_rows(self, rows):
        rows = self.filter_rows(rows)
        keys = self.sort_keys()
        if not keys:
            return rows
        fast = self._sort_fast(rows, keys)
        if fast is not None:
            return fast
        cache = {}

        def key(i, k, r):
            c = (id(r), i)
            if c not in cache:
                cache[c] = self.sort_key(k, r)
            return cache[c]

        def cmp(a, b):
            for i, (p, d) in enumerate(keys):
                x, y = key(i, p, a), key(i, p, b)
                if js_seq(x, y):
                    continue
                if js_nullish(x):
                    return 1
                if js_nullish(y):
                    return -1
                return (-1 if js_lt(x, y) else 1) * d
            return 0
        return sorted(rows, key=cmp_to_key(cmp))

    def _sort_fast(self, rows, keys):
        """Dieselbe Reihenfolge wie der Vergleich oben, aber ohne Vergleichsfunktion - nur wenn jede Sortierstufe eine
        echte Ordnung ist: Werte alle Text (UTF-16-Reihenfolge) oder alle Zahlen ohne NaN, Leeres (null) dazwischen.
        Dann ist das Ergebnis einer stabilen Sortierung eindeutig: Stufe fuer Stufe von hinten stabil sortieren, Leeres
        jeweils ans Ende (unabhaengig von der Richtung). Gemischte Typen/NaN/undefined -> None (Vergleichsfunktion)."""
        if len(rows) < 2:
            return None
        cols = []
        for p, d in keys:
            vals, kind = [], None
            for r in rows:
                v = self.sort_key(p, r)
                if v is None:
                    vals.append(None)
                    continue
                if isinstance(v, str):
                    k = "s"
                    v = _u16(v)
                elif _isnum(v):
                    k = "n"
                    try:
                        v = float(v)
                    except OverflowError:
                        return None
                    if v != v:
                        return None
                else:
                    return None
                if kind is None:
                    kind = k
                elif kind != k:
                    return None
                vals.append(v)
            cols.append((vals, d))
        idx = list(range(len(rows)))
        for vals, d in reversed(cols):
            full = [i for i in idx if vals[i] is not None]
            full.sort(key=vals.__getitem__, reverse=d < 0)          # stabil, auch rueckwaerts
            idx = full + [i for i in idx if vals[i] is None]
        return [rows[i] for i in idx]

    def filter_rows(self, rows):
        fl = self.filters
        if self.q:
            self.prefetch_labels(rows)
        return [r for r in rows if all(self.match(r, f) for f in fl) and (not self.q or self.q in self.row_text(r))]

    # Gruppen (eachGrouped / renderBoard): erst "Ohne <Spalte>", dann je Option in Schema-Reihenfolge
    def groups(self, rows, g, by_status=False):
        if by_status and is_status(g):                         # 2.5: nach Status-Gruppe (Spalten todo/doing/done)
            gid = g.get("id", UNDEF)
            out = [(None, [r for r in rows if status_group(g, self._get(r, gid)) is None])]
            for grp in STATUS_GROUPS:
                out.append((grp, [r for r in rows if status_group(g, self._get(r, gid)) == grp]))
            return out
        if not js_seq(g.get("type"), "select"):                  # Formel/Rollup: Gruppen = vorkommende Werte (nur lesend)
            gid = g.get("id", UNDEF)
            return value_groups(rows, g, lambda r: self._get(r, gid))
        opts = [o for o in (g.get("options") if isinstance(g.get("options"), list) else []) if isinstance(o, dict)]
        known = {_key(o.get("id", UNDEF)) for o in opts} | _PROTO
        gid = g.get("id", UNDEF)
        out = [(None, [r for r in rows if _key(self._get(r, gid)) not in known])]
        for o in opts:
            oid = o.get("id", UNDEF)
            out.append((oid, [r for r in rows if js_seq(self._get(r, gid), oid)]))
        return out


def is_status(p):
    """Status-Eigenschaft (2.5, P5-E2): Auswahl mit status:true - Optionen tragen group todo/doing/done."""
    return isinstance(p, dict) and js_seq(p.get("type"), "select") and p.get("status") is True


def status_group(p, v):
    """Gruppe der gewaehlten Option (ohne Angabe: todo); None = keine Status-Eigenschaft oder unbekannte Option."""
    if not is_status(p):
        return None
    for o in p.get("options") if isinstance(p.get("options"), list) else []:
        if isinstance(o, dict) and js_seq(o.get("id", UNDEF), v):
            g = o.get("group")
            return g if g in STATUS_GROUPS else "todo"
    return None


def is_fx(p):
    """Formel oder Rollup (Wert kommt fertig vom Server, Typ des Ergebnisses in rtype)."""
    return isinstance(p, dict) and (js_seq(p.get("type"), "formula") or js_seq(p.get("type"), "rollup"))


GROUPABLE = ("number", "text", "checkbox", "date")


def groupable(p):
    """Darf nach p gruppiert werden (static/pages.js groupable)? Auswahl; Formel/Rollup mit Ergebnis Zahl/Text/Haekchen/Datum."""
    if not isinstance(p, dict):
        return False
    return js_seq(p.get("type"), "select") or (is_fx(p) and isinstance(p.get("rtype"), str) and p["rtype"] in GROUPABLE)


def _jstype(v):
    if isinstance(v, bool):
        return "boolean"
    if _isnum(v):
        return "number"
    if isinstance(v, str):
        return "string"
    return "undefined" if v is UNDEF else "object"


def value_groups(rows, g, get):
    """Gruppen nach vorkommenden Werten (static/pages.js valueGroups): [(UNDEF, leere), (wert, zeilen), ...].
    Haekchen: nein/ja (keine leeren). Reihenfolge: Zahlen aufsteigend (keine Zahl zuletzt); Text nach Wochentag, wenn ALLE
    Werte Wochentagsnamen sind (formatDate "dddd"/"ddd"), sonst nach UTF-16-Codeeinheiten; Datum als Text. Gleicher
    Wert (typeof + String) = eine Gruppe, Wert der Gruppe = erstes Vorkommen."""
    from saga.formula import WEEKDAY_INDEX
    rt = g.get("rtype")
    empty, order, buckets = [], [], {}
    for r in rows:
        v = get(r)
        if js_seq(rt, "checkbox"):
            v = v is True
        elif js_nullish(v) or (isinstance(v, str) and v == ""):
            empty.append(r)
            continue
        k = (_jstype(v), js_str(v))
        if k not in buckets:
            buckets[k] = (v, [])
            order.append(k)
        buckets[k][1].append(r)
    vals = [buckets[k][0] for k in order]
    if js_seq(rt, "number"):
        def key(v):
            x = js_num(v)
            return (1, 0.0) if x != x else (0, x)
    elif js_seq(rt, "checkbox"):
        def key(v):
            return (0, 1 if v is True else 0)
    elif vals and all(isinstance(v, str) and v in WEEKDAY_INDEX for v in vals):
        def key(v):
            return (0, WEEKDAY_INDEX[v])
    else:
        def key(v):
            return (0, _u16(js_str(v)))
    idx = sorted(range(len(order)), key=lambda i: key(vals[i]))
    return [(UNDEF, empty)] + [(vals[i], buckets[order[i]][1]) for i in idx]


def view_of(schema, vid):
    """curView(): Ansicht mit dieser id, sonst die erste; altes Einzelfeld 'sort' -> 'sorts'."""
    vs = schema.get("views") if isinstance(schema, dict) else None
    vs = [v for v in vs if isinstance(v, dict)] if isinstance(vs, list) else []
    v = next((x for x in vs if js_seq(x.get("id", UNDEF), vid)), None) or (vs[0] if vs else None)
    if v is not None and js_truthy(v.get("sort")) and not js_truthy(v.get("sorts")):
        v = dict(v, sorts=[v["sort"]])
    return v


def group_prop(q, view, explicit=UNDEF):
    """Gruppier-Spalte: explizit (group=pid, '' = keine) oder aus der Ansicht - Board: group_by (sonst erste
    Auswahl-Spalte wie renderBoard), Tabelle/Liste: group (nur Auswahl-Spalten)."""
    def sel(p):
        return p if groupable(p) else None
    if explicit is not UNDEF:
        return sel(q.prop_by_id(explicit)) if explicit else None
    if not view:
        return None
    t = view.get("type")
    if js_seq(t, "board"):
        return sel(q.prop_by_id(view.get("group_by", UNDEF))) or next((p for p in q.props if js_seq(p.get("type"), "select")), None)
    if js_seq(t, "gallery") or js_seq(t, "calendar"):
        return None
    # Tabelle, Liste und jede unbekannte Darstellung (renderDb zeigt dann die Tabelle)
    return sel(q.prop_by_id(view["group"])) if js_truthy(view.get("group")) else None


def parse_args(params):
    """Query-Parameter von /rows -> dict oder None (keiner gesetzt: alte Antwort). ValueError bei falschen Werten."""
    keys = ("limit", "offset", "view", "filters", "sorts", "q", "group", "tz", "today")
    if not any(k in params for k in keys):
        return None
    a = {}
    for k in ("limit", "offset"):
        if k in params:
            try:
                n = int(params[k])
            except ValueError:
                raise ValueError(k)
            if n < (1 if k == "limit" else 0) or n > 2 ** 62:           # groesser: Postgres-OFFSET (bigint) -> 500
                raise ValueError(k)
            a[k] = min(n, LIMIT_MAX) if k == "limit" else n
    for k in ("filters", "sorts"):
        if k in params:
            try:
                v = json.loads(params[k])
            except ValueError:
                raise ValueError(k)
            if not isinstance(v, list) or len(v) > 50:
                raise ValueError(k)
            a[k] = v
    for k in ("view", "q", "group"):
        if k in params:
            if len(params[k]) > 500:
                raise ValueError(k)
            a[k] = params[k]
    if params.get("tz"):
        try:
            a["tz"] = ZoneInfo(params["tz"])
        except (ValueError, KeyError, OSError):
            raise ValueError("tz")
    if params.get("today"):
        try:
            a["today"] = date.fromisoformat(params["today"])
        except ValueError:
            raise ValueError("today")
    return a


_NUMCHARS = frozenset("0123456789.e+-nainfty")      # String(zahl) kleingeschrieben: Ziffern, Exponent, NaN, Infinity
_OBJ = "[object object]"


def q_prefilter(schema, args):
    """Suche (q) in /rows ohne JSON-Lesen jeder Zeile vorfiltern: -> None (nicht sicher moeglich) oder
    f(titel, props_roh) -> bool. False heisst: die Zeile kann die Suche NICHT bestehen (rowText enthaelt q sicher nicht);
    True: Kandidat, danach entscheidet die unveraenderte Logik (filter_rows) - Treffer, Reihenfolge, Gruppen, Fehler gleich.

    Begruendung: rowText = lower(Teile mit " " verbunden); Teile = Titel, String(wert) je Spalte, Optionsnamen, Titel von
    Relationen. Ohne Leerzeichen in q liegt jeder Treffer in EINEM Teil; ohne σ/ς in q stimmt lower(Teil) dort mit
    lower(ganzer Text) ueberein (einzige kontextabhaengige Kleinschreibung in Python: Schluss-Sigma). Ein Text-Wert steht
    im gespeicherten JSON woertlich, solange es keinen Backslash gibt (sonst Kandidat); true/false ebenso. Zahlen
    (String(zahl) besteht nur aus _NUMCHARS), Arrays (mit Komma verbunden) und Objekte ("[object Object]") schliesst q
    selbst aus. Optionsnamen: Zeile ist Kandidat, wenn sie die id einer passenden Option enthaelt. Nicht vorfiltern bei
    Relationen (Titel anderer Seiten), q mit Leerzeichen/Komma/Sigma, q nur aus Zahlzeichen und bei Filtern, die je Zeile
    einen Fehler ausloesen koennen (null, enthaelt mit Nicht-Text-Wert) - der Fehler darf nicht von der Suche abhaengen."""
    q = js_lower(js_trim(args.get("q", "") or ""))
    if isinstance(schema, dict) and isinstance(schema.get("props"), list) and any(is_fx(p) for p in schema["props"]):
        return None                                  # Formel-/Rollup-Werte stehen nicht im gespeicherten JSON
    if not q or any(ch in q for ch in " ,σς") or q in _OBJ or set(q) <= _NUMCHARS:
        return None
    view = view_of(schema, args["view"]) if "view" in args else None
    filters = args["filters"] if "filters" in args else (view.get("filters") if view and isinstance(view.get("filters"), list) else [])
    for f in filters:
        if f is None:
            return None
        if isinstance(f, dict) and isinstance(f.get("op"), str) and f["op"] in ("contains", "not_contains") \
                and not isinstance(f.get("value"), str):
            return None
    needles = set()
    for _key_, kind, _omap, opts in Query(schema)._row_text_cols():
        if kind == "rel":
            return None
        if kind == "sel":
            for o in opts:
                name = o.get("name", UNDEF)
                if q in js_lower("" if js_nullish(name) else js_str(name)):
                    oid = o.get("id", UNDEF)
                    if not isinstance(oid, str):
                        return None
                    needles.add('"' + oid + '"')
    needles = tuple(needles)

    def cand(title, raw):
        if not isinstance(title, str) and title is not None:
            return True
        if q in js_lower(title or ""):
            return True
        if not raw:
            return False                             # keine Werte (wie _json_load -> {})
        if not isinstance(raw, str) or "\\" in raw:
            return True
        return q in raw.lower() or any(n in raw for n in needles)
    return cand


def run(schema, rows, args, labels=None, untitled="Unbenannt"):
    """Ansicht anwenden. -> (zeilen der Seite, treffer, gruppen | None). rows in Server-Reihenfolge (sort, created)."""
    view = view_of(schema, args["view"]) if "view" in args else None
    filters = args["filters"] if "filters" in args else (view.get("filters") if view and isinstance(view.get("filters"), list) else [])
    sorts = args["sorts"] if "sorts" in args else (view.get("sorts") if view and isinstance(view.get("sorts"), list) else [])
    q = Query(schema, filters, sorts, args.get("q", ""), tz=args.get("tz"), today=args.get("today"), labels=labels, untitled=untitled)
    out = q.sorted_rows(rows)
    matched = len(out)                                       # wie "n von total Eintraegen" im Web
    g = group_prop(q, view, args["group"] if "group" in args else UNDEF)
    groups = None
    by_status = bool(view and view.get("group_status") is True and "group" not in args)
    sub = (view or {}).get("sub") if g is None else None
    if isinstance(sub, dict) and sub.get("mode") in ("nested", "parents_only"):
        out = nest(q, rows, out, sub)
        matched = len(out)
    if g is not None:
        gs = q.groups(out, g, by_status)
        if js_seq(g.get("type"), "select"):
            groups = [{"id": None if oid is UNDEF else oid, "count": len(rs)} for oid, rs in gs]
        else:                                                # Formel/Rollup: id = Wert als Text, value = Wert (nur lesend)
            groups = [{"id": None, "count": len(rs)} if v is UNDEF else {"id": js_str(v), "value": v, "count": len(rs)} for v, rs in gs]
        out = [r for _, rs in gs for r in rs]                # Reihenfolge wie angezeigt (gruppiert)
    off = args.get("offset", 0)
    out = out[off:off + args["limit"]] if "limit" in args else out[off:]
    return out, matched, groups


SUB_DEPTH = 10


def nest(q, all_rows, shown, sub):
    """Unteraufgaben (2.5, view.sub): Kinder direkt hinter ihren Eltern, eingerueckt. Passt ein Kind zum Filter, bleibt die
    Eltern-Kette sichtbar (wie Notion). Reihenfolge je Ebene = sortierte Reihenfolge. Setzt r["_depth"] und r["_sub"]
    (Eltern-ID oder None) an den Zeilen. parents_only: nur Zeilen ohne (vorhandene) Eltern. Kreise/zu tief: als Wurzel."""
    pid = _key(sub.get("parent"))
    by_id = {r.get("id"): r for r in all_rows}

    def parent_of(r):
        v = q._props(r).get(pid)
        if isinstance(v, list) and v and _isnum(v[0]) and not isinstance(v[0], bool) and v[0] in by_id and v[0] != r.get("id"):
            return v[0]
        return None
    if sub.get("mode") == "parents_only":
        res = [r for r in shown if parent_of(r) is None]
        for r in res:
            r["_depth"], r["_sub"] = 0, None
        return res
    keep, order = {}, {}
    for i, r in enumerate(shown):
        order[r.get("id")] = i
        keep[r.get("id")] = r
    for r in shown:                                           # Eltern-Kette der Treffer dazunehmen
        cur, n = parent_of(r), 0
        while cur is not None and n < SUB_DEPTH:
            if cur not in keep:
                keep[cur] = by_id[cur]
            cur, n = parent_of(by_id[cur]), n + 1
    base = len(shown)
    rank = {rid: order.get(rid, base + k) for k, rid in enumerate(r.get("id") for r in all_rows)}
    kids, roots = {}, []
    for rid, r in keep.items():
        par = parent_of(r)
        # Kreis (Eltern-Kette kommt zurueck) oder Eltern nicht sichtbar -> Wurzel
        seen, cur = {rid}, par
        while cur is not None and cur not in seen and len(seen) <= SUB_DEPTH:
            seen.add(cur)
            cur = parent_of(by_id[cur]) if cur in keep else None
        if par is None or par not in keep or cur is not None:
            roots.append(rid)
        else:
            kids.setdefault(par, []).append(rid)
    out = []

    def walk(rid, depth, par):
        r = keep[rid]
        r["_depth"], r["_sub"] = depth, par
        out.append(r)
        for k in sorted(kids.get(rid, []), key=rank.__getitem__):
            walk(k, depth + 1, rid)
    for rid in sorted(roots, key=rank.__getitem__):
        walk(rid, 0, None)
    return out
