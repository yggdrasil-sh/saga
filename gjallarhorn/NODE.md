# Gjallarhorn-Knoten (Betriebsart `node`) – Stand P1 + P2 + P3 + P5 + P6/P7 + P9/P10

Der Knoten ist der Weiterleiter (SFU) für große Saga-Gruppenanrufe (Konzept v2, §1, §2.1). `gjallarhorn node` nutzt
dieselben Weichen wie die Horn-Probe. Statt des Spiegels arbeitet der echte Weiterleiter aus `internal/sfu`. Beine
gibt es nur mit einem Medien-Token von Saga (P3, unten). Zum Testen ohne Saga gibt es `HORN_DEV=1`.

Platzhalter: `horn-1.example.org`.

## Weichen (P1)

| Port | erstes Byte | Ziel |
|---|---|---|
| 443/TCP | `0x16` | TLS: ALPN `http/1.1` → HTTPS/WSS (Steuerung, `/probe`), ALPN `stun.turn` oder STUN im Strom → TURN-TLS (Relay nur zum Knoten selbst) |
| 443/TCP | `0x00`/`0x01` | ICE-TCP (RFC 4571) → eigener `netx.ICEMux` (keine Allokation je Paket) |
| 443/TCP | `\r` bzw. `P` | PROXY-Kopf v2 bzw. v1 (nur mit `HORN_PROXY_PROTOCOL=1` oder `auto`) |
| 443/UDP (optional) | RFC 9443 | STUN/DTLS/RTP an pion. Einfache STUN-Bindings ohne USERNAME beantwortet die Weiche selbst (Erreichbarkeitsprüfung, höchstens 200/s) |

- Selbsttest beim Start: Bindings an die öffentlichen Adressen, sonst lokal. Das Ergebnis steht in
  `/api/session` → `udp_check`. Die Gegenprobe von außen macht Saga beim Koppeln (P3) über denselben Beantworter.
- Höchstens 512 unzugeordnete Verbindungen. Fristen: erstes Byte 5 s, TLS-Handshake 10 s.
- TCP: `TCP_NODELAY`, `TCP_NOTSENT_LOWAT` 16 KB, BBR wenn erlaubt.
- **Nachläufer** gegen lange TCP-Staus (`HORN_TAILPROBE_MS`, Standard 10, `0` = aus): Geht das letzte Segment eines
  Schubs verloren, merkt Linux das erst über die Tail-Loss-Probe nach 2 RTT, bei nur einem Segment unterwegs nach
  2 RTT + 200 ms. Der Knoten schickt deshalb 10 ms nach dem Schub eine STUN Binding Indication (28 Byte). Damit
  erkennt RACK den Verlust schon nach etwa 1,25 RTT.
  - Der Nachläufer läuft nur auf Verbindungen mit Wiederholungen in den letzten 30 s und einer RTT ab 20 ms
    (`TCP_INFO`). Auf sauberen Wegen kostet er also nichts.
  - Auf TURN-TLS beginnt er erst nach dem ersten ChannelData.

## Weiterleiter (P2)

- **Beine und Rollen:** Auf TCP-Wegen hat ein Gerät zwei Beine (H1): die Ton-Leitung (`role=audio`) und die
  Bild-Leitung (`role=video`). Auf UDP ist es ein Bein (`both`) mit BUNDLE. Je `leg_ref` gibt es ein Bein; ein
  zweites ersetzt das erste.
- **Empfangsplätze:** Die Reihenfolge der recvonly-Transceiver im Angebot ist fest: erst Ton (höchstens 3), dann
  Sprecher, dann Bildschirm (falls angefragt), dann Kacheln. Last-N kappt die Kacheln: Desktop 9, Handy 4.
  - Die Geräteklasse kommt aus dem Token. Der Hinweis `device=mobile` des Clients kann sie nur senken.
- **Wer ist wo:** Jedes Paket trägt die CSRC des Absenders. Browser lesen sie per `receiver.getContributingSources()`
  bzw. im Encoded-Transform aus `getMetadata().contributingSources`. Die Zuordnung CSRC → Teilnehmende liefert die
  Steuerung (`Room.Peers`, `Config.OnLayout`).
- **Top-3-Ton** per `ssrc-audio-level`. Der Hauptsprecher wird mit Hysterese gewählt (mindestens 1,5 s, ein Neuer
  muss 1,5 s am lautesten oder 1,4-mal lauter sein). Kacheln sind nach letzter Sprache geordnet, die Plätze bleiben fest.
