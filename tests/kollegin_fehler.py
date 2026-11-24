"""Abbruch- und Fehlerreihe der Kollegin, K-A-Teil (2.6, Bugs-DB 2266 K-A2, teil-tests.md §5): saga/jobs.py gegen die
Datenbank aus DATABASE_URL, Worker als UNTERPROZESS dieser Datei (--worker), der an den Absturzpunkten per SIGKILL stirbt.

Prueft: kill -9 an 4 Stellen (after_approve, before_write, after_write, mid_compensate) -> Wiederaufnahme am letzten
bestaetigten Schritt, genau eine Wirkung · zwei Worker-Prozesse gleichzeitig -> jeder Auftrag genau einmal · Rechte
entzogen (RT-07: Konto gesperrt, KI-Gruppe weg, Konto mitten im Schritt geloescht) -> wartet_dich bzw. weg, nichts
geschrieben · Modell weg (fake-500, Port zu) -> laeuft/wartet_modell mit Backoff 1/5/15/60 min, 0 Einheiten, nach 24 h
fehlgeschlagen, kommt das Modell zurueck -> erledigt.
K-A6 (Runner ai.task, saga/ai_task.py): Auftrag "Zeile aendern" (update_row mit rev) + "Aufgabe anlegen" (create_task),
kill -9 an Stelle 3 (after_write) in Schritt 1 bzw. 2 -> je Schritt Stempel genau eines Versuchs, genau eine Wirkung;
kill -9 mitten im Aufruf (mid_call: belegt, nicht abgerechnet) -> Belegung bei der Wiederaufnahme genau einmal frei,
Pool = Summe der Abrechnungen · Tageslimit -> zurueckgestellt 'budget' (vor dem ersten und mitten im Auftrag, laufender
Auftrag darf einen Schritt ueberziehen), nach Tageswechsel weiter · Seite nicht mehr geteilt -> wartet_dich
'kein_zugriff', nichts geschrieben · Ziel ausserhalb des freigegebenen Plans -> fehlgeschlagen 'plan_geaendert'.
Testuhr in der Zukunft (der Server-Worker der Testumgebung sieht diese Auftraege nie faellig); die Worker-Unterprozesse
bekommen denselben Versatz (KF_OFFSET).
Wegwerf-Konten kf-*; stellt KI-Einstellung und Testuhr wieder her. Fake-Modell auf HK_FAKEAI_PORT (Standard 18291).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_fehler.py"""
import os
import subprocess
import sys
import threading

import db

db.wait_ready()
from db import connect  # noqa: E402
import kollegin_testarten as KT  # noqa: E402
from saga import ai_task, ai_units, api_struct, api_v1, apitok, jobs, sharing, testclock, userdata  # noqa: E402

A, B = "kf-anna", "kf-bert"
TOK = "f" * 40
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def go(jid, uid=A):
    s = jobs.state_of(jid)
    return jobs.approve(jid, uid, s["plan_v"], s["plan_hash"])


def worker(mode, jid, crash=None):
    """Unterprozess: diese Datei mit --worker; crash = Absturzpunkt (SIGKILL) -> (rc, Ausgabe)."""
    env = dict(os.environ, SAGA_TEST_HOOKS="1", SAGA_TEST_TOKEN=TOK)
    env.pop("SAGA_TEST_CRASH", None)
    env["KF_OFFSET"] = repr(testclock._state["offset"])        # dieselbe Testuhr wie hier (K-A6-Teile: Zukunft)
    if crash:
        env["SAGA_TEST_CRASH"] = crash
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", mode, str(jid)], env=env, capture_output=True,
                       text=True, timeout=120)
    return p.returncode, (p.stdout + p.stderr)[-800:]


def worker_main(mode, jid):
    KT.register_all()
    ai_task.register()
    if os.environ.get("KF_OFFSET"):
        testclock.set_clock({"advance": float(os.environ["KF_OFFSET"])})
    if mode == "approve":
        go(jid)
        KT.run_all()
    elif mode == "run":
        KT.run_all()
    elif mode == "undo":
        jobs.undo(jid, A)
        KT.run_all()
    elif mode == "drain":
        n = 0
        for _ in range(200):
            k = jobs.run_once(limit=3)
            n += k
            if not k:
                break
        print("DRAIN", n, flush=True)
    sys.exit(0)


