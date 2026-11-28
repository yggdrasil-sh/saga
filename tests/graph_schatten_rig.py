"""Vorbereitung fuer tests/browser_graph_schatten.js (2.6, Bugs-DB 2601/2603, docs/MEMORY.md 10.4): Test-Gedaechtnis mit
eingeschaltetem Schattenmodus fuer ein Konto - Datenbank "Erinnerungen (Schatten-Test)" (Inhalt, Art mit Option
"Schnappschuss", Sensibel, Abrufe, Letzter Abruf), gebunden als memory.db, Ich-Kern-Seite (memory.core), Zeilen mit
Abruf-Zaehlern (einige "gerade bewusst": letzter Abruf < 30 min), drei Schnappschuesse, eine sensible Zeile,
Verknuepfungen (mem_link) mit verschiedenen Gewichten und Herkunft (aehnlich/gemeinsam/hand, eine gepinnt) und Schatten-
Spuren der letzten 24 h (mem_trace mit neu -> hits24), dazu ein echter API-Token mit Bereich memory. Embeddings aus
(Rueckfall Trigramme). Die Frage "query" (POST /api/v1/memory/recall) trifft "Tomaten" und bringt ueber den gepinnten
Link "Basilikum" als Schatten-Treffer (Live-Ereignis memory.recalled mit schatten). Erfundene Testdaten.
  PYTHONPATH=tests/pgshim:. python tests/graph_schatten_rig.py setup <konto> [--aus]   -> eine JSON-Zeile auf stdout {db, core,
                                                       rows, snaps, sensibel, links, query, schatten_id, token, shadow} (Log auf stderr)
  PYTHONPATH=tests/pgshim:. python tests/graph_schatten_rig.py clean <konto>
--aus: alles gleich, aber Schattenmodus aus (memory.shadow fehlt; Graph ohne links/hits24, Einstellungen ohne Schalter).
Zustand (angelegte IDs) in GS_STATE (Standard /tmp/graph-schatten-<konto>.json)."""
import json
import os
import secrets
import sys
import time

import auth
import db

# Titel, Inhalt, Abrufe, vor wie vielen Minuten zuletzt abgerufen (None = nie)
ROWS = [("Tomaten", "Tomaten brauchen viel Sonne und gleichmaessig Wasser", 24, 5),
        ("Basilikum", "Basilikum mag es warm und hell", 3, 300),
        ("Kompost", "Kompost alle zwei Monate umsetzen", 1, 2400),
        ("Hochbeet", "Hochbeet im Herbst mit Laub auffuellen", 0, None),
        ("Nachtzug", "Nachtzug nach Wien ist bequemer als der Flug", 12, 20),
        ("Wien", "In Wien lieber die Strassenbahn als das Taxi nehmen", 6, 1100),
        ("Packliste", "Ladekabel und Steckdosenadapter immer zuerst einpacken", 2, 4200),
        ("Kaffee", "Kaffee mit 92 Grad aufbruehen, nicht kochend", 30, 1),
        ("Sauerteig", "Sauerteig braucht zwoelf Stunden Ruhe im Kuehlschrank", 0, None),
        ("Brotbacken", "Brot bei 250 Grad anbacken, dann auf 210 herunter", 8, 3000)]
SENS = "Tagebuch"                                   # sensible Zeile (nie ueber Verknuepfungen)
# Verknuepfungen: (a, b, gewicht, herkunft, gepinnt, vor Tagen zuletzt)
LINKS = [("Tomaten", "Basilikum", 1.0, "hand", True, 0), ("Tomaten", "Kompost", 0.62, "aehnlich", False, 1),
         ("Kompost", "Hochbeet", 0.45, "aehnlich", False, 10), ("Basilikum", "Hochbeet", 0.3, "gemeinsam", False, 2),
         ("Nachtzug", "Wien", 0.85, "gemeinsam", False, 0), ("Wien", "Packliste", 0.25, "aehnlich", False, 90),
         ("Kaffee", "Brotbacken", 0.55, "aehnlich", False, 5), ("Sauerteig", "Brotbacken", 0.7, "gemeinsam", False, 3),
         ("Kaffee", "Sauerteig", 0.2, "aehnlich", False, 200)]
# Schatten-Spuren (neu) der letzten 24 h: (Zeile, vor Stunden)
HITS = [("Hochbeet", 1), ("Hochbeet", 5), ("Hochbeet", 20), ("Packliste", 3), ("Sauerteig", 0.5)]
PROPS = [{"name": "Inhalt", "type": "text"}, {"name": "Art", "type": "select", "options": ["Fakt", "Schnappschuss"]},
         {"name": "Sensibel", "type": "checkbox"}, {"name": "Abrufe", "type": "number"}, {"name": "Letzter Abruf", "type": "date"}]


def _state(uid):
    return os.environ.get("GS_STATE") or f"/tmp/graph-schatten-{uid}.json"


def _tok(uid):
    from saga import apitok
    return apitok.Tok(10 ** 9 + 26031, uid, "gs", "sga_gs", apitok.SCOPES, None, None)


