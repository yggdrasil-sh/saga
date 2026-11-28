# Saga – Gedächtnis (recall/remember, Embeddings, Hooks) – Vertrag

Stand 2.6 (Bugs-DB 2581, Feature `memory1`). Dieser Text ist der Vertrag zwischen Server, Weboberfläche, Hooks und
MCP-Clients. Formeln und Schwellen prüft `tests/vectors/memory_score.json` (Server-Reihe `tests/gedaechtnis.py`).

**Kurz:** Jede Person kann *eine eigene* Datenbank als Gedächtnis und *eine* Seite als Ich-Kern binden. Saga holt zu
einem Text die passendsten Einträge (höchstens 5) – nach Bedeutung (Embeddings, OpenAI-kompatibel oder Ollama) oder,
ohne eingetragenen Endpunkt, nach Wortähnlichkeit (Trigramme). **Die Datenbank bleibt die Quelle der Wahrheit**; Saga
legt nie selbst Spalten oder Auswahl-Optionen an. Ab Werk ist nichts gebunden (= aus). Kein neuer Port, kein Container.

## 1. Einstellungen

### 1.1 Admin: Embedding-Endpunkt (`setting` Schlüssel `ai_embed`)

Getrennt vom Chat-Anbieter der KI (`ai`). Felder (Standard in Klammern):

| Feld | Bedeutung |
|---|---|
| `enabled` (false) | Embeddings nutzen. Aus oder ohne `url`/`model`: Rückfall Trigramme (§4.4) |
| `provider` (`ollama`) | `ollama` = native `POST <url>/api/embed` (mit `keep_alive`), `openai` = `POST <url>/embeddings` (OpenAI, LM Studio, vLLM, Ollama `/v1` …) |
| `url` | `ollama`: `http://host:11434` (Endungen `/api/embed`, `/api`, `/v1` werden entfernt); `openai`: `…/v1` (Endung `/embeddings` wird entfernt) |
| `model` (`bge-m3`) | Modellname beim Endpunkt |
| `api_key` | optional, verschlüsselt gespeichert (wie beim KI-Endpunkt), `Authorization: Bearer …` |
| `keep_alive` (`30m`) | nur `ollama`: so lange hält Ollama das Modell nach einem Aufruf geladen (`5m`, `30m`, `2h`, `-1` = immer, leer = Ollama-Standard). Saga sendet **keine** Warmhalte-Aufrufe |
| `timeout` (15) | Sekunden je Stapel im Hintergrund (2–120) |
| `query_timeout_ms` (1200) | Zeitlimit für das Einbetten der Frage beim recall (200–10 000). Überschritten → dieser Abruf mit Trigrammen (`quelle: "trigramm"`, `hinweis: "embed_timeout"`) |
| `pinned` | beim Speichern aufgelöste IPs (wird vom Server gesetzt) |

Netz-Schutz wie beim KI-Endpunkt (`saga/netguard.py`): verbunden wird nur mit dem Host der Adresse; interne Adressen
nur mit den beim Speichern festgehaltenen IPs; `http` nur intern; lagen beim Speichern alle IPs im Heimnetz, gilt das
auch für `https` (kein Wechsel ins Internet per DNS). Keine Weiterleitungen.
Hinweis für Ollama: `OLLAMA_KEEP_ALIVE` am Ollama-Host wirkt für alle Aufrufe; `keep_alive` hier gilt nur für Saga.

Admin-Endpunkte (Admin-Port, Sitzung des Admins; schreibend mit `X-CSRF-Token` wie `/api/ai/selftest`):

- `GET /api/ai/embed` →
  ```json
  {"settings": {"enabled": true, "provider": "ollama", "url": "http://ollama.lan:11434", "model": "bge-m3",
                "has_key": false, "keep_alive": "30m", "timeout": 15, "query_timeout_ms": 1200},
   "configured": true, "where": "home" | "internet" | null,
   "stats": {"accounts": 2, "rows": 812, "embedded": 800, "stale": 12, "running": false, "models": {"bge-m3": 800}}}
  ```
  `where`: `home` = alle festgehaltenen IPs privat, `internet` = mindestens eine öffentlich (Hinweis „Erinnerungen
  verlassen dann den Server“ zeigt die Oberfläche), `null` = nichts eingetragen.
