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

### Neu (2.5)
- **Föderation (Bughunt W02/W03): Personen anderer Server bleiben dieselben – per Handle wie per Namen, auch nach dem
  Umbenennen.** Teilen per Handle (`~…@server`) mit einer Person, die dort einen öffentlichen Namen hat, kam nie an
  (W02): Der Server der Person fragte mit dem Namen an, die Freigabe stand aber unter dem Handle. Jetzt nennt er bei
  Anfragen zusätzlich den stabilen Handle (neue Fähigkeit `rename`), und Handle und Name werden zu einer Person
  zusammengeführt – Teilen, Rolle ändern, Entziehen, Kommentare und Öffnen gehen über beide. Umbenennen des öffentlichen
  Namens löschte alle Freigaben von anderen Servern auf beiden Seiten (W03): Jetzt kommt `person.renamed` (alt → neu,
  Handle) statt `person.gone`; Freigaben, Eingangs-Verweise (auch für Eigentümer), Kommentare, Versionen,
  Benachrichtigungen, Anzeigenamen und Adressbuch werden umgeschrieben. Ebenso, wenn jemand erstmals einen Namen setzt.
  Server ohne `rename` (ältere Versionen) bekommen wie bisher `person.gone`; der Umbenennen-Dialog nennt dann vorher die
  betroffenen Seiten und Server. Nur der Server der Person kann sie umbenennen; ein Name, der dort schon einer anderen
  Person gehört, wird nicht übernommen. Neue Tabelle `fed_alias`. Tests `foederation_handles` (neu), `foederation_boese`.
- **Sicherheit (Bughunt S01/R01): Schaltfläche „Neuer Eintrag“ mit Vorlage über `via`.** Wer eine geteilte Datenbank
  bearbeiten durfte, konnte sich eine eigene Seite mit gefälschter Schaltfläche bauen und so jede Vorlage des Eigentümers
  kopieren und lesen (auch aus nicht geteilten Datenbanken). Jetzt signiert der Server Schaltflächen mit Vorlage, wenn der
  Eigentümer die Seite speichert; die via-Seite muss dem Eigentümer gehören und genau diese signierte Schaltfläche
  enthalten. Andere Bearbeiter behalten vorhandene Schaltflächen, führen aber keine neuen Vorlagen ein. `template: true`
  wirkt per via nicht mehr (R03), Nextcloud-Blöcke der Vorlage bleiben signiert (R05). **Bestehende Seiten:** Der
  Eigentümer speichert die Seite mit der Schaltfläche einmal (z. B. Schaltfläche öffnen und schließen), dann geht sie für
  Geteilte wieder. Test `vorlagen_teilen` 18/18.
- KI „Datenbank zuordnen“: Option „Keine“ wird zugeordnet (R06), sichtbare Datenbanken hinter gesperrten fehlen nicht
  mehr (R07), leere Einträge auch jenseits von 2000 Zeilen (R08). Web: Standardvorlage + Enter in der letzten Zeile
  verschluckte danach Eingaben (R02). Anrufe: ein Begleitgerät, das kurz aus der Liste fällt, gilt nicht mehr als
  „übernommen“ (R09).
- **Anrufe: eigenes Bild verschiebbar und verkleinerbar (Web).** Ziehen rastet in der nächsten Ecke ein, Klick
  (oder Enter) verkleinert es zu einer kleinen Blase und wieder zurück, Pfeiltasten verschieben. Der Browser merkt sich
  beides (`saga-call-self`); ohne Wahl bleibt alles wie bisher.
- **Behoben: Schaltfläche „Neuer Eintrag“ mit Vorlage auf geteilten Seiten.** Für Personen, mit denen die Seite geteilt
  ist, schlug sie fehl, wenn die Vorlage in einer (nicht geteilten) Datenbank des Eigentümers liegt – Duplizieren durfte
  nur der Eigentümer. Jetzt schicken Clients `via` (die Seite mit der Schaltfläche) mit; der Server erlaubt die Kopie nur,
  wenn diese Seite (Lesen genügt) genau diese Schaltfläche (Vorlage → Ziel) enthält, die Person das Ziel bearbeiten darf
  und Vorlage und Ziel demselben Konto gehören. Der Eintrag gehört dem Eigentümer („angelegt von“ = die Person).
- **Eingebettete Datenbank: Titel und Ansichts-Reiter ausblendbar.** Unter „⚙ Ansicht“ → „Dieser Block“ je Block
  „Datenbanktitel anzeigen“ und „Ansichts-Reiter anzeigen“ (wie Notion). Gespeichert im Block (`hide_title`,
  `hide_tabs`); veröffentlichte Seiten lassen den Titel dann ebenfalls weg. Die Datenbank-Seite selbst bleibt unverändert.
- **Vorlagen verwalten.** Vorlagen ließen sich bisher nur anlegen. Jetzt unter „+ Neu ▾“ bzw. „⚙ Ansicht“ einer
  Datenbank und unter „+ Neue Seite“ (eigene Seitenvorlagen): Liste mit Umbenennen direkt im Feld, Bearbeiten,
  Duplizieren (bleibt Vorlage), „Keine Vorlage mehr“, Papierkorb (mit Rückfrage) und „+ Neue Vorlage“. Je Datenbank
  eine **Standardvorlage für „+ Neu“** (Schema `default_tpl`; „Leerer Eintrag“ legt weiter leer an). Ältere Apps, die das
  Schema ohne den Schlüssel speichern, behalten den Standard; nur Vorlagen dieser Datenbank zählen.
  `POST /api/notes/<id>/duplicate` nimmt `template: true`. Menüs aus Dialogen liegen jetzt über dem Dialog.
- **KI-Assistent und Datenbanken.** Zusammenfassen/Aufgaben einer Seite lesen jetzt auch die **eingebetteten
  Datenbanken** (auch in Spalten/Toggles, nur mit Zugriff, höchstens 5 Datenbanken / 300 Einträge). Neu „**Datenbank
  zuordnen**“: der Assistent schlägt für eine Eigenschaft (Auswahl, Mehrfachauswahl, Häkchen, Text, Zahl, Datum, URL)
  je Eintrag einen Wert vor – auf Wunsch nur für leere Felder und mit eigenem Hinweis („nach Dringlichkeit“). Auswahl
  nur aus vorhandenen Optionen, unsichere Einträge bleiben leer. Der Assistent schreibt weiterhin nichts: die Person
  hakt ab und übernimmt (normale Bearbeiten-Rechte). `GET /api/ai/page/<id>/dbs`, `POST /api/ai/page/<id>`
  `{action: "assign", db, prop, only_empty?, hint?}` → `done.assign {db, prop, items[{id,title,value,label,old}], unsure}`.
- **Alle Anrufe über Knoten (auch 1:1).** Neue Admin-Einstellung unter Anrufe → Gjallarhorn (Standard aus): 1:1-Anrufe
  laufen dann über einen Knoten – die Geräte sehen gegenseitig keine IP-Adressen, weniger NAT-Probleme.
  `GET /api/calls/config` meldet `horn_dm`; Clients rufen mit `via: "horn"` an und bekommen ohne bereiten Knoten, ohne
  passendes Gerät beim Gegenüber oder im föderierten Chat 409 `fallback: "direct"` (dann wie bisher direkt). Klingeln,
  Besetzt, Ablehnen, Push (`dm: true`, Name der anrufenden Person) und Chat-Eintrag wie 1:1. Web-Client nutzt den Weg
  mit Rückfall; ältere Clients rufen weiter direkt an.
- **1:1 über Knoten auch mit anderen Servern.** Föderierte Direkt-Chats nutzen bei „Alle Anrufe über Knoten“ ebenfalls
  einen Knoten (neuer Föderations-Auftrag `dmhorn`, Fähigkeit `horn_dm`): Den Anruf führt der Server der anrufenden
  Person, der andere Server legt eine Kopie an und lässt klingeln; Annehmen, Auflegen und Knotenwahl laufen wie bei
  föderierten Gruppenanrufen über Knoten (Kaskade bzw. geteilte Knoten). Damit klappen Anrufe auch, wenn TURN nur
  intern Relay-Adressen vergibt. Ohne `horn_dm` beim anderen Server weiter direkt (`horn_block: "fed"`).
  Kopien von Gruppenanrufen melden jetzt beim Wechsel auf „aktiv“ `call.state active` an die eigenen Geräte, und wer
  über einen anderen Server beitritt, gilt dort nicht mehr als „verpasst“.
- **Anruf-Oberfläche vereinheitlicht (Web, gleiche Regeln wie die Apps, docs/ANRUF-UI.md in saga-apps).** Kopf mit
  Titel (Gegenüber bzw. Gruppe) und Dauer darunter, dann eine Sicherheitszeile; Teilnehmenden-Chip mit Symbol nur in
  Gruppen (`11/12` erst nahe der Grenze); ab drei Geräten ist das eigene Bild eine normale Kachel „Du“, bei drei
  Kacheln die dritte über die volle Breite; „… teilt den Bildschirm“ als neutrale Bildunterschrift am geteilten Bild;
  Pseudonym-Handles in der Mitte gekürzt; Symbole statt Emoji (Schloss, stumm, Verkleinern); Namensleiste 70 % deckend.
  Der Hinweis vor dem Teilen sagt in Gruppen „sehen alle im Anruf“.

### Behoben – „Hierher übernehmen“ bei Anrufen über einen Knoten (Web)
- Über einen Knoten gibt es keine Direktverbindung zum bisherigen Gerät: Das neue Gerät meldet die Übernahme jetzt
  über den Signalweg (state.ready + replaces) und meldet das alte danach beim Server ab; ein Gerät, das aus der
  Teilnehmerliste fällt, während ein anderes eigenes dabei ist, beendet den Anruf („Anruf auf … übernommen“).

### Admin und Knoten
- Anrufe: „Über Knoten ab“ (ab wie vielen Mitgliedern ein Gruppenanruf über einen Knoten läuft), Liste „Geteilte
  Knoten anderer Server“, und der Hinweis „kein Knoten bereit“ nennt Server, deren geteilte Knoten nutzbar sind.
- Knoten auf testing aktualisieren sich selbst: die CI legt signierte Binärdateien jeder Vorabversion in das
  rollende Vorab-Release `horn-testing`.
- IPv6: Ohne IPv6 bei Saga (Heimnetz, Proxy ohne AAAA) meldet der Knoten die öffentliche IPv6 seiner Schnittstelle,
  und Saga übernimmt sie ungeprüft, wenn der Knoten über IPv4 bestätigt ist (Hinweis im Admin).

### Behoben – Profilbilder fehlten manchmal (alle Plattformen)
- Ohne gültige Sitzung antwortete `/api/avatar/…` mit 404 („kein Bild“). Die Apps merkten sich das (Android bis
  zum Neustart, iOS 10 Minuten) und erneuerten die abgelaufene Sitzung nicht – nach dem Start oder einer Pause
  (z. B. vom Chat in eine Seite) blieben nur Initialen. Jetzt 401 (für jeden nicht freigegebenen Schlüssel gleich,
  verrät also nichts); die Apps erneuern die Sitzung und laden das Bild. Freigegebene Bilder für veröffentlichte
  Seiten kommen wie bisher ohne Anmeldung. Web: ein fehlgeschlagenes Profilbild wird einmal nachgeladen.

### Sicherheit (Prüfung 2.5)
- **Kaskade: fremdes Token streng geprüft.** Das Kaskaden-Token eines verbundenen Servers geht unverändert in eine
  Kopfzeile der Wählanfrage des eigenen Knotens. Saga (`fed_horn.ctok_ok`) und der Knoten (`bifrost.go peerTokenOK`, vor
  jedem Wählen) nehmen nur noch `c1.<Base64url>.<Base64url>` bis 4096 Zeichen – CR/LF konnten vorher Kopfzeilen einschleusen.
- **Kaskade: kein DNS-Rebinding.** Saga gibt dem Knoten die geprüften Adressen des Wählziels mit (`ips`), der Knoten wählt
  genau diese (Name nur für SNI/Host). Ohne Adressen löst der Knoten selbst auf und nimmt nur öffentliche (auch `::ffff:`,
  NAT64, 6to4 geprüft); Kaskaden zu eigenen Knoten (`own`) bleiben im LAN möglich.
- **CSP nur aus geprüften Ursprüngen.** Adressen geteilter Knoten (hello `horn_shared`) und Knoten-URLs (`_clean_hello`)
  müssen genau `https://host[:port]` sein (keine Wildcards, kein Leerraum, kein Pfad); der CSP-Zusatz nimmt nur solche.
- **Kopplung: 424 ohne Netzdetails.** Die Antwort nennt je Adressfamilie nur einen Fehlercode (`timeout`, `unreachable_tcp`,
  `tls`, `http`, `signatur`, `adresse`); Details stehen nur im Prüfprotokoll des Admin-Bereichs.
- **chat_v3 aus wirkt auch auf föderierte Ereignisse:** eingehende `chat.react`/`chat.msg.edit` werden dann ignoriert
  (nicht gespeichert, nicht ausgeliefert).
- **Wächter/Selbst-Update ohne Downgrade:** nur Versionen neuer als die laufende (SemVer, rc-Ordnung); der Wächter startet nie
  ein älteres oder gleiches `bin/current` als das Image-Binär und verwirft ein älteres `bin/next`.
- **TURN-Relay des Knotens enger:** keine TCP-Allocations (RFC 6062) mehr; UDP-Relay nur an die Relay-Adresse und dort nur an
  Ports der ICE-Agenten des TURN-Wegs. Vorher erreichte ein Gerät mit gültigem Medien-Token jeden TCP-/UDP-Dienst auf der
  Relay-Adresse (Standard 127.0.0.1). CreatePermission/ChannelBind auf andere Adressen (auch `::ffff:`, intern) abgelehnt.
- **Hinweis „Knoten müssen neu gekoppelt werden“** (Admin, Anrufe), wenn Saga einen neuen Signaturschlüssel erzeugt hat,
  obwohl schon Knoten gekoppelt waren (SECRET_KEY gewechselt, Wiederherstellung ohne Gjallarhorn-Einstellungen).
- **Rate-Limit beim Koppeln:** volle Speicher verdrängen die am längsten unbenutzten Einträge statt alles zu leeren.

### Gjallarhorn über Servergrenzen (Web-Test 2.5)
- **Kein HTTP 500 mehr beim föderierten Beitritt:** antwortet das Heim mit Text statt JSON (Zeitüberschreitung, Fehlerseite),
  gibt es eine klare Meldung; ein unerwarteter Fehler trägt das Gerät wieder aus (kein Bein ohne Person mehr). Der Client
  meldet sich auch nach einer fehlgeschlagenen Beitrittsanfrage ab – damit erscheint die Leiste „Gruppenanruf läuft ·
  Beitreten“ im föderierten Chat wieder.
- **Kein falscher Hinweis „Kein Knoten bereit“** vor einem Gruppenanruf in föderierten Chats (dort entscheidet das Heim, der
  Server nennt beim Mesh-Rückfall selbst den Grund).
- **Notaus trennt sichtbar:** die Geräte des betroffenen Servers werden aus laufenden Knoten-Anrufen ausgetragen; die anderen
  sehen „wurde getrennt (Notaus …)“, das Gerät selbst beendet den Anruf mit Begründung statt mit eingefrorenem Bild.
- **Mesh-Rückfall mit richtigem Grund** (Notaus, Erlaubt-Liste, kein Knoten). Erlaubt-Liste aus verhindert die Kaskade über
  einen eigenen Knoten des anderen Servers nicht (eine zwischengespeicherte „0 Knoten“ wird vor dem Start neu erfragt).
- **Erweitern:** Warnung bei `http://`- bzw. lokaler Saga-Adresse (Dialog und Skript), Image per Digest gepinnt, wenn bekannt
  (`SAGA_HORN_IMAGE_DIGEST` oder `gjallarhorn/image.digest`); Fortschritt und Weg-Tooltip nennen den tatsächlichen Port.
- **Neue Kachel im Knoten-Anruf** bleibt bis zum ersten echten Bild dunkel mit Avatar; Namen stehen auf dunklem Grund.

### Chat-Stufe 3 (in Arbeit, Konzept „Krypto v3 über Handles“)
- **Testvektoren für Reaktionen und Bearbeiten:** `tests/vectors/chat_v3.json` mit Schlüsselableitung, AD und Signatur einer
  Reaktion, dem Inhalt einer Bearbeitung, Fällen, die jede Umsetzung ablehnen muss (fremder Absender, falsches `edit_of`,
  andere `chat_ref`, fremder Schlüssel, falscher Inhalt) und den Regeln für die Übernahme des Vertrauens von v1 auf Handles.
  Server-Referenz `saga/chat_v3.py`, Gegenproben `tools/chat_vectors_v3.py` (PyNaCl) und `tools/chat_vectors_v3.mjs`
  (libsodium.js mit `static/chat-crypto.js`); die Testreihe `chat_v2_vektoren` prüft sie mit.
- **Datenbank: Migration 15** – Umstellpunkt je Chat (`chat.v3_seq`), Bearbeitungen am Original (`chat_msg.edit_of`,
  `edit_seq`, `edited_at`, `edit_n`) und die Tabelle `chat_reaction`. Läuft beim Start automatisch (vorher Backup);
  2.4-Backups bleiben einspielbar, umgekehrt nicht.
- **Lokale Chats der Stufe 3 (Server-Kern, vorerst abschaltbar):** Ist die Einstellung `chat_v3` an (Standard noch aus,
  bis Apps und Web nachziehen), legt Saga neue Direkt-, Gruppen-, freie und Seiten-Chats im Format `saga-chat/v2` an,
  sobald alle Mitglieder ein Handle-Zertifikat haben – über den Weg der Chats über Servergrenzen, mit dem eigenen
  Server als Heim, auch bei abgeschalteter Föderation. In solchen Chats steht für andere Konten überall der Handle statt
  des Anmeldenamens: Mitglieder, Gegenüber, Absender, Empfänger, Gelesen, Tippen, Löschen-Hinweise. Apps 2.3/2.4
  (`X-Saga-Chat: 2`) sehen sie als `v: 2`, ältere einen Hinweis. Schlüsselwechsel melden Chat-Partnern lokal
  `chat.keys.changed`; Schlüssel eigener Konten gibt es per Handle nur für Chat-Partner; ein Stufe-3-Chat macht den
  Anmeldenamen nicht mehr als Eingabe nutzbar. Neue Testreihe `anmeldenamen_v3` (Marker-Scan über alle Antworten und
  Ereignisse). **Anrufe** in Stufe-3-Chats laufen als saga-call/v2 mit Handles (1:1 und Gruppe, `v: 2`, Gegenüber, Teilnehmerliste,
  Klingeln, Anruf-Eintrag im Chat ohne Anmeldenamen); `tests/browser_v3.py` lässt die Browser-Tests für Anrufe und Gelesen
  in Stufe 3 laufen. **Chats über Servergrenzen** zeigen lokale Mitmitglieder ebenso nur als Handle (Mitglieder, Absender,
  Gelesen, Tippen, Anrufe); 2.3 umgestellte Seiten-Chats merken sich den Umstellpunkt (`v3_seq`, Altbestand v1).
  Eine **freie Gruppe der Stufe 3** nimmt Personen anderer Server auf (`POST …/members {handle}`): sie wird ohne neue Krypto
  zu einer Gruppe über Servergrenzen (gleiche `chat_ref`, KI aus, Systemzeile).
