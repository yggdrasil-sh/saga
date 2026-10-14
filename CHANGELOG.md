# Änderungen

Alle nennenswerten Änderungen an Saga. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/):
**Major** (2.0.0) = Umbau, der Handarbeit beim Update braucht · **Minor** (1.1.0) = neue Funktionen ·
**Patch** (1.0.1) = Fehlerbehebungen. Datenbank-Änderungen laufen beim Start automatisch, vorher legt Saga ein Backup an.

> **Entwicklung:** Neues landet zuerst auf dem Branch `testing` (Image `ghcr.io/yggdrasil-sh/saga:testing`, wird bei
> jedem Push gebaut) und wird hier unter `[Unveröffentlicht]` notiert.
> **Release:** `testing` nach `main` übernehmen, `VERSION` erhöhen, `[Unveröffentlicht]` in `[x.y.z] – Datum`
> umbenennen und auf `main` pushen. Die GitHub Action legt Tag, Images und Release an.

## [Unveröffentlicht]

### Neu
- **Teilen mit Personen und Gruppen:** Knopf „Teilen“ im Seitenkopf (nur Eigentümer) bzw. Seitenmenü → „Teilen …“.
  Personensuche ab 3 Zeichen (Anzeigename + Benutzername, nie eine Liste aller Konten), Gruppen nur, in denen man
  selbst ist (lokal, LDAP, SSO), Rollen **Lesen**, **Kommentieren**, **Bearbeiten**; Rolle ändern und Freigabe
  entfernen (mit Rückfrage) in der Liste, geerbte Freigaben werden genannt. Eine Freigabe gilt für alle Unterseiten
  und Datenbank-Einträge darunter (höchste Rolle in der Kette; archivierte Seiten, Papierkorb und Vorlagen sieht nur
  der Eigentümer). Entzug, Gruppenwechsel und Abschalten wirken mit der nächsten Anfrage.
  - Seitenleiste: Bereich **„👥 Mit mir geteilt“** (oberste geteilte Seiten mit Eigentümer, aufklappbar, auf dem Handy
    in der Schublade); 👥 an eigenen geteilten Seiten; Suche, Schnellsuche und Verweise kennen geteilte Seiten
  - Fremde Seiten: Kopfzeile „Geteilt von Name · Rolle“; mit Lesen/Kommentieren ist der Editor schreibgeschützt
    (wie „Gesperrt“), Datenbanken zeigen „Nur lesen“. Aufgaben-, Kalender- und Immich-Blöcke zeigen Nicht-Eigentümern
    nur „Nur für den Eigentümer sichtbar“; Verweise auf nicht geteilte Seiten erscheinen als „Seite ohne Zugriff“
  - Bearbeiten: Inhalt, Titel, Symbol, Titelbild, Tags, Eigenschaften und Ansichten ändern, Unterseiten/Einträge
    anlegen, Bilder hochladen – alles gehört dem Eigentümer (zählt bei ihm), Versionen entstehen bei ihm. Löschen,
    Archivieren, Verschieben, Sperren, Vorlage, Veröffentlichen, Versionen und Freigaben bleiben beim Eigentümer
  - Gleichzeitiges Bearbeiten: der Konflikt-Hinweis nennt, wer die Seite gerade geändert hat
- **Kommentare** (dezent): am Block nur eine kleine Zahl, solange es offene Unterhaltungen gibt; „Kommentieren“ im
  Block-Menü (⋮⋮ und Strg/⌘ + /), bei Nur-Kommentieren ein kleiner Knopf am Block. Unterhaltung in einer Seitenleiste
  rechts (Handy: Bottom-Sheet): antworten, eigene bearbeiten/löschen (Eigentümer darf alle löschen), „✓ Erledigt“
  (zugeklappt, ausgeblendet; Schalter „Erledigte zeigen“), „💬 n“ im Seitenkopf, ungelesen = kleiner Punkt, gelöschter
  Block bleibt als „Block entfernt“. Klartext bis 2000 Zeichen, Links werden erkannt, höchstens 30 Kommentare je
  Minute; auch auf eigenen, nicht geteilten Seiten. Erster Kommentar mit fremden Antworten: die Antworten bleiben,
  er erscheint als „Kommentar gelöscht“; der Eigentümer kann die ganze Unterhaltung löschen (`DELETE …?all=1`)
