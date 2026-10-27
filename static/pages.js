// Saga - Seiten (Notizen im Notion-Stil) auf Basis von Editor.js
// Wird von notes.js erst beim Oeffnen des Notizen-Tabs nachgeladen.
// Alle Benutzerinhalte im Baum/Suche per textContent; Block-Inhalte bereinigt der Server.
(function () {
  'use strict';
  var HK = window.HK, $ = HK.$, el = HK.el, api = HK.api;

  // Editor.js markiert beim Link-Einfuegen die Auswahl mit execCommand('backColor') -> <span style="..."> -> CSP-Fehler
  // "Refused to apply inline style". Ersatz ohne Inline-Style: CSS Custom Highlight API (::highlight(hk-sel), style.css).
  (function () {
    var orig = document.execCommand;
    if (!orig || document.execCommand.hkPatched) return;
    document.execCommand = function (cmd, ui, val) {
      if (String(cmd).toLowerCase() !== 'backcolor') return orig.apply(document, arguments);
      try {
        if (window.CSS && CSS.highlights && window.Highlight) {
          var sel = window.getSelection();
          if (val && val !== 'transparent' && sel && sel.rangeCount) CSS.highlights.set('hk-sel', new window.Highlight(sel.getRangeAt(0).cloneRange()));
          else CSS.highlights.delete('hk-sel');
        }
      } catch (e) { /* nur Optik */ }
      return true;
    };
    document.execCommand.hkPatched = true;
  })();

  var I18N = { messages: {
    ui: {
      blockTunes: { toggler: { 'Click to tune': T('Optionen'), 'or drag to move': T('oder ziehen zum Verschieben') } },
      inlineToolbar: { converter: { 'Convert to': T('Umwandeln in') } },
      toolbar: { toolbox: { Add: T('Block hinzufügen') } },
      popover: { Filter: T('Filtern …'), 'Nothing found': T('Nichts gefunden'), 'Convert to': T('Umwandeln in') }
    },
    // Block-Menue filtert nur nach dem Namen -> "Aufzählungsliste" (wie Notion), damit "Liste" auch die Aufzaehlung findet
    toolNames: { Text: T('Text'), Heading: T('Überschrift'), 'Unordered List': T('Aufzählungsliste'), 'Ordered List': T('Nummerierte Liste'),
      Checklist: T('Checkliste'), Quote: T('Zitat'), Code: T('Code'), Delimiter: T('Trennlinie'), Bold: T('Fett'), Italic: T('Kursiv'),
      Link: T('Link'), Marker: T('Markieren'), InlineCode: T('Code'), 'Inline Code': T('Code'),
      Table: T('Tabelle'), Image: T('Bild'), Attachment: T('Datei'), Attaches: T('Datei') },
    tools: {
      link: { 'Add a link': T('Link einfügen') },
      header: { 'Heading 1': T('Überschrift 1'), 'Heading 2': T('Überschrift 2'), 'Heading 3': T('Überschrift 3'), 'Heading 4': T('Überschrift 4') },
      list: { Unordered: T('Aufzählung'), Ordered: T('Nummeriert'), Checklist: T('Checkliste'), 'Start with': T('Beginnt mit'), 'Counter type': T('Zählweise') },
      quote: { 'Align Left': T('Linksbündig'), 'Align Center': T('Zentriert') },
      table: { 'With headings': T('Mit Kopfzeile'), 'Without headings': T('Ohne Kopfzeile'), 'Add column to left': T('Spalte links einfügen'),
        'Add column to right': T('Spalte rechts einfügen'), 'Delete column': T('Spalte löschen'), 'Add row above': T('Zeile oberhalb einfügen'),
        'Add row below': T('Zeile unterhalb einfügen'), 'Delete row': T('Zeile löschen') },
      image: { Caption: T('Bildunterschrift'), 'Select an Image': T('Bild auswählen'), 'With border': T('Mit Rahmen'),
        'Stretch image': T('Volle Breite'), 'With background': T('Mit Hintergrund') },
      attaches: { 'Select file to upload': T('Datei auswählen'), 'File title': T('Titel') }
    },
    blockTunes: {
      delete: { Delete: T('Löschen'), 'Click to delete': T('Zum Löschen erneut klicken') },
      moveUp: { 'Move up': T('Nach oben') }, moveDown: { 'Move down': T('Nach unten') }
    }
  } };

  // --- Editor.js nachladen ---------------------------------------------------
  var V = document.body.dataset.v || '';
  var VENDOR = ['editorjs.umd.js', 'header.umd.js', 'editorjs-list.umd.js', 'quote.umd.js',
                'code.umd.js', 'delimiter.umd.js', 'inline-code.umd.js', 'marker.umd.js',
                'table.umd.js', 'image.umd.js', 'attaches.umd.js', 'drag-drop.js'];
  // async=false: alle Skripte laden parallel, ausgefuehrt werden sie trotzdem in dieser Reihenfolge (Bughunt P04 -
  // vorher wurde jedes erst nach dem onload des vorigen eingefuegt: 12 Rundreisen nacheinander)
  function loadScript(src) {
    return new Promise(function (ok, fail) {
      var s = document.createElement('script'); s.async = false; s.src = src; s.onload = ok;
      s.onerror = function () { fail(new Error(T('Laden fehlgeschlagen: {src}', { src: src }))); };
      document.body.appendChild(s);
    });
  }
  var vendorReady = null;
  function loadVendor() {
    if (vendorReady) return vendorReady;
    // CSS als Datei (CSP-konform). Die ids sorgen dafuer, dass Editor.js selbst KEINE <style>-Tags injiziert.
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.id = 'editor-js-styles';
    l.href = '/static/vendor/editorjs/editorjs.css?v=' + V; document.head.appendChild(l);
    var m = document.createElement('meta'); m.id = 'codex-tooltips-style'; document.head.appendChild(m);
    vendorReady = Promise.all(VENDOR.map(function (f) { return loadScript('/static/vendor/editorjs/' + f + '?v=' + V); }));
    return vendorReady;
  }

  // --- Markdown-Kuerzel beim Tippen (wie in Notion) --------------------------
  // "# ", "## ", "### ", "- ", "* ", "1. ", "[] ", "> ", "```", "---" am Anfang eines Absatzes
  var SHORTCUTS = [
    [/^#\s/, function (r) { return ['header', { text: r, level: 1 }]; }],
    [/^##\s/, function (r) { return ['header', { text: r, level: 2 }]; }],
    [/^###\s/, function (r) { return ['header', { text: r, level: 3 }]; }],
    [/^####\s/, function (r) { return ['header', { text: r, level: 4 }]; }],
    [/^[-*]\s/, function (r) { return ['list', { style: 'unordered', meta: {}, items: [{ content: r, meta: {}, items: [] }] }]; }],
    [/^1[.)]\s/, function (r) { return ['list', { style: 'ordered', meta: {}, items: [{ content: r, meta: {}, items: [] }] }]; }],
    [/^\[\s?\]\s/, function (r) { return ['list', { style: 'checklist', meta: {}, items: [{ content: r, meta: { checked: false }, items: [] }] }]; }],
    [/^>\s/, function (r) { return ['quote', { text: r, caption: '', alignment: 'left' }]; }],
    [/^```$/, function () { return ['code', { code: '' }]; }],
    [/^---$/, function () { return ['delimiter', {}]; }]
  ];
  function tryShortcut(editor) {
    var i = editor.blocks.getCurrentBlockIndex();
    var blk = i >= 0 && editor.blocks.getBlockByIndex(i);
    if (!blk || blk.name !== 'paragraph') return;
    var node = blk.holder.querySelector('.ce-paragraph');
    if (!node) return;
    var txt = node.textContent.replace(/\u00a0/g, ' ');
    for (var k = 0; k < SHORTCUTS.length; k++) {
      var m = txt.match(SHORTCUTS[k][0]);
      if (!m) continue;
      var rest = HK.escapeHtml(txt.slice(m[0].length));
      var res = SHORTCUTS[k][1](rest);
      editor.blocks.insert(res[0], res[1], undefined, i, true, true);   // Absatz ersetzen
      if (res[0] === 'delimiter') {                                      // nach Trennlinie weiterschreiben
        editor.blocks.insert('paragraph', { text: '' }, undefined, i + 1, true);
        editor.caret.setToBlock(i + 1, 'start');
      } else {
        editor.caret.setToBlock(i, 'end');
      }
      return;
    }
  }

  // --- Block-Menue ("/") ---------------------------------------------------------
  // Editor.js uebernimmt im gefilterten Menue mit Enter nur einen per Pfeiltaste markierten Eintrag; getippt + Enter
  // blieb wirkungslos und weiterer Text landete im Filter. Ohne Markierung -> erster sichtbarer Treffer.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains('cdx-search-field__input')) return;
    var pop = t.closest('.ce-popover');
    if (!pop || !pop.classList.contains('ce-popover--opened') || pop.querySelector('.ce-popover-item--focused')) return;
    if (slashColorPick(slashSrc || slashP, t.value)) { e.preventDefault(); e.stopPropagation(); return; }     // 2.5 (G3)
    // 2.4 (Testrunde W7): Editor.js filtert verzoegert - bei schnellem "/Hinweis⏎" ist noch nichts ausgeblendet. Daher
    // selbst nach dem Suchtext abgleichen (Titel beginnt mit / enthaelt ihn), erst sonst der erste sichtbare Eintrag.
    var first = slashMatch(pop, t.value) || [].filter.call(pop.querySelectorAll('.ce-popover-item'), function (x) {
      return !x.classList.contains('ce-popover-item--hidden') && !x.classList.contains('ce-popover-item--disabled') && x.getClientRects().length;
    })[0];
    if (!first) return;
    e.preventDefault(); e.stopPropagation();
    first.click();
  }, true);
  // Eintrag des Block-Menues zum Suchtext q: Titel beginnt mit q, sonst enthaelt ihn, sonst Werkzeugname; null = keiner
  function slashMatch(pop, q) {
    q = String(q || '').replace(/\u00a0/g, ' ').trim().toLowerCase();
    if (!q || !pop) return null;
    var all = [].filter.call(pop.querySelectorAll('.ce-popover-item'), function (x) { return !x.classList.contains('ce-popover-item--disabled'); });
    function ti(x) { var e = x.querySelector('.ce-popover-item__title'); return e ? e.textContent.trim().toLowerCase() : ''; }
    return all.filter(function (x) { return ti(x).indexOf(q) === 0; })[0] || all.filter(function (x) { return ti(x).indexOf(q) >= 0; })[0]
      || all.filter(function (x) { return String(x.getAttribute('data-item-name') || '').toLowerCase().indexOf(q) === 0; })[0] || null;
  }
  // Schnell getipptes "/Hinweis⏎" auf dem Desktop: der Text landete noch im Absatz (Menue war noch nicht offen bzw. hatte
  // das Suchfeld noch nicht) -> Enter waehlt den passenden Block, statt "/Hinweis" als Text stehen zu lassen.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' || e.isComposing || e.shiftKey || e.altKey || e.ctrlKey || e.metaKey || mobilePop()) return;
    var t = e.target, p = t && t.closest ? t.closest('.ce-paragraph') : null;
    if (!p || !S.editor || !$('pg-editor').contains(p)) return;
    var cm = /^\/(.{2,40})$/.exec(slashText(p).trim());
    if (cm && (slashPopOpen() || p === slashSrc) && slashColor(cm[1])) {           // "/rot", "/rot hintergrund" (2.5, G3)
      e.preventDefault(); e.stopPropagation(); slashColorPick(p, cm[1]); return;
    }
    var m = /^\/([^\s\/]{1,40})$/.exec(slashText(p).trim()), pop = slashPopOpen();
    if (!m || (!pop && p !== slashSrc)) return;              // nur wenn das Menue offen ist bzw. "/" in einem leeren Absatz begann
    var it = pop ? slashMatch(pop, m[1]) : null;
    if (pop && !it) return;                                  // kein passender Block: normales Enter (Text bleibt Text)
    e.preventDefault(); e.stopPropagation();
    if (it) { p.innerHTML = ''; slashReset(); slashSrc = null; it.click(); return; }
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(p) && (!best || best.holder.contains(x.holder))) best = x; });
    var ed = best ? best.ed : S.editor, q = m[1];
    try { ed.toolbar.toggleToolbox(true); } catch (err) { return; }
    (function pick(n) {                                      // Menue geht gleich auf: dann den passenden Block waehlen
      var pp = slashPopOpen(), x = slashMatch(pp, q);
      if (x) { p.innerHTML = ''; slashReset(); slashSrc = null; x.click(); return; }
      if (n > 0) { setTimeout(function () { pick(n - 1); }, 40); return; }
      try { ed.toolbar.close(); } catch (err) { /* egal */ }
    })(8);
  }, true);
  // Handy-Tastaturen melden beim Tastendruck oft key "Unidentified" -> Editor.js erkennt "/" nicht.
  // Ersatz: "/" allein in einem leeren Absatz (input-Ereignis) oeffnet das Block-Menue dieses Editors.
  document.addEventListener('input', function (e) {
    if (e.data !== '/' || !S.editor || document.querySelector('.ce-popover--opened')) return;
    var t = e.target, p = t && t.closest ? t.closest('.ce-paragraph') : null;
    if (!p || !$('pg-editor').contains(p) || p.textContent.replace(/\u00a0/g, ' ').trim() !== '/') return;
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(p) && (!best || best.holder.contains(x.holder))) best = x; });
    var ed = best ? best.ed : S.editor;
    slashReset(); slashP = p; slashSrc = p;      // Absatz war vor dem "/" leer -> darf beim Abbrechen geleert werden
    setTimeout(function () { try { ed.toolbar.toggleToolbox(true); } catch (err) { /* Editor noch nicht bereit */ } }, 0);
  }, true);
  // Handy (Editor.js unter 650 px): das Block-Menue ist ein Bottom-Sheet OHNE Suchfeld, Getipptes landet im Absatz.
  // Ersatz: solange das Menue offen ist und der Absatz mit "/" beginnt, filtert der Text dahinter die Eintraege;
  // Enter waehlt den ersten Treffer. Vor der Auswahl wird "/…" geleert, damit der Block ersetzt statt ergaenzt wird.
  var slashP = null;
  function mobilePop() {
    var pop = document.querySelector('.ce-popover--opened');
    if (!pop || pop.querySelector('.cdx-search-field') || !pop.querySelector('.ce-popover__overlay:not(.ce-popover__overlay--hidden)')) return null;
    return pop;
  }
  function slashText(p) { return p && p.isConnected ? p.textContent.replace(/\u00a0/g, ' ') : ''; }
  function slashReset() {
    [].forEach.call(document.querySelectorAll('.pg-slash-hidden'), function (x) { x.classList.remove('pg-slash-hidden'); });
    slashP = null;
  }
  function slashItems(pop) {
    return [].filter.call(pop.querySelectorAll('.ce-popover-item'), function (x) {
      return !x.classList.contains('pg-slash-hidden') && !x.classList.contains('ce-popover-item--hidden') && !x.classList.contains('ce-popover-item--disabled');
    });
  }
  function slashPick(pop) {
    var first = slashItems(pop)[0];
    if (!first) return false;
    if (slashText(slashP).charAt(0) === '/') slashP.innerHTML = '';
    slashReset(); first.click();
    return true;
  }
  document.addEventListener('input', function (e) {
    var pop = mobilePop(), t = e.target, p = t && t.closest ? t.closest('.ce-paragraph') : null;
    if (!pop || !p) return;
    var txt = slashText(p);
    if (txt.charAt(0) !== '/') return;
    slashP = p;
    var q = txt.slice(1).trim().toLowerCase();
    [].forEach.call(pop.querySelectorAll('.ce-popover-item'), function (x) {
      var ti = x.querySelector('.ce-popover-item__title');
      x.classList.toggle('pg-slash-hidden', !!q && !(ti && ti.textContent.toLowerCase().indexOf(q) >= 0));
    });
  }, true);
  // Farbe per Slash-Kuerzel: "/…" entfernen, Menue schliessen, Block (den Absatz mit dem "/") einfaerben
  function slashColorPick(p, q) {
    var st = slashColor(q);
    if (!st || !p || !p.isConnected) return false;
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(p) && (!best || best.holder.contains(x.holder))) best = x; });
    var ed = best ? best.ed : S.editor;
    if (slashText(p).charAt(0) === '/') p.innerHTML = '';
    slashReset(); slashSrc = null;
    try { ed.toolbar.close(); } catch (err) { /* schon zu */ }
    if (!styleAt(p, st)) return true;
    HK.toast(T('Farbe: {farbe}', { farbe: st.bg ? T('{farbe} (Hintergrund)', { farbe: COLOR_NAMES[st.c] }) : COLOR_NAMES[st.c] }));
    setTimeout(function () { if (p.isConnected) { p.focus(); } }, 0);
    return true;
  }
  function slashEnter(e) {
    var pop = mobilePop();
    if (pop && slashP && slashP.contains(e.target) && slashText(slashP).charAt(0) === '/' && slashColorPick(slashP, slashText(slashP).slice(1))) {
      e.preventDefault(); e.stopPropagation(); return;
    }
    if (!pop || !slashP || slashText(slashP).charAt(0) !== '/' || !slashP.contains(e.target)) return;
    if (slashPick(pop)) { e.preventDefault(); e.stopPropagation(); }
  }
  document.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.isComposing && !e.shiftKey) slashEnter(e); }, true);
  document.addEventListener('beforeinput', function (e) { if (e.inputType === 'insertParagraph' || e.inputType === 'insertLineBreak') slashEnter(e); }, true);
  // Auswahl per Antippen: "/…" vorher leeren; Menue ueber "+" oder Overlay geschlossen -> Filter zuruecksetzen
  document.addEventListener('pointerdown', function (e) {
    var t = e.target;
    if (!t || !t.closest) return;
    var item = t.closest('.ce-popover-item'), pop = mobilePop();
    if (item && pop && pop.contains(item) && slashP && slashText(slashP).charAt(0) === '/' && slashText(slashP).length > 1) slashP.innerHTML = '';
    else if (t.closest('.ce-popover__overlay') && slashPopOpen()) slashUndo();      // Menue ohne Auswahl zu: "/…" weg
    else if (t.closest('.ce-toolbar__plus') || t.closest('.ce-popover__overlay')) slashReset();
  }, true);
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t && t.closest && t.closest('.ce-popover-item') && document.querySelector('.pg-slash-hidden')) setTimeout(slashReset, 0);
  }, true);
  // Menue ohne Auswahl geschlossen (Escape, auf dem Handy Tipp neben das Menue): "/" und den getippten Filter wieder
  // entfernen, sonst bleibt "/…" als Text stehen. Editor.js oeffnet das Menue per "/" nur in einem leeren Absatz -
  // dessen Inhalt war also nur "/…".
  var slashSrc = null;
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target, p = t && t.closest ? t.closest('.ce-paragraph') : null;
    if (t && t.closest && t.closest('.ce-popover')) return;        // "/" im Suchfeld des Menues: Herkunft behalten
    slashSrc = p && $('pg-editor').contains(p) && !slashText(p).trim() ? p : null;
    if (slashSrc) setTimeout(function () { if (!slashPopOpen()) slashSrc = null; }, 150);   // Menue ging nicht auf -> normaler Text
  }, true);
  function slashUndo() {
    // Nur den Absatz leeren, in dem das Menue per "/" in einem LEEREN Absatz aufging (slashSrc). slashP allein reicht
    // nicht: der Handy-Filter setzt es fuer jeden Absatz, der mit "/" beginnt (z. B. "/usr/bin …") -> echter Text.
    var p = slashSrc, txt = slashText(p);
    // Handy: Filter steht im Absatz ("/…"); Desktop: Filter im Suchfeld des Menues, im Absatz nur "/"
    var mine = p && p === slashP ? txt.charAt(0) === '/' : txt.trim() === '/';
    slashReset(); slashSrc = null;
    if (!p || !p.isConnected || !mine) return;
    p.innerHTML = '';
    setTimeout(function () {                    // nach dem Schliessen: Cursor zurueck in den (jetzt leeren) Absatz
      if (!p.isConnected) return;
      p.focus();
      try { var r = document.createRange(); r.setStart(p, 0); r.collapse(true); var s = window.getSelection(); s.removeAllRanges(); s.addRange(r); } catch (err) { /* egal */ }
    }, 0);
  }
  function slashPopOpen() {
    var pop = document.querySelector('.ce-popover--opened');
    return pop && !pop.closest('.ce-settings') && !pop.closest('.ce-inline-toolbar') ? pop : null;
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && slashPopOpen() && (slashP || slashSrc)) slashUndo();
  }, true);

  // Enter, das Editor.js NICHT selbst behandelt hat (kein aktueller Block nach dem Umwandeln per "/" oder Kuerzel, Menue-
  // Zustand haengt, Handy-Tastatur mit keyCode 229): der Browser fuegte ein <div> IN die Ueberschrift/den Absatz ein, der
  // Sanitizer verwirft es beim Speichern -> Zeilen kleben zusammen ("KuchenMehl"). Editor.js verhindert bei eigenem Enter
  // schon das keydown, dann kommt kein beforeinput - hier also nur der Fall ohne Behandlung: Block an der Schreibmarke
  // teilen, Rest als neuen Absatz darunter (wie Editor.js selbst). BUG-011
  document.addEventListener('beforeinput', function (e) {
    if (e.defaultPrevented || e.inputType !== 'insertParagraph' || e.isComposing) return;
    var t = e.target, node = t && t.closest ? t.closest('.ce-paragraph, .ce-header') : null, box = $('pg-editor');
    var blk = node && node.parentNode && node.parentNode.classList.contains('ce-block__content') ? node.closest('.ce-block') : null;
    if (!blk || !box || !box.contains(node) || !node.isContentEditable) return;
    var sel = window.getSelection(), r = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
    if (!r || !node.contains(r.startContainer) || !node.contains(r.endContainer)) return;
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(blk) && (!best || best.holder.contains(x.holder))) best = x; });
    var ed = best ? best.ed : S.editor, idx = [].filter.call(blk.parentNode.children, function (x) { return x.classList.contains('ce-block'); }).indexOf(blk);
    if (!ed || idx < 0) return;
    e.preventDefault();
    r.deleteContents();
    var tail = document.createRange(); tail.setStart(r.endContainer, r.endOffset); tail.setEnd(node, node.childNodes.length);
    var tmp = document.createElement('div'); tmp.appendChild(tail.extractContents());
    var rest = tmp.textContent.trim() ? tmp.innerHTML : '';          // trim() kennt auch das geschuetzte Leerzeichen
    try { ed.toolbar.close(); } catch (err) { /* Komfort */ }
    ed.blocks.insert('paragraph', { text: rest }, undefined, idx + 1, true);
    ed.caret.setToBlock(idx + 1, 'start');
  });

  // 2.4 (Testrunde W7): Aus einem Zitat (Editor.js-Quote: Enter = Zeilenumbruch) per Tastatur herauskommen: Enter in
  // einer leeren letzten Zeile oder in "Quelle", bzw. Pfeil nach unten am Ende -> neuer Absatz darunter (wie Notion).
  function blockEd(blk) {
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(blk) && (!best || best.holder.contains(x.holder))) best = x; });
    return best ? best.ed : S.editor;
  }
  function caretAtEnd(node) {
    var sel = window.getSelection();
    if (!sel || !sel.rangeCount || !sel.isCollapsed) return false;
    var r = sel.getRangeAt(0), tail = document.createRange();
    tail.setStart(r.endContainer, r.endOffset); tail.setEnd(node, node.childNodes.length);
    return !tail.toString().replace(/[\u00a0\n\s]/g, '').length;
  }
  function leaveBlock(blk, trim) {
    var ed = blockEd(blk), idx = [].filter.call(blk.parentNode.children, function (x) { return x.classList.contains('ce-block'); }).indexOf(blk);
    if (!ed || idx < 0) return false;
    if (trim) {                                          // leere letzte Zeile(n) entfernen
      var last;
      while ((last = trim.lastChild) && ((last.nodeName === 'BR') || (last.nodeType === 3 && !last.textContent.replace(/[\u00a0\s]/g, '').length)
             || (last.nodeName === 'DIV' && !last.textContent.replace(/[\u00a0\s]/g, '').length))) trim.removeChild(last);
    }
    var next = ed.blocks.getBlockByIndex(idx + 1);
    if (next && next.name === 'paragraph' && next.holder && !next.holder.textContent.trim()) { ed.caret.setToBlock(idx + 1, 'start'); return true; }
    try { ed.toolbar.close(); } catch (err) { /* Komfort */ }
    ed.blocks.insert('paragraph', {}, undefined, idx + 1, true);
    ed.caret.setToBlock(idx + 1, 'start');
    S.dirtyBody = true; schedule();
    return true;
  }
  document.addEventListener('keydown', function (e) {
    if (e.isComposing || e.altKey || e.ctrlKey || e.metaKey || (e.key !== 'Enter' && e.key !== 'ArrowDown')) return;
    var t = e.target, q = t && t.closest ? t.closest('.cdx-quote') : null;
    if (!q || !$('pg-editor') || !$('pg-editor').contains(q) || readOnly()) return;
    var blk = q.closest('.ce-block'), txt = q.querySelector('.cdx-quote__text'), cap = q.querySelector('.cdx-quote__caption');
    if (!blk) return;
    var inCap = cap && cap.contains(t), field = inCap ? cap : txt;
    if (!field || !caretAtEnd(field)) return;
    if (e.key === 'Enter') {
      if (e.shiftKey) return;
      if (inCap) { e.preventDefault(); e.stopPropagation(); leaveBlock(blk); return; }
      var html = txt.innerHTML, plain = txt.innerText || '';
      // zweites Enter: letzte Zeile ist leer (Editor.js-Quote fuegt <br> bzw. <div><br></div> ein)
      if (/(<br\s*\/?>\s*|<div[^>]*>(<br\s*\/?>)?<\/div>\s*)$/i.test(html) && /\n\s*$/.test(plain)) {
        e.preventDefault(); e.stopPropagation(); leaveBlock(blk, txt);
      }
      return;
    }
    if (inCap || !cap || !cap.textContent.trim()) {        // Pfeil nach unten am Ende: unter das Zitat
      var b = blockEd(blk), idx = [].filter.call(blk.parentNode.children, function (x) { return x.classList.contains('ce-block'); }).indexOf(blk);
      if (b && idx === b.blocks.getBlocksCount() - 1) { e.preventDefault(); e.stopPropagation(); leaveBlock(blk); }
    }
  }, true);

  // --- Zustand ---------------------------------------------------------------
  var S = { pages: [], shared: [], cur: null, editor: null, loading: false, dirtyBody: false, dirtyTitle: false, timer: null,
            open: {}, onCount: function () {} };
  try { S.open = JSON.parse(HK.store('hk-pg-open') || '{}') || {}; } catch (e) { S.open = {}; }
  function saveOpen() { HK.store('hk-pg-open', JSON.stringify(S.open)); }
  // Eigene Seiten (S.pages) und fuer mich freigegebene fremde Seiten (S.shared, /api/shared)
  // Index ueber S.pages/S.shared (ID -> Seite, Elternseite -> Kinder): statt Array.find je Seite (O(n^2) beim Baum).
  // Wird neu gebaut, sobald S.pages/S.shared ersetzt werden oder ihre Laenge sich aendert; wer parent_id einer
  // Seite direkt aendert, ruft dropIdx(). Reihenfolge der Kinder = Reihenfolge in S.pages (wie vorher filter()).
  var IX = null;
  function dropIdx() { IX = null; }
  function pix() {
    if (IX && IX.p === S.pages && IX.s === S.shared && IX.np === S.pages.length && IX.ns === S.shared.length) return IX;
    var ix = { p: S.pages, s: S.shared, np: S.pages.length, ns: S.shared.length, own: new Map(), sh: new Map(),
               kids: new Map(), top: [], shKids: new Map() };
    S.pages.forEach(function (p) { if (!ix.own.has(p.id)) ix.own.set(p.id, p); });
    S.shared.forEach(function (p) {
      if (!ix.sh.has(p.id)) ix.sh.set(p.id, p);
      if (!p.root) { var l = ix.shKids.get(p.parent_id); if (!l) ix.shKids.set(p.parent_id, l = []); l.push(p); }
    });
    S.pages.forEach(function (p) {
      var k = p.parent_id || null, l = ix.kids.get(k); if (!l) ix.kids.set(k, l = []); l.push(p);
      if (k !== null && !ix.own.has(k) && !ix.sh.has(k)) ix.top.push(p);            // Elternseite unbekannt: oben anzeigen
      else if (k === null) ix.top.push(p);
    });
    return (IX = ix);
  }
  function byId(id) { var ix = pix(); return ix.own.get(id) || ix.sh.get(id) || null; }
  function allPages() { return S.pages.concat(S.shared); }
  // Rolle auf der offenen Seite: owner > edit > comment > view (fehlt das Feld: aeltere Antwort = eigene Seite)
  function role() { return S.cur ? (S.cur.role || 'owner') : null; }
  // Seite einer anderen Saga-Instanz (Foederation): negative ID, Inhalt kommt ueber den eigenen Server
  // Schlagworte defensiv: Daten anderer Instanzen koennen kaputt sein (BUG-034)
  function tagsOf(p) { return p && Array.isArray(p.tags) ? p.tags.filter(function (t) { return typeof t === 'string'; }) : []; }
  function remoteId(id) { return typeof id === 'number' && id < 0; }
  // 2.3: was mit dem Server einer fremden Seite geht (db subpages upload versions export presence live chatgroups);
  // eigene Seiten koennen alles. Aeltere Gegenstellen (2.1/2.2) melden nichts - dann wie bisher nur Lesen/Bearbeiten/Kommentare.
  function remCap(p, cap) { return !p || !remoteId(p.id) || ((p.remote || {}).caps || []).indexOf(cap) >= 0; }
  function isOwner() { return role() === 'owner'; }
  function foreign() { return !!S.cur && !isOwner(); }
  function canEdit() { var r = role(); return r === 'owner' || r === 'edit'; }
  function roRole() { return !!S.cur && !canEdit(); }                    // Lesen/Kommentieren: Editor schreibgeschuetzt
  function readOnly() { return !!S.cur && (!!S.cur.locked || roRole()); }
  // Rollen-Etiketten (eigene Schluessel mit ##Rolle: im Englischen "Can edit" statt der Aktion "Edit")
  var ROLE_NAME = { owner: T('Eigentümer'), edit: T('Bearbeiten##Rolle'), comment: T('Kommentieren##Rolle'), view: T('Lesen##Rolle') };
  var ROLE_YOU = { edit: T('Du kannst bearbeiten'), comment: T('Du kannst lesen und kommentieren'), view: T('Du kannst lesen') };
  var ROLE_HELP = { view: T('Lesen: Seite, Unterseiten und Einträge ansehen und exportieren.'),
                    comment: T('Kommentieren: lesen und Kommentare schreiben.'),
                    edit: T('Bearbeiten: Inhalte ändern und Unterseiten anlegen. Löschen, Verschieben und Teilen bleibt bei dir.') };
  var RANK = { view: 1, comment: 2, edit: 3, owner: 4 };
  // Gruppen-/Personen-Symbol einfarbig (CSS-Maske in Textfarbe) statt des farbigen Emojis
  function grpIc(cls) { var s = el('span', 'ic-grp' + (cls ? ' ' + cls : '')); s.setAttribute('aria-hidden', 'true'); return s; }
  // Uploads in fremde Seiten: Datei gehoert dem Eigentuemer (Server prueft 'edit') - Editor.js liest die Kopfzeilen beim Hochladen
  var UPH = { 'X-Saga': '1' };
  function uploadNote() { if (foreign()) UPH['X-Saga-Note'] = String(S.cur.id); else delete UPH['X-Saga-Note']; HK.fileNote = foreign() ? S.cur.id : null; }
  function children(pid) { var ix = pix(); return (pid === null ? ix.top : ix.kids.get(pid) || []).slice(); }
  function ancestors(id) { var out = [], p = byId(id), guard = 0; while (p && p.parent_id && guard++ < 50) { p = byId(p.parent_id); if (p) out.unshift(p); } return out; }
  function descendants(id) {
    var out = [], st = [id], ix = pix();
    while (st.length) {
      var x = st.pop();
      (x === null ? S.pages.filter(function (p) { return p.parent_id === null; }) : ix.kids.get(x) || []).forEach(function (p) { out.push(p.id); st.push(p.id); });
    }
    return out;
  }
  function label(p) { return p && p.title ? p.title : T('Unbenannt'); }
  function nSub(n) { return n === 1 ? T('1 Unterseite') : T('{n} Unterseiten', { n: n }); }

  function state(t, cls) { var s = $('pg-state'); s.textContent = t; s.className = 'note-state ' + (cls || ''); }

  // --- Seitenbaum -----------------------------------------------------------
  function renderTree() {
    var ul = $('pg-tree'); ul.textContent = ''; treeRootDnd(ul);
    treeNodes(ul, children(null), 0, children, true);
    if (!S.pages.length) {                                 // statt leerer Liste
      var nl = el('li', 'pg-tree-empty', T('Noch keine Seiten')); nl.setAttribute('role', 'treeitem'); nl.setAttribute('aria-disabled', 'true'); ul.appendChild(nl);
    }
    renderShared();
    $('pg-empty').hidden = S.pages.length > 0 || S.shared.length > 0 || !!S.cur || !!($('pg-noaccess') && !$('pg-noaccess').hidden);
    pickHintSoon();
    S.onCount(S.pages.length);
    renderFavs();
  }
  // Seiten vorhanden (eigene oder geteilte), aber keine offen - z. B. nur geteilte Seiten sind eingetroffen oder die offene
  // Seite liegt jetzt im Papierkorb: Hinweis statt leerer Flaeche (BUG-095). Kurz verzoegert, sonst blitzt er beim Oeffnen auf.
  var pickT = null;
  function pickHintSoon() { clearTimeout(pickT); pickT = setTimeout(pickHint, 400); }
  function pickHint() {
    var h = $('pg-pick');
    if (!h) {
      h = el('div', 'pages-empty pg-pick'); h.id = 'pg-pick'; h.setAttribute('role', 'status');
      h.appendChild(el('p', null, T('Wähle links eine Seite – oder lege eine neue an.')));
      var b = el('button', 'btn', T('+ Neue Seite')); b.type = 'button';
      b.addEventListener('click', function () { newPageMenu(b, null); }); h.appendChild(b);
      $('pg-empty').parentNode.insertBefore(h, $('pg-empty'));
    }
    var busy = function (id) { var x = $(id); return !!x && !x.hidden; };
    h.hidden = !!S.cur || !(S.pages.length || S.shared.length) || busy('page') || busy('pg-empty') || busy('pg-noaccess') || busy('pg-trash') || EMBED;
  }
  // Ein Zweig des Baums - eigene Seiten (mit Ziehen) und "Mit mir geteilt" (ohne Ziehen, "+" nur mit 'Bearbeiten')
  function treeNodes(list, items, depth, kidsOf, own) {
    items.forEach(function (p) {
      // Datenbank-Zeilen erscheinen nicht einzeln im Baum (wie in Notion)
      var kids = p.kind === 'database' ? [] : kidsOf(p.id), li = el('li', 'pg-node'); li.setAttribute('role', 'treeitem');
      var row = el('div', 'pg-row' + (S.cur && S.cur.id === p.id ? ' on' : ''));
      row.style.paddingLeft = (0.35 + depth * 1.05) + 'rem';
      var tw = el('button', 'pg-twisty' + (kids.length ? '' : ' leaf'), kids.length ? '▸' : '');
      tw.type = 'button'; tw.tabIndex = kids.length ? 0 : -1;
      if (S.open[p.id]) tw.classList.add('open');
      tw.setAttribute('aria-label', S.open[p.id] ? T('Zuklappen') : T('Aufklappen'));
      // Auf-/Zuklappen: nur diesen Ast neu aufbauen bzw. entfernen (nicht den ganzen Baum)
      tw.addEventListener('click', function (e) {
        e.stopPropagation(); S.open[p.id] = !S.open[p.id]; saveOpen();
        var now = p.kind === 'database' ? [] : kidsOf(p.id), old = li.querySelector(':scope > .pg-sub');
        if (old) li.removeChild(old);
        tw.classList.toggle('open', !!S.open[p.id]);
        tw.setAttribute('aria-label', S.open[p.id] ? T('Zuklappen') : T('Aufklappen'));
        if (now.length && S.open[p.id]) { var s2 = el('ul', 'pg-sub'); s2.setAttribute('role', 'group'); treeNodes(s2, now, depth + 1, kidsOf, own); li.appendChild(s2); }
      });
      var go = el('button', 'pg-go'); go.type = 'button';
      go.appendChild(icoNode(p.icon, '📄', 'pg-ic'));
      go.appendChild(el('span', 'pg-name', label(p)));
      if (p.template) go.appendChild(el('span', 'pg-badge', T('Vorlage')));
      if (p.unread) { var ud = el('span', 'pg-unread'); ud.title = T('Neue Kommentare'); ud.setAttribute('aria-label', ud.title); go.appendChild(ud); }
      if (own && p.shared) {
        var si = el('span', 'pg-shared-ic'); si.appendChild(grpIc()); si.title = T('Geteilt'); si.setAttribute('role', 'img'); si.setAttribute('aria-label', si.title); go.appendChild(si);
      }
      if (!own && p.root && p.owner) go.title = T('Geteilt von {name} · {role}', { name: p.owner.name, role: ROLE_YOU[p.role] || '' });
      go.addEventListener('click', function () { openPage(p.id); });
      row.appendChild(tw); row.appendChild(go);
      if (own || (p.role === 'edit' && remCap(p, 'subpages'))) {      // fremde Instanz: nur, wenn ihr Server es kann (2.3)
        var plus = el('button', 'pg-add', '+'); plus.type = 'button'; plus.title = T('Unterseite hinzufügen');
        plus.setAttribute('aria-label', T('Unterseite zu {name} hinzufügen', { name: label(p) }));
        plus.addEventListener('click', function (e) { e.stopPropagation(); if (own) newPageMenu(plus, p.id); else newPage(p.id); });
        row.appendChild(plus);
      }
      li.appendChild(row);
      if (own) treeDnd(row, p);
      if (kids.length && S.open[p.id]) { var sub = el('ul', 'pg-sub'); sub.setAttribute('role', 'group'); treeNodes(sub, kids, depth + 1, kidsOf, own); li.appendChild(sub); }
      list.appendChild(li);
    });
  }
  function sharedKids(pid) { return (pix().shKids.get(pid) || []).slice(); }
  // "Mit mir geteilt": oberste geteilte Seiten (mit Eigentuemer im Tooltip und klein darunter), aufklappbar
  function renderShared() {
    var box = $('pg-shared-box'), ul = $('pg-shared-tree'); if (!box || !ul) return;
    var roots = S.shared.filter(function (p) { return p.root; });
    box.hidden = !roots.length && !S.shareOff; ul.textContent = '';
    var n = S.shared.filter(function (p) { return p.unread; }).length, cnt = $('pg-shared-n');
    if (cnt) {                                             // Zahl am Bereich: Seiten mit neuen Kommentaren
      cnt.hidden = !n; cnt.textContent = n ? String(n) : '';
      cnt.title = n === 1 ? T('1 Seite mit neuen Kommentaren') : T('{n} Seiten mit neuen Kommentaren', { n: n });
    }
    var off = $('pg-shared-off'); if (off) off.hidden = !S.shareOff;
    treeNodes(ul, roots, 0, sharedKids, false);
    [].forEach.call(ul.children, function (li, i) {        // Eigentuemer klein unter der obersten Seite
      var p = roots[i], go = li.querySelector('.pg-go');
      if (p && p.owner && go) { var o = el('span', 'pg-owner', p.owner.name); go.querySelector('.pg-name').appendChild(o); }
    });
  }

  function renderCrumbs() {
    var c = $('pg-crumbs'); c.textContent = '';
    if (!S.cur) return;
    ancestors(S.cur.id).forEach(function (a) {
      var b = el('button', 'crumb', (ti(a.icon) ? ti(a.icon) + ' ' : '') + label(a)); b.type = 'button';
      b.addEventListener('click', function () { openPage(a.id); });
      c.appendChild(b); c.appendChild(el('span', 'crumb-sep', '/'));
    });
    c.appendChild(el('span', 'crumb cur', (ti(S.cur.icon) ? ti(S.cur.icon) + ' ' : '') + label(S.cur)));
  }

  // Seitenbaum neu laden. Mehrere Aufrufe, waehrend eine Anfrage laeuft, teilen sich EINE weitere Anfrage, die erst danach
  // startet - jeder Aufrufer bekommt also Daten, die nach seinem Aufruf geholt wurden (nichts veraltet), aber 5 Aktionen
  // kurz hintereinander kosten 2 statt 5 vollstaendige Baum-Abfragen.
  var LL = null, LLnext = null;
  function loadList() {
    if (LLnext) return LLnext;
    if (LL) return (LLnext = LL.catch(function () {}).then(function () { LLnext = null; return startList(); }));
    return startList();
  }
  function startList() {
    var p = LL = loadListNow();
    var done = function () { if (LL === p) LL = null; };
    p.then(done, done);
    return p;
  }
  // Neuer Datenbank-Eintrag (eigene Datenbank): lokal in S.pages/S.all eintragen statt den ganzen Baum neu zu laden.
  // Eintraege erscheinen nicht im Baum; gebraucht werden sie fuer byId (Pfad, Seitenkopf), Relationen und Suche. Gleiche
  // Felder wie /api/notes; der naechste vollstaendige Abgleich (Aktion, Live-Kanal, Rueckkehr in den Tab) ersetzt den Eintrag.
  // false: Datenbank gehoert jemand anderem -> der Aufrufer laedt wie bisher neu (S.shared braucht Rolle/Eigentuemer).
  function addRowLocal(dbId, d, title) {
    var db = pix().own.get(dbId);
    if (LL || !db || db.kind !== 'database' || !d || !d.id || byId(d.id)) return false;   // LL: laufender Abgleich kaeme ohne ihn zurueck
    var p = { id: d.id, title: title || '', icon: '', parent_id: dbId, updated: d.updated, tags: [], pinned: false, template: false,
              kind: 'page', archived: false, locked: false, shared: false, unread: false };
    if (S.all && S.all !== S.pages) S.all.push(p);
    S.pages.push(p);
    renderTree(); renderCrumbs(); renderTagBar(); renderFiltered();      // wie nach loadList (Zaehler, Leer-Hinweise)
    return true;
  }
  // Seitenbaum per Abgleich (2.2, docs/APP-API-LIVE.md "Seitenbaum abgleichen"): beim ersten Mal ganz (delta=1, mit Stand),
  // danach nur Geaendertes seit dem Stand (since=...) - eingearbeitet in eine NEUE Liste in derselben Reihenfolge wie ein
  // ganzes /api/notes (jede geaenderte Seite steht hinter ihrem "after"; unveraenderte Eintraege bleiben dieselben
  // Objekte). Der Server antwortet mit full:true, wenn der Stand nicht mehr passt; spaetestens alle 30 min holt das Web
  // trotzdem einmal alles.
  var TREE_FULL_MS = 30 * 60 * 1000;
  function fetchTree() {
    var cur = S.treeCur, base = S.all;
    var full = !cur || !base || Date.now() - (S.treeFullAt || 0) > TREE_FULL_MS;
    return api('GET', full ? '/api/notes?delta=1' : '/api/notes?since=' + encodeURIComponent(cur)).then(function (d) {
      if (Array.isArray(d)) { S.treeCur = null; return d; }                       // Server ohne Abgleich: ganze Liste
      S.treeCur = d.cursor || null;
      if (d.full || full) { S.treeFullAt = Date.now(); return d.notes; }
      if (S.all !== base) { S.treeCur = null; return fetchTree(); }               // inzwischen ganz neu geladen: sicher ist sicher
      if (!d.notes.length && !d.removed.length) return base.slice();
      var gone = new Set(d.removed), chains = new Map(), out = [];
      d.notes.forEach(function (p) {                    // Ketten: Vorgaenger -> Seiten direkt dahinter (in Listen-Reihenfolge)
        gone.add(p.id);
        var k = p.after == null ? 'start' : p.after, l = chains.get(k); if (!l) chains.set(k, l = []);
        var n = {}; Object.keys(p).forEach(function (x) { if (x !== 'after') n[x] = p[x]; }); l.push(n);
      });
      function emit(k) {                                // ohne Rekursion (lange Ketten bei vielen neuen Seiten)
        var l = chains.get(k); if (!l) return;
        chains.delete(k);
        var st = [[l, 0]];
        while (st.length) {
          var top = st[st.length - 1];
          if (top[1] >= top[0].length) { st.pop(); continue; }
          var n = top[0][top[1]++], c = chains.get(n.id);
          out.push(n);
          if (c) { chains.delete(n.id); st.push([c, 0]); }
        }
      }
      emit('start');
      base.forEach(function (p) { if (!gone.has(p.id)) { out.push(p); emit(p.id); } });
      if (chains.size) {                                // Vorgaenger unbekannt (Stand weicht ab): anhaengen, naechstes Mal ganz
        chains.forEach(function (l) { l.forEach(function (n) { out.push(n); }); });
        S.treeCur = null;
      }
      return out;
    });
  }
  function loadListNow() {
    var sh = api('GET', '/api/shared').catch(function () { return []; });      // Mit mir geteilt (aeltere Server: leer)
    // andere Saga-Instanzen (Foederation): eigene Anfrage, wartet nicht auf langsame Instanzen - kommt nach
    var my = S.fedSeq = (S.fedSeq || 0) + 1;
    var fed = api('GET', '/api/fed/shared').then(function (d) { return d && Array.isArray(d.items) ? d.items : []; }).catch(function () { return null; });
    fed.then(function (items) {
      if (items === null || my !== S.fedSeq) return;
      S.fedShared = items; S.shared = S.shared.filter(function (p) { return !remoteId(p.id); }).concat(items);
      renderTree(); renderCrumbs();                     // renderTree: auch der Leer-Hinweis rechts
    });
    return Promise.all([fetchTree(), sh]).then(function (r) {
      var list = r[0];
      S.all = list; S.pages = list.filter(function (p) { return !p.archived; });       // Archiv: nicht im Baum/Suche/Palette
      S.shared = (Array.isArray(r[1]) ? r[1] : []).concat(S.fedShared || []);
      if (!S.shared.length) {                     // nichts geteilt - oder Teilen abgeschaltet? Dann ein Hinweis statt still nichts
        api('GET', '/api/share/info').then(function (d) { var off = !d.enabled && d.received > 0; if (off !== !!S.shareOff) { S.shareOff = off; renderShared(); } })
          .catch(function () {});
      } else S.shareOff = false;
      renderTree(); renderCrumbs(); renderTagBar(); renderFiltered(); return list; });
  }

  // --- Editor ---------------------------------------------------------------
  function textToBlocks(t) {   // alte Klartext-Notiz -> Absaetze
    var paras = String(t || '').split(/\n{2,}/).map(function (s) { return s.trim(); }).filter(Boolean);
    return { blocks: paras.map(function (s) { return { type: 'paragraph', data: { text: HK.escapeHtml(s).replace(/\n/g, '<br>') } }; }) };
  }

  function ensureEditor(data) {
    if (S.editor) return S.editor.isReady.then(function () { return S.editor.render(data); }).then(function () { return S.editor; });
    return loadVendor().then(function () {
      S.editor = new window.EditorJS({
        holder: 'pg-editor', data: data, minHeight: 80, autofocus: false,
        placeholder: T("Schreib los – '/' für Blöcke und Verweise, Rechtsklick für Verweise im Text"),
        inlineToolbar: ['bold', 'italic', 'link', 'marker', 'inlineCode', 'hkdate'],
        tools: S.tools = {
          header: { class: window.Header, inlineToolbar: true, config: { levels: [1, 2, 3, 4], defaultLevel: 2, placeholder: T('Überschrift') } },
          list: { class: window.EditorjsList, inlineToolbar: true, config: { defaultStyle: 'unordered' } },
          quote: { class: window.Quote, inlineToolbar: true, config: { quotePlaceholder: T('Zitat'), captionPlaceholder: T('Quelle') } },
          code: { class: window.HKCode || window.CodeTool, config: { placeholder: T('Code') } },   // static/code.js: Sprache + Farben
          delimiter: window.Delimiter,
          inlineCode: { class: window.InlineCode },
          marker: { class: window.Marker },
          verweis: { class: RefTool },
          buttons: { class: ButtonsTool },
          columns: { class: ColumnsTool },
          database: { class: DatabaseTool },
          tasks: { class: TasksTool },
          calendar: { class: CalendarTool },
          widget: { class: WidgetTool },                // 2.5: Widgets in Seiten (gilt auch in Spalten/Toggles)
          image: { class: hkImage(window.ImageTool), config: {                 // 2.5 (G2): Breite/Ausrichtung, fremde Felder bleiben
            endpoints: { byFile: '/api/files' }, field: 'image', additionalRequestHeaders: UPH,
            types: 'image/png,image/jpeg,image/gif,image/webp', captionPlaceholder: T('Bildunterschrift'), buttonContent: T('Bild auswählen …') } },
          attaches: { class: window.AttachesTool, config: {
            endpoint: '/api/files', field: 'file', additionalRequestHeaders: UPH,
            buttonText: T('Datei auswählen …'), errorMessage: T('Hochladen fehlgeschlagen') } },
          immich: { class: ImmichTool },
          nextcloud: { class: NextcloudTool },
          embed: { class: EmbedTool },
          table: { class: window.Table, inlineToolbar: true, config: { rows: 2, cols: 3, withHeadings: true } },
          toggle: { class: ToggleTool, inlineToolbar: true },
          callout: { class: CalloutTool, inlineToolbar: true },
          hkdate: { class: DateInline },
          hkcomment: { class: CommentTune },
          hkstyle: { class: StyleTune }, hkdup: { class: DupTune }, hklink: { class: LinkTune }
        },
        tunes: BLOCK_TUNES,                    // Block-Menue (⋮⋮, 2.5 G5): Farbe, Duplizieren, Link, Kommentieren
        i18n: I18N,
        onChange: edChanged
      });
      $('pg-editor').addEventListener('input', function () { if (!S.loading) tryShortcut(S.editor); });
      return S.editor.isReady.then(function () {
        try { new window.DragDrop(S.editor); } catch (e) { /* Ziehen ist Komfort - Editor laeuft auch ohne */ }
        return S.editor;
      });
    });
  }

  // Editor meldet eine Aenderung. Editor.js meldet auch Aenderungen, die keine sind (Verweise werden beim Oeffnen
  // beschriftet, eingebettete Datenbanken/Kalender laden nach, verschachtelte Editoren werden fertig ...). Deshalb:
  // die ERSTE Meldung nach einem gespeicherten Stand wird mit dem gespeicherten Inhalt (S.lastBlocks) verglichen -
  // nur bei echtem Unterschied "Ungespeichert" + Speichern. Weitere Meldungen (schon ungespeichert) ohne Vergleich.
  function edChanged() {
    if (S.loading) return;
    if (S.dirtyBody || !S.editor || !S.cur) { S.dirtyBody = true; schedule(); return; }
    var id = S.cur.id;
    if (S.baseId !== id) return;                     // Seite laedt noch - Vergleichsstand kommt gleich (openPage)
    if (S.checking) { S.recheck = true; return; }
    S.checking = true;
    S.editor.save().then(function (o) {
      S.checking = false;
      // inzwischen andere Seite geoeffnet: nichts markieren (openPage hat die alte Seite schon gespeichert, s. dort)
      if (!S.cur || S.cur.id !== id || S.baseId !== id) { S.recheck = false; return; }
      if (!o || !o.blocks || JSON.stringify(o.blocks) !== S.lastBlocks) { S.recheck = false; S.dirtyBody = true; schedule(); }
      else if (S.recheck) { S.recheck = false; edChanged(); }
    }, function () { S.checking = false; S.dirtyBody = true; schedule(); });
  }
  // Echte Eingabe (Tippen, Einfuegen, Ziehen) im Editor, auch in Spalten/Ausklappbar (isolate() stoppt das Ereignis
  // dort - daher Capture am document). Editor.js meldet Aenderungen erst ~400 ms spaeter (gebuendelt); eine Eingabe
  // ist aber sicher eine: sofort "ungespeichert", damit ein schneller Seitenwechsel sie mitspeichert. Waehrend die
  // Seite noch laedt (kein Vergleichsstand): merken - der Vergleichsstand enthaelt die Eingabe dann ja schon.
  document.addEventListener('input', function (e) {
    var box = $('pg-editor'), tg = e.target;
    if (!box || !tg || !tg.isContentEditable || !box.contains(tg) || !S.cur) return;
    if (S.loading || S.baseId !== S.cur.id) { S.typed = true; return; }
    S.dirtyBody = true; schedule();
  }, true);
  function schedule() {
    if (S.lost) return;                                                               // Zugriff verloren: Dialog zeigt den Text
    if (readOnly()) { S.dirtyBody = S.dirtyTitle = false; return; }                    // gesperrt / nur Lesen: nichts speichern
    state(T('Ungespeichert …')); clearTimeout(S.timer); S.timer = setTimeout(save, 900);
  }

  // Speichern nacheinander (Warteschlange): ein zweites Speichern waehrend das erste noch laeuft, wuerde sonst
  // die veraltete Revision mitschicken -> falscher Konflikt "mit sich selbst" (z. B. bei langsamer Verbindung)
  function save() {
    S.saveChain = (S.saveChain || Promise.resolve()).then(saveNow, saveNow);
    return S.saveChain;
  }
  function saveNow() {
    if (!S.cur || S.conflict || S.lost || (!S.dirtyBody && !S.dirtyTitle)) return Promise.resolve();
    clearTimeout(S.timer);
    var id = S.cur.id, body = {};
    if (S.dirtyTitle) { body.title = $('pg-title').value; S.dirtyTitle = false; }
    var wantBody = !!(S.dirtyBody && S.editor), getBody = wantBody ? S.editor.save() : Promise.resolve(null);
    S.dirtyBody = false;
    state(T('Speichert …'));
    return getBody.then(function (out) {
      // W01: Editor.js liefert bei einem Fehler im Block nichts - nicht still als "nichts zu speichern" werten
      if (wantBody && !(out && Array.isArray(out.blocks))) { S.dirtyBody = true; throw new Error(T('Inhalt konnte nicht gelesen werden')); }
      if (out) { body.body = JSON.stringify(out); body.format = 'blocks'; }
      if (!Object.keys(body).length) return;
      if (S.cur && S.cur.id === id && !S.force && S.cur.rev != null) body.rev = S.cur.rev;   // Konflikterkennung
      S.force = false;
      if (S.overwrite) { body.overwrite = true; S.overwrite = false; }       // "Meine Version behalten": andere Fassung als Version sichern
      S.inflight = (S.inflight || 0) + 1;                                    // Live: waehrenddessen nicht still neu laden
      var done = function () { S.inflight = Math.max(0, (S.inflight || 1) - 1); };
      var req = api('PATCH', '/api/notes/' + id, body); req.then(done, done);
      return req.then(function (d) {
        if (S.cur && S.cur.id === id && d.rev != null) S.cur.rev = d.rev;
        if (S.cur && S.cur.id === id && d.updated) S.cur.updated = d.updated;
        if (S.cur && S.cur.id === id && S.cur.updated_by && !S.cur.updated_by.me) { S.cur.updated_by = { me: true }; applyMeta(); }
        var p = byId(id); if (p) { p.updated = d.updated; if ('title' in body) p.title = body.title.trim(); }
        if (out && /"widget"/.test(body.body || '')) setTimeout(loadWidgets, 0);      // Widgets: Werte zum gespeicherten Stand
        if (!S.dirtyBody && !S.dirtyTitle) state(T('Gespeichert ✓ {time}', { time: HK.fUpd.format(new Date(d.updated * 1000)) }), 'ok');
        if (!remoteId(id)) { S.ckId = id; clearTimeout(S.ckTimer); S.ckTimer = setTimeout(function () { checkpoint('pause'); }, CK_PAUSE); }
        if ('title' in body) { if (S.cur && S.cur.id === id) S.cur.title = body.title.trim(); renderTree(); renderCrumbs(); setLocation(); }
      });
    }).catch(function (err) {
      S.dirtyBody = S.dirtyBody || !!body.body || (wantBody && !body.body); S.dirtyTitle = S.dirtyTitle || 'title' in body;
      if (err.message === 'conflict') { S.conflict = true; showConflict(err.data && err.data.by); state(T('Nicht gespeichert – Seite wurde zwischenzeitlich geändert'), 'err'); return; }
      // Freigabe entzogen, Teilen abgeschaltet (404) oder Rolle auf Lesen gefallen (403): verstaendlich melden, Text retten
      if ((err.status === 404 || err.status === 403) && S.cur && S.cur.id === id && foreign()) { lostAccess(err.status); return; }
      state(T('Nicht gespeichert: {err}', { err: err.message }), 'err');
    });
  }
  // 2.4: Versionen beim Bearbeiten - der Server sichert beim Speichern hoechstens alle 5 Minuten den vorigen Stand. Damit
  // auch der zuletzt bearbeitete Stand im Verlauf landet: nach einer Pause (2 Minuten ohne Speichern) und beim Verlassen
  // der Seite (andere Seite, Tab im Hintergrund, Schliessen) den aktuellen Stand sichern lassen (Server drosselt).
  var CK_PAUSE = 120000;
  function checkpoint(why) {
    var id = S.ckId;
    clearTimeout(S.ckTimer);
    if (!id) return Promise.resolve();
    S.ckId = null;
    return save().then(function () { return api('POST', '/api/notes/' + id + '/versions/checkpoint', { why: why }); }).catch(function () {});
  }
  // Konflikt: jemand (anderer Tab, Peek, spaeter andere Person) hat Titel/Inhalt inzwischen geaendert
  function showConflict(by) {
    clearRemote();                                   // Live-Hinweis "X hat diese Seite geaendert" geht im Konflikt auf
    var bar = $('pg-conflict');
    if (!bar) { bar = el('div', 'pg-conflict'); bar.id = 'pg-conflict'; bar.setAttribute('role', 'alert'); $('page').insertBefore(bar, $('page').firstChild); }
    bar.textContent = '';
    bar.appendChild(el('span', 'pgc-t', by ? T('⚠ {name} hat diese Seite gerade geändert. Deine letzten Änderungen sind noch nicht gespeichert.', { name: by })
                                           : T('⚠ Diese Seite wurde inzwischen an anderer Stelle geändert. Deine letzten Änderungen sind noch nicht gespeichert.')));
    // Hauptknopf: neue Version laden - der eigene, nicht gespeicherte Text bleibt danach zum Kopieren sichtbar
    var load = el('button', 'btn btn-primary', T('Neue Version laden')); load.type = 'button';
    load.addEventListener('click', function () {
      var draft = draftText(), id = S.cur.id;
      clearConflict(); S.dirtyBody = S.dirtyTitle = false;
      openPage(id).then(function () {
        if (draft) showDraft(T('Dein Entwurf'), T('Die neue Version ist geladen. Dein nicht gespeicherter Text steht unten – kopiere, was du behalten willst.'), draft);
      });
    });
    // Ueberschreiben nur mit Rueckfrage; die andere Fassung sichert der Server als Version (mit Autor)
    var keep = el('button', 'btn', T('Meine Version behalten')); keep.type = 'button';
    keep.addEventListener('click', function () {
      HK.ask({ title: by ? T('Änderungen von {name} überschreiben?', { name: by }) : T('Andere Änderungen überschreiben?'),
               text: by ? T('Die Fassung von {name} bleibt in den Versionen erhalten.', { name: by }) : T('Die andere Fassung bleibt in den Versionen erhalten.'),
               ok: T('Überschreiben'), danger: true }).then(function (yes) {
        if (!yes) return;
        clearConflict(); S.force = true; S.overwrite = true; S.dirtyBody = true; S.dirtyTitle = true; save();
      });
    });
    bar.appendChild(load); bar.appendChild(keep);
  }
  // Eigener Text der offenen Seite als Klartext (Titel + Bloecke, so wie sie gerade im Editor stehen)
  function draftText() {
    var red = $('pg-editor').querySelector('.codex-editor__redactor'), out = [];
    if (red) [].forEach.call(red.children, function (b) {
      var c = b.querySelector('.ce-block__content'); var t = c ? (c.innerText || '').trim() : '';
      if (t) out.push(t);
    });
    var title = $('pg-title').value.trim();
    return ((title ? title + '\n\n' : '') + out.join('\n\n')).trim();
  }
  // Text zum Kopieren zeigen (Entwurf nach Konflikt, Zugriff verloren) - einheitlich: Titel, kurzer Text, Textfeld (nur so
  // hoch wie noetig, hoechstens ~40 % der Hoehe), Hauptknopf "Kopieren", zweiter Knopf "Schliessen" (kein zweites x oben)
  function showDraft(title, text, draft, onClose) {
    var cp = null;
    openModal(title, function (body) {
      var box = el('div', 'pub-dlg draft-dlg'); body.appendChild(box);
      box.appendChild(el('p', 'draft-t', text));
      var ta = el('textarea', 'hk-in draft-ta'); ta.readOnly = true; ta.value = draft; ta.rows = 2; ta.setAttribute('aria-label', T('Dein Text'));
      box.appendChild(ta);
      var bar = el('div', 'hk-actions');
      bar.appendChild(pubBtn(T('Schließen'), closeModal));
      cp = pubBtn(T('Kopieren'), function () {
        ta.select();
        (navigator.clipboard ? navigator.clipboard.writeText(draft) : Promise.reject(new Error('x')))
          .then(function () { HK.toast(T('Text kopiert')); }, function () { try { document.execCommand('copy'); HK.toast(T('Text kopiert')); } catch (e) { /* markiert */ } });
      }, 'btn-primary');
      bar.appendChild(cp);
      box.appendChild(bar);
      requestAnimationFrame(function () { ta.style.height = 'auto'; ta.style.height = (ta.scrollHeight + 2) + 'px'; });
    }, true);
    $('pg-modal').classList.add('no-x');
    if (cp) cp.focus();
    S.modalClose = onClose || null;
  }
  function lostAccess(status) {
    if (S.lost) return;
    var id = S.cur.id, draft = draftText();
    S.lost = true; clearTimeout(S.timer); S.dirtyBody = S.dirtyTitle = false;
    clearConflict();                                   // kein Konflikt-Banner mehr neben "Nur noch lesen" / "Kein Zugriff"
    state(T('Nicht gespeichert'), 'err');
    showDraft(status === 403 ? T('Nur noch lesen') : T('Kein Zugriff mehr'),
      status === 403 ? T('Du darfst diese Seite nicht mehr bearbeiten. Dein letzter Text ist unten – kopiere ihn, wenn du ihn behalten willst.')
                     : T('Du hast keinen Zugriff mehr auf diese Seite. Dein letzter Text ist unten – kopiere ihn, wenn du ihn behalten willst.'),
      draft, function () { S.lost = false; });
    // Neuen Zustand sofort zeigen (hinter dem Dialog): 403 -> schreibgeschuetzt mit neuem Etikett, 404 -> "Kein Zugriff"
    loadList().then(function () { return openPage(id); });
  }
  // Rolle/Zugriff einer fremden Seite still neu pruefen (Rueckkehr in den Tab, Fehler beim Kommentieren): geaenderte Rolle
  // oder Sperre -> Seite neu oeffnen (Etikett, Schreibschutz), kein Zugriff mehr -> eigene Ansicht
  var recheckT = 0;
  function recheck(force) {
    var c = S.cur, now = Date.now();
    if (!c || !foreign() || S.lost || S.conflict || S.loading || S.dirtyBody || S.dirtyTitle || !$('pg-modal').hidden) return Promise.resolve();
    if (!force && now - recheckT < 5000) return Promise.resolve();
    recheckT = now;
    var id = c.id;
    return api('GET', '/api/notes/' + id).then(function (n) {
      if (!S.cur || S.cur.id !== id) return;
      if ((n.role || 'owner') !== role() || !!n.locked !== !!S.cur.locked) return loadList().then(function () { return openPage(id); });
    }).catch(function (err) {
      if ((err.status === 404 || err.status === 403) && S.cur && S.cur.id === id) return loadList().then(function () { noAccess(id); });
    });
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) recheck(); });
  window.addEventListener('focus', function () { recheck(); });
  // "Zu Mit mir geteilt": Bereich in der Seitenleiste zeigen (Handy: Schublade) und den ersten Eintrag fokussieren
  function goShared() {
    var box = $('pg-shared-box'), ul = $('pg-shared-tree'); if (!box || !ul) return;
    box.hidden = false;
    if (!ul.querySelector('.pg-go') && !ul.querySelector('.pg-tree-empty')) {
      var e = el('li', 'pg-tree-empty', T('Zurzeit ist nichts mit dir geteilt.')); e.setAttribute('role', 'treeitem'); e.setAttribute('aria-disabled', 'true'); ul.appendChild(e);
    }
    drawer(true);
    var f = ul.querySelector('.pg-go');
    box.scrollIntoView({ block: 'nearest' });
    box.classList.remove('hk-flash'); void box.offsetWidth; box.classList.add('hk-flash');
    setTimeout(function () { box.classList.remove('hk-flash'); }, 1600);
    if (f) f.focus();
  }
  // Seite nicht (mehr) erreichbar: eigener Hinweis statt "Noch keine Seiten" - ohne Titel/Symbol der Seite, mit
  // "Erneut versuchen" und "Zu Mit mir geteilt"
  function noAccess(id) {
    S.cur = null; $('page').hidden = true;
    var box = $('pg-noaccess');
    if (!box) {
      box = el('div', 'pages-empty pg-noaccess'); box.id = 'pg-noaccess'; box.setAttribute('role', 'status');
      var ic = el('div', 'pg-na-ic', '🔒'); ic.setAttribute('aria-hidden', 'true'); box.appendChild(ic);
      box.appendChild(el('p', 'pg-na-t', T('Kein Zugriff auf diese Seite')));
      box.appendChild(el('p', 'pg-na-x', T('Die Seite gibt es nicht (mehr), oder sie ist nicht (mehr) mit dir geteilt.')));
      var acts = el('div', 'pg-na-acts');
      var rt = el('button', 'btn btn-primary pg-na-retry', T('Erneut versuchen')); rt.type = 'button';
      rt.addEventListener('click', function () { var t = +box.dataset.id; if (t) loadList().then(function () { return openPage(t); }); });
      var gs = el('button', 'btn pg-na-shared', T('Zu „Mit mir geteilt“')); gs.type = 'button';
      gs.addEventListener('click', goShared);
      // eigene Seite im Papierkorb (direkt oder mit der Elternseite): dorthin bzw. gleich wiederherstellen (BUG-056)
      var rs = el('button', 'btn btn-primary pg-na-restore', T('Wiederherstellen')); rs.type = 'button'; rs.hidden = true;
      rs.addEventListener('click', function () {
        var t = +box.dataset.id;
        api('POST', '/api/trash/' + t + '/restore').then(loadList).then(function () { box.hidden = true; return openPage(t); })
          .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
      });
      var tb = el('button', 'btn pg-na-trash', T('Papierkorb öffnen')); tb.type = 'button'; tb.hidden = true;
      tb.addEventListener('click', function () { box.hidden = true; showTrash(); });
      acts.appendChild(rs); acts.appendChild(rt); acts.appendChild(tb); acts.appendChild(gs); box.appendChild(acts);
      $('pg-empty').parentNode.insertBefore(box, $('pg-empty'));
    }
    box.dataset.id = id ? String(id) : '';
    box.querySelector('.pg-na-retry').hidden = !id;
    box.querySelector('.pg-na-t').textContent = T('Kein Zugriff auf diese Seite');
    box.querySelector('.pg-na-x').textContent = T('Die Seite gibt es nicht (mehr), oder sie ist nicht (mehr) mit dir geteilt.');
    box.querySelector('.pg-na-restore').hidden = true; box.querySelector('.pg-na-trash').hidden = true;
    box.hidden = false; $('pg-empty').hidden = true; document.title = T('Kein Zugriff') + ' · Saga';
    renderTree();
    if (id && !remoteId(id) && !EMBED) api('GET', '/api/trash').then(function (list) {
      if (box.dataset.id !== String(id) || box.hidden || !list.length) return;
      var hit = list.filter(function (p) { return p.id === id; })[0];
      box.querySelector('.pg-na-trash').hidden = false;
      if (hit) {
        box.querySelector('.pg-na-t').textContent = T('„{name}“ liegt im Papierkorb', { name: label(hit) });
        box.querySelector('.pg-na-x').textContent = T('Du kannst die Seite wiederherstellen.');
        box.querySelector('.pg-na-restore').hidden = false; box.querySelector('.pg-na-retry').hidden = true;
      } else {
        box.querySelector('.pg-na-x').textContent = T('Die Seite gibt es nicht (mehr), sie liegt mit einer übergeordneten Seite im Papierkorb, oder sie ist nicht (mehr) mit dir geteilt.');
      }
    }).catch(function () { /* nur Zusatzhinweis */ });
  }
  function clearConflict() { S.conflict = false; var b = $('pg-conflict'); if (b) b.remove(); }

  // Browser-Tab-Titel und Direktlink (/notes#<id>) aktuell halten
  function setLocation() {
    var h1 = $('pg-h1'); if (h1) h1.textContent = S.cur ? label(S.cur) : '';          // Bughunt D21: Ueberschrift fuer Screenreader
    if (!S.cur) { document.title = T('Seiten') + ' · Saga'; return; }
    document.title = (ti(S.cur.icon) ? ti(S.cur.icon) + ' ' : '') + label(S.cur) + ' · ' + T('Seiten');
    if (parseInt(location.hash.slice(1), 10) === S.cur.id) return;                 // auch "#12:block" = diese Seite
    if (!EMBED && /^#-?\d/.test(location.hash)) history.pushState(null, '', '#' + S.cur.id);   // Zurueck/Vor wie im Browser
    else history.replaceState(null, '', '#' + S.cur.id);
  }

  function openPage(id) {
    drawer(false);                                  // Handy: Seitenleiste (Schublade) schliessen, Inhalt zeigen
    if (S.ckId && S.ckId !== id) checkpoint('leave');   // 2.4: andere Seite -> zuletzt bearbeiteten Stand sichern
    // Review Runde 2: schnelles Wechseln - nur das zuletzt angeforderte Oeffnen gilt (Antworten koennen sich ueberholen),
    // und der Editor zeichnet nacheinander (sonst konnte Inhalt einer Seite unter einer anderen landen)
    var seq = S.openSeq = (S.openSeq || 0) + 1; S.wantId = id;
    return save().then(function () { return api('GET', '/api/notes/' + id); }).then(function (n) {
      if (seq !== S.openSeq) return null;
      S.cur = n; S.loading = true; uploadNote();
      var na = $('pg-noaccess'); if (na) na.hidden = true;
      document.dispatchEvent(new CustomEvent('hk-page-open', { detail: { id: n.id } }));
      $('page').hidden = false; $('pg-empty').hidden = true;
      $('pg-title').value = n.title || '';
      showIcon(n.icon); showCover(); $('page').classList.toggle('wide', !!n.wide); applyFont(n);
      var data;
      try { data = n.format === 'blocks' ? JSON.parse(n.body || '{"blocks":[]}') : textToBlocks(n.body); }
      catch (e) { data = { blocks: [] }; }
      S.ncKeys = ncKeys(data); S.ncStat = null;        // Nextcloud-Bloecke dieser Seite (Schutz beim Einfuegen fremder Bloecke)
      if (HK.nc && !S.ncMe) HK.nc.info().then(function (s) { S.ncMe = s.me; }).catch(function () {});
      ancestors(id).forEach(function (a) { S.open[a.id] = true; }); saveOpen();
      if (!EMBED) HK.store('hk-pg-last', String(id));
      setLocation();
      renderTree(); renderCrumbs();
      // nur Lesen/Kommentieren: kein "Gespeichert" (man speichert ja nichts) - die Kopfzeile nennt die Rolle
      state(n.updated && (!n.role || n.role === 'owner' || n.role === 'edit') ? T('Gespeichert ✓ {time}', { time: HK.fUpd.format(new Date(n.updated * 1000)) }) : '', 'ok');
      applyKind();      // Ansicht (Datenbank / Zeile / Seite) sofort umschalten, nicht erst nach dem Rendern
      var q = S.renderQ = (S.renderQ || Promise.resolve()).catch(function () {}).then(function () {
        return seq === S.openSeq ? ensureEditor(data) : null;      // inzwischen andere Seite angefordert: nicht zeichnen
      });
      return q;
    }).then(function () { if (seq === S.openSeq) S.loading = false; })
      .catch(function (err) {
        if (seq !== S.openSeq) return;
        S.loading = false;
        if (err.status === 404 || err.status === 403) { noAccess(id); return; }
        HK.toast(T('Seite konnte nicht geöffnet werden: {err}', { err: err.message }));
      });
  }

  // Tasten, die zwischen "Leere Seite" und dem Fokus im Titel getippt werden (Server-Antwort + Editor brauchen bis
  // ~1,5 s), sammeln statt verlieren - danach in den Titel (BUG-047). Liefert eine Stopp-Funktion -> gesammelter Text.
  function titleBuffer(init) {
    var buf = init || '';
    function kd(e) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
      var a = document.activeElement;
      if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
      if (e.key.length === 1) { buf += e.key; e.preventDefault(); }
      else if (e.key === 'Backspace' && buf) { buf = buf.slice(0, -1); e.preventDefault(); }
    }
    document.addEventListener('keydown', kd, true);
    return function () { document.removeEventListener('keydown', kd, true); return buf; };
  }
  function focusTitle(extra) {
    var t = $('pg-title'); t.focus();
    if (extra && !readOnly()) { t.value += extra; S.dirtyTitle = true; schedule(); }
    try { t.setSelectionRange(t.value.length, t.value.length); } catch (e) { /* egal */ }
  }
  function newPage(parentId, typed) {
    var stop = titleBuffer(typed);
    return save().then(function () { return api('POST', '/api/notes', { parent_id: parentId || null }); })
      .then(function (d) { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList().then(function () { return openPage(d.id); }); })
      .then(function () { focusTitle(stop()); })
      .catch(function (err) { stop(); HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
  }

  // Nur speichern, wenn sich der Inhalt wirklich geaendert hat (Editor.js meldet beim Laden teils "Aenderungen")
  var _origSave = save;
  save = function () {
    if (S.dirtyBody && S.editor && !S.dirtyTitle) {
      return S.editor.save().then(function (o) {
        if (JSON.stringify(o.blocks) === S.lastBlocks) { S.dirtyBody = false; clearTimeout(S.timer); state(T('Gespeichert ✓'), 'ok'); return; }
        S.lastBlocks = JSON.stringify(o.blocks);
        return _origSave();
      });
    }
    if (S.dirtyBody && S.editor) return S.editor.save().then(function (o) { S.lastBlocks = JSON.stringify(o.blocks); return _origSave(); });
    return _origSave();
  };
  var _origOpen = openPage;
  openPage = function (id) {
    if (S.conflict && S.cur && S.cur.id !== id && !confirm(T('Auf dieser Seite gibt es einen Speicher-Konflikt. Seite trotzdem wechseln? Deine ungespeicherten Änderungen gehen verloren.'))) return Promise.resolve();
    clearConflict();
    // Vergleich der alten Seite laeuft noch (Aenderung gemeldet, aber noch nicht markiert): sicherheitshalber markieren -
    // save() in _origOpen vergleicht ohnehin mit dem gespeicherten Stand und speichert nur bei echtem Unterschied
    if (S.checking && S.cur && S.baseId === S.cur.id) S.dirtyBody = true;
    S.baseId = null; S.typed = false;
    $('pg-trash').hidden = true;
    if (PK.id) closePeek();
    return _origOpen(id).then(function () {
      if (S.cur) { S.loading = true; decorateLinks(); S.loading = false; renderTags(); loadBacklinks(S.cur.id); rememberRecent(S.cur.id); archiveBar(); applyLock(); applyPublic(); applyShare(); applyMeta(); }
      var bm = location.hash.match(/^#(-?\d+):(.+)$/); if (bm && S.cur && +bm[1] === S.cur.id) setTimeout(function () { showBlock(decodeURIComponent(bm[2])); }, 150);
      if (EMBED && S.cur && !S.cur.title) $('pg-title').focus();      // neue Karte im Peek: gleich den Namen tippen
      if (!S.editor) return;
      // Vergleichsstand erst, wenn auch die verschachtelten Editoren (Spalten, Ausklappbar) fertig und beschriftet sind
      var cur = S.cur && S.cur.id;
      return Promise.all((S.eds || []).filter(function (x) { return x.holder.isConnected; }).map(function (x) { return x.ed.isReady; }))
        .then(function () { return new Promise(function (r) { setTimeout(r, 30); }); })
        .then(function () { return S.editor.save(); })
        .then(function (o) { return o && o.blocks ? JSON.stringify(o.blocks) : null; }, function () { return null; })
        .then(function (base) {
          if (!S.cur || S.cur.id !== cur) return;               // inzwischen andere Seite
          S.lastBlocks = base; S.baseId = cur;                  // null (Fehler): kein Vergleich, jede Meldung speichert
          document.dispatchEvent(new CustomEvent('hk-page-ready', { detail: { id: cur } }));      // Kommentare: Zahlen an die Bloecke
          // schon waehrend des Ladens getippt: steckt im Vergleichsstand, ist aber nicht gespeichert -> jetzt speichern
          if (S.typed) { S.typed = false; S.lastBlocks = null; S.dirtyBody = true; schedule(); }
        });
    });
  };

  // --- Kopfbereich: Titel, Icon, Menue, Vollbild ------------------------------
  $('pg-title').addEventListener('input', function () { S.dirtyTitle = true; schedule(); });
  $('pg-title').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); save(); if (S.editor) S.editor.caret.setToFirstBlock('start'); }
  });
  $('pg-title').addEventListener('blur', function () { save(); });

  var EMOJI = ['📄', '📝', '📌', '📚', '💡', '✅', '🗂️', '📅', '🧭', '🛠️', '🖥️', '🌐', '🔐', '🔑', '🧪', '🐛',
               '🚀', '⚙️', '📦', '🗄️', '☁️', '🧾', '💶', '📊', '🎯', '⭐', '❤️', '🏠', '👪', '✈️', '🎮', '🍳'];
  $('pg-icon').addEventListener('click', function () {
    var btn = this; if (readOnly() || !remCap(S.cur, 'db')) return;
    HK.openMenu(btn, function (m) {
      m.appendChild(HK.menuLabel(T('Icon')));
      var g = el('div', 'emoji-grid');
      EMOJI.forEach(function (e) {
        var b = el('button', 'emoji', e); b.type = 'button'; b.setAttribute('aria-label', T('Icon {e}', { e: e }));
        b.addEventListener('click', function () { HK.closeMenu(); setIcon(e); });
        g.appendChild(b);
      });
      m.appendChild(g);
      m.appendChild(HK.menuItem(T('🖼 Eigenes Bild hochladen …'), function () { pickImage('icon'); }));
      if (HK.immichPick && !foreign()) m.appendChild(HK.menuItem(T('📷 Aus Immich …'), function () {
        var id = S.cur.id;
        HK.immichPick(function (url) { api('PATCH', '/api/notes/' + id, { icon: url }).then(function () { setIconLocal(id, url); HK.toast(T('Icon gespeichert')); }); });
      }));
      if (S.cur && S.cur.icon) m.appendChild(HK.menuItem(T('Icon entfernen'), function () { setIcon(''); }));
    });
  });
  function setIcon(e) {
    if (!S.cur) return;
    api('PATCH', '/api/notes/' + S.cur.id, { icon: e }).then(function () {
      S.cur.icon = e; var p = byId(S.cur.id); if (p) p.icon = e;
      showIcon(e);
      renderTree(); renderCrumbs(); setLocation();
    }).catch(function (err) { HK.toast(T('Icon nicht gespeichert: {err}', { err: err.message })); });
  }

  function movePage(parentId) {
    save().then(function () { return api('PATCH', '/api/notes/' + S.cur.id, { parent_id: parentId }); })
      .then(function () { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList(); })
      .then(function () { HK.toast(T('Verschoben.')); })
      .catch(function (err) { HK.toast(T('Nicht verschoben: {err}', { err: err.message })); });
  }
  $('pg-more').addEventListener('click', function () {
    var btn = this; if (!S.cur) return;
    HK.openMenu(btn, function (m) {
      var c = S.cur, n = descendants(c.id).length;
      if (foreign()) { foreignMenu(m, c); return; }        // fremde Seite: nur, was die Rolle erlaubt
      m.appendChild(HK.menuItem(c.pinned ? T('Von Favoriten lösen') : T('Zu Favoriten'), function () { setPinned(!c.pinned); }, { icon: c.pinned ? '☆' : '★' }));
      m.appendChild(HK.menuItem(T('Volle Breite'), function () { setWide(!c.wide); }, { icon: '↔', on: !!c.wide }));
      designMenu(m, c);
      // Bughunt D17: auf Touch-Geraeten steht "Titelbild" nicht mehr dauerhaft ueber dem Titel, sondern hier
      if (!c.cover && !c.locked) m.appendChild(HK.menuItem(T('Titelbild'), function () { coverMenu(btn); }, { icon: '🖼' }));
      m.appendChild(HK.menuItem(c.locked ? T('Seite entsperren') : T('Seite sperren'), function () { setLocked(!c.locked); }, { icon: '🔒', on: !!c.locked }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(T('Duplizieren'), duplicatePage, { icon: '⧉' }));
      m.appendChild(HK.menuItem(T('Unterseite hinzufügen'), function () { newPage(c.id); }, { icon: '＋' }));
      m.appendChild(HK.menuItem(T('Verschieben nach'), null, { icon: '↪', subSearch: T('Seite suchen …'), sub: function (sm) {
        var blocked = descendants(c.id).concat([c.id]);
        if (c.parent_id) sm.appendChild(HK.menuItem(T('Oberste Ebene'), function () { moveFromMenu(c.id, null); }, { icon: '⌂' }));
        S.pages.filter(function (p) { return blocked.indexOf(p.id) < 0 && p.id !== c.parent_id && p.kind !== 'database'; }).forEach(function (p) {
          sm.appendChild(HK.menuItem(label(p), function () { moveFromMenu(c.id, p.id); },
            { icon: ti(p.icon) || '📄', hint: ancestors(p.id).map(label).join(' / ') }));
        });
      } }));
      m.appendChild(HK.menuItem(c.template ? T('Keine Vorlage mehr') : T('Als Vorlage markieren'), function () { setTemplate(!c.template); }, { icon: '📐', on: !!c.template }));
      m.appendChild(el('div', 'menu-sep'));
      if (window.HKGraph) HKGraph.menu(m);                           // static/graph.js: lokaler Graph dieser Seite
      m.appendChild(HK.menuItem(T('Versionsverlauf'), openVersions, { icon: '🕘' }));
      var shi = HK.menuItem(T('Teilen …'), openShare, { icon: ' ', on: !!c.shared }), shc = shi.querySelector('.mi-ic');
      if (shc) { shc.textContent = ''; shc.appendChild(grpIc()); }                    // einfarbiges Gruppen-Symbol
      m.appendChild(shi);
      m.appendChild(HK.menuItem(T('Kommentare'), function () { if (window.HKComments) HKComments.openAll(); },
        { icon: '💬', hint: c.comments_resolved ? T('{n} erledigt', { n: c.comments_resolved }) : '' }));
      if (window.HKAI) HKAI.menu(m);                                 // static/ai.js (nur, wenn der Assistent an ist)
      m.appendChild(HK.menuItem(T('Veröffentlichen …'), openPublish, { icon: '🌐', on: !!c.published }));
      m.appendChild(HK.menuItem(T('Exportieren'), null, { icon: '⬇', sub: function (sm) {
        sm.appendChild(HK.menuItem(T('Als Markdown (.md)'), function () { exportPage(false); }, { icon: '📄' }));
        if (n) sm.appendChild(HK.menuItem(T('Mit Unterseiten (.zip)'), function () { exportPage(true); }, { icon: '🗜', hint: nSub(n) }));
      } }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(c.archived ? T('Aus dem Archiv holen') : T('Archivieren'), function () { setArchived(c.id, !c.archived); },
        { icon: '📦', hint: !c.archived && n ? '+ ' + nSub(n) : '' }));
      m.appendChild(HK.menuItem(T('In den Papierkorb'), function () {
        var id = S.cur.id, name = label(S.cur), parent = S.cur.parent_id;
        save().then(function () { return api('DELETE', '/api/notes/' + id); }).then(function () {
          if (EMBED) {                     // im Peek: nicht zur uebergeordneten Seite springen - Peek schliessen (macht das Hauptfenster)
            S.cur = null; window.parent.postMessage({ hk: 'peek-deleted', id: id, name: name }, location.origin); return;
          }
          S.cur = null; $('page').hidden = true; setLocation(); history.replaceState(null, '', location.pathname);
          HK.toast(T('„{name}“ in den Papierkorb verschoben', { name: name }), T('Rückgängig'), function () {
            api('POST', '/api/trash/' + id + '/restore').then(loadList).then(function () { return openPage(id); })
              .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
          });
          return loadList().then(function () { if (parent && byId(parent)) return openPage(parent); renderTree(); });
        }).catch(function (err) { HK.toast(T('Nicht gelöscht: {err}', { err: err.message })); });
      }, { icon: '🗑', danger: true, hint: n ? '+ ' + nSub(n) : '' }));
    });
  });

  // Seitenmenue auf einer fremden Seite: Breite/Unterseite/Verschieben/Versionen nur mit 'Bearbeiten'; Link, Kommentare
  // und Export fuer alle. Seiten anderer Saga-Server (2.3): je nach dem, was ihr Server kann (remCap)
  function foreignMenu(m, c) {
    m.appendChild(HK.menuLabel(T('Geteilt von {name} · {role}', { name: (c.owner || {}).name || '', role: ROLE_YOU[role()] || '' })));
    var rem = remoteId(c.id);
    if (canEdit()) {
      if (remCap(c, 'db')) m.appendChild(HK.menuItem(T('Volle Breite'), function () { setWide(!c.wide); }, { icon: '↔', on: !!c.wide }));
      if (remCap(c, 'db')) designMenu(m, c);
      if (remCap(c, 'subpages')) {
        m.appendChild(HK.menuItem(T('Unterseite hinzufügen'), function () { newPage(c.id); }, { icon: '＋' }));
        var cand = sharedMoveTargets(c);
        if (cand.length) m.appendChild(HK.menuItem(T('Verschieben nach'), null, { icon: '↪', subSearch: T('Seite suchen …'), sub: function (sm) {
          cand.forEach(function (p) {
            sm.appendChild(HK.menuItem(label(p), function () { moveFromMenu(c.id, p.id); }, { icon: ti(p.icon) || '📄', hint: ancestors(p.id).map(label).join(' / ') }));
          });
          sm.appendChild(el('div', 'menu-hint', T('Nur innerhalb des geteilten Bereichs mit denselben Freigaben.')));
        } }));
      }
      if (remCap(c, 'versions')) m.appendChild(HK.menuItem(T('Versionsverlauf'), openVersions, { icon: '🕘' }));
    }
    m.appendChild(HK.menuItem(T('Kommentare'), function () { if (window.HKComments) HKComments.openAll(); },
      { icon: '💬', hint: c.comments_resolved ? T('{n} erledigt', { n: c.comments_resolved }) : '' }));
    m.appendChild(HK.menuItem(T('Link zur Seite kopieren'), function () { copyLink(); }, { icon: '🔗' }));
    if (window.HKGraph && !rem) HKGraph.menu(m);       // Graph kennt nur Seiten dieses Servers
    if (window.HKAI && !rem) HKAI.menu(m);            // KI nur fuer Seiten dieses Servers (andere Instanz: 404, BUG-052)
    var n = descendants(c.id).length || S.shared.filter(function (p) { return p.parent_id === c.id; }).length;
    if (remCap(c, 'export')) m.appendChild(HK.menuItem(T('Exportieren'), null, { icon: '⬇', sub: function (sm) {
      sm.appendChild(HK.menuItem(T('Als Markdown (.md)'), function () { exportPage(false); }, { icon: '📄' }));
      if (n) sm.appendChild(HK.menuItem(T('Mit Unterseiten (.zip)'), function () { exportPage(true); }, { icon: '🗜' }));
    } }));
    m.appendChild(el('div', 'menu-hint', rem && !remCap(c, 'db') ? T('Diese Seite liegt auf {host}. Hier kannst du sie lesen, kommentieren und – mit Bearbeiten – Titel und Inhalt ändern.', { host: (c.remote || {}).host || '' })
                                             : rem ? T('Diese Seite liegt auf {host}. Löschen und Freigaben verwaltet der Eigentümer.', { host: (c.remote || {}).host || '' })
                                             : T('Löschen und Freigaben verwaltet der Eigentümer.')));
  }
  // Verschieben fuer Bearbeiter (2.3): Ziele im selben geteilten Baum (gleicher Eigentuemer, gleicher Server), keine
  // Datenbanken, nicht unter sich selbst - ob die Freigaben gleich sind, prueft der Server (sonst 403 mit Hinweis)
  function sharedMoveTargets(c) {
    var blocked = descendants(c.id).concat([c.id, c.parent_id]), ou = (c.owner || {}).username, host = (c.remote || {}).host;
    return S.shared.filter(function (p) {
      return blocked.indexOf(p.id) < 0 && p.kind !== 'database' && p.role === 'edit' && (p.owner || {}).username === ou
        && ((p.remote || {}).host || null) === (host || null) && remoteId(p.id) === remoteId(c.id);
    });
  }

  function setFull(on) {
    $('pages').classList.toggle('pg-full', on);
    document.body.classList.toggle('pg-full-open', on);
    $('pg-full').setAttribute('aria-pressed', on ? 'true' : 'false');
    $('pg-full').title = on ? T('Vollbild beenden (Esc)') : T('Vollbild');
  }
  $('pg-full').addEventListener('click', function () { setFull(!$('pages').classList.contains('pg-full')); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && $('pages').classList.contains('pg-full') && !document.querySelector('.menu:not([hidden])')
        && !document.querySelector('.ce-popover--opened')) { setFull(false); }
  });

  // --- Suche ----------------------------------------------------------------
  var qT = null;
  $('pg-search').addEventListener('input', function () {
    var q = this.value.trim(); clearTimeout(qT);
    if (!q) { $('pg-results').hidden = true; $('pg-tree').hidden = false; renderFiltered(); return; }
    qT = setTimeout(function () {
      api('POST', '/api/notes/search', { q: q }).then(function (hits) {
        var ul = $('pg-results'); ul.textContent = ''; ul.hidden = false; $('pg-tree').hidden = true;
        if (!hits.length) { ul.appendChild(el('li', 'pg-noresult', T('Nichts gefunden.'))); return; }
        hits.forEach(function (h) {
          var b = el('button', 'pg-hit'); b.type = 'button';
          b.appendChild(el('span', 'pg-hit-t', (ti(h.icon) || '📄') + ' ' + label(h)));
          if (h.owner && !h.owner.me) b.appendChild(el('span', 'pg-hit-o', T('Geteilt von {name}', { name: h.owner.name })));
          if (h.snippet) b.appendChild(el('span', 'pg-hit-s', h.snippet));
          b.addEventListener('click', function () { $('pg-search').value = ''; ul.hidden = true; $('pg-tree').hidden = false; openPage(h.id); });
          var li = el('li'); li.appendChild(b); ul.appendChild(li);
        });
      }).catch(function () {});
    }, 250);
  });

  $('pg-new').addEventListener('click', function () { newPageMenu(this, null); });
  $('pg-empty-new').addEventListener('click', function () { newPageMenu(this, null); });
  window.addEventListener('pagehide', function () { save(); checkpoint('leave'); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) { save(); checkpoint('leave'); } });
  // Neu laden / andere Adresse kurz nach dem Tippen (BUG-012): schon bei beforeunload speichern - HK.api schickt es dann mit
  // keepalive, die Anfrage ueberlebt das Entladen. Laeuft gerade noch ein Speichern (die naechste Anfrage muss darauf warten)
  // oder ist der Inhalt zu gross fuer keepalive, fragt der Browser vor dem Verlassen nach.
  window.addEventListener('beforeunload', function (e) {
    if (!S.cur || S.conflict || S.lost || readOnly() || (!S.dirtyBody && !S.dirtyTitle)) return;
    var busy = (S.inflight || 0) > 0;
    save();
    var txt = $('pg-editor').textContent, big = (window.TextEncoder ? new TextEncoder().encode(txt).length : txt.length * 3) > 40000;   // Bytes (CJK/Emoji)
    if (busy || big) { e.preventDefault(); e.returnValue = ''; }
  });


  // --- Tags -----------------------------------------------------------------
  S.tagFilter = null;
  function allTags() {
    var m = {};
    S.pages.forEach(function (p) { tagsOf(p).forEach(function (t) { var k = t.toLowerCase(); (m[k] = m[k] || { name: t, n: 0 }).n++; }); });
    return Object.keys(m).map(function (k) { return m[k]; }).sort(function (a, b) { return b.n - a.n || a.name.localeCompare(b.name, HK.locale); });
  }
  function renderTagBar() {
    var bar = $('pg-tagbar'); bar.textContent = '';
    var tags = allTags(); bar.hidden = !tags.length;
    tags.forEach(function (t) {
      var on = S.tagFilter && S.tagFilter.toLowerCase() === t.name.toLowerCase();
      var b = el('button', 'pg-tag' + (on ? ' on' : ''), '#' + t.name); b.type = 'button';
      b.appendChild(el('span', 'pg-tag-n', String(t.n)));
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      b.addEventListener('click', function () { S.tagFilter = on ? null : t.name; renderTagBar(); renderFiltered(); });
      bar.appendChild(b);
    });
    var dl = $('pg-tag-list'); dl.textContent = '';
    tags.forEach(function (t) { var o = document.createElement('option'); o.value = t.name; dl.appendChild(o); });
  }
  function renderFiltered() {   // Tag-Filter: flache Liste statt Baum
    if ($('pg-search').value.trim()) return;           // Suche hat Vorrang
    var ul = $('pg-results');
    if (!S.tagFilter) { ul.hidden = true; $('pg-tree').hidden = false; return; }
    var f = S.tagFilter.toLowerCase();
    var hits = S.pages.filter(function (p) { return tagsOf(p).some(function (t) { return t.toLowerCase() === f; }); });
    ul.textContent = ''; ul.hidden = false; $('pg-tree').hidden = true;
    hits.forEach(function (h) {
      var b = el('button', 'pg-hit'); b.type = 'button';
      b.appendChild(el('span', 'pg-hit-t', (ti(h.icon) || '📄') + ' ' + label(h)));
      b.addEventListener('click', function () { openPage(h.id); });
      var li = el('li'); li.appendChild(b); ul.appendChild(li);
    });
  }
  function renderTags() {
    var box = $('pg-tags'); box.textContent = '';
    if (!S.cur) return;
    tagsOf(S.cur).forEach(function (t) {
      var c = el('span', 'tag', '#' + t);
      if (!roRole()) {
        var x = el('button', null, '×'); x.type = 'button'; x.setAttribute('aria-label', T('Tag {tag} entfernen', { tag: t }));
        x.addEventListener('click', function () { setTags(tagsOf(S.cur).filter(function (y) { return y !== t; })); });
        c.appendChild(x);
      }
      box.appendChild(c);
    });
    if (roRole()) return;
    var add = el('button', 'tag-add', T('+ Tag')); add.type = 'button';
    add.addEventListener('click', function () {
      var inp = el('input', 'tag-input'); inp.type = 'text'; inp.maxLength = 30; inp.placeholder = T('Tag …');
      inp.setAttribute('list', 'pg-tag-list'); inp.setAttribute('aria-label', T('Neuer Tag'));
      add.replaceWith(inp); inp.focus();
      var done = false;
      function finish(ok) {
        if (done) return; done = true;
        var v = inp.value.trim().replace(/^#/, '');
        if (ok && v) setTags(tagsOf(S.cur).concat([v])); else renderTags();
      }
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
      });
      inp.addEventListener('blur', function () { finish(true); });
    });
    box.appendChild(add);
  }
  function setTags(tags) {
    if (!S.cur) return;
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { tags: tags }).then(function () {
      return api('GET', '/api/notes/' + id);       // Server normalisiert (Dubletten, #, Leerzeichen)
    }).then(function (n) {
      if (S.cur && S.cur.id === id) S.cur.tags = n.tags;
      var p = byId(id); if (p) p.tags = n.tags;
      renderTags(); renderTagBar(); renderFiltered();
    }).catch(function (err) { HK.toast(T('Tags nicht gespeichert: {err}', { err: err.message })); renderTags(); });
  }

  // --- Verweise auf Seiten und Dienste ---------------------------------------
  S.services = [];
  var NOTE_HREF = /^\/notes#(-?[1-9][0-9]{0,17})$/;          // negativ: Seite einer anderen Saga-Instanz
  function normUrl(u) { return String(u || '').replace(/\/+$/, ''); }
  function serviceFor(href) { var n = normUrl(href); return S.services.find(function (s) { return normUrl(s.url) === n; }) || null; }

  // Nach dem Laden: Verweise als Marken darstellen, Seitentitel aktualisieren, tote Ziele markieren
  function decorateLinks(root) {
    [].forEach.call((root || $('pg-editor')).querySelectorAll('a[href]'), function (a) {
      var m = (a.getAttribute('href') || '').match(NOTE_HREF);
      if (m) {
        var p = byId(+m[1]), na = !p && foreign();          // fremde Seite: Ziel nicht freigegeben -> Titel nicht zeigen
        a.classList.add('note-link');
        a.classList.toggle('broken', !p && !na);
        a.classList.toggle('noaccess', na);
        if (na) { a.dataset.na = T('Seite ohne Zugriff'); a.setAttribute('aria-label', a.dataset.na); } else { delete a.dataset.na; a.removeAttribute('aria-label'); }
        a.title = p ? T('Zur Seite {name}', { name: label(p) }) : na ? T('Seite ohne Zugriff') : T('Seite nicht gefunden');
        if (p) a.textContent = (ti(p.icon) ? ti(p.icon) + ' ' : '') + label(p);
      } else if (serviceFor(a.getAttribute('href'))) {
        a.classList.add('svc-link'); a.title = T('Dienst öffnen');
      }
    });
  }

  // Klick auf Verweise im Editor: Seitenverweise und Dienste oeffnen direkt,
  // normale Links nur mit Strg/Cmd (sonst wird der Text bearbeitet)
  function linkClick(e) {
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    var href = a.getAttribute('href'), m = href.match(NOTE_HREF), mod = e.ctrlKey || e.metaKey;
    if (m) {
      e.preventDefault();
      if (!byId(+m[1])) { HK.toast(foreign() ? T('Seite ohne Zugriff') : T('Diese Seite gibt es nicht mehr.')); return; }
      if (mod) window.open('/notes#' + m[1], '_blank', 'noopener'); else location.hash = '#' + m[1];
    } else if (a.classList.contains('svc-link') || mod) {
      e.preventDefault(); window.open(href, '_blank', 'noopener');
    }
  }
  $('pg-editor').addEventListener('click', linkClick, true);

  // Klick in die Flaeche unter dem letzten Block (Web-Runde 2, M2): Cursor in den letzten Block, wenn er ein leerer Absatz
  // ist, sonst neuen Absatz darunter - Getipptes geht sonst ins Leere
  $('pg-editor').closest('article').addEventListener('mouseup', function (e) {
    var ed = S.editor;
    if (!ed || !ed.blocks || e.button !== 0 || document.body.classList.contains('pg-locked') || $('pg-editor').hidden) return;
    if (e.target.closest('.ce-block, .ce-toolbar, .ce-popover, .ce-inline-toolbar, input, textarea, button, a, select, .backlinks, .db, .props, .page-head')) return;
    var sel = window.getSelection();
    if (sel && sel.rangeCount && !sel.isCollapsed) return;
    var blocks = $('pg-editor').querySelectorAll('.codex-editor__redactor > .ce-block');
    var last = blocks[blocks.length - 1];
    if (!last || e.clientY < last.getBoundingClientRect().bottom) return;
    var ae = document.activeElement;
    if (ae && ae.isContentEditable && $('pg-editor').contains(ae)) return;     // Editor.js hat den Cursor schon gesetzt
    var n = ed.blocks.getBlocksCount(), b = ed.blocks.getBlockByIndex(n - 1);
    try {
      if (b && b.name === 'paragraph' && b.isEmpty) ed.caret.setToBlock(n - 1, 'end');
      else { ed.blocks.insert('paragraph', {}, {}, n, true); ed.caret.setToBlock(n, 'start'); }
    } catch (err) { /* Editor gerade im Umbau */ }
  });

  // "[[" oeffnet die Auswahl
  var pick = { range: null, items: [], idx: 0 };
  // a) "/"-Menue -> "Verweis": Editor.js-Blockwerkzeug, das sich sofort in einen Absatz
  //    verwandelt und dort die Auswahl oeffnet (der Verweis landet im Absatz, nicht als eigener Block)
  function RefTool() {}
  RefTool.toolbox = { title: T('Verweis'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>' };
  RefTool.prototype.render = function () {
    var d = el('div', 'ce-paragraph cdx-block');
    if (!S.loading) setTimeout(function () { refFromToolbox(d); }, 0);
    return d;
  };
  RefTool.prototype.save = function () { return { text: '' }; };
  function refFromToolbox(marker) {
    var ed = S.editor, idx = -1;
    for (var i = 0; i < ed.blocks.getBlocksCount(); i++) { if (ed.blocks.getBlockByIndex(i).holder.contains(marker)) { idx = i; break; } }
    if (idx < 0) return;
    ed.blocks.insert('paragraph', { text: '' }, undefined, idx, true, true);   // Werkzeug-Block -> leerer Absatz
    ed.caret.setToBlock(idx, 'start');
    var p = ed.blocks.getBlockByIndex(idx).holder.querySelector('.ce-paragraph');
    var r = document.createRange(); r.selectNodeContents(p); r.collapse(true);
    pick.range = r;
    openPicker(p.getBoundingClientRect());
  }

  // b) Rechtsklick im Text -> kleines Menue "Verweis einfuegen …" an der geklickten Stelle.
  //    Shift + Rechtsklick zeigt das normale Browser-Menue (Rechtschreibung, Kopieren …).
  function rangeAt(x, y) {
    if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
    var pos = document.caretPositionFromPoint && document.caretPositionFromPoint(x, y);
    if (!pos) return null;
    var r = document.createRange(); r.setStart(pos.offsetNode, pos.offset); r.collapse(true); return r;
  }
  var ctxAnchor = el('div', 'ctx-anchor'); document.body.appendChild(ctxAnchor);
  $('pg-editor').addEventListener('contextmenu', function (e) {
    if (e.shiftKey || S.loading) return;
    var editable = e.target.closest && e.target.closest('[contenteditable="true"]');
    if (!editable || !$('pg-editor').contains(editable)) return;            // nur im Fliesstext, nicht im Code-Block o. Ae.
    e.preventDefault();
    var sel = window.getSelection(), r = null;
    if (sel.rangeCount && !sel.isCollapsed && editable.contains(sel.anchorNode)) r = sel.getRangeAt(0).cloneRange();   // Markierung ersetzen
    else r = rangeAt(e.clientX, e.clientY);
    if (!r || !editable.contains(r.startContainer)) { r = document.createRange(); r.selectNodeContents(editable); r.collapse(false); }
    pick.range = r;
    ctxAnchor.style.left = e.clientX + 'px'; ctxAnchor.style.top = e.clientY + 'px';
    HK.openMenu(ctxAnchor, function (m) {
      m.appendChild(HK.menuItem(r.collapsed ? T('Verweis einfügen …') : T('Markierung durch Verweis ersetzen …'), function () {
        openPicker({ left: e.clientX, right: e.clientX, top: e.clientY, bottom: e.clientY });
      }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(el('div', 'menu-hint', T('Browser-Menü: Shift + Rechtsklick')));
    });
  });

  // --- Bilder kopieren / ausschneiden / einfuegen (2.1) ----------------------------------------------------------
  // Editor.js kennt keine Auswahl EINES Bildblocks: Klick aufs Bild markierte nichts, Strg/⌘+C kopierte leer (nur
  // ⌘A ⌘A bzw. Ziehen ueber mehrere Bloecke ging). Jetzt: Klick aufs Bild markiert den Block; Strg/⌘+C bzw. +X legt
  // ihn in die Zwischenablage - als Editor.js-Block (Bildunterschrift/Optionen bleiben), als HTML (<img> mit dem Block
  // in data-saga-block) und, wo der Browser es erlaubt (Async Clipboard), zusaetzlich als image/png fuer andere
  // Programme. Einfuegen in Saga (gleiche/andere Seite, anderer Tab): Dateiverweise /files/<id> laufen ueber
  // /api/files/copy - gleiche Referenz, wenn die Zielseite demselben Konto gehoert, sonst eine Kopie fuer den
  // Eigentuemer der Zielseite (Rechte: Quelle sehen, Ziel bearbeiten). Gilt auch fuer per ⌘A kopierte Bloecke.
  var picked = null;                                     // {blk: .ce-block, ed, data}
  var FILE_REF = /\/files\/[0-9a-f]{24}/g;
  function edOf(node) {
    var best = null;
    (S.eds || []).forEach(function (x) { if (x.ed && x.holder.isConnected && x.holder.contains(node) && (!best || best.holder.contains(x.holder))) best = x; });
    return best ? best.ed : S.editor;
  }
  function unpick() { if (picked && picked.blk) picked.blk.classList.remove('pg-picked'); picked = null; }
  function imgData(blk) {                                // Fallback aus dem DOM, falls block.save() noch laeuft
    var im = blk.querySelector('.image-tool__image-picture'), cap = blk.querySelector('.image-tool__caption'), tool = blk.querySelector('.image-tool');
    var u = im ? String(im.getAttribute('src') || '') : '';
    try { var x = new URL(u, location.href); if (x.origin === location.origin) u = x.pathname; } catch (e) { /* lassen */ }
    var has = function (c) { return !!(tool && tool.classList.contains('image-tool--' + c)); };
    return { file: { url: u }, caption: cap ? cap.innerHTML : '', withBorder: has('withBorder'), withBackground: has('withBackground'), stretched: has('stretched') };
  }
  document.addEventListener('mousedown', function (e) {
    var t = e.target, box = $('pg-editor');
    var im = t && t.closest && t.closest('.image-tool__image');
    if (!im || !box || !box.contains(im)) { if (picked && !(t && t.closest && t.closest('.menu, .ce-popover'))) unpick(); return; }
    var blk = im.closest('.ce-block'), ed = edOf(blk);
    if (!blk || !ed) return;
    unpick();
    picked = { blk: blk, ed: ed, data: null };
    blk.classList.add('pg-picked');
    var b = blk.dataset.id && ed.blocks.getById ? ed.blocks.getById(blk.dataset.id) : null;
    var me = picked;
    if (b && b.save) b.save().then(function (s) { if (picked === me && s && s.data) me.data = s.data; }).catch(function () {});
  }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && picked) unpick(); });
  function pickedBlock() { return picked && picked.blk.isConnected ? picked : (unpick(), null); }
  function clipHtml(data) {
    var u = data.file && data.file.url ? data.file.url : '', abs = /^\//.test(u) ? location.origin + u : u;
    var cap = el('div'); cap.innerHTML = data.caption || '';
    var img = el('img'); img.src = abs; img.alt = cap.textContent.trim();
    img.setAttribute('data-saga-block', JSON.stringify([{ tool: 'image', data: data }]));
    return { html: img.outerHTML, text: abs };
  }
  function pngOf(url) {                                    // fuer andere Programme: Chrome/Safari nehmen nur image/png
    return fetch(url + (/^\/files\//.test(url) ? '?w=2000' : ''), { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); })
      .then(function (b) {
        if (b.type === 'image/png') return b;
        return createImageBitmap(b).then(function (bm) {
          var cv = document.createElement('canvas'); cv.width = bm.width; cv.height = bm.height; cv.getContext('2d').drawImage(bm, 0, 0);
          return new Promise(function (res, rej) { cv.toBlob(function (o) { if (o) res(o); else rej(new Error('png')); }, 'image/png'); });
        });
      });
  }
  function onClip(e) {
    var p = pickedBlock(); if (!p) return;
    var a = document.activeElement;
    if (a && a !== document.body && (a.isContentEditable || /^(INPUT|TEXTAREA)$/.test(a.tagName)) && !p.blk.contains(a)) return;   // Text woanders markiert
    var data = p.data || imgData(p.blk);
    if (!data.file || !data.file.url) return;
    var c = clipHtml(data), dt = e.clipboardData;
    e.preventDefault(); e.stopImmediatePropagation();
    dt.setData('text/plain', c.text); dt.setData('text/html', c.html);
    dt.setData('application/x-editor-js', JSON.stringify([{ tool: 'image', data: data }]));
    // Zusaetzlich als Bild fuer andere Programme (ersetzt den Inhalt oben; HTML mit data-saga-block bleibt dabei)
    if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
      try {
        var item = new ClipboardItem({ 'image/png': pngOf(data.file.url), 'text/html': new Blob([c.html], { type: 'text/html' }),
                                       'text/plain': new Blob([c.text], { type: 'text/plain' }) });
        navigator.clipboard.write([item]).catch(function () { /* nicht erlaubt: Text/HTML/Block von oben bleiben */ });
      } catch (err) { /* aeltere Browser */ }
    }
    if (e.type === 'cut' && !readOnly()) {
      var i = p.ed.blocks.getBlockIndex ? p.ed.blocks.getBlockIndex(p.blk.dataset.id) : -1;
      unpick();
      if (i >= 0) { p.ed.blocks.delete(i); S.dirtyBody = true; schedule(); }
    }
    HK.toast(e.type === 'cut' ? T('Bild ausgeschnitten') : T('Bild kopiert'));
  }
  document.addEventListener('copy', onClip, true);
  document.addEventListener('cut', onClip, true);
  // Einfuegen: Bloecke mit Dateiverweisen selbst einfuegen (Verweise ueber den Server pruefen/kopieren), Rest wie bisher
  function clipBlocks(dt) {
    var x = dt.getData('application/x-editor-js'), out = null;
    if (x) { try { out = JSON.parse(x); } catch (e) { out = null; } }
    if (!out) {
      var h = dt.getData('text/html');
      if (h && h.indexOf('data-saga-block') >= 0) {
        var n = new DOMParser().parseFromString(h, 'text/html').querySelector('[data-saga-block]');
        try { out = n ? JSON.parse(n.getAttribute('data-saga-block')) : null; } catch (e) { out = null; }
      }
    }
    if (!Array.isArray(out) || !out.length || !out.every(function (b) { return b && typeof b.tool === 'string' && b.data && typeof b.data === 'object'; })) return null;
    return JSON.stringify(out).match(FILE_REF) ? out : null;          // ohne Dateiverweise: Editor.js wie gewohnt
  }
  // Bloecke aus der Zwischenablage gehen an ed.blocks.insert, und das bereinigt NICHTS (anders als das Einfuegen durch
  // Editor.js selbst). Deshalb vorher: nur Block-Werkzeuge, die der Ziel-Editor kennt (Spalten in Spalten/Ausklappbar
  // werden aufgeloest wie beim Speichern), Inline-HTML nach derselben Positivliste wie der Server (saga/pages.py,
  // _Sanitizer), Adressen nur relativ/http(s)/mailto, verschachtelte Bloecke (Ausklappbar, Spalten) ebenso.
  var CLIP_INLINE_ONLY = { inlineCode: 1, marker: 1, hkdate: 1, hkcomment: 1, hkstyle: 1, hkdup: 1, hklink: 1 };
  var CLIP_TAGS = { b: 1, strong: 1, i: 1, em: 1, u: 1, s: 1, mark: 1, code: 1, a: 1, br: 1, time: 1 };
  var CLIP_CLASSES = { mark: ['cdx-marker'], code: ['inline-code'], time: ['hk-date', 'hk-remind'] };
  var CLIP_DROP = /^(script|style|template|iframe|frame|frameset|object|embed|noscript|svg|math|textarea|select|button|form|title|head|link|meta)$/;
  var CLIP_URL_KEYS = { url: 1, href: 1, link: 1, source: 1, embed: 1, src: 1 };
  function clipEsc(v) { return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function clipInline(s) {
    if (s.indexOf('<') < 0 && s.indexOf('&') < 0) return s;
    var doc = new DOMParser().parseFromString('<!doctype html><body>' + s, 'text/html'), out = [];   // inertes Dokument
    (function walk(n, inA) {
      [].forEach.call(n.childNodes, function (c) {
        if (c.nodeType === 3) { out.push(clipEsc(c.nodeValue).replace(/&quot;/g, '"')); return; }
        if (c.nodeType !== 1) return;
        var tg = c.tagName.toLowerCase(), keep = '';
        if (CLIP_DROP.test(tg)) return;
        if (!CLIP_TAGS[tg] || (tg === 'a' && inA)) { walk(c, inA); return; }
        if (tg === 'br') { out.push('<br>'); return; }
        if (tg === 'a') {
          var h = (c.getAttribute('href') || '').trim();
          if (/^\/notes#[1-9][0-9]{0,17}$/.test(h)) keep = ' href="' + h + '"';
          else if (/^(https?:|mailto:)/i.test(h)) keep = ' href="' + clipEsc(h) + '" target="_blank" rel="noopener noreferrer"';
          else { walk(c, inA); return; }
        }
        if (tg === 'time') {
          var dt = (c.getAttribute('datetime') || '').trim();
          if (!/^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$/.test(dt)) { walk(c, inA); return; }
          keep = ' datetime="' + dt + '"';
        }
        var cls = (c.getAttribute('class') || '').split(/\s+/).filter(function (x) { return (CLIP_CLASSES[tg] || []).indexOf(x) >= 0; });
        if (cls.length) keep += ' class="' + cls.join(' ') + '"';
        out.push('<' + tg + keep + '>'); walk(c, inA || tg === 'a'); out.push('</' + tg + '>');
      });
    })(doc.body, false);
    return out.join('');
  }
  function clipVal(v, key, depth) {
    if (depth > 40) return null;
    if (typeof v === 'string') {
      if (key === 'code') return v;                                      // Code-Bloecke sind Klartext (wie der Server)
      if (CLIP_URL_KEYS[key]) { var u = v.trim(); return /^(\/(?!\/)|https?:\/\/|mailto:)/i.test(u) ? u : ''; }
      return clipInline(v);
    }
    if (Array.isArray(v)) return v.slice(0, 2000).map(function (x) { return clipVal(x, key, depth + 1); });
    if (v && typeof v === 'object') {
      var o = {};
      Object.keys(v).forEach(function (k) { if (k !== '__proto__' && k !== 'constructor' && k !== 'prototype') o[k] = clipVal(v[k], k, depth + 1); });
      return o;
    }
    return (typeof v === 'number' && isFinite(v)) || typeof v === 'boolean' || v === null ? v : null;
  }
  function toolsOf(ed) {
    if (ed === S.editor) return S.tools || {};
    var x = (S.eds || []).filter(function (e) { return e.ed === ed; })[0];
    return (x && x.tools) || nestedTools();
  }
  function cleanClip(list, tools, depth) {                // [{tool|type, data}] -> [{tool, data}] (nur Bekanntes, bereinigt)
    depth = depth || 0;
    var out = [];
    if (!Array.isArray(list) || depth > 3) return out;
    list.slice(0, 500).forEach(function (b) {
      if (!b || typeof b !== 'object') return;
      var name = typeof b.tool === 'string' ? b.tool : b.type, d = b.data;
      if (typeof name !== 'string' || !d || typeof d !== 'object' || Array.isArray(d)) return;
      if (CLIP_INLINE_ONLY[name] || (name !== 'paragraph' && !Object.prototype.hasOwnProperty.call(tools, name))) {   // Absatz: Editor.js-Standard
        if (name === 'columns' && Array.isArray(d.cols)) {            // Spalten in Spalte/Ausklappbar: Inhalt aufloesen
          d.cols.forEach(function (c) { out.push.apply(out, cleanClip(c && c.blocks, tools, depth + 1)); });
        }
        return;
      }
      var data = clipVal(d, null, 0), inner = nestedTools();
      if (name === 'toggle') data.blocks = cleanClip(d.blocks, inner, depth + 1).map(function (x) { return { type: x.tool, data: x.data }; });
      if (name === 'columns') {
        data.cols = (Array.isArray(d.cols) ? d.cols : []).slice(0, 4).map(function (c) {
          return { blocks: cleanClip(c && c.blocks, inner, depth + 1).map(function (x) { return { type: x.tool, data: x.data }; }) };
        });
      }
      out.push({ tool: name, data: data });
    });
    return out;
  }
  function fixRefs(blocks) {
    var s = JSON.stringify(blocks.map(function (b) { return { tool: b.tool, data: b.data }; })), refs = {};
    (s.match(FILE_REF) || []).forEach(function (r) { refs[r] = 1; });
    return Promise.all(Object.keys(refs).map(function (r) {
      return api('POST', '/api/files/copy', { url: r, note: S.cur ? S.cur.id : null }).then(function (d) { refs[r] = d.url; });
    })).then(function () { return JSON.parse(s.replace(FILE_REF, function (r) { return refs[r]; })); });
  }
  document.addEventListener('paste', function (e) {
    var box = $('pg-editor'), t = e.target, p = pickedBlock();
    var inEd = box && t && t.nodeType === 1 && box.contains(t);
    if ((!inEd && !p) || !S.cur || readOnly() || !e.clipboardData) return;
    var blocks = clipBlocks(e.clipboardData);
    if (!blocks) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var ed = p ? p.ed : edOf(t), anchorId = p ? p.blk.dataset.id : null, cid = S.cur.id;
    fixRefs(blocks).then(function (bl) {
      if (!S.cur || S.cur.id !== cid) return;
      bl = cleanClip(bl, toolsOf(ed));
      if (!bl.length) return;
      var i, replace = false;
      if (anchorId && ed.blocks.getBlockIndex) i = ed.blocks.getBlockIndex(anchorId) + 1;
      else {
        i = ed.blocks.getCurrentBlockIndex();
        var cur = i >= 0 ? ed.blocks.getBlockByIndex(i) : null;
        if (cur && cur.isEmpty && cur.name === 'paragraph') replace = true; else i = i < 0 ? ed.blocks.getBlocksCount() : i + 1;
      }
      bl.forEach(function (b, k) {
        ed.blocks.insert(b.tool, b.data, undefined, i + k, k === bl.length - 1, k === 0 && replace);
      });
      unpick(); S.dirtyBody = true; schedule();
    }).catch(function (err) { HK.toast(T('Einfügen fehlgeschlagen: {err}', { err: err.message })); });
  }, true);

  function openPicker(rect) {
    var pk = $('pg-picker'); pk.hidden = false;
    $('pg-picker-q').value = ''; renderPicker();
    // Erst rendern, dann mit der TATSAECHLICHEN Hoehe entscheiden: unter dem Text, sonst darueber
    var h = pk.offsetHeight, below = rect.bottom + 6, above = rect.top - 6 - h;
    var top = (below + h <= window.innerHeight - 8 || above < 8) ? Math.min(below, window.innerHeight - h - 8) : above;
    pk.style.top = Math.max(8, top) + 'px';
    pk.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - pk.offsetWidth - 8)) + 'px';
    $('pg-picker-q').focus();
  }
  function closePicker(restore) {
    $('pg-picker').hidden = true;
    if (restore && pick.range) {
      var c = pick.range.startContainer, h = (c.nodeType === 3 ? c.parentElement : c).closest('[contenteditable="true"]');
      if (h) h.focus();
      var s = window.getSelection(); s.removeAllRanges(); s.addRange(pick.range);
    }
  }
  function renderPicker() {
    var q = $('pg-picker-q').value.trim().toLowerCase(), list = $('pg-picker-list');
    list.textContent = ''; pick.items = []; pick.idx = 0;
    var pages = allPages().filter(function (p) { return (!S.cur || p.id !== S.cur.id) && (!q || label(p).toLowerCase().indexOf(q) >= 0); }).slice(0, 8);
    var svcs = S.services.filter(function (s) { return !q || (s.name + ' ' + s.host).toLowerCase().indexOf(q) >= 0; }).slice(0, 8);
    function item(text, sub, fn) {
      var b = el('button', 'picker-item'); b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(el('span', 'picker-t', text)); if (sub) b.appendChild(el('span', 'picker-sub', sub));
      b.addEventListener('mousedown', function (e) { e.preventDefault(); });   // Fokus nicht verlieren
      b.addEventListener('click', fn); pick.items.push({ b: b, fn: fn }); list.appendChild(b);
    }
    if (pages.length) list.appendChild(el('div', 'menu-label', T('Seiten')));
    pages.forEach(function (p) { item((ti(p.icon) || '📄') + ' ' + label(p), '', function () { insertLink('/notes#' + p.id, (ti(p.icon) ? ti(p.icon) + ' ' : '') + label(p), 'note-link'); }); });
    if (svcs.length) list.appendChild(el('div', 'menu-label', T('Dienste')));
    svcs.forEach(function (s) { item(s.name, s.host, function () { insertLink(s.url, s.name, 'svc-link'); }); });
    if (q) {
      list.appendChild(el('div', 'menu-label', T('Neu')));
      item(T('+ Neue Seite „{name}“', { name: $('pg-picker-q').value.trim() }), '', function () {
        var title = $('pg-picker-q').value.trim();
        api('POST', '/api/notes', { title: title, parent_id: S.cur ? S.cur.id : null }).then(function (d) {
          if (S.cur) { S.open[S.cur.id] = true; saveOpen(); }
          return loadList().then(function () { insertLink('/notes#' + d.id, '📄 ' + title, 'note-link'); });
        }).catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
      });
    }
    if (!pick.items.length) list.appendChild(el('div', 'pg-noresult', T('Nichts gefunden.')));
    highlight();
  }
  function highlight() { pick.items.forEach(function (it, i) { it.b.classList.toggle('on', i === pick.idx); if (i === pick.idx) it.b.scrollIntoView({ block: 'nearest' }); }); }
  function insertLink(href, text, cls) {
    closePicker(false);
    var r = pick.range; if (!r) return;
    if (!r.collapsed) r.deleteContents();     // markierten Text durch den Verweis ersetzen
    var a = document.createElement('a'); a.href = href; a.className = cls; a.textContent = text;
    var sp = document.createTextNode('\u00a0');
    r.insertNode(sp); r.insertNode(a);
    // Klebt der Verweis direkt an einem Wort ("Keycloak.Verweis"), Leerzeichen davor setzen
    var prev = a.previousSibling;
    if (prev && prev.nodeType === 3 && prev.data.length && !/\s$/.test(prev.data)) a.parentNode.insertBefore(document.createTextNode(' '), a);
    var s = window.getSelection(), nr = document.createRange(); nr.setStartAfter(sp); nr.collapse(true); s.removeAllRanges(); s.addRange(nr);
    var holder = a.closest('[contenteditable="true"]'); if (holder) holder.focus();
    S.dirtyBody = true; schedule();
  }
  $('pg-picker-q').addEventListener('input', renderPicker);
  $('pg-picker-q').addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); pick.idx = Math.min(pick.idx + 1, pick.items.length - 1); highlight(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); pick.idx = Math.max(pick.idx - 1, 0); highlight(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (pick.items[pick.idx]) pick.items[pick.idx].fn(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePicker(true); }
  });
  document.addEventListener('mousedown', function (e) { if (!$('pg-picker').hidden && !$('pg-picker').contains(e.target)) closePicker(false); });

  // --- Rueckverweise ----------------------------------------------------------
  // Rueckverweise erst laden, wenn das Seitenende in die Naehe des sichtbaren Bereichs kommt (der Server durchsucht dafuer
  // alle Inhalte). Kurze Seiten: sofort sichtbar = sofort geladen. Merkpunkt (1 px) vor dem Abschnitt, weil der
  // Abschnitt selbst bis zum Ergebnis versteckt ist.
  var BLW = null;
  function scrollRoot(n) {
    for (var x = n && n.parentElement; x && x !== document.body; x = x.parentElement) {
      var o = getComputedStyle(x).overflowY; if (o === 'auto' || o === 'scroll') return x;
    }
    return null;
  }
  function loadBacklinks(id) {
    var sec = $('pg-backlinks'), ul = $('pg-backlinks-list');
    if (BLW) { BLW.disconnect(); BLW = null; }
    if (remoteId(id)) { sec.hidden = true; ul.textContent = ''; return; }   // andere Instanz: keine Rueckverweise (BUG-097)
    if (!window.IntersectionObserver) return fetchBacklinks(id);
    if (ul.dataset.id !== String(id)) { sec.hidden = true; ul.textContent = ''; }   // keine Liste der vorigen Seite stehen lassen
    var w = $('pg-bl-watch');
    if (!w) { w = el('div'); w.id = 'pg-bl-watch'; w.style.height = '1px'; w.setAttribute('aria-hidden', 'true'); sec.parentNode.insertBefore(w, sec); }
    var io = BLW = new IntersectionObserver(function (es) {
      if (!es.some(function (e) { return e.isIntersecting; })) return;
      io.disconnect(); if (BLW === io) BLW = null;
      fetchBacklinks(id);
    }, { root: scrollRoot(w), rootMargin: '600px 0px' });
    io.observe(w);
  }
  function fetchBacklinks(id) {
    var sec = $('pg-backlinks'), ul = $('pg-backlinks-list');
    api('GET', '/api/notes/' + id + '/backlinks').then(function (list) {
      if (!S.cur || S.cur.id !== id) return;
      ul.dataset.id = String(id); ul.textContent = ''; sec.hidden = !list.length;
      list.forEach(function (p) {
        var b = el('button', 'bl-item', (ti(p.icon) || '📄') + ' ' + label(p)); b.type = 'button';
        b.addEventListener('click', function () { openPage(p.id); });
        var li = el('li'); li.appendChild(b); ul.appendChild(li);
      });
    }).catch(function () { sec.hidden = true; });
  }


  // --- Favoriten --------------------------------------------------------------
  function renderFavs() {
    var favs = S.pages.filter(function (p) { return p.pinned; });
    $('pg-favs').hidden = !favs.length;
    var ul = $('pg-fav-list'); ul.textContent = '';
    favs.forEach(function (p) {
      var b = el('button', 'pg-fav' + (S.cur && S.cur.id === p.id ? ' on' : '')); b.type = 'button';
      b.appendChild(icoNode(p.icon, '📄', 'pg-ic')); b.appendChild(el('span', 'pg-name', label(p)));
      b.addEventListener('click', function () { openPage(p.id); });
      var li = el('li'); li.appendChild(b); ul.appendChild(li);
    });
  }
  function setPinned(on) {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { pinned: on }).then(function () {
      S.cur.pinned = on; var p = byId(id); if (p) p.pinned = on; renderTree();
      HK.toast(on ? T('Zu Favoriten hinzugefügt') : T('Von Favoriten gelöst'));
    }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }

  // --- Dialog (Versionsverlauf) -----------------------------------------------
  function openModal(title, build, small) {
    $('pg-modal').firstElementChild.classList.toggle('hk-dialog-s', !!small);
    $('pg-modal').classList.remove('no-x');
    $('pg-modal-title').textContent = title; var body = $('pg-modal-body'); body.textContent = '';
    S.modalClose = null;
    if ($('pg-modal').hidden) {                     // D08: Fokus danach zurueck zum Ausloeser (aus einem Menue: dessen Knopf)
      var a = document.activeElement;
      if (!a || a === document.body || !a.isConnected || (a.closest && a.closest('.menu'))) a = (HK.lastMenuOwner && HK.lastMenuOwner()) || a;
      S.modalBack = a;
    }
    build(body); $('pg-modal').hidden = false; $('pg-modal-close').focus();
  }
  function closeModal() {
    var b = S.modalBack; S.modalBack = null;
    $('pg-modal').hidden = true; var f = S.modalClose; S.modalClose = null; if (f) f();
    if (b && !b.isConnected && b.className && typeof b.className === 'string') {      // neu gezeichnet (z. B. Datenbank-Leiste): gleicher Knopf
      var same = Array.prototype.filter.call(document.querySelectorAll('.' + b.className.trim().split(/\s+/).join('.')),
        function (x) { return x.textContent === b.textContent; });
      b = same[0] || null;
    }
    if (b && b.isConnected && $('pg-modal').hidden && typeof b.focus === 'function') try { b.focus(); } catch (e) { /* egal */ }
  }
  // D08: Tab bleibt im Dialog (aria-modal)
  $('pg-modal').addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    var f = Array.prototype.filter.call(this.querySelectorAll('button,[href],input,select,textarea,[tabindex]:not([tabindex="-1"])'),
      function (x) { return !x.disabled && x.getClientRects().length; });
    if (!f.length) return;
    var a = f[0], z = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === a || !this.contains(document.activeElement))) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && (document.activeElement === z || !this.contains(document.activeElement))) { e.preventDefault(); a.focus(); }
  });
  $('pg-modal-close').addEventListener('click', closeModal);
  $('pg-modal').addEventListener('mousedown', function (e) { if (e.target === this) closeModal(); });

  var fVer = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function openVersions() {
    var id = S.cur.id;
    save().then(function () { return api('GET', '/api/notes/' + id + '/versions'); }).then(function (list) {
      openModal(T('Versionsverlauf'), function (body) {
        if (!list.length) { body.appendChild(el('p', 'nt-empty', T('Noch keine früheren Versionen. Beim Bearbeiten sichert Saga höchstens alle 5 Minuten, nach einer Pause und beim Verlassen der Seite einen Stand (bis zu 50 pro Seite).'))); return; }
        var wrap = el('div', 'ver'), ul = el('ul', 'ver-list'), prev = el('div', 'ver-prev');
        prev.appendChild(el('p', 'nt-empty', T('Version auswählen, um sie anzusehen.')));
        list.forEach(function (v) {
          var b = el('button', 'ver-item'); b.type = 'button';
          b.appendChild(el('span', 'ver-date', fVer.format(new Date(v.created * 1000))));
          b.appendChild(el('span', 'ver-title', v.title || T('Unbenannt')));
          if (v.author) {
            var vb = el('span', 'ver-by'), va = el('span', 'ver-av', (v.author.name || '?').charAt(0).toUpperCase());
            va.setAttribute('aria-hidden', 'true'); vb.appendChild(HK.avatarImg(va, v.author.avatar, 17));
            vb.appendChild(document.createTextNode(T('von {name}', { name: v.author.name }))); b.appendChild(vb);
          }
          else if (v.by === 'me') b.appendChild(el('span', 'ver-by', T('von dir')));     // "unknown": aeltere Fassung ohne Autor (BUG-092)
          b.addEventListener('click', function () {
            [].forEach.call(ul.querySelectorAll('.ver-item'), function (x) { x.classList.toggle('on', x === b); });
            api('GET', '/api/notes/' + id + '/versions/' + v.id).then(function (d) {
              prev.textContent = '';
              prev.appendChild(el('div', 'ver-prev-title', d.title || T('Unbenannt')));
              prev.appendChild(el('div', 'ver-prev-text', d.text || T('(leer)')));
              var r = el('button', 'btn', T('Diese Version wiederherstellen')); r.type = 'button';
              r.addEventListener('click', function () {
                HK.ask({ title: T('Diese Version wiederherstellen?'),
                         text: T('Der Inhalt der Seite wird durch die Version vom {date} ersetzt. Der jetzige Stand wird vorher als Version gesichert – du kannst ihn jederzeit zurückholen.', { date: fVer.format(new Date(v.created * 1000)) }),
                         ok: T('Wiederherstellen') }).then(function (yes) { if (yes) restore(); });
              });
              function restore() {
                api('POST', '/api/notes/' + id + '/versions/' + v.id + '/restore').then(function () {
                  closeModal(); clearTimeout(S.timer); S.dirtyBody = S.dirtyTitle = false;
                  return loadList().then(function () { return openPage(id); });
                }).then(function () { HK.toast(T('Version wiederhergestellt – der bisherige Stand ist als Version gesichert.')); })
                  .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
              }
              prev.appendChild(r);
            });
          });
          var li = el('li'); li.appendChild(b); ul.appendChild(li);
        });
        wrap.appendChild(ul); wrap.appendChild(prev); body.appendChild(wrap);
      });
    }).catch(function (err) { HK.toast(T('Versionen nicht geladen: {err}', { err: err.message })); });
  }

  // --- Export (Download ueber fetch, weil die API einen eigenen Header verlangt) ---
  function exportPage(withSub) {
    var id = S.cur.id;
    save().then(function () {
      return fetch('/api/notes/' + id + '/export' + (withSub ? '?sub=1' : ''), { credentials: 'same-origin', headers: { 'X-Saga': '1' } });
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var cd = r.headers.get('Content-Disposition') || '', m = cd.match(/filename\*=UTF-8''([^;]+)/);
      var name = m ? decodeURIComponent(m[1]) : (withSub ? 'export.zip' : 'export.md');
      return r.blob().then(function (b) {
        var u = URL.createObjectURL(b), a = el('a'); a.href = u; a.download = name;
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 2000);
      });
    }).catch(function (err) { HK.toast(T('Export fehlgeschlagen: {err}', { err: err.message })); });
  }

  // --- Papierkorb -------------------------------------------------------------
  var fDay = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  var fDate = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  function showTrash() {
    save().then(function () {
      S.cur = null; $('page').hidden = true; $('pg-empty').hidden = true; $('pg-trash').hidden = false;
      history.replaceState(null, '', location.pathname); document.title = T('Papierkorb') + ' · ' + T('Seiten');
      renderTree(); return loadTrash();
    });
  }
  function loadTrash() {
    return api('GET', '/api/trash').then(function (list) {
      var ul = $('pg-trash-list'); ul.textContent = '';
      $('pg-trash-empty').hidden = !list.length;
      if (!list.length) { ul.appendChild(el('li', 'nt-empty', T('Der Papierkorb ist leer.'))); return; }
      list.forEach(function (p) {
        var li = el('li', 'trash-item'), info = el('div', 'trash-info');
        info.appendChild(el('span', 'trash-name', (ti(p.icon) || '📄') + ' ' + label(p)));
        info.appendChild(el('span', 'trash-meta', T('gelöscht {date}', { date: fDay.format(new Date(p.deleted_at * 1000)) }) +
          (p.subpages ? ' · ' + T('mit {sub}', { sub: nSub(p.subpages) }) : '') +
          ' · ' + T('endgültig weg am {date}', { date: fDate.format(new Date(p.purge_at * 1000)) })));
        var rb = el('button', 'btn', T('Wiederherstellen')); rb.type = 'button';
        rb.addEventListener('click', function () {
          api('POST', '/api/trash/' + p.id + '/restore').then(function () { return Promise.all([loadList(), loadTrash()]); })
            .then(function () { HK.toast(T('„{name}“ wiederhergestellt', { name: label(p) }), T('Öffnen'), function () { openPage(p.id); }); })
            .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
        });
        var db = el('button', 'linkbtn danger', T('Endgültig löschen')); db.type = 'button';
        db.addEventListener('click', function () {
          if (!confirm(p.subpages ? T('„{name}“ und {sub} endgültig löschen? Das lässt sich nicht rückgängig machen.', { name: label(p), sub: nSub(p.subpages) })
                               : T('„{name}“ endgültig löschen? Das lässt sich nicht rückgängig machen.', { name: label(p) }))) return;
          api('DELETE', '/api/trash/' + p.id).then(loadTrash).catch(function (err) { HK.toast(T('Nicht gelöscht: {err}', { err: err.message })); });
        });
        li.appendChild(info); li.appendChild(rb); li.appendChild(db); ul.appendChild(li);
      });
    });
  }
  $('pg-trash-btn').addEventListener('click', showTrash);
  $('pg-trash-empty').addEventListener('click', function () {
    if (!confirm(T('Papierkorb endgültig leeren? Das lässt sich nicht rückgängig machen.'))) return;
    api('POST', '/api/trash/empty').then(loadTrash).catch(function (err) { HK.toast(T('Nicht geleert: {err}', { err: err.message })); });
  });

  // --- Schnellsuche (Strg/Cmd+K) ----------------------------------------------
  var qs = { items: [], idx: 0 };
  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  $('pg-qs-kbd').textContent = isMac ? '⌘K' : T('Strg') + ' K';
  function openQS() { $('pg-qs').hidden = false; $('pg-qs-q').value = ''; renderQS(); $('pg-qs-q').focus(); }
  function closeQS() { $('pg-qs').hidden = true; }
  function renderQS() {
    var q = $('pg-qs-q').value.trim().toLowerCase(), list = $('pg-qs-list');
    list.textContent = ''; qs.items = []; qs.idx = 0;
    var cmdOnly = q.charAt(0) === '>'; if (cmdOnly) q = q.slice(1).trim();
    var rec = recents().map(byId).filter(Boolean);
    var hits = cmdOnly ? [] : q ? allPages().filter(function (p) { return (label(p) + ' ' + tagsOf(p).join(' ')).toLowerCase().indexOf(q) >= 0; })
                                     .sort(function (a, b) { return (rec.indexOf(b) >= 0) - (rec.indexOf(a) >= 0) || b.updated - a.updated; })
                 : rec.concat(allPages().filter(function (p) { return rec.indexOf(p) < 0; }).sort(function (a, b) { return b.updated - a.updated; }));
    if (!q && !cmdOnly) list.appendChild(el('div', 'menu-label', rec.length ? T('Zuletzt geöffnet') : T('Zuletzt bearbeitet')));
    function item(text, sub, fn) {
      var b = el('button', 'picker-item'); b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(el('span', 'picker-t', text)); if (sub) b.appendChild(el('span', 'picker-sub', sub));
      b.addEventListener('click', function () { closeQS(); fn(); });
      qs.items.push({ b: b, fn: fn }); list.appendChild(b);
      return b;
    }
    hits.slice(0, q ? 8 : 10).forEach(function (p) {
      var path = (p.owner ? p.owner.name + (ancestors(p.id).length ? ' · ' : '') : '') + ancestors(p.id).map(label).join(' / ');
      var b = item((ti(p.icon) || '📄') + ' ' + label(p), path, function () { openPage(p.id); }), ps = b.querySelector('.picker-sub');
      if (p.owner && ps) { ps.insertBefore(document.createTextNode(' '), ps.firstChild); ps.insertBefore(grpIc(), ps.firstChild); }
    });
    var acts = actions().filter(function (a) { return !q || (a.t + ' ' + (a.kw || '')).toLowerCase().indexOf(q) >= 0; });
    var cmdAt = qs.items.length, cmdLabel = null;
    if (acts.length && (q || cmdOnly)) {
      list.appendChild(cmdLabel = el('div', 'menu-label', T('Befehle')));
      acts.slice(0, cmdOnly ? 30 : 6).forEach(function (a) {
        if (a.ic !== 'grp') { item(a.ic + ' ' + a.t, a.kbd || '', a.fn); return; }
        var t = item(' ' + a.t, a.kbd || '', a.fn).querySelector('.picker-t'); t.insertBefore(grpIc(), t.firstChild);   // einfarbiges Symbol
      });
    } else if (!q) list.appendChild(el('div', 'pg-qs-hint', T('Tipp: „>“ zeigt alle Befehle')));
    if (q && !cmdOnly) item(T('+ Neue Seite „{name}“', { name: $('pg-qs-q').value.trim() }), '', function () {
      api('POST', '/api/notes', { title: $('pg-qs-q').value.trim() }).then(function (d) { return loadList().then(function () { return openPage(d.id); }); });
    });
    if (!qs.items.length) list.appendChild(el('div', 'pg-noresult', T('Nichts gefunden.')));
    qs.items.forEach(function (it, i) { it.b.classList.toggle('on', i === 0); });
    // Volltext (wie "Seiten durchsuchen"): Treffer im Inhalt nachladen, vor den Befehlen einfuegen
    var token = qs.token = (qs.token || 0) + 1; clearTimeout(qs.timer);
    if (!q || cmdOnly || q.length < 2) return;
    qs.timer = setTimeout(function () {
      api('POST', '/api/notes/search', { q: q }).then(function (res) {
        if (token !== qs.token || $('pg-qs').hidden) return;
        var seen = {}; hits.forEach(function (p) { seen[p.id] = 1; });
        var extra = (res || []).filter(function (h) { return !seen[h.id] && byId(h.id); }).slice(0, 6);
        if (!extra.length) return;
        var at = cmdLabel ? cmdAt : qs.items.length - 1, before = cmdLabel || (qs.items[at] ? qs.items[at].b : null);
        list.insertBefore(el('div', 'menu-label', T('Im Inhalt gefunden')), before);
        var add = extra.map(function (h) {
          var b = el('button', 'picker-item qs-hit'); b.type = 'button'; b.setAttribute('role', 'option');
          b.appendChild(el('span', 'picker-t', (ti(h.icon) || '📄') + ' ' + label(h)));
          if (h.snippet) b.appendChild(el('span', 'pg-hit-s', h.snippet));
          var fn = function () { openPage(h.id); };
          b.addEventListener('click', function () { closeQS(); fn(); });
          list.insertBefore(b, before); return { b: b, fn: fn };
        });
        // Ohne Titel-Treffer stand die Auswahl auf "+ Neue Seite" -> jetzt den ersten Inhalts-Treffer waehlen
        var curItem = qs.items[qs.idx], wasNew = !hits.length && qs.idx === qs.items.length - 1;
        Array.prototype.splice.apply(qs.items, [at, 0].concat(add));
        qs.idx = wasNew ? at : Math.max(0, qs.items.indexOf(curItem)); qsHighlight();
      }).catch(function () {});
    }, 200);
  }
  function qsHighlight() { qs.items.forEach(function (it, i) { it.b.classList.toggle('on', i === qs.idx); if (i === qs.idx) it.b.scrollIntoView({ block: 'nearest' }); }); }
  $('pg-qs-q').addEventListener('input', renderQS);
  $('pg-qs-q').addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); qs.idx = Math.min(qs.idx + 1, qs.items.length - 1); qsHighlight(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); qs.idx = Math.max(qs.idx - 1, 0); qsHighlight(); }
    else if (e.key === 'Enter') { e.preventDefault(); if (qs.items[qs.idx]) { closeQS(); qs.items[qs.idx].fn(); } }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeQS(); }
  });
  $('pg-qs').addEventListener('mousedown', function (e) { if (e.target === this) closeQS(); });
  $('pg-qs-btn').addEventListener('click', openQS);
  document.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
      // Mit markiertem Text im Editor gehoert Strg/Cmd+K dem Link-Werkzeug von Editor.js
      var sel = window.getSelection();
      if (sel && !sel.isCollapsed && $('pg-editor').contains(sel.anchorNode)) return;
      e.preventDefault(); openQS();
    }
    // preventDefault: Esc schliesst NUR den Dialog - nicht danach auch noch den Peek, in dem er steckt (Bughunt 2)
    else if (e.key === 'Escape' && !$('pg-modal').hidden) { e.preventDefault(); closeModal(); }
  });


  // --- Inline-Werkzeug "Datum": markierten Text ("morgen", "Freitag", "12.10.") zum Datums-Chip machen.
  // Meldet Editor.js ausserdem <time datetime> an - sonst wuerden @-Daten beim Speichern entfernt.
  // Block-Menue (⋮⋮) -> "Kommentieren": Unterhaltung zu diesem Block in der Seitenleiste (static/comments.js)
  function CommentTune(o) { this.block = o.block; }
  CommentTune.isTune = true;
  CommentTune.prototype.render = function () {
    var self = this;
    return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
             title: T('Kommentieren'), closeOnActivate: true,
             onActivate: function () { if (window.HKComments && self.block) HKComments.openBlock(self.block.id); } };
  };
  // --- Seite gestalten (2.5): Block-Menue ⋮⋮ (G5) mit Farbe (G3), Duplizieren, Link zum Block -----------------------------
  // Reihenfolge im Menue: Einstellungen des Blocks (Editor.js), "Umwandeln in", Farbe, Duplizieren, Link, Kommentieren, dann
  // Nach oben / Loeschen / Nach unten. Lesen/Kommentieren (bzw. gesperrt): nur Link und Kommentieren.
  var BLOCK_TUNES = ['hkstyle', 'hkdup', 'hklink', 'hkcomment'];
  var STYLE_TYPES = { paragraph: 1, header: 1, list: 1, quote: 1, toggle: 1, callout: 1, checklist: 1 };   // wie saga/notes.py
  var DUP_TYPES = { paragraph: 1, header: 1, list: 1, quote: 1, code: 1, toggle: 1, callout: 1, delimiter: 1, table: 1, image: 1 };
  var COLOR_NAMES = { gray: T('Grau'), brown: T('Braun'), orange: T('Orange'), yellow: T('Gelb'), green: T('Grün'), blue: T('Blau'),
                      purple: T('Lila'), pink: T('Rosa'), red: T('Rot') };
  var STYLE_OF = new WeakMap();                      // .ce-block__content -> StyleTune (Farbe per Slash/Cmd+/ setzen)
  function cleanStyle(st) {                         // wie der Server: nur Palette, bg nur true
    if (!st || typeof st !== 'object' || COLORS.indexOf(st.c) < 0) return null;
    return st.bg === true ? { c: st.c, bg: true } : { c: st.c };
  }
  function StyleTune(o) { this.api = o.api; this.block = o.block; this.data = cleanStyle(o.data); this.box = null; }
  StyleTune.isTune = true;
  StyleTune.prototype.wrap = function (content) { this.box = content; STYLE_OF.set(content, this); this.paint(); return content; };
  StyleTune.prototype.paint = function () {
    var b = this.box; if (!b) return;
    COLORS.forEach(function (c) { b.classList.remove('hk-c-' + c, 'hk-bg-' + c); });
    if (this.data) b.classList.add((this.data.bg ? 'hk-bg-' : 'hk-c-') + this.data.c);
  };
  StyleTune.prototype.set = function (st) {
    this.data = cleanStyle(st); this.paint();
    if (this.block && this.block.dispatchChange) this.block.dispatchChange();
    if (!S.loading) { S.dirtyBody = true; schedule(); }
  };
  StyleTune.prototype.save = function () { return this.data || undefined; };
  StyleTune.prototype.render = function () {
    var self = this, name = this.block && this.block.name;
    if (!STYLE_TYPES[name] || readOnly()) return [];
    var cur = this.data, items = [{ title: T('Standard'), icon: '<span class="hk-sw">A</span>', isActive: !cur, closeOnActivate: true,
      onActivate: function () { self.set(null); } }, { type: 'separator' }];
    COLORS.forEach(function (c) {
      items.push({ title: COLOR_NAMES[c], icon: '<span class="hk-sw hk-c-' + c + '">A</span>', isActive: !!cur && cur.c === c && !cur.bg,
        closeOnActivate: true, onActivate: function () { self.set({ c: c }); } });
    });
    items.push({ type: 'separator' });
    COLORS.forEach(function (c) {
      items.push({ title: T('{farbe} (Hintergrund)', { farbe: COLOR_NAMES[c] }), icon: '<span class="hk-sw hk-bg-' + c + '">A</span>',
        isActive: !!cur && cur.c === c && !!cur.bg, closeOnActivate: true, onActivate: function () { self.set({ c: c, bg: true }); } });
    });
    return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 19l5.5-14h3L19 19M7.4 13h9.2"/></svg>',
             title: T('Farbe'), name: 'hk-color', children: { searchable: false, items: items } };
  };
  // Block an einem Element (Absatz, Titel ...) einfaerben - fuer "/rot", "/blau hintergrund" und Cmd+/
  function styleAt(node, st) {
    var box = node && node.closest ? node.closest('.ce-block__content') : null, tune = box && STYLE_OF.get(box);
    if (!tune || !STYLE_TYPES[tune.block && tune.block.name]) return false;
    tune.set(st); return true;
  }
  // "/rot", "/rot hintergrund", "/red bg" (Farbnamen der eingestellten Sprache und die Palette-Namen) -> {c, bg} | null
  function slashColor(q) {
    var n = String(q || '').replace(/ /g, ' ').trim().toLowerCase(), bg = false, words = [T('Hintergrund').toLowerCase(), 'bg', 'background'];
    if (!n) return null;
    for (var i = 0; i < words.length; i++) {
      var w = ' ' + words[i];
      if (n.length > w.length && n.slice(-w.length) === w) { bg = true; n = n.slice(0, -w.length).trim(); break; }
    }
    for (var k = 0; k < COLORS.length; k++) {
      var c = COLORS[k];
      if (n === c || n === COLOR_NAMES[c].toLowerCase()) return bg ? { c: c, bg: true } : { c: c };
    }
    return null;
  }
  function DupTune(o) { this.api = o.api; this.block = o.block; }
  DupTune.isTune = true;
  DupTune.prototype.render = function () {
    var self = this, name = this.block && this.block.name;
    if (readOnly()) return [];
    return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
             title: T('Duplizieren'), secondaryLabel: isMac ? '⌘D' : T('Strg') + '+D', isDisabled: !DUP_TYPES[name], closeOnActivate: true,
             onActivate: function () { dupBlock(self.api, self.block); } };
  };
  // Block samt Farbe (tunes) direkt darunter kopieren (auch Cmd+D, static/keys.js)
  function dupBlock(eapi, blk) {
    if (!blk || !DUP_TYPES[blk.name]) { HK.toast(T('Diesen Block bitte über das Block-Menü kopieren')); return Promise.resolve(); }
    var i = eapi.blocks.getBlockIndex(blk.id);
    return blk.save().then(function (s) {
      if (!s) return;
      var nb = eapi.blocks.insert(s.tool, s.data, undefined, i + 1, true);
      styleBlock(nb, s.tunes && s.tunes.hkstyle);
      try { eapi.caret.setToBlock(i + 1, 'end'); } catch (e) { /* ohne Textfeld */ }
    });
  }
  // Farbe an einen eben eingefuegten Block (blocks.insert kennt keine tunes; update wuerde den Block neu bauen)
  function styleBlock(nb, st) {
    st = cleanStyle(st);
    var box = st && nb && nb.holder ? nb.holder.querySelector('.ce-block__content') : null, tune = box && STYLE_OF.get(box);
    if (tune) tune.set(st);
  }

  function LinkTune(o) { this.block = o.block; }
  LinkTune.isTune = true;
  LinkTune.prototype.render = function () {
    var self = this;
    return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/></svg>',
             title: T('Link zum Block kopieren'), closeOnActivate: true,
             onActivate: function () {
               var h = self.block && self.block.holder;
               if (h && h.parentNode && h.parentNode.closest && h.parentNode.closest('.col, .tgl-body')) { HK.toast(T('Link zu Blöcken in Toggles/Spalten: bitte den äußeren Block verlinken')); }
               copyLink(self.block && self.block.id);
             } };
  };

  // Bild (2.5, G2): Editor.js-Bildwerkzeug um Breite (10-100 %) und Ausrichtung ergaenzen - als Unterklasse, damit Daten,
  // die das Werkzeug nicht kennt (andere Clients), beim Speichern erhalten bleiben
  var IMG_KNOWN = { file: 1, caption: 1, withBorder: 1, withBackground: 1, stretched: 1, width: 1, align: 1 };
  var ALIGNS = { left: 1, center: 1, right: 1 };
  function sizeW(v) { return typeof v === 'number' && isFinite(v) ? Math.min(100, Math.max(10, Math.round(v))) : null; }
  function hkImage(Base) {
    if (!Base || typeof Reflect === 'undefined') return Base;
    if (Base.hkWrapped) return Base.hkWrapped;
    function HKImage(o) {
      var self = Reflect.construct(Base, [o], HKImage), d = (o && o.data) || {};
      self.hkExtra = {}; Object.keys(d).forEach(function (k) { if (!IMG_KNOWN[k]) self.hkExtra[k] = d[k]; });
      self.hkW = sizeW(d.width); self.hkA = ALIGNS[d.align] ? d.align : null; self.hkRo = !!(o && o.readOnly);
      return self;
    }
    HKImage.prototype = Object.create(Base.prototype); HKImage.prototype.constructor = HKImage;
    Object.setPrototypeOf(HKImage, Base);             // Editor.js liest toolbox, pasteConfig ... an der Klasse
    HKImage.prototype.render = function () {
      var w = Base.prototype.render.call(this); this.hkBox = w;
      sizable(this, w, function () { return w.querySelector('.image-tool__image'); });
      return w;
    };
    HKImage.prototype.renderSettings = function () {
      var own = Base.prototype.renderSettings.call(this) || [];
      return (Array.isArray(own) ? own : [own]).concat(alignItems(this));
    };
    HKImage.prototype.save = function () {
      var d = Base.prototype.save.call(this), out = Object.assign({}, this.hkExtra, d);
      if (this.hkW) out.width = this.hkW;
      if (this.hkA) out.align = this.hkA;
      return out;
    };
    Base.hkWrapped = HKImage;
    return HKImage;
  }
  // Ausrichtung im Block-Menue (Bild, Player)
  function alignItems(t) {
    if (t.hkRo || readOnly()) return [];
    return [['left', T('Linksbündig'), 'M4 6h16M4 10h10M4 14h16M4 18h10'], ['center', T('Zentriert'), 'M4 6h16M7 10h10M4 14h16M7 18h10'],
            ['right', T('Rechtsbündig'), 'M4 6h16M10 10h10M4 14h16M10 18h10']].map(function (a) {
      return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="' + a[2] + '"/></svg>',
               title: a[1], toggle: 'hk-align', isActive: (t.hkA || 'center') === a[0], closeOnActivate: true,
               onActivate: function () { t.hkA = a[0]; paintSize(t); sizeChanged(t); } };
    });
  }
  function paintSize(t) {
    var w = t.hkBox; if (!w) return;
    w.classList.add('hk-sz');
    w.classList.toggle('hk-iw', !!t.hkW);
    if (t.hkW) w.style.setProperty('--iw', t.hkW + '%'); else w.style.removeProperty('--iw');
    ['left', 'center', 'right'].forEach(function (a) { w.classList.toggle('hk-al-' + a, (t.hkA || 'center') === a); });
  }
  function sizeChanged(t) {
    if (t.block && t.block.dispatchChange) t.block.dispatchChange();
    if (!S.loading) { S.dirtyBody = true; schedule(); }
  }
  // Griffe links/rechts (wie Notion): Breite live, beim Loslassen speichern; Doppelklick = Standardbreite
  function sizable(t, w, target) {
    paintSize(t);
    if (t.hkRo || roRole()) return;
    ['l', 'r'].forEach(function (side) {
      var h = el('div', 'hk-rs hk-rs-' + side); h.contentEditable = 'false'; h.dataset.mutationFree = 'true';
      h.setAttribute('aria-hidden', 'true'); h.title = T('Ziehen ändert die Breite, Doppelklick setzt sie zurück');
      h.addEventListener('mousedown', function (e) { e.stopPropagation(); });
      h.addEventListener('dblclick', function (e) { e.preventDefault(); e.stopPropagation(); if (t.hkW) { t.hkW = null; paintSize(t); sizeChanged(t); } });
      h.addEventListener('pointerdown', function (e) {
        if (readOnly() || e.button !== 0) return;
        e.preventDefault(); e.stopPropagation();
        var box = w.getBoundingClientRect(), before = t.hkW;
        if (!box.width) return;
        try { h.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
        document.body.classList.add('hk-resizing');
        function mv(ev) {
          var al = t.hkA || 'center', px = al === 'left' ? ev.clientX - box.left : al === 'right' ? box.right - ev.clientX
            : 2 * Math.abs(ev.clientX - (box.left + box.width / 2));
          var pct = Math.min(100, Math.max(10, Math.round(Math.abs(px) / box.width * 100)));
          if (pct !== t.hkW) { t.hkW = pct >= 100 ? null : pct; paintSize(t); }
        }
        function up() {
          h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
          document.body.classList.remove('hk-resizing');
          if (t.hkW !== before) sizeChanged(t);
        }
        h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
      });
      var tg = target(); if (tg) tg.appendChild(h);
    });
  }

  function DateInline(o) { this.api = o.api; }
  DateInline.isInline = true;
  DateInline.title = T('Datum');
  DateInline.sanitize = { time: { datetime: true, class: true } };
  DateInline.prototype.render = function () {
    var b = el('button', 'ce-inline-tool hk-date-tool', '📅'); b.type = 'button'; b.title = T('Als Datum (z. B. „morgen“, „Freitag“, „12.10.“)'); return b;
  };
  DateInline.prototype.surround = function (range) {
    if (!range || !window.HKKeys) return;
    var d = HKKeys.parseDate(range.toString());
    if (!d) { HK.toast(T('Kein Datum erkannt – z. B. „morgen“, „Freitag“, „12.10.“, „in 3 Tagen“')); return; }
    var chip = HKKeys.dateChip(d, false);
    range.deleteContents(); range.insertNode(chip);
    var s = window.getSelection(), r = document.createRange(); r.setStartAfter(chip); r.collapse(true); s.removeAllRanges(); s.addRange(r);
  };
  DateInline.prototype.checkState = function () { return false; };

  // --- Eigene Werkzeuge: Toggle und Callout -------------------------------------
  var INLINE_SAN = { b: true, i: true, u: true, s: true, br: true, a: { href: true }, code: { class: true }, mark: { class: true }, time: { datetime: true, class: true } };
  function editable(cls, html, ph) {
    var d = el('div', cls); d.contentEditable = roRole() ? 'false' : 'true'; d.innerHTML = html || ''; d.dataset.placeholder = ph; return d;
  }
  // Ausklappbar (Toggle) mit echten Bloecken im Inhalt (eigener Editor, wie Spalten)
  function ToggleTool(o) {
    var d = o.data || {};
    this.title = d.title || ''; this.open = d.open !== false;
    this.level = d.level === 1 || d.level === 2 || d.level === 3 ? d.level : 0;     // 2.5 (G10): Toggle-Ueberschrift H1-H3
    var ex = this.extra = {}; Object.keys(d).forEach(function (k) { if (['title', 'open', 'blocks', 'text', 'level'].indexOf(k) < 0) ex[k] = d[k]; });
    // Alte Toggles (nur Text) werden beim Oeffnen umgewandelt: Text -> erster Absatz
    this.blocks = Array.isArray(d.blocks) ? d.blocks : (d.text ? [{ type: 'paragraph', data: { text: d.text } }] : []);
    this.ed = null;
  }
  var TGL_OF = new WeakMap();                       // .tgl -> ToggleTool (static/keys.js: Toggle <-> Toggle-Ueberschrift)
  var TGL_IC = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>';
  ToggleTool.toolbox = [{ title: T('Ausklappbar'), icon: TGL_IC },
    { title: T('Toggle-Überschrift 1'), icon: TGL_IC.replace('<path', '<text x="13" y="21" font-size="9" stroke="none" fill="currentColor">1</text><path'), data: { level: 1 } },
    { title: T('Toggle-Überschrift 2'), icon: TGL_IC.replace('<path', '<text x="13" y="21" font-size="9" stroke="none" fill="currentColor">2</text><path'), data: { level: 2 } },
    { title: T('Toggle-Überschrift 3'), icon: TGL_IC.replace('<path', '<text x="13" y="21" font-size="9" stroke="none" fill="currentColor">3</text><path'), data: { level: 3 } }];
  // Umwandeln (Block-Menue "Umwandeln in", Editor.js): Titel <-> Text anderer Bloecke (Ueberschrift <-> Toggle-Ueberschrift)
  var TGL_STASH = null;
  window.addEventListener('unhandledrejection', function (e) { if (e.reason && e.reason.hkKeep) e.preventDefault(); });   // gewollter Abbruch (s. u.)
  ToggleTool.conversionConfig = {
    // Editor.js nutzt export auch beim Verschmelzen (Ruecktaste am Titelanfang) und beim Umwandeln in Text/Ueberschrift:
    // beides wuerde den Inhalt verwerfen -> mit Inhalt verweigern (wie Cmd+/, static/keys.js); Ebene: Toggle-Ueberschrift 1-3
    export: function (d) {
      if (Array.isArray(d.blocks) && d.blocks.length) {
        HK.toast(T('Der Toggle hat Inhalt – erst ausräumen, dann umwandeln'));
        var er = new Error('toggle not empty'); er.hkKeep = true; throw er;
      }
      TGL_STASH = { blocks: d.blocks, open: d.open, at: Date.now() }; return d.title || '';
    },
    import: function (html) {
      var st = TGL_STASH && Date.now() - TGL_STASH.at < 1500 ? TGL_STASH : null; TGL_STASH = null;     // Toggle -> Toggle: Inhalt behalten
      return { title: html, open: st ? st.open !== false : true, blocks: st && Array.isArray(st.blocks) ? st.blocks : [] };
    }
  };
  // Bereinigung: nur der Titel (wie Inline-Text); der verschachtelte Inhalt bleibt unangetastet (true) - den bereinigt
  // der eigene Editor im Toggle je Block, und eine leere Regel wuerde alle Formatierung entfernen. Ohne Regel wandte
  // Editor.js die Inline-Regeln auf jedes Feld an: das Feld "code" eines Code-Blocks traf auf die Regel des Inline-Codes,
  // die Bereinigung brach ab und die ganze Seite liess sich nicht mehr speichern (Bughunt W01). Der Server bereinigt
  // den Titel zusaetzlich (Positivliste).
  ToggleTool.sanitize = { title: INLINE_SAN, blocks: true };
  ToggleTool.prototype.render = function () {
    var self = this, w = el('div', 'tgl' + (this.open ? ' open' : '') + (this.level ? ' tgl-h tgl-h' + this.level : '')), head = el('div', 'tgl-head');
    var btn = el('button', 'tgl-btn', '▸'); btn.type = 'button'; btn.contentEditable = 'false'; btn.setAttribute('aria-label', T('Auf- oder zuklappen'));
    this.t = editable('tgl-title', this.title, T('Ausklappbare Überschrift'));
    this.b = el('div', 'tgl-body'); isolate(this.b);
    btn.addEventListener('click', function () { w.classList.toggle('open'); self.ensure(); if (!S.loading) { S.dirtyBody = true; schedule(); } });
    this.t.addEventListener('keydown', function (e) {   // Enter im Titel -> aufklappen und in den Inhalt springen
      if (e.key !== 'Enter') return;
      e.preventDefault(); e.stopPropagation(); w.classList.add('open');
      var ed = self.ensure(); ed.isReady.then(function () { ed.caret.setToFirstBlock('start'); });
    });
    head.appendChild(btn); head.appendChild(this.t); w.appendChild(head); w.appendChild(this.b);
    TGL_OF.set(w, this);
    this.w = w; if (this.open) this.ensure();          // zugeklappt: Editor erst beim ersten Aufklappen (spart Ladezeit)
    return w;
  };
  ToggleTool.prototype.renderSettings = function () {
    var self = this; if (readOnly()) return [];
    var opts = [[0, T('Ausklappbar')], [1, T('Toggle-Überschrift 1')], [2, T('Toggle-Überschrift 2')], [3, T('Toggle-Überschrift 3')]];
    return opts.filter(function (o) { return o[0] !== self.level; }).map(function (o) {
      return { icon: TGL_IC, title: o[1], closeOnActivate: true, onActivate: function () { self.setLevel(o[0]); } };
    });
  };
  ToggleTool.prototype.setLevel = function (lv) {
    this.level = lv === 1 || lv === 2 || lv === 3 ? lv : 0;
    var w = this.w; w.classList.remove('tgl-h', 'tgl-h1', 'tgl-h2', 'tgl-h3');
    if (this.level) w.classList.add('tgl-h', 'tgl-h' + this.level);
    if (this.block && this.block.dispatchChange) this.block.dispatchChange();
    if (!S.loading) { S.dirtyBody = true; schedule(); }
  };
  ToggleTool.prototype.ensure = function () { if (!this.ed) this.ed = nestedEditor(this.b, this.blocks, T("Inhalt – '/' für Blöcke")); return this.ed; };
  // Seitenwechsel/Block entfernt: den inneren Editor abbauen (sonst bleiben seine document-Listener und damit der ganze
  // alte Inhalt im Speicher, Bughunt P01)
  ToggleTool.prototype.destroy = function () { if (this.ed) killEditor(this.ed); this.ed = null; };
  ToggleTool.prototype.save = function () {
    var self = this, res = Object.assign({}, this.extra, { title: this.t.innerHTML, open: this.w.classList.contains('open') });
    if (this.level) res.level = this.level;
    if (!this.ed) { res.blocks = this.blocks; return res; }
    return this.ed.isReady.then(function () { return self.ed.save(); }).then(function (o) { self.blocks = o.blocks; res.blocks = o.blocks; return res; });
  };

  var CALLOUT_EMOJI = ['💡', '⚠️', '✅', '❗', 'ℹ️', '🔥', '📌'];
  function CalloutTool(o) { this.data = o.data || {}; }
  CalloutTool.toolbox = { title: T('Hinweis'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="M8 10h8M8 14h5"/></svg>' };
  CalloutTool.sanitize = { text: INLINE_SAN };
  CalloutTool.prototype.render = function () {
    var w = el('div', 'callout'), self = this;
    this.emoji = this.data.emoji || '💡';
    var e = el('button', 'callout-emoji', this.emoji); e.type = 'button'; e.contentEditable = 'false';
    e.title = T('Symbol wechseln'); e.setAttribute('aria-label', T('Symbol wechseln'));
    e.addEventListener('click', function () {
      self.emoji = CALLOUT_EMOJI[(CALLOUT_EMOJI.indexOf(self.emoji) + 1) % CALLOUT_EMOJI.length];
      e.textContent = self.emoji; w.dataset.kind = String(CALLOUT_EMOJI.indexOf(self.emoji));
      if (S.editor) { S.dirtyBody = true; schedule(); }
    });
    w.dataset.kind = String(Math.max(0, CALLOUT_EMOJI.indexOf(this.emoji)));
    this.t = editable('callout-text', this.data.text, T('Hinweis …'));
    w.appendChild(e); w.appendChild(this.t); return w;
  };
  CalloutTool.prototype.save = function () { return { emoji: this.emoji, text: this.t.innerHTML }; };

  // --- Vorlagen -------------------------------------------------------------------
  function H2(t) { return { type: 'header', data: { text: t, level: 2 } }; }
  function UL(items, style) { return { type: 'list', data: { style: style || 'unordered', meta: {}, items: items.map(function (x) { return { content: x, meta: style === 'checklist' ? { checked: false } : {}, items: [] }; }) } }; }
  function P(t) { return { type: 'paragraph', data: { text: t || '' } }; }
  var today = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  var BUILTIN = [
    { name: T('Meeting'), icon: '🗓️', title: function () { return T('Meeting') + ' ' + today.format(new Date()); }, blocks: [
      H2(T('Teilnehmer')), UL(['']), H2(T('Agenda')), UL([''], 'ordered'), H2(T('Notizen')), P(''), H2(T('Aufgaben')), UL([''], 'checklist')] },
    { name: T('Störung'), icon: '🚨', title: function () { return T('Störung') + ' ' + today.format(new Date()); }, blocks: [
      { type: 'callout', data: { emoji: '⚠️', text: '<b>' + T('Status:') + '</b> ' + T('offen') + ' · <b>' + T('Priorität:') + '</b> ' } },
      H2(T('Symptome')), P(''), H2(T('Betroffene Systeme')), UL(['']), H2(T('Zeitlinie')), UL(['']),
      H2(T('Ursache')), P(''), H2(T('Lösung')), P(''), H2(T('Nacharbeiten')), UL([''], 'checklist')] },
    { name: T('Kundenbesuch'), icon: '🤝', title: function () { return T('Kundenbesuch') + ' ' + today.format(new Date()); }, blocks: [
      H2(T('Kunde')), { type: 'table', data: { withHeadings: false, content: [[T('Firma'), ''], [T('Ansprechpartner'), ''], [T('Telefon'), ''], [T('Adresse'), '']] } },
      H2(T('Anlass')), P(''), H2(T('Ergebnisse')), UL(['']), H2(T('Nächste Schritte')), UL([''], 'checklist')] }
  ];
  function fromBuiltin(t, parentId) {
    return save().then(function () { return api('POST', '/api/notes', { title: t.title(), icon: t.icon, parent_id: parentId || null }); })
      .then(function (d) {
        return api('PATCH', '/api/notes/' + d.id, { body: JSON.stringify({ blocks: t.blocks }), format: 'blocks' })
          .then(function () { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList(); }).then(function () { return openPage(d.id); });
      })
      // Nr. 134/139: Fokus in den Titel (markiert) wie bei eigenen Vorlagen - sonst lag er auf <body>
      .then(function () { if (S.cur && !readOnly()) { $('pg-title').focus(); $('pg-title').select(); } })
      .catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
  }
  function fromTemplate(p, parentId) {
    return save().then(function () { return api('POST', '/api/notes/' + p.id + '/duplicate', { parent_id: parentId || null }); })
      .then(function (d) { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList().then(function () { return openPage(d.id); }); })
      .then(function () { $('pg-title').focus(); $('pg-title').select(); })
      .catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
  }
  // Menue "+ Neue Seite" offen und jemand tippt gleich los (wie ohne Vorlagen gewohnt): leere Seite mit diesem Titel
  // statt verschluckter Buchstaben (Bughunt 2, Variante von BUG-047). Leertaste waehlt weiter den markierten Eintrag.
  var NPM = null;
  document.addEventListener('keydown', function (e) {
    if (!NPM || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || e.key.length !== 1 || e.key === ' ') return;
    if (NPM.anchor.getAttribute('aria-expanded') !== 'true' || !document.querySelector('.menu:not([hidden])')) { NPM = null; return; }
    var a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return;
    e.preventDefault(); e.stopPropagation();
    var pid = NPM.parentId; NPM = null; HK.closeMenu(); newPage(pid, e.key);
  }, true);
  function newPageMenu(anchor, parentId) {
    NPM = { anchor: anchor, parentId: parentId };
    HK.openMenu(anchor, function (m) {
      m.appendChild(HK.menuItem(T('Leere Seite'), function () { newPage(parentId); }, { icon: '📄' }));
      m.appendChild(HK.menuItem(T('Datenbank'), function () { newDatabase(parentId); }, { icon: '🗃️', hint: T('Tabelle, Board …') }));
      m.appendChild(HK.menuLabel(T('Vorlagen')));
      BUILTIN.forEach(function (t) { m.appendChild(HK.menuItem(t.name, function () { fromBuiltin(t, parentId); }, { icon: t.icon })); });
      S.pages.filter(function (p) { return p.template; }).forEach(function (p) {
        m.appendChild(HK.menuItem(label(p), function () { fromTemplate(p, parentId); }, { icon: ti(p.icon) || '📄', hint: T('eigene') }));
      });
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(T('Vorlagen verwalten …'), function () { manageTemplates({ db: null, list: pageTemplates }); }, { icon: '📐' }));
      m.appendChild(el('div', 'menu-hint', T('Eigene Vorlage: Seite öffnen → ⋯ → „Als Vorlage markieren“')));
    });
  }
  function setTemplate(on) {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { template: on }).then(function () {
      S.cur.template = on; var p = byId(id); if (p) p.template = on; renderTree();
      HK.toast(on ? T('Als Vorlage markiert – erscheint unter „+ Neue Seite“') : T('Keine Vorlage mehr'));
    }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }


  // 2.5 Vorlagen verwalten (vorher liessen sie sich nur anlegen): Liste mit Umbenennen (direkt im Feld), Bearbeiten, Duplizieren,
  // Standard fuer "+ Neu" (nur Datenbank), "Keine Vorlage mehr" und Papierkorb. o: { db: id|null, list: fn -> Promise<[{id,title,icon}]>,
  // dflt: fn -> id|null, setDflt: fn(id|null) -> Promise, changed: fn }
  function manageTemplates(o) {
    var items = [], body = null;
    function reload() { return o.list().then(function (l) { items = l || []; if (body) draw(); }); }
    function act(p) {
      return p.then(function () { if (o.changed) o.changed(); return reload(); })
        .catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
    }
    function draw() {
      body.textContent = '';
      if (!items.length) {
        body.appendChild(el('p', 'nt-empty', o.db ? T('Diese Datenbank hat noch keine Vorlagen.') : T('Noch keine eigenen Vorlagen.')));
        body.appendChild(el('p', 'menu-hint', T('Eine Vorlage ist ein Muster mit festem Inhalt und Werten – neue Einträge können daraus entstehen.')));   // D12
      }
      var ul = el('ul', 'tpl-list');
      items.forEach(function (t) {
        var li = el('li', 'tpl-item'), isD = !!(o.dflt && o.dflt() === t.id);
        li.appendChild(icoNode(t.icon, '📐', 'tpl-ic'));
        var name = el('input', 'tpl-name'); name.type = 'text'; name.maxLength = 200; name.value = t.title || ''; name.placeholder = T('Unbenannt');
        name.setAttribute('aria-label', T('Name der Vorlage'));
        name.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); name.blur(); } });
        name.addEventListener('change', function () {
          var v = name.value.trim(); if (v === (t.title || '')) return;
          act(api('PATCH', '/api/notes/' + t.id, { title: v }).then(function () { HK.toast(T('Gespeichert ✓')); return loadList(); }));   // D12: nicht still
        });
        li.appendChild(name);
        if (isD) li.appendChild(el('span', 'pg-badge', T('Standard')));
        var ed = el('button', 'btn tpl-edit', T('Bearbeiten')); ed.type = 'button';
        ed.addEventListener('click', function () { closeModal(); openPage(t.id); });
        li.appendChild(ed);
        var more = el('button', 'btn tpl-more', '⋯'); more.type = 'button'; more.title = T('Weitere Aktionen'); more.setAttribute('aria-label', T('Weitere Aktionen'));
        more.addEventListener('click', function () {
          HK.openMenu(more, function (m) {
            m.appendChild(HK.menuItem(T('Duplizieren'), function () {
              act(api('POST', '/api/notes/' + t.id + '/duplicate', { parent_id: o.db || t.parent_id || null, template: true,
                title: T('{name} (Kopie)', { name: t.title || T('Vorlage') }) }).then(function () { return loadList(); }));
            }, { icon: '⧉' }));
            if (o.setDflt) m.appendChild(HK.menuItem(isD ? T('Nicht mehr Standard') : T('Als Standard für „+ Neu“'), function () { act(o.setDflt(isD ? null : t.id)); }, { icon: '★', on: isD }));
            m.appendChild(HK.menuItem(T('Keine Vorlage mehr'), function () {
              act(api('PATCH', '/api/notes/' + t.id, { template: false }).then(function () { return isD && o.setDflt ? o.setDflt(null) : null; })
                .then(function () { return loadList(); }));
            }, { icon: '📐', hintBelow: true, hint: o.db ? T('wird ein normaler Eintrag') : T('wird eine normale Seite') }));
            m.appendChild(el('div', 'menu-sep'));
            m.appendChild(HK.menuItem(T('In den Papierkorb'), function () {
              HK.ask({ title: T('Vorlage „{name}“ in den Papierkorb?', { name: t.title || T('Unbenannt') }),
                       text: T('Du kannst sie dort jederzeit wiederherstellen.'), ok: T('In den Papierkorb'), danger: true })
                .then(function (y) {
                  if (y) act(api('DELETE', '/api/notes/' + t.id).then(function () { return isD && o.setDflt ? o.setDflt(null) : null; }).then(function () { return loadList(); }));
                });
            }, { icon: '🗑' }));
          });
        });
        li.appendChild(more); ul.appendChild(li);
      });
      body.appendChild(ul);
      var add = el('button', 'btn btn-primary tpl-add', T('+ Neue Vorlage')); add.type = 'button';
      add.addEventListener('click', function () {
        save().then(function () { return api('POST', '/api/notes', { parent_id: o.db || null, title: T('Neue Vorlage') }); })
          .then(function (d) { return api('PATCH', '/api/notes/' + d.id, { template: true }).then(function () { return d; }); })
          .then(function (d) { if (o.changed) o.changed(); closeModal(); return loadList().then(function () { return openPage(d.id); }); })
          .then(function () { if (S.cur && !readOnly()) { $('pg-title').focus(); $('pg-title').select(); } })
          .catch(function (err) { HK.toast(T('Vorlage nicht angelegt: {err}', { err: err.message })); });
      });
      body.appendChild(add);
    }
    openModal(o.db ? T('Vorlagen dieser Datenbank') : T('Eigene Vorlagen'), function (b) { body = b; body.appendChild(el('p', 'nt-empty', T('Lädt …'))); });
    reload().catch(function (err) { if (body) { body.textContent = ''; body.appendChild(el('p', 'nt-empty', err.message)); } });
  }
  function pageTemplates() {
    return loadList().then(function () {
      return S.pages.filter(function (p) { return p.template && (byId(p.parent_id) || {}).kind !== 'database'; })
        .map(function (p) { return { id: p.id, title: p.title, icon: p.icon, parent_id: p.parent_id }; });
    });
  }

  // --- Datenbanken: Tabellenansicht (Notion-Stil) ----------------------------------
  var TYPES = { text: [T('Text'), '≡'], number: [T('Zahl'), '#'], select: [T('Auswahl'), '◉'], multi_select: [T('Mehrfachauswahl'), '☰'],
                date: [T('Datum'), '▦'], checkbox: [T('Häkchen'), '☑'], url: [T('URL'), '🔗'] };
  var COLORS = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  function rid() { return Math.random().toString(36).slice(2, 10); }
  function optChip(o) { var s = el('span', 'opt opt-' + (o ? o.color : 'gray'), o ? o.name : ''); return s; }
  var fDate2 = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  // --- Datenbanken: Ansichten, Filter, Board ---------------------------------------
  var OPS = {
    text: [['contains', T('enthält')], ['not_contains', T('enthält nicht')], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]],
    number: [['eq', '='], ['gt', '>'], ['lt', '<'], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]],
    select: [['is', T('ist')], ['is_not', T('ist nicht')], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]],
    multi_select: [['contains', T('enthält')], ['not_contains', T('enthält nicht')], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]],
    date: [['eq', T('am')], ['before', T('vor')], ['after', T('nach')], ['today', T('ist heute')], ['this_week', T('ist diese Woche')], ['this_month', T('ist diesen Monat')], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]],
    checkbox: [['checked', T('angehakt')], ['unchecked', T('nicht angehakt')]]
  };
  OPS.url = OPS.text;
  OPS.calc = OPS.number;
  TYPES.calc = [T('Berechnet'), '∑'];
  OPS.relation = [['contains', T('enthält')], ['not_contains', T('enthält nicht')], ['is_empty', T('ist leer')], ['not_empty', T('ist nicht leer')]];
  TYPES.relation = [T('Relation'), '↗'];
  function relRows(target) { return allPages().filter(function (p) { return p.parent_id === target; }); }
  function relChips(ids, clickable) {           // verknuepfte Eintraege als Marken; Klick oeffnet den Eintrag
    var s = el('span', 'rel-chips');
    (ids || []).forEach(function (id) {
      var p = byId(id); if (!p) return;
      var c = el(clickable ? 'button' : 'span', 'rel-chip'); if (clickable) c.type = 'button';
      if (p.icon) c.appendChild(icoNode(p.icon, '', 'rel-ic'));
      c.appendChild(document.createTextNode(label(p)));
      if (clickable) c.addEventListener('click', function (e) { e.stopPropagation(); openPage(id); });
      s.appendChild(c);
    });
    return s;
  }
  var NOVALUE = { is_empty: 1, not_empty: 1, checked: 1, unchecked: 1, today: 1, this_week: 1, this_month: 1 };
  function relDate(op, iso) {                     // relative Datumsfilter wie in Notion ("diese Woche" = Mo-So)
    var n = new Date(), a = new Date(n.getFullYear(), n.getMonth(), n.getDate()), b;
    if (op === 'this_month') { a = new Date(n.getFullYear(), n.getMonth(), 1); b = new Date(n.getFullYear(), n.getMonth() + 1, 1); }
    else if (op === 'this_week') { a.setDate(a.getDate() - (a.getDay() + 6) % 7); b = new Date(a); b.setDate(b.getDate() + 7); }
    else { b = new Date(a); b.setDate(b.getDate() + 1); }
    var s = String(iso || ''), x = new Date(+s.slice(0, 4), +s.slice(5, 7) - 1, +s.slice(8, 10));
    return !isNaN(x) && x >= a && x < b;
  }
  var TITLE_PROP = { id: 'title', name: T('Name'), type: 'text' };
  var VIEW_ICON = { table: '▦', board: '▥', list: '☰', gallery: '▣', calendar: '🗓' };
  // Automatische Eigenschaften (nur lesbar) und Zahlen-Darstellung
  TYPES.created = [T('Erstellt am'), '⏱']; TYPES.edited = [T('Zuletzt bearbeitet'), '✎'];
  var AUTO = { created: 'created', edited: 'updated' };
  OPS.created = OPS.edited = [['after', T('nach')], ['before', T('vor')], ['eq', T('am')], ['today', T('ist heute')], ['this_week', T('ist diese Woche')], ['this_month', T('ist diesen Monat')]];
  function propValue(p, r) { return p.type === 'calc' ? calcValue2(p, r) : AUTO[p.type] ? r[AUTO[p.type]] : (r.props || {})[p.id]; }
  function numLike(p) { return !!p && (p.type === 'number' || p.type === 'calc'); }
  // Werte einer Spalte fuer die Fusszeile (calcValue) und fuer Widgets (calcNum; der Server rechnet gleich:
  // saga/page_widgets.aggregate, gemeinsame Testvektoren tests/widget_vectors.json)
  function calcVals(p, rows) {
    var vals = rows.map(function (r) { var v = p ? propValue(p, r) : r.title; if (p && AUTO[p.type] && v) v = isoDay(v); return v; });
    var nums = vals.filter(function (v) { return typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v))); }).map(Number);
    return { vals: vals, nums: nums };
  }
  // Zahl statt Text (null = "–"): count, checked, pct_checked (ganze %), sum/avg/min/max (auf 2 Stellen) - wie calcValue
  function calcNum(op, p, rows) {
    var cv = calcVals(p, rows), vals = cv.vals, nums = cv.nums, n = vals.length;
    var r2 = function (x) { return Math.round(x * 100) / 100; };
    var s = function () { return nums.reduce(function (a, b) { return a + b; }, 0); };
    var chk = function () { return vals.filter(function (v) { return v === true; }).length; };
    switch (op) {
      case 'count': return n;
      case 'checked': return chk();
      case 'pct_checked': return n ? Math.round(chk() / n * 100) : null;
      case 'sum': return nums.length ? r2(s()) : null;
      case 'avg': return nums.length ? r2(s() / nums.length) : null;
      case 'min': return nums.length ? r2(Math.min.apply(null, nums)) : null;
      case 'max': return nums.length ? r2(Math.max.apply(null, nums)) : null;
    }
    return null;
  }
  // Filterwert einer Zahlenspalte: Eingabe in der Sprache der Oberflaeche, gespeichert als "1200.5" (BUG-013)
  function numStore(x) { var n = HK.parseNum(x); return String(x).trim() === '' || isNaN(n) ? String(x).trim() : String(n); }
  var CALC_P = [['div', 'A ÷ B', '÷'], ['pct', T('A in % von B'), '%'], ['mul', 'A × B', '×'], ['add', 'A + B', '+'], ['sub', 'A − B', '−']];
  // "Berechnet": aus zwei Zahlen-Spalten der Zeile, immer frisch (wird nie gespeichert)
  function calcValue2(p, r) {
    var pv = r.props || {}, num = function (k) { var v = pv[k]; return v == null || v === '' || isNaN(Number(v)) ? null : Number(v); };
    var a = num(p.a), b = num(p.b), op = p.op || 'div', v;
    if (op === 'add' || op === 'sub') { if (a == null && b == null) return null; a = a || 0; b = b || 0; }
    else if (a == null || b == null) return null;
    if (op === 'div' || op === 'pct') { if (!b) return null; v = a / b * (op === 'pct' ? 100 : 1); }
    else v = op === 'mul' ? a * b : op === 'add' ? a + b : a - b;
    return Math.round(v * 10000) / 10000;
  }
  var fStamp = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function stampText(ts) { return ts ? fStamp.format(new Date(ts * 1000)) : ''; }
  function isoDay(ts) { var d = new Date(ts * 1000); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  var fNum = new Intl.NumberFormat(window.SAGA_LOCALE, { maximumFractionDigits: 2 }), fEur = new Intl.NumberFormat(window.SAGA_LOCALE, { style: 'currency', currency: 'EUR' });
  var fInt = new Intl.NumberFormat(window.SAGA_LOCALE, { maximumFractionDigits: 0 });
  // Fortschritt (Ring/Balken): ganze Prozent wie Notion
  function numText(p, v) { return p.fmt === 'percent' ? ((p.show === 'ring' || p.show === 'bar') ? fInt : fNum).format(v) + ' %' : p.fmt === 'euro' ? fEur.format(v) : fNum.format(v); }
  function ring(pct) {
    var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg'), C = 2 * Math.PI * 7.5;
    svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('class', 'nfmt-ring'); svg.setAttribute('aria-hidden', 'true');
    [['nfmt-track', C + ' 0'], ['nfmt-prog', (pct / 100 * C).toFixed(2) + ' ' + C]].forEach(function (x) {
      var c = document.createElementNS(NS, 'circle'); c.setAttribute('cx', '10'); c.setAttribute('cy', '10'); c.setAttribute('r', '7.5');
      c.setAttribute('class', x[0]); c.setAttribute('stroke-dasharray', x[1]); c.setAttribute('transform', 'rotate(-90 10 10)'); svg.appendChild(c);
    });
    return svg;
  }
  function numNode(p, v) {
    var s = el('span', 'nfmt'), pct = Math.max(0, Math.min(100, Number(v) || 0));
    s.appendChild(el('span', 'nfmt-t', numText(p, v)));
    if (p.show === 'ring') s.appendChild(ring(pct));
    if (p.show === 'bar') { var b = el('span', 'nfmt-bar'), f = el('span', 'nfmt-fill'); f.style.width = pct + '%'; b.appendChild(f); s.appendChild(b); }
    return s;
  }

  // Textfeld in einem Menue: schon beim Tippen speichern (verzoegert), sofort bei Enter und beim Verlassen.
  // 'change' allein reicht NICHT: Safari feuert es nicht, wenn das Menue beim Schliessen das Feld entfernt
  // (Chrome schon) - Umbenennungen gingen dort still verloren.
  function liveText(inp, apply, onEnter) {
    var t = null, last = inp.value;
    function go() { clearTimeout(t); if (inp.value === last) return; last = inp.value; apply(inp.value); }
    inp.addEventListener('input', function () { clearTimeout(t); t = setTimeout(go, 450); });
    inp.addEventListener('blur', go);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); go(); if (onEnter) onEnter(); } });
  }

  // --- Lange Tabellen/Listen (2.2): nur der sichtbare Ausschnitt steht im DOM -------------------------------------------
  // Ab VLIST_MIN Eintraegen setzt vlist nur die Eintraege im Sichtfenster (+ Vorlauf VLIST_OVER px oben/unten) ein;
  // Platzhalter halten die volle Scrollhoehe. Gemessene Hoehen merkt sich die Ansicht je Eintrag (hcache), unbekannte
  // werden geschaetzt. Der Eintrag mit dem Fokus bleibt immer im DOM (laufende Eingaben gehen nicht verloren); Tab/Enter
  // laufen ueber den Vorlauf weiter. Drucken: vorher alle Eintraege einsetzen, danach wieder nur den Ausschnitt.
  // box: Eltern der Eintraege (tbody bzw. Liste), make(i) -> Knoten, key(i) -> Schluessel fuer hcache, pad(px) ->
  // Platzhalter, end: fester letzter Knoten in box (oder null), after(nodes): nach dem Einsetzen neuer Knoten.
  var VLIST_MIN = 150, VLIST_OVER = 900, BOARD_PAGE = 50;
  // Drucken: im Board alle Karten einsetzen (sonst nur die ersten je Spalte)
  window.addEventListener('beforeprint', function () { [].forEach.call(document.querySelectorAll('.board-more'), function (b) { if (b.hkAll) b.hkAll(); }); });
  function vlist(box, n, make, key, pad, end, hcache, after) {
    var H = new Array(n), nodes = new Map(), pads = [], raf = 0, all = false, clips = null, pin = -1, est = hcache.est || 38;
    var idxOf = new Map(), rowRO = window.ResizeObserver ? new ResizeObserver(function (es) { rowsResized(es); }) : null;
    for (var i = 0; i < n; i++) H[i] = hcache.get(key(i)) || 0;
    function off() { var o = new Array(n + 1); o[0] = 0; for (var k = 0; k < n; k++) o[k + 1] = o[k] + (H[k] || est); return o; }
    function clipRange() {                       // sichtbarer Bereich (Fenster geschnitten mit scrollenden Vorfahren)
      var c0 = 0, c1 = window.innerHeight || document.documentElement.clientHeight;
      if (!clips) {
        clips = [];
        for (var x = box.parentElement; x && x !== document.body && x !== document.documentElement; x = x.parentElement) {
          var cs = getComputedStyle(x); if (cs.overflowY !== 'visible') clips.push(x);
        }
      }
      clips.forEach(function (x) { var r = x.getBoundingClientRect(); if (r.height) { c0 = Math.max(c0, r.top); c1 = Math.min(c1, r.bottom); } });
      return [c0, c1];
    }
    function focusedIdx() {
      var a = document.activeElement, hit = -1;
      if (!a || !box.contains(a)) return -1;
      nodes.forEach(function (nd, k) { if (nd.contains(a)) hit = k; });
      return hit;
    }
    function insert(k, prev) {
      var nd = make(k); nodes.set(k, nd);
      box.insertBefore(nd, prev ? prev.nextSibling : box.firstChild);
      if (rowRO) { idxOf.set(nd, k); rowRO.observe(nd); }
      return nd;
    }
    function drop(k, nd) { nd.remove(); nodes.delete(k); if (rowRO) { rowRO.unobserve(nd); idxOf.delete(nd); } }
    var seen = false, tries = 0;
    function update() {
      raf = 0;
      if (!box.isConnected) {                    // noch nicht eingehaengt (eingebettet): spaeter; schon entfernt: abmelden
        if (seen) detach(); else if (tries++ < 300) schedule();
        return;
      }
      seen = true;
      if (all) return;
      var o = off(), top = box.getBoundingClientRect().top, c = clipRange(), y0, y1;
      if (c[1] <= c[0]) { y0 = 0; y1 = 600; } else { y0 = c[0] - top - VLIST_OVER; y1 = c[1] - top + VLIST_OVER; }
      var a = 0; while (a < n - 1 && o[a + 1] <= y0) a++;
      var b = a; while (b < n - 1 && o[b + 1] < y1) b++;
      var want = {}, f = focusedIdx(), W = [];
      for (var k = a; k <= b && n; k++) want[k] = 1;
      if (f >= 0) want[f] = 1;
      if (pin >= 0 && pin < n) want[pin] = 1;
      pads.forEach(function (p) { p.remove(); }); pads = [];
      nodes.forEach(function (nd, k) { if (!want[k]) drop(k, nd); });
      Object.keys(want).forEach(function (k) { W.push(+k); }); W.sort(function (x, y) { return x - y; });
      var prev = null, fresh = [];
      W.forEach(function (k) { var nd = nodes.get(k); if (!nd) { nd = insert(k, prev); fresh.push(nd); } prev = nd; });
      if (fresh.length && after) after(fresh);
      // Bughunt P06: Hoehen misst der ResizeObserver der Zeilen (nach dem Layout dieses Frames, kein erzwungenes Layout
      // hier); nur ohne ResizeObserver wie frueher sofort per offsetHeight
      var changed = rowRO ? false : measure(W);
      place(W);
      if (changed) schedule();                   // geschaetzte Hoehen korrigiert: Ausschnitt noch einmal pruefen
    }
    // Neue/geaenderte Zeilenhoehen (ResizeObserver): laeuft nach dem Layout, offsetHeight kostet hier nichts. Weicht eine
    // Hoehe von der fuer die Platzhalter angenommenen ab, die Platzhalter gleich (noch vor dem Zeichnen) neu setzen und
    // den Ausschnitt im naechsten Frame pruefen - wie frueher nach measure().
    function rowsResized(es) {
      if (all) return;
      var changed = false, sum = 0, cnt = 0;
      es.forEach(function (e) {
        var nd = e.target, k = idxOf.get(nd);
        if (k == null || nodes.get(k) !== nd) return;
        var hh = nd.offsetHeight; if (!hh) return;
        sum += hh; cnt++;
        if (hh !== (H[k] || est)) changed = true;
        if (hh !== H[k]) { H[k] = hh; hcache.set(key(k), hh); }
      });
      if (cnt && !hcache.est) { est = hcache.est = Math.round(sum / cnt); changed = true; }
      if (!changed || !box.isConnected) return;
      pads.forEach(function (p) { p.remove(); }); pads = [];
      var W = []; nodes.forEach(function (nd, k) { W.push(k); }); W.sort(function (x, y) { return x - y; });
      place(W); schedule();
    }
    function measure(W) {
      var changed = false, sum = 0, cnt = 0;
      W.forEach(function (k) {
        var hh = nodes.get(k).offsetHeight; if (!hh) return;
        sum += hh; cnt++;
        if (hh !== H[k]) { H[k] = hh; hcache.set(key(k), hh); changed = true; }
      });
      if (cnt && !hcache.est) { est = hcache.est = Math.round(sum / cnt); changed = true; }
      return changed;
    }
    function place(W) {                          // Platzhalter vor, zwischen und nach den eingesetzten Eintraegen
      var o = off();
      function gap(px, ref) { if (px <= 0) return; var p = pad(px); box.insertBefore(p, ref); pads.push(p); }
      if (!W.length) { gap(o[n], end); return; }
      gap(o[W[0]], nodes.get(W[0]));
      for (var k = 0; k < W.length - 1; k++) if (W[k + 1] > W[k] + 1) gap(o[W[k + 1]] - o[W[k] + 1], nodes.get(W[k + 1]));
      gap(o[n] - o[W[W.length - 1] + 1], end);
    }
    function schedule() { if (!raf) raf = requestAnimationFrame(update); }
    function renderAll() {                       // Drucken: alles einsetzen
      all = true; pads.forEach(function (p) { p.remove(); }); pads = [];
      var prev = null, fresh = [];
      for (var k = 0; k < n; k++) { var nd = nodes.get(k); if (!nd) { nd = insert(k, prev); fresh.push(nd); } prev = nd; }
      if (fresh.length && after) after(fresh);
    }
    function afterPrint() { all = false; update(); }
    var ro = window.ResizeObserver ? new ResizeObserver(schedule) : null;     // sichtbar geworden, Spaltenbreite ...
    function detach() {
      document.removeEventListener('scroll', schedule, true); window.removeEventListener('resize', schedule);
      window.removeEventListener('beforeprint', renderAll); window.removeEventListener('afterprint', afterPrint);
      if (ro) ro.disconnect();
      if (rowRO) rowRO.disconnect();
    }
    document.addEventListener('scroll', schedule, { capture: true, passive: true }); window.addEventListener('resize', schedule);
    window.addEventListener('beforeprint', renderAll); window.addEventListener('afterprint', afterPrint);
    if (ro) ro.observe(box);
    return {
      update: update, schedule: schedule, renderAll: renderAll,
      node: function (k) { return nodes.get(k) || null; },
      reveal: function (k) {                     // Eintrag k einsetzen und ins Bild holen -> Knoten
        if (k < 0 || k >= n) return null;
        pin = k; update(); var nd = nodes.get(k);
        if (nd && nd.scrollIntoView) nd.scrollIntoView({ block: 'nearest' });
        update(); pin = -1; return nodes.get(k) || null;
      }
    };
  }

  // --- Datenbank-Ansicht als Baustein: jede Instanz hat ihren EIGENEN Zustand (Seite, eingebettet, Zeilen-Eigenschaften)
  function makeDbView(BOX, OPTS) {
    OPTS = OPTS || {};
    var DB = { id: null, schema: { props: [] }, rows: [], role: 'owner' };
    function locked() { return !!DB.schema.locked; }
    // Freigabe nur Lesen/Kommentieren: Werte nur anzeigen, keine neuen Eintraege/Spalten; Ansichten (Sortierung, Filter)
    // lassen sich ausprobieren, werden aber nicht gespeichert
    function ro() { return DB.role === 'view' || DB.role === 'comment' || (!OPTS.embedded && !BOX && roRole()); }
    // Eingebettete Datenbank (wie Notions "Linked View"): EIGENE Ansichten im Block, Spalten bleiben gemeinsam
    function views() { return DB.localViews || DB.schema.views; }
    function viewsLocked() { return locked() && !DB.localViews; }   // Sperre schuetzt Datenbank-Ansichten, nicht die eines Blocks
    function cleanViews(vs) {                      // Verweise auf geloeschte Spalten entfernen (der Server bereinigt Block-Ansichten nicht)
      var ok = function (pid) { return pid === 'title' || !!propById(pid); };
      (vs || []).forEach(function (v) {
        ['filters', 'colors', 'sorts'].forEach(function (k) { if (v[k]) { v[k] = v[k].filter(function (f) { return f && ok(f.pid); }); if (!v[k].length) delete v[k]; } });
        ['hidden', 'order'].forEach(function (k) { if (v[k]) { v[k] = v[k].filter(ok); if (!v[k].length) delete v[k]; } });
        if (v.widths) Object.keys(v.widths).forEach(function (k) { if (!ok(k)) delete v.widths[k]; });
        ['group', 'group_by', 'date_by'].forEach(function (k) { if (v[k] && !ok(v[k])) delete v[k]; });
      });
      return vs;
    }
    function initLocalViews() {
      var stored = OPTS.getViews && OPTS.getViews();
      if (stored && stored.length) return cleanViews(JSON.parse(JSON.stringify(stored)));
      // Erste Einbettung / alte Einbettung: nur die Ansicht uebernehmen, die der Block gerade zeigt
      var cur = OPTS.getView && OPTS.getView(), base = DB.schema.views.find(function (v) { return v.id === cur; }) || DB.schema.views[0];
      return [JSON.parse(JSON.stringify(base))];
    }
    function lockedHint() { HK.toast(T('🔒 Die Datenbank ist gesperrt – unter „⚙ Ansicht“ entsperren.')); }
    function setLocked(on) { DB.schema.locked = on || undefined; if (!on) delete DB.schema.locked; saveSchema().then(renderDb); HK.toast(on ? T('🔒 Datenbank gesperrt') : T('🔓 Datenbank entsperrt')); }
    function rowColor(r) {                         // bedingte Farbe: erste passende Regel der Ansicht
      var cr = curView().colors || [];
      for (var i = 0; i < cr.length; i++) { try { if (matchFilter(r, cr[i])) return cr[i].color; } catch (e) { /* unvollstaendige Regel */ } }
      return null;
    }
    function tint(node, r) { var c = rowColor(r); if (c) node.classList.add('rc', 'rc-' + c); return node; }
    function setViewId(id) { if (OPTS.setView) OPTS.setView(id); else HK.store('hk-db-view-' + DB.id, id); }
    function propById(pid) { return DB.schema.props.find(function (p) { return p.id === pid; }); }

    function saveSchema() {
      if (ro()) return Promise.resolve();                                        // nur Lesen: nichts speichern
      if (DB.localViews && OPTS.saveViews) OPTS.saveViews(DB.localViews);        // Block-Ansichten -> Seiteninhalt
      var snap = JSON.stringify(DB.schema);
      if (snap === DB.snap) return Promise.resolve();                            // Datenbank selbst unveraendert
      // srev: Stand des Schemas - der Server loescht so keine Spalten, die inzwischen jemand anderes angelegt hat (BUG-001)
      return api('PATCH', '/api/notes/' + DB.id, { schema: DB.schema }).then(function (d) {
        if (d && d.srev != null) { DB.schema.srev = d.srev; snap = JSON.stringify(DB.schema); }
        DB.snap = snap; return d;
      })
        .catch(function (err) { HK.toast(T('Spalte nicht gespeichert: {err}', { err: err.message })); throw err; });
    }
    function setProp(row, pid, value) {
      if (ro()) return Promise.resolve();
      var patch = {}; patch[pid] = value;
      return api('PATCH', '/api/notes/' + row.id, { props: patch }).then(function () {
        if (value === null || value === '' || value === false || (Array.isArray(value) && !value.length)) delete row.props[pid]; else row.props[pid] = value;
      }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
    }

    // Auswahl-Menue fuer select / multi_select, inkl. neue Option anlegen
    // Relation bearbeiten: Eintraege der Ziel-Datenbank suchen, (ab)waehlen oder neu anlegen
    function relMenu(anchor, prop, row, onDone) {
      HK.openMenu(anchor, function (m) {
        var cur = (row.props[prop.id] || []).slice(), target = byId(prop.target);
        var inp = el('input', 'menu-input'); inp.type = 'text'; inp.maxLength = 200; inp.placeholder = T('Eintrag in „{db}“ suchen …', { db: label(target || {}) });
        m.appendChild(inp);
        var list = el('div', 'menu-opts'); m.appendChild(list);
        function toggle(id) {
          var a = cur.slice(), i = a.indexOf(id); if (i >= 0) a.splice(i, 1); else a.push(id);
          HK.closeMenu(); setProp(row, prop.id, a).then(onDone);
        }
        function draw() {
          list.textContent = ''; var q = inp.value.trim().toLowerCase();
          relRows(prop.target).filter(function (p) { return !q || label(p).toLowerCase().indexOf(q) >= 0; }).slice(0, 60).forEach(function (p) {
            var b = el('button', 'menu-item opt-item'); b.type = 'button';
            var l = el('span', 'mi-t'); if (p.icon) l.appendChild(icoNode(p.icon, '', 'rel-ic')); l.appendChild(document.createTextNode(label(p)));
            b.appendChild(l); if (cur.indexOf(p.id) >= 0) b.appendChild(el('span', 'opt-check', '✓'));
            b.addEventListener('click', function () { toggle(p.id); }); list.appendChild(b);
          });
          if (q) {
            var nb = el('button', 'menu-item'); nb.type = 'button'; nb.appendChild(el('span', 'mi-t', T('+ Neu in „{db}“: {name}', { db: label(target || {}), name: inp.value.trim() })));
            nb.addEventListener('click', function () {
              api('POST', '/api/notes', { parent_id: prop.target, title: inp.value.trim() })
                .then(function (d) {
                  var listed = addRowLocal(prop.target, d, inp.value.trim()) ? Promise.resolve() : loadList();   // wie newRow
                  return listed.then(function () { cur.push(d.id); HK.closeMenu(); return setProp(row, prop.id, cur); });
                })
                .then(onDone).catch(function (err) { HK.toast(T('Nicht angelegt: {err}', { err: err.message })); });
            });
            list.appendChild(nb);
          }
          if (!list.childNodes.length) list.appendChild(el('div', 'menu-hint', target ? T('Noch keine Einträge – Namen eintippen.') : T('Ziel-Datenbank fehlt.')));
        }
        inp.addEventListener('input', draw);
        inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); var f = list.querySelector('.menu-item'); if (f) f.click(); } });
        draw(); setTimeout(function () { inp.focus(); }, 0);
      });
    }
    function optionMenu(anchor, prop, row, onDone) {
      HK.openMenu(anchor, function (m) {
        var multi = prop.type === 'multi_select', cur = row.props[prop.id];
        var inp = el('input', 'menu-input'); inp.type = 'text'; inp.maxLength = 40; inp.placeholder = T('Option suchen oder anlegen …');
        m.appendChild(inp);
        var list = el('div', 'menu-opts'); m.appendChild(list);
        function choose(o) {
          var v = multi ? (function () { var a = (cur || []).slice(), i = a.indexOf(o.id); if (i >= 0) a.splice(i, 1); else a.push(o.id); return a; })()
                        : (cur === o.id ? null : o.id);
          HK.closeMenu(); setProp(row, prop.id, v).then(onDone);
        }
        function draw() {
          list.textContent = ''; var q = inp.value.trim().toLowerCase(), any = false;
          (prop.options || []).forEach(function (o) {
            if (q && o.name.toLowerCase().indexOf(q) < 0) return; any = true;
            var b = el('button', 'menu-item opt-item'); b.type = 'button'; b.appendChild(optChip(o));
            var on = multi ? (cur || []).indexOf(o.id) >= 0 : cur === o.id;
            if (on) b.appendChild(el('span', 'opt-check', '✓'));
            b.addEventListener('click', function () { choose(o); }); list.appendChild(b);
          });
          if (q && !(prop.options || []).some(function (o) { return o.name.toLowerCase() === q; })) {
            var nb = el('button', 'menu-item'); nb.type = 'button'; nb.appendChild(document.createTextNode(T('+ Anlegen:') + ' '));
            nb.appendChild(optChip({ name: inp.value.trim(), color: COLORS[(prop.options || []).length % COLORS.length] }));
            if (!locked()) { nb.addEventListener('click', create); list.appendChild(nb); }
          } else if (!any) list.appendChild(el('div', 'menu-hint', T('Noch keine Optionen – Namen eintippen.')));
        }
        function create() {
          if (locked()) { lockedHint(); return; }
          var o = { id: rid(), name: inp.value.trim().slice(0, 40), color: COLORS[(prop.options || []).length % COLORS.length] };
          prop.options = (prop.options || []).concat([o]);
          saveSchema().then(function () { choose(o); });
        }
        inp.addEventListener('input', draw);
        inp.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') { e.preventDefault(); var first = list.querySelector('.menu-item'); if (first) first.click(); }
        });
        draw(); setTimeout(function () { inp.focus(); }, 0);
      });
    }

    // Editor-Element fuer einen Wert (Tabellenzelle und Eigenschaften-Bereich einer Zeile)
    function valueEditor(prop, row, onDone) {
      var v = propValue(prop, row), t = prop.type, wrap = el('div', 'val val-' + t);
      if (ro()) { var dv = valueDisplay(prop, row); wrap.classList.add('val-ro'); wrap.appendChild(dv || el('span', 'val-empty', '–')); return wrap; }
      if (AUTO[t]) { wrap.appendChild(el('span', 'val-auto', stampText(v))); return wrap; }      // automatisch, nur lesbar
      if (t === 'calc') { wrap.appendChild(v == null ? el('span', 'val-empty', '–') : numNode(prop, v)); wrap.title = T('Berechnet – Einstellung im Spaltenkopf'); return wrap; }
      if (t === 'checkbox') {
        var cb = el('input'); cb.type = 'checkbox'; cb.checked = !!v; cb.setAttribute('aria-label', prop.name);
        cb.addEventListener('change', function () { setProp(row, prop.id, cb.checked).then(onDone); });
        wrap.appendChild(cb); return wrap;
      }
      if (t === 'relation') {
        var rb = el('button', 'val-btn'); rb.type = 'button'; rb.setAttribute('aria-label', prop.name);
        if ((v || []).some(function (id) { return byId(id); })) rb.appendChild(relChips(v, false)); else rb.appendChild(el('span', 'val-empty', T('Leer')));
        rb.addEventListener('click', function () { relMenu(rb, prop, row, onDone); });
        wrap.appendChild(rb); return wrap;
      }
      if (t === 'select' || t === 'multi_select') {
        var b = el('button', 'val-btn'); b.type = 'button'; b.setAttribute('aria-label', prop.name);
        var ids = t === 'select' ? (v ? [v] : []) : (v || []);
        ids.forEach(function (id) { var o = (prop.options || []).find(function (x) { return x.id === id; }); if (o) b.appendChild(optChip(o)); });
        if (!ids.length) b.appendChild(el('span', 'val-empty', T('Leer')));
        b.addEventListener('click', function () { optionMenu(b, prop, row, onDone); });
        wrap.appendChild(b); return wrap;
      }
      if (t === 'number' && v != null && (prop.fmt || prop.show)) {        // formatierte Anzeige, Klick = bearbeiten
        var nb = el('button', 'val-btn val-numbtn'); nb.type = 'button'; nb.setAttribute('aria-label', T('{name} bearbeiten', { name: prop.name }));
        nb.appendChild(numNode(prop, v));
        nb.addEventListener('click', function () { wrap.textContent = ''; buildInput(); var i = wrap.querySelector('input'); if (i) { i.focus(); i.select(); } });
        wrap.appendChild(nb); return wrap;
      }
      // Datum in der Sprache der Oberflaeche anzeigen (Nr. 150: <input type=date> zeigt das Format des Browsers, z. B.
      // mm/dd/yyyy in einer deutschen Oberflaeche); Klick = bearbeiten wie bei Zahlen
      if (t === 'date' && typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v)) {
        var dbt = el('button', 'val-btn val-datebtn', fDate2.format(new Date(v + 'T00:00:00'))); dbt.type = 'button';
        dbt.setAttribute('aria-label', T('{name} bearbeiten', { name: prop.name }));
        dbt.addEventListener('click', function () {
          wrap.textContent = ''; buildInput(); var i = wrap.querySelector('input');
          if (i) { i.focus(); try { if (i.showPicker) i.showPicker(); } catch (e) { /* aeltere Browser */ } }
        });
        wrap.appendChild(dbt); return wrap;
      }
      buildInput(); return wrap;
      function buildInput() {
      var inp = el('input', 'val-in'); inp.setAttribute('aria-label', prop.name);
      // Zahl als Text in der Sprache der Oberflaeche ("1.200,5" de): type=number las "1.200" als 1,2 (BUG-013)
      inp.type = t === 'date' ? 'date' : t === 'url' ? 'url' : 'text';
      if (t === 'number') inp.inputMode = 'decimal';
      inp.value = v == null ? '' : t === 'number' ? HK.editNum(v) : String(v); inp.placeholder = t === 'url' ? 'https://…' : '';
      function markEmpty() { inp.classList.toggle('is-empty', !inp.value); }
      markEmpty(); inp.addEventListener('input', markEmpty);
      var start = inp.value;
      function commit() {
        if (inp.value === start) { if (start !== '' && (t === 'date' || (t === 'number' && (prop.fmt || prop.show)))) onDone(); return; }   // zurueck zur Anzeige
        var nv = inp.value.trim(), num = t === 'number' && nv !== '' ? HK.parseNum(nv) : null;
        if (t === 'number' && nv !== '' && isNaN(num)) {                     // keine Zahl: Hinweis, alten Wert zeigen
          HK.toast(T('„{v}“ ist keine Zahl', { v: nv })); inp.value = start; markEmpty(); return;
        }
        start = inp.value;
        setProp(row, prop.id, nv === '' ? null : (t === 'number' ? num : nv)).then(onDone);
      }
      inp.addEventListener('change', commit); inp.addEventListener('blur', commit);
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } if (e.key === 'Escape') { inp.value = start; inp.blur(); } });
      wrap.appendChild(inp);
      if (t === 'url' && v) {
        var a = el('a', 'val-link', '↗'); a.href = v; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.title = T('Öffnen');
        wrap.appendChild(a);
      }
      }
    }

    function sortKey(p, r) {
      if (!p) return (r.title || '').toLowerCase();
      var v = propValue(p, r);
      if (p.type === 'select') { var o = (p.options || []).findIndex(function (x) { return x.id === v; }); return o < 0 ? 1e9 : o; }
      if (p.type === 'multi_select' || p.type === 'relation') return (v || []).length;
      if (p.type === 'checkbox') return v ? 1 : 0;
      return v == null ? null : (typeof v === 'string' ? v.toLowerCase() : v);
    }
    function sortedRows() {                       // bis zu 3 Sortierstufen, Leeres immer nach unten
      var rows = filterRows(DB.rows), keys = (curView().sorts || []).map(function (s) {
        return { p: s.pid === 'title' ? null : propById(s.pid), d: s.dir === 'desc' ? -1 : 1 };
      }).filter(function (k) { return k.p !== undefined; });
      if (!keys.length) return rows;
      rows.sort(function (a, b) {
        for (var i = 0; i < keys.length; i++) {
          var x = sortKey(keys[i].p, a), y = sortKey(keys[i].p, b);
          if (x === y) continue; if (x == null) return 1; if (y == null) return -1;
          return (x < y ? -1 : 1) * keys[i].d;
        }
        return 0;
      });
      return rows;
    }
    function orderedProps(v) {                    // Reihenfolge pro Ansicht (view.order), Rest in Schema-Reihenfolge
      var o = (v || curView()).order || [];
      return DB.schema.props.slice().sort(function (a, b) {
        var x = o.indexOf(a.id), y = o.indexOf(b.id);
        return (x < 0 ? 1e6 + DB.schema.props.indexOf(a) : x) - (y < 0 ? 1e6 + DB.schema.props.indexOf(b) : y);
      });
    }
    function visibleProps() { var h = curView().hidden || []; return orderedProps().filter(function (p) { return h.indexOf(p.id) < 0; }); }
    // Gruppierung (Tabelle/Liste) nach einer Auswahl-Eigenschaft
    function eachGrouped(rows, onHead, onRow) {
      var g = curView().group && propById(curView().group);
      if (!g || g.type !== 'select') { rows.forEach(onRow); return; }
      var known = {}; (g.options || []).forEach(function (o) { known[o.id] = 1; });
      [{ o: null, rows: rows.filter(function (r) { return !known[r.props[g.id]]; }) }].concat((g.options || []).map(function (o) {
        return { o: o, rows: rows.filter(function (r) { return r.props[g.id] === o.id; }) };
      })).forEach(function (c) { if (!c.rows.length) return; onHead(c.o, c.rows.length, g); c.rows.forEach(onRow); });
    }

    function headMenu(th, prop) {
      HK.openMenu(th, function (m) {
        if (ro()) {                                   // nur Lesen: sortieren geht (wird nicht gespeichert)
          m.appendChild(HK.menuLabel(prop ? prop.name : titleName()));
          var rp = prop ? prop.id : 'title';
          m.appendChild(HK.menuItem(T('Aufsteigend sortieren'), function () { setSort(rp, 'asc'); }, { icon: '↑' }));
          m.appendChild(HK.menuItem(T('Absteigend sortieren'), function () { setSort(rp, 'desc'); }, { icon: '↓' }));
          m.appendChild(el('div', 'menu-hint', T('Nur lesen – Änderungen an der Ansicht werden nicht gespeichert.')));
          return;
        }
        if (locked()) { m.appendChild(HK.menuLabel(T('🔒 Datenbank gesperrt'))); m.appendChild(el('div', 'menu-hint', T('Spalten und Ansichten sind geschützt – entsperren unter „⚙ Ansicht“.'))); return; }
        var name = el('input', 'menu-input'); name.type = 'text'; name.maxLength = 60; name.setAttribute('aria-label', T('Spaltenname'));
        name.value = prop ? prop.name : titleName();
        liveText(name, function (val) {
          val = val.trim(); if (!val) return;
          if (prop) prop.name = val; else DB.schema.title_name = val;
          saveSchemaSoon(); renderDb();
        }, function () { HK.closeMenu(); });
        m.appendChild(name);
        var pid = prop ? prop.id : 'title';
        m.appendChild(HK.menuItem(T('Aufsteigend sortieren'), function () { setSort(pid, 'asc'); }, { icon: '↑' }));
        m.appendChild(HK.menuItem(T('Absteigend sortieren'), function () { setSort(pid, 'desc'); }, { icon: '↓' }));
        if ((curView().sorts || []).length) m.appendChild(HK.menuItem(T('Sortierung aufheben'), function () { setSort(null); }, { icon: '✕' }));
        if (!prop) return;
        m.appendChild(el('div', 'menu-sep'));
        if (prop.type === 'calc') {                // Rechnung einstellen: A, B, Rechenart
          var nums = DB.schema.props.filter(function (x) { return x.type === 'number'; });
          if (!nums.length) m.appendChild(el('div', 'menu-hint', T('Lege erst zwei Zahlen-Spalten an (z. B. „Seiten“ und „Gelesen“).')));
          var cop = CALC_P.find(function (x) { return x[0] === (prop.op || 'div'); }) || CALC_P[0];
          m.appendChild(HK.menuItem(T('Rechnung'), null, { icon: '∑', hint: cop[1], sub: function (sm) {
            CALC_P.forEach(function (x) { sm.appendChild(HK.menuItem(x[1], function () { prop.op = x[0]; if (x[0] === 'pct' && !prop.fmt) prop.fmt = 'percent'; saveSchema().then(renderDb); }, { icon: x[2], on: x === cop })); });
          } }));
          ['a', 'b'].forEach(function (k) {
            var cur = nums.find(function (x) { return x.id === prop[k]; });
            m.appendChild(HK.menuItem(T('Zahl {k}', { k: k.toUpperCase() }), null, { icon: k.toUpperCase(), hint: cur ? cur.name : T('wählen'), sub: function (sm) {
              nums.forEach(function (x) { sm.appendChild(HK.menuItem(x.name, function () { prop[k] = x.id; saveSchema().then(renderDb); }, { icon: '#', on: x === cur })); });
            } }));
          });
        }
        if (numLike(prop)) {
          var FMT = [['', T('Zahl'), '#'], ['percent', T('Prozent'), '%'], ['euro', T('Euro'), '€']], SHOW = [['', T('Nur Zahl'), '#'], ['bar', T('Balken (0–100)'), '▬'], ['ring', T('Ring (0–100)'), '◔']];
          var cf = FMT.find(function (f) { return f[0] === (prop.fmt || ''); }), cs = SHOW.find(function (f) { return f[0] === (prop.show || ''); });
          m.appendChild(HK.menuItem(T('Zahlenformat'), null, { icon: '%', hint: cf[1], sub: function (sm) {
            FMT.forEach(function (f) { sm.appendChild(HK.menuItem(f[1], function () { if (f[0]) prop.fmt = f[0]; else delete prop.fmt; saveSchema().then(renderDb); }, { icon: f[2], on: f === cf })); });
          } }));
          m.appendChild(HK.menuItem(T('Anzeigen als'), null, { icon: '◔', hint: cs[1], sub: function (sm) {
            SHOW.forEach(function (f) { sm.appendChild(HK.menuItem(f[1], function () { if (f[0]) prop.show = f[0]; else delete prop.show; saveSchema().then(renderDb); }, { icon: f[2], on: f === cs })); });
          } }));
        }
        if (prop.type === 'relation') {
          var tgt = byId(prop.target);
          m.appendChild(HK.menuItem(T('Zweiseitig'), function () {
            if (prop.target === DB.id) { HK.toast(T('Verknüpfungen innerhalb derselben Datenbank sind nur einseitig möglich.')); return; }
            if (prop.pair && !confirm(T('Zweiseitig ausschalten? Die Gegenspalte in „{db}“ wird samt ihren Einträgen entfernt.', { db: label(tgt || {}) }))) return;
            prop.two_way = !prop.pair;
            saveSchema().then(loadDb).then(function () { refreshDbViews(prop.target); });
          }, { icon: '⇄', on: !!prop.pair, hint: prop.pair ? T('auch in {db}', { db: label(tgt || {}) }) : '' }));
          m.appendChild(HK.menuItem(T('Verknüpft mit'), null, { icon: '↗', hint: label(byId(prop.target) || {}), subSearch: T('Datenbank suchen …'), sub: function (sm) {
            S.pages.filter(function (d) { return d.kind === 'database'; }).forEach(function (d) {
              sm.appendChild(HK.menuItem(label(d), function () {
                if (d.id === prop.target) return;
                if (!confirm(T('Relation auf „{db}“ umstellen? Bisherige Verknüpfungen dieser Spalte werden entfernt.', { db: label(d) }))) return;
                prop.target = d.id; prop.two_way = d.id !== DB.id; DB.rows.forEach(function (r) { delete r.props[prop.id]; });
                saveSchema().then(function () { return Promise.all(DB.rows.map(function (r) { return api('PATCH', '/api/notes/' + r.id, { props: (function () { var x = {}; x[prop.id] = null; return x; })() }).catch(function () {}); })); }).then(loadDb);
              }, { icon: ti(d.icon) || '🗃️', on: d.id === prop.target }));
            });
          } }));
        }
        m.appendChild(HK.menuItem(T('Typ'), null, { icon: TYPES[prop.type][1], hint: TYPES[prop.type][0], sub: function (sm) {
          Object.keys(TYPES).forEach(function (t) {
            sm.appendChild(HK.menuItem(TYPES[t][0], function () {
              if (t === prop.type) return;
              if (!confirm(T('Typ von „{name}“ zu {type} ändern? Werte, die nicht passen, werden entfernt.', { name: prop.name, type: TYPES[t][0] }))) return;
              prop.type = t; if ((t === 'select' || t === 'multi_select') && !prop.options) prop.options = [];
              if (t === 'relation' && !prop.target) prop.target = (S.pages.find(function (d) { return d.kind === 'database' && d.id !== DB.id; }) || { id: DB.id }).id;
              saveSchema().then(loadDb);
            }, { icon: TYPES[t][1], on: t === prop.type }));
          });
        } }));
        m.appendChild(el('div', 'menu-sep'));
        m.appendChild(HK.menuItem(T('Spalte löschen'), function () {
          if (!confirm(T('Spalte „{name}“ samt allen Werten löschen?', { name: prop.name }))) return;
          DB.schema.props = DB.schema.props.filter(function (p) { return p.id !== prop.id; });
          saveSchema().then(loadDb);
        }, { icon: '🗑', danger: true }));
      });
    }
    function addColumn(btn) {
      HK.openMenu(btn, function (m) {
        m.appendChild(HK.menuLabel(T('Neue Spalte')));
        Object.keys(TYPES).forEach(function (t) {
          if (t === 'relation') {
            m.appendChild(HK.menuItem(T('Relation'), null, { icon: '↗', subSearch: T('Datenbank suchen …'), sub: function (sm) {
              sm.appendChild(HK.menuLabel(T('Verknüpfen mit …')));
              S.pages.filter(function (d) { return d.kind === 'database'; }).forEach(function (d) {
                sm.appendChild(HK.menuItem(label(d), function () {
                  DB.schema.props.push({ id: rid(), name: label(d), type: 'relation', target: d.id, two_way: d.id !== DB.id });
                  saveSchema().then(loadDb).then(function () { refreshDbViews(d.id); });
                }, { icon: ti(d.icon) || '🗃️', hint: d.id === DB.id ? T('diese (einseitig)') : T('zweiseitig') }));
              });
            } }));
            return;
          }
          m.appendChild(HK.menuItem(TYPES[t][0], function () {
            var p = { id: rid(), name: TYPES[t][0], type: t }; if (t === 'select' || t === 'multi_select') p.options = [];
            if (t === 'calc') {                      // Vorgabe wie Lesefortschritt: erste Zahl in % der zweiten, als Ring
              var ns = DB.schema.props.filter(function (x) { return x.type === 'number'; });
              p.name = T('Fortschritt'); p.op = 'pct'; p.fmt = 'percent'; p.show = 'ring';
              if (ns[0]) p.a = ns[0].id; if (ns[1]) p.b = ns[1].id;
            }
            DB.schema.props.push(p); saveSchema().then(renderDb);
          }, { icon: TYPES[t][1] }));
        });
      });
    }

    // Enter im Namen der letzten Zeile legt die naechste an. Was bis dahin weitergetippt wird, sammelt typeAhead
    // und landet danach im neuen Namen (sonst ging es verloren bzw. haengte sich an die alte Zeile).
    var typeAhead = null, tableV = null, FRESH = {};
    // Nr. 148: Esc im leeren Namen einer gerade angelegten, noch leeren Zeile nimmt sie wieder weg (Papierkorb)
    function dropFresh(r) {
      delete FRESH[r.id];
      return api('DELETE', '/api/notes/' + r.id).then(function () {
        DB.rows = DB.rows.filter(function (x) { return x.id !== r.id; });
        renderDb();
        var nb = BOX.querySelector('.db-new-row'); if (nb && !nb.hidden) nb.focus();
        return loadList();
      }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
    }
    function isBlankRow(r) {
      return !String(r.title || '').trim() && !Object.keys(r.props || {}).some(function (k) {
        var v = r.props[k]; return v != null && v !== '' && v !== false && !(Array.isArray(v) && !v.length);
      });
    }
    function defaultTpl() {                          // Standardvorlage (2.5), nur wenn es sie noch gibt
      var id = DB.schema && DB.schema.default_tpl;
      return id && (DB.templates || []).some(function (t) { return t.id === id; }) ? id : null;
    }
    function templatesUI() {
      manageTemplates({ db: DB.id,
        list: function () { return loadDb().then(function () { renderDb(); return DB.templates.slice(); }); },
        dflt: defaultTpl,
        setDflt: function (id) { if (id) DB.schema.default_tpl = id; else DB.schema.default_tpl = null; return saveSchema(); },
        changed: function () { refreshDbViews(DB.id); } });
    }
    function newRow(props, blankOnly) {
      if (ro()) return Promise.resolve();
      if (props instanceof Event) props = null;
      if (!props && !blankOnly && defaultTpl()) { typeAhead = null; return newFromTemplate(defaultTpl()); }   // Standardvorlage fuer "+ Neu" (R02: Tippsammler nicht haengen lassen)
      // Nr. 148: noch eine leere, gerade angelegte Zeile da ("+ Neu" mehrmals, Enter auf dem Knopf) -> dorthin statt noch einer
      var blank = !props && curView().type !== 'board' && DB.rows.filter(function (x) { return FRESH[x.id] && isBlankRow(x); })[0];
      var bi = blank && BOX.querySelector('tr[data-id="' + blank.id + '"] .db-title-in');
      if (bi) { typeAhead = null; bi.focus(); return Promise.resolve(); }
      return api('POST', '/api/notes', { parent_id: DB.id, title: '', props: props || undefined }).then(function (d) {
        DB.rows.push({ id: d.id, title: '', icon: '', props: props || {}, updated: d.updated, created: d.updated });
        FRESH[d.id] = true;
        renderDb();
        var inp = BOX.querySelector('tr[data-id="' + d.id + '"] .db-title-in');
        if (!inp && tableV) {                // lange Tabelle: neue Zeile ausserhalb des Ausschnitts -> einsetzen und hinscrollen
          var nd = tableV.reveal(tableV.indexOfRow(d.id)); inp = nd && nd.querySelector('.db-title-in');
        }
        if (inp) {
          inp.focus();
          if (typeAhead) { inp.value = typeAhead; inp.setSelectionRange(inp.value.length, inp.value.length); growTitle(inp); }
        }
        typeAhead = null;   // erst nach renderDb (die alte Zeile sammelt bis dahin)
        // Baum nicht komplett neu laden: Eintrag lokal ergaenzen (fremde Datenbank oder laufender Abgleich: wie bisher)
        var listed = addRowLocal(DB.id, d, '') ? Promise.resolve() : loadList();
        if (curView().type === 'board') {     // Board: neue Karte direkt als Seite oeffnen, Cursor im Titel
          return listed.then(function () { return openRowUI(d.id); })
            .then(function () { if ((curView().open || 'side') === 'page') $('pg-title').focus(); });   // im Peek setzt die Seite selbst den Cursor
        }
        return listed;
      }).catch(function (err) { typeAhead = null; HK.toast(T('Zeile nicht angelegt: {err}', { err: err.message })); });
    }

    function newFromTemplate(tid) {
      return api('POST', '/api/notes/' + tid + '/duplicate', { parent_id: DB.id }).then(function (d) {
        return loadDb().then(function () { return loadList(); }).then(function () { return openRowUI(d.id); });   // Kopie ist keine Vorlage mehr
      }).catch(function (err) { HK.toast(T('Eintrag nicht angelegt: {err}', { err: err.message })); });
    }

    // Namen in Tabellen: Textfeld waechst mit dem Umbruch (wo der Browser field-sizing kann, macht er es selbst)
    var FIELD_SIZING = window.CSS && CSS.supports && CSS.supports('field-sizing', 'content');
    function growTitle(t) { if (FIELD_SIZING || !t.isConnected) return; t.style.height = 'auto'; t.style.height = t.scrollHeight + 'px'; }
    function growTitles(root) { if (!FIELD_SIZING) [].forEach.call(root.querySelectorAll('textarea.db-title-in'), growTitle); }
    // Schatten am Rand, solange seitlich noch mehr Tabelle kommt (sonst wirkt sie abgeschnitten)
    function scrollHints(sc) {
      sc.addEventListener('scroll', function () { scrollHintQ(sc); }, { passive: true });
      if (SH_RO) SH_RO.observe(sc); else scrollHintQ(sc);      // ResizeObserver meldet sich nach observe() von selbst
    }

    function renderDb() {
      var box = BOX; box.textContent = ''; box.classList.toggle('db-ro', ro()); tableV = null; renderGen++;
      if (OPTS.embedded && headOn('title')) box.appendChild(embedHead());
      if (OPTS.embedded) { box.setAttribute('role', 'region'); box.setAttribute('aria-label', label(byId(DB.id) || {})); }   // D09: Name auch ohne sichtbaren Titel
      box.appendChild(viewBar());
      var vt = curView().type;
      if (vt === 'board') { box.appendChild(renderBoard()); return; }
      if (vt === 'list') { var L = renderList(); box.appendChild(L); if (L.vlist) L.vlist.update(); return; }
      if (vt === 'gallery') { box.appendChild(renderGallery()); return; }
      if (vt === 'calendar') { box.appendChild(renderCalendar()); return; }
      var scroll = el('div', 'db-scroll'), tb = el('table', 'db-table'), thead = el('thead'), hr = el('tr');
      var tv = curView(), vis = visibleProps(), widths = tv.widths || {}, cg = el('colgroup'), cols = {};
      var base = {};                                 // gewuenschte Breiten (gespeichert bzw. Standard)
      function addCol(key, w) { var c = el('col'); cg.appendChild(c); if (key) { cols[key] = c; base[key] = w; } }
      addCol('title', widths.title || 260);
      vis.forEach(function (p) { addCol(p.id, widths[p.id] || 170); });
      addCol(null, 0); tb.appendChild(cg);           // letzte (leere) Spalte nimmt den Rest -> Tabelle fuellt die Breite
      // Passt die Tabelle nicht in den Platz (schmale Spalte, Handy), werden alle Spalten anteilig schmaler -
      // bis zu einer Mindestbreite; erst danach wird seitlich gescrollt
      function fit() {
        // zuerst die anderen Spalten (bis 90 px), erst danach den Namen (bis 150 px) - der Name soll lesbar bleiben
        var cw = scroll.clientWidth, keys = Object.keys(base), w = {}, sum = 44;
        keys.forEach(function (k) { w[k] = base[k]; sum += base[k]; });
        function shrink(list, min) {
          var over = sum - cw, room = 0;
          list.forEach(function (k) { room += Math.max(0, w[k] - min); });
          if (over <= 0 || !room) return;
          var f = Math.min(1, over / room);
          list.forEach(function (k) { var d = Math.floor(Math.max(0, w[k] - min) * f); w[k] -= d; sum -= d; });
        }
        if (cw) { shrink(keys.filter(function (k) { return k !== 'title'; }), 90); shrink(['title'], 200); }   // D02: Namen bleiben lesbar, die Tabelle scrollt waagrecht
        keys.forEach(function (k) { cols[k].style.width = w[k] + 'px'; });
        tb.style.minWidth = sum + 'px';
      }
      fit();
      if (window.ResizeObserver) { var lastW = 0; new ResizeObserver(function () { if (scroll.clientWidth !== lastW) { lastW = scroll.clientWidth; fit(); growTitles(tb); } }).observe(scroll); }
      // Spaltenbreite ziehen (Rand rechts im Kopf), pro Ansicht gespeichert
      function resizer(th, key) {
        if (viewsLocked()) return;
        var h = el('span', 'db-rs'); h.title = T('Breite ziehen'); h.setAttribute('aria-hidden', 'true');
        h.addEventListener('click', function (e) { e.stopPropagation(); });
        h.addEventListener('pointerdown', function (e) {
          // Bewegung am ganzen Dokument verfolgen (kein setPointerCapture - das kann werfen und die Handler verhindern)
          e.preventDefault(); e.stopPropagation();
          var x0 = e.clientX, w0 = parseInt(cols[key].style.width, 10), t0 = parseInt(tb.style.minWidth, 10);
          document.body.classList.add('db-resizing');
          function mv(ev) { var w = Math.max(60, Math.min(800, w0 + ev.clientX - x0)); cols[key].style.width = w + 'px'; tb.style.minWidth = (t0 + w - w0) + 'px'; }
          function up() {
            document.removeEventListener('pointermove', mv); document.removeEventListener('pointerup', up);
            document.body.classList.remove('db-resizing');
            var w = parseInt(cols[key].style.width, 10); if (w === w0) return;
            base[key] = w; tv.widths = Object.assign({}, tv.widths || {}); tv.widths[key] = w; saveSchemaSoon();
          }
          document.addEventListener('pointermove', mv); document.addEventListener('pointerup', up);
        });
        th.appendChild(h);
      }
      var th0 = el('th', 'db-th db-th-title'); th0.appendChild(el('span', 'db-ticon', 'Aa')); th0.appendChild(document.createTextNode(' ' + titleName()));
      th0.tabIndex = 0; th0.addEventListener('click', function () { headMenu(th0, null); }); resizer(th0, 'title'); hr.appendChild(th0);
      vis.forEach(function (p) {
        var th = el('th', 'db-th'); th.tabIndex = 0; resizer(th, p.id);
        th.appendChild(el('span', 'db-ticon', TYPES[p.type][1])); th.appendChild(document.createTextNode(' ' + p.name));
        th.addEventListener('click', function () { headMenu(th, p); });
        th.addEventListener('keydown', function (e) { if (e.key === 'Enter') headMenu(th, p); });
        hr.appendChild(th);
      });
      var add = el('th', 'db-th db-th-add'); var ab = el('button', 'db-add-col', '+'); ab.type = 'button'; ab.title = T('Spalte hinzufügen');
      ab.setAttribute('aria-label', T('Spalte hinzufügen')); ab.addEventListener('click', function () { addColumn(ab); }); if (!locked() && !ro()) add.appendChild(ab); hr.appendChild(add);
      thead.appendChild(hr); tb.appendChild(thead);
      var body = el('tbody'), items = [], V = null;
      // Eintraege (Gruppenkopf | Zeile) erst sammeln: kurze Tabellen setzen alle ein, lange nur den Ausschnitt (vlist)
      eachGrouped(sortedRows(), function (o, n, g) { items.push({ o: o, n: n, g: g }); }, function (r) { items.push({ r: r }); });
      function mkHead(it) {
        var gr = el('tr', 'db-grow'), gd = el('td'); gd.colSpan = vis.length + 2;
        gd.appendChild(it.o ? optChip(it.o) : el('span', 'board-none', T('Ohne {name}', { name: it.g.name }))); gd.appendChild(el('span', 'board-n', String(it.n)));
        gr.appendChild(gd); return gr;
      }
      function mkRow(r, idx) {
        var tr = tint(el('tr', 'db-row'), r); tr.dataset.id = r.id;
        var td = el('td', 'db-cell db-title'), ti = el('textarea', 'db-title-in'); ti.rows = 1; ti.maxLength = 200; ti.value = r.title; ti.placeholder = T('Unbenannt');   // Textfeld: lange Namen brechen um
        ti.readOnly = ro();
        ti.setAttribute('aria-label', T('Name'));
        var start = r.title;
        function commitTitle() {
          if (ti.value === start || ro()) return; start = ti.value;
          // sofort lokal uebernehmen: ein gleich folgendes renderDb (naechste Zeile per Enter) zeigt sonst den alten Namen
          var prev = r.title; r.title = ti.value.trim(); var p = byId(r.id); if (p) p.title = r.title;
          api('PATCH', '/api/notes/' + r.id, { title: ti.value })
            .catch(function (err) { r.title = prev; if (p) p.title = prev; HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
        }
        ti.addEventListener('blur', commitTitle);
        // Enter: speichern und weiter in die naechste Zeile; in der letzten Zeile (mit Namen) eine neue anlegen.
        // Der Fokus bleibt immer in einem Namensfeld - frueher landete er auf <body> und Weitergetipptes ging verloren.
        ti.addEventListener('keydown', function (e) {
          if (e.key === 'Escape' && !e.isComposing && FRESH[r.id] && !ti.value.trim() && isBlankRow(r) && typeAhead === null && !ro()) {
            e.preventDefault(); e.stopPropagation(); dropFresh(r); return;
          }
          if (e.key !== 'Enter' || e.isComposing) return;
          e.preventDefault();
          if (typeAhead !== null) return;                       // naechste Zeile entsteht gerade
          commitTitle();
          var nx;
          if (V) { var j = idx + 1; while (j < items.length && !items[j].r) j++; nx = V.reveal(j); }     // naechste Zeile, auch ausserhalb des Ausschnitts
          else { var rows = [].slice.call(body.querySelectorAll('tr.db-row')); nx = rows[rows.indexOf(tr) + 1]; }
          var ni = nx && nx.querySelector('.db-title-in');
          if (ni) { ni.focus(); ni.setSelectionRange(ni.value.length, ni.value.length); return; }
          if (!ti.value.trim()) return;                          // leere letzte Zeile: einfach stehen bleiben
          typeAhead = ''; newRow();
        });
        ti.addEventListener('beforeinput', function (e) {      // waehrend die neue Zeile entsteht: Getipptes sammeln
          if (typeAhead === null) return;
          e.preventDefault();
          if (e.data && /^insert/.test(e.inputType) && e.inputType !== 'insertLineBreak' && e.inputType !== 'insertParagraph') typeAhead += e.data;
        });
        ti.addEventListener('input', function () { if (/\n/.test(ti.value)) ti.value = ti.value.replace(/\s*\n\s*/g, ' '); growTitle(ti); });
        var ob = el('button', 'db-open', T('Öffnen')); ob.type = 'button'; ob.title = T('Als Seite öffnen');
        ob.addEventListener('click', function () { openRowUI(r.id); });
        var tw = el('div', 'db-title-wrap');
        if (r.icon) tw.appendChild(icoNode(r.icon, '', 'db-row-icon'));
        tw.appendChild(ti); tw.appendChild(ob); td.appendChild(tw); tr.appendChild(td);
        vis.forEach(function (p) {
          var c = el('td', 'db-cell'); c.appendChild(valueEditor(p, r, function () {
            var cv = curView();   // betrifft die Aenderung Sortierung/Filter -> neu aufbauen, sonst nur die Zelle
            if ((cv.sorts || []).some(function (s) { return s.pid === p.id; }) || cv.group === p.id || (cv.filters || []).some(function (f) { return f.pid === p.id; })) renderDb(); else refreshCell(c, p, r);
          }));
          tr.appendChild(c);
        });
        tr.appendChild(el('td', 'db-cell db-cell-pad'));
        return tr;
      }
      function mkItem(i) { var it = items[i]; return it.r ? mkRow(it.r, i) : mkHead(it); }
      if (items.length >= VLIST_MIN) {
        V = vlist(body, items.length, mkItem, function (i) { var it = items[i]; return tv.id + ':' + (it.r ? it.r.id : 'g:' + (it.o ? it.o.id : '')); },
          function (px) {
            var p = el('tr', 'db-vpad'), d = el('td'); d.colSpan = vis.length + 2; d.style.height = px + 'px'; d.style.padding = '0'; d.style.border = '0';
            p.setAttribute('aria-hidden', 'true'); p.appendChild(d); return p;
          }, null, DB.vh || (DB.vh = new Map()), function (nodes) { nodes.forEach(function (n) { growTitles(n); }); });
        V.indexOfRow = function (id) { for (var i = 0; i < items.length; i++) if (items[i].r && items[i].r.id === id) return i; return -1; };
      } else items.forEach(function (it, i) { body.appendChild(mkItem(i)); });
      tableV = V;
      tb.dataset.rows = String(items.filter(function (it) { return it.r; }).length);   // Anzahl Zeilen (auch ausserhalb des Ausschnitts)
      tb.appendChild(body);
      var tf = el('tfoot'), fr = el('tr'), fd = el('td'); fd.colSpan = vis.length + 2;
      var nr = el('button', 'db-new-row', T('+ Neu')); nr.type = 'button'; nr.addEventListener('click', newRow); nr.hidden = ro();
      fd.appendChild(nr); fr.appendChild(fd); tf.appendChild(fr); tf.appendChild(calcRow(vis, tv)); tb.appendChild(tf);
      scroll.appendChild(tb); box.appendChild(scroll); scrollHints(scroll);
      if (V) V.update();
    }
    // --- Fusszeile: Berechnung je Spalte (Anzahl, Summe, Durchschnitt, % angehakt ...) - pro Ansicht gespeichert
    var CALC_NAMES = { count: T('Anzahl'), filled: T('Ausgefüllt'), empty: T('Leer'), pct_filled: T('% ausgefüllt'), unique: T('Verschiedene'),
      sum: T('Summe'), avg: T('Durchschnitt'), median: T('Median'), min: T('Min'), max: T('Max'), range: T('Spanne'),
      checked: T('Angehakt'), unchecked: T('Nicht angehakt'), pct_checked: T('% angehakt'), earliest: T('Frühestes'), latest: T('Spätestes') };
    function calcOps(p) {
      var t = p ? (p.type === 'calc' ? 'number' : p.type) : 'text';
      if (t === 'checkbox') return ['count', 'checked', 'unchecked', 'pct_checked'];
      var o = ['count', 'filled', 'empty', 'pct_filled', 'unique'];
      if (t === 'number') o = o.concat(['sum', 'avg', 'median', 'min', 'max', 'range']);
      if (t === 'date' || AUTO[t]) o = o.concat(['earliest', 'latest']);
      return o;
    }
    var fCalcDay = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: 'numeric', month: 'short', year: 'numeric' });
    function calcValue(op, p, rows) {
      var cv = calcVals(p, rows), vals = cv.vals, nums = cv.nums;
      function isEmpty(v) { return v == null || v === '' || v === false || (Array.isArray(v) && !v.length); }
      var n = vals.length, filled = vals.filter(function (v) { return !isEmpty(v); });
      function fmt(x) { x = Math.round(x * 100) / 100; return numLike(p) ? numText(p, x) : fNum.format(x); }
      function pct(a) { return n ? Math.round(a / n * 100) + ' %' : '–'; }
      function sum() { return nums.reduce(function (a, b) { return a + b; }, 0); }
      var days = filled.filter(function (v) { return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v); }).map(function (v) { return v.slice(0, 10); }).sort();
      function day(s) { var q = s.split('-'); return fCalcDay.format(new Date(+q[0], +q[1] - 1, +q[2])); }
      switch (op) {
        case 'count': return String(n);
        case 'filled': return String(filled.length);
        case 'empty': return String(n - filled.length);
        case 'pct_filled': return pct(filled.length);
        case 'unique': var u = {}; filled.forEach(function (v) { (Array.isArray(v) ? v : [v]).forEach(function (x) { u[String(x)] = 1; }); }); return String(Object.keys(u).length);
        case 'sum': return nums.length ? fmt(sum()) : '–';
        case 'avg': return nums.length ? fmt(sum() / nums.length) : '–';
        case 'median': if (!nums.length) return '–'; var s = nums.slice().sort(function (a, b) { return a - b; }), m = s.length >> 1; return fmt(s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2);
        case 'min': return nums.length ? fmt(Math.min.apply(null, nums)) : '–';
        case 'max': return nums.length ? fmt(Math.max.apply(null, nums)) : '–';
        case 'range': return nums.length ? fmt(Math.max.apply(null, nums) - Math.min.apply(null, nums)) : '–';
        case 'checked': return String(vals.filter(function (v) { return v === true; }).length);
        case 'unchecked': return String(vals.filter(function (v) { return v !== true; }).length);
        case 'pct_checked': return pct(vals.filter(function (v) { return v === true; }).length);
        case 'earliest': return days.length ? day(days[0]) : '–';
        case 'latest': return days.length ? day(days[days.length - 1]) : '–';
      }
      return '';
    }
    function calcRow(vis, tv) {
      var tr = el('tr', 'db-calc-row'), rows = sortedRows(), calcs = tv.calcs || {};
      [null].concat(vis).forEach(function (p) {
        var key = p ? p.id : 'title', op = calcs[key], td = el('td', 'db-calc');
        var b = el('button', 'db-calc-b' + (op ? ' on' : '')); b.type = 'button';
        if (op) { b.appendChild(el('span', 'db-calc-n', CALC_NAMES[op])); b.appendChild(el('span', 'db-calc-v', calcValue(op, p, rows))); }
        else b.textContent = T('Berechnen ▾');
        b.addEventListener('click', function () {
          if (viewsLocked()) { lockedHint(); return; }
          HK.openMenu(b, function (m) {
            function set(o) { var c = Object.assign({}, tv.calcs || {}); if (o) c[key] = o; else delete c[key]; if (Object.keys(c).length) tv.calcs = c; else delete tv.calcs; saveSchemaSoon(); renderDb(); }
            m.appendChild(HK.menuItem(T('Keine'), function () { set(null); }, { on: !op }));
            calcOps(p).forEach(function (o) { m.appendChild(HK.menuItem(CALC_NAMES[o], function () { set(o); }, { on: op === o, hint: calcValue(o, p, rows) })); });
          });
        });
        td.appendChild(b); tr.appendChild(td);
      });
      tr.appendChild(el('td', 'db-calc'));
      return tr;
    }
    function refreshCell(cell, p, r) { cell.textContent = ''; cell.appendChild(valueEditor(p, r, function () { refreshCell(cell, p, r); })); }

    function loadDb() {
      return api('GET', '/api/notes/' + DB.id + '/rows').then(function (d) {
        DB.schema = d.schema; DB.rows = d.rows; DB.templates = d.templates || []; ensureViews(); DB.snap = JSON.stringify(DB.schema);
        DB.role = d.role || 'owner';
        if (OPTS.getViews) {
          var had = (OPTS.getViews() || []).length; DB.localViews = initLocalViews();
          if (!had && OPTS.saveViews) OPTS.saveViews(DB.localViews);   // einmalig im Block ablegen -> ab jetzt unabhaengig
        }
        renderDb();
      }).catch(function (err) { HK.toast(T('Datenbank nicht geladen: {err}', { err: err.message })); });
    }

    // Eigenschaften-Bereich oben auf einer Zeilen-Seite
    function titleName() { return DB.schema.title_name || T('Name'); }
    function fprop(pid) { return pid === 'title' ? { id: 'title', name: titleName(), type: 'text' } : propById(pid); }

    function ensureViews() {
      if (!DB.schema.views || !DB.schema.views.length) {
        var v = { id: 'tabelle', name: T('Tabelle'), type: 'table' };
        try { var old = JSON.parse(HK.store('hk-db-sort-' + DB.id) || 'null'); if (old) v.sort = old; } catch (e) { /* alte Sortierung egal */ }
        DB.schema.views = [v];
      }
    }
    function curView() {
      var id = OPTS.getView ? OPTS.getView() : HK.store('hk-db-view-' + DB.id);
      var v = views().find(function (x) { return x.id === id; }) || views()[0];
      if (v && v.sort) { if (!v.sorts) v.sorts = [v.sort]; delete v.sort; }   // altes Einzelfeld -> Liste
      return v;
    }
    var saveT = null, searchT = null;
    // Ansicht-Einstellungen (wie Notions "View settings")
    function viewSettings(anchor) {
      var v = curView();
      HK.openMenu(anchor, function (m) {
        var box = el('div', 'flt-box vs');
        function commit() { saveSchemaSoon(); renderDb(); draw(); }
        function sec(txt) { box.appendChild(el('div', 'menu-label', txt)); }
        function row(lbl, ctrl) { var l = el('label', 'vs-row'); l.appendChild(el('span', 'vs-lbl', lbl)); l.appendChild(ctrl); box.appendChild(l); }
        function sel(opts, val, fn) {
          var s = el('select', 'flt-val'); opts.forEach(function (o) { var e = el('option', null, o[1]); e.value = o[0]; s.appendChild(e); });
          s.value = val == null ? '' : String(val); s.addEventListener('change', function () { fn(s.value); }); return s;
        }
        function propOpts(withTitle) { return (withTitle ? [['title', titleName()]] : []).concat(DB.schema.props.map(function (p) { return [p.id, p.name]; })); }
        function draw() {
          box.textContent = '';
          if (OPTS.embedded && OPTS.setHead) {           // eingebetteter Block: Kopf ein-/ausblenden (gilt nur fuer diesen Block)
            sec(T('Dieser Block'));
            [['title', T('Datenbanktitel anzeigen')], ['tabs', T('Ansichts-Reiter anzeigen')]].forEach(function (h) {
              var l = el('label', 'vs-row vs-check'), cb = el('input'); cb.type = 'checkbox'; cb.checked = headOn(h[0]);
              cb.addEventListener('change', function () { OPTS.setHead(h[0], cb.checked); renderDb(); draw(); });
              l.appendChild(cb); l.appendChild(el('span', 'vs-lbl', h[1])); box.appendChild(l);
            });
          }
          if (DB.role === 'owner') {    // Vorlagen der Datenbank (2.5): auch erreichbar, wenn es noch keine gibt - eigener Abschnitt (D10)
            if (OPTS.embedded && OPTS.setHead) sec(T('Vorlagen'));
            var tb = el('button', 'menu-item vs-tpl', '📐 ' + T('Vorlagen verwalten …') + ((DB.templates || []).length ? ' (' + DB.templates.length + ')' : ''));
            tb.type = 'button'; tb.addEventListener('click', function () { HK.closeMenu(); templatesUI(); }); box.appendChild(tb);
          }
          if (OPTS.embedded && OPTS.setHead && !viewsLocked()) sec(T('Ansicht'));
          if (viewsLocked()) {
            box.appendChild(el('div', 'menu-hint vs-locked', T('🔒 Diese Datenbank ist gesperrt: Spalten, Ansichten, Filter und Sortierung sind geschützt. Einträge kannst du weiter bearbeiten.')));
            var ul = el('button', 'menu-item', T('🔓 Entsperren')); ul.type = 'button';
            ul.addEventListener('click', function () { HK.closeMenu(); setLocked(false); }); box.appendChild(ul);
            return;
          }
          var name = el('input', 'flt-val'); name.type = 'text'; name.maxLength = 40; name.value = v.name;
          liveText(name, function (val) { if (val.trim()) { v.name = val.trim(); saveSchemaSoon(); renderDb(); } });
          row(T('Name'), name);
          row(T('Layout'), sel([['table', '▦ ' + T('Tabelle')], ['board', '▥ ' + T('Board')], ['list', '☰ ' + T('Liste')], ['gallery', '▣ ' + T('Galerie')], ['calendar', '🗓 ' + T('Kalender')]], v.type, function (x) { v.type = x; commit(); }));
          var sels = DB.schema.props.filter(function (p) { return p.type === 'select'; }).map(function (p) { return [p.id, p.name]; });
          if (v.type === 'board') row(T('Spalten nach'), sel(sels.length ? sels : [['', T('– keine Auswahl-Eigenschaft –')]], v.group_by, function (x) { if (x) v.group_by = x; commit(); }));
          if (v.type === 'table' || v.type === 'list') row(T('Gruppieren'), sel([['', T('nicht gruppieren')]].concat(sels), v.group, function (x) { if (x) v.group = x; else delete v.group; commit(); }));
          row(T('Öffnen als'), sel([['side', '◨ ' + T('Seitenansicht rechts')], ['left', '◧ ' + T('Seitenansicht links')], ['center', '▣ ' + T('Mittig')], ['page', '▭ ' + T('Ganze Seite')]], v.open || 'side',
            function (x) { if (x === 'side') delete v.open; else v.open = x; commit(); }));
          if (v.type === 'calendar') {
            var dps = DB.schema.props.filter(function (p) { return p.type === 'date' || AUTO[p.type]; }).map(function (p) { return [p.id, p.name]; });
            row(T('Datum aus'), sel(dps.length ? dps : [['', T('– keine Datumsspalte –')]], v.date_by, function (x) { if (x) v.date_by = x; commit(); }));
          }
          if (v.type === 'gallery') row(T('Kartengröße'), sel([['s', T('Klein')], ['', T('Mittel')], ['l', T('Groß')]], v.card || '', function (x) { if (x) v.card = x; else delete v.card; commit(); }));
          sec(T('Eigenschaften (anzeigen · Reihenfolge dieser Ansicht)'));
          var op = orderedProps(v);
          op.forEach(function (p, i) {
            var l = el('div', 'vs-prop'), cb = el('input'); cb.type = 'checkbox'; cb.checked = (v.hidden || []).indexOf(p.id) < 0;
            cb.setAttribute('aria-label', T('{name} anzeigen', { name: p.name }));
            cb.addEventListener('change', function () {
              var h = (v.hidden || []).filter(function (x) { return x !== p.id; }); if (!cb.checked) h.push(p.id);
              if (h.length) v.hidden = h; else delete v.hidden; commit();
            });
            l.appendChild(cb); l.appendChild(el('span', 'vs-pname', TYPES[p.type][1] + ' ' + p.name));
            [['↑', -1], ['↓', 1]].forEach(function (d) {
              var b = el('button', 'vs-mv', d[0]); b.type = 'button'; b.disabled = i + d[1] < 0 || i + d[1] >= op.length;
              b.setAttribute('aria-label', d[1] < 0 ? T('{name} nach oben', { name: p.name }) : T('{name} nach unten', { name: p.name }));
              b.addEventListener('click', function () {   // nur diese Ansicht
                var a = op.map(function (x) { return x.id; }), j = i + d[1], x = a[i]; a[i] = a[j]; a[j] = x; v.order = a; commit();
              });
              l.appendChild(b);
            });
            box.appendChild(l);
          });
          sec(T('Sortierung (bis zu 3 Stufen)'));
          var ss = v.sorts || [];
          ss.forEach(function (s, i) {
            var l = el('div', 'vs-sort');
            l.appendChild(sel(propOpts(true), s.pid, function (x) { s.pid = x; commit(); }));
            l.appendChild(sel([['asc', T('aufsteigend')], ['desc', T('absteigend')]], s.dir, function (x) { s.dir = x; commit(); }));
            var x = el('button', 'flt-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Sortierstufe entfernen'));
            x.addEventListener('click', function () { ss.splice(i, 1); if (ss.length) v.sorts = ss; else delete v.sorts; commit(); });
            l.appendChild(x); box.appendChild(l);
          });
          if (ss.length < 3) {
            var add = sel([['', T('+ Sortierung hinzufügen …')]].concat(propOpts(true)), '', function (x) { if (x) { v.sorts = ss.concat([{ pid: x, dir: 'asc' }]); commit(); } });
            add.classList.add('flt-add'); box.appendChild(add);
          }
          sec(T('Bedingte Farbe (erste passende Regel)'));
          var COL = [['red', '🟥 ' + T('Rot')], ['orange', '🟧 ' + T('Orange')], ['yellow', '🟨 ' + T('Gelb')], ['green', '🟩 ' + T('Grün')], ['blue', '🟦 ' + T('Blau')],
                     ['purple', '🟪 ' + T('Lila')], ['pink', '🩷 ' + T('Rosa')], ['brown', '🟫 ' + T('Braun')], ['gray', '⬜ ' + T('Grau')]];
          var cr = v.colors || [];
          cr.forEach(function (f, i) {
            var pp = fprop(f.pid) || fprop('title'), ops = OPS[pp.type] || OPS.text, l = el('div', 'vs-rule');
            l.appendChild(sel(propOpts(true), f.pid, function (x) { f.pid = x; f.op = (OPS[(fprop(x) || {}).type] || OPS.text)[0][0]; f.value = ''; commit(); }));
            l.appendChild(sel(ops.map(function (o) { return [o[0], o[1]]; }), f.op, function (x) { f.op = x; commit(); }));
            if (!NOVALUE[f.op]) {
              if (pp.type === 'select' || pp.type === 'multi_select' || pp.type === 'relation') {
                var src = pp.type === 'relation' ? relRows(pp.target).map(function (x) { return [String(x.id), label(x)]; }) : (pp.options || []).map(function (o) { return [o.id, o.name]; });
                l.appendChild(sel([['', T('– wählen –')]].concat(src), f.value, function (x) { f.value = x; commit(); }));
              } else {
                var vi = el('input', 'flt-val'); vi.type = (pp.type === 'date' || AUTO[pp.type]) ? 'date' : 'text';
                if (numLike(pp)) vi.inputMode = 'decimal';
                vi.value = numLike(pp) ? HK.editNum(f.value) : f.value || ''; vi.setAttribute('aria-label', T('Wert'));
                liveText(vi, function (x) { f.value = numLike(pp) ? numStore(x) : x; saveSchemaSoon(); renderDb(); }); l.appendChild(vi);
              }
            }
            l.appendChild(sel(COL, f.color, function (x) { f.color = x; commit(); }));
            var x = el('button', 'flt-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Regel entfernen'));
            x.addEventListener('click', function () { cr.splice(i, 1); if (cr.length) v.colors = cr; else delete v.colors; commit(); });
            l.appendChild(x); box.appendChild(l);
          });
          if (cr.length < 10) {
            var ar = el('button', 'menu-item', T('+ Farbregel hinzufügen')); ar.type = 'button';
            ar.addEventListener('click', function () { v.colors = cr.concat([{ pid: 'title', op: OPS.text[0][0], value: '', color: 'red' }]); commit(); });
            box.appendChild(ar);
          }
          sec(T('Weiteres'));
          var nf = (v.filters || []).length;
          var fb = el('button', 'menu-item', T('Filter bearbeiten …') + (nf ? ' (' + nf + ')' : '')); fb.type = 'button';
          fb.addEventListener('click', function () { HK.closeMenu(); var c = BOX.querySelector('.db-filter'); setTimeout(function () { filterMenu(c || anchor); }, 0); });
          box.appendChild(fb);
          var cl = el('button', 'menu-item', T('🔗 Link zur Datenbank kopieren')); cl.type = 'button';
          cl.addEventListener('click', function () {
            var u = location.origin + '/notes#' + DB.id; HK.closeMenu();
            (navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject(new Error('x'))).then(function () { HK.toast(T('Link kopiert')); }, function () { HK.toast(u); });
          });
          box.appendChild(cl);
          var lb = el('button', 'menu-item', locked() ? T('🔓 Datenbank entsperren') : T('🔒 Datenbank sperren')); lb.type = 'button';
          lb.addEventListener('click', function () { HK.closeMenu(); setLocked(!locked()); });
          if (DB.role === 'owner') box.appendChild(lb);                    // Sperre: nur der Eigentuemer (wie bei Seiten)
          if (DB.localViews) box.appendChild(el('div', 'menu-hint', T('Diese Ansichten gehören nur zu diesem Block – die Datenbank selbst hat eigene.')));
          if (OPTS.embedded) {
            var src = el('button', 'menu-item', T('↗ Quelle öffnen: {name}', { name: label(byId(DB.id) || {}) })); src.type = 'button';
            src.addEventListener('click', function () { HK.closeMenu(); openPage(DB.id); }); box.appendChild(src);
          }
        }
        draw(); m.appendChild(box);
      });
    }
    function saveSchemaSoon() { clearTimeout(saveT); saveT = setTimeout(function () { saveSchema(); }, 400); }
    function setSort(pid, dir) { var v = curView(); if (pid) v.sorts = [{ pid: pid, dir: dir }]; else delete v.sorts; saveSchema().then(renderDb); }

    function matchFilter(r, f) {
      var p = fprop(f.pid); if (!p) return true;
      var v = f.pid === 'title' ? r.title : propValue(p, r);
      if (AUTO[p.type] && v) v = isoDay(v);
      var empty = v == null || v === '' || v === false || (Array.isArray(v) && !v.length);
      if (f.op === 'is_empty') return empty;
      if (f.op === 'not_empty') return !empty;
      if (f.op === 'checked') return !!v;
      if (f.op === 'unchecked') return !v;
      if (f.op === 'today' || f.op === 'this_week' || f.op === 'this_month') return !empty && relDate(f.op, v);
      var fv = f.value; if (fv === '' || fv == null) return true;        // unvollstaendiger Filter zaehlt nicht
      if (p.type === 'relation') fv = +fv;                                // Relation: Eintrags-IDs sind Zahlen
      switch (f.op) {
        case 'contains': return Array.isArray(v) ? v.indexOf(fv) >= 0 : String(v || '').toLowerCase().indexOf(fv.toLowerCase()) >= 0;
        case 'not_contains': return Array.isArray(v) ? (v || []).indexOf(fv) < 0 : String(v || '').toLowerCase().indexOf(fv.toLowerCase()) < 0;
        case 'is': return v === fv;
        case 'is_not': return v !== fv;
        case 'eq': return numLike(p) ? v != null && Number(v) === Number(fv) : v === fv;
        case 'gt': return v != null && Number(v) > Number(fv);
        case 'lt': return v != null && Number(v) < Number(fv);
        case 'before': return !!v && v < fv;
        case 'after': return !!v && v > fv;
      }
      return true;
    }
    function rowText(r) {
      var parts = [r.title || ''];
      DB.schema.props.forEach(function (p) {
        var v = r.props[p.id]; if (v == null) return;
        if (p.type === 'relation') (v || []).forEach(function (id) { var x = byId(id); if (x) parts.push(label(x)); });
        else if (p.type === 'select' || p.type === 'multi_select') (Array.isArray(v) ? v : [v]).forEach(function (id) { var o = (p.options || []).find(function (x) { return x.id === id; }); if (o) parts.push(o.name); });
        else parts.push(String(v));
      });
      return parts.join(' ').toLowerCase();
    }
    function filterRows(rows) {
      var fl = curView().filters || [], q = (DB.q || '').trim().toLowerCase();
      return rows.filter(function (r) { return fl.every(function (f) { return matchFilter(r, f); }) && (!q || rowText(r).indexOf(q) >= 0); });
    }

    function filterMenu(anchor) {
      var v = curView();
      HK.openMenu(anchor, function (m) {
        var box = el('div', 'flt-box'); m.appendChild(box);      // eigener Container: das Menue-Element ist geteilt
        function commit() { saveSchemaSoon(); renderDb(); draw(); }
        function draw() {
          box.textContent = ''; box.appendChild(HK.menuLabel(T('Filter (alle müssen zutreffen)')));
          (v.filters || []).forEach(function (f, i) {
            var p = fprop(f.pid); if (!p) return;
            var line = el('div', 'flt'); line.appendChild(el('span', 'flt-name', p.name));
            var op = el('select', 'flt-op'); op.setAttribute('aria-label', T('Bedingung'));
            (OPS[p.type] || OPS.text).forEach(function (o) { var opt = el('option', null, o[1]); opt.value = o[0]; op.appendChild(opt); });
            op.value = f.op; op.addEventListener('change', function () { f.op = op.value; commit(); });
            line.appendChild(op);
            if (NOVALUE[f.op]) op.classList.add('flt-wide');
            if (!NOVALUE[f.op]) {
              var val;
              if (p.type === 'select' || p.type === 'multi_select' || p.type === 'relation') {
                val = el('select', 'flt-val'); var e0 = el('option', null, T('– wählen –')); e0.value = ''; val.appendChild(e0);
                (p.type === 'relation' ? relRows(p.target).map(function (x) { return { id: x.id, name: label(x) }; }) : (p.options || []))
                  .forEach(function (o) { var opt = el('option', null, o.name); opt.value = o.id; val.appendChild(opt); });
              } else {
                val = el('input', 'flt-val'); val.type = (p.type === 'date' || AUTO[p.type]) ? 'date' : 'text';
                if (numLike(p)) val.inputMode = 'decimal';
              }
              val.value = val.tagName !== 'SELECT' && numLike(p) ? HK.editNum(f.value) : f.value || ''; val.setAttribute('aria-label', T('Wert'));
              if (val.tagName === 'SELECT') val.addEventListener('change', function () { f.value = val.value; commit(); });
              else liveText(val, function (x) { f.value = numLike(p) ? numStore(x) : x; saveSchemaSoon(); renderDb(); });
              line.appendChild(val);
            }
            var x = el('button', 'flt-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Filter entfernen'));
            x.addEventListener('click', function () { v.filters.splice(i, 1); if (!v.filters.length) delete v.filters; commit(); });
            line.appendChild(x); box.appendChild(line);
          });
          var add = el('select', 'flt-add'); add.setAttribute('aria-label', T('Filter hinzufügen'));
          var a0 = el('option', null, T('+ Filter hinzufügen …')); a0.value = ''; add.appendChild(a0);
          [fprop('title')].concat(DB.schema.props).forEach(function (p) { var o = el('option', null, p.name); o.value = p.id; add.appendChild(o); });
          add.addEventListener('change', function () {
            var p = fprop(add.value); if (!p) return;
            v.filters = (v.filters || []).concat([{ pid: p.id, op: (OPS[p.type] || OPS.text)[0][0], value: '' }]); commit();
          });
          box.appendChild(add);
        }
        draw();
      });
    }

    function viewMenu(anchor, v) {
      HK.openMenu(anchor, function (m) {
        var name = el('input', 'menu-input'); name.type = 'text'; name.maxLength = 40; name.value = v.name; name.setAttribute('aria-label', T('Name der Ansicht'));
        liveText(name, function (val) { if (val.trim()) { v.name = val.trim(); saveSchemaSoon(); renderDb(); } }, function () { HK.closeMenu(); });
        m.appendChild(name);
        m.appendChild(HK.menuLabel(T('Darstellung')));
        m.appendChild(HK.menuItem(T('Tabelle'), function () { v.type = 'table'; saveSchema().then(renderDb); }, { icon: '▦', on: v.type === 'table' }));
        m.appendChild(HK.menuItem(T('Board'), function () { v.type = 'board'; saveSchema().then(renderDb); }, { icon: '▥', on: v.type === 'board' }));
        m.appendChild(HK.menuItem(T('Liste'), function () { v.type = 'list'; saveSchema().then(renderDb); }, { icon: '☰', on: v.type === 'list' }));
        m.appendChild(HK.menuItem(T('Galerie'), function () { v.type = 'gallery'; saveSchema().then(renderDb); }, { icon: '▣', on: v.type === 'gallery' }));
        m.appendChild(HK.menuItem(T('Kalender'), function () { v.type = 'calendar'; saveSchema().then(renderDb); }, { icon: '🗓', on: v.type === 'calendar' }));
        var sels = DB.schema.props.filter(function (p) { return p.type === 'select'; });
        if (v.type === 'board' && sels.length) {
          m.appendChild(HK.menuLabel(T('Gruppieren nach')));
          sels.forEach(function (p) { m.appendChild(HK.menuItem(p.name, function () { v.group_by = p.id; saveSchema().then(renderDb); }, { on: v.group_by === p.id })); });
        }
        if (views().length > 1) {
          m.appendChild(el('div', 'menu-sep'));
          m.appendChild(HK.menuItem(T('Ansicht löschen'), function () {
            var nv = views().filter(function (x) { return x !== v; }); if (DB.localViews) DB.localViews = nv; else DB.schema.views = nv; saveSchema().then(renderDb);
          }, { icon: '🗑', danger: true }));
        }
      });
    }
    function addView(anchor) {
      HK.openMenu(anchor, function (m) {
        m.appendChild(HK.menuLabel(T('Neue Ansicht')));
        [['table', '▦', T('Tabelle')], ['board', '▥', T('Board')], ['list', '☰', T('Liste')], ['gallery', '▣', T('Galerie')], ['calendar', '🗓', T('Kalender')]].forEach(function (t) {
          m.appendChild(HK.menuItem(t[2], function () {
            var v = { id: rid(), name: t[2], type: t[0] };
            var s = DB.schema.props.find(function (p) { return p.type === 'select'; }); if (t[0] === 'board' && s) v.group_by = s.id;
          var dd = DB.schema.props.find(function (p) { return p.type === 'date'; }); if (t[0] === 'calendar' && dd) v.date_by = dd.id;
            views().push(v); setViewId(v.id); saveSchema().then(renderDb);
          }, { icon: t[1] }));      // ganzes Zeichen (charAt(0) halbierte das Emoji 🗓)
        });
      });
    }

    function viewBar() {
      var bar = el('div', 'db-bar'), tabs = el('div', 'db-tabs'), cur = curView();
      views().forEach(function (v) {
        var b = el('button', 'db-tab' + (v === cur ? ' on' : ''), (VIEW_ICON[v.type] || '▦') + ' ' + v.name); b.type = 'button';
        b.title = v === cur ? T('Ansicht bearbeiten') : T('Ansicht wechseln');
        b.addEventListener('click', function () { if (v === cur) { if (viewsLocked()) lockedHint(); else viewMenu(b, v); } else { setViewId(v.id); renderDb(); } });
        tabs.appendChild(b);
      });
      var pl = el('button', 'db-tab db-tab-add', '+'); pl.type = 'button'; pl.title = T('Ansicht hinzufügen'); pl.setAttribute('aria-label', T('Ansicht hinzufügen'));
      pl.addEventListener('click', function () { addView(pl); }); if (!viewsLocked() && !ro()) tabs.appendChild(pl);   // Lesen/Kommentieren: wuerde nicht gespeichert (BUG-093)
      if (headOn('tabs')) bar.appendChild(tabs);
      var shown = filterRows(DB.rows).length;
      bar.appendChild(el('span', 'db-count', shown !== DB.rows.length ? T('{n} von {total} Einträgen', { n: shown, total: DB.rows.length }) : DB.rows.length === 1 ? T('1 Eintrag') : T('{n} Einträge', { n: shown })));
      var nf = (cur.filters || []).length;
      var fb = el('button', 'db-chip db-filter' + (nf ? ' on' : ''), nf ? T('Filter') + ' · ' + nf : T('Filter')); fb.type = 'button';
      fb.addEventListener('click', function () { if (viewsLocked()) lockedHint(); else filterMenu(fb); }); bar.appendChild(fb);
      var ss = cur.sorts || [];
      if (ss.length) {
        var sp = fprop(ss[0].pid);
        var sb = el('button', 'db-chip on', T('Sortiert:') + ' ' + (sp ? sp.name : '?') + (ss[0].dir === 'desc' ? ' ↓' : ' ↑') + (ss.length > 1 ? ' +' + (ss.length - 1) : '') + '  ×'); sb.type = 'button';
        sb.title = T('Sortierung aufheben'); sb.addEventListener('click', function () { if (viewsLocked()) lockedHint(); else setSort(null); }); bar.appendChild(sb);
      }
      var q = el('input', 'db-search'); q.type = 'search'; q.placeholder = T('🔍 Suchen'); q.value = DB.q || ''; q.setAttribute('aria-label', T('In der Datenbank suchen'));
      q.addEventListener('input', function () {
        DB.q = q.value; clearTimeout(searchT);
        searchT = setTimeout(function () { renderDb(); var n = BOX.querySelector('.db-search'); if (n) { n.focus(); n.setSelectionRange(n.value.length, n.value.length); } }, 200);
      });
      bar.appendChild(q);
      if (locked()) {
        var lk = el('button', 'db-chip db-lock', T('🔒 Gesperrt')); lk.type = 'button'; lk.title = T('Gesperrt – klicken zum Entsperren');
        if (DB.role === 'owner') lk.addEventListener('click', function () { setLocked(false); }); else lk.title = T('Der Eigentümer hat die Datenbank gesperrt');
        bar.appendChild(lk);
      }
      if (ro()) { var rc = el('span', 'db-chip db-ro-chip', T('👁 Nur lesen')); bar.appendChild(rc); }
      var gb = el('button', 'db-chip db-gear', T('⚙ Ansicht')); gb.type = 'button'; gb.title = T('Ansicht-Einstellungen');
      gb.addEventListener('click', function () { viewSettings(gb); }); bar.appendChild(gb);
      var nb = el('button', 'btn db-new-top', T('+ Neu')); nb.type = 'button'; if (!ro()) bar.appendChild(nb);
      nb.addEventListener('click', function () {
        if (!(DB.templates || []).length) return newRow();
        HK.openMenu(nb, function (m) {                // Notion: "Neu ▾" mit den Vorlagen dieser Datenbank
          var dt = defaultTpl();
          m.appendChild(HK.menuItem(T('Leerer Eintrag'), function () { newRow(null, true); }, { icon: '📄' }));
          m.appendChild(HK.menuLabel(T('Vorlagen')));
          DB.templates.forEach(function (t) {
            m.appendChild(HK.menuItem(t.title || T('Vorlage'), function () { newFromTemplate(t.id); }, { icon: ti(t.icon) || '📐', hint: t.id === dt ? T('Standard') : '' }));
          });
          if (DB.role === 'owner') {
            m.appendChild(el('div', 'menu-sep'));
            m.appendChild(HK.menuItem(T('Vorlagen verwalten …'), templatesUI, { icon: '⚙' }));
          }
        });
      });
      if ((DB.templates || []).length) nb.textContent = T('+ Neu') + ' ▾';
      return bar;
    }

    var dragId = null, boardLim = {}, boardKeep = null;
    // "Mehr laden" unter einer Spalte: haengt die naechsten BOARD_PAGE Karten an; hkAll (Drucken) setzt alle ein
    function boardMore(rows, list, g, ck, lim) {
      var b = el('button', 'btn board-more'); b.type = 'button'; b.style.margin = '.25rem .4rem';   // auch fuer Leser (board-add ist dort aus)
      function txt() { b.textContent = T('Mehr laden') + ' '; b.appendChild(el('span', 'board-n', T('(+{n} weitere)', { n: rows.length - lim }))); }
      function grow(to) {
        if (list.dataset.done !== '1') { setTimeout(function () { grow(to); }, 30); return; }   // erste Haeppchen laufen noch
        var f = document.createDocumentFragment();
        rows.slice(lim, to).forEach(function (r) { f.appendChild(boardCard(r, g)); });
        list.appendChild(f); lim = boardLim[ck] = to;
        if (lim >= rows.length) b.remove(); else txt();
      }
      b.addEventListener('click', function () { grow(Math.min(rows.length, lim + BOARD_PAGE)); });
      b.hkAll = function () { grow(rows.length); };
      txt(); return b;
    }
    function boardCard(r, g) {
      var c = tint(el('div', 'board-card'), r); c.draggable = true; c.tabIndex = 0; c.setAttribute('role', 'button');
      c.appendChild(el('div', 'board-title', (ti(r.icon) ? ti(r.icon) + ' ' : '') + (r.title || T('Unbenannt'))));
      visibleProps().forEach(function (p) {
        if (p.id === g.id) return;
        var v = r.props[p.id]; if (v == null || v === '' || v === false || (Array.isArray(v) && !v.length)) return;
        var line = el('div', 'board-prop');
        if (p.type === 'select' || p.type === 'multi_select') {
          (Array.isArray(v) ? v : [v]).forEach(function (id) { var o = (p.options || []).find(function (x) { return x.id === id; }); if (o) line.appendChild(optChip(o)); });
        } else if (p.type === 'relation') line.appendChild(relChips(v, false));
        else if (p.type === 'checkbox') line.textContent = '☑ ' + p.name;
        else if (p.type === 'date') line.textContent = '▦ ' + fDate2.format(new Date(v + 'T00:00:00'));
        else if (numLike(p)) { line.appendChild(el('span', null, p.name + ': ')); line.appendChild(numNode(p, v)); }
        else line.textContent = String(v);
        c.appendChild(line);
      });
      c.addEventListener('click', function () { openRowUI(r.id); });
      c.addEventListener('keydown', function (e) { if (e.key === 'Enter') openRowUI(r.id); });
      c.addEventListener('dragstart', function (e) { dragId = r.id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(r.id)); c.classList.add('dragging'); });
      c.addEventListener('dragend', function () { dragId = null; c.classList.remove('dragging'); });
      return c;
    }
    function renderBoard() {
      var v = curView(), g = propById(v.group_by), wrap = el('div', 'board');
      if (!g || g.type !== 'select') {
        g = DB.schema.props.find(function (p) { return p.type === 'select'; });
        if (g) { v.group_by = g.id; saveSchemaSoon(); }
      }
      if (!g) {
        wrap.appendChild(el('p', 'nt-empty', T('Für ein Board braucht die Datenbank eine Auswahl-Eigenschaft (z. B. Status).')));
        var mk = el('button', 'btn', T('Status-Spalte anlegen')); mk.type = 'button';
        mk.addEventListener('click', function () {
          var p = { id: rid(), name: T('Status'), type: 'select', options: [{ id: rid(), name: T('Offen'), color: 'gray' }, { id: rid(), name: T('In Arbeit'), color: 'blue' }, { id: rid(), name: T('Erledigt'), color: 'green' }] };
          DB.schema.props.push(p); v.group_by = p.id; saveSchema().then(renderDb);
        });
        wrap.appendChild(mk); return wrap;
      }
      var rows = sortedRows(), known = {};
      (g.options || []).forEach(function (o) { known[o.id] = 1; });
      var cols = [{ o: null, rows: rows.filter(function (r) { return !known[r.props[g.id]]; }) }].concat(
        (g.options || []).map(function (o) { return { o: o, rows: rows.filter(function (r) { return r.props[g.id] === o.id; }) }; }));
      if (!cols[0].rows.length) cols.shift();           // "Ohne Status" nur, wenn es solche Zeilen gibt
      cols.forEach(function (col) {
        var c = el('div', 'board-col'), h = el('div', 'board-head');
        h.appendChild(col.o ? optChip(col.o) : el('span', 'board-none', T('Ohne {name}', { name: g.name }))); h.appendChild(el('span', 'board-n', String(col.rows.length)));
        c.appendChild(h);
        // Bughunt P07: je Spalte zuerst BOARD_PAGE Karten, der Rest per "Mehr laden" (1500 Karten = ~50 000 Knoten).
        // Aufgeklappt bleibt aufgeklappt (auch nach renderDb); eine gerade gezogene oder neue Karte ist immer zu sehen.
        var ck = curView().id + ':' + (col.o ? col.o.id : ''), lim = Math.max(BOARD_PAGE, boardLim[ck] || 0);
        col.rows.forEach(function (r, i) { if ((r.id === boardKeep || FRESH[r.id]) && i >= lim) lim = i + 1; });
        lim = boardLim[ck] = Math.min(lim, col.rows.length);
        var list = el('div', 'board-cards'); chunked(col.rows.slice(0, lim), function (r) { return boardCard(r, g); }, list, null, 60); c.appendChild(list);
        if (lim < col.rows.length) c.appendChild(boardMore(col.rows, list, g, ck, lim));
        var add = el('button', 'board-add', T('+ Neu')); add.type = 'button';
        add.addEventListener('click', function () { var pr = {}; if (col.o) pr[g.id] = col.o.id; newRow(pr); });
        c.appendChild(add);
        c.addEventListener('dragover', function (e) { if (dragId) { e.preventDefault(); c.classList.add('over'); } });
        c.addEventListener('dragleave', function (e) { if (!c.contains(e.relatedTarget)) c.classList.remove('over'); });
        c.addEventListener('drop', function (e) {
          e.preventDefault(); c.classList.remove('over');
          var r = DB.rows.find(function (x) { return x.id === dragId; }); dragId = null; if (!r) return;
          var nv = col.o ? col.o.id : null; if ((r.props[g.id] || null) === nv) return;
          boardKeep = r.id;                            // in der Zielspalte sichtbar, auch hinter "Mehr laden"
          setProp(r, g.id, nv).then(renderDb);
        });
        wrap.appendChild(c);
      });
      scrollHints(wrap);                               // Bughunt D15: Schatten am Rand, wenn es seitlich weitergeht
      return wrap;
    }



    // Kompakte Anzeige eines Werts (Liste, Galerie)
    function valueDisplay(p, r) {
      var v = propValue(p, r);
      if (v == null || v === '' || v === false || (Array.isArray(v) && !v.length)) return null;
      var s = el('span', 'dv dv-' + p.type);
      if (p.type === 'relation') { var rc = relChips(v, true); return rc.childNodes.length ? rc : null; }
      if (p.type === 'select' || p.type === 'multi_select') {
        (Array.isArray(v) ? v : [v]).forEach(function (id) { var o = (p.options || []).find(function (x) { return x.id === id; }); if (o) s.appendChild(optChip(o)); });
        return s.childNodes.length ? s : null;
      }
      if (p.type === 'checkbox') s.textContent = '☑ ' + p.name;
      else if (p.type === 'date') s.textContent = fDate2.format(new Date(v + 'T00:00:00'));
      else if (p.type === 'url') s.textContent = String(v).replace(/^https?:\/\//, '');
      else if (AUTO[p.type]) s.textContent = stampText(v);
      else if (numLike(p)) s.appendChild(numNode(p, v));
      else s.textContent = String(v);
      return s;
    }
    // Zeile oeffnen wie in der Ansicht eingestellt: Seitenansicht (rechts), mittig oder ganze Seite
    function openRowUI(id) {
      var mode = curView().open || 'side';
      if (mode === 'page') return openPage(id);
      return openPeek(id, { mode: mode, dbId: DB.id, schema: DB.schema, ids: sortedRows().map(function (x) { return x.id; }),
        refresh: function () { return loadDb(); }, setMode: function (m) { curView().open = m; saveSchemaSoon(); } });
    }
    function openRow(r) { return function () { openRowUI(r.id); }; }
    function renderList() {
      var wrap = el('div', 'dbl'), items = [], vp = visibleProps();
      eachGrouped(sortedRows(), function (o, n, g) { items.push({ o: o, n: n, g: g }); }, function (r) { items.push({ r: r }); });
      function mk(i) {
        var it = items[i], r = it.r;
        if (!r) {
          var h = el('div', 'dbl-group'); h.appendChild(it.o ? optChip(it.o) : el('span', 'board-none', T('Ohne {name}', { name: it.g.name }))); h.appendChild(el('span', 'board-n', String(it.n)));
          return h;
        }
        var line = tint(el('div', 'dbl-row'), r); line.tabIndex = 0; line.setAttribute('role', 'button');
        var left = el('div', 'dbl-left'); left.appendChild(icoNode(r.icon, '📄', 'dbl-ic')); left.appendChild(el('span', 'dbl-title', r.title || T('Unbenannt')));
        var right = el('div', 'dbl-props');
        vp.forEach(function (p) { var n = valueDisplay(p, r); if (n) right.appendChild(n); });
        line.appendChild(left); line.appendChild(right);
        line.addEventListener('click', openRow(r)); line.addEventListener('keydown', function (e) { if (e.key === 'Enter') openRowUI(r.id); });
        return line;
      }
      var nb = el('button', 'db-new-row', T('+ Neu')); nb.type = 'button'; nb.addEventListener('click', function () { newRow(); });
      if (items.length >= VLIST_MIN) {               // lange Liste: nur der Ausschnitt (wie die Tabelle)
        var tv = curView(), V = vlist(wrap, items.length, mk, function (i) { var it = items[i]; return 'l:' + tv.id + ':' + (it.r ? it.r.id : 'g:' + (it.o ? it.o.id : '')); },
          function (px) { var p = el('div', 'dbl-vpad'); p.style.height = px + 'px'; p.style.flex = 'none'; p.setAttribute('aria-hidden', 'true'); return p; },
          nb, DB.vh || (DB.vh = new Map()), null);
        wrap.appendChild(nb);
        wrap.vlist = V;                              // renderDb setzt den Ausschnitt nach dem Einhaengen ein
      } else items.forEach(function (it, i) { wrap.appendChild(mk(i)); });
      if (!nb.parentNode) wrap.appendChild(nb);
      wrap.dataset.rows = String(items.filter(function (it) { return it.r; }).length);
      return wrap;
    }
    function renderGallery() {
      var g = el('div', 'dbg' + (curView().card ? ' dbg-' + curView().card : ''));
      var nb = el('button', 'dbg-new', T('+ Neu')); nb.type = 'button'; nb.addEventListener('click', function () { newRow(); }); g.appendChild(nb);
      chunked(sortedRows(), function (r) {
        var c = tint(el('div', 'dbg-card'), r); c.tabIndex = 0; c.setAttribute('role', 'button');
        var pv = el('div', 'dbg-prev');
        if (r.preview) { var im = el('img'); im.loading = 'lazy'; im.decoding = 'async'; im.src = thumb(r.preview, 640); im.alt = ''; pv.appendChild(im); }
        else pv.appendChild(icoNode(r.icon, '📄', 'dbg-ph'));
        c.appendChild(pv);
        var body = el('div', 'dbg-body'), tl = el('div', 'dbg-title');
        if (r.icon) tl.appendChild(icoNode(r.icon, '', 'dbg-tic'));
        tl.appendChild(document.createTextNode(r.title || T('Unbenannt'))); body.appendChild(tl);
        visibleProps().forEach(function (p) { var n = valueDisplay(p, r); if (n) body.appendChild(n); });
        c.appendChild(body);
        c.addEventListener('click', openRow(r)); c.addEventListener('keydown', function (e) { if (e.key === 'Enter') openRowUI(r.id); });
        return c;
      }, g, nb, 120);
      return g;
    }
    // Viele Karten (Galerie, Board): die ersten sofort, den Rest in kurzen Haeppchen danach (Seite bleibt bedienbar) -
    // am Ende steht genau derselbe DOM wie frueher. Neu gerendert (renderDb) -> laufende Haeppchen hoeren auf.
    // Karten entstehen in Haeppchen von ~12 ms, eingehaengt werden sie erst, wenn sich die Anzahl im DOM verdoppelt
    // (bzw. am Ende): jedes Einhaengen kostet ein Layout des ganzen Boards - so bleibt es bei wenigen Layouts.
    var renderGen = 0;
    function chunked(list, make, parent, before, first) {
      var gen = renderGen, i = Math.min(list.length, first), shown = i, frag = document.createDocumentFragment(), held = 0;
      for (var k = 0; k < i; k++) parent.insertBefore(make(list[k]), before);
      function step() {
        if (gen !== renderGen) return;
        var t0 = Date.now();
        while (i < list.length && Date.now() - t0 < 12) { frag.appendChild(make(list[i++])); held++; }
        if (i >= list.length || held >= shown) { parent.insertBefore(frag, before); shown += held; held = 0; }
        if (i < list.length) setTimeout(step, 0); else parent.dataset.done = '1';
      }
      if (i < list.length) setTimeout(step, 0); else parent.dataset.done = '1';
    }
    // Kopf einer eingebetteten Datenbank: Titel fuehrt zur vollen Datenbank-Seite
    // Kalender: Monatsraster, Eintraege am Tag ihres Datums, Ziehen aendert das Datum
    var fMonth = new Intl.DateTimeFormat(window.SAGA_LOCALE, { month: 'long', year: 'numeric' }), calDrag = null;
    function ymd(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }   // lokal, nicht UTC
    function renderCalendar() {
      var v = curView(), wrap = el('div', 'cal'), dp = propById(v.date_by);
      if (!dp || !(dp.type === 'date' || AUTO[dp.type])) {
        dp = DB.schema.props.find(function (p) { return p.type === 'date'; }) || DB.schema.props.find(function (p) { return AUTO[p.type]; });
        if (dp) { v.date_by = dp.id; saveSchemaSoon(); }
      }
      if (!dp) {
        wrap.appendChild(el('p', 'nt-empty', T('Für einen Kalender braucht die Datenbank eine Datumsspalte.')));
        var mk = el('button', 'btn', T('Datumsspalte anlegen')); mk.type = 'button';
        mk.addEventListener('click', function () { var p = { id: rid(), name: T('Datum'), type: 'date' }; DB.schema.props.push(p); v.date_by = p.id; saveSchema().then(renderDb); });
        wrap.appendChild(mk); return wrap;
      }
      var editable = dp.type === 'date', now = new Date(), today = ymd(now);
      if (!DB.calMonth) DB.calMonth = new Date(now.getFullYear(), now.getMonth(), 1);
      var m0 = DB.calMonth;
      var head = el('div', 'cal-head');
      [['‹', -1, T('Voriger Monat')], ['›', 1, T('Nächster Monat')]].forEach(function (x, i) {
        var b = el('button', 'icon-btn cal-nav', x[0]); b.type = 'button'; b.setAttribute('aria-label', x[2]);
        b.addEventListener('click', function () { DB.calMonth = new Date(m0.getFullYear(), m0.getMonth() + x[1], 1); renderDb(); });
        if (i === 0) head.appendChild(b); else { head.appendChild(el('span', 'cal-title', fMonth.format(m0))); head.appendChild(b); }
      });
      var tb = el('button', 'db-chip', T('Heute')); tb.type = 'button';
      tb.addEventListener('click', function () { DB.calMonth = new Date(now.getFullYear(), now.getMonth(), 1); renderDb(); }); head.appendChild(tb);
      if (window.HKCal) {                          // externe Termine (mailcow/Nextcloud) einblenden - pro Ansicht
        var xb = el('button', 'db-chip' + (v.ext ? ' on' : ''), '📅 ' + T('Termine')); xb.type = 'button';
        xb.title = v.ext ? T('Externe Termine ausblenden') : T('Termine aus verbundenen Kalendern einblenden');
        xb.addEventListener('click', function () { if (viewsLocked()) { lockedHint(); return; } if (v.ext) delete v.ext; else v.ext = true; saveSchema().then(renderDb); });
        head.appendChild(xb);
      }
      head.appendChild(el('span', 'cal-src', T('nach „{name}“', { name: dp.name })));
      wrap.appendChild(head);
      var byDay = {}, noDate = 0;
      sortedRows().forEach(function (r) {
        var val = propValue(dp, r); if (!val) { noDate++; return; }
        var d = AUTO[dp.type] ? isoDay(val) : String(val).slice(0, 10);
        (byDay[d] = byDay[d] || []).push(r);
      });
      var grid = el('div', 'cal-grid'), calCells = {};
      window.HK.weekdaysShort().forEach(function (w) { grid.appendChild(el('div', 'cal-wd', w)); });
      var start = new Date(m0.getFullYear(), m0.getMonth(), 1 - ((m0.getDay() + 6) % 7));
      var weeks = Math.ceil((((m0.getDay() + 6) % 7) + new Date(m0.getFullYear(), m0.getMonth() + 1, 0).getDate()) / 7);
      for (var i = 0; i < weeks * 7; i++) {
        (function (day) {
          var ds = ymd(day), cell = el('div', 'cal-day' + (day.getMonth() !== m0.getMonth() ? ' other' : '') + (ds === today ? ' today' : ''));
          calCells[ds] = cell;
          var top = el('div', 'cal-top'); top.appendChild(el('span', 'cal-num', String(day.getDate())));
          if (editable) {
            var add = el('button', 'cal-add', '+'); add.type = 'button'; add.title = T('Eintrag am {date}', { date: day.toLocaleDateString(window.SAGA_LOCALE) });
            add.setAttribute('aria-label', add.title);
            add.addEventListener('click', function () { var pr = {}; pr[dp.id] = ds; newRow(pr); });
            top.appendChild(add);
          }
          cell.appendChild(top);
          (byDay[ds] || []).forEach(function (r) {
            var b = tint(el('button', 'cal-item'), r); b.type = 'button'; b.title = r.title || T('Unbenannt');
            if (r.icon) b.appendChild(icoNode(r.icon, '', 'cal-ic'));
            b.appendChild(el('span', 'cal-it', r.title || T('Unbenannt')));
            b.addEventListener('click', function () { openRowUI(r.id); });
            if (editable) {
              b.draggable = true;
              b.addEventListener('dragstart', function (e) { calDrag = r.id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(r.id)); b.classList.add('dragging'); });
              b.addEventListener('dragend', function () { calDrag = null; b.classList.remove('dragging'); });
            }
            cell.appendChild(b);
          });
          if (editable) {
            cell.addEventListener('dragover', function (e) { if (calDrag) { e.preventDefault(); cell.classList.add('over'); } });
            cell.addEventListener('dragleave', function (e) { if (!cell.contains(e.relatedTarget)) cell.classList.remove('over'); });
            cell.addEventListener('drop', function (e) {
              e.preventDefault(); cell.classList.remove('over');
              var r = DB.rows.find(function (x) { return x.id === calDrag; }); calDrag = null;
              if (r && r.props[dp.id] !== ds) setProp(r, dp.id, ds).then(renderDb);
            });
          }
          grid.appendChild(cell);
        })(new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
      }
      wrap.appendChild(grid);
      if (v.ext && window.HKCal) {
        HKCal.load(start, new Date(start.getFullYear(), start.getMonth(), start.getDate() + weeks * 7)).then(function (d) {
          var map = HKCal.byDay(d.events);
          Object.keys(map).forEach(function (k) {
            if (calCells[k]) map[k].forEach(function (e) { calCells[k].appendChild(HKCal.chip(e, function (ev) { HKCal.eventDialog(ev, {}, renderDb); })); });
          });
        }).catch(function (err) { HK.toast(T('Termine nicht geladen: {err}', { err: err.message })); });
      }
      if (noDate) wrap.appendChild(el('div', 'cal-nodate', (noDate === 1 ? T('1 Eintrag') : T('{n} Einträge', { n: noDate })) + ' ' + T('ohne „{name}“ (in Tabelle/Liste sichtbar)', { name: dp.name })));
      return wrap;
    }

    function headOn(k) { var h = OPTS.getHead && OPTS.getHead(); return !h || h[k] !== false; }
    function embedHead() {
      var meta = byId(DB.id) || {}, h = el('div', 'embed-head');
      var b = el('button', 'embed-title'); b.type = 'button'; b.title = T('Als ganze Seite öffnen');
      b.appendChild(icoNode(meta.icon, '🗃️', 'embed-ic')); b.appendChild(document.createTextNode(' ' + label(meta)));
      b.addEventListener('click', function () { openPage(DB.id); });
      h.appendChild(b); return h;
    }
    var self = { boxEl: BOX, DB: DB, valueEditor: valueEditor, render: renderDb, reload: function () { return loadDb(); },
             load: function (id, schema) { DB.id = id; DB.rows = []; DB.schema = schema || { props: [] }; return loadDb(); } };
    if (BOX) LIVE_DBS.push(self);
    return self;
  }
  // Bughunt P02/P11: Schatten-Hinweise der waagrechten Scrollbereiche gesammelt je Frame - erst alle messen, dann alle
  // Klassen setzen (vorher je Bereich lesen/schreiben im Wechsel = erzwungenes Layout, ~1 s bei 20 eingebetteten DBs)
  // und ein gemeinsamer ResizeObserver, der entfernte Bereiche wieder loslaesst (vorher je Bereich einer, nie getrennt).
  var SH_Q = [], SH_RAF = 0;
  var SH_RO = window.ResizeObserver ? new ResizeObserver(function (es) { es.forEach(function (e) { scrollHintQ(e.target); }); }) : null;
  function scrollHintQ(sc) {
    if (SH_Q.indexOf(sc) < 0) SH_Q.push(sc);
    if (!SH_RAF) SH_RAF = requestAnimationFrame(function () {
      var q = SH_Q; SH_Q = []; SH_RAF = 0;
      var m = q.map(function (x) {
        if (!x.isConnected) { if (SH_RO && x._shOn) SH_RO.unobserve(x); return null; }   // erst nach dem Einhaengen loslassen
        x._shOn = true;
        return [x.scrollLeft > 1, x.scrollLeft + x.clientWidth < x.scrollWidth - 1];
      });
      q.forEach(function (x, i) { if (m[i]) { x.classList.toggle('sc-l', m[i][0]); x.classList.toggle('sc-r', m[i][1]); } });
    });
  }
  var LIVE_DBS = [];
  function refreshDbViews(dbId) {          // alle sichtbaren Ansichten dieser Datenbank (Seite + eingebettete) neu laden
    LIVE_DBS = LIVE_DBS.filter(function (v) { return v.DB && v.render && document.body.contains(v.boxEl || document.body); });
    LIVE_DBS.forEach(function (v) { if (v.DB.id === dbId) v.reload(); });
  }
  // Live (BUG-049, BUG-001): Zeile oder Schema der Datenbank anderswo geaendert -> Ansichten neu laden. Wird gerade
  // eine Zelle bearbeitet, erst danach (die eigene Eingabe geht nicht verloren)
  var dbLiveT = {};
  function dbLiveReload(dbId) {
    clearTimeout(dbLiveT[dbId]);
    dbLiveT[dbId] = setTimeout(function () {
      var a = document.activeElement;
      var editing = a && a !== document.body && LIVE_DBS.some(function (v) {
        return v.DB.id === dbId && v.boxEl && v.boxEl.contains(a) && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable);
      });
      if (editing || document.querySelector('.menu:not([hidden])')) { dbLiveReload(dbId); return; }
      refreshDbViews(dbId);
    }, 400);
  }
  document.addEventListener('hk-note-changed', function (e) {
    var d = e.detail || {};
    if (d.id && !d.gone && LIVE_DBS.some(function (v) { return v.DB.id === d.id; })) dbLiveReload(d.id);
  });

  function renderProps(parent) {
    var box = $('pg-props'); box.textContent = ''; box.hidden = false;
    var inst = makeDbView(null); inst.DB.id = parent.id; inst.DB.schema = parent.schema || { props: [] };   // neue Optionen -> richtige Datenbank
    var row = { id: S.cur.id, props: S.cur.props || {}, created: S.cur.created, updated: S.cur.updated };
    ((parent.schema || {}).props || []).forEach(function (p) {
      var line = el('div', 'prop-line');
      line.appendChild(el('span', 'prop-name', TYPES[p.type][1] + ' ' + p.name));
      line.appendChild(inst.valueEditor(p, row, function () { S.cur.props = row.props; renderProps(parent); }));
      box.appendChild(line);
    });
    var back = el('button', 'linkbtn prop-back', '← ' + (ti(parent.icon) ? ti(parent.icon) + ' ' : '') + label(parent)); back.type = 'button';
    back.addEventListener('click', function () { openPage(parent.id); });
    box.appendChild(back);
  }

  // Ansicht nach dem Oeffnen einer Seite umschalten: Datenbank / Zeile / normale Seite
  var pageDb = null;
  function applyKind() {
    $('pg-db').hidden = true; $('pg-props').hidden = true; $('pg-editor').hidden = false;
    if (!S.cur) return;
    if (S.cur.kind === 'database') {
      $('pg-editor').hidden = true; $('pg-backlinks').hidden = true; $('pg-db').hidden = false;
      pageDb = pageDb || makeDbView($('pg-db')); return pageDb.load(S.cur.id, S.cur.schema);
    }
    var par = S.cur.parent_id && byId(S.cur.parent_id);
    if (par && par.kind === 'database') {
      return api('GET', '/api/notes/' + par.id).then(function (d) { if (S.cur && S.cur.parent_id === d.id) renderProps(d); });
    }
  }
  function newDatabase(parentId) {
    return save().then(function () { return api('POST', '/api/notes', { kind: 'database', title: T('Neue Datenbank'), icon: '🗃️', parent_id: parentId || null }); })
      .then(function (d) { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList().then(function () { return openPage(d.id); }); })
      .then(function () { $('pg-title').focus(); $('pg-title').select(); })
      .catch(function (err) { HK.toast(T('Datenbank nicht angelegt: {err}', { err: err.message })); });
  }


  // --- Bild-Icons und Titelbilder ------------------------------------------------
  function isImg(icon) { return typeof icon === 'string' && (icon.indexOf('/files/') === 0 || icon.indexOf('/api/fed/files/') === 0); }
  // eigene Bilder verkleinert laden (Server erzeugt WebP-Vorschau einmal und merkt sie sich)
  function thumb(u, w) { return typeof u === 'string' && /^(\/files|\/api\/fed\/files\/-[0-9]+)\/[0-9a-f]{24}$/.test(u) ? u + '?w=' + w : u; }
  function ti(icon) { return isImg(icon) ? '' : (icon || ''); }          // Icon fuer reine Textstellen
  function icoNode(icon, fallback, cls) {                                 // Icon als Element (Emoji oder Bild)
    if (isImg(icon)) { var i = el('img', (cls || '') + ' ic-img'); i.decoding = 'async'; i.src = thumb(icon, 160); i.alt = ''; return i; }
    return el('span', cls || '', icon || fallback || '');
  }
  function showIcon(icon) {
    var b = $('pg-icon'); b.textContent = '';
    if (isImg(icon)) b.appendChild(icoNode(icon, '', 'page-icon-img')); else b.textContent = icon || '';
    b.classList.toggle('empty', !icon);
    b.hidden = !icon && (readOnly() || !remCap(S.cur, 'db'));   // "+ Icon" nur, wenn man es setzen darf (BUG-093; fremde Server: 2.3)
  }
  function uploadImage(file) {
    var fd = new FormData(); fd.append('image', file);
    return fetch('/api/files', { method: 'POST', credentials: 'same-origin', headers: Object.assign({}, UPH), body: fd })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok || !d.success) throw new Error(d.error || ('HTTP ' + r.status)); return d.file.url; }); });
  }
  var filePurpose = null;
  function pickImage(purpose) { filePurpose = purpose; $('pg-file').value = ''; $('pg-file').click(); }
  $('pg-file').addEventListener('change', function () {
    var f = this.files && this.files[0], purpose = filePurpose; if (!f || !S.cur) return;
    var id = S.cur.id;
    HK.toast(T('Lade Bild hoch …'));
    uploadImage(f).then(function (url) {
      if (purpose === 'icon') return api('PATCH', '/api/notes/' + id, { icon: url }).then(function () { setIconLocal(id, url); });
      return api('PATCH', '/api/notes/' + id, { cover: url, cover_pos: 50 }).then(function () {
        if (S.cur && S.cur.id === id) { S.cur.cover = url; S.cur.cover_pos = 50; showCover(); }
      });
    }).then(function () { HK.toast(T('Bild gespeichert')); }).catch(function (err) { HK.toast(T('Bild nicht gespeichert: {err}', { err: err.message })); });
  });
  function setIconLocal(id, icon) {
    if (S.cur && S.cur.id === id) { S.cur.icon = icon; showIcon(icon); }
    var p = byId(id); if (p) p.icon = icon; renderTree(); renderCrumbs(); setLocation();
  }

  function showCover() {
    var c = $('pg-cover'), has = !!(S.cur && S.cur.cover);
    c.hidden = !has; $('pg-addcover').hidden = has;
    if (has) { $('pg-cover-img').src = thumb(S.cur.cover, 2000); $('pg-cover-img').style.objectPosition = 'center ' + (S.cur.cover_pos == null ? 50 : S.cur.cover_pos) + '%'; }
    c.classList.remove('moving'); $('pg-cover-move').textContent = T('Verschieben');
  }
  // Titelbild: hochladen oder aus Immich
  function coverMenu(btn) {
    if (!HK.immichPick || foreign()) return pickImage('cover');          // Immich ist das eigene Konto - nicht in fremden Seiten
    HK.openMenu(btn, function (m) {
      m.appendChild(HK.menuItem(T('🖼 Bild hochladen …'), function () { pickImage('cover'); }));
      m.appendChild(HK.menuItem(T('📷 Aus Immich …'), function () {
        var id = S.cur.id;
        HK.immichPick(function (url) {
          api('PATCH', '/api/notes/' + id, { cover: url, cover_pos: 50 }).then(function () {
            if (S.cur && S.cur.id === id) { S.cur.cover = url; S.cur.cover_pos = 50; showCover(); }
            HK.toast(T('Titelbild gespeichert'));
          }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
        });
      }));
    });
  }
  $('pg-addcover').addEventListener('click', function () { coverMenu(this); });
  $('pg-cover-change').addEventListener('click', function () { coverMenu(this); });
  $('pg-cover-del').addEventListener('click', function () {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { cover: null }).then(function () { if (S.cur && S.cur.id === id) { S.cur.cover = ''; showCover(); } })
      .catch(function (err) { HK.toast(T('Nicht entfernt: {err}', { err: err.message })); });
  });
  // Verschieben: Bild senkrecht ziehen (wie "Reposition" in Notion), "Fertig" speichert
  var mv = null;
  $('pg-cover-move').addEventListener('click', function () {
    var c = $('pg-cover');
    if (c.classList.contains('moving')) {
      c.classList.remove('moving'); this.textContent = T('Verschieben');
      var id = S.cur.id, pos = S.cur.cover_pos;
      api('PATCH', '/api/notes/' + id, { cover_pos: pos }).catch(function (err) { HK.toast(T('Position nicht gespeichert: {err}', { err: err.message })); });
    } else { c.classList.add('moving'); this.textContent = T('Fertig'); }
  });
  $('pg-cover-img').addEventListener('pointerdown', function (e) {
    if (!$('pg-cover').classList.contains('moving')) return;
    e.preventDefault(); this.setPointerCapture(e.pointerId);
    mv = { y: e.clientY, pos: S.cur.cover_pos == null ? 50 : S.cur.cover_pos, h: this.getBoundingClientRect().height };
  });
  $('pg-cover-img').addEventListener('pointermove', function (e) {
    if (!mv) return;
    var pos = Math.max(0, Math.min(100, mv.pos - (e.clientY - mv.y) / mv.h * 100));
    S.cur.cover_pos = Math.round(pos * 10) / 10; this.style.objectPosition = 'center ' + S.cur.cover_pos + '%';
  });
  $('pg-cover-img').addEventListener('pointerup', function () { mv = null; });
  $('pg-cover-img').addEventListener('pointercancel', function () { mv = null; });

  // --- Schaltflaechen-Leiste (Notion "Buttons") -----------------------------------
  function decode(s) { var d = document.createElement('textarea'); d.innerHTML = s || ''; return d.value; }   // Server-Bereinigung macht & zu &amp;
  var BTN_ACTIONS = { db: T('Neuer Eintrag in Datenbank'), task: T('Neue Aufgabe'), page: T('Seite öffnen') };
  function ButtonsTool(o) {
    var d = o.data || {};
    this.buttons = (d.buttons || []).map(function (b) {
      return { label: decode(b.label), icon: decode(b.icon), action: b.action || '', target: b.target || null, tpl: b.tpl || null, title: decode(b.title), open: b.open || '' };
    });
    if (!this.buttons.length) this.buttons = [{ label: T('Neu'), icon: '➕', action: '', target: null, tpl: null, title: '' }];
  }
  ButtonsTool.toolbox = { title: T('Schaltflächen'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="8" height="10" rx="2"/><rect x="13" y="7" width="8" height="10" rx="2"/></svg>' };
  ButtonsTool.prototype.render = function () { this.w = el('div', 'btnrow'); this.draw(); return this.w; };
  ButtonsTool.prototype.changed = function () { this.draw(); if (!S.loading) { S.dirtyBody = true; schedule(); } };
  ButtonsTool.prototype.draw = function () {
    var self = this, w = this.w, ro_ = roRole(); w.textContent = '';     // W06: Lesen/Kommentieren - nur benutzen, nicht einrichten
    this.buttons.forEach(function (b, i) {
      var g = el('div', 'nbtn-wrap');
      var btn = el('button', 'nbtn'); btn.type = 'button';
      if (b.icon) btn.appendChild(el('span', 'nbtn-ic', b.icon));
      btn.appendChild(el('span', null, b.label || T('Schaltfläche')));
      btn.addEventListener('click', function () { runButton(b, function () { if (!roRole()) btnConfig(btn, self, i); }); });
      if (ro_) { g.appendChild(btn); w.appendChild(g); return; }
      var cfg = el('button', 'nbtn-cfg', '⚙'); cfg.type = 'button'; cfg.title = T('Schaltfläche einrichten'); cfg.setAttribute('aria-label', T('Schaltfläche einrichten'));
      cfg.addEventListener('click', function () { btnConfig(cfg, self, i); });
      g.appendChild(btn); g.appendChild(cfg); w.appendChild(g);
    });
    if (ro_) return;
    var add = el('button', 'nbtn-add', '+'); add.type = 'button'; add.title = T('Schaltfläche hinzufügen'); add.setAttribute('aria-label', T('Schaltfläche hinzufügen'));
    add.addEventListener('click', function () { self.buttons.push({ label: T('Neu'), icon: '➕', action: '', target: null, tpl: null, title: '' }); self.changed(); });
    w.appendChild(add);
  };
  ButtonsTool.prototype.save = function () { return { buttons: this.buttons }; };

  function btnConfig(anchor, tool, i) {
    var b = tool.buttons[i];
    HK.openMenu(anchor, function (m) {
      var box = el('div', 'flt-box');
      function field(lbl, node) { var l = el('label', 'cfg-line'); l.appendChild(el('span', 'cfg-lbl', lbl)); l.appendChild(node); box.appendChild(l); return node; }
      function inp(v, ph, max) { var x = el('input', 'flt-val'); x.type = 'text'; x.value = v || ''; x.placeholder = ph || ''; x.maxLength = max || 60; return x; }
      function sel(opts, v) {
        var s = el('select', 'flt-val'); opts.forEach(function (o) { var e = el('option', null, o[1]); e.value = o[0]; s.appendChild(e); }); s.value = v == null ? '' : String(v); return s;
      }
      function draw() {
        box.textContent = '';
        var lab = field(T('Beschriftung'), inp(b.label, T('z. B. Neue Notiz'), 40));
        liveText(lab, function (x) { b.label = x.trim(); tool.changed(); });
        var ic = field(T('Symbol'), inp(b.icon, T('Emoji'), 8));
        liveText(ic, function (x) { b.icon = x.trim(); tool.changed(); });
        var act = field(T('Aktion'), sel([['', T('– wählen –')]].concat(Object.keys(BTN_ACTIONS).map(function (k) { return [k, BTN_ACTIONS[k]]; })), b.action));
        act.addEventListener('change', function () { b.action = act.value; b.target = null; b.tpl = null; tool.changed(); draw(); });
        if (b.action === 'db') {
          var dbs = S.pages.filter(function (p) { return p.kind === 'database'; });
          var t = field(T('Datenbank'), sel([['', dbs.length ? T('– wählen –') : T('keine Datenbank vorhanden')]].concat(dbs.map(function (p) { return [String(p.id), (ti(p.icon) || '🗃️') + ' ' + label(p)]; })), b.target));
          t.addEventListener('change', function () { b.target = +t.value || null; tool.changed(); });
          var tpls = S.pages.filter(function (p) { return p.template; });
          var tp = field(T('Vorlage'), sel([['', T('keine (leere Seite)')]].concat(tpls.map(function (p) { return [String(p.id), (ti(p.icon) || '📄') + ' ' + label(p)]; })), b.tpl));
          tp.addEventListener('change', function () { b.tpl = +tp.value || null; tool.changed(); });
          var op = field(T('Öffnen als'), sel([['', T('Ganze Seite')], ['side', T('Seitenansicht rechts')], ['left', T('Seitenansicht links')], ['center', T('Mittig')]], b.open || ''));
          op.addEventListener('change', function () { b.open = op.value; tool.changed(); });
          var tt = field(T('Titel'), inp(b.title, T('z. B. Tagebuch {datum}'), 80));
          liveText(tt, function (x) { b.title = x.trim(); tool.changed(); });
          box.appendChild(el('div', 'menu-hint', T('{datum} wird beim Klick durch das heutige Datum ersetzt.')));
        } else if (b.action === 'page') {
          var pg = field(T('Seite'), sel([['', T('– wählen –')]].concat(S.pages.map(function (p) { return [String(p.id), (ti(p.icon) || '📄') + ' ' + label(p)]; })), b.target));
          pg.addEventListener('change', function () { b.target = +pg.value || null; tool.changed(); });
          var op2 = field(T('Öffnen als'), sel([['', T('Ganze Seite')], ['side', T('Seitenansicht rechts')], ['left', T('Seitenansicht links')], ['center', T('Mittig')]], b.open || ''));
          op2.addEventListener('change', function () { b.open = op2.value; tool.changed(); });
        } else if (b.action === 'task') {
          box.appendChild(el('div', 'menu-hint', T('Legt eine Saga-Aufgabe an (erscheint im Dashboard und in der Aufgabenleiste).')));
        }
        var del = el('button', 'menu-item danger', T('Schaltfläche entfernen')); del.type = 'button';
        del.addEventListener('click', function () { HK.closeMenu(); tool.buttons.splice(i, 1); if (!tool.buttons.length) tool.buttons.push({ label: T('Neu'), icon: '➕', action: '' }); tool.changed(); });
        box.appendChild(del);
      }
      draw(); m.appendChild(box);
    });
  }

  function runButton(b, configure) {
    if (b.action === 'db' && b.target) {
      var title = (b.title || '').replace(/\{(datum|date|fecha)\}/g, today.format(new Date()));
      var dup = { parent_id: b.target }; if (title) dup.title = title;
      if (S.cur && S.cur.id) dup.via = S.cur.id;                  // geteilte Seite (auch einer anderen Instanz, negative ID): Server prueft die Schaltflaeche dort (2.5)
      var req = b.tpl ? api('POST', '/api/notes/' + b.tpl + '/duplicate', dup)
                      : api('POST', '/api/notes', { parent_id: b.target, title: title });
      return save().then(function () { return req; }).then(function (d) {
        return loadList().then(function () {
          if (b.open) return openPeek(d.id, { mode: b.open, dbId: b.target, ids: [d.id], refresh: function () { refreshDbViews(b.target); } });
          return openPage(d.id).then(function () { if (!title) $('pg-title').focus(); });
        });
      })
        .catch(function (err) { HK.toast(T('Eintrag nicht angelegt: {err}', { err: err.message })); });
    }
    if (b.action === 'page' && b.target) {
      if (!byId(b.target)) { HK.toast(T('Die Zielseite gibt es nicht mehr.')); return; }
      if (b.open) return openPeek(b.target, { mode: b.open, dbId: (byId(b.target) || {}).parent_id, ids: [b.target], refresh: function () {} });
      return openPage(b.target);
    }
    if (b.action === 'task') {
      openModal(T('Neue Aufgabe'), function (body) {
        var f = el('div', 'task-quick'), inp = el('input', 'flt-val'); inp.type = 'text'; inp.maxLength = 300; inp.placeholder = T('Was ist zu tun?');
        var ok = el('button', 'btn', T('Anlegen')); ok.type = 'button';
        function go() {
          var text = inp.value.trim(); if (!text) return;
          api('POST', '/api/tasks', { text: text }).then(function () { closeModal(); HK.toast(T('Aufgabe angelegt ✓')); })
            .catch(function (err) { HK.toast(T('Aufgabe nicht angelegt: {err}', { err: err.message })); });
        }
        ok.addEventListener('click', go); inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
        f.appendChild(inp); f.appendChild(ok); body.appendChild(f); setTimeout(function () { inp.focus(); }, 0);
      });
      return;
    }
    configure();          // noch nicht eingerichtet -> Einstellungen oeffnen
  }


  // --- Bloecke "Datenbank" (eingebettet) und "Aufgaben" ---------------------------
  // Tastendruecke/Ziehen/Einfuegen in diesen Bloecken gehoeren ihnen - nicht an Editor.js weiterreichen
  // (sonst: Enter in Tabellenzelle = neuer Absatz, Backspace loescht Block, Karte ziehen = Block ziehen)
  function isolate(w) {
    ['keydown', 'keyup', 'keypress', 'input', 'paste', 'cut', 'copy', 'dragstart', 'dragover', 'dragenter', 'dragleave', 'drop', 'dragend']
      .forEach(function (ev) { w.addEventListener(ev, function (e) { e.stopPropagation(); }); });
  }
  // hide_title / hide_tabs (2.5): Kopf des Blocks ausblendbar (wie Notion "Datenbanktitel / Ansichts-Reiter anzeigen")
  function DatabaseTool(o) {
    var d = o.data || {};
    this.data = { db: d.db || null, view: d.view || null, views: Array.isArray(d.views) ? d.views : null, hide_title: !!d.hide_title, hide_tabs: !!d.hide_tabs };
  }
  DatabaseTool.toolbox = { title: T('Datenbank'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>' };
  DatabaseTool.prototype.render = function () { this.w = el('div', 'embed-db'); isolate(this.w); this.draw(); return this.w; };
  DatabaseTool.prototype.mark = function () { if (!S.loading) { S.dirtyBody = true; schedule(); } };
  DatabaseTool.prototype.draw = function () {
    var self = this, w = this.w; w.textContent = '';
    var meta = this.data.db && byId(this.data.db);
    if ((!meta || meta.kind !== 'database') && foreign() && (this.data.db || !canEdit())) {
      w.appendChild(el('div', 'embed-pick owner-only', '🗃️ ' + (this.data.db ? T('Seite ohne Zugriff') : T('Keine Datenbank gewählt'))));
      return;
    }
    if (!meta || meta.kind !== 'database') {
      var pick = el('div', 'embed-pick');
      pick.appendChild(el('span', 'embed-pick-t', this.data.db ? T('Die eingebettete Datenbank gibt es nicht mehr.') : T('🗃️ Datenbank einbetten:')));
      var own = foreign() && S.cur.owner ? S.cur.owner.username : null;      // fremde Seite: nur geteilte Datenbanken desselben Eigentuemers
      var dbs = (own ? S.shared.filter(function (p) { return p.owner && p.owner.username === own; }) : S.pages).filter(function (p) { return p.kind === 'database'; });
      var sel = el('select', 'flt-val'); sel.setAttribute('aria-label', T('Datenbank wählen'));
      var o0 = el('option', null, dbs.length ? T('– bestehende wählen –') : T('noch keine Datenbank')); o0.value = ''; sel.appendChild(o0);
      dbs.forEach(function (p) { var o = el('option', null, (ti(p.icon) || '🗃️') + ' ' + label(p)); o.value = p.id; sel.appendChild(o); });
      sel.addEventListener('change', function () { if (!sel.value) return; self.data.db = +sel.value; self.data.view = null; self.mark(); self.draw(); });
      var nb = el('button', 'btn', T('+ Neue Datenbank hier')); nb.type = 'button';
      nb.addEventListener('click', function () {
        api('POST', '/api/notes', { kind: 'database', title: T('Neue Datenbank'), icon: '🗃️', parent_id: S.cur ? S.cur.id : null })
          .then(function (d) { return loadList().then(function () { self.data.db = d.id; self.mark(); self.draw(); }); })
          .catch(function (err) { HK.toast(T('Datenbank nicht angelegt: {err}', { err: err.message })); });
      });
      pick.appendChild(sel); pick.appendChild(nb); w.appendChild(pick); return;
    }
    var box = el('div', 'embed-box'); w.appendChild(box);
    this.inst = makeDbView(box, { embedded: true,
      getView: function () { return self.data.view; },
      setView: function (v) { self.data.view = v; self.mark(); },
      getViews: function () { return self.data.views; },
      saveViews: function (vs) { self.data.views = JSON.parse(JSON.stringify(vs)); self.mark(); },
      getHead: function () { return { title: !self.data.hide_title, tabs: !self.data.hide_tabs }; },
      setHead: canEdit() ? function (k, on) { self.data[k === 'title' ? 'hide_title' : 'hide_tabs'] = !on; self.mark(); } : null });
    this.inst.load(this.data.db);
  };
  DatabaseTool.prototype.save = function () {
    var o = { db: this.data.db, view: this.data.view, views: this.data.views };
    if (this.data.hide_title) o.hide_title = true;
    if (this.data.hide_tabs) o.hide_tabs = true;
    return o;
  };

  // --- Block "Widget" (2.5, Etappe 1): Uhr, Countdown, Fortschritt, Kennzahl, Zitat ---------------------------------
  // Vertrag: docs/APP-API.md "Widgets in Seiten". Uhr und Countdown rechnet der Browser; Fortschritt aus einer Datenbank,
  // Kennzahl und Zitat rechnet der Server mit den Rechten des Betrachters (GET /api/notes/{id}/widgets, alle 60 s,
  // solange die Seite sichtbar ist). Einrichten: Klick auf das Widget (Bearbeiten/Eigentuemer) -> Panel mit Vorschau.
  var WG_KINDS = ['clock', 'countdown', 'progress', 'metric', 'quote'];
  var WG_NAMES = { clock: T('Uhr'), countdown: T('Countdown'), progress: T('Fortschritt'), metric: T('Kennzahl'), quote: T('Zitat') };
  var WG_IC = { clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', countdown: '<path d="M7 3h10M7 21h10M8 3c0 5 8 5 8 9s-8 4-8 9M16 3c0 5-8 5-8 9"/>',
    progress: '<rect x="3" y="9" width="18" height="6" rx="3"/><path d="M6 12h7"/>', metric: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    quote: '<path d="M7 7h4v4c0 3-2 5-4 5M14 7h4v4c0 3-2 5-4 5"/>' };
  var WG_ERR = { no_access: T('Kein Zugriff auf die Datenquelle'), gone: T('Die Datenquelle gibt es nicht mehr'),
    bad_cfg: T('Widget noch nicht fertig eingerichtet'), too_big: T('Zu viele Einträge für ein Widget') };
  var WG = new Set();                                    // sichtbare Widget-Bloecke (Werte nachladen)
  function wgIcon(k) { return '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + (WG_IC[k] || WG_IC.metric) + '</svg>'; }
  function wgServer(d) { var c = (d && d.cfg) || {}; return !!d && (d.kind === 'metric' || d.kind === 'quote' || (d.kind === 'progress' && c.src === 'db')); }
  function wgDefaults(kind) {
    return { clock: { tz: '', h24: true, sec: false, analog: false }, countdown: { date: '', time: '', dir: 'until', unit: 'days' },
      progress: { src: 'fixed', value: 0, max: 100 }, metric: { db: null, view: '', agg: 'count', prop: '', fmt: 'num' },
      quote: { src: 'fixed', items: [], pick: 'daily' } }[kind] || {};
  }
  function wgFmt(v, fmt) {
    if (v == null || typeof v !== 'number' || !isFinite(v)) return '–';
    return fmt === 'pct' ? fNum.format(v) + ' %' : fmt === 'eur' ? fEur.format(v) : fNum.format(v);
  }
  // Countdown: Tage nach Kalendertagen (wie der Server beim Veroeffentlichen); "auto" unter einem Tag Std./Min.
  function wgCountdown(cfg, now) {
    if (!cfg.date || !/^\d{4}-\d{2}-\d{2}$/.test(cfg.date)) return null;
    var q = cfg.date.split('-'), hm = /^\d{2}:\d{2}$/.test(cfg.time || '') ? cfg.time.split(':') : null;
    var tgt = new Date(+q[0], +q[1] - 1, +q[2], hm ? +hm[0] : 0, hm ? +hm[1] : 0);
    var since = cfg.dir === 'since', ms = since ? now - tgt : tgt - now;
    var day0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()), dayT = new Date(+q[0], +q[1] - 1, +q[2]);
    var days = Math.round((since ? day0 - dayT : dayT - day0) / 86400000);
    if (cfg.unit === 'auto' && hm && ms > 0 && ms < 86400000) {
      var h = Math.floor(ms / 3600000), m = Math.floor(ms % 3600000 / 60000);
      return since ? T('seit {h} Std. {m} Min.', { h: h, m: m }) : T('noch {h} Std. {m} Min.', { h: h, m: m });
    }
    if (days <= 0) return T('heute');
    return since ? T('seit {n} {n:Tag|Tagen}', { n: days }) : T('noch {n} {n:Tag|Tage}', { n: days });
  }
  function WidgetTool(o) {
    var d = o.data || {};
    this.data = JSON.parse(JSON.stringify(d));           // Regel 0: unbekannte Felder bleiben erhalten
    if (!this.data.kind) this.data.kind = 'metric';
    if (WG_KINDS.indexOf(this.data.kind) >= 0) {
      this.data.v = 1;
      this.fresh = !d.cfg;                                 // eben eingefuegt: Einstellungen gleich oeffnen
      this.data.cfg = Object.assign(wgDefaults(this.data.kind), this.data.cfg || {});
    }
    this.block = o.block; this.ro = !!o.readOnly; this.pv = null; this.schemas = {};
  }
  WidgetTool.isReadOnlySupported = true;
  WidgetTool.toolbox = WG_KINDS.map(function (k) { return { title: T('Widget: {art}', { art: WG_NAMES[k] }), icon: wgIcon(k), data: { kind: k } }; });
  WidgetTool.prototype.render = function () {
    var self = this, w = el('div', 'wg-blk'); this.w = w; isolate(w);
    this.card = el('div'); w.appendChild(this.card);
    this.card.addEventListener('click', function (e) { if (e.target.closest('a')) return; if (self.editable() && !self.panel) self.openPanel(); });
    WG.add(this); this.draw();
    if (this.fresh && this.editable()) setTimeout(function () { if (w.isConnected) self.openPanel(); }, 0);
    return w;
  };
  WidgetTool.prototype.editable = function () { return !this.ro && !readOnly() && WG_KINDS.indexOf(this.data.kind) >= 0; };
  WidgetTool.prototype.destroy = function () { clearInterval(this.tick); WG.delete(this); };
  WidgetTool.prototype.changed = function () {
    if (S.loading) return;
    if (this.block && this.block.dispatchChange) this.block.dispatchChange(); else { S.dirtyBody = true; schedule(); }
  };
  WidgetTool.prototype.value = function () {
    if (this.pv) return this.pv;
    var bid = this.block && this.block.id;
    return (S.wvals && bid && S.wvals[bid]) || null;
  };
  WidgetTool.prototype.draw = function () {
    var self = this, d = this.data, c = this.card, cfg = d.cfg || {};
    clearInterval(this.tick); this.tick = null;
    c.className = 'wg wg-' + (d.size === 's' || d.size === 'l' ? d.size : 'm') + ' wg-' + (d.style === 'plain' ? 'plain' : 'card')
      + (COLORS.indexOf(d.accent) >= 0 ? ' wg-a-' + d.accent : '') + (this.editable() ? ' wg-ed' : '');
    c.textContent = '';
    c.dataset.kind = String(d.kind || '');
    if (WG_KINDS.indexOf(d.kind) < 0) { c.appendChild(el('div', 'wg-note', T('Widget – neuere Version nötig'))); return; }
    if (d.title) c.appendChild(el('div', 'wg-t', String(d.title)));
    var body = el('div', 'wg-b'); c.appendChild(body);
    if (d.kind === 'clock') {
      var fmt = { hour: '2-digit', minute: '2-digit', hour12: cfg.h24 === false };
      if (cfg.sec) fmt.second = '2-digit';
      var fd = { weekday: 'long', day: 'numeric', month: 'long' };
      if (cfg.tz) { fmt.timeZone = cfg.tz; fd.timeZone = cfg.tz; }
      var ft, fdt;
      try { ft = new Intl.DateTimeFormat(window.SAGA_LOCALE, fmt); fdt = new Intl.DateTimeFormat(window.SAGA_LOCALE, fd); }
      catch (e) { ft = new Intl.DateTimeFormat(window.SAGA_LOCALE, { hour: '2-digit', minute: '2-digit' }); fdt = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'long', day: 'numeric', month: 'long' }); }
      var v = el('div', 'wg-v wg-clock'), sub = el('div', 'wg-sub'), hands = null;
      if (cfg.analog) {
        var NS = 'http://www.w3.org/2000/svg', svg = document.createElementNS(NS, 'svg'); svg.setAttribute('viewBox', '0 0 100 100'); svg.setAttribute('class', 'wg-analog');
        svg.setAttribute('aria-hidden', 'true');
        var face = document.createElementNS(NS, 'circle'); face.setAttribute('cx', '50'); face.setAttribute('cy', '50'); face.setAttribute('r', '46'); face.setAttribute('class', 'wg-face'); svg.appendChild(face);
        hands = ['h', 'm', 's'].filter(function (k) { return k !== 's' || cfg.sec; }).map(function (k) {
          var l = document.createElementNS(NS, 'line'); l.setAttribute('x1', '50'); l.setAttribute('y1', '50'); l.setAttribute('x2', '50');
          l.setAttribute('y2', k === 'h' ? '26' : k === 'm' ? '14' : '10'); l.setAttribute('class', 'wg-hand wg-hand-' + k); svg.appendChild(l); return [k, l];
        });
        body.appendChild(svg);
      }
      body.appendChild(v); body.appendChild(sub);
      var paint = function () {
        var now = new Date();
        v.textContent = ft.format(now); sub.textContent = fdt.format(now);
        if (hands) {
          var parts = {}; (ft.formatToParts ? new Intl.DateTimeFormat('en-GB', { hour: 'numeric', minute: 'numeric', second: 'numeric', hour12: false, timeZone: cfg.tz || undefined }).formatToParts(now) : [])
            .forEach(function (p) { parts[p.type] = +p.value; });
          var H = (parts.hour || 0) % 12, M = parts.minute || 0, Sx = parts.second || 0;
          hands.forEach(function (x) { var a = x[0] === 'h' ? (H + M / 60) * 30 : x[0] === 'm' ? (M + Sx / 60) * 6 : Sx * 6; x[1].setAttribute('transform', 'rotate(' + a.toFixed(1) + ' 50 50)'); });
        }
      };
      paint(); this.tick = setInterval(function () { if (!self.w.isConnected) { self.destroy(); return; } if (!document.hidden) paint(); }, cfg.sec ? 1000 : 15000);
      return;
    }
    if (d.kind === 'countdown') {
      var cv = el('div', 'wg-v'), when = el('div', 'wg-sub'); body.appendChild(cv); body.appendChild(when);
      var paintC = function () {
        var txt = wgCountdown(cfg, new Date());
        cv.textContent = txt == null ? '–' : txt;
        if (cfg.date) { var q = cfg.date.split('-'); when.textContent = fCalcDay2.format(new Date(+q[0], +q[1] - 1, +q[2])) + (cfg.time ? ' · ' + cfg.time : ''); }
        else when.textContent = T('Kein Datum gewählt');
      };
      paintC(); this.tick = setInterval(function () { if (!self.w.isConnected) { self.destroy(); return; } if (!document.hidden) paintC(); }, 60000);
      return;
    }
    var w = d.kind === 'progress' && cfg.src !== 'db' ? { value: +cfg.value || 0, max: +cfg.max || 100 } : this.value();
    if (wgServer(d) && remoteId(S.cur && S.cur.id) && S.wUnsupported) { body.appendChild(el('div', 'wg-note', T('Widget nur auf dem Ursprungsserver'))); return; }
    if (wgServer(d) && S.wMissing) { body.appendChild(el('div', 'wg-note', T('Werte gibt es erst mit einer neueren Server-Version'))); return; }
    if (w && w.error) { body.appendChild(el('div', 'wg-note wg-err', WG_ERR[w.error] || WG_ERR.bad_cfg)); return; }
    if (d.kind === 'progress') {
      var pv = w ? w.value : null, mx = w ? w.max : null, pct = typeof pv === 'number' && typeof mx === 'number' && mx > 0 ? Math.max(0, Math.min(100, pv / mx * 100)) : 0;
      body.appendChild(el('div', 'wg-v', w ? Math.round(pct) + ' %' : '…'));
      var bar = el('div', 'wg-bar'), fill = el('span', 'wg-fill'); fill.style.width = pct.toFixed(1) + '%'; bar.appendChild(fill); body.appendChild(bar);
      bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100'); bar.setAttribute('aria-valuenow', String(Math.round(pct)));
      if (w) body.appendChild(el('div', 'wg-sub', wgFmt(pv) + ' / ' + wgFmt(mx)));
      return;
    }
    if (d.kind === 'metric') {
      body.appendChild(el('div', 'wg-v', w ? wgFmt(w.value, w.fmt) : '…'));
      if (w && typeof w.max === 'number') {
        var pm = w.max ? Math.max(0, Math.min(100, (w.value || 0) / w.max * 100)) : 0;
        var b2 = el('div', 'wg-bar'), f2 = el('span', 'wg-fill'); f2.style.width = pm.toFixed(1) + '%'; b2.appendChild(f2); body.appendChild(b2);
        body.appendChild(el('div', 'wg-sub', T('Ziel: {wert}', { wert: wgFmt(w.max, w.fmt) })));
      }
      return;
    }
    // Zitat: Auswahl immer durch den Server (Tag des Betrachters + Block-ID bzw. zufaellig), auch bei eigenen Texten
    var txt = w ? w.text : null;
    body.appendChild(el('blockquote', 'wg-q', txt || (w ? T('Noch keine Zitate') : '…')));
  };
  var fCalcDay2 = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  // Einrichten: Panel unter dem Widget, jede Aenderung zeichnet neu (Vorschau ueber den Server, wo er rechnet)
  WidgetTool.prototype.openPanel = function () {
    var self = this, d = this.data;
    this.fresh = false;
    var p = this.panel = el('div', 'wg-panel'); p.setAttribute('role', 'group'); p.setAttribute('aria-label', T('Widget einrichten'));
    this.w.appendChild(p); this.fill();
    var done = el('button', 'btn wg-done', T('Fertig')); done.type = 'button';
    done.addEventListener('click', function () { self.closePanel(); });
    this.doneBtn = done;
    p.appendChild(done);
    void d;
  };
  WidgetTool.prototype.closePanel = function () { if (this.panel) this.panel.remove(); this.panel = null; this.pv = null; this.draw(); loadWidgets(); };
  WidgetTool.prototype.fill = function () {
    var self = this, d = this.data, cfg = d.cfg, p = this.panel;
    [].slice.call(p.querySelectorAll('.wg-row')).forEach(function (x) { x.remove(); });
    var at = p.querySelector('.wg-done');
    function row(label, input) { var r = el('label', 'wg-row'); r.appendChild(el('span', 'wg-l', label)); r.appendChild(input); p.insertBefore(r, at); return input; }
    function set(k, v, top) { if (top) d[k] = v; else cfg[k] = v; self.changed(); self.update(); }
    function sel(label, opts, cur, fn) {
      var s = el('select', 'flt-val');
      opts.forEach(function (o) { var x = el('option', null, o[1]); x.value = o[0]; if (String(cur == null ? '' : cur) === String(o[0])) x.selected = true; s.appendChild(x); });
      s.addEventListener('change', function () { fn(s.value); });
      return row(label, s);
    }
    function inp(label, type, cur, fn, attrs) {
      var i = el('input', 'flt-val'); i.type = type; i.value = cur == null ? '' : String(cur);
      Object.keys(attrs || {}).forEach(function (k) { i.setAttribute(k, attrs[k]); });
      i.addEventListener(type === 'checkbox' ? 'change' : 'input', function () { fn(type === 'checkbox' ? i.checked : i.value); });
      if (type === 'checkbox') i.checked = !!cur;
      return row(label, i);
    }
    inp(T('Titel'), 'text', d.title || '', function (v) { if (v.trim()) d.title = v.slice(0, 80); else delete d.title; self.changed(); self.update(); }, { maxlength: '80' });
    sel(T('Größe'), [['s', T('Klein')], ['m', T('Mittel')], ['l', T('Groß')]], d.size || 'm', function (v) { set('size', v, true); });
    sel(T('Darstellung'), [['card', T('Karte')], ['plain', T('Ohne Rahmen')]], d.style || 'card', function (v) { set('style', v, true); });
    sel(T('Farbe'), [['', T('Standard')]].concat(COLORS.map(function (c) { return [c, COLOR_NAMES[c]]; })), d.accent || '', function (v) { if (v) d.accent = v; else delete d.accent; self.changed(); self.update(); });
    var k = d.kind;
    if (k === 'clock') {
      var zones = [];
      try { zones = Intl.supportedValuesOf ? Intl.supportedValuesOf('timeZone') : []; } catch (e) { zones = []; }
      if (zones.length) sel(T('Zeitzone'), [['', T('Wie beim Betrachter')]].concat(zones.map(function (z) { return [z, z.replace(/_/g, ' ')]; })), cfg.tz, function (v) { set('tz', v); });
      else inp(T('Zeitzone'), 'text', cfg.tz, function (v) { set('tz', v.trim()); }, { placeholder: 'Europe/Berlin' });
      inp(T('24 Stunden'), 'checkbox', cfg.h24 !== false, function (v) { set('h24', v); });
      inp(T('Sekunden'), 'checkbox', cfg.sec, function (v) { set('sec', v); });
      inp(T('Analog'), 'checkbox', cfg.analog, function (v) { set('analog', v); });
      return;
    }
    if (k === 'countdown') {
      inp(T('Datum'), 'date', cfg.date, function (v) { set('date', v); });
      inp(T('Uhrzeit (optional)'), 'time', cfg.time, function (v) { set('time', v); });
      sel(T('Richtung'), [['until', T('Bis zum Datum')], ['since', T('Seit dem Datum')]], cfg.dir, function (v) { set('dir', v); });
      sel(T('Einheit'), [['days', T('Tage')], ['auto', T('Automatisch (unter einem Tag Std./Min.)')]], cfg.unit, function (v) { set('unit', v); });
      return;
    }
    if (k === 'progress' || k === 'quote') {
      sel(T('Quelle'), [['fixed', k === 'quote' ? T('Eigene Texte') : T('Feste Zahlen')], ['db', T('Datenbank')]], cfg.src, function (v) {
        d.cfg = Object.assign(v === 'db' ? { src: 'db', db: null, view: '', prop: '' } : wgDefaults(k), k === 'quote' ? { pick: cfg.pick || 'daily' } : {}, v === 'db' && k === 'progress' ? { mode: 'checked' } : {});
        d.cfg.src = v; self.changed(); self.fill(); self.update();
      });
    }
    if (k === 'progress' && cfg.src !== 'db') {
      inp(T('Wert'), 'number', cfg.value, function (v) { set('value', v === '' ? 0 : +v); }, { step: 'any' });
      inp(T('Maximum'), 'number', cfg.max, function (v) { set('max', +v > 0 ? +v : 100); }, { step: 'any', min: '0' });
      return;
    }
    if (k === 'quote' && cfg.src !== 'db') {
      var ta = el('textarea', 'flt-val wg-items'); ta.rows = 4; ta.value = (cfg.items || []).join('\n'); ta.placeholder = T('Ein Zitat je Zeile');
      ta.addEventListener('input', function () { set('items', ta.value.split('\n').map(function (x) { return x.trim().slice(0, 300); }).filter(Boolean).slice(0, 20)); });
      row(T('Zitate'), ta);
      sel(T('Auswahl'), [['daily', T('Jeden Tag ein anderes')], ['open', T('Bei jedem Öffnen zufällig')]], cfg.pick, function (v) { set('pick', v); });
      return;
    }
    // Quelle Datenbank (Fortschritt, Kennzahl, Zitat)
    if (S.cur && remoteId(S.cur.id)) { p.insertBefore(el('div', 'wg-row wg-note', T('Datenquelle nur auf dem Ursprungsserver einstellbar')), at); return; }
    var dbs = (S.pages || []).concat(S.shared || []).filter(function (x) { return x.kind === 'database' && !remoteId(x.id) && !x.archived; });
    var dsel = sel(T('Datenbank'), [['', dbs.length ? T('– wählen –') : T('noch keine Datenbank')]].concat(dbs.map(function (x) { return [x.id, (ti(x.icon) || '🗃️') + ' ' + label(x)]; })),
      cfg.db, function (v) { cfg.db = v ? +v : null; cfg.view = ''; cfg.prop = ''; self.changed(); self.fill(); self.update(); });
    if (cfg.db && !dbs.some(function (x) { return x.id === cfg.db; })) {                   // gespeicherte Quelle, die ich nicht sehe: nie ihren Namen
      var o0 = el('option', null, T('Datenbank ohne Zugriff')); o0.value = String(cfg.db); o0.selected = true; dsel.appendChild(o0);
    }
    if (!cfg.db) return;
    var sc = this.schemas[cfg.db];
    if (!sc) {
      this.schemas[cfg.db] = 'load';
      api('GET', '/api/notes/' + cfg.db + '/rows?limit=1').then(function (r) { self.schemas[cfg.db] = r.schema || { props: [] }; if (self.panel) self.fill(); })
        .catch(function () { self.schemas[cfg.db] = { props: [], views: [] }; if (self.panel) self.fill(); });
      return;
    }
    if (sc === 'load') return;
    var props = Array.isArray(sc.props) ? sc.props : [], views = Array.isArray(sc.views) ? sc.views : [];
    sel(T('Ansicht'), [['', T('Erste Ansicht')]].concat(views.map(function (v) { return [v.id, v.name || T('Ansicht')]; })), cfg.view, function (v) { set('view', v); });
    var byType = function (types) { return props.filter(function (x) { return types.indexOf(x.type) >= 0; }).map(function (x) { return [x.id, x.name || x.id]; }); };
    if (k === 'progress') {
      sel(T('Fortschritt aus'), [['checked', T('Anteil angehakt')], ['sum', T('Summe einer Zahl bis zum Ziel')]], cfg.mode, function (v) { cfg.mode = v; cfg.prop = ''; self.changed(); self.fill(); self.update(); });
      sel(T('Eigenschaft'), [['', T('– wählen –')]].concat(byType(cfg.mode === 'sum' ? ['number', 'calc'] : ['checkbox'])), cfg.prop, function (v) { set('prop', v); });
      if (cfg.mode === 'sum') inp(T('Ziel'), 'number', cfg.goal, function (v) { if (+v > 0) cfg.goal = +v; else delete cfg.goal; self.changed(); self.update(); }, { step: 'any', min: '0' });
      return;
    }
    if (k === 'metric') {
      sel(T('Rechenart'), [['count', T('Anzahl')], ['sum', T('Summe')], ['avg', T('Durchschnitt')], ['min', T('Min')], ['max', T('Max')], ['pct_checked', T('% angehakt')]], cfg.agg, function (v) {
        cfg.agg = v; cfg.prop = ''; self.changed(); self.fill(); self.update(); });
      if (cfg.agg !== 'count') sel(T('Eigenschaft'), [['', T('– wählen –')]].concat(byType(cfg.agg === 'pct_checked' ? ['checkbox'] : ['number', 'calc'])), cfg.prop, function (v) { set('prop', v); });
      if (cfg.agg !== 'pct_checked') sel(T('Format'), [['num', T('Zahl')], ['pct', T('Prozent')], ['eur', T('Euro')]], cfg.fmt, function (v) { set('fmt', v); });
      inp(T('Ziel (optional)'), 'number', cfg.goal, function (v) { if (v !== '' && isFinite(+v)) cfg.goal = +v; else delete cfg.goal; self.changed(); self.update(); }, { step: 'any' });
      return;
    }
    sel(T('Text aus'), [['', T('Name')]].concat(byType(['text', 'url', 'email', 'phone', 'number'])), cfg.prop, function (v) { set('prop', v); });
    sel(T('Auswahl'), [['daily', T('Jeden Tag ein anderes')], ['open', T('Bei jedem Öffnen zufällig')]], cfg.pick, function (v) { set('pick', v); });
  };
  // Vorschau: Server-Werte fuer den ungespeicherten Stand (verzoegert, nur das letzte Ergebnis zaehlt)
  WidgetTool.prototype.update = function () {
    var self = this;
    this.draw();
    if (!wgServer(this.data) || !S.cur) return;
    clearTimeout(this.pvT);
    this.pvT = setTimeout(function () {
      var seq = self.pvSeq = (self.pvSeq || 0) + 1, tz = '';
      try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { tz = ''; }
      api('POST', '/api/notes/' + S.cur.id + '/widgets/preview' + (tz ? '?tz=' + encodeURIComponent(tz) : ''), { block: { id: self.block && self.block.id, type: 'widget', data: self.data } })
        .then(function (r) { if (seq === self.pvSeq && r && r.widget) { self.pv = r.widget; self.draw(); } })
        .catch(function () { if (seq === self.pvSeq) { self.pv = { error: 'bad_cfg' }; self.draw(); } });
    }, 350);
  };
  WidgetTool.prototype.save = function () { return JSON.parse(JSON.stringify(this.data)); };
  // Werte der Seite laden (beim Oeffnen, nach dem Speichern, alle 60 s solange sichtbar)
  function loadWidgets() {
    var mine = [];
    WG.forEach(function (x) { if (!x.w || !x.w.isConnected) WG.delete(x); else if (wgServer(x.data)) mine.push(x); });
    if (!S.cur || !mine.length) return Promise.resolve();
    var id = S.cur.id, tz = '';
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { tz = ''; }
    return api('GET', '/api/notes/' + id + '/widgets' + (tz ? '?tz=' + encodeURIComponent(tz) : '')).then(function (r) {
      if (!S.cur || S.cur.id !== id) return;
      S.wvals = (r && r.widgets) || {}; S.wUnsupported = !!(r && r.unsupported); S.wMissing = false;
      mine.forEach(function (x) { if (!x.panel) x.draw(); });
    }).catch(function (err) {
      if (!S.cur || S.cur.id !== id) return;
      if (err.status === 404 && !remoteId(id)) { S.wMissing = true; mine.forEach(function (x) { if (!x.panel) x.draw(); }); }
    });
  }
  document.addEventListener('hk-page-open', function () { S.wvals = {}; S.wUnsupported = false; S.wMissing = false; });
  document.addEventListener('hk-page-ready', function () { loadWidgets(); });
  setInterval(function () { if (!document.hidden && S.cur) loadWidgets(); }, 60000);

  // Block "Kalender": Termine der verbundenen externen Kalender - Monat oder Liste (naechste 14 Tage, wie in der App)
  function CalendarTool(o) {
    var d = (o && o.data) || {};
    this.block = o && o.block; this.view = d.view === 'list' ? 'list' : 'month'; this.days = Math.max(1, Math.min(62, +d.days || 14));
  }
  CalendarTool.toolbox = { title: T('Kalender'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>' };
  CalendarTool.prototype.render = function () { this.w = el('div', 'embed-cal'); isolate(this.w); this.draw(); return this.w; };
  // Aufgaben, Kalender, Immich gehoeren dem Konto, das die Seite anschaut - auf fremden Seiten nie zeigen (weder meine noch die des Eigentuemers)
  function ownerOnly(w, what) {
    w.textContent = ''; w.classList.add('owner-only');
    w.appendChild(el('span', 'owner-only-t', what + ' · ' + T('Nur für den Eigentümer sichtbar')));
  }
  CalendarTool.prototype.draw = function () {
    var self = this, w = this.w; w.textContent = '';
    if (foreign()) { ownerOnly(w, '🗓 ' + T('Kalender')); return; }
    var sw = el('div', 'cal-mode');                                   // Umschalter beim Ueberfahren (wie bei Spalten)
    [['month', '▦ ' + T('Monat')], ['list', '☰ ' + T('Liste')]].forEach(function (m) {
      var b = el('button', 'cal-mode-b' + (self.view === m[0] ? ' on' : ''), m[1]); b.type = 'button';
      b.addEventListener('click', function () {
        if (self.view === m[0]) return; self.view = m[0]; self.draw();
        if (self.block && self.block.dispatchChange) self.block.dispatchChange(); else { S.dirtyBody = true; schedule(); }
      });
      sw.appendChild(b);
    });
    w.appendChild(sw);
    var box = el('div'); w.appendChild(box);
    if (!window.HKCal) { box.textContent = T('Kalender nicht verfügbar'); return; }
    if (this.view === 'list') HKCal.agenda(box, { days: this.days }); else HKCal.month(box);
  };
  CalendarTool.prototype.save = function () { return { view: this.view, days: this.days }; };

  // "/Bild aus Immich": oeffnet die Auswahl und ersetzt sich durch einen normalen Bild-Block (Abbruch -> Block weg)
  function ImmichTool(o) { this.api = o.api; }
  ImmichTool.toolbox = { title: T('Bild aus Immich'), icon: '<svg width="17" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3.2"/><path d="M8 5l1.5-2h5L16 5"/></svg>' };
  ImmichTool.prototype.render = function () {
    if (foreign()) { this.w = el('div', 'imm-placeholder'); ownerOnly(this.w, '📷 Immich'); return this.w; }
    var self = this; this.w = el('div', 'imm-placeholder', '📷 ' + T('Bild aus Immich wählen …'));
    this.w.addEventListener('click', function () { self.open(); });
    setTimeout(function () { self.open(); }, 0);
    return this.w;
  };
  ImmichTool.prototype.index = function () {
    for (var i = 0, n = this.api.blocks.getBlocksCount(); i < n; i++) { var b = this.api.blocks.getBlockByIndex(i); if (b && b.holder && b.holder.contains(this.w)) return i; }
    return -1;
  };
  ImmichTool.prototype.open = function () {
    var self = this; if (!HK.immichPick || this.opening) return; this.opening = true;
    HK.immichPick(function (url, name) {
      var i = self.index(); if (i < 0) return;
      self.api.blocks.insert('image', { file: { url: url }, caption: '', withBorder: false, withBackground: false, stretched: false }, {}, i, true);
      self.api.blocks.delete(i + 1);
    }, function () { self.opening = false; var i = self.index(); if (i >= 0) self.api.blocks.delete(i); });
  };
  ImmichTool.prototype.save = function () { return {}; };
  ImmichTool.prototype.validate = function () { return false; };     // Platzhalter nie mitspeichern

  // "/Nextcloud-Datei" (2.3): Verweis auf eine Datei/einen Ordner im EIGENEN Nextcloud. Vorschau und Download laufen ueber
  // Saga mit dem Konto der Person, die den Block eingefuegt hat (author - setzt der Server). Schalter "veroeffentlichte
  // Seite" / "verbundene Server" (Standard aus) nur fuer diese Person. Fremde Bloecke lassen sich nicht in andere Seiten
  // kopieren (der Server wuerde das Speichern ablehnen) - sie fallen beim Speichern mit einem Hinweis weg.
  function ncKeys(data) {
    var keys = {};
    (function walk(bs) {
      (Array.isArray(bs) ? bs : []).forEach(function (b) {
        if (!b || typeof b !== 'object') return;
        var d = b.data || {};
        if (b.type === 'nextcloud') keys[ncKey(d)] = true;
        if (Array.isArray(d.blocks)) walk(d.blocks);
        if (Array.isArray(d.cols)) d.cols.forEach(function (c) { walk(c && c.blocks); });
      });
    })(data && data.blocks);
    return keys;
  }
  function ncKey(d) { return [d.author || '', d.inst || '', d.fileid || ''].join('|'); }
  function ncStatus() {
    var id = S.cur && S.cur.id;
    if (!id) return Promise.resolve({ items: [] });
    if (!S.ncStat || S.ncStat.id !== id || Date.now() - S.ncStat.t > 60000)
      S.ncStat = { id: id, t: Date.now(), p: api('GET', '/api/notes/' + id + '/nc').catch(function () { return { items: [], failed: true }; }) };
    return S.ncStat.p;
  }
  function decodeHtml(s) { var t = document.createElement('textarea'); t.innerHTML = String(s || ''); return t.value; }
  function NextcloudTool(o) {
    this.api = o.api; this.block = o.block; this.ro = !!o.readOnly;
    var d = o.data || {}; this.data = {};
    var self = this;
    ['inst', 'fileid', 'kind', 'name', 'mime', 'size', 'mtime', 'author', 'public', 'fed', 'sig'].forEach(function (k) { if (d[k] !== undefined) self.data[k] = d[k]; });
  }
  NextcloudTool.toolbox = { title: T('Nextcloud-Datei'), icon: '<svg width="18" height="14" viewBox="0 0 24 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 16h11a4 4 0 0 0 .6-7.95A6 6 0 0 0 7.2 7 4.5 4.5 0 0 0 7 16z"/></svg>' };
  NextcloudTool.isReadOnlySupported = true;
  NextcloudTool.prototype.render = function () { this.w = el('div', 'nc-block'); isolate(this.w); this.draw(); return this.w; };
  NextcloudTool.prototype.changed = function () { if (this.block && this.block.dispatchChange) this.block.dispatchChange(); else { S.dirtyBody = true; schedule(); } };
  NextcloudTool.prototype.index = function () {
    for (var i = 0, n = this.api.blocks.getBlocksCount(); i < n; i++) { var b = this.api.blocks.getBlockByIndex(i); if (b && b.holder && b.holder.contains(this.w)) return i; }
    return -1;
  };
  NextcloudTool.prototype.open = function () {
    var self = this; if (!HK.nc || this.opening) return; this.opening = true;
    HK.nc.pick(function (r) {
      self.opening = false;
      if (r.mode === 'copy') {                          // Kopie: normaler Bild-/Datei-Block (folgt den Saga-Rechten)
        var i = self.index(); if (i < 0) return;
        if (r.image) self.api.blocks.insert('image', { file: { url: r.file.url }, caption: '', withBorder: false, withBackground: false, stretched: false }, {}, i, true);
        else self.api.blocks.insert('attaches', { file: { url: r.file.url, name: r.file.name, size: r.file.size, extension: r.file.extension }, title: r.file.name }, {}, i, true);
        self.api.blocks.delete(i + 1); return;
      }
      HK.nc.info().then(function (s) {
        self.data = r.block; self.data.author = s.me; S.ncKeys = S.ncKeys || {}; S.ncKeys[ncKey(self.data)] = true;
        S.ncStat = null; self.draw(); self.changed();
      });
    }, function () { self.opening = false; if (!self.data.fileid) { var i = self.index(); if (i >= 0) self.api.blocks.delete(i); } },
    // Seite einer anderen Saga-Instanz: nur "Als Kopie" (ein Verweis liefe ueber ein Konto, das der Server dort nicht kennt)
    { note: foreign() ? S.cur.id : null, link: !(S.cur && remoteId(S.cur.id)) });
  };
  NextcloudTool.prototype.draw = function () {
    var self = this, w = this.w, d = this.data; w.textContent = '';
    var remote = !!S.cur && remoteId(S.cur.id);
    if (!d.fileid) {
      if (this.ro) return;
      var ph = el('button', 'nc-empty', '☁ ' + T('Datei aus Nextcloud wählen …')); ph.type = 'button';
      ph.addEventListener('click', function () { self.open(); }); w.appendChild(ph);
      setTimeout(function () { self.open(); }, 0); return;
    }
    var name = decodeHtml(d.name) || T('Nextcloud-Datei');
    var nid = S.cur ? S.cur.id : 0;
    var base = '/api/notes/' + nid + '/nc/' + encodeURIComponent(d.fileid) + '?a=' + encodeURIComponent(d.author || '');
    var card = el('div', 'nc-cardb'); w.appendChild(card);
    var top = el('div', 'nc-top'); card.appendChild(top);
    top.appendChild(el('span', 'nc-ic', HK.nc ? HK.nc.icon(d) : '📄'));
    var tx = el('div', 'nc-tx'); top.appendChild(tx);
    tx.appendChild(el('span', 'nc-name', name));
    var meta = el('span', 'nc-meta', [d.kind === 'folder' ? T('Ordner') : (HK.nc ? HK.nc.size(d.size) : ''), 'Nextcloud'].filter(Boolean).join(' · '));
    tx.appendChild(meta);
    var acts = el('div', 'nc-acts'); top.appendChild(acts);
    var media = el('div', 'nc-media'); card.appendChild(media);
    var note = el('p', 'nc-note'); note.hidden = true; card.appendChild(note);
    // eben eingefuegt (noch ohne Signatur): der Server kennt den Block erst nach dem Speichern - kurz nachfragen
    var fresh = !d.sig && !remote;
    function retry() {
      if (!fresh || (self.tries = (self.tries || 0) + 1) > 8 || !document.contains(w)) return false;
      note.hidden = false; note.textContent = T('Lade …');
      setTimeout(function () { if (S.ncStat && S.ncStat.id === nid) S.ncStat = null; self.draw(); }, 1500);
      return true;
    }
    function unavailable(txt) { if (retry()) return; media.textContent = ''; note.hidden = false; note.textContent = txt; card.classList.add('off'); acts.querySelectorAll('.nc-dl').forEach(function (a) { a.remove(); }); }
    if (d.kind !== 'folder') {
      var dl = el('a', 'btn nc-dl', T('Herunterladen')); dl.href = base + '&dl=1'; dl.setAttribute('download', name); acts.appendChild(dl);
    }
    if (/^https?:\/\/[^\s"<>]+$/.test(d.inst || '') && !remote) {
      var op = el('a', 'btn nc-open', T('In Nextcloud öffnen') + ' ↗'); op.href = d.inst.replace(/\/$/, '') + '/f/' + encodeURIComponent(d.fileid);
      op.target = '_blank'; op.rel = 'noopener noreferrer'; acts.appendChild(op);
    }
    ncStatus().then(function (st) {
      var it = (st.items || []).filter(function (x) { return x.fileid === String(d.fileid) && (x.author === d.author || remote); })[0];
      if (st.failed && !it) { if (remote) unavailable(T('Nur auf dem Server der Seite sichtbar (Freigabe für verbundene Server ist aus).')); return; }
      if (!it || !it.ok) {
        unavailable(remote ? T('Nur auf dem Server der Seite sichtbar (Freigabe für verbundene Server ist aus).')
          : T('Nicht verfügbar – Verknüpfung getrennt, kein Zugriff mehr oder Datei gelöscht.'));
        return;
      }
      if (it.name && it.name !== name) tx.firstChild.textContent = it.name;
      meta.textContent = [it.dir ? T('Ordner') : HK.nc.size(it.size), it.mtime ? new Date(it.mtime * 1000).toLocaleDateString() : '', 'Nextcloud'].filter(Boolean).join(' · ');
      if (it.dir) {
        var ul = el('ul', 'nc-entries');
        (it.entries || []).forEach(function (e) { ul.appendChild(el('li', null, (e.dir ? '📁 ' : '📄 ') + e.name)); });
        if (!(it.entries || []).length) ul.appendChild(el('li', 'nc-meta', T('Leerer Ordner')));
        media.appendChild(ul);
      } else if (d.kind === 'image' || it.preview) {
        var img = el('img', d.kind === 'image' ? 'nc-img' : 'nc-prev'); img.alt = name; img.loading = 'lazy';
        img.src = base + '&w=' + (d.kind === 'image' ? 1280 : 320);
        img.addEventListener('error', function () { img.remove(); retry(); });
        if (d.kind === 'image') img.addEventListener('click', function () { window.open(base + '&w=2000', '_blank', 'noopener'); });
        media.appendChild(img);
      }
    });
    // Nur die Person, die den Block eingefuegt hat: Schalter + "Als Kopie uebernehmen"
    if (this.ro || remote || !canEdit()) return;
    HK.nc && HK.nc.info().then(function (s) {
      if (s.me !== d.author) { card.appendChild(el('p', 'nc-by', T('Eingefügt aus dem Nextcloud einer anderen Person – nur sie kann die Freigaben ändern.'))); return; }
      var opts = el('div', 'nc-opts'); card.appendChild(opts);
      function sw(label, key, hint) {
        var l = el('label', 'nc-sw'), i = el('input'); i.type = 'checkbox'; i.checked = d[key] === true;
        l.appendChild(i); l.appendChild(el('span', null, label)); l.title = hint;
        i.addEventListener('change', function () { d[key] = i.checked; delete d.sig; self.changed(); });
        opts.appendChild(l);
      }
      sw(T('Auf veröffentlichter Seite zeigen'), 'public', T('Besucher der veröffentlichten Seite sehen Vorschau und Download (über Saga, nicht über deine Nextcloud-Adresse).'));
      sw(T('Über verbundene Server zeigen'), 'fed', T('Personen anderer Saga-Server, mit denen die Seite geteilt ist, sehen Vorschau und Download.'));
      if (d.kind !== 'folder') {
        var cp = el('button', 'btn nc-copy', T('Als Kopie übernehmen')); cp.type = 'button'; opts.appendChild(cp);
        cp.addEventListener('click', function () {
          cp.disabled = true;
          api('POST', '/api/nextcloud/import', foreign() ? { fileid: d.fileid, note: S.cur.id } : { fileid: d.fileid }).then(function (r) {
            var i = self.index(); if (i < 0) return;
            if (/^image\//.test(r.file.mime || '')) self.api.blocks.insert('image', { file: { url: r.file.url }, caption: '', withBorder: false, withBackground: false, stretched: false }, {}, i, true);
            else self.api.blocks.insert('attaches', { file: { url: r.file.url, name: r.file.name, size: r.file.size, extension: r.file.extension }, title: r.file.name }, {}, i, true);
            self.api.blocks.delete(i + 1);
          }).catch(function (e) { cp.disabled = false; HK.toast(e.message); });
        });
      }
    }).catch(function () {});
  };
  NextcloudTool.prototype.save = function () { return JSON.parse(JSON.stringify(this.data)); };
  NextcloudTool.prototype.validate = function (d) {
    if (!d || !d.fileid) return false;
    // fremder Block, der nicht schon in dieser Seite stand (Kopieren aus einer anderen Seite): nicht mitspeichern
    if (d.author && S.ncMe && d.author !== S.ncMe && !(S.ncKeys || {})[ncKey(d)]) {
      if (!this.warned) { this.warned = true; HK.toast(T('Nextcloud-Dateien anderer Personen lassen sich nicht in andere Seiten kopieren.')); }
      return false;
    }
    return true;
  };

  // "/Einbetten": YouTube als Player (youtube-nocookie), alle anderen Links als Karte (GoodNotes, Webseiten ...)
  function ytId(u) {
    var m = String(u || '').match(/(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})/);
    return m ? m[1] : null;
  }
  function decodeAmp(s) { return String(s || '').replace(/&amp;/g, '&'); }          // der Server speichert & als &amp;
  function EmbedTool(o) {
    var d = o.data || {}; this.api = o.api; this.block = o.block; this.ro = !!o.readOnly; this.title = decodeAmp(d.title);
    this.url = /^https?:\/\/\S+$/i.test(decodeAmp(d.url)) ? decodeAmp(d.url) : '';      // nur http(s) - nie javascript: o. ae.
    // 2.5 (G2): Breite/Ausrichtung des Players; Felder anderer Clients bleiben beim Speichern erhalten
    this.hkW = sizeW(d.width); this.hkA = ALIGNS[d.align] ? d.align : null; this.hkRo = this.ro; this.hkExtra = {};
    var self = this; Object.keys(d).forEach(function (k) { if (['url', 'title', 'kind', 'width', 'align'].indexOf(k) < 0) self.hkExtra[k] = d[k]; });
  }
  EmbedTool.toolbox = { title: T('Einbetten (YouTube, Link)'), icon: '<svg width="17" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M10 9l5 3-5 3z"/></svg>' };
  EmbedTool.isReadOnlySupported = true;
  EmbedTool.pasteConfig = { patterns: { url: /^https?:\/\/[^\s]+$/i } };
  EmbedTool.prototype.render = function () { this.w = el('div', 'hk-embed'); this.draw(); return this.w; };
  EmbedTool.prototype.changed = function () { if (this.block && this.block.dispatchChange) this.block.dispatchChange(); };
  EmbedTool.prototype.draw = function () {
    var self = this, w = this.w; w.textContent = '';
    if (!this.url) {
      if (this.ro) return;
      var inp = el('input', 'hk-in hk-embed-in'); inp.type = 'url'; inp.placeholder = T('Link einfügen – YouTube, Webseite, GoodNotes … (Enter)');
      inp.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return; e.preventDefault(); e.stopPropagation();
        var v = inp.value.trim(); if (!/^https?:\/\/\S+$/i.test(v)) { HK.toast(T('Bitte einen Link mit http:// oder https://')); return; }
        self.url = v; self.draw(); self.changed();
      });
      w.appendChild(inp); setTimeout(function () { inp.focus(); }, 0); return;
    }
    var id = ytId(this.url);
    if (id) {
      var box = el('div', 'hk-embed-yt'), f = el('iframe');
      f.src = 'https://www.youtube-nocookie.com/embed/' + id; f.title = T('YouTube-Video'); f.loading = 'lazy';
      f.setAttribute('allow', 'accelerometer; encrypted-media; gyroscope; picture-in-picture; fullscreen');
      f.setAttribute('allowfullscreen', ''); f.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
      box.appendChild(f); w.appendChild(box);
      this.hkBox = w; sizable(this, w, function () { return box; });
    } else {
      w.classList.remove('hk-sz', 'hk-iw', 'hk-al-left', 'hk-al-center', 'hk-al-right'); w.style.removeProperty('--iw');
      var u; try { u = new URL(this.url); } catch (e) { u = null; }
      var a = el('a', 'hk-embed-card'); a.href = this.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.appendChild(el('span', 'hk-embed-ic', /goodnotes/.test(this.url) ? '📒' : '🔗'));
      var tx = el('span', 'hk-embed-tx');
      tx.appendChild(el('span', 'hk-embed-t', this.title || (u ? u.hostname.replace(/^www\./, '') + (u.pathname.length > 1 ? u.pathname : '') : this.url)));
      tx.appendChild(el('span', 'hk-embed-u', u ? u.hostname.replace(/^www\./, '') : this.url));
      a.appendChild(tx); a.appendChild(el('span', 'hk-embed-go', '↗')); w.appendChild(a);
    }
    if (!this.ro) {
      var bar = el('div', 'hk-embed-bar');
      if (!id) {
        var tb = el('button', 'linkbtn', T('Titel')); tb.type = 'button';
        tb.addEventListener('click', function () { var t = prompt(T('Titel der Karte'), self.title || ''); if (t === null) return; self.title = t.trim().slice(0, 200); self.draw(); self.changed(); });
        bar.appendChild(tb);
      }
      var cb = el('button', 'linkbtn', T('Link ändern')); cb.type = 'button';
      cb.addEventListener('click', function () { self.url = ''; self.title = ''; self.draw(); self.changed(); });
      bar.appendChild(cb); w.appendChild(bar);
    }
  };
  EmbedTool.prototype.onPaste = function (e) {
    this.url = e.detail.data; if (this.w) this.draw(); this.changed();
    // Link aus der eigenen (verknuepften) Nextcloud -> Block "Nextcloud-Datei"
    var self = this, url = this.url;
    if (!HK.nc || !S.cur || remoteId(S.cur.id)) return;
    HK.nc.info().then(function (s) {
      if (!s.connected || !s.server || url.toLowerCase().indexOf(String(s.server).toLowerCase() + '/') !== 0) return null;
      return api('POST', '/api/nextcloud/resolve', { url: url }).then(function (r) {
        if (!r.match || self.url !== url) return;
        var i = -1;
        for (var k = 0, n = self.api.blocks.getBlocksCount(); k < n; k++) { var b = self.api.blocks.getBlockByIndex(k); if (b && b.holder && b.holder.contains(self.w)) { i = k; break; } }
        if (i < 0) return;
        var d = r.block; d.author = s.me; d.public = false; d.fed = false;
        S.ncKeys = S.ncKeys || {}; S.ncKeys[ncKey(d)] = true; S.ncStat = null;
        self.api.blocks.insert('nextcloud', d, {}, i, true); self.api.blocks.delete(i + 1);
        HK.toast(T('Nextcloud-Datei eingefügt. Alle, die diese Seite sehen, sehen auch diese Datei.'));
      });
    }).catch(function () { /* bleibt ein normaler Link */ });
  };
  EmbedTool.prototype.save = function () {
    var d = Object.assign({}, this.hkExtra, { url: this.url }); if (this.title) d.title = this.title;
    if (ytId(this.url)) { d.kind = 'youtube'; if (this.hkW) d.width = this.hkW; if (this.hkA) d.align = this.hkA; }   // Link-Karten: ohne Breite
    return d;
  };
  EmbedTool.prototype.renderSettings = function () { return this.url && ytId(this.url) ? alignItems(this) : []; };
  EmbedTool.prototype.validate = function (d) { return /^https?:\/\//i.test(d.url || ''); };

  var fTaskDue = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
  function TasksTool() {}
  TasksTool.toolbox = { title: T('Aufgaben'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l2 2 4-4M4 14l2 2 4-4M13 7h7M13 15h7"/></svg>' };
  TasksTool.prototype.render = function () { this.w = el('div', 'embed-tasks'); isolate(this.w); this.draw(); return this.w; };
  TasksTool.prototype.draw = function () {
    var self = this, w = this.w;
    if (foreign()) { ownerOnly(w, '✓ ' + T('Aufgaben')); return; }
    api('GET', '/api/tasks').then(function (d) {
      w.textContent = '';
      var open = d.open || [];
      var h = el('div', 'embed-head'); h.appendChild(el('span', 'embed-title-s', '✓ ' + T('Aufgaben'))); h.appendChild(el('span', 'db-count', T('{n} offen', { n: open.length })));
      w.appendChild(h);
      var ul = el('div', 'etk-list');
      open.forEach(function (t) {
        var row = el('div', 'etk-row p' + (t.prio || 2));
        var ck = el('button', 'etk-check'); ck.type = 'button'; ck.setAttribute('aria-label', T('Erledigt: {text}', { text: t.text }));
        ck.addEventListener('click', function () {
          row.classList.add('done');
          api('PATCH', '/api/tasks/' + t.id, { done: true }).then(function (r) {
            var msg = r && r.next_due ? T('Erledigt. Nächstes Mal: {date}', { date: fTaskDue.format(new Date(r.next_due + 'T00:00:00')) }) : T('Aufgabe erledigt');
            HK.toast(msg, T('Rückgängig'), function () {      // wie Dashboard und Apps: 8 s zum Zuruecknehmen
              api('PATCH', '/api/tasks/' + t.id, { done: false }).then(function () { self.draw(); })
                .catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
            }, 8000);
            self.draw();
          }).catch(function (err) { row.classList.remove('done'); HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
        });
        row.appendChild(ck); row.appendChild(el('span', 'etk-text', t.text));
        if (t.repeat) row.appendChild(el('span', 'etk-meta', '↻'));
        if (t.due) row.appendChild(el('span', 'etk-meta' + (t.due < new Date().toISOString().slice(0, 10) ? ' overdue' : ''), fTaskDue.format(new Date(t.due + 'T00:00:00'))));
        ul.appendChild(row);
      });
      if (!open.length) ul.appendChild(el('div', 'nt-empty', T('Alles erledigt 🎉')));
      w.appendChild(ul);
      var add = el('input', 'etk-add'); add.type = 'text'; add.maxLength = 300; add.placeholder = T('+ Neue Aufgabe (Enter)'); add.setAttribute('aria-label', T('Neue Aufgabe'));
      add.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter' || !add.value.trim()) return;
        var text = add.value.trim(); add.value = '';
        api('POST', '/api/tasks', { text: text }).then(function () { self.draw(); }).catch(function (err) { HK.toast(T('Aufgabe nicht angelegt: {err}', { err: err.message })); });
      });
      w.appendChild(add);
    }).catch(function (err) { w.textContent = T('Aufgaben nicht geladen: {err}', { err: err.message }); });
  };
  TasksTool.prototype.save = function () { return {}; };

  // --- Seitenleiste ein/aus (pro Browser) und volle Breite (pro Seite, Server) ------
  var isMacKey = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  function setSide(show) {
    document.body.classList.toggle('side-hidden', !show);
    var b = $('pg-side-btn'); b.setAttribute('aria-pressed', show ? 'true' : 'false');
    b.title = (show ? T('Seitenleiste ausblenden') : T('Seitenleiste einblenden')) + (isMacKey ? ' (⌘\\)' : ' (' + T('Strg') + '+\\)');
    HK.store('hk-pg-side', show ? '1' : '0');
  }
  // Schmale Fenster (Handy): Seitenleiste als Schublade von links ueber dem Inhalt; der Inhalt steht sofort oben.
  // Die Einstellung "ausgeblendet" (breite Fenster) bleibt davon unberuehrt.
  var narrow = window.matchMedia('(max-width: 760px)'), scrim = null;
  function drawerMode() { return !!narrow && narrow.matches && document.body.classList.contains('notes-page') && !document.body.classList.contains('embed'); }
  function drawer(open) {
    if (!drawerMode() && open) return;
    document.body.classList.toggle('side-open', !!open);
    if (!scrim) {
      scrim = el('div', 'pg-scrim'); scrim.hidden = true; document.body.appendChild(scrim);
      scrim.addEventListener('click', function () { drawer(false); });
    }
    scrim.hidden = !open;
    var b = $('pg-side-btn');
    if (b && drawerMode()) { b.setAttribute('aria-expanded', open ? 'true' : 'false'); b.title = open ? T('Seitenleiste schließen') : T('Seiten anzeigen'); }
    // Suchfeld nur mit Maus/Tastatur fokussieren - auf Touch-Geraeten sprang sonst bei jedem Oeffnen die Tastatur auf
    if (open && finePointer.matches) { var f = $('pg-search'); if (f) setTimeout(function () { f.focus({ preventScroll: true }); }, 60); }
  }
  var finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
  narrow.addEventListener('change', function () { drawer(false); });
  if (document.body.classList.contains('notes-page') && !document.body.classList.contains('embed')) {
    setSide(HK.store('hk-pg-side') !== '0');
    if (drawerMode()) drawer(false);
    $('pg-side-btn').addEventListener('click', function () {
      if (drawerMode()) drawer(!document.body.classList.contains('side-open'));
      else setSide(document.body.classList.contains('side-hidden'));
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && document.body.classList.contains('side-open')) { drawer(false); $('pg-side-btn').focus(); return; }
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === '\\' || e.code === 'Backslash')) {
        e.preventDefault();
        if (drawerMode()) drawer(!document.body.classList.contains('side-open')); else setSide(document.body.classList.contains('side-hidden'));
      }
    });
    // Papierkorb/Archiv aus der Schublade: danach Inhalt zeigen
    ['pg-trash-btn', 'pg-archive-btn', 'pg-qs-btn'].forEach(function (id) { var x = $(id); if (x) x.addEventListener('click', function () { drawer(false); }); });
  }
  // 2.5 (G9): Schrift (Standard, Serif, Mono) und kleiner Text je Seite - wie "Volle Breite" auf dem Server gespeichert
  function applyFont(n) {
    var pg = $('page'); if (!pg) return;
    pg.classList.toggle('pf-serif', !!n && n.font === 'serif'); pg.classList.toggle('pf-mono', !!n && n.font === 'mono');
    pg.classList.toggle('p-small', !!n && !!n.small);
  }
  function fontKnown(c) { return !!c && Object.prototype.hasOwnProperty.call(c, 'font'); }     // aeltere Server liefern font nicht
  function setDesign(b) {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, b).then(function () {
      if (!S.cur || S.cur.id !== id) return;
      if ('font' in b) S.cur.font = b.font === 'default' ? null : b.font;
      if ('small' in b) S.cur.small = !!b.small;
      applyFont(S.cur);
    }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }
  function designMenu(m, c) {
    if (!fontKnown(c)) return;
    var cur = c.font === 'serif' || c.font === 'mono' ? c.font : 'default';
    var names = { 'default': T('Standard'), serif: T('Serif'), mono: T('Mono') };
    m.appendChild(HK.menuItem(T('Schrift'), null, { icon: 'Aa', hint: names[cur], sub: function (sm) {
      ['default', 'serif', 'mono'].forEach(function (f) {
        var it = HK.menuItem(names[f], function () { setDesign({ font: f }); }, { icon: f === cur ? '✓' : ' ', on: f === cur });
        it.classList.add('pf-item-' + f); sm.appendChild(it);
      });
    } }));
    m.appendChild(HK.menuItem(T('Kleiner Text'), function () { setDesign({ small: !c.small }); }, { icon: 'a', on: !!c.small }));
  }
  function setWide(on) {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { wide: on }).then(function () {
      if (S.cur && S.cur.id === id) { S.cur.wide = on; $('page').classList.toggle('wide', on); }
    }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }

  // --- Spalten: jede Spalte ist ein eigener Editor (Editor-in-Editor) ----------------
  function nestedTools() { var t = {}; Object.keys(S.tools || {}).forEach(function (k) { if (k !== 'columns') t[k] = S.tools[k]; }); return t; }
  // Editor.js kennt destroy() erst, wenn der Editor fertig ist - sonst danach abbauen (schneller Seitenwechsel)
  function killEditor(ed) {
    var k = function () { try { if (typeof ed.destroy === 'function') ed.destroy(); } catch (x) { /* schon weg */ } };
    if (typeof ed.destroy === 'function') k(); else if (ed.isReady) ed.isReady.then(k, function () {});
  }
  function nestedEditor(holder, blocks, placeholder) {
    var tools = nestedTools();
    var ed = new window.EditorJS({
      holder: holder, data: { blocks: blocks || [] }, tools: tools, tunes: BLOCK_TUNES, i18n: I18N, minHeight: 24, placeholder: placeholder,
      inlineToolbar: ['bold', 'italic', 'link', 'marker', 'inlineCode', 'hkdate'],
      onChange: edChanged
    });
    holder.addEventListener('input', function () { if (!S.loading) tryShortcut(ed); });   // Markdown-Kuerzel im eigenen Editor
    S.eds = (S.eds || []).filter(function (x) { return x.holder.isConnected; }); S.eds.push({ ed: ed, holder: holder, tools: tools });   // fuer Tastenkuerzel
    ed.isReady.then(function () { decorateLinks(); });                                       // Verweise als Marken
    return ed;
  }
  var COLS = new WeakMap();                      // Spalten-Element -> Werkzeug (fuer Zusammenfuehren)
  function ColumnsTool(o) {
    var d = o.data || {};
    this.cols = (d.cols && d.cols.length ? d.cols : [{ blocks: [] }, { blocks: [] }]).slice(0, 4);
    this.editors = [];
    this.api = o.api; this.block = o.block;
    this.ws = colWeights(this.cols);                // 2.5 (G1): relative Breiten (null = alle gleich)
  }
  // Gewichte nur, wenn jede Spalte eines hat (wie der Server), sonst null; Summe 1
  function colWeights(cols) {
    if (!cols || cols.length < 2) return null;
    var ok = cols.every(function (c) { return c && typeof c.w === 'number' && isFinite(c.w) && c.w > 0; });
    if (!ok) return null;
    var tot = cols.reduce(function (a, c) { return a + c.w; }, 0);
    return cols.map(function (c) { return c.w / tot; });
  }
  ColumnsTool.toolbox = { title: T('Spalten'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></svg>' };
  ColumnsTool.prototype.render = function () {
    var self = this, w = el('div', 'cols'); this.w = w; COLS.set(w, this);
    w.style.setProperty('--cols', String(this.cols.length));
    isolate(w);                    // Tastatur der Spalten-Editoren nicht an den aeusseren Editor weiterreichen
    this.cols.forEach(function (c, i) { self.editors[i] = self.column(c, i); });
    this.ctl(); this.grid();
    return w;
  };
  // Breiten als grid-template-columns (fr) und Ziehgriffe zwischen den Spalten (2.5, G1)
  ColumnsTool.prototype.grid = function () {
    var self = this, w = this.w, n = this.editors.length;
    if (this.ws && this.ws.length !== n) this.ws = null;
    if (this.ws) w.style.setProperty('--cols-t', frs(this.ws)); else w.style.removeProperty('--cols-t');
    w.classList.toggle('cols-w', !!this.ws);
    [].forEach.call(w.querySelectorAll(':scope > .col-rs'), function (x) { x.remove(); });
    if (roRole()) return;
    for (var i = 0; i < n - 1; i++) (function (i) {
      var h = el('div', 'col-rs'); h.contentEditable = 'false'; h.dataset.mutationFree = 'true'; h.setAttribute('aria-hidden', 'true');
      h.title = T('Ziehen ändert die Spaltenbreite, Doppelklick setzt beide gleich');
      w.appendChild(h); self.placeRs(h, i);
      h.addEventListener('mousedown', function (e) { e.stopPropagation(); });
      h.addEventListener('dblclick', function (e) {
        e.preventDefault(); e.stopPropagation(); if (readOnly()) return;
        var ws = self.ws ? self.ws.slice() : self.equal(), sum = ws[i] + ws[i + 1];
        ws[i] = ws[i + 1] = sum / 2; self.setWs(ws, true);
      });
      h.addEventListener('pointerdown', function (e) {
        if (readOnly() || e.button !== 0) return;
        e.preventDefault(); e.stopPropagation();
        var box = w.getBoundingClientRect(), start = self.ws ? self.ws.slice() : self.equal(), before = JSON.stringify(self.ws);
        var left = start.slice(0, i).reduce(function (a, x) { return a + x; }, 0), pair = start[i] + start[i + 1];
        var gap = parseFloat(getComputedStyle(w).columnGap) || 0, usable = box.width - gap * (n - 1);
        if (usable <= 0) return;
        try { h.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
        document.body.classList.add('hk-resizing'); h.classList.add('on');
        function mv(ev) {
          var x = (ev.clientX - box.left - gap * (i + 0.5)) / usable - left, a = Math.round((left + x) * 20) / 20 - left;   // Einrasten: 5-%-Schritte
          a = Math.min(pair - 0.15, Math.max(0.15, a));
          var ws = start.slice(); ws[i] = a; ws[i + 1] = pair - a; self.setWs(ws, false);
        }
        function up() {
          h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', up); h.removeEventListener('pointercancel', up);
          document.body.classList.remove('hk-resizing'); h.classList.remove('on');
          if (JSON.stringify(self.ws) !== before) self.changed();
        }
        h.addEventListener('pointermove', mv); h.addEventListener('pointerup', up); h.addEventListener('pointercancel', up);
      });
    })(i);
  };
  function frs(ws) { return ws.map(function (x) { return 'minmax(0,' + (Math.round(x * 1000) / 1000) + 'fr)'; }).join(' '); }
  ColumnsTool.prototype.equal = function () { var n = this.editors.length, a = []; for (var i = 0; i < n; i++) a.push(1 / n); return a; };
  ColumnsTool.prototype.placeRs = function (h, i) {
    var ws = this.ws || this.equal(), x = ws.slice(0, i + 1).reduce(function (a, v) { return a + v; }, 0);
    h.style.setProperty('--rs-f', String(Math.round(x * 10000) / 10000)); h.style.setProperty('--rs-i', String(i));   // CSS: Abstand mitrechnen
  };
  ColumnsTool.prototype.setWs = function (ws, save) {
    var eq = ws.every(function (x) { return Math.abs(x - 1 / ws.length) < 0.004; });
    this.ws = eq ? null : ws.map(function (x) { return Math.round(x * 100) / 100; });
    var self = this, w = this.w;
    if (this.ws) w.style.setProperty('--cols-t', frs(this.ws)); else w.style.removeProperty('--cols-t');
    w.classList.toggle('cols-w', !!this.ws);
    [].forEach.call(w.querySelectorAll(':scope > .col-rs'), function (h, i) { self.placeRs(h, i); });
    if (save) this.changed();
  };
  ColumnsTool.prototype.changed = function () {
    if (this.block && this.block.dispatchChange) this.block.dispatchChange();
    if (!S.loading) { S.dirtyBody = true; schedule(); }
  };
  // Sichtbare Leiste "Spalten: 2 3 4" oben rechts (das Block-Menue zeigt beim Ueberfahren die INNEREN Bloecke)
  ColumnsTool.prototype.ctl = function () {
    var self = this, bar = el('div', 'cols-ctl'); bar.contentEditable = 'false';
    bar.appendChild(el('span', 'cols-ctl-t', T('Spalten')));
    [2, 3, 4].forEach(function (n) {
      var b = el('button', 'cols-ctl-b' + (self.editors.length === n ? ' on' : ''), String(n)); b.type = 'button';
      b.setAttribute('aria-label', T('{n} Spalten', { n: n })); b.setAttribute('aria-pressed', self.editors.length === n ? 'true' : 'false');
      b.addEventListener('click', function () { self.setCount(n); });
      bar.appendChild(b);
    });
    // Wie Notion: Bloecke unter den Spalten in eine Spalte holen; gestapelte Spalten-Bloecke zusammenfuehren
    var extra = el('span', 'cols-ctl-x'); bar.appendChild(extra);
    bar.addEventListener('mouseenter', function () { self.ctlExtra(extra); });
    bar.addEventListener('focusin', function () { self.ctlExtra(extra); });
    this.w.appendChild(bar);
  };
  ColumnsTool.prototype.index = function () {      // Position dieses Spalten-Blocks im aeusseren Editor
    for (var i = 0, n = this.api.blocks.getBlocksCount(); i < n; i++) {
      var b = this.api.blocks.getBlockByIndex(i); if (b && b.holder && b.holder.contains(this.w)) return i;
    }
    return -1;
  };
  ColumnsTool.prototype.ctlExtra = function (box) {
    var self = this, i = this.index(); box.textContent = '';
    var next = i >= 0 ? this.api.blocks.getBlockByIndex(i + 1) : null; if (!next) return;
    box.appendChild(el('span', 'cols-ctl-sep', '|'));
    if (next.name === 'columns') {
      var m = el('button', 'cols-ctl-b wide', '⤓ ' + T('Zusammenführen')); m.type = 'button';
      m.title = T('Spalten darunter mit diesen zusammenführen (Spalte 1 + 1, 2 + 2 …)');
      m.addEventListener('click', function () { self.mergeBelow(i, next); });
      box.appendChild(m);
    } else {
      box.appendChild(el('span', 'cols-ctl-t', T('↑ Block darunter →')));
      this.editors.forEach(function (_, ci) {
        var b = el('button', 'cols-ctl-b', String(ci + 1)); b.type = 'button'; b.title = T('Block unter den Spalten ans Ende von Spalte {n}', { n: ci + 1 });
        b.addEventListener('click', function () { self.takeBelow(i, next, ci); });
        box.appendChild(b);
      });
    }
  };
  ColumnsTool.prototype.rebuild = function (cols) {
    var self = this;
    this.editors.forEach(function (ed) { try { ed.destroy(); } catch (x) { /* schon weg */ } });
    this.editors = []; this.cols = cols; this.w.textContent = ''; this.w.style.setProperty('--cols', String(cols.length));
    cols.forEach(function (c, i) { self.editors[i] = self.column(c, i); });
    this.ws = null; this.ctl(); this.grid(); S.dirtyBody = true; schedule();
  };
  // Eine Spalte: eigener Editor + "+ Block" darunter (in GENAU dieser Spalte weiterschreiben, wie Notion)
  ColumnsTool.prototype.column = function (c, i) {
    var col = el('div', 'col'), h = el('div', 'col-ed'), add = el('button', 'col-add', T('+ Block'));
    add.type = 'button'; add.title = T('Neuer Block am Ende dieser Spalte');
    col.appendChild(h); col.appendChild(add); this.w.appendChild(col);
    var ed = nestedEditor(h, c.blocks, T("Spalte – '/' für Blöcke"));
    add.addEventListener('click', function () {
      ed.isReady.then(function () {
        var n = ed.blocks.getBlocksCount(), last = n ? ed.blocks.getBlockByIndex(n - 1) : null;
        if (last && last.name === 'paragraph' && !last.holder.textContent.trim()) { ed.caret.setToBlock(n - 1, 'end'); return; }   // leerer Absatz da: dorthin
        ed.blocks.insert('paragraph', {}, {}, n, true);
        ed.caret.setToBlock(n, 'start');
      });
    });
    return ed;
  };
  ColumnsTool.prototype.mergeBelow = function (i, next) {
    var self = this, other = COLS.get(next.holder.querySelector('.cols'));
    if (!other) return;
    Promise.all([this.save(), other.save()]).then(function (r) {
      var a = r[0].cols, b = r[1].cols, n = Math.min(4, Math.max(a.length, b.length)), cols = [];
      for (var k = 0; k < n; k++) cols.push({ blocks: ((a[k] || {}).blocks || []).concat((b[k] || {}).blocks || []) });
      self.api.blocks.delete(i + 1);
      self.rebuild(cols);
      HK.toast(T('Spalten zusammengeführt'));
    });
  };
  ColumnsTool.prototype.takeBelow = function (i, next, ci) {
    var self = this;
    Promise.all([next.save(), this.save()]).then(function (r) {
      var saved = r[0]; if (!saved || !saved.tool) return;
      var cols = r[1].cols;
      cols[ci].blocks = (cols[ci].blocks || []).concat([{ type: saved.tool, data: saved.data }]);
      self.api.blocks.delete(i + 1);
      self.rebuild(cols);
    });
  };
  ColumnsTool.prototype.destroy = function () { this.editors.forEach(killEditor); this.editors = []; };
  ColumnsTool.prototype.setCount = function (n) {
    var self = this;
    if (n === this.editors.length) return;
    this.save().then(function (d) {
      var cols = d.cols;
      if (n < cols.length) {                      // Inhalt entfernter Spalten in die letzte verbleibende Spalte
        var keep = cols.slice(0, n);
        cols.slice(n).forEach(function (c) { keep[n - 1].blocks = keep[n - 1].blocks.concat(c.blocks); });
        cols = keep;
      }
      while (cols.length < n) cols.push({ blocks: [] });
      self.editors.forEach(function (ed) { try { ed.destroy(); } catch (x) { /* schon weg */ } });
      self.editors = []; self.cols = cols; self.w.textContent = ''; self.w.style.setProperty('--cols', String(n));
      cols.forEach(function (c, i) { self.editors[i] = self.column(c, i); });
      self.ws = null; self.ctl(); self.grid(); S.dirtyBody = true; schedule();     // andere Anzahl: wieder gleich breit
    });
  };
  ColumnsTool.prototype.save = function () {
    var self = this;
    return Promise.all(this.editors.map(function (e) { return e.isReady.then(function () { return e.save(); }); }))
      .then(function (outs) {
        var ws = self.ws && self.ws.length === outs.length ? self.ws : null;
        return { cols: outs.map(function (o, i) {
          var c = Object.assign({}, self.cols[i] && typeof self.cols[i] === 'object' ? self.cols[i] : {}, { blocks: o.blocks });   // fremde Felder bleiben
          if (ws) c.w = ws[i]; else delete c.w;
          return c;
        }) };
      });
  };

  // --- Seitenbaum: Seiten per Ziehen verschieben (wie in Notion) -----------------------
  // oberes Viertel = davor, unteres Viertel = danach, Mitte = als Unterseite hinein
  var dragPage = null;
  function dropZone(row, p, e) {
    if (!dragPage || dragPage === p.id || descendants(dragPage).indexOf(p.id) >= 0) return null;
    var r = row.getBoundingClientRect(), y = (e.clientY - r.top) / r.height;
    if (p.kind === 'database') return y < 0.5 ? 'before' : 'after';     // nicht in eine Datenbank hinein
    return y < 0.28 ? 'before' : y > 0.72 ? 'after' : 'inside';
  }
  function clearDrop() { [].forEach.call(document.querySelectorAll('.drop-before, .drop-after, .drop-inside, .drop-root'), function (x) { x.classList.remove('drop-before', 'drop-after', 'drop-inside', 'drop-root'); }); }
  // "Verschieben nach" im Seitenmenue: Baum und Pfad sofort umstellen (der Server antwortet erst nach ~1 s), danach
  // Rueckmeldung mit dem Ziel (BUG-096)
  function moveFromMenu(id, parentId) {
    var p = byId(id), idx = children(parentId).length, prev = p ? p.parent_id : null;
    if (S.cur && S.cur.id === id) S.cur.parent_id = parentId;
    if (p) { p.parent_id = parentId; dropIdx(); if (parentId) { S.open[parentId] = true; saveOpen(); } renderTree(); renderCrumbs(); }
    return api('POST', '/api/notes/' + id + '/move', { parent_id: parentId, index: idx })
      .then(function () {
        HK.toast(parentId ? T('Verschoben nach „{name}“', { name: label(byId(parentId) || {}) }) : T('Auf die oberste Ebene verschoben'));
        return loadList();
      })
      .catch(function (err) { if (p) { p.parent_id = prev; dropIdx(); renderTree(); renderCrumbs(); } HK.toast(T('Nicht verschoben: {err}', { err: err.message })); });
  }
  function movePageTo(id, parentId, index) {
    return api('POST', '/api/notes/' + id + '/move', { parent_id: parentId, index: index })
      .then(function () { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList(); })
      .catch(function (err) { HK.toast(T('Nicht verschoben: {err}', { err: err.message })); });
  }
  function treeDnd(row, p) {
    row.draggable = true;
    row.addEventListener('dragstart', function (e) {
      dragPage = p.id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', label(p)); row.classList.add('dragging');
    });
    row.addEventListener('dragend', function () { dragPage = null; row.classList.remove('dragging'); clearDrop(); });
    row.addEventListener('dragover', function (e) {
      var z = dropZone(row, p, e); if (!z) return;
      e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = 'move';
      clearDrop(); row.classList.add('drop-' + z);
    });
    row.addEventListener('dragleave', function (e) { if (!row.contains(e.relatedTarget)) row.classList.remove('drop-before', 'drop-after', 'drop-inside'); });
    row.addEventListener('drop', function (e) {
      var z = dropZone(row, p, e), id = dragPage; clearDrop(); if (!z) return;
      e.preventDefault(); e.stopPropagation();
      if (z === 'inside') return movePageTo(id, p.id, children(p.id).length);
      var parentId = p.parent_id && byId(p.parent_id) ? p.parent_id : null;
      var sib = children(parentId).filter(function (x) { return x.id !== id; }).map(function (x) { return x.id; });
      movePageTo(id, parentId, sib.indexOf(p.id) + (z === 'after' ? 1 : 0));
    });
  }
  function treeRootDnd(ul) {           // auf freie Flaeche unter dem Baum ziehen = oberste Ebene, ans Ende
    if (ul.dataset.dnd) return; ul.dataset.dnd = '1';
    ul.addEventListener('dragover', function (e) { if (!dragPage || e.target !== ul) return; e.preventDefault(); clearDrop(); ul.classList.add('drop-root'); });
    ul.addEventListener('dragleave', function (e) { if (e.target === ul) ul.classList.remove('drop-root'); });
    ul.addEventListener('drop', function (e) {
      if (!dragPage || e.target !== ul) return; e.preventDefault(); var id = dragPage; clearDrop();
      movePageTo(id, null, children(null).length);
    });
  }

  // --- Seite duplizieren (Kopie direkt unter dem Original) -----------------------------
  function duplicatePage() {
    var src = S.cur, parentId = src.parent_id && byId(src.parent_id) ? src.parent_id : null;
    save().then(function () { return api('POST', '/api/notes/' + src.id + '/duplicate', { parent_id: parentId, title: T('{name} (Kopie)', { name: label(src) }) }); })
      .then(function (d) {
        var sib = children(parentId).map(function (x) { return x.id; });
        return api('POST', '/api/notes/' + d.id + '/move', { parent_id: parentId, index: sib.indexOf(src.id) + 1 })
          .then(function () { return loadList(); }).then(function () { return openPage(d.id); });
      })
      .then(function () { HK.toast(src.kind === 'database' ? T('Datenbank dupliziert (Spalten und Ansichten, ohne Einträge)') : T('Seite dupliziert')); })
      .catch(function (err) { HK.toast(T('Nicht dupliziert: {err}', { err: err.message })); });
  }

  // --- Peek: die ECHTE Seite als eingebetteter Rahmen (/notes?embed=1#id) - zeigt alles, was eine Seite kann
  var EMBED = document.body.classList.contains('embed');
  var PK = { id: null, ctx: null };
  function peekModeUI() {
    var c = PK.ctx.mode === 'center';
    if (!c) PK.sideMode = PK.ctx.mode;             // merken, ob rechts oder links (fuer das Umschalten)
    $('pg-peek').classList.toggle('center', c); $('pg-peek').classList.toggle('left', PK.ctx.mode === 'left');
    $('peek-mode').textContent = c ? '◨' : '▣'; $('peek-mode').title = c ? T('Als Seitenansicht (rechts) anzeigen') : T('Mittig anzeigen');
  }
  function openPeek(id, ctx) {
    PK.id = id; PK.ctx = ctx || PK.ctx;
    $('pg-peek').hidden = false; peekModeUI();
    var db = byId(PK.ctx.dbId); $('peek-crumb').textContent = db ? (ti(db.icon) ? ti(db.icon) + ' ' : '') + label(db) : '';
    var i = PK.ctx.ids.indexOf(id); $('peek-prev').disabled = i <= 0; $('peek-next').disabled = i < 0 || i >= PK.ctx.ids.length - 1;
    var f = $('peek-frame');
    if (f.dataset.loaded) { try { f.contentWindow.location.hash = '#' + id; return Promise.resolve(); } catch (x) { /* neu laden */ } }
    f.dataset.loaded = '1'; f.src = '/notes?embed=1#' + id;
    return Promise.resolve();
  }
  function closePeek() {
    if (!PK.id) return Promise.resolve();
    var ctx = PK.ctx, f = $('peek-frame'), flush = Promise.resolve();
    try { var w = f.contentWindow; if (w && w.HKPages && w.HKPages.flush) flush = w.HKPages.flush(); } catch (x) { /* Rahmen noch nicht geladen */ }
    return flush.then(function () {}, function (e) {            // Konflikt im Rahmen: offen lassen, damit nichts verloren geht
      if (e && e.message === 'conflict') { HK.toast(T('Speicher-Konflikt im Eintrag – bitte erst dort klären.')); throw e; }
    }).then(function () {      // ausstehendes Speichern im Rahmen abwarten
      PK.id = null; $('pg-peek').hidden = true;
      if (ctx && ctx.refresh) ctx.refresh();
    });
  }
  function peekStep(d) { var ids = PK.ctx.ids, i = ids.indexOf(PK.id) + d; if (i >= 0 && i < ids.length) openPeek(ids[i], PK.ctx); }
  $('peek-close').addEventListener('click', function () { closePeek(); });
  $('peek-prev').addEventListener('click', function () { peekStep(-1); });
  $('peek-next').addEventListener('click', function () { peekStep(1); });
  $('peek-full').addEventListener('click', function () { var id = PK.id; closePeek().then(function () { openPage(id); }); });
  $('peek-mode').addEventListener('click', function () {
    PK.ctx.mode = PK.ctx.mode === 'center' ? (PK.sideMode || 'side') : 'center'; if (PK.ctx.setMode) PK.ctx.setMode(PK.ctx.mode); peekModeUI();
  });
  document.addEventListener('keydown', function (e) {           // naechster/vorheriger Eintrag wie Notion
    if (!PK.id || !e.ctrlKey || !e.shiftKey || e.metaKey || e.altKey || (e.code !== 'KeyJ' && e.code !== 'KeyK')) return;
    e.preventDefault(); peekStep(e.code === 'KeyJ' ? 1 : -1);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !PK.id || e.defaultPrevented) return;
    if (!$('pg-modal').hidden || !$('pg-picker').hidden || !$('pg-qs').hidden) return;
    closePeek();
  });
  document.addEventListener('mousedown', function (e) {        // Klicks IM Rahmen kommen hier gar nicht an
    if (!PK.id) return;
    var tg = e.target;
    if ($('pg-peek').querySelector('.peek-panel').contains(tg)) return;
    if (tg.closest && tg.closest('.menu, .modal, .picker, .qs, .toast')) return;
    closePeek();
  });
  window.addEventListener('message', function (e) {             // Esc im Rahmen -> schliessen
    if (e.origin !== location.origin || !PK.id || e.source !== $('peek-frame').contentWindow) return;
    if (e.data && e.data.hk === 'peek-close') closePeek();
    if (e.data && e.data.hk === 'peek-step') peekStep(e.data.d > 0 ? 1 : -1);
    if (e.data && e.data.hk === 'peek-deleted') {                // Eintrag im Peek geloescht: Peek zu, Ansicht auffrischen, Rueckgaengig hier anbieten
      var ctx = PK.ctx, id = e.data.id;
      PK.id = null; $('pg-peek').hidden = true;
      if (ctx && ctx.refresh) ctx.refresh();
      loadList();
      HK.toast(T('„{name}“ in den Papierkorb verschoben', { name: e.data.name || T('Eintrag') }), T('Rückgängig'), function () {
        api('POST', '/api/trash/' + id + '/restore').then(loadList).then(function () { if (ctx && ctx.refresh) ctx.refresh(); })
          .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
      });
    }
  });
  if (EMBED) {                                                  // ICH bin der Rahmen: Esc an das Peek-Fenster melden
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (!$('pg-modal').hidden || !$('pg-picker').hidden || !$('pg-qs').hidden || document.querySelector('.ce-popover--opened')) return;
      window.parent.postMessage({ hk: 'peek-close' }, location.origin);
    });
    document.addEventListener('keydown', function (e) {        // Ctrl+Shift+J/K im Rahmen -> im Peek blaettern
      if (!e.ctrlKey || !e.shiftKey || e.metaKey || e.altKey || (e.code !== 'KeyJ' && e.code !== 'KeyK')) return;
      e.preventDefault(); window.parent.postMessage({ hk: 'peek-step', d: e.code === 'KeyJ' ? 1 : -1 }, location.origin);
    });
  }

  // --- Archiv: Seiten (mit Unterseiten) aus Baum und Suche nehmen, ohne sie zu loeschen -----------
  function anyById(id) { return (S.all || S.pages).find(function (p) { return p.id === id; }) || S.shared.find(function (p) { return p.id === id; }) || null; }
  function archiveBar() {
    var bar = $('pg-archived'); if (bar) bar.remove();
    if (!S.cur || !S.cur.archived) return;
    bar = el('div', 'pg-archived'); bar.id = 'pg-archived'; bar.setAttribute('role', 'status');
    bar.appendChild(el('span', null, T('📦 Diese Seite ist archiviert – sie fehlt im Seitenbaum und in der Suche.')));
    var b = el('button', 'btn', T('Zurückholen')); b.type = 'button'; b.addEventListener('click', function () { setArchived(S.cur.id, false); });
    bar.appendChild(b); $('page').insertBefore(bar, $('page').firstChild);
  }
  function setArchived(id, on) {
    return save().then(function () { return api('POST', '/api/notes/' + id + '/archive', { archived: on }); }).then(function () {
      if (S.cur && S.cur.id === id) S.cur.archived = on;
      return loadList().then(function () {
        archiveBar();
        HK.toast(on ? T('„{name}“ archiviert', { name: label(anyById(id)) }) : T('„{name}“ ist wieder da', { name: label(anyById(id)) }), on ? T('Rückgängig') : null,
                 on ? function () { setArchived(id, false); } : null);
      });
    }).catch(function (err) { HK.toast(T('Nicht geändert: {err}', { err: err.message })); });
  }
  // --- Seite sperren: Schutz vor versehentlichem Bearbeiten (Datenbanken/Aufgaben/Kalender darin bleiben bedienbar)
  function setLocked(on) {
    if (!S.cur) return; var id = S.cur.id;
    (on ? save() : Promise.resolve()).then(function () { return api('PATCH', '/api/notes/' + id, { locked: on }); }).then(function () {
      if (S.cur && S.cur.id === id) { S.cur.locked = on; applyLock(); }
      var p = byId(id); if (p) p.locked = on;
      HK.toast(on ? T('🔒 Seite gesperrt') : T('🔓 Seite entsperrt'));
    }).catch(function (err) { HK.toast(T('Nicht geändert: {err}', { err: err.message })); });
  }
  // --- Veroeffentlichen: Dialog aus dem Seitenmenue, Hinweis "Oeffentlich" im Seitenkopf ------------------------
  // Erst der oeffentliche Name (einmal je Konto, vorgeschlagen und geprueft), dann die Einstellungen der Seite.
  function applyPublic() {
    var c = S.cur, on = !!(c && c.published), via = c && !on ? c.published_via : null;
    var b = $('pg-public'), go = $('pg-public-go');
    if (!b) {
      b = el('button', 'pg-lock-b pg-pub-b'); b.id = 'pg-public'; b.type = 'button';
      b.addEventListener('click', openPublish);
      go = el('a', 'pg-lock-b pg-pub-b pg-pub-go', '↗'); go.id = 'pg-public-go'; go.target = '_blank'; go.rel = 'noopener';
      go.title = T('Öffentliche Seite öffnen'); go.setAttribute('aria-label', T('Öffentliche Seite öffnen'));
      $('pg-state').parentNode.insertBefore(b, $('pg-state'));
      $('pg-state').parentNode.insertBefore(go, $('pg-state'));
    }
    var t = via ? String(via.title || '') : '';
    if (t.length > 28) t = t.slice(0, 27) + '…';
    b.hidden = go.hidden = !(on || via);
    // Symbol + Text getrennt: auf schmalen Bildschirmen bleibt nur "🌐" (sonst schiebt der Hinweis die Kopfzeile
    // ueber den Rand); der volle Text steht dann in aria-label/title
    var full = via ? T('🌐 Öffentlich (über „{title}“)', { title: t }) : T('🌐 Öffentlich'), mm = /^(\S+)\s+([\s\S]+)$/.exec(full);
    b.textContent = '';
    if (mm) { b.appendChild(el('span', 'pg-pub-ic', mm[1])); b.appendChild(el('span', 'pg-pub-l', ' ' + mm[2])); b.setAttribute('aria-label', mm[2]); }
    else { b.textContent = full; b.removeAttribute('aria-label'); }
    b.title = via ? T('Öffentlich als Unterseite von „{title}“ – klicken für Einzelheiten', { title: via.title }) : T('Diese Seite ist öffentlich – klicken für Einstellungen');
    var href = on ? c.public_url : via ? via.url : '';
    if (href) go.href = href; else go.removeAttribute('href');
    go.hidden = go.hidden || !href;
  }
  function pubStatus(box, txt, cls) { box.textContent = txt; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }
  // Feld mit Beschriftung; extra = Knoten unter dem Feld (Fehler/Hinweis direkt am Feld, nicht in einer Statuszeile)
  function pubField(label, node, extra) {
    var l = el('label', 'hk-field'), col = el('div', 'pub-fcol');
    l.appendChild(el('span', null, label)); col.appendChild(node);
    (extra || []).forEach(function (x) { col.appendChild(x); });
    l.appendChild(col); return l;
  }
  // Schalter statt Haekchen: Text links, Schalter rechts (role=switch)
  function pubCheck(label, on, hint) {
    var l = el('label', 'pub-opt'), cb = el('input', 'hk-switch'); cb.type = 'checkbox'; cb.checked = !!on; cb.setAttribute('role', 'switch');
    var tx = el('span', 'pub-opt-tx'); tx.appendChild(el('span', 'pub-opt-t', label)); if (hint) l.hint = tx.appendChild(el('small', 'hk-hint', hint));
    l.appendChild(tx); l.appendChild(cb); l.cb = cb; return l;
  }
  function pubCopy(u) {
    (navigator.clipboard ? navigator.clipboard.writeText(u) : Promise.reject(new Error('x')))
      .then(function () { HK.toast(T('Link kopiert')); }, function () { HK.toast(u); });
  }
  // Teilen nur dort, wo es ueblich ist (Handy/Tablet mit Teilen-Menue des Systems)
  function pubCanShare() { return !!navigator.share && !!window.matchMedia && matchMedia('(pointer: coarse)').matches; }
  function pubBtn(label, fn, cls) { var b = el('button', 'btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; b.addEventListener('click', fn); return b; }
  function pubLink(u, cls) { var a = HK.urlNode(u, 'a', cls || 'pub-link-a'); a.href = u; a.target = '_blank'; a.rel = 'noopener'; return a; }
  function openPublish() {
    if (!S.cur) return;
    var id = S.cur.id;
    api('GET', '/api/notes/' + id + '/publish').then(function (st) {
      openModal(T('Veröffentlichen'), function (body) { (st.name ? pubMain : pubName)(body, id, st); }, true);
    }).catch(function (err) { HK.toast(T('Nicht geladen: {err}', { err: err.message })); });
  }
  // Schritt 1 (einmal je Konto): oeffentlicher Name. Ein Fehler ersetzt den Hilfetext unter dem Feld.
  function pubName(body, id, st) {
    body.textContent = '';
    var box = el('div', 'pub-dlg'); body.appendChild(box);
    box.appendChild(el('p', null, T('Wähle zuerst deinen öffentlichen Namen. Er steht in jeder Adresse deiner öffentlichen Seiten und ist für alle sichtbar.')));
    var inp = el('input', 'hk-in'); inp.type = 'text'; inp.maxLength = 32; inp.value = st.suggested_name || '';
    inp.autocomplete = 'off'; inp.spellcheck = false; inp.setAttribute('autocapitalize', 'off');
    var row = el('div', 'pub-namerow'), prev = el('span', 'pub-url'), msg = el('span', 'hk-status'), fm = el('p', 'hk-fieldmsg');
    row.appendChild(prev); row.appendChild(msg);
    box.appendChild(pubField(T('Öffentlicher Name'), inp, [row, fm]));
    var setErr = HK.fieldMsg(inp, fm, T('Nicht dein Anmeldename: wähle etwas, das alle sehen dürfen. 2–32 Zeichen, a–z, 0–9, - und _.'));
    var bar = el('div', 'hk-actions'), go = el('button', 'btn btn-primary', T('Weiter')); go.type = 'button'; bar.appendChild(go); box.appendChild(bar);
    var tmr = null, base = location.origin + '/@';
    function check() {
      var v = inp.value.trim().toLowerCase();
      prev.textContent = ''; prev.appendChild(HK.urlNode(base + (v || '…')));
      clearTimeout(tmr); go.disabled = true; pubStatus(msg, ''); setErr('');
      if (!v) return;
      tmr = setTimeout(function () {
        api('PUT', '/api/account/public-name', { name: v, check: true }).then(function (r) {
          if (inp.value.trim().toLowerCase() !== v) return;
          if (r.ok) pubStatus(msg, T('✓ Frei'), 'ok'); else setErr(r.error);
          go.disabled = !r.ok;
        }).catch(function (err) { setErr(err.message); });
      }, 250);
    }
    inp.addEventListener('input', check);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !go.disabled) { e.preventDefault(); go.click(); } });
    go.addEventListener('click', function () {
      go.disabled = true;
      api('PUT', '/api/account/public-name', { name: inp.value.trim().toLowerCase() }).then(function () {
        return api('GET', '/api/notes/' + id + '/publish');
      }).then(function (ns) { pubMain(body, id, ns); })
        .catch(function (err) { pubStatus(msg, ''); setErr(err.message); go.disabled = false; });
    });
    check(); setTimeout(function () { inp.focus(); inp.select(); }, 0);
  }
  // Schritt 2: 1 Schalter -> 2 Link-Karte -> 3 Adresse -> 4 Optionen -> 5 "Veroeffentlichung beenden"
  function pubMain(body, id, st) {
    body.textContent = '';
    var box = el('div', 'pub-dlg'); body.appendChild(box);
    var url = '/api/notes/' + id + '/publish';
    if (!st.enabled) box.appendChild(el('p', 'pub-warn', T('Der Admin hat das Veröffentlichen auf diesem Server abgeschaltet – öffentliche Adressen liefern zurzeit „Nicht gefunden“.')));
    // Oeffentlich ueber eine Elternseite ("Unterseiten mit veroeffentlichen"): erklaeren und dorthin verweisen
    var via = el('div', 'pub-via'); box.appendChild(via);
    var gone = el('p', 'pub-note'); box.appendChild(gone);
    // 1 Schalter
    var on = pubCheck(T('Öffentlich'), st.published, T('Jeder mit dem Link kann diese Seite ohne Anmeldung lesen.'));
    on.classList.add('pub-main-sw'); box.appendChild(on);
    // 2 Link-Karte (solange privat: kuenftige Adresse, gedaempft und ohne Link)
    var card = el('div', 'pub-link'); box.appendChild(card);
    // 3 Adresse: Aenderung nur ueber den Knopf, der erst erscheint, wenn die Eingabe abweicht
    var slug = el('input', 'hk-in'); slug.type = 'text'; slug.maxLength = 60; slug.value = st.slug || '';
    slug.autocomplete = 'off'; slug.spellcheck = false; slug.setAttribute('autocapitalize', 'off');
    var sbtn = el('button', 'btn btn-primary', T('Adresse ändern')); sbtn.type = 'button';
    var srow = el('div', 'pub-slug'); srow.appendChild(slug); srow.appendChild(sbtn);
    var sfm = el('p', 'hk-fieldmsg');
    var adr = pubField(T('Adresse'), srow, [sfm]); box.appendChild(adr);
    var setSlugErr = HK.fieldMsg(slug, sfm, '');
    // 4 Optionen (solange privat mit Ueberschrift: gelten erst beim Veroeffentlichen)
    var opts = el('div', 'pub-opts'), oh = el('div', 'pub-opts-h', T('Gilt, sobald die Seite öffentlich ist')); opts.appendChild(oh);
    var ch = pubCheck(T('Unterseiten mit veröffentlichen'), st.children, T('Unterseiten (und Einträge von Datenbanken) dieser Seite sind dann ebenfalls öffentlich.'));
    var ix = pubCheck(T('Suchmaschinen erlauben'), st.indexable, T('Sonst bitten wir Suchmaschinen, die Seite nicht aufzunehmen.'));
    var pr = pubCheck(T('Als Profilseite (/@name)'), st.profile, T('Erscheint unter deiner Profiladresse – höchstens eine Seite.'));
    // Geteilt mit "Bearbeiten": neue Unterseiten/Bilder dieser Personen sind sofort oeffentlich
    var chw = el('p', 'pub-note pub-warn pub-edit-warn', T('Auch Unterseiten und Bilder, die Personen mit ‚Bearbeiten‘ anlegen, werden sofort öffentlich.'));
    function drawChw() { chw.hidden = !(st.shared_edit && ch.cb.checked); }
    ch.cb.addEventListener('change', drawChw); drawChw();
    [ch, chw, ix, pr].forEach(function (x) { opts.appendChild(x); }); box.appendChild(opts);
    var msg = el('div', 'hk-status'); msg.setAttribute('role', 'status'); box.appendChild(msg);
    box.appendChild(el('p', 'hk-hint', T('Öffentlicher Name: @{name} – ändern unter Einstellungen → Konto.', { name: st.name })));
    // 5 Beenden
    var end = el('button', 'btn btn-danger', T('Veröffentlichung beenden')); end.type = 'button';
    var ebar = el('div', 'hk-actions pub-end'); ebar.appendChild(end); box.appendChild(ebar);
    function newSlug() { return slug.value.trim().toLowerCase(); }
    function drawCard() {
      card.textContent = '';
      var pub = !!st.published;
      card.classList.toggle('is-future', !pub);
      if (!pub) {
        var fu = HK.urlNode((st.profile_url || location.origin + '/@' + st.name) + '/' + (newSlug() || '…'), 'span', 'pub-link-a pub-link-future');
        fu.setAttribute('aria-disabled', 'true'); card.appendChild(fu);
        return;
      }
      card.appendChild(pubLink(st.url));
      var lbar = el('div', 'hk-actions');
      lbar.appendChild(pubBtn(T('Link kopieren'), function () { pubCopy(st.url); }, 'pub-copy'));
      var op = el('a', 'btn', T('Öffnen')); op.href = st.url; op.target = '_blank'; op.rel = 'noopener'; lbar.appendChild(op);
      if (pubCanShare()) {
        lbar.appendChild(pubBtn(T('Link senden …'), function () {
          navigator.share({ url: st.url, title: (S.cur && S.cur.id === id && S.cur.title) || '' }).catch(function () { /* abgebrochen */ });
        }));
      }
      card.appendChild(lbar);
      if (st.profile && st.profile_url) {                    // Profilseite: zweite Zeile mit der Profiladresse
        var prow = el('div', 'pub-prof');
        prow.appendChild(el('span', 'pub-prof-l', T('Profiladresse') + ':'));
        prow.appendChild(pubLink(st.profile_url));
        prow.appendChild(pubBtn(T('Kopieren'), function () { pubCopy(st.profile_url); }, 'pub-copy-prof'));
        card.appendChild(prow);
      }
    }
    function draw() {
      var v = st.published_via, pub = !!st.published, viaOnly = !!v && !pub;
      on.cb.checked = pub; on.cb.disabled = !st.enabled && !pub;
      via.textContent = ''; via.hidden = !viaOnly;
      if (viaOnly) {
        via.appendChild(el('p', null, T('Diese Seite ist bereits öffentlich: „{title}“ ist mit Unterseiten veröffentlicht.', { title: v.title })));
        via.appendChild(pubLink(v.url));
        var vb = el('div', 'hk-actions');
        vb.appendChild(pubBtn(T('Link kopieren'), function () { pubCopy(v.url); }));
        vb.appendChild(pubBtn(T('„{title}“ öffnen', { title: v.title }), function () { closeModal(); openPage(v.note_id); }));
        via.appendChild(vb);
        via.appendChild(el('p', 'hk-hint', T('Privat wird sie erst, wenn dort „Unterseiten mit veröffentlichen“ aus ist oder die Seite verschoben wird.')));
      }
      on.querySelector('.pub-opt-t').textContent = viaOnly ? T('Zusätzlich unter eigener Adresse veröffentlichen') : T('Öffentlich');
      gone.hidden = pub || !st.withdrawn_at;
      if (!gone.hidden) {
        var when = fVer.format(new Date(st.withdrawn_at * 1000));
        gone.textContent = st.withdrawn_by === 'admin'
          ? T('Vom Admin zurückgezogen am {when}. Adresse und Einstellungen sind gemerkt – du kannst die Seite wieder veröffentlichen.', { when: when })
          : T('Veröffentlichung beendet am {when}. Adresse und Einstellungen sind gemerkt und gelten beim erneuten Veröffentlichen wieder.', { when: when });
        gone.className = 'pub-note' + (st.withdrawn_by === 'admin' ? ' pub-warn' : '');
      }
      // Ueber die Elternseite: Adresse und Optionen erst, wenn "Zusaetzlich ..." an ist
      card.hidden = adr.hidden = opts.hidden = viaOnly;
      oh.hidden = pub; opts.classList.toggle('is-pending', !pub);
      drawCard(); drawChw();
      var changed = pub && newSlug() !== st.slug;
      sbtn.hidden = !changed;
      setSlugErr.hint(changed ? T('Wird erst mit „Adresse ändern“ übernommen.') : '');
      ebar.hidden = !pub;
      var po = st.profile_other;
      pr.hint.textContent = po ? T('Ersetzt die bisherige Profilseite „{title}“.', { title: po.title })
        : T('Erscheint unter deiner Profiladresse – höchstens eine Seite.');
      if (st.home && !msg.textContent) pubStatus(msg, T('Diese Seite ist die Startseite für Besucher.'));
    }
    function sync(ns) {
      st = ns;
      if (S.cur && S.cur.id === id) { S.cur.published = !!ns.published; S.cur.public_url = ns.url; S.cur.published_via = ns.published_via; applyPublic(); }
    }
    function put(data, done) {
      if (!st.enabled) { pubStatus(msg, T('Veröffentlichen ist auf diesem Server abgeschaltet.'), 'err'); draw(); return; }
      api('PUT', url, data).then(function (ns) {
        sync(ns); setSlugErr('');
        if (data.slug !== undefined && ns.slug) slug.value = ns.slug;
        pubStatus(msg, ns.replaced_profile ? T('✓ Gespeichert – ersetzt die bisherige Profilseite „{title}“.', { title: ns.replaced_profile.title })
          : (done || T('✓ Gespeichert')), 'ok');
        draw();
      }).catch(function (err) {
        // Fehler zur Adresse direkt am Feld, alles andere in der Statuszeile
        if (data.slug !== undefined && (!st.published || data.slug !== st.slug)) { pubStatus(msg, ''); setSlugErr(err.message); slug.focus(); }
        else pubStatus(msg, err.message, 'err');
        draw();
      });
    }
    function stop() {
      HK.ask({ title: T('Veröffentlichung beenden?'), text: T('Der Link funktioniert danach nicht mehr.'), ok: T('Beenden'), danger: true }).then(function (yes) {
        if (!yes) { draw(); return; }
        api('DELETE', url).then(function (ns) {
          sync(ns); pubStatus(msg, ''); draw();          // Zustand steht genau einmal da: "Veroeffentlichung beendet am ..."
        }).catch(function (err) { pubStatus(msg, err.message, 'err'); draw(); });
      });
    }
    function changeSlug() {
      var v = newSlug(); if (!st.published || v === st.slug) return;
      HK.ask({ title: T('Adresse ändern?'), ok: T('Adresse ändern'),
               text: HK.fmtNodes(T('Der bisherige Link {url} funktioniert danach nicht mehr.'), { url: HK.urlNode(st.url) }) }).then(function (yes) {
        if (yes) put({ slug: v }, T('✓ Neue Adresse gespeichert')); else slug.focus();
      });
    }
    on.cb.addEventListener('change', function () {
      if (on.cb.checked) put({ slug: newSlug(), children: ch.cb.checked, indexable: ix.cb.checked, profile: pr.cb.checked }); else stop();
    });
    [['children', ch], ['indexable', ix], ['profile', pr]].forEach(function (x) {
      x[1].cb.addEventListener('change', function () { if (st.published) { var d = {}; d[x[0]] = x[1].cb.checked; put(d); } });
    });
    slug.addEventListener('input', function () { pubStatus(msg, ''); setSlugErr(''); draw(); });
    slug.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); changeSlug(); } });
    sbtn.addEventListener('click', changeSlug);
    end.addEventListener('click', stop);
    draw();
  }

  // Gesperrt (Eigentuemer) oder nur Lesen/Kommentieren (Freigabe): derselbe Schutz - der Editor bleibt lesbar, Eingaben gehen nicht
  function applyLock() {
    var on = !!(S.cur && S.cur.locked), ro = readOnly();
    document.body.classList.toggle('pg-locked', ro);
    document.body.classList.toggle('pg-ro', roRole());
    document.body.classList.toggle('pg-lock-on', on);        // gesperrt: in Datenbank-Bloecken kein "Neu", keine Ansichts-Einstellungen
    // Seite einer anderen Saga-Instanz: nur Titel und Inhalt aenderbar (kein Symbol, Titelbild, keine Tags)
    document.body.classList.toggle('pg-remote', !!S.cur && remoteId(S.cur.id) && !remCap(S.cur, 'db'));   // aeltere Gegenstelle: nur Titel/Inhalt
    $('pg-title').readOnly = ro;
    var b = $('pg-lock');
    if (!b) {
      b = el('button', 'pg-lock-b'); b.id = 'pg-lock'; b.type = 'button';
      b.addEventListener('click', function () { if (isOwner()) setLocked(false); else lockHint(); });
      $('pg-state').parentNode.insertBefore(b, $('pg-state'));
    }
    b.hidden = !on || roRole(); b.textContent = T('🔒 Gesperrt');
    b.title = isOwner() ? T('Seite ist gesperrt – klicken zum Entsperren') : T('Der Eigentümer hat die Seite gesperrt');
  }
  var LOCK_OK = '.embed-db, .embed-cal, .embed-tasks, .btnrow';      // bleibt auf gesperrten Seiten bedienbar
  function lockBlocked(e) {
    if (!readOnly()) return false;
    var tg = e.target; if (!tg || !tg.closest) return false;
    if (!$('pg-editor').contains(tg) || tg.closest(roRole() ? '.embed-db' : LOCK_OK)) return false;   // nur Lesen: auch Schaltflaechen nicht
    return true;
  }
  var lockHintT = 0;
  function lockHint() {
    var n = Date.now(); if (n - lockHintT <= 2500) return; lockHintT = n;
    if (roRole()) HK.toast(role() === 'comment' ? T('💬 Du kannst diese Seite lesen und kommentieren, aber nicht bearbeiten.') : T('👁 Du kannst diese Seite nur lesen.'));
    else if (!isOwner()) HK.toast(T('🔒 Der Eigentümer hat diese Seite gesperrt.'));
    else HK.toast(T('🔒 Seite ist gesperrt – oben auf „Gesperrt“ klicken zum Bearbeiten'));
  }

  // --- Teilen (1.3): Kopfzeile fuer fremde Seiten, Knopf "Teilen" (nur Eigentuemer), Dialog -------------------------
  function applyShare() {
    var c = S.cur, bar = $('pg-sharedby');
    $('pg-share').hidden = !c || !isOwner() || EMBED;
    // Seiten-Chat (1.5): nur auf geteilten Seiten; Seiten anderer Server (2.3), wenn ihr Server Gruppen-Chats kann
    if ($('pg-chat')) $('pg-chat').hidden = !c || EMBED || !(c.shared || foreign()) || !remCap(c, 'chatgroups');
    $('pg-share').classList.toggle('on', !!(c && c.shared));
    $('pg-share').title = c && c.shared ? T('Geteilt – Freigaben verwalten') : T('Mit Personen oder Gruppen teilen');
    if (bar) bar.remove();
    if (!c || !foreign()) return;
    // Etikett UNTER dem Titel (darunter "Zuletzt geaendert von ..."), Sekundaertext wie die Zeile darunter
    bar = el('div', 'pg-sharedby'); bar.id = 'pg-sharedby'; bar.setAttribute('role', 'note');
    bar.appendChild(grpIc('pg-sb-ic'));
    bar.appendChild(HK.fmtNodes(T('Geteilt von {name}'), { name: el('b', null, (c.owner || {}).name || '') }));
    bar.appendChild(el('span', 'pg-sb-sep', '·'));
    bar.appendChild(el('span', 'pg-sb-role pg-sb-' + role(), ROLE_YOU[role()] || ''));
    $('pg-title').after(bar);
    var m = $('pg-meta'); if (m) bar.after(m);
  }
  // Wer hat angelegt / zuletzt geaendert (nur, wenn es nicht man selbst war) - direkt unter Titel bzw. Etikett
  function applyMeta() {
    var c = S.cur, m = $('pg-meta'); if (m) m.remove();
    if (!c) return;
    var parts = [];
    if (c.created_by && !c.created_by.me) parts.push(T('Angelegt von {name}', { name: c.created_by.name }));
    if (c.updated_by && !c.updated_by.me && c.updated_by.name && c.updated)
      parts.push(T('Zuletzt geändert von {name} · {when}', { name: c.updated_by.name, when: HK.relTime(c.updated - Date.now() / 1000) }));
    if (!parts.length) return;
    m = el('div', 'pg-meta', parts.join(' · ')); m.id = 'pg-meta';
    if (c.updated) m.title = fVer.format(new Date(c.updated * 1000));
    ($('pg-sharedby') || $('pg-title')).after(m);
  }
  $('pg-share').addEventListener('click', function () { openShare(); });
  if ($('pg-chat')) $('pg-chat').addEventListener('click', function () {
    if (!S.cur) return;
    var w = window.open('/chats#p' + S.cur.id, 'hk-chats'); if (w) w.focus();
  });
  function openShare() {
    if (!S.cur || !isOwner()) return;
    var id = S.cur.id;
    api('GET', '/api/notes/' + id + '/shares').then(function (st) {
      openModal(T('Teilen'), function (body) { shareMain(body, id, st); }, true);
    }).catch(function (err) { HK.toast(T('Nicht geladen: {err}', { err: err.message })); });
  }
  function roleSelect(cur, label_) {
    var s = el('select', 'hk-in sh-role'); s.setAttribute('aria-label', label_);
    ['view', 'comment', 'edit'].forEach(function (r) { var o = el('option', null, ROLE_NAME[r]); o.value = r; s.appendChild(o); });
    s.value = cur || 'view'; return s;
  }
  function viaLabel(v) { return v.type === 'group' ? v.name : T('„{title}“', { title: v.title || T('Unbenannt') }); }
  function shareMain(body, id, st) {
    body.textContent = '';
    var box = el('div', 'pub-dlg sh-dlg'); body.appendChild(box);
    if (!st.enabled) box.appendChild(el('p', 'pub-warn', T('Der Admin hat das Teilen auf diesem Server abgeschaltet – Freigaben wirken zurzeit nicht.')));
    if (!st.shareable) box.appendChild(el('p', 'pub-warn', T('Archivierte Seiten und Vorlagen lassen sich nicht teilen.')));
    // Suchen + Rolle + Treffer
    var row = el('div', 'sh-add'), inp = el('input', 'hk-in sh-q'); inp.type = 'search'; inp.maxLength = 60; inp.autocomplete = 'off';
    inp.placeholder = T('Person oder Gruppe suchen'); inp.setAttribute('aria-label', T('Person oder Gruppe suchen'));
    inp.setAttribute('role', 'combobox'); inp.setAttribute('aria-expanded', 'false'); inp.setAttribute('aria-controls', 'sh-hits');
    var rsel = roleSelect('view', T('Rolle für neue Freigabe'));
    var wrap = el('div', 'sh-addwrap');                     // Treffer als Liste UEBER dem Dialog (kein Verschieben beim Klicken)
    row.appendChild(inp); row.appendChild(rsel); wrap.appendChild(row); box.appendChild(wrap);
    var fm = el('p', 'hk-fieldmsg'); box.appendChild(fm);
    // Bei 1-2 Zeichen sucht der Server nur genaue Treffer (kurze Namen wie "hd") - Hinweis direkt am Feld; sonst hoechstens der Gruppen-Hinweis
    var scopeHint = st.scope ? T('Nur Personen aus deinen Gruppen.') : st.federation ? T('Andere Saga-Instanzen: name@server eingeben.') : '';
    var setErr = HK.fieldMsg(inp, fm, scopeHint);
    function lenHint(q) { setErr.hint(q.length && q.length < 3 ? (st.scope ? T('Nur Personen aus deinen Gruppen. Bei 1–2 Zeichen nur genaue Treffer.') : T('Bei 1–2 Zeichen nur genaue Treffer.')) : scopeHint); }
    var help = el('p', 'sh-help'); box.appendChild(help);                 // was die gewaehlte Rolle darf
    var chips = el('div', 'sh-chips'); box.appendChild(chips);             // leeres Suchfeld: eigene Gruppen als Chips
    var hits = el('div', 'sh-hits'); hits.id = 'sh-hits'; hits.setAttribute('role', 'listbox'); hits.hidden = true; wrap.appendChild(hits);
    // Liste der Freigaben
    var lh = el('div', 'pub-opts-h', T('Zugriff')); box.appendChild(lh);
    box.appendChild(el('p', 'sh-note', T('Unterseiten und Einträge kommen mit.')));
    var list = el('ul', 'sh-list'); box.appendChild(list);
    var inh = el('div', 'sh-inh'); box.appendChild(inh);
    var msg = el('div', 'hk-status'); msg.setAttribute('role', 'status'); box.appendChild(msg);
    // Link zur Seite (oeffnet sie fuer alle mit Zugriff)
    var link = location.origin + '/notes#' + id, lc = el('div', 'pub-link sh-link');
    lc.appendChild(HK.urlNode(link, 'span', 'pub-link-a'));
    var lb = el('div', 'hk-actions'); lb.appendChild(pubBtn(T('Link kopieren'), function () { pubCopy(link); })); lc.appendChild(lb);
    box.appendChild(lc); box.appendChild(el('p', 'hk-hint sh-link-h', T('Der Link öffnet die Seite für alle, die Zugriff haben.')));
    var url = '/api/notes/' + id + '/shares', tmr = null, seq = 0, last = null;
    function hideHits() { hits.hidden = true; inp.setAttribute('aria-expanded', 'false'); }
    // Trefferliste schliessen, sobald Fokus oder Klick woanders hin geht (auch Titel/Rand des Dialogs)
    $('pg-modal').addEventListener('mousedown', function (e) { if (!row.contains(e.target) && !hits.contains(e.target)) hideHits(); });
    box.addEventListener('focusout', function () { setTimeout(function () { var a = document.activeElement; if (a !== inp && !hits.contains(a)) hideHits(); }, 0); });
    function drawHelp() { help.textContent = ROLE_HELP[rsel.value] || ''; }
    rsel.addEventListener('change', function () { drawHelp(); if (last && !hits.hidden) hitList(last); drawChips(); });
    function sync(ns) {
      st = ns; var has = !!(ns.shares || []).length;
      if (S.cur && S.cur.id === id) { S.cur.shared = has; applyShare(); }
      var p = S.pages.find(function (x) { return x.id === id; }); if (p) { p.shared = has; renderTree(); }
      draw(); drawChips();
    }
    function put(type, target, r, done, li) {
      return api('PUT', url, { type: type, target: target, role: r }).then(function (ns) { sync(ns); pubStatus(msg, done || T('✓ Gespeichert'), 'ok'); })
        .catch(function (err) { pubStatus(msg, ''); if (li) itemErr(li, err.message); else setErr(err.message); });
    }
    function itemErr(li, text) {                     // Fehler direkt am Eintrag (nicht unten in der Statuszeile)
      var e = li.querySelector('.sh-err'); if (!e) { e = el('small', 'sh-err'); e.setAttribute('role', 'alert'); li.appendChild(e); }
      e.textContent = text;
    }
    function isGrp(type) { return type === 'group' || type === 'remote_group'; }
    function avatar(type, name, url) {
      var a = el('span', 'sh-av' + (isGrp(type) ? ' sh-av-g' : '') + (type === 'remote' || type === 'remote_group' ? ' sh-av-r' : ''),
                 isGrp(type) ? '' : (name || '?').charAt(0).toUpperCase());
      if (isGrp(type)) a.appendChild(grpIc());
      a.setAttribute('aria-hidden', 'true');
      return isGrp(type) ? a : HK.avatarImg(a, url, 30);      // Profilbild ueber den Initialen (falls vorhanden)
    }
    function who(x) {
      var li = el('li', 'sh-item'); li.appendChild(avatar(x.type, x.name, x.avatar));
      var tx = el('span', 'sh-who'); tx.appendChild(el('span', 'sh-name', x.name || x.target));
      var sub = x.type === 'remote' ? T('Andere Saga-Instanz')               // Name enthaelt schon name@server
        : x.type === 'remote_group' ? T('Gruppe auf einer anderen Saga-Instanz')
        : x.type !== 'group' ? T('Person') : x.members == null ? T('Gruppe')      // 2.4 (W4): nie den Anmeldenamen
        : x.members === 1 ? T('Gruppe · 1 Person') : T('Gruppe · {n} Personen', { n: x.members });
      tx.appendChild(el('small', 'sh-sub', sub)); li.appendChild(tx);
      return li;
    }
    function draw() {
      list.textContent = '';
      var on = (S.cur && S.cur.id === id && S.cur.owner) ? S.cur.owner.name : '';
      var me = el('li', 'sh-item sh-owner'); me.appendChild(avatar('user', on || T('Du'), on ? S.cur.owner.avatar : null));
      var mt = el('span', 'sh-who'); mt.appendChild(el('span', 'sh-name', on ? T('{name} (du)', { name: on }) : T('Du')));
      mt.appendChild(el('small', 'sh-sub', T('Eigentümer'))); me.appendChild(mt);
      list.appendChild(me);
      (st.shares || []).forEach(function (x) {
        var li = who(x), sel = roleSelect(x.role, T('Rolle von {name}', { name: x.name }));
        var top = (x.via || [])[0];
        if (x.type === 'user' && top && RANK[top.role] > RANK[x.role]) {     // ueber eine Gruppe/Seite darueber mehr Rechte
          var v = el('small', 'sh-via', T('(über {via}: {role})', { via: viaLabel(top), role: ROLE_NAME[top.role] }));
          li.querySelector('.sh-who').appendChild(v);
        }
        sel.addEventListener('change', function () { put(x.type, x.target, sel.value, T('✓ Rolle geändert'), li); });
        var rm = el('button', 'icon-btn sh-rm', '×'); rm.type = 'button'; rm.title = T('Freigabe entfernen'); rm.setAttribute('aria-label', T('Freigabe für {name} entfernen', { name: x.name }));
        rm.addEventListener('click', function () {
          var text = top ? T('{name} behält über {via} die Rolle {role}.', { name: x.name, via: viaLabel(top), role: ROLE_NAME[top.role] })
            : isGrp(x.type) ? T('Die Gruppe {name} verliert sofort den Zugriff auf diese Seite und ihre Unterseiten.', { name: x.name })
            : T('{name} verliert sofort den Zugriff auf diese Seite und ihre Unterseiten.', { name: x.name });
          HK.ask({ title: T('Freigabe entfernen?'), text: text, ok: T('Entfernen'), danger: true }).then(function (yes) {
            if (!yes) return;
            api('DELETE', url + '?type=' + encodeURIComponent(x.type) + '&target=' + encodeURIComponent(x.target))
              .then(function (ns) { sync(ns); pubStatus(msg, T('✓ Freigabe entfernt'), 'ok'); })
              .catch(function (err) { itemErr(li, err.message); });
          });
        });
        li.appendChild(sel); li.appendChild(rm); list.appendChild(li);
      });
      if (!(st.shares || []).length) list.appendChild(el('li', 'sh-empty', T('Noch mit niemandem geteilt. Suche oben nach einer Person oder Gruppe.')));
      inh.textContent = '';
      if ((st.inherited || []).length) {
        inh.appendChild(el('div', 'pub-opts-h', T('Über übergeordnete Seiten')));
        inh.appendChild(el('p', 'sh-note', T('Diese Freigaben gelten auch hier. Ändern kannst du sie auf der übergeordneten Seite.')));
        var ul = el('ul', 'sh-list');
        st.inherited.forEach(function (x) {
          var li = who(x); li.classList.add('sh-inherited');
          li.appendChild(el('span', 'sh-role-l', ROLE_NAME[x.role] || ''));
          var from = el('button', 'linkbtn sh-from', T('über „{title}“', { title: x.title })); from.type = 'button';
          from.title = T('Zur übergeordneten Seite');
          from.addEventListener('click', function () { closeModal(); openPage(x.note_id).then(openShare); });
          li.appendChild(from); ul.appendChild(li);
        });
        inh.appendChild(ul);
      }
    }
    function taken() { var o = {}; (st.shares || []).forEach(function (x) { o[x.type + ':' + x.target] = x.role; }); return o; }
    function drawChips() {
      chips.textContent = '';
      var gs = st.groups || [];
      chips.hidden = !gs.length || !!inp.value.trim() || inp.disabled;
      if (chips.hidden) return;
      chips.appendChild(el('span', 'sh-chips-l', T('Deine Gruppen:')));
      var tk = taken();
      gs.forEach(function (g) {
        // hat schon Zugriff: Haken im Chip (nicht nur Farbe); sonst einfarbiges Gruppen-Symbol
        var b = el('button', 'sh-chip' + (tk['group:' + g] ? ' on' : '')); b.type = 'button';
        if (tk['group:' + g]) b.appendChild(el('span', 'sh-chip-ok', '✓')); else b.appendChild(grpIc());
        b.appendChild(el('span', null, g));
        b.title = tk['group:' + g] ? T('hat Zugriff: {role}', { role: ROLE_NAME[tk['group:' + g]] }) : T('als {role} hinzufügen', { role: ROLE_NAME[rsel.value] });
        b.addEventListener('click', function () { put('group', g, rsel.value, T('✓ Geteilt mit {name} ({role})', { name: g, role: ROLE_NAME[rsel.value] })); });
        chips.appendChild(b);
      });
    }
    function hitList(d) {
      last = d; hits.textContent = ''; var any = false, tk = taken();
      function add(type, target, name, sub, url) {
        any = true;
        var b = el('button', 'sh-hit'); b.type = 'button'; b.setAttribute('role', 'option');
        b.appendChild(avatar(type, name, url));
        var tx = el('span', 'sh-who'); tx.appendChild(el('span', 'sh-name', name)); tx.appendChild(el('small', 'sh-sub', sub)); b.appendChild(tx);
        b.appendChild(el('span', 'sh-has', tk[type + ':' + target] ? T('hat Zugriff') : T('als {role} hinzufügen', { role: ROLE_NAME[rsel.value] })));
        b.addEventListener('click', function () {
          inp.value = ''; hideHits(); drawChips();
          put(type, target, rsel.value, T('✓ Geteilt mit {name} ({role})', { name: name, role: ROLE_NAME[rsel.value] })); inp.focus();
        });
        hits.appendChild(b);
      }
      (d.users || []).forEach(function (u) { add('user', u.username, u.name, T('Person auf diesem Server') + (u.sub ? ' · ' + u.sub : ''), u.avatar); });   // sub: Merkmal fuer Gleichnamige (2.4)
      (d.groups || []).forEach(function (g) { add('group', g.name, g.name, T('Gruppe')); });
      (d.remote || []).forEach(function (h) {             // andere Saga-Instanz: Adressbuch (2.3) bzw. genaue Adresse name@server
        if (h.label) add(h.type, h.target, h.label, h.pub ? T('Person auf {host} · {addr}', { host: h.host, addr: h.target.replace(/^@/, '') })
                                                          : T('Person auf {host}', { host: h.host }), h.avatar);   // ohne oeffentlichen Namen: pseudonymer Handle (2.3)
        else add(h.type, h.target, h.name, h.type === 'remote' ? T('Andere Saga-Instanz') : T('Gruppe auf einer anderen Saga-Instanz'));
      });
      hits.hidden = !any; inp.setAttribute('aria-expanded', any ? 'true' : 'false');
      if (!any && inp.value.trim().length >= 3) setErr(d.remote_hint || T('Niemand gefunden.'));   // name@server: Grund vom Server
    }
    function search() {
      var q = inp.value.trim(); setErr(''); lenHint(q); pubStatus(msg, ''); clearTimeout(tmr); drawChips();
      if (!q) { hideHits(); return; }
      var my = ++seq;
      tmr = setTimeout(function () {
        api('GET', '/api/share/targets?q=' + encodeURIComponent(q)).then(function (d) { if (my === seq && inp.value.trim()) hitList(d); })
          .catch(function (err) { setErr(err.message); });
      }, 200);
    }
    inp.addEventListener('input', search);
    inp.addEventListener('keydown', function (e) {
      var items = [].slice.call(hits.querySelectorAll('.sh-hit'));
      if (e.key === 'Escape' && !hits.hidden) { e.preventDefault(); e.stopPropagation(); hideHits(); return; }   // Esc: erst die Liste
      if (e.key === 'ArrowDown' && items.length) { e.preventDefault(); items[0].focus(); }
      if (e.key === 'Enter' && items.length) { e.preventDefault(); items[0].click(); }
    });
    hits.addEventListener('keydown', function (e) {
      var items = [].slice.call(hits.querySelectorAll('.sh-hit')), i = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown') { e.preventDefault(); (items[i + 1] || items[i]).focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); if (i <= 0) inp.focus(); else items[i - 1].focus(); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); hideHits(); inp.focus(); }
    });
    inp.disabled = rsel.disabled = !st.enabled || !st.shareable;
    drawHelp(); draw(); drawChips();
    setTimeout(function () { if (!inp.disabled) inp.focus(); }, 0);
    inp.addEventListener('click', function () { if (hits.hidden && last && inp.value.trim()) hitList(last); });
  }
  document.addEventListener('keydown', function (e) {
    if (!lockBlocked(e)) return;
    var nav = /^(Arrow|Page|Home|End|Escape|Shift|Control|Alt|Meta|Tab$)/.test(e.key) || ((e.metaKey || e.ctrlKey) && /^[acfkpl\[\]]$/i.test(e.key));
    if (nav) return;
    e.preventDefault(); e.stopPropagation(); lockHint();
  }, true);
  ['beforeinput', 'paste', 'drop', 'cut'].forEach(function (ev) {
    document.addEventListener(ev, function (e) { if (lockBlocked(e)) { e.preventDefault(); e.stopPropagation(); lockHint(); } }, true);
  });
  document.addEventListener('click', function (e) {               // Haekchen in Checklisten nicht umschalten
    if (lockBlocked(e) && e.target.closest('.cdx-list__checkbox, .cdx-list__checkbox-check')) { e.preventDefault(); e.stopPropagation(); lockHint(); }
  }, true);

  function showArchive() {
    var arch = (S.all || []).filter(function (p) { return p.archived && !(p.parent_id && (anyById(p.parent_id) || {}).archived); });
    openModal('📦 ' + T('Archiv'), function (body) {
      if (!arch.length) { body.appendChild(el('p', 'trash-hint', T('Nichts archiviert. Archivieren geht im Seitenmenü „⋯“ – die Seite verschwindet dann aus Baum und Suche, bleibt aber erhalten.'))); return; }
      body.appendChild(el('p', 'trash-hint', T('Archivierte Seiten fehlen im Seitenbaum und in der Suche. Links darauf funktionieren weiter.')));
      var ul = el('ul', 'trash-list');
      arch.forEach(function (p) {
        var li = el('li'), o = el('button', 'linkbtn', (ti(p.icon) || '📄') + ' ' + label(p)); o.type = 'button';
        o.addEventListener('click', function () { closeModal(); openPage(p.id); });
        var rb = el('button', 'linkbtn', T('Zurückholen')); rb.type = 'button';
        rb.addEventListener('click', function () { closeModal(); setArchived(p.id, false); });
        li.appendChild(o); li.appendChild(rb); ul.appendChild(li);
      });
      body.appendChild(ul);
    });
  }
  $('pg-archive-btn').addEventListener('click', showArchive);

  // --- Zuletzt geoeffnet, Links, Befehle (Befehlspalette + static/keys.js) --------------------
  function recents() { try { return (JSON.parse(HK.store('hk-pg-recent') || '[]') || []).filter(function (x) { return typeof x === 'number'; }); } catch (e) { return []; } }
  function rememberRecent(id) {
    if (EMBED) return;
    var r = recents().filter(function (x) { return x !== id; }); r.unshift(id); HK.store('hk-pg-recent', JSON.stringify(r.slice(0, 20)));
  }
  function pageLink(id, block) { return location.origin + '/notes#' + id + (block ? ':' + encodeURIComponent(block) : ''); }
  function copyLink(block) {
    if (!S.cur) return;
    var u = pageLink(S.cur.id, block);
    var done = function () { HK.toast(block ? T('Link zum Block kopiert') : T('Link zur Seite kopiert')); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(u).then(done, function () { prompt(T('Link kopieren:'), u); });
    else prompt(T('Link kopieren:'), u);
  }
  function showBlock(bid) {
    var b = null;
    try { b = document.querySelector('#pg-editor .ce-block[data-id="' + CSS.escape(bid) + '"]'); } catch (e) { b = null; }
    if (!b) return false;
    b.scrollIntoView({ block: 'center', behavior: 'smooth' });
    b.classList.remove('hk-flash'); void b.offsetWidth; b.classList.add('hk-flash');
    setTimeout(function () { b.classList.remove('hk-flash'); }, 2200);
    return true;
  }
  function goParent() { if (S.cur && S.cur.parent_id && byId(S.cur.parent_id)) openPage(S.cur.parent_id); }
  function actions() {
    var c = S.cur, a = [];
    function add(t, ic, fn, o) { o = o || {}; a.push({ t: t, ic: ic, fn: fn, kw: o.kw, kbd: o.kbd }); }
    var mk = isMac ? '⌘' : T('Strg') + '+';
    add(T('Neue Seite'), '＋', function () { newPage(null); }, { kw: 'new page anlegen erstellen' });
    var own = isOwner(), ed = canEdit();
    if (c) {
      if (ed && remCap(c, 'subpages')) add(T('Neue Unterseite'), '＋', function () { newPage(c.id); }, { kw: 'subpage unterseite anlegen' });
      add(T('Link zur Seite kopieren'), '🔗', function () { copyLink(); }, { kw: 'url teilen copy link', kbd: mk + 'L' });
      if (own) add(T('Teilen …'), 'grp', openShare, { kw: 'share teilen freigeben personen gruppen' });
      add(T('Kommentare'), '💬', function () { if (window.HKComments) HKComments.openAll(); }, { kw: 'comments kommentar unterhaltung' });
      if (own) add(c.pinned ? T('Von Favoriten lösen') : T('Zu Favoriten'), '★', function () { setPinned(!c.pinned); }, { kw: 'favorit stern pin' });
      if (ed && remCap(c, 'db')) add(c.wide ? T('Volle Breite aus') : T('Volle Breite ein'), '↔', function () { setWide(!c.wide); }, { kw: 'breite wide layout' });
      add(T('Vollbild'), '⤢', function () { setFull(!$('pages').classList.contains('pg-full')); }, { kw: 'fullscreen fokus' });
      if (own) add(T('Seite duplizieren'), '⧉', duplicatePage, { kw: 'kopie duplicate' });
      if (ed && remCap(c, 'versions')) add(T('Versionsverlauf'), '🕘', openVersions, { kw: 'history versionen' });
      if (remCap(c, 'export')) add(T('Als Markdown exportieren'), '⬇', function () { exportPage(false); }, { kw: 'export md download' });
      if (c.parent_id) add(T('Übergeordnete Seite'), '↑', goParent, { kw: 'eltern parent hoch', kbd: mk + '⇧U' });
      if (window.HKKeys) add(T('Inhaltsverzeichnis ein/aus'), '☰', HKKeys.toggleToc, { kw: 'toc gliederung ueberschriften' });
      if (window.HKGraph && !remoteId(c.id)) add(T('Lokaler Graph'), '✳', HKGraph.openLocal, { kw: 'graph local nachbarn verbindungen links' });
    }
    if (window.HKGraph && !EMBED) add(T('Graph'), '✳', function () { HKGraph.open({ mode: 'all', id: c && !remoteId(c.id) ? c.id : null }); }, { kw: 'graph netz verbindungen links obsidian' });
    if (!EMBED) {
      add(T('Zurück'), '←', function () { history.back(); }, { kw: 'back', kbd: mk + '[' });
      add(T('Vor'), '→', function () { history.forward(); }, { kw: 'forward', kbd: mk + ']' });
      add(T('Seitenleiste ein/aus'), '◧', function () { setSide(document.body.classList.contains('side-hidden')); }, { kw: 'sidebar', kbd: mk + '\\' });
    }
    if (c && own) add(c.locked ? T('Seite entsperren') : T('Seite sperren'), '🔒', function () { setLocked(!c.locked); }, { kw: 'lock sperren schuetzen' });
    if (c && own) add(c.archived ? T('Aus dem Archiv holen') : T('Seite archivieren'), '📦', function () { setArchived(c.id, !c.archived); }, { kw: 'archiv archive' });
    add(T('Archiv anzeigen'), '📦', showArchive, { kw: 'archiv archive' });
    add(T('Papierkorb'), '🗑', showTrash, { kw: 'trash geloescht' });
    if (window.HKSettings) add(T('Einstellungen'), '⚙', function () { HKSettings.open(); }, { kw: 'settings kalender immich nextcloud schluessel' });
    add(T('Übersicht (Startseite)'), '⌂', function () { location.href = '/'; }, { kw: 'home dashboard start' });
    return a;
  }

  // --- Live (1.4): jemand anderes hat die offene Seite geaendert (static/live.js meldet es) ------------------
  // Ohne ungespeicherte Aenderungen: still neu laden (Scroll-Position und Schreibmarke so gut es geht behalten).
  // Sonst (oder waehrend einer Eingabe ausserhalb des Editors, Peek, offener Schublade): Hinweis mit "Neu laden".
  // Der vorhandene Konfliktweg (409 beim Speichern) bleibt die Absicherung.
  function busy() { return !!(S.dirtyBody || S.dirtyTitle || S.conflict || S.lost || S.checking || S.loading || S.inflight); }
  function remoteChange(ev) {
    if (!S.cur || !ev || ev.id !== S.cur.id || EMBED) return;
    if (!ev.gone && S.cur.kind === 'database') return;            // Datenbank: die Ansicht laedt selbst neu (hk-note-changed)
    if (ev.gone) { if (busy() && foreign()) lostAccess(404); else if (!busy()) openPage(ev.id); return; }
    var newer = ev.rev != null && S.cur.rev != null ? ev.rev > S.cur.rev : false;
    if (!newer && !(ev.updated && S.cur.updated && ev.updated > S.cur.updated + 0.001)) return;
    if (S.conflict) return;                                         // Konflikt-Hinweis steht schon
    var a = document.activeElement, inEditor = a && $('pg-editor').contains(a) && a.isContentEditable;
    var typingElsewhere = a && $('page').contains(a) && !inEditor && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName);
    if (busy() || typingElsewhere || PK.id || document.body.classList.contains('side-open') || !$('pg-modal').hidden) { remoteHint(ev.by); return; }
    silentReload(ev.by);
  }
  function remoteHint(by) {
    var bar = $('pg-remote');
    if (!bar) { bar = el('div', 'pg-conflict pg-remote'); bar.id = 'pg-remote'; bar.setAttribute('role', 'status'); $('page').insertBefore(bar, $('page').firstChild); }
    bar.textContent = '';
    bar.appendChild(el('span', 'pgc-t', by && by.name ? T('{name} hat diese Seite geändert', { name: by.name }) : T('Diese Seite wurde an anderer Stelle geändert')));
    var go = el('button', 'btn btn-primary', T('Neu laden')); go.type = 'button';
    go.addEventListener('click', function () {
      var id = S.cur && S.cur.id, draft = (S.dirtyBody || S.dirtyTitle) ? draftText() : '';
      clearRemote(); if (!id) return;
      clearTimeout(S.timer); S.dirtyBody = S.dirtyTitle = false; clearConflict();
      openPage(id).then(function () {
        if (draft) showDraft(T('Dein Entwurf'), T('Die neue Version ist geladen. Dein nicht gespeicherter Text steht unten – kopiere, was du behalten willst.'), draft);
      });
    });
    var x = el('button', 'icon-btn pg-remote-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Hinweis schließen'));
    x.addEventListener('click', clearRemote);
    bar.appendChild(go); bar.appendChild(x);
  }
  function clearRemote() { var b = $('pg-remote'); if (b) b.remove(); }
  document.addEventListener('hk-page-open', clearRemote);
  // Position der Schreibmarke: Index des obersten Blocks + Zeichen-Abstand im bearbeitbaren Teil
  function caretPos() {
    var sel = window.getSelection(), box = $('pg-editor');
    if (!sel || !sel.rangeCount || !box) return null;
    var r = sel.getRangeAt(0), node = r.startContainer, elx = node.nodeType === 1 ? node : node.parentElement;
    if (!elx || !box.contains(elx)) return null;
    var red = box.querySelector('.codex-editor__redactor'), blk = elx.closest('.ce-block');
    while (blk && blk.parentElement !== red) blk = blk.parentElement ? blk.parentElement.closest('.ce-block') : null;
    var ed = elx.closest('[contenteditable="true"]');
    if (!blk || !ed || !blk.contains(ed)) return null;
    var pre = document.createRange(); pre.selectNodeContents(ed); pre.setEnd(r.startContainer, r.startOffset);
    return { idx: [].indexOf.call(red.children, blk), off: pre.toString().length };
  }
  function silentReload(by) {
    var sc = document.scrollingElement || document.documentElement, y = sc.scrollTop, pos = caretPos(), id = S.cur.id;
    return openPage(id).then(function () {
      if (!S.cur || S.cur.id !== id) return;
      sc.scrollTop = y;
      if (pos && S.editor) { try { S.editor.caret.setToBlock(pos.idx, 'default', pos.off); sc.scrollTop = y; } catch (e) { /* Block gibt es nicht mehr */ } }
      if (by && by.name) state(T('Aktualisiert – geändert von {name}', { name: by.name }), 'ok');
    });
  }

  // --- Start ----------------------------------------------------------------
  window.HKPages = {
    // Bausteine fuer static/keys.js (Tastenkuerzel, Block-Aktionen, Inhaltsverzeichnis)
    k: { cur: function () { return S.cur; }, editor: function () { return S.editor; },
         eds: function () { return (S.eds || []).filter(function (x) { return x.holder.isConnected; }); },
         open: function (id) { return openPage(id); }, byId: byId, goParent: goParent, copyLink: copyLink, showBlock: showBlock,
         readOnly: function () { return readOnly(); },
         dup: function (ed, blk) { return dupBlock(ed, blk); }, styleAt: styleAt, styleBlock: styleBlock, colors: COLORS, colorNames: COLOR_NAMES,
         toggleOf: function (node) { var w = node && node.closest ? node.closest('.tgl') : null; return w ? TGL_OF.get(w) || null : null; },
         embed: EMBED, isMac: isMac, cleanClip: function (list, nested) { return cleanClip(list, nested ? nestedTools() : (S.tools || {})); }, openQS: function () { openQS(); }, pages: function () { return S.pages; }, label: label, ti: ti,
         recents: recents, decorate: function () { decorateLinks(); }, ancestors: ancestors,
         recheck: function () { return recheck(true); },        // static/comments.js: Rolle/Zugriff nach 403/404 neu pruefen
         // Live-Kanal (static/live.js): Seitenliste neu laden, fremde Aenderung der offenen Seite, ungelesen-Punkt im Baum
         reloadList: function () { return loadList(); }, remote: remoteChange,
         // Live notes.tree (2.2): Baum anderswo geaendert -> nachladen. Das erste Ereignis nach einer Pause sofort (Bughunt
         // P13; der Server buendelt Folgeereignisse ohnehin je 2 s), nur Serien kurz sammeln. loadList legt gleichzeitige
         // Aufrufe zusammen.
         treeChanged: function () {
           var now = Date.now(), quiet = now - (S.treeEvAt || 0) > 2500;
           S.treeEvAt = now; clearTimeout(S.treeT);
           if (quiet) loadList().catch(function () {});
           else S.treeT = setTimeout(function () { loadList().catch(function () {}); }, 300);
         }, busy: busy, foreign: function () { return foreign(); },
         setUnread: function (id, on) { var p = byId(id); if (p && !!p.unread !== !!on) { p.unread = !!on; renderTree(); } } },
    editor: function () { return S.editor; },   // fuer Tests/Diagnose
    flush: function () { return S.conflict ? Promise.reject(new Error('conflict')) : save(); },   // Peek: vor dem Schliessen speichern
    init: function (onCount) {
      S.onCount = onCount || S.onCount;
      // Editor.js schon jetzt laden (parallel zu Baum, Diensten und Seite) - geoeffnet wird fast immer eine Seite (P04)
      loadVendor().catch(function () { /* openPage meldet den Fehler */ });
      var svc = api('GET', '/api/services').then(function (s) { S.services = s; }).catch(function () {});
      return Promise.all([loadList(), svc]).then(function () {
        var fromHash = parseInt(location.hash.slice(1), 10), last = +HK.store('hk-pg-last');
        if (fromHash && !anyById(fromHash)) return openPage(fromHash);       // alte/fremde Adresse: Server fragen ("Kein Zugriff")
        var start = anyById(fromHash) || byId(last) || children(null)[0];
        if (start) return openPage(start.id);
        setLocation();
      });
    }
  };

  window.addEventListener('hashchange', function () {
    var id = parseInt(location.hash.slice(1), 10), bm = location.hash.match(/^#-?\d+:(.+)$/);
    if (id && S.cur && S.cur.id === id && bm) { showBlock(decodeURIComponent(bm[1])); return; }
    // schon offen - es sei denn, inzwischen ist eine andere Seite angefordert (schnelles Wechseln: S.cur hinkt hinterher)
    if (!id || (S.cur && S.cur.id === id && (S.wantId == null || S.wantId === id))) return;
    // Unbekannte ID (z.B. gerade per Schaltflaeche angelegt): erst Seitenliste neu laden, dann oeffnen
    (anyById(id) ? Promise.resolve() : loadList()).then(function () { openPage(id); });
  });
  // Eigene Notizen-Seite (/notes): selbst starten
  if (document.body.classList.contains('notes-page')) window.HKPages.init();
})();