- **Umstellung bestehender Chats auf Stufe 3 (Auftrag, stündlich):** Ist Stufe 3 an, stellt Saga lokale Chats v1 um, sobald
  alle Mitglieder ein Handle-Zertifikat haben und **alle aktiven Geräte** (letzte 30 Tage) Saga 2.5 melden – Apps 2.3/2.4
  übernehmen „Überprüft“ nicht, deshalb wartet der Auftrag auf sie. Spätestens nach einer Frist (Admin-Bereich →
  Einstellungen → Chats, Standard 90 Tage) wird trotzdem umgestellt; ältere Apps sehen dann im Chat-Namen „Sicherheitsnummer
  bitte neu prüfen“, die Systemzeile trägt `verify`. Nichts wird neu verschlüsselt: Nachrichten bis zum Umstellpunkt
  (`v3_seq`) bleiben v1 und lesbar, danach gilt das Format v2. Wer nach der Umstellung dazukommt, sieht keinen
  Anmeldenamen; `knows()` zählt nur Mitglieder von vor der Umstellung. Clients der Stufe 3 bekommen in Chats v1
  `members[].handle` (für die Vertrauens-Übernahme) und melden ihre Stufe am Gerät (`chat3`). Kontolöschung räumt
  Reaktionen und Verweise auf Bearbeitungen auf. Neue Testreihe `chat_v3_umstellung`.

### Chat-Grundlagen: Reaktionen, Bearbeiten, Sprachnachrichten (Server)
- **Vorerst abschaltbar:** hängt wie Stufe 3 an der Einstellung `chat_v3` (Standard aus). Aus: keine Fähigkeit `chat3`
  gegenüber anderen Servern, `x3_ok: false`, Reaktion/Bearbeiten 409, eingehende Reaktionen/Bearbeitungen werden ignoriert.
- **Reaktionen:** eine je Person und Nachricht, Ende-zu-Ende verschlüsselt mit einem aus dem Nachrichtenschlüssel
  abgeleiteten Schlüssel (`PUT /api/chats/{id}/messages/{mid}/reaction`, `rx` in `GET …/messages`,
  `GET /api/chats/{id}/reactions`, Live `chat.react` gebündelt). Der Server sieht wer, wann und worauf, nicht womit, und
  prüft die Signatur. Kein Push.
- **Nachricht bearbeiten:** nur eigene, bis 24 Stunden nach dem Senden, höchstens 20-mal, nicht für KI-Antworten. Kein
  Verlauf – der Server behält nur die neueste Bearbeitung und löscht ältere samt Schlüsseln; `GET …/messages` liefert sie am
  Original (`edit`, `edited_at`, `edit_n`), Live `chat.edited`, kein Push, kein Ungelesen.
- **Sprachnachrichten:** ein verschlüsselter Anhang (Ogg/Opus); der Server meldet nur die Grenzen (höchstens 15 Minuten,
  24 kbit/s) in `GET /api/chat/me` (`x3_limits`).
- **Über Servergrenzen** nur, wenn alle beteiligten Server die neue Fähigkeit `chat3` melden (Chat-Feld `x3_ok`):
  Reaktionen und Bearbeitungen laufen wie Gelesen-Bestätigungen über das Heim. Server 2.4 merken nichts davon.
  Neue Testreihen `chat_x3` und `foederation_x3` (auch gegen einen 2.4-Server).

### Chat im Web: Stufe 3, Reaktionen, Bearbeiten, Sprachnachrichten
- **Stufe 3:** Das Web meldet `X-Saga-Chat: 3` und nimmt in lokalen Chats der Stufe 3 die Wege von saga-chat/v2, ohne
  Server-Herkunft anzuzeigen. Vertrauen aus alten Chats (`trust:<benutzer>`) wird bei gleichem Identitätsschlüssel zu
  `trust2:<handle>` übernommen („Überprüft“ bleibt); die Sicherheitsnummer zeigt dann „Neue Darstellung, gleicher
  Schlüssel“. Systemzeile zur Umstellung, gerade umgestellte Chats beim Senden neu laden. Anrufe in Stufe 3 wie saga-call/v2.
- **Reaktionen:** Schnellleiste mit 👍 ❤️ 😂 😮 😢 🙏 und „+“ (lokale Emoji-Auswahl ohne CDN, zuletzt benutzte), Chips mit
  Anzahl unter der Nachricht (eigene hervorgehoben, Tippen = mitmachen bzw. zurücknehmen), live; schnelles Wechseln
  sendet nur die letzte Wahl. Prüfen und Öffnen im Chat-Worker, Signaturen über WebCrypto Ed25519 (sonst libsodium.js),
  neueste Nachrichten zuerst, sehr viele Reaktionen auf zwei Worker verteilt.
- **Bearbeiten:** im Nachrichtenmenü oder mit Pfeil hoch im leeren Feld; Leiste „Bearbeiten · Abbrechen“ (Esc), danach
  „bearbeitet“ mit Zeitpunkt, auch in Antworten und Vorschauen der neueste Text.
- **Sprachnachrichten:** Mikrofon-Knopf bei leerem Feld; Aufnahme Opus mono 24 kbit/s als Ogg (Chromium: WebCodecs und
  eigener Ogg-Muxer, Firefox: MediaRecorder Ogg, Safari: WebM in Ogg umgepackt), höchstens 15 Minuten (Hinweis ab 14:00),
  unter 1 Sekunde verworfen; Vorschau zum Anhören, Verwerfen oder Senden. Abspielen über den Service Worker wie Videos,
  Wellenform mit 64 Balken zum Springen, Tempo 1×/1,5×/2×, danach die nächste Sprachnachricht.
  Ogg-Granule nach RFC 7845 (zählt ab 0 inklusive pre-skip, Ende auf die Aufnahme abgeschnitten).
- Neue Browser-Reihen `browser_x3.js`, `browser_voice.js` und `browser_trust3.js` (Vertrauen über die Umstellung, über `browser_v3`), Messungen `tools/perf/x3_server.py`,
  `tools/perf/x3_browser.js`, `tools/perf/x3_worker.mjs`.

### Mitteilungen

- **Android ohne Verteiler-App (eigene Push-Verbindung).** UnifiedPush (ntfy, NextPush …) bleibt der Hauptweg. Ist auf
  dem Handy kein Verteiler installiert, hält die Android-App selbst eine stromsparende Verbindung zu Saga
  (WebSocket `GET /api/push/stream`, kein Google FCM). Für den Server ist das nur ein weiteres Push-Ziel (Abo-Art
  `stream`): dieselbe nach RFC 8291 verschlüsselte Nutzlast wie für Web Push/UnifiedPush, dieselbe Lebensdauer (TTL) und
  Ersetzung (Topic); was ankommt, während das Handy nicht verbunden ist, wird abgelegt und beim nächsten Verbinden
  zugestellt (bis zur Bestätigung durch die App). Der Server pingt nie selbst – die App wählt den größten Abstand, den
  Netz und Proxy zulassen (lernend, Start bei 9 Minuten, bis 28 Minuten; für früh trennende Netze/Proxys
  auch kürzer). Hat ein Gerät später ein UnifiedPush-Abo, bekommt seine
  eigene Verbindung nichts mehr (Umstieg ohne doppelte Mitteilungen).
- **Sparsam auf dem Server:** reines ASGI vor allen Middlewares, keine Zeitschleifen, keine Warteschlangen im Speicher.
  Gemessen (`tools/perf/push_stream.py`, Zuwachs zwischen 500 und 2500 ruhenden Verbindungen): Python ≈ 23 KB je
  Verbindung; `saga-media` reicht die Verbindung über einen schlanken Tunnel durch (≈ 8 KB je Verbindung statt ≈ 84 KB
  mit dem allgemeinen Proxy). Leerlauf-CPU unverändert (keine Arbeit je ruhender Verbindung), ≈ 0,1 s CPU je 1000
  Lebenszeichen.
- **Admin:** neuer Schalter „Eigene Verbindung der Android-App (ohne Verteiler)“ unter Mitteilungen (Standard an), mit
  Zahl der verbundenen Geräte. Hinter nginx braucht `/api/push/stream` die WebSocket-Köpfe und ein langes
  `proxy_read_timeout` (Anleitung, Abschnitt Reverse Proxy).

### Behoben (Testrunde Web 2.5)
- **Kleinigkeiten aus der Testrunde Web (niedrig):** Admin „Anrufe“: „Erweitern“ erst mit Gjallarhorn an (Nr. 002),
  Mesh-Grenzen unter eigener Überschrift „Gruppenanrufe ohne Knoten (Mesh)“ (Nr. 002), nach dem Einschalten lädt die
  Knotentabelle nach, sobald der eingebaute Knoten da ist, Kontrollkästchen gleich groß (Nr. 003), „mit 1 verbundenen
  Server“ und einheitlich „Fähigkeiten jetzt abgleichen“ (Nr. 005), Föderation: Kontingent-Felder mit dem Standard als
  Platzhalter und ohne gequetschte Beschriftung (Nr. 006), Knotenname ohne einsamen Punkt ohne DNS-Zone (Nr. 008),
  Texte mit dem tatsächlichen Port statt fest 443 (Nr. 013/069), „Port prüfen“ springt nicht und meldet „Geprüft ✓“
  samt Zeitpunkt (Nr. 014), Netzfehler in Worten statt „ConnectError“ (Nr. 018). Anrufe: Hinweise (z. B. „… hat dich
  stummgeschaltet“) liegen jetzt über dem Anruffenster (Nr. 074), „Wirklich entfernen?“ 8 s (Nr. 076), Runensiegel und
  Teilnehmerliste schließen mit Esc und nie übereinander (Nr. 071), Kopf zeigt den Chatnamen auch bei anderem offenem
  Chat (Nr. 106), kein minutenlanges „neuer Versuch“ bei Notaus/erschöpftem Kontingent des anderen Servers (Nr. 119).
  Chat: „… tippt“ verschwindet mit der Nachricht (Nr. 103). Seiten: Datumszellen im Format der Oberfläche (Nr. 150),
  Esc nimmt eine gerade angelegte leere Zeile wieder weg, „+ Neu“ legt keine zweite leere an (Nr. 148), Fokus im Titel
  nach eingebauter Vorlage (Nr. 134/139). Aufgaben: Tipp zum Fälligkeitsdatum bei „… morgen“ (Nr. 160).
- **1:1-Anruf klingelte manchmal nicht** (Nr. 173, schon in 2.4): Kam ein Anruf in der Lücke zwischen dem Laden der Seite
  und dem Aufbau des Live-Kanals (der startet erst ≈2 s nach dem Laden), ging das Ereignis verloren – der Anrufer sah
  „Klingelt …“, beim Gegenüber blieb es still. Der Browser gleicht laufende Anrufe jetzt bei jedem `hello` des Live-Kanals ab
  (neues internes Ereignis `live.hello`) und nimmt dabei auch ein Klingeln weg, dessen Ende in dieser Lücke lag.
  Regressionstest `tests/browser_klingeln.js` (verzögert den Live-Kanal gezielt, läuft in `browser_v3` in Stufe 3).
- **„✓ überprüft“ bleibt nach der Wiederherstellung** (Nr. 063): Der Browser sichert seinen Vertrauens-Stand (wer als
  überprüft markiert ist, gemerkte Identitätsschlüssel) verschlüsselt auf dem Server (`GET`/`PUT /api/chat/trust`, Schlüssel
  aus dem Backup-Schlüssel – der Server kann nichts lesen und kein „überprüft“ unterschieben). Ein neuer Browser holt ihn
  nach Wiederherstellungsschlüssel oder Kopplung vor dem ersten Laden; eigene Geräte gleichen ihn nebenher ab. Neu
  beginnen verwirft ihn. `docs/APP-API.md` (Chat-Stufe 3), Tests in `chat` und `browser_trust3.js`.
- **✓✓ bleibt nach der Umstellung auf Stufe 3** (Nr. 054): Alte, schon gelesene Nachrichten (vor `v3_seq`, ohne `fseq`)
  zeigen wieder „gelesen“ – aus der noch gespeicherten Bestätigung im alten Format (geprüft mit dem gemerkten Benutzernamen
  zum Handle) oder, sobald die Person nach der Umstellung etwas bestätigt hat. Test in `browser_trust3.js`.
- **Anrufdauer** (Nr. 075/089): Die Anzeige läuft im Sekundentakt aus Zeitstempeln (bisher nur im Takt der Statistik,
  unter Last seltener) und zählt für alle ab dem Beginn des Gesprächs laut Server (`answered`, umgerechnet über die neue
  Server-Uhr `now` im Anruf-Objekt) – wer später beitritt, sieht dieselbe Dauer wie der Chat-Eintrag. Der Chat-Eintrag
  selbst rechnet weiter ab dem Gesprächsbeginn (zweite Person), nicht ab dem Klingeln: im Testfall 4:42 bei 16 s Klingeln
  und Ende um 09:21:41 – korrekt.
- **Admin → Allgemein → Chats: Schalter „Chat-Stufe 3“** (Nr. 007) mit Erklärung (5 Sprachen) direkt über der Frist
  `chat_v3_days` – bisher nur über die Datenbank einzuschalten.
- **Admin → Anrufe → Grenzen** (Nr. 020): Werte, die der Server korrigiert, meldet er jetzt („Video je Anruf: auf 25
  begrenzt (erlaubt 2–25)“, „Teilnehmende: 0 geht nicht – es gilt der Standard 10“) statt nur „Gespeichert.“.
- **Admin-Kopfleiste** bei mittleren Breiten (721–1600 px): Titel und Knöpfe in einer Zeile, die Reiter darunter in einer
  waagrecht scrollbaren Zeile (wie am Handy) statt in zwei bis drei umgebrochenen Zeilen; der aktive Reiter steht mittig.
- **Emoji-Leiste** (Nr. 034): keine leere Trennlinie mehr unter der Schnellleiste.
- **Kleinigkeiten im Chat:** Bearbeiten mit leerem Text sagt jetzt, dass das nicht geht (und wie man löscht), statt still
  abzubrechen (Nr. 044); Tempo der Sprachnachricht in der Schreibweise der Sprache („1,5×“, Nr. 049); im Kopf von Gruppen
  steht „du“ zuerst (Nr. 058); die Eingabe des Wiederherstellungsschlüssels nennt das Format (Nr. 061); die Zeit in der
  Chatliste („vor 12 Sekunden“) läuft mit (Nr. 032).

### Neu (Vorbereitung 2.5, Gjallarhorn)
- **Horn-Probe** (`gjallarhorn/`, Paket P0): Prototyp des Gjallarhorn-Knotens als eigenes Go-Programm (Pion, nur
  MIT/BSD/Apache-Abhängigkeiten). `gjallarhorn probe` öffnet einen Port für HTTPS-Messseite, TURN-TLS (nur zum Knoten
  selbst) und ICE-TCP, optional UDP mit Weiche nach RFC 9443. Die Messseite simuliert ohne Kamera einen Anruf mit
  12 Kameras bzw. 25 Personen nur mit Ton und misst je Weg RTT, Ton-Verdeckung, Einfrieren und Datenraten.
  `gjallarhorn bench` misst die Knotenlast mit lokalen Bots, `gjallarhorn report` erstellt die Go/No-Go-Tabelle.
  Zertifikat aus Dateien (mit Neuladen), selbst signiert für Tests oder per TLS-ALPN-01; PROXY-Protokoll v2 (`0`, `1`,
  `auto`). Anleitung: `gjallarhorn/PROBE.md`. Saga selbst ist davon noch nicht berührt.
- **Gjallarhorn-Knoten, Weichen (P1):**
  - PROXY-Protokoll zusätzlich in v1 (Modi `0`, `1`, `auto` bleiben).
  - Begrenzung offener Handshakes und kurze Fristen.
  - 443/UDP beantwortet einfache STUN-Bindings selbst (Erreichbarkeitsprüfung); dazu ein UDP-Selbsttest beim Start.
  - Eigener ICE-TCP-Mux ohne Allokation je Paket.
  - „Nachläufer“ gegen lange TCP-Staus auf verlustbehafteten Wegen.
  - Das Zertifikat wird nur noch bei geänderten Dateien neu geladen und gemeldet (vorher alle 30 s).
- **Gjallarhorn-Knoten, Weiterleiter (P2):** `gjallarhorn node` mit echtem SFU (`internal/sfu`).
  - Räume, Beine mit Rollen: Ton- und Bild-Leitung auf TCP, BUNDLE auf UDP.
  - Top-3-Ton per audio-level, Hauptsprecher mit Hysterese, Last-N (Desktop 9, Handy 4).
  - Schichtwahl je Empfänger: UDP 3 Schichten, TCP 2. Kacheln auf TCP mit 180p und höchstens 10 Bildern/s.
  - Vorrang-Warteschlange mit Verwerfen ab 150 ms.
  - NACK/RTX und GCC nur auf UDP.
  - Interne Go-API für die Saga-Steuerung (P3) mit Token-Prüfschnittstelle.
  - `bench --sfu` misst den echten Weiterleiter. Dazu kommen ein Browser-Test (4 Chromium über ICE-TCP und TURN-TLS)
    und ein Einfrier-Regressionstest (80 ms RTT, 1,5 % Verlust).
  - Bei vollen Ton- bzw. Kachelplätzen verdrängt eine neue Sprecherin den stillsten Platzhalter (vorher kam sie nie dran).
  - Beschreibung: `gjallarhorn/NODE.md`. Saga-Steuerung: P3 (unten).
- **Gjallarhorn P4 – Frame-E2E** (Grundlage für große Gruppenanrufe über Knoten, Feature-Flag `calls.horn`, Standard
  aus): Format `saga-horn/v1` (`docs/HORN-E2E.md`) – AES-128-GCM je codiertem Rahmen im FrameCryptor-Format, ein
  Schlüssel je sendendem Gerät, Ratsche beim Beitritt, neuer Schlüssel beim Verlassen, Wiederholungsschutz,
  Runensiegel. Web-Worker `static/frame-e2e.js` (`RTCRtpScriptTransform`, Rückfall `createEncodedStreams`, keine
  CSP-Lockerung). Die Schlüssel laufen als `fkey`/`fkey-ack` versiegelt im vorhandenen Signalweg, auch über
  Servergrenzen (Fähigkeit `horn` in `features` und `CAPS2`); der Server sieht sie nicht. Testvektoren
  `tests/vectors/horn_e2e.json` mit Gegenproben in Python, Node und im Browser für die Apps.
