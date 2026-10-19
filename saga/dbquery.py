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
NOVALUE = {"is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month"}


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
        return isinstance(p, dict) and (js_seq(p.get("type"), "number") or js_seq(p.get("type"), "calc"))

    # Filter (matchFilter)
    def rel_date(self, op, iso):
        a = self.today
        if op == "this_month":
            d = date.fromordinal(a + 719163)
            a = _days_from_civil(d.year, d.month, 1)
            b = _days_from_civil(d.year + (d.month == 12), d.month % 12 + 1, 1)
        elif op == "this_week":
            a -= date.fromordinal(a + 719163).weekday()          # (getDay() + 6) % 7: Montag = 0
            b = a + 7
        else:
            b = a + 1
        x = _rel_date_day(js_str(iso) if js_truthy(iso) else "")
        return x is not None and a <= x < b

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
        if op == "today" or op == "this_week" or op == "this_month":
            return not empty and self.rel_date(op, v)
        if novalue:
            return True
        if op == "contains" or op == "not_contains":
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
        if js_seq(t, "multi_select") or js_seq(t, "relation"):
            return js_len(v if js_truthy(v) else [])
        if js_seq(t, "checkbox"):
            return 1 if js_truthy(v) else 0
        if js_nullish(v):
            return None
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
    def groups(self, rows, g):
        opts = [o for o in (g.get("options") if isinstance(g.get("options"), list) else []) if isinstance(o, dict)]
        known = {_key(o.get("id", UNDEF)) for o in opts} | _PROTO
        gid = g.get("id", UNDEF)
        out = [(None, [r for r in rows if _key(self._get(r, gid)) not in known])]
        for o in opts:
            oid = o.get("id", UNDEF)
            out.append((oid, [r for r in rows if js_seq(self._get(r, gid), oid)]))
        return out


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
        return p if isinstance(p, dict) and js_seq(p.get("type"), "select") else None
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
    if g is not None:
        gs = q.groups(out, g)
        groups = [{"id": None if oid is UNDEF else oid, "count": len(rs)} for oid, rs in gs]
        out = [r for _, rs in gs for r in rs]                # Reihenfolge wie angezeigt (gruppiert)
    off = args.get("offset", 0)
    out = out[off:off + args["limit"]] if "limit" in args else out[off:]
    return out, matched, groups
