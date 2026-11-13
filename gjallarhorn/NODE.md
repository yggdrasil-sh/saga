# Gjallarhorn-Knoten (Betriebsart `node`) – Stand P1 + P2 + P3 + P5 + P6/P7 + P9/P10

Der Knoten ist der Weiterleiter (SFU) für große Saga-Gruppenanrufe (Konzept v2, §1, §2.1). `gjallarhorn node` nutzt
dieselben Weichen wie die Horn-Probe. Statt des Spiegels arbeitet der echte Weiterleiter aus `internal/sfu`. Beine
gibt es nur mit einem Medien-Token von Saga (P3, unten). Zum Testen ohne Saga gibt es `HORN_DEV=1`.

Platzhalter: `horn-1.example.org`.

## Weichen (P1)

| Port | erstes Byte | Ziel |
|---|---|---|
| 443/TCP | `0x16` | TLS: ALPN `http/1.1` → HTTPS/WSS (Steuerung, `/probe`), ALPN `stun.turn` oder STUN im Strom → TURN-TLS (Relay nur zum Knoten selbst) |
| 443/TCP | `0x00`/`0x01` | ICE-TCP (RFC 4571) → eigener `netx.ICEMux` (keine Allokation je Paket) |
| 443/TCP | `\r` bzw. `P` | PROXY-Kopf v2 bzw. v1 (nur mit `HORN_PROXY_PROTOCOL=1` oder `auto`) |
| 443/UDP (optional) | RFC 9443 | STUN/DTLS/RTP an pion. Einfache STUN-Bindings ohne USERNAME beantwortet die Weiche selbst (Erreichbarkeitsprüfung, höchstens 200/s) |

- Selbsttest beim Start: Bindings an die öffentlichen Adressen, sonst lokal. Das Ergebnis steht in
  `/api/session` → `udp_check`. Die Gegenprobe von außen macht Saga beim Koppeln (P3) über denselben Beantworter.
- Höchstens 512 unzugeordnete Verbindungen. Fristen: erstes Byte 5 s, TLS-Handshake 10 s.
- TCP: `TCP_NODELAY`, `TCP_NOTSENT_LOWAT` 16 KB, BBR wenn erlaubt.
- **Nachläufer** gegen lange TCP-Staus (`HORN_TAILPROBE_MS`, Standard 10, `0` = aus): Geht das letzte Segment eines
  Schubs verloren, merkt Linux das erst über die Tail-Loss-Probe nach 2 RTT, bei nur einem Segment unterwegs nach
  2 RTT + 200 ms. Der Knoten schickt deshalb 10 ms nach dem Schub eine STUN Binding Indication (28 Byte). Damit
  erkennt RACK den Verlust schon nach etwa 1,25 RTT.
  - Der Nachläufer läuft nur auf Verbindungen mit Wiederholungen in den letzten 30 s und einer RTT ab 20 ms
    (`TCP_INFO`). Auf sauberen Wegen kostet er also nichts.
  - Auf TURN-TLS beginnt er erst nach dem ersten ChannelData.

## Weiterleiter (P2)

- **Beine und Rollen:** Auf TCP-Wegen hat ein Gerät zwei Beine (H1): die Ton-Leitung (`role=audio`) und die
  Bild-Leitung (`role=video`). Auf UDP ist es ein Bein (`both`) mit BUNDLE. Je `leg_ref` gibt es ein Bein; ein
  zweites ersetzt das erste.
- **Empfangsplätze:** Die Reihenfolge der recvonly-Transceiver im Angebot ist fest: erst Ton (höchstens 3), dann
  Sprecher, dann Bildschirm (falls angefragt), dann Kacheln. Last-N kappt die Kacheln: Desktop 9, Handy 4.
  - Die Geräteklasse kommt aus dem Token. Der Hinweis `device=mobile` des Clients kann sie nur senken.
- **Wer ist wo:** Jedes Paket trägt die CSRC des Absenders. Browser lesen sie per `receiver.getContributingSources()`
  bzw. im Encoded-Transform aus `getMetadata().contributingSources`. Die Zuordnung CSRC → Teilnehmende liefert die
  Steuerung (`Room.Peers`, `Config.OnLayout`).
- **Top-3-Ton** per `ssrc-audio-level`. Der Hauptsprecher wird mit Hysterese gewählt (mindestens 1,5 s, ein Neuer
  muss 1,5 s am lautesten oder 1,4-mal lauter sein). Kacheln sind nach letzter Sprache geordnet, die Plätze bleiben fest.
- **Schichten:**
  - UDP: bis zu 3 Schichten (`l`/`m`/`h`).
  - TCP: höchstens 2.
  - Kacheln auf TCP: nur die untere Schicht und höchstens 10 Bilder/s. Dafür lässt der Knoten zeitliche Schichten
    (TID) weg; PictureID und TL0PICIDX bleiben lückenlos.
  - Der Sprecher bekommt mehr, wenn die Schätzung reicht.
  - Ist zu wenig Rate da, werden Kacheln von hinten pausiert. Nach oben geht es nur mit 15 % Reserve und nach einer
    Sperrzeit, die sich bei Fehlversuchen verdoppelt (2 bis 30 s).
  - Hysterese gegen Pendeln (Horn-Fix, `layerStep`): Ohne Stau bleibt die laufende Schicht bis 10 % über der Schätzung
    (kurze Ratenspitzen, z. B. Schlüsselbilder, schalten nicht herunter); bei Stau (Sperrzeit) sofort herunter – so
    weit, wie die Schätzung es verlangt (von `h` auf `m`, wenn sie `m` trägt; früher immer bis `l`). Nach
    einem Wechsel nach unten geht derselbe Platz für dieselbe Quelle frühestens nach 4 s wieder hoch.
    Fällt ein Platz binnen 2 s nach dem Hochschalten wegen des Budgets wieder zurück (Rate der Zielschicht
    unterschätzt, z. B. kurz nach einer Auflösungsänderung des Senders), verdoppelt sich dieser Aufschub je
    Fehlversuch (8, 16, 32 s … höchstens 5 min – auf einer Leitung zwischen zwei Schichten bleibt so am Ende ein
    Versuch je 5 min statt einer alle 32 s). Jede Schicht hat ihren eigenen Aufschub (`slot.dw`): Fällt der
    Platz nach gescheiterten `h`-Versuchen später tiefer (Einbruch `m` → `l`), darf er nach 4 s zurück auf `m`; ein
    dabei gescheiterter `m`-Versuch beginnt bei 8 s und erbt nicht den Aufschub von `h`, dessen Uhr weiterläuft
    (`TestSchaetzerAufschubJeSchicht`). Weg ist der Aufschub einer Schicht (und aller tieferen), sobald sie 30 s
    ohne Pause gehalten hat – unabhängig davon, warum sie später endet (`TestSchaetzerQuelleWeg`). Hielt ein
    Versuch länger als 2 s und fällt dann wegen des Budgets zurück, ist das kein Fehlversuch: Der Aufschub wird auf
    höchstens 32 s gedeckelt, aber nicht verkürzt – sonst begänne bei abwechselnd frühem und spätem Scheitern alles
    von vorn (`TestBudgetFehlversuchAufschub`, `TestSchaetzerAufschubZurueck`, `TestSchaetzerWechselndesScheitern`). Auf UDP zählt zusätzlich eine gescheiterte
    Schonfrist als Fehlversuch (siehe unten).
  - UDP: Je Takt geht höchstens ein Platz eine Schicht höher (derselbe Platz darf mehrere Schichten nehmen); jeder
    Schritt ist eine Probe der Leitung, und ein Stau danach soll einem Schritt zuzuordnen sein. Kacheln, die aus der
    Pause dazukommen, zählen nicht mit (`TestSchaetzerGruppeEinSchritt`).
  - Hysterese gegen flatternde Schichten (`internal/sfu/flap.go`, je Quelle, damit für eigene Beine und Brücken
    gleich): Senkt der Sender eine Schicht selbst (libwebrtc bei CPU-Last/Wärme) und liefert sie nur noch zeitweise,
    fällt sie sofort aus der Auswahl, sobald sie nicht mehr live ist (keine Pakete in der letzten Sekunde bzw. über
    die Brücke „aus“ gemeldet), während eine tiefere live ist. Wieder genommen wird sie erst nach **3 s am Stück
    live**; flattert sie, bevor sie nach der Rückkehr 30 s stabil war, verdoppelt sich die Wartezeit (bis 60 s), nach
    30 s Stabilität beginnt sie wieder bei 3 s. Fallen alle Schichten zugleich weg (Sender, Netz, Freigabe
    pausiert), gibt es keine Wartezeit. Budget-Wechsel (Schätzung, Kontingent) bleiben bei `layerStep`. Die Brücke
    meldet `on` ungefiltert; die Hysterese wirkt am Knoten, der die Schicht wählt.
  - PLI-Bremse: höchstens eine Anforderung je Quelle und `fwd.PLIInterval` (500 ms), für alle Empfänger zusammen;
    Abo-Wechsel (`Out.Assign`, auch über die Brücke: `pli` bzw. neuer Ausgang auf der Gegenseite) laufen durch
    dieselbe Bremse (`TestPLIAbstandBeiWechsel`).
  - Grund je Wechsel im Ereignis `layout`: Slots tragen nur in der Meldung des Wechsels selbst `why`
    (`budget` | `gone` = bisherige Schicht nicht mehr live | `back` = Rückkehr nach der Hysterese), `src_kbit`
    (gemessene Rate der Quelle; `gone`: der weggefallenen, sonst der neuen), `live_ms` (`gone`: Live-Zeit vor dem
    Ausfall, `back`: am Stück) und `hold_ms` (`gone`: Wartezeit bis zur Rückkehr, `back`: die abgewartete). Alte
    Geräte ignorieren die Felder. Das Web hängt sie an den Platz im Diagnose-Ereignis `layers` an
    (`s0:l3@ab12!gone,31k,12.4s,w3s`).
- **Lebensdauer der Beine (Horn-Fix):** Beine der Betriebsart `node` leben so lange wie der Anruf. Geräumt wird nur,
  was gescheitert/geschlossen ist (nach 30 s) bzw. nie verbunden wurde (nach 60 s). Die frühere Grenze von 15 min
  gilt nur noch für Spiegel-/Bench-Beine. Jedes Bein schreibt `bein auf`/`bein zu` ins Protokoll – gekürzte ID,
  Betriebsart, Weg, Dauer, die Zähler des Abgleichs auf Doppel (siehe unten) und zuletzt der Grund
  (`aufraeumer: alter|gescheitert|nie verbunden`, `verbindung failed|closed`, `knoten: …`, `abgemeldet`,
  `knoten beendet`, `aufbau fehlgeschlagen`); keine Adressen, kein ufrag.
- **Schätzung je Bein:**
  - TCP: aus dem Alter der eigenen Warteschlange. Dank `TCP_NOTSENT_LOWAT` liegt der Stau im Knoten.
  - UDP: zusätzlich GCC (TWCC), als Stausignal. Ohne Probepakete ist GCC anwendungsbegrenzt und taugt nicht als
    Obergrenze. Stau ist ein GCC-Ziel unter 90 % der gesendeten Rate – außer es läuft der Rate nur nach
    (`estimator.lagging`, `internal/sfu/control.go`). pion-GCC senkt sein Ziel nur bei Stau (Verzögerung: auf 85 % der
    empfangenen Rate; Verlust über 10 %) und hebt es sonst mit rund 8 %/s. Springt die gesendete Rate (höhere
    Schicht, Kachel dazu, Schlüsselbild), liegt ein stehendes oder steigendes Ziel deshalb eine Weile darunter,
    ohne dass die Leitung eng wäre. Diese **Schonfrist** gilt nur, solange das Ziel nicht fällt (weder im Takt des
    Ratensprungs noch danach um mehr als 3 % unter seinen Höchstwert), je Sekunde um mindestens 1,5 % steigt,
    insgesamt höchstens 15 s lang und nicht während einer Sperrzeit; sonst zählt das Ziel wie bisher als Stau.
    Solange sie läuft, kommt nichts Neues dazu (kein weiterer Schritt nach oben).
    Scheitert sie, fällt die Schätzung 2 s lang nicht unter die Rate vor dem Ratensprung – aber nur, wenn die Folge
    mit einem Ratensprung begann (Rate über 1,1 × ihres geglätteten Werts). Bricht die Leitung bei gleichbleibender
    Rate ein, folgt die Schätzung dem Ziel sofort.
  - UDP, Deckel: Die freie Schätzung wächst höchstens bis `1,6 × gesendete Rate + 300 kbit/s` (mindestens der
    Startwert) – und auf UDP mindestens bis zum Bedarf des nächsten Schritts nach oben (bzw. der laufenden Auswahl)
    mit 15 % Reserve und 5 % Zuschlag, zuzüglich des Tonabzugs, höchstens das Kontingent. Der Wechsel selbst ist
    die Probe; scheitert er, meldet GCC das. Ohne diese Regel blieb ein Empfänger im 1:1 auf `m` (≈ 500 kbit/s →
    Deckel 1200 kbit/s; Browser-`h` braucht mit Reserve ≈ 1430 kbit/s). TCP behält den alten Deckel.
  - UDP, nach oben: nur, wenn das GCC-Ziel mindestens 1,25 × der gesendeten Rate ist. Nach einem Rücksprung steigt
    das Ziel auch bei anhaltendem Stau zunächst wieder (der Verlust-Teil holt mit 5 % je 200 ms zum
    Verzögerungs-Teil auf); erst von einem erholten Ziel aus ist ein Stau nach dem Schritt als Fall erkennbar.
  - UDP, Anrufbeginn: `h` geht mit der ersten Messung der Quelle durch (sie beginnt mit einem Teil-Takt, der Kodierer
    fährt erst hoch). Die zwischenzeitliche Anlauf-Regel (`srcSettle`: Schritt hinauf auf eine junge Quelle mit der
    angenommenen Rate, `h` erst nach rund 2 s; seit Stufe 3 nur bei `eigen`, Test-Einstellung `HORN_ANLAUF`) ist
    **seit Stufe 3b gestrichen** – im Labor brachte sie weder bei `gcc` (`labbwe2`: Rückfälle zum Anrufbeginn 5 gegen
    4 von 32) noch bei `eigen` (`labbwe3`: mit Regel 1 von 24 Richtungen, ohne 0 von 24) etwas Messbares, kostete aber
    rund 2 s bis `h` und nach „Kamera an“. Der Anrufbeginn plant bei `gcc` und `eigen` wieder wie am Stand `22a5bd3`
    (im Modell ohne Probing Takt für Takt gleich).
  - UDP, gescheiterter Versuch: Endet die Schonfrist nach einem Hochschalten mit Stau, zählt der Rückfall als
    Fehlversuch (Aufschub 8/16/32 s … bis 5 min, siehe oben), auch wenn er später als 2 s kommt. Die Schätzung fällt dabei 2 s
    lang nicht unter die Rate vor dem Versuch – der erste Rücksprung des Ziels (85 % eines Messfensters, das noch
    zur Hälfte die alte Rate enthält) sagt nichts über die Stufe davor.
  - Tests: `internal/sfu/schaetzer_test.go` (freie Leitung, enge Leitung, Stau-Schub, Einzelregeln), mit einem
    Modell des GCC-Ziels nach Messwerten am echten `gcc.SendSideBWE`.
