"""Formel-Sprache fuer Datenbank-Eigenschaften (2.5, Paket 1 Etappe 1) - rein, ohne Datenbank (notes importiert das).

Notion-2.0-kompatible Teilmenge: Tokenizer + Pratt-Parser -> AST -> Python-Closures. KEIN eval/exec, kein ast-Modul,
keine regulaeren Ausdruecke aus der Formel (replace/replaceAll/contains arbeiten woertlich). Funktions- und
Methodenschreibweise (length(x) und x.length()) sind gleichwertig.

Werte beim Rechnen: Zahl (float), Text (str), Haekchen (bool), Datum (FDate), Liste (list), leer (None).
Ergebnistyp (rtype) wird beim Uebersetzen bestimmt: number | text | checkbox | date | list. Ausgabe in props:
Zahl (ganze Zahlen ohne ,0), Text, true/false, Datum "JJJJ-MM-TT" bzw. "JJJJ-MM-TTTHH:MM" (Ortszeit), Liste von Texten.

Grenzen (DoS): Ausdruck <= EXPR_MAX Zeichen, <= NODES_MAX Knoten, Verschachtelung <= DEPTH_MAX, Formel->Formel-Ketten
<= CHAIN_MAX (Zyklen werden erkannt), Text <= TEXT_MAX, Listen <= LIST_MAX, Schritte je Zeile <= STEPS_MAX, ^ nur mit
|Exponent| <= POW_MAX und endlichem Ergebnis. Laufzeitfehler -> FxError(code), der Wert der Zelle bleibt leer."""
import functools
import json
import math
import re
from datetime import date, datetime, timedelta, timezone

from saga.dbquery import js_num, js_num_str, js_round
from saga.i18n import t

EXPR_MAX = 2000
NODES_MAX = 400
DEPTH_MAX = 32
CHAIN_MAX = 8
TEXT_MAX = 10000
LIST_MAX = 1000
STEPS_MAX = 20000
POW_MAX = 1000
RTYPES = ("number", "text", "checkbox", "date", "list")
GROUPABLE = ("number", "text", "checkbox", "date")
# Fehlercodes je Zelle (fx_err) - die Clients uebersetzen sie selbst (siehe docs/APP-API.md)
CODES = ("syntax", "type", "num", "len", "budget", "time", "arg", "ref", "date", "cycle")


