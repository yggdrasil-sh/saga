// Saga - Seiten (Notizen im Notion-Stil) auf Basis von Editor.js
// Wird von notes.js erst beim Oeffnen des Notizen-Tabs nachgeladen.
// Alle Benutzerinhalte im Baum/Suche per textContent; Block-Inhalte bereinigt der Server.
(function () {
  'use strict';
  var HK = window.HK, $ = HK.$, el = HK.el, api = HK.api;

  var I18N = { messages: {
    ui: {
      blockTunes: { toggler: { 'Click to tune': T('Optionen'), 'or drag to move': T('oder ziehen zum Verschieben') } },
      inlineToolbar: { converter: { 'Convert to': T('Umwandeln in') } },
      toolbar: { toolbox: { Add: T('Block hinzufügen') } },
      popover: { Filter: T('Filtern …'), 'Nothing found': T('Nichts gefunden'), 'Convert to': T('Umwandeln in') }
    },
    toolNames: { Text: T('Text'), Heading: T('Überschrift'), 'Unordered List': T('Aufzählung'), 'Ordered List': T('Nummerierte Liste'),
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
  function loadScript(src) {
    return new Promise(function (ok, fail) {
      var s = document.createElement('script'); s.src = src; s.onload = ok;
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
    vendorReady = VENDOR.reduce(function (p, f) {
      return p.then(function () { return loadScript('/static/vendor/editorjs/' + f + '?v=' + V); });
    }, Promise.resolve());
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

  // --- Zustand ---------------------------------------------------------------
  var S = { pages: [], cur: null, editor: null, loading: false, dirtyBody: false, dirtyTitle: false, timer: null,
            open: {}, onCount: function () {} };
  try { S.open = JSON.parse(HK.store('hk-pg-open') || '{}') || {}; } catch (e) { S.open = {}; }
  function saveOpen() { HK.store('hk-pg-open', JSON.stringify(S.open)); }
  function byId(id) { return S.pages.find(function (p) { return p.id === id; }) || null; }
  function children(pid) { return S.pages.filter(function (p) { return (p.parent_id || null) === pid || (pid === null && p.parent_id && !byId(p.parent_id)); }); }
  function ancestors(id) { var out = [], p = byId(id), guard = 0; while (p && p.parent_id && guard++ < 50) { p = byId(p.parent_id); if (p) out.unshift(p); } return out; }
  function descendants(id) { var out = [], st = [id]; while (st.length) { var x = st.pop(); S.pages.forEach(function (p) { if (p.parent_id === x) { out.push(p.id); st.push(p.id); } }); } return out; }
  function label(p) { return p && p.title ? p.title : T('Unbenannt'); }
  function nSub(n) { return n === 1 ? T('1 Unterseite') : T('{n} Unterseiten', { n: n }); }

  function state(t, cls) { var s = $('pg-state'); s.textContent = t; s.className = 'note-state ' + (cls || ''); }

  // --- Seitenbaum -----------------------------------------------------------
  function renderTree() {
    var ul = $('pg-tree'); ul.textContent = ''; treeRootDnd(ul);
    (function add(list, pid, depth) {
      children(pid).forEach(function (p) {
        // Datenbank-Zeilen erscheinen nicht einzeln im Baum (wie in Notion)
        var kids = p.kind === 'database' ? [] : children(p.id), li = el('li', 'pg-node'); li.setAttribute('role', 'treeitem');
        var row = el('div', 'pg-row' + (S.cur && S.cur.id === p.id ? ' on' : ''));
        row.style.paddingLeft = (0.35 + depth * 1.05) + 'rem';
        var tw = el('button', 'pg-twisty' + (kids.length ? '' : ' leaf'), kids.length ? '▸' : '');
        tw.type = 'button'; tw.tabIndex = kids.length ? 0 : -1;
        if (S.open[p.id]) tw.classList.add('open');
        tw.setAttribute('aria-label', S.open[p.id] ? T('Zuklappen') : T('Aufklappen'));
        tw.addEventListener('click', function (e) { e.stopPropagation(); S.open[p.id] = !S.open[p.id]; saveOpen(); renderTree(); });
        var go = el('button', 'pg-go'); go.type = 'button';
        go.appendChild(icoNode(p.icon, '📄', 'pg-ic'));
        go.appendChild(el('span', 'pg-name', label(p)));
        if (p.template) go.appendChild(el('span', 'pg-badge', T('Vorlage')));
        go.addEventListener('click', function () { openPage(p.id); });
        var plus = el('button', 'pg-add', '+'); plus.type = 'button'; plus.title = T('Unterseite hinzufügen');
        plus.setAttribute('aria-label', T('Unterseite zu {name} hinzufügen', { name: label(p) }));
        plus.addEventListener('click', function (e) { e.stopPropagation(); newPageMenu(plus, p.id); });
        row.appendChild(tw); row.appendChild(go); row.appendChild(plus); li.appendChild(row);
        treeDnd(row, p);
        if (kids.length && S.open[p.id]) { var sub = el('ul', 'pg-sub'); sub.setAttribute('role', 'group'); add(sub, p.id, depth + 1); li.appendChild(sub); }
        list.appendChild(li);
      });
    })(ul, null, 0);
    $('pg-empty').hidden = S.pages.length > 0 || !!S.cur;
    S.onCount(S.pages.length);
    renderFavs();
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

  function loadList() {
    return api('GET', '/api/notes').then(function (list) {
      S.all = list; S.pages = list.filter(function (p) { return !p.archived; });       // Archiv: nicht im Baum/Suche/Palette
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
          immich: { class: ImmichTool },
          embed: { class: EmbedTool },
          table: { class: window.Table, inlineToolbar: true, config: { rows: 2, cols: 3, withHeadings: true } },
          toggle: { class: ToggleTool, inlineToolbar: true },
          callout: { class: CalloutTool, inlineToolbar: true },
          hkdate: { class: DateInline },
          image: { class: window.ImageTool, config: {
            endpoints: { byFile: '/api/files' }, field: 'image', additionalRequestHeaders: { 'X-Saga': '1' },
            types: 'image/png,image/jpeg,image/gif,image/webp', captionPlaceholder: T('Bildunterschrift'), buttonContent: T('Bild auswählen …') } },
          attaches: { class: window.AttachesTool, config: {
            endpoint: '/api/files', field: 'file', additionalRequestHeaders: { 'X-Saga': '1' },
            buttonText: T('Datei auswählen …'), errorMessage: T('Hochladen fehlgeschlagen') } }
        },
        i18n: I18N,
        onChange: function () { if (!S.loading) { S.dirtyBody = true; schedule(); } }
      });
      $('pg-editor').addEventListener('input', function () { if (!S.loading) tryShortcut(S.editor); });
      return S.editor.isReady.then(function () {
        try { new window.DragDrop(S.editor); } catch (e) { /* Ziehen ist Komfort - Editor laeuft auch ohne */ }
        return S.editor;
      });
    });
  }

  function schedule() {
    if (S.cur && S.cur.locked) { S.dirtyBody = S.dirtyTitle = false; return; }        // gesperrt: nichts speichern
    state(T('Ungespeichert …')); clearTimeout(S.timer); S.timer = setTimeout(save, 900);
  }

  // Speichern nacheinander (Warteschlange): ein zweites Speichern waehrend das erste noch laeuft, wuerde sonst
  // die veraltete Revision mitschicken -> falscher Konflikt "mit sich selbst" (z. B. bei langsamer Verbindung)
  function save() {
    S.saveChain = (S.saveChain || Promise.resolve()).then(saveNow, saveNow);
    return S.saveChain;
  }
  function saveNow() {
    if (!S.cur || S.conflict || (!S.dirtyBody && !S.dirtyTitle)) return Promise.resolve();
    clearTimeout(S.timer);
    var id = S.cur.id, body = {};
    if (S.dirtyTitle) { body.title = $('pg-title').value; S.dirtyTitle = false; }
    var getBody = S.dirtyBody && S.editor ? S.editor.save() : Promise.resolve(null);
    S.dirtyBody = false;
    state(T('Speichert …'));
    return getBody.then(function (out) {
      if (out) { body.body = JSON.stringify(out); body.format = 'blocks'; }
      if (!Object.keys(body).length) return;
      if (S.cur && S.cur.id === id && !S.force && S.cur.rev != null) body.rev = S.cur.rev;   // Konflikterkennung
      S.force = false;
      return api('PATCH', '/api/notes/' + id, body).then(function (d) {
        if (S.cur && S.cur.id === id && d.rev != null) S.cur.rev = d.rev;
        var p = byId(id); if (p) { p.updated = d.updated; if ('title' in body) p.title = body.title.trim(); }
        if (!S.dirtyBody && !S.dirtyTitle) state(T('Gespeichert ✓ {time}', { time: HK.fUpd.format(new Date(d.updated * 1000)) }), 'ok');
        if ('title' in body) { if (S.cur && S.cur.id === id) S.cur.title = body.title.trim(); renderTree(); renderCrumbs(); setLocation(); }
      });
    }).catch(function (err) {
      S.dirtyBody = S.dirtyBody || !!body.body; S.dirtyTitle = S.dirtyTitle || 'title' in body;
      if (err.message === 'conflict') { S.conflict = true; showConflict(); state(T('Nicht gespeichert – Seite wurde zwischenzeitlich geändert'), 'err'); return; }
      state(T('Nicht gespeichert: {err}', { err: err.message }), 'err');
    });
  }
  // Konflikt: jemand (anderer Tab, Peek, spaeter andere Person) hat Titel/Inhalt inzwischen geaendert
  function showConflict() {
    var bar = $('pg-conflict');
    if (!bar) { bar = el('div', 'pg-conflict'); bar.id = 'pg-conflict'; bar.setAttribute('role', 'alert'); $('page').insertBefore(bar, $('page').firstChild); }
    bar.textContent = '';
    bar.appendChild(el('span', 'pgc-t', T('⚠ Diese Seite wurde inzwischen an anderer Stelle geändert. Deine letzten Änderungen sind noch nicht gespeichert.')));
    var load = el('button', 'btn', T('Neue Version laden')); load.type = 'button';
    load.addEventListener('click', function () {
      if (!confirm(T('Deine ungespeicherten Änderungen verwerfen und die neue Version laden?'))) return;
      clearConflict(); S.dirtyBody = S.dirtyTitle = false; openPage(S.cur.id);
    });
    var keep = el('button', 'btn', T('Meine Version behalten')); keep.type = 'button';
    keep.addEventListener('click', function () { clearConflict(); S.force = true; S.dirtyBody = true; S.dirtyTitle = true; save(); });
    bar.appendChild(load); bar.appendChild(keep);
  }
  function clearConflict() { S.conflict = false; var b = $('pg-conflict'); if (b) b.remove(); }

  // Browser-Tab-Titel und Direktlink (/notes#<id>) aktuell halten
  function setLocation() {
    if (!S.cur) { document.title = T('Notizen') + ' · Saga'; return; }
    document.title = (ti(S.cur.icon) ? ti(S.cur.icon) + ' ' : '') + label(S.cur) + ' · ' + T('Notizen');
    if (parseInt(location.hash.slice(1), 10) === S.cur.id) return;                 // auch "#12:block" = diese Seite
    if (!EMBED && /^#\d/.test(location.hash)) history.pushState(null, '', '#' + S.cur.id);   // Zurueck/Vor wie im Browser
    else history.replaceState(null, '', '#' + S.cur.id);
  }

  function openPage(id) {
    return save().then(function () { return api('GET', '/api/notes/' + id); }).then(function (n) {
      S.cur = n; S.loading = true;
      $('page').hidden = false; $('pg-empty').hidden = true;
      $('pg-title').value = n.title || '';
      showIcon(n.icon); showCover(); $('page').classList.toggle('wide', !!n.wide);
      var data;
      try { data = n.format === 'blocks' ? JSON.parse(n.body || '{"blocks":[]}') : textToBlocks(n.body); }
      catch (e) { data = { blocks: [] }; }
      ancestors(id).forEach(function (a) { S.open[a.id] = true; }); saveOpen();
      if (!EMBED) HK.store('hk-pg-last', String(id));
      setLocation();
      renderTree(); renderCrumbs();
      state(n.updated ? T('Gespeichert ✓ {time}', { time: HK.fUpd.format(new Date(n.updated * 1000)) }) : '', 'ok');
      applyKind();      // Ansicht (Datenbank / Zeile / Seite) sofort umschalten, nicht erst nach dem Rendern
      return ensureEditor(data);
    }).then(function () { S.loading = false; })
      .catch(function (err) { S.loading = false; HK.toast(T('Seite konnte nicht geöffnet werden: {err}', { err: err.message })); });
  }

  function newPage(parentId) {
    return save().then(function () { return api('POST', '/api/notes', { parent_id: parentId || null }); })
      .then(function (d) { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList().then(function () { return openPage(d.id); }); })
      .then(function () { $('pg-title').focus(); })
      .catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
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
    $('pg-trash').hidden = true;
    if (PK.id) closePeek();
    return _origOpen(id).then(function () {
      if (S.cur) { S.loading = true; decorateLinks(); S.loading = false; renderTags(); loadBacklinks(S.cur.id); rememberRecent(S.cur.id); archiveBar(); applyLock(); }
      var bm = location.hash.match(/^#(\d+):(.+)$/); if (bm && S.cur && +bm[1] === S.cur.id) setTimeout(function () { showBlock(decodeURIComponent(bm[2])); }, 150);
      if (EMBED && S.cur && !S.cur.title) $('pg-title').focus();      // neue Karte im Peek: gleich den Namen tippen
      if (S.editor) return S.editor.save().then(function (o) { S.lastBlocks = JSON.stringify(o.blocks); });
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
    var btn = this;
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
      if (HK.immichPick) m.appendChild(HK.menuItem(T('📷 Aus Immich …'), function () {
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
      m.appendChild(HK.menuItem(c.pinned ? T('Von Favoriten lösen') : T('Zu Favoriten'), function () { setPinned(!c.pinned); }, { icon: c.pinned ? '☆' : '★' }));
      m.appendChild(HK.menuItem(T('Volle Breite'), function () { setWide(!c.wide); }, { icon: '↔', on: !!c.wide }));
      m.appendChild(HK.menuItem(c.locked ? T('Seite entsperren') : T('Seite sperren'), function () { setLocked(!c.locked); }, { icon: '🔒', on: !!c.locked }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(T('Duplizieren'), duplicatePage, { icon: '⧉' }));
      m.appendChild(HK.menuItem(T('Unterseite hinzufügen'), function () { newPage(c.id); }, { icon: '＋' }));
      m.appendChild(HK.menuItem(T('Verschieben nach'), null, { icon: '↪', subSearch: T('Seite suchen …'), sub: function (sm) {
        var blocked = descendants(c.id).concat([c.id]);
        if (c.parent_id) sm.appendChild(HK.menuItem(T('Oberste Ebene'), function () { movePageTo(c.id, null, children(null).length); }, { icon: '⌂' }));
        S.pages.filter(function (p) { return blocked.indexOf(p.id) < 0 && p.id !== c.parent_id && p.kind !== 'database'; }).forEach(function (p) {
          sm.appendChild(HK.menuItem(label(p), function () { movePageTo(c.id, p.id, children(p.id).length); },
            { icon: ti(p.icon) || '📄', hint: ancestors(p.id).map(label).join(' / ') }));
        });
      } }));
      m.appendChild(HK.menuItem(c.template ? T('Keine Vorlage mehr') : T('Als Vorlage markieren'), function () { setTemplate(!c.template); }, { icon: '📐', on: !!c.template }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem(T('Versionsverlauf'), openVersions, { icon: '🕘' }));
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
          if (h.snippet) b.appendChild(el('span', 'pg-hit-s', h.snippet));
          b.addEventListener('click', function () { $('pg-search').value = ''; ul.hidden = true; $('pg-tree').hidden = false; openPage(h.id); });
          var li = el('li'); li.appendChild(b); ul.appendChild(li);
        });
      }).catch(function () {});
    }, 250);
  });

  $('pg-new').addEventListener('click', function () { newPageMenu(this, null); });
  $('pg-empty-new').addEventListener('click', function () { newPageMenu(this, null); });
  window.addEventListener('pagehide', function () { save(); });
  document.addEventListener('visibilitychange', function () { if (document.hidden) save(); });


  // --- Tags -----------------------------------------------------------------
  S.tagFilter = null;
  function allTags() {
    var m = {};
    S.pages.forEach(function (p) { (p.tags || []).forEach(function (t) { var k = t.toLowerCase(); (m[k] = m[k] || { name: t, n: 0 }).n++; }); });
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
    var hits = S.pages.filter(function (p) { return (p.tags || []).some(function (t) { return t.toLowerCase() === f; }); });
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
    (S.cur.tags || []).forEach(function (t) {
      var c = el('span', 'tag', '#' + t);
      var x = el('button', null, '×'); x.type = 'button'; x.setAttribute('aria-label', T('Tag {tag} entfernen', { tag: t }));
      x.addEventListener('click', function () { setTags(S.cur.tags.filter(function (y) { return y !== t; })); });
      c.appendChild(x); box.appendChild(c);
    });
    var add = el('button', 'tag-add', T('+ Tag')); add.type = 'button';
    add.addEventListener('click', function () {
      var inp = el('input', 'tag-input'); inp.type = 'text'; inp.maxLength = 30; inp.placeholder = T('Tag …');
      inp.setAttribute('list', 'pg-tag-list'); inp.setAttribute('aria-label', T('Neuer Tag'));
      add.replaceWith(inp); inp.focus();
      var done = false;
      function finish(ok) {
        if (done) return; done = true;
        var v = inp.value.trim().replace(/^#/, '');
        if (ok && v) setTags((S.cur.tags || []).concat([v])); else renderTags();
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
  var NOTE_HREF = /^\/notes#([1-9][0-9]{0,8})$/;
  function normUrl(u) { return String(u || '').replace(/\/+$/, ''); }
  function serviceFor(href) { var n = normUrl(href); return S.services.find(function (s) { return normUrl(s.url) === n; }) || null; }

  // Nach dem Laden: Verweise als Marken darstellen, Seitentitel aktualisieren, tote Ziele markieren
  function decorateLinks(root) {
    [].forEach.call((root || $('pg-editor')).querySelectorAll('a[href]'), function (a) {
      var m = (a.getAttribute('href') || '').match(NOTE_HREF);
      if (m) {
        var p = byId(+m[1]);
        a.classList.add('note-link');
        a.classList.toggle('broken', !p);
        a.title = p ? T('Zur Seite {name}', { name: label(p) }) : T('Seite nicht gefunden');
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
      if (!byId(+m[1])) { HK.toast(T('Diese Seite gibt es nicht mehr.')); return; }
      if (mod) window.open('/notes#' + m[1], '_blank', 'noopener'); else location.hash = '#' + m[1];
    } else if (a.classList.contains('svc-link') || mod) {
      e.preventDefault(); window.open(href, '_blank', 'noopener');
    }
  }
  $('pg-editor').addEventListener('click', linkClick, true);

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
    var pages = S.pages.filter(function (p) { return (!S.cur || p.id !== S.cur.id) && (!q || label(p).toLowerCase().indexOf(q) >= 0); }).slice(0, 8);
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
  function loadBacklinks(id) {
    var sec = $('pg-backlinks'), ul = $('pg-backlinks-list');
    api('GET', '/api/notes/' + id + '/backlinks').then(function (list) {
      if (!S.cur || S.cur.id !== id) return;
      ul.textContent = ''; sec.hidden = !list.length;
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
  function openModal(title, build) {
    $('pg-modal-title').textContent = title; var body = $('pg-modal-body'); body.textContent = '';
    build(body); $('pg-modal').hidden = false; $('pg-modal-close').focus();
  }
  function closeModal() { $('pg-modal').hidden = true; }
  $('pg-modal-close').addEventListener('click', closeModal);
  $('pg-modal').addEventListener('mousedown', function (e) { if (e.target === this) closeModal(); });

  var fVer = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function openVersions() {
    var id = S.cur.id;
    save().then(function () { return api('GET', '/api/notes/' + id + '/versions'); }).then(function (list) {
      openModal(T('Versionsverlauf'), function (body) {
        if (!list.length) { body.appendChild(el('p', 'nt-empty', T('Noch keine früheren Versionen. Beim Bearbeiten wird höchstens alle 10 Minuten ein Stand gesichert (bis zu 50 pro Seite).'))); return; }
        var wrap = el('div', 'ver'), ul = el('ul', 'ver-list'), prev = el('div', 'ver-prev');
        prev.appendChild(el('p', 'nt-empty', T('Version auswählen, um sie anzusehen.')));
        list.forEach(function (v) {
          var b = el('button', 'ver-item'); b.type = 'button';
          b.appendChild(el('span', 'ver-date', fVer.format(new Date(v.created * 1000))));
          b.appendChild(el('span', 'ver-title', v.title || T('Unbenannt')));
          b.addEventListener('click', function () {
            [].forEach.call(ul.querySelectorAll('.ver-item'), function (x) { x.classList.toggle('on', x === b); });
            api('GET', '/api/notes/' + id + '/versions/' + v.id).then(function (d) {
              prev.textContent = '';
              prev.appendChild(el('div', 'ver-prev-title', d.title || T('Unbenannt')));
              prev.appendChild(el('div', 'ver-prev-text', d.text || T('(leer)')));
              var r = el('button', 'btn', T('Diese Version wiederherstellen')); r.type = 'button';
              r.addEventListener('click', function () {
                api('POST', '/api/notes/' + id + '/versions/' + v.id + '/restore').then(function () {
                  closeModal(); clearTimeout(S.timer); S.dirtyBody = S.dirtyTitle = false;
                  return loadList().then(function () { return openPage(id); });
                }).then(function () { HK.toast(T('Version wiederhergestellt – der bisherige Stand ist als Version gesichert.')); })
                  .catch(function (err) { HK.toast(T('Nicht wiederhergestellt: {err}', { err: err.message })); });
              });
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
      history.replaceState(null, '', location.pathname); document.title = T('Papierkorb') + ' · ' + T('Notizen');
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
    var hits = cmdOnly ? [] : q ? S.pages.filter(function (p) { return (label(p) + ' ' + (p.tags || []).join(' ')).toLowerCase().indexOf(q) >= 0; })
                                     .sort(function (a, b) { return (rec.indexOf(b) >= 0) - (rec.indexOf(a) >= 0) || b.updated - a.updated; })
                 : rec.concat(S.pages.filter(function (p) { return rec.indexOf(p) < 0; }).sort(function (a, b) { return b.updated - a.updated; }));
    if (!q && !cmdOnly) list.appendChild(el('div', 'menu-label', rec.length ? T('Zuletzt geöffnet') : T('Zuletzt bearbeitet')));
    function item(text, sub, fn) {
      var b = el('button', 'picker-item'); b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(el('span', 'picker-t', text)); if (sub) b.appendChild(el('span', 'picker-sub', sub));
      b.addEventListener('click', function () { closeQS(); fn(); });
      qs.items.push({ b: b, fn: fn }); list.appendChild(b);
    }
    hits.slice(0, q ? 8 : 10).forEach(function (p) {
      var path = ancestors(p.id).map(label).join(' / ');
      item((ti(p.icon) || '📄') + ' ' + label(p), path, function () { openPage(p.id); });
    });
    var acts = actions().filter(function (a) { return !q || (a.t + ' ' + (a.kw || '')).toLowerCase().indexOf(q) >= 0; });
    if (acts.length && (q || cmdOnly)) {
      list.appendChild(el('div', 'menu-label', T('Befehle')));
      acts.slice(0, cmdOnly ? 30 : 6).forEach(function (a) { item(a.ic + ' ' + a.t, a.kbd || '', a.fn); });
    } else if (!q) list.appendChild(el('div', 'pg-qs-hint', T('Tipp: „>“ zeigt alle Befehle')));
    if (q && !cmdOnly) item(T('+ Neue Seite „{name}“', { name: $('pg-qs-q').value.trim() }), '', function () {
      api('POST', '/api/notes', { title: $('pg-qs-q').value.trim() }).then(function (d) { return loadList().then(function () { return openPage(d.id); }); });
    });
    if (!qs.items.length) list.appendChild(el('div', 'pg-noresult', T('Nichts gefunden.')));
    qs.items.forEach(function (it, i) { it.b.classList.toggle('on', i === 0); });
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
    else if (e.key === 'Escape' && !$('pg-modal').hidden) closeModal();
  });


  // --- Inline-Werkzeug "Datum": markierten Text ("morgen", "Freitag", "12.10.") zum Datums-Chip machen.
  // Meldet Editor.js ausserdem <time datetime> an - sonst wuerden @-Daten beim Speichern entfernt.
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
    var d = el('div', cls); d.contentEditable = 'true'; d.innerHTML = html || ''; d.dataset.placeholder = ph; return d;
  }
  // Ausklappbar (Toggle) mit echten Bloecken im Inhalt (eigener Editor, wie Spalten)
  function ToggleTool(o) {
    var d = o.data || {};
    this.title = d.title || ''; this.open = d.open !== false;
    // Alte Toggles (nur Text) werden beim Oeffnen umgewandelt: Text -> erster Absatz
    this.blocks = Array.isArray(d.blocks) ? d.blocks : (d.text ? [{ type: 'paragraph', data: { text: d.text } }] : []);
    this.ed = null;
  }
  ToggleTool.toolbox = { title: T('Ausklappbar'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>' };
  // Bewusst KEINE sanitize-Regel: Editor.js bereinigt sonst alle nicht genannten Felder mit leerer Regel und
  // entfernt die Formatierung im gesamten verschachtelten Inhalt. Den Titel bereinigt der Server (Positivliste).
  ToggleTool.prototype.render = function () {
    var self = this, w = el('div', 'tgl' + (this.open ? ' open' : '')), head = el('div', 'tgl-head');
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
    this.w = w; if (this.open) this.ensure();          // zugeklappt: Editor erst beim ersten Aufklappen (spart Ladezeit)
    return w;
  };
  ToggleTool.prototype.ensure = function () { if (!this.ed) this.ed = nestedEditor(this.b, this.blocks, T("Inhalt – '/' für Blöcke")); return this.ed; };
  ToggleTool.prototype.save = function () {
    var self = this, res = { title: this.t.innerHTML, open: this.w.classList.contains('open') };
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
      }).catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
  }
  function fromTemplate(p, parentId) {
    return save().then(function () { return api('POST', '/api/notes/' + p.id + '/duplicate', { parent_id: parentId || null }); })
      .then(function (d) { if (parentId) { S.open[parentId] = true; saveOpen(); } return loadList().then(function () { return openPage(d.id); }); })
      .then(function () { $('pg-title').focus(); $('pg-title').select(); })
      .catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
  }
  function newPageMenu(anchor, parentId) {
    HK.openMenu(anchor, function (m) {
      m.appendChild(HK.menuItem(T('Leere Seite'), function () { newPage(parentId); }, { icon: '📄' }));
      m.appendChild(HK.menuItem(T('Datenbank'), function () { newDatabase(parentId); }, { icon: '🗃️', hint: T('Tabelle, Board …') }));
      m.appendChild(HK.menuLabel(T('Vorlagen')));
      BUILTIN.forEach(function (t) { m.appendChild(HK.menuItem(t.name, function () { fromBuiltin(t, parentId); }, { icon: t.icon })); });
      S.pages.filter(function (p) { return p.template; }).forEach(function (p) {
        m.appendChild(HK.menuItem(label(p), function () { fromTemplate(p, parentId); }, { icon: ti(p.icon) || '📄', hint: T('eigene') }));
      });
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
  function relRows(target) { return S.pages.filter(function (p) { return p.parent_id === target; }); }
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
  var VIEW_ICON = { table: '▦', board: '▤', list: '☰', gallery: '▣', calendar: '🗓' };
  // Automatische Eigenschaften (nur lesbar) und Zahlen-Darstellung
  TYPES.created = [T('Erstellt am'), '⏱']; TYPES.edited = [T('Zuletzt bearbeitet'), '✎'];
  var AUTO = { created: 'created', edited: 'updated' };
  OPS.created = OPS.edited = [['after', T('nach')], ['before', T('vor')], ['eq', T('am')], ['today', T('ist heute')], ['this_week', T('ist diese Woche')], ['this_month', T('ist diesen Monat')]];
  function propValue(p, r) { return p.type === 'calc' ? calcValue2(p, r) : AUTO[p.type] ? r[AUTO[p.type]] : (r.props || {})[p.id]; }
  function numLike(p) { return !!p && (p.type === 'number' || p.type === 'calc'); }
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

  // --- Datenbank-Ansicht als Baustein: jede Instanz hat ihren EIGENEN Zustand (Seite, eingebettet, Zeilen-Eigenschaften)
  function makeDbView(BOX, OPTS) {
    OPTS = OPTS || {};
    var DB = { id: null, schema: { props: [] }, rows: [] };
    function locked() { return !!DB.schema.locked; }
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
      if (DB.localViews && OPTS.saveViews) OPTS.saveViews(DB.localViews);        // Block-Ansichten -> Seiteninhalt
      var snap = JSON.stringify(DB.schema);
      if (snap === DB.snap) return Promise.resolve();                            // Datenbank selbst unveraendert
      return api('PATCH', '/api/notes/' + DB.id, { schema: DB.schema }).then(function (d) { DB.snap = snap; return d; })
        .catch(function (err) { HK.toast(T('Spalte nicht gespeichert: {err}', { err: err.message })); throw err; });
    }
    function setProp(row, pid, value) {
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
                .then(function (d) { return loadList().then(function () { cur.push(d.id); HK.closeMenu(); return setProp(row, prop.id, cur); }); })
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
      buildInput(); return wrap;
      function buildInput() {
      var inp = el('input', 'val-in'); inp.setAttribute('aria-label', prop.name);
      inp.type = t === 'number' ? 'number' : t === 'date' ? 'date' : t === 'url' ? 'url' : 'text';
      if (t === 'number') inp.step = 'any';
      inp.value = v == null ? '' : String(v); inp.placeholder = t === 'url' ? 'https://…' : '';
      function markEmpty() { inp.classList.toggle('is-empty', !inp.value); }
      markEmpty(); inp.addEventListener('input', markEmpty);
      var start = inp.value;
      function commit() {
        if (inp.value === start) { if (t === 'number' && start !== '' && (prop.fmt || prop.show)) onDone(); return; }   // zurueck zur Anzeige
        var nv = inp.value.trim(); start = inp.value;
        setProp(row, prop.id, nv === '' ? null : (t === 'number' ? Number(nv) : nv)).then(onDone);
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

    function newRow(props) {
      if (props instanceof Event) props = null;
      return api('POST', '/api/notes', { parent_id: DB.id, title: '', props: props || undefined }).then(function (d) {
        DB.rows.push({ id: d.id, title: '', icon: '', props: props || {}, updated: d.updated, created: d.updated });
        renderDb();
        var inp = BOX.querySelector('tr[data-id="' + d.id + '"] .db-title-in'); if (inp) inp.focus();
        if (curView().type === 'board') {     // Board: neue Karte direkt als Seite oeffnen, Cursor im Titel
          return loadList().then(function () { return openRowUI(d.id); })
            .then(function () { if ((curView().open || 'side') === 'page') $('pg-title').focus(); });   // im Peek setzt die Seite selbst den Cursor
        }
        return loadList();
      }).catch(function (err) { HK.toast(T('Zeile nicht angelegt: {err}', { err: err.message })); });
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
      function upd() {
        sc.classList.toggle('sc-l', sc.scrollLeft > 1);
        sc.classList.toggle('sc-r', sc.scrollLeft + sc.clientWidth < sc.scrollWidth - 1);
      }
      sc.addEventListener('scroll', upd, { passive: true });
      if (window.ResizeObserver) new ResizeObserver(upd).observe(sc);
      requestAnimationFrame(upd);
    }

    function renderDb() {
      var box = BOX; box.textContent = '';
      if (OPTS.embedded) box.appendChild(embedHead());
      box.appendChild(viewBar());
      var vt = curView().type;
      if (vt === 'board') { box.appendChild(renderBoard()); return; }
      if (vt === 'list') { box.appendChild(renderList()); return; }
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
        if (cw) { shrink(keys.filter(function (k) { return k !== 'title'; }), 90); shrink(['title'], 150); }
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
      ab.setAttribute('aria-label', T('Spalte hinzufügen')); ab.addEventListener('click', function () { addColumn(ab); }); if (!locked()) add.appendChild(ab); hr.appendChild(add);
      thead.appendChild(hr); tb.appendChild(thead);
      var body = el('tbody');
      eachGrouped(sortedRows(), function (o, n, g) {
        var gr = el('tr', 'db-grow'), gd = el('td'); gd.colSpan = vis.length + 2;
        gd.appendChild(o ? optChip(o) : el('span', 'board-none', T('Ohne {name}', { name: g.name }))); gd.appendChild(el('span', 'board-n', String(n)));
        gr.appendChild(gd); body.appendChild(gr);
      }, function (r) {
        var tr = tint(el('tr', 'db-row'), r); tr.dataset.id = r.id;
        var td = el('td', 'db-cell db-title'), ti = el('textarea', 'db-title-in'); ti.rows = 1; ti.maxLength = 200; ti.value = r.title; ti.placeholder = T('Unbenannt');   // Textfeld: lange Namen brechen um
        ti.setAttribute('aria-label', T('Name'));
        var start = r.title;
        function commitTitle() {
          if (ti.value === start) return; start = ti.value;
          api('PATCH', '/api/notes/' + r.id, { title: ti.value }).then(function () { r.title = ti.value.trim(); var p = byId(r.id); if (p) p.title = r.title; })
            .catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
        }
        ti.addEventListener('blur', commitTitle);
        ti.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); ti.blur(); } });
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
        body.appendChild(tr);
      });
      tb.appendChild(body);
      var tf = el('tfoot'), fr = el('tr'), fd = el('td'); fd.colSpan = vis.length + 2;
      var nr = el('button', 'db-new-row', T('+ Neu')); nr.type = 'button'; nr.addEventListener('click', newRow);
      fd.appendChild(nr); fr.appendChild(fd); tf.appendChild(fr); tf.appendChild(calcRow(vis, tv)); tb.appendChild(tf);
      scroll.appendChild(tb); box.appendChild(scroll); scrollHints(scroll);
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
      var vals = rows.map(function (r) { var v = p ? propValue(p, r) : r.title; if (p && AUTO[p.type] && v) v = isoDay(v); return v; });
      function isEmpty(v) { return v == null || v === '' || v === false || (Array.isArray(v) && !v.length); }
      var n = vals.length, filled = vals.filter(function (v) { return !isEmpty(v); });
      var nums = vals.filter(function (v) { return typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !isNaN(Number(v))); }).map(Number);
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
          if (viewsLocked()) {
            box.appendChild(el('div', 'menu-hint vs-locked', T('🔒 Diese Datenbank ist gesperrt: Spalten, Ansichten, Filter und Sortierung sind geschützt. Einträge kannst du weiter bearbeiten.')));
            var ul = el('button', 'menu-item', T('🔓 Entsperren')); ul.type = 'button';
            ul.addEventListener('click', function () { HK.closeMenu(); setLocked(false); }); box.appendChild(ul);
            return;
          }
          var name = el('input', 'flt-val'); name.type = 'text'; name.maxLength = 40; name.value = v.name;
          liveText(name, function (val) { if (val.trim()) { v.name = val.trim(); saveSchemaSoon(); renderDb(); } });
          row(T('Name'), name);
          row(T('Layout'), sel([['table', '▦ ' + T('Tabelle')], ['board', '▤ ' + T('Board')], ['list', '☰ ' + T('Liste')], ['gallery', '▣ ' + T('Galerie')], ['calendar', '🗓 ' + T('Kalender')]], v.type, function (x) { v.type = x; commit(); }));
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
                var vi = el('input', 'flt-val'); vi.type = (pp.type === 'date' || AUTO[pp.type]) ? 'date' : numLike(pp) ? 'number' : 'text';
                vi.value = f.value || ''; vi.setAttribute('aria-label', T('Wert'));
                liveText(vi, function (x) { f.value = x; saveSchemaSoon(); renderDb(); }); l.appendChild(vi);
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
          lb.addEventListener('click', function () { HK.closeMenu(); setLocked(!locked()); }); box.appendChild(lb);
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
                val = el('input', 'flt-val'); val.type = (p.type === 'date' || AUTO[p.type]) ? 'date' : numLike(p) ? 'number' : 'text';
                if (numLike(p)) val.step = 'any';
              }
              val.value = f.value || ''; val.setAttribute('aria-label', T('Wert'));
              if (val.tagName === 'SELECT') val.addEventListener('change', function () { f.value = val.value; commit(); });
              else liveText(val, function (x) { f.value = x; saveSchemaSoon(); renderDb(); });
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
        m.appendChild(HK.menuItem(T('Board'), function () { v.type = 'board'; saveSchema().then(renderDb); }, { icon: '▤', on: v.type === 'board' }));
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
        [['table', '▦', T('Tabelle')], ['board', '▤', T('Board')], ['list', '☰', T('Liste')], ['gallery', '▣', T('Galerie')], ['calendar', '🗓', T('Kalender')]].forEach(function (t) {
          m.appendChild(HK.menuItem(t[2], function () {
            var v = { id: rid(), name: t[2], type: t[0] };
            var s = DB.schema.props.find(function (p) { return p.type === 'select'; }); if (t[0] === 'board' && s) v.group_by = s.id;
          var dd = DB.schema.props.find(function (p) { return p.type === 'date'; }); if (t[0] === 'calendar' && dd) v.date_by = dd.id;
            views().push(v); setViewId(v.id); saveSchema().then(renderDb);
          }, { icon: t[1].charAt(0) }));
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
      pl.addEventListener('click', function () { addView(pl); }); if (!viewsLocked()) tabs.appendChild(pl);
      bar.appendChild(tabs);
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
        lk.addEventListener('click', function () { setLocked(false); }); bar.appendChild(lk);
      }
      var gb = el('button', 'db-chip db-gear', T('⚙ Ansicht')); gb.type = 'button'; gb.title = T('Ansicht-Einstellungen');
      gb.addEventListener('click', function () { viewSettings(gb); }); bar.appendChild(gb);
      var nb = el('button', 'btn db-new-top', T('+ Neu')); nb.type = 'button'; bar.appendChild(nb);
      nb.addEventListener('click', function () {
        if (!(DB.templates || []).length) return newRow();
        HK.openMenu(nb, function (m) {                // Notion: "Neu ▾" mit den Vorlagen dieser Datenbank
          m.appendChild(HK.menuItem(T('Leerer Eintrag'), function () { newRow(); }, { icon: '📄' }));
          m.appendChild(HK.menuLabel(T('Vorlagen')));
          DB.templates.forEach(function (t) {
            m.appendChild(HK.menuItem(t.title || T('Vorlage'), function () { newFromTemplate(t.id); }, { icon: ti(t.icon) || '📐' }));
          });
          m.appendChild(el('div', 'menu-hint', T('Vorlage bearbeiten: im Seitenbaum unter der Datenbank öffnen')));
        });
      });
      if ((DB.templates || []).length) nb.textContent = T('+ Neu') + ' ▾';
      return bar;
    }

    var dragId = null;
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
        var list = el('div', 'board-cards'); col.rows.forEach(function (r) { list.appendChild(boardCard(r, g)); }); c.appendChild(list);
        var add = el('button', 'board-add', T('+ Neu')); add.type = 'button';
        add.addEventListener('click', function () { var pr = {}; if (col.o) pr[g.id] = col.o.id; newRow(pr); });
        c.appendChild(add);
        c.addEventListener('dragover', function (e) { if (dragId) { e.preventDefault(); c.classList.add('over'); } });
        c.addEventListener('dragleave', function (e) { if (!c.contains(e.relatedTarget)) c.classList.remove('over'); });
        c.addEventListener('drop', function (e) {
          e.preventDefault(); c.classList.remove('over');
          var r = DB.rows.find(function (x) { return x.id === dragId; }); dragId = null; if (!r) return;
          var nv = col.o ? col.o.id : null; if ((r.props[g.id] || null) === nv) return;
          setProp(r, g.id, nv).then(renderDb);
        });
        wrap.appendChild(c);
      });
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
      var wrap = el('div', 'dbl');
      eachGrouped(sortedRows(), function (o, n, g) {
        var h = el('div', 'dbl-group'); h.appendChild(o ? optChip(o) : el('span', 'board-none', T('Ohne {name}', { name: g.name }))); h.appendChild(el('span', 'board-n', String(n)));
        wrap.appendChild(h);
      }, function (r) {
        var line = tint(el('div', 'dbl-row'), r); line.tabIndex = 0; line.setAttribute('role', 'button');
        var left = el('div', 'dbl-left'); left.appendChild(icoNode(r.icon, '📄', 'dbl-ic')); left.appendChild(el('span', 'dbl-title', r.title || T('Unbenannt')));
        var right = el('div', 'dbl-props');
        visibleProps().forEach(function (p) { var n = valueDisplay(p, r); if (n) right.appendChild(n); });
        line.appendChild(left); line.appendChild(right);
        line.addEventListener('click', openRow(r)); line.addEventListener('keydown', function (e) { if (e.key === 'Enter') openRowUI(r.id); });
        wrap.appendChild(line);
      });
      var nb = el('button', 'db-new-row', T('+ Neu')); nb.type = 'button'; nb.addEventListener('click', function () { newRow(); }); wrap.appendChild(nb);
      return wrap;
    }
    function renderGallery() {
      var g = el('div', 'dbg' + (curView().card ? ' dbg-' + curView().card : ''));
      sortedRows().forEach(function (r) {
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
        g.appendChild(c);
      });
      var nb = el('button', 'dbg-new', T('+ Neu')); nb.type = 'button'; nb.addEventListener('click', function () { newRow(); }); g.appendChild(nb);
      return g;
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
  var LIVE_DBS = [];
  function refreshDbViews(dbId) {          // alle sichtbaren Ansichten dieser Datenbank (Seite + eingebettete) neu laden
    LIVE_DBS = LIVE_DBS.filter(function (v) { return v.DB && v.render && document.body.contains(v.boxEl || document.body); });
    LIVE_DBS.forEach(function (v) { if (v.DB.id === dbId) v.reload(); });
  }

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
  function isImg(icon) { return typeof icon === 'string' && icon.indexOf('/files/') === 0; }
  // eigene Bilder verkleinert laden (Server erzeugt WebP-Vorschau einmal und merkt sie sich)
  function thumb(u, w) { return typeof u === 'string' && /^\/files\/[0-9a-f]{24}$/.test(u) ? u + '?w=' + w : u; }
  function ti(icon) { return isImg(icon) ? '' : (icon || ''); }          // Icon fuer reine Textstellen
  function icoNode(icon, fallback, cls) {                                 // Icon als Element (Emoji oder Bild)
    if (isImg(icon)) { var i = el('img', (cls || '') + ' ic-img'); i.decoding = 'async'; i.src = thumb(icon, 160); i.alt = ''; return i; }
    return el('span', cls || '', icon || fallback || '');
  }
  function showIcon(icon) {
    var b = $('pg-icon'); b.textContent = '';
    if (isImg(icon)) b.appendChild(icoNode(icon, '', 'page-icon-img')); else b.textContent = icon || '';
    b.classList.toggle('empty', !icon);
  }
  function uploadImage(file) {
    var fd = new FormData(); fd.append('image', file);
    return fetch('/api/files', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1' }, body: fd })
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
    if (!HK.immichPick) return pickImage('cover');
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
    var self = this, w = this.w; w.textContent = '';
    this.buttons.forEach(function (b, i) {
      var g = el('div', 'nbtn-wrap');
      var btn = el('button', 'nbtn'); btn.type = 'button';
      if (b.icon) btn.appendChild(el('span', 'nbtn-ic', b.icon));
      btn.appendChild(el('span', null, b.label || T('Schaltfläche')));
      btn.addEventListener('click', function () { runButton(b, function () { btnConfig(btn, self, i); }); });
      var cfg = el('button', 'nbtn-cfg', '⚙'); cfg.type = 'button'; cfg.title = T('Schaltfläche einrichten'); cfg.setAttribute('aria-label', T('Schaltfläche einrichten'));
      cfg.addEventListener('click', function () { btnConfig(cfg, self, i); });
      g.appendChild(btn); g.appendChild(cfg); w.appendChild(g);
    });
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
      var req = b.tpl ? api('POST', '/api/notes/' + b.tpl + '/duplicate', title ? { parent_id: b.target, title: title } : { parent_id: b.target })
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
  function DatabaseTool(o) { var d = o.data || {}; this.data = { db: d.db || null, view: d.view || null, views: Array.isArray(d.views) ? d.views : null }; }
  DatabaseTool.toolbox = { title: T('Datenbank'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>' };
  DatabaseTool.prototype.render = function () { this.w = el('div', 'embed-db'); isolate(this.w); this.draw(); return this.w; };
  DatabaseTool.prototype.mark = function () { if (!S.loading) { S.dirtyBody = true; schedule(); } };
  DatabaseTool.prototype.draw = function () {
    var self = this, w = this.w; w.textContent = '';
    var meta = this.data.db && byId(this.data.db);
    if (!meta || meta.kind !== 'database') {
      var pick = el('div', 'embed-pick');
      pick.appendChild(el('span', 'embed-pick-t', this.data.db ? T('Die eingebettete Datenbank gibt es nicht mehr.') : T('🗃️ Datenbank einbetten:')));
      var dbs = S.pages.filter(function (p) { return p.kind === 'database'; });
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
      saveViews: function (vs) { self.data.views = JSON.parse(JSON.stringify(vs)); self.mark(); } });
    this.inst.load(this.data.db);
  };
  DatabaseTool.prototype.save = function () { return { db: this.data.db, view: this.data.view, views: this.data.views }; };

  // Block "Kalender": Termine der verbundenen externen Kalender - Monat oder Liste (naechste 14 Tage, wie in der App)
  function CalendarTool(o) {
    var d = (o && o.data) || {};
    this.block = o && o.block; this.view = d.view === 'list' ? 'list' : 'month'; this.days = Math.max(1, Math.min(62, +d.days || 14));
  }
  CalendarTool.toolbox = { title: T('Kalender'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>' };
  CalendarTool.prototype.render = function () { this.w = el('div', 'embed-cal'); isolate(this.w); this.draw(); return this.w; };
  CalendarTool.prototype.draw = function () {
    var self = this, w = this.w; w.textContent = '';
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

  // "/Einbetten": YouTube als Player (youtube-nocookie), alle anderen Links als Karte (GoodNotes, Webseiten ...)
  function ytId(u) {
    var m = String(u || '').match(/(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})/);
    return m ? m[1] : null;
  }
  function decodeAmp(s) { return String(s || '').replace(/&amp;/g, '&'); }          // der Server speichert & als &amp;
  function EmbedTool(o) {
    var d = o.data || {}; this.api = o.api; this.block = o.block; this.ro = !!o.readOnly; this.title = decodeAmp(d.title);
    this.url = /^https?:\/\/\S+$/i.test(decodeAmp(d.url)) ? decodeAmp(d.url) : '';      // nur http(s) - nie javascript: o. ae.
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
    } else {
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
  EmbedTool.prototype.onPaste = function (e) { this.url = e.detail.data; if (this.w) this.draw(); this.changed(); };
  EmbedTool.prototype.save = function () { var d = { url: this.url }; if (this.title) d.title = this.title; if (ytId(this.url)) d.kind = 'youtube'; return d; };
  EmbedTool.prototype.validate = function (d) { return /^https?:\/\//i.test(d.url || ''); };

  var fTaskDue = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
  function TasksTool() {}
  TasksTool.toolbox = { title: T('Aufgaben'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l2 2 4-4M4 14l2 2 4-4M13 7h7M13 15h7"/></svg>' };
  TasksTool.prototype.render = function () { this.w = el('div', 'embed-tasks'); isolate(this.w); this.draw(); return this.w; };
  TasksTool.prototype.draw = function () {
    var self = this, w = this.w;
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
            if (r && r.next_due) HK.toast(T('Erledigt. Nächstes Mal: {date}', { date: fTaskDue.format(new Date(r.next_due + 'T00:00:00')) }));
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
  if (document.body.classList.contains('notes-page') && !document.body.classList.contains('embed')) {
    setSide(HK.store('hk-pg-side') !== '0');
    $('pg-side-btn').addEventListener('click', function () { setSide(document.body.classList.contains('side-hidden')); });
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === '\\' || e.code === 'Backslash')) { e.preventDefault(); setSide(document.body.classList.contains('side-hidden')); }
    });
  }
  function setWide(on) {
    var id = S.cur.id;
    api('PATCH', '/api/notes/' + id, { wide: on }).then(function () {
      if (S.cur && S.cur.id === id) { S.cur.wide = on; $('page').classList.toggle('wide', on); }
    }).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }

  // --- Spalten: jede Spalte ist ein eigener Editor (Editor-in-Editor) ----------------
  function nestedTools() { var t = {}; Object.keys(S.tools || {}).forEach(function (k) { if (k !== 'columns') t[k] = S.tools[k]; }); return t; }
  function nestedEditor(holder, blocks, placeholder) {
    var ed = new window.EditorJS({
      holder: holder, data: { blocks: blocks || [] }, tools: nestedTools(), i18n: I18N, minHeight: 24, placeholder: placeholder,
      inlineToolbar: ['bold', 'italic', 'link', 'marker', 'inlineCode', 'hkdate'],
      onChange: function () { if (!S.loading) { S.dirtyBody = true; schedule(); } }
    });
    holder.addEventListener('input', function () { if (!S.loading) tryShortcut(ed); });   // Markdown-Kuerzel im eigenen Editor
    S.eds = (S.eds || []).filter(function (x) { return x.holder.isConnected; }); S.eds.push({ ed: ed, holder: holder });   // fuer Tastenkuerzel
    ed.isReady.then(function () { decorateLinks(); });                                       // Verweise als Marken
    return ed;
  }
  var COLS = new WeakMap();                      // Spalten-Element -> Werkzeug (fuer Zusammenfuehren)
  function ColumnsTool(o) {
    var d = o.data || {};
    this.cols = (d.cols && d.cols.length ? d.cols : [{ blocks: [] }, { blocks: [] }]).slice(0, 4);
    this.editors = [];
    this.api = o.api;
  }
  ColumnsTool.toolbox = { title: T('Spalten'),
    icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></svg>' };
  ColumnsTool.prototype.render = function () {
    var self = this, w = el('div', 'cols'); this.w = w; COLS.set(w, this);
    w.style.setProperty('--cols', String(this.cols.length));
    isolate(w);                    // Tastatur der Spalten-Editoren nicht an den aeusseren Editor weiterreichen
    this.cols.forEach(function (c, i) { self.editors[i] = self.column(c, i); });
    this.ctl();
    return w;
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
    this.ctl(); S.dirtyBody = true; schedule();
  };
  // Eine Spalte: eigener Editor + "+ Block" darunter (in GENAU dieser Spalte weiterschreiben, wie Notion)
  ColumnsTool.prototype.column = function (c, i) {
    var col = el('div', 'col'), h = el('div', 'col-ed'), add = el('button', 'col-add', T('+ Block'));
    add.type = 'button'; add.title = T('Neuer Block am Ende dieser Spalte');
    col.appendChild(h); col.appendChild(add); this.w.appendChild(col);
    var ed = nestedEditor(h, c.blocks, i ? T('Spalte …') : T("Spalte – '/' für Blöcke"));
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
  ColumnsTool.prototype.destroy = function () { this.editors.forEach(function (ed) { try { ed.destroy(); } catch (x) { /* schon weg */ } }); };
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
      self.ctl(); S.dirtyBody = true; schedule();
    });
  };
  ColumnsTool.prototype.save = function () {
    return Promise.all(this.editors.map(function (e) { return e.isReady.then(function () { return e.save(); }); }))
      .then(function (outs) { return { cols: outs.map(function (o) { return { blocks: o.blocks }; }) }; });
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
  function anyById(id) { return (S.all || S.pages).find(function (p) { return p.id === id; }) || null; }
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
  function applyLock() {
    var on = !!(S.cur && S.cur.locked);
    document.body.classList.toggle('pg-locked', on);
    $('pg-title').readOnly = on;
    var b = $('pg-lock');
    if (!b) {
      b = el('button', 'pg-lock-b'); b.id = 'pg-lock'; b.type = 'button';
      b.addEventListener('click', function () { setLocked(false); });
      $('pg-state').parentNode.insertBefore(b, $('pg-state'));
    }
    b.hidden = !on; b.textContent = T('🔒 Gesperrt'); b.title = T('Seite ist gesperrt – klicken zum Entsperren');
  }
  var LOCK_OK = '.embed-db, .embed-cal, .embed-tasks, .btnrow';      // bleibt auf gesperrten Seiten bedienbar
  function lockBlocked(e) {
    if (!S.cur || !S.cur.locked) return false;
    var tg = e.target; if (!tg || !tg.closest) return false;
    if (!$('pg-editor').contains(tg) || tg.closest(LOCK_OK)) return false;
    return true;
  }
  var lockHintT = 0;
  function lockHint() { var n = Date.now(); if (n - lockHintT > 2500) { lockHintT = n; HK.toast(T('🔒 Seite ist gesperrt – oben auf „Gesperrt“ klicken zum Bearbeiten')); } }
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
    if (c) {
      add(T('Neue Unterseite'), '＋', function () { newPage(c.id); }, { kw: 'subpage unterseite anlegen' });
      add(T('Link zur Seite kopieren'), '🔗', function () { copyLink(); }, { kw: 'url teilen copy link', kbd: mk + 'L' });
      add(c.pinned ? T('Von Favoriten lösen') : T('Zu Favoriten'), '★', function () { setPinned(!c.pinned); }, { kw: 'favorit stern pin' });
      add(c.wide ? T('Volle Breite aus') : T('Volle Breite ein'), '↔', function () { setWide(!c.wide); }, { kw: 'breite wide layout' });
      add(T('Vollbild'), '⤢', function () { setFull(!$('pages').classList.contains('pg-full')); }, { kw: 'fullscreen fokus' });
      add(T('Seite duplizieren'), '⧉', duplicatePage, { kw: 'kopie duplicate' });
      add(T('Versionsverlauf'), '🕘', openVersions, { kw: 'history versionen' });
      add(T('Als Markdown exportieren'), '⬇', function () { exportPage(false); }, { kw: 'export md download' });
      if (c.parent_id) add(T('Übergeordnete Seite'), '↑', goParent, { kw: 'eltern parent hoch', kbd: mk + '⇧U' });
      if (window.HKKeys) add(T('Inhaltsverzeichnis ein/aus'), '☰', HKKeys.toggleToc, { kw: 'toc gliederung ueberschriften' });
    }
    if (!EMBED) {
      add(T('Zurück'), '←', function () { history.back(); }, { kw: 'back', kbd: mk + '[' });
      add(T('Vor'), '→', function () { history.forward(); }, { kw: 'forward', kbd: mk + ']' });
      add(T('Seitenleiste ein/aus'), '◧', function () { setSide(document.body.classList.contains('side-hidden')); }, { kw: 'sidebar', kbd: mk + '\\' });
    }
    if (c) add(c.locked ? T('Seite entsperren') : T('Seite sperren'), '🔒', function () { setLocked(!c.locked); }, { kw: 'lock sperren schuetzen' });
    if (c) add(c.archived ? T('Aus dem Archiv holen') : T('Seite archivieren'), '📦', function () { setArchived(c.id, !c.archived); }, { kw: 'archiv archive' });
    add(T('Archiv anzeigen'), '📦', showArchive, { kw: 'archiv archive' });
    add(T('Papierkorb'), '🗑', showTrash, { kw: 'trash geloescht' });
    if (window.HKSettings) add(T('Einstellungen'), '⚙', function () { HKSettings.open(); }, { kw: 'settings kalender immich schluessel' });
    add(T('Übersicht (Startseite)'), '⌂', function () { location.href = '/'; }, { kw: 'home dashboard start' });
    return a;
  }

  // --- Start ----------------------------------------------------------------
  window.HKPages = {
    // Bausteine fuer static/keys.js (Tastenkuerzel, Block-Aktionen, Inhaltsverzeichnis)
    k: { cur: function () { return S.cur; }, editor: function () { return S.editor; },
         eds: function () { return (S.eds || []).filter(function (x) { return x.holder.isConnected; }); },
         open: function (id) { return openPage(id); }, byId: byId, goParent: goParent, copyLink: copyLink, showBlock: showBlock,
         embed: EMBED, isMac: isMac, openQS: function () { openQS(); }, pages: function () { return S.pages; }, label: label, ti: ti,
         recents: recents, decorate: function () { decorateLinks(); }, ancestors: ancestors },
    editor: function () { return S.editor; },   // fuer Tests/Diagnose
    flush: function () { return S.conflict ? Promise.reject(new Error('conflict')) : save(); },   // Peek: vor dem Schliessen speichern
    init: function (onCount) {
      S.onCount = onCount || S.onCount;
      var svc = api('GET', '/api/services').then(function (s) { S.services = s; }).catch(function () {});
      return Promise.all([loadList(), svc]).then(function () {
        var fromHash = parseInt(location.hash.slice(1), 10), last = +HK.store('hk-pg-last');
        var start = anyById(fromHash) || byId(last) || children(null)[0];
        if (start) return openPage(start.id);
        setLocation();
      });
    }
  };

  window.addEventListener('hashchange', function () {
    var id = parseInt(location.hash.slice(1), 10), bm = location.hash.match(/^#\d+:(.+)$/);
    if (id && S.cur && S.cur.id === id && bm) { showBlock(decodeURIComponent(bm[1])); return; }
    if (!id || (S.cur && S.cur.id === id)) return;
    // Unbekannte ID (z.B. gerade per Schaltflaeche angelegt): erst Seitenliste neu laden, dann oeffnen
    (anyById(id) ? Promise.resolve() : loadList()).then(function () { if (anyById(id)) openPage(id); });
  });
  // Eigene Notizen-Seite (/notes): selbst starten
  if (document.body.classList.contains('notes-page')) window.HKPages.init();
})();
