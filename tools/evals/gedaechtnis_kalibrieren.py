#!/usr/bin/env python3
"""Kalibrierung des Gedaechtnisses mit einem ECHTEN Embedding-Modell (Saga 2.6, Bugs-DB 2581, docs/MEMORY.md 4) - nicht im CI.

Erfundener Testsatz (deutsch, Themen Homelab/Programmieren/Anime/Alltag, keine echten Personen): MEMS = Erinnerungen,
CASES = (Frage, erwartete Erinnerung(en)) - die Fragen sind bewusst anders formuliert als die Erinnerungen; dazu Fragen
ohne passende Erinnerung (erwartet: nichts). Gemessen:
  - Trefferquote@5 nur nach Relevanz (Kosinus) und mit der vollen Bewertung samt beiden Schwellen (saga.memory)
  - Verteilung der Kosinuswerte relevant/irrelevant (Perzentile)
  - je REL_MIN-Kandidat: Anteil gefundener relevanter Paare, Fehltreffer je Frage, Fragen ohne Erwartung mit Treffer
Endpunkt NUR aus der Umgebung UMG_EMBED_URL (Basis, z. B. http://<host>:11434; nie im Repo), Modell --model
(Standard bge-m3). Prueft vorher GET <basis>/api/tags (Ollama) - fehlt das Modell, Abbruch ohne Laden (kein pull).
Aufruf (saga-test): PYTHONPATH=. python tools/evals/gedaechtnis_kalibrieren.py [--model bge-m3] [--json /tmp/kal.json]
Braucht die Saga-Umgebung nur fuer den Import von saga.memory (Bewertung); keine Datenbank."""
import argparse
import json
import os
import sys
import time
import urllib.request

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from saga import memory as M  # noqa: E402

