# KI in den Apps – Vertrag für die App-Teams (2.1)

Kurzbeschreibung der Schnittstellen rund um die KI: Persona (Name, Profilbild) und – im zweiten Abschnitt – die KI im
Chat. Alle Endpunkte wie gewohnt mit Sitzung oder Geräte-Schlüssel (`Authorization: Bearer hkd_…`) und `X-Saga: 1`.
Alles ist rückwärtskompatibel: unbekannte Felder und Ereignisse ignorieren. `MIN_APP` ändert sich dadurch nicht.

## 1. Persona

### `GET /api/ai/persona`

```json
{"name": "Marin", "custom_name": true, "avatar": "/api/ai/avatar?v=3", "avatar_etag": "1f0c…", "enabled": true,
 "chat": true, "web": false}
```

| Feld | Bedeutung |
|---|---|
| `name` | Anzeigename der KI. Ohne eigenen Namen der Standard **in der Sprache der Anfrage** (`Accept-Language` bzw. Konto-Sprache): „Saga KI“ / „Saga AI“ / „Saga IA“. |
| `custom_name` | `false` = Standardname – die App darf dann ihren eigenen, lokalisierten Standard zeigen. |
| `avatar` | Relativer Pfad zum Profilbild oder `null` = kein Bild: **neutrales Symbol** zeigen (Web: ✦ auf Akzentfarbe). Der Wert ändert sich (`?v=`), sobald ein neues Bild hochgeladen wird. |
| `avatar_etag` | Kennung des Bildinhalts – zum Vergleich mit einem Zwischenspeicher. |
| `enabled` | Ist der Assistent für **dieses Konto** eingeschaltet (Admin-Schalter + Gruppen)? `false` = keine KI-Funktionen anbieten. |
| `chat` | (2.1) Serverweit „KI in Chats erlauben“ an **und** KI eingerichtet – unabhängig vom Konto. Damit erkennen auch Apps, für deren Konto der Assistent aus ist (kein `/api/ai/status`), ob „KI im Chat“ serverweit aus ist (z. B. Hinweis in Chats mit `ai == true` ausblenden). Fehlt das Feld (älterer Server): wie `false` behandeln bzw. `/api/ai/status` fragen. |
| `web` | (2.1) Websuche der KI serverweit aktiv (Admin-Schalter, SearXNG eingerichtet, KI eingerichtet) – für die Hinweistexte (Abschnitt 3). |

Dasselbe Objekt (ohne `enabled`, `chat`, `web` – die stehen dort auf oberster Ebene) steckt auch in
`GET /api/ai/status` unter `persona`, solange der Assistent für das Konto an ist. Das Live-Ereignis `ai.persona.changed`
enthält `chat`/`web` nicht – nach dem Ereignis `/api/ai/persona` neu abfragen.

### `GET /api/ai/avatar?v=…&s=64|128|256`

Wie `/api/avatar/<schlüssel>`: nur angemeldet (Sitzung oder `hkd_`-Schlüssel), sonst 404. Größe `s` (nächstgrößere Stufe),
WebP mit `Accept: image/webp` (oder `&fmt=webp`), sonst PNG/JPEG. `ETag` + `If-None-Match` → 304. Mit passendem `v`
ist das Bild einen Tag cachebar. 404 = kein Bild (Symbol zeigen).

### Live-Ereignis `ai.persona.changed`

Über `/api/events` an die verbundenen Konten, die die KI nutzen dürfen (Gruppen des Assistenten) bzw. bis zur Änderung
nutzen durften, wenn der Admin Name/Bild ändert, die KI bzw. „KI in Chats“ ein- oder ausschaltet oder die Gruppen
ändert:

```
event: ai.persona.changed
data: {"name": "Marin", "custom_name": true, "avatar": "/api/ai/avatar?v=4", "avatar_etag": "…"}
```

Ohne `enabled` (das ist je Person verschieden): Apps fragen danach `GET /api/ai/persona` (oder `/api/ai/status`) neu ab.
Nach einer Wiederverbindung ohne Nachholen (`reset`) ebenfalls neu abfragen.

### Beschriftung

„Frage {name}“ / „Ask {name}“ / „Pregunta a {name}“ für den Einstieg, KI-Antworten mit kleinem Profilbild und Namen.

### Tonfall

Der Admin kann eine kurze Persönlichkeit/Tonfall-Beschreibung hinterlegen. Sie geht nur serverseitig in den
System-Prompt; Apps sehen sie nicht und müssen nichts tun. Antwortformate (z. B. Aufgaben-Vorschläge `done.tasks`)
bleiben unverändert.

## 2. KI im Chat – nur auf Zuruf

Grundsatz: Chats bleiben Ende-zu-Ende-verschlüsselt. Die KI liest **nur** Nachrichten, in denen sie mit `@Name`
erwähnt wird, plus einen kleinen Verlauf, den **die App des Fragenden** mitschickt. Der Server speichert weder Frage
noch Antwort, nur den SHA-256 der Antwort als Beleg.

### Freischalten

- Server: `GET /api/ai/status` → `"chat": true`, wenn der Admin „KI in Chats erlauben“ eingeschaltet hat.
- Chat: `GET /api/chats/{cid}` (und `/api/chats`) → `"ai": true`, wenn die KI in diesem Chat erlaubt ist.
- Umschalten: `PUT /api/chats/{cid}/ai {"allowed": true|false}` → `{"ai": …}`. Erlaubt: freie Gruppe – Chat-Admins
  (`role == "admin"`), Direkt-Chat – beide, Gruppen-Chat – Saga-Admins, Seiten-Chat – Eigentümer der Seite; sonst 403.
  Einschalten bei ausgeschaltetem Server-Schalter: 403. Alle Mitglieder bekommen `chat.changed`.
- Die App zeigt in Chats mit `ai == true` (und `status.chat == true`) **sichtbar einen Hinweis**, z. B. „Marin ist in
  diesem Chat erlaubt – nur auf Zuruf: Nachrichten mit @Marin und bis zu 12 Nachrichten davor gehen im Klartext an
  die KI (host). Alles andere bleibt Ende-zu-Ende-verschlüsselt.“ (`host` aus `/api/ai/status`). Beim Schreiben einer
  Nachricht mit `@Name` ebenfalls kurz anzeigen, dass sie an die KI geht. Bei `@` die KI als ersten Vorschlag anbieten.

### Ablauf

1. Die Person schreibt z. B. `@Marin wann fährt der Zug?` – die App sendet diese Nachricht **ganz normal**
   verschlüsselt (`POST /api/chats/{cid}/messages`) und merkt sich ihre `id`.
2. Erwähnung erkannt (`@` + Name, Groß-/Kleinschreibung egal, danach kein Buchstabe/keine Ziffer; davor Anfang,
   Leerraum oder `( [ " '`) → `POST /api/chats/{cid}/ai`, `Accept: text/event-stream`:

   ```json
   {"q": "@Marin wann fährt der Zug?", "msg_id": "<id der Nachricht, 32 Hex>",
    "history": [{"name": "Ben", "text": "…"}, {"name": "Clara", "text": "…"}]}
   ```

   `history`: höchstens **12** Nachrichten **vor** der Frage, älteste zuerst, je höchstens 600 Zeichen (Server kürzt,
   ältere fallen weg, wenn zusammen über 4000 Zeichen); mehr als 12 → 400. Nur Text, keine Anhänge. Eigene frühere
   KI-Antworten mit dem Namen der KI. `q` höchstens 1000 Zeichen und muss `@Name` enthalten (sonst 400).
3. Antwort als Stream wie `/api/ai/ask`: `delta {t}` (mitlaufend anzeigen, z. B. „Marin denkt nach …“), `status`,
   `reset`, dann `done {text, reply: {id, sha256}, persona: {name}, quota_left}` oder `error {error}`.
   **`done.text` ist maßgeblich** (ohne Leerraum am Anfang/Ende); `sha256` = SHA-256 (hex) über dessen UTF-8-Bytes.
