"""Gemeinsame Bausteine von K-C/K-D (2.6, Bugs-DB 2270 K-C Schritt S2): bus-Ereignis "job.state" aus saga.jobs._to und
Ruhe-Helfer saga.ai_saga.set_quiet/quiet_until/next_local. Im Prozess gegen die Datenbank aus DATABASE_URL, Zeit ueber die
Testuhr, Arten aus tests/kollegin_testarten.py (ohne Modell).

Prueft: job.state genau einmal je echtem Wechsel (wartet_ok -> laeuft -> erledigt, wartet_ok -> abgebrochen) mit job_id,
uid, kind, old, new, reason · kein Ereignis bei verweigertem Wechsel · ein scheiternder Zuhoerer bricht den Wechsel nicht
ab · Ruhe je Stelle (push/today getrennt), Ablauf ueber die Testuhr, "wieder an", Vergangenheit = aus, Obergrenze
14 Tage, unbekannte Stelle ValueError, eigener user_kv-Eintrag (ai_prefs bleibt unberuehrt) · next_local gegen die
Vektoren tests/vectors/push_kollegin.json (quiet, auch Zeitumstellung).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_zustand_ruhe.py"""
import json
import os
import sys

import db

db.wait_ready()
from db import connect  # noqa: E402
import kollegin_testarten as KT  # noqa: E402
from saga import ai_saga, bus, jobs, testclock  # noqa: E402

A, B = "kz-anna", "kz-bert"
T0 = 1893456000.0                       # 2030-01-01 00:00 UTC
HERE = os.path.dirname(os.path.abspath(__file__))
res = []
SEEN = []
BOOM = {"on": False}


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:400], flush=True)


def _rec(c, **d):
    if str(d.get("uid", "")).startswith("kz-"):
        SEEN.append(d)


def _boom(c, **d):
    if BOOM["on"]:
        raise RuntimeError("Zuhoerer kaputt (gewollt)")


bus.on("job.state", _rec)
bus.on("job.state", _boom)


def mk(uid=A, **kw):
    return jobs.create(uid, "test.echo", steps=[{"tool": "echo", "args": {"x": 1}}], **kw)


def go(jid, uid=A):
    s = jobs.state_of(jid)
    return jobs.approve(jid, uid, s["plan_v"], s["plan_hash"])


def part_state():
    SEEN.clear()
    jid = mk()
    ok("Anlegen (wartet_ok) ist kein Wechsel: kein Ereignis", SEEN == [], SEEN)
    go(jid)
    KT.run_all()
    seq = [(e["old"], e["new"]) for e in SEEN if e["job_id"] == jid]
    ok(f"wartet_ok -> laeuft -> erledigt: je genau ein Ereignis ({seq})", seq == [("wartet_ok", "laeuft"), ("laeuft", "erledigt")])
    e = SEEN[-1]
    ok("Felder job_id, uid, kind, old, new, reason", set(e) == {"job_id", "uid", "kind", "old", "new", "reason"} and
       e["uid"] == A and e["kind"] == "test.echo" and e["job_id"] == jid, e)
    SEEN.clear()
    ok("verweigerter Wechsel (erledigt -> abbrechen): kein Ereignis", not jobs.cancel(jid, A) and SEEN == [], SEEN)
    j2 = mk()
    ok("Abbrechen: ein Ereignis mit Grund", jobs.cancel(j2, A, "person") and
       [(x["old"], x["new"], x["reason"]) for x in SEEN] == [("wartet_ok", "abgebrochen", "person")], SEEN)
    SEEN.clear()
    ok("fremde Person bricht nicht ab: kein Ereignis", not jobs.cancel(mk(), B) and SEEN == [], SEEN)
    BOOM["on"] = True
    try:
        j3 = mk()
        go(j3)
        KT.run_all()
    finally:
        BOOM["on"] = False
    ok("scheiternder Zuhoerer: Wechsel gilt trotzdem (erledigt)", jobs.state_of(j3)["state"] == "erledigt",
       jobs.state_of(j3))