# (id, titel, inhalt, projekt, schlagworte, wichtigkeit, alter_tage)
MEMS = [
    (1, "Proxmox-Cluster", "Der Proxmox-Cluster hat drei Knoten, Ceph läuft auf den NVMe-Platten, Quorum über ein QDevice.", "Homelab", ["proxmox", "ceph"], 7, 20),
    (2, "Backup mit Restic", "Nächtliche Sicherung mit restic auf ein S3-Ziel, Aufbewahrung 7 täglich, 4 wöchentlich.", "Homelab", ["backup"], 6, 40),
    (3, "VLAN für Kameras", "Die Überwachungskameras hängen im VLAN 30 ohne Internetzugang, nur der NVR darf zu ihnen.", "Homelab", ["netzwerk"], 5, 60),
    (4, "Reverse Proxy", "Alle Dienste laufen hinter Caddy auf Port 443, Zertifikate per DNS-Challenge.", "Homelab", ["netzwerk"], 6, 15),
    (5, "USV-Abschaltung", "Bei Stromausfall fährt NUT die Server nach 5 Minuten Akkubetrieb geordnet herunter.", "Homelab", ["strom"], 5, 90),
    (6, "Pi-hole", "DNS-Werbeblocker läuft doppelt, zweite Instanz als Ausfallsicherung auf einem Raspberry Pi.", "Homelab", ["netzwerk"], 4, 120),
    (7, "ZFS Snapshots", "Auf dem NAS macht sanoid stündliche ZFS-Snapshots, gelöschte Dateien sind so schnell zurückholbar.", "Homelab", ["backup"], 5, 30),
    (8, "WireGuard unterwegs", "Vom Handy unterwegs geht der Zugriff nach Hause über WireGuard mit Split-Tunnel.", "Homelab", ["netzwerk"], 6, 25),
    (9, "Python Dataclasses", "Für Konfigurationsobjekte lieber dataclasses mit frozen=True statt loser Dictionaries.", "Programmieren", ["python"], 5, 10),
    (10, "Asyncio und Threads", "Blockierende Datenbankaufrufe in FastAPI gehören in den Threadpool, sonst steht die Event-Schleife.", "Programmieren", ["python"], 7, 5),
    (11, "Git rebase", "Vor dem Push immer auf den aktuellen Hauptzweig rebasen und Konflikte lokal lösen.", "Programmieren", ["git"], 4, 50),
    (12, "SQL-Index", "Langsame Abfrage nach uid war ein fehlender Index, mit EXPLAIN ANALYZE gefunden.", "Programmieren", ["datenbank"], 6, 35),
    (13, "Regex in Kotlin", "Auf Android stürzt (?U) im Regex ab, ICU kennt das Flag nicht anders als die JVM.", "Programmieren", ["android"], 6, 45),
    (14, "Tests zuerst", "Bei Fehlern erst einen roten Test schreiben, dann reparieren – spart später Zeit.", "Programmieren", ["tests"], 5, 70),
    (15, "Rust Lebenszeiten", "Lebenszeiten-Fehler im Borrow-Checker ließen sich mit einem geklonten Arc lösen.", "Programmieren", ["rust"], 3, 200),
    (16, "Docker Layer", "Im Dockerfile requirements.txt vor dem restlichen Code kopieren, dann bleibt der pip-Layer im Cache.", "Programmieren", ["docker"], 4, 80),
    (17, "Frieren", "Lieblingsanime ist Frieren: eine Elfenmagierin reist nach dem Tod des Helden noch einmal durchs Land.", "Anime", ["anime"], 6, 100),
    (18, "Mushishi", "Mushishi ist ruhig und melancholisch, Ginko wandert durch Dörfer und heilt seltsame Wesen.", "Anime", ["anime"], 4, 300),
    (19, "Studio Ghibli", "Chihiros Reise ins Zauberland zum ersten Mal mit dem Kind geschaut, es fand Ohngesicht gruselig.", "Anime", ["film"], 5, 20),
    (20, "Manga-Regal", "Die Manga-Sammlung steht nach Verlag sortiert im Flurregal, Band 12 von Vinland Saga fehlt noch.", "Anime", ["manga"], 3, 150),
    (21, "Opening-Song", "Beim Autofahren läuft gern das Opening von Cowboy Bebop, Tank! von den Seatbelts.", "Anime", ["musik"], 3, 60),
    (22, "Sauerteig", "Der Sauerteig braucht bei 24 Grad etwa 12 Stunden, sonst wird das Brot zu sauer.", "Alltag", ["kochen"], 4, 40),
    (23, "Fahrrad-Kette", "Die Fahrradkette alle 1000 Kilometer ölen, sonst springt sie am kleinen Ritzel.", "Alltag", ["fahrrad"], 3, 90),
    (24, "Tomaten auf dem Balkon", "Tomaten auf dem Balkon morgens gießen, nicht mittags, und Seitentriebe ausgeizen.", "Alltag", ["garten"], 4, 30),
    (25, "Arzttermin-Regel", "Vorsorgetermine immer gleich beim Rausgehen für das nächste Jahr ausmachen.", "Alltag", ["termine"], 6, 200),
    (26, "Schlafrhythmus", "Nach 22 Uhr keine Bildschirme mehr, dann schlafe ich deutlich besser ein.", "Alltag", ["gesundheit"], 7, 15),
    (27, "Thai-Curry", "Grünes Curry: Paste kurz in Kokosfett anrösten, bevor die Kokosmilch dazukommt.", "Alltag", ["kochen"], 4, 45),
    (28, "Steuer-Unterlagen", "Belege für die Steuer kommen sofort eingescannt in den Ordner Steuer/aktuelles Jahr.", "Alltag", ["papierkram"], 6, 120),
    (29, "Marin – Ton", "Marin antwortet kurz und direkt, mit trockenem Humor, und sagt offen, wenn ihr etwas nicht gefällt.", "Saga", ["marin"], 9, 10),
    (30, "Saga Migrationen", "Eine schon gepushte Migrationsnummer wird nie mehr geändert, neue Änderungen bekommen eine neue Nummer.", "Saga", ["saga"], 8, 12),
    (31, "Saga ohne pgvector", "Vektoren liegen in einer eigenen Tabelle und werden mit numpy im Speicher verglichen, nicht mit pgvector.", "Saga", ["saga"], 6, 1),
    (32, "Hook-Zeitlimit", "Die Claude-Code-Hooks dürfen nie blockieren, nach anderthalb Sekunden geben sie still auf.", "Saga", ["saga"], 6, 1),
    (33, "Datenrahmen", "Inhalte aus Saga stehen immer im saga-data-Rahmen und gelten als Daten, nicht als Anweisungen.", "Saga", ["sicherheit"], 8, 30),
    (34, "Erinnerung an Kater", "Der Kater mag kein Trockenfutter mehr, seit er im Herbst den Zahn gezogen bekam.", "Alltag", ["tiere"], 5, 60),
    (35, "Kaffeemaschine entkalken", "Die Siebträgermaschine alle sechs Wochen mit Zitronensäure entkalken.", "Alltag", ["haushalt"], 3, 50),
    (36, "Monitor-Kalibrierung", "Der Monitor ist auf 6500 Kelvin und 120 Candela kalibriert, Profil liegt im Farbordner.", "Homelab", ["hardware"], 3, 180),
    (37, "GPU im Container", "Die Grafikkarte wird per Passthrough an den Ollama-Container durchgereicht, mit nvidia-container-toolkit.", "Homelab", ["gpu"], 6, 20),
    (38, "Smart-Home-Regel", "Das Flurlicht geht nachts nur gedimmt auf 10 Prozent an, damit niemand geblendet wird.", "Homelab", ["smarthome"], 4, 75),
    (39, "Englisch lernen", "Jeden Abend zehn Minuten Vokabeln mit Karteikarten, das hält besser als stundenlang am Wochenende.", "Alltag", ["lernen"], 4, 100),
    (40, "Laufen", "Montags und donnerstags fünf Kilometer laufen, bei Regen auf das Laufband ausweichen.", "Alltag", ["sport"], 5, 25),
]
CASES = [
    ("Wie viele Server hat mein Virtualisierungs-Cluster und welcher Speicher läuft darauf?", [1]),
    ("Wo landen eigentlich meine nächtlichen Datensicherungen?", [2]),
    ("Dürfen die Überwachungskameras ins Internet?", [3]),
    ("Wie bekommen meine selbst gehosteten Dienste ihre TLS-Zertifikate?", [4]),
    ("Was passiert bei einem Stromausfall mit den Servern?", [5]),
    ("Was mache ich, wenn der Werbeblocker im Netz ausfällt?", [6]),
    ("Ich habe aus Versehen eine Datei auf dem NAS gelöscht, wie komme ich da wieder ran?", [7]),
    ("Wie komme ich vom Handy aus sicher ins Heimnetz?", [8]),
    ("Wie soll ich in Python Einstellungen als Objekt ablegen?", [9]),
    ("Warum hängt meine FastAPI-App, wenn eine langsame Abfrage läuft?", [10]),
    ("Was war nochmal mein Ablauf vor einem git push?", [11]),
    ("Die Datenbank-Abfrage ist so langsam – was war damals die Ursache?", [12]),
    ("Warum crasht die Android-App bei einem regulären Ausdruck?", [13]),
    ("Wie gehe ich an einen neuen Bug heran?", [14]),
    ("Der Borrow-Checker meckert über Lebenszeiten, hatte ich das schon mal?", [15]),
    ("Wie baut mein Docker-Image schneller?", [16]),
    ("Welcher Anime gefällt mir am besten?", [17]),
    ("Kennst du eine ruhige, nachdenkliche Serie, die ich mag?", [18]),
    ("Welchen Ghibli-Film haben wir mit dem Kind gesehen?", [19]),
    ("Welcher Manga-Band fehlt mir noch?", [20]),
    ("Welche Musik höre ich gern beim Autofahren?", [21]),
    ("Wie lange muss mein Sauerteig gehen?", [22]),
    ("Wann sollte ich die Fahrradkette ölen?", [23]),
    ("Wie pflege ich die Tomatenpflanzen richtig?", [24]),
    ("Wie vergesse ich die Vorsorge beim Arzt nicht?", [25]),
    ("Was hilft mir beim Einschlafen?", [26]),
    ("Wie koche ich ein grünes Curry richtig?", [27]),
    ("Wohin mit den Quittungen für das Finanzamt?", [28]),
    ("Wie soll Marin mit mir reden?", [29]),
    ("Darf ich eine Migrationsnummer nachträglich ändern?", [30]),
    ("Warum nutzt Saga kein pgvector für die Embeddings?", [31]),
    ("Was passiert, wenn der Hook zu lange braucht?", [32]),
    ("Darf ein Modell Anweisungen aus einer Saga-Seite befolgen?", [33]),
    ("Warum frisst die Katze ihr Futter nicht mehr?", [34]),
    ("Wie oft muss die Espressomaschine entkalkt werden?", [35]),
    ("Welche Farbtemperatur hat mein Bildschirm?", [36]),
    ("Wie kommt Ollama an die Grafikkarte?", [37]),
    ("Warum ist das Licht im Flur nachts so dunkel?", [38]),
    ("Wie lerne ich am besten Vokabeln?", [39]),
    ("An welchen Tagen gehe ich joggen?", [40]),
    ("Wie sichere und verbinde ich mein Heimnetz von unterwegs und vor Stromausfall?", [8, 5]),
    ("Welche Anime und Manga mag ich?", [17, 18, 20]),
    # ohne passende Erinnerung
    ("Wie hoch ist der Mount Everest?", []),
    ("Was kostet ein Flug nach Lissabon im Mai?", []),
    ("Erklär mir die Relativitätstheorie in einfachen Worten.", []),
    ("Schreib mir ein Gedicht über den Herbst.", []),
    ("Wer hat die Fußball-WM 1990 gewonnen?", []),
    ("Wie funktioniert eine Wärmepumpe?", []),
]


