"""Aufgabenquellen (2.5, Paket 7 Teil A): Datenbank-Zeilen in der persoenlichen Aufgabenliste.

Eine Datenbank wird zur Aufgabenquelle, wenn ihr Schema eine Zuordnung "tasks" traegt (saga.notes.clean_tasks_map:
done = Status/Auswahl/Haekchen, due, prio, assignee = Personen-Eigenschaft). Jede Person abonniert Quellen selbst
(Tabelle task_source, hoechstens SRC_MAX): eigene Datenbanken standardmaessig mit allen offenen Zeilen ('all'), geteilte
nur mit den ihr zugewiesenen ('assigned', ueber row_person) - je Quelle umschaltbar (P7-E1). Erledigt erkennt der Server
an der Status-Gruppe "Erledigt", sonst am Haekchen bzw. an done_values der Auswahl (P7-E2).

Rechte: JEDES Lesen prueft die Rolle an der Datenbank neu (sharing.Access, ab 'view'); ein Abo ohne Zugriff liefert
nichts und wird dabei entfernt. Abhaken/Anlegen laeuft ueber denselben Weg wie das Props-PATCH der Seite
(pages._note_update / note_create / databases.note_duplicate - gleiche Rechtepruefung, Live, Versionen, db.assigned).

Ausgabe (GET /api/tasks?with_sources=1, Vertrag docs/APP-API.md):
  items   - Inbox-Aufgaben und offene Quell-Zeilen, fertig sortiert (ueberfaellig, heute, mit Datum, ohne Datum; je
            Prioritaet). Personen nur als Kennung (Handle '~…' bzw. Prinzipal), nie Anmeldenamen.
  sources - abonnierte Quellen mit Zuordnung (map) - Apps koennen damit offline ueber das Props-PATCH abhaken.
  people  - {kennung: {name, avatar, me, ...}} (saga.people.label_map).
"diese Woche erledigt": Tabelle row_done (Zeitpunkt des Uebergangs offen -> erledigt, gepflegt beim Speichern).
Taegliche Zusammenfassung (P7-E4): Mitteilung task.due "heute faellig / ueberfaellig", je Konto ein (Standard aus), Uhrzeit
und Zeitzone je Konto; ueber saga.notify (Glocke) und saga.push.
Foederierte Quellen (negative IDs) kommen spaeter (P7-E5) - bis dahin 400."""
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, notify, people
from saga.core import RESTORE_TEST, TZ, _api_guard, _bad, _body, _json_load, t
from saga.notes import clean_tasks_map
from saga.sharing import RANK, Access, is_remote

router = APIRouter()

SRC_MAX = 20                       # Quellen je Konto
OPEN_MAX = 500                     # offene Zeilen je Quelle (darueber: truncated)
SCAN_MAX = 5000                    # so viele Zeilen je Quelle werden hoechstens gelesen
SCOPES = ("all", "assigned")
TEXT_MAX = 500
DIGEST_KIND = "task.due"
DIGEST_DEF = {"on": False, "time": "07:30", "tz": ""}
TICK = max(5.0, float(os.environ.get("SAGA_TASKDUE_TICK", "60")))


# --- Zuordnung -------------------------------------------------------------------------------------------------------
def _props(schema):
    return [p for p in (schema or {}).get("props") or [] if isinstance(p, dict) and p.get("id")]


def task_map(schema):
    """Gueltige Zuordnung einer Datenbank (oder None)."""
    m = (schema or {}).get("tasks")
    return clean_tasks_map(m, _props(schema)) if isinstance(m, dict) else None


