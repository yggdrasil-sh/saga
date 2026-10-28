"""Seiten im Notion-Stil: anlegen, lesen, speichern, verschieben, suchen."""
import hashlib
import json
import re
import secrets
import time
from html import escape as html_escape, unescape as html_unescape
from html.parser import HTMLParser
from itertools import islice
from urllib.parse import urlparse
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from db import clean_line, connect as _db
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from saga import bus, dbcalc, dbvals, mentions, nextcloud, page_widgets, synced, treesync
from saga.core import BASE_URL, account_lang, _api_guard, _bad, _body, _json_load, _session_user, local_ref, origin_of, ref_of, t, user_error
from saga.dashboard import visible_tiles
from saga.publish import published_via
from saga.siteicons import ICON_RE, svc_icon_path
from saga.comments import counts as comment_counts
from saga.sharing import RANK, Access, access, is_remote, mask_links, names, person, restore_links, shared_ids, title_of, unread_notes, viewer
from saga.notes import REL_ROLES, clean_tasks_map, _self_rel_backfill, _self_rel_check, formula_check, BLOCK_ID_RE, CONTAINERS, MAX_JSON_DEPTH, _iter_blocks, base_of, ensure_ids, fix_block_ids, keep_base, NOTE_BODY_MAX, PALETTE, STYLE_TYPES, note_chain, note_limit_error, NOTE_META_COLS, NOTE_TITLE_MAX, _FILE_URL, _check_file_refs, _check_parent, _clean_props, _clean_schema, _default_schema, _note_meta, _note_text, _purge_old, _rel_check, _rel_link, _rel_sync, _rel_unlink, _schema_merge, announce_row, announce_shared, announce_tree, _schema_srev, _schema_stamp, _snapshot, blocks_text, share_targets, tree_changed

router = APIRouter()


# --- Notizen als Seiten (Notion-Stil) ----------------------------------------
# Inhalt: Editor.js-Bloecke als JSON (format="blocks") oder alter Klartext (format="text").
# Seiten koennen Unterseiten haben (parent_id). Alles strikt pro Benutzer.
NOTE_BLOCKS_MAX = 200_000          # Zeichen JSON pro Seite
NOTE_ICON_MAX = 8
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "mark", "code", "a", "br", "time", "span"}
_ALLOWED_CLASSES = {"mark": {"cdx-marker"}, "code": {"inline-code"}, "time": {"hk-date", "hk-remind"}}
_TIME_ATTR = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")      # @-Datum im Text: <time datetime="2026-10-10">
# 2.5 (P6-0, Vertrag Editor-Rest): Inline-Farben nur aus der Palette - Text <span class="hk-c-<farbe>">, Hintergrund
# <mark class="hk-bg-<farbe>"> (cdx-marker bleibt = Gelb, Standard); Erwaehnung <span class="hk-mention" data-u="<handle>">;
# Inline-Mathe <code class="hk-math">TeX</code>. Je Element genau eine Klasse; span ohne erlaubte Klasse faellt weg.
_SPAN_CLASSES = {"hk-c-" + p for p in PALETTE}
_MARK_COLORS = {"hk-bg-" + p for p in PALETTE}
_MENTION_U = re.compile(r"~[a-z2-7]{16}|@(?:[a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260}")   # Handle bzw. Prinzipal
# Neue Inline-Formen, die aeltere Apps beim Bearbeiten verlieren (Markenerhalt, inline2)
NEW_MARKS = re.compile(r'<span |class="hk-bg-|class="hk-math"')


def _one_class(tag, cls, a):
    """Erlaubte Klasse(n) eines Elements -> genau eine (bzw. None = Element faellt weg / ohne Klasse)."""
    if tag == "span":
        if "hk-mention" in cls:
            u = (a.get("data-u") or "").strip()
            return ("hk-mention", u) if _MENTION_U.fullmatch(u) else None
        c = next((x for x in cls if x in _SPAN_CLASSES), None)
        return (c, None) if c else None
    if tag == "mark":
        c = next((x for x in cls if x in _MARK_COLORS), None) or ("cdx-marker" if "cdx-marker" in cls else "")
        return (c, None)
    if tag == "code":
        return ("hk-math" if "hk-math" in cls else "inline-code" if "inline-code" in cls else "", None)
    c = next((x for x in cls if x in _ALLOWED_CLASSES.get(tag, set())), "")
    return (c, None)


_NOTE_HREF = re.compile(r"/notes#[1-9][0-9]{0,17}")      # wie sharing._NOTE_LINK / publish._NOTE_HREF (BUG-090)
# Echte HTML-Elemente ausserhalb der Positivliste werden entfernt; alles andere in spitzen Klammern ist Klartext
_HTML_TAGS = set("html head body title meta link script style noscript template iframe frame frameset object embed applet param "
                 "img picture source video audio track canvas svg math map area form input button select option optgroup "
                 "textarea label fieldset legend output datalist div span p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd table thead "
                 "tbody tfoot tr td th caption col colgroup blockquote pre q cite abbr address article aside footer header "
                 "main nav section figure figcaption details summary dialog menu hr sub sup small big tt kbd samp var del "
                 "ins font center marquee blink base basefont bdi bdo data dfn rp rt ruby wbr xmp plaintext listing image "
                 "slot portal search hgroup acronym strike nobr noembed noframes".split())


class _Sanitizer(HTMLParser):
    """Positivliste fuer Inline-HTML in Bloecken. Alles andere wird zu Text. stack: (tag, ausgegeben?) - weggefallene
    span-Elemente bleiben als (tag, False) darauf, damit ihr Ende kein aeusseres Element schliesst. In Inline-Mathe
    (code.hk-math) gibt es keine weiteren Elemente (nur der TeX-Text)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.math = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            if tag not in _HTML_TAGS:           # "<bob@x.de>", "<3 Herz>": Klartext, kein HTML -> als Text behalten (BUG-039)
                self.out.append(html_escape(self.get_starttag_text() or "", quote=False))
            return
        if self.math:                            # in Inline-Mathe: nur Text
            if tag != "br":
                self.stack.append((tag, False))
            return
        a = dict(attrs)
        keep = ""
        if tag == "a":
            if any(x[0] == "a" and x[1] for x in self.stack):   # keine Verweise in Verweisen (wie der Browser): aeusseren erst
                self.handle_endtag("a")        # schliessen - sonst verdeckte sharing.mask_links den Text des inneren Verweises nicht
            href = (a.get("href") or "").strip()
            if _NOTE_HREF.fullmatch(href):          # interner Seitenverweis, exakt /notes#<zahl>
                keep = f' href="{href}"'
            elif urlparse(href).scheme in ("http", "https", "mailto"):
                keep = f' href="{html_escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
            else:
                return
        if tag == "time":
            dt = (a.get("datetime") or "").strip()
            if not _TIME_ATTR.fullmatch(dt):
                return
            keep = f' datetime="{dt}"'
        if tag == "br":
            self.out.append("<br>")
            return
        one = _one_class(tag, (a.get("class") or "").split(), a)
        if one is None:                          # span ohne erlaubte Klasse / Erwaehnung ohne gueltige Kennung: Tag weg, Text bleibt
            self.stack.append((tag, False))
            return
        if one[0]:
            keep += f' class="{one[0]}"'
        if one[1]:
            keep += f' data-u="{html_escape(one[1], quote=True)}"'
        self.out.append(f"<{tag}{keep}>")
        self.stack.append((tag, True))
        if one[0] == "hk-math":
            self.math = len(self.stack)

    def handle_endtag(self, tag):
        if any(x[0] == tag for x in self.stack):
            while self.stack:
                t, emit = self.stack.pop()
                if emit:
                    self.out.append(f"</{t}>")
                if self.math > len(self.stack):
                    self.math = 0
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(html_escape(data, quote=False))

    def result(self):
        while self.stack:
            t, emit = self.stack.pop()
            if emit:
                self.out.append(f"</{t}>")
        return "".join(self.out)


def sanitize_inline(s):
    p = _Sanitizer()
    p.feed(s)
    if p.rawdata.startswith("<") and ">" not in p.rawdata:     # "a<b c Ende": unvollstaendiger Tag am Ende ist Text (BUG-039)
        p.handle_data(p.rawdata)
        p.rawdata = ""
    p.close()
    return p.result()


PLAIN_KEYS = ("code", "tex")              # Klartext-Felder: Code-Bloecke (Textarea), Mathe-Block (TeX, 2.5 P6-0)


def _sanitize_value(v, key=None):
    if isinstance(v, str):
        return v if key in PLAIN_KEYS else sanitize_inline(v)
    if isinstance(v, list):
        return [_sanitize_value(x, key) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_value(x, k) for k, x in v.items()}
    return v


def _json_depth_ok(obj, limit=MAX_JSON_DEPTH):
    """Iterativ (ohne Rekursion) pruefen, dass JSON nicht absurd tief verschachtelt ist."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if d > limit:
            return False
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)
    return True


class TooLarge(ValueError):
    """Seite zu gross -> HTTP 413."""


def clean_blocks(raw, limit=NOTE_BLOCKS_MAX):
    """JSON-String pruefen und bereinigen -> (json_string, plaintext). ValueError bei Unsinn."""
    if len(raw) > limit:
        raise TooLarge(t("Seite zu groß"))
    d = json.loads(raw)
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError(t("kein Block-Dokument"))
    if not _json_depth_ok(d):
        raise ValueError(t("zu tief verschachtelt"))
    blocks = []
    for b in d["blocks"]:
        if not isinstance(b, dict) or not isinstance(b.get("type"), str) or not isinstance(b.get("data"), dict):
            raise ValueError(t("ungültiger Block"))
        nb = {k: (_sanitize_value(v) if k == "data" else v) for k, v in b.items() if k in ("id", "type", "data")}
        if "tunes" in b:
            nb["tunes"] = b["tunes"]
        blocks.append(nb)
    blocks = _clean_design(_flatten_nested_columns(blocks))
    fix_block_ids(blocks)                     # 2.5 (P3-E1): eindeutige ids fuer ALLE Bloecke (Merge, Kommentare, Widgets)
    doc = {"time": d.get("time"), "version": d.get("version"), "blocks": blocks}
    return json.dumps(doc, ensure_ascii=False), blocks_text(blocks)


def _flatten_nested_columns(blocks, nested=False):
    """Spalten in Spalten/Toggles kann der Editor nicht darstellen -> innere Spalten-Bloecke aufloesen:
    ihr Inhalt (Spalte 1, dann 2, ...) rueckt an ihre Stelle. Nichts geht verloren."""
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            cols = [c for c in (d.get("cols") or []) if isinstance(c, dict)]
            for c in cols:
                c["blocks"] = _flatten_nested_columns(c.get("blocks") if isinstance(c.get("blocks"), list) else [], True)
            if nested:
                for c in cols:
                    out.extend(c["blocks"])
                continue
        elif b.get("type") in CONTAINERS and isinstance(d.get("blocks"), list):
            d["blocks"] = _flatten_nested_columns(d["blocks"], True)
        out.append(b)
    return out


