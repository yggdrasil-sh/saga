# Föderation 2.3 – Vertrag für die App-Teams (Etappen E0 – E8)

Saga 2.3 bringt Protokoll 2 zwischen verbundenen Saga-Servern. Für die Apps (Web, iOS/macOS, Android, Linux) kommen in
dieser Etappe drei Dinge dazu: **Adressbuch-Suche** (Personen anderer Server nach Anzeigename), **föderierte Handles**
und das **Chat-Krypto-Format `saga-chat/v2`** mit Schlüsselverzeichnis, gemeinsamen Testvektoren und TOFU (E0/E1),
dazu **Direkt-Chats über Servergrenzen** mit Nachrichtenanfragen (E2, Abschnitt 6), **Anrufe** im Format
`saga-call/v2` (E3, Abschnitt 7), **Seiten anderer Server vollständig** – Datenbanken, Unterseiten, Hochladen,
Versionen, Export, Anwesenheit und Live (E4/E6, Abschnitt 8) – sowie **freie Gruppen und Seiten-Chats** mit Mitgliedern
mehrerer Server (E5, Abschnitt 9). Abschnitt 10 fasst zusammen, wie sich die Server gegen protokollwidrige Gegenstellen
schützen (E8) und was Apps davon sehen. **2.4:** Gelesen-Bestätigungen (versiegelt, über das Heim) und Online-Status
über Servergrenzen – Abschnitt 11 (ersetzt die frühere Regel „Gelesen bleibt lokal“).

Alle Endpunkte wie gewohnt mit Sitzung oder Geräte-Schlüssel (`Authorization: Bearer hkd_…`) und `X-Saga: 1`. Alles
ist rückwärtskompatibel: neue Felder und Ereignisse ignorieren ältere Apps; lokale Chats bleiben **`saga-chat/v1`**,
unverändert. `MIN_APP` ändert sich dadurch nicht. **Geräte sprechen nie direkt mit fremden Servern** – nur mit dem
eigenen.

## 1. Föderierte Handles

Jedes Konto hat einen stabilen, pseudonymen Handle: `~` + 16 Zeichen aus `a–z2–7`, dazu `@host` des Heimservers
(Host ggf. mit Port), z. B. `~k3m5q7r2tz4wxa6b@saga.verein.example`.

- Zufällig, ändert sich nie (auch nicht beim Ändern des öffentlichen Namens), ist **nie** der Anmeldename.
- Der öffentliche Name (Veröffentlichen, `name@server`) bleibt, wo es ihn gibt, die Adresse zum **Teilen von Seiten**
  (Protokoll 1). Chat-Schlüssel binden sich ausschließlich an den Handle.
- Eigener Handle: `GET /api/chat/v2/me` → `handle` bzw. `GET /api/account/federation` → `handle` (erst nach erstem
  Bedarf vorhanden, sonst `null`).

## 2. Adressbuch-Suche

`GET /api/fed/people?q=<Suchtext>[&purpose=chat|share]` → `{"people": [...]}`, höchstens 10 Treffer. Ab 3 Zeichen
Teilwort-Suche im Anzeigenamen; bei 1–2 Zeichen nur genaue Treffer (Anzeigename oder öffentlicher Name, Groß/Klein
egal – z. B. `hd`); `name@host` bzw. `@name@host` findet genau diese Person (öffentlicher Name oder Handle) auf diesem
Server, auch mit kurzem Namen. Höchstens 60 Anfragen je Minute (sonst 429). Apps dürfen deshalb schon ab 1 Zeichen
suchen (entprellt); dasselbe gilt für `GET /api/chat/people` und `GET /api/share/targets`:

| Feld | Bedeutung |
|---|---|
| `type` | immer `"remote"` |
| `name` | Anzeigename der Person (vom Heimserver; nie Anmeldename) |
| `host` | Server der Person |
| `label` | fertig zum Anzeigen: `"Anna Berg · b.example"`; bei gleichen Namen auf demselben Server zusätzlich `· annab` (öffentlicher Name) bzw. `· ~k3m5q7…`. Der Trennpunkt kommt nur vom eigenen Server: aus Namen anderer Server entfernt er Trennpunkte (`·`, `•`, `・` …), damit niemand eine fremde Herkunft vortäuscht |
| `handle` | föderierter Handle `~x@host` (für Chat/Schlüssel) |
| `target` | Ziel zum Teilen: `@name@host` (öffentlicher Name) bzw. `@~x@host` |
| `pub` | `name@host`, falls die Person einen öffentlichen Namen hat, sonst `null` |
| `avatar` | Profilbild-Adresse beim eigenen Server oder `null` (Initialen) |

- `purpose=share` liefert nur Server, mit denen Teilen erlaubt ist. Seit E2 auch Personen **ohne** öffentlichen Namen:
  `target` ist dann `@~x@host` (pseudonymer Handle, `pub` = `null`); zum Teilen
  `PUT /api/notes/{nid}/shares {"type":"remote","target":<target>,"role":…}` wie bisher. Nur Server ab 2.3 (Fähigkeit
  `handles`) verstehen Handles – bei älteren bleibt es beim öffentlichen Namen. Zeigt den Handle nicht an (`label`/
  `host` reichen).
- Dieselben Treffer stehen zusätzlich in den bestehenden Suchen: `GET /api/chat/people` → neues Feld `remote` (Liste
  wie oben; Direkt-Chat beginnen: Abschnitt 6.1) und `GET /api/share/targets` → `remote` (Treffer mit
  `label`/`host`/`avatar`/`pub`, vor exakten `name@server`-Treffern).
- Erwähnungen von Personen (`@Person`) gibt es in Saga noch nicht; wenn sie kommen, nutzen sie denselben Endpunkt.
- Gleiche Namen immer über `label`/`host` unterscheidbar machen, nie nur den Namen zeigen.

**Datenschutz-Schalter je Konto** (Einstellungen → Konto → „Verbundene Saga-Server“):
`GET /api/account/federation` → `{directory, federation, peers, handle}`;
`PUT /api/account/federation {"directory": false|true}` – „In Adressbüchern verbundener Server erscheinen“
(Standard an). Wirkt sofort (verbundene Server löschen den Eintrag). Den Schalter nur zeigen, wenn `federation` true ist.
Im Abgleich stehen nur Anzeigename, Bild-Stand, Handle und ggf. öffentlicher Name – nie Anmeldename, E-Mail, Gruppen.

## 3. Krypto-Format `saga-chat/v2`

Notation wie v1: `B64` = Standard-Base64 mit Padding, `LP(f1..fn)` = je Feld `uint32_be(len) || bytes` (Strings UTF-8),
`V2 = "saga-chat/v2/"`, nur libsodium-Primitive.

| Was | Definition |
|---|---|
| `chat_ref` | 32 Hex-Zeichen + `@` + Host des Heimservers des Chats (ersetzt die lokale `chat_id`) |
| Handle-Zertifikat | `sig = sign_detached(LP(V2+"handle", handle, ident_pk, backup_pk), ident_sk)` |
| Geräte-Zertifikat v2 | `fed_sig = sign_detached(LP(V2+"device", handle, device_id, enc_pk, sign_pk), ident_sk)` |
| Nachricht | `body_ct = B64(nonce24 ‖ xchacha20poly1305_ietf(inhalt, AD, nonce, K))` |
| AD | `LP(V2+"msg", chat_ref, msg_id, sender_handle, sender_device, ts, reply_to\|"", ai\|"")` |
| Signatur | `sign_detached(LP(V2+"sig", chat_ref, msg_id, sender_handle, sender_device, ts, reply_to\|"", ai\|"", generichash32(raw(body_ct))), device_sign_sk)` |
| `ai` | `""` oder Hex-SHA-256 des Textes einer KI-Antwort (mitsigniert – v1 kannte das nur als Server-Beleg) |
| Empfänger | `K` per `crypto_box_seal` je Gerät `dev:<device_id>@<host>` und je Mitglied `backup:<handle>` |
| Datei | Blob `nonce24 ‖ xchacha20poly1305(datei, AD = LP(V2+"file", chat_ref))` |
| Geräte-Liste | `dset = {ver, ids, sig}`, `sig = sign_detached(LP(V2+"devices", handle, ver, ids sortiert mit "," verbunden), ident_sk)`; `ver` steigt bei jeder Änderung |
| Sicherheitsnummer v2 | beide `(handle, ident_pk)` nach Handle (UTF-8-Bytes) sortiert, `h = generichash64(LP(V2+"safety", h_a, i_a, h_b, i_b))`, 12 Blöcke: je 5 Bytes big-endian mod 100000, fünfstellig |

