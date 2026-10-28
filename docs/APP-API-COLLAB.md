# Gleichzeitig bearbeiten live – Protokoll `WS /api/collab` (2.5, P3 Stufe 2)

Mehrere Personen schreiben in derselben Seite und sehen Eingaben und Cursor der anderen sofort. Grundlage ist ein CRDT
im **Yjs-Binärformat** (Web: `yjs`, Server und Linux: `pycrdt`). Der Server ist je Seite der **Hub**: Er prüft jedes
Update, bevor er es verteilt, legt es dauerhaft ab und schreibt den Stand entprellt als normalen Seiteninhalt
(`note.body`) – alle bisherigen Leser (GET, Suche, Veröffentlichen, Export, Föderation v1, Apps ohne Live) bleiben
unverändert. Dieses Papier reicht, um ohne Server-Code einen Client zu schreiben.

- **Schalter:** Admin-Bereich → Einstellungen → Seiten → „Gleichzeitig bearbeiten live (Stufe 2, Test)“
  (`notes.live`, Standard **aus**). Nur wenn an, enthält `GET /api/version` → `features` den Wert **`live1`**.
- **Aus** (oder `live1` fehlt, oder der WebSocket kommt nicht zustande, z. B. Proxy ohne Upgrade): **Stufe 1** wie
  bisher (`PATCH` mit `merge: 1`, `APP-API.md` „Gleichzeitig bearbeiten“). Ein Client muss jederzeit auf Stufe 1
  zurückfallen können (siehe „Rückfall“).
- Seiten anderer Server (negative IDs) und Text-Notizen: nur Stufe 1 (`error` `federated` bzw. `format`).
  Föderation (`collab1`) folgt in einer späteren Etappe.

## 1. Verbindung

`GET /api/collab` mit WebSocket-Upgrade, über den normalen Web-Port (hinter dem Reverse Proxy muss WebSocket für
diesen Pfad durchgereicht werden; saga-media tut das). Nur **Binärrahmen**; ein Textrahmen beendet die Verbindung (4426).

**Anmeldung** wie beim Live-Kanal (SSE, `APP-API-LIVE.md`):

| Client | Anmeldung |
|---|---|
| Web | Sitzungs-Cookie `saga`. Der Kopf `Origin` muss genau `BASE_URL` sein (sonst 4400). |
| Apps | `Authorization: Bearer hkd_…` (Geräte-Schlüssel) – ohne `Origin`; oder die App-Sitzung (Cookie) nach `/app/resume` |

Höchstens **5 Verbindungen je Konto** (wie SSE, `LIVE_MAX_PER_USER`; die älteste wird mit 4429 geschlossen), höchstens
20 Seiten je Verbindung. Eine Verbindung kann mehrere Seiten abonnieren. Der Server prüft Sitzung, Gerät und Schalter
alle 25 s erneut (sonst `bye` und Schließen).

**Schließcodes**

| Code | Bedeutung | Client |
|---|---|---|
| 4403 | Live ist aus (`notes.live`) | Stufe 1, erst nach Neuladen von `/api/version` wieder versuchen |
| 4401 | nicht angemeldet / Sitzung bzw. Gerät ungültig | neu anmelden, bis dahin Stufe 1 |
| 4400 | `Origin` falsch (Web) | Fehler im Client – Stufe 1 |
| 4426 | Protokollfehler (Textrahmen, kaputter Rahmen) | Fehler im Client – Stufe 1 |
| 4429 | zu viele Verbindungen bzw. Puffer voll (langsamer Client) | nach 5–30 s (zufällig) neu verbinden |
| 1012 | Neustart des Servers | nach 1–5 s neu verbinden, Abos mit Zustandsvektor (`sub` mit `gen`) |
| 1000/1001/1006 | normal / weg | neu verbinden mit Wartezeit (1, 2, 4 … höchstens 30 s) |

## 2. Rahmenformat (bytegenau)

Jede Nachricht ist **ein** Binärrahmen:

```
+--------+----------------------------+---------------+---------------+-----
| art u8 | note_id int64 big-endian   | teil 1        | teil 2        | ...
+--------+----------------------------+---------------+---------------+-----
                                       teil = varuint(länge) ++ bytes
```

- `varuint` = lib0/Yjs-Kodierung: je Byte 7 Bit, niederwertige zuerst, höchstes Bit = „es folgt noch ein Byte“
  (300 = `ac 02`).
- `note_id` = Seiten-ID (positiv). `0` für Nachrichten der Verbindung selbst (erstes `hello`, `ping`).
- Teile sind **JSON** (UTF-8, ein Objekt) oder **Yjs-Binärdaten** – je nach Art (Tabelle). Leere/fehlende Teile sind
  erlaubt, wo „optional“ steht. Unbekannte zusätzliche Teile ignorieren.
- Rahmen höchstens **256 KB** (größer: `reject` `size` bzw. Schließen 4429).

| art | Name | Richtung | Teile |
|---|---|---|---|
| 1 | `hello` | S→C | JSON (siehe unten) |
| 2 | `sub` | C→S | JSON `{schema, caps?, gen?}`, optional Zustandsvektor (Yjs `encodeStateVector`) |
| 3 | `unsub` | C→S | – |
| 4 | `sync1` | beide | Zustandsvektor |
| 5 | `sync2` | beide | Update (Yjs `encodeStateAsUpdate(doc, sv)`) |
| 6 | `update` | beide | Update; C→S optional zweiter Teil `ref` (ASCII, ≤ 40 Zeichen) |
| 7 | `aw` | beide | C→S: JSON (eigene Awareness) bzw. `null`; S→C: JSON `{sid, state}` |
| 8 | `reject` | S→C | JSON `{code, ref}`, **Vollzustand** (Update) |
| 9 | `bye` | S→C | JSON `{reason}` |
| 10 | `error` | S→C | JSON `{code}` |
| 11 | `ping` | C→S | – (Antwort `pong`) |
| 12 | `pong` | S→C | – |
| 13 | `ack` | S→C | `ref` (wie gesendet) |

Beispiel `sub` für Seite 812 (`0x32c`) mit JSON `{"schema":{"model":"saga-pm","ver":1,"hash":"…"}}`:
`02 00 00 00 00 00 00 03 2c` ++ `varuint(len)` ++ JSON-Bytes.

## 3. Ablauf

```
C                                   S
|---- (Upgrade, Cookie/Bearer) ---->|
|<--- hello (note 0) ---------------|  {proto: 1, schema: {model, ver, hash}, max: 262144}
|---- sub (note N) {schema, caps} ->|  Rechte, Sperre, Schema prüfen
|<--- hello (note N) ---------------|  {proto, gen, role, ro, why, schema, me, name, color}
|<--- sync1 (note N) sv_S ----------|
|<--- sync2 (note N) update --------|  alles, was C fehlt (ohne sv im sub: ganzer Zustand)
|---- sync2 (note N) update ------->|  optional: was S fehlt (aus sv_S berechnet), wie ein update
|---- update (N) u, ref ----------->|  geprüft, dauerhaft abgelegt, dann verteilt
|<--- ack (N) ref ------------------|      bzw. reject (N) {code, ref} + Vollzustand
|<--- update (N) u' ----------------|  Updates der anderen (und Korrekturen des Servers)
|---- aw (N) {cursor, sel} -------->|
|<--- aw (N) {sid, state} ----------|  Awareness der anderen (state null = weg)
|<--- bye (N) {reason} -------------|  Abo beendet (Rechte, gelöscht, aus, Neustart)
```

**`sub`** – Teil 1 JSON:

| Feld | Bedeutung |
|---|---|
| `schema` | **Pflicht** `{model: "saga-pm", ver: 1, hash: "<16 hex>"}` – das Dokumentmodell, das der Client kennt (Abschnitt 5). Weicht es vom Server ab (`hello.schema`), bekommt der Client das Abo **nur lesend** (`ro: true`, `why: "schema"`): Er darf nichts senden, zeigt „Seite neu laden“ und bleibt bei Stufe 1 für eigene Änderungen. So kann ein Client mit anderem Schema keine ihm unbekannten Knoten löschen. |
| `caps` | optional, wie `X-Saga-Caps` (`inline2,cols6,merge1,sync1`) – wirkt auf synchronisierte Blöcke (`sync1`) |
| `gen` | optional: Generation aus einem früheren `hello` dieser Seite. Nur wenn sie gleich ist, wertet der Server den Zustandsvektor (Teil 2) aus und schickt in `sync2` nur die Differenz. Sonst (neue Generation = Zustand wurde neu aus dem Seiteninhalt aufgebaut) **eigenes Doc verwerfen** und den Vollzustand übernehmen. |

**`hello` der Seite:**

| Feld | Bedeutung |
|---|---|
| `gen` | Generation des Zustands (16 hex). Ändert sie sich zwischen zwei Abos, ist der alte lokale Zustand ungültig. |
| `role` | `owner` · `edit` · `comment` · `view` |
| `ro` | `true` = nur lesen (Rolle `view`/`comment`, Seite gesperrt, anderes Schema). Updates würden abgelehnt. |
| `why` | `null` · `locked` · `schema` |
| `me`, `name`, `color` | eigene Awareness-Kennung (`sid`), Anzeigename und Farbe (Palette, wie `presence`), so wie andere sie sehen |
| `update` | `true`, wenn das `hello` während des Abos kommt (Rechte/Sperre geändert): `ro`/`role` übernehmen, sonst nichts |

**Fehler beim `sub`** (`error` mit derselben `note_id`, kein Abo): `notfound` (kein Zugriff oder gelöscht – bewusst nicht
unterscheidbar), `format` (Text-Notiz), `federated` (negative ID), `limit` (mehr als 20 Seiten), `nosub` (Nachricht
für eine nicht abonnierte Seite), `kind` (unbekannte Art).

**Updates senden:** Jede lokale Transaktion als `update` (Yjs `encodeStateAsUpdate(doc, svVorher)` bzw. das Update aus
`doc.on('update')`), mit eigener `ref` (z. B. laufende Nummer). Der Server antwortet mit `ack ref` **nachdem** das
Update geprüft und dauerhaft abgelegt ist, oder mit `reject`. Unbestätigte Updates nach einem Neuaufbau einfach erneut
senden (Duplikate sind harmlos: `ack`, nichts verteilt). Eingehende `update`/`sync2` mit `Y.applyUpdate` anwenden
(Herkunft „remote“ – nicht zurücksenden, nicht ins eigene Undo).

**Lücken:** Ein Update, das an Einträgen hängt, die der Server nicht kennt (Client hat ein eigenes Update nicht
gesendet bzw. eine Bestätigung verpasst), wird mit `reject` `pending` abgelehnt. Client: Vollzustand übernehmen (s. u.)
und seine noch nicht bestätigten Änderungen neu anwenden – oder einfacher: vor dem Senden immer alle unbestätigten
Updates zusammen schicken (`Y.mergeUpdates`). Gegenrichtung: Erkennt ein Client eine Lücke (sein Doc hat nach
`applyUpdate` ausstehende Einträge, `doc.store.pendingStructs`), sendet er `sync1` mit seinem Zustandsvektor; der
Server antwortet mit `sync2`.

**`reject`** – `{code, ref}` + Vollzustand des Servers. Der Server hat das Update **nicht** übernommen und an niemanden
verteilt. Client: lokales Doc durch den Vollzustand ersetzen (neues `Y.Doc`, `applyUpdate(voll)`; Editor-Bindung neu
aufbauen, Cursor über relative Positionen wiederherstellen), eigenes Undo für die verworfene Änderung leeren, bei
`forbidden`/`signature`/`sanitize`/`type`/`size` kurz „Änderung nicht übernommen“ zeigen.

