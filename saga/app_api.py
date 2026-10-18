"""App-Schluessel, Teilen-Erweiterung und Widgets (eng begrenzte Rechte)."""
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from db import connect as _db
from fastapi import APIRouter
from saga.calendars import _cal_collect
from saga.core import APP_CAPTURE_MAX, APP_TOKEN_MAX, TZ, _api_guard, _bad, _body, _json_load, _tok_db, _tok_hash, t, t_account, user_info
from saga.databases import _first_image
from saga.files import _thumb, _thumb_mime, _thumb_w
from saga.notes import FILES_DIR, _FILE_URL, _clean_props, tree_changed
from saga.pages import clean_blocks
from saga.sharing import Access, access
from saga.tasks import TASK_MAX_LEN, _client_today, _due, _task_done

router = APIRouter()


def _app_uid(request):
    """NUR Bearer-Schluessel (keine Sitzung): liefert den Benutzer oder None."""
    hdr = request.headers.get("authorization", "")
    if not hdr.startswith("Bearer hk_") or len(hdr) > 120:
        return None
    with _db() as c, c:
        _tok_db(c)
        r = c.execute("SELECT id, uid, last_used FROM app_token WHERE hash=?", (_tok_hash(hdr[7:].strip()),)).fetchone()
        if not r:
            return None
        if not r[2] or time.time() - r[2] > 300:          # "zuletzt benutzt" grob mitfuehren
            c.execute("UPDATE app_token SET last_used=? WHERE id=?", (time.time(), r[0]))
    return r[1] if user_info(r[1]) is not None else None


@router.get("/api/settings/tokens")
def api_tok_list(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _tok_db(c)
        rows = c.execute("SELECT id, name, prefix, created, last_used FROM app_token WHERE uid=? ORDER BY id", (uid,)).fetchall()
    return JSONResponse([{"id": r[0], "name": r[1], "prefix": r[2], "created": r[3], "last_used": r[4]} for r in rows])


@router.post("/api/settings/tokens")
async def api_tok_add(request: Request):
    """Neuen Schluessel erzeugen - der Klartext wird NUR in dieser Antwort gezeigt."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:60] or "iPhone"
    tok = "hk_" + secrets.token_urlsafe(32)
    with _db() as c, c:
        _tok_db(c)
        if c.execute("SELECT COUNT(*) FROM app_token WHERE uid=?", (uid,)).fetchone()[0] >= APP_TOKEN_MAX:
            return _bad(t("Höchstens {n} Schlüssel", n=APP_TOKEN_MAX))
        cur = c.execute("INSERT INTO app_token (uid, name, hash, prefix, created) VALUES (?,?,?,?,?)",
                        (uid, name, _tok_hash(tok), tok[:9], time.time()))
    return JSONResponse({"id": cur.lastrowid, "name": name, "token": tok, "prefix": tok[:9]})


@router.delete("/api/settings/tokens/{tid}")
def api_tok_delete(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _tok_db(c)
        n = c.execute("DELETE FROM app_token WHERE id=? AND uid=?", (tid, uid)).rowcount
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


def _inbox(c, uid):
    """Seite '📥 Eingang' (oberste Ebene) - wird bei Bedarf angelegt bzw. neu angelegt, wenn geloescht."""
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='inbox'", (uid,)).fetchone()
    if r and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (int(r[0]), uid)).fetchone():
        return int(r[0])
    now = time.time()
    nid = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                    (uid, t_account(uid, "Eingang"), json.dumps({"blocks": []}), "blocks", "📥", None, now, now, now)).lastrowid
    c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'inbox', ?)", (uid, str(nid)))
    tree_changed(c, uid, [nid], "created")
    return nid


@router.post("/api/app/capture")
async def api_app_capture(request: Request):
    """Aus der Teilen-Erweiterung: als Seite im Eingang oder als Aufgabe ablegen."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    kind = b.get("kind") if b.get("kind") in ("page", "task") else "page"
    title = re.sub(r"\s+", " ", str(b.get("title") or "")).strip()[:200]
    text = str(b.get("text") or "").strip()[:APP_CAPTURE_MAX]
    url = str(b.get("url") or "").strip()[:2000]
    if url and not re.fullmatch(r"https?://[^\s<>\"]+", url):
        return _bad(t("ungültiger Link"))
    if not (title or text or url):
        return _bad(t("nichts zum Speichern"))
    if kind == "task":
        txt = (title or text.split("\n")[0] or url)[:TASK_MAX_LEN - (len(url) + 1 if url and url not in (title or text) else 0)]
        if url and url not in txt:
            txt = (txt + " " + url).strip()
        try:
            due = _due(b.get("due"))                  # Siri/Kurzbefehle: optional faellig am
        except (ValueError, TypeError):
            return _bad(t("ungültiges Datum"))
        with _db() as c, c:
            cur = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                            (uid, txt[:TASK_MAX_LEN], due, 2, None, None, time.time()))
        return JSONResponse({"ok": True, "kind": "task", "id": cur.lastrowid})
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    blocks = []
    if url:
        blocks.append({"type": "paragraph", "data": {"text": f'🔗 <a href="{esc(url)}">{esc(url)}</a>'}})
    for para in [p for p in re.split(r"\n\s*\n", text) if p.strip()][:200]:
        blocks.append({"type": "paragraph", "data": {"text": esc(para.strip()).replace("\n", "<br>")}})
    body, _ = clean_blocks(json.dumps({"blocks": blocks}))         # derselbe Bereiniger wie im Editor
    now = time.time()
    with _db() as c, c:
        _tok_db(c)
        parent = _inbox(c, uid)
        nid = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, created, updated) VALUES (?,?,?,?,?,?,?,?,?)",
                        (uid, title or (url or text)[:80] or t("Geteilt"), body, "blocks", "🔗" if url else "📝", parent, now, now, now)).lastrowid
        tree_changed(c, uid, [nid], "created")
    return JSONResponse({"ok": True, "kind": "page", "id": nid, "url": f"/notes#{nid}"})


