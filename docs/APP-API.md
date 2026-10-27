# App-API – Verträge für die App-Teams

Ergänzungen der HTTP-API für die Apps (iOS/iPadOS/macOS inkl. Widgets, Android, Linux). Live-Ereignisse stehen in
`APP-API-LIVE.md`, Chat in `APP-API-CHAT.md`, KI in `APP-API-KI.md`.

## Seite gestalten (2.5)

Alle Felder sind **optional**; fehlt eines, gilt das bisherige Verhalten. Ältere Server liefern sie nicht, ältere Apps
ignorieren sie. `MIN_APP` bleibt.

**Grundregel für alle Apps:** Beim Speichern einer Seite unbekannte Schlüssel in `block.data` und den Schlüssel
`block.tunes` **unverändert zurückschreiben** (auch wenn die App sie nicht darstellt). Farben nur aus der Palette
`gray, brown, orange, yellow, green, blue, purple, pink, red` (wie die Auswahl-Farben der Datenbanken), nie freie Werte.
Zahlen beim Lesen trotzdem begrenzen (fremde/alte Server).

| Feld | Werte (der Server bereinigt beim Speichern) |
|---|---|
| `columns.data.cols[i].w` | Zahl > 0, relatives Gewicht. Nur wenn **alle** Spalten eines haben: Summe 1, je Spalte mind. 0,15, auf 0,01 gerundet; sonst werden alle `w` entfernt (= gleich breit). Max. 6 Spalten (2.5, P6-0; vorher 4). Darstellung ab Tablet-/Fensterbreite, Telefone untereinander. |
| `image.data.width`, `embed.data.width` | ganze Zahl 10–100 (% der Inhaltsbreite). Bei `embed` nur mit Player (`kind: "youtube"`), Link-Karten nie. `image.data.stretched` gewinnt. Telefone: mindestens 50 %. |
| `image.data.align`, `embed.data.align` | `"left"`, `"center"` (Standard), `"right"`; sonst entfernt. |
| `block.tunes.hkstyle` | `{"c": "<palette>", "bg": true}` – `bg` fehlt = Textfarbe. Nur an `paragraph, header, list, quote, toggle, callout, checklist`; andere Typen und alle anderen `tunes` außer `hkconflict` (siehe „Gleichzeitig bearbeiten“) entfernt der Server (auch verschachtelt in Spalten und Toggles). |
| `toggle.data.level` | 0 (fehlt) = normaler Toggle, 1–3 = Toggle-Überschrift H1–H3 (Titel in Überschriftengröße). |

Beispiel:

```json
{"blocks": [
  {"type": "paragraph", "data": {"text": "Wichtig"}, "tunes": {"hkstyle": {"c": "red"}}},
  {"type": "columns", "data": {"cols": [{"w": 0.7, "blocks": []}, {"w": 0.3, "blocks": []}]}},
  {"type": "image", "data": {"file": {"url": "/files/<id>"}, "caption": "", "width": 50, "align": "left"}},
  {"type": "toggle", "data": {"title": "Kapitel", "level": 2, "open": true, "blocks": []}}
]}
```

**Seitenfelder:** `GET /api/notes/{id}` liefert `font` (`"serif"`, `"mono"` oder `null` = Standard) und `small` (bool).
`PATCH /api/notes/{id}` nimmt `font` (`"serif"`, `"mono"`, `"default"` oder `null` → Standard; andere Werte → `400`) und
`small` (bool) – wie `wide`, ab Rolle *Bearbeiten*. Darstellung: Serif `ui-serif, Georgia, "Times New Roman", serif`
(SwiftUI `.fontDesign(.serif)`, Android `FontFamily.Serif`), Mono `ui-monospace, "SF Mono", Menlo, Consolas, monospace`;
klein = 0,875 × Grundgröße; gilt für Titel, Inhalt und Eigenschaften, Code-Blöcke bleiben Mono. Föderierte Seiten laufen
über denselben Handler (Fähigkeit `db`, siehe `APP-API-FED.md`); ältere Server liefern die Felder nicht → Standard.

## Inline-Vertrag und neue Blocktypen (2.5, P6-0)

Grundlage für Inline-Farben, Personen-Erwähnungen, Mathe, Mermaid, synchronisierte Blöcke und 5–6 Spalten. Die
Werkzeuge zum **Setzen** kommen später (Web P6-1, Apps P6-8) – Apps müssen die Formen aber schon jetzt **anzeigen oder
mindestens unverändert zurückschreiben**. Gemeinsame Testvektoren: `tests/vectors/editor_inline.json`
(`sanitize`: Eingabe → gespeichertes HTML, `publish`, `markdown`).

### Inline-HTML (Positivliste des Servers)

Bisher: `b strong i em u s mark code a br time`. Neu dazu:

| Form | HTML | Regel |
|---|---|---|
| Textfarbe | `<span class="hk-c-<farbe>">…</span>` | Palette `gray brown orange yellow green blue purple pink red`; genau eine Klasse; `span` ohne erlaubte Klasse fällt weg (Text bleibt) |
| Hintergrund | `<mark class="hk-bg-<farbe>">…</mark>` | `mark.cdx-marker` bleibt = Gelb (Standard); fremde Klasse → `mark` ohne Klasse |
| beides | `<span class="hk-c-red"><mark class="hk-bg-blue">…</mark></span>` | Reihenfolge fest: `span` außen |
| Erwähnung | `<span class="hk-mention" data-u="~abcdefghijklmnop">Anna</span>` | `data-u` = Handle (`~` + 16 Zeichen, lokal) oder Prinzipal `@name@host` bzw. `@~handle@host`; sonst nur Text. Der Text ist nur Rückfall (Export, alte Apps) – angezeigt wird später der aktuelle Name (P6-5) |
| Inline-Mathe | `<code class="hk-math">TeX</code>` | enthält nur Text (keine weiteren Elemente); alte Apps zeigen Inline-Code |

Keine `style`-Attribute, keine freien Farben. Darstellung: Klassen `hk-c-*`/`hk-bg-*` mit den Farben der Block-Farbe
(hell/dunkel wie `tunes.hkstyle`). Veröffentlichen: `c-*`/`bg-*`, Erwähnung nur Text, Mathe als Code. Markdown-Export:
Farben weg, `mark` → `==Text==`, Erwähnung → `@Name`, Inline-Mathe → `$TeX$`.

### Neue Blocktypen

| Typ | `data` | Hinweise |
|---|---|---|
| `math` | `{"tex": "…", "render"?}` | TeX als Klartext (kein HTML), höchstens 4000 Zeichen. Export `$$…$$` |
| `code` (Mermaid) | `{"code", "language": "mermaid", "view"?: "code"\|"preview"\|"split", "render"?}` | ohne Unterstützung: normaler Code-Block |
| `synced` | Original `{"sid", "blocks": [...]}`, Kopie `{"sid"}` | `sid` = 16 Zeichen `[A-Za-z0-9]`, vergibt der Server (P6-6). Inhalt wie Toggle (eine Ebene, keine Spalten darin). `data.src` setzt nur der Server beim Lesen, gespeichert wird es nie |
| `render` (bei `math`/Mermaid) | `{"png"?, "svg"?: "/files/<id>", "h": "<16 hex>", "w"?, "hgt"?}` | Vorschaubild vom Web; `h` = SHA-256 des Quelltexts (16 Zeichen) – passt es nicht, ist das Bild veraltet. Dateien gehören dem Seiten-Eigentümer |

Alte Apps: unbekannte Typen als Rückfall-Karte anzeigen und **unverändert** zurückschreiben (Grundregel oben).

### Fähigkeiten und Schutz älterer Clients (`X-Saga-Caps`)

Apps melden beim Speichern (`PATCH /api/notes/{id}`), was sie verstehen: Kopf `X-Saga-Caps: inline2,cols6,merge1`
(kommagetrennt; unbekannte Werte werden ignoriert).

- `inline2` = die App erhält die Inline-Formen oben beim Bearbeiten (auch unverstandene: Tags samt Klasse behalten).
- `cols6` = die App kann bis 6 Spalten bearbeiten.
- `merge1` = die App sendet `merge: 1` (siehe „Gleichzeitig bearbeiten“; nur Hinweis, das Feld selbst entscheidet).

Ohne Kopf gilt: Browser-Sitzung (Web) = alles; App-Sitzung/Geräte-Schlüssel = nichts davon. Dann schützt der Server:
Textfelder eines Blocks (gleiche `id`, gleicher Typ), deren **reiner Text unverändert** ist, behalten ihr altes HTML,
wenn es neue Formen enthielt, die jetzt fehlen (Zwischenregel bis zum zeichengenauen Markenerhalt). Ein Spalten-Block mit
genau 4 Spalten, der vorher 5–6 hatte, bekommt die fehlenden Spalten zurück (Breiten danach gleich). Föderation:
dasselbe für Gegenstellen ohne die Fähigkeiten `inline2`/`cols6` (`APP-API-FED.md`).

## Gleichzeitig bearbeiten: blockweises Zusammenführen (2.5, P3 Stufe 1)

Statt `409` führt der Server (bei föderierten Seiten später der Eigentümer-Server, Etappe E3) zwei Fassungen einer Seite
**blockweise zusammen**. Die Merge-Logik gibt es nur im Server (`saga/merge.py`); Apps führen nur Buch. Testvektoren:
`tests/vectors/merge.json` (Basis, meins, deren → erwartet). Alte Apps ohne `merge` bekommen weiterhin `409`.

### Voraussetzung: eindeutige Block-IDs

Jeder Block – auch in Spalten, Toggles und `synced` – hat eine eindeutige `id` (`[A-Za-z0-9_-]{1,40}`). Apps vergeben
für neue Blöcke zufällige IDs (10 Zeichen) und behalten IDs beim Bearbeiten, Verschieben (auch in/aus Spalten und
Toggles) und Umwandeln. Fehlt eine ID oder ist sie doppelt, vergibt der Server beim Speichern eine neue (die erste
doppelte behält ihre). Alte Stände ohne eindeutige IDs liefert `GET /api/notes/{id}` mit **deterministisch**
ergänzten IDs (gleich für alle Clients, Kommentare/Widgets/Anker daran bleiben gültig) – Apps übernehmen sie einfach.

### Speichern (`PATCH /api/notes/{id}`)

| Feld | Bedeutung |
|---|---|
| `rev` | wie bisher: Revision, auf der der Client beruht (ohne `rev` kein Zusammenführen) |
| `merge: 1` | der Client kann eine zusammengeführte Antwort übernehmen. Ohne: bei veralteter `rev` `409` wie bisher |
| `base: "sent"` | optional: die Basis ist **die zuletzt eingereichte Fassung** zu `rev` (nicht der zurückgegebene Stand) – siehe Rebase |
| `bases: {blockId: rev}` | optional, höchstens 500: für einzelne Blöcke eine ältere Basis (eingereichte Fassung jener Revision) |

Antwort `200` (zusammengeführt, d. h. `rev` war veraltet bzw. `base`/`bases` gesetzt):

```json
{"ok": true, "rev": 12, "updated": 1793117573.4, "body": "<Inhalt wie GET>", "format": "blocks", "title": "…",
 "merged": {"from": 10, "conflicts": ["a1-k"], "restored": ["b2"], "lost": ["db7"], "title_lost": true}}
```

- `body`/`title`/`rev` = neuer Stand, so wie `GET` ihn dieser Person zeigt (verdeckte Verweise maskiert).
- `conflicts`: neue Konfliktblöcke (siehe unten). `restored`: Blöcke, die man gelöscht hatte, die aber jemand anderes
  inzwischen geändert hat (sie bleiben). `lost`: Blöcke, bei denen ein Nicht-Text-Feld beidseitig verschieden geändert
  wurde – die eigene Fassung gilt, die andere liegt in den Versionen (dezenter Hinweis). `title_lost`: Titel ebenso.
- Ohne `merged` in der Antwort wurde ganz normal gespeichert.
- `409` mit `merge: 1` nur noch, wenn die Basis nicht mehr vorliegt (älter als 24 h bzw. 30 Revisionen) – dann der
  bisherige Konfliktdialog. Ältere Server kennen `merge` nicht: `409` wie bisher, Dialog bleibt im Code.

### Regeln (Kurzform; genau: Testvektoren)

- Verschiedene Blöcke geändert/eingefügt/gelöscht → alles bleibt; neue Blöcke beider Seiten am selben Anker: die des
  Servers zuerst. Gelöscht gegen geändert → die Änderung gewinnt (`restored`). Reihenfolge: einseitig übernommen,
  beidseitig die des Servers plus eigene Verschiebungen ohne Kollision. Verschieben zwischen Listen: eigene Position.
- Gleicher Block: je Feld (`level`, `tunes.hkstyle`, `checked` …). Textfelder (`text`, `title`, `caption`, Listenpunkte,
  Tabellenzellen, `code`) zeichenweise inkl. Formatierung; Verweise, `<time>`, Erwähnungen und Inline-Mathe sind je eine
  Einheit; der Mathe-Block (`tex`) wird nie gemischt.
- **Überschneidung im Text → Konfliktblock**: die Fassung des Servers bleibt an der Stelle, die eingereichte steht als
  **neuer Block direkt darunter** mit `tunes.hkconflict = {by, name, at, of}` (`by` Handle bzw. Prinzipal, `of` = ID des
  Originals). Darstellung: gelber Rand + „Fassung von {name} – Übernehmen / Verwerfen“. Übernehmen = Original (`of`)
  löschen und `hkconflict` entfernen; Verwerfen = Konfliktblock löschen; dann normal speichern. Vorher sichert der
  Server eine Version. Alte Apps sehen einen normalen Absatz (unbekannte Tunes unverändert zurückschreiben).

### Rebase im Client (ohne eigene Merge-Logik)

1. Vor dem Senden den eingereichten Inhalt merken (`sent`).
2. Antwort mit `merged`: Ist der Editor seit dem Senden unverändert, den Inhalt der Antwort blockweise übernehmen
   (geänderte Blöcke ersetzen, Fokus im eigenen Block halten). Wurde weitergeschrieben: lokalen Stand behalten, `rev`
   übernehmen und beim nächsten Speichern `merge: 1, base: "sent"` senden – der Server nimmt dann die eingereichte
   Fassung als Basis und bringt die fremden Blöcke mit der nächsten Antwort.
3. Antwort ohne `merged`: wie bisher (`rev` übernehmen).
4. Feiner (optional): je lokal geänderten Block die `rev` merken, auf der die Änderung beruht, und als `bases` senden.
5. Fremde Änderung ohne eigenes Speichern (`note.changed` mit höherer `rev`): `GET` und blockweise übernehmen, ungespeicherte
   eigene Blöcke behalten (bzw. einfach sofort mit `merge: 1` speichern). Kein „Neu laden“-Hinweis mehr nötig.
6. Kopf `X-Saga-Caps: …,merge1` melden (siehe Inline-Vertrag).

### Anwesenheit je Block

`POST /api/presence` nimmt zusätzlich `block` (ID des fokussierten Blocks oder `null`; nur zusammen mit `editing: true`
wirksam). Senden bei Blockwechsel (höchstens etwa 1×/s; schnellere Meldungen ändern nur den Block), sonst wie bisher
alle ~20 s; ohne `block` bleibt der zuletzt gemeldete. Antwort und Ereignis `presence` liefern je Person zusätzlich
`block` und `color` (Palette, stabil je Person). Darstellung: farbiger Rand links am Block + „{name} bearbeitet“ –
nur Hinweis, keine Sperre. Ältere Clients ignorieren die Felder. Föderiert: folgt mit E3 (`presence2`).

## Widgets in Seiten (2.5, Etappe 1)

Neuer Blocktyp **`widget`** (nicht verwechseln mit den Home-Bildschirm-Widgets der Apps unter `/api/app/…`). Fünf Arten:
Uhr, Countdown, Fortschritt, Kennzahl, Zitat. Ältere Server speichern den Block unverändert (unbekannter Typ), liefern aber
keine Werte (`GET …/widgets` → 404). `MIN_APP` bleibt; Bearbeiten der Einstellungen in den Apps ist für Etappe 1 nicht
nötig (Hinweis „Im Web bearbeiten“), **Regel 0 gilt**: Widget-Blöcke unverändert zurückschreiben.

```json
{"id": "a1b2c3", "type": "widget",
 "data": {"v": 1, "kind": "metric", "size": "m", "style": "card", "accent": "blue", "title": "Offene Aufgaben",
          "cfg": {"db": 123, "view": "offen", "agg": "count", "prop": "", "fmt": "num"}}}
```

| Feld | Werte (der Server bereinigt beim Speichern, auch in Spalten und Toggles) |
|---|---|
| `kind` | `clock`, `countdown`, `progress`, `metric`, `quote`. Andere Werte bleiben **unverändert** gespeichert (neuere Version) – Clients zeigen „Widget – neuere Version nötig“. |
| `size` | `s`, `m` (Standard), `l` – Höhe/Schriftgröße. Sonst entfernt. |
| `style` | `card` (Standard), `plain`. |
| `accent` | Palette `gray … red` oder fehlt (= Akzentfarbe). |
| `title` | Klartext (ohne HTML, ohne Steuerzeichen), höchstens 80 Zeichen, optional. Nur er landet in Suche/Klartext. |
| `id` | Block-ID; der Server vergibt eine neue, wenn sie fehlt, ungültig (`[A-Za-z0-9_-]{1,40}`) oder auf der Seite doppelt ist. Die Werte kommen je `id`. |

`cfg` je Art (Unbekanntes fällt weg, falsche Typen werden zum Standard):

| kind | cfg | Rechnet |
|---|---|---|
| `clock` | `tz` (IANA, `""` = Zeitzone des Betrachters), `h24` (Standard true), `sec`, `analog` (bool) | Client |
| `countdown` | `date` `"YYYY-MM-DD"` (sonst `""`), `time` `"HH:MM"` oder `""`, `dir` `until`/`since`, `unit` `days`/`auto` (auto: unter einem Tag Std./Min.) | Client |
| `progress` | `src` `fixed`: `value` (Zahl, Standard 0), `max` (Zahl > 0, Standard 100). `src` `db`: `db` (ID), `view` (Ansicht-ID, `""` = erste), `mode` `checked` (Anteil angehakt der Eigenschaft `prop`, max = Zeilen) / `sum` (Summe von `prop` bis `goal` > 0). **fix27:** `src` `tasks` (ohne weitere Felder) | Client bei fixed, Server bei db/tasks |
| `metric` | `db`, `view`, `agg` `count/sum/avg/min/max/pct_checked`, `prop` (bei count `""`), `fmt` `num/pct/eur`, `goal` (optional, Zahl → `max`). **fix27:** `src` `tasks`: nur `agg` `open/due_today/overdue/done_week` (sonst `open`), `fmt` `num`, `goal` optional | Server |
| `quote` | `src` `fixed`: `items` (≤ 20 Klartexte je ≤ 300 Zeichen). `src` `db`: `db`, `view`, `prop` (`""` = Titel). `pick` `daily` (Tag + Block-ID, deterministisch) / `open` (zufällig je Abruf) | Server |

`db` ist immer eine Datenbank **dieses** Servers (positive ID); negative IDs speichert der Server als `null`.

**`GET /api/notes/{id}/widgets[?tz=<IANA>]`** (ab Rolle *Lesen*, wie die Seite) → `{"widgets": {"<block_id>": W, …}}` für
alle Widgets der **gespeicherten** Seite, die der Server rechnet (Fortschritt aus einer Datenbank, Kennzahl, Zitat; auch in
Spalten/Toggles; höchstens 50, weitere `too_big`). `tz` bestimmt „heute“ (Filter der Ansicht, Zitat des Tages), ungültig →
`400`. Seiten anderer Server (negative ID): siehe `APP-API-FED.md` – ohne Fähigkeit `widgets1` kommt
`{"widgets": {}, "unsupported": true}` (Clients zeigen „Widget nur auf dem Ursprungsserver“).

**`POST /api/notes/{id}/widgets/preview[?tz=…]`** `{"block": {"id"?, "type": "widget", "data": {…}}}` (ab Rolle
*Bearbeiten*) → `{"widget": W}` für einen noch nicht gespeicherten Block (Live-Vorschau beim Einrichten).

```json
W = {"kind": "metric", "value": 4, "max": 10, "text": null, "fmt": "num", "as_of": "2026-10-27T08:00:00Z", "error": null}
```

- `value`/`max`: Zahl oder `null` (`null` = „–“, z. B. Summe ohne Zahlen). Fortschritt: `value` von `max`; Kennzahl:
  `max` = `goal`. `text`: Zitat. `fmt`: Kennzahl (`pct_checked` immer `pct`), sonst `null`.
- `error`: `null` | `no_access` (Betrachter darf die Quell-Datenbank nicht sehen – dann **keine** Werte und kein Name,
  auch nicht für nicht vorhandene IDs) | `gone` (Datenbank im Papierkorb) | `bad_cfg` (unvollständig, Eigenschaft
  unbekannt) | `too_big` (mehr als 5000 Zeilen, wie `LIMIT_MAX` von `…/rows`).
- Gerechnet wird **mit den Rechten des Betrachters** an der Quelle (nicht des Seiteneigentümers); Zeilen wie
  `GET …/rows?view=<view>` (gleiche Filter-Semantik, Nicht-Eigentümer ohne archivierte Zeilen). Aggregation wie die
  Fußzeile der Datenbank-Tabelle im Web: Zahlen sind Zahlwerte und nicht leere Texte, die `Number()` versteht; Ergebnis auf
  2 Stellen; `pct_checked` ganze Prozent. Gemeinsame Testvektoren: `tests/widget_vectors.json`.
- Zwischenspeicher 60 s je (Konto, Seite, Block, Einstellungen, Zeitzone, Tag) **und Stand der Quelle** (fix27 #3: neue,
  geänderte, gelöschte, archivierte Zeilen und Änderungen an Schema/Ansichten rechnen beim nächsten Abruf sofort neu;
  `src` `tasks` wird immer frisch gerechnet); Rechte an Seite und Quelle prüft jeder Abruf neu. Apps laden die Werte beim Öffnen der Seite und beim Zurückkehren in den Vordergrund, das Web zusätzlich alle
  60 s und nach dem Speichern.

**Quelle Saga-Aufgaben (fix27 E3, `src: "tasks"`):** die Aufgaben (`/api/tasks`) der **betrachtenden** Person – auf einer
geteilten Seite sieht also jede Person ihre eigenen. Kennzahl: `open` = offen, `due_today` = offen und heute fällig,
`overdue` = offen und vor heute fällig, `done_week` = in dieser Woche erledigt (`done_at`). Fortschritt: `value` = diese
Woche erledigt, `max` = diese Woche erledigt + offen mit Fälligkeit in dieser Woche (`max` kann 0 sein → „–“ bzw. 0 %).
Woche = Montag bis Sonntag, „heute“ in `tz`. Personen anderer Server und veröffentlichte Seiten: `error` `no_access`.
Ältere Server kennen `src: "tasks"` nicht: Kennzahl → `bad_cfg`, Fortschritt wird beim Speichern zu `fixed` – Clients
schreiben den Block unverändert zurück (Regel 0) und bieten die Quelle nur an, wenn der Server sie kennt – Erkennen:
`POST …/widgets/preview` mit `{"kind": "metric", "cfg": {"src": "tasks"}}` liefert dann einen Wert statt `bad_cfg`.

**Darstellung (alle Clients):** Karte mit Titel (oder ohne Rahmen bei `plain`), Akzentfarbe aus der Palette, Größen s/m/l.
Uhr tickt lokal (Sekunde bzw. Minute), Countdown minütlich nach Kalendertagen (heute = „heute“). Fehlerzustände
freundlich: „Kein Zugriff auf die Datenquelle“, „Die Datenquelle gibt es nicht mehr“, „Widget noch nicht fertig
eingerichtet“, „Zu viele Einträge für ein Widget“. **Veröffentlichen:** statisch ohne JS (Uhr = Datum des Abrufs, Countdown
= Tagesstand, Fortschritt als Balken mit Klasse `pb-NN` in 5-%-Schritten); Werte aus einer Datenbank nur, wenn sie
demselben Konto gehört – sonst entfällt das Widget. **Markdown-Export:** eine Zeile `**<Titel oder Art>:** <Wert>`
(„(Stand <Datum>)“ bei Datenbank-Werten; ohne Zugriff `–`).

## Eintrags-Vorlagen, Startseite, neue Einträge (2.5, Fix-Runde 27.10.)

### E1 – Eintrags-Vorlagen für Bearbeitende

- **`GET /api/notes/{db}/rows`**: `templates` = `[{"id", "title", "icon"}]` (Reihenfolge wie im Web) für Rolle `owner`
  **und `edit`**; `view`/`comment` → `[]`. `schema.default_tpl` (ID einer dieser Vorlagen oder fehlt) kommt wie bisher
  für alle mit. Gilt auch mit Abfrage-Parametern (z. B. `?limit=1`, um nur Schema + Vorlagen zu holen).
