"""Vorbereitung fuer tests/browser_graph_gedaechtnis.js (2.6, Bugs-DB 2595): Test-Gedaechtnis fuer ein Konto - Datenbank
"Erinnerungen (Graph-Test)" mit Zeilen, gebunden als memory.db, Abruf-Zaehler in mem_vec (verschieden oft und verschieden
lange her abgerufen), drei Themen-Seiten, ein echter API-Token mit Bereich memory (fuer recall ueber REST). Embeddings aus
(Rueckfall Trigramme). Erfundene Testdaten.
  PYTHONPATH=tests/pgshim:. python tests/graph_gedaechtnis_rig.py setup <konto>   -> JSON {db, rows, pages, token}
  PYTHONPATH=tests/pgshim:. python tests/graph_gedaechtnis_rig.py clean <konto>
Zustand (angelegte IDs) in GM_STATE (Standard /tmp/graph-gedaechtnis-<konto>.json)."""
import json
import os
import secrets
import sys
import time

import auth
import db

# Titel, Inhalt, Abrufe, vor wie vielen Stunden zuletzt abgerufen (None = nie), Themen-Seite
ROWS = [("Tomaten", "Tomaten brauchen viel Sonne und gleichmaessig Wasser", 24, 0.3, "Garten"),
        ("Basilikum", "Basilikum nicht neben Tomaten in den Schatten stellen", 3, 2, "Garten"),
        ("Kompost", "Kompost alle zwei Monate umsetzen", 1, 40, "Garten"),
        ("Hochbeet", "Hochbeet im Herbst mit Laub auffuellen", 0, None, "Garten"),
        ("Zugfahrt", "Nachtzug nach Wien ist bequemer als der Flug", 12, 5, "Reisen"),
        ("Wien", "In Wien lieber die Strassenbahn als das Taxi nehmen", 6, 18, "Reisen"),
        ("Packliste", "Ladekabel und Steckdosenadapter immer zuerst einpacken", 2, 70, "Reisen"),
        ("Fahrrad", "Fahrradkette alle 500 Kilometer oelen", 1, 30, "Reisen"),
        ("Kaffee", "Kaffee mit 92 Grad aufbrühen, nicht kochend", 30, 0.1, "Kueche"),
        ("Sauerteig", "Sauerteig braucht zwoelf Stunden Ruhe im Kuehlschrank", 0, None, "Kueche"),
        ("Gewuerze", "Gewuerze dunkel und luftdicht lagern", 4, 10, "Kueche"),
        ("Brotbacken", "Brot bei 250 Grad anbacken, dann auf 210 herunter", 8, 50, "Kueche")]
PAGES = {"Garten": "Gartenplan", "Reisen": "Reisenotizen", "Kueche": "Küchenbuch"}
PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Abrufe", "type": "number"}, {"name": "Letzter Abruf", "type": "date"}]


def _state(uid):
    return os.environ.get("GM_STATE") or f"/tmp/graph-gedaechtnis-{uid}.json"


def _tok(uid):
    from saga import apitok
    return apitok.Tok(10 ** 9 + 25951, uid, "gm", "sga_gm", apitok.SCOPES, None, None)


def setup(uid):
    db.wait_ready()
    from saga import api_struct, api_v1, apitok
    from saga.core import _tok_hash
    clean(uid, quiet=True)
    if not auth.get_user(uid):
        sys.exit(f"Konto {uid} fehlt")
    auth.save_setting("ai_embed", dict(auth.DEFAULTS["ai_embed"]))
    tk = _tok(uid)
    mem = api_struct.database_create(tk, {"title": "Erinnerungen (Graph-Test)", "properties": PROPS})[1]["id"]
    pages = {k: api_v1.page_create(tk, {"title": t, "markdown": "Themen-Seite"})[1]["id"] for k, t in PAGES.items()}
    rows, now = {}, time.time()
    with db.connect() as c, c:
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, 'memory.db', ?) ON CONFLICT (uid, k) DO UPDATE SET v=EXCLUDED.v",
                  (uid, str(mem)))
    for title, text, hits, ago, topic in ROWS:
        rid = api_v1.row_create(tk, mem, {"title": title, "properties": {"Inhalt": text}})[1]["id"]
        rows[title] = rid
        if hits or ago is not None:
            with db.connect() as c, c:
                c.execute("INSERT INTO mem_vec (note_id, uid, model, dim, vec, text_hash, hits, last_hit, mirrored, updated) "
                          "VALUES (?,?,'',0,?,'',?,?,?,?)", (rid, uid, b"", hits, None if ago is None else now - ago * 3600, now, now))
    raw = apitok.PREFIX + secrets.token_urlsafe(32)
    with db.connect() as c, c:
        cur = c.execute("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, "gm-web", _tok_hash(raw), raw[:12], json.dumps(apitok.norm_scopes(["memory"])), None, now, now + 7200))
        tid = cur.lastrowid
    topics = {title: topic for title, _t, _h, _a, topic in ROWS}
    st = {"db": mem, "rows": rows, "pages": pages, "topics": topics, "token_id": tid}
    with open(_state(uid), "w") as f:
        json.dump(st, f)
    print(json.dumps(dict(st, token=raw)))


def clean(uid, quiet=False):
    try:
        with open(_state(uid)) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    with db.connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k LIKE 'memory.%'", (uid,))
        c.execute("DELETE FROM mem_vec WHERE uid=?", (uid,))
        if st:
            ids = [st["db"]] + list(st["rows"].values()) + list(st["pages"].values())
            c.execute("DELETE FROM note WHERE uid=? AND (id = ANY(?) OR parent_id = ?)", (uid, ids, st["db"]))
            c.execute("DELETE FROM api_token WHERE id=? AND uid=?", (st["token_id"], uid))
    if st:
        os.remove(_state(uid))
    if not quiet:
        print("rig clean")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "setup"
    {"setup": setup, "clean": clean}[a](sys.argv[2])
