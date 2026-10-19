# Föderation 2.3 – Vertrag für die App-Teams (Etappen E0 + E1)

Saga 2.3 bringt Protokoll 2 zwischen verbundenen Saga-Servern. Für die Apps (Web, iOS/macOS, Android, Linux) kommen in
dieser Etappe drei Dinge dazu: **Adressbuch-Suche** (Personen anderer Server nach Anzeigename), **föderierte Handles**
und das **Chat-Krypto-Format `saga-chat/v2`** mit Schlüsselverzeichnis, gemeinsamen Testvektoren und TOFU. Chats über
Servergrenzen selbst (Senden, Empfangen, Anfragen) folgen mit E2 – die Apps können v2 jetzt schon umsetzen und gegen die
Testvektoren prüfen.

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

`GET /api/fed/people?q=<ab 3 Zeichen>[&purpose=chat|share]` → `{"people": [...]}`, höchstens 10 Treffer:

| Feld | Bedeutung |
|---|---|
| `type` | immer `"remote"` |
| `name` | Anzeigename der Person (vom Heimserver; nie Anmeldename) |
| `host` | Server der Person |
| `label` | fertig zum Anzeigen: `"Anna Berg · b.example"`; bei gleichen Namen auf demselben Server zusätzlich `· annab` (öffentlicher Name) bzw. `· ~k3m5q7…` |
| `handle` | föderierter Handle `~x@host` (für Chat/Schlüssel) |
| `target` | Ziel zum Teilen: `@name@host` (öffentlicher Name) bzw. `@~x@host` |
| `pub` | `name@host`, falls die Person einen öffentlichen Namen hat, sonst `null` |
| `avatar` | Profilbild-Adresse beim eigenen Server oder `null` (Initialen) |

- `purpose=share` liefert nur Personen mit öffentlichem Namen und nur Server, mit denen Teilen erlaubt ist; zum Teilen
  `PUT /api/notes/{nid}/shares {"type":"remote","target":<target>,"role":…}` wie bisher.
- Dieselben Treffer stehen zusätzlich in den bestehenden Suchen: `GET /api/chat/people` → neues Feld `remote` (Liste
  wie oben; bis E2 zum Anzeigen, ein Direkt-Chat lässt sich damit noch nicht beginnen) und `GET /api/share/targets` →
  `remote` (Treffer mit `label`/`host`/`avatar`, vor exakten `name@server`-Treffern).
- Erwähnungen (@Person) gibt es in Saga noch nicht; wenn sie kommen, nutzen sie denselben Endpunkt.
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
| Sicherheitsnummer v2 | beide `(handle, ident_pk)` nach Handle (UTF-8-Bytes) sortiert, `h = generichash64(LP(V2+"safety", h_a, i_a, h_b, i_b))`, 12 Blöcke: je 5 Bytes big-endian mod 100000, fünfstellig |

Identitäts-, Backup- und Geräteschlüssel sind **dieselben** wie in v1 – es kommen nur die Zertifikate v2 dazu.

### 3.1 Eigene Zertifikate hochladen

Nach dem Entsperren (das Gerät kennt `ident_sk`):

1. `GET /api/chat/v2/me` → `{handle, host, identity, cert_ok, devices:[{id, fed_sig, ok}], missing:[ids], need, federation}`.
2. Ist `need` true: `PUT /api/chat/v2/cert` mit
   `{"handle_sig": B64(Handle-Zertifikat)}` (wenn `cert_ok` false) und/oder
   `{"devices": {"<device_id>": B64(fed_sig), …}}` für die IDs aus `missing`.
   Für **andere** eigene Geräte nur signieren, wenn deren v1-Zertifikat (aus `GET /api/chat/me`) gegen den eigenen
   Identitätsschlüssel stimmt. Der Server prüft jede Signatur (403 bei Fehler) und meldet die Änderung verbundenen Servern.
3. Nach „Neu beginnen“ (neue Identität), neuem oder gekoppeltem Gerät: wieder 1.–2. (still im Hintergrund).

### 3.2 Schlüssel anderer Personen

`GET /api/chat/v2/keys?handles=~a@b.example,~c@c.example[&fresh=1]` (höchstens 20) →
`{"keys": {"<handle>": eintrag | null}}`, Eintrag:
`{v:2, handle, ident_pub, backup_pub, sig, devices:[{id, enc_pub, sign_pub, fed_sig, revoked}], source, fetched, stale?}`.

- `source`: `local` (eigener Server), `remote` (gerade beim Heimserver geholt), `cache` (Zwischenspeicher ≤ 1 h oder
  bis `chat.keys.changed`); `stale: true` = Heimserver gerade nicht erreichbar, letzter bekannter Stand.
