"""Papierkorb, Versionsverlauf und Markdown-Export."""
import json
import os
import re
import tempfile
import time
import zipfile
from html import unescape as html_unescape
from urllib.parse import quote
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from db import connect as _db
from fastapi import APIRouter
from saga import i18n, nextcloud
from saga.core import BASE_URL, TZ, _api_guard, _bad, _json_load, origin_of, t
from saga.sharing import Access, access, mask_links, names, person, viewer
from saga.notes import AUTO_TYPES, FILES_DIR, NOTE_META_COLS, TRASH_DAYS, _FILE_URL, _calc_value, _disposition, _note_meta, _note_text, _purge_ids, _purge_old, _snapshot, announce_tree, blocks_text, share_targets, tree_changed

router = APIRouter()


def _trash_batch(c, uid, nid):
    """Seite im Papierkorb + alle mit ihr zusammen geloeschten Unterseiten (gleicher Zeitstempel)."""
    r = c.execute("SELECT deleted_at FROM note WHERE id=? AND uid=? AND deleted_at IS NOT NULL", (nid, uid)).fetchone()
    if not r:
        return None, []
    ids = [x[0] for x in c.execute(
        "WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id "
        "WHERE n.uid=? AND n.deleted_at=?) SELECT id FROM t", (nid, uid, r[0]))]
    return r[0], ids


@router.get("/api/trash")
def api_trash(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _purge_old(c, uid)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, deleted_at FROM note n WHERE uid=? AND deleted_at IS NOT NULL "
                         "AND NOT EXISTS (SELECT 1 FROM note p WHERE p.id=n.parent_id AND p.uid=n.uid AND p.deleted_at=n.deleted_at) "
                         "ORDER BY deleted_at DESC", (uid,)).fetchall()
        out = []
        for r in rows:
            d = _note_meta(r)
            _, ids = _trash_batch(c, uid, r[0])
            d.update(deleted_at=r[9], subpages=len(ids) - 1, purge_at=r[9] + TRASH_DAYS * 86400)
            out.append(d)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/api/trash/{nid}/restore")
def api_trash_restore(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _, ids = _trash_batch(c, uid, nid)
        if not ids:
            return _bad("not found", 404)
        c.executemany("UPDATE note SET deleted_at=NULL WHERE id=? AND uid=?", [(i, uid) for i in ids])
        # Elternseite weg oder selbst im Papierkorb -> auf oberste Ebene
        par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, uid)).fetchone()[0]
        before = None
        if par and not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (par, uid)).fetchone():
            before = share_targets(c, uid, [nid])                     # Kette vorher (Live: alte und neue Kette)
            c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, uid))
        announce_tree(c, uid, nid, ids, uid, time.time(), False, origin=origin_of(request), before=before)   # Live: zurueck (BUG-033)
        tree_changed(c, uid, ids, "restored", origin_of(request), before=before)
    return JSONResponse({"restored": len(ids)})


