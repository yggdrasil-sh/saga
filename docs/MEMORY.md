# Saga – Gedächtnis (recall/remember, Embeddings, Hooks) – Vertrag

Stand 2.6 (Bugs-DB 2581, Feature `memory1`; Stufe 3: Nickerchen 2601 + Schattenmodus Phase 1 2603, Feature `memory2`).
Dieser Text ist der Vertrag zwischen Server, Weboberfläche, Hooks und MCP-Clients. Formeln und Schwellen prüfen
`tests/vectors/memory_score.json` (Server-Reihe `tests/gedaechtnis.py`), `tests/vectors/memory_nap.json` (§11, Reihe
`tests/gedaechtnis_schlaf.py`) und `tests/vectors/memory_shadow.json` (§12, Reihe `tests/gedaechtnis_schatten.py`);
erzeugt von `tools/memory_{score,nap,shadow}_vectors.py` unabhängig von `saga/`.

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
| `memory.map` (2601) | zusätzlich `"schnappschuss": "<Optionsname in Art>"` (Standard „Schnappschuss“, Groß/Klein egal) – keine Spalte, sondern die Option der Spalte „Art“, die einen Schnappschuss kennzeichnet (§11) |
| `memory.shadow` (2603) | Unix-Sekunden des Einschaltens (Text); fehlt = Schattenmodus aus (ab Werk, §12) |

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
   "databases": [{"id": 12, "title": "Erinnerungen"}, …],
   "snapshot": {"option": "Schnappschuss", "found": true, "count": 12, "latest": "2026-11-28T09:12+01:00" | null},
   "shadow": {"on": false, "since": null | "2026-11-28T13:40+01:00", "links": 0, "traces": 0, "running": false}}
  ```
  `columns[x].ok`: Spalte gefunden **und** Typ passt (§2); `found && !ok` = falscher Typ (zählt wie fehlend).
  `status.embedded` = Zeilen mit aktuellem Vektor (Modell und Text gleich) – „n von m eingebettet“ = `embedded`/`rows`.
  `databases` = eigene Datenbanken (höchstens 200, zuletzt geändert zuerst) für die Auswahl.
  `status.error` (optional): letzter Fehler des Endpunkts beim Einbetten oder recall dieses Kontos (kurzer Klartext,
  ohne Adresse/Schlüssel), höchstens 1 h alt; verschwindet beim nächsten erfolgreichen Aufruf.
  `map`/`columns` enthalten auch `sensibel` (§2); `map` zusätzlich `schnappschuss` (Optionsname, nicht in `columns`).
  `snapshot` (2601): `option` = Optionsname aus `memory.map`; `found` = Spalte „Art“ ist da (Auswahl) **und** hat diese
  Option; `count`/`latest` = lebende Zeilen dieser Art (Anzahl, neueste Anlage in Server-Zeitzone) – ohne Bindung bzw.
  ohne Option `0`/`null`. `shadow` (2603): `on` = `memory.shadow` gesetzt, `since` = Einschaltzeitpunkt, `links` =
  `mem_link`-Zeilen des Kontos, `traces` = `mem_trace`-Zeilen (≤ 30 Tage), `running` = Nachziehen läuft (§12.2).
- `PUT /api/account/memory` `{"db": id|null, "core": id|null, "map": {…}|null, "weights": {…}|null}` – nur
  angegebene Felder ändern, `null` = entfernen (Standard). → wie GET.
  Fehler: 404 `not_found` (keine solche Seite/Datenbank oder kein Zugriff), 403 `forbidden` (nicht die eigene –
  geteilte Datenbanken/Seiten gehen nicht), 400 `bad_request` (keine Datenbank, unbekanntes Feld in `map`/`weights`,
  Wert außerhalb des Bereichs). Neue Bindung → Bestand wird im Hintergrund eingebettet.
  Zusätzlich (2603) `"shadow": true|false` und `"forget_traces": true`: `shadow: true` ohne gebundene Datenbank → 409
  `not_bound`; Einschalten setzt `memory.shadow` (bleibt beim erneuten Einschalten) und plant das Nachziehen (§12.2);
  `shadow: false` löscht nur `memory.shadow` – Links und Spuren bleiben (Spuren laufen nach 30 Tagen ab).
  `forget_traces: true` löscht sofort alle `mem_trace` des Kontos (Links bleiben). `map` nimmt auch `sensibel` und
  `schnappschuss` an.
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
| `sensibel` (2603) | Sensibel | Kontrollkästchen (`checkbox`) | nichts ist sensibel |

`sensibel` wirkt nach §12.6 (nie über Verknüpfungen, nur bei direkter Nennung). Es steht **nicht** im eingebetteten Text
(`text_hash` bleibt gleich). `remember` nimmt `sensibel` (bool); ohne Spalte → `ignoriert`.

Der Titel ist die Titel-Spalte der Datenbank. Zeilen im Papierkorb, archivierte Zeilen und Eintrags-Vorlagen zählen
nicht. **Eingebetteter Text** einer Zeile (und Grundlage von `text_hash` = SHA-256, UTF-8):
`Titel \n Inhalt \n Schlagworte (", "-getrennt) \n Projekt` – leere Teile entfallen, höchstens 8 000 Zeichen.

## 3. Vektoren (Migration 42, Tabelle `mem_vec`)

`mem_vec(note_id PK, uid, model, dim, vec BYTEA, text_hash, hits, last_hit, mirrored, updated)`, Index `mem_vec_uid`.
`vec` = float32 (Little Endian), auf Länge 1 normiert. Nur Ableitung: fehlt sie, wird sie neu berechnet; ein Backup
ohne sie verliert nur die Zähler seit dem letzten Spiegeln (§4.3). Kein pgvector: Kosinus mit numpy im Speicher, je
Konto im Prozess zwischengespeichert (Abgleich über Anzahl/letzte Änderung).

Einbetten: beim Binden, bei Modell-/Adresswechsel und mit `python run.py gedaechtnis einbetten [--konto <name>]`
(Verknüpfungen des Schattenmodus: `python run.py gedaechtnis verknuepfen [--konto <name>]`, §12.2)
(Stapel à 32, Fortschritt im Log ohne Inhalte); laufend bei Zeile angelegt/geändert (entprellt 2 s); Papierkorb/
Löschen/Archiv/andere Datenbank → Vektor weg. Beim recall werden bis zu 8 Zeilen ohne Vektor zusammen mit der Frage
eingebettet, der Rest im Hintergrund. Vektoren eines anderen Modells zählen als veraltet.

Stufe 3 (Migration 43): `mem_link(a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft, gepinnt)`
(a < b, Herkunft `aehnlich`|`gemeinsam`|`hand`), `mem_trace` (Spuren §12.7, 30 Tage) und `mem_link_log` (Handeingriffe,
Phase 2, bis dahin leer) – wie `mem_vec` ohne Fremdschlüssel auf `note`.

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
beim nächsten Laden. (Der recall selbst meldet die Treffer als `memory.recalled` an den Graph, §10.) Ändert eine Person die beiden Spalten selbst, übernimmt Saga diesen Wert als neuen Zählerstand.

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
  Zusätzlich (2603) `"sensibel"?: true|false` (Kontrollkästchen, §2; ohne Spalte → `ignoriert`). Im Schattenmodus legt
  remember die Verknüpfungen „ähnlich“ an (§12.2).

Stufe 3 (2601/2603):
- `POST /api/v1/memory/nap` `{"stimmung", "gedanke", "valenz"?, "erregung"?, "faeden"?: [..], "ort"?, "projekt"?}`
  (schreibend, Bereich `memory`) → 201 `{"id", "titel", "zeilen", "zeichen", "ignoriert"?}` – §11.1.
- `GET /api/v1/memory/core` → wie bisher + `"wake": {…} | null` (§11.3; `null` ohne gebundene Datenbank); `kontext`
  enthält zusätzlich den Aufwach-Teil (§11.3).
- `GET /api/v1/memory/wake` → `{"kern": {"id", "title", "markdown", "updated"} | null, "wake": {…} | null}`; braucht
  `memory.db` **oder** `memory.core` (fehlt beides → 409 `not_bound`; fehlt nur eines, ist dieser Teil `null`).
- `POST /api/v1/memory/recall_used` `{"ids": [77, 81]}` (schreibend, 1–10 ganze Zahlen) →
  `{"ok": true, "schatten": bool, "verstaerkt": n, "neu": n, "ignoriert": [ids]}` – §12.4.
- `GET /api/v1/memory/shadow?tage=14&stichproben=8` → §13 (`tage` 1–30, `stichproben` 0–20; sonst 400).
- `POST /recall`: `ms` bekommt `"schatten": 3.1` **nur** bei eingeschaltetem Schattenmodus; `treffer`, `quelle`,
  `hinweis`, `kontext` sind byteweise gleich wie ausgeschaltet (Phase 1, §12.1).
- Ratenbegrenzung: `nap` und `recall_used` zählen zum recall-Topf (60/min je Token); `shadow` 6/min je Token.

## 6. MCP (`/mcp`, Werkzeuge nur mit Bereich `memory`, nie für die Kollegin)

| Werkzeug | Klasse | Rückgängig | Argumente |
|---|---|---|---|
| `recall` | own (schreibt Zähler) | final | `text`, `projekt`?, `limit`? |
| `remember` | own | delete_only | wie REST (+ `sensibel`?, boolean) |
| `nap` (2601) | own | delete_only | `stimmung`, `gedanke`, `valenz`?, `erregung`?, `faeden`? (array string, ≤ 5), `ort`?, `projekt`? |
| `wake` (2601) | read | – | – |
| `recall_used` (2603) | own | final | `ids` (array integer, 1–10) |
| `shadow_report` (2603) | read | – | `tage`? (1–30, Std. 14), `stichproben`? (0–20, Std. 8) |

Antworten im `saga-data`-Rahmen (`source="memory"`), Inhalt = JSON wie REST ohne `kontext`/`ms`. Fehler als
`isError` mit `code: Meldung`. Ohne Bindung: Meldung, wie man unter Einstellungen › Gedächtnis bindet.
Die Werkzeuge stehen am Ende von `tools/list`; Listen ohne Bereich `memory` sind byteweise unverändert.
Stufe 3: alle sechs mit `kollegin=False`, Bereich `memory`, nicht destruktiv, Ziel `memory`; Reihenfolge am Ende von
`tools/list`: recall, remember, nap, wake, recall_used, shadow_report. Rahmen-Kennung (`source`): `memory` (recall,
remember, nap, recall_used), `memory:core` (wake), `memory:report` (shadow_report). Inhalt = JSON wie REST.
`recall` sagt in der Beschreibung zusätzlich „Danach mit recall_used melden, was geholfen hat.“
Hinweis: Clients sehen neue Werkzeuge erst nach dem nächsten `tools/list` – die Claude-App ggf. neu verbinden (der
Bereich `memory` bleibt, keine neue Zustimmung nötig).

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

Stufe 3 bringt keine neuen Codes: `nap` ohne Spalte „Art“ (Auswahl) oder „Inhalt“ → 400 `missing_column`; Option
„Schnappschuss“ fehlt → 400 `unknown_option` `{"unbekannt": {"art": ["Schnappschuss"]}, "optionen": {"art": […]}}`,
nichts angelegt; Feld zu lang/zu viele Fäden/Zahl außerhalb → 400 `bad_request`, Meldung mit Feldname; `wake` ohne jede
Bindung → 409 `not_bound`; `shadow: true` ohne Datenbank → 409 `not_bound`.

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
- Stufe 3: Fragen, Stichworte, Schnappschuss-Texte und Titel stehen nie im Log (nur Zahlen und `_uh(uid)`).
  Leistung: Schattenteil ≤ 25 ms (90 %) **zusätzlich** bei 5 000 Erinnerungen / 25 000 Kanten (`ms.schatten`); Suche +
  Bewertung bleiben ≤ 150 ms; `wake` ≤ 50 ms ohne Nachladen von Kalendern (`calendars.cached`, wartet nie).
- Konto löschen: `mem_link`, `mem_trace`, `mem_link_log` gehen mit. Persönliches Backup: `memory.shadow` geht mit
  (eingespielt nur zusammen mit einer abgebildeten Datenbank; danach wird nachgezogen, §12.2); Links und Spuren gehen
  **nicht** mit (abgeleitet bzw. Messdaten; Seiten-IDs ändern sich; Stichworte nicht in fremde Ziele). Server-Backup
  (`pg_dump`) enthält alles.

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

## 10. Graph: abgerufene Erinnerungen hervorheben (Bugs-DB 2595)

Im Graph (`static/graph.js`) fallen oft und kürzlich abgerufene Erinnerungen auf. Nur für die **eigene** gebundene
Gedächtnis-Datenbank der ansehenden Person; Zähler fremder oder freigegebener Seiten gehen nie hinaus.

### 10.1 `GET /api/graph`, Feld `mem`
Nur wenn die Person ein Gedächtnis gebunden hat (`memory.db`, eigene Datenbank, nicht im Papierkorb):
```json
"mem": {"db": 12, "now": 1764300000, "rows": [[77, 5, 1764290000], [78, 2, null]]}
```
`rows` = `[id, hits, last_hit]` je Zeile der gebundenen Datenbank, die im Graph als Knoten steht und `hits > 0` oder
`last_hit` hat (andere fehlen; Reihenfolge nach id). `hits`/`last_hit` (Unix-Sekunden, ganzzahlig, oder `null`) kommen
**live aus `mem_vec`**, nicht aus der 10-min-Spiegelung in die Spalten. `now` = Serverzeit (für das Abklingen,
unabhängig von der Uhr des Geräts). Ohne Bindung fehlt `mem` ganz; gebunden, aber noch nichts abgerufen: `rows: []`. Eine Abfrage für
alle Zeilen (kein N+1), auch bei 5 000 Knoten. Kein Feature-Schalter nötig: das Feld meldet sich selbst; ältere Clients
lassen es liegen.

### 10.2 Live-Ereignis `memory.recalled`
Nach jedem recall mit Treffern (REST §5 und MCP §6, Zähler gezählt) bekommt **nur die Besitzerin des Tokens** auf allen
ihren Verbindungen (Web-Tabs, Geräte; `/api/events`) nach dem Commit:
```json
{"ids": [77, 78]}
```
Nur die IDs der zurückgegebenen Treffer, nie Titel, Inhalte oder die Frage. Andere Konten (auch mit Freigabe der
Datenbank) bekommen nichts. Kein Ereignis ohne Treffer und für interne Abrufe ohne Zählung.

### 10.3 Darstellung (Web)
- Größe: Radius × (1 + 0,8 · `freq`), `freq` = min(1, ln(1 + hits) / ln(21)) wie §4.1 – höchstens × 1,8 (ab 20 Abrufen).
- Nachleuchten: Glühen in `--gr-mem` (hell/dunkel eigene Farbe) mit Stärke g = (1 − Δt / 24 h)², Δt = `now` − `last_hit`
  (älter als 24 h: kein Glühen). Das Glühen ist ruhig (keine Animation), es klingt beim Neuzeichnen ab.
- Puls: `memory.recalled` in einer offenen Graph-Ansicht → betroffene Knoten senden zwei Ringe aus (~1,6 s), `hits` + 1,
  `last_hit` = jetzt (ohne Neuladen). `prefers-reduced-motion`: statt Puls ein ruhiger Ring für 4 s, keine Bewegung.
- Schalter „Gedächtnis hervorheben“ (Einstellungen › Anzeige, nur sichtbar mit `mem`), Standard an, gespeichert wie die
  übrigen Graph-Einstellungen (`saga-graph` → `mem`). Legende „Kürzlich abgerufen“, Karte/Ansage „n Abrufe“.


### 10.4 Graph mit Schattenmodus (Stufe 3, 2601/2603)
`mem` bekommt zusätzlich:
```json
"core": 40 | null, "snap": [81, 90], "shadow": true | false,
"links": [[77, 81, 0.412, "a", 0]], "links_total": 312,
"hits24": [[78, 3, 1764299000]]
```
- `core` = ID der Ich-Kern-Seite, wenn sie im Graph als Knoten steht, sonst `null`.
- `snap` = Schnappschuss-Zeilen (§11) unter den Knoten, aufsteigend.
- `shadow` = Schattenmodus an. `links`, `links_total` und `hits24` gibt es **nur** bei eingeschaltetem Modus.
- `links` = `[a, b, gewicht, herkunft, gepinnt]`, a < b, **beide Enden Knoten**; `gewicht` = w_eff(now) (§12.3) auf
  3 Stellen; `herkunft` `a` (ähnlich) | `g` (gemeinsam) | `h` (Hand); `gepinnt` 0/1. Höchstens 20 000 – bei mehr die
  stärksten (w_eff absteigend, dann a, b) –, ausgegeben nach (a, b). `links_total` = alle Links mit beiden Enden als
  Knoten (vor der Kürzung).
- `hits24` = `[id, n, last]`: Zeilen, die in den letzten 24 h nur dank Verknüpfung aufgetaucht wären (`neu`, §12.5),
  Anzahl und letzter Zeitpunkt (Unix-Sekunden, ganzzahlig); nur Knoten, nach id.
- Je eine Abfrage für Zeilen, Links und Spuren der letzten 24 h (kein N+1).
- Live-Ereignis `memory.recalled` (§10.2) bekommt bei eingeschaltetem Modus `"schatten": [ids]` (nur `neu`, nur wenn
  nicht leer). Nur dann kann `ids` auch leer sein (keine Basis-Treffer, aber Schatten-Treffer); ausgeschaltet bleibt
  §10.2 unverändert.

## 11. Nickerchen und Aufwachen (Stufe 3, Bugs-DB 2601)

Ein **Schnappschuss** ist eine Zeile der gebundenen Gedächtnis-Datenbank mit der Art-Option „Schnappschuss“
(`memory.map.schnappschuss`). Saga legt die Option nie selbst an. Der Hauptweg ist das Modell selbst: es ruft `nap` bei
der Verabschiedung und nach großen Runden und liest beim Start `wake` (bzw. `memory/core`). Claude-Code-Hooks sind
Nebenweg (SessionStart zeigt den Aufwach-Teil über `kontext`; PreCompact/SessionEnd schreiben nichts, siehe
`docs/CLAUDE-CODE-HOOKS.md`). Schnappschüsse werden nie gelöscht.

### 11.1 `nap`
Felder (Leerraum zusammengefasst, Zeilenumbrüche → Leerzeichen, leer = fehlend): `stimmung` ≤ 40 Zeichen (Pflicht),
`gedanke` ≤ 240 (Pflicht), `ort` ≤ 100, `faeden` Liste von Texten (leere fallen weg), ≤ 5 je ≤ 130, `valenz` −1…1,
`erregung` 0…1, `projekt` (vorhandene Option der Spalte „Projekt“, sonst `unknown_option`). Überschreitung → 400
`bad_request` mit Feldname, **nichts gekürzt**, nichts angelegt.

Text in der **Kontosprache** (Konto > Server-Standard), feste Reihenfolge, leere Teile entfallen (≤ 9 Zeilen):
```
Stimmung: neugierig (Valenz +0,4 · Erregung 0,6)
Ort: Saga – Gedächtnis Stufe 3
Offene Fäden:
- Migration 43 prüfen
Letzter Gedanke: Morgen zuerst die Vektoren.
```
Zahlen mit 1 Nachkommastelle (Komma de/es/ru, Punkt en/th); Valenz mit Vorzeichen (`+0,4`, `-0,3`; gerundet 0 → `0,0`
ohne Vorzeichen); fehlt eine Zahl, fehlt ihr Teil in der Klammer, fehlen beide, die Klammer. Nach dem Zusammensetzen hart
≤ 1 200 Zeichen und ≤ 10 Zeilen, sonst 400 (`text`). Texte und Grenzfälle: `tests/vectors/memory_nap.json`.

Zeile: Titel `"<Optionsname> JJJJ-MM-TT HH:MM"` in der Konto-Zeitzone; `art` = Schnappschuss-Option, `inhalt` = Text,
`valenz`/`erregung` zusätzlich in ihre Spalten (ohne Spalte → `ignoriert`), `projekt` optional, `datum` heute (Server-
Zeitzone), `abrufe` 0. Kein `aehnlich`, keine Verknüpfung; eingebettet wird im Hintergrund. Antwort 201
`{"id", "titel", "zeilen", "zeichen", "ignoriert"?}`.

### 11.2 Ausschluss
Zeilen mit Art = Schnappschuss-Option sind aus allem ausgeschlossen, **bevor** die Kandidaten (`CAND_MAX`) gezählt
werden: recall (Embeddings: Maske auf den Kosinus-Werten; Trigramme: nicht im Wortindex), `aehnlich` bei remember,
Verknüpfungen, Ausbreitung und die Hub-Zählung N (§12). Ohne Spalte „Art“ oder ohne die Option ist nichts
ausgeschlossen. Schnappschüsse werden trotzdem eingebettet („n von m eingebettet“ zählt sie mit).

### 11.3 `wake`
```json
{"since": "2026-11-26T22:14+01:00" | null, "seit_s": 191400 | null,
 "text": "Letztes Nickerchen: vor 2 Tagen, 5 Stunden. Seitdem 7 Seiten geändert.",
 "schnappschuss": {"id": 90, "titel": "…", "inhalt": "…", "valenz": 0.4 | null, "erregung": 0.6 | null,
                   "datum": "2026-11-26" | null} | null,
 "anzahl": 12,
 "heute": "<sentence aus /api/ai/today>" | null,
 "geaendert": {"n": 7, "seiten": [{"id": 5, "titel": "…"}]} | null,
 "hinweis"?: "no_snapshot_option"}