Identitäts-, Backup- und Geräteschlüssel sind **dieselben** wie in v1 – es kommen nur die Zertifikate v2 dazu.

### 3.1 Eigene Zertifikate hochladen

Nach dem Entsperren (das Gerät kennt `ident_sk`):

1. `GET /api/chat/v2/me` → `{handle, host, identity, cert_ok, devices:[{id, fed_sig, ok}], missing:[ids], need, federation}`.
2. Ist `need` true: `PUT /api/chat/v2/cert` mit
   `{"handle_sig": B64(Handle-Zertifikat)}` (wenn `cert_ok` false) und/oder
   `{"devices": {"<device_id>": B64(fed_sig), …}}` für die IDs aus `missing`; danach (Antwort erneut prüfen), wenn
   `device_set_ok` false: `{"device_set": {"ver": (device_set.ver oder 0) + 1, "ids": device_set_want, "sig": …}}` – die
   signierte Liste der aktiven Geräte (genau `device_set_want`, sonst 409; gleiche oder kleinere `ver` → 409).
   Für **andere** eigene Geräte nur signieren, wenn deren v1-Zertifikat (aus `GET /api/chat/me`) gegen den eigenen
   Identitätsschlüssel stimmt. Der Server prüft jede Signatur (403 bei Fehler) und meldet die Änderung verbundenen Servern.
3. Nach „Neu beginnen“ (neue Identität), neuem, gekoppeltem oder entferntem Gerät: wieder 1.–2. (still im Hintergrund;
   Auslöser auch das Live-Ereignis `chat.keys.changed` mit `own: true`).

### 3.2 Schlüssel anderer Personen

`GET /api/chat/v2/keys?handles=~a@b.example,~c@c.example[&fresh=1]` (höchstens 20) →
`{"keys": {"<handle>": eintrag | null}}`, Eintrag:
`{v:2, handle, ident_pub, backup_pub, sig, dset?, devices:[{id, enc_pub, sign_pub, fed_sig, revoked}], source, fetched, stale?}`.

- `source`: `local` (eigener Server), `remote` (gerade beim Heimserver geholt), `cache` (Zwischenspeicher ≤ 1 h oder
  bis `chat.keys.changed`); `stale: true` = Heimserver gerade nicht erreichbar, letzter bekannter Stand.
- `null`: unbekannt, Person ohne Zertifikat v2, Server nicht verbunden bzw. Chat/Schlüssel dort abgeschaltet.
- **Die App prüft selbst** (der eigene Server könnte lügen): Handle-Zertifikat gegen `ident_pub` mit genau dem
  angefragten Handle; jedes Gerät gegen `fed_sig` – ungültige Geräte verwerfen, bei ungültigem Handle-Zertifikat den
  ganzen Eintrag. `dset` gegen `ident_pub` prüfen: **aktiv ist ein Gerät nur, wenn es in der gültigen signierten Liste
  steht und nicht `revoked` ist** (ein Server kann Geräte so nicht unterschieben und Widerrufe nicht verschweigen; eine
  falsche `revoked`-Meldung kostet höchstens Zustellung). Ohne gültige `dset` (Altbestand): `revoked` vom Server.
  Web: `SagaChatCrypto.checkKeysV2(handle, eintrag)` → `{…, ver, devices (mit berechnetem revoked)}`.
- Empfängerliste: je nicht widerrufenem Gerät `dev:<id>@<host aus dem Handle>`, dazu `backup:<handle>`
  (`recipientsV2` in `static/chat-crypto.js`, `recipients_v2` in `tests/chatlib.py`).

### 3.3 TOFU (Vertrauen beim ersten Kontakt)

Je Handle merkt sich die App lokal `{ident, verified}` (Web: IndexedDB `trust2:<eigenes Konto>:<handle>`; getrennt von
den v1-Einträgen `trust:<uid>`). Regeln (maschinenlesbar in `tests/vectors/chat_v2.json` → `tofu`):

Je Handle merkt sich die App zusätzlich die höchste gesehene Version der Geräte-Liste (`dver`).

| gespeichert | gesehen | Ergebnis |
|---|---|---|
| nichts | ident A, ver v | vertrauen, `{ident: A, verified: false, dver: v}` speichern |
| A | A, ver ≥ dver | vertrauen (größere ver merken) |
| A | A, ver < dver | **ablehnen** (ältere Geräte-Liste – ein Server will einen Widerruf zurückdrehen) |
| A (nicht überprüft) | B | **Warnung** („Schlüssel hat sich geändert“), `{ident: B, verified: false, changed: true, old: A, dver}` speichern |
| B mit `changed: true` | B | **weiter Warnung** – bis die Person sie bestätigt („Verstanden“: `changed` weg) oder überprüft |
| A (überprüft) | B | **blockieren** – erst nach erneutem Vergleich der Sicherheitsnummer weiter |
| – | ungültiges Zertifikat | ablehnen |

Bei Warnung, Blockade oder Ablehnung **nicht senden und nicht anrufen** (Web: Hinweis im Chat-Kopf mit „Sicherheitsnummer“
und „Verstanden“). „Überprüft“ setzt die Person nach dem Vergleich der Sicherheitsnummer v2
(Web: `SagaChatFed.verify(handle, ident_pub, true)`); im Chat-Kopf erscheint dann „✓ überprüft“.

### 3.4 Testvektoren (Pflicht in der CI jeder App)

`tests/vectors/chat_v2.json` – erzeugt mit PyNaCl (`python3 tools/chat_vectors_v2.py gen|check`), Gegenprobe
libsodium.js + `static/chat-crypto.js` (`node tools/chat_vectors_v2.mjs`), Server-Referenz `saga/chat_v2.py`, Testreihe
`tests/chat_v2_vektoren.py`. Inhalt: Schlüssel aus Seeds, Handle- und Geräte-Zertifikat (Nachricht + Signatur), signierte
Geräte-Liste (`device_set`), zwei
Nachrichten (mit `reply_to` bzw. `ai`) mit AD, Ciphertext und Signatur, versiegelte Schlüssel für `dev:`/`backup:`, die
jede Umsetzung **öffnen** muss (crypto_box_seal ist zufällig), Datei, Sicherheitsnummer, Abschnitt `reject` (Fälle,
die **abgelehnt** werden müssen: falscher Handle, v1-Zertifikat als v2, andere `chat_ref`, anderer Absender, entferntes
`ai`, geänderte Zeit, Geräte-Liste mit anderen IDs/anderer Version/anderem Handle) und `tofu` (mit `ver`). Die v1-Vektoren `tests/chat_vectors.json` bleiben unverändert gültig.

## 4. Live-Ereignisse und Server-Ereignisse

Zwischen den Servern: `directory.changed` (Adressbuch neu holen), `chat.keys.changed` (Schlüssel eines Handles geändert
→ Zwischenspeicher veraltet), dazu die Chat-Ereignisse aus Abschnitt 6.6.

Live-Ereignisse an Apps (wie alle über `/api/events`, Art `direct`):

| Ereignis | Daten | Bedeutung |
|---|---|---|
| `chat.keys.changed` | `{handle, own?}` | Schlüssel einer Person, mit der du einen Chat über Servergrenzen hast, haben sich geändert → Schlüssel frisch holen (`fresh=1`), TOFU prüfen. `own: true`: deine eigenen (Geräte-Liste neu signieren, 3.1) |
| `chat.request` | `{chat_id}` | neue Nachrichtenanfrage (still – keine Benachrichtigung) |
| `chat.message` | wie bisher, zusätzlich `kind: 'sys'` bzw. `failed: true` | neue Nachricht, Systemzeile; bei `id` einer eigenen wartenden Nachricht: jetzt zugestellt bzw. abgewiesen |
| `chat.typing` | `{chat_id, user:{username, handle, name}}` | tippt (auch Personen anderer Server) |
| `chat.receipt` | `{chat_id, receipts:[{from, handle, sealed}], more?}` | (2.4) Gelesen-Bestätigungen, auch von Personen anderer Server (Abschnitt 11) |
| `chat.presence` | `{username, online, last}` | (2.4) Online-Status einer abonnierten Person, auch anderer Server |
| `chat.removed` / `chat.deleted` / `chat.changed` | wie bisher | auch für Chats über Servergrenzen |