class Mapping:
    """Zuordnung mit den Eigenschaften - erledigt? Wert fuer erledigt/offen, Faelligkeit, Prioritaet."""

    def __init__(self, schema, tm):
        self.tm = tm
        by = {p["id"]: p for p in _props(schema)}
        self.dp = by.get(tm["done"])
        self.due = by.get(tm.get("due")) if tm.get("due") else None
        self.prio = by.get(tm.get("prio")) if tm.get("prio") else None
        self.assignee = by.get(tm.get("assignee")) if tm.get("assignee") else None
        self.kind = "checkbox" if self.dp["type"] == "checkbox" else ("status" if self.dp.get("status") else "select")
        opts = [o for o in self.dp.get("options") or []] if self.kind != "checkbox" else []
        if self.kind == "status":
            self.done_ids = [o["id"] for o in opts if o.get("group") == "done"]
            todo = [o["id"] for o in opts if o.get("group", "todo") == "todo"]
        elif self.kind == "select":
            self.done_ids = list(tm.get("done_values") or [])
            todo = [o["id"] for o in opts if o["id"] not in self.done_ids]
        else:
            self.done_ids, todo = [], []
        self.done_value = True if self.kind == "checkbox" else (self.done_ids[0] if self.done_ids else None)
        self.open_value = None if self.kind == "checkbox" else (tm.get("open_value") or (todo[0] if todo else None))
        self.prio_of, self.prio_val = {}, {}
        if self.prio is not None:
            po = [o["id"] for o in self.prio.get("options") or []]
            pm = tm.get("prio_map") or {}
            for i, oid in enumerate(po):       # ohne Angabe: Reihenfolge der Optionen (erste = hoch, letzte = niedrig)
                self.prio_of[oid] = pm.get(oid) or (2 if len(po) < 2 else 1 + round(2 * i / (len(po) - 1)))
            for oid, n in self.prio_of.items():
                self.prio_val.setdefault(n, oid)

    def is_done(self, pv):
        v = (pv or {}).get(self.dp["id"])
        if self.kind == "checkbox":
            return v is True
        return v in self.done_ids

    def public(self):
        """Fuer Apps: welche Eigenschaft, welche Werte (offline abhaken ueber PATCH /api/notes/{zeile} props)."""
        out = {"done": self.dp["id"], "done_type": self.kind, "done_value": self.done_value, "open_value": self.open_value}
        if self.due is not None:
            out["due"] = self.due["id"]
        if self.prio is not None:
            out["prio"] = self.prio["id"]
            out["prio_values"] = {str(k): v for k, v in sorted(self.prio_val.items())}
        if self.assignee is not None:
            out["assignee"] = self.assignee["id"]
        return out


def suggest(schema):
    """Vorschlag einer Zuordnung aus den Typen (Dialog "Als Aufgabenquelle verwenden"): Status vor Haekchen vor Auswahl mit
    einer Option, die nach "erledigt" klingt; erstes Datum; Auswahl mit Namen wie Prioritaet; erste Personen-Eigenschaft."""
    ps = _props(schema)
    out = {}
    st = next((p for p in ps if p["type"] == "select" and p.get("status")), None)
    cb = next((p for p in ps if p["type"] == "checkbox"), None)
    words = ("erledigt", "fertig", "done", "complete", "hecho", "listo", "готово", "выполн", "เสร็จ")
    if st is not None:
        out["done"] = st["id"]
    elif cb is not None:
        out["done"] = cb["id"]
    else:
        for p in ps:
            if p["type"] != "select":
                continue
            dv = [o["id"] for o in p.get("options") or [] if any(w in (str(o.get("name", "")) + " " + o["id"]).lower() for w in words)]
            if dv and len(dv) < len(p.get("options") or []):
                out.update(done=p["id"], done_values=dv)
                break
    dp = next((p for p in ps if p["type"] == "date"), None)
    if dp is not None:
        out["due"] = dp["id"]
    pw = ("prio", "wichtig", "importan", "приор", "ความสำคัญ")
    pp = next((p for p in ps if p["type"] == "select" and p["id"] != out.get("done")
               and any(w in (str(p.get("name", "")) + " " + p["id"]).lower() for w in pw)), None)
    if pp is not None:
        out["prio"] = pp["id"]
    ap = next((p for p in ps if p["type"] == "person"), None)
    if ap is not None:
        out["assignee"] = ap["id"]
    return clean_tasks_map(out, ps)


def looks_like_tasks(schema):
    """Hinweis fuer bestehende Datenbanken (z. B. alte Instanzen der Vorlage "Aufgaben & Projekte"): keine Zuordnung, aber
    Status/Auswahl mit "erledigt" bzw. Haekchen und ein Datum."""
    if task_map(schema):
        return False
    s = suggest(schema)
    return bool(s and s.get("due"))


# --- Faelligkeit -----------------------------------------------------------------------------------------------------
def _one(s, tz):
    if len(s) == 10:
        return date.fromisoformat(s).isoformat(), None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    dt = dt.astimezone(tz)
    return dt.date().isoformat(), dt.strftime("%H:%M")


def due_parts(v, tz):
    """Datumswert (Tag, Zeitpunkt, Zeitraum) -> (starttag, uhrzeit|None, endtag) in tz oder None."""
    if not isinstance(v, str) or not 10 <= len(v) <= 60:
        return None
    ps = v.split("/")
    try:
        a = _one(ps[0], tz)
        b = _one(ps[-1], tz) if len(ps) == 2 else a
    except (ValueError, TypeError, OverflowError):
        return None
    return a[0], a[1], b[0]