@router.delete("/api/trash/{nid}")
def api_trash_purge(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _, ids = _trash_batch(c, uid, nid)
        if not ids:
            return _bad("not found", 404)
        _purge_ids(c, uid, ids)
    return JSONResponse({"purged": len(ids)})


@router.post("/api/trash/empty")
def api_trash_empty(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        ids = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NOT NULL", (uid,))]
        _purge_ids(c, uid, ids)
    return JSONResponse({"purged": len(ids)})


# Versionen (2.3): ansehen und wiederherstellen ab Rolle 'edit' (vorher nur der Eigentuemer) - auch fuer Personen
# anderer Saga-Instanzen (saga.fed_pages). Die Fassungen bleiben beim Eigentuemer; Verweise auf Seiten ohne Zugriff
# zeigen Nicht-Eigentuemern wie beim Lesen nur den Platzhalter.
VERSION_ROLE = "edit"


@router.get("/api/notes/{nid}/versions")
def api_versions(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return versions_list(uid, nid)


def versions_list(uid, nid):
    with _db() as c:
        owner, _, err = access(c, uid, nid, VERSION_ROLE)
        if err is not None:
            return err
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        rows = c.execute("SELECT id, title, created, length(body), author FROM note_version WHERE uid=? AND note_id=? "
                         "ORDER BY created DESC", (owner, nid)).fetchall()
        nm = names(c, [r[4] for r in rows])
    # author: wer diese Fassung geschrieben hat (None = Eigentuemer bzw. unbekannt); by: "me" | "other" | "unknown" (BUG-092)
    return JSONResponse([{"id": r[0], "title": r[1] or "", "created": r[2], "size": r[3] or 0,
                          "author": person(c, r[4], uid, nm) if r[4] and r[4] != uid else None,
                          "by": "me" if r[4] == uid else "other" if r[4] else "unknown"} for r in rows],
                        headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}/versions/{vid}")
def api_version_get(nid: int, vid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return version_get(uid, nid, vid)


def version_get(uid, nid, vid):
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, VERSION_ROLE, acc)
        if err is not None:
            return err
        r = c.execute("SELECT v.title, v.body, v.format, v.created FROM note_version v JOIN note n ON n.id=v.note_id AND n.uid=v.uid "
                      "WHERE v.id=? AND v.note_id=? AND v.uid=? AND n.deleted_at IS NULL", (vid, nid, owner)).fetchone() if 0 < vid < 2 ** 63 else None
        body = (mask_links(r[1], r[2], viewer(acc)) if role != "owner" else r[1]) if r else None
    if not r:
        return _bad("not found", 404)
    return JSONResponse({"title": r[0] or "", "created": r[3], "text": _note_text(r[2], body, True)[:5000]},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/notes/{nid}/versions/{vid}/restore")
def api_version_restore(nid: int, vid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return version_restore(uid, nid, vid, origin_of(request))


def version_restore(uid, nid, vid, origin=None):
    with _db() as c, c:
        owner, role, err = access(c, uid, nid, VERSION_ROLE)
        if err is not None:
            return err
        cur = c.execute("SELECT title, body, format, updated_by, locked FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        v = c.execute("SELECT title, body, format FROM note_version WHERE id=? AND note_id=? AND uid=?", (vid, nid, owner)).fetchone() \
            if 0 < vid < 2 ** 63 else None
        if not cur or not v:
            return _bad("not found", 404)
        if cur[4] and role != "owner":                  # gesperrte Seite: nur der Eigentuemer
            return JSONResponse({"error": t("Seite ist gesperrt – erst entsperren")}, status_code=423)
        now = time.time()
        _snapshot(c, owner, nid, cur[:4], now, force=True, author=cur[3], by=uid)       # aktueller Stand bleibt als Version erhalten
        body = nextcloud.limit_restore(uid, nid, cur[1], cur[2], v[1], v[2])     # Nextcloud: fremde Schalter nicht wieder oeffnen
        c.execute("UPDATE note SET title=?, body=?, format=?, updated=?, rev=rev+1, updated_by=? WHERE id=? AND uid=?",
                  (v[0], body, v[2], now, uid, nid, owner))
        rev = c.execute("SELECT rev FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
        announce_tree(c, owner, nid, [nid], uid, now, False, rev, origin=origin)       # Live: Version zurueck (BUG-033)
        if v[0] != cur[0]:
            tree_changed(c, owner, [nid], "updated", origin)                      # anderer Titel im Baum
    return JSONResponse({"ok": True, "updated": now, "rev": rev})


# --- Markdown-Export -------------------------------------------------------------
def _md_inline(s, link):
    s = re.sub(r"<br\s*/?>", "  \n", s or "")
    s = re.sub(r"</?(b|strong)>", "**", s)
    s = re.sub(r"</?(i|em)>", "*", s)
    s = re.sub(r"</?s>", "~~", s)
    s = re.sub(r'<code[^>]*>(.*?)</code>', r"`\1`", s, flags=re.S)
    s = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>', lambda m: f"[{m.group(2)}]({link(html_unescape(m.group(1)))})", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "", s)
    return html_unescape(s)


def blocks_to_md(blocks, link=lambda h: h):
    out = []

    def items(lst, style, depth):
        for i, it in enumerate(lst or [], 1):
            if isinstance(it, str):
                it = {"content": it, "items": []}
            pre = "  " * depth + ("- [x] " if style == "checklist" and (it.get("meta") or {}).get("checked") else
                                  "- [ ] " if style == "checklist" else f"{i}. " if style == "ordered" else "- ")
            out.append(pre + _md_inline(it.get("content", ""), link))
            items(it.get("items"), style, depth + 1)

    for b in blocks:
        typ, d = b.get("type"), b.get("data") or {}
        if typ == "header":
            out += ["#" * min(max(int(d.get("level", 2)), 1), 6) + " " + _md_inline(d.get("text", ""), link), ""]
        elif typ == "list":
            items(d.get("items"), d.get("style", "unordered"), 0); out.append("")
        elif typ == "quote":
            out += ["> " + l for l in _md_inline(d.get("text", ""), link).split("\n")]
            if d.get("caption"):
                out.append("> — " + _md_inline(d["caption"], link))
            out.append("")
        elif typ == "code":
            lang = re.sub(r"[^a-z0-9+-]", "", str(d.get("language") or "").lower())[:20]
            out += ["```" + lang, d.get("code", ""), "```", ""]
        elif typ == "delimiter":
            out += ["---", ""]
        elif typ == "table":
            rows = d.get("content") or []
            if rows:
                cells = [[_md_inline(str(x), link).replace("|", "\\|").replace("\n", " ") for x in r] for r in rows]
                w = max(len(r) for r in cells)
                cells = [r + [""] * (w - len(r)) for r in cells]
                head, body = (cells[0], cells[1:]) if d.get("withHeadings") else ([""] * w, cells)
                out += ["| " + " | ".join(head) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in body] + [""]
        elif typ == "image":
            out += [f"![{_md_inline(d.get('caption', ''), link)}]({link((d.get('file') or {}).get('url', ''))})", ""]
        elif typ == "attaches":
            f = d.get("file") or {}
            out += [f"[📎 {d.get('title') or f.get('name') or t('Datei')}]({link(f.get('url', ''))})", ""]
        elif typ == "nextcloud":                       # Link statt Kopie (die Datei bleibt in Nextcloud)
            out += [nextcloud.block_md(d), ""]
        elif typ == "columns":
            for col in d.get("cols") if isinstance(d.get("cols"), list) else []:
                sub = blocks_to_md(col.get("blocks") or [], link).strip() if isinstance(col, dict) else ""
                if sub:
                    out += [sub, ""]
        elif typ == "toggle":
            inner = (blocks_to_md(d["blocks"], link).strip() if isinstance(d.get("blocks"), list)
                     else _md_inline(d.get("text", ""), link))
            out += [f"<details><summary>{_md_inline(d.get('title', ''), link)}</summary>", "", inner, "", "</details>", ""]
        elif typ == "callout":
            out += ["> " + (d.get("emoji") or "💡") + " " + _md_inline(d.get("text", ""), link).replace("\n", "\n> "), ""]
        else:
            txt = _md_inline(d.get("text", ""), link) if "text" in d else blocks_text([b])
            if txt.strip():
                out += [txt, ""]
    return "\n".join(out).strip() + "\n"


def _safe_name(s):
    s = re.sub(r"[^\w äöüÄÖÜß.\-]+", "_", s or t("Unbenannt")).strip(" ._") or t("Unbenannt")
    return s[:80]


def md_page(rows, r, link):
    """Eine Seite als Markdown (Export, persoenliches Backup). rows: id -> [id, title, icon, parent_id, body, format, kind,
    schema, props, created, updated] (alle exportierten Seiten; Datenbank-Zeilen, Relationen und Eltern daraus)."""
    def val_of(p, row, pv):
        if p["type"] == "calc":
            return _calc_value(p, pv)
        return {"created": row[9], "edited": row[10]}.get(p["type"], pv.get(p["id"]))

    def fmt_val(p, v):
        if v is None:
            return ""
        if p["type"] in AUTO_TYPES:
            return i18n.fmt_date(v, TZ, with_time=True)          # Datumsformat der Sprache
        if p["type"] == "relation":
            return ", ".join((rows[x][1] or t("Unbenannt")) for x in v if x in rows)
        if p["type"] in ("number", "calc"):
            v = round(v, 2) if isinstance(v, float) else v
            return f"{v} %" if p.get("fmt") == "percent" else f"{v} €" if p.get("fmt") == "euro" else str(v)
        if p["type"] in ("select", "multi_select"):
            names = {o["id"]: o["name"] for o in p.get("options", [])}
            return ", ".join(names.get(x, "") for x in (v if isinstance(v, list) else [v]))
        if p["type"] == "checkbox":
            return "✓" if v else ""
        return str(v)

    head = f"# {(r[2] + ' ') if r[2] and not r[2].startswith('/files/') else ''}{r[1] or t('Unbenannt')}\n\n"
    par = rows.get(r[3])
    if r[6] == "database":           # Datenbank -> Markdown-Tabelle aller Zeilen
        props = _json_load(r[7], {"props": []}).get("props", [])
        cells = lambda s: str(s).replace("|", "\\|").replace("\n", " ")
        tname = _json_load(r[7], {}).get("title_name") or t("Name")
        lines = [f"| {cells(tname)} | " + " | ".join(cells(p["name"]) for p in props) + " |", "|---|" + "---|" * len(props)]
        for x in sorted((x for x in rows.values() if x[3] == r[0]), key=lambda x: x[1] or ""):
            pv = _json_load(x[8], {})
            lines.append(f"| [{cells(x[1] or t('Unbenannt'))}]({link('/notes#' + str(x[0]))}) | "
                         + " | ".join(cells(fmt_val(p, val_of(p, x, pv))) for p in props) + " |")
        return head + "\n".join(lines) + "\n"
    if par and par[6] == "database":  # Zeile -> Eigenschaften als Liste unter dem Titel
        pv = _json_load(r[8], {})
        lst = [f"- **{p['name']}:** {fmt_val(p, val_of(p, r, pv))}"
               for p in _json_load(par[7], {"props": []}).get("props", []) if val_of(p, r, pv) not in (None, "", [])]
        if lst:
            head += "\n".join(lst) + "\n\n"
    if r[5] == "blocks":
        try:
            return head + blocks_to_md(json.loads(r[4]).get("blocks", []), link)
        except ValueError:
            return head
    return head + (r[4] or "") + "\n"


@router.get("/api/notes/{nid}/export")
def api_note_export(nid: int, request: Request, sub: int = 0):
    """Seite als Markdown (.md) oder mit allen Unterseiten als ZIP (Ordnerstruktur)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return note_export(uid, nid, sub)


def note_export(uid, nid, sub=0):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person: nur, was sie sehen darf)."""
    with _db() as c:                     # erst ohne Inhalte (Baum, Titel, Eigenschaften); Inhalte nur fuer exportierte Seiten
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)      # Lesen reicht; fremde Seiten: nur was ich sehen darf
        if err is not None:
            return err
        rows = {r[0]: list(r) for r in c.execute("SELECT id, title, icon, parent_id, NULL, format, kind, schema, props, created, updated FROM note "
                                                 "WHERE uid=? AND deleted_at IS NULL", (owner,))}
        if role != "owner":
            vis = acc.tree(owner)
            rows = {k: v for k, v in rows.items() if k in vis}
    if nid not in rows:
        return _bad("not found", 404)

    def load_bodies(ids):
        ids = list(ids)
        with _db() as c:
            vis = viewer(Access(c, uid)) if role != "owner" else None
            for k in range(0, len(ids), 500):
                part = ids[k:k + 500]
                for i, body in c.execute(f"SELECT id, body FROM note WHERE uid=? AND id IN ({','.join('?' * len(part))})", (owner, *part)):
                    # fremd: Verweise auf Seiten ohne Zugriff ohne ihren Titel
                    rows[i][4] = mask_links(body, rows[i][5], vis) if vis else body

    md_of = lambda r, link: md_page(rows, r, link)      # noqa: E731
    ext_link = lambda h: (BASE_URL + h) if h.startswith(("/notes#", "/files/")) else h
    if not sub:
        load_bodies([nid])
        r = rows[nid]
        return Response(md_of(r, ext_link).encode(), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": _disposition(_safe_name(r[1]) + ".md")})
    # ZIP: Seite + Unterseiten, Verweise innerhalb des Exports als relative Links
    paths, used = {}, set()

    def place(i, folder):
        name, k = _safe_name(rows[i][1]), 1
        while f"{folder}{name}.md".lower() in used:
            k += 1; name = f"{_safe_name(rows[i][1])} ({k})"
        used.add(f"{folder}{name}.md".lower()); paths[i] = f"{folder}{name}.md"
        for ch in sorted((x for x in rows.values() if x[3] == i), key=lambda x: x[1] or ""):
            place(ch[0], f"{folder}{name}/")
    place(nid, "")
    load_bodies(paths)
    # Bilder/Dateien der exportierten Seiten mit einpacken (Ordner _dateien)
    fids = set()
    for i in paths:
        fids |= set(_FILE_URL.findall(rows[i][4] or ""))
    fpaths = {}
    if fids:
        with _db() as c:
            for fid, name in c.execute(f"SELECT id, name FROM file WHERE uid=? AND id IN ({','.join('?' * len(fids))})", (owner, *fids)):
                fpaths[fid] = f"_dateien/{fid[:8]}_{_safe_name(name)}"
    # ZIP in eine Temp-Datei (Daten-Volume, siehe files_init) statt in den Speicher - mit Dateien bis zur Quote gross
    tmp = tempfile.NamedTemporaryFile(prefix="export-", suffix=".zip", delete=False)
    try:
        _write_zip(tmp, paths, fpaths, rows, md_of, ext_link)
    except BaseException:
        tmp.close(); os.remove(tmp.name)
        raise
    tmp.close()
    return _TempFileResponse(tmp.name, media_type="application/zip",
                             headers={"Content-Disposition": _disposition(_safe_name(rows[nid][1]) + ".zip"), "Cache-Control": "no-store"})


class _TempFileResponse(FileResponse):
    """FileResponse, die ihre (temporaere) Datei immer loescht - auch wenn der Client den Download abbricht
    (dann laeuft ein BackgroundTask nicht)."""

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                os.remove(self.path)
            except OSError:
                pass


def _write_zip(fh, paths, fpaths, rows, md_of, ext_link):
    with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as z:
        for i, path in paths.items():
            def rel(h, here=path):
                m = re.fullmatch(r"/notes#(\d+)", h)
                if m and int(m.group(1)) in paths:
                    return quote(os.path.relpath(paths[int(m.group(1))], os.path.dirname(here) or "."))
                m = _FILE_URL.fullmatch(h)
                if m and m.group(1) in fpaths:
                    return quote(os.path.relpath(fpaths[m.group(1)], os.path.dirname(here) or "."))
                return ext_link(h)
            z.writestr(path, md_of(rows[i], rel))
        for fid, fp in fpaths.items():
            try:
                z.write(os.path.join(FILES_DIR, fid), fp)
            except FileNotFoundError:
                pass
