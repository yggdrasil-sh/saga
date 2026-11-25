# Push-Relais und Apple-Mitteilungen Ende-zu-Ende (`saga-relay/1`, `saga-push-e2e/1`)

Verbindlicher Vertrag für den Saga-Server (`saga/push.py`, `saga/push_relay.py`), den Relais-Dienst (`relay/`) und die
iOS-/iPadOS-App (`Shared/PushE2E.swift`, Notification Service Extension). Stand Saga 2.6 (Bugs-DB 2262), Feature-Kennung
`pushrelay1` in `/api/version`. Testvektoren: `tests/vectors/push_e2e.json`, erzeugt und geprüft von
`tools/push_e2e_vectors.py` (unabhängige Umsetzung mit `cryptography`), Gegenprobe gegen den Server:
`tests/push_relais_vertrag.py`.

**Warum:** Apple (APNs) nimmt Mitteilungen an die Store-App nur mit dem Schlüssel des Apple-Kontos an, das die App
veröffentlicht. Fremde Saga-Server schicken ihre Mitteilungen deshalb über ein Relais, das dieses Konto betreibt. Das
Relais sieht **keinen Inhalt**: der Server verschlüsselt für die App, das Relais reicht nur Steuerköpfe und Chiffrat weiter.

Beispiele in diesem Dokument nutzen `relay.example.org` (Relais), `saga.example.org` (Saga-Server) und das Bundle
`org.example.saga`. Echte Namen stehen nur in der Konfiguration des Relais.

Begriffe: **Server** = ein Saga-Server; **Relais** = der Relais-Dienst; **App** = Saga-App auf iPhone/iPad; **B** = ein
Bundle-Bezeichner der App (z. B. `org.example.saga`); **Abo** = Zeile `push_sub` (Art `apns` bzw. `apns-voip`).

## 1. Versionen

| Was | Kennung | Wo |
|---|---|---|
| Relais-API | Pfad `/v1/…`, `v:1` in Anfragen, `/v1/info` → `v:[1]` | Server ↔ Relais |
| Signatur | RFC 9421 mit Tag `saga-relay/1` | jede Anfrage außer `/v1/info` |
| Ende-zu-Ende | `saga.e2e = 1`, Format `saga-push-e2e/1` | Server ↔ App |
| Fähigkeit | `pushrelay1` (FEATURES), `server.apns_e2e = 1` in `/api/push` | Server → App |

Neue Felder dürfen hinzukommen; Empfänger ignorieren unbekannte Felder. Was sich nicht abwärtsverträglich ändert, bekommt
eine neue Version (`/v2/`, `e2e:2`, `saga-relay/2`). Das Relais meldet in `/v1/info` alle Versionen, die es spricht.

## 2. Signatur der Anfragen

Verfahren genau wie die Föderation (`saga/fedsig.py`, RFC 9421, Ed25519, Content-Digest RFC 9530), nur mit eigenem Tag:

```
Signature-Input: saga=("@method" "@authority" "@path" "@query" "content-digest" "saga-actor");created=…;expires=…;nonce="…";keyid="s_…";alg="ed25519";tag="saga-relay/1"
Signature:       saga=:<Base64 64 Byte>:
Content-Digest:  sha-256=:<Base64>:
Saga-Actor:      -
```

- `fedsig.sign_request(…, tag=fedsig.RELAY_TAG)` / `fedsig.verify_request(…, tag=fedsig.RELAY_TAG)`. Standard der Funktionen
  bleibt `saga-fed/1`: eine Relais-Signatur gilt nie in der Föderation und umgekehrt (der Tag steht in der Signaturbasis).
- `keyid` = vom Relais vergebene Server-Kennung `server_id` (`s_` + 16 Zeichen aus `a-z2-7`); bei der Anmeldung `"reg"`.
- `@authority` = Host[:Port] des Relais, wie der Server ihn aufruft (Standard-Ports weggelassen, klein). Das Relais prüft
  gegen seinen konfigurierten öffentlichen Namen.