def _sort_key(it, today):
    d = it.get("due")
    bucket = 0 if it.get("overdue") else 1 if d == today else 2 if d else 3
    return (bucket, d or "", it.get("time") or "", it.get("prio") or 2, (it.get("text") or "").lower(), it["key"])


# --- Abos ------------------------------------------------------------------------------------------------------------
def _db_row(c, acc, db_id):
    """-> (eigentuemer, rolle, titel, icon, schema, aktiv) oder None (kein Zugriff / keine Datenbank)."""
    role = acc.role(db_id)
    if not role:
        return None
    owner = acc.owner(db_id)
    r = c.execute("SELECT title, icon, schema, deleted_at, archived, kind, is_template FROM note WHERE id=? AND uid=?",
                  (db_id, owner)).fetchone()
    if not r or r[5] != "database" or r[6]:
        return None
    return owner, role, r[0] or "", r[1] or "", _json_load(r[2], {"props": []}), r[3] is None and r[4] is None


def subs(c, uid):
    return c.execute("SELECT db_id, scope FROM task_source WHERE uid=? ORDER BY added, db_id LIMIT ?", (uid, SRC_MAX)).fetchall()


def _rows(c, owner, db_id, m, scope, me):
    """Aktive Zeilen der Quelle (nicht geloescht/archiviert, keine Vorlagen); 'assigned': nur mit me in der Personen-Spalte."""
    sql = "SELECT id, title, props, created FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND archived IS NULL AND is_template=0"
    args = [owner, db_id]
    if scope == "assigned":
        if m.assignee is None:
            return []
        sql += " AND id IN (SELECT note_id FROM row_person WHERE db_id=? AND pid=? AND person=?)"
        args += [db_id, m.assignee["id"], me]
    return c.execute(sql + " ORDER BY sort NULLS FIRST, created, id LIMIT ?", (*args, SCAN_MAX)).fetchall()


def collect(c, uid, today, tz, acc=None, open_only=True, with_inbox=True, clean=True):
    """Zusammengefuehrte Liste fuer uid -> (items, sources, people). today: date in der Zeitzone der Anfrage.
    clean: Abos ohne Zugriff entfernen (nur in einer schreibenden Transaktion)."""
    acc = acc or Access(c, uid)
    t0 = today.isoformat()
    items, sources, canons, gone = [], [], [], []
    if with_inbox:
        for tid, text, due, prio, rep in c.execute("SELECT id, text, due, prio, repeat FROM task WHERE uid=? AND done=0 "
                                                   "ORDER BY prio, due IS NULL, due, created", (uid,)).fetchall():
            items.append({"key": f"t:{tid}", "kind": "task", "id": tid, "text": text, "due": due, "prio": prio, "repeat": rep,
                          "done": False, "overdue": bool(due and due < t0)})
    for db_id, scope in subs(c, uid) if not is_remote(uid) else []:
        d = _db_row(c, acc, db_id)
        if d is None:
            gone.append(db_id)
            continue
        owner, role, title, icon, schema, active = d
        tm = task_map(schema)
        src = {"db": db_id, "title": title, "icon": icon, "role": role, "scope": scope, "own": owner == uid,
               "count": 0, "truncated": False, "map": None}
        sources.append(src)
        if not tm or not active:
            src["inactive" if tm else "unmapped"] = True
            continue
        m = Mapping(schema, tm)
        src["map"] = m.public()
        if scope == "assigned" and m.assignee is None:
            src["no_assignee"] = True
        edit = RANK.get(role, 0) >= RANK["edit"]
        n = 0
        for rid, rtitle, raw, _created in _rows(c, owner, db_id, m, scope, uid):
            pv = _json_load(raw, {})
            if not isinstance(pv, dict) or (open_only and m.is_done(pv)):
                continue
            if n >= OPEN_MAX:
                src["truncated"] = True
                break
            n += 1
            it = {"key": f"r:{rid}", "kind": "row", "id": rid, "src": db_id, "text": (rtitle or "").strip()[:TEXT_MAX] or t("Unbenannt"),
                  "due": None, "prio": 2, "done": False, "overdue": False, "edit": edit}
            if m.due is not None:
                dp = due_parts(pv.get(m.due["id"]), tz)
                if dp:
                    it["due"] = dp[0]
                    if dp[1]:
                        it["time"] = dp[1]
                    if dp[2] != dp[0]:
                        it["due_end"] = dp[2]
                    it["overdue"] = dp[2] < t0
            if m.prio is not None:
                it["prio"] = m.prio_of.get(pv.get(m.prio["id"]), 2)
            if m.assignee is not None:
                av = [x for x in (pv.get(m.assignee["id"]) or []) if isinstance(x, str)] if isinstance(pv.get(m.assignee["id"]), list) else []
                canons.extend(av)
                it["assignees"] = [people.to_ref(c, x, uid) for x in av]
            items.append(it)
        src["count"] = n
    if gone and clean:
        c.execute(f"DELETE FROM task_source WHERE uid=? AND db_id IN ({','.join('?' * len(gone))})", (uid, *gone))
    items.sort(key=lambda x: _sort_key(x, t0))
    return items, sources, people.label_map(c, canons, uid) if canons else {}


