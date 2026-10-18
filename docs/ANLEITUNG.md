# Saga – Anleitung

[English](en/GUIDE.md) · **Deutsch** · [Español](es/GUIA.md)

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

Hat das Konto einen zweiten Faktor (Passkey, Authenticator-App), entfernt dieser Notfall-Befehl ihn ebenfalls –
danach meldet man sich nur mit dem neuen Passwort an und richtet den zweiten Faktor neu ein. Verlangt die
Richtlinie „2FA verlangen“ einen Faktor, führt die nächste Anmeldung (auch im Admin-Bereich) direkt zur Einrichtung –
ausgesperrt wird man nicht. Weitere Notfall-Befehle:

```sh
docker compose exec saga python run.py 2fa-zuruecksetzen anna   # nur den zweiten Faktor entfernen (Passwort bleibt, auch LDAP)
docker compose exec saga python run.py 2fa-richtlinie           # Richtlinie „2FA verlangen“ anzeigen
docker compose exec saga python run.py 2fa-richtlinie none      # … ausschalten (none | admins | all)
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
    request_body {
        max_size 12MB                      # größte Anfrage: Datei-Upload (ATTACH_MAX_MB + 2)
    }
    reverse_proxy 127.0.0.1:8080
}
```

**nginx:**

```nginx
server {
    listen 443 ssl http2;
    server_name saga.example.org;
    # ssl_certificate ... / ssl_certificate_key ...
    client_max_body_size 12m;              # größte Anfrage: Datei-Upload (ATTACH_MAX_MB + 2)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Live-Kanal (Benachrichtigungen, Anwesenheit, Live-Aktualisierung):** Saga schickt Neuigkeiten über eine offen
bleibende Verbindung (Server-Sent Events, `GET /api/events`) – über denselben Port wie alles andere, ohne WebSocket und
ohne zusätzlichen Port. Der Proxy darf diese Antwort nicht puffern und die Verbindung nicht nach kurzer Zeit schließen.
Saga hilft dabei selbst: Es sendet `X-Accel-Buffering: no` und `Cache-Control: no-cache` und alle 25 Sekunden eine
Heartbeat-Zeile, damit Leerlauf-Timeouts nicht greifen.

- **Caddy:** nichts zu tun – `reverse_proxy` gibt `text/event-stream` sofort weiter.
- **nginx:** funktioniert schon mit der Konfiguration oben (geprüft: kein Puffern, Verbindung bleibt auch über das
  Standard-`proxy_read_timeout` von 60 s hinaus offen). Empfohlen – und nötig, wenn davor noch ein puffernder Proxy
  sitzt – ist ein eigener Block im `server { … }`:

```nginx
    location /api/events {                 # Live-Kanal: nicht puffern, lange offen lassen
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
    }
```

- **Traefik:** funktioniert ohne Einstellungen. Keine `buffering`-Middleware auf den Router legen und keine
  Leerlauf-Timeouts unter 30 Sekunden setzen.
- **NetBird-Reverse-Proxy und andere:** geprüft sind Caddy 2, nginx 1.27 und Traefik 3.1. So prüfst du deinen Proxy:
  Zwei Konten in zwei Browsern anmelden, im einen eine Seite mit dem anderen teilen – die Glocke im anderen Browser muss
  sofort eine 1 zeigen. In den Entwicklerwerkzeugen (Netzwerk) steht `/api/events` dann als „eventsource“ dauerhaft offen.
  Puffert ein Proxy oder bricht er die Verbindung ab, schaltet Saga von selbst auf Abfrage alle 60 Sekunden um: Alles
  kommt an, nur später.

**Größe von Anfragen:** Saga lehnt zu große Anfragen selbst mit 413 ab – allgemein ab 2 MB (`REQUEST_MAX_MB`),
Datei-Uploads ab `ATTACH_MAX_MB` (+ etwas Formular-Rahmen), im Admin-Bereich ab 1 MB. Eine Grenze im Reverse
Proxy (wie oben, etwas über `ATTACH_MAX_MB`) ist trotzdem empfehlenswert: Dann kommen riesige Anfragen gar nicht
erst bis zu Saga. Erhöhst du `ATTACH_MAX_MB`, die Grenze im Proxy mit anheben.

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
| Standardsprache | Sprache für Anmeldeseite, Admin-Bereich und alle Konten mit „Automatisch“; Vorgabe: Automatisch (Browser) |

![Admin: Allgemein](screenshots/admin-allgemein.png)

## 5. Benutzer und Registrierung

Unter **Benutzer** legst du Konten an, setzt Passwörter, vergibst Gruppen und Admin-Rechte.

- **Lokale Konten** haben ein Passwort in Saga (mindestens 10 Zeichen, gespeichert als scrypt-Hash).
- **LDAP- und SSO-Konten** entstehen normalerweise automatisch bei der ersten Anmeldung. Ist „automatisch
  anlegen“ aus, legst du sie hier vorab mit der Kontoart LDAP bzw. SSO an.
- **Sperren** beendet sofort alle Sitzungen, App-Anmeldungen und App-Schlüssel des Kontos.
- **Überall abmelden** hilft bei einem verlorenen Gerät.
- **2FA zurücksetzen** (Karte „Zweiter Faktor“): entfernt Passkeys, Authenticator-App und Wiederherstellungscodes
  eines Kontos – nur, wenn die Person alles verloren hat und du sicher bist, dass sie es ist. Wer wann
  zurückgesetzt hat, steht im Log und auf der Benutzerseite.
- **2FA verlangen** (Allgemein → „Zweiter Faktor“): niemanden (Standard), nur Admins oder alle lokalen und
  LDAP-Konten. Wer betroffen ist und noch keinen zweiten Faktor hat, wird nach der Anmeldung zur Einrichtung
  geführt – ausgesperrt wird niemand, angemeldete Apps und Sitzungen bleiben angemeldet. Das gilt auch hier im
  **Admin-Bereich**: Ein Admin ohne Faktor richtet nach dem Passwort eine Authenticator-App ein (QR-Code, dann Code)
  und ist danach angemeldet; schon offene Admin-Sitzungen bleiben. Bei „alle“ brauchen auch **neue
  App-Anmeldungen** (Anmeldefenster der App, QR-Kopplung) einen Faktor. Details in Abschnitt 9
  „Passkeys und Zwei-Faktor-Anmeldung“, Notfall-Befehle in Abschnitt 2.
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

**Feste SSO-Kennung:** Bei der ersten SSO-Anmeldung merkt sich Saga die Kennung des Anbieters (`sub`). Meldet sich
später jemand mit demselben Benutzernamen, aber einer anderen Kennung an, wird er abgewiesen („mit einer anderen
SSO-Identität verknüpft“). **Anbieter gewechselt** (alle Kennungen neu)? Dann im Admin-Bereich unter Benutzer →
Konto → **„SSO-Verknüpfung lösen“** – die nächste SSO-Anmeldung mit diesem Benutzernamen verknüpft das Konto neu,
alle Daten bleiben. Nur lösen, wenn beim Anbieter sicher dieselbe Person diesen Namen hat.

![Admin: SSO](screenshots/admin-sso.png)

## 9. Saga benutzen

### Dashboard

Kacheln für deine Dienste und Lesezeichen, nach Kategorien sortiert. **★** macht eine Kachel zum Favoriten.
Rechts stehen deine offenen Aufgaben und die Termine von heute und morgen. Die Suche findet Dienste, Lesezeichen
und – darunter – auch deine **Seiten** (Titel und Inhalt).

| Taste | Wirkung |
|---|---|
| `/` | Suchen |
| `1`–`9` | Kachel öffnen |
| `Enter` | erster Treffer (sonst Websuche) |
| `t` | neue Aufgabe |
| `n` | Seiten |

![Dashboard](screenshots/dashboard.png)

### Lesezeichen

**+ Lesezeichen** unten auf dem Dashboard oder im Benutzermenü unter Einstellungen. Deine Lesezeichen siehst
nur du – auch in den Apps; der Admin sieht nur ihre Anzahl. Es reicht die Adresse, ohne Namen heißt die Kachel
wie der Rechner (z. B. `wiki.example.org`). Symbol: Name von [selfh.st/icons](https://selfh.st/icons), sonst
erscheint der Anfangsbuchstabe. Bearbeiten über **✎** an der Kachel.

![Lesezeichen](screenshots/lesezeichen.png)

### Aufgaben

- **Anlegen:** rechts in der Leiste **Schnell hinzufügen …** und `Enter` (Taste `t` springt dorthin). Ist die
  Leiste eingeklappt (**»**) oder das Fenster schmal, stehen die Aufgaben im Kasten **Aufgaben** im Hauptbereich –
  nie beides zugleich. Dort gibt es beim Anlegen gleich **Priorität**, **Wiederholung** (täglich, werktags …) und
  **Fälligkeitsdatum** (Symbole im Eingabefeld). **Alle Aufgaben …** unten in der Leiste öffnet diesen Kasten.
- **Datum:** beim Anlegen über das Kalender-Symbol oder später über **⋯** an der Aufgabe → **Datum wählen …**.
  Fällige Aufgaben zeigen „heute“, überfällige sind rot.
- **Abhaken:** auf den Kreis vor der Aufgabe klicken. Unten erscheint **„Aufgabe erledigt · Rückgängig“**; etwa
  8 Sekunden lang holt **Rückgängig** sie zurück. Bei wiederkehrenden Aufgaben steht dort das nächste Datum, und
  Rückgängig nimmt auch die schon angelegte nächste Aufgabe zurück.
- **Erledigte:** **Erledigte anzeigen** unter der Liste. Ein Klick auf den Haken öffnet eine Aufgabe wieder,
  **×** löscht sie.

Dieselben Aufgaben gibt es in den Apps und – als Block `/Aufgaben` – auch in Seiten.

### Seiten und Datenbanken

Unter **Seiten** liegt dein Seitenbaum. Tippe `/` für neue Blöcke: Überschriften, Listen, Checklisten,
Tabellen, Code, Bilder, Dateien, Spalten, Verweise auf andere Seiten, Termine und mehr. Weitertippen filtert
das Menü, `Enter` übernimmt den ersten Treffer, `Esc` schließt es und nimmt das „/“ wieder weg.
`Strg`+`K` (Mac: `⌘`+`K`) sucht in Titeln und Inhalten. Seiten haben Versionen, einen Papierkorb (30 Tage) und
lassen sich als Markdown exportieren.
**Bilder kopieren:** Ein Klick aufs Bild markiert es; `Strg`+`C`/`X` (Mac: `⌘`) kopiert bzw. schneidet es aus, `Strg`+`V`
fügt es in dieser oder einer anderen Seite (auch in einem anderen Tab) wieder ein – mit Bildunterschrift. Auf einer Seite
eines anderen Kontos bekommt dessen Eigentümer eine eigene Kopie der Datei. Andere Programme bekommen das Bild als PNG,
wo der Browser das erlaubt.

![Seite](screenshots/seite.png)

**Datenbanken** sammeln Einträge mit Eigenschaften (Status, Datum, Zahl, Auswahl, Relation, Berechnung …) und
zeigen sie als Tabelle, Board, Liste, Galerie oder Kalender – mit Filtern, Sortierung und Summen. In der Tabelle
springt `Enter` im Namen in die nächste Zeile; in der letzten legt es gleich den nächsten Eintrag an.

![Datenbank als Board](screenshots/board.png)

### Seiten veröffentlichen

Jede Seite lässt sich **öffentlich** machen – lesbar ohne Anmeldung, schlicht dargestellt, hell oder dunkel nach
Gerät und gut lesbar auf dem Handy. Im Seitenmenü (**⋯** rechts über der Seite, neben „Gespeichert“)
**„Veröffentlichen …“** wählen und im Dialog den Schalter **„Öffentlich“** einschalten – ab dann ist die Seite unter
ihrer Adresse erreichbar. Der Dialog zeigt von oben nach unten: Schalter, Link (mit Kopieren und Öffnen, auf dem Handy
auch Teilen), Adresse, Optionen und „Veröffentlichung beenden“. Solange die Seite privat ist, steht dort die künftige
Adresse blass und noch ohne Link, und die Optionen stehen unter „Gilt, sobald die Seite öffentlich ist“. Bei einer
öffentlichen Seite gelten Änderungen an den Optionen sofort; nur eine neue Adresse übernimmst du ausdrücklich mit
„Adresse ändern“. Fehler (z. B. eine schon benutzte Adresse) stehen direkt unter dem Feld.

- **Öffentlicher Name:** Beim ersten Mal wählst du einen öffentlichen Namen (vorgeschlagen aus deinem
  Anzeigenamen, 2–32 Zeichen `a–z 0–9 - _`). Er steht in jeder Adresse und ist für alle sichtbar – er ist **nie**
  dein Anmeldename. Ändern unter Einstellungen → Konto im eigenen Block „Öffentlicher Name“ (mit eigenem Knopf
  „Namen speichern“; Saga prüft schon beim Tippen, ob der Name gültig und frei ist, und zeigt „Neue Adresse: …
  (bisher …)“); alte Links funktionieren danach nicht mehr. Namen wie `admin`, `api` oder `login` sind gesperrt.
- **Adresse:** `https://saga.example.org/@name/seiten-adresse`. Die Seiten-Adresse wird aus dem Titel
  vorgeschlagen und lässt sich ändern (2–60 Zeichen): Sobald die Eingabe von der gespeicherten Adresse abweicht,
  erscheint der Knopf **„Adresse ändern“** – nach einer Rückfrage, denn der bisherige Link funktioniert danach nicht
  mehr. Großbuchstaben oder ein „/“ am Ende leitet Saga auf die richtige
  Schreibweise um.
