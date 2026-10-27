"""CSV -> Datenbank (2.5, Paket 2 E3): Kodierung und Trennzeichen erkennen, Spaltentypen raten, Vorschau; Lauf legt eine
neue Datenbank an oder haengt Zeilen an eine bestehende an (Spalten zuordnen). Alles streamend (zeilenweise ueber
io.TextIOWrapper, zwei Durchlaeufe statt alles im Speicher). Typraten wird auch vom Notion-Import benutzt (guess_column).

Grenzen: CSV_MAX_MB (Standard 50), CSV_ROWS_MAX Zeilen (Standard 5000, Rest im Bericht), 30 Spalten (mehr: im Dialog
abwaehlen), 50 Optionen je Auswahl (mehr: Text), Zelle 2000 Zeichen, Titel 200."""
import codecs
import csv
import io
import re
from datetime import date, datetime

from saga.core import E, t
from saga.import_common import MB, ImportFail, opt_color, slug_id

CSV_MAX = max(1, int(E.get("IMPORT_CSV_MAX_MB", "50"))) * MB
ROWS_MAX = max(1, int(E.get("IMPORT_CSV_ROWS_MAX", "5000")))
COLS_MAX = 30
OPTS_MAX = 50
CELL_MAX = 2000
PREVIEW = 20
SEPS = [",", ";", "\t", "|"]
TYPES = ("text", "number", "select", "multi_select", "date", "checkbox", "url")
csv.field_size_limit(CELL_MAX * 20)


# =====================================================================================================
# Kodierung, Trennzeichen
# =====================================================================================================
def detect_encoding(f):
    """f: binaer, am Anfang. -> 'utf-8-sig' | 'utf-16' | 'utf-8' | 'cp1252' | 'latin-1' (ganze Datei gestreamt geprueft)."""
    head = f.read(4)
    f.seek(0)
    if head.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    for enc in ("utf-8", "cp1252"):
        dec = codecs.getincrementaldecoder(enc)()
        try:
            while True:
                b = f.read(MB)
                if not b:
                    dec.decode(b"", final=True)
                    break
                dec.decode(b)
            f.seek(0)
            return enc
        except UnicodeDecodeError:
            f.seek(0)
    return "latin-1"


def detect_sep(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(SEPS)).delimiter
    except csv.Error:
        lines = [x for x in sample.splitlines()[:20] if x.strip()]
        best = max(SEPS, key=lambda s: (sum(x.count(s) for x in lines), -SEPS.index(s)))
        return best if lines and sum(x.count(best) for x in lines) else ","


def reader(f, enc, sep):
    txt = io.TextIOWrapper(f, encoding=enc, errors="replace", newline="")
    return csv.reader(txt, delimiter=sep, quotechar='"', doublequote=True, skipinitialspace=False, strict=False)


def records(f, enc, sep, header):
    """-> (kopf | None, Iterator der Datenzeilen). Leere Zeilen fallen weg; Kopf = erste nicht leere Zeile."""
    rd = (r for r in reader(f, enc, sep) if any(x.strip() for x in r))
    head = next(rd, None) if header else None
    return head, rd


# =====================================================================================================
# Werte erkennen / umwandeln
# =====================================================================================================
_TRUE = {"true", "yes", "ja", "wahr", "x", "✓", "✔", "☑", "y", "on", "да", "sí", "si", "1"}
_FALSE = {"false", "no", "nein", "falsch", "", "☐", "n", "off", "нет", "0"}
_WORDS = (_TRUE | _FALSE) - {"1", "0", ""}
_MONTHS = {m: i + 1 for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                            "september", "october", "november", "december"))}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_RX_ISO = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?")
_RX_SLASH_Y = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})(?:[ T][0-9:]+(?: ?[AaPp][Mm])?)?")
_RX_DOT = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?:,? [0-9:]+)?")
_RX_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?:,? [0-9:]+(?: ?[AaPp][Mm])?)?")
_RX_EN = re.compile(r"([A-Za-z]{3,9})\.? (\d{1,2}),? (\d{4})(?:,? [0-9:]+(?: ?[AaPp][Mm])?)?(?: \(?[A-Z]{2,5}\)?)?")
_RX_URL = re.compile(r"(https?://\S+|mailto:\S+@\S+|www\.\S+\.\S+)", re.I)
_RX_NUM = re.compile(r"[+-]?(\d+|\d{1,3}(?:[.,  ']\d{3})+)(?:[.,]\d+)?")


