"""Importe (2.5, Paket 2): HTTP-Schnittstelle. Unterbau in saga/import_common.py, Formate in saga/import_csv.py (CSV ->
Datenbank) und saga/import_md.py (Markdown/Obsidian). Ablauf fuer Web und Apps (docs/APP-API.md, "Import"):

  POST /api/import/upload?name=<datei>[&token=<t>][&replace=1]   Rohdaten (Datenstrom) -> {token, files, kind}. Mehrere
                                                      Dateien (Ordner ohne ZIP): weitere Teile mit demselben token.
                                                      Hoechstens EIN wartender Upload je Konto: ein neuer (ohne token)
                                                      -> 409, solange ein anderer wartet; replace=1 verwirft den alten.
  POST /api/import/{token}/analyze {kind?, path?, encoding?, sep?, header?}  -> Analyse (nichts wird angelegt)
  POST /api/import/{token}/run {kind, parent_id?, ...}  -> 202 {run_id} (Hintergrund; 409 wenn schon einer laeuft)
  GET  /api/import/runs[/{id}]                          -> Zustand, Fortschritt, Bericht
  POST /api/import/runs/{id}/cancel                     -> Abbruch (Angelegtes wandert in den Papierkorb)
  DELETE /api/import/{token}                            -> Upload verwerfen

Nur eigene Uploads/Laeufe; Ziel nur eigene Seiten/Datenbanken. Upload-Ablage auf dem Daten-Volume, nach dem Lauf bzw.
nach 30 Minuten ohne Lauf geloescht."""
import json
import os
import re
import time
import zipfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import connect as _db
from saga import i18n
from saga import import_common as ic
from saga import import_csv
from saga.core import TZ, _api_guard, _bad, _body, _json_load, t
from saga.notes import _clean_schema, _schema_stamp, tree_changed

router = APIRouter()
MD_EXT = (".md", ".markdown", ".txt")
CSV_EXT = (".csv", ".tsv")


def _uid(request):
    return _api_guard(request)


def kind_of(paths):
    """Art eines Uploads aus den Dateinamen: csv | md | notion | None."""
    low = [p.lower() for p in paths]
    if any(re.search(r" [0-9a-f]{32}\.(md|csv)$", p) for p in low):
        return "notion"
    if any(p.endswith(MD_EXT[:2]) for p in low):
        return "md"
    if any(p.endswith(CSV_EXT) for p in low):
        return "csv"
    return None


def _files_out(u, src=None):
    if src is not None:
        return [{"path": p, "size": e.size} for p, e in list(src.entries.items())[:5000]]
    return [{"path": f["name"], "size": f["size"]} for f in u["files"]][:5000]