def _scope_default(owner, uid, m):
    return "all" if owner == uid or m is None or m.assignee is None else "assigned"


def subscribe(c, uid, db_id, scope=None):
    """Quelle abonnieren bzw. Umfang aendern -> (scope, None) | (None, Fehlerantwort). Eigene: Standard 'all', geteilte mit
    Personen-Zuordnung: 'assigned'."""
    if db_id < 0:
        return None, _bad(t("Datenbanken anderer Saga-Server lassen sich (noch) nicht als Aufgabenquelle verwenden."))
    acc = Access(c, uid)
    d = _db_row(c, acc, db_id)
    if d is None:
        return None, _bad("not found", 404)
    owner, _, _, _, schema, _ = d
    tm = task_map(schema)
    if not tm:
        return None, _bad(t("Diese Datenbank ist noch keine Aufgabenquelle."))
    m = Mapping(schema, tm)
    if scope is not None and scope not in SCOPES:
        return None, _bad(t("ungültige Angabe"))
    have = c.execute("SELECT scope FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).fetchone()
    scope = scope or (have[0] if have else _scope_default(owner, uid, m))
    if scope == "assigned" and m.assignee is None:
        return None, _bad(t("Ohne Personen-Eigenschaft gibt es nur „alle offenen Einträge“."))
    if not have and c.execute("SELECT COUNT(*) FROM task_source WHERE uid=?", (uid,)).fetchone()[0] >= SRC_MAX:
        return None, _bad(t("Höchstens {n} Aufgabenquellen", n=SRC_MAX), 409)
    c.execute("INSERT OR REPLACE INTO task_source (uid, db_id, scope, added) VALUES (?,?,?,?)",
              (uid, db_id, scope, time.time()) if not have else (uid, db_id, scope, _added(c, uid, db_id)))
    return scope, None


def _added(c, uid, db_id):
    r = c.execute("SELECT added FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).fetchone()
    return r[0] if r else time.time()


def auto_subscribe(c, uid, owner, db_id, schema):
    """Galerie: neue Datenbank mit Zuordnung gleich abonnieren (still, wenn schon 20 Quellen)."""
    tm = task_map(schema)
    if not tm or c.execute("SELECT COUNT(*) FROM task_source WHERE uid=?", (uid,)).fetchone()[0] >= SRC_MAX:
        return
    c.execute("INSERT OR IGNORE INTO task_source (uid, db_id, scope, added) VALUES (?,?,?,?)",
              (uid, db_id, _scope_default(owner, uid, Mapping(schema, tm)), time.time()))


# --- erledigt (row_done) ---------------------------------------------------------------------------------------------
def track(c, owner, db_id, row_id):
    """Zeile gespeichert: Uebergang offen -> erledigt merken (row_done), wieder offen -> loeschen. Nur fuer Quellen."""
    r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (db_id, owner)).fetchone()
    if not r or '"tasks"' not in (r[0] or ""):
        return
    schema = _json_load(r[0], {})
    tm = task_map(schema)
    if not tm:
        return
    pr = c.execute("SELECT props, deleted_at FROM note WHERE id=? AND uid=? AND parent_id=?", (row_id, owner, db_id)).fetchone()
    if not pr:
        return
    if Mapping(schema, tm).is_done(_json_load(pr[0], {})) and pr[1] is None:
        c.execute("INSERT OR IGNORE INTO row_done (note_id, db_id, done_at) VALUES (?,?,?)", (row_id, db_id, time.time()))
    else:
        c.execute("DELETE FROM row_done WHERE note_id=?", (row_id,))


def _on_saved(c, note_id=None, owner=None, row=None, **_):
    if row is not None and note_id is not None and owner:
        track(c, owner, note_id, row)


bus.on("note.saved", _on_saved)


def done_week(c, uid, mon, sun, tz, acc=None):
    """Erledigte Zeilen der abonnierten Quellen in [mon, sun] (Tage in tz) - mit Umfang (assigned) und Rechten."""
    acc = acc or Access(c, uid)
    lo = datetime.combine(mon, datetime.min.time(), tz).timestamp()
    hi = datetime.combine(sun + timedelta(days=1), datetime.min.time(), tz).timestamp()
    n = 0
    for db_id, scope in subs(c, uid):
        d = _db_row(c, acc, db_id)
        if d is None or not d[5]:
            continue
        tm = task_map(d[4])
        if not tm:
            continue
        sql = ("SELECT COUNT(*) FROM row_done r JOIN note n ON n.id=r.note_id WHERE r.db_id=? AND n.parent_id=? AND n.deleted_at IS NULL "
               "AND n.archived IS NULL AND r.done_at>=? AND r.done_at<?")
        args = [db_id, db_id, lo, hi]
        if scope == "assigned":
            if not tm.get("assignee"):
                continue
            sql += " AND r.note_id IN (SELECT note_id FROM row_person WHERE db_id=? AND pid=? AND person=?)"
            args += [db_id, tm["assignee"], uid]
        n += c.execute(sql, tuple(args)).fetchone()[0]
    return n


def stats(c, uid, today, tz):
    """Zaehler der Quellen fuer src:"tasks"-Widgets: {open, due_today, overdue, done_week, open_week}."""
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)
    acc = Access(c, uid)
    items, _, _ = collect(c, uid, today, tz, acc, with_inbox=False, clean=False)
    out = {"open": len(items), "due_today": 0, "overdue": 0, "done_week": 0, "open_week": 0}
    t0 = today.isoformat()
    for it in items:
        if it["overdue"]:
            out["overdue"] += 1
        elif it.get("due") == t0:
            out["due_today"] += 1
        if it.get("due") and mon.isoformat() <= it["due"] <= sun.isoformat():
            out["open_week"] += 1
    out["done_week"] = done_week(c, uid, mon, sun, tz, acc)
    return out