# --- Seite gestalten (2.5, G1-G3, G10): nur Zahlen in festen Grenzen, Farben nur aus der Palette ----------------------
# Der Vertrag fuer alle Clients steht in docs/APP-API.md ("Seite gestalten"). Fehlt ein Feld, gilt das alte Verhalten.
ALIGNS = ("left", "center", "right")
COL_MIN, COLS_MAX = 0.15, 6             # 2.5 (P6-0, P6-E7): bis 6 Spalten
FONTS = ("serif", "mono")                 # Schrift je Seite (note.font); NULL = Standard


def _num_ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e9


_CONFLICT_BY = re.compile(r"~[a-z2-7]{16}|@(?:[a-z0-9][a-z0-9_-]{1,31}|~[a-z2-7]{16})@[a-z0-9.:-]{1,260}|[a-z0-9][a-z0-9_.@-]{0,63}")


def _clean_hkstyle(typ, tunes):
    """tunes -> {"hkstyle": {"c": <palette>, "bg": true?}, "hkconflict": {...}} oder None. Alle anderen Tunes fallen weg.
    hkconflict (2.5, P3 Stufe 1): Konfliktblock aus dem Zusammenfuehren (saga.merge) - {by: Handle/Prinzipal, name, at}."""
    if not isinstance(tunes, dict):
        return None
    out = {}
    st = tunes.get("hkstyle")
    if typ in STYLE_TYPES and isinstance(st, dict) and st.get("c") in PALETTE:
        out["hkstyle"] = {"c": st["c"]}
        if st.get("bg") is True:
            out["hkstyle"]["bg"] = True
    cf = tunes.get("hkconflict")
    if isinstance(cf, dict):
        x = {}
        if isinstance(cf.get("by"), str) and _CONFLICT_BY.fullmatch(cf["by"]):
            x["by"] = cf["by"]
        if isinstance(cf.get("name"), str):
            x["name"] = re.sub(r"[<>&\x00-\x1f]", "", cf["name"])[:80]
        if _num_ok(cf.get("at")):
            x["at"] = round(float(cf["at"]), 3)
        if isinstance(cf.get("of"), str) and BLOCK_ID_RE.fullmatch(cf["of"]):
            x["of"] = cf["of"]                    # ID des Originals (Fassung "uebernehmen" ersetzt es)
        out["hkconflict"] = x
    return out or None


# 2.5 (P6-0): Mathe-Block {tex}, Mermaid = Code-Block mit language "mermaid" und view, Vorschaubild render (vom Web erzeugt)
MATH_MAX = 4000
CODE_VIEWS = ("code", "preview", "split")
_SID = re.compile(r"[A-Za-z0-9]{16}")
_HASH16 = re.compile(r"[0-9a-f]{16}")


def _clean_render(r):
    """data.render {png, svg, h, w, hgt} -> bereinigt oder None (Dateien: /files/<id>, Eigentum prueft _check_file_refs)."""
    if not isinstance(r, dict):
        return None
    out = {}
    for k in ("png", "svg"):
        if isinstance(r.get(k), str) and _FILE_URL.fullmatch(r[k]):
            out[k] = r[k]
    if not out or not (isinstance(r.get("h"), str) and _HASH16.fullmatch(r["h"])):
        return None
    out["h"] = r["h"]
    for k in ("w", "hgt"):
        if _num_ok(r.get(k)) and 0 < r[k] <= 20000:
            out[k] = int(r[k])
    return out


def src_hash(src):
    """Kennung des Quelltexts fuer render.h: SHA-256 (UTF-8), die ersten 16 Hex-Zeichen."""
    return hashlib.sha256(str(src or "").encode("utf-8")).hexdigest()[:16]


def _clean_rendered(d, src):
    """render bereinigen; 2.5 (P6-1): passt h nicht (mehr) zum Quelltext, setzt der Server stale: true - Apps zeigen das
    Bild dann als veraltet (bis das Web es neu erzeugt). Vom Client gesendetes stale zaehlt nicht."""
    if "render" in d:
        r = _clean_render(d["render"])
        if r:
            if r["h"] != src_hash(src):
                r["stale"] = True
            d["render"] = r
        else:
            d.pop("render")


def _col_weights(cols):
    """Spaltenbreiten: alle w gueltig (> 0) -> Summe 1, je Spalte mind. 0,15, auf 0,01 gerundet; sonst None."""
    if not 2 <= len(cols) <= COLS_MAX or not all(_num_ok(c.get("w")) and c["w"] > 0 for c in cols):
        return None
    tot = sum(c["w"] for c in cols)
    w = [c["w"] / tot for c in cols]
    fixed = [False] * len(w)
    for _ in range(len(w)):                       # Mindestbreite: zu schmale auf 0,15, den Rest anteilig verteilen
        low = [i for i, x in enumerate(w) if not fixed[i] and x < COL_MIN]
        if not low:
            break
        for i in low:
            fixed[i] = True
            w[i] = COL_MIN
        free = [i for i in range(len(w)) if not fixed[i]]
        rest = 1 - COL_MIN * sum(fixed)
        s = sum(w[i] for i in free)
        for i in free:
            w[i] = w[i] / s * rest if s > 0 else rest / len(free)
    r = [round(x, 2) for x in w]
    big = max(range(len(r)), key=lambda i: r[i])  # Rundungsrest an die breiteste Spalte -> Summe genau 1
    r[big] = round(r[big] + 1 - sum(r), 2)
    return r


TABLE_COLORS = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
TABLE_COLS_MAX = 20
_TIDX = re.compile(r"[0-9]{1,4}")          # Zeilen-/Spaltenindex der Farben (nur ASCII-Ziffern)


def _clean_table(d):
    """Einfache Tabelle (2.5): headCol bool, colWidths [%] (je 5-90, Summe 100, so viele wie Spalten, hoechstens 20),
    colors {r: {zeile: farbe}, c: {spalte: farbe}, x: {"z,s": farbe}} nur mit Indizes innerhalb der Tabelle. Falsches
    faellt weg; content/withHeadings/stretched und unbekannte Felder bleiben unangetastet."""
    rows = [r for r in d.get("content") or [] if isinstance(r, list)] if isinstance(d.get("content"), list) else []
    nr, nc = len(rows), max((len(r) for r in rows), default=0)
    if "headCol" in d:
        if d["headCol"] is True:
            d["headCol"] = True
        else:
            d.pop("headCol")
    if "colWidths" in d:
        w = d["colWidths"]
        if (isinstance(w, list) and 0 < len(w) == nc <= TABLE_COLS_MAX and all(_num_ok(x) and x > 0 for x in w)):
            tot = sum(w)
            w = [min(90.0, max(5.0, x * 100.0 / tot)) for x in w]
            tot = sum(w)
            w = [round(x * 100.0 / tot, 1) for x in w]
            big = max(range(len(w)), key=lambda i: w[i])
            w[big] = round(w[big] + 100.0 - sum(w), 1)
            d["colWidths"] = w
        else:
            d.pop("colWidths")
    if "colors" in d:
        src, out = d["colors"] if isinstance(d["colors"], dict) else {}, {}
        for k, lim in (("r", nr), ("c", nc)):
            m = {str(int(i)): col for i, col in (src.get(k) or {}).items()
                 if isinstance(src.get(k), dict) and _TIDX.fullmatch(str(i)) and int(i) < lim and col in TABLE_COLORS} if isinstance(src.get(k), dict) else {}
            if m:
                out[k] = dict(list(m.items())[:200])
        if isinstance(src.get("x"), dict):
            m = {}
            for key, col in list(src["x"].items())[:1000]:
                ps = str(key).split(",")
                if len(ps) == 2 and all(_TIDX.fullmatch(p) for p in ps) and int(ps[0]) < nr and int(ps[1]) < nc and col in TABLE_COLORS:
                    m[f"{int(ps[0])},{int(ps[1])}"] = col
            if m:
                out["x"] = m
        if out:
            d["colors"] = out
        else:
            d.pop("colors")


def _clean_design(blocks):
    """Gestaltungsfelder bereinigen (auch in Spalten und Toggles): tunes nur hkstyle, Spaltengewichte, Bild-/Player-Breite
    und -Ausrichtung, Toggle-Ueberschrift. Unbekannte Felder in data bleiben unangetastet."""
    for b in blocks:
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        if "tunes" in b:
            tn = _clean_hkstyle(typ, b["tunes"])
            if tn:
                b["tunes"] = tn
            else:
                b.pop("tunes")
        d = b.get("data")
        if not isinstance(d, dict):
            continue
        if typ == "columns":
            cols = [c for c in (d.get("cols") or []) if isinstance(c, dict)] if isinstance(d.get("cols"), list) else []
            ws = _col_weights(cols)
            for i, c in enumerate(cols):
                if ws:
                    c["w"] = ws[i]
                else:
                    c.pop("w", None)
                if isinstance(c.get("blocks"), list):
                    _clean_design(c["blocks"])
        elif typ in ("image", "embed"):
            player = typ == "image" or d.get("kind") == "youtube"      # Link-Karten (Embed ohne Player): unveraendert
            if "width" in d:
                if player and _num_ok(d["width"]):
                    d["width"] = int(min(100, max(10, round(d["width"]))))
                else:
                    d.pop("width")
            if "align" in d and not (player and d["align"] in ALIGNS):
                d.pop("align")
        elif typ == "widget":                          # 2.5: Widgets in Seiten (saga.page_widgets)
            b["data"] = page_widgets.clean_data(d)
        elif typ == "table":                           # 2.5 (G12/G13): Kopfspalte, Spaltenbreiten, Farben
            _clean_table(d)
        elif typ == "toggle":
            if "level" in d:
                lv = int(min(3, max(0, round(d["level"])))) if _num_ok(d["level"]) else 0
                if lv:
                    d["level"] = lv
                else:
                    d.pop("level")
            if isinstance(d.get("blocks"), list):
                _clean_design(d["blocks"])
        elif typ == "math":                           # 2.5 (P6-0): Mathe-Block - TeX als Klartext
            d["tex"] = str(d.get("tex") or "")[:MATH_MAX]
            _clean_rendered(d, d["tex"])
        elif typ == "code":                           # Mermaid (language "mermaid"): Ansicht und Vorschaubild
            if "view" in d and d["view"] not in CODE_VIEWS:
                d.pop("view")
            _clean_rendered(d, d.get("code") if isinstance(d.get("code"), str) else "")
        elif typ == "synced":                         # 2.5 (P6-0): synchronisierter Block - Original {sid, blocks}, Kopie {sid}
            src = d.pop("src", None)                  # setzt der Server beim Lesen ein (P6-6), nie gespeichert
            d.pop("srev", None)
            if isinstance(src, dict) and isinstance(src.get("rev"), int) and not isinstance(src.get("rev"), bool) and src["rev"] >= 0:
                d["srev"] = src["rev"]                # Stand des Originals beim Lesen: Basis fuers Durchschreiben (synced.prepare)
            if not (isinstance(d.get("sid"), str) and _SID.fullmatch(d["sid"])):
                d.pop("sid", None)
            if isinstance(d.get("blocks"), list):
                _clean_design(d["blocks"])
            elif "blocks" in d:
                d.pop("blocks")
    return blocks