- `Saga-Actor` ist immer `-` (das Relais kennt keine Personen).
- Zeitfenster: `created` höchstens 300 s von der Uhr des Relais entfernt, `expires − created ≤ 300`.
- Nonce: das Relais merkt sich jede Nonce 10 Minuten; erneut benutzt → `401 {"error":"auth","reason":"replay"}`.
- Antworten des Relais sind **nicht** signiert (TLS genügt, sie enthalten nichts Geheimes).
- Schlüssel des Servers: eigenes Ed25519-Paar nur fürs Relais (nicht der Föderationsschlüssel), privater Teil verschlüsselt in
  `setting push_relay`. Öffentlicher Schlüssel überall als Base64url ohne `=` (32 Byte).

## 3. Endpunkte des Relais

Nur HTTPS (443), JSON (`Content-Type: application/json`), Anfragekörper höchstens **8 KB** (sonst 413).

### 3.1 `GET /v1/info` (offen, ohne Signatur)

```json
{"v": [1], "push_types": ["alert", "background", "voip", "liveactivity", "widgets"],
 "topics": ["org.example.saga"], "registration": "verify",
 "tiers": {"neu":   {"ring": {"per_min": 30,  "per_token_min": 10}, "alert": {"per_min": 120,  "per_token_min": 30, "per_day": 5000},
                     "bg":   {"per_min": 60,  "per_token_min": 6}},
           "aktiv": {"ring": {"per_min": 120, "per_token_min": 10}, "alert": {"per_min": 1200, "per_token_min": 30, "per_day": 100000},
                     "bg":   {"per_min": 600, "per_token_min": 6}}},
 "max_payload": 4096, "max_payload_voip": 5120}
```

- `topics`: die Bundles **B**, für die das Relais Apple-Schlüssel hat (nicht die abgeleiteten `.voip`-Topics).
- `registration`: `verify` (Domain-Nachweis schaltet frei) oder `approve` (immer Freischaltung von Hand).

### 3.2 `POST /v1/servers` – Anmeldung

Signiert mit dem **neuen** Schlüssel (Besitznachweis), `keyid="reg"`. Das Relais prüft die Signatur mit `pub` aus dem Körper.

```json
{"v": 1, "pub": "<Base64url 32 Byte>", "host": "saga.example.org", "saga_version": "2.6.0"}
```

Antwort `201` (neu) bzw. `200` (Schlüssel schon bekannt → **dieselbe** `server_id`, idempotent):

```json
{"server_id": "s_abcdefghijklmnop", "state": "active", "stage": "neu", "limits": {…wie tiers.neu…}, "topics": ["org.example.saga"]}
```

- **Domain-Nachweis**: das Relais ruft einmal `https://<host>/.well-known/saga-push-relay` ab (nur öffentliche Adressen,
  nur https, 5 s, höchstens 4 KB, keine Weiterleitungen) und erwartet `{"v": 1, "key": "<pub>"}`. Passt `key` → `state:"active"`,
  sonst (oder bei `registration:"approve"`) `state:"pending"` bis zur Freischaltung (`relayctl approve`).
- `host`: Hostname (ohne Schema/Pfad, optional `:port`), 1–253 Zeichen.
- Höchstens 5 Anmeldungen je IP-Adresse und Stunde (sonst `429` mit `scope:"register"`).
- Der Server liefert `/.well-known/saga-push-relay` aus, sobald er einen Relais-Schlüssel hat (unabhängig von der Föderation),
  sonst 404.

### 3.3 `GET /v1/servers/me`

→ `200 {"server_id", "state": "active"|"pending"|"blocked", "stage": "neu"|"aktiv"|"eigen"|"gesperrt", "limits", "topics", "created"}`
(`created` Unix-Sekunden). Der Server ruft das stündlich auf (und `/v1/info`), um Zustand und Grenzen anzuzeigen.