def texts(uid=A):
    return sorted(r[0] for r in KT.q("SELECT text FROM task WHERE uid=?", (uid,)))


def nchanges(jid):
    return KT.q("SELECT COUNT(*) FROM job_change WHERE job_id=?", (jid,))[0][0]


def real_clock():
    testclock.set_clock({"reset": True})


# --- kill -9 -----------------------------------------------------------------------------------------------------
def part_kill():
    for point, mode in (("after_approve", "approve"), ("before_write", "run"), ("after_write", "run")):
        real_clock()
        KT.cleanup(A)
        jid = jobs.create(A, "test.task", steps=[{"tool": "create_task", "args": {"text": f"k-{point}"}},
                                                 {"tool": "create_task", "args": {"text": f"k-{point}-2"}}])
        if mode == "run":
            go(jid)
        rc, out = worker(mode, jid, crash=point)
        ok(f"kill -9 {point}: Worker stirbt am Absturzpunkt (rc {rc})", rc == -9 and f"Absturzpunkt {point}" in out, out)
        st = [s["state"] for s in jobs.steps_of(jid)]
        before = texts()
        if point == "after_approve":
            ok(f"{point}: Freigabe bestaetigt, noch nichts geschrieben ({st}, {before})",
               jobs.state_of(jid)["state"] == "laeuft" and before == [] and st == ["offen", "offen"])
        elif point == "before_write":
            ok(f"{point}: Schritt beansprucht, nichts geschrieben ({st})", st[0] == "laeuft" and before == [] and nchanges(jid) == 0)
        else:
            ok(f"{point}: geschrieben + Stempel, Abschluss fehlt ({st}, {before})",
               st[0] == "laeuft" and before == [f"k-{point}"] and nchanges(jid) == 1)
        if point != "after_approve":
            ok(f"{point}: vor Ablauf der Lease nimmt niemand den Auftrag", jobs.run_once() == 0)
            testclock.set_clock({"advance": jobs.LEASE + 1})
        KT.run_all()
        ok(f"{point}: Wiederaufnahme -> erledigt, jede Aufgabe genau einmal ({texts()})",
           jobs.state_of(jid)["state"] == "erledigt" and texts() == [f"k-{point}", f"k-{point}-2"] and nchanges(jid) == 2)
        r = jobs.steps_of(jid)[0]["result"] or {}
        if point == "after_write":
            ok(f"{point}: Schritt 1 nicht wiederholt, aus dem Stempel erledigt ({r})", r.get("resumed") is True)
    # im Rueckgaengig
    real_clock()
    KT.cleanup(A)
    jid = jobs.create(A, "test.task", steps=[{"tool": "create_task", "args": {"text": "u-1"}}, {"tool": "create_task", "args": {"text": "u-2"}},
                                             {"tool": "create_task", "args": {"text": "u-3"}}])
    go(jid)
    KT.run_all()
    rc, out = worker("undo", jid, crash="mid_compensate")
    left = texts()
    ok(f"kill -9 mid_compensate: Worker stirbt nach der ersten Ruecknahme (rc {rc}, {left})",
       rc == -9 and "Absturzpunkt mid_compensate" in out and left == ["u-1", "u-2"] and jobs.state_of(jid)["state"] == "erledigt", out)
    ok("mid_compensate: vor Ablauf der Lease nimmt niemand den Auftrag", jobs.run_once() == 0)
    testclock.set_clock({"advance": jobs.LEASE + 1})
    KT.run_all()
    ev = KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='undo' AND data->>'change' IS NOT NULL", (jid,))
    per = sorted(e[0]["change"] for e in ev)
    ok(f"mid_compensate: Rest zurueckgenommen, jede Wirkung genau einmal ({len(per)} Ruecknahmen, {texts()})",
       jobs.state_of(jid)["state"] == "zurueckgenommen" and texts() == [] and len(per) == 3 and len(set(per)) == 3)


