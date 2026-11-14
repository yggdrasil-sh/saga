"""Strukturen ueber API-Token anlegen (2.5, MCP-Wunsch 2021): Datenbanken (mit Eigenschaften und Ansichten), Schema
ergaenzen, Widgets in Seiten, eingebettete Datenbank. Aufrufer: saga.mcp - dieselben Regeln wie saga.api_v1 (Bereich,
roots, Rolle) und derselbe Weg wie das Web (pages.note_create/_note_update, Bereinigung _clean_schema/page_widgets).

Entscheidungen
- Eigenschaften per Name; IDs vergibt der Server. Typen wie im Web; "status" = Auswahl mit Status-Gruppen. Relation nur
  auf eine Datenbank, die der Token sehen darf (Pruefung der Ziel-Datenbank wie im Web in _note_update); Formel, Rollup und
  Rechnung (calc) lehnen wir ab - Ausdruecke mit Eigenschafts-IDs sind fuer eine KI fehleranfaellig.
- update_database_schema ergaenzt nur (Eigenschaften, Optionen, Ansichten) und benennt um - es loescht nie etwas: ohne
  Papierkorb fuer Spalten waeren Werte aller Zeilen weg.
- Widgets: Datenbank-Quellen brauchen read:db und muessen in den roots liegen; Aufgaben-Quellen read:tasks. Werte
  (get_widgets) nur unter denselben Bedingungen, persoenliche Arten (upcoming, reading) nur ohne roots; upcoming dazu mit
  read:tasks bzw. read:calendar (je nachdem, was es zeigt)."""
import contextlib
import json
import re
import secrets
from datetime import date

from db import connect as _db
from saga import blockedit, i18n, page_widgets
from saga.api_v1 import Fail, _guard, _relay, _schema_of, blocks_edit, hidden_props, page_create, schema_out
from saga.apitok import in_roots
from saga.core import account_lang, t
from saga.notes import OPT_COLORS, STATUS_GROUPS, VIEW_TYPES
from saga.sharing import Access

PROP_TYPES_IN = ("text", "number", "select", "multi_select", "status", "date", "checkbox", "url", "created", "edited",
                 "person", "files", "relation")
PROP_REJECT = ("formula", "rollup", "calc")
COLOR_CYCLE = ("blue", "green", "orange", "purple", "pink", "yellow", "red", "brown", "gray")
PROPS_MAX, OPTS_MAX, VIEWS_MAX = 30, 50, 10


@contextlib.contextmanager
def _stored_lang(lang):
    """Gespeicherte Vorgabe-Namen (Status-Optionen, Ansichtsnamen) in der Sprache des Kontos wie im Web
    (pages.note_create -> notes._default_schema(account_lang(owner))); ohne lang die Sprache der Anfrage."""
    tok_ = i18n.set_lang(lang) if lang else None
    try:
        yield
    finally:
        if tok_ is not None:
            i18n._cur.reset(tok_)


def _new_id(prefix, used):
    while True:
        x = prefix + secrets.token_hex(3)
        if x not in used:
            used.add(x)
            return x


def _name(v, n=60, what="name"):
    if not isinstance(v, str) or not v.strip():
        raise Fail(400, t("„{k}“ fehlt.", k=what))
    return " ".join(v.split())[:n]


def _options(raw, used_ids, status=False, start=0):
    """["A", "B"] oder [{name, color?, group?}] -> Optionen mit IDs (Farben reihum)."""
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > OPTS_MAX:
        raise Fail(400, t("„options“ muss eine Liste sein (höchstens {n}).", n=OPTS_MAX))
    out = []
    for i, o in enumerate(raw):
        o = {"name": o} if isinstance(o, str) else o
        if not isinstance(o, dict):
            raise Fail(400, t("ungültige Option"))
        opt = {"id": _new_id("o", used_ids), "name": _name(o.get("name"), 40, "options.name"),
               "color": o.get("color") if o.get("color") in OPT_COLORS else COLOR_CYCLE[(start + i) % len(COLOR_CYCLE)]}
        if status:
            opt["group"] = o.get("group") if o.get("group") in STATUS_GROUPS else ("todo", "doing", "done")[min(i, 2)]
        out.append(opt)
    return out


def _db_target(tok, v):
    """Ziel-Datenbank (Relation, Widget-Quelle): ID, die der Token sehen darf (roots, Rolle) -> (id, owner, schema)."""
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise Fail(400, t("ungültige Angabe"))
    try:
        dbid = int(v)
    except (TypeError, ValueError):
        raise Fail(400, t("ungültige Angabe"))
    with _db() as c:
        owner, _ = _guard(c, tok, dbid, "view")
        schema = _schema_of(c, owner, dbid)            # 404, wenn es keine Datenbank ist
    return dbid, owner, schema


