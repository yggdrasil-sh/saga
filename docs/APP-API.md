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

## Videos im Chat: saga-file/v2 (2.4, Etappe E2)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android, Linux und Web (Referenz: `static/chat-video.js`, `static/sw.js`,
Server `saga/chat_files.py`, Format `saga/file_v2.py`). Videos bis `limits.file_max` (`ATTACH_MAX_MB`, Standard 10 MB)
bleiben im bisherigen Format v1 (ein AEAD-Block, `POST /api/chats/{id}/files`) und funktionieren in allen Apps wie bisher.
Größere Videos gehen **nur** im neuen Format `saga-file/v2` (blockweise, wahlfreier Zugriff, fortsetzbarer Upload).
Ältere Apps zeigen sie als Anhang mit Namen; Öffnen schlägt dort fehl („Mit Saga 2.4 öffnen“). `MIN_APP` ändert sich
nicht – alles Neue wird über `limits.file2` ausgehandelt.

### Fähigkeiten (`GET /api/chat/me`)

```json
"limits": {"file_max": 10485760, "file2": true, "video_max": 524288000, "upload_part": 8388608, "file2_cs": 262144,
           "uploads_max": 2, "quota": 4294967296, "used": 123456789, "…": "…"}
```

- `file2: true` = der Server kann saga-file/v2 (Upload in Teilen, Range, Medien-Token). Fehlt das Feld (Server 2.3):
  nur v1 bis `file_max` anbieten.
- `video_max` = größte **Klartext**-Größe eines v2-Videos (Admin: Einstellungen → Chats, sonst `VIDEO_MAX_MB`, Standard 500).
- `upload_part` = höchstens so viele Bytes je `PUT`. `file2_cs` = Blockgröße (in 2.4 immer 262144).
- `quota`/`used` = Chat-Speicher des Kontos in Bytes (Standard 4096 MB, Admin änderbar); laufende Uploads zählen mit
  ihrer vollen Größe. Anzeigen als „Chat-Speicher: 1,2 von 4 GB“.
- Chats über Servergrenzen: Chat-Objekt `big_files` (`true`, wenn alle beteiligten Server `chatfile2` können). Bei
  `false` (Gegenüber hat Saga 2.3) nur Videos bis `file_max` anbieten, sonst Hinweis „Der Server des Gegenübers kann
  große Videos noch nicht“. Der Server prüft es ohnehin (`409` beim Anlegen des Uploads und beim Senden).

### Absender: Poster, Metadaten, „optimiert 720p“

- **Poster** erzeugt das Absender-Gerät: Bild bei 1 s (bzw. 10 % der Dauer), längste Seite 320 px, JPEG, **≤ 30 KB**.
  Es geht wie ein Vorschaubild als **v1-Anhang** mit eigenem Schlüssel hoch (`th`, wie bei Bildern seit 2.1) – auch
  ältere Apps können es so zeigen.
- **Standard auf dem Handy: „optimiert 720p“** (H.264/HEVC, lange Seite 1280, kurze 720, ~2,5 Mbit/s, AAC 128 kbit/s,
  `moov` vorn/faststart), „Original“ wählbar. iOS `AVAssetExportSession` (`shouldOptimizeForNetworkUse = true`), Android
  Media3 `Transformer`. Web: nur wo der Browser MP4 aufnehmen kann (MediaRecorder), sonst Original mit Hinweis. Höchstens
  ein Export gleichzeitig, bei `thermalState >= .serious` pausieren.
- Ist das (optimierte) Video größer als `file_max`: saga-file/v2. Größer als `video_max`: nicht senden (Hinweis).

Nachrichteninhalt (verschlüsselt, `body.files[]`; neue Felder, ältere Apps ignorieren sie):

```json
{"id": "Q1w2E3r4T5y6U7i8O9p0AsDf", "k": "<B64 32 Bytes K>", "n": "Urlaub.mp4", "m": "video/mp4", "s": 187654321,
 "x": 2, "cs": 262144, "dur": 83.4, "w": 1280, "h": 720, "rot": 0, "fs": true,
 "th": {"id": "Zx9…", "k": "<B64>", "m": "image/jpeg", "w": 320, "h": 180, "s": 21877}}
```

