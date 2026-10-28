"""Kommentare (1.3): Unterhaltungen an Bloecken einer Seite - dezent: am Block steht nur eine kleine Zahl, die
Unterhaltung oeffnet sich in einer Seitenleiste; der Seiteninhalt bleibt unveraendert.

- Lesen darf jede Rolle (auch 'view'), schreiben, antworten und "Erledigt" ab 'comment'. Bearbeiten nur der Verfasser,
  loeschen der Verfasser oder der Eigentuemer der Seite (Moderation). Auch auf eigenen, nicht geteilten Seiten.
- Klartext (hoechstens 2000 Zeichen, Zeilenumbrueche erlaubt; Links erkennt die Anzeige), hoechstens 30 Kommentare
  je Minute und Konto.
- Eine Unterhaltung = erster Kommentar (thread_id NULL) + Antworten. "Erledigt" klappt sie zu; ungelesen ist sie, wenn
  jemand anderes nach meinem letzten Blick (note_comment_seen) geschrieben hat.
- Wird der Block geloescht, bleibt die Unterhaltung (block_exists=false, Anzeige "Block entfernt")."""
import json
import re
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from db import connect as _db
from saga import bus
from saga.core import _api_guard, _bad, _body, t
from saga.notes import _iter_blocks, ensure_ids
from saga.sharing import RANK, Access, access, names, person

router = APIRouter()