- **Unterseiten mit veröffentlichen:** Dann sind auch alle Unterseiten (und Einträge von Datenbanken) unter dieser
  Seite lesbar, unter `/@name/seiten-adresse/<nummer>`. Ohne diesen Schalter bleiben sie privat. Solche Unterseiten
  zeigen im Seitenkopf „🌐 Öffentlich (über „Elternseite“)“; ihr Dialog erklärt das und führt zur Elternseite.
- **Suchmaschinen erlauben:** Standard ist aus – die Seite bittet Suchmaschinen dann, sie nicht aufzunehmen.
- **Als Profilseite:** Diese Seite erscheint unter `/@name` (höchstens eine je Konto). Ohne Profilseite zeigt
  `/@name` eine schlichte Liste deiner öffentlichen Seiten. Ist schon eine andere Seite Profilseite, nennt der Dialog
  sie – die neue ersetzt sie. Ist die Seite Profilseite, zeigt der Dialog unter dem Link auch die **Profiladresse**
  `…/@name` zum Kopieren.
- **Link kopieren / Öffnen** (auf dem Handy auch **Teilen**) und **Veröffentlichung beenden** (mit Rückfrage). Veröffentlichte Seiten tragen im Seitenkopf den
  Hinweis „🌐 Öffentlich“ – ein Klick öffnet den Dialog, „↗“ daneben die öffentliche Seite.
- **Beenden merkt sich alles:** Adresse und Einstellungen bleiben gespeichert (die Adresse bleibt für diese Seite
  reserviert). Veröffentlichst du die Seite wieder, gilt dieselbe Adresse wie vorher.

**Was öffentlich wird – und was nicht:** Titel, Symbol, Titelbild und Inhalt (Text, Überschriften, Listen,
Checklisten, Zitate, Code, Bilder, Tabellen, Spalten, Hinweise, Ausklappbares). Datenbanken erscheinen als einfache
Tabelle (Titel und sichtbare Eigenschaften der Ansicht). Einbettungen werden zu Links. **Nicht** öffentlich werden
Aufgaben-, Kalender- und Immich-Blöcke, Schaltflächen und Dateianhänge. Verweise auf andere Seiten werden nur dann
zu Links, wenn das Ziel ebenfalls öffentlich ist – sonst bleibt nur der Text. Bilder liefert Saga nur aus, wenn sie
in genau dieser Seite stehen.

**So sehen Besucher die Seite:** Unter dem Titel steht „@name · aktualisiert 14.10.2026“ (verlinkt auf `/@name`). Eine
Kopfzeile gibt es nur bei Unterseiten (Pfad zurück) und auf der Startseite für Besucher (Name der Instanz, „Anmelden“). Leiste, Datum und Meldungen erscheinen in der Sprache des
Besuchers; der Inhalt ist mit der Sprache deines Kontos gekennzeichnet (wichtig für Vorlesen und Silbentrennung).
Text steht in angenehmer Lesebreite, Bilder und Tabellen dürfen breiter sein. Eine Adresse, die es nicht gibt, bietet
als nächsten Schritt „Zur Startseite“ (wenn der Admin eine gewählt hat) oder „Anmelden“.

**Papierkorb und Archiv:** Archivierte Seiten und Seiten im Papierkorb sind nicht erreichbar („Nicht gefunden“).
Die Veröffentlichung bleibt aber gespeichert: Holst du die Seite zurück, ist sie wieder unter derselben Adresse
öffentlich. Erst beim endgültigen Löschen (und beim Löschen des Kontos) verschwindet sie.

**Für den Admin** (Admin-Bereich → Allgemein → „Veröffentlichen“):

- **Veröffentlichen erlauben** (Standard an). Aus = alle öffentlichen Adressen liefern sofort „Nicht gefunden“;
  die Einstellungen der Seiten bleiben erhalten.
- **Startseite für Besucher:** eine schon veröffentlichte Seite, die Besucher ohne Anmeldung unter `/` sehen
  (oben rechts „Anmelden“). Angemeldete sehen weiter ihr Dashboard.
- **Veröffentlicht** (eigener Reiter): alle öffentlichen Seiten mit Konto, Name und Datum – **Zurückziehen** beendet
  eine Veröffentlichung sofort (mit Rückfrage). Die Person sieht im Dialog „Vom Admin zurückgezogen am …“; Adresse und
  Einstellungen bleiben gemerkt.

Jeder Fehlerfall – unbekannter Name, private Seite, abgeschaltet – liefert dieselbe neutrale Seite „Nicht gefunden“.
Öffentliche Seiten kommen ohne JavaScript und ohne Cookies aus; je IP-Adresse gilt eine Grenze von 120 Aufrufen pro
Minute (`PUBLIC_RATE`). Läuft Saga hinter einem Reverse Proxy, muss dieser in `TRUSTED_PROXIES` stehen
(Abschnitt 3) – sonst teilen sich alle Besucher eine gemeinsame Grenze.

**Eigene Domain:** Soll z. B. `blog.example.org` die öffentlichen Seiten zeigen, lass die Domain per DNS auf deinen
Server zeigen, leite sie im Reverse Proxy auf Saga weiter (wie in Abschnitt 3) und trage sie in der `.env` ein:
`PUBLIC_HOSTS=blog.example.org` (danach `docker compose up -d`). `BASE_URL` bleibt die Hauptadresse. Unter der
eigenen Domain liefert Saga nur die Startseite für Besucher (`/`, dafür im Admin-Bereich eine wählen), die Adressen
`/@…` und die nötigen Dateien aus – Anmeldung und alles andere leitet weiter auf `BASE_URL`. Aufgerufen über diese
Domain, nennen die Seiten sie auch als Hauptadresse (`canonical`). Beispiel für Caddy:

```
blog.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

### Teilen und Kommentieren

Seiten und Datenbanken lassen sich mit **anderen Konten** und **Gruppen** derselben Saga-Instanz teilen. Knopf
**„Teilen“** oben rechts über der eigenen Seite (oder Seitenmenü **⋯** → „Teilen …“): Person oder Gruppe suchen
(ab 3 Zeichen; angezeigt werden Anzeigename und Benutzername, nie eine Liste aller Konten), Rolle wählen und den
Treffer anklicken. Gruppen bietet der Dialog nur an, wenn du selbst darin bist – lokale Gruppen ebenso wie Gruppen aus
LDAP oder SSO; bei leerem Suchfeld stehen sie als Chips „Deine Gruppen:“ bereit. Unter dem Feld steht, was die
gewählte Rolle darf. In der Liste darunter änderst du die Rolle oder entfernst eine Freigabe (mit Rückfrage; der
Zugriff endet sofort). Die Liste zeigt die tatsächliche Rolle: Hat jemand über eine Gruppe mehr Rechte, steht das
dabei („Kommentieren (über team: Bearbeiten)“), und die Rückfrage beim Entfernen nennt den verbleibenden Zugriff.
Freigaben von übergeordneten Seiten stehen in einem eigenen Bereich (nur lesend, mit Weg zur Elternseite). Unten im
Dialog kopierst du den Link zur Seite – er öffnet sie für alle, die Zugriff haben.

| Rolle | darf |
|---|---|
| **Lesen** | Seite, Unterseiten und Datenbank-Einträge lesen, exportieren, Kommentare lesen |
| **Kommentieren** | zusätzlich kommentieren, antworten, Unterhaltungen erledigen |
| **Bearbeiten** | zusätzlich Titel, Inhalt, Symbol, Titelbild, Tags und Eigenschaften ändern, Unterseiten und Einträge anlegen, Bilder hochladen |

- **Unterseiten kommen mit:** Eine Freigabe gilt auch für alle Unterseiten und die Einträge von Datenbanken darunter.
  Es zählt die höchste Rolle (Person oder Gruppe, diese Seite oder eine Seite darüber). Archivierte Seiten, Seiten im
  Papierkorb und Vorlagen sieht nur der Eigentümer.
- **Was nur der Eigentümer darf:** löschen, archivieren, verschieben, sperren, als Vorlage markieren, veröffentlichen,
  Versionen ansehen und wiederherstellen, Freigaben verwalten. Was andere anlegen oder hochladen, gehört dem
  Eigentümer der Seite; Versionen entstehen bei ihm. Personen mit „Bearbeiten“ verbrauchen also **Speicherplatz des
  Eigentümers** – ihre Bilder und Dateien zählen bei ihm.
- **Teilen und Veröffentlichen:** Ist eine Seite mit „Unterseiten mit veröffentlichen“ öffentlich und zugleich mit
  „Bearbeiten“ geteilt, werden Unterseiten und Bilder, die diese Personen anlegen, sofort öffentlich – der
  Veröffentlichen-Dialog weist darauf hin.
- **Mit mir geteilt:** In der Seitenleiste listet der Bereich „Mit mir geteilt“ die Seiten, die andere mit dir
  geteilt haben (mit Namen der Eigentümerin oder des Eigentümers). Eigene geteilte Seiten tragen ein Personen-Symbol im Seitenbaum.
  Neue Kommentare anderer zeigt ein kleiner Punkt an der Seite im Seitenbaum, die Zahl am Bereich „Mit mir geteilt“
  und „💬 n“ am Knopf „Seiten“ auf der Startseite. Ist Teilen im Admin-Bereich abgeschaltet, steht dort ein Hinweis.
- **Wer was geändert hat:** Unter dem Titel steht „Zuletzt geändert von Name · vor 5 Min.“ (wenn es nicht du warst)
  und bei Seiten, die eine andere Person angelegt hat, „Angelegt von Name“. Der Versionsverlauf nennt bei jeder
  gesicherten Fassung, von wem sie stammt.
- **Fremde Seiten** zeigen unter dem Titel „Geteilt von Name · Du kannst lesen/kommentieren/bearbeiten“ (darunter, wer
  zuletzt geändert hat). Ändert der Eigentümer deine Rolle, zeigt die Seite das beim nächsten Speichern oder wenn du
  zum Tab zurückkehrst. Mit „Lesen“ oder „Kommentieren“ ist der Editor
  schreibgeschützt; Datenbanken zeigen „Nur lesen“ (Sortieren und Filtern geht, wird aber nicht gespeichert).
  Aufgaben-, Kalender- und Immich-Blöcke zeigen nur „Nur für den Eigentümer sichtbar“ – nie die Daten des Eigentümers
  oder deine eigenen. Verweise auf Seiten, die nicht mit dir geteilt sind, erscheinen als „Seite ohne Zugriff“.
- **Gleichzeitig bearbeiten:** Speichern zwei Personen fast gleichzeitig, erscheint der bekannte Konflikt-Hinweis –
  mit Namen: „Bert hat diese Seite gerade geändert“. **„Neue Version laden“** lädt Berts Fassung und zeigt deinen
  nicht gespeicherten Text zum Kopieren. **„Meine Version behalten“** fragt erst nach; Berts Fassung bleibt dann im
  Versionsverlauf des Eigentümers erhalten – es geht nichts spurlos verloren.
- **Zugriff endet, während du schreibst:** Entzieht der Eigentümer die Freigabe (oder fällt sie auf „Lesen“, oder
  der Admin schaltet Teilen ab), erscheint ein Dialog mit deinem letzten Text zum Kopieren. Alte Links auf Seiten
  ohne Zugriff zeigen „Kein Zugriff auf diese Seite“ mit „Erneut versuchen“ und „Zu „Mit mir geteilt““.

**Kommentare** hängen an einem Block und bleiben dezent: Am Rand steht nur eine kleine Zahl (💬 2), und zwar nur,
solange es offene Unterhaltungen gibt. Kommentieren geht über das Block-Menü (**⋮⋮** links neben dem Block →
„Kommentieren“, oder Strg/⌘ + / → „Kommentieren“); beim Überfahren eines Blocks erscheint außerdem ein kleiner Knopf 💬+ (auf Touch-Geräten bei schreibgeschützten Seiten nach dem Antippen).
Die Unterhaltung öffnet sich in einer Seitenleiste rechts (auf dem Handy von unten). Dort antwortest du, bearbeitest
oder löschst eigene Kommentare (der Eigentümer der Seite darf alle löschen) und schließt eine Unterhaltung mit
**„✓ Erledigt“** ab – sie klappt zu und verschwindet; der Schalter **„Erledigte zeigen“** holt sie zurück, „Wieder
öffnen“ macht sie wieder aktiv. „💬 3“ im Seitenkopf öffnet alle Unterhaltungen der Seite; ein kleiner Punkt zeigt
Ungelesenes. Bearbeiten und Löschen eines Kommentars stehen in seinem ⋯-Menü; der Eigentümer findet alle
Unterhaltungen auch im Seitenmenü **⋯** → „Kommentare“ (mit Anzahl der erledigten). Kommentare sind Klartext (höchstens 2000 Zeichen, Zeilenumbrüche erlaubt; Links werden erkannt), höchstens
30 Kommentare pro Minute. Auch auf eigenen, nicht geteilten Seiten kannst du kommentieren – als Notiz an dich selbst.
Wird ein Block gelöscht, bleibt seine Unterhaltung als „Block entfernt“ in der Liste. Löschst du den ersten Kommentar
einer Unterhaltung, auf den andere geantwortet haben, bleiben die Antworten stehen und dein Kommentar erscheint als
„Kommentar gelöscht“; der Eigentümer der Seite kann die ganze Unterhaltung löschen (mit Rückfrage).

**Für den Admin** (Admin-Bereich → Allgemein → „Teilen“): **Teilen erlauben** (Standard an; aus = alle Freigaben
wirken sofort nicht mehr, bleiben aber gespeichert) und **Nur innerhalb gemeinsamer Gruppen** (Standard aus;
Personensuche und Teilen nur mit Konten, die mit einem eine Gruppe gemeinsam haben; wirkt nur auf neue Freigaben –
bestehende bleiben, wenn du es später einschaltest). Gruppen aus LDAP oder SSO übernimmt Saga bei der Anmeldung:
Ändert sich die Mitgliedschaft dort, gilt das für Freigaben an Gruppen erst ab der nächsten Anmeldung des Kontos.
Das Benutzer-Detail zeigt nur
die Anzahl der Freigaben, keine Inhalte. Wird ein Konto gelöscht, verschwinden seine Freigaben und die an das Konto;
seine Kommentare auf fremden Seiten bleiben als „Gelöschtes Konto“ stehen. Wird eine Gruppe gelöscht, enden die
Freigaben an sie.

### Mit anderen Saga-Instanzen teilen (Föderation)

Mehrere Saga-Server (z. B. deiner und der eines Vereins) lassen sich verbinden. Danach teilt man eine Seite auch mit
**name@server** – mit einer Person dort (über ihren öffentlichen Namen, Abschnitt „Seiten veröffentlichen“) oder mit
einer Gruppe dort (**gruppe@server**). Die Inhalte bleiben immer auf dem Server des Eigentümers: Die andere Person
liest, kommentiert und bearbeitet über ihren eigenen Saga-Server, der im Hintergrund beim Eigentümer-Server nachfragt.
Welche Rolle sie hat, entscheidet immer der Eigentümer-Server – Entzug wirkt sofort. Alles läuft über den normalen
HTTPS-Port, es gibt keinen zusätzlichen Dienst.

**Verbinden (Admins beider Server):**
1. Admin-Bereich → **Föderation** → „Föderation einschalten“ (Standard: aus). Dort stehen eure Adresse und euer
   **Fingerprint** (der öffentliche Schlüssel dieses Servers in Kurzform).
2. Server A: **„Einladungscode erzeugen“** – Adresse und Code an den Admin von Server B geben (der Code gilt 24 Stunden
   und nur einmal).
3. Server B: unter „Mit einer Instanz verbinden“ Adresse und Code eintragen → Saga zeigt den Fingerprint von A. Stimmt
   er mit dem überein, den A genannt hat (am besten auf einem zweiten Weg vergleichen), → „Fingerprint stimmt –
   verbinden“.
4. Server A: Die Anfrage steht unter „Verbundene Instanzen“ mit dem Fingerprint von B → **Bestätigen**. Ab jetzt sind
   beide verbunden. War B gerade nicht erreichbar, dort „Status prüfen“.

**Trennen** (jede Seite, jederzeit) wirkt sofort: alle Freigaben an Personen und Gruppen des anderen Servers enden,
Seiten von dort verschwinden. Das **Prüfprotokoll** zeigt Verbindungen, Freigaben über Servergrenzen und abgewiesene
Anfragen – nie Inhalte. Interne Adressen (192.168.x, 10.x …) erreicht Saga nur, wenn sie unter Allgemein →
„Freigegebene interne Hosts“ stehen; andere Server verbinden sich nur über https.

**Teilen:** Im Teilen-Dialog `name@server` (oder `@name@server`) eingeben – Saga fragt den verbundenen Server, ob es die
Person bzw. Gruppe gibt, und zeigt sie mit Herkunft an („Bert (bert@verein.example)“). Dafür brauchst du einen
öffentlichen Namen (Einstellungen → Konto). Personen anderer Server erscheinen in „Mit mir geteilt“, in Kommentaren und
Benachrichtigungen immer mit `@server`, damit niemand mit einem gleichnamigen Konto hier verwechselt wird. Ist „Teilen
nur innerhalb gemeinsamer Gruppen“ an, geht Teilen mit anderen Servern nicht.

**Geteilt bekommen:** Seiten anderer Server stehen in **„Mit mir geteilt“** (mit `name@server`), öffnen sich im
normalen Editor und lassen sich je nach Rolle lesen, kommentieren und bearbeiten (Titel und Inhalt; bei
gleichzeitigem Bearbeiten greift der bekannte Konflikt-Hinweis). Bilder kommen über den eigenen Server. Du brauchst
dafür einen öffentlichen Namen. Benachrichtigungen („hat eine Seite mit dir geteilt“, „hat geantwortet“) kommen auch
über Servergrenzen – ohne Kommentartext.

**Noch nicht über Servergrenzen** (diese Runde): Datenbanken anderer Server öffnen (es erscheint ein Hinweis), Bilder
in fremde Seiten hochladen, Unterseiten anlegen, Versionen, Export, „Wer gerade hier ist“ und Live-Aktualisierung,
Chat. Wer seinen öffentlichen Namen ändert oder sein Konto löscht, verliert Freigaben anderer Server (sie werden dort
entfernt); der alte Name wird für andere Konten gesperrt.

### Benachrichtigungen und Live-Aktualisierung

Saga hält das Dashboard und offene Seiten auf dem neuesten Stand – ohne Neuladen.

- **Glocke** oben rechts (Dashboard und Seiten): Die Zahl nennt ungelesene Benachrichtigungen, ein Klick öffnet die
  Liste (auf dem Handy von unten). Eine Benachrichtigung bekommst du, wenn jemand eine Seite mit dir teilt oder deine
  Rolle ändert, wenn jemand deine Seite kommentiert, wenn jemand in einer Unterhaltung antwortet, an der du beteiligt
  bist, und wenn ein Admin eine Veröffentlichung von dir zurückzieht – nie für deine eigenen Aktionen. Mehrere
  Kommentare derselben Unterhaltung innerhalb von 10 Minuten fasst Saga zusammen („3 neue Antworten in einer
  Unterhaltung“). Ein Klick öffnet die Seite (bei Kommentaren gleich die Unterhaltung) und markiert den Eintrag als
  gelesen; **„Alle gelesen“** markiert alles. Titel und Kommentar-Auszug stehen nur da, solange du Zugriff hast – nach
  einem Entzug zeigt der Eintrag „Kein Zugriff mehr“. Benachrichtigungen bleiben 90 Tage gespeichert.
- **Einstellungen → Konto → Benachrichtigungen:** jede Art einzeln an- oder abschalten.
- **Wer gerade hier ist:** Haben andere die Seite offen, stehen ihre Anfangsbuchstaben im Seitenkopf („Bert ist auch
  hier“); ein Ring bedeutet „Bert bearbeitet gerade“.
- **Die Seite ändert sich, während du sie offen hast:** Ohne ungespeicherte Änderungen lädt Saga die neue Fassung still
  nach (Scroll-Position und Schreibmarke bleiben, so gut es geht). Schreibst du gerade selbst, erscheint stattdessen
  „Anna hat diese Seite geändert“ mit **Neu laden**; speicherst du trotzdem, greift der bekannte Konflikt-Hinweis.
- Kommentare, die Punkte für Ungelesenes, „Mit mir geteilt“ und „💬 n“ am Knopf „Seiten“ aktualisieren sich ebenfalls
  sofort.
- Ohne Live-Verbindung (Proxy puffert, mehr als 5 offene Tabs desselben Kontos) fragt Saga alle 60 Sekunden nach.
  Für den Reverse Proxy siehe Abschnitt 3; der Admin-Bereich zeigt unter Allgemein die Zahl aktiver Live-Verbindungen.

### Chats (Ende-zu-Ende-verschlüsselt)

Knopf **„Chats“** im Kopf von Dashboard und Seiten (mit Zahl ungelesener Nachrichten) öffnet die Chats in einem eigenen
Tab. Alle Nachrichten, Anhänge und die Namen freier Gruppen werden **auf deinen Geräten** verschlüsselt – der Server
speichert nur verschlüsselte Daten. Auch der Admin kann keine Inhalte lesen.

- **Einrichten (einmal je Konto):** Beim ersten Öffnen erzeugt Saga deine Schlüssel und zeigt einen
  **Wiederherstellungsschlüssel** (32 Zeichen, z. B. `ABCD-EFGH-…`). Schreibe ihn ab oder speichere ihn im
  Passwort-Manager („Kopieren“, „Als Datei speichern“) und bestätige die letzten vier Zeichen. Ohne ihn und ohne ein
  anderes eingerichtetes Gerät sind deine Nachrichten verloren – auch der Admin kann sie nicht wiederherstellen.
- **Neues Gerät oder neuer Browser:** entweder **„Mit anderem Gerät bestätigen“** – auf einem Gerät, auf dem die Chats
  schon laufen, erscheint die Anfrage mit einem 20-stelligen Code (vier Fünfergruppen); stimmt er vollständig mit dem Code auf dem neuen Gerät
  überein, bestätigen – oder den **Wiederherstellungsschlüssel eingeben**. Danach liest das neue Gerät auch den bisherigen Verlauf.
  Wer beides nicht mehr hat, kann **neu beginnen** (neue Schlüssel; alte Nachrichten sind dann nicht mehr lesbar).
  Dafür fragt Saga nach deinem Passwort; bei SSO-Konten meldest du dich kurz neu an (danach 5 Minuten Zeit).
- **Unterhaltungen:** **„+ Neu“** → Person suchen (ab 3 Zeichen) für einen **Direkt-Chat**, eine eigene Gruppe für einen
  **Gruppen-Chat** (Mitglieder folgen der Gruppe, auch aus LDAP/SSO) oder „Gruppe mit mehreren Personen“ für eine
  **freie Gruppe** (der Ersteller lädt ein und entfernt; der Name ist verschlüsselt). Auf geteilten Seiten öffnet
  **„💬 Chat“** im Seitenkopf den **Seiten-Chat** mit allen, die die Seite sehen dürfen – ändern sich die Freigaben,
  ändern sich die Mitglieder. Wer neu dazukommt, sieht nur Nachrichten ab dem Beitritt; wer geht, bekommt nichts Neues.
- **Schreiben:** Enter sendet, Umschalt+Enter macht eine neue Zeile. 📎 hängt Bilder oder Dateien an (verschlüsselt,
  höchstens so groß wie Datei-Anhänge sonst); **@** verlinkt eine Seite – andere sehen den Titel nur, wenn sie die Seite
  selbst sehen dürfen. Bilder erscheinen im Verlauf als kleines Vorschaubild (beim Senden erzeugt, ebenfalls verschlüsselt);
  das Original lädt erst beim Antippen. Geladene Bilder hält der Browser verschlüsselt vor (bis 200 MB, beim Abmelden
  geleert). Über „⋯“ an einer Nachricht: **Antworten**, Text kopieren, eigene Nachricht **für alle löschen**.
  „tippt …“ und neue Nachrichten erscheinen sofort (Live-Kanal).
- **Chat-Menü (⋯ oben):** **Sicherheitsnummer** (60 Ziffern, auf beiden Seiten gleich – persönlich oder per Anruf
  vergleichen und „Als überprüft markieren“; ändert sie sich, warnt der Chat), Mitglieder, **Stummschalten**
  (keine Benachrichtigung, zählt nicht als ungelesen), **Nachrichten automatisch löschen nach** 1 Stunde … 1 Jahr
  (gilt für neue Nachrichten), freie Gruppe umbenennen oder verlassen. Die Lupe sucht in den geladenen Nachrichten – nur auf
  diesem Gerät.
- **Archivieren und löschen:** im Chat-Menü (⋯) oder per Rechtsklick bzw. langem Antippen in der Chatliste.
  **Archivieren** schiebt den Chat in den Bereich **„Archiviert“** unten in der Liste – er zählt nicht mehr als
  ungelesen und kommt mit der nächsten Nachricht von selbst zurück (stummgeschaltete bleiben im Archiv). **Chat
  löschen …** fragt: **Nur für mich** – der Verlauf verschwindet nur bei dir, auf allen deinen Geräten (die Schlüssel
  dafür löscht der Server); ein Direkt-Chat verschwindet aus der Liste, bis eine neue Nachricht kommt, andere Chats
  werden archiviert; eine freie Gruppe kannst du dabei auch verlassen. **Für alle** – Direkt-Chat (beide Personen) und
  freie Gruppe (ihre Admins): der Chat ist samt Nachrichten, Anhängen und Anrufen für alle weg, die anderen sehen
  „Name hat den Chat gelöscht“. Gruppen-Chat (Saga-Admins) und Seiten-Chat (wem die Seite gehört): nur der Verlauf wird
  gelöscht, der Chat bleibt. Beides lässt sich nicht rückgängig machen; wer Nachrichten schon gesehen hat, kann Kopien
  haben. Schnittstelle für Apps: `docs/APP-API-CHAT.md`.
- **⚙ Chats & Geräte:** Liste der Geräte mit Zugriff, Geräte entfernen (bekommen sofort nichts mehr), neuen
  Wiederherstellungsschlüssel erzeugen (der alte gilt dann nicht mehr).
- Benachrichtigung „Neue Nachricht von Name“ – ohne Inhalt; abschaltbar unter Einstellungen → Konto → Benachrichtigungen.

**Admin:** Allgemein → „Chats“: **Chats erlauben** (Standard an) und **Nachrichten höchstens aufbewahren (Tage)** – ältere
Nachrichten und Anhänge löscht der Server stündlich, auch wenn ein Chat eine längere Frist hat. Beim Benutzer stehen nur
Anzahlen (Chats, Geräte). Konto löschen entfernt Schlüssel, Geräte, Mitgliedschaften, gesendete Nachrichten und Anhänge;
Direkt-Chats mit dem Konto verschwinden ganz. Grenzen: 64 KB je Nachricht, 60 Nachrichten je Minute, 10 Geräte je Konto,
50 Mitglieder je Chat, `CHAT_QUOTA_MB` (Standard 1024) für Anhänge je Konto. Kein zusätzlicher Port.

**Technik (Format v1):** nur libsodium (Browser: libsodium.js, mitgeliefert unter `static/vendor/libsodium`). Je Konto
ein Identitätsschlüssel (Ed25519) und ein Backup-Schlüssel (X25519), je Gerät X25519 + Ed25519, vom Identitätsschlüssel
signiert. Jede Nachricht: zufälliger Schlüssel, XChaCha20-Poly1305 (`crypto_aead_xchacha20poly1305_ietf`), der Schlüssel
mit `crypto_box_seal` für jedes Gerät und den Backup-Schlüssel jedes Mitglieds versiegelt, vom Gerät signiert
(`crypto_sign_detached`). Das Schlüssel-Bündel auf dem Server ist mit einem per `crypto_pwhash` (Argon2id) aus dem
Wiederherstellungsschlüssel abgeleiteten Schlüssel verschlüsselt. Genaues Format: Kopf von `saga/chat.py`; Testvektoren
für eigene Clients: `tests/chat_vectors.json` (prüfen mit `python3 tools/chat_vectors.py check` bzw.
`node tools/chat_vectors.mjs`). Die privaten Schlüssel liegen im Browser in IndexedDB; im privaten Fenster gehen Chats nicht.

### Anrufe (Audio, Video, Bildschirm)

Im **Direkt-Chat** stehen oben zwei Knöpfe: **📞 Anrufen** und **🎥 Videoanruf**. Saga verbindet die beiden Geräte
direkt (WebRTC) – die Medien laufen nicht über den Saga-Server und sind **Ende-zu-Ende verschlüsselt**. Kein
zusätzlicher Port: Saga vermittelt den Anruf über den normalen Web-Port (Live-Kanal + normale Anfragen).

- **Klingeln:** Beim Gegenüber erscheint auf **jeder Saga-Seite** (Dashboard, Seiten, Chats) „Name ruft an“ mit
  Annehmen/Ablehnen (bei Videoanrufen auch „Nur mit Ton annehmen“), dazu ein Klingelton (🔔/🔕 schaltet ihn ab, die
  Wahl gilt für diesen Browser) und eine **Mitteilung** aufs Handy/den Computer – „Anruf von Name“ bzw. ohne Namen,
  wenn „Namen zeigen“ aus ist. Alle Geräte und Tabs der Person klingeln; nimmt man auf einem an, hören die anderen auf.
  Annehmen auf dem Dashboard oder einer Seite öffnet den Anruf im Chat-Tab (dort liegen die Schlüssel des Browsers).
- **Im Anruf:** großes Bild des Gegenübers, kleines eigenes Bild, **Stummschalten**, **Kamera an/aus** (auch in
  einem Audioanruf nachträglich), **Bildschirm teilen** (am Computer), **Auflegen**, „▭“ verkleinert das Fenster in
  eine Ecke (weiter im Chat schreiben). Oben stehen Dauer, Verbindungsqualität und der Weg: **direkt** oder **über
  Relais (TURN)**, und „🔒 Ende-zu-Ende verschlüsselt“, sobald die Prüfung (unten) geklappt hat.
- **Nicht angenommen:** Nach der Klingeldauer (Standard 45 s) gilt der Anruf als **verpasst**. Im Chat steht dann ein
  Eintrag „Verpasster Anruf“ (mit „Zurückrufen“), dazu eine Benachrichtigung „Verpasster Anruf von Name“ (abschaltbar
  unter Benachrichtigungen bzw. Mitteilungen → „Anrufe“). Auch beendete, abgelehnte und besetzte Anrufe stehen im Chat.
- **Besetzt:** Wer gerade telefoniert, bekommt keinen zweiten Anruf – der Anrufende sieht „Besetzt“.
- **Wer darf anrufen?** Nur die beiden Personen eines Direkt-Chats, beide mit eingerichteten Chats. Stummgeschaltete
  Chats klingeln nicht aufs Handy. Gegen Klingel-Spam: höchstens 3 Anrufe je Minute an dieselbe Person.
- **Kamera/Mikrofon:** Der Browser fragt beim ersten Anruf. Wurde es verweigert, erklärt Saga, wo man es wieder
  erlaubt (Schloss-Symbol neben der Adresse). Anrufe brauchen `https`.

**Erreichbarkeit:** Im selben Netz und zwischen üblichen Heimanschlüssen klappt die Direktverbindung fast immer – dafür
fragen die Geräte einen **STUN-Server** nach ihrer öffentlichen Adresse (Standard: `stun:stun.nextcloud.com:443`).
Hinter strengen Firmen-Firewalls, in manchen Mobilfunknetzen (CGNAT) oder wenn eine Seite nur Port 443 nach außen darf,
braucht es einen **TURN-Server**, der die Medien weiterreicht (weiterhin verschlüsselt – der TURN-Server kann nicht
mithören). Ohne ihn sehen die Personen „Direktverbindung nicht möglich … Der Admin kann einen TURN-Server eintragen.“

**Admin → Allgemein → „Anrufe“:** **Anrufe erlauben** (Standard an; braucht Chats), **STUN-Server** (leer = keiner;
Datenschutz: der STUN-Server sieht die IP-Adressen der Anrufenden – wer das nicht will, trägt einen eigenen ein, z. B.
den eigenen coturn), **TURN-Server** mit **gemeinsamem Geheimnis** (empfohlen: Saga erzeugt je Anruf Zugangsdaten nach
dem TURN-REST-Verfahren, gültig 12 Stunden; der TURN-Server sieht nur einen Hash statt des Kontonamens) oder **festem
Benutzer/Passwort**, **Nur über TURN verbinden** (die Geräte sehen dann die IP-Adresse des Gegenübers nicht) und die
**Klingeldauer** (15–120 s). Geheimnisse werden verschlüsselt gespeichert; „TURN-Zugangsdaten löschen“ entfernt sie.

#### TURN über Port 443 (coturn hinter dem Reverse Proxy, per SNI)

Saga öffnet keinen Port – und auch für TURN muss keiner dazukommen: Der Reverse Proxy schaut beim Verbindungsaufbau auf
den Namen im TLS-Handshake (SNI) und reicht `turn.example.org` unverändert an coturn weiter, alles andere wie bisher an
die Webserver. Man braucht nur einen zusätzlichen DNS-Namen (und ein Zertifikat dafür).

1. **coturn** installieren (Paket `coturn` oder Container `coturn/coturn`) und `/etc/turnserver.conf`:

```ini
use-auth-secret
static-auth-secret=HIER-EIN-LANGES-ZUFALLSGEHEIMNIS     # z. B. openssl rand -hex 32 – dasselbe im Saga-Admin eintragen
realm=turn.example.org
listening-ip=127.0.0.1                                   # nur der Proxy spricht mit coturn
tls-listening-port=5349
cert=/etc/coturn/fullchain.pem                          # Zertifikat fuer turn.example.org
pkey=/etc/coturn/privkey.pem
fingerprint
no-cli
no-multicast-peers
# keine Weiterleitung ins eigene Netz (Schutz vor Missbrauch als Tunnel) ...
denied-peer-ip=10.0.0.0-10.255.255.255
denied-peer-ip=172.16.0.0-172.31.255.255
denied-peer-ip=192.168.0.0-192.168.255.255
# ... ausser zu den eigenen Relais-Adressen (beide Seiten ueber TURN): IP dieses Servers
allowed-peer-ip=192.0.2.10                               # Beispiel-IP - durch die eigene ersetzen
relay-ip=192.0.2.10
```

2. **Reverse Proxy** auf Port 443 nach SNI aufteilen. Die bisherigen HTTPS-Server lauschen dann intern (hier 8443).

nginx (Modul `stream`, `ssl_preread`):

```nginx
stream {
    map $ssl_preread_server_name $backend {
        turn.example.org  127.0.0.1:5349;     # coturn (macht TLS selbst)
        default           127.0.0.1:8443;     # alle bisherigen https-Server: "listen 127.0.0.1:8443 ssl;"
    }
    server {
        listen 443;
        listen [::]:443;
        ssl_preread on;
        proxy_pass $backend;
    }
}
```

HAProxy:

```
frontend tls443
    bind :443
    mode tcp
    tcp-request inspect-delay 5s
    tcp-request content accept if { req.ssl_hello_type 1 }
    use_backend coturn if { req.ssl_sni -i turn.example.org }
    default_backend web