# --- Aeltere Clients (2.5, P6-0): Markenerhalt und Spalten-Schutz ----------------------------------------------------
# Aeltere iOS-/Linux-Apps (und Server ohne Faehigkeit inline2) verwerfen beim Bearbeiten span-Elemente und Klassen
# (Inline-Farben, Erwaehnungen, Inline-Mathe), aeltere Stande schneiden Spalten 5-6 ab. Zwischenregel bis zum Markenerhalt
# ueber saga.merge (P6-3): Textfelder eines Blocks (gleiche id und gleicher Typ), deren reiner Text sich nicht geaendert
# hat, behalten ihr altes HTML vollstaendig, wenn es neue Formen enthielt, die jetzt fehlen. Ein Spalten-Block mit genau 4
# Spalten, der vorher 5-6 hatte, bekommt die fehlenden Spalten aus dem alten Stand zurueck.
CLIENT_CAPS = frozenset(("inline2", "cols6", "merge1", "sync1"))   # was dieser Server-/Web-Stand versteht (X-Saga-Caps);
# sync1 (2.5, P6-6) = der Client schickt den Inhalt synchronisierter Kopien bearbeitet zurueck (Durchschreiben ins Original)


def client_caps(request):
    """Faehigkeiten des Clients laut Kopf X-Saga-Caps (kommagetrennt). Ohne Kopf: Web (Browser-Sitzung) alle, Apps
    (Geraete-Sitzung, Schluessel) keine - aeltere Apps kennen den Kopf nicht."""
    hdr = request.headers.get("x-saga-caps")
    if hdr is not None:
        return frozenset(x for x in re.split(r"[\s,]+", hdr) if x in CLIENT_CAPS)
    if request.session.get("did") is None and not (request.headers.get("authorization") or "").startswith("Bearer "):
        return CLIENT_CAPS
    return frozenset()


def _plain_inline(s):
    return re.sub(r"\s+", " ", html_unescape(re.sub(r"<[^>]*>", "", s or ""))).strip()


def _keep_fields(old, new):
    """Textfelder (rekursiv, gleicher Pfad): alter HTML-Text mit neuen Formen, gleicher reiner Text, Formen fehlen -> alt."""
    if isinstance(old, str) and isinstance(new, str):
        if old != new and NEW_MARKS.search(old) and not NEW_MARKS.search(new) and _plain_inline(old) == _plain_inline(new):
            return old
        return new
    if isinstance(old, dict) and isinstance(new, dict):
        return {k: (_keep_fields(old[k], v) if k in old and k not in ("blocks", "cols") else v) for k, v in new.items()}
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [_keep_fields(o, n) for o, n in zip(old, new)]
    return new


def legacy_keep(old_body, new_body, caps):
    """Speichern eines Clients ohne inline2/cols6: Marken und Spalten aus dem alten Stand erhalten (s. o.). -> JSON."""
    if not old_body or ("inline2" in caps and "cols6" in caps):
        return new_body
    try:
        od, nd = json.loads(old_body), json.loads(new_body)
    except ValueError:
        return new_body
    olds = {b.get("id"): b for b in _iter_blocks(od.get("blocks") or []) if isinstance(b.get("id"), str)}
    changed = False
    for b in _iter_blocks(nd.get("blocks") or []):
        o = olds.get(b.get("id"))
        if not o or o.get("type") != b.get("type") or not isinstance(o.get("data"), dict) or not isinstance(b.get("data"), dict):
            continue
        if "inline2" not in caps:
            kept = _keep_fields(o["data"], b["data"])
            if kept != b["data"]:
                b["data"], changed = kept, True
        if "cols6" not in caps and b.get("type") == "columns":
            oc, nc = o["data"].get("cols"), b["data"].get("cols")
            if isinstance(oc, list) and isinstance(nc, list) and len(nc) == 4 and 5 <= len(oc) <= COLS_MAX:
                have = {x.get("id") for c in nc if isinstance(c, dict) for x in (c.get("blocks") or []) if isinstance(x, dict)}
                for c in oc[4:]:                       # fehlende Spalten anhaengen - ohne Bloecke, die schon woanders stehen
                    if isinstance(c, dict):
                        c = dict(c, blocks=[x for x in (c.get("blocks") or []) if isinstance(x, dict) and x.get("id") not in have])
                        nc.append(c)
                for c in nc:
                    if isinstance(c, dict):
                        c.pop("w", None)               # Breiten passen nicht mehr: wieder gleich breit
                changed = True
    return json.dumps(nd, ensure_ascii=False) if changed else new_body


TAG_MAX_LEN, TAG_MAX_COUNT = 30, 20
_TAG_RE = re.compile(r"[\w\- .äöüÄÖÜß]+")


def _tags(v):
    """Liste von Tags -> bereinigt, ohne Dubletten (Gross/Klein egal), max. 20 a 30 Zeichen."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(t("Tags müssen eine Liste sein"))
    out, seen = [], set()
    for x in v:
        s = re.sub(r"\s+", " ", str(x)).strip().lstrip("#")[:TAG_MAX_LEN]
        if not s:
            continue
        if not _TAG_RE.fullmatch(s):
            raise ValueError(t("unerlaubte Zeichen im Tag"))
        if s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    if len(out) > TAG_MAX_COUNT:
        raise ValueError(t("maximal {n} Tags", n=TAG_MAX_COUNT))
    return out


def _own_image(c, uid, url):
    """True, wenn url = /files/<id> ein eigenes, am Inhalt als Bild erkanntes Upload ist."""
    m = _FILE_URL.fullmatch(str(url or ""))
    r = m and c.execute("SELECT is_image FROM file WHERE id=? AND uid=?", (m.group(1), uid)).fetchone()
    return bool(r and r[0])


def _icon(v, c=None, uid=None):
    """Emoji (max. 8 Zeichen) oder eigenes hochgeladenes Bild (/files/<id>)."""
    s = str(v or "").strip()
    if s.startswith("/files/"):
        if c is None or not _own_image(c, uid, s):
            raise ValueError(t("Icon-Bild muss ein eigenes hochgeladenes Bild sein"))
        return s
    s = s[:NOTE_ICON_MAX]
    if any(ch in s for ch in "<>&\"'"):
        raise ValueError
    return s


def _may_edit(acc, role):
    """Fuer _rel_sync: der Eigentuemer darf alle Zielzeilen nachziehen, andere nur die, die sie bearbeiten duerfen."""
    if role == "owner":
        return None
    return lambda rid: RANK.get(acc.role(rid), 0) >= RANK["edit"]


def _rel_check_for(c, owner, acc, role, old_props=None):
    """Relationen: nur Eintraege, die es in der Ziel-Datenbank des Eigentuemers gibt - fuer Nicht-Eigentuemer zusaetzlich
    nur solche, die sie selbst sehen duerfen (keine IDs aus nicht geteilten Datenbanken). Schon verknuepfte Eintraege
    bleiben erhalten, auch wenn die bearbeitende Person sie nicht sieht."""
    base = _rel_check(c, owner)
    if role == "owner":
        return base
    keep = {x for v in (old_props or {}).values() if isinstance(v, list) for x in v if isinstance(x, int)}
    return lambda target, ids: {i for i in base(target, ids) if i in keep or acc.role(i)}


def _val_checks(c, owner, uid, nid):
    """2.5 (Paket 1): Pruefer fuer Personen-/Datei-Werte einer Zeile (nid: Datenbank bzw. Zeile - Zugriff gilt geerbt).
    -> dict fuer _clean_props(people=..., files=...)."""
    from saga import people

    def ppl(p, v, old):
        return people.clean_values(c, owner, nid, uid, v, old)

    def fls(ids):
        if not ids:
            return set()
        ids = sorted(ids)
        return {r[0] for r in c.execute(f"SELECT id FROM file WHERE uid=? AND id IN ({','.join('?' * len(ids))})", (owner, *ids))}
    return {"people": ppl, "files": fls}


@router.get("/api/notes")
def api_notes(request: Request):
    """Seitenbaum (ohne Inhalte)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    q = request.query_params
    if any(k in q for k in ("rows", "fields", "delta", "since")):    # schlank/Abgleich (2.2, saga/treesync.py) - nur auf Wunsch
        rows = q.get("rows") if q.get("rows") in ("0", "only") else "1"
        with _db() as c:
            out = treesync.tree(c, uid, rows=rows, fields=treesync.parse_fields(q.get("fields")),
                                delta=q.get("delta") == "1", since=q.get("since"))
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    with _db() as c:
        _purge_old(c, uid, every=3600)             # Papierkorb-Frist: hoechstens stuendlich je Konto (nicht bei jedem Baum)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, archived, locked FROM note WHERE uid=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                         (uid,)).fetchall()
        sh = shared_ids(c, uid)                    # Symbol im Baum: Seite ist (selbst) freigegeben
        un = unread_notes(c, uid, [uid])           # Punkt im Baum: neue Kommentare von anderen
    return JSONResponse([dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10]), shared=r[0] in sh, unread=r[0] in un) for r in rows],
                        headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}")