- **Gjallarhorn P3 – Saga-Steuerung** (Feature-Flag `calls.horn`, Standard aus; Vertrag `docs/APP-API.md`, Abschnitt
  „Anrufe über Gjallarhorn-Knoten“):
  - **Eingebauter Knoten:** saga-media startet `gjallarhorn node` als zweiten Kindprozess auf `HORN_PORT` (Standard
    8443), nur solange das Flag an ist. Steuerung über localhost (`/api/horn/ctl`, Protokoll `horn-ctl/1` mit Challenge,
    Folgenummern und Lastmeldung alle 5 s). Das Image enthält das statische Binär (amd64/arm64, +13 MB).
  - **Medien-Token:** Saga signiert mit einem eigenen Ed25519-Schlüssel (`horn_sign`, verschlüsselt in den
    Einstellungen). Token gelten 60 s, je Knoten und Bein, einmal, mit zufälligen Kennungen statt Konto- oder
    Gerätenamen. Der Knoten prüft lokal.
  - **Gruppenanrufe über den Knoten:** `topo=horn` für Gruppen über der Mesh-Grenze, wenn ein Knoten bereit ist; sonst
    Mesh wie 2.4. Beitritt ohne Mesh-Angebote, Kandidaten mit Token im `join`, frische Token über
    `POST /api/calls/{id}/horn`. Grenzen 12 Video / 50 Ton über TCP, 25 / 100 mit UDP.
  - **Web (Minimum für den Nachweis):** `static/calls.js` baut Ton- und Bild-Leitung zum Knoten auf (ICE-TCP, sonst
    TURN-TLS), verschlüsselt jeden Rahmen mit `saga-horn/v1` und zeigt Kacheln. Die volle Oberfläche folgt mit P5.
  - Datenbank: Tabellen `horn_node`, `horn_pair`, Migration 16 (`chat_call_leg.node`).
- **Gjallarhorn P6–P8 – weitere Knoten, DNS, Admin-Seite „Anrufe“** (Konzept v2 §2.2–2.9, §4.1):
  - **Erweitern:** Admin → Anrufe → „Erweitern“ erzeugt einen einmaligen Kopplungs-Code (32 Byte, 30 min, nur als
    SHA-256 gespeichert, an Name und Saga-Adresse gebunden) und ein vollständig sichtbares Deploy-Skript: Docker Compose
    (Image `ghcr.io/yggdrasil-sh/gjallarhorn:<Version>`, per Digest gepinnt mit `SAGA_HORN_IMAGE_DIGEST`) oder systemd
    unter Debian ohne Docker (Binär aus dem Release, Signatur per openssl geprüft). Der Knoten koppelt sich über
    `GET /api/horn/whoami` und `POST /api/horn/pair`; Saga prüft die Erreichbarkeit zurück (443/TCP je IPv4/IPv6 mit
    signiertem Echo, 443/UDP per STUN). Live-Fortschritt im Dialog bis „Fertig“.
  - **Steuerkanal für gekoppelte Knoten:** nur mit dem registrierten Knotenschlüssel, gesperrte Schlüssel 4410, saga_pub
    beim Knoten angeheftet. Lastmeldung mit Upload/Download, Leeren (Umzugsereignis `call.horn_move` an die Geräte),
    Zielversion = Saga-Version.
  - **Selbst-Update ohne Docker-Socket:** Wächter `gjallarhorn watch` (PID 1 im Knoten-Image bzw. systemd-Dienst) lädt
    das signierte Binär (Ed25519, fester Release-Schlüssel `gjallarhorn/release.pub`), leert den Knoten und startet um
    (allein: erst in einer Zeit ohne Anrufe). **Noch ohne Release-Schlüssel – Updates sind aus, bis er gesetzt ist.**
  - **Entfernen:** Schlüssel sperren, signiertes `remove`, Anrufe ziehen um, DNS-Einträge aufräumen.
  - **Cloudflare-DNS (P7):** Token nur „DNS bearbeiten“ für eine Zone, beim Speichern geprüft (verify, Zone, Probe-Eintrag),
    verschlüsselt gespeichert, maskiert angezeigt. A/AAAA mit grauer Wolke, TTL 120, Kommentar `saga-horn:<id>`,
    nachgeführt bei neuer Adresse. Zertifikate per ACME DNS-01 über Saga (TXT nur für den eigenen Namen, der Token
    verlässt Saga nie), ohne Token per TLS-ALPN-01.
  - **Admin-Seite „Anrufe“ (P8):** Knotentabelle (Status, Weg, Auslastung, Teilnehmende, Version, Teilen, Aktionen),
    Port-Hinweis je Knoten mit Live-Prüfung und Firewall-Beispielen, DNS, Grenzen und Kontingent je verbundenem Server,
    Schalter „Eingebauten Knoten für Anrufe nutzen“ (aus: nur externe Knoten, sonst Mesh), Knopf „Fähigkeiten jetzt
    abgleichen“. Die STUN/TURN-Felder sind von „Allgemein“ in den Abschnitt „1:1-Anrufe (TURN, optional)“ umgezogen.
  - Datenbank: Migration 17 (Kopplung, Port-Prüfung, Zertifikats-/Update-Status), Tabellen `horn_ban`, `horn_log`,
    `horn_share`.

- **Gjallarhorn P9 – Kaskade „Bifröst“ zwischen Knoten** (Flag `calls.horn`, Standard aus; `gjallarhorn/NODE.md`,
  „Kaskade“):
  - Sind Teilnehmende eines Anrufs auf mehreren Knoten, verbindet Saga die Knoten sternförmig (Mittelpunkt = Knoten mit
    den meisten Endpunkten) über **TLS auf 443/TCP**. Beide Knoten prüfen ein von Saga signiertes Kaskaden-Token
    (Anruf, beide Knoten, 60 s, einmalig); die Knotenschlüssel unterschreiben die TLS-Kanalbindung.
  - Ton: je Richtung die lautesten drei; Bild nur auf Nachfrage (Last-N, Sprecher, Schichtwahl der Gegenseite),
    Schlüsselbild-Anforderungen über die Brücke. Pakete bleiben unverändert – Frame-E2E durchgehend, die Brücke sieht
    nur Chiffrat. Über 150 ms Stau verwirft die Brücke Bild bis zum nächsten Schlüsselbild, Ton nie.
  - Kandidaten nennen jetzt auch andere bereite Knoten; der Bonus „Anruf schon hier“ hält die meisten Anrufe auf einem
    Knoten, „anderer Knoten besser“ (Hysterese) wechselt nur bei deutlichem Vorteil.
  - Admin → Anrufe zeigt „Kaskaden: n“ je Knoten. `gjallarhorn bench --sfu --cascade` misst zwei Knoten mit Brücke.
  - **playout-delay auf TCP/TLS:** Der Knoten setzt für Bild die RTP-Kopferweiterung playout-delay (150–400 ms) auf
    Schlüsselbildern – Gegenmaßnahme gegen Einfrieren auch für Apps, die `jitterBufferTarget` nicht setzen können
    (in Chromium nachgewiesen: Jitter-Puffer-Ziel 11 → 138 ms). Nicht auf UDP. `HORN_PLAYOUT_DELAY` (`0` = aus).
- **Gjallarhorn P10 – Gruppenanrufe über Knoten mit anderen Saga-Servern** (Cap `horn`; Vertrag
  `docs/APP-API-FED.md` §7.4; Anleitung `docs/FELDTEST-GJALLARHORN.md`):
  - Das Heim des Anrufs koordiniert. **Teilen an** (je Knoten, Standard aus): Personen verbundener Server verbinden sich
    direkt mit dem Knoten, mit Token des Heims und **Kontingent je Server** (Standard 10 Teilnehmende / 20 Mbit/s je
    Richtung, vom Knoten durchgesetzt). **Teilen aus:** sie kommen über ihren eigenen Knoten (Kaskade). Ohne beides:
    Mesh wie 2.4 mit klarer Meldung.
  - Admin → Föderation je Server: Teilen erlaubt (Erlaubt-Liste), Teilnehmende, Mbit/s, **Notaus** (trennt sofort alle
    Teilnehmenden und Kaskaden des Servers), Hinweis Auftragsverarbeitung. Geteilte Knoten nehmen die Seiten des
    anderen Servers an (CORS); seine Seiten erlauben die Knotenadresse (per `hello`).
  - Server ohne `horn` (2.3/2.4) bleiben still im Mesh. Der Knopf „Fähigkeiten jetzt abgleichen“ macht `horn` sofort
    bekannt.
  - Web-Client: Geräte melden ihre Kennungen an den Knoten im versiegelten Zustand (`state.hp`) – Teilnehmende anderer
    Knoten und Server werden so ohne Serverhilfe zugeordnet.
  - Tests: `tests/foederation_horn.py` (zwei Server mit Knoten: Kaskade, Teilen, Kontingent, Erlaubt-Liste, Notaus,
    Mesh-Rückfall, Server B auf 2.4), Browser `tests/browser_fed_horn.py` (3 + 3 Personen, Kaskade und Teilen).

- **Gjallarhorn P5 – Web-Client für Gruppenanrufe über Knoten** (Flag `calls.horn`, Standard aus; Vertrag
  `docs/APP-API.md`, Abschnitt „Anrufe über Gjallarhorn-Knoten“):
  - **Knotenwahl beim Beitritt:** Das Gerät misst alle Kandidaten parallel (TLS-Aufbau, 5 WSS-Pings, bei `udp_ok` ein
    STUN-Binding; zusammen höchstens 800 ms) und wählt nach Punktzahl mit Bonus „Anruf schon hier“
    (`static/horn.js`). Ist ein Knoten nicht erreichbar, erscheint eine klare Meldung und der nächste Kandidat folgt;
    ohne Knoten neue Versuche mit Backoff.
  - **Live-Umschalten (make-before-break):** Verbindung weg oder Knoten wird geleert → sofort auf den nächsten
    Kandidaten; ein besserer Knoten nur mit Hysterese (≥ 25 % und ≥ 30 ms für 20 s, 60 s Verweildauer, höchstens
    3 Wechsel in 10 min). Frame-Schlüssel bleiben gleich, der Ton spielt über das alte Bein weiter, bis das neue liefert.
    UDP-Weg, wenn der Knoten UDP meldet; bei Verlust über 20 % zurück auf TCP am selben Knoten.
  - **Ereigniskanal am Knoten** (`/api/sfu/leg/{id}/events`): Teilnehmende mit CSRC sofort beim Beitritt (vor dem
    ersten Rahmen, statt Abfrage alle 1,5 s), Belegung der Plätze, Leeren, beendete Beine, Ping. Leeren per
    SIGUSR1/SIGUSR2 oder per Steuerbefehl aus dem Admin-Bereich (P6). Bildschirm-Spur über `screen_mid`.
  - **Oberfläche:** Kachelansicht bis 25 mit Last-N (Desktop 9 bewegt, Handy 4, die übrigen als Standbild mit
    Sprechring), Sprecheransicht, Bildschirm teilen über den Knoten, Weg „UDP/TCP/TLS“ mit Qualitätspunkt,
    Runensiegel mit Erklärung, Teilnehmerliste, Bild-im-Bild wie in 2.4. Vor dem Start ohne bereiten Knoten ein Hinweis
    auf die Mesh-Grenze.
  - **Gastgeber:** `POST /api/calls/{id}/moderate` (stummschalten, entfernen; nur mit Flag `calls.horn`). Entfernte Personen kommen erst nach
    einer neuen Einladung wieder hinein.
  - **Server:** Knotenwahl in `saga/horn_web.py` (fällt der Knoten des Anrufs aus, gehen alle auf den besten
    bereiten); `avoid` in `POST /api/calls/{id}/horn`; Teilnehmerliste mit `hpn` (Kennung je Knoten);
    `config.horn_ready`; Anruf-Objekt `host`.
  - Behoben: Ein Knoten, der sich geleert hatte, kam im Register nicht mehr auf „bereit“ zurück; ein Knoten legte den
    Raum für ein gültiges Token nicht selbst an.

## [2.4.0] – 2026-10-20