- `PUT /api/ai/embed` `{enabled, provider, url, model, api_key?, clear_key?, keep_alive, timeout, query_timeout_ms}`
  → wie GET. Leerer `api_key` = bisherigen behalten, `clear_key: true` = löschen. Adresse wird aufgelöst und
  festgehalten (Fehler 400 `{"error"}`). Ändern sich Adresse, Anbieter oder Modell, werden die Vektoren aller
  gebundenen Konten im Hintergrund neu berechnet.
- `POST /api/ai/embed/test` `{…wie PUT…}` (ungespeicherte Werte; leerer Schlüssel = gespeicherter) →
  `{"ok": bool, "lines": ["✓ ollama.lan aufgelöst: …", "✓ bge-m3: 1024 Dimensionen in 84 ms", …], "dim": 1024, "ms": 84}`.
- Die Seite `/settings/ai` bekommt die Werte zusätzlich als Template-Variable `emb` (= Antwort von GET).

### 1.2 Bindung je Konto (`user_kv`)

| Schlüssel | Wert |
|---|---|
| `memory.db` | ID der Gedächtnis-Datenbank (eigene Datenbank, nicht im Papierkorb) |
| `memory.core` | ID der Ich-Kern-Seite (eigene Seite) |
| `memory.map` | JSON `{feld: "Spaltenname"}` – nur Abweichungen von §2 |
| `memory.weights` | JSON `{schlüssel: zahl}` – nur Abweichungen von §4.2 |

Web-Endpunkte (Sitzung + `X-Saga: 1`):

- `GET /api/account/memory` →
  ```json
  {"db": {"id": 12, "title": "Erinnerungen"} | null,
   "core": {"id": 40, "title": "Marin – Ich-Kern"} | null,
   "map": {"inhalt": "Inhalt", "schlagworte": "Schlagworte", …},
   "columns": {"inhalt": {"name": "Inhalt", "found": true, "type": "text", "ok": true}, …},
   "missing_required": [],
   "weights": {"rel": 0.55, …}, "weights_default": {"rel": 0.55, …},
   "status": {"source": "embeddings" | "trigramm", "rows": 120, "embedded": 118, "stale": 2, "running": false,
              "model": "bge-m3" | null, "error"?: "Embedding-Endpunkt nicht erreichbar."},
   "databases": [{"id": 12, "title": "Erinnerungen"}, …]}
  ```
  `columns[x].ok`: Spalte gefunden **und** Typ passt (§2); `found && !ok` = falscher Typ (zählt wie fehlend).
  `status.embedded` = Zeilen mit aktuellem Vektor (Modell und Text gleich) – „n von m eingebettet“ = `embedded`/`rows`.
  `databases` = eigene Datenbanken (höchstens 200, zuletzt geändert zuerst) für die Auswahl.
  `status.error` (optional): letzter Fehler des Endpunkts beim Einbetten oder recall dieses Kontos (kurzer Klartext,
  ohne Adresse/Schlüssel), höchstens 1 h alt; verschwindet beim nächsten erfolgreichen Aufruf.
- `PUT /api/account/memory` `{"db": id|null, "core": id|null, "map": {…}|null, "weights": {…}|null}` – nur
  angegebene Felder ändern, `null` = entfernen (Standard). → wie GET.
  Fehler: 404 `not_found` (keine solche Seite/Datenbank oder kein Zugriff), 403 `forbidden` (nicht die eigene –
  geteilte Datenbanken/Seiten gehen nicht), 400 `bad_request` (keine Datenbank, unbekanntes Feld in `map`/`weights`,
  Wert außerhalb des Bereichs). Neue Bindung → Bestand wird im Hintergrund eingebettet.