def api_note_get(nid: int, request: Request):
    """Seite lesen (ab Rolle 'view'). Fuer alle: role, owner{name, username, me}, shared, Kommentar-Zaehler."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return note_get(uid, nid)


def note_get(uid, nid):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person '@name@host')."""
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        r = c.execute(f"SELECT {NOTE_META_COLS}, body, format, schema, props, cover, cover_pos, created, wide, rev, archived, locked, updated_by, created_by, "
                      "font, small FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        mine = role == "owner"
        pub = mine and c.execute("SELECT p.slug, n.name FROM note_publish p LEFT JOIN public_name n ON n.uid=p.uid "
                                 "WHERE p.note_id=? AND p.uid=? AND p.active=1", (nid, owner)).fetchone()
        via = mine and published_via(c, owner, nid)      # ueber eine Elternseite mit "Unterseiten mit veroeffentlichen"
        shared = bool(c.execute("SELECT 1 FROM note_share WHERE note_id=? LIMIT 1", (nid,)).fetchone()) if mine else True
        per, n_open, n_unread = comment_counts(c, uid, nid)
        n_res = c.execute("SELECT COUNT(*) FROM note_comment WHERE note_id=? AND thread_id IS NULL AND resolved_at IS NOT NULL",
                          (nid,)).fetchone()[0]
        nm = names(c, [owner, r[20], r[21]])
        body = ensure_ids(r[9], r[10], nid)       # 2.5 (P3-E1): alte Staende ohne eindeutige Block-IDs (deterministisch)
        if not mine:                               # Verweise auf Seiten ohne Zugriff: Titel nie ausliefern
            body = mask_links(body, r[10], viewer(acc))
            if is_remote(uid):                     # Nextcloud-Bloecke: Autor fuer andere Instanzen nur als Platzhalter
                body = nextcloud.mask_authors(body, r[10])
            parent_hidden = r[3] is not None and not acc.role(r[3])
        if not is_remote(uid):                     # 2.4 (N1): Autor fremder Nextcloud-Bloecke lokal als Handle
            body = nextcloud.mask_local(body, r[10], uid)
        if r[10] == "blocks":                      # 2.5 (P6-6): Kopien synchronisierter Bloecke je Betrachter einsetzen
            body = synced.expand(c, body, nid, uid, acc)
        ment = mentions.refs_in(body, r[10])       # 2.5 (P6-5): Namen der Erwaehnungen (Woerterbuch people)
        ment_pm = mentions.people_map(c, list(ment), uid) if ment else {}
        row_fx = _row_formulas(c, owner, uid, r)    # 2.5: Formeln/Rollups einer Datenbank-Zeile
        blocked = False
        if r[3] is not None:                       # 2.5: Zeile mit Abhaengigkeit - "blockiert" (nur gelesen)
            par_s = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (r[3], owner)).fetchone()
            if par_s and '"blocked_by"' in (par_s[0] or ""):
                tmp = [{"id": r[0], "props": _json_load(r[12], {})}]
                dbcalc.mark_blocked(c, owner, r[3], _json_load(par_s[0], {"props": []}), tmp)
                blocked = bool(tmp[0].get("blocked"))
        row_vals = _row_values(c, owner, uid, r, row_fx)
    d = _note_meta(r)
    d["published"] = bool(pub)                     # Seitenkopf: Hinweis "Oeffentlich" (Einzelheiten: /api/notes/{nid}/publish)
    d["public_url"] = f"{BASE_URL}/@{pub[1]}/{pub[0]}" if pub and pub[1] else None
    d["published_via"] = via or None
    d.update(body=body, format=r[10] or "text", props=_json_load(r[12], {}), cover=r[13] or "",
             cover_pos=50.0 if r[14] is None else r[14], created=r[15], wide=bool(r[16]), rev=r[17], archived=bool(r[18]), locked=bool(r[19]),
             font=r[22] if r[22] in FONTS else None, small=bool(r[23]))       # 2.5 (G9): Schrift und kleiner Text
    if row_fx is not None:
        d["props"] = row_fx["props"]
        if row_fx.get("fx_err"):
            d["fx_err"] = row_fx["fx_err"]
    if row_vals is not None:                       # 2.5: Personen (Handles + people) und Dateien (Groesse/MIME)
        d["props"] = row_vals["props"]
        if row_vals.get("people"):
            d["people"] = row_vals["people"]
    if ment_pm:                                    # 2.5 (P6-5): Erwaehnungen im Inhalt - dasselbe Woerterbuch
        d["people"] = dict(ment_pm, **(d.get("people") or {}))
    if d["kind"] == "database":
        d["schema"] = _json_load(r[11], {"props": []})
    if blocked:
        d["blocked"] = True
    # Teilen (1.3) - nur Erweiterungen, bestehende Felder bleiben wie sie sind
    d.update(role=role, owner=person(c, owner, uid, nm), shared=shared, comment_counts=per, comments_open=n_open,
             comments_unread=n_unread, comments_resolved=n_res, updated_by=person(c, r[20], uid, nm) if r[20] else None,
             created_by=person(c, r[21], uid, nm) if r[21] and r[21] != owner else None)      # angelegt von jemand anderem
    if not mine:
        d["pinned"] = False                        # Favoriten sind die des Eigentuemers
        d["body"] = body
        if parent_hidden:
            d["parent_id"] = None                  # Elternseite nicht freigegeben: nicht einmal ihre ID
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


def _row_formulas(c, owner, uid, r):
    """Zeile einer Datenbank mit Formeln/Rollups: {props, fx_err} mit den Rechten von uid - sonst None."""
    if r[3] is None:
        return None
    par = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (r[3], owner)).fetchone()
    schema = _json_load(par[0], {"props": []}) if par else None
    if not dbcalc.has_calc(schema):
        return None
    row = {"id": r[0], "title": r[1] or "", "props": _json_load(r[12], {}), "created": r[15], "updated": r[4]}
    dbcalc.fill(c, owner, schema, [row], viewer=uid)
    return row


def _row_values(c, owner, uid, r, row_fx=None):
    """Zeile mit Personen-/Datei-Eigenschaften: {props, people} fuer uid - sonst None."""
    if r[3] is None:
        return None
    par = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (r[3], owner)).fetchone()
    schema = _json_load(par[0], {"props": []}) if par else None
    if not dbvals.has_any(schema):
        return None
    row = {"props": dict(row_fx["props"]) if row_fx is not None else _json_load(r[12], {})}
    extra = dbvals.out_rows(c, owner, schema, [row], uid)
    return {"props": row["props"], "people": extra.get("people")}


def row_calc(c, owner, uid, nid):
    """Werte der Formeln/Rollups einer Zeile nach dem Speichern (PATCH-Antwort 'calc': {pid: wert}, 'fx_err') oder None."""
    r = c.execute("SELECT n.id, n.title, n.props, n.created, n.updated, d.schema FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                  "WHERE n.id=? AND n.uid=? AND d.kind='database'", (nid, owner)).fetchone()
    schema = _json_load(r[5], {"props": []}) if r else None
    if not dbcalc.has_calc(schema) and not dbvals.has_any(schema):
        return None
    row = {"id": r[0], "title": r[1] or "", "props": _json_load(r[2], {}), "created": r[3], "updated": r[4]}
    out = {}
    if dbcalc.has_calc(schema):
        dbcalc.fill(c, owner, schema, [row], viewer=uid)
        pids = [p["id"] for p in schema.get("props", []) if isinstance(p, dict) and p.get("type") in ("formula", "rollup")]
        out.update(calc={k: row["props"].get(k) for k in pids}, fx_err=row.get("fx_err") or {})
    if dbvals.has_any(schema):           # 2.5: Personen/Dateien der Zeile, wie gespeichert (Handles + people, Datei-Angaben)
        extra = dbvals.out_rows(c, owner, schema, [row], uid)
        vp = [p["id"] for p in schema.get("props", []) if isinstance(p, dict) and p.get("type") in ("person", "files")]
        out["values"] = {k: row["props"].get(k) for k in vp}
        if extra.get("people"):
            out["people"] = extra["people"]
    return out


# 2.3 (saga.fed_pages): Unterseite in einer Seite einer anderen Saga-Instanz (parent_id negativ) - fn(uid, b, origin)
FED_CREATE = None


@router.post("/api/notes")
async def api_note_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    pid_raw = b.get("parent_id")
    if FED_CREATE is not None and isinstance(pid_raw, (int, str)) and not isinstance(pid_raw, bool) and str(pid_raw).startswith("-"):
        return await run_in_threadpool(FED_CREATE, uid, b, origin_of(request))
    return note_create(uid, b, origin_of(request))


def note_create(uid, b, origin=None):
    """Neue Seite bzw. Unterseite (b: parent_id, title, icon, tags, kind, props) - fuer die Weboberflaeche und die
    Foederation (uid = fremde Person '@name@host': nur als Unterseite einer Seite, die sie bearbeiten darf)."""
    owner, role = uid, "owner"
    now = time.time()
    if is_remote(uid) and b.get("parent_id") is None:      # fremde Person: nie eine eigene Seite hier
        return _bad("not found", 404)
    with _db() as c, c:
        acc = Access(c, uid)
        if b.get("parent_id") is not None:
            # Unterseite: braucht 'edit' auf der Elternseite und gehoert deren Eigentuemer (zaehlt bei ihm)
            try:
                pid = int(b["parent_id"])
            except (TypeError, ValueError, OverflowError):
                return _bad(t("ungültige Angabe"))
            owner, role, err = access(c, uid, pid, "edit", acc)
            if err is not None:
                return err
        lim = note_limit_error(c, owner, pid if b.get("parent_id") is not None else None)
        if lim:
            return _bad(lim, 409)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
            icon = _icon(b.get("icon"), c, owner)
            tags = _tags(b.get("tags"))
            title = _title_in(b.get("title"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError):
            return _bad(t("ungültige Angabe"))
        kind = "database" if b.get("kind") == "database" else "page"
        schema = json.dumps(_default_schema(account_lang(owner)), ensure_ascii=False) if kind == "database" else None
        props = None
        if parent and b.get("props"):
            ps = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (parent, owner)).fetchone()
            if ps:
                try:
                    props = _clean_props(_json_load(ps[0], {"props": []}), b["props"], check=_rel_check_for(c, owner, acc, role),
                                         **_val_checks(c, owner, uid, parent))
                    _self_rel_check(c, owner, _json_load(ps[0], {"props": []}), None, props)
                    props = json.dumps(props, ensure_ascii=False)
                except (ValueError, TypeError) as ex:
                    return _bad(user_error(ex, t("ungültige Werte")))
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated, updated_by, created_by) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, json.dumps({"blocks": []}), "blocks", icon, parent, now,
                         json.dumps(tags, ensure_ascii=False), kind, schema, props, now, now, uid, uid))
        if props and ps:                                   # zweiseitige Relationen: Gegenseite nachziehen
            _rel_sync(c, owner, _json_load(ps[0], {"props": []}), cur.lastrowid, {}, json.loads(props), _may_edit(acc, role))
            dbvals.assigned(c, owner, _json_load(ps[0], {"props": []}), cur.lastrowid, {}, json.loads(props), uid)   # 2.5: db.assigned
        if parent:
            announce_row(c, owner, cur.lastrowid, uid, now, origin)      # neue Zeile (BUG-049)
        tree_changed(c, owner, [cur.lastrowid], "created", origin)
        return JSONResponse({"id": cur.lastrowid, "updated": now})


def _title_in(v):
    """Seitentitel aus einer Anfrage: null -> leer, Liste/Objekt/Wahrheitswert -> Fehler statt "None"/"['x']" (BUG-083);
    Steuer-, Bidi- und unsichtbare Zeichen raus (BUG-038)."""
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise ValueError(t("ungültige Angabe"))
    return clean_line(v).strip()[:NOTE_TITLE_MAX]


class _Reply(Exception):
    """Fehlerantwort mitten in einer Transaktion: als Ausnahme geworfen, damit 'with c:' zurueckrollt
    (ein 'return' wuerde die bis dahin gemachten Aenderungen committen - Teil-Commit trotz 4xx)."""

    def __init__(self, resp):
        super().__init__()
        self.resp = resp


SHRINK_MIN, SHRINK_RATIO = 400, 0.5     # Sicherheitsnetz: Version erzwingen, wenn eine Seite (> 400 Zeichen) leer/viel kleiner wird


def _shrinks(old_body, body, fmt):
    if len(old_body or "") <= SHRINK_MIN:
        return False
    if fmt == "blocks":
        empty = not _json_load(body, {}).get("blocks")
    else:
        empty = not body.strip()
    return empty or len(body) < len(old_body) * SHRINK_RATIO