@router.post("/api/import/upload")
async def api_upload(request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    q = request.query_params
    name = ic.safe_part_name(q.get("name") or "")
    if not name:
        return _bad(t("Dateiname fehlt oder ist unzulässig."))
    tok = q.get("token")
    if tok:
        u = ic.get_upload(uid, tok)
        if not u:
            return _bad("not found", 404)
        if len(u["files"]) >= ic.PARTS_MAX:
            return _bad(t("Zu viele Dateien (höchstens {n}).", n=ic.PARTS_MAX), 413)
    else:
        ic.cleanup()
        old = ic.waiting_uploads(uid)                # hoechstens ein wartender Upload je Konto (sonst Platte voll)
        if old and q.get("replace") != "1":
            return _bad(t("Es wartet schon ein Import-Upload – erst abschließen oder verwerfen."), 409)
        for k in old:
            ic.drop_upload(k)
        tok = ic.new_upload(uid)
        u = ic.get_upload(uid, tok)
    if u["busy"]:
        return _bad(ic.reason_text("busy"), 409)
    limit = ic.IMPORT_MAX - u["size"]
    if int(request.headers.get("content-length") or 0) > limit:
        return _bad(t("Upload zu groß (höchstens {n} MB).", n=ic.IMPORT_MAX // ic.MB), 413)
    path = os.path.join(u["dir"], f"{len(u['files']):05d}")
    n = 0
    try:
        with open(path, "wb") as f:
            async for chunk in request.stream():
                n += len(chunk)
                if n > limit:
                    raise ic.ImportFail("too_big")
                f.write(chunk)
    except ic.ImportFail:
        os.remove(path)
        return _bad(t("Upload zu groß (höchstens {n} MB).", n=ic.IMPORT_MAX // ic.MB), 413)
    u["files"].append({"name": name, "path": path, "size": n})
    u["size"] += n
    names = [f["name"] for f in u["files"]]
    if len(u["files"]) == 1 and name.lower().endswith(".zip"):
        if ic.zip_too_big_cd(path):
            ic.drop_upload(tok)
            return _bad(ic.reason_text("zip_entries"))
        try:
            with zipfile.ZipFile(path) as z:
                names = [i.filename for i in z.infolist()[:ic.ENTRIES_MAX]]
        except (zipfile.BadZipFile, OSError):
            ic.drop_upload(tok)
            return _bad(ic.reason_text("not_zip"))
    return JSONResponse({"token": tok, "kind": kind_of(names), "files": len(u["files"]), "size": u["size"]},
                        headers={"Cache-Control": "no-store"})


@router.delete("/api/import/{tok}")
def api_drop(tok: str, request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    u = ic.get_upload(uid, tok)
    if not u:
        return _bad("not found", 404)
    if u["busy"]:
        return _bad(ic.reason_text("busy"), 409)
    ic.drop_upload(tok)
    return JSONResponse({"ok": True})


def _csv_path(src, want):
    paths = [p for p in src.paths() if p.lower().endswith(CSV_EXT + (".txt",))]
    if isinstance(want, str) and want in src.entries:
        return want
    return paths[0] if paths else (src.paths()[0] if len(src.paths()) == 1 else None)


@router.post("/api/import/{tok}/analyze")
async def api_analyze(tok: str, request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(analyze, uid, tok, b)


def analyze(uid, tok, b):
    u = ic.get_upload(uid, tok)
    if not u:
        return _bad("not found", 404)
    rep = ic.Report()
    try:
        src = ic.Source(u, rep)
    except ic.ImportFail as ex:
        return _bad(ic.reason_text(ex.code, ex.p))
    try:
        kind = b.get("kind") if b.get("kind") in ("csv", "md") else kind_of(src.paths())
        out = {"kind": kind, "files": len(src.entries), "bytes": sum(e.size for e in src.entries.values())}
        if kind == "csv":
            path = _csv_path(src, b.get("path"))
            if not path:
                return _bad(ic.reason_text("nothing"))
            out.update(import_csv.analyze(src, path, b), path=path,
                       csv_files=[p for p in src.paths() if p.lower().endswith(CSV_EXT + (".txt",))][:200])
        elif kind == "md":
            from saga import import_md
            out.update(import_md.analyze(src, rep))
        elif kind == "notion":
            out["error"] = t("Notion-Import kommt in einer späteren Version.")
        else:
            out["error"] = ic.reason_text("unsupported")
        out["report"] = ic.report_out(rep.to_json())
        return JSONResponse(out, headers={"Cache-Control": "no-store"})
    except ic.ImportFail as ex:
        return _bad(ic.reason_text(ex.code, ex.p))
    except (zipfile.BadZipFile, OSError, EOFError, ValueError, UnicodeError):
        return _bad(ic.reason_text("bad_file"))
    finally:
        src.close()


@router.post("/api/import/{tok}/run")
async def api_run(tok: str, request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(start, uid, tok, b)


def _own(c, uid, nid, kind=None):
    r = c.execute("SELECT kind, schema, title FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()
    if not r or (kind and r[0] != kind):
        return None
    return r


def start(uid, tok, b):
    u = ic.get_upload(uid, tok)
    if not u:
        return _bad("not found", 404)
    kind = b.get("kind")
    parent = b.get("parent_id")
    if parent is not None:
        if not (isinstance(parent, int) and not isinstance(parent, bool) and 0 < parent < 2 ** 63):
            return _bad(t("ungültige Angabe"))
        with _db() as c:
            r = _own(c, uid, parent)
        if not r or r[0] == "database":
            return _bad(ic.reason_text("no_target"), 404)
    target = b.get("target_db")
    if target is not None:
        if not (isinstance(target, int) and not isinstance(target, bool) and 0 < target < 2 ** 63):
            return _bad(t("ungültige Angabe"))
        with _db() as c:
            if not _own(c, uid, target, "database"):
                return _bad(ic.reason_text("no_target"), 404)
    if kind == "csv":
        cols = b.get("columns")
        if not isinstance(cols, list) or len(cols) > 500:
            return _bad(t("ungültige Angabe"))
        if target is None:
            active = [c for c in cols if isinstance(c, dict) and c.get("type") not in ("skip", None) and c.get("index") != b.get("title_col")]
            if len(active) > import_csv.COLS_MAX:
                return _bad(t("Höchstens {n} Spalten – bitte Spalten abwählen.", n=import_csv.COLS_MAX))
        work = _csv_work(tok, b, parent, target)
        name = str(b.get("title") or "")[:200]
    elif kind == "md":
        from saga import import_md
        work = import_md.work(tok, b, parent)
        name = str(b.get("title") or "")[:200]
    else:
        return _bad(ic.reason_text("unsupported"))
    rid = ic.start_run(uid, kind, name or kind, _wrap(u, work), tok)
    if rid is None:
        return _bad(ic.reason_text("busy"), 409)
    return JSONResponse({"run_id": rid}, status_code=202)


def _wrap(u, work):
    """work(run, src) mit geoeffneter Quelle; Bericht als Seite neben dem Ziel."""
    def go(run):
        src = ic.Source(u, run.report)
        try:
            root, parent = work(run, src)
        finally:
            src.close()
        if run.report.items:
            _report_page(run, parent)
        return root
    return go


def _csv_work(tok, b, parent, target):
    def work(run, src):
        path = _csv_path(src, b.get("path"))
        if not path:
            raise ic.ImportFail("nothing")
        run.step("analyze", 0, 0, force=True)
        if target is None:
            plan = import_csv.plan_new(src, path, b, run.report)
            return ic.apply_plan(run, plan, parent), parent
        return _csv_append(run, src, path, b, target), _parent_of(run.uid, target)
    return work


def _parent_of(uid, nid):
    with _db() as c:
        r = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, uid)).fetchone()
    return r[0] if r else None


def _csv_append(run, src, path, b, target):
    uid = run.uid
    with _db() as c:
        r = _own(c, uid, target, "database")
    if not r:
        raise ic.ImportFail("no_target")
    old = _json_load(r[1], {"props": []})
    schema = json.loads(json.dumps(old))

    def titles(pid):
        p = next((x for x in schema["props"] if x["id"] == pid), None)
        if not p or not p.get("target"):
            return {}
        with _db() as c:
            got = c.execute("SELECT id, title FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                            (uid, p["target"])).fetchall()
        out = {}
        for i, tl in got:                     # gleiche Titel: erste Uebereinstimmung
            out.setdefault((tl or "").strip().lower(), i)
        return out
    schema, rows, n = import_csv.plan_append(src, path, b, run.report, schema, titles)
    ic.check_limits(uid, 0, n)
    sc = _schema_stamp(old, _clean_schema(schema), uid)
    with _db() as c, c:
        c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(sc, ensure_ascii=False), target, uid))
    ic._insert_rows(run, target, sc, {"rows": rows}, {}, 0, n)
    return target


def _report_page(run, parent):
    """Seite "Importbericht" neben dem Ziel (gleiche Elternseite; nie in einer Datenbank)."""
    from html import escape
    rep = ic.report_out(run.report.to_json())
    cnt = rep["counts"]
    blocks = [{"id": "ir0", "type": "paragraph", "data": {"text": escape(t(
        "Angelegt: {pages} Seiten, {dbs} Datenbanken, {rows} Einträge, {files} Dateien. Nicht übernommen: {skipped}.",
        pages=cnt.get("pages", 0), dbs=cnt.get("databases", 0), rows=cnt.get("rows", 0), files=cnt.get("files", 0),
        skipped=cnt.get("skipped", 0)), quote=False)}}]
    for i, g in enumerate(rep["groups"]):
        blocks.append({"id": f"irh{i}", "type": "header", "data": {"level": 3, "text": escape(g["text"], quote=False)}})
        blocks.append({"id": f"irl{i}", "type": "list", "data": {"style": "unordered", "items": [
            {"content": escape(x["path"] or "–", quote=False), "items": []} for x in g["items"][:500]]}})
    if rep["more"]:
        blocks.append({"id": "irm", "type": "paragraph", "data": {"text": escape(t("… und {n} weitere.", n=rep["more"]), quote=False)}})
    with _db() as c, c:
        if parent is not None and not _own(c, run.uid, parent):
            parent = None
        if parent is not None and _own(c, run.uid, parent, "database"):
            parent = None
        nid = ic.new_ids(c, 1)[0]
        body = ic.clean_body(c, run.uid, blocks, ic.Report(), "")
        ic.insert_page(c, run.uid, nid, t("Importbericht {date}", date=i18n.fmt_date(time.time(), TZ, with_time=True)), parent,
                       time.time(), icon="📋", body=body)
        run.created.append(nid)
        tree_changed(c, run.uid, [nid], "created")
    run.report.page = nid


@router.get("/api/import/runs")
def api_runs(request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        rows = c.execute(f"SELECT {ic.RUN_COLS} FROM import_run WHERE uid=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
    return JSONResponse({"runs": [ic.run_out(r) for r in rows]}, headers={"Cache-Control": "no-store"})


@router.get("/api/import/runs/{rid}")
def api_run_get(rid: int, request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = c.execute(f"SELECT {ic.RUN_COLS} FROM import_run WHERE id=? AND uid=?", (rid, uid)).fetchone() if 0 < rid < 2 ** 63 else None
    if not r:
        return _bad("not found", 404)
    return JSONResponse(ic.run_out(r), headers={"Cache-Control": "no-store"})


@router.post("/api/import/runs/{rid}/cancel")
def api_run_cancel(rid: int, request: Request):
    uid = _uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not (0 < rid < 2 ** 63) or not ic.cancel(uid, rid):
        return _bad("not found", 404)
    return JSONResponse({"ok": True}, status_code=202)
