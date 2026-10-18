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
