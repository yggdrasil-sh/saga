"""Dokumentmodell fuer das gleichzeitige Bearbeiten (2.5, P3 Stufe 2): Saga-Block-JSON <-> Y.Doc (pycrdt).

Austauschbar und ohne Wissen ueber Hub, WebSocket, Rechte oder Datenbank (saga/collab.py kennt nur diese Schnittstelle):

    MODEL, SCHEMA_VER, SCHEMA_HASH       Kennung des Modells (note_crdt.model/schema_ver, Handshake beim "sub")
    build(body, title) -> bytes          deterministischer Anfangszustand aus note.body (gleicher Inhalt = gleiche Bytes)
    read(doc, resolve, base=None)        Y.Doc -> (Block-Dokument als dict, Titel); ModelError bei Unbekanntem/Unsinn
    write(doc, blocks_doc, title)        Y.Doc in einer Transaktion auf diesen Stand bringen (kleinste Aenderung je Block)
    roots_ok(doc)                        nur die erlaubten Wurzeln, meta unveraendert
    to_pm(doc_dict) / from_pm(...)       Zwischenform (ProseMirror-JSON nach Saga-Schema v1) - fuer Tests und Vektoren

Schema v1 (docs/APP-API-COLLAB.md "Dokumentmodell", abgestimmt mit KONZEPT-tiptap.md 2.3): ein Y.XmlFragment
"prosemirror" wie y-prosemirror/y-tiptap es schreibt - je Saga-Block ein Y.XmlElement mit nodeName = Saga-Typ, Attribute
= id + Nicht-Text-Felder (JSON-Werte), Rich-Text als Y.XmlText mit Marken {name: attrs}, Inline-Knoten (mention, date,
math_inline, page, hardBreak) als Y.XmlElement neben dem Y.XmlText. Seitenverweise und Erwaehnungen tragen KEINEN Text
(Titel-Leck, P3-E7): den setzt die Materialisierung ueber resolve (Eigentuemer-Sicht) wieder ein. Dazu die Wurzeln
"title" (Y.Text, Seitentitel) und "meta" (Y.Map {model, schema_ver})."""
import hashlib
import json
import re
from difflib import SequenceMatcher
from html import escape as _esc
from html.parser import HTMLParser

from pycrdt import Doc, Map, Text, XmlElement, XmlFragment, XmlText

MODEL = "saga-pm"
SCHEMA_VER = 1
FRAGMENT = "prosemirror"
ROOTS = ("prosemirror", "title", "meta")
BASE_CLIENT = 1                     # client_id des deterministischen Anfangszustands (Clients/Server schreiben mit Zufalls-ID)
MAX_DEPTH = 24

PALETTE = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
# Rangfolge der Marken beim Schreiben (aussen -> innen), KONZEPT-tiptap 2.3.3: so entsteht fuer denselben Inhalt immer
# dasselbe HTML (Normalform, gleich in Python und JS)
MARK_RANK = ("link", "color", "bg", "mark", "b", "i", "u", "s", "code")
MARKS = set(MARK_RANK)
INLINE_NODES = ("hardBreak", "mention", "date", "math_inline", "page")

# Block-Knoten: Saga-Typ -> (Felder in data, die zum Knoten gehoeren). Alles andere in data -> Attribut extra.
TEXTBLOCK = {"paragraph": ("text",), "header": ("text", "level"), "callout": ("emoji", "text")}
OPAQUE = ("database", "tasks", "calendar", "widget", "buttons", "attaches", "nextcloud", "embed")
BLOCKS = set(TEXTBLOCK) | set(OPAQUE) | {"quote", "code", "delimiter", "toggle", "columns", "synced", "synced_ref", "list",
                                         "table", "image", "math", "unknown"}
FIELDS = {"paragraph": ("text",), "header": ("text", "level"), "callout": ("emoji", "text"),
          "quote": ("text", "caption", "alignment"), "code": ("code", "language", "view", "render"), "delimiter": (),
          "toggle": ("title", "open", "level", "blocks"), "columns": ("cols",), "synced": ("sid", "blocks"),
          "synced_ref": ("sid",), "list": ("style", "meta", "items"),
          "table": ("withHeadings", "headCol", "colWidths", "colors", "stretched", "content"),
          "image": ("file", "caption", "withBorder", "withBackground", "stretched", "width", "align"),
          "math": ("tex", "render")}
# Attribute je Knoten (ausser id/style/conflict/extra der Bloecke) - der Server lehnt unbekannte ab
ATTRS = {"paragraph": (), "header": ("level",), "callout": ("emoji",), "quote": ("alignment",), "qtext": (), "qcap": (),
         "code": ("language", "view", "render"), "delimiter": (), "toggle": ("open", "level"), "ttitle": (),
         "columns": (), "column": ("w", "extra"), "synced": ("sid",), "synced_ref": ("sid",), "list": ("listStyle", "meta"),
         "litem": ("id", "meta", "extra"), "ltext": (), "table": ("withHeadings", "headCol", "colWidths", "colors", "stretched"),
         "trow": (), "tcell": (), "image": ("file", "withBorder", "withBackground", "stretched", "width", "align"), "icap": (),
         "math": ("tex", "render"), "unknown": ("type", "data"),
         "hardBreak": ("marks",), "mention": ("u", "marks"), "date": ("dt", "remind", "label", "marks"),
         "math_inline": ("tex", "marks"), "page": ("id", "marks")}
