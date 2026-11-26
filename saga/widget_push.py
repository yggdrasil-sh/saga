"""Widgets sofort aktuell (2.6, Bugs-DB 2188): stiller Push OHNE Inhalt an die Geraete einer Person, wenn sich etwas
aendert, das eines ihrer eingerichteten Homescreen-Widgets zeigt - auch an das Geraet, das die Aenderung ausgeloest hat.

- Welche Seiten als Widget eingerichtet sind, meldet die App je Geraete-Anmeldung (PUT /api/app/widgets {pages, today});
  gespeichert in user_kv 'app_widgets' = {"<device_id>": {"p": [seiten-ids], "t": 0|1, "at": zeit, "s": [geplante Pushes]}}.
  Nur eigene Seiten (wie GET /api/app/pages); Geraete ohne Anmeldung fallen beim naechsten Schreiben heraus.
- Ausloeser (bus): note.saved (Seite/Datenbank selbst oder Zeile row; dazu die Elternseite - Unterseiten-/Datenbank-Widget),
  notes.tree (Seitenbaum; "moved" = alle Seiten-Widgets, die alte Elternseite kennt das Ereignis nicht), task.created /
  task.completed / task.changed (Heute-Widget), Zeilen einer Aufgabenquelle (task_source: Heute-Widget aller Abonnenten).
  Gesammelt je Transaktion, kurz vor dem Commit nach Konto sortiert geplant (wie push._held_add, Bugs-DB 2364).
- Zustellung ueber push_queue mit der internen Art push.WIDGET_KIND an Abos mit caps "widgets" (apns, unifiedpush, stream -
  nie VoIP, ntfy oder Browser): APNs still (background, content-available, apns-expiration 0 - der direkte Weg und das
  Relais gleich), Android als Daten-Nachricht (update). Nutzlast ohne Titel, Namen oder Inhalte.
- Drosselung je Geraet: Abstand GAP (Standard 60 s), hoechstens HOUR Pushes je Stunde (Standard 6 - Apple drosselt stille
  Pushes und WidgetKit hat ein Tagesbudget fuers Neuladen); wartet schon ein Push, kommt kein zweiter (gebuendelt).
  Der erste kommt nach SETTLE s, damit ein Schwall Speichervorgaenge einer ist."""
import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga import bus, push
from saga.core import _bad, _body, _json_load, t

router = APIRouter()


def _env_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name) or default)))
    except ValueError:
        return default


KV = "app_widgets"
CAP = "widgets"
TYPES = ("apns", "unifiedpush", "stream")      # Abo-Arten, die einen stillen Widget-Push bekommen koennen
PAGES_MAX = 64                                 # Seiten je Geraet
GAP = _env_int("SAGA_WIDGET_PUSH_GAP", 60, 1, 3600)        # s zwischen zwei Widget-Pushes an ein Geraet
HOUR = _env_int("SAGA_WIDGET_PUSH_HOUR", 6, 1, 60)         # Widget-Pushes je Geraet und Stunde
SETTLE = min(3, GAP)                           # s bis zum ersten Push (Speichervorgaenge buendeln)
REG_MIN = 30                                   # Meldungen je Konto und Minute
TX = "widget_push"
_reg_rate = {}


def _local(uid):
    return isinstance(uid, str) and bool(uid) and not uid.startswith("@")      # "@name@host": Person anderer Instanz


def _load(c, uid, lock=False):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?" + (" FOR UPDATE" if lock else ""), (uid, KV)).fetchone()
    v = _json_load(r[0], {}) if r else {}
    return v if isinstance(v, dict) else {}


def _save(c, uid, reg):
    if reg:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, KV, json.dumps(reg, separators=(",", ":"))))
    else:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV))


def _prune(c, uid, reg):
    """Geraete ohne Geraete-Anmeldung entfernen -> True, wenn sich etwas geaendert hat."""
    if not reg:
        return False
    alive = {str(r[0]) for r in c.execute("SELECT id FROM device_login WHERE uid=?", (uid,)).fetchall()}
    gone = [k for k in reg if k not in alive]
    for k in gone:
        reg.pop(k, None)
    return bool(gone)