- **Probing mit Fülldaten (experimentell, `HORN_PROBING`, Standard an; `HORN_PROBING=0` = aus):**
  - Wozu: Ohne Proben ist der Schichtwechsel selbst die Probe. Scheitert er, kostet das ein Schlüsselbild, den
    Rückwechsel und kurz verdeckten Ton; der Aufschub begrenzt nur, wie oft. Mit Probing füllt der Knoten die Leitung
    vor einem Schritt nach oben kurz mit wertlosen RTP-Padding-Paketen auf die Rate, die der Schritt bräuchte. Trägt
    die Leitung das ohne Stausignal, wird der Schritt freigegeben; sonst bleibt alles, wie es ist.
  - Für wen: nur UDP-Beine mit GCC. TCP-/TLS-Beine und der Schalter „aus“ verhalten sich exakt wie oben beschrieben.
  - Auslöser: Ein Schritt nach oben (auch Bildschirm, auch eine Kachel aus der Pause) scheitert nur an der Schätzung
    – keine Sperrzeit, keine laufende Schonfrist, GCC-Ziel mindestens 1,25 × der Rate, die Bild-Auswahl des Beins
    seit 4 s unverändert (vor der ersten Probe eines Beins: 2 s), die Quelle des Füll-Ausgangs sendet. Höchstens
    eine Probe je Bein. Der Deckel der Schätzung folgt mit Probing nicht mehr dem Bedarf des nächsten Schritts (er
    bleibt bei `1,6 × Rate + 300 kbit/s`, mindestens der Startwert von 1,2 Mbit/s); was darüber liegt, braucht eine
    bestandene Probe. Schritte darunter gehen weiter ohne Probe.
  - **Freier Versuch (ohne Probe, zeitlich wie ohne Probing)** – in zwei Fällen, jeweils einmal:
    - *Anrufbeginn:* Der erste Schritt nach oben eines Beins, den die Schätzung allein nicht hergibt. Es gibt noch
      keine Vorgeschichte, die eine Probe rechtfertigt, und die Probe kostete gut 4 s (Labor: `h` stabil nach 12,8 s
      statt 8,8 s). Verbraucht mit dem Versuch, mit der ersten gescheiterten Probe und mit dem ersten Rückfall eines
      Platzes. Nicht als Rückfall zählt, wenn die Schicht beim Schritt hinauf noch nicht eingeschwungen war (sie
      sendet inzwischen mehr als das 1,3-Fache – zu Anrufbeginn die Regel: `h` geht mit 300 kbit/s durch und fällt,
      sobald der Kodierer hochgefahren ist); es bleibt dann beim Aufschub wie ohne Probing.
      Bis dieser Versuch verbraucht ist (Anlauf), plant das Bein genau wie ohne Probing: Der Deckel der Schätzung
      folgt dem Bedarf der laufenden Auswahl, Schonfrist und Aufschub wie dort, keine Proben (Labor, Runde 3: mit
      Probing stand die Schätzung beim ersten Schritt auf dem Anfangswert, `h` hielt seltener – stabil ab 8,4 s statt 3,0 s).
    - *Rückkehr:* Fällt ein Platz von einer Schicht zurück, die er mindestens 30 s gehalten hat, darf er einmal ohne
      Probe wieder hinauf (nach 4 s Mindestverweildauer, die Schätzung wächst bis zum Bedarf des Schritts). Hält die
      Schicht danach wieder 30 s, gilt das beim nächsten Rückfall erneut; fällt sie früher, braucht der Weg zurück
      eine Probe. Nur für dieselbe Quelle, nicht nach einem Sprecherwechsel.
    Solange ein freier Versuch ansteht, beginnt auf dem Bein keine Probe; er gilt nur für seinen Schritt (andere
    Plätze gehen in der Zeit nicht ohne Probe mit). Scheitert er, entsteht der Aufschub wie ohne Probing, und es gilt
    wieder die Proben-Pflicht. Preis: auf einer Leitung, die die Schicht nicht trägt, ein Fehlversuch mehr zu
    Anrufbeginn (Modell, 10 min auf 1,0 Mbit/s: 1 statt 0; ohne Probing 7) und einer, wenn die Leitung nach langem
    Halten eng wird.
  - Ablauf (seit Stufe 3): Zielrate = Bedarf des Schritts × 1,05 + Ton, mindestens 200 kbit/s über der laufenden
    Rate (vorher × 1,15); Füllrate = Zielrate − was das Bein gerade sendet (die geglätteten Schichtraten der Planung
    enthalten die Schlüsselbilder, im Fenster wird weniger gesendet – mit 5 % Reserve fehlte dem Nachweis sonst der
    Bedarf), nie über das Kontingent (P10). 2 Takte halbe Füllrate, dann volle Füllrate für das Fenster (2,5
    Umlaufzeiten aus den Empfängerberichten, mindestens 2 Takte, höchstens 0,6 s – also 0,4 oder 0,6 s; vorher 3 Takte
    bis 1,5 s), danach 2 Takte Nachlauf ohne Fülldaten. Eine bestandene Probe gibt den Schritt mit seiner vollen
    Reserve (× 1,15) frei. Ändert sich die Bild-Auswahl währenddessen (Sprecherwechsel, Pause), bricht die Probe ohne Wertung
    ab; die nächste kommt frühestens 2 s später.
  - **Deckel der Füllrate, 1,5 Mbit/s:** Ein Schritt, der mehr Füllung bräuchte (z. B. Bildschirm von der unteren
    auf die obere Ebene), wird nicht geprobt und auch nicht „halb geprobt“ freigegeben. Er läuft wie ohne Probing:
    Der Deckel der Schätzung folgt seinem Bedarf, der Wechsel selbst ist die Probe, der Aufschub das Netz.
  - **Auswertung (eigene, seit Saga 2.5 Prüfung 2):** Ein gefallenes GCC-Ziel allein lässt die Probe nicht mehr
    scheitern. Grund: pion-GCC senkt sein Ziel auf zwei Arten – beim *Rücksprung* wegen Stau auf 85 % der
    empfangenen Rate, und ohne jeden Stau durch eine *Klemmung*: Liegt die empfangene Rate nahe der Rate beim
    letzten Rücksprung, rechnet der Anstieg `min(Ziel + Schritt, 1,5 × empfangen)`. Ein Ziel, das seit dem Rücksprung
    weit gewachsen ist (GCC ist ohne Fülldaten anwendungsbegrenzt und wächst mit 8 %/s ungebremst), fällt dabei in
    einem Zug – und die Fülldaten der Probe schieben die empfangene Rate selbst in dieses Band. Das war die Ursache
    der falsch gescheiterten Proben auf freier Leitung im Labor (16–54 % `fail:gcc`); im Testaufbau ohne jedes
    Rauschen nachgestellt (Ziel 2796 → 1069 kbit/s bei Laufzeit-Trend 0,7 ms und Schwelle 6 ms).
    Je Takt wertet die Probe aus:
    - *Stau, gescheitert:* Alter oder Verlust in der eigenen Warteschlange (`queue`); Ziel gefallen (mehr als 3 %
      unter den Höchstwert, nicht im Nachlauf) **und** es liegt unter dem 1,2-Fachen der Rate vor der Probe – so tief
      klemmt pion nie – oder im selben Takt meldet die Laufzeit-Messung Überlast (`usage` „overuse“ bzw. `state`
      „decrease“) bzw. der Verlustanteil liegt zwei Takte in Folge 5 Punkte über dem Stand vor der Probe (`gcc`).
    - *Kein Stau:* Ziel gefallen, liegt aber über dem, was das Bein in den letzten 500 ms gesendet hat (mit
      Fülldaten; Schwelle 1,0 × Senderate). Ein Rücksprung läge bei 85 % der Rate, die pion empfangen hat, eine
      Klemmung beim 1,5-Fachen. Die Probe läuft weiter (Zähler `clamped`).
      Die Senderate zählt dafür wie pion-GCC: Kopf samt Erweiterungen + Nutzlast (`LegStats.HdrExtra`, 8 Byte je
      Paket für transport-cc – bei Füllpaketen 3 %, über alles rund 1 %); alle anderen Raten bleiben bei `BytesOut`.
      Auf Dauer wird höchstens empfangen, was gesendet wurde – im einzelnen Fenster nicht: pion teilt die Bytes seines
      500-ms-Fensters durch die Zeit zwischen dessen erstem und letztem Paket, und sein Fenster liegt um den Weg der
      Rückmeldung versetzt. Gemessen (Testaufbau mit pion-Gegenstelle, 9 Läufe, gleiche Bytes): pions Empfangsrate
      liegt im Mittel 3–4 % über der Senderate, in 5 % der Takte über 10–13 %. Bei 50 Rücksprüngen lag das Ziel in
      26 Fällen über 87 % der Senderate (die Schwelle bis Prüfung 3 – sie trennte nicht), in den 24 Fällen innerhalb
      einer Probe höchstens bei 86 %, außerhalb bis 99 %; über 100 % nur einmal, gleich nach einem Schritt nach
      unten (dort wird weder geprobt noch entschuldigt). Klemmungen (62) lagen bei mindestens 105 %. Der Abstand ist also
      klein: Was die Senderate nicht trennt, fangen die übrigen Signale (Überlast, Verlust, weiterer Fall) – und die
      Ausnahme in der Schonfrist gilt nur kurz nach einem Schritt (unten).
    - *Einzelner Hinweis* (Fall nicht einzuordnen; Überlast oder Verlust ohne Fall): Die Probe läuft weiter und wartet
      auf Bestätigung, das Fenster wird dafür bis 1 s verlängert. Ein zweiter Hinweis, solange der erste offen ist:
      Stau, gescheitert (`gcc`, `over`, `loss`). Steigt das Ziel 2 Takte in Folge wieder (Regler im Zustand „Anstieg“
      – bei anhaltendem Stau stünde es), ist der Hinweis ausgeräumt. Steigt es 2 Takte nicht: Ergebnis **unklar** –
      Abbruch ohne Wertung (`abort`, Grund `unklar`, kein Abstand), Wiederholung nach 2 s. Ist auch die Wiederholung
      (binnen 10 s) unklar, zählt sie als gescheitert.
    Dieselbe Klemmung traf auch den Wechsel selbst (nach bestandener Probe springt die Rate durch das Band, das Ziel
    fällt unter die neue Rate – im Testaufbau hielt `h` 0,2 s): Mit Probing erkennt die Schonfrist sie nach derselben
    Regel und verlangt von da an nur, dass das Ziel wieder steigt – aber nur 3 s lang nach einem Schritt nach oben
    (`clampWin`): So lange kann pions Empfangsrate noch Pakete von vor dem Schritt enthalten (Fenster 500 ms, davor
    das Schlüsselbild der neuen Schicht und der Weg der Rückmeldung). Danach ist ein gefallenes Ziel unter der Rate
    wieder sofort Stau, wie ohne Probing: Im Dauerzustand springt die Rate nicht durch das Band, und die Ausnahme
    verdeckte dort einen echten Rücksprung bei leichtem Stau bis zu 15 s (Prüfung 3). Preis: Auf verrauschter freier
    Leitung fällt `h` wieder so oft wie ohne Probing (Testaufbau mit Jitter und Pausen, je 2 min: 4 und 0 Rückfälle
    mit, 3 und 1 ohne Probing) – pion springt dort wirklich zurück, nur ohne Stau. Ohne Probing bleibt die
    Schonfrist, wie sie war.
    Während der Probe und bis 2 s nach einem Abbruch (gescheitert oder unklar) ändert sich die
    Schätzung nicht – ein Stau, den die Fülldaten erzeugt haben, sagt nichts über die laufende Auswahl. Der Schutz
    gilt nur dafür: Steht 2 Takte nach dem Ende der Fülldaten weiter ein Stausignal an (Warteschlange; GCC-Ziel
    unter der Medienrate und dabei fallend bzw. Überlast gemeldet), zwei Takte in Folge, endet er sofort, und der
    Knoten reagiert wie ohne Probing (Modell, Einbruch von 1150 auf 300 kbit/s mitten in der Probe: Rückzug nach
    1,2 s; ohne Probing 0,4 s, in den ersten 20 s nach dem Start des Knotens 0,8 s). Liegt das GCC-Ziel am Ende der 2 s noch unter der Rate, zählt das als Stau wie
    bisher.
  - Bestanden: kein Stau und kein offener Hinweis bis zum Ende des Nachlaufs, und die Fülldaten sind wirklich geflossen (mindestens
    60 % der Füllrate; sonst „abgebrochen“ mit Abstand wie gescheitert). Dass das GCC-Ziel die Zielrate erreicht,
    wird **nicht** verlangt: pion-GCC hebt sein Ziel auch auf freier Leitung nur um rund 8 % je Sekunde. Der
    Wechsel folgt im selben bzw. nächsten Takt.
  - Abstand: je **Bein und Zielrate**, nicht je Platz (in Gruppen wechselt der Sprecherplatz laufend den Besitzer).
    Eine gescheiterte Probe mit Zielrate X sperrt Proben ab 0,9 × X für 3 s, je weitere gescheiterte × 1,5 bis
    höchstens 20 s (3 / 4,5 / 6,75 / 10,1 / 15,2 / 20 s; seit Stufe 3, vorher 10 s verdoppelnd bis 2 min); Proben mit
    kleinerer Zielrate dürfen früher. Bis eine Probe ab dieser Rate besteht, gibt es dafür auch keinen Versuch
    ohne Probe – gleich für welchen Platz. Eine bestandene Probe setzt zurück, ebenso längere Ruhe (Abstand + 30 s
    ohne neue Probe).
  - Aufschub und Probe: Der Aufschub (8/16/32 s … 5 min) entsteht unverändert, wenn ein echter Wechsel scheitert.
    Der Fehlversuch zählt zugleich wie eine gescheiterte Probe seiner Zielrate (für das ganze Bein). Derselbe Platz
    probt für diese Schicht frühestens nach dem Aufschub, höchstens aber nach 2 min – echte Versuche kommen so nie
    häufiger als ohne Probing, und eine frei gewordene Leitung ist nach gut 2 min (statt 5 min) wieder oben:
    Die Probe kommt spätestens 2 min nach dem letzten Fehlversuch, dazu kommen Ruhezeit, Probe und Wechsel –
    gemessen bis 2 min 43 s. **Zusammenspiel mit dem kurzen Abstand (Stufe 3):** Gescheiterte Proben setzen keinen
    Aufschub; er entsteht nur aus echten Wechseln, die scheitern. In anhaltender Enge ohne Fehlversuch gilt allein der
    Proben-Abstand (höchstens 20 s), der Aufschub zieht ihn nicht auf Minuten. Die 2 min nach einem echten Fehlversuch
    bleiben als Netz (eigene Grenze `probeDwellMax`): Mit 20 s kämen auf einer Leitung, die die Probe trägt, die
    Schicht aber nicht, Versuche alle ~20 s.
  - **Frischer Rückfall und lange Probe (Stufe 3, seit Stufe 3b am Bein):** Fällt binnen 15 s nach einem Schritt,
    der mit einer bestandenen Probe kam, ein Platz des Beins wegen des Budgets zurück (`probeFresh`), ist das ein
    *frischer Rückfall* – die Leitung trug die Probe, aber nicht die Schicht (Token-Bucket-Policer: der Eimer trägt die
    Füllung, die Dauerlast nicht). Gezählt wird je Bein und Zielrate, nicht je Platz (`freshBrake`): In der Gruppe
    fällt nach dem geprobten Schritt des Sprechers zuerst eine Kachel, und der Sprecherplatz wechselt laufend den
    Besitzer – bis Stufe 3 begann die Bremse damit jedes Mal von vorn (Prüfer `bwe3`, H2).
    - *Erster frischer Rückfall:* Die nächste Probe ab 90 % dieser Zielrate fährt **lang**: Fenster fest 1,5 s, Füllziel
      Bedarf × 1,15 (Nachweis bei `eigen` 1/1,15) – wie vor Stufe 3. Die kurze Probe schob nur (Bedarf × 1,05 −
      Policer-Rate) × 0,4–0,6 s ≈ 80–120 kbit über die Grenze, ein Eimer von 1–2 × Rate × 100 ms (145–290 kbit) leerte
      sich nie; die lange schiebt ≈ 400–600 kbit (beides ist nötig – mit altem Fenster, aber × 1,05 blieb der Zyklus).
      Bis dahin deckelt die Bremse die Auswahl unter 90 % dieser Zielrate (auch laufende Schichten; kein freier
      Versuch, kein Schritt ohne Probe). Besteht die lange Probe, gilt wieder die kurze; scheitert sie, wächst der
      Proben-Abstand (3 s × 1,5 bis **40 s** – kurze Proben weiter höchstens 20 s; `probeGapMaxLong`), und die
      nächste ist wieder lang. Die Bremse wartet diesen Abstand ab (sonst käme nach mehr als 30 s wieder eine kurze).
    - *Zweiter in Folge* (der Eimer ist so groß, dass auch die lange Probe ihn nicht leert): Fehlversuch wie binnen
      2 s – Aufschub des Platzes und ein Aufschub am Bein (8, 16, 32 … s, die Probe danach höchstens nach 2 min), den
      weder eine bestandene Probe noch ein Sprecherwechsel aufhebt. Ohne diese Regel (Modell, Eimer 0,6 s) 36 statt 9
      Rückfälle in 10 min, Gruppe mit Sprecherwechsel 71 statt 28.
    - *Nur Policer-Muster (Stufe 3c):* Der Policer nimmt den Schritt zurück, die Leitung trägt aber weiter, was vor
      ihm lief – und das immer wieder auf dieselbe Zielrate. Ein Störschub (Querverkehr) drückt das Bein dagegen unter
      den Stand vor dem Schritt, oft alle Plätze zugleich; er wäre auch ohne den Schritt gekommen. Kein frischer
      Rückfall ist deshalb (a) ein **Einbruch**: Budget bzw. GCC-Ziel lagen zwischen Schritt und Rückfall unter 0,7 ×
      Zielrate vor dem Schritt (`probeSlump`; die Folge beginnt dann von vorn); (b) ein Rückfall auf eine **andere,
      kleinere Zielrate** als die der laufenden Bremse (er senkt sie nicht und frischt sie nicht auf); (c) eine
      **Umverteilung** (die Auswahl des Beins sinkt nicht – eine Kachel fällt, weil der Sprecher steigt); (d) ein
      Rückfall, nachdem das Bein ohne Probe **über** den geprobten Schritt hinaus gestiegen ist (der hielt). Bis
      dahin (Prüfer `bwe3b`, H1) zählte in der Gruppe auf freier Leitung mit Störschüben fast jeder Schub: Die Bremse
      fiel auf die Zielrate einer Kachel-Stufe, der Deckel hielt alle Plätze auf `l`, der Aufschub am Bein wuchs bis
      5 min. Modell (Gruppe 3, Wechsel 20 s, Schübe alle 10–30 s, 10 min): Sprecher auf `h` 12 → 47 % (`eigen` 14 →
      49 %), alle Plätze auf `l` 76 → 24 % (67 → 22 %) der Takte, volle Belegung nach dem letzten Schub nach 12,6 s
      statt nie (`TestProbingGruppeStoerschuebe`) – wie vor Stufe 3b; Policer-Fälle unverändert. Testaufbau (1:1,
      13.11.): Token-Bucket 1450/150 und 1500/100 ms, 180 s wie Stufe 3b (40 Rückfälle je Stunde, 30–35 MB Fülldaten
      je Stunde); 600 s 12 je Stunde, 19,8 MB je Stunde. `h` nach dem Ende des Policers (90 s frei beobachtet) im
      Median nach 31,6 s (10,6–34,2 s, 12 Läufe) – mit der ersten langen Probe danach, Abstand 41–43 s. 3 %
      Zufallsverlust, freie Leitung und Enge 1550/1600 wie Stufe 3b. **Grenze:** GCC
      springt an einem Policer über dem Stand vor dem Schritt auf ~0,85 × dessen Rate, mit Reserve gerechnet also
      nicht unter ~0,74 – 0,7 liegt knapp darunter (Modell: ab 0,8 zählen Policer-Rückfälle in der Gruppe als
      Einbruch). Im 1:1 sind Schub (600 kbit/s) und Policer nach einem Schritt von `m` auf `h` nicht zu trennen (Stand
      vor dem Schritt ~0,6 Mbit/s): Dort bleibt die lange Probe nach dem Schub (Preis wie Stufe 3b).
    - Ein Rückfall nach mehr als 15 s setzt die Folge zurück (die lange Probe bleibt fällig); erledigt ist die Bremse
      nach 30 s (+ Aufschub) ohne neues Zeichen (frischer Rückfall, gescheiterte lange Probe). **Grenze:** Ein Eimer,
      der sich erst nach mehr als 15 s leert (Modell: 7 % unter `h`, Eimer ≥ 1,2 s), entgeht der Regel. 30 s fingen ihn
      (Modell 27 → 8 Rückfälle), bremsten aber die freie Leitung mit Störschüben (Anteil `h` 74 → 23 %) – deshalb 15 s.
    - Modell (Policer 7 % unter der vollen Belegung, 10 min; vorher = Stufe 3; Abstand der langen Probe 40 s): 1:1
      Eimer 0,15 s 8 → 1 Rückfall, `h` nach dem Ende des Policers nach 15 statt 32 s (`eigen` 19 statt 34 s); Gruppe 3
      mit Sprecherwechsel alle 20 s, Eimer 0,15/0,6 s: `gcc` 107/90 → 7/28, `eigen` 87/82 → 9/27, Sprecherplatz
      96–98 % auf `h`; Gruppe ohne Wechsel, Eimer 0,15 s: volle Belegung nach dem Ende nach 20–32 s (vorher 18–28 s –
      sie wartet auf die nächste lange Probe; mit 20 s Abstand waren es 4 s). **Preis:** auf anhaltendem Policer eine lange
      Probe je Abstand (jede leert den Eimer und kostet dort kurz Verlust, auch an Bildpaketen); auf freier Leitung
      mit Störschüben alle 10–30 s 3,9 statt 2,2 MB in 10 min (`h` 74 statt 76 %); auf freier Leitung ohne Störung
      nichts. **Abstand der langen Probe 40 statt 20 s** (`probeGapMaxLong`): Testaufbau, Policer 1450/150 ms, 600 s:
      20 statt 31 Proben, 20 statt 30 MB Fülldaten je Stunde, 5,5 statt 6,8 Tausend verworfene Pakete (Modell 1:1:
      2,6 statt 4,3 MB in 10 min). Dafür kommt `h` nach dem Ende des Policers erst mit der nächsten langen Probe – im
      Mittel nach ~20 s, höchstens ~40 s (vorher ≤ 20 s; Testaufbau 180 s Policer: in 30 s freier Zeit danach in
      keinem der 4 Läufe, Abstand zuletzt 41 s). Engen mit Schlange (Proben scheitern kurz) und die Serie sind nicht
      betroffen: Der Abstand überschreitet 20 s erst nach sechs gescheiterten langen Proben in Folge.
      Tests: `internal/sfu/policer_test.go`.
    Die Probe verlängert den Aufschub nie. Aufschub, Proben-Abstand und die Bremse nach frischem Rückfall überleben
    einen Wegwechsel (udp → tcp → udp) des Geräts für 5 min, ebenso der verbrauchte freie Versuch zum Anrufbeginn
    (auch ohne gescheiterte Probe).
  - **Abwägung – Gruppe mit Sprecherwechsel, Leitung wird frei:** Mit Probing kommt eine solche Gruppe nach dem
    Freiwerden der Leitung langsamer auf die volle Belegung zurück: 2 min 15 s bis 2 min 34 s gegenüber 25–44 s
    ohne Probing (Modell mit pion-nahem Ziel-Regler, 3 min auf 1,2 Mbit/s, Sprecherwechsel alle 20 s: 104 s gegenüber
    10 s; vor der eigenen Auswertung kam sie dort in 6 min gar nicht zurück) (mit Probing gilt der Abstand nach gescheiterten Proben je Bein und Zielrate und überlebt den
    Sprecherwechsel). Dafür gibt es in der engen Phase davor 0–2 statt 9–19 Fehlversuche (jeder mit Schlüsselbild,
    Rückwechsel und kurz verdecktem Ton). Mit `HORN_PROBING=0` kommt eine solche Gruppe schneller zurück, zahlt
    aber in der engen Zeit.
  - **Probing Stufe 3 – Messzahlen (Testaufbau mit pion-Gegenstelle, 13.11.; vorher = Stand vor Stufe 3):**
    Serie 7 Fälle × 3: Rückfälle auf freier Leitung `gcc` 12 → 10, `eigen` 0 → 0; Anteil `h` auf freier Leitung nach
    einer Enge (engfrei/policer/mitte/knapp) `gcc` 80/65/52/59 → 94/74/90/93 %, `eigen` 78/54/64/79 → 89/62/89/84 %;
    Proben in der Enge `gcc` 38 → 66, `eigen` 48 → 72 (keine bestanden – `gcc` ist auch mit dem kurzen Fenster
    bewertbar); Fülldaten der Serie `gcc` 5,45 → 5,04, `eigen` 4,46 → 4,67 MB. Rückkehr nach der Freigabe (`h`
    dauerhaft, Median): engfrei `gcc` 14,6 → 8,6 s, `eigen` 16,1 → 9,6 s; Leitung 1000/20 ms 19,6 → 15,0 bzw. 20,4 →
    15,8 s; mitte 16,7 → 2,4 bzw. 11,0 → 3,9 s; knapp 13,5 → 2,1 bzw. 5,4 → 5,5 s. Anhaltende Enge 1450/1520/1550
    (6 Läufe, 720 s): `eigen` 46 → 57 Proben, 2,02 → 2,10 MB (unklare 21 → 7), `m`/`l` 687/30 → 680/37 s; `gcc` 31 →
    54 Proben, 1,13 → 1,89 MB, `m`/`l` 547/170 → 616/101 s. Knapp über `h` (1600/1700): `eigen` hält `h` 124 s statt
    6 s (4 Läufe). Token-Bucket 1450/150 ms bzw. 1500/100 ms (180 s): vorher scheiterte jede Probe (das lange Fenster
    leert den Eimer), jetzt bestehen sie und der Platz fällt 110–120-mal je Stunde zurück; über 600 s gedämpft durch den
    frischen Rückfall: 10 Rückfälle, zuletzt alle ~2 min (vorher 1). **Das kurze Fenster macht Policer schlimmer**;
    nach dem Ende des Policers kann `h` bis zum Ablauf des Aufschubs (bis 2 min) ausbleiben.
  - **Probing Stufe 3b – Messzahlen (Testaufbau mit pion-Gegenstelle, 13.11.; vorher = Stufe 3):** Token-Bucket
    1450/150 bzw. 1500/100 ms, 180 s (2 Läufe je Fall): Rückfälle je Stunde 110–120 → 40 (Beginn des Policers und
    ein frischer nach der ersten kurzen Probe, danach nur noch lange Proben), Anteil `h` 26–33 → 6–9 % (`h` wird auf
    dem Policer nicht mehr überschossen), vom Policer verworfene Pakete 12–15 → 4–6 Tausend; 600 s: 9–11 → 2
    Rückfälle, 12–22 → 2 % `h`, 24–33 → 5–7 Tausend verworfen (mit 40 s Abstand 5,5 Tausend). Serie 7 Fälle × 3:
    Rückfälle auf freier Leitung `gcc` 10 → 18 (alles Zielstürze von pion-GCC; Rückfall in beiden Ständen genau dann,
    wenn das Ziel unter ~1405 kbit/s sinkt – die Einbrüche waren tiefer), `eigen` 0 → 0; Rückkehr nach der Freigabe
    (Median) engfrei/Leitung 1000/20 ms/mitte/knapp `gcc` 8,6/15,0/2,4/2,1 → 8,4/15,0/3,6/5,0 s, `eigen`
    9,6/15,8/3,9/5,5 → 10,4/15,6/5,6/6,0 s (gleicher Ablauf; Streuung, wann GCC in der Enge auf `l` fällt).
    Knapp unter `h` (1550/1600, 120 s, 2 Durchgänge) und anhaltende Enge 1550 (600 s): Proben, Fülldaten und Zeit auf
    `m`/`l` wie vorher, keine lange Probe. 3 % Zufallsverlust: 0 Rückfälle, kein Policer-Merkmal (pion-GCC reagiert
    im Testaufbau auf 3 % gar nicht).
  - **Bekannte Grenze – Leitung zwischen Bedarf und Bedarf × 1,05** (vor Stufe 3: × 1,15): Die Probe prüft die
    Zielrate mit Reserve. Eine Leitung, die die höhere Schicht gerade trägt, aber nicht 5 % mehr (Modell:
    Browser-`h` mit 1250 kbit/s auf einer Leitung bis 1,34 Mbit/s), besteht die Probe nie und **erreicht die Schicht
    mit Probing nicht** – ohne Probing hält sie dort. Ab Bedarf × 1,06 besteht die Probe. Stellschraube im Code: `probeBlindAfter` in
    `internal/sfu/probe.go` (Standard 0 = nie; z. B. 3 = nach drei gescheiterten Proben in Folge ein Versuch ohne
    Probe, mit Aufschub, falls er scheitert). Entscheidung nach dem Labor.
  - Füllpakete (`internal/fwd`, `Leg.SetPadding`): reine Padding-Pakete (P-Bit, 255 Byte, keine Nutzlast) auf der
    Bild-SSRC des Ausgangs, nur zwischen zwei Bildern (nach einem Paket mit Marker), mit fortlaufender Folgenummer
    des Ausgangs und dem Zeitstempel des letzten Bilds. Sie gehen **direkt hinter einem Paket des Beins** hinaus
    (Ton oder Bild, im selben Schub, höchstens das Guthaben von 110 ms); einen eigenen Takt gibt es nur, wenn das
    Bein 100 ms lang nichts zu senden hatte (stille Quelle, weniger als 10 Bilder/s). Die 100 ms liegen mit Absicht
    weit über dem Bildabstand üblicher Kameras: Mit 40 ms (bis Prüfung 2) feuerte der Wecker auf einem Bein ohne
    dichten Ton bei 22–25 Bildern/s kurz vor jedem Bild (Test: 28 von 30 Bildern mit eigenem Füll-Schub in den 8 ms
    davor; jetzt 0, bei 24 und 30 Bildern/s). Grund (Saga 2.5, N2): pion-GCC fasst
    zu einer Gruppe zusammen, was binnen 5 ms nach ihrem ersten Paket gesendet wurde, und misst vom ersten Senden
    bis zur letzten Ankunft. Der frühere eigene 5-ms-Takt setzte Füll-Schübe laufend kurz vor Ton- und Bildpakete;
    die Gruppen wurden scheinbar verschieden lang, die Laufzeitmessung rauschte während einer Probe auf freier
    Leitung um 4 ms (Median) statt 0,4 ms, und etwa jede 30. Probe scheiterte dort fälschlich mit `fail:gcc`
    (in 22 Läufen des Prüfers einmal drei in Folge). Mit Füllpaketen hinter den Medienpaketen bleibt das
    Gruppenmuster das des Mediums: 0,3–0,4 ms, in 90 Läufen keine gescheiterte Probe auf freier Leitung. Der Takt
    der Steuerung setzt nur Atomics, der Schreiber des Beins füllt ohne Sperren und Allokationen. Ein Paket der Quelle, das erst nach einem
    Füll-Schub eintrifft, aber davor gehört (Nachsendung, Umsortierung), geht mit dem Versatz von damals hinaus:
    Jede Stelle, die der Ausgang überspringt, merkt sich ihren Versatz selbst – dieselbe Mechanik wie bei
    ausgelassenen Bildern, siehe „Doppel und Nachzügler“ (256 Stellen je Ausgang; ältere Nachzügler werden verworfen
    – eine Lücke ist besser als eine doppelt vergebene Folgenummer; Zähler `late_dropped`). Füllpakete bekommen die
    nächste freie Folgenummer hinter den Lücken, die noch auf ihr Paket warten. Totmann: Nach 3 s ohne neue Vorgabe füllt das Bein nicht mehr. Der
    NACK-Antworter puffert Füllpakete nicht (ein verlorenes wird nicht nachgesendet). Die Raten der Quellen zählen
    sie nicht mit, die Rate des Beins schon.
  - Zeit bis zur oberen Ebene auf freier Leitung: Der erste Schritt läuft ohne Probe (freier Versuch) – im Modell
    `h` 1,4 s nach Beginn, mit und ohne Probing gleich.
  - **Offen – Leitungen mit Ratengrenze knapp über dem Bedarf (am Testaufbau gemessen, vor der eigenen Auswertung;
    seither nicht neu gemessen – im Labor vom 11.11. bestanden auf 6 Mbit/s 7 von 7 Proben):** Der
    Testaufbau (pion hinter einem nachgebildeten Engpass, Zeitauflösung des Empfängers rund 1 ms) zeigt, dass
    pion-GCC auf einer ratenbegrenzten Leitung schon beim Einsetzen der Fülldaten häufig Überlast meldet, obwohl
    die Leitung die Zielrate trägt – jeder Schub braucht dort messbar Zeit durch den Engpass, und die Schwelle des
    Überlast-Erkenners ist in der ruhigen Zeit davor weit gesunken. Schritt von `m` auf `h` (Zielrate rund
    1,8 Mbit/s), je 10 Läufe: Leitung 10 Mbit/s – alle 10 Proben bestanden (mit dem alten 5-ms-Takt 11 von 18
    gescheitert, 3 Läufe ohne `h` in 44 s); Leitung 6 Mbit/s – 19 von 24 Proben gescheitert, 5 Läufe ohne `h` in
    44 s (alter Takt: 16 von 21, ebenfalls 5). Leitung 1,2 Mbit/s, Schritt von `l` zurück auf `m` (Zielrate rund
    0,8 Mbit/s): scheitert in beiden Fassungen etwa jedes zweite Mal. Die Probe irrt hier zur sicheren Seite (kein
    Wechsel, nächste Probe nach 10/20/40 s), kostet aber Bildqualität auf Leitungen, die mehr trügen. Ob echte
    Leitungen sich so verhalten, zeigt erst das Labor; mögliche Stellschrauben danach: `probeBlindAfter`, eine
    flachere Rampe, eine eigene Auswertung der Laufzeiten statt des Überlast-Signals von pion.
  - Datenverbrauch und Zahl der Proben (Modell, je 10 min): bestandene Probe von 360p auf 720p 0,14 MB;
    gescheiterte brechen früher ab (0,02–0,07 MB). Dauerhaft zu enge Leitung, 1:1: 8 Proben, 0,2–0,6 MB. Gruppe
    mit 4 Sendern und Sprecherwechsel auf enger Leitung: 7–11 Proben, 0,1–0,4 MB. Freie Leitung mit Stau-Schüben
    alle 10–30 s: eine Probe je Rückkehr auf die obere Ebene, 8–28 Proben, 1,1–3,9 MB – hier ist Probing am
    teuersten und bringt gegenüber „aus“ keinen Gewinn.
  - Diagnose: Im Ereignis `layout` trägt der Platz, der höher gehen soll, einmalig `probe` (`start` | `ok` | `fail` |
    `abort`), `probe_kbit` (Zielrate) und `probe_why` (`gcc`, `over`, `loss`, `queue`; bei `abort`: `unklar`, `platz`, `ausgang`,
    `fuell`) – also höchstens zwei zusätzliche Meldungen je Probe. Alte Geräte ignorieren die Felder; die Belegung
    ändert sich durch eine Probe nicht. Das Web übernimmt sie in sein Diagnose-Ereignis `layers` hinter dem Platz
    (`s3:m3@ab12?start,1599k`, `?ok,1599k`, `?fail:gcc,1599k`, `?abort:unklar,1599k`). Der Knoten schreibt je
    Probenende eine Zeile ins Log – nur die Kurzkennung des Beins wie bei „bein auf“, keine Kennungen von Personen,
    Geräten oder Räumen:
    `probe 5884a4 platz=speaker schritt=m>h ziel=1882k fuell=1136k ergebnis=fail grund=gcc dauer=0.4s stufe=1 geklemmt=0 hinweis=- est=1338k gcc=721k`
    (`stufe` 1 halbe Füllrate, 2 volle, 3 Nachlauf; `geklemmt` = Fälle des Ziels in dieser Probe, die kein Stau
    waren; `hinweis` = letzter einzelner Hinweis auf Stau, auch wenn er ausgeräumt wurde – bei `abort`/`unklar` der,
    der stehen blieb; `est`/`gcc` = Schätzung des Beins und GCC-Ziel beim Ende). Zähler je Bein: `GET /api/sfu/leg/{id}` →
    `probes {started, passed, failed, aborted, unclear, clamped, pad_bytes, late_dropped, last}` (`unclear` = davon
    abgebrochen, weil das Ergebnis unklar war), `stats.pad_bytes`, `stats.late_dropped` (verworfene Nachzügler,
    deren Stelle der Ausgang nicht mehr kennt – sollte 0 bleiben; steigt er, kommen Pakete der Quelle mehr als 256
    Pakete zu spät an). Beide Felder fehlen in `stats`, solange sie 0 sind.
  - Tests: `internal/sfu/probe_test.go` (Modell mit Fülldaten: enge Leitung, Leitung wird frei, Störschübe,
    Gruppen, Sprecherwechsel, Einbruch in der Probe, knappe Leitung, großer Schritt, Wegwechsel),
    `internal/sfu/probe_robust_test.go` (Klemmung des Ziels auf freier und enger Leitung, unklares Ergebnis und
    Wiederholung, Rückkehr nach langem Halten, Anrufbeginn; dazu im Modell ein Ziel-Regler nach den Regeln von
    pion-GCC, `estRoom.pion`), `internal/sfu/probe_zahlen_test.go` (Kennzahlen mit und ohne Probing, Ausgabe mit `-v`),
    `internal/fwd/pad_test.go` (Folgenummern, verspätete Pakete, kein Füllpaket mitten im Bild, Füllpakete nur
    hinter Medienpaketen, Totmann, keine Allokation), `probing_udp_test.go` (Ende zu Ende mit pion-Gegenstelle hinter einem Engpass).
