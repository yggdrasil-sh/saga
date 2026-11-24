"""Auftrags-Maschine der Kollegin (2.6, Bugs-DB 2266 K-A2, Konzept K2/K4): Auftraege mit fester Schrittliste, Fristen,
Warten auf Ereignisse, Lease, Testuhr. Ein weiterer Hintergrund-Thread wie saga.push (kein neuer Dienst).

Zustaende (STATES, K2): wartet_ok -> laeuft -> erledigt, dazu wartet_dich, fehlgeschlagen, abgebrochen, zurueckgenommen.
Feinere Unterzustaende nur in 'laeuft' (SUBS): wartet_frist, wartet_ereignis, antwort_da, wartet_modell. Nach aussen vier
Gruppen (GROUPS): Wartet auf dich = wartet_ok + wartet_dich, Laeuft = laeuft, Fertig = erledigt + abgebrochen +
zurueckgenommen, Hat nicht geklappt = fehlgeschlagen. Erlaubte Uebergaenge: TRANSITIONS, alles andere ValueError.

Takt (run_once): EINE Abfrage holt faellige Auftraege (next_at <= jetzt, FOR UPDATE SKIP LOCKED) und setzt die Lease
(LEASE s; tries zaehlt jede Uebernahme und ist das Kennzeichen der Lease). Leerlauf = genau diese eine Abfrage. Die Zeit
kommt immer aus testclock.now() als Parameter, nie aus der Datenbank. Je Takt und Auftrag hoechstens ein Schritt; ist er
erledigt und folgt noch einer, ist der Auftrag sofort wieder faellig.

Ein Schritt (Runner je kind, register()):
  1. Wiederaufnahme: steht der Schritt noch auf 'laeuft' (Absturz, Lease abgelaufen), gilt er als erledigt, wenn es einen
     Stempel gibt (job_change mit seinem idem_key), sonst wird er wiederholt.
  2. _precheck vor JEDEM Schritt: Konto da und aktiv, caps.can(uid, "ai"), bei klass 'out' zusaetzlich "ai_act",
     consent_ok(uid) (bis K-I immer True). Fehlt etwas -> wartet_dich mit reason, nichts wird geschrieben (RT-07/RT-36).
  3. Beanspruchen (eigene Transaktion): state 'laeuft', idem_key = "<job>:<n>:<plan_v>".
  4. Runner unter actor.acting(Actor(service, svc:..., on_behalf_of=uid, job_id, step, idem, attempt)) im Worker-Thread
     (with-Block je Schritt, danach kein Akteur; darunter startet die Maschine weder Tasks noch Threads). Jede Wirkung
     ueber bus.fire stempelt _stamp() in DIESELBE Transaktion: job_change(idem_key, target). Der Stempel laesst den
     Schreibvorgang scheitern (StepTaken), wenn der Auftrag nicht mehr laeuft oder ein ANDERER Versuch desselben Schritts
     schon gewirkt hat - so gibt es auch bei zwei Workern (Lease abgelaufen) genau eine Wirkung.
  5. Abschluss (eigene Transaktion, nur mit eigener Lease): Outcome done/skip/wait/ask/fail/retry_model.
Ein Schritt = hoechstens eine Wirkung (ein Werkzeugaufruf); ein Warte-Schritt schreibt nicht (eigene Schritte schreiben).
Modell nicht erreichbar (Outcome.retry_model oder ModelDown): sub wartet_modell, Backoff 1/5/15/60 min, nach 24 h
fehlgeschlagen. Mehr als MAX_RETRIES Uebernahmen ohne Fortschritt (Absturz-Schleife): fehlgeschlagen.

Warten: Outcome.wait(on_kind, ref, until) legt job_wait an; signal(c, on_kind, ref) (eine indizierte Abfrage, kein Modell)
macht den Auftrag faellig, der Runner sieht ctx.hits bzw. ctx.timed_out (Frist genau until). bus-Haken: task.completed ->
signal("task_done", "task:<id>"), note.saved -> signal("note_saved", "note:<id>"), danach after_commit(wake).
Sperr-Reihenfolge ueberall job_step -> job_wait -> job (Stempel, signal, Abschluss), damit sich nichts verklemmt.

Ruecknahme (undo, Grundgeruest fuer K-B): nur erledigte Auftraege eines kind mit compensate(c, job, change); der Worker
nimmt job_change fuer job_change zurueck (neueste zuerst, je eine Transaktion mit undone_at) und setzt danach
zurueckgenommen - nach einem Absturz geht es mit dem Rest weiter. Vorher-Stand/Konfliktpruefung kommen mit K-B.

Absturzpunkte fuer tests/kollegin_fehler.py: _crash(name) mit name in CRASH_POINTS beendet den Prozess per SIGKILL - nur
mit aktiven Testhaken (testclock.enabled()) und SAGA_TEST_CRASH=<name>.
Aufbewahrung (cleanup, stuendlich): Klartext (plan, args, result, before/after) 30 Tage nach ended_at, alles 90 Tage;
nicht freigegebene Auftraege mit abgelaufener Frist (due_at) werden abgebrochen. Kontoloeschung: USER_TABLES "job".
Tests: tests/kollegin_jobs.py, tests/kollegin_fehler.py; Messung: tools/perf/kollegin_takt.py."""
import hashlib
import json
import os
import re
import signal as _sig
import threading
import traceback
import uuid
from dataclasses import dataclass, field

from db import clean_line, connect as _db
from saga import bus, testclock
from saga.actor import SERVICES, Actor, acting
from saga.core import RESTORE_TEST

