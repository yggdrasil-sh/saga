# Handbuch-Bilder

Aufgenommen am 3.11.2026; am 23.11.2026 fast alle Web-Bilder neu (Saga 2.5, Seitenleiste „Heute · Chats · Aufgaben ·
Termine“, Bugs-DB 2189 und 2244). Hosts `saga.example` (Familie
Sommer) und `verein.example` (Gartenverein). Desktop 1440×900 (Faktor 2), Handy 390×844 (Faktor 3). Nummern im Bild =
Legende rechts. Abweichungen von der Gliederung stehen unten.

**Neu aufnehmen** – die Werkzeuge liegen im Repository und laufen auf der Testmaschine (root, Testumgebung
`tools/testumgebung`, eigener Netz-Namensraum und eigene Datenbank je Lauf):

```sh
source /srv/saga-test/env.sh
tools/handbuch-stack.sh --liste                      # welche Bilder die Skripte kennen (Welt familie|verein, Art server|knoten)
tools/handbuch-stack.sh --nur a06-02,b13-08          # einzelne Bilder (auch Vorsilben wie b18 oder a)
tools/handbuch-stack.sh                              # alle bekannten Bilder
```

- `tools/handbuch-stack.sh` startet je Welt und Art einen Lauf der Testumgebung (Art `knoten`: Server hinter
  saga-media mit eingebautem Knoten; ohne `UMG_BIN` baut das Skript gjallarhorn und saga-media wie im Image mit der
  Version aus `VERSION`) und darin `tools/screens-handbuch.py`. Der eingebaute Knoten des Gartenvereins heißt
  `knoten.verein.example` (Zertifikat der Test-CA, nur im Lauf). Log je Lauf:
  `$UMG/log/screens-handbuch.py.<UMG_NS><slot>-<welt>-<art>.log`.
- `tools/handbuch-demo.py` legt die Demo-Welt an (Konten, Profilbilder, Seiten, Datenbanken, Aufgaben, Dienste,
  Verbindungen) – per DB und HTTP-API wie ein Browser.
- `tools/handbuch-fakes/fakes.py`: Weiterleitung der Anzeige-Hosts (`saga.example`, `admin.saga.example`,
  `verein.example`, `admin.verein.example`, `cloud.saga.example`, `bookorbit.example`, `wetter.saga.example`,
  `ki.saga.example` → 127.0.1.x:80, nur im Namensraum), Nextcloud-, BookOrbit-, Wetter- (Open-Meteo-Format) und
  KI-Attrappe (OpenAI-kompatibel, feste Antworten), gezeichnete Bilder (Profilbilder, Titelbilder, Buchcover,
  Dokument-Vorschau). Für die Föderations-Bilder startet das Skript `verein.example` als zweiten Server im selben
  Namensraum (eigene DB, `FED_ALLOW_HTTP=1`) und verbindet beide über die Admin-Oberflächen.
- `tools/screens-handbuch.py` nimmt die Bilder mit Playwright/Chromium auf (Ordner `HB_OUT`); Kamera und Bildschirm
  sind gezeichnete Ersatzbilder, das Runensiegel braucht Noto Sans Runic (lädt `handbuch-stack.sh` nach `HB_FONTS`).
- Bilder ohne Szene im Skript (noch vom 3.11.) stehen unten unter „Durchgang 4“; `--liste` zeigt alle Szenen.