```
- Neuester Schnappschuss = größtes `created` unter den lebenden Schnappschuss-Zeilen (Gleichstand: größere id),
  unabhängig von „Datum“. `since` = dessen Anlage (Konto-Zeitzone, Minuten), `seit_s` = Sekunden bis jetzt, `anzahl` =
  lebende Schnappschüsse. Ohne Option bzw. Spalte: `hinweis: "no_snapshot_option"`, `anzahl` 0.
- Zeitangabe (abgerundet, höchstens 2 Einheiten): < 60 s „gerade eben“; < 1 h „vor {m} Minuten“; < 24 h „vor {h}
  Stunden[, {m} Minuten]“ (Minuten nur > 0); < 7 Tage „vor {d} Tagen[, {h} Stunden]“ (Stunden nur > 0); ab 7 Tagen
  „vor {d} Tagen“; Einzahl/Mehrzahl je Sprache. `text` = „Letztes Nickerchen: {wann}. Seitdem {n} Seiten geändert.“
  (ohne `geaendert`: nur der erste Satz; ohne Schnappschuss „Noch kein Nickerchen.“). Vektoren `memory_nap.json`.
- `geaendert`: eigene Seiten (Konto), `updated > since`, nicht im Papierkorb, nicht archiviert, keine Vorlage, **ohne**
  die gebundene Gedächtnis-Datenbank und alle ihre Zeilen; die Ich-Kern-Seite zählt mit. `seiten` = die 5 zuletzt
  geänderten. Ohne Schnappschuss `null`. Token mit Seitenbäumen (`roots`): nur Seiten innerhalb der `roots` (`n` über
  höchstens die 2 000 zuletzt geänderten).
- `heute` = `sentence` aus `/api/ai/today` (Konto-Zeitzone und -sprache, ohne Modell); ohne Fähigkeit „ai“, bei
  einem Fehler oder für Token mit `roots` (der Satz nennt Aufgaben/Termine des ganzen Kontos) `null`. Ein Fehler in
  einem Teil macht nur diesen Teil `null`.
- `kontext` von `/core`: Rahmen `memory:core` mit `# Titel`, Markdown, dann `## Aufwachen` mit `text`, `heute` und dem
  `inhalt` des Schnappschusses. Die Hooks geben ihn unverändert weiter.