STATES = ("wartet_ok", "laeuft", "wartet_dich", "erledigt", "fehlgeschlagen", "abgebrochen", "zurueckgenommen")
SUBS = ("wartet_frist", "wartet_ereignis", "antwort_da", "wartet_modell")
GROUPS = {"wartet_auf_dich": ("wartet_ok", "wartet_dich"), "laeuft": ("laeuft",),
          "fertig": ("erledigt", "abgebrochen", "zurueckgenommen"), "nicht_geklappt": ("fehlgeschlagen",)}
TRANSITIONS = {"wartet_ok": ("laeuft", "abgebrochen"),
               "laeuft": ("wartet_dich", "erledigt", "fehlgeschlagen", "abgebrochen"),
               "wartet_dich": ("laeuft", "abgebrochen", "fehlgeschlagen"),
               "erledigt": ("zurueckgenommen",)}
ACTIVE = ("wartet_ok", "laeuft", "wartet_dich")
ENDED = ("erledigt", "fehlgeschlagen", "abgebrochen", "zurueckgenommen")
SOURCES = ("saga", "share", "thunderbird", "api")
KLASSES = ("read", "own", "out")
UNDO_CLASSES = ("undo", "delete_only", "final")
CONDS = ("if_yes", "if_no", "if_none_by")
STEP_STATES = ("offen", "laeuft", "wartet", "erledigt", "uebersprungen", "fehlgeschlagen")
CRASH_POINTS = ("after_approve", "before_write", "after_write", "mid_compensate")
# Wirkungen, die der Stempel festhaelt: Ereignis -> (Ziel-Praefix, Feld mit der Kennung)
STAMP_TARGETS = {"note.saved": ("note", "note_id"), "row.changed": ("row", "note_id"), "task.created": ("task", "task_id"),
                 "task.completed": ("task", "task_id"), "comment.added": ("comment", "comment_id")}

LEASE = 60                       # s: so lange gehoert ein Auftrag nach dem Holen einem Worker
MAX_ACTIVE = 5                   # nicht beendete Auftraege je Person (RT-28)
MAX_STEPS = 20
MAX_RETRIES = 40                 # Uebernahmen ohne Fortschritt (Absturz-Schleife)
BACKOFF = (60, 300, 900, 3600)   # Modell weg: 1, 5, 15, 60 min
MODEL_GIVEUP = 86400             # nach 24 h ohne Modell: fehlgeschlagen
WAIT_MAX = 30 * 86400            # Warten ohne Frist: hoechstens 30 Tage, dann ctx.timed_out
KEEP_TEXT = 30 * 86400           # Klartext nach Ende
KEEP = 90 * 86400                # Protokoll und Metadaten nach Ende (wie notification)
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}(\.[a-z0-9_]{1,30}){0,3}$")
_TOOL_RE = re.compile(r"^[a-z][a-z0-9_.]{0,59}$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_]{0,15}$")
_REF_RE = re.compile(r"^[a-z_]{1,20}:[A-Za-z0-9_.:@-]{0,120}$")
_JOB_COLS = ("id, uid, kind, source, state, sub, title, plan, plan_v, plan_hash, units, unit, actor, tries, retries, "
             "wait_since, undo_at")
_CLAIM = (f"UPDATE job SET lease_until=?, next_at=?, tries=tries+1, retries=retries+1 WHERE id IN (SELECT id FROM job "
          f"WHERE next_at<=? ORDER BY next_at, id LIMIT ? FOR UPDATE SKIP LOCKED) RETURNING {_JOB_COLS}")


class LimitError(Exception):
    """Zu viele nicht beendete Auftraege dieser Person (RT-28)."""


class StepTaken(RuntimeError):
    """Stempel verweigert: Auftrag laeuft nicht mehr oder ein anderer Versuch hat den Schritt schon ausgefuehrt."""


class ModelDown(Exception):
    """Runner: Modell gerade nicht erreichbar (wie Outcome.retry_model)."""


def now():
    return testclock.now()


def consent_ok(uid):
    """Einwilligung der Person fuer die Kollegin (K-I) - bis dahin immer True."""
    return True


def group_of(state):
    for g, sts in GROUPS.items():
        if state in sts:
            return g
    return None


def check_transition(old, new):
    """ValueError, wenn der Uebergang old -> new nicht erlaubt ist."""
    if old not in STATES or new not in STATES or new not in TRANSITIONS.get(old, ()):
        raise ValueError(f"Uebergang {old} -> {new} nicht erlaubt")


def _code(s, n=60):
    """Grund/Kennung fuer job.reason und job_event: kurz, einzeilig (kein Klartext aus Inhalten)."""
    return clean_line(str(s))[:n] if s is not None else None