def prop_spec(tok, raw, used_ids, start=0, lang=None):
    """Eigenschaft aus der Eingabe -> Schema-Eintrag (fuer _clean_schema im Speicherweg). lang: Sprache der Vorgabe-Optionen."""
    if not isinstance(raw, dict):
        raise Fail(400, t("ungültige Eigenschaft"))
    name = _name(raw.get("name"), 60, "properties.name")
    typ = raw.get("type") or "text"
    if typ in PROP_REJECT:
        raise Fail(400, t("Eigenschaften vom Typ „{t}“ bitte im Web anlegen (Ausdrücke mit Eigenschafts-IDs).", t=typ))
    if typ not in PROP_TYPES_IN:
        raise Fail(400, t("Unbekannter Typ „{t}“ – möglich: {all}", t=str(typ)[:30], all=", ".join(PROP_TYPES_IN)))
    p = {"id": _new_id("p", used_ids), "name": name, "type": "select" if typ == "status" else typ}
    if typ in ("select", "multi_select", "status"):
        opts = raw.get("options")
        if typ == "status" and not opts:
            with _stored_lang(lang):
                opts = [{"name": t("Offen"), "color": "gray", "group": "todo"}, {"name": t("In Arbeit"), "color": "blue", "group": "doing"},
                        {"name": t("Erledigt"), "color": "green", "group": "done"}]
        p["options"] = _options(opts, used_ids, typ == "status", start)
        if typ == "status":
            p["status"] = True
    elif typ == "relation":
        p["target"], _o, _s = _db_target(tok, raw.get("target", raw.get("database_id")))
        if raw.get("two_way") is True:
            p["two_way"] = True
    elif typ == "date":
        for k in ("time", "range"):
            if raw.get(k) is True:
                p[k] = True
    elif typ == "number":
        if raw.get("format") in ("percent", "euro"):
            p["fmt"] = raw["format"]
    elif typ == "person":
        if raw.get("multi") is False:
            p["multi"] = False
    return p


def _find_prop(props, key):
    low = str(key or "").strip().casefold()
    for p in props:
        if p.get("id") == key:
            return p
    for p in props:
        if str(p.get("name") or "").strip().casefold() == low:
            return p
    return None


# Filter-Operatoren je Eigenschaftstyp - GENAU wie die Filterzeile im Web (static/pages.js OPS/opsOf): nur so zeigt das
# Web eine per API gespeicherte Ansicht mit dem richtigen Operator an. Bedeutung in saga/dbquery.py.
_REL_MORE = ("yesterday", "tomorrow", "last_week", "next_week", "last_month", "next_month", "this_year", "overdue",
             "next_days", "past_days", "on_or_before", "on_or_after")
_OPS_TEXT = ("contains", "not_contains", "is_empty", "not_empty")
_OPS_NUM = ("eq", "gt", "lt", "is_empty", "not_empty")
_OPS_AUTO = ("after", "before", "eq", "today", "this_week", "this_month") + tuple(x for x in _REL_MORE if x != "overdue")
OPS_BY_TYPE = {
    "text": _OPS_TEXT, "url": _OPS_TEXT, "number": _OPS_NUM, "calc": _OPS_NUM,
    "select": ("is", "is_not", "is_empty", "not_empty"), "multi_select": _OPS_TEXT, "relation": _OPS_TEXT, "list": _OPS_TEXT,
    "date": ("eq", "before", "after", "today", "this_week", "this_month") + _REL_MORE + ("is_empty", "not_empty"),
    "created": _OPS_AUTO, "edited": _OPS_AUTO, "checkbox": ("checked", "unchecked"),
    "person": ("me",) + _OPS_TEXT, "files": ("is_empty", "not_empty")}
NOVALUE_OPS = {"is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month", "yesterday", "tomorrow",
               "last_week", "next_week", "last_month", "next_month", "this_year", "overdue", "me"}
SORT_MAX, VFILTERS_MAX = 3, 10
_SG = ("open", "done", "todo", "doing")
_TITLE = {"id": "title", "type": "text"}


def ops_of(p):
    """Erlaubte Filter-Operatoren einer Eigenschaft (wie opsOf im Web)."""
    typ = p.get("type")
    if typ in ("formula", "rollup"):
        typ = {"number": "number", "checkbox": "checkbox", "date": "date", "list": "list"}.get(p.get("rtype"), "text")
    if typ == "select" and p.get("status") is True:
        return ("status_group",) + OPS_BY_TYPE["select"]
    return OPS_BY_TYPE.get(typ, _OPS_TEXT)


