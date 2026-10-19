# App-API – Verträge für die App-Teams

Ergänzungen der HTTP-API für die Apps (iOS/iPadOS/macOS inkl. Widgets, Android, Linux). Live-Ereignisse stehen in
`APP-API-LIVE.md`, Chat in `APP-API-CHAT.md`, KI in `APP-API-KI.md`.

## Datenbank-Zeilen: Ansicht, Filter, Sortierung, Seiten (2.2)

`GET /api/notes/{id}/rows` (Sitzung oder Geräte-Schlüssel `hkd_…`, ab Rolle *Lesen*) und
`GET /api/app/rows/{id}` (App-Schlüssel `hk_…`, Widget) können die Ansicht einer Datenbank **auf dem Server** anwenden
und nur einen Ausschnitt liefern. **Opt-in:** ohne einen der neuen Parameter ist die Antwort Byte für Byte wie bisher
(alle Zeilen, keine neuen Felder). Rückwärtskompatibel, `MIN_APP` ändert sich nicht. Unbekannte Parameter werden wie
bisher ignoriert.

### Parameter (Query-String, alle optional)

| Parameter | Bedeutung |
|---|---|
| `view` | ID einer Ansicht aus `schema.views`. Unbekannte ID → erste Ansicht (wie im Web). Übernimmt deren `filters`, `sorts` (auch das alte Einzelfeld `sort`) und Gruppierung. |
| `filters` | JSON-Liste wie `view.filters` (`[{"pid":"…","op":"…","value":"…"}]`, höchstens 50). Ersetzt die Filter der Ansicht – für eingebettete Ansichten (deren Ansichten im Block der Seite liegen, nicht in der Datenbank). `[]` = keine Filter. |
| `sorts` | JSON-Liste wie `view.sorts` (`[{"pid":"…","dir":"asc"\|"desc"}]`). Ersetzt die Sortierung der Ansicht. |
| `q` | Suchtext wie das Suchfeld der Datenbank (Name, Werte, Namen der Optionen, Titel verknüpfter Einträge), höchstens 500 Zeichen. |
| `group` | ID einer Auswahl-Spalte: Zeilen gruppiert liefern (siehe `groups`). Leer (`group=`) = nicht gruppieren. Ohne den Parameter: Gruppierung der Ansicht (Tabelle/Liste: `group`, Board: `group_by`, sonst die erste Auswahl-Spalte wie im Web; Galerie/Kalender: keine). |
| `limit` | Höchstens so viele Zeilen (1–5000; größere Werte werden auf 5000 begrenzt). Ohne `limit`: alle ab `offset`. |
| `offset` | Ab dieser Position (0-basiert) in der fertig gefilterten, sortierten (und gruppierten) Liste. |
| `tz` | Zeitzone des Geräts (IANA, z. B. `Europe/Berlin`) für „heute / diese Woche / diesen Monat“ und das Tagesdatum von „Erstellt“/„Zuletzt bearbeitet“. Standard: UTC. **Apps sollten sie immer mitschicken**, wenn Datumsfilter vorkommen können. |
| `today` | Tagesdatum des Geräts `YYYY-MM-DD` (Standard: heute in `tz`). |

Falsche Werte (kein JSON, keine Liste, `limit=0`, unbekannte Zeitzone …) → `400`. Kann eine Ansicht nicht ausgewertet
werden, weil die gespeicherten Daten nicht passen (dort, wo das Web mit einem Fehler abbricht – z. B. Relation
„enthält“ bei Zeilen ohne Verknüpfung), kommt `400` mit Meldung statt einer abweichenden Liste. Rechte wie bisher:
kein Zugriff → `404`; Nicht-Eigentümer sehen keine archivierten Zeilen und keine Vorlagen.

### Antwort

Wie bisher (`schema`, `templates`, `rows` mit `id`, `title`, `icon`, `props`, `updated`, `created`, `preview`, bei
`/api/notes/…/rows` zusätzlich `role`) – `rows` enthält aber nur die Zeilen des Ausschnitts, in der Reihenfolge der
Ansicht. Zusätzlich:

| Feld | Bedeutung |
|---|---|
| `total` | Alle Zeilen der Datenbank, die der Aufrufer sieht (ohne Filter) – im Web „… von **total** Einträgen“. |
| `matched` | Zeilen nach Filter und Suche – im Web „**n** von …“. |
| `offset`, `limit` | Wie angefragt (`limit` nur, wenn gesetzt; begrenzt auf 5000). |
| `groups` | Nur bei Gruppierung: `[{"id": null, "count": n}, {"id": "<option-id>", "count": n}, …]` – zuerst „Ohne …“ (`id: null`), dann je Option in Schema-Reihenfolge, **auch leere**. Das Web zeigt in Tabelle/Liste leere Gruppen nicht, im Board leere Spalten schon (nur „Ohne …“ leer nicht). `rows` steht dann in Gruppen-Reihenfolge; Seiten (`offset`/`limit`) laufen über diese Reihenfolge. |