@router.get("/api/app/today")
def api_app_today(request: Request):
    """Fuer das Widget: Termine heute/morgen + offene Aufgaben (faellig zuerst)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    today = datetime.now(TZ).date()
    try:
        events, errors = _cal_collect(uid, today, today + timedelta(days=2))
    except Exception:
        events, errors = [], [{"error": t("Kalender nicht lesbar")}]
    with _db() as c:
        rows = c.execute("SELECT id, text, due, prio FROM task WHERE uid=? AND done=0 "
                         "ORDER BY (due IS NULL), due, prio, created LIMIT 20", (uid,)).fetchall()
    tasks = [{"id": r[0], "text": r[1], "due": r[2], "prio": r[3], "overdue": bool(r[2] and r[2] < today.isoformat())} for r in rows]
    slim = [{k: e[k] for k in ("summary", "start", "end", "allday", "location", "color", "calendar")} for e in events]
    return JSONResponse({"date": today.isoformat(), "events": slim, "tasks": tasks, "errors": [x.get("error") for x in errors]},
                        headers={"Cache-Control": "no-store"})


# --- Widgets: eng begrenzt (nur eigene Daten; lesen = Titel/Zeilen, schreiben = Aufgabe abhaken / Haekchen-Feld) ---
@router.get("/api/app/pages")
def api_app_pages(request: Request):
    """Fuer die Widget-Auswahl: Seiten + Datenbanken (mit ihren Ansichten) - nur Titel, Symbol, Eltern."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        rows = c.execute("SELECT id, title, icon, kind, parent_id, schema, cover, cover_pos FROM note WHERE uid=? AND deleted_at IS NULL "
                         "AND is_template=0 AND archived IS NULL ORDER BY sort NULLS FIRST, created LIMIT 5000", (uid,)).fetchall()
    out = []
    for r in rows:
        o = {"id": r[0], "title": r[1] or "", "icon": r[2] or "", "kind": r[3] or "page", "parent_id": r[4],
             "cover": bool(r[6]), "cover_pos": r[7] if r[7] is not None else 50}
        if r[3] == "database":
            o["views"] = [{"id": v.get("id"), "name": v.get("name"), "type": v.get("type")}
                          for v in _json_load(r[5], {}).get("views", []) if isinstance(v, dict)]
        out.append(o)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.get("/api/app/page-image/{nid}/{which}")