def _vprop(props, key, hide, where):
    """Eigenschaft eines Filters/einer Sortierung per Name oder ID ("title" = Titel-Spalte) - sonst 400."""
    if key == "title":
        return _TITLE
    p = _find_prop(props, key) if isinstance(key, str) and key.strip() else None
    if not p or p.get("id") in hide:
        raise Fail(400, t("{where}: Unbekannte Eigenschaft: {name}", where=where, name=str(key)[:60]))
    return p


def _opt_ref(p, v, where):
    """Option per ID oder Name (Gross/Klein egal) -> ID; unbekannt -> 400 mit den moeglichen Namen."""
    opts = [o for o in p.get("options") or [] if isinstance(o, dict)]
    if isinstance(v, str):
        for o in opts:
            if o.get("id") == v:
                return v
        low = " ".join(v.split()).casefold()
        for o in opts:
            if " ".join(str(o.get("name") or "").split()).casefold() == low:
                return o.get("id")
    raise Fail(400, t("{where}: Option „{v}“ gibt es bei „{p}“ nicht (möglich: {all}).", where=where, v=str(v)[:60],
                      p=p.get("name"), all=", ".join(str(o.get("name")) for o in opts[:20]) or "–"))


def _is_day(s):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        return False
    try:
        date.fromisoformat(s)
    except ValueError:
        return False
    return True


def _num_str(v):
    if isinstance(v, bool):
        raise ValueError
    n = float(v)
    if n != n or n in (float("inf"), float("-inf")):
        raise ValueError
    return str(int(n)) if n.is_integer() and abs(n) < 2 ** 53 else repr(n)


def filter_spec(raw, props, hide=(), where="filters"):
    """Filter wie bei query_database ({property: Name|ID|"title", op, value}; Optionen per Name) -> gespeichertes Format der
    Ansicht wie im Web ({pid, op, value: Text}; Auswahl als Options-ID). Unbekanntes -> 400."""
    if not isinstance(raw, list) or len(raw) > VFILTERS_MAX:
        raise Fail(400, t("„{k}“ muss eine Liste sein (höchstens {n}).", k=where, n=VFILTERS_MAX))
    out = []
    for f in raw:
        if not isinstance(f, dict):
            raise Fail(400, t("ungültige Angabe"))
        p = _vprop(props, f.get("property", f.get("pid")), hide, where)
        op = f.get("op")
        allowed = ops_of(p)
        if op not in allowed:
            raise Fail(400, t("{where}: Operator „{op}“ passt nicht zu „{p}“ (möglich: {all}).", where=where, op=str(op)[:30],
                              p=p.get("name") or t("Name"), all=", ".join(allowed)))
        v = f.get("value")
        if op in NOVALUE_OPS:
            val = ""
        elif v is None or isinstance(v, str) and not v.strip():
            raise Fail(400, t("{where}: Wert fehlt (Operator „{op}“).", where=where, op=op))
        elif op == "status_group":
            if v not in _SG:
                raise Fail(400, t("{where}: status_group braucht open, done, todo oder doing.", where=where))
            val = v
        elif p.get("type") in ("select", "multi_select") and op in ("is", "is_not", "contains", "not_contains"):
            val = _opt_ref(p, v, where)
        elif op in ("next_days", "past_days"):
            if isinstance(v, bool) or not str(v).strip().isdigit() or not 1 <= int(str(v).strip()) <= 366:
                raise Fail(400, t("{where}: „{op}“ braucht eine Anzahl Tage (1–366).", where=where, op=op))
            val = str(int(str(v).strip()))
        elif op in ("on_or_before", "on_or_after") or op in ("eq", "before", "after") and p.get("type") in ("date", "created", "edited"):
            val = str(v).strip()
            if not (op in ("on_or_before", "on_or_after") and re.fullmatch(r"today(?:[+-][0-9]{1,4})?", val)) and not _is_day(val):
                raise Fail(400, t("{where}: Datum als JJJJ-MM-TT angeben.", where=where))
        elif op in ("eq", "gt", "lt"):
            try:
                val = _num_str(v)
            except (TypeError, ValueError, OverflowError):
                raise Fail(400, t("{where}: „{op}“ braucht eine Zahl.", where=where, op=op))
        elif isinstance(v, (str, int)) and not isinstance(v, bool):
            val = str(v)[:200]
        else:
            raise Fail(400, t("ungültige Angabe"))
        out.append({"pid": p["id"], "op": op, "value": val})
    return out