backend coturn
    mode tcp
    server turn 127.0.0.1:5349
backend web
    mode tcp
    server web 127.0.0.1:8443
```

Traefik (TCP-Router mit TLS-Durchreichung, Einstiegspunkt `websecure` = 443):

```yaml
tcp:
  routers:
    turn:
      entryPoints: [websecure]
      rule: "HostSNI(`turn.example.org`)"
      service: coturn
      tls:
        passthrough: true
  services:
    coturn:
      loadBalancer:
        servers:
          - address: "127.0.0.1:5349"
```

Caddy kann das nur mit dem Plugin `caddy-l4` (Matcher `tls sni turn.example.org` → `proxy 127.0.0.1:5349`).

3. Im **Saga-Admin** eintragen: TURN-Server `turns:turn.example.org:443?transport=tcp`, Zugang „Gemeinsames
   Geheimnis“ und dasselbe Geheimnis wie in coturn. Optional „Nur über TURN verbinden“.
4. **Prüfen:** Anruf zwischen einem Gerät im WLAN und einem Handy im Mobilfunk – oben im Anruffenster steht dann „über
   Relais (TURN)“. Ohne Anruf: `turnutils_uclient -t -S -p 443 -W <geheimnis> turn.example.org` (aus dem coturn-Paket).

Hinweise: TURN über TLS/TCP auf 443 kommt durch nahezu jede Firewall, kostet aber etwas Latenz. Die Weiterleitung
zwischen zwei Geräten läuft in coturn selbst (beide Seiten bekommen eine Relais-Adresse) – deshalb muss der
Relais-Portbereich (`min-port`/`max-port`) nicht von außen erreichbar sein. Wer zusätzlich UDP 3478 öffnen kann, trägt
`turn:turn.example.org:3478` als zweite Adresse ein (schneller, wenn UDP erlaubt ist).

**Technik (Format `saga-call/v1`):** WebRTC mit DTLS-SRTP. Jede Signalisierungs-Nachricht (SDP-Angebot/-Antwort,
ICE-Kandidaten, Zustand) signiert das sendende **Chat-Gerät** mit seinem Ed25519-Schlüssel – über
`LP("saga-call/v1/signal", call_id, von_konto, von_gerät, an_konto, an_gerät, body)` – und versiegelt sie mit
`crypto_box_seal` für das Zielgerät. Der Server sieht weder SDP noch IP-Adressen und kann keinen eigenen
DTLS-Schlüssel unterschieben: Die SDP mit dem DTLS-Fingerabdruck ist signiert, das Gerätezertifikat hängt am
Identitätsschlüssel (Sicherheitsnummer im Chat), und nach dem Verbindungsaufbau vergleicht der Browser den
Fingerabdruck des Gegenübers noch einmal mit der signierten SDP. Passt etwas nicht, bricht der Anruf mit einer
deutlichen Meldung ab. Endpunkte: `/api/calls/config`, `POST /api/calls`, `/api/calls/{id}` (`answer`, `decline`,
`signal`, `end`, `alive`), `/api/calls/active`; Live-Ereignisse `call.ring`, `call.signal`, `call.state`. Testvektoren
für eigene Clients: `tests/call_vectors.json` (`python3 tools/call_vectors.py check`, `node tools/call_vectors.mjs`).
Im Chat stehen Anrufe als Eintrag mit Metadaten (Art, Ergebnis, Dauer – nur, was der Server ohnehin weiß); Apps
bekommen sie mit `?calls=1`. Push: Art `call` mit hoher Priorität und kurzer Lebensdauer (ntfy Priorität 5), für die
iOS-/macOS-App zusätzlich Apple-VoIP-Push (PushKit, Abo-Typ `apns-voip`, Topic `<Bundle-ID>.voip`). Das Zugriffs-Log
enthält für `/api/calls…` keine IP-Adressen.

**Später (Gruppenanrufe):** geplant als Mesh bis etwa 4 Personen im Gruppen-Chat (jede Person baut zu jeder anderen
eine eigene, ebenso geprüfte Verbindung auf; Signalisierung wie oben je Paar). Darüber bräuchte es einen
Weiterleitungs-Server (SFU) mit zusätzlicher Inhaltsverschlüsselung (Insertable Streams/SFrame) – das ist bewusst noch
nicht Teil von Saga.

### Mitteilungen aufs Handy und den Computer

Die Glocke zeigt alles, solange Saga offen ist. Für unterwegs schickt Saga **Mitteilungen** – auch wenn Saga
geschlossen ist. Einschalten unter **Einstellungen → Mitteilungen**:

- **Dieser Browser:** „In diesem Browser einschalten“ und die Nachfrage des Browsers erlauben (Chrome, Edge, Firefox,
  Safari). Auf **iPhone und iPad** geht das nur, wenn Saga als App auf dem Home-Bildschirm liegt (Teilen → „Zum
  Home-Bildschirm“) – dann dort öffnen und einschalten. Braucht `https`. Beim Abmelden in diesem Browser endet das
  Abo; „überall abmelden“ und ein Passwortwechsel beenden alle Browser-Abos.
- **ntfy:** In der App [ntfy](https://ntfy.sh) (Android, iPhone, Linux) ein Topic mit schwer zu erratendem Namen
  abonnieren und unter „ntfy hinzufügen“ dessen Adresse eintragen (z. B. `https://ntfy.sh/mein-geheimes-topic`, bei
  geschützten Topics mit Zugangstoken). Eigene ntfy-Server im Heimnetz gibt der Admin unter Allgemein frei.