Seiten passen lückenlos aneinander: gleiche Reihenfolge bei jeder Anfrage (Grundreihenfolge wie ohne Parameter:
Sortierfeld, Anlegezeit – bei exakt gleicher Anlegezeit zusätzlich nach ID, damit Seiten stabil sind). Zwischen zwei
Seiten geänderte Daten können sich wie bei jeder Seitenabfrage verschieben – meldet der Live-Kanal eine Änderung an
der Datenbank oder ihren Einträgen, die aktuelle Seite neu laden (wie bisher die ganze Liste).

### Genau wie im Web

Der Server bildet die Logik des Webs (`static/pages.js`) 1:1 nach, inklusive Eigenheiten: Vergleiche von Text nach
Zeichencode nach Kleinschreibung (keine sprachabhängige Sortierung, „Ä“ nach „z“), Leeres immer unten, Auswahl nach
Reihenfolge der Optionen, Mehrfachauswahl/Relation nach Anzahl, „Berechnet“ (A ÷ B …) wie im Web gerundet, Zahlen-
filter mit `Number()`-Umwandlung. Geprüft mit Testvektoren aus dem Web-Code (`tools/dbquery_vectors.mjs`,
`tests/datenbank_abfrage.py`). Die Apps können ihre eigene Logik (`DBQuery.swift`, `database.py`) damit vergleichen.

### Beispiele

```
GET /api/app/rows/812?view=offen&limit=8&tz=Europe/Berlin            Widget: erste 8 Einträge der Ansicht "offen"
GET /api/notes/812/rows?view=tab&limit=100&offset=200&tz=Europe/Berlin  dritte Seite à 100
GET /api/notes/812/rows?filters=[]&sorts=[{"pid":"title","dir":"asc"}]&q=angebot&limit=50
GET /api/notes/812/rows?limit=500&offset=0                              nur blättern, Reihenfolge wie ohne Parameter
```

Antwort (gekürzt): `{"schema": {…}, "templates": [], "rows": [{…}, …], "offset": 0, "limit": 8, "total": 2000,
"matched": 37, "groups": [{"id": null, "count": 2}, {"id": "a1b2", "count": 20}, …], "role": "owner"}`

## Persönliches Backup (2.3)

Alle Endpunkte mit Sitzung, Geräte-Schlüssel `hkd_…` oder App-Schlüssel `hk_…` und `X-Saga: 1`; sie wirken immer nur
auf das **eigene** Konto (keine Konto-Kennung in der Anfrage). Ist die Funktion vom Admin abgeschaltet, antworten alle
außer `GET` mit `403` (`GET` liefert `allowed: false`). Neue Endpunkte, `MIN_APP` ändert sich nicht.

- `GET /api/account/backup` → `{allowed, ready, running, last_ok, last_attempt, next, target: {kind, address, user,
  folder, flow, allowed} | null, keys: [{short, fp}], schedule: off|weekly|daily, effective_schedule, time, weekday
  (0 = Montag), keep, cats: {pages, templates, archive, trash, versions, files, comments, comments_others, tasks,
  bookmarks, settings}, exclude: [id], runs: [{started, finished, trigger, name, ok, size, step, error}],
  limits: {max_mb, min_h, max_schedule}, flow: {state, login, message}, age_ok}`. `?pages=1` zusätzlich
  `pages: [{id, title, icon, parent, db}]` (eigene Seiten für die Ausschluss-Auswahl). Zeiten in Unix-Sekunden, Texte
  (`step`, `error`, `message`) in der Sprache der Anfrage. **Für Apps: Status anzeigen = diese Antwort.**
- `POST /api/account/backup/run` → `202 {started}` (läuft im Hintergrund; Fortschritt über `GET`). `409` ohne
  Ziel/Schlüssel oder wenn schon eines läuft, `429 {error, retry_at}` bei Unterschreiten des Mindestabstands.
- `POST /api/account/backup/connect` `{url, user?, password?, folder?}` → `{ok, kind, free}` oder bei Nextcloud ohne
  Benutzer `{flow: <login-url>}` (im Browser öffnen; `GET` zeigt `flow.state` wait/ok/err). `POST …/flow/cancel`,
  `POST …/test` → `{ok, free}`, `DELETE /api/account/backup/target` → `{ok, revoked}`.