COMMENT_MAX, COMMENT_RATE, THREADS_MAX = 2000, 30, 500
_BID = re.compile(r"[A-Za-z0-9_-]{0,40}")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _clean(body):
    s = str(body if body is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    s = _CTRL.sub("", s)
    return re.sub(r"\n{4,}", "\n\n\n", s).strip()


def block_ids(body, fmt, nid=None):
    """IDs aller Bloecke einer Seite (auch in Spalten/Ausklappbar) - wie GET /api/notes/{id} sie liefert (ensure_ids)."""
    if fmt != "blocks":
        return set()
    try:
        return {str(b.get("id")) for b in _iter_blocks(json.loads(ensure_ids(body, fmt, nid) or "{}").get("blocks", [])) if b.get("id")}
    except (ValueError, AttributeError):
        return set()


def counts(c, uid, nid):
    """Fuer GET /api/notes/{nid}: ({block_id: offene Unterhaltungen}, offen gesamt, ungelesen gesamt)."""
    per = {r[0]: r[1] for r in c.execute("SELECT block_id, COUNT(*) FROM note_comment WHERE note_id=? AND thread_id IS NULL "
                                         "AND resolved_at IS NULL GROUP BY block_id", (nid,))}
    unread = c.execute(
        "SELECT COUNT(*) FROM note_comment r WHERE r.note_id=? AND r.thread_id IS NULL AND r.resolved_at IS NULL AND EXISTS ("
        " SELECT 1 FROM note_comment x WHERE (x.id=r.id OR x.thread_id=r.id) AND (x.uid IS NULL OR x.uid<>?)"
        " AND x.created > COALESCE((SELECT s.seen_at FROM note_comment_seen s WHERE s.uid=? AND s.thread_id=r.id), 0))",
        (nid, uid, uid)).fetchone()[0]
    return per, sum(per.values()), unread


def _cdict(r, me, nm):
    """r = (id, thread_id, block_id, uid, body, created, edited). Leerer Text = geloeschter erster Kommentar,
    der wegen fremder Antworten stehen bleibt ("Kommentar geloescht")."""
    return {"id": r[0], "thread_id": r[1], "block_id": r[2], "author": person(None, r[3], me, nm), "body": r[4],
            "created": r[5], "edited": r[6], "mine": bool(r[3]) and r[3] == me, "deleted": not r[4]}


C_COLS = "id, thread_id, block_id, uid, body, created, edited"


def _seen(c, uid, thread, now=None):
    c.execute("INSERT OR REPLACE INTO note_comment_seen (uid, thread_id, seen_at) VALUES (?,?,?)", (uid, thread, now or time.time()))


@router.get("/api/notes/{nid}/comments")
def api_comments(nid: int, request: Request, resolved: int = 0):
    """Unterhaltungen der Seite (ohne erledigte; ?resolved=1 mit)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return comments_list(uid, nid, resolved)


def comments_list(uid, nid, resolved=0):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person '@name@host')."""
    with _db() as c:
        _, role, err = access(c, uid, nid, "view")
        if err is not None:
            return err
        n = c.execute("SELECT body, format FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone()
        if not n:
            return _bad("not found", 404)
        roots = c.execute(f"SELECT {C_COLS}, resolved_at, resolved_by FROM note_comment WHERE note_id=? AND thread_id IS NULL"
                          + ("" if resolved else " AND resolved_at IS NULL") + " ORDER BY created, id LIMIT ?", (nid, THREADS_MAX)).fetchall()
        n_res = c.execute("SELECT COUNT(*) FROM note_comment WHERE note_id=? AND thread_id IS NULL AND resolved_at IS NOT NULL",
                          (nid,)).fetchone()[0]
        reps = {}
        if roots:
            ids = [r[0] for r in roots]
            for r in c.execute(f"SELECT {C_COLS} FROM note_comment WHERE note_id=? AND thread_id IN ({','.join('?' * len(ids))}) "
                               "ORDER BY created, id", (nid, *ids)):
                reps.setdefault(r[1], []).append(r)
            seen = {r[0]: r[1] for r in c.execute(f"SELECT thread_id, seen_at FROM note_comment_seen WHERE uid=? AND thread_id IN "
                                                  f"({','.join('?' * len(ids))})", (uid, *ids))}
        else:
            seen = {}
        nm = names(c, [r[3] for r in roots] + [r[8] for r in roots] + [x[3] for v in reps.values() for x in v])
        from saga import mentions           # 2.5 (P6-5): Erwaehnungen "<@ref>" in Kommentaren -> Woerterbuch people
        pm = mentions.comment_people(c, [r[4] for r in roots] + [x[4] for v in reps.values() for x in v], uid)
    blocks = block_ids(n[0], n[1], nid)
    threads = []
    for r in roots:
        cm = [_cdict(r[:7], uid, nm)] + [_cdict(x, uid, nm) for x in reps.get(r[0], [])]
        last_other = max([x["created"] for x in cm if not x["mine"]] or [0])
        threads.append({"id": r[0], "block_id": r[2], "block_exists": (not r[2]) or r[2] in blocks,
                        "resolved_at": r[7], "resolved_by": person(None, r[8], uid, nm) if r[8] else None,
                        "unread": last_other > seen.get(r[0], 0), "comments": cm})
    out = {"role": role, "can_comment": RANK[role] >= RANK["comment"], "is_owner": role == "owner",
           "threads": threads, "resolved_count": n_res, "max": COMMENT_MAX}
    if pm:
        out["people"] = pm
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/api/notes/{nid}/comments")
async def api_comment_add(nid: int, request: Request):
    """Neue Unterhaltung {block_id, body} oder Antwort {thread_id, body}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    return comment_add(uid, nid, b)


def comment_add(uid, nid, b):
    body = _clean(b.get("body"))
    now = time.time()
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "comment")
        if err is not None:
            return err
        if not c.execute("SELECT 1 FROM note WHERE id=? AND deleted_at IS NULL", (nid,)).fetchone():
            return _bad("not found", 404)
        if not body:
            return _bad(t("Der Kommentar ist leer."))
        if len(body) > COMMENT_MAX:
            return _bad(t("Höchstens {n} Zeichen.", n=COMMENT_MAX))
        if c.execute("SELECT COUNT(*) FROM note_comment WHERE uid=? AND created>?", (uid, now - 60)).fetchone()[0] >= COMMENT_RATE:
            return _bad(t("Zu viele Kommentare in kurzer Zeit – bitte kurz warten."), 429)
        thread, block = None, ""
        if b.get("thread_id") is not None:
            try:
                tid = int(b["thread_id"])
            except (TypeError, ValueError, OverflowError):
                return _bad(t("ungültige Angabe"))
            r = c.execute("SELECT note_id, thread_id, block_id FROM note_comment WHERE id=?", (tid,)).fetchone() if 0 < tid < 2 ** 63 else None
            if not r or r[0] != nid:
                return _bad("not found", 404)
            thread, block = r[1] or tid, r[2]
            if r[1]:
                block = c.execute("SELECT block_id FROM note_comment WHERE id=?", (thread,)).fetchone()[0]
            c.execute("UPDATE note_comment SET resolved_at=NULL, resolved_by=NULL WHERE id=?", (thread,))   # Antwort oeffnet wieder
        else:
            block = str(b.get("block_id") or "")
            if not _BID.fullmatch(block):
                return _bad(t("ungültiger Block"))
        owner = c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone()[0]
        if "<@@" in body:                     # 2.5 (P6-5): Prinzipale dieser Instanz -> Handle (Kommentar von einer anderen Instanz)
            from saga import mentions
            body = mentions.normalize_tokens(c, body)
        cid = c.execute("INSERT INTO note_comment (note_id, owner_uid, block_id, thread_id, uid, body, created) VALUES (?,?,?,?,?,?,?)",
                        (nid, owner, block, thread, uid, body, now)).lastrowid
        _seen(c, uid, thread or cid, now)
        # Benachrichtigungen an Beteiligte/Eigentuemer (saga.notify), Zahlen live an alle mit Zugriff (saga.live)
        bus.fire("comment.added", c, note_id=nid, owner=owner, comment_id=cid, thread_id=thread or cid, block_id=block, author=uid)
        nm = names(c, [uid])
        return JSONResponse({"comment": _cdict((cid, thread, block, uid, body, now, None), uid, nm), "thread_id": thread or cid})


def _load(c, uid, cid):
    """-> (zeile (id, note_id, thread_id, uid, body), rolle, Access) oder (None, None, fehler)"""
    r = c.execute("SELECT id, note_id, thread_id, uid, body FROM note_comment WHERE id=?", (cid,)).fetchone() if 0 < cid < 2 ** 63 else None
    if not r:
        return None, None, _bad("not found", 404)
    acc = Access(c, uid)
    role = acc.role(r[1])
    if not role or not c.execute("SELECT 1 FROM note WHERE id=? AND deleted_at IS NULL", (r[1],)).fetchone():
        return None, None, _bad("not found", 404)
    return r, role, None


@router.patch("/api/comments/{cid}")
async def api_comment_edit(cid: int, request: Request):
    """Eigenen Kommentar bearbeiten {body}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return comment_edit(uid, cid, await _body(request) or {})


def comment_edit(uid, cid, b):
    body = _clean(b.get("body"))
    with _db() as c, c:
        r, role, err = _load(c, uid, cid)
        if err is not None:
            return err
        if RANK[role] < RANK["comment"] or r[3] != uid:
            return _bad(t("Nur eigene Kommentare lassen sich bearbeiten."), 403)
        if not r[4]:
            return _bad(t("Gelöschte Kommentare lassen sich nicht bearbeiten."), 409)
        if not body:
            return _bad(t("Der Kommentar ist leer."))
        if len(body) > COMMENT_MAX:
            return _bad(t("Höchstens {n} Zeichen.", n=COMMENT_MAX))
        now = time.time()
        if "<@@" in body:                     # 2.5 (P6-5): wie comment_add
            from saga import mentions
            body = mentions.normalize_tokens(c, body)
        c.execute("UPDATE note_comment SET body=?, edited=? WHERE id=?", (body, now, cid))
        bus.fire("comments.changed", c, note_id=r[1])
        row = c.execute(f"SELECT {C_COLS} FROM note_comment WHERE id=?", (cid,)).fetchone()
        return JSONResponse({"ok": True, "comment": _cdict(row, uid, names(c, [uid]))})


@router.delete("/api/comments/{cid}")
def api_comment_delete(cid: int, request: Request, all: int = 0):
    """Kommentar loeschen (Verfasser oder Eigentuemer der Seite).
    Erster Kommentar ohne fremde Antworten: ganze Unterhaltung weg. Mit fremden Antworten bleiben die Antworten,
    der erste Kommentar wird zu "Kommentar geloescht" (Text leer). ?all=1 (nur Eigentuemer): ganze Unterhaltung."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return comment_delete(uid, cid, all)


def comment_delete(uid, cid, all=0):
    with _db() as c, c:
        r, role, err = _load(c, uid, cid)
        if err is not None:
            return err
        if not (role == "owner" or (r[3] == uid and RANK[role] >= RANK["comment"])):
            return _bad(t("Nur eigene Kommentare lassen sich löschen."), 403)
        if all and r[2] is None and role != "owner":
            return _bad(t("Nur eigene Kommentare lassen sich löschen."), 403)
        soft = False
        if r[2] is None:                               # Anfang der Unterhaltung
            others = [x[0] for x in c.execute("SELECT uid FROM note_comment WHERE thread_id=?", (cid,))]
            if not all and any(x != r[3] or x is None for x in others):
                c.execute("UPDATE note_comment SET body='' WHERE id=?", (cid,))
                n, soft = 1, True
            else:
                n = c.execute("DELETE FROM note_comment WHERE id=? OR thread_id=?", (cid, cid)).rowcount
                c.execute("DELETE FROM note_comment_seen WHERE thread_id=?", (cid,))
        else:
            n = c.execute("DELETE FROM note_comment WHERE id=?", (cid,)).rowcount
            # letzte Antwort unter einem geloeschten ersten Kommentar -> Unterhaltung ganz weg
            if not c.execute("SELECT 1 FROM note_comment WHERE thread_id=? LIMIT 1", (r[2],)).fetchone() and \
                    c.execute("SELECT 1 FROM note_comment WHERE id=? AND body=''", (r[2],)).fetchone():
                n += c.execute("DELETE FROM note_comment WHERE id=?", (r[2],)).rowcount
                c.execute("DELETE FROM note_comment_seen WHERE thread_id=?", (r[2],))
        bus.fire("comments.changed", c, note_id=r[1])
    return JSONResponse({"ok": True, "deleted": n, "soft": soft})


@router.post("/api/comments/{cid}/resolve")
async def api_comment_resolve(cid: int, request: Request):
    """Unterhaltung erledigen bzw. wieder oeffnen {resolved: true|false}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return comment_resolve(uid, cid, await _body(request) or {})


def comment_resolve(uid, cid, b):
    on = b.get("resolved", True) is not False
    with _db() as c, c:
        r, role, err = _load(c, uid, cid)
        if err is not None:
            return err
        if RANK[role] < RANK["comment"]:
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        root = r[2] or r[0]
        now = time.time() if on else None
        c.execute("UPDATE note_comment SET resolved_at=?, resolved_by=? WHERE id=?", (now, uid if on else None, root))
        bus.fire("comments.changed", c, note_id=r[1])
    return JSONResponse({"ok": True, "thread_id": root, "resolved_at": now})


@router.post("/api/comments/{cid}/seen")
def api_comment_seen(cid: int, request: Request):
    """Unterhaltung als gelesen markieren (kleiner Punkt verschwindet)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return comment_seen(uid, cid)


def comment_seen(uid, cid):
    with _db() as c, c:
        r, _, err = _load(c, uid, cid)
        if err is not None:
            return err
        _seen(c, uid, r[2] or r[0])
        bus.fire("comment.seen", c, uid=uid, note_id=r[1])          # ungelesen-Punkte in anderen Tabs
    return JSONResponse({"ok": True})
