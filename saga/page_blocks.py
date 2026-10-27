"""Bloecke auf eine andere Seite verschieben (2.5, P6-4, Konzept P6 2.2 / P3 3.7).

POST /api/notes/{a}/blocks/move {ids: [...], to: b, after?: <block-id>|null, at?: "start"|"end", rev_a, rev_b?}
in EINER Transaktion: Rechte (Bearbeiten auf A und B), Bloecke samt Kindern aus A loesen und in B einfuegen (IDs bleiben,
neue nur bei Kollision in B), Kommentare wandern mit (note_id/owner_uid/block_id), Dateien: gleicher Eigentuemer -> nichts,
anderer Eigentuemer -> Kopie fuer den Eigentuemer von B (Kontingent von B), Signaturen (Schaltflaechen, Nextcloud) neu bzw.
weg, vorher beide Seiten als Version sichern, danach beide rev+1 und die ueblichen Live-/Index-Ereignisse.
Seiten anderer Saga-Instanzen (negative IDs): nicht verschiebbar - die Clients bieten dort "Kopieren nach ..." an."""
import json
import os
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from db import connect as _db
from saga import bus, nextcloud
from saga.core import _api_guard, _bad, _body, origin_of, t
from saga.databases import btn_sig, _tpl_buttons
from saga.notes import (BLOCK_ID_RE, CONTAINERS, FILE_QUOTA, FILES_DIR, _FILE_URL, _iter_blocks, _new_block_id, _snapshot,
                        ensure_ids, keep_base)
from saga.sharing import RANK, Access, mask_links, restore_links, title_of, viewer
import hmac

router = APIRouter()
MOVE_MAX = 200                                   # Bloecke je Aufruf
NOTE_BLOCKS_MAX = 200_000                        # wie saga.pages (Zeichen JSON je Seite)


class _Stop(Exception):
    def __init__(self, resp):
        super().__init__()
        self.resp = resp


def _err(msg, code=400, **extra):
    return JSONResponse(dict({"error": msg}, **extra), status_code=code)


def _int(v):
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if -2 ** 63 < n < 2 ** 63 else None


def _lists(blocks):
    """Alle Blocklisten eines Dokuments (oberste Ebene, Spalten, Container) -> [(liste, tiefe)]."""
    out, stack = [], [(blocks, 0)]
    while stack:
        lst, d = stack.pop()
        if not isinstance(lst, list) or d > 8:
            continue
        out.append((lst, d))
        for b in lst:
            if not isinstance(b, dict) or not isinstance(b.get("data"), dict):
                continue
            if b.get("type") == "columns":
                for col in b["data"].get("cols") or []:
                    if isinstance(col, dict) and isinstance(col.get("blocks"), list):
                        stack.append((col["blocks"], d + 1))
            elif b.get("type") in CONTAINERS and isinstance(b["data"].get("blocks"), list):
                stack.append((b["data"]["blocks"], d + 1))
    return out


def _take(doc, ids):
    """Bloecke mit diesen IDs (beliebige Tiefe, Kinder wandern mit) aus doc loesen -> Liste in Dokumentreihenfolge.
    Liegt ein gewaehlter Block in einem anderen gewaehlten, zaehlt nur der aeussere."""
    want = set(ids)
    order = {}
    for i, b in enumerate(_iter_blocks(doc["blocks"])):
        if b.get("id") in want and b["id"] not in order:
            order[b["id"]] = i
    inner = set()
    for b in _iter_blocks(doc["blocks"]):
        if b.get("id") in want and b["id"] not in inner:
            sub = [x.get("id") for x in _iter_blocks([b])][1:]
            inner.update(x for x in sub if x in want)
    taken = []
    for lst, _ in _lists(doc["blocks"]):
        keep = []
        for b in lst:
            if isinstance(b, dict) and b.get("id") in want and b["id"] not in inner:
                taken.append(b)
            else:
                keep.append(b)
        lst[:] = keep
    taken.sort(key=lambda b: order.get(b.get("id"), 0))
    return taken


