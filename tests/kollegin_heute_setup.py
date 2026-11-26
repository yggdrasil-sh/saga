"""Einrichtung fuer tests/browser_kollegin_heute.js (2.6, Bugs-DB 2271 K-D: Tageszeile "Kurz gesagt" auf "Heute").

  --setup     Konten kdw-an (KI an, du), kdw-sie (KI an, Anrede Sie), kdw-aus (ohne KI), KI an fuer Gruppe kdw-fam
              (kein Modell noetig - die Tageszeile ruft keins), Aufgaben (ueberfaellig mit XSS im Titel, heute faellig) und
              wartende Auftraege -> JSON auf stdout.
  --teardown  Konten, Daten und KI-Einstellung zurueck.
  --task <uid> <text> <due>   weitere Aufgabe (Live aus zweitem Tab) -> {"id": n}

Wegwerf-Konten kdw-*. Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/kollegin_heute_setup.py --setup"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import auth
import db
from db import connect

PW = "kdw-Passwort-2271!"
AN, SIE, AUS = "kdw-an", "kdw-sie", "kdw-aus"
GROUP = "kdw-fam"
XSS = '<img src=x onerror="window.__kdxss=1">Pavillon abholen'
SAVE = os.path.join(os.environ.get("STATE_DIR") or "/tmp", "kdw-ai-save.json")
TZ = ZoneInfo("Europe/Berlin")


def day(off=0):
    return (datetime.now(TZ).date() + timedelta(days=off)).isoformat()


def cleanup():
    from saga.userdata import delete_user_data
    with connect() as c:
        users = [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'kdw-%'").fetchall()]
    for u in users:
        try:
            delete_user_data(u)
        except Exception:  # noqa: BLE001 - Aufraeumen best effort
            pass
    with connect() as c, c:
        for t_ in ("job", "user_kv", "note", "task", "ai_consent", "ai_usage"):
            c.execute(f"DELETE FROM {t_} WHERE uid LIKE 'kdw-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kdw-%'")
        c.execute("DELETE FROM groups WHERE name LIKE 'kdw-%'")


def task(uid, text, due, prio=2):
    now = time.time()
    with connect() as c, c:
        cur = c.execute("INSERT INTO task (uid, text, due, prio, done, created) VALUES (?, ?, ?, ?, 0, ?) RETURNING id",
                        (uid, text, due, prio, now))
        return cur.fetchone()[0]


def waiting(uid, title, state):
    from saga import jobs
    j = jobs.create(uid, "test.echo", title=title, steps=[{"tool": "echo", "args": {"x": 1}}])
    with connect() as c, c:
        c.execute("UPDATE job SET state=?, updated=? WHERE id=?", (state, time.time(), j))
    return j


def setup():
    db.wait_ready()
    db.init_schema()
    cleanup()
    st = auth.settings(fresh=True)
    if not os.path.exists(SAVE):
        with open(SAVE, "w") as f:
            json.dump({"ai": st.get("ai") or {}, "first_steps": st.get("first_steps", True)}, f)
    for u in (AN, SIE, AUS):
        auth.create_user(u, PW, u.replace("kdw-", "Kdw").title())
    for u in (AN, SIE):
        auth.set_user_groups(auth.get_user(u)["id"], [GROUP])
    auth.save_setting("first_steps", False)
    cur = dict(st.get("ai") or {})
    cur.update(enabled=True, provider="openai", url="http://127.0.0.1:9/v1", model="fake", pinned=["127.0.0.1"],
               groups=[GROUP], quota=5, timeout=5, endpoint_note="")
    auth.save_setting("ai", cur)
    auth.settings(fresh=True)
    ids = {"over": task(AN, XSS, day(-3), 1), "over2": task(AN, "Altglas wegbringen", day(-2)),
           "over3": task(AN, "Rechnung prüfen", day(-1)), "due": task(AN, "Kuchen backen", day(0)),
           "sie_due": task(SIE, "Steuererklärung abgeben", day(0))}
    ids["job"] = waiting(AN, "Grillkohle", "wartet_ok")
    waiting(SIE, "Brief an die Schule", "wartet_dich")
    waiting(SIE, "Termin bestätigen", "wartet_ok")
    # Anrede Sie fuer kdw-sie setzt der Browser-Test ueber PUT /api/ai/prefs
    print(json.dumps({"an": AN, "sie": SIE, "aus": AUS, "pw": PW, "xss": XSS, "ids": ids}))


def teardown():
    cleanup()
    if os.path.exists(SAVE):
        with open(SAVE) as f:
            prev = json.load(f)
        auth.save_setting("ai", prev["ai"])
        auth.save_setting("first_steps", prev["first_steps"])
        os.remove(SAVE)
    print("{}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--setup"]:
        setup()
    elif a[:1] == ["--teardown"]:
        teardown()
    elif a[:1] == ["--task"] and len(a) == 4:
        print(json.dumps({"id": task(a[1], a[2], a[3])}))
    else:
        print(__doc__)
        sys.exit(2)