| Datei | Kapitel | Plattform | hell/dunkel | was zu sehen ist | Nummern-Legende |
|---|---|---|---|---|---|
| a01-01-uebersicht.png | A01 | Web | hell | Admin-Übersicht Gartenverein: Menü (mit Titelbilder, Diagnose, Netz), aktive Benutzer, Admins, Gruppen, Dienste, Anmeldung, Medien, zuletzt angemeldet | 1 = Menü des Admin-Bereichs; 2 = Kennzahlen |
| a01-02-admin-login-2fa.png | A01 | Web | hell | Anmeldung im Admin-Bereich (eigene Adresse admin.…), Benutzername frieda; danach ggf. zweiter Faktor | – |
| a01-03-uebersicht-dunkel.png | A01 | Web | dunkel | Admin-Übersicht im dunklen Modus | – |
| a02-01-benutzerliste.png | A02 | Web | hell | Benutzer: admin, frieda (Admin), greta, paul – Gruppen, letzte Anmeldung | 1 = Liste (Klick auf den Namen öffnet das Konto) |
| a02-02-benutzer-detail.png | A02 | Web | hell | Konto „paul“: Profil & Rechte, Gruppen, Passwort, 2FA, Sitzungen, löschen | 1 = Profil, Admin-Recht, Gruppen; 2 = Passwort setzen; 3 = 2FA zurücksetzen |
| a02-03-gruppen.png | A02 | Web | hell | Gruppen mitglieder, s_kalender, s_wiki, vorstand (Kennung s_ = Zugang zum Dienst) | – |
| a03-01-dienste.png | A03 | Web | hell | Dienste: Vereinswiki (nur s_wiki) und Vereinskalender (für alle, Status geprüft) | – |
| a03-02-dienst-formular.png | A03 | Web | hell | Dienst „Vereinswiki“ bearbeiten: Kurzname wiki ↔ Gruppe s_wiki, Symbol, Kategorie, für alle | – |
| a04-01-2fa-verlangen.png | A04 | Web | hell | Allgemein → Zweiter Faktor: 2FA verlangen (niemand / Admins / alle), Passkeys | 1 = 2FA verlangen für |
| a04-02-ldap.png | A04 | Web | hell | LDAP / AD: Vorlage (FreeIPA, 389 DS, AD …), Benutzer, Gruppen, Testen – leer | – |
| a04-03-sso.png | A04 | Web | hell | SSO / OpenID Connect: Anbieter, Weiterleitungs-Adresse, Konten & Gruppen – leer | – |
| a04-04-hinweis-proxy.png | A04 | Web | hell | Hinweis TRUSTED_PROXIES (nachgestellt, Beispieladresse 172.18.0.1) – Abhilfe in der Anleitung Kap. 3 | 1 = Hinweis (erscheint 24 h nach dem letzten Auftreten) |
| a05-01-allgemein-seiten.png | A05 | Web | hell | Allgemein → Seiten: Vorlagengalerie, Editor (Tiptap/Editor.js/wählbar), Editor-Diagnose, live, Wetter | – |
| a05-02-allgemein-chats.png | A05 | Web | hell | Allgemein → Chats: erlauben, Aufbewahrung, Speicher, Videogröße, Linkvorschau, Moderne Chats | – |
| a05-03-api.png | A05 | Web | hell | Allgemein → API, Webhooks und MCP | – |
| a05-04-ki.png | A05 | Web | hell | KI-Assistent: OpenAI-kompatibel, Adresse ki.verein.example (festgehalten 192.0.2.10, Beispiel), Modell, API-Schlüssel leer | – |
| a05-05-mitteilungen.png | A05 | Web | hell | Mitteilungen: Web Push, ntfy, UnifiedPush, Apple Push (Felder leer) | – |
| a05-06-diagnose.png | A05 | Web | hell | Allgemein → Diagnose: „Diagnose senden“ der Benutzer (clientlog) | – |
| a06-01-anrufe.png | A06 | Web | hell | Anrufe → 1:1-Anrufe: STUN/TURN (Geheimnis leer), nur über TURN, Anruf-Diagnose, Klingeldauer, Gruppenanrufe ohne Knoten | – |
| a06-02-knoten-liste.png | A06 | Web | hell | Gjallarhorn (Experimentell): drei Schalter (alle an), Tunnel-Kasten („Bereit als Rückweg“), Knoten-Liste mit dem eingebauten Knoten knoten.verein.example (Bereit, 2.5.0), Erweitern | – |
| a06-03-knoten-koppeln.png | A06 | Web | hell | Erweitern: weiteren Knoten koppeln – Name (Vorschlag des Servers), Optionen, Kopplungs-Skript erzeugen | 1 = Name des neuen Knotens; 2 = Skript erzeugen (auf dem neuen Server ausführen) |
| a06-04-tunnel-kasten.png | A06 | Web | hell | Kasten „Anrufe über die Saga-Adresse (Tunnel)“ nach „Jetzt prüfen“: Zustand „Bereit als Rückweg“ (Knoten mit eigenem Namen), „Proxy reicht WebSocket durch? Ja ✓“, Tunnel, Browser (WebSocket) | – |
| a07-01-foederation.png | A07 | Web | hell | Föderation (ganze Seite): diese Instanz verein.example, Fingerprint, Einladen, Verbinden, verbundene Instanz saga.example | – |
| a07-02-einladung.png | A07 | Web | hell | Einladungscode erzeugen (gilt einmal; Code hier erfunden) – die andere Instanz trägt ihn ein | – |
| a07-03-funktionen-je-instanz.png | A07 | Web | hell | Funktionen je Instanz (Familie Sommer, saga.example): Protokoll, gemeinsame Fähigkeiten, Chat, Anrufe, Adressbuch, gemeinsame Gruppen | – |
| a08-01-veroeffentlicht.png | A08 | Web | hell | Veröffentlicht (Familie): „Sommerfest-Einladung“ von Anna (@anna-sommer) – zurückziehen | 1 = Zurückziehen |
| a08-02-meldungen.png | A08 | Web | hell | Meldungen aus Chats: Grund Spam, gemeldet von Oma Lena, Chat, Absender Ben, mitgeschickter Text – keine Inhalte | – |
| a09-01-log-ausschnitt.png | A09 | Terminal | dunkel | `docker compose logs --no-log-prefix saga`: echte Zeilen `clientdiag {"id":…}` und `clientlog` aus dem Demo-Lauf (gekürzt), Konto nur als Hash | – |
| a09-02-diagnose-liste.png | A09 | Web | hell | Admin → Diagnose (Gartenverein): Kennung, Art, Zeitraum, Filtern, Als Datei herunterladen; Meldungen von Browser, iOS und Android; Aufbewahrung | – |
| a10-01-update-hinweis.png | A10 | Web | hell | Update-Hinweis im Admin-Bereich (nachgestellt: 2.5.1 verfügbar) | 1 = Hinweis mit „Was ist neu?“ und Befehl |
| b01-01-login.png | B01 | Web | hell | Anmeldeseite von saga.example mit Passwort und „Mit Passkey anmelden“ | 1 = Benutzername; 2 = Passwort; 3 = Mit Passkey anmelden; 4 = Sprache vor der Anmeldung |
| b01-02-login-2fa.png | B01 | Web | hell | Zweiter Schritt der Anmeldung: Code aus der Authenticator-App oder Passkey | 1 = Code aus der App (oder Wiederherstellungscode); 2 = stattdessen mit Passkey bestätigen |
| b01-03-sicherheit.png | B01 | Web | hell | Anmeldung & Sicherheit: Passkey „Annas MacBook“, Authenticator-App, Wiederherstellungscodes | 1 = Passkeys; 2 = Authenticator-App; 3 = Wiederherstellungscodes / Bestätigen |
| b01-04-profilbild-zuschneiden.png | B01 | Web | hell | Profilbild zuschneiden: Ausschnitt verschieben und zoomen | 1 = Ausschnitt (ziehen); 2 = Zoom |
| b01-05-sprache.png | B01 | Web | hell | Einstellungen → Profil & Sprache → Sprache (Automatisch, Deutsch, English, Español, Русский, ไทย), darunter Darstellung | 1 = Sprache (Automatisch folgt dem Browser) |
| b01-06-login-handy.png | B01 | Handy | dunkel | Anmeldeseite von saga.example mit Passwort und „Mit Passkey anmelden“ | 1 = Benutzername; 2 = Passwort; 3 = Mit Passkey anmelden; 4 = Sprache vor der Anmeldung |
| b01-07-einstellungen-reiter.png | B01 | Web | dunkel | Einstellungen mit der Navigation links (Konto bis Hilfe), rechts „Profil & Sprache“ | 1 = Bereiche: Konto (Profil & Sprache · Sicherheit · Mitteilungen) · Arbeitsbereich (Seiten · Kalender · Lesezeichen) · Verbindungen (Immich · BookOrbit · Nextcloud · Apps & Geräte · API) · Daten (Mein Backup) · Hilfe |
| b02-01-dashboard.png | B02 | Web | hell | „Heute“ von Anna: Seitenleiste links, Begrüßung, Schnellaktionen, Weitermachen, Kacheln (Familie, Freizeit, Haus, Urlaub), rechts offene Aufgaben | 1 = Heute · Chats · Aufgaben · Termine; 2 = Schnellaktionen (N, V, T, E); 3 = Kacheln der Dienste; 4 = Leiste einklappen |
| b02-02-dashboard-dunkel.png | B02 | Web | dunkel | „Heute“ dunkel | – |
| b02-03-dashboard-handy.png | B02 | Handy | hell | „Heute“ auf dem Handy: Begrüßung, Schnellaktionen, Weitermachen, Kasten Aufgaben, Kacheln; unten Heute · Seiten · Suchen · Chats | – |
| b02-04-startseite-festlegen.png | B02 | Web | hell | Seitenmenü ⋯ von „Sommerfest“ mit „Als Startseite festlegen“ | 1 = Als Startseite festlegen |
| b03-01-lesezeichen-dialog.png | B03 | Web | hell | Einstellungen → Lesezeichen (Clara): Vertretungsplan und Mensa, neues Lesezeichen mit Adresse schule.example.org | 1 = Adresse (Pflicht) |
| b03-02-kacheln.png | B03 | Web | hell | Claras „Heute“ mit eigenen Lesezeichen (Mensa, Vertretungsplan) in der Kategorie „Schule“ | 1 = eigenes Lesezeichen; 2 = + Lesezeichen |
| b04-01-seitenbaum.png | B04 | Web | hell | Seitenleiste: Suche, Heute/Chats/Aufgaben/Termine, Seitenbaum „Familie Sommer“, Neue Seite, „Mit mir geteilt“ (Radtour von Ben), Fußleiste | 1 = Seiten durchsuchen; 2 = + Neue Seite; 3 = Mit mir geteilt; 4 = Schnellsuche Strg/⌘+K (daneben Graph); 5 = Archiv; 6 = Papierkorb (30 Tage) |
| b04-02-slash-menu.png | B04 | Web | hell | Slash-Menü in einer leeren Zeile von „Notizen Elternabend“ – Weitertippen filtert, Esc schließt | – |
| b04-03-auswahl-leiste-farbe.png | B04 | Web | hell | Auswahl-Leiste über markiertem Text, Farbauswahl „A“ geöffnet | 1 = fett; 2 = kursiv; 3 = Text- und Hintergrundfarbe; 4 = Formel im Text |
| b04-04-block-menu.png | B04 | Web | dunkel | Block-Menü am Griff ⋮⋮ (Hinweis-Kasten): Aktion suchen, Umwandeln, Farbe, Duplizieren, Verschieben, synchronisierter Block, Link zum Block, Kommentieren, Löschen | 1 = Griff ⋮⋮ (klicken = Menü, ziehen = verschieben) |
| b04-05-spalten-ziehen.png | B04 | Web | hell | Zwei Spalten „Ferienwohnung Möwe“ / „Pension Seestern“, Trenner zum Ziehen | 1 = Spaltenbreite ziehen; 2 = Spalten: Anzahl |
| b04-06-tabelle-farben.png | B04 | Web | hell | Tabelle „Ausflüge“ mit Kopfzeile; Leiste Zeile · Spalte · Zelle (zum Einfärben) | 1 = Tabellenleiste: Zeile · Spalte · Zelle |
| b04-07-mathe-mermaid.png | B04 | Web | hell | Mathe-Block (KaTeX) und Mermaid-Diagramm mit Umschalter Vorschau/Geteilt/Code | 1 = Vorschau · Geteilt · Code |
| b04-08-erwaehnung.png | B04 | Web | hell | @-Menü: Personen, Datum, Erinnerung | – |
| b04-09-synced-block.png | B04 | Web | hell | Synchronisierter Block „Wichtige Nummern“ (Original im Familien-Wiki) | 1 = Original öffnen |
| b04-10-befehlspalette.png | B04 | Web | dunkel | Schnellsuche (Strg/⌘+K) mit „Re“: Seiten nach Titel und Inhalt | – |
| b04-11-editor-handy.png | B04 | Handy | hell | Seite „Urlaub Ostsee 2027“ im Handy-Browser (unten Heute · Seiten · Suchen · Chats) | – |
| b04-12-versionen.png | B04 | Web | hell | Versionsverlauf von „Urlaub Ostsee 2027“ mit Autor und Zeit | – |
| b04-14-unterseiten-galerie.png | B04 | Web | hell | „Familien-Wiki“ mit dem Block „Unterseiten“ als Galerie (Kartengröße M, Kartenbild = Titelbild) | – |
| b05-01-titelbild-symbol.png | B05 | Web | hell | Datenbank „Rezepte“ mit gezeichnetem Titelbild und Symbol 🍲 | 1 = Titelbild ändern/verschieben/entfernen; 2 = Symbol (Emoji) |
| b05-02-seitenmenue-gestalten.png | B05 | Web | hell | Seitenmenü ⋯ → Darstellung: Volle Breite, Schrift (Standard/Serif/Mono), Kleiner Text, Titelbild, Seite sperren | 1 = Volle Breite; 2 = Schrift; 3 = Titelbild |
| b05-03-gestaltet-dunkel.png | B05 | Web | dunkel | Claras „Lernplan Abi“: Titelbild, Serif-Schrift, volle Breite (dunkel) | – |
| b06-01-galerie.png | B06 | Web | hell | Vorlagengalerie: Filter Alle/Persönlich/Arbeit/Team und alle Vorlagen | – |
| b06-02-galerie-vorschau.png | B06 | Web | hell | Vorschau der Vorlage „Home-Dashboard“ mit Inhaltsliste | – |
| b06-03-wetter-ort.png | B06 | Web | hell | Beim Anlegen: Ort für das Wetter-Widget suchen („Lübeck“, Wetter-Attrappe) | 1 = Ort suchen |
| b06-04-home-dashboard.png | B06 | Web | hell | Annas Seite aus der Vorlage „Home-Dashboard“ (ganze Seite) | – |
| b06-05-home-dashboard-dunkel.png | B06 | Web | dunkel | Home-Dashboard dunkel | – |
| b06-06-widget-einrichten.png | B06 | Web | hell | Widget „Countdown“ einrichten: Datum, Richtung, Einheit, Vorschau | 1 = Einstellungen (jede Änderung sofort in der Vorschau) |
| b06-07-home-dashboard-handy.png | B06 | Handy | hell | Home-Dashboard im Handy-Browser | – |
| b06-09-widget-reihe.png | B06 | Web | hell | Vier Widgets in Spalten, gleich hoch: Countdown, Fortschritt, Kennzahl (offene Aufgaben), Gewohnheiten (7 Tage) | – |
| b07-01-tabelle-einkauf.png | B07 | Web | hell | Einkaufsliste als Tabelle mit Abteilung, Formel „Summe“ und Summenzeile | 1 = Ansichten (Reiter); 2 = Filter; 3 = Ansicht-Einstellungen; 4 = + Neu |
| b07-02-board-status.png | B07 | Web | hell | Hausputz als Board nach Status (Offen · Dabei · Erledigt) | – |
| b07-03-kalender-woche.png | B07 | Web | hell | Familienkalender in der Wochenansicht | – |
| b07-04-galerie-rezepte.png | B07 | Web | hell | Rezepte als Galerie: Bild, Tags, Gesamtzeit, Person | – |
| b07-05-zeitleiste-umzug.png | B07 | Web | hell | Familienkalender als Zeitleiste (Balken ziehbar) | – |
| b07-06-diagramm.png | B07 | Web | dunkel | Diagramm-Ansicht „Aufwand je Raum“ (Summe Minuten) – dunkel | – |
| b07-07-filter-relativ.png | B07 | Web | hell | Filter „Wann ist nächste Woche“ – relative Bedingungen (heute, morgen, nächste Woche …) | 1 = Filter (Anzahl aktiver Filter); 2 = Bedingung, auch relativ |
| b07-08-formel-editor.png | B07 | Web | hell | Formel-Editor der Spalte „Summe“: prop("menge") * prop("preis") | – |
| b07-09-peek.png | B07 | Web | hell | Rezept „Apfelkuchen nach Oma Lena“ im Peek rechts | 1 = Peek rechts/mittig; 2 = Als Seite öffnen |
| b07-10-csv-import.png | B07 | Web | hell | Zeilen aus CSV hinzufügen: Vorschau und Zuordnung der Spalten (Semikolon, Excel Deutsch) | – |
| b07-11-notion-import.png | B07 | Web | hell | Importieren: Markdown/Obsidian, Notion-ZIP, CSV | – |
| b07-13-tabelle-handy.png | B07 | Handy | hell | Einkaufsliste im Handy-Browser | – |
| b08-01-aufgabenleiste.png | B08 | Web | hell | Aufgabenleiste rechts auf „Heute“: fällig, überfällig, Quelle | 1 = Aufgabe schnell hinzufügen; 2 = Aufgaben (Kreis = erledigt); 3 = alle Aufgaben |
| b08-02-aufgabe-optionen.png | B08 | Handy | hell | Kasten Aufgaben auf „Heute“: neue Aufgabe mit Priorität ⚑, Wiederholung (Menü offen) und Datum | 1 = Priorität; 2 = Wiederholung; 3 = Datum |
| b08-03-aufgabenquelle.png | B08 | Web | hell | Datenbank „Hausputz“ als Aufgabenquelle: Zuordnung erledigt/Fällig/Priorität/Person | – |
| b08-06-rueckgaengig.png | B08 | Handy | dunkel | Aufgabe abgehakt – kurz „Rückgängig“ möglich | 1 = Rückgängig |
| b09-01-live-cursor.png | B09 | Web | hell | Anna und Ben gleichzeitig in „Sommerfest“ (live): Anwesenheit oben, Bens Markierung | 1 = Wer gerade hier ist |
| b09-02-versionen.png | B09 | Web | hell | Versionen: ältere Fassung von Ben ansehen und wiederherstellen | – |
| b09-03-konflikt.png | B09 | Web | hell | Konflikt ohne live: Ben hat denselben Absatz zuerst gespeichert | 1 = Neue Version laden (deine Änderung geht verloren); 2 = Meine Version behalten (Bens Fassung bleibt als Version) |
| b10-01-teilen-dialog.png | B10 | Web | hell | Teilen von „Urlaub Ostsee 2027“ (Reiter Personen): Ben bearbeitet, Clara kommentiert | – |
| b10-02-kommentare.png | B10 | Web | hell | Kommentare zur Seite: Ben zur Restzahlung, Annas Antwort, Claras Frage | 1 = Kommentare ein-/ausblenden |
| b10-03-veroeffentlichen.png | B10 | Web | hell | Teilen → Im Web: öffentliche Adresse, Unterseiten, Suchmaschinen, Profilseite | – |
| b10-04-oeffentliche-seite-handy.png | B10 | Handy | dunkel | Veröffentlichte „Sommerfest-Einladung“ ohne Anmeldung (dunkel) | – |
| b10-05-mit-mir-geteilt.png | B10 | Web | hell | Oma Lena: „Mit mir geteilt“ mit Haushalt und Sommerfest (über die Gruppe familie) | 1 = Mit mir geteilt |
| b11-01-teilen-foederiert.png | B11 | Web | hell | Frieda (verein.example) teilt „Parzellenplan 2027“ mit anna-sommer@saga.example (zweiter Server im Lauf, echte Föderation) | 1 = Handle name@server eingeben; 2 = schon geteilt: Anna auf saga.example (Bearbeiten) |
| b11-02-fremde-seite.png | B11 | Web | hell | Anna öffnet „Parzellenplan 2027“ von verein.example in ihrem Saga | 1 = Mit mir geteilt (auch von anderen Servern); 2 = Geteilt von Frieda Linde (verein.example) · Du kannst bearbeiten |
| b12-01-chatliste.png | B12 | Web | hell | Chats: in der Seitenleiste Direktchat mit Ben und Familienchat „Sommers 🏡“, rechts leer | – |
| b12-02-familienchat.png | B12 | Web | hell | Familienchat: angeheftete Nachricht, Claras Antwort mit Zitat, Reaktion, Bens Foto | 1 = Antwort mit Zitat; 2 = Reaktion; 3 = Bild |
| b12-03-gruppeninfo.png | B12 | Web | hell | Gruppeninfo „Sommers 🏡“: Name, Beschreibung, Rechte, Mitglieder (Anna Admin) | 1 = Name und Bild (✎ ändern); 2 = Gruppenrechte; 3 = Medien, Dateien, Links |
| b12-04-gruppeninfo-handy.png | B12 | Handy | dunkel | Gruppeninfo auf Bens Handy (dunkel) | – |
| b12-05-kontakte.png | B12 | Web | hell | Neue Unterhaltung: Kontakte (zuletzt zuerst) und Suche nach Personen | 1 = Kontakte |
| b12-06-linkvorschau.png | B12 | Web | hell | Linkvorschau: Titel, Beschreibung und Seitenname der Einladung | 1 = Vorschau (vom Server geholt, wenn der Admin sie erlaubt) |
| b12-07-angeheftet.png | B12 | Web | hell | Angeheftete Nachricht oben im Chat (bis zu 3; Klick springt hin) | 1 = angeheftete Nachricht |
| b12-08-geraet-bestaetigen.png | B12 | Web | hell | Neuer Browser (Clara): Chats mit anderem Gerät bestätigen oder Wiederherstellungsschlüssel eingeben | 1 = Mit anderem Gerät bestätigen (Code vergleichen); 2 = Wiederherstellungsschlüssel eingeben |
| b12-09-wiederherstellungsschluessel.png | B12 | Web | hell | Wiederherstellungsschlüssel eingeben (32 Zeichen A–Z, 2–7; hier erfunden) | 1 = Schlüssel (Leerzeichen egal) |
| b12-10-archiv.png | B12 | Web | hell | Rechtsklick in der Chatliste: öffnen, stummschalten, anheften, ungelesen, sperren, melden, archivieren, löschen | 1 = Archivieren |
| b13-01-anruf-video.png | B13 | Web | dunkel | Videoanruf Anna ↔ Ben über den Knoten (dunkel, Kamera-Ersatzbild statt Gesicht, Bens Handy) | 1 = Stummschalten; 2 = Kamera aus; 3 = Bildschirm teilen; 4 = Auflegen |
| b13-02-bildschirm-teilen.png | B13 | Web | hell | Ben teilt seinen Bildschirm (Fotos vom Urlaub) – Anna sieht ihn groß | – |
| b13-03-gruppenanruf.png | B13 | Web | dunkel | Gruppen-Videoanruf mit vier Personen über den Knoten (dunkel) | – |
| b13-04-hierher-uebernehmen.png | B13 | Web | hell | Bens Laptop: „Anruf läuft auf Chrome · Android …“ – Hinzufügen oder Hierher übernehmen | 1 = Hierher übernehmen |
| b13-05-klingeln-handy.png | B13 | Handy | dunkel | Ben wird angerufen (Handy-Browser, dunkel): Ablehnen, nur Ton, mit Video | 1 = Ablehnen; 2 = Mit Video annehmen |
| b13-07-begleitgeraet-teilt.png | B13 | Web + Handy | dunkel | Zusammengesetzt: Annas Laptop (Begleitgerät, teilt den Bildschirm) und ihr Handy (zeigt die Präsentation), 1:1 mit Ben über den Knoten | – |
| b13-08-teilnehmer-nicht-dabei.png | B13 | Web | dunkel | Gruppenanruf, Teilnehmerliste: Anna, Ben; „Nicht dabei (2)“ Clara und Oma Lena (nicht angenommen) | 1 = Nicht dabei; 2 = Erneut klingeln; 3 = Person hinzufügen |
| b13-09-einzel-zu-gruppe.png | B13 | Web | dunkel | 1:1 Anna ↔ Ben, „Person hinzufügen“ Clara → Gruppenanruf mit drei Kacheln (neue Gruppe „Ben Sommer, Clara Sommer“) | – |
| b13-10-in-gruppenanruf-verschoben.png | B13 | Web | dunkel | Bens 1:1 mit Anna danach: Eintrag „In Gruppenanruf verschoben“ mit Dauer | 1 = Zur Gruppe |
| b14-01-glocke.png | B14 | Web | hell | Glocke oben in der Seitenleiste: geteilte Seite, neue Kommentare – „Alle gelesen“ | 1 = Glocke mit Zahl; 2 = Alle gelesen |
| b14-02-mitteilungen-einstellungen.png | B14 | Web | hell | Einstellungen → Mitteilungen: dieser Browser, Geräte, ntfy, Kategorien, Ruhezeiten | – |
| b14-03-glocke-handy.png | B14 | Handy | dunkel | Glocke auf dem Handy (dunkel, Liste von unten) | – |
| b15-01-ki-seite.png | B15 | Web | hell | Frage Saga KI (Attrappe ki.saga.example): Antwort zum „Sommerfest“ mit Verweisen und Quellen | 1 = Bereiche: Frag deine Seiten · Diese Seite; 2 = Verweis auf die Quelle; 3 = Quellen |
| b15-02-ki-websuche.png | B15 | Web | hell | KI mit Websuche: Wetter am Wochenende, Quellen aus dem Web (Attrappe) | 1 = Im Web suchen; 2 = Web-Quelle |
| b15-03-ki-chat.png | B15 | Web | hell | KI im Familienchat auf Zuruf (`@Saga KI`, Attrappe) – Antwort für alle sichtbar | – |
| b16-01-kalender-verbinden.png | B16 | Web | hell | Einstellungen → Kalender: Familienkalender verbunden, zweiten Kalender (CalDAV) hinzufügen | 1 = verbundener Kalender „Familie“; 2 = Adresse des CalDAV-Servers (Passwort: App-Passwort, hier leer) |
| b16-02-immich.png | B16 | Web | hell | Bild aus Immich: Album „Ostsee 2026“ (Attrappe), Foto wählen → wird in die Seite kopiert | 1 = Alben; 2 = gewähltes Album; 3 = Foto anklicken |
| b16-03-nextcloud-datei.png | B16 | Web | hell | Nextcloud-Datei als Verweis in „Ferienwohnung Möwe“: Karte mit Vorschau, Herunterladen, In Nextcloud öffnen (Attrappe cloud.saga.example) | – |
| b16-04-bookorbit-verbunden.png | B16 | Web | hell | Einstellungen → BookOrbit: verbunden (Attrappe bookorbit.example), „Markierungen als Seite übernehmen“ mit drei Büchern | – |
| b16-05-bookorbit-buchseite.png | B16 | Web | hell | Buchseite „Das Leuchtfeuer von Hiddensee“ unter „Bücher“: Cover, Autorin, Notiz, Kapitel-Unterseiten mit Zahl der Markierungen | – |
| b16-06-bookorbit-kapitel.png | B16 | Web | hell | Kapitelseite „3 Sturmflut“: Markierungen als Zitate, Notiz zur Markierung darunter | – |
| b17-01-qr-kopplung.png | B17 | Web | hell | Apps & Geräte: App per QR-Code anmelden (Code gilt 2 Minuten, nur einmal) | 1 = QR-Code mit der App scannen |
| b18-01-api-token-neu.png | B18 | Web | hell | Neuer API-Token „Home Assistant“: Bereiche wählen (Token selbst wird nicht gezeigt) | 1 = Name; 2 = Bereiche (nie mehr Rechte als dein Konto) |
| b18-02-webhook.png | B18 | Web | hell | Webhook anlegen: Adresse und Ereignisse (Signatur mit eigenem Geheimnis) | 1 = Adresse des Empfängers; 2 = Webhook anlegen |
| b19-01-diagnose-senden.png | B19 | Web | hell | Einstellungen → Hilfe: Diagnose senden (Text + letzte Ereignisse, keine Inhalte) | 1 = kurze Beschreibung; 2 = Diagnose senden → Kennung D-XXXXX für den Admin |
| b19-02-mein-backup.png | B19 | Web | hell | Mein Backup: Ziel (Nextcloud/WebDAV), Schlüssel, Zeitplan – noch nicht eingerichtet | – |

