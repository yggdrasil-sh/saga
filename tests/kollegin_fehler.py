"""Abbruch- und Fehlerreihe der Kollegin, K-A-Teil (2.6, Bugs-DB 2266 K-A2, teil-tests.md §5): saga/jobs.py gegen die
Datenbank aus DATABASE_URL, Worker als UNTERPROZESS dieser Datei (--worker), der an den Absturzpunkten per SIGKILL stirbt.

Prueft: kill -9 an 4 Stellen (after_approve, before_write, after_write, mid_compensate) -> Wiederaufnahme am letzten
bestaetigten Schritt, genau eine Wirkung · zwei Worker-Prozesse gleichzeitig -> jeder Auftrag genau einmal · Rechte
entzogen (RT-07: Konto gesperrt, KI-Gruppe weg, Konto mitten im Schritt geloescht) -> wartet_dich bzw. weg, nichts
geschrieben · Modell weg (fake-500, Port zu) -> laeuft/wartet_modell mit Backoff 1/5/15/60 min, 0 Einheiten, nach 24 h
fehlgeschlagen, kommt das Modell zurueck -> erledigt.
OFFEN (nicht gezaehlt): Tageslimit -> zurueckgestellt (mit K-A5 ai_units), Seite nicht mehr geteilt (mit K-A3 JobTok).
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
from saga import jobs, testclock, userdata  # noqa: E402

A, B = "kf-anna", "kf-bert"
TOK = "f" * 40
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def offen(n):
    print("OFFEN", n, flush=True)


def go(jid, uid=A):
    s = jobs.state_of(jid)
    return jobs.approve(jid, uid, s["plan_v"], s["plan_hash"])


def worker(mode, jid, crash=None):
    """Unterprozess: diese Datei mit --worker; crash = Absturzpunkt (SIGKILL) -> (rc, Ausgabe)."""
    env = dict(os.environ, SAGA_TEST_HOOKS="1", SAGA_TEST_TOKEN=TOK)
    env.pop("SAGA_TEST_CRASH", None)
    if crash:
        env["SAGA_TEST_CRASH"] = crash
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "--worker", mode, str(jid)], env=env, capture_output=True,
                       text=True, timeout=120)
    return p.returncode, (p.stdout + p.stderr)[-800:]


def worker_main(mode, jid):
    KT.register_all()
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
    offen("RT-07 Seite nicht mehr geteilt / ausserhalb roots -> mit K-A3 (JobTok, tests/kollegin_werkzeuge.py)")


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
    offen("Tageslimit waehrend eines Auftrags -> zurueckgestellt statt verworfen (mit K-A5 ai_units, tests/kollegin_budget.py)")


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
