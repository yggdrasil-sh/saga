"""Oeffentliche API /api/v1 (2.5, Paket 1 E5; Konzept P1-E5): duenne, versionierte Schicht ueber den internen Funktionen.

Anmeldung NUR mit API-Token (Authorization: Bearer sga_..., saga.apitok) - keine Sitzung, kein X-Saga, kein CORS.
Rechte = Bereich des Tokens (read:/write: pages, db, tasks, files, comments) UND optionale Seitenbaeume (roots) UND die
normale Rolle des Kontos (sharing.Access). Seiten ausserhalb der roots bzw. ohne Zugriff: 404 (wie nicht vorhanden).
Personen erscheinen nur als Handle ("~...") bzw. Prinzipal ("@name@host") mit Anzeigename - nie als Anmeldename.
Inhalte: Editor.js-Bloecke oder Markdown (lesen: page_tools.blocks_to_md, schreiben: md2blocks + pages.clean_blocks).
Fehler: {"error": text, "code": kurz} mit passendem Status. Doku: docs/API.md.

Die Funktionen unten (search, page_read, page_create, ...) nehmen (tok, ...) und liefern (status, dict) - saga.mcp ruft
dieselben auf, damit API und MCP-Server dieselben Regeln haben."""
import json
import os
import re

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import md2blocks
from saga.apitok import authed, in_roots, node_role
from saga.core import BASE_URL, _body, _json_load, local_ref, t
from saga.notes import FILES_DIR
from saga.sharing import Access

router = APIRouter()

MD_MAX = 400_000                 # Markdown-Eingabe (Zeichen)
LIMIT_DEFAULT, LIMIT_MAX = 50, 100
OUT_MD_MAX = 100_000             # Markdown-Ausgabe fuer MCP (Kuerzung mit Hinweis), API liefert alles
_PAGE_LINK = re.compile(r"^(?:(?:https?://[^/]+)?/notes#|page:|saga:)([1-9][0-9]{0,17})$")
_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 413: "too_large",
          415: "unsupported", 423: "locked", 429: "rate_limited", 507: "quota"}


class Fail(Exception):
    """Fehler mit Status - von API und MCP gleich behandelt."""

    def __init__(self, status, msg, code=None):
        super().__init__(msg)
        self.status, self.msg, self.code = status, msg, code or _CODES.get(status, "error")


def _resp(status, data):
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def _relay(r):
    """Antwort einer internen Funktion (JSONResponse) -> dict oder Fail."""
    try:
        d = json.loads(r.body)
    except (ValueError, AttributeError):
        d = {}
    if r.status_code >= 400:
        msg = d.get("error") if isinstance(d, dict) else None
        raise Fail(r.status_code, msg if isinstance(msg, str) and msg else "error")
    return d


def _guard(c, tok, nid, minimum="view"):
    owner, role, err = node_role(c, tok, nid, minimum)
    if err is not None:
        raise Fail(err.status_code, json.loads(err.body).get("error") or "error")
    return owner, role


def _who(p, uid):
    """sharing.person(...) -> {handle, name} (eigenes Konto ebenfalls als Handle)."""
    if not p:
        return None
    h = p.get("username")
    if p.get("me"):
        h = local_ref(uid)
    return {"handle": h, "name": p.get("name") or ""}


# --- Markdown <-> Bloecke ----------------------------------------------------------------------------------------
def md_to_blocks(md):
    """Markdown -> Editor.js-Bloecke. Seitenverweise: [Text](/notes#123), [Text](page:123) oder Saga-Adresse."""
    if not isinstance(md, str):
        raise Fail(400, t("ungültige Angabe"))
    if len(md) > MD_MAX:
        raise Fail(413, t("Seite zu groß"))

    def resolve(target, wiki):
        m = _PAGE_LINK.match((target or "").strip())
        return ("page", m.group(1)) if m and not wiki else None
    return md2blocks.parse(md, md2blocks.Ctx(resolve))


def blocks_to_markdown(blocks):
    from saga.page_tools import blocks_to_md
    return blocks_to_md(blocks or [], lambda h: BASE_URL + h if h.startswith("/") else h)