- `POST /api/account/backup/key/generate` → `{private, public, filename}` (privater Schlüssel nur in dieser Antwort),
  `POST …/key/confirm` `{last6}` bzw. `{drop: true}`, `POST /api/account/backup/key` `{key}`,
  `DELETE /api/account/backup/key/{fp}`.
- `PUT /api/account/backup/settings` `{schedule?, time?, weekday?, keep?, cats?, exclude?}` → wie `GET`.
- `GET /api/account/backup/remote` → `{items: [{name, time, size, complete}]}`;
  `GET /api/account/backup/remote/{name}` → die verschlüsselte Datei (durchgereicht).
- Einspielen in zwei Schritten: `POST /api/account/backup/restore/upload` (Rohdaten, `Content-Type:
  application/octet-stream`, `.zip.age` oder `.zip`) → `{token, size, encrypted}`; dann `POST /api/account/backup/restore`
  `{token | remote: <name>, key?, folder = true, trash = false, versions = true, tasks = false, bookmarks = false}` →
  `{pages, files, versions, tasks, bookmarks, skipped, folder}`. Ein Token gilt 30 Minuten und nur für das Konto, das
  hochgeladen hat; nach dem Einspielen ist er verbraucht (Datei gelöscht) – auch nach einem Fehler. Ausnahme: falscher
  oder fehlender Schlüssel bei einem verschlüsselten Upload, dann bleibt er für einen neuen Versuch gültig.
- Benachrichtigung bei Fehlern: Art `userbackup.failed` (Glocke, `url` = `/?settings=backup`).

## Nextcloud-Dateien in Seiten (2.3)

Neuer Blocktyp `nextcloud` im Seiteninhalt (Editor.js-Format). Apps, die ihn (noch) nicht darstellen, zeigen ihn als
Karte und **schreiben ihn beim Speichern unverändert zurück** (wie jeden unbekannten Block). `MIN_APP` ändert sich nicht.

```json
{"id": "x1", "type": "nextcloud", "data": {"inst": "https://cloud.example.org", "fileid": "4711", "kind": "file",
 "name": "Bericht.pdf", "mime": "application/pdf", "size": 52311, "mtime": 1792400000, "author": "anna",
 "public": false, "fed": false, "sig": "…"}}
```

- `kind`: `file` | `image` | `folder`. `name`, `mime`, `size`, `mtime`: Stand beim Einfügen (nur Anzeige; die aktuellen
  Werte liefert der Status). Keine Pfade – `fileid` ist der Schlüssel.
- `author` (Konto, das den Block eingefügt hat) und `sig` setzt **nur der Server**. Beim Speichern prüft er: bestehende
  Blöcke anderer bleiben unverändert (der Server setzt die gespeicherten Daten wieder ein, auch wenn `sig` fehlt), neue
  Blöcke müssen aus der **eigenen** verknüpften Nextcloud kommen (sonst `403`/`400`, ohne Verknüpfung `409`).
  `public`/`fed` darf nur der Autor ändern. Auf Seiten anderer Instanzen steht in `author` ein Platzhalter (`~…`).
- Status aller Blöcke einer Seite: `GET /api/notes/{id}/nc` → `{enabled, items: [{fileid, author, ok, name?, size?,
  mtime?, mime?, dir?, preview?, entries?: [{name, dir, size}]}]}` (`ok: false` = Karte ohne Inhalt). Ab Rolle „Lesen“.
- Vorschau/Datei: `GET /api/notes/{id}/nc/{fileid}?a={author}&w={breite}` (Bild, `w` ≤ 2000) bzw. `…&dl=1` (Datei als
  Download). Sitzung reicht (für `<img>`), `X-Saga` ist nicht nötig; ohne Leserecht oder ohne Inhalt `404`. Negative
  Seiten-IDs (andere Instanz) gehen über den eigenen Server, nur für Blöcke mit `fed: true`.
- Verknüpfen: `GET /api/settings/nextcloud` → `{enabled, connected, me, server, user, since, used, suggest, flow: {state,
  login, message}}`; `POST /api/settings/nextcloud` `{url}` → `{flow: <login-url>}` (im Browser öffnen, dann `GET`
  abfragen); `POST /api/settings/nextcloud/cancel`; `DELETE /api/settings/nextcloud` → `{ok, revoked}`.
