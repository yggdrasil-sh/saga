# Horn-Probe – Kurzanleitung

Die Horn-Probe ist der Prototyp des Gjallarhorn-Knotens (Saga 2.5, Paket P0). Sie misst, ob große Gruppenanrufe über
**443/TCP** in deinen Netzen tragen: 12 Personen mit Kamera bzw. 25 nur mit Ton. Gemessen werden je Weg (UDP, ICE-TCP,
TURN-TLS) Verbindungsaufbau, RTT, Ton-Verdeckung, Bild-Einfrieren und Datenraten, dazu die Last am Knoten. Am Ende
steht eine Go/No-Go-Tabelle nach Konzept §5.4.

Saga selbst wird dafür nicht angefasst. Die Probe ist ein eigenes Programm (`gjallarhorn probe`).

Platzhalter in dieser Anleitung: `probe.example.org` (Name der Probe), `192.0.2.10` (Adresse des Testhosts).

## 1. Was du brauchst

- Einen Testhost: VPS, Heimserver oder LXC (z. B. Debian 13), amd64 oder arm64.
- Einen DNS-Eintrag `probe.example.org`, der auf den Testhost bzw. auf deinen Proxy zeigt (bei Cloudflare: graue
  Wolke, kein Cloudflare-Proxy).
- Ein Zertifikat für `probe.example.org` (Dateien) **oder** freien Zugang auf 443/TCP für Let's Encrypt (TLS-ALPN-01).

## 2. Wege und was davon bei dir ankommt

| Weg | Transport | kommt durch einen SNI-Passthrough-Proxy? |
|---|---|---|
| TURN-TLS | TLS auf 443/TCP, im Browser als `turns:` | **ja** – das ist dann der Hauptweg |
| ICE-TCP | rohes TCP (RFC 4571) auf 443/TCP | nein, braucht eine direkte Portfreigabe |
| UDP | 443/UDP | nein, braucht eine direkte UDP-Freigabe |

Liegt die Probe hinter einem Proxy, der nur TLS nach SNI weiterreicht, ist das in Ordnung: Die Messseite zeigt
ICE-TCP und UDP nach wenigen Sekunden als „nicht erreichbar“ an und misst TURN-TLS normal weiter.

## 3. Starten ohne Docker (Debian 13, systemd)

Das Binär ist statisch gelinkt und braucht keine Bibliotheken.

**Binär bauen** (auf einem beliebigen Rechner mit Go ≥ 1.24, im Saga-Repo):

```sh
cd gjallarhorn && ./build.sh
# ergibt dist/gjallarhorn-linux-amd64 und dist/gjallarhorn-linux-arm64 (+ SHA256SUMS)
```

**Auf dem Testhost einrichten** (als root):

```sh
install -m 0755 gjallarhorn-linux-amd64 /usr/local/bin/gjallarhorn
useradd --system --home-dir /var/lib/gjallarhorn --shell /usr/sbin/nologin gjallarhorn
install -d -m 0750 -o root -g gjallarhorn /etc/gjallarhorn /etc/gjallarhorn/tls
install -m 0640 -o root -g gjallarhorn probe.env.example /etc/gjallarhorn/probe.env   # aus gjallarhorn/deploy/
install -m 0644 gjallarhorn-probe.service /etc/systemd/system/                        # aus gjallarhorn/deploy/
```

**Zertifikat ablegen**, lesbar für die Gruppe `gjallarhorn`:

```sh
install -m 0640 -o root -g gjallarhorn fullchain.pem privkey.pem /etc/gjallarhorn/tls/
```

Die Probe lädt die Dateien neu, sobald sie sich ändern (Prüfung alle 30 s), oder sofort mit
`systemctl reload gjallarhorn-probe` (SIGHUP). Ein Erneuerungs-Hook muss also nur die Dateien ersetzen.

**`/etc/gjallarhorn/probe.env` anpassen** (Vorlage: `deploy/probe.env.example`), mindestens:

