"""Rueckgaengig fuer Auftraege der Kollegin (Saga 2.6, Bugs-DB 2267 K-B, Konzept K5, UX R4): Vorher-Stand je Wirkung in
job_change, Konfliktpruefung (rev bzw. Fingerabdruck/ETag) und Ruecknahme (compensate) fuer die eigenen Werkzeuge.

Ablauf (Runner ai.task, saga/ai_task.py): vor einem schreibenden Schritt before(uid, spec, args) - Stand der Ziele, die
schon da sind; nach dem Werkzeug record(...) - die Stempel des Schritts (job_change, saga.jobs) bekommen before (was
compensate braucht), after (Kurzinfo) und rev_after (rev der Seite, Fingerabdruck der Zeile/Aufgabe, ETag des Termins).
Termine haben keinen Datenbank-Stempel (CalDAV): record legt fuer sie selbst eine Zeile event:<kennung> an.

Arten (before.op): note (Seite/Inhalt/Titel/Kaestchen: alter Titel, Inhalt, Format), props (Zeile: alte Werte der
geaenderten Eigenschaften, Titel, Symbol), trash (neue Seite/Zeile -> Papierkorb, nur loeschbar), task_delete (neue
Aufgabe -> loeschen), task_open (abgehakt -> wieder offen, ohne Serie), event_delete (neuer Termin -> loeschen, If-Match),
none (Nebenstempel desselben Ziels, z. B. note:<id> neben row:<id>). Ohne before (Wiederaufnahme nach Absturz, Ziel
zwischen Lesen und Schreiben geaendert: gap) laesst sich nur noch die alte Fassung ansehen.

Konflikt: Ziel seit der Wirkung geaendert (rev/Fingerabdruck/ETag anders) -> jobs.UndoConflict mit version_url =
/api/ai/jobs/<auftrag>/changes/<aenderung>/before (alte Fassung holen, saga/ai_jobs.py). Nie still ueberschreiben.
conflicts() prueft das vor dem Anstossen (409 changed in der API), compensate() noch einmal beim Zuruecknehmen.
Schon zurueckgenommen (Stand = Vorher-Stand, Zeile weg): gilt als erledigt (Wiederholung nach Absturz).

Klartext (before/after) loescht jobs.cleanup 30 Tage nach Ende. Tests: tests/kollegin_plan.py (Rueckgaengig-Teil)."""
import hashlib
import json
import time
import types

from db import connect as _db
from saga import jobs
from saga.core import t

UNDO_OPS = ("note", "props", "trash", "task_delete", "task_open", "event_delete", "none")
RESTORE_TOOLS = ("append_to_page", "update_page_title", "set_checkbox")


def _fp(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]


def _jl(s, d):
    try:
        v = json.loads(s) if isinstance(s, str) and s else s
    except ValueError:
        return d
    return v if isinstance(v, type(d)) else d


def _note(c, nid):
    return c.execute("SELECT uid, title, body, format, rev, icon, props, deleted_at FROM note WHERE id=?", (nid,)).fetchone()


def _task(c, tid):
    return c.execute("SELECT uid, text, due, prio, done, next_id, repeat FROM task WHERE id=?", (tid,)).fetchone()


def _task_fp(r):
    return _fp([r[1], r[2], r[3], r[4], r[5]])


def _row_fp(r, keys):
    props = _jl(r[6], {})
    return _fp([r[1], r[5], {k: props.get(k) for k in sorted(keys)}])


def before(uid, spec, args):
    """Vor dem Schreiben: Stand der vorhandenen Ziele -> {ref: daten} (nur Kennungen, die das Werkzeug aendert)."""
    if not spec.write:
        return {}
    out = {}
    try:
        refs = spec.refs(args or {})
    except (ValueError, TypeError):
        return {}
    with _db() as c:
        for ref in refs:
            kind, _, v = str(ref).partition(":")
            if kind == "note" and v.isdigit() and spec.name in RESTORE_TOOLS + ("update_row",):
                r = _note(c, int(v))
                if r and r[7] is None:
                    out[f"note:{v}"] = {"title": r[1], "body": r[2], "format": r[3], "rev": r[4], "icon": r[5], "props": _jl(r[6], {})}
            elif kind == "task" and v.isdigit():
                r = _task(c, int(v))
                if r:
                    out[ref] = {"done": r[4], "fp": _task_fp(r)}
    return out