## 12. Schattenmodus Phase 1 – nur messen (Bugs-DB 2603)

### 12.1 Grundsatz
Ab Werk aus; aus = es wird nichts geschrieben. Eingeschaltet rechnet jeder **gezählte** recall (REST, MCP, Hooks) im
Hintergrund mit, wie Verknüpfungen das Ergebnis verändern würden, und legt eine Spur an. Die Antwort von recall bleibt
unverändert (außer dem Schnappschuss-Ausschluss §11.2, der immer gilt). Fehler im Schattenteil werden gefangen; im Log
steht nur `gedaechtnis: Schatten <Typ>` ohne Inhalte. Phase 2 (scharf schalten, Handeingriffe) ist nicht Teil dieses
Vertrags; die Tabelle `mem_link_log` (Migration 43) bleibt bis dahin leer.

### 12.2 Verknüpfungen (`mem_link`, a < b)
Nie zu Schnappschüssen, nie zu sensiblen Zeilen, nie zu sich selbst. Entstehen nur bei eingeschaltetem Modus:
- **ähnlich:** bei jedem neuen/geänderten Vektor (remember, Abgleich) zu den besten K = 5 Nachbarn mit Kosinus ≥ 0,55
  (Trigramme: rel ≥ 0,50). Startgewicht `g0 = 0,2 + 0,4·clamp((cos − 0,55)/0,45, 0, 1)` (Trigramme
  `0,2 + 0,4·clamp((rel − 0,50)/0,50, 0, 1)`), Untergrenze `u = 0,05`, treffer 0. Vorhandene bleiben unverändert.