## 5. Server-zu-Server (zur Einordnung)

`/.well-known/saga` meldet `protocol: 2`, `protocols: [1, 2]` und `capabilities` (v1: `share read edit comment files
notify`; neu: `v2 express handles directory keys chat2 calls2 live presence db subpages upload versions export
chatgroups`; 2.4: `receipts online`; 2.5: `horn` nur bei eingeschaltetem Feature-Flag `calls.horn`, Gjallarhorn
mit Frame-E2E, `docs/HORN-E2E.md`). Funktionen gelten nur, wenn **beide** Server sie melden (Austausch
über `POST /fed/v2/hello`, signiert) **und** der Admin-Schalter je Instanz an ist (Chat, Anrufe, Adressbuch; Ausnahme
von „Teilen nur innerhalb gemeinsamer Gruppen“ – Standard aus). Neue signierte Endpunkte: `POST /fed/v2/hello`,
`POST /fed/v2/events` (Ereignisse mit Kennung `id`, optional Kette `chain` + `cseq`: idempotent, streng aufsteigend je
Kette), `GET /fed/v2/directory?since=&cfg=`, `GET /fed/v2/keys?handles=`. Zustellung zuerst **express** (synchron,
harte Frist `FED_EXPRESS_TIMEOUT`, Standard 5 s, eigener Thread-Pool), sonst Ausgangskorb mit Wiederholung in der
Reihenfolge der Kette. Server mit Protokoll 1 (2.1/2.2) bleiben voll verbunden: Teilen, Kommentare,
Benachrichtigungen wie bisher; Adressbuch und Schlüssel gibt es mit ihnen nicht.

## 6. Direkt-Chats über Servergrenzen (E2)

Alle bisherigen Chat-Endpunkte gelten weiter; Chats über Servergrenzen haben `v: 2`. **Apps, die sie verstehen, senden
bei jeder Anfrage die Kopfzeile `X-Saga-Chat: 2`.** Ohne sie (ältere Apps) erscheinen diese Chats mit dem Zusatz „bitte
App aktualisieren“ im Namen und `fed_hint`, `GET …/messages` liefert keine Nachrichten (nur `fed_hint`), Senden und
`…/recipients` antworten 400 mit dem Hinweis – nichts geht kaputt. `MIN_APP` ändert sich nicht.

### 6.1 Chat beginnen

`POST /api/chats {"kind":"dm","handle":"~x@host","device":…}` (Handle aus der Adressbuch-Suche). Der eigene Server wird
**Heim** des Chats und fragt beim Server der Person an (synchron): beim Gegenüber erscheint eine **Nachrichtenanfrage**.
Gibt es schon einen gemeinsamen Chat (auch einen, den die andere Seite begonnen hat), kommt dieser zurück. Fehler: 403
(Chats mit diesem Server aus, gesperrt), 404 (Person unbekannt), 502 (Server nicht erreichbar), 429. Ein Handle des
eigenen Servers ergibt einen normalen lokalen Chat (v1).

### 6.2 Chat-Objekt (zusätzliche Felder bei `v: 2`)

| Feld | Bedeutung |
|---|---|
| `v`, `ref`, `fed` | `2`, globale Kennung `chat_ref` (`<32 hex>@<heim>`), `true` |
| `home`, `home_self` | Host des Heimservers; ob es der eigene ist |
| `my_handle` | eigener Handle (Absender in v2) |
| `state`, `request` | eigener Stand `ok` / `request` (Nachrichtenanfrage, noch nicht angenommen) |
| `readonly`, `readonly_reason` | schreibgeschützt (Verbindung getrennt, Konto der anderen Person gelöscht, vom Admin abgeschaltet) |
| `peer` | `{username: '@~x@host', handle, name, label, host, avatar}` |
| `members[]` | zusätzlich `handle`, `host`, bei fremden `remote: true`, `label` |
| `ai` | KI erlaubt? Standard **aus** |

`GET /api/chats` liefert dazu `requests` (Anzahl); Anfragen zählen nicht in `unread`. Abgelehnte Chats fehlen.

### 6.3 Anfragen beantworten, Sperren

`POST /api/chats/{id}/request {"action":"accept"|"decline"|"block", "scope":"person"|"instance"}` – Ablehnen und Sperren
erfährt die andere Seite nicht (ihr Heim stellt nur nicht mehr zu; schickt es trotzdem, verwirft der eigene Server die
Nachrichten). Ist der eigene Server selbst das Heim, weist er Nachrichten der gesperrten bzw. abgelehnten Person ab – deren
App sieht dann „nicht zugestellt“ (HTTP 409). Nimmt man später doch an, holt der Server den Verlauf nach. Bis zur Annahme: keine Benachrichtigung, kein Push,
kein Klingeln, aber die Nachrichten sind lesbar (zeigt sie als Anfrage). Wer selbst schreibt oder den Chat selbst
beginnt, nimmt an. `GET /api/chat/blocks` → `{blocks:[{target, kind: person|instance, name, host, label, created}]}`,
`DELETE /api/chat/blocks?target=…` entsperrt.

### 6.4 Senden und Empfangen

1. Empfänger: Handles aller Mitglieder (`members[].handle`, auch der eigene) → `GET /api/chat/v2/keys?handles=…` →
   selbst prüfen + TOFU (Abschnitt 3) → Empfänger `recipientsV2` (aktive Geräte + `backup:<handle>` je Person).
2. Nachricht v2 verschlüsseln und signieren (Abschnitt 3, `chat_ref` = `ref`, `sender_handle` = `my_handle`) und
   `POST /api/chats/{id}/messages` mit `{v:2, id, ts, sender_device, body_ct, sig, reply_to?, ai?, ai_reply?, keys, files}`
   (`keys`: `dev:<id>@host` / `backup:<handle>` → versiegelt). `ai` = Hex-SHA-256 des Textes einer KI-Antwort
   (mitsigniert), `ai_reply` = Beleg wie in v1.
3. Antwort `{ok, seq, fseq, pending?}`: `pending: true` = Heim gerade nicht erreichbar, die Nachricht wartet beim
   eigenen Server (bis 3 Tage) – anzeigen als „wird zugestellt“. **409 mit `handles`**: Mitglieder/Geräte haben sich
   geändert → Schlüssel mit `fresh=1` holen und neu versiegeln (einmal). 403 mit Hinweis: schreibgeschützt bzw. Gerät
   noch ohne Zertifikat v2 (3.1). Abgewiesen → 409 mit Text.
4. Abruf wie bisher (`GET …/messages?calls=1&device=…`); Nachrichten in v2-Chats haben zusätzlich `fseq` (Reihenfolge des
   Heims), `sender_handle`, `ai` (Hash, Text) und `fstate` (`pending` | `failed` | `null`). Schlüssel `key`/`key_for` wie
   v1. Prüfen: Gerät `sender_device` aus den geprüften Schlüsseln von `sender_handle`, Signatur v2. `kind: 'sys'` =
   Systemzeile (Klartext-Metadaten in `body_ct`, z. B. `{"type":"ai","on":true,"by":handle,"name":…}`).
5. Nachricht mit `fstate: 'failed'`: „nicht zugestellt“, anbieten „erneut senden“ (neu verschlüsseln, alte löschen).

Der Lese-Zeiger (`…/read` mit `seq`, Ungelesen-Zähler) bleibt auf dem eigenen Server. **Gelesen-Bestätigungen** (2.4)
gehen versiegelt über das Heim an die anderen (Abschnitt 11) – nur mit Servern, die `receipts` melden. Tippen
(`…/typing`) geht direkt an den anderen Server (verwerfbar). Push und Benachrichtigungen kommen nur vom eigenen Server.

