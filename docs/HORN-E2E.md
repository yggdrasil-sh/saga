# Gjallarhorn: Frame-E2E `saga-horn/v1`

Verbindlicher Vertrag für Web (`static/frame-e2e.js`), iOS/iPadOS/macOS und Android. Stand Saga 2.5 (Paket P4, hinter
dem Feature-Flag `calls.horn`, Standard aus). Testvektoren: `tests/vectors/horn_e2e.json`; Gegenproben:
`tools/horn_e2e_vectors.py` (Python, `cryptography`), `tools/horn_e2e_vectors.mjs` (Node, WebCrypto),
`tests/browser_horn_e2e.js` (Worker im Browser).

**Ziel:** Ein Gjallarhorn-Knoten leitet Rahmen nur weiter. Er sieht weder Ton noch Bild und kann keine Rahmen fälschen
oder alte wieder einspielen, ohne dass die Empfänger sie verwerfen. Die Schlüssel laufen nie über einen Knoten.

Begriffe: **Sender** = ein Gerät, das Ton/Bild schickt. Jeder Sender hat **einen** eigenen Schlüssel für alle seine
Spuren (Ton, Kamera mit allen Simulcast-Schichten, Bildschirm) und alle seine Beine zu Knoten.

## 1. Schlüssel

| Name | Größe | Bedeutung |
|---|---|---|
| `K` | 32 Byte | Schlüsselmaterial des Senders (Zufall oder Ratsche) |
| `n` | 0 … 2³¹−1 | Generation: zählt jeden Schlüsselwechsel des Senders in dieser Sitzung (Start `0`) |
| `kid` | 0 … 15 | `n mod 16`, steht in jedem Rahmen |
| `s` | 16 Hex-Zeichen | Sitzung des Senders: neu bei jedem Start (Neuladen, App-Neustart). Neue `s` ⇒ Empfänger verwerfen alles Alte dieses Senders |
| `FK` | 16 Byte | AES-128-Schlüssel für die Rahmen |

```
FK = HKDF-SHA256(IKM = K, salt = UTF8("saga-horn/v1/frame"), info = 128 × 0x00, L = 16)
K' = HKDF-SHA256(IKM = K, salt = UTF8("saga-horn-ratchet"),  info = leer,       L = 32)   (Ratsche)
```

- `FK` entspricht genau dem FrameCryptor-KeyProvider im HKDF-Modus mit `ratchetSalt = "saga-horn/v1/frame"`
  (`webrtc-sdk`: HKDF-SHA256, Salz = ratchetSalt, info = 128 Nullbytes, Länge 128 Bit). Die Apps geben `K` als
  Schlüsselmaterial an `setKey`, FrameCryptor leitet `FK` selbst ab.
- Die **Ratsche gehört Saga**, nicht FrameCryptor: FrameCryptors eigene Ratsche (`ratchetKey`, Ratsche beim
  Entschlüsselungsfehler) benutzt dasselbe Salz wie die Rahmenschlüssel. Dann sind die ersten 16 Byte von `K'` genau
  das alte `FK`, und ein Neuer könnte alles Vorherige lesen. Deshalb das eigene Salz `saga-horn-ratchet`.

## 2. Rahmenformat

Je **codiertem Rahmen** (nicht je RTP-Paket), nur VP8 und Opus (der Knoten handelt nichts anderes aus):

```
Ausgabe = Kopf (h Byte, Klartext) ‖ Chiffrat (Länge wie der Rest) ‖ Tag (16) ‖ IV (12) ‖ 0x0C ‖ kid
Chiffrat ‖ Tag = AES-128-GCM(FK, IV, AAD = Kopf, Klartext = Rahmen[h:])
```

| Rahmen | `h` (Kopf als AAD) | wie FrameCryptor |
|---|---|---|
| Opus | 1 (TOC-Byte) | ja |
| VP8-Schlüsselbild (`type: key`) | 10 (Rahmen-Tag 3 + Startcode 3 + Größe 4) | ja |
| VP8-Zwischenbild (`type: delta`) | 3 | ja |
| leer, `type: empty`, kürzer als `h`, anderer Codec | – | **verwerfen** (keine Ausgabe) |

