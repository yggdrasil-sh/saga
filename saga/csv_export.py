"""Datenbank als CSV exportieren (2.5, Paket 2 E1).

GET /api/notes/{nid}/export.csv?view=<vid>&all=0|1&sep=,|;&safe=1|0&tz=...&today=...&q=...&filters=...&sorts=...
- all=1: alle Eintraege (Server-Reihenfolge), alle Spalten in Schema-Reihenfolge.
- sonst: Ansicht view (fehlt sie: die erste) mit ihren Filtern/Sortierung/Gruppierung - gerechnet von saga.dbquery.run
  genau wie /rows und das Web (keine zweite Filterlogik); Spalten wie sichtbar (view.order, ohne view.hidden).
  filters/sorts/q/group (wie /rows) ueberschreiben die Ansicht (z. B. die Suche im Web); cols=id,id,... legt Spalten
  und Reihenfolge fest (eingebettete Datenbank mit eigenen Ansichten im Block, die der Server nicht kennt).
- sep=";" = "Excel (Deutsch)": Semikolon und Dezimalkomma. Standard: Komma, Dezimalpunkt.
- UTF-8 mit BOM (Excel erkennt die Kodierung), Zeilenende CRLF (RFC 4180).
- safe=1 (Standard): Formel-Injektion verhindern - Text-Zellen, die mit = + - @ Tab oder CR beginnen, bekommen ein '
  davor (Zahlen-, Datums- und Haekchen-Spalten erzeugt der Server selbst, die bleiben unveraendert).
Rechte wie /rows (ab 'Lesen'; fremd: ohne archivierte Zeilen, Relationen nur mit Titeln, die man sehen darf). Seiten
anderer Saga-Instanzen ueber saga.fed_pages (Faehigkeit csv). Werte ueber saga.proptext (maschinenlesbar)."""
import csv
import io

from fastapi import APIRouter, Request
from fastapi.responses import Response

from db import connect as _db
from saga import dbcalc, dbquery, proptext
from saga.app_api import _db_query
from saga.core import _api_guard, _bad, _json_load, t
from saga.notes import _disposition
from saga.page_tools import _safe_name
from saga.sharing import Access, access

router = APIRouter()

CSV_ROWS_MAX = 100_000
_DANGER = ("=", "+", "-", "@", "\t", "\r")
_PLAIN_TYPES = {"number", "calc", "date", "created", "edited", "checkbox"}     # Werte erzeugt der Server selbst
_FX_TYPES = ("formula", "rollup")                  # Zahl/Haekchen ungeschuetzt, Text/Liste mit Schutz vor Formel-Injektion


def _guard_cell(s):
    return "'" + s if s and s.startswith(_DANGER) else s