- **Nachziehen:** beim Einschalten (Hintergrund, `shadow.running`) und mit `python run.py gedaechtnis verknuepfen
  [--konto <name>]` für den ganzen Bestand, Blöcke à 256, Log nur Zahlen.
- **gemeinsam:** ein Paar aus den **Basis-Treffern** eines recall (nie aus der Schatten-Menge – Rückkopplung), noch ohne
  Link, das in ≥ 3 Spuren der letzten 30 Tage gemeinsam unter `treffer` steht (die aktuelle zählt mit): `g0 = 0,15`,
  `u = 0,05`. Auftauchen allein verändert vorhandene Links nie.
- **genutzt:** §12.4.

### 12.3 Gewicht (lazy)
`w_eff(t) = u + (g − u) · 0,5^(max(0, t − zuletzt) / H_L)`, H_L = 60 Tage; gepinnt: `w_eff = g`. Abklingen schreibt
nie; immer 0 ≤ u ≤ w_eff ≤ g ≤ 1. Nichts wird vergessen: das Gewicht sinkt höchstens bis zur Untergrenze.

### 12.4 Verstärkung nur über `recall_used`
Gültige ids = Zeilen der gebundenen Datenbank, die in einer Spur des Kontos der letzten 2 h unter `treffer` stehen
(sonst `ignoriert`); jede gültige id wird in der jüngsten passenden Spur unter `genutzt` vermerkt. Je Paar gültiger ids
(beide nicht sensibel, keine Schnappschüsse): Link vorhanden → `w = w_eff(t)`, `g' = w + 0,2·(1 − w)`,
`u' = min(0,5, u + 0,05, g')`, treffer + 1, zuletzt = t (`verstaerkt`); kein Link → neu mit Herkunft gemeinsam, g = 0,30,
u = 0,10, treffer 1 (`neu`). Modus aus: 200 mit `schatten: false`, keine Wirkung. Nur das verstärkt Gewichte.