```sh
HORN_NAME=probe.example.org
HORN_LISTEN=:5349        # hoher Port, kein Root nötig; der Proxy reicht 443 hierher durch
HORN_UDP=0               # hinter einem reinen TLS-Proxy
HORN_PROXY_PROTOCOL=0    # 1 oder auto, wenn der Proxy einen PROXY-v2-Kopf sendet
```

**Starten:**

```sh
systemctl daemon-reload
systemctl enable --now gjallarhorn-probe
journalctl -u gjallarhorn-probe -f
```

Bricht der Start im LXC mit `226/NAMESPACE` ab, im Container „nesting“ einschalten oder die Härtungszeilen in der
Unit auskommentieren.

**Proxy davor** (Beispiel HAProxy, TLS wird nicht entschlüsselt):

```
frontend tls443
    bind :443
    mode tcp
    tcp-request inspect-delay 5s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend horn if { req.ssl_sni -i probe.example.org }
backend horn
    mode tcp
    server probe 192.0.2.10:5349            # mit "send-proxy-v2" nur, wenn HORN_PROXY_PROTOCOL=1/auto
```

## 4. Starten mit Docker

Das fertige Image `ghcr.io/yggdrasil-sh/gjallarhorn` entsteht erst mit dem nächsten Push. Bis dahin lokal bauen:

```sh
docker build -t gjallarhorn:probe gjallarhorn/
docker run -d --name horn-probe -p 443:443/tcp -p 443:443/udp -v horn-probe:/data \
  -e HORN_NAME=probe.example.org gjallarhorn:probe probe
```

- Ohne UDP: `-p 443:443/udp` weglassen und `-e HORN_UDP=0` setzen.
- Zertifikat aus Dateien: nach `/data/tls/fullchain.pem` und `/data/tls/privkey.pem` legen (Volume), sonst holt die
  Probe es per TLS-ALPN-01 selbst (Port 443/TCP muss dafür direkt bei ihr ankommen).
- Läuft Docker so, dass Port 443 im Container nicht erlaubt ist: `-e HORN_LISTEN=:8443 -p 443:8443`.
- Steht der Host hinter NAT, ermittelt die Probe die öffentliche Adresse aus `HORN_NAME`. Sonst `HORN_PUBLIC_IP` setzen.

Später, mit dem Image aus der Action: `ghcr.io/yggdrasil-sh/gjallarhorn:2.5.0-probe` statt `gjallarhorn:probe`.

## 5. Messen

1. `https://probe.example.org` öffnen: Laptop (Chrome, Firefox, Safari), iPhone, Android.
2. Netz-Bezeichnung eintragen (z. B. „WLAN zu Hause“, „LTE“, „Firma“), Gerät wählen, **Messen** drücken.
3. Der Browser fragt einmal nach dem **Mikrofon**: bitte erlauben. Ohne diese Freigabe versucht Chrome den ICE-TCP-Weg
   gar nicht (in echten Anrufen ist sie da). Das Mikrofon wird sofort geschlossen, nichts wird aufgenommen oder
   gesendet. Bild und Ton sind künstlich (Canvas und Tongenerator).
4. Seite im Vordergrund lassen. Ein Lauf dauert etwa 4 Minuten:
   30 s Grundmessung (WSS-Ping) → je 60 s UDP, ICE-TCP, TURN-TLS mit „12 Video“ → 30 s „25 Ton“ auf dem besten TCP-Weg.
5. Je Netz und Gerät wiederholen. Jeder Lauf landet als JSON unter `/data/results` bzw. `/var/lib/gjallarhorn/results`.

Gespeichert werden nur Messwerte, Browser/System, Gerätetyp und deine Netz-Bezeichnung. **Keine IP-Adressen.** Die
Client-Adresse aus dem PROXY-Kopf nutzt die Probe nur im Speicher (Grenze von 12 Verbindungen je Adresse).

Hinweis: Ist im Browser ein HTTP-Proxy eingestellt (z. B. Firmen-Proxy), tunnelt Chrome ICE-TCP per `CONNECT` darüber.
Das ist Absicht des Browsers und kann in Firmennetzen sogar helfen.