- **Admin → Allgemein → Teilen:** „Teilen erlauben“ (Standard an) und „Nur innerhalb gemeinsamer Gruppen“ (Standard
  aus, wirkt nur auf neue Freigaben); Benutzer-Detail zeigt die Anzahl der Freigaben (erteilt/erhalten). Gruppen aus
  LDAP/SSO gelten ab der nächsten Anmeldung. Konto löschen entfernt Freigaben (eigene
  und an das Konto) und anonymisiert Kommentare („Gelöschtes Konto“); eine gelöschte Gruppe beendet ihre Freigaben
- Schnittstelle für die Apps (nur Erweiterungen, bestehende Antworten bleiben gleich): `GET /api/shared`,
  `GET`/`PUT`/`DELETE /api/notes/{id}/shares`, `GET /api/share/targets?q=`, `GET`/`POST /api/notes/{id}/comments`,
  `PATCH`/`DELETE /api/comments/{id}`, `POST /api/comments/{id}/resolve`, `POST /api/comments/{id}/seen`;
  `GET /api/notes/{id}` liefert zusätzlich `role`, `owner`, `shared`, `comment_counts`, `comments_open`,
  `comments_unread`, `updated_by` (für Nicht-Eigentümer: Titel nicht geteilter Seiten in Verweisen als „Seite ohne
  Zugriff“, `parent_id` null bei unsichtbarer Elternseite; beim Speichern stellt der Server die Verweistexte wieder
  her); Kommentare zusätzlich `deleted`; `GET /api/notes/{id}/publish` zusätzlich `shared_edit` (Hinweis im Dialog,
  wenn mit „Bearbeiten“ geteilt); `GET /api/notes` zusätzlich `shared`; `GET /api/notes/{id}/rows` zusätzlich `role`;
  409 beim Speichern zusätzlich `by`. Uploads in fremde Seiten mit Kopfzeile `X-Saga-Note: <id>` (bzw. `note` beim
  Immich-Import). `MIN_APP` bleibt 1.0.0

- **Teilen, Nachbesserungen:** Konflikt ohne Datenverlust („Meine Version behalten“ mit Rückfrage; die überschriebene
  Fassung bleibt als Version mit Autor erhalten, `overwrite` beim Speichern; „Neue Version laden“ zeigt den eigenen
  Text zum Kopieren); Zugriff endet beim Schreiben → Dialog mit dem letzten Text statt „not found“; alte Links:
  „Kein Zugriff auf diese Seite“; Hinweis für Empfänger, wenn Teilen abgeschaltet ist (`GET /api/share/info`).
  Teilen-Dialog: tatsächliche Rolle über Gruppen/Elternseiten (`effective`, `via`), Mitgliederzahl bei Gruppen,
  Erklärzeile zur Rolle, „Deine Gruppen“ als Chips, Link-Karte, geerbte Freigaben als eigener Bereich. Neue Kommentare
  außerhalb der Seite: Punkt im Seitenbaum (`unread` in `GET /api/notes` und `/api/shared`), Zahl an „Mit mir
  geteilt“, „💬 n“ am Knopf „Seiten“ der Startseite. „Zuletzt geändert von“ / „Angelegt von“ (`created_by`),
  Versionsverlauf mit Autor. Kommentare ruhiger (⋯-Menü je Kommentar, 💬+ für alle, die kommentieren dürfen,
  „Kommentare“ im Seitenmenü der Eigentümerin, leere Zustände mit nächstem Schritt, Handy: Block bleibt über dem
  Bottom-Sheet sichtbar, Trefferflächen ≥ 44 px). Einzahl/Mehrzahl, englische Rollen „Can view/comment/edit“ und
  Aktion „Resolve“ (Kontext `##` in i18n-Schlüsseln)