def _body_in(b):
    """Inhalt aus der Anfrage: markdown (Text) oder blocks (Liste) -> JSON-Text fuer pages oder None."""
    if b.get("markdown") is not None:
        return json.dumps({"blocks": md_to_blocks(b["markdown"])}, ensure_ascii=False)
    if b.get("blocks") is not None:
        if not isinstance(b["blocks"], list):
            raise Fail(400, t("ungültige Angabe"))
        return json.dumps({"blocks": b["blocks"]}, ensure_ascii=False)
    return None


# --- Seiten ------------------------------------------------------------------------------------------------------
def me(tok):
    from saga.core import user_info
    info = user_info(tok.uid) or {}
    return 200, {"handle": local_ref(tok.uid), "name": info.get("name") or "", "token": {"id": tok.id, "name": tok.name, "prefix": tok.prefix},
                 "scopes": sorted(tok.scopes), "roots": tok.roots, "expires": tok.expires}


def search(tok, q, limit=LIMIT_DEFAULT):
    from saga.pages import _note_search
    q = str(q or "").strip().lower()[:100]
    if not q:
        return 200, {"results": []}
    hits = _note_search(tok.uid, q, "")
    out = []
    with _db() as c:
        acc = Access(c, tok.uid)
        for h in hits:
            owner = acc.owner(h["id"])
            if not owner or not in_roots(c, tok, owner, h["id"]):
                continue
            out.append({"id": h["id"], "title": h.get("title") or "", "icon": h.get("icon") or "", "kind": h.get("kind"),
                        "parent_id": h.get("parent_id"), "updated": h.get("updated"), "snippet": h.get("snippet") or "",
                        "role": h.get("role") or "owner"})
            if len(out) >= limit:
                break
    return 200, {"results": out}


def page_read(tok, nid, fmt="blocks"):
    from saga.pages import note_get
    with _db() as c:
        owner, role = _guard(c, tok, nid, "view")
    d = _relay(note_get(tok.uid, int(nid)))
    out = {k: d.get(k) for k in ("id", "title", "icon", "kind", "parent_id", "updated", "created", "rev", "tags", "archived", "locked")}
    out["role"] = role
    out["owner"] = _who(d.get("owner"), tok.uid)
    out["updated_by"] = _who(d.get("updated_by"), tok.uid)
    if out["parent_id"] is not None:
        with _db() as c:
            if not in_roots(c, tok, owner, out["parent_id"]):
                out["parent_id"] = None             # Elternseite ausserhalb der freigegebenen Baeume: nicht einmal die ID
    if d.get("schema") is not None:
        out["schema"] = schema_out(d["schema"])
    if d.get("props"):
        out["properties"] = d["props"]
        par = _parent_db(nid) if tok.roots else None
        if par is not None:
            with _db() as c:
                po = Access(c, tok.uid).owner(par)
                hide = hidden_props(c, tok, po, _schema_of(c, po, par)) if po else set()
            out["properties"] = {k: v for k, v in d["props"].items() if k not in hide}
        if d.get("people"):
            out["people"] = d["people"]
    blocks = []
    if d.get("format") == "blocks":
        blocks = _json_load(d.get("body"), {}).get("blocks") or []
    elif d.get("body"):
        blocks = [{"type": "paragraph", "data": {"text": d["body"]}}]
    if fmt == "markdown":
        out["markdown"] = blocks_to_markdown(blocks)
    else:
        out["blocks"] = blocks
    return 200, out


def page_create(tok, b):
    from saga.pages import _Reply, _note_update, note_create, note_trash
    parent = b.get("parent_id")
    if parent is None and tok.roots:
        raise Fail(403, t("Dieser Token darf nur in freigegebenen Seitenbäumen anlegen – bitte parent_id angeben."))
    if parent is not None:
        with _db() as c:
            _guard(c, tok, parent, "edit")
            if not tok.has("write:db") and c.execute("SELECT 1 FROM note WHERE id=? AND kind='database'", (int(parent),)).fetchone():
                raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="write:db"), "insufficient_scope")
    if b.get("properties") is not None and not tok.has("write:db"):
        raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="write:db"), "insufficient_scope")
    body = _body_in(b)                                  # vorher pruefen: kaputtes Markdown legt keine leere Seite an
    req = {k: b[k] for k in ("title", "icon", "tags") if k in b}
    req["parent_id"] = parent
    if b.get("kind") == "database":
        req["kind"] = "database"
    if b.get("properties") is not None:
        req["props"] = props_in(tok, parent, b["properties"])
    d = _relay(note_create(tok.uid, req, "api"))
    nid = d["id"]
    if body is not None:
        try:
            _relay(_note_update(tok.uid, nid, {"body": body, "format": "blocks"}, "api"))
        except (_Reply, Fail) as ex:
            note_trash(tok.uid, nid, "api")             # halbe Seite nicht stehen lassen (nur als Eigentuemer moeglich)
            if isinstance(ex, Fail):
                raise
            _relay(ex.resp)
    return 201, {"id": nid, "updated": d.get("updated")}