def api_app_page_image(nid: int, which: str, request: Request):
    """Widget "Seite": NUR Titelbild bzw. Symbol-Bild einer eigenen Seite (kein Zugriff auf andere Dateien).
    Ohne Titelbild: erstes Bild im Inhalt (wie die Galerie)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    if which not in ("cover", "icon"):
        return _bad("not found", 404)
    with _db() as c:
        r = c.execute("SELECT cover, icon, body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()
        url = "" if not r else (r[1] if which == "icon" else (r[0] or _first_image(r[2], r[3])))
        m = _FILE_URL.fullmatch(str(url or ""))
        f = m and c.execute("SELECT mime FROM file WHERE id=? AND uid=? AND is_image=1", (m.group(1), uid)).fetchone()
    path = os.path.join(FILES_DIR, m.group(1)) if f else ""
    if not f or not os.path.isfile(path):
        return _bad("not found", 404)
    w = _thumb_w(request)
    tp = _thumb(m.group(1), path, f[0], w) if w else None
    return FileResponse(tp or path, media_type=_thumb_mime(tp) if tp else f[0],
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/api/app/rows/{nid}")
def api_app_rows(nid: int, request: Request):
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    d = _db_rows(uid, nid)
    return JSONResponse(d, headers={"Cache-Control": "no-store"}) if d else _bad("not found", 404)


@router.post("/api/app/tasks/{tid}/done")
async def api_app_task_done(tid: int, request: Request):
    """Widget: Aufgabe abhaken (done=true) oder wieder oeffnen - dieselbe Logik wie im Web (Folgeaufgaben)."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    with _db() as c, c:
        row = c.execute("SELECT text, done, due, prio, repeat, rep_anchor, next_id FROM task WHERE id=? AND uid=?", (tid, uid)).fetchone()
        if not row:
            return _bad("not found", 404)
        text, done, due, prio, rep, anchor, next_id = row
        next_id, nd = _task_done(c, uid, tid, bool(b.get("done", True)), done, text, due, prio, rep, anchor, next_id, _client_today(b))
        c.execute("UPDATE task SET next_id=? WHERE id=? AND uid=?", (next_id, tid, uid))
    return JSONResponse({"ok": True, "next_due": nd})


@router.post("/api/app/rows/{nid}/check")
async def api_app_row_check(nid: int, request: Request):
    """Widget: NUR ein Haekchen-Feld einer eigenen Datenbank-Zeile setzen."""
    uid = _app_uid(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    pid = str(b.get("pid") or "")
    with _db() as c, c:
        ps = c.execute("SELECT d.schema, n.props FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                       "WHERE n.id=? AND n.uid=? AND d.kind='database' AND n.deleted_at IS NULL", (nid, uid)).fetchone()
        if not ps:
            return _bad("not found", 404)
        schema = _json_load(ps[0], {"props": []})
        if not any(p.get("id") == pid and p.get("type") == "checkbox" for p in schema.get("props", [])):
            return _bad(t("kein Häkchen-Feld"))
        merged = _clean_props(schema, {pid: bool(b.get("value"))}, _json_load(ps[1], {}))
        c.execute("UPDATE note SET props=?, updated=? WHERE id=? AND uid=?", (json.dumps(merged, ensure_ascii=False), time.time(), nid, uid))
    return JSONResponse({"ok": True})


@router.get("/api/notes/{nid}/rows")
def api_db_rows(nid: int, request: Request):
    """Zeilen einer Datenbank (aktive Unterseiten) mit ihren Werten - ab Rolle 'view'; Antwort mit 'role'."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        vis = None if role == "owner" else acc.tree(owner)      # fremd: ohne archivierte Zeilen und ohne Vorlagen
    d = _db_rows(owner, nid, vis)
    if d:
        d["role"] = role
    return JSONResponse(d, headers={"Cache-Control": "no-store"}) if d else _bad("not found", 404)


def _db_rows(owner, nid, visible=None):
    """Schema + aktive Zeilen einer Datenbank (None, wenn nicht vorhanden / nicht von owner).
    visible: Menge sichtbarer Seiten fuer Nicht-Eigentuemer (sharing.Access.tree) - dann ohne Vorlagen."""
    with _db() as c:
        db = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not db:
            return None
        rows = c.execute("SELECT id, title, icon, props, updated, created, cover, body, format FROM note WHERE uid=? AND parent_id=? "
                         "AND deleted_at IS NULL AND is_template=0 ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()
        tpls = c.execute("SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND is_template=1 "
                         "ORDER BY sort NULLS FIRST, created", (owner, nid)).fetchall()      # Vorlagen der Datenbank: keine Zeilen (wie Notion)
    if visible is not None:
        rows, tpls = [r for r in rows if r[0] in visible], []
    return {"schema": _json_load(db[0], {"props": []}), "templates": [{"id": t[0], "title": t[1] or "", "icon": t[2] or ""} for t in tpls],
            "rows": [{"id": r[0], "title": r[1] or "", "icon": r[2] or "", "props": _json_load(r[3], {}),
                      "updated": r[4], "created": r[5], "preview": r[6] or _first_image(r[7], r[8])} for r in rows]}