4. Die App sendet die Antwort als normale verschlüsselte Nachricht an alle Mitglieder:
   Inhalt `{"v": 1, "type": "text", "text": done.text, "ai": {"reply": done.reply.id, "name": "Marin"}}`,
   `reply_to` = id der Frage, und im Sende-Body zusätzlich `"ai_reply": done.reply.id` (nicht signiert, nur Verweis).
   Der Server prüft: Beleg gehört zu diesem Chat und diesem Konto, ist höchstens 1 Stunde alt und noch unbenutzt –
   sonst 409. Ein Beleg kann genau einmal verwendet werden.
5. Die App des Fragenden muss dafür online bleiben, bis `done` da ist; bricht sie ab, gibt es keine Antwort im Chat.

### Anzeige bei allen Mitgliedern

`GET /api/chats/{cid}/messages` liefert für Nachrichten mit Beleg zusätzlich

```json
"ai": {"ref": "<beleg-id>", "sha256": "<hex>", "by": "<benutzername des Fragenden>", "q_msg": "<id der Frage>"}
```

Als **KI-Antwort** (Profilbild + Name der KI, z. B. „gefragt von Ben“) nur anzeigen, wenn nach dem Entschlüsseln
`body.ai.reply == ai.ref` **und** `sha256(trim(body.text)) == ai.sha256`. Hat der Inhalt `body.ai`, aber es fehlt
`ai` oder der Hash passt nicht: als normale Nachricht des Absenders mit Warnung „Als KI-Antwort ausgegeben, aber nicht
bestätigt“ zeigen.

`ai.by` kann `null` sein (Konto des Fragenden gelöscht, Nachricht ohne Absender `sender == null`): dann trotzdem als
KI-Antwort zeigen, „gefragt von“ mit dem neutralen Text für gelöschte Konten („Gelöschtes Konto“). Löscht der
Absender die Nachricht oder läuft sie ab, verschwindet der Beleg mit ihr.

### Fehler

| Status | Bedeutung |
|---|---|
| 400 | ohne `@Name`, `history`/`msg_id` in falscher Form, zu lang |
| 403 | Assistent für die Person aus (Gruppen), Server-Schalter aus, KI im Chat nicht erlaubt |
| 404 | kein Mitglied des Chats |
| 409 | eine Anfrage läuft schon (eine je Person) |
| 429 | Kontingent aufgebraucht, mehr als 6 Fragen je Minute (Person) oder 20 je 10 Minuten (Chat) |
| 503 | Assistent ausgelastet (`Retry-After`) |

### Was die KI sieht

Nur Frage, Namen und mitgeschickten Verlauf (als Daten markiert), dazu Persona und Tonfall. **Keine** Seiten, Aufgaben,
Kalender oder Dateien – sonst könnte jemand über die KI eigene Inhalte in einen Chat mit anderen tragen. Mit
eingeschalteter Websuche (Abschnitt 3) zusätzlich Webtreffer zur Frage.

## 3. Websuche (2.1)

Der Admin kann der KI erlauben, bei Fragen nach Aktuellem (Wetter, Nachrichten, Öffnungszeiten …) im Web zu suchen –
über die SearXNG-Instanz des Servers. Standard: aus. Die Apps brauchen dafür **nichts Neues**; alles läuft über die
bekannten Streams. Für Hinweise und den Schalter je Frage:

- `GET /api/ai/status` → `"web": true`, wenn die Websuche für die KI wirksam ist (Admin-Schalter an **und** SearXNG
  eingerichtet). Fehlt das Feld (ältere Server): `false`.
- **Hinweise ergänzen**, wenn `web == true`: Chat-Hinweis z. B. „… gehen im Klartext an die KI (host) und Suchbegriffe
  über die Websuche an Suchmaschinen.“, beim Schreiben „… im Klartext an host – und Suchbegriffe über die Websuche.“,
  im Assistenten „Bei Fragen nach Aktuellem (Wetter, Nachrichten …) sucht Marin im Web, wenn „Im Web suchen“ an ist:
  Suchbegriffe gehen über die Websuche an Suchmaschinen.“ (Texte de/en/es wie im Web, Schlüssel in `saga/i18n`).
- **Assistent** (`POST /api/ai/ask`): neues Feld `"web": true|false` – Schalter „Im Web suchen“ je Frage neben
  Aufgaben/Kalender, Standard an; nur zeigen, wenn `status.web`. Fehlt das Feld, gilt `true`. Für Zusammenfassen,
  Verbessern, Übersetzen und Aufgaben vorschlagen (`/api/ai/page/{nid}`) sucht Saga nie im Web.
- **Chat** (`POST /api/chats/{cid}/ai`): kein eigener Schalter – mit `status.web` darf die KI suchen.

Ablauf auf dem Server: Das Modell entscheidet in einem kurzen Vorab-Aufruf, ob eine Suche nötig ist (höchstens zwei
knappe Suchbegriffe), dann fragt Saga SearXNG (höchstens 6 Treffer) und liest den Text der besten Treffer (nur
öffentliche Adressen). Die Treffer gehen als Daten markiert an das Modell.

**Ereignisse im Stream** (unbekannte Ereignisse/Felder ignorieren – alles rückwärtskompatibel):

| Ereignis | Bedeutung |
|---|---|
| `status {"web": "searching", "text": "Marin sucht im Web …"}` | Suche läuft – `text` (Sprache der Person, mit Persona-Name) statt „denkt nach …“ zeigen |
| `status {"web": "done", "results": n}` | Suche fertig – wieder „Marin denkt nach …“ |
| `status {"web": "failed"}` | Suche fehlgeschlagen – die Antwort kommt trotzdem (ohne Webdaten) |
| `sources {sources: […]}` (Assistent, ein **zweites** Mal) | Liste ersetzen: Seitenquellen + Webquellen `{"n", "id": null, "title", "url", "host", "web": true}` |
| `done {…, "web": n, "notice": "…"}` | `web`: Zahl der Webquellen; `notice`: kurzer Hinweis (z. B. „Die Websuche ist gerade nicht erreichbar – Antwort ohne Webdaten.“) unter der Antwort zeigen |
| `done {…, "text": "…"}` (Assistent, 2.6) | nur wenn Saga die Antwort bereinigt hat (wiederholte Datenmarker, K-T 2265): angezeigten Text durch `text` ersetzen – so steht er auch im Verlauf |

- **Quellen im Assistenten:** `[n]` in der Antwort kann jetzt auch auf eine Webquelle zeigen – dann `url` im Browser
  öffnen (nur `http`/`https`), daneben `host` anzeigen. Der Verlauf (`GET /api/ai/history`) liefert Webquellen genauso.
  Ältere Apps zeigen Webquellen wie „Seite ohne Zugriff“ (Titel ohne Link) – nichts bricht.
- **Quellen im Chat:** Saga hängt die genutzten Webquellen an `done.text` an („Quellen:“ und je Zeile
  `[n] Titel – https://…`) – sie sind Teil des Textes, den die App verschlüsselt sendet, und vom Beleg (`sha256`)
  abgedeckt. Links im Text wie in normalen Nachrichten klickbar machen. Gibt es einen Hinweis (Suche fehlgeschlagen),
  steht er in Klammern am Ende.
- **Grenzen:** Jede Websuche zählt als **weitere Anfrage** im Tageskontingent (`quota_left` in `done` ist schon
  verrechnet); zusätzlich höchstens 10 Websuchen je Person in 10 Minuten (darüber: Antwort ohne Websuche mit `notice`).
  Fehlgeschlagene Suchen zählen nicht.

## 4. KI-Darstellung (2.6, Bugs-DB 2248)

Modelle schreiben gern Markdown, obwohl Saga Klartext verlangt. Damit KI-Antworten überall gleich aussehen, gilt eine
**feste Teilmenge** – alles andere bleibt Text (nie als Markdown, HTML oder Link auswerten). Referenz:
`saga/ai_md.py`, Testvektoren `docs/ki-markdown-vektoren.json` (erzeugt mit `tools/ki_md_vectors.py`; in den App-Tests
nachspielen).

**Assistent** (`/api/ai/ask`, `/api/ai/page/{nid}`, Verlauf) – die App zeichnet die Antwort selbst, je Zeile:

| Zeile | Darstellung (`lines[].k`) |
|---|---|
| leer | fällt weg (Absatzgrenze) |
| `- `, `* `, `• ` + Text (auch eingerückt) | Listenpunkt (`ul`) |
| `1. ` bzw. `1) ` + Text (1–2 Ziffern 0–9) | nummerierter Punkt (`ol`) – angezeigt wird die Nummer aus dem Text (`n`), nicht die Reihenfolge |
| `#`…`######` + Leerzeichen + Text | Überschrift als fette Zeile (`h`) |
| sonst | Absatz (`p`) |

In jeder Zeile (`s`): `**fett**` und `__fett__` → fett (`b`), `` `code` `` → Festbreite (`c`), `[n]` bzw. `[1, 2]` →
Quellenverweis (`n`, nur wenn es Quelle n gibt; sonst Text `[n]`; nur Ziffern 0–9). Sonst Text (`t`) – auch `*kursiv*`,
Links, Tabellen. **Adressen im Text nicht verlinken** – Links kommen nur aus den Quellen (`sources[].url`).

**Chat** (`POST /api/chats/{cid}/ai`): Saga schreibt die Antwort **vor** dem Beleg in die Chat-Formatierung chatfmt1 um
(`**fett**` → `*fett*`, `*kursiv*` → `_kursiv_`, `~~durch~~` → `~durch~`, Überschrift → `*fett*`, `* `/`+ `/`• ` → `- `,
Codeblöcke bleiben; `x**2` und `foo__bar__baz` bleiben, wie sie sind). Apps zeigen `done.text` wie jede Chat-Nachricht –
nichts Neues nötig; alle Clients mit Chat-Formatierung (ab 2.5) zeigen es richtig, ältere die Zeichen `*fett*`. Links, Bilder und HTML aus dem Modell entfernt der Server dort vorher (`[Link entfernt]`, Bugs-DB 2260); die
angehängten Webquellen (`Quellen:` …) kommen von der Suchmaschine und bleiben.

**Datenrahmen** (Bugs-DB 2260, nur Server): Das Material steht zwischen Markern mit einer Zufallskennung je Anfrage
(`<<<SAGA_DATA <kennung>` … `<kennung> SAGA_DATA>>>`), jede Schreibweise des Markers im Inhalt wird entschärft. Apps
sehen davon nichts; wer Prompts nachbaut (Tests), liest die Kennung aus dem System-Prompt.

## 5. Fähigkeiten (2.6, Bugs-DB 2261)

`GET /api/account` liefert `"caps": [...]` – was die KI für diese Person darf (der Server prüft jede Fähigkeit selbst;
Apps blenden nur Einstiege danach ein). Unbekannte Einträge ignorieren, fehlt das Feld (ältere Server): wie bisher.

| Fähigkeit | Bedeutung |
|---|---|
| `ai` | Assistent nutzen (wie `GET /api/ai/status` → `enabled`) |
| `ai_act` | Aufträge nach außen (später: Nachrichten in deinem Namen) und Websuche. Fehlt sie, ist die Person eingeschränkt (z. B. Kinder): Saga schlägt nur vor, `status.web` ist `false` |
| `ai_ideas` | Ideen von sich aus darf die Person einschalten |

`GET /api/ai/status` nennt dieselbe Liste als `caps`; `persona.web` bleibt serverweit – ob für dieses Konto gesucht wird,
sagen `status.web` bzw. `ai_act` in `caps`. Nach `ai.persona.changed` auch `caps` neu holen (Gruppen können sich geändert
haben). Reserviert für 3.0 (Telefonie), heute nie gesetzt: `tel`, `tel_out`,
`tel_in`, `tel_intl`, `tel_mailbox`. Die Gruppen je Fähigkeit stellt der Admin unter „KI-Assistent“ ein (leer = alle).

## 6. KI-Antworten melden (2.6, Bugs-DB 2258 Teil a; Apple 1.2)

Ein gemeinsames Melde-Bauteil für Chat-Nachrichten und KI-Antworten: dieselben Gründe, dieselben Grenzen (5 je Stunde,
20 je Tag – Chat und KI zusammen), dieselbe Liste im Admin-Bereich („Meldungen“) und dieselbe Benachrichtigung der Admins.

- **Assistent und Seiten-Hilfen:** `POST /api/ai/report` `{where: "ask"|"page", reason: "spam"|"abuse"|"illegal"|"other",
  text?: "…", page?: <id bei where=page>}` → `{ok, id}`. `text` ist die Antwort der KI, die die Person selbst mitschickt
  (vorbelegt, abwählbar, änderbar; höchstens 2000 Zeichen). Fehler: 400 (Angaben), 403 (Assistent aus), 429 (zu viele).
- **KI im Chat:** wie jede Nachricht über `POST /api/chat/report` (Abschnitt Melden in APP-API-CHAT). Neu: die **eigene**
  KI-Antwort (Beleg passt) darf man ebenfalls melden – Menüpunkt „KI-Antwort melden …“; andere eigene Nachrichten weiter
  nicht (400). Im Admin-Bereich steht dann „KI-Antwort“.
- Dialog wie im Web: Grund, Schalter „Antwort der KI mitschicken“ (an), Textfeld mit der Antwort, Hinweis, dass die
  Meldung an die Admins dieses Servers geht. Unter jeder fertigen Antwort ein unauffälliger Knopf „Melden …“.
- Der Dialog bleibt beim Senden offen: bei einem Fehler (429, Netz) sieht man die Meldung und der Text bleibt; erst
  nach `ok` schließt er. Je Antwort eine Meldung (Knopf danach „Gemeldet“).
- **Grenze der Kennzeichnung:** Chats sind Ende-zu-Ende-verschlüsselt, der Server kann nicht prüfen, ob der Inhalt einer
  eigenen „KI-Antwort“ zum Beleg passt – die Kennzeichnung ist eine Angabe des Clients (gedeckelt durch die Meldegrenzen).
  KI-Antworten von Personen auf anderen Servern tragen kein KI-Merkmal (der Beleg liegt nur auf deren Server).

## 7. Kontext-Ausweis und Quellen-Schalter (2.6, Bugs-DB 2266 K-A4)

Was der Assistent liest, wählt der Server an einer Stelle (`saga/ai_ctx.py`) und nennt es in `done`:

- **`done.read`** (Assistent, Seiten-Hilfen, Zuordnen, KI im Chat): Liste von Kennungen, **nie Text** – `note:<id>`
  (Seite, auch Datenbank), `tasks:open`, `cal:<JJJJ-MM-TT>/14` (Termine ab Datum, Tage), `chat:<chat-id>:<anzahl>`
  (so viele Verlaufs-Nachrichten). Ältere Server senden das Feld nicht; Apps ignorieren unbekannte Kennungen. Bei
  eingeschaltetem Verlauf steht dieselbe Liste auch im gespeicherten Eintrag.
- **Schalter je Person:** `GET /api/ai/status` → `"sources": {"pages": true, "tasks": true, "calendar": true,
  "chat": true}` (Standard: alles an). Ändern mit `PUT /api/ai/settings {"sources": {"tasks": false}}` → `{"sources":
  {…}}` (nur genannte Quellen ändern sich; falsche Form 400). Ohne `history` im Body bleibt der Verlauf unberührt; mit
  beidem gilt beides. Eine Quelle wirkt nur, wenn Anfrage (`tasks`/`calendar` je Frage) **und** Person sie einschalten;
  `chat: false` = beim Zuruf geht kein Verlauf an das Modell. Die Seiten-Hilfen lesen weiter genau die gewählte Seite.
- Föderierte Seiten (negative IDs) und Gäste anderer Server bekommen nie Kontext; Chat-Verlauf nur beim Zuruf.

## 8. Einheiten (2.6, Bugs-DB 2266 K-A5)

- **`GET /api/ai/status` → `"units": {"left": 11650, "of": 12000}`** – Messwert in Einheiten (etwa Zeichen/4) für heute,
  alle Pools zusammen (Fragen, Aufträge, Ideen); beide `null` bei unbegrenztem Kontingent. Das Feld steht am Ende, alle
  älteren Felder bleiben in Form und Reihenfolge gleich. Ältere Server senden es nicht.
- **Stellgröße bleibt das Kontingent in Anfragen:** Ob eine Frage geht, entscheiden weiter `quota`/`used`/`quota_left`
  (auch in `done` und `error`). Einheiten werden für Fragen nur gemessen – eine App zeigt sie höchstens klein an und
  sperrt nie danach. Grenzen in Einheiten gelten nur für Aufträge und Ideen der Kollegin (ab K-B).

