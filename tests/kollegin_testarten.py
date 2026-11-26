"""Gemeinsame Test-Arten und Hilfen fuer tests/kollegin_jobs.py und tests/kollegin_fehler.py (2.6, Bugs-DB 2266 K-A2) -
keine eigene Reihe. Auftragsarten ohne saga/tools.py:
  test.echo   erledigt sofort, Ergebnis = args, units = args.units
  test.task   legt eine Aufgabe an (tasks.task_add unter acting -> Stempel task:<id>), Ruecknahme loescht sie
  test.wait   wartet auf args.on/args.ref bis args.until (ohne args: sofort erledigt); Treffer -> {"hit": ref}, Frist -> {"timeout": true}
  test.block  wie test.task, haelt aber beim ersten Aufruf an BLOCK["go"] (Lease-/Pause-Faelle)
  test.model  ruft das Modell (ai.stream_model, Fake-Modell); nicht erreichbar -> Outcome.retry_model
Zaehler CALLS je Art. Fake-Modell: tests/fakeai.py auf FAKE_PORT (HK_FAKEAI_PORT, Standard 18291)."""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import auth
from db import connect
from saga import jobs, tasks

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_PORT = int(os.environ.get("HK_FAKEAI_PORT", "18291"))
CALLS = {"echo": 0, "task": 0, "wait": 0, "block": 0, "model": 0}
BLOCK = {"go": threading.Event(), "in": threading.Event(), "first": True}
PW = "Kj-Pass-12345"


def _task(ctx, step):
    r = tasks.task_add(ctx.uid, {"text": (step["args"] or {}).get("text") or f"Aufgabe {ctx.idem}"})
    return json.loads(r.body)["id"]


def run_echo(ctx, job, step):
    CALLS["echo"] += 1
    a = step["args"] or {}
    return jobs.Outcome.done(a, units=a.get("units", 0))


def run_task(ctx, job, step):
    CALLS["task"] += 1
    return jobs.Outcome.done({"task": _task(ctx, step)}, units=(step["args"] or {}).get("units", 0))


def run_wait(ctx, job, step):
    CALLS["wait"] += 1
    a = step["args"] or {}
    if not a:                                    # Schritt ohne Angaben: nichts zu warten
        return jobs.Outcome.done({"echo": True})
    if ctx.hits:
        return jobs.Outcome.done({"hit": ctx.hits[0]["ref"], "n": len(ctx.hits)})
    if ctx.timed_out:
        return jobs.Outcome.done({"timeout": True, "at": ctx.now})
    return jobs.Outcome.wait(a.get("on"), a.get("ref"), a.get("until"))


def run_block(ctx, job, step):
    CALLS["block"] += 1
    if BLOCK["first"]:
        BLOCK["first"] = False
        BLOCK["in"].set()
        BLOCK["go"].wait(30)
    return jobs.Outcome.done({"task": _task(ctx, step)})


def run_model(ctx, job, step):
    CALLS["model"] += 1
    from saga import ai
    out = []
    try:
        ai.stream_model(ai.cfg(), [{"role": "system", "content": "Test"}, {"role": "user", "content": "Hallo"}], out.append, max_tokens=50)
    except ai.AIError:
        return jobs.Outcome.retry_model("modell_weg")
    return jobs.Outcome.done({"chars": len("".join(out))}, units=len("".join(out)))


def comp_task(c, job, ch):
    kind, _, ref = ch["target"].partition(":")
    if kind == "task":
        c.execute("DELETE FROM task WHERE id=? AND uid=?", (int(ref), job["uid"]))


def register_all():
    jobs.register("test.echo", run_echo)
    jobs.register("test.task", run_task, comp_task)
    jobs.register("test.wait", run_wait)
    jobs.register("test.block", run_block, comp_task)
    jobs.register("test.model", run_model)


def reset_block():
    BLOCK["go"] = threading.Event()
    BLOCK["in"] = threading.Event()
    BLOCK["first"] = True


_USERS = set()


def consent(*names):
    """2.6 K-I (2269): Einwilligung fuer die Kollegin (sonst haelt jeder Schritt mit 'einwilligung' an) - fuer den
    jetzt eingestellten Endpunkt; ohne eingestellte KI nichts (ai_on holt es nach)."""
    from saga import ai, ai_saga
    if not ai.configured():
        return
    for u in names:
        if not ai_saga.consent_ok(u):
            ai_saga.grant(u, "off")


def users(*names):
    for u in names:
        if not auth.get_user(u):
            auth.create_user(u, PW, u)
        with connect() as c, c:
            c.execute("UPDATE users SET disabled=0 WHERE username=?", (u,))
        auth.set_user_groups(auth.get_user(u)["id"], [])
        _USERS.add(u)
    consent(*names)


def ai_on(model="fake", **kw):
    """KI eingeschaltet mit dem Fake-Modell (127.0.0.1 festgehalten) -> vorherige Einstellung."""
    prev = dict(auth.settings(fresh=True).get("ai") or {})
    cfg = dict(prev, enabled=True, provider="openai", url=f"http://127.0.0.1:{FAKE_PORT}/v1", model=model, pinned=["127.0.0.1"],
               groups=[], caps={}, timeout=5)
    cfg.update(kw)
    auth.save_setting("ai", cfg)
    auth.settings(fresh=True)
    consent(*sorted(_USERS))                       # K-I: Einwilligung fuer den (neuen) Endpunkt
    return prev


def ai_restore(prev):
    auth.save_setting("ai", prev)
    auth.settings(fresh=True)


def fake_start():
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "fakeai.py"), str(FAKE_PORT)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            socket.create_connection(("127.0.0.1", FAKE_PORT), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return p


def fake(method, path):
    import httpx
    with httpx.Client(timeout=5, trust_env=False) as cl:
        r = cl.request(method, f"http://127.0.0.1:{FAKE_PORT}{path}")
        return r.json() if r.content else {}


def cleanup(prefix):
    with connect() as c, c:
        c.execute("DELETE FROM job WHERE uid LIKE ?", (prefix + "%",))
        c.execute("DELETE FROM task WHERE uid LIKE ?", (prefix + "%",))


def q(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).fetchall()


def run_all(max_rounds=20):
    n = 0
    for _ in range(max_rounds):
        k = jobs.run_once()
        n += k
        if not k:
            break
    return n