- **Schichten:**
  - UDP: bis zu 3 Schichten (`l`/`m`/`h`).
  - TCP: höchstens 2.
  - Kacheln auf TCP: nur die untere Schicht und höchstens 10 Bilder/s. Dafür lässt der Knoten zeitliche Schichten
    (TID) weg; PictureID und TL0PICIDX bleiben lückenlos.
  - Der Sprecher bekommt mehr, wenn die Schätzung reicht.
  - Ist zu wenig Rate da, werden Kacheln von hinten pausiert. Nach oben geht es nur mit 15 % Reserve und nach einer
    Sperrzeit, die sich bei Fehlversuchen verdoppelt (2 bis 30 s).
  - Hysterese gegen Pendeln (Horn-Fix, `layerStep`): Ohne Stau bleibt die laufende Schicht bis 10 % über der Schätzung
    (kurze Ratenspitzen, z. B. Schlüsselbilder, schalten nicht herunter); bei Stau (Sperrzeit) sofort herunter – so
    weit, wie die Schätzung es verlangt (von `h` auf `m`, wenn sie `m` trägt; früher immer bis `l`). Nach
    einem Wechsel nach unten geht derselbe Platz für dieselbe Quelle frühestens nach 4 s wieder hoch.
    Fällt ein Platz binnen 2 s nach dem Hochschalten wegen des Budgets wieder zurück (Rate der Zielschicht
    unterschätzt, z. B. kurz nach einer Auflösungsänderung des Senders), verdoppelt sich dieser Aufschub je
    Fehlversuch (8, 16, höchstens 32 s); hielt die höhere Schicht länger als 2 s, gelten wieder 4 s
    (`TestBudgetFehlversuchAufschub`).
  - Hysterese gegen flatternde Schichten (`internal/sfu/flap.go`, je Quelle, damit für eigene Beine und Brücken
    gleich): Senkt der Sender eine Schicht selbst (libwebrtc bei CPU-Last/Wärme) und liefert sie nur noch zeitweise,
    fällt sie sofort aus der Auswahl, sobald sie nicht mehr live ist (keine Pakete in der letzten Sekunde bzw. über
    die Brücke „aus“ gemeldet), während eine tiefere live ist. Wieder genommen wird sie erst nach **3 s am Stück
    live**; flattert sie, bevor sie nach der Rückkehr 30 s stabil war, verdoppelt sich die Wartezeit (bis 60 s), nach
    30 s Stabilität beginnt sie wieder bei 3 s. Fallen alle Schichten zugleich weg (Sender, Netz, Freigabe
    pausiert), gibt es keine Wartezeit. Budget-Wechsel (Schätzung, Kontingent) bleiben bei `layerStep`. Die Brücke
    meldet `on` ungefiltert; die Hysterese wirkt am Knoten, der die Schicht wählt.
  - PLI-Bremse: höchstens eine Anforderung je Quelle und `fwd.PLIInterval` (500 ms), für alle Empfänger zusammen;
    Abo-Wechsel (`Out.Assign`, auch über die Brücke: `pli` bzw. neuer Ausgang auf der Gegenseite) laufen durch
    dieselbe Bremse (`TestPLIAbstandBeiWechsel`).
  - Grund je Wechsel im Ereignis `layout`: Slots tragen nur in der Meldung des Wechsels selbst `why`
    (`budget` | `gone` = bisherige Schicht nicht mehr live | `back` = Rückkehr nach der Hysterese), `src_kbit`
    (gemessene Rate der Quelle; `gone`: der weggefallenen, sonst der neuen), `live_ms` (`gone`: Live-Zeit vor dem
    Ausfall, `back`: am Stück) und `hold_ms` (`gone`: Wartezeit bis zur Rückkehr, `back`: die abgewartete). Alte
    Geräte ignorieren die Felder. Das Web hängt sie an den Platz im Diagnose-Ereignis `layers` an
    (`s0:l3@ab12!gone,31k,12.4s,w3s`).
- **Lebensdauer der Beine (Horn-Fix):** Beine der Betriebsart `node` leben so lange wie der Anruf. Geräumt wird nur,
  was gescheitert/geschlossen ist (nach 30 s) bzw. nie verbunden wurde (nach 60 s). Die frühere Grenze von 15 min
  gilt nur noch für Spiegel-/Bench-Beine. Jedes Bein schreibt `bein auf`/`bein zu` ins Protokoll – gekürzte ID,
  Betriebsart, Weg, Dauer, Grund (`aufraeumer: alter|gescheitert|nie verbunden`, `verbindung failed|closed`,
  `knoten: …`, `abgemeldet`, `knoten beendet`, `aufbau fehlgeschlagen`); keine Adressen, kein ufrag.