def setup(uid, off=False):
    db.wait_ready()
    from saga import api_struct, api_v1, apitok, memory
    from saga.core import _tok_hash
    clean(uid, quiet=True)
    if not auth.get_user(uid):
        sys.exit(f"Konto {uid} fehlt")
    auth.save_setting("ai_embed", dict(auth.DEFAULTS["ai_embed"]))
    tk = _tok(uid)
    mem = api_struct.database_create(tk, {"title": "Erinnerungen (Schatten-Test)", "properties": PROPS})[1]["id"]
    core = api_v1.page_create(tk, {"title": "Ich-Kern (Schatten-Test)", "markdown": "Ich bin die Test-Assistentin."})[1]["id"]
    now = time.time()
    with db.connect() as c, c:
        for k, v in (("memory.db", str(mem)), ("memory.core", str(core))):
            c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, ?, ?) ON CONFLICT (uid, k) DO UPDATE SET v=EXCLUDED.v", (uid, k, v))
    rows = {}
    for title, text, hits, ago in ROWS:
        rid = api_v1.row_create(tk, mem, {"title": title, "properties": {"Inhalt": text, "Art": "Fakt"}})[1]["id"]
        rows[title] = rid
        if hits or ago is not None:
            with db.connect() as c, c:
                c.execute("INSERT INTO mem_vec (note_id, uid, model, dim, vec, text_hash, hits, last_hit, mirrored, updated) "
                          "VALUES (?,?,'',0,?,'',?,?,?,?)", (rid, uid, b"", hits, None if ago is None else now - ago * 60, now, now))
    sens = api_v1.row_create(tk, mem, {"title": SENS, "properties": {"Inhalt": "Persoenliche Notiz zu Tomaten", "Sensibel": True,
                                                                       "Art": "Fakt"}})[1]["id"]
    snaps = [memory.nap(uid, {"stimmung": s, "gedanke": g}, tk)[1]["id"]
             for s, g in (("ruhig", "Morgen weiter mit dem Garten."), ("neugierig", "Die Reiseplanung ist fast fertig."),
                          ("zufrieden", "Brot ist im Ofen."))]
    with db.connect() as c, c:
        for k, sid in enumerate(snaps):              # Schnappschuesse: vor 3, 2, 1 Tagen
            c.execute("UPDATE note SET created=? WHERE id=?", (now - (3 - k) * 86400, sid))
        for a, b, g, her, pin, ago in LINKS:
            x, y = sorted((rows[a], rows[b]))
            z = now - ago * 86400
            c.execute("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft, gepinnt) "
                      "VALUES (?,?,?,?,?,?,?,?,?,? <> 0)", (x, y, uid, g, 0.05, 1 if her == "gemeinsam" else 0, z, z, her, int(pin)))
        for title, h in HITS:
            c.execute("INSERT INTO mem_trace (uid, at, quelle, frage_hash, stichworte, lim, treffer, neu) VALUES "
                      "(?, ?, 'trigramm', 'rig', 'rig', 5, CAST(? AS JSONB), CAST(? AS JSONB))",
                      (uid, now - h * 3600, json.dumps([rows["Tomaten"]]), json.dumps([rows[title]])))
        if not off:
            c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, 'memory.shadow', ?) ON CONFLICT (uid, k) DO UPDATE SET v=EXCLUDED.v",
                      (uid, str(int(now - 3 * 86400))))
    raw = apitok.PREFIX + secrets.token_urlsafe(32)
    with db.connect() as c, c:
        cur = c.execute("INSERT INTO api_token (uid, name, hash, prefix, scopes, roots, created, expires) VALUES (?,?,?,?,?,?,?,?)",
                        (uid, "gs-web", _tok_hash(raw), raw[:12], json.dumps(apitok.norm_scopes(["memory"])), None, now, now + 7200))
        tid = cur.lastrowid
    st = {"db": mem, "core": core, "rows": rows, "snaps": snaps, "sensibel": sens, "token_id": tid, "shadow": not off,
          "links": [[min(rows[a], rows[b]), max(rows[a], rows[b]), g, {"aehnlich": "a", "gemeinsam": "g", "hand": "h"}[her], 1 if pin else 0]
                    for a, b, g, her, pin, _ago in LINKS],
          "query": "Tomaten Sonne Wasser", "schatten_id": rows["Basilikum"]}
    with open(_state(uid), "w") as f:
        json.dump(st, f)
    sys.__stdout__.write(json.dumps(dict(st, token=raw)) + "\n")       # stdout: nur diese eine JSON-Zeile
    sys.__stdout__.flush()


def clean(uid, quiet=False):
    try:
        with open(_state(uid)) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = None
    with db.connect() as c, c:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k LIKE 'memory.%'", (uid,))
        for t in ("mem_vec", "mem_link", "mem_trace", "mem_link_log"):
            c.execute(f"DELETE FROM {t} WHERE uid=?", (uid,))
        if st:
            ids = [st["db"], st["core"], st["sensibel"]] + list(st["rows"].values()) + list(st["snaps"])
            c.execute("DELETE FROM note WHERE uid=? AND (id = ANY(?) OR parent_id = ?)", (uid, ids, st["db"]))
            c.execute("DELETE FROM api_token WHERE id=? AND uid=?", (st["token_id"], uid))
    if st:
        os.remove(_state(uid))
    if not quiet:
        print("rig clean")


if __name__ == "__main__":
    a = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if a == "setup":
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):      # Log-Zeilen (z. B. "gedaechtnis: ... Nickerchen") nach stderr
            setup(sys.argv[2], "--aus" in sys.argv[3:])
    else:
        clean(sys.argv[2])