def page_update(tok, nid, b):
    from saga.pages import _Reply, _note_update, note_get
    with _db() as c:
        owner, role = _guard(c, tok, nid, "edit")
    req = {k: b[k] for k in ("title", "icon", "tags", "rev") if k in b}
    body = _body_in(b)
    if b.get("append_markdown") is not None:
        cur = _relay(note_get(tok.uid, int(nid)))
        if cur.get("format") != "blocks":
            raise Fail(400, t("ungültige Angabe"))
        blocks = (_json_load(cur.get("body"), {}).get("blocks") or []) + md_to_blocks(b["append_markdown"])
        body = json.dumps({"blocks": blocks}, ensure_ascii=False)
        req.setdefault("rev", cur.get("rev"))           # dazwischen geaendert -> 409 statt still ueberschreiben
    if body is not None:
        req.update(body=body, format="blocks")
    if b.get("properties") is not None:
        if not tok.has("write:db"):                     # Eigenschaften sind Datenbank-Werte (write:pages reicht nicht)
            raise Fail(403, t("Dem Token fehlt der Bereich „{s}“.", s="write:db"), "insufficient_scope")
        par = _parent_db(nid)
        req["props"] = props_in(tok, par, b["properties"])
    if not req or set(req) == {"rev"}:
        raise Fail(400, t("nichts zu ändern"))
    try:
        d = _relay(_note_update(tok.uid, int(nid), req, "api"))
    except _Reply as r:
        _relay(r.resp)
    out = {"ok": True, "rev": d.get("rev"), "updated": d.get("updated")}
    if d.get("calc") and not tok.roots:                 # roots: berechnete Werte koennten fremde Seitenbaeume zeigen
        out["calc"] = d["calc"]
    return 200, out


def page_delete(tok, nid):
    from saga.pages import note_trash
    with _db() as c:
        _guard(c, tok, nid, "view")
    d = _relay(note_trash(tok.uid, int(nid), "api"))
    return 200, {"ok": True, "trashed": d.get("deleted", 0)}


def _parent_db(nid):
    with _db() as c:
        r = c.execute("SELECT d.id FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=? AND d.kind='database'",
                      (int(nid),)).fetchone()
    return r[0] if r else None


# --- Datenbanken -------------------------------------------------------------------------------------------------
def schema_out(s):
    """Schema fuer die API: Eigenschaften mit id, name, type (+ options mit id/name, Ziel der Relation), Ansichten."""
    props = []
    for p in (s or {}).get("props") or []:
        if not isinstance(p, dict):
            continue
        x = {"id": p.get("id"), "name": p.get("name"), "type": p.get("type")}
        if p.get("options"):
            x["options"] = [{"id": o.get("id"), "name": o.get("name")} for o in p["options"] if isinstance(o, dict)]
        for k in ("target", "rtype", "multi", "range"):
            if k in p:
                x[k] = p[k]
        props.append(x)
    return {"title_name": (s or {}).get("title_name") or t("Name"), "properties": props,
            "views": [{"id": v.get("id"), "name": v.get("name"), "type": v.get("type")} for v in (s or {}).get("views") or [] if isinstance(v, dict)]}


def _schema_of(c, owner, dbid):
    r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (dbid, owner)).fetchone()
    if not r:
        raise Fail(404, "not found")
    return _json_load(r[0], {"props": []})