- Datenbank-Migration 5: `note.created_by`, `note_version.author`

### Geändert
- Zentrale Zugriffsprüfung (`saga/sharing.py`, `note_access`) für alle Seiten-, Datenbank-, Datei-, Such- und
  Export-Endpunkte; ohne Rolle antworten sie wie bisher mit 404, mit zu kleiner Rolle mit 403. Titel nicht geteilter
  Seiten erreichen Nicht-Eigentümer weder über Inhalt, Suche, Export noch Rückverweise; Zwei-Wege-Relationen ändern
  die Gegenspalte nur in Einträgen, die die bearbeitende Person selbst bearbeiten darf. Speichern durch Bearbeiter:
  Längenprüfung erst nach dem Wiedereinsetzen der Verweistexte; ein kopierter Platzhalter bekommt beim Eigentümer den
  aktuellen Titel (nur Seiten desselben Eigentümers). Speichern ohne `rev` über den Stand einer anderen Person sichert
  diesen vorher als Version. Erzwungene Sicherungen von Bearbeitern sind gedrosselt (eine je Seite und Person je 60 s,
  außer der bisherige Stand stammt von jemand anderem); beim Kürzen auf 50 Versionen bleiben die neuesten 10 Fassungen
  des Eigentümers immer erhalten
- Datenbank-Migration 4: `note.updated_by` (wer zuletzt geändert hat); neue Tabellen `note_share`, `note_comment`,
  `note_comment_seen`

## [1.2.0] – 2026-10-14

