"""Auftrags-Maschine der Kollegin (2.6, Bugs-DB 2266 K-A2, Konzept K2/K4): saga/jobs.py im Prozess gegen die Datenbank
aus DATABASE_URL, Zeit ueber die Testuhr (testclock.set_clock), Arten test.echo/test.task/test.wait/test.block
(tests/kollegin_testarten.py), Fake-Modell nur zum Zaehlen (/_count).

Prueft: 7 Zustaende, 4 Gruppen (Fertig = erledigt + abgebrochen + zurueckgenommen), jeder verbotene Uebergang ValueError ·
Anlegen (Herkunft, freies kind, Einheit, genau eine lokale Person, Akteur-Spalten) · Freigabe nur mit plan_v + plan_hash,
zwei gleichzeitig = eine · Frist genau until, auch ueber die Zeitumstellung im Oktober · Warten auf task.completed und
note.saved ohne Modellaufruf, signal = eine Abfrage · Idempotenz (Wiederaufnahme mit Stempel ohne Wiederholung, ohne
Stempel wiederholt, zweiter Versuch schreibt nicht) · Lease (zwei Threads, Uebernahme nach +61 s, genau eine Wirkung) ·
RT-28 (6. Auftrag abgelehnt, 50 gleichzeitig -> 5, keiner doppelt) · RT-36-Teil pause_all/Einwilligung · Abbrechen ·
Stempel ohne Akteur = keine Abfrage, scheiternder Stempel = kein Schreiben · Leerlauf-Takt = eine Abfrage ·
/_test/tick ohne Haken 404, mit Haken Takt · Aufbewahrung · Kontoloeschung.
K-A6 Runner ai.task (saga/ai_task.py): run_step uebernimmt den Versuch (zweiter Versuch schreibt nicht, nie ohne Versuch),
zwei Worker ueber run_step = eine Wirkung · JobTok.issued = Freigabe (Passwortwechsel danach -> wartet_dich, Fortsetzen
-> weiter) · "ueberall abmelden" haelt an · JobTok-Ablauf gegen die Testuhr · Plan nach der Freigabe veraendert ->
fehlgeschlagen · Klasse passt nicht · lesender Schritt -> job_event 'read' (ai_ctx.record, nur Kennungen).
Wegwerf-Konten kj-*; stellt die KI-Einstellung und die Testuhr wieder her.
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_jobs.py"""
import json
import os
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import db

db.wait_ready()
from db import connect  # noqa: E402
import kollegin_testarten as KT  # noqa: E402
from saga import actor, bus, jobs, tasks, testclock, userdata  # noqa: E402
from saga.actor import Actor, acting  # noqa: E402

A, B, C = "kj-anna", "kj-bert", "kj-cora"
T0 = 1893456000.0                       # 2030-01-01 00:00 UTC
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def clock(t):
    testclock.set_clock({"freeze": True})
    testclock.set_clock({"set": t})


def adv(s):
    testclock.set_clock({"advance": s})


def mk(uid=A, kind="test.echo", steps=None, **kw):
    return jobs.create(uid, kind, steps=steps or [{"tool": "echo", "args": {"x": 1}}], **kw)


def go(jid, uid=A):
    s = jobs.state_of(jid)
    return jobs.approve(jid, uid, s["plan_v"], s["plan_hash"])


class Count:
    """Zaehlt db.Conn.execute (alle Verbindungen) im with-Block."""

    def __enter__(self):
        self.n, self.sql = 0, []
        self.orig = db.Conn.execute
        me = self

        def ex(conn, sql, args=()):
            me.n += 1
            me.sql.append(sql[:80])
            return me.orig(conn, sql, args)
        db.Conn.execute = ex
        return self

    def __exit__(self, *a):
        db.Conn.execute = self.orig
        return False


def task_count(uid=A):
    return KT.q("SELECT COUNT(*) FROM task WHERE uid=?", (uid,))[0][0]


def changes(jid):
    return KT.q("SELECT step_n, target FROM job_change WHERE job_id=? ORDER BY id", (jid,))


# --- Zustaende ---------------------------------------------------------------------------------------------------
def part_states():
    ok("7 Zustaende in fester Reihenfolge", jobs.STATES == ("wartet_ok", "laeuft", "wartet_dich", "erledigt", "fehlgeschlagen",
                                                            "abgebrochen", "zurueckgenommen"))
    ok("Unterzustaende nur fuer laeuft", jobs.SUBS == ("wartet_frist", "wartet_ereignis", "antwort_da", "wartet_modell"))
    ok("4 Gruppen: Fertig = erledigt + abgebrochen + zurueckgenommen, Hat nicht geklappt = fehlgeschlagen",
       jobs.GROUPS == {"wartet_auf_dich": ("wartet_ok", "wartet_dich"), "laeuft": ("laeuft",),
                       "fertig": ("erledigt", "abgebrochen", "zurueckgenommen"), "nicht_geklappt": ("fehlgeschlagen",)})
    ok("jeder Zustand in genau einer Gruppe", sorted(s for g in jobs.GROUPS.values() for s in g) == sorted(jobs.STATES))
    allowed = {("wartet_ok", "laeuft"), ("wartet_ok", "abgebrochen"), ("laeuft", "wartet_dich"), ("laeuft", "erledigt"),
               ("laeuft", "fehlgeschlagen"), ("laeuft", "abgebrochen"), ("wartet_dich", "laeuft"), ("wartet_dich", "abgebrochen"),
               ("wartet_dich", "fehlgeschlagen"), ("erledigt", "zurueckgenommen")}
    wrong = []
    for a in jobs.STATES + ("quatsch",):
        for b in jobs.STATES + ("quatsch",):
            try:
                jobs.check_transition(a, b)
                good = True
            except ValueError:
                good = False
            if good != ((a, b) in allowed):
                wrong.append((a, b, good))
    ok("Uebergaenge: genau die 10 erlaubten, alle anderen ValueError (auch unbekannte, gleiche)", not wrong, wrong)