- **Anlegen aus einer Vorlage:** `POST /api/notes/{vorlage}/duplicate` `{"parent_id": <db>, "title"?: "…"}` – derselbe
  Aufruf wie für den Eigentümer, ohne `via`. Erlaubt ab *Bearbeiten* an `<db>`, und nur, wenn die Vorlage genau in
  dieser Datenbank liegt. Sonst `403` (Rolle zu klein) bzw. `404` (andere Datenbank, fremde Vorlage, kein Ziel).
  `template: true` wirkt nur für den Eigentümer. Der Eintrag gehört dem Eigentümer der Datenbank, `created_by` = wer anlegt.
- **„+ Neu“** nimmt `default_tpl`, wenn es diese Vorlage in `templates` gibt – für Eigentümer und Bearbeitende gleich;
  Menü „Neu ▾“ mit „Leerer Eintrag“ + Vorlagen. „Vorlagen verwalten“, Vorlagen öffnen/ändern/löschen/umbenennen und
  `default_tpl` setzen bleiben beim Eigentümer: `GET/PATCH/DELETE /api/notes/{vorlage}` → `404` für alle anderen, ein
  `PATCH` des Schemas durch Bearbeitende lässt `default_tpl` unverändert.
- **Neuer Eintrag über eine Relations-Eigenschaft (#7):** erst `GET /api/notes/{ziel-db}/rows?limit=1`; gibt es
  `schema.default_tpl` in `templates`, dann `POST /api/notes/{default_tpl}/duplicate {"parent_id": <ziel-db>, "title": <Name>}`,
  sonst wie bisher `POST /api/notes {"parent_id", "title"}`. Danach die Relation setzen.
- **Föderation** (Fähigkeit `rowtpl`, `APP-API-FED.md`): dieselben Felder/Aufrufe mit negativen IDs (`templates[].id`,
  `default_tpl`). Fehlt `rowtpl` in `remote.caps` (älterer Server), ist `templates` leer – Clients verhalten sich wie bisher.

### E2 – Eigene Startseite

- `GET /api/account` → zusätzlich `"start_page": <Seiten-ID> | null`. `null` auch, wenn die gespeicherte Seite im
  Papierkorb, eine Vorlage oder nicht mehr zugänglich ist (der Server prüft bei jedem Abruf).
- `PATCH /api/account {"start_page": <id>}` setzt, `{"start_page": null}` setzt zurück → `{"ok": true, "start_page": …}`.
  Erlaubt: eigene und mit mir geteilte Seiten **dieses** Servers (jede Rolle, auch Datenbanken). Föderierte (negative) IDs,
  fremde Seiten ohne Zugriff → `404`; kein Integer → `400`. Geht für jedes Konto (auch LDAP/SSO); Name/E-Mail im selben
  Aufruf gelten weiter nur für lokale Konten.
- Web: `/` leitet bei gesetzter Startseite auf `/notes#<id>` um, das Dashboard liegt dann unter **`/dashboard`** (immer
  erreichbar, Links „Dashboard“ zeigen dorthin). Seitenmenü „Als Startseite festlegen“ / „Startseite zurücksetzen“.
- Apps: beim Kaltstart `start_page` öffnen, wenn gesetzt (sonst wie bisher); Seitenmenü-Eintrag zum Setzen/Zurücksetzen.
  Ältere Server liefern das Feld nicht → wie `null`, Menüeintrag ausblenden.

### E4 – Neue Einträge aus einer Vorlage

- `POST /api/notes/{vorlage}/duplicate` in eine **Datenbank** (Vorlage ist eine Eintrags-Vorlage, nicht `template: true`):
  Titel ist **leer**, außer `title` wird mitgeschickt (Schaltflächen mit Titel wie „Tagebuch {datum}“). Eigenschaften,
  Symbol, Inhalt wie bisher aus der Vorlage. Antwort zusätzlich
  `{"id", "updated", "fresh": "<Schlüssel, 24 Zeichen [A-Za-z0-9_-]>", "placeholder": "<Name der Vorlage>"}`.
  Auch für Schaltflächen mit `via` und über die Föderation. Andere Duplikate (Seiten, `template: true`) unverändert,
  ohne `fresh`.
- Clients zeigen `placeholder` als Platzhalter im leeren Titel (z. B. „Neues Buch“), nicht als Text.
- **Ohne Änderung geschlossen** (Peek/Seite verlassen, zurück, nächster Eintrag): `POST /api/notes/{id}/discard
  {"fresh": "<Schlüssel>"}` → `200 {"deleted": true}` – der Eintrag ist **endgültig** weg (nicht im Papierkorb, Live wie
  gelöscht). Der Server entscheidet:
  - nur die Person, die angelegt hat (Konto bzw. Person eines anderen Servers), mit dem Schlüssel und noch mindestens
    *Bearbeiten* – sonst `404`;
  - nur binnen **15 Minuten** nach dem Anlegen – sonst `409 {"deleted": false, "reason": "expired"}`;
  - nur unverändert: Titel, Symbol, Tags, Eigenschaften (leere Werte zählen wie fehlend), Inhalt (Block-IDs und
    Editor-Zeitstempel zählen nicht), Titelbild; keine Kommentare, Unterseiten, Freigaben, Veröffentlichung – sonst
    `409 {"deleted": false, "reason": "changed"}`. Auch Änderungen anderer Personen zählen.
  - `409`/`404`: nichts tun (der Eintrag bleibt). Clients senden `discard` höchstens einmal je Eintrag und merken sich
    `fresh` nur im Speicher der laufenden Sitzung. Ältere Server: kein `fresh` → nie `discard` senden.

## Vorlagengalerie (2.5)

Mitgelieferte Vorlagen (Seiten und Datenbanken) als Kopie im eigenen Konto anlegen. Format der Vorlagen und Prüfskript:
`docs/GALERIE.md`. Alle Endpunkte brauchen eine Sitzung mit `X-Saga: 1` (App-Schlüssel `hk_` gehen nicht → 401).

**Erkennen:** `GET /api/gallery` → 404 `{"error": "not found"}` heißt: kein Galerie-Server (älter) **oder** vom Admin
abgeschaltet („Vorlagengalerie anbieten“). Dann den Eintrag „Aus Vorlage …“ ausblenden. Abgeschaltet antworten **alle**
Galerie-Endpunkte 404 `{"error": "not found"}` (nie 403).

`GET /api/gallery?lang=de|en|es|ru|th` (ohne `lang`: Sprache des Kontos bzw. der Anfrage)
```json
{"lang": "de", "items": [{"id": "aufgaben-projekte", "title": "Aufgaben & Projekte", "desc": "…", "icon": "✅",
  "category": "work", "order": 20, "needs": [],
  "contents": [{"kind": "page", "title": "Aufgaben & Projekte", "icon": "✅", "example": false},
               {"kind": "database", "title": "Aufgaben", "icon": "☑️", "example": false}]}]}
```
- Sortiert nach `order`. `category`: `personal` · `work` · `team`. `needs`: optionale Fähigkeiten (`calendar`, `immich`,
  `nextcloud`, `ai`) – nur ein Hinweis, angelegt wird trotzdem. Unbekannte Felder ignorieren.

`GET /api/gallery/{id}?lang=` – Vorschau (404 bei unbekannter id): wie ein Listeneintrag und zusätzlich
- `preview`: `{"title", "icon", "kind", "wide", "blocks": [Editor.js-Blöcke der Startseite]}` – übersetzt; Verweise auf
  Seiten der Vorlage stehen als `{"preview_ref": "<ref>", "title": "<Titel>"}` statt einer ID, Links im Text zeigen auf
  `/notes#0`. Nur zum Anzeigen, nie speichern.
- `databases`: `[{"title", "icon", "props": [{"id", "name", "type"}], "views": [{"id", "name", "type"}], "rows": <ohne
  Beispiele>, "examples": <Beispielzeilen>, "templates": ["<Titel>"]}]`

`POST /api/gallery/{id}/use?lang=` `{"parent_id": null, "examples": true}` → `{"root": 123, "created": [123, 124, …]}`
- `examples` (Standard `true`): Beispielseiten und -zeilen mit anlegen. `root` = Startseite zum Öffnen, `created` = alle
  angelegten IDs (Seiten, Datenbanken, Eintrags-Vorlagen, Zeilen).
- `parent_id`: `null` = oberste Ebene, sonst eine Seite mit mindestens „Bearbeiten“ (wie „Neue Unterseite“; unter einer
  geteilten Seite gehört alles dem Eigentümer der Elternseite). Datenbank als Elternseite → 400. Negative ID (Seite eines
  anderen Saga-Servers) → 400 (in Etappe 1 nicht möglich). Fremd/gelöscht → 404, nur Lesen/Kommentieren → 403.
- Alles oder nichts: bei einem Fehler wird nichts angelegt (400/409 mit `error`). 409 = Seitengrenze des Kontos erreicht.
- 429 bei mehr als 10 Aufrufen je Minute und Konto (`GALLERY_RATE`).
- Angelegt wird in der Sprache `lang` (fehlende Übersetzung: Deutsch). Danach sind es normale Seiten ohne Verbindung zur Galerie.

## Kachel-Symbole: `icon_url` (2.5)

`GET /api/services` und `GET /api/bookmarks` liefern je Kachel zusätzlich **`icon_url`** – die fertige, relative
Bildadresse (PNG, Sitzung bzw. Cookie wie bisher):

- gesetztes Symbol → `/api/services/icon/<icon>` (wie bisher selbst gebaut),
- **Dienst** (`own=false`) ohne Symbol → leer (Anfangsbuchstabe); automatische Symbole gibt es nur für Lesezeichen,
- **Lesezeichen** ohne Symbol → `/api/services/favicon/<key>?h=<hash>`: der Server rät zuerst selfh.st nach dem Domainnamen und holt
  sonst das Favicon der Seite (≤ 256 px). Nur wer die Kachel sieht; `404` = kein Bild (Anfangsbuchstabe zeigen),
  `429` = Ratenlimit je Konto, `503` (`Cache-Control: no-store`) = gerade belegt bzw. CDN/Seite nicht erreichbar
  (beides: Anfangsbuchstabe zeigen, später erneut). `h` ändert sich mit dem Host der Adresse (Cache-Schlüssel).
  Auch `/api/services/icon/<icon>` antwortet `503` (no-store), wenn das CDN gerade nicht erreichbar ist (bisher `502`).
- leer → kein Bild (z. B. Server ohne Internet, `ICON_CDN=` und „Favicons laden“ aus).

Apps nehmen `icon_url`, wenn vorhanden, sonst wie bisher `icon`. Das Feld `icon` bleibt unverändert der gespeicherte
Name (beim Bearbeiten eines Lesezeichens zurückschicken, nie `icon_url`). Rückwärtskompatibel, `MIN_APP` bleibt.

### `icon_direct`: Favicon direkt vom Gerät (2.5)

Nur bei **Lesezeichen** (`own=true`) liefern `/api/services` und `/api/bookmarks` zusätzlich **`icon_direct`**: eine
Liste von höchstens 3 absoluten Adressen auf dem Host des Lesezeichens, Schema wie die Lesezeichen-Adresse (`http`/`https`),
ohne Zugangsdaten, in dieser Reihenfolge: `<origin>/apple-touch-icon.png`, `<origin>/favicon.ico` (später ggf. ein vom
Server gefundener `<link rel=icon>` zuerst). Dienste haben das Feld nicht. Leere Liste: Admin-Schalter „Favicons laden“
aus oder Adresse ohne gültigen Hostnamen (z. B. IPv6-Literal).

Reihenfolge auf dem Gerät: `icon_url` (bzw. `icon`) über den Server; bei `404`/`429`/`503`/Netzfehler der Reihe nach
`icon_direct`; sonst der Anfangsbuchstabe. Direkte Abrufe wie ein Browser-Tab, aber: Zeitlimit ≤ 5 s, höchstens 512 KB,
nur Rasterbilder (PNG, ICO, JPEG, GIF, WebP – kein SVG, kein HTML), **ohne Cookies und Anmeldedaten** und ohne
Referer, Ergebnis lokal zwischenspeichern (z. B. 7 Tage, Fehlversuch 1 h). Nichts zum Server hochladen.
Server-seitig wird ein Fehlversuch durch `5xx`/`403`/`429` (Bot-Schutz) nur 1 h gemerkt, `404` ohne Favicon 1 Tag.
Das Web erlaubt dafür in der CSP (`img-src`) genau die Origins der eigenen Lesezeichen. Rückwärtskompatibel, `MIN_APP` bleibt.

## Datenbank-Zeilen: Ansicht, Filter, Sortierung, Seiten (2.2)

`GET /api/notes/{id}/rows` (Sitzung oder Geräte-Schlüssel `hkd_…`, ab Rolle *Lesen*) und
`GET /api/app/rows/{id}` (App-Schlüssel `hk_…`, Widget) können die Ansicht einer Datenbank **auf dem Server** anwenden
und nur einen Ausschnitt liefern. **Opt-in:** ohne einen der neuen Parameter ist die Antwort Byte für Byte wie bisher
(alle Zeilen, keine neuen Felder; Ausnahme seit 2.5: Datenbanken mit Formeln/Rollups tragen deren Werte in `props` und ggf.
`fx_err`). Rückwärtskompatibel, `MIN_APP` ändert sich nicht. Unbekannte Parameter werden wie
bisher ignoriert.

### Parameter (Query-String, alle optional)

| Parameter | Bedeutung |
|---|---|
| `view` | ID einer Ansicht aus `schema.views`. Unbekannte ID → erste Ansicht (wie im Web). Übernimmt deren `filters`, `sorts` (auch das alte Einzelfeld `sort`) und Gruppierung. |
| `filters` | JSON-Liste wie `view.filters` (`[{"pid":"…","op":"…","value":"…"}]`, höchstens 50). Ersetzt die Filter der Ansicht – für eingebettete Ansichten (deren Ansichten im Block der Seite liegen, nicht in der Datenbank). `[]` = keine Filter. |
| `sorts` | JSON-Liste wie `view.sorts` (`[{"pid":"…","dir":"asc"\|"desc"}]`). Ersetzt die Sortierung der Ansicht. |
| `q` | Suchtext wie das Suchfeld der Datenbank (Name, Werte, Namen der Optionen, Titel verknüpfter Einträge), höchstens 500 Zeichen. |
| `group` | ID einer Auswahl-Spalte (seit 2.5 auch Formel/Rollup, siehe „Formeln und Rollups“): Zeilen gruppiert liefern (siehe `groups`). Leer (`group=`) = nicht gruppieren. Ohne den Parameter: Gruppierung der Ansicht (Tabelle/Liste: `group`, Board: `group_by`, sonst die erste Auswahl-Spalte wie im Web; Galerie/Kalender: keine). |
| `limit` | Höchstens so viele Zeilen (1–5000; größere Werte werden auf 5000 begrenzt). Ohne `limit`: alle ab `offset`. |
| `offset` | Ab dieser Position (0-basiert) in der fertig gefilterten, sortierten (und gruppierten) Liste. |
| `tz` | Zeitzone des Geräts (IANA, z. B. `Europe/Berlin`) für „heute / diese Woche / diesen Monat“ und das Tagesdatum von „Erstellt“/„Zuletzt bearbeitet“. Standard: UTC. **Apps sollten sie immer mitschicken**, wenn Datumsfilter vorkommen können. |
| `today` | Tagesdatum des Geräts `YYYY-MM-DD` (Standard: heute in `tz`). |

Falsche Werte (kein JSON, keine Liste, `limit=0`, unbekannte Zeitzone …) → `400`. Kann eine Ansicht nicht ausgewertet
werden, weil die gespeicherten Daten nicht passen (dort, wo das Web mit einem Fehler abbricht – z. B. Relation
„enthält“ bei Zeilen ohne Verknüpfung), kommt `400` mit Meldung statt einer abweichenden Liste. Rechte wie bisher:
kein Zugriff → `404`; Nicht-Eigentümer sehen keine archivierten Zeilen; Eintrags-Vorlagen (`templates`) bekommen
Eigentümer und – seit fix27 E1 – Personen mit *Bearbeiten* (siehe „Eintrags-Vorlagen, Startseite, neue Einträge“).

### Antwort

Wie bisher (`schema`, `templates`, `rows` mit `id`, `title`, `icon`, `props`, `updated`, `created`, `preview`, bei
`/api/notes/…/rows` zusätzlich `role`) – `rows` enthält aber nur die Zeilen des Ausschnitts, in der Reihenfolge der
Ansicht. Zusätzlich:

| Feld | Bedeutung |
|---|---|
| `total` | Alle Zeilen der Datenbank, die der Aufrufer sieht (ohne Filter) – im Web „… von **total** Einträgen“. |
| `matched` | Zeilen nach Filter und Suche – im Web „**n** von …“. |
| `offset`, `limit` | Wie angefragt (`limit` nur, wenn gesetzt; begrenzt auf 5000). |
| `groups` | Nur bei Gruppierung: `[{"id": null, "count": n}, {"id": "<option-id>", "count": n}, …]` – zuerst „Ohne …“ (`id: null`), dann je Option in Schema-Reihenfolge, **auch leere**. Das Web zeigt in Tabelle/Liste leere Gruppen nicht, im Board leere Spalten schon (nur „Ohne …“ leer nicht). `rows` steht dann in Gruppen-Reihenfolge; Seiten (`offset`/`limit`) laufen über diese Reihenfolge. |

Seiten passen lückenlos aneinander: gleiche Reihenfolge bei jeder Anfrage (Grundreihenfolge wie ohne Parameter:
Sortierfeld, Anlegezeit – bei exakt gleicher Anlegezeit zusätzlich nach ID, damit Seiten stabil sind). Zwischen zwei
Seiten geänderte Daten können sich wie bei jeder Seitenabfrage verschieben – meldet der Live-Kanal eine Änderung an
der Datenbank oder ihren Einträgen, die aktuelle Seite neu laden (wie bisher die ganze Liste).

### Genau wie im Web

Der Server bildet die Logik des Webs (`static/pages.js`) 1:1 nach, inklusive Eigenheiten: Vergleiche von Text nach
Zeichencode nach Kleinschreibung (keine sprachabhängige Sortierung, „Ä“ nach „z“), Leeres immer unten, Auswahl nach
Reihenfolge der Optionen, Mehrfachauswahl/Relation nach Anzahl, „Berechnet“ (A ÷ B …) wie im Web gerundet, Zahlen-
filter mit `Number()`-Umwandlung. Geprüft mit Testvektoren aus dem Web-Code (`tools/dbquery_vectors.mjs`,
`tests/datenbank_abfrage.py`). Die Apps können ihre eigene Logik (`DBQuery.swift`, `database.py`) damit vergleichen.

### Beispiele

```
GET /api/app/rows/812?view=offen&limit=8&tz=Europe/Berlin            Widget: erste 8 Einträge der Ansicht "offen"
GET /api/notes/812/rows?view=tab&limit=100&offset=200&tz=Europe/Berlin  dritte Seite à 100
GET /api/notes/812/rows?filters=[]&sorts=[{"pid":"title","dir":"asc"}]&q=angebot&limit=50
GET /api/notes/812/rows?limit=500&offset=0                              nur blättern, Reihenfolge wie ohne Parameter
```

Antwort (gekürzt): `{"schema": {…}, "templates": [], "rows": [{…}, …], "offset": 0, "limit": 8, "total": 2000,
"matched": 37, "groups": [{"id": null, "count": 2}, {"id": "a1b2", "count": 20}, …], "role": "owner"}`

## Datenbank als CSV exportieren (2.5, Paket 2)

`GET /api/notes/{id}/export.csv` (Sitzung oder Geräte-Schlüssel `hkd_…`, ab Rolle *Lesen*; negative IDs = Datenbank
eines anderen Saga-Servers, wenn er die Fähigkeit `csv` meldet – `remote.caps` enthält dann `"csv"`). Antwort
`text/csv; charset=utf-8` mit `Content-Disposition` (Dateiname = Titel der Datenbank), sonst JSON-Fehler wie `/rows`.
Rein additiv, `MIN_APP` ändert sich nicht.

| Parameter | Bedeutung |
|---|---|
| `all=1` | Alle Einträge (Server-Reihenfolge) und alle Spalten in Schema-Reihenfolge. Sonst gilt die Ansicht: |
| `view`, `filters`, `sorts`, `q`, `group`, `tz`, `today` | Wie bei `GET …/rows` (siehe oben): dieselbe Auswertung (`saga/dbquery.py`), Reihenfolge = `/rows`. Ohne `view`: erste Ansicht. |
| `cols` | Kommaliste von Eigenschafts-IDs: Spalten und Reihenfolge (z. B. sichtbare Spalten einer Block-Ansicht). Ohne: `view.order`, ohne `view.hidden`. |
| `sep=;` | „Excel (Deutsch)“: Semikolon und Dezimalkomma. Standard: Komma und Dezimalpunkt. |
| `safe=0` | Schutz vor Formel-Injektion aus (Standard an: Text-Zellen, die mit `= + - @` Tab oder CR beginnen, bekommen `'` davor). |

Format: UTF-8 mit BOM, CRLF, RFC 4180 (Felder mit Trennzeichen/Zeilenumbruch/Anführungszeichen in `"…"`). Erste Spalte
= Titel (Kopf: `title_name` bzw. „Name“). Werte: Datum `YYYY-MM-DD`, Zahl ohne Einheit und ohne Exponent, Häkchen
`true`/`false`, Auswahl/Mehrfachauswahl/Relation als Namen bzw. Titel mit `, ` verbunden (Relationen nur mit Titeln,
die der Aufrufer sehen darf), „Berechnet“ als Ergebnis, Erstellt/Bearbeitet `YYYY-MM-DD HH:MM` in `tz`. Künftige Typen
(Formel, Rollup, Person, Datei) erscheinen als Text. Apps: Download + Teilen-Blatt wie beim Markdown-Export.

## Import (2.5, Paket 2)

Importe laufen auf dem Server; Vorschau, Zuordnung und Fortschritt zeigt das Web (Apps: „Im Web öffnen“, später
Teilen-Ziel). Alle Endpunkte mit Sitzung oder Geräte-Schlüssel, nur eigene Uploads/Läufe (fremd → `404`).

| Endpunkt | Bedeutung |
|---|---|
| `POST /api/import/upload?name=<pfad>[&token=<t>][&replace=1]` | Rohdaten der Datei als Body (Datenstrom). Antwort `{token, kind, files, size}`; `kind` = `csv`, `md`, `notion` oder `null`. Weitere Dateien (Ordner ohne ZIP, `name` = relativer Pfad) mit demselben `token`. Grenzen: `IMPORT_MAX_MB` für alle Teile zusammen (`413`), höchstens 2000 Teile; Token gilt 30 Minuten. Höchstens **ein wartender Upload je Konto**: ein neuer Upload ohne `token` antwortet `409`, solange ein anderer wartet (noch nicht gestartet, nicht abgelaufen); mit `replace=1` wird der alte verworfen. |
| `POST /api/import/{token}/analyze` | Body `{kind?, path?, encoding?, sep?, header?}`. CSV: `{encoding, sep, header, rows, columns: [{index, name, guess, values, distinct, options, decimal, date_order, fmt}], title_col, preview (20 Zeilen), warnings, limits}`. Markdown: `{md: {pages, folders, files, unresolved, title}, warnings}`. Immer `report` (geprüfte, verworfene Einträge). Nichts wird angelegt. |
| `POST /api/import/{token}/run` | CSV neu: `{kind: "csv", columns: [{index, type: text\|number\|select\|multi_select\|date\|checkbox\|url\|skip, name?, decimal?, date_order?}], title_col, title?, parent_id?, encoding?, sep?, header?}`. CSV an bestehende Datenbank: `target_db` und `columns: [{index, target: "title"\|<eigenschafts-id>}]`. Markdown: `{kind: "md", title?, tags: bool, props: bool, parent_id?}`. → `202 {run_id}`; `409`, wenn schon ein Import des Kontos läuft; `400` bei mehr als 30 Spalten. |
| `GET /api/import/runs`, `GET /api/import/runs/{id}` | `{id, kind, name, state: queued\|running\|done\|failed\|cancelled, step: {phase, done, total}, report: {counts, groups: [{code, text, items: [{path, text}]}], more, page}, root, error}`. Abfragen alle 1–2 s. `root` = angelegte Datenbank bzw. Sammelseite, `report.page` = Seite „Importbericht“. |
| `POST /api/import/runs/{id}/cancel` | Abbruch; bereits Angelegtes wandert in den Papierkorb. |
| `DELETE /api/import/{token}` | Upload verwerfen. |

## Formeln und Rollups in Datenbanken (2.5, Paket 1 Etappe 1)

Zwei neue Eigenschaftstypen. **Der Server rechnet die Werte beim Lesen** – Apps zeigen sie nur an (kein eigener Parser,
keine eigene Rollup-Logik). Ob ein Server das kann: `GET /api/version` → `features` enthält `formula1` (Formeln),
`rollup1` (Rollups) und `dbgroup1` (Gruppieren nach Formel/Rollup). Ältere Server melden kein `features`-Feld. Bei
Seiten anderer Saga-Instanzen zusätzlich `remote.caps` mit `formula1`. `MIN_APP` bleibt `1.0.0`.

### Schema

```
{"id": "summe", "name": "Summe", "type": "formula", "expr": "if(prop(\"menge\") > 0, prop(\"preis\") * prop(\"menge\"), 0)",
 "rtype": "number", "fmt": "euro", "show": "ring"}
{"id": "pbud", "name": "Projektbudget", "type": "rollup", "rel": "<relation-pid>", "target": "<pid>|title",
 "fn": "sum", "rtype": "number", "fmt": "euro"}
```

- `rtype` (Ergebnistyp) setzt **der Server**: `number` | `text` | `checkbox` | `date` | `list`. Clients schicken ihn
  nicht (wird ignoriert). Fehlerhafte Formel (z. B. Spalte gelöscht): `rtype: "text"`, Werte kommen als Fehler (s. u.).
- `expr` enthält **IDs** (`prop("menge")`), nie Namen. `fmt`/`show` wie bei Zahlen (nur für `rtype: "number"` sinnvoll).
- Rollup `fn`: `count filled empty pct_filled unique sum avg median min max range checked unchecked pct_checked earliest
  latest` (wie die Fußzeile; gerundet auf 2 Stellen, Prozente ganzzahlig) oder `show` („Original anzeigen“, Liste von
  Texten). `earliest`/`latest` → `rtype: "date"`, `show` → `list`, sonst `number`.
- Schema wird wie immer **vollständig roh** zurückgeschrieben (unbekannte Schlüssel bleiben) – Apps dürfen Formel-/
  Rollup-Spalten weder entfernen noch ändern (Bearbeiten nur im Web: Hinweis „Formel im Web bearbeiten“).

### Werte in `props` (alle Lesestellen)

`GET …/rows` (auch `/api/app/rows/{id}`), `GET /api/notes/{zeile}` (Einzelzeile), Widgets, Export: Werte stehen fertig in
`row.props[pid]` – wie gespeicherte Werte, aber **nie gespeichert** (beim `PATCH` mitgeschickt → stillschweigend
verworfen):

| `rtype` | Wert in `props` | Anzeige |
|---|---|---|
| `number` | Zahl (ganze Zahlen ohne `.0`) | wie Zahl-Spalte (`fmt`, `show`) |
| `text` | Text (höchstens 10 000 Zeichen) | Klartext (kein HTML/Markdown; `style()`/`link()` liefern nur den Text) |
| `checkbox` | `true` / `false` | Häkchen (nur lesen) |
| `date` | `"YYYY-MM-DD"` (Tag) oder `"YYYY-MM-DDTHH:MM"` (Zeitpunkt, Ortszeit in `tz`) | Datum bzw. Datum + Uhrzeit |
| `list` | Liste von Texten | Chips oder „a, b, c“ |

Leerer Wert = Schlüssel fehlt. Laufzeitfehler je Zelle: Wert fehlt, die Zeile hat
`"fx_err": {"<pid>": "<code>"}` (nur wenn es Fehler gibt). Codes: `syntax type num len budget time arg ref date cycle`
(z. B. `num` = Division durch 0, `ref` = verwendete Spalte gelöscht, `time` = Zeitlimit der Anfrage, Wert nicht
gerechnet). Anzeige „#Fehler“, Erklärung im Tooltip/Untertitel.

**Zeitzone:** `today()`, `now()`, `formatDate` usw. rechnet der Server in `tz` (Query-Parameter von `…/rows`, wie bei
Datumsfiltern; ohne `tz`: Zeitzone des Servers). **Apps schicken `tz` immer mit** – auch ohne Filter.

**Rechte (Rollups):** Ein Rollup sieht nur verknüpfte Einträge, die die anfragende Person sehen darf (Ziel-Datenbank
nicht geteilt → kein Wert, kein Titel). Gleiches gilt für Relationstitel in Formeln.

### Nach Speichern sofort anzeigen

`PATCH /api/notes/{zeile}` mit `props` oder `title` antwortet zusätzlich mit `"calc": {"<pid>": wert, …}` (alle
Formeln/Rollups der Zeile, `null` = leer) und `"fx_err": {…}` (leer = keine Fehler) – nur, wenn die Datenbank
Formeln/Rollups hat. Werte übernehmen, kein Neuladen nötig. Datenbanken, deren Rollups auf die geänderte Datenbank
zeigen, bekommen `note.changed` (ohne `row`) – dann neu laden.

### Filter, Sortierung, Gruppen

Formel/Rollup verhalten sich wie ihr `rtype`: `number` wie Zahl (`eq gt lt`, Sortierung numerisch), `text` wie Text
(`contains not_contains is_empty not_empty`), `checkbox` wie Häkchen, `date` wie Datum (`before after eq today
this_week this_month`), `list` wie Relation (`contains` mit Text, Sortierung nach Anzahl). Auf dem Server identisch
(`dbquery`), geprüft gegen das Web.

**Gruppieren nach Formel/Rollup** (`dbgroup1`, Ergebnis `number`/`text`/`checkbox`/`date`): `view.group_by` (Board) bzw.
`view.group` (Tabelle/Liste) darf eine solche Spalte sein. Gruppen = **vorkommende Werte**, nur lesend (Karten nicht
zwischen Spalten ziehen, „+ Neu“ in einer Spalte setzt keinen Wert). `groups` in der Antwort:

```
"groups": [{"id": null, "count": 1},                                  zuerst die Leeren ("Ohne …")
           {"id": "Montag", "value": "Montag", "count": 2},           dann je Wert: id = Wert als Text, value = Wert
           {"id": "Mittwoch", "value": "Mittwoch", "count": 1}]
```

Reihenfolge: `number` aufsteigend; `checkbox` erst `false`, dann `true` (keine Leeren); `text` nach Zeichencode – **sind
alle Werte Wochentagsnamen** (`formatDate(…, "dddd")`/`"ddd"`, de/en/es/ru/th), dann Montag … Sonntag; `date` als Text.
Leere Gruppen gibt es hier nicht (nur „Ohne …“ mit `count` 0). Apps, die selbst gruppieren, nehmen genau diese Regel;
einfacher: `group` bzw. `view` an den Server geben und `groups` übernehmen.

### Formel prüfen (Editor, nur Web nötig)

`POST /api/notes/{db}/formula/check` `{"expr": "...", "pid": "<spalte>", "names": true}` (ab *Bearbeiten*) →
`{"ok", "rtype", "expr" (mit IDs), "display" (mit Namen), "error": {"pos", "msg", "code"} | null, "preview":
[{"id", "title", "value", "error"}]}` (erste 5 Einträge, Rechte der Person). `names: true` = `prop("…")` nennt Namen.

### Sprache (Notion-2.0-Teilmenge, Etappe 1)

Operatoren `+ - * / % ^ == != < <= > >= and or not && || ! ? :`, Listen `[a, b]`, Funktions- und Methodenschreibweise
(`length(x)` = `x.length()`). Logik `if ifs empty and or not`; Zahlen `abs round floor ceil min max sum mean median sqrt
cbrt pow mod sign exp ln log10 log2 toNumber pi e`; Text `concat join length lower upper trim contains startsWith
endsWith replace replaceAll substring slice split repeat padStart padEnd format style unstyle link` (keine Regex: ersetzt
wörtlich); Datum `now today date parseDate formatDate dateAdd dateSubtract dateBetween timestamp fromTimestamp year month
week day weekday hour minute` (`day` = Wochentag 1–7, `date(d)` = Tag im Monat); Listen `at first last includes sort
reverse unique flat`. Werte: Auswahl → Name, Mehrfachauswahl/Relation → Liste von Namen/Titeln, Erstellt/Bearbeitet →
Zeitpunkt. Später (Etappe 2): `map filter find some every let lets test match`, Eigenschaften verknüpfter Einträge.
Grenzen: 2000 Zeichen, 400 Bausteine, 32 Ebenen, 8 Formeln hintereinander, `^` mit |Exponent| ≤ 1000, Text 10 000
Zeichen, Listen 1000 Einträge, Schrittbudget je Zeile, 2 s je Anfrage.

Testvektoren für die Anzeige-Tests der Apps: `tests/vectors/formula.json` (Ausdruck → Wert/rtype/Fehler).

### Ältere Server und Föderation

- Server ohne `formula1`: Typen kommen nicht vor; nichts zu tun.
- Eine ältere **Gegen-Instanz** (Föderation ohne `formula1`) bekommt Formel/Rollup als nur-lesbare Text-Spalte
  (`type: "text"`, Original in `fx_orig`, Wert als Text). Speichert sie das Schema zurück, stellt der Eigentümer die
  Formel aus `fx_orig` wieder her. Apps müssen dafür nichts tun.
- Seiten anderer Instanzen (negative IDs): Werte rechnet der **Eigentümer-Server** mit den Rechten der Person; Antwort-
  format identisch (`fx_err`, `groups` mit `value`, `calc` im PATCH).

## Datenbanken: Status, relative Filter, Datum mit Uhrzeit, Unteraufgaben, Tabellenoptionen (2.5, Paket 5)

Alles **rückwärtskompatibel**: neue Felder im Schema/in den Ansichten, keine neuen Typen, keine Migration, `MIN_APP` bleibt
`1.0.0`. Ob ein Server es kann: `GET /api/version` → `features` enthält

| Fähigkeit | Bedeutung |
|---|---|
| `status1` | Auswahl mit `status: true` und `group` je Option; Ansicht `group_status`; Filter `status_group` |
| `relfilter1` | neue relative Datumsfilter (s. u.) |
| `datetime1` | Datum mit Uhrzeit (Offset) und Zeitraum `start/end`; Apps dürfen den **vollen** Wert schreiben |
| `subtasks1` | Selbst-Relationen mit Rolle (Unteraufgaben/Abhängigkeiten), `view.sub`, `depth`/`sub_of`, `blocked` |
| `tableopts1` | Tabellen-Block `headCol`/`colWidths`/`colors`; Datenbank-Tabelle `wrap_cols`/`freeze`; Kalender `range` |

**Regel 0 gilt weiter:** Clients schreiben Schema und Blöcke **vollständig roh** zurück (unbekannte Schlüssel bleiben) –
dann bleiben alle neuen Felder auch bei Apps erhalten, die sie nicht kennen. Einstellen (Status-Gruppen zuordnen,
Unteraufgaben/Abhängigkeiten aktivieren, Fixieren, Farben/Breiten) geschieht im Web; Apps müssen es nur **anzeigen**
(Mindestumfang unten).

### Status (P5-E2: Auswahl mit Gruppen)

```
{"id": "status", "name": "Status", "type": "select", "status": true, "options": [
  {"id": "todo", "name": "Offen", "color": "gray", "group": "todo"},
  {"id": "doing", "name": "In Arbeit", "color": "blue", "group": "doing"},
  {"id": "done", "name": "Erledigt", "color": "green", "group": "done"}]}
```

- Wert in `props` wie bei jeder Auswahl: die **Options-ID** (kein neuer Typ). Ohne `status1` ist es eine normale Auswahl.
- `group` ∈ `todo | doing | done`; fehlt/ungültig → der Server speichert `todo`. Nur bei `status: true`.
- Neue Datenbanken (Standard-Schema) haben `status: true` mit den drei Gruppen.
- **Anzeige:** Gruppensymbol vor dem Namen: `todo` ○, `doing` ◐, `done` ● (Web: `○ Offen`). Auswahl-Liste beim
  Bearbeiten nach Gruppen ordnen (Zu erledigen / In Arbeit / Erledigt), Werte bleiben Options-IDs.
- **„Erledigt“** = Option in Gruppe `done` (für Aufgabenquellen, „blockiert“, Fortschritt).
- **Ansicht** `"group_status": true` (Board mit `group_by` bzw. Tabelle/Liste mit `group` auf eine Status-Spalte):
  Spalten/Gruppen nach Gruppe statt je Option. `groups` in `/rows`:
  `[{"id": null, "count": n}, {"id": "todo", …}, {"id": "doing", …}, {"id": "done", …}]` (`id` = Gruppe, nicht Option).
  Karte in eine Gruppen-Spalte ziehen → Wert = **erste Option** dieser Gruppe (Schema-Reihenfolge); liegt die Karte schon
  in einer Option dieser Gruppe, nichts ändern.
- **Filter** `{"pid": "<status>", "op": "status_group", "value": "todo|doing|done|open"}` – `open` = alles außer `done`.
  Leere Zeilen passen nie; unbekannter Wert → Filter zählt nicht.

### Relative Datumsfilter (relfilter1)

Neue `op` für Datum, Erstellt/Bearbeitet und Formel/Rollup mit `rtype: "date"` (gelten auch für bedingte Farben
`view.colors`):

| `op` | `value` | Bedeutung (Tage im Kalender von `tz`, „heute“ = `today`) |
|---|---|---|
| `yesterday`, `tomorrow` | – | gestern / morgen |
| `last_week`, `next_week` | – | Mo–So der Vor-/Folgewoche (`this_week` wie bisher Mo–So) |
| `last_month`, `next_month`, `this_year` | – | Kalendermonat / -jahr |
| `overdue` | – | Ende vor heute (Zeitraum: das **Ende** zählt) |
| `next_days` | `"N"` (1–366) | heute bis heute+N−1 (**inkl. heute**) |
| `past_days` | `"N"` (1–366) | heute−N+1 bis heute |
| `on_or_before`, `on_or_after` | `"YYYY-MM-DD"`, `"today"`, `"today+N"`, `"today-N"` | Stichtag inklusive |

- Ein **Zeitraum** passt, wenn er das Fenster **berührt** (Start ≤ Fensterende und Ende ≥ Fensteranfang);
  `on_or_before` prüft den Start, `on_or_after` das Ende. Leere Werte passen nie.
- Unvollständiger `value` (leer, keine Zahl, 0, > 366, ungültiges Datum) → der Filter **zählt nicht** (alle Zeilen,
  auch leere), wie andere unvollständige Filter.
- `tz` und `today` an `…/rows` wie bisher – **immer mitschicken**. Server (`saga/dbquery.py`) und Web
  (`static/pages.js relDate`) sind identisch (Testvektoren `tests/dbquery_vectors.json`, 640 Fälle).
- Alte Apps: unbekannter `op` → Filter wirkt nicht (zeigen mehr Zeilen). Neue Apps rechnen lokal nach derselben Regel
  oder geben `view`/`filters` an den Server.

### Datum mit Uhrzeit / Zeitraum (P5-E1, datetime1)

Schema: `{"type": "date", "time": true, "range": true}` – beides nur **Vorgaben für die Eingabe** (Uhrzeit- bzw. Ende-Feld
zeigen); jeder Datumswert darf jede Form haben. Wert bleibt **ein String**:

| Form | Beispiel |
|---|---|
| Tag | `2026-10-27` |
| Zeitpunkt (Wandzeit **mit Offset**, ohne Sekunden) | `2026-10-27T14:30+01:00` |
| Zeitraum (`start/end`, Ende inklusive, Teile gemischt erlaubt) | `2026-10-27/2026-10-29`, `2026-10-27T09:00+01:00/2026-10-27T11:00+01:00` |

- **Schreiben** (`PATCH`/`POST` `props`): Server prüft streng und speichert normalisiert – Sekunden fallen weg, `Z` →
  `+00:00`, `+0100` → `+01:00`; Offset Pflicht bei Uhrzeit (sonst `400`), Uhrzeit nur für Jahre 1900–9998,
  Ende vor Beginn → `400`, `a/a` → `a`. Neue Apps schreiben die Ortszeit des Geräts mit **ihrem** Offset.
- **Alte Apps** schreiben nur `YYYY-MM-DD`: Ist der gespeicherte Wert ein Zeitpunkt/Zeitraum mit **demselben Starttag**,
  behält der Server den gespeicherten Wert (Uhrzeit/Ende gehen nicht verloren). Anderer Tag → nur der neue Tag
  (Uhrzeit weg). Neue Apps erkennen an `datetime1`, dass sie den vollen Wert schreiben dürfen.
  Uhrzeit/Ende **bewusst entfernen** (gleicher Tag): `YYYY-MM-DD/YYYY-MM-DD` schicken – der Server speichert den reinen Tag.
- **Lesen:** die ersten 10 Zeichen sind der Tag der eintragenden Person (alte Apps zeigen so den richtigen Tag bzw. den
  Start). **Anzeige** in der Zeitzone des Betrachters umrechnen (`14:30+01:00` → in New York 08:30), Zeitraum als
  „27.10.2026 14:30 – 16:00“ (gleicher Tag) bzw. „27.10.2026 – 29.10.2026“; Originalzeit bei anderer Zone im Tooltip.
- **Filter/Sortierung (Server und Web):** Kalendertag in `tz` (`23:30+02:00` am 27. ist in Bangkok der 28.); `eq` =
  Tag liegt im Zeitraum, `before` = Start vor dem Tag, `after` = Ende nach dem Tag; Sortierung nach Start (lokal
  `YYYY-MM-DDTHH:MM`, ein reiner Tag zählt als Tagesbeginn – bei gleichem Text-Beginn stabil).
- **Formeln:** Datums-Eigenschaft liefert Zeitpunkt bzw. Zeitraum; `hour`, `minute` in `tz`; `dateStart(d)`/`dateEnd(d)`
  = Beginn/Ende eines Zeitraums (sonst der Wert selbst); `dateBetween(dateEnd(p), dateStart(p), "hours")` = Dauer.
- **Kalender-Ansicht:** Uhrzeit vor dem Namen, Zeitraum auf jedem Tag (höchstens 62); Ziehen auf einen anderen Tag
  verschiebt Uhrzeit **und** Ende mit.
- CSV: ISO-String unverändert. Markdown/Veröffentlichen: lesbar (Zeitzone des Servers).

### Unteraufgaben und Abhängigkeiten (subtasks1)

Zwei gekoppelte Relationen **derselben Datenbank** (`target` = eigene ID), Kopplung stellt nur der Server her:

```
{"id": "par", "name": "Übergeordnet", "type": "relation", "target": 812, "pair": "r1a2b3c4", "role": "parent"}
{"id": "r1a2b3c4", "name": "Unteraufgaben", "type": "relation", "target": 812, "pair": "par", "role": "children"}
{"id": "blk", "name": "Blockiert durch", "type": "relation", "target": 812, "pair": "r5d6e7f8", "role": "blocked_by"}
{"id": "r5d6e7f8", "name": "Blockiert", "type": "relation", "target": 812, "pair": "blk", "role": "blocking"}
```

- Aktivieren (Web): Spalte mit `two_way: true`, `role: "parent"` bzw. `"blocked_by"`, `target` = eigene DB → Server legt
  die Gegenspalte im selben Schema an und spiegelt vorhandene Werte. Rollen gibt es nur bei Selbst-Relationen.
- Werte wie jede Relation (Liste von IDs), Gegenseite zieht der Server nach. `parent` höchstens **ein** Wert (weitere
  werden verworfen; ein neuer Elternteil löst den alten). `400` bei Selbstbezug, Kreis oder Tiefe > 10.
- **Ansicht** `"sub": {"parent": "<pid mit role parent>", "mode": "nested|flat|parents_only"}` (Tabelle/Liste, nicht mit
  Gruppierung). `nested` in `…/rows?view=…`: Kinder direkt hinter ihren Eltern (je Ebene in Ansichts-Reihenfolge),
  passt ein Kind zum Filter, bleibt die Eltern-Kette sichtbar; jede Zeile hat dann `depth` (0 = oberste Ebene) und
  `sub_of` (Eltern-ID oder `null`); Blättern läuft über diese Reihenfolge. `parents_only`: nur Zeilen ohne Elternteil.
- **„blockiert“:** Zeilen in `…/rows` und `GET /api/notes/{zeile}` tragen `"blocked": true`, wenn ein Eintrag aus
  `blocked_by` nicht erledigt ist (Status-Gruppe `done`; ohne Status-Spalte: erstes Häkchen). Nur gelesen, nie
  gespeichert. Anzeige: „⛔“ mit Hinweis.
- Apps Etappe 1: Relationen gehen schon; neu nur Einrückung in der **Liste** (`depth`) und „⛔ blockiert“; Tabelle flach
  mit Spalte „Übergeordnet“. Aktivieren nur im Web. Ohne `subtasks1`: normale (einseitige) Relation.

### „Diese Seite“ in Vorlagen (P5-E3)

Filter in **Block-Ansichten** eingebetteter Datenbanken (`data.views[].filters`) dürfen
`{"pid": "<relation>", "op": "contains", "value": "@self", "self": true}` sein. Beim Anlegen aus einer Eintrags-Vorlage,
beim Duplizieren und beim Galerie-Import (`$self`) setzt der Server `value` auf die **ID der neuen Seite**; `self: true`
bleibt. Apps tun nichts (sie sehen eine echte ID). Im Vorlagen-Body selbst bleibt `@self` (alte App filtert dort „leer“ –
korrekt, die Vorlage hat keine Einträge).

### Tabellenoptionen (tableopts1)

**Einfacher Tabellen-Block** (`type: "table"`), zusätzliche `data`-Felder (Server bereinigt):

```
{"withHeadings": true, "content": [["A","B","C"], …], "headCol": true, "colWidths": [50, 30, 20],
 "colors": {"r": {"1": "blue"}, "c": {"2": "green"}, "x": {"2,1": "red"}}}
```

- `headCol`: erste Spalte als Kopf (fett, Kopf-Hintergrund). `colWidths`: Prozent je Spalte (5–90, Summe 100, genau so
  viele wie Spalten, höchstens 20) – passt die Anzahl nicht (Spalte hinzugefügt), gleich breit anzeigen.
- `colors`: Hintergrund je Zeile `r`, Spalte `c`, Zelle `x` (`"zeile,spalte"`, 0-basiert, Zeile 0 = Kopfzeile), Farben
  aus `gray brown orange yellow green blue purple pink red` (hell getönt, Text bleibt Vordergrundfarbe). Vorrang
  **Zelle > Zeile > Spalte**.
- **Apps: Anzeige Pflicht**; Zellen bearbeiten mit Regel 0 (alle Felder außer `content` unverändert zurückschreiben).
  Markdown-Export ohne Farben/Breiten; Veröffentlichen mit Klassen (`pw-N`, `pc-<farbe>`).

**Datenbank-Ansicht:**

| Feld | Bedeutung |
|---|---|
| `wrap: true` | Zeilenumbruch in allen Zellen (sonst einzeilig, abgeschnitten) |
| `wrap_cols: ["title", "<pid>", …]` | Umbruch nur in diesen Spalten |
| `freeze: 1..3` | so viele Spalten ab links fixiert (1 = Name) – beim waagrechten Scrollen stehen sie |
| `range: "week"` | nur Kalender: Wochenansicht Mo–So (ohne Feld = Monat); Web: Umschalter Woche/Monat + „Heute“ |
| `colors` | bedingte Farbe (wie bisher) – färbt Tabellenzeilen, Listeneinträge, Kalendereinträge **und** Board-/Galerie-Karten (Fläche + Streifen oben) |

Apps: `wrap`/`freeze` anzeigen (Telefon: erste Spalte fixiert ist ohnehin sinnvoll), `range: "week"`: Woche als Spalten
(Tablet/Mac/Linux) bzw. Tagesliste (Telefon); Einstellen optional.

### Mindestumfang Apps (Paket 5)

Anzeigen: Status-Symbol + gruppierte Auswahl, Datum mit Uhrzeit/Zeitraum in Zeitzone des Betrachters, „⛔ blockiert“,
Einrückung in der Liste (`depth`), Tabellen-Block mit Breiten/Kopfspalte/Farben, DB-Tabelle `wrap`/`freeze`,
Kalender-Woche, Kartenfarben. Bearbeiten: Datum mit Uhrzeit/Zeitraum (nur mit `datetime1`), neue Filter im Filterdialog.
Fallback ohne `features`: alles wie bisher.

### Föderation

Keine neue Fähigkeit nötig: Status/Rollen/Ansichtsfelder sind Schema-Felder (alte Gegenstellen zeigen eine normale
Auswahl/Relation und schreiben das Schema roh zurück), Werte rechnet/prüft der Eigentümer-Server. Ältere Gegenstellen
werten unbekannte Filter-Operatoren nicht aus (zeigen mehr Zeilen) – harmlos. „Diese Seite“ schreibt der Eigentümer
beim Anlegen um (`rowtpl`).

## Personen und Dateien in Datenbanken (2.5, Paket 1 Etappen 2/3)

Zwei neue Eigenschaftstypen. Ob ein Server sie kann: `GET /api/version` → `features` enthält `person1` bzw. `file1`;
bei Seiten anderer Saga-Instanzen zusätzlich `remote.caps` mit `dbx1`. Ältere Server: Typen kommen nicht vor.
`MIN_APP` bleibt `1.0.0` (ältere Apps zeigen unbekannte Typen leer und schreiben das Schema roh zurück – nichts geht verloren).

### Schema

```
{"id": "wer", "name": "Zuständig", "type": "person", "multi": true, "notify": true}
{"id": "anh", "name": "Anhänge", "type": "files", "max": 10}
```

`multi: false` = höchstens eine Person; `notify: false` = keine Mitteilung bei Zuweisung (Standard: an). `max` = 1…20
Dateien je Zelle (Standard 10). Der Server setzt fehlende Felder auf die Standards.

### Werte – Personen

- In `row.props[pid]` steht eine **Liste von Kennungen**: lokale Konten als Handle `~` + 16 Zeichen (auch das eigene
  Konto), Personen anderer Instanzen als Prinzipal `@name@host`. **Nie der Anmeldename.** Die Kennung hängt nicht von der
  betrachtenden Person ab (gespeicherte Filter gelten für alle gleich).
- Dazu oben in der Antwort `"people": {"<kennung>": {"name", "avatar", "me", "remote", "gone"?}}` – bei `GET …/rows`,
  `GET /api/notes/{zeile}` und der `PATCH`-Antwort. `me: true` = die anfragende Person. `gone: true` = Konto gelöscht
  bzw. unbekannt (Anzeige „Gelöschte Person“, ausgegraut). `avatar` wie überall (`/api/avatar/…`, `null` = Initialen).
- **Setzen:** `PATCH /api/notes/{zeile}` `{"props": {"wer": ["~abc…", "@bert@b.example"]}}` (oder `null` = leeren).
  Erlaubt sind Personen **mit Zugriff auf die Datenbank** (Eigentümer, Freigaben an Personen/Gruppen, auch geerbt, und
  fremde Personen mit Freigabe) sowie man selbst; sonst `400` („Diese Person hat keinen Zugriff …“). Schon eingetragene
  Personen bleiben gültig, auch wenn ihr Zugriff endet. Höchstens 20 je Zelle; Doppelte fallen weg.
- **Auswahl:** `GET /api/notes/{db}/people?q=` (ab Rolle *Lesen*, 60/min) → `{"people": [{"ref", "name", "avatar",
  "me", "remote"}], "more"}` – höchstens 20, nur Personen mit Zugriff, Suche nach Anzeigename. Dieselbe Liste nutzen
  später @Erwähnungen und Aufgaben-Zuweisungen.
- **Filter:** `{"pid": "wer", "op": "me"}` = „ist mir zugewiesen“ (ohne Wert); `contains`/`not_contains` mit einer
  Kennung als `value`; `is_empty`/`not_empty`. **Sortierung** nach dem Namen der ersten Person (leer zuletzt).
  **Suche** (`q`) findet Anzeigenamen. **Gruppieren** (`view.group`/`group_by`, nur lesend): je Kombination von Personen,
  `groups: [{"id": null, "count"}, {"id": "<kennungen mit ,>", "value": ["<kennung>", …], "count"}]`, Reihenfolge
  nach den Namen.
- **Formeln** sehen Personen als Liste von Anzeigenamen. CSV/Markdown-Export: Anzeigenamen. Veröffentlichte Seiten
  zeigen Personen (und Dateien) **nie** – auch nicht über Formeln/Rollups.

### Werte – Dateien

- `row.props[pid]` = `[{"url": "/files/<id>", "name", "size", "mime", "image"}]` (beim Lesen ergänzt; gespeichert werden
  nur `url` und `name`). Fremde Seiten: `url` = `/api/fed/files/<zeile>/<id>`. Datei weg → fällt aus der Liste.
- **Hinzufügen:** wie im Editor hochladen – `POST /api/files?note=<zeile>` (Multipart, Feld `file` bzw. `image`; die
  Datei gehört dem Eigentümer der Datenbank und zählt bei ihm, ab Rolle *Bearbeiten*) → `file.url`; dann
  `PATCH /api/notes/{zeile}` `{"props": {"anh": [{"url", "name"}, …]}}` mit der **ganzen** Liste. Nur Dateien des
  Eigentümers der Datenbank (sonst `400`); mehr als `max` → `400`.
- Öffnen: `GET <url>` (Bilder mit `?w=` verkleinert, sonst Download). Sichtbar für alle, die die Zeile sehen.

### Nach Speichern

`PATCH /api/notes/{zeile}` antwortet bei Datenbanken mit Personen/Dateien zusätzlich mit `"values": {"<pid>": […]}`
(Personen- und Datei-Spalten so, wie der Server sie gespeichert hat – Kennungen bzw. Datei-Angaben) und `"people"`.

### Mitteilung „eingetragen“

Neue Art `db.assigned` (`GET /api/notifications`, Einstellung je Konto unter `/api/notifications/settings`, Standard an;
Push-Kategorie `assign`): Text „{name} hat dich eingetragen“ (gebündelt je Zeile, `count`), `note` = die Zeile. Nicht an
sich selbst, nicht bei `notify: false`, nur mit Zugriff; Personen anderer Instanzen über deren Server.

## Lesezeichen aus der Teilen-Erweiterung (2.5)

Nur mit App-Schlüssel `hk_…` (`Authorization: Bearer hk_…`), nie mit Sitzung. Neue Endpunkte, `MIN_APP` ändert sich nicht.

- `GET /api/app/bookmarks/categories` → `{"categories": ["Arbeit", "Lesezeichen"], "default": "Lesezeichen"}`: Namen
  der eigenen Lesezeichen-Kategorien in Dashboard-Reihenfolge, Standard-Namen in der Sprache von `Accept-Language`.
- `POST /api/app/bookmarks` mit `{name, url, category, icon}` → wie `POST /api/bookmarks` (`{id, key, name, url, icon,
  category}`; leerer Name → Host, leere Kategorie → Standard, `400` bei ungültiger Adresse bzw. ungültigem Symbol).

Ändern, Löschen und Auflisten bleiben bei `/api/bookmarks` (nur mit Sitzung, auch der Apps nach `/app/resume`).

## Persönliches Backup (2.3)

Alle Endpunkte mit Sitzung, Geräte-Schlüssel `hkd_…` oder App-Schlüssel `hk_…` und `X-Saga: 1`; sie wirken immer nur
auf das **eigene** Konto (keine Konto-Kennung in der Anfrage). Ist die Funktion vom Admin abgeschaltet, antworten alle
außer `GET` mit `403` (`GET` liefert `allowed: false`). Neue Endpunkte, `MIN_APP` ändert sich nicht.

- `GET /api/account/backup` → `{allowed, ready, running, last_ok, last_attempt, next, target: {kind, address, user,
  folder, flow, allowed} | null, keys: [{short, fp}], schedule: off|weekly|daily, effective_schedule, time, weekday
  (0 = Montag), keep, cats: {pages, templates, archive, trash, versions, files, comments, comments_others, tasks,
  bookmarks, settings}, exclude: [id], runs: [{started, finished, trigger, name, ok, size, step, error}],
  limits: {max_mb, min_h, max_schedule}, flow: {state, login, message}, age_ok}`. `?pages=1` zusätzlich
  `pages: [{id, title, icon, parent, db}]` (eigene Seiten für die Ausschluss-Auswahl). Zeiten in Unix-Sekunden, Texte
  (`step`, `error`, `message`) in der Sprache der Anfrage. **Für Apps: Status anzeigen = diese Antwort.**
- `POST /api/account/backup/run` → `202 {started}` (läuft im Hintergrund; Fortschritt über `GET`). `409` ohne
  Ziel/Schlüssel oder wenn schon eines läuft, `429 {error, retry_at}` bei Unterschreiten des Mindestabstands.
- `POST /api/account/backup/connect` `{url, user?, password?, folder?}` → `{ok, kind, free}` oder bei Nextcloud ohne
  Benutzer `{flow: <login-url>}` (im Browser öffnen; `GET` zeigt `flow.state` wait/ok/err). `POST …/flow/cancel`,
  `POST …/test` → `{ok, free}`, `DELETE /api/account/backup/target` → `{ok, revoked}`.
- `POST /api/account/backup/key/generate` → `{private, public, filename}` (privater Schlüssel nur in dieser Antwort),
  `POST …/key/confirm` `{last6}` bzw. `{drop: true}`, `POST /api/account/backup/key` `{key}`,
  `DELETE /api/account/backup/key/{fp}`.
- `PUT /api/account/backup/settings` `{schedule?, time?, weekday?, keep?, cats?, exclude?}` → wie `GET`.
- `GET /api/account/backup/remote` → `{items: [{name, time, size, complete}]}`;
  `GET /api/account/backup/remote/{name}` → die verschlüsselte Datei (durchgereicht).
- Einspielen in zwei Schritten: `POST /api/account/backup/restore/upload` (Rohdaten, `Content-Type:
  application/octet-stream`, `.zip.age` oder `.zip`) → `{token, size, encrypted}`; dann `POST /api/account/backup/restore`
  `{token | remote: <name>, key?, folder = true, trash = false, versions = true, tasks = false, bookmarks = false}` →
  `{pages, files, versions, tasks, bookmarks, skipped, folder}`. Ein Token gilt 30 Minuten und nur für das Konto, das
  hochgeladen hat; nach dem Einspielen ist er verbraucht (Datei gelöscht) – auch nach einem Fehler. Ausnahme: falscher
  oder fehlender Schlüssel bei einem verschlüsselten Upload, dann bleibt er für einen neuen Versuch gültig.
- Benachrichtigung bei Fehlern: Art `userbackup.failed` (Glocke, `url` = `/?settings=backup`).

## Nextcloud-Dateien in Seiten (2.3)

Neuer Blocktyp `nextcloud` im Seiteninhalt (Editor.js-Format). Apps, die ihn (noch) nicht darstellen, zeigen ihn als
Karte und **schreiben ihn beim Speichern unverändert zurück** (wie jeden unbekannten Block). `MIN_APP` ändert sich nicht.

```json
{"id": "x1", "type": "nextcloud", "data": {"inst": "https://cloud.example.org", "fileid": "4711", "kind": "file",
 "name": "Bericht.pdf", "mime": "application/pdf", "size": 52311, "mtime": 1792400000, "author": "anna",
 "public": false, "fed": false, "sig": "…"}}
```

- `kind`: `file` | `image` | `folder`. `name`, `mime`, `size`, `mtime`: Stand beim Einfügen (nur Anzeige; die aktuellen
  Werte liefert der Status). Keine Pfade – `fileid` ist der Schlüssel.
- `author` (Konto, das den Block eingefügt hat) und `sig` setzt **nur der Server**. Beim Speichern prüft er: bestehende
  Blöcke anderer bleiben unverändert (der Server setzt die gespeicherten Daten wieder ein, auch wenn `sig` fehlt), neue
  Blöcke müssen aus der **eigenen** verknüpften Nextcloud kommen (sonst `403`/`400`, ohne Verknüpfung `409`).
  `public`/`fed` darf nur der Autor ändern. Auf Seiten anderer Instanzen steht in `author` ein Platzhalter (`~…`).
- Status aller Blöcke einer Seite: `GET /api/notes/{id}/nc` → `{enabled, items: [{fileid, author, ok, name?, size?,
  mtime?, mime?, dir?, preview?, entries?: [{name, dir, size}]}]}` (`ok: false` = Karte ohne Inhalt). Ab Rolle „Lesen“.
- Vorschau/Datei: `GET /api/notes/{id}/nc/{fileid}?a={author}&w={breite}` (Bild, `w` ≤ 2000) bzw. `…&dl=1` (Datei als
  Download). Sitzung reicht (für `<img>`), `X-Saga` ist nicht nötig; ohne Leserecht oder ohne Inhalt `404`. Negative
  Seiten-IDs (andere Instanz) gehen über den eigenen Server, nur für Blöcke mit `fed: true`.
- Verknüpfen: `GET /api/settings/nextcloud` → `{enabled, connected, me, server, user, since, used, suggest, flow: {state,
  login, message}}`; `POST /api/settings/nextcloud` `{url}` → `{flow: <login-url>}` (im Browser öffnen, dann `GET`
  abfragen); `POST /api/settings/nextcloud/cancel`; `DELETE /api/settings/nextcloud` → `{ok, revoked}`.
- Auswahl (immer das eigene Konto, `X-Saga`): `GET /api/nextcloud/list?path=/Ordner`, `GET /api/nextcloud/search?q=`
  (leer = zuletzt geändert) → `{items: [{fileid, name, path, dir, mime, size, mtime, preview, kind, inst}]}`;
  `GET /api/nextcloud/thumb/{fileid}?w=`; `POST /api/nextcloud/resolve` `{fileid}` oder `{url}` → `{match, block, item}`
  (Daten für einen neuen Block, ohne `author`/`sig`); `POST /api/nextcloud/import` `{fileid, note?}` → wie `POST
  /api/files` („Als Kopie übernehmen“).

## Anrufe: saga-call v3 (2.4, Etappe E0)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Web (`static/calls.js`). Signatur und Versiegelung bleiben
**unverändert** (`saga-call/v1` lokal, `saga-call/v2` föderiert, Testvektoren wie bisher). Neu sind nur Felder und Typen
im signierten JSON-Text `body`. Endpunkte wie 2.1/2.3 (`POST /api/calls`, `/answer`, `/signal`, `/end`, `/alive`);
`MIN_APP` ändert sich nicht. Ein 1:1-Anruf mit einer 2.1/2.3-Gegenstelle läuft genau wie bisher.

### Server (`GET /api/calls/config`)

Zusätzliche Felder (ältere Server liefern sie nicht – dann gilt: keine v3-Funktionen):

```json
{"enabled": true, "format": "saga-call/v1", "body_v": 3, "features": ["reneg", "pip_hint", "screen2", "multi", "group"],
 "limits": {"endpoints_video": 4, "endpoints_audio": 6, "signals_max": 600, "signals_per_min": 60,
            "signals_hard": 6000, "sig_rate": 240, "sealed_max": 24576, "caps_max": 16, "ring_silent": 8, "handover_s": 15}, "…": "…"}
```

- `features`: `reneg` = der Server reicht offer/answer während des Gesprächs weiter (Budget unten), `pip_hint` = `want`
  wird unterstützt, `screen2` = Bildschirm als zweite Videospur (E3, Abschnitt „Bildschirm v2“), `multi` = weitere Geräte
  im Anruf (E4), `group` = Gruppenanrufe (E5) – beides im Abschnitt „Anrufe: Mehrgeräte und Gruppen“. 2.5: `horn`
  (nur bei eingeschaltetem Feature-Flag `calls.horn`, dazu `"e2e": "saga-horn/v1"`) = Gjallarhorn mit Frame-E2E,
  Vertrag `docs/HORN-E2E.md`; Geräte melden dann `horn` in `caps`.
- `limits.endpoints_*`: Geräte je Anruf (Mesh: 4 mit Video, 6 nur Ton; eigene Zusatzgeräte zählen mit; der Admin kann
  nur senken). E0-Server meldeten 2.
- Signal-Budget je Anruf: `signals_max` + `signals_per_min` je **angefangener** Minute seit der Annahme, höchstens
  `signals_hard`; darüber und über `sig_rate` je Gerät und Minute → `429`. Ein versiegeltes Signal darf `sealed_max`
  Zeichen (B64) haben.
- `POST /api/calls` und `POST /api/calls/{id}/answer` nehmen optional `caps: ["reneg","want","bye"]` (Liste kurzer Namen
  `[a-z0-9_]{1,16}`, höchstens 16; Ungültiges fällt weg). Das ist **nur ein Hinweis** für Oberfläche und Server;
  maßgeblich sind die signierten `caps` im Body. Anruf-Objekte (`POST /api/calls`, `GET /api/calls/{id}`,
  `/api/calls/active`) tragen `caps: {"caller": [...], "callee": [...]}`. Lokale Anrufe tragen weiterhin **kein** `v`
  (`v: 2` heißt wie bisher föderiert).

### Fähigkeiten (`caps`) und wann was gesendet wird

| Fähigkeit | Bedeutung | melden, wenn |
|---|---|---|
| `reneg` | versteht `offer`/`answer` während des Gesprächs (Neuverhandlung, ICE-Restart) | der **eigene** Server `features` mit `reneg` liefert |
| `want` | beachtet `state.want` des Gegenübers (Bitrate senken) | immer (2.4-Client) |
| `bye` | versteht `bye` | immer (2.4-Client) |
| `screen2` | sendet und empfängt den Bildschirm als eigene Videospur (Betriebsart B) | der eigene Server `features` mit `reneg` **und** `screen2` liefert und der Client B kann |
| `multi` | Raum: mehrere PeerConnections je Anruf, `join`/`leave`, Teilnehmerliste (E4/E5) | der eigene Server `features` mit `reneg` **und** `multi` liefert und der Client den Raum kann |

- `caps` steht im Body von **jedem** `offer` und `answer` (auch im ersten). Fehlt es (2.1/2.3), gilt `[]`.
- Neuverhandlung und ICE-Restart nur, wenn **beide** Seiten `reneg` melden (eigene caps und die des Gegenübers).
- `state` mit `pip`/`want`/`path` wird **immer** gesendet, auch ohne `reneg` (ältere Clients ignorieren die Felder).
- `bye` nur an eine Gegenstelle mit `bye`. Unbekannte `t`-Typen und unbekannte Felder **ignorieren** (nicht abbrechen).

### Felder im Body

| Feld | Typ | in | Bedeutung |
|---|---|---|---|
| `t` | Text | alle | `offer` · `answer` · `ice` · `state` · `bye`; 2.5: `fkey` · `fkey-ack` (nur mit `horn`, `docs/HORN-E2E.md` §4) |
| `pc` | Text | alle | Kennung der PeerConnection. **Der Anrufer legt sie fest** (je Zielgerät `min(dev_a, dev_b) + ":" + max(dev_a, dev_b)`, device_ids als Hex), der Angerufene **übernimmt** sie aus dem ersten Angebot. Signale mit fremdem `pc` ignorieren; fehlt `pc` (2.3), gilt die einzige Verbindung |
| `n` | Zahl | offer, answer | laufende Nummer **je Absender** (erstes Angebot des Anrufers `1`, das erste eigene Angebot des Angerufenen ebenfalls `1`). Die Antwort trägt das `n` des Angebots, das sie beantwortet |
| `caps` | Liste | offer, answer | siehe oben |
| `sdp`, `fp`, `media` | | offer, answer | wie bisher; `fp` = sortierte `a=fingerprint`-Zeilen der signierten SDP |
| `restart` | bool | offer | optional, nur Hinweis: Angebot ist ein ICE-Restart |
| `c` | Liste | ice | `RTCIceCandidateInit` (`candidate`, `sdpMid`, `sdpMLineIndex`, **`usernameFragment`**) |
| `mic`, `cam`, `screen` | bool | state | wie bisher |
| `pip` | bool | state | eigenes Fenster im Bild-im-Bild |
| `want` | Objekt | state | `{"video": "large" \| "small" \| "off"}` – wie groß ich das Bild des Gegenübers zeige |
| `path` | Text | state | eigener Weg: `direct` · `relay-udp` · `relay-tcp` (fehlt = unbekannt) |
| `reason` | Text | bye | z. B. `hangup` |

### Abläufe

**Anrufen/Annehmen** wie bisher, zusätzlich `pc`, `n: 1`, `caps` im Body und `caps` im JSON der Anfrage:

```json
{"t":"offer","pc":"2f67…568c:65fb…5b8f","n":1,"caps":["reneg","want","bye"],"media":"video","sdp":"v=0…","fp":["sha-256 4A:AD:…"]}
{"t":"answer","pc":"2f67…568c:65fb…5b8f","n":1,"caps":["reneg","want","bye"],"media":"video","sdp":"v=0…","fp":["sha-256 1C:…"]}
```

**Neuverhandlung („Perfect Negotiation“)**, nur wenn beide `reneg` melden. Höflich (*polite*) ist die Seite mit der
lexikographisch **größeren** device_id.

1. `negotiationneeded` (oder ICE-Restart) → eigenes `n` erhöhen, `offer` mit neuem `n` über `POST /api/calls/{id}/signal`.
2. Eingehendes `offer`: ist `n` ≤ dem zuletzt verarbeiteten `n` dieses Absenders → ignorieren. Kollision = ich erstelle
   gerade ein Angebot oder `signalingState != stable`. Unhöflich + Kollision → Angebot ignorieren. Sonst
   `setRemoteDescription(offer)` (höflich: implizites Rollback), `answer` mit **demselben** `n` senden.
3. Eingehendes `answer`: nur in `have-local-offer` und nur, wenn `n` = mein letztes Angebot; sonst ignorieren.
4. Der DTLS-Fingerabdruck (`fp`) eines neuen offer/answer muss dem des ersten entsprechen – sonst Sicherheitsfehler
   und auflegen (wie bei falscher Signatur).

**ICE-Restart** (Netzwechsel WLAN↔LTE, Verbindung weg), nur mit `reneg` auf beiden Seiten:

- Auslöser: `iceConnectionState` `disconnected` länger als **2,5 s**, sofort bei `failed`, Netzwechsel (iOS
  `NWPathMonitor`, Android `ConnectivityManager.NetworkCallback`, Web `online`/`navigator.connection` `change`).
- `restartIce()` bzw. `createOffer(iceRestart: true)` → `offer` wie oben (optional `restart: true`).
- Höchstens **3 Versuche** je Abbruch (zurückgesetzt, sobald wieder `connected`). Kommt die Verbindung nach `failed`
  nicht innerhalb von **20 s** wieder, Anruf wie bisher mit `end {reason: "failed"}` beenden.
- Ohne `reneg` beim Gegenüber: Verhalten wie 2.3 (bei `failed` auflegen).
- Kandidaten mit einem `usernameFragment`, das nicht zur aktuell gesetzten Remote-SDP (`a=ice-ufrag`) passt, **zurückhalten**,
  bis die passende SDP gesetzt ist; Kandidaten älterer Generationen verwerfen. Neue Kandidaten kommen oft vor der Antwort.

**Bild-im-Bild / Tab im Hintergrund:** Beim Wechsel ins PiP (bzw. App/Tab im Hintergrund mit sichtbarem Video)
`state` mit `pip: true, want: {video: "small"}` senden, beim Verlassen `want: {video: "large"}`. Empfängt ein Gerät
`want.video = "small"`, senkt es seinen **Kamera**-Sender nach der Tabelle unten; `off` = Kamera-Encoding
`active: false`. Bildschirmübertragung bleibt unverändert.

**Auflegen:** an eine Gegenstelle mit `bye` zuerst `{"t":"bye","reason":"hangup"}`, dann wie bisher `POST /end`.

### Bitrate je Weg (`RTCRtpSender.setParameters`)

Weg aus `getStats` alle 2 s: lokaler Kandidat `relay` mit `relayProtocol` `tcp`/`tls` → `relay-tcp`, `relay` sonst →
`relay-udp`; lokaler Nicht-Relay-Kandidat mit `protocol: tcp` (ICE-TCP) → wie `relay-tcp`; entfernter Kandidat `relay`
→ mindestens `relay-udp`; sonst `direct`. Es gilt der **schlechtere** Weg von eigenem und gemeldetem (`state.path`).

| Weg | Kamera | Bildschirm | Mesh je Strom (n ≥ 3, ab E5) | Audio |
|---|---|---|---|---|
| `direct` | `maxBitrate` 1 500 000, ≤ 720p | 2 500 000, `maxFramerate` 15 | 600 000, ≤ 540p | 32 000 |
| `relay-udp` | 1 200 000, ≤ 720p | 1 800 000, 15 fps | 500 000, ≤ 540p | 32 000 |
| `relay-tcp` | 800 000, ≤ 540p | 1 200 000, 8 fps, `degradationPreference: "maintain-resolution"` | 350 000, ≤ 360p, 15 fps | 24 000 |
| `want: small` | `min(…, 300 000)`, `scaleResolutionDownBy` = `max(2, min(3, Höhe/240))` | unverändert | 200 000 | – |

„≤ 720p“ heißt `scaleResolutionDownBy = Quellhöhe / 720` (nur wenn größer, sonst 1). Bildschirm immer
`degradationPreference: "maintain-resolution"`. Opus-FEC (`useinbandfec=1`) bleibt an. Parameter nur bei Änderung setzen.

### Beispiele

```json
{"t":"ice","pc":"2f67…:65fb…","c":[{"candidate":"candidate:1 1 udp 2122260223 192.0.2.10 54321 typ host","sdpMid":"0","sdpMLineIndex":0,"usernameFragment":"oNrl"}]}
{"t":"offer","pc":"2f67…:65fb…","n":2,"restart":true,"caps":["reneg","want","bye"],"media":"video","sdp":"…a=ice-ufrag:Zx9q…","fp":["sha-256 4A:AD:…"]}
{"t":"answer","pc":"2f67…:65fb…","n":2,"caps":["reneg","want","bye"],"media":"video","sdp":"…","fp":["sha-256 1C:…"]}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":false,"pip":true,"want":{"video":"small"},"path":"relay-tcp"}
{"t":"bye","pc":"2f67…:65fb…","reason":"hangup"}
```

Tests: `tests/anrufe_v3.py` (Server, Signalfluss, Grenzen), `tests/browser_calls3.js` (Web, gegen den 2.3-Client),
föderiert `tests/foederation_chat.py`.

## Anrufe: Bildschirm v2 (2.4, Etappe E3)

Baut auf saga-call v3 auf (Neuverhandlung oben). Server: nur `features: "screen2"`, keine neuen Endpunkte; der Body
bleibt signiert und für den Server undurchsichtig. Ein Angebot mit drei m-Zeilen (Ton, Kamera, Bildschirm) ist in Chrome
≈ 6–10 KB SDP und passt in `sealed_max` (24 KB). `MIN_APP` ändert sich nicht.

### Zwei Betriebsarten

| | wann | wie |
|---|---|---|
| **B** – eigene Spur | **beide** Seiten melden `reneg` **und** `screen2` in den signierten `caps`, und die erste Aushandlung ist fertig | zusätzlicher Video-Transceiver (bzw. Wiederverwendung, siehe unten) über die Neuverhandlung; die Kamera läuft auf ihrer m-Zeile weiter (gedrosselt) |
| **A** – wie 2.1/2.3 | sonst (2.1/2.3, E0-Clients ohne `screen2`, noch nicht verbunden) | `replaceTrack(screen)` auf dem Kamera-Sender, Kamera pausiert. Kein Angebot, keine neue m-Zeile |

Ein Client, der B nicht kann, meldet `screen2` nicht – er bekommt dann immer A. Empfangen muss **jeder** 2.4-Client mit
`screen2` beides können.

### Sender (Betriebsart B)

1. Bildschirm-Spur mit `contentHint = "detail"` (iOS/Android: entsprechend „Text“/Detail-Encoder-Einstellung).
2. **m-Zeile finden oder anlegen:** gibt es schon einen Video-Transceiver mit Rolle `screen` – eigener aus einem früheren
   Teilen **oder** einer, den das Gegenüber angelegt hat (seine `tracks` nennen diese mid `screen`) – diesen
   wiederverwenden: `sender.replaceTrack(screen)`, `sender.setStreams(bildschirm-stream)`, `direction = "sendrecv"`.
   Sonst `addTransceiver(screen, {direction: "sendrecv", streams: [bildschirm-stream]})`. Es gibt höchstens **eine**
   Bildschirm-m-Zeile je Verbindung (beide Seiten teilen sie, auch gleichzeitig) – das SDP wächst nicht bei jedem Teilen.
3. `negotiationneeded` → `offer` wie bei saga-call v3 (`n` + 1) mit `tracks`/`msid` (unten).
4. **Nach** dem Angebot `state` mit `screen: true`, `screen_mid`, `tracks`, `msid` senden (die mid steht erst nach
   `setLocalDescription` fest). Ergebnis: höchstens 4 Signale je Start (offer, answer, state).
5. **Ende** (Knopf, System-„Teilen beenden“, Spur `ended`): `replaceTrack(null)`, `direction = "recvonly"` (nicht
   `stopped`, nicht entfernen) → Neuverhandlung → teilt das Gegenüber nicht, ist die m-Zeile danach `inactive`.
   Dann `state` mit `screen: false` (ohne `screen_mid`). Beim Auflegen, bei Übergabe auf ein anderes Gerät und bei
   Verlust der PeerConnection endet das Teilen automatisch.
6. Systemton (wo die Plattform ihn liefert, Web: Chromium-Tab/Systemton) wird **in die vorhandene Tonspur gemischt**
   (Mikrofon + Quelle); Stumm schaltet nur das Mikrofon. Keine eigene Ton-m-Zeile. `state.screen_audio: true` als Hinweis.

Betriebsart A sendet `state` mit `screen: true` wie bisher; `screen_mid` ist dann die mid der **Kamera**-m-Zeile (dort läuft
der Bildschirm) – ältere Clients kennen das Feld nicht und zeigen wie bisher die einzige Videospur.

### Neue Felder im Body

| Feld | Typ | in | Bedeutung |
|---|---|---|---|
| `tracks` | Objekt | offer, answer, state | **Rolle jeder eigenen m-Zeile**: `{"<mid>": "mic" \| "cam" \| "screen"}`. Fest je m-Zeile (die Kamera-m-Zeile bleibt `cam`, auch wenn in A gerade der Bildschirm darauf läuft). Nur senden, wer `screen2` meldet. Höchstens 8 Einträge, mid `[A-Za-z0-9_-]{1,16}`, Unbekanntes ignorieren |
| `msid` | Objekt | offer, answer, state | `{"<mid>": "<Stream-ID>"}` = Stream-ID aus `a=msid:<Stream-ID> <Spur-ID>` der SDP. Ton und Kamera teilen einen Stream, der Bildschirm hat einen eigenen. Nur zur Kontrolle/Anzeige; maßgeblich ist die mid |
| `screen_mid` | Text | state | nur bei `screen: true`: mid, auf der **jetzt** der Bildschirm läuft (B: Bildschirm-m-Zeile, A: Kamera-m-Zeile) |
| `screen_audio` | bool | state | nur bei `screen: true`: Ton der Quelle ist in die Tonspur gemischt |

### Empfänger: Kamera und Bildschirm unterscheiden

Die **mid** ist auf beiden Seiten dieselbe (sie gehört zur m-Zeile) und steht am Transceiver (`ontrack`:
`event.transceiver.mid`; iOS `RTCRtpTransceiver.mid`, Android `RtpTransceiver.getMid()`).

1. `tracks` aus jedem offer/answer/state des Gegenübers merken (letzter Stand gilt).
2. `state.screen == true` **und** `screen_mid` vorhanden → die Spur auf dieser mid ist der Bildschirm. Ist es nicht die
   `cam`-mid und meldet `state.cam == true`, ist die Spur der `cam`-mid die Kamera.
3. `state.screen == true` **ohne** `screen_mid` (2.1/2.3/E0-Gegenstelle) → die einzige Videospur ist der Bildschirm.
4. Ohne `tracks` (ältere Gegenstelle): die **erste** Video-m-Zeile ist die Kamera.
5. Anzeige: Bildschirm groß (`object-fit: contain`, nicht beschneiden), Kamera klein; Tippen/„Ansicht tauschen“ tauscht.
   Sichtbare Anzeige „<Name> teilt den Bildschirm“. Neues Teilen beginnt wieder mit dem Bildschirm groß.
6. Eine Spur ohne eintreffende Bilder (`muted`) nicht groß zeigen (z. B. kurz nach dem Angebot).

### Bitrate (wie Tabelle oben)

- Bildschirm-Sender: je Weg `maxBitrate` 2 500 000 / 1 800 000 / 1 200 000, `maxFramerate` 15 / 15 / 8,
  `scaleResolutionDownBy` 1, immer `degradationPreference: "maintain-resolution"`. `want: small` ändert nichts.
  Aufnahme höchstens 1080p (lange Kante 1920) und 15 fps.
- **Kamera während des Teilens (nur B):** `maxBitrate` = min(Regel, 300 000), `scaleResolutionDownBy` ≥ Quellhöhe/360;
  über `relay-tcp` Kamera-Encoding `active: false` (Hinweis „Kamera pausiert (langsame Verbindung)“ beim Teilenden).
  Summe je Richtung über TCP ≤ 1,6 Mbit/s. Nach dem Ende gelten wieder die normalen Kamera-Regeln.
- Betriebsart A: der Kamera-Sender bekommt solange die Bildschirm-Regeln.

### Datenschutz (alle Plattformen)

- Vor dem **ersten** Teilen ein Hinweis: alles in der Auswahl ist sichtbar, auch Benachrichtigungen, Passwörter und andere
  Fenster/Chats; nichts wird aufgezeichnet oder auf dem Server gespeichert. Kästchen „Nicht mehr anzeigen“ (je Gerät).
- Während des Teilens dauerhaft sichtbar: „Du teilst deinen Bildschirm“ mit „Beenden“ (Web: rote Leiste; Apps: Leiste
  bzw. Systemanzeige). Keine Aufnahme, keine Fernsteuerung; Medien nur DTLS-SRTP zwischen den Geräten.

### Beispiele

Anrufer (Kamera mid `1`) teilt, Neuverhandlung n = 3; der Angerufene antwortet, dann Zustand:

```json
{"t":"offer","pc":"2f67…:65fb…","n":3,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…m=video 9 UDP/TLS/RTP/SAVPF 96 …\r\na=mid:2\r\na=sendrecv\r\na=msid:ec27dfd5… 9b1f…","fp":["sha-256 4A:AD:…"],
 "tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…","2":"ec27dfd5…"}}
{"t":"answer","pc":"2f67…:65fb…","n":3,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…a=mid:2\r\na=recvonly…","fp":["sha-256 1C:…"],
 "tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"452e5d05…","1":"452e5d05…"}}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":true,"screen_mid":"2","screen_audio":true,"pip":false,
 "want":{"video":"large"},"path":"direct","tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…","2":"ec27dfd5…"}}
```

Ende (Richtung `recvonly`, n = 4, danach ist die m-Zeile `inactive`):

```json
{"t":"offer","pc":"2f67…:65fb…","n":4,"caps":["reneg","want","bye","screen2"],"media":"video","sdp":"…a=mid:2\r\na=recvonly…","fp":["sha-256 4A:AD:…"],"tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":true,"screen":false,"pip":false,"want":{"video":"large"},"path":"direct","tracks":{"0":"mic","1":"cam","2":"screen"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
```

Betriebsart A (Gegenüber ohne `screen2`) – kein Angebot, nur der Zustand:

```json
{"t":"state","pc":"2f67…:65fb…","mic":true,"cam":false,"screen":true,"screen_mid":"1","pip":false,"want":{"video":"large"},"tracks":{"0":"mic","1":"cam"},"msid":{"0":"9b3c97c5…","1":"9b3c97c5…"}}
```

Tests: `tests/browser_screen2.js` (Web: B zwischen 2.4-Clients, A gegen E0- und 2.3-Client, Messung),
`tests/anrufe_v3.py` (Server reicht großes Angebot und die neuen Felder unverändert weiter).

## Anrufe: Mehrgeräte und Gruppen (2.4, Etappen E4/E5)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Web (`static/calls.js`). Ein Anruf ist ein **Raum** mit
Endpunkten je **Gerät**; ein 1:1-Anruf ist der Raum mit zwei Endpunkten und läuft genau wie bisher. Dazu kommen weitere
eigene Geräte (Begleitgerät, Übernahme) und Gruppenanrufe in Gruppen-, freien und Seiten-Chats. Medien laufen als
**Mesh**: je Gerätepaar eine PeerConnection (`pc` = sortiertes Paar der device_ids), DTLS-SRTP je Paar, Fingerabdrücke in
der signierten SDP – E2E wie bisher, der Server sieht keine SDP. Signatur und Versiegelung bleiben `saga-call/v1` bzw.
`saga-call/v2` (föderiert). Bestehende Endpunkte brechen nicht; `MIN_APP` ändert sich nicht.

### Voraussetzungen und Fähigkeiten

- Server: `GET /api/calls/config` → `features` enthält `multi` und `group`; `limits.endpoints_video` (4),
  `limits.endpoints_audio` (6), `ring_silent` (8), `handover_s` (15). Fehlt `multi`, gibt es keine Raumfunktionen.
- Gerät: `multi` in den signierten `caps` (offer/answer) und als Hinweis in den JSON-Anfragen. Nur melden, wenn der eigene
  Server `reneg` **und** `multi` liefert.
- **Fähigkeiten melden:** `PUT /api/calls/caps` `{"device": "<id>", "caps": ["reneg","want","bye","screen2","multi"]}` →
  `{"ok": true}` – beim Start bzw. sobald die Chat-Schlüssel bereit sind (und nach einem Update). Zu Gruppenanrufen
  eingeladen wird nur, wer ein Gerät mit `multi` hat; die anderen sehen nur den Chat-Eintrag („braucht Saga 2.4“).
- Beitreten geht nur, wenn **alle** Endpunkte `multi` melden (eine 2.1/2.3/E0-Gegenstelle bleibt 1:1) und – föderiert –
  alle beteiligten Server `calls3` melden. Sonst `409` mit `join_block: "old"`.
- Grenze: Endpunkte = Geräte, eigene Zusatzgeräte zählen mit. Video 4, nur Ton 6 → `409` mit `join_block: "full"`.

### Anruf-Objekt (neu)

`POST /api/calls`, `GET /api/calls/{id}`, `GET /api/calls/active`, `POST …/join` liefern zusätzlich:

```json
{"id":"…","kind":"dm","media":"video","state":"active","limit":4,"joinable":true,"join_block":null,
 "mine":["5cbb…"],"rv":1792458591.204,
 "legs":[{"who":"anna","name":"Anna","device":"0e32…","dev_name":"iPhone","role":"member","state":"active",
          "caps":["reneg","want","bye","screen2","multi"],"joined":1792458574.1,"me":false},
         {"who":"bert","name":"Bert","device":"5cbb…","dev_name":"MacBook","role":"companion","state":"active",
          "caps":["…"],"joined":1792458590.7,"me":true}]}
```

- `kind`: `dm` | `group`. `legs`: aktive Endpunkte in Beitrittsreihenfolge; föderiert zusätzlich `handle` (bei fremden
  Personen auch `host`). `role`: `member` · `companion` (Begleitgerät) · `handover` (übernimmt, mit `replaces`).
- `mine`: eigene Geräte im Anruf. `joinable`/`join_block`: kann ein (weiteres) Gerät beitreten – `old` (jemand braucht
  Saga 2.4), `full`, `ended`, `none` (1:1 noch nicht angenommen).
- `rv`: Stand der Teilnehmerliste (Zahl, streng steigend; auch in `call.roster`). Eine Liste (`legs`) aus Antwort oder
  Ereignis nur übernehmen, wenn ihr `rv` nicht kleiner ist als das der zuletzt übernommenen – Antworten und Ereignisse
  können sich überholen. Fehlt `rv` (ältere 2.4-Testserver), wie bisher immer übernehmen.
- Gruppe: `role: "member"`, `from` (wer begonnen hat), `invite` (eigene Einladung: `ringing` · `declined` · `joined` ·
  `old` · `missed`), `old` (Personen ohne 2.4-Gerät, `[{who, name}]`), `members` (Mitglieder des Chats) und `silent`
  (mehr als `ring_silent` Mitglieder) wie in `call.invite` – auch ein aus `GET /api/calls/active` nachgeholtes Klingeln
  ist bei `silent: true` still (nur „Anruf läuft – beitreten“).
- `GET /api/chats/{id}` → `chat.call`: `{"group": true|false, "group_block": "<Hinweis>"|null, "running": {id, media, kind,
  state, n, joinable, join_block, mine}|null}`. Den Gruppenanruf-Knopf nur zeigen, wenn `group` true ist (föderiert: alle
  Server melden `calls3`).

### Endpunkte (neu bzw. erweitert)

| Endpunkt | Body | Antwort / Fehler |
|---|---|---|
| `POST /api/calls` (Gruppen-, freier, Seiten-Chat) | `{id, chat_id, media, device, caps}` – **ohne** `signals` | `{ok, call}` (`kind: group`, `state: ringing`, nur der eigene Endpunkt). `409 {running}` = läuft schon → beitreten; `409` ohne `multi` bzw. Server ohne `calls3` |
| `POST /api/calls/{id}/join` | `{device, role, replaces?, caps, signals: {geraet: versiegeltes offer}}` | `{ok, call}`. `409 {join_block, error, legs?, limit?}`; `409 {legs}` ohne `join_block` = Liste veraltet → neu anbieten; `403` fremdes Gerät; `429` |
| `POST /api/calls/{id}/leave` | `{device, reason?: "hangup"\|"handover"}` | `{ok}`; nur eigene Geräte (auch ein anderes eigenes) |
| `POST /api/calls/{id}/invite` | `{uids: [benutzername \| Prinzipal]}` | `{ok, n}` – Gruppe: Mitglieder erneut klingeln lassen |
| `POST /api/calls/{id}/end` | wie bisher, **mit `device`** | Gruppe bzw. Begleitgerät: nur dieses Gerät verlässt (`{ok, left: [...]}`); Hauptgerät im 1:1: Ende für alle |
| `POST /api/calls/{id}/decline` | wie bisher | Gruppe: nur die eigene Einladung (alle eigenen Geräte hören auf zu klingeln) |
| `POST /api/calls/{id}/alive` | `{device}` | zusätzlich `leg: "left"` = dieses Gerät ist nicht mehr im Anruf (z. B. übernommen) → Anruf hier beenden |
| `POST /api/calls/{id}/signal` | wie bisher | Ziele = alle anderen aktiven Endpunkte (`legs`); föderiert sternförmig über den führenden Server |

`join`: `signals` muss je ein Angebot an **jedes Gerät anderer Personen** enthalten (eigene Geräte optional, empfohlen).
Ein zweites eigenes Gerät mit `role: "member"` wird zum `companion`. `handover` braucht `replaces` = eigenes Gerät im
Anruf. Ein Gruppenanruf wird `active`, sobald zwei Personen dabei sind, und läuft, solange mindestens zwei Personen dabei
sind; dem niemand beitritt, endet er nach `ring_s` (verpasst).

### Live-Ereignisse und Mitteilungen

- `call.roster` `{call_id, chat_id, kind, media, state, limit, legs, joinable, join_block, rv, answered, now}` an alle Konten des Anrufs
  (1:1 beide Personen mit **allen** Geräten, Gruppe alle Mitglieder). `legs[].me` ist hier immer `false` – eigene
  Endpunkte über `who` erkennen. `answered` (Beginn des Gesprächs, `null` solange es klingelt) und `now` (Server-Uhr):
  die Anrufdauer läuft bei allen ab `answered`, umgerechnet mit `now` – nicht ab dem eigenen Verbinden.
- `call.invite` `{call_id, chat_id, kind: "group", media, from: {username, name, avatar, handle?, chat}, from_device,
  ring_s, silent, members}` – Gruppenanruf klingelt. `silent: true` (mehr als `ring_silent` Mitglieder): nicht klingeln,
  nur „Anruf läuft – beitreten“ anzeigen.
- `call.state` zusätzlich: `kind: "group"`; `state: "declined"` (eigene Einladung auf einem anderen Gerät abgelehnt);
  `moved: "caller"|"callee"` mit `device` (Übernahme im 1:1). Gruppe `active` heißt nur „zwei sind dabei“ – nicht
  aufhören zu klingeln. Aufhören, wenn `call.roster` ein **eigenes** Gerät zeigt.
- Push: Art `call.group` (`call`, `chat`, `media`, `caller`, `group: true`, bei 1:1 über Knoten `dm: true`, kurze
  Lebensdauer); iOS/macOS: wie `call` über Apple-VoIP (Abo `apns-voip`, Body `kind`, `title`, `body`, `url`, `call`,
  `chat`, `media`, `caller`, `group`, `dm`) – jede VoIP-Mitteilung ist ein klingelnder Anruf für CallKit; das normale
  APNs-Abo desselben Geräts bekommt ihn dann nicht. Ältere Apps zeigen sie als normale Mitteilung.
- Chat-Eintrag `kind: "call"`: Gruppe mit `group: true` und `n` (Zahl der Personen, die dabei waren); `status`
  `ended` bzw. `missed`.

### Body (E2E, ergänzt saga-call v3)

| Feld | in | Bedeutung |
|---|---|---|
| `pc` | alle | `min(dev_a, dev_b) + ":" + max(dev_a, dev_b)`; der **Hinzukommende** legt sie in seinem Angebot fest |
| `state.ready` + `replaces` | state | Übernahme fertig: das neue Gerät schickt `{"t":"state","ready":true,"replaces":"<altes Gerät>",…}` an das alte |
| `bye.reason` | bye | `hangup` · `handover` |

### Abläufe

**Beitreten (alle Fälle):** `GET /api/calls/{id}?device=` (Liste) → je anderem Endpunkt eine PeerConnection, `offer` mit
`n: 1`, `pc`, `caps` → alle in **einem** `POST …/join`. Antworten und ICE kommen über `call.signal` (bzw. `GET …?after=`).
Ein `409` mit `legs` (ohne `join_block`) heißt: Liste neu, einmal neu anbieten.

**Angebot eines unbekannten Geräts (die anderen):** gehört das Gerät laut `legs` zum Anruf und steht es in der **signierten
Geräteliste** seiner Person (Geräte-Zertifikat vom Identitätsschlüssel, TOFU wie im Chat; föderiert über die geprüften
v2-Schlüssel)? Dann **ohne Klingeln** annehmen und „<Name> (<Gerät>) ist hinzugekommen“ zeigen; sonst ablehnen (Hinweis
„Unbekanntes Gerät von <Name> abgelehnt“). Jeden Endpunkt mit Personen- und Gerätenamen anzeigen.

**Begleitgerät („Hinzufügen“, E4):** `join` mit `role: "companion"`. Standard: Mikrofon **aus**, Wiedergabe **stumm**
(Echo-Schutz), Kamera aus – nicht nach Mikrofon fragen, solange es aus ist. Typisch: Bildschirm vom Laptop teilen, Ton
bleibt am Handy. Knöpfe: Mikrofon, Kamera, Ton an/aus, Bildschirm, **„Hierher übernehmen“** (eigener Knopf).

**Übernahme („Hierher übernehmen“):** aus der Leiste ohne Beitritt: `join` mit `role: "handover"`, `replaces` = eigenes
Hauptgerät (`legs`: eigenes Gerät mit `role != companion`); als Begleitgerät: kein neuer `join`.
1. Mikrofon und Wiedergabe zunächst aus. Sobald alle Verbindungen zu **anderen Personen** `connected` sind: Mikrofon und
   Ton an, dann `state` mit `ready: true, replaces: <altes Gerät>` an das alte Gerät.
2. Das alte Gerät schaltet sofort Mikrofon und Ton aus (Überlappung ≤ 300 ms), schickt `bye` (`reason: "handover"`) an
   die anderen, dann `POST …/leave {device, reason: "handover"}`, und zeigt „Anruf auf <Gerät> übernommen“. CallKit/
   ConnectionService: „anderswo angenommen“; auf dem neuen Gerät als laufend melden.
3. Klappt es nicht innerhalb von `handover_s` (15 s): das neue Gerät verlässt den Anruf mit Hinweis; das alte bleibt.
4. Der Server macht das neue Gerät zum Hauptgerät (`caller_device`/`callee_device` bzw. `role: member`).

**Leiste „Anruf läuft“ auf anderen eigenen Geräten:** aus `GET /api/calls/active` (nach `call.roster`/`call.state` neu
holen): eigenes Gerät im Anruf, dieses nicht → „Anruf läuft auf <dev_name>“ mit **Hinzufügen** und **Hierher
übernehmen**; Gruppenanruf im Chat ohne eigenes Gerät → „Gruppenanruf läuft · n Geräte“ mit **Beitreten**. Bei
`joinable: false` stattdessen der Hinweis (`old` → „braucht Saga 2.4“, `full` → „Der Anruf ist voll“).

**Gruppenanruf (E5):** `POST /api/calls` ohne `signals` → allein im Anruf („Klingelt bei den anderen …“). Die anderen
bekommen `call.invite` (+ Push) und treten mit `join` (`role: "member"`) bei; ablehnen mit `decline`. Verlassen mit
`end {device}` bzw. `leave`. Ein Gerät, das `bye` bekommt bzw. in `call.roster` fehlt, wird getrennt; der Anruf endet für
mich erst mit `call.state ended` bzw. `alive` → `ended`/`leg: "left"`.

**Ansicht und Bitrate:** ab drei Endpunkten Mesh-Werte je Strom (Tabelle „Bitrate je Weg“: 600/500/350 kbit/s, ≤ 540p bzw.
360p15). Kacheln je Verbindung, Sprecher hervorheben (Pegel der empfangenen Tonspur, z. B. `getSynchronizationSources()`
bzw. `audioLevel`). Ein 1:1 mit stillem Begleitgerät (ohne Ton, Kamera, Bildschirm) bleibt Einzelansicht. Ton eigener
Geräte nie abspielen. Bildschirm teilen gilt für alle Verbindungen (je Verbindung Betriebsart A/B nach deren `caps`;
eine später hinzukommende Verbindung bekommt den laufenden Bildschirm).

### Beispiele

```json
PUT /api/calls/caps        {"device":"5cbb…","caps":["reneg","want","bye","screen2","multi"]}
POST /api/calls            {"id":"7927…","chat_id":12,"media":"video","device":"0e32…","caps":["reneg","want","bye","screen2","multi"]}
POST /api/calls/7927…/join {"device":"a130…","role":"companion","caps":["reneg","want","bye","screen2","multi"],
                            "signals":{"0e32…":"<versiegelt>","5cbb…":"<versiegelt>"}}
→ 409 {"error":"Der Anruf ist voll (höchstens 4 Geräte).","join_block":"full","limit":4,"legs":[…]}
{"t":"offer","pc":"5cbb…:a130…","n":1,"caps":["reneg","want","bye","screen2","multi"],"media":"video","sdp":"…","fp":["sha-256 4A:…"]}
{"t":"state","pc":"0e32…:a130…","ready":true,"replaces":"0e32…","mic":true,"cam":false,"screen":false}
{"t":"bye","pc":"0e32…:5cbb…","reason":"handover"}
```

### Sicherheit

Ein Server könnte ein fremdes Gerät einschleusen (Geister-Gerät): Clients nehmen nur Geräte an, die in der signierten
Geräteliste der Person stehen, und zeigen jeden Endpunkt mit Namen. Lässt der Server jemanden weg, ist das nur DoS.
Ein Begleitgerät ist für alle sichtbar. Dauerhafte Schlüssel gibt es im Mesh nicht (DTLS je Sitzung).

Tests: `tests/anrufe_raum.py` (Server), `tests/browser_raum.js` (Web: 1:1 + Begleitgerät + Übernahme, 4er-Mesh,
Bildschirm in der Gruppe, 2.3-Client), föderiert `tests/foederation_anrufe_raum.py` (drei Server, auch `FED_B_23=1`) und
`BJS=1 python3 tests/foederation_anrufe_raum.py` (Browser über drei Server, `tests/browser_fed_raum.js`).

### Ausblick 2.5: SFU mit Frame-E2E (Spike D3, nur Bewertung)

Zeitlich begrenzter Spike (20.10.2026), **in 2.4 wird nichts umgestellt**. Geprüft im Web (headless Chromium 141, Loopback
zweier PeerConnections, Kamera-/Mikrofon-Attrappe 720p, 8 s): `RTCRtpScriptTransform` mit einem Worker, AES-GCM-128 je
Frame (WebCrypto), Kopf unverschlüsselt (VP8: 10 Byte Schlüsselbild / 3 Byte sonst, Opus: 1 Byte, als AAD), Trailer
`IV (12) + Schlüssel-ID (1)` wie bei LiveKit/Jitsi.

| Messung | ohne Frame-E2E | mit Frame-E2E |
|---|---|---|
| Video-Frames dekodiert | 156 | 156 (0 Entschlüsselungsfehler) |
| Zeit je Frame im Worker | – | 0,07 ms verschlüsseln, 0,05 ms entschlüsseln |
| Zusatz je Frame | – | 29 Byte (12 IV + 1 ID + 16 Tag) |

Bewertung für 2.5:
- **Web: machbar** (`RTCRtpScriptTransform` in Chromium, Safari ≥ 15.4, Firefox ≥ 117; Rückfall
  `createEncodedStreams` für ältere Chromium). CPU ist vernachlässigbar.
- **CSP:** Saga erlaubt `worker-src 'self'` – der Transform-Worker muss eine **statische Datei** sein (z. B.
  `static/frame-e2e.js`), Blob-Worker werden abgewiesen. Keine CSP-Lockerung nötig.
- **Ton:** 29 Byte je Opus-Paket (50/s) sind ≈ 11,6 kbit/s – bei 24–32 kbit/s Ton zu viel. Für 2.5 kürzerer Trailer
  (Zähler mit variabler Länge wie SFrame RFC 9605, IV aus Schlüssel-Salz + Zähler) oder 40/60 ms Opus-Rahmen. Video
  (+≈ 7 kbit/s bei 30 fps) ist unkritisch.
- **Apps:** brauchen den Bibliothekswechsel auf `webrtc-sdk` (`RTCFrameCryptor`/`FrameCryptor`, AES-GCM, gleiches
  Trailer-Format); im Spike nicht gemessen (D3: erst in 2.5, eigener Interop-Test Web ↔ iOS ↔ Android vor dem Umstieg).
- Ein striktes RFC-9605-SFrame bieten die nativen Bibliotheken nicht – Empfehlung bleibt das LiveKit-kompatible Format.
- Schlüssel: je sendendem Gerät, als `fkey`-Signal versiegelt verteilt (Abschnitt „saga-call v3“: Typ reserviert); neuer
  Schlüssel bei jedem Verlassen, HKDF-Ratsche bei jedem Beitritt.
- **Umgesetzt in 2.5 (P4):** `docs/HORN-E2E.md` (`saga-horn/v1`). Abweichend vom Spike ist der Trailer 14 Byte
  (IV ‖ IV-Länge `0x0C` ‖ kid, wie FrameCryptor), Zusatz also 30 Byte je Rahmen; die Ratsche ist Saga-eigen.
## Anrufe über Gjallarhorn-Knoten (2.5, Pakete P3 und P5)

Nur mit Feature-Flag `calls.horn` (Standard aus) und nur für Geräte, die `horn` in `caps` melden. Konzept:
`claude/saga-gjallarhorn-konzept-v2.md` §2; Frame-E2E: `docs/HORN-E2E.md`; Knoten: `gjallarhorn/NODE.md`.

**Wann:** Ein Gruppenanruf läuft über einen Knoten (`topo: "horn"` im Anruf-Objekt, in `call.roster` und in
`/api/chats/{id}` → `call.running`), wenn der Chat mehr Mitglieder hat als die Mesh-Grenze (`limits.endpoints_video`
bzw. `_audio`; Admin: `calls.horn_from`), das anrufende Gerät `horn` meldet, ein Knoten bereit ist und der Chat nicht
föderiert ist (Föderation: P10). Sonst Mesh wie 2.4 (`topo: "mesh"`). Einladungen gehen nur an Personen mit einem
Gerät mit `horn`; die anderen sehen `old` („braucht Saga 2.5“).

**Grenzen** (`GET /api/calls/config` → `limits.horn`): `video_tcp` 12, `audio_tcp` 50, `video_udp` 25, `audio_udp` 100
(UDP gilt, wenn der Knoten des Anrufs UDP meldet). `limits.endpoints_video/_audio` bleiben die Mesh-Grenzen. Das
Anruf-Objekt meldet in `limit` die Grenze dieses Anrufs.

**Beitreten:** `POST /api/calls/{id}/join {device, role, caps, dev_class?}` **ohne** `signals` (mit Angeboten: 400).
Ohne `horn` in `caps`: 409 `join_block: "old"`; kein Knoten erreichbar: 409 `join_block: "node"`. Die Antwort (wie auch
`POST /api/calls` beim Start) trägt im Anruf-Objekt:

```json
"horn": {"e2e": "saga-horn/v1", "ttl": 60, "candidates": [
  {"node": "horn-0.example.org", "url": "https://horn-0.example.org", "udp": false, "here": true, "load": 0.12,
   "peer": "<peer_ref>", "exp": 1760000000,
   "tokens": {"audio": "h1.…", "video": "h1.…", "both": "h1.… (nur mit udp)"}}]}
```

- Bis zu 3 Kandidaten, beste zuerst. Ohne Kaskade (bis P9) ist es nur der Knoten, auf dem der Anruf schon läuft.
- `dev_class`: `desktop` (Standard) oder `mobile` (Last-N: 9 bzw. 4 bewegte Kacheln).
- Jede Teilnehmerliste (`legs`) trägt je Endpunkt `hp` = Kennung des Geräts am Knoten (gleich `peer` im eigenen
  Kandidaten). Damit ordnet das Gerät die Plätze des Knotens (`slots[].peer`) und die CSRC der Rahmen einem Gerät zu.

**Frische Token:** `POST /api/calls/{id}/horn {device, node?, dev_class?}` → `{horn: {…}}` wie oben. Für das eigene,
aktive Gerät im Anruf (sonst 409, höchstens 30/min). Ist der Anruf beendet oder hat das eigene Gerät ihn verlassen
(bzw. wurde entfernt): 404 `{error, ended: true}` – das Gerät hört auf (Bughunt 3; vorher 409). `node` = gewählter Kandidat; Saga merkt ihn am Endpunkt.

**Medien-Token** (`h1.<payload>.<sig>`, Ed25519 von Saga, je Bein, 60 s, einmal verwendbar): Der Knoten prüft lokal,
das Gerät reicht es nur weiter. Inhalt `{v, node, call_ref, peer_ref, leg_ref, role, pub, max_kbit, udp_ok, dev_class,
exp, jti}` – zufällige Kennungen je Knoten, keine Konto- oder Gerätenamen. Rollen: `audio` (Ton-Leitung), `video`
(Bild-Leitung), `both` (UDP-Weg). Begleitgeräte bekommen keine Ton-Senderechte.

**Am Knoten** (HTTPS, CORS für die Saga-Herkunft):
- `POST {url}/api/sfu/leg {token, path: tcp|turn|udp, offer, audio, speaker, screen, tiles, device}` →
  `{id, answer, csrc, slots}`. Angebot: zuerst die eigene Sendespur (Ton bzw. Kamera mit Simulcast `l`/`m`), dann die
  Empfangsplätze in fester Reihenfolge (Ton: 3; Bild: Sprecher, Bildschirm falls angefragt, Kacheln).
- `POST {url}/api/sfu/turn {token}` → `{username, credential}` für TURN-TLS auf demselben Port (TLS-Weg).
- `GET {url}/api/sfu/leg/{id}` → Belegung `slots[] {i, class, peer, csrc, layer, paused}` und Schätzung;
  `DELETE` beendet das Bein.
- Fehler: 401 Token ungültig, abgelaufen, schon benutzt, für einen anderen Knoten oder Person entfernt; 404 Raum
  unbekannt (Anruf beendet); 429 voll.

**Schlüssel:** Jedes Gerät verteilt seinen Frame-Schlüssel als `fkey` über `POST /api/calls/{id}/signal` an alle
anderen Endpunkte (`docs/HORN-E2E.md` §4); im Knoten-Anruf gibt es keine Mesh-Signale (offer/answer/ice).
Verlassen (`/leave`, `/end` mit `device`) beendet die Beine des Geräts am Knoten sofort; das Ende des Anrufs schließt
den Raum.

**Umzug (2.5, P6):** Wird ein Knoten geleert, aktualisiert oder entfernt, bekommen alle Personen mit Endpunkt dort das
Live-Ereignis `call.horn_move {call_id, chat_id, node, why: drain|update|removed}`. Das Gerät holt dann mit
`POST /api/calls/{id}/horn` (ohne `node`) neue Kandidaten – ein Knoten im Leeren erscheint nicht mehr – und schaltet
make-before-break um (Web: P5, Apps). Beitritt und `/horn` folgen dem bereiten Knoten mit den meisten Endpunkten des
Anrufs. Ist kein Knoten bereit: 409 `join_block: "node"` (dann Mesh-Rückfall beim nächsten Anruf).

**Knotenbetrieb (nur Server ↔ Knoten, nicht für Apps):** `GET /api/horn/whoami` → `{ip}`, `POST /api/horn/pair`
(Kopplung), WebSocket `/api/horn/ctl` (Steuerung). Beschreibung: `gjallarhorn/NODE.md`.

### Web-Client und Live-Umschalten (P5)

Referenz: `static/calls.js` (Ablauf) und `static/horn.js` (Entscheidungen, in Node getestet: `tests/horn_logik.js`). Die
Apps (P11/P12) übernehmen dieselben Regeln. Alles Neue ist rückwärtsverträglich (neue Felder, ein neuer Endpunkt);
`MIN_APP` ändert sich nicht.

**Neue Felder am Server:**
- `GET /api/calls/config` → `horn_ready` (bool, nur mit Flag): ist gerade ein Knoten bereit? Bei `false` und einer
  Gruppe über `limits.endpoints_video/_audio` vor dem Start den Hinweis zeigen, dass die Mesh-Grenze gilt
  („Kein Knoten für große Gruppenanrufe bereit … höchstens 4 Geräte mit Video bzw. 6 nur mit Ton“).
- Teilnehmerliste (`legs[]`, auch in `call.roster`): `hpn: {knotenname: peer_ref}` – Kennung des Geräts auf **jedem**
  Knoten, auf dem der Anruf laufen kann (`hpn[Knoten des Anrufs] = hp`). Während eines Wechsels sind Geräte kurz auf zwei
  Knoten; je Verbindung gilt die Kennung dieses Knotens. Taucht am Knoten eine unbekannte Kennung auf, die Liste neu holen
  (`GET /api/calls/{id}`).
- Anruf-Objekt (Gruppe): `host` (bool) – dieses Konto hat den Anruf begonnen (Gastgeber).
- `POST /api/calls/{id}/horn {device, node?, dev_class?, avoid?: [knotenname]}`: `avoid` = Knoten, die das Gerät nicht
  erreicht oder verlässt; sie fehlen in den Kandidaten. **Knoten des Anrufs** = unter den bereiten, nicht gemiedenen
  Knoten der mit den meisten aktiven Endpunkten. Ohne Kaskade (bis P9) kommt nur er, `node` darf nur er sein (sonst
  409). Läuft dort nichts mehr (Knoten getrennt, `draining`, voll oder gemieden), kommen die besten bereiten Knoten; der
  erste, auf den jemand wechselt, ist danach der Knoten des Anrufs (`here: true`). Ein Beitritt landet ebenso dort.

**Gastgeber:** `POST /api/calls/{id}/moderate {device, target, action: mute|remove}` – nur mit Flag `calls.horn` (sonst
404 `not available`; `by` ist der Anzeigename, nie der Anmeldename), nur wer den Gruppenanruf begonnen
hat (sonst 403), nicht für eigene Geräte (400). `mute`: Ereignis `call.mod {call_id, chat_id, action, device, by}` an die
Person; ihr Gerät `device` schaltet das Mikrofon aus und zeigt „{by} hat dich stummgeschaltet“ (sie kann es selbst wieder
einschalten – Ton bleibt Ende-zu-Ende verschlüsselt, der Server kann nicht selbst stummschalten). `remove`: alle Geräte
der Person verlassen den Anruf (Grund `removed`, am Knoten sofort beendet), `call.mod` mit `action: remove` an die
Person; Beitritt danach 409 `join_block: "removed"`, bis sie erneut eingeladen wird (`POST /invite`). Die anderen wechseln
den Frame-Schlüssel wie bei jedem Abgang.

**Am Knoten (neu):**
- `POST /api/sfu/leg` nimmt zusätzlich `screen_mid` (mid des Bildschirm-Senders; Browser können die Stream-ID nicht
  wählen) und antwortet zusätzlich mit `peers: {peer_ref: csrc}` (alle Teilnehmenden des Raums) und `state` (`ok` |
  `draining`). Mit `peers` kennt das Gerät jeden Absender vor dessen erstem Rahmen.
- **Ereigniskanal** `GET wss://{knoten}/api/sfu/leg/{id}/events` (WebSocket, Herkunft der Saga-Seiten, die Bein-Kennung
  ist die Berechtigung): `hello {leg, peers, slots, est_kbit, state}`, `peers {peers}` (Beitritt sofort, Abgang im
  nächsten Takt), `layout {slots, est_kbit}`, `state {state}`, `closed {why}` (z. B. `entfernt`, `ersetzt`, `raum
  geschlossen`), `pong {n}` auf `{t: "ping", n}` (höchstens alle 150 ms). Ersetzt das Abfragen von `GET /api/sfu/leg/{id}`.
- Während des Leerens (`state: draining`) antwortet `POST /api/sfu/leg` mit 429.

**Bild-Leitung (Angebot):** eigene Kamera (Simulcast `l`/`m`, UDP zusätzlich `h`), eigener Bildschirm-Sender (ohne Spur,
bis geteilt wird), dann die Empfangsplätze Sprecher, Bildschirm, Kacheln. **Last-N zählt den Sprecherplatz mit:**
Desktop 1 + 8 Kacheln = 9 bewegte Bilder, Handy 1 + 3 = 4; alle weiteren als Standbild (letztes Bild) mit Sprechring.
TCP/TLS-Wege: `jitterBufferTarget = 150` ms an den Bildempfängern.

**Zustand:** Body-Typ `state {mic, cam, screen, hp}` über den versiegelten Signalweg an alle Endpunkte (nach jeder Änderung,
gebündelt, an jedes neue Gerät und – P9/P10 – nach jeder neuen Verbindung zu einem Knoten). `hp` = die eigenen Kennungen
(`peer_ref`) an den Knoten, mit denen das Gerät gerade verbunden ist (höchstens 4). Ältere Clients ignorieren ihn.

**Knotenwahl beim Beitritt** (Konzept §2.6): alle Kandidaten parallel messen, zusammen höchstens 800 ms – TLS-Aufbau und
5 Pings über `wss://{knoten}/probe` (Echo), bei `udp_ok` ein STUN-Binding an `{knoten}:{port}` (UDP).
Punktzahl (kleiner = besser): `rtt_p50 + 2·jitter + Lastabzug + (UDP fehlt ? 15 : 0) − (here ? 25 : 0)`, Lastabzug
`(load − 0,7) · 200 ms` ab 70 %, ab 90 % +1000 ms. Bei nur einem Kandidaten sofort verbinden (Messung nebenher für die
Anzeige; mit `udp` wird vorher gemessen). Weg: UDP (`tokens.both`, eine Leitung), wenn der Knoten `udp_ok` meldet und
das STUN-Binding antwortet (srflx-Kandidat bzw. Kandidatensuche ohne Fehler beendet; Versuch 2 s) – ist das Ergebnis
unklar (ohne NAT verwirft der Browser den srflx-Kandidaten), UDP kurz probieren (1,5 s); sonst ICE-TCP (4 s), sonst
TURN-TLS mit frischen Token. Auf TCP/TLS zu einem Knoten mit UDP alle 10 s erneut messen. Scheitert ein Knoten: 60 s meiden, Meldung „Knoten … ist nicht
erreichbar – versuche …“, nächster Kandidat; gar keiner: „Kein Knoten erreichbar – neuer Versuch in … s“ (2 … 15 s, nach
2 min aufgeben).

**Live-Umschalten (make-before-break):**

| Auslöser | Reaktion |
|---|---|
| ICE `disconnected` > 2 s, `failed`/`closed`, Ereignis `closed` | sofort: `avoid` = aktueller Knoten, bester Kandidat (gibt es nur ihn: dorthin neu) |
| Ereignis `state: draining` | sofort wie oben |
| UDP-Verlust > 20 % für 3 s | TCP-Weg am selben Knoten |
| UDP-Probe ok für 30 s (auf TCP) | zurück auf UDP, höchstens 1× je 5 min |
| anderer Kandidat besser (alle 30 s neu gemessen) | nur wenn ≥ 25 % **und** ≥ 30 ms besser, 20 s am Stück; Verweildauer 60 s; höchstens 3 solche Wechsel je 10 min |

Ablauf: neue Leitungen aufbauen (frische Token mit `node`), dort senden – derselbe Frame-Schlüssel, neue SSRC (eigener
Zähler und Wiederholungsschutz je SSRC). Ton je Absender aus der neuen Verbindung, sobald sie Rahmen liefert und der
Absender dort einen Tonplatz hat, bis dahin aus der alten (keine Lücke, kein Doppel). Bild: Kachel wechselt auf den
neuen Platz (ein Schlüsselbild). Die alte Verbindung schließt 2 s nachdem die neue liefert; solange dort noch Geräte
sind, die nicht gewechselt haben, höchstens 15 s. CSRCs vergibt jeder Knoten selbst: Zuordnung CSRC → Gerät je
Verbindung.

### Kaskade und Knoten anderer Server (P9/P10)

- **Kandidaten:** neben dem Knoten des Anrufs (`here: true`, zuerst) auch andere bereite Knoten. Der Bonus „here“ hält
  die meisten Geräte auf einem Knoten; wählt ein Gerät einen anderen (deutlich besser, Umzug), verbindet Saga die
  Knoten (Kaskade). Für Apps ändert sich am Ablauf nichts: frische Token mit `node` holen, verbinden.
- **Teilnehmende anderer Knoten** erscheinen am eigenen Knoten wie alle anderen in `peers` (Kennung → CSRC), mit der
  Kennung **ihres** Knotens. Zuordnung Kennung → Gerät: zuerst `hp`/`hpn` der Teilnehmerliste (alle Knoten dieses
  Servers, nicht nur der eigenen Verbindung), dann `state.hp` der Geräte (Knoten anderer Server). Unbekannte Kennung:
  Teilnehmerliste neu holen, Rahmen bis zur Zuordnung verwerfen.
- **Anrufe, die ein anderer Server führt** (Föderation, `docs/APP-API-FED.md` §7.4): `horn.candidates[].home: true` =
  geteilter Knoten des Heims (direkt verbinden), `horn.fed {share, why}`, ohne Kandidaten `horn.error` (Meldung anzeigen,
  wie „kein Knoten“ erneut versuchen). Ein Gruppenanruf ohne Knoten mit einem anderen Server trägt `horn_mesh` (Hinweis
  einmal anzeigen).

### 1:1 über Knoten (2.5, „Alle Anrufe über Knoten“)

Admin-Einstellung `calls.horn_all` (Standard aus, Anrufe → Gjallarhorn). Ist sie an (und `calls.horn`), meldet
`GET /api/calls/config` `horn_dm: true`. Die Geräte sehen sich dann gegenseitig keine IP-Adressen.

- **Anrufen:** Gerät mit `horn` + `multi` schickt im Direkt-Chat `POST /api/calls {id, chat_id, media, device, caps,
  via: "horn", dev_class?}` **ohne** `signals`. Erfolg: 200 `{call}` wie ein Gruppenanruf über Knoten (`kind: "group"`,
  `topo: "horn"`, `dm: true`, `horn.candidates`); weiter wie in diesem Abschnitt (Knoten verbinden, `fkey`).
- **Rückfall:** 409 `{fallback: "direct", horn_block, error}` – es ist nichts angelegt. `horn_block`: `off` (Einstellung
  aus), `caps` (Gerät ohne horn/multi), `node` (kein Knoten bereit), `peer` (Gegenüber ohne Gerät mit `horn`), `fed`
  (föderierter Direkt-Chat, dessen Server kein `horn_dm` kann bzw. den Auftrag ablehnt – sonst gehen föderierte
  Direkt-Chats sonst auch über Knoten, siehe `APP-API-FED.md` 7.5). Der Client ruft sofort mit **derselben id** wie bisher direkt an
  (Angebote je Gerät). Andere 409 wie beim direkten Anruf: `busy` (der Anruf ist angelegt und als „besetzt“ beendet),
  `incoming` (Gegenüber ruft gerade an).
- **Ankommend:** `call.invite {…, kind: "group", dm: true}`, Push `call.group` mit `dm: true` (Text wie 1:1, `caller` =
  Name der anrufenden Person). Anzeige wie 1:1: Name/Avatar aus `from` (Anrufobjekt: `peer` = Gegenüber, `from` =
  anrufende Person). Annehmen = `POST /api/calls/{id}/join {device, role: "member", caps, dev_class}` ohne `signals`.
  Ablehnen = `POST /decline {reason: "declined"|"busy"}` beendet den Anruf (beim Anrufer `call.state ended`, Grund
  `declined`/`busy`). Telefoniert das Gerät gerade: `decline` mit `busy` (wie 1:1).
- **Zustände:** `call.state active {kind: "group", dm: true}` (andere eigene Geräte hören auf zu klingeln), Ende wie
  Gruppe (`leave`/`end`; unter zwei Personen endet der Anruf). Chat-Eintrag wie 1:1 (`from`/`to`, ohne `group`/`n`).
  Besetzt-Prüfung, Klingeldauer und „verpasst“ wie 1:1.
- **Ältere Clients:** rufen weiter direkt an. 2.4-Geräte des Gegenübers sehen den Anruf als Gruppenanruf; Beitreten
  scheitert mit `join_block: "old"` („braucht Saga 2.5“). Klingeln wird nur, wenn das Gegenüber mindestens ein Gerät
  mit `horn` hat.

## Videos im Chat: saga-file/v2 (2.4, Etappe E2)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android, Linux und Web (Referenz: `static/chat-video.js`, `static/sw.js`,
Server `saga/chat_files.py`, Format `saga/file_v2.py`). Videos bis `limits.file_max` (`ATTACH_MAX_MB`, Standard 10 MB)
bleiben im bisherigen Format v1 (ein AEAD-Block, `POST /api/chats/{id}/files`) und funktionieren in allen Apps wie bisher.
Größere Videos gehen **nur** im neuen Format `saga-file/v2` (blockweise, wahlfreier Zugriff, fortsetzbarer Upload).
Ältere Apps zeigen sie als Anhang mit Namen; Öffnen schlägt dort fehl („Mit Saga 2.4 öffnen“). `MIN_APP` ändert sich
nicht – alles Neue wird über `limits.file2` ausgehandelt.