def _upd(c, cid, b, after, rev_after):
    c.execute("UPDATE job_change SET before=CAST(? AS JSONB), after=CAST(? AS JSONB), rev_after=? WHERE id=?",
              (json.dumps(b, ensure_ascii=False), json.dumps(after, ensure_ascii=False), rev_after, cid))


def record(job_id, n, idem, attempt, spec, args, snap, out):
    """Nach dem Schreiben: Vorher-Stand, Kurzinfo und rev_after an die Stempel des Schritts (eine Transaktion)."""
    if not spec.write:
        return
    refs = set()
    try:
        refs = {str(r) for r in spec.refs(args or {})}
    except (ValueError, TypeError):
        pass
    with _db() as c, c:
        if spec.name == "create_event":
            ev = _jl(out if isinstance(out, str) else None, {}).get("event")
            if isinstance(ev, str) and ev:
                etag = _event_etag(ev)
                c.execute("INSERT INTO job_change (job_id, step_n, idem_key, attempt, target, at, before, after, rev_after) "
                          "VALUES (?, ?, ?, ?, ?, ?, CAST(? AS JSONB), CAST(? AS JSONB), ?) ON CONFLICT (idem_key, target) DO NOTHING",
                          (job_id, n, idem, attempt, f"event:{ev[:400]}", jobs.now(), json.dumps({"op": "event_delete"}),
                           json.dumps({"summary": (args or {}).get("summary") or "", "start": (args or {}).get("start") or ""},
                                      ensure_ascii=False), etag))
            return
        rows = c.execute("SELECT id, target FROM job_change WHERE idem_key=? AND before IS NULL ORDER BY id", (idem,)).fetchall()
        primary = {}
        for cid, target in rows:
            kind, _, v = target.partition(":")
            if not v.isdigit():
                _upd(c, cid, {"op": "none"}, {}, None)
                continue
            i = int(v)
            if kind == "row" or (kind == "note" and spec.name == "update_row"):
                if spec.name == "update_row" and i != (args or {}).get("id"):   # z. B. die Datenbank selbst (Ansicht/Zaehler)
                    _upd(c, cid, {"op": "none"}, {}, None)
                    continue
                if i in primary:                 # Nebenstempel (note:<id> neben row:<id>)
                    _upd(c, cid, {"op": "none", "same": primary[i]}, {}, None)
                    continue
                primary[i] = cid
                r = _note(c, i)
                old = snap.get(f"note:{i}")
                if not r or old is None:
                    _upd(c, cid, {"op": "props", "gap": True}, {}, None)
                    continue
                now_p = _jl(r[6], {})
                keys = sorted({k for k in set(now_p) | set(old["props"]) if now_p.get(k) != old["props"].get(k)})
                b = {"op": "props", "props": {k: old["props"].get(k) for k in keys}, "title": old["title"] if old["title"] != r[1] else None,
                     "icon": old["icon"] if old["icon"] != r[5] else None, "keys": keys, "title_changed": old["title"] != r[1],
                     "icon_changed": old["icon"] != r[5]}
                _upd(c, cid, b, {"title": r[1], "keys": keys}, _row_fp(r, keys))
            elif kind == "note":
                r = _note(c, i)
                if not r:
                    _upd(c, cid, {"op": "none"}, {}, None)
                    continue
                if target in refs and spec.name in RESTORE_TOOLS:
                    old = snap.get(target)
                    gap = old is None or r[4] != old["rev"] + 1
                    b = {"op": "note", "title": old["title"], "body": old["body"], "format": old["format"], "gap": gap} if old \
                        else {"op": "note", "gap": True}
                    _upd(c, cid, b, {"title": r[1]}, str(r[4]))
                elif target not in refs and spec.name in ("create_page", "create_row"):
                    _upd(c, cid, {"op": "trash"}, {"title": r[1]}, str(r[4]))
                else:
                    _upd(c, cid, {"op": "none"}, {}, None)
            elif kind == "task":
                r = _task(c, i)
                if not r:
                    _upd(c, cid, {"op": "none"}, {}, None)
                elif spec.name == "create_task":
                    _upd(c, cid, {"op": "task_delete"}, {"text": r[1], "due": r[2]}, _task_fp(r))
                elif spec.name == "complete_task":
                    _upd(c, cid, {"op": "task_open", "gap": bool(r[5]) or bool(r[6])}, {"text": r[1]}, _task_fp(r))
                else:
                    _upd(c, cid, {"op": "none"}, {}, None)
            else:
                _upd(c, cid, {"op": "none"}, {}, None)