def _flatten_cols(blocks, nested):
    """Spalten in Spalten/Containern gibt es nicht: beim Einfuegen in eine Spalte/einen Container aufloesen."""
    out = []
    for b in blocks:
        if nested and b.get("type") == "columns":
            for col in (b.get("data") or {}).get("cols") or []:
                if isinstance(col, dict):
                    out.extend(x for x in col.get("blocks") or [] if isinstance(x, dict))
        else:
            out.append(b)
    return out


def _place(doc, blocks, after, at):
    """Bloecke in doc einfuegen: nach Block `after` (in dessen Liste), sonst am Anfang/Ende der obersten Ebene."""
    if after:
        for lst, d in _lists(doc["blocks"]):
            for i, b in enumerate(lst):
                if isinstance(b, dict) and b.get("id") == after:
                    lst[i + 1:i + 1] = _flatten_cols(blocks, d > 0)
                    return True
        return False
    if at == "start":
        doc["blocks"][0:0] = blocks
    else:
        doc["blocks"].extend(blocks)
    return True


def _resign(blocks, a, b, owner_a, owner_b, uid):
    """Server-gebundene Signaturen fuer Seite b: Schaltflaechen (Vorlage) nur, wenn derselbe Eigentuemer verschiebt,
    Nextcloud nur fuer eigene Dateien der verschiebenden Person - sonst Signatur weg (Block in b inaktiv). -> Anzahl weg."""
    dropped = 0
    doc = {"blocks": blocks}
    for bt, tpl, target in _tpl_buttons(doc):
        ok = isinstance(bt.get("sig"), str) and hmac.compare_digest(bt["sig"], btn_sig(a, owner_a, tpl, target))
        if ok and owner_a == owner_b and uid == owner_b:
            bt["sig"] = btn_sig(b, owner_b, tpl, target)
        elif "sig" in bt:
            bt.pop("sig")
            dropped += 1
    for blk in nextcloud.nc_blocks(doc):
        d = blk["data"]
        if nextcloud.sig_ok(a, d) and d.get("author") == uid:
            blk["data"] = dict(d, sig=nextcloud.sign(b, d))
        elif "sig" in d:
            blk["data"] = {k: v for k, v in d.items() if k != "sig"}
            dropped += 1
    return dropped


def _file_refs(blocks):
    """Datei-IDs in den Bloecken (Bilder, Anhaenge, Vorschaubilder)."""
    return sorted(set(_FILE_URL.findall(json.dumps(blocks, ensure_ascii=False))))