def part_two_workers():
    real_clock()
    KT.cleanup(A)
    KT.cleanup(B)
    ids = []
    for uid in (A, B):
        for i in range(5):
            jid = jobs.create(uid, "test.task", steps=[{"tool": "create_task", "args": {"text": f"{uid}-{i}-a"}},
                                                       {"tool": "create_task", "args": {"text": f"{uid}-{i}-b"}}])
            go(jid, uid)
            ids.append(jid)
    outs = []
    th = [threading.Thread(target=lambda: outs.append(worker("drain", 0))) for _ in range(2)]
    [x.start() for x in th]
    [x.join(150) for x in th]
    KT.run_all()
    alltexts = texts(A) + texts(B)
    ok(f"zwei Worker-Prozesse gleichzeitig: 10 Auftraege, 20 Aufgaben, keine doppelt ({len(alltexts)}, {[o[0] for o in outs]})",
       len(alltexts) == 20 and len(set(alltexts)) == 20 and all(jobs.state_of(j)["state"] == "erledigt" for j in ids)
       and all(o[0] == 0 for o in outs), [o[1][-200:] for o in outs])
    KT.cleanup(B)


# --- Rechte entzogen (RT-07) --------------------------------------------------------------------------------------
def part_rt07():
    real_clock()
    KT.cleanup(A)
    jid = jobs.create(A, "test.task", steps=[{"tool": "create_task", "args": {"text": "r-1"}}, {"tool": "create_task", "args": {"text": "r-2"}}])
    go(jid)
    jobs.run_once()
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=1 WHERE username=?", (A,))
    KT.run_all()
    s = jobs.state_of(jid)
    ok(f"RT-07 Konto gesperrt zwischen zwei Schritten: wartet_dich, zweiter Schritt nicht geschrieben ({s['reason']}, {texts()})",
       s["state"] == "wartet_dich" and s["reason"] == "konto_gesperrt" and texts() == ["r-1"])
    with connect() as c, c:
        c.execute("UPDATE users SET disabled=0 WHERE username=?", (A,))
    jobs.cancel(jid, A)
    prev = KT.ai_on(groups=["kf-niemand"])
    try:
        jid = jobs.create(A, "test.task", steps=[{"tool": "create_task", "args": {"text": "r-3"}}])
        go(jid)
        KT.run_all()
        s = jobs.state_of(jid)
        ok(f"RT-07 KI-Gruppe entzogen: wartet_dich 'ai', nichts geschrieben ({s['reason']})",
           s["state"] == "wartet_dich" and s["reason"] == "ai" and "r-3" not in texts())
        jobs.cancel(jid, A)
    finally:
        KT.ai_restore(prev)
    # Konto mitten im Schritt geloescht
    KT.users("kf-weg")
    KT.reset_block()
    jid = jobs.create("kf-weg", "test.block", steps=[{"tool": "create_task", "args": {"text": "nach dem Loeschen"}}])
    go(jid, "kf-weg")
    th = threading.Thread(target=jobs.run_once)
    th.start()
    KT.BLOCK["in"].wait(10)
    userdata.delete_user_data("kf-weg", drop_user=True)
    KT.BLOCK["go"].set()
    th.join(20)
    ok("RT-07 Konto mitten im Schritt geloescht: nichts geschrieben, Auftrag weg, Worker laeuft weiter",
       not KT.q("SELECT 1 FROM task WHERE uid='kf-weg'") and jobs.state_of(jid) is None and jobs.run_once() == 0)