@router.patch("/api/notes/{nid}")
async def api_note_update(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    origin = origin_of(request)
    try:                                     # Datenbank-Arbeit und Bereinigung im Threadpool (Event-Loop bleibt frei)
        return await run_in_threadpool(_note_update, uid, nid, b, origin, client_caps(request))
    except _Reply as r:
        return r.resp


OWNER_FIELDS = ("pinned", "template", "locked", "parent_id")      # Favorit, Vorlage, Sperre, Verschieben: nur der Eigentuemer


def _conflict(c, uid, nid, rev):
    """409 mit Revision und - wenn es jemand anderes war - wer zuletzt geaendert hat ("X hat diese Seite gerade geaendert")."""
    r = c.execute("SELECT updated_by FROM note WHERE id=?", (nid,)).fetchone()
    by = r[0] if r and r[0] and r[0] != uid else None
    return JSONResponse({"error": "conflict", "rev": rev, "by": names(c, [by]).get(by) if by else None}, status_code=409)


def _merge_req(c, uid, nid, b, old, want):
    """2.5 (P3 Stufe 1, saga.merge): PATCH mit merge: 1 und veralteter bzw. mit bases gemeldeter Basis -> (neues b, info)
    mit zusammengefuehrtem Inhalt/Titel, oder None (-> 409 wie bisher: Basis nicht mehr da, Text-Notiz, Unsinn).
    "mine" laeuft vorher durch dieselbe Bereinigung wie beim Speichern; verdeckte Verweistexte (Nicht-Eigentuemer) werden
    gegen die Basis wieder eingesetzt, damit nur echte Aenderungen zaehlen."""
    from saga import merge
    if old[2] != "blocks" or b.get("format", "blocks") != "blocks" or not 0 <= want <= old[3]:
        return None
    base = base_of(c, nid, want, sent=b.get("base") == "sent")
    if base is None or base[2] != "blocks":
        return None
    out, info = dict(b), {"from": want}
    acc = Access(c, uid)
    owner = c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone()[0]
    try:
        theirs = json.loads(ensure_ids(old[1], "blocks", nid))
        base_doc = json.loads(ensure_ids(base[1], "blocks", nid))
        if "body" in b:
            raw = str(b["body"])
            mine_s, _ = clean_blocks(raw, NOTE_BLOCKS_MAX + 64 * raw.count("/notes#"))
            if owner != uid:
                mine_s = restore_links(base[1], mine_s, viewer(acc), title_of(c, owner))
            mine = json.loads(mine_s)
            bases = {}
            if isinstance(b.get("bases"), dict):
                cache = {}
                for bid, r in list(b["bases"].items())[:500]:
                    if not isinstance(bid, str) or isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= old[3]:
                        continue
                    if r not in cache:
                        x = base_of(c, nid, r, sent=True)
                        cache[r] = {k.get("id"): k for k in _iter_blocks(json.loads(ensure_ids(x[1], "blocks", nid)).get("blocks") or [])} \
                            if x and x[2] == "blocks" else {}
                    if bid in cache[r]:
                        bases[bid] = cache[r][bid]
            by = {"by": uid if is_remote(uid) else local_ref(uid), "name": names(c, [uid]).get(uid) or ""}
            doc, mi = merge.merge_doc(base_doc, mine, theirs, bases, by, time.time())
            out["body"] = json.dumps(doc, ensure_ascii=False)
            info.update(mi)
            info["sent"] = mine_s
        else:
            info.update(conflicts=[], restored=[], lost=[])
        if "title" in b:
            info["sent_title"] = _title_in(b["title"])
            out["title"], info["title_lost"] = merge.merge_title(base[0] or "", info["sent_title"], old[0] or "")
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return None
    out.pop("bases", None)
    out.pop("base", None)
    return out, info


def _merged_view(uid, nid):
    """Antwort auf ein zusammengefuehrtes Speichern: Inhalt, Titel und Revision so, wie GET /api/notes/{id} sie dieser
    Person zeigt (Maskierung fuer Nicht-Eigentuemer, Nextcloud-Autoren, Block-IDs)."""
    r = note_get(uid, nid)
    try:
        d = json.loads(r.body)
    except (ValueError, AttributeError):
        return {}
    return {k: d[k] for k in ("body", "format", "title", "rev", "updated") if k in d} if r.status_code == 200 else {}


def _note_update(uid, nid, b, origin=None, caps=CLIENT_CAPS):
    """caps: Faehigkeiten des Clients bzw. der Gegenstelle (client_caps; Foederation: Faehigkeiten des Peers)."""
    sets, args, merged, row_changed, sync_plan, sync_res = [], [], None, None, None, None
    with _db() as c, c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "edit", acc)
        if err is not None:
            return err
        if role != "owner" and any(k in b for k in OWNER_FIELDS):
            return _bad(t("Das darf nur der Eigentümer der Seite."), 403)
        # 2.5 (P3): Titel/Inhalt - Zeile bis zum Ende der Transaktion sperren: gleichzeitige Speichervorgaenge derselben
        # Seite laufen nacheinander, der zweite sieht den neuen Stand und fuehrt zusammen (statt 409 aus dem Wettlauf)
        lock = " FOR UPDATE" if ("body" in b or "title" in b) else ""
        old = c.execute("SELECT title, body, format, rev, locked, updated_by FROM note WHERE id=? AND uid=? AND deleted_at IS NULL" + lock,
                        (nid, owner)).fetchone()
        if not old:
            return _bad("not found", 404)
        if old[4] and b.get("locked") is not False and ("body" in b or "title" in b):     # gesperrt: Inhalt/Titel bleiben unveraendert
            return JSONResponse({"error": t("Seite ist gesperrt – erst entsperren")}, status_code=423)
        # Konflikterkennung: wer Titel/Inhalt speichert und die gekannte Revision mitschickt, ueberschreibt
        # keine zwischenzeitliche Aenderung (anderer Tab, Peek, andere Person). Ohne 'rev' wie bisher.
        content = "body" in b or "title" in b
        check_rev = content and b.get("rev") is not None
        if check_rev:
            try:
                want = int(b["rev"])
            except (TypeError, ValueError, OverflowError):
                return _bad(t("ungültige Revision"))
            if want != old[3]:
                # 2.5 (P3 Stufe 1): Client kann zusammenfuehren (merge: 1) und die Basis liegt noch vor -> blockweise
                # zusammenfuehren statt 409. Sonst wie bisher 409 (aeltere Apps, Basis zu alt, Text-Notiz).
                mg = _merge_req(c, uid, nid, b, old, want) if b.get("merge") else None
                if mg is None:
                    return _conflict(c, uid, nid, old[3])
                b, merged = mg
        # "Meine Version behalten" (overwrite, ohne rev): trotzdem nur gegen die eben gelesene und als Version gesicherte
        # Fassung schreiben - eine Aenderung, die genau dazwischen eintrifft, wuerde sonst ungesichert ueberschrieben
        # Speichern ohne rev (aeltere Apps, Zeilen umbenennen) ueber den Stand einer anderen Person: vorher sichern
        if check_rev and merged is None and b.get("merge") and "body" in b and ((isinstance(b.get("bases"), dict) and b["bases"])
                                                                              or b.get("base") == "sent"):
            mg = _merge_req(c, uid, nid, b, old, old[3])        # rev aktuell, aber einzelne Bloecke beruhen auf aelteren Staenden
            if mg is not None:
                b, merged = mg
        foreign = content and not check_rev and bool(old[5]) and old[5] != uid
        atomic = check_rev or (content and bool(b.get("overwrite"))) or foreign
        shrink, srev, moved_from = False, None, None
        try:
            if "pinned" in b:
                sets.append("pinned=?"); args.append(1 if b["pinned"] else 0)
            if "template" in b:
                sets.append("is_template=?"); args.append(1 if b["template"] else 0)
            if "wide" in b:
                sets.append("wide=?"); args.append(1 if b["wide"] else 0)
            if "font" in b:                       # 2.5 (G9): "default"/null = Standard, sonst nur serif/mono
                if b["font"] not in FONTS + ("default", None):
                    raise ValueError(t("unbekannte Schriftart"))
                sets.append("font=?"); args.append(b["font"] if b["font"] in FONTS else None)
            if "small" in b:
                sets.append("small=?"); args.append(1 if b["small"] else 0)
            if "locked" in b:
                sets.append("locked=?"); args.append(1 if b["locked"] else 0)
            if "schema" in b:
                me = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
                if me[0] != "database":
                    raise ValueError(t("nur Datenbanken haben ein Schema"))
                new_schema = _clean_schema(b["schema"])
                old_s = _json_load((c.execute("SELECT schema FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone() or [None])[0], {"props": []})
                # BUG-001: Spalten/Optionen, die der Client (Stand srev) nicht kennen konnte, nicht loeschen
                _schema_merge(old_s, new_schema, _schema_srev(b["schema"]), uid)
                formula_check(new_schema, old_s)       # 2.5: neue/geaenderte Formeln -> 400 mit Position
                # Standardvorlage: Clients, die sie nicht kennen (aeltere Apps), schicken den Schluessel nicht mit -> behalten;
                # nur eine Vorlage DIESER Datenbank zaehlt, null/0 hebt sie auf
                if ("default_tpl" not in b["schema"] or role != "owner") and old_s.get("default_tpl"):
                    new_schema["default_tpl"] = old_s["default_tpl"]     # fix27 E1: Standard setzt nur der Eigentuemer
                elif role != "owner":
                    new_schema.pop("default_tpl", None)
                if new_schema.get("default_tpl") and not c.execute(
                        "SELECT 1 FROM note WHERE id=? AND uid=? AND parent_id=? AND is_template=1 AND deleted_at IS NULL",
                        (new_schema["default_tpl"], owner, nid)).fetchone():
                    new_schema.pop("default_tpl")
                if "tasks" not in b["schema"] and old_s.get("tasks"):    # 2.5 (Paket 7): Clients ohne Aufgabenquellen behalten sie
                    tm = clean_tasks_map(old_s["tasks"], new_schema["props"])
                    if tm:
                        new_schema["tasks"] = tm
                if role != "owner" and bool(old_s.get("locked")) != bool(new_schema.get("locked")):
                    raise _Reply(_bad(t("Das darf nur der Eigentümer der Seite."), 403))     # Sperre der Datenbank: wie die Seiten-Sperre
                if old_s.get("locked") and new_schema.get("locked"):     # gesperrt: nur Entsperren ist erlaubt
                    strip = lambda s: json.dumps([s.get("props"), s.get("views"), s.get("title_name")], sort_keys=True)
                    if strip(new_schema) != strip(_clean_schema(old_s)):
                        raise ValueError(t("Die Datenbank ist gesperrt – zum Ändern erst unter „Ansicht“ entsperren"))
                old_by = {p["id"]: p for p in old_s.get("props", [])}
                old_rel = {k: p.get("target") for k, p in old_by.items() if p.get("type") == "relation"}

                def rel_ok(target):
                    """Relation anlegen/koppeln/entkoppeln schreibt in die Ziel-Datenbank: Nicht-Eigentuemer brauchen dort 'edit'."""
                    if role != "owner" and RANK.get(acc.role(target), 0) < RANK["edit"]:
                        raise _Reply(_bad(t("Diese Relation kann nur der Eigentümer ändern."), 403))
                for rp in new_schema["props"]:          # Relation nur auf eigene, aktive Datenbanken
                    if rp["type"] == "relation" and old_rel.get(rp["id"]) != rp["target"]:
                        rel_ok(rp["target"])
                        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL",
                                         (rp["target"], owner)).fetchone():
                            raise ValueError(t("Ziel-Datenbank der Relation nicht gefunden"))
                # Zweiseitige Relationen: Kopplung (pair) stellt NUR der Server her - vom Browser nur uebernehmen, was schon so bestand
                raw_by = {str(p.get("id")): p for p in b["schema"].get("props", []) if isinstance(p, dict)}
                my_title = c.execute("SELECT title FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                self_new = []                                # 2.5: neue Gegenspalten im selben Schema (Selbst-Relation)
                for rp in list(new_schema["props"]):
                    if rp["type"] != "relation":
                        continue
                    prev = old_by.get(rp["id"]) or {}
                    if prev.get("pair") and prev.get("target") == rp["target"]:
                        rp["pair"] = prev["pair"]                # bestehende Kopplung bleibt, egal was der Browser schickt
                        if prev.get("role") in REL_ROLES:
                            rp["role"] = prev["role"]            # Rolle (Unteraufgaben/Abhaengigkeiten) ebenso
                        else:
                            rp.pop("role", None)
                    else:
                        rp.pop("pair", None)
                    if rp["target"] == nid:                      # 2.5: Selbst-Relation - beide Spalten in DIESEM Schema
                        want = raw_by.get(rp["id"], {}).get("two_way")
                        if rp.get("pair"):
                            if want is False:                    # zweiseitig aus: Gegenspalte (hier) samt Werten weg
                                mate = rp.pop("pair")
                                rp.pop("role", None)
                                new_schema["props"] = [x for x in new_schema["props"] if x["id"] != mate]
                            continue
                        if rp["id"] in {x[1] for x in self_new}:
                            continue
                        if want is True and not prev.get("pair"):
                            if len(new_schema["props"]) >= 30:
                                raise ValueError(t("Die Datenbank hat schon 30 Eigenschaften"))
                            role = rp.get("role") if rp.get("role") in REL_ROLES else None
                            used = {x["id"] for x in new_schema["props"]} | set(old_by)
                            mid = next(x for x in ("r" + secrets.token_hex(4) for _ in range(50)) if x not in used)
                            mate = {"id": mid, "type": "relation", "target": nid, "pair": rp["id"],
                                    "name": {"parent": t("Unteraufgaben"), "children": t("Übergeordnet"), "blocked_by": t("Blockiert"),
                                             "blocking": t("Blockiert durch")}.get(role, rp["name"])}
                            if role:
                                mate["role"] = REL_ROLES[role]
                            else:
                                rp.pop("role", None)
                            rp["pair"] = mid
                            new_schema["props"].append(mate)
                            self_new.append((rp["id"], mid))
                        elif not rp.get("pair"):
                            rp.pop("role", None)
                        continue
                    rp.pop("role", None)                         # Rollen nur fuer Selbst-Relationen
                    was_self = prev.get("pair") and prev.get("target") == nid
                    if was_self:                                 # war Selbst-Relation: Gegenspalte hier entfernen
                        new_schema["props"] = [x for x in new_schema["props"] if x["id"] != prev["pair"]]
                    if prev.get("pair") and prev.get("target") != rp["target"] and not was_self:      # Ziel gewechselt: alte Gegenspalte entfernen
                        rel_ok(prev["target"])
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], True)
                    want = raw_by.get(rp["id"], {}).get("two_way")
                    if want is True and not rp.get("pair") and rp["target"] != nid:
                        rel_ok(rp["target"])
                        rp["pair"] = _rel_link(c, owner, nid, my_title, rp, actor=uid)
                    elif want is False and rp.get("pair"):
                        rel_ok(rp["target"])
                        _rel_unlink(c, owner, nid, rp["target"], rp.pop("pair"), True)
                new_ids = {p["id"] for p in new_schema["props"]}
                for p_ in new_schema["props"]:                  # Selbst-Relation: Gegenspalte geloescht -> hier nur entkoppeln
                    if p_["type"] == "relation" and p_["target"] == nid and p_.get("pair") and p_["pair"] not in new_ids:
                        p_.pop("pair")
                        p_.pop("role", None)
                for k, prev in old_by.items():                                    # Spalte geloescht: Gegenseite bleibt, nur entkoppelt
                    if prev.get("target") == nid:
                        continue                                                  # Selbst-Relation: oben erledigt
                    if k not in new_ids and prev.get("type") == "relation" and prev.get("pair"):
                        rel_ok(prev["target"])
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], False)
                parents = {x["id"] for x in new_schema["props"] if x["type"] == "relation" and x.get("role") == "parent"}
                for v_ in new_schema.get("views") or []:        # Unteraufgaben-Ansicht nur mit (noch) gekoppelter Eltern-Spalte
                    if isinstance(v_.get("sub"), dict) and v_["sub"].get("parent") not in parents:
                        v_.pop("sub")
                # Gegenspalten (_rel_link/_rel_unlink) koennen dieses Schema nicht veraendert haben (nur Ziel-Datenbanken)
                _schema_stamp(old_s, new_schema, uid)
                srev = new_schema["srev"]
                sets.append("schema=?"); args.append(json.dumps(new_schema, ensure_ascii=False))
                # Werte, die nach Typ-/Optionsaenderung nicht mehr passen, aus allen Zeilen entfernen
                # (Mehrfachauswahl: nur geloeschte Optionen herausfiltern, nicht die ganze Liste verwerfen)
                by_id = {p["id"]: p for p in new_schema["props"]}
                for rid, rp in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=?", (owner, nid)).fetchall():
                    old_p, keep = _json_load(rp, {}), {}
                    for k, v in old_p.items():
                        pr = by_id.get(k)
                        if pr and pr["type"] == "multi_select" and isinstance(v, list):
                            v = [x for x in v if x in {o["id"] for o in pr.get("options", [])}]
                        try:
                            keep.update(_clean_props(new_schema, {k: v}, check=_rel_check(c, owner), trusted=True))   # Relation: nur IDs aus dem (neuen) Ziel
                        except (ValueError, TypeError):
                            pass
                    if keep != old_p:
                        c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(keep, ensure_ascii=False), rid, owner))
                for a_, b_ in self_new:                         # neue Selbst-Kopplung: bestehende Verknuepfungen spiegeln
                    _self_rel_backfill(c, owner, nid, a_, b_)
            if "props" in b:
                ps = c.execute("SELECT d.schema, n.props FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                               "WHERE n.id=? AND n.uid=? AND d.kind='database'", (nid, owner)).fetchone()
                if not ps:
                    raise ValueError(t("Seite ist keine Datenbank-Zeile"))
                new_props = _clean_props(_json_load(ps[0], {"props": []}), b["props"], _json_load(ps[1], {}), check=_rel_check_for(c, owner, acc, role, _json_load(ps[1], {})),
                                         **_val_checks(c, owner, uid, nid))
                _self_rel_check(c, owner, _json_load(ps[0], {"props": []}), nid, new_props)     # 2.5: keine Kreise/Selbstbezug
                _rel_sync(c, owner, _json_load(ps[0], {"props": []}), nid, _json_load(ps[1], {}), new_props, _may_edit(acc, role))
                dbvals.assigned(c, owner, _json_load(ps[0], {"props": []}), nid, _json_load(ps[1], {}), new_props, uid)   # 2.5: db.assigned
                sets.append("props=?"); args.append(json.dumps(new_props, ensure_ascii=False))
                old_p = _json_load(ps[1], {})
                row_changed = sorted(k for k in set(old_p) | set(new_props) if old_p.get(k) != new_props.get(k))
            if "title" in b:
                sets.append("title=?"); args.append(_title_in(b["title"]))
            if "icon" in b:
                sets.append("icon=?"); args.append(_icon(b["icon"], c, owner))
            if "cover" in b:
                cv = b["cover"]
                if cv in (None, ""):
                    sets.append("cover=?"); args.append(None)
                elif _own_image(c, owner, cv):
                    sets.append("cover=?"); args.append(cv)
                else:
                    raise ValueError(t("Titelbild muss ein eigenes hochgeladenes Bild sein"))
            if "cover_pos" in b:
                sets.append("cover_pos=?"); args.append(max(0.0, min(100.0, float(b["cover_pos"]))))
            if "tags" in b:
                sets.append("tags=?"); args.append(json.dumps(_tags(b["tags"]), ensure_ascii=False))
            if "body" in b:
                fmt = b.get("format", "blocks")
                if fmt == "blocks":
                    raw = str(b["body"])
                    if role != "owner":
                        # verdeckte Verweistexte (Seiten ohne Zugriff) wieder einsetzen; die Laenge zaehlt erst danach
                        # (der Platzhalter kann laenger sein als der Titel - Spielraum je Verweis)
                        body, _ = clean_blocks(raw, NOTE_BLOCKS_MAX + 64 * raw.count("/notes#"))
                        body = restore_links(old[1] if old[2] == "blocks" else "", body, viewer(acc), title_of(c, owner))
                        if len(body) > NOTE_BLOCKS_MAX:
                            raise TooLarge(t("Seite zu groß"))
                    else:
                        body, _ = clean_blocks(raw)
                    if old[2] == "blocks":                 # 2.5 (P6-0): aeltere Clients verlieren keine neuen Formen/Spalten
                        body = legacy_keep(old[1], body, caps)
                    body = mentions.normalize(c, body)     # 2.5 (P6-5): Prinzipale dieser Instanz -> Handle
                    body = mentions.strip_foreign(c, owner, nid, old[1] if old[2] == "blocks" else "", body)
                    # 2.5 (P6-6): Kopien synchronisierter Bloecke ohne Inhalt speichern, Durchschreiben ins Original vormerken
                    body, sync_plan = synced.prepare(c, owner, nid, uid, old[1] if old[2] == "blocks" else "", body, caps, acc)
                    _check_file_refs(owner, json.loads(body)["blocks"])
                    if '"buttons"' in body or '"sig"' in body:   # Schaltflaechen mit Vorlage: Signatur setzt nur der Server
                        from saga.databases import sign_buttons
                        body = sign_buttons(body, nid, owner, uid, old[1] if old[2] == "blocks" else None)
                    if '"nextcloud"' in body:          # Nextcloud-Bloecke: author/sig setzt nur der Server (saga/nextcloud.py)
                        try:
                            body = nextcloud.check_body(uid, nid, old[1], old[2], body)
                        except nextcloud.Rejected as ex:
                            raise _Reply(_bad(str(ex), ex.status))
                elif fmt == "text":
                    body = str(b["body"])
                    if len(body) > NOTE_BODY_MAX:
                        raise _Reply(_bad(t("Notiz zu lang (max. {n} Zeichen)", n=NOTE_BODY_MAX), 413))
                else:
                    raise ValueError(t("unbekanntes Format"))
                sets += ["body=?", "format=?"]; args += [body, fmt]
                shrink = _shrinks(old[1], body, fmt)
            if "parent_id" in b:
                newpar = _check_parent(c, owner, nid, b["parent_id"])
                cur_par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                if newpar is not None:          # wie beim Verschieben: keine normale Seite in eine Datenbank
                    if c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (newpar, owner)).fetchone()[0] == "database" and cur_par != newpar:
                        raise ValueError(t("Seiten können nicht in eine Datenbank verschoben werden"))
                if cur_par != newpar:           # Kette VOR dem Verschieben: wer bisher Zugriff hatte (Live)
                    moved_from = share_targets(c, owner, [nid])
                sets.append("parent_id=?"); args.append(newpar)
        except LookupError:
            raise _Reply(_bad("not found", 404))
        except (ValueError, TypeError, OverflowError, AttributeError) as ex:
            raise _Reply(_bad(user_error(ex, t("ungültige Angabe")), 413 if isinstance(ex, TooLarge) else 400))
        if not sets:
            raise _Reply(_bad(t("nichts zu ändern")))
        now = time.time()
        if "body" in b or (content and (b.get("overwrite") or foreign)):
            # leer/viel kleiner: alten Stand immer sichern. "Meine Version behalten" nach einem Konflikt (overwrite) oder
            # Speichern ohne rev ueber fremden Stand: die ueberschriebene Fassung bleibt als Version erhalten - mit Autor
            # (erzwungene Sicherungen von Nicht-Eigentuemern gedrosselt, s. _snapshot)
            _snapshot(c, owner, nid, old, now, force=shrink or bool(b.get("overwrite")) or foreign
                      or bool(merged and (merged["conflicts"] or merged["lost"] or merged.get("title_lost"))), author=old[5], by=uid)
        if content:
            sets.append("rev=rev+1")
            sets.append("updated_by=?"); args.append(uid)            # fuer den Konflikt-Hinweis bei anderen
        # Atomar: mit Revision nur schreiben, wenn sie seit dem Lesen niemand erhoeht hat (sonst 409, alles zurueck)
        n = c.execute(f"UPDATE note SET {', '.join(sets)}, updated=? WHERE id=? AND uid=?" + (" AND rev=?" if atomic else ""),
                      (*args, now, nid, owner, *((old[3],) if atomic else ()))).rowcount
        if atomic and n == 0:
            cur = c.execute("SELECT rev FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
            raise _Reply(_conflict(c, uid, nid, cur[0] if cur else None))
        if content:                               # 2.5 (P3): Basisstand fuer spaeteres Zusammenfuehren
            cur = c.execute("SELECT title, body, format FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
            keep_base(c, nid, old[3] + 1, cur[0], cur[1], cur[2], now, (old[3], old[0], old[1], old[2]),
                      (merged.get("sent_title", cur[0]), merged.get("sent", cur[1])) if merged else None)
        if "body" in b:                           # 2.5 (P6-5): Erwaehnungen (Index, Mitteilung beim ersten Mal)
            mentions.sync(c, owner, nid, old[1], body, uid, old_fmt=old[2], new_fmt=fmt)
        if sync_plan is not None:                 # 2.5 (P6-6): Originale/Kopien nachfuehren, Kopien ins Original schreiben
            try:
                sync_res = synced.after_save(c, sync_plan, uid, old[3] + 1, now, origin)
            except ValueError as ex:
                raise _Reply(_bad(user_error(ex, t("ungültige Angabe"))))
        # Live-Kanal (1.4): wer die Seite gerade offen hat, laedt still neu bzw. bekommt einen Hinweis (nach dem Commit)
        bus.fire("note.saved", c, note_id=nid, owner=owner, rev=old[3] + (1 if content else 0), by=uid, updated=now, origin=origin)
        if "props" in b or "title" in b or "icon" in b:
            announce_row(c, owner, nid, uid, now, origin)     # Datenbank-Zeile: auch an Betrachter der Tabelle (BUG-049)
        if "body" in b:                     # 2.5: Webhooks page.updated / row.updated (Inhalt; Autosave fasst saga.webhooks zusammen)
            bus.fire("page.content", c, owner=owner, note_id=nid, by=uid)
        if row_changed or ("title" in b and _title_in(b["title"]) != old[0]):      # 2.5: Webhooks row.updated (geaenderte Eigenschaften)
            bus.fire("row.changed", c, owner=owner, note_id=nid, by=uid,
                     changed=(row_changed or []) + (["title"] if "title" in b and _title_in(b["title"]) != old[0] else []))
        # Seitenbaum (notes.tree): nur Felder, die /api/notes zeigt (bzw. Titelbild) - Titel nur, wenn er sich aendert
        if moved_from is not None:          # andere Elternseite: geerbte Freigaben aendern sich (alte UND neue Kette)
            announce_shared(c, owner, nid, [nid], moved_from)
        if ("parent_id" in b or any(k in b for k in ("icon", "cover", "tags", "pinned", "template", "locked"))
                or ("title" in b and _title_in(b["title"]) != old[0])):
            tree_changed(c, owner, [nid], "moved" if "parent_id" in b else "updated", origin, before=moved_from)
        rc = row_calc(c, owner, uid, nid) if ("props" in b or "title" in b) else None      # 2.5: Formeln/Rollups der Zeile
    out = {"ok": True, "updated": now, "rev": old[3] + (1 if content else 0)}
    if rc is not None:
        out.update(rc)
    if merged is not None:                   # 2.5 (P3): zusammengefuehrt - der Client uebernimmt Inhalt und Titel
        out["merged"] = {k: merged[k] for k in ("conflicts", "restored", "lost")}
        out["merged"]["from"] = merged["from"]
        if merged.get("title_lost"):
            out["merged"]["title_lost"] = True
        out.update(_merged_view(uid, nid))
    if srev is not None:
        out["srev"] = srev                   # neuer Schema-Stand (Client kann ihn fuer die naechste Aenderung uebernehmen)
    if sync_res:
        out["synced"] = sync_res             # 2.5 (P6-6): Durchschreiben je sid (ok | merged | denied | locked | gone)
    return JSONResponse(out)


@router.post("/api/notes/{nid}/move")
async def api_note_move(nid: int, request: Request):
    """Seite im Baum verschieben: neue Eltern (oder oberste Ebene) und Position unter den Geschwistern.
    Die Geschwister werden danach sauber durchnummeriert (sort = 1..n)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    return await run_in_threadpool(note_move, uid, nid, b, origin_of(request))


def _chain_shares(c, owner, nid):
    """Freigaben (Art, Ziel, Rolle) auf nid und allen Seiten darueber (Vererbung wie sharing.Access)."""
    chain = note_chain(c, owner, nid) if nid is not None else []
    if not chain:
        return set()
    return {tuple(r) for r in c.execute(f"SELECT target_type, target, role FROM note_share WHERE owner_uid=? AND note_id IN "
                                        f"({','.join('?' * len(chain))})", (owner, *chain)).fetchall()}


def _chain_pubs(c, owner, nid):
    """Veroeffentlichungen "mit Unterseiten" auf nid und allen Seiten darueber (vererben sich wie Freigaben)."""
    chain = note_chain(c, owner, nid) if nid is not None else []
    if not chain:
        return set()
    return {r[0] for r in c.execute(f"SELECT note_id FROM note_publish WHERE uid=? AND active=1 AND children=1 AND note_id IN "
                                    f"({','.join('?' * len(chain))})", (owner, *chain)).fetchall()}


def note_move(uid, nid, b, origin=None):
    """Verschieben fuer die Weboberflaeche und die Foederation. Eigentuemer: frei. Wer 'Bearbeiten' hat (2.3), darf
    innerhalb eines geteilten Baums verschieben: Seite und neue Elternseite bearbeitbar, und die Freigaben ueber der
    alten und der neuen Stelle sind genau dieselben - so bekommt niemand Zugriff dazu und niemand verliert ihn."""
    try:
        index = max(0, int(b.get("index", 0)))
    except (TypeError, ValueError):
        return _bad(t("ungültige Position"))
    with _db() as c, c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "edit", acc)
        if err is not None:
            return err
        me = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        if role != "owner" and "parent_id" not in b:
            b = dict(b, parent_id=me[0])               # nur umsortieren
        try:
            parent = _check_parent(c, owner, nid, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(user_error(ex, t("ungültige Angabe")))
        if role != "owner":
            if parent is None or me[0] is None or RANK.get(acc.role(parent), 0) < RANK["edit"]:
                return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
            if parent != me[0] and (_chain_shares(c, owner, parent) != _chain_shares(c, owner, me[0])
                                    or _chain_pubs(c, owner, parent) != _chain_pubs(c, owner, me[0])):    # nicht in/aus Veroeffentlichung
                return _bad(t("Verschieben geht nur innerhalb desselben geteilten Bereichs (gleiche Freigaben) – sonst nur durch den Eigentümer."), 403)
        if parent is not None:
            pk = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (parent, owner)).fetchone()[0]
            if pk == "database" and me[0] != parent:
                return _bad(t("Seiten können nicht in eine Datenbank verschoben werden"))
        sib = [r[0] for r in c.execute(
            "SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id<>? AND "
            + ("parent_id=?" if parent is not None else "parent_id IS NULL") + " ORDER BY sort NULLS FIRST, created",
            (owner, nid, parent) if parent is not None else (owner, nid))]
        sib.insert(min(index, len(sib)), nid)
        before = share_targets(c, owner, [nid]) if me[0] != parent else None    # Kette VOR dem Verschieben
        c.execute("UPDATE note SET parent_id=? WHERE id=? AND uid=?", (parent, nid, owner))
        c.executemany("UPDATE note SET sort=? WHERE id=? AND uid=?", [(i + 1, x, owner) for i, x in enumerate(sib)])
        if me[0] != parent:                  # andere Elternseite: geerbte Freigaben aendern sich - alte UND neue Kette (BUG-033)
            announce_tree(c, owner, nid, [nid], uid, time.time(), False, origin=origin, before=before)
        tree_changed(c, owner, [nid], "moved", origin, before=before)       # auch nur umsortiert
    return JSONResponse({"ok": True, "parent_id": parent, "index": sib.index(nid)})


@router.post("/api/notes/{nid}/archive")
async def api_note_archive(nid: int, request: Request):
    """Archivieren (archived=true) samt aktiver Unterseiten - gemeinsamer Zeitstempel, damit Zurueckholen genau
    diese Seiten zurueckbringt. Archivierte Seiten fehlen in Baum und Suche, bleiben aber per Link erreichbar."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid                              # Archivieren: nur der Eigentuemer
    b = await _body(request) or {}
    on = bool(b.get("archived", True))
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
        me = c.execute("SELECT archived, parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if on:
            if me[0] is not None:
                return JSONResponse({"ok": True, "archived": 0})
            now = time.time()
            n = sum(c.execute("UPDATE note SET archived=? WHERE id=? AND uid=? AND archived IS NULL", (now, i, owner)).rowcount for i in ids)
            announce_tree(c, owner, nid, ids, uid, now, True, origin=origin_of(request))
            tree_changed(c, owner, ids, "archived", origin_of(request))
            return JSONResponse({"ok": True, "archived": n})
        if me[0] is None:
            return JSONResponse({"ok": True, "restored": 0})
        n = sum(c.execute("UPDATE note SET archived=NULL WHERE id=? AND uid=? AND archived=?", (i, owner, me[0])).rowcount for i in ids)
        before = None
        if me[1]:             # Eltern noch archiviert (und keine Datenbank): Seite nach oben holen, sonst waere sie unsichtbar
            par = c.execute("SELECT archived, kind FROM note WHERE id=? AND uid=?", (me[1], owner)).fetchone()
            if par and par[0] is not None and par[1] != "database":
                before = share_targets(c, owner, [nid])                 # Kette vorher (Live)
                c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, owner))
        announce_tree(c, owner, nid, ids, uid, time.time(), False, origin=origin_of(request), before=before)
        tree_changed(c, owner, ids, "restored", origin_of(request), before=before)
        return JSONResponse({"ok": True, "restored": n})


@router.delete("/api/notes/{nid}")
def api_note_delete(nid: int, request: Request):
    """Verschiebt die Seite samt aktiver Unterseiten in den Papierkorb (gemeinsamer Zeitstempel)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return note_trash(uid, nid, origin_of(request))


def note_trash(uid, nid, origin=None):
    """In den Papierkorb (nur der Eigentuemer) - Weboberflaeche und /api/v1."""
    owner = uid                              # Loeschen: nur der Eigentuemer
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if not ids:
            return _bad("not found", 404)
        now = time.time()
        c.executemany("UPDATE note SET deleted_at=? WHERE id=? AND uid=?", [(now, i, owner) for i in ids])
        announce_tree(c, owner, nid, ids, uid, now, True, origin=origin)
        tree_changed(c, owner, ids, "trashed", origin)
    return JSONResponse({"deleted": len(ids), "trash": True})


@router.post("/api/notes/search")
async def api_note_search(request: Request):
    """Volltextsuche in Titel, Tags und Inhalt (POST: Suchbegriff landet nicht im Log).
    'tag' (exakt, Gross/Klein egal) filtert zusaetzlich nach einem Tag."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    q = str(b.get("q", "")).strip().lower()[:100]
    tag = str(b.get("tag", "")).strip().lower()[:TAG_MAX_LEN]
    if not q and not tag:
        return JSONResponse([])
    hits = await run_in_threadpool(_note_search, uid, q, tag)      # Datenbank + Pruefen der Kandidaten: nicht im Event-Loop
    return JSONResponse(hits, headers={"Cache-Control": "no-store"})


def _search_hit(r, q, tag):
    """r = (NOTE_META_COLS..., body, format) -> Treffer oder None"""
    d = _note_meta(r)
    tags_l = [x.lower() for x in d["tags"]]
    if tag and tag not in tags_l:
        return None
    if q:
        text = _note_text(r[10], r[9])
        i = text.lower().find(q)
        if not (q in (r[1] or "").lower() or i >= 0 or any(q in x for x in tags_l)):
            return None
        if i >= 0:
            s = max(0, i - 40)
            d["snippet"] = ("…" if s else "") + text[s:i + len(q) + 60] + ("…" if i + len(q) + 60 < len(text) else "")
    return d


def _touches(q, p):
    """Kann ein Treffer von q den Platzhalter p (Verweis auf eine Seite ohne Zugriff) beruehren - ganz, teilweise oder
    ueber seinen Rand hinaus? Dann kommen fuer die Suche in fremden Seiten alle Seiten mit Verweisen in Frage."""
    if not q or not p:
        return False
    return (q in p or p in q or any(q.startswith(p[i:]) for i in range(1, len(p)))
            or any(q.endswith(p[:i]) for i in range(1, len(p))))


SEARCH_PAGE = 200                  # Kandidaten je Abfrage (waechst je Folgeseite x4 bis 5000)
SEARCH_IDS_SQL = 2000              # bis zu so vielen sichtbaren fremden Seiten: als Liste an die Abfrage, darueber hier filtern


def _search_cands(c, owner, q, tag, ids=None, masked=False):
    """Kandidaten-ids der Seiten von owner, neueste zuerst (updated, id absteigend) - eine Obermenge der Treffer, geprueft
    wird danach wie bisher mit _search_hit: abgeglichene Seiten, deren Suchtext passt (saga.pageindex; bei masked auch
    alle mit Seitenverweisen), dazu jede noch nicht abgeglichene Seite. Seitenweise (LIMIT), damit die Suche bei
    haeufigen Woertern nach den ersten 50 Treffern aufhoert. ids: nur diese Seiten (Freigaben), sonst eigene ohne Archiv."""
    from saga.pageindex import SEP, kick, like_any
    conds, args = [], []
    if q:
        conds.append("d.search LIKE ?"); args.append(like_any(q))
    if tag:
        conds.append("d.tags LIKE ?"); args.append(like_any(SEP + tag + SEP))
    keep = None
    if ids is not None and len(ids) > SEARCH_IDS_SQL:
        # Viele sichtbare Seiten: ohne die lange id-Liste abfragen (Index nach Datum, bricht frueh ab) und hier filtern
        keep, scope, sargs = set(ids), "TRUE", []
    else:
        scope, sargs = ("n.id = ANY(?)", [ids]) if ids is not None else ("n.archived IS NULL", [])
    base = f"n.uid=? AND n.deleted_at IS NULL AND {scope}"
    match = " AND ".join(conds)
    if masked:
        match = f"({match} OR EXISTS (SELECT 1 FROM note_link l WHERE l.src=n.id))"
    # noch nicht abgeglichene Seiten: erst ihre ids (meist keine), dann gezielt - unabhaengig von der Statistik von note_stale
    sids = [r[0] for r in c.execute("SELECT note_id FROM note_stale WHERE uid=?", (owner,)).fetchall()]
    stale = sorted((r for r in c.execute(f"SELECT n.updated, n.id FROM note n WHERE n.id = ANY(?) AND {base}",
                                         (sids, owner, *sargs)).fetchall() if keep is None or r[1] in keep), reverse=True) if sids else []
    skip = {i for _, i in stale}
    if stale:
        kick()                             # Abgleich gleich anstossen (sonst spaetestens nach pageindex.IDLE)
    k, last, size = 0, None, SEARCH_PAGE if q else SEARCH_PAGE * 25     # nur Tag: wenige Treffer, auf einmal (Tag-Index)
    while True:
        rows = c.execute(f"SELECT n.updated, n.id FROM note n JOIN note_derived d ON d.note_id=n.id WHERE {base} AND {match}"
                         + (" AND (n.updated, n.id) < (?, ?)" if last else "") + " ORDER BY n.updated DESC, n.id DESC LIMIT ?",
                         (owner, *sargs, *args, *(last or ()), size)).fetchall()
        for r in rows:
            while k < len(stale) and stale[k] > tuple(r):
                yield stale[k][1]
                k += 1
            if r[1] not in skip and (keep is None or r[1] in keep):
                yield r[1]
        if len(rows) < size:
            break
        last, size = tuple(rows[-1]), min(size * 4, 5000)
    for _, i in stale[k:]:
        yield i


def _search_rows(c, owner, cands, body=True):
    """Zeilen (NOTE_META_COLS..., body, format) der Kandidaten in deren Reihenfolge - Inhalte in Portionen
    (body=False: nur Tag-Filter, der Inhalt wird nicht gebraucht -> None)."""
    it = iter(cands)
    while True:
        part = list(islice(it, 100))
        if not part:
            return
        rows = {r[0]: r for r in c.execute(f"SELECT {NOTE_META_COLS}, {'body' if body else 'NULL'}, format FROM note "
                                           "WHERE uid=? AND id = ANY(?)", (owner, part)).fetchall()}
        for x in part:
            if x in rows:
                yield rows[x]


def _note_search(uid, q, tag):
    """Eigene Seiten (neueste zuerst), danach fuer mich freigegebene (je Eigentuemer neueste zuerst, mit role/owner) -
    hoechstens 50. Treffer wie bisher (_search_hit: Teilwort in Titel, Inhalt oder Tag, Gross/Klein egal; fremde Seiten
    nur im sichtbaren Text), aber nur fuer Kandidaten aus dem Suchtext-Index statt fuer jede Seite (saga.pageindex)."""
    hits = []
    with _db() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")     # ein Datenstand fuer alle Abfragen
        for r in _search_rows(c, uid, _search_cands(c, uid, q, tag), bool(q)):
            d = _search_hit(r, q, tag)
            if d:
                hits.append(d)
                if len(hits) >= 50:
                    return hits
        acc = Access(c, uid)
        vis = viewer(acc)
        owners = acc.owners()
        nm = names(c, owners)
        masked = _touches(q, t("Seite ohne Zugriff").lower())
        for o in owners:
            roles = acc.tree(o)
            if not roles:
                continue
            for r in _search_rows(c, o, _search_cands(c, o, q, tag, list(roles), masked), bool(q)):
                if q:
                    r = tuple(r[:9]) + (mask_links(r[9], r[10], vis), r[10])      # Suche nur im sichtbaren Text
                d = _search_hit(r, q, tag)
                if d:
                    d.update(role=roles[r[0]], pinned=False, owner={"name": nm.get(o) or o, "username": ref_of(None, o, uid), "me": False,
                                                                         "avatar": nm.avatar(o)})
                    if d["parent_id"] not in roles:
                        d["parent_id"] = None
                    hits.append(d)
                    if len(hits) >= 50:
                        return hits
    return hits


@router.get("/api/notes/{nid}/backlinks")
def api_note_backlinks(nid: int, request: Request):
    """Seiten des Eigentuemers, die auf diese Seite verweisen (Rueckverweise) - fuer andere nur die, die sie sehen duerfen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        # Im gespeicherten JSON steht der Link als href=\"/notes#<id>\" - der Backslash vor dem
        # Anfuehrungszeichen grenzt die Zahl ab (#1 trifft nicht #12). Abgeglichene Seiten: Verweis-Tabelle note_link
        # (saga.pageindex, mit genau diesem Muster gefuellt); gerade geaenderte (note_stale) wie bisher am Inhalt.
        # Einzelne kleine Abfragen (ein Datenstand: REPEATABLE READ oben) - keine Planwahl, die von note_stale abhaengt.
        stale = [r[0] for r in c.execute("SELECT note_id FROM note_stale WHERE uid=?", (owner,)).fetchall()]
        ids = {r[0] for r in c.execute("SELECT src FROM note_link WHERE dst=?", (nid,)).fetchall()} - set(stale)
        if stale:
            ids |= {r[0] for r in c.execute("SELECT id FROM note WHERE id = ANY(?) AND strpos(body, ?) > 0",
                                            (stale, f'/notes#{nid}\\"')).fetchall()}
        rows = c.execute(f"SELECT {NOTE_META_COLS} FROM note WHERE id = ANY(?) AND uid=? AND id<>? AND format='blocks' "
                         "AND deleted_at IS NULL ORDER BY updated DESC", (sorted(ids), owner, nid)).fetchall() if ids else []
        if role != "owner":
            vis = acc.tree(owner)
            rows = [r for r in rows if r[0] in vis]
    out = [_note_meta(r) for r in rows]
    if role != "owner":
        for d in out:
            if d["parent_id"] not in vis:
                d["parent_id"] = None
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.get("/api/services")
def api_services(request: Request):
    """Dienste, die der Benutzer sehen darf, und seine eigenen Lesezeichen (own=true) - fuer Apps und Verweise in Seiten."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    out = [{"name": s["name"], "url": s["url"], "host": s["host"], "category": s["category"], "icon": s.get("icon") or "",
            "own": bool(s.get("own")), "key": s["key"], "icon_url": s.get("icon_url") or "",
            **({"icon_direct": s.get("icon_direct") or []} if s.get("own") else {})} for s in visible_tiles(uid).values()]
    out.sort(key=lambda s: (s["category"].lower(), s["name"].lower()))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


# Dienst-Symbole (selfh.st, wie die Kacheln im Web) fuer die Apps: einmal vom CDN holen, als PNG ablegen
# (saga/siteicons.py, dort auch die automatischen Symbole /api/services/favicon/<schluessel>).
@router.get("/api/services/icon/{name}")
def api_service_icon(name: str, request: Request):
    if not _session_user(request) or not ICON_RE.fullmatch(name):
        return PlainTextResponse("not found", status_code=404)
    path, code = svc_icon_path(name)
    if not path:                                  # 503: belegt bzw. CDN gerade nicht erreichbar (saga/siteicons.py)
        return PlainTextResponse("not found" if code == 404 else "busy", status_code=code,
                                 headers={"Cache-Control": "no-store"} if code != 404 else None)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=604800"})