### Fähigkeiten (`GET /api/chat/me`)

```json
"limits": {"file_max": 10485760, "file2": true, "video_max": 524288000, "upload_part": 8388608, "file2_cs": 262144,
           "uploads_max": 2, "quota": 4294967296, "used": 123456789, "…": "…"}
```

- `file2: true` = der Server kann saga-file/v2 (Upload in Teilen, Range, Medien-Token). Fehlt das Feld (Server 2.3):
  nur v1 bis `file_max` anbieten.
- `video_max` = größte **Klartext**-Größe eines v2-Videos (Admin: Einstellungen → Chats, sonst `VIDEO_MAX_MB`, Standard 500).
- `upload_part` = höchstens so viele Bytes je `PUT`. `file2_cs` = Blockgröße (in 2.4 immer 262144).
- `quota`/`used` = Chat-Speicher des Kontos in Bytes (Standard 4096 MB, Admin änderbar); laufende Uploads zählen mit
  ihrer vollen Größe. Anzeigen als „Chat-Speicher: 1,2 von 4 GB“.
- Chats über Servergrenzen: Chat-Objekt `big_files` (`true`, wenn alle beteiligten Server `chatfile2` können). Bei
  `false` (Gegenüber hat Saga 2.3) nur Videos bis `file_max` anbieten, sonst Hinweis „Der Server des Gegenübers kann
  große Videos noch nicht“. Der Server prüft es ohnehin (`409` beim Anlegen des Uploads und beim Senden).