| code | Ursache |
|---|---|
| `type` | Zustand passt nicht zum Schema: unbekannter Knoten, Attribut oder Marke, Einbettung im Text, falsche Verschachtelung (z. B. Spalten in Spalten), fremde Wurzel, `meta` verändert |
| `sanitize` | die Server-Bereinigung (Positivliste Inline-HTML, `clean_blocks`) müsste etwas ändern (z. B. `javascript:`-Verweis, unerlaubte Farbe, Script-Tag) |
| `signature` | fremde/gefälschte Signatur (Schaltflächen mit Vorlage, Nextcloud-Block einer anderen Person) |
| `forbidden` | sonstige Prüfung beim Speichern (fremde Datei in Bild/Anhang, Erwähnung einer Person ohne Zugriff …) |
| `size` | Rahmen > 256 KB, Seite > 200 000 Zeichen JSON oder Zustand > 1 MB |
| `title` | Titel zu lang bzw. mit Steuerzeichen |
| `client` | das Update schreibt unter einer Yjs-`clientID`, die schon einer anderen Person gehört (oder 1, der Anfangszustand) – neue `clientID` wählen (`doc.clientID` beim Neuaufbau zufällig neu) |
| `pending` | Lücke, siehe oben |
| `readonly` | Rolle `view`/`comment`, Seite gesperrt oder anderes Schema |
| `invalid` | kaputtes Update |
| `retry` | Server kurz gestört (Datenbank) – Vollzustand übernehmen, eigene Änderung in 1–5 s erneut |

**`bye`** – `{reason}`: `access` (Zugriff entzogen, Seite in Papierkorb/gelöscht: `gone`), `auth` (Sitzung/Gerät
ungültig), `off` (Live ausgeschaltet → Stufe 1), `restart` (Neustart, neu verbinden). Danach ist das Abo beendet; der
Client verwirft das Live-Doc der Seite (bei `access`/`gone` auch die Anzeige wie heute bei `note.changed` mit `gone`).

**Rechte während des Abos:** Ändern sich Freigaben (auch über Gruppen), die Sperre der Seite oder kommt sie in den
Papierkorb, prüft der Server sofort: ohne Zugriff `bye access`, sonst ein `hello` mit `update: true` und neuem
`ro`/`role`.

**Awareness (`aw`):** Client sendet höchstens ~20×/s ein JSON-Objekt (≤ 2 KB) mit den Feldern `cursor`, `sel`
(relative Positionen, z. B. `Y.relativePositionToJSON`), `block` (fokussierter Block) und `typing`; andere Felder
verwirft der Server. `null` = „ich bin weg“ (sonst beim Abmelden automatisch). Der Server verteilt
`{sid, state: {…, name, color, me: false}}` – `name`/`color` setzt **nur der Server** (Anzeigename, nie Anmeldename);
`state: null` = Person hat die Seite verlassen. Beim `sub` bekommt man die aktuellen Zustände der anderen.
Awareness ist nicht das Yjs-Awareness-Protokoll (kein `clock`, keine `clientID`) – Clients bilden sie auf ihre
Cursor-Anzeige ab. Die bisherige Anwesenheit (`POST /api/presence`, Ereignis `presence`) bleibt daneben bestehen.

**`ping`/`pong`:** Der Server pingt nie selbst. Clients senden bei Ruhe alle 25–30 s ein `ping` (Proxys schließen sonst
ruhende Verbindungen); bleibt das `pong` 10 s aus: Verbindung als tot behandeln.

## 4. Rückfall auf Stufe 1

- `live1` fehlt, Schließcode 4403/4400/4426, mehrfach kein Aufbau (z. B. 3 Versuche) oder `bye off` → Stufe 1.
- Wechsel Live → Stufe 1 während der Bearbeitung: aktuellen Stand aus dem Live-Doc in Block-JSON umwandeln und mit
  `PATCH {body, rev, merge: 1, base: "sent"}` speichern (Basis = zuletzt bekannte `rev`, siehe `note.changed`/GET).
- Stufe 1 → Live (z. B. nach dem Einschalten): Seite mit `sub` ohne Zustandsvektor öffnen; ungespeicherte Änderungen
  vorher per `PATCH` speichern.