# --- Modell weg ------------------------------------------------------------------------------------------------
def part_model():
    real_clock()
    KT.cleanup(A)
    KT.CALLS["model"] = 0
    prev = KT.ai_on(model="fake-500")
    try:
        KT.fake("POST", "/_reset")
        jid = jobs.create(A, "test.model", steps=[{"tool": "ask_model", "klass": "read"}], unit="zeichen")
        go(jid)
        t0 = testclock.now()
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"fake-500: laeuft/wartet_modell, naechster Versuch in 1 min, 0 Einheiten ({s['state']}/{s['sub']}, "
           f"{round(s['next_at'] - t0)} s)", s["state"] == "laeuft" and s["sub"] == "wartet_modell" and abs(s["next_at"] - t0 - 60) < 1
           and s["units"] == 0 and KT.fake("GET", "/_count")["n"] >= 1)
        delays = []
        for _ in range(4):
            testclock.set_clock({"advance": s["next_at"] - testclock.now() - 1})
            early = jobs.run_once()
            testclock.set_clock({"advance": 1})
            t = testclock.now()
            jobs.run_once()
            s = jobs.state_of(jid)
            delays.append((early, round(s["next_at"] - t)))
        ok(f"Backoff 5/15/60/60 min, vorher nicht ({delays})", delays == [(0, 300), (0, 900), (0, 3600), (0, 3600)])
        ok("Schritt wartet wieder offen, nichts gestempelt", jobs.steps_of(jid)[0]["state"] == "offen" and nchanges(jid) == 0)
        KT.ai_restore(prev)
        prev = KT.ai_on(model="fake")
        testclock.set_clock({"advance": 3601})
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"Modell wieder da: erledigt, Einheiten gezaehlt ({s['state']}, {s['units']} {s['unit']})",
           s["state"] == "erledigt" and s["units"] > 0 and s["unit"] == "zeichen")
        # Port zu, 24 h lang
        KT.ai_restore(prev)
        prev = KT.ai_on(model="fake")
        cfg = dict(KT.auth.settings(fresh=True)["ai"], url="http://127.0.0.1:9/v1")
        KT.auth.save_setting("ai", cfg)
        KT.auth.settings(fresh=True)
        jid = jobs.create(A, "test.model", steps=[{"tool": "ask_model", "klass": "read"}])
        go(jid)
        t0 = testclock.now()
        KT.CALLS["model"] = 0
        jobs.run_once()
        n = 0
        while jobs.state_of(jid)["state"] == "laeuft" and n < 60:
            s = jobs.state_of(jid)
            testclock.set_clock({"advance": max(0, s["next_at"] - testclock.now())})
            jobs.run_once()
            n += 1
        s = jobs.state_of(jid)
        ok(f"Port zu: nach 24 h fehlgeschlagen 'modell' ({s['state']}, {s['reason']}, {KT.CALLS['model']} Versuche, "
           f"{round((testclock.now() - t0) / 3600, 1)} h)", s["state"] == "fehlgeschlagen" and s["reason"] == "modell" and
           s["group"] == "nicht_geklappt" and 24 <= (testclock.now() - t0) / 3600 < 25.1 and 20 <= KT.CALLS["model"] <= 30)
        ev = KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='model'", (jid,))
        ok("Protokoll: je Versuch Wartezeit und Kennung, kein Fehlertext", ev and all(set(e[0]) == {"n", "delay", "reason"} and
                                                                                e[0]["reason"] == "modell_weg" for e in ev), ev[:2])
    finally:
        KT.ai_restore(prev)


# --- K-A6: Runner ai.task ------------------------------------------------------------------------------------------
FULL_A = apitok.Tok(10 ** 9, A, "voll", "sga_test", apitok.SCOPES, None, None)
FULL_B = apitok.Tok(10 ** 9 + 1, B, "voll", "sga_test", apitok.SCOPES, None, None)


def future(days=40):
    testclock.set_clock({"reset": True})
    testclock.set_clock({"set": testclock.now() + days * 86400})