- **Schätzung je Bein:**
  - TCP: aus dem Alter der eigenen Warteschlange. Dank `TCP_NOTSENT_LOWAT` liegt der Stau im Knoten.
  - UDP: zusätzlich GCC (TWCC), als Stausignal. Ohne Probepakete ist GCC anwendungsbegrenzt und taugt nicht als
    Obergrenze. Stau ist ein GCC-Ziel unter 90 % der gesendeten Rate – außer es läuft der Rate nur nach
    (`estimator.lagging`, `internal/sfu/control.go`). pion-GCC senkt sein Ziel nur bei Stau (Verzögerung: auf 85 % der
    empfangenen Rate; Verlust über 10 %) und hebt es sonst mit rund 8 %/s. Springt die gesendete Rate (höhere
    Schicht, Kachel dazu, Schlüsselbild), liegt ein stehendes oder steigendes Ziel deshalb eine Weile darunter,
    ohne dass die Leitung eng wäre. Diese **Schonfrist** gilt nur, solange das Ziel nicht fällt (weder im Takt des
    Ratensprungs noch danach um mehr als 3 % unter seinen Höchstwert), je Sekunde um mindestens 1,5 % steigt,
    insgesamt höchstens 15 s lang und nicht während einer Sperrzeit; sonst zählt das Ziel wie bisher als Stau.
    Solange sie läuft, kommt nichts Neues dazu (kein weiterer Schritt nach oben).
    Scheitert sie, fällt die Schätzung 2 s lang nicht unter die Rate vor dem Ratensprung – aber nur, wenn die Folge
    mit einem Ratensprung begann (Rate über 1,1 × ihres geglätteten Werts). Bricht die Leitung bei gleichbleibender
    Rate ein, folgt die Schätzung dem Ziel sofort.
  - UDP, Deckel: Die freie Schätzung wächst höchstens bis `1,6 × gesendete Rate + 300 kbit/s` (mindestens der
    Startwert) – und auf UDP mindestens bis zum Bedarf des nächsten Schritts nach oben (bzw. der laufenden Auswahl)
    mit 15 % Reserve und 5 % Zuschlag, zuzüglich des Tonabzugs, höchstens das Kontingent. Der Wechsel selbst ist
    die Probe; scheitert er, meldet GCC das. Ohne diese Regel blieb ein Empfänger im 1:1 auf `m` (≈ 500 kbit/s →
    Deckel 1200 kbit/s; Browser-`h` braucht mit Reserve ≈ 1430 kbit/s). TCP behält den alten Deckel.
  - UDP, nach oben: nur, wenn das GCC-Ziel mindestens 1,25 × der gesendeten Rate ist. Nach einem Rücksprung steigt
    das Ziel auch bei anhaltendem Stau zunächst wieder (der Verlust-Teil holt mit 5 % je 200 ms zum
    Verzögerungs-Teil auf); erst von einem erholten Ziel aus ist ein Stau nach dem Schritt als Fall erkennbar.
  - UDP, gescheiterter Versuch: Endet die Schonfrist nach einem Hochschalten mit Stau, zählt der Rückfall als
    Fehlversuch (Aufschub 8/16/32 s, siehe oben), auch wenn er später als 2 s kommt. Die Schätzung fällt dabei 2 s
    lang nicht unter die Rate vor dem Versuch – der erste Rücksprung des Ziels (85 % eines Messfensters, das noch
    zur Hälfte die alte Rate enthält) sagt nichts über die Stufe davor.
  - Tests: `internal/sfu/schaetzer_test.go` (freie Leitung, enge Leitung, Stau-Schub, Einzelregeln), mit einem
    Modell des GCC-Ziels nach Messwerten am echten `gcc.SendSideBWE`.
- **Vorrang und Verwerfen (H3):**
  - Die Warteschlange je Bein arbeitet nach Vorrang: Ton > Bildschirm > Sprecher > Kacheln.
  - Ist ein Rahmen älter als 150 ms, verwirft der Knoten Bilder bis zum nächsten Schlüsselbild und fordert eine PLI
    an (höchstens 1 je Quelle und 500 ms). Ton wird nie verworfen.
- **NACK/RTX und TWCC:** NACK/RTX gibt es nur auf UDP. TWCC-Rückmeldung für den Uplink der Geräte auf allen Wegen.
  Opus auf TCP: ptime 40, ohne FEC, mit DTX.
- **Rahmen bleiben opak:** Gelesen wird nur der VP8-Descriptor (Schlüsselbild, TID, PictureID) und das P-Bit. Der
  E2E-Trailer wird nie angefasst.

### Erwartungen an Clients (für P5 und die Apps)

- **Simulcast auf TCP-Wegen:** `l` mit `scaleResolutionDownBy` 4, 20 fps und `scalabilityMode: 'L1T2'` (der Knoten
  nimmt davon 10 fps für Kacheln) sowie `m` mit 30 fps `L1T3`. Auf UDP zusätzlich `h`.
- Bildschirm: Die Stream-ID beginnt mit `screen`.
- **Jitter-Puffer:** Auf TCP-Wegen für empfangene Bilder `receiver.jitterBufferTarget = 150` (ms) setzen, siehe
  Messung unten. Geräte, die das nicht können (Apps mit webrtc-sdk M150), bekommt der Knoten dasselbe über die
  Kopferweiterung **playout-delay** (unten).
- Ton und Bild auf getrennten PeerConnections über TCP (H1).

### Hinter einer TLS-Durchreiche (SNI-Proxy: Traefik `HostSNI`, NetBird, HAProxy `mode tcp`) – A1

H2, H3, der Nachläufer und die TCP-Schätzung wirken nur auf **Sockets, die der Knoten selbst annimmt**
(`internal/netx/demux.go` `handle` → `Tune`). Hinter einer Durchreiche ist das die Verbindung *vom Proxy*. Die
Verbindung *zum Gerät* gehört dem Proxy und hat dessen Standard: kein `TCP_NOTSENT_LOWAT`, Sendepuffer per Autotuning
bis `tcp_wmem`-Maximum (oft 4 MB, bei 600 kbit/s fast eine Minute Stau), meist CUBIC. Folgen (Labor 2.11.): Der Stau
liegt im Proxy statt im Knoten. Das Alter der Warteschlange bleibt klein, also verwirft der Knoten nichts (H3), und die
Schätzung bleibt beim Startwert `est=1200`. Der Nachläufer sieht RTT ≈ 0 ohne Wiederholungen und bleibt aus. Beim
Bildschirmteilen wächst die RTT auf mehrere Sekunden.