## Abweichungen von der Gliederung

- b05-04 (iPhone), b06-08 (Android), b07-12 (iPhone), b08-04 (Android), b08-05 (iPhone): App-Bilder – kommen mit den
  Simulatoren/Emulatoren in einem eigenen Durchgang, nicht aus dem Browser.
- b04-06: Tabelle zeigt die Leiste „Zelle einfärben“ (geöffnet), die Demo-Tabelle selbst hat noch keine Zellfarben.
- b04-07: Mermaid-Diagramm ist „Packen → Fähre → Ferienwohnung → Strand“ aus „Urlaub Ostsee 2027“, nicht
  „Wochenplan Fahrdienst“.
- b07-05: Zeitleiste des Familienkalenders statt „Umzug“; b07-06: Diagramm „Aufwand je Raum“ (Hausputz) statt
  Haushaltsbuch.
- b01-05: „Sprache“ im Benutzermenü öffnet die Einstellungen (Konto → Sprache, Auswahlliste); kein eigenes Menü.
- b02-01: Aufgabenleiste zeigt „Fähre buchen“ u. a. statt „Milch kaufen“ und die Beispielaufgaben der Vorlage
  „Home-Dashboard“ (Aufgabenquelle, mit festen Daten im November – daher „überfällig“); ohne CalDAV-Attrappe keine Termine.
