"""Notion-Export importieren (2.5, Paket 2 E5). Quelle: ZIP aus Notion "Exportieren" -> Format "Markdown & CSV"
(auch verschachtelt: Export-....zip mit Teil-ZIPs, siehe import_common.Source), Unterseiten eingeschlossen.

Aufbau des Exports (tolerant gegen die bekannten Varianten): Seiten "Titel <id>.md", Unterseiten im Ordner "Titel <id>/"
(auch ohne ID im Ordnernamen), Datenbanken "Titel <id>.csv" bzw. "Titel <id>_all.csv" (enthaelt auch gefilterte Zeilen,
wird bevorzugt) mit den Zeilen-Seiten im gleichnamigen Ordner. <id> = 32 Hex-Zeichen (auch als UUID mit Bindestrichen).
Die CSV enthaelt keine IDs: Zeile <-> Seite ueber den Titel (Ueberschrift der Seite, dann Dateiname, dann gekuerzter
Dateiname) in Reihenfolge; Seiten ohne passende Zeile werden Eintraege ohne Eigenschaften (Bericht "orphan").

Eigenschaftstypen werden aus den Werten geraten (Typraten aus import_csv plus Notion-Eigenheiten): Datum (mit Uhrzeit ->
Zeitpunkt in der Zeitzone des Servers, "A -> B" -> Zeitraum), Auswahl, Mehrfachauswahl, Status (Spalte "Status" bzw.
typische Statuswerte -> Auswahl mit Status-Gruppen), Haekchen (Yes/No), Zahl (%, EUR, $), URL, E-Mail (-> URL mailto:),
Telefon (-> Text), Relation (Links auf Zeilen-Seiten bzw. Titel einer Datenbank im Export; mehrdeutige Titel -> erster
Treffer + Bericht), Person (-> Text, Namen lassen sich keinem Konto zuordnen, Bericht), Dateien (Pfade im ZIP -> Datei-
Eigenschaft), Formel/Rollup (Notion exportiert nur Ergebnisse -> Text mit Ergebniswert, Spalte markiert "fx_hint" ->
im Web "In Formel umwandeln"). Erkennung von Formel/Rollup nur ueber den Spaltennamen; im Dialog je Spalte aenderbar.
Mehr als 30 Spalten: Rest als Liste "Name: Wert" oben in der Eintragsseite (Bericht).

Inhalte ueber md2blocks (Notion-Formen: <aside>/<callout>, <details>, <columns>, $$...$$, Farben). Links auf Seiten/
Datenbanken des Exports (relativ oder notion.so/...<id>) -> Verweise auf die neuen Seiten; Zeile nur mit Link auf eine
CSV -> Datenbank-Block; Bilder/Dateien im ZIP -> image/attaches; externe Bilder bleiben Links (P2-E4)."""
import posixpath
import re
import time
from datetime import datetime
from html import escape
from urllib.parse import unquote

from saga import i18n, md2blocks
from saga import import_common as ic
from saga import import_csv as icsv
from saga.core import TZ, t
from saga.files import _sniff_image
from saga.notes import FILE_MAX

MD_MAX = 2 * ic.MB
COLS_MAX = 30
OPTS_MAX = 50
TOKENS_MAX = 5000                     # gemerkte Einzelwerte je Spalte (Relation ueber Titel)
NTYPES = ("text", "number", "select", "multi_select", "status", "date", "checkbox", "url", "email", "phone", "relation",
          "person", "files", "fx", "skip")
_ID = re.compile(r"(?:^|[\s_-])([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", re.I)
_ANY_ID = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_REL_TOKEN = re.compile(r"\s*([^,]*?)\s*\(((?:[^()]|\([^()]*\))*?(?:\.md|notion\.so/[^()\s]*|notion\.site/[^()\s]*))\)\s*(?:,|$)", re.I)
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?[\d ()/.-]{6,20}")
_TIME = re.compile(r"(?:^|,?\s+|T)(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?\s*([AaPp]\.?[Mm]\.?)?\s*$")
_TZ_TAIL = re.compile(r"\s*\(?(?:GMT|UTC)\s*([+-])(\d{1,2})(?::?(\d{2}))?\)?\s*$|\s*\([A-Za-z/_ ]{2,40}\)\s*$|\s*(Z)$")
_CURRENCY = re.compile(r"^([+-]?)(?:US\$|\$|£|¥|₹|CHF\s?)\s?([\d.,' ]+)$|^([+-]?[\d.,' ]+)\s?(?:\$|£|¥|USD|CHF)$")
STATUS_NAMES = {"status", "estado", "статус", "สถานะ", "stand", "zustand"}
FX_NAMES = re.compile(r"formel|formula|fórmula|формул|สูตร|rollup|roll-up|rollups|zusammenfassung|сводк", re.I)
PERSON_NAMES = re.compile(r"^(person|people|personen|assign(ee|ed)?( to)?|owner|besitzer|verantwortlich|zuständig|zugewiesen|"
                          r"created by|last edited by|erstellt von|zuletzt bearbeitet von|responsable|asignad[oa]|"
                          r"ответственн|исполнител|ผู้รับผิดชอบ)$", re.I)
TAG_NAMES = re.compile(r"^(tags?|labels?|schlagw|stichw|kategorien|etiquetas|теги|метки|แท็ก)", re.I)
PHONE_NAMES = re.compile(r"phone|telefon|teléfono|телефон|โทรศัพท์|mobil|handy", re.I)
DONE_WORDS = re.compile(r"done|complete|erledigt|fertig|abgeschlossen|geschlossen|closed|finished|archiv|cancel|abgebrochen|"
                        r"hecho|terminad|completad|готов|выполн|заверш|เสร็จ", re.I)