def sort_spec(raw, props, hide=(), where="sorts"):
    """[{property, dir asc|desc}] -> [{pid, dir}] (hoechstens 3 Stufen wie im Web)."""
    if not isinstance(raw, list) or len(raw) > SORT_MAX:
        raise Fail(400, t("„{k}“ muss eine Liste sein (höchstens {n}).", k=where, n=SORT_MAX))
    out = []
    for s_ in raw:
        if not isinstance(s_, dict) or s_.get("dir", "asc") not in ("asc", "desc"):
            raise Fail(400, t("{where}: dir muss asc oder desc sein.", where=where))
        p = _vprop(props, s_.get("property", s_.get("pid")), hide, where)
        out.append({"pid": p["id"], "dir": s_.get("dir", "asc")})
    return out


def _view_name(raw, typ, lang):
    name = " ".join(str(raw.get("name") or "").split())[:40]
    if name:
        return name
    with _stored_lang(lang):
        return {"table": t("Tabelle"), "board": t("Board"), "list": t("Liste"), "gallery": t("Galerie"), "calendar": t("Kalender"),
                "timeline": t("Zeitleiste"), "chart": t("Diagramm")}[typ]


def _vref(props, v, key, types, what):
    p = _find_prop(props, key)
    if not p or p["type"] not in types:
        raise Fail(400, t("Ansicht „{v}“: {what} „{p}“ nicht gefunden (Typ {types}).", v=v["name"], what=what, p=str(key)[:60],
                          types="/".join(types)))
    return p["id"]


def view_spec(raw, props, used_ids, lang=None, hide=()):
    """Neue Ansicht: type, name, group_by, date_by, end_by, filters, sorts (wie query_database)."""
    if not isinstance(raw, dict) or raw.get("type", "table") not in VIEW_TYPES:
        raise Fail(400, t("Ansicht: type muss einer von {all} sein.", all=", ".join(sorted(VIEW_TYPES))))
    typ = raw.get("type", "table")
    v = {"id": _new_id("v", used_ids), "type": typ, "name": _view_name(raw, typ, lang)}

    def prop(key, types, what):
        return _vref(props, v, key, types, what)
    if typ == "board" or raw.get("group_by"):
        g = raw.get("group_by")
        if g is None:
            cand = [p for p in props if p["type"] == "select" and p.get("status")] + [p for p in props if p["type"] == "select"]
            if not cand:
                raise Fail(400, t("Ein Board braucht eine Auswahl-Eigenschaft (group_by)."))
            v["group_by"] = cand[0]["id"]
        else:
            v["group_by"] = prop(g, ("select", "person"), "group_by")
    if typ in ("calendar", "timeline") or raw.get("date_by"):
        d = raw.get("date_by")
        if d is None:
            cand = [p for p in props if p["type"] == "date"]
            if not cand:
                raise Fail(400, t("Kalender und Zeitleiste brauchen eine Datums-Eigenschaft (date_by)."))
            v["date_by"] = cand[0]["id"]
        else:
            v["date_by"] = prop(d, ("date", "created", "edited"), "date_by")
    if raw.get("end_by"):
        v["end_by"] = prop(raw["end_by"], ("date",), "end_by")
    where = t("Ansicht „{v}“", v=v["name"])
    if raw.get("filters"):
        v["filters"] = filter_spec(raw["filters"], props, hide, where)
    if raw.get("sorts"):
        v["sorts"] = sort_spec(raw["sorts"], props, hide, where)
    return v


def _find_view(views, key):
    """Ansicht per ID oder Name (Gross/Klein egal)."""
    if not isinstance(key, str) or not key.strip():
        return None
    for v in views:
        if v.get("id") == key:
            return v
    low = " ".join(key.split()).casefold()
    hit = [v for v in views if " ".join(str(v.get("name") or "").split()).casefold() == low]
    return hit[0] if len(hit) == 1 else None