for _o in OPAQUE:
    ATTRS[_o] = ("data",)
BLOCK_COMMON = ("id", "style", "conflict", "extra")
INLINE_CONTENT = {"paragraph", "header", "callout", "qtext", "qcap", "ttitle", "ltext", "tcell", "icap", "code"}
SCHEMA_HASH = hashlib.sha256(json.dumps([MODEL, SCHEMA_VER, sorted(BLOCKS), sorted((k, list(v)) for k, v in ATTRS.items()),
                                         list(MARK_RANK), list(INLINE_NODES), BLOCK_COMMON], separators=(",", ":"))
                             .encode()).hexdigest()[:16]
_HASHED_MARK = re.compile(r"(.*)--[a-zA-Z0-9+/=]{8}$")     # y-prosemirror: ueberlappende Marken gleichen Typs
_NOTE_HREF = re.compile(r"/notes#([1-9][0-9]{0,17})")


class ModelError(ValueError):
    """Zustand passt nicht zum Schema (unbekannter Knoten/Attribut/Marke, falsche Verschachtelung)."""


# =====================================================================================================================
# Inline-HTML (Positivliste des Servers, bereits bereinigt) <-> ProseMirror-Inhalt
# =====================================================================================================================
class _Inline(HTMLParser):
    """Bereinigtes Inline-HTML -> PM-Inhalt (Text mit Marken, Inline-Knoten). Text von Verweisen auf Seiten und von
    Erwaehnungen wird verworfen (kommt beim Materialisieren ueber resolve zurueck)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.cap = [], [], None    # cap: [art, attrs, text, tiefe] fuer page/mention/math/date

    def _marks(self):
        m = {}
        for _tag, mk in self.stack:
            if mk:
                m[mk[0]] = mk[1]
        if "bg" in m:
            m.pop("mark", None)
        return m

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.cap is not None:
            if tag != "br":
                self.cap[3] += 1
            return
        cls = (a.get("class") or "").split()
        if tag == "br":
            self._node("hardBreak", {})
            return
        if tag == "a":
            href = a.get("href") or ""
            m = _NOTE_HREF.fullmatch(href)
            if m:
                self.cap = ["page", {"id": int(m.group(1))}, [], 0]
                return
            self.stack.append(("a", ("link", {"href": href})))
            return
        if tag == "span" and "hk-mention" in cls:
            self.cap = ["mention", {"u": a.get("data-u") or ""}, [], 0]
            return
        if tag == "code" and "hk-math" in cls:
            self.cap = ["math_inline", {}, [], 0]
            return
        if tag == "time":
            self.cap = ["date", {"dt": a.get("datetime") or "", "remind": "hk-remind" in cls}, [], 0]
            return
        mk = None
        if tag in ("b", "strong"):
            mk = ("b", {})
        elif tag in ("i", "em"):
            mk = ("i", {})
        elif tag in ("u", "s"):
            mk = (tag, {})
        elif tag == "code":
            mk = ("code", {})
        elif tag == "mark":
            c = next((x[6:] for x in cls if x.startswith("hk-bg-") and x[6:] in PALETTE), None)
            mk = ("bg", {"c": c}) if c else ("mark", {})
        elif tag == "span":
            c = next((x[5:] for x in cls if x.startswith("hk-c-") and x[5:] in PALETTE), None)
            mk = ("color", {"c": c}) if c else None
        self.stack.append((tag, mk))

    def handle_endtag(self, tag):
        if self.cap is not None:
            if self.cap[3] > 0:
                self.cap[3] -= 1
                return
            kind, attrs, txt, _ = self.cap
            self.cap = None
            if kind == "math_inline":
                attrs["tex"] = "".join(txt)
            elif kind == "date":
                attrs["label"] = "".join(txt)
            self._node(kind, attrs)
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.cap is not None:
            self.cap[2].append(data)
            return
        if not data:
            return
        m = self._marks()
        if self.out and self.out[-1].get("type") == "text" and _mk_eq(self.out[-1].get("marks"), m):
            self.out[-1]["text"] += data
        else:
            self.out.append(_text(data, m))

    def _node(self, kind, attrs):
        n = {"type": kind, "attrs": attrs}
        m = self._marks()
        if m:
            n["marks"] = _mk_list(m)
        self.out.append(n)

    def result(self):
        if self.cap is not None:               # nicht geschlossen (kommt nach der Bereinigung nicht vor): wie geschlossen
            self.handle_endtag("")
        return self.out


def _mk_list(m):
    return [{"type": k, **({"attrs": m[k]} if m[k] else {})} for k in MARK_RANK if k in m]


def _mk_dict(lst):
    return {x["type"]: (x.get("attrs") or {}) for x in lst or ()}


def _mk_eq(lst, m):
    return _mk_dict(lst) == m


def _text(s, m):
    n = {"type": "text", "text": s}
    if m:
        n["marks"] = _mk_list(m)
    return n


def html_to_inline(s):
    p = _Inline()
    p.feed(s or "")
    p.close()
    return p.result()


def _open(name, attrs):
    if name == "link":
        return f'<a href="{_esc(str(attrs.get("href") or ""), quote=True)}" target="_blank" rel="noopener noreferrer">', "</a>"
    if name == "color":
        return f'<span class="hk-c-{_esc(str(attrs.get("c") or ""), quote=True)}">', "</span>"
    if name == "bg":
        return f'<mark class="hk-bg-{_esc(str(attrs.get("c") or ""), quote=True)}">', "</mark>"
    if name == "mark":
        return '<mark class="cdx-marker">', "</mark>"
    if name == "code":
        return '<code class="inline-code">', "</code>"
    return f"<{name}>", f"</{name}>"


def inline_to_html(nodes, resolve=None, bid=None):
    """PM-Inhalt -> Inline-HTML in Normalform (Rangfolge MARK_RANK, aussen beginnend). resolve: Texte fuer page/mention."""
    out, open_ = [], []                       # open_: [(name, attrs_json)]
    seen = {}
    for n in nodes:
        m = _mk_dict(n.get("marks"))
        want = [(k, json.dumps(m[k], sort_keys=True)) for k in MARK_RANK if k in m]
        k = 0
        while k < len(open_) and k < len(want) and open_[k] == want[k]:
            k += 1
        while len(open_) > k:
            out.append(_open(open_.pop()[0], {})[1])
        for name, aj in want[k:]:
            out.append(_open(name, json.loads(aj))[0])
            open_.append((name, aj))
        t = n.get("type")
        a = n.get("attrs") or {}
        if t == "text":
            out.append(_esc(n.get("text") or "", quote=False))
        elif t == "hardBreak":
            out.append("<br>")
        elif t == "math_inline":
            out.append(f'<code class="hk-math">{_esc(str(a.get("tex") or ""), quote=False)}</code>')
        elif t == "date":
            out.append(f'<time datetime="{_esc(str(a.get("dt") or ""), quote=True)}" class="{"hk-remind" if a.get("remind") else "hk-date"}">'
                       f'{_esc(str(a.get("label") or ""), quote=False)}</time>')
        elif t == "mention":
            u = str(a.get("u") or "")
            key = ("m", u)
            i = seen.get(key, 0); seen[key] = i + 1
            txt = resolve.mention(bid, u, i) if resolve else ""
            out.append(f'<span class="hk-mention" data-u="{_esc(u, quote=True)}">{txt}</span>')
        elif t == "page":
            pid = a.get("id")
            key = ("p", pid)
            i = seen.get(key, 0); seen[key] = i + 1
            txt = resolve.page(bid, pid, i) if resolve else ""
            out.append(f'<a href="/notes#{int(pid) if isinstance(pid, (int, float)) and not isinstance(pid, bool) else 0}">{txt}</a>')
        else:
            raise ModelError(f"inline {t}")
    while open_:
        out.append(_open(open_.pop()[0], {})[1])
    return "".join(out)


# =====================================================================================================================
# Block-JSON <-> PM-JSON (Saga-Schema v1)
# =====================================================================================================================
def _num(v):
    """Zahlen wie in JSON/JS: 2.0 -> 2 (pycrdt liefert alle Zahlen als float)."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    if isinstance(v, list):
        return [_num(x) for x in v]
    if isinstance(v, dict):
        return {k: _num(x) for k, x in v.items()}
    return v