## 9. Bausteine, Persona-Form und Anrede (2.6, Bugs-DB 2268 K-H)

Was die Kollegin in Karten, Mitteilungen (`detail`) und beim Vorlesen sagt, entsteht auf dem Server aus **Bausteinen**:
festen Satzrahmen je Gesprächszug in de/en/es/ru/th (`saga/say/<lang>.json`), deren Lücken der Server füllt (Plan,
Protokoll, Kontakte). Das Modell füllt höchstens einzelne Lücken (`idea.observation`, `ask.question` nur bei großen
Modellen, `draft.text`, `report.followup`) als JSON nach Schema; jede Modell-Lücke läuft durch den Stilprüfer (Regeln
P1–P13) und fällt bei einem Verstoß auf den neutralen Rahmen **ohne** Modelltext zurück. Zustandswörter, Knöpfe,
Kennzeichnungen und Fehlerarten bleiben Systemtext (i18n) – Apps bauen keine Persona-Sätze selbst.

- **Server-Schnittstelle** (für Planer/Aufträge): `saga.ai_say.say(zug, fields, lang, persona=None) -> str`. Züge:
  `intro greet idea ask plan draft start wait seen report fail oops cant undo quiet read ident hedge`; Kanal über
  `fields.channel` = `card` (Standard) | `push` (≤ 100 Grapheme, ohne Folgeidee; nur `ask start wait seen report fail oops undo ident`, alle anderen → `""`, Ideen nie per Mitteilung) |
  `voice` (ohne `[n]`, höchstens 3 Sätze, dann „Soll ich weiterlesen?“). Felder je Zug: Kopf von `saga/ai_say.py`
  (`ZUEGE`). Berichte: `report_from_log(c, job_id, lang)` – nur aus `job_change` (nicht zurückgenommen), Ereignis
  `job_event` `verdict {value: yes|no|unclear}`, Fristablauf (`job_step.result.timeout`) und `job.reason`; Namen und
  Gegenstand für den Ergebnissatz aus `job.plan.say {recipe, person, item, deadline}`. Titel nur, wenn die Person sie
  jetzt sehen darf, sonst „Eine Seite geändert“.
- **Persona (Admin › KI › Persona):** Name (vorhandenes Feld, leer = „Saga KI“/„Saga AI“ …), Bild, Tonfall wie bisher,
  neu **Sprachform** `sie` (Standard, D3) | `er` | `neutral` (wirkt auf RU-Vergangenheit, TH-Partikel ค่ะ/คะ bzw. ครับ,
  ES-Adjektive), **Lebhaftigkeit** `sachlich` | `warm` (Standard) | `verspielt`, **Anrede (Standard)** `du` | `sie`.
  Gespeichert in `ai_persona {name, tone, form, lively, salutation}`; `GET /api/ai/persona` bleibt unverändert.
- **`GET /api/ai/status` → `"style": {"label": "Marin · KI", "form": "sie", "lively": "warm", "salutation": "du"}`**
  – wirksam für diese Person, `label` in der Sprache der Anfrage (Namensregel M1: die Persona spricht, „Saga“ ist
  Ort/App/Kennzeichen; ohne eigenen Namen nur „Saga KI“, keine doppelte Marke). Steht nach `units`; ältere Felder
  unverändert, ältere Server senden es nicht.
- **`GET /api/ai/prefs`** (Sitzung oder Geräteschlüssel, `X-Saga: 1`) → `{"salutation": null|"du"|"sie", "lively":
  null|…, "effective": {salutation, lively}, "default": {salutation, lively}, "choices": {"salutation": ["du","sie"],
  "lively": [… bis zur Stufe des Admins]}}`; KI für die Person aus → `{"enabled": false}`.
  **`PUT /api/ai/prefs`** `{"salutation"?: "du"|"sie"|null, "lively"?: …|null}` – nur genannte Felder, `null` = Standard
  des Servers; lebhafter als der Admin oder unbekannter Wert → 400; KI aus → 403. Unbekannte Felder werden ignoriert:
  **andere Pakete ergänzen hier später ihre Felder** (Ideen-Stufe, „nie Inhalt in Mitteilungen“, Diktat „nur Gerät“);
  gespeichert wird je Person ein JSON in `user_kv` `ai_prefs`, fremde Schlüssel darin bleiben beim Speichern erhalten.
- **Anrede in Entwürfen** (Nachricht in deinem Namen) kommt **nie** aus dieser Einstellung und nie aus dem Chatverlauf
  (M10, RT-41), sondern aus dem Kontaktfeld (`contact.form` informal/formal, Grußform `contact.salutation`) oder als
  `du`/`sie` vom Gerät; unbekannt = Sie. Entwürfe sind kurz, ohne Unterschrift und ohne „Danke!“-Bot-Ton (M9).
- **Identitätsfragen** („Bist du ein Mensch?“, „Bist du Anna?“, „Hast du Gefühle?“) erkennt der Server ohne Modell
  (`detect_ident`) und antwortet immer aus der Vorlage; „im Auftrag von Anna“ nur mit gültigem `agent`-Beleg.
- Web: Einstellungen › Saga (Anrede, ruhigere Lebhaftigkeit). Apps zeigen dieselben zwei Einstellungen über
  `/api/ai/prefs`; für Englisch gibt es keine Anrede-Unterscheidung (Auswahl darf dort entfallen).

## 10. Aufträge (2.6, Bugs-DB 2267 K-B – Abbruchpunkt A)

Eine Bitte wird zum **Plan**, der Plan nach dem OK der Person zum **Auftrag**. In 2.6-A gibt es nur Schritte zum Lesen
und für **eigene Änderungen** (eintragen, Aufgabe, Termin – Werkzeuge der Registry `saga/tools.py` mit `kollegin=True`
und Klasse `read`/`own`); Schritte nach außen (Nachrichten, `act`, `outbox`, `inbox`) kommen später (K-C/K-E). Das
Modell schlägt nur vor – was gilt, prüft der Server (Registry, Rechte, Texte, Seiten).

**Fähigkeit:** `GET /api/ai/status` hängt `"jobs": true|false` und `"level": "plans"|"recipes"|"answers"` hinten an
(Fähigkeitsstufe, die der Admin einstellt; `answers` = keine Aufträge). Ältere Server senden die Felder nicht.

**Schlüssel:** Sitzung (Web; Apps nach `/app/resume` mit `hkd_`) mit Kopf `X-Saga: 1`. Der Widget-Schlüssel `hk_` darf
nur `GET /api/ai/jobs`. Jeder andere `Authorization: Bearer …` (API-Token `sga_`, MCP, OAuth) bekommt auf **allen**
Pfaden unter `/api/ai/jobs` `403`, nie `2xx` (RT-42) – auch auf Pfaden, die es noch nicht gibt.