def _j(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def plan_hash(plan, steps):
    return hashlib.sha256(_j({"plan": plan, "steps": steps}).encode()).hexdigest()


# --- Ergebnis eines Schritts -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Outcome:
    kind: str                        # done | skip | wait | ask | fail | retry_model
    result: object = None
    units: int = 0
    reason: str | None = None
    on_kind: str | None = None
    ref: str | None = None
    until: float | None = None

    @classmethod
    def done(cls, result=None, units=0):
        return cls("done", result=result, units=units)

    @classmethod
    def skip(cls, reason=None):
        return cls("skip", reason=reason)

    @classmethod
    def wait(cls, on_kind=None, ref=None, until=None, units=0):
        """Warten auf ein Ereignis (on_kind + ref) und/oder bis until (Sekunden, testclock)."""
        return cls("wait", on_kind=on_kind, ref=ref, until=until, units=units)

    @classmethod
    def ask(cls, reason, units=0):
        return cls("ask", reason=reason, units=units)

    @classmethod
    def fail(cls, reason, units=0):
        return cls("fail", reason=reason, units=units)

    @classmethod
    def retry_model(cls, reason="modell", units=0):
        return cls("retry_model", reason=reason, units=units)


@dataclass
class Ctx:
    """Was der Runner ueber seinen Schritt weiss. hits: eingetroffene Ereignisse [{on_kind, ref, at, data}];
    timed_out: Frist erreicht ohne Ereignis; changes: Ziele, die fruehere Versuche dieses Schritts gestempelt haben."""
    job_id: int
    uid: str
    step: int
    now: float
    idem: str
    attempt: str
    tries: int
    hits: list = field(default_factory=list)
    timed_out: bool = False
    until: float | None = None
    changes: list = field(default_factory=list)

    def keep(self, seconds=LEASE):
        """Lease verlaengern (lange Modellaufrufe) -> False, wenn der Auftrag nicht mehr diesem Versuch gehoert."""
        t = now()
        with acting(None), _db() as c, c:
            return c.execute("UPDATE job SET lease_until=?, next_at=? WHERE id=? AND tries=? AND lease_until IS NOT NULL",
                             (t + seconds, t + seconds, self.job_id, self.tries)).rowcount == 1


_RUNNERS, _COMP = {}, {}


def register(kind, runner, compensate=None):
    """runner(ctx, job, step) -> Outcome; compensate(c, job, change) nimmt eine Wirkung in der Transaktion c zurueck."""
    if not _KIND_RE.match(kind or ""):
        raise ValueError(f"ungueltige Auftragsart: {kind!r}")
    _RUNNERS[kind] = runner
    if compensate is not None:
        _COMP[kind] = compensate
    else:
        _COMP.pop(kind, None)


def _crash(name):
    if os.environ.get("SAGA_TEST_CRASH") == name and name in CRASH_POINTS and testclock.enabled():
        print(f"jobs: Absturzpunkt {name} (SAGA_TEST_CRASH)", flush=True)
        os.kill(os.getpid(), _sig.SIGKILL)


def _event(c, job_id, kind, t, **data):
    c.execute("INSERT INTO job_event (job_id, at, kind, data) VALUES (?, ?, ?, CAST(? AS JSONB))", (job_id, t, kind, _j(data)))


def _to(c, job_id, old, new, t, reason=None, where="", args=(), sets="", sargs=()):
    """Zustand old -> new (geprueft) -> True, wenn der Auftrag noch in old war."""
    check_transition(old, new)
    end = new in ENDED
    n = c.execute(f"UPDATE job SET state=?, sub=NULL, updated=?, reason=?, retries=0, wait_since=NULL, lease_until=NULL, "
                  f"next_at={'?' if new == 'laeuft' else 'NULL'}, ended_at={'?' if end else 'NULL'}{sets} "
                  f"WHERE id=? AND state=?{where}",
                  (new, t, _code(reason), *((t,) if new == "laeuft" else ()), *((t,) if end else ()), *sargs, job_id, old,
                   *args)).rowcount
    if n:
        _event(c, job_id, "state", t, frm=old, to=new, reason=_code(reason))
        if new == "laeuft":
            c.after_commit(wake)
    return bool(n)


# --- Anlegen und Bedienen ----------------------------------------------------------------------------------------
def _norm_steps(steps):
    out = []
    for i, s in enumerate(steps or ()):
        if not isinstance(s, dict):
            raise ValueError(f"Schritt {i}: kein Objekt")
        tool, klass, undo = s.get("tool"), s.get("klass", "own"), s.get("undo_class", "undo")
        if not isinstance(tool, str) or not _TOOL_RE.match(tool):
            raise ValueError(f"Schritt {i}: Werkzeug ungueltig")
        if klass not in KLASSES or undo not in UNDO_CLASSES:
            raise ValueError(f"Schritt {i}: Klasse ungueltig")
        cond = s.get("cond")
        if cond is not None and cond not in CONDS:
            raise ValueError(f"Schritt {i}: Bedingung ungueltig")
        args = s.get("args")
        if args is not None and not isinstance(args, dict):
            raise ValueError(f"Schritt {i}: args muss ein Objekt sein")
        out.append({"tool": tool, "klass": klass, "undo_class": undo, "args": args, "cond": cond, "client": bool(s.get("client"))})
    if not 1 <= len(out) <= MAX_STEPS:
        raise ValueError(f"1 bis {MAX_STEPS} Schritte")
    return out


def create(uid, kind, *, source="saga", title="", plan=None, steps=(), due_at=None, actor="svc:kollegin", unit="u", units_est=0):
    """Neuer Auftrag (Zustand wartet_ok) -> id. Besitzer ist genau eine lokale Person (keine Foederations-Kennung, kein
    Dienst); hoechstens MAX_ACTIVE nicht beendete Auftraege je Person, sonst LimitError (RT-28)."""
    if not isinstance(uid, str) or not uid or "@" in uid or uid.startswith("svc:") or uid != uid.strip():
        raise ValueError("Auftraggeber muss eine lokale Person sein")
    if not isinstance(kind, str) or not _KIND_RE.match(kind):
        raise ValueError("ungueltige Auftragsart")
    if source not in SOURCES:
        raise ValueError("ungueltige Herkunft")
    if actor not in SERVICES:
        raise ValueError("unbekannter Dienst")
    Actor("service", actor, on_behalf_of=uid)        # dieselben Regeln wie beim Schreiben
    if not isinstance(unit, str) or not _UNIT_RE.match(unit):
        raise ValueError("ungueltige Einheit")
    if plan is None:
        plan = {}
    if not isinstance(plan, dict):
        raise ValueError("plan muss ein Objekt sein")
    st = _norm_steps(steps)
    units_est = max(0, int(units_est or 0))
    due_at = float(due_at) if due_at is not None else None
    title = clean_line(str(title or ""))[:200]
    ph = plan_hash(plan, st)
    t = now()
    with acting(None), _db() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(2266, hashtext(?))", (uid,))     # 50 gleichzeitig: Zaehlen + Anlegen am Stueck
        u = c.execute("SELECT disabled FROM users WHERE username=?", (uid,)).fetchone()
        if not u or u[0]:
            raise ValueError("Konto nicht vorhanden oder gesperrt")
        n = c.execute("SELECT COUNT(*) FROM job WHERE uid=? AND state IN ('wartet_ok','laeuft','wartet_dich')", (uid,)).fetchone()[0]
        if n >= MAX_ACTIVE:
            raise LimitError(f"hoechstens {MAX_ACTIVE} offene Auftraege")
        jid = c.execute("INSERT INTO job (uid, kind, source, state, title, plan, plan_v, plan_hash, due_at, units_est, unit, created, "
                        "updated, actor_kind, actor, on_behalf_of) VALUES (?, ?, ?, 'wartet_ok', ?, CAST(? AS JSONB), 1, ?, ?, ?, ?, ?, ?, "
                        "'service', ?, ?)", (uid, kind, source, title, _j(plan), ph, due_at, units_est, unit, t, t, actor, uid)).lastrowid
        c.executemany("INSERT INTO job_step (job_id, n, tool, klass, undo_class, args, cond, client) VALUES "
                      "(?, ?, ?, ?, ?, CAST(? AS JSONB), ?, ?)",
                      [(jid, i, s["tool"], s["klass"], s["undo_class"], None if s["args"] is None else _j(s["args"]), s["cond"],
                        1 if s["client"] else 0) for i, s in enumerate(st)])
        _event(c, jid, "state", t, frm=None, to="wartet_ok", steps=len(st))
    return jid


def approve(job_id, uid, plan_v, plan_hash_):
    """Freigabe genau des gezeigten Plans (plan_v + plan_hash) -> True; zweite Freigabe/veralteter Plan -> False."""
    t = now()
    with acting(None), _db() as c, c:
        ok = _to(c, job_id, "wartet_ok", "laeuft", t, where=" AND uid=? AND plan_v=? AND plan_hash=?", args=(uid, plan_v, plan_hash_))
    if ok:
        _crash("after_approve")
    return ok


def resume(job_id, uid):
    """wartet_dich -> laeuft (die Person hat nachgebessert); fehlt weiter etwas, landet er beim naechsten Schritt wieder dort."""
    with acting(None), _db() as c, c:
        return _to(c, job_id, "wartet_dich", "laeuft", now(), where=" AND uid=?", args=(uid,))


def cancel(job_id, uid, reason="person"):
    """Abbrechen (nur die Besitzerin, nur nicht beendete Auftraege) -> True. Ein gerade laufender Schritt darf danach
    nicht mehr schreiben (der Stempel verweigert)."""
    t = now()
    with acting(None), _db() as c, c:
        r = c.execute("SELECT state FROM job WHERE id=? AND uid=?", (job_id, uid)).fetchone()
        if not r or r[0] not in ACTIVE:
            return False
        c.execute("DELETE FROM job_wait WHERE job_id=?", (job_id,))
        return _to(c, job_id, r[0], "abgebrochen", t, reason=reason)


def pause_all(uid, reason):
    """Alle laufenden Auftraege der Person anhalten (Einwilligung widerrufen RT-36, Konto gesperrt RT-07) -> Anzahl.
    Danach kein weiterer Schritt und kein Modellaufruf; ein laufender Schritt darf nicht mehr schreiben."""
    t = now()
    n = 0
    with acting(None), _db() as c, c:
        for (jid,) in c.execute("SELECT id FROM job WHERE uid=? AND state='laeuft' ORDER BY id FOR UPDATE", (uid,)).fetchall():
            n += _to(c, jid, "laeuft", "wartet_dich", t, reason=reason)
    return n


def undo(job_id, uid):
    """Ruecknahme eines erledigten Auftrags anstossen (Worker, kind mit compensate) -> True."""
    t = now()
    with acting(None), _db() as c, c:
        r = c.execute("SELECT kind FROM job WHERE id=? AND uid=? AND state='erledigt' AND undo_at IS NULL", (job_id, uid)).fetchone()
        if not r or r[0] not in _COMP:
            return False
        c.execute("UPDATE job SET undo_at=?, next_at=?, updated=? WHERE id=?", (t, t, t, job_id))
        _event(c, job_id, "undo", t, requested=True)
        c.after_commit(wake)
    return True


def wait_for(c, job_id, on_kind, ref, until, step_n=None):
    """Auftrag auf ein Ereignis warten lassen (in der Transaktion c). until None = hoechstens WAIT_MAX."""
    if not _REF_RE.match(f"{on_kind}:") or not isinstance(ref, str) or len(ref) > 140:
        raise ValueError("ungueltiges Ereignis")
    t = now()
    if step_n is None:
        r = c.execute("SELECT MIN(n) FROM job_step WHERE job_id=? AND state IN ('offen','laeuft','wartet')", (job_id,)).fetchone()
        step_n = r[0] if r and r[0] is not None else 0
    until = float(until) if until is not None else t + WAIT_MAX
    c.execute("INSERT INTO job_wait (job_id, step_n, on_kind, ref, until) VALUES (?, ?, ?, ?, ?)", (job_id, step_n, on_kind, ref, until))
    c.execute("UPDATE job SET sub=?, next_at=CASE WHEN lease_until IS NOT NULL AND lease_until > ? THEN next_at ELSE ? END, "
              "updated=? WHERE id=? AND state='laeuft'", ("wartet_ereignis" if on_kind != "time" else "wartet_frist", t, until, t, job_id))


_SIGNAL = ("WITH w AS (UPDATE job_wait SET hit_at=?, data=CAST(? AS JSONB) WHERE on_kind=? AND ref=? AND hit_at IS NULL "
           "RETURNING job_id) UPDATE job SET next_at=CASE WHEN lease_until IS NOT NULL AND lease_until > ? THEN next_at ELSE ? END, "
           "updated=? WHERE id IN (SELECT job_id FROM w) AND state='laeuft' RETURNING id")


def signal(c, on_kind, ref, **data):
    """Ereignis eingetroffen -> Anzahl geweckter Auftraege. Genau eine (indizierte) Abfrage, kein Modellaufruf; data nur
    Kennungen. Haelt ein Worker den Auftrag gerade (Lease), bleibt next_at - der Abschluss sieht den Treffer."""
    t = now()
    n = len(c.execute(_SIGNAL, (t, _j(data), on_kind, ref, t, t, t)).fetchall())
    if n:
        c.after_commit(wake)
    return n


def state_of(job_id):
    """-> {state, sub, group, reason, next_at, units, unit, plan_v, plan_hash} oder None."""
    with _db() as c:
        r = c.execute("SELECT state, sub, reason, next_at, units, unit, plan_v, plan_hash, uid, kind, source FROM job WHERE id=?",
                      (job_id,)).fetchone()
    if not r:
        return None
    return {"state": r[0], "sub": r[1], "group": group_of(r[0]), "reason": r[2], "next_at": r[3], "units": r[4], "unit": r[5],
            "plan_v": r[6], "plan_hash": r[7], "uid": r[8], "kind": r[9], "source": r[10]}


def steps_of(job_id):
    with _db() as c:
        return [{"n": r[0], "tool": r[1], "state": r[2], "idem_key": r[3], "result": r[4]} for r in c.execute(
            "SELECT n, tool, state, idem_key, result FROM job_step WHERE job_id=? ORDER BY n", (job_id,)).fetchall()]


# --- Stempel (bus.fire) und bus-Haken ----------------------------------------------------------------------------
def _stamp(c, name, a, data):
    """In der Schreib-Transaktion: Wirkung festhalten. Fehler -> der Schreibvorgang scheitert (bus.fire reicht weiter)."""
    spec = STAMP_TARGETS.get(name)
    if spec is None or data.get(spec[1]) is None:
        return
    if a.job_id is None or a.step is None:
        raise StepTaken("Schreiben unter Schritt-Schluessel ohne Auftrag")
    r = c.execute("SELECT s.state, j.state FROM job_step s JOIN job j ON j.id=s.job_id WHERE s.idem_key=? AND s.job_id=? AND s.n=? "
                  "FOR UPDATE OF s", (a.idem, a.job_id, a.step)).fetchone()
    if not r or r[0] != "laeuft" or r[1] != "laeuft":
        raise StepTaken("Auftrag oder Schritt laeuft nicht mehr")
    if c.execute("SELECT 1 FROM job_change WHERE idem_key=? AND attempt IS DISTINCT FROM ? LIMIT 1", (a.idem, a.attempt)).fetchone():
        raise StepTaken("Schritt hat schon gewirkt (anderer Versuch)")
    c.execute("INSERT INTO job_change (job_id, step_n, idem_key, attempt, target, at) VALUES (?, ?, ?, ?, ?, ?) "
              "ON CONFLICT (idem_key, target) DO NOTHING", (a.job_id, a.step, a.idem, a.attempt, f"{spec[0]}:{data[spec[1]]}", now()))


bus.set_stamp(_stamp)


def _on_task_completed(c, task_id=None, **_):
    if task_id is not None:
        signal(c, "task_done", f"task:{task_id}")


def _on_note_saved(c, note_id=None, **_):
    if note_id is not None:
        signal(c, "note_saved", f"note:{note_id}")


bus.on("task.completed", _on_task_completed)
bus.on("note.saved", _on_note_saved)


# --- Worker ------------------------------------------------------------------------------------------------------
@dataclass
class _Job:
    id: int
    uid: str
    kind: str
    source: str
    state: str
    sub: str | None
    title: str
    plan: object
    plan_v: int
    plan_hash: str
    units: int
    unit: str
    actor: str
    tries: int
    retries: int
    wait_since: float | None
    undo_at: float | None

    def view(self):
        return {"id": self.id, "uid": self.uid, "kind": self.kind, "source": self.source, "title": self.title, "plan": self.plan,
                "plan_v": self.plan_v, "units": self.units, "unit": self.unit, "actor": self.actor}


def _precheck(c, uid, klass):
    """-> Grund (Kennung) oder None. Vor jedem Schritt (RT-07, RT-36)."""
    from saga import caps
    u = c.execute("SELECT disabled FROM users WHERE username=?", (uid,)).fetchone()
    if not u:
        return "konto_weg"
    if u[0]:
        return "konto_gesperrt"
    if not caps.can(uid, "ai", c):
        return "ai"
    if klass == "out" and not caps.can(uid, "ai_act", c):
        return "ai_act"
    if not consent_ok(uid):
        return "einwilligung"
    return None


def _mine(c, j, lock=True):
    """Gehoert der Auftrag noch dieser Lease? -> (state, units) oder None."""
    r = c.execute("SELECT state, units, tries FROM job WHERE id=?" + (" FOR UPDATE" if lock else ""), (j.id,)).fetchone()
    return (r[0], r[1]) if r and r[2] == j.tries else None


def _release(j, t):
    with _db() as c, c:
        c.execute("UPDATE job SET next_at=NULL, lease_until=NULL, updated=? WHERE id=? AND tries=?", (t, j.id, j.tries))


def _end(j, new, reason, t, step=None, step_state=None):
    """laeuft -> new mit eigener Lease (Schritt-Zustand optional mit)."""
    with _db() as c, c:
        if step is not None and step_state:
            c.execute("UPDATE job_step SET state=? WHERE job_id=? AND n=?", (step_state, j.id, step))
        if new in ENDED:
            c.execute("DELETE FROM job_wait WHERE job_id=?", (j.id,))
        m = _mine(c, j)
        if not m or m[0] != "laeuft":
            c.rollback()
            return False
        return _to(c, j.id, "laeuft", new, t, reason=reason)


def _run_job(j):
    t = now()
    if j.state == "erledigt" and j.undo_at is not None:
        return _compensate(j, t)
    if j.state != "laeuft":
        return _release(j, t)
    if j.sub == "wartet_modell" and j.wait_since is not None and t - j.wait_since >= MODEL_GIVEUP:
        return _end(j, "fehlgeschlagen", "modell", t)
    if j.retries > MAX_RETRIES:
        return _end(j, "fehlgeschlagen", "versuche", t)
    with _db() as c:
        steps = c.execute("SELECT n, tool, klass, undo_class, args, cond, client, state, result FROM job_step WHERE job_id=? ORDER BY n",
                          (j.id,)).fetchall()
        cur = next((s for s in steps if s[7] in ("offen", "laeuft", "wartet")), None)
        if cur is None:
            pass
        else:
            n, tool, klass, undo_c, args, cond, client, sstate, _res = cur
            key = f"{j.id}:{n}:{j.plan_v}"
            changes = [r[0] for r in c.execute("SELECT target FROM job_change WHERE idem_key=? ORDER BY id", (key,)).fetchall()]
            why = None if (sstate == "laeuft" and changes) else _precheck(c, j.uid, klass)
    if cur is None:
        return _end(j, "erledigt", None, t)
    if sstate == "laeuft" and changes:           # Wiederaufnahme: Wirkung ist schon da -> erledigt, nicht wiederholen
        return _finish(j, n, key, None, Outcome.done({"changes": changes, "resumed": True}), t, len(steps))
    if why:
        return _end(j, "wartet_dich", why, t)
    runner = _RUNNERS.get(j.kind)
    if runner is None:
        return _end(j, "fehlgeschlagen", "unbekannte_art", t, n, "fehlgeschlagen")
    attempt = uuid.uuid4().hex[:16]
    with _db() as c, c:                          # Schritt beanspruchen (nur mit eigener Lease)
        if not c.execute("UPDATE job_step SET state='laeuft', idem_key=? WHERE job_id=? AND n=? AND state IN ('offen','laeuft','wartet') "
                         "AND EXISTS (SELECT 1 FROM job WHERE id=? AND state='laeuft' AND tries=?)",
                         (key, j.id, n, j.id, j.tries)).rowcount:
            return None
        waits = c.execute("SELECT id, on_kind, ref, hit_at, data, until FROM job_wait WHERE job_id=? AND step_n=? ORDER BY id",
                          (j.id, n)).fetchall()
    _crash("before_write")
    hits = [{"on_kind": w[1], "ref": w[2], "at": w[3], "data": w[4]} for w in waits if w[3] is not None]
    until = min((w[5] for w in waits if w[5] is not None), default=None)
    ctx = Ctx(job_id=j.id, uid=j.uid, step=n, now=t, idem=key, attempt=attempt, tries=j.tries, hits=hits,
              timed_out=bool(waits) and not hits and until is not None and t >= until, until=until, changes=changes)
    step = {"n": n, "tool": tool, "klass": klass, "undo_class": undo_c, "args": args, "cond": cond, "client": bool(client),
            "idem_key": key, "state": sstate}
    a = Actor("service", j.actor, on_behalf_of=j.uid, job_id=j.id, step=n, idem=key, attempt=attempt)
    try:
        with acting(a):                          # nur fuer den Runner; danach wieder kein Akteur
            out = runner(ctx, j.view(), step)
        if not isinstance(out, Outcome) or out.kind not in ("done", "skip", "wait", "ask", "fail", "retry_model"):
            out = Outcome.fail("runner")
    except StepTaken:
        out = Outcome("taken")
    except ModelDown:
        out = Outcome.retry_model()
    except Exception as ex:  # noqa: BLE001 - ein kaputter Runner haelt die Maschine nicht auf
        print(f"jobs: Auftrag {j.id} Schritt {n}: {type(ex).__name__}: {ex}", flush=True)
        traceback.print_exc()
        out = Outcome.fail("intern")
    _crash("after_write")
    return _finish(j, n, key, attempt, out, now(), len(steps), seen={w[0] for w in waits if w[3] is not None})


def _finish(j, n, key, attempt, out, t, nsteps, seen=()):
    with _db() as c, c:
        # Sperr-Reihenfolge job_step -> job_wait -> job (wie _stamp und signal)
        c.execute("SELECT state FROM job_step WHERE job_id=? AND n=? FOR UPDATE", (j.id, n))
        waits = c.execute("SELECT id, on_kind, ref, hit_at FROM job_wait WHERE job_id=? AND step_n=? ORDER BY id FOR UPDATE",
                          (j.id, n)).fetchall()
        m = _mine(c, j)
        stamped = bool(c.execute("SELECT 1 FROM job_change WHERE idem_key=? LIMIT 1", (key,)).fetchone())
        if not m:                                # Lease verloren (ein anderer Worker hat uebernommen): nichts anfassen
            return None
        if m[0] != "laeuft":                     # inzwischen abgebrochen/pausiert: nur den Schritt richtig festhalten
            c.execute("UPDATE job_step SET state=?, done_at=? WHERE job_id=? AND n=? AND state='laeuft'",
                      ("erledigt" if stamped else "offen", t if stamped else None, j.id, n))
            c.execute("UPDATE job SET lease_until=NULL WHERE id=?", (j.id,))
            return None
        units = max(0, int(out.units or 0))
        k = out.kind
        if k == "taken":
            k, out = "done", Outcome.done({"taken": True})
        if k in ("done", "skip"):
            c.execute("UPDATE job_step SET state=?, result=CAST(? AS JSONB), done_at=? WHERE job_id=? AND n=?",
                      ("erledigt" if k == "done" else "uebersprungen", _j(out.result), t, j.id, n))
            c.execute("DELETE FROM job_wait WHERE job_id=? AND step_n=?", (j.id, n))
            _event(c, j.id, "step", t, n=n, outcome=k, reason=_code(out.reason))
            last = n >= nsteps - 1
            if last:
                c.execute("UPDATE job SET units=units+? WHERE id=?", (units, j.id))
                _to(c, j.id, "laeuft", "erledigt", t)
            else:
                c.execute("UPDATE job SET units=units+?, sub=NULL, retries=0, wait_since=NULL, lease_until=NULL, next_at=?, updated=? "
                          "WHERE id=?", (units, t, t, j.id))
                c.after_commit(wake)
            return k
        if k == "wait":
            on_kind = out.on_kind or "time"
            ref = out.ref if out.on_kind else ""
            if not _REF_RE.match(f"{on_kind}:") or not isinstance(ref, str) or len(ref) > 140:
                return _fail_in(c, j, n, "warten_ungueltig", t, units)
            seen_ids = {w[0] for w in waits if w[0] in seen}       # Treffer, die der Runner gesehen hat
            for wid in seen_ids:
                c.execute("DELETE FROM job_wait WHERE id=?", (wid,))
            rest = [w for w in waits if w[0] not in seen_ids]
            until = float(out.until) if out.until is not None else t + WAIT_MAX
            same = next((w for w in rest if w[1] == on_kind and w[2] == ref and w[3] is None), None)
            if same:
                c.execute("UPDATE job_wait SET until=? WHERE id=?", (until, same[0]))
            else:
                c.execute("INSERT INTO job_wait (job_id, step_n, on_kind, ref, until) VALUES (?, ?, ?, ?, ?)", (j.id, n, on_kind, ref, until))
            hit = any(w[3] is not None for w in rest)           # waehrend des Laufs eingetroffen
            c.execute("UPDATE job_step SET state='wartet' WHERE job_id=? AND n=?", (j.id, n))
            c.execute("UPDATE job SET units=units+?, sub=?, retries=0, wait_since=NULL, lease_until=NULL, next_at=?, updated=? WHERE id=?",
                      (units, "wartet_frist" if on_kind == "time" else "wartet_ereignis", t if hit else until, t, j.id))
            _event(c, j.id, "wait", t, n=n, on=on_kind)
            if hit:
                c.after_commit(wake)
            return k
        if k == "retry_model":
            r = c.execute("SELECT wait_since, retries FROM job WHERE id=?", (j.id,)).fetchone()
            since = r[0] if r[0] is not None else t
            c.execute("UPDATE job_step SET state=? WHERE job_id=? AND n=?", ("erledigt" if stamped else "offen", j.id, n))
            if t - since >= MODEL_GIVEUP:
                c.execute("UPDATE job SET units=units+? WHERE id=?", (units, j.id))
                _to(c, j.id, "laeuft", "fehlgeschlagen", t, reason="modell")
                return "fail"
            delay = BACKOFF[min(max(r[1], 1), len(BACKOFF)) - 1]
            c.execute("UPDATE job SET units=units+?, sub='wartet_modell', wait_since=?, lease_until=NULL, next_at=?, updated=? WHERE id=?",
                      (units, since, t + delay, t, j.id))
            _event(c, j.id, "model", t, n=n, delay=delay, reason=_code(out.reason))
            return k
        if k == "ask":
            c.execute("UPDATE job_step SET state=? WHERE job_id=? AND n=?", ("erledigt" if stamped else "offen", j.id, n))
            c.execute("UPDATE job SET units=units+? WHERE id=?", (units, j.id))
            _to(c, j.id, "laeuft", "wartet_dich", t, reason=out.reason or "frage")
            return k
        return _fail_in(c, j, n, out.reason or "fehler", t, units)


def _fail_in(c, j, n, reason, t, units):
    c.execute("UPDATE job_step SET state='fehlgeschlagen', done_at=? WHERE job_id=? AND n=?", (t, j.id, n))
    c.execute("DELETE FROM job_wait WHERE job_id=?", (j.id,))
    c.execute("UPDATE job SET units=units+? WHERE id=?", (units, j.id))
    _to(c, j.id, "laeuft", "fehlgeschlagen", t, reason=reason)
    return "fail"


def _compensate(j, t):
    comp = _COMP.get(j.kind)
    with _db() as c, c:
        u = c.execute("SELECT disabled FROM users WHERE username=?", (j.uid,)).fetchone()
        todo = [r for r in c.execute("SELECT id, step_n, target FROM job_change WHERE job_id=? AND undone_at IS NULL ORDER BY id DESC",
                                     (j.id,)).fetchall()]
    if comp is None or not u or u[0]:
        with _db() as c, c:
            if _mine(c, j):
                c.execute("UPDATE job SET undo_at=NULL, next_at=NULL, lease_until=NULL, updated=? WHERE id=?", (t, j.id))
                _event(c, j.id, "undo", t, blocked="art" if comp is None else "konto")
        return None
    for i, (cid, sn, target) in enumerate(todo):
        if i:
            _crash("mid_compensate")             # nach der ersten Ruecknahme (tests/kollegin_fehler.py)
        with _db() as c, c:
            if c.execute("SELECT undone_at FROM job_change WHERE id=? FOR UPDATE", (cid,)).fetchone()[0] is not None:
                continue
            if not _mine(c, j):
                c.rollback()
                return None
            with acting(Actor("service", j.actor, on_behalf_of=j.uid, job_id=j.id)):
                comp(c, j.view(), {"id": cid, "step": sn, "target": target})
            c.execute("UPDATE job_change SET undone_at=? WHERE id=?", (now(), cid))
            _event(c, j.id, "undo", now(), change=cid, target=target)
    with _db() as c, c:
        if _mine(c, j) and _to(c, j.id, "erledigt", "zurueckgenommen", now(), reason="person"):
            return "undone"
    return None


def run_once(limit=20):
    """Ein Takt: faellige Auftraege holen (EINE Abfrage) und je einen Schritt ausfuehren -> Anzahl geholter Auftraege."""
    with acting(None):                           # nie mit einem Akteur des Aufrufers (Tests, /_test/tick)
        t = now()
        with _db() as c, c:
            rows = c.execute(_CLAIM, (t + LEASE, t + LEASE, t, limit)).fetchall()
        for r in rows:
            try:
                _run_job(_Job(*r))
            except Exception as ex:  # noqa: BLE001 - ein Auftrag haelt die anderen nicht auf (Lease laeuft ab)
                print(f"jobs: Auftrag {r[0]}: {type(ex).__name__}: {ex}", flush=True)
                traceback.print_exc()
        return len(rows)


def cleanup():
    """Stuendlich: Aufbewahrung (Klartext 30, alles 90 Tage nach Ende), nicht freigegebene Auftraege mit Frist."""
    t = now()
    with acting(None), _db() as c, c:
        gone = c.execute("DELETE FROM job WHERE ended_at < ?", (t - KEEP,)).rowcount
        old = "SELECT id FROM job WHERE ended_at < ?"
        c.execute(f"UPDATE job_step SET args=NULL, result=NULL WHERE job_id IN ({old}) AND (args IS NOT NULL OR result IS NOT NULL)",
                  (t - KEEP_TEXT,))
        c.execute(f"UPDATE job_change SET before=NULL, after=NULL WHERE job_id IN ({old}) AND (before IS NOT NULL OR after IS NOT NULL)",
                  (t - KEEP_TEXT,))
        c.execute("UPDATE job SET plan='{}'::jsonb WHERE ended_at < ? AND plan <> '{}'::jsonb", (t - KEEP_TEXT,))
        late = 0
        for (jid,) in c.execute("SELECT id FROM job WHERE state='wartet_ok' AND due_at IS NOT NULL AND due_at < ?", (t,)).fetchall():
            late += _to(c, jid, "wartet_ok", "abgebrochen", t, reason="abgelaufen")
    return gone + late


_wake = threading.Event()
_thread = {"t": None, "stop": False}


def wake():
    _wake.set()


def _loop():
    import time
    last = 0.0
    while not _thread["stop"]:
        n = 0
        try:
            n = run_once()
            if time.time() - last > 3600:
                last = time.time()
                cleanup()
        except Exception as ex:  # noqa: BLE001 - Datenbank kurz weg: spaeter weiter
            print(f"jobs: {type(ex).__name__}: {ex}", flush=True)
            time.sleep(5)
        if not n:
            _wake.wait(5.0)
            _wake.clear()


def start():
    if RESTORE_TEST:
        return
    if _thread["t"] is None or not _thread["t"].is_alive():
        _thread["stop"] = False
        _thread["t"] = threading.Thread(target=_loop, name="saga-jobs", daemon=True)
        _thread["t"].start()


def stop():
    _thread["stop"] = True
    _wake.set()
    _thread["t"] = None


testclock.on_tick(run_once)                      # /_test/tick: faellige Auftraege sofort (nur mit Testhaken)