def view_change(views, raw, props, hide=()):
    """Bestehende Ansicht aendern (nie loeschen): {view: ID|Name, name?, filters?, sorts?, group_by?}. filters/sorts
    ersetzen die bisherigen ([] = keine). -> Name der Ansicht."""
    if not isinstance(raw, dict):
        raise Fail(400, t("ungültige Angabe"))
    key = raw.get("view", raw.get("id"))
    v = _find_view(views, key)
    if v is None:
        raise Fail(400, t("Ansicht „{v}“ nicht gefunden (ID oder eindeutiger Name; möglich: {all}).", v=str(key)[:60],
                          all=", ".join(str(x.get("name")) for x in views[:20]) or "–"))
    if not any(k in raw for k in ("name", "filters", "sorts", "group_by")):
        raise Fail(400, t("Ansicht „{v}“: nichts zu ändern (name, filters, sorts, group_by).", v=v.get("name")))
    if "name" in raw:
        v["name"] = _name(raw["name"], 40, "name")
    where = t("Ansicht „{v}“", v=v.get("name"))
    if "filters" in raw:
        fl = filter_spec(raw["filters"] if raw["filters"] is not None else [], props, hide, where)
        if fl:
            v["filters"] = fl
        else:
            v.pop("filters", None)
    if "sorts" in raw:
        so = sort_spec(raw["sorts"] if raw["sorts"] is not None else [], props, hide, where)
        if so:
            v["sorts"] = so
        else:
            v.pop("sorts", None)
        v.pop("sort", None)                      # altes Einzelfeld (wie das Web beim Bearbeiten)
    if "group_by" in raw:
        if raw["group_by"] is None:
            if v.get("type") == "board":
                raise Fail(400, t("Ein Board braucht eine Auswahl-Eigenschaft (group_by)."))
            v.pop("group_by", None)
        else:
            v["group_by"] = _vref(props, v, raw["group_by"], ("select", "person"), "group_by")
    return v.get("name")


def _save_schema(tok, dbid, schema):
    from saga.pages import _Reply, _note_update
    try:
        d = _relay(_note_update(tok.uid, int(dbid), {"schema": schema}, "api"))
    except _Reply as r:
        d = _relay(r.resp)
    return d


def database_create(tok, b):
    """{title, parent_id, icon?, properties?, views?, title_name?, embed_in_page?} -> (201, {id, embedded?, schema})."""
    title = _name(b.get("title"), 200, "title")
    parent = b.get("parent_id")
    props_in, views_in = b.get("properties"), b.get("views")
    if props_in is not None and (not isinstance(props_in, list) or len(props_in) > PROPS_MAX - 1):
        raise Fail(400, t("„properties“ muss eine Liste sein (höchstens {n}).", n=PROPS_MAX - 1))
    if views_in is not None and (not isinstance(views_in, list) or not 0 < len(views_in) <= VIEWS_MAX):
        raise Fail(400, t("„views“ muss eine Liste mit 1 bis {n} Ansichten sein.", n=VIEWS_MAX))
    embed = b.get("embed_in_page") is True
    if embed and parent is None:
        raise Fail(400, t("embed_in_page braucht parent_id."))
    used = set()
    props = None
    lang = account_lang(tok.uid)
    if parent is not None:                      # gespeicherte Namen in der Sprache des Eigentuemers (wie note_create)
        with _db() as c:
            _guard(c, tok, parent, "edit")
            lang = account_lang(Access(c, tok.uid).owner(int(parent)) or tok.uid)
    if props_in is not None:                    # vorher pruefen: Fehler legen keine halbe Datenbank an
        props, names = [], set()
        for i, raw in enumerate(props_in):
            p = prop_spec(tok, raw, used, i, lang)
            if p["name"].casefold() in names:
                raise Fail(400, t("Eigenschaft „{name}“ doppelt.", name=p["name"]))
            names.add(p["name"].casefold())
            props.append(p)
    if parent is not None:
        with _db() as c:
            _guard(c, tok, parent, "edit")
            locked = c.execute("SELECT locked FROM note WHERE id=?", (int(parent),)).fetchone()
        if embed and locked and locked[0]:
            raise Fail(423, t("Seite ist gesperrt – erst entsperren"))
    req = {"title": title, "parent_id": parent, "kind": "database"}
    if b.get("icon"):
        req["icon"] = b["icon"]
    _, d = page_create(tok, req)
    dbid = d["id"]
    try:
        with _db() as c:
            owner = Access(c, tok.uid).owner(dbid)
            cur = _schema_of(c, owner, dbid)
        if props is not None or views_in is not None or b.get("title_name"):
            new = dict(cur)
            if props is not None:
                new["props"] = props
            all_props = new.get("props") or []
            if views_in is not None:
                new["views"] = [view_spec(v, all_props, used, lang) for v in views_in]
            elif props is not None:             # wie im Web: Tabelle, mit Auswahl-Eigenschaft zusaetzlich ein Board
                new["views"] = [view_spec({"type": "table"}, all_props, used, lang)]
                if any(p["type"] == "select" for p in all_props):
                    new["views"].append(view_spec({"type": "board"}, all_props, used, lang))
            if b.get("title_name"):
                new["title_name"] = _name(b["title_name"], 60, "title_name")
            _save_schema(tok, dbid, new)
    except Fail:
        from saga.pages import note_trash
        note_trash(tok.uid, dbid, "api")          # keine halbe Datenbank stehen lassen
        raise
    out = {"id": dbid}
    if embed:
        _, e = blocks_edit(tok, parent, lambda blocks: _insert(blocks, {"type": "database", "data": {"db": dbid}}, "end"))
        out["embedded"] = {"page_id": int(parent), "block_id": e.get("block_id")}
    with _db() as c:
        owner = Access(c, tok.uid).owner(dbid)
        out["schema"] = schema_out(_schema_of(c, owner, dbid))
    return 201, out