def _subs_of(c, uid, did):
    """Abos dieser Geraete-Anmeldung, die Widget-Pushes annehmen."""
    return [r[0] for r in c.execute("SELECT id, kind, caps FROM push_sub WHERE uid=? AND device_id=?", (uid, did)).fetchall()
            if r[1] in TYPES and push._has_cap(r[2], CAP)]


def _entry_out(c, uid, did, e):
    return {"pages": [int(x) for x in (e or {}).get("p") or []], "today": bool((e or {}).get("t")),
            "push": bool(_subs_of(c, uid, did))}


# =====================================================================================================
# Meldung der App
# =====================================================================================================
@router.get("/api/app/widgets")
def api_widgets_get(request: Request):
    """Was dieses Geraet zuletzt gemeldet hat, und ob es Widget-Pushes bekommen kann (Abo mit caps "widgets")."""
    uid, did = push._who(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not did:
        return _bad(t("Nur für Apps mit Geräte-Anmeldung."), 400)
    with _db() as c:
        return JSONResponse(_entry_out(c, uid, did, _load(c, uid).get(str(did))), headers={"Cache-Control": "no-store"})


@router.put("/api/app/widgets")
async def api_widgets_put(request: Request):
    """App meldet die Widgets auf dem Homescreen dieses Geraets: {pages: [seiten-ids], today: bool}. Nur eigene, nicht
    geloeschte Seiten werden gemerkt (andere still weggelassen); leer + today false = Eintrag weg."""
    uid, did = push._who(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not did:
        return _bad(t("Nur für Apps mit Geräte-Anmeldung."), 400)
    b = await _body(request)
    if not isinstance(b, dict):
        return _bad(t("ungültige Anfrage"))
    pages, today = b.get("pages", []), b.get("today", False)
    if (not isinstance(pages, list) or len(pages) > 4 * PAGES_MAX or not isinstance(today, bool)
            or any(not isinstance(x, int) or isinstance(x, bool) or not 0 < x < 2 ** 62 for x in pages)):
        return _bad(t("ungültige Angabe"))
    if not push._rate_ok(_reg_rate, uid, REG_MIN):
        return _bad(t("Zu viele Versuche – bitte kurz warten."), 429)
    want = list(dict.fromkeys(pages))
    with _db() as c, c:
        have = {r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND id = ANY(?) AND deleted_at IS NULL",
                                        (uid, want)).fetchall()} if want else set()
        keep = [x for x in want if x in have][:PAGES_MAX]
        reg = _load(c, uid, lock=True)
        _prune(c, uid, reg)
        if not keep and not today:
            reg.pop(str(did), None)
        else:
            e = reg.get(str(did)) if isinstance(reg.get(str(did)), dict) else {}
            e.update({"p": keep, "t": 1 if today else 0, "at": round(time.time(), 1)})
            reg[str(did)] = e
        _save(c, uid, reg)
        out = _entry_out(c, uid, did, reg.get(str(did)))
    return JSONResponse(dict(out, ok=True), headers={"Cache-Control": "no-store"})


# =====================================================================================================
# Ausloeser: sammeln je Transaktion, vor dem Commit planen
# =====================================================================================================
def mark(c, uid, ids=(), today=False, every=False):
    """Aenderung fuer uid vormerken: Seiten ids (Eltern rechnet _flush dazu), Heute-Widget, alle Seiten-Widgets."""
    if not _local(uid):
        return
    reg = getattr(c, "before_commit", None)
    pend = c.tx.get(TX) if reg is not None else {}
    if pend is None:
        pend = c.tx[TX] = {}
        reg(lambda: _flush(c, pend))
    e = pend.setdefault(uid, {"ids": set(), "today": False, "all": False})
    e["ids"].update(int(i) for i in ids if isinstance(i, int) and not isinstance(i, bool))
    e["today"] = e["today"] or bool(today)
    e["all"] = e["all"] or bool(every)
    if reg is None:                       # Verbindung ohne Haken (Werkzeuge): sofort
        _flush(c, pend)


def _flush(c, pend):
    """Laeuft beim Commit ausserhalb des Savepoints von bus.fire - darum eigener Savepoint: ein Fehler kostet nur den
    Widget-Push, nie die eigentliche Aenderung."""
    try:
        with c.savepoint():
            for uid in sorted(pend):
                _schedule(c, uid, pend[uid])
    except Exception as ex:  # noqa: BLE001 - ein Widget-Push darf Speichern nie verhindern
        print(f"widget_push: {type(ex).__name__}: {ex}", flush=True)
    pend.clear()


def plan_at(sent, now, gap=None, hour=None, settle=None):
    """Zeitpunkt des naechsten Widget-Pushes an ein Geraet -> (zeitpunkt, neue Liste der geplanten Zeitpunkte).
    sent: bisher geplante Zeitpunkte (auch kuenftige). Abstand gap, hoechstens hour je 3600 s, fruehestens now + settle."""
    gap = GAP if gap is None else gap
    hour = HOUR if hour is None else hour
    settle = SETTLE if settle is None else settle
    recent = sorted(float(x) for x in sent or () if isinstance(x, (int, float)) and not isinstance(x, bool) and now - x < 3600)
    at = now + settle
    if recent:
        at = max(at, recent[-1] + gap)
    if len(recent) >= hour:
        at = max(at, recent[-hour] + 3600)
    return round(at, 3), (recent + [round(at, 3)])[-hour:]


def _schedule(c, uid, e):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=? FOR UPDATE", (uid, KV)).fetchone()
    reg = _json_load(r[0], {}) if r else {}
    if not isinstance(reg, dict) or not reg:
        return
    ids = set(e["ids"])
    if ids:
        ids |= {p for (p,) in c.execute("SELECT parent_id FROM note WHERE id = ANY(?) AND uid=?", (list(ids), uid)).fetchall() if p}
    devs = {k for k, d in reg.items() if isinstance(d, dict) and (
        (e["today"] and d.get("t")) or (d.get("p") and (e["all"] or ids & set(d.get("p") or []))))}
    if not devs:
        return
    srv = push.server()
    info = auth.user_info(uid)
    if not srv["enabled"] or info is None:
        return
    sids = push._route(c, push._subs(c, uid, info.get("epoch"), srv), push.WIDGET_KIND)
    if not sids:
        return
    by_dev = {}
    for sid, kind, caps, dev in c.execute(f"SELECT id, kind, caps, device_id FROM push_sub WHERE id IN ({','.join('?' * len(sids))})",
                                          tuple(sids)).fetchall():
        if kind in TYPES and push._has_cap(caps, CAP) and dev is not None and str(dev) in devs:
            by_dev.setdefault(str(dev), []).append(sid)
    if not by_dev or c.execute("SELECT COUNT(*) FROM push_queue WHERE uid=?", (uid,)).fetchone()[0] >= push.QUEUE_MAX:
        return
    now, changed = time.time(), False
    for dev in sorted(by_dev):
        subs = by_dev[dev]
        if c.execute("SELECT 1 FROM push_queue WHERE sub_id = ANY(?) AND kind=? AND tries=0 LIMIT 1", (subs, push.WIDGET_KIND)).fetchone():
            continue                      # wartet schon - der kommt mit dem neuesten Stand (die App holt ihn selbst)
        at, sent = plan_at(reg[dev].get("s"), now)
        for sid in subs:
            c.execute("INSERT INTO push_queue (uid, sub_id, kind, ref, actor, data, created, next_at) VALUES (?,?,?,?,?,?,?,?)",
                      (uid, sid, push.WIDGET_KIND, None, None, "{}", at, at))
        reg[dev]["s"] = sent
        changed = True
    if changed:
        _save(c, uid, reg)
        c.after_commit(push.wake)


def _on_note_saved(c, note_id=None, owner=None, row=None, **_):
    if not _local(owner) or note_id is None:
        return
    mark(c, owner, [note_id] + ([row] if row is not None else []))
    if row is not None:                   # Zeile einer Aufgabenquelle: Heute-Widget aller, die sie abonniert haben
        for (u,) in c.execute("SELECT uid FROM task_source WHERE db_id=?", (note_id,)).fetchall():
            mark(c, u, today=True)


def _on_tree(c, owner=None, ids=None, op=None, **_):
    if _local(owner):
        mark(c, owner, ids or (), every=op == "moved")


def _on_task(c, uid=None, **_):
    mark(c, uid, today=True)


bus.on("note.saved", _on_note_saved)
bus.on("notes.tree", _on_tree)
bus.on("task.created", _on_task)
bus.on("task.completed", _on_task)
bus.on("task.changed", _on_task)