| Feld | Bedeutung |
|---|---|
| `x` | Format: fehlt oder `1` = v1 (ein Block, wie bisher); `2` = saga-file/v2. Größere Werte: „Mit einer neueren Saga-Version öffnen“ |
| `s` | Größe des **Klartexts** (Bytes). Blob-Größe = `48 + s + 16 · N`, `N = max(1, ⌈s / cs⌉)` |
| `cs` | Blockgröße (nur bei `x: 2`; 262144) |
| `dur`, `w`, `h`, `rot` | Dauer in Sekunden (eine Nachkommastelle), Abmessungen des Videos, Drehung (0/90/180/270) |
| `fs` | Hinweis: `moov` liegt vorn (faststart). Nur fürs Vorladen; `false` ist kein Fehler (ein Roundtrip mehr) |
| `th` | Poster wie Vorschaubilder (v1-Anhang, eigener Schlüssel) |

In der Chatliste **nur das Poster** (lazy, in Anzeigegröße dekodiert) mit Dauer und Abspiel-Symbol – **nie** ein Player
je Zelle. Der Player entsteht erst beim Antippen (ein gemeinsamer Player).

### Format saga-file/v2

```
Blob    = Kopf(48 B) || Block_0 || … || Block_{N-1}
Kopf    = "SGF2" || u8 2 || u8 0 || u16 0 || u32 cs || u32 0 || u64 s || prefix(16 B, zufällig) || 8 × 0x00   (big-endian)
Block_i = crypto_aead_xchacha20poly1305_ietf_encrypt(plain[i·cs .. (i+1)·cs), AD_i, nonce_i, K)        (Länge ≤ cs + 16)
nonce_i = prefix(16 B) || u64_be(i)
AD_i    = LP("saga-file/v2", u64_be(i), u64_be(N), u32_be(cs), u64_be(s), u8(i == N-1 ? 1 : 0))
LP      = je Feld u32_be(Länge) || Bytes (wie saga-chat/v1)
```

- `K` (32 B) neu je Datei, nur in `files[].k`. Eine leere Datei hat einen Block (nur das Tag, 16 B).
- Klartext-Bereich `a..b` → Blöcke `i = a / cs` bis `j = b / cs` → Ciphertext-Bytes `48 + i·(cs+16)` bis
  `min(48 + (j+1)·(cs+16), Blob-Größe) − 1`. Entschlüsseln je Block, dann zuschneiden.
- Jeder Block prüft `i`, `N`, `cs`, `s` und die Endmarke: vertauschte, fehlende oder angehängte Blöcke und ein falscher
  Kopf schlagen fehl. **Nie** Klartext eines Blocks ausgeben, dessen Tag nicht stimmt.
- Testvektoren (CI-Pflicht in jeder App): `tests/vectors/file_v2.json`. Klartext je Fall `plain[k] = k mod 251`; geprüft
  werden Kopf, AD/Nonce des ersten und letzten Blocks, Blockanfänge/-enden und SHA-256 des ganzen Blobs, dazu
  `reject` (muss scheitern) und `ranges` (Bereich → Blöcke). Gegenproben: `tools/file_vectors_v2.py`, `.mjs`.

### Upload (fortsetzbar)

1. Anlegen – liefert die Upload-Kennung (= spätere Datei-Kennung):

   ```
   POST /api/chats/{cid}/uploads        {"size": <Blob-Größe>, "fmt": 2}
   200 {"upload": "Q1w2…", "offset": 0, "size": 187700000, "part_max": 8388608, "expires": 1760999999.0,
        "url": "/api/chat/uploads/Q1w2…"}
   ```

   `400` Größe passt zu keinem v2-Blob · `409` Server des Gegenübers ohne chatfile2 · `413` größer als `video_max` oder
   Chat-Speicher voll (Text in `error`) · `429` mehr als `uploads_max` gleichzeitig bzw. zu schnell.
2. Teile senden, **nur fortlaufend** (Kopf und Blöcke wie oben, Teilgrenzen beliebig, höchstens `part_max`):

   ```
   PUT /api/chat/uploads/{id}
   Content-Range: bytes 0-8388607/187700000
   Content-Type: application/octet-stream          (+ Anmeldung wie immer, X-Saga: 1)
   200 {"offset": 8388608, "done": false}          Upload-Offset: 8388608
   …
   200 {"id": "Q1w2…", "size": 187700000, "fmt": 2, "offset": 187700000, "done": true}     (letzter Teil)
   ```

   `409 {"error": "offset", "offset": n}` falsche Stelle (dann ab `n` weiter) bzw. `"busy"` (ein anderer PUT läuft) ·
   `400` Content-Range/Content-Length passen nicht, unvollständiger Teil, beim Abschluss falscher Kopf (der Upload ist
   dann verworfen) · `413` Teil zu groß · `404` Upload unbekannt, abgelaufen (24 h) oder abgebrochen.
