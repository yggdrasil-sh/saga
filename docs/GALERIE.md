# Vorlagengalerie – Dateiformat und Prüfskript (2.5)

Mitgelieferte Vorlagen („Bundles“) aus Seiten und Datenbanken. „Verwenden“ legt alles als Kopie im Konto der Person an;
danach sind es normale Seiten ohne Verbindung zur Galerie. Nicht zu verwechseln mit eigenen Vorlagen (`is_template`,
Datenbank-Vorlagen, `default_tpl`) – Bundles dürfen solche Datenbank-Vorlagen mitbringen.

API für Web und Apps: `docs/APP-API.md` („Vorlagengalerie“). Bedienung: `docs/ANLEITUNG.md`.

## Ablage

- `gallery/<id>.json` – eine Datei je Vorlage (Dateiname = `id`).
- `gallery/i18n/<lang>.json` für `en`, `es`, `ru`, `th`: `{"<deutscher Text>": "<Übersetzung>"}`. Quelle ist Deutsch im Bundle.
- Der Server lädt den Ordner beim Start (`SAGA_GALLERY_DIR`, Standard `gallery/` neben `app.py`). Ungültige Bundles stehen als
  Warnung im Log und werden nicht angeboten. Tests nutzen `SAGA_GALLERY_DIR=tests/gallery_fixtures`.

## Prüfskript

```
python3 tools/gallery_check.py                     # gallery/ prüfen
python3 tools/gallery_check.py tests/gallery_fixtures
python3 tools/gallery_check.py --todo ru           # fehlende Übersetzungen als JSON-Gerüst {"deutsch": ""}
python3 tools/gallery_check.py --no-i18n           # fehlende Übersetzungen nicht als Fehler werten (beim Schreiben)
```

Ohne Datenbank und ohne Konfiguration lauffähig (nur Python 3). Prüft JSON, Pflichtfelder, alle `$ref`-Verweise (unbekannt,
falsche Art, Verweis auf Beispiele), Block-, Eigenschafts-, Ansichts- und Widget-Typen, Werte der Zeilen gegen das Schema,
Grenzen (30 items, 200 Zeilen, 200 000 Zeichen) und die Übersetzungen (fehlend, Platzhalter `{…}` und `$ref` gleich).
Rückgabe 0 = in Ordnung, 1 = Fehler. Was das Skript akzeptiert, lädt auch der Server (gleicher Code: `saga/gallery_format.py`).

## Bundle

```json
{"id": "aufgaben-projekte", "v": 1, "order": 20, "category": "work", "icon": "✅",
 "title": "Aufgaben & Projekte", "desc": "Projekte mit Aufgaben, Board nach Status …",
 "needs": [], "root": "home",
 "items": [ … ]}
```

| Feld | Bedeutung |
|---|---|
| `id` | `[a-z0-9][a-z0-9-]{0,39}`, gleich dem Dateinamen |
| `v` | Formatversion, immer `1` |
| `order` | Sortierung in der Galerie (aufsteigend) |
| `category` | `personal` · `work` · `team` |
| `icon`, `title`, `desc` | Emoji (≤ 8 Zeichen), Titel (≤ 80), Beschreibung (≤ 300) |
| `needs` | optionale Fähigkeiten: `calendar`, `immich`, `nextcloud`, `ai` – Web/Apps zeigen einen Hinweis, angelegt wird trotzdem |
| `root` | `ref` der Startseite (oberste Seite, `parent: null`) – wird nach dem Anlegen geöffnet |
| `items` | 1–30 Seiten/Datenbanken. **Genau ein** Eintrag hat `parent: null`, alle anderen hängen darunter |

### Seite (`kind: "page"`)

`{"ref", "kind": "page", "parent": <ref|null>, "title", "icon", "cover": null, "wide": false, "font": null|"serif"|"mono",
"small": false, "example": false, "body": [Editor.js-Blöcke]}`

- `parent` muss eine Seite sein, die **weiter oben** in `items` steht (keine Seiten in Datenbanken).
- `cover` immer `null` (Titelbilder sind eigene Uploads).

### Datenbank (`kind: "database"`)

`{"ref", "kind": "database", "parent", "title", "icon", "schema": {…}, "templates": […], "rows": […]}`

- `schema` wie `PATCH /api/notes/{id}` `schema`: `props` (feste IDs `[a-z0-9_]{1,24}`, Typen `text number select
  multi_select date checkbox url created edited relation calc`, Optionen mit festen IDs und Farbe `gray brown orange yellow
  green blue purple pink red`), `views` (feste IDs, Typen `table board list gallery calendar chart`, mindestens eine; `chart`
  mit `chart`-Spec wie `docs/APP-API.md` „Diagramme“), optional
  `title_name`, `default_tpl: "$ref:<vorlage>"`. `srev`, `_since` und `pair` setzt der Server.
