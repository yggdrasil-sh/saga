"""Aufgaben (Inbox je Konto). 2.5 (Paket 7): GET ?with_sources=1 und POST {src} - Aufgabenquellen in saga.tasksrc."""
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from fastapi import Request
from fastapi.responses import JSONResponse
from db import clean_line, connect as _db
from fastapi import APIRouter
from saga import bus
from saga.core import TZ, _api_guard, _bad, _body, t

router = APIRouter()


# --- Aufgaben & Notizen (pro Benutzer) --------------------------------------
# Sicherheitsprinzip: JEDE Abfrage filtert auf die uid aus der Session.
# Fremde oder unbekannte IDs liefern 404 (nicht 403), damit nicht einmal
# sichtbar wird, ob es die ID gibt.
TASK_MAX_LEN, TASK_MAX_COUNT = 500, 500
PRIOS = {1, 2, 3}                                    # 1 hoch, 2 normal, 3 niedrig
REPEATS = {"daily", "weekdays", "weekly", "monthly", "yearly"}
TASK_COLS = "id, text, done, due, prio, repeat"


def _due(v):
    """'' / None -> None, 'YYYY-MM-DD' -> str, sonst ValueError."""
    if v in (None, ""):
        return None
    return date.fromisoformat(str(v)).isoformat()