3. **Fortsetzen** nach Netzabbruch oder App-Neustart: `HEAD /api/chat/uploads/{id}` → `204` mit `Upload-Offset` und
   `Upload-Length`; ab `Upload-Offset` weiter (auch mitten im Block – die Verschlüsselung ist deterministisch, die App
   erzeugt die Bytes ab jeder Stelle neu). Ist der Upload schon fertig (Antwort verpasst): `204` mit
   `Upload-Done: 1` und `Upload-Offset = Upload-Length` – Datei-Kennung = Upload-Kennung.
   Dafür merkt sich die App **K, prefix, Datei-Pfad und Upload-Kennung** im geschützten Speicher (Schlüsselbund,
   EncryptedSharedPreferences, libsecret) bis zum Senden der Nachricht; danach löschen.
4. Abbrechen: `DELETE /api/chat/uploads/{id}`. Danach wie bisher `POST /api/chats/{cid}/messages` mit
   `files: [id, th.id]`.

Im Container nimmt saga-media die Teile an und schreibt sie direkt auf die Platte (keine Bytes durch Python); ohne
saga-media antwortet Python mit genau denselben Codes.

### Abruf mit Range und Medien-Token

- `GET /api/chat/files/{fid}` mit `Range: bytes=a-b` → `206` mit `Content-Range`, `Accept-Ranges: bytes`, `ETag: "<fid>"`
  (unveränderlich), `If-Range` wird beachtet; `bytes=-n` (Ende) geht, mehrere Bereiche nicht (dann nur der erste bzw.
  `multipart/byteranges` ohne saga-media – nicht verwenden). Hinter dem Ende → `416`. Die App fordert nur die Blöcke an,
  die der Player braucht (Kopf einmal: `bytes=0-47`).
- **Kürzere Antwort möglich**: Liegt der Anhang bei einem anderen Server und fehlt der Bereich noch, holt der eigene
  Server ihn erst; ohne saga-media liefert er dann höchstens 8 MiB je Antwort (`Content-Range` beachten, Rest neu anfordern).
- **Medien-Token** für lange Wiedergaben (die Berechtigung hängt so nicht an einem wechselnden Cookie):

  ```
  POST /api/chat/files/{fid}/token          (Anmeldung wie immer, X-Saga: 1)
  200 {"token": "m1.eyJ…", "expires": 1761000900, "ttl": 900}

  GET /api/chat/files/{fid}
  Range: bytes=48-1310847
  X-Saga-Media-Token: m1.eyJ…                (kein Cookie, kein X-Saga nötig)
  ```

  Gilt nur für diese Datei und diese Anmeldung (Konto, Passwort-Stand, Gerät). Bei `401` ein neues Token holen. Jede
  Abfrage prüft Konto und Mitgliedschaft neu; saga-media merkt sich eine Erlaubnis höchstens 60 s – Entzug (Nachricht
  gelöscht, Mitglied entfernt, Passwort geändert, Gerät abgemeldet) wirkt also spätestens nach 60 s.

### Wiedergabe je Plattform

Gemeinsam: Ciphertext blockweise holen (Range, mit Token), Block entschlüsseln, Klartext an den Player – **kein**
Komplett-Download, Klartext nie auf die Platte (außer beim ausdrücklichen Speichern). Ciphertext-Cache je App
(LRU, Standard 512 MB, getrennt vom Bildcache). Mobilfunk: kein Autoplay, nur Poster vorab.

**iOS/macOS** – `AVURLAsset` mit eigenem Schema und `AVAssetResourceLoaderDelegate`:

```swift
let asset = AVURLAsset(url: URL(string: "saga-media://\(file.id)")!)
asset.resourceLoader.setDelegate(loader, queue: loaderQueue)          // ein gemeinsamer AVPlayer
// loader: resourceLoader(_:shouldWaitForLoadingOfRequestedResource:) -> true
if let info = req.contentInformationRequest {
    info.contentType = UTType(mimeType: file.m)?.identifier ?? UTType.mpeg4Movie.identifier
    info.contentLength = Int64(file.s)                                 // Klartext-Größe
    info.isByteRangeAccessSupported = true
}
if let d = req.dataRequest {
    let a = d.currentOffset, b = d.requestsAllDataToEndOfResource ? file.s - 1 : d.requestedOffset + Int64(d.requestedLength) - 1
    // Blöcke a/cs ... min(b, a + 4 MiB)/cs: Ciphertext-Range holen (URLSession, X-Saga-Media-Token), je Block
    // crypto_aead_xchacha20poly1305_ietf_decrypt(AD_i, nonce_i), zuschneiden -> d.respond(with:), dann req.finishLoading()
}
```