def _y(y):
    y = int(y)
    return y + 2000 if y < 70 else y + 1900 if y < 100 else y


def _mk(y, m, d):
    try:
        return date(_y(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def parse_date(s, order="dmy"):
    """Text -> 'YYYY-MM-DD' oder None. order: Reihenfolge fuer a/b/yyyy ('dmy' oder 'mdy'). Bereich 'A → B': Anfang."""
    s = str(s or "").strip()
    if not s:
        return None
    if "→" in s:
        s = s.split("→", 1)[0].strip()
    m = _RX_ISO.fullmatch(s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3))
    m = _RX_SLASH_Y.fullmatch(s)
    if m:
        return _mk(m.group(1), m.group(2), m.group(3))
    m = _RX_DOT.fullmatch(s)
    if m:
        return _mk(m.group(3), m.group(2), m.group(1))
    m = _RX_SLASH.fullmatch(s)
    if m:
        a, b = m.group(1), m.group(2)
        return _mk(m.group(3), b, a) if order == "dmy" else _mk(m.group(3), a, b)
    m = _RX_EN.fullmatch(s)
    if m and m.group(1).lower() in _MONTHS:
        return _mk(m.group(3), _MONTHS[m.group(1).lower()], m.group(2))
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        return None


def _slash_hint(s):
    """a/b/yyyy: 'dmy' wenn a > 12, 'mdy' wenn b > 12, sonst None."""
    m = _RX_SLASH.fullmatch(str(s or "").strip())
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return "dmy" if a > 12 >= b else "mdy" if b > 12 >= a else None


def _num_parts(s):
    """-> (zahltext ohne Einheit, fmt) oder None."""
    s = str(s or "").strip().replace(" ", " ")
    fmt = None
    if s.endswith("%"):
        s, fmt = s[:-1].strip(), "percent"
    elif s.endswith("€") or s.startswith("€"):
        s, fmt = s.strip("€ ").strip(), "euro"
    elif s.upper().endswith(" EUR"):
        s, fmt = s[:-4].strip(), "euro"
    s = s.replace(" ", "")
    return (s, fmt) if _RX_NUM.fullmatch(s) else None


def _dec_style(s):
    """'comma' | 'dot' | None (mehrdeutig, z. B. '12' oder '1,234')."""
    p = _num_parts(s)
    if not p:
        return None
    x = p[0].lstrip("+-")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?|\d+,\d+", x) and not re.fullmatch(r"\d{1,3},\d{3}", x):
        return "comma"
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?|\d+\.\d+", x) and not re.fullmatch(r"\d{1,3}\.\d{3}", x):
        return "dot"
    return None


def parse_number(s, decimal="."):
    """Text -> (int|float, fmt) oder None. decimal: ',' (Dezimalkomma) oder '.'."""
    p = _num_parts(s)
    if not p:
        return None
    x, fmt = p
    x = x.replace("'", "").replace(" ", "")
    if decimal == ",":
        x = x.replace(".", "").replace(",", ".")
    else:
        x = x.replace(",", "")
    try:
        v = float(x)
    except ValueError:
        return None
    if v != v or abs(v) > 1e15:
        return None
    return (int(v) if v == int(v) and "." not in x else v), fmt


def parse_bool(s):
    v = str(s or "").strip().lower()
    return True if v in _TRUE else False if v in _FALSE else None


def parse_url(s):
    s = str(s or "").strip()
    if not _RX_URL.fullmatch(s):
        return None
    return ("https://" + s) if s.lower().startswith("www.") else s[:500]


# =====================================================================================================
# Spalten-Statistik und Typraten
# =====================================================================================================
class Col:
    """Statistik einer Spalte (streamend; verschiedene Werte nur bis OPTS_MAX + 1 gemerkt)."""

    def __init__(self, name):
        self.name, self.n, self.maxlen = name, 0, 0
        self.ok = {k: 0 for k in ("number", "date", "checkbox", "url")}
        self.words = 0                     # Haekchen-Woerter (nicht nur 0/1)
        self.dec = {"comma": 0, "dot": 0}
        self.order = {"dmy": 0, "mdy": 0}
        self.fmt = {}
        self.distinct, self.many = {}, False
        self.parts, self.parts_many, self.split = {}, False, 0

    def add(self, v):
        v = v.strip()
        if not v:
            return
        self.n += 1
        self.maxlen = max(self.maxlen, len(v))
        st = _dec_style(v)
        if st:
            self.dec[st] += 1
        if _num_parts(v):
            self.ok["number"] += 1
            f = _num_parts(v)[1]
            if f:
                self.fmt[f] = self.fmt.get(f, 0) + 1
        h = _slash_hint(v)
        if h:
            self.order[h] += 1
        if parse_date(v, "dmy") or parse_date(v, "mdy"):
            self.ok["date"] += 1
        b = parse_bool(v)
        if b is not None:
            self.ok["checkbox"] += 1
            if v.lower() in _WORDS:
                self.words += 1
        if parse_url(v):
            self.ok["url"] += 1
        if not self.many:
            self.distinct[v] = self.distinct.get(v, 0) + 1
            if len(self.distinct) > OPTS_MAX:
                self.many = True
        if "," in v or ";" in v:
            self.split += 1
        if not self.parts_many:
            for x in re.split(r"\s*[,;]\s*", v):
                if x:
                    self.parts[x] = self.parts.get(x, 0) + 1
            if len(self.parts) > OPTS_MAX:
                self.parts_many = True

    def guess(self):
        n = self.n
        if not n:
            return "text"
        need = max(1, int(n * 0.95 + 0.999))
        if self.ok["checkbox"] >= need and self.words:
            return "checkbox"
        if self.ok["date"] >= need and (self.ok["number"] < need):
            return "date"
        if self.ok["number"] >= need:
            return "number"
        if self.ok["url"] >= need:
            return "url"
        if not self.many and len(self.distinct) < n and all(len(x) <= 40 for x in self.distinct) and len(self.distinct) <= max(2, n * 0.6):
            return "select"
        if self.split and not self.parts_many and self.split >= n * 0.1 and sum(self.parts.values()) > len(self.parts) \
                and all(len(x) <= 40 for x in self.parts):
            return "multi_select"
        return "text"

    def decimal(self, sep):
        if self.dec["comma"] > self.dec["dot"]:
            return ","
        if self.dec["dot"] > self.dec["comma"]:
            return "."
        return "," if sep == ";" else "."

    def date_order(self, sep):
        if self.order["dmy"] != self.order["mdy"]:
            return "dmy" if self.order["dmy"] > self.order["mdy"] else "mdy"
        return "dmy" if sep == ";" else "mdy"

    def num_fmt(self):
        if not self.fmt:
            return None
        f, k = max(self.fmt.items(), key=lambda x: x[1])
        return f if k >= self.ok["number"] * 0.5 else None

    def options(self, typ):
        src = self.distinct if typ == "select" else self.parts
        return [k for k, _ in sorted(src.items(), key=lambda x: -x[1])][:OPTS_MAX]


# =====================================================================================================
# Datei lesen, Analyse
# =====================================================================================================
def open_csv(src, path):
    e = src.entries[path]
    if e.size > CSV_MAX:
        raise ImportFail("too_big", mb=CSV_MAX // MB)
    return src.open(path)


def _seekable(src, path):
    """Binaere, zurueckspulbare Datei: Einzeldatei direkt, ZIP-Eintrag (nicht seekbar) in eine Temp-Datei."""
    import tempfile
    e = src.entries[path]
    if e.file is not None:
        return open(e.file, "rb")
    tmp = tempfile.TemporaryFile()
    with open_csv(src, path) as f:
        while True:
            b = f.read(MB)
            if not b:
                break
            tmp.write(b)
    tmp.seek(0)
    return tmp


def analyze(src, path, opts=None):
    """-> dict mit encoding, sep, header, columns [{index, name, guess, distinct, samples}], preview, rows, warnings."""
    opts = opts or {}
    if src.entries[path].size > CSV_MAX:
        raise ImportFail("too_big", mb=CSV_MAX // MB)
    with _seekable(src, path) as f:
        enc = opts.get("encoding") if opts.get("encoding") in ("utf-8", "utf-8-sig", "utf-16", "cp1252", "latin-1") else detect_encoding(f)
        f.seek(0)
        sample = f.read(65536).decode(enc if enc != "utf-8-sig" else "utf-8-sig", errors="replace")
        f.seek(0)
        sep = opts.get("sep") if opts.get("sep") in SEPS else detect_sep(sample)
        header = opts.get("header") is not False
        cols, preview, rows, width = [], [], 0, 0
        try:
            head, rd = records(f, enc, sep, header)
            if head is not None:
                cols = [Col(_col_name(x, k)) for k, x in enumerate(head)]
                width = len(cols)
            for rec in rd:
                while len(cols) < len(rec):
                    cols.append(Col(_col_name("", len(cols))))
                for k, x in enumerate(rec):
                    cols[k].add(x[:CELL_MAX])
                if len(preview) < PREVIEW:
                    preview.append([x[:200] for x in rec])
                rows += 1
        except csv.Error:
            raise ImportFail("csv_error", n=rows + 1)
    width = max(width, len(cols))
    out = []
    for k, c in enumerate(cols):
        g = c.guess()
        out.append({"index": k, "name": c.name, "guess": g, "values": c.n, "distinct": None if c.many else len(c.distinct),
                    "options": c.options(g) if g in ("select", "multi_select") else [],
                    "decimal": c.decimal(sep), "date_order": c.date_order(sep), "fmt": c.num_fmt()})
    title = next((x["index"] for x in out if x["guess"] == "text"), 0)
    warn = []
    if len(out) > COLS_MAX + 1:
        warn.append(t("Mehr als {n} Spalten – bitte Spalten abwählen.", n=COLS_MAX))
    if rows > ROWS_MAX:
        warn.append(t("{n} Zeilen – übernommen werden höchstens {max}.", n=rows, max=ROWS_MAX))
    return {"encoding": enc, "sep": sep, "header": header, "columns": out, "title_col": title, "preview": preview,
            "rows": rows, "width": width, "warnings": warn, "limits": {"rows": ROWS_MAX, "cols": COLS_MAX, "options": OPTS_MAX}}


def _col_name(x, k):
    x = re.sub(r"\s+", " ", str(x or "")).strip().lstrip("﻿")[:60]
    return x or t("Spalte {n}", n=k + 1)


# =====================================================================================================
# Lauf: Plan bauen (neue Datenbank) bzw. Zeilen an bestehende Datenbank
# =====================================================================================================
def _check_cols(cfg, width):
    cols = cfg.get("columns")
    if not isinstance(cols, list):
        raise ValueError("columns")
    out = {}
    for c in cols:
        if not isinstance(c, dict) or not isinstance(c.get("index"), int) or not 0 <= c["index"] < max(width, 1):
            raise ValueError("columns")
        typ = c.get("type")
        if typ == "skip":
            continue
        if typ not in TYPES and typ != "title" and not c.get("target"):
            raise ValueError("type")
        out[c["index"]] = c
    return out


def convert(typ, v, col, rep, path):
    """Zellwert -> gespeicherter Wert fuer _clean_props (None = leer). col: {decimal, date_order, opt_ids}."""
    v = v.strip()
    if typ == "checkbox":
        b = parse_bool(v)
        if b is None:
            rep.note(path, "bad_value", type=typ)
        return True if b else None
    if not v:
        return None
    if typ == "text":
        if len(v) > CELL_MAX:
            rep.note(path, "truncated")
        return v[:CELL_MAX]
    if typ == "number":
        p = parse_number(v, col.get("decimal", "."))
        if p is None:
            rep.note(path, "bad_value", type=typ)
            return None
        return p[0]
    if typ == "date":
        d = parse_date(v, col.get("date_order", "dmy"))
        if d is None:
            rep.note(path, "bad_value", type=typ)
        return d
    if typ == "url":
        u = parse_url(v)
        if u is None:
            rep.note(path, "bad_value", type=typ)
        return u
    if typ == "select":
        oid = col["opt_ids"].get(v)
        if oid is None:
            rep.note(path, "bad_value", type=typ)
        return oid
    if typ == "multi_select":
        ids = [col["opt_ids"].get(x) for x in re.split(r"\s*[,;]\s*", v) if x]
        if None in ids:
            rep.note(path, "bad_value", type=typ)
        return [x for x in dict.fromkeys(ids) if x][:20] or None
    return v[:CELL_MAX]


def _rows_gen(src, path, enc, sep, header, cols, title_col, conv, rep):
    """Zeilen als {title, props} - zweiter Durchlauf ueber die Datei."""
    with _seekable(src, path) as f:
        n = 0
        try:
            for rec in records(f, enc, sep, header)[1]:
                n += 1
                if n > ROWS_MAX:
                    rep.skip(f"{path}: {t('Zeile {n}', n=n)}", "rows_limit", n=ROWS_MAX)
                    break
                where = f"{path}: {t('Zeile {n}', n=n)}"
                title = rec[title_col].strip() if title_col is not None and title_col < len(rec) else ""
                if len(title) > 200:
                    rep.note(where, "truncated")
                props = {}
                for k, c in cols.items():
                    if k == title_col or k >= len(rec):
                        continue
                    val = conv(c, rec[k][:CELL_MAX * 2], rep, where)
                    if val is not None:
                        props[c["pid"]] = val
                yield {"title": title[:200], "props": props, "path": where}
        except csv.Error:
            raise ImportFail("csv_error", n=n + 1)


def plan_new(src, path, cfg, rep):
    """Neue Datenbank: Plan fuer import_common.apply_plan. cfg: Analyse-Optionen + columns + title_col + title."""
    a = analyze(src, path, cfg)
    stats = {c["index"]: c for c in a["columns"]}
    cols = _check_cols(cfg, a["width"])
    tcol = cfg.get("title_col")
    tcol = tcol if isinstance(tcol, int) and tcol in stats else None
    active = [k for k in cols if k != tcol]
    if len(active) > COLS_MAX:
        raise ImportFail("too_many_cols")
    props, used = [], set()
    for k in active:
        c = cols[k]
        typ = c.get("type") if c.get("type") in TYPES else "text"
        name = str(c.get("name") or stats[k]["name"]).strip()[:60] or stats[k]["name"]
        pid = slug_id(name, used)
        p = {"id": pid, "name": name, "type": typ}
        c.update(pid=pid, typ=typ, decimal=c.get("decimal") if c.get("decimal") in (",", ".") else stats[k]["decimal"],
                 date_order=c.get("date_order") if c.get("date_order") in ("dmy", "mdy") else stats[k]["date_order"])
        if typ == "number" and stats[k].get("fmt"):
            p["fmt"] = stats[k]["fmt"]
        if typ in ("select", "multi_select"):
            names = _collect_options(src, path, a, k, typ)
            if names is None:
                rep.note(stats[k]["name"], "too_many_opts")
                p["type"] = c["typ"] = "text"
            else:
                oused, opts = set(), []
                for i, nm in enumerate(names):
                    opts.append({"id": slug_id(nm, oused), "name": nm[:40], "color": opt_color(i)})
                p["options"] = opts
                c["opt_ids"] = {nm: o["id"] for nm, o in zip(names, opts)}
        props.append(p)
    schema = {"props": props, "views": [{"id": "tabelle", "name": t("Tabelle"), "type": "table"}]}
    if tcol is not None:
        schema["title_name"] = stats[tcol]["name"]

    def conv(c, v, r, where):
        return convert(c["typ"], v, c, r, where)
    title = str(cfg.get("title") or "").strip() or re.sub(r"\.(csv|tsv|txt)$", "", path.rsplit("/", 1)[-1], flags=re.I)
    rows = min(a["rows"], ROWS_MAX)
    return {"items": [{"ref": "db", "kind": "database", "parent": None, "title": title[:200], "icon": "▦", "schema": schema,
                       "rows": lambda: _rows_gen(src, path, a["encoding"], a["sep"], a["header"], {k: cols[k] for k in active}, tcol, conv, rep),
                       "row_count": rows, "path": path}], "files": {}}


def _collect_options(src, path, a, k, typ):
    """Alle Optionsnamen einer Spalte (eigener Durchlauf) - None bei mehr als OPTS_MAX."""
    seen = {}
    with _seekable(src, path) as f:
        for rec in records(f, a["encoding"], a["sep"], a["header"])[1]:
            if k >= len(rec):
                continue
            v = rec[k].strip()
            vals = [v] if typ == "select" else [x for x in re.split(r"\s*[,;]\s*", v)]
            for x in vals:
                if x:
                    seen[x[:40]] = seen.get(x[:40], 0) + 1
                    if len(seen) > OPTS_MAX:
                        return None
    return list(seen)


def plan_append(src, path, cfg, rep, schema, target_titles):
    """Zeilen an eine bestehende Datenbank: columns[].target = Eigenschafts-ID (oder 'title'). Fehlende Auswahl-Optionen
    werden ergaenzt (bis 50), Relationen ueber Titel der Ziel-Datenbank (target_titles(pid) -> {titel_klein: id}).
    -> (neues Schema, rows-Generator, Zeilenzahl)."""
    a = analyze(src, path, cfg)
    cols = _check_cols(cfg, a["width"])
    by_id = {p["id"]: p for p in schema.get("props", [])}
    tcol, active = None, {}
    for k, c in cols.items():
        tg = c.get("target")
        if tg == "title":
            tcol = k
        elif tg in by_id and by_id[tg]["type"] in TYPES + ("relation",):
            st = next(x for x in a["columns"] if x["index"] == k)
            c.update(pid=tg, typ=by_id[tg]["type"], decimal=c.get("decimal") if c.get("decimal") in (",", ".") else st["decimal"],
                     date_order=c.get("date_order") if c.get("date_order") in ("dmy", "mdy") else st["date_order"])
            active[k] = c
    for k, c in active.items():
        p = by_id[c["pid"]]
        if p["type"] in ("select", "multi_select"):
            names = _collect_options(src, path, a, k, p["type"]) or []
            have = {o["name"].lower(): o["id"] for o in p.get("options", [])}
            oused = {o["id"] for o in p.get("options", [])}
            for nm in names:
                if nm.lower() not in have:
                    if len(p.setdefault("options", [])) >= OPTS_MAX:
                        rep.note(p["name"], "too_many_opts")
                        break
                    o = {"id": slug_id(nm, oused), "name": nm[:40], "color": opt_color(len(p["options"]))}
                    p["options"].append(o)
                    have[nm.lower()] = o["id"]
            c["opt_ids"] = {nm: have.get(nm.lower()) for nm in names}
        if p["type"] == "relation":
            c["titles"] = target_titles(c["pid"])

    def conv(c, v, r, where):
        if c["typ"] == "relation":
            out = []
            for x in re.split(r"\s*[,;]\s*", v.strip()):
                if not x:
                    continue
                rid = c["titles"].get(x.lower())
                if rid is None:
                    r.note(where, "rel_missing", title=x)
                else:
                    out.append(rid)
            return out or None
        return convert(c["typ"], v, c, r, where)
    rows = min(a["rows"], ROWS_MAX)
    return schema, (lambda: _rows_gen(src, path, a["encoding"], a["sep"], a["header"], active, tcol, conv, rep)), rows