- Auswahl (immer das eigene Konto, `X-Saga`): `GET /api/nextcloud/list?path=/Ordner`, `GET /api/nextcloud/search?q=`
  (leer = zuletzt geändert) → `{items: [{fileid, name, path, dir, mime, size, mtime, preview, kind, inst}]}`;
  `GET /api/nextcloud/thumb/{fileid}?w=`; `POST /api/nextcloud/resolve` `{fileid}` oder `{url}` → `{match, block, item}`
  (Daten für einen neuen Block, ohne `author`/`sig`); `POST /api/nextcloud/import` `{fileid, note?}` → wie `POST
  /api/files` („Als Kopie übernehmen“).

## Anrufe: saga-call v3 (2.4, Etappe E0)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Web (`static/calls.js`). Signatur und Versiegelung bleiben
**unverändert** (`saga-call/v1` lokal, `saga-call/v2` föderiert, Testvektoren wie bisher). Neu sind nur Felder und Typen
im signierten JSON-Text `body`. Endpunkte wie 2.1/2.3 (`POST /api/calls`, `/answer`, `/signal`, `/end`, `/alive`);
`MIN_APP` ändert sich nicht. Ein 1:1-Anruf mit einer 2.1/2.3-Gegenstelle läuft genau wie bisher.

### Server (`GET /api/calls/config`)

Zusätzliche Felder (ältere Server liefern sie nicht – dann gilt: keine v3-Funktionen):

```json
{"enabled": true, "format": "saga-call/v1", "body_v": 3, "features": ["reneg", "pip_hint"],
 "limits": {"endpoints_video": 2, "endpoints_audio": 2, "signals_max": 600, "signals_per_min": 60,
            "signals_hard": 6000, "sig_rate": 240, "sealed_max": 24576, "caps_max": 16}, "…": "…"}
```

- `features`: `reneg` = der Server reicht offer/answer während des Gesprächs weiter (Budget unten), `pip_hint` = `want`
  wird unterstützt. Später: `multi` (E4), `group` (E5), `screen2` (E3).
- `limits.endpoints_*`: Endpunkte je Anruf (E0: 2; E4/E5 heben sie an, der Admin kann nur senken).
- Signal-Budget je Anruf: `signals_max` + `signals_per_min` je **angefangener** Minute seit der Annahme, höchstens
  `signals_hard`; darüber und über `sig_rate` je Gerät und Minute → `429`. Ein versiegeltes Signal darf `sealed_max`
  Zeichen (B64) haben.
- `POST /api/calls` und `POST /api/calls/{id}/answer` nehmen optional `caps: ["reneg","want","bye"]` (Liste kurzer Namen
  `[a-z0-9_]{1,16}`, höchstens 16; Ungültiges fällt weg). Das ist **nur ein Hinweis** für Oberfläche und Server;
  maßgeblich sind die signierten `caps` im Body. Anruf-Objekte (`POST /api/calls`, `GET /api/calls/{id}`,
  `/api/calls/active`) tragen `caps: {"caller": [...], "callee": [...]}`. Lokale Anrufe tragen weiterhin **kein** `v`
  (`v: 2` heißt wie bisher föderiert).

### Fähigkeiten (`caps`) und wann was gesendet wird

| Fähigkeit | Bedeutung | melden, wenn |
|---|---|---|
| `reneg` | versteht `offer`/`answer` während des Gesprächs (Neuverhandlung, ICE-Restart) | der **eigene** Server `features` mit `reneg` liefert |
| `want` | beachtet `state.want` des Gegenübers (Bitrate senken) | immer (2.4-Client) |
| `bye` | versteht `bye` | immer (2.4-Client) |

- `caps` steht im Body von **jedem** `offer` und `answer` (auch im ersten). Fehlt es (2.1/2.3), gilt `[]`.
- Neuverhandlung und ICE-Restart nur, wenn **beide** Seiten `reneg` melden (eigene caps und die des Gegenübers).
- `state` mit `pip`/`want`/`path` wird **immer** gesendet, auch ohne `reneg` (ältere Clients ignorieren die Felder).
- `bye` nur an eine Gegenstelle mit `bye`. Unbekannte `t`-Typen und unbekannte Felder **ignorieren** (nicht abbrechen).

### Felder im Body

