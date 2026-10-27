"""Vorlagengalerie (2.5): Dateiformat der Bundles - laden, pruefen, Texte sammeln, uebersetzen, Verweise ersetzen.

Bewusst ohne Abhaengigkeit zu Datenbank und Konfiguration (nur Standardbibliothek): wird vom Server (saga.gallery) und
vom Pruefskript tools/gallery_check.py benutzt. Die Typ-Listen spiegeln saga.notes / saga.page_widgets
(tests/galerie.py prueft, dass sie gleich bleiben).

Format: docs/GALERIE.md (Vertrag zwischen Mechanik und Inhalt)."""
import copy
import json
import os
import re
from datetime import date

# --- Spiegel der Server-Listen (saga.notes, saga.page_widgets) - Gleichheit prueft tests/galerie.py ---
PROP_TYPES = {"text", "number", "select", "multi_select", "date", "checkbox", "url", "created", "edited", "relation", "calc"}
VIEW_TYPES = {"table", "board", "list", "gallery", "calendar"}
WIDGET_KINDS = ("clock", "countdown", "progress", "metric", "quote")
OPT_COLORS = {"gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}
PID = re.compile(r"[a-z0-9_]{1,24}")

# Bloecke, die eine Galerie-Vorlage verwenden darf (ohne Dateien: image/attaches/immich/nextcloud brauchen eigene Uploads)
BLOCK_TYPES = {"paragraph", "header", "list", "checklist", "quote", "code", "delimiter", "table", "toggle", "callout",
               "columns", "buttons", "database", "tasks", "calendar", "widget", "embed"}
CATEGORIES = ("personal", "work", "team")
NEEDS = {"calendar", "immich", "nextcloud", "ai"}
FONTS = ("serif", "mono")
LANGS = ("en", "es", "ru", "th")                 # Quelle ist Deutsch

MAX_ITEMS, MAX_ROWS, MAX_CHARS = 30, 200, 200_000
MAX_DEPTH = 8
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
REF_RE = re.compile(r"[a-z0-9_-]{1,40}")
REF_ANY = re.compile(r"\$ref:([a-z0-9_-]{1,40})")
REF_FULL = re.compile(r"\$ref:([a-z0-9_-]{1,40})")
PH = re.compile(r"\{(\w+)\}")

# Schluessel, deren Text-Werte (auch Listen von Texten) uebersetzt werden - in Bloecken und Widget-Einstellungen
TEXT_KEYS = {"text", "title", "caption", "content", "label", "items", "message"}


class BundleError(ValueError):
    """Bundle ungueltig."""


# =====================================================================================================
# Texte sammeln und uebersetzen
# =====================================================================================================
def _is_text(s):
    return isinstance(s, str) and s.strip() != "" and not REF_FULL.fullmatch(s)


def _walk_data(v, key, fn):
    """Block-Daten: Texte unter TEXT_KEYS (auch in Listen/Tabellen) durch fn ersetzen; alles andere unveraendert."""
    if isinstance(v, str):
        return fn(v) if key in TEXT_KEYS and _is_text(v) else v
    if isinstance(v, list):
        return [_walk_data(x, key, fn) for x in v]
    if isinstance(v, dict):
        return {k: _walk_data(x, k, fn) for k, x in v.items()}
    return v


def _walk_blocks(blocks, fn):
    out = []
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            out.append(b)
            continue
        nb = dict(b)
        if isinstance(b.get("data"), dict):
            nb["data"] = _walk_data(b["data"], None, fn)
        out.append(nb)
    return out


def _walk_schema(s, fn):
    if not isinstance(s, dict):
        return s
    s = copy.deepcopy(s)
    for p in s.get("props") or []:
        if isinstance(p, dict):
            if _is_text(p.get("name")):
                p["name"] = fn(p["name"])
            for o in p.get("options") or []:
                if isinstance(o, dict) and _is_text(o.get("name")):
                    o["name"] = fn(o["name"])
    for v in s.get("views") or []:
        if isinstance(v, dict) and _is_text(v.get("name")):
            v["name"] = fn(v["name"])
    if _is_text(s.get("title_name")):
        s["title_name"] = fn(s["title_name"])
    return s


def _walk_entry(e, fn):
    """Seite, Datenbank, Zeile oder Eintrags-Vorlage: Titel, Inhalt, Schema, Zeilen/Vorlagen."""
    e = dict(e)
    if _is_text(e.get("title")):
        e["title"] = fn(e["title"])
    if "body" in e:
        e["body"] = _walk_blocks(e["body"], fn)
    if "schema" in e:
        e["schema"] = _walk_schema(e["schema"], fn)
    for k in ("rows", "templates"):
        if isinstance(e.get(k), list):
            e[k] = [_walk_entry(x, fn) if isinstance(x, dict) else x for x in e[k]]
    return e


def walk(bundle, fn):
    """Kopie des Bundles, in der jeder uebersetzbare Text durch fn(text) ersetzt ist."""
    out = dict(bundle)
    for k in ("title", "desc"):
        if _is_text(out.get(k)):
            out[k] = fn(out[k])
    out["items"] = [_walk_entry(it, fn) if isinstance(it, dict) else it for it in bundle.get("items") or []]
    return out


def texts(bundle):
    """Alle uebersetzbaren Texte (deutsche Quelle) eines Bundles, in Reihenfolge des Auftretens."""
    seen = {}

    def grab(s):
        seen.setdefault(s, None)
        return s
    walk(bundle, grab)
    return list(seen)


def translate(bundle, table):
    """Bundle in eine Sprache: table = {"deutsch": "Uebersetzung"} (fehlt ein Text: Deutsch)."""
    if not table:
        return copy.deepcopy(bundle)
    return copy.deepcopy(walk(bundle, lambda s: table.get(s) or s))


# =====================================================================================================
# Verweise
# =====================================================================================================
def resolve(v, ids):
    """Alle "$ref:x" ersetzen: ganzer String -> Zahl (ID), im Text (z. B. href="/notes#$ref:x") -> Ziffern.
    Verweise auf nicht angelegte Eintraege (Beispiele aus): ganzer String -> None, im Text -> Verweis bleibt leer (0)."""
    if isinstance(v, str):
        m = REF_FULL.fullmatch(v)
        if m:
            return ids.get(m.group(1))
        if "$ref:" in v:
            return REF_ANY.sub(lambda m: str(ids.get(m.group(1)) or 0), v)
        return v
    if isinstance(v, list):
        out = [resolve(x, ids) for x in v]
        return out
    if isinstance(v, dict):
        return {k: resolve(x, ids) for k, x in v.items()}
    return v


def _refs_in(v):
    """Alle refs in einem Wert (ueberall, auch im Text)."""
    out = []
    if isinstance(v, str):
        out += REF_ANY.findall(v)
    elif isinstance(v, list):
        for x in v:
            out += _refs_in(x)
    elif isinstance(v, dict):
        for x in v.values():
            out += _refs_in(x)
    return out


# =====================================================================================================
# Pruefen
# =====================================================================================================
def _iter_blocks(blocks, depth=0):
    if depth > MAX_DEPTH:
        raise BundleError("Bloecke zu tief verschachtelt")
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        yield b
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            for c in d.get("cols") or []:
                if isinstance(c, dict):
                    yield from _iter_blocks(c.get("blocks"), depth + 1)
        elif b.get("type") == "toggle":
            yield from _iter_blocks(d.get("blocks"), depth + 1)


class _Ctx:
    def __init__(self, bundle):
        self.b = bundle
        self.errors = []
        self.objs = {}          # ref -> (art, Objekt, Datenbank-ref oder None, example)
        self.rows = 0

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")


def _ref_of(v):
    m = REF_FULL.fullmatch(v) if isinstance(v, str) else None
    return m.group(1) if m else None


def _check_str(cx, where, v, limit, empty_ok=True):
    if v is None and empty_ok:
        return
    if not isinstance(v, str) or len(v) > limit or (not empty_ok and not v.strip()):
        cx.err(where, f"Text fehlt, ist kein Text oder laenger als {limit} Zeichen")


def _register(cx, where, ref, kind, obj, db, example):
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        cx.err(where, f"ungueltiger ref {ref!r}")
        return
    if ref in cx.objs:
        cx.err(where, f"ref {ref!r} doppelt")
        return
    cx.objs[ref] = (kind, obj, db, example)


def _check_schema(cx, where, s, dbref):
    if not isinstance(s, dict) or not isinstance(s.get("props"), list):
        cx.err(where, "schema braucht props (Liste)")
        return {}, set()
    if len(s["props"]) > 30:
        cx.err(where, "mehr als 30 Eigenschaften")
    pids = {}
    for i, p in enumerate(s["props"]):
        w = f"{where}.props[{i}]"
        if not isinstance(p, dict):
            cx.err(w, "keine Eigenschaft")
            continue
        pid, typ = p.get("id"), p.get("type")
        if not isinstance(pid, str) or not PID.fullmatch(pid):
            cx.err(w, f"ungueltige Eigenschafts-ID {pid!r} (Muster [a-z0-9_]{{1,24}})")
        elif pid in pids:
            cx.err(w, f"Eigenschafts-ID {pid!r} doppelt")
        if typ not in PROP_TYPES:
            cx.err(w, f"unbekannter Eigenschaftstyp {typ!r}")
        _check_str(cx, w + ".name", p.get("name"), 60, empty_ok=False)
        pids[pid] = p
        if typ in ("select", "multi_select"):
            oids = set()
            opts = p.get("options") if isinstance(p.get("options"), list) else []
            if len(opts) > 50:
                cx.err(w, "mehr als 50 Optionen")
            for j, o in enumerate(opts):
                ow = f"{w}.options[{j}]"
                if not isinstance(o, dict) or not isinstance(o.get("id"), str) or not PID.fullmatch(o["id"]) or o["id"] in oids:
                    cx.err(ow, "ungueltige oder doppelte Options-ID")
                    continue
                oids.add(o["id"])
                if o.get("color") not in OPT_COLORS:
                    cx.err(ow, f"unbekannte Farbe {o.get('color')!r}")
                _check_str(cx, ow + ".name", o.get("name"), 40, empty_ok=False)
        if typ == "relation":
            r = _ref_of(p.get("target"))
            if not r:
                cx.err(w, "Relation braucht target \"$ref:<datenbank>\"")
            if "two_way" in p and not isinstance(p["two_way"], bool):
                cx.err(w, "two_way muss true/false sein")
            if "pair" in p:
                cx.err(w, "pair setzt der Server (two_way: true verwenden)")
        if typ == "calc":
            for k in ("a", "b"):
                if p.get(k) is not None and not isinstance(p.get(k), str):
                    cx.err(w, f"calc.{k} muss eine Eigenschafts-ID sein")
    for p in s["props"]:                             # calc: nur aus Zahlen-Spalten
        if isinstance(p, dict) and p.get("type") == "calc":
            for k in ("a", "b"):
                if p.get(k) and (pids.get(p[k]) or {}).get("type") != "number":
                    cx.err(f"{where}.props[{p.get('id')}]", f"calc.{k} {p.get(k)!r} ist keine Zahlen-Eigenschaft")
    vids = set()
    views = s.get("views") if isinstance(s.get("views"), list) else []
    if not views:
        cx.err(where, "schema braucht mindestens eine Ansicht (views)")
    if len(views) > 10:
        cx.err(where, "mehr als 10 Ansichten")
    for i, v in enumerate(views):
        w = f"{where}.views[{i}]"
        if not isinstance(v, dict) or not isinstance(v.get("id"), str) or not PID.fullmatch(v["id"]) or v["id"] in vids:
            cx.err(w, "ungueltige oder doppelte Ansichts-ID")
            continue
        vids.add(v["id"])
        if v.get("type") not in VIEW_TYPES:
            cx.err(w, f"unbekannter Ansichtstyp {v.get('type')!r}")
        _check_str(cx, w + ".name", v.get("name"), 40, empty_ok=False)
        for k in ("group_by", "group"):
            if k in v and (pids.get(v[k]) or {}).get("type") != "select":
                cx.err(w, f"{k} {v[k]!r} ist keine Auswahl-Eigenschaft")
        if "date_by" in v and (pids.get(v["date_by"]) or {}).get("type") not in ("date", "created", "edited"):
            cx.err(w, f"date_by {v['date_by']!r} ist keine Datums-Eigenschaft")
        for f in (v.get("filters") or []) + (v.get("sorts") or []) + (v.get("colors") or []):
            if isinstance(f, dict) and f.get("pid") != "title" and f.get("pid") not in pids:
                cx.err(w, f"Filter/Sortierung auf unbekannte Eigenschaft {f.get('pid')!r}")
    if "default_tpl" in s and not _ref_of(s["default_tpl"]):
        cx.err(where, "default_tpl muss \"$ref:<vorlage>\" sein")
    if "srev" in s or "_since" in s:
        cx.err(where, "srev/_since setzt der Server")
    return pids, vids


def _day_ok(v):
    if not (isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)):
        return False
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _check_props(cx, where, vals, pids):
    if vals is None:
        return
    if not isinstance(vals, dict):
        cx.err(where, "props muss ein Objekt sein")
        return
    for k, v in vals.items():
        p = pids.get(k)
        if not p:
            cx.err(where, f"unbekannte Eigenschaft {k!r}")
            continue
        typ = p.get("type")
        opts = {o.get("id") for o in p.get("options") or [] if isinstance(o, dict)}
        if typ in ("created", "edited", "calc"):
            cx.err(where, f"{k}: Wert fuer {typ} wird nie gespeichert")
        elif typ == "select" and v not in opts:
            cx.err(where, f"{k}: unbekannte Option {v!r}")
        elif typ == "multi_select" and (not isinstance(v, list) or any(x not in opts for x in v)):
            cx.err(where, f"{k}: unbekannte Option(en) {v!r}")
        elif typ == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            cx.err(where, f"{k}: keine Zahl")
        elif typ == "checkbox" and not isinstance(v, bool):
            cx.err(where, f"{k}: true/false erwartet")
        elif typ == "date" and not _day_ok(v):
            cx.err(where, f"{k}: Datum JJJJ-MM-TT erwartet")
        elif typ == "url" and not (isinstance(v, str) and re.match(r"(https?://|mailto:)", v)):
            cx.err(where, f"{k}: URL mit http(s):// oder mailto: erwartet")
        elif typ == "text" and not isinstance(v, str):
            cx.err(where, f"{k}: Text erwartet")
        elif typ == "relation":
            if not isinstance(v, list) or not all(_ref_of(x) for x in v):
                cx.err(where, f"{k}: Relation = Liste aus \"$ref:<zeile>\"")


