# Änderungen

Alle nennenswerten Änderungen an Saga. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/):
**Major** (2.0.0) = Umbau, der Handarbeit beim Update braucht · **Minor** (1.1.0) = neue Funktionen ·
**Patch** (1.0.1) = Fehlerbehebungen. Datenbank-Änderungen laufen beim Start automatisch, vorher legt Saga ein Backup an.

> **Entwicklung:** Neues landet zuerst auf dem Branch `testing` (Image `ghcr.io/yggdrasil-sh/saga:testing`, gleich
> `:rc`, wird bei jedem Push gebaut) und wird hier unter `[Unveröffentlicht]` notiert.
> **Version auf `testing`:** Direkt nach einem Release steht in `VERSION` auf `testing` schon die nächste geplante Version
> (z. B. `1.3.0`). Jeder Testing-Build ist ein Release Candidate `1.3.0-rc.<N>` (N = Commits seit dem letzten
> Release-Tag, `tools/rc-version.sh`; Commit als Build-Kennung) – man sieht sofort, welche Version gerade entsteht.
> Ein Release entsteht dadurch noch nicht.
> **Release:** auf `testing` `[Unveröffentlicht]` in `[x.y.z] – Datum` umbenennen (passend zu `VERSION`), dann `testing`
> nach `main` übernehmen und pushen. Die GitHub Action legt Tag, Images und Release an. Danach auf `testing` `VERSION`
> auf die nächste geplante Version setzen.

## [Unveröffentlicht]

### Schneller

- **Kompression:** JSON-, Text-, JS- und CSS-Antworten ab 1 KB kommen gzip-komprimiert, wenn Browser oder App es
  anbieten (alle tun das). Seitenbaum, Freigaben, Datenbank-Zeilen und Widget-Listen werden dadurch um 80–90 % kleiner
  (z. B. 5,2 MB → 0,6 MB bei 25.000 Notizen; App im Vordergrund 8 h: 2,5 GB → 0,28 GB). Nie komprimiert werden der
  Live-Kanal und der KI-Stream (`text/event-stream`), Bilder, Dateien, Zip-Exporte und Teilantworten (Video spulen).
  Einstellbar mit `GZIP_LEVEL` (Standard 5, `0` = aus) und `GZIP_MIN_BYTES` (Standard 1024).
- **Seitenbaum im Web:** Aufbau über einen Index statt wiederholter Suche durch alle Seiten; Auf- und Zuklappen baut nur
  den betroffenen Ast neu. Bei 25.000 Notizen: Baum sichtbar nach 1,3 statt 3,1 s, Ast aufklappen 0,15 statt 1 s,
  Seite öffnen 1,1 statt 2,7 s.
- **Rückverweise** („Verlinkt von“) werden erst geladen, wenn das Seitenende in Sicht kommt – kurze Seiten wie bisher
  sofort, lange erst beim Hinscrollen.
- **Weniger Neuladen des Seitenbaums:** Mehrere Aktionen kurz hintereinander teilen sich eine Abfrage (immer mit Daten
  von nach der Aktion); ein neuer Datenbank-Eintrag („+ Neu“, „+ Neu in …“ bei Relationen) wird direkt eingetragen statt
  den ganzen Baum neu zu laden.
- **Papierkorb:** eigener Index; die 30-Tage-Frist läuft beim Seitenbaum höchstens stündlich statt bei jedem Laden
  (die Papierkorb-Liste prüft weiterhin jedes Mal).
- **Seiten-Grenze:** `NOTE_MAX_COUNT` (5.000) zählt nur noch normale Seiten ohne Papierkorb. Datenbank-Einträge haben
  eine eigene Grenze `NOTE_ROW_MAX_COUNT` (Standard 50.000 bzw. 10 × `NOTE_MAX_COUNT`); alles zusammen inkl. Papierkorb
  höchstens doppelt so viel wie beide Grenzen. Bisher war ein Konto mit größeren Datenbanken schon nach 5.000 Einträgen
  „voll“. Keine Grenze ist strenger als vorher.
- **Live-Ereignis `notes.tree`** (für Apps, Vertrag in `docs/APP-API-LIVE.md`): meldet Änderungen am Seitenbaum (neu,
  umbenannt/Symbol/Tags/Favorit/Sperre, verschoben, Papierkorb, wiederhergestellt, archiviert, endgültig gelöscht,
  Freigabe geändert) – nur IDs, an alle Geräte des Eigentümers und an Personen mit Freigabe, gesammelt höchstens eins je
  2 s. Apps können damit bei verbundenem Live-Kanal seltener abgleichen (5 min statt 60 s). Die Weboberfläche lädt den
  Baum in anderen Tabs und auf anderen Geräten damit sofort nach.
  - **Ausschluss je Tab:** Nur der Tab, der eine Änderung ausgelöst hat, bekommt sie nicht zurück – andere Tabs
    derselben Browser-Sitzung schon (Kennung je Tab: Kopf `X-Saga-Tab`, beim Live-Kanal `?tab=`). Das gilt auch für
    `note.changed`: eine Seite, die in zwei Tabs offen ist, bleibt in beiden aktuell. Apps: je Gerät wie bisher.
  - **Verschieben** aus einem geteilten Teilbaum heraus oder hinein (auch per `PATCH parent_id` und beim
    Wiederherstellen auf die oberste Ebene): Empfänger der alten **und** der neuen Kette bekommen `notes.tree` und
    `shared.changed` – bisher nur die neue Kette. Papierkorb/Archiv: `shared.changed` erreicht jetzt auch Freigaben auf
    Seiten oberhalb.
  - **Gruppenwechsel** (Admin-Bereich, LDAP/OIDC-Abgleich bei der Anmeldung, Gruppe gelöscht): betroffene Konten
    bekommen `shared.changed` und `notes.tree` (ganzer Baum), wenn eine der Gruppen Ziel einer Freigabe ist; entprellt.
  - **Nachholen mit `Last-Event-ID`:** `notes.tree` wird gegen die aktuellen Rechte geprüft; ist eine fremde Seite nicht
    mehr sichtbar, kommt das Ereignis als „ganzen Baum laden“ ohne IDs.