- **Trailer = 14 Byte:** IV (12) ‖ IV-Länge (1, immer `0x0C`) ‖ kid (1). Das ist das FrameCryptor-/LiveKit-Format.
  Das Konzept nennt „IV(12) + kid(1)“; das Längenbyte gehört zum Format dazu. Zusatz je Rahmen: **30 Byte**
  (Tag 16 + Trailer 14). Bei Opus mit 40 ms (TCP-Weg, H5) sind das 6 kbit/s, mit 20 ms 12 kbit/s.
- **IV** = SSRC (u32 BE) ‖ RTP-Zeitstempel (u32 BE) ‖ Zähler (u32 BE).
  - SSRC und Zeitstempel sind die des **Senders** (`getMetadata().synchronizationSource`, `.rtpTimestamp`). Der
    Knoten schreibt beim Weiterleiten SSRC und Zeitstempel im RTP-Kopf um, die Werte in der IV bleiben.
  - **Zähler**: je (Schlüssel, SSRC) ab 0, +1 je Rahmen, nie wiederholt. Damit ist die IV je Schlüssel eindeutig,
    auch wenn ein neues Bein zufällig dieselbe SSRC bekommt.
  - FrameCryptor-Sender schreiben an die dritte Stelle `ts − (count mod 0xFFFF)`. Empfänger werten die dritte Stelle
    **nicht** aus, beide Formen sind gültig (Vektor `opus-framecryptor-iv`).
- Die IV ist über GCM mitgeschützt: Wer sie ändert, fällt bei der Tag-Prüfung durch.
- Transform bzw. FrameCryptor **vor dem ersten Rahmen** anhängen (Web: direkt nach `addTransceiver`, vor der
  Verhandlung; Apps: Cryptor erzeugen, bevor der Track gesetzt wird). Ein Rahmen ohne Transform ginge im Klartext raus.

## 3. Empfang

Für jeden Rahmen eines Senders (`from` = Geräte-ID, aus der signierten Zuordnung Spur → Gerät):

1. Codec bzw. `type` nicht erlaubt → `codec`. Länge < `h + 30` → `short`. Vorletztes Byte ≠ `0x0C` oder `kid` ≥ 16 → `format`.
2. Schlüssel wählen: gehaltene Generation `m` mit `m mod 16 = kid`.
   - Keine passende, aber `j = (kid − newest mod 16) mod 16` mit `1 ≤ j ≤ 4`: `K` der neuesten Generation `j`-mal
     ratschen und damit versuchen (**Ratsche beim ersten Rahmen**, so muss beim Beitritt niemand neue `fkey` an die
     Bestehenden schicken). Eingesetzt wird der geratschte Schlüssel erst, wenn ein Rahmen damit aufgeht.
   - Sonst → `nokey`.
3. Wiederholungsschutz prüfen (unten) → `replay`.
4. AES-GCM öffnen, Fehler → `auth`.
5. Erst jetzt: Zeitstempel ins Fenster aufnehmen. Ist das der erste Rahmen mit einer neueren Generation als bisher,
   laufen alle älteren Generationen dieses Senders **3 s später** ab (danach `nokey`).

Jeder Fehler heißt: **Rahmen verwerfen, nichts ausgeben**. Nie Klartext durchreichen, auch nicht ohne Schlüssel.
Gehalten werden höchstens die drei neuesten Generationen je Sender (`newest − 2 … newest`). Ein `fkey` mit
`n < newest − 1` (gleiche Sitzung) wird ignoriert.

**Wiederholungsschutz (Fenster 128)** je (Sender, Generation, SSRC aus der IV), über den RTP-Zeitstempel `t` aus der
IV (bei jedem Sender streng steigend, auch bei FrameCryptor). `max` = größter angenommener Wert, `W` = die 128 zuletzt
angenommenen Werte (in Annahme-Reihenfolge). Serielle Arithmetik: `d = (t − max) mod 2³²`.

- leeres Fenster → annehmen
- `1 ≤ d < 2³¹` (neuer) → annehmen, `max = t`
- `d = 0` oder `t ∈ W` → `replay`
- `|W| < 128` → annehmen
- sonst nur annehmen, wenn `t` jünger ist als der älteste Wert in `W` (Alter = `(max − v) mod 2³²`), sonst `replay`

