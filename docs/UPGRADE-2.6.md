# Saga 2.6 – Upgrade von 2.5

Für Admins mit eigenem Saga-Server unter **docker compose**, vor allem hinter **Nginx Proxy Manager (NPM)** oder einem
anderen Reverse Proxy, der TLS selbst beendet. Wer von 2.3/2.4 kommt, liest zuerst [UPGRADE-2.5.md](UPGRADE-2.5.md) –
die Migrationen laufen dann in einem Zug bis 2.6 durch. Was sich im Einzelnen geändert hat, steht im
[Changelog](../CHANGELOG.md), alles Weitere in der [Anleitung](ANLEITUNG.md). English: [en/UPGRADE-2.6.md](en/UPGRADE-2.6.md).

**Kurz:** Backup anlegen, Image ziehen, neu starten – die Datenbank baut Saga selbst um. Am Proxy ändert sich nichts.
Neu ist vor allem: **Gjallarhorn ist ab Werk an** – große Gruppenanrufe und auch Anrufe zu zweit laufen über den
eingebauten Knoten.

Inhalt: [1. Vorher](#1-vorher) · [2. Aktualisieren](#2-aktualisieren) · [3. Gjallarhorn ab Werk an](#3-gjallarhorn-ab-werk-an) ·
[4. Reverse Proxy](#4-reverse-proxy) · [5. Föderation](#5-föderation) · [6. Knoten](#6-knoten) ·
[7. Zurück auf 2.5](#7-zurück-auf-25)

## 1. Vorher

1. **Version prüfen:** unten im Admin-Bereich oder `curl -s https://saga.example.org/api/version` – `schema` ist **35**
   bei 2.5.0 (Testversionen von `main` können schon höher stehen). Saga 2.6 hebt ihn auf **40**.
2. **Backup.** Saga legt vor dem Umbau selbst eins an (`/data/backups/…-vor-schema-40.dump`). Lege trotzdem eins von
   Hand an, mit Dateien:

   ```sh
   docker compose exec saga python run.py backup --dateien
   docker compose exec saga ls -l /data/backups
   ```

   Dazu gehören die `.env` (vor allem `SECRET_KEY`) und am besten ein `pg_dump` des Datenbank-Containers
   ([Anleitung, Abschnitt 11](ANLEITUNG.md#11-sicherung-wiederherstellung-updates)).
3. **Kein Downgrade.** Nach den Migrationen startet 2.5 auf dieser Datenbank nicht mehr – sie meldet „Die Datenbank hat
   Schema-Stand 40, diese Saga-Version kennt nur 35“ (`SchemaTooNew`). Zurück geht es nur über das Backup (Abschnitt 7).

## 2. Aktualisieren

```sh
git pull                                  # optional: docker-compose.yml und Anleitung auf neuen Stand
docker compose pull
docker compose up -d                      # Migrationen 36 bis 40 laufen beim Start
docker compose logs saga | grep saga:     # "Backup vor dem Update: …" und "Datenbank-Migration …"
curl -s http://127.0.0.1:8080/healthz     # "ok" (Port = HOST_PORT)
```

Mit `SAGA_VERSION=2` (Standard) kommt 2.6 von selbst; wer auf `2.5` festgenagelt hat, stellt auf `2.6` bzw. `2` um
([Anleitung, „Updates“](ANLEITUNG.md#updates)). Die Migrationen laufen selbst. Eine davon (38, Adressbuch) füllt beim
ersten Start einmal die Kontakte aus den Chats und hält den Start so lange an – etwa 0,25 ms je Chat-Kontakt, z. B.
47.000 in rund 11 s.

## 3. Gjallarhorn ab Werk an

Ab 2.6 ist **„Große Gruppenanrufe über Gjallarhorn“** (Admin › Anrufe) ab Werk an. Das heißt:

- **Auch ein in 2.5 gespeichertes „aus“ gilt nach dem Update als an** – solange du den Schalter nicht ab 2.6 bewusst
  setzt. Wer Gjallarhorn nicht will, nimmt den Haken nach dem Update heraus und speichert (oder
  `docker compose exec saga python run.py einstellen calls.horn=0`). Ab dann bleibt es aus.
- **Anrufe zu zweit (1:1) laufen über den eingebauten Knoten** („Alle Anrufe über Knoten (auch 1:1)“, ab Werk an): Die
  Geräte sehen keine IP-Adressen voneinander. Direkt verbindet Saga nur als Rückfall – wenn kein Knoten bereit ist oder
  ein in den letzten 30 Tagen genutztes Gerät des Gegenübers es nicht kann (z. B. eine alte App).
- **Grenzen je Anruf** (Obergrenzen, du kannst sie nur senken; beim Start des Anrufs festgehalten): über den eingebauten
  Knoten (Tunnel/TCP) **16 mit Video, 50 nur Ton**; über einen Knoten mit UDP (z. B. einen gekoppelten) **25 mit Video,
  50 nur Ton**. Ist ein Knoten mit UDP bereit (unter 70 % Auslastung), startet ein neuer Anruf dort. Direkt (Mesh)
  bleibt es bei 4 mit Video / 6 nur Ton. Messwerte dazu:
  [Anleitung, „Grenzen und Messwerte“](ANLEITUNG.md#grenzen-und-messwerte-gjallarhorn).
- Die Marke „Experimentell“ ist im Web und im Admin-Bereich weg. Admin › Anrufe zeigt stattdessen die Zeile **„Grenzen
  je Anruf über Knoten“** und je Knoten **„Geschätzte Kapazität: etwa N mit Video / M nur Ton gleichzeitig“**.

Gekoppelte Knoten sind weiterhin ein Zusatz für mehr Leistung; für den Anfang reicht der eingebaute Knoten.

## 4. Reverse Proxy

Unverändert gegenüber 2.5: **eine Domain, nur Port 443**, am Proxy **„Websockets Support“** an (Caddy und Traefik:
nichts zu tun; nginx: Upgrade-Kopfzeilen). Weil Anrufe jetzt ab Werk über den eingebauten Knoten laufen, lohnt nach
dem Update einmal Admin › Anrufe › **„Proxy reicht WebSocket durch?“ → Jetzt prüfen** – „Ja ✓“ ist richtig. Ohne
WebSocket antwortet `/api/sfu/ws` mit 426; Einzelheiten und Fehlersuche in
[UPGRADE-2.5.md, Abschnitt 3](UPGRADE-2.5.md#3-reverse-proxy).

## 5. Föderation

**Große Anrufe über Servergrenzen** brauchen **alle beteiligten Server auf 2.6** (Bugs-DB 2439: ab etwa 12 Geräten
bekommt sonst nicht jedes Gerät den Schlüssel). Nach dem Update **„Fähigkeiten jetzt abgleichen“** (Admin › Anrufe
bzw. Föderation), damit die verbundenen Server den neuen Stand sofort kennen. Alles andere geht mit Servern auf 2.5
weiter wie bisher.

## 6. Knoten

- **`HORN_MAX_LEGS`:** Der Standard ist ab 2.6 **200** (bis 2.5: 48), `HORN_MAX_PER_IP` **40** (bis 2.5: 12). Steht bei
  einem Knoten noch der alte Wert 48 – fest in der `.env` bzw. Konfiguration des Knotens oder weil ein gekoppelter
  Knoten noch auf 2.5 läuft –, warnt Admin › Anrufe beim Knoten: „HORN_MAX_LEGS (48) ist zu knapp …“ mit dem nötigen
  Wert (bei den Grenzen oben 112). Abhilfe: den Eintrag entfernen (dann gilt 200) bzw. den Knoten aktualisieren.
- **Gekoppelte Knoten** aktualisieren sich selbst, sobald gerade kein Anruf läuft. Erst Knoten ab 2.6 melden ihre Kerne
  mit der CPU-Quote des Containers; ältere melden die Kerne des Rechners – in einem Container mit CPU-Quote fällt die geschätzte
  Kapazität dann zu hoch aus.

## 7. Zurück auf 2.5

Nur über das Backup von vor dem Update (`…-vor-schema-40.dump`), **mit 2.6 zurückgespielt**, erst danach
`SAGA_VERSION` umstellen – genau wie in [UPGRADE-2.5.md, Abschnitt 7](UPGRADE-2.5.md#7-zurück-auf-24) beschrieben:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups        # z. B. saga-20261201-183000-vor-schema-40.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261201-183000-vor-schema-40.dump
# dann in der .env SAGA_VERSION=2.5 (bzw. 2.5.0) setzen und:
docker compose pull && docker compose up -d
```

Alles, was nach dem Backup geändert wurde, ist danach weg – also nur kurz nach einem missglückten Update. Startet 2.5
mit „Schema-Stand 40 … kennt nur 35“, läuft sie noch auf der umgebauten Datenbank: Backup zurückspielen wie oben.
