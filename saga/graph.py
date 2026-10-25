"""2.5 Graph (wie Obsidian): Seiten als Knoten, Verbindungen als Kanten - Verweise im Text, Unterseiten, Datenbank-
Relationen und gemeinsame Tags (als eigene Tag-Knoten). Nur, was die Person sehen darf: eigene Seiten (ohne Papierkorb)
und fuer sie freigegebene Seiten dieses Servers. Seiten anderer Server erscheinen nicht (ihre Verweise zeigen auf
negative IDs, die hier nicht aufgeloest werden).

GET /api/graph -> {nodes: [[id, titel, symbol, art, eigen, eltern]], edges: [[von, nach, art]], tags: [[name, [ids]]],
truncated: bool}. art der Kante: l = Verweis, c = Unterseite, r = Relation. Kompakt als Listen, weil es tausende sein
koennen. Vorlagen fehlen (sind keine Inhalte)."""
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from db import connect as _db
from saga.core import _api_guard, _bad
from saga.notes import _tags_load
from saga.pageindex import _LINK
from saga.sharing import Access

router = APIRouter()
NODES_MAX = 5000                    # mehr zeichnet ein Browser nicht mehr fluessig; die neuesten zuerst
EDGES_MAX = 30000


def graph(c, uid):
    acc = Access(c, uid)
    rows = c.execute("SELECT id, title, icon, parent_id, kind, tags, updated FROM note WHERE uid=? AND deleted_at IS NULL "
                     "AND archived IS NULL AND is_template=0 ORDER BY updated DESC", (uid,)).fetchall()
    nodes = {r[0]: [r[0], r[1] or "", r[2] or "", r[4] or "page", 1, r[3], r[5], r[6] or 0] for r in rows}
    for o in acc.owners():                         # fuer mich freigegebene Seiten anderer Konten dieses Servers
        for nid, (role, row) in acc.tree(o, meta=True).items():
            if nid in nodes or row[7]:             # row = NOTE_META_COLS (id, title, icon, parent_id, updated, tags, pinned, is_template, kind)
                continue
            nodes[nid] = [nid, row[1] or "", row[2] or "", row[8] or "page", 0, row[3], row[5], row[4] or 0]
    for n in nodes.values():                       # Zeilen = Seiten direkt unter einer Datenbank
        if n[3] == "page" and n[5] in nodes and nodes[n[5]][3] == "database":
            n[3] = "row"
    truncated = len(nodes) > NODES_MAX
    if truncated:
        keep = sorted(nodes.values(), key=lambda n: -n[7])[:NODES_MAX]
        nodes = {n[0]: n for n in keep}
    ids = list(nodes)
    edges = set()
    # Unterseiten: Eltern -> Kind (nur, wenn beide sichtbar sind)
    for n in nodes.values():
        if n[5] in nodes:
            edges.add((n[5], n[0], "c"))
    if ids:
        # Verweise: abgeglichene Seiten aus note_link, gerade geaenderte (note_stale) direkt am Inhalt
        stale = {r[0] for r in c.execute("SELECT note_id FROM note_stale WHERE note_id = ANY(?)", (ids,)).fetchall()}
        for s, d in c.execute("SELECT src, dst FROM note_link WHERE src = ANY(?)", (ids,)).fetchall():
            if s not in stale and d in nodes and s != d:
                edges.add((s, d, "l"))
        if stale:
            for s, body in c.execute("SELECT id, body FROM note WHERE id = ANY(?) AND format='blocks'", (sorted(stale),)).fetchall():
                for d in _LINK.findall(body or ""):
                    if d[0] != "0" and len(d) < 19 and int(d) in nodes and int(d) != s:
                        edges.add((s, int(d), "l"))
        # Relationen: Eigenschaften vom Typ relation der Datenbank (Schema) -> Ziel-IDs in den Zeilen (props)
        dbs = [n[0] for n in nodes.values() if n[3] == "database"]
        rel = {}
        for did, schema in (c.execute("SELECT id, schema FROM note WHERE id = ANY(?)", (dbs,)).fetchall() if dbs else []):
            try:
                ps = (json.loads(schema) if schema else {}).get("props") or []
            except (ValueError, AttributeError):
                ps = []
            pids = [p.get("id") for p in ps if isinstance(p, dict) and p.get("type") == "relation" and p.get("id")]
            if pids:
                rel[did] = pids
        if rel:
            for rid, parent, props in c.execute("SELECT id, parent_id, props FROM note WHERE parent_id = ANY(?) AND id = ANY(?)",
                                                (list(rel), ids)).fetchall():
                try:
                    pv = json.loads(props) if props else {}
                except ValueError:
                    continue
                if not isinstance(pv, dict):
                    continue
                for pid in rel[parent]:
                    v = pv.get(pid)
                    for d in (v if isinstance(v, list) else []):
                        if isinstance(d, int) and not isinstance(d, bool) and d in nodes and d != rid:
                            a, b = sorted((rid, d))        # Relationen sind beidseitig - eine Kante
                            edges.add((a, b, "r"))
    tags = {}
    for n in nodes.values():
        for tg in _tags_load(n[6]):
            tags.setdefault(tg, []).append(n[0])
    el = sorted(edges)[:EDGES_MAX]
    return {"nodes": [n[:6] for n in nodes.values()], "edges": [list(e) for e in el],
            "tags": sorted([[k, v] for k, v in tags.items()], key=lambda x: x[0].lower()),
            "truncated": truncated or len(edges) > EDGES_MAX}


@router.get("/api/graph")
def api_graph(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        out = graph(c, uid)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})