- **ETag bei gzip:** gepackte Antworten tragen ein schwaches ETag (`W/"…"`); `If-None-Match` vergleicht schwach, 304
  funktioniert für gepackte und ungepackte Fassung gleich.

### Lastmessung (nur Werkzeuge, keine Änderung an Saga selbst)

- `tools/perf/`: wiederholbare Lastmessung für Test-Stacks. `gen.py` erzeugt realistische Testdaten in drei Stufen
  (S: 500 Seiten · M: 5.000 Seiten + 20.000 Datenbank-Einträge · L: 10.000 Seiten + 50.000 Einträge, eine Datenbank
  mit 10.000 Einträgen, Baum bis Tiefe 6, Bilder, Kommentare, Freigaben an ein zweites Konto, Chat mit 5.000
  Nachrichten) direkt in Postgres. `bench.py` misst Antwortzeit (p50/p95) und Größe der wichtigsten Endpunkte, die
  Netzwerk-Abläufe der Apps, parallele Last, den Live-Kanal sowie CPU/RAM der Container; `explain.py` zeigt die Pläne
  der teuersten Abfragen; `web.py` misst die Weboberfläche mit Playwright (headless).
- `tools/perf/run.sh` führt alles je Stufe aus und prüft Grenzwerte aus `tools/perf/limits.json` (Exit 1 bei
  Überschreitung), z. B. `SAGA_SCRIPT=perf/run.sh tools/test-stack.sh "S M"`. Nur für Test-Stacks: legt die Konten
  `perf-a`/`perf-b` neu an und setzt `NOTE_MAX_COUNT=200000` in der `.env` des Test-Stacks.
- `bench.py` misst als Größe die übertragenen Bytes (mit gzip komprimiert) und entpackt Antworten selbst.

### Testing-Builds als Release Candidates

- Testing-Images heißen jetzt `2.2.0-rc.7` statt `2.2.0-testing.<commit>` (7 = Commits seit dem letzten Release-Tag;
  ohne Tags im Build `2.2.0-rc+<commit>`). Der Commit steht als Build-Kennung daneben: im Admin-Bereich
  „2.2.0-rc.7 (1cc210f) · 🧪 Release Candidate“, bei `python run.py version` und in `/api/version` als neues Feld
  `build` (dazu `channel`: `stable`, `rc` oder `testing`). `version` bleibt reines SemVer ohne Build-Kennung.
- Ein Release Candidate ist immer neuer als die stabile Version davor: Der Update-Hinweis vergleicht nach SemVer
  (2.1.0 < 2.2.0-rc.7 < 2.2.0) – kein Hinweis auf 2.1.0, aber auf 2.2.0, sobald es erschienen ist. Steht `VERSION`
  noch auf der gerade veröffentlichten Version, zählt der RC zur nächsten Patch-Version (z. B. `2.1.1-rc.0`).
- Docker: zusätzlich das Tag `rc` neben `testing` (das bleibt, bestehende Installationen laufen unverändert weiter).
  Release-Builds (main) unverändert, z. B. `2.2.0`. Apps: nichts zu tun, `MIN_APP` bleibt.

### Behoben

- Chat → „Neue Unterhaltung“ (auch „+ Gruppe mit mehreren Personen“ und „Person hinzufügen“): Wer `name@server`,
  `@name@server` oder eine E-Mail-Adresse eingab, sah nur „Keine Treffer.“. Jetzt erklärt ein Hinweis, dass es Chats mit
  Personen anderer Saga-Server noch nicht gibt, Seiten aber mit `name@server` (öffentlicher Name) geteilt werden
  können, und dass Personen hier über Namen oder Benutzernamen gefunden werden – mit Link zur Anleitung (Föderation).
- Teilen-Dialog: Bei `name@server` ohne Treffer steht statt „Niemand gefunden.“ der Grund – die Person gibt es dort
  nicht bzw. sie hat keinen öffentlichen Namen, der Server ist nicht verbunden, er ist gerade nicht erreichbar oder
  es ist dieser Server selbst; bei E-Mail-artigen Eingaben der Hinweis, dass der öffentliche Name gemeint ist.
  `GET /api/share/targets` liefert dafür das neue, optionale Feld `remote_hint` (nur ohne Treffer). Apps: nichts zu
  tun, `MIN_APP` bleibt.

