# Gjallarhorn-Knoten (Betriebsart `node`) – Stand P1 + P2 + P3 + P5 + P6/P7

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
- **Schätzung je Bein:**
  - TCP: aus dem Alter der eigenen Warteschlange. Dank `TCP_NOTSENT_LOWAT` liegt der Stau im Knoten.
  - UDP: zusätzlich GCC (TWCC), als Stausignal. Ohne Probepakete ist GCC anwendungsbegrenzt und taugt nicht als
    Obergrenze.
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
  Messung unten.
- Ton und Bild auf getrennten PeerConnections über TCP (H1).

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
Saga-Version. Der Knoten lädt `<HORN_UPDATE_BASE>/v<ver>/gjallarhorn-linux-<arch>` und `.sig`, prüft gegen den festen
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

## Testen

```sh
go test -race ./...                                   # inkl. Fuzz-Startwerte, SFU mit pion-Bots, Einfrieren
go test ./internal/netx -run x -fuzz FuzzWeiche       # weitere: FuzzSTUN, FuzzProxyHeader, rtpx: FuzzParse, FuzzVP8
gjallarhorn bench -n 25 --video 12 --sfu              # Last mit dem echten Weiterleiter (ohne --sfu: Spiegel wie bisher)
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers e2e/sfu.sh  # 4 Chromium, ICE-TCP + TURN-TLS, Schichtwechsel bei Drossel
```

`HORN_DEV=1` (nur Tests) schaltet die Endpunkte unter `/api/dev/` frei: Entwicklungs-Token, Räume, Drossel je Bein und
Lastzahlen. Dazu kommt die Testseite `/dev/`. Auf keinen Fall produktiv setzen.