### Neu
- **Seiten veröffentlichen:** Seitenmenü → „Veröffentlichen …“ macht eine Seite ohne Anmeldung lesbar unter
  `/@name/<adresse>` – serverseitig gerendert, ohne JavaScript (CSP ohne Skripte), hell/dunkel, mobil gut lesbar.
  Optionen: Adresse, „Unterseiten mit veröffentlichen“ (nur Seiten, die über die Seitenstruktur wirklich darunter
  liegen), „Suchmaschinen erlauben“ (Standard: `noindex`), „Als Profilseite (/@name)“ – ohne Profilseite zeigt
  `/@name` eine Liste der öffentlichen Seiten. Im Seitenkopf steht dann „🌐 Öffentlich“ (mit „↗“ zur öffentlichen
  Seite); Unterseiten, die über ihre Elternseite öffentlich sind, zeigen „🌐 Öffentlich (über „…“)“ und verweisen im
  Dialog auf die Elternseite. Die Adresse ändert man mit eigenem Knopf „Adresse ändern“ (mit Rückfrage).
  - **Beenden merkt sich alles:** „Veröffentlichung beenden“ (und „Zurückziehen“ durch den Admin) behält Adresse und
    Einstellungen; erneutes Veröffentlichen nutzt dieselbe Adresse. Vom Admin zurückgezogene Seiten zeigen das im Dialog
  - Großbuchstaben oder „/“ am Ende einer öffentlichen Adresse → 301 auf die richtige Schreibweise
  - **Öffentlicher Name** je Konto (nie der Anmeldename): beim ersten Veröffentlichen gewählt (Vorschlag aus dem
    Anzeigenamen, Prüfung auf frei und gültig schon beim Tippen, reservierte Namen gesperrt), änderbar unter
    Einstellungen → Konto (eigener Block mit eigenem Knopf)
  - Inhalte laufen bei der Ausgabe durch eine strenge Positivliste; Verweise auf andere Seiten werden nur zu Links,
    wenn das Ziel ebenfalls öffentlich ist; Bilder nur, wenn genau diese Seite sie benutzt; Aufgaben, Kalender,
    Immich, Schaltflächen und Dateianhänge entfallen; Datenbanken als einfache Tabelle
  - Alle Fehlerfälle liefern dieselbe neutrale 404-Seite; Rate-Limit für öffentliche Pfade (`PUBLIC_RATE`)
  - Papierkorb/Archiv: nicht erreichbar, die Veröffentlichung bleibt aber gespeichert und gilt nach dem
    Wiederherstellen wieder; endgültiges Löschen und Konto löschen entfernen sie
  - **Admin → Allgemein:** „Veröffentlichen erlauben“ (Standard an) und „Startseite für Besucher“ (eine
    veröffentlichte Seite unter `/` für Besucher ohne Anmeldung, oben rechts „Anmelden“); neuer Reiter
    **„Veröffentlicht“** mit „Zurückziehen“
  - Eigene Domain nur für öffentliche Seiten: `PUBLIC_HOSTS=blog.example.org` (`canonical` zeigt dann auf diese Domain)
  - Schnittstelle für die Apps: `GET`/`PUT`/`DELETE /api/notes/{id}/publish`, `GET`/`PUT /api/account/public-name`;
    `GET /api/notes/{id}` meldet zusätzlich `published`, `public_url` und `published_via` (Elternseite, über die eine
    Unterseite öffentlich ist)
  - **Design-Runde (Web):** Dialog in fester Reihenfolge Schalter → Link → Adresse → Optionen → „Veröffentlichung
    beenden“; Schalter statt Häkchen; solange privat zeigt die Link-Karte die künftige Adresse blass und ohne Link,
    die Optionen stehen unter „Gilt, sobald die Seite öffentlich ist“; „Adresse ändern“ erscheint erst, wenn die
    Eingabe abweicht; Profiladresse mit eigenem Kopieren; auf dem Handy „Teilen“; Fehler direkt am Feld (im
    Namensschritt statt des Hilfetexts); eigene Rückfragen statt Browser-`confirm()` mit Titel und Knöpfen
    („Beenden“, „Adresse ändern“, „Ändern“); Adressen brechen nur nach „/“ um; „beendet“ steht nur noch einmal da.
    Einstellungen → Öffentlicher Name zeigt beim Tippen „Neue Adresse: … (bisher …)“
  - **Öffentliche Seite:** Inhalt mit `lang` der Kontosprache des Besitzers (sonst Server-Standard), Bedienung in der
    Sprache des Besuchers; Absenderzeile „@name · aktualisiert <Datum>“; Lesebreite ~70 Zeichen (Bilder, Tabellen,
    Datenbanken, Spalten breiter); Leiste mit @name bzw. Name der Instanz, „Anmelden“ als ruhiger Textlink;
    Checklisten-Häkchen wirken nicht mehr bedienbar; die neutrale 404 bietet „Zur Startseite“ bzw. „Anmelden“
  - Neue Tabellen `public_name` und `note_publish`; Datenbank-Migration 3 (Spalten `active`, `withdrawn_at`,
    `withdrawn_by` – vorher legt Saga wie immer ein Backup an)
- Editor: „/Bild“ + Enter wählt den normalen Bild-Block (vorher „Bild aus Immich“); beim Einfügen eines Links keine
  CSP-Fehler mehr in der Konsole (Markierung der Auswahl ohne Inline-Style); direkt nach dem Öffnen einer Seite
  erscheint nicht mehr kurz „Ungespeichert“ und es wird nichts gespeichert, solange nichts geändert wurde
- Deaktivierte Knöpfe sehen überall deaktiviert aus

## [1.1.0] – 2026-10-14

### Neu
- Mehrsprachigkeit: Web-Oberfläche und Admin-Bereich auf Deutsch, Englisch und Spanisch (inkl. Editor, Datenbanken,
  Kalender, Einstellungen, Meldungen und Fehlermeldungen der Schnittstelle). Sprachwahl: Einstellungen → Konto →
  „Sprache“, sonst die Standardsprache des Servers (Admin → Allgemein → „Standardsprache“), sonst die Browsersprache,
  sonst Englisch. Datums- und Zahlenformate folgen der Sprache. Kataloge in `saga/i18n/{de,en,es}.json`,
  Prüfung mit `python3 tools/i18n.py check`