### Absender: Poster, Metadaten, „optimiert 720p“

- **Poster** erzeugt das Absender-Gerät: Bild bei 1 s (bzw. 10 % der Dauer), längste Seite 320 px, JPEG, **≤ 30 KB**.
  Es geht wie ein Vorschaubild als **v1-Anhang** mit eigenem Schlüssel hoch (`th`, wie bei Bildern seit 2.1) – auch
  ältere Apps können es so zeigen.
- **Standard auf dem Handy: „optimiert 720p“** (H.264/HEVC, lange Seite 1280, kurze 720, ~2,5 Mbit/s, AAC 128 kbit/s,
  `moov` vorn/faststart), „Original“ wählbar. iOS `AVAssetExportSession` (`shouldOptimizeForNetworkUse = true`), Android
  Media3 `Transformer`. Web: nur wo der Browser MP4 aufnehmen kann (MediaRecorder), sonst Original mit Hinweis. Höchstens
  ein Export gleichzeitig, bei `thermalState >= .serious` pausieren.
- Ist das (optimierte) Video größer als `file_max`: saga-file/v2. Größer als `video_max`: nicht senden (Hinweis).

Nachrichteninhalt (verschlüsselt, `body.files[]`; neue Felder, ältere Apps ignorieren sie):

```json
{"id": "Q1w2E3r4T5y6U7i8O9p0AsDf", "k": "<B64 32 Bytes K>", "n": "Urlaub.mp4", "m": "video/mp4", "s": 187654321,
 "x": 2, "cs": 262144, "dur": 83.4, "w": 1280, "h": 720, "rot": 0, "fs": true,
 "th": {"id": "Zx9…", "k": "<B64>", "m": "image/jpeg", "w": 320, "h": 180, "s": 21877}}
```

