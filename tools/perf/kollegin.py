"""Messung "Kollegin: Kontext-Auswahl" (Saga 2.6 K-A4, Bugs-DB 2266): saga/ai_ctx.select im Prozess gegen echtes Postgres.

Stufe S: 3.000 eigene Seiten (so viele liest ai.visible_docs hoechstens: SCAN_MAX), 60 Aufgaben.
Stufe M (wie gen.py M): 8.050 eigene Notizen (2.000 Seiten, 50 Datenbanken mit 6.000 Zeilen - gelesen werden die neuesten
3.000) und 300 Seiten einer anderen Person unter einer freigegebenen Seite (Freigabe mit Vererbung), 60 Aufgaben.
Stufe G (Grenzfall, ohne Grenzwert): 3.000 eigene + 3.000 geteilte Seiten - mehr Auszuege als der Zwischenspeicher
(AI_CACHE_DOCS, Standard 5.000) fasst.
Gemessen: p50/p95/max je Auswahl (Seiten + Aufgaben) ueber verschiedene Fragen, Zwischenspeicher der Auszuege warm
(erster Lauf kalt, wird mit ausgegeben). Grenzwerte: tools/perf/limits.json, Stufe S/M, Name "Kollegin: Kontext-Auswahl".

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin.py [--stufe SMG] [--n 10]
    (saga-test: tools/testumgebung/umgebung.sh lauf server <slot> $PWD/tools/perf/kollegin.py)
Wegwerf-Konten kp-*; raeumt auf. Exit 1, wenn ein Grenzwert ueberschritten ist."""
import argparse
import json
import os
import random
import statistics
import sys
import time

from db import connect
from saga import ai_ctx

NAME = "Kollegin: Kontext-Auswahl"
HERE = os.path.dirname(os.path.abspath(__file__))
WORDS = ("Garten Tomaten Zucchini Rechnung Steuer Urlaub Kiel Hamburg Zahnarzt Termin Projekt Budget Server Router WLAN "
         "Passwort Umzug Kartons Transporter Linsensuppe Moehren Rezept Kuchen Geburtstag Schule Elternabend Auto Reifen "
         "Werkstatt Versicherung Vertrag Kuendigung Strom Gas Wasser Heizung Fenster Dach Handwerker Angebot Rechnung "
         "Meeting Protokoll Aufgabe Liste Einkauf Milch Brot Kaese Bibliothek Buch Roman Kapitel Figur Szene Notiz Idee").split()
QUERIES = ("Wann ist der Zahnarzt Termin?", "Was kostet das Projekt Budget?", "Welche Handwerker haben ein Angebot geschickt?",
           "Rezept fuer Linsensuppe mit Moehren", "Was steht im Protokoll vom Meeting?", "Wie ist das WLAN Passwort am Router?",
           "Was muss ich fuer den Umzug packen?", "Wann ist der Elternabend in der Schule?", "Reifen wechseln Werkstatt",
           "Welche Kapitel hat der Roman?")


