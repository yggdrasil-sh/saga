"""Vorlagen und Datenbanken (Ansichten, Spalten, Zeilen, Berechnungen)."""
import json
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from db import connect as _db
from fastapi import APIRouter
from saga.core import _api_guard, _bad, _body, _json_load, origin_of
from saga.sharing import access
from saga.notes import NOTE_TITLE_MAX, note_limit_error, _FILE_URL, _check_parent, _iter_blocks, tree_changed

router = APIRouter()


# --- Vorlagen ---------------------------------------------------------------------
def _map_props(c, owner, src_parent, dst_parent, props_json):
    """Eigenschaften einer Vorlage in eine ANDERE Datenbank uebertragen: nach Name + Typ der Spalte,
    Auswahl-Optionen nach Namen. Was es im Ziel nicht gibt, faellt weg (statt falscher IDs)."""
    def schema(nid):
        if not nid:
            return []
        row = c.execute("SELECT schema, kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
        return (_json_load(row[0], {}) or {}).get("props", []) if row and row[1] == "database" else []
    src, dst = schema(src_parent), schema(dst_parent)
    vals = _json_load(props_json, {}) or {}
    if not dst or not isinstance(vals, dict):
        return None
    out = {}
    by_id = {p.get("id"): p for p in src}
    for pid, v in vals.items():
        sp = by_id.get(pid)
        if not sp:
            continue
        dp = next((p for p in dst if p.get("name", "").strip().lower() == sp.get("name", "").strip().lower() and p.get("type") == sp.get("type")), None)
        if not dp or dp.get("type") in ("relation", "created", "edited"):
            continue
        if dp.get("type") in ("select", "multi_select"):
            names = {o.get("id"): o.get("name", "").strip().lower() for o in sp.get("options", [])}
            dst_ids = {o.get("name", "").strip().lower(): o.get("id") for o in dp.get("options", [])}
            ids = v if isinstance(v, list) else [v]
            mapped = [dst_ids[names[i]] for i in ids if i in names and names[i] in dst_ids]
            if not mapped:
                continue
            v = mapped if dp["type"] == "multi_select" else mapped[0]
        out[dp["id"]] = v
    return json.dumps(out, ensure_ascii=False) if out else None


@router.post("/api/notes/{nid}/duplicate")
async def api_note_duplicate(nid: int, request: Request):
    """Kopie einer eigenen Seite (Vorlage -> neue Seite). Unterseiten werden nicht kopiert."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid                              # Duplizieren: v1 nur der Eigentuemer (Vorlagen sieht ohnehin nur er)
    b = await _body(request) or {}
    now = time.time()
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
        r = c.execute("SELECT title, icon, tags, body, format, kind, schema, props, cover, cover_pos, wide, parent_id "
                      "FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        lim = note_limit_error(c, owner, parent)
        if lim:
            return _bad(lim, 409)
        title = str(b.get("title", r[0] or "")).strip()[:NOTE_TITLE_MAX]
        props = r[7]
        if parent != r[11]:                     # andere Datenbank (z. B. Vorlagen-Datenbank) -> Eigenschaften nach Name/Typ zuordnen
            props = _map_props(c, owner, r[11], parent, r[7])
        # Alles kopieren, was die Seite ausmacht: auch Titelbild (+ Position) und volle Breite
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, cover, cover_pos, wide, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, r[3], r[4], r[1], parent, now, r[2], r[5] or "page", r[6], props, r[8], r[9], r[10], now, now))
        tree_changed(c, owner, [cur.lastrowid], "created", origin_of(request))
        return JSONResponse({"id": cur.lastrowid, "updated": now})


def _first_image(body, fmt):
    """Erstes Bild im Seiteninhalt (Galerie-Vorschau, wenn die Zeile kein Titelbild hat)."""
    if fmt != "blocks":
        return ""
    doc = _json_load(body, {})
    try:
        blocks = list(_iter_blocks(doc.get("blocks", []) if isinstance(doc, dict) else []))
    except ValueError:
        return ""
    for b in blocks:
        if b.get("type") == "image":
            u = str(((b.get("data") or {}).get("file") or {}).get("url", ""))
            if _FILE_URL.fullmatch(u):
                return u
    return ""