# --- Anlegen -----------------------------------------------------------------------------------------------------
def part_create():
    bad = []
    for kw in (dict(uid="kj-anna@saga.example"), dict(uid="@kj-anna@saga.example"), dict(uid="svc:kollegin"), dict(uid=""),
               dict(uid=[A, B]), dict(uid="kj-niemand"), dict(kind="Gross"), dict(kind="a..b"), dict(source="mail"),
               dict(actor="svc:fremd"), dict(unit="Euro!"), dict(steps=[]), dict(steps=[{"tool": "x"}] * 21),
               dict(steps=[{"tool": "x", "klass": "admin"}]), dict(steps=[{"tool": "x", "cond": "vielleicht"}]),
               dict(steps=[{"tool": "x", "args": [1]}]), dict(plan=[1])):
        args = dict(uid=A, kind="test.echo", steps=[{"tool": "echo"}])
        args.update(kw)
        try:
            jobs.create(args.pop("uid"), args.pop("kind"), **args)
            bad.append(kw)
        except (ValueError, TypeError):
            pass
    ok("Anlegen abgelehnt: foederierte/fehlende/mehrere Besitzer, Dienst, kind, Herkunft, Dienst, Einheit, Schritte, Plan", not bad, bad)
    jid = jobs.create(A, "mailbox.test", source="thunderbird", title="Post‮ sortieren", plan={"ziel": "x"},
                      steps=[{"tool": "read_mail", "klass": "read", "undo_class": "final", "args": {"n": 1}, "cond": "if_yes", "client": True},
                             {"tool": "create_task"}], unit="min", units_est=12)
    r = KT.q("SELECT uid, kind, source, state, sub, next_at, unit, units_est, units, actor_kind, actor, on_behalf_of, plan_v, title, "
             "plan_hash FROM job WHERE id=?", (jid,))[0]
    ok(f"freies kind, Herkunft, Einheit, Akteur service/svc:kollegin im Auftrag der Person ({r[:13]})",
       r[:13] == (A, "mailbox.test", "thunderbird", "wartet_ok", None, None, "min", 12, 0, "service", "svc:kollegin", A, 1))
    ok("Titel ohne Richtungs-Steuerzeichen", r[13] == "Post sortieren", r[13])
    ok("plan_hash = sha256 ueber Plan + Schritte (stabil)", r[14] == jobs.plan_hash({"ziel": "x"}, jobs._norm_steps(
        [{"tool": "read_mail", "klass": "read", "undo_class": "final", "args": {"n": 1}, "cond": "if_yes", "client": True},
         {"tool": "create_task"}])) and len(r[14]) == 64)
    st = KT.q("SELECT n, tool, klass, undo_class, args, cond, client, state, idem_key FROM job_step WHERE job_id=? ORDER BY n", (jid,))
    ok("Schritte fest gespeichert (Klasse, Rueckgaengig-Klasse, Bedingung, Client-Schritt), noch ohne idem_key",
       st == [(0, "read_mail", "read", "final", {"n": 1}, "if_yes", 1, "offen", None), (1, "create_task", "own", "undo", None, None, 0, "offen", None)], st)
    s = jobs.state_of(jid)
    ok("Gruppe 'Wartet auf dich' vor der Freigabe", s["group"] == "wartet_auf_dich" and s["state"] == "wartet_ok")
    ok("Takt holt nicht freigegebene Auftraege nicht", jobs.run_once() == 0 and jobs.state_of(jid)["state"] == "wartet_ok")
    ok("Freigabe mit falschem plan_v/plan_hash/fremder Person: nein",
       not jobs.approve(jid, A, 2, s["plan_hash"]) and not jobs.approve(jid, A, 1, "0" * 64) and not jobs.approve(jid, B, 1, s["plan_hash"]))
    ok("Freigabe mit passendem Plan: ja, zweite: nein", go(jid) and not go(jid))
    KT.run_all()
    s = jobs.state_of(jid)
    ok(f"Art ohne Runner: fehlgeschlagen 'unbekannte_art' ({s['state']}, {s['reason']})",
       s["state"] == "fehlgeschlagen" and s["reason"] == "unbekannte_art" and s["group"] == "nicht_geklappt")
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=1 WHERE username=?", (C,))
    try:
        jobs.create(C, "test.echo", steps=[{"tool": "echo"}])
        ok("gesperrtes Konto kann keinen Auftrag anlegen", False)
    except ValueError:
        ok("gesperrtes Konto kann keinen Auftrag anlegen", True)
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=0 WHERE username=?", (C,))
    # zwei Geraete druecken gleichzeitig "Passt, los"
    jid = mk()
    s = jobs.state_of(jid)
    out, bar = [], threading.Barrier(4)

    def press():
        bar.wait()
        out.append(jobs.approve(jid, A, s["plan_v"], s["plan_hash"]))
    th = [threading.Thread(target=press) for _ in range(4)]
    [x.start() for x in th]
    [x.join() for x in th]
    ok(f"vier gleichzeitige Freigaben: genau eine ({out})", sorted(out) == [False, False, False, True])
    jobs.cancel(jid, A)


def part_echo():
    KT.CALLS["echo"] = 0
    jid = mk(steps=[{"tool": "echo", "args": {"units": 3}}, {"tool": "echo", "args": {"units": 4}}], unit="tok")
    go(jid)
    ok("nach der Freigabe: Laeuft, sofort faellig", jobs.state_of(jid)["group"] == "laeuft" and jobs.state_of(jid)["next_at"] == testclock.now())
    n1 = jobs.run_once()
    ok("ein Takt = ein Schritt", n1 == 1 and [s["state"] for s in jobs.steps_of(jid)] == ["erledigt", "offen"])
    KT.run_all()
    s = jobs.state_of(jid)
    ok(f"zwei Schritte -> erledigt, Gruppe Fertig, Einheiten in freier Einheit ({s})",
       s["state"] == "erledigt" and s["group"] == "fertig" and s["units"] == 7 and s["unit"] == "tok" and s["next_at"] is None)
    ok("jeder Schritt genau einmal", KT.CALLS["echo"] == 2)
    keys = [x["idem_key"] for x in jobs.steps_of(jid)]
    ok(f"idem_key = job:schritt:plan_v ({keys})", keys == [f"{jid}:0:1", f"{jid}:1:1"])
    ev = KT.q("SELECT kind, data FROM job_event WHERE job_id=? ORDER BY id", (jid,))
    ok("Protokoll: Zustaende + Schritte, nur Kennungen", [e[0] for e in ev] == ["state", "state", "step", "step", "state"]
       and ev[-1][1].get("to") == "erledigt" and all(set(e[1]) <= {"frm", "to", "reason", "steps", "n", "outcome"} for e in ev), ev)
    r = KT.q("SELECT ended_at, lease_until FROM job WHERE id=?", (jid,))[0]
    ok("Ende gesetzt, keine Lease mehr", r[0] == testclock.now() and r[1] is None, r)
    ok("Zustand eines unbekannten Auftrags: None", jobs.state_of(-5) is None)