- Admin → Benutzer: **„SSO-Verknüpfung lösen“** (mit Rückfrage) für SSO-Konten – nach einem Wechsel des
  OIDC-Anbieters (neue `sub`-Kennungen) verknüpft die nächste SSO-Anmeldung das bestehende Konto neu

### Oberfläche (Web)
- „Notizen“ heißt in der ganzen Oberfläche jetzt **„Seiten“** (Knopf im Dashboard, Titel, Meldungen)
- Einstellungen mit **Reitern** (Konto · Lesezeichen · Kalender · Immich · Apps & Geräte) statt eines langen Dialogs;
  auf dem Handy scrollt die Reiterleiste waagrecht (Verlauf am Rand, aktiver Reiter immer ganz sichtbar) – ebenso
  die Navigation im Admin-Bereich
- Dashboard-Suche findet auch **Seiten** (Volltext, Treffer unter den Kacheln); die Schnellsuche (Strg+K) zeigt
  Inhaltstreffer und wählt ohne Titel-Treffer den ersten Inhaltstreffer statt „Neue Seite“
- **Handy:** Seitenleiste als Schublade (Knopf oben links), der Inhalt steht sofort oben; die Schublade schließt sich
  nach der Auswahl, auf Touch-Geräten springt beim Öffnen keine Tastatur mehr auf
- **Block-Menü („/“):** Tippen + Enter übernimmt den ersten Treffer; auf dem Handy öffnet „/“ das Menü auch bei
  Tastaturen ohne Tastencode, und der Text nach „/“ filtert die Einträge (Enter wählt den ersten)
- **Kopplungslink kopieren** (Einstellungen → Apps & Geräte) für die Mac- und Linux-App – gleicher Code wie der QR-Code
- Bessere **Kontraste** (Text mind. 4,5:1, Feldränder 3:1, hell und dunkel) und sichtbarer **Tastatur-Fokus** überall
- Dienst- und Lesezeichen-Symbole im Dunkelmodus auf einer hellen **Plakette** (dunkle Logos wie Vaultwarden bleiben sichtbar)
- Dashboard: Kategorien nebeneinander, Konto-Kasten in der Fußzeile, Tastenkürzel-Hinweise nur mit Tastatur
- Admin-Bereich: Zeiten in der Zeitzone des Browsers mit Kürzel (z. B. MESZ), Tabellen auf dem Handy als Karten
- **Aufgaben abhaken** (Dashboard und Block „Aufgaben“ in Seiten): Meldung „Aufgabe erledigt · Rückgängig“, 8 s lang
  (wie in den Apps); Rückgängig nimmt bei Wiederholungen auch die nächste Aufgabe zurück
- Dashboard: „Aufgaben“ steht nur noch einmal – mit sichtbarer rechter Leiste entfällt der Aufgaben-Kasten im
  Hauptbereich; Einklappen der Leiste bzw. „Alle Aufgaben …“ öffnet ihn dort (mit Priorität, Datum, Erledigten)
- Dashboard-Suche ohne Treffer zeigt „Keine Treffer.“
- „+ Lesezeichen“: Adresse ist das erste Feld (Cursor dort), der Name ist optional und zeigt den Rechnernamen als Vorschlag
- Block-Menü: Die Aufzählung heißt „Aufzählungsliste“, „Liste“ findet sie also auch

### Sicherheit (Team-Review)
- Größe von Anfragen begrenzt (Hauptseite und Admin-Port): allgemein 2 MB (`REQUEST_MAX_MB`), Datei-Uploads
  `ATTACH_MAX_MB` + Formular-Rahmen, Admin-Formulare 1 MB. Zu große Anfragen bekommen sofort 413 – auch ohne
  Content-Length (chunked) und vor jeder Anmeldung. Zwischendateien von Uploads und ZIP-Export liegen im
  Daten-Volume (`/data/tmp`) statt im RAM-tmpfs `/tmp`
- Passwortprüfung (scrypt) höchstens 4 gleichzeitig (`KDF_PARALLEL`); wer länger als 2 Sekunden auf einen freien
  Platz wartet, bekommt eine Meldung („gleich noch einmal versuchen“) statt vollem Speicher oder blockierter Anfragen
