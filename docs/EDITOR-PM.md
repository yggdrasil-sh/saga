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
  bleiben erhalten (Regel 0). `opts.carry(base, out)`: Felder nach `save()` übernehmen. `opts.meta(base, out)`: Optionen für `ctx.change` (z. B.
  `{history: false}` für den einmaligen Ansichten-Nachtrag alter DB-Einbettungen). `opts.live(inst, data)`: neue
  Daten ohne Neuaufbau; sonst Neuaufbau, verschoben solange ein Eingabefeld im Werkzeug, ein Menü oder der Dialog offen
  ist. Die Brücke ist Übergang bis T7.
- **Daten-Werkzeuge** (`static/pm-tools-daten.js`): `database` (Ansicht, Ansichten, Kopf; neue Zeilen gehen wie bisher
  per API an die Datenbank und ändern das Dokument nicht), `tasks`, `calendar` (Monat/Liste), `widget` (Panel bleibt bei
  `update` offen, Werte vom Server wie bisher), `buttons` (`sig` und unbekannte Felder je Schaltfläche bleiben, solange
  Aktion/Ziel/Vorlage gleich sind; der Server prüft wie bisher in `databases.sign_buttons`). Rollen, fremde Seiten und
  „Nur für den Eigentümer“ entscheiden die Klassen wie im Editor.js-Editor.
- Test: `tests/browser_tiptap_daten.js` (Mini-Editor nur mit Schema + Node-Views auf `/notes`).

## Kern-Editor (T2)

`static/pm-editor.js` (+ `static/pm-editor.css`) ist der Tiptap-Editor für Seiten. Er ist nur aktiv mit
`<body data-editor="tiptap">`; das setzt der Server aus der Admin-Einstellung `editor.engine` (`editorjs` Standard |
`tiptap` | `choice`) und bei `choice` aus der Konto-Wahl (`user_kv` `editor_tiptap`, `GET/PUT /api/account/editor`,
Einstellungen → Seiten) – `saga.core.editor_engine(uid)`. Wechsel gilt ab dem nächsten Laden. `/api/version` meldet `pm1`.

**Aufbau:** `HKPmEditor.load()` lädt Bundle, `pm-schema.js`, `pm-adapter.js`, `pm-tools.js`, `pm-tools-daten.js` und
die CSS-Dateien. `HKPmEditor.create(cb)` gibt `pages.js` ein Objekt an Stelle der Editor.js-Instanz: `render(data)`
(Editor je Seite neu, Durchreichung per `bind`), `save()` (`blocksFromDoc`), `applyBlocks(blocks)` (Rebase/Live Stufe 1:
gleicher Anfang/gleiches Ende bleiben dieselben Knoten, eine Transaktion mit `addToHistory:false`, Meta `hkRemote`),
`setEditable`, `caret`, `text()` (Entwurf bei Konflikt), `focusBlock()` (Anwesenheit), `styleAt()` (Blockfarbe).
`pages.js` behält Speichern, Warteschlange, `merge:1`/`base:"sent"`, `mergedReply`, `?since`-Delta, Offline und
Konfliktleiste unverändert; Änderungen meldet der Editor als Transaktion (`cb.changed(ids)`, beim Laden ignoriert).

**Fassade** `HKPages.k.doc` mit `kind: 'tiptap'` – Semantik wie `static/editor-doc.js`. Blöcke in Spalten, Toggles
und synchronisierten Blöcken stehen mit `parent`/`slot` in `blocks()`/`byId()` (ab T3), Blöcke in Karten fehlen;
Mutationen sind synchron. `select(ids)` ist eine Dekoration (`.pm-sel`). Zusätzlich `menu(id)` (Block-Menü) und
`undo()`. `findId` und die Schutzbereiche sind je Dokumentstand gecacht (WeakMap).

**Node-Views:** Jedes Blockelement ist ein äußeres `div.pm-blk[data-block-id][data-block-type]` mit innerem
Inhaltselement. Mutationen außerhalb des Inhalts (Kommentarzahl, Anwesenheit `data-hk-by`, Klassen) ignoriert
ProseMirror – deshalb dürfen `comments.js`/`live.js` weiter am Blockelement schreiben. Listen: `ul|ol.pm-list` >
`li.pm-li` > `div.pm-li-c` (Zähler per CSS, Startwert per CSSOM `counter-reset`), Häkchen `button.pm-check`.

**Karten:** Alle Blocktypen außer `paragraph, header, callout, quote, list, delimiter` und den Werkzeugen aus
`HKTools.nodeViews` zeigen eine Karte („im klassischen Editor bearbeiten“). Das Plugin `hkGuard` lehnt jede Transaktion
ab, die ins Innere einer Karte greift (auch Marken-Schritte); eine Schreibmarke darin wird zur Knotenauswahl. Ganze
Karten lassen sich löschen/verschieben.

**Bedienung:** Formatierungsleiste (BubbleMenu, E8), Vorschlagsmenüs über `Suggestion` (`/` mit Registry
`HKPmEditor.slash.register({key, group, t, ic, desc, kw, kbd, block|run})` plus `HKTools.slash()`, `@` mit
`HKKeys.atOptions` + `HKMention.options`, `[[`, `:` mit `HKKeys.emoji`), Markdown-Regeln (E5), eigener Platzhalter
(Dekoration `data-ph`, E7), Listen-Tasten, `Shift+Enter` = Zeilenumbruch, Rückschritt/Entf neben Karten wählt die
Karte. `TrailingNode` ist bewusst **nicht** aktiv (würde beim Laden einen Absatz anhängen = Scheinänderung); dafür
Klickfläche unter dem letzten Block und ein leerer Absatz auf leeren Seiten.