# --- Lesen/Schreiben fuer die Endpunkte ------------------------------------------------------------------------------
def tz_of(v):
    try:
        return ZoneInfo(v) if isinstance(v, str) and v and len(v) <= 64 else TZ
    except Exception:  # noqa: BLE001 - unbekannte Zone: Server-Zeitzone
        return TZ


def merged(uid, params):
    """Teil fuer GET /api/tasks?with_sources=1 -> {items, sources, people, limits}."""
    from saga.tasks import _client_today
    tz = tz_of(params.get("tz"))
    today = _client_today({"today": params.get("today")}) if params.get("today") else datetime.now(tz).date()
    with _db() as c, c:
        items, sources, ppl = collect(c, uid, today, tz)
    return {"items": items, "sources": sources, "people": ppl, "limits": {"sources": SRC_MAX, "rows": OPEN_MAX}}


def _row_of(c, uid, row):
    """-> (eigentuemer, db_id, schema, mapping, rolle) einer Zeile einer Aufgabenquelle oder Fehlerantwort."""
    acc = Access(c, uid)
    role = acc.role(row)
    if not role:
        return _bad("not found", 404)
    owner = acc.owner(row)
    r = c.execute("SELECT d.id, d.schema FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid AND d.kind='database' "
                  "WHERE n.id=? AND n.uid=? AND n.deleted_at IS NULL", (row, owner)).fetchone()
    if not r:
        return _bad("not found", 404)
    schema = _json_load(r[1], {"props": []})
    tm = task_map(schema)
    if not tm:
        return _bad(t("Diese Datenbank ist keine Aufgabenquelle."))
    return owner, r[0], schema, Mapping(schema, tm), role


def _due_in(v):
    if v in (None, ""):
        return None
    return date.fromisoformat(str(v)).isoformat()