Gegenmittel ohne Codeänderung: Auf dem **Proxy-Host** die Voreinstellung für alle Sockets setzen. Der Proxy blockiert
dann beim Schreiben und liest langsamer vom Knoten. So kommt der Rückstau beim Knoten an, und H3 und die Schätzung
greifen wieder:

```
# /etc/sysctl.d/90-saga-horn.conf auf dem Rechner mit dem SNI-Proxy (bzw. dem NetBird-Proxy/VPS)
net.ipv4.tcp_notsent_lowat = 16384
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
```

Läuft der Proxy auf demselben Host wie der Knoten, reicht dieselbe Datei dort. Die Wirkung (Freezes/min und RTT beim
Bildschirmteilen über TLS, vorher und nachher) ist bisher nur im Labor messbar. Die Web-Clients deckeln auf TCP/TLS
zusätzlich selbst (`static/horn.js` `K.TX`/`TxGov`).

## Einfrieren auf TCP (5G-Messung, G4)

Eine Messung über TURN-TLS/5G mit dem Spiegel ergab 5,45 Einfrieren je Bildplatz und 5 min. Ursache ist
Head-of-Line-Blocking auf dem TCP-Weg, nicht fehlende Schlüsselbilder:

- Im Regressionstest `TestFreezeLossyTCP` (80 ms RTT, 1,5 % Verlust, Nachbildung `internal/impair`, denn netem fehlt
  hier) gibt es 0 Pausen, 0 verworfene Rahmen und eine PLI-Antwort p95 von 128 ms.
- Ein verlorenes Segment hält alles dahinter 1,25–2,5 RTT auf. Die Einfrier-Schwelle der Browser liegt bei
  „Mittel + 150 ms“, ein 30-fps-Bild friert also schon bei etwa 150 ms Stau.

| Maßnahme | Wirkung in der Nachbildung |
|---|---|
| Kacheln 10 fps (Schwelle 300 ms) | Kacheln frieren kaum noch |
| Nachläufer | längster Stau 300 → 160 ms, bei 2 % Verlust ein Drittel weniger Einfrieren (streng gemessen) |
| `jitterBufferTarget` 150 ms im Client | 0 Einfrieren bei 1,5 % und 2 % Verlust (Ziel ≤ 1 je 5 min) |
| vorbeugende Schlüsselbilder | keine (auf TCP geht nichts verloren), daher aus (`KeyframeEvery`) |
| kleinere Sprecher-Bildrate | kaum (Schwelle ist Mittel + 150 ms), daher optional (`TCPSpeakerFPS`) |

### playout-delay auf TCP-/TLS-Beinen (P9)

Die Apps (webrtc-sdk M150) können `jitterBufferTarget` nicht setzen – das war die Gegenmaßnahme gegen das Einfrieren
über TCP (5G-Messung: 5,45 → 0 Einfrieren je 5 min). Deshalb setzt der Knoten auf TCP- und TLS-Beinen für Bild die
RTP-Kopferweiterung `http://www.webrtc.org/experiments/rtp-hdrext/playout-delay` mit **min 150 ms, max 400 ms**
(`HORN_PLAYOUT_DELAY="150,400"`, `0` = aus):

- Ausgehandelt im Knotenangebot nur für Video und nur auf den TCP-/TURN-APIs (nicht UDP). Bietet das Gerät die
  Erweiterung nicht an, bleibt sie aus (`TrackLocalContext.HeaderExtensions`).
- Geschrieben auf allen Paketen von **Schlüsselbildern** je Ausgang (der Empfänger behält den Wert bis zum nächsten; nach
  jedem Schicht- oder Quellwechsel kommt ohnehin ein Schlüsselbild), ohne Allokation (Kopf und Nutzlast im Ausgang
  wiederverwendet; `TestPlayoutDelay`). Die Brücke reicht Pakete unverändert weiter und setzt nichts.
- Nachweis in Chromium (`e2e/playout.sh`, 2 Geräte über ICE-TCP, Empfänger ohne `jitterBufferTarget`, 10 s
  `getStats`): ohne Erweiterung `jitterBufferTargetDelay` je Rahmen 11 ms / `jitterBufferDelay` 8 ms, mit
  138 ms / 130 ms. libwebrtc übernimmt den Wert vom SFU.

## Interne Go-API für die Steuerung (P3)