| Feld | Typ | in | Bedeutung |
|---|---|---|---|
| `t` | Text | alle | `offer` · `answer` · `ice` · `state` · `bye` (später `fkey`) |
| `pc` | Text | alle | Kennung der PeerConnection. **Der Anrufer legt sie fest** (je Zielgerät `min(dev_a, dev_b) + ":" + max(dev_a, dev_b)`, device_ids als Hex), der Angerufene **übernimmt** sie aus dem ersten Angebot. Signale mit fremdem `pc` ignorieren; fehlt `pc` (2.3), gilt die einzige Verbindung |
| `n` | Zahl | offer, answer | laufende Nummer **je Absender** (erstes Angebot des Anrufers `1`, das erste eigene Angebot des Angerufenen ebenfalls `1`). Die Antwort trägt das `n` des Angebots, das sie beantwortet |
| `caps` | Liste | offer, answer | siehe oben |
| `sdp`, `fp`, `media` | | offer, answer | wie bisher; `fp` = sortierte `a=fingerprint`-Zeilen der signierten SDP |
| `restart` | bool | offer | optional, nur Hinweis: Angebot ist ein ICE-Restart |
| `c` | Liste | ice | `RTCIceCandidateInit` (`candidate`, `sdpMid`, `sdpMLineIndex`, **`usernameFragment`**) |
| `mic`, `cam`, `screen` | bool | state | wie bisher |
| `pip` | bool | state | eigenes Fenster im Bild-im-Bild |
| `want` | Objekt | state | `{"video": "large" \| "small" \| "off"}` – wie groß ich das Bild des Gegenübers zeige |
| `path` | Text | state | eigener Weg: `direct` · `relay-udp` · `relay-tcp` (fehlt = unbekannt) |
| `reason` | Text | bye | z. B. `hangup` |

### Abläufe

**Anrufen/Annehmen** wie bisher, zusätzlich `pc`, `n: 1`, `caps` im Body und `caps` im JSON der Anfrage:

```json
{"t":"offer","pc":"2f67…568c:65fb…5b8f","n":1,"caps":["reneg","want","bye"],"media":"video","sdp":"v=0…","fp":["sha-256 4A:AD:…"]}
{"t":"answer","pc":"2f67…568c:65fb…5b8f","n":1,"caps":["reneg","want","bye"],"media":"video","sdp":"v=0…","fp":["sha-256 1C:…"]}
```

**Neuverhandlung („Perfect Negotiation“)**, nur wenn beide `reneg` melden. Höflich (*polite*) ist die Seite mit der
lexikographisch **größeren** device_id.

1. `negotiationneeded` (oder ICE-Restart) → eigenes `n` erhöhen, `offer` mit neuem `n` über `POST /api/calls/{id}/signal`.
2. Eingehendes `offer`: ist `n` ≤ dem zuletzt verarbeiteten `n` dieses Absenders → ignorieren. Kollision = ich erstelle
   gerade ein Angebot oder `signalingState != stable`. Unhöflich + Kollision → Angebot ignorieren. Sonst
   `setRemoteDescription(offer)` (höflich: implizites Rollback), `answer` mit **demselben** `n` senden.
3. Eingehendes `answer`: nur in `have-local-offer` und nur, wenn `n` = mein letztes Angebot; sonst ignorieren.
4. Der DTLS-Fingerabdruck (`fp`) eines neuen offer/answer muss dem des ersten entsprechen – sonst Sicherheitsfehler
   und auflegen (wie bei falscher Signatur).

**ICE-Restart** (Netzwechsel WLAN↔LTE, Verbindung weg), nur mit `reneg` auf beiden Seiten:

- Auslöser: `iceConnectionState` `disconnected` länger als **2,5 s**, sofort bei `failed`, Netzwechsel (iOS
  `NWPathMonitor`, Android `ConnectivityManager.NetworkCallback`, Web `online`/`navigator.connection` `change`).
- `restartIce()` bzw. `createOffer(iceRestart: true)` → `offer` wie oben (optional `restart: true`).
- Höchstens **3 Versuche** je Abbruch (zurückgesetzt, sobald wieder `connected`). Kommt die Verbindung nach `failed`
  nicht innerhalb von **20 s** wieder, Anruf wie bisher mit `end {reason: "failed"}` beenden.
- Ohne `reneg` beim Gegenüber: Verhalten wie 2.3 (bei `failed` auflegen).
- Kandidaten mit einem `usernameFragment`, das nicht zur aktuell gesetzten Remote-SDP (`a=ice-ufrag`) passt, **zurückhalten**,
  bis die passende SDP gesetzt ist; Kandidaten älterer Generationen verwerfen. Neue Kandidaten kommen oft vor der Antwort.

**Bild-im-Bild / Tab im Hintergrund:** Beim Wechsel ins PiP (bzw. App/Tab im Hintergrund mit sichtbarem Video)
`state` mit `pip: true, want: {video: "small"}` senden, beim Verlassen `want: {video: "large"}`. Empfängt ein Gerät
`want.video = "small"`, senkt es seinen **Kamera**-Sender nach der Tabelle unten; `off` = Kamera-Encoding
`active: false`. Bildschirmübertragung bleibt unverändert.

