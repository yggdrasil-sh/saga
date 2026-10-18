# Änderungen

Alle nennenswerten Änderungen an Saga. Versionen folgen [Semantic Versioning](https://semver.org/lang/de/):
**Major** (2.0.0) = Umbau, der Handarbeit beim Update braucht · **Minor** (1.1.0) = neue Funktionen ·
**Patch** (1.0.1) = Fehlerbehebungen. Datenbank-Änderungen laufen beim Start automatisch, vorher legt Saga ein Backup an.

> **Entwicklung:** Neues landet zuerst auf dem Branch `testing` (Image `ghcr.io/yggdrasil-sh/saga:testing`, wird bei
> jedem Push gebaut) und wird hier unter `[Unveröffentlicht]` notiert.
> **Version auf `testing`:** Direkt nach einem Release steht in `VERSION` auf `testing` schon die nächste geplante Version
> (z. B. `1.3.0`). Die Testversion heißt dann `1.3.0-testing.<commit>` – man sieht sofort, welche Version gerade entsteht.
> Ein Release entsteht dadurch noch nicht.
> **Release:** auf `testing` `[Unveröffentlicht]` in `[x.y.z] – Datum` umbenennen (passend zu `VERSION`), dann `testing`
> nach `main` übernehmen und pushen. Die GitHub Action legt Tag, Images und Release an. Danach auf `testing` `VERSION`
> auf die nächste geplante Version setzen.

## [Unveröffentlicht]

### Neu: Russisch und Thai
- **Zwei neue Oberflächensprachen:** Русский (ru) und ไทย (th) für Web-Oberfläche, Admin-Bereich, öffentliche Seiten,
  Fehlermeldungen der API und Mitteilungstexte – wählbar wie bisher unter Einstellungen → Konto → Sprache, im
  Umschalter der Anmeldeseite (`/lang`), als Standardsprache im Admin-Bereich, per `?lang=th`/`?lang=ru` der Apps und
  automatisch über Accept-Language (`th`, `th-TH`, `ru`, `ru-RU` …). Sprachnamen erscheinen in der eigenen Sprache.
  Die Übersetzungen sind **maschinell erstellt** und sollten von Muttersprachlern gegengelesen werden (Russisch in der
  „ты“-Form wie das deutsche „du“; Thai neutral-höflich).
- **Pluralformen:** Übersetzungen können `{n:eins|wenige|viele}` neben `{n}` enthalten (Server `t()` und `T()` im
  Browser, CLDR-Regeln für de/en/es/ru/th – Russisch 1 / 2–4 / 5+ mit 11–14 = viele, Thai ohne Pluralformen). Bestehende
  Schlüssel bleiben unverändert; `tools/i18n.py check` prüft die Anzahl der Formen je Sprache und HTML-Tags,
  `tools/i18n.py merge <lang> <datei.json>` übernimmt Übersetzungen.
- **Datum/Zahlen:** Thai mit gregorianischem Kalender (`th-TH-u-ca-gregory` statt buddhistischer Jahreszählung),
  Russisch `ru-RU`; Server-Datumsformat für ru `TT.MM.JJJJ`, th `T/M/JJJJ`. Der @-Datumsvorschlag im Editor versteht
  auch „завтра“, „пятница“, „через 3 дня“, „พรุ่งนี้“, „วันศุกร์“.
- **KI:** Antworten und Zusammenfassungen in der Oberflächensprache auch für Russisch/Thai; Fragesprache wird bei
  Kyrillisch/Thai an der Schrift erkannt; „Übersetzen nach“ bietet Русский und ไทย.
- **Darstellung:** Thai mit Schrift-Fallbacks (Leelawadee UI, Thonburi, Noto Sans Thai) und etwas mehr Zeilenhöhe.

### Neu: Websuche für die KI
- **Die KI kann im Web suchen** – über die vorhandene SearXNG-Anbindung (`SEARXNG_URL`, wie die Websuche im Dashboard).
  Admin-Bereich → KI-Assistent → „Websuche erlauben“ (Standard: aus; nur wählbar, wenn SearXNG eingerichtet ist, mit
  Datenschutz-Hinweis: Suchbegriffe gehen über SearXNG an Suchmaschinen).
- **Modellunabhängig** (auch kleine Ollama-Modelle, ohne Tool-Calls): Ein kurzer Vorab-Aufruf entscheidet, ob eine Suche
  nötig ist (`NONE` oder höchstens zwei Zeilen `SEARCH: …`, serverseitig streng gefiltert). Dann fragt Saga SearXNG
  (Zeitlimit, höchstens 6 Treffer) und liest den Text der besten zwei Treffer – nur öffentliche Adressen (Prüfung bei
  jeder Verbindung, auch nach Weiterleitungen), nur Port 80/443, nur `text/html`/`text/plain`, mit Größen- und
  Zeitlimit (`AI_WEB_FETCH=0`: nur Ausschnitte). Die Treffer gehen als Daten markiert in den Prompt; Anweisungen darin
  zählen nicht. Antworten nennen die Webquellen mit `[n]` und Link.
- Gilt für den Assistenten (neuer Schalter „Im Web suchen“ je Frage, Standard an) und für „KI im Chat“ (Quellen als
  Links in der Antwort, vom Beleg abgedeckt). Zusammenfassen, Verbessern, Übersetzen und Aufgaben vorschlagen suchen nie.
- Statusanzeige „Marin sucht im Web …“ (Persona-Name, de/en/es) über das `status`-Ereignis; `/api/ai/status` meldet
  `web`, Hinweise im Chat und im Assistenten nennen dann auch die Websuche. Vertrag: `docs/APP-API-KI.md` Abschnitt 3.
- `GET /api/ai/persona` meldet zusätzlich `chat` („KI in Chats erlauben“ an und KI eingerichtet) und `web` – auch für
  Konten, für die der Assistent aus ist (die bekommen `/api/ai/status` nicht); so erkennen Apps, ob „KI im Chat“
  serverweit aus ist.
- Grenzen: jede Websuche zählt als weitere Anfrage im Kontingent, höchstens 10 Websuchen je Person in 10 Minuten
  (`AI_WEB_RATE`), Logs nur mit Anzahl und Dauer. Ist SearXNG nicht erreichbar, kommt die Antwort trotzdem – ohne
  Webdaten, mit kurzem Hinweis.
- Alle KI-Funktionen bekommen jetzt auch die **Uhrzeit** (Zeitzone des Servers) im System-Prompt, damit „heute“,
  „morgen“ und „heute Abend“ stimmen.
- Tests: neue Reihe `tests/ki_web.py` (Fake-SearXNG, Fake-Modell); der Test-Stack setzt dafür `SEARXNG_URL` der
  Test-Instanz auf den Fake.

### Neu: Chats archivieren und löschen
- **Archivieren** (jede Chat-Art, nur für mich): Chat-Menü „⋯“ oder Rechtsklick/Kontextmenü-Taste/langes Antippen in
  der Chatliste. Archivierte Chats stehen in einem aufklappbaren Bereich „Archiviert (n)“ unten in der Liste, zählen nicht
  zu den Ungelesenen (Liste, Dashboard-Knopf, `/api/chats/unread`) und kommen mit der nächsten Nachricht oder einem Anruf
  von selbst zurück – außer sie sind stummgeschaltet. „Aus Archiv holen“ jederzeit.
- **Chat löschen …** mit Auswahl und deutlicher Warnung (nicht rückgängig): **Nur für mich** – der Server setzt eine
  Grenze und löscht die für mich versiegelten Inhaltsschlüssel (alle Geräte + Backup-Schlüssel) der bisherigen
  Nachrichten; alte Nachrichten, Anhänge und Anruf-Einträge liefert er mir nicht mehr aus, die anderen behalten alles.
  Direkt-Chats verschwinden aus der Liste und kommen bei einer neuen Nachricht leer wieder, andere Chats werden
  archiviert, freie Gruppen kann man dabei zusätzlich verlassen; der verschlüsselte Gruppenname bleibt lesbar.
  **Für alle** – Direkt-Chat (beide) und freie Gruppe (Chat-Admins): Chat samt Nachrichten, Schlüsseln, Anhängen und
  Vorschaubildern, Lesestand, KI-Freigabe/-Belegen, Anrufen und Benachrichtigungen gelöscht; die anderen bekommen live
  `chat.removed` und den Hinweis „Name hat den Chat gelöscht.“. Gruppen-Chat (Saga-Admins) und Seiten-Chat
  (Eigentümer der Seite): nur der Verlauf (`chat.cleared`, „Name hat den Verlauf gelöscht.“), der Chat bleibt.
- **Apps**: `DELETE /api/chats/{cid}?scope=me|all`, `PATCH /api/chats/{cid} {archived}`, neue Felder `archived`,
  `archived_at`, `cleared_seq`, `delete_for_all` in der Chatliste, Live-Ereignisse `chat.cleared`/`chat.removed`.
  Vertrag: `docs/APP-API-CHAT.md`. `MIN_APP` unverändert (ältere Apps zeigen archivierte Chats weiter in der Liste).
- **Datenbank**: neue Tabelle `chat_pref` (Stand je Person und Chat) per `CREATE TABLE IF NOT EXISTS` – keine Migration,
  kein neuer Schema-Stand; Kontolöschung entfernt die Einträge, beim Löschen eines Chats gehen sie mit.
- Tests: neue Reihe `tests/chat_loeschen.py` (Rechte je Chat-Art, Archiv-Rückkehr, „für mich“ liefert nichts Altes und
  entfernt die Schlüssel, „für alle“ räumt alles ab, Live-Ereignisse, Kontolöschung).

### Neu: KI-Persona
- **Name, Profilbild und Tonfall für die KI** (Admin-Bereich → KI-Assistent → Persona): Name (leer = „Saga KI“ /
  „Saga AI“ / „Saga IA“), Profilbild mit denselben Prüfungen wie Profilbilder (neu kodiert, 64/128/256 px, WebP und
  PNG/JPEG; ohne Bild ein neutrales ✦-Symbol), optional eine kurze Persönlichkeit/Tonfall (höchstens 500 Zeichen). Der
  Tonfall geht bereinigt (eine Zeile, ohne Datenmarkierungen) als nachrangiger Stil-Zusatz in den System-Prompt aller
  KI-Funktionen – Regeln und Formate wie `TASK:`/`NONE` bleiben unverändert. Änderungen stehen im Server-Log (ohne
  Inhalte).
- **Web**: „Frage Marin“ mit kleinem Profilbild im Seitenkopf und im Seitenmenü, Name und Bild im Kopf der
  Seitenleiste und über jeder Antwort (auch im Verlauf), „Marin denkt nach …“; hell/dunkel und mobil.
- **Apps**: `GET /api/ai/persona` (Name, Bild-Adresse, ETag, ob die KI für mich an ist), `persona` in
  `/api/ai/status`, Bild unter `/api/ai/avatar` (nur angemeldet), Live-Ereignis `ai.persona.changed`. Vertrag:
  `docs/APP-API-KI.md`. Keine Datenbank-Migration (Bild in `avatar_img` unter der Kennung `#ai`).

### Neu: KI im Chat – nur auf Zuruf
- **Die KI kann in Chats mitmachen**, wenn der Admin „KI in Chats erlauben“ einschaltet (Standard: aus) und der Chat sie
  zulässt (Chat-Menü: freie Gruppe – Chat-Admins, Direkt-Chat – beide, Gruppen-Chat – Saga-Admins, Seiten-Chat –
  Eigentümer). Alle Mitglieder sehen dann einen Hinweis. Die KI liest **nur Nachrichten mit @Name** plus bis zu 12
  Nachrichten davor, die das Gerät des Fragenden gezielt schickt (`POST /api/chats/{cid}/ai`); alles andere bleibt
  Ende-zu-Ende-verschlüsselt. Sie sieht keine Seiten, Aufgaben oder Kalender.
- Die Antwort verschlüsselt das Gerät des Fragenden für alle Mitglieder; der Server speichert nur den SHA-256 als Beleg
  (`chat_ai_reply`), Clients zeigen die Nachricht nur bei passendem Hash als Antwort der KI (Name + Bild, „gefragt von
  …“). Kontingent und Gruppen des Assistenten gelten, dazu 6 Fragen je Minute und Person, 20 je 10 Minuten und Chat.
- **Web**: KI als erster Vorschlag bei „@“, Hinweis beim Schreiben, Antwort erscheint live. Vertrag für die Apps:
  `docs/APP-API-KI.md`. Neue Tabellen `chat_ai`, `chat_ai_reply` (ohne Migration, `CREATE TABLE IF NOT EXISTS`).

### Behoben
- **KI mit neueren Claude-Modellen über die OpenAI-kompatible Schnittstelle** (`https://api.anthropic.com/v1`): Saga
  schickt `temperature` nicht mehr, wenn das Modell den Parameter ablehnt (Fehler 400 „temperature is deprecated“), und
  „Verbindung testen“ listet dort auch die angebotenen Modelle.
- **KI im Chat – Belege räumen mit auf**: Der Hash-Beleg einer KI-Antwort (`chat_ai_reply`) wird jetzt mit der
  Nachricht entfernt – beim Löschen der Nachricht, beim Ablauf (Frist des Chats bzw. Admin-Höchstwert) und beim Löschen
  des Chats; das stündliche Aufräumen entfernt zusätzlich abgelaufene, nie gesendete Belege und Belege ohne Nachricht.
- **KI im Chat – Kontolöschung**: Belege, deren Nachricht bestehen bleibt, verlieren den Personenbezug statt zu
  verschwinden (die Kennzeichnung als KI-Antwort bleibt gültig, `ai.by` = `null`); der Vermerk, wer die KI im Chat
  erlaubt hat (`chat_ai.by_uid`), wird geleert. Web zeigt dann „gefragt von Gelöschtes Konto“.
- **KI-Persona – Live-Ereignis `ai.persona.changed`** geht nur noch an Konten, die die KI nutzen dürfen (Gruppen) bzw.
  bis zur Änderung nutzen durften (KI aus, Gruppen geändert – deren Clients blenden sie aus), nicht mehr an alle.
  Gruppenänderungen im Admin-Bereich lösen das Ereignis jetzt auch aus.
- **Tests – Avatare**: „Veröffentlichte Seite“ schlug ab dem zweiten Lauf auf demselben Stack mit 400 („Dieser Name
  ist schon vergeben“) fehl. Kein Serverfehler: der feste Testname eines gelöschten Kontos ist gesperrt, sobald die
  Föderation einen Schlüssel hat (`fed_name_retired`, gewollt). Der Test nimmt jetzt je Lauf einen neuen Namen.

### Verbessert
- **KI-Assistent – „Aufgaben vorschlagen“** liefert nur noch konkrete nächste Schritte im Imperativ (z. B.
  „Weiterlesen ab Seite 12“) statt Feststellungen („Das Buch hat 258 Seiten“): das Modell schreibt je Aufgabe eine Zeile
  `TASK: …` oder `NONE`, der Server verwirft Aussagesätze und wiederholte Eigenschaften. Gibt es nichts zu tun (auch
  bei leeren Seiten, ohne Modellaufruf), kommt die Meldung „Auf dieser Seite gibt es nichts zu tun.“
- **Datenbank-Zeilen**: Eigenschaften gehen je Zeile mit Spaltennamen an das Modell, Rechen-Spalten mit Wert und
  Herkunft („Fortschritt: 4 % (Gelesen 11 / Seiten 258)“), Zahlen mit %/€. „Zusammenfassen“ einer Zeile ohne
  nennenswerten Text antwortet ehrlich „Kaum Inhalt – nur Eigenschaften: …“ (ohne Modell).
- **Markierung nur Bild/leer**: Verbessern/Übersetzen nimmt dann den Text der ganzen Seite und sagt das; leere Blöcke
  einer Markierung werden übergangen.
- **Sprache der Seiten-Hilfen**: Zusammenfassen und Aufgaben in der Sprache der Oberfläche (Konto > Server-Standard >
  Browser), Verbessern behält die Sprache des Textes, Übersetzen die Zielsprache.

### Sprache wählbar – Web
- **Sprachumschalter auf der Anmeldeseite** (🌐 unten: Automatisch (Browser) · Deutsch · English · Español) – für den
  fremden Rechner, der nicht die eigene Sprache spricht. Gilt auch für 2FA-Schritt, Registrierung,
  „Anmeldung & Sicherheit“, App-Anmeldung/-Kopplung und die öffentlichen Fehlerseiten (404/zu viele Anfragen, dort als
  Links ohne Skript). Gemerkt wird nur die Sprache im Cookie `saga_lang` (ein Jahr, HttpOnly, kein Tracking);
  „Automatisch“ löscht es. Ohne JavaScript gibt es den Knopf „Sprache wechseln“.
- **Sprache der App:** Öffnen die Apps Anmeldung oder Geräte-Kopplung mit `&lang=de|en|es`, gilt diese Sprache für die
  Browser-Sitzung (Sitzungs-Cookie `saga_lang_app`, auch über die Weiterleitungen `/app/login` → `/login` → 2FA →
  Rückgabe an die App); unbekannte Werte werden ignoriert.
- **Reihenfolge:** Konto-Sprache > Umschalter (Cookie) > Sprache der App > Standardsprache des Servers > Browser >
  Englisch. Nach der Anmeldung gilt also die Sprache des Kontos, falls eine eingestellt ist, sonst die gewählte bzw.
  die des Browsers.
- **Admin-Bereich:** Umschalter in der Kopfzeile und unter der Admin-Anmeldung. Angemeldet mit fester Konto-Sprache
  ändert der Umschalter die Konto-Sprache mit (sonst hätte er keine Wirkung).
- **Konto-Sprache leichter zu finden:** Einstellungen → Konto zeigt „🌐 Sprache“ jetzt als ersten Block (bisher
  unauffällig zwischen Profilbild und Mitteilungen), „Automatisch“ nennt die gerade aktive Sprache; neu im
  Benutzermenü: **„Sprache“** öffnet direkt diese Auswahl.

## [2.1.0] – 2026-10-17

Saga 2.1 macht die Anmeldung sicherer und den Chat persönlicher: Passkeys und Zwei-Faktor-Anmeldung, Profilbilder,
Audio- und Videoanrufe im Direkt-Chat, Bilder aus Immich im Chat und ein deutlich treffsicherer KI-Assistent.
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
- **Immich im Chat** (Web): „Aus Immich …“ beim Anhängen, mit derselben Auswahl wie auf Seiten; das Bild wird wie jeder
  Anhang Ende-zu-Ende verschlüsselt.
- **Schnelle Chat-Bilder:** Beim Senden entsteht ein verschlüsseltes Vorschaubild. Der Verlauf lädt nur sichtbare
  Vorschaubilder in einen Platzhalter der richtigen Größe (nichts springt), das Original erst beim Antippen.
  Ältere Nachrichten bekommen ihr Vorschaubild im Browser.
- **Bilder in Seiten kopieren, ausschneiden und einfügen:** Bild anklicken, `Strg`/`⌘`+`C`/`X`/`V` – in derselben
  Seite, einer anderen oder einem anderen Tab (für andere Programme auch als PNG). Auf Seiten anderer Konten entsteht
  eine eigene Kopie.
- **KI-Assistent findet mehr und das Richtige:** Wortstämme, Gewichtung seltener Wörter (BM25), zusammengesetzte
  Wörter („Zahnarzttermin“ findet „Zahnarzt … Termin“), Eigenschaften von Datenbank-Zeilen („Welche Projekte sind in
  Arbeit?“), Zeitangaben wie „letzte Woche“. In 22 Testfragen: richtige Quelle 82 % → 95 %. Antworten mit Absätzen,
  Listen und Quellen; ohne passende Seite antwortet Saga selbst, ohne das Kontingent zu belasten.
- **Lokale Modelle robuster:** Ollama bekommt einen passenden Kontext (`num_ctx`), Denkphasen (`<think>` usw.) werden
  nie angezeigt („Das Modell denkt nach …“), das Zeitlimit gilt zwischen zwei Stücken statt für die ganze Antwort.
- **API für die Apps:** Feld `avatar` an allen Personen, `/api/account/avatar`, Live-Ereignis `avatar.changed`;
  Anrufe unter `/api/calls…` mit Live-Ereignissen `call.*` und Push-Abo `apns-voip`; Anmeldung mit zweitem Faktor
  über `/app/login`; „Neu beginnen“ im Chat auch mit Code oder Passkey. Alles additiv.
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

### Geändert
- Anwesenheit erscheint sofort beim Öffnen einer Seite (vorher bis zu 20 s) und flackert nicht mehr.
- Chat-Anhänge werden im Browser zwischengespeichert (verschlüsselt, höchstens 200 MB, geleert beim Abmelden).
  Bis zu 20 Dateien je Nachricht, 60 Uploads je Minute.
- Admin → KI: Verbindungstest prüft das Modell gegen die ganze Liste und bietet sie zur Auswahl an.
- Neue Abhängigkeiten: `webauthn`, `pillow-heif`.

### Behoben
- **Sicherheit:** Eingefügte Blöcke aus der Zwischenablage werden im Browser geprüft (vorher hätte eine präparierte
  Zwischenablage HTML in den Editor bringen können; gespeichert wurde es schon bereinigt).
- **Fehlerrunden aus zwei Bughunts:** Uploads ohne Längenangabe über der Grenze → 413 statt 500; Rückruf während es
  noch klingelt; Anruffenster auf dem Handy; ersetzter Gruppenname nimmt seine Anhänge mit; Esc im Peek schließt nur
  den Dialog; Untermenüs bleiben im Peek sichtbar; veröffentlichte Seiten ohne „@name“ oben; Chat-Platzhalter auf dem
  Handy; Kommentar-Auszug bei Tabellen und Checklisten; Konto wechseln in den Apps; KI-Fehltreffer durch falsche
  Wortzerlegung.

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
- **Passkeys** brauchen in `BASE_URL` einen Hostnamen mit https (oder `localhost`), keine IP-Adresse.
- **2FA-Richtlinie** steht auf „niemanden“ – es ändert sich nichts, bis der Admin sie einschaltet. Bei Problemen:
  `docker compose exec saga python run.py 2fa-zuruecksetzen <name>` bzw. `… run.py 2fa-richtlinie none`.

### Bekannte Grenzen
- Anrufe nur zu zweit im Direkt-Chat, noch keine Gruppenanrufe.
- Ohne TURN-Server kommen Anrufe nicht in allen Netzen zustande (z. B. hinter strengen Firmen- oder Mobilfunk-NATs).
- Passkeys funktionieren nur unter einem Hostnamen (nicht unter einer IP-Adresse in `BASE_URL`).

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