### 6.5 Anhänge, Löschen, Frist, KI

- Anhänge: Upload wie v1 (`POST /api/chats/{id}/files`), aber verschlüsselt mit `AD = LP(V2+"file", chat_ref)`. Abruf
  wie v1 (`GET /api/chat/files/{fid}`); liegt der Anhang bei einem anderen Server, holt der eigene ihn bei Bedarf
  (Zwischenkopie; kleine bis 256 KB gleich vorab) – der erste Abruf kann etwas dauern. Zwischenkopien sind je Server
  begrenzt (`FED_CHAT_CACHE_MB`, Standard 1024; zuletzt benutzte bleiben) und verfallen nach `FED_CHAT_CACHE_DAYS`
  (Standard 30) ohne Abruf – danach holt der eigene Server sie beim nächsten Abruf erneut (Anhänge in der App ruhig
  selbst zwischenspeichern, die Antwort ist `immutable`).
- `DELETE …/messages/{mid}` (eigene) und `DELETE /api/chats/{id}?scope=all` (Direkt-Chat für beide) laufen über das Heim
  an alle; `scope=me` bleibt lokal.
- `PATCH /api/chats/{id} {"retention_s": …}` gilt für alle (über das Heim, Antwort ggf. `pending`); jeder Server wendet
  zusätzlich seine eigene Höchstfrist an (die strengere gilt).
- `PUT /api/chats/{id}/ai {"allowed": true}` – in Chats über Servergrenzen standardmäßig aus; Ein-/Ausschalten erscheint
  bei allen als Systemzeile. Fragen an die KI gehen an den Server der fragenden Person.

### 6.5a Große Anhänge und Videos über Servergrenzen (2.4, `chatfile2`)

Für die Apps ändert sich nichts gegenüber lokalen Chats: Upload, Range-Abruf und Medien-Token wie in `APP-API.md`,
Abschnitt „Videos im Chat“. Zu beachten:

- Chat-Objekt `big_files`: `true`, wenn **alle** beteiligten Server (Heim und Server aller Mitglieder) die Fähigkeit
  `chatfile2` haben. Bei `false` (mindestens ein Server mit Saga 2.3) nur Anhänge bis `limits.file_max` – der Server
  weist größere beim Anlegen des Uploads und beim Senden mit `409` ab („Der Server des Gegenübers kann große Videos noch
  nicht“). Videos bis `file_max` (v1 oder v2) gehen wie bisher, auch an 2.3.
- Ein großer Anhang bleibt beim Server des Absenders. Der eigene Server holt **nur die angefragten Bereiche**
  (Range) und legt sie als lückenhafte Zwischenkopie ab; Anfang und Ende (je 1 MiB, `moov` vorn oder hinten) holt er
  gleich beim Eingang der Nachricht. Der erste Abruf eines noch fehlenden Bereichs dauert daher etwas länger (Messung
  lokal: ~30–60 ms je MiB); danach wie lokal. Ohne saga-media kann eine Antwort kürzer sein als angefragt (höchstens
  8 MiB, `Content-Range` beachten).
- Zwischenkopien zählen bei `FED_CHAT_CACHE_MB` mit ihren geholten 1-MiB-Einheiten; geräumte Bereiche werden bei Bedarf
  neu geholt.

Zwischen den Servern (zur Einordnung): `GET /fed/v2/chat/files/{fid}?range=a-b` – der Bereich steht in der **signierten**
Query (die Signatur deckt `@path` und `@query`), höchstens 4 MiB je Teil. Antwort `206`, signiert mit `Content-Digest` über
genau diesen Teil und der Nonce der Anfrage, dazu `Saga-Size` (Gesamtgröße) und `Content-Range`. Ohne `range` liefert der
Server nur Anhänge bis `FILE_MAX` (sonst `413`), ungültige Bereiche `416`. Ankündigungen (`files: [{id, size}]`) dürfen mit
`chatfile2` bis 8 GiB groß sein, sonst bis `FILE_MAX`; an Server ohne `chatfile2` gehen größere Anhänge gar nicht erst
(der Fan-out lässt sie weg). Im Container liefert und holt saga-media die Bytes selbst; Python prüft und signiert nur
(`/internal/media/fed-serve`, `fed-need`, `fed-got`).

### 6.6 Zwischen den Servern (zur Einordnung)

Heim = Server der Person, die den Chat beginnt; maßgeblich für Mitglieder, Empfängerliste (prüft sie exakt), Reihenfolge
(`fseq`), Frist, Löschen, KI-Schalter. Signierte Endpunkte: `POST /fed/v2/chat` (Aufträge `request accept decline send
del drop meta typing`, 2.4 dazu `receipt online online.ev` – Abschnitt 11, synchron, harte Frist `FED_EXPRESS_TIMEOUT`; sonst Warteschlange beim Absender-Server als
Ereignis `chat.op`, Kette `chatup:<id>`), `GET /fed/v2/chat/msgs?ref=&after=` (Lücken nachholen),
`GET /fed/v2/chat/files/{fid}` (Anhang beim Absender-Server). Ereignisse vom Heim (Kette `chat:<id>`, mit Epoche):
`chat.msg`, `chat.msg.del`, `chat.removed`, `chat.meta`, `chat.reject`, 2.4 `chat.receipt`. Absender müssen zum anfragenden Server
gehören und richtig signiert sein; Wiederholungen (`msg_id`), Umsortieren und Fälschungen werden abgewiesen und im
Prüfprotokoll vermerkt. Trennen → Chats mit Personen bzw. Heim dort schreibgeschützt.

## 7. Anrufe über Servergrenzen (E3)

Aus angenommenen Direkt-Chats mit `v: 2` (nicht bei `request`/`readonly`), Endpunkte wie bisher (`/api/calls…`); der
Anruf trägt `v: 2`, `peer.handle`. Format **`saga-call/v2`** – wie v1, aber mit Handles:

`sig = sign_detached(LP("saga-call/v2/signal", call_id, from_handle, from_device, to_handle, to_device, body), device_sign_sk)`,
versiegelt wird `{"v":2,"body":body,"sig":B64(sig)}` für das Zielgerät (Web: `sealSignalV2`, `openSignalV2`,
`verifySignalV2` in `static/chat-crypto.js`; Python: `tests/chatlib.py`).

- Anrufen: `POST /api/calls` mit `signals` für **alle aktiven Geräte des Gegenübers aus den geprüften v2-Schlüsseln**
  (Geräte-IDs ohne Host); 409 mit `devices` → Schlüssel frisch holen. Weitere Antworten wie v1 (busy, 409 „erst
  annehmen“, 502 nicht erreichbar).
- Annehmen, Ablehnen, Signale, Auflegen, Lebenszeichen wie v1. Der Server der anrufenden Person führt den Zustand, die
  Signalisierung läuft synchron über die Föderation (keine Warteschlange).
- ICE/TURN: jede Seite holt ihre Server wie bisher über `GET /api/calls/config` beim **eigenen** Server.
- Nachrichtenanfragen klingeln nie; Trennen der Server beendet laufende Anrufe.

### 7.1 saga-call v3 über Servergrenzen (2.4, E0)

Der v3-Body (`pc`, `caps`, `n`, Neuverhandlung, ICE-Restart, `state.pip/want/path`, `bye`; Vertrag in `APP-API.md`,
Abschnitt „Anrufe: saga-call v3“) gilt unverändert auch für föderierte Anrufe: Er steckt im versiegelten
`saga-call/v2`-Text, die Server sehen ihn nicht. Weitere `offer`/`answer` laufen wie ICE als `signal` über
`/fed/v2/call` (synchron, in beide Richtungen, Budget wie lokal). Der `caps`-Hinweis aus `POST /api/calls` bzw.
`/answer` geht in `ring`/`answer` mit; ein 2.3-Server ignoriert ihn (dann ist `caps` beim Gegenüber leer – maßgeblich
bleiben die signierten `caps` im Body). Jede Seite prüft `reneg` beim **eigenen** Server (`GET /api/calls/config`).
Die Server-Fähigkeit `calls3` (Raum, mehrere Geräte, Gruppen) regelt Abschnitt 7.2.
Bildschirm v2 (E3, `screen2`: zweite Videospur, `tracks`/`msid`/`screen_mid`) gilt ebenso föderiert und braucht keine
neue Server-Fähigkeit: Jede Seite meldet `screen2` nur, wenn ihr **eigener** Server `reneg` und `screen2` liefert; fehlt
es auf einer Seite, teilt der Client wie bisher per `replaceTrack` (Betriebsart A).

