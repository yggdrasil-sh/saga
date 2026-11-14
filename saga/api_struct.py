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
  (get_widgets) nur unter denselben Bedingungen, persoenliche Arten (upcoming, reading) nur ohne roots mit read:tasks."""
import json
import secrets

from db import connect as _db
from saga import blockedit, page_widgets
from saga.api_v1 import Fail, _guard, _relay, _schema_of, blocks_edit, page_create, schema_out
from saga.apitok import in_roots
from saga.core import t
from saga.notes import OPT_COLORS, STATUS_GROUPS, VIEW_TYPES
from saga.sharing import Access

PROP_TYPES_IN = ("text", "number", "select", "multi_select", "status", "date", "checkbox", "url", "created", "edited",
                 "person", "files", "relation")
PROP_REJECT = ("formula", "rollup", "calc")
COLOR_CYCLE = ("blue", "green", "orange", "purple", "pink", "yellow", "red", "brown", "gray")
PROPS_MAX, OPTS_MAX, VIEWS_MAX = 30, 50, 10


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


def prop_spec(tok, raw, used_ids, start=0):
    """Eigenschaft aus der Eingabe -> Schema-Eintrag (fuer _clean_schema im Speicherweg)."""
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


def view_spec(raw, props, used_ids):
    if not isinstance(raw, dict) or raw.get("type", "table") not in VIEW_TYPES:
        raise Fail(400, t("Ansicht: type muss einer von {all} sein.", all=", ".join(sorted(VIEW_TYPES))))
    typ = raw.get("type", "table")
    v = {"id": _new_id("v", used_ids), "type": typ, "name": " ".join(str(raw.get("name") or "").split())[:40] or {
        "table": t("Tabelle"), "board": t("Board"), "list": t("Liste"), "gallery": t("Galerie"), "calendar": t("Kalender"),
        "timeline": t("Zeitleiste"), "chart": t("Diagramm")}[typ]}

    def prop(key, types, what):
        p = _find_prop(props, key)
        if not p or p["type"] not in types:
            raise Fail(400, t("Ansicht „{v}“: {what} „{p}“ nicht gefunden (Typ {types}).", v=v["name"], what=what, p=str(key)[:60],
                              types="/".join(types)))
        return p["id"]
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
    return v


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
    if props_in is not None:                    # vorher pruefen: Fehler legen keine halbe Datenbank an
        props, names = [], set()
        for i, raw in enumerate(props_in):
            p = prop_spec(tok, raw, used, i)
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
                new["views"] = [view_spec(v, all_props, used) for v in views_in]
            elif props is not None:             # wie im Web: Tabelle, mit Auswahl-Eigenschaft zusaetzlich ein Board
                new["views"] = [view_spec({"type": "table"}, all_props, used)]
                if any(p["type"] == "select" for p in all_props):
                    new["views"].append(view_spec({"type": "board"}, all_props, used))
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
    add_views, title_name. Nie loeschen."""
    with _db() as c:
        owner, _ = _guard(c, tok, dbid, "edit")
        cur = _schema_of(c, owner, int(dbid))
    new = json.loads(json.dumps(cur))
    props = [p for p in new.get("props") or [] if isinstance(p, dict)]
    new["props"] = props
    used = {p.get("id") for p in props} | {o.get("id") for p in props for o in p.get("options") or [] if isinstance(o, dict)} \
        | {v.get("id") for v in new.get("views") or [] if isinstance(v, dict)}
    did = []
    for k in ("add_properties", "rename_properties", "add_options", "add_views"):
        if b.get(k) is not None and not isinstance(b[k], list):
            raise Fail(400, t("„{k}“ muss eine Liste sein.", k=k))
    for raw in b.get("add_properties") or []:
        if len(props) >= PROPS_MAX:
            raise Fail(400, t("Die Datenbank hat schon 30 Eigenschaften"))
        p = prop_spec(tok, raw, used, len(props))
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
        v = view_spec(raw, props, used)
        views.append(v)
        did.append(t("Ansicht „{name}“ angelegt", name=v["name"]))
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


def _resolve_cfg(tok, kind, cfg):
    """Namen -> IDs (Ansicht, Eigenschaften) und Rechte der Quellen pruefen."""
    cfg = dict(cfg)
    if cfg.get("src") == "tasks" and not tok.has("read:tasks"):
        raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="read:tasks"), "insufficient_scope")
    if kind == "upcoming" and (cfg.get("tasks") is not False or cfg.get("cal") is not False) and not tok.has("read:tasks"):
        raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="read:tasks"), "insufficient_scope")
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
            if d.get("kind") in page_widgets.PERSONAL:
                allowed = not tok.roots and tok.has("read:tasks")
            elif cfg.get("src") == "tasks":
                allowed = tok.has("read:tasks")
            else:
                dbs = [x for x in _w_src_dbs(cfg) if isinstance(x, int)]
                if dbs:
                    acc = Access(c, tok.uid)
                    allowed = tok.has("read:db") and all(acc.owner(x) and in_roots(c, tok, acc.owner(x), x) for x in dbs)
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