- Relation: `{"type": "relation", "target": "$ref:<datenbank>", "two_way": true}` – `two_way` legt in der Ziel-Datenbank
  die Gegenspalte an (Name = Titel dieser Datenbank).
- `templates`: Eintrags-Vorlagen `{"ref", "title", "icon", "props", "body", "default": true}` (höchstens eine `default`;
  setzt `default_tpl`, falls das Schema keinen hat).
- `rows`: Zeilen `{"ref"?, "title", "icon", "props": {"<prop-id>": Wert}, "body", "example": true}`. `ref` nur nötig, wenn
  eine Relation darauf zeigt. Werte wie in der API: Auswahl = Options-ID, Mehrfachauswahl = Liste, Datum `JJJJ-MM-TT`,
  Kontrollkästchen `true`, Relation = Liste aus `"$ref:<zeile>"`.

### Verweise `$ref`

- Ein String, der **ganz** `"$ref:<ref>"` ist, wird beim Anlegen durch die ID ersetzt (als Zahl): Relationsziel und -werte,
  `default_tpl`, Datenbank-Block `db`, Widget `cfg.db`, Schaltflächen `target`/`tpl`.
- **Im Text** wird `$ref:<ref>` durch die Ziffern ersetzt – für Seitenverweise: `<a href="/notes#$ref:tasks">Aufgaben</a>`.
- Unbekannte refs machen das Bundle ungültig. Nicht-Beispiele dürfen nicht auf Beispiele verweisen (außer in
  Relationswerten – die fallen ohne Beispiele weg).

### Blöcke

Erlaubt: `paragraph header list quote code delimiter table toggle callout columns buttons database tasks calendar widget
embed` (alle Daten wie im Editor, Gestaltung laut `docs/APP-API.md` „Seite gestalten“ und „Widgets in Seiten“).
Nicht erlaubt: `image attaches immich nextcloud` (brauchen eigene Dateien).

- Datenbank-Block: `{"type": "database", "data": {"db": "$ref:tasks", "view": "board"}}`
- Schaltfläche „Neuer Eintrag“: `{"label", "icon", "action": "db", "target": "$ref:tasks", "tpl": "$ref:tpl_task",
  "title": "Aufgabe {datum}", "open": "side"}` – der Server signiert sie beim Anlegen. `action: "page"` mit `target`.
- Widgets: `cfg.db: "$ref:<datenbank>"`, `view`/`prop` sind die festen IDs aus dem Schema. Widget-Blöcke brauchen eine `id`.
  Kennzahl/Fortschritt mit `cfg.src: "tasks"` (Saga-Aufgaben der betrachtenden Person) brauchen kein `db`.
  2.5 Paket 4: `habit` (`cfg.date`, `cfg.habits` = Häkchen-IDs) und `chart` (`cfg.x`/`y`/`series`) brauchen `cfg.db`;
  `weather` ohne Koordinaten zeigt „noch nicht eingerichtet“; `upcoming` ohne `dbs` (persönliche Quellen).

### Beispieldaten

Seiten und Zeilen mit `"example": true` werden nur angelegt, wenn die Person „Mit Beispielen“ wählt (Standard an).
Unterseiten eines Beispiels müssen selbst Beispiele sein. Auch einzelne Blöcke (auch in Spalten/Toggles) können
`"example": true` tragen (z. B. Beispiel-Stichpunkte) – ohne Beispiele fallen sie weg; das Feld selbst wird nie
gespeichert.

### Übersetzungen

Übersetzt werden Titel, `desc`, Eigenschafts-, Options- und Ansichtsnamen, `title_name` und in Blöcken alle Texte unter
den Schlüsseln `text title caption content label items message` (Absätze, Überschriften, Listen, Tabellen, Hinweise,
Toggles, Schaltflächen-Beschriftung und -Titel, Widget-Titel, Zitat-Texte), die Namen eingebetteter Ansichten eines
`database`-Blocks (`data.views[].name`) und die Werte von Text-Eigenschaften (`text`) in Zeilen und Eintrags-Vorlagen.
Gleicher deutscher Text = eine Übersetzung.
Angelegt wird in der Sprache der Person (`?lang=`, sonst die Sprache der Anfrage); fehlt eine Übersetzung, bleibt Deutsch.
Platzhalter wie `{datum}` (Schaltflächen-Titel) und `$ref:` im Text müssen in der Übersetzung gleich bleiben.