- App-Anmeldung: `/app/pair`, `/app/exchange`, `/app/resume` und `/app/forget` nur noch mit
  `Content-Type: application/json` (fremde Webseiten können so keine Anmeldung/Abmeldung auslösen; alle Apps senden JSON).
  `/app/handoff` fragt vor der Übergabe an die App nach („App anmelden?“) – außer direkt nach einer frischen
  Anmeldung (Formular/SSO, höchstens 2 Minuten) in genau diesem Ablauf. „Abbrechen“ führt zurück in die App
  (`saga://auth?error=cancelled` bzw. `hlidskjalf://…`) statt aufs Dashboard
- Kalender (CalDAV): Wer Adresse oder Benutzer ändert, muss das App-Passwort neu eingeben (auch beim Verbindungstest).
  CalDAV und Immich verbinden nur noch mit der beim Verbindungsaufbau geprüften IP (Schutz gegen DNS-Rebinding).
  **Achtung:** Dadurch gehen diese Verbindungen nicht mehr über `HTTPS_PROXY`/`HTTP_PROXY` – sonst ließe sich die
  Prüfung über den Proxy umgehen. Wer CalDAV/Immich nur über einen Proxy erreicht, braucht eine direkte Verbindung
- Eigener Passwortwechsel meldet jetzt auch alle Apps/Geräte und App-Schlüssel ab – außer dem Gerät, in dem gerade
  geändert wird (`"logout_devices": false` behält sie)
- SSO (OIDC): Das Konto merkt sich die Kennung `sub` der ersten Anmeldung; eine andere Kennung mit gleichem
  Benutzernamen wird abgewiesen. Wird der Benutzername aus der E-Mail gebildet, gilt `email_verified: false` als Ablehnung
- Sperre nach Fehlversuchen: je Benutzer und Adresse (8), je Benutzer insgesamt (40), je Adresse (30); der Admin-Port
  zählt getrennt. Warnung (Log und Admin-Bereich), wenn ein Reverse Proxy nicht in `TRUSTED_PROXIES` steht
- Kopplungs-Code (`/app/pair?c=…`) erscheint nicht mehr im Zugriffs-Log

### Behoben
- App-Anmeldung: Wer sich im Browser erst frisch anmelden musste, blieb danach hängen (der Browser blockierte den
  Sprung zurück in die App). Jetzt erscheint kurz „Zurück zur App“, die App öffnet sich automatisch oder per Knopf.
- DB-Tabelle: Enter im Namen ließ den Fokus auf die Seite fallen, weiter Getipptes ging verloren. Jetzt springt Enter
  in die nächste Zeile bzw. legt in der letzten Zeile einen neuen Eintrag an; was währenddessen getippt wird, landet
  im neuen Namen
- Block-Menü: Escape (auf dem Handy: Tipp neben das Menü) entfernt das „/“ und den getippten Filter wieder
- Standardnamen (z. B. Ansicht „Tabelle“) bei Anfragen ohne Browser-Sprache (API, Apps) bei Konto-Sprache
  „Automatisch“ und ohne Server-Standard: jetzt in der Sprache, in der die Weboberfläche zuletzt angezeigt wurde,
  statt Englisch
- Dienst-Symbole im WebP-Format vom CDN wurden nicht umgewandelt (fehlender Import)
- Speichern einer Seite: Fehlerantworten (z. B. ungültiges Symbol nach einer Schema-Änderung) speichern nichts mehr halb;
  die Revisionsprüfung ist atomar (gleichzeitige Speicherungen mit derselben Revision -> 409)
- Sicherheitsnetz: Wird eine Seite (über 400 Zeichen) geleert oder um mehr als die Hälfte kleiner, sichert Saga den
  alten Stand immer als Version – auch innerhalb der 10 Minuten seit der letzten Sicherung
