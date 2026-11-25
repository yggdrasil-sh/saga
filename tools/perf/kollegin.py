"""Messung "Kollegin: Kontext-Auswahl" (Saga 2.6 K-A4, Bugs-DB 2266): saga/ai_ctx.select im Prozess gegen echtes Postgres.

Stufe S: 3.000 eigene Seiten (so viele liest ai.visible_docs hoechstens: SCAN_MAX), 60 Aufgaben.
Stufe M (wie gen.py M): 8.050 eigene Notizen (2.000 Seiten, 50 Datenbanken mit 6.000 Zeilen - gelesen werden die neuesten
3.000) und 300 Seiten einer anderen Person unter einer freigegebenen Seite (Freigabe mit Vererbung), 60 Aufgaben.
Stufe G (Grenzfall, Bugs-DB 2349): 3.000 eigene + 5.000 geteilte Seiten - gelesen werden 3.000 eigene (SCAN_MAX) und die
zuletzt geaenderten 3.000 geteilten (SCAN_MAX_SHARED); 6.000 Seiten sind mehr, als AI_CACHE_DOCS (Standard 5.000) allein
fasst - die Obergrenze des Zwischenspeichers richtet sich nach dem Bedarf (ai._cap_docs, Deckel AI_CACHE_MB).
--geteilt N: andere Zahl geteilter Seiten in Stufe G (Vorher-Messung 2349: 3.000). --speicher: Speicher je Eintrag des
Zwischenspeichers mit tracemalloc messen und mit der Schaetzung ai._entry_bytes vergleichen.
Gemessen: p50/p95/max je Auswahl (Seiten + Aufgaben) ueber verschiedene Fragen, Zwischenspeicher der Auszuege warm
(erster Lauf kalt, wird mit ausgegeben). Grenzwerte: tools/perf/limits.json, Stufe S/M/G, Name "Kollegin: Kontext-Auswahl".

    PYTHONPATH=tests/pgshim:. python tools/perf/kollegin.py [--stufe SMG] [--n 10] [--geteilt N] [--speicher]
    (saga-test: tools/testumgebung/umgebung.sh lauf server <slot> $PWD/tools/perf/kollegin.py)
Wegwerf-Konten kp-*; raeumt auf. Exit 1, wenn ein Grenzwert ueberschritten ist."""
import argparse
import json
import os
import random
import statistics
import sys
import time
import tracemalloc

from db import connect
from saga import ai, ai_ctx

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


def gen(stufe, geteilt=None):
    rnd = random.Random(2266)
    now = time.time()
    own, shared, dbs, per_db = {"S": (3000, 0, 0, 0), "M": (2000, 300, 50, 120), "G": (3000, 5000, 0, 0)}[stufe]
    if stufe == "G" and geteilt is not None:
        shared = geteilt
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


def speicher():
    """Speicher je Eintrag: Zwischenspeicher leeren, alle sichtbaren Seiten zerlegen (mit tracemalloc), mit der Schaetzung
    vergleichen. Zaehlt auch den Zuwachs des Stamm-Caches (stem) mit - er gehoert zum echten Bedarf."""
    with connect() as c:
        docs = ai.visible_docs(c, "kp-a")
    with ai._IDX_LOCK:
        ai._IDX.clear()
        ai._IDX_BYTES[0] = 0
    ai._note_demand("kp-a", len(docs))
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    for d in docs:
        ai._doc_index(d, -1)
    real = tracemalloc.get_traced_memory()[0] - base
    tracemalloc.stop()
    est = ai._IDX_BYTES[0]
    n = len(ai._IDX)
    return {"docs": len(docs), "eintraege": n, "real_kb_je": round(real / max(1, n) / 1024, 1),
            "schaetzung_kb_je": round(est / max(1, n) / 1024, 1), "verhaeltnis": round(est / max(1, real), 2),
            "real_mb": round(real / 1048576, 1), "body_kb_je": round(sum(len(d.get("body") or "") for d in docs) / max(1, len(docs)) / 1024, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stufe", default="SMG")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--geteilt", type=int, default=None)
    ap.add_argument("--speicher", action="store_true")
    a = ap.parse_args()
    with open(os.path.join(HERE, "limits.json"), encoding="utf-8") as f:
        limits = json.load(f)
    fail = 0
    for st in [x for x in a.stufe if x in "SMG"]:
        cleanup()
        try:
            gen(st, a.geteilt)
            r = measure(a.n)
            if hasattr(ai, "cache_view"):
                r["cache"] = ai.cache_view()
            if a.speicher and hasattr(ai, "_entry_bytes"):
                r["speicher"] = speicher()
        finally:
            cleanup()
        lim = limits.get(st, {}).get(NAME)
        good = lim is None or r["p95_ms"] <= lim
        fail |= not good
        print(("INFO" if lim is None else "OK  " if good else "FAIL"), f"Stufe {st}: {NAME} p95 {r['p95_ms']} ms (Grenze {lim}), p50 {r['p50_ms']}, "
              f"max {r['max_ms']}, kalt {r['cold_ms']} ms, n={r['n']}, read bis {r['read_max']} Kennungen, "
              f"AI_CACHE_DOCS={os.environ.get('AI_CACHE_DOCS', '5000')}, SCAN_MAX_SHARED={getattr(ai, 'SCAN_MAX_SHARED', '-')}", flush=True)
        print("PERF " + json.dumps(dict(r, stufe=st, limit=lim)), flush=True)
    print("Kollegin-Messung", "- ALLES GRUEN" if not fail else "- FEHLER")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