def _prop_by(schema, key):
    """Eigenschaft nach ID oder (Gross/Klein egal) Name."""
    props = [p for p in schema.get("props") or [] if isinstance(p, dict)]
    for p in props:
        if p.get("id") == key:
            return p
    low = str(key).strip().lower()
    for p in props:
        if str(p.get("name") or "").strip().lower() == low:
            return p
    return None


def _opt_id(p, v):
    for o in p.get("options") or []:
        if v == o.get("id"):
            return v
    low = str(v).strip().lower()
    for o in p.get("options") or []:
        if str(o.get("name") or "").strip().lower() == low:
            return o.get("id")
    return v                                  # unbekannt: pages._clean_props lehnt ab


def props_in(tok, dbid, raw):
    """Eigenschaften nach Name oder ID -> {pid: wert}; Auswahl auch per Optionsname."""
    if not isinstance(raw, dict):
        raise Fail(400, t("ungültige Werte"))
    if dbid is None:
        raise Fail(400, t("Seite ist keine Datenbank-Zeile"))
    with _db() as c:
        acc = Access(c, tok.uid)
        owner = acc.owner(int(dbid))
        if not owner:
            raise Fail(404, "not found")
        schema = _schema_of(c, owner, int(dbid))
    out = {}
    for k, v in raw.items():
        p = _prop_by(schema, k)
        if not p:
            raise Fail(400, t("Unbekannte Eigenschaft: {name}", name=str(k)[:60]))
        if p.get("type") == "select" and v not in (None, ""):
            v = _opt_id(p, v)
        elif p.get("type") == "multi_select" and isinstance(v, list):
            v = [_opt_id(p, x) for x in v]
        out[p["id"]] = v
    return out


def _value_out(p, v):
    if p.get("type") == "select":
        return next((o.get("name") for o in p.get("options") or [] if o.get("id") == v), v)
    if p.get("type") == "multi_select" and isinstance(v, list):
        names = {o.get("id"): o.get("name") for o in p.get("options") or []}
        return [names.get(x, x) for x in v]
    return v


def hidden_props(c, tok, owner, schema):
    """Token mit roots: Relationen auf Datenbanken ausserhalb der roots, Rollups darueber und (vorsichtshalber) alle
    Formeln liefern nichts - sonst kaemen Titel/Werte fremder Seitenbaeume ueber die Zeile heraus."""
    if not tok.roots:
        return set()
    props = [p for p in (schema or {}).get("props") or [] if isinstance(p, dict)]
    out = set()
    for p in props:
        if p.get("type") == "relation":
            try:
                tgt = int(p.get("target"))
            except (TypeError, ValueError):
                tgt = None
            if tgt is None or not in_roots(c, tok, owner, tgt):
                out.add(p.get("id"))
    if out:
        out |= {p.get("id") for p in props if p.get("type") == "formula" or (p.get("type") == "rollup" and p.get("rel") in out)}
    return out


def row_out(schema, r, hide=()):
    props = {}
    named = {}
    for p in schema.get("props") or []:
        if not isinstance(p, dict) or p.get("id") not in (r.get("props") or {}) or p.get("id") in hide:
            continue
        v = r["props"][p["id"]]
        props[p["id"]] = v
        named[p.get("name") or p["id"]] = _value_out(p, v)
    out = {"id": r["id"], "title": r.get("title") or "", "icon": r.get("icon") or "", "created": r.get("created"),
           "updated": r.get("updated"), "values": props, "properties": named}
    if r.get("fx_err") and not hide:
        out["formula_errors"] = r["fx_err"]
    return out


def databases(tok, q="", limit=LIMIT_DEFAULT):
    """Datenbanken, die das Token sehen darf (eigene und geteilte, innerhalb der roots)."""
    q = str(q or "").strip().lower()[:100]
    out = []
    with _db() as c:
        acc = Access(c, tok.uid)
        cand = [(tok.uid, r[0], r[1]) for r in c.execute(
            "SELECT id, title FROM note WHERE uid=? AND kind='database' AND deleted_at IS NULL AND archived IS NULL AND is_template=0 "
            "ORDER BY updated DESC LIMIT 2000", (tok.uid,)).fetchall()]
        for o in acc.owners():
            ids = list(acc.tree(o))
            if ids:
                cand += [(o, r[0], r[1]) for r in c.execute("SELECT id, title FROM note WHERE uid=? AND id = ANY(?) AND kind='database'",
                                                            (o, ids)).fetchall()]
        for owner, i, title in cand:
            if q and q not in (title or "").lower():
                continue
            if not in_roots(c, tok, owner, i):
                continue
            out.append({"id": i, "title": title or "", "role": acc.role(i)})
            if len(out) >= limit:
                break
    return 200, {"databases": out}