### 12.5 Ausbreitung und Gesamtwert (je gezähltem recall bei an)
- Seeds S = Kandidaten mit rel ≥ Tor (`rel_min` bzw. `rel_min_trgm`), höchstens 50, ohne sensible.
- Hub-Dämpfung `h(i) = ln(1 + N/(1 + deg_i)) / ln(1 + N)`; N = lebende Zeilen ohne Schnappschüsse (N ≤ 1 → h = 1);
  deg_i = Links zu lebenden, nicht sensiblen Nicht-Schnappschüssen.
- Übertrag `t(i→j) = w_eff(i,j) · δ · √(h(i)·h(j))`, δ = 0,5.
- `A1(j) = max_{s∈S, s≠j} rel(s)·t(s→j)`; `A2(k) = max über einfache Pfade s → j → k (s ≠ k, j ≠ k, j nicht sensibel)
  von rel(s)·t(s→j)·t(j→k)` – ein Seed bekommt kein Echo seiner eigenen Relevanz. `A = max(A1, A2)`, A < 0,02 → 0.
  Sensible Zeilen sind nie Ziel und nie Durchgang. `via` = unmittelbarer Vorgänger, `hop` 1 oder 2 (Gleichstand:
  kleinere via-ID; hop 1 vor 2).
- `r* = min(1, rel + A)`; rel für Nicht-Kandidaten exakt (Skalarprodukt bzw. Trigramm-rel, < 0 → 0).
  `gesamt = score(factors(r*, …))` nach §4.1/§4.2 mit den Gewichten des Kontos.
