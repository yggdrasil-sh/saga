<p align="center">
  <img src="static/icon.svg" width="96" height="96" alt="Saga">
</p>

<h1 align="center">Saga</h1>

<p align="center">
  <b>Dein selbst gehostetes Zuhause für Dienste, Notizen und Aufgaben.</b><br>
  Dashboard · Seiten &amp; Datenbanken im Notion-Stil · Aufgaben · Kalender · Lesezeichen · mehrbenutzerfähig
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Deutsch</b> · <a href="README.es.md">Español</a>
</p>

<p align="center">
  <a href="https://github.com/yggdrasil-sh/saga/releases"><img src="https://img.shields.io/github/v/release/yggdrasil-sh/saga?label=Version" alt="Version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Lizenz-AGPL--3.0-blue" alt="Lizenz AGPL-3.0"></a>
</p>

<p align="center">
  <a href="https://yggdrasil-sh.github.io/saga/">Projektseite</a> ·
  <a href="docs/ANLEITUNG.md">Anleitung</a> ·
  <a href="#schnellstart">Schnellstart</a> ·
  <a href="#funktionen">Funktionen</a> ·
  <a href="#screenshots">Screenshots</a>
</p>

![Saga Dashboard](docs/screenshots/dashboard.png)

> **Neu in Saga 2.1:** Passkeys und Zwei-Faktor-Anmeldung, Profilbilder, Audio- und Videoanrufe im Direkt-Chat,
> Bilder aus Immich im Chat, schnelle Chat-Bilder mit Vorschau, Chats archivieren und löschen, ein treffsicherer
> KI-Assistent mit eigenem Namen, der auf Zuruf im Chat hilft und im Web suchen kann, und fünf Sprachen (neu: Russisch
> und Thai), wählbar schon auf der Anmeldeseite.
> Seit 2.0: Teilen und Kommentare, Live-Aktualisierung, Ende-zu-Ende-verschlüsselte Chats, Mitteilungen aufs Handy
> und Föderation mit anderen Saga-Servern. Alles Weitere im [Changelog](CHANGELOG.md).

## Funktionen

- **Dashboard** mit Kacheln für alle Dienste, Status-Punkten, Favoriten, Suche (optional Websuche über SearXNG)
  und Tastenkürzeln
- **Dienste per Gruppe:** Wer in der Gruppe `s_nextcloud` ist, sieht die Kachel „Nextcloud“ automatisch –
  egal ob die Gruppe lokal angelegt ist oder aus LDAP/SSO kommt
- **Eigene Lesezeichen** für jeden Benutzer, nur für ihn sichtbar
- **Seiten im Notion-Stil:** Blöcke, Unterseiten, Vorlagen, Versionen, Papierkorb, Export als Markdown
- **Datenbanken** mit Tabelle, Board, Liste, Galerie und Kalender, Filtern, Relationen und Berechnungen
- **Seiten veröffentlichen:** eine Seite (auf Wunsch mit Unterseiten) unter `/@name/seite` teilen – ohne Konto
  lesbar, ohne JavaScript, optional als Startseite für Besucher
- **Teilen und Kommentieren:** Seiten und Datenbanken mit Personen oder Gruppen (lokal, LDAP, SSO) teilen – Rollen
  Lesen, Kommentieren, Bearbeiten, Unterseiten erben die Freigabe; dezente Kommentare am Block mit „Erledigt“
- **Chats mit Ende-zu-Ende-Verschlüsselung:** Direkt-, Gruppen- und Seiten-Chats mit Anhängen, Antworten und
  @-Seitenverweisen; der Server (und der Admin) sieht nur verschlüsselte Daten – libsodium, Wiederherstellungsschlüssel,
  Sicherheitsnummern, mehrere Geräte; Chats archivieren oder löschen (nur für mich oder für alle)
- **Audio- und Videoanrufe:** aus dem Direkt-Chat, mit Bildschirm teilen – WebRTC zwischen den Geräten,
  Ende-zu-Ende verschlüsselt und mit den Chat-Schlüsseln geprüft, kein zusätzlicher Port (optional TURN über 443)