DOING_WORDS = re.compile(r"progress|doing|arbeit|review|prüfung|blocked|blockiert|wip|started|begonnen|läuft|aktiv|active|"
                         r"curso|progreso|работ|процесс|กำลัง", re.I)
TODO_WORDS = re.compile(r"not started|to ?do|backlog|offen|open|neu|new|geplant|planned|pendiente|nuevo|не начат|новая|ยังไม่", re.I)


# =====================================================================================================
# Namen und IDs
# =====================================================================================================
def split_id(stem):
    """'Titel 0123...cdef' -> ('Titel', '0123...cdef') bzw. (stem, None)."""
    m = _ID.search(stem)
    if not m:
        return stem.strip(), None
    return stem[:m.start()].strip(), m.group(1).replace("-", "").lower()


def _norm(s):
    """Titel/Dateiname zum Vergleich: klein, nur Buchstaben/Ziffern, Leerraum zusammengefasst."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", str(s or "").lower())).strip()


def _rel(src, path):
    r = src.root
    return path[len(r) + 1:] if r and path.startswith(r + "/") else path


def _ignored(rp):
    parts = rp.split("/")
    return any(p.startswith(".") or p.lower() == "__macosx" for p in parts) or parts[-1].lower() in ("thumbs.db", "desktop.ini")


def looks_like(paths):
    """Notion-Export? (mindestens eine .md/.csv mit Notion-ID im Namen)"""
    for p in paths:
        stem, ext = posixpath.splitext(posixpath.basename(p))
        if ext.lower() in (".md", ".csv") and split_id(re.sub(r"_all$", "", stem))[1]:
            return True
    return False


def html_export(paths):
    """Notion-Export im Format HTML (nicht unterstuetzt)?"""
    return any(p.lower().endswith(".html") and split_id(posixpath.splitext(posixpath.basename(p))[0])[1] for p in paths) \
        and not any(p.lower().endswith((".md", ".csv")) for p in paths)


# =====================================================================================================
# Werte
# =====================================================================================================
def _offset(dt_naive, sign=None, hh=None, mm=None):
    """Offset '+hh:mm' - aus der Angabe im Wert (GMT+2, Z) bzw. der Zeitzone des Servers (Notion exportiert Ortszeit)."""
    if sign:
        return f"{sign}{int(hh):02d}:{int(mm or 0):02d}"
    try:
        off = dt_naive.replace(tzinfo=TZ).utcoffset()
    except Exception:  # noqa: BLE001
        off = None
    mins = int((off.total_seconds() if off else 0) // 60)
    s = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return f"{s}{mins // 60:02d}:{mins % 60:02d}"


def _date_part(s, order, day=None):
    """Teil eines Notion-Datums -> ('YYYY-MM-DD' | 'YYYY-MM-DDTHH:MM+hh:mm', tag) oder (None, None).
    day: Tag des Beginns (Ende eines Zeitraums nur mit Uhrzeit, z. B. '3:00 PM -> 5:00 PM')."""
    s = s.strip()
    sign = hh = mm = None
    m = _TZ_TAIL.search(s)
    if m:
        if m.group(1):
            sign, hh, mm = m.group(1), m.group(2), m.group(3)
        elif m.group(4):
            sign, hh, mm = "+", 0, 0
        s = s[:m.start()].strip()
    tm = _TIME.search(s)
    hour = minute = None
    if tm:
        hour, minute = int(tm.group(1)), int(tm.group(2))
        ap = (tm.group(3) or "").lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        elif ap == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None, None
        s = s[:tm.start()].strip().rstrip(",").strip()
    d = (icsv.parse_date(s, order) or _month_words(s)) if s else day
    if not d:
        return None, None
    if hour is None:
        return d, d
    try:
        dt = datetime(int(d[:4]), int(d[5:7]), int(d[8:10]), hour, minute)
    except ValueError:
        return None, None
    if not 1900 <= dt.year <= 9998:
        return d, d
    return f"{d}T{hour:02d}:{minute:02d}{_offset(dt, sign, hh, mm)}", d


_MONTHS_X = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
             "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12, "okt": 10, "dez": 12, "mär": 3,
             "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
             "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}


def _month_words(s):
    """'27. Oktober 2026' / '27 de octubre de 2026' / 'Oct 27 2026' -> 'YYYY-MM-DD' oder None."""
    m = re.fullmatch(r"(\d{1,2})\.?\s+(?:de\s+)?([^\W\d_]{3,12})\.?,?\s+(?:de\s+)?(\d{4})", s.strip(), re.I)
    if m:
        mon = _MONTHS_X.get(m.group(2).lower()) or icsv._MONTHS.get(m.group(2).lower())
        return icsv._mk(m.group(3), mon, m.group(1)) if mon else None
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s.strip())
    if m and m.group(1).lower() in icsv._MONTHS:
        return icsv._mk(m.group(3), icsv._MONTHS[m.group(1).lower()], m.group(2))
    return None


def parse_date(v, order="mdy"):
    """Notion-Datum (auch mit Uhrzeit, Zeitzone, Zeitraum 'A -> B') -> Saga-Datumswert oder None."""
    s = str(v or "").strip()
    if not s:
        return None
    parts = [x for x in re.split(r"\s*(?:→|->)\s*", s, maxsplit=1)]
    a, day = _date_part(parts[0], order)
    if a is None:
        return None
    if len(parts) == 2 and parts[1].strip():
        b, _ = _date_part(parts[1], order, day)
        if b and b != a:
            from saga.notes import clean_date
            try:
                return clean_date(f"{a}/{b}")
            except ValueError:
                return a
    return a


def _has_time(v):
    s = _TZ_TAIL.sub("", str(v or "").split("→")[0].strip())
    return bool(_TIME.search(s))


def _num(v):
    """Waehrung ($, GBP ...) abtrennen, damit das Typraten Zahlen erkennt."""
    m = _CURRENCY.match(str(v or "").strip())
    if m:
        return (m.group(1) or "") + (m.group(2) or m.group(3) or "").strip()
    return v


def rel_tokens(v):
    """Relationswert -> [(titel, ziel|None)]. Formen: 'A (A%20<id>.md), B (https://www.notion.so/B-<id>)' oder 'A, B'."""
    s = str(v or "").strip()
    if not s:
        return []
    out, pos = [], 0
    for m in _REL_TOKEN.finditer(s):
        if m.start() != pos:
            break
        out.append((m.group(1).strip(), m.group(2).strip()))
        pos = m.end()
    if out and pos >= len(s.rstrip(", ")):
        return out
    return [(x.strip(), None) for x in re.split(r"\s*,\s*", s) if x.strip()]


def file_tokens(v):
    return [x.strip() for x in re.split(r",\s*(?=[^,]*(?:\.[A-Za-z0-9]{1,8}|/)\s*(?:,|$))", str(v or "")) if x.strip()]


def status_group(name):
    if DONE_WORDS.search(name):
        return "done"
    if TODO_WORDS.search(name):
        return "todo"
    if DOING_WORDS.search(name):
        return "doing"
    return "todo"


# =====================================================================================================
# Baum aus den Pfaden
# =====================================================================================================
class Node:
    __slots__ = ("key", "dir", "stem", "title", "nid", "kind", "path", "csv", "ref", "parent", "db", "row")

    def __init__(self, d, stem, kind, path):
        self.dir, self.stem, self.kind, self.path = d, stem, kind, path
        self.key = (d, stem)
        self.title, self.nid = split_id(stem)
        self.csv = None
        self.ref = None
        self.parent = None             # ref der Elternseite (None = Wurzel)
        self.db = None                 # Datenbank, deren Zeile diese Seite ist (Node)
        self.row = None                # Index der CSV-Zeile (nach Zuordnung)


class Tree:
    def __init__(self, src, rep):
        self.src, self.rep = src, rep
        self.nodes, self.by_id, self.by_path = {}, {}, {}
        self.files, self.file_by_name, self.folders = {}, {}, {}
        self._img = {}
        k = 0
        csvs = {}
        for p in sorted(src.paths(), key=lambda x: (x.count("/"), x.lower())):
            rp = _rel(src, p)
            if _ignored(rp):
                continue
            d, base = posixpath.dirname(rp), posixpath.basename(rp)
            stem, ext = posixpath.splitext(base)
            ext = ext.lower()
            if ext in (".md", ".markdown"):
                n = self.nodes.get((d, stem))
                if n is None:
                    n = self.nodes[(d, stem)] = Node(d, stem, "page", p)
                elif n.kind == "database":
                    self.by_path[p] = n                   # Beschreibung einer Datenbank: Verweise zeigen auf sie
                    continue
                self.by_path[p] = n
            elif ext == ".csv":
                all_ = stem.lower().endswith("_all")
                s2 = stem[:-4] if all_ else stem
                prev = csvs.get((d, s2))
                if prev is None or all_:
                    csvs[(d, s2)] = p
            elif src.entries[p].size == 0:
                rep.skip(rp, "empty")
            elif ext in (".html", ".htm") and split_id(stem)[1]:
                rep.skip(rp, "notion_unsupported")
            else:
                k += 1
                self.files[p] = f"f{k}"
                self.file_by_name.setdefault(base.lower(), []).append(p)
        for (d, s2), p in csvs.items():
            n = self.nodes.get((d, s2))
            if n is not None:                              # .md zur Datenbank (neuere Exporte): Datenbank gewinnt
                n.kind, n.csv = "database", p
            else:
                n = self.nodes[(d, s2)] = Node(d, s2, "database", p)
                n.csv = p
            for q in (p, p[:-8] + ".csv" if p.lower().endswith("_all.csv") else p[:-4] + "_all.csv"):
                self.by_path[q] = n
        for n in self.nodes.values():
            if n.nid:
                self.by_id.setdefault(n.nid, n)
            n.ref = ("d" if n.kind == "database" else "n") + (n.nid or f"x{abs(hash(n.key)) % 10 ** 12}")
        # Eltern: Ordner "Titel <id>/" gehoert zur Seite/Datenbank gleichen Namens daneben; Ordner ohne ID ueber den Titel
        by_title = {}
        for n in self.nodes.values():
            by_title.setdefault((n.dir, _norm(n.title)), []).append(n)
        self._owner_cache = {}
        for n in list(self.nodes.values()):
            owner = self._owner(n.dir, by_title)
            if owner is None:
                n.parent = None
            elif owner.kind == "database" and n.kind == "page":
                n.db = owner
                n.parent = owner.ref
            else:
                n.parent = owner.ref

    def _owner(self, d, by_title):
        """Ordner d -> Node, dem er gehoert (Seite, Datenbank oder Ordner-Seite) bzw. None (oberste Ebene)."""
        if not d:
            return None
        if d in self._owner_cache:
            return self._owner_cache[d]
        pd, name = posixpath.dirname(d), posixpath.basename(d)
        n = self.nodes.get((pd, name))
        if n is None:
            title, nid = split_id(name)
            if nid and nid in self.by_id and self.by_id[nid].dir == pd:
                n = self.by_id[nid]
            else:
                cand = by_title.get((pd, _norm(title))) or []
                n = cand[0] if len(cand) == 1 else None
        if n is None:                                      # Ordner ohne Seite (z. B. "Private & Shared"): eigene Seite
            n = Node(pd, name, "page", None)
            n.ref = "o" + str(len(self.folders) + 1)
            self.folders[d] = n
            self.nodes[("\0folder", d)] = n
            own = self._owner(pd, by_title)
            n.parent = own.ref if own is not None else None
            if own is not None and own.kind == "database":
                n.db = own
        self._owner_cache[d] = n
        return n

    # --- Verweise ---------------------------------------------------------------------------------------------------
    def node_of_target(self, here_dir, target):
        """Relativer Pfad bzw. notion.so-Link -> Node (Seite/Datenbank) oder None."""
        tgt = target.split("#", 1)[0].split("?", 1)[0].strip()
        if not tgt:
            return None
        low = tgt.lower()
        if low.startswith(("http://", "https://")):
            if not re.search(r"(^|\.)notion\.(so|site)/", low.split("//", 1)[1] if "//" in low else low):
                return None
            m = _ANY_ID.findall(tgt)
            return self.by_id.get(m[-1].replace("-", "").lower()) if m else None
        root = (self.src.root + "/") if self.src.root else ""
        for cand in (posixpath.normpath(posixpath.join(here_dir, tgt)), tgt.lstrip("/")):
            if cand.startswith(".."):
                continue
            n = self.by_path.get(root + cand) or self.by_path.get(cand)
            if n is not None:
                return n
        stem = posixpath.splitext(posixpath.basename(tgt))[0]
        _, nid = split_id(re.sub(r"_all$", "", stem))
        return self.by_id.get(nid) if nid else None

    def file_of_target(self, here_dir, target):
        tgt = target.split("#", 1)[0].split("?", 1)[0].strip()
        if not tgt or re.match(r"^[a-z][a-z0-9+.-]*:", tgt, re.I):
            return None
        root = (self.src.root + "/") if self.src.root else ""
        for cand in (posixpath.normpath(posixpath.join(here_dir, tgt)), tgt.lstrip("/")):
            if cand.startswith(".."):
                continue
            for p in (root + cand, cand):
                if p in self.files:
                    return p
        hits = self.file_by_name.get(posixpath.basename(tgt).lower()) or []
        if len(hits) == 1:
            return hits[0]
        if hits:                                            # gleicher Name mehrfach: der naechste am Ordner
            return sorted(hits, key=lambda h: (not _rel(self.src, h).startswith(here_dir + "/"), h.count("/"), h))[0]
        return None

    def is_image(self, p):
        if p not in self._img:
            try:
                with self.src.open(p) as f:
                    head = f.read(16)
            except Exception:  # noqa: BLE001 - kaputte Datei: Anhang
                head = b""
            self._img[p] = bool(_sniff_image(head))
        return self._img[p]

    def resolve(self, here_dir, target):
        n = self.node_of_target(here_dir, target)
        if n is not None and n.ref:
            return ("db" if n.kind == "database" else "page", "$ref:" + n.ref)
        low = target.lower()
        if low.startswith(("http://", "https://", "mailto:")):
            return ("url", target)
        p = self.file_of_target(here_dir, target)
        if p is not None:
            return ("file", "$file:" + self.files[p], posixpath.basename(p), self.is_image(p))
        return None


# =====================================================================================================
# Datenbanken: Spalten lesen und Typen raten
# =====================================================================================================
class NCol:
    """Statistik einer Notion-Spalte (zusaetzlich zu import_csv.Col)."""

    def __init__(self, name):
        self.col = icsv.Col(name)
        self.name = name
        self.n = 0
        self.email = self.phone = self.time = self.range = self.dates = self.nums = 0
        self.rel_link, self.rel_dbs = 0, {}
        self.file_ok, self.file_local = 0, 0
        self.tokens, self.tokens_many = {}, False

    def add(self, v, tree, here_dir):
        v = v.strip()
        if not v:
            return
        self.n += 1
        self.col.add(_num(v))
        if _EMAIL.fullmatch(v):
            self.email += 1
        if _PHONE.fullmatch(v) and sum(ch.isdigit() for ch in v) >= 6:
            self.phone += 1
        if "→" in v or "->" in v:
            self.range += 1
        if parse_date(v, "mdy") or parse_date(v, "dmy"):
            self.dates += 1
        if icsv._num_parts(_num(v)):
            self.nums += 1
        if _has_time(v):
            self.time += 1
        toks = rel_tokens(v)
        if toks and all(x[1] for x in toks):
            self.rel_link += 1
            for _, tgt in toks:
                n = tree.node_of_target(here_dir, unquote(tgt))
                if n is not None and n.db is not None:
                    self.rel_dbs[n.db.ref] = self.rel_dbs.get(n.db.ref, 0) + 1
        elif not self.tokens_many:
            for x, _ in toks:
                self.tokens[x] = self.tokens.get(x, 0) + 1
            if len(self.tokens) > TOKENS_MAX:
                self.tokens_many = True
        fs = file_tokens(v)
        if fs and all(tree.file_of_target(here_dir, unquote(x)) or re.match(r"^https?://\S+\.[A-Za-z0-9]{2,5}(\?\S*)?$", x) for x in fs):
            self.file_ok += 1
            if any(tree.file_of_target(here_dir, unquote(x)) for x in fs):
                self.file_local += 1

    def guess(self, titles_by_db):
        """-> (typ, extra) - extra: Ziel-Datenbank (relation)."""
        n = self.n
        name = self.name.strip()
        if FX_NAMES.search(name):
            return "fx", None
        if PERSON_NAMES.search(name):
            return "person", None
        if not n:
            return "text", None
        need = max(1, int(n * 0.95 + 0.999))
        if self.rel_link >= need and self.rel_dbs:
            return "relation", max(self.rel_dbs.items(), key=lambda x: x[1])[0]
        if self.file_ok >= need and self.file_local:
            return "files", None
        if self.phone >= need and PHONE_NAMES.search(name):
            return "phone", None
        g = self.col.guess()
        if g != "checkbox" and self.dates >= need and self.nums < need:
            g = "date"
        short = not self.col.many and self.col.distinct and all(len(x) <= 40 for x in self.col.distinct)
        if g == "text" and short and _norm(name) in STATUS_NAMES:
            return "status", None
        if g in ("text", "select") and self.col.split and not self.col.parts_many and TAG_NAMES.search(name) \
                and all(len(x) <= 40 for x in self.col.parts):
            g = "multi_select"
        if g == "text" and short and TAG_NAMES.search(name):
            g = "select"
        if g in ("text", "select", "multi_select"):
            if self.email >= need:
                return "email", None
            if not self.tokens_many and self.tokens and _norm(name) not in STATUS_NAMES:
                # Relation als reine Titel (aeltere/andere Exporte): ALLE Einzelwerte sind Titel derselben Datenbank
                best, hits = None, 0
                total = sum(self.tokens.values())
                for ref, titles in titles_by_db.items():
                    h = sum(c for x, c in self.tokens.items() if x.lower() in titles)
                    if h > hits:
                        best, hits = ref, h
                if best is not None and hits == total and (g != "select" or len(self.tokens) >= 2):
                    return "relation", best
            if g == "select" and (_norm(name) in STATUS_NAMES or self._statusish()):
                return "status", None
        return g, None

    def _statusish(self):
        names = list(self.col.distinct)
        return bool(names) and len(names) <= 10 and sum(1 for x in names if re.fullmatch(
            r"(not started|in progress|done|to ?do|doing|offen|in arbeit|erledigt|backlog|blocked|in review)", x.strip(), re.I)) >= max(2, len(names) // 2)

    def options(self, typ):
        if typ in ("select", "status"):
            names = list(self.col.distinct) if not self.col.many else None
        else:
            names = list(self.col.parts) if not self.col.parts_many else None
        if names is None or len(names) > OPTS_MAX:
            return None
        return names


class DB:
    """Eine Datenbank des Exports: Kopf, Spaltenstatistik, Titel der Zeilen, Zuordnung Zeile <-> Seite."""

    def __init__(self, node, tree, rep):
        self.node, self.tree, self.rep = node, tree, rep
        self.path = node.csv
        self.dir = posixpath.dirname(_rel(tree.src, node.csv))
        self.head, self.cols, self.titles, self.rows = [], [], [], 0
        self.enc, self.sep = "utf-8-sig", ","
        self.error = None

    def scan(self):
        src = self.tree.src
        try:
            with icsv._seekable(src, self.path) as f:
                self.enc = icsv.detect_encoding(f)
                f.seek(0)
                sample = f.read(65536).decode(self.enc, errors="replace")
                f.seek(0)
                self.sep = "," if sample.count(",") >= sample.count(";") else icsv.detect_sep(sample)
                head, rd = icsv.records(f, self.enc, self.sep, True)
                self.head = [icsv._col_name(x, k) for k, x in enumerate(head or [])]
                self.cols = [NCol(h) for h in self.head]
                for rec in rd:
                    self.rows += 1
                    while len(self.cols) < len(rec):
                        self.cols.append(NCol(icsv._col_name("", len(self.cols))))
                        self.head.append(self.cols[-1].name)
                    self.titles.append((rec[0] if rec else "").strip()[:200])
                    for k, x in enumerate(rec[1:], 1):
                        self.cols[k].add(x[:icsv.CELL_MAX], self.tree, self.dir)
        except ic.ImportFail as ex:
            self.error = ex
            self.rep.skip(_rel(src, self.path), ex.code, **ex.p)
        except Exception:  # noqa: BLE001 - kaputte CSV: Datenbank ohne Zeilen, Bericht
            self.error = ic.ImportFail("csv_error", n=self.rows + 1)
            self.rep.skip(_rel(src, self.path), "csv_error", n=self.rows + 1)


def _row_pages(tree, db):
    """Seiten im Ordner der Datenbank den CSV-Zeilen zuordnen -> {zeilenindex: Node}; Rest = Waisen (Liste)."""
    pages = [n for n in tree.nodes.values() if n.db is db.node and n.kind == "page" and n.path]
    heads = {}
    for n in pages:
        h = None
        try:
            with tree.src.open(n.path) as f:
                first = f.read(4096).decode("utf-8", "replace").lstrip("﻿")
            m = re.match(r"\s*#\s+(.+)", first)
            h = m.group(1).strip() if m else None
        except Exception:  # noqa: BLE001
            pass
        heads[n.key] = h
    free = list(pages)
    out = {}

    def take(i, pred):
        for n in free:
            if pred(n):
                out[i] = n
                free.remove(n)
                return True
        return False
    preds = (lambda n, nt: heads.get(n.key) is not None and _norm(heads[n.key]) == nt,          # Ueberschrift der Seite
             lambda n, nt: _norm(n.title) == nt,                                                    # Dateiname
             lambda n, nt: len(nt) >= 10 and len(_norm(n.title)) >= 10 and nt.startswith(_norm(n.title)))   # gekuerzt
    for pred in preds:
        for i, title in enumerate(db.titles):
            if i not in out:
                nt = _norm(title)
                take(i, lambda n: pred(n, nt))
    for i, n in out.items():
        n.row = i
    return out, free


# =====================================================================================================
# Analyse und Plan
# =====================================================================================================
def _types_cfg(cfg, db):
    """Vorgaben aus dem Dialog: cfg["types"][<csv-pfad>][<index>] = Typ."""
    tp = (cfg.get("types") or {}) if isinstance(cfg.get("types"), dict) else {}
    x = tp.get(_rel(db.tree.src, db.path)) if isinstance(tp, dict) else None
    return {int(k): v for k, v in (x or {}).items() if str(k).isdigit() and v in NTYPES} if isinstance(x, dict) else {}


def _build(src, rep):
    tree = Tree(src, rep)
    dbs = [DB(n, tree, rep) for n in tree.nodes.values() if n.kind == "database"]
    for db in dbs:
        db.scan()
    titles_by_db = {}
    for db in dbs:
        s = {}
        for x in db.titles:
            s[x.lower()] = s.get(x.lower(), 0) + 1
        titles_by_db[db.node.ref] = s
    for db in dbs:
        db.guesses = [col.guess(titles_by_db) if k else ("title", None) for k, col in enumerate(db.cols)]
    return tree, dbs, titles_by_db


def analyze(src, report):
    if html_export(src.paths()):
        return {"error": t("Notion-Export im Format HTML – bitte in Notion „Markdown & CSV“ wählen.")}
    tree, dbs, _ = _build(src, report)
    pages = sum(1 for n in tree.nodes.values() if n.kind == "page" and n.db is None)
    rows = sum(db.rows for db in dbs)
    big = [p for p in tree.files if src.entries[p].size > FILE_MAX]
    warn = []
    if big:
        warn.append(t("{n} Dateien sind größer als {mb} MB und werden nicht übernommen.", n=len(big), mb=FILE_MAX // ic.MB))
    if any(len(db.cols) > COLS_MAX + 1 for db in dbs):
        warn.append(t("Datenbanken mit mehr als {n} Spalten: der Rest steht als Text in den Einträgen.", n=COLS_MAX))
    out_dbs = []
    for db in dbs[:200]:
        cols = []
        for k, (col, (g, _)) in enumerate(zip(db.cols, db.guesses)):
            if k == 0:
                continue
            cols.append({"index": k, "name": col.name, "guess": g})
        out_dbs.append({"path": _rel(src, db.path), "title": db.node.title, "rows": db.rows, "columns": cols})
    return {"notion": {"pages": pages, "databases": len(dbs), "rows": rows, "files": len(tree.files),
                       "dbs": out_dbs}, "warnings": warn}


def work(tok, b, parent):
    """Lauf fuer importer.start: work(run, src) -> (root, parent)."""
    title = str(b.get("title") or "").strip()[:200]

    def go(run, src):
        rep = run.report
        run.step("analyze", 0, 0, force=True)
        if html_export(src.paths()):
            raise ic.ImportFail("unsupported")
        tree, dbs, titles_by_db = _build(src, rep)
        if not tree.nodes:
            raise ic.ImportFail("nothing")
        root_title = title or t("Notion-Import {date}", date=i18n.fmt_date(time.time(), TZ))
        items = [{"ref": "root", "kind": "page", "parent": None, "title": root_title, "icon": "📥"}]
        db_by_node = {db.node.key: db for db in dbs}
        row_refs = {}                                      # db ref -> {titel_klein: [zeilen-ref]}
        matched = {}
        for db in dbs:
            pages, orphans = _row_pages(tree, db)
            matched[db.node.key] = (pages, orphans)
            m = {}
            for i, x in enumerate(db.titles):
                n = pages.get(i)
                m.setdefault(x.lower(), []).append(n.ref if n is not None else f"r{db.node.ref}_{i}")
            row_refs[db.node.ref] = m
        used = set()
        for n in sorted(tree.nodes.values(), key=lambda x: (x.dir.count("/"), x.dir, x.stem.lower())):
            if n.kind == "page" and n.db is not None and n.path:      # Zeile (oder Waise) -> beim Eintrag
                continue
            if n.kind == "page" and n.db is not None:                 # Ordner-Seite in einer Datenbank: Eintrag
                continue
            it = {"ref": n.ref, "kind": n.kind, "parent": n.parent or "root", "title": n.title or n.stem,
                  "path": _rel(src, n.path) if n.path else n.stem}
            if n.kind == "page":
                if n.path:
                    _page_body(it, tree, n, src, rep, used)
                elif n.dir is not None:
                    it["icon"] = "📁"
            else:
                db = db_by_node[n.key]
                _db_item(it, db, tree, src, rep, b, titles_by_db, row_refs, matched[n.key], used)
            items.append(it)
        files = {}
        for p, key in tree.files.items():
            if key not in used:
                continue
            e = src.entries[p]
            if e.size > FILE_MAX:
                rep.skip(_rel(src, p), "too_big", mb=FILE_MAX // ic.MB)
                continue
            files[key] = {"name": posixpath.basename(p), "size": e.size, "path": _rel(src, p), "open": (lambda p=p: src.open(p))}
        for p, key in tree.files.items():
            if key not in used:
                rep.note(_rel(src, p), "ignored")
        return ic.apply_plan(run, {"items": items, "files": files}, parent), parent
    return go


def _scan_links(txt, tree, here_dir, used):
    """Dateien, auf die ein Text verweist, als benutzt merken (vor dem Einspielen, Dateien gehen vorher hoch)."""
    for m in re.finditer(r"!?\[[^\]]*\]\(<?([^)>\s]+)>?\)", txt):
        p = tree.file_of_target(here_dir, unquote(m.group(1)))
        if p is not None:
            used.add(tree.files[p])


def _read_md(src, n, rep):
    rp = _rel(src, n.path)
    try:
        raw = src.read(n.path, MD_MAX)
    except ic.ImportFail as ex:
        rep.skip(rp, ex.code, **ex.p)
        return None
    return raw.decode("utf-8", "replace").lstrip("﻿").replace("\r\n", "\n")


def _strip_head(txt, title):
    """Erste Zeile '# Titel' entfernen -> (rest, titel aus der Ueberschrift)."""
    m = re.match(r"\s*#\s+([^\n]+)\n?", txt)
    if m:
        return txt[m.end():], m.group(1).strip()
    return txt, None


def _strip_props(txt, names):
    """Eigenschaften-Block einer Zeilen-Seite ('Name: Wert' direkt nach dem Titel) entfernen - Werte kommen aus der CSV."""
    lines = txt.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    j = i
    low = {x.lower() for x in names}
    while j < len(lines) and lines[j].strip():
        m = re.match(r"^\s*([^:\n]{1,100}):\s?(.*)$", lines[j])
        if not m or m.group(1).strip().lower() not in low:
            break
        j += 1
    return "\n".join(lines[j:]) if j > i else txt


def _blocks_fn(tree, n, body, src, rep, prefix=None):
    here = posixpath.dirname(_rel(src, n.path))
    rp = _rel(src, n.path)

    def blocks():
        ctx = md2blocks.Ctx(lambda target, wiki: tree.resolve(here, target),
                            lambda code, **p: rep.note(f"{rp}: {p.get('target', '')}" if p.get("target") else rp, code),
                            notion=True)
        out = md2blocks.parse(body, ctx)
        return (prefix or []) + out
    return blocks


def _page_body(it, tree, n, src, rep, used, strip_names=None, prefix=None):
    txt = _read_md(src, n, rep)
    if txt is None:
        it["body"] = prefix or None
        return
    body, head = _strip_head(txt, n.title)
    if head:
        it["title"] = head[:200]
    if strip_names:
        body = _strip_props(body, strip_names)
    _scan_links(body, tree, posixpath.dirname(_rel(src, n.path)), used)
    it["body"] = _blocks_fn(tree, n, body, src, rep, prefix)


def _db_item(it, db, tree, src, rep, cfg, titles_by_db, row_refs, match, used):
    """Datenbank-Item: Schema aus den geratenen (bzw. im Dialog gewaehlten) Typen, Zeilen als Generator."""
    rp = _rel(src, db.path)
    it["icon"] = None
    over = _types_cfg(cfg, db)
    props, conv, extra, pids = [], {}, [], set()
    title_name = db.head[0] if db.head else ""
    active = 0
    for k in range(1, len(db.cols)):
        col = db.cols[k]
        g, target = db.guesses[k]
        typ = over.get(k, g)
        if typ == "skip":
            continue
        if typ == "relation" and target is None:
            target = g == "relation" and db.guesses[k][1] or None
            if target is None:                          # im Dialog gewaehlt, aber kein Ziel erkennbar
                typ = "text"
        if active >= COLS_MAX:
            extra.append(k)
            continue
        active += 1
        pid = ic.slug_id(col.name, pids)
        p = {"id": pid, "name": col.name[:60], "type": "text"}
        c = {"pid": pid, "typ": typ, "order": col.col.date_order(","), "decimal": col.col.decimal(",")}
        if typ in ("select", "multi_select", "status"):
            names = col.options(typ)
            if names is None:
                rep.note(f"{rp}: {col.name}", "too_many_opts")
                typ = c["typ"] = "text"
            else:
                oused, opts = set(), []
                for i, nm in enumerate(names):
                    o = {"id": ic.slug_id(nm, oused), "name": nm[:40], "color": ic.opt_color(i)}
                    if typ == "status":
                        o["group"] = status_group(nm)
                    opts.append(o)
                if typ == "status" and not any(o["group"] == "done" for o in opts) and len(opts) > 1:
                    pass                                    # keine "erledigt"-Option erkannt: Gruppen wie geraten
                p.update(type="multi_select" if typ == "multi_select" else "select", options=opts)
                if typ == "status":
                    p["status"] = True
                c["opt_ids"] = {nm: o["id"] for nm, o in zip(names, opts)}
                c["opt_low"] = {nm.lower(): o["id"] for nm, o in zip(names, opts)}
        if typ == "number":
            p["type"] = "number"
            f = col.col.num_fmt()
            if f:
                p["fmt"] = f
        elif typ == "date":
            p["type"] = "date"
            if col.time:
                p["time"] = True
            if col.range:
                p["range"] = True
        elif typ == "checkbox":
            p["type"] = "checkbox"
        elif typ in ("url", "email"):
            p["type"] = "url"
        elif typ == "relation":
            p.update(type="relation", target="$ref:" + target)
            c["titles"] = row_refs.get(target, {})
        elif typ == "files":
            p.update(type="files", max=20)
        elif typ == "fx":
            p["fx_hint"] = True
            rep.note(f"{rp}: {col.name}", "fx_text")
        elif typ == "person":
            rep.note(f"{rp}: {col.name}", "person_text")
        props.append(p)
        conv[k] = c
    if extra:
        rep.note(rp, "too_many_cols")
    schema = {"props": props, "views": [{"id": "tabelle", "name": t("Tabelle"), "type": "table"}]}
    if title_name and title_name != "Name":
        schema["title_name"] = title_name[:60]
    it["schema"] = schema
    pages, orphans = match
    it["row_count"] = (db.rows if not db.error else 0) + len(orphans)
    head_names = db.head[1:]
    for n in orphans:
        rep.note(_rel(src, n.path), "orphan")
    # Dateien der Zeilen-Seiten (und Datei-Eigenschaften) vorab als benutzt merken
    row_items = {}
    for i, n in list(pages.items()) + [(None, n) for n in orphans]:
        ri = {"ref": n.ref, "path": _rel(src, n.path)}
        _page_body(ri, tree, n, src, rep, used, strip_names=head_names)
        row_items[n.key] = ri
    fprops = [k for k, c in conv.items() if c["typ"] == "files"]
    if fprops and not db.error:
        with icsv._seekable(src, db.path) as f:
            for rec in icsv.records(f, db.enc, db.sep, True)[1]:
                for k in fprops:
                    if k < len(rec):
                        for x in file_tokens(rec[k]):
                            p = tree.file_of_target(db.dir, unquote(x))
                            if p is not None:
                                used.add(tree.files[p])

    def rows():
        if not db.error:
            with icsv._seekable(src, db.path) as f:
                i = -1
                for rec in icsv.records(f, db.enc, db.sep, True)[1]:
                    i += 1
                    where = f"{rp}: {t('Zeile {n}', n=i + 1)}"
                    title = (rec[0] if rec else "").strip()
                    if len(title) > 200:
                        rep.note(where, "truncated")
                    vals = {}
                    for k, c in conv.items():
                        if k >= len(rec):
                            continue
                        v = _convert(c, rec[k][:icsv.CELL_MAX * 2], tree, db, rep, where)
                        if v is not None:
                            vals[c["pid"]] = v
                    n = pages.get(i)
                    ri = row_items.get(n.key) if n is not None else None
                    pre = _extra_block(db, rec, extra)
                    body = ri.get("body") if ri else None
                    if pre:
                        body = _with_prefix(body, pre)
                    yield {"title": title[:200], "props": vals, "path": where,
                           "ref": n.ref if n is not None else f"r{db.node.ref}_{i}", "body": body}
        for n in orphans:
            ri = row_items[n.key]
            yield {"title": ri.get("title") or n.title, "props": {}, "path": ri["path"], "ref": n.ref, "body": ri.get("body")}
    it["rows"] = rows


def _with_prefix(body, pre):
    def fn():
        b = body() if callable(body) else (body or [])
        return pre + list(b or [])
    return fn


def _extra_block(db, rec, extra):
    items = []
    for k in extra:
        v = rec[k].strip() if k < len(rec) else ""
        if v:
            items.append({"content": "<b>" + escape(db.head[k], quote=False) + ":</b> " + escape(v[:icsv.CELL_MAX], quote=False),
                          "meta": {}, "items": []})
    if not items:
        return None
    return [{"id": "nx1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": items[:200]}}]


def _convert(c, v, tree, db, rep, where):
    typ = c["typ"]
    v = v.strip()
    if typ == "checkbox":
        b = icsv.parse_bool(v)
        if b is None:
            rep.note(where, "bad_value", type=typ)
        return True if b else None
    if not v:
        return None
    if typ in ("text", "fx", "person", "phone"):
        if len(v) > icsv.CELL_MAX:
            rep.note(where, "truncated")
        return v[:icsv.CELL_MAX]
    if typ == "number":
        p = icsv.parse_number(_num(v), c.get("decimal", "."))
        if p is None:
            rep.note(where, "bad_value", type=typ)
            return None
        return p[0]
    if typ == "date":
        d = parse_date(v, c.get("order", "mdy"))
        if d is None:
            rep.note(where, "bad_value", type=typ)
        return d
    if typ == "url":
        u = icsv.parse_url(v)
        if u is None:
            rep.note(where, "bad_value", type=typ)
        return u
    if typ == "email":
        if not _EMAIL.fullmatch(v):
            rep.note(where, "bad_value", type="url")
            return None
        return "mailto:" + v[:490]
    if typ in ("select", "status"):
        oid = c["opt_ids"].get(v) or c["opt_low"].get(v.lower())
        if oid is None:
            rep.note(where, "bad_value", type="select")
        return oid
    if typ == "multi_select":
        ids = [c["opt_ids"].get(x) or c["opt_low"].get(x.lower()) for x in re.split(r"\s*[,;]\s*", v) if x]
        if None in ids:
            rep.note(where, "bad_value", type=typ)
        return [x for x in dict.fromkeys(ids) if x][:20] or None
    if typ == "relation":
        out = []
        for title, tgt in rel_tokens(v):
            ref = None
            if tgt:
                n = tree.node_of_target(db.dir, unquote(tgt))
                if n is not None:
                    ref = n.ref
            if ref is None:
                hits = c["titles"].get(title.lower()) or []
                if len(hits) > 1:
                    rep.note(where, "rel_ambiguous", title=title)
                ref = hits[0] if hits else None
            if ref is None:
                rep.note(where, "rel_missing", title=title)
                continue
            out.append("$ref:" + ref)
        return list(dict.fromkeys(out))[:50] or None
    if typ == "files":
        out = []
        for x in file_tokens(v):
            p = tree.file_of_target(db.dir, unquote(x))
            if p is not None:
                out.append({"url": "$file:" + tree.files[p], "name": posixpath.basename(p)})
            else:
                rep.note(f"{where}: {x[:200]}", "ext_image" if x.lower().startswith(("http://", "https://")) else "link_missing")
        return out[:20] or None
    return v[:icsv.CELL_MAX]