def units_state(jid):
    """-> (offene Belegungen, Summe der Abrechnungen, Pool job heute)."""
    ev = [e[0] for e in KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='units'", (jid,))]
    return (sum(1 for e in ev if e.get("open") == 1), sum(int(e.get("units") or 0) for e in ev),
            ai_units.used(A)["job"]["units"])


def attempts(jid):
    """-> {schritt: Anzahl verschiedener Versuche in job_change}"""
    return {n: k for n, k in KT.q("SELECT step_n, COUNT(DISTINCT attempt) FROM job_change WHERE job_id=? GROUP BY step_n", (jid,))}


def part_ai_kill():
    future()
    KT.cleanup(A)
    root = api_v1.page_create(FULL_A, {"title": "KF Sommerfest", "markdown": "Plan"})[1]["id"]
    dbid = api_struct.database_create(FULL_A, {"title": "KF Liste", "parent_id": root,
                                               "properties": [{"name": "Erledigt", "type": "checkbox"}]})[1]["id"]
    for which, point in ((0, "after_write"), (1, "after_write"), (0, "mid_call")):
        KT.cleanup(A)
        with connect() as c, c:
            c.execute("DELETE FROM ai_units WHERE uid=?", (A,))
        row = api_v1.row_create(FULL_A, dbid, {"title": f"KF Kohle {which} {point}"})[1]["id"]
        rev = api_v1.page_read(FULL_A, row)[1]["rev"]
        text = f"KF Kohle abholen {which} {point}"
        jid = jobs.create(A, "ai.task", title="Kohle", steps=[
            {"tool": "update_row", "args": {"id": row, "rev": rev, "properties": {"Erledigt": True}}},
            {"tool": "create_task", "args": {"text": text}}])
        go(jid)
        if which == 1:
            jobs.run_once()                      # Schritt 1 hier, Absturz in Schritt 2
        rc, out = worker("run", jid, crash=point)
        tag = f"ai.task kill -9 {point} in Schritt {which + 1}"
        ok(f"{tag}: Worker stirbt am Absturzpunkt (rc {rc})", rc == -9 and f"Absturzpunkt {point}" in out, out)
        st = [x["state"] for x in jobs.steps_of(jid)]
        opened, _settled, pool = units_state(jid)
        if point == "after_write":
            wrote = (api_v1.page_read(FULL_A, row)[1].get("properties") or {}).get("Erledigt") is True if which == 0 else texts() == [text]
            ok(f"{tag}: geschrieben + Stempel, Abschluss fehlt ({st})", st[which] == "laeuft" and wrote and which in attempts(jid))
        else:
            ok(f"{tag}: belegt, nichts geschrieben, Belegung offen ({st}, offen {opened}, Pool {pool})",
               st[0] == "laeuft" and not attempts(jid) and opened == 1 and pool >= ai_task.STEP_EST)
        ok(f"{tag}: vor Ablauf der Lease nimmt niemand den Auftrag", jobs.run_once() == 0)
        testclock.set_clock({"advance": jobs.LEASE + 1})
        KT.run_all()
        props = api_v1.page_read(FULL_A, row)[1].get("properties") or {}
        att = attempts(jid)
        ok(f"{tag}: erledigt, Zeile geaendert, genau eine Aufgabe, je Schritt Stempel genau eines Versuchs ({texts()}, {att})",
           jobs.state_of(jid)["state"] == "erledigt" and props.get("Erledigt") is True and texts() == [text] and att == {0: 1, 1: 1},
           props)
        ch = KT.q("SELECT step_n, target FROM job_change WHERE job_id=? ORDER BY id", (jid,))
        ok(f"{tag}: Ziele der Stempel = Zeile bzw. Aufgabe ({ch})",
           f"row:{row}" in [t for n, t in ch if n == 0] and all(t.startswith("task:") for n, t in ch if n == 1)
           and len([1 for n, _t in ch if n == 1]) == 1)
        if point == "after_write":
            r = jobs.steps_of(jid)[which]["result"] or {}
            ok(f"{tag}: abgestuerzter Schritt aus dem Stempel erledigt, nicht wiederholt ({r})", r.get("resumed") is True)
        opened, settled, pool = units_state(jid)
        ok(f"{tag}: keine Belegung offen, Pool = Summe der Abrechnungen ({opened}, {settled}, {pool})",
           opened == 0 and pool == settled)


def part_ai_budget():
    """Tageslimit (quota 1 -> Pool job 1.200 Einheiten): zurueckgestellt, nicht fehlgeschlagen, nach Tageswechsel weiter."""
    prev = KT.ai_on(quota=1)
    cap = ai_units.budget(A, "job")
    try:
        testclock.set_clock({"reset": True})
        testclock.set_clock({"freeze": True})
        day = ai_units.next_day(testclock.now() + 40 * 86400)
        testclock.set_clock({"set": day - 3600})              # 23:00 Ortszeit, 40 Tage voraus
        KT.cleanup(A)

        def pool(n):
            with connect() as c, c:
                c.execute("INSERT INTO ai_units (uid, day, pool, units, real_in, real_out) VALUES (?, ?, 'job', ?, 0, 0) "
                          "ON CONFLICT (uid, day, pool) DO UPDATE SET units=EXCLUDED.units", (A, ai_units.today(), n))
        pool(cap)
        jid = jobs.create(A, "ai.task", steps=[{"tool": "create_task", "args": {"text": "KF Budget 1"}}])
        go(jid)
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"Tageslimit vor dem ersten Schritt: zurueckgestellt 'budget' bis Mitternacht, nichts geschrieben ({s['state']}/{s['sub']}, "
           f"{s['reason']}, {s['next_at'] - day})", s["state"] == "laeuft" and s["sub"] == "wartet_frist" and s["reason"] == "budget"
           and s["next_at"] == day and texts() == [] and s["group"] == "laeuft")
        ev = KT.q("SELECT data FROM job_event WHERE job_id=? AND kind='wait'", (jid,))
        ok("Protokoll: zurueckgestellt mit Grund, keine Belegung offen", [e[0].get("reason") for e in ev] == ["budget"]
           and units_state(jid)[0] == 0)
        testclock.set_clock({"advance": 3599})
        ok("eine Sekunde vor Mitternacht: nichts faellig", jobs.run_once() == 0)
        testclock.set_clock({"advance": 2})
        KT.run_all()
        s = jobs.state_of(jid)
        ok(f"nach dem Tageswechsel: weiter und erledigt, genau eine Aufgabe ({s['state']}, {texts()})",
           s["state"] == "erledigt" and texts() == ["KF Budget 1"] and s["reason"] is None)
        # mitten im Auftrag: laufender Auftrag darf genau einen Schritt ueberziehen, dann zurueckgestellt
        KT.cleanup(A)
        jid = jobs.create(A, "ai.task", steps=[{"tool": "create_task", "args": {"text": f"KF Lauf {i}"}} for i in range(3)])
        go(jid)
        jobs.run_once()
        pool(cap - 100)                          # Schaetzung (>= 500) passt nicht mehr, Pool aber noch nicht voll
        jobs.run_once()
        ok(f"laufender Auftrag, Pool fast voll: Schritt 2 laeuft noch (ueberzieht einmal) ({texts()})",
           texts() == ["KF Lauf 0", "KF Lauf 1"] and ai_units.used(A)["job"]["units"] >= cap - 100)
        pool(cap)
        jobs.run_once()
        s = jobs.state_of(jid)
        ok(f"Pool voll: Schritt 3 zurueckgestellt 'budget', Auftrag nicht fehlgeschlagen ({s['state']}, {s['reason']}, "
           f"{[x['state'] for x in jobs.steps_of(jid)]})", s["state"] == "laeuft" and s["reason"] == "budget"
           and [x["state"] for x in jobs.steps_of(jid)] == ["erledigt", "erledigt", "wartet"] and texts() == ["KF Lauf 0", "KF Lauf 1"])
        testclock.set_clock({"set": ai_units.next_day() + 1})
        KT.run_all()
        ok(f"naechster Tag: Rest erledigt, keiner doppelt ({texts()})", jobs.state_of(jid)["state"] == "erledigt"
           and texts() == ["KF Lauf 0", "KF Lauf 1", "KF Lauf 2"])
    finally:
        KT.ai_restore(prev)
        testclock.set_clock({"reset": True})
        with connect() as c, c:
            c.execute("DELETE FROM ai_units WHERE uid=?", (A,))