def _text_in(v):
    """Aufgabentext: null -> leer, Liste/Objekt/Wahrheitswert -> leer statt "None"/"['x']" (BUG-083), einzeilig (BUG-038)."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        return ""
    return clean_line(v).strip()[:TASK_MAX_LEN]


def _prio(v):
    p = int(v)
    if p not in PRIOS:
        raise ValueError
    return p


def _repeat(v):
    if v in (None, "", "none"):
        return None
    if v not in REPEATS:
        raise ValueError
    return v


def _today():
    """Heute in der eingestellten Zeitzone (TIMEZONE) - nicht in der des Containers (meist UTC)."""
    return datetime.now(TZ).date()


def _client_today(b):
    """'Heute' aus Sicht des Browsers (Zeitzone des Benutzers), sonst Server-Datum."""
    try:
        t = date.fromisoformat(str(b.get("today")))
        if abs((t - _today()).days) <= 1:   # Plausibilitaet: max. 1 Tag Zeitzonen-Versatz
            return t
    except (ValueError, TypeError):
        pass
    return _today()


def next_due(due_iso, repeat, anchor_iso, today):
    """Naechstes Faelligkeitsdatum einer wiederkehrenden Aufgabe.

    - Monatlich/jaehrlich richtet sich nach dem Tag des Ursprungsdatums (anchor):
      31.01. -> 28./29.02. -> 31.03. (kein schleichendes Wandern auf den 28.)
    - Ueberfaellige Wiederholungen stapeln sich nicht: es wird bis zum ersten
      Termin >= heute vorgespult.
    """
    d = date.fromisoformat(due_iso) if due_iso else today
    a = date.fromisoformat(anchor_iso) if anchor_iso else d

    def step(x):
        if repeat == "daily":
            return x + timedelta(days=1)
        if repeat == "weekdays":
            x += timedelta(days=1)
            while x.weekday() >= 5:
                x += timedelta(days=1)
            return x
        if repeat == "weekly":
            return x + timedelta(weeks=1)
        if repeat == "monthly":
            y, m = (x.year + 1, 1) if x.month == 12 else (x.year, x.month + 1)
            return date(y, m, min(a.day, monthrange(y, m)[1]))
        if repeat == "yearly":
            y = x.year + 1
            return date(y, a.month, min(a.day, monthrange(y, a.month)[1]))
        raise ValueError(repeat)

    n = step(d)
    for _ in range(5000):          # Schutz gegen Endlosschleife bei absurden Daten
        if n >= today:
            break
        n = step(n)
    return n.isoformat()


def _task_row(r):
    return {"id": r[0], "text": r[1], "done": bool(r[2]), "due": r[3], "prio": r[4], "repeat": r[5]}


def _note_row(r):
    return {"id": r[0], "title": r[1], "body": r[2], "updated": r[3]}


@router.get("/api/tasks")
def api_tasks(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        open_ = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=0 "
                          "ORDER BY prio, due IS NULL, due, created", (uid,)).fetchall()
        done = c.execute(f"SELECT {TASK_COLS} FROM task WHERE uid=? AND done=1 "
                         "ORDER BY done_at DESC LIMIT 50", (uid,)).fetchall()
    out = {"open": [_task_row(r) for r in open_], "done": [_task_row(r) for r in done]}
    q = request.query_params
    if q.get("with_sources") == "1" or q.get("sources") == "1":     # 2.5 (Paket 7, tasksrc1): zusammengefuehrte Liste
        from saga import tasksrc
        out.update(tasksrc.merged(uid, q))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/api/tasks")
async def api_task_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    if b.get("src") is not None:                 # 2.5 (Paket 7): neue Zeile in einer Aufgabenquelle statt Inbox
        from starlette.concurrency import run_in_threadpool
        from saga import tasksrc
        from saga.core import origin_of
        return await run_in_threadpool(tasksrc.add_row, uid, b["src"], b, origin_of(request))
    return task_add(uid, b)


def task_add(uid, b):
    """Neue Aufgabe (Weboberflaeche und /api/v1) -> JSONResponse {id}."""
    text = _text_in(b.get("text"))
    if not text:
        return _bad(t("leere Aufgabe"))
    try:
        due = _due(b.get("due"))
        prio = _prio(b.get("prio", 2))
        rep = _repeat(b.get("repeat"))
    except (ValueError, TypeError):
        return _bad(t("ungültige Angabe (Datum, Priorität oder Wiederholung)"))
    if rep and not due:                      # Wiederholung braucht ein Datum: ab heute
        due = _client_today(b).isoformat()
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM task WHERE uid=?", (uid,)).fetchone()[0] >= TASK_MAX_COUNT:
            return _bad(t("maximal {n} Aufgaben", n=TASK_MAX_COUNT), 409)
        cur = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                        (uid, text, due, prio, rep, due if rep else None, time.time()))
        bus.fire("task.created", c, uid=uid, task_id=cur.lastrowid)      # 2.5: Webhooks (saga.webhooks)
        return JSONResponse({"id": cur.lastrowid})


def _task_done(c, uid, tid, want, done, text, due, prio, rep, anchor, next_id, today):
    """Abhaken / wieder oeffnen - gemeinsam fuer Web, App und Widget. Liefert (next_id, Faelligkeit der Folgeaufgabe)."""
    if bool(want) == bool(done):
        return next_id, None
    nd = None
    if want:
        if rep:   # Wiederkehrend: Folgeaufgabe anlegen
            nd = next_due(due, rep, anchor, today)
            next_id = c.execute("INSERT INTO task (uid, text, due, prio, repeat, rep_anchor, created) VALUES (?,?,?,?,?,?,?)",
                                (uid, text, nd, prio, rep, anchor, time.time())).lastrowid
        c.execute("UPDATE task SET done=1, done_at=? WHERE id=? AND uid=?", (time.time(), tid, uid))
    else:
        if next_id:  # Haken zurueckgenommen: noch offene, automatisch erzeugte Folgeaufgabe entfernen
            c.execute("DELETE FROM task WHERE id=? AND uid=? AND done=0", (next_id, uid))
            next_id = None
        c.execute("UPDATE task SET done=0, done_at=NULL WHERE id=? AND uid=?", (tid, uid))
    return next_id, nd


@router.patch("/api/tasks/{tid}")
async def api_task_update(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    return task_update(uid, tid, b)


def task_update(uid, tid, b):
    """Aufgabe aendern/abhaken (Weboberflaeche und /api/v1) -> JSONResponse."""
    with _db() as c, c:
        row = c.execute("SELECT text, done, due, prio, repeat, rep_anchor, next_id FROM task WHERE id=? AND uid=?",
                        (tid, uid)).fetchone()
        if not row:
            return _bad("not found", 404)
        text, done, due, prio, rep, anchor, next_id = row
        out = {"ok": True}

        # --- Felder aendern
        try:
            if "text" in b:
                text = _text_in(b["text"])
                if not text:
                    return _bad(t("leere Aufgabe"))
            if "due" in b:
                due = _due(b["due"])
                if rep:
                    anchor = due
            if "prio" in b:
                prio = _prio(b["prio"])
            if "repeat" in b:
                new_rep = _repeat(b["repeat"])
                if new_rep and not due:
                    due = _client_today(b).isoformat()
                if new_rep != rep:
                    anchor = due if new_rep else None
                rep = new_rep
        except (ValueError, TypeError):
            return _bad(t("ungültige Angabe (Datum, Priorität oder Wiederholung)"))

        # --- Abhaken / wieder oeffnen
        if "done" in b:
            next_id, nd = _task_done(c, uid, tid, b["done"], done, text, due, prio, rep, anchor, next_id, _client_today(b))
            if nd:
                out["next_due"] = nd
            if b["done"] and not done:
                bus.fire("task.completed", c, uid=uid, task_id=tid)      # 2.5: Webhooks (saga.webhooks)

        c.execute("UPDATE task SET text=?, due=?, prio=?, repeat=?, rep_anchor=?, next_id=? WHERE id=? AND uid=?",
                  (text, due, prio, rep, anchor, next_id, tid, uid))
    return JSONResponse(out)


@router.delete("/api/tasks/{tid}")
def api_task_delete(tid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM task WHERE id=? AND uid=?", (tid, uid)).rowcount
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


@router.post("/api/tasks/clear-done")
def api_task_clear(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM task WHERE uid=? AND done=1", (uid,)).rowcount
    return JSONResponse({"deleted": n})