def part_quiet():
    testclock.set_clock({"freeze": True})
    testclock.set_clock({"set": T0})
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_quiet', 'ai_prefs')", (A,))
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, 'ai_prefs', ?)", (A, json.dumps({"lively": "sachlich"})))
    ok("ohne Eintrag: nicht ruhig", ai_saga.quiet_until(A, "push") is None and ai_saga.quiet_until(A, "today") is None)
    u = ai_saga.set_quiet(A, "push", T0 + 3600)
    ok("push bis +1 h, today unberuehrt", u == T0 + 3600 and ai_saga.quiet_until(A, "push") == T0 + 3600 and
       ai_saga.quiet_until(A, "today") is None)
    ai_saga.set_quiet(A, "today", T0 + 7200)
    ok("today bis +2 h getrennt", ai_saga.quiet_until(A, "today") == T0 + 7200 and ai_saga.quiet_until(A, "push") == T0 + 3600)
    ok("andere Person nicht ruhig", ai_saga.quiet_until(B, "push") is None)
    testclock.set_clock({"advance": 3601})
    ok("Ablauf ueber die Testuhr: push aus, today noch an", ai_saga.quiet_until(A, "push") is None and
       ai_saga.quiet_until(A, "today") == T0 + 7200)
    ok("wieder an (None) -> None, Eintrag weg", ai_saga.set_quiet(A, "today", None) is None and
       not KT.q("SELECT 1 FROM user_kv WHERE uid=? AND k='ai_quiet'", (A,)))
    ok("Zeit in der Vergangenheit = aus", ai_saga.set_quiet(A, "push", testclock.now() - 5) is None and
       ai_saga.quiet_until(A, "push") is None)
    u = ai_saga.set_quiet(A, "push", testclock.now() + 90 * 86400)
    ok("hoechstens 14 Tage", u == testclock.now() + 14 * 86400, u)
    with connect() as c, c:
        ai_saga.set_quiet(A, "today", testclock.now() + 60, conn=c)
        ok("mit conn in derselben Transaktion lesbar", ai_saga.quiet_until(A, "today", conn=c) == testclock.now() + 60)
    try:
        ai_saga.set_quiet(A, "chat", testclock.now() + 60)
        bad = False
    except ValueError:
        bad = True
    ok("unbekannte Stelle: ValueError", bad)
    pr = KT.q("SELECT v FROM user_kv WHERE uid=? AND k='ai_prefs'", (A,))
    ok("ai_prefs bleibt unberuehrt", pr and json.loads(pr[0][0]) == {"lively": "sachlich"}, pr)
    with connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k IN ('ai_quiet', 'ai_prefs')", (A,))


def part_next_local():
    with open(os.path.join(HERE, "vectors", "push_kollegin.json"), encoding="utf-8") as f:
        vec = json.load(f)["quiet"]
    wrong = [(q["tz"], q["now_local"], ai_saga.next_local(7, q["tz"], q["now"]), q["until"]) for q in vec
             if ai_saga.next_local(7, q["tz"], q["now"]) != q["until"]]
    ok(f"next_local(7) = Vektoren quiet ({len(vec)} Faelle, auch Zeitumstellung)", vec and not wrong, wrong)
    ok("unbekannte Zone: Server-Zeitzone statt Fehler", isinstance(ai_saga.next_local(7, "Mars/Olympus", T0), int))


def main():
    KT.users(A, B)
    KT.register_all()
    prev = KT.ai_on()
    for u in (A, B):
        KT.cleanup(u)
    testclock.set_clock({"freeze": True})
    testclock.set_clock({"set": T0})
    try:
        part_state()
        part_quiet()
        part_next_local()
    finally:
        KT.ai_restore(prev)
        testclock.set_clock({"reset": True})
        for u in (A, B):
            KT.cleanup(u)
    print(f"\nKOLLEGIN-ZUSTAND-RUHE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    sys.exit(0 if res and all(res) else 1)


if __name__ == "__main__":
    main()