- `null`: unbekannt, Person ohne Zertifikat v2, Server nicht verbunden bzw. Chat/Schlüssel dort abgeschaltet.
- **Die App prüft selbst** (der eigene Server könnte lügen): Handle-Zertifikat gegen `ident_pub` mit genau dem
  angefragten Handle; jedes Gerät gegen `fed_sig` – ungültige Geräte verwerfen, bei ungültigem Handle-Zertifikat den
  ganzen Eintrag. Web: `SagaChatCrypto.checkKeysV2(handle, eintrag)`.
- Empfängerliste: je nicht widerrufenem Gerät `dev:<id>@<host aus dem Handle>`, dazu `backup:<handle>`
  (`recipientsV2` in `static/chat-crypto.js`, `recipients_v2` in `tests/chatlib.py`).

### 3.3 TOFU (Vertrauen beim ersten Kontakt)

Je Handle merkt sich die App lokal `{ident, verified}` (Web: IndexedDB `trust2:<eigenes Konto>:<handle>`; getrennt von
den v1-Einträgen `trust:<uid>`). Regeln (maschinenlesbar in `tests/vectors/chat_v2.json` → `tofu`):

| gespeichert | gesehen | Ergebnis |
|---|---|---|
| nichts | ident A | vertrauen, `{ident: A, verified: false}` speichern |
| A | A | vertrauen |
| A (nicht überprüft) | B | **Warnung** („Schlüssel hat sich geändert“), `{ident: B, verified: false, changed: true, old: A}` speichern |
| A (überprüft) | B | **blockieren** – erst nach erneutem Vergleich der Sicherheitsnummer weiter |
| – | ungültiges Zertifikat | ablehnen |

„Überprüft“ setzt die Person nach dem Vergleich der Sicherheitsnummer v2 (Web: `SagaChatFed.verify(handle, ident_pub, true)`).

### 3.4 Testvektoren (Pflicht in der CI jeder App)

`tests/vectors/chat_v2.json` – erzeugt mit PyNaCl (`python3 tools/chat_vectors_v2.py gen|check`), Gegenprobe
libsodium.js + `static/chat-crypto.js` (`node tools/chat_vectors_v2.mjs`), Server-Referenz `saga/chat_v2.py`, Testreihe
`tests/chat_v2_vektoren.py`. Inhalt: Schlüssel aus Seeds, Handle- und Geräte-Zertifikat (Nachricht + Signatur), zwei
Nachrichten (mit `reply_to` bzw. `ai`) mit AD, Ciphertext und Signatur, versiegelte Schlüssel für `dev:`/`backup:`, die
jede Umsetzung **öffnen** muss (crypto_box_seal ist zufällig), Datei, Sicherheitsnummer, Abschnitt `reject` (Fälle,
die **abgelehnt** werden müssen: falscher Handle, v1-Zertifikat als v2, andere `chat_ref`, anderer Absender, entferntes
`ai`, geänderte Zeit) und `tofu`. Die v1-Vektoren `tests/chat_vectors.json` bleiben unverändert gültig.

## 4. Live-Ereignisse und Server-Ereignisse

Zwischen den Servern neu (nicht an Apps): `directory.changed` (Adressbuch neu holen), `chat.keys.changed` (Schlüssel
eines Handles geändert → Zwischenspeicher veraltet). Apps bekommen dazu in E1 kein eigenes Live-Ereignis; sie holen
Schlüssel vor dem Senden ohnehin über `GET /api/chat/v2/keys`.

## 5. Server-zu-Server (zur Einordnung)

`/.well-known/saga` meldet `protocol: 2`, `protocols: [1, 2]` und `capabilities` (v1: `share read edit comment files
notify`; neu: `v2 express handles directory keys`). Funktionen gelten nur, wenn **beide** Server sie melden (Austausch
über `POST /fed/v2/hello`, signiert) **und** der Admin-Schalter je Instanz an ist (Chat, Anrufe, Adressbuch; Ausnahme
von „Teilen nur innerhalb gemeinsamer Gruppen“ – Standard aus). Neue signierte Endpunkte: `POST /fed/v2/hello`,
`POST /fed/v2/events` (Ereignisse mit Kennung `id`, optional Kette `chain` + `cseq`: idempotent, streng aufsteigend je
Kette), `GET /fed/v2/directory?since=&cfg=`, `GET /fed/v2/keys?handles=`. Zustellung zuerst **express** (synchron,
harte Frist `FED_EXPRESS_TIMEOUT`, Standard 5 s, eigener Thread-Pool), sonst Ausgangskorb mit Wiederholung in der
Reihenfolge der Kette. Server mit Protokoll 1 (2.1/2.2) bleiben voll verbunden: Teilen, Kommentare,
Benachrichtigungen wie bisher; Adressbuch und Schlüssel gibt es mit ihnen nicht.
