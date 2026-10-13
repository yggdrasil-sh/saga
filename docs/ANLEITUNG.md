# Saga – Anleitung

Diese Anleitung führt von der Installation bis zum Alltag: erst für die Person, die Saga betreibt (Admin),
dann für alle, die Saga benutzen.

**Inhalt**

1. [Voraussetzungen](#1-voraussetzungen)
2. [Installation](#2-installation)
3. [HTTPS mit Reverse Proxy](#3-https-mit-reverse-proxy)
4. [Der Admin-Bereich](#4-der-admin-bereich)
5. [Benutzer und Registrierung](#5-benutzer-und-registrierung)
6. [Gruppen und Dienste](#6-gruppen-und-dienste)
7. [LDAP / Active Directory](#7-ldap--active-directory)
8. [SSO mit OpenID Connect](#8-sso-mit-openid-connect)
9. [Saga benutzen](#9-saga-benutzen)
10. [Websuche (optional)](#10-websuche-optional)
11. [Sicherung, Wiederherstellung, Updates](#11-sicherung-wiederherstellung-updates)
12. [Fehlerbehebung](#12-fehlerbehebung)
13. [Alle Einstellungen der .env](#13-alle-einstellungen-der-env)

---

## 1. Voraussetzungen

- Ein Linux-Server (oder VM/LXC) mit **Docker** und **Docker Compose** (`docker compose version`)
- Etwa 512 MB RAM und ein paar GB Platz (je nach Bildern in den Seiten)
- Für den Zugriff von außen: ein Domainname und ein Reverse Proxy mit HTTPS (siehe Abschnitt 3)

Saga braucht **keinen** externen Anmeldedienst: lokale Konten funktionieren sofort. LDAP und SSO kannst du
jederzeit später im Admin-Bereich ergänzen.

## 2. Installation

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
```

Öffne die `.env` und setze mindestens diese drei Werte:

| Einstellung | Bedeutung | Beispiel |
|---|---|---|
| `BASE_URL` | Adresse, unter der Benutzer Saga aufrufen | `https://saga.example.org` |
| `SECRET_KEY` | Geheimer Schlüssel für Sitzungen und verschlüsselte Zugangsdaten | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | Passwort der Datenbank (nur intern) | `openssl rand -hex 24` |

> ⚠️ **`SECRET_KEY` gut sichern und nicht ändern.** Mit ihm sind gespeicherte Kalender-, Immich-, LDAP- und
> SSO-Zugangsdaten verschlüsselt. Ein neuer Schlüssel macht sie unlesbar (die Benutzer müssen sie dann neu
> eintragen).

Optional: `ADMIN_USERNAME` und `ADMIN_PASSWORD` (mindestens 10 Zeichen) für das erste Admin-Konto.

Starten:

```sh
docker compose pull        # fertiges Image von GitHub (ghcr.io/yggdrasil-sh/saga)
docker compose up -d
docker compose ps          # db und saga sollten "healthy" sein
```

Lieber selbst bauen (z. B. mit eigenen Änderungen)? `docker compose up -d --build` baut das Image aus dem Quelltext.

Saga ist jetzt unter `http://<server>:8080` erreichbar.

**Erstes Admin-Konto:** Hast du kein `ADMIN_PASSWORD` gesetzt, erzeugt Saga beim ersten Start eines und
schreibt es ins Log und in die Datei `/data/admin-passwort.txt` im Container:

```sh
docker compose logs saga | grep Admin-Konto
```

Passwort vergessen oder ausgesperrt? Dieser Befehl setzt ein neues Zufallspasswort, entsperrt das Konto und
gibt ihm Admin-Rechte zurück:

```sh
docker compose exec saga python run.py admin-passwort          # Konto "admin"
docker compose exec saga python run.py admin-passwort anna     # anderes Konto
```

![Anmeldeseite](screenshots/login.png)

## 3. HTTPS mit Reverse Proxy

Saga selbst spricht nur HTTP. Für den Betrieb gehört ein Reverse Proxy mit HTTPS davor. Wichtig:

- `BASE_URL` in der `.env` muss die **https-Adresse** sein, unter der Saga aufgerufen wird.
- `TRUSTED_PROXIES` = IP des Proxys, damit Saga die echte Adresse der Besucher sieht (für die Sperre nach
  Fehlversuchen). Läuft der Proxy auf demselben Server, reicht es, den Port nur lokal zu öffnen
  (`HOST_BIND=127.0.0.1`) und `TRUSTED_PROXIES=*` zu setzen.
- Aufrufe über andere Hostnamen leitet Saga auf `BASE_URL` um (`CANONICAL_REDIRECT=true`).

**Caddy** (holt das Zertifikat automatisch):

```
saga.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name saga.example.org;
    # ssl_certificate ... / ssl_certificate_key ...
    client_max_body_size 20m;              # Bilder/Dateien in Seiten
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 4. Der Admin-Bereich

Der Admin-Bereich läuft auf einem **eigenen Port (8081)** mit eigener Anmeldung. Er ist standardmäßig nur auf
dem Server selbst erreichbar (`ADMIN_BIND=127.0.0.1`). Von deinem Rechner aus kommst du per SSH-Tunnel hin:

```sh
ssh -L 8081:127.0.0.1:8081 benutzer@server
# dann im Browser: http://localhost:8081
```

Für Zugriff aus dem Heimnetz kannst du `ADMIN_BIND` auf die LAN-IP des Servers setzen. **Stell den
Admin-Port nicht ins Internet.**

Anmelden können sich alle Konten mit Admin-Rechten – lokal oder per LDAP, auch wenn auf der normalen
Anmeldeseite lokale Konten abgeschaltet sind.

![Admin-Übersicht](screenshots/admin-uebersicht.png)

Unter **Allgemein** stellst du ein:

| Einstellung | Wirkung |
|---|---|
| Titel | Name oben im Dashboard und auf der Anmeldeseite |
| Registrierung erlauben | Jeder, der die Seite erreicht, kann sich ein Konto anlegen |
| Anmeldung mit lokalen Konten | Aus = auf der Anmeldeseite nur LDAP/SSO |
| Standardgruppen | Bekommt jedes neue Konto (z. B. `s_wiki` für einen Dienst, den alle sehen sollen) |
| Gruppen-Kennung | Vorsilbe der Zugangsgruppen für Dienste, Standard `s_` (siehe Abschnitt 6) |
| Interne Adressen | Ob Benutzer Kalender/Immich im Heimnetz verbinden dürfen (siehe Abschnitt 9) |

![Admin: Allgemein](screenshots/admin-allgemein.png)

## 5. Benutzer und Registrierung

Unter **Benutzer** legst du Konten an, setzt Passwörter, vergibst Gruppen und Admin-Rechte.

- **Lokale Konten** haben ein Passwort in Saga (mindestens 10 Zeichen, gespeichert als scrypt-Hash).
- **LDAP- und SSO-Konten** entstehen normalerweise automatisch bei der ersten Anmeldung. Ist „automatisch
  anlegen“ aus, legst du sie hier vorab mit der Kontoart LDAP bzw. SSO an.
- **Sperren** beendet sofort alle Sitzungen, App-Anmeldungen und App-Schlüssel des Kontos.
- **Überall abmelden** hilft bei einem verlorenen Gerät.
- **Löschen** entfernt das Konto **mit allen Daten** (Seiten, Aufgaben, Dateien, Lesezeichen, Verbindungen).
  Externe Konten lieber sperren: Bei der nächsten Anmeldung würde sonst ein neues, leeres Konto entstehen.

Inhalte der Benutzer sind privat – auch Admins sehen nur Anzahlen, nicht die Seiten selbst.

![Admin: Benutzer bearbeiten](screenshots/admin-benutzer-detail.png)

**Selbst-Registrierung:** Unter Allgemein „Registrierung erlauben“ einschalten. Auf der Anmeldeseite erscheint
dann „Jetzt registrieren“. Neue Konten bekommen die Standardgruppen. Ist LDAP aktiv, kann sich niemand einen
Namen registrieren, den es im Verzeichnis schon gibt.

## 6. Gruppen und Dienste

**Dienste** sind die Kacheln auf dem Dashboard (Nextcloud, Jellyfin, Home Assistant …). Wer welchen Dienst
sieht, steuern Gruppen:

> Ein Dienst mit dem Kurznamen **`nextcloud`** ist für alle Mitglieder der Gruppe **`s_nextcloud`** sichtbar
> (Gruppen-Kennung `s_` + Kurzname).

Das funktioniert mit **lokalen Gruppen** genauso wie mit Gruppen aus **LDAP/AD** oder dem **SSO-Anbieter** –
lege dort einfach eine Gruppe `s_nextcloud` an. Beim Anlegen eines Dienstes erstellt Saga die passende lokale
Gruppe automatisch.

Zusätzlich kannst du pro Dienst angeben:

- **Weitere Gruppen**, z. B. `familie` oder `admins`
- **Für alle angemeldeten Benutzer**
- **Symbol** – der Name aus [selfh.st/icons](https://selfh.st/icons), z. B. `jellyfin`, `home-assistant`
- **Kategorie** und **Reihenfolge**
- **Erreichbarkeit prüfen** – Saga ruft die Adresse jede Minute ab und zeigt einen Status-Punkt

![Admin: Dienste](screenshots/admin-dienste.png)

**Dienste aus LDAP (FreeIPA-Stil):** Unter LDAP kannst du „Dienste aus Gruppenbeschreibungen übernehmen“
einschalten. Dann wird jede Gruppe mit der Kennung, deren Beschreibung so aussieht, zu einem Dienst:

```
Beliebiger Text || Nextcloud|https://cloud.example.org|nextcloud|Dateien
```

Mehrere Dienste pro Gruppe: weitere `|| Name|URL|Symbol|Kategorie` anhängen.

## 7. LDAP / Active Directory

Benutzer melden sich auf der normalen Anmeldeseite mit ihrem Verzeichnis-Passwort an. Ihre Gruppen liest Saga
bei jeder Anmeldung neu (und wenn eine App ihre Sitzung erneuert).

1. **LDAP / AD** öffnen, **Vorlage** wählen (FreeIPA, Active Directory, OpenLDAP) und „Vorlage übernehmen“.
2. **Server** eintragen: `ldaps://ipa.example.org` oder `ldap://dc1.example.org` mit StartTLS.
3. **Dienstkonto** (nur Leserechte) und dessen Passwort eintragen.
4. **Suchbasis** für Benutzer und Gruppen anpassen.
5. Unten einen Benutzernamen eintragen und **Verbindung testen** – Saga zeigt DN, Name, E-Mail und Gruppen.
6. **LDAP aktivieren** und speichern.

| | FreeIPA | Active Directory | OpenLDAP |
|---|---|---|---|
| Server | `ldaps://ipa.example.org` | `ldaps://dc1.example.org` | `ldap://ldap.example.org` + StartTLS |
| Dienstkonto | `uid=saga,cn=sysaccounts,cn=etc,dc=example,dc=org` | `CN=saga,OU=Dienste,DC=example,DC=org` | `cn=saga,dc=example,dc=org` |
| Suchbasis Benutzer | `cn=users,cn=accounts,dc=example,dc=org` | `OU=Benutzer,DC=example,DC=org` | `ou=people,dc=example,dc=org` |
| Gruppen | memberOf am Benutzer | AD inkl. verschachtelter Gruppen | Gruppen durchsuchen |

Weitere Optionen: **Nur Mitglieder dieser Gruppe** dürfen sich anmelden; Mitglieder der **Admin-Gruppe**
werden Admins. Eigene Zertifizierungsstelle (CA)? Die Datei als `.crt` in den Ordner `certs/` neben der
`docker-compose.yml` legen und `docker compose restart saga`.

![Admin: LDAP](screenshots/admin-ldap.png)

## 8. SSO mit OpenID Connect

Anmeldung über Keycloak, Authentik, Authelia, Zitadel, Entra ID, Google & Co. Beim Anbieter einen
**vertraulichen Client** (Authorization Code, PKCE) anlegen:

- **Redirect-URI:** `https://saga.example.org/auth/callback`
- **Post-Logout-Redirect-URI:** `https://saga.example.org/login?bye=1`

In Saga unter **SSO / OIDC** eintragen:

| Feld | Beispiel (Keycloak) |
|---|---|
| Issuer-Adresse | `https://sso.example.org/realms/home` |
| Client-ID / Client-Secret | aus dem Client beim Anbieter |
| Claim Benutzername | `preferred_username` |
| Claim Gruppen | `groups` (in Keycloak einen „Group Membership“-Mapper anlegen, „Full group path“ aus) |

Mit **Verbindung testen** prüft Saga das Discovery-Dokument des Anbieters. Danach **SSO aktivieren** und
speichern – auf der Anmeldeseite erscheint der SSO-Knopf. Sind lokale Konten abgeschaltet und kein LDAP aktiv,
geht die Anmeldeseite direkt zum Anbieter.

Gruppen wie `s_nextcloud` aus dem SSO-Anbieter geben Dienste genauso frei wie lokale Gruppen.

**SSO-Anbieter mit Verzeichnis dahinter** (z. B. Keycloak mit FreeIPA- oder AD-Anbindung): Statt einen
Gruppen-Claim einzurichten, kann Saga die Gruppen direkt aus dem Verzeichnis lesen. Dazu unter **LDAP / AD**
das Verzeichnis eintragen und **„Gruppen von SSO-Konten aus dem Verzeichnis lesen“** einschalten. Soll die
Anmeldeseite nur SSO anbieten, „Anmeldung mit Verzeichnis-Passwort“ ausschalten. Gesperrte Konten im Verzeichnis
verlieren dann auch in Saga sofort den Zugang (bei FreeIPA über die Vorlage). **Nur Mitglieder
dieser Gruppe** und **Admin-Gruppe** funktionieren wie bei LDAP.

> **Sicherheit:** Eine SSO- oder LDAP-Anmeldung übernimmt nie ein bestehendes Konto anderer Herkunft mit
> gleichem Namen. Ist die Registrierung offen, könnte jemand einen Namen vorab belegen – bei SSO dann die
> Registrierung aus lassen oder die Konten vorab anlegen.

![Admin: SSO](screenshots/admin-sso.png)

## 9. Saga benutzen

### Dashboard

Kacheln für deine Dienste und Lesezeichen, nach Kategorien sortiert. **★** macht eine Kachel zum Favoriten.
Rechts stehen deine offenen Aufgaben und die Termine von heute und morgen.

| Taste | Wirkung |
|---|---|
| `/` | Suchen |
| `1`–`9` | Kachel öffnen |
| `Enter` | erster Treffer (sonst Websuche) |
| `t` | neue Aufgabe |
| `n` | Notizen |

![Dashboard](screenshots/dashboard.png)

### Lesezeichen

**+ Lesezeichen** unten auf dem Dashboard oder im Benutzermenü unter Einstellungen. Lesezeichen sieht nur du –
auch in den Apps. Symbol: Name von [selfh.st/icons](https://selfh.st/icons), sonst erscheint der
Anfangsbuchstabe. Bearbeiten über **✎** an der Kachel.

![Lesezeichen](screenshots/lesezeichen.png)

### Seiten und Datenbanken

Unter **Notizen** liegt dein Seitenbaum. Tippe `/` für neue Blöcke: Überschriften, Listen, Checklisten,
Tabellen, Code, Bilder, Dateien, Spalten, Verweise auf andere Seiten, Termine und mehr.
Seiten haben Versionen, einen Papierkorb (30 Tage) und lassen sich als Markdown exportieren.

![Seite](screenshots/seite.png)

**Datenbanken** sammeln Einträge mit Eigenschaften (Status, Datum, Zahl, Auswahl, Relation, Berechnung …) und
zeigen sie als Tabelle, Board, Liste, Galerie oder Kalender – mit Filtern, Sortierung und Summen.

![Datenbank als Board](screenshots/board.png)

### Konto, Kalender, Immich, Apps

Im Benutzermenü oben rechts unter **Einstellungen**:

- **Konto:** Name, E-Mail, Passwort ändern (bei LDAP/SSO-Konten beim Anbieter)
- **Kalender:** CalDAV-Konten verbinden (Nextcloud: `https://cloud.example.org/remote.php/dav/`).
  Am besten ein eigenes App-Passwort nur für Kalender anlegen.
- **Immich:** Adresse und einen API-Schlüssel mit den Rechten `album.read`, `asset.read`, `asset.view`,
  `asset.download`, `user.read` eintragen. Dann gibt es Fotos als Bilder und Titelbilder.
- **Apps & Geräte:** eine Liste aller angemeldeten Apps. Zu jeder gehört ein kleiner Schlüssel für Widget,
  Teilen-Erweiterung und Kurzbefehle; **Abmelden** entfernt beides. Von Hand erzeugte Schlüssel (z. B. für
  Skripte) stehen eingeklappt unter „Weitere Schlüssel“.
- **App per QR-Code anmelden:** unter „Apps & Geräte“ erscheint ein QR-Code, der zwei Minuten gilt
  und nur einmal benutzt werden kann. Mit der Handy-Kamera scannen – die App übernimmt Serveradresse
  und Anmeldung, ganz ohne Passwort-Eingabe am Gerät. Der Link im QR-Code funktioniert auch als Kopplungslink
  in Desktop-Apps.

Öffentliche Kalender- und Immich-Server (https) gehen immer. **Server im Heimnetz** (z. B.
`192.168.1.20` oder `nextcloud.lan`) muss der Admin unter Allgemein freigeben – das schützt das interne Netz
davor, über Saga abgefragt zu werden.

![Einstellungen](screenshots/einstellungen.png)

### Auf dem Handy

Saga passt sich kleinen Bildschirmen an und lässt sich als App zum Home-Bildschirm hinzufügen
(„Zum Home-Bildschirm“ im Browser-Menü).

<img src="screenshots/handy.png" width="320" alt="Saga auf dem Handy">

## 10. Websuche (optional)

Das Suchfeld kann zusätzlich im Web suchen – über eine eigene SearXNG-Instanz, die nur Saga erreicht:

```sh
# in der .env
COMPOSE_PROFILES=search
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=<openssl rand -hex 32>
```

Danach `docker compose up -d`. Die Suchanfragen gehen nur an deinen Server, nicht direkt an Suchmaschinen.

## 11. Sicherung, Wiederherstellung, Updates

**Was gesichert werden muss:**

1. die Datenbank,
2. das Volume `saga-data` (Bilder und Dateien der Seiten),
3. die Datei `.env` (vor allem `SECRET_KEY`).

```sh
# Datenbank sichern
docker compose exec -T db pg_dump -U saga -d saga -Fc > saga-$(date +%F).dump
# Dateien sichern
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar czf /backup/saga-data-$(date +%F).tgz -C /data .
```

```sh
# Wiederherstellen (leere Installation, gleicher SECRET_KEY)
docker compose up -d db
docker compose exec -T db pg_restore -U saga -d saga --clean --if-exists < saga-2026-10-13.dump
docker run --rm -v saga_saga-data:/data -v "$PWD":/backup alpine tar xzf /backup/saga-data-2026-10-13.tgz -C /data
docker compose up -d
```

(Der Volume-Name beginnt mit dem Ordnernamen des Projekts – `docker volume ls` zeigt ihn.)

### Updates

Saga hat Versionsnummern nach [Semantic Versioning](https://semver.org/lang/de/): **1.2.3** = Major.Minor.Patch.
Was sich geändert hat, steht im [Changelog](../CHANGELOG.md) und bei den
[Releases](https://github.com/yggdrasil-sh/saga/releases). Die installierte Version siehst du unten im
Admin-Bereich; dort erscheint auch ein Hinweis, wenn es eine neue gibt (abschaltbar unter Allgemein).

Welche Version läuft, bestimmt `SAGA_VERSION` in der `.env`:

| Wert | Bedeutung |
|---|---|
| `1` (Standard) | immer die neueste 1.x – neue Funktionen und Fehlerbehebungen, nichts, was Handarbeit braucht |
| `1.2` | nur Fehlerbehebungen von 1.2 |
| `1.2.3` | genau diese Version |

**Aktualisieren:**

```sh
git pull                                  # docker-compose.yml und Anleitung auf neuen Stand
docker compose pull                       # neues Image
docker compose up -d                      # neu starten
docker compose logs saga | grep saga:     # Backup und Datenbank-Migration im Log
```

Ändert ein Update die Datenbank, legt Saga **vorher automatisch ein Backup** unter `/data/backups` an
(im Volume `saga-data`, die letzten 10 bleiben, `BACKUP_KEEP` in der `.env`). Ein Backup von Hand:

```sh
docker compose exec saga python run.py backup
docker compose exec saga ls -l /data/backups
```

Eine neue **Major-Version** (2.0.0) kann Handarbeit brauchen – sie steht dann im Changelog. Dafür
`SAGA_VERSION=2` setzen.

**Zurück zur vorigen Version:** Eine ältere Saga startet nicht auf einer Datenbank, die schon eine neuere
Version umgebaut hat (sie meldet das im Log, statt Daten zu beschädigen). Dann das Backup von vor dem Update
zurückspielen:

```sh
docker compose stop saga
docker compose run --rm saga ls /data/backups                     # z. B. saga-20261013-183000-vor-schema-3.dump
docker compose run --rm saga python run.py restore /data/backups/saga-20261013-183000-vor-schema-3.dump
# SAGA_VERSION in der .env auf die alte Version setzen (z. B. 1.2.3), dann:
docker compose pull && docker compose up -d
```

Alles, was nach dem Backup geändert wurde, ist dann weg – also nur kurz nach einem missglückten Update.

**Apps:** Die Apps prüfen beim Start die Version des Servers. Ist eine App zu alt für den Server, zeigt sie
„Bitte App aktualisieren“. In den Einstellungen der App stehen App- und Server-Version.

## 12. Fehlerbehebung

**Nach dem Login lande ich wieder auf der Anmeldeseite.**
`BASE_URL` muss genau die Adresse sein, die im Browser steht (inklusive `https://`). Bei `https` setzt Saga
Cookies nur über HTTPS.

**„Zu viele Fehlversuche“.**
Nach 8 falschen Passwörtern pro Konto (30 pro Adresse) in 15 Minuten wartet Saga. Hinter einem Reverse Proxy
`TRUSTED_PROXIES` setzen, sonst teilen sich alle Besucher die Adresse des Proxys.

**Dienst-Symbole fehlen.**
Saga lädt sie einmal von `cdn.jsdelivr.net` (selfh.st) und speichert sie. Ohne Internet erscheint der
Anfangsbuchstabe. Mit `ICON_CDN=` (leer) holt Saga gar nichts aus dem Internet.

**LDAP: „Dienstkonto-Anmeldung fehlgeschlagen“ oder Zertifikatsfehler.**
Mit „Verbindung testen“ prüfen. Bei eigener CA die Datei nach `certs/` legen (Abschnitt 7).

**SSO: „SSO-Anmeldung fehlgeschlagen“.**
Redirect-URI beim Anbieter prüfen (`BASE_URL/auth/callback`) und mit „Verbindung testen“ das
Discovery-Dokument kontrollieren. Details stehen in `docker compose logs saga`.

**Kalender: „Server ist nicht erreichbar oder nicht freigegeben“.**
Interne Server müssen unter Admin → Allgemein freigegeben sein.

## 13. Alle Einstellungen der .env

| Variable | Standard | Bedeutung |
|---|---|---|
| `BASE_URL` | – (Pflicht) | Öffentliche Adresse von Saga |
| `SECRET_KEY` | – (Pflicht) | Mindestens 32 Zeichen, nie ändern |
| `POSTGRES_PASSWORD` | – (Pflicht) | Datenbank-Passwort |
| `POSTGRES_USER` / `POSTGRES_DB` | `saga` | Datenbank-Benutzer und -Name |
| `ADMIN_URL` | – | Link „Admin-Bereich“ im Benutzermenü der Admins |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / erzeugt | Erstes Admin-Konto |
| `HOST_BIND` / `HOST_PORT` | `0.0.0.0` / `8080` | Wo Saga auf dem Server lauscht |
| `ADMIN_BIND` / `ADMIN_HOST_PORT` | `127.0.0.1` / `8081` | Wo der Admin-Bereich lauscht |
| `TRUSTED_PROXIES` | `127.0.0.1` | IPs des Reverse Proxys (`*` = alle) |
| `CANONICAL_REDIRECT` | `true` | Andere Hostnamen auf `BASE_URL` umleiten |
| `SESSION_HOURS` | `12` | Gültigkeit einer Browser-Sitzung (Apps bleiben angemeldet) |
| `SAGA_VERSION` | `1` | Welche Version `docker compose pull` holt: `1`, `1.2` oder `1.2.3` (Abschnitt 11) |
| `BACKUP_KEEP` | `10` | So viele automatische/manuelle Backups bleiben in `/data/backups` |
| `SAGA_UPDATE_CHECK` | `1` | `0` = nie bei GitHub nach neuen Versionen fragen (auch im Admin-Bereich abschaltbar) |
| `ADMIN_SESSION_HOURS` | `4` | Gültigkeit einer Sitzung im Admin-Bereich |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone für Exporte |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Größe je Datei / Speicher je Benutzer |
| `NOTE_MAX_COUNT` | `5000` | Seiten je Benutzer |
| `IMMICH_URL` | – | Vorschlag im Immich-Dialog (wird automatisch freigegeben) |
| `ICON_CDN` | selfh.st über jsDelivr | Quelle der Dienst-Symbole; leer = keine |
| `STATUS_INTERVAL` | `60` | Sekunden zwischen Status-Prüfungen |
| `CACHE_TTL` | `60` | Sekunden, die Konto- und Dienstdaten zwischengespeichert werden |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Websuche (Abschnitt 10) |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Sprache und Filter der Websuche |
| `LOG_LEVEL` | `info` | Ausführlichkeit des Logs |
| `DATABASE_URL` | aus `POSTGRES_*` | Eigene Datenbank-Verbindung (statt des `db`-Containers) |