def _lid(bid, path):
    """Interne ID eines Listenpunkts (nicht im Block-JSON): deterministisch aus Block-ID und Position."""
    return "l" + hashlib.sha256(f"{bid}/{path}".encode()).hexdigest()[:9]


def _common(b, typ, used):
    a = {}
    if isinstance(b.get("id"), str):
        a["id"] = b["id"]
    tn = b.get("tunes") if isinstance(b.get("tunes"), dict) else {}
    if tn.get("hkstyle") is not None:
        a["style"] = tn["hkstyle"]
    if tn.get("hkconflict") is not None:
        a["conflict"] = tn["hkconflict"]
    d = b.get("data") if isinstance(b.get("data"), dict) else {}
    extra = {k: v for k, v in d.items() if k not in used}
    tx = {k: v for k, v in tn.items() if k not in ("hkstyle", "hkconflict")}
    if extra or tx:
        a["extra"] = {**({"data": extra} if extra else {}), **({"tunes": tx} if tx else {})}
    return a


def _txt(v):
    return html_to_inline(v) if isinstance(v, str) else []


def _plain(v):
    return [{"type": "text", "text": v}] if isinstance(v, str) and v else []


def block_to_pm(b):
    """Ein Saga-Block (bereinigt) -> PM-Knoten."""
    typ = b.get("type")
    d = b.get("data") if isinstance(b.get("data"), dict) else {}
    if typ == "synced" and not isinstance(d.get("blocks"), list):
        typ = "synced_ref"
    if typ not in BLOCKS or (typ in ("unknown", "synced_ref") and b.get("type") != "synced"):
        # unbekannter Typ (auch die Knotennamen unknown/synced_ref selbst): Auffangknoten, Daten unveraendert (Regel 0)
        n = {"type": "unknown", "attrs": _common(b, "unknown", set(d))}
        n["attrs"].update(type=b.get("type"), data=d)
        n["attrs"].pop("extra", None)
        tn = b.get("tunes") if isinstance(b.get("tunes"), dict) else {}
        tx = {k: v for k, v in tn.items() if k not in ("hkstyle", "hkconflict")}
        if tx:
            n["attrs"]["extra"] = {"tunes": tx}
        return n
    fields = FIELDS.get(typ, ())
    # bekannte Felder mit unpassender Form (z. B. Text kein String) wandern unveraendert nach extra
    bad = set()
    for k in ("text", "caption", "title", "code"):
        if k in fields and k in d and not isinstance(d[k], str):
            bad.add(k)
    for k in fields:
        if k in d and d[k] is None:
            bad.add(k)
    if typ in ("toggle", "synced") and "blocks" in d and not isinstance(d["blocks"], list):
        bad.add("blocks")
    if typ == "columns" and not (isinstance(d.get("cols"), list) and all(isinstance(c, dict) and isinstance(c.get("blocks", []), list)
                                                                        for c in d["cols"])):
        bad.add("cols")
    if typ == "list" and not (isinstance(d.get("items", []), list) and _items_ok(d.get("items", []))):
        bad.add("items")
    if typ == "table" and not (isinstance(d.get("content", []), list) and all(isinstance(r, list) and all(isinstance(x, str) for x in r)
                                                                            for r in d.get("content", []))):
        bad.add("content")
    if typ in OPAQUE:
        n = {"type": typ, "attrs": _common(b, typ, set(d))}
        n["attrs"]["data"] = d
        return n
    used = set(fields) - bad
    a = _common(b, typ, used)
    attrs_of = [k for k in ATTRS.get(typ, ()) if k in d and k in used]
    a.update({k: d[k] for k in attrs_of})
    if typ == "list" and "style" in used and "style" in d:       # "style" ist am Block die Farbe (tunes.hkstyle)
        a["listStyle"] = d["style"]
    n = {"type": typ, "attrs": a}
    bid = b.get("id")
    if typ in TEXTBLOCK:
        n["content"] = _txt(d.get("text")) if "text" in used else []
    elif typ == "quote":
        n["content"] = [{"type": "qtext", "content": _txt(d.get("text")) if "text" in used else []},
                        {"type": "qcap", "content": _txt(d.get("caption")) if "caption" in used else []}]
    elif typ == "code":
        n["content"] = _plain(d.get("code")) if "code" in used else []
    elif typ == "image":
        n["content"] = [{"type": "icap", "content": _txt(d.get("caption")) if "caption" in used else []}]
    elif typ == "toggle":
        n["content"] = [{"type": "ttitle", "content": _txt(d.get("title")) if "title" in used else []}]
        if "blocks" in used:
            n["content"] += [block_to_pm(x) for x in d.get("blocks") or [] if isinstance(x, dict)]
            if "blocks" not in d:
                a.setdefault("extra", {}).setdefault("absent", []).append("blocks")
    elif typ == "synced":
        n["content"] = [block_to_pm(x) for x in d.get("blocks") or [] if isinstance(x, dict)]
    elif typ == "columns":
        cols = []
        for c in (d.get("cols") or []) if "cols" in used else []:
            ca = {}
            if "w" in c:
                ca["w"] = c["w"]
            ex = {k: v for k, v in c.items() if k not in ("w", "blocks")}
            if ex or "blocks" not in c:
                ca["extra"] = {**ex, **({"_noblocks": True} if "blocks" not in c else {})}
            cols.append({"type": "column", "attrs": ca, "content": [block_to_pm(x) for x in c.get("blocks") or [] if isinstance(x, dict)]})
        n["content"] = cols
    elif typ == "list":
        n["content"] = _items_pm(d.get("items") or [], bid, "") if "items" in used else []
    elif typ == "table":
        n["content"] = [{"type": "trow", "content": [{"type": "tcell", "content": _txt(x)} for x in r]}
                        for r in (d.get("content") or [] if "content" in used else [])]
    # Felder, die im Block fehlen, aber zum Knoten gehoeren (Text leer): sonst entstuenden sie beim Zurueckschreiben
    absent = [k for k in ("text", "caption", "title", "code", "items", "content", "cols") if k in used and k not in d]
    if absent:
        ex = a.setdefault("extra", {})
        ex["absent"] = sorted(set(ex.get("absent", [])) | set(absent))
    return n