```go
n := sfu.NewNode(sfu.Config{Name: "horn-1.example.org", Verifier: v /* Ed25519, P3 */, OnLayout: f, OnLegClosed: g})
r, _ := n.CreateRoom(callRef, &sfu.Limits{MaxPeers: 50})   // idempotent
g, r, err := n.Admit(token, sfu.PathTCP)                    // Token prüfen, Raum muss existieren
l, err := r.AddLeg(g, path, sfu.Want{Audio: 3, Speaker: true, Tiles: 9})
r.RemoveLeg(legRef); r.RemovePeer(peer); n.CloseRoom(callRef)
n.Load()                                                    // Lastmeldung §2.6
```

- `sfu.Verifier` ist eine Schnittstelle: Ed25519-Prüfer für Saga-Token (`horntoken.go`), HMAC-Prüfer nur im
  Entwicklungsmodus.
- `Grant` enthält `Node`, `Room` (call_ref), `Peer` (peer_ref), `Leg` (leg_ref), `Role`, `Pub` (a/v/s), `MaxKbit`,
  `UDPOK`, `Device`, `Exp` und `ID` (jti).
- HTTP im Knoten:
  - `POST /api/sfu/leg {token, path, offer, audio, speaker, screen, tiles, device}` → `{id, answer, csrc, slots}`
  - `POST /api/sfu/turn {token}` → TURN-Zugang (10 min)
  - `GET /api/sfu/leg/{id}` → Belegung und Schätzung

## Saga-Steuerung (P3)

- **Eingebauter Knoten:** saga-media startet `gjallarhorn node`, solange das Feature-Flag `calls.horn` an ist
  (`media/horn.go`). Umgebung: `HORN_MODE=node`, `HORN_LISTEN=:HORN_PORT` (Standard 8443), `HORN_DATA=STATE_DIR/horn`,
  `HORN_SAGA_CTL=ws://127.0.0.1:<intern>/api/horn/ctl` und das Geheimnis `HORN_CTL_KEY`. Weitere `HORN_*` (Name,
  `HORN_PUBLIC_URL`, `HORN_TLS_*`, `HORN_UDP` …) reicht saga-media durch. Saga selbst bekommt nie UDP.
  - UDP-Kandidat = öffentliche Adresse (`HORN_PUBLIC_IP` bzw. Adressen von `HORN_NAME`) + **lokaler** UDP-Port
    (`HORN_UDP_LISTEN`, sonst `HORN_PORT`). Hinter einer TLS-Durchreiche (Traefik `HostSNI`) kommt kein UDP an: den Port
    direkt mit gleicher Nummer veröffentlichen. Der Selbsttest (`udp_check` im `hello`: `ok`/`aus`/`fehlt`) steht in
    Admin → Anrufe in der Spalte „Weg“.
- **Steuerkanal `horn-ctl/1`** (`ctl.go`, Gegenstück `saga/horn.py`): ausgehende WebSocket-Verbindung. `challenge` →
  `hello` (signiert mit dem Knotenschlüssel `HORN_DATA/node.key`) → `welcome {node_id, saga_pub, origin, rooms}`, danach
  Folgenummern je Richtung. Saga → Knoten: `open`, `close`, `kick`, `ping`. Knoten → Saga: `load` (alle 5 s: cpu, mem,
  legs, calls, udp_share, fwd_p95_us, drops, state), `closed`, `pong`. Neuverbindung mit Backoff 1–30 s; beim
  Verbinden schließt der Knoten Räume, die Saga nicht mehr kennt. Gekoppelte Knoten (Erweitern) folgen mit P6.
- **Medien-Token** (`horntoken.go`): `h1.<payload>.<sig>`, Ed25519 über `"saga-horn-token/1." + payload`, Schlüssel
  aus `welcome` (oder `HORN_SAGA_PUB`). Geprüft: Signatur, Form der Kennungen, Lebensdauer ≤ 120 s, Knotenname,
  Ablauf, UDP nur mit `udp_ok`. Jedes Token gilt **einmal** je Zweck (Bein bzw. TURN-Zugang, `jti`).
- **Räume:** Ein gültiges Token legt einen unbekannten Raum selbst an (die Anlage über `open` darf später kommen).
  Geschlossene Räume und entfernte Teilnehmende (`kick`) bleiben 3 min gesperrt, damit noch gültige Token nicht
  wieder hereinkommen. Leere Räume schließt der Knoten nach 2 min.
- **CORS:** `/api/sfu/*` und `/probe` nehmen die Herkunft der eigenen Saga an (`welcome.origin`, `HORN_SAGA_ORIGIN`).
  Saga ergänzt dafür `connect-src` der Seiten um die Knotenadressen.
- **Ohne Steuerkanal** (fester Saga-Schlüssel `HORN_SAGA_PUB`): Ein gültiges Saga-Token legt seinen Raum ebenso selbst
  an (P5; bis P6 für Tests mit einem zweiten Knoten). Räume schließt dann nur der Knoten selbst (leer nach 2 min).
- **Mitschnitt nur für Tests:** `HORN_CAPTURE=<ordner>` schreibt die ersten 3000 Pakete jeder eingehenden Spur roh
  heraus (`tests/anrufe_horn_knoten.py` prüft damit, dass nur verschlüsselte Rahmen ankommen).

## Weitere Knoten (P6/P7): Kopplung, Steuerung, Updates, Zertifikate