## 6. Last am Knoten messen

```sh
# Docker
docker exec horn-probe gjallarhorn bench -n 25 --video 12 --secs 120
docker exec horn-probe gjallarhorn bench -n 12 --video 12 --secs 120
# systemd
sudo -u gjallarhorn HORN_DATA=/var/lib/gjallarhorn gjallarhorn bench -n 25 --video 12 --secs 120
```

Der Bench startet einen zweiten, lokalen Knoten auf 127.0.0.1 und lässt N Bots (je ein Ton- und ein Bild-Bein über
ICE-TCP) dagegen laufen. Er braucht kein Netz und stört eine laufende Probe nicht. Gemessen werden CPU-µs je Paket,
CPU-Anteil, RAM je Bein, Paketraten und verworfene Rahmen. `--path udp` misst den UDP-Weg.

## 7. Bericht

```sh
docker exec horn-probe gjallarhorn report > horn-probe.md
# oder: sudo -u gjallarhorn HORN_DATA=/var/lib/gjallarhorn gjallarhorn report > horn-probe.md
```

Der Bericht enthält eine Tabelle je Gerät, Netz und Weg, die Bench-Werte und die Bewertung G1–G9. Läufe mit der
Netz-Bezeichnung `e2e-lokal` (lokale Tests) zählen nur mit `--lokal`.

## 8. Einstellungen

| Variable | Standard | Bedeutung |
|---|---|---|
| `HORN_NAME` | `localhost` | Name der Probe (Zertifikat, TURN-Realm, Ermittlung der öffentlichen Adresse) |
| `HORN_LISTEN` | `:443` | TCP-Port für HTTPS, TURN-TLS und ICE-TCP |
| `HORN_UDP` | `1` | UDP auf demselben Port (`0` = aus) |
| `HORN_UDP_LISTEN` | wie `HORN_LISTEN` | eigener UDP-Port |
| `HORN_DATA` | `/data` | Datenordner (`results/`, `tls/`) |
| `HORN_TLS_CERT`, `HORN_TLS_KEY` | `$HORN_DATA/tls/fullchain.pem`, `privkey.pem` | Zertifikat aus Dateien, wird bei Änderung und mit SIGHUP neu geladen |
| `HORN_TLS_SELF` | `0` | `1` = selbst signiert (nur für lokale Tests) |
| `HORN_ACME_EMAIL`, `HORN_ACME_URL` | – | ACME (TLS-ALPN-01), wenn keine Dateien da sind; URL z. B. für die Staging-Umgebung |
| `HORN_PROXY_PROTOCOL` | `0` | PROXY v2: `0` aus, `1` Pflicht, `auto` lesen, wenn vorhanden |
| `HORN_PUBLIC_IP` | aus `HORN_NAME` | öffentliche Adresse(n) für ICE-TCP/UDP-Kandidaten, kommagetrennt (je eine IPv4/IPv6) |
| `HORN_RELAY_IP` | `127.0.0.1` | Adresse des TURN-Relays; erlaubt ist nur der Knoten selbst |
| `HORN_MAX_LEGS` | `48` | höchstens so viele Verbindungen gleichzeitig |
| `HORN_COALESCE` | `1` | `0` = TCP-Schreibvorgänge nicht bündeln (zum Vergleichen) |
| `HORN_DEBUG` | `0` | `1` = mehr Protokoll (Kandidaten, pion-Warnungen) |

## 9. Aufräumen

```sh
docker rm -f horn-probe && docker volume rm horn-probe
# oder
systemctl disable --now gjallarhorn-probe && rm -rf /var/lib/gjallarhorn /etc/gjallarhorn
```

## 10. Lokal testen (Entwicklung)

```sh
cd gjallarhorn
go test ./... && go vet ./...
e2e/run.sh          # Probe auf :18443 mit selbst signiertem Zertifikat, Messung in headless Chromium
e2e/run.sh tls      # "nur TLS": Mini-Proxy mit PROXY v2 davor, nur TURN-TLS erreichbar
```