def patch_row(uid, row, b, origin=None):
    """PATCH /api/tasks/src/{row} {done?, due?, prio?, text?} - Huelle ueber das Props-PATCH (gleiche Rechte: 'edit')."""
    from saga import pages
    if row < 0:
        return _bad(t("Datenbanken anderer Saga-Server lassen sich (noch) nicht als Aufgabenquelle verwenden."))
    with _db() as c:
        x = _row_of(c, uid, row)
    if not isinstance(x, tuple):
        return x
    owner, db_id, schema, m, role = x
    if RANK.get(role, 0) < RANK["edit"]:
        return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    props, up = {}, {}
    try:
        if "done" in b:
            if not isinstance(b["done"], bool):
                raise ValueError
            v = m.done_value if b["done"] else m.open_value
            if b["done"] and v is None:
                raise ValueError
            props[m.dp["id"]] = v
        if "due" in b:
            if m.due is None:
                return _bad(t("Diese Aufgabenquelle hat kein Fälligkeitsdatum."))
            props[m.due["id"]] = _due_in(b["due"])
        if "prio" in b:
            if m.prio is None:
                return _bad(t("Diese Aufgabenquelle hat keine Priorität."))
            p = b["prio"]
            if isinstance(p, bool) or p not in (1, 2, 3):
                raise ValueError
            props[m.prio["id"]] = m.prio_val.get(p) or m.prio_val.get(2)
        if "text" in b:
            from saga.tasks import _text_in
            tx = _text_in(b["text"])
            if not tx:
                return _bad(t("leere Aufgabe"))
            up["title"] = tx
    except (ValueError, TypeError):
        return _bad(t("ungültige Angabe (Datum, Priorität oder Wiederholung)"))
    if props:
        up["props"] = props
    if not up:
        return _bad(t("nichts zu ändern"))
    try:
        res = pages._note_update(uid, row, up, origin)
    except pages._Reply as r:
        return r.resp
    if res.status_code != 200:
        return res
    out = {"ok": True, "src": db_id}
    if "done" in b:
        out["done"] = b["done"]
        out["done_value"] = props[m.dp["id"]]
    return JSONResponse(out)


def add_row(uid, db_id, b, origin=None):
    """POST /api/tasks {text, src: <db>, due?, prio?} -> neue Zeile (Titel = Text, Standardvorlage der Datenbank)."""
    from saga import databases, pages
    from saga.tasks import _text_in
    if isinstance(db_id, bool) or not isinstance(db_id, int):
        return _bad(t("ungültige Angabe"))
    if db_id < 0:
        return _bad(t("Datenbanken anderer Saga-Server lassen sich (noch) nicht als Aufgabenquelle verwenden."))
    text = _text_in(b.get("text"))
    if not text:
        return _bad(t("leere Aufgabe"))
    try:
        due = _due_in(b.get("due"))
        prio = b.get("prio", 2)
        if isinstance(prio, bool) or prio not in (1, 2, 3):
            raise ValueError
    except (ValueError, TypeError):
        return _bad(t("ungültige Angabe (Datum, Priorität oder Wiederholung)"))
    with _db() as c:
        acc = Access(c, uid)
        d = _db_row(c, acc, db_id)
        if d is None:
            return _bad("not found", 404)
        owner, role, _, _, schema, active = d
        tm = task_map(schema)
        if not tm:
            return _bad(t("Diese Datenbank ist keine Aufgabenquelle."))
        if RANK.get(role, 0) < RANK["edit"]:
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if not active:
            return _bad("not found", 404)
        m = Mapping(schema, tm)
        sub = c.execute("SELECT scope FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).fetchone()
        tpl = schema.get("default_tpl")
        if tpl and not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND parent_id=? AND is_template=1 AND deleted_at IS NULL",
                                 (tpl, owner, db_id)).fetchone():
            tpl = None
    props = {}
    if not tpl and m.open_value is not None:
        props[m.dp["id"]] = m.open_value
    if due and m.due is not None:
        props[m.due["id"]] = due
    if prio != 2 and m.prio is not None and m.prio_val.get(prio):
        props[m.prio["id"]] = m.prio_val[prio]
    if m.assignee is not None and ((sub and sub[0] == "assigned") or (not sub and owner != uid)):
        props[m.assignee["id"]] = [uid]            # eigener Benutzername ist als Eingabe erlaubt (people.from_ref)
    if tpl:
        res = databases.note_duplicate(uid, tpl, {"parent_id": db_id, "title": text}, origin)
        if res.status_code != 200:
            return res
        rid = json.loads(res.body)["id"]
        if props:
            try:
                r2 = pages._note_update(uid, rid, {"props": props}, origin)
            except pages._Reply as r:
                r2 = r.resp
            if r2.status_code != 200:
                return r2
    else:
        res = pages.note_create(uid, {"parent_id": db_id, "title": text, "props": props}, origin)
        if res.status_code != 200:
            return res
        rid = json.loads(res.body)["id"]
    return JSONResponse({"id": rid, "kind": "row", "src": db_id})