| Endpunkt | Zweck |
|---|---|
| `POST /api/ai/jobs` (SSE) `{text, context?: {note}, choice?, source?, thread?}` | Auftrag geben. Ereignisse: `status {phase, text}` (zuerst), dann genau eines von `plan`, `ask {question, choices[]}`, `refuse {zug, case, text, alternative?}`, `error {status, error}`. `thread: true` = aus dem Gespräch im Ort „Saga“ (Abschnitt 11.7): Bitte und Ergebnis stehen als Züge im Verlauf, das Ereignis trägt zusätzlich `turn` |
| `POST /api/ai/jobs` `{recipe: "aufgabe", fields: {text}, text?}` | Rezept direkt, ohne Modell (Knopf „Als Aufgabe anlegen“ aus `refuse.alternative`). Nur die Kennung eines eingeschalteten Rezepts – ein Rezept-Objekt ist 400. `alternative.fields.text` ist ein kurzer Titel (erste Zeile/erster Satz, höchstens 80 Zeichen, Befehlsform bleibt – 2509); die ganze Bitte als `text` mitschicken, sie steht dann im Detail als `request` (Aufgaben haben kein Beschreibungsfeld) |
| `GET /api/ai/jobs[?group=wartet_auf_dich|laeuft|fertig|nicht_geklappt]` | Liste: `{groups: [{key, label, items: [{id, title, state, state_text, sub, group, line, created, updated, plan_v}]}], counts}` – immer die 4 Gruppen in dieser Reihenfolge, „Fertig“ = letzte 30 Tage |
| `GET /api/ai/jobs/{id}` | Detail: `state`/`state_text` (genaues Wort), `reason_text`, `plan` (wie Ereignis `plan`), `edited` (wie `plan.edited`), `request` (die Bitte im Wortlaut), `approved {at, device}`, `changes[]`, `report` (bei Erledigt/Zurückgenommen), `events[] {at, kind, text}` (Protokoll), `actions[]` |
| `PUT /api/ai/jobs/{id}/plan` `{plan_v, edits: [{n, args}]}` | „Ändern“: nur die Felder aus `steps[].edit`. Antwort = neuer Plan (`plan_v`+1, neuer `plan_hash`, `edited: true`); die alte Freigabe gilt nicht mehr. 409 = veraltet, 400 = Text mit Adresse/Erwähnung oder festes Feld |
| `POST /api/ai/jobs/{id}/approve` `{plan_v, plan_hash}` | „Passt, los“ → `{state: "laeuft", say}`. 409 `code: stale` (Plan geändert) bzw. `code: state` (schon freigegeben/abgebrochen). Zweimal gleichzeitig = genau eine Freigabe |
| `POST /api/ai/jobs/{id}/cancel` | „Lieber nicht“ / „Auftrag abbrechen“ → `{state: "abgebrochen", text}` |
| `POST /api/ai/jobs/{id}/undo` `{change?}` | Rückgängig – ganz oder eine Änderung. 409 `{code: "changed", why, why_text, version_url}` = das Ziel wurde seitdem geändert (nie still überschreiben); `version_url` ist `null`, wenn es keine alte Fassung gibt (Aufgaben, Termine, neue Seiten). Einen Termin, den es nicht mehr gibt (von Hand gelöscht), zählt der Server als zurückgenommen: `200 {ok, already: true, text: "Schon erledigt – …"}` statt 409 |
| `GET /api/ai/jobs/{id}/changes/{c}/before` | „Alte Fassung holen“: `{target, title, text}` (Klartext 30 Tage nach Ende) |

**Plan** (Ereignis `plan` und `detail.plan`): `{job, plan_v, plan_hash, title, state, steps[], read[], read_text, wording[],
classes[], say, recipe, edited}`; je Schritt `{n, tool, klass (read|own), kind_text, undo_class (undo|delete_only|final, bei read
null), class_text, text, wording, edit[], fields{}}`. `text`, `kind_text`, `class_text`, `read_text` („Ich lese dafür: …“)
sind Systemtext in der Sprache der Anfrage – Apps zeigen sie unverändert. `wording` ist der wörtliche Text eines Schritts
(KI-Markdown-Teilmenge, Abschnitt 4). Der Plan ist ein Vertrag: Freigabe nur mit genau diesem `plan_v` + `plan_hash`.

**`edited` (Bugs-DB 2515, Vertrag mit den Apps):** Ereignis `plan`, `detail.plan`, `detail` (oben) und die Antwort auf `PUT
…/plan` enthalten **immer** `edited: true|false` – `true`, solange der Plan seit dem Vorschlag geändert wurde („Ändern“)
und noch nicht neu freigegeben ist (Zustand `wartet_ok`); nach der Freigabe und bei frischen Plänen `false`. Die
Plan-Karte zeigt bei `true` „geändert – bitte neu freigeben“ – auch nach dem Neuladen und auf einem anderen Gerät.
Nach **409 `stale`** (Freigabe/Ändern auf altem `plan_v`) das Detail neu laden und den neuen Plan mit diesem Hinweis
zeigen, nie still zum neuen Plan wechseln; nach **409 `state`** (anderswo schon freigegeben/abgebrochen) keine
Plan-Karte mit aktivem „Passt, los“ stehen lassen, sondern den jetzigen Stand (Rückmeldung bzw. Zustandswort). Die
Rückmeldung nicht nur am Live-Ereignis festmachen: beim (Wieder-)Verbinden der Ereignis-Quelle und beim Öffnen den
Auftrag einmal neu holen (Ereignisse dazwischen gehen verloren).

**Zustände** (K2): `wartet_ok` „Wartet auf dein OK“, `laeuft` „Läuft“, `wartet_dich` „Wartet auf dich“, `erledigt`
„Erledigt“, `fehlgeschlagen` „Hat nicht geklappt“, `abgebrochen` „Abgebrochen“, `zurueckgenommen` „Zurückgenommen“; nach
außen 4 Gruppen. Wörter immer aus `state_text`/`label` (Systemtext), nie selbst übersetzen.

**Rückmeldung** (`detail.report`): `{text, lines: [{change, text, label, undone, can_undo}]}` – Ergebnis zuerst, je Änderung
eine Zeile mit Knopf „Rückgängig“ (≥ 44 pt). `text` nennt die Änderungen schon; damit das Ergebnis nicht zweimal steht,
zeigt das Web neben „Rückgängig“ nur `label` (den Gegenstand) bzw. bei einer einzigen Änderung nur den Knopf (Name für
Screenreader: „Rückgängig: {lines[].text}“). Bei `undo_class` `delete_only` vor dem Löschen nachfragen.

**`changes[]`** `{id, step, target, label, undo_class, class_text, undone, can_undo, why, why_text, version_url}` (2515):
`why` sagt, warum `can_undo` `false` ist (nur bei „Erledigt“): `changed` (seitdem geändert), `gone` (gibt es nicht mehr),
`no_undo` (lässt sich nicht zurücknehmen), `done` (schon wie vorher, z. B. die Aufgabe von Hand gelöscht) – sonst `null`;
`why_text` ist der Satz dazu (Systemtext, unverändert zeigen). **„Alte Fassung ansehen“ nur zeigen, wenn `version_url`
ein Text ist** – den gibt es nur für geänderte Seiten und Zeilen (`target` `note:`/`row:` mit gespeichertem Vorher-Stand);
für Aufgaben, Termine und neu angelegte Seiten ist er `null` (vorher zeigte der Knopf dort ins Leere). Dasselbe gilt für
`version_url` in den 409-Antworten von `…/undo`.

**Live:** `ai.job.changed {id, state, sub, plan_v}` an die Besitzerin (nur Kennungen) – Liste/Detail neu laden.

**Protokoll und Titel** (web-saga): Das Ereignis „Freigegeben“ steht vor „Läuft“ (beide in derselben Transaktion).
Ohne Titel des Modells nimmt der Server den Text des ersten Schritts, beim Eintragen die erste Zeile des Wortlauts,
sonst den Namen des Rezepts – „Auftrag“ nur, wenn gar nichts da ist. Listen-Symbole im Web: ✓ nur bei „Erledigt“,
„Abgebrochen“ –, „Zurückgenommen“ ↶.

**Melden:** `POST /api/ai/report {where: "job", page: <auftrag>, reason, text?}` (wie Abschnitt 6).

**Admin** (Admin-Port): Admin › KI-Assistent › Aufträge – Fähigkeitsstufe, eingebaute Rezepte an/aus, eigene Rezepte
(JSON, nur Werkzeuge `read`/`own`), Knopf „Selbsttest“ = `POST /api/ai/selftest` (Admin-Sitzung + `X-CSRF-Token`) →
`{level, valid, total, text: "Vorschlag: Stufe „…“ (x von y Plänen gültig)", note, cases}` – nur ein Vorschlag.

## 11. Ort „Saga“, Einwilligung und „Meine Daten“ (2.6, Bugs-DB 2269 K-I)

Konzept K10/K11/K19, M1/M3/M4/M7/M11; Server `saga/ai_saga.py`. Alle Endpunkte mit Sitzung oder Geräte-Anmeldung und
`X-Saga: 1` wie der Rest von `/api/ai/*`. Ältere Server kennen sie nicht (404) – Apps blenden dann den Ort „Saga“ aus.

### 11.1 Anbieter-/Host-Hinweis – bildet nur der Server (M4, RT-39)

`GET /api/ai/status` → zusätzlich (am Ende, ältere Felder unverändert):