- Während einer Live-Sitzung schreiben andere weiter über JSON (alte Apps, iOS/Android, Föderation, KI, Galerie,
  Versionen …): Der Server übernimmt jede solche Änderung in den CRDT und verteilt sie als `update`. Clients mit Live
  speichern **nicht zusätzlich** per `PATCH` (der Server materialisiert selbst). Titel, Inhalt und Revision kommen dann
  wie gewohnt über `note.changed` (die Revision steigt mit jeder Materialisierung).
- `PATCH` von Feldern außerhalb des Inhalts (Symbol, Titelbild, Sperre, Eigenschaften …) bleibt wie bisher.

## 5. Dokumentmodell, Schema-Version 1 (`model: "saga-pm"`, `ver: 1`)

Abgestimmt mit dem ProseMirror/Tiptap-Editor (y-prosemirror bzw. `@tiptap/y-tiptap`). Der Server implementiert es in
`saga/collab_model.py`; Prüfsumme `hash` = erste 16 Hex-Zeichen von SHA-256 über die Schema-Beschreibung (Knoten,
Attribute, Marken) – Clients übernehmen den Wert für ihre Version aus `hello` eines Servers mit derselben Version bzw.
aus den gemeinsamen Testvektoren. **Jede Änderung an Knoten, Attributen oder Marken ist eine neue Version**; der Server
verwirft dann alte Zustände und baut sie neu aus dem Seiteninhalt auf (neue `gen`).

### 5.1 Wurzeln

| Name | Yjs-Typ | Inhalt |
|---|---|---|
| `prosemirror` | `Y.XmlFragment` | die Blöcke (Tiptap: `Collaboration.configure({ field: 'prosemirror' })`) |
| `title` | `Y.Text` | Seitentitel (Klartext, ohne Marken) |
| `meta` | `Y.Map` | `{model: "saga-pm", schema_ver: 1}` – schreibt nur der Anfangszustand; Clients ändern `meta` nie |

Andere Wurzeln → `reject type`.

### 5.2 Allgemeine Regeln

1. Ein Saga-Block = ein `Y.XmlElement` mit `nodeName` = Saga-Typ (`paragraph`, `header`, `list`, … – nicht
   `heading`/`bulletList`).
2. Attribute aller Blockknoten: `id` (Saga-Block-ID `[A-Za-z0-9_-]{1,40}`), `style` (= `tunes.hkstyle` `{c, bg?}`),
   `conflict` (= `tunes.hkconflict`), `extra` (unbekannte `data`-Felder `{data: {…}}`, unbekannte Tunes `{tunes: {…}}`,
   intern `absent: [feld]` für Felder, die im Block-JSON fehlen). Fehlende Attribute = nicht gesetzt (`null` wird nie
   gespeichert).
3. Attributwerte sind **JSON-Werte** (Zahl, Wahrheitswert, Zeichenkette, Objekt, Liste) – nicht als Zeichenkette
   kodiert. Zahlen sind in Yjs Gleitkommazahlen; `2` und `2.0` gelten als gleich.
4. Rich-Text-Felder sind Kinder des Elements: benachbarter Text steht in **einem** `Y.XmlText` mit Marken als
   Formatierungs-Attribute `{markenname: attrs}` (`{b: {}}`, `{color: {c: "red"}}`); Inline-Knoten sind `Y.XmlElement`
   **neben** den `Y.XmlText` (nicht als Einbettung im Text – Einbettungen → `reject type`). Überlappende Marken
   gleichen Typs schreibt y-prosemirror als `name--<8 Zeichen>`; der Server liest den Namen davor.
5. Undurchsichtige Blöcke: ein Element ohne Kinder mit **einem** Attribut `data` (ganzes `data`-Objekt des Blocks).
   Bearbeiten = Attribut `data` ersetzen (gewinnt die letzte Schreibung je Block).