def _copy_files(c, blocks, owner_b, made):
    """Dateien anderer Eigentuemer fuer owner_b kopieren (Kontingent von b) und die Verweise umschreiben -> Anzahl.
    made: Liste der neu angelegten Dateien (zum Aufraeumen, falls die Transaktion scheitert)."""
    ids = _file_refs(blocks)
    if not ids:
        return 0
    rows = {r[0]: r for r in c.execute(f"SELECT id, uid, name, mime, size, is_image FROM file WHERE id IN ({','.join('?' * len(ids))})", ids)}
    todo = [rows[i] for i in ids if i in rows and rows[i][1] != owner_b]
    if not todo:
        return 0
    c.execute("SELECT pg_advisory_xact_lock(hashtext(?))", ("file:" + owner_b,))
    used = c.execute("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?", (owner_b,)).fetchone()[0]
    if used + sum(r[4] for r in todo) > FILE_QUOTA:
        raise _Stop(_err(t("Speicherplatz voll (max. {n} MB)", n=FILE_QUOTA // 1048576), 507, quota=True))
    repl = {}
    for fid, _, name, mime, size, img in todo:
        src = os.path.join(FILES_DIR, fid)
        if not os.path.isfile(src):
            continue
        nid = os.urandom(12).hex()
        dst = os.path.join(FILES_DIR, nid)
        try:
            os.link(src, dst)                     # Dateien aendern sich nie: harter Link spart Platz
        except OSError:
            with open(src, "rb") as fs, open(dst, "wb") as fd:
                fd.write(fs.read())
        made.append(dst)
        c.execute("INSERT INTO file (id, uid, name, mime, size, is_image, created) VALUES (?,?,?,?,?,?,?)",
                  (nid, owner_b, name, mime, size, img, time.time()))
        repl[fid] = nid

    def walk(v):
        if isinstance(v, str):
            m = _FILE_URL.fullmatch(v)
            return f"/files/{repl[m.group(1)]}" if m and m.group(1) in repl else v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    for blk in blocks:
        if isinstance(blk.get("data"), dict):
            blk["data"] = walk(blk["data"])
    return len(repl)


def _rename_ids(blocks, taken_ids):
    """Kollisionen mit IDs, die es in b schon gibt (und doppelte unter den verschobenen) -> {alt: neu}."""
    seen, ren = set(taken_ids), {}
    for b in _iter_blocks(blocks):
        bid = b.get("id")
        if not isinstance(bid, str) or not BLOCK_ID_RE.fullmatch(bid) or bid in seen:
            nb = _new_block_id()
            while nb in seen:
                nb = _new_block_id()
            if isinstance(bid, str):
                ren[bid] = nb
            b["id"] = nb
        seen.add(b["id"])
    return ren


@router.post("/api/notes/{nid}/blocks/move")
async def api_blocks_move(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if not isinstance(b, dict):
        return _bad(t("ungültige Anfrage"))
    return await run_in_threadpool(blocks_move, uid, nid, b, origin_of(request))


def blocks_move(uid, a, req, origin=None):
    ids = req.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > MOVE_MAX or not all(isinstance(x, str) and BLOCK_ID_RE.fullmatch(x) for x in ids):
        return _bad(t("ungültige Angabe"))
    to, rev_a = _int(req.get("to")), _int(req.get("rev_a"))
    rev_b = _int(req.get("rev_b")) if req.get("rev_b") is not None else None
    after = req.get("after")
    at = req.get("at") if req.get("at") in ("start", "end") else "end"
    if to is None or rev_a is None or (req.get("rev_b") is not None and rev_b is None):
        return _bad(t("ungültige Angabe"))
    if after is not None and not (isinstance(after, str) and BLOCK_ID_RE.fullmatch(after)):
        return _bad(t("ungültige Angabe"))
    if to < 0:                                    # Seite einer anderen Saga-Instanz: kein Verschieben ueber Servergrenzen
        return _err(t("Zwischen verschiedenen Saga-Servern lassen sich Blöcke nicht verschieben – nur kopieren."), 400, federated=True)
    if to == a:
        return _bad(t("Ziel ist dieselbe Seite"))
    made = []
    try:
        with _db() as c, c:
            return _move(c, uid, a, to, ids, after, at, rev_a, rev_b, origin, made)
    except _Stop as s:
        for p in made:                            # Transaktion zurueckgerollt: angelegte Dateikopien wieder weg
            try:
                os.remove(p)
            except OSError:
                pass
        return s.resp


def _move(c, uid, a, b, ids, after, at, rev_a, rev_b, origin, made):
    acc = Access(c, uid)
    for nid in (a, b):
        r = acc.role(nid)
        if not r:
            raise _Stop(_bad("not found", 404))
        if RANK[r] < RANK["edit"]:
            raise _Stop(_bad(t("Dafür fehlt dir die Berechtigung."), 403))
    owner_a, owner_b = acc.owner(a), acc.owner(b)
    rows = {}
    for nid, owner in sorted(((a, owner_a), (b, owner_b))):       # feste Reihenfolge: keine Verklemmung bei Gegenrichtung
        r = c.execute("SELECT title, body, format, rev, locked, updated_by, kind FROM note WHERE id=? AND uid=? AND deleted_at IS NULL FOR UPDATE",
                      (nid, owner)).fetchone()
        if not r:
            raise _Stop(_bad("not found", 404))
        rows[nid] = r
    ra, rb = rows[a], rows[b]
    if ra[4] or rb[4]:
        raise _Stop(_err(t("Seite ist gesperrt – erst entsperren"), 423))
    if ra[2] != "blocks" or rb[2] != "blocks" or rb[6] == "database":
        raise _Stop(_bad(t("Dorthin lassen sich keine Blöcke verschieben.")))
    if ra[3] != rev_a or (rev_b is not None and rb[3] != rev_b):
        raise _Stop(JSONResponse({"error": "conflict", "rev_a": ra[3], "rev_b": rb[3]}, status_code=409))
    doc_a = json.loads(ensure_ids(ra[1], "blocks", a) or '{"blocks": []}')
    doc_b = json.loads(ensure_ids(rb[1], "blocks", b) or '{"blocks": []}')
    if not isinstance(doc_a.get("blocks"), list) or not isinstance(doc_b.get("blocks"), list):
        raise _Stop(_bad(t("kein Block-Dokument")))
    moved = _take(doc_a, ids)
    if not moved:
        raise _Stop(_bad("not found", 404))
    # Verweistexte: nur, was die verschiebende Person sieht (wie Kopieren + Einfuegen); gleicher Eigentuemer: Titel zurueck
    part = json.dumps({"blocks": moved}, ensure_ascii=False)
    if uid != owner_a:
        part = mask_links(part, "blocks", viewer(acc))
    moved = json.loads(part)["blocks"]
    old_ids = [x.get("id") for x in _iter_blocks(moved)]
    ren = _rename_ids(moved, {x.get("id") for x in _iter_blocks(doc_b["blocks"])})
    if not _place(doc_b, moved, after, at):
        raise _Stop(_bad(t("Zielblock nicht gefunden"), 404))
    copied = _copy_files(c, moved, owner_b, made) if owner_a != owner_b else 0
    unsigned = _resign(moved, a, b, owner_a, owner_b, uid)
    body_a = json.dumps(doc_a, ensure_ascii=False)
    body_b = json.dumps(doc_b, ensure_ascii=False)
    if uid != owner_b:
        body_b = restore_links(rb[1], body_b, viewer(acc), title_of(c, owner_b))
    if len(body_b) > NOTE_BLOCKS_MAX:
        raise _Stop(_err(t("Seite zu groß"), 413))
    now = time.time()
    for nid, owner, r, body in ((a, owner_a, ra, body_a), (b, owner_b, rb, body_b)):
        _snapshot(c, owner, nid, r[:3], now, force=True, author=r[5], by=uid)
        c.execute("UPDATE note SET body=?, rev=rev+1, updated_by=?, updated=? WHERE id=? AND uid=?", (body, uid, now, nid, owner))
        keep_base(c, nid, r[3] + 1, r[0], body, "blocks", now, (r[3], r[0], r[1], r[2]))
    # Kommentare: Unterhaltungen an den verschobenen Bloecken (Antworten tragen dieselbe block_id) wandern mit
    moved_ids = [x for x in old_ids if isinstance(x, str)]
    n_comments = 0
    for k in range(0, len(moved_ids), 500):
        part_ids = moved_ids[k:k + 500]
        for old, cid in c.execute(f"SELECT block_id, id FROM note_comment WHERE note_id=? AND block_id IN ({','.join('?' * len(part_ids))})",
                                  (a, *part_ids)).fetchall():
            c.execute("UPDATE note_comment SET note_id=?, owner_uid=?, block_id=? WHERE id=?", (b, owner_b, ren.get(old, old), cid))
            n_comments += 1
    for nid, owner, r in ((a, owner_a, ra), (b, owner_b, rb)):
        bus.fire("note.saved", c, note_id=nid, owner=owner, rev=r[3] + 1, by=uid, updated=now, origin=origin)
    return JSONResponse({"ok": True, "rev_a": ra[3] + 1, "rev_b": rb[3] + 1, "ids": [ren.get(x, x) for x in ids if x in set(old_ids)],
                         "renamed": ren, "files_copied": copied, "unsigned": unsigned, "comments": n_comments, "updated": now})