```json
"endpoint": {"host": "ollama.lan:11434", "net": "home", "label": "ollama.lan:11434 (im Heimnetz)", "note": "Steht bei uns im Keller."},
"consent":  {"state": "none"},
"intro":    {"version": "k19-1", "lines": ["Ich bin Marin, die KI eures Servers.", "Ich sehe nur, … gehen an ollama.lan:11434 (im Heimnetz).", "Ich ändere nichts …"],
             "note": "Steht bei uns im Keller.", "question": "Soll ich dir ab und zu Ideen zeigen – …?", "ideas_allowed": true,
             "details": ["Nicht Ende-zu-Ende verschlüsselt: …", "Was genau rausgeht: …", "Die Adresse liegt in eurem Heimnetz.", "Du kannst …"],
             "bar": "Was du hier schreibst, liest die KI auf ollama.lan:11434 (im Heimnetz)"}
```

- `host` = Host[:Port] des **Modell-Endpunkts**, nie die Saga-Domain. `net`: `home` nur, wenn alle beim Speichern
  festgehaltenen IPs (bzw. die IP in der Adresse) privat sind; sonst `internet` – auch ohne festgehaltene IPs.
- Bekannte Anbieter (nur bei `internet`): `provider` (z. B. „Anthropic“), `country` (ISO, z. B. `US`) und
  `country_name` (Sprache der Anfrage). Azure/Google Cloud ohne Land (je Region).
- `note`: der eine Zusatzsatz des Admins (Admin › KI-Assistent, „Zusatzsatz zum Anbieter-Hinweis“): eine Zeile, erster
  Satz, ≤ 200 Zeichen. Bei `internet` fällt er weg, sobald er „lokal“, „im Heimnetz“, „on-premise“, „local“ o. Ä.
  behauptet (auch verschleiert geschrieben).
- `label`, `intro.*`: fertige Texte in der Sprache der Anfrage. **Apps zeigen genau diese Texte** und bauen nie selbst
  „im Heimnetz“/„lokal“. `intro.lines[1]` nennt immer „Deine Frage und passende Ausschnitte aus deinen Seiten, Aufgaben
  und Terminen gehen an {label}.“; `details` gehören hinter ⓘ (M3), `bar` ist die feste Leiste B6 im Ort „Saga“.
- `question` nur mit Fähigkeit `ai_ideas` (sonst `null`, Knopf „Ja, einverstanden“ statt „Ja, mit/ohne Ideen“).
- `intro.lines` kommen aus dem Baustein `intro` (Abschnitt 9, Anrede du/Sie der Person) mit `label` als Ort; nur wenn
  der Baustein fehlt, die festen Sätze. Die Felder stehen in `/api/ai/status` hinter `style`.

### 11.2 Einwilligung (K19, Apple 5.1.2(i), Art. 7 DSGVO)

| Aufruf | Wirkung |
|---|---|
| `POST /api/ai/consent {"answer": "ideas"\|"no_ideas", "version": "<intro.version>"}` | erteilt (Zeitpunkt, Fassung, Host); `ideas` setzt Ideen „Nur auf Heute“ (nur mit `ai_ideas`). Andere Fassung → **409** `{error, version}`: Status neu laden, Karte neu zeigen. → `{consent, prefs}` |
| `POST /api/ai/consent {"answer": "later"}` | „Jetzt nicht“: keine Einwilligung; „Heute“ zeigt die Karte nicht mehr, der Ort „Saga“ bei jedem Öffnen. → `{consent}` |
| `DELETE /api/ai/consent` | Widerruf: Ideen aus, laufende Aufträge → `wartet_dich` mit Grund `einwilligung` (RT-36). → `{consent, paused}` |

`consent.state`: `none` · `later` · `granted` (`at`, `version`, `ideas`, `host`) · `withdrawn` (`at`) · `stale` =
erteilt, aber für einen anderen Host (Admin hat den Endpunkt gewechselt) – gilt nicht, Karte erneut zeigen. Ebenso
(Bugs-DB 2494), wenn die Einwilligung für `endpoint.net` `home` galt und der Endpunkt jetzt `internet` ist (neu gespeichert,
oder der Name löst beim Verbinden öffentlich auf – dann verweigert der Server die Verbindung, bis der Admin neu speichert).
Ohne Fähigkeit `ai` → 403. Gespeichert als Protokoll (`ai_consent`), Nachweis bis zur Kontolöschung.

**Was die Einwilligung sperrt** (Entscheidung 26.11.: der Widerruf stoppt nur die Kollegin): Planen, Ändern und
Freigeben von Aufträgen (`/api/ai/jobs`, `…/plan`, `…/approve` → 403 `{consent: "needed"}`), jeden Schritt eines
Auftrags (`jobs.consent_ok`), Ideen von sich aus (`ai_saga.ideas_ok`) und das Gespräch im Ort „Saga“. **Unverändert**
(Fähigkeit `ai`): „Frag deine Seiten“ (`/api/ai/ask`), die Seiten-Hilfen und die KI im Chat auf Zuruf – sie zeigen
denselben Host-Hinweis: `/api/ai/status` → `notice` = „Was du hier fragst, liest die KI auf {label}.“, Details hinter ⓘ
aus `ask_details` (eigene Sätze: Frage und passende Ausschnitte, frühere Fragen gehen nicht mit, „Diese Seite“, ggf.
Websuche – nicht die Sätze des Gesprächs aus `intro.details`; ältere Server: `intro.details`); im Chat steht
`endpoint.label` statt des nackten Hosts.

`POST /api/ai/consent` braucht zusätzlich `host` = `intro.host` (der Host, den die Karte nannte); weicht er vom
jetzigen Endpunkt ab → **409** `{error, version, host}`: Status neu laden, Karte neu zeigen. `host` (auch `/api/ai/status`
`host`) nie mit Zugangsdaten (`user:pw@` fällt weg).

**Anrede (2515):** Alle Teile von `intro` – `lines`, `bar`, `question` und `details` – folgen der Anrede der Person
(`/api/ai/prefs` `salutation`, sonst Standard des Admins): bei „Sie“ die gesiezten Bausteine `intro.bar`,
`intro.question`, `intro.e2e`, `intro.what`, `intro.home`, `intro.withdraw` (`saga/say/<lang>.json`, nur de/es/ru – en/th
haben keine Formunterscheidung, dort bleibt der Systemtext). Apps zeigen die Texte unverändert; nach dem Ändern der
Anrede `/api/ai/status` neu laden.

### 11.3 Gespräch im Ort „Saga“ (K10)

- `GET /api/ai/thread` → `{turns: [{n, role: "me"|"ai", zug, text, fields, read, job_id, created, expires}], history,
  keep, consent}`. `keep` = 86400 (Verlauf aus: Züge laufen nach 24 h ab), `null` mit Verlauf (höchstens 200 Züge).
- `POST /api/ai/thread {q, tasks?, calendar?}` → `text/event-stream` wie `/api/ai/ask` (`sources`, `status`, `delta`,
  `done`), `done.turn` = Nummer des KI-Zugs, `done.read` = Kontext-Ausweis. Die letzten 6 Züge der letzten 24 h gehen
  im Datenrahmen an das Modell (als zitierte Daten, nie als eigene `assistant`-Nachrichten – K7). Ohne Einwilligung
  **403** `{error, consent: "needed"}` – nichts geht ans Modell. Höchstens 200 eigene Fragen je 24 h (429); der eigene
  Zug wird erst nach bestandener Kontingent-Prüfung gespeichert.
  Kontingent, Spur und Fehler wie bei `/api/ai/ask`. Keine Websuche (kommt später).
  Anders als `/api/ai/ask` geht **jede** Nachricht ans Modell, auch ohne passende Seite und ohne früheren Zug (2538):
  auf Gruß/Dank antwortet die Kollegin kurz in der Persona, Faktenfragen ohne Material mit „nichts gefunden“ (vom
  Modell, Zug `answer`, zählt aufs Kontingent). `done.no_match` kommt im Gespräch nicht mehr; ältere Züge mit
  `zug: "no_match"` bleiben lesbar.
  Quellen (2542): das erste `sources` nennt alle gelesenen Seiten (für die `[n]`-Links beim Streamen); direkt vor
  `done` kommt ein zweites `sources` mit nur den belegten (`[n]` in der Antwort, ohne Beleg – etwa „nichts gefunden“ –
  leer) und ersetzt das erste. Der gespeicherte Zug (`fields.sources`) enthält nur die belegten; was gelesen wurde,
  steht weiter in `done.read`/`read`. Clients zeigen höchstens drei Quellen und den Rest hinter „{n} weitere“.