| Feld | Bedeutung |
|---|---|
| `x` | Format: fehlt oder `1` = v1 (ein Block, wie bisher); `2` = saga-file/v2. Größere Werte: „Mit einer neueren Saga-Version öffnen“ |
| `s` | Größe des **Klartexts** (Bytes). Blob-Größe = `48 + s + 16 · N`, `N = max(1, ⌈s / cs⌉)` |
| `cs` | Blockgröße (nur bei `x: 2`; 262144) |
| `dur`, `w`, `h`, `rot` | Dauer in Sekunden (eine Nachkommastelle), Abmessungen des Videos, Drehung (0/90/180/270) |
| `fs` | Hinweis: `moov` liegt vorn (faststart). Nur fürs Vorladen; `false` ist kein Fehler (ein Roundtrip mehr) |
| `th` | Poster wie Vorschaubilder (v1-Anhang, eigener Schlüssel) |

In der Chatliste **nur das Poster** (lazy, in Anzeigegröße dekodiert) mit Dauer und Abspiel-Symbol – **nie** ein Player
je Zelle. Der Player entsteht erst beim Antippen (ein gemeinsamer Player).

### Format saga-file/v2

```
Blob    = Kopf(48 B) || Block_0 || … || Block_{N-1}
Kopf    = "SGF2" || u8 2 || u8 0 || u16 0 || u32 cs || u32 0 || u64 s || prefix(16 B, zufällig) || 8 × 0x00   (big-endian)
Block_i = crypto_aead_xchacha20poly1305_ietf_encrypt(plain[i·cs .. (i+1)·cs), AD_i, nonce_i, K)        (Länge ≤ cs + 16)
nonce_i = prefix(16 B) || u64_be(i)
AD_i    = LP("saga-file/v2", u64_be(i), u64_be(N), u32_be(cs), u64_be(s), u8(i == N-1 ? 1 : 0))
LP      = je Feld u32_be(Länge) || Bytes (wie saga-chat/v1)
```

