# Handbuch-Bilder

Aufgenommen (Stand 3.11.2026) mit eigenen Aufnahme-Werkzeugen (`screens-handbuch.py`, `handbuch-stack.sh`,
`handbuch-demo.py`, `handbuch-fakes/`). **Diese Werkzeuge liegen nicht im Repository** (nicht übernommen, Neuaufbau
offen) – neue oder geänderte Bilder lassen sich derzeit nicht per Skript erzeugen. Hosts `saga.example` (Familie Sommer) und `verein.example` (Gartenverein). Desktop 1440×900
(Faktor 2), Handy 390×844 (Faktor 3). Nummern im Bild = Legende rechts. Abweichungen von der Gliederung stehen unten.

| Datei | Kapitel | Plattform | hell/dunkel | was zu sehen ist | Nummern-Legende |
|---|---|---|---|---|---|
| a01-01-uebersicht.png | A01 | Web | hell | Admin-Übersicht Gartenverein: aktive Benutzer, Admins, Gruppen, Anmeldung, Medien, zuletzt angemeldet | 1 = Menü des Admin-Bereichs; 2 = Kennzahlen |
| a01-02-admin-login-2fa.png | A01 | Web | hell | Anmeldung im Admin-Bereich (eigene Adresse admin.…); danach ggf. zweiter Faktor | – |
| a01-03-uebersicht-dunkel.png | A01 | Web | dunkel | Admin-Übersicht im dunklen Modus | – |
| a02-01-benutzerliste.png | A02 | Web | hell | Benutzer: admin, frieda (Admin), paul – Gruppen, letzte Anmeldung | 1 = Liste (Klick auf den Namen öffnet das Konto) |
| a02-02-benutzer-detail.png | A02 | Web | hell | Konto „paul“: Profil & Rechte, Gruppen, Passwort, 2FA, Sitzungen, löschen | 1 = Profil, Admin-Recht, Gruppen; 2 = Passwort setzen; 3 = 2FA zurücksetzen |
| a02-03-gruppen.png | A02 | Web | hell | Gruppen vorstand, mitglieder, s_wiki (Kennung s_ = Zugang zum Dienst) | – |
| a03-01-dienste.png | A03 | Web | hell | Dienste: Vereinswiki (nur s_wiki) und Vereinskalender (für alle) | – |
| a03-02-dienst-formular.png | A03 | Web | hell | Dienst „Vereinswiki“ bearbeiten: Kurzname wiki ↔ Gruppe s_wiki, Symbol, Kategorie, für alle | – |
| a04-01-2fa-verlangen.png | A04 | Web | hell | Allgemein → Zweiter Faktor: 2FA verlangen (niemand / Admins / alle), Passkeys | 1 = 2FA verlangen für |
| a04-02-ldap.png | A04 | Web | hell | LDAP / AD: Vorlage (FreeIPA, 389 DS, AD …), Benutzer, Gruppen, Testen – leer | – |
| a04-03-sso.png | A04 | Web | hell | SSO / OpenID Connect: Anbieter, Weiterleitungs-Adresse, Konten & Gruppen – leer | – |
| a04-04-hinweis-proxy.png | A04 | Web | hell | Hinweis TRUSTED_PROXIES (nachgestellt, Beispieladresse) – Abhilfe in der Anleitung Kap. 3 | 1 = Hinweis (erscheint 24 h nach dem letzten Auftreten) |
| a05-01-allgemein-seiten.png | A05 | Web | hell | Allgemein → Seiten: Vorlagengalerie, Editor (Tiptap/Editor.js/wählbar), Editor-Diagnose, live, Wetter | – |
| a05-02-allgemein-chats.png | A05 | Web | hell | Allgemein → Chats: erlauben, Aufbewahrung, Speicher, Videogröße, Linkvorschau, Moderne Chats | – |
| a05-03-api.png | A05 | Web | hell | Allgemein → API, Webhooks und MCP | – |
| a05-04-ki.png | A05 | Web | hell | KI-Assistent: Anbieter/Adresse (Attrappe), Modell, API-Schlüssel leer | – |
| a05-05-mitteilungen.png | A05 | Web | hell | Mitteilungen: Web Push, ntfy, UnifiedPush, Apple Push (Felder leer) | – |
| a05-06-diagnose.png | A05 | Web | hell | Allgemein → Diagnose: „Diagnose senden“ der Benutzer (clientlog) | – |
| a06-01-anrufe.png | A06 | Web | hell | Anrufe → 1:1-Anrufe: STUN/TURN (Geheimnis leer), nur über TURN, Klingeldauer | – |
| a06-02-knoten-liste.png | A06 | Web | hell | Gjallarhorn: eingebauter Knoten, Liste der Knoten (Port prüfen, Teilen, Leeren), Erweitern | – |
| a06-03-knoten-koppeln.png | A06 | Web | hell | Erweitern: weiteren Knoten koppeln – Name, Optionen, Kopplungs-Skript erzeugen | 1 = Name des neuen Knotens; 2 = Skript erzeugen (auf dem neuen Server ausführen) |
| a07-01-foederation.png | A07 | Web | hell | Föderation: diese Instanz (verein.example), Fingerprint, verbundene Instanz saga.example | – |
| a07-02-einladung.png | A07 | Web | hell | Einladungscode erzeugen (gilt einmal; Code hier erfunden) – die andere Instanz trägt ihn ein | – |
| a07-03-funktionen-je-instanz.png | A07 | Web | hell | Funktionen je Instanz: Chat, Anrufe, Adressbuch, gemeinsame Gruppen | – |
| a08-01-veroeffentlicht.png | A08 | Web | hell | Veröffentlicht (Familie): „Sommerfest-Einladung“ von Anna – zurückziehen | 1 = Zurückziehen |
| a08-02-meldungen.png | A08 | Web | hell | Meldungen aus Chats: Grund, Text der meldenden Person, Zeit und Absender – keine Inhalte | – |
| a09-01-log-ausschnitt.png | A09 | Terminal | hell | docker logs: Diagnose D-7K3FM finden (clientdiag) – erfundene Zeilen, Konto nur als Hash | – |
| a10-01-update-hinweis.png | A10 | Web | hell | Update-Hinweis im Admin-Bereich (nachgestellt: 2.5.1 verfügbar) | 1 = Hinweis mit „Was ist neu?“ und Befehl |
| b01-01-login.png | B01 | Web | hell | Anmeldeseite von saga.example mit Passwort und „Mit Passkey anmelden“ | 1 = Benutzername; 2 = Passwort; 3 = Mit Passkey anmelden; 4 = Sprache vor der Anmeldung |
| b01-02-login-2fa.png | B01 | Web | hell | Zweiter Schritt der Anmeldung: Code aus der Authenticator-App oder Passkey | 1 = Code aus der App (oder Wiederherstellungscode); 2 = stattdessen mit Passkey bestätigen |
| b01-03-sicherheit.png | B01 | Web | hell | Anmeldung & Sicherheit: Passkey „Annas MacBook“, Authenticator-App, Wiederherstellungscodes | 1 = Passkeys; 2 = Authenticator-App; 3 = Wiederherstellungscodes / Bestätigen |
| b01-04-profilbild-zuschneiden.png | B01 | Web | hell | Profilbild zuschneiden: Ausschnitt verschieben und zoomen | 1 = Ausschnitt (ziehen); 2 = Zoom |
| b01-05-sprache.png | B01 | Web | hell | Einstellungen → Konto → Sprache (Automatisch, Deutsch, English, Español, Русский, ไทย) | 1 = Sprache (Automatisch folgt dem Browser) |
| b01-06-login-handy.png | B01 | Handy | dunkel | Anmeldeseite von saga.example mit Passwort und „Mit Passkey anmelden“ | 1 = Benutzername; 2 = Passwort; 3 = Mit Passkey anmelden; 4 = Sprache vor der Anmeldung |
| b01-07-einstellungen-reiter.png | B01 | Web | dunkel | Einstellungen mit allen Reitern (Konto bis Hilfe) | 1 = Reiter: Konto · Mitteilungen · Lesezeichen · Kalender · Immich · Nextcloud · Mein Backup · Apps & Geräte · Seiten · API · Hilfe |
| b02-01-dashboard.png | B02 | Web | hell | Dashboard von Anna: Dienste, Lesezeichen, rechts Aufgaben und Termine | 1 = Suche (Dienste, Lesezeichen, Seiten); 2 = Kacheln der Dienste; 3 = Aufgaben und Termine; 4 = Leiste einklappen |
| b02-02-dashboard-dunkel.png | B02 | Web | dunkel | Dashboard dunkel | – |
| b02-03-dashboard-handy.png | B02 | Handy | hell | Dashboard auf dem Handy | – |
| b02-04-startseite-festlegen.png | B02 | Web | hell | Seitenmenü ⋯ einer Seite mit „Als Startseite festlegen“ | 1 = Als Startseite festlegen |
| b03-01-lesezeichen-dialog.png | B03 | Web | hell | Neues Lesezeichen: Adresse schule.example.org, Name und Symbol optional | 1 = Adresse (Pflicht) |
| b03-02-kacheln.png | B03 | Web | hell | Claras Dashboard mit eigenen Lesezeichen (Vertretungsplan, Mensa) | 1 = eigenes Lesezeichen; 2 = + Lesezeichen |
| b04-01-seitenbaum.png | B04 | Web | hell | Seitenbaum „Familie Sommer“ mit Unterseiten und „Mit mir geteilt“ | 1 = Seiten durchsuchen; 2 = + Neue Seite; 3 = Mit mir geteilt; 4 = Schnellsuche Strg/⌘+K; 5 = Archiv; 6 = Papierkorb (30 Tage) |
| b04-02-slash-menu.png | B04 | Web | hell | Slash-Menü in einer leeren Zeile – Weitertippen filtert, Esc schließt | – |
| b04-03-auswahl-leiste-farbe.png | B04 | Web | hell | Auswahl-Leiste über markiertem Text, Farbauswahl „A“ geöffnet | 1 = fett; 2 = kursiv; 3 = Text- und Hintergrundfarbe; 4 = Formel im Text |
| b04-04-block-menu.png | B04 | Web | dunkel | Block-Menü am Griff ⋮⋮: Umwandeln, Farbe, Duplizieren, Link zum Block, Kommentieren, Verschieben | 1 = Griff ⋮⋮ (klicken = Menü, ziehen = verschieben) |
| b04-05-spalten-ziehen.png | B04 | Web | hell | Zwei Spalten „Ferienwohnung Möwe“ / „Pension Seestern“, Trenner zum Ziehen | 1 = Spaltenbreite ziehen; 2 = Spalten: Anzahl und Ausrichtung |
| b04-06-tabelle-farben.png | B04 | Web | hell | Tabelle „Ausflüge“ mit Kopfzeile; Leiste für Zeile, Spalte und Zelle einfärben | 1 = Tabellenleiste: Zeile · Spalte · Zelle einfärben |
| b04-07-mathe-mermaid.png | B04 | Web | hell | Mathe-Block (KaTeX) und Mermaid-Diagramm mit Umschalter Vorschau/Geteilt/Code | 1 = Vorschau · Geteilt · Code |
| b04-08-erwaehnung.png | B04 | Web | hell | @-Menü: Personen (Ben, Clara, Oma Lena), Datum/Erinnerung, Seiten | – |
| b04-09-synced-block.png | B04 | Web | hell | Synchronisierter Block „Wichtige Nummern“ (Original im Familien-Wiki) | 1 = Original öffnen |
| b04-10-befehlspalette.png | B04 | Web | dunkel | Befehlspalette (Strg/⌘+K): Seiten und Befehle suchen | – |
| b04-11-editor-handy.png | B04 | Handy | hell | Seite „Urlaub Ostsee 2027“ im Handy-Browser | – |
| b04-12-versionen.png | B04 | Web | hell | Versionsverlauf von „Urlaub Ostsee 2027“ mit Autor und Zeit | – |
| b05-01-titelbild-symbol.png | B05 | Web | hell | Datenbank „Rezepte“ mit Titelbild und Symbol 🍲 | 1 = Titelbild ändern/verschieben; 2 = Symbol (Emoji) |
| b05-02-seitenmenue-gestalten.png | B05 | Web | hell | Seitenmenü ⋯: Volle Breite, Schrift (Standard/Serif/Mono), Titelbild | 1 = Volle Breite; 2 = Schrift; 3 = Titelbild hinzufügen |
| b05-03-gestaltet-dunkel.png | B05 | Web | dunkel | Claras „Lernplan Abi“: Titelbild, Serif-Schrift, volle Breite (dunkel) | – |
| b06-01-galerie.png | B06 | Web | hell | Vorlagen-Galerie: Filter Persönlich/Arbeit/Team und alle Vorlagen | – |
| b06-02-galerie-vorschau.png | B06 | Web | hell | Vorschau der Vorlage „Home-Dashboard“ mit Inhaltsliste | – |
| b06-03-wetter-ort.png | B06 | Web | hell | Beim Anlegen: Ort für das Wetter-Widget suchen („Lübeck“) | 1 = Ort suchen |
| b06-04-home-dashboard.png | B06 | Web | hell | Annas Startseite aus der Vorlage „Home-Dashboard“ (ganze Seite) | – |
| b06-05-home-dashboard-dunkel.png | B06 | Web | dunkel | Home-Dashboard dunkel | – |
| b06-06-widget-einrichten.png | B06 | Web | hell | Widget „Countdown“ einrichten: Datum, Richtung, Einheit, Vorschau | 1 = Einstellungen (jede Änderung sofort in der Vorschau) |
| b06-07-home-dashboard-handy.png | B06 | Handy | hell | Home-Dashboard im Handy-Browser | – |
| b07-01-tabelle-einkauf.png | B07 | Web | hell | Einkaufsliste als Tabelle mit Abteilung, Formel „Summe“ und Summenzeile | 1 = Ansichten (Reiter); 2 = Filter; 3 = Ansicht-Einstellungen; 4 = + Neu |
| b07-02-board-status.png | B07 | Web | hell | Hausputz als Board nach Status (Offen · Dabei · Erledigt) | – |
| b07-03-kalender-woche.png | B07 | Web | hell | Familienkalender in der Wochenansicht | – |
| b07-04-galerie-rezepte.png | B07 | Web | hell | Rezepte als Galerie: Bild, Tags, Gesamtzeit, Person | – |
| b07-05-zeitleiste-umzug.png | B07 | Web | hell | Familienkalender als Zeitleiste (Balken ziehbar) | – |
| b07-06-diagramm.png | B07 | Web | dunkel | Diagramm-Ansicht „Aufwand je Raum“ (Summe Minuten) – dunkel | – |
| b07-07-filter-relativ.png | B07 | Web | hell | Filter „Wann ist nächste Woche“ – relative Bedingungen (heute, morgen, nächste Woche …) | 1 = Filter (Anzahl aktiver Filter); 2 = Bedingung, auch relativ |
| b07-08-formel-editor.png | B07 | Web | hell | Formel-Editor der Spalte „Summe“: prop("menge") * prop("preis") | – |
| b07-09-peek.png | B07 | Web | hell | Rezept „Apfelkuchen nach Oma Lena“ im Peek rechts, mit Kommentar von Oma Lena | 1 = Peek rechts/mittig; 2 = Als Seite öffnen |
| b07-10-csv-import.png | B07 | Web | hell | Zeilen aus CSV hinzufügen: Vorschau und Zuordnung der Spalten (Semikolon, Excel Deutsch) | – |
| b07-11-notion-import.png | B07 | Web | hell | Importieren: Markdown/Obsidian, Notion-ZIP, CSV | – |
| b07-13-tabelle-handy.png | B07 | Handy | hell | Einkaufsliste im Handy-Browser | – |
| b08-01-aufgabenleiste.png | B08 | Web | hell | Aufgabenleiste auf dem Dashboard: fällig, überfällig, Priorität, Wiederholung | 1 = Aufgabe schnell hinzufügen; 2 = Aufgaben (Haken = erledigt); 3 = alle Aufgaben |
| b08-02-aufgabe-optionen.png | B08 | Handy | hell | Neue Aufgabe mit Priorität ⚑, Wiederholung (Menü offen) und Datum | 1 = Priorität; 2 = Wiederholung; 3 = Datum |
| b08-03-aufgabenquelle.png | B08 | Web | hell | Datenbank „Hausputz“ als Aufgabenquelle: Zuordnung erledigt/Datum/Priorität/Person | – |
| b08-06-rueckgaengig.png | B08 | Handy | dunkel | Aufgabe abgehakt – kurz „Rückgängig“ möglich | 1 = Rückgängig |
| b09-01-live-cursor.png | B09 | Web | hell | Anna und Ben gleichzeitig in „Sommerfest“: Anwesenheit oben, Bens Markierung live | 1 = Wer gerade hier ist |
| b09-02-versionen.png | B09 | Web | hell | Versionen: ältere Fassung von Ben ansehen und wiederherstellen | – |
| b09-03-konflikt.png | B09 | Web | hell | Konflikt ohne live: Ben hat denselben Absatz zuerst gespeichert | 1 = Neue Version laden (deine Änderung geht verloren); 2 = Meine Version behalten (Bens Fassung bleibt als Version) |
| b10-01-teilen-dialog.png | B10 | Web | hell | Teilen von „Urlaub Ostsee 2027“: Ben bearbeitet, Clara kommentiert | – |
| b10-02-kommentare.png | B10 | Web | hell | Kommentare zur Seite: Ben zur Restzahlung, Annas Antwort, Claras Frage | 1 = Kommentare ein-/ausblenden |
| b10-03-veroeffentlichen.png | B10 | Web | hell | Veröffentlichen: öffentliche Adresse, Unterseiten, Suchmaschinen, beenden | – |
| b10-04-oeffentliche-seite-handy.png | B10 | Handy | dunkel | Veröffentlichte „Sommerfest-Einladung“ ohne Anmeldung (dunkel) | – |
| b10-05-mit-mir-geteilt.png | B10 | Web | hell | Oma Lena: „Mit mir geteilt“ mit den Rezepten und dem Sommerfest | 1 = Mit mir geteilt |
| b11-01-teilen-foederiert.png | B11 | Web | hell | Frieda (verein.example) teilt „Parzellenplan 2027“ mit anna-sommer@saga.example | 1 = Handle name@server eingeben; 2 = schon geteilt: Anna auf saga.example (Bearbeiten) |
| b11-02-fremde-seite.png | B11 | Web | hell | Anna bearbeitet „Parzellenplan 2027“ von verein.example in ihrem Saga | 1 = Mit mir geteilt (auch von anderen Servern); 2 = Geteilt von Frieda Linde (frieda-linde@verein.example) · Du kannst bearbeiten |
| b12-01-chatliste.png | B12 | Web | hell | Chatliste: Familienchat „Sommers 🏡“ und Direktchat mit Ben, rechts leer | – |
| b12-02-familienchat.png | B12 | Web | hell | Familienchat: Claras Antwort mit Zitat, Reaktionen, Bens Foto, Linkvorschau | 1 = Antwort mit Zitat; 2 = Reaktion; 3 = Bild; 4 = Linkvorschau |
| b12-03-gruppeninfo.png | B12 | Web | hell | Gruppeninfo „Sommers 🏡“: Bild, Name, Beschreibung, Rechte, Mitglieder (Anna, Ben Admins) | 1 = Name und Bild (✎ ändern); 2 = Gruppenrechte; 3 = Medien, Dateien, Links |
| b12-04-gruppeninfo-handy.png | B12 | Handy | dunkel | Gruppeninfo auf dem Handy (dunkel), Ben ist Admin | – |
| b12-05-kontakte.png | B12 | Web | hell | Neue Unterhaltung: Kontakte (zuletzt zuerst) und Suche nach Personen | 1 = Kontakte |
| b12-06-linkvorschau.png | B12 | Web | hell | Linkvorschau: Titel, Beschreibung und Seitenname der Einladung | 1 = Vorschau (vom Server geholt, wenn der Admin sie erlaubt) |
| b12-07-angeheftet.png | B12 | Web | hell | Angeheftete Nachricht oben im Chat (bis zu 3; Klick springt hin) | 1 = angeheftete Nachricht |
| b12-08-geraet-bestaetigen.png | B12 | Web | hell | Neuer Browser: Chats mit anderem Gerät bestätigen oder Wiederherstellungsschlüssel eingeben | 1 = Mit anderem Gerät bestätigen (Code vergleichen); 2 = Wiederherstellungsschlüssel eingeben |
| b12-09-wiederherstellungsschluessel.png | B12 | Web | hell | Wiederherstellungsschlüssel eingeben (32 Zeichen A–Z, 2–7; hier erfunden) | 1 = Schlüssel (Leerzeichen egal) |
| b12-10-archiv.png | B12 | Web | hell | Rechtsklick in der Chatliste: anheften, stummschalten (Dauer), Mitteilungen, ausblenden, archivieren | 1 = Archivieren |
| b13-01-anruf-video.png | B13 | Web | dunkel | Videoanruf Anna ↔ Ben (dunkel, Kamera-Ersatzbild statt Gesicht) | 1 = Stummschalten; 2 = Kamera aus; 3 = Bildschirm teilen; 4 = Auflegen |
| b13-02-bildschirm-teilen.png | B13 | Web | hell | Ben teilt seinen Bildschirm (Fotos vom Urlaub) – Anna sieht ihn groß | – |
| b13-03-gruppenanruf.png | B13 | Web | dunkel | Gruppen-Videoanruf im Familienchat mit vier Personen (Mesh, dunkel) | – |
| b13-04-hierher-uebernehmen.png | B13 | Web | hell | Bens Laptop: „Anruf läuft auf …“ – Hierher übernehmen oder als Begleitgerät hinzufügen | 1 = Hierher übernehmen |
| b13-05-klingeln-handy.png | B13 | Handy | dunkel | Ben wird angerufen (Handy-Browser, dunkel): Ablehnen, nur Ton, mit Video | 1 = Ablehnen; 2 = Mit Video annehmen |
| b14-01-glocke.png | B14 | Web | hell | Glocke: geteilte Seiten, Kommentare, Erwähnungen, Aufgaben – „Alle gelesen“ | 1 = Glocke mit Zahl; 2 = Alle gelesen |
| b14-02-mitteilungen-einstellungen.png | B14 | Web | hell | Einstellungen → Mitteilungen: dieser Browser, Geräte, ntfy, Kategorien, Ruhezeiten | – |
| b14-03-glocke-handy.png | B14 | Handy | dunkel | Glocke auf dem Handy (dunkel) | – |
| b15-01-ki-seite.png | B15 | Web | hell | Frage Saga KI: Antwort zur Seite „Sommerfest“ mit Quelle [1] | 1 = Bereiche: Frag deine Seiten · Diese Seite; 2 = Verweis auf die Quelle; 3 = Quellen |
| b15-02-ki-websuche.png | B15 | Web | hell | KI mit Websuche: Wetter am Wochenende, Quellen aus dem Web (Attrappe) | 1 = Im Web suchen; 2 = Web-Quelle |
| b15-03-ki-chat.png | B15 | Web | hell | KI im Familienchat auf Zuruf (`@Saga KI`) – Antwort für alle sichtbar | – |
| b16-01-kalender-verbinden.png | B16 | Web | hell | Einstellungen → Kalender: Familienkalender verbunden, zweiten Kalender (CalDAV) hinzufügen | 1 = verbundener Kalender „Familie“; 2 = Adresse des CalDAV-Servers (Passwort: App-Passwort, hier leer) |
| b16-02-immich.png | B16 | Web | hell | Bild aus Immich: Album „Ostsee 2026“ (Attrappe), Foto wählen → wird in die Seite kopiert | 1 = Alben; 2 = gewähltes Album; 3 = Foto anklicken |
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
- b02-01: Aufgabenleiste zeigt „Fähre buchen“ u. a. statt „Milch kaufen“; Termin „Zahnarzt Clara“ aus dem Familienkalender
  (CalDAV-Attrappe hat für heute keinen „Elternabend“).
