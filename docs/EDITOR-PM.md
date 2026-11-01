# Web-Editor: Saga-Schema v1 für Tiptap/ProseMirror

Stand 2.5, Tiptap-Umbau Etappe T1. Das Saga-Format bleibt Block-JSON; ProseMirror sieht nur der Web-Editor. Das
Dokumentmodell für das gleichzeitige Bearbeiten (Yjs) steht in `docs/APP-API-COLLAB.md` Abschnitt 5 und ist in
`saga/collab_model.py` umgesetzt – **das ist die Vorgabe**, der Web-Code folgt ihr.

| Datei | Inhalt |
|---|---|
| `static/pm-schema.js` | `HKPmSchema.extensions(T, opts)` – Knoten, Marken, Inline-Knoten als Tiptap-Erweiterungen, UniqueID-Konfiguration; `VERSION`, `BLOCKS`, `MARKS`, `ATTRS`, `hash()` (= `collab_model.SCHEMA_HASH`) |
| `static/pm-adapter.js` | `HKPmAdapter.docFromBlocks(save, ctx)`, `blocksFromDoc(doc, ctx)`, `bind(pmDoc, save, ctx)`, `htmlToInline`, `inlineToHtml`, `marksToAttrs`/`attrsToMarks` |
| `tests/vectors/pm_schema.json` | gemeinsame Vektoren (erzeugt von `tools/pm_vectors_gen.py`) |
| `tools/pm_vectors.mjs` | Prüfer Web-Adapter + echtes Tiptap-Schema |
| `tests/pm_vectors.py` | Prüfer Server-Modell (pycrdt) + Fixpunkt der Adapter-Ausgabe |
| `tools/pm_ytest.mjs` (+ `tools/pm_ytest.py`) | Gegentest echtes `@tiptap/y-tiptap` gegen pycrdt, Risiko R1 |

Beide Dateien sind ES5-nah ohne Build-Schritt (Browser: `window.HKPmSchema`/`window.HKPmAdapter`, `pm-schema.js` zuerst
laden; Node: `require`). `T` sind die Tiptap-Bausteine: im Browser `window.SagaPM.core` plus `UniqueID:
window.SagaPM.UniqueID`, in Node `require('@tiptap/core')` plus `UniqueID` aus `@tiptap/extension-unique-id`.

## Abbildung

- Knotenname = Saga-Typ (`paragraph`, `header`, `list` …), Attribute exakt wie `collab_model.ATTRS` plus `id`, `style`
  (= `tunes.hkstyle`), `conflict` (= `tunes.hkconflict`), `extra` (unbekannte `data`-Felder/Tunes, intern `absent`).
  Alle Attribute haben den Vorgabewert `null` (y-tiptap schreibt `null` nicht nach Yjs).
- Hilfsknoten: `qtext`/`qcap` (Zitat), `ttitle` (Toggle-Titel), `column`, `litem`/`ltext`, `trow`/`tcell`, `icap`.
  `litem.id` ist intern (aus Block-ID und Position, SHA-256 wie im Server) und steht nie im Block-JSON.
- Gruppen für die Verschachtelungsverbote: `block` (oberste Ebene), `blockNoCols` (in `toggle`, `column`),
  `blockNoSync` (in `synced`). Inhalts-Ausdrücke sind **so weit wie `clean_blocks`** (leere Listen/Tabellen, 1 oder
  mehr als 6 Spalten): Was nicht ins Schema passt, löscht y-tiptap aus dem gemeinsamen Zustand (siehe R1). Grenzen wie
  2–6 Spalten setzt der Editor beim Bearbeiten.
- Marken in Rangfolge `link > color > bg > mark > b > i > u > s > icode`. Inline-Code heißt **`icode`**: ProseMirror
  erlaubt keinen Knoten und keine Marke mit demselben Namen (`code` ist der Code-Block).
- Inline-Knoten `hardBreak`, `mention {u}`, `date {dt, remind, label}`, `math_inline {tex}`, `page {id}` – ohne Text;
  Titel/Namen liefert `ctx.resolve` (`page(bid, id, i)`, `mention(bid, u, i)`, HTML-maskiert).
- Marken **um** Inline-Knoten: im Editor `node.marks`, in Yjs das Attribut `marks` (y-tiptap überträgt `node.marks`
  nicht). Der Adapter liest beides; `marksToAttrs` wandelt für den Weg nach Yjs.