- **Eigener Stau-Detektor (experimentell, `HORN_BWE`, Standard `gcc`; „Weg B“):**
  - `HORN_BWE=gcc` (Standard): pion-GCC allein, wie bisher – die Hülle wird gar nicht eingebaut (die Fabrik des
    Schätzers bleibt, wie sie war). `HORN_BWE=mit` (Stufe 0): Um den pion-Schätzer jedes UDP-Beins liegt eine Hülle, die
    jedes abgehende Paket (TWCC-Folgenummer, Sendezeit, Größe, Art) und jede transport-cc-Rückmeldung mitliest und danach
    unverändert an pion-GCC weitergibt. Daneben urteilt ein eigener Detektor (`internal/bwe`) über die Leitung. **Er
    entscheidet nichts:** Schichtwahl, Schätzung und Proben laufen wie bei `gcc`. `HORN_BWE=eigen` (Stufe 1 und 2):
    dieselbe Hülle, und der Detektor **entscheidet mit** – siehe „`eigen`“ unten. Jeder andere Wert gilt als `gcc`
    (mit Warnzeile). Beim Start steht bei `mit` bzw. `eigen` eine Zeile `HORN_BWE=…: …` im Log.
  - Verfahren: Pakete werden nach Sendezeit zu Fenstern gebündelt (mindestens 100 ms und 20 gemeldete Pakete,
    höchstens 500 ms). Die Schlange eines Fensters ist seine kleinste Laufzeit (Ankunft minus Sendezeit) über der
    Basis, der kleinsten Laufzeit der letzten 30 s – Jitter und kurze Pausen heben das Minimum nicht, eine stehende
    Schlange schon. Der Verlust wird nicht je Fenster beurteilt, sondern über die letzten 160 gemeldeten Pakete
    (höchstens 3 s zurück, mindestens 60 Pakete): Bei 5 % Zufallsverlust haben 26 % aller Fenster von 20 Paketen
    10 % Verlust und mehr – das ergab im Modell 66–171 falsche Warnungen in 30 min, jetzt 0–1.
    Zustände: `frei`, `warnung` (2 Fenster mit Schlange über 50 ms, oder 2 Fenster in Folge mit Verlust ab 12 % über
    die letzten 160 Pakete und Verlust im Fenster selbst), `stau` (4 Fenster Schlange bzw. 10 Fenster Verlust),
    zurück nach `frei` nach 3 bzw. 5 Fenstern mit Schlange unter 20 ms und Verlust unter 8 %; `unbekannt` ohne
    Rückmeldung (1 s bei laufendem Senden) und nach einem Wegwechsel (ICE wählt ein anderes Adresspaar – die Basis
    wird verworfen).
  - **Verlust ohne Stau** (Labor vom 12.11., wichtigster Fund): Reiner Verlust ohne Verzögerung ergibt höchstens
    `warnung`; `stau grund=verlust` nur bei schwerem, anhaltendem Verlust (ab 12 % über gut eine Sekunde – das Bild
    eines Policers, bei dem Verlust das einzige Signal ist: Modell 1000/1200/1400 kbit/s bei `h`: Warnung nach
    0,6–0,9 s, Stau nach 1,4–1,7 s; bis Stufe 0 nach 0,4–0,5 s und 0,8–0,9 s). Dauerverlust um 4–5 % ergibt keine
    Meldung, und ein früher erreichter `stau` löst sich dort wieder (Modell: nach 2,1 s; vorher blieb ein Bein 3 min
    hängen, weil die Rückkehr unter 3 % verlangte). **Schonzeit:** In den ersten 3 s eines Beins zählt Verlust nicht
    (Labor: frühe `warnung grund=verlust` auf 32 % der Beine); eine Schlange zählt auch dann.
  - **Doppel – welche Pakete der Browser nicht zählt** (Labor 7.1/7.2; am Code und an den Labordaten geklärt, mit
    echtem Browser noch nicht gegengeprüft): Lieferte eine Quelle ein Paket zweimal, leitete der Knoten es bis zum
    Abgleich je Quelle (unten, „Doppel und Nachzügler“) zweimal weiter – mit **derselben RTP-Folgenummer**, aber neuer
    transport-cc-Nummer. Das geschieht, wenn der **sendende** Browser
    zum Füllen bzw. Proben alte Pakete über RTX wiederholt (libwebrtc füllt mit Wiederholungen, sobald RTX
    ausgehandelt ist – zum Anrufbeginn, nach „Kamera an“, beim Start des Teilens und dauerhaft, solange eine obere
    Simulcast-Ebene mangels Schätzung aus ist): pion gibt RTX-Pakete entpackt, mit der ursprünglichen Folgenummer,
    auf der Spur zurück. Der **Empfänger** verwirft das Doppel am SRTP-Wiederholungsschutz – bevor er es zählt
    (`packetsReceived`), als Lücke sähe (`packetsLost`) oder per transport-cc bestätigt. Für den Knoten sieht es wie
    Verlust aus, für pion-GCC ebenso (dessen Verlust-Regler senkt das Ziel ab 10 %). Im Labor passte dazu: gesendet/
    gezählt 1,11–1,17 in den Teilen-Läufen auch beim alten Stand, und im Einzellauf mit dem Verhältnis 1,55 sendete
    der Browser der Quelle auf `m` gut doppelt so viele Bytes wie sonst (25,2 gegen 11,5 MB), während seine Ebene `h`
    aktiv, aber leer war. **Seit dem Abgleich im Weiterleiter erreicht kein Doppel der Quelle mehr ein Bein** – bei
    keiner Stellung des Schalters, auch nicht bei `gcc`: pion-GCC führt dafür keinen Verlust mehr
    (`TestPruefDoppelGCCVerlust`, Quelle mit 25 % Doppeln, echter pion-Schätzer: mit Abgleich 0,0 % Verlust und Ziel
    rund 1405 kbit/s wie ohne Doppel – 1404 bis 1406 je Lauf, 900 Pakete gesendet und 900 gezählt; dieselben Doppel
    direkt in die Hülle 25,0 % Verlust und 134 kbit/s, 1200 gesendet und 900 gezählt).
    Bei `mit` und `eigen` erkennt die Hülle ein Paket mit wiederholter Folgenummer weiterhin (Fenster von 1024 Nummern
    je Strom): Bleibt seine Bestätigung aus, zählt das für den Detektor nicht als Verlust (Zähler `sent.dup`,
    `dup_unacked`). Beide Zähler bleiben als Messwert und **sollten 0 sein** – alles andere hieße, der Weiterleiter
    vergibt doch eine Folgenummer zweimal. Der frühere Versuch, Doppel bei `eigen` an pion-GCC vorbeizuleiten, ist
    entfernt: Er wirkte nicht (pion führt die transport-cc-Nummer aus der Rückmeldung auch dann als verloren, wenn es
    das Paket nie gesehen hat).
  - Zeile je Zustandswechsel (nur die Kurzkennung des Beins, keine Kennungen von Personen, Geräten oder Räumen):
    `bwe 5884a4 zustand=stau vorher=warnung grund=schlange schlange=123ms verlust=2.5% durchsatz=1012k kapazitaet=1007k gcc=1062k est=1200k`
    (`grund`: `schlange`, `verlust`, `leer`, `stille`, `beginn`, `wechsel`; `verlust` = Anteil über die letzten 160
    gemeldeten Pakete; `durchsatz` = vom Empfänger bestätigt über die letzte Sekunde; `kapazitaet` = Durchsatz der
    Fenster, die den letzten Stau gezeigt haben, 0 = noch keiner; `gcc`/`est` = GCC-Ziel und Schätzung des Knotens
    zum Vergleich).
  - Zeilen der Steuerung (bei `mit` und `eigen`; bei `gcc` bleibt das Log, wie es war). Je **Rücknahme einer Schicht**:
    `schicht 5884a4 platz=speaker h>m grund=gcc-fall est=1100k gcc=900k rate=1330k zustand=frei schlange=24ms verlust=3.1%`
    (`p` = Pause). `grund`: `quelle` (die Schicht der Quelle ist weg bzw. flattert), `schlange` (eigene Warteschlange
    des Knotens), `gcc-…` (das GCC-Ziel liegt unter der Rate und war nicht entschuldigt: `fall` – gefallen, als die
    Rate es überholte; `faellt`, `steht`, `frist` – die Schonfrist ist gescheitert; `detektor` – eine Widerlegung
    endete, weil der Detektor nicht mehr widerlegt; `sperre` – Sperrzeit nach einem Stau; `tot` – die Folge war schon
    gescheitert; `nach-probe` – das Ziel liegt nach einer gescheiterten Probe weiter unter der Rate), `kontingent`,
    `bedarf` (die Schätzung ist nicht gefallen, aber die Auswahl passt nicht mehr hinein). Bei `eigen` dazu je
    **Widerlegung** eine Zeile am Beginn und eine am Ende:
    `bwe 5884a4 widerlegung=beginn art=spitze gcc=1130k rate=1330k est=1480k zustand=frei schlange=1ms verlust=0.0%`
    `bwe 5884a4 widerlegung=ende art=spitze grund=erholt dauer=1.6s gcc=1250k rate=1330k est=1480k zustand=frei schlange=1ms verlust=0.0%`
    (`art`: `spitze` – der Detektor widerlegt ausdrücklich; `halten` – Halten nach bestandener Probe. `grund` am Ende:
    `erholt` – das Ziel liegt wieder über der Rate; `detektor` – er widerlegt nicht mehr, das Signal von GCC gilt;
    `faellt`, `steht`, `frist` – die Schonfrist ist gescheitert; `probe`.) `rate` ist die Medienrate des Takts, an
    der das GCC-Ziel gemessen wurde (nicht die geglättete); `zustand`, `schlange` und `verlust` sind, was der Detektor
    in dem Augenblick an der Spitze sieht (Verlust der letzten 500 ms; Schlange −1 = nichts Frisches). Nur
    Zustandswechsel, nicht je Takt; nur die Kurzkennung des Beins. Zeigt der Detektor das **Policer-Merkmal**
    (Stufe 3b), enden `schicht`- und `widerlegung`-Zeilen mit ` policer=an`; Beginn und Ende des Merkmals stehen als
    eigene Zeile im Log: `bwe 5884a4 policer=an folgen=71% korrelation=0.47 verloren=76 fenster=95 verlust=4.2%`
    (Anteil der Verluste in Folgen ab 3 Paketen, Korrelation Senderate/Verlust je Messfenster, verlorene Pakete und
    Fenster der letzten 10 s, Verlust an der Spitze). Die `bwe`-Zeilen eines Beins (Zustandswechsel, Policer-Merkmal)
    stehen in der Reihenfolge ihres Entstehens im Log (seit Stufe 3c; vorher konnten sich zwei rasch folgende Zeilen
    vertauschen).
  - Zusatz in der Zeile `probe` bei `mit`: `… est=1338k gcc=721k bwe=staut bwe_schlange=140ms bwe_verlust=0.0% bwe_durchsatz=699k bwe_zustand=warnung bwe_fenster=4 bwe_sofort=staut-nicht`.
    `bwe` ist das Urteil des Detektors über die Pakete der Probe **nach dem Nachlauf** (zwei Umlaufzeiten + 200 ms,
    höchstens 1 s; ohne bekannte Umlaufzeit 500 ms), `bwe_sofort` das, was er beim Ende der Probe schon wusste – je
    `staut`, `staut-nicht` oder `unklar`. `bwe_schlange` ist die zweithöchste Fenster-Schlange der Probe,
    `bwe_durchsatz` die Spitze des bestätigten Durchsatzes über mindestens 400 ms, `bwe_zustand` der schlechteste
    Zustand in der Probe, `bwe_fenster` die Zahl der Fenster. **Die Zeile `probe` kommt bei `mit` um den Nachlauf
    später** (bei `gcc` sofort und ohne Zusatz). Urteil über eine Spanne: `staut` bei Schlange über 50 ms (in
    mindestens zwei Fenstern), bei gemeldetem Stau oder wenn der Verlust der Spanne deutlich **über dem der 10 s
    davor** liegt (5 Punkte mehr, mindestens 8 % und 3 Pakete – Verlust, der mit der Last kommt, ist das Zeichen einer
    Ratengrenze; Verlust, der schon vorher da war, sagt nichts); `staut-nicht` bei Schlange unter 20 ms, durchgehend
    `frei` und nicht gestiegenem Verlust; `unklar` sonst – auch, wenn die Basis jünger als 2 s ist bzw. in oder kurz
    vor der Spanne zurückgesetzt wurde (eine junge Basis kann selbst schon eine Schlange enthalten).
  - **`eigen` – Stufe 1, Wertung der Proben:** Das Stausignal von GCC (Ziel gefallen, Überlast, Verlust; auch ein
    unbestätigter einzelner Hinweis, der stehen bleibt) hält nur noch die **Füllung an**. Danach wartet die Probe den
    Nachlauf ab (wie oben; mindestens 2 Takte), und der Detektor wertet:
    - `staut` → gescheitert (`grund=stau`), mit Abstand wie bisher. Meldet der Detektor nach einem Stausignal von GCC
      schon vor Ende des Nachlaufs Stau, wartet die Probe nicht weiter.
    - Stausignal von GCC **und** ein Anzeichen beim Detektor unterhalb seiner Schwellen (Schlange ab 20 ms, Verlust
      gestiegen, Warnung) → zwei Hinweise: gescheitert, Grund der von GCC. (Im Labor lag die Schlange in der Enge oft
      bei 43–48 ms, knapp unter der Schwelle von 50 ms.)
    - `staut-nicht` **und** Durchsatz-Nachweis → bestanden – auch wenn GCC ein Stausignal gemeldet hatte. Nachweis:
      Während der vollen Füllung wurde der **Bedarf des Schritts wirklich gesendet** (die Zielrate ohne die Reserve:
      seit Stufe 3 95,2 % der Zielrate, Reserve 5 %; vorher 87 %, Reserve 15 %), und der Empfänger hat
      davon **im selben Zeitraum** mindestens 95 % bestätigt (bestätigte Bytes je Ankunftszeit gegen gesendete Bytes
      je Sendezeit derselben Messfenster, über mindestens 200 ms – ganze 100-ms-Fenster der Füllung, bei 0,4 s Fenster
      2 oder 3; vorher 400 ms) – und **auch das Bestätigte erreicht den Bedarf** (sonst reichten Bedarf × 95 %, weniger
      als die Schicht braucht). Damit der Bedarf im Fenster wirklich gesendet wird, füllt die Probe seit Stufe 3 gegen
      das, was das Bein gerade sendet (siehe Ablauf; vorher `l`→`m` gemessen 87–102 % der Zielrate).
      Alles zählt am selben Zähler (alle Pakete des Beins mit Kopf, Ton und Füllung) – nicht mehr Zielrate (mit
      Reserve und Ton) gegen Durchsatz. Hält GCC die
      Füllung früh an, ist die Zielrate nie gesendet worden; `staut-nicht` sagt dann nichts über sie (Labor: auf
      enger Leitung 10 von 29 Proben `staut-nicht`, keine davon mit Nachweis).
    - sonst unklar → Abbruch ohne Wertung (`abort`, Grund `unklar`, kein Abstand), Wiederholung nach 2 s. Höchstens
      **drei** unklare Proben in Folge (je binnen 60 s nach der vorigen): die dritte zählt als gescheitert, mit
      Abstand (Grund: das Signal von GCC, sonst `nachweis` bzw. `bwe`). **Kam das Stausignal von GCC schon in der
      Rampe** (halbe Füllung – die volle Füllung lief nie, die Probe hat die Zielrate nicht geprüft), zählt schon die
      **zweite** solche Probe in Folge als gescheitert – und seit Stufe 3 schon die **erste**, wenn die letzte gewertete
      Probe (bestanden oder gescheitert) auf dieses Ziel (ab 0,9 × ihrer Zielrate, binnen 60 s) gescheitert war: In
      anhaltender Enge ging sonst jeder gescheiterten Probe genau eine unklare voraus (Messung `bwe13`: 21 von 24). Auf
      freier Leitung (letzte gewertete bestanden) bleibt der zweite Versuch.
    Preis: Auf einer engen Leitung, auf der GCC die Füllung anhält, bevor der Detektor etwas sieht, kommen so zwei
    kurze Proben (je 0,4–0,8 s Füllung, dann Nachlauf) statt einer je Abstand – vor der Rampen-Regel bis zu drei, und
    die dritte lief oft bis zur vollen Füllung und baute 50–100 ms Schlange. Im Modell 14–16 statt 7 Proben in
    10 min auf 1,0 Mbit/s, 0,33–0,38 statt 0,16 MB (vorher 21 Proben, 0,49 MB). Die eigene Warteschlange (`queue`)
    lässt die Probe weiter sofort scheitern. Bricht die Leitung mitten in der Probe ein, kommt der Rückzug im Modell
    0,2 s später als bei `gcc` (1,4 statt 1,2 s).
    Zeile `probe` bei `eigen` (sofort, die Probe hat den Nachlauf selbst abgewartet):
    `… est=1878k gcc=900k bwe=staut-nicht bwe_gcc=- bwe_gesendet=1940k bwe_bestaetigt=1940k bwe_schlange=0ms bwe_verlust=0.0% bwe_zustand=frei bwe_fenster=12`
    (`bwe` = das Urteil, nach dem gewertet wurde; `bwe_gcc` = Stausignal von GCC, das die Füllung angehalten hat,
    `-` = keins; `bwe_gesendet`/`bwe_bestaetigt` = der Nachweis, 0 = volle Füllung nicht erreicht).
  - **`eigen` – Stufe 2, Schicht-Entscheidung:** Fällt das GCC-Ziel unter die Rate, nimmt der Knoten bei `gcc` die
    Schicht zurück – auch wenn die Leitung frei ist (pion springt nach einer kurzen Pause der Leitung oder wegen
    Verlust-Meldungen zurück; im Testaufbau 11-mal in 1679 s auf freier Leitung, der Detektor jedes Mal `frei`). Bei
    `eigen` gilt: **Das Signal von GCC gilt weiter, solange der Detektor es nicht ausdrücklich widerlegt.** Widerlegt
    ist ein Stau nur, wenn alles zutrifft: Zustand `frei`, Basis mindestens 2 s alt, letzte Rückmeldung höchstens
    400 ms alt, das jüngste bestätigte Paket höchstens 600 ms zuvor gesendet, die **kleinste Laufzeit der letzten 5
    bestätigten Pakete höchstens 10 ms über der Basis**, Verlust der letzten 500 ms nicht gestiegen (höchstens 4
    Punkte über dem der 10 s davor) und unter 12 %. Dann beginnt – wie bei der Klemmung – eine Schonfrist beim neuen
    Ziel: Es muss von dort wieder steigen (1,5 % je Sekunde), längstens 15 s, und die Widerlegung muss **in jedem
    Takt neu gelten**. Fällt sie weg (Schlange an der Spitze, Verlust, Warnung, Rückmeldung alt), gilt das Signal von
    GCC sofort. Der Detektor nimmt von sich aus **keine** Schicht zurück – ein längerer Weg ohne ICE-Wechsel sähe
    sonst bis zu 30 s wie Stau aus (Grenze 3).
    Warum diese Richtung: Der *Zustand* des Detektors ist in echter Enge langsamer als GCC (Warnung nach 0,4–0,7 s,
    GCC 0,2–0,3 s; im Labor meldete er in 11 von 12 Schritten nichts, bevor der Knoten unten war) – auf seine Meldung
    zu warten hieße, echte Enge später zu behandeln. Die *Widerlegung* braucht weniger: Staut die Leitung wirklich,
    haben schon die jüngsten bestätigten Pakete eine Schlange (GCC schließt aus denselben Rückmeldungen auf
    Überlast). Die kleinste Laufzeit, weil nach einer Pause der Leitung die aufgehaltenen Pakete zusammen ankommen –
    das zuletzt gesendete hat kaum gewartet –, in einer wachsenden Schlange aber jedes gewartet hat. Modell (1520
    kbit/s auf 1000/1250/1400 kbit/s und Policer 1000): Die Widerlegung hält längstens 0,15/0,15/0,17/0,15 s nach
    Beginn der Enge – GCC meldet nach 0,2–0,3 s. Auf freier Leitung widerlegt sie in 99,9–100 % der Takte (Jitter,
    Pausen 30–90 ms), bei 3 % bzw. 5 % Zufallsverlust in 95–98 % bzw. 91–96 %.
    **Grenze:** Liegt die Leitung nur wenige Prozent unter der Rate, wächst die Schlange so langsam, dass die
    Widerlegung noch gilt, wenn GCC schon meldet – die Schicht bleibt dann, bis die Schlange 10 ms erreicht (Modell,
    Leitung 1,5 % bzw. 3 % unter `h`: 0,8 bzw. 0,6 s statt 0,4 s). Auf Leitungen mit starkem Jitter (LTE, ±20 ms)
    widerlegt die Spitze selten – dort bleibt es beim Verhalten von `gcc`.
    **Weitere Grenzen der Widerlegung** (Prüfung; Modell bzw. gerechnet, über UDP nicht nachstellbar): (a) **Puffer
    unter 10 ms bei 2–6 % Überlast** – die Schlange kann die Schwelle von 10 ms nie erreichen, und der Verlust bleibt
    meist unter der Schwelle für „gestiegen“ (4 Punkte über den 10 s davor): Die Widerlegung gilt dann in 63–80 % der
    ersten 15 s weiter, obwohl die Leitung zu eng ist; die Schicht bleibt, bis das Ziel nicht mehr steigt (Schonfrist,
    längstens 15 s). (b) **Verlust, der länger als rund
    10 s anhält**, steht im Vergleichswert (Verlust der 10 s davor) und gilt dann nicht mehr als gestiegen – ein Stau,
    der sich nur als gleichbleibender Verlust unter 12 % zeigt, ist danach für die Widerlegung kein Zeichen mehr –
    **seit Stufe 3b** außer, er folgt der Senderate bzw. kommt in Folgen (Policer-Merkmal, unten).
    (c) **Echter Policer** (Token-Bucket ohne Puffer): Eine Lücke bleibt nur bei 10–12 % Verlust – ab 10 % senkt der
    Verlust-Regler von pion das Ziel, und erst ab 12 % gilt der Verlust dem Detektor als hoch.
    **Policer-Merkmal (Stufe 3b):** Im Labor (`labbwe3`, `eigen`, Policer 1400 kbit/s, Eimer 200 ms) widerlegte der
    Detektor Fälle des GCC-Ziels, obwohl der Policer verwarf – der Verlust (4,25 %) blieb unter 12 % und lag nach einigen
    Sekunden im Vergleichswert. Unterscheiden lässt er sich vom Zufallsverlust an seiner Verteilung (Mitschnitt beim
    Empfänger, je 1 Lauf): am Policer Korrelation Senderate/Verlust je 100 ms 0,47, 71 % der Verluste in Folgen ab 3
    Paketen; beim Zufallsverlust 3 % 0,07 und 0 %. Der Detektor zählt deshalb je Messfenster die Verluste in Folgen ab 3
    (nach Folgenummer) und fasst die letzten 10 s zusammen (`bwe.Tip.Pol`). **Policer** = mindestens 10 verlorene Pakete
    und Folgen ab 30 % oder Korrelation ab 0,35 über mindestens 50 Fenster. Begründung: Bei unabhängigem Verlust p
    liegt ein verlorenes Paket mit 1 − (1−p)² (1+2p) in einer Folge ab 3 (3 %: 0,3 %, 10 %: 2,8 %, erst 30 %: 22 %);
    die Korrelation streut dort um 0 mit ≈ 1/√Fenster (50 Fenster: 0,14). Dann ist **jeder Verlust an der Spitze ein
    Zeichen** (`LossUp`): keine Widerlegung, kein Halten nach der Probe, kein entschuldigtes Bündel – wie bei `gcc`;
    gleichmäßiger Verlust bleibt unauffällig. Modell (`internal/bwe/policer_test.go`, Video mit Schlüsselbildern, je
    3 × 120 s): Policer 1400 (Eimer 100/200 ms) Merkmal in jedem Takt, kein Takt mit Verlust an der Spitze widerlegt
    (ohne Merkmal 2–39 von ≈ 150); Zufallsverlust 3/5/10 % nie, widerlegt wie bisher. **Grenze:** Bündelverlust ohne
    Bezug zur Rate (z. B. Funk) zählt über die Folgen ebenfalls als Policer – dann verhält sich `eigen` dort wie `gcc`.
    Labor: nur je ein Lauf; die Schwellen liegen deutlich unter den Laborwerten.
    **Halten nach bestandener Probe:** Eine Probe, die gegen GCC bestanden hat (Stufe 1), wäre wenig wert, wenn die
    Schicht-Entscheidung GCC gleich danach wieder gehorchte – auf einer Leitung mit 1400 kbit/s und 20 ms Puffer hielt
    von 6 Schritten `l`→`m` keiner länger als 0,2 s: Das Schlüsselbild der neuen Schicht kommt als Bündel, überläuft
    den kurzen Puffer, pion-GCC sieht 14–19 % Verlust und senkt das Ziel. Deshalb gilt **3 s lang nach einer
    bestandenen Probe:** Ein Fall oder Stillstand des GCC-Ziels unter der Rate nimmt die Schicht nicht zurück, solange
    der Detektor **kein Zeichen von Stau** zeigt – Zustand `frei`, Basis mindestens 2 s alt, Rückmeldung frisch,
    kleinste Laufzeit der letzten 5 bestätigten Pakete höchstens 10 ms über der Basis (die Bedingungen der
    Widerlegung ohne die zum Verlust). Gestiegener Verlust ist ein Zeichen – außer in den **ersten 1,2 s nach dem
    letzten Schritt nach oben** des Beins (das Schlüsselbild; gemessen stand der Verlust 0,7–0,8 s lang in den
    Rückmeldungen). Eine Schlange an der Spitze zählt sofort, ebenso die eigene Warteschlange des Knotens. Nach den
    3 s steht die Folge wie jede Widerlegung auf dem Detektor: Er muss in jedem Takt ausdrücklich widerlegen, und das
    Ziel muss wieder steigen. Sieht der Detektor nichts (Rückmeldung alt, Basis jung, Wegwechsel), gilt das Halten
    nicht. Das Halten gilt **für das Bein**, nicht für einen Platz (die Schätzung ist eine je Bein): In den 3 s ist
    auch ein weiterer Schritt nach oben ohne Probe gehalten – eine Kachel aus der Pause, ein anderer Platz –, und
    er setzt die 1,2 s für den Verlust neu an (längstens bis zum Ende der 3 s). Außerhalb der 3 s wird ein Schritt
    ohne Probe nicht gehalten. **Grenze:** Bricht die Leitung gleich nach dem Schritt ein und zeigt sich das nur als
    Verlust (Policer, Puffer unter 10 ms), wird das bis zu 1,2 s später behandelt als ohne das Halten – mit weiteren
    Schritten nach oben in der Zeit bis zu 3 s (Modell: 1,2 statt 0,4 s; über UDP gemessen 0,64 statt 0,44 s); mit
    einer Schlange so schnell wie bisher (über UDP 0,44–0,46 s in allen Läufen, vorher 0,43–0,44 s).
    **Eigenes Bündel in einer laufenden Widerlegung:** Ein Schlüsselbild geht als Bündel hinaus; auf einer Leitung
    mit Ratengrenze stehen seine Pakete hintereinander an, und die Spitze zeigt einen Takt lang eine Schlange (am
    Testaufbau 18–31 ms bei 1450–1520 kbit/s). Fiele die Widerlegung dabei weg, endete sie an jedem Schlüsselbild, das
    in sie fällt – gemessen rund jede zweite (5 von 11), mit Rückfall auf `l`, obwohl die Leitung `m` doppelt trug.
    Deshalb übersteht eine **laufende** Widerlegung **einen** Takt ohne ausdrückliche Widerlegung, wenn das Bein in
    diesem oder im vorigen Takt selbst ein Bündel gesendet hat (Medienrate mindestens 1,25 × der geglätteten Rate
    davor) und der Detektor sonst nichts zeigt (Zustand `frei`, Basis mindestens 2 s alt, Rückmeldung frisch, Verlust
    nicht gestiegen). Nicht zweimal binnen 300 ms – bei 200 ms je Takt also nicht zwei Takte in Folge (verspätet sich
    ein Takt unter Last, können zwei aufeinanderfolgende entschuldigt werden). Eine Widerlegung **beginnt** so nicht,
    und ohne eigenes Bündel gilt das Signal von GCC sofort wie bisher. Die Bedingung „eigenes Bündel“ trifft nicht nur
    auf ein Schlüsselbild zu: Sie gilt auch rund 1 s lang nach jedem Schritt nach oben (die geglättete Rate läuft
    nach) und bei Beinen mit wenigen Bildern je Sekunde in vielen Takten – deshalb die Begrenzung auf einen Takt.
    **Grenze:** Beginnt eine echte Enge in einem solchen Takt, während eine Widerlegung läuft (auch das Halten nach
    einer bestandenen Probe, sofern der Verlust nicht gestiegen ist), gilt das Signal von GCC einen Takt (0,2 s)
    später (Modell: 0,4 statt 0,2 s).
    Aufschub, Schonfrist, Sperrzeit, „ein Schritt je Takt“ und die Regeln des Probings gelten unverändert.
  - **`eigen` gegen `gcc` im Testaufbau** (pion-Gegenstelle hinter einer nachgebildeten Leitung; `TestUDPBWEMessung`;
    Stand nach der Nacharbeit, die Zahlen davor in Klammern).
    *Serie* (7 Fälle × 3 Durchgänge je Stellung, im Wechsel): Rückfälle auf freier Leitung in 1844 s: `gcc` 12 (12),
    `eigen` **0** (2); 18 (12) widerlegte Zielstürze, 17 davon endeten mit erholtem Ziel. Anteil auf `h` bei Jitter und
    Pausen: 83 % gegen 95 % (90 s) und 89 % gegen 96 % (240 s nach einer Enge). Beginn einer Enge mitten im Anruf
    (Policer 1000, Leitung 1000/1250/1450/1520 kbit/s, je 3-mal): der Platz fällt in allen 15 Fällen je Stellung 0,4 s
    danach (Raster 0,1 s) – kein Unterschied. Proben `m`→`h` auf Enge und Policer: `gcc` 33 von 33 gescheitert; `eigen`
    29 gescheitert, 12 unklar (wiederholt), **keine bestanden**. Auf freier Leitung: `gcc` 32 von 32 bestanden, `eigen`
    23 von 25 (zwei unklar, wiederholt), kein `fail` – `eigen` probt dort seltener, weil es seltener zurückfällt
    (Fülldaten der Serie 4,5 gegen 5,5 MB). Der Detektor meldete auf freier Leitung weder Warnung noch Stau. Nach der
    Freigabe einer Enge steht `h` bei `eigen` im Median nach 16 s (30 s Leitung 1000), 20 s (Policer), 11 s
    (Leitung 1000/1250) bzw. 5 s (1450/1520) wieder, bei `gcc` nach 14, 20, 17 und 14 s – das bestimmt der Abstand
    nach den gescheiterten Proben in der Enge (10 s, verdoppelnd), bei je 3 bis 6 Freigaben mit entsprechender
    Streuung.
    *Leitung knapp unter `h`* (1450/1520/1550 kbit/s, je 120 s, 6 Läufe je Stellung): Zeit auf `m`/`l` bei `eigen`
    687/30 s (685/32), bei `gcc` 547/170 s; Proben 46 (59) gegen 31, Fülldaten 2,0 MB (2,8) gegen 1,1 MB. Die
    Rampen-Regel hat die unklaren Proben von 36 auf 21 gesenkt – je Folge bleibt eine; „nahe `gcc`“ ist das nicht.
    Von den 7 Rückfällen auf `l`, die `eigen` dort noch hat, tragen 6 den Grund `gcc-steht`: Das Ziel erholt sich
    nach einem widerlegten Sturz und stürzt erneut, bevor die Folge geschlossen ist (offen). Von 16 Widerlegungen
    endeten 10 mit erholtem Ziel, keine mehr an einem Schlüsselbild.
    *Leitung 1400 kbit/s mit 20 ms Puffer:* über 120 s (2 Läufe) `eigen` 214/25 s auf `m`/`l` (62/177), `gcc` 125/114 s.
    Gezielt (Beginn auf 500 kbit/s, dann 1400/20 ms, je 4 Läufe): Nach einer bestandenen Probe `l`→`m` hielt `m` bei
    `eigen` in 5 von 5 Schritten länger als 3 s (3,8 bis über 56 s), vor der Nacharbeit fielen 3 von 7 nach 0,2–0,4 s
    zurück. Der kürzeste (3,8 s) endete nach dem Halten an einem Verlust-Schub durch ein Schlüsselbild, als das
    GCC-Ziel noch unter der Rate lag.
    *Knapp über `h`* (1600/1700 kbit/s, 4 Läufe): `eigen` verlässt `h` nach 1,2–1,6 s, `gcc` nach 0,4–0,8 s (Grenze
    oben). *Einbruch gleich nach einer bestandenen Probe* (auf 1000 kbit/s, je 4 Läufe): mit Schlange (Puffer 300 ms)
    `h` verlassen nach 0,44–0,46 s (vor der Nacharbeit 0,43–0,44 s, `gcc` 0,43–0,45 s); nur als Verlust sichtbar
    (Puffer 5 ms) 0,46–0,65 s (0,42–0,47 s, `gcc` 0,43–0,64 s).
    `TestUDPProbing` mit `eigen`: vor der Nacharbeit 50 von 50 Läufen grün (auf 1,2 Mbit/s 129 Proben: 50 gescheitert,
    56 unklar; `h` kam dort nur mit dem freien Versuch zum Anrufbeginn), danach in allen Läufen der Abschlussprüfung.
    Mit echtem Browser ist `eigen` noch nicht gemessen.
  - Zähler und Zustand je Bein: `GET /api/sfu/leg/{id}` → `"bwe"` (nur bei `mit` und `eigen`): `mode`, `state`, `why`,
    `queue_ms`, `loss`, `acked_kbit`, `cap_kbit`, `age_ms` (Alter der letzten Rückmeldung, −1 = keine), `sent {video,
    audio, rtx, pad, dup}`, `dup_unacked` (Doppel ohne Bestätigung – kein Verlust; beide seit dem Abgleich im
    Weiterleiter erwartet 0), `feedbacks`, `bad` (nicht
    entpackbar), `acked`, `lost`, `dup` (doppelt gemeldet), `recovered` (als verloren gemeldet, später doch
    angekommen), `unknown` (Folgenummer nicht im Ring), `windows`, `thin` (Fenster mit zu wenig Paketen), `late`,
    `resets`, `to_free`, `to_warn`, `to_cong`, `to_unknown`, `policer {on, run_pct, corr, lost, windows}` (Merkmal,
    siehe oben) und `win` (Zähler je Messfenster der letzten 10 s, ältestes zuerst: `[gesendet kbit/s, Pakete,
    verloren, davon in Folgen ab 3]` – fürs Labor); bei `eigen` dazu `refuted` (Fälle des GCC-Ziels unter
    die Rate, die der Detektor widerlegt hat), `held` (Folgen, deren Widerlegung mit dem Halten nach einer
    bestandenen Probe begann – eine als `spitze` begonnene Folge, die später nur das Halten trägt, zählt bei
    `refuted`) und `bursts` (Takte einer laufenden Widerlegung, die ein eigenes Bündel entschuldigt hat, auch wenn
    die Folge im selben Takt aus anderem Grund endete).
  - Bekannte Grenzen: (1) **Wiederholungen sind unsichtbar** – der NACK-Antworter liegt in der Kette vor dem
    Schätzer und schreibt an ihm und am TWCC-Kopf vorbei; weder pion-GCC noch der Mitleser sehen sie (`sent.rtx`
    bleibt 0; sie tragen die transport-cc-Nummer des Originals und erscheinen als `recovered`). (2) **Niedrige
    Paketrate:** Auf `l` ohne Ton (~25 Pakete/s) dauert ein Fenster bis 0,5 s, Stau wird erst nach rund 2 s
    gemeldet; der Verlust ist dort erst nach 60 Paketen (gut 2 s) bestimmt. (3) **Längerer Weg ohne ICE-Wechsel**
    (Laufzeit steigt bleibend, z. B. +80 ms, ohne dass ICE ein anderes Paar wählt) sieht bis zu 30 s wie Stau aus,
    bis die Basis herausgealtert ist – bei `eigen` gilt in der Zeit das Signal von GCC wie bei `gcc`, und Proben
    scheitern. (4) In der Enge ist der Zustand des Detektors langsamer als GCC: Warnung nach 0,4–0,7 s, Stau nach
    0,8–1,0 s (GCC meldet Überlast nach 0,2–0,3 s) – die Rückmeldung über ein Paket in der Schlange kommt um die
    Schlange später. Fällt der Knoten vorher zurück (nach 0,4 s), läuft die Schlange ab, und der Detektor meldet bei
    knapper Enge oder kurzem Puffer (Policer) oft nur `warnung`, keinen `stau`. (5) Urteile über Proben, die GCC
    früh anhält, bleiben oft `unklar`. (6) **Anrufbeginn mit Chromium:** In den ersten 1–2,5 s meldet der Browser
    5–75 % der Pakete als nicht angekommen, auch ohne jeden Engpass – nach heutigem Stand die Doppel des sendenden
    Browsers (oben; mit der pion-Gegenstelle der Tests tritt es nicht auf). Der Abgleich im Weiterleiter und die
    Schonzeit fangen es ab; ob danach nichts übrig bleibt, zeigt erst das Labor. (7) **Wechselt ICE zwischen zwei
    gleichwertigen Adresspaaren hin und her** (im Container alle 2–5 s, weil der Knoten auf zwei Adressen erreichbar ist; im Labor
    nicht beobachtet), verliert der Detektor jedes Mal seine Basis: ein Fenster `unbekannt`, und bei `eigen` für 2 s
    weder Widerlegung noch `staut-nicht` – das Bein verhält sich dann wie bei `gcc`, Proben bleiben unklar. (8) Nicht
    als Doppel erkennbare unbestätigte Pakete ohne Verzögerung ergeben `warnung`, ab gut 1 s Dauer `stau`
    (Modell, Schübe von 0,3–0,9 s mit 45–85 %: 108–134 Warnungen in 3 × 30 min, kein Stau).
  - Rechenlast: je abgehendem Paket rund 25 ns, je Rückmeldung mit 16 Paketen rund 0,8 µs, keine Allokation
    (`go test ./internal/bwe -bench .`). Rechenzeit eines ganzen Laufs (Knoten und Bots in einem Prozess, 60 s auf
    `h`, je 3 Läufe; Stufe 0): 1:1 `gcc` 3,32 s, `mit` 3,34 s; ein Sender und 8 Empfänger `gcc` 8,48 s, `mit` 8,63 s –
    der Unterschied (+0,5 % bzw. +1,7 %) liegt in der Streuung der Läufe.
  - Tests: `internal/bwe/*_test.go` (Detektor an einer künstlichen Leitung: Jitter, Pausen, wachsende Schlange,
    Verlust, Policer, Wegwechsel, Stille, dünner Strom, Probe; `eigen_test.go`: Zufallsverlust 3 % und 5 % über
    30 min, unbestätigte Pakete ohne Verzögerung und Doppel, Schonzeit, Dauerverlust, junge Basis, Policer, Spitze,
    Raten; Entpacker; Zuordnung), `internal/sfu/eigen_test.go` (Raten-Modell mit Detektor: grundloser Zielsturz,
    echte Enge, Wertung der Probe, Einbruch in der Probe, Kennzahlen `gcc` gegen `eigen` mit `-v`),
    `internal/sfu/halten_test.go`, `rampe_test.go` und `buendel_test.go` (Halten nach bestandener Probe samt
    Gegenprobe „Einbruch gleich nach dem Schritt“, zweite Rampen-Probe, Nachweis „bestätigt ≥ Bedarf“, Ereignisse fürs
    Log, eigenes Bündel in einer laufenden Widerlegung),
    `internal/bwe/halten_test.go` (Spitze bei Schlüsselbild-Bündel, Enge und Policer),
    `internal/fwd` (`TestDoppelDerQuelle`), `pruef12_test.go` (`TestPruefDoppelGCCVerlust`: pion-GCC mit und ohne
    Abgleich), `bwe_udp_test.go` (Schalter, Zeilen, Doppel in der Hülle, Ende zu Ende mit
    pion-Gegenstelle bei `mit` und `gcc`, Zeile `probe` sofort und nach dem Nachlauf; mit
    `HORN_BWE_MESSUNG=<fall,…>`, `HORN_BWE_ART=gcc|mit|eigen` und `HORN_BWE_OUT=<ordner>` Messläufe – auch frei
    geplante (`HORN_BWE_PLAN`) und die Gegenprobe zum Halten (`HORN_BWE_NACHPROBE`) –, mit
    `HORN_BWE_LAST=<s>[,<empfänger>]` Rechenzeit eines Laufs), `probing_udp_test.go` (`TestUDPProbingEigen`: der
    Ablauf von `TestUDPProbing` mit `eigen`; `TestUDPProbing` selbst mit `HORN_BWE_ART=eigen`).