### 3.4 `POST /v1/servers/me/rotate` – Schlüsselwechsel

Signiert mit dem **alten** Schlüssel. Körper:

```json
{"new_pub": "<Base64url 32 Byte>", "proof": "<Base64url Ed25519-Signatur, 64 Byte>"}
```

`proof` = Signatur des **neuen** Schlüssels über die UTF-8-Bytes `saga-relay-rotate/1|<server_id>|<new_pub>`
(Vektor: `rotate` in `push_e2e.json`). → `200 {"ok": true}`; danach gilt nur noch der neue Schlüssel, `server_id` bleibt.

### 3.5 `DELETE /v1/servers/me` – Abmelden

→ `200 {"ok": true}`. Das Relais vergisst Server-Kennung und Schlüssel.

### 3.6 `POST /v1/push` – eine Mitteilung

```json
{"token": "<Gerätetoken hex>", "env": "production", "topic": "org.example.saga", "push_type": "alert",
 "priority": 10, "expiration": 1798776000, "collapse_id": "c1SSbcsv3l_EHbzFZFEu5bf", "rate": "alert",
 "aps": {"alert": {"title-loc-key": "SAGA_PUSH_TITLE", "loc-key": "SAGA_PUSH_BODY"}, "mutable-content": 1, "sound": "default"},
 "saga": {"e2e": 1, "k": "rJ1YACNZ", "c": "<Base64url Chiffrat>"}}
```

Regeln (Relais prüft, Verstoß → `400 {"error":"bad","reason":…}`):

| Feld | Regel |
|---|---|
| `token` | Hex, 64–200 Zeichen |
| `env` | `production` oder `sandbox` |
| `push_type` | aus `/v1/info push_types`; sonst 400 (z. B. `mdm`, `location`, `complication`) |
| `topic` | je Typ aus den konfigurierten **B**: `alert`/`background`: `B`; `voip`: `B.voip`; `liveactivity`: `B.push-type.liveactivity`; `widgets`: `B.push-type.widgets` (nur wenn im Relais eingeschaltet) |
| `priority` | `10`, `5` oder `1`; `background` und `widgets` nur `5`/`1` |
| `expiration` | Unix-Sekunden, `0` oder höchstens 30 Tage in der Zukunft |
| `collapse_id` | optional, höchstens 64 Byte, `[A-Za-z0-9_-]` |
| `rate` | `voip` → `ring` (Pflicht); `alert` → `alert` oder `ring`; `background`/`liveactivity`/`widgets` → `bg` |
| `aps` | Objekt, **nur** diese Schlüssel: `alert` (Objekt nur mit `title-loc-key`, `loc-key`, Werte aus `SAGA_PUSH_TITLE`, `SAGA_PUSH_BODY`, `SAGA_PUSH_CALL`), `mutable-content` (1), `content-available` (1), `sound` (`"default"`), `interruption-level` (`active`, `passive`, `time-sensitive`). **Freitext (`alert` als Text, `title`, `body`, `subtitle`, `*-loc-args`, `thread-id`, `category`, `badge` …) → 400.** `voip`: `aps` = `{}`; `background`: nur `content-available` |
| `saga` | Objekt genau `{"e2e": 1, "k": <8 Zeichen [A-Za-z0-9_-]>, "c": <Base64url, höchstens 4000 Zeichen>}` |

- Unbekannte Felder der Anfrage ignoriert das Relais (Erweiterungen), es reicht sie **nicht** weiter.
- Das Relais reicht `push_type`, `topic`, `priority`, `expiration`, `collapse_id` **unverändert** als `apns-push-type`,
  `apns-topic`, `apns-priority`, `apns-expiration`, `apns-collapse-id` an Apple weiter; `authorization` ist sein eigenes
  Anbieter-Token (ES256). APNs-Körper ist **genau** `{"aps": aps, "saga": saga}` (kompaktes JSON).