## Normalform

`blocksFromDoc` schreibt Inline-HTML genau wie `collab_model.inline_to_html`: feste Rangfolge außen → innen, gleiche
Marken benachbarter Stücke werden nicht neu geöffnet, `strong`/`em` → `b`/`i`, `<mark>` ohne Klasse → `cdx-marker`,
Listenpunkte als Text → Objekte. Links mit `target="_blank" rel="noopener noreferrer"`, Datum als
`<time datetime="…" class="hk-date|hk-remind">`. Lesen: im Browser über `<template>`, in Node ein kleiner Tokenizer
(`HKPmAdapter.config.tokenizer`); beide ergeben für alle Vektoren dasselbe.

**Durchreichung (T-E4):** `ctx.orig` ist eine `WeakMap` Knoten → Original-Block. `docFromBlocks` füllt sie für die
erzeugten JSON-Knoten, `bind(editor.state.doc, save, ctx)` für die echten Editor-Knoten nach `Node.fromJSON`. Ein
unveränderter Knoten (gleiches Objekt) ergibt das Original-Objekt – byte-gleich, auch wenn es nicht in Normalform ist.
Ein geänderter Toggle/Spalten-Block wird neu geschrieben, seine unveränderten Kinder kommen weiter aus dem Original.

**Altformat Toggle `{text}`:** wie im Server bleibt `text` über `extra` erhalten; mit `ctx.upgrade` wandelt
`docFromBlocks` es wie das bisherige ToggleTool in leeren Titel + ersten Absatz (dieser Block wird nicht durchgereicht).

## Vektoren und Prüfer

Je Fall in `tests/vectors/pm_schema.json`: `blocks` (server-kanonisch), `pm` (`collab_model.doc_to_pm`), `out`
(Ergebnis Block-JSON → Y → Block-JSON mit dem Resolver der Datei, nur wenn ≠ `blocks`), `html_norm` (`[Eingabe,
Normalform]`), `y` (Y-Struktur, eigene Fälle), `upgrade` (`{pm, out}` für Altformat-Toggle). Quellen: `editor_inline.json`
(sanitize), `merge.json`, `gallery/*.json`, eigene Fälle je Knoten/Marke, Altformate, Grenzwerte.

```sh
cd tools/pm-test && npm ci --ignore-scripts && cd ../..   # Test-Pakete (exakt gepinnt, nur hier)
node tools/pm_vectors.mjs                                 # Adapter + Tiptap-Schema
python3 tests/pm_vectors.py                               # Server-Modell, ruft node tools/pm_vectors.mjs --dump
node tools/pm_ytest.mjs                                   # y-tiptap <-> pycrdt (PYTHON=… mit pycrdt)
python3 tools/pm_vectors_gen.py                           # Vektoren neu erzeugen (nach Änderungen am Modell)
```

`tests/pm_vectors.py` braucht weder Server noch Datenbank. Änderungen an Knoten, Attributen oder Marken sind eine neue
Schema-Version (`collab_model.SCHEMA_VER`, Prüfsumme) – danach Vektoren neu erzeugen; `pm_vectors.mjs` prüft
`HKPmSchema.hash()` gegen die Datei.

## Risiko R1 (Schema-Schieflage im Live-Betrieb)

Gemessen mit `@tiptap/y-tiptap` 3.0.12 (`tools/pm_ytest.mjs`, Teil 4):

- **Unbekannter Blocktyp** im Zustand: y-tiptap löscht das Element schon beim Laden (`initProseMirrorDoc`) aus dem
  gemeinsamen Zustand – ein Update, das der Server annimmt; der Block ist für alle weg.
- **Unbekannte Marke** in einem Text: der ganze Text des Blocks wird beim Laden gelöscht.
- **Unbekanntes Attribut** (Block oder Inline-Knoten): bleibt beim Laden, wird beim nächsten Schreiben **dieses**
  Blocks entfernt; andere Blöcke behalten es.

Schutz heute: der Server gibt Clients mit anderer Schema-Prüfsumme nur Lesezugriff (`ro: "schema"`). Der Web-Client
darf dann **nichts** senden – auch nicht die Löschungen, die y-tiptap beim Laden erzeugt. Weitere Gegenmaßnahmen
(Knoten `unknown` statt Löschen, Handshake vor dem Binden) folgen in T5.