- **Vorrang und Verwerfen (H3):**
  - Die Warteschlange je Bein arbeitet nach Vorrang: Ton > Bildschirm > Sprecher > Kacheln.
  - Ist ein Rahmen älter als 150 ms, verwirft der Knoten Bilder bis zum nächsten Schlüsselbild und fordert eine PLI
    an (höchstens 1 je Quelle und 500 ms). Ton wird nie verworfen.
- **NACK/RTX und TWCC:** NACK/RTX gibt es nur auf UDP. TWCC-Rückmeldung für den Uplink der Geräte auf allen Wegen.
  Opus auf TCP: ptime 40, ohne FEC, mit DTX.
- **Doppel und Nachzügler (Abgleich je Quelle, `internal/fwd`):** pion gibt RTX-Pakete des sendenden Geräts entpackt mit
  der ursprünglichen Folgenummer zurück – Nachsendungen nach einem NACK, aber auch Wiederholungen, mit denen ein
  Browser füllt bzw. probt. Vor der Verteilung gleicht jede Quelle deshalb mit einem Fenster der letzten 1024
  Folgenummern ab (`Source.admit`; Lese-Goroutine, ohne Sperre und Allokation, 2,3 KiB je Quelle):
  - **Das erste Exemplar geht durch, jedes weitere wird verworfen und gezählt** (`dups`) – Bild und Ton, auch auf der
    Brücke und für reine Füllpakete des Absenders. Ein Doppel zählt weder in Rate und Bildrate der Schicht noch als
    Schlüsselbild oder Lebenszeichen. Vorher ging es mit derselben Folgenummer an jeden Empfänger (im Labor 11–17 %
    mehr Pakete, im Einzelfall das Doppelte), der es ungezählt und unbestätigt verwarf – für die Schätzung des Knotens
    sah das wie Verlust aus –, und es verstellte den Ausgang: Schlüsselbild nach einem Schichtwechsel auf einer schon
    vergebenen Nummer, Versatz während eines ausgelassenen Bildes eins zu weit, ein doppeltes Füllpaket zählte zweimal.
  - **Spätes erstes Exemplar** (das Original ging auf dem Weg zum Knoten verloren und kommt per NACK nach, oder
    Umsortierung): Es geht mit der Folgenummer und PictureID **seiner Stelle** hinaus – der Lücke, die der Empfänger
    sieht und nachfordert. Dafür merkt sich jede Stelle, die ein Ausgang überspringt, wie er an ihr vorbeiging (Ring,
    256 Stellen, 2,5 KiB je Ausgang): Versätze, ob er dort Bilder ausließ oder bis zum Schlüsselbild verwarf, welche
    Zeitschicht galt. Das späte Paket ändert den Stand des Ausgangs nicht (nächste Folgenummer, Bildzustand,
    Schichtwechsel) und geht nicht hinaus, wenn der Ausgang an seiner Stelle verwarf, seine Zeitschicht ausließ oder
    die übrigen Pakete seines Bildes ausgelassen hat. Ist die Stelle älter als der Ring (bei der hohen Schicht rund
    1,5 s; der Knoten fordert etwa 1 s lang nach), wird es verworfen und gezählt (`late_dropped`) – nie landet es auf
    einer schon vergebenen Nummer. Umgekehrt setzt ein Schichtwechsel bzw. Neuanfang der Quelle hinter der höchsten
    Folgenummer und PictureID und nach dem jüngsten Zeitstempel an, die der Ausgang **je geschrieben** hat – auch von
    einem Nachzügler (ein ganz nachgekommenes Bild kann jünger sein als das letzte in Reihe geschriebene): Lag dessen
    Lücke direkt hinter dem letzten in Reihe geschriebenen Paket und kam danach nur Ausgelassenes, trüge das
    Schlüsselbild sonst seine Nummer (der Empfänger verwürfe es). Lücken, die beim Wechsel noch offen sind, verfallen
    – ein Nachzügler von davor geht danach nicht mehr hinaus; ohne ihn schließt der Wechsel lückenlos an wie bisher.
    Ein verspätetes Füllpaket des Absenders rückt nichts mehr; an seiner Stelle bleibt für die Empfänger eine Lücke,
    die niemand füllt (sie fragen höchstens zehnmal nach).
  - **Älter als das Fenster:** verworfen und getrennt gezählt (`too_old`). Das Fenster reicht weiter zurück, als der
    Knoten nachfordert (512 Nummern) und als libwebrtc Pakete für Wiederholungen vorhält (rund 600 bzw. 1–3 s).
  - **Sprung über das Fenster hinaus:** Trägt das Paket einen jüngeren Zeitstempel als alles bisher Gesehene (lange
    Unterbrechung; Brücke, deren Ausgang auf der Gegenseite nach einer Pause neu beginnt), setzt die Quelle das Fenster
    sofort neu auf – nichts geht verloren. Das gilt auch, wenn die neue Folge nach einem Umlauf knapp **hinter** dem
    alten Stand beginnt, also im Fenster (Brücke nach sehr langer Pause): Ein Paket hinter dem Stand, dessen
    Zeitstempel mehr als 10 s (bei 90 kHz) jünger ist als alles Gesehene, kann keine Nachsendung sein. Springt die
    Folge rückwärts, zählt jeder Ausgang lückenlos weiter, statt alte Nummern noch einmal zu vergeben. Ein Neustart
    mit fremder Zeitbasis gilt ab dem zweiten Paket in Reihe (wie RFC 3550, das erste davon geht verloren); ein
    einzelnes Paket von weit zurück ist zu alt. **Alte Nachsendungen** – hinter dem Stand, älter als das Fenster,
    Zeitstempel weniger als rund 47 s (90 kHz) zurück – beginnen keine neue Folge, auch nicht zwei in Reihe (bei sehr
    hoher Umlaufzeit kommen Nachsendungen am Stück; die Folge setzte sonst rückwärts neu an). Preis: Eine fremde
    Folge, die rückwärts beginnt und deren Zeitstempel zufällig so nah liegt (rund 0,1 %), gilt erst als neu, wenn
    ihr Zeitstempel jünger wird oder ihre Nummern den alten Stand erreichen.
  - **Grenzen:** Das Verhalten der Browser dazu ist am Code von pion und libwebrtc gelesen, nicht am Gerät gemessen.
    Fehlt der Anfang eines Bildes, läuft sein Rest wie bisher unter dem Zustand des Bildes davor. Ein später
    Rahmenanfang wird nach der Zeitschicht-Grenze beurteilt, die galt, als der Ausgang an seiner Stelle vorbeiging.
  - **Zähler:** `GET /api/stats` → `dups`, `too_old`, `late` (späte erste Exemplare, weitergegeben), `late_dropped`
    (alle in `pkts_in` enthalten). `GET /api/leg/{id}` und `stats` in `GET /api/sfu/leg/{id}` → `sources[]` je
    laufender eingehender Spur des Beins (`kind`, `layer`, `pkts` ohne Doppel, `dups`, `too_old`, `late`) und
    `late_dropped` des Beins als Empfänger. Protokollzeile `bein zu … doppel=… alt=… spaet=… spaet_verworfen=…
    grund=…` – darin zählen auch die Spuren mit, die vor dem Ende des Beins endeten (sie stehen nicht mehr in
    `sources[]`).
  - Tests: `internal/fwd/doppel_test.go` (Umlauf der Folgenummern, Sprung und Neustart, Schichtwechsel, Nachsendung,
    ausgelassene Bilder, Verwerfen, Füllpakete, Kontingent, Ton, Zufallslauf gegen einen ungestörten Vergleichslauf;
    Nachzügler direkt vor einem Schichtwechsel bzw. Neuanfang, alte Nachsendungen in Reihe, Sprung knapp zurück,
    Zufallslauf mit Schichtwechseln: keine Folgenummer und keine PictureID zweimal).
    Kosten (`BenchmarkForward1`/`BenchmarkFanout10`): rund 20 ns je Paket und Quelle, 8 ns je Ausgang, keine Allokation.
