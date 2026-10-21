# Feldtest mit zwei Servern – Gruppenanrufe über Gjallarhorn-Knoten (Saga 2.5)

Diese Anleitung beschreibt einen Feldtest mit **zwei eigenständigen Saga-Servern** (A und B), deren Personen
gemeinsam große Gruppenanrufe führen. Beide Server betreiben ihre eigenen Konten; die Anrufe laufen über
Gjallarhorn-Knoten. Platzhalter: `saga-a.example.org`, `saga-b.example.org`, Knoten `horn-a1.example.org`,
`horn-b1.example.org`.

Getestet werden drei Fälle:

| Fall | B hat einen Knoten | A teilt seinen Knoten mit B | Ergebnis |
|---|---|---|---|
| **a – Kaskade** | ja | egal (aus) | Personen von A am Knoten von A, Personen von B am Knoten von B; die beiden Knoten verbinden sich („Bifröst“, TLS auf 443) |
| **b – Teilen** | nein | ja | Personen von B verbinden sich **direkt** mit dem Knoten von A (an das Kontingent für B gebunden) |
| **c – ohne** | nein | nein | Anruf läuft ohne Knoten direkt zwischen den Geräten (höchstens 4 mit Video bzw. 6 nur mit Ton) – mit klarer Meldung |

Der Server, in dessen Chat der Anruf beginnt (das **Heim** des Chats), führt ihn und koordiniert die Knoten.

## 1. Voraussetzungen

- Beide Server auf demselben 2.5-Stand (`testing`, Release-Kandidat). Vor dem Update ein Backup – ein Zurück auf 2.4
  geht nur mit dem Backup von vorher.
- Die Server sind verbunden (Admin-Bereich → **Föderation**, Einladungscode, beidseitig bestätigt) und dort ist für
  den jeweils anderen **Anrufe** eingeschaltet.
- Je Server mindestens ein Knoten, der **443/TCP von außen** erreicht (für Fall a auch auf B):
  - der **eingebaute Knoten** mit eigenem Namen und TLS-Durchreiche (SNI) am Reverse Proxy auf `HORN_PORT` (Standard 8443),
  - oder ein weiterer Knoten über **Erweitern** (Admin → Anrufe), z. B. auf einem kleinen vServer.
- **Ausgehend** muss jeder Knoten 443/TCP zu Saga **und zu den anderen Knoten** öffnen dürfen (die Kaskade wählt den
  Knoten des anderen Servers an). 443/UDP ist optional.
- Browser: Chrome/Edge oder Firefox aktuell; Safari geht über den TLS-Weg. Apps folgen mit ihrem 2.5-Release.

## 2. Einschalten (beide Server)

1. Admin → **Anrufe** → „Gjallarhorn – große Gruppenanrufe“ einschalten. Der eingebaute Knoten startet, sofern
   „Eingebauten Knoten für Anrufe nutzen“ an ist.
2. In der Knotentabelle muss jeder Knoten **Bereit** zeigen (Port-Prüfung grün, Zertifikat vorhanden).
3. Admin → Anrufe → **„Fähigkeiten jetzt abgleichen“**. Damit erfährt der andere Server sofort, dass hier Knoten-Anrufe
   gehen (sonst erst beim nächsten Abgleich, bis zu 6 h). Unter Föderation steht danach bei den gemeinsamen Fähigkeiten
   `horn`.

## 3. Teilen einstellen (Server A)

- Admin → Anrufe → in der Knotentabelle **„Teilen an“** beim Knoten, den B mitnutzen darf. Standard ist aus.
- Admin → **Föderation** → Abschnitt „Gjallarhorn-Knoten für B“:
  - **Knoten mit diesem Server teilen erlaubt** (Erlaubt-Liste; Standard: alle verbundenen Server),
  - **Teilnehmende** und **Mbit/s je Richtung** (leer = Standard aus Admin → Anrufe: 10 Teilnehmende, 20 Mbit/s),
  - **Notaus**: trennt sofort alle Teilnehmenden und Kaskaden von B von deinen Knoten.
- Hinweis Auftragsverarbeitung: Läuft der geteilte Knoten bei einem Anbieter, verarbeitet dieser auch IP-Adressen der
  Personen von B.
- Nach dem Umschalten bekommt B die Adresse des geteilten Knotens automatisch (Abgleich im Hintergrund). Personen von B
  laden die Saga-Seite danach **einmal neu**, damit der Browser den Knoten ansprechen darf.