def part_frist():
    KT.CALLS["wait"] = 0
    t = testclock.now()
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": "task:0", "until": t + 3600}}])
    go(jid)
    jobs.run_once()
    s = jobs.state_of(jid)
    ok(f"wartet auf Ereignis: laeuft/wartet_ereignis, naechster Takt = Frist ({s['sub']})",
       s["state"] == "laeuft" and s["sub"] == "wartet_ereignis" and s["next_at"] == t + 3600)
    adv(3599)
    ok("eine Sekunde vor der Frist: nichts faellig", jobs.run_once() == 0)
    adv(1)
    jobs.run_once()
    st = jobs.steps_of(jid)
    ok(f"genau zur Frist: Runner sieht timed_out ({st[0]['result']})",
       jobs.state_of(jid)["state"] == "erledigt" and st[0]["result"].get("timeout") is True and st[0]["result"]["at"] == t + 3600)
    ok("Warten kostet keine Runner-Aufrufe zwischendurch", KT.CALLS["wait"] == 2)
    # Zeitumstellung: Sa 24.10.2026 12:00 Sommerzeit -> "morgen 12:00" = So 25.10. 12:00 Winterzeit = 25 h spaeter
    tz = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 24, 12, 0, tzinfo=tz).timestamp()
    until = datetime(2026, 10, 25, 12, 0, tzinfo=tz).timestamp()
    clock(start)
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": "task:0", "until": until}}])
    go(jid)
    jobs.run_once()
    clock(start + 24 * 3600)                     # 11:00 Winterzeit - eine naive "+24 h"-Uhr waere hier schon fertig
    n_early = jobs.run_once()
    clock(until - 1)
    n_late = jobs.run_once()
    clock(until)
    jobs.run_once()
    r = jobs.steps_of(jid)[0]["result"] or {}
    ok(f"Zeitumstellung Oktober: Frist 12:00 Ortszeit bleibt 12:00 (25 h), nicht 24 h ({n_early}, {n_late}, "
       f"{datetime.fromtimestamp(r.get('at', 0), tz):%H:%M %Z})",
       n_early == 0 and n_late == 0 and r.get("at") == until and until - start == 25 * 3600 and
       datetime.fromtimestamp(r["at"], tz).hour == 12)
    clock(T0)


def part_event():
    KT.CALLS["wait"] = 0
    KT.fake("POST", "/_reset")
    tid = json.loads(tasks.task_add(A, {"text": "Grillkohle kaufen"}).body)["id"]
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": f"task:{tid}", "until": T0 + 7 * 86400}},
                                      {"tool": "echo"}])
    go(jid)
    jobs.run_once()
    for _ in range(3):
        adv(3600)
        jobs.run_once()
    ok("waehrend des Wartens: kein weiterer Runner-Aufruf", KT.CALLS["wait"] == 1)
    with Count() as cnt:
        tasks.task_update(A, tid, {"done": True})
    w = KT.q("SELECT hit_at FROM job_wait WHERE job_id=?", (jid,))
    ok(f"task.completed -> Treffer im Index, Auftrag sofort faellig ({w})",
       w and w[0][0] == testclock.now() and jobs.state_of(jid)["next_at"] == testclock.now())
    sig = [s for s in cnt.sql if s.startswith("WITH w AS (UPDATE job_wait")]
    ok(f"signal = genau eine Abfrage beim Abhaken ({len(sig)})", len(sig) == 1)
    jobs.run_once()
    st = jobs.steps_of(jid)
    ok(f"Runner sieht den Treffer ({st[0]['result']})", st[0]["result"] == {"hit": f"task:{tid}", "n": 1} and st[1]["state"] == "offen")
    KT.run_all()
    ok("danach erledigt, Index leer", jobs.state_of(jid)["state"] == "erledigt" and not KT.q("SELECT 1 FROM job_wait WHERE job_id=?", (jid,)),
       (jobs.state_of(jid), jobs.steps_of(jid)))
    ok(f"Warten + Ereignis: 0 Modellaufrufe (Fake-KI /_count {KT.fake('GET', '/_count')})", KT.fake("GET", "/_count")["n"] == 0)
    # Widget/App (/api/app/tasks/{tid}/done) nutzt nur tasks._task_done - auch dort muss task.completed kommen
    tid = json.loads(tasks.task_add(A, {"text": "Widget"}).body)["id"]
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": f"task:{tid}", "until": T0 + 7 * 86400}}])
    go(jid)
    jobs.run_once()
    src = open(os.path.join(os.path.dirname(tasks.__file__), "app_api.py"), encoding="utf-8").read()
    with connect() as c, c:
        r = c.execute("SELECT text, done, due, prio, repeat, rep_anchor, next_id FROM task WHERE id=?", (tid,)).fetchone()
        tasks._task_done(c, A, tid, True, r[1], r[0], r[2], r[3], r[4], r[5], r[6], datetime.now().date())
    jobs.run_once()
    ok("Abhaken ueber den gemeinsamen Weg (Widget/App) weckt ebenfalls (task.completed in _task_done)",
       "_task_done(c, uid, tid" in src and jobs.state_of(jid)["state"] == "erledigt")
    # note.saved
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "note_saved", "ref": "note:424242", "until": T0 + 86400}}])
    go(jid)
    jobs.run_once()
    with connect() as c, c:
        bus.fire("note.saved", c, note_id=424243, owner=A, by=A, updated=testclock.now(), origin="web")
    ok("andere Seite weckt nicht", jobs.state_of(jid)["next_at"] == T0 + 86400)
    with connect() as c, c:
        bus.fire("task.completed", c, uid=A, task_id=424242)
    ok("anderes Ereignis mit gleicher Kennung weckt nicht", jobs.state_of(jid)["next_at"] == T0 + 86400)
    with connect() as c, c:
        bus.fire("note.saved", c, note_id=424242, owner=A, by=A, updated=testclock.now(), origin="web")
    ok("note.saved der Seite -> faellig", jobs.state_of(jid)["next_at"] == testclock.now())
    jobs.run_once()
    ok("Seite gespeichert -> erledigt", jobs.state_of(jid)["state"] == "erledigt")
    with connect() as c, c:
        n = jobs.signal(c, "note_saved", "note:424242")
    ok("erledigte Auftraege weckt signal nicht mehr", n == 0)
    ok("Fake-KI weiter 0 Aufrufe", KT.fake("GET", "/_count")["n"] == 0)