- **Apps:** Die Android- und Linux-App melden sich über UnifiedPush an, die iOS-/macOS-App über Apple Push (APNs,
  richtet der Admin ein). Sie erscheinen in der Geräteliste.
- **Geräte:** Liste aller Browser, ntfy-Topics und Apps mit „Test“ und „Entfernen“; „Letzter Fehler“ hilft bei der
  Suche, wenn nichts ankommt.
- **Worüber?** Teilen, Kommentare, Chat und Veröffentlichung einzeln an- oder abschalten. **Namen zeigen** aus →
  nur „Neuer Kommentar auf deiner Seite“ statt „Jonas hat deine Seite kommentiert“. **Ruhezeiten** (z. B. 22:00–07:00,
  in deiner Zeitzone): in dieser Zeit keine Mitteilungen, die Glocke hat trotzdem alles.

Datenschutz: Mitteilungen enthalten nur einen kurzen Satz und den Link – nie Seitentitel, Kommentare oder
Chat-Inhalte. Browser-Mitteilungen sind Ende-zu-Ende verschlüsselt (Web Push, RFC 8291): Der Push-Dienst des
Browsers (Google, Mozilla, Apple, Microsoft) sieht nur verschlüsselte Daten. Über **ntfy** (und UnifiedPush ohne
Verschlüsselung) gehen **nie Namen**, weil der ntfy-Server den Text lesen kann. Vor dem Zustellen prüft Saga, ob du
die Seite noch sehen darfst. Saga verbindet sich nur **ausgehend** – kein zusätzlicher Port.