- Schatten-Menge aus Kandidaten und erreichten Zeilen (A > 0): r* ≥ Tor **und** gesamt ≥ score_min, **ohne Limit**,
  Reihenfolge (−gesamt, −r*, id). Obergrenze für eingespielten Text: Summe `len(titel) + len(inhalt[:2000])` ≤ 2 000,
  das erste Element immer; ab dem ersten Element, das die Summe überschreiten würde, ist der Rest `gekappt` (gezählt).
- `neu` = Elemente der Schatten-Menge außerhalb der Basis-Treffer, die ohne A kein Treffer wären (nur dank
  Verknüpfung); `neu_limit` = übrige Neue (nur wegen des Limits 5 bisher nicht dabei); `verdraengt` = Basis-Treffer,
  die nicht in der Schatten-Menge sind (in Phase 1 nur durch die Obergrenze). Vektoren: `memory_shadow.json`.

### 12.6 Sensibel
Nie automatische Links, keine Ausbreitung über oder zu diesen Zeilen; im normalen recall unverändert (nur direkte
Relevanz). Wird eine Zeile später sensibel, bleiben ihre Links, werden aber ignoriert.

### 12.7 Spur `mem_trace`
Je gezähltem recall bei an: `at`, `quelle`, `frage_hash`, `stichworte`, `lim`, `treffer` (Basis-IDs), `neu`, `genutzt`,
`gekappt`, `zeichen`, `daten = {"w": {"tor", "score_min"}, "projekt": bool, "basis": [[id, rel, score]],
"schatten": [[id, rel, akt, gesamt, via, hop]] (≤ 30), "neu_limit": [ids], "verdraengt": [ids], "ms": 3.1}`.
`frage_hash` = HMAC-SHA256 (Schlüssel aus dem Server-Geheimnis) über `uid + "\n" + Frage` (NFKC, casefold, Leerraum
zusammengefasst), 32 Hex-Zeichen. `stichworte = ", ".join(keywords(frage)[:8])[:160]` (§4.4). **Nie der Fragetext.**
Aufräumen: stündlich Spuren älter als 30 Tage; täglich Links, deren a oder b nicht mehr in `note` existiert (Zeilen im
Papierkorb bleiben verknüpft, werden aber ignoriert).