def part_idem():
    KT.CALLS["task"] = 0
    n0 = task_count()
    jid = mk(kind="test.task", steps=[{"tool": "create_task", "args": {"text": "Idem 1"}}])
    go(jid)
    orig = jobs._finish
    jobs._finish = lambda *a, **k: None          # "Absturz" nach dem Schreiben: Abschluss fehlt
    try:
        jobs.run_once()
    finally:
        jobs._finish = orig
    ok("geschrieben, Schritt steht noch auf laeuft, Stempel da",
       task_count() == n0 + 1 and jobs.steps_of(jid)[0]["state"] == "laeuft" and len(changes(jid)) == 1, changes(jid))
    ok("vor Ablauf der Lease: nichts faellig", jobs.run_once() == 0)
    adv(61)
    jobs.run_once()
    st = jobs.steps_of(jid)
    ok(f"Wiederaufnahme mit Stempel: erledigt OHNE Wiederholung ({st[0]['result']}, Aufrufe {KT.CALLS['task']})",
       jobs.state_of(jid)["state"] == "erledigt" and KT.CALLS["task"] == 1 and st[0]["result"].get("resumed") is True)
    tid = KT.q("SELECT id FROM task WHERE uid=? AND text='Idem 1'", (A,))
    ok("2x ausgefuehrt -> 1 Aufgabe, 1 job_change mit ihrem Ziel", task_count() == n0 + 1 and len(tid) == 1 and
       changes(jid) == [(0, f"task:{tid[0][0]}")] and st[0]["result"]["changes"] == [f"task:{tid[0][0]}"], (changes(jid), tid))
    # Absturz VOR dem Schreiben: kein Stempel -> wiederholen
    KT.CALLS["task"] = 0
    jid = mk(kind="test.task", steps=[{"tool": "create_task", "args": {"text": "Idem 2"}}])
    go(jid)
    jobs.register("test.task", lambda ctx, job, step: None, KT.comp_task)
    jobs._finish = lambda *a, **k: None
    try:
        jobs.run_once()                          # Runner liefert nichts und schreibt nicht, Abschluss fehlt
    finally:
        jobs._finish = orig
        KT.register_all()
    adv(61)
    jobs.run_once()
    ok(f"Wiederaufnahme ohne Stempel: Schritt wiederholt, genau eine Aufgabe ({KT.CALLS['task']})",
       KT.CALLS["task"] == 1 and task_count() == n0 + 2 and len(changes(jid)) == 1 and jobs.state_of(jid)["state"] == "erledigt")
    # zweiter Versuch desselben Schritts darf nicht schreiben
    jid = mk(kind="test.task", steps=[{"tool": "create_task"}])
    go(jid)
    key = f"{jid}:0:1"
    with connect() as c, c:
        c.execute("UPDATE job_step SET state='laeuft', idem_key=? WHERE job_id=? AND n=0", (key, jid))
    with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="v1")):
        tasks.task_add(A, {"text": "Versuch 1"})
        tasks.task_add(A, {"text": "Versuch 1b"})          # derselbe Versuch darf mehrere Transaktionen
    try:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="v2")):
            tasks.task_add(A, {"text": "Versuch 2"})
        refused = False
    except jobs.StepTaken:
        refused = True
    ok("anderer Versuch desselben Schritts: Stempel verweigert, Aufgabe NICHT angelegt (atomar)",
       refused and not KT.q("SELECT 1 FROM task WHERE uid=? AND text='Versuch 2'", (A,)) and len(changes(jid)) == 2)
    jobs.cancel(jid, A)
    try:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="v1")):
            tasks.task_add(A, {"text": "nach Abbruch"})
        refused = False
    except jobs.StepTaken:
        refused = True
    ok("abgebrochener Auftrag: auch derselbe Versuch schreibt nicht mehr", refused and not KT.q("SELECT 1 FROM task WHERE text='nach Abbruch'"))
    try:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=987654321, step=0, idem="987654321:0:1")):
            tasks.task_add(A, {"text": "ohne Auftrag"})
        refused = False
    except jobs.StepTaken:
        refused = True
    ok("Schritt-Schluessel ohne Auftrag: kein Schreiben", refused and not KT.q("SELECT 1 FROM task WHERE text='ohne Auftrag'"))


def part_stamp_page():
    """Pruefer K-A2: neue Seite (pages.note_create, Werkzeug create_page) feuert nur notes.tree - auch das wird gestempelt;
    ein zweiter Versuch legt keine zweite Seite an. task.completed feuert je Abhaken genau einmal (Web/API und Widget)."""
    from saga import pages
    jid = mk(kind="test.task", steps=[{"tool": "create_page"}])
    go(jid)
    key = f"{jid}:0:1"
    with connect() as c, c:
        c.execute("UPDATE job_step SET state='laeuft', idem_key=? WHERE job_id=? AND n=0", (key, jid))
    with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="p1")):
        nid = json.loads(pages.note_create(A, {"title": "KJ Stempel Seite"}).body)["id"]
    ok(f"neue Seite unter einem Schritt: Stempel note:<id> ({changes(jid)})", (0, f"note:{nid}") in changes(jid))
    try:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="p2")):
            pages.note_create(A, {"title": "KJ Stempel Seite 2"})
        refused = False
    except jobs.StepTaken:
        refused = True
    ok("zweiter Versuch: keine zweite Seite (Stempel verweigert, alles zurueck)",
       refused and not KT.q("SELECT 1 FROM note WHERE uid=? AND title='KJ Stempel Seite 2'", (A,)))
    r = pages.note_create(A, {"title": "KJ Person Seite"})
    ok("ohne Akteur: Person legt Seite wie bisher an (kein Stempel)", r.status_code == 200 and len(changes(jid)) == 1)
    jobs.cancel(jid, A)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid=? AND title LIKE 'KJ % Seite%'", (A,))
    seen = []
    fn = lambda c, **d: seen.append(d.get("task_id"))      # noqa: E731
    bus.on("task.completed", fn)
    try:
        tid = json.loads(tasks.task_add(A, {"text": "KJ einmal"}).body)["id"]
        tasks.task_update(A, tid, {"done": True, "text": "KJ einmal!"})
        tasks.task_update(A, tid, {"done": True})
        tasks.task_update(A, tid, {"done": False})
    finally:
        bus._hooks["task.completed"].remove(fn)
    ok(f"task.completed je Abhaken genau einmal, erneutes Abhaken/Oeffnen ohne Ereignis ({seen})", seen == [tid])


def part_race():
    """Pruefer K-A2: zwei Versuche desselben Schritts schreiben GLEICHZEITIG (beide Transaktionen offen) - der zweite
    wartet am FOR UPDATE des Schritts und bekommt danach StepTaken; genau eine Wirkung."""
    jid = mk(kind="test.task", steps=[{"tool": "create_task"}])
    go(jid)
    key = f"{jid}:0:1"
    with connect() as c, c:
        c.execute("UPDATE job_step SET state='laeuft', idem_key=? WHERE job_id=? AND n=0", (key, jid))
    inside, release = threading.Event(), threading.Event()

    def hold(c, **d):
        a = actor.current()
        if a is not None and a.attempt == "r1":
            inside.set()
            release.wait(20)
    bus.on("task.created", hold)
    out = {}

    def run(att, text):
        try:
            with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt=att)):
                tasks.task_add(A, {"text": text})
            out[att] = "ok"
        except jobs.StepTaken:
            out[att] = "taken"
        except Exception as ex:  # noqa: BLE001
            out[att] = repr(ex)
    try:
        ta = threading.Thread(target=run, args=("r1", "KJ Wettlauf A"))
        ta.start()
        inside.wait(10)
        tb = threading.Thread(target=run, args=("r2", "KJ Wettlauf B"))
        tb.start()
        tb.join(1.0)
        waited = tb.is_alive()
        release.set()
        ta.join(20)
        tb.join(20)
    finally:
        release.set()
        bus._hooks["task.created"].remove(hold)
    ok(f"gleichzeitig: zweiter Versuch wartet am Schritt, dann StepTaken ({out}, gewartet {waited})",
       waited and out == {"r1": "ok", "r2": "taken"})
    ok("gleichzeitig: genau eine Aufgabe, ein Stempel",
       len(KT.q("SELECT 1 FROM task WHERE uid=? AND text LIKE 'KJ Wettlauf %'", (A,))) == 1 and len(changes(jid)) == 1)
    jobs.cancel(jid, A)