**Admin → Mitteilungen:** Wege einzeln abschalten (Web Push, ntfy, UnifiedPush) und Apple Push einrichten: im
Apple-Developer-Konto unter *Certificates, Identifiers & Profiles → Keys* einen Schlüssel mit „Apple Push
Notifications service (APNs)“ anlegen, die `.p8`-Datei (gibt es nur einmal) hochladen und Team-ID, Key-ID und die
Bundle-ID der App eintragen. Der Schlüssel wird verschlüsselt gespeichert. Ohne Schlüssel bleibt Apple Push aus.
Den Schlüssel für Web Push (VAPID) erzeugt Saga beim ersten Bedarf selbst; er hängt am `SECRET_KEY`.

### KI-Assistent (optional)

Ist der Assistent eingeschaltet, steht auf der Seiten-Ansicht oben **Frage Saga KI** (bzw. der Name, den der Admin
vergeben hat, z. B. „Frage Marin“) mit seinem Profilbild. Er arbeitet mit einem Sprachmodell,
das der Admin einträgt (z. B. Ollama im eigenen Netz). **Alles, was du dem Assistenten gibst – deine Frage und die dazu
passenden Ausschnitte aus Seiten –, geht an diesen Endpunkt**; die Adresse steht unten in der Seitenleiste.

- **Frag deine Seiten:** Saga sucht in den Seiten, die *du* sehen darfst (eigene und mit dir geteilte; Papierkorb und
  Archiv nicht), schickt die passendsten Abschnitte mit und zeigt die Antwort Wort für Wort. Die Zahlen **[1]**, **[2]**
  führen zu den Quellseiten; darunter stehen alle Quellen. Verweise auf Seiten, die du nicht sehen darfst, gehen nur
  als „Seite ohne Zugriff“ mit. **Aufgaben** und **Kalender** nimmt der Assistent nur dazu, wenn du die Schalter unter
  dem Eingabefeld einschaltest. Chats (Ende-zu-Ende-verschlüsselt) liest er nicht – außer
  Nachrichten, in denen du ihn dort ausdrücklich mit @Name erwähnst (siehe unten); ebenso keine Seiten anderer
  Saga-Instanzen (Föderation).
- **Diese Seite** (auch im Seitenmenü „…“ → KI-Assistent): **Zusammenfassen**, **Aufgaben vorschlagen** (Auswahl
  ankreuzen → „In Aufgaben übernehmen“ oder „Als Checkliste einfügen“), **Markierten Text verbessern** und
  **Übersetzen nach …** für die Blöcke, die du gerade markiert hast. Das Ergebnis ist immer nur ein **Vorschlag**:
  „Ersetzen“ (mit Rückfrage), „Darunter einfügen“ oder „Kopieren“ – der Assistent selbst kann nichts ändern.
  Ersetzen und Einfügen gibt es nur, wenn du die Seite bearbeiten darfst; frühere Fassungen stehen im Versionsverlauf.
- **Verlauf:** standardmäßig aus. Unter „Verlauf“ kannst du ihn einschalten, einzelne Einträge oder alles löschen;
  Ausschalten löscht den gespeicherten Verlauf. Quellen, auf die du keinen Zugriff mehr hast, zeigen keinen Titel mehr.
- Der Admin kann die Zahl der Anfragen je Tag begrenzen („Heute noch 12 von 50 Anfragen“). Es läuft immer nur eine
  Anfrage je Person; **Stopp** bricht eine Antwort ab.

**Für Admins:** Admin-Bereich → **KI-Assistent**. Standard: aus.

| Feld | Bedeutung |
|---|---|
| Anbieter | *OpenAI-kompatibel* (`/v1/chat/completions` – OpenAI, Ollama, LM Studio, vLLM …) oder *Ollama* (eigene Schnittstelle `/api/chat`) |
| Adresse | z. B. `http://ollama.lan:11434/v1` (OpenAI-kompatibel) bzw. `http://ollama.lan:11434` (Ollama). `http` nur für interne Adressen |
| Modell | z. B. `llama3.1:8b` – „Verbindung testen“ listet die angebotenen Modelle und holt eine kurze Antwort |
| API-Schlüssel | optional, verschlüsselt gespeichert (wie LDAP/OIDC); leer lassen = behalten |
| Nur für diese Gruppen | leer = alle Konten, sonst nur Mitglieder (lokale, LDAP- oder SSO-Gruppen) |
| Zeitlimit, Anfragen je Person und Tag, Seiteninhalt je Anfrage | Grenzen (0 Anfragen = unbegrenzt) |
| Verlauf erlauben | Personen dürfen ihren Verlauf einschalten |

Saga baut nur **ausgehende** Verbindungen zu genau dieser Adresse auf – kein zusätzlicher Port. Interne Adressen (LAN-Ollama)
sind erlaubt, aber nur mit den IP-Adressen, die beim Speichern aufgelöst wurden („Festgehaltene Adressen“); ändert sich
die Adresse im DNS, einmal neu speichern. Weiterleitungen folgt Saga nicht. Gleichzeitige Anfragen insgesamt begrenzt
`AI_MAX_PARALLEL` (Standard 4). Fragen, Inhalte und Antworten landen nie im Log.

Zum Schutz vor untergeschobenen Anweisungen („Ignoriere alle Regeln …“ in einer Seite) kennzeichnet Saga Seiteninhalte
für das Modell als Daten und gibt dem Assistenten keinerlei Schreibrechte. Ein Sprachmodell kann sich trotzdem irren
oder sich von Seiteninhalten beeinflussen lassen – Antworten deshalb prüfen, besonders bei geteilten Seiten.

**Persona:** Unter Admin-Bereich → KI-Assistent → **Persona** bekommt die KI einen Namen (leer = „Saga KI“ / „Saga AI“ /
„Saga IA“ je nach Sprache), ein **Profilbild** (PNG, JPEG, GIF, WebP oder HEIC; wird wie Profilbilder geprüft, quadratisch
zugeschnitten und neu kodiert – ohne Bild zeigt Saga ein neutrales ✦-Symbol) und optional eine kurze **Persönlichkeit
und Tonfall** (höchstens 500 Zeichen, z. B. „freundlich und knapp, duzt die Personen“). Der Tonfall wirkt nur auf die
Formulierung eigener Antworten; Regeln, Quellenangaben und Formate haben Vorrang, Verbessern und Übersetzen behalten
den Ton des Textes. Web und Apps zeigen Name und Bild überall, wo die KI vorkommt, und übernehmen Änderungen sofort.

**KI im Chat – nur auf Zuruf:** Schaltet der Admin „KI in Chats erlauben“ ein (Standard: aus), kann die KI in einzelnen
Chats zugelassen werden (Chat-Menü „…“ → „Marin in diesem Chat erlauben“: in freien Gruppen die Chat-Admins, im
Direkt-Chat beide, im Gruppen-Chat Saga-Admins, im Seiten-Chat der Eigentümer der Seite). Alle Mitglieder sehen dann
oben im Chat einen Hinweis. Wer **@Marin** schreibt (beim Tippen von „@“ wird die KI vorgeschlagen), schickt **diese
Nachricht und bis zu 12 Nachrichten davor im Klartext** an den KI-Endpunkt – alles andere bleibt Ende-zu-Ende-
verschlüsselt. Die KI sieht dabei keine Seiten, Aufgaben oder Kalender. Ihre Antwort verschlüsselt das Gerät des
Fragenden wie jede Nachricht für alle Mitglieder; sie erscheint mit Name und Bild der KI („gefragt von …“). Der Server
speichert weder Frage noch Antwort, nur eine Prüfsumme der Antwort, mit der die anderen Geräte prüfen, dass sie
wirklich von der KI stammt. Es gelten Kontingent und Gruppen des Assistenten, dazu höchstens 6 Fragen je Minute.

**Websuche für die KI:** Ist die Websuche (Abschnitt 10) eingerichtet, kann der Admin unter KI-Assistent
„Websuche erlauben“ einschalten (Standard: aus). Dann sucht die KI bei Fragen nach Aktuellem – Wetter, Nachrichten,
Öffnungszeiten – selbst im Web: Das Modell entscheidet vorab, ob eine Suche nötig ist, und bildet höchstens zwei kurze
Suchbegriffe; diese gehen über SearXNG an Suchmaschinen. Saga liest außerdem den Text der besten Treffer (nur
öffentliche Adressen). Die Antwort nennt die Webquellen mit Link; währenddessen steht dort „Marin sucht im Web …“. Im
Assistenten lässt sich das je Frage mit „Im Web suchen“ abschalten; Zusammenfassen, Verbessern, Übersetzen und
Aufgaben vorschlagen suchen nie im Web. Jede Websuche zählt als weitere Anfrage im Kontingent.

### Konto, Kalender, Immich, Apps

Im Benutzermenü oben rechts unter **Einstellungen** (je ein Reiter):

- **Konto:** Name, E-Mail, Passwort ändern (bei LDAP/SSO-Konten beim Anbieter). Nach einem Passwortwechsel
  werden andere Browser-Sitzungen **und alle angemeldeten Apps** abgemeldet – außer dieser Sitzung und der App,
  in der du gerade bist. Die Apps melden sich dann einmal neu an. Hier lässt sich auch der **öffentliche Name**
  ändern (siehe „Seiten veröffentlichen“).
- **Kalender:** CalDAV-Konten verbinden (Nextcloud: `https://cloud.example.org/remote.php/dav/`).
  Am besten ein eigenes App-Passwort nur für Kalender anlegen.
- **Immich:** Adresse und einen API-Schlüssel mit den Rechten `album.read`, `asset.read`, `asset.view`,
  `asset.download`, `user.read` eintragen. Dann gibt es Fotos als Bilder und Titelbilder.
- **Apps & Geräte:** eine Liste aller angemeldeten Apps. Zu jeder gehört ein kleiner Schlüssel für Widget,
  Teilen-Erweiterung und Kurzbefehle; **Abmelden** entfernt beides. Von Hand erzeugte Schlüssel (z. B. für
  Skripte) stehen eingeklappt unter „Weitere Schlüssel“.
- **App per QR-Code anmelden:** unter „Apps & Geräte“ erscheint ein QR-Code, der zwei Minuten gilt
  und nur einmal benutzt werden kann. Mit der Handy-Kamera scannen – die App übernimmt Serveradresse
  und Anmeldung, ganz ohne Passwort-Eingabe am Gerät. Für die Mac- und Linux-App gibt es darunter
  **„Link kopieren“**: den Kopplungslink in der App bei der Anmeldung einfügen.
- **Anmeldung in der App über den Browser:** Die App öffnet die Anmeldeseite von Saga. Bist du dort schon
  angemeldet, fragt Saga nach („App anmelden?“) – nur bestätigen, wenn du die Anmeldung gerade selbst in der
  App gestartet hast. Meldest du dich in diesem Fenster gerade erst an (Passwort oder SSO), entfällt die
  Rückfrage. **Abbrechen** führt zurück in die App.