- **Rahmen bleiben opak:** Gelesen wird nur der VP8-Descriptor (Schlüsselbild, TID, PictureID) und das P-Bit. Der
  E2E-Trailer wird nie angefasst.

### Erwartungen an Clients (für P5 und die Apps)

- **Simulcast auf TCP-Wegen:** `l` mit `scaleResolutionDownBy` 4, 20 fps und `scalabilityMode: 'L1T2'` (der Knoten
  nimmt davon 10 fps für Kacheln) sowie `m` mit 30 fps `L1T3`. Auf UDP zusätzlich `h`.
- Bildschirm: Die Stream-ID beginnt mit `screen`.
- **Jitter-Puffer:** Auf TCP-Wegen für empfangene Bilder `receiver.jitterBufferTarget = 150` (ms) setzen, siehe
  Messung unten. Geräte, die das nicht können (Apps mit webrtc-sdk M150), bekommt der Knoten dasselbe über die
  Kopferweiterung **playout-delay** (unten).
- Ton und Bild auf getrennten PeerConnections über TCP (H1).

### Hinter einer TLS-Durchreiche (SNI-Proxy: Traefik `HostSNI`, NetBird, HAProxy `mode tcp`) – A1

H2, H3, der Nachläufer und die TCP-Schätzung wirken nur auf **Sockets, die der Knoten selbst annimmt**
(`internal/netx/demux.go` `handle` → `Tune`). Hinter einer Durchreiche ist das die Verbindung *vom Proxy*. Die
Verbindung *zum Gerät* gehört dem Proxy und hat dessen Standard: kein `TCP_NOTSENT_LOWAT`, Sendepuffer per Autotuning
bis `tcp_wmem`-Maximum (oft 4 MB, bei 600 kbit/s fast eine Minute Stau), meist CUBIC. Folgen (Labor 2.11.): Der Stau
liegt im Proxy statt im Knoten. Das Alter der Warteschlange bleibt klein, also verwirft der Knoten nichts (H3), und die
Schätzung bleibt beim Startwert `est=1200`. Der Nachläufer sieht RTT ≈ 0 ohne Wiederholungen und bleibt aus. Beim
Bildschirmteilen wächst die RTT auf mehrere Sekunden.