**Kopplung („Erweitern“):** Das Deploy-Skript aus dem Admin-Bereich setzt `HORN_SAGA`, `HORN_PAIR` (einmaliger Code,
30 min) und `HORN_NAME`. Beim ersten Start erzeugt der Knoten `HORN_DATA/node.key`, fragt `GET /api/horn/whoami` je
über IPv4 und IPv6, macht den UDP-Selbsttest und ruft `POST /api/horn/pair` mit
`sig = Ed25519(node_sk, "horn-pair/1\n" + code + "\n" + name)`. Saga prüft zurück:
`GET https://<ip>:<port>/api/horn/echo?n=<nonce>` (TLS ohne SNI, Rückfall-Zertifikat) muss mit
`Ed25519(node_sk, "horn-echo/1\n" + n + "\n" + name)` antworten; UDP per STUN-Binding. Antwort
`{node_id, saga_pub, ctl_url, name}` landet in `HORN_DATA/pair.json`; `saga_pub` bleibt angeheftet. Fehler 403/410
sind endgültig (neuen Code erzeugen), 424 (nicht erreichbar) versucht der Knoten mit Backoff erneut.

**Steuerkanal** wie beim eingebauten Knoten, aber ohne gemeinsames Geheimnis: Saga erkennt den Knoten am Schlüssel aus
der Kopplung (gesperrt: 4410, anderer Name: 4409). Zusätzlich: Saga → Knoten `drain {on}`, `peers {solo}`,
`version {want}`, `cert {cert}`, `acme_txt` (Antwort), `remove {sig}` mit
`Ed25519(saga_sk, "saga-horn-remove/1\n" + node_id + "\n" + name)`; Knoten → Saga `addr {v4, v6}` (alle 10 min neu
ermittelt), `cert {mode, state, not_after, err}`, `update {state, want, have, err}`, `acme_txt {rid, op, name, value}`.
Die Lastmeldung trägt `up`/`down` (Mbit/s, aus den Bytezählern) und etwa 180 Byte.

**Leeren:** Während des Leerens lehnt der Knoten neue Beine ab (429), laufende bleiben, Saga meldet den Geräten
`call.horn_move` und schlägt andere Knoten vor.

**Wächter und Selbst-Update** (ohne Docker-Socket): `gjallarhorn watch` startet `node` als Kind. Zielversion =
Saga-Version. Der Knoten lädt `<HORN_UPDATE_BASE>/v<ver>/gjallarhorn-linux-<arch>` und `.sig` (Vorabversionen wie
`2.5.0-rc.131` vom Branch testing: `<HORN_UPDATE_BASE>/horn-testing/gjallarhorn-<ver>-linux-<arch>` – rollendes
Vorab-Release, das die CI bei jedem testing-Push ersetzt), prüft gegen den festen
Release-Schlüssel und legt `HORN_DATA/bin/next` ab. Mit weiteren Knoten: Leeren, Neustart wenn leer oder nach 10 min;
als einziger Knoten erst ohne Beine (Hinweis nach 24 h). Exit 75 → der Wächter prüft erneut und startet `bin/current`;
stürzt es dreimal binnen 30 s ab, fällt er auf das Image-Binär zurück (`bin/bad`). Nach `remove` startet er nicht mehr
(`HORN_DATA/removed`).

**Release-Schlüssel (einmalig von den Maintainern einzurichten):**
1. `python3 tools/horn-release.py keygen` (offline) → `secret: …` als GitHub-Secret `HORN_RELEASE_KEY`, `pub: …` in
   `gjallarhorn/release.pub` eintragen und committen.
2. In der Release-Action je Architektur `gjallarhorn/build.sh <version>` und
   `HORN_RELEASE_KEY=${{ secrets.HORN_RELEASE_KEY }} python3 tools/horn-release.py sign <version> dist/gjallarhorn-linux-<arch>`
   ausführen und `gjallarhorn-linux-<arch>` + `.sig` als Release-Assets hochladen (noch nicht in `release.yml`).
3. Ohne Schlüssel melden Knoten `update: aus` und die systemd-Variante des Deploy-Skripts ist gesperrt.

**Zertifikate (P7):** Gekoppelte Knoten starten mit einem selbst signierten Rückfall-Zertifikat (nur für fremde oder
fehlende SNI und bis zur Ausstellung). Mit Cloudflare-Token in Saga gilt `dns01`: Der Knoten macht ACME selbst und lässt
Saga nur `_acme-challenge.<eigener Name>` setzen und wieder löschen (`HORN_ACME_URL`, `HORN_ACME_DNS_WAIT`, Standard
20 s). Ohne Token `alpn` (TLS-ALPN-01 auf demselben Port). Eigene Dateien in `/data/tls/` ohne `dns01.json` bleiben
unberührt. Erneuerung 30 Tage vor Ablauf.

Umgebung: `HORN_SAGA`, `HORN_PAIR`, `HORN_NAME`, `HORN_LINK_MBIT` (Upload für die Auslastung), `HORN_PUBLIC_PORT`,
`HORN_UPDATE_BASE`, `HORN_UPDATE_RETRY` (nur Tests), `HORN_ACME_URL`, `HORN_ACME_EMAIL`, `HORN_ACME_DNS_WAIT`,
`HORN_INSECURE_SAGA=1` (http zu Saga außerhalb von localhost, nur Tests).