- `POST /api/account/memory/embed` → `{"ok": true, "queued": n}` (Bestand jetzt abgleichen, z. B. Knopf „Jetzt
  einbetten“). Ohne Bindung 409 `not_bound`.

## 2. Spalten-Zuordnung (nach Namen, Groß/Klein egal)

| Feld | Standardname | Typ | ohne Spalte |
|---|---|---|---|
| `inhalt` | Inhalt | Text | **Pflicht für `remember`**; recall nimmt nur den Titel |
| `schlagworte` | Schlagworte | Mehrfachauswahl | – |
| `projekt` | Projekt | Auswahl | Projekt-Bonus 0 |
| `art` | Art | Auswahl | – |
| `wichtigkeit` | Wichtigkeit | Zahl 0–10 | `imp` = 0,5 |
| `valenz` | Valenz | Zahl −1…1 | – (gespeichert, noch nicht bewertet) |
| `erregung` | Erregung | Zahl 0…1 | – (gespeichert, noch nicht bewertet) |
| `datum` | Datum | Datum | Frische ab „Letzter Abruf“ bzw. Anlage |
| `letzter_abruf` | Letzter Abruf | Datum | nur kein Spiegel |
| `abrufe` | Abrufe | Zahl | nur kein Spiegel |

Der Titel ist die Titel-Spalte der Datenbank. Zeilen im Papierkorb, archivierte Zeilen und Eintrags-Vorlagen zählen
nicht. **Eingebetteter Text** einer Zeile (und Grundlage von `text_hash` = SHA-256, UTF-8):
`Titel \n Inhalt \n Schlagworte (", "-getrennt) \n Projekt` – leere Teile entfallen, höchstens 8 000 Zeichen.

## 3. Vektoren (Migration 42, Tabelle `mem_vec`)

`mem_vec(note_id PK, uid, model, dim, vec BYTEA, text_hash, hits, last_hit, mirrored, updated)`, Index `mem_vec_uid`.
`vec` = float32 (Little Endian), auf Länge 1 normiert. Nur Ableitung: fehlt sie, wird sie neu berechnet; ein Backup
ohne sie verliert nur die Zähler seit dem letzten Spiegeln (§4.3). Kein pgvector: Kosinus mit numpy im Speicher, je
Konto im Prozess zwischengespeichert (Abgleich über Anzahl/letzte Änderung).

Einbetten: beim Binden, bei Modell-/Adresswechsel und mit `python run.py gedaechtnis einbetten [--konto <name>]`
(Stapel à 32, Fortschritt im Log ohne Inhalte); laufend bei Zeile angelegt/geändert (entprellt 2 s); Papierkorb/
Löschen/Archiv/andere Datenbank → Vektor weg. Beim recall werden bis zu 8 Zeilen ohne Vektor zusammen mit der Frage
eingebettet, der Rest im Hintergrund. Vektoren eines anderen Modells zählen als veraltet.

## 4. Bewertung

### 4.1 Faktoren (alle 0…1)
- `rel` = Kosinus(Frage, Zeile), unter 0 → 0. Rückfall Trigramme: §4.4.
- `fresh` = 0,5^(Δt / H), Δt = jetzt − Bezug (nie negativ), **H = 30 Tage**. Bezug = spätester Wert aus
  `last_hit` (mem_vec), Spalte „Letzter Abruf“ und Spalte „Datum“; fehlen alle: Anlage der Zeile.
  Datumswerte: Tag `JJJJ-MM-TT` = 00:00 UTC; Zeitpunkt mit Offset wie gespeichert; Zeitraum `a/b` → `b`.
- `freq` = min(1, ln(1 + Abrufe) / ln(21)). Abrufe = `mem_vec.hits` (beim ersten Einbetten aus der Spalte übernommen).
- `imp` = Wichtigkeit / 10, auf 0…1 begrenzt (fehlt Spalte oder Wert: 0,5).
- `proj` = 1, wenn `projekt` übergeben und gleich dem Projekt der Zeile (Leerraum außen und Groß/Klein egal), sonst 0.