Gegenmittel ohne Codeänderung: Auf dem **Proxy-Host** die Voreinstellung für alle Sockets setzen. Der Proxy blockiert
dann beim Schreiben und liest langsamer vom Knoten. So kommt der Rückstau beim Knoten an, und H3 und die Schätzung
greifen wieder:

```
# /etc/sysctl.d/90-saga-horn.conf auf dem Rechner mit dem SNI-Proxy (bzw. dem NetBird-Proxy/VPS)
net.ipv4.tcp_notsent_lowat = 16384
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
```

Läuft der Proxy auf demselben Host wie der Knoten, reicht dieselbe Datei dort. Die Wirkung (Freezes/min und RTT beim
Bildschirmteilen über TLS, vorher und nachher) ist bisher nur im Labor messbar. Die Web-Clients deckeln auf TCP/TLS
zusätzlich selbst (`static/horn.js` `K.TX`/`TxGov`).

## Einfrieren auf TCP (5G-Messung, G4)

Eine Messung über TURN-TLS/5G mit dem Spiegel ergab 5,45 Einfrieren je Bildplatz und 5 min. Ursache ist
Head-of-Line-Blocking auf dem TCP-Weg, nicht fehlende Schlüsselbilder:

- Im Regressionstest `TestFreezeLossyTCP` (80 ms RTT, 1,5 % Verlust, Nachbildung `internal/impair`, denn netem fehlt
  hier) gibt es 0 Pausen, 0 verworfene Rahmen und eine PLI-Antwort p95 von 128 ms.