**Auflegen:** an eine Gegenstelle mit `bye` zuerst `{"t":"bye","reason":"hangup"}`, dann wie bisher `POST /end`.

### Bitrate je Weg (`RTCRtpSender.setParameters`)

Weg aus `getStats` alle 2 s: lokaler Kandidat `relay` mit `relayProtocol` `tcp`/`tls` → `relay-tcp`, `relay` sonst →
`relay-udp`; lokaler Nicht-Relay-Kandidat mit `protocol: tcp` (ICE-TCP) → wie `relay-tcp`; entfernter Kandidat `relay`
→ mindestens `relay-udp`; sonst `direct`. Es gilt der **schlechtere** Weg von eigenem und gemeldetem (`state.path`).

| Weg | Kamera | Bildschirm | Mesh je Strom (n ≥ 3, ab E5) | Audio |
|---|---|---|---|---|
| `direct` | `maxBitrate` 1 500 000, ≤ 720p | 2 500 000, `maxFramerate` 15 | 600 000, ≤ 540p | 32 000 |
| `relay-udp` | 1 200 000, ≤ 720p | 1 800 000, 15 fps | 500 000, ≤ 540p | 32 000 |
| `relay-tcp` | 800 000, ≤ 540p | 1 200 000, 8 fps, `degradationPreference: "maintain-resolution"` | 350 000, ≤ 360p, 15 fps | 24 000 |
| `want: small` | `min(…, 300 000)`, `scaleResolutionDownBy` = `max(2, min(3, Höhe/240))` | unverändert | 200 000 | – |

„≤ 720p“ heißt `scaleResolutionDownBy = Quellhöhe / 720` (nur wenn größer, sonst 1). Bildschirm immer
`degradationPreference: "maintain-resolution"`. Opus-FEC (`useinbandfec=1`) bleibt an. Parameter nur bei Änderung setzen.

### Beispiele

```json
{"t":"ice","pc":"2f67…:65fb…","c":[{"candidate":"candidate:1 1 udp 2122260223 192.0.2.10 54321 typ host","sdpMid":"0","sdpMLineIndex":0,"usernameFragment":"oNrl"}]}
{"t":"offer","pc":"2f67…:65fb…","n":2,"restart":true,"caps":["reneg","want","bye"],"media":"video","sdp":"…a=ice-ufrag:Zx9q…","fp":["sha-256 4A:AD:…"]}
{"t":"answer","pc":"2f67…:65fb…","n":2,"caps":["reneg","want","bye"],"media":"video","sdp":"…","fp":["sha-256 1C:…"]}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":false,"pip":true,"want":{"video":"small"},"path":"relay-tcp"}
{"t":"bye","pc":"2f67…:65fb…","reason":"hangup"}
```

Tests: `tests/anrufe_v3.py` (Server, Signalfluss, Grenzen), `tests/browser_calls3.js` (Web, gegen den 2.3-Client),
föderiert `tests/foederation_chat.py`.

## Gelesen-Bestätigungen und Online-Status (2.4)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Linux (Web: `static/chat.js`, Server: `saga/chat_status.py`).
„Wie WhatsApp“: **standardmäßig an**, je Konto getrennt abschaltbar, **gegenseitig** (wer etwas abschaltet, sendet es
nicht und sieht es bei anderen auch nicht). Nur neue Endpunkte und optionale Felder – ältere Apps merken nichts,
`MIN_APP` ändert sich nicht. Föderiert: `APP-API-FED.md`, Abschnitt 11.

### Erkennen, ob der Server es kann

`GET /api/chat/me` liefert zusätzlich (ältere Server nicht → Funktion ausblenden):

```json
{"privacy": {"receipts": true, "online": true}, "features": ["receipts", "online"],
 "status_limits": {"receipt_gap": 2.0, "presence_ttl": 90, "presence_users": 50, "last_round": 300}, "…": "…"}
```

Chats über Servergrenzen (`v: 2`) tragen in `/api/chats` und `/api/chats/{id}` zusätzlich `receipts_ok` und `online_ok`:
`false` = der andere Server (Saga 2.3) kann es nicht → keine Bestätigung versiegeln, keinen Online-Status anzeigen.
Lokale Chats haben die Felder nicht (= `true`).

### Einstellungen

- `GET /api/chat/privacy` → `{"receipts": true, "online": true}`
- `PUT /api/chat/privacy` mit `{"receipts": false}` und/oder `{"online": false}` → neuer Stand (400 bei anderen Typen).
  Live-Ereignis `chat.privacy {receipts, online}` an die eigenen anderen Geräte/Tabs.