def database_get(tok, dbid):
    with _db() as c:
        owner, role = _guard(c, tok, dbid, "view")
        schema = _schema_of(c, owner, int(dbid))
        title = c.execute("SELECT title FROM note WHERE id=?", (int(dbid),)).fetchone()[0]
    return 200, {"id": int(dbid), "title": title or "", "role": role, "schema": schema_out(schema)}


def database_query(tok, dbid, b):
    from saga.app_api import db_rows
    with _db() as c:
        owner, _ = _guard(c, tok, dbid, "view")
        schema = _schema_of(c, owner, int(dbid))
        hide = hidden_props(c, tok, owner, schema)
    if hide and b.get("q"):
        raise Fail(403, t("Dafür fehlt dir die Berechtigung."))
    try:
        limit = min(LIMIT_MAX, max(1, int(b.get("limit") or LIMIT_DEFAULT)))
        offset = max(0, int(b.get("offset") or 0))
    except (TypeError, ValueError):
        raise Fail(400, t("ungültige Angabe"))
    params = {"limit": str(limit), "offset": str(offset)}

    def fix(lst, kind):
        if not isinstance(lst, list) or len(lst) > 50:
            raise Fail(400, t("ungültige Angabe"))
        res = []
        for f in lst:
            if not isinstance(f, dict):
                raise Fail(400, t("ungültige Angabe"))
            f = dict(f)
            key = f.pop("property", None) if "property" in f else f.get("pid")
            if key not in (None, "title"):
                p = _prop_by(schema, key)
                if not p or p["id"] in hide:
                    raise Fail(400, t("Unbekannte Eigenschaft: {name}", name=str(key)[:60]))
                f["pid"] = p["id"]
                if kind == "filter" and p.get("type") in ("select", "multi_select") and isinstance(f.get("value"), str):
                    f["value"] = _opt_id(p, f["value"])
            elif key == "title":
                f["pid"] = "title"
            res.append(f)
        return json.dumps(res)
    if b.get("filters") is not None:
        params["filters"] = fix(b["filters"], "filter")
    if b.get("sorts") is not None:
        params["sorts"] = fix(b["sorts"], "sort")
    for k in ("view", "q", "tz"):
        if isinstance(b.get(k), str) and b[k]:
            params[k] = b[k][:500]
    d = _relay(db_rows(tok.uid, int(dbid), params))
    out = {"rows": [row_out(d.get("schema") or schema, r, hide) for r in d.get("rows") or []], "total": d.get("total"),
           "matched": d.get("matched"), "offset": offset, "limit": limit}
    if d.get("people"):
        out["people"] = {k: {"name": v.get("name"), "remote": bool(v.get("remote"))} for k, v in d["people"].items()}
    return 200, out


def row_create(tok, dbid, b):
    with _db() as c:
        _guard(c, tok, dbid, "edit")
        if not c.execute("SELECT 1 FROM note WHERE id=? AND kind='database'", (int(dbid),)).fetchone():
            raise Fail(400, t("Seite ist keine Datenbank-Zeile"))
    b = dict(b, parent_id=int(dbid))
    return page_create(tok, b)


def row_update(tok, rid, b):
    if _parent_db(rid) is None:
        raise Fail(400, t("Seite ist keine Datenbank-Zeile"))
    return page_update(tok, rid, b)


# --- Aufgaben ----------------------------------------------------------------------------------------------------
def tasks_list(tok, done=False):
    from saga.tasks import TASK_COLS, _task_row
    with _db() as c:
        if done:
            rows = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=1 ORDER BY done_at DESC LIMIT 100", (tok.uid,)).fetchall()
        else:
            rows = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=0 ORDER BY prio, due IS NULL, due, created LIMIT 500",
                             (tok.uid,)).fetchall()
    return 200, {"tasks": [_task_row(r) for r in rows]}