- b08-02: Optionen (Priorität/Wiederholung/Datum) gibt es nur im Aufgaben-Bereich (Handy/schmale Ansicht), nicht in der
  Schnell-Leiste rechts – Bild daher als Handy.
- b07-04 (Design-Fund, behoben in der Demo): Galerie-Karten zeigen Eigenschaften ohne Beschriftung; reine Zahlen (30/45/75)
  sind unverständlich. Demo blendet sie aus und zeigt „75 Min.“ als Formel-Text. Saga bietet keine Beschriftungen auf
  Galerie-Karten – Vorschlag: Option „Eigenschaftsnamen zeigen“.
- b01-02/b01-03: Passkeys brauchen https – Aufnahme in einer TLS-Phase (handbuch-stack.sh), Faktoren danach entfernt.
- b09-03 (Konflikt): aufgenommen mit kurz abgeschaltetem „Gleichzeitig bearbeiten live“ (danach wieder an). Weil Saga 2.5
  ohne live die Änderung des anderen nachlädt bzw. blockweise zusammenführt, ist der Konflikt nachgestellt: Annas Speichern
  schickt den alten Stand ohne Zusammenführen (wie ein älterer Client) → echte Server-Antwort 409 und echte Leiste.
- App-Durchgang (Simulatoren/Emulatoren, nicht aus dem Browser): b11-03 (iOS), b12-11 (iOS), b12-12 (Android dunkel),
  b13-06 (iOS), b15-04 (iOS), b17-02 … b17-09 (iOS/iPad/Mac/Android/Linux), b19-03 (Android).