Saga 2.4 bringt Videos in den Chat und macht Anrufe erwachsen: Gruppenanrufe, Bildschirm teilen mit Kamera daneben,
Bild-im-Bild und einen laufenden Anruf auf ein anderes Gerät übergeben. Chat-Anhänge und Videos liefert der neue
Medienpfad **saga-media** aus, ohne Python zu belasten; Gelesen-Haken und Online-Status kommen dazu. Nach einer
Testrunde ist Saga außerdem strenger beim Schutz: Fehlversuche bremsen statt Konten zu sperren, Anmeldenamen bleiben
privat, zweite Faktoren ändern sich nur mit Bestätigung, Gravatar gibt es nur mit Einwilligung.
Gleichzeitig erscheinen die [Saga-Apps 2.4](https://github.com/yggdrasil-sh/saga-apps/releases) mit Videos,
Gruppenanrufen, weiterem Gerät im Anruf und Gelesen-Haken. Die Schnittstelle wurde nur erweitert: Apps und Server mit
2.1/2.3 laufen weiter (`MIN_APP` unverändert), neue Funktionen werden per Fähigkeit ausgehandelt und sonst ausgeblendet.
Datenbank: Migrationen 10 (Anrufe), 11 (Videos/Dateien), 12 (Anruf als Raum), 13 (Gelesen/Online) und 14 (Testrunde)
laufen beim Start automatisch; Backups von 2.3 bleiben einspielbar.

### Höhepunkte

- **Videos im Chat.** Poster mit Dauer, sofort abspielen, spulen ohne Komplett-Download, fortsetzbarer Upload bis
  `VIDEO_MAX_MB` (Standard 500 MB) – Ende-zu-Ende-verschlüsselt in Blöcken, auch über Servergrenzen. Gemessen: 1 GB
  hochladen kostet Python 0,06 % eines Kerns und +0 MB (2.3.0 mit 10-MB-Anhängen: 19,8 %, +20 MB), Range-TTFB 1 MiB
  p95 0,8 ms, ein Chat mit 1.000 Video-Nachrichten lädt eine Seite in p95 15,8 ms.
- **Gruppenanrufe und weiteres Gerät.** Gruppenanrufe in Gruppen-, freien und Seiten-Chats (Mesh mit bis zu 4 Geräten
  mit Video bzw. 6 nur mit Ton), lokal und über Servergrenzen; ein laufender Anruf lässt sich ohne Abbruch auf ein
  anderes eigenes Gerät übernehmen oder um ein Begleitgerät ergänzen. 4er-Mesh lokal aufgebaut in 1,1–1,8 s,
  Python-CPU-Spitze ≤ 19 %; über drei Server 3,1 s.
- **Bildschirm, Bild-im-Bild, stabile Verbindung.** Bildschirm als eigene Videospur mit Kamera daneben, Bild-im-Bild im
  Web, Neuverbindung bei Netzwechsel statt Auflegen, Bitrate je Verbindungsweg. Zeigt das Gegenüber das Bild nur klein,
  sinkt die gesendete Bitrate von 1,5 auf 0,29 Mbit/s.
- **Medienpfad saga-media.** Ein kleiner Go-Dienst im Container liefert Chat-Anhänge direkt von der Platte. Gemessen
  (2 Kerne, Median aus drei Läufen): 20 × 10 MB parallel 0,22 statt 0,67 s, Python-CPU dabei 0 statt 0,64 s;
  Range-TTFB 1 MiB p95 1,6 statt 8,8 ms.
- **Gelesen und online.** Haken „wie WhatsApp“, „Gelesen von“ in Gruppen und Online-Status – Ende-zu-Ende-signiert,
  getrennt abschaltbar. 50er-Gruppe mit 49 Lesenden alle 2 s: `/read` mit Bestätigung p95 21–33 ms.
- **Mehr Schutz.** Fehlversuche bremsen nur die angreifende Adresse, Anmeldenamen bleiben privat (Handles), zweiter
  Faktor nur mit Bestätigung, Gravatar nur mit Einwilligung.

### Für alle

#### Videos im Chat

- **Videos mit Poster, Dauer und Streaming.** Im Verlauf erscheint ein Poster mit Dauer (erzeugt das sendende Gerät,
  ≤ 30 KB); Antippen spielt sofort ab, Spulen lädt nur die nötigen Bereiche. Im Web entschlüsselt der Service Worker
  (`static/sw.js`) die Range-Anfragen des Players blockweise – kein Komplett-Download, Schlüssel nur im Speicher.
  Herunterladen als entschlüsselte Datei, als Strom. „Optimiert 720p“ im Web, wo der Browser MP4 aufnehmen kann (vom
  Handy Standard), sonst Original mit Hinweis. In der Chatliste nur Poster, kein Player.
- **Fortsetzbarer Upload:** Bricht ein Upload ab, geht er ab der Abbruchstelle weiter. Höchstens 2 Uploads gleichzeitig,
  offene verfallen nach 24 h; laufende Uploads zählen beim Speicher mit.
- **Über Servergrenzen:** Große Videos holt der eigene Server nur bereichsweise beim Server des Absenders. Mit Servern auf
  Saga 2.3 gehen nur Anhänge bis `ATTACH_MAX_MB` (Hinweis statt verlorener Nachricht). Ältere Apps zeigen große Videos
  als Anhang („Mit Saga 2.4 öffnen“).
- **Videos, die der Browser nicht kann:** Statt eines stummen Players bei 0:00 erscheint „Kann im Browser nicht abgespielt
  werden – herunterladen“, im Verlauf ein Platzhalter mit Dateiname statt einer leeren Fläche (das Vorschaubild fehlt, weil
  das sendende Gerät das Video nicht dekodieren konnte). „Auf 720p verkleinern“ bietet Saga nur an, wenn dieser Browser
  das Video dekodieren und als MP4 aufnehmen kann, sonst steht ehrlich „Original“ da.

#### Anrufe

- **Stabile Verbindung:** Anrufe können während des Gesprächs neu ausgehandelt werden („Perfect Negotiation“). Bricht die
  Verbindung ab oder wechselt das Netz (WLAN ↔ Mobilfunk), baut Saga sie per ICE-Restart neu auf, statt aufzulegen
  (bis zu 3 Versuche, 20 s Frist). Mit Gegenstellen auf 2.1/2.3 läuft alles wie bisher.
- **Bitrate je Verbindungsweg:** Kamera, Bildschirm und Ton passen sich an, je nachdem ob die Verbindung direkt, über
  TURN per UDP oder über TURN per TCP/TLS (Port 443) läuft. Zeigt das Gegenüber das Bild nur klein (Bild-im-Bild, Tab im
  Hintergrund), sendet Saga nur noch ≤ 300 kbit/s (gemessen 1,5 Mbit/s → 0,29 Mbit/s).
- **Bild-im-Bild im Web:** Knopf im Anruffenster (Chrome/Edge: eigenes kleines Fenster mit Stumm, Zurück und Auflegen;
  sonst Video-Bild-im-Bild). Ohne Browser-Unterstützung (z. B. Firefox) erscheint der Knopf nicht.
- **Bildschirm als eigene Videospur:** Zwischen 2.4-Geräten läuft der geteilte Bildschirm über eine zweite Videospur
  (Neuverhandlung), die Kamera läuft daneben gedrosselt weiter. Das Gegenüber sieht den Bildschirm groß und die Kamera
  klein und kann die Ansicht tauschen. Gegenstellen auf 2.1/2.3 (und ohne `screen2`) bekommen wie bisher den Bildschirm
  statt der Kamera.
- **Systemton:** Wo der Browser ihn liefert (Chrome/Edge: Ton des Tabs, unter Windows/ChromeOS auch der ganze Systemton),
  wird er in die Tonspur gemischt.
- **Datenschutz beim Teilen:** Vor dem ersten Teilen ein Hinweis, dass auch Benachrichtigungen und andere Fenster
  sichtbar sind (abschaltbar je Gerät). Während des Teilens eine rote Leiste „Du teilst deinen Bildschirm“ mit „Beenden“,
  beim Gegenüber „… teilt den Bildschirm“.
- **Bitrate beim Teilen:** Bildschirm je Verbindungsweg 2,5 / 1,8 / 1,2 Mbit/s, höchstens 15 fps (über TURN per TCP
  8 fps), Auflösung bleibt erhalten („maintain-resolution“, Text bleibt lesbar). Die Kamera daneben sendet ≤ 300 kbit/s,
  über TURN per TCP pausiert sie.
- **Weiteres Gerät:** Läuft ein Anruf auf einem Gerät, zeigen die anderen eigenen Geräte „Anruf läuft auf …“ mit
  **Hinzufügen** (Begleitgerät: ohne Mikrofon und Ton, z. B. zum Bildschirmteilen vom Laptop) und **Hierher übernehmen**
  (eigener Knopf, ohne Abbruch: das neue Gerät verbindet sich, übernimmt Ton und Mikrofon, das alte geht; klappt es nicht
  in 15 s, bleibt das alte). Auch über Servergrenzen.
- **Gruppenanrufe:** In Gruppen-, freien und Seiten-Chats, lokal und über Servergrenzen. Klingelt bei allen Mitgliedern
  mit Saga 2.4 (in Chats mit mehr als 8 Mitgliedern nur still), jederzeit beitreten und verlassen; der Anruf läuft,
  solange mindestens zwei dabei sind. Mesh mit höchstens **4 Geräten mit Video bzw. 6 nur mit Ton** (eigene
  Zusatzgeräte zählen mit, der Admin kann nur senken). Kachelansicht, Sprecher hervorgehoben, Bitrate je Strom ab drei
  Geräten (600/500/350 kbit/s je Weg), Bildschirm teilen in der Gruppe, Chat-Eintrag mit Teilnehmerzahl, verpasst für
  Eingeladene.
- **Ende-zu-Ende:** je Gerätepaar eine eigene, DTLS-SRTP-verschlüsselte Verbindung; Signale je Zielgerät versiegelt und
  signiert. Jedes neue Gerät wird gegen die signierte Geräteliste seiner Person geprüft (unbekannte werden abgelehnt) und
  mit Namen angezeigt. Der Server sieht keine SDP, Medien laufen nie durch Saga.
- **Kompatibilität:** Der Gruppenanruf-Knopf erscheint nur, wenn alle beteiligten Server die neue Fähigkeit `calls3`
  melden; Geräte ohne `multi` (2.1/2.3, E0) werden nicht eingeladen und sehen „braucht Saga 2.4“. 2.3-Apps und -Server
  bleiben bei 1:1.
- **Nach der Testrunde:** Nach „Hierher übernehmen“ hat die Kamera den Zustand des abgebenden Geräts; die Angerufene sieht
  ihr Eigenbild sofort; ein neuer Tab zum Annehmen fragt den Schlüsselzustand einmal nach, bevor er „Chats einrichten“
  zeigt (dort angelegte Schlüssel überschrieben nie eine bestehende Identität, der Server lehnt das ab).
- **Mehrere Geräte im Anruf:** Die Kopplungsanfrage eines neuen Geräts erscheint über dem Vollbild-Anruf und schließt
  sich, wenn ein anderes Gerät sie erledigt hat. Behoben: „Hinzufügen“ hing bei „Verbinde …“ und „Hierher übernehmen“
  scheiterte, wenn das Gerät erst während des Anrufs gekoppelt wurde – die Gegenstelle kannte seine Schlüssel noch nicht
  und holt sie jetzt einmal frisch (neue Reihe `browser_geraete.js`: alle Geräte in einem Browser wie bei der Testrunde).
- **Gruppenanruf:** „Klingelt bei den anderen …“ bzw. „Verbinde …“ verschwinden, sobald Medien fließen; den Zustand je
  Verbindung zeigen die Kacheln.

#### Gelesen-Bestätigungen und Online-Status

- **Gelesen-Haken „wie WhatsApp“:** Eigene Nachrichten zeigen ⏳ wird zugestellt → ✓ zugestellt (beim Server bzw. Heim)
  → ✓✓ gelesen. In Gruppen zählt der Haken mit, ein Klick zeigt **„Gelesen von“** mit Namen und Zeit.
- **Online-Status:** Im Kopf eines Direkt-Chats „online“ bzw. „zuletzt online vor …“ (auf 5 Minuten gerundet), in der
  Chatliste ein grüner Ring. Nur für Personen, mit denen man einen angenommenen Chat hat; abgeleitet aus der
  Live-Verbindung (offline 30 s nach der letzten, `CHAT_OFFLINE_GRACE`). Kein Verlauf, nichts davon im Admin-Bereich.
- **Datenschutz:** Beides ist standardmäßig an und unter „Chats & Geräte“ → Datenschutz getrennt abschaltbar
  („Gelesen-Bestätigungen senden“, „Online-Status zeigen“) – gegenseitig: Wer etwas abschaltet, sieht es bei anderen
  auch nicht. Abschalten zieht schon gesendete Bestätigungen zurück, auch auf anderen Servern.
- **Ende-zu-Ende:** Eine Bestätigung („gelesen bis Nachricht X“) wird vom Gerät signiert und je Mitglied für dessen
  Backup-Schlüssel versiegelt (`saga-chat/v1|v2 receipt`, Testvektoren `tests/vectors/receipts.json`). Der Server
  speichert nur die neueste je Person, sieht nur, *dass* es eine gibt, und kann kein „gelesen“ erfinden. Der Lese-Zeiger
  für den Ungelesen-Zähler bleibt wie bisher auf dem eigenen Server.
- **Über Servergrenzen:** neue Fähigkeiten `receipts` und `online` (Protokoll 2). Bestätigungen gehen versiegelt über
  das Heim des Chats (in der Reihenfolge der Nachrichten), der Online-Status als kurzes Abo (90 s) beim Server der
  Person. Mit 2.3-Servern wird nichts davon gesendet. Ersetzt die frühere Regel „Gelesen bleibt lokal“.

#### Privatsphäre und Anmeldung

- **Anmeldenamen bleiben privat:** Andere Konten sehen nie den Anmeldenamen – Personensuche (Teilen, Chats), Freigaben,
  „Mit mir geteilt“, Kommentare, Benachrichtigungen, Anwesenheit, Versionen und Seitensuche liefern Anzeigename,
  öffentlichen Namen oder einen pseudonymen Handle (`~` + 16 Zeichen). Freigeben, Chat beginnen und Mitglied hinzufügen
  nehmen den Handle oder den Benutzernamen. Mitglieder eines gemeinsamen Chats/Anrufs bekommen den
  Anmeldenamen technisch weiter (Schlüssel und Signaturen von Chats v1 hängen daran), die Weboberfläche zeigt ihn nicht.
- **Gleichnamige Personen unterscheidbar:** Personensuche (Teilen, Chats) und Chat-Mitglieder liefern `sub` – den
  öffentlichen Namen bzw. den Handle `~…`, nie den Anmeldenamen; das Web zeigt ihn in der Unterzeile. Der
  Mitglieder-Dialog einer Gruppe zeigt keine Anmeldenamen mehr; Personen anderer Server erklärt er in lokalen Gruppen
  („nur in neuen Gruppen“).
- **Nextcloud-Dateien in Seiten:** Mitlesende Konten sehen als Autor eines eingefügten Nextcloud-Blocks den Handle
  statt des Anmeldenamens (auch in `GET /api/notes/{id}/nc`); Speichern, Vorschau (`?a=`) und Download nehmen den Handle
  an, gespeichert bleibt der echte Autor mit seiner Signatur.
- **Gruppen mit Personen anderer Server:** Anlegen ging mit Handles aus der Personensuche nicht mehr (400 „Mit diesem
  Konto ist kein Chat möglich“) – lokale Mitglieder als Handle und `handles` anderer Server lassen sich wieder gemischt
  angeben.
- **Personensuche `name@server`:** Hat die Person keinen öffentlichen Namen, sucht Saga zusätzlich im Adressbuch genau
  dieses Servers nach Anzeigenamen bzw. Handle (lokal, kein Anmeldename geht nach außen).
- **Öffentlicher Name:** Der eigene Anmeldename ist als öffentlicher Name nicht mehr wählbar (er stünde in
  `/@name`-Adressen und bei anderen Saga-Servern). Saga hat den öffentlichen Namen nie mit dem Anmeldenamen vorbelegt;
  wer ihn früher selbst so gewählt hat, behält ihn (keine Zwangsumstellung, alte Links gehen weiter) und sieht in den
  Einstellungen einen Hinweis mit der Möglichkeit, ihn zu ändern.
- **Gravatar nur mit Einwilligung:** Neue Konten beginnen mit „Gravatar verwenden“ **aus**; beim Einschalten erklärt Saga,
  dass ein Hash der E-Mail-Adresse an Gravatar geht. Auch bestehende Konten starten nach dem Update mit „aus“ (siehe
  „Update von 2.3“).
- **Zweiter Faktor entfernen** (Authenticator-App, Passkey) verlangt jetzt immer eine ausdrückliche Bestätigung mit
  Passwort, Code oder Passkey (höchstens 5 Minuten alt) – eine frische Anmeldung allein reicht nicht mehr.
- **Zweiter Faktor hinzufügen und neue Wiederherstellungscodes** verlangen dieselbe Bestätigung, sobald das Konto schon
  einen Passkey oder eine Authenticator-App hat; nur die Ersteinrichtung geht weiter mit frischer Anmeldung. Ein Faktor,
  der erst nach der Anmeldung dieser Sitzung hinzukam, bestätigt nichts und wird im Bestätigungsdialog nicht angeboten.
  Bisher konnte eine frisch gekaperte Sitzung einen eigenen Passkey anlegen, sich damit bestätigen und danach
  Authenticator-App und Passkeys des Kontos entfernen. Sitzungen merken sich dafür ihren Beginn (`since`).

#### Seiten, Editor und Oberfläche

- **Versionen beim Bearbeiten:** Saga sichert beim Speichern höchstens alle 5 (statt 10) Minuten, dazu den aktuellen
  Stand nach 2 Minuten Pause und beim Verlassen der Seite (`POST /api/notes/{id}/versions/checkpoint`, gedrosselt, keine
  doppelten Fassungen). „Diese Version wiederherstellen“ fragt nach und sichert den aktuellen Stand immer vorher – steht
  er schon im Verlauf, ohne Kopie; für Nicht-Eigentümer gilt dabei dieselbe Drossel wie beim Speichern (ein eigener
  Sicherungspunkt je Seite und Minute, ein weiterer ersetzt ihn). So kann niemand mit „Bearbeiten“ durch wiederholtes
  Wiederherstellen ältere Fassungen des Eigentümers oder Dritter aus dem Verlauf verdrängen.
- **Export, Versionsvorschau, Suche:** Markdown-Export behält die Adresse von Einbettungen (YouTube), zeigt eingebettete
  Datenbanken mit Titel und Kalender/Aufgaben als Hinweis; Vorschau und Suchausschnitt zeigen keine internen Werte
  („month“, „task“, Ansichtsnamen) und Dateinamen nur einmal. Migration 14 leitet den Suchtext betroffener Seiten neu ab.
- **Editor:** Ein Klick unter den letzten Block setzt den Cursor in den leeren letzten Absatz bzw. legt einen neuen an
  (Getipptes ging verloren). Schnell getipptes „/Hinweis⏎“ wählt den Block, auch wenn das Menü noch nicht gefiltert hat.
  Aus einem Zitat kommt man per Enter in leerer Zeile, Enter in „Quelle“ oder Pfeil nach unten heraus.
- **Oberfläche:** Die klebende Reiterleiste der Einstellungen schließt oben bündig ab; der Datumswähler einer Aufgabe in
  der Seitenleiste öffnet an der Aufgabe.
- **Kleinigkeiten:** Einstellungs-Reiter brechen auf breiten Bildschirmen um (keine versteckten Reiter mehr), Thai-Titel
  ohne Sperrschrift, klebende Reiterleiste deckt den Inhalt ab; „Kalender verbinden …“ öffnet den Reiter „Kalender“;
  „1 Termin“ in der Einzahl, „… sind jetzt dabei“ bei mehreren; Dateien unter 1 KiB in Byte; Anmeldeseite setzt nach einem
  Fehler den Fokus ins Passwortfeld und erklärt „Passwort vergessen?“; Sprachwahl mit fester Breite; einheitlicher
  Platzhalter in Spalten; Nextcloud: Tippfehler in der Adresse heißen „Adresse ungültig“ statt „Ziel nicht erreichbar“.

#### Behoben

- **Chats über Servergrenzen: eigene Nachricht blieb auf „⏳ wird zugestellt“.** Liegt der Direkt-Chat auf dem anderen
  Server (Heim), legt der eigene Server die Nachricht zuerst als ausstehend an und meldet sie sofort an die Geräte. Bestätigte
  das Heim gleich, kam dafür kein Live-Ereignis mehr – Web und Apps zeigten die längst zugestellte Nachricht bis zum
  Neuladen als ausstehend. Der Server meldet die Bestätigung jetzt (ebenso „nicht zugestellt“ und eine wegen geänderter
  Empfänger verworfene Kopie); die Weboberfläche übernimmt zusätzlich die Bestätigung aus der Antwort beim Senden.

### Für Admins

#### Medienpfad „saga-media“

- **Chat-Anhänge ohne Python.** Im Container steht vor Saga der kleine Dienst `saga-media` (Go, nur Standardbibliothek,
  statisch gelinkt, Quelltext in `media/`). Er lauscht auf dem bisherigen Port, leitet alles an Saga weiter (Streaming,
  Live-Kanal ungepuffert) und liefert `GET/HEAD /api/chat/files/{fid}` selbst von der Platte: Range (206/416), `sendfile`,
  `ETag` = Kennung, If-None-Match (304), If-Range, dieselben Sicherheitsköpfe wie bisher. Gemessen (`tools/perf/media.py`,
  Median aus drei Läufen, 2 Kerne): 20 × 10 MB parallel 0,22 statt 0,67 s, Python-CPU dabei 0 statt 0,64 s; 20 Ströme
  je 2 Mbit/s Python 0,3 % (Ruhewert) statt 2,9 % eines Kerns und kein RAM-Zuwachs statt +14 MB, saga-media 0,1 % und
  8 MB; Range-TTFB 1 MiB p95 1,6 statt 8,8 ms. Image etwa +7,5 MB.
- **Berechtigung bleibt in Saga:** `saga-media` fragt je Datei und Sitzung einmal `/internal/media/authz` (nur mit
  einem beim Start erzeugten Geheimnis, von außen gesperrt) und merkt sich die Erlaubnis höchstens 60 s
  (`MEDIA_AUTHZ_TTL`). Entzug wirkt also spätestens nach einer Minute, eine gelöschte Datei sofort. Ablehnungen beantwortet
  wie bisher Saga selbst (401/404, föderierte Anhänge werden dort nachgeholt).
- **Sicher davor:** Client-Adresse nach denselben Regeln wie bisher (`TRUSTED_PROXIES`; eingeschleuste
  X-Forwarded-For/-Proto zählen nicht), nur echte Dateien direkt im Blob-Verzeichnis (kein Traversal, keine Symlinks –
  das prüft jetzt auch Python), Grenzen für Verbindungen und gleichzeitige Downloads, Zeitlimits gegen Slowloris
  (Kopfzeilen 15 s, stockende Uploads/Downloads `MEDIA_STALL_TIMEOUT`). Upload-Grenzen unverändert.
- **Grenze je Client-Adresse.** Höchstens `MEDIA_MAX_CONNS_IP` (Standard 256) offene Verbindungen je Adresse
  (vertraute Proxys ausgenommen); Keep-Alive-Leerlauf ohne Anmeldedaten endet nach `MEDIA_IDLE_UNAUTH` (10 s). Ist die
  Grenze erreicht, schließt saga-media die am längsten ruhende Verbindung, statt neue warten zu lassen – eine einzelne
  Adresse kann mit ruhenden Verbindungen niemanden mehr aussperren. Als angemeldet (75 s Leerlauf) gilt eine
  Verbindung erst, wenn Saga die Anmeldung bestätigt hat (Berechtigung für Datei/Upload bzw. interner Antwortkopf
  `X-Saga-Media-Auth`, den saga-media entfernt) – ein beliebiger Cookie- oder Authorization-Kopf reicht nicht.
- **Container:** mehrstufiger Build (Go auf der Build-Plattform für amd64/arm64), `tini` als Init; `saga-media` startet
  und überwacht Saga (intern `127.0.0.1:8079`, `MEDIA_INTERNAL_PORT`) und endet mit ihm. Kein neuer Port, Healthcheck
  unverändert. `SAGA_MEDIA=off` startet wie bis 2.3.
- **Ohne Container:** `MEDIA_ACCEL=nginx|caddy` – der Reverse Proxy liefert per `X-Accel-Redirect` (Beispiele in der
  Anleitung, „Medienpfad“, mit nginx 1.24 und Caddy 2.8 geprüft). Sonst liefert Python wie bisher, aber höchstens
  `MEDIA_PY_STREAMS` (Standard 4) gleichzeitig; der Admin-Bereich zeigt unter „Medien“, welcher Weg aktiv ist.
- Föderation (`/fed/v2/chat/files`) unverändert; Teilbereiche mit Signatur folgen mit den Videos (siehe „Technisch“).

#### Chats und Videos

- **Grenzen im Admin:** „Größtes Video im Chat“ (`VIDEO_MAX_MB`, Standard 500) und „Speicher für Chat-Anhänge je Konto“
  (`CHAT_QUOTA_MB`, Standard jetzt 4096 statt 1024); laufende Uploads zählen mit.

#### Anrufe

- **Geräte je Anruf:** Admin → Allgemein → Anrufe „Höchstens Geräte je Anruf mit Video“ bzw. „… nur mit Ton“ – die
  Mesh-Grenzen 4 bzw. 6 lassen sich nur senken (mindestens 2 = 1:1).
- **Fremder STUN-Server:** Admin → Allgemein → Anrufe weist darauf hin, dass der eingetragene STUN-Server (ab Werk
  `stun.nextcloud.com:443`, bleibt der Standard) die IP-Adressen der Anrufenden sieht, mit Link zur Anleitung „TURN über
  Port 443“ (eigener coturn). Alle Geheimnis-Felder im Admin-Bereich sind Passwortfelder, leer = unverändert, der Wert
  steht nie im HTML.

#### Anmeldung und Personensuche

- **Fehlversuche bremsen statt Konten sperren:** Nach 8 falschen Passwörtern für ein Konto von derselben Adresse wartet
  *diese Adresse* 2, 4, 8 … s (höchstens 15 min), ebenso nach 30 Fehlversuchen einer Adresse. Andere Adressen sind
  nicht betroffen – ein Fremder kann niemanden mehr aussperren. Erst nach 100 Fehlversuchen von mindestens 10 Adressen in
  einer Stunde sperrt Saga die Passwort-Anmeldung eine Stunde lang (neue Tabelle `login_lock`): Die Person bekommt eine
  Benachrichtigung, der Admin sieht die Sperre auf der Übersicht, in der Benutzerliste und beim Konto und hebt sie mit
  „Sperre aufheben“ auf; „Passwort setzen“ hebt sie ebenfalls auf. Passkeys und offene Sitzungen gehen weiter, LDAP wird
  genauso gebremst, SSO ist nicht betroffen. Am Admin-Port gilt zusätzlich wie bis 2.3 eine feste Grenze je Konto über
  alle Adressen (40 Fehlversuche in 15 Minuten, dann wartet jede Adresse) – dort gibt es keine harte Sperre, und ohne
  diese Grenze ließe sich ein Admin-Konto verteilt unbegrenzt raten. IPv6-Adressen zählen in allen Bremsen je /64-Netz
  (Anmeldung, Admin-Port, zweiter Faktor, Passkeys, Registrierung und `MEDIA_MAX_CONNS_IP` in saga-media) – ein
  Anschluss hat meist ein ganzes /64, jede Adresse einzeln zu zählen hieße praktisch keine Grenze. Die harte Sperre
  bleibt bei 100 Fehlversuchen von 10 Adressen: Wer viele Adressen hat (Tor, Botnetz), sperrt auch bei einer höheren
  Schwelle, während sie verteiltes Raten länger erlauben würde; Passkeys und offene Sitzungen gehen während der Sperre
  weiter, Person und Admin werden informiert. Ein Restore spielt keine laufenden Sperren der aktuellen Datenbank mit
  zurück (`login_lock` gehört zur alten Datenbank).
- **Kein Orakel für Anmeldenamen:** Die Personensuche findet andere Konten nicht mehr über Teile des Anmeldenamens,
  sondern über Anzeigenamen, öffentlichen Namen und den genauen Handle; den **genauen** Anmeldenamen nur, solange der
  neue Admin-Schalter „Suche nach Anmeldename“ (Allgemein → Teilen) an ist – Standard an, weil Konten ohne eigenen
  Anzeigenamen sonst nicht mehr auffindbar wären. Ist er aus, nehmen auch Teilen und „Chat beginnen“ einen
  Anmeldenamen nur noch für Personen, mit denen man schon chattet bzw. an die man schon geteilt hat, und antworten
  sonst wie für ein unbekanntes Konto. `GET /api/chat/keys` liefert nur noch Schlüssel des eigenen Kontos und von
  Chat-Partnern (auch per Handle), unbekannte und fremde Konten fehlen gleichermaßen.

### Technisch (für App-Teams und Betreiber)

#### Anrufe: saga-call v3, Bildschirm v2, Mehrgeräte und Gruppen (Etappen E0, E3, E4/E5)

- **saga-call v3 (E0):** Neuverhandlung während des Anrufs („Perfect Negotiation“) und ICE-Restart. Signatur und
  Verschlüsselung bleiben unverändert, nur der signierte Inhalt hat neue Felder (`pc`, `caps`, `n`, `state.pip/want/path`,
  `bye`). Mit Gegenstellen auf 2.1/2.3 läuft alles wie bisher.
- **Server (E0):** `GET /api/calls/config` meldet `body_v`, `features` und `limits`; mehrere offer/answer je Anruf in beide
  Richtungen, lokal und föderiert, mit einem Signal-Budget, das mit der Gesprächsdauer wächst (statt fester 600).
  Migration 10 (Grundgerüst für Räume): `chat_call.v`, `topo`, `kind`, `caps_a`, `caps_b`.
- **Server (E3):** `GET /api/calls/config` meldet `features: screen2`; sonst keine Änderung (keine Medien durch den Server,
  3 Signale je Start bzw. Stopp).
- **Anruf als Raum (E4/E5):** Jeder Anruf hat Endpunkte je Gerät (neue Tabellen `chat_call_leg`, `chat_call_invite`,
  Migration 12). Ein 1:1-Anruf ist der Raum mit zwei Geräten und läuft wie bisher; mit 2.1/2.3-Gegenstellen bleibt er 1:1.
- **Server (E4/E5):** neue Endpunkte `POST /api/calls/{id}/join`, `/leave`, `/invite`, `PUT /api/calls/caps`; `POST /api/calls`
  startet in Gruppen-Chats einen Gruppenanruf; Anruf-Objekte mit `kind`, `legs`, `joinable`, `rv` (Stand der Liste, auch in `call.roster`) und bei Gruppen `silent`/`members` (auch in `/api/calls/active`); Live `call.roster` und
  `call.invite`; Push-Art `call.group`; `features` `multi` und `group`. Föderation: Cap `calls3`, Aufträge `join`,
  `leave`, `roster`, `invite`; den Anruf führt der Server der anrufenden Person bzw. das Heim des Chats, Signale laufen
  sternförmig über ihn (auch zwischen Servern, die nicht direkt verbunden sind). Sicherung: ältere Backups ohne die neuen
  Tabellen bleiben einspielbar.
- **App-Vertrag:** `docs/APP-API.md`, Abschnitte „Anrufe: saga-call v3“ (Föderation: `APP-API-FED.md` §7.1), „Anrufe:
  Bildschirm v2“ (mid-Zuordnung über `tracks`, `msid`, `screen_mid`) und „Anrufe: Mehrgeräte und Gruppen“ (mit
  Beispielen), `docs/APP-API-FED.md` Abschnitt 7.2. Bewertung Frame-E2E für die SFU in 2.5 (Spike,
  `tools/perf/spike_frame_e2e.js`): im Web machbar.

#### Videos: Format `saga-file/v2`, Upload und Föderation (Etappe E2)

- **Neues Format `saga-file/v2`** für Videos über `ATTACH_MAX_MB`: 256-KiB-Blöcke XChaCha20-Poly1305 mit Blocknummer,
  Blockzahl, Größe und Endmarke im AD (gegen Vertauschen und Abschneiden). Testvektoren für alle Plattformen:
  `tests/vectors/file_v2.json`. Kleinere Videos und alle bisherigen Anhänge bleiben unverändert (v1); ältere Apps zeigen
  große Videos als Anhang („Mit Saga 2.4 öffnen“).
- **Fortsetzbarer Upload** (`POST /api/chats/{id}/uploads`, `PUT`/`HEAD`/`DELETE /api/chat/uploads/{id}`): saga-media
  schreibt die Teile direkt auf die Platte, Python legt nur Metadaten und Kontingent an. Abbruch → weiter ab
  `Upload-Offset`. Höchstens 2 Uploads gleichzeitig, offene verfallen nach 24 h.
- **Grenzen:** `/api/chat/me` meldet `limits.file2`, `video_max`, `upload_part`, `quota`, `used`.
- **Medien-Token** für lange Wiedergaben (`POST /api/chat/files/{id}/token`, Kopf `X-Saga-Media-Token`): an Datei und
  Anmeldung gebunden, 15 min; Entzug wirkt spätestens nach 60 s.
- **Medien-Token undurchsichtig (Testrunde):** `POST /api/chat/files/{fid}/token` liefert `m2.…` (AES-GCM), aus dem sich
  weder Benutzername noch Datei-Kennung lesen lassen. Apps behandeln das Token schon als beliebige Zeichenkette;
  `m1`-Token laufen nach höchstens 15 Minuten aus.
- **Über Servergrenzen** (Fähigkeit `chatfile2`): Der eigene Server holt große Videos nur bereichsweise beim Server des
  Absenders – signiert je Teil (höchstens 4 MiB, Bereich in der signierten Anfrage), lückenhafte Zwischenkopie mit
  1-MiB-Bitmap, Anfang und Ende vorab. saga-media liefert und holt die Bytes selbst, Python prüft und signiert nur. Mit
  Servern auf Saga 2.3 gehen nur Anhänge bis `ATTACH_MAX_MB` (Hinweis statt verlorener Nachricht; Chat-Objekt `big_files`).
- **Migration 11:** `chat_file.fmt`, `chat_file_fed.have`, Index `chat_file(uid)`; neue Tabelle `chat_upload`.
- **App-Vertrag:** `docs/APP-API.md`, Abschnitt „Videos im Chat“ (mit Beispielen für AVAssetResourceLoader, Media3
  DataSource, GTK/GStreamer), Föderation `docs/APP-API-FED.md` §6.5a.
- **Gemessen** (nativ, 2 Kerne; `tools/perf/video.py`, `gen.py` Stufe V, `tests/foederation_videos.py`):
  Upload 1 GB bei 400 Mbit/s: Python 0,06 % eines Kerns, +0 MB (vorher 2.3.0 mit 10-MB-Anhängen: 19,8 %, +20 MB,
  4,25 CPU-s je GB), saga-media 4,5 % und 14 MB. Range-TTFB 1 MiB p95 0,8 ms (mit Medien-Token ebenso). Chat mit 1.000
  Video-Nachrichten: Seite (50) p95 15,8 ms (vorher 1.000 Bild-Nachrichten 19,2 ms), Antwort 30,5 statt 29,0 KB (die
  Video-Metadaten im Ciphertext). Über Servergrenzen 1 MiB kalt 21–57 ms, danach 4–16 ms. Image +3,4 MB
  (unkomprimiert 107,7 → 111,1 MB), Start bis `/healthz` 3,0 statt 2,9 s, RAM nach dem Start 83 statt 78 MiB.

#### Gelesen-Bestätigungen und Online-Status

- **Last:** Bestätigungen werden je Chat gebündelt (höchstens ein Live-Ereignis je 2 s; mehr als 8 auf einmal nur als
  Hinweis, den offene Chats abrufen), Clients senden höchstens eine je Chat und 2 s; der Online-Status geht nur an Konten,
  die die Person gerade sichtbar haben (Abo). Messung gegen E0 (Stufe S, 2-Kern-Container, Median aus 3 Läufen):
  bestehende Endpunkte unverändert im Rauschen, alle Grenzwerte eingehalten. 50er-Gruppe, 49 Lesende alle 2 s (490
  Bestätigungen mit je 49 Siegeln in 25 s): `/read` mit Bestätigung p95 21–33 ms (E0 ohne Wirkung 14 ms), Python-CPU
  14 % statt 9 %, je Konto ≤ 12 Live-Ereignisse, Live-Kanal aller 50 zusammen ~210 KB, Speicher unverändert;
  Online-Abo für 49 Personen p95 24–36 ms. Neue Grenzwerte in `tools/perf/limits.json`.
- **Server:** `GET/PUT /api/chat/privacy`, `POST /api/chats/{id}/read` mit `receipt`, `GET /api/chats/{id}/receipts`,
  `POST /api/chat/presence`, Live-Ereignisse `chat.receipt`, `chat.presence`, `chat.privacy`; `chat.message` meldet bei
  der Zustellbestätigung des Heims auch `fseq`. **Migration 13**: Tabellen
  `chat_receipt` und `chat_seen`; beim Zurückspielen älterer Backups werden sie neu angelegt. Kontolöschung und
  persönliches Backup berücksichtigen sie.
- **App-Vertrag:** `docs/APP-API.md`, Abschnitt „Gelesen-Bestätigungen und Online-Status“ (Föderation:
  `APP-API-FED.md` §11). `MIN_APP` unverändert – ältere Apps sehen einfach keine Haken.

#### Datenbank

- **Migrationen 10–14:** 10 Anrufe (`chat_call.v`, `topo`, `kind`, `caps_a`, `caps_b`), 11 Videos/Dateien
  (`chat_file.fmt`, `chat_file_fed.have`, Index `chat_file(uid)`, `chat_upload`), 12 Anruf als Raum (`chat_call.lead`, `chat_device.caps`,
  Tabellen `chat_call_leg`, `chat_call_invite`),
  13 Gelesen/Online (`chat_receipt`, `chat_seen`), 14 Testrunde (Gravatar für alle Konten aus, Suchtext betroffener
  Seiten neu; dazu `login_lock`). Vor der Migration legt Saga wie immer ein Backup an (`…-vor-schema-14.dump`); 2.3
  startet danach nicht mehr auf dieser Datenbank. Beim Zurückspielen älterer Backups entfernt Saga die neuen Tabellen
  vorher und legt sie neu an.
- **Server meldet 2.4.0** (`VERSION`).

#### Tests und Messung

- **Anrufe:** `tools/perf/bench.py` misst die Anruf-Signalisierung, `tests/browser_calls.js` den Anrufaufbau;
  `tests/browser_screen2.js` (Bildschirm-Attrappe im headless Chromium, gegen E0- und 2.3-Client); `tests/anrufe_raum.py`,
  `tests/browser_raum.js` (4 Geräte, Übernahme, 4er-Mesh, 2.3-Client), `tests/foederation_anrufe_raum.py` (drei Server,
  auch mit 2.3-Server; mit `BJS=1` im Browser). 4er-Mesh lokal: Aufbau 1,1–1,8 s, Python-CPU-Spitze ≤ 19 % (nie über
  20 %); über drei Server 3,1 s; Signal B → Heim → C 69 ms.
- **Medienpfad:** neue Reihe `tests/medienpfad.py` (Range-Varianten, 304/ETag, Rechteentzug, gelöschte Dateien, Traversal,
  `/internal` gesperrt, SSE durch den Proxy, X-Forwarded-For, Upload-Grenzen, Slowloris, Python-Grenze, nginx/Caddy),
  Go-Tests in `media/`. Die Testinstanz von `tools/stack-remote.sh` läuft hinter `saga-media`. Messstufe `MEDIA` in
  `tools/perf/run.sh`, Grenzwerte in `tools/perf/limits.json`.
- **Videos:** `tests/datei_v2_vektoren.py`, `tests/videos.py` (mit und ohne saga-media), `tests/foederation_videos.py`
  (gemischt, nur Python, gegen 2.3.0), `tests/browser_videos.js` (Upload mit Abbruch, Abspielen, Spulen, Herunterladen),
  Go-Tests in `media/`.
- **Gelesen/Online:** `chat_gelesen`, `foederation_gelesen` (auch gegen 2.3 als Gegenstelle), `browser_gelesen.js`,
  `browser_fed_chat.js` erweitert, Vektoren in `chat_v2_vektoren`; Messung `bench.py` Phase `receipts`.
- **Mehrere Geräte:** neue Reihe `browser_geraete.js` (alle Geräte in einem Browser wie bei der Testrunde).

### Update von 2.3

- **Update:** Mit `SAGA_VERSION=2` in der `.env` reicht `docker compose pull && docker compose up -d`.
- **Backup:** 2.4 hebt die Datenbank von Schema-Stand 9 auf 14 (Migrationen 10–14) und legt vorher automatisch ein
  Backup an (`/data/backups/saga-…-vor-schema-14.dump`). Wer ganz sicher gehen will, sichert vorher zusätzlich selbst
  (`docker compose exec saga python run.py backup --dateien`). Zurück auf 2.3 nur über dieses Backup – mit 2.4
  zurückspielen, dann `SAGA_VERSION=2.3` (Anleitung, Abschnitt 11 „Von 2.3 auf 2.4“).
- **Gravatar ist danach für alle Konten aus.** Bis 2.3 war „Gravatar verwenden“ ab Werk an; eine bewusste Wahl lässt
  sich davon nicht unterscheiden. Migration 14 schaltet es deshalb für alle Konten ab und löscht zwischengespeicherte
  Gravatar-Bilder – bis zur Einwilligung geht keine Anfrage an gravatar.com. Wer Gravatar will, schaltet es unter
  Einstellungen → Konto → Profilbild einmal neu ein. Eigene Profilbilder und SSO-Bilder bleiben.
- **Neu im Container: saga-media** auf dem bisherigen Port 8080 (Saga intern auf `127.0.0.1:8079`). Kein neuer Port,
  nichts am Reverse Proxy zu ändern; `TRUSTED_PROXIES` muss wie bisher stimmen (sonst teilen sich alle Besucher die
  Grenze `MEDIA_MAX_CONNS_IP`). `SAGA_MEDIA=off` startet wie bis 2.3.
- **Ohne Container:** saga-media selbst bauen und davor starten oder `MEDIA_ACCEL=nginx|caddy` (Beispiele in der
  Anleitung, „Medienpfad“); sonst liefert Python Anhänge wie bisher, aber höchstens `MEDIA_PY_STREAMS` gleichzeitig.
- **Admin-Schalter prüfen:** Allgemein → Chats „Größtes Video im Chat“ (500 MB) und „Speicher für Chat-Anhänge je
  Konto“ (neu 4096 statt 1024 MB – Platz auf dem Volume prüfen); Allgemein → Teilen „Suche nach Anmeldename“ (Standard
  an); Allgemein → Anrufe Geräte je Anruf (nur senken).
- **TURN/STUN:** Der ab Werk eingetragene STUN-Server (`stun.nextcloud.com:443`) sieht die IP-Adressen der Anrufenden;
  der Admin-Bereich weist darauf hin. Für Gruppenanrufe und Begleitgeräte (mehr Verbindungen je Anruf) empfiehlt sich ein
  eigener TURN-Server über Port 443 (Anleitung, „TURN über Port 443“).
- **Öffentlicher Name gleich Anmeldename:** wird nicht umgestellt – Saga hat ihn nie so vorbelegt, er ist also selbst
  gewählt, und eine Umstellung bräche alle Links auf veröffentlichte Seiten. Die Einstellungen weisen darauf hin; neue
  öffentliche Namen dürfen nicht der Anmeldename sein.
- **Zweiten Faktor entfernen** braucht jetzt eine Bestätigung (Passwort, Code oder Passkey). Web und Apps fragen bei
  `403 {reauth: true}` schon heute nach und wiederholen. Ebenso **einen weiteren Faktor hinzufügen** und **neue
  Wiederherstellungscodes erzeugen**, sobald schon ein zweiter Faktor eingerichtet ist (Web fragt nach; Apps siehe
  `docs/APP-API.md`).
- **Neue Einstellungen der `.env`** (alle optional, Anleitung Abschnitt 13): `SAGA_MEDIA`, `MEDIA_INTERNAL_PORT`,
  `MEDIA_AUTHZ_TTL`, `MEDIA_MAX_CONNS`, `MEDIA_MAX_STREAMS`, `MEDIA_MAX_STREAMS_IP`, `MEDIA_MAX_CONNS_IP`,
  `MEDIA_IDLE_UNAUTH`, `MEDIA_STALL_TIMEOUT`, `MEDIA_ACCEL`/`MEDIA_ACCEL_PREFIX`, `MEDIA_PY_STREAMS`, `VIDEO_MAX_MB`,
  `CHAT_QUOTA_MB` (neuer Standard 4096), `CHAT_OFFLINE_GRACE`.
- **Apps 2.4 empfohlen** (Videos, Gruppenanrufe, weiteres Gerät, Gelesen-Haken):
  [Saga-Apps](https://github.com/yggdrasil-sh/saga-apps/releases). Apps 2.3 und 2.1 laufen weiter, nur ohne diese
  Funktionen.

### Bekannte Grenzen

- **Gruppenanrufe als Mesh:** höchstens 4 Geräte mit Video bzw. 6 nur mit Ton (eigene Zusatzgeräte zählen mit). Größere
  Runden bräuchten einen Weiterleitungs-Server (SFU); Frame-E2E dafür ist für 2.5 bewertet. Gruppenanrufe, Gelesen/Online
  und große Videos über Servergrenzen nur, wenn alle beteiligten Server 2.4 haben.
- **Anmeldename (Restgrenze H1):** Wer mit einem Konto chatten darf, erfährt beim Anlegen eines Direkt-Chats technisch
  dessen Anmeldenamen, auch bevor es antwortet – Chats v1 binden Geräte-Zertifikate, Nachrichten-Signaturen und die
  Sicherheitsnummer daran (Einzelheiten in `docs/APP-API.md`). Die Oberfläche zeigt ihn nicht. Der Hilfetext zum
  Admin-Schalter „Suche nach Anmeldename“ weist darauf hin; begrenzen lässt es sich nur mit „Nur innerhalb gemeinsamer
  Gruppen“.
- **Systemton beim Bildschirmteilen** fehlt auf iPhone, iPad und Mac (Apps); im Web liefern ihn nur Chrome/Edge (Ton des
  Tabs, unter Windows/ChromeOS auch der ganze Systemton).
- **Linux:** Anrufe laufen in der Linux-App weiterhin im Browser (die App klingelt und öffnet das Gespräch dort).
- **Bild-im-Bild** im Web nur, wo der Browser es kann (nicht in Firefox).
- **Bekannt (so gewollt):** Wird ein Mitglied aus einem Chat entfernt, kann es einen eigenen, schon begonnenen Upload über
  saga-media noch bis zu 60 s fortsetzen (gemerkte Erlaubnis, `MEDIA_AUTHZ_TTL`). Die Datei lässt sich ohne
  Mitgliedschaft keiner Nachricht zuordnen und wird nach einer Stunde entfernt.

## [2.3.0] – 2026-10-19

Saga 2.3 ist das große Herbst-Update: spürbar schneller bei vielen Seiten und großen Datenbanken, Föderation mit
Chats, Anrufen und vollständig gemeinsam bearbeiteten Seiten über Servergrenzen, verschlüsselte Backups für Admins
und für jede Person sowie Dateien aus der eigenen Nextcloud direkt in Seiten. Eine Version 2.2 gibt es nicht: Die
Optimierungswoche (Testversionen `2.2.0-rc.*`) steckt vollständig in 2.3.
Gleichzeitig erscheinen die [Saga-Apps 2.3](https://github.com/yggdrasil-sh/saga-apps/releases) mit Föderation,
Backups und Nextcloud-Dateien auf allen Plattformen. Die Schnittstelle wurde nur erweitert – Apps 2.1 und älter laufen
weiter (`MIN_APP` bleibt), nur ohne die neuen Funktionen; Chats mit Personen anderer Server zeigen sie mit „bitte App
aktualisieren“.

### Höhepunkte

- **Schneller.** Große Antworten kommen gzip-komprimiert (80–90 % kleiner; App 8 h im Vordergrund: 2,5 GB → 0,28 GB),
  der Seitenbaum gleicht nur noch Änderungen ab, lange Datenbanken zeigen nur den sichtbaren Ausschnitt. Gemessen mit
  `tools/perf`: Seitenbaum bei 25.000 Notizen sichtbar nach 1,3 statt 3,1 s, Baum neu laden bei 60.000 Seiten/Einträgen
  0,05 statt 1,0 s, Datenbank mit 2.000 Zeilen bedienbar nach 0,6 statt 12,0 s, „Mit mir geteilt“ 180 statt 498 ms.
- **Föderation komplett.** Personen verbundener Server über ihren Namen finden, Ende-zu-Ende-verschlüsselt chatten
  (direkt, in Gruppen, im Seiten-Chat) und telefonieren, geteilte Seiten samt Datenbanken, Unterseiten, Uploads und
  Versionen gemeinsam bearbeiten – geschützt gegen fehlerhafte und böswillige Server. Beide Server brauchen 2.3.
- **Verschlüsselte Backups.** Der Admin-Bereich sichert täglich Datenbank, Dateien und Schlüssel – mit age
  verschlüsselt – nach S3, in einen Ordner oder auf Nextcloud/WebDAV; Wiederherstellung auch ohne Saga. Dazu
  **persönliche Backups**: jede Person sichert ihren eigenen Workspace in ihre eigene Nextcloud, mit eigenem Schlüssel.
- **Nextcloud-Dateien in Seiten.** Die eigene Nextcloud verknüpfen und Bilder, Dateien und Ordner als Block einfügen –
  alle, die die Seite sehen, sehen auch die Datei, ohne eigenes Nextcloud-Konto.

### Für alle

#### Föderation

- **Personen finden:** In der Chat- und Teilen-Suche erscheinen Personen verbundener Server mit Anzeigename und Herkunft
  („Anna Berg · b.example“, gleiche Namen unterscheidbar, mit Profilbild). Übertragen werden nur Anzeigename, Profilbild
  und eine zufällige, feste Kennung (`~…@server`) – nie Anmeldename, E-Mail oder Gruppen. Wer nicht gefunden werden will,
  schaltet unter Einstellungen → Konto „In Adressbüchern verbundener Server erscheinen“ ab; die anderen Server löschen den
  Eintrag dann sofort (und den Namen, wo die Person dort nirgends vorkommt); auch über die Kennung ist der Anzeigename
  dann nicht mehr abfragbar (ein selbst gewählter öffentlicher Name bleibt auffindbar). Deaktivierte Konten erscheinen
  nie. Alle Personensuchen finden bei 1–2 Zeichen genaue Treffer („hd“), ab 3 Zeichen auch Teile von Namen; `name@server`
  findet genau diese Person. Die Weboberfläche sucht ab dem ersten Zeichen (höchstens 60 Suchen je Minute), die Apps
  vorerst weiter ab 3 Zeichen.
- **Direkt-Chats über Servergrenzen:** In „+ Neu“ Personen anderer Server anschreiben. Der erste Kontakt kommt als
  **Nachrichtenanfrage** – ohne Benachrichtigung, Push oder Klingeln, bis man annimmt. Ablehnen oder Sperren (die Person
  oder den ganzen Server) erfährt die andere Seite nicht; die Sperrliste steht unter „Chats & Geräte“. Ist der andere
  Server kurz weg, wartet die Nachricht („wird zugestellt …“, bis 3 Tage) und geht später in der richtigen Reihenfolge
  raus. Anhänge, „für alle löschen“ und „Automatisch löschen nach …“ funktionieren wie gewohnt (die strengere Vorgabe des
  eigenen Admins gilt immer); die KI ist in diesen Chats aus, bis jemand sie einschaltet (Hinweis für alle). Anhänge
  bleiben beim Server der sendenden Person, die anderen holen sie beim Öffnen (Vorschaubilder gleich). „Gelesen“ bleibt
  auf dem eigenen Server, Push löst nur der eigene Server aus.
- **Gruppen und Seiten-Chats** mit Personen mehrerer Server: Einladung als Nachrichtenanfrage, Änderungen an den
  Mitgliedern erscheinen für alle mit Herkunft. Ist eine Seite mit Personen oder Gruppen anderer Server geteilt, gehören
  sie zum Seiten-Chat (ältere Nachrichten bleiben lesbar, die KI ist danach aus); Mitglieder einer Gruppe eines anderen
  Servers meldet nur dieser – und nur, wenn die Gruppe dort Ziel einer Freigabe ist. Personen eines dritten Servers, mit
  dem der eigene nicht verbunden ist, sind trotzdem dabei (Schlüssel und Anhänge über das Heim, die App prüft selbst).
  Sperren in einer Gruppe trifft genau die Person, die dich eingeladen hat.
- **Anrufe** (Audio, Video, Bildschirm) aus angenommenen Direkt-Chats auch über Servergrenzen; jede Seite nutzt ihren
  eigenen TURN-Server.
- **Sicherheit der Chats:** Ende-zu-Ende wie bisher – die Server sehen keine Inhalte. Sicherheitsnummer und „überprüft“
  funktionieren wie lokal; ändert sich der Schlüssel der anderen Person, warnt Saga, bei „überprüft“ markierten Personen
  wird Senden gesperrt, bis man neu verglichen hat. Die Geräte einer Person stehen in einer von ihr selbst signierten
  Liste – ein Server kann keine Geräte unterschieben oder Widerrufe verschweigen. Ältere Apps zeigen solche Chats mit
  „bitte App aktualisieren“.
- **Seiten anderer Server vollständig:** Datenbanken öffnen, filtern und bearbeiten, Einträge und Unterseiten anlegen,
  innerhalb des geteilten Bereichs verschieben, Bilder und Dateien hochladen (liegen beim Eigentümer; je Person eines
  anderen Servers eigene Grenze `FED_UPLOAD_QUOTA_MB`, Standard 200), Versionen ansehen/wiederherstellen, exportieren,
  „Wer gerade hier ist“ und Live-Aktualisierung (kurzes Abo ohne Inhalte, `FED_WATCH_TTL` 90 s; neue oder verschobene
  Unterseiten aktualisieren „Mit mir geteilt“). Teilen geht auch an Personen ohne öffentlichen Namen.
- **Rollen (auch lokal):** Wer „Bearbeiten“ darf, sieht jetzt auch Versionen und stellt sie wieder her (gesperrte Seiten
  weiter nur der Eigentümer) und verschiebt Seiten innerhalb eines geteilten Bereichs – nur dorthin, wo genau dieselben
  Freigaben gelten.
- **Trennen und Kontolöschung:** Nach dem Trennen bleiben Chats mit Personen des anderen Servers lesbar, sind aber
  schreibgeschützt; laufende Anrufe enden. Ein gelöschtes Konto verschwindet auch auf den anderen Servern aus seinen
  Direkt-Chats.
- Mit Servern 2.1 bleibt die Verbindung wie bisher (Teilen, Kommentare, Benachrichtigungen). Anleitung:
  `docs/ANLEITUNG.md`, Abschnitt Föderation.

#### Persönliches Backup

- **Einstellungen → Mein Backup:** jede Person sichert ihren eigenen Workspace in ihr **eigenes** Ziel – eigene
  Nextcloud (Login Flow v2, eigenes App-Passwort, „Trennen“ widerruft) oder anderes WebDAV – verschlüsselt mit ihrem
  **eigenen age-Schlüssel** (erzeugen wie beim Admin, privater Schlüssel nur einmal angezeigt, oder eigenen öffentlichen
  eintragen). Zeitplan aus/wöchentlich/täglich, Aufbewahrung, „Jetzt sichern“, Status, Benachrichtigung bei Fehlern.
- **Nur eigene Daten:** eigene Seiten/Unterseiten/Datenbanken samt Einträgen, eigene Dateien, Vorlagen, Archiv,
  Papierkorb, Versionen, eigene Kommentare, Aufgaben, Lesezeichen, Profil/Einstellungen – je Kategorie abschaltbar,
  einzelne Seitenbäume ausschließbar. Nie: „Mit mir geteilt“, Seiten/Dateien anderer Konten, Chats. Kommentare anderer
  auf eigenen Seiten standardmäßig aus; fremde Benutzernamen werden weggelassen.
- **Offenes Format:** `saga-<zeit>.zip.age` (age-verschlüsseltes ZIP mit `manifest.json` + SHA-256 je Datei,
  `daten.json`, Markdown wie der Export, Dateien, `LIESMICH.txt`) und `.sha256` daneben.
- **Backup einspielen:** Datei hochladen oder aus dem eigenen Ziel wählen, privater Schlüssel nur für den Vorgang; Import
  nur ins eigene Konto als neue Seiten (neue IDs, Verweise, Relationen, eingebettete Datenbanken, Schaltflächen und
  Bilder umgeschrieben, Inhalte bereinigt), optional in „Wiederhergestellt <Datum>“. Grenzen: `daten.json` höchstens
  64 MB, höchstens 20 000 Dateien, je Seite die neuesten 50 Versionen; Textseiten auf die Länge wie beim Speichern
  gekürzt. Hochgeladene Backups werden nach dem Einspielen sofort gelöscht, auch nach einem Fehler (nur nach falschem
  Schlüssel bleibt ein verschlüsselter Upload für einen neuen Versuch).

#### Nextcloud-Dateien in Seiten

- **Einstellungen → Nextcloud:** jede Person verknüpft ihre **eigene** Nextcloud (beliebige eigene Adresse, Login Flow v2,
  eigenes App-Passwort verschlüsselt gespeichert, „Trennen“ widerruft es).
- **Block „Nextcloud-Datei“** im „/“-Menü: Auswahl (Zuletzt, Ordner, Suche), eingefügte Nextcloud-Links der eigenen
  Instanz werden zum Block. Bilder in Seitenbreite, Dateien als Karte mit Vorschau/Download/„In Nextcloud öffnen“,
  Ordner mit ersten Einträgen. Hinweis beim Einfügen: alle, die die Seite sehen, sehen auch die Datei.
- **Vorschau und Download mit den Rechten des Autors** (Person, die den Block eingefügt hat – setzt nur der Server, mit
  Signatur je Seite): Leser brauchen kein eigenes Nextcloud, aber Leserecht auf die Seite. Mitbearbeitende fügen nur
  Dateien aus ihrer eigenen Nextcloud ein; Blöcke anderer bleiben unverändert und lassen sich nicht in andere Seiten
  kopieren. Getrennt, kein Bearbeiten-Recht mehr oder Datei weg → Karte ohne Inhalt.
- **Als Kopie übernehmen:** Datei wird normale Saga-Datei (folgt den Saga-Rechten, Backup, Export, offline).
- **Veröffentlichte Seiten und verbundene Saga-Server** nur mit Schalter je Block (Standard aus); Vorschau/Download dann
  über die Adresse der Seite bzw. den eigenen Server.
- Export: Link statt Kopie; Suche findet den Dateinamen.

#### Schneller

- **Seitenbaum im Web:** Aufbau über einen Index statt wiederholter Suche durch alle Seiten; Auf- und Zuklappen baut nur
  den betroffenen Ast neu. Bei 25.000 Notizen: Baum sichtbar nach 1,3 statt 3,1 s, Ast aufklappen 0,15 statt 1 s,
  Seite öffnen 1,1 statt 2,7 s.
- **Seitenbaum nur noch abgleichen:** Nach Aktionen, Live-Ereignissen und der Rückkehr in den Tab lädt die
  Weboberfläche nur noch die Änderungen (beim Öffnen und spätestens alle 30 Minuten einmal ganz). Mehrere Aktionen kurz
  hintereinander teilen sich eine Abfrage (immer mit Daten von nach der Aktion); ein neuer Datenbank-Eintrag („+ Neu“,
  „+ Neu in …“ bei Relationen) wird direkt eingetragen. Bei 60.000 Seiten/Einträgen: Baum neu laden 1,0 s → 0,05 s,
  neuer Datenbank-Eintrag samt Nachladen 1,2 s → 0,04 s; der erste Aufbau bleibt gleich.
- **Andere Tabs und Geräte** laden den Seitenbaum nach Änderungen sofort nach; eine Seite, die in zwei Tabs offen ist,
  bleibt in beiden aktuell.
- **Lange Tabellen und Listen** (ab 150 Einträgen): Nur die sichtbaren Zeilen (plus Vorlauf) stehen im DOM,
  Platzhalter halten die volle Scrollhöhe. Summenzeile, Anzahl, Gruppen, Suche, Sortierung, Filter, bedingte Farben,
  Spaltenbreiten, „Öffnen“/Seitenansicht mit Vor/Zurück rechnen weiter über **alle** Zeilen. Tastatur: Enter springt
  auch über den Ausschnitt hinaus in die nächste Zeile; die Zeile mit dem Fokus bleibt beim Wegscrollen erhalten
  (keine verlorenen Eingaben); „+ Neu“ holt die neue Zeile ins Bild. Drucken setzt vorher alle Zeilen ein.
  Gemessen (`tools/perf`, Stufe M, Datenbank mit 2.000 Zeilen): öffnen bis bedienbar 12,0 s → 0,6 s (DOM-Knoten
  176.000 → 12.000, längste Blockade 9,9 s → 0,15 s), Suchfeld 4,4 s → 0,5 s, Ansicht „Offen“ 9,5 s → 0,5 s, zurück zur
  Tabelle 11,1 s → 0,3 s, „+ Neu“ 16,8 s → 0,9 s. Stufe L (10.000 Zeilen; vorher brach die Messung ab): öffnen
  0,9 s, Suche 0,6 s, ans Ende scrollen 0,4 s.
  Kurze Tabellen (unter 150 Einträgen) entstehen genau wie bisher. Einschränkung: Die Suche des Browsers (Strg+F) findet
  in langen Tabellen nur Zeilen im Ausschnitt – dafür gibt es das Suchfeld der Datenbank.
- **Galerie und Board** setzen die ersten Karten sofort ein, den Rest in kurzen Häppchen danach – die Seite bleibt
  bedienbar, am Ende steht dieselbe Ansicht wie bisher (Ziehen zwischen Spalten wie gehabt). Board mit 2.000 Karten:
  erste Karten nach 1,1 s, alle nach 2,0 s (vorher 2,4 s blockiert).
- **Rückverweise** („Verlinkt von“) werden erst geladen, wenn das Seitenende in Sicht kommt – kurze Seiten wie bisher
  sofort, lange erst beim Hinscrollen.
- **Suche, „Mit mir geteilt“ und Startseite** rechnen auf dem Server über vorbereitete Daten statt über jede Seite
  (Einzelheiten unter „Technisch“): gleiche Treffer und Rollen, nur schneller. Stufe L: `/api/shared` 498 → 180 ms,
  Startseite als Empfänger 251 → 36 ms, voller Seitenbaum (ältere Apps) 815 → 616 ms, `?rows=0` 204 → 154 ms, Suche
  in einer Datenbank-Ansicht mit 10.000 Einträgen 346 → 181 ms. Einzige sichtbare Änderung: Bei mehr als 500 für dich
  geteilten Seiten eines Kontos sind deren Treffer jetzt durchgehend nach Änderungsdatum sortiert (bisher blockweise je
  500 Seiten).
- **Mehr Platz für Datenbanken:** `NOTE_MAX_COUNT` (5.000) zählt nur noch normale Seiten ohne Papierkorb; Datenbank-
  Einträge haben eine eigene, größere Grenze (siehe „Für Admins“). Bisher war ein Konto mit größeren Datenbanken schon
  nach 5.000 Einträgen „voll“.

#### Behoben

- Chat → „Neue Unterhaltung“ (auch „+ Gruppe mit mehreren Personen“ und „Person hinzufügen“): Wer `name@server`,
  `@name@server` oder eine E-Mail-Adresse eingab, sah nur „Keine Treffer.“. Jetzt erklärt ein Hinweis, dass Personen
  verbundener Saga-Server über ihren Anzeigenamen oder `name@server` gefunden werden und Personen hier über Namen oder
  Benutzernamen (nicht über die E-Mail-Adresse) – mit Link zur Anleitung (Föderation).
- Nachrichtenanfrage: Die Schaltfläche zum Annehmen hieß auf Englisch, Spanisch, Russisch und Thai wie das Annehmen
  eines Anrufs („Answer“, „Responder“ …) – jetzt eigener Text („Anfrage annehmen“, „Accept request“ …).
- Teilen-Dialog: Bei `name@server` ohne Treffer steht statt „Niemand gefunden.“ der Grund – die Person gibt es dort
  nicht bzw. sie hat keinen öffentlichen Namen, der Server ist nicht verbunden, er ist gerade nicht erreichbar oder
  es ist dieser Server selbst; bei E-Mail-artigen Eingaben der Hinweis, dass der öffentliche Name gemeint ist.

### Für Admins

#### Verschlüsselte Backups (S3, Ordner, Nextcloud/WebDAV)

- **Admin-Bereich → Backups:** tägliches Backup (Uhrzeit wählbar) von Datenbank, Dateien (`files` ohne Vorschaubilder,
  `chat`, `svc-icons`) und – abschaltbar, Standard an – `SECRET_KEY`/`CAL_SECRET_KEY` in ein Ziel nach Wahl (S3, Ordner,
  WebDAV/Nextcloud); beim Wechsel oder Trennen werden die Zugangsdaten des vorigen Ziels geleert.
- **Ziel WebDAV/Nextcloud:** Nextcloud wird über `/status.php` erkannt: Anmeldung per **Login Flow v2** (gespeichert
  werden nur Anmeldename und App-Passwort, verschlüsselt wie die anderen Admin-Geheimnisse; „Trennen“ widerruft das
  App-Passwort), große Dateien mit **Chunking v2**. Sonst Benutzer/Passwort + PUT. Abrufe über den SSRF-Schutz
  (netguard); die eingetragene Adresse gilt als freigegeben, keine Weiterleitungen.
- **Ziel S3** (MinIO, Garage, Backblaze B2, Hetzner, Wasabi, AWS …): Endpunkt, Region, Bucket, Präfix, Access-/Secret-Key
  (verschlüsselt gespeichert), path-style oder virtual-hosted (automatisch: AWS virtual, sonst path). Große Dateien als
  **Multipart-Upload** (streamend von der Platte, bei Fehlern abgebrochen).
- **Ziel Ordner im Container** (eingebundenes Volume, NAS-Mount): nur unterhalb von `SAGA_BACKUP_DIRS` (Standard
  `/backup`), Pfade aufgelöst (Symlinks, `..`) – kein Ausbrechen; Schreiben über `.part` + `fsync` + Umbenennen.
  docker-compose-Beispiel in der Anleitung.
- **Verschlüsselung mit age** (Paket `age` im Image) und **öffentlichen Schlüsseln** – Saga kennt den privaten Schlüssel
  nie. „Schlüsselpaar erzeugen“ zeigt ihn einmal (Text + Download), Bestätigung mit den letzten 6 Zeichen; oder eigenen
  öffentlichen Schlüssel eintragen (`age1…`, `ssh-ed25519 …`), mehrere Empfänger möglich.
- Je Lauf ein Ordner `saga-<zeit>/` mit `db.dump.age` (vorher mit `pg_restore --list` geprüft), `dateien.tar.age`,
  `geheimnisse.env.age`, `manifest.json.age` (Prüfsummen der Klartexte) und zuletzt `manifest.json` (Version, Schema,
  Größen + SHA-256, Schlüssel-Fingerabdrücke; ohne Namen/Hostnamen). Vor dem Upload Prüfung des freien Platzes, danach
  Größe + erneutes Herunterladen mit SHA-256-Vergleich (`BACKUP_VERIFY_MB`).
- **Zeitplan** im Hintergrund: genau ein Läufer je Datenbank (Advisory-Lock), niedrige Priorität (`nice`/`ionice`);
  **Aufbewahrung GFS** 7 täglich / 4 wöchentlich / 6 monatlich (einstellbar, Läufe der letzten 24 h bleiben immer);
  unvollständige Läufe werden nach einem Tag entfernt, fremde Ordner im Ziel nie angefasst.
- **Status und Benachrichtigung:** letzter Erfolg, nächster Lauf, Verlauf, Backups im Ziel mit Fingerabdrücken. Bei
  Fehlern und nach 48 h ohne Erfolg Benachrichtigung an alle Admins (Glocke + Mitteilung, neue Art `backup.failed`,
  Push-Kategorie „admin“ nur für Admins), optional Webhook (ntfy, Gotify, beliebige URL).
- **Kommandozeile:** `run.py backup-extern`, `run.py backup-holen [<lauf>] [--liste]`,
  `run.py restore --schluessel <datei> [<lauf>|<ordner>]` (holt, prüft, entschlüsselt, spielt Datenbank über das
  unveränderte `db.restore` sowie Dateien und Geheimnisse zurück; frischer Server: Ziel per `SAGA_BACKUP_URL`/`_USER`/
  `_PASSWORD`, `SAGA_BACKUP_S3_*` oder `SAGA_BACKUP_PATH`), `run.py backup-pruefen --schluessel <datei>`
  (Testwiederherstellung in eine Wegwerf-Datenbank).
  `run.py backup`/`restore <datei>` und die Backups vor Migrationen bleiben unverändert.
- **`SAGA_RESTORE_TEST=1`:** Testwiederherstellung auf einem zweiten Server ohne ausgehende Verbindungen zu den Diensten
  der echten Instanz (Föderation, Mitteilungen, Backups, auch persönliche, Versionshinweis, Kalender-Abgleich, KI,
  Immich, Websuche, Avatare aus – zentral im SSRF-Schutz `netguard`) – ein zurückgespieltes Backup trägt die Identität
  der Instanz. Erlaubt bleibt nur das Lesen vom eingetragenen Backup-Ziel auf der Kommandozeile (`backup-holen`,
  `restore --schluessel`, `backup-pruefen`): nur dessen Host, nur GET/HEAD/PROPFIND; Hochladen, Löschen und Aufräumen
  bleiben gesperrt.
- **Arbeitsordner** `/data/backups/extern-tmp/`: Lauf und `backup-pruefen` je in eigenem Unterordner (räumen sich nicht
  gegenseitig weg); Reste abgebrochener Läufe (mit `db.dump` im Klartext) löscht der nächste Start.
- Anleitung: Abschnitt 11 „Verschlüsselte Backups“ (auch Wiederherstellung ohne Saga mit `age -d`).

#### Persönliche Backups und Nextcloud-Dateien

- **Admin → Backups → Persönliche Backups:** erlauben (Standard an), Größe je Backup, Mindestabstand für „Jetzt
  sichern“, häufigster Zeitplan, freigegebene interne Hosts (sonst nur öffentliche https-Ziele, netguard); Hinweis zum
  Datenschutz (Server-Backup vs. persönliches Backup). `USER_BACKUP_MAX_MB` (Standard 4096) begrenzt Größe und Uploads,
  `USER_BACKUP_DATA_MB` (64) und `USER_BACKUP_FILES_MAX` (20 000) das Einspielen. Kontolöschung entfernt die
  Einstellungen und widerruft das App-Passwort.
- **Nextcloud-Dateien in Seiten:** Ohne Admin-Freigabe nur https und öffentliche Adressen (netguard); Heimnetz-Server
  und der Schalter „Nextcloud-Dateien in Seiten erlauben“ (Standard an) unter Admin → Allgemein → „Verbindungen der
  Benutzer“. Vorschaubilder auf Platte (`/data/nc-cache`, nicht im Backup, `NC_CACHE_MB`), Downloads gestreamt
  (`NC_DOWNLOAD_MAX_MB`, für andere Server `NC_FED_MAX_MB`). Kontolöschung widerruft das App-Passwort.

#### Föderation

- **Schalter je verbundenem Server** (Admin → Föderation → „Funktionen je Instanz“): Chat, Anrufe, Adressbuch (optional
  nur bestimmte Gruppen) – Standard an – und die Ausnahme von „Teilen nur innerhalb gemeinsamer Gruppen“ (Standard aus).
  Für Skripte: `GET /api/federation`, `PUT /api/federation/peers/{id}`.
- **Zustellung:** Ereignisse gehen sofort raus (höchstens `FED_EXPRESS_TIMEOUT`, Standard 5 s), sonst übernimmt der
  Ausgangskorb in der richtigen Reihenfolge. Ein Ereignis, das ein Server dauerhaft abweist, hält nur seine eigene Kette
  an („event.stuck“); nach dem Zurückspielen eines Backups beginnen die Ketten neu, ohne dass die Gegenstellen etwas
  still verwerfen.
- **Schutz vor fehlerhaften oder böswilligen Servern:** Absender, Signaturen, Reihenfolge und Schlüssel prüft jeder
  Server selbst; Fälschungen, Wiederholungen, umsortierte oder unterdrückte Nachrichten werden abgewiesen bzw. nachgeholt
  und im Prüfprotokoll vermerkt. Abgelehnte und gesperrte Chats bleiben es, auch wenn das Heim weiter zustellt
  (nichts gespeichert, kein Vorabruf); versiegelte Schlüssel vom Heim werden geprüft.
  Schlüssel einer Person kommen nur von ihrem eigenen Server, wenn dieser verbunden ist (ein anderer Server kann sie
  nicht unterschieben). Personen, die ein Server als Gruppenmitglieder „erfindet“, erscheinen mit ihrer wahren Herkunft;
  Namen anderer Server dürfen keine Herkunft vortäuschen (Trennpunkte werden entfernt).
- **Fluten und langsame Server:** Raten je Server für Anfragen, Ereignisse, Chat-Nachrichten, Mitgliederlisten,
  Namensauflösung, Personensuche (WebFinger) und Uploads; Nachrichten und Mitgliederlisten werden bei Überlast später
  erneut zugestellt statt verworfen (`FED_CHAT_IN_RATE`). Antworten „zu viele Anfragen“ sind signiert und nennen eine
  Wartezeit – ein bremsender Server bekommt dann eine Pause je Server statt vieler Einzelversuche („Gegenseite bremst“).
  Ein langsamer Server belegt höchstens ein paar Hintergrund-Aufträge gleichzeitig (`FED_PEER_SLOTS`), der Ausgangskorb
  arbeitet je Server getrennt (`FED_OUT_WORKERS`) – Chats mit anderen Servern laufen ungebremst weiter.
- **Zwischenkopien fremder Chat-Anhänge** sind je Server begrenzt (`FED_CHAT_CACHE_MB`, Standard 1024; die zuletzt
  benutzten bleiben) und verfallen nach `FED_CHAT_CACHE_DAYS` (30) ohne Abruf – bei Bedarf holt Saga sie neu.
  Von getrennten (oder für Chats abgeschalteten) Servern bleiben sie, da sie sich nicht neu holen lassen.
- **Rückfall auf Protokoll 1** (ältere Version beim anderen Server zurückgespielt oder ein Angriff) steht im
  Prüfprotokoll; Chats mit diesem Server ruhen, wartende Ereignisse bleiben im Ausgangskorb, bis er wieder Protokoll 2
  meldet.
- **Datenschutz beim Trennen:** Freigaben, Adressbuch, Schlüssel-Zwischenspeicher, wartende Ereignisse und die Namen von
  Personen des anderen Servers werden gelöscht – außer dort, wo sie hier noch vorkommen (Kommentare, Chat-Verläufe).
  Neu in der Anleitung: Überblick für Admins mit Datenschutz-Hinweis und Fehlerbildern.

#### Leistung und Betrieb

- **Kompression:** JSON-, Text-, JS- und CSS-Antworten ab 1 KB kommen gzip-komprimiert, wenn Browser oder App es
  anbieten (alle tun das). Seitenbaum, Freigaben, Datenbank-Zeilen und Widget-Listen werden dadurch um 80–90 % kleiner
  (z. B. 5,2 MB → 0,6 MB bei 25.000 Notizen; App im Vordergrund 8 h: 2,5 GB → 0,28 GB). Nie komprimiert werden der
  Live-Kanal und der KI-Stream (`text/event-stream`), Bilder, Dateien, Zip-Exporte und Teilantworten (Video spulen).
  Einstellbar mit `GZIP_LEVEL` (Standard 5, `0` = aus) und `GZIP_MIN_BYTES` (Standard 1024).
- **Seiten-Grenze:** `NOTE_MAX_COUNT` (5.000) zählt nur noch normale Seiten ohne Papierkorb. Datenbank-Einträge haben
  eine eigene Grenze `NOTE_ROW_MAX_COUNT` (Standard 50.000 bzw. 10 × `NOTE_MAX_COUNT`); alles zusammen inkl. Papierkorb
  höchstens doppelt so viel wie beide Grenzen. Keine Grenze ist strenger als vorher.
- **Papierkorb:** eigener Index; die 30-Tage-Frist läuft beim Seitenbaum höchstens stündlich statt bei jedem Laden
  (die Papierkorb-Liste prüft weiterhin jedes Mal).
- **Erster Start nach dem Update:** Saga gleicht einmal alle Seiten für Suche, Rückverweise und Freigaben ab (etwa
  1–2 s je 1.000 Seiten, im Hintergrund). Bis dahin prüft Saga noch nicht abgeglichene Seiten wie bisher direkt am
  Inhalt – das Ergebnis hängt nie vom Stand des Abgleichs ab.
- **Backup zurückspielen** (`python run.py restore`) scheiterte mit der Standard-Compose (`postgres:16`) immer: Das Image
  bringt `pg_restore` 17 mit, das `SET transaction_timeout` schickt („unrecognized configuration parameter“);
  `pg_restore` 16 kann die Backups von `pg_dump` 17 umgekehrt nicht lesen. Jetzt erzeugt `pg_restore` 17 die SQL-Liste,
  Saga lässt Einstellungen weg, die der Server nicht kennt, und `psql` spielt alles in **einer** Transaktion ein – mit
  `postgres:16` und `:17`, für alle bisherigen Backups. Ein beschädigtes Backup ändert nichts mehr (COMMIT erst, wenn
  das Backup vollständig gelesen ist). Ein Backup von vor 2.3 lässt sich auf einer 2.3-Datenbank einspielen (die neuen
  abgeleiteten Tabellen werden in derselben Transaktion vorher entfernt, der nächste Start legt sie neu an). Backups
  selbst (automatisch vor Updates, `python run.py backup`) unverändert, Image unverändert groß.
- **Testing-Builds als Release Candidates:** Testing-Images heißen jetzt `x.y.z-rc.N` statt `x.y.z-testing.<commit>`
  (z. B. `2.3.0-rc.7`, N = Commits seit dem letzten Release-Tag; ohne Tags im Build `2.3.0-rc+<commit>`). Der Commit
  steht als Build-Kennung daneben: im Admin-Bereich „2.3.0-rc.7 (1cc210f) · 🧪 Release Candidate“, bei
  `python run.py version` und in `/api/version` als neues Feld `build` (dazu `channel`: `stable`, `rc` oder `testing`).
  `version` bleibt reines SemVer ohne Build-Kennung. Ein Release Candidate ist immer neuer als die stabile Version davor:
  Der Update-Hinweis vergleicht nach SemVer (2.1.0 < 2.3.0-rc.7 < 2.3.0) – kein Hinweis auf 2.1.0, aber auf 2.3.0,
  sobald es erschienen ist. Steht `VERSION` noch auf der gerade veröffentlichten Version, zählt der RC zur nächsten
  Patch-Version (z. B. `2.1.1-rc.0`). Docker: zusätzlich das Tag `rc` neben `testing` (das bleibt, bestehende
  Installationen laufen unverändert weiter). Release-Builds (main) unverändert.

### Technisch (für App-Teams und Betreiber)

#### Föderation, Protokoll 2

- **Protokoll 2:** `/.well-known/saga` meldet `protocol: 2` und Fähigkeiten (`v2 express handles directory keys chat2
  calls2 chatgroups live presence db subpages upload versions export nc_files`), Austausch signiert über
  `POST /fed/v2/hello`; eine Funktion gilt nur, wenn beide Server sie melden und der Admin-Schalter an ist. Ereignisse
  mit Kennung (höchstens einmal verarbeitet) und Ketten (`chain`/`cseq`, mit Epoche), Adressbuch
  `GET /fed/v2/directory?since=` mit `directory.changed`, Schlüssel `GET /fed/v2/keys`, Chats `POST /fed/v2/chat`
  (Aufträge an das Heim), Ereignisse `chat.msg`, `chat.members`, `chat.keys.changed` …, Seiten `/fed/v2/notes/{id}/…`,
  Live `POST /fed/v2/live`. `429`-Antworten sind signiert und tragen `Retry-After`. Fähigkeit `nc_files`:
  Nextcloud-Blöcke über Servergrenzen (Autor nur als Platzhalter). Vertrag für die Apps: `docs/APP-API-FED.md`.
- **Chat-Krypto `saga-chat/v2`:** Handle- und Geräte-Zertifikat v2, Nachrichten gebunden an die globale Chat-Kennung,
  den Absender-Handle und den mitsignierten KI-Hash, Empfänger `dev:<id>@host`/`backup:<handle>`, Sicherheitsnummer v2,
  Anrufe `saga-call/v2`. Gemeinsame Testvektoren `tests/vectors/chat_v2.json` (PyNaCl, Server-Referenz
  `saga/chat_v2.py`, libsodium.js). Lokale Chats bleiben unverändert `saga-chat/v1`.
  Neu im Chat-Objekt: `invited_by.remote` (eingeladen von einer Person dieses Servers: nur Ablehnen anbieten).
- `GET /api/share/targets` liefert das neue, optionale Feld `remote_hint` (nur ohne Treffer).

#### Seitenbaum, Datenbanken, Live (App-Verträge)

- **Seitenbaum-Abgleich:** `GET /api/notes?delta=1` liefert den Baum mit einem Stand (`cursor`), `?since=<cursor>`
  danach nur neue, geänderte und entfernte Seiten (Papierkorb, endgültig gelöscht), jede mit ihrer Position (`after`:
  hinter welcher Seite sie in der Liste steht). Passt der Stand nicht (zu alt, Neustart, Backup zurückgespielt), kommt
  der ganze Baum (`full: true`). Erfasst wird alles, was der Baum zeigt – auch Freigabe-Symbol und „neue Kommentare“.
  Der Baum enthält wie bisher nur eigene Seiten; „Mit mir geteilt“ kommt unverändert aus `/api/shared`. Schlank:
  `?rows=0` ohne Datenbank-Einträge (`rows=only`: nur diese), `?fields=…` nur die genannten Felder. `GET /api/notes`
  ohne Parameter liefert byte-gleich dasselbe wie bisher. Vertrag: `docs/APP-API-LIVE.md`, Abschnitt „Seitenbaum
  abgleichen“.
- **Live-Ereignis `notes.tree`** (Vertrag in `docs/APP-API-LIVE.md`): meldet Änderungen am Seitenbaum (neu,
  umbenannt/Symbol/Tags/Favorit/Sperre, verschoben, Papierkorb, wiederhergestellt, archiviert, endgültig gelöscht,
  Freigabe geändert) – nur IDs, an alle Geräte des Eigentümers und an Personen mit Freigabe, gesammelt höchstens eins je
  2 s. Apps können damit bei verbundenem Live-Kanal seltener abgleichen (5 min statt 60 s).
  - **Ausschluss je Tab:** Nur der Tab, der eine Änderung ausgelöst hat, bekommt sie nicht zurück – andere Tabs
    derselben Browser-Sitzung schon (Kennung je Tab: Kopf `X-Saga-Tab`, beim Live-Kanal `?tab=`). Das gilt auch für
    `note.changed`. Apps: je Gerät wie bisher.
  - **Verschieben** aus einem geteilten Teilbaum heraus oder hinein (auch per `PATCH parent_id` und beim
    Wiederherstellen auf die oberste Ebene): Empfänger der alten **und** der neuen Kette bekommen `notes.tree` und
    `shared.changed` – bisher nur die neue Kette. Papierkorb/Archiv: `shared.changed` erreicht jetzt auch Freigaben auf
    Seiten oberhalb.
  - **Gruppenwechsel** (Admin-Bereich, LDAP/OIDC-Abgleich bei der Anmeldung, Gruppe gelöscht): betroffene Konten
    bekommen `shared.changed` und `notes.tree` (ganzer Baum), wenn eine der Gruppen Ziel einer Freigabe ist; entprellt.
  - **Nachholen mit `Last-Event-ID`:** `notes.tree` wird gegen die aktuellen Rechte geprüft; ist eine fremde Seite nicht
    mehr sichtbar, kommt das Ereignis als „ganzen Baum laden“ ohne IDs.
- **`/rows` mit Ansicht, Filter, Sortierung und Seiten (opt-in, für Apps/Widgets):** `GET /api/notes/{id}/rows` und
  `GET /api/app/rows/{id}` verstehen `view`, `filters`, `sorts`, `q`, `group`, `limit`, `offset`, `tz`, `today` und
  liefern dann nur den Ausschnitt samt `total`, `matched` und `groups`. Ohne diese Parameter ist die Antwort
  unverändert. Der Server rechnet dabei exakt wie das Web (gleiche Vergleiche, Leeres unten, Zeichencode-Sortierung,
  „Berechnet“, Relationen-Titel nur, wenn der Betrachter sie sehen darf) – geprüft mit Testvektoren, die aus dem
  Web-Code selbst erzeugt werden (`tools/dbquery_vectors.mjs`). Nur Blättern ohne Filter läuft direkt in SQL
  (2.000 Zeilen: Seite à 100 in 15 ms statt 51 ms für alles); Widget „Ansicht, 8 Einträge“: 1,3 KB statt 136 KB
  (10.000 Zeilen: 1,3 KB statt 675 KB, Antwortzeit gleich). Bei `q=…` werden Einträge, deren Text den Suchbegriff
  sicher nicht enthält, gar nicht erst gelesen (wo das nicht sicher geht – Relationen, Suchbegriff mit Leerzeichen,
  Komma oder Sigma, nur Ziffern – wie bisher). Vertrag: `docs/APP-API.md`. Das Web lädt weiterhin alle Zeilen (Summen,
  Gruppen, Suche beim Tippen, Kalender, Relationen und die Seitenansicht brauchen sie).
- **ETag bei gzip:** gepackte Antworten tragen ein schwaches ETag (`W/"…"`); `If-None-Match` vergleicht schwach, 304
  funktioniert für gepackte und ungepackte Fassung gleich.
- **Nextcloud-Block und persönliches Backup** für Apps: `docs/APP-API.md` (Block-Format, Abschnitt „Persönliches
  Backup“).

#### Server intern

- **Suche** lädt und parst nicht mehr jede Seite: Saga leitet beim Speichern einen Suchtext ab (Titel, Klartext, Tags in
  Kleinbuchstaben) und sucht darin per `LIKE '%…%'` mit Trigramm-Index (`pg_trgm`, im Postgres-Image enthalten) –
  dieselbe Teilwort-Suche wie bisher, auch für Thai ohne Wortgrenzen, Russisch, Umlaute, `%`/`_` im Suchbegriff.
  Geprüft werden nur noch die Kandidaten, mit genau der bisherigen Trefferlogik (Ausschnitt, Rollen, in fremden Seiten
  nur der sichtbare Text). Ohne `pg_trgm` (eigener Postgres ohne Erweiterung) gleiches Ergebnis ohne Index.
- **Freigaben** (`/api/shared`, Startseite, Suche, Rückverweise, Export, Bilder in geteilten Seiten): Die sichtbaren
  Seiten kommen aus einer rekursiven Abfrage ab den Freigaben statt aus dem ganzen Seitenbaum der anderen Person – gleiche
  Rollen und Vererbung (Papierkorb, Archiv und Vorlagen unterbrechen wie bisher). Der Freigabe-Baum wird
  zwischengespeichert, solange sich an den Seiten der anderen Person nichts geändert hat (derselbe Änderungs-Zähler wie
  beim Seitenbaum-Abgleich: jede Änderung an Titel, Symbol, Ort, Papierkorb, Archiv, Vorlage, Art, Reihenfolge,
  Schlagworten oder Favorit verwirft den Eintrag sofort – auch aus Transaktionen, die beim Füllen noch liefen).
  Freigaben und Gruppen werden weiter bei jeder Anfrage frisch gelesen (Entzug wirkt sofort); nichts Unbestätigtes wird
  gemerkt; höchstens 5 Minuten und 150.000 Seiten.
- **Rückverweise** aus einer Verweis-Tabelle statt Volltext-Scan über alle Inhalte; **Datenbank-Zeilen** (`/rows`,
  Widget) mit gespeichertem Vorschaubild statt jeden Inhalt zu lesen; **Schlagworte** in Listen ohne erneutes JSON-Lesen
  leerer und wiederholter Listen.
- Abgeleitete Daten liegen in eigenen Tabellen (`note_derived`, `note_link`, `note_stale`); ein Trigger merkt jede
  Änderung an Titel, Inhalt, Format oder Tags vor, ein Hintergrund-Abgleich arbeitet sie ab. Für den Seitenbaum-Abgleich
  merkt ein Trigger je Seite die Transaktion der letzten baum-relevanten Änderung (Tabellen `note_tseq`,
  `note_tseq_meta`, Grabsteine gelöschter Seiten 30 Tage; nach dem Zurückspielen eines Backups laden Clients einmal ganz).
  Diese Tabellen brauchen keinen eigenen Schema-Stand.
- JSON-Antworten mit `orjson` (gleiche Ausgabe; nur sehr kleine Kommazahlen wie `1e-05` erscheinen als `0.00001`,
  derselbe Wert). Abschaltbar mit `SAGA_ORJSON=0`. Die Datenbank-Schicht wandelt Zeilen nur noch um, wenn eine Spalte
  vom Typ `numeric` ist (sonst kein Durchlauf über jeden Wert großer Listen).
- **Backups:** AWS-Signatur v4 ohne Zusatzbibliothek (hmac/hashlib, geprüft gegen die Beispiele der AWS-Dokumentation),
  jede Anfrage mit SHA-256 des Inhalts; S3-Auflisten mit Seiten (continuation-token), Löschen je Lauf, Holen für
  `backup-holen`/`restore --schluessel`/`backup-pruefen`. Die PROPFIND-Antwort wird mit Größengrenze gestreamt statt erst
  ganz geladen. Ziel-Schnittstelle in `saga/backups.py` nimmt eine eigene Freigabe-Regel (`ip_ok`); die
  Markdown-Erzeugung des Exports ist als `page_tools.md_page` wiederverwendbar. Persönliches Backup: Grenzen beim
  Einspielen werden beim Lesen geprüft (`daten.json` abgebrochen, ZIP-Einträge gezählt, bevor etwas geschrieben wird).
- **Nextcloud-Dateien:** Metadaten je Autor in einer WebDAV-SEARCH-Anfrage; Signatur je Seite, damit nur der Server den
  Autor eines Blocks setzt.

#### Datenbank

- **Migration 7** (Spalten in `fed_peer`, `fed_outbox`; Tabellen `fed_handle`, `fed_dir_local`, `fed_directory`,
  `fed_event_seen`, `fed_chain_in/out`, `fed_chat_keys`, `chat_fed_cert`, `chat_device_fed`), **Migration 8** (Chats und
  Anrufe über Servergrenzen, Ketten-Epoche, Geräte-Liste; Tabellen `fed_chat_block`, `chat_file_fed`), **Migration 9**
  (`file.fed_by`/`fed_note`; Tabellen `fed_name_handle`, `fed_group_members`). Vor der Migration legt Saga wie immer ein
  Backup an (`…-vor-schema-9.dump`); ältere Versionen starten danach nicht mehr auf dieser Datenbank (Backup
  zurückspielen, siehe „Hinweise zum Update“).
- Neue Tabellen ohne Migration (`CREATE TABLE IF NOT EXISTS`): `chat_invite` (wer in eine Gruppe eingeladen hat),
  `backup_run` (Verlauf der Server-Backups), `user_backup`, `user_backup_run` (persönliche Backups), `nc_account`
  (Nextcloud-Verknüpfung), dazu die abgeleiteten Tabellen oben. Kontolöschung entfernt die persönlichen Einträge.

#### Tests und Werkzeuge

- **Föderation:** Reihen `foederation2`, `foederation_chat`, `foederation_teilen`, `foederation_gruppen` (drei Server,
  mit `FED_B_APP` auch gemischt mit 2.1/2.2), `chat_v2_vektoren` und `foederation_boese`: ein dritter Server verhält sich
  protokollwidrig (gefälschte Absender und Signaturen, Replays, Lücken, Geister-Mitglieder, Schlüsseltausch, Fluten,
  übergroße und langsame Antworten, ID-Tricks, Rückfall auf Protokoll 1, gefälschtes Adressbuch) – alles abgewiesen
  bzw. sichtbar, ohne Serverfehler, ohne andere Server aufzuhalten; ebenfalls mit `FED_B_APP`. Browser-Tests
  `tests/browser_fed_chat.js` und `tests/browser_fed_share.js`.
- **Backups:** Reihe `backup_extern` gegen ein nachgemachtes Nextcloud/WebDAV (`tests/fakedav.py`) und einen
  nachgemachten S3-Server (`tests/fakes3.py`: prüft Signatur v4 unabhängig nach, SHA-256 des Inhalts, Multipart mit
  Mindestgröße je Teil, Seiten beim Auflisten, virtual-hosted) sowie ein Ordner-Ziel mit Ausbruchsversuchen; Reihe
  `backup_persoenlich`; `tests/sicherung.py` (Zurückspielen mit `postgres:16`/`:17`, alte Backups).
- **Nextcloud-Dateien:** Reihe `nextcloud_dateien` (`tests/fakedav.py` mit PROPFIND/SEARCH/Vorschau).
- **Leistung:** `tests/browser_db.js` (lange Datenbanken), `tests/baum_cache.py` (Zwischenspeicher nach jeder Art von
  Änderung, laufende und offene Transaktionen, Rollback, REPEATABLE READ), `tests/zeilen_suche.py` (mit/ohne Vorfilter
  über die Web-Testvektoren, Zufallsdaten in de/ru/th, türkisches I, Schluss-Sigma, Escapes, Zahlen; per HTTP
  byte-gleich).
- **Lastmessung `tools/perf/`** (nur Werkzeuge für Test-Stacks): `gen.py` erzeugt realistische Testdaten in drei Stufen
  (S: 500 Seiten · M: 5.000 Seiten + 20.000 Datenbank-Einträge · L: 10.000 Seiten + 50.000 Einträge, eine Datenbank
  mit 10.000 Einträgen, Baum bis Tiefe 6, Bilder, Kommentare, Freigaben an ein zweites Konto, Chat mit 5.000
  Nachrichten) direkt in Postgres und wartet danach auf den Abgleich. `bench.py` misst Antwortzeit (p50/p95) und
  übertragene Größe (mit gzip, entpackt selbst) der wichtigsten Endpunkte – auch `delta=1`, `rows=0`, `since` und „neuer
  Eintrag + Abgleich“ –, die Netzwerk-Abläufe der Apps, parallele Last (auch mit 2.2-Clients), den Live-Kanal sowie
  CPU/RAM der Container; `explain.py` zeigt die Pläne der teuersten Abfragen (auch der neuen); `web.py` misst die
  Weboberfläche mit Playwright (headless): Baum neu laden, „erste Zeile sichtbar“, „danach bedienbar“, „ans Ende
  scrollen“, zählt Zeilen virtualisierter Tabellen über `data-rows`, läuft auch gegen Versionen vor 2.2
  (`SAGA_PERF_WEB_ARGS`). `tools/perf/run.sh` führt alles je Stufe aus und prüft Grenzwerte aus
  `tools/perf/limits.json` (Exit 1 bei Überschreitung), z. B. `SAGA_SCRIPT=perf/run.sh tools/test-stack.sh "S M"`. Nur
  für Test-Stacks: legt die Konten `perf-a`/`perf-b` neu an und setzt `NOTE_MAX_COUNT=200000` in der `.env` des
  Test-Stacks.

### Hinweise zum Update

- **Update:** Mit `SAGA_VERSION=2` in der `.env` reicht `docker compose pull && docker compose up -d`. Von 2.1 kommend
  gibt es keine Zwischenschritte (2.2 wurde nie veröffentlicht).
- **Datenbank:** 2.3 hebt die Datenbank von Schema-Stand 6 auf 9 (Migrationen 7–9) und legt vorher automatisch ein
  Backup an (`/data/backups/saga-…-vor-schema-9.dump`). Wer ganz sicher gehen will, sichert vorher zusätzlich selbst
  (`docker compose exec saga python run.py backup --dateien`). Beim ersten Start gleicht Saga einmal alle Seiten ab
  (etwa 1–2 s je 1.000 Seiten, im Hintergrund).
- **Kein Rückweg auf 2.1 ohne Backup:** 2.1 startet auf Schema-Stand 9 nicht mehr. Zurück nur über das Backup von vor
  dem Update – und zwar **mit 2.3 zurückspielen, dann** `SAGA_VERSION` ändern (2.1 scheitert beim Zurückspielen mit
  `postgres:16`): `docker compose stop saga`, `docker compose run --rm saga python run.py restore
  /data/backups/<…-vor-schema-9.dump>`, dann `SAGA_VERSION=2.1` und `docker compose pull && docker compose up -d`.
  Alles nach dem Backup Geänderte ist dann weg. Schon gewechselt? Die neuere Version nur zum Zurückspielen nehmen
  oder vorher `DROP TABLE IF EXISTS note_link, note_stale, note_derived` (Anleitung, Abschnitt 11 „Updates“).
- **Föderation:** Verbundene Server mit 2.3 schalten Chat, Anrufe und **Adressbuch** je Server standardmäßig ein –
  Anzeigenamen und Profilbilder eurer Personen werden dann an diese Server übertragen. Wer das nicht will, schaltet es
  vor oder direkt nach dem Update unter Admin → Föderation → „Funktionen je Instanz“ ab; Personen können unter
  Einstellungen → Konto widersprechen. Datenschutzerklärung prüfen (Anleitung, „Für Admins: Überblick, Datenschutz,
  Fehlerbilder“).
- **Nextcloud-Dateien in Seiten** und **persönliche Backups** sind standardmäßig erlaubt (nur öffentliche https-Adressen,
  bis der Admin interne Hosts freigibt); abschaltbar unter Allgemein → „Verbindungen der Benutzer“ bzw. Backups →
  „Persönliche Backups“. Das Server-Backup ist aus, bis du ein Ziel einrichtest.
- **Neue Einstellungen der `.env`** (alle optional, Anleitung Abschnitt 13): `FED_*` (u. a. `FED_UPLOAD_QUOTA_MB`,
  `FED_WATCH_TTL`, `FED_EXPRESS_TIMEOUT`, `FED_PEER_SLOTS`, `FED_OUT_WORKERS`, `FED_CHAT_IN_RATE`, `FED_CHAT_CACHE_MB`,
  `FED_CHAT_CACHE_DAYS`), `SAGA_BACKUP_*` (nur für die Wiederherstellung auf einem frischen Server und
  `SAGA_BACKUP_DIRS`), `BACKUP_VERIFY_MB`, `BACKUP_CHUNK_MB`, `USER_BACKUP_*`, `NC_*`, `SAGA_RESTORE_TEST`,
  `GZIP_LEVEL`/`GZIP_MIN_BYTES`, `NOTE_ROW_MAX_COUNT`, `SAGA_ORJSON`.
- **Apps 2.3 empfohlen** (Föderations-Chats und -Anrufe, Teilen über Servergrenzen, Mein Backup):
  [Saga-Apps](https://github.com/yggdrasil-sh/saga-apps/releases). Apps 2.1 laufen weiter, nur ohne diese Funktionen.
- **Ordner-Ziel für Backups:** braucht ein zusätzliches Volume in der `docker-compose.yml` (Beispiel in der Anleitung,
  Abschnitt 11).
- **Release Candidates:** Wer `SAGA_VERSION=testing` nutzt, sieht jetzt `2.x.y-rc.N`; `rc` geht als Kanal ebenso.

### Bekannte Grenzen

- Chats, Anrufe, Adressbuch und vollständige Seiten über Servergrenzen nur, wenn **beide** Server 2.3 haben; mit 2.1
  bleibt es bei Teilen, Kommentaren und Benachrichtigungen.
- Über Servergrenzen nicht möglich: Seiten löschen, archivieren, veröffentlichen, Freigaben verwalten, Rückverweise,
  KI auf der Seite. Anrufe weiterhin nur zu zweit.
- Die Apps suchen Personen vorerst erst ab 3 Zeichen.
- Die Browsersuche (Strg+F) findet in langen Datenbank-Tabellen nur Zeilen im sichtbaren Ausschnitt.
- Persönliche Backups enthalten keine Chats und keine Seiten anderer; Kommentare und Einstellungen werden beim
  Einspielen nicht übernommen.

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
  Eigentümer); alle Mitglieder sehen einen Hinweis. Die KI liest **nur Nachrichten mit `@Name`** und bis zu 12 Nachrichten
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
  den Dialog; Untermenüs bleiben im Peek sichtbar; veröffentlichte Seiten ohne „`@name`“ oben; Chat-Platzhalter auf dem
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
  „KI im Chat“ gehen die Nachricht mit `@Name` und bis zu 12 davor im Klartext an den KI-Endpunkt, bei der Websuche die
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
    Sprache des Besuchers; Absenderzeile „`@name` · aktualisiert <Datum>“; Lesebreite ~70 Zeichen (Bilder, Tabellen,
    Datenbanken, Spalten breiter); Leiste mit `@name` bzw. Name der Instanz, „Anmelden“ als ruhiger Textlink;
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