Nach dem Annehmen `t` an `W` anhängen, bei mehr als 128 den ältesten Eintrag entfernen. Ein Rahmen, der mehr als 2³¹
Takte zurückliegt (≈ 6,6 h Video, 12,4 h Ton), erschiene als „neuer“. Deshalb wird jeder Schlüssel nach spätestens
**6 h** gewechselt (§4).

## 4. Verteilung: `fkey` und `fkey-ack`

Body-Typen im **vorhandenen versiegelten Signalweg** (`POST /api/calls/{id}/signal`, versiegelt je Zielgerät und vom
Gerät signiert; lokal `saga-call/v1`, in Chats der Stufe 3 und über Servergrenzen `saga-call/v2` mit Handles). Mehrere
Ziele gehen in **einer** Anfrage (`signals: {gerät: versiegelt, …}`). Der Server sieht weder Typ noch Inhalt. Ein
Knoten bekommt nie ein Signal.

```json
{"t":"fkey","n":3,"kid":3,"key":"<Base64, 32 Byte>","s":"0123456789abcdef"}
{"t":"fkey-ack","n":3,"s":"0123456789abcdef","ok":true}
```

- `fkey` gilt nur für den **signierenden Absender** (`from_device`). Ungültig und still ignoriert, wenn `n` keine
  ganze Zahl 0 … 2³¹−1 ist, `kid ≠ n mod 16`, `key` nicht genau 32 Byte Base64 (Standard, mit Padding) oder `s` nicht
  `[0-9a-f]{16}` ist (Vektoren `fkey.invalid`).
- `fkey-ack`: Empfänger bestätigt `n` der Sitzung `s` (`ok: true`). `ok: false` = „ich kann dich nicht lesen“ (siehe
  Nachforderung). Acks an mehrere Sender bündeln (≤ 100 ms sammeln).
- `fkey` geht nur an aktive Endpunkte des Anrufs mit Fähigkeit `horn`, deren Geräte-Zertifikat gültig ist (TOFU wie
  2.4). Ein vom Server untergeschobenes Gerät bekommt keinen Schlüssel und wird namentlich angezeigt.

| Ereignis | Sender | Empfänger |
|---|---|---|
| **Start** (eigenes Bein steht) | `K` zufällig, `n = 0`, sofort aktiv; `fkey` an alle | einsetzen, `fkey-ack` |
| **Beitritt** eines Geräts (Teilnehmerliste) | neuesten Schlüssel ratschen: `K' = R(K)`, `n+1`, **sofort** aktiv; `fkey` **nur an den Neuen** | Bestehende: Ratsche beim ersten Rahmen (§3). Apps setzen sie vorab ein (§6) |
| **Verlassen/Entfernen** | neues zufälliges `K`, `n+1`; `fkey` an alle Verbliebenen; **Umschalten nach 1,5 s** (früher, wenn alle bestätigt haben) | einsetzen; alter Schlüssel noch 3 s ab dem ersten Rahmen mit dem neuen |
| Beitritt, während ein Wechsel wartet | der **wartende** Schlüssel wird geratscht, Umschaltzeit bleibt; `fkey` an den Neuen | wie Beitritt |
| mehrere Abgänge kurz hintereinander | Abgänge innerhalb von 0,5 s lösen **einen** Wechsel aus | – |
| Schlüssel 6 h in Gebrauch | wie Verlassen | – |
| Neustart des Senders | neue `s`, `n = 0`, wie Start | neue `s` ⇒ alten Zustand des Senders verwerfen |
| Nachforderung | auf `fkey-ack ok:false` den **neuesten** Schlüssel (nie einen älteren, der vor dem Beitritt des Anfragenden galt) an diesen Empfänger | sendet `ok:false` (mit neuestem bekannten `n`, sonst `-1`), wenn ein Sender seit 2 s nur Unlesbares liefert, höchstens alle 5 s |

Ein Neuer kann nichts Vergangenes lesen, weil HKDF nicht umkehrbar ist. Wer geht, kann nach dem Umschalten nichts
mehr lesen; in den 1,5 s bis zum Umschalten ist der Ton noch mit dem alten Schlüssel verschlüsselt (bewusst, damit
niemand eine Lücke hört).