### 7.2 Mehrgeräte und Gruppenanrufe über Servergrenzen (2.4, E4/E5, Cap `calls3`)

Client-Vertrag: `APP-API.md`, Abschnitt „Anrufe: Mehrgeräte und Gruppen“ – föderiert gilt er unverändert (Body im
versiegelten `saga-call/v2`-Text, Handles statt Benutzernamen, Schlüssel fremder Personen über die geprüften
v2-Schlüssel). Für Server-Entwickler:

- **Fähigkeit** `calls3` in `CAPS2` (braucht den Admin-Schalter „Anrufe“). Raumfunktionen nur, wenn **alle** beteiligten
  Server `calls3` melden; mit einem 2.3-Server (nur `calls2`) bleibt ein 1:1-Anruf `v=1` mit einem Gerät je Seite, und in
  Chats mit Personen dieses Servers gibt es keinen Gruppenanruf (`chat.call.group: false`, `group_block`: „Der Server
  <host> kann noch keine Gruppenanrufe (braucht Saga 2.4)“).
- **Führender Server** (`chat_call.lead` = NULL): 1:1 der Server der anrufenden Person (wie 2.3), Gruppe das **Heim** des
  Chats. Er hält die maßgebliche Liste der Endpunkte (`chat_call_leg`, je Gerät, mit `peer_id` des Servers des Geräts);
  die anderen Server spiegeln den Anruf (`lead` = Instanz) für ihre Konten.
- **Signale sternförmig:** Gerät → eigener Server → führender Server → Server des Zielgeräts, **synchron**, ohne
  Ausgangskorb, versiegelt je Zielgerät (kein Server liest etwas). Zwei Server, die nicht direkt verbunden sind (B und C
  einer Gruppe mit Heim A), signalisieren über A.
- Auftrag `POST /fed/v2/call` (signiert, wie 2.3) – neue `op`:

| op | von → an | Felder | Antwort |
|---|---|---|---|
| `join` | Server des Geräts → führender | `id, actor, device, role, replaces?, caps, name, signals`; Gruppenstart zusätzlich `start: true, ref, media` | `200 {ok, role, legs}`; `409 {join_block}` bzw. `{running}`; `403` Nicht-Mitglied |
| `leave` | Server des Geräts → führender | `id, actor, device` | `200 {ok}` |
| `roster` | führender → alle anderen (nach dem Commit, verwerfbar, alle 60 s erneut) | `id, ref, kind, media, state, created, from, from_device, legs: [{device, who: handle, role, state, caps, name, replaces?}], ring: [handles], invites: {handle: state}, rv` | `200 {ok, old: [handles ohne Gerät mit multi]}`; nur vom führenden Server (sonst `403`); `rv` = Stand – eine ältere Liste überschreibt keine neuere |
| `invite` | beliebig → führender bzw. führender → Server der Person | `id, actor, who: [handles]` | `200 {ok, n}` |
| `signal` | wie 2.3, im Raum zusätzlich `leg` (Endpunkt des Absenders) | vom führenden Server auch für Personen dritter Server (`actor` = deren Handle) | |
| `alive` | zusätzlich `device` (Lebenszeichen je Endpunkt an den führenden Server) | | |
| `end` | Gruppe: **nur** vom führenden Server | | |

- **Gruppenanruf, gestartet auf einem Server, der nicht das Heim ist:** `join` mit `start: true` beim Heim → das Heim legt
  den Anruf an, lädt ein und schickt `roster` mit `ring` an die anderen Server; diese klingeln bei ihren Mitgliedern
  (`call.invite`) und melden in der Antwort, wer kein 2.4-Gerät hat (`old`).
- **Kopien** ohne `roster` vom führenden Server enden nach 150 s; Trennen der Server beendet laufende Anrufe bzw. nimmt die
  Endpunkte der getrennten Instanz aus Gruppenanrufen, die wir führen.
- **Prüfung:** `actor` muss zum anfragenden Server gehören (außer bei Signalen vom führenden Server), Endpunkte nur für
  Mitglieder des Chats bzw. die beiden Personen des 1:1, Geräte eigener Konten nur, wenn sie wirklich zu ihnen gehören.
  Grenzen wie lokal (`SIGNALS_MAX` je Endpunkt-Paar, `SIG_RATE` je Gerät, `CALL_RATE` auch für `join`).

Tests: `tests/foederation_anrufe_raum.py` (drei Server, B und C nur mit A verbunden; Laufzeit eines Signals B → A → C;
`FED_B_23=1` mit 2.3-Server B), Browser über drei Server mit `BJS=1`.

### 7.3 Frame-Schlüssel über Servergrenzen (2.5, Gjallarhorn P4, Cap `horn`)

`horn` in `CAPS2` (Admin-Schalter „Anrufe“, nur solange `calls.horn` an ist). Die Frame-Schlüssel der Geräte (`fkey`,
`fkey-ack`, Vertrag `docs/HORN-E2E.md` §4) sind Body-Typen von `saga-call/v2` und laufen im Auftrag `signal` wie jedes
Raum-Signal: sternförmig über den führenden Server, versiegelt je Zielgerät, an Handles gebunden. Kein Server und kein
Knoten liest sie. Am Protokoll ändert sich sonst nichts (Test `tests/foederation_anrufe_raum.py`).

### 7.4 Gruppenanrufe über Knoten über Servergrenzen (2.5, Gjallarhorn P9/P10, Cap `horn`)

Das Heim des Anrufs (der führende Server) koordiniert; jeder Server stellt Token nur für **seine** Knoten aus.
Konzept: `claude/saga-gjallarhorn-konzept-v2.md` §2.4, §2.6, §2.7, §3.4; Knoten: `gjallarhorn/NODE.md` („Kaskade“).

**Wann über Knoten (Heim beim Start):** Feature-Flag `calls.horn`, das startende Gerät meldet `horn`, das Heim hat einen
bereiten Knoten, **alle** beteiligten Server melden `horn` und jeder kann einen Knoten nutzen: entweder teilt das Heim
mit ihm (Knoten mit „Teilen an“, Server erlaubt, kein Notaus) oder er hat selbst einen bereiten Knoten (Auftrag
`horn_nodes`, 60 s zwischengespeichert; beim Start/Beitritt schickt der Server des Geräts `horn_nodes: n` mit).
Sonst bleibt der Anruf im Mesh (2.4-Grenzen): mit Servern ohne `horn` (2.3/2.4) still, sonst mit Hinweis
`call.horn_mesh` (Text in der Sprache der Anfrage, auch in der Kopie beim anderen Server; Apps zeigen ihn einmal an).

**Für Apps (eigener Server führt den Anruf nicht):** nichts Neues an den Endpunkten. `POST /join` und
`POST /api/calls/{id}/horn` antworten wie lokal mit `horn.candidates`; der eigene Server holt die Kandidaten auf
geteilten Knoten beim Heim (Token des Heims, mit Kontingent) und hängt seine eigenen bereiten Knoten an. Neu im Block:

| Feld | Bedeutung |
|---|---|
| `candidates[].home` | `true` = Knoten des Heims (geteilt; das Gerät verbindet sich direkt dorthin) |
| `fed.share` | das Heim teilt gerade einen Knoten mit diesem Server |
| `fed.why` | ohne geteilten Knoten: `noshare` (Teilen aus bzw. Server nicht erlaubt), `quota` (Kontingent erschöpft), `off` (Notaus), `gone` (Heim nicht erreichbar) |
| `error` | nur ohne Kandidaten: klare Meldung für die Oberfläche (z. B. „Das Kontingent … ist erschöpft.“) |