def schema_update(tok, dbid, b):
    """Nur ergaenzen/umbenennen: add_properties, rename_properties [{property, name}], add_options [{property, options}],
    add_views, update_views [{view, name?, filters?, sorts?, group_by?}], title_name. Nie loeschen (keine Eigenschaft,
    Option oder Ansicht; update_views ersetzt nur Filter/Sortierung/Gruppierung/Name einer Ansicht)."""
    with _db() as c:
        owner, _ = _guard(c, tok, dbid, "edit")
        cur = _schema_of(c, owner, int(dbid))
        hide = hidden_props(c, tok, owner, cur)
    lang = account_lang(owner)
    new = json.loads(json.dumps(cur))
    props = [p for p in new.get("props") or [] if isinstance(p, dict)]
    new["props"] = props
    used = {p.get("id") for p in props} | {o.get("id") for p in props for o in p.get("options") or [] if isinstance(o, dict)} \
        | {v.get("id") for v in new.get("views") or [] if isinstance(v, dict)}
    did = []
    for k in ("add_properties", "rename_properties", "add_options", "add_views", "update_views"):
        if b.get(k) is not None and not isinstance(b[k], list):
            raise Fail(400, t("„{k}“ muss eine Liste sein.", k=k))
    for raw in b.get("add_properties") or []:
        if len(props) >= PROPS_MAX:
            raise Fail(400, t("Die Datenbank hat schon 30 Eigenschaften"))
        p = prop_spec(tok, raw, used, len(props), lang)
        if _find_prop(props, p["name"]) or p["name"].casefold() == str(new.get("title_name") or t("Name")).casefold():
            raise Fail(400, t("Eigenschaft „{name}“ gibt es schon.", name=p["name"]))
        props.append(p)
        did.append(t("Eigenschaft „{name}“ angelegt", name=p["name"]))
    for raw in b.get("rename_properties") or []:
        p = _find_prop(props, raw.get("property")) if isinstance(raw, dict) else None
        if not p:
            raise Fail(400, t("Unbekannte Eigenschaft: {name}", name=str((raw or {}).get("property") if isinstance(raw, dict) else raw)[:60]))
        nn = _name(raw.get("name"), 60, "name")
        other = _find_prop(props, nn)
        if other and other is not p:
            raise Fail(400, t("Eigenschaft „{name}“ gibt es schon.", name=nn))
        did.append(t("„{a}“ heißt jetzt „{b}“", a=p.get("name"), b=nn))
        p["name"] = nn
    for raw in b.get("add_options") or []:
        p = _find_prop(props, raw.get("property")) if isinstance(raw, dict) else None
        if not p or p.get("type") not in ("select", "multi_select"):
            raise Fail(400, t("Optionen gehen nur bei Auswahl-Eigenschaften."))
        have = {str(o.get("name") or "").casefold() for o in p.get("options") or []}
        add = [o for o in (raw.get("options") or []) if str(o.get("name") if isinstance(o, dict) else o).strip().casefold() not in have]
        opts = _options(add, used, bool(p.get("status")), len(p.get("options") or []))
        if len(p.get("options") or []) + len(opts) > OPTS_MAX:
            raise Fail(400, t("Höchstens {n} Optionen je Eigenschaft.", n=OPTS_MAX))
        p.setdefault("options", []).extend(opts)
        if opts:
            did.append(t("{n} Optionen bei „{name}“ ergänzt", n=len(opts), name=p.get("name")))
    for raw in b.get("add_views") or []:
        views = new.setdefault("views", [])
        if len(views) >= VIEWS_MAX:
            raise Fail(400, t("Höchstens {n} Ansichten.", n=VIEWS_MAX))
        v = view_spec(raw, props, used, lang, hide)
        views.append(v)
        did.append(t("Ansicht „{name}“ angelegt", name=v["name"]))
    for raw in b.get("update_views") or []:
        views = [v for v in new.get("views") or [] if isinstance(v, dict)]
        did.append(t("Ansicht „{name}“ geändert", name=view_change(views, raw, props, hide)))
    if b.get("title_name"):
        new["title_name"] = _name(b["title_name"], 60, "title_name")
        did.append(t("Titel-Spalte heißt „{name}“", name=new["title_name"]))
    if not did:
        raise Fail(400, t("nichts zu ändern"))
    _save_schema(tok, dbid, new)                   # srev aus dem gelesenen Stand: fremde neue Spalten bleiben (BUG-001)
    with _db() as c:
        sch = schema_out(_schema_of(c, owner, int(dbid)))
    return 200, {"id": int(dbid), "done": did, "schema": sch}