- **Benachrichtigungen und Mitteilungen:** Glocke mit Live-Aktualisierung; auf Wunsch aufs Handy/Desktop per Web Push
  (Ende-zu-Ende verschlüsselt, auch iPhone als Home-Bildschirm-App), ntfy/UnifiedPush oder Apple Push – ohne Inhalte
- **KI-Assistent (optional):** Fragen an die eigenen Seiten, Zusammenfassen, Aufgaben vorschlagen, Text verbessern oder
  übersetzen – mit einem Modell nach Wahl (z. B. Ollama im eigenen Netz). Nur sichtbare Inhalte, Quellen verlinkt, nur Vorschläge, standardmäßig aus.
  Mit eigenem Namen, Bild und Tonfall; auf Wunsch **im Chat nur auf Zuruf** (@Name, alles andere bleibt verschlüsselt)
  und mit **Websuche** über die eigene SearXNG-Instanz (Quellen verlinkt) – beides standardmäßig aus
- **Föderation:** Saga-Server verbinden – Seiten mit Personen und Gruppen dort teilen (auch Datenbanken, Unterseiten,
  Hochladen, Versionen, Export und Anwesenheit), Personen im gemeinsamen Adressbuch nach Namen finden (Widerspruch je
  Konto), Ende-zu-Ende-verschlüsselte Direkt-, Gruppen- und Seiten-Chats und Anrufe über Servergrenzen, mit
  Nachrichtenanfragen und Sperren. Inhalte bleiben beim Eigentümer, jede Anfrage zwischen den Servern ist signiert
  (Ed25519, HTTP Message Signatures), ein fehlerhafter Server wird eingedämmt (Prüfungen, Raten je Server, Prüfprotokoll)
- **Aufgaben** mit Fälligkeit, Priorität und Wiederholung
- **Kalender (CalDAV):** Nextcloud, mailcow/SOGo, Radicale, iCloud … anzeigen und bearbeiten
- **Immich:** eigene Fotos als Bilder und Titelbilder einbinden
- **Jeder Benutzer verwaltet seinen Bereich selbst:** Kalender, Immich, Lesezeichen, App-Schlüssel –
  privat, auch Admins sehen keine Inhalte
- **Konten:** lokale Benutzer, Registrierung an/aus, **LDAP/Active Directory** und **SSO/OIDC**
  (Keycloak, Authentik, Authelia, Zitadel, Entra ID …)
- **Passkeys und Zwei-Faktor-Anmeldung:** Passkeys (auch ganz ohne Passwort), Authenticator-App (TOTP) und
  Wiederherstellungscodes; der Admin kann 2FA für Admins oder alle verlangen
- **Profilbilder:** eigenes Bild (zugeschnitten, ohne Metadaten neu kodiert) oder Gravatar – vom Server geholt
  und zwischengespeichert, Browser fragen nie bei Gravatar; optional aus SSO/LDAP
- **Admin-Bereich auf eigenem Port** für Benutzer, Gruppen, Dienste und Anmeldung
- **Deutsch, Englisch, Spanisch, Russisch und Thai:** jeder Benutzer kann seine Sprache in den Einstellungen wählen,
  auf der Anmeldeseite schon vor dem Login
  (Russisch und Thai maschinell übersetzt – Verbesserungen von Muttersprachlern sind willkommen)