- Größe des APNs-Körpers höchstens `max_payload` (4096 Byte), bei `voip` `max_payload_voip` (5120) → sonst `413`.
- `liveactivity` und `widgets` sind in v1 vorgesehen (Weiche 2188/3.0), Inhalte nur in `saga`; genauere `aps`-Regeln für sie
  kommen mit dem ersten Nutzer als Vertragsänderung (§11).

## 4. Antworten und was der Server daraus macht

| Relais | Bedeutung | Ergebnis im Server |
|---|---|---|
| `200 {"ok": true}` | an Apple übergeben | `ok` |
| `410 {"error":"gone","reason"}` | Token tot (APNs 410, `BadDeviceToken`, `Unregistered`, `DeviceTokenNotForTopic`) | `gone` → Abo löschen |
| `400`/`413 {"error":"bad","reason"}` | Nutzlast/Topic/Feld falsch (auch andere APNs-400) | `fail` (zählt `fails`) |
| `429 {"error":"rate","tier","scope":"server"\|"token"\|"apns"\|"register","retry_after"}` | Grenze erreicht | `hold`, nächster Versuch nach `retry_after` (höchstens 1 h) |
| `401 {"error":"auth","reason"}` | Signatur/Zeit/Nonce falsch oder Server unbekannt | `hold`, automatische Neuanmeldung höchstens 1× je Stunde |
| `403 {"error":"pending"\|"blocked"}` | noch nicht freigeschaltet / gesperrt | `hold`, Hinweis an die Admins |
| `502`/`503`, Zeitüberschreitung, Verbindung weg | Relais oder Apple gestört (auch APNs 5xx, APNs 403 mit dem Schlüssel des Relais) | `hold`, Schutzschalter |

Genauer (Relais 2262-2b): bei `429` ist `tier` der Name der Stufe (`neu`, `aktiv`, `eigen`); APNs selbst meldet 429 →
`scope:"apns"`, `retry_after: 60`; alle APNs-Verbindungsplätze des Relais belegt → `scope:"apns"`, `retry_after: 5`.
`scope` `server`, `apns` und `register` halten alle Relais-Aufträge des Servers an, `token` nur den betroffenen Auftrag.
`DELETE /v1/servers/me` eines gesperrten Servers → `403 {"error":"blocked"}` (Abmelden geht dann nicht, Zustand bleibt
„gesperrt“).

**`hold`** (neu in `saga/push.py`): das Abo ist schuldlos. Es wird nur `last_err` gesetzt, `fails` bleibt, nichts wird
gelöscht. Der Auftrag bleibt in der Warteschlange (`tries` wird zurückgezählt, damit neue Chat-Nachrichten weiter in den
wartenden Auftrag gebündelt werden) und entfällt erst nach seiner Lebensdauer (`ttl` der Mitteilung bzw. 4 h), klingelnde
Anrufe nach der Klingeldauer.

## 5. Ende-zu-Ende zwischen Server und App

### 5.1 Abo (App → Server)

`POST /api/push/subscriptions` mit Art `apns` bzw. `apns-voip` bekommt zwei optionale Felder – die App schickt sie nur,
wenn `/api/push` `server.apns_e2e = 1` meldet:

```json
{"type": "apns", "token": "<hex>", "env": "production", "name": "iPhone", "lang": "de",
 "keys": {"p256dh": "<Base64url 65 Byte, P-256 unkomprimiert>", "auth": "<Base64url 16 Byte>"},
 "topic": "org.example.saga"}
```

- Je Konto und Server ein eigenes Schlüsselpaar + `auth`; dasselbe Paar für `apns` und `apns-voip` desselben Kontos.
- `topic`: Bundle-Bezeichner der App (`[A-Za-z0-9.-]{3,200}`), immer aus der laufenden App (sonst `DeviceTokenNotForTopic`).
- Erneuern mit gleichem Token = gleiches Abo; fehlt ein Feld, bleibt der gespeicherte Wert. Neue Schlüssel (alle 30 Tage,
  nach Neuinstallation) ersetzen die alten; die App hebt alte Schlüssel 7 Tage auf (Auswahl über `k`).