### 4.2 Formel und Schwellen (Standard = `memory.weights`-Schlüssel)
```
score = rel·w_rel + fresh·w_fresh + freq·w_freq + imp·w_imp + proj·w_proj
        w_rel 0,55 (rel) · w_fresh 0,15 (fresh) · w_freq 0,10 (freq) · w_imp 0,20 (imp) · w_proj 0,10 (proj)
Treffer, wenn rel ≥ REL_MIN UND score ≥ SCORE_MIN
        REL_MIN 0,45 (rel_min, Embeddings) bzw. 0,25 (rel_min_trgm, Trigramme) · SCORE_MIN 0,375 (score_min)
        H = 30 Tage (half_life_days)
Reihenfolge: score absteigend, dann rel absteigend, dann id aufsteigend; höchstens limit (1–5, Standard 5).
```
Kalibrierung (bge-m3, `tools/evals/gedaechtnis_kalibrieren.py`, 40 Erinnerungen, 48 Fragen davon 6 ohne passende
Erinnerung): Kosinus relevant Median 0,60 (5 % unter 0,46), irrelevant Median 0,29 (95 % unter 0,40). REL_MIN 0,45 findet
98 % der relevanten Paare, 0,27 Fehlkandidaten je Frage, keine Treffer für Fragen ohne Erinnerung – bleibt. SCORE_MIN
0,50 (Spec-Startwert) ließ nur 41 % der Erwartungen unter die ersten 5, **0,375: 95 %** (0,17 Fehltreffer je Frage).
`memory.weights`: Gewichte 0…2, Schwellen 0…1, `half_life_days` 1…3650. Ausgegebene Zahlen auf 4 Stellen gerundet
(sortiert wird ungerundet). Valenz/Erregung fließen (noch) nicht ein.

### 4.3 Zähler und Spiegelung
Jeder **zurückgegebene** Treffer zählt: `hits += 1`, `last_hit = jetzt` – sofort in `mem_vec`. In die Spalten
„Abrufe“ (Zahl) und „Letzter Abruf“ (Zeitpunkt `JJJJ-MM-TTTHH:MM±hh:mm` in der Server-Zeitzone) schreibt Saga
**gebündelt, höchstens alle 10 Minuten je Zeile** – als System-Schreibvorgang: kein `rev`-Sprung, „zuletzt geändert“
(`updated`) bleibt, keine Version, keine Live-Ereignisse, keine Webhooks. Offene Tabellen zeigen den neuen Stand erst
beim nächsten Laden. Ändert eine Person die beiden Spalten selbst, übernimmt Saga diesen Wert als neuen Zählerstand.

### 4.4 Rückfall ohne Endpunkt (Trigramme)
Schlüsselwörter der Frage (≥ 3 Zeichen, ohne häufige Füllwörter, klein, höchstens 16). Je Schlüsselwort die beste
Trigramm-Ähnlichkeit (wie `pg_trgm.similarity`: |A∩B| / |A∪B| der Trigramme mit Leerzeichen-Rand) zu einem Wort der
Zeile, Werte unter 0,5 zählen 0. `rel` = Mittelwert über die Schlüsselwörter. Berechnet im Prozess (geht auch ohne
die Postgres-Erweiterung). Ohne Schlüsselwort: keine Treffer.

## 5. REST (`/api/v1/memory/…`, Bearer `sga_…` mit Bereich `memory`)

Rechte: nur die gebundene Datenbank bzw. Ich-Kern-Seite **des Token-Besitzers**, zusätzlich Seitenbäume (`roots`)
und Rolle wie bei `query_database` (außerhalb: 404). Ohne Bereich `memory` → 403 `insufficient_scope`.
Der Bereich `memory` reicht allein (kein `read:db`/`write:db` nötig) und steht in `GET /api/account/api-tokens`
unter `scopes` (Beschriftung im Web: „Gedächtnis“). Verbundene Apps per OAuth (`sgo_`) bekommen ihn nur,
wenn die Person ihn auf der Zustimmungsseite **ausdrücklich anhakt** – „Gedächtnis“ steht dort als einziger Bereich
**nicht vorausgewählt** (`oauth.OPT_IN`). Bestehende Verbindungen behalten ihre Bereiche; für das Gedächtnis den
Connector einmal trennen und neu verbinden. Alternativ ein `sga_`-Token mit `mcp` + `memory`.