- **[Apps](https://github.com/yggdrasil-sh/saga-apps/blob/main/README.de.md)** (iOS/iPadOS/macOS, Linux/GTK, Android) über dieselbe
  Schnittstelle, mit dauerhafter Anmeldung und Kopplung per QR-Code

## Schnellstart

Voraussetzung: ein Server mit Docker und Docker Compose.

```sh
git clone https://github.com/yggdrasil-sh/saga.git
cd saga
cp .env.example .env
nano .env        # BASE_URL, SECRET_KEY, POSTGRES_PASSWORD eintragen (siehe Kommentare)
docker compose pull && docker compose up -d
```

**Update:** `git pull && docker compose pull && docker compose up -d` – vor Datenbank-Änderungen legt Saga
automatisch ein Backup an. Von 1.x auf 2.x: vorher `SAGA_VERSION=2` in der `.env` setzen. 2.1 baut die Datenbank um (zurück auf 2.0.x nur über das Backup). Was neu ist, steht im **[Changelog](CHANGELOG.md)**. Neugierig auf das Neueste?
`SAGA_VERSION=testing` in der `.env` holt die Testversion aus dem Branch `testing`. Bilder und Dateien der Seiten
stecken nicht im Datenbank-Backup: das Volume `saga-data` sichern oder
`docker compose exec saga python run.py backup --dateien`.

- **Saga:** `http://<server>:8080` – für den Betrieb hinter einen Reverse Proxy mit HTTPS stellen (mit einer
  Grenze für Anfragen von etwa 12 MB, siehe Anleitung)
- **Admin-Bereich:** `http://127.0.0.1:8081` auf dem Server (standardmäßig nur lokal erreichbar, z. B. per
  SSH-Tunnel `ssh -L 8081:127.0.0.1:8081 server`)
- **Erstes Admin-Konto:** `admin` – Passwort aus `ADMIN_PASSWORD`, sonst steht ein erzeugtes in
  `docker compose logs saga`. Vergessen? `docker compose exec saga python run.py admin-passwort`

Alles Weitere – Reverse Proxy, Benutzer, Dienste, LDAP, SSO, Kalender, Sicherung, Updates – steht in der
**[Anleitung](docs/ANLEITUNG.md)**.

## Screenshots

| | |
|---|---|
| ![Seite](docs/screenshots/seite.png) | ![Datenbank als Board](docs/screenshots/board.png) |
| **Seiten** mit Blöcken, Checklisten, Tabellen | **Datenbanken** als Tabelle, Board, Kalender … |
| ![Admin: Dienste](docs/screenshots/admin-dienste.png) | ![Admin: SSO](docs/screenshots/admin-sso.png) |
| **Admin:** Dienste mit Zugang über Gruppen | **Admin:** SSO/OIDC mit Verbindungstest |
| ![Helles Design](docs/screenshots/dashboard-hell.png) | ![Einstellungen](docs/screenshots/einstellungen.png) |
| Helles Design (folgt dem System) | Eigene Einstellungen jedes Benutzers |

## Aufbau

```
Browser / Apps ──► Saga (Python, FastAPI)  :8080  Dashboard, Seiten, API
                   Admin-Bereich           :8081  eigene Anmeldung, nur Admins
                        │
                        ├── PostgreSQL     Konten, Seiten, Aufgaben, Einstellungen
                        ├── /data          Bilder und Dateien der Seiten
                        └── optional: LDAP/AD, OIDC-Anbieter, SearXNG, CalDAV, Immich
```

## Sicherheit in Kürze

- Passwörter mit scrypt, Sperre nach wiederholten Fehlversuchen, CSRF-Schutz, strenge Content-Security-Policy
- Kalender-, Immich-, LDAP- und SSO-Zugangsdaten verschlüsselt in der Datenbank (Schlüssel: `SECRET_KEY`)
- Benutzerdaten strikt getrennt – automatisch getestet (`tests/isolation.py`)
- Interne Adressen (Heimnetz) für Kalender/Immich nur nach Freigabe durch den Admin (Schutz vor SSRF)
- Sperren eines Kontos beendet sofort alle Sitzungen und App-Anmeldungen

Sicherheitslücke gefunden? Bitte nicht als öffentliches Issue, sondern über
[Security Advisories](https://github.com/yggdrasil-sh/saga/security/advisories/new) melden.

## Entwicklung &amp; Tests

Die Tests laufen gegen einen echten Stack mit PostgreSQL, Radicale (CalDAV), einem Test-OpenLDAP und
einem OIDC-Testanbieter:

```sh
SAGA_SSH=root@dockerhost tools/test-stack.sh
```

Screenshots für diese Seite: `SAGA_SSH=… SAGA_SCRIPT=screens-remote.sh tools/test-stack.sh`.

## Lizenz

[GNU AGPL-3.0](LICENSE) – du darfst Saga frei nutzen, verändern und weitergeben; wer eine veränderte
Fassung als Dienst für andere betreibt, muss deren Quelltext ebenfalls offenlegen. Mitgelieferte Editor.js-Bausteine unter ihren eigenen Lizenzen
(`static/vendor/editorjs/licenses`). Dienst-Symbole von [selfh.st/icons](https://selfh.st/icons).