# --- Bloecke einfuegen (Widgets, eingebettete Datenbank) -----------------------------------------------------------
def _insert(blocks, blk, position="end", after=None):
    used = blockedit.used_ids(blocks)
    blockedit.fresh_ids([blk], used)
    if after is not None:
        _b, lst, i, _ch = blockedit.find_block(blocks, after)
        lst.insert(i + 1, blk)
    elif position == "start":
        blocks.insert(0, blk)
    elif position in (None, "end"):
        blocks.append(blk)
    else:
        raise blockedit.EditError(400, t("position: end, start oder after_block"))
    return {"block_id": blk["id"]}


def _w_src_dbs(cfg):
    out = [cfg.get("db")] if cfg.get("db") is not None else []
    out += [x.get("db") for x in cfg.get("dbs") or [] if isinstance(x, dict)]
    return out


def _w_hidden(c, tok, acc, cfg, dbs):
    """Token mit roots: nutzt das Widget eine Eigenschaft, die query_database ausblendet (hidden_props)?"""
    refs = {cfg.get(k) for k in ("prop", "date", "x", "y", "series")} | {x for x in cfg.get("habits") or [] if isinstance(x, str)}
    for x in dbs:
        try:
            if refs & hidden_props(c, tok, acc.owner(x), _schema_of(c, acc.owner(x), x)):
                return True
        except Fail:
            return True
    return False


def _resolve_cfg(tok, kind, cfg):
    """Namen -> IDs (Ansicht, Eigenschaften) und Rechte der Quellen pruefen."""
    cfg = dict(cfg)
    if cfg.get("src") == "tasks" and not tok.has("read:tasks"):
        raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="read:tasks"), "insufficient_scope")
    if kind == "upcoming":                       # persoenlich: Aufgaben bzw. CalDAV-Termine der betrachtenden Person
        for need, on in (("read:tasks", cfg.get("tasks") is not False), ("read:calendar", cfg.get("cal") is not False)):
            if on and not tok.has(need):
                raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s=need), "insufficient_scope")
    srcs = []
    if kind in ("progress", "metric", "quote", "habit", "chart") and cfg.get("db") is not None:
        srcs.append(cfg)
    if kind == "upcoming":
        srcs += [x for x in cfg.get("dbs") or [] if isinstance(x, dict)]
    for s in srcs:
        if not tok.has("read:db"):
            raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="read:db"), "insufficient_scope")
        s["db"], _o, schema = _db_target(tok, s.get("db"))
        if kind in ("progress", "quote") and cfg.get("src") is None:
            cfg["src"] = "db"                    # Quelle Datenbank, wenn db angegeben ist
        props = [p for p in schema.get("props") or [] if isinstance(p, dict)]
        if s.get("view"):
            v = next((x for x in schema.get("views") or [] if isinstance(x, dict) and (x.get("id") == s["view"]
                      or str(x.get("name") or "").casefold() == str(s["view"]).casefold())), None)
            if v is None:
                raise Fail(400, t("Ansicht „{v}“ nicht gefunden.", v=str(s["view"])[:40]))
            s["view"] = v["id"]
        for k in ("prop", "date", "x", "y", "series"):
            if isinstance(s.get(k), str) and s[k] and s[k] != "title":
                p = _find_prop(props, s[k])
                if p is None:
                    raise Fail(400, t("Unbekannte Eigenschaft: {name}", name=s[k][:60]))
                s[k] = p["id"]
        if isinstance(s.get("habits"), list):
            hs = []
            for h in s["habits"]:
                p = _find_prop(props, h)
                if p is None:
                    raise Fail(400, t("Unbekannte Eigenschaft: {name}", name=str(h)[:60]))
                hs.append(p["id"])
            s["habits"] = hs
    return cfg