def part_stamp_noop():
    seen = []
    bus.on("kj.test", lambda c, **d: seen.append(d))
    with connect() as c, c:
        with Count() as n0:
            bus.fire("kj.test", c, note_id=1)
        with acting(Actor("service", "svc:bot", on_behalf_of=A)), Count() as n1:
            bus.fire("note.saved", c, note_id=-1)
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=1, step=0, idem="x:0:1")), Count() as n2:
            bus.fire("kj.test", c, note_id=1)
    ok(f"Stempel ohne Akteur / ohne idem / fuer Ereignisse ohne Wirkung: keine Abfrage ({n0.n}, {n1.n}, {n2.n})",
       n0.n == 0 and n2.n == 0 and not any("job_change" in s or "job_step" in s for s in n1.sql) and len(seen) == 2, (n1.sql,))
    ok("fire ohne Akteur: Zuhoerer laufen wie bisher", seen == [{"note_id": 1}, {"note_id": 1}])
    bus._hooks.pop("kj.test", None)
    old = bus._stamp
    bus.set_stamp(None)
    try:
        with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=1, step=0, idem="x:0:1")):
            tasks.task_add(A, {"text": "ohne Stempel"})
        refused = False
    except bus.StampMissing:
        refused = True
    finally:
        bus.set_stamp(old)
    ok("Schritt-Schluessel, aber kein Stempel angemeldet: nicht schreiben", refused and not KT.q("SELECT 1 FROM task WHERE text='ohne Stempel'"))


def part_idle():
    with Count() as n:
        for _ in range(5):
            jobs.run_once()
    ok(f"Leerlauf: hoechstens 1 DB-Abfrage je Takt ({n.n} in 5 Takten)", n.n <= 5, n.sql)


def part_lease():
    KT.reset_block()
    KT.CALLS["block"] = 0
    n0 = task_count()
    jid = mk(kind="test.block", steps=[{"tool": "create_task", "args": {"text": "Lease"}}])
    go(jid)
    got = {}
    ta = threading.Thread(target=lambda: got.setdefault("a", jobs.run_once()))
    ta.start()
    KT.BLOCK["in"].wait(10)
    got["b"] = jobs.run_once()
    ok(f"Worker A haelt den Auftrag, Worker B bekommt ihn nicht (SKIP LOCKED/Lease) ({got.get('b')})", got["b"] == 0)
    adv(61)
    got["b2"] = jobs.run_once()
    ok("nach +61 s uebernimmt B, fuehrt aus", got["b2"] == 1 and jobs.state_of(jid)["state"] == "erledigt" and task_count() == n0 + 1)
    KT.BLOCK["go"].set()
    ta.join(20)
    ok(f"A kommt spaeter zurueck: schreibt nicht, ueberschreibt nichts - genau eine Aufgabe, ein Stempel ({KT.CALLS['block']})",
       task_count() == n0 + 1 and len(changes(jid)) == 1 and jobs.state_of(jid)["state"] == "erledigt" and KT.CALLS["block"] == 2)
    r = KT.q("SELECT tries FROM job WHERE id=?", (jid,))[0][0]
    ok(f"tries zaehlt beide Uebernahmen ({r})", r == 2)


def part_rt28():
    for u in (A, B):
        KT.cleanup(u)
    ids = [mk(A, kind="test.task", steps=[{"tool": "create_task", "args": {"text": f"rt28-{i}"}}]) for i in range(5)]
    try:
        mk(A)
        ok("RT-28: 6. offener Auftrag abgelehnt", False)
    except jobs.LimitError:
        ok("RT-28: 6. offener Auftrag abgelehnt", True)
    jobs.cancel(ids[0], A)
    ok("nach Abbruch (Fertig) wieder Platz", isinstance(mk(A), int))
    out, bar = {"ok": [], "limit": 0, "other": []}, threading.Barrier(50)

    def one(i):
        bar.wait()
        try:
            out["ok"].append(jobs.create(B, "test.task", steps=[{"tool": "create_task", "args": {"text": f"b-{i}"}}]))
        except jobs.LimitError:
            out["limit"] += 1
        except Exception as ex:  # noqa: BLE001
            out["other"].append(repr(ex))
    th = [threading.Thread(target=one, args=(i,)) for i in range(50)]
    [x.start() for x in th]
    [x.join() for x in th]
    act = KT.q("SELECT COUNT(*) FROM job WHERE uid=? AND state IN ('wartet_ok','laeuft','wartet_dich')", (B,))[0][0]
    ok(f"RT-28: 50 gleichzeitig -> genau 5 angelegt, 45 abgelehnt ({len(out['ok'])}, {out['limit']}, {out['other'][:2]})",
       len(out["ok"]) == 5 and out["limit"] == 45 and act == 5)
    for jid in out["ok"]:
        go(jid, B)
    KT.run_all()
    texts = sorted(r[0] for r in KT.q("SELECT text FROM task WHERE uid=?", (B,)))
    ok(f"alle 5 erledigt, keiner doppelt ({texts})", len(texts) == 5 and len(set(texts)) == 5 and
       all(jobs.state_of(j)["state"] == "erledigt" for j in out["ok"]))
    for jid in ids[1:]:
        jobs.cancel(jid, A)
    KT.cleanup(A)