## Werkzeuge als Node-Views (T4b)

Undurchsichtige Blöcke (atom-Knoten mit Attribut `data`) zeichnet im Tiptap-Editor ein Saga-Werkzeug. Schnittstelle in
`static/pm-tools.js` (`window.HKTools`), unabhängig von Editor.js und Tiptap:

```js
HKTools.register('widget', {
  title, icon, slash: [{title, icon, data}],     // Einträge fürs "/"-Menü (HKTools.slash())
  atom: true,
  mount(el, data, ctx) {                         // el: Hülle der Node-View, data: Kopie von attrs.data
    return { update(data) { return true; },      // neue Daten von außen (Live, Undo, Rebase); false = neu aufbauen
             destroy() {}, stopEvent(e) { return true; } };
  }
});
```

- **Einbinden (Kern-Editor):** `HKPmSchema.extensions(T, {…, nodeViews: HKTools.nodeViews(base)})`. `base` ist
  optional (Werte oder Funktionen): `readOnly`, `role`, `foreign`, `page`, `replaceWith(ctx, blocks)`,
  `blockToPm(block)`. Ohne Angabe gilt der Zustand der offenen Seite (`HKPages.k.readOnly/cur/foreign`) und
  `editor.isEditable`. Laden: `pm-schema.js`, `pm-adapter.js`, `pm-tools.js`, danach die Gruppen (`pm-tools-daten.js`;
  vor `pages.js` geladen, holt `HKTools.setup` die Registrierung beim ersten `nodeViews()` nach).
- **ctx je Block** (Getter, immer aktuell): `type`, `id`, `readOnly`, `role`, `foreign`, `page`, `editor`, `view`,
  `getPos()`, `node()`, `change(newData[, {history: false}])`, `remove()`, `replaceWith(blocks)`, `insertAfter(blocks)`,
  `select()`, `focusNext()`. `change` schreibt genau **eine** Transaktion (`setNodeMarkup`, Meta `hkTool` = Typ), nichts
  bei gleichem `data` (Vergleich mit sortierten Schlüsseln) oder `readOnly`. Die eigene Änderung kommt nicht als
  `update()` zurück.
- **Hülle:** `div.hk-tool.hk-tool-<typ>[data-block-id][data-block-type][contenteditable=false]`, Blockfarbe als
  `hk-st-*`, `ignoreMutation: true`, `selectNode` → `ProseMirror-selectednode`. `stopEvent`: alles im Werkzeug bleibt
  im Werkzeug (Tasten, Ziehen, Einfügen, Maus); ein Ereignis direkt auf der Hülle gehört ProseMirror (Block wählen).
  `dom.hkTool` ist das Ergebnis von `mount` (Tests/Diagnose).
- **Brücke** `HKTools.fromEditorJs(Klasse, opts)` für die bisherigen Editor.js-Klassen (`HKPages.k.tools`):
  `o = {data, api, block, readOnly, config}`, `block.dispatchChange` und die per `opts.hook` umgebogenen Schreibstellen
  (`S.dirtyBody = true; schedule()`) → `ctx.change(save())`. `opts.known`: Felder, die `save()` kennt – alle anderen
  bleiben erhalten (Regel 0). `opts.carry(base, out)`: Felder nach `save()` übernehmen. `opts.live(inst, data)`: neue
  Daten ohne Neuaufbau; sonst Neuaufbau, verschoben solange ein Eingabefeld im Werkzeug, ein Menü oder der Dialog offen
  ist. Die Brücke ist Übergang bis T7.
- **Daten-Werkzeuge** (`static/pm-tools-daten.js`): `database` (Ansicht, Ansichten, Kopf; neue Zeilen gehen wie bisher
  per API an die Datenbank und ändern das Dokument nicht), `tasks`, `calendar` (Monat/Liste), `widget` (Panel bleibt bei
  `update` offen, Werte vom Server wie bisher), `buttons` (`sig` und unbekannte Felder je Schaltfläche bleiben, solange
  Aktion/Ziel/Vorlage gleich sind; der Server prüft wie bisher in `databases.sign_buttons`). Rollen, fremde Seiten und
  „Nur für den Eigentümer“ entscheiden die Klassen wie im Editor.js-Editor.
- Test: `tests/browser_tiptap_daten.js` (Mini-Editor nur mit Schema + Node-Views auf `/notes`).