Signale je Ereignis bei `N` Geräten: Start `N(N−1)` über den ganzen Aufbau, Beitritt `2(N−1)`, Verlassen
`(N−1)(N−2)`. Das passt in das Raum-Budget von 2.4 (`pairs_budget`, `sig_rate`), weil jedes Gerät in **einer**
Anfrage an alle sendet.

## 5. Runensiegel

```
H     = SHA-256( UTF8(call_id) ‖ pk_1 ‖ … ‖ pk_m )     pk = Ed25519-Geräteschlüssel (sign_pk, 32 Byte) aller aktiven
                                                         Endpunkte inkl. des eigenen, bytweise sortiert, ohne Doppelte
v     = H[0..4] als 40-Bit-Zahl (big-endian)
r_i   = v mod 24, v = ⌊v / 24⌋   für i = 0 … 5          Anzeige r_0 … r_5 von links
```

`call_id` = die 32 Hex-Zeichen des Anrufs. ≈ 27,5 Bit. Das Siegel ändert sich bei jedem Beitritt und Verlassen.
Alphabet (älteres Futhark, Index 0 … 23; U+16A0 16A2 16A6 16A8 16B1 16B2 16B7 16B9 16BA 16BE 16C1 16C3 16C7 16C8 16C9
16CA 16CF 16D2 16D6 16D7 16DA 16DC 16DE 16DF) und Namen zum Vorlesen bzw. für Schriften ohne Runen:

```
ᚠ ᚢ ᚦ ᚨ ᚱ ᚲ ᚷ ᚹ ᚺ ᚾ ᛁ ᛃ ᛇ ᛈ ᛉ ᛊ ᛏ ᛒ ᛖ ᛗ ᛚ ᛜ ᛞ ᛟ
fehu uruz thurisaz ansuz raidho kaunan gebo wunjo hagalaz naudiz isa jera eihwaz perthro algiz sowilo tiwaz
berkanan ehwaz mannaz laguz ingwaz dagaz othala
```

## 6. Server, Web, Apps

**Server** (`saga/calls.py`, `saga/federation.py`, `saga/fed_calls.py`): Feature-Flag `calls.horn` (Einstellung,
Standard aus). An: `GET /api/calls/config` meldet `features` mit `horn` und `"e2e": "saga-horn/v1"`, die Föderation
meldet `horn` in `CAPS2` (Admin-Schalter „Anrufe“ je Instanz; neue Instanzen erfahren es beim nächsten `hello`).
Geräte melden `horn` in `caps`. Für `fkey`/`fkey-ack` braucht der Server nichts Eigenes: sie sind für ihn gewöhnliche
versiegelte Signale (Tests `tests/anrufe_horn.py`, `tests/foederation_anrufe_raum.py`).

**Web** (`static/frame-e2e.js`, eine Datei für Worker, Fenster und Node):
- `SagaFrameE2E.create()` startet den Worker `/static/frame-e2e.js` (CSP `worker-src 'self'`, Blob-Worker sind
  verboten) und hängt mit `sender(rtpSender, kind)` / `receiver(rtpReceiver, kind, from)` an:
  `RTCRtpScriptTransform`, sonst `createEncodedStreams` (PeerConnection mit `encodedInsertableStreams: true`).
  Ohne beides: `create()` wirft, das Gerät darf dann nicht in einen Knoten-Anruf (Mesh wie 2.4).
- `own('start' | 'ratchet' | 'rekey')` → `{n, kid, key, s}` zum Versiegeln, `peer(from, n, key, s)`, `gone(from)`,
  `ownKeys()` (für die Nachforderung), `switchNow()`, Ereignis `need {from, n, s}`, `runes(call_id, sign_pks)`.
- Messung (headless Chromium 141, 2 Kerne, `tests/browser_horn_e2e.js`): reine Ver-/Entschlüsselung im Worker
  0,03–0,05 ms je 6-KB-Rahmen (720p bei 1,5 Mbit/s), 0,01–0,02 ms je Opus-Rahmen, 0,12–0,3 ms je 30 KB. Im laufenden
  Loopback-Anruf 720p, wenn Kodierer, Dekodierer und Canvas dieselben 2 Kerne belegen: 0,13–0,2 ms Wartezeit je
  Rahmen. Eine Kopie je Rahmen (WebCrypto schreibt nicht in fremde Puffer). Geprüft ist nur Chromium;
  Safari (≥ 15.4) und Firefox (≥ 117) haben `RTCRtpScriptTransform`, sind aber noch nicht gemessen.