- B13 Anrufe: Demo läuft über http – Kamera/Mikrofon ersetzt das Skript im Browser durch ein gezeichnetes Ersatzbild
  (Initialen, keine Gesichter) und stillen Ton; WebRTC läuft echt zwischen den Browser-Kontexten. Seit Durchgang 4
  laufen alle Anrufbilder über den eingebauten Knoten (Art `knoten`, „Alle Anrufe über Knoten“ ab Werk an), auch b13-03.
- b13-04: „Anruf läuft auf …“ entsteht mit zwei Browsern derselben Person (Bens Handy mit Android-Kennung und sein
  Laptop auf der Seite „Radtour an die Schlei“) – Gerätename „Chrome · Android“ kommt vom Test-Browser.
- b14-02: Abschnitt „Dieser Browser“ zeigt „kann keine Mitteilungen empfangen (oder Saga läuft nicht über https)“ – Demo
  läuft über http; auf einem echten Server steht dort der Schalter.
- b15-02: Websuche über eine SearXNG-Attrappe (Treffer wetter.example.org / kueste.example.org); b15-03 KI im Chat zeigt
  die Antwort mit rohen ** (siehe Design-Fund).
- b16-02: Immich-Attrappe des ersten Durchgangs (Album „Ostsee 2026“, gezeichnete Bilder) unter fotos.saga.example – im neuen
  `tools/handbuch-fakes/fakes.py` noch nicht nachgebaut.