def page_body(rnd, i):
    text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(40, 160)))
    return json.dumps({"blocks": [{"id": f"b{k}", "type": "paragraph", "data": {"text": text[k * 400:(k + 1) * 400] or "-"}}
                                  for k in range(max(1, len(text) // 400 + 1))]})


SCHEMA = json.dumps({"props": [{"id": "st", "name": "Status", "type": "text"}, {"id": "nr", "name": "Nummer", "type": "number"}]})


def gen(stufe):
    rnd = random.Random(2266)
    now = time.time()
    own, shared, dbs, per_db = {"S": (3000, 0, 0, 0), "M": (2000, 300, 50, 120), "G": (3000, 3000, 0, 0)}[stufe]
    with connect() as c, c:
        rows = [("kp-a", f"{rnd.choice(WORDS)} {rnd.choice(WORDS)} {i}", page_body(rnd, i), now - i * 60, now - i * 60, "blocks")
                for i in range(own)]
        c.executemany("INSERT INTO note (uid, title, body, created, updated, format) VALUES (?,?,?,?,?,?)", rows)
        for d in range(dbs):                       # Datenbanken mit Zeilen (zeitlich mit den Seiten gemischt)
            dbid = c.execute("INSERT INTO note (uid, title, body, created, updated, format, kind, schema) VALUES (?,?,?,?,?,?,?,?)",
                             ("kp-a", f"Liste {rnd.choice(WORDS)} {d}", "", now - d * 900, now - d * 900, "text", "database",
                              SCHEMA)).lastrowid
            c.executemany("INSERT INTO note (uid, title, body, created, updated, format, parent_id, props) VALUES (?,?,?,?,?,?,?,?)",
                          [("kp-a", f"{rnd.choice(WORDS)} {rnd.choice(WORDS)}", "", now - rnd.randint(0, 200000),
                            now - rnd.randint(0, 200000), "text", dbid, json.dumps({"st": rnd.choice(WORDS), "nr": i}))
                           for i in range(per_db)])
        c.executemany("INSERT INTO task (uid, text, done, created, prio) VALUES (?,?,0,?,2)",
                      [("kp-a", f"{rnd.choice(WORDS)} erledigen {i}", now) for i in range(60)])
        if shared:
            root = c.execute("INSERT INTO note (uid, title, body, created, updated, format) VALUES (?,?,?,?,?,?)",
                             ("kp-b", "Geteilter Bereich", page_body(rnd, 0), now, now, "blocks")).lastrowid
            c.executemany("INSERT INTO note (uid, title, body, created, updated, format, parent_id) VALUES (?,?,?,?,?,?,?)",
                          [("kp-b", f"{rnd.choice(WORDS)} geteilt {i}", page_body(rnd, i), now - i * 30, now - i * 30, "blocks", root)
                           for i in range(shared)])
            c.execute("INSERT INTO note_share (note_id, owner_uid, target_type, target, role, created) VALUES (?,?,?,?,?,?)",
                      (root, "kp-b", "user", "kp-a", "view", now))


def cleanup():
    with connect() as c, c:
        c.execute("DELETE FROM note_share WHERE owner_uid LIKE 'kp-%'")
        c.execute("DELETE FROM note WHERE uid LIKE 'kp-%'")
        c.execute("DELETE FROM task WHERE uid LIKE 'kp-%'")
        c.execute("DELETE FROM user_kv WHERE uid LIKE 'kp-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'kp-%'")


def measure(n):
    want = {"pages": True, "tasks": True}
    t0 = time.perf_counter()
    cx = ai_ctx.select("kp-a", QUERIES[0], way="ask", budget=8000, want=want)
    cold = time.perf_counter() - t0
    times, reads = [], 0
    for _ in range(n):
        for q in QUERIES:
            t0 = time.perf_counter()
            cx = ai_ctx.select("kp-a", q, way="ask", budget=8000, want=want)
            times.append(time.perf_counter() - t0)
            reads = max(reads, len(cx.read))
    times.sort()
    q95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
    return {"name": NAME, "n": len(times), "p50_ms": round(statistics.median(times) * 1000, 1), "p95_ms": round(q95 * 1000, 1),
            "max_ms": round(times[-1] * 1000, 1), "cold_ms": round(cold * 1000, 1), "read_max": reads}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", default="SMG")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    with open(os.path.join(HERE, "limits.json"), encoding="utf-8") as f:
        limits = json.load(f)
    fail = 0
    for st in [x for x in a.stufe if x in "SMG"]:
        cleanup()
        try:
            gen(st)
            r = measure(a.n)
        finally:
            cleanup()
        lim = limits.get(st, {}).get(NAME)
        good = lim is None or r["p95_ms"] <= lim
        fail |= not good
        print(("INFO" if lim is None else "OK  " if good else "FAIL"), f"Stufe {st}: {NAME} p95 {r['p95_ms']} ms (Grenze {lim}), p50 {r['p50_ms']}, "
              f"max {r['max_ms']}, kalt {r['cold_ms']} ms, n={r['n']}, read bis {r['read_max']} Kennungen, "
              f"AI_CACHE_DOCS={os.environ.get('AI_CACHE_DOCS', '5000')}", flush=True)
        print("PERF " + json.dumps(dict(r, stufe=st, limit=lim)), flush=True)
    print("Kollegin-Messung", "- ALLES GRUEN" if not fail else "- FEHLER")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