def _items_ok(items):
    for it in items:
        if isinstance(it, str):
            continue
        if not isinstance(it, dict) or not isinstance(it.get("content", ""), str) or not isinstance(it.get("items", []), list):
            return False
        if not _items_ok(it.get("items", [])):
            return False
    return True


def _items_pm(items, bid, path):
    out = []
    for i, it in enumerate(items):
        p = f"{path}.{i}"
        if isinstance(it, str):
            it = {"content": it, "meta": {}, "items": []}
        a = {"id": _lid(bid, p)}
        if "meta" in it:
            a["meta"] = it["meta"]
        ex = {k: v for k, v in it.items() if k not in ("content", "meta", "items")}
        if "items" not in it:
            ex["_noitems"] = True
        if ex:
            a["extra"] = ex
        out.append({"type": "litem", "attrs": a,
                    "content": [{"type": "ltext", "content": _txt(it.get("content"))}] + _items_pm(it.get("items") or [], bid, p)})
    return out


def doc_to_pm(doc):
    return {"type": "doc", "content": [block_to_pm(b) for b in (doc.get("blocks") or []) if isinstance(b, dict)]}


# --- Rueckweg ----------------------------------------------------------------------------------------------------------
def _attrs(n, allowed):
    a = n.get("attrs") or {}
    bad = set(a) - set(allowed)
    if bad:
        raise ModelError(f"{n.get('type')}: Attribut {sorted(bad)[0]}")
    return a