# --- Endpunkte -------------------------------------------------------------------------------------------------------
@router.patch("/api/tasks/src/{row}")
async def api_src_patch(row: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    from saga.core import origin_of
    return await run_in_threadpool(patch_row, uid, row, b, origin_of(request))


@router.get("/api/tasks/sources")
def api_sources(request: Request):
    """Abonnierte Quellen (auch ohne Zugriff/Zuordnung, damit man sie entfernen kann) -> {sources, max}."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        _, sources, _ = collect(c, uid, datetime.now(TZ).date(), TZ, with_inbox=False)
    return JSONResponse({"sources": sources, "max": SRC_MAX}, headers={"Cache-Control": "no-store"})


def _source_info(c, uid, db_id):
    acc = Access(c, uid)
    d = _db_row(c, acc, db_id)
    if d is None:
        return None
    owner, role, title, icon, schema, _ = d
    sub = c.execute("SELECT scope FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).fetchone()
    tm = task_map(schema)
    n = c.execute("SELECT COUNT(*) FROM task_source WHERE uid=?", (uid,)).fetchone()[0]
    return {"db": db_id, "title": title, "icon": icon, "role": role, "own": owner == uid, "map": tm, "suggest": suggest(schema),
            "can_map": RANK.get(role, 0) >= RANK["edit"], "scope": sub[0] if sub else None,
            "default_scope": _scope_default(owner, uid, Mapping(schema, tm)) if tm else ("all" if owner == uid else "assigned"),
            "hint": looks_like_tasks(schema), "count": n, "max": SRC_MAX}


@router.get("/api/notes/{db_id}/tasksource")
def api_source_get(db_id: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if db_id < 0:
        return JSONResponse({"db": db_id, "remote": True, "map": None, "scope": None, "can_map": False})
    with _db() as c:
        out = _source_info(c, uid, db_id)
    return JSONResponse(out, headers={"Cache-Control": "no-store"}) if out else _bad("not found", 404)


def put_source(uid, db_id, b, origin=None):
    """{map?: {...}|null (setzt die Zuordnung im Schema - ab 'Bearbeiten'), subscribe?: bool (Standard true), scope?}."""
    from saga import pages
    if db_id < 0:
        return _bad(t("Datenbanken anderer Saga-Server lassen sich (noch) nicht als Aufgabenquelle verwenden."))
    if "map" in b:
        with _db() as c:
            acc = Access(c, uid)
            d = _db_row(c, acc, db_id)
            if d is None:
                return _bad("not found", 404)
            if RANK.get(d[1], 0) < RANK["edit"]:
                return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
            schema = d[4]
        mp = b["map"]
        if mp is not None:
            mp = clean_tasks_map(mp, _props(schema))
            if not mp:
                return _bad(t("Bitte eine Eigenschaft für „erledigt“ wählen (Status, Häkchen oder Auswahl)."))
        if mp != task_map(schema):
            ns = dict(schema, tasks=mp)       # null hebt die Zuordnung ausdruecklich auf (der Schluessel muss mitkommen)
            try:
                res = pages._note_update(uid, db_id, {"schema": ns}, origin)
            except pages._Reply as r:
                return r.resp
            if res.status_code != 200:
                return res
    with _db() as c, c:
        if b.get("subscribe", True) is not False:
            _, err = subscribe(c, uid, db_id, b.get("scope"))
            if err is not None and not ("map" in b and b["map"] is None):
                return err
        else:
            c.execute("DELETE FROM task_source WHERE uid=? AND db_id=?", (uid, db_id))
        if "map" in b and b["map"] is None:
            c.execute("DELETE FROM task_source WHERE uid=? AND db_id=?", (uid, db_id))
        out = _source_info(c, uid, db_id)
    return JSONResponse(out or {"ok": True})


@router.put("/api/notes/{db_id}/tasksource")
async def api_source_put(db_id: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    from saga.core import origin_of
    return await run_in_threadpool(put_source, uid, db_id, b, origin_of(request))


@router.delete("/api/notes/{db_id}/tasksource")
def api_source_delete(db_id: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        n = c.execute("DELETE FROM task_source WHERE uid=? AND db_id=?", (uid, db_id)).rowcount
    return JSONResponse({"ok": True}) if n else _bad("not found", 404)


# --- Taegliche Zusammenfassung (P7-E4) -------------------------------------------------------------------------------
def _zones():
    from saga.push import _zones as z
    return z()


def digest_cfg(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='task_due'", (uid,)).fetchone()
    d = _json_load(r[0], {}) if r else {}
    out = dict(DIGEST_DEF)
    out.update({k: d[k] for k in DIGEST_DEF if k in d})
    return out


def _acct_tz(c, uid, cfg):
    if cfg.get("tz") in _zones():
        return ZoneInfo(cfg["tz"])
    from saga.push import prefs
    q = prefs(c, uid).get("quiet") or {}
    return ZoneInfo(q["tz"]) if q.get("tz") in _zones() else TZ


@router.get("/api/tasks/digest")
def api_digest_get(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        return JSONResponse(digest_cfg(c, uid), headers={"Cache-Control": "no-store"})


@router.put("/api/tasks/digest")
async def api_digest_put(request: Request):
    """{on: bool, time: 'HH:MM', tz: 'Europe/Berlin' | ''} - auch teilweise."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None or any(k not in DIGEST_DEF for k in b):
        return _bad(t("ungültige Angabe"))
    from saga.push import TIME_RE
    if "on" in b and not isinstance(b["on"], bool):
        return _bad(t("ungültige Angabe"))
    if "time" in b and not (isinstance(b["time"], str) and TIME_RE.fullmatch(b["time"])):
        return _bad(t("ungültige Uhrzeit"))
    if "tz" in b and b["tz"] not in ("", None) and (not isinstance(b["tz"], str) or b["tz"] not in _zones()):
        return _bad(t("unbekannte Zeitzone"))
    with _db() as c, c:
        cfg = digest_cfg(c, uid)
        cfg.update({k: (v or "") if k == "tz" else v for k, v in b.items()})
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'task_due', ?)", (uid, json.dumps(cfg)))
    return JSONResponse(cfg)