- Texte (de): „Gelesen-Bestätigungen senden“ – „Aus: Andere sehen nicht, ob du ihre Nachrichten gelesen hast – und du
  siehst es bei ihnen auch nicht.“ · „Online-Status zeigen“ – „Aus: Deine Chat-Partner sehen nicht, ob und wann du
  zuletzt online warst – und du siehst es bei ihnen auch nicht.“ (alle fünf Sprachen in `saga/i18n/*.json`).
- `receipts: false`: Der Server zieht alle eigenen Bestätigungen zurück (auch über Servergrenzen), nimmt keine neuen an
  (`receipt: "off"`) und liefert keine fremden mehr (`enabled: false`). `online: false`: niemand sieht den eigenen
  Status, Abfragen liefern `enabled: false` und nichts.

### Format (Ende-zu-Ende)

Eine Bestätigung sagt „gelesen bis Nachricht `id` (Position `n`)“. `n` ist die Reihenfolge, die alle Teilnehmer gleich
sehen: in lokalen Chats `seq`, in Chats über Servergrenzen `fseq` (Reihenfolge des Heims).

```
body   = JSON-Text {"id": "<msg_id, 32 hex>", "n": <int>, "ts": <ms seit 1970>}     (genau dieser Text wird signiert)
sig    = sign_detached(LP("saga-chat/v1/receipt", str(chat_id), benutzername, geraet, body), device_sign_sk)    lokal
       = sign_detached(LP("saga-chat/v2/receipt", chat_ref, handle, geraet, body), device_sign_sk)             v2
inner  = JSON-Text {"v": 1, "dev": "<geraet>", "body": body, "sig": B64(sig)}
sealed = B64(crypto_box_seal(inner, backup_pub des Gegenübers))          je anderem Mitglied eines
```

Versiegelt wird für den **Backup-Schlüssel** jedes anderen Mitglieds (alle Geräte eines Kontos haben `backup_sk`) –
eine Bestätigung je Person, nicht je Gerät. Testvektoren (Pflicht in der CI jeder App): `tests/vectors/receipts.json`
(Erzeugen/Prüfen `tools/receipt_vectors.py`, Gegenprobe libsodium.js `tools/receipt_vectors.mjs`). Web:
`sealReceipt`, `openReceipt`, `verifyReceipt`, `receiptMsg` in `static/chat-crypto.js`; Python: `tests/chatlib.py`.

### Senden

Zusammen mit dem Lese-Zeiger: `POST /api/chats/{id}/read` mit `{"seq": <wie bisher>, "receipt": {"keys": {…}}}`.

- `keys`: lokal `"backup:<benutzername>"`, v2 `"backup:<handle>"` → `sealed`, für jedes andere Mitglied mit
  Chat-Schlüsseln. Schlüssel lokal aus `GET /api/chats/{id}/recipients` (`backup_pub`, vorher `backup_sig` prüfen wie
  beim Senden), v2 aus `GET /api/chat/v2/keys` (selbst geprüft; bei TOFU `reject`/`block` weglassen). Höchstens 50
  Einträge, je 112–2048 Zeichen; Unbekannte (keine Mitglieder, man selbst) ignoriert der Server.
- **Wann:** nur wenn der Chat wirklich sichtbar ist (App bzw. Tab im Vordergrund, Chat offen – wie beim Lese-Zeiger),
  die neueste Nachricht mit Position **nicht von dir** ist und ihr `n` größer als das zuletzt bestätigte.
  Höchstens **eine je Chat und 2 s** (`status_limits.receipt_gap`; nachziehen statt verwerfen). Nicht bei
  Nachrichtenanfragen (`request`), schreibgeschützten Chats, `receipts_ok: false` oder eigener Einstellung aus.
- Antwort wie bisher plus `receipt`: `"ok"` · `"off"` (eigene Einstellung aus) · `"none"` (niemand zu benachrichtigen,
  Anfrage noch offen, schreibgeschützt – nicht wiederholen) · `"rate"` (mehr als 120 je Minute – später erneut) ·
  `"bad"` (Form falsch). Ohne `receipt` im Body bleibt `/read` Byte für Byte wie bisher.

### Empfangen und anzeigen

- Beim Öffnen eines Chats: `GET /api/chats/{id}/receipts` →
  `{"chat_id": 7, "enabled": true, "receipts": [{"from": "bert", "sealed": "…"}]}` (v2 zusätzlich `"handle"`; `from` ist
  dann der Benutzername bzw. Prinzipal `@~x@host` wie in `members[].username`). Je anderem Mitglied nur die neueste.
  Eigene Einstellung aus → `enabled: false`, leer.