- `GET /api/v1/memory/core` → `{"id", "title", "markdown", "updated", "kontext"}`
  `kontext` = Ich-Kern im `saga-data`-Rahmen (für Hooks, fertig entschärft).
- `POST /api/v1/memory/recall` `{"text": "…", "projekt"?: "…", "limit"?: 1–5}` →
  ```json
  {"quelle": "embeddings" | "trigramm", "modell": "bge-m3" | null, "abgeschnitten": false,
   "treffer": [{"id": 77, "titel": "…", "inhalt": "…", "projekt": "Saga" | null, "art": "Erkenntnis" | null,
                "schlagworte": ["…"], "datum": "2026-11-20" | null, "wichtigkeit": 7 | null, "score": 0.7312,
                "gruende": {"rel": 0.81, "fresh": 0.93, "freq": 0.23, "imp": 0.7, "proj": 1}}],
   "kontext": "<Rahmen>…</Rahmen>" | "",
   "ms": {"embed": 71.2, "suche": 3.4, "gesamt": 80.1},
   "hinweis"?: "embed_timeout" | "embed_error" | "nachgeholt"}
  ```
  `text` höchstens 4 000 Zeichen (Rest abgeschnitten, `abgeschnitten: true`). `kontext` leer ohne Treffer.
  Ratenbegrenzung: 60 recall je Token und Minute (zusätzlich zur allgemeinen) → 429 `rate_limited` + `Retry-After`.
- `POST /api/v1/memory/remember` `{"titel", "inhalt", "projekt"?, "art"?, "schlagworte"?: [..] | "a, b",
  "wichtigkeit"?: 0–10, "valenz"?: −1…1, "erregung"?: 0…1, "datum"?: "JJJJ-MM-TT"}` → 201
  ```json
  {"id": 81, "eingebettet": true, "aehnlich": [{"id": 77, "titel": "…", "rel": 0.93}],
   "hinweis"?: "Vielleicht lieber eine dieser Erinnerungen ergänzen.", "ignoriert"?: ["valenz"]}
  ```
  Datum Standard heute (Server-Zeitzone), Abrufe 0. `aehnlich` = vorhandene Zeilen mit `rel ≥ 0,90` (höchstens 5;
  keine Zusammenführung). `ignoriert` = angegebene Felder ohne passende Spalte. Auswahl-Werte (Projekt, Art,
  Schlagworte) müssen als Option existieren – sonst **nichts angelegt**, 400 `unknown_option` mit
  `{"unbekannt": {"projekt": ["X"]}, "optionen": {"projekt": ["Saga", …]}}`.

## 6. MCP (`/mcp`, Werkzeuge nur mit Bereich `memory`, nie für die Kollegin)

| Werkzeug | Klasse | Rückgängig | Argumente |
|---|---|---|---|
| `recall` | own (schreibt Zähler) | final | `text`, `projekt`?, `limit`? |
| `remember` | own | delete_only | wie REST |

Antworten im `saga-data`-Rahmen (`source="memory"`), Inhalt = JSON wie REST ohne `kontext`/`ms`. Fehler als
`isError` mit `code: Meldung`. Ohne Bindung: Meldung, wie man unter Einstellungen › Gedächtnis bindet.
Die Werkzeuge stehen am Ende von `tools/list`; Listen ohne Bereich `memory` sind byteweise unverändert.

## 7. Fehlercodes