def _kids(n, types=None, inline=False):
    c = n.get("content") or []
    if not isinstance(c, list):
        raise ModelError("content")
    for x in c:
        t = x.get("type") if isinstance(x, dict) else None
        if inline:
            if t != "text" and t not in INLINE_NODES:
                raise ModelError(f"{n.get('type')}: {t}")
        elif types is not None and t not in types:
            raise ModelError(f"{n.get('type')}: {t}")
    return c


def _inline_html(n, resolve, bid):
    c = _kids(n, inline=True)
    for x in c:
        for mk in x.get("marks") or []:
            if mk.get("type") not in MARKS:
                raise ModelError(f"Marke {mk.get('type')}")
        if x.get("type") != "text":
            _attrs(x, ATTRS[x["type"]])
    return inline_to_html(c, resolve, bid)


def _blocks_of(nodes, resolve, depth, allow):
    return [pm_to_block(x, resolve, depth + 1, allow) for x in nodes]


NESTED_OK = BLOCKS - {"columns"}
SYNC_OK = BLOCKS - {"columns", "synced", "synced_ref"}


def pm_to_block(n, resolve=None, depth=0, allow=None):
    """PM-Knoten -> Saga-Block. allow: erlaubte Typen an dieser Stelle (Schema: keine Spalten in Spalten/Toggles)."""
    if depth > MAX_DEPTH or not isinstance(n, dict):
        raise ModelError("zu tief")
    typ = n.get("type")
    if typ not in BLOCKS or (allow is not None and typ not in allow):
        raise ModelError(f"Block {typ}")
    a = _attrs(n, BLOCK_COMMON + ATTRS[typ])
    bid = a.get("id")
    ex = a.get("extra") if isinstance(a.get("extra"), dict) else {}
    absent = set(ex.get("absent") or ())
    out = {}
    if bid is not None:
        out["id"] = bid
    d = {}
    if typ == "unknown":
        out["type"] = a.get("type")
        d = a.get("data") if isinstance(a.get("data"), dict) else {}
    elif typ in OPAQUE:
        out["type"] = typ
        d = a.get("data") if isinstance(a.get("data"), dict) else {}
        _kids(n, ())
    else:
        out["type"] = "synced" if typ == "synced_ref" else typ
        if typ in TEXTBLOCK:
            if typ == "callout" and "emoji" in a:
                d["emoji"] = a["emoji"]
            d["text"] = _inline_html(n, resolve, bid)
            if typ == "header" and "level" in a:
                d["level"] = a["level"]
        elif typ == "quote":
            c = _kids(n, ("qtext", "qcap"))
            if [x["type"] for x in c] != ["qtext", "qcap"]:
                raise ModelError("quote")
            d["text"] = _inline_html(c[0], resolve, bid)
            d["caption"] = _inline_html(c[1], resolve, bid)
            if "alignment" in a:
                d["alignment"] = a["alignment"]
        elif typ == "code":
            c = _kids(n, ("text",))
            if any(x.get("marks") for x in c):
                raise ModelError("code: Marken")
            d["code"] = "".join(x.get("text") or "" for x in c)
            for k in ("language", "view", "render"):
                if k in a:
                    d[k] = a[k]
        elif typ == "image":
            c = _kids(n, ("icap",))
            if len(c) != 1:
                raise ModelError("image")
            for k in ("file", "withBorder", "withBackground", "stretched", "width", "align"):
                if k in a:
                    d[k] = a[k]
            d["caption"] = _inline_html(c[0], resolve, bid)
        elif typ == "math":
            _kids(n, ())
            for k in ("tex", "render"):
                if k in a:
                    d[k] = a[k]
        elif typ == "delimiter":
            _kids(n, ())
        elif typ == "toggle":
            c = _kids(n)
            if not c or c[0].get("type") != "ttitle":
                raise ModelError("toggle")
            d["title"] = _inline_html(c[0], resolve, bid)
            for k in ("open", "level"):
                if k in a:
                    d[k] = a[k]
            d["blocks"] = _blocks_of(c[1:], resolve, depth, NESTED_OK)
        elif typ == "synced":
            if "sid" in a:
                d["sid"] = a["sid"]
            d["blocks"] = _blocks_of(_kids(n), resolve, depth, SYNC_OK)
        elif typ == "synced_ref":
            _kids(n, ())
            if "sid" in a:
                d["sid"] = a["sid"]
        elif typ == "columns":
            cols = []
            for col in _kids(n, ("column",)):
                ca = _attrs(col, ATTRS["column"])
                cx = dict(ca.get("extra") or {}) if isinstance(ca.get("extra"), dict) else {}
                nob = cx.pop("_noblocks", False)
                cd = dict(cx)
                if "w" in ca:
                    cd["w"] = ca["w"]
                if not nob:
                    cd["blocks"] = _blocks_of(_kids(col), resolve, depth + 1, NESTED_OK)
                cols.append(cd)
            d["cols"] = cols
        elif typ == "list":
            if "listStyle" in a:
                d["style"] = a["listStyle"]
            if "meta" in a:
                d["meta"] = a["meta"]
            d["items"] = _items_block(_kids(n, ("litem",)), resolve, bid, depth)
        elif typ == "table":
            for k in ("withHeadings", "headCol", "colWidths", "colors", "stretched"):
                if k in a:
                    d[k] = a[k]
            d["content"] = [[_inline_html(cell, resolve, bid) for cell in _kids(r, ("tcell",))] for r in _kids(n, ("trow",))]
        for k in absent:
            if d.get(k) in ("", [], None):
                d.pop(k, None)
        xd = ex.get("data") if isinstance(ex.get("data"), dict) else {}
        d = {**_ordered(typ, d), **{k: v for k, v in xd.items() if k not in d}}
    out["data"] = d
    tn = {}
    if a.get("style") is not None:
        tn["hkstyle"] = a["style"]
    if a.get("conflict") is not None:
        tn["hkconflict"] = a["conflict"]
    if isinstance(ex.get("tunes"), dict):
        tn.update({k: v for k, v in ex["tunes"].items() if k not in tn})
    if tn:
        out["tunes"] = tn
    return _num(out)