def _event_etag(ev):
    import base64
    try:
        return str(json.loads(base64.urlsafe_b64decode(ev + "=" * (-len(ev) % 4)).decode())[3])[:200]
    except (ValueError, TypeError, IndexError, UnicodeDecodeError):
        return None


def version_url(job_id, cid):
    return f"/api/ai/jobs/{int(job_id)}/changes/{int(cid)}/before"


def _state(c, cid):
    return c.execute("SELECT target, before, rev_after, undone_at, job_id FROM job_change WHERE id=?", (cid,)).fetchone()


def check_one(c, job_uid, row):
    """(target, before, rev_after) -> None (zuruecknehmbar), 'done' (schon wie vorher) oder Grund ('changed', 'gone',
    'no_undo'). Nur Lesen."""
    target, b, rev_after = row[0], row[1] if isinstance(row[1], dict) else {}, row[2]
    op = b.get("op")
    kind, _, v = target.partition(":")
    if op == "none":
        return "skip"
    if op is None:                               # keine Aufzeichnung (Wiederaufnahme nach Absturz): nicht still uebergehen
        return "no_undo"
    if b.get("gap"):
        return "changed"
    if op == "note":
        r = _note(c, int(v))
        if not r or r[7] is not None:
            return "gone"
        if (r[1], r[2], r[3]) == (b.get("title"), b.get("body"), b.get("format")):
            return "done"
        return None if str(r[4]) == str(rev_after) else "changed"
    if op == "props":
        r = _note(c, int(v))
        if not r or r[7] is not None:
            return "gone"
        return None if _row_fp(r, b.get("keys") or []) == rev_after else "changed"
    if op == "trash":
        r = _note(c, int(v))
        if not r or r[7] is not None:
            return "done"
        return None if str(r[4]) == str(rev_after) else "changed"
    if op in ("task_delete", "task_open"):
        r = _task(c, int(v))
        if not r:
            return "done" if op == "task_delete" else "gone"
        if op == "task_open" and not r[4]:
            return "done"
        return None if _task_fp(r) == rev_after else "changed"
    if op == "event_delete":
        return None                              # ohne Netz nicht pruefbar: das If-Match beim Loeschen entscheidet
    return "no_undo"


def conflicts(job_id, uid):
    """Vor dem Anstossen (API): offene Aenderungen, die sich nicht mehr zuruecknehmen lassen -> [{id, target, why,
    version_url}] (leer = alles geht)."""
    out = []
    with _db() as c:
        for cid, target, b, rev_after in c.execute("SELECT id, target, before, rev_after FROM job_change WHERE job_id=? AND undone_at IS NULL "
                                                   "ORDER BY id", (job_id,)).fetchall():
            why = check_one(c, uid, (target, b, rev_after))
            if why in ("changed", "gone", "no_undo"):
                out.append({"id": cid, "target": target, "why": why, "version_url": version_url(job_id, cid)})
    return out


