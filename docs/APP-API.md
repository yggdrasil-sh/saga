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
{"enabled": true, "format": "saga-call/v1", "body_v": 3, "features": ["reneg", "pip_hint", "screen2", "multi", "group"],
 "limits": {"endpoints_video": 4, "endpoints_audio": 6, "signals_max": 600, "signals_per_min": 60,
            "signals_hard": 6000, "sig_rate": 240, "sealed_max": 24576, "caps_max": 16, "ring_silent": 8, "handover_s": 15}, "…": "…"}
```

- `features`: `reneg` = der Server reicht offer/answer während des Gesprächs weiter (Budget unten), `pip_hint` = `want`
  wird unterstützt, `screen2` = Bildschirm als zweite Videospur (E3, Abschnitt „Bildschirm v2“), `multi` = weitere Geräte
  im Anruf (E4), `group` = Gruppenanrufe (E5) – beides im Abschnitt „Anrufe: Mehrgeräte und Gruppen“.
- `limits.endpoints_*`: Geräte je Anruf (Mesh: 4 mit Video, 6 nur Ton; eigene Zusatzgeräte zählen mit; der Admin kann
  nur senken). E0-Server meldeten 2.
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
| `screen2` | sendet und empfängt den Bildschirm als eigene Videospur (Betriebsart B) | der eigene Server `features` mit `reneg` **und** `screen2` liefert und der Client B kann |
| `multi` | Raum: mehrere PeerConnections je Anruf, `join`/`leave`, Teilnehmerliste (E4/E5) | der eigene Server `features` mit `reneg` **und** `multi` liefert und der Client den Raum kann |

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

## Anrufe: Bildschirm v2 (2.4, Etappe E3)

Baut auf saga-call v3 auf (Neuverhandlung oben). Server: nur `features: "screen2"`, keine neuen Endpunkte; der Body
bleibt signiert und für den Server undurchsichtig. Ein Angebot mit drei m-Zeilen (Ton, Kamera, Bildschirm) ist in Chrome
≈ 6–10 KB SDP und passt in `sealed_max` (24 KB). `MIN_APP` ändert sich nicht.

### Zwei Betriebsarten

| | wann | wie |
|---|---|---|
| **B** – eigene Spur | **beide** Seiten melden `reneg` **und** `screen2` in den signierten `caps`, und die erste Aushandlung ist fertig | zusätzlicher Video-Transceiver (bzw. Wiederverwendung, siehe unten) über die Neuverhandlung; die Kamera läuft auf ihrer m-Zeile weiter (gedrosselt) |
| **A** – wie 2.1/2.3 | sonst (2.1/2.3, E0-Clients ohne `screen2`, noch nicht verbunden) | `replaceTrack(screen)` auf dem Kamera-Sender, Kamera pausiert. Kein Angebot, keine neue m-Zeile |

Ein Client, der B nicht kann, meldet `screen2` nicht – er bekommt dann immer A. Empfangen muss **jeder** 2.4-Client mit
`screen2` beides können.

### Sender (Betriebsart B)

1. Bildschirm-Spur mit `contentHint = "detail"` (iOS/Android: entsprechend „Text“/Detail-Encoder-Einstellung).
2. **m-Zeile finden oder anlegen:** gibt es schon einen Video-Transceiver mit Rolle `screen` – eigener aus einem früheren
   Teilen **oder** einer, den das Gegenüber angelegt hat (seine `tracks` nennen diese mid `screen`) – diesen
   wiederverwenden: `sender.replaceTrack(screen)`, `sender.setStreams(bildschirm-stream)`, `direction = "sendrecv"`.
   Sonst `addTransceiver(screen, {direction: "sendrecv", streams: [bildschirm-stream]})`. Es gibt höchstens **eine**
   Bildschirm-m-Zeile je Verbindung (beide Seiten teilen sie, auch gleichzeitig) – das SDP wächst nicht bei jedem Teilen.
3. `negotiationneeded` → `offer` wie bei saga-call v3 (`n` + 1) mit `tracks`/`msid` (unten).
4. **Nach** dem Angebot `state` mit `screen: true`, `screen_mid`, `tracks`, `msid` senden (die mid steht erst nach
   `setLocalDescription` fest). Ergebnis: höchstens 4 Signale je Start (offer, answer, state).
5. **Ende** (Knopf, System-„Teilen beenden“, Spur `ended`): `replaceTrack(null)`, `direction = "recvonly"` (nicht
   `stopped`, nicht entfernen) → Neuverhandlung → teilt das Gegenüber nicht, ist die m-Zeile danach `inactive`.
   Dann `state` mit `screen: false` (ohne `screen_mid`). Beim Auflegen, bei Übergabe auf ein anderes Gerät und bei
   Verlust der PeerConnection endet das Teilen automatisch.
6. Systemton (wo die Plattform ihn liefert, Web: Chromium-Tab/Systemton) wird **in die vorhandene Tonspur gemischt**
   (Mikrofon + Quelle); Stumm schaltet nur das Mikrofon. Keine eigene Ton-m-Zeile. `state.screen_audio: true` als Hinweis.

Betriebsart A sendet `state` mit `screen: true` wie bisher; `screen_mid` ist dann die mid der **Kamera**-m-Zeile (dort läuft
der Bildschirm) – ältere Clients kennen das Feld nicht und zeigen wie bisher die einzige Videospur.

### Neue Felder im Body

| Feld | Typ | in | Bedeutung |
|---|---|---|---|
| `tracks` | Objekt | offer, answer, state | **Rolle jeder eigenen m-Zeile**: `{"<mid>": "mic" \| "cam" \| "screen"}`. Fest je m-Zeile (die Kamera-m-Zeile bleibt `cam`, auch wenn in A gerade der Bildschirm darauf läuft). Nur senden, wer `screen2` meldet. Höchstens 8 Einträge, mid `[A-Za-z0-9_-]{1,16}`, Unbekanntes ignorieren |
| `msid` | Objekt | offer, answer, state | `{"<mid>": "<Stream-ID>"}` = Stream-ID aus `a=msid:<Stream-ID> <Spur-ID>` der SDP. Ton und Kamera teilen einen Stream, der Bildschirm hat einen eigenen. Nur zur Kontrolle/Anzeige; maßgeblich ist die mid |
| `screen_mid` | Text | state | nur bei `screen: true`: mid, auf der **jetzt** der Bildschirm läuft (B: Bildschirm-m-Zeile, A: Kamera-m-Zeile) |
| `screen_audio` | bool | state | nur bei `screen: true`: Ton der Quelle ist in die Tonspur gemischt |

### Empfänger: Kamera und Bildschirm unterscheiden

Die **mid** ist auf beiden Seiten dieselbe (sie gehört zur m-Zeile) und steht am Transceiver (`ontrack`:
`event.transceiver.mid`; iOS `RTCRtpTransceiver.mid`, Android `RtpTransceiver.getMid()`).

1. `tracks` aus jedem offer/answer/state des Gegenübers merken (letzter Stand gilt).
2. `state.screen == true` **und** `screen_mid` vorhanden → die Spur auf dieser mid ist der Bildschirm. Ist es nicht die
   `cam`-mid und meldet `state.cam == true`, ist die Spur der `cam`-mid die Kamera.
3. `state.screen == true` **ohne** `screen_mid` (2.1/2.3/E0-Gegenstelle) → die einzige Videospur ist der Bildschirm.
4. Ohne `tracks` (ältere Gegenstelle): die **erste** Video-m-Zeile ist die Kamera.
5. Anzeige: Bildschirm groß (`object-fit: contain`, nicht beschneiden), Kamera klein; Tippen/„Ansicht tauschen“ tauscht.
   Sichtbare Anzeige „<Name> teilt den Bildschirm“. Neues Teilen beginnt wieder mit dem Bildschirm groß.
6. Eine Spur ohne eintreffende Bilder (`muted`) nicht groß zeigen (z. B. kurz nach dem Angebot).

### Bitrate (wie Tabelle oben)

- Bildschirm-Sender: je Weg `maxBitrate` 2 500 000 / 1 800 000 / 1 200 000, `maxFramerate` 15 / 15 / 8,
  `scaleResolutionDownBy` 1, immer `degradationPreference: "maintain-resolution"`. `want: small` ändert nichts.
  Aufnahme höchstens 1080p (lange Kante 1920) und 15 fps.
- **Kamera während des Teilens (nur B):** `maxBitrate` = min(Regel, 300 000), `scaleResolutionDownBy` ≥ Quellhöhe/360;
  über `relay-tcp` Kamera-Encoding `active: false` (Hinweis „Kamera pausiert (langsame Verbindung)“ beim Teilenden).
  Summe je Richtung über TCP ≤ 1,6 Mbit/s. Nach dem Ende gelten wieder die normalen Kamera-Regeln.
- Betriebsart A: der Kamera-Sender bekommt solange die Bildschirm-Regeln.

### Datenschutz (alle Plattformen)

- Vor dem **ersten** Teilen ein Hinweis: alles in der Auswahl ist sichtbar, auch Benachrichtigungen, Passwörter und andere
  Fenster/Chats; nichts wird aufgezeichnet oder auf dem Server gespeichert. Kästchen „Nicht mehr anzeigen“ (je Gerät).
- Während des Teilens dauerhaft sichtbar: „Du teilst deinen Bildschirm“ mit „Beenden“ (Web: rote Leiste; Apps: Leiste
  bzw. Systemanzeige). Keine Aufnahme, keine Fernsteuerung; Medien nur DTLS-SRTP zwischen den Geräten.

### Beispiele

Anrufer (Kamera mid `1`) teilt, Neuverhandlung n = 3; der Angerufene antwortet, dann Zustand:

```json
{"t":"offer","pc":"2f67…:65fb…","n":3,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…m=video 9 UDP/TLS/RTP/SAVPF 96 …\r\na=mid:2\r\na=sendrecv\r\na=msid:ec27dfd5… 9b1f…","fp":["sha-256 4A:AD:…"],
 "tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…","2":"ec27dfd5…"}}
{"t":"answer","pc":"2f67…:65fb…","n":3,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…a=mid:2\r\na=recvonly…","fp":["sha-256 1C:…"],
 "tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"452e5d05…","1":"452e5d05…"}}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":true,"screen_mid":"2","screen_audio":true,"pip":false,
 "want":{"video":"large"},"path":"direct","tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…","2":"ec27dfd5…"}}