## 13. Auswertung (`shadow_report`, `GET /api/v1/memory/shadow`)
```json
{"schatten": true, "seit": "2026-11-28T13:40+01:00", "tage": 14,
 "abrufe": {"n": 212, "mit_treffern": 180, "genutzt_gemeldet": 95, "quote_genutzt": 0.53},
 "schatten_treffer": {"abrufe_mit_neu": 37, "neu_je_abruf": 0.4, "neu_limit_je_abruf": 0.9, "verdraengt": 2,
                      "gekappt": 3, "zeichen_median": 1420, "zeichen_p90": 1980},
 "kanten": {"n": 312, "herkunft": {"aehnlich": 280, "gemeinsam": 32, "hand": 0}, "gepinnt": 0,
            "gewicht": {"faecher": [[0.0, 0.1, 40], …], "median": 0.31, "p90": 0.55}, "verstaerkt": 22},
 "hubs": [{"id": 77, "titel": "…", "grad": 41, "daempfung": 0.42}],
 "oft_feuernd": [{"a": {"id", "titel"}, "b": {"id", "titel"}, "anteil": 0.18, "gewicht": 0.62}],
 "ausreisser": [{"art": "dauerbrenner", "id", "titel", "anteil"} | {"art": "starke_kante", "a", "b", "gewicht"}
                | {"art": "gekappt", "am", "gekappt", "zeichen"}],
 "sensibel": 3,
 "stichproben": [{"am": "…", "stichworte": "…", "treffer": [{"id", "titel", "rel", "score"}],
                  "waere": [{"id", "titel", "vorschau", "rel", "akt", "gesamt", "ueber": {"id", "titel"} | null}]}]}
```
- Zeitraum: Spuren der letzten `tage` Tage. `abrufe`: n = Spuren, `mit_treffern` = Spuren mit Basis-Treffern,
  `genutzt_gemeldet` = Spuren mit `genutzt`, `quote_genutzt` = genutzt_gemeldet / mit_treffern (0 ohne).