def part_pause():
    KT.fake("POST", "/_reset")
    t = testclock.now()
    ids = [mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": f"task:-{i}", "until": t + 600}}]) for i in (1, 2)]
    for jid in ids:
        go(jid)
    jobs.run_once()
    n = jobs.pause_all(A, "einwilligung")
    ok(f"RT-36: pause_all haelt alle laufenden an ({n})", n == 2 and all(jobs.state_of(j)["state"] == "wartet_dich" and
                                                                        jobs.state_of(j)["reason"] == "einwilligung" for j in ids))
    adv(700)
    ok("danach kein Takt fuer sie (Frist laeuft ins Leere), 0 Modellaufrufe",
       jobs.run_once() == 0 and KT.fake("GET", "/_count")["n"] == 0)
    jobs.consent_ok, orig = (lambda uid: False), jobs.consent_ok
    try:
        ok("Fortsetzen: wieder laeuft", jobs.resume(ids[0], A) and jobs.state_of(ids[0])["state"] == "laeuft")
        KT.CALLS["wait"] = 0
        jobs.run_once()
        s = jobs.state_of(ids[0])
        ok(f"ohne Einwilligung: vor dem Schritt wieder wartet_dich, Runner nicht aufgerufen ({s['reason']})",
           s["state"] == "wartet_dich" and s["reason"] == "einwilligung" and KT.CALLS["wait"] == 0)
    finally:
        jobs.consent_ok = orig
    for jid in ids:
        jobs.cancel(jid, A)
    # Pause waehrend eines laufenden Schritts: der Schritt darf danach nicht mehr schreiben
    KT.reset_block()
    n0 = task_count()
    jid = mk(kind="test.block", steps=[{"tool": "create_task", "args": {"text": "waehrend Pause"}}])
    go(jid)
    th = threading.Thread(target=jobs.run_once)
    th.start()
    KT.BLOCK["in"].wait(10)
    jobs.pause_all(A, "einwilligung")
    KT.BLOCK["go"].set()
    th.join(20)
    st = jobs.steps_of(jid)[0]
    ok(f"Pause mitten im Schritt: nichts geschrieben, Schritt wieder offen ({st['state']})",
       task_count() == n0 and not changes(jid) and st["state"] == "offen" and jobs.state_of(jid)["state"] == "wartet_dich")
    jobs.cancel(jid, A)


def part_cancel():
    t = testclock.now()
    jid = mk(kind="test.wait", steps=[{"tool": "wait", "args": {"on": "task_done", "ref": "task:-9", "until": t + 600}}])
    go(jid)
    jobs.run_once()
    ok("Abbrechen durch eine andere Person: nein", not jobs.cancel(jid, B))
    ok("Abbrechen durch die Besitzerin: ja", jobs.cancel(jid, A, "person"))
    s = jobs.state_of(jid)
    ok(f"abgebrochen: Gruppe Fertig, nichts mehr faellig, Index leer ({s})",
       s["state"] == "abgebrochen" and s["group"] == "fertig" and s["next_at"] is None and
       not KT.q("SELECT 1 FROM job_wait WHERE job_id=?", (jid,)))
    ok("zweites Abbrechen / Fortsetzen / Freigeben: nein", not jobs.cancel(jid, A) and not jobs.resume(jid, A) and not go(jid))
    done = mk()
    go(done)
    KT.run_all()
    ok("erledigt laesst sich nicht abbrechen", not jobs.cancel(done, A) and jobs.state_of(done)["state"] == "erledigt")
    ok("Ruecknahme nur fuer Arten mit compensate", not jobs.undo(done, A))


def part_undo():
    n0 = task_count()
    jid = mk(kind="test.task", steps=[{"tool": "create_task", "args": {"text": "u1"}}, {"tool": "create_task", "args": {"text": "u2"}}])
    go(jid)
    KT.run_all()
    ok("zwei Aufgaben angelegt", task_count() == n0 + 2 and len(changes(jid)) == 2)
    ok("Ruecknahme durch andere Person: nein", not jobs.undo(jid, B))
    ok("Ruecknahme angestossen", jobs.undo(jid, A) and not jobs.undo(jid, A))
    KT.run_all()
    s = jobs.state_of(jid)
    ok(f"zurueckgenommen (Gruppe Fertig), Aufgaben weg, Stempel als zurueckgenommen markiert ({s['state']})",
       s["state"] == "zurueckgenommen" and s["group"] == "fertig" and task_count() == n0 and
       all(r[0] is not None for r in KT.q("SELECT undone_at FROM job_change WHERE job_id=?", (jid,))))


def part_rt07():
    n0 = task_count()
    for what in ("konto_gesperrt", "ai"):
        jid = mk(kind="test.task", steps=[{"tool": "create_task", "args": {"text": what}}])
        go(jid)
        if what == "konto_gesperrt":
            with connect() as c, c:
                c.execute("UPDATE users SET disabled=1 WHERE username=?", (A,))
        else:
            prev = KT.ai_on(groups=["kj-niemand"])
        KT.CALLS["task"] = 0
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"RT-07 {what}: wartet_dich mit Grund, nichts geschrieben ({s['state']}, {s['reason']})",
           s["state"] == "wartet_dich" and s["reason"] == what and task_count() == n0 and KT.CALLS["task"] == 0)
        if what == "konto_gesperrt":
            with connect() as c, c:
                c.execute("UPDATE users SET disabled=0 WHERE username=?", (A,))
        else:
            KT.ai_restore(prev)
        jobs.resume(jid, A)
        KT.run_all()
        ok(f"RT-07 {what}: nach Behebung fortgesetzt, genau eine Aufgabe", jobs.state_of(jid)["state"] == "erledigt" and task_count() == n0 + 1)
        n0 += 1
    prev = KT.ai_on(caps={"ai_act": ["kj-niemand"]})
    try:
        jid = mk(kind="test.echo", steps=[{"tool": "echo", "klass": "read"}, {"tool": "send", "klass": "out"}])
        go(jid)
        KT.run_all()
        s = jobs.state_of(jid)
        ok(f"Aussen-Schritt ohne ai_act: Lesen ja, dann wartet_dich 'ai_act' ({s['reason']}, {[x['state'] for x in jobs.steps_of(jid)]})",
           s["state"] == "wartet_dich" and s["reason"] == "ai_act" and [x["state"] for x in jobs.steps_of(jid)] == ["erledigt", "offen"])
        jobs.cancel(jid, A)
    finally:
        KT.ai_restore(prev)


def part_tick():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    class From:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                scope = dict(scope, client=("127.0.0.1", 50000))
            return await self.app(scope, receive, send)
    app = FastAPI()
    app.include_router(testclock.router)
    TOK = "k" * 40
    cl = TestClient(From(app), headers={"X-Saga-Test": TOK})
    old = {k: os.environ.pop(k, None) for k in ("SAGA_TEST_HOOKS", "SAGA_TEST_TOKEN")}
    try:
        jid = mk()
        go(jid)
        ok("/_test/tick ohne Haken: 404, Auftrag unberuehrt",
           cl.post("/_test/tick").status_code == 404 and jobs.state_of(jid)["state"] == "laeuft")
        os.environ.update(SAGA_TEST_HOOKS="1", SAGA_TEST_TOKEN=TOK)
        r = cl.post("/_test/tick")
        ok(f"/_test/tick mit Haken: Takt der Auftraege ({r.status_code} {r.json() if r.status_code == 200 else ''})",
           r.status_code == 200 and jobs.state_of(jid)["state"] == "erledigt" and jobs.run_once in testclock._TICK)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def part_retention():
    t = testclock.now()
    old = mk(steps=[{"tool": "echo", "args": {"geheim": "Klartext"}}], plan={"wortlaut": "Klartext"})
    go(old)
    KT.run_all()
    mid = mk()
    go(mid)
    KT.run_all()
    late = mk(due_at=t + 10)
    with connect() as c, c:
        c.execute("UPDATE job SET ended_at=? WHERE id=?", (t - 31 * 86400, old))
        c.execute("UPDATE job SET ended_at=? WHERE id=?", (t - 91 * 86400, mid))
    adv(11)
    jobs.cleanup()
    p = KT.q("SELECT plan FROM job WHERE id=?", (old,))
    a = KT.q("SELECT args, result FROM job_step WHERE job_id=?", (old,))
    ok(f"30 Tage nach Ende: Klartext weg, Auftrag und Protokoll bleiben ({p}, {a})", p == [({},)] and a == [(None, None)]
       and KT.q("SELECT COUNT(*) FROM job_event WHERE job_id=?", (old,))[0][0] > 0)
    ok("90 Tage nach Ende: ganz weg (mit Schritten, Protokoll)", not KT.q("SELECT 1 FROM job WHERE id=?", (mid,)) and
       not KT.q("SELECT 1 FROM job_event WHERE job_id=?", (mid,)) and not KT.q("SELECT 1 FROM job_step WHERE job_id=?", (mid,)))
    s = jobs.state_of(late)
    ok(f"nicht freigegeben, Frist vorbei: abgebrochen 'abgelaufen' ({s['state']}, {s['reason']})",
       s["state"] == "abgebrochen" and s["reason"] == "abgelaufen")