Die Wahl des Knotens (`node` in `POST …/horn`) meldet der Server dem Heim im selben Schritt. Die Kennung eines Geräts an
einem Knoten (`peer_ref`) leitet jeder Server selbst ab; Teilnehmende über eine Kaskade behalten die Kennung ihres
Knotens. **Zuordnung CSRC → Gerät:** zusätzlich zu `hpn` (Knoten des eigenen Servers) schickt jedes Gerät im
versiegelten Zustand (Body-Typ `state` von `saga-call/v2`) seine aktuellen Kennungen mit: `{"t":"state", …, "hp":
["<peer_ref>", …]}` (höchstens 4, alle Verbindungen während eines Wechsels). Apps senden `hp` nach jedem neuen Bein
und werten es beim Empfang aus (unbekannte Kennungen sonst wie bisher: Teilnehmerliste neu holen).

**Aufträge zwischen den Servern** (`POST /fed/v2/call`, signiert; nur wenn beide `horn` melden, sonst 403):

| Auftrag | Richtung | Inhalt → Antwort |
|---|---|---|
| `horn_nodes` | Heim → Server | `{}` → `{nodes}` (bereite Knoten dort; 0 bei Notaus) |
| `horn` | Server → Heim | `{id, actor, device, dev_class, avoid, horn_nodes, node?: {name, own, url?, pub?}, own_first?: {name, url, pub}}` → `{candidates, share, why, cascade, here, assigned?}`. Mit `node`: Wahl eintragen – eigener Knoten des Servers (`own`, mit Adresse und Knotenschlüssel für die Kaskade) bzw. geteilter Knoten des Heims (409 bei Kontingent). Ohne `node` beim Beitritt merkt das Heim den Knoten vor wie lokal (`assigned {name, own}`): den ersten geteilten Kandidaten, sonst `own_first` |
| `horn_cascade` | Heim → Server | `{id, bridge, node, url, peer, peer_pub, token}`: der Knoten `node` des Servers soll den Mittelpunkt `peer` (Adresse `url`, Knotenschlüssel `peer_pub`) anwählen; `token` ist das accept-Token des Heims. Der Server prüft (Anruf mit diesem Heim, eigener Endpunkt auf dem Knoten, kein Notaus) und gibt seinem Knoten das eigene dial-Token. Alle 20 s erneut, solange gewünscht |
| `horn_uncascade` | Heim → Server | `{id, bridge}` – Brücke abbauen |

Die Teilnehmerliste (`roster`) und die Antwort auf `join` mit `start` tragen `topo` (`horn`/`mesh`) und ggf.
`horn_mesh`; die Kopie übernimmt `topo` beim Anlegen.

**Adressen geteilter Knoten:** `hello` (beide Richtungen) trägt `horn_shared: [url]` – die Knoten mit „Teilen an“, nur
wenn der andere Server sie nutzen darf (sonst `[]`). Der andere Server nimmt sie in `connect-src` seiner Seiten auf
(Geräte sprechen den Knoten direkt an); Änderungen am Teilen lösen sofort ein `hello` aus. Seiten, die vorher geladen
wurden, brauchen ein Neuladen (der Web-Client sagt das). Die Knoten selbst erlauben die Herkunft der Seiten dieser
Server (Steuerkanal `origins`). `horn_cascade` wählt nur öffentliche bzw. unter „Freigegebene interne Hosts“
eingetragene Adressen an (sonst 403).

**Teilen und Kontingente (Heim, Admin-Bereich → Föderation, je Server):** erlaubt (Standard ja), Teilnehmende und
Mbit/s je Richtung (leer = Standard aus Admin → Anrufe, 10 / 20), Notaus. Das Kontingent steht in jedem Medien-Token
für Geräte dieses Servers (`q` = zufällige Kennung je Server, `q_legs`, `q_kbit`) und im accept-Token seiner Kaskaden;
der Knoten setzt es durch (mehr Teilnehmende → 429, über der Rate verwirft er Bild bis zum nächsten Schlüsselbild, Ton
nie; Downlink je Teilnehmendem höchstens Kontingent / Teilnehmende). Notaus schickt `quota_off` an alle Knoten: sie
entfernen sofort alle Teilnehmenden und Brücken des Servers; danach gibt es keine Token mehr.

Tests: `tests/foederation_horn.py` (zwei Server mit je einem Knoten: Kaskade, Teilen, Kontingent, Erlaubt-Liste,
Notaus, Mesh-Rückfall, 1:1 über Knoten; `FED_B_APP=<Saga 2.4>`), Browser `tests/browser_fed_horn.js` (3 + 3 Personen,
einmal Kaskade, einmal Teilen).

### 7.5 1:1 über Knoten über Servergrenzen (2.5, Cap `horn_dm`)

Für die Apps ändert sich nichts gegenüber dem lokalen Fall (`APP-API.md`, „1:1 über Knoten“): `POST /api/calls` mit
`via: "horn"` geht jetzt auch in föderierten Direkt-Chats. Rückfall `horn_block: "fed"` nur noch, wenn der andere
Server `horn_dm` nicht meldet (älter, Gjallarhorn aus) oder den Auftrag ablehnt; `peer`/`off`/`node` meldet der andere
Server selbst (Gegenüber ohne Gerät mit `horn`, Gjallarhorn dort aus), `busy` wie 1:1. Antwortet er nicht rechtzeitig,
kommt 502 (kein Rückfall – dort könnte es schon klingeln; der Server schickt ein verwerfbares `end` hinterher).

Zwischen den Servern: Den Anruf führt wie in 2.3 der Server der anrufenden Person (A). Er prüft (`horn_dm` bei beiden,
eigener bereiter Knoten, je Server ein nutzbarer Knoten wie bei Gruppen) und schickt synchron:

| Auftrag | Richtung | Inhalt |
|---|---|---|
| `dmhorn` | A → B | `{id, ref, from, from_device, media, caps, name, horn_nodes?}`: B legt die Kopie an (`kind: group`, `topo: horn`, `callee` = eigenes Konto, `lead` = A) und lässt klingeln (`call.invite` mit `dm: true`). Antworten: 200; 409 `{busy}`, `{horn_block: "peer"\|"off"}`, `{error: "request"}` – dann ist bei B nichts angelegt |

Erst nach 200 legt A seinen Anruf an. Danach wie 7.4: Annehmen = `join` an A, Kandidaten über `horn`, Teilnehmerliste
`roster` (die Kopie wechselt mit `state: active` und meldet `call.state active` an die eigenen Geräte), Auflegen über
`leave`/`end`. Ablehnen bei B beendet die Kopie und schickt `end {reason: "declined"|"busy"}` an A (nur der
Angerufene, nur solange es klingelt); A beendet den Anruf ohne Rückmeldung.

## 8. Seiten anderer Server vollständig (E4/E6)

Seiten anderer Server haben wie bisher **negative IDs** (`-(id_dort * 1024 + peer_id)`) und laufen über die gewohnten
Endpunkte des **eigenen** Servers. Was mit dem Server der Seite geht, steht in jedem Seiten-Objekt einer fremden Seite
(`GET /api/notes/{id}`, `GET /api/fed/shared` → `items[]`) unter `remote`:

| Feld | Bedeutung |
|---|---|
| `remote.peer`, `remote.host`, `remote.name` | wie bisher |
| `remote.caps` | NEU: Liste aus `db subpages upload versions export presence live chatgroups` (2.5: auch `tplbtn`, `widgets1`, `rowtpl`) – nur, was beide Server können und der Admin erlaubt. Fehlt eine Fähigkeit (Server 2.1/2.2), bleibt es bei Lesen, Kommentieren und Titel/Inhalt bearbeiten |

Die Rolle prüft immer der Server der Seite (`role` wie bei eigenen Freigaben). Fehler kommen wie gewohnt (403/404/409/
423); kann der andere Server etwas noch nicht, antwortet der eigene Server mit **404** und dem Text „Der Server … kann das
noch nicht (ältere Saga-Version)“; nicht erreichbar → 502.