## [2.1.0] – 2026-10-18

Saga 2.1 macht die Anmeldung sicherer und den Chat persönlicher: Passkeys und Zwei-Faktor-Anmeldung, Profilbilder,
Audio- und Videoanrufe im Direkt-Chat, Bilder aus Immich, Chats archivieren und löschen. Die KI bekommt einen Namen,
ein Gesicht und einen Tonfall, hilft auf Zuruf im Chat und kann – wenn der Admin es erlaubt – im Web suchen. Die
Oberfläche spricht jetzt fünf Sprachen (neu: Русский und ไทย), wählbar schon auf der Anmeldeseite.
Die Schnittstelle wurde nur erweitert – Apps 2.0 und älter laufen weiter (`MIN_APP` bleibt). Die passenden Apps sind
die [Saga-Apps 2.1](https://github.com/yggdrasil-sh/saga-apps/releases).

### Neu
- **Passkeys und Zwei-Faktor-Anmeldung** (Einstellungen → Konto → „Anmeldung & Sicherheit“, lokale und LDAP-Konten):
  Anmeldung **ohne Passwort** per Passkey (WebAuthn/FIDO2, Autofill beim Benutzernamen) oder Passkey als zweiter
  Faktor; **Authenticator-App** (TOTP); **10 Wiederherstellungscodes**. Nach dem Passwort ein kurzer Zwischenschritt
  (5 Minuten, 5 Fehlversuche je 15 Minuten), erst dann gibt es eine Sitzung. Änderungen an den Faktoren nur mit
  frischer Bestätigung. Der Admin-Bereich verlangt bei Konten mit 2FA ebenfalls den zweiten Faktor; optional auch bei
  SSO (`amr`/`acr` des Anbieters). Die Apps zeigen den zweiten Schritt in ihrer Anmeldeseite.
- **2FA-Richtlinie** (Admin → Allgemein): „2FA verlangen für niemanden / Admins / alle“ (Standard: niemanden).
  Betroffene ohne Faktor werden zur Einrichtung geführt, nicht ausgesperrt – auch im Admin-Bereich und bei neuen
  App-Anmeldungen; bestehende Geräte und Sitzungen bleiben gültig. „2FA zurücksetzen“ je Konto.
- **Profilbilder** überall statt Initialen (Kopfzeile, Kommentare, Anwesenheit, Benachrichtigungen, Teilen, Chat,
  Versionsverlauf, Föderation): eigenes Bild (PNG, JPEG, GIF, WebP, HEIC; im Browser zuschneiden, bis 5 MB, der Server
  kodiert neu und entfernt Metadaten), **Gravatar** (vom Server abgeholt, Browser und Apps fragen nie bei Gravatar),
  optional Bild aus OIDC (`picture`) oder LDAP (`jpegPhoto`/`thumbnailPhoto`). Bilder nur für angemeldete Personen;
  auf veröffentlichten Seiten nur mit Erlaubnis der Person.
- **Audio- und Videoanrufe im Direkt-Chat** (Web): 📞/🎥 im Chat-Kopf; Stummschalten, Kamera an/aus, Bildschirm teilen,
  Ton-Anzeige, kleines Fenster. WebRTC direkt zwischen den Geräten, **ohne zusätzlichen Port** (Signalisierung über
  den Live-Kanal). **Ende-zu-Ende geprüft:** jede Signal-Nachricht ist signiert und für das Zielgerät verschlüsselt,
  der Server sieht weder Verbindungsdaten noch IP-Adressen. Klingeln auf allen Geräten und Tabs, Push mit hoher
  Priorität, verpasste Anrufe im Chat. Admin → Allgemein → „Anrufe“: an/aus, STUN, optional TURN, Klingeldauer.
- **Chats archivieren und löschen** (Chat-Menü „⋯“ oder Rechtsklick/langes Antippen in der Chatliste):
  **Archivieren** (jede Chat-Art, nur für mich) schiebt den Chat in den aufklappbaren Bereich „Archiviert (n)“ – er
  zählt nicht mehr als ungelesen und kommt mit der nächsten Nachricht oder einem Anruf von selbst zurück (außer er ist
  stummgeschaltet). **Chat löschen …** mit deutlicher Warnung (nicht rückgängig): **Nur für mich** – der Server löscht
  die für mich versiegelten Schlüssel der bisherigen Nachrichten und liefert mir den alten Verlauf nicht mehr aus, die
  anderen behalten alles; freie Gruppen kann man dabei auch verlassen. **Für alle** – Direkt-Chat (beide) und freie
  Gruppe (Chat-Admins): Chat samt Nachrichten, Anhängen, Anrufen und Benachrichtigungen weg, die anderen sehen „Name hat
  den Chat gelöscht.“; Gruppen-Chat (Saga-Admins) und Seiten-Chat (Eigentümer der Seite): nur der Verlauf.
- **Immich im Chat** (Web): „Aus Immich …“ beim Anhängen, mit derselben Auswahl wie auf Seiten; das Bild wird wie jeder
  Anhang Ende-zu-Ende verschlüsselt.
- **Schnelle Chat-Bilder:** Beim Senden entsteht ein verschlüsseltes Vorschaubild. Der Verlauf lädt nur sichtbare
  Vorschaubilder in einen Platzhalter der richtigen Größe (nichts springt), das Original erst beim Antippen.
  Ältere Nachrichten bekommen ihr Vorschaubild im Browser.
- **Bilder in Seiten kopieren, ausschneiden und einfügen:** Bild anklicken, `Strg`/`⌘`+`C`/`X`/`V` – in derselben
  Seite, einer anderen oder einem anderen Tab (für andere Programme auch als PNG). Auf Seiten anderer Konten entsteht
  eine eigene Kopie.
- **KI-Persona** (Admin → KI-Assistent → Persona): **Name** (leer = „Saga KI“ / „Saga AI“ / „Saga IA“), **Profilbild**
  (geprüft und neu kodiert wie Profilbilder; ohne Bild ein neutrales ✦) und optional **Persönlichkeit und Tonfall**
  (höchstens 500 Zeichen, als nachrangiger Stil-Zusatz – Regeln und Formate bleiben). Web und Apps zeigen Name und
  Bild überall, wo die KI vorkommt („Frage Marin“, „Marin denkt nach …“), und übernehmen Änderungen sofort.
- **KI im Chat – nur auf Zuruf** (Standard: aus): Erlaubt der Admin „KI in Chats erlauben“, kann die KI in einzelnen
  Chats zugelassen werden (freie Gruppe – Chat-Admins, Direkt-Chat – beide, Gruppen-Chat – Saga-Admins, Seiten-Chat –
  Eigentümer); alle Mitglieder sehen einen Hinweis. Die KI liest **nur Nachrichten mit @Name** und bis zu 12 Nachrichten
  davor, die das Gerät des Fragenden gezielt schickt – alles andere bleibt Ende-zu-Ende-verschlüsselt; Seiten, Aufgaben
  und Kalender sieht sie dort nicht. Die Antwort verschlüsselt das Gerät des Fragenden für alle; der Server speichert
  nur eine Prüfsumme als Beleg, damit die anderen Geräte die Antwort als KI-Antwort erkennen („gefragt von …“). Der
  Beleg verschwindet mit der Nachricht (Löschen, Ablauf, Chat löschen), bei Kontolöschung verliert er den
  Personenbezug. Kontingent und Gruppen des Assistenten gelten, dazu 6 Fragen je Minute und Person, 20 je 10 Minuten
  und Chat.
- **Websuche für die KI** (Standard: aus; Admin → KI-Assistent → „Websuche erlauben“, nur mit eingerichtetem SearXNG,
  `SEARXNG_URL`): Bei Fragen nach Aktuellem – Wetter, Nachrichten, Öffnungszeiten – sucht die KI im Assistenten und im
  Chat selbst im Web und nennt die Quellen mit `[n]` und Link („Marin sucht im Web …“). **Modellunabhängig**, auch mit
  kleinen Ollama-Modellen ohne Tool-Calls: ein kurzer Vorab-Aufruf entscheidet, ob gesucht wird (höchstens zwei
  Suchbegriffe), Saga fragt SearXNG und liest den Text der besten zwei Treffer – nur öffentliche Adressen, Port 80/443,
  Text/HTML, mit Größen- und Zeitlimit (`AI_WEB_FETCH=0`: nur Ausschnitte). Treffer gehen als Daten markiert in den
  Prompt, Anweisungen darin zählen nicht. Im Assistenten je Frage abschaltbar („Im Web suchen“); Zusammenfassen,
  Verbessern, Übersetzen und Aufgaben vorschlagen suchen nie. Jede Websuche zählt als weitere Anfrage im Kontingent,
  höchstens 10 je Person in 10 Minuten (`AI_WEB_RATE`). Ist SearXNG nicht erreichbar, kommt die Antwort trotzdem.
- **Sprache wählbar, schon vor der Anmeldung:** Umschalter 🌐 auf der Anmeldeseite (Automatisch (Browser) · Deutsch ·
  English · Español · Русский · ไทย) – für den fremden Rechner, der nicht die eigene Sprache spricht. Gilt auch für
  2FA-Schritt, Registrierung, „Anmeldung & Sicherheit“, App-Anmeldung/-Kopplung und die öffentlichen Fehlerseiten (dort
  als Links ohne Skript). Gemerkt wird nur die Sprache im Cookie `saga_lang` (ein Jahr, HttpOnly, kein Tracking).
  Öffnen die Apps Anmeldung oder Kopplung mit `&lang=…`, gilt deren Sprache für diese Browser-Sitzung. Reihenfolge:
  Konto-Sprache > Umschalter > Sprache der App > Standardsprache des Servers > Browser > Englisch. Umschalter auch im
  Admin-Bereich (Kopfzeile und Admin-Anmeldung) und im Benutzermenü („Sprache“).
- **Russisch und Thai:** zwei neue Oberflächensprachen für Web, Admin-Bereich, öffentliche Seiten, Fehlermeldungen der
  API und Mitteilungstexte – im Umschalter, unter Einstellungen → Konto → Sprache, als Standardsprache im Admin-Bereich,
  per `?lang=ru`/`?lang=th` und automatisch über die Browsersprache. Datum und Zahlen passend (Thai mit gregorianischem
  Kalender), der @-Datumsvorschlag versteht „завтра“ oder „พรุ่งนี้“, die KI antwortet auch auf Russisch und Thai,
  „Übersetzen nach“ bietet beide an. Die Übersetzungen sind **maschinell erstellt** – Gegenlesen durch Muttersprachler
  ist willkommen.
- **KI-Assistent findet mehr und das Richtige:** Wortstämme, Gewichtung seltener Wörter (BM25), zusammengesetzte
  Wörter („Zahnarzttermin“ findet „Zahnarzt … Termin“), Eigenschaften von Datenbank-Zeilen („Welche Projekte sind in
  Arbeit?“), Zeitangaben wie „letzte Woche“. In 22 Testfragen: richtige Quelle 82 % → 95 %. Antworten mit Absätzen,
  Listen und Quellen; ohne passende Seite antwortet Saga selbst, ohne das Kontingent zu belasten.
- **Lokale Modelle robuster:** Ollama bekommt einen passenden Kontext (`num_ctx`), Denkphasen (`<think>` usw.) werden
  nie angezeigt („Das Modell denkt nach …“), das Zeitlimit gilt zwischen zwei Stücken statt für die ganze Antwort.
- **API für die Apps** (alles additiv): Feld `avatar` an allen Personen, `/api/account/avatar`, Live-Ereignis
  `avatar.changed`; Anrufe unter `/api/calls…` mit Live-Ereignissen `call.*` und Push-Abo `apns-voip`; Anmeldung mit
  zweitem Faktor über `/app/login`; „Neu beginnen“ im Chat auch mit Code oder Passkey; KI-Persona
  (`GET /api/ai/persona`, `/api/ai/avatar`, `ai.persona.changed`), KI im Chat (`POST /api/chats/{cid}/ai`), Websuche
  (`status`-Ereignis, `web` in `/api/ai/status`); Chats archivieren/löschen (`PATCH`/`DELETE /api/chats/{cid}`,
  `chat.cleared`/`chat.removed`). Verträge: `docs/APP-API-KI.md`, `docs/APP-API-CHAT.md`.
- **Notfall per Kommandozeile:** `docker compose exec saga python run.py 2fa-zuruecksetzen <name>` (nur den Faktor
  entfernen, Passwort bleibt) und `… run.py 2fa-richtlinie none|admins|all`; `admin-passwort` entfernt den zweiten
  Faktor wie bisher mit.

### Verbessert
- **KI-Assistent mit kleinen lokalen Modellen** (gemessen mit Ollama + `llama3:8b`): Die Antwortsprache wird aus der
  Frage erkannt und am Ende der Anfrage noch einmal genannt (vorher antwortete das Modell auf deutsche Fragen öfter
  englisch); die Seiten-Hilfen wiederholen ihren Auftrag nach dem Seitentext. Einleitungen wie „Here is the improved
  text:“ landen beim Verbessern/Übersetzen nicht mehr im ersetzten Block. Ollama bekommt ein gleichbleibendes
  `num_ctx` (aus „Seiteninhalt je Anfrage“) – vorher lud Ollama das Modell bei jedem Wechsel neu (5–6 s Wartezeit).
  „Nichts gefunden“ erscheint in der Sprache der Frage.
- **„Aufgaben vorschlagen“** liefert nur noch konkrete nächste Schritte („Weiterlesen ab Seite 12“) statt
  Feststellungen („Das Buch hat 258 Seiten“). Gibt es nichts zu tun, kommt „Auf dieser Seite gibt es nichts zu tun.“
- **Datenbank-Zeilen für die KI:** Eigenschaften gehen mit Spaltennamen an das Modell, Rechen-Spalten mit Wert und
  Herkunft („Fortschritt: 4 % (Gelesen 11 / Seiten 258)“). „Zusammenfassen“ einer Zeile ohne nennenswerten Text
  antwortet ehrlich „Kaum Inhalt – nur Eigenschaften: …“ (ohne Modell).
- **Markierung nur Bild/leer:** Verbessern/Übersetzen nimmt dann den Text der ganzen Seite und sagt das.
- **Sprache der Seiten-Hilfen:** Zusammenfassen und Aufgaben in der Sprache der Oberfläche, Verbessern behält die
  Sprache des Textes, Übersetzen die Zielsprache.
- Alle KI-Funktionen kennen jetzt auch die **Uhrzeit** (Zeitzone des Servers), damit „heute“, „morgen“ und „heute
  Abend“ stimmen.
- **Konto-Sprache leichter zu finden:** Einstellungen → Konto zeigt „🌐 Sprache“ als ersten Block, „Automatisch“ nennt
  die gerade aktive Sprache.

### Geändert
- Anwesenheit erscheint sofort beim Öffnen einer Seite (vorher bis zu 20 s) und flackert nicht mehr.
- Chat-Anhänge werden im Browser zwischengespeichert (verschlüsselt, höchstens 200 MB, geleert beim Abmelden).
  Bis zu 20 Dateien je Nachricht, 60 Uploads je Minute.
- Admin → KI: Verbindungstest prüft das Modell gegen die ganze Liste und bietet sie zur Auswahl an.
- Übersetzungen können Pluralformen enthalten (`{n:eins|wenige|viele}`, CLDR-Regeln); `tools/i18n.py check` prüft sie,
  `tools/i18n.py merge <lang> <datei.json>` übernimmt Übersetzungen.
- Neue Tabellen `chat_pref`, `chat_ai`, `chat_ai_reply` entstehen beim Start (`CREATE TABLE IF NOT EXISTS`, kein
  weiterer Schema-Stand).
- Neue Abhängigkeiten: `webauthn`, `pillow-heif`. Neue Testreihen `tests/chat_loeschen.py` und `tests/ki_web.py`.

### Behoben
- **Sicherheit:** Eingefügte Blöcke aus der Zwischenablage werden im Browser geprüft (vorher hätte eine präparierte
  Zwischenablage HTML in den Editor bringen können; gespeichert wurde es schon bereinigt).
- **KI mit neueren Claude-Modellen über die OpenAI-kompatible Schnittstelle** (`https://api.anthropic.com/v1`): Saga
  schickt `temperature` nicht mehr, wenn das Modell den Parameter ablehnt (Fehler 400 „temperature is deprecated“), und
  „Verbindung testen“ listet dort auch die angebotenen Modelle.
- **Fehlerrunden aus zwei Bughunts:** Uploads ohne Längenangabe über der Grenze → 413 statt 500; Rückruf während es
  noch klingelt; Anruffenster auf dem Handy; ersetzter Gruppenname nimmt seine Anhänge mit; Esc im Peek schließt nur
  den Dialog; Untermenüs bleiben im Peek sichtbar; veröffentlichte Seiten ohne „@name“ oben; Chat-Platzhalter auf dem
  Handy; Kommentar-Auszug bei Tabellen und Checklisten; Konto wechseln in den Apps; KI-Fehltreffer durch falsche
  Wortzerlegung.
- Tests: „Veröffentlichte Seite“ in der Avatar-Reihe lief nur beim ersten Mal auf einem Stack durch (fester Testname
  eines gelöschten Kontos ist gewollt gesperrt); der Test nimmt jetzt je Lauf einen neuen Namen.

### Hinweise zum Update
- **Update:** Mit `SAGA_VERSION=2` in der `.env` reicht `docker compose pull && docker compose up -d`.
- **Kein Rückweg auf 2.0.x:** Saga 2.1 hebt die Datenbank auf Schema-Stand 6 und legt vorher automatisch ein Backup an
  (`…-vor-schema-6.dump`). Ältere Versionen starten auf dieser Datenbank nicht mehr; zurück zu 2.0 nur über das Backup
  (Anleitung, Abschnitt 11 „Updates“).
- **Gravatar ist standardmäßig an:** Für Personen mit E-Mail-Adresse fragt der Server bei Gravatar nach einem Bild
  (gehashte E-Mail-Adresse, nicht der Browser). Abschaltbar für alle (Admin → Allgemein → Profilbilder) oder je Person
  (Einstellungen → Konto).
- **Anrufe** nutzen standardmäßig den STUN-Server `stun:stun.nextcloud.com:443` (Datenschutzhinweis im Admin, leer =
  keiner). **TURN** ist optional; ohne TURN klappen Anrufe nicht in allen Netzen. Anleitung für TURN über Port 443
  per Reverse Proxy (nginx, HAProxy, Traefik).
- **KI im Chat und Websuche der KI sind standardmäßig aus.** Beides schaltet der Admin unter KI-Assistent ein; die
  Websuche braucht eine eingerichtete SearXNG-Instanz (`SEARXNG_URL`, Anleitung Abschnitt 10). Datenschutz: Bei
  „KI im Chat“ gehen die Nachricht mit @Name und bis zu 12 davor im Klartext an den KI-Endpunkt, bei der Websuche die
  Suchbegriffe über SearXNG an Suchmaschinen.
- **Passkeys** brauchen in `BASE_URL` einen Hostnamen mit https (oder `localhost`), keine IP-Adresse.
- **2FA-Richtlinie** steht auf „niemanden“ – es ändert sich nichts, bis der Admin sie einschaltet. Bei Problemen:
  `docker compose exec saga python run.py 2fa-zuruecksetzen <name>` bzw. `… run.py 2fa-richtlinie none`.

### Bekannte Grenzen
- Anrufe nur zu zweit im Direkt-Chat, noch keine Gruppenanrufe.
- Ohne TURN-Server kommen Anrufe nicht in allen Netzen zustande (z. B. hinter strengen Firmen- oder Mobilfunk-NATs).
- Passkeys funktionieren nur unter einem Hostnamen (nicht unter einer IP-Adresse in `BASE_URL`).
- Russisch und Thai sind maschinell übersetzt (Web, Admin-Bereich, Mitteilungen und Apps 2.1); die Anleitung gibt
  es nur auf Deutsch, Englisch und Spanisch.
- Apps vor 2.1 zeigen archivierte Chats weiter in der normalen Liste und KI-Antworten im Chat als Nachricht der fragenden Person.

## [2.0.0] – 2026-10-15

Saga 2.0 macht aus dem persönlichen Notizbuch einen gemeinsamen Ort: Seiten teilen und kommentieren, live sehen,
was andere tun, verschlüsselt chatten, Mitteilungen aufs Handy, ein optionaler KI-Assistent und – neu – mit
anderen Saga-Servern teilen. Alle neuen Funktionen sind optional; die Schnittstelle wurde nur erweitert, die Apps
1.x laufen weiter (`MIN_APP` bleibt 1.0.0). Die passenden Apps sind die [Saga-Apps 2.0](https://github.com/yggdrasil-sh/saga-apps/releases).

### Neu
- **Teilen und Kommentare:** Seiten und Datenbanken mit Personen oder Gruppen (lokal, LDAP, SSO) teilen – Rollen
  **Lesen**, **Kommentieren**, **Bearbeiten**; Unterseiten und Einträge erben die Freigabe. Bereich „Mit mir geteilt“
  in der Seitenleiste, Kopfzeile „Geteilt von … · Du kannst …“, „Zuletzt geändert von …“, Versionsverlauf mit Autor,
  Konflikt ohne Datenverlust („Meine Version behalten“, die überschriebene Fassung bleibt als Version).
  Kommentare dezent am Block, Unterhaltung in einer Seitenleiste (Handy: Bottom-Sheet), „✓ Erledigt“, ungelesen-Punkte.
  Admin → Allgemein: „Teilen erlauben“ (Standard an), „Nur innerhalb gemeinsamer Gruppen“
- **Live-Kanal und Benachrichtigungen:** Glocke mit ungelesenen Benachrichtigungen (geteilt, Rolle geändert,
  Kommentar, Antwort, Veröffentlichung zurückgezogen, neue Chat-Nachricht – gebündelt, je Art abschaltbar).
  Seiten aktualisieren sich ohne Neuladen; „Name ist auch hier“ / „bearbeitet gerade“ im Seitenkopf.
  Technik: Server-Sent Events über den normalen Port (`GET /api/events`), kein WebSocket, kein zusätzlicher Port;
  ohne Kanal fragt die Seite alle 60 s nach
- **Chats mit Ende-zu-Ende-Verschlüsselung:** neue Seite `/chats` – Direkt-, Gruppen-, Seiten-Chats und freie Gruppen,
  Antworten, verschlüsselte Bilder/Dateien, @-Seitenverweise, „tippt …“, Stummschalten, Aufbewahrungsfrist,
  lokale Suche. Wiederherstellungsschlüssel, neues Gerät per 20-stelligem Code bestätigen, Sicherheitsnummer mit
  Warnung bei Wechsel, Geräteliste. Nur libsodium-Primitiven (Format v1, Testvektoren in `tests/chat_vectors.json`);
  der Server sieht nur verschlüsselte Daten. Admin → Allgemein: „Chats erlauben“ (Standard an), Aufbewahrung;
  Speicher für Anhänge `CHAT_QUOTA_MB` (Standard 1024)
- **Mitteilungen aufs Handy/Desktop:** Einstellungen → „Mitteilungen“ – **Web Push** (auch iPhone/iPad als
  Home-Bildschirm-App, verschlüsselt nach RFC 8291), **ntfy**, **UnifiedPush** und **Apple Push (APNs)** für die
  iOS-App. Nie mit Inhalten (nur der Satz aus der Glocke + Link), Zugriff wird beim Zustellen erneut geprüft;
  Bereiche, Ruhezeiten, „Namen zeigen“, Test je Gerät. Zustellung über eine Warteschlange mit Wiederholungen
- **KI-Assistent (optional, Standard aus):** ✦ Assistent auf Seiten – **Frag deine Seiten** (Antwort mit Quellen,
  nur aus Seiten, die die Person sehen darf) und **Diese Seite** (zusammenfassen, Aufgaben vorschlagen, Text
  verbessern oder übersetzen). Modell nach Wahl: OpenAI-kompatibel (auch Ollama, LM Studio, vLLM) oder Ollama nativ.
  Ergebnisse sind nur Vorschläge, der Assistent hat keine Schreibrechte; Chats liest er nie
- **Föderation (optional, Standard aus):** Saga-Server miteinander verbinden (Einladungscode, beide Admins
  bestätigen per Fingerprint) und Seiten an `name@server` oder `gruppe@server` teilen. **Die Daten bleiben beim
  Eigentümer:** Lesen, Bearbeiten und Kommentieren laufen über den eigenen Server, jede Anfrage und Antwort ist
  signiert (Ed25519, HTTP Message Signatures RFC 9421, Content-Digest RFC 9530, Schutz gegen Wiederholung).
  Anmeldenamen verlassen den Server nie. Noch nicht über Servergrenzen: Datenbanken, Hochladen, Unterseiten,
  Versionen, Export, Live/Anwesenheit, Chat und KI-Assistent

### Geändert
- Zentrale Zugriffsprüfung für alle Seiten-, Datenbank-, Datei-, Such- und Export-Endpunkte: ohne Rolle wie bisher
  404, mit zu kleiner Rolle 403; Titel nicht geteilter Seiten erreichen niemanden sonst (auch nicht über Suche,
  Export oder Verweise)
- „Neu beginnen“ im Chat verlangt das Passwort oder eine frische Anmeldung (SSO: „Neu anmelden“)
- Ausgehende Verbindungen (ntfy, UnifiedPush, KI, Föderation) laufen über einen gemeinsamen SSRF-Schutz
  (`saga/netguard.py`); interne Ziele und http nur nach Freigabe durch den Admin
- Beim Beenden schließt Saga zuerst die Live-Verbindungen (höchstens 5 s Wartezeit)
- Neue Abhängigkeit `h2` (HTTP/2 für APNs); Browser-Bibliothek libsodium.js 0.8.4 mitgeliefert, die CSP erlaubt
  `'wasm-unsafe-eval'` nur auf `/chats`
- `tools/test-stack.sh`: `SAGA_PORT` für einen zweiten Test-Stack auf demselben Host

### Behoben
- Datenbanken: Speichern mit veraltetem Schema löscht keine Spalten/Optionen anderer mehr (`srev` im Schema);
  Änderungen an Zeilen erscheinen live bei allen, die die Datenbank offen haben (BUG-001, BUG-049)
- „Apps & Geräte“: Abmelden beendet die Sitzung sofort, „Dieses Gerät“ wird markiert, gleich benannte Geräte
  bekommen „(2)“ (BUG-002, BUG-046)
- Eingaben mit NUL-Zeichen, halben Emoji, NaN/Infinity oder falschem Typ führen nicht mehr zu HTTP 500; keine rohen
  Python-Meldungen in Antworten, deutsche Meldungen mit Umlauten (BUG-007, BUG-008, BUG-041, BUG-083–085)
- Titel und Namen ohne Steuer-, Bidi- und unsichtbare Zeichen (BUG-038); `a<b` im Block-Text bleibt Text (BUG-039)
- Fehler eines internen Ereignis-Zuhörers rollen nur dessen Teil zurück, abgebrochene Transaktionen melden einen
  Fehler (BUG-009); Konto löschen erst sperren, dann alles in einer Transaktion (BUG-042)
- Live: Änderungen erreichen auch die anderen Geräte desselben Kontos; Papierkorb, Archiv, Wiederherstellen,
  Verschieben und Versionen werden gemeldet; Warteschlange begrenzt (BUG-032, BUG-033, BUG-043)
- Föderation: robustere Übernahme fremder Daten, Benachrichtigungen ohne Freigabe abgewiesen, verständliche
  Meldung bei nicht erreichbarer Gegenstelle, Zustellung gleich nach deren Rückkehr (BUG-034–037, BUG-089)
- KI findet auch Seiten, die nur einen Titel haben (BUG-040); Versionsverlauf zeigt „von dir“ und eine lesbare
  Vorschau (BUG-092)
- Seiten-Chat: keine Ereignisse mehr an gerade Entzogene (BUG-088); lange interne Verweise (BUG-090);
  Backup-Dateiname in `TIMEZONE`, „Neu beginnen“ mit falschem Passwort meldet „Passwort falsch.“ (BUG-091)
- Letztes Speichern beim Schließen der Seite: Größengrenze in UTF-8-Bytes statt Zeichen gemessen (Seiten mit
  CJK-Schrift oder Emoji) (BUG-012)

### Hinweise zum Update
- **Version wählen:** `SAGA_VERSION=1` bleibt auf 1.x. Für 2.0 in der `.env` `SAGA_VERSION=2` setzen (neue
  Installationen haben das schon), dann `git pull && docker compose pull && docker compose up -d`.
- **Datenbank:** Migration 4 (`note.updated_by`) und 5 (`note.created_by`, `note_version.author`) sowie neue Tabellen
  laufen beim Start automatisch, vorher legt Saga ein Backup an. Zurück auf 1.x geht danach nur über dieses Backup.
- **Admin einschalten:** KI-Assistent (Admin → „KI-Assistent“) und Föderation (Admin → „Föderation“) sind aus, bis
  der Admin sie einrichtet. Mitteilungen für die iOS-App brauchen einen APNs-Schlüssel (Admin → „Mitteilungen“);
  Web Push, ntfy und UnifiedPush gehen ohne Einrichtung. Teilen und Chats sind an und lassen sich unter
  Admin → Allgemein abschalten.
- **Reverse Proxy:** Für den Live-Kanal darf der Proxy `/api/events` nicht puffern (Beispiele für Caddy, nginx und
  Traefik in der Anleitung, Abschnitt 3). Ohne das funktioniert alles, nur mit bis zu 60 s Verzögerung.
- **Apps:** Die neuen Funktionen gibt es in den Saga-Apps 2.0; ältere Apps funktionieren weiter.
- **Testversion:** `SAGA_VERSION=testing` trägt künftig die nächste geplante Version (z. B. `2.1.0-testing.<commit>`);
  zurück zur stabilen Version mit `SAGA_VERSION=2`.

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