def part_delete():
    import auth
    KT.users("kj-weg")
    jid = mk("kj-weg", kind="test.task", steps=[{"tool": "create_task"}])
    go(jid, "kj-weg")
    KT.run_all()
    ok("Konto mit Auftrag, Schritt, Stempel, Protokoll", len(changes(jid)) == 1)
    ok("job in USER_TABLES", "job" in userdata.USER_TABLES)
    userdata.delete_user_data("kj-weg", drop_user=True)
    left = [t for t in ("job", "job_step", "job_event", "job_change", "job_wait")
            if KT.q(f"SELECT 1 FROM {t} WHERE {'id' if t == 'job' else 'job_id'}=?", (jid,))]
    ok(f"Kontoloeschung: Auftrag samt Kindern weg ({left})", not left and not auth.get_user("kj-weg"))


def part_thread():
    ok("Worker startet nicht bei SAGA_RESTORE_TEST (Quelltext)", "if RESTORE_TEST:\n        return" in open(jobs.__file__, encoding="utf-8").read())
    src = open(os.path.join(os.path.dirname(jobs.__file__), "..", "app.py"), encoding="utf-8").read()
    ok("app.py startet jobs nach mentions und stoppt ihn", src.index("jobs.start()") > src.index("mentions.start()") and "jobs.stop()" in src)
    seen = []
    jobs.register("test.akteur", lambda ctx, job, step: seen.append(actor.current()) or jobs.Outcome.done())
    jid = mk(kind="test.akteur")
    go(jid)
    with acting(Actor("person", B)):
        jobs.run_once()
        after = actor.current()
    a = seen[0] if seen else None
    ok(f"Runner laeuft als svc:kollegin im Auftrag mit Schritt-Schluessel ({a})",
       a is not None and (a.kind, a.actor, a.on_behalf_of, a.job_id, a.step, a.idem) == ("service", "svc:kollegin", A, jid, 0, f"{jid}:0:1"))
    ok("danach nichts Klebriges: Akteur des Aufrufers unveraendert", after is not None and after.actor == B)
    ok("im Worker ohne Akteur: nach dem Takt keiner", actor.current() is None)