**Apps (FrameCryptor)**, eine `KeyProvider`-Instanz je Anruf:

| Einstellung | Wert |
|---|---|
| `sharedKey` | `false` (je Sender ein Schlüssel; `participantId` = Geräte-ID, 32 Hex) |
| `ratchetSalt` | `UTF8("saga-horn/v1/frame")` |
| `keyDerivationAlgorithm` | **HKDF** |
| `ratchetWindowSize` | `0` (FrameCryptor-Ratsche aus, siehe §1) |
| `failureTolerance` | `-1` (ein eingeschleuster kaputter Rahmen sperrt den Schlüssel nicht) |
| `keyRingSize` | `16` |
| `discardFrameWhenCryptorNotReady` | `true` |
| `uncryptedMagicBytes` / SIF-Trailer | keine (`nil`) |
| Algorithmus | `AES_GCM` |

- **Senden:** `FrameCryptor` für jeden `RtpSender` (Ton, Kamera, Bildschirm) mit der eigenen Geräte-ID.
  `setKey(own, kid, K)` setzt den Schlüssel ein; welcher gesendet wird, bestimmt `setKeyIndex(kid)` am Cryptor.
  Bei „Verlassen“ also `setKey` sofort, `setKeyIndex` erst nach 1,5 s. **Nie `setEnabled(false)`** im Knoten-Anruf:
  FrameCryptor reicht dann Klartext durch.
- **Empfangen:** `FrameCryptor` je `RtpReceiver` mit der Geräte-ID des Senders. `fkey` → `setKey(from, kid, K)`.
  Die Ratsche beim ersten Rahmen kann FrameCryptor nicht, und er meldet nicht, wann ein `kid` zum ersten Mal kommt.
  Die App führt deshalb je Sender `newest` aus `fkey` **und** der Teilnehmerliste (jeder Beitritt = `newest + 1` bei
  allen Sendern) und hält die vier folgenden Ratschenstufen **vorab** eingesetzt (`newest+1 … newest+4`). Ein späteres
  `fkey` für denselben `kid` überschreibt sie.
- **Ablauf:** FrameCryptor kann Schlüssel nicht löschen. 3 s nach dem Ereignis, das `newest` erhöht hat (Beitritt bzw.
  erster Rahmen nach `fkey`, ersatzweise Erhalt + 1,5 s), überschreibt die App den Platz der älteren Generation mit 32
  Zufallsbytes. `need` kommt aus dem Beobachter (`MissingKey`/`DecryptionFailed` länger als 2 s).
- **Wiederholungsschutz** fehlt in FrameCryptor (Unterschied 5 unten). Die Apps bekommen ihn erst mit einem eigenen
  Transformer. Bis dahin ist das ein bekanntes Restrisiko (Knoten könnte alte Rahmen derselben Generation einspielen).

## 7. Unterschiede zum FrameCryptor-Standard (webrtc-sdk / LiveKit)

| # | FrameCryptor | `saga-horn/v1` | Folge für Apps |
|---|---|---|---|
| 1 | Ableitung standardmäßig PBKDF2-SHA256 (100 000 Runden) | **HKDF** (Option `keyDerivationAlgorithm`, ab webrtc-sdk 144.7559.01) | Bibliothek mit HKDF-Option nötig (§10) |
| 2 | eigene Ratsche mit demselben Salz wie die Rahmenschlüssel | eigene Saga-Ratsche (§1) | `ratchetWindowSize = 0`, Ratsche in der App |
| 3 | Ratsche behält den `kid` | Ratsche = `kid + 1` | vorab einsetzen (§6) |
| 4 | dritter IV-Teil `ts − (count mod 0xFFFF)` | Zähler je (Schlüssel, SSRC) | keine; Empfänger werten ihn nicht aus |
| 5 | kein Wiederholungsschutz | Fenster 128 über den RTP-Zeitstempel der IV | Web prüft; Apps später |
| 6 | Ablauf über Neu-Setzen | alter Schlüssel 3 s nach dem ersten Rahmen mit dem neuen | überschreiben (§6) |
| 7 | leere Rahmen ggf. durchgereicht | immer verwerfen | `discardFrameWhenCryptorNotReady = true` |
| 8 | H.264/AV1/H.265 | nur VP8 und Opus | Knoten handelt nur diese aus |