- `DELETE /api/ai/thread` → `{deleted}` (Gespräch leeren).
- Kein E2E-Chat: eigene Tabellen `ai_thread`/`ai_turn`, nie in `/api/chats`. Apps zeigen den Ort als eigenen Tab bzw.
  Bereich „Saga“ (M1), Kopf „{persona.name} · KI“ mit Persona-Bild und KI-Marke (K11).

### 11.4 Einhängepunkt für Plan-Karte und Rückmeldung (Web)

`ai_turn` hat `zug` (Baustein-Kennung: `answer`/`no_match`, mit Aufträgen `job`/`plan`/`ask`/`refuse`, Abschnitt 11.7),
`fields` (JSON) und `job_id`. Im Web: Container `#sg-cards` und `HKSaga.mount(node)` (hängt eine Karte an den Verlauf),
Ereignisse auf `document`: `saga:status` `{status}`, `saga:turn` `{turn, q}` nach jeder Antwort, `saga:consent`
`{consent}`.

### 11.5 Einstellungen der Person – Ergänzung zu `GET/PUT /api/ai/prefs` (Abschnitt 9)

Derselbe Endpunkt wie in Abschnitt 9 (Anrede, Lebhaftigkeit); K-I hängt an: `ideas: "off"|"today"`, `sources: {pages,
tasks, calendar, chat}`, `history`, `history_allowed`, `notify_content: "unlocked"|"never"`, `dictation:
"server"|"device"`, `ideas_allowed`, `consent`. `PUT` ändert nur genannte Felder, falsche Werte 400 (vor jedem
Schreiben). `ideas: "today"` ohne Einwilligung 409, ohne Fähigkeit `ai_ideas` 403. Ohne Einwilligung liefert `GET`
immer `ideas: "off"`. `history: false` löscht den gespeicherten Verlauf und lässt das Gespräch nach 24 h ablaufen
(ebenso `PUT /api/ai/settings {history: false}`). Alles liegt im gemeinsamen Eintrag `user_kv` `ai_prefs`; fremde
Schlüssel bleiben stehen. `notify_content` und `dictation` werden gespeichert und von späteren Paketen gelesen.

### 11.6 Meine Daten (M7, Art. 15/17/20, RT-35)

- `GET /api/ai/mydata?format=json|md` → Datei (`Content-Disposition: attachment`): `{format: "saga-ai-export/1",
  exported_at, account, persona, endpoint, consent: [...], prefs, jobs: [{…, steps, events, changes, waits}], conversation,
  history, counters: {requests, units}}` bzw. dieselben Inhalte als Markdown. Nur eigene Daten, höchstens 500 Aufträge.
- `POST /api/ai/mydata/delete {"confirm": true}` → `{ok, deleted: {jobs, turns, history}}`: löscht Aufträge (mit
  Schritten, Protokoll, Änderungen), Gespräch und Verlauf. Es bleiben: das Einwilligungs-Protokoll (Nachweis) und die
  Tageszähler (nur Zahlen; 7 bzw. 90 Tage). Ohne `confirm: true` → 400.
- Aufbewahrung (M11): Gespräch ohne Verlauf 24 h, Aufträge über `jobs.cleanup` (Klartext 30, Protokoll 90 Tage).
  Kontolöschung räumt `ai_consent`, `ai_thread`, `ai_turn` ab.
- Web: Einstellungen › Saga › „Meine Daten“ mit „Einwilligung widerrufen“, Export (JSON/Markdown), „Alles löschen“
  (Rückfrage) und dem Hinweis „Was mein Admin sehen kann“. Die Apps zeichnen denselben Bereich (Runde 2).
- `waits[]` (web-saga): worauf ein Auftrag wartet – `{step, on, ref, until, paused_at, hit_at, data}` (Tabelle `job_wait`).

### 11.7 Aufträge im Ort „Saga“ (2.6 W2, web-saga – Abbruchpunkt A)

Konzept K1/K10, UX §2 und R3/R4: Der Ort „Saga“ hat die Reiter **Gespräch / Aufträge** (Apps: Segment). Der Reiter
„Aufträge“ (Liste in 4 Gruppen + Detail) ist aus dem KI-Bereich der Seiten hierher gewandert; dort bleiben „Frag deine
Seiten“ und die Hilfen für die offene Seite.

- **Auftrag geben im Gespräch:** ein klarer Umschalter **„Als Auftrag“** neben dem Eingabefeld (kein Raten, ob ein Satz
  ein Auftrag ist – ein Fehlgriff würde ohne Absicht planen bzw. einen Auftrag als Frage beantworten, und eine
  Einordnung durch das Modell kostete einen Aufruf je Satz). Aus: `POST /api/ai/thread` (Frage). An: `POST /api/ai/jobs`
  mit `thread: true` – Knopf „Auftrag geben“, Platzhalter „Sag {name}, was du brauchst …“.
- **Züge** (`GET /api/ai/thread`): die Bitte `{role: "me", zug: "job", text}`, dann genau einer: `{role: "ai", zug:
  "plan", job_id, text: "Plan: {title}"}` · `{zug: "ask", text: <frage>, fields: {choices, q}}` · `{zug: "refuse", text,
  fields: {alternative, q}}`. Fehler (nichts passiert) erzeugen keine Züge. Ein Plan-Zug zeigt immer den **jetzigen
  Stand** des Auftrags aus `GET /api/ai/jobs/{id}`: `wartet_ok` → Plan-Karte, `erledigt`/`zurueckgenommen` →
  Rückmeldung mit „Rückgängig“, sonst Zustandswort + Grund + „Ansehen“ (öffnet das Detail im Reiter „Aufträge“). Auf
  `ai.job.changed` zeichnet der Verlauf den Baustein neu (nicht während „Ändern“ offen ist).
- **Ein Freigabe-Muster:** Plan-Karte, Rückfrage, Absage und Rückmeldung sind im Web dieselben Bausteine wie im
  Detail (`static/ai-jobs.js`: `HKJobs.planCard`, `askCard`, `refuseCard`, `feedback`, `head`); `HKJobs.mount(el,
  {form: false, give})` zeigt Liste und Detail ohne eigene Eingabe. Apps zeichnen ebenso nur eine Plan-Karte.
- **Ohne Einwilligung** (Widerruf, `stale`): Gespräch und „Als Auftrag“ sind gesperrt (nur die Kennenlernen-Karte), aber
  vorhandene Aufträge bleiben über den Reiter „Aufträge“ erreichbar – Liste, Detail, Abbrechen und Rückgängig brauchen
  keine Einwilligung; darüber steht „Deine Aufträge sind angehalten, weil du die Einwilligung widerrufen hast …“. Apps
  verhalten sich genauso. Die Züge zu einem Auftrag legt der Server auch an, wenn die Verbindung nach dem Planen reißt
  (genau einmal, `ai_jobs.plan_job`).
- **Auftrag zu einer offenen Seite (2508):** Im KI-Bereich einer Seite (Reiter „Diese Seite“) führt „{name} soll sich
  kümmern …“ (nur mit `status.jobs`, nur Seiten dieses Servers) in den Ort „Saga“ (`/saga?note=<id>`): Gespräch mit
  „Als Auftrag“ an, über dem Eingabefeld „Zur Seite „…““ mit ×. Jeder Auftrag von dort geht als `POST /api/ai/jobs`
  `{text, thread: true, context: {note: <id>}}`. Der Server nimmt die Seite nur, wenn die Person sie lesen darf
  (`sharing.access … view`); sonst bleibt sie still draußen (kein Fehler, nichts von ihr im Plan oder Material), ebenso
  gelöschte Seiten. **Apps (später, K-G):** derselbe Weg – aus der Seitenansicht in den Ort „Saga“ springen, Seite als
  Kontext merken (Titel aus `GET /api/notes/<id>`) und beim Auftrag `context.note` mitschicken; das Ereignis `plan`
  nennt die Seite in `read`/`read_text` („Ich lese dafür: …“).