def build(uid, nid, params):
    """-> (dateiname, bytes) oder Fehlerantwort (JSONResponse). uid: Konto oder Prinzipal einer anderen Instanz."""
    all_rows = str(params.get("all") or "") == "1"
    sep = ";" if params.get("sep") == ";" else ","
    safe = str(params.get("safe") or "1") != "0"
    try:
        q = dbquery.parse_args({k: v for k, v in params.items() if k in ("view", "filters", "sorts", "q", "group", "tz", "today")}) or {}
    except ValueError as e:
        return _bad(f"bad parameter: {e}")
    if all_rows:
        q = {k: v for k, v in q.items() if k in ("tz", "today")}
    else:
        q.setdefault("view", "")                               # ohne Angabe: erste Ansicht (dbquery.view_of)
    with _db() as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        db = c.execute("SELECT schema, title FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL",
                       (nid, owner)).fetchone()
        if not db:
            return _bad("not found", 404)
        shared = role != "owner"
        sql = ("SELECT id, title, icon, props, updated, created, cover, format FROM note WHERE uid=? AND parent_id=? "
               "AND deleted_at IS NULL AND is_template=0" + (" AND archived IS NULL" if shared else ""))
        try:
            rows, parsed, extra = _db_query(c, owner, nid, shared, sql, db[0], q, uid)
        except dbquery.JsError:
            return _bad(t("Filter oder Sortierung lässt sich nicht auswerten."))
        rows = rows[:CSV_ROWS_MAX]
        schema = _json_load(db[0], {"props": []})
        if dbcalc.has_calc(schema) and not (extra and "_fx" in extra):    # 2.5: Formeln/Rollups (mit Filter usw.: schon gerechnet)
            data = []
            for r in rows:
                pv = parsed.get(r[0]) if r[0] in parsed else _json_load(r[3], {})
                parsed[r[0]] = pv if isinstance(pv, dict) else {}
                data.append({"id": r[0], "title": r[1] or "", "props": parsed[r[0]], "created": r[5], "updated": r[4]})
            dbcalc.fill(c, owner, schema, data, viewer=uid, tz=q.get("tz"))
        props = [p for p in schema.get("props") or [] if isinstance(p, dict)]
        cols = [x for x in str(params.get("cols") or "").split(",") if x][:60] if not all_rows else []
        if cols:
            byid = {p.get("id"): p for p in props}
            props = [byid[x] for x in dict.fromkeys(cols) if x in byid]
        elif not all_rows:
            view = dbquery.view_of(schema, q.get("view")) or {}
            order = view.get("order") if isinstance(view.get("order"), list) else []
            hidden = set(view.get("hidden") or []) if isinstance(view.get("hidden"), list) else set()
            pos = {pid: i for i, pid in enumerate(order) if isinstance(pid, str)}
            props = sorted(props, key=lambda p: (pos.get(p.get("id"), 10 ** 6), props.index(p)))
            props = [p for p in props if p.get("id") not in hidden]
        # Titel verknuepfter Eintraege: nur, was der Betrachter kennt (wie /rows)
        rel = {p["id"] for p in props if p.get("type") == "relation"}
        want = set()
        for r in rows:
            pv = parsed.get(r[0]) if r[0] in parsed else _json_load(r[3], {})
            parsed[r[0]] = pv if isinstance(pv, dict) else {}
            for k in rel:
                v = parsed[r[0]].get(k)
                if isinstance(v, list):
                    want.update(x for x in v if isinstance(x, int) and not isinstance(x, bool))
        titles = {}
        if want:
            ids = list(want)
            if not shared:
                titles = dict(c.execute("SELECT id, title FROM note WHERE id = ANY(?) AND uid=? AND deleted_at IS NULL "
                                        "AND archived IS NULL", (ids, owner)).fetchall())
            else:
                vis = acc.tree(owner)
                ok = [i for i in ids if i in vis]
                titles = dict(c.execute("SELECT id, title FROM note WHERE id = ANY(?)", (ok,)).fetchall()) if ok else {}
    ctx = proptext.Ctx("csv", lambda x: (titles.get(x) or "") if x in titles else None, "," if sep == ";" else ".",
                       q.get("tz"))
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    g = _guard_cell if safe else (lambda s: s)
    w.writerow([g(schema.get("title_name") or t("Name"))] + [g(str(p.get("name") or "")) for p in props])
    for r in rows:
        pv = parsed.get(r[0]) or {}
        line = [g(r[1] or "")]
        for p in props:
            v = proptext.value_of(p, pv, r[5], r[4])
            s = proptext.prop_text(p, v, ctx).replace("\x00", "")
            plain = p.get("type") in _PLAIN_TYPES or (p.get("type") in _FX_TYPES and isinstance(v, (int, float)))
            line.append(s if plain else g(s))
        w.writerow(line)
    return _safe_name(db[1] or t("Datenbank")) + ".csv", buf.getvalue().encode("utf-8")


def response(res):
    if not isinstance(res, tuple):
        return res
    name, data = res
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": _disposition(name), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/notes/{nid}/export.csv")
def api_export_csv(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return response(build(uid, nid, dict(request.query_params)))