## 4. Ablauf der Fälle

Für jeden Fall: eine freie Gruppe auf A mit mindestens drei Personen von A und drei von B (B nimmt die Einladung an).
Über Knoten läuft ein Gruppenanruf, sobald der Chat mehr Mitglieder hat als die Mesh-Grenze (Video: ab 5).

**Fall a – Kaskade** (B hat einen bereiten Knoten, A teilt nicht):
1. Eine Person von A startet den Videoanruf, die anderen treten bei.
2. Erwartung: Personen von A sind am Knoten von A, Personen von B am Knoten von B (Weg-Anzeige im Anruf, Knotenname im Tooltip).
3. Admin → Anrufe auf A und B: beim jeweiligen Knoten erscheint **„Kaskaden: 1“**.
4. Alle sehen und hören alle. Wer spricht, bekommt den Sprechring bei allen.

**Fall b – Teilen** (Knoten von B im Admin-Bereich „Leeren“ oder ausschalten, A teilt):
1. Personen von B laden die Seite neu, dann neuer Anruf wie oben.
2. Erwartung: alle sechs sind am Knoten von A. Keine Kaskade.
3. Kontingent prüfen: „Teilnehmende“ für B auf 2 setzen – die dritte Person von B bekommt beim Beitreten die Meldung
   „Das Kontingent … ist erschöpft“. Danach zurücksetzen.
4. Notaus prüfen: Notaus für B an – Personen von B fallen sofort aus dem Anruf, ein erneuter Beitritt bekommt keinen
   Knoten. Notaus wieder aus.

**Fall c – ohne Knoten** (B ohne bereiten Knoten, A teilt nicht):
1. Neuer Anruf. Erwartung: Meldung „Teilnehmende von saga-b.example.org können keinen Gjallarhorn-Knoten nutzen …“;
   der Anruf läuft direkt zwischen den Geräten (höchstens 4 mit Video).

## 5. Was ich mir anschaue (bitte notieren)

- Zeit vom Klick bis zum Bild, Ton- und Bildqualität je Fall, Abbrüche.
- Admin → Anrufe: Auslastung, Upload, „Kaskaden“, bei geteilten Knoten die Zahl der Teilnehmenden von B.
- Bei Problemen in der Anrufinfo das Protokoll (Weg, Knoten, Fehler) und im Admin-Bereich das Knoten-Protokoll.
- Knoten-Log (`docker compose logs gjallarhorn`): Zeilen `bifroest …` (Kaskade auf/zu, Grund) und `kontingent …`.

## 6. Fehlerbehebung

| Beobachtung | Ursache / Abhilfe |
|---|---|
| Anruf startet ohne Knoten, obwohl beide Knoten haben | `horn` fehlt in den gemeinsamen Fähigkeiten → „Fähigkeiten jetzt abgleichen“ auf beiden Servern; Knoten „Bereit“? |
| Personen von B: „Kein Knoten erreichbar“ trotz Teilen | Seite nach dem Einschalten von „Teilen“ neu laden (Adresse des Knotens); Erlaubt-Liste und Notaus prüfen |
| Fall a: jede Seite sieht nur die eigenen Personen | Kaskade steht nicht: ausgehend 443/TCP vom Knoten B zum Knoten A erlaubt? Knoten-Log `bifroest … abgelehnt`/`lesen:` |
| „Das Kontingent … ist erschöpft“ | Admin → Föderation → Teilnehmende für B erhöhen |
| Bild ruckelt nur bei Personen von B (Teilen) | Mbit/s-Kontingent zu klein (über dem Kontingent verwirft der Knoten Bild, Ton nie) |

## 7. Sicherheit und Datenschutz in Kürze

- Ton und Bild sind Ende-zu-Ende-verschlüsselt (Frame-E2E). Knoten – auch der geteilte Knoten des anderen Servers und
  die Kaskade zwischen den Knoten – leiten nur Chiffrat weiter.
- Knoten sehen IP-Adressen und zufällige Kennungen, keine Namen. Beim Teilen sieht der Knoten von A die IP-Adressen der
  Personen von B (deshalb der Hinweis auf Auftragsverarbeitung).
- Die Kaskade ist an Saga-signierte Token (Anruf, beide Knoten, 60 s) und die Knotenschlüssel gebunden; Notaus beendet
  sie sofort.