def _ordered(typ, d):
    order = FIELDS.get(typ, ())
    return {**{k: d[k] for k in order if k in d}, **{k: v for k, v in d.items() if k not in order}}


def _items_block(items, resolve, bid, depth):
    if depth > MAX_DEPTH:
        raise ModelError("zu tief")
    out = []
    for it in items:
        a = _attrs(it, ATTRS["litem"])
        c = _kids(it, ("ltext", "litem"))
        if not c or c[0].get("type") != "ltext" or any(x.get("type") != "litem" for x in c[1:]):
            raise ModelError("litem")
        ex = dict(a.get("extra") or {}) if isinstance(a.get("extra"), dict) else {}
        noitems = ex.pop("_noitems", False)
        o = {"content": _inline_html(c[0], resolve, bid)}
        if "meta" in a:
            o["meta"] = a["meta"]
        if not noitems:
            o["items"] = _items_block(c[1:], resolve, bid, depth + 1)
        o.update({k: v for k, v in ex.items() if k not in o})
        out.append(o)
    return out


def pm_to_doc(pm, resolve=None):
    if not isinstance(pm, dict) or pm.get("type") != "doc":
        raise ModelError("doc")
    return {"blocks": [pm_to_block(x, resolve, 0, BLOCKS) for x in _kids(pm)]}


# =====================================================================================================================
# PM-JSON <-> Y (wie y-prosemirror: XmlElement je Knoten, benachbarte Texte in EINEM XmlText, Marken als Attribute)
# =====================================================================================================================
def _ymarks(lst):
    return {x["type"]: dict(x.get("attrs") or {}) for x in lst or ()}


def _put_children(el, content):
    """content (PM) als Kinder von el anhaengen (el ist eingebunden)."""
    i = 0
    while i < len(content):
        x = content[i]
        if x.get("type") == "text":
            t = el.children.append(XmlText())
            pos = 0
            while i < len(content) and content[i].get("type") == "text":
                s = content[i].get("text") or ""
                if s:
                    t.insert(pos, s, _ymarks(content[i].get("marks")) or None)
                    pos += len(s.encode("utf-8"))
                i += 1
            continue
        _put_node(el, x, len(el.children))
        i += 1


def _put_node(parent, n, index):
    e = parent.children.insert(index, XmlElement(n["type"]))
    a = dict(n.get("attrs") or {})
    if n.get("marks"):
        a["marks"] = n["marks"]
    for k in sorted(a):
        if a[k] is not None:
            e.attributes[k] = a[k]
    _put_children(e, n.get("content") or [])
    return e


def _mark_name(k):
    m = _HASHED_MARK.fullmatch(k)
    return m.group(1) if m else k


def _y_inline(t):
    out = []
    for ins, attrs in t.diff():
        if not isinstance(ins, str):
            raise ModelError("Einbettung im Text")    # Inline-Knoten sind Elemente, keine Embeds (S2)
        m = {}
        for k, v in (attrs or {}).items():
            if v is None:
                continue
            m[_mark_name(k)] = _num(v) if isinstance(v, dict) else {}
        if out and out[-1]["type"] == "text" and _mk_dict(out[-1].get("marks")) == m:
            out[-1]["text"] += ins
        elif ins:
            out.append(_text(ins, {k: m[k] for k in m}) if all(k in MARKS for k in m) else _bad_marks(ins, m))
    return out


def _bad_marks(ins, m):
    n = {"type": "text", "text": ins, "marks": [{"type": k, "attrs": v} for k, v in m.items()]}
    return n