- b08-02: Optionen (Priorität/Wiederholung/Datum) gibt es nur im Aufgaben-Bereich (Handy/schmale Ansicht), nicht in der
  Schnell-Leiste rechts – Bild daher als Handy.
- b07-04 (Design-Fund, behoben in der Demo): Galerie-Karten zeigen Eigenschaften ohne Beschriftung; reine Zahlen (30/45/75)
  sind unverständlich. Demo blendet sie aus und zeigt „75 Min.“ als Formel-Text. Saga bietet keine Beschriftungen auf
  Galerie-Karten – Vorschlag: Option „Eigenschaftsnamen zeigen“.
- b01-02/b01-03: Passkeys brauchen https – Aufnahme in einer TLS-Phase (handbuch-stack.sh), Faktoren danach entfernt.
- **Veraltet gegenüber 2.5 (neu aufnehmen):** a06-02 zeigt noch den alten Hinweis „eigener Name per TLS-Durchreiche
  (SNI)“ beim eingebauten Knoten, „Alle Anrufe über Knoten“ ohne Haken (ab Werk an) und eine leere Knoten-Liste;
  a05-01 zeigt den Editor noch als „Neu (Tiptap, Test)“ statt „Standard (Tiptap)“.
  a09-01 zeigt `docker logs saga` und vereinfachte Zeilen; echt sind die Zeilen JSON (`clientdiag {"id":…}`), und
  der Befehl lautet `docker compose logs --no-log-prefix saga` (der Container heißt unter compose nicht `saga`).