- `K` (32 B) neu je Datei, nur in `files[].k`. Eine leere Datei hat einen Block (nur das Tag, 16 B).
- Klartext-Bereich `a..b` → Blöcke `i = a / cs` bis `j = b / cs` → Ciphertext-Bytes `48 + i·(cs+16)` bis
  `min(48 + (j+1)·(cs+16), Blob-Größe) − 1`. Entschlüsseln je Block, dann zuschneiden.
- Jeder Block prüft `i`, `N`, `cs`, `s` und die Endmarke: vertauschte, fehlende oder angehängte Blöcke und ein falscher
  Kopf schlagen fehl. **Nie** Klartext eines Blocks ausgeben, dessen Tag nicht stimmt.
- Testvektoren (CI-Pflicht in jeder App): `tests/vectors/file_v2.json`. Klartext je Fall `plain[k] = k mod 251`; geprüft
  werden Kopf, AD/Nonce des ersten und letzten Blocks, Blockanfänge/-enden und SHA-256 des ganzen Blobs, dazu
  `reject` (muss scheitern) und `ranges` (Bereich → Blöcke). Gegenproben: `tools/file_vectors_v2.py`, `.mjs`.

### Upload (fortsetzbar)

1. Anlegen – liefert die Upload-Kennung (= spätere Datei-Kennung):

   ```
   POST /api/chats/{cid}/uploads        {"size": <Blob-Größe>, "fmt": 2}
   200 {"upload": "Q1w2…", "offset": 0, "size": 187700000, "part_max": 8388608, "expires": 1760999999.0,
        "url": "/api/chat/uploads/Q1w2…"}
   ```

   `400` Größe passt zu keinem v2-Blob · `409` Server des Gegenübers ohne chatfile2 · `413` größer als `video_max` oder
   Chat-Speicher voll (Text in `error`) · `429` mehr als `uploads_max` gleichzeitig bzw. zu schnell.