def y_to_pm(el, depth=0):
    if depth > MAX_DEPTH + 8:
        raise ModelError("zu tief")
    tag = el.tag
    n = {"type": tag}
    a = {k: _num(v) for k, v in el.attributes}
    marks = a.pop("marks", None)
    if a:
        n["attrs"] = a
    if marks is not None:
        if not isinstance(marks, list):
            raise ModelError("marks")
        n["marks"] = marks
    content = []
    for ch in el.children:
        if isinstance(ch, XmlText):
            part = _y_inline(ch)
            if content and part and content[-1]["type"] == "text" and part[0]["type"] == "text" \
                    and _mk_dict(content[-1].get("marks")) == _mk_dict(part[0].get("marks")):
                content[-1]["text"] += part[0]["text"]
                part = part[1:]
            content += part
        elif isinstance(ch, XmlElement):
            content.append(y_to_pm(ch, depth + 1))
        else:
            raise ModelError("Knotenart")
    if content:
        n["content"] = content
    return n


def fragment_to_pm(frag):
    out = []
    for ch in frag.children:
        if not isinstance(ch, XmlElement):
            raise ModelError("Text auf oberster Ebene")
        out.append(y_to_pm(ch))
    return {"type": "doc", "content": out}


# =====================================================================================================================
# Schnittstelle
# =====================================================================================================================
def _clean_title(title):
    return str(title or "")


def build(body, title=""):
    """note.body (bereinigtes Block-JSON, str oder dict) + Titel -> Anfangszustand (Update-Bytes, deterministisch)."""
    doc = json.loads(body) if isinstance(body, str) else body
    pm = doc_to_pm(doc if isinstance(doc, dict) else {"blocks": []})
    d = Doc(client_id=BASE_CLIENT)
    meta = d.get("meta", type=Map)
    frag = d.get(FRAGMENT, type=XmlFragment)
    tt = d.get("title", type=Text)
    with d.transaction():
        meta["model"] = MODEL
        meta["schema_ver"] = SCHEMA_VER
        for i, n in enumerate(pm["content"]):
            _put_node(frag, n, i)
        if title:
            tt.insert(0, _clean_title(title))
    return d.get_update()


def new_doc(client_id=None):
    """Leeres Doc mit den Wurzeln (Hub, Clients in Tests)."""
    d = Doc(client_id=client_id, allow_multithreading=True) if client_id else Doc(allow_multithreading=True)
    d.get("meta", type=Map)
    d.get(FRAGMENT, type=XmlFragment)
    d.get("title", type=Text)
    return d


def roots_ok(doc):
    """Nur die drei Wurzeln, meta unveraendert (Server und Clients schreiben meta nie)."""
    try:
        if set(doc.keys()) - set(ROOTS):
            return False
        m = doc.get("meta", type=Map)
        return dict(m) in ({"model": MODEL, "schema_ver": SCHEMA_VER}, {"model": MODEL, "schema_ver": float(SCHEMA_VER)}, {})
    except Exception:  # noqa: BLE001 - Wurzel mit falschem Typ
        return False


def read_pm(doc):
    return fragment_to_pm(doc.get(FRAGMENT, type=XmlFragment))


def read(doc, resolve=None, base=None):
    """Y.Doc -> (Block-Dokument {"blocks": [...]}, Titel). base: zuletzt gespeichertes Block-Dokument - Bloecke, die
    unveraendert sind (gleiche Normalform), kommen byte-gleich aus base (WeakMap-Durchreichung wie im Web-Adapter)."""
    pm = read_pm(doc)
    out = pm_to_doc(pm, resolve)
    if base:
        _passthrough(out["blocks"], base, resolve)
    return out, str(doc.get("title", type=Text))


def canon_block(b, resolve=None):
    return pm_to_block(block_to_pm(b), resolve, 0, None)


def _passthrough(blocks, base, resolve):
    old = {}
    for b in base.get("blocks") or []:
        if isinstance(b, dict) and isinstance(b.get("id"), str):
            old[b["id"]] = b
    for i, b in enumerate(blocks):
        o = old.get(b.get("id"))
        if o is None:
            continue
        try:
            if _same(canon_block(o, resolve), b):
                blocks[i] = o
        except ModelError:
            pass


def _same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


# --- write: Y auf einen Zielstand bringen (kleinste Aenderung) ------------------------------------------------------
def write(doc, blocks_doc, title=None):
    """Doc in EINER Transaktion auf blocks_doc (und title, wenn nicht None) bringen. Unveraenderte Bloecke bleiben
    unberuehrt (gleichzeitige Eingaben dort gehen nicht verloren); geaenderte Texte zeichenweise."""
    target = doc_to_pm(blocks_doc)["content"]
    frag = doc.get(FRAGMENT, type=XmlFragment)
    with doc.transaction():
        _sync_list(frag, target, 0)
        if title is not None:
            tt = doc.get("title", type=Text)
            cur = str(tt)
            if cur != title:
                _sync_plain(tt, cur, title)


def _key(n, idx):
    a = n.get("attrs") or {}
    if n.get("type") in BLOCKS and isinstance(a.get("id"), str):
        return ("id", a["id"])
    return (n.get("type"), idx)


def _keys(nodes):
    cnt, out = {}, []
    for n in nodes:
        t = n.get("type")
        i = cnt.get(t, 0); cnt[t] = i + 1
        out.append(_key(n, i))
    return out