- Abos **ohne** Schlüssel (Apps 2.5) laufen nur direkt mit eigenem APNs-Schlüssel wie bisher, **nie** über das Relais.

`/api/push` meldet zusätzlich: `server.apns_e2e: 1`, `server.apns_topics: [B…]` (Bundles, für die dieser Server zustellen
kann – eigener Schlüssel und/oder Relais), `server.apns_relay: {"state": "ok"|"down"|"pending"|"blocked"|"auth", "topics": […]}`
oder `null`; `server.apns` bleibt wie bisher (nur eigener Schlüssel). `devices[]` bekommt `via: "direct"|"relay"|null` und `e2e`.

### 5.2 Verschlüsselung

Innerer Text = das bisherige Feld-Set der Mitteilung als kompaktes JSON (UTF-8), genau das Objekt aus `push.message()`:
`title, body, url, tag, kind, ts, v, lang` und je nach Art `thread, call, chat, media, caller, group, dm, ttl, actor, n,
group_key, update, removed, silent, digest` (`call` = Anrufkennung, 32 Hex-Zeichen). Die App liest es wie bisher die flachen APNs-Felder (unbekannte ignorieren).

Verfahren: **RFC 8291** (Web Push, `aes128gcm` nach RFC 8188) mit dem Schlüssel des Abos (`p256dh`, `auth`), ein einziger
Datensatz, `rs = 4096`, Kopf `salt(16) | rs(4) | idlen(1)=65 | as_pub(65)` – Byte für Byte wie bei Web Push
(`push.encrypt`). Dazu **Auffüllen** nach RFC 8188 Abschnitt 2: Klartext-Datensatz = `inhalt | 0x02 | 0x00…`, Länge

```
pad_to(n) = kleinste Stufe aus (512, 1024, 2048) mit n + 1 ≤ Stufe, sonst ⌈(n + 1) / 512⌉ · 512        (n = Länge des Inhalts)
```

(`push.pad_size`, `push.encrypt(…, pad_to=…)`). Innerer Text höchstens **`E2E_MAX = 2400` Byte**, sonst wird nicht gesendet
(`fail "zu gross"`). Das Chiffrat ist damit höchstens 2662 Byte (Base64url 3550 Zeichen).

Entschlüsseln (App): Kopf lesen, ECDH mit dem eigenen privaten Schlüssel, HKDF wie RFC 8291, AES-128-GCM; danach Nullen am
Ende entfernen, das letzte Byte muss `0x02` sein (sonst Fehler). Mehr als ein Datensatz, falsches `idlen`, falsches Tag → Fehler.

### 5.3 APNs-Körper

```json
{"aps": {…sichtbarer Teil…}, "saga": {"e2e": 1, "k": "<8 Zeichen>", "c": "<Base64url Chiffrat>"}}
```

- `saga.k` = erste 8 Zeichen von `Base64url(SHA-256(p256dh roh, 65 Byte))` – welcher Schlüssel (mehrere Konten/Server
  auf einem Gerät, alte Schlüssel). `push.e2e_kid`.
- Sichtbarer Teil (`aps`) **ohne Inhalte**:

| Art | `apns-push-type` | `priority` | `rate` | `aps` |
|---|---|---|---|---|
| normale Mitteilung | `alert` | 10 | `alert` | `{"alert": {"title-loc-key": "SAGA_PUSH_TITLE", "loc-key": "SAGA_PUSH_BODY"}, "mutable-content": 1, "sound": "default"}` (bei `silent` ohne `sound`) |
| klingelnder Anruf ohne VoIP-Abo (`call`, `call.group`) | `alert` | 10 | `ring` | wie oben, aber `loc-key: "SAGA_PUSH_CALL"` und `"interruption-level": "time-sensitive"` |
| klingelnder Anruf an VoIP-Abo | `voip` | 10 | `ring` | `{}` |
| Aktualisierung (`update`) | `background` | 5 | `bg` | `{"content-available": 1}` |