def _check_blocks(cx, where, blocks):
    if blocks is None:
        return
    if not isinstance(blocks, list):
        cx.err(where, "body muss eine Liste von Bloecken sein")
        return
    try:
        allb = list(_iter_blocks(blocks))
    except BundleError as ex:
        cx.err(where, str(ex))
        return
    for i, b in enumerate(allb):
        w = f"{where}[{b.get('type')}#{i}]"
        if not isinstance(b.get("type"), str) or not isinstance(b.get("data"), dict):
            cx.err(w, "Block braucht type und data")
            continue
        if b["type"] not in BLOCK_TYPES:
            cx.err(w, f"Blocktyp {b['type']!r} nicht erlaubt (erlaubt: {', '.join(sorted(BLOCK_TYPES))})")
        d = b["data"]
        if b["type"] == "widget":
            if d.get("kind") not in WIDGET_KINDS:
                cx.err(w, f"unbekannte Widget-Art {d.get('kind')!r}")
            cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
            if "db" in cfg and not _ref_of(cfg["db"]):
                cx.err(w, "Widget cfg.db muss \"$ref:<datenbank>\" sein")
            if d.get("kind") in ("metric",) or (d.get("kind") in ("progress", "quote") and cfg.get("src") == "db"):
                if not _ref_of(cfg.get("db")):
                    cx.err(w, "Widget braucht cfg.db \"$ref:<datenbank>\"")
        elif b["type"] == "database":
            if not _ref_of(d.get("db")):
                cx.err(w, "Datenbank-Block braucht db \"$ref:<datenbank>\"")
        elif b["type"] == "buttons":
            for j, bt in enumerate(d.get("buttons") or []):
                if not isinstance(bt, dict):
                    continue
                if bt.get("action") in ("db", "page") and not _ref_of(bt.get("target")):
                    cx.err(f"{w}.buttons[{j}]", "Schaltflaeche braucht target \"$ref:...\"")
                if bt.get("tpl") is not None and not _ref_of(bt.get("tpl")):
                    cx.err(f"{w}.buttons[{j}]", "tpl muss \"$ref:<vorlage>\" sein")
                if "sig" in bt:
                    cx.err(f"{w}.buttons[{j}]", "sig setzt der Server")