def _sync_list(parent, target, depth):
    """Kinder (nur Elemente) von parent auf target bringen: nach id (Bloecke) bzw. Typ+Position zuordnen."""
    if depth > MAX_DEPTH + 8:
        raise ModelError("zu tief")
    cur_y = list(parent.children)
    if any(not isinstance(x, XmlElement) for x in cur_y):
        for i in range(len(cur_y) - 1, -1, -1):
            del parent.children[i]
        for i, n in enumerate(target):
            _put_node(parent, n, i)
        return
    cur = [y_to_pm(x) for x in cur_y]
    ck, tk = _keys(cur), _keys(target)
    sm = SequenceMatcher(None, ck, tk, autojunk=False)
    keep = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            keep[i + k] = j + k
    for i in range(len(cur) - 1, -1, -1):           # nicht zugeordnete loeschen (von hinten)
        if i not in keep:
            del parent.children[i]
    pos_of = sorted(keep.items())                    # [(alt, neu)] in Reihenfolge
    kept_new = {j: idx for idx, (_, j) in enumerate(pos_of)}
    # neue einfuegen
    y_now = list(parent.children)
    inserted = 0
    for j, n in enumerate(target):
        if j in kept_new:
            continue
        before = sum(1 for jj in kept_new if jj < j) + inserted
        _put_node(parent, n, before)
        inserted += 1
    y_now = list(parent.children)
    for j, n in enumerate(target):
        el = y_now[j]
        if j in kept_new:
            _sync_el(parent, j, el, cur[pos_of[kept_new[j]][0]], n, depth)


def _sync_el(parent, idx, el, cur, tgt, depth):
    if cur.get("type") != tgt.get("type"):
        del parent.children[idx]
        _put_node(parent, tgt, idx)
        return
    ca = dict(cur.get("attrs") or {})
    ta = dict(tgt.get("attrs") or {})
    if cur.get("marks"):
        ca["marks"] = cur["marks"]
    if tgt.get("marks"):
        ta["marks"] = tgt["marks"]
    if tgt.get("type") == "litem" and "id" in ca:     # interne IDs der Listenpunkte bleiben
        ta["id"] = ca["id"]
    for k in sorted(set(ca) - set(ta)):
        del el.attributes[k]
    for k in sorted(ta):
        if ta[k] is None:
            if k in ca:
                del el.attributes[k]
        elif not _same(ca.get(k), ta[k]):
            el.attributes[k] = ta[k]
    if tgt.get("type") in INLINE_CONTENT:
        _sync_inline(el, cur.get("content") or [], tgt.get("content") or [])
    else:
        _sync_list(el, tgt.get("content") or [], depth + 1)


def _chars(content):
    """Inhalt ohne Inline-Knoten -> [(zeichen, marken-json)]"""
    out = []
    for x in content:
        mj = json.dumps(_mk_dict(x.get("marks")), sort_keys=True)
        for ch in x.get("text") or "":
            out.append((ch, mj))
    return out


def _sync_inline(el, cur, tgt):
    if _same(cur, tgt):
        return
    kids = list(el.children)
    plain = all(x.get("type") == "text" for x in cur) and all(x.get("type") == "text" for x in tgt) \
        and len(kids) <= 1 and all(isinstance(k, XmlText) for k in kids)
    if not plain:
        for i in range(len(kids) - 1, -1, -1):
            del el.children[i]
        _put_children(el, tgt)
        return
    t = kids[0] if kids else el.children.append(XmlText())
    a, b = _chars(cur), _chars(tgt)
    sa, sb = "".join(c for c, _ in a), "".join(c for c, _ in b)
    ops = SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):
        if tag == "equal":
            continue
        p1 = len(sa[:i1].encode("utf-8"))
        if tag in ("delete", "replace"):
            del t[p1:p1 + len(sa[i1:i2].encode("utf-8"))]
        if tag in ("insert", "replace"):
            # Einfuegen mit den Marken des Ziels, je Lauf gleicher Marken
            pos, k = p1, j1
            while k < j2:
                m = b[k][1]
                e = k
                while e < j2 and b[e][1] == m:
                    e += 1
                s = sb[k:e]
                t.insert(pos, s, {kk: vv for kk, vv in json.loads(m).items()} or {})
                pos += len(s.encode("utf-8"))
                k = e
    # Marken angleichen
    now = _chars(_y_inline(t))
    off, k = 0, 0
    while k < len(b):
        if now[k][1] == b[k][1]:
            off += len(b[k][0].encode("utf-8")); k += 1
            continue
        e, ln = k, 0
        while e < len(b) and now[e][1] != b[e][1] and now[e][1] == now[k][1] and b[e][1] == b[k][1]:
            ln += len(b[e][0].encode("utf-8")); e += 1
        want, have = json.loads(b[k][1]), json.loads(now[k][1])
        attrs = {kk: None for kk in have if kk not in want}
        attrs.update(want)
        t.format(off, off + ln, attrs)
        off += ln
        k = e


def _sync_plain(t, cur, new):
    ops = SequenceMatcher(None, cur, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):
        if tag == "equal":
            continue
        p1 = len(cur[:i1].encode("utf-8"))
        if tag in ("delete", "replace"):
            del t[p1:p1 + len(cur[i1:i2].encode("utf-8"))]
        if tag in ("insert", "replace"):
            t.insert(p1, new[j1:j2])