- `apns-expiration` = jetzt + (`ttl` der Mitteilung oder 4 h).
- **`voip` nur für `kind ∈ {call, call.group}`** – sonst sendet der Server nicht (`fail`). Aufträge der Kollegin (`job.*`)
  sind nie `voip`.
- **Keine `thread-id`** im Klartext; die Notification Service Extension setzt `threadIdentifier = thread ?? tag`.
- **`apns-collapse-id` undurchsichtig**: `"c" + Base64url(HMAC-SHA256(auth roh, "saga-collapse/1|" + T))[:22]` mit
  `T = tag` bzw. `"u" + tag` bei Aktualisierungen (`push.e2e_collapse`). Gleicher tag → gleiche Kennung, der tag selbst
  bleibt verborgen.
- Die NSE entschlüsselt, setzt `title`/`body`, `userInfo` = innere Felder, Ton nur ohne `silent`, bei Anrufen
  `interruptionLevel`/Kategorie. Kann sie nicht entschlüsseln (unbekanntes `k`, Fehler, Zeit abgelaufen, Gerät noch nie
  entsperrt), zeigt sie die Texte der Loc-Keys (neutraler Platzhalter, z. B. „Saga – Neue Mitteilung“).
- Abos mit Schlüsseln bekommen **auch auf dem eigenen APNs-Weg** nur diesen Körper (Einstellung `push.apns_e2e_direct`,
  Standard an).

### 5.4 Welcher Weg (Server)

Je Abo: (1) eigener APNs-Schlüssel eingerichtet **und** (`topic` leer oder = eigenes Bundle) → direkt; (2) sonst Relais
eingeschaltet und nutzbar, Abo mit Schlüsseln und `topic ∈ relay.topics` → Relais; (3) sonst `fail "APNs nicht eingerichtet"`.

## 6. Was das Relais sieht und speichert

Sieht: Server-Kennung und Host, Gerätetoken, `env`, Topic, Push-Typ, Priorität, Ablaufzeit, undurchsichtige collapse-id,
Ratenstufe, `aps` (nur Loc-Keys und Schalter), Länge des Chiffrats, Uhrzeit. **Nie**: Namen, Texte, Anmeldenamen, Chat-,
Seiten- oder Auftragskennungen.

Log je Anfrage (stdout, eine JSON-Zeile): `ts, server_id, push_type, rate, prio, status, apns_reason, ms` – kein Token, keine
Nutzlast; nur bei Fehlern `tok_h` = 8 Hex-Zeichen aus HMAC-SHA256(Token, Tagessalz). Speicher (SQLite): `servers`, `nonces`
(10 min), `daily_stats` (30 Tage). Ratenzähler nur im Speicher, geschlüsselt mit HMAC(Token).

## 7. Ratenstufen

Token-Bucket je Server und je Token, Werte in `/v1/info tiers` (Stufe `neu` ab Anmeldung, `aktiv` nach 7 Tagen ohne Sperre
oder per `relayctl`, `eigen` = Werte je Server, `gesperrt`). `ring` hat einen **eigenen Topf und 4 reservierte gleichzeitige
APNs-Verbindungsplätze** und wartet nie hinter `alert`/`bg`. Der Server stellt Anrufe in seiner Warteschlange zuerst zu.

## 8. Missbrauchsschutz

Schlüssel je Server mit Besitznachweis; Domain-Nachweis oder Freischaltung; Zeitfenster und Nonce; Erlaubnislisten (Typen,
Topics, `aps` ohne Freitext); Grenzen je Server und Token; Anmelde-Bremse je IP; Größengrenzen. Werkzeug `relayctl`
(`list, show, approve, block, unblock, stage, limits, stats`).