class FxError(Exception):
    """Laufzeitfehler einer Formel in einer Zeile -> Wert leer, fx_err[pid] = code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class FxSyntax(ValueError):
    """Fehler beim Uebersetzen (Syntax, Typen, Grenzen) mit Position im Ausdruck."""

    def __init__(self, msg, pos=0, code="syntax"):
        super().__init__(msg)
        self.msg, self.pos, self.code = msg, pos, code


# =====================================================================================================
# Datum
# =====================================================================================================
class FDate:
    """Zeitpunkt (aware, in der Zeitzone der Anfrage) oder reiner Tag (time=False, dt = Mitternacht)."""
    __slots__ = ("dt", "time")

    def __init__(self, dt, time):
        self.dt, self.time = dt, time

    def iso(self):
        return self.dt.strftime("%Y-%m-%dT%H:%M") if self.time else self.dt.date().isoformat()

    def key(self):
        return self.dt.timestamp()

    def __eq__(self, o):
        return isinstance(o, FDate) and self.dt == o.dt

    def __hash__(self):
        return hash(self.dt)


def _local(tz, y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=tz)


def day_of(s, tz):
    """'JJJJ-MM-TT' (gespeichertes Datum) -> FDate oder None."""
    if not isinstance(s, str) or len(s) < 10:
        return None
    try:
        d = date.fromisoformat(s[:10])
    except ValueError:
        return None
    return FDate(_local(tz, d.year, d.month, d.day), False)


def stamp_of(ts, tz):
    """Unix-Zeit (Sekunden) -> FDate mit Uhrzeit."""
    try:
        return FDate(datetime.fromtimestamp(float(ts), tz), True)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?)?\s*(Z|[+-]\d{2}:?\d{2})?")


def parse_date(s, tz):
    if not isinstance(s, str):
        raise FxError("type")
    m = _ISO.fullmatch(s.strip())
    if not m:
        raise FxError("date")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        if m.group(4) is None:
            return FDate(_local(tz, y, mo, d), False)
        hh, mi, ss = int(m.group(4)), int(m.group(5)), int(m.group(6) or 0)
        off = m.group(7)
        if off is None:
            return FDate(_local(tz, y, mo, d, hh, mi, ss), True)
        if off == "Z":
            z = timezone.utc
        else:
            sg = -1 if off[0] == "-" else 1
            o = off[1:].replace(":", "")
            z = timezone(sg * timedelta(hours=int(o[:2]), minutes=int(o[2:])))
        return FDate(datetime(y, mo, d, hh, mi, ss, tzinfo=z).astimezone(tz), True)
    except (ValueError, OverflowError):
        raise FxError("date")


# Namen fuer formatDate (Sprache der Anfrage). WEEKDAYS ist in static/pages.js gespiegelt (Gruppen nach Wochentag in
# fester Reihenfolge) - tests/formeln.py prueft, dass beide gleich sind.
WEEKDAYS = {
    "de": (("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"), ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")),
    "en": (("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"), ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")),
    "es": (("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"), ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")),
    "ru": (("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"), ("пн", "вт", "ср", "чт", "пт", "сб", "вс")),
    "th": (("วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"), ("จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา.")),
}
MONTHS = {
    "de": (("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"),
           ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")),
    "en": (("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
           ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")),
    "es": (("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"),
           ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")),
    "ru": (("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"),
           ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")),
    "th": (("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"),
           ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")),
}
WEEKDAY_INDEX = {}
for _lang, (_long, _short) in WEEKDAYS.items():
    for _i, _n in enumerate(_long + _short):
        WEEKDAY_INDEX.setdefault(_n, _i % 7)
_FMT = re.compile(r"\[[^\]]*\]|YYYY|YY|Q|MMMM|MMM|MM|M|Do|DD|D|dddd|ddd|dd|d|E|WW|W|HH|H|hh|h|mm|m|ss|s|A|a")


def format_date(fd, fmt, lang):
    """formatDate mit den Mustern von Notion (Moment.js): YYYY YY Q MMMM MMM MM M Do DD D dddd ddd dd d E W WW HH H hh h
    mm m ss s A a, [Text] woertlich. E = Wochentag 1 (Montag) bis 7 (Sonntag), d = 0 (Sonntag) bis 6."""
    dt = fd.dt
    wl, ws = WEEKDAYS.get(lang, WEEKDAYS["en"])
    ml, ms = MONTHS.get(lang, MONTHS["en"])
    wd = dt.isoweekday()

    def rep(m):
        k = m.group(0)
        if k[0] == "[":
            return k[1:-1]
        h12 = (dt.hour + 11) % 12 + 1
        if k == "Do":
            if lang == "en":
                n = dt.day
                suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
                return f"{n}{suf}"
            return f"{dt.day}."
        return {
            "YYYY": f"{dt.year:04d}", "YY": f"{dt.year % 100:02d}", "Q": str((dt.month - 1) // 3 + 1),
            "MMMM": ml[dt.month - 1], "MMM": ms[dt.month - 1], "MM": f"{dt.month:02d}", "M": str(dt.month),
            "DD": f"{dt.day:02d}", "D": str(dt.day), "dddd": wl[wd - 1], "ddd": ws[wd - 1], "dd": ws[wd - 1],
            "d": str(wd % 7), "E": str(wd), "WW": f"{dt.isocalendar()[1]:02d}", "W": str(dt.isocalendar()[1]),
            "HH": f"{dt.hour:02d}", "H": str(dt.hour), "hh": f"{h12:02d}", "h": str(h12),
            "mm": f"{dt.minute:02d}", "m": str(dt.minute), "ss": f"{dt.second:02d}", "s": str(dt.second),
            "A": "AM" if dt.hour < 12 else "PM", "a": "am" if dt.hour < 12 else "pm"}[k]
    return _FMT.sub(rep, fmt)


_UNITS = {"year": "years", "years": "years", "quarter": "quarters", "quarters": "quarters", "month": "months",
          "months": "months", "week": "weeks", "weeks": "weeks", "day": "days", "days": "days", "hour": "hours",
          "hours": "hours", "minute": "minutes", "minutes": "minutes", "second": "seconds", "seconds": "seconds"}


def _add_months(fd, n):
    dt = fd.dt
    y, m = divmod(dt.month - 1 + n, 12)
    y += dt.year
    if not 1 <= y <= 9999:
        raise FxError("date")
    last = (date(y + (m == 11), (m + 1) % 12 + 1, 1) - timedelta(days=1)).day
    return FDate(datetime(y, m + 1, min(dt.day, last), dt.hour, dt.minute, dt.second, tzinfo=dt.tzinfo), fd.time)


def date_add(fd, n, unit, tz):
    u = _UNITS.get(unit if isinstance(unit, str) else "")
    if u is None:
        raise FxError("arg")
    if n != n or math.isinf(n) or abs(n) > 1e6:
        raise FxError("arg")
    n = int(n)
    try:
        if u in ("years", "quarters", "months"):
            return _add_months(fd, n * {"years": 12, "quarters": 3, "months": 1}[u])
        if u in ("weeks", "days"):
            dt = fd.dt
            nd = date(dt.year, dt.month, dt.day) + timedelta(days=n * (7 if u == "weeks" else 1))
            return FDate(datetime(nd.year, nd.month, nd.day, dt.hour, dt.minute, dt.second, tzinfo=dt.tzinfo), fd.time)
        sec = n * {"hours": 3600, "minutes": 60, "seconds": 1}[u]
        return FDate((fd.dt.astimezone(timezone.utc) + timedelta(seconds=sec)).astimezone(tz), True)
    except (ValueError, OverflowError):
        raise FxError("date")


def date_between(a, b, unit):
    """a - b in der Einheit, Richtung null abgeschnitten (wie Notion)."""
    u = _UNITS.get(unit if isinstance(unit, str) else "")
    if u is None:
        raise FxError("arg")
    if u in ("years", "quarters", "months"):
        x, y = a.dt, b.dt.astimezone(a.dt.tzinfo)
        n = (x.year - y.year) * 12 + (x.month - y.month)
        rest_x, rest_y = (x.day, x.hour, x.minute, x.second), (y.day, y.hour, y.minute, y.second)
        if n > 0 and rest_x < rest_y:
            n -= 1
        elif n < 0 and rest_x > rest_y:
            n += 1
        return float(math.trunc(n / {"years": 12, "quarters": 3, "months": 1}[u]))
    if not a.time and not b.time:
        sec = (a.dt.date() - b.dt.date()).days * 86400
    else:
        sec = a.dt.timestamp() - b.dt.timestamp()
    return float(math.trunc(sec / {"weeks": 604800, "days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}[u]))


# =====================================================================================================
# Werte-Hilfen
# =====================================================================================================
def _isnum(v):
    return isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool))


def fin(x):
    x = float(x)
    if x != x or math.isinf(x):
        raise FxError("num")
    return x


def txt(s):
    if len(s) > TEXT_MAX:
        raise FxError("len")
    return s


def lst(v):
    if len(v) > LIST_MAX:
        raise FxError("len")
    return v


def fmt(v):
    """format(x): Text eines Werts."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if _isnum(v):
        return js_num_str(v)
    if isinstance(v, str):
        return v
    if isinstance(v, FDate):
        return v.dt.strftime("%Y-%m-%d %H:%M") if v.time else v.dt.date().isoformat()
    if isinstance(v, list):
        return txt(", ".join(fmt(x) for x in v))
    return ""


def truthy(v):
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if _isnum(v):
        return v == v and v != 0
    if isinstance(v, (str, list)):
        return len(v) > 0
    return True


def is_empty(v):
    return v is None or v is False or v == "" or (isinstance(v, list) and not v) or (_isnum(v) and v == 0)


def equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _isnum(a) and _isnum(b):
        return float(a) == float(b)
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, FDate) and isinstance(b, FDate):
        return a.key() == b.key()
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return False


def _u16(s):
    return s.encode("utf-16-be", "surrogatepass")


def less(a, b):
    """a < b fuer gleiche Typen; None ist nie kleiner/groesser. Verschiedene Typen: Typfehler."""
    if a is None or b is None:
        return None
    if _isnum(a) and _isnum(b) and not isinstance(a, bool) and not isinstance(b, bool):
        return float(a) < float(b)
    if isinstance(a, str) and isinstance(b, str):
        return _u16(a) < _u16(b)
    if isinstance(a, FDate) and isinstance(b, FDate):
        return a.key() < b.key()
    if isinstance(a, bool) and isinstance(b, bool):
        return a < b
    raise FxError("type")


def num(v):
    """Zahl erwartet: None bleibt None, sonst Typfehler."""
    if v is None or _isnum(v):
        return v if v is None else float(v)
    raise FxError("type")