- Live: `chat.receipt {"chat_id": 7, "receipts": [{"from", "handle"?, "sealed"}]}` – je Chat gebündelt (höchstens ein
  Ereignis je 2 s mit allen neuen).
- Öffnen mit `backup_pk/backup_sk`, dann **prüfen**: Gerät `dev` muss ein geprüftes Gerät von `from` sein (lokal: Geräte
  aus `/recipients` mit gültigem Zertifikat; v2: Geräte aus den geprüften v2-Schlüsseln von `handle`), Signatur mit
  `chat_id`/`chat_ref` und Benutzername/Handle wie oben. Nicht prüfbar → ignorieren. Je Person nur das größte `n` merken.
- **Haken an eigenen Nachrichten:** `⏳` wird zugestellt (`fstate: "pending"`) → `✓` zugestellt (beim Server bzw. Heim
  angekommen) → `✓✓` gelesen (`n` der Bestätigung ≥ Position der Nachricht). Direkt-Chat: `✓✓`, sobald das Gegenüber
  bestätigt hat. Gruppen: `✓✓`, wenn alle anderen bestätigt haben, sonst `✓` bzw. `✓✓` mit Zähler; Tippen öffnet
  „Gelesen von“ (Name + Zeit aus `ts`) mit dem Hinweis „Wer Gelesen-Bestätigungen abgeschaltet hat, erscheint hier nicht.“
- Position eigener Nachrichten in v2-Chats: `fseq` aus der Antwort von `POST …/messages` bzw. – war sie ausstehend – aus
  dem Live-Ereignis `chat.message`, das die Zustellung meldet (enthält jetzt `fseq`).
- Der Server sieht nur, **dass** es eine Bestätigung gibt (und wann), nicht bis wohin. Der Lese-Zeiger `last_read`
  (Ungelesen-Zähler, Benachrichtigungen) bleibt wie bisher auf dem eigenen Server.

### Online-Status

- Nur für Personen mit einem gemeinsamen, angenommenen Chat. Abo: `POST /api/chat/presence` mit
  `{"users": ["bert", "@~abcdefghijklmnop@b.example"]}` (Benutzernamen bzw. Prinzipale aus `members[].username` /
  `peer.username`, höchstens 50, höchstens 30 Abfragen je Minute) →

  ```json
  {"enabled": true, "ttl": 90, "users": {"bert": {"online": true, "last": null},
                                          "@~abcdefghijklmnop@b.example": {"online": false, "last": 1792449900.0}}}
  ```

  `last` = „zuletzt online“ (Unix-Sekunden, auf 5 Minuten abgerundet, `null` = unbekannt). Fehlt eine Person: kein
  Status (kein gemeinsamer Chat, abgeschaltet oder noch unbekannt) – nichts anzeigen.
- Nur abonnieren, was **sichtbar** ist (offener Direkt-Chat, sichtbare Direkt-Chats der Liste), alle **60 s** erneuern,
  solange sichtbar; beim Verlassen `{"users": [...], "leave": true}` (optional – das Abo läuft nach `ttl` aus).
- Live: `chat.presence {"username": "bert", "online": true|false, "last": <zahl>|null}` nur an Konten mit laufendem Abo.
  `online: false` mit `last: null` = Status nicht mehr verfügbar (Person hat ihn abgeschaltet) → entfernen.
- Personen anderer Server: Der eigene Server fragt beim Server der Person nach; die erste Antwort kann leer sein (z. B.
  gleich nach dem Annehmen einer Anfrage) – dann eine Minute lang alle 10 s erneut fragen, danach im 60-s-Takt.
- Online = mindestens eine Live-Verbindung (`/api/events`), offline 30 s nach der letzten (Server-Einstellung
  `CHAT_OFFLINE_GRACE`). Apps halten die Live-Verbindung wie bisher nur im Vordergrund. Anzeige: „online“ bzw.
  „zuletzt online vor 5 Minuten“ (relativ, gerundet).
- Kein Verlauf: Der Server speichert je Konto nur „zuletzt online“ im 5-Minuten-Raster, nirgends für Admins sichtbar.

### Beispiele

```http
POST /api/chats/7/read
{"seq": 812, "receipt": {"keys": {"backup:bert": "u3J…", "backup:cora": "kQ9…"}}}
→ 200 {"ok": true, "last_read": 812, "unread": 0, "receipt": "ok"}

GET /api/chats/7/receipts
→ 200 {"chat_id": 7, "enabled": true, "receipts": [{"from": "bert", "sealed": "pT1…"}]}

PUT /api/chat/privacy
{"online": false}
→ 200 {"receipts": true, "online": false}
```
