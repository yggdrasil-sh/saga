"""Vorbereitung fuer tests/browser_kollegin_teilen.js (Saga 2.6, Bugs-DB 2275 K-G, Web/PWA-Teilen-Ziel): KI an mit dem
Fake-Modell fake-plan (tests/fakeai.py laeuft getrennt auf HK_FAKEAI_PORT, startet die Reihe selbst), Stufe "Plaene",
Persona "Marin", Wegwerf-Konto kgw-web (Passwort KGW_PASS) mit Einwilligung. Einstellungen sind im Server 5 s
zwischengespeichert - die Reihe wartet danach.
  PYTHONPATH=tests/pgshim:. KGW_PASS=... HK_FAKEAI_PORT=19048 python tests/kollegin_teilen_web_rig.py setup
  PYTHONPATH=tests/pgshim:. python tests/kollegin_teilen_web_rig.py ai '{"enabled": false}'   (KI-Einstellung mischen)
  PYTHONPATH=tests/pgshim:. python tests/kollegin_teilen_web_rig.py note <id>     (Titel + Inhalt einer Seite als JSON)
  PYTHONPATH=tests/pgshim:. python tests/kollegin_teilen_web_rig.py hosts '["https://www.x.example/"]'  (ai_plan.share_host je Adresse)
  PYTHONPATH=tests/pgshim:. python tests/kollegin_teilen_web_rig.py clean          (Einstellungen zurueck, Konto weg)
"""
import json
import os
import sys

import auth
import db

USER = "kgw-web"
PREV = os.path.join(os.environ.get("STATE_DIR") or "/tmp", "kgw-web-prev.json")


def setup():
    db.wait_ready()
    db.init_schema()
    from saga import ai_plan, ai_saga
    port = int(os.environ.get("HK_FAKEAI_PORT", "19048"))
    cur = auth.settings(fresh=True)
    if not os.path.exists(PREV):
        with open(PREV, "w") as f:
            json.dump({"ai": cur.get("ai"), "persona": cur.get("ai_persona"), "plan": cur.get(ai_plan.SETTING)}, f)
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": f"http://127.0.0.1:{port}/v1", "model": "fake-plan",
                             "api_key": "", "groups": [], "timeout": 10, "quota": 0, "context": 8000, "history": True,
                             "pinned": ["127.0.0.1"], "chat": False, "web": False, "caps": {}})
    auth.save_setting(ai_plan.SETTING, {"level": "plans"})
    auth.save_setting("ai_persona", {"name": "Marin"})
    if not auth.get_user(USER):
        auth.create_user(USER, os.environ["KGW_PASS"], "Kim Web")
    auth.settings(fresh=True)
    ai_saga.grant(USER)
    print("rig ok", USER)


def ai(patch):
    cur = dict(auth.settings(fresh=True)["ai"])
    cur.update(json.loads(patch))
    auth.save_setting("ai", cur)
    auth.settings(fresh=True)
    print("ai ok")


def note(nid):
    with db.connect() as c:
        r = c.execute("SELECT title, body, parent_id FROM note WHERE id=? AND uid=?", (int(nid), USER)).fetchone()
    print(json.dumps({"title": r[0], "body": r[1], "parent": r[2]} if r else None, ensure_ascii=False))


def hosts(urls):
    from saga import ai_plan
    print(json.dumps([ai_plan.share_host(u) for u in json.loads(urls)]))


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
        os.remove(PREV)
    if auth.get_user(USER):
        delete_user_data(USER, drop_user=True)
    print("rig clean")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd == "ai":
        ai(sys.argv[2])
    elif cmd == "note":
        note(sys.argv[2])
    elif cmd == "hosts":
        hosts(sys.argv[2])
    elif cmd == "clean":
        clean()
    else:
        print(__doc__)
        sys.exit(2)