def _check_entry(cx, where, e, kind, parent_db=None, parent_example=False):
    if not isinstance(e, dict):
        cx.err(where, "kein Objekt")
        return
    _check_str(cx, where + ".title", e.get("title"), 200, empty_ok=kind == "row")
    ic = e.get("icon")
    if ic is not None and (not isinstance(ic, str) or len(ic) > 8 or ic.startswith("/") or any(ch in ic for ch in "<>&\"'")):
        cx.err(where, "icon: Emoji (hoechstens 8 Zeichen)")
    if "example" in e and not isinstance(e["example"], bool):
        cx.err(where, "example muss true/false sein")
    _check_blocks(cx, where + ".body", e.get("body"))


def validate(bundle, name=None):
    """-> Liste der Fehler (leer = gueltig)."""
    cx = _Ctx(bundle)
    if not isinstance(bundle, dict):
        return ["Bundle ist kein Objekt"]
    bid = bundle.get("id")
    if not isinstance(bid, str) or not ID_RE.fullmatch(bid):
        cx.err("id", f"ungueltig {bid!r} (Muster [a-z0-9][a-z0-9-]{{0,39}})")
    elif name is not None and name != bid:
        cx.err("id", f"{bid!r} passt nicht zum Dateinamen {name!r}")
    if bundle.get("v") != 1:
        cx.err("v", "Formatversion muss 1 sein")
    if not isinstance(bundle.get("order", 0), int) or isinstance(bundle.get("order"), bool):
        cx.err("order", "ganze Zahl erwartet")
    if bundle.get("category") not in CATEGORIES:
        cx.err("category", f"eine von {', '.join(CATEGORIES)}")
    _check_str(cx, "title", bundle.get("title"), 80, empty_ok=False)
    _check_str(cx, "desc", bundle.get("desc"), 300)
    ic = bundle.get("icon")
    if not isinstance(ic, str) or not ic or len(ic) > 8:
        cx.err("icon", "Emoji (hoechstens 8 Zeichen) erwartet")
    needs = bundle.get("needs", [])
    if not isinstance(needs, list) or any(n not in NEEDS for n in needs):
        cx.err("needs", f"Liste aus {', '.join(sorted(NEEDS))}")
    items = bundle.get("items")
    if not isinstance(items, list) or not items:
        cx.err("items", "Liste mit mindestens einem Eintrag erwartet")
        return cx.errors
    if len(items) > MAX_ITEMS:
        cx.err("items", f"mehr als {MAX_ITEMS} Eintraege")
    # 1. refs registrieren (Eltern muessen vorher stehen)
    schemas = {}
    for i, it in enumerate(items):
        w = f"items[{i}]"
        if not isinstance(it, dict):
            cx.err(w, "kein Objekt")
            continue
        w = f"items[{it.get('ref', i)}]"
        kind = it.get("kind")
        if kind not in ("page", "database"):
            cx.err(w, f"kind muss page oder database sein, nicht {kind!r}")
            continue
        par = it.get("parent")
        ex = it.get("example") is True
        if par is not None:
            po = cx.objs.get(par) if isinstance(par, str) else None
            if not po:
                cx.err(w, f"parent {par!r} unbekannt (Elternseiten muessen vorher stehen)")
            elif po[0] != "page":
                cx.err(w, f"parent {par!r} ist keine Seite (in Datenbanken nur rows/templates)")
            elif po[3] and not ex:
                cx.err(w, f"parent {par!r} ist ein Beispiel, dieser Eintrag nicht")
        _register(cx, w, it.get("ref"), kind, it, None, ex)
        _check_entry(cx, w, it, kind)
        if it.get("cover") is not None:
            cx.err(w, "cover: nur null (Titelbilder sind eigene Uploads)")
        if "wide" in it and not isinstance(it["wide"], bool) or "small" in it and not isinstance(it["small"], bool):
            cx.err(w, "wide/small muessen true/false sein")
        if it.get("font") not in (None,) + FONTS:
            cx.err(w, f"font: null, {', '.join(FONTS)}")
        if kind == "page":
            for k in ("schema", "rows", "templates"):
                if k in it:
                    cx.err(w, f"{k} nur bei kind database")
            continue
        schemas[it.get("ref")] = _check_schema(cx, w + ".schema", it.get("schema"), it.get("ref"))
        dfl = 0
        for j, tp in enumerate(it.get("templates") or []):
            tw = f"{w}.templates[{j}]"
            if not isinstance(tp, dict):
                cx.err(tw, "kein Objekt")
                continue
            _register(cx, tw, tp.get("ref"), "template", tp, it.get("ref"), False)
            _check_entry(cx, tw, tp, "template")
            if tp.get("example"):
                cx.err(tw, "Eintrags-Vorlagen sind nie Beispiele")
            if "default" in tp and not isinstance(tp["default"], bool):
                cx.err(tw, "default muss true/false sein")
            dfl += tp.get("default") is True
        if dfl > 1:
            cx.err(w, "hoechstens eine Vorlage mit default: true")
        if not isinstance(it.get("rows", []), list) or not isinstance(it.get("templates", []), list):
            cx.err(w, "rows/templates muessen Listen sein")
            continue
        for j, row in enumerate(it.get("rows") or []):
            rw = f"{w}.rows[{j}]"
            if not isinstance(row, dict):
                cx.err(rw, "kein Objekt")
                continue
            cx.rows += 1
            rex = row.get("example") is True or ex
            if "ref" in row:
                _register(cx, rw, row["ref"], "row", row, it.get("ref"), rex)
            _check_entry(cx, rw, row, "row")
    if cx.rows > MAX_ROWS:
        cx.err("items", f"mehr als {MAX_ROWS} Zeilen")
    root = cx.objs.get(bundle.get("root")) if isinstance(bundle.get("root"), str) else None
    if not root or root[0] not in ("page", "database") or root[1].get("parent") is not None:
        cx.err("root", "muss der ref einer obersten Seite/Datenbank (parent null) sein")
    elif root[3]:
        cx.err("root", "darf kein Beispiel sein")
    tops = [it for it in items if isinstance(it, dict) and it.get("parent") is None]
    if len(tops) != 1:
        cx.err("items", "genau ein Eintrag mit parent null (die Startseite) - alles andere haengt darunter")

    # 2. Verweise: bekannt, passende Art, keine Beispiele aus Nicht-Beispielen (ausser in Relationen)
    def need(where, ref, kinds, from_example, rel=False):
        o = cx.objs.get(ref)
        if not o:
            cx.err(where, f"unbekannter ref {ref!r}")
            return None
        if kinds and o[0] not in kinds:
            cx.err(where, f"ref {ref!r} ist {o[0]}, erwartet {'/'.join(kinds)}")
        if o[3] and not from_example and not rel:
            cx.err(where, f"ref {ref!r} zeigt auf ein Beispiel (fehlt ohne Beispiele)")
        return o

    def check_body_refs(where, blocks, ex):
        try:
            allb = list(_iter_blocks(blocks))
        except BundleError:
            return
        for b in allb:
            d = b.get("data") if isinstance(b.get("data"), dict) else {}
            t = b.get("type")
            if t == "database" and _ref_of(d.get("db")):
                o = need(where, _ref_of(d["db"]), ("database",), ex)
                if o and d.get("view") and d["view"] not in schemas.get(_ref_of(d["db"]), ({}, set()))[1]:
                    cx.err(where, f"Ansicht {d['view']!r} gibt es in {_ref_of(d['db'])!r} nicht")
            elif t == "widget":
                cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
                r = _ref_of(cfg.get("db"))
                if r:
                    o = need(where, r, ("database",), ex)
                    pids, vids = schemas.get(r, ({}, set()))
                    if o and cfg.get("view") and cfg["view"] not in vids:
                        cx.err(where, f"Widget-Ansicht {cfg['view']!r} gibt es in {r!r} nicht")
                    if o and cfg.get("prop") and cfg["prop"] not in pids:
                        cx.err(where, f"Widget-Eigenschaft {cfg['prop']!r} gibt es in {r!r} nicht")
            elif t == "buttons":
                for bt in d.get("buttons") or []:
                    if not isinstance(bt, dict):
                        continue
                    tg = _ref_of(bt.get("target"))
                    if bt.get("action") == "db" and tg:
                        need(where, tg, ("database",), ex)
                        tp = _ref_of(bt.get("tpl"))
                        if tp:
                            o = need(where, tp, ("template",), ex)
                            if o and o[2] != tg:
                                cx.err(where, f"Vorlage {tp!r} gehoert nicht zur Datenbank {tg!r}")
                    elif bt.get("action") == "page" and tg:
                        need(where, tg, ("page", "database", "row", "template"), ex)
        for r in _refs_in(blocks):
            need(where, r, None, ex)

    for it in items:
        if not isinstance(it, dict) or it.get("kind") not in ("page", "database"):
            continue
        ref, ex = it.get("ref"), it.get("example") is True
        w = f"items[{ref}]"
        check_body_refs(w + ".body", it.get("body"), ex)
        if it.get("kind") != "database":
            continue
        s = it.get("schema") if isinstance(it.get("schema"), dict) else {}
        pids = schemas.get(ref, ({}, set()))[0]
        for p in s.get("props") or []:
            if isinstance(p, dict) and p.get("type") == "relation" and _ref_of(p.get("target")):
                need(f"{w}.schema", _ref_of(p["target"]), ("database",), ex)
        if _ref_of(s.get("default_tpl")):
            o = need(f"{w}.schema", _ref_of(s["default_tpl"]), ("template",), ex)
            if o and o[2] != ref:
                cx.err(f"{w}.schema", "default_tpl gehoert nicht zu dieser Datenbank")
        for sub in ("templates", "rows"):
            for j, e in enumerate(it.get(sub) or []):
                if not isinstance(e, dict):
                    continue
                ew = f"{w}.{sub}[{e.get('ref', j)}]"
                eex = ex or e.get("example") is True
                _check_props(cx, ew + ".props", e.get("props"), pids)
                for k, v in (e.get("props") or {}).items() if isinstance(e.get("props"), dict) else []:
                    p = pids.get(k) or {}
                    if p.get("type") == "relation" and isinstance(v, list):
                        tdb = _ref_of(p.get("target"))
                        for x in v:
                            o = need(ew + ".props", _ref_of(x) or "", ("row",), eex, rel=True)
                            if o and o[2] != tdb:
                                cx.err(ew + ".props", f"{x!r} ist keine Zeile von {tdb!r}")
                    elif "$ref:" in json.dumps(v):
                        cx.err(ew + ".props", f"{k}: Verweise nur in Relationen")
                check_body_refs(ew + ".body", e.get("body"), eex)
    extra = {}                                       # zweiseitige Relationen legen im Ziel eine Spalte an
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("schema"), dict):
            for p in it["schema"].get("props") or []:
                if isinstance(p, dict) and p.get("type") == "relation" and p.get("two_way") is True and _ref_of(p.get("target")):
                    if _ref_of(p["target"]) == it.get("ref"):
                        cx.err(f"items[{it.get('ref')}].schema", "two_way auf die eigene Datenbank geht nicht")
                    extra[_ref_of(p["target"])] = extra.get(_ref_of(p["target"]), 0) + 1
    for r, n in extra.items():
        if r in schemas and len(schemas[r][0]) + n > 30:
            cx.err(f"items[{r}].schema", "mit den Gegenspalten zweiseitiger Relationen mehr als 30 Eigenschaften")
    for k in ("title", "desc", "icon", "category"):
        if "$ref:" in str(bundle.get(k) or ""):
            cx.err(k, "keine Verweise erlaubt")
    try:
        size = len(json.dumps(bundle, ensure_ascii=False))
    except (TypeError, ValueError):
        size = MAX_CHARS + 1
    if size > MAX_CHARS:
        cx.err("Bundle", f"{size} Zeichen, erlaubt {MAX_CHARS}")
    return list(dict.fromkeys(cx.errors))