- b09-03 (Konflikt): aufgenommen mit kurz abgeschaltetem „Gleichzeitig bearbeiten live“ (danach wieder an). Weil Saga 2.5
  ohne live die Änderung des anderen nachlädt bzw. blockweise zusammenführt, ist der Konflikt nachgestellt: Annas Speichern
  schickt den alten Stand ohne Zusammenführen (wie ein älterer Client) → echte Server-Antwort 409 und echte Leiste.
- App-Durchgang (Simulatoren/Emulatoren, nicht aus dem Browser): b11-03 (iOS), b12-11 (iOS), b12-12 (Android dunkel),
  b13-06 (iOS), b15-04 (iOS), b17-02 … b17-09 (iOS/iPad/Mac/Android/Linux), b19-03 (Android).
- B13 Anrufe: Demo läuft über http – Kamera/Mikrofon ersetzt das Skript im Browser durch ein gezeichnetes Ersatzbild
  (Initialen, keine Gesichter) und stillen Ton; WebRTC läuft echt zwischen den Browser-Kontexten. Gjallarhorn-Knoten
  (Go-Binary) nicht gestartet: b13-03 ist ein Mesh-Gruppenanruf mit 4 Personen (Grenze ohne Knoten), kein Knoten-Anruf.
- b13-04: „Anruf läuft auf …“ entsteht mit zwei Browsern derselben Person (Handy-Format und Laptop) – Gerätename
  „Chrome · Linux“ kommt vom Test-Browser.