Öffentliche Kalender- und Immich-Server (https) gehen immer. **Server im Heimnetz** (z. B.
`192.168.1.20` oder `nextcloud.lan`) muss der Admin unter Allgemein freigeben – das schützt das interne Netz
davor, über Saga abgefragt zu werden. Saga verbindet sich dabei direkt mit der geprüften Adresse; ein
Ausgangs-Proxy (`HTTPS_PROXY`/`HTTP_PROXY`) wird für Kalender und Immich **nicht** benutzt.

![Einstellungen](screenshots/einstellungen.png)

### Passkeys und Zwei-Faktor-Anmeldung

Unter **Einstellungen → Konto → Anmeldung & Sicherheit** schützt du ein lokales oder LDAP-Konto zusätzlich zum
Passwort (SSO-Konten machen das beim Anmeldedienst, siehe unten):

- **Passkeys:** Der Browser bzw. das Handy legt einen Schlüssel an, der im Passwort-Manager des Geräts liegt
  (iCloud-Schlüsselbund, Google Passwortmanager, 1Password, Bitwarden …) oder auf einem Sicherheitsschlüssel
  (YubiKey). Damit meldest du dich **ganz ohne Passwort** an – auf der Anmeldeseite „Mit Passkey anmelden“ oder
  direkt aus der Vorschlagsliste beim Benutzernamen – und Fingerabdruck, Gesicht oder Geräte-PIN bestätigen.
  Meldest du dich mit Passwort an, dient der Passkey als zweiter Faktor. Mehrere Passkeys je Konto sind möglich
  (z. B. Handy und Laptop); jeder hat einen Namen, „angelegt“ und „zuletzt benutzt“.
- **Authenticator-App** (TOTP: Aegis, 2FAS, Google/Microsoft Authenticator …): QR-Code scannen oder den
  Schlüssel abtippen, dann einen Code eingeben. Saga fragt den 6-stelligen Code nach dem Passwort ab; jeder Code
  gilt nur einmal.
- **Wiederherstellungscodes:** Mit dem ersten Faktor bekommst du 10 Einmal-Codes – ausdrucken oder im
  Passwort-Manager speichern, Saga zeigt sie nur einmal. Damit kommst du hinein, wenn Handy oder Passkey fehlen.
  „Neue Codes erzeugen“ macht die alten ungültig.

**Anmeldung:** Passwort richtig → Saga fragt nach dem zweiten Faktor (Passkey, Code oder Wiederherstellungscode).
Dieser Zwischenschritt gilt 5 Minuten und nur in diesem Browser; nach 5 falschen Codes ist für 15 Minuten Pause.
Einen „Diesem Browser vertrauen“-Haken gibt es bewusst nicht.

**Bestätigen vor Änderungen:** Passkeys hinzufügen/löschen, die Authenticator-App ändern oder neue Codes erzeugen
geht nur innerhalb von 5 Minuten nach der Anmeldung – sonst fragt Saga vorher nach Passwort, Code oder Passkey.
Dasselbe gilt für „Chats neu beginnen“: dort reichen jetzt auch Passkey oder Code statt des Passworts.

**Apps:** Die Anmeldung in den Apps läuft über die Anmeldeseite von Saga im System-Browserfenster – dort erscheint
der zweite Schritt genauso, Passkeys funktionieren darin auf iPhone, iPad, Mac und Android. Bereits angemeldete
Apps bleiben angemeldet, auch wenn du 2FA einschaltest oder der Admin sie verlangt. Die QR-Code-Kopplung braucht
keinen zweiten Schritt (sie startet in einer schon angemeldeten Sitzung). Verlangt der Admin 2FA für alle und dein
Konto hat noch keinen Faktor, führt eine **neue** App-Anmeldung zuerst zur Einrichtung („Weiter“ geht danach zurück
zur App); einen QR-Code zum Koppeln gibt es erst danach.

**Voraussetzung für Passkeys:** `BASE_URL` mit Rechnernamen und `https://` (z. B. `https://saga.example.org`;
nur `http://localhost` geht ohne https). Ruft man Saga über eine andere Adresse auf als `BASE_URL`, erklärt Saga
das, statt still zu scheitern. Passkeys gehören zur Domain: Wird `BASE_URL` später auf eine andere Domain
umgestellt, funktionieren bestehende Passkeys nicht mehr (Anmeldung mit Passwort + Code/Wiederherstellungscode
geht weiter). Soll ein Passkey für mehrere Subdomains gelten, `PASSKEY_RP_ID` auf die gemeinsame Domain setzen.
Im **Admin-Bereich** gehen Passkeys nur, wenn `ADMIN_URL` unter derselben Domain läuft – sonst dort Code oder
Wiederherstellungscode verwenden. Verlangt die Richtlinie einen Faktor, richtet ein Admin ohne Faktor dort deshalb
eine Authenticator-App ein (Passkeys auf der Hauptseite unter „Anmeldung & Sicherheit“).

**SSO (OIDC):** Saga bietet SSO-Konten keine eigene 2FA an – der Anmeldedienst (z. B. Keycloak) fragt den
zweiten Faktor ab. Mit dem Admin-Schalter „Bei SSO einen zweiten Faktor verlangen“ lässt Saga SSO-Anmeldungen
nur zu, wenn der Anbieter einen meldet (`amr` mit `mfa`, `otp`, `hwk` …) oder einen der eingetragenen `acr`-Werte
(die Saga beim Anbieter auch anfragt).

**Handy verloren:** mit einem Wiederherstellungscode anmelden und den Faktor neu einrichten. Ohne Codes setzt der
Admin den zweiten Faktor zurück (Abschnitt 5) – oder, wenn der Admin selbst nicht mehr hineinkommt, auf dem Server
`docker compose exec saga python run.py 2fa-zuruecksetzen <name>` (Abschnitt 2).

### Profilbilder

Statt der farbigen Initialen zeigt Saga ein **Profilbild** – in der Kopfzeile, bei Kommentaren, in der Anwesenheit
auf einer Seite, in Benachrichtigungen, im Teilen-Dialog und in der Personensuche, im Chat (Liste, Unterhaltung,
Mitglieder) und beim Versionsverlauf („von …“). Ohne Bild bleiben die Initialen.

- **Eigenes Bild:** Einstellungen → Konto → Profilbild → „Bild hochladen …“. Im Browser wählst du einen
  quadratischen Ausschnitt (verschieben, zoomen). Erlaubt sind PNG, JPEG, GIF, WebP und HEIC bis `AVATAR_MAX_MB`
  (Standard 5 MB). Der Server kodiert jedes Bild neu (64/128/256 Pixel, WebP und PNG/JPEG) und entfernt dabei alle
  Metadaten (EXIF mit Kamera, Ort, Datum). „Eigenes Bild entfernen“ löscht es wieder.
- **Gravatar ist Standard:** Wer kein eigenes Bild hat, bekommt – wenn das Konto eine E-Mail-Adresse hat – sein
  [Gravatar](https://gravatar.com). Datenschutz: **Nur der Server** fragt bei Gravatar (mit dem SHA-256-Hash der
  E-Mail-Adresse) und speichert das Ergebnis einen Tag – auch „kein Bild“. Browser und Apps fragen nie selbst bei
  Gravatar; Gravatar erfährt also nicht, wer sich ein Bild ansieht. Jede Person kann Gravatar für sich abschalten
  („Gravatar verwenden, wenn ich kein eigenes Bild habe“), der Admin für alle (Allgemein → Profilbilder).
- **Bild vom Anmeldedienst (optional, Admin):** „Bild aus dem SSO übernehmen“ nimmt die Adresse aus dem
  OIDC-Claim `picture` (abgeholt vom Server, nicht vom Browser), „Bild aus LDAP übernehmen“ liest `jpegPhoto`
  bzw. `thumbnailPhoto` bei der Anmeldung. Interne Adressen holt Saga nur, wenn sie unter „Freigegebene interne
  Hosts“ stehen (Schutz vor SSRF); abgeholte Bilder sind auf 2 MB und wenige Sekunden begrenzt.
- **Reihenfolge:** eigenes Bild → Bild vom Anmeldedienst → Gravatar → Initialen.
- **Wer sieht das Bild?** Die Bildadressen enthalten einen nicht erratbaren Schlüssel (`/api/avatar/<schlüssel>`);
  abrufen können sie nur angemeldete Personen (bzw. Apps). Konten lassen sich darüber nicht durchzählen. Auf
  **veröffentlichten Seiten** erscheint dein Bild neben „@name“ nur, wenn du „Auch auf meinen veröffentlichten
  Seiten zeigen“ einschaltest (Standard: aus).
- **Föderation:** Personen anderer Saga-Instanzen zeigen ihr Bild ebenfalls. Dein Server holt es signiert bei
  deren Instanz ab (höchstens 512 KB), kodiert es neu und speichert es einen Tag; beim Trennen der Verbindung
  wird der Zwischenspeicher gelöscht.
- **Konto löschen** entfernt Bild, Einstellungen und alle zwischengespeicherten Bilder dieses Kontos.

### Sprache

Saga spricht Deutsch, Englisch, Spanisch, Russisch (Русский) und Thai (ไทย); diese Anleitung gibt es auf Deutsch, Englisch
und Spanisch. Welche Sprache du siehst, entscheidet sich in dieser Reihenfolge:

1. deine eigene Wahl unter **Einstellungen** → **Konto** → **Sprache** – gilt sofort und in jedem Browser,
   weil sie in deinem Konto gespeichert wird,
2. die **Standardsprache**, die der Admin unter Allgemein festlegt,
3. die Sprache deines Browsers,
4. sonst Englisch (auch bei allen anderen Browsersprachen).

„Automatisch“ heißt jeweils: Die nächste Stufe entscheidet. Datum und Zahlen richten sich nach der gewählten
Sprache. Die Apps folgen der Sprache des Geräts.

### Auf dem Handy