def task_create(tok, b):
    from saga.tasks import task_add
    d = _relay(task_add(tok.uid, b))
    return 201, d


def task_change(tok, tid, b):
    from saga.tasks import task_update
    return 200, _relay(task_update(tok.uid, int(tid), b))


# --- Kommentare --------------------------------------------------------------------------------------------------
def comments_get(tok, nid):
    from saga.comments import comments_list
    with _db() as c:
        _guard(c, tok, nid, "view")
    d = _relay(comments_list(tok.uid, int(nid), 1))
    th = []
    for x in d.get("threads") or []:
        th.append({"id": x["id"], "block_id": x.get("block_id") or None, "resolved": bool(x.get("resolved_at")),
                   "comments": [{"id": m["id"], "author": _who(m.get("author"), tok.uid), "text": m.get("body") or "",
                                 "created": m.get("created"), "edited": m.get("edited")} for m in x.get("comments") or []]})
    return 200, {"threads": th}


def comment_create(tok, nid, b):
    from saga.comments import comment_add
    with _db() as c:
        _guard(c, tok, nid, "comment")
    req = {"body": b.get("text") if b.get("text") is not None else b.get("body")}
    if b.get("thread_id") is not None:
        req["thread_id"] = b["thread_id"]
    else:
        req["block_id"] = b.get("block_id") or ""
    d = _relay(comment_add(tok.uid, int(nid), req))
    return 201, {"id": d["comment"]["id"], "thread_id": d.get("thread_id")}


# --- Router ------------------------------------------------------------------------------------------------------
async def _run(request, scope, write, fn, *args):
    tok, err = authed(request, write=write, scope=scope)
    if err is not None:
        return err
    try:
        st, data = await run_in_threadpool(fn, tok, *args)
    except Fail as f:
        return _resp(f.status, {"error": f.msg, "code": f.code})
    return _resp(st, data)


async def _json(request):
    b = await _body(request)
    if b is None:
        return None, _resp(400, {"error": t("ungültige Anfrage"), "code": "bad_request"})
    return b, None