- Ein verlorenes Segment hält alles dahinter 1,25–2,5 RTT auf. Die Einfrier-Schwelle der Browser liegt bei
  „Mittel + 150 ms“, ein 30-fps-Bild friert also schon bei etwa 150 ms Stau.

| Maßnahme | Wirkung in der Nachbildung |
|---|---|
| Kacheln 10 fps (Schwelle 300 ms) | Kacheln frieren kaum noch |
| Nachläufer | längster Stau 300 → 160 ms, bei 2 % Verlust ein Drittel weniger Einfrieren (streng gemessen) |
| `jitterBufferTarget` 150 ms im Client | 0 Einfrieren bei 1,5 % und 2 % Verlust (Ziel ≤ 1 je 5 min) |
| vorbeugende Schlüsselbilder | keine (auf TCP geht nichts verloren), daher aus (`KeyframeEvery`) |
| kleinere Sprecher-Bildrate | kaum (Schwelle ist Mittel + 150 ms), daher optional (`TCPSpeakerFPS`) |

### playout-delay auf TCP-/TLS-Beinen (P9)

Die Apps (webrtc-sdk M150) können `jitterBufferTarget` nicht setzen – das war die Gegenmaßnahme gegen das Einfrieren
über TCP (5G-Messung: 5,45 → 0 Einfrieren je 5 min). Deshalb setzt der Knoten auf TCP- und TLS-Beinen für Bild die
RTP-Kopferweiterung `http://www.webrtc.org/experiments/rtp-hdrext/playout-delay` mit **min 150 ms, max 400 ms**
(`HORN_PLAYOUT_DELAY="150,400"`, `0` = aus):

- Ausgehandelt im Knotenangebot nur für Video und nur auf den TCP-/TURN-APIs (nicht UDP). Bietet das Gerät die
  Erweiterung nicht an, bleibt sie aus (`TrackLocalContext.HeaderExtensions`).
- Geschrieben auf allen Paketen von **Schlüsselbildern** je Ausgang (der Empfänger behält den Wert bis zum nächsten; nach
  jedem Schicht- oder Quellwechsel kommt ohnehin ein Schlüsselbild), ohne Allokation (Kopf und Nutzlast im Ausgang
  wiederverwendet; `TestPlayoutDelay`). Die Brücke reicht Pakete unverändert weiter und setzt nichts.
- Nachweis in Chromium (`e2e/playout.sh`, 2 Geräte über ICE-TCP, Empfänger ohne `jitterBufferTarget`, 10 s
  `getStats`): ohne Erweiterung `jitterBufferTargetDelay` je Rahmen 11 ms / `jitterBufferDelay` 8 ms, mit
  138 ms / 130 ms. libwebrtc übernimmt den Wert vom SFU.

## Interne Go-API für die Steuerung (P3)

```go
n := sfu.NewNode(sfu.Config{Name: "horn-1.example.org", Verifier: v /* Ed25519, P3 */, OnLayout: f, OnLegClosed: g})
r, _ := n.CreateRoom(callRef, &sfu.Limits{MaxPeers: 50})   // idempotent
g, r, err := n.Admit(token, sfu.PathTCP)                    // Token prüfen, Raum muss existieren
l, err := r.AddLeg(g, path, sfu.Want{Audio: 3, Speaker: true, Tiles: 9})
r.RemoveLeg(legRef); r.RemovePeer(peer); n.CloseRoom(callRef)
n.Load()                                                    // Lastmeldung §2.6
```

- `sfu.Verifier` ist eine Schnittstelle: Ed25519-Prüfer für Saga-Token (`horntoken.go`), HMAC-Prüfer nur im
  Entwicklungsmodus.
- `Grant` enthält `Node`, `Room` (call_ref), `Peer` (peer_ref), `Leg` (leg_ref), `Role`, `Pub` (a/v/s), `MaxKbit`,
  `UDPOK`, `Device`, `Exp` und `ID` (jti).
- HTTP im Knoten:
  - `POST /api/sfu/leg {token, path, offer, audio, speaker, screen, tiles, device}` → `{id, answer, csrc, slots}`
  - `POST /api/sfu/turn {token}` → TURN-Zugang (10 min)
  - `GET /api/sfu/leg/{id}` → Belegung und Schätzung

## Saga-Steuerung (P3)

- **Eingebauter Knoten:** saga-media startet `gjallarhorn node`, solange das Feature-Flag `calls.horn` an ist
  (`media/horn.go`). Umgebung: `HORN_MODE=node`, `HORN_LISTEN=:HORN_PORT` (Standard 8443), `HORN_DATA=STATE_DIR/horn`,
  `HORN_SAGA_CTL=ws://127.0.0.1:<intern>/api/horn/ctl` und das Geheimnis `HORN_CTL_KEY`. Weitere `HORN_*` (Name,
  `HORN_PUBLIC_URL`, `HORN_TLS_*`, `HORN_UDP` …) reicht saga-media durch. Saga selbst bekommt nie UDP.
  - UDP-Kandidat = öffentliche Adresse (`HORN_PUBLIC_IP` bzw. Adressen von `HORN_NAME`) + **lokaler** UDP-Port
    (`HORN_UDP_LISTEN`, sonst `HORN_PORT`). Hinter einer TLS-Durchreiche (Traefik `HostSNI`) kommt kein UDP an: den Port
    direkt mit gleicher Nummer veröffentlichen. Der Selbsttest (`udp_check` im `hello`: `ok`/`aus`/`fehlt`) steht in
    Admin → Anrufe in der Spalte „Weg“.
- **Steuerkanal `horn-ctl/1`** (`ctl.go`, Gegenstück `saga/horn.py`): ausgehende WebSocket-Verbindung. `challenge` →
  `hello` (signiert mit dem Knotenschlüssel `HORN_DATA/node.key`) → `welcome {node_id, saga_pub, origin, rooms}`, danach
  Folgenummern je Richtung. Saga → Knoten: `open`, `close`, `kick`, `ping`. Knoten → Saga: `load` (alle 5 s: cpu, mem,
  legs, calls, udp_share, fwd_p95_us, drops, state), `closed`, `pong`. Neuverbindung mit Backoff 1–30 s; beim
  Verbinden schließt der Knoten Räume, die Saga nicht mehr kennt. Gekoppelte Knoten (Erweitern) folgen mit P6.
- **Medien-Token** (`horntoken.go`): `h1.<payload>.<sig>`, Ed25519 über `"saga-horn-token/1." + payload`, Schlüssel
  aus `welcome` (oder `HORN_SAGA_PUB`). Geprüft: Signatur, Form der Kennungen, Lebensdauer ≤ 120 s, Knotenname,
  Ablauf, UDP nur mit `udp_ok`. Jedes Token gilt **einmal** je Zweck (Bein bzw. TURN-Zugang, `jti`).
- **Räume:** Ein gültiges Token legt einen unbekannten Raum selbst an (die Anlage über `open` darf später kommen).
  Geschlossene Räume und entfernte Teilnehmende (`kick`) bleiben 3 min gesperrt, damit noch gültige Token nicht
  wieder hereinkommen. Leere Räume schließt der Knoten nach 2 min.