`preferredForwardBufferDuration` 20 s (Mobilfunk 10 s). Speichern: blockweise in eine Temp-Datei
(`NSFileProtectionComplete`), dann `PHAssetCreationRequest` (nur `addOnly`) bzw. „In Dateien sichern“, Temp-Datei sofort
löschen. macOS: `NSSavePanel` als Strom.

**Android** – Media3 ExoPlayer mit eigenem `DataSource` über einem Ciphertext-`CacheDataSource`:

```kotlin
class SagaFileDataSource(private val f: FileMeta, private val ct: DataSource /* CacheDataSource -> OkHttp mit Token */) : BaseDataSource(true) {
    override fun open(spec: DataSpec): Long {            // spec.position/length = Klartext
        pos = spec.position; end = if (spec.length == C.LENGTH_UNSET.toLong()) f.s - 1 else pos + spec.length - 1
        head = readCipher(0, 47); return end - pos + 1   // Kopf einmal, pruefen (SGF2, cs, s)
    }
    override fun read(buf: ByteArray, off: Int, len: Int): Int {
        if (pos > end) return C.RESULT_END_OF_INPUT
        val i = pos / f.cs; val block = blockCache.getOrPut(i) { decryptBlock(i, readCipher(48 + i * (f.cs + 16), …)) }
        val n = minOf(len.toLong(), block.size - (pos - i * f.cs), end - pos + 1).toInt()
        System.arraycopy(block, (pos - i * f.cs).toInt(), buf, off, n); pos += n; bytesTransferred(n); return n
    }
}
```

`DefaultLoadControl` 15–30 s, eine Player-Instanz. Speichern: `MediaStore.Video` mit `IS_PENDING = 1`, entschlüsselt als
Strom schreiben, danach `IS_PENDING = 0` (ab Android 10 ohne Berechtigung).

**Linux (GTK 4)** – `Gtk.MediaFile.new_for_input_stream()` mit eigenem `Gio.InputStream` + `Gio.Seekable` (PyNaCl je
Block):

```python
class SagaStream(Gio.InputStream, Gio.Seekable):
    def do_read_fn(self, buf, cancellable):             # buf: Puffer, liest ab self.pos
        i = self.pos // cs; blk = self.block(i)          # Ciphertext 48 + i*(cs+16) .. holen (httpx, Token), entschluesseln
        n = min(len(buf), len(blk) - (self.pos - i * cs)); buf[:n] = blk[self.pos - i * cs:][:n]; self.pos += n; return n
    def do_seek(self, offset, type_, cancellable): …  # SEEK_SET/CUR/END auf Klartext-Positionen
    def do_can_seek(self): return True
```

Fallback ohne GL (siehe Klingelton in `calls.py`): GStreamer `appsrc` mit `stream-type=random-access` (Signal
`seek-data`), sonst „Im Standard-Player öffnen“ über eine private Temp-Datei (0600, beim Beenden gelöscht, mit Hinweis).
Speichern: `Gtk.FileDialog` und Entschlüsselung als Strom.

**Web** – Service Worker (`static/sw.js`): Seite meldet die Wiedergabe mit `postMessage({op:'media', id, fid, key, size,
cs, m, token})` an, `<video src="/_m/<id>">`; der Service Worker beantwortet Range-Anfragen mit 206 und entschlüsselt
blockweise (Strom). Ohne Service Worker: bis 100 MB als Blob, sonst nur „Herunterladen“. Speichern: `showSaveFilePicker`
als Strom, sonst Download-Strom über den Service Worker (`/_m/<id>?dl=1`), zuletzt Blob (≤ 500 MB).

### Grenzen und Antworten (Übersicht)

| Was | Grenze | Antwort |
|---|---|---|
| Video (Klartext) | `video_max` (Standard 500 MB) | `413` beim Anlegen |
| Chat-Speicher je Konto | `quota` (Standard 4096 MB, inkl. laufender Uploads) | `413` |
| gleichzeitige Uploads | `uploads_max` (2) | `429` |
| Teil je PUT | `upload_part` (8 MiB) | `413` |
| Upload offen | 24 h, danach entfernt | `404` |
| Föderation mit Saga 2.3 | nur bis `file_max` | `409` (Chat-Objekt `big_files: false`) |
| Medien-Token | 15 min, Entzug ≤ 60 s | `401` → neues Token |