6. Seitenverweise und Erwähnungen tragen **keinen Text** (Titel-Leck: jede Person mit Lesezugriff bekommt den ganzen
   Zustand). Den Titel zeigt der Client aus seiner Seitenliste (fremde Seite ohne Zugriff: „Seite ohne Zugriff“), den
   Namen aus `people` (`GET /api/notes/{id}`); beim Materialisieren setzt der Server die Texte aus Sicht des
   Eigentümers ein (bisheriger Linktext bleibt, wie heute `restore_links`). Ebenso: Nextcloud-Autoren stehen als Handle
   im Zustand, nie als Anmeldename.
7. Verboten (→ `reject type`): `columns` in `column`, `toggle` oder `synced`; `synced`/`synced_ref` in `synced`;
   Text direkt im Fragment; Kinder in undurchsichtigen Blöcken.

### 5.3 Knoten

| Knoten (`nodeName`) | Kinder | Attribute (außer `id/style/conflict/extra`) | Block-JSON |
|---|---|---|---|
| `paragraph` | Inline | – | `{text}` |
| `header` | Inline | `level` 1–4 | `{text, level}` |
| `callout` | Inline | `emoji` | `{emoji, text}` |
| `quote` | `qtext` (Inline), `qcap` (Inline) – genau diese zwei | `alignment` | `{text, caption, alignment}` |
| `code` | ein `Y.XmlText` ohne Marken | `language`, `view`, `render` | `{code, language, view?, render?}` |
| `delimiter` | – | – | `{}` |
| `toggle` | `ttitle` (Inline), dann Blöcke (ohne `columns`) | `open`, `level` 0–3 | `{title, open, level?, blocks}` |
| `columns` | `column` (2–6) | – | `{cols: [{w?, blocks}]}` |
| `column` | Blöcke (ohne `columns`) | `w` (Zahl), `extra` | Spalte |
| `synced` | Blöcke (ohne `columns`, `synced`, `synced_ref`) | `sid` | Original `{sid, blocks}` |
| `synced_ref` | – | `sid` | Kopie `{sid}` (Inhalt nicht im Zustand; Bearbeiten nur im Original) |
| `list` | `litem`+ | `listStyle` (`unordered`/`ordered`/`checklist` = `data.style`), `meta` | `{style, meta, items}` |
| `litem` | `ltext` (Inline), dann `litem`* | `id` (nur intern, deterministisch, nicht im Block-JSON), `meta` (`{checked}` …), `extra` | `{content, meta, items}` |
| `table` | `trow`+ | `withHeadings`, `headCol`, `colWidths`, `colors`, `stretched` | `{…, content: [[html]]}` |
| `trow` | `tcell`+ | – | Zeile |
| `tcell` | Inline | – | Zelle (HTML) |
| `image` | `icap` (Inline) | `file`, `withBorder`, `withBackground`, `stretched`, `width`, `align` | `{file, caption, …}` |
| `math` | – | `tex`, `render` | `{tex, render?}` |
| `database`, `tasks`, `calendar`, `widget`, `buttons`, `attaches`, `nextcloud`, `embed` | – | `data` | `data` unverändert |
| `unknown` | – | `type` (Saga-Typ), `data` | unbekannte Typen (z. B. altes `checklist`, neuere Versionen) unverändert |

Hinweis: Das Attribut `style` ist am Block immer die **Farbe** (`tunes.hkstyle`); der Listenstil heißt deshalb
`listStyle`. Listenpunkte, die im Block-JSON als Zeichenkette stehen, werden beim Materialisieren zu Objekten
`{content, meta: {}, items: []}`.

### 5.4 Marken (Formatierungs-Attribute im `Y.XmlText`) und Inline-Knoten

| Marke | Wert | Inline-HTML (Normalform) |
|---|---|---|
| `link` | `{href}` (http/https/mailto) | `<a href="…" target="_blank" rel="noopener noreferrer">` |
| `color` | `{c}` (Palette) | `<span class="hk-c-<c>">` |
| `bg` | `{c}` (Palette) | `<mark class="hk-bg-<c>">` (schließt `mark` aus) |
| `mark` | `{}` | `<mark class="cdx-marker">` (Gelb) |
| `b`, `i`, `u`, `s` | `{}` | `<b>`, `<i>`, `<u>`, `<s>` |
| `code` | `{}` | `<code class="inline-code">` |