def widget_data(tok, a, base=None):
    """Eingabe -> bereinigtes data eines Widget-Blocks. base: bisheriges data (update_widget, Felder ergaenzen)."""
    base = base or {}
    kind = a.get("kind") or base.get("kind")
    if kind not in page_widgets.KINDS:
        raise Fail(400, t("Unbekannte Widget-Art – möglich: {all}", all=", ".join(page_widgets.KINDS)))
    d = {"kind": kind}
    for k in ("title", "size", "style", "accent"):
        v = a.get(k, base.get(k))
        if v is not None:
            d[k] = v
    if a.get("size") not in (None,) + page_widgets.SIZES or a.get("style") not in (None,) + page_widgets.STYLES:
        raise Fail(400, t("size: s, m, l · style: card, plain"))
    cfg_in = a.get("cfg")
    if cfg_in is not None and not isinstance(cfg_in, dict):
        raise Fail(400, t("„cfg“ muss ein Objekt sein."))
    cfg = dict(base.get("cfg") or {}) if base.get("kind") == kind else {}
    cfg.update(cfg_in or {})
    d["cfg"] = _resolve_cfg(tok, kind, cfg)
    return page_widgets.clean_data(d)


def widget_add(tok, nid, a):
    data = widget_data(tok, a)
    pos, after = a.get("position"), a.get("after_block")
    if after is not None and not isinstance(after, str):
        raise Fail(400, t("ungültige Angabe"))
    _, d = blocks_edit(tok, nid, lambda blocks: _insert(blocks, {"type": "widget", "data": data}, pos, after), a.get("rev"))
    return 200, {"ok": True, "rev": d.get("rev"), "block_id": d.get("block_id"), "widget": data}


def widget_update(tok, nid, a):
    res = {}

    def fn(blocks):
        b = blockedit.find_block(blocks, a.get("block_id"))[0]
        if b.get("type") != "widget":
            raise blockedit.EditError(400, t("Der Block ist kein Widget."))
        res["data"] = b["data"] = widget_data(tok, a, b.get("data") if isinstance(b.get("data"), dict) else {})
        return {"block_id": b.get("id")}
    _, d = blocks_edit(tok, nid, fn, a.get("rev"))
    return 200, {"ok": True, "rev": d.get("rev"), "block_id": d.get("block_id"), "widget": res.get("data")}


def widgets_get(tok, nid, tz=None):
    """Werte der Widgets einer Seite (wie GET /api/notes/{id}/widgets) - eingeschraenkt auf das, was der Token darf."""
    from saga.api_v1 import page_read
    _, pg = page_read(tok, nid, "blocks")
    params = {"tz": tz} if tz else {}
    if tz is not None and not page_widgets.tz_ok(tz):
        raise Fail(400, t("Unbekannte Zeitzone"))
    vals = _relay(page_widgets.page_widgets(tok.uid, int(nid), params)).get("widgets") or {}
    out = []
    from saga.notes import _iter_blocks
    with _db() as c:
        for b in _iter_blocks(pg.get("blocks") or []):
            if b.get("type") != "widget" or not isinstance(b.get("data"), dict):
                continue
            d = b["data"]
            cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
            e = {"block_id": b.get("id"), "kind": d.get("kind"), "title": d.get("title") or "", "cfg": cfg}
            w = vals.get(b.get("id"))
            allowed = True
            if d.get("kind") == "upcoming":         # Aufgaben/Termine der Person: nur ohne roots und mit den Bereichen
                allowed = not tok.roots and (cfg.get("tasks") is False or tok.has("read:tasks")) and (
                    cfg.get("cal") is False or tok.has("read:calendar")) and (not cfg.get("dbs") or tok.has("read:db"))
            elif d.get("kind") in page_widgets.PERSONAL:
                allowed = not tok.roots
            elif cfg.get("src") == "tasks":
                allowed = tok.has("read:tasks")
            else:
                dbs = [x for x in _w_src_dbs(cfg) if isinstance(x, int)]
                if dbs:
                    acc = Access(c, tok.uid)
                    allowed = tok.has("read:db") and all(acc.owner(x) and in_roots(c, tok, acc.owner(x), x) for x in dbs)
                    if allowed and tok.roots:       # wie query_database: Relationen/Rollups auf fremde Baeume zeigen nichts
                        allowed = not _w_hidden(c, tok, acc, cfg, dbs)
            if w is not None:
                e["value"] = w if allowed else {"kind": d.get("kind"), "error": "no_access"}
            elif d.get("kind") == "progress" and cfg.get("src") == "fixed":
                e["value"] = {"kind": "progress", "value": cfg.get("value"), "max": cfg.get("max")}
            out.append(e)
    return 200, {"widgets": out}


def database_embed(tok, nid, a):
    dbid, _o, _s = _db_target(tok, a.get("database_id"))
    blk = {"type": "database", "data": {"db": dbid}}
    pos, after = a.get("position"), a.get("after_block")
    _, d = blocks_edit(tok, nid, lambda blocks: _insert(blocks, blk, pos, after), a.get("rev"))
    return 200, {"ok": True, "rev": d.get("rev"), "block_id": d.get("block_id")}