- `schatten_treffer`: `abrufe_mit_neu` = Spuren mit `neu`; `neu_je_abruf`/`neu_limit_je_abruf` = Summe / n;
  `verdraengt` = Summe; `gekappt` = Spuren, in denen die Obergrenze griff; Median/90 %-Wert von `zeichen` über Spuren mit
  Schatten-Menge.
- `kanten`: Links des Kontos (ohne die zu gelöschten Zeilen), `gewicht` über w_eff(jetzt): 10 Fächer à 0,1 `[von, bis,
  Anzahl]`, Median, 90 %-Wert; `verstaerkt` = Links mit treffer > 0.
- `hubs` = die 5 Zeilen mit dem höchsten Grad (> 0) samt Dämpfung h; `oft_feuernd` = die 5 Kanten (via, id), über die
  `neu`-Einträge am häufigsten kamen, `anteil` = Anteil der Spuren; `ausreisser` (≤ 5, in dieser Reihenfolge):
  Zeilen in ≥ 25 % der Schatten-Mengen (erst ab 4 Spuren mit Schatten-Menge), Kanten mit w_eff ≥ 0,9 ohne Pin, Spuren
  mit Kappung. `sensibel` = Anzahl lebender sensibler Zeilen.
- `stichproben`: aus den m Spuren mit `neu` gleichmäßig verteilt (Index `round(i·(m−1)/(n−1))`, n = min(stichproben, m);
  n = 1 → die erste), älteste zuerst, deterministisch. Titel und Vorschau (120 Zeichen) kommen live aus der Datenbank,
  gelöschte Zeilen heißen „(gelöscht)“. Die ganze Ausgabe ≤ 15 000 Zeichen (sonst fallen hintere Stichproben weg).
- Modus aus und keine Spuren: alle Zahlen 0, Listen leer, `schatten: false` – kein Fehler.