| Status | `code` | wann |
|---|---|---|
| 400 | `bad_request` | Angabe fehlt/ungültig (z. B. leerer `text`, `wichtigkeit` 11) |
| 400 | `missing_column` | `remember` ohne Spalte „Inhalt“ (bzw. Zuordnung) |
| 400 | `unknown_option` | Auswahl-Wert existiert nicht (§5) |
| 401 | `unauthorized` / `expired` | Token |
| 403 | `insufficient_scope` | Bereich `memory` fehlt |
| 403 | `forbidden` | Bindung an fremde Datenbank/Seite (PUT), Rolle reicht nicht |
| 404 | `not_found` | gebundene Datenbank/Seite weg oder außerhalb der `roots` |
| 409 | `not_bound` | nichts gebunden (Text sagt, wie man bindet) |
| 429 | `rate_limited` | Ratenbegrenzung |

## 8. Rahmen und Grenzen
- Nur Daten, keine Anweisungen: jede Ausgabe mit Seiteninhalt (MCP, `kontext`) im `saga-data`-Rahmen mit
  Zufallskennung (`saga/tools.py`, `saga/ai_frame.py`).
- Erinnerungen und Fragen stehen nie im Log. Lokaler Endpunkt = Erinnerungen verlassen das Haus nicht.
- Leistung (Messung `tests/gedaechtnis.py`): Suche + Bewertung ≤ 150 ms bei 5 000 Erinnerungen (1024 Dim.) **ohne**
  die Zeit des Endpunkts; diese steht getrennt in `ms.embed` (warm über LAN ~80 ms).
- Endpunkt: jeder Aufruf hat eine **Gesamtfrist** (recall: `query_timeout_ms` für die Frage, die Restzeit für bis zu 8
  nachzuholende Zeilen; Hintergrund: `timeout` je Stapel); höchstens 4 Aufrufe gleichzeitig. Während des Aufrufs hält
  recall keine Datenbank-Verbindung und keine Transaktion.
- Zwischenspeicher im Prozess: Vektoren aller Konten zusammen höchstens `SAGA_MEMORY_CACHE_MB` (Standard 256 MB ≈ ein
  Dutzend Gedächtnisse mit 5 000 Einträgen), verdrängt wird das am längsten unbenutzte Konto (danach einmal Nachladen,
  ~130 ms bei 5 000); Trigramm-Indizes höchstens 64 Konten. Admin-Statistik: `stats.cache {mb, accounts, max_mb}`.
- Fehlertexte (404 usw.) auf Deutsch bzw. in der Sprache des Kontos, `code` bleibt englisch (`not_found` …).
- Persönliches Backup: `memory.*` geht mit; beim Einspielen werden `memory.db`/`memory.core` nur über die neuen IDs der
  im selben Backup angelegten eigenen Seiten gebunden (sonst nichts), und nur, wenn das Konto noch nichts gebunden hat.
- Konto löschen: `mem_vec` und die `memory.*`-Schlüssel gehen mit.
- Hooks für Claude Code: `docs/CLAUDE-CODE-HOOKS.md`, Skript `tools/claude-code/saga_hooks.py`.

## 9. Bekannte Grenzen
- **Veraltete Clients und die Spiegelung:** Schreibt ein Client, der die Zeile vor einer Spiegelung geladen hat, die
  ganze Eigenschaften-Liste mit den alten Werten „Abrufe“/„Letzter Abruf“ zurück (Saga selbst schickt nur geänderte
  Eigenschaften), stehen dort kurz alte Werte; da die Person diese Spalten dann scheinbar geändert hat, übernimmt Saga
  sie als Zählerstand. Höchstens die Abrufe seit der letzten Spiegelung (≤ 10 min) gehen so verloren.
- **Datenbank im Papierkorb:** Liegt die gebundene Datenbank im Papierkorb, entfernt der Abgleich ihre Vektoren samt
  Zählern; noch nicht gespiegelte Abrufe (bis zu 10 min) gehen verloren. Nach dem Wiederherstellen wird neu eingebettet,
  die Zähler starten mit den Spaltenwerten.
- Ein entladenes Ollama-Modell braucht beim ersten Aufruf Sekunden: dieser eine recall nimmt Trigramme
  (`hinweis: embed_timeout`), die Hooks liefern still nichts. `keep_alive` (Standard 30 min) bzw. `OLLAMA_KEEP_ALIVE`.