def text(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    raise FxError("type")


def dt_(v):
    if v is None or isinstance(v, FDate):
        return v
    raise FxError("type")


def lst_(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    raise FxError("type")


def _int(v, lo=-1e9, hi=1e9):
    v = num(v)
    if v is None:
        return None
    if not lo <= v <= hi:
        raise FxError("arg")
    return int(v)


def to_number(v):
    if v is None or _isnum(v):
        return None if v is None else float(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, FDate):
        return v.dt.timestamp() * 1000
    if isinstance(v, str):
        x = js_num(v) if v.strip() else math.nan
        return None if x != x or math.isinf(x) else x
    raise FxError("type")


def _nums(args):
    out = []
    for a in args:
        for x in (a if isinstance(a, list) else [a]):
            if x is None:
                continue
            if isinstance(x, bool) or not _isnum(x):
                raise FxError("type")
            out.append(float(x))
    return out


# =====================================================================================================
# Tokenizer + Pratt-Parser
# =====================================================================================================
_TOK = re.compile(r"""(?P<ws>\s+)
 |(?P<num>(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)
 |(?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|“(?:\\.|[^”\\])*”)
 |(?P<id>[A-Za-z_][A-Za-z0-9_]*)
 |(?P<op>==|!=|<=|>=|&&|\|\||≠|≤|≥|[-+*/%^<>!?:(),.\[\]=])""", re.X)
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}


def _unquote(s):
    body = s[1:-1]
    return re.sub(r"\\(.)", lambda m: _ESC.get(m.group(1), m.group(1)), body)


def quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def tokenize(src):
    out, i = [], 0
    while i < len(src):
        m = _TOK.match(src, i)
        if not m:
            ch = src[i]
            if ch in "\"'“":
                raise FxSyntax(t("Text ohne schließendes Anführungszeichen"), i)
            raise FxSyntax(t("Unerwartetes Zeichen „{c}“", c=ch), i)
        k = m.lastgroup
        if k != "ws":
            v = m.group(0)
            if k == "op":
                v = {"≠": "!=", "≤": "<=", "≥": ">=", "=": "=="}.get(v, v)
            out.append((k, v, i, m.end()))
        i = m.end()
    out.append(("end", "", len(src), len(src)))
    return out


_BP = {"?": 1, "or": 2, "||": 2, "and": 3, "&&": 3, "==": 4, "!=": 4, "<": 5, "<=": 5, ">": 5, ">=": 5,
       "+": 6, "-": 6, "*": 7, "/": 7, "%": 7, "^": 9, ".": 11}
UNSUPPORTED = {"map", "filter", "find", "findIndex", "some", "every", "let", "lets", "test", "match", "current", "index",
               "dateRange", "name", "email"}


class _Parser:
    def __init__(self, src):
        self.toks = tokenize(src)
        self.i = 0
        self.nodes = 0
        self.depth = 0

    def peek(self):
        return self.toks[self.i]

    def next(self):
        tk = self.toks[self.i]
        self.i += 1
        return tk

    def expect(self, v):
        tk = self.next()
        if tk[1] != v or tk[0] == "str":
            raise FxSyntax(t("„{v}“ erwartet", v=v), tk[2])
        return tk

    def node(self, *a):
        self.nodes += 1
        if self.nodes > NODES_MAX:
            raise FxSyntax(t("Formel zu groß (höchstens {n} Bausteine)", n=NODES_MAX), a[1] if len(a) > 1 else 0)
        return a

    def lbp(self, tk):
        if tk[0] == "op" or (tk[0] == "id" and tk[1] in ("and", "or")):
            return _BP.get(tk[1], 0)
        return 0

    def expr(self, rbp=0):
        self.depth += 1
        if self.depth > DEPTH_MAX:
            raise FxSyntax(t("Formel zu tief verschachtelt (höchstens {n} Ebenen)", n=DEPTH_MAX), self.peek()[2])
        left = self.nud(self.next())
        while rbp < self.lbp(self.peek()):
            left = self.led(self.next(), left)
        self.depth -= 1
        return left

    def args(self, close=")"):
        out = []
        if self.peek()[1] == close and self.peek()[0] == "op":
            self.next()
            return out
        while True:
            out.append(self.expr(0))
            tk = self.next()
            if tk[1] == close and tk[0] == "op":
                return out
            if tk[1] != "," or tk[0] != "op":
                raise FxSyntax(t("„,“ oder „{v}“ erwartet", v=close), tk[2])

    def nud(self, tk):
        k, v, pos = tk[0], tk[1], tk[2]
        if k == "num":
            return self.node("num", pos, float(v))
        if k == "str":
            return self.node("str", pos, _unquote(v))
        if k == "id":
            if v in ("true", "false"):
                return self.node("bool", pos, v == "true")
            if v == "not":
                return self.node("not", pos, self.expr(8))
            if self.peek()[1] == "(" and self.peek()[0] == "op":
                self.next()
                if v == "prop":
                    a = self.next()
                    if a[0] != "str":
                        raise FxSyntax(t("prop() braucht den Namen einer Eigenschaft in Anführungszeichen"), a[2])
                    self.expect(")")
                    return self.node("prop", pos, _unquote(a[1]), a[2], a[3])
                return self.node("call", pos, v, self.args())
            if v in ("pi", "e"):
                return self.node("call", pos, v, [])
            raise FxSyntax(t("Unbekannter Name „{v}“", v=v), pos)
        if k == "op":
            if v == "(":
                e = self.expr(0)
                self.expect(")")
                return e
            if v == "[":
                return self.node("list", pos, self.args("]"))
            if v in ("-", "+"):
                return self.node("neg" if v == "-" else "pos", pos, self.expr(8))
            if v == "!":
                return self.node("not", pos, self.expr(8))
        if k == "end":
            raise FxSyntax(t("Formel endet unerwartet"), pos)
        raise FxSyntax(t("Unerwartetes „{v}“", v=v), pos)

    def led(self, tk, left):
        v, pos = tk[1], tk[2]
        if v == "?":
            a = self.expr(0)
            self.expect(":")
            b = self.expr(0)
            return self.node("call", pos, "if", [left, a, b])
        if v == ".":
            nm = self.next()
            if nm[0] != "id":
                raise FxSyntax(t("Name einer Funktion erwartet"), nm[2])
            self.expect("(")
            return self.node("call", nm[2], nm[1], [left] + self.args())
        if v == "^":
            return self.node("bin", pos, v, left, self.expr(_BP[v] - 1))      # rechtsassoziativ
        op = {"&&": "and", "||": "or"}.get(v, v)
        return self.node("bin", pos, op, left, self.expr(_BP[v]))


def parse(src):
    if not isinstance(src, str):
        raise FxSyntax(t("Formel fehlt"), 0)
    if len(src) > EXPR_MAX:
        raise FxSyntax(t("Formel zu lang (höchstens {n} Zeichen)", n=EXPR_MAX), EXPR_MAX)
    p = _Parser(src)
    if p.peek()[0] == "end":
        return None, p.nodes
    tree = p.expr(0)
    if p.peek()[0] != "end":
        raise FxSyntax(t("Unerwartetes „{v}“", v=p.peek()[1]), p.peek()[2])
    return tree, p.nodes


def rewrite_props(src, fn):
    """prop("x") -> prop(fn(x)) im Text (Rest der Formel bleibt byte-gleich). fn(x) -> neuer Name oder None (lassen)."""
    toks = tokenize(src)
    out, last = [], 0
    for i in range(len(toks) - 3):
        a, b, c = toks[i], toks[i + 1], toks[i + 2]
        if a[0] == "id" and a[1] == "prop" and b[1] == "(" and b[0] == "op" and c[0] == "str":
            if i > 0 and toks[i - 1][1] == "." and toks[i - 1][0] == "op":
                continue
            nv = fn(_unquote(c[1]))
            if nv is not None:
                out.append(src[last:c[2]])
                out.append(quote(nv))
                last = c[3]
    out.append(src[last:])
    return "".join(out)


# =====================================================================================================
# Uebersetzen: Typen + Closures
# =====================================================================================================
N, S, B, D, A = "number", "text", "checkbox", "date", "any"


def L(e=A):
    return "list:" + e


def is_list(ty):
    return ty.startswith("list")


def elem(ty):
    return ty[5:] if ty.startswith("list:") else A


def unify(a, b):
    if a == b:
        return a
    if is_list(a) and is_list(b):
        return L(unify(elem(a), elem(b)))
    return A


def rtype_of(ty):
    if is_list(ty):
        return "list"
    return ty if ty in (N, S, B, D) else S


PROP_TYPE = {"text": S, "url": S, "select": S, "number": N, "calc": N, "checkbox": B, "date": D, "created": D,
             "edited": D, "multi_select": L(S), "relation": L(S)}


class Ctx:
    """Zustand einer Auswertung (eine Zeile): Werte der Eigenschaften, Zeitzone, jetzt, Schrittzaehler."""
    __slots__ = ("vals", "tz", "now", "steps", "lang", "row_id")

    def __init__(self, vals, tz, now, lang="en", row_id=None):
        self.vals, self.tz, self.now, self.steps, self.lang, self.row_id = vals, tz, now, 0, lang, row_id


def _step(ctx, n=1):
    ctx.steps += n
    if ctx.steps > STEPS_MAX:
        raise FxError("budget")


def _ok(ty, kinds):
    """Passt der statische Typ ty zu einer der erwarteten Arten (n s b d l x)?"""
    if ty == A or "x" in kinds:
        return True
    k = "l" if is_list(ty) else {N: "n", S: "s", B: "b", D: "d"}[ty]
    return k in kinds


# Funktionstabelle: name -> (min, max, [arten je Argument, letzte gilt fuer den Rest], Ergebnis)
# Ergebnis: Typ oder Funktion(argtypen) -> Typ
def _first_list_elem(ts):
    return elem(ts[0]) if ts and is_list(ts[0]) else (S if ts and ts[0] == S else A)


FUNCS = {
    "abs": (1, 1, ["n"], N), "floor": (1, 1, ["n"], N), "ceil": (1, 1, ["n"], N), "round": (1, 2, ["n"], N),
    "sqrt": (1, 1, ["n"], N), "cbrt": (1, 1, ["n"], N), "pow": (2, 2, ["n"], N), "mod": (2, 2, ["n"], N),
    "sign": (1, 1, ["n"], N), "exp": (1, 1, ["n"], N), "ln": (1, 1, ["n"], N), "log10": (1, 1, ["n"], N),
    "log2": (1, 1, ["n"], N), "pi": (0, 0, [], N), "e": (0, 0, [], N), "toNumber": (1, 1, ["x"], N),
    "min": (1, 50, ["nl"], N), "max": (1, 50, ["nl"], N), "sum": (1, 50, ["nl"], N), "mean": (1, 50, ["nl"], N),
    "median": (1, 50, ["nl"], N), "add": (2, 2, ["x"], lambda ts: S if S in ts else (N if ts == [N, N] else A)),
    "subtract": (2, 2, ["n"], N), "multiply": (2, 2, ["n"], N), "divide": (2, 2, ["n"], N),
    "unaryMinus": (1, 1, ["n"], N), "unaryPlus": (1, 1, ["x"], N),
    "equal": (2, 2, ["x"], B), "unequal": (2, 2, ["x"], B), "larger": (2, 2, ["x"], B), "largerEq": (2, 2, ["x"], B),
    "smaller": (2, 2, ["x"], B), "smallerEq": (2, 2, ["x"], B),
    "empty": (1, 1, ["x"], B), "not": (1, 1, ["x"], B), "and": (1, 50, ["x"], B), "or": (1, 50, ["x"], B),
    "if": (3, 3, ["x"], lambda ts: unify(ts[1], ts[2])), "ifs": (2, 51, ["x"], None),
    "length": (1, 1, ["sl"], N), "lower": (1, 1, ["s"], S), "upper": (1, 1, ["s"], S), "trim": (1, 1, ["s"], S),
    "contains": (2, 2, ["slx", "x"], B), "includes": (2, 2, ["l", "x"], B), "startsWith": (2, 2, ["s"], B),
    "endsWith": (2, 2, ["s"], B), "replace": (3, 3, ["s"], S), "replaceAll": (3, 3, ["s"], S),
    "substring": (2, 3, ["s", "n"], S), "slice": (2, 3, ["sl", "n"], lambda ts: ts[0] if ts[0] != A else A),
    "split": (2, 2, ["s"], L(S)), "repeat": (2, 2, ["s", "n"], S), "padStart": (2, 3, ["s", "n", "s"], S),
    "padEnd": (2, 3, ["s", "n", "s"], S), "format": (1, 1, ["x"], S), "style": (1, 20, ["x", "s"], S),
    "unstyle": (1, 1, ["x"], S), "link": (2, 2, ["s"], S),
    "join": (2, 2, ["l", "s"], S), "concat": (1, 50, ["x"], lambda ts: L(functools.reduce(unify, [elem(x) for x in ts]))
                                                if all(is_list(x) for x in ts) else S),
    "at": (2, 2, ["sl", "n"], lambda ts: S if ts[0] == S else elem(ts[0])),
    "first": (1, 1, ["l"], lambda ts: elem(ts[0])), "last": (1, 1, ["l"], lambda ts: elem(ts[0])),
    "sort": (1, 1, ["l"], lambda ts: ts[0] if is_list(ts[0]) else L()), "reverse": (1, 1, ["l"], lambda ts: ts[0] if is_list(ts[0]) else L()),
    "unique": (1, 1, ["l"], lambda ts: ts[0] if is_list(ts[0]) else L()), "flat": (1, 1, ["l"], lambda ts: L()),
    "now": (0, 0, [], D), "today": (0, 0, [], D), "date": (1, 3, ["dn", "n"], lambda ts: N if len(ts) == 1 else D),
    "parseDate": (1, 1, ["s"], D), "formatDate": (2, 2, ["d", "s"], S), "dateAdd": (3, 3, ["d", "n", "s"], D),
    "dateSubtract": (3, 3, ["d", "n", "s"], D), "dateBetween": (3, 3, ["d", "d", "s"], N), "timestamp": (1, 1, ["d"], N),
    "fromTimestamp": (1, 1, ["n"], D), "year": (1, 1, ["d"], N), "month": (1, 1, ["d"], N), "week": (1, 1, ["d"], N),
    "day": (1, 1, ["d"], N), "weekday": (1, 1, ["d"], N), "hour": (1, 1, ["d"], N), "minute": (1, 1, ["d"], N),
    "dateStart": (1, 1, ["d"], D), "dateEnd": (1, 1, ["d"], D), "id": (0, 0, [], S),
}
FUNC_NAMES = sorted(FUNCS)


def _f_round(x, d=0.0):
    x = num(x)
    if x is None:
        return None
    d = _int(d if d is not None else 0.0, -15, 15)
    f = 10.0 ** d
    return fin(js_round(x * f) / f)


def _mathfn(f, ok=lambda x: True):
    def g(x):
        x = num(x)
        if x is None:
            return None
        if not ok(x):
            raise FxError("num")
        try:
            return fin(f(x))
        except (ValueError, OverflowError):
            raise FxError("num")
    return g


def _pow(a, b):
    a, b = num(a), num(b)
    if a is None or b is None:
        return None
    if abs(b) > POW_MAX:
        raise FxError("num")
    try:
        return fin(math.pow(a, b))
    except (ValueError, OverflowError, ZeroDivisionError):
        raise FxError("num")


def _mod(a, b):
    a, b = num(a), num(b)
    if a is None or b is None:
        return None
    if b == 0:
        raise FxError("num")
    return fin(math.fmod(a, b))


def _div(a, b):
    a, b = num(a), num(b)
    if a is None or b is None:
        return None
    if b == 0:
        raise FxError("num")
    return fin(a / b)


def _plus(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return txt(fmt(a) + fmt(b))
    a, b = num(a), num(b)
    if a is None or b is None:
        return None
    return fin(a + b)


def _arith(op):
    def g(a, b):
        a, b = num(a), num(b)
        if a is None or b is None:
            return None
        return fin(a - b if op == "-" else a * b)
    return g


def _median(*a):
    xs = sorted(_nums(a))
    if not xs:
        return None
    m = len(xs) // 2
    return fin(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2)


def _agg(f):
    def g(*a):
        xs = _nums(a)
        return fin(f(xs)) if xs else None
    return g


def _contains(a, b):
    if isinstance(a, list):
        return any(equal(x, b) for x in a)
    return fmt(b) in fmt(a)


def _replace(s, a, b, all_=False):
    s, a, b = text(s), text(a), text(b)
    n = (s.count(a) if a else len(s) + 1) if all_ else (1 if a in s else 0)
    if len(s) + n * (len(b) - len(a)) > TEXT_MAX:
        raise FxError("len")
    return s.replace(a, b) if all_ else s.replace(a, b, 1)


def _substring(s, a, b=None):
    s = text(s)
    n = len(s)
    a = 0 if a is None else max(0, min(_int(a), n))
    b = n if b is None else max(0, min(_int(b), n))
    if a > b:
        a, b = b, a
    return s[a:b]


def _slice(v, a, b=None):
    if v is None:
        return None
    if not isinstance(v, (str, list)):
        raise FxError("type")
    n = len(v)

    def norm(i, dflt):
        if i is None:
            return dflt
        i = _int(i)
        return max(0, n + i) if i < 0 else min(i, n)
    return v[norm(a, 0):norm(b, n)]


def _split(s, sep):
    s, sep = text(s), text(sep)
    out = list(s) if sep == "" else s.split(sep)
    return lst(out)


def _repeat(s, n):
    s, n = text(s), _int(n, 0, 1e9)
    if n is None:
        return None
    if len(s) * n > TEXT_MAX:
        raise FxError("len")
    return s * n


def _pad(start):
    def g(s, n, p=" "):
        s, n = fmt(s), _int(n, 0, TEXT_MAX)
        p = text(p) if p is not None else " "
        if n is None or len(s) >= n or not p:
            return s
        fill = (p * (n // len(p) + 1))[:n - len(s)]
        return fill + s if start else s + fill
    return g


def _link(label, url):
    label, url = text(label), text(url).strip()
    if not re.match(r"(?i)(https?://|mailto:)", url):
        raise FxError("arg")
    return label or url


def _length(v):
    if v is None:
        return 0.0
    if isinstance(v, (str, list)):
        return float(len(v))
    raise FxError("type")


def _at(v, i):
    v = lst_(v) if not isinstance(v, str) else v
    i = _int(i)
    if i is None:
        return None
    if i < 0:
        i += len(v)
    return v[i] if 0 <= i < len(v) else None


def _sortkey(v):
    if _isnum(v):
        return (0, float(v), b"")
    if isinstance(v, FDate):
        return (1, v.key(), b"")
    return (2, 0.0, _u16(fmt(v)))


def _unique(v):
    out = []
    for x in lst_(v):
        if not any(equal(x, y) for y in out):
            out.append(x)
    return out


def _flat(v):
    out = []
    for x in lst_(v):
        if isinstance(x, list):
            out.extend(x)
        else:
            out.append(x)
    return lst(out)


def _concat(*a):
    if a and all(isinstance(x, list) or x is None for x in a) and any(isinstance(x, list) for x in a):
        out = []
        for x in a:
            out.extend(x or [])
        return lst(out)
    return txt("".join(fmt(x) for x in a))


def _join(v, sep):
    return txt(text(sep).join(fmt(x) for x in lst_(v)))


def _cmp(f):
    def g(a, b):
        r = f(a, b)
        return False if r is None else r
    return g


def _ge(a, b):
    r = less(a, b)
    return None if r is None else not r


def _gt(a, b):
    return less(b, a)


def _le(a, b):
    r = less(b, a)
    return None if r is None else not r


def _dpart(f):
    def g(d):
        d = dt_(d)
        return None if d is None else float(f(d.dt))
    return g


# Funktionen mit Kontext (Zeitzone/jetzt) bekommen ctx als erstes Argument
CTX_FUNCS = {
    "now": lambda ctx: FDate(ctx.now.astimezone(ctx.tz), True),
    "today": lambda ctx: (lambda n: FDate(_local(ctx.tz, n.year, n.month, n.day), False))(ctx.now.astimezone(ctx.tz)),
    "parseDate": lambda ctx, s: None if s is None or s == "" else parse_date(text(s), ctx.tz),
    "formatDate": lambda ctx, d, f: None if dt_(d) is None else txt(format_date(d, text(f)[:100], ctx.lang)),
    "dateAdd": lambda ctx, d, n, u: None if dt_(d) is None or num(n) is None else date_add(d, num(n), u, ctx.tz),
    "dateSubtract": lambda ctx, d, n, u: None if dt_(d) is None or num(n) is None else date_add(d, -num(n), u, ctx.tz),
    "fromTimestamp": lambda ctx, ms: None if num(ms) is None else (stamp_of(num(ms) / 1000, ctx.tz) or _raise("date")),
    "date": lambda ctx, *a: _date_fn(ctx, *a),
    "id": lambda ctx: str(ctx.row_id or ""),
}


def _raise(code):
    raise FxError(code)


def _date_fn(ctx, *a):
    if len(a) == 1:                                       # Notion: date(datum) = Tag im Monat
        d = dt_(a[0])
        return None if d is None else float(d.dt.day)
    if len(a) != 3:
        raise FxError("arg")
    y, m, d = (_int(x, -1e6, 1e6) for x in a)
    if None in (y, m, d):
        return None
    try:
        return FDate(_local(ctx.tz, y, m, d), False)
    except (ValueError, OverflowError):
        raise FxError("date")


IMPL = {
    "abs": _mathfn(abs), "floor": _mathfn(math.floor), "ceil": _mathfn(math.ceil), "round": _f_round,
    "sqrt": _mathfn(math.sqrt, lambda x: x >= 0), "cbrt": _mathfn(lambda x: math.copysign(abs(x) ** (1 / 3), x)),
    "pow": _pow, "mod": _mod, "sign": _mathfn(lambda x: (x > 0) - (x < 0)), "exp": _mathfn(math.exp),
    "ln": _mathfn(math.log, lambda x: x > 0), "log10": _mathfn(math.log10, lambda x: x > 0),
    "log2": _mathfn(math.log2, lambda x: x > 0), "pi": lambda: math.pi, "e": lambda: math.e, "toNumber": to_number,
    "min": _agg(min), "max": _agg(max), "sum": lambda *a: fin(sum(_nums(a))), "mean": _agg(lambda xs: sum(xs) / len(xs)),
    "median": _median, "add": _plus, "subtract": _arith("-"), "multiply": _arith("*"), "divide": _div,
    "unaryMinus": lambda x: None if num(x) is None else -num(x), "unaryPlus": to_number,
    "equal": equal, "unequal": lambda a, b: not equal(a, b), "larger": _cmp(_gt), "largerEq": _cmp(_ge),
    "smaller": _cmp(less), "smallerEq": _cmp(_le), "empty": is_empty, "not": lambda x: not truthy(x),
    "length": _length, "lower": lambda s: text(s).lower(), "upper": lambda s: txt(text(s).upper()),
    "trim": lambda s: text(s).strip(), "contains": _contains, "includes": lambda a, b: any(equal(x, b) for x in lst_(a)),
    "startsWith": lambda a, b: text(a).startswith(text(b)), "endsWith": lambda a, b: text(a).endswith(text(b)),
    "replace": lambda s, a, b: _replace(s, a, b), "replaceAll": lambda s, a, b: _replace(s, a, b, True),
    "substring": _substring, "slice": _slice, "split": _split, "repeat": _repeat, "padStart": _pad(True),
    "padEnd": _pad(False), "format": lambda x: fmt(x), "style": lambda x, *a: fmt(x), "unstyle": lambda x: fmt(x),
    "link": _link, "join": _join, "concat": _concat, "at": _at,
    "first": lambda v: (lst_(v) or [None])[0], "last": lambda v: (lst_(v) or [None])[-1],
    "sort": lambda v: sorted(lst_(v), key=_sortkey), "reverse": lambda v: list(reversed(lst_(v))), "unique": _unique,
    "flat": _flat, "dateBetween": lambda a, b, u: None if dt_(a) is None or dt_(b) is None else date_between(a, b, u),
    "timestamp": lambda d: None if dt_(d) is None else d.dt.timestamp() * 1000, "year": _dpart(lambda x: x.year),
    "month": _dpart(lambda x: x.month), "week": _dpart(lambda x: x.isocalendar()[1]),
    "day": _dpart(lambda x: x.isoweekday()), "weekday": _dpart(lambda x: x.isoweekday()), "hour": _dpart(lambda x: x.hour),
    "minute": _dpart(lambda x: x.minute), "dateStart": dt_, "dateEnd": dt_,
}


class _Compiler:
    def __init__(self, ptypes, resolve):
        self.ptypes = ptypes          # pid -> statischer Typ
        self.resolve = resolve        # name aus prop("...") -> pid oder None
        self.refs = set()

    def c(self, n):
        k, pos = n[0], n[1]
        if k == "num":
            v = n[2]
            if v != v or math.isinf(v):
                raise FxSyntax(t("Zahl zu groß"), pos)
            return (lambda ctx, v=v: v), N
        if k == "str":
            if len(n[2]) > TEXT_MAX:
                raise FxSyntax(t("Text zu lang"), pos)
            return (lambda ctx, v=n[2]: v), S
        if k == "bool":
            return (lambda ctx, v=n[2]: v), B
        if k == "list":
            fs = [self.c(x) for x in n[2]]
            ty = L(functools.reduce(unify, [x[1] for x in fs]) if fs else A)
            fns = [f for f, _ in fs]

            def mk(ctx, fns=fns):
                _step(ctx, len(fns))
                return [f(ctx) for f in fns]
            return mk, ty
        if k == "prop":
            pid = self.resolve(n[2])
            if pid is None:
                raise FxSyntax(t("Eigenschaft „{name}“ gibt es nicht", name=n[2]), n[3], "ref")
            self.refs.add(pid)
            ty = self.ptypes.get(pid, A)
            return (lambda ctx, pid=pid: ctx.vals.get(pid)), ty
        if k == "neg" or k == "pos":
            f, ty = self.c(n[2])
            self._want(ty, "n", n[2][1])
            if k == "pos":
                return (lambda ctx: num(f(ctx))), N
            return (lambda ctx: (lambda x: None if x is None else -x)(num(f(ctx)))), N
        if k == "not":
            f, _ = self.c(n[2])
            return (lambda ctx: not truthy(f(ctx))), B
        if k == "bin":
            return self.bin(n)
        if k == "call":
            return self.call(n)
        raise FxSyntax(t("Formel fehlerhaft"), pos)

    def _want(self, ty, kinds, pos):
        if not _ok(ty, kinds):
            raise FxSyntax(t("Typ passt nicht: {ty} statt {want}", ty=_tname(ty), want=_kname(kinds)), pos, "type")

    def bin(self, n):
        op, pos = n[2], n[1]
        fa, ta = self.c(n[3])
        fb, tb = self.c(n[4])
        if op in ("and", "or"):
            if op == "and":
                return (lambda ctx: truthy(fa(ctx)) and truthy(fb(ctx))), B
            return (lambda ctx: truthy(fa(ctx)) or truthy(fb(ctx))), B
        if op == "==":
            return (lambda ctx: equal(fa(ctx), fb(ctx))), B
        if op == "!=":
            return (lambda ctx: not equal(fa(ctx), fb(ctx))), B
        if op in ("<", "<=", ">", ">="):
            if ta != A and tb != A and ta != tb:
                raise FxSyntax(t("Vergleich verschiedener Typen: {a} und {b}", a=_tname(ta), b=_tname(tb)), pos, "type")
            g = {"<": _cmp(less), "<=": _cmp(_le), ">": _cmp(_gt), ">=": _cmp(_ge)}[op]
            return (lambda ctx: g(fa(ctx), fb(ctx))), B
        if op == "+":
            if ta == S or tb == S:
                ty = S
            elif ta == N and tb == N:
                ty = N
            else:
                for x, p in ((ta, n[3][1]), (tb, n[4][1])):
                    self._want(x, "ns", p)
                ty = A
            return (lambda ctx: _plus(fa(ctx), fb(ctx))), ty
        self._want(ta, "n", n[3][1])
        self._want(tb, "n", n[4][1])
        g = {"-": _arith("-"), "*": _arith("*"), "/": _div, "%": _mod, "^": _pow}[op]
        return (lambda ctx: g(fa(ctx), fb(ctx))), N

    def call(self, n):
        pos, name, args = n[1], n[2], n[3]
        if name in UNSUPPORTED:
            raise FxSyntax(t("„{f}“ kommt in einer späteren Version", f=name), pos)
        spec = FUNCS.get(name)
        if spec is None:
            raise FxSyntax(t("Unbekannte Funktion „{f}“", f=name), pos)
        lo, hi, kinds, ret = spec
        if not lo <= len(args) <= hi:
            raise FxSyntax(t("„{f}“ braucht {a} bis {b} Argumente", f=name, a=lo, b=hi) if lo != hi else
                           t("„{f}“ braucht {a} Argumente", f=name, a=lo), pos)
        cs = [self.c(a) for a in args]
        tys = [ty for _, ty in cs]
        for i, ty in enumerate(tys):
            if kinds:
                self._want(ty, kinds[min(i, len(kinds) - 1)], args[i][1])
        fns = [f for f, _ in cs]
        if name == "if":
            c, a, b = fns
            return (lambda ctx: a(ctx) if truthy(c(ctx)) else b(ctx)), ret(tys)
        if name == "ifs":
            pairs = [(fns[i], fns[i + 1]) for i in range(0, len(fns) - 1, 2)]
            dflt = fns[-1] if len(fns) % 2 else None
            vt = [tys[i + 1] for i in range(0, len(tys) - 1, 2)] + ([tys[-1]] if dflt else [])
            ty = functools.reduce(unify, vt)

            def ifs(ctx):
                for c, v in pairs:
                    if truthy(c(ctx)):
                        return v(ctx)
                return dflt(ctx) if dflt else None
            return ifs, ty
        if name in ("and", "or"):
            if name == "and":
                return (lambda ctx: all(truthy(f(ctx)) for f in fns)), B
            return (lambda ctx: any(truthy(f(ctx)) for f in fns)), B
        if name == "date" and len(args) == 1 and tys[0] == N:
            raise FxSyntax(t("date() braucht ein Datum oder Jahr, Monat, Tag"), pos, "type")
        ty = ret(tys) if callable(ret) else ret
        if name in CTX_FUNCS:
            g = CTX_FUNCS[name]

            def callc(ctx, g=g, fns=fns):
                _step(ctx)
                return g(ctx, *[f(ctx) for f in fns])
            return callc, ty
        g = IMPL[name]
        heavy = name in ("sort", "unique", "join", "concat", "flat", "includes", "contains", "split", "repeat", "replaceAll",
                         "sum", "min", "max", "mean", "median")

        def call(ctx, g=g, fns=fns):
            vals = [f(ctx) for f in fns]
            if heavy:
                _step(ctx, 1 + sum(len(v) if isinstance(v, (list, str)) else 0 for v in vals))
            else:
                _step(ctx)
            return g(*vals)
        return call, ty


def _tname(ty):
    if is_list(ty):
        return t("Liste")
    return {N: t("Zahl"), S: t("Text"), B: t("Häkchen"), D: t("Datum")}.get(ty, t("Wert"))


def _kname(kinds):
    names = {"n": t("Zahl"), "s": t("Text"), "b": t("Häkchen"), "d": t("Datum"), "l": t("Liste")}
    return " / ".join(names[k] for k in kinds if k in names) or t("Wert")


class Compiled:
    __slots__ = ("fn", "ty", "rtype", "refs", "nodes")

    def __init__(self, fn, ty, refs, nodes):
        self.fn, self.ty, self.refs, self.nodes = fn, ty, refs, nodes
        self.rtype = rtype_of(ty)


def compile_expr(src, ptypes, resolve):
    """-> Compiled. FxSyntax bei Fehlern (mit Position). Leere Formel: Ergebnis immer leer (Text)."""
    tree, nodes = parse(src)
    if tree is None:
        return Compiled(lambda ctx: None, S, set(), 0)
    cm = _Compiler(ptypes, resolve)
    fn, ty = cm.c(tree)
    return Compiled(fn, ty, cm.refs, nodes)


def out_value(rtype, v):
    """Wert -> JSON-Wert fuer props (None = leer). Passt der Wert nicht zum Ergebnistyp: FxError('type')."""
    if v is None:
        return None
    if rtype == "number":
        if isinstance(v, bool) or not _isnum(v):
            raise FxError("type")
        x = fin(v)
        return int(x) if x.is_integer() and abs(x) < 2 ** 53 else x
    if rtype == "checkbox":
        if not isinstance(v, bool):
            raise FxError("type")
        return v
    if rtype == "date":
        if not isinstance(v, FDate):
            raise FxError("type")
        return v.iso()
    if rtype == "list":
        if not isinstance(v, list):
            raise FxError("type")
        return lst([txt(fmt(x)) for x in v])
    s = fmt(v)
    return txt(s) if s != "" else None


# =====================================================================================================
# Plan einer Datenbank: alle Formeln in Abhaengigkeits-Reihenfolge, Ergebnistypen
# =====================================================================================================
ROLLUP_FNS = ("count", "filled", "empty", "pct_filled", "unique", "sum", "avg", "median", "min", "max", "range",
              "checked", "unchecked", "pct_checked", "earliest", "latest", "show")


def rollup_rtype(fn):
    return "list" if fn == "show" else "date" if fn in ("earliest", "latest") else "number"


class Plan:
    """formulas: {pid: Compiled}, errors: {pid: FxSyntax}, order: [pid] (Abhaengigkeiten zuerst), rtype: {pid: rtype}."""
    __slots__ = ("formulas", "errors", "order", "rtype")


def _resolver(props, title_name, names):
    by_id = {p.get("id"): p for p in props}
    by_name = {}
    for p in props:
        by_name.setdefault(p.get("name"), p.get("id"))
    tnames = {title_name, "Name", t("Name")} if names else set()

    def resolve(x):
        if names:
            if x in by_name:
                return by_name[x]
            if x in tnames:
                return "title"
        if x == "title":
            return "title"
        return x if x in by_id else None
    return resolve


def _base_types(props):
    out = {"title": S}
    for p in props:
        ty = p.get("type")
        if ty == "rollup":
            rt = rollup_rtype(p.get("fn"))
            out[p["id"]] = L(S) if rt == "list" else rt
        elif ty in PROP_TYPE:
            out[p["id"]] = PROP_TYPE[ty]
    return out


def _key(props, title_name):
    slim = [(p.get("id"), p.get("type"), p.get("name"), p.get("expr") if p.get("type") == "formula" else None,
             p.get("fn") if p.get("type") == "rollup" else None) for p in props if isinstance(p, dict)]
    return json.dumps([slim, title_name], ensure_ascii=False)


@functools.lru_cache(maxsize=512)
def _plan_cached(key, lang):
    slim, title_name = json.loads(key)
    props = [{"id": i, "type": ty, "name": nm, "expr": ex, "fn": fn} for i, ty, nm, ex, fn in slim]
    types = _base_types(props)
    resolve = _resolver(props, title_name, False)
    fx = {p["id"]: p for p in props if p["type"] == "formula"}
    # Abhaengigkeiten aus den Verweisen (ohne Typen): erst parsen, dann in Reihenfolge uebersetzen
    deps, errors = {}, {}
    for pid, p in fx.items():
        try:
            refs = set()
            for tk in _prop_refs(p.get("expr") or ""):
                r = resolve(tk)
                if r in fx:
                    refs.add(r)
            deps[pid] = refs
        except FxSyntax as ex:
            errors[pid] = ex
            deps[pid] = set()
    order, state, depth = [], {}, {}

    def visit(pid, stack):
        st = state.get(pid)
        if st == 2:
            return depth[pid]
        if st == 1:
            raise _Cycle(stack[stack.index(pid):])
        state[pid] = 1
        d = 0
        for q in sorted(deps[pid]):
            d = max(d, visit(q, stack + [pid]) + 1)
        state[pid] = 2
        depth[pid] = d
        order.append(pid)
        return d
    for pid in fx:
        try:
            visit(pid, [])
        except _Cycle as cy:
            for q in cy.members:
                errors.setdefault(q, FxSyntax(t("Formeln verweisen im Kreis aufeinander"), 0, "cycle"))
            for q in list(state):
                if state[q] == 1:
                    del state[q]
    seen, ordered = set(), []
    for pid in order:
        if pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    for pid in fx:
        if pid not in seen and pid not in errors:
            errors[pid] = FxSyntax(t("Formeln verweisen im Kreis aufeinander"), 0, "cycle")
    plan = Plan()
    plan.formulas, plan.errors, plan.rtype = {}, errors, {}
    for pid in ordered:
        if pid in errors:
            continue
        if depth.get(pid, 0) >= CHAIN_MAX:
            errors[pid] = FxSyntax(t("Zu viele Formeln hintereinander (höchstens {n})", n=CHAIN_MAX), 0, "cycle")
            continue
        if any(q in errors for q in deps[pid]):
            errors[pid] = FxSyntax(t("Verwendete Formel ist fehlerhaft"), 0, "ref")
            continue
        try:
            cm = compile_expr(fx[pid].get("expr") or "", types, resolve)
        except FxSyntax as ex:
            errors[pid] = ex
            continue
        plan.formulas[pid] = cm
        types[pid] = L(S) if cm.rtype == "list" else cm.rtype
    plan.order = [p for p in ordered if p in plan.formulas]
    for pid in fx:
        plan.rtype[pid] = plan.formulas[pid].rtype if pid in plan.formulas else "text"
    for p in props:
        if p["type"] == "rollup":
            plan.rtype[p["id"]] = rollup_rtype(p.get("fn"))
    return plan


class _Cycle(Exception):
    def __init__(self, members):
        super().__init__("cycle")
        self.members = members


def _prop_refs(src):
    tree, _ = parse(src)
    out = []

    def walk(n):
        if not isinstance(n, tuple):
            return
        if n[0] == "prop":
            out.append(n[2])
            return
        for x in n[2:]:
            if isinstance(x, tuple):
                walk(x)
            elif isinstance(x, list):
                for y in x:
                    walk(y)
    walk(tree)
    return out


def plan_of(schema):
    props = [p for p in (schema.get("props") or []) if isinstance(p, dict)] if isinstance(schema, dict) else []
    from saga.i18n import get_lang
    return _plan_cached(_key(props, schema.get("title_name") if isinstance(schema, dict) else None), get_lang())


def annotate(props, title_name=None):
    """Ergebnistyp (rtype) aller Formel-/Rollup-Eigenschaften setzen (props wird veraendert). Fehlerhafte Formeln: text."""
    if not any(isinstance(p, dict) and p.get("type") in ("formula", "rollup") for p in props):
        return props
    from saga.i18n import get_lang
    plan = _plan_cached(_key(props, title_name), get_lang())
    for p in props:
        if p.get("type") in ("formula", "rollup"):
            p["rtype"] = plan.rtype.get(p["id"], "text")
    return props


def check(schema, pid, expr, names=False):
    """Formel expr fuer Eigenschaft pid im Schema pruefen. -> (Compiled, ids_expr). FxSyntax bei Fehlern.
    names=True: prop("...") nennt Namen (Editor) - Ergebnis zusaetzlich mit IDs."""
    props = [p for p in (schema.get("props") or []) if isinstance(p, dict)]
    others = [dict(p) for p in props if p.get("id") != pid]
    me = {"id": pid, "type": "formula", "name": next((p.get("name") for p in props if p.get("id") == pid), ""), "expr": ""}
    title_name = schema.get("title_name")
    resolve = _resolver(props if pid in {p.get("id") for p in props} else props + [me], title_name, names)
    ids = rewrite_props(expr, lambda x: resolve(x)) if names else expr
    if len(ids) > EXPR_MAX:
        raise FxSyntax(t("Formel zu lang (höchstens {n} Zeichen)", n=EXPR_MAX), EXPR_MAX)
    # Kreis: verweist die neue Formel (ueber andere Formeln) auf sich selbst?
    from saga.i18n import get_lang
    plan = _plan_cached(_key(others + [dict(me, expr=ids)], title_name), get_lang())
    err = plan.errors.get(pid)
    if err is not None and err.code in ("cycle",):
        raise err
    types = _base_types(others)
    for q, rt in plan.rtype.items():
        if q != pid:
            types[q] = L(S) if rt == "list" else rt
    cm = compile_expr(expr, types, resolve)
    if pid in cm.refs:
        raise FxSyntax(t("Eine Formel kann sich nicht selbst verwenden"), 0, "cycle")
    return cm, ids


def to_names(schema, expr):
    """Gespeicherte Formel (IDs) -> Anzeige mit Namen fuer den Editor."""
    props = [p for p in (schema.get("props") or []) if isinstance(p, dict)]
    names = {p.get("id"): p.get("name") for p in props}
    title = schema.get("title_name") or t("Name")
    return rewrite_props(expr or "", lambda x: title if x == "title" else names.get(x))