- **Begriffe:** „Auftrag“ (etwas, das die KI erledigt) und „Aufgabe“ (Eintrag der Aufgabenliste) sind verschieden:
  en **Job**/„Jobs“ vs. *Task*, es **Encargo**/„Encargos“ vs. *Tarea* (ru „Поручение“ vs. „Задача“). „Requests“ scheidet
  aus, weil „Anfragen/Tag“ (Kontingent) im Englischen schon „requests“ heißt. Systemtexte kommen fertig vom Server.

## 12. Mitteilungen und Aktionen (2.6, Bugs-DB 2270 K-C – Abbruchpunkt B, `jobpush1`)

Rückmeldungen zu Aufträgen kommen als Mitteilung aufs Gerät, mit Knöpfen direkt in der Mitteilung (Konzept K14/K15).
Nutzlast, Felder, Kategorien, Android-Kanäle und Knopf-Regel: [`APP-API.md`](APP-API.md) „Push-Nutzlast 3 und Aufträge
(`jobpush1`)“; Testvektoren `tests/vectors/push_kollegin.json`. Hier: Glocke, Aktions-Token, Endpunkt `act`, Ruhe.

### 12.1 Glocke

Jeder Übergang mit Mitteilung (APP-API.md, Tabelle „Arten und Übergänge“) legt auch einen Eintrag in der Glocke an –
unabhängig von Push, „Nicht stören“ und „Gerade nicht“ (die Glocke bekommt immer alles): `GET /api/notifications` →
`{kind: "job.done"|"job.needs_you"|"job.failed", text: "Erledigt: Grillkohle besorgen", url: "/saga?tab=jobs&job=42",
job: 42, …}` (`text` = `t1`, Systemtext in der Sprache der Anfrage). Ein neuer Eintrag desselben Auftrags ersetzt den
alten; bei `abgebrochen`/`zurueckgenommen` verschwinden die Einträge des Auftrags (`notification.changed` `removed`).

### 12.2 Aktions-Token (`atok`)

- Je Abo und Mitteilung ein Token je Knopf (`actions: [{a, t}]`), zufällig (`secrets.token_urlsafe(18)`, 24 Zeichen
  Base64url), **einmalig**; in der Datenbank steht nur `sha256(t)` (Tabelle `ai_act_token`).
- Gebunden an: Konto, Auftrag, Abo (und damit Gerät), `plan_v` + `plan_hash`, Aktion, Ablauf. Ablauf 24 h; bei
  „braucht dich“ bis zur Frist des Auftrags (`due_at`), höchstens 14 Tage.
- Nur auf E2E-Wegen und nur mit Abo-`caps` `actions`. `approve`/`undo` nur, wenn auch `detail` mitgeht (was man nicht
  sieht, bestätigt man nicht): `approve` nur bei `wartet_ok` mit ausschließlich `read`/`own`-Schritten, alle
  `undo_class: undo`; `undo` nur bei `erledigt`, wenn sich alle Änderungen zurücknehmen lassen (nicht `delete_only`,
  nicht `final`). Abo entfernt oder Konto gelöscht → Token weg.

### 12.3 `POST /api/ai/jobs/{id}/act`

```json
POST /api/ai/jobs/42/act   {"action": "undo", "atok": "WuKANryrN8RlH60qY3vFUDoR", "pv": 2}
→ 200 {"ok": true, "action": "undo", "state": "zurueckgenommen"}
```

**Zugänge:** Geräte-Schlüssel `Authorization: Bearer hkd_…` (Apps im Hintergrund, auch ohne Sitzung) oder Sitzung mit
`X-Saga: 1` (Web, Service Worker, Hülle). Ein Token mit Gerät gilt nur für **dieses** Gerät; ein Token ohne Gerät
(Browser-Abo) nur mit Sitzung. Widget-Schlüssel `hk_…` nur `later`/`quiet`. `sga_`, MCP, OAuth: `403` (RT-42).

| Aktion | Wirkung | Antwort 200 |
|---|---|---|
| `approve` | „Passt, los“ – wie `POST …/approve` mit `plan_v`/`plan_hash` aus dem Token (Einwilligung nötig) | `{state: "laeuft"}` |
| `undo` | „Rückgängig“ – dieselbe Rücknahme wie `POST …/undo` (ganz), wartet ≤ 3 s auf einen laufenden Schritt | `{state: "zurueckgenommen"}` |
| `later` | „Später“ – dieselbe Mitteilung (Zustand beim Zustellen neu geprüft) nach 3 h erneut, höchstens 2× | `{state, at}` |
| `quiet` | „Gerade nicht“ – keine `job.*`-Mitteilungen bis zum nächsten 07:00 Kontozeit; wartende `job.*` entfallen; Glocke bekommt weiter alles | `{state, until}` |
| `open` | nichts (ohne Token) | `{url}` |
| `retry`, `reply`, `ideas_off` | reserviert | `409 {code: "open"}` |

**Fehler** (geprüft in dieser Reihenfolge; nur `200` verbraucht den Token):

| Status | `code` | Bedeutung | App |
|---|---|---|---|
| `403` | `key` | Schlüssel darf das nicht (`hk_` mit `approve`/`undo`, `sga_`/OAuth) | App öffnen |
| `404` | – | Auftrag gibt es nicht (mehr) bzw. gehört einem anderen Konto | Mitteilung entfernen |
| `403` | `cap` | Fähigkeit `ai` fehlt inzwischen | App öffnen |
| `410` | `used` / `expired` / `gone` | Token schon benutzt, abgelaufen, unbekannt (Abo weg) oder für eine andere Aktion/einen anderen Auftrag | Hinweis „Bitte in Saga ansehen“ |
| `403` | `device` | Token gehört zu einem anderen Gerät bzw. braucht eine Sitzung | App öffnen |
| `409` | `stale` | `pv` bzw. Plan seit der Mitteilung geändert | Hinweis „Bitte in Saga ansehen“ → `saga://job/<id>` |
| `409` | `state` | Zustand passt nicht mehr (anderswo schon freigegeben, abgebrochen …) | wie `stale` |
| `409` | `changed` | Rücknahme nicht möglich: Ziel seitdem geändert (wie `…/undo`) | wie `stale` |
| `409` | `open` | geht nur in der App (z. B. Schritt nach außen, reservierte Aktion) | wie `stale` |

`409` trägt `url` (`/saga?tab=jobs&job=<id>`). Nach `200` entfernt die App die Mitteilung. Netzfehler: einmal
wiederholen, dann wie `409` (eine zweite Freigabe mit demselben Token ist harmlos: `410`).

### 12.4 Ruhe „Gerade nicht“ und Einstellungen

- `GET/PUT /api/ai/prefs` (Abschnitt 9/11.5): `notify_content` `unlocked` (Standard) | `never` – „Inhalt in
  Mitteilungen“: bei `never` kein `detail` (und damit kein `approve`/`undo`) auf keinem Gerät. Neu `notify_quiet_until`
  (Zahl, Unix-Zeit, oder `null`): bis dann keine `job.*`-Mitteilungen; `PUT {"notify_quiet_until": null}` = „Wieder an“.
- „Mitteilungen von {Persona}“ = Bereich `jobs` in `PUT /api/push` `cats`.
- Kontozeit = Zeitzone der Ruhezeiten (`/api/push` `prefs.quiet.tz`), sonst die des Servers; „bis 07:00“ heißt das
  nächste 07:00 nach jetzt (Wanduhr, auch über die Zeitumstellung; Beispiele in den Vektoren `quiet`).

### 12.5 Server intern (für andere Bereiche)

- Jeder Zustandswechsel eines Auftrags feuert in derselben Transaktion `bus.fire("job.state", c, job_id=, uid=, kind=,
  old=, new=, reason=)` (`saga.jobs._to`); `saga.ai_notify` hört nur auf Aufträge der Kollegin.
- Ruhe-Helfer `saga.ai_saga.set_quiet(uid, where, until)` / `quiet_until(uid, where)` mit `where` `push` (K-C) bzw.
  `today` (Tageszeile, K-D); `until` `None` bzw. in der Vergangenheit = aus. Zeit aus `testclock.now()`.