- b12-08/b12-09: neuer Browser von Clara ohne Chat-Schlüssel; Schlüssel in b12-09 ist erfunden.
- b18-01: nur das Formular (Name, Bereiche) – der erzeugte Token wird nicht gezeigt.
- Admin: a08-01/a08-02 auf saga.example (Familie, Admin anna), weil nur dort veröffentlicht/gemeldet wird; Meldung in a08-02
  ist für das Bild angelegt („Ausprobiert für das Handbuch“). a04-04 (TRUSTED_PROXIES) und a10-01 (Update 2.5.1) sind
  nachgestellt (gleiche Stelle/Klasse wie im Server; lokal nicht auslösbar). a06-03: „Erweitern“ (Gjallarhorn ab Werk an),
  Name = Vorschlag des Servers; Kopplungs-Skript nicht erzeugt. a07-02: Code
  ersetzt durch DEMO-EINLADUNG-…. a05-04: Adresse ki.verein.example, festgehaltene Adresse
  192.0.2.10 (Beispieladresse, direkt in den Einstellungen der Demo).
  a09-01: Terminal-Bild (HTML) aus echten Log-Zeilen des Demo-Laufs; Kennung auf D-7K3FM gesetzt (wie im Text), lange
  Zeilen gekürzt.
- a01-02: Admin-Anmeldung ohne zweiten Faktor (Demo verlangt keinen); 2FA-Abfrage selbst siehe b01-03.
- b10-04: öffentliche Adresse ist `saga.example/@anna-sommer/sommerfest` (öffentlicher Name der Demo), nicht `@sommer`.