def compensate(c, job, change):
    """saga.jobs-Ruecknahme einer Wirkung (Transaktion c fuer Aufgaben; Seiten/Zeilen/Termine ueber die normalen Wege
    mit eigener Transaktion). Konflikt -> jobs.UndoConflict."""
    r = _state(c, change["id"])
    if not r:
        return
    target, b = r[0], r[1] if isinstance(r[1], dict) else {}
    why = check_one(c, job["uid"], r)
    if why in ("skip", "done"):
        return
    if why:
        raise jobs.UndoConflict(target, version_url(job["id"], change["id"]), why)
    kind, _, v = target.partition(":")
    op, uid = b.get("op"), job["uid"]
    if op == "note":
        _restore_note(uid, int(v), b, r[2], target, job["id"], change["id"])
    elif op == "props":
        _restore_props(uid, int(v), b, target, job["id"], change["id"])
    elif op == "trash":
        from saga.pages import note_trash
        res = note_trash(uid, int(v))
        if getattr(res, "status_code", 200) != 200:
            raise jobs.UndoConflict(target, version_url(job["id"], change["id"]), "no_access")
    elif op == "task_delete":
        c.execute("DELETE FROM task WHERE id=? AND uid=?", (int(v), uid))
    elif op == "task_open":
        c.execute("UPDATE task SET done=0, done_at=NULL WHERE id=? AND uid=?", (int(v), uid))
    elif op == "event_delete":
        from saga import api_calendar
        from saga.api_v1 import Fail
        try:
            api_calendar.event_delete(types.SimpleNamespace(uid=uid), {"event": target[6:]})
        except Fail as f:
            if f.status in (404,):
                return
            raise jobs.UndoConflict(target, None, "changed" if f.status == 409 else "no_access")


def _restore_note(uid, nid, b, rev_after, target, job_id, cid):
    """Alten Stand ueber die Versionen wiederherstellen (wie "Version wiederherstellen" im Web): erst den Vorher-Stand
    als Version ablegen, dann zurueckholen - der jetzige Stand bleibt im Verlauf."""
    from saga import page_tools
    from saga.notes import _insert_version
    with _db() as c, c:
        r = c.execute("SELECT uid, rev, updated_by FROM note WHERE id=? AND deleted_at IS NULL FOR UPDATE", (nid,)).fetchone()
        if not r or str(r[1]) != str(rev_after):
            raise jobs.UndoConflict(target, version_url(job_id, cid), "changed")
        _insert_version(c, r[0], nid, (b.get("title"), b.get("body"), b.get("format")), time.time(), uid)
        vid = c.execute("SELECT MAX(id) FROM note_version WHERE uid=? AND note_id=?", (r[0], nid)).fetchone()[0]
    res = page_tools.version_restore(uid, nid, vid)
    if getattr(res, "status_code", 200) != 200:
        raise jobs.UndoConflict(target, version_url(job_id, cid), "no_access")


def _restore_props(uid, rid, b, target, job_id, cid):
    from saga.pages import _note_update, _Reply
    req = {}
    if b.get("keys"):
        req["props"] = {k: b["props"].get(k) for k in b["keys"]}
    if b.get("title_changed"):
        req["title"] = b.get("title") or ""
    if b.get("icon_changed"):
        req["icon"] = b.get("icon") or ""
    if not req:
        return
    try:
        res = _note_update(uid, rid, req, "api")
    except _Reply as rp:
        res = rp.resp
    if getattr(res, "status_code", 200) != 200:
        raise jobs.UndoConflict(target, version_url(job_id, cid), "changed" if getattr(res, "status_code", 0) == 409 else "no_access")


