"""Vorbereitung fuer tests/browser_gedaechtnis.js (2.6, Bugs-DB 2581, Web): Konto gw-web (Passwort GW_PASS) mit zwei eigenen
Datenbanken und zwei Seiten, Admin-Konto gw-adm, KI an mit dem Fake-Modell (tests/fakeai.py auf HK_FAKEAI_PORT), damit der
Bereich "Saga" wie bei echten Konten neben "Gedaechtnis" steht. Embeddings ab Werk aus (Rueckfall Trigramme).
Erfundene Testdaten, keine echten Namen.
  PYTHONPATH=tests/pgshim:. GW_PASS=... HK_FAKEAI_PORT=8915 python tests/gedaechtnis_web_rig.py setup
  PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py admin-cookie     -> Wert fuer das Cookie hk_admin
  PYTHONPATH=tests/pgshim:. python tests/gedaechtnis_web_rig.py clean
Datenbanken: "Erinnerungen (Web-Test)" - Inhalt, Schlagworte, Projekt, Art, Wichtigkeit, Datum, Letzter Abruf passen;
Valenz/Erregung fehlen; "Abrufe" ist Text (falscher Typ). "Lesezeichen (Web-Test)" ohne Spalte "Inhalt".
"""
import base64
import hashlib
import json
import os
import sys
import time

import auth
import db

USER, ADMIN = "gw-web", "gw-adm"
PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Schlagworte", "type": "multi_select", "options": ["garten", "kochen", "reise"]},
         {"name": "Projekt", "type": "select", "options": ["Gartenbuch", "Reiseplan"]},
         {"name": "Art", "type": "select", "options": ["Erkenntnis", "Vorliebe"]}, {"name": "Wichtigkeit", "type": "number"},
         {"name": "Datum", "type": "date"}, {"name": "Letzter Abruf", "type": "date"}, {"name": "Abrufe", "type": "text"}]
ROWS = [("Tomaten", "Tomaten brauchen viel Sonne und gleichmaessig Wasser", "Gartenbuch"),
        ("Basilikum", "Basilikum nicht neben Tomaten in den Schatten stellen", "Gartenbuch"),
        ("Zugfahrt", "Nachtzug nach Wien ist bequemer als der Flug", "Reiseplan"),
        ("Brot", "Sauerteig braucht zwoelf Stunden Ruhe im Kuehlschrank", None)]


def _tok(uid):
    from saga import apitok
    return apitok.Tok(10 ** 9 + 25810, uid, "gw", "sga_gw", apitok.SCOPES, None, None)


def setup():
    db.wait_ready()
    db.init_schema()
    from saga import ai_saga, api_struct, api_v1
    clean(quiet=True)
    port = int(os.environ.get("HK_FAKEAI_PORT", "8915"))
    auth.save_setting("ai", {"enabled": True, "provider": "openai", "url": f"http://127.0.0.1:{port}/v1", "model": "fake",
                             "api_key": "", "groups": [], "timeout": 10, "quota": 0, "context": 8000, "history": False,
                             "pinned": ["127.0.0.1"], "chat": False, "web": False, "caps": {}})
    auth.save_setting("ai_persona", {"name": "Marin"})
    auth.save_setting("ai_embed", dict(auth.DEFAULTS["ai_embed"]))
    for name, disp, adm in ((USER, "Gerda Web", False), (ADMIN, "Admin Web", True)):
        _u, err = auth.create_user(name, os.environ["GW_PASS"], disp, is_admin=adm)
        if err:
            sys.exit(f"Konto {name}: {err}")
    auth.settings(fresh=True)
    ai_saga.grant(USER)
    tk = _tok(USER)
    mem = api_struct.database_create(tk, {"title": "Erinnerungen (Web-Test)", "properties": PROPS})[1]["id"]
    for title, text, proj in ROWS:
        props = {"Inhalt": text}
        if proj:
            props["Projekt"] = proj
        api_v1.row_create(tk, mem, {"title": title, "properties": props})
    other = api_struct.database_create(tk, {"title": "Lesezeichen (Web-Test)", "properties": [{"name": "Notiz", "type": "text"}]})[1]["id"]
    api_v1.row_create(tk, other, {"title": "Ein Lesezeichen", "properties": {"Notiz": "nur ein Test"}})
    # Ich-Kern-Auswahl zeigt hoechstens 300 Seiten (neueste zuerst): eine alte Seite hinter 310 neueren - nur per Suche waehlbar
    old = api_v1.page_create(tk, {"title": "Alte Kernseite (Web-Test)", "markdown": "Ganz alt."})[1]["id"]
    with db.connect() as c, c:
        c.execute("UPDATE note SET updated = updated - 86400 * 400 WHERE id=?", (old,))
        now = time.time()
        c.executemany("INSERT INTO note (uid, title, body, created, updated) VALUES (?,?,?,?,?)",
                      [(USER, f"Füllseite {i:03d}", "", now - 1000 + i, now - 1000 + i) for i in range(310)])
    core = api_v1.page_create(tk, {"title": "Marin – Ich-Kern (Web-Test)", "markdown": "# Ich\n\nIch bin Marin, neugierig und direkt."})[1]["id"]
    api_v1.page_create(tk, {"title": "Projektnotizen (Web-Test)", "markdown": "Nur eine Seite."})
    print(json.dumps({"rig": "ok", "user": USER, "mem": mem, "other": other, "core": core}))


def admin_cookie():
    from itsdangerous import TimestampSigner
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest())
    print(sa.sign(base64.b64encode(json.dumps({"aid": ADMIN, "ep": time.time() + 5, "csrf": "gw-csrf-token"}).encode())).decode())


def clean(quiet=False):
    from saga.userdata import delete_user_data
    for u in (USER, ADMIN):
        if auth.get_user(u):
            delete_user_data(u, drop_user=True)
    auth.save_setting("ai_embed", dict(auth.DEFAULTS["ai_embed"]))
    if not quiet:
        print("rig clean")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "setup"
    {"setup": setup, "clean": clean, "admin-cookie": admin_cookie}[a]()