Saga passt sich kleinen Bildschirmen an und lässt sich als App zum Home-Bildschirm hinzufügen
(„Zum Home-Bildschirm“ im Browser-Menü). Bei den Seiten steht der Inhalt sofort oben; der Seitenbaum öffnet sich
als Schublade über den Knopf oben links. Das Block-Menü öffnet sich mit `/` als Leiste von unten – antippen oder
weitertippen (filtert) und `Enter`. Reiterleisten (Einstellungen, Admin-Bereich) lassen sich seitlich wischen.

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
Dieselbe Anbindung kann auch die KI nutzen („Websuche erlauben“ unter KI-Assistent, siehe oben).

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
| `2` (Standard) | immer die neueste 2.x – neue Funktionen und Fehlerbehebungen, nichts, was Handarbeit braucht |
| `2.1` | nur Fehlerbehebungen von 2.1 |
| `2.1.0` | genau diese Version |
| `1` | bleibt bei der neuesten 1.x (1.2) – ältere Installationen haben diesen Wert noch in der `.env` |
| `testing` | **Testversion:** das Neueste aus dem Branch `testing`, noch vor dem Release. Kann Fehler haben – nur zum Ausprobieren |

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
docker compose exec saga python run.py backup              # nur die Datenbank
docker compose exec saga python run.py backup --dateien    # dazu Bilder/Dateien der Seiten als tar.gz
docker compose exec saga ls -l /data/backups
```

Die Bilder und Dateien der Seiten (`/data/files`) stecken **nicht** im Datenbank-Backup. `--dateien` legt
zusätzlich `saga-dateien-<zeit>.tar.gz` an (ohne Vorschaubilder, die entstehen neu); zurückholen mit
`docker compose exec saga tar -xzf /data/backups/<datei> -C /data`. Wer das Volume `saga-data` ohnehin
sichert (siehe oben), braucht den Schalter nicht.

**Testversion:** Mit `SAGA_VERSION=testing` bekommst du neue Funktionen, sobald sie fertig sind – noch vor
dem Release. Die Version heißt dann z. B. `2.2.0-testing.ab12cd3` (unten im Admin-Bereich mit 🧪). Zurück zur
stabilen Version: `SAGA_VERSION=2`, `docker compose pull && docker compose up -d`. Hat die Testversion die
Datenbank schon umgebaut, startet die stabile Version erst, wenn sie diesen Umbau ebenfalls enthält (spätestens
mit dem nächsten Release) – oder du spielst das Backup von vor dem Wechsel zurück.

Eine neue **Major-Version** kann Handarbeit brauchen – sie steht dann im Changelog und kommt nur, wenn du
`SAGA_VERSION` auf die neue Hauptversion setzt.

**Von 1.x auf 2.0:** Wer noch `SAGA_VERSION=1` in der `.env` hat, bleibt auf 1.2. Für Saga 2.0 `SAGA_VERSION=2`
setzen und wie oben aktualisieren – die Datenbank wird automatisch umgebaut (vorher Backup). Danach im
Admin-Bereich nach Wunsch KI-Assistent, Föderation und Apple-Mitteilungen (APNs) einrichten (sie sind aus) und den
Reverse Proxy für den Live-Kanal prüfen (Abschnitt 3). Alles Weitere steht im [Changelog](../CHANGELOG.md).

**Von 2.0 auf 2.1:** Mit `SAGA_VERSION=2` kommt 2.1 beim normalen Update. Die Datenbank geht auf Schema-Stand 6
(vorher Backup, danach kein Rückweg auf 2.0.x außer über dieses Backup). Neu und gleich aktiv: Profilbilder mit
**Gravatar** (abschaltbar unter Allgemein → Profilbilder) und **Anrufe** mit dem STUN-Server
`stun:stun.nextcloud.com:443` (Allgemein → Anrufe; TURN optional, Abschnitt „Anrufe“). Passkeys brauchen in
`BASE_URL` einen Hostnamen, keine IP-Adresse; die 2FA-Richtlinie steht auf „niemanden“.

**Zurück zur vorigen Version:** Eine ältere Saga startet nicht auf einer Datenbank, die schon eine neuere
Version umgebaut hat (sie meldet das im Log, statt Daten zu beschädigen). Das gilt auch für **2.1 → 2.0.x**: 2.1
hebt den Schema-Stand auf 6 (Backup `…-vor-schema-6.dump`), 2.0.x bricht dann mit „Die Datenbank hat Schema-Stand 6,
diese Saga-Version kennt nur 5“ ab – sonst liefe 2.0 weiter und ließe Konten mit zweitem Faktor nur mit dem
Passwort hinein. Dann das Backup von vor dem Update zurückspielen:

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

**Passkey: „Der Browser meldet die Adresse …, Saga ist aber für … eingerichtet“.**
Saga wurde über eine andere Adresse aufgerufen als `BASE_URL` (z. B. per IP-Adresse oder altem Namen). Passkeys
sind an die Adresse gebunden – Saga über `BASE_URL` öffnen oder `BASE_URL` korrigieren. „Passkeys brauchen eine
Adresse mit Rechnernamen“: `BASE_URL` enthält eine IP-Adresse oder kein `https://`.

**Dienst-Symbole fehlen.**
Saga lädt sie einmal von `cdn.jsdelivr.net` (selfh.st) und speichert sie. Ohne Internet erscheint der
Anfangsbuchstabe. Mit `ICON_CDN=` (leer) holt Saga gar nichts aus dem Internet.

**LDAP: „Dienstkonto-Anmeldung fehlgeschlagen“ oder Zertifikatsfehler.**
Mit „Verbindung testen“ prüfen. Bei eigener CA die Datei nach `certs/` legen (Abschnitt 7).

**SSO: „SSO-Anmeldung fehlgeschlagen“.**
Redirect-URI beim Anbieter prüfen (`BASE_URL/auth/callback`) und mit „Verbindung testen“ das
Discovery-Dokument kontrollieren. Details stehen in `docker compose logs saga`.

**Kalender: „Server ist nicht erreichbar oder nicht freigegeben“.**
Interne Server müssen unter Admin → Allgemein freigegeben sein. Kalender und Immich gehen nicht über
`HTTPS_PROXY`/`HTTP_PROXY` (Schutz davor, dass die Prüfung über den Proxy umgangen wird) – Saga braucht eine
direkte Verbindung zu diesen Servern.

**„Gerade sehr viele Anmeldungen – bitte gleich noch einmal versuchen.“**
Saga prüft höchstens `KDF_PARALLEL` Passwörter gleichzeitig (Standard 4, je Prüfung ca. 32 MB Speicher) und
wartet höchstens 2 Sekunden auf einen freien Platz. Kommt das im Alltag vor (viele Benutzer, kräftiger Server),
`KDF_PARALLEL` in der `.env` erhöhen.

**Anruf: „Direktverbindung nicht möglich“ oder es klingelt, verbindet aber nicht.**
Eine Seite sitzt hinter einer strengen Firewall oder im Mobilfunk-CGNAT. Einen TURN-Server eintragen (Abschnitt
„Anrufe“ → „TURN über Port 443“). Steht im Anruffenster „über Relais (TURN)“, klappt TURN. „Kamera/Mikrofon nicht
erlaubt“: im Browser beim Schloss-Symbol neben der Adresse erlauben; Anrufe brauchen `https`. Klingelt es nicht auf
anderen Seiten, prüfen, ob der Live-Kanal durch den Proxy kommt (Abschnitt 3).

**Hochladen oder Speichern scheitert mit „zu groß“ (413).**
Dateien: `ATTACH_MAX_MB` (Standard 10). Alles andere, z. B. sehr lange Seiten: `REQUEST_MAX_MB` (Standard 2).
Kommt die Meldung vom Reverse Proxy, dort die Grenze anheben (Abschnitt 3).

**Nach dem Passwortwechsel muss ich mich in den Apps neu anmelden.**
Gewollt: Ein neues Passwort meldet alle anderen Browser-Sitzungen und Apps ab (z. B. falls ein Gerät verloren
ging). Nur die Sitzung und die App, in der du das Passwort geändert hast, bleiben angemeldet.

**Die Glocke zählt erst nach einer Minute, offene Seiten aktualisieren sich nicht sofort.**
Dann kommt der Live-Kanal nicht durch und Saga fragt ersatzweise alle 60 Sekunden ab. Meist puffert der Reverse Proxy
die Antwort von `/api/events` oder schließt sie nach kurzer Zeit – Einstellungen in Abschnitt 3. Auch mehr als fünf
offene Tabs mit demselben Konto schalten die ältesten auf Abfrage um.

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
| `PUBLIC_HOSTS` | – | Eigene Domains nur für öffentliche Seiten (Komma-getrennt, Abschnitt 9 „Seiten veröffentlichen“) |
| `PUBLIC_RATE` | `120` | Aufrufe öffentlicher Seiten je IP-Adresse und Minute (Bilder: das Fünffache) |
| `SESSION_HOURS` | `12` | Gültigkeit einer Browser-Sitzung (Apps bleiben angemeldet) |
| `PASSKEY_RP_ID` | Rechnername aus `BASE_URL` | Domain der Passkeys; nur setzen, wenn sie für eine übergeordnete Domain gelten sollen (z. B. `example.org`) |
| `SAGA_VERSION` | `2` | Welche Version `docker compose pull` holt: `2`, `2.1`, `2.1.0` oder `testing` (Abschnitt 11) |
| `BACKUP_KEEP` | `10` | So viele automatische/manuelle Backups bleiben in `/data/backups` |
| `SAGA_UPDATE_CHECK` | `1` | `0` = nie bei GitHub nach neuen Versionen fragen (auch im Admin-Bereich abschaltbar) |
| `ADMIN_SESSION_HOURS` | `4` | Gültigkeit einer Sitzung im Admin-Bereich |
| `TIMEZONE` | `Europe/Berlin` | Zeitzone für Exporte |
| `ATTACH_MAX_MB` / `ATTACH_QUOTA_MB` | `10` / `500` | Größe je Datei / Speicher je Benutzer |
| `AVATAR_MAX_MB` | `5` | Größte Datei beim Hochladen eines Profilbilds |
| `CHAT_QUOTA_MB` | `1024` | Speicher für Chat-Anhänge je Benutzer (verschlüsselt, unter `STATE_DIR/chat`) |
| `REQUEST_MAX_MB` | `2` | Größte Anfrage außer Datei-Uploads (z. B. sehr lange Seiten); größere bekommen 413 |
| `KDF_PARALLEL` | `4` | So viele Passwortprüfungen gleichzeitig (je ca. 32 MB Speicher); weitere warten bis 2 s |
| `NOTE_MAX_COUNT` | `5000` | Seiten je Benutzer |
| `IMMICH_URL` | – | Vorschlag im Immich-Dialog (wird automatisch freigegeben) |
| `ICON_CDN` | selfh.st über jsDelivr | Quelle der Dienst-Symbole; leer = keine |
| `STATUS_INTERVAL` | `60` | Sekunden zwischen Status-Prüfungen |
| `CACHE_TTL` | `60` | Sekunden, die Konto- und Dienstdaten zwischengespeichert werden |
| `LIVE_PING` | `25` | Sekunden bis zur Heartbeat-Zeile im Live-Kanal (hält Proxys offen) |
| `LIVE_MAX_PER_USER` / `LIVE_MAX_TOTAL` | `5` / `2000` | Live-Verbindungen je Konto (die älteste wird geschlossen) / insgesamt |
| `LIVE_MAX_AGE` | `3600` | Nach so vielen Sekunden baut der Browser die Live-Verbindung neu auf (Sitzung wird dabei frisch geprüft) |
| `FED_ALLOW_HTTP` | – | `1` = Föderation auch über http (nur für Tests; sonst nur https) |
| `FED_RATE` / `FED_TIMEOUT` | `600` / `10` | Anfragen je Minute und verbundener Instanz / Sekunden bis zur Zeitüberschreitung |
| `SEARXNG_URL` / `SEARXNG_SECRET` | – | Websuche (Abschnitt 10) |
| `AI_WEB_FETCH` / `AI_WEB_RATE` | `2` / `10` | Websuche der KI: Trefferseiten, deren Text gelesen wird (0 = nur Ausschnitte); Websuchen je Person in 10 Minuten |
| `SEARCH_LANGUAGE` / `SEARCH_SAFESEARCH` | `de-DE` / `0` | Sprache und Filter der Websuche |
| `LOG_LEVEL` | `info` | Ausführlichkeit des Logs |
| `DATABASE_URL` | aus `POSTGRES_*` | Eigene Datenbank-Verbindung (statt des `db`-Containers) |