| Fähigkeit | Endpunkt (negative ID) | Hinweise |
|---|---|---|
| `db` | `GET /api/notes/{id}` | Datenbanken kommen mit `schema` (statt des früheren Hinweises); Relationen: `schema.props[].target` und Werte in `props` sind negative IDs |
| `db` | `GET /api/notes/{id}/rows[?limit&offset&view&filters&sorts&q&group&tz&today]` | wie bei eigenen Datenbanken (2.2, Phase 2c), rechnet der Server der Seite; `templates` immer leer |
| `db` | `PATCH /api/notes/{id}` | zusätzlich zu `title body format rev overwrite`: `props schema icon cover cover_pos tags wide font small` (`font`/`small` ab 2.5). Relationen nur auf Seiten desselben Servers (andere fallen weg), Bilder nur dieser Seite (`/api/fed/files/{id}/{fid}`) |
| `subpages` | `POST /api/notes {parent_id: <negativ>, title, icon?, tags?, kind?, props?}` | Unterseite bzw. Datenbank-Zeile (mit `props`) → `{id: <negativ>, updated}`; braucht `edit` |
| `subpages` | `POST /api/notes/{id}/move {parent_id: <negativ>, index}` | nur innerhalb desselben Servers und des geteilten Bereichs mit **gleichen Freigaben** (sonst 403 mit Hinweis); `parent_id: null` nie |
| `upload` | `POST /api/files` mit `X-Saga-Note: <negativ>` (bzw. `?note=`) | Multipart wie bisher → `file.url = /api/fed/files/{id}/{fid}`. Die Datei liegt beim Server der Seite; 507 = Grenze erreicht (Eigentümer bzw. `FED_UPLOAD_QUOTA_MB` je fremder Person). Bis zum Speichern sieht nur die hochladende Person die Datei |
| `upload` | `POST /api/files/copy {url, note: <negativ>}` | eigene Datei (`/files/<id>`) wird hochgeladen; Datei derselben fremden Seite (`/api/fed/files/…`) dort kopiert |
| `versions` | `GET /api/notes/{id}/versions`, `GET …/versions/{vid}`, `POST …/versions/{vid}/restore` | ab Rolle `edit` (lokal jetzt ebenso); `vid` bleibt die ID des anderen Servers |
| `export` | `GET /api/notes/{id}/export[?sub=1]` | Markdown bzw. ZIP wie lokal |
| `presence` / `live` | `POST /api/presence {note_id: <negativ>, editing, leave?}` | wie lokal (alle ~20 s); meldet zugleich ein **Abo** beim Server der Seite an (`FED_WATCH_TTL`, Standard 90 s). Solange es läuft, kommen über `/api/events` die gewohnten Ereignisse `note.changed`, `presence`, `comments.changed` mit negativen IDs; Personen anderer Server mit `remote: true` und Herkunft im Namen. Ohne `presence` antwortet der eigene Server mit `{users:[ich], live:false}` |
| `widgets1` | `GET /api/notes/{id}/widgets[?tz=]` | 2.5: Werte der Widgets einer fremden Seite (Format wie lokal, `APP-API.md` „Widgets in Seiten“). Der Server der Seite rechnet mit dem Prinzipal der Person (Name und Server) – Datenbanken, die nicht mit ihr geteilt sind, kommen als `no_access`. Das Heim reicht nur die bekannten Felder weiter. Ohne `widgets1` → `{"widgets": {}, "unsupported": true}` (kein Fehler). Zwischen den Servern: `GET /fed/v2/notes/{rid}/widgets` (signiert, wie `…/rows`) |
| `rowtpl` | `GET …/rows`, `POST /api/notes/{-vorlage}/duplicate` ohne `via`, `POST /api/notes/{-id}/discard` | 2.5 (fix27 E1/E4): Eintrags-Vorlagen für Bearbeitende – `templates` mit negativen IDs, `schema.default_tpl` als negative ID (vorher roh durchgereicht), Anlegen aus einer Vorlage ihrer Datenbank ohne Schaltfläche, Antwort mit `fresh`/`placeholder`, Verwerfen eines unveränderten neuen Eintrags. Ohne `rowtpl`: `templates` leer, Anlegen ohne `via` und `discard` → `404` mit Hinweis „kann das noch nicht“. Zwischen den Servern: `POST /fed/v2/notes/{rid}/duplicate {parent_id, title?}` und `POST /fed/v2/notes/{rid}/discard {fresh}` (signiert); der Server der Seite prüft mit dem Prinzipal wie lokal |
| `inline2`, `cols6` | `PATCH /api/notes/{id}` | 2.5 (P6-0, `APP-API.md` „Inline-Vertrag“): der Server der Seite kennt Inline-Farben, Erwähnungen, Inline-Mathe bzw. bis 6 Spalten. Speichert ein Server **ohne** diese Fähigkeit beim Eigentümer, erhält dieser Marken (Text unverändert → altes HTML) und Spalten 5–6 aus dem alten Stand |
| – | `shared.changed` (Live) | Unterseiten in geteilten Bereichen eines anderen Servers neu/verschoben/gelöscht → „Mit mir geteilt“ neu laden |

Eingebettete Datenbanken in fremden Seiten (Block `database`, `data.db`) tragen ebenfalls negative IDs. Nicht über
Servergrenzen: Löschen, Archivieren, Duplizieren/Vorlagen, Veröffentlichen, Freigaben, Rückverweise, KI auf der Seite.

**Auch lokal neu (Rollen):** `GET/POST …/versions…` ab `edit` (vorher nur Eigentümer; Wiederherstellen gesperrter Seiten
nur Eigentümer, 423); `POST /api/notes/{id}/move` für `edit` innerhalb eines geteilten Bereichs mit gleichen Freigaben
(Ziel muss bearbeitbar sein, oberste Ebene nur Eigentümer).

## 9. Freie Gruppen und Seiten-Chats über Servergrenzen (E5)

Chats mit `v: 2` und `kind: 'free'` bzw. `'page'` funktionieren wie Direkt-Chats über Servergrenzen (Abschnitt 6:
`X-Saga-Chat: 2`, Krypto `saga-chat/v2`, Empfänger aus `members[].handle`). **Heim** einer freien Gruppe ist der Server
der Person, die sie anlegt; Heim eines Seiten-Chats ist der Server der Seite (`home`, `home_self`).

- **Gruppe anlegen:** `POST /api/chats {kind:'free', members:[benutzer hier], handles:['~x@host', …], device}` – Handles
  aus der Adressbuch-Suche (`/api/chat/people` → `remote`). Bei den anderen erscheint die Gruppe als
  **Nachrichtenanfrage** (`request: true`, `invited_by: {name, host, label, remote}`); Annehmen/Ablehnen/Sperren wie 6.3.
  `invited_by` ist die Person, die **dich** eingeladen hat (nicht unbedingt die, die die Gruppe angelegt hat); `block`
  sperrt genau diese Person bzw. ihren Server. `remote: false` = eingeladen von einer Person des eigenen Servers – dann
  nur Annehmen/Ablehnen anbieten (`block` lehnt nur ab, `targets` ist leer).
- **Name der Gruppe:** wie v1 als verschlüsselte Nachricht `kind: 'title'` – in v2 mit denselben Feldern wie eine
  Nachricht (`v: 2`, `kind: 'title'`); `title_msg` im Chat-Objekt hat `sender_handle`, prüfen wie jede v2-Nachricht.
- **Mitglieder:** `POST /api/chats/{id}/members {handle:'~x@host'}` bzw. `{user}` (Konto hier), `DELETE …/members/{who}`
  (`who` = `members[].username`, ich selbst = verlassen). Nur Admins der Gruppe (`role: 'admin'`) ändern andere; die
  Änderung läuft über das Heim (ist es nicht erreichbar: 502). Danach `chat.changed`; die neue Liste kommt vom Heim.
- **Systemzeilen** (`kind: 'sys'`, Klartext-Metadaten in `body_ct`):
  `{"type":"members","added":[{name,host,handle}],"removed":[…],"by":{name,host,handle}|null,"reason":"disconnected"|"gone"|"access"?}`
  – Herkunft (`host`) immer anzeigen (der eigene Server nimmt sie aus dem Handle, nicht aus der Angabe des Heims – ein
  „Geist“ eines Servers, den das Heim gar nicht kennen kann, ist so als fremd erkennbar); `by` = `removed[0]` heißt
  „hat die Gruppe verlassen“.
  `{"type":"fed","on":true,"ai_off":true?}` – ein Seiten-Chat ist ab hier ein Chat über Servergrenzen.