## Ereigniskanal, Leeren, Bildschirm (P5)

- `GET /api/sfu/leg/{id}/events` (WebSocket, Herkunft wie `/probe`; die Bein-Kennung ist die Berechtigung):
  `hello {leg, peers, slots, est_kbit, state}`, danach `peers` (Teilnehmende geändert: beim Beitritt sofort aus
  `Room.AddLeg`, also bevor das neue Gerät Medien schickt; beim Gehen im nächsten Takt), `layout` (aus
  `Config.OnLayout`), `state` (`ok`/`draining`), `closed {why}` (Bein vom Knoten beendet) und `pong {n}` auf
  `{t:"ping", n}` (höchstens alle 150 ms). 32 Nachrichten Puffer je Verbindung; wer nicht liest, fliegt raus.
  `sfu.Config.OnPeers` meldet die Änderungen der Teilnehmenden.
- `POST /api/sfu/leg` liefert zusätzlich `peers` (alle Teilnehmenden mit CSRC) und `state`.
- **Leeren:** `SIGUSR1` = keine neuen Beine (429 „knoten wird geleert“), Lastmeldung `state: draining`, Ereignis an alle
  Geräte, die daraufhin make-before-break den Knoten wechseln; `SIGUSR2` hebt es auf. Ebenso der Steuerbefehl `drain` aus dem
  Admin-Bereich bzw. das Update (P6, `server.setDrain`).
- **Bildschirm:** Eine Spur gilt als Bildschirm, wenn ihre Stream-ID mit `screen` beginnt **oder** ihre mid im
  `screen_mid` der Bein-Anfrage steht (Browser können die Stream-ID nicht wählen).
- **Grenze je Adresse:** höchstens `HORN_MAX_PER_IP` Beine (Standard 12) je Quell-Adresse, insgesamt `HORN_MAX_LEGS`
  (Standard 48). Hinter einem gemeinsamen NAT (Büro, Schule) mit vielen Geräten höher setzen.


## Kaskade „Bifröst“ (P9) und Kontingente (P10)

Sind Teilnehmende eines Anrufs auf mehreren Knoten, verbindet Saga die Knoten sternförmig (Mittelpunkt = Knoten mit den
meisten Endpunkten, `saga/horn_bridge.py`). Die meisten Anrufe bleiben dank Bonus „Anruf schon hier“ auf einem Knoten;
eine Brücke entsteht nur, wenn wirklich zwei Knoten beteiligt sind (anderer Knoten deutlich besser, Umzug, Föderation).

**Aufbau** (`bifrost.go`): Saga schickt dem wählenden Knoten über den Steuerkanal
`cascade {bridge, url, peer, peer_pub, token, mine}`. Beide Seiten prüfen ein von Saga signiertes **Kaskaden-Token**:

```
c1.<payload>.<sig>   sig = Ed25519(saga_sk, "saga-horn-cascade/1." + payload)
payload {v:1, bridge, node, room, role: accept|dial, peer_node, peer_pub, q?, q_legs?, q_kbit?, exp (+60 s), jti}
```

`mine` (Rolle dial) prüft der wählende Knoten selbst, `token` (Rolle accept, von der Saga des anderen Knotens) der
annehmende (Signatur, eigener Name, Gegenstelle, Ablauf, einmalig je `jti`). Die Verbindung ist TLS auf 443/TCP (ALPN
`http/1.1`) zum anderen Knoten:

```
GET /api/sfu/bridge   Upgrade: saga-bifrost/1   X-Bifrost-Bridge, X-Bifrost-From, X-Bifrost-Token
X-Bifrost-Sig: B64(Ed25519(knoten_sk, "saga-bifrost/1 dial\n" + bridge + "\n" + HEX(EKM)))
→ 101, X-Bifrost-Sig: B64(Ed25519(knoten_sk, "saga-bifrost/1 accept\n" + bridge + "\n" + HEX(EKM)))
```

EKM = `ExportKeyingMaterial("saga-bifrost/1", 32)` der TLS-Verbindung: Die Knotenschlüssel (`peer_pub`, von Saga) binden die
Unterschrift an genau diese Verbindung. Ein Mittelsmann kann sie nicht weiterreichen; das Web-PKI-Zertifikat spielt
für die Brücke keine Rolle.

**Rahmen** in beide Richtungen: `[typ u8][länge u16][nutzlast]`, Typ 1 = RTP (`[sid u16][paket]`), Typ 2 = Steuerung
(JSON). Gepuffert und je Schub geleert (wie die Beine), Schreibfrist 5 s, Lesefrist 20 s.

| Steuerung | Inhalt |
|---|---|
| `peers` | Teilnehmende dieses Knotens und anderer Brücken (nie die der Gegenseite): `{p:[{id, d, sp, s:[[sid, art, schicht, bildschirm, ext, aktiv]]}]}` – bei Änderung, sonst alle 2 s (letzte Sprache `sp` in ms) |
| `want` | `{s:[sid]}` – Bild-Quellen, die die Gegenseite gerade braucht |
| `pli` | `{s: sid}` – Schlüsselbild anfordern (weiter bis zum Gerät, begrenzt wie lokal) |
| `bye` | Brücke zu (`why`) |