def part_ai_task():
    """K-A6: Runner ai.task ueber tools.run_step (echte Werkzeuge, Testuhr angehalten auf T0)."""
    from saga import ai_ctx, ai_task, tools
    KT.cleanup(A)
    ok("Runner ai.task registriert, mit Wiederaufnahme", jobs._RUNNERS.get("ai.task") is ai_task.run and jobs._RESUME.get("ai.task") is ai_task.resume)

    def texts(prefix):
        return sorted(r[0] for r in KT.q("SELECT text FROM task WHERE uid=? AND text LIKE ?", (A, prefix + "%")))

    # (9) run_step: Versuch vom Aufrufer bzw. vom laufenden Akteur, nie ohne
    st = [{"tool": "create_task", "args": {"text": "KJ run_step"}}]
    jid = mk(kind="ai.task", steps=st)
    go(jid)
    key = f"{jid}:0:1"
    with connect() as c, c:
        c.execute("UPDATE job_step SET state='laeuft', idem_key=? WHERE job_id=? AND n=0", (key, jid))
    tok = tools.plan_tok(A, jid, st)
    job, step = {"id": jid, "uid": A}, {"n": 0, "tool": "create_task", "args": st[0]["args"], "idem_key": key}
    tools.run_step(job, step, tok, attempt="w1")

    def refused(**kw):
        try:
            tools.run_step(job, step, tok, **kw)
            return False
        except jobs.StepTaken:
            return True
    ok("run_step: anderer Versuch (attempt) desselben Schritts schreibt nicht (StepTaken)", refused(attempt="w2"))
    ok("run_step ohne Versuch und ohne Akteur: eigener Versuch, nie NULL -> schreibt nicht", refused())
    with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="w9")):
        r9 = refused()
    with acting(Actor("service", "svc:kollegin", on_behalf_of=A, job_id=jid, step=0, idem=key, attempt="w1")):
        r1 = refused()
    att = {r[0] for r in KT.q("SELECT attempt FROM job_change WHERE job_id=?", (jid,))}
    ok(f"run_step uebernimmt den Versuch des laufenden Akteurs (w9 nein, w1 ja; Stempel {att})", r9 and not r1 and att == {"w1"})
    jobs.cancel(jid, A)

    # (9) zwei Worker ueber run_step: A haelt die Schreib-Transaktion offen, Lease laeuft ab, B uebernimmt
    jid = mk(kind="ai.task", steps=[{"tool": "create_task", "args": {"text": "KJ zwei Worker"}}])
    go(jid)
    inside, release = threading.Event(), threading.Event()

    def hold(c, **d):
        a = actor.current()
        if a is not None and a.job_id == jid and not inside.is_set():
            inside.set()
            release.wait(20)
    bus.on("task.created", hold)
    try:
        ta = threading.Thread(target=jobs.run_once)
        ta.start()
        inside.wait(10)
        adv(jobs.LEASE + 1)
        tb = threading.Thread(target=jobs.run_once)
        tb.start()
        tb.join(1.0)
        release.set()
        ta.join(20)
        tb.join(20)
    finally:
        release.set()
        bus._hooks["task.created"].remove(hold)
    adv(ai_task.LANE_WAIT + 1)
    KT.run_all()
    att = KT.q("SELECT DISTINCT attempt FROM job_change WHERE job_id=?", (jid,))
    ok(f"zwei Worker ueber run_step: genau eine Aufgabe, Stempel von genau einem Versuch, erledigt ({texts('KJ zwei')}, {att})",
       texts("KJ zwei") == ["KJ zwei Worker"] and len(att) == 1 and jobs.state_of(jid)["state"] == "erledigt")

    # (1) issued = Freigabe: Passwortwechsel nach der Freigabe -> wartet_dich; Fortsetzen = neue Freigabe
    jid = mk(kind="ai.task", steps=[{"tool": "create_task", "args": {"text": "KJ nach Passwort"}}])
    go(jid)
    with connect() as c, c:
        c.execute("UPDATE users SET pw_changed=? WHERE username=?", (testclock.now() + 1, A))
    try:
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"Passwortwechsel nach der Freigabe: wartet_dich 'abgemeldet', nichts geschrieben ({s['state']}, {s['reason']})",
           s["state"] == "wartet_dich" and s["reason"] == "abgemeldet" and not texts("KJ nach Passwort"))
        adv(2)
        jobs.resume(jid, A)
        KT.run_all()
        ok("Fortsetzen nach dem Passwortwechsel (neue Freigabe): erledigt, genau eine Aufgabe",
           jobs.state_of(jid)["state"] == "erledigt" and texts("KJ nach Passwort") == ["KJ nach Passwort"])
    finally:
        with connect() as c, c:
            c.execute("UPDATE users SET pw_changed=NULL WHERE username=?", (A,))

    # (8) "ueberall abmelden" (userdata.revoke_user_logins - setzt weder logout_at noch pw_changed) haelt an
    jid = mk(kind="ai.task", steps=[{"tool": "create_task", "args": {"text": "KJ Abmelden 1"}},
                                    {"tool": "create_task", "args": {"text": "KJ Abmelden 2"}}])
    go(jid)
    jobs.run_once()
    jb = mk(uid=B, steps=[{"tool": "echo", "args": {"x": 1}}, {"tool": "echo", "args": {"x": 2}}])   # Pruefer: fremder Auftrag
    go(jb, B)
    userdata.revoke_user_logins(A)
    sb = jobs.state_of(jb)
    ok(f"ueberall abmelden haelt nur Auftraege dieser Person an (Auftrag von B: {sb['state']})", sb["state"] == "laeuft")
    jobs.cancel(jb, B)
    s = jobs.state_of(jid)
    ok(f"ueberall abmelden: laufender Auftrag wartet_dich 'abgemeldet', danach kein Schritt ({s['state']}, {s['reason']})",
       s["state"] == "wartet_dich" and s["reason"] == "abgemeldet" and jobs.run_once() == 0 and texts("KJ Abmelden") == ["KJ Abmelden 1"])
    jobs.resume(jid, A)
    KT.run_all()
    ok("nach dem Fortsetzen: zweiter Schritt, keiner doppelt", texts("KJ Abmelden") == ["KJ Abmelden 1", "KJ Abmelden 2"])

    # (2) JobTok-Ablauf gegen die Testuhr (T0 liegt in der Zukunft)
    import time
    ok("JobTok.expires gegen die Testuhr: echt noch gueltig, auf der Testuhr abgelaufen -> None",
       tools.job_tok(A, 4711, ["get_page"], [1], expires=time.time() + 3600) is None
       and tools.job_tok(A, 4711, ["get_page"], [1], expires=testclock.now() + 60) is not None)

    # Plan nach der Freigabe veraendert (Angaben verbogen) -> fehlgeschlagen, nichts geschrieben
    jid = mk(kind="ai.task", steps=[{"tool": "create_task", "args": {"text": "KJ Plan echt"}}])
    go(jid)
    with connect() as c, c:
        c.execute("UPDATE job_step SET args=CAST(? AS JSONB) WHERE job_id=?", (json.dumps({"text": "KJ Plan verbogen"}), jid))
    jobs.run_once()
    s = jobs.state_of(jid)
    ok(f"Plan nach der Freigabe veraendert: fehlgeschlagen 'plan_geaendert', nichts geschrieben ({s['state']}, {s['reason']})",
       s["state"] == "fehlgeschlagen" and s["reason"] == "plan_geaendert" and not texts("KJ Plan"))
    jid = mk(kind="ai.task", steps=[{"tool": "create_task", "klass": "read", "args": {"text": "KJ Klasse"}}])
    go(jid)
    jobs.run_once()
    s = jobs.state_of(jid)
    ok(f"Klasse im Plan passt nicht zum Werkzeug: fehlgeschlagen 'klasse' ({s['reason']})",
       s["state"] == "fehlgeschlagen" and s["reason"] == "klasse" and not texts("KJ Klasse"))

    # (5) lesender Schritt -> job_event 'read' (nur Kennungen); record direkt filtert
    jid = mk(kind="ai.task", steps=[{"tool": "list_tasks", "klass": "read"}, {"tool": "create_task", "args": {"text": "KJ nach Lesen"}}])
    go(jid)
    KT.run_all()
    ev = KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='read'", (jid,))
    ok(f"lesender Schritt: job_event 'read' mit Kennungen ({ev}), Ergebnis ohne Inhalt ({jobs.steps_of(jid)[0]['result']})",
       [e[0] for e in ev] == [{"read": ["tasks"]}] and jobs.state_of(jid)["state"] == "erledigt"
       and set(jobs.steps_of(jid)[0]["result"]) == {"tool", "chars"})
    with connect() as c, c:
        ai_ctx.record(c, job_id=jid, ctx=ai_ctx.Ctx(read=["note:5", "<b>Text</b>", "cal:2030-01-01/14", "note:5"]))
    ev = KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='read' ORDER BY id", (jid,))
    ok(f"ai_ctx.record(job_id=...): nur Kennungen, ohne Doppelte ({ev[-1][0]})", ev[-1][0] == {"read": ["note:5", "cal:2030-01-01/14"]})
    un = KT.q("SELECT COUNT(*) FROM job_event WHERE job_id=? AND kind='units' AND data->>'open'='1'", (jid,))[0][0]
    ok(f"Einheiten: je Schritt belegt und abgerechnet, nichts offen ({un})", un == 0
       and KT.q("SELECT COUNT(*) FROM job_event WHERE job_id=? AND kind='units'", (jid,))[0][0] == 2)
    KT.cleanup(A)


def main():
    KT.users(A, B, C)
    KT.register_all()
    prev = KT.ai_on()
    fk = KT.fake_start()
    for u in (A, B, C, "kj-weg"):
        KT.cleanup(u)
    clock(T0)
    try:
        part_states()
        part_create()
        part_echo()
        part_frist()
        clock(T0)
        part_event()
        part_idem()
        part_stamp_page()
        part_race()
        part_stamp_noop()
        part_idle()
        part_lease()
        part_rt28()
        part_pause()
        part_cancel()
        part_undo()
        part_rt07()
        part_tick()
        part_retention()
        part_delete()
        part_thread()
        part_ai_task()
    finally:
        fk.terminate()
        KT.ai_restore(prev)
        testclock.set_clock({"reset": True})
        for u in (A, B, C, "kj-weg"):
            KT.cleanup(u)
    print(f"\nKOLLEGIN-JOBS {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