# =====================================================================================================
# Laden
# =====================================================================================================
def load_dir(path):
    """-> (bundles {id: bundle}, i18n {lang: {de: x}}, problems {datei: [fehler]}). Ungueltige Bundles fehlen in bundles."""
    bundles, problems, tables = {}, {}, {}
    idir = os.path.join(path, "i18n")
    for lang in LANGS:
        p = os.path.join(idir, lang + ".json")
        if not os.path.isfile(p):
            tables[lang] = {}
            continue
        try:
            with open(p, encoding="utf-8") as f:
                tb = json.load(f)
            if not isinstance(tb, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in tb.items()):
                raise ValueError("erwartet {\"deutsch\": \"Uebersetzung\"}")
            tables[lang] = tb
        except (OSError, ValueError) as ex:
            problems[f"i18n/{lang}.json"] = [str(ex)]
            tables[lang] = {}
    names = sorted(f for f in os.listdir(path) if f.endswith(".json")) if os.path.isdir(path) else []
    for fn in names:
        stem = fn[:-5]
        try:
            with open(os.path.join(path, fn), encoding="utf-8") as f:
                raw = f.read()
            if len(raw) > MAX_CHARS * 2:
                raise ValueError(f"Datei zu gross ({len(raw)} Zeichen)")
            b = json.loads(raw)
        except (OSError, ValueError) as ex:
            problems[fn] = [f"JSON: {ex}"]
            continue
        errs = validate(b, stem)
        if not errs and b["id"] in bundles:
            errs = [f"id {b['id']!r} doppelt"]
        if errs:
            problems[fn] = errs
        else:
            bundles[b["id"]] = b
    return bundles, tables, problems


def missing_translations(bundle, tables):
    """-> {lang: [fehlende Texte]} und Platzhalter-/ref-Abweichungen als {lang: [(text, problem)]}."""
    miss, bad = {}, {}
    src = texts(bundle)
    for lang in LANGS:
        tb = tables.get(lang) or {}
        m = [s for s in src if not tb.get(s)]
        if m:
            miss[lang] = m
        for s in src:
            tr = tb.get(s)
            if not tr:
                continue
            if sorted(PH.findall(s)) != sorted(PH.findall(tr)):
                bad.setdefault(lang, []).append((s, "Platzhalter {..} weichen ab"))
            if sorted(REF_ANY.findall(s)) != sorted(REF_ANY.findall(tr)):
                bad.setdefault(lang, []).append((s, "$ref-Verweise weichen ab"))
    return miss, bad