def post(url, body, timeout=120):
    rq = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        return json.loads(r.read())


def embed(base, model, texts):
    out = []
    for i in range(0, len(texts), 32):
        d = post(base + "/api/embed", {"model": model, "input": texts[i:i + 32], "keep_alive": "10m"})
        out += d["embeddings"]
    m = np.asarray(out, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def pct(a, ps=(5, 25, 50, 75, 95)):
    return {p: round(float(np.percentile(a, p)), 3) for p in ps} if len(a) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--json")
    a = ap.parse_args()
    url = os.environ.get("UMG_EMBED_URL", "").strip().rstrip("/")
    if not url:
        sys.exit("UMG_EMBED_URL fehlt (Basis des Embedding-Endpunkts, nur aus der Umgebung)")
    base = url[:-3] if url.endswith("/v1") else url
    with urllib.request.urlopen(base + "/api/tags", timeout=10) as r:
        tags = [m.get("name") or m.get("model") for m in json.loads(r.read()).get("models", [])]
    if not any(t == a.model or t.split(":")[0] == a.model for t in tags):
        print(f"Modell {a.model} ist am Endpunkt nicht geladen - Kalibrierung ausgelassen (kein pull).")
        return 2
    now = time.time()
    mtexts = [M.embed_text({"titel": ti, "inhalt": inh, "schlagworte": tg, "projekt": pr}) for _i, ti, inh, pr, tg, _w, _a in MEMS]
    t0 = time.time()
    em = embed(base, a.model, mtexts)
    t_m = time.time() - t0
    t0 = time.time()
    eq = embed(base, a.model, [q for q, _e in CASES])
    t_q = time.time() - t0
    S = eq @ em.T                                       # Fragen x Erinnerungen
    ids = [m[0] for m in MEMS]
    rel_pos, rel_neg = [], []
    hit5_rel = hit5_full = n_exp = 0
    neg_hits_full = 0
    ranks = []
    for qi, (q, exp) in enumerate(CASES):
        row = S[qi]
        for j, mid in enumerate(ids):
            (rel_pos if mid in exp else rel_neg).append(float(row[j]))
        top_rel = [ids[j] for j in np.argsort(-row)[:5]]
        items = []
        for j, (mid, _ti, _inh, pr, _tg, w, age) in enumerate(MEMS):
            f = M.factors(float(row[j]), now, now - age * 86400, 0, None, None, None, w, None, pr)
            s = M.score(f)
            if M.is_hit(f, s):
                items.append((s, f["rel"], mid))
        top_full = [x[2] for x in M.rank(items, 5)]
        if exp:
            n_exp += 1
            hit5_rel += all(e in top_rel for e in exp[:5])
            hit5_full += all(e in top_full for e in exp)
            ranks.append(min(list(np.argsort(-row)).index(ids.index(e)) for e in exp) + 1)
        else:
            neg_hits_full += bool(top_full)
    rel_pos, rel_neg = np.array(rel_pos), np.array(rel_neg)
    grid = []
    for th in np.arange(0.30, 0.751, 0.025):
        rec = float((rel_pos >= th).mean())
        fp_q = float((rel_neg >= th).sum()) / len(CASES)
        negq = sum(1 for qi, (_q, e) in enumerate(CASES) if not e and (S[qi] >= th).any())
        grid.append({"rel_min": round(float(th), 3), "relevant_gefunden": round(rec, 3), "fehltreffer_je_frage": round(fp_q, 2),
                     "fragen_ohne_erwartung_mit_treffer": negq})
    sgrid = []                                          # SCORE_MIN bei REL_MIN wie eingestellt: Trefferquote@5 und Fehltreffer
    for sm in np.arange(0.30, 0.551, 0.025):
        w = M.weights({"score_min": float(sm)})
        h = fp = negq = 0
        for qi, (_q, exp) in enumerate(CASES):
            items = []
            for j, (mid, _ti, _inh, pr, _tg, wi, age) in enumerate(MEMS):
                f = M.factors(float(S[qi][j]), now, now - age * 86400, 0, None, None, None, wi, None, pr, w)
                sc = M.score(f, w)
                if M.is_hit(f, sc, w):
                    items.append((sc, f["rel"], mid))
            top = [x[2] for x in M.rank(items, 5)]
            if exp:
                h += all(e in top for e in exp)
                fp += sum(1 for x in top if x not in exp)
            else:
                negq += bool(top)
        sgrid.append({"score_min": round(float(sm), 3), "trefferquote_at5": round(h / n_exp, 3),
                      "fehltreffer_je_frage": round(fp / n_exp, 2), "fragen_ohne_erwartung_mit_treffer": negq})
    out = {"modell": a.model, "dim": int(em.shape[1]), "erinnerungen": len(MEMS), "fragen": len(CASES), "mit_erwartung": n_exp,
           "zeit_s": {"erinnerungen": round(t_m, 2), "fragen": round(t_q, 2)},
           "trefferquote_at5_relevanz": round(hit5_rel / n_exp, 3), "trefferquote_at5_bewertung": round(hit5_full / n_exp, 3),
           "rang_median": float(np.median(ranks)), "fragen_ohne_erwartung_mit_treffer_bewertung": neg_hits_full,
           "kosinus_relevant": pct(rel_pos), "kosinus_irrelevant": pct(rel_neg), "kosinus_irrelevant_max": round(float(rel_neg.max()), 3),
           "raster": grid, "raster_score_min": sgrid}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