## 9. „Relais weg“ (Server)

- Schutzschalter: nach einem Fehler 15 s „down“, bei weiteren Fehlern verdoppelt bis 300 s; währenddessen ergibt jede
  Relais-Zustellung sofort `hold` ohne Netzverkehr (Verbindungsaufbau 3 s, Lesen 8 s). Web Push, ntfy und die eigene
  Verbindung werden nicht ausgebremst.
- Nichts geht verloren: die Glocke ist unabhängig vom Push; Aufträge warten (`hold`) bis zu ihrer Lebensdauer; nach der
  Rückkehr kommt je Chat bzw. tag nur der neueste Stand.
- Admins: `setting push_relay_state` (`ok|down|pending|blocked|auth`, `since`, `last_err`); nach 10 min durchgehend „down“
  bzw. sofort bei `pending`/`blocked` **eine** Glocke `push.relay` je Störung; bei Erholung als gelesen markiert.
- App: `/api/push` → `server.apns_relay.state`; bei `down` zeigt die App „Mitteilungen ans iPhone sind gerade gestört –
  Saga zeigt alles in der Glocke.“

## 10. Testvektoren (`tests/vectors/push_e2e.json`)

| Schlüssel | Inhalt |
|---|---|
| `params` | `rs`, `pad_steps`, `pad_round`, `e2e_max`, `ttl_default`, `collapse_info`, `rotate_info`, `now`, `loc_keys`, `ring_kinds` |
| `keys` | fester App-Schlüssel: `ua_priv` (32 Byte Skalar), `ua_pub`, `auth`, `k` |
| `rfc8291` | RFC 8291 Anhang A (ohne Auffüllen), unverändert |
| `cases[]` | je Fall: `msg` (innere Felder), `plain` (genaue Bytes), `pad_to`, `salt`, `as_priv` (fester Absender-Schlüssel), `push_type`, `priority`, `ttl`, `expiration` (= `now + ttl`), `rate`, `collapse_tag`, `collapse_id`, `aps`, `apns_body` (vollständiger APNs-Körper mit Chiffrat) |
| `pad` | `[n, pad_to(n)]` |
| `collapse` | `tag → collapse_id` (u. a. `job-1`/`job-2` verschieden) |
| `rotate` | Besitznachweis beim Schlüsselwechsel (`new_priv_seed`, `new_pub`, `signed`, `proof`) |
| `bad[]` | Chiffrate, die die App verwerfen muss (verändert, fremder Schlüssel, ohne Trenner, abgeschnitten) |

Fälle: Chat, Kommentar (mit `thread`), Anruf über VoIP, Gruppenanruf als `alert` (iPad), Aktualisierung (`background`),
stille Sammelmeldung, Testmitteilung, Auftrag der Kollegin (`job.done`, nie `voip`), groß (Stufen 1024, 2048, 2560).

Prüfen: `python3 tools/push_e2e_vectors.py check` (unabhängig), `tests/push_relais_vertrag.py` (gegen `saga.push`/`saga.fedsig`);
iOS: Kopie nach `ios/Tests/vectors/push_e2e.json`.

## 11. Änderungen am Vertrag

Jede Änderung nach dem ersten Stand bekommt einen eigenen Commit (`2262-1b …`) und eine Zeile hier.

| Stand | Änderung |
|---|---|
| 2262-1 | erster Stand |
| 2262-1b | Abschnitt 4: `tier`/`scope`/`retry_after` bei 429 und `DELETE /v1/servers/me` bei gesperrtem Server genauer beschrieben (so baut es das Relais; keine neue Bedeutung). |
| 2262-1b | Vektoren `anruf_voip`, `gruppenanruf_alert` und `collapse` mit echter Anrufkennung (32 Hex-Zeichen wie `chat_call.id`; die App verwirft kürzere). Format und Verfahren unverändert. |