**Tests:** `SAGA_EDITOR=tiptap|editorjs` (`tests/lib/editor_dom.js`: `EDITOR`, `UI` je Editor, `setEditor`, `isEditor`;
der Server braucht `editor.engine = choice`). Abnahme: `tests/browser_tiptap_kern.js`.

## Struktur (T3)

`static/pm-struktur.js` meldet sich beim Kern mit `HKPmEditor.use(fn)` an. `fn(X)` bekommt die internen Bausteine
(`X.walk`, `findId`, `blockAt`, `convertAt`, `setSel`, `select` …, siehe Ende von `pm-editor.js`) und liefert
`{edit, nodeViews, plugins(editor), keys(editor), protect(node), api}`: `edit` macht Typen bearbeitbar (keine Karte),
`keys` laufen vor den Kern-Tasten, `protect` schützt Knoten wie eine Karte (synchronisierte Kopie nur lesend), `api`
ist über `HKPmEditor.api(name)` erreichbar (`savedReply` für `mention.js`, `selectBlocks`/`deleteSel`/`dupSel` für
die Auswahl-Leiste in `editor25.js`, `openBlockMenu`, `moveBlock` …).

- **Spalten** `columns > column{2,6} > Blöcke` (Schema erlaubt `column*`, die Grenzen setzt der Editor). Darstellung
  mit den Klassen von Editor.js (`.cols`, `--cols`, `--cols-t` per CSSOM), Breitengriffe `.pm-col-rs` und Leiste
  `.cols-ctl` liegen außerhalb des Inhalts. Ein Zug = eine Transaktion mit allen `w` (gleich breit = kein `w`).
  Aufräumen als `appendTransaction` (gleicher Undo-Schritt): Spalten, die **in dieser Änderung** leer wurden, entfallen;
  eine übrige Spalte löst den Block auf. Geladene Seiten (auch mit 1 Spalte oder leeren Spalten) bleiben unberührt.
- **Toggle** `toggle(ttitle, Blöcke)`, `open` im Dokument (T-E6 A): geschrieben nur beim eigenen Klick, ohne
  Undo-Schritt; Lesen/gesperrt klappt nur lokal. Zugeklappt verbirgt CSS den Inhalt; die Schreibmarke springt über ihn.
  Enter im Titel → erster Inhalt (öffnet), Enter auf leerem letzten Absatz → hinter den Toggle, Rückschritt am
  Titelanfang → Absatz (ID bleibt) + Inhalt dahinter. Umwandeln mit Inhalt wird verweigert. Altformat `{text}`:
  `docFromBlocks(…, {upgrade:true})` zeigt den Text als ersten Absatz, `bind` ohne upgrade hält den Toggle am
  Original → unbearbeitet byte-gleich, bearbeitet im neuen Format.
- **Synchronisiert:** Original `{sid, blocks}` und Kopie mit vom Server eingesetztem Inhalt sind beide der Knoten
  `synced` (Unterscheidung über `data.src`, im Modell `extra.data.src`); die Kopie wird wie bisher beim Speichern
  über `sync1` ins Original geschrieben, `savedReply` übernimmt `synced_rev` als neue Basis (`src.rev`, ohne
  Änderungsmeldung). Nur lesend (Lesen/Kommentieren auf dem Original, kein Zugriff) = geschützt wie eine Karte. Kopie
  ohne Inhalt (frisch eingefügt, nicht gefunden, kein Zugriff) = `synced_ref` mit Hinweis; nach dem Speichern lädt
  die Seite neu. Einfügen einer Kopie über `application/x-editor-js` `[{tool:'synced', data:{sid}}]`.
- **Konfliktblock:** Attribut `conflict` → Widget-Dekoration `.pm-cf-bar` über dem Block. Übernehmen = Original
  (`conflict.of`) löschen + Attribut leeren, Verwerfen = Block löschen, je eine Transaktion.
- **Griffe/Ziehen:** eigene Zeigerlogik statt `SagaPM.DragHandle` (Rand-Drop, Ziellinien, Geisterbild und langer Druck
  am Telefon brauchen eigene Ziele): `.pm-handles` mit `.pm-handle-plus`/`.pm-handle-menu` in `.pm-holder`. Ziele:
  zwischen Blöcken (waagrechte Linie), untere Titelhälfte eines offenen Toggles (erster Inhalt), leere Spalte, Rand
  (40 px) eines Blocks der obersten Ebene → neue Spalten, Rand eines Spalten-Blocks → neue Spalte (bis 6, Breiten
  gleich). Unzulässige Ziele (Spalten in Spalten …) prüft `canReplace` am Schema. Ein Drop = eine Transaktion.
- **Mehrfachauswahl** (T-E5 A): Zustand = Fassaden-Auswahl (`hkBlockSel`). Esc, ⇧↑/↓ (Anker/Kopf in
  Dokumentreihenfolge, zugeklappte Inhalte zählen nicht), Ziehen im Rand (Rahmen `.pm-band`), Text-Auswahl über
  Blockgrenzen → Blockauswahl (nach dem Loslassen), ⌘A zweimal, Rückschritt/Entf, ⌘D, ⌘C/⌘X (HTML nach Schema +
  `application/x-editor-js`), Enter = bearbeiten. ⌘⇧↑/↓ (`keys.js` → `move`) verschiebt Block oder Auswahl auch in die
  Nachbarspalte bzw. aus Toggle/Spalten heraus. Die Leiste (`editor25.js`) nimmt alle gewählten Wurzeln und wandelt je
  zusammenhängender Gruppe um.

Test: `tests/browser_tiptap_struktur.js`.