def _limit(v):
    try:
        return min(LIMIT_MAX, max(1, int(v or LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        return LIMIT_DEFAULT


@router.get("/api/v1/me")
async def v1_me(request: Request):
    tok, err = authed(request)
    if err is not None:
        return err
    return _resp(*me(tok))


@router.get("/api/v1/search")
async def v1_search(request: Request, q: str = "", limit: int = LIMIT_DEFAULT):
    return await _run(request, "read:pages", False, search, q, _limit(limit))


@router.post("/api/v1/pages")
async def v1_page_create(request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:pages", True, page_create, b)


@router.get("/api/v1/pages/{nid}")
async def v1_page_get(nid: int, request: Request, format: str = "blocks"):
    return await _run(request, "read:pages", False, page_read, nid, "markdown" if format in ("markdown", "md") else "blocks")


@router.patch("/api/v1/pages/{nid}")
async def v1_page_patch(nid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:pages", True, page_update, nid, b)


@router.delete("/api/v1/pages/{nid}")
async def v1_page_delete(nid: int, request: Request):
    return await _run(request, "write:pages", True, page_delete, nid)


@router.get("/api/v1/pages/{nid}/comments")
async def v1_comments(nid: int, request: Request):
    return await _run(request, "read:comments", False, comments_get, nid)


@router.post("/api/v1/pages/{nid}/comments")
async def v1_comment_add(nid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:comments", True, comment_create, nid, b)


@router.get("/api/v1/databases")
async def v1_databases(request: Request, q: str = "", limit: int = LIMIT_DEFAULT):
    return await _run(request, "read:db", False, databases, q, _limit(limit))


@router.get("/api/v1/databases/{nid}")
async def v1_database(nid: int, request: Request):
    return await _run(request, "read:db", False, database_get, nid)


@router.post("/api/v1/databases/{nid}/query")
async def v1_database_query(nid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "read:db", False, database_query, nid, b)


@router.post("/api/v1/databases/{nid}/rows")
async def v1_row_create(nid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:db", True, row_create, nid, b)


@router.patch("/api/v1/rows/{nid}")
async def v1_row_patch(nid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:db", True, row_update, nid, b)


@router.get("/api/v1/tasks")
async def v1_tasks(request: Request, done: int = 0):
    return await _run(request, "read:tasks", False, tasks_list, bool(done))


@router.post("/api/v1/tasks")
async def v1_task_add(request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:tasks", True, task_create, b)


@router.patch("/api/v1/tasks/{tid}")
async def v1_task_patch(tid: int, request: Request):
    b, err = await _json(request)
    return err or await _run(request, "write:tasks", True, task_change, tid, b)


# --- Dateien -----------------------------------------------------------------------------------------------------
@router.post("/api/v1/files")
async def v1_file_upload(request: Request, page: int = 0):
    """Datei hochladen (multipart, Feld "file"). ?page=<id>: Datei gehoert dem Eigentuemer dieser Seite (Rolle 'edit');
    mit roots ist page Pflicht. Antwort {url, name, size} - die url in Bloecke/Datei-Eigenschaften eintragen."""
    from saga.files import _sniff_image, store_upload
    from saga.notes import FILE_MAX
    tok, err = authed(request, write=True, scope="write:files")
    if err is not None:
        return err
    owner = tok.uid
    if page or tok.roots:
        def chk():
            with _db() as c:
                return _guard(c, tok, page, "edit")[0]
        try:
            owner = await run_in_threadpool(chk)
        except Fail as f:
            return _resp(f.status, {"error": f.msg, "code": f.code})
    form = await request.form()
    up = form.get("file") or form.get("image")
    if up is None or not hasattr(up, "read"):
        return _resp(400, {"error": t("keine Datei"), "code": "bad_request"})
    data = await up.read(FILE_MAX + 1)
    if len(data) > FILE_MAX:
        return _resp(413, {"error": t("Datei zu groß (max. {n} MB)", n=FILE_MAX // 1048576), "code": "too_large"})
    if not data:
        return _resp(400, {"error": t("leere Datei"), "code": "bad_request"})
    name = re.sub(r"[\x00-\x1f/\\\\]", "_", os.path.basename(up.filename or "datei"))[:120] or "datei"
    out, e2 = await run_in_threadpool(store_upload, owner, name, data, _sniff_image(data[:16]))
    if e2 is not None:
        d = json.loads(e2.body)
        return _resp(e2.status_code, {"error": d.get("error") or "error", "code": _CODES.get(e2.status_code, "error")})
    f = out["file"]
    return _resp(201, {"url": f["url"], "name": f["name"], "size": f["size"]})


def _file_ok(tok, fid, page):
    with _db() as c:
        r = c.execute("SELECT name, mime, is_image, uid FROM file WHERE id=?", (fid,)).fetchone()
        if not r:
            raise Fail(404, "not found")
        if r[3] == tok.uid and not tok.roots:
            return r
        if not page:
            raise Fail(404, "not found")
        owner, _ = _guard(c, tok, page, "view")
        ref = f"/files/{fid}"
        if owner != r[3] or not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND (body LIKE ? OR props LIKE ? OR cover=? OR icon=?)",
                                          (int(page), owner, f"%{ref}%", f"%{ref}%", ref, ref)).fetchone():
            raise Fail(404, "not found")
        return r


@router.get("/api/v1/files/{fid}")
async def v1_file_get(fid: str, request: Request, page: int = 0):
    """Datei herunterladen: eigene Dateien direkt; fremde bzw. bei Tokens mit roots nur mit ?page=<Seite, die sie enthaelt>."""
    from saga.notes import _disposition
    tok, err = authed(request, scope="read:files")
    if err is not None:
        return err
    if not re.fullmatch(r"[0-9a-f]{24}", fid):
        return _resp(404, {"error": "not found", "code": "not_found"})
    try:
        r = await run_in_threadpool(_file_ok, tok, fid, page)
    except Fail as f:
        return _resp(f.status, {"error": f.msg, "code": f.code})
    path = os.path.join(FILES_DIR, fid)
    if not os.path.isfile(path):
        return _resp(404, {"error": "not found", "code": "not_found"})
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Content-Disposition": _disposition(r[0]), "Cache-Control": "private, no-store",
                                 "X-Content-Type-Options": "nosniff"})