| Inline-Knoten (`Y.XmlElement`, keine Kinder) | Attribute | Inline-HTML |
|---|---|---|
| `hardBreak` | – | `<br>` |
| `mention` | `u` (Handle `~…` bzw. Prinzipal) | `<span class="hk-mention" data-u="…">Name</span>` (Name vom Server) |
| `date` | `dt` (`YYYY-MM-DD` bzw. `…THH:MM`), `remind` (bool), `label` (Text) | `<time datetime="…" class="hk-date\|hk-remind">label</time>` |
| `math_inline` | `tex` | `<code class="hk-math">TeX</code>` |
| `page` | `id` (Seiten-ID, Zahl) | `<a href="/notes#id">Titel</a>` (Titel vom Server) |

Inline-Knoten dürfen ein Attribut `marks` tragen (Liste `[{type, attrs?}]`, z. B. Farbe um eine Erwähnung).
**Normalform beim Materialisieren:** Marken in fester Rangfolge von außen nach innen
`link > color > bg > mark > b > i > u > s > code`; gleiche Marken benachbarter Stücke werden nicht neu geöffnet. Damit
ergibt derselbe Inhalt immer dasselbe HTML (wie im Web-Adapter, gemeinsame Testvektoren). `strong`/`em` werden zu
`b`/`i`, `<mark>` ohne Klasse zu `cdx-marker`.

### 5.5 Anfangszustand

Beim ersten Live-Öffnen baut der Server den Zustand **deterministisch** aus dem gespeicherten Inhalt
(`client_id = 1`, Reihenfolge der Knoten und Attribute fest). Clients schreiben nie mit `clientID` 1. Gleicher Inhalt
ergibt dieselben Einträge (IDs, Längen); nur die Reihenfolge der Schlüssel innerhalb von JSON-Attributwerten kann in
den Bytes abweichen.

## 6. Was der Server prüft (vor dem Verteilen)

Jedes Update wird auf eine Kopie des Zustands angewendet; das Ergebnis wird in Block-JSON zurückgeführt und durchläuft
**dieselben** Prüfungen wie `PATCH /api/notes/{id}`: Schema (5.), Positivliste Inline-HTML und `clean_blocks`,
Erwähnungen (nur Personen mit Zugriff), synchronisierte Blöcke, eigene Dateien, Signaturen (Schaltflächen mit Vorlage,
Nextcloud), Größen. Ändert die Prüfung etwas, wird abgelehnt – mit einer Ausnahme: Felder, die **nur der Server**
setzt (Signatur einer neuen eigenen Vorlagen-Schaltfläche, Autor/Signatur eines eigenen Nextcloud-Blocks), ergänzt er
selbst als eigenes `update` an alle. Erst dann wird das Update dauerhaft abgelegt, bestätigt (`ack`) und verteilt.

## 7. Speicherung (für Betreiber)

- `note_crdt` (je Seite): Zustand, Modell-Kennung (`model`, `schema_ver`), Generation, Stand des letzten Abgleichs mit
  `note.body`. `note_crdt_log`: angenommene Updates seit der letzten Verdichtung (vor dem Verteilen geschrieben).
  Verdichtung nach 200 Updates bzw. 30 s Ruhe. Zustände ohne Live-Sitzung werden nach 30 Tagen verworfen.
- `note.body` wird höchstens 2 s nach der letzten Änderung (und beim letzten Abmelden bzw. Herunterfahren sofort)
  über den normalen Speicherpfad geschrieben: neue Revision, Versionen, Live-Ereignis `note.changed`, Suche.
- Nach einem Neustart holt der Server Änderungen, die schon angenommen, aber noch nicht in `note.body` stehen, sofort nach.
- Abschalten (`notes.live` aus) beendet alle Live-Verbindungen innerhalb von 25 s mit `bye off`; der Seiteninhalt ist
  dann schon materialisiert, Clients arbeiten mit Stufe 1 weiter.