2. Teile senden, **nur fortlaufend** (Kopf und Blöcke wie oben, Teilgrenzen beliebig, höchstens `part_max`):

   ```
   PUT /api/chat/uploads/{id}
   Content-Range: bytes 0-8388607/187700000
   Content-Type: application/octet-stream          (+ Anmeldung wie immer, X-Saga: 1)
   200 {"offset": 8388608, "done": false}          Upload-Offset: 8388608
   …
   200 {"id": "Q1w2…", "size": 187700000, "fmt": 2, "offset": 187700000, "done": true}     (letzter Teil)
   ```

   `409 {"error": …, "offset": n}` falsche Stelle oder ein anderer PUT läuft gerade (dann ab `n` weiter; `error` ist nur
   Text, maßgeblich ist `offset`) ·
   `400` Content-Range/Content-Length passen nicht, unvollständiger Teil, beim Abschluss falscher Kopf (der Upload ist
   dann verworfen) · `413` Teil zu groß · `404` Upload unbekannt, abgelaufen (24 h) oder abgebrochen.
3. **Fortsetzen** nach Netzabbruch oder App-Neustart: `HEAD /api/chat/uploads/{id}` → `204` mit `Upload-Offset` und
   `Upload-Length`; ab `Upload-Offset` weiter (auch mitten im Block – die Verschlüsselung ist deterministisch, die App
   erzeugt die Bytes ab jeder Stelle neu). Ist der Upload schon fertig (Antwort verpasst): `204` mit
   `Upload-Done: 1` und `Upload-Offset = Upload-Length` – Datei-Kennung = Upload-Kennung.
   Dafür merkt sich die App **K, prefix, Datei-Pfad und Upload-Kennung** im geschützten Speicher (Schlüsselbund,
   EncryptedSharedPreferences, libsecret) bis zum Senden der Nachricht; danach löschen.
4. Abbrechen: `DELETE /api/chat/uploads/{id}`. Danach wie bisher `POST /api/chats/{cid}/messages` mit
   `files: [id, th.id]`.

Im Container nimmt saga-media die Teile an und schreibt sie direkt auf die Platte (keine Bytes durch Python); ohne
saga-media antwortet Python mit genau denselben Codes.

### Abruf mit Range und Medien-Token

- `GET /api/chat/files/{fid}` mit `Range: bytes=a-b` → `206` mit `Content-Range`, `Accept-Ranges: bytes`, `ETag: "<fid>"`
  (unveränderlich), `If-Range` wird beachtet; `bytes=-n` (Ende) geht, mehrere Bereiche nicht (dann nur der erste bzw.
  `multipart/byteranges` ohne saga-media – nicht verwenden). Hinter dem Ende → `416`. Die App fordert nur die Blöcke an,
  die der Player braucht (Kopf einmal: `bytes=0-47`).
- **Kürzere Antwort möglich**: Liegt der Anhang bei einem anderen Server und fehlt der Bereich noch, holt der eigene
  Server ihn erst; ohne saga-media liefert er dann höchstens 8 MiB je Antwort (`Content-Range` beachten, Rest neu anfordern).
- **Medien-Token** für lange Wiedergaben (die Berechtigung hängt so nicht an einem wechselnden Cookie):

  ```
  POST /api/chat/files/{fid}/token          (Anmeldung wie immer, X-Saga: 1)
  200 {"token": "m1.eyJ…", "expires": 1761000900, "ttl": 900}

  GET /api/chat/files/{fid}
  Range: bytes=48-1310847
  X-Saga-Media-Token: m1.eyJ…                (kein Cookie, kein X-Saga nötig)
  ```

  Gilt nur für diese Datei und diese Anmeldung (Konto, Passwort-Stand, Gerät). Bei `401` ein neues Token holen. Jede
  Abfrage prüft Konto und Mitgliedschaft neu; saga-media merkt sich eine Erlaubnis höchstens 60 s – Entzug (Nachricht
  gelöscht, Mitglied entfernt, Passwort geändert, Gerät abgemeldet) wirkt also spätestens nach 60 s.

### Wiedergabe je Plattform

Gemeinsam: Ciphertext blockweise holen (Range, mit Token), Block entschlüsseln, Klartext an den Player – **kein**
Komplett-Download, Klartext nie auf die Platte (außer beim ausdrücklichen Speichern). Ciphertext-Cache je App
(LRU, Standard 512 MB, getrennt vom Bildcache). Mobilfunk: kein Autoplay, nur Poster vorab.

**iOS/macOS** – `AVURLAsset` mit eigenem Schema und `AVAssetResourceLoaderDelegate`:

```swift
let asset = AVURLAsset(url: URL(string: "saga-media://\(file.id)")!)
asset.resourceLoader.setDelegate(loader, queue: loaderQueue)          // ein gemeinsamer AVPlayer
// loader: resourceLoader(_:shouldWaitForLoadingOfRequestedResource:) -> true
if let info = req.contentInformationRequest {
    info.contentType = UTType(mimeType: file.m)?.identifier ?? UTType.mpeg4Movie.identifier
    info.contentLength = Int64(file.s)                                 // Klartext-Größe
    info.isByteRangeAccessSupported = true
}
if let d = req.dataRequest {
    let a = d.currentOffset, b = d.requestsAllDataToEndOfResource ? file.s - 1 : d.requestedOffset + Int64(d.requestedLength) - 1
    // Blöcke a/cs ... min(b, a + 4 MiB)/cs: Ciphertext-Range holen (URLSession, X-Saga-Media-Token), je Block
    // crypto_aead_xchacha20poly1305_ietf_decrypt(AD_i, nonce_i), zuschneiden -> d.respond(with:), dann req.finishLoading()
}
```

`preferredForwardBufferDuration` 20 s (Mobilfunk 10 s). Speichern: blockweise in eine Temp-Datei
(`NSFileProtectionComplete`), dann `PHAssetCreationRequest` (nur `addOnly`) bzw. „In Dateien sichern“, Temp-Datei sofort
löschen. macOS: `NSSavePanel` als Strom.

**Android** – Media3 ExoPlayer mit eigenem `DataSource` über einem Ciphertext-`CacheDataSource`:

```kotlin
class SagaFileDataSource(private val f: FileMeta, private val ct: DataSource /* CacheDataSource -> OkHttp mit Token */) : BaseDataSource(true) {
    override fun open(spec: DataSpec): Long {            // spec.position/length = Klartext
        pos = spec.position; end = if (spec.length == C.LENGTH_UNSET.toLong()) f.s - 1 else pos + spec.length - 1
        head = readCipher(0, 47); return end - pos + 1   // Kopf einmal, pruefen (SGF2, cs, s)
    }
    override fun read(buf: ByteArray, off: Int, len: Int): Int {
        if (pos > end) return C.RESULT_END_OF_INPUT
        val i = pos / f.cs; val block = blockCache.getOrPut(i) { decryptBlock(i, readCipher(48 + i * (f.cs + 16), …)) }
        val n = minOf(len.toLong(), block.size - (pos - i * f.cs), end - pos + 1).toInt()
        System.arraycopy(block, (pos - i * f.cs).toInt(), buf, off, n); pos += n; bytesTransferred(n); return n
    }
}
```

`DefaultLoadControl` 15–30 s, eine Player-Instanz. Speichern: `MediaStore.Video` mit `IS_PENDING = 1`, entschlüsselt als
Strom schreiben, danach `IS_PENDING = 0` (ab Android 10 ohne Berechtigung).

**Linux (GTK 4)** – `Gtk.MediaFile.new_for_input_stream()` mit eigenem `Gio.InputStream` + `Gio.Seekable` (PyNaCl je
Block):

```python
class SagaStream(Gio.InputStream, Gio.Seekable):
    def do_read_fn(self, buf, cancellable):             # buf: Puffer, liest ab self.pos
        i = self.pos // cs; blk = self.block(i)          # Ciphertext 48 + i*(cs+16) .. holen (httpx, Token), entschluesseln
        n = min(len(buf), len(blk) - (self.pos - i * cs)); buf[:n] = blk[self.pos - i * cs:][:n]; self.pos += n; return n
    def do_seek(self, offset, type_, cancellable): …  # SEEK_SET/CUR/END auf Klartext-Positionen
    def do_can_seek(self): return True
```

Fallback ohne GL (siehe Klingelton in `calls.py`): GStreamer `appsrc` mit `stream-type=random-access` (Signal
`seek-data`), sonst „Im Standard-Player öffnen“ über eine private Temp-Datei (0600, beim Beenden gelöscht, mit Hinweis).
Speichern: `Gtk.FileDialog` und Entschlüsselung als Strom.

**Web** – Service Worker (`static/sw.js`): Seite meldet die Wiedergabe mit `postMessage({op:'media', id, fid, key, size,
cs, m, token})` an, `<video src="/_m/<id>">`; der Service Worker beantwortet Range-Anfragen mit 206 und entschlüsselt
blockweise (Strom). Ohne Service Worker: bis 100 MB als Blob, sonst nur „Herunterladen“. Speichern: `showSaveFilePicker`
als Strom, sonst Download-Strom über den Service Worker (`/_m/<id>?dl=1`), zuletzt Blob (≤ 500 MB).

### Grenzen und Antworten (Übersicht)

| Was | Grenze | Antwort |
|---|---|---|
| Video (Klartext) | `video_max` (Standard 500 MB) | `413` beim Anlegen |
| Chat-Speicher je Konto | `quota` (Standard 4096 MB, inkl. laufender Uploads) | `413` |
| gleichzeitige Uploads | `uploads_max` (2) | `429` |
| Teil je PUT | `upload_part` (8 MiB) | `413` |
| Upload offen | 24 h, danach entfernt | `404` |
| Föderation mit Saga 2.3 | nur bis `file_max` | `409` (Chat-Objekt `big_files: false`) |
| Medien-Token | 15 min, Entzug ≤ 60 s | `401` → neues Token |
## Gelesen-Bestätigungen und Online-Status (2.4)

Verbindlicher Vertrag für iOS/iPadOS/macOS, Android und Linux (Web: `static/chat.js`, Server: `saga/chat_status.py`).
„Wie WhatsApp“: **standardmäßig an**, je Konto getrennt abschaltbar, **gegenseitig** (wer etwas abschaltet, sendet es
nicht und sieht es bei anderen auch nicht). Nur neue Endpunkte und optionale Felder – ältere Apps merken nichts,
`MIN_APP` ändert sich nicht. Föderiert: `APP-API-FED.md`, Abschnitt 11.

### Erkennen, ob der Server es kann

`GET /api/chat/me` liefert zusätzlich (ältere Server nicht → Funktion ausblenden):

```json
{"privacy": {"receipts": true, "online": true}, "features": ["receipts", "online"],
 "status_limits": {"receipt_gap": 2.0, "presence_ttl": 90, "presence_users": 50, "last_round": 300}, "…": "…"}
```

Chats über Servergrenzen (`v: 2`) tragen in `/api/chats` und `/api/chats/{id}` zusätzlich `receipts_ok` und `online_ok`:
`false` = der andere Server (Saga 2.3) kann es nicht → keine Bestätigung versiegeln, keinen Online-Status anzeigen.
Lokale Chats haben die Felder nicht (= `true`).

### Einstellungen

- `GET /api/chat/privacy` → `{"receipts": true, "online": true}`
- `PUT /api/chat/privacy` mit `{"receipts": false}` und/oder `{"online": false}` → neuer Stand (400 bei anderen Typen).
  Live-Ereignis `chat.privacy {receipts, online}` an die eigenen anderen Geräte/Tabs.
- Texte (de): „Gelesen-Bestätigungen senden“ – „Aus: Andere sehen nicht, ob du ihre Nachrichten gelesen hast – und du
  siehst es bei ihnen auch nicht.“ · „Online-Status zeigen“ – „Aus: Deine Chat-Partner sehen nicht, ob und wann du
  zuletzt online warst – und du siehst es bei ihnen auch nicht.“ (alle fünf Sprachen in `saga/i18n/*.json`).
- `receipts: false`: Der Server zieht alle eigenen Bestätigungen zurück (auch über Servergrenzen), nimmt keine neuen an
  (`receipt: "off"`) und liefert keine fremden mehr (`enabled: false`). `online: false`: niemand sieht den eigenen
  Status, Abfragen liefern `enabled: false` und nichts.

### Format (Ende-zu-Ende)

Eine Bestätigung sagt „gelesen bis Nachricht `id` (Position `n`)“. `n` ist die Reihenfolge, die alle Teilnehmer gleich
sehen: in lokalen Chats `seq`, in Chats über Servergrenzen `fseq` (Reihenfolge des Heims).

```
body   = JSON-Text {"id": "<msg_id, 32 hex>", "n": <int>, "ts": <ms seit 1970>}     (genau dieser Text wird signiert)
sig    = sign_detached(LP("saga-chat/v1/receipt", str(chat_id), benutzername, geraet, body), device_sign_sk)    lokal
       = sign_detached(LP("saga-chat/v2/receipt", chat_ref, handle, geraet, body), device_sign_sk)             v2
inner  = JSON-Text {"v": 1, "dev": "<geraet>", "body": body, "sig": B64(sig)}
sealed = B64(crypto_box_seal(inner, backup_pub des Gegenübers))          je anderem Mitglied eines
```

Versiegelt wird für den **Backup-Schlüssel** jedes anderen Mitglieds (alle Geräte eines Kontos haben `backup_sk`) –
eine Bestätigung je Person, nicht je Gerät. Testvektoren (Pflicht in der CI jeder App): `tests/vectors/receipts.json`
(Erzeugen/Prüfen `tools/receipt_vectors.py`, Gegenprobe libsodium.js `tools/receipt_vectors.mjs`). Web:
`sealReceipt`, `openReceipt`, `verifyReceipt`, `receiptMsg` in `static/chat-crypto.js`; Python: `tests/chatlib.py`.

### Senden

Zusammen mit dem Lese-Zeiger: `POST /api/chats/{id}/read` mit `{"seq": <wie bisher>, "receipt": {"keys": {…}}}`.

- `keys`: lokal `"backup:<benutzername>"`, v2 `"backup:<handle>"` → `sealed`, für jedes andere Mitglied mit
  Chat-Schlüsseln. Schlüssel lokal aus `GET /api/chats/{id}/recipients` (`backup_pub`, vorher `backup_sig` prüfen wie
  beim Senden), v2 aus `GET /api/chat/v2/keys` (selbst geprüft; bei TOFU `reject`/`block` weglassen). Höchstens 50
  Einträge, je 112–2048 Zeichen; Unbekannte (keine Mitglieder, man selbst) ignoriert der Server.
- **Wann:** nur wenn der Chat wirklich sichtbar ist (App bzw. Tab im Vordergrund, Chat offen – wie beim Lese-Zeiger),
  die neueste Nachricht mit Position **nicht von dir** ist und ihr `n` größer als das zuletzt bestätigte.
  Höchstens **eine je Chat und 2 s** (`status_limits.receipt_gap`; nachziehen statt verwerfen). Nicht bei
  Nachrichtenanfragen (`request`), schreibgeschützten Chats, `receipts_ok: false` oder eigener Einstellung aus.
- Antwort wie bisher plus `receipt`: `"ok"` · `"off"` (eigene Einstellung aus) · `"none"` (niemand zu benachrichtigen,
  Anfrage noch offen, schreibgeschützt – nicht wiederholen) · `"rate"` (mehr als 120 je Minute – später erneut) ·
  `"bad"` (Form falsch). Ohne `receipt` im Body bleibt `/read` Byte für Byte wie bisher.

### Empfangen und anzeigen

- Beim Öffnen eines Chats: `GET /api/chats/{id}/receipts` →
  `{"chat_id": 7, "enabled": true, "receipts": [{"from": "bert", "sealed": "…"}]}` (v2 zusätzlich `"handle"`; `from` ist
  dann der Benutzername bzw. Prinzipal `@~x@host` wie in `members[].username`). Je anderem Mitglied nur die neueste.
  Eigene Einstellung aus → `enabled: false`, leer.
- Live: `chat.receipt {"chat_id": 7, "receipts": [{"from", "handle"?, "sealed"}]}` – je Chat gebündelt (höchstens ein
  Ereignis je 2 s mit allen neuen). Sind es mehr als 8 (große Gruppe), kommt nur `{"chat_id": 7, "receipts": [],
  "more": true}` – dann, falls der Chat offen ist, `GET …/receipts` holen.
- Öffnen mit `backup_pk/backup_sk`, dann **prüfen**: Gerät `dev` muss ein geprüftes Gerät von `from` sein (lokal: Geräte
  aus `/recipients` mit gültigem Zertifikat; v2: Geräte aus den geprüften v2-Schlüsseln von `handle`), Signatur mit
  `chat_id`/`chat_ref` und Benutzername/Handle wie oben. Nicht prüfbar → ignorieren. Je Person nur das größte `n` merken.
- **Haken an eigenen Nachrichten:** `⏳` wird zugestellt (`fstate: "pending"`) → `✓` zugestellt (beim Server bzw. Heim
  angekommen) → `✓✓` gelesen (`n` der Bestätigung ≥ Position der Nachricht). Direkt-Chat: `✓✓`, sobald das Gegenüber
  bestätigt hat. Gruppen: `✓✓`, wenn alle anderen bestätigt haben, sonst `✓` bzw. `✓✓` mit Zähler; Tippen öffnet
  „Gelesen von“ (Name + Zeit aus `ts`) mit dem Hinweis „Wer Gelesen-Bestätigungen abgeschaltet hat, erscheint hier nicht.“
- Position eigener Nachrichten in v2-Chats: `fseq` aus der Antwort von `POST …/messages` bzw. – war sie ausstehend – aus
  dem Live-Ereignis `chat.message`, das die Zustellung meldet (enthält jetzt `fseq`).
- Der Server sieht nur, **dass** es eine Bestätigung gibt (und wann), nicht bis wohin. Der Lese-Zeiger `last_read`
  (Ungelesen-Zähler, Benachrichtigungen) bleibt wie bisher auf dem eigenen Server.

### Online-Status

- Nur für Personen mit einem gemeinsamen, angenommenen Chat. Abo: `POST /api/chat/presence` mit
  `{"users": ["bert", "@~abcdefghijklmnop@b.example"]}` (Benutzernamen bzw. Prinzipale aus `members[].username` /
  `peer.username`, höchstens 50, höchstens 30 Abfragen je Minute) →

  ```json
  {"enabled": true, "ttl": 90, "users": {"bert": {"online": true, "last": null},
                                          "@~abcdefghijklmnop@b.example": {"online": false, "last": 1792449900.0}}}
  ```

  `last` = „zuletzt online“ (Unix-Sekunden, auf 5 Minuten abgerundet, `null` = unbekannt). Fehlt eine Person: kein
  Status (kein gemeinsamer Chat, abgeschaltet oder noch unbekannt) – nichts anzeigen.