```

Ende (Richtung `recvonly`, n = 4, danach ist die m-Zeile `inactive`):

```json
{"t":"offer","pc":"2f67…:65fb…","n":4,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…a=mid:2\r\na=recvonly…","fp":["sha-256 4A:AD:…"],"tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":false,"pip":false,"want":{"video":"large"},"path":"direct","tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
```

Betriebsart A (Gegenüber ohne `screen2`) – kein Angebot, nur der Zustand:

```json
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":false,"screen":true,"screen_mid":"1","pip":false,"want":{"video":"large"},"tracks":{"0":"mic","1":"cam"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
```

Tests: `tests/browser_screen2.js` (Web: B zwischen 2.4-Clients, A gegen E0- und 2.3-Client, Messung),
`tests/anrufe_v3.py` (Server reicht großes Angebot und die neuen Felder unverändert weiter).

## Anrufe: Mehrgeräte und Gruppen (2.4, Etappen E4/E5)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Web (`static/calls.js`). Ein Anruf ist ein **Raum** mit
Endpunkten je **Gerät**; ein 1:1-Anruf ist der Raum mit zwei Endpunkten und läuft genau wie bisher. Dazu kommen weitere
eigene Geräte (Begleitgerät, Übernahme) und Gruppenanrufe in Gruppen-, freien und Seiten-Chats. Medien laufen als
**Mesh**: je Gerätepaar eine PeerConnection (`pc` = sortiertes Paar der device_ids), DTLS-SRTP je Paar, Fingerabdrücke in
der signierten SDP – E2E wie bisher, der Server sieht keine SDP. Signatur und Versiegelung bleiben `saga-call/v1` bzw.
`saga-call/v2` (föderiert). Bestehende Endpunkte brechen nicht; `MIN_APP` ändert sich nicht.

### Voraussetzungen und Fähigkeiten

- Server: `GET /api/calls/config` → `features` enthält `multi` und `group`; `limits.endpoints_video` (4),
  `limits.endpoints_audio` (6), `ring_silent` (8), `handover_s` (15). Fehlt `multi`, gibt es keine Raumfunktionen.
- Gerät: `multi` in den signierten `caps` (offer/answer) und als Hinweis in den JSON-Anfragen. Nur melden, wenn der eigene
  Server `reneg` **und** `multi` liefert.
- **Fähigkeiten melden:** `PUT /api/calls/caps` `{"device": "<id>", "caps": ["reneg","want","bye","screen2","multi"]}` →
  `{"ok": true}` – beim Start bzw. sobald die Chat-Schlüssel bereit sind (und nach einem Update). Zu Gruppenanrufen
  eingeladen wird nur, wer ein Gerät mit `multi` hat; die anderen sehen nur den Chat-Eintrag („braucht Saga 2.4“).
- Beitreten geht nur, wenn **alle** Endpunkte `multi` melden (eine 2.1/2.3/E0-Gegenstelle bleibt 1:1) und – föderiert –
  alle beteiligten Server `calls3` melden. Sonst `409` mit `join_block: "old"`.
- Grenze: Endpunkte = Geräte, eigene Zusatzgeräte zählen mit. Video 4, nur Ton 6 → `409` mit `join_block: "full"`.

### Anruf-Objekt (neu)

`POST /api/calls`, `GET /api/calls/{id}`, `GET /api/calls/active`, `POST …/join` liefern zusätzlich:

```json
{"id":"…","kind":"dm","media":"video","state":"active","limit":4,"joinable":true,"join_block":null,
 "mine":["5cbb…"],"rv":1792458591.204,
 "legs":[{"who":"anna","name":"Anna","device":"0e32…","dev_name":"iPhone","role":"member","state":"active",
          "caps":["reneg","want","bye","screen2","multi"],"joined":1792458574.1,"me":false},
         {"who":"bert","name":"Bert","device":"5cbb…","dev_name":"MacBook","role":"companion","state":"active",
          "caps":["…"],"joined":1792458590.7,"me":true}]}
```

- `kind`: `dm` | `group`. `legs`: aktive Endpunkte in Beitrittsreihenfolge; föderiert zusätzlich `handle` (bei fremden
  Personen auch `host`). `role`: `member` · `companion` (Begleitgerät) · `handover` (übernimmt, mit `replaces`).
- `mine`: eigene Geräte im Anruf. `joinable`/`join_block`: kann ein (weiteres) Gerät beitreten – `old` (jemand braucht
  Saga 2.4), `full`, `ended`, `none` (1:1 noch nicht angenommen).
- `rv`: Stand der Teilnehmerliste (Zahl, streng steigend; auch in `call.roster`). Eine Liste (`legs`) aus Antwort oder
  Ereignis nur übernehmen, wenn ihr `rv` nicht kleiner ist als das der zuletzt übernommenen – Antworten und Ereignisse
  können sich überholen. Fehlt `rv` (ältere 2.4-Testserver), wie bisher immer übernehmen.
- Gruppe: `role: "member"`, `from` (wer begonnen hat), `invite` (eigene Einladung: `ringing` · `declined` · `joined` ·
  `old` · `missed`), `old` (Personen ohne 2.4-Gerät, `[{who, name}]`), `members` (Mitglieder des Chats) und `silent`
  (mehr als `ring_silent` Mitglieder) wie in `call.invite` – auch ein aus `GET /api/calls/active` nachgeholtes Klingeln
  ist bei `silent: true` still (nur „Anruf läuft – beitreten“).
- `GET /api/chats/{id}` → `chat.call`: `{"group": true|false, "group_block": "<Hinweis>"|null, "running": {id, media, kind,
  state, n, joinable, join_block, mine}|null}`. Den Gruppenanruf-Knopf nur zeigen, wenn `group` true ist (föderiert: alle
  Server melden `calls3`).

### Endpunkte (neu bzw. erweitert)

| Endpunkt | Body | Antwort / Fehler |
|---|---|---|
| `POST /api/calls` (Gruppen-, freier, Seiten-Chat) | `{id, chat_id, media, device, caps}` – **ohne** `signals` | `{ok, call}` (`kind: group`, `state: ringing`, nur der eigene Endpunkt). `409 {running}` = läuft schon → beitreten; `409` ohne `multi` bzw. Server ohne `calls3` |
| `POST /api/calls/{id}/join` | `{device, role, replaces?, caps, signals: {geraet: versiegeltes offer}}` | `{ok, call}`. `409 {join_block, error, legs?, limit?}`; `409 {legs}` ohne `join_block` = Liste veraltet → neu anbieten; `403` fremdes Gerät; `429` |
| `POST /api/calls/{id}/leave` | `{device, reason?: "hangup"\|"handover"}` | `{ok}`; nur eigene Geräte (auch ein anderes eigenes) |
| `POST /api/calls/{id}/invite` | `{uids: [benutzername \| Prinzipal]}` | `{ok, n}` – Gruppe: Mitglieder erneut klingeln lassen |
| `POST /api/calls/{id}/end` | wie bisher, **mit `device`** | Gruppe bzw. Begleitgerät: nur dieses Gerät verlässt (`{ok, left: [...]}`); Hauptgerät im 1:1: Ende für alle |
| `POST /api/calls/{id}/decline` | wie bisher | Gruppe: nur die eigene Einladung (alle eigenen Geräte hören auf zu klingeln) |
| `POST /api/calls/{id}/alive` | `{device}` | zusätzlich `leg: "left"` = dieses Gerät ist nicht mehr im Anruf (z. B. übernommen) → Anruf hier beenden |
| `POST /api/calls/{id}/signal` | wie bisher | Ziele = alle anderen aktiven Endpunkte (`legs`); föderiert sternförmig über den führenden Server |

`join`: `signals` muss je ein Angebot an **jedes Gerät anderer Personen** enthalten (eigene Geräte optional, empfohlen).
Ein zweites eigenes Gerät mit `role: "member"` wird zum `companion`. `handover` braucht `replaces` = eigenes Gerät im
Anruf. Ein Gruppenanruf wird `active`, sobald zwei Personen dabei sind, und läuft, solange mindestens zwei Personen dabei
sind; dem niemand beitritt, endet er nach `ring_s` (verpasst).

### Live-Ereignisse und Mitteilungen

- `call.roster` `{call_id, chat_id, kind, media, state, limit, legs, joinable, join_block, rv}` an alle Konten des Anrufs
  (1:1 beide Personen mit **allen** Geräten, Gruppe alle Mitglieder). `legs[].me` ist hier immer `false` – eigene
  Endpunkte über `who` erkennen.
- `call.invite` `{call_id, chat_id, kind: "group", media, from: {username, name, avatar, handle?, chat}, from_device,
  ring_s, silent, members}` – Gruppenanruf klingelt. `silent: true` (mehr als `ring_silent` Mitglieder): nicht klingeln,
  nur „Anruf läuft – beitreten“ anzeigen.
- `call.state` zusätzlich: `kind: "group"`; `state: "declined"` (eigene Einladung auf einem anderen Gerät abgelehnt);
  `moved: "caller"|"callee"` mit `device` (Übernahme im 1:1). Gruppe `active` heißt nur „zwei sind dabei“ – nicht
  aufhören zu klingeln. Aufhören, wenn `call.roster` ein **eigenes** Gerät zeigt.
- Push: Art `call.group` (`call`, `chat`, `media`, `caller`, `group: true`, kurze Lebensdauer); iOS VoIP/CallKit wie
  `call`. Ältere Apps zeigen sie als normale Mitteilung.
- Chat-Eintrag `kind: "call"`: Gruppe mit `group: true` und `n` (Zahl der Personen, die dabei waren); `status`
  `ended` bzw. `missed`.

### Body (E2E, ergänzt saga-call v3)

| Feld | in | Bedeutung |
|---|---|---|
| `pc` | alle | `min(dev_a, dev_b) + ":" + max(dev_a, dev_b)`; der **Hinzukommende** legt sie in seinem Angebot fest |
| `state.ready` + `replaces` | state | Übernahme fertig: das neue Gerät schickt `{"t":"state","ready":true,"replaces":"<altes Gerät>",…}` an das alte |
| `bye.reason` | bye | `hangup` · `handover` |

### Abläufe

**Beitreten (alle Fälle):** `GET /api/calls/{id}?device=` (Liste) → je anderem Endpunkt eine PeerConnection, `offer` mit
`n: 1`, `pc`, `caps` → alle in **einem** `POST …/join`. Antworten und ICE kommen über `call.signal` (bzw. `GET …?after=`).
Ein `409` mit `legs` (ohne `join_block`) heißt: Liste neu, einmal neu anbieten.

**Angebot eines unbekannten Geräts (die anderen):** gehört das Gerät laut `legs` zum Anruf und steht es in der **signierten
Geräteliste** seiner Person (Geräte-Zertifikat vom Identitätsschlüssel, TOFU wie im Chat; föderiert über die geprüften
v2-Schlüssel)? Dann **ohne Klingeln** annehmen und „<Name> (<Gerät>) ist hinzugekommen“ zeigen; sonst ablehnen (Hinweis
„Unbekanntes Gerät von <Name> abgelehnt“). Jeden Endpunkt mit Personen- und Gerätenamen anzeigen.

**Begleitgerät („Hinzufügen“, E4):** `join` mit `role: "companion"`. Standard: Mikrofon **aus**, Wiedergabe **stumm**
(Echo-Schutz), Kamera aus – nicht nach Mikrofon fragen, solange es aus ist. Typisch: Bildschirm vom Laptop teilen, Ton
bleibt am Handy. Knöpfe: Mikrofon, Kamera, Ton an/aus, Bildschirm, **„Hierher übernehmen“** (eigener Knopf).

**Übernahme („Hierher übernehmen“):** aus der Leiste ohne Beitritt: `join` mit `role: "handover"`, `replaces` = eigenes
Hauptgerät (`legs`: eigenes Gerät mit `role != companion`); als Begleitgerät: kein neuer `join`.
1. Mikrofon und Wiedergabe zunächst aus. Sobald alle Verbindungen zu **anderen Personen** `connected` sind: Mikrofon und
   Ton an, dann `state` mit `ready: true, replaces: <altes Gerät>` an das alte Gerät.
2. Das alte Gerät schaltet sofort Mikrofon und Ton aus (Überlappung ≤ 300 ms), schickt `bye` (`reason: "handover"`) an
   die anderen, dann `POST …/leave {device, reason: "handover"}`, und zeigt „Anruf auf <Gerät> übernommen“. CallKit/
   ConnectionService: „anderswo angenommen“; auf dem neuen Gerät als laufend melden.
3. Klappt es nicht innerhalb von `handover_s` (15 s): das neue Gerät verlässt den Anruf mit Hinweis; das alte bleibt.
4. Der Server macht das neue Gerät zum Hauptgerät (`caller_device`/`callee_device` bzw. `role: member`).

**Leiste „Anruf läuft“ auf anderen eigenen Geräten:** aus `GET /api/calls/active` (nach `call.roster`/`call.state` neu
holen): eigenes Gerät im Anruf, dieses nicht → „Anruf läuft auf <dev_name>“ mit **Hinzufügen** und **Hierher
übernehmen**; Gruppenanruf im Chat ohne eigenes Gerät → „Gruppenanruf läuft · n Geräte“ mit **Beitreten**. Bei
`joinable: false` stattdessen der Hinweis (`old` → „braucht Saga 2.4“, `full` → „Der Anruf ist voll“).

**Gruppenanruf (E5):** `POST /api/calls` ohne `signals` → allein im Anruf („Klingelt bei den anderen …“). Die anderen
bekommen `call.invite` (+ Push) und treten mit `join` (`role: "member"`) bei; ablehnen mit `decline`. Verlassen mit
`end {device}` bzw. `leave`. Ein Gerät, das `bye` bekommt bzw. in `call.roster` fehlt, wird getrennt; der Anruf endet für
mich erst mit `call.state ended` bzw. `alive` → `ended`/`leg: "left"`.

**Ansicht und Bitrate:** ab drei Endpunkten Mesh-Werte je Strom (Tabelle „Bitrate je Weg“: 600/500/350 kbit/s, ≤ 540p bzw.
360p15). Kacheln je Verbindung, Sprecher hervorheben (Pegel der empfangenen Tonspur, z. B. `getSynchronizationSources()`
bzw. `audioLevel`). Ein 1:1 mit stillem Begleitgerät (ohne Ton, Kamera, Bildschirm) bleibt Einzelansicht. Ton eigener
Geräte nie abspielen. Bildschirm teilen gilt für alle Verbindungen (je Verbindung Betriebsart A/B nach deren `caps`;
eine später hinzukommende Verbindung bekommt den laufenden Bildschirm).

### Beispiele

```json
PUT /api/calls/caps        {"device":"5cbb…","caps":["reneg","want","bye","screen2","multi"]}
POST /api/calls            {"id":"7927…","chat_id":12,"media":"video","device":"0e32…","caps":["reneg","want","bye","screen2","multi"]}
POST /api/calls/7927…/join {"device":"a130…","role":"companion","caps":["reneg","want","bye","screen2","multi"],
                            "signals":{"0e32…":"<versiegelt>","5cbb…":"<versiegelt>"}}
→ 409 {"error":"Der Anruf ist voll (höchstens 4 Geräte).","join_block":"full","limit":4,"legs":[…]}
{"t":"offer","pc":"5cbb…:a130…","n":1,"caps":["reneg","want","bye","screen2","multi"],"media":"video","sdp":"…","fp":["sha-256 4A:…"]}
{"t":"state","pc":"0e32…:a130…","ready":true,"replaces":"0e32…","mic":true,"cam":false,"screen":false}
{"t":"bye","pc":"0e32…:5cbb…","reason":"handover"}
```

### Sicherheit

Ein Server könnte ein fremdes Gerät einschleusen (Geister-Gerät): Clients nehmen nur Geräte an, die in der signierten
Geräteliste der Person stehen, und zeigen jeden Endpunkt mit Namen. Lässt der Server jemanden weg, ist das nur DoS.
Ein Begleitgerät ist für alle sichtbar. Dauerhafte Schlüssel gibt es im Mesh nicht (DTLS je Sitzung).

Tests: `tests/anrufe_raum.py` (Server), `tests/browser_raum.js` (Web: 1:1 + Begleitgerät + Übernahme, 4er-Mesh,
Bildschirm in der Gruppe, 2.3-Client), föderiert `tests/foederation_anrufe_raum.py` (drei Server, auch `FED_B_23=1`) und
`BJS=1 python3 tests/foederation_anrufe_raum.py` (Browser über drei Server, `tests/browser_fed_raum.js`).

### Ausblick 2.5: SFU mit Frame-E2E (Spike D3, nur Bewertung)

Zeitlich begrenzter Spike (20.10.2026), **in 2.4 wird nichts umgestellt**. Geprüft im Web (headless Chromium 141, Loopback
zweier PeerConnections, Kamera-/Mikrofon-Attrappe 720p, 8 s): `RTCRtpScriptTransform` mit einem Worker, AES-GCM-128 je
Frame (WebCrypto), Kopf unverschlüsselt (VP8: 10 Byte Schlüsselbild / 3 Byte sonst, Opus: 1 Byte, als AAD), Trailer
`IV (12) + Schlüssel-ID (1)` wie bei LiveKit/Jitsi.

| Messung | ohne Frame-E2E | mit Frame-E2E |
|---|---|---|
| Video-Frames dekodiert | 156 | 156 (0 Entschlüsselungsfehler) |
| Zeit je Frame im Worker | – | 0,07 ms verschlüsseln, 0,05 ms entschlüsseln |
| Zusatz je Frame | – | 29 Byte (12 IV + 1 ID + 16 Tag) |

Bewertung für 2.5:
- **Web: machbar** (`RTCRtpScriptTransform` in Chromium, Safari ≥ 15.4, Firefox ≥ 117; Rückfall
  `createEncodedStreams` für ältere Chromium). CPU ist vernachlässigbar.
- **CSP:** Saga erlaubt `worker-src 'self'` – der Transform-Worker muss eine **statische Datei** sein (z. B.
  `static/frame-e2e.js`), Blob-Worker werden abgewiesen. Keine CSP-Lockerung nötig.
- **Ton:** 29 Byte je Opus-Paket (50/s) sind ≈ 11,6 kbit/s – bei 24–32 kbit/s Ton zu viel. Für 2.5 kürzerer Trailer
  (Zähler mit variabler Länge wie SFrame RFC 9605, IV aus Schlüssel-Salz + Zähler) oder 40/60 ms Opus-Rahmen. Video
  (+≈ 7 kbit/s bei 30 fps) ist unkritisch.
- **Apps:** brauchen den Bibliothekswechsel auf `webrtc-sdk` (`RTCFrameCryptor`/`FrameCryptor`, AES-GCM, gleiches
  Trailer-Format); im Spike nicht gemessen (D3: erst in 2.5, eigener Interop-Test Web ↔ iOS ↔ Android vor dem Umstieg).
- Ein striktes RFC-9605-SFrame bieten die nativen Bibliotheken nicht – Empfehlung bleibt das LiveKit-kompatible Format.
- Schlüssel: je sendendem Gerät, als `fkey`-Signal versiegelt verteilt (Abschnitt „saga-call v3“: Typ reserviert); neuer
  Schlüssel bei jedem Verlassen, HKDF-Ratsche bei jedem Beitritt.
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

   `409 {"error": …, "offset": n}` falsche Stelle oder ein anderer PUT läuft gerade (dann ab `n` weiter; `error` ist nur
   Text, maßgeblich ist `offset`) ·
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
  Ereignis je 2 s mit allen neuen). Sind es mehr als 8 (große Gruppe), kommt nur `{"chat_id": 7, "receipts": [],
  "more": true}` – dann, falls der Chat offen ist, `GET …/receipts` holen.
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

## Anmeldenamen, Medien-Token, Versionen (2.4, Testrunde)

- **Anmeldenamen anderer Konten:** Personensuche (`GET /api/share/targets`, `GET /api/chat/people`), Freigaben
  (`GET/PUT/DELETE /api/notes/{id}/shares`, Feld `target` bei `type: user`), `GET /api/shared` (`owner.username`),
  Kommentare, Benachrichtigungen (`actor.username`), Anwesenheit und Versionen liefern für andere lokale Konten einen
  pseudonymen Handle (`~` + 16 Zeichen `[a-z2-7]`) statt des Anmeldenamens; `name` ist Anzeigename, öffentlicher Name
  oder derselbe Handle. Das eigene Konto (`me: true`) kommt unverändert. Apps behandeln das Feld wie bisher als
  undurchsichtige Kennung: `PUT/DELETE …/shares`, `POST /api/chats` (`user`, `members`) und
  `POST /api/chats/{id}/members` (`user`) nehmen den Handle oder den Benutzernamen. Chat-Mitglieder
  (`members[].username`, `sender.username`) und Anruf-Teilnehmer bleiben Anmeldenamen (Chats v1 und saga-call v1 binden
  Schlüssel und Signaturen daran); neu ist `members[].ref` (derselbe Handle wie in der Personensuche) zum Abgleich.
  Apps sollten den Anmeldenamen anderer nicht anzeigen.
- **Sicherheitsrunde 2 (Orakel geschlossen):**
  - Personensuche trifft andere Konten nur noch über Anzeigenamen (nicht, wenn er gleich Anmeldename/E-Mail ist),
    öffentlichen Namen, den genauen Handle und – solange der Admin-Schalter „Suche nach Anmeldename“ an ist (Standard) –
    den **genauen** Anmeldenamen. Kein Präfix des Anmeldenamens mehr; die Reihenfolge hängt nicht mehr vom Anmeldenamen ab.
  - Ist der Schalter aus, nehmen `PUT …/shares`, `POST /api/chats` (`user`, `members`) und `POST /api/chats/{id}/members`
    einen Benutzernamen nur noch für Personen, mit denen man schon einen Chat hat bzw. an die man schon geteilt hat;
    sonst dieselbe Antwort wie für ein unbekanntes Konto (404 bzw. 400). Der Handle geht immer. Apps senden ohnehin die
    Kennung aus der Personensuche – keine Änderung nötig.
  - `GET /api/chat/keys?users=` nimmt Benutzernamen **oder Handles** (`~…`) und liefert nur noch das eigene Konto und
    Personen mit einem gemeinsamen Chat (auch früher). Die Antwort steht unter der angefragten Kennung (Handle bleibt
    Handle); `null` = noch keine Chats eingerichtet. Unbekannte, gesperrte und noch fremde Konten fehlen gleichermaßen
    (bis 2.3 auch Konten, mit denen man einen Chat beginnen darf). Apps holen Schlüssel erst für Chat-Mitglieder bzw.
    Anruf-Gegenüber – das bleibt so; Schlüssel vor dem Anlegen eines Chats gibt es nicht mehr.
  - **Bekannte Grenze (Chats v1):** Wer mit einem Konto einen Chat beginnen darf, bekommt beim Anlegen des
    Direkt-Chats bzw. der Gruppe dessen Anmeldenamen in `members[].username` – auch bevor das Gegenüber geantwortet hat.
    Das ist nötig: Geräte-Zertifikate (`saga-chat/v1/device`), Zusatzdaten und Signatur jeder Nachricht
    (`saga-chat/v1/msg`, `…/sig`), die Sicherheitsnummer und saga-call v1 enthalten den Anmeldenamen, und schon die
    erste Nachricht lässt sich nur mit den so geprüften Geräteschlüsseln des Gegenübers verschlüsseln. Lokale
    Direkt-Chats kennen keine Annahme (anders als Chats über Servergrenzen mit Handles, v2). Eine Bindung an den Handle
    für neue lokale Chats bräuchte neue Zertifikate von allen Geräten (2.3-Apps erzeugen sie nicht) und ist für eine
    spätere Version vorgemerkt.
- **Unterzeile `sub` (Linux-Runde 2):** `GET /api/share/targets` und `GET /api/chat/people` liefern je Person `sub`
  (öffentlicher Name, sonst Handle `~…`, leer, wenn `name` schon der Handle ist), `members[]` in Gruppen-, freien und
  Seiten-Chats ebenso. Nie der Anmeldename. Apps zeigen `sub` unter dem Namen, damit sich Gleichnamige unterscheiden.
- **Öffentlicher Name:** `PUT /api/account/public-name` lehnt den eigenen Anmeldenamen ab (400 bzw. `ok: false` bei
  `check`); `GET` meldet `is_login: true`, wenn ein früher gewählter Name dem Anmeldenamen entspricht (Hinweis zeigen).
- **Zweiter Faktor entfernen:** `DELETE /api/account/totp` und `DELETE /api/account/passkeys/{id}` verlangen eine
  Bestätigung über `POST /api/account/security/confirm` (höchstens 5 Minuten alt); eine frische Anmeldung allein reicht
  nicht mehr. Sonst `403 {reauth: true}` – wie bisher bestätigen und wiederholen.
- **Zweiter Faktor hinzufügen, neue Wiederherstellungscodes (2.4, M3b):** `POST /api/account/passkeys/options`,
  `POST /api/account/passkeys`, `POST /api/account/totp/setup`, `POST /api/account/totp/confirm` und
  `POST /api/account/recovery` verlangen dieselbe Bestätigung, sobald das Konto schon einen Passkey oder eine
  Authenticator-App hat. Nur die Ersteinrichtung (noch kein zweiter Faktor) geht weiter mit frischer Anmeldung bzw.
  Passwort. Apps müssen deshalb auch beim **Hinzufügen** mit `403 {reauth: true}` rechnen: Bestätigungsdialog zeigen
  (`methods` aus `GET /api/account/security`), `POST /api/account/security/confirm`, dann den abgewiesenen Schritt
  wiederholen (auch den zweiten Schritt `POST /api/account/passkeys` mit derselben `credential` – die Challenge bleibt
  gültig). Ein Passkey oder eine Authenticator-App, die erst in dieser Sitzung bzw. nach dieser Anmeldung angelegt
  wurde, bestätigt nichts (`403` mit Text); `methods` und `POST /api/account/security/options` bzw.
  `/api/chat/reauth/options` bieten solche Faktoren gar nicht erst an. Ältere Server: kein `403` beim Hinzufügen.
- **Nextcloud-Blöcke:** Im Seiteninhalt (`GET /api/notes/{id}`) und im Status (`GET /api/notes/{id}/nc`) steht als
  `author` fremder Blöcke der Handle; `?a=` beim Abruf und das Zurückspeichern nehmen ihn an. Eigene Blöcke behalten den
  eigenen Namen (Vergleich mit dem eigenen Konto wie bisher).
- **Medien-Token:** `POST /api/chat/files/{fid}/token` liefert `m2.…` (verschlüsselt, nicht lesbar). Weiter nur als
  Kopf `X-Saga-Media-Token` verwenden.
- **Versionen:** `POST /api/notes/{id}/versions/checkpoint` (Rolle Bearbeiten) sichert den aktuellen Stand als Version,
  wenn er sich von der neuesten unterscheidet und diese mindestens 60 s alt ist -> `{created: bool}`. Apps können es nach
  einer Bearbeitungspause und beim Verlassen einer Seite rufen (optional; ältere Server: 404 ignorieren).
- **Anmeldung:** Fehlversuche bremsen statt sperren; die Meldung (429/401 mit `error`) nennt die Wartezeit. Eine harte
  Sperre gibt es nur nach sehr vielen Versuchen von vielen Adressen (Benachrichtigung `login.locked`, `system: true`).

## Chat-Stufe 3: Krypto über Handles (2.5, in Arbeit)

Stand der ersten Stufe; Konzept „Krypto v3 über Handles“. **Kein neues Krypto-Format:** Chats der Stufe 3 sind
`saga-chat/v2` (`docs/APP-API-FED.md`, Abschnitt 6) – gebunden an den stabilen Handle `~x@host` und `chat_ref` –, auch
wenn alle Mitglieder auf diesem Server sind. Der Server legt neue lokale Chats nur dann in Stufe 3 an, wenn die
Einstellung `chat_v3` an ist (vorerst Standard aus) und alle Mitglieder ein Handle-Zertifikat haben (jedes Konto mit
einem Gerät ab App 2.3). Bestehende Chats bleiben vorerst v1.

- **Kopfzeile `X-Saga-Chat`:** höchste Chat-Stufe des Clients. `3` (Apps/Web 2.5): Chat mit `v: 3`, `fed: false`.
  `2` (Apps/Web 2.3/2.4): **Kompat-Sicht** – derselbe Chat als `v: 2` mit `ref`, `home_self: true`, `my_handle`; die
  Krypto passt, die Apps nehmen ihre v2-Wege. Ohne Kopfzeile (2.1/2.2): `fed_hint` „mit Saga 2.5 öffnen“, keine
  Nachrichten, Senden 400. `GET /api/chat/me` meldet `features` mit `chat3`, solange die Einstellung an ist.
- **Keine Anmeldenamen anderer Konten:** `members[].username` ist für andere Konten der Handle (= `handle`), das eigene
  Konto bleibt unverändert (`me: true`). Ebenso `peer.username`, `sender.username` (Nachrichten mit `sender_handle`),
  `recipients_v2[].username`, `receipts[].from`, `title_msg.sender`, Live-Ereignisse `chat.message` (`sender`),
  `chat.typing` (`user.username`), `chat.receipt` (`from`), `chat.removed`/`chat.cleared` (`by.username`) – das eigene
  Konto bekommt in Ereignissen weiter den eigenen Benutzernamen. `devices`/`idents` in `GET …/messages` gibt es nur
  noch für Nachrichten im alten Format (ohne `sender_handle`).
- **Endpunkte nehmen den Handle:** `DELETE /api/chats/{id}/members/{handle}`, `POST /api/chats/{id}/members`
  (`user` = Such-Handle oder Handle `~x@host`), `POST /api/chat/presence` (`users`; Antwort und `chat.presence` unter
  der angefragten Kennung).
- **Schlüssel:** `GET /api/chat/v2/keys` liefert Schlüssel von Konten dieses Servers nur für sich selbst und Personen
  mit einem (früheren) gemeinsamen Chat. `GET /api/chat/keys` (v1) zählt Chats der Stufe 3 nicht. Ändern sich Schlüssel
  (neues Gerät, Zertifikate, neue Geräte-Liste), bekommen Chat-Partner mit einem gemeinsamen v2-/Stufe-3-Chat das
  Live-Ereignis `chat.keys.changed {handle}` – auch ohne Föderation.
- **Anrufe:** saga-call/v2 wie über Servergrenzen – Anruf-Objekt mit `v: 2`, `peer.username` = `peer.handle` (+ `host`),
  `legs[].who` für andere als Handle (mit `handle`, `host`; Schlüssel über `/api/chat/v2/keys`), `call.ring`/`call.invite`
  mit `from` als Handle, `call.state.by` als Handle, Anruf-Eintrag im Chat (`kind: call`) mit `from`/`to` als Handles.
  `POST /api/calls/{id}/invite` nimmt Handles.
- **Noch nicht in Stufe 3:** Umstellung bestehender Chats (`v3_seq`), Reaktionen,
  Bearbeiten, Sprachnachrichten. Testvektoren für Reaktionen und Bearbeiten: `tests/vectors/chat_v3.json`.