Gleich sind: AES-128-GCM, Kopf als AAD mit denselben Längen, Trailer `IV ‖ 0x0C ‖ kid`, Schlüsselplatz je `kid`,
Schlüssel je Teilnehmer.

## 8. Testvektoren und wie die Apps prüfen

`tests/vectors/horn_e2e.json` (Erzeugung `python3 tools/horn_e2e_vectors.py gen`, feste Seeds):

| Teil | Inhalt |
|---|---|
| `keys` | Ratschenkette `K0 → K1 → K2` mit `FK` je Generation |
| `frames` | VP8-Schlüsselbild, VP8-Zwischenbild, Opus, Zwischenbild mit `n = 1`, Opus mit FrameCryptor-IV: Klartext, IV, Ausgabe |
| `cases` | Empfänger-Fälle: ohne Schlüssel, falscher `kid`, `kid` auf anderen Schlüssel, Kopf (AAD) / Chiffrat / Tag / IV manipuliert, Kopfart vertauscht, IV-Länge falsch, `kid` ≥ 16, zu kurz, Wiederholung, Fenster 128 über den 2³²-Überlauf, Ratsche beim ersten Rahmen (eine und zwei Stufen), Ablauf nach 3 s |
| `window` | Fensterfolge nur aus Zeitstempeln |
| `runes` | drei Geräte, vier Geräte, andere Reihenfolge mit Doppelten |
| `fkey` | gültiger Body und acht ungültige |

Pflicht in der CI jeder App (wie die Chat-Vektoren):
1. **Ableitungen** mit der Plattform-Kryptografie (CryptoKit `HKDF<SHA256>`; Android eigenes HMAC-SHA256 bzw. Tink):
   `FK` und `K'` für alle `keys`, Runensiegel für alle `runes`, `fkey`-Prüfung.
2. **Rahmenformat** mit `AES.GCM` (CryptoKit) bzw. `Cipher("AES/GCM/NoPadding")`: alle `frames` öffnen und
   (bis auf `iv_mode: fc`) bitgleich erzeugen. Das sichert das gemeinsame Verständnis des FrameCryptor-Formats.
3. **FrameCryptor selbst** (Instrumented/UI-Test): Loopback zweier PeerConnections im Prozess, Sender mit
   FrameCryptor (`K0` aus den Vektoren, Einstellungen §6), Empfänger mit FrameCryptor → Bild und Ton; dann
   Empfänger **ohne** Schlüssel → nichts dekodiert. Zuletzt gegen das Web: Horn-Probe-Knoten mit festem Testschlüssel,
   App sendet, Browser (`static/frame-e2e.js`) empfängt und umgekehrt (Interop-Test vor dem Umstieg, Risiko R5).

## 9. Grenzen

- Der Knoten sieht weiterhin Metadaten: Rahmengrößen und -zeitpunkte, Schlüsselwechsel (am `kid`), Schichtwahl.
- Bis zum Umschalten (1,5 s) kann ein gegangenes Gerät noch mithören.
- Apps ohne eigenen Transformer haben keinen Wiederholungsschutz (§6).
- Das Flag ist in 2.5 nur die Grundlage: Knoten-Beine, `topo = horn` und das Verteilen in `calls.js`/`horn.js`
  kommen mit P3/P5.

## 10. Bibliotheken der Apps (Stand 20.10.2026)