- Nur abonnieren, was **sichtbar** ist (offener Direkt-Chat, sichtbare Direkt-Chats der Liste), alle **60 s** erneuern,
  solange sichtbar; beim Verlassen `{"users": [...], "leave": true}` (optional – das Abo läuft nach `ttl` aus).
- Live: `chat.presence {"username": "bert", "online": true|false, "last": <zahl>|null}` nur an Konten mit laufendem Abo.
  `online: false` mit `last: null` = Status nicht mehr verfügbar (Person hat ihn abgeschaltet) → entfernen.
- Personen anderer Server: Der eigene Server fragt beim Server der Person nach; die erste Antwort kann leer sein (z. B.
  gleich nach dem Annehmen einer Anfrage) – dann eine Minute lang alle 10 s erneut fragen, danach im 60-s-Takt.
- Online = mindestens eine Live-Verbindung (`/api/events`), offline 30 s nach der letzten (Server-Einstellung
  `CHAT_OFFLINE_GRACE`). Apps halten die Live-Verbindung wie bisher nur im Vordergrund. Anzeige: „online“ bzw.
  „zuletzt online vor 5 Minuten“ (relativ, gerundet).
- Kein Verlauf: Der Server speichert je Konto nur „zuletzt online“ im 5-Minuten-Raster, nirgends für Admins sichtbar.

### Beispiele

```http
POST /api/chats/7/read
{"seq": 812, "receipt": {"keys": {"backup:bert": "u3J…", "backup:cora": "kQ9…"}}}
→ 200 {"ok": true, "last_read": 812, "unread": 0, "receipt": "ok"}

GET /api/chats/7/receipts
→ 200 {"chat_id": 7, "enabled": true, "receipts": [{"from": "bert", "sealed": "pT1…"}]}

PUT /api/chat/privacy
{"online": false}
→ 200 {"receipts": true, "online": false}
```

## Anmeldenamen, Medien-Token, Versionen (2.4, Testrunde)

- **Anmeldenamen anderer Konten:** Personensuche (`GET /api/share/targets`, `GET /api/chat/people`), Freigaben
  (`GET/PUT/DELETE /api/notes/{id}/shares`, Feld `target` bei `type: user`), `GET /api/shared` (`owner.username`),
  Kommentare, Benachrichtigungen (`actor.username`), Anwesenheit und Versionen liefern für andere lokale Konten einen
  pseudonymen Handle (`~` + 16 Zeichen `[a-z2-7]`) statt des Anmeldenamens; `name` ist Anzeigename, öffentlicher Name
  oder derselbe Handle. Das eigene Konto (`me: true`) kommt unverändert. Apps behandeln das Feld wie bisher als
  undurchsichtige Kennung: `PUT/DELETE …/shares`, `POST /api/chats` (`user`, `members`) und
  `POST /api/chats/{id}/members` (`user`) nehmen den Handle oder den Benutzernamen. Chat-Mitglieder
  (`members[].username`, `sender.username`) und Anruf-Teilnehmer bleiben Anmeldenamen (Chats v1 und saga-call v1 binden
  Schlüssel und Signaturen daran); neu ist `members[].ref` (derselbe Handle wie in der Personensuche) zum Abgleich.
  Apps sollten den Anmeldenamen anderer nicht anzeigen.
- **Sicherheitsrunde 2 (Orakel geschlossen):**
  - Personensuche trifft andere Konten nur noch über Anzeigenamen (nicht, wenn er gleich Anmeldename/E-Mail ist),
    öffentlichen Namen, den genauen Handle und – solange der Admin-Schalter „Suche nach Anmeldename“ an ist (Standard) –
    den **genauen** Anmeldenamen. Kein Präfix des Anmeldenamens mehr; die Reihenfolge hängt nicht mehr vom Anmeldenamen ab.
  - Ist der Schalter aus, nehmen `PUT …/shares`, `POST /api/chats` (`user`, `members`) und `POST /api/chats/{id}/members`
    einen Benutzernamen nur noch für Personen, mit denen man schon einen Chat hat bzw. an die man schon geteilt hat;
    sonst dieselbe Antwort wie für ein unbekanntes Konto (404 bzw. 400). Der Handle geht immer. Apps senden ohnehin die
    Kennung aus der Personensuche – keine Änderung nötig.
  - `GET /api/chat/keys?users=` nimmt Benutzernamen **oder Handles** (`~…`) und liefert nur noch das eigene Konto und
    Personen mit einem gemeinsamen Chat (auch früher). Die Antwort steht unter der angefragten Kennung (Handle bleibt
    Handle); `null` = noch keine Chats eingerichtet. Unbekannte, gesperrte und noch fremde Konten fehlen gleichermaßen
    (bis 2.3 auch Konten, mit denen man einen Chat beginnen darf). Apps holen Schlüssel erst für Chat-Mitglieder bzw.
    Anruf-Gegenüber – das bleibt so; Schlüssel vor dem Anlegen eines Chats gibt es nicht mehr.
  - **Bekannte Grenze (Chats v1):** Wer mit einem Konto einen Chat beginnen darf, bekommt beim Anlegen des
    Direkt-Chats bzw. der Gruppe dessen Anmeldenamen in `members[].username` – auch bevor das Gegenüber geantwortet hat.
    Das ist nötig: Geräte-Zertifikate (`saga-chat/v1/device`), Zusatzdaten und Signatur jeder Nachricht
    (`saga-chat/v1/msg`, `…/sig`), die Sicherheitsnummer und saga-call v1 enthalten den Anmeldenamen, und schon die
    erste Nachricht lässt sich nur mit den so geprüften Geräteschlüsseln des Gegenübers verschlüsseln. Lokale
    Direkt-Chats kennen keine Annahme (anders als Chats über Servergrenzen mit Handles, v2). Eine Bindung an den Handle
    für neue lokale Chats bräuchte neue Zertifikate von allen Geräten (2.3-Apps erzeugen sie nicht) und ist für eine
    spätere Version vorgemerkt.
- **Unterzeile `sub` (Linux-Runde 2):** `GET /api/share/targets` und `GET /api/chat/people` liefern je Person `sub`
  (öffentlicher Name, sonst Handle `~…`, leer, wenn `name` schon der Handle ist), `members[]` in Gruppen-, freien und
  Seiten-Chats ebenso. Nie der Anmeldename. Apps zeigen `sub` unter dem Namen, damit sich Gleichnamige unterscheiden.
- **Öffentlicher Name:** `PUT /api/account/public-name` lehnt den eigenen Anmeldenamen ab (400 bzw. `ok: false` bei
  `check`); `GET` meldet `is_login: true`, wenn ein früher gewählter Name dem Anmeldenamen entspricht (Hinweis zeigen).
- **Zweiter Faktor entfernen:** `DELETE /api/account/totp` und `DELETE /api/account/passkeys/{id}` verlangen eine
  Bestätigung über `POST /api/account/security/confirm` (höchstens 5 Minuten alt); eine frische Anmeldung allein reicht
  nicht mehr. Sonst `403 {reauth: true}` – wie bisher bestätigen und wiederholen.
- **Zweiter Faktor hinzufügen, neue Wiederherstellungscodes (2.4, M3b):** `POST /api/account/passkeys/options`,
  `POST /api/account/passkeys`, `POST /api/account/totp/setup`, `POST /api/account/totp/confirm` und
  `POST /api/account/recovery` verlangen dieselbe Bestätigung, sobald das Konto schon einen Passkey oder eine
  Authenticator-App hat. Nur die Ersteinrichtung (noch kein zweiter Faktor) geht weiter mit frischer Anmeldung bzw.
  Passwort. Apps müssen deshalb auch beim **Hinzufügen** mit `403 {reauth: true}` rechnen: Bestätigungsdialog zeigen
  (`methods` aus `GET /api/account/security`), `POST /api/account/security/confirm`, dann den abgewiesenen Schritt
  wiederholen (auch den zweiten Schritt `POST /api/account/passkeys` mit derselben `credential` – die Challenge bleibt
  gültig). Ein Passkey oder eine Authenticator-App, die erst in dieser Sitzung bzw. nach dieser Anmeldung angelegt
  wurde, bestätigt nichts (`403` mit Text); `methods` und `POST /api/account/security/options` bzw.
  `/api/chat/reauth/options` bieten solche Faktoren gar nicht erst an. Ältere Server: kein `403` beim Hinzufügen.
- **Nextcloud-Blöcke:** Im Seiteninhalt (`GET /api/notes/{id}`) und im Status (`GET /api/notes/{id}/nc`) steht als
  `author` fremder Blöcke der Handle; `?a=` beim Abruf und das Zurückspeichern nehmen ihn an. Eigene Blöcke behalten den
  eigenen Namen (Vergleich mit dem eigenen Konto wie bisher).
- **Medien-Token:** `POST /api/chat/files/{fid}/token` liefert `m2.…` (verschlüsselt, nicht lesbar). Weiter nur als
  Kopf `X-Saga-Media-Token` verwenden.
- **Versionen:** `POST /api/notes/{id}/versions/checkpoint` (Rolle Bearbeiten) sichert den aktuellen Stand als Version,
  wenn er sich von der neuesten unterscheidet und diese mindestens 60 s alt ist -> `{created: bool}`. Apps können es nach
  einer Bearbeitungspause und beim Verlassen einer Seite rufen (optional; ältere Server: 404 ignorieren).
- **Anmeldung:** Fehlversuche bremsen statt sperren; die Meldung (429/401 mit `error`) nennt die Wartezeit. Eine harte
  Sperre gibt es nur nach sehr vielen Versuchen von vielen Adressen (Benachrichtigung `login.locked`, `system: true`).

## Chat-Stufe 3: Krypto über Handles (2.5, in Arbeit)

Stand der ersten Stufe; Konzept „Krypto v3 über Handles“. **Kein neues Krypto-Format:** Chats der Stufe 3 sind
`saga-chat/v2` (`docs/APP-API-FED.md`, Abschnitt 6) – gebunden an den stabilen Handle `~x@host` und `chat_ref` –, auch
wenn alle Mitglieder auf diesem Server sind. Der Server legt neue lokale Chats nur dann in Stufe 3 an, wenn die
Einstellung `chat_v3` an ist (vorerst Standard aus) und alle Mitglieder ein Handle-Zertifikat haben (jedes Konto mit
einem Gerät ab App 2.3). Bestehende Chats bleiben vorerst v1.

- **Kopfzeile `X-Saga-Chat`:** höchste Chat-Stufe des Clients. `3` (Apps/Web 2.5): Chat mit `v: 3`, `fed: false`.
  `2` (Apps/Web 2.3/2.4): **Kompat-Sicht** – derselbe Chat als `v: 2` mit `ref`, `home_self: true`, `my_handle`; die
  Krypto passt, die Apps nehmen ihre v2-Wege. Ohne Kopfzeile (2.1/2.2): `fed_hint` „mit Saga 2.5 öffnen“, keine
  Nachrichten, Senden 400. `GET /api/chat/me` meldet `features` mit `chat3`, solange die Einstellung an ist.
- **Keine Anmeldenamen anderer Konten:** `members[].username` ist für andere Konten der Handle (= `handle`), das eigene
  Konto bleibt unverändert (`me: true`). Ebenso `peer.username`, `sender.username` (Nachrichten mit `sender_handle`),
  `recipients_v2[].username`, `receipts[].from`, `title_msg.sender`, Live-Ereignisse `chat.message` (`sender`),
  `chat.typing` (`user.username`), `chat.receipt` (`from`), `chat.removed`/`chat.cleared` (`by.username`) – das eigene
  Konto bekommt in Ereignissen weiter den eigenen Benutzernamen. `devices`/`idents` in `GET …/messages` gibt es nur
  noch für Nachrichten im alten Format (ohne `sender_handle`).
- **Endpunkte nehmen den Handle:** `DELETE /api/chats/{id}/members/{handle}`, `POST /api/chats/{id}/members`
  (`user` = Such-Handle oder Handle `~x@host`), `POST /api/chat/presence` (`users`; Antwort und `chat.presence` unter
  der angefragten Kennung).
- **Schlüssel:** `GET /api/chat/v2/keys` liefert Schlüssel von Konten dieses Servers nur für sich selbst und Personen
  mit einem (früheren) gemeinsamen Chat. `GET /api/chat/keys` (v1) zählt Chats der Stufe 3 nicht. Ändern sich Schlüssel
  (neues Gerät, Zertifikate, neue Geräte-Liste), bekommen Chat-Partner mit einem gemeinsamen v2-/Stufe-3-Chat das
  Live-Ereignis `chat.keys.changed {handle}` – auch ohne Föderation.
- **Anrufe:** saga-call/v2 wie über Servergrenzen – Anruf-Objekt mit `v: 2`, `peer.username` = `peer.handle` (+ `host`),
  `legs[].who` für andere als Handle (mit `handle`, `host`; Schlüssel über `/api/chat/v2/keys`), `call.ring`/`call.invite`
  mit `from` als Handle, `call.state.by` als Handle, Anruf-Eintrag im Chat (`kind: call`) mit `from`/`to` als Handles.
  `POST /api/calls/{id}/invite` nimmt Handles.
- **Umstellung bestehender Chats:** Der Server stellt Chats v1 auf Stufe 3 um, sobald alle aktiven Geräte `chat3` melden
  (Clients der Stufe 3 tun das automatisch bei jeder Anfrage mit `device`), spätestens nach `chat_v3_days`. Danach:
  Chat-Objekt mit `v3: {seq, at, forced}`; Nachrichten mit `seq ≤ v3.seq` bzw. ohne `sender_handle` mit v1 prüfen
  (Absender-Benutzername und `devices`/`idents` gibt es dafür weiter), alles danach im Format v2; Systemzeile
  `{"type": "crypto", "level": 3, "verify"?: true}`. In Chats v1 liefert der Server Clients der Stufe 3
  `members[].handle`: daraus übernimmt die App ihr Vertrauen `trust:<benutzer>` → `trust2:<handle>`, aber nur bei gleichem
  Identitätsschlüssel (`tests/vectors/chat_v3.json`, `tofu_migrate`). Senden v1 in einen gerade umgestellten Chat: 409
  mit `chat_changed: true` – Chat neu laden.
- **Reaktionen, Bearbeiten, Sprachnachrichten (`x3_ok`):** nur mit der Einstellung `chat_v3` (wie Stufe 3, Standard aus;
  aus: keine Fähigkeit `chat3` in der Föderation, `x3_ok: false`, Endpunkte 409, keine `react`/`edit`/`voice` in
  `features`) und nur, wenn das Chat-Objekt `x3_ok: true` meldet (Format v2,
  angenommen, nicht schreibgeschützt, alle Server mit `chat3`); `GET /api/chat/me` meldet `features` mit `react`, `edit`,
  `voice` und `x3_limits` (`edit_window` 86400, `edit_max` 20, `emoji_max` 32, `react_ct_max`, `react_rate`,
  `voice_max_s` 900, `voice_kbps` 24, `voice_max_bytes`). Formate: `saga/chat_v3.py`, Vektoren `tests/vectors/chat_v3.json`.
  - Reaktion: `PUT /api/chats/{id}/messages/{mid}/reaction {dev, ts, ct, sig}` (eine je Person; neuere `ts` ersetzt,
    `""` = zurücknehmen). `GET …/messages` liefert je Nachricht `rx: [{by, dev, ts, ct, sig}]` (höchstens 50, `by` =
    Handle); `GET /api/chats/{id}/reactions?mids=a,b` → `{reactions: {mid: [...]}}`. Live `chat.react {chat_id, items:
    [{id, by, dev, ts, ct, sig}]}` bzw. `{more: true, mids}` – je Chat höchstens einmal je Sekunde. Fehler: 409 ohne
    `x3_ok`, 404 unbekannte Nachricht (nur `kind: msg`), 400 Signatur/Form, 403 fremdes Gerät, 429 (120 je Minute).
  - Bearbeiten: `POST …/messages` wie eine v2-Nachricht mit `kind: "edit"`, `edit_of` und Inhalt `{"edit": <mid>, "t":
    <text>}` (keine `files`, kein `reply_to`, kein `ai`). 403: fremde Nachricht, älter als 24 h, schon 20-mal, KI-Antwort.
    `GET …/messages` liefert sie am Original als `edit` (vollständige Nachricht mit `key`, `edit_of`), dazu `edited_at`
    und `edit_n`; Live `chat.edited {chat_id, id, seq, edit_seq}`. Löschen des Originals nimmt die Bearbeitung mit.
  - Sprachnachricht: Anhang in `files[]` mit `voice: true, dur, wf` (64 Werte 0–255, B64) und `m: "audio/ogg; codecs=opus"`.
- **Warum keine Reaktionen (`x3_why`, 2.5):** Ist `x3_ok` nicht `true`, sagt das Chat-Objekt (Liste und einzeln) warum;
  Chats v1 melden jetzt auch `x3_ok: false`. Ältere Server: Feld fehlt – dann keinen Hinweis zeigen.
  - `"setting"`: Stufe 3 ist auf dem Server aus (Admin).
  - `"devices"`: lokaler Chat v1, die Umstellung wartet auf ältere Geräte; dazu `x3_wait: {mine: ["Gerätename", …],
    others: n, until: <Unix-Sekunden, Stichtag>}` – Namen nur der EIGENEN Geräte, von anderen nur die Anzahl Personen.
    `mine: []` und `others: 0`: es wartet nichts mehr, die Umstellung kommt beim nächsten stündlichen Lauf.
  - `"server"` (über Servergrenzen): ein beteiligter Server kann `chat3` noch nicht; dazu `x3_hosts: ["host", …]`.
  - `"pending"`: Nachrichtenanfrage noch nicht angenommen (`state` ≠ `ok`).
  - `"readonly"`: nur lesbar (schreibgeschützt, z. B. Verbindung getrennt).
- **Mehrfachauswahl (2.5):**
  - Mehrere eigene Nachrichten für alle löschen: `POST /api/chats/{id}/messages/delete {ids: [mid, …]}` (1–100, sonst
    400) → `{deleted: [mid…], failed: {mid: "not_found" | "not_own" | "error"}}`. Regeln und Live-Ereignisse wie
    `DELETE /api/chats/{id}/messages/{mid}` (je Nachricht `chat.deleted`; Format v2 über das Heim bzw. die Warteschlange).
    404/405 (älterer Server): auf Einzel-`DELETE` zurückfallen.
  - Nur für mich ausblenden (auf allen eigenen Geräten): `POST /api/chats/{id}/hide {ids: [mid, …]}` (1–500) →
    `{hidden: n}` (unbekannte ids zählen nicht, nochmals ausblenden ist harmlos). `GET …/messages` (auch `?after=`) lässt
    sie für mich weg, Ungelesen zählt sie nicht. Live an die eigenen Geräte: `chat.hidden {chat_id, ids}`. Verschwindet
    mit „für mich löschen“ (Chat), Austritt, Löschen der Nachricht/des Chats und dem Konto. 404/405 (älterer Server):
    nur lokal ausblenden, Hinweis „nur auf diesem Gerät“.
  - Weiterleiten und „In Seite übernehmen“ sind reine Client-Funktionen (normaler Sendeweg bzw. `POST /api/notes` +
    `PATCH /api/notes/{id}`); Referenz `static/chat.js` (`selForward`, `selToPage`).
- **Vertrauen sichern (optional, Web ab 2.5):** `GET /api/chat/trust` → `{blob}` (`null`, wenn nichts gesichert),
  `PUT /api/chat/trust {blob}` (höchstens 256 KiB, 404 ohne eingerichtete Chats). `blob` = JSON-Text `{v: 1, nonce, ct}`:
  XChaCha20-Poly1305 mit Schlüssel `generichash(32, LP("saga-chat/v1/trust-key", uid), backup_sk)` und AD
  `LP("saga-chat/v1/trust", uid)`, Klartext `{v: 1, at, items: {"t1:<benutzer>" | "t2:<handle>": {ident, verified, …}}}`
  (TOFU-Stand). Nach einer Wiederherstellung bzw. Kopplung holt der Client ihn vor dem ersten Laden und führt zusammen:
  fehlt ein Eintrag, kommt er dazu; gleicher `ident` → `verified` bleibt erhalten; anderer `ident` → der eigene Stand gilt.
  Neu beginnen verwirft den gesicherten Stand. Referenz: `static/chat-crypto.js` (`sealTrust`/`openTrust`), `static/chat.js`.

## Eigene Push-Verbindung ohne Verteiler (2.5)

UnifiedPush bleibt der Hauptweg. Hat ein Android-Gerät **keinen** Verteiler, hält die App selbst **eine** WebSocket-
Verbindung zum eigenen Server. Für den Server ist das ein weiteres Push-Ziel; Inhalt, Verschlüsselung und Regeln sind
dieselben wie bei Web Push/UnifiedPush mit Schlüsseln.

### Erkennen und anmelden

- `GET /api/push` -> `server.stream: true` und `server.stream_path: "/api/push/stream"` (ältere Server: Feld fehlt –
  dann gibt es den Weg nicht). Der Admin kann ihn abschalten (`stream: false`).
- Anmelden **mit dem Geräte-Schlüssel** (`Authorization: Bearer hkd_…`, nur Apps mit Geräte-Anmeldung):
  `POST /api/push/subscriptions {type: "stream", keys: {p256dh, auth}, name}` -> `201 {id, device, active, stream_path}`
  (erneut: `200`, dasselbe Abo; je Geräte-Anmeldung genau eines, Endpunkt vergibt der Server). Schlüssel wie bei
  UnifiedPush (P-256, 16 Byte auth). Ohne Geräte-Anmeldung `400`, Weg abgeschaltet `403`.
- In der Geräteliste: `type: "stream"`, `where: "Saga (eigene Verbindung)"`, `encrypted: true`, `connected: bool`.
- Mit Namen wie bei Web Push (Ende-zu-Ende verschlüsselt, nur der eigene Server ist beteiligt).
- **Umstieg:** Hat dasselbe Gerät zusätzlich ein `unifiedpush`-Abo, bekommt das `stream`-Abo nichts mehr. Die App
  entfernt ihr `stream`-Abo (`DELETE /api/push/subscriptions/{id}`), sobald UnifiedPush läuft.

### Verbindung `GET /api/push/stream` (WebSocket)

Kopf `Authorization: Bearer hkd_…`. Danach nur Binär-Rahmen, das erste Byte ist die Art:

| Richtung | Rahmen | Bedeutung |
|---|---|---|
| Server -> App | `0x01` + Kennung (8 Byte, big endian) + Körper | Mitteilung; Körper = aes128gcm nach RFC 8291 wie bei Web Push (gleiche Entschlüsselung wie UnifiedPush) |
| App -> Server | `0x02` + Kennung (8 Byte), auch mehrere Kennungen hintereinander | erhalten – der Server löscht sie |
| App -> Server | `0x03` | Lebenszeichen |
| Server -> App | `0x04` | Antwort auf `0x03` |

- Beim Aufbau schickt der Server alles Abgelegte, das noch nicht abgelaufen ist (Kennungen aufsteigend). Unbestätigtes
  kommt beim nächsten Aufbau noch einmal – Doppelte an der Kennung erkennen. Ablage je Abo höchstens 100, Ablauf wie die
  TTL der Mitteilung (normal 4 h, Anruf: solange es klingelt), gleiche `tag` ersetzt eine noch nicht bestätigte.
- **Der Server sendet von sich aus nie etwas** außer Mitteilungen (kein Ping, auch kein WebSocket-Ping). Die App wählt
  den Abstand ihrer Lebenszeichen: so groß, wie NAT und Proxy die ruhende Verbindung überleben lassen. Empfehlung
  (Android-App): Stufen 45 s, 2, 4, 7, 9, 14, 20, 28 min (die unteren nur für Netze/Proxys, die früh trennen),
  Start bei 9 min; nach drei erfolgreichen Lebenszeichen eine Stufe höher, stirbt die ruhende Verbindung (keine Antwort
  auf `0x03` oder Abbruch ohne Netzwechsel), eine Stufe zurück und die höhere für dieses Netz 24 h sperren.
- Echte WebSocket-Pings beantwortet der Server ebenfalls (Protokoll), sie sind aber nicht nötig.
- **Schließen (Code nach dem Aufbau):** `4401` Anmeldung ungültig (Gerät abgemeldet, Konto gesperrt) – nicht neu
  verbinden; `4404` kein `stream`-Abo bzw. Weg abgeschaltet – `GET /api/push` prüfen, ggf. neu anmelden; `4409` eine
  neuere Verbindung desselben Geräts hat übernommen – nicht zurückdrängen; `4429` zu viele Aufbauten (mehr als 12 je
  Minute) – mit Wartezeit; `1012` Server startet neu – nach kurzer, zufälliger Wartezeit neu verbinden.
- Hinter `saga-media` (Container) wird die Verbindung als schlanker Tunnel durchgereicht; ein Reverse Proxy davor muss
  ruhende WebSocket-Verbindungen mindestens 30 min offen lassen (Anleitung, Abschnitt Reverse Proxy).

