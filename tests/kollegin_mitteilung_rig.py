"""Vorbereitung fuer tests/browser_kollegin_mitteilung.js (2.6, Bugs-DB 2270 K-C, Web): KI an mit dem Fake-Modell
fake-plan (tests/fakeai.py laeuft getrennt auf HK_FAKEAI_PORT), Stufe "Plaene", Persona "Marin", Mitteilungen an,
Konto kcm-web (Passwort KCM_PASS) mit Einwilligung. Vor dem Start des Servers aufrufen (Einstellungen sind im Server
5 s zwischengespeichert).
  PYTHONPATH=tests/pgshim:. KCM_PASS=... HK_FAKEAI_PORT=8912 python tests/kollegin_mitteilung_rig.py setup
  PYTHONPATH=tests/pgshim:. python tests/kollegin_mitteilung_rig.py clean
  PYTHONPATH=tests/pgshim:. python tests/kollegin_mitteilung_rig.py msg <job> <job.done|job.needs_you>
      -> Nutzlast 3 wie fuer ein Browser-Abo (Web Push, caps detail+actions, ohne Geraet) mit echten Einmal-Token, als
         JSON auf stdout (nur fuer den Test: der echte Service Worker bekommt sie per CDP zugestellt).
  PYTHONPATH=tests/pgshim:. python tests/kollegin_mitteilung_rig.py needs <titel>
      -> Auftrag von ausserhalb des Orts "Saga" (source api, wartet auf das OK - meldet sich also), Kennung auf stdout.
"""
import json
import os
import sys

import auth
import db

USER = "kcm-web"


def setup():
    db.wait_ready()
    db.init_schema()
    from saga import ai_plan, ai_saga
    port = int(os.environ.get("HK_FAKEAI_PORT", "8912"))
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": f"http://127.0.0.1:{port}/v1", "model": "fake-plan",
                             "api_key": "", "groups": [], "timeout": 10, "quota": 0, "context": 8000, "history": False,
                             "pinned": ["127.0.0.1"], "chat": False, "web": False, "caps": {}})
    auth.save_setting(ai_plan.SETTING, {"level": "plans"})
    auth.save_setting("ai_persona", {"name": "Marin"})
    if not auth.get_user(USER):
        auth.create_user(USER, os.environ["KCM_PASS"], "Kim Web")
    auth.settings(fresh=True)
    ai_saga.grant(USER)
    print("rig ok", USER)


def clean():
    from saga.userdata import delete_user_data
    if auth.get_user(USER):
        delete_user_data(USER, drop_user=True)
    print("rig clean")


def msg(job, kind):
    from saga import ai_notify, push  # noqa: F401  (ai_notify meldet die Darstellung an)
    with db.connect() as c:
        m = push.message(c, USER, kind, data={"job": int(job)}, sub_kind="webpush", lang="de",
                         sub={"id": None, "kind": "webpush", "caps": ("detail", "actions"), "device": None, "lang": "de", "e2e": True})
    print(json.dumps(m, ensure_ascii=False))


def needs(title):
    from saga import ai_notify, ai_plan, jobs  # noqa: F401
    print(jobs.create(USER, ai_plan.KIND, source="api", title=title, plan={},
                      steps=[{"tool": "create_task", "args": {"text": title}}]))


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if a == "msg":
        msg(sys.argv[2], sys.argv[3])
    elif a == "needs":
        needs(sys.argv[2])
    else:
        {"setup": setup, "clean": clean}[a]()
