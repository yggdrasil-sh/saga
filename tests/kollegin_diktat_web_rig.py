"""Vorbereitung fuer tests/browser_kollegin_diktat.js (Saga 2.6, Bugs-DB 2274 K-F, Web): KI an mit dem Fake-Modell fake-plan
(tests/fakeai.py, startet die Reihe selbst auf HK_FAKEAI_PORT), Stufe "Plaene", Persona "Marin", Wegwerf-Konto kfw-web
(Passwort KFW_PASS) mit Einwilligung und Diktat "server". Optional der Diktat-Dienst (Admin-Einstellung ai_stt) auf die
Attrappe tests/fakewhisper.py (HK_FAKEWHISPER_PORT) - nur, wenn der Server ai_stt kennt (sonst "stt fehlt").
Einstellungen sind im Server 5 s zwischengespeichert - die Reihe wartet danach.
  PYTHONPATH=tests/pgshim:. KFW_PASS=... HK_FAKEAI_PORT=19058 python tests/kollegin_diktat_web_rig.py setup
  PYTHONPATH=tests/pgshim:. HK_FAKEWHISPER_PORT=19059 python tests/kollegin_diktat_web_rig.py stt '{"formats": ["ogg", "wav"]}'
  PYTHONPATH=tests/pgshim:. python tests/kollegin_diktat_web_rig.py stt off
  PYTHONPATH=tests/pgshim:. python tests/kollegin_diktat_web_rig.py prefs '{"dictation": "server"}'
  PYTHONPATH=tests/pgshim:. python tests/kollegin_diktat_web_rig.py clean          (Einstellungen zurueck, Konto weg)
"""
import json
import os
import sys

import auth
import db

USER = "kfw-web"
PREV = os.path.join(os.environ.get("STATE_DIR") or "/tmp", "kfw-web-prev.json")


def setup():
    db.wait_ready()
    db.init_schema()
    from saga import ai_plan, ai_saga
    port = int(os.environ.get("HK_FAKEAI_PORT", "19058"))
    cur = auth.settings(fresh=True)
    if not os.path.exists(PREV):
        with open(PREV, "w") as f:
            json.dump({"ai": cur.get("ai"), "persona": cur.get("ai_persona"), "plan": cur.get(ai_plan.SETTING),
                       "stt": cur.get("ai_stt")}, f)
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": f"http://127.0.0.1:{port}/v1", "model": "fake-plan",
                             "api_key": "", "groups": [], "timeout": 10, "quota": 0, "context": 8000, "history": True,
                             "pinned": ["127.0.0.1"], "chat": False, "web": False, "caps": {}})
    auth.save_setting(ai_plan.SETTING, {"level": "plans"})
    auth.save_setting("ai_persona", {"name": "Marin"})
    if not auth.get_user(USER):
        auth.create_user(USER, os.environ["KFW_PASS"], "Kim Diktat")
    auth.settings(fresh=True)
    ai_saga.grant(USER)
    prefs('{"dictation": "server"}')
    print("rig ok", USER)


def stt(arg):
    """Diktat-Dienst einrichten (ai_stt auf die Attrappe) bzw. "off" - braucht den Endpunkt der Server-Rolle (kf-srv)."""
    if "ai_stt" not in getattr(auth, "DEFAULTS", {}):
        print("stt fehlt")
        return
    if arg == "off":
        auth.save_setting("ai_stt", dict(auth.DEFAULTS["ai_stt"]))
        auth.settings(fresh=True)
        print("stt off")
        return
    port = int(os.environ.get("HK_FAKEWHISPER_PORT", "19059"))
    cfg = dict(auth.DEFAULTS["ai_stt"])
    cfg.update({"enabled": True, "url": f"http://127.0.0.1:{port}", "model": "fake", "pinned": ["127.0.0.1"]})
    cfg.update(json.loads(arg or "{}"))
    auth.save_setting("ai_stt", cfg)
    auth.settings(fresh=True)
    print("stt ok")


def prefs(patch):
    from saga import ai_saga
    with db.connect() as c, c:
        ai_saga._set_prefs(c, USER, json.loads(patch))
    print("prefs ok")


def clean():
    from saga import ai_plan
    from saga.userdata import delete_user_data
    if os.path.exists(PREV):
        with open(PREV) as f:
            p = json.load(f)
        if p.get("ai") is not None:
            auth.save_setting("ai", p["ai"])
        auth.save_setting("ai_persona", p.get("persona") or {})
        auth.save_setting(ai_plan.SETTING, p.get("plan") or {})
        if "ai_stt" in getattr(auth, "DEFAULTS", {}):
            auth.save_setting("ai_stt", p.get("stt") or dict(auth.DEFAULTS["ai_stt"]))
        os.remove(PREV)
    if auth.get_user(USER):
        delete_user_data(USER, drop_user=True)
    print("rig clean")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "stt":
        stt(sys.argv[2] if len(sys.argv) > 2 else "{}")
    elif cmd == "prefs":
        prefs(sys.argv[2])
    elif cmd == "clean":
        clean()
    else:
        print(__doc__)
        sys.exit(2)