def digest_counts(c, uid, today, tz):
    items, _, _ = collect(c, uid, today, tz, clean=False)
    t0 = today.isoformat()
    over = sum(1 for x in items if x["overdue"])
    due = sum(1 for x in items if not x["overdue"] and x.get("due") == t0)
    return due, over


def digest_run(now=None):
    """Alle Konten mit eingeschalteter Zusammenfassung: ist die Uhrzeit (Ortszeit des Kontos) erreicht und heute noch nichts
    gesendet -> task.due (nur wenn etwas faellig/ueberfaellig ist). Mehrere Prozesse: der Merker task_due_sent wird atomar
    gesetzt. -> Anzahl angelegter Mitteilungen."""
    import auth
    now = now or time.time()
    with _db() as c:
        rows = c.execute("SELECT uid, v FROM user_kv WHERE k='task_due'").fetchall()
    sent = 0
    for uid, raw in rows:
        cfg = _json_load(raw, {})
        if cfg.get("on") is not True:
            continue
        try:
            with _db() as c, c:
                cfg = digest_cfg(c, uid)
                tz = _acct_tz(c, uid, cfg)
                loc = datetime.fromtimestamp(now, tz)
                if loc.strftime("%H:%M") < (cfg.get("time") or DIGEST_DEF["time"]):
                    continue
                day = loc.date().isoformat()
                c.execute("INSERT OR IGNORE INTO user_kv (uid, k, v) VALUES (?, 'task_due_sent', '')", (uid,))
                if not c.execute("UPDATE user_kv SET v=? WHERE uid=? AND k='task_due_sent' AND v<>?", (day, uid, day)).rowcount:
                    continue
                if auth.user_info(uid) is None:
                    continue
                due, over = digest_counts(c, uid, loc.date(), tz)
                if due or over:
                    notify.create(c, uid, DIGEST_KIND, data={"due": due, "overdue": over, "day": day})
                    sent += 1
        except Exception as ex:  # noqa: BLE001 - ein Konto darf die anderen nicht aufhalten
            print(f"task.due: {type(ex).__name__}: {ex}", flush=True)
    return sent


def _digest_text(due, over):
    if due and over:
        return t("Heute fällig: {n} · überfällig: {o}", n=due, o=over)
    if over:
        return t("Überfällige Aufgaben: {o}", o=over)
    return t("Heute fällig: {n} Aufgaben", n=due)


def _render(c, uid, item, d):
    item["access"] = True
    item["text"] = _digest_text(int(d.get("due") or 0), int(d.get("overdue") or 0))
    item["title"] = t("Aufgaben")
    item["url"] = "/"
    item["count"] = 1


notify.RENDER[DIGEST_KIND] = _render

_thread = {"t": None, "stop": False, "ev": threading.Event()}


def _loop():
    while not _thread["stop"]:
        try:
            digest_run()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg
            print(f"task.due: {type(ex).__name__}: {ex}", flush=True)
        _thread["ev"].wait(TICK)


def start():
    if RESTORE_TEST or _thread["t"] is not None:
        return
    _thread["stop"] = False
    _thread["t"] = threading.Thread(target=_loop, name="task-due", daemon=True)
    _thread["t"].start()


def stop():
    _thread["stop"] = True
    _thread["ev"].set()
    _thread["t"] = None