- Aufgaben: „heute“ (Wiederholungen) nach der eingestellten Zeitzone statt der des Containers
- Kalender-Fehlermeldungen in der Sprache der Anfrage (vorher immer Englisch); Kalender-Zwischenspeicher begrenzt
- Standardnamen: Seite „Eingang“ und Spalten/Ansichten neuer Datenbanken in der Konto-Sprache (statt der Sprache
  der gerade anfragenden App); die Kategorien „Dienste“/„Lesezeichen“ werden in der Sprache der Anzeige gezeigt
- Export: Datumsangaben im Format der Sprache; Admin-Bereich zeigt Zeiten in der eingestellten Zeitzone (mit Kürzel)
- Immich: unerwartete Antworten führen zu einer Meldung statt zu einem Serverfehler
- Konto löschen entfernt auch offene QR-Kopplungs-Codes
- ZIP-Export: Die Zwischendatei wird auch gelöscht, wenn der Download abbricht; liegengebliebene Zwischendateien
  (älter als 1 Stunde) räumt Saga beim Start und stündlich auf

### Intern
- `app.py` in Bereiche aufgeteilt (Paket `saga/`: Seiten, Datenbanken, Kalender, Apps …); Verhalten und Schnittstellen unverändert
- Seiten speichern und suchen im Threadpool statt im Event-Loop; die Sprachwahl fragt die Datenbank nicht mehr im
  Event-Loop; ZIP-Export als Temp-Datei (nur die Inhalte der exportierten Seiten werden geladen)
- `python run.py backup --dateien` sichert zusätzlich die Bilder/Dateien (`/data/files`) als tar.gz; ohne den Schalter
  weist `backup` darauf hin, dass sie im Datenbank-Backup fehlen
- Neue Testreihe `tests/haertung.py`

### Betrieb
- Testversion: Branch `testing` baut bei jedem Push das Image `ghcr.io/yggdrasil-sh/saga:testing` (`SAGA_VERSION=testing`)

### Dokumentation
- Anleitung (de/en/es): „Seiten“ statt „Notizen“, `backup --dateien`, `REQUEST_MAX_MB` und `KDF_PARALLEL`,
  Body-Limit im Reverse Proxy, Verhalten beim Passwortwechsel und bei der App-Anmeldung, Proxy-Hinweis für CalDAV/Immich
- README, Anleitung und Projektseite auch auf Englisch und Spanisch: `README.md` ist jetzt englisch
  (deutsch: `README.de.md`, spanisch: `README.es.md`), Anleitung unter `docs/en/GUIDE.md` und `docs/es/GUIA.md`,
  Projektseite unter `/en/` und `/es/` mit Sprachumschalter

## [1.0.0] – 2026-10-13

Erste Version mit Versionsnummer.

### Funktionen
- Dashboard mit Diensten (Kacheln je nach Gruppe), Lesezeichen, Suche und Websuche (SearXNG, optional)
- Seiten mit Blöcken, Datenbanken (Tabelle, Board, Liste, Galerie, Kalender), Vorlagen, Versionsverlauf, Papierkorb
- Aufgaben, Kalender (CalDAV: Nextcloud, mailcow, Radicale, iCloud …), Bilder aus Immich
- Konten: lokal, LDAP/Active Directory (auch Gruppen für SSO-Konten), SSO/OIDC; Admin-Bereich auf eigenem Port
- Apps (iOS/iPadOS/macOS, Linux) bleiben dauerhaft angemeldet; **Anmeldung per QR-Code**
- **Apps & Geräte** als eine Liste: Abmelden entfernt Anmeldung und Widget-Schlüssel eines Geräts zusammen

### Betrieb
- Versionsnummer (`VERSION`), `GET /api/version` und Anzeige im Admin-Bereich
- Fertige Images unter `ghcr.io/yggdrasil-sh/saga` (amd64, arm64); Update mit `docker compose pull && docker compose up -d`
- Nummerierte Datenbank-Migrationen mit **automatischem Backup** vorher; ältere Versionen starten nicht auf einer neueren Datenbank
- `python run.py backup` / `restore` / `version`
- Hinweis auf neue Versionen im Admin-Bereich (abschaltbar)