def undo_one(job_id, uid, cid):
    """Eine einzelne Aenderung zuruecknehmen (Detail: Rueckgaengig je Zeile) -> (status, antwort). Sind danach alle
    Aenderungen zurueck, wird der Auftrag zurueckgenommen."""
    from saga.actor import Actor, acting
    with _db() as c:
        j = c.execute("SELECT state, kind, undo_at FROM job WHERE id=? AND uid=?", (job_id, uid)).fetchone()
        r = c.execute("SELECT id, step_n, target, undone_at FROM job_change WHERE id=? AND job_id=?", (cid, job_id)).fetchone() if j else None
    if not j or not r:
        return 404, {"error": "not found"}
    if j[0] != "erledigt" or j[2] is not None:
        return 409, {"error": t("Das lässt sich gerade nicht zurücknehmen."), "code": "state", "state": j[0]}
    if r[3] is not None:
        return 200, {"ok": True, "already": True}
    jv = {"id": job_id, "uid": uid, "kind": j[1]}
    try:
        with _db() as c, c:
            if c.execute("SELECT undone_at FROM job_change WHERE id=? FOR UPDATE", (cid,)).fetchone()[0] is not None:
                return 200, {"ok": True, "already": True}
            with acting(Actor("service", "svc:kollegin", on_behalf_of=uid, job_id=job_id)):
                compensate(c, jv, {"id": cid, "step": r[1], "target": r[2]})
            c.execute("UPDATE job_change SET undone_at=? WHERE id=?", (jobs.now(), cid))
            jobs._event(c, job_id, "undo", jobs.now(), change=cid, target=r[2])
            left = c.execute("SELECT COUNT(*) FROM job_change WHERE job_id=? AND undone_at IS NULL AND id<>? AND "
                             "COALESCE(before->>'op', 'x') <> 'none'", (job_id, cid)).fetchone()[0]
            if not left:
                jobs._to(c, job_id, "erledigt", "zurueckgenommen", jobs.now(), reason="person")
            else:
                jobs._live(c, job_id)
    except jobs.UndoConflict as ex:
        return 409, {"error": t("Das wurde seitdem geändert."), "code": "changed", "target": ex.target, "why": ex.why,
                     "version_url": ex.version_url}
    return 200, {"ok": True}


def before_view(job_id, uid, cid):
    """'Alte Fassung holen': Vorher-Stand einer Aenderung als Text -> (status, antwort). Nur die Besitzerin des Auftrags
    und nur, solange der Klartext aufbewahrt wird (30 Tage nach Ende)."""
    with _db() as c:
        r = c.execute("SELECT ch.target, ch.before FROM job_change ch JOIN job j ON j.id=ch.job_id WHERE ch.id=? AND ch.job_id=? AND j.uid=?",
                      (cid, job_id, uid)).fetchone()
    if not r:
        return 404, {"error": "not found"}
    b = r[1] if isinstance(r[1], dict) else {}
    if b.get("op") == "note" and b.get("body") is not None:
        from saga.notes import _note_text
        return 200, {"target": r[0], "title": b.get("title") or "", "text": _note_text(b.get("format"), b.get("body"), True)[:20000]}
    if b.get("op") == "props":
        return 200, {"target": r[0], "title": b.get("title") or "", "text": "\n".join(f"{k}: {v}" for k, v in (b.get("props") or {}).items())}
    return 404, {"error": t("Die alte Fassung ist nicht mehr gespeichert.")}


def label(target, after, labels):
    """Zeile einer Aenderung in der Rueckmeldung (Systemtext)."""
    a = after if isinstance(after, dict) else {}
    kind = target.partition(":")[0]
    if kind == "task":
        return t("Aufgabe „{text}“", text=a.get("text") or "")
    if kind == "event":
        return t("Termin „{text}“", text=a.get("summary") or "")
    if kind in ("note", "row"):
        return t("Seite „{title}“", title=a.get("title") or labels.get(target) or t("Unbenannt"))
    return target


def ops_of(rows):
    return [r for r in rows if isinstance(r.get("before"), dict) and r["before"].get("op") not in (None, "none")]