- **Seiten-Chat einer fremden Seite:** `POST /api/chats {kind:'page', note_id:<negativ>, device}` → derselbe Chat wie
  beim Server der Seite (`note_id` negativ, `name` = Titel). Mitglieder ergeben sich aus den Freigaben (Personen und
  Gruppen anderer Server inklusive); Ändern geht dort nicht.
- **Umgestellte Seiten-Chats:** Bekommen Personen anderer Server Zugriff, wird ein bestehender Seiten-Chat zu `v: 2`
  (gleiche `id`). Nachrichten **ohne `sender_handle`** stammen aus der Zeit davor: mit v1 entschlüsseln und prüfen
  (`chat_id`, Absender `sender.username`, Geräte aus `devices`/`idents` der Antwort). Neue Nachrichten immer v2.
- **Personen dritter Server:** `GET /api/chat/v2/keys` liefert auch Personen von Servern, mit denen der eigene Server
  nicht verbunden ist (über das Heim eines gemeinsamen Chats, `source: 'home'`). Zertifikate und TOFU prüft die App wie
  immer selbst; Anhänge solcher Personen holt der eigene Server über das Heim (`GET /api/chat/files/{fid}` wie gewohnt).
- **Trennen:** Personen des getrennten Servers fallen aus Gruppen mit Heim hier (Systemzeile `reason: 'disconnected'`);
  auf dem getrennten Server ist der Chat `readonly`.
- Zwischen den Servern (zur Einordnung): Ereignis `chat.members` vom Heim (Kette `chat:<id>`), Aufträge an das Heim
  `members`, `leave`, `pagechat`, an Teilnehmer `resolve` (Handles/Gruppen nur für Ziele eigener Freigaben),
  `GET /fed/v2/chat/keys?ref=&handles=` (nur Mitglieder, nur für Teilnehmer-Server).

## 10. Schutz vor protokollwidrigen Servern (E8)

Ein verbundener Server kann sich falsch verhalten (Fehler, alte Version, Übernahme). Die Server prüfen deshalb alles,
was von dort kommt, selbst – Apps müssen dafür nichts tun außer dem, was ohnehin gilt: **Zertifikate, Signaturen und TOFU
prüft die App immer selbst** (Abschnitt 3), Herkunft (`host`, `label`) immer anzeigen.

| Angriff / Fehlerbild | Verhalten des eigenen Servers |
|---|---|
| gefälschte Absender (Konten anderer Server oder des eigenen), falsche Signaturen, Wiederholungen (Replay), Umsortieren | abgewiesen, Prüfprotokoll (`chat.forged`, `chat.badsig`, `chat.replay`, `chat.reorder`, `sig.replay`) |
| Unterdrücken von Nachrichten (Lücke in `fseq`) | Lücke erkannt, beim Heim nachgeholt (`chat.gap`) |
| Heim liefert eigene Schlüssel für eine Person eines Servers, mit dem der eigene Server **selbst verbunden** ist | nur der Abruf dort zählt (Nachricht wartet kurz, bis die Schlüssel geholt sind) |
| Geister-Mitglieder (Personen, die das Heim nicht kennen kann) | sichtbar mit Herkunft aus dem Handle; Systemzeile |
| neuer Identitätsschlüssel einer Person | geliefert wie jeder Schlüssel + Live-Ereignis `chat.keys.changed` – **die App warnt (TOFU)** bzw. blockiert bei „überprüft“ |
| Flut (Nachrichten, Mitgliederlisten, Namensauflösung, Hochladen, Personensuche, Ereignisse) | Raten je Server; Nachrichten und Mitgliederlisten werden später erneut zugestellt (nichts verworfen), sonst `429` mit `Retry-After` |
| langsame oder zu große Antworten | harte Fristen und Größengrenzen; je Server höchstens wenige Hintergrund-Aufträge gleichzeitig (`FED_PEER_SLOTS`), die Zustellung an andere Server läuft getrennt weiter |
| Zugriff mit negativen IDs außerhalb einer Freigabe, ID-Tricks | 404; unsinnige IDs aus Antworten fallen weg |
| Rückfall auf Protokoll 1 | sichtbar im Prüfprotokoll („Rückfall auf Protokoll 1“); Chat-Ereignisse von dort werden ignoriert, eigene warten im Ausgangskorb, bis Protokoll 2 wieder da ist; Chats zeigen `readonly` bzw. senden mit `pending` |
| Adressbuch mit fremden Namen/Hosts | Herkunft immer vom verbundenen Server, Namen bereinigt (keine Trennpunkte, keine Steuerzeichen) |

Für Apps sichtbar: `429` aus `/api/fed/people` (60/min), Nachrichten mit `pending` bzw. `fstate: 'failed'`, ein
erneuter (langsamerer) erster Abruf eines Anhangs nach Ablauf der Zwischenkopie, `invited_by.remote`. Verlässt eine
Person das Adressbuch ihres Servers (Opt-out) oder trennt der Admin die Verbindung, löscht der eigene Server ihren
Anzeigenamen, sofern sie hier nirgends mehr vorkommt (Kommentare, Versionen und Chat-Verläufe behalten ihn).

## 11. Gelesen-Bestätigungen und Online-Status über Servergrenzen (2.4)

Für Apps gilt derselbe Vertrag wie lokal (`APP-API.md`, Abschnitt „Gelesen-Bestätigungen und Online-Status“) mit
Format **v2**: signiert wird `LP("saga-chat/v2/receipt", chat_ref, eigener Handle, Gerät, body)`, versiegelt je
anderem Mitglied für dessen `backup_pub` aus den geprüften v2-Schlüsseln, Schlüssel `"backup:<handle>"`; `n` ist `fseq`.
Beim Empfang `handle` nutzen (Geräteschlüssel aus `GET /api/chat/v2/keys`, TOFU wie bei Nachrichten). Die Chat-Felder
`receipts_ok` / `online_ok` sagen, ob der andere Server mitmacht (Saga 2.3: `false` → nichts versiegeln, nichts zeigen).
Die frühere Regel „Gelesen bleibt lokal“ gilt nur noch für den Lese-Zeiger (`last_read`, Ungelesen-Zähler).

**Zwischen den Servern (zur Einordnung):** Fähigkeiten `receipts` und `online` in `CAPS2` (beide Server, Admin-Schalter
„Chat“ an). Mit 2.3-Servern wird nichts davon gesendet.

- Bestätigungen laufen **über das Heim**, in der Kette des Chats (also nach den Nachrichten, auf die sie sich beziehen):
  Teilnehmer-Server → Heim: Auftrag `{"op": "receipt", "ref", "actor": <handle>, "keys": {"backup:<handle>": sealed}}`
  (Kette `chatup:<id>`); Heim → Teilnehmer-Server: Ereignis `{"type": "chat.receipt", "ref", "by": <handle>, "keys"}`
  (Kette `chat:<id>`, je Server nur die Schlüssel seiner Konten). Zurücknehmen (Einstellung aus): dieselben Wege mit
  `"off": true` statt `keys`. Geprüft wird wie bei Nachrichten: `actor` gehört zum anfragenden Server, `by` kommt vom
  Heim, beide sind angenommene Mitglieder; der Server der lesenden Person schickt je Leser und Chat höchstens alle 2 s
  eine, jeder Server begrenzt je Instanz (1200 je Minute). Gespeichert wird nur das Versiegelte (neueste je Leser und
  Konto) – kein Lese-Zeiger im Klartext, keine Nachrichten-Kennung.
- Online-Status: Der eigene Server abonniert beim **Server der Person** (Muster wie die Seiten-Anwesenheit, 90 s,
  erneuert mit den Abfragen der Apps): `{"op": "online", "actor": <handle>, "items": [{"h": <handle>, "ref": <chat_ref>}]}`
  → `{"users": {<handle>: {"online", "last"}}, "ttl": 90}` – nur für Personen dort, die mit `actor` einen angenommenen
  Chat `ref` haben und den Status zeigen. Wechsel meldet der Server der Person verwerfbar mit
  `{"op": "online.ev", "items": [{"h", "online", "last"} | {"h", "hidden": true}]}`; angenommen werden nur Handles des
  meldenden Servers. „Zuletzt online“ geht nur gerundet (5 Minuten) über die Leitung.