## Funde aus Durchgang 2

- Bug (behoben im Skript, nicht in Saga): Datumsfelder folgen dem Gebietsschema des Browser-Prozesses – Chromium braucht
  `LANG=de_DE.UTF-8`, `locale='de-DE'` im Kontext reicht nicht (b06-06 und b07-07 zeigten mm/dd/yyyy bzw. 07/15/2027).
- Bug (Test-Umgebung): Playwright speichert Blobs in IndexedDB als leere Objekte; Saga reicht sie an den Krypto-Worker
  weiter → DataCloneError, Chatbild „nicht geladen“. Saga könnte ungültige Cache-Einträge verwerfen statt das Bild
  aufzugeben (static/chat.js bGet: nur ArrayBuffer/Blob akzeptieren).
- Design: KI-Antwort im Chat (b15-03) zeigt Markdown roh (**Getränke**), im KI-Seitenpanel dagegen fett.
- Hinweis: Ein nicht aufgelegter Anruf blockiert den nächsten („Du telefonierst gerade schon“) – das Skript legt jetzt
  nach jedem Anrufbild auf.
- Design: b14-02 – auf http fehlt jeder Hinweis, wie man Mitteilungen doch bekommt (ntfy/App); Text nennt nur https.

## Durchgang 3 (23.11.2026, Werkzeuge im Repository)

- Neu aufgenommen, weil die Oberfläche sich geändert hat: alle Einstellungs-Bilder mit der alten Reiterleiste (jetzt
  Navigation links): b01-04, b01-05, b01-07, b03-01, b14-02, b16-01, b17-01, b18-01, b18-02, b19-01, b19-02; dazu
  a05-01 („Standard (Tiptap)“), a06-02 (Tunnel-Betrieb, „Alle Anrufe über Knoten“ an, Knoten-Liste), a09-01.
