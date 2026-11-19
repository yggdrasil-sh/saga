# Saga 2.5 – Upgrade von 2.3/2.4

Diese Anleitung ist für Admins mit eigenem Saga-Server unter **docker compose** hinter einem Reverse Proxy, der TLS
selbst beendet – vor allem **Nginx Proxy Manager (NPM)**. Für Traefik, Caddy und nginx steht das Nötige jeweils kurz
dabei. Die ausführliche Anleitung bleibt [ANLEITUNG.md](ANLEITUNG.md), was sich im Einzelnen geändert hat, steht im
[Changelog](../CHANGELOG.md). English: [en/UPGRADE-2.5.md](en/UPGRADE-2.5.md).

**Kurz:** Backup anlegen, Image ziehen, neu starten – die Datenbank baut Saga selbst um. Am Proxy brauchst du nur
**eine Domain, Port 443 und „Websockets Support“**. Danach im Admin-Bereich ein paar Schalter prüfen, die Apps auf 2.5
bringen, fertig.

Inhalt: [1. Vorher](#1-vorher) · [2. Aktualisieren](#2-aktualisieren) · [3. Reverse Proxy](#3-reverse-proxy) ·
[4. Nach dem Update im Admin-Bereich](#4-nach-dem-update-im-admin-bereich) · [5. Apps](#5-apps) ·
[6. Fehlersuche](#6-fehlersuche) · [7. Zurück auf 2.4](#7-zurück-auf-24)

## 1. Vorher

1. **Versionen prüfen.** Unten im Admin-Bereich steht die installierte Version; ohne Admin-Bereich:

   ```sh
   curl -s https://saga.example.org/api/version
   # {"name":"Saga","version":"2.4.0",...,"schema":14,...}
   ```

   `schema` ist der Stand der Datenbank: **9** bei 2.3, **14** bei 2.4. Saga 2.5 hebt ihn auf **35** (Stand der
   Release Candidates im November 2026; ein späterer Release Candidate kann weitere Migrationen bringen – `schema` in
   `/api/version` zeigt immer den aktuellen Wert). Von 2.3 geht es direkt auf 2.5, alle Migrationen laufen
   nacheinander; lies dann zusätzlich den Abschnitt „Von 2.3 auf 2.4“ in der [Anleitung](ANLEITUNG.md#updates)
   (u. a. Gravatar wird für alle Konten abgeschaltet, neuer Dienst saga-media).

2. **Backup.** Saga legt vor dem Umbau selbst ein Backup der Datenbank an (`/data/backups/…-vor-schema-35.dump`).
   Lege trotzdem eins von Hand an, mit Dateien:

   ```sh
   docker compose exec saga python run.py backup --dateien    # Datenbank + Bilder/Dateien der Seiten
   docker compose exec saga ls -l /data/backups
   ```

   Hast du verschlüsselte Backups eingerichtet: Admin-Bereich → **Backups** → „Jetzt sichern“. Unabhängig davon
   gehören die `.env` (vor allem `SECRET_KEY`) und am besten ein `pg_dump` des Datenbank-Containers dazu
   ([Anleitung, Abschnitt 11](ANLEITUNG.md#11-sicherung-wiederherstellung-updates)).

3. **Kein Downgrade.** Nach den Migrationen startet 2.4 (oder 2.3) auf dieser Datenbank **nicht mehr** – sie meldet
   „Die Datenbank hat Schema-Stand 35, diese Saga-Version kennt nur 14“. Zurück geht es nur über das Backup von vor
   dem Update (Abschnitt 7). Alles, was danach geändert wurde, ist dann weg.

## 2. Aktualisieren

Welche Version läuft, bestimmt `SAGA_VERSION` in der `.env`:

| Wert | Was du bekommst |
|---|---|
| `2` (Standard) | immer die neueste stabile 2.x – 2.5 kommt, sobald 2.5.0 erschienen ist |
| `2.5` bzw. `2.5.0` | nur 2.5 (mit Fehlerbehebungen) bzw. genau 2.5.0 |
| `testing` (gleich `rc`) | Release Candidates `2.5.0-rc.N` schon vor dem Release – zum Testen |

**Normal (stabil):**

```sh
git pull                                  # docker-compose.yml und Anleitung auf neuen Stand (optional, siehe unten)
docker compose pull                       # neues Image
docker compose up -d                      # neu starten, Migrationen laufen beim Start
docker compose logs saga | grep saga:     # "saga: Backup vor dem Update: …" und "saga: Datenbank-Migration … "
curl -s http://127.0.0.1:8080/healthz     # "ok" (Port = HOST_PORT)
```

Für den Betrieb hinter NPM ändert sich in `docker-compose.yml` und `.env` nichts: Saga lauscht weiter auf 8080, NPM
leitet dorthin. Die neuen, auskommentierten Zeilen in `docker-compose.yml` (443, 8443, UDP) brauchst du dafür nicht.
Wer die Datei selbst angepasst hat, muss `git pull` also nicht unbedingt mitmachen.

**Release Candidates automatisch (Kanal `testing`):** Das Skript
[`tools/saga-testing-einrichten.sh`](../tools/saga-testing-einrichten.sh) stellt `SAGA_VERSION` um, sichert vorher
`.env` und Datenbank und richtet einen systemd-Timer ein, der alle 15 Minuten das Image prüft und Saga nur bei einem
neuen Image neu startet (danach Prüfung über `/healthz`):

```sh
cd /pfad/zu/saga                                   # Ordner mit docker-compose.yml und .env
curl -fsSLO https://raw.githubusercontent.com/yggdrasil-sh/saga/testing/tools/saga-testing-einrichten.sh
sudo sh saga-testing-einrichten.sh "$PWD" testing
journalctl -t saga-autoupdate                      # Protokoll: "aktualisiert: 2.4.0 -> 2.5.0-rc.N …"
```

Die Datenbank-Sicherung liegt dann in `sicherung/` neben der `docker-compose.yml`. Zurück auf stabil, sobald 2.5.0
erschienen ist: `sudo sh saga-testing-einrichten.sh "$PWD" 2`. Abschalten: `sudo systemctl disable --now
saga-autoupdate.timer`.

## 3. Reverse Proxy

Für Saga 2.5 gilt hinter einem Proxy, der TLS selbst beendet: **eine Domain, nur Port 443, WebSocket durchreichen.**
Mehr nicht – keine TLS-Durchreiche (Stream/SNI), kein zweiter Name für den Anrufknoten, keine weiteren Ports.
Frühere Hinweise auf einen eigenen Namen `horn-0…`, Port 8443 oder eine SNI-Weiche sind für diesen Weg überholt;
sie gelten nur noch, wenn du bewusst einen Knoten mit eigenem Namen bzw. die Betriebsart „Ein Port“ willst
([Anleitung, Abschnitt 3](ANLEITUNG.md#ein-name-ein-port-443)).

WebSocket braucht Saga 2.5 an mehreren Stellen, alle unter derselben Adresse: Anrufe über den eingebauten Knoten
(`/api/sfu/ws` – Apps im Tunnel, Browser über den Rahmen-Weg), gleichzeitiges Bearbeiten live (`/api/collab`) und die
eigene Push-Verbindung der Android-App (`/api/push/stream`).

**Nginx Proxy Manager** – Proxy-Host für `saga.example.org`:

- *Details:* Scheme `http`, Forward Hostname/IP = Saga-Host, Forward Port `8080` (bzw. dein `HOST_PORT`),
  **„Websockets Support“ an** – das ist Pflicht. Ohne Upgrade antwortet `/api/sfu/ws` mit **426**, Anrufe über den
  Knoten kommen nicht zustande.
- *SSL:* Zertifikat (Let's Encrypt) für den Namen, „Force SSL“ nach Wunsch.
- *Advanced (optional):* `proxy_read_timeout 1h; proxy_send_timeout 1h;` – damit ruhende WebSockets (Push der
  Android-App) mindestens 30 Minuten offen bleiben. Ohne das funktioniert es, die App schickt dann nur häufiger
  Lebenszeichen (Hinweis in ihren Push-Einstellungen).
- In der `.env`: `BASE_URL=https://saga.example.org` (mit DNS-Namen) und `TRUSTED_PROXIES` auf die Adresse von NPM –
  läuft NPM auf demselben Host, `HOST_BIND=127.0.0.1` und `TRUSTED_PROXIES=*` (Hinweise zu Docker und IPv6 in der
  [Anleitung, Abschnitt 3](ANLEITUNG.md#3-https-mit-reverse-proxy)). Die Grenzen je Gerät im Anruf-Tunnel zählen die
  echte Adresse aus `X-Forwarded-For`.

**Traefik, Caddy:** nichts zu tun – beide reichen WebSocket durch. Bei Traefik keine `buffering`-Middleware auf den
Router legen.

**nginx (von Hand):** im `location /` zusätzlich die Upgrade-Kopfzeilen (wie NPM):

```nginx
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $http_connection;
```

**Cloudflare (orangene Wolke):** reicht WebSocket durch, der Tunnel geht technisch. Dauerhafter Medienverkehr über
Cloudflare kann aber gegen deren Nutzungsbedingungen verstoßen (vor allem im kostenlosen Tarif) – prüfe sie, bevor
du Anrufe darüber laufen lässt; sonst den Namen auf „DNS only“ (graue Wolke) stellen.

**Optional, nicht nötig:** UDP 443 für den Knoten (`HORN_UDP_LISTEN=:443` und `"443:443/udp"` in
`docker-compose.yml`; NPM belegt nur TCP 443). Das bringt etwas bessere Anrufe, ist aber ein zusätzlich geöffneter
Port. Ohne UDP laufen Anrufe über TCP/TLS bzw. den WebSocket.

**Prüfen, ob der Proxy WebSocket durchreicht** (nach Abschnitt 4, wenn Gjallarhorn an ist): Admin-Bereich →
**Anrufe** → Kasten „Anrufe über die Saga-Adresse (Tunnel)“ → Zeile **„Proxy reicht WebSocket durch?“** →
**Jetzt prüfen**. „Ja ✓ – der Tunnel kommt beim Knoten an.“ ist richtig. Von außen per `curl`:

```sh
curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
  -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' -H 'Sec-WebSocket-Protocol: saga-tunnel.1' \
  https://saga.example.org/api/sfu/ws
# 101 = Proxy reicht WebSocket durch (curl bricht danach nach 5 s ab, das ist normal)
# 426 = Upgrade kommt nicht an -> "Websockets Support" einschalten
```

## 4. Nach dem Update im Admin-Bereich

Der Admin-Bereich läuft wie bisher auf `ADMIN_PORT` (Standard 8081, nur lokal – z. B. per
`ssh -L 8081:127.0.0.1:8081 server`, dann `http://localhost:8081`). Die Namen unten sind die der Oberfläche.

**Anrufe** (Reiter „Anrufe“, Abschnitt „Gjallarhorn – große Gruppenanrufe“). Gjallarhorn ist in 2.5
**experimentell** und ab Werk **aus**; ohne Gjallarhorn telefoniert Saga wie in 2.4 direkt zwischen den Geräten.

1. **„Große Gruppenanrufe über Gjallarhorn“** einschalten und **Speichern**.
2. **„Eingebauten Knoten für Anrufe nutzen“** an lassen (Standard an). Hinter NPM läuft der eingebaute Knoten dann
   im **Tunnel-Betrieb**: kein eigener Port, kein eigenes Zertifikat, Medien über `/api/sfu/ws`. Der Kasten „Anrufe
   über die Saga-Adresse (Tunnel)“ zeigt „An ✓ – Apps und Browser telefonieren über https://saga.example.org …“, die Knoten-Tabelle den eingebauten Knoten
   als „Bereit“ mit Weg „Tunnel ✓“.
3. **„Alle Anrufe über Knoten (auch 1:1)“** ist ab Werk **an**: auch Anrufe zu zweit und Gruppen zu
   dritt/viert laufen über den Knoten, die Geräte sehen gegenseitig keine IP-Adressen. Direkt nur noch als Rückfall
   (kein Knoten bereit, ein in den letzten 30 Tagen genutztes Gerät des Gegenübers kann es nicht). **Achtung bei
   bestehenden Servern:** Ein von früher gespeichertes „aus“ gilt einmal als nicht gesetzt, also an. Wer 1:1 weiter
   direkt will, nimmt den Haken bewusst heraus und speichert (oder `docker compose exec saga python run.py einstellen
   calls.horn_all=0`).
4. **„Proxy reicht WebSocket durch?“** → **Jetzt prüfen** (Abschnitt 3).
5. **„Fähigkeiten jetzt abgleichen“** (unten auf der Seite): fragt alle verbundenen Server sofort nach ihren
   Fähigkeiten – sonst dauert das bis zu 6 Stunden. Je Server geht das auch unter **Föderation** beim jeweiligen
   Server. Am besten nach jedem Update auf einer Seite der Föderation.

**Allgemein:**

- **Chats** → **„Moderne Chats (Stufe 3) – Standard an“**: Reaktionen, Bearbeiten, Sprachnachrichten, Handles statt
  Anmeldenamen. Neue Chats entstehen gleich so; bestehende stellt der Server stündlich um, sobald alle aktiven
  App-Geräte der Mitglieder Saga 2.5 haben – spätestens nach **„Bestehende Chats spätestens umstellen nach (Tagen)“**
  (`chat_v3_days`, Standard 90). Nichts wird neu verschlüsselt, der Verlauf bleibt lesbar. Hast du unter Allgemein
  früher schon einmal gespeichert, steht der Schalter evtl. ausdrücklich auf „aus“ – dann bewusst einschalten (oder
  aus lassen, wenn du noch warten willst; Ausschalten stellt Umgestelltes nicht zurück).
- **Seiten** → **„Seiten-Editor“**: **„Standard (Tiptap)“**. Das Update stellt einmalig auf Tiptap um (Migration 31),
  auch wenn früher „Editor.js“ gespeichert war. Wer den alten Editor noch braucht: „Klassisch (Editor.js, wird in
  einer späteren Version entfernt)“ oder „Jede Person wählt selbst“. Gespeichert wird in beiden Fällen dasselbe
  Format, die Apps sind nicht betroffen.
- **Seiten** → **„Gleichzeitig bearbeiten live (Stufe 2, Test)“** (`notes.live`): ab Werk **aus**. Eingeschaltet
  sehen mehrere Personen in derselben Seite Eingaben und Cursor der anderen sofort (WebSocket `/api/collab`, braucht
  „Websockets Support“). Aus: blockweises Zusammenführen wie bisher.
- **Profilbilder** → **„Gravatar verwenden“**: in 2.5 unverändert. Der Schalter erlaubt Gravatar auf dem Server; jede
  Person schaltet es seit 2.4 selbst ein (Einstellungen → Konto → Profilbild). Kommst du von 2.3, ist Gravatar nach
  dem Update für alle Konten aus – die Personen kurz informieren.
- **Diagnose** → **„Fehlerberichte und ‚Diagnose senden‘“** (Standard an).

**Diagnose** (eigener Reiter, neu in 2.5): Fehlerberichte, „Diagnose senden“ (Kennung `D-…`), Editor- und
Anruf-Diagnose an einer Stelle – Suche nach Kennung, Filter nach Art und Zeitraum, „Als Datei herunterladen“.
Aufbewahrung 14 Tage, einstellbar. Mehr in der [Anleitung, Abschnitt 12](ANLEITUNG.md#12-fehlerbehebung).

**Netz** (neu): Karte mit verbundenen Servern, Knoten und der Zahl verbundener Clients – praktisch für einen ersten
Blick nach dem Update.

## 5. Apps

Für alles Neue in 2.5 brauchen die Personen **Apps 2.5** ([Saga-Apps](https://github.com/yggdrasil-sh/saga-apps/releases)):

- **Android:** `Saga-<Version>.apk` – bis zum Release aus der **Vorabversion** `v2.5.0-rc.N` (Pre-release, nicht
  „Latest“), danach aus dem normalen Release. Installation aus unbekannten Quellen erlauben.
- **iPhone/iPad:** keine Version im App Store. Entweder verteilt der Betreiber die App selbst (z. B. per MDM) oder
  die Personen nutzen Saga als **Web-App** (Safari → Teilen → „Zum Home-Bildschirm“). Safari telefoniert in 2.5 noch
  nicht über den Rahmen-Weg: Anrufe aus der Web-App laufen direkt bzw. über einen Knoten mit UDP oder TURN-TLS – hinter
  NPM ohne diese Wege also direkt.
- **Mac, Linux:** `.dmg` bzw. `.rpm`/`.flatpak` aus denselben Releases.

**Alte Apps (2.4, auch 2.3) an einem 2.5-Server** – die App-Mindestversion bleibt 1.0.0, alte Apps melden sich also
weiter an. Was geht und was nicht:

| Bereich | Alte App (2.4) |
|---|---|
| Seiten, Datenbanken, Aufgaben, Kalender | gehen; gleichzeitiges Bearbeiten wird blockweise zusammengeführt statt live |
| Chats | gehen. Umgestellte Chats (Stufe 3) lesen Apps 2.3/2.4; nach der Frist sehen sie „Sicherheitsnummer bitte neu prüfen“, noch ältere „Bitte die App aktualisieren“. Ein altes Gerät, das in den letzten 30 Tagen benutzt wurde, hält die Umstellung bis zur Frist auf |
| Große Dateien im Chat | Videos wie in 2.4; andere große Dateien (Blockformat, neu in 2.5) erscheinen als „Mit neuerer Saga-Version öffnen“ |
| 1:1-Anruf | läuft **direkt** wie in 2.4, wenn ein aktives Gerät der Person zu alt ist (Hinweis „Gegenstelle veraltet“). Klingelt ein Knoten-Anruf auf einem alten Gerät, scheitert das Annehmen sauber („braucht Saga 2.5“), die anderen Geräte klingeln weiter |
| Gruppenanruf bis 4 | läuft direkt, mit Hinweis, wer die ältere Version hat – die Person kann beitreten |
| Gruppenanruf ab 5 / über den Tunnel | bleibt am Knoten; die alte App sieht „braucht Saga 2.5“ bzw. „kein Anruf-Knoten erreichbar“ und kann nicht beitreten |
| 1:1 → Konferenz (Person hinzufügen) | eine alte App bleibt verbunden, zeigt den Anruf aber weiter als 1:1 |

Faustregel: Wer telefoniert, braucht 2.5. Lange nicht benutzte alte Geräte (über 30 Tage) zählen nicht mehr; ein
vergessenes altes Handy zwingt Anrufe also nicht auf direkt.

## 6. Fehlersuche

**426 an `/api/sfu/ws`, Admin zeigt „Proxy reicht WebSocket nicht durch“.**
Der Proxy reicht das Upgrade nicht weiter. NPM: im Proxy-Host „Websockets Support“ einschalten und speichern; nginx:
Upgrade-Kopfzeilen (Abschnitt 3). Steht ein zweiter Proxy davor (z. B. ein Load Balancer beim Hoster oder Cloudflare),
muss auch der WebSocket durchreichen. Danach „Jetzt prüfen“ bzw. der `curl`-Test aus Abschnitt 3 (101 statt 426).

**401 bei der Föderation** (Adressbuch oder Chat-Verlauf eines verbundenen Servers bleiben aus; im **Prüfprotokoll**
unter Föderation stehen `sig.rejected`/`sig.replay`). Jede Anfrage zwischen Servern ist signiert, über Methode, Name,
Pfad und Query. Prüfen:
- **Uhrzeit** beider Server – mehr als 5 Minuten Abweichung genügt für 401 (NTP/`timedatectl`).
- **Nichts am Proxy umschreiben:** keine Regeln, die Pfad oder Query ändern, sortieren oder kürzen.
- **`BASE_URL`** muss genau der Name sein, unter dem der andere Server dich verbunden hat.
- **Selbst gebaute Images** mit neuerem Go (z. B. 1.27) vor der Korrektur in saga-media (Bugs-DB 2182) verloren
  signierte Abrufe mit 401 – auf den aktuellen Stand bringen. Die veröffentlichten Images waren praktisch nicht
  betroffen.
- Danach **„Fähigkeiten jetzt abgleichen“** (Föderation bzw. Anrufe). Ein verbundener Server auf 2.4 bleibt
  verbunden; Neues aus 2.5 (z. B. Anrufe über seinen Knoten) geht erst nach seinem Update.

**Ein Anruf kommt nicht zustande.**
1. Admin → **Anrufe**: Ist Gjallarhorn an, steht der eingebaute Knoten auf „Bereit“, sagt „Proxy reicht WebSocket
   durch?“ „Ja ✓“?
2. Gerät und Version: alte App (Abschnitt 5)? Safari bzw. iPhone/iPad im Browser (kein Rahmen-Weg)? Browser ohne
   „Encoded Transform“ telefonieren direkt.
3. **Diagnose senden:** Gleich nach dem Fehlversuch auf beiden Seiten **Einstellungen → Hilfe → Diagnose senden**,
   ein Satz dazu („Anruf an X um 14:05 klingelte nicht“). Die Kennung `D-XXXXX` an den Admin geben.
4. Admin → **Diagnose**: Kennung ins Suchfeld, dazu die Anruf-Diagnose (`calldiag`) aus demselben Zeitraum. Mit
   „Als Datei herunterladen“ (JSONL) lässt sich das weitergeben – ohne Inhalte, Namen oder IP-Adressen.

**Saga startet nach dem Update nicht.** `docker compose logs saga | tail -50`. „Schema-Stand … kennt nur …“ heißt: Es
läuft eine ältere Version auf einer schon umgebauten Datenbank – `SAGA_VERSION` prüfen (Abschnitt 7).

## 7. Zurück auf 2.4

Nur über das Backup von vor dem Update, **mit 2.5 zurückgespielt** (sie spielt in einer Transaktion ein und entfernt
Tabellen, die 2.4 nicht kennt), erst danach `SAGA_VERSION` umstellen:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups        # z. B. saga-20261120-183000-vor-schema-35.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261120-183000-vor-schema-35.dump
# dann in der .env SAGA_VERSION=2.4 (bzw. 2.4.0) setzen und:
docker compose pull && docker compose up -d
```

Alles, was nach dem Backup geändert wurde, ist danach weg – also nur kurz nach einem missglückten Update. Läuft der
Timer aus Abschnitt 2, vorher `sudo systemctl disable --now saga-autoupdate.timer`, sonst zieht er wieder `testing`.