- b14-02: Abschnitt „Dieser Browser“ zeigt „kann keine Mitteilungen empfangen (oder Saga läuft nicht über https)“ – Demo
  läuft über http; auf einem echten Server steht dort der Schalter.
- b15-02: Websuche über eine SearXNG-Attrappe (Treffer wetter.example.org / kueste.example.org); b15-03 KI im Chat zeigt
  die Antwort mit rohen ** (siehe Design-Fund).
- b16-02: Immich-Attrappe (handbuch-fakes/fakes.py, nicht im Repository, Album „Ostsee 2026“, gezeichnete Bilder) unter fotos.saga.example.
- b16-03 (Nextcloud-Datei) fehlt: Die Nextcloud-Anmeldung (Login Flow v2) speichert die Server-Adresse, die die Attrappe
  meldet; die Test-Attrappe tests/fakedav.py meldet 127.0.0.1:<port>, das stünde im Datei-Block. Bräuchte eine eigene
  Attrappe unter einem Anzeige-Host – zweiter Durchgang.
- b12-08/b12-09: neuer Browser von Clara ohne Chat-Schlüssel; Schlüssel in b12-09 ist erfunden.
- b18-01: nur das Formular (Name, Bereiche) – der erzeugte Token wird nicht gezeigt.
- Admin: a08-01/a08-02 auf saga.example (Familie, Admin anna), weil nur dort veröffentlicht/gemeldet wird; Meldung in a08-02
  ist für das Bild angelegt („Ausprobiert für das Handbuch“). a04-04 (TRUSTED_PROXIES) und a10-01 (Update 2.5.1) sind
  nachgestellt (gleiche Stelle/Klasse wie im Server; lokal nicht auslösbar). a06-03: „Erweitern“ erscheint nur mit
  Gjallarhorn an – für das Bild kurz eingeschaltet, danach wieder aus; Kopplungs-Skript nicht erzeugt. a07-02: Code
  ersetzt durch DEMO-EINLADUNG-…. a05-04: festgehaltene Adressen der lokalen Attrappe durch 192.0.2.10 ersetzt.
  a09-01: Terminal-Bild mit erfundenen Log-Zeilen (HTML), kein echtes Log.
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