| Plattform | heute | FrameCryptor | HKDF-Option | Empfehlung |
|---|---|---|---|---|
| Android | `io.getstream:stream-webrtc-android` 1.3.10 (11.09.2025, letzter Tag) | ja: `org.webrtc.FrameCryptor`, `FrameCryptorFactory.createFrameCryptorKeyProvider(sharedKey, ratchetSalt, ratchetWindowSize, uncryptedMagicBytes, failureTolerance, keyRingSize, discardFrameWhenCryptorNotReady)`, nur `FrameCryptorAlgorithm.AES_GCM` | **nein** (nur PBKDF2, 100 000 Runden je `setKey`) | Wechsel auf `io.github.webrtc-sdk:android` ≥ 144.7559.01 (Maven Central, gleiches Paket `org.webrtc`, Fabrik mit `FrameCryptorKeyDerivationAlgorithm.HKDF`); aktuell 144.7559.15 bzw. 150.7871.01 |
| iOS/iPadOS/macOS | `stasel/WebRTC` 154.0.0 (SPM) | nein | – | `https://github.com/webrtc-sdk/Specs` (SPM-Produkt `WebRTC`) ≥ 144.7559.01; `RTCFrameCryptor`, `RTCFrameCryptorKeyProvider initWithRatchetSalt:ratchetWindowSize:sharedKeyMode:uncryptedMagicBytes:failureTolerance:keyRingSize:discardFrameWhenCryptorNotReady:keyDerivationAlgorithm:` mit `RTCKeyDerivationAlgorithmHKDF` |

Beim Wechsel `stasel/WebRTC` → `webrtc-sdk` (Apple) beachten:
- Modul und Produkt heißen beide `WebRTC`, Klassen ohne Präfix (`RTC_OBJC_TYPE_PREFIX` leer): `import WebRTC` bleibt,
  nur `ios/project.yml` ändert URL und Version. xcframework mit iOS-, Simulator-, Catalyst- und macOS-Teil
  (iOS 13, macOS 10.15).
- Basis wird **älter**: stasel 154 folgt libwebrtc M154, webrtc-sdk liegt bei M144/M150 (mit eigenen Patches für
  Audio, Kamera, Bildschirm). Neuere Google-Korrekturen fehlen; Regressionen in 1:1 zuerst prüfen (Risiko R4).
- **CallKit/Audio-Session:** `RTCAudioSession` mit `useManualAudio`, `isAudioEnabled`, `audioSessionDidActivate:` und
  `RTCAudioSessionConfiguration` gibt es unverändert; die Fabrik ohne ADM-Angabe nimmt wie bisher das
  AudioUnit-Gerät (`kPlatformDefaultAudio`). Den AVAudioEngine-ADM (`initWithAudioDeviceModuleType:…AudioEngine`)
  **nicht** einschalten, ohne CallKit, Ducking und Bluetooth neu zu testen.
- **macOS:** auch webrtc-sdk hat dort kein `RTCAudioSession`; neu vorhanden sind `RTCAudioDevice`/`RTCAudioDeviceModule`
  und `RTCDesktopCapturer` (die heutige Lücke in `ScreenCapture.swift` lässt sich damit später schließen).
- FrameCryptor vor dem ersten Rahmen anhängen (§2), sonst geht das erste Bild im Klartext.

Belege: GetStream/webrtc-android Tag `1.3.10` (`stream-webrtc-android/src/main/java/org/webrtc/FrameCryptor*.java`,
`libjingle_peerconnection_so.so` mit `FrameCryptorTransformer::encryptFrame`); webrtc-sdk/webrtc `m137_release`
Commit „feat: HkdfSha256 key derivation. (#224)“ (06.03.2026), `m144_release` „patch from m137_release (#217)“
(12.03.2026), `api/crypto/frame_crypto_transformer.{h,cc}` (`KeyDerivationAlgorithm`, `DeriveHkdfSha256FromSecret`
mit info = 128 Nullbytes, `PKCS5_PBKDF2_HMAC(…, 100000, EVP_sha256())`, Trailer `getIvSize()` + `key_index_`);
webrtc-sdk/Specs `CHANGES.md` und `WebRTC.xcframework` 144.7559.01/.15 (Header `RTCFrameCryptorKeyProvider.h` mit
`RTCKeyDerivationAlgorithmHKDF`, geprüft); Maven Central `io.github.webrtc-sdk:android` 144.7559.01 (13.03.2026) bis
150.7871.01 (Quellstand `sdk/android/api/org/webrtc/FrameCryptorKeyDerivationAlgorithm.java`; das AAR selbst ist vor
dem Wechsel noch zu prüfen).