def part_ai_share():
    """Seite im laufenden Auftrag nicht mehr geteilt / Ziel ausserhalb des Plans -> kein Schreiben, sauberer Zustand."""
    future()
    KT.cleanup(A)
    pb = api_v1.page_create(FULL_B, {"title": "KF Bert geteilt", "markdown": "Start"})[1]["id"]
    other = api_v1.page_create(FULL_B, {"title": "KF Bert auch geteilt", "markdown": "Start"})[1]["id"]
    with connect() as c, c:
        sharing.set_share(c, B, pb, "user", A, "edit")
        sharing.set_share(c, B, other, "user", A, "edit")
    jid = jobs.create(A, "ai.task", steps=[{"tool": "append_to_page", "args": {"id": pb, "markdown": "KF eins"}},
                                           {"tool": "append_to_page", "args": {"id": pb, "markdown": "KF zwei"}}])
    go(jid)
    jobs.run_once()
    body = api_v1.page_read(FULL_B, pb, "markdown")[1].get("markdown") or ""
    ok("geteilte Seite: erster Schritt schreibt", "KF eins" in body, body[-200:])
    with connect() as c, c:
        sharing.remove_share(c, B, pb, "user", A)
    jobs.run_once()
    s = jobs.state_of(jid)
    body = api_v1.page_read(FULL_B, pb, "markdown")[1].get("markdown") or ""
    st = [x["state"] for x in jobs.steps_of(jid)]
    ok(f"Seite nicht mehr geteilt: wartet_dich 'kein_zugriff', zweiter Schritt nicht geschrieben, Schritt wieder offen "
       f"({s['state']}, {s['reason']}, {st})", s["state"] == "wartet_dich" and s["reason"] == "kein_zugriff" and "KF zwei" not in body
       and st == ["erledigt", "offen"] and len(KT.q("SELECT 1 FROM job_change WHERE job_id=? AND step_n=1", (jid,))) == 0)
    ok("danach kein Takt fuer den Auftrag", jobs.run_once() == 0)
    jobs.cancel(jid, A)
    # Ziel ausserhalb des freigegebenen Plans (Angaben nach der Freigabe auf eine andere geteilte Seite verbogen)
    jid = jobs.create(A, "ai.task", steps=[{"tool": "append_to_page", "args": {"id": other, "markdown": "KF drei"}}])
    go(jid)
    plan = jobs.state_of(jid)
    with connect() as c, c:
        c.execute("UPDATE job_step SET args=CAST(? AS JSONB) WHERE job_id=?", ('{"id": %d, "markdown": "KF boese"}' % pb, jid))
    jobs.run_once()
    s = jobs.state_of(jid)
    ok(f"Ziel ausserhalb des freigegebenen Plans: fehlgeschlagen 'plan_geaendert', nichts geschrieben ({s['state']}, {s['reason']})",
       s["state"] == "fehlgeschlagen" and s["reason"] == "plan_geaendert" and plan["plan_hash"] == s["plan_hash"]
       and "KF boese" not in (api_v1.page_read(FULL_B, pb, "markdown")[1].get("markdown") or ""))
    testclock.set_clock({"reset": True})


def main():
    KT.users(A, B)
    KT.register_all()
    prev = KT.ai_on()
    fk = KT.fake_start()
    for u in (A, B, "kf-weg"):
        KT.cleanup(u)
    try:
        part_kill()
        part_two_workers()
        part_rt07()
        part_model()
        part_ai_kill()
        part_ai_budget()
        part_ai_share()
    finally:
        fk.terminate()
        KT.ai_restore(prev)
        testclock.set_clock({"reset": True})
        for u in (A, B, "kf-weg"):
            KT.cleanup(u)
    print(f"\nKOLLEGIN-FEHLER {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--worker":
        worker_main(sys.argv[2], int(sys.argv[3]))
    else:
        main()