- **CORS:** `/api/sfu/*` und `/probe` nehmen die Herkunft der eigenen Saga an (`welcome.origin`, `HORN_SAGA_ORIGIN`).
  Saga ergänzt dafür `connect-src` der Seiten um die Knotenadressen.
- **Ohne Steuerkanal** (fester Saga-Schlüssel `HORN_SAGA_PUB`): Ein gültiges Saga-Token legt seinen Raum ebenso selbst
  an (P5; bis P6 für Tests mit einem zweiten Knoten). Räume schließt dann nur der Knoten selbst (leer nach 2 min).
- **Mitschnitt nur für Tests:** `HORN_CAPTURE=<ordner>` schreibt die ersten 3000 Pakete jeder eingehenden Spur roh
  heraus (`tests/anrufe_horn_knoten.py` prüft damit, dass nur verschlüsselte Rahmen ankommen).

## Weitere Knoten (P6/P7): Kopplung, Steuerung, Updates, Zertifikate

**Kopplung („Erweitern“):** Das Deploy-Skript aus dem Admin-Bereich setzt `HORN_SAGA`, `HORN_PAIR` (einmaliger Code,
30 min) und `HORN_NAME`. Beim ersten Start erzeugt der Knoten `HORN_DATA/node.key`, fragt `GET /api/horn/whoami` je
über IPv4 und IPv6, macht den UDP-Selbsttest und ruft `POST /api/horn/pair` mit
`sig = Ed25519(node_sk, "horn-pair/1\n" + code + "\n" + name)`. Saga prüft zurück:
`GET https://<ip>:<port>/api/horn/echo?n=<nonce>` (TLS ohne SNI, Rückfall-Zertifikat) muss mit
`Ed25519(node_sk, "horn-echo/1\n" + n + "\n" + name)` antworten; UDP per STUN-Binding. Antwort
`{node_id, saga_pub, ctl_url, name}` landet in `HORN_DATA/pair.json`; `saga_pub` bleibt angeheftet. Fehler 403/410
sind endgültig (neuen Code erzeugen), 424 (nicht erreichbar) versucht der Knoten mit Backoff erneut.

**Steuerkanal** wie beim eingebauten Knoten, aber ohne gemeinsames Geheimnis: Saga erkennt den Knoten am Schlüssel aus
der Kopplung (gesperrt: 4410, anderer Name: 4409). Zusätzlich: Saga → Knoten `drain {on}`, `peers {solo}`,
`version {want}`, `cert {cert}`, `acme_txt` (Antwort), `remove {sig}` mit
`Ed25519(saga_sk, "saga-horn-remove/1\n" + node_id + "\n" + name)`; Knoten → Saga `addr {v4, v6}` (alle 10 min neu
ermittelt), `cert {mode, state, not_after, err}`, `update {state, want, have, err}`, `acme_txt {rid, op, name, value}`.
Die Lastmeldung trägt `up`/`down` (Mbit/s, aus den Bytezählern) und etwa 180 Byte.

**Leeren:** Während des Leerens lehnt der Knoten neue Beine ab (429), laufende bleiben, Saga meldet den Geräten
`call.horn_move` und schlägt andere Knoten vor.

**Wächter und Selbst-Update** (ohne Docker-Socket): `gjallarhorn watch` startet `node` als Kind. Zielversion =
Saga-Version. Der Knoten lädt `<HORN_UPDATE_BASE>/v<ver>/gjallarhorn-linux-<arch>` und `.sig` (Vorabversionen wie
`2.5.0-rc.131` vom Branch testing: `<HORN_UPDATE_BASE>/horn-testing/gjallarhorn-<ver>-linux-<arch>` – rollendes
Vorab-Release, das die CI bei jedem testing-Push ersetzt), prüft gegen den festen
Release-Schlüssel und legt `HORN_DATA/bin/next` ab. Mit weiteren Knoten: Leeren, Neustart wenn leer oder nach 10 min;
als einziger Knoten erst ohne Beine (Hinweis nach 24 h). Exit 75 → der Wächter prüft erneut und startet `bin/current`;
stürzt es dreimal binnen 30 s ab, fällt er auf das Image-Binär zurück (`bin/bad`). Nach `remove` startet er nicht mehr
(`HORN_DATA/removed`).

**Release-Schlüssel (einmalig von den Maintainern einzurichten):**
1. `python3 tools/horn-release.py keygen` (offline) → `secret: …` als GitHub-Secret `HORN_RELEASE_KEY`, `pub: …` in
   `gjallarhorn/release.pub` eintragen und committen.
2. In der Release-Action je Architektur `gjallarhorn/build.sh <version>` und
   `HORN_RELEASE_KEY=${{ secrets.HORN_RELEASE_KEY }} python3 tools/horn-release.py sign <version> dist/gjallarhorn-linux-<arch>`
   ausführen und `gjallarhorn-linux-<arch>` + `.sig` als Release-Assets hochladen (noch nicht in `release.yml`).
3. Ohne Schlüssel melden Knoten `update: aus` und die systemd-Variante des Deploy-Skripts ist gesperrt.

**Zertifikate (P7):** Gekoppelte Knoten starten mit einem selbst signierten Rückfall-Zertifikat (nur für fremde oder
fehlende SNI und bis zur Ausstellung). Mit Cloudflare-Token in Saga gilt `dns01`: Der Knoten macht ACME selbst und lässt
Saga nur `_acme-challenge.<eigener Name>` setzen und wieder löschen (`HORN_ACME_URL`, `HORN_ACME_DNS_WAIT`, Standard
20 s). Ohne Token `alpn` (TLS-ALPN-01 auf demselben Port). Eigene Dateien in `/data/tls/` ohne `dns01.json` bleiben
unberührt. Erneuerung 30 Tage vor Ablauf.

Umgebung: `HORN_SAGA`, `HORN_PAIR`, `HORN_NAME`, `HORN_LINK_MBIT` (Upload für die Auslastung), `HORN_PUBLIC_PORT`,
`HORN_UPDATE_BASE`, `HORN_UPDATE_RETRY` (nur Tests), `HORN_ACME_URL`, `HORN_ACME_EMAIL`, `HORN_ACME_DNS_WAIT`,
`HORN_INSECURE_SAGA=1` (http zu Saga außerhalb von localhost, nur Tests).

## Ereigniskanal, Leeren, Bildschirm (P5)

- `GET /api/sfu/leg/{id}/events` (WebSocket, Herkunft wie `/probe`; die Bein-Kennung ist die Berechtigung):
  `hello {leg, peers, slots, est_kbit, state}`, danach `peers` (Teilnehmende geändert: beim Beitritt sofort aus
  `Room.AddLeg`, also bevor das neue Gerät Medien schickt; beim Gehen im nächsten Takt), `layout` (aus
  `Config.OnLayout`), `state` (`ok`/`draining`), `closed {why}` (Bein vom Knoten beendet) und `pong {n}` auf
  `{t:"ping", n}` (höchstens alle 150 ms). 32 Nachrichten Puffer je Verbindung; wer nicht liest, fliegt raus.
  `sfu.Config.OnPeers` meldet die Änderungen der Teilnehmenden.
- `POST /api/sfu/leg` liefert zusätzlich `peers` (alle Teilnehmenden mit CSRC) und `state`.
- **Leeren:** `SIGUSR1` = keine neuen Beine (429 „knoten wird geleert“), Lastmeldung `state: draining`, Ereignis an alle
  Geräte, die daraufhin make-before-break den Knoten wechseln; `SIGUSR2` hebt es auf. Ebenso der Steuerbefehl `drain` aus dem
  Admin-Bereich bzw. das Update (P6, `server.setDrain`).
- **Bildschirm:** Eine Spur gilt als Bildschirm, wenn ihre Stream-ID mit `screen` beginnt **oder** ihre mid im
  `screen_mid` der Bein-Anfrage steht (Browser können die Stream-ID nicht wählen).
- **Grenze je Adresse:** höchstens `HORN_MAX_PER_IP` Beine (Standard 12) je Quell-Adresse, insgesamt `HORN_MAX_LEGS`
  (Standard 48). Hinter einem gemeinsamen NAT (Büro, Schule) mit vielen Geräten höher setzen.


## Kaskade „Bifröst“ (P9) und Kontingente (P10)

Sind Teilnehmende eines Anrufs auf mehreren Knoten, verbindet Saga die Knoten sternförmig (Mittelpunkt = Knoten mit den
meisten Endpunkten, `saga/horn_bridge.py`). Die meisten Anrufe bleiben dank Bonus „Anruf schon hier“ auf einem Knoten;
eine Brücke entsteht nur, wenn wirklich zwei Knoten beteiligt sind (anderer Knoten deutlich besser, Umzug, Föderation).

**Aufbau** (`bifrost.go`): Saga schickt dem wählenden Knoten über den Steuerkanal
`cascade {bridge, url, peer, peer_pub, token, mine}`. Beide Seiten prüfen ein von Saga signiertes **Kaskaden-Token**:

```
c1.<payload>.<sig>   sig = Ed25519(saga_sk, "saga-horn-cascade/1." + payload)
payload {v:1, bridge, node, room, role: accept|dial, peer_node, peer_pub, q?, q_legs?, q_kbit?, exp (+60 s), jti}
```

`mine` (Rolle dial) prüft der wählende Knoten selbst, `token` (Rolle accept, von der Saga des anderen Knotens) der
annehmende (Signatur, eigener Name, Gegenstelle, Ablauf, einmalig je `jti`). Die Verbindung ist TLS auf 443/TCP (ALPN
`http/1.1`) zum anderen Knoten:

```
GET /api/sfu/bridge   Upgrade: saga-bifrost/1   X-Bifrost-Bridge, X-Bifrost-From, X-Bifrost-Token
X-Bifrost-Sig: B64(Ed25519(knoten_sk, "saga-bifrost/1 dial\n" + bridge + "\n" + HEX(EKM)))
→ 101, X-Bifrost-Sig: B64(Ed25519(knoten_sk, "saga-bifrost/1 accept\n" + bridge + "\n" + HEX(EKM)))
```

EKM = `ExportKeyingMaterial("saga-bifrost/1", 32)` der TLS-Verbindung: Die Knotenschlüssel (`peer_pub`, von Saga) binden die
Unterschrift an genau diese Verbindung. Ein Mittelsmann kann sie nicht weiterreichen; das Web-PKI-Zertifikat spielt
für die Brücke keine Rolle.

**Rahmen** in beide Richtungen: `[typ u8][länge u16][nutzlast]`, Typ 1 = RTP (`[sid u16][paket]`), Typ 2 = Steuerung
(JSON). Gepuffert und je Schub geleert (wie die Beine), Schreibfrist 5 s, Lesefrist 20 s.

| Steuerung | Inhalt |
|---|---|
| `peers` | Teilnehmende dieses Knotens und anderer Brücken (nie die der Gegenseite): `{p:[{id, d, sp, s:[[sid, art, schicht, bildschirm, ext, aktiv]]}]}` – bei Änderung, sonst alle 2 s (letzte Sprache `sp` in ms) |
| `want` | `{s:[sid]}` – Bild-Quellen, die die Gegenseite gerade braucht |
| `pli` | `{s: sid}` – Schlüsselbild anfordern (weiter bis zum Gerät, begrenzt wie lokal) |
| `bye` | Brücke zu (`why`) |

- **Ton:** jede Seite schickt von sich aus ihre drei lautesten (Top-3, wer spricht, bleibt). Der Empfänger wählt daraus
  wie lokal seine Top-3 und den Hauptsprecher.
- **Bild nur auf Nachfrage:** Teilnehmende der Gegenseite stehen als `Peer.Via` im Raum (`internal/sfu/remote.go`), ihre
  Quellen gelten als nutzbar, wenn der andere Knoten sie als aktiv meldet. Last-N, Sprecher und Schichtwahl planen sie wie
  eigene; was ein Ausgang wählt (`Source.Subscribed`), geht als `want` hinüber – also nur die Schichten, die wirklich
  jemand sieht. Umschalten wie lokal am nächsten Schlüsselbild.
- **Pakete bleiben unverändert** (Kopferweiterungen inklusive, Frame-E2E: die Brücke sieht nur Chiffrat). Nur die
  Folgenummer hält der Ausgang lückenlos, wenn er nach H3 verwirft (Warteschlange älter als 150 ms → Bild bis zum
  nächsten Schlüsselbild verwerfen, Ton nie). Doppel der Quelle gehen auch hier nicht hinüber, Nachzügler mit der
  Nummer ihrer Stelle (Abgleich je Quelle, siehe Weiterleiter). Beginnt der Ausgang der Gegenseite nach einer Pause
  neu, erkennt die Quelle hier die neue Folge am jüngeren Zeitstempel.
- **Kennungen:** Teilnehmende behalten ihre Kennung (`peer_ref`) ihres Knotens; die CSRC vergibt jeder Knoten selbst. Gibt
  es eine Kennung schon (Gerät beim Wechsel auf beiden Knoten), gewinnt der erste Weg.
- **Ende:** `uncascade`, Raum zu, Lesefehler, oder 30 s ohne eigene Teilnehmende und ohne weitere Brücke. Die
  Lastmeldung trägt `bridges {id: {st: up|dial|err|zu, peer, why}}`; Saga baut nicht stehende Brücken nach 8 s neu auf.

**Kontingente (P10, geteilte Knoten):** Medien-Token für Teilnehmende verbundener Server und accept-Token ihrer
Brücken tragen `q` (zufällige Kennung je Server), `q_legs` und `q_kbit`. Der Knoten zählt je `q` die Teilnehmenden (direkt
und über Brücken): mehr als `q_legs` → 429 `kontingent erschoepft`. Eingehend ein gemeinsamer Eimer mit `q_kbit` (0,5 s
tief): darüber verwirft er Bild ab dem nächsten Rahmenanfang bis zum nächsten Schlüsselbild (Ton nie, Folgenummern
lückenlos); ausgehend begrenzt er die Schätzung jedes Beins auf `q_kbit / Teilnehmende`. Notaus:
`quota_off {q, secs}` entfernt sofort alle Teilnehmenden und Brücken mit `q` und weist neue 3 min ab. Die Lastmeldung
trägt `quotas {q: {peers, legs, kbit, dropped, off}}`.

**Herkunft:** `welcome.origins` bzw. `origins {list}` nennt die Seiten verbundener Server, mit denen Saga teilt; CORS für
`/api/sfu/*`, `/probe` und den Ereigniskanal nimmt sie zusätzlich zur eigenen Saga an.

**Messung** (`gjallarhorn bench --sfu --cascade -n 12 --video 12`): zwei Knoten im selben Container, Bots je zur Hälfte,
Brücke dazwischen (Loopback). 2 Kerne, TCP, 20 s: je Knoten 5,3 % eines Kerns (einzelner Knoten mit allen 12: 7,9 %),
Brücke 1,6 Mbit/s je Richtung, Bots empfangen 4312 Pakete/s (erwartet ~4325), 0 verworfene Rahmen.
20 Bots / 12 Video / 60 s: je Knoten 7,5 % (einzelner Knoten: 11,4 %), Brücke 1,6–1,7 Mbit/s, Bots 7114 statt 7117
Pakete/s, 4 Einfrieren auf 145 000 Rahmen (einzeln 0), 0 verworfene Rahmen.

## Testen

```sh
go test -race ./...                                   # inkl. Fuzz-Startwerte, SFU mit pion-Bots, Einfrieren
go test ./internal/netx -run x -fuzz FuzzWeiche       # weitere: FuzzSTUN, FuzzProxyHeader, rtpx: FuzzParse, FuzzVP8
gjallarhorn bench -n 25 --video 12 --sfu              # Last mit dem echten Weiterleiter (ohne --sfu: Spiegel wie bisher)
gjallarhorn bench -n 12 --video 12 --sfu --cascade    # zwei Knoten mit Bruecke (Kaskade, P9)
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers e2e/sfu.sh  # 4 Chromium, ICE-TCP + TURN-TLS, Schichtwechsel bei Drossel
```

`HORN_DEV=1` (nur Tests) schaltet die Endpunkte unter `/api/dev/` frei: Entwicklungs-Token, Räume, Drossel je Bein und
Lastzahlen. Dazu kommt die Testseite `/dev/`. Auf keinen Fall produktiv setzen.