- **Ton:** jede Seite schickt von sich aus ihre drei lautesten (Top-3, wer spricht, bleibt). Der Empfänger wählt daraus
  wie lokal seine Top-3 und den Hauptsprecher.
- **Bild nur auf Nachfrage:** Teilnehmende der Gegenseite stehen als `Peer.Via` im Raum (`internal/sfu/remote.go`), ihre
  Quellen gelten als nutzbar, wenn der andere Knoten sie als aktiv meldet. Last-N, Sprecher und Schichtwahl planen sie wie
  eigene; was ein Ausgang wählt (`Source.Subscribed`), geht als `want` hinüber – also nur die Schichten, die wirklich
  jemand sieht. Umschalten wie lokal am nächsten Schlüsselbild.
- **Pakete bleiben unverändert** (Kopferweiterungen inklusive, Frame-E2E: die Brücke sieht nur Chiffrat). Nur die
  Folgenummer hält der Ausgang lückenlos, wenn er nach H3 verwirft (Warteschlange älter als 150 ms → Bild bis zum
  nächsten Schlüsselbild verwerfen, Ton nie).
- **Kennungen:** Teilnehmende behalten ihre Kennung (`peer_ref`) ihres Knotens; die CSRC vergibt jeder Knoten selbst. Gibt
  es eine Kennung schon (Gerät beim Wechsel auf beiden Knoten), gewinnt der erste Weg.
- **Ende:** `uncascade`, Raum zu, Lesefehler, oder 30 s ohne eigene Teilnehmende und ohne weitere Brücke. Die
  Lastmeldung trägt `bridges {id: {st: up|dial|err|zu, peer, why}}`; Saga baut nicht stehende Brücken nach 8 s neu auf.

**Kontingente (P10, geteilte Knoten):** Medien-Token für Teilnehmende verbundener Server und accept-Token ihrer
Brücken tragen `q` (zufällige Kennung je Server), `q_legs` und `q_kbit`. Der Knoten zählt je `q` die Teilnehmenden (direkt
und über Brücken): mehr als `q_legs` → 429 `kontingent erschoepft`. Eingehend ein gemeinsamer Eimer mit `q_kbit` (0,5 s
tief): darüber verwirft er Bild ab dem nächsten Rahmenanfang bis zum nächsten Schlüsselbild (Ton nie, Folgenummern
lückenlos); ausgehend begrenzt er die Schätzung jedes Beins auf `q_kbit / Teilnehmende`. Notaus:
`quota_off {q, secs}` entfernt sofort alle Teilnehmenden und Brücken mit `q` und weist neue 3 min ab. Die Lastmeldung
trägt `quotas {q: {peers, legs, kbit, dropped, off}}`.

**Herkunft:** `welcome.origins` bzw. `origins {list}` nennt die Seiten verbundener Server, mit denen Saga teilt; CORS für
`/api/sfu/*`, `/probe` und den Ereigniskanal nimmt sie zusätzlich zur eigenen Saga an.

**Messung** (`gjallarhorn bench --sfu --cascade -n 12 --video 12`): zwei Knoten im selben Container, Bots je zur Hälfte,
Brücke dazwischen (Loopback). 2 Kerne, TCP, 20 s: je Knoten 5,3 % eines Kerns (einzelner Knoten mit allen 12: 7,9 %),
Brücke 1,6 Mbit/s je Richtung, Bots empfangen 4312 Pakete/s (erwartet ~4325), 0 verworfene Rahmen.
20 Bots / 12 Video / 60 s: je Knoten 7,5 % (einzelner Knoten: 11,4 %), Brücke 1,6–1,7 Mbit/s, Bots 7114 statt 7117
Pakete/s, 4 Einfrieren auf 145 000 Rahmen (einzeln 0), 0 verworfene Rahmen.

## Testen

```sh
go test -race ./...                                   # inkl. Fuzz-Startwerte, SFU mit pion-Bots, Einfrieren
go test ./internal/netx -run x -fuzz FuzzWeiche       # weitere: FuzzSTUN, FuzzProxyHeader, rtpx: FuzzParse, FuzzVP8
gjallarhorn bench -n 25 --video 12 --sfu              # Last mit dem echten Weiterleiter (ohne --sfu: Spiegel wie bisher)
gjallarhorn bench -n 12 --video 12 --sfu --cascade    # zwei Knoten mit Bruecke (Kaskade, P9)
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers e2e/sfu.sh  # 4 Chromium, ICE-TCP + TURN-TLS, Schichtwechsel bei Drossel
```

`HORN_DEV=1` (nur Tests) schaltet die Endpunkte unter `/api/dev/` frei: Entwicklungs-Token, Räume, Drossel je Bein und
Lastzahlen. Dazu kommt die Testseite `/dev/`. Auf keinen Fall produktiv setzen.