- Anzeige-Hosts: Der Server läuft mit `BASE_URL=http://saga.example` bzw. `http://verein.example`; im Namensraum zeigen
  die Namen per `/etc/hosts` auf 127.0.1.x, Port 80 leitet `fakes.forward` weiter. Deshalb http, nicht https.
- a06-02/a06-04 (Art `knoten`): seit Durchgang 4 heißt der eingebaute Knoten im Skript `knoten.verein.example` (eigenes
  Zertifikat der Test-CA) und die Binaries melden `2.5.0` (gebaut wie im Image) – im Bild ist nichts mehr ersetzt. Der Knoten
  hat einen eigenen Namen, daher „Bereit als Rückweg“; den Tunnel-Betrieb („An ✓ …“) gibt es nur mit https-`BASE_URL`
  hinter einem Proxy (nicht in der Testumgebung).
- B13 (Art `knoten`, „Alle Anrufe über Knoten“ an): Kamera = gezeichnete Initialen, Bildschirm = gezeichnete Urlaubsfotos
  (Skript im Browser), stiller Ton; WebRTC läuft echt über den Knoten. Das eigene Bild ist gespiegelt (wie in Saga). Gerätename
  „Chrome · Linux“ bzw. „Chrome · Android“ (Handy mit Android-Kennung) kommt vom Test-Browser. Hinweise (Toasts) sind vor dem
  Bild entfernt. b13-07 ist aus zwei Bildern zusammengesetzt (Laptop links, Handy rechts).
- b13-08: Clara und Oma Lena haben je ein Gerät mit Chat-Schlüssel, nehmen aber nicht ab („nicht angenommen“).
- b16-01: Der Kalender-Zugang „Familie“ steht nur für dieses Bild in der DB (kein CalDAV-Server; sonst zeigt „Termine“
  einen Fehler).
- b16-03/b16-04/b16-05: Nextcloud- und BookOrbit-Attrappe aus `tools/handbuch-fakes/fakes.py` (erfundene Dateien, Bücher,
  Markierungen; gezeichnete Cover und Vorschau).
- a09-02: Pauls Meldung kommt echt aus dem Browser („Diagnose senden“); die Meldungen von iOS und Android sind dieselbe
  Meldung, über dieselbe API mit anderem Client geschickt, dazu ein Fehlerbericht der Android-App.
- Neue Nummern: b04-13, b06-08 und b13-06 sind für App-Bilder vergeben, die neuen Web-Bilder heißen daher b04-14, b06-09
  und b13-07 … b13-10.


## Durchgang 4 (23.11.2026, Seitenleiste – Bugs-DB 2244)

- Neu aufgenommen, weil die Oberfläche jetzt die Seitenleiste „Heute · Chats · Aufgaben · Termine“ mit Benutzermenü unten
  links bzw. im Admin-Bereich ein längeres Menü (Titelbilder, Diagnose, Netz) hat: alle Admin-Bilder außer a05-01 und a09-*,
  dazu b02-*, b03-02, b04-01/02/04/05/06/07/08/10/11/12, b05-01/02/03, b06-01 … b06-05, b06-07, b07-01 … b07-06, b07-09,
  b07-13, b08-01/02/03/06, b09-01/02, b10-*, b11-01/02, b12-01 … b12-05, b12-07 … b12-10, b13-01 … b13-05, b14-01/03,
  b15-01, b15-03 (KI im Chat zeigt weiter die rohen ** – Design-Fund aus Durchgang 2 besteht).
- Demo-Welt (tools/handbuch-demo.py) neu: Haushalt mit Einkaufsliste, Rezepte, Familienkalender, Hausputz; Sommerfest mit
  veröffentlichter Einladung; „Urlaub Ostsee 2027“ mit Spalten, Tabelle, Mathe, Mermaid, Kommentaren und Versionen
  (Versionen mit Zeitstempel direkt in `note_version`); Notizen Elternabend; Claras Lernplan; Bens Radtour (mit Anna geteilt);
  Home-Dashboard aus der Vorlage (deutsch, Wetter Lübeck aus der Attrappe). Zufällige Titelbilder sind für die Demo-Konten aus.
- Chats (b12, a08-02): Schlüssel, Gruppe „Sommers 🏡“, Antworten, Reaktion, Foto und Anheften über die Oberfläche im
  Browser (Ende-zu-Ende wie bei echten Nutzern); die Meldung in a08-02 schickt Oma Lena über die API („Ausprobiert für das
  Handbuch“).
- Föderation (b11, a07-01/03): zweiter Server verein.example im selben Lauf, verbunden über die Admin-Oberflächen
  (Einladungscode, Fingerprint, Bestätigen), dann echtes Teilen mit `anna-sommer@saga.example`.
- b09-01: „Gleichzeitig bearbeiten live“ nur für dieses Bild an; Bens Markierung ist eine echte Textauswahl in seinem Browser.
- Weiter die Bilder vom 3.11. (nicht neu aufgenommen): b01-01/b01-06 (Anmeldeseite unverändert; der Passkey-Knopf erscheint
  nur über https), b01-02/b01-03 (TLS-Phase mit Passkeys), b04-03 (Auswahl-Leiste; oben angeschnitten noch die alte
  Kopfleiste), b04-09 (synchronisierter Block), b06-06 (Widget einrichten), b07-07/b07-08/b07-10/b07-11 (Filter, Formel,
  CSV- und Notion-Import – Dialoge ohne Seitenleiste), b09-03 (Konflikt, alte Kopfleiste – nachgestellter 409 nicht im
  Skript), b12-06 (Linkvorschau: der Server holt Vorschauen nur von öffentlichen Adressen, im Namensraum gibt es keine),
  b15-02 (Websuche: braucht eine SearXNG-Attrappe, alte Kopfleiste), b16-02 (Immich-Attrappe fehlt).
