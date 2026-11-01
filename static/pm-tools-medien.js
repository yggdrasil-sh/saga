/* Medien im Tiptap-Editor (2.5, Tiptap-Umbau T4a): Bild, Datei, Bild aus Immich, Nextcloud-Datei, Einbetten.
 *
 * Wird von static/pm-editor.js mitgeladen (TOOL_SCRIPTS, nach pm-tools.js) und besteht aus zwei Teilen:
 *
 * 1. Werkzeuge ueber HKTools (static/pm-tools.js) - atom-Knoten mit Attribut data:
 *    - attaches   eigene Node-View (Hochladen ueber /api/files Feld "file", Titel, Groesse, Herunterladen); ersetzt das
 *                 vendor-Plugin. Unbekannte Felder bleiben (Regel 0).
 *    - nextcloud  Bruecke HKTools.fromEditorJs(NextcloudTool) - Signatur sig und alle Felder kommen unveraendert zurueck,
 *                 solange niemand die Schalter aendert (dann setzt der Server sie neu, saga/nextcloud.py check_body).
 *    - embed      Bruecke HKTools.fromEditorJs(EmbedTool) - YouTube (youtube-nocookie) mit Breite/Ausrichtung, sonst
 *                 Link-Karte.
 * 2. Erweiterung des Kern-Editors (HKPmEditor.use):
 *    - image      Knoten image(icap) mit Rich-Text-Unterschrift; Node-View mit Hochladen, Groessengriffen (width 10-100 %),
 *                 Ausrichtung/Rahmen/Hintergrund/volle Breite im Block-Menue - je eine Transaktion.
 *    - Zwischenablage: Bild-Datei einfuegen/ziehen -> Hochladen (Bild oder Datei-Block); Saga-Bloecke mit Dateiverweisen
 *      (/files/<id>) von anderer Seite -> /api/files/copy (fixRefs aus pages.js) + cleanClip; fremde Nextcloud-Bloecke
 *      abweisen; Link in leeren Absatz -> Einbetten (eigener Nextcloud-Link -> nextcloud ueber /api/nextcloud/resolve);
 *      Strg/Cmd+C/X auf ausgewaehltem Bild -> Saga-Block + HTML + image/png (clipImage aus pages.js).
 *    - Slash-Eintraege in der Gruppe "Medien" (Bild, Datei, Bild aus Immich nur fuer den Eigentuemer, Nextcloud-Datei,
 *      Einbetten). Immich ist kein Blocktyp: der Eintrag oeffnet die Auswahl und fuegt einen Bild-Block ein.
 *    - validate: neue Bloecke ohne Inhalt (Bild/Datei ohne Datei, Einbetten ohne Link, Nextcloud ohne Datei oder von
 *      einer anderen Person) werden nicht gespeichert - wie validate() der Editor.js-Werkzeuge.
 *
 * Rollen: Lesen/Kommentieren/gesperrt zeigen alles an, aber ohne Griffe, Knoepfe und Eingabefelder.
 * CSP (script-src 'self'; style-src 'self'): keine style-Attribute, Breite nur ueber CSSOM (style.setProperty).
 */
(function (root) {
  'use strict';
  var T = root.T || function (s, p) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return p && k in p ? p[k] : m; }); };
  function isDict(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function K() { return root.HKPages && root.HKPages.k; }
  function M() { var k = K(); return (k && k.media) || {}; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function toast(m) { if (root.HK && HK.toast) HK.toast(m); }
  function sizeW(v) { return typeof v === 'number' && isFinite(v) ? Math.min(100, Math.max(10, Math.round(v))) : null; }
  var ALIGNS = { left: 1, center: 1, right: 1 };
  var IMG_TYPES = /^image\/(png|jpeg|gif|webp)$/;
  var URL_RE = /^https?:\/\/[^\s]+$/i;
  var IC = {
    image: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-9 9"/></svg>',
    file: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.4 11.1l-8.5 8.5a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8"/></svg>'
  };

  // Hochladen wie die Editor.js-Werkzeuge: /api/files, Feld image|file, Kopfzeilen aus pages.js (X-Saga-Note fuer fremde Seiten)
  function upload(file, field) {
    var fd = new FormData(); fd.append(field, file, file.name || (field === 'image' ? 'bild.png' : 'datei'));
    var h = Object.assign({}, (K() && K().uph) || { 'X-Saga': '1' });
    return fetch('/api/files', { method: 'POST', credentials: 'same-origin', headers: h, body: fd }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok || !d.success || !d.file || !d.file.url) throw new Error(d.error || T('Hochladen fehlgeschlagen'));
        return d.file;
      });
    });
  }
  function pickFile(accept, cb) {
    var inp = el('input', 'pm-file-in'); inp.type = 'file'; inp.hidden = true; if (accept) inp.accept = accept;
    inp.addEventListener('change', function () { var f = inp.files && inp.files[0]; inp.remove(); if (f) cb(f); });
    inp.addEventListener('cancel', function () { inp.remove(); });
    document.body.appendChild(inp);
    inp.click();
  }

  // ===================================================================================================================
  // 1. Werkzeuge (HKTools)
  // ===================================================================================================================
  function sizeText(n) {
    if (typeof n !== 'number' || !isFinite(n) || n < 0) return '';
    var u = ['B', 'KB', 'MB', 'GB'], i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : String(n)) + ' ' + u[i];
  }
  function attachesSpec() {
    return {
      title: T('Datei'), icon: IC.file, slash: [], atom: true,
      mount: function (box, data, ctx) {
        var d = isDict(data) ? data : {}, busy = false;
        box.classList.add('pm-att-box');
        function set(patch) { var nd = Object.assign(clone(d), patch); if (ctx.change(nd)) d = nd; draw(); }
        function choose() {
          if (ctx.readOnly || busy) return;
          pickFile('', function (f) {
            busy = true; draw();
            upload(f, 'file').then(function (file) { busy = false; set({ file: file, title: file.title || file.name || '' }); })
              .catch(function (e) { busy = false; draw(); toast(e.message); });
          });
        }
        function draw() {
          box.textContent = '';
          var f = isDict(d.file) ? d.file : null, url = f && typeof f.url === 'string' ? f.url : '';
          if (!url) {
            if (ctx.readOnly) { box.appendChild(el('div', 'pm-att-none', T('Datei'))); return; }
            var b = el('button', 'btn pm-att-pick', busy ? T('Wird hochgeladen …') : T('Datei auswählen …')); b.type = 'button'; b.disabled = busy;
            b.addEventListener('click', function (e) { e.preventDefault(); choose(); });
            box.appendChild(b); return;
          }
          var card = el('div', 'pm-att'), ext = String(f.extension || (/\.([a-z0-9]{1,6})$/i.exec(f.name || url) || [])[1] || '').slice(0, 5);
          card.appendChild(el('span', 'pm-att-ic', ext ? ext.toUpperCase() : '📎'));
          var tx = el('div', 'pm-att-tx'), title = typeof d.title === 'string' ? d.title : (f.name || '');
          if (ctx.readOnly) tx.appendChild(el('span', 'pm-att-t', title || f.name || T('Datei')));
          else {
            var inp = el('input', 'pm-att-t pm-att-in'); inp.type = 'text'; inp.value = title; inp.placeholder = T('Titel der Datei');
            inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); inp.blur(); } });
            inp.addEventListener('change', function () { if (inp.value !== title) set({ title: inp.value.slice(0, 500) }); });
            tx.appendChild(inp);
          }
          var sz = sizeText(f.size); if (sz) tx.appendChild(el('span', 'pm-att-sz', sz));
          card.appendChild(tx);
          var a = el('a', 'pm-att-dl', '⬇'); a.href = url; a.setAttribute('download', f.name || title || ''); a.title = T('Herunterladen'); a.setAttribute('aria-label', T('Herunterladen'));
          card.appendChild(a);
          box.appendChild(card);
        }
        draw();
        return { update: function (nd) { d = isDict(nd) ? nd : {}; if (!(document.activeElement && box.contains(document.activeElement))) draw(); return true; },
                 stopEvent: function () { return true; } };
      }
    };
  }

  function setupTools() {
    var H = root.HKTools, k = K(), W = k && k.tools;
    if (!H || !W || !W.NextcloudTool || !W.EmbedTool) return false;
    H.register('attaches', attachesSpec());
    H.register('nextcloud', H.fromEditorJs(W.NextcloudTool, {
      slash: [],
      known: ['inst', 'fileid', 'kind', 'name', 'mime', 'size', 'mtime', 'author', 'public', 'fed', 'sig'],
      // Schreibstellen der Klasse: "Als Kopie uebernehmen"/Auswahl "Kopie" ersetzt den Block (eine Transaktion),
      // Abbrechen ohne Datei entfernt ihn
      hook: function (inst, notify, ctx) {
        var replaced = false;
        inst.index = function () { return 0; };
        inst.api = { blocks: {
          getBlocksCount: function () { return 1; }, getBlockByIndex: function () { return null; },
          insert: function (type, d) { replaced = true; ctx.replaceWith([{ id: ctx.id || undefined, type: type, data: d || {} }]); },
          delete: function () { if (!replaced) ctx.remove(); }
        } };
      }
    }));
    H.register('embed', H.fromEditorJs(W.EmbedTool, { slash: [], known: null }));
    return true;
  }
  if (root.HKTools) root.HKTools.setup(setupTools);

  // ===================================================================================================================
  // 2. Kern-Erweiterung: Bild-Knoten, Zwischenablage, Slash, Block-Menue
  // ===================================================================================================================
  if (!root.HKPmEditor || typeof HKPmEditor.use !== 'function') return;
  HKPmEditor.use(function (X) {
    var P = X.P, S0 = P.pm.state, M0 = P.pm.model, Kk = X.K;
    function ed() { return X.ed(); }
    function ro() { return X.ro(); }
    function dispatch(tr) { ed().view.dispatch(tr); }
    function fileOf(n) { var f = n.attrs.file; return isDict(f) && typeof f.url === 'string' && f.url ? f : null; }
    function setAttrsAt(pos, patch) {
      if (ro()) return false;
      var n = ed().state.doc.nodeAt(pos); if (!n) return false;
      dispatch(ed().state.tr.setNodeMarkup(pos, null, Object.assign({}, n.attrs, patch)));
      return true;
    }
    function byId(id, patch) { var x = X.findId(id); return x ? setAttrsAt(x.pos, patch) : false; }

    // --- Node-View Bild ------------------------------------------------------------------------------------------
    function imageView(props) {
      var node = props.node, getPos = props.getPos, pe = props.editor;
      // beim ersten Zeichnen steht der Editor noch nicht (X.ro() waere true): Rolle/Sperre aus pages.js + Editor-Option
      function ro() {
        if (Kk.readOnly && Kk.readOnly()) return true;
        var e = X.ed(); if (e) return !e.isEditable;
        return !!(pe && pe.options && pe.options.editable === false);
      }
      var dom = el('div', 'pm-blk pm-b-image'), box = el('div', 'pm-img'), frame = el('div', 'image-tool__image'), img = el('img', 'image-tool__image-picture');
      var inner = el('div', 'pm-in pm-img-cap'), up = null, busy = false, liveW = null;
      box.contentEditable = 'false';
      img.alt = ''; img.draggable = false; img.loading = 'lazy';
      frame.appendChild(img); box.appendChild(frame);
      dom.appendChild(box); dom.appendChild(inner);
      function pos() { return X.posOf(getPos); }
      function selectMe() {
        var p = pos(); if (p == null) return;
        var v = ed().view; v.dispatch(v.state.tr.setSelection(S0.NodeSelection.create(v.state.doc, p))); v.focus();
      }
      function chooseUpload() {
        if (ro() || busy) return;
        pickFile('image/png,image/jpeg,image/gif,image/webp', uploadInto);
      }
      function uploadInto(f) {
        if (!IMG_TYPES.test(f.type || '')) { toast(T('kein unterstütztes Bild (PNG, JPEG, GIF, WebP)')); return; }
        var id = node.attrs.id; busy = true; draw();
        upload(f, 'image').then(function (file) { busy = false; if (!byId(id, { file: file })) draw(); })
          .catch(function (e) { busy = false; draw(); toast(e.message); });
      }
      function paintSize(w) {
        var a = node.attrs, al = ALIGNS[a.align] ? a.align : 'center';
        box.classList.add('hk-sz');
        box.classList.toggle('hk-iw', !!w);
        if (w) box.style.setProperty('--iw', w + '%'); else box.style.removeProperty('--iw');
        ['left', 'center', 'right'].forEach(function (x) { box.classList.toggle('hk-al-' + x, al === x); });
      }
      function handles() {
        ['l', 'r'].forEach(function (side) {
          var h = el('div', 'hk-rs hk-rs-' + side); h.setAttribute('aria-hidden', 'true');
          h.title = T('Ziehen ändert die Breite, Doppelklick setzt sie zurück');
          h.addEventListener('dblclick', function (e) { e.preventDefault(); e.stopPropagation(); var p = pos(); if (p != null && node.attrs.width != null) setAttrsAt(p, { width: null }); });
          h.addEventListener('pointerdown', function (e) {
            if (ro() || e.button !== 0) return;
            e.preventDefault(); e.stopPropagation();
            var r = box.getBoundingClientRect(), before = sizeW(node.attrs.width), al = ALIGNS[node.attrs.align] ? node.attrs.align : 'center';
            if (!r.width) return;
            try { h.setPointerCapture(e.pointerId); } catch (x) { /* egal */ }
            document.body.classList.add('hk-resizing'); liveW = before;
            function mv(ev) {
              var px = al === 'left' ? ev.clientX - r.left : al === 'right' ? r.right - ev.clientX : 2 * Math.abs(ev.clientX - (r.left + r.width / 2));
              var pct = Math.min(100, Math.max(10, Math.round(Math.abs(px) / r.width * 100)));
              liveW = pct >= 100 ? null : pct; paintSize(liveW);
            }
            function upEv() {
              h.removeEventListener('pointermove', mv); h.removeEventListener('pointerup', upEv); h.removeEventListener('pointercancel', upEv);
              document.body.classList.remove('hk-resizing');
              var w = liveW; liveW = null;
              var p = pos(); if (p != null && w !== before) setAttrsAt(p, { width: w }); else paintSize(before);   // ein Zug = eine Transaktion
            }
            h.addEventListener('pointermove', mv); h.addEventListener('pointerup', upEv); h.addEventListener('pointercancel', upEv);
          });
          frame.appendChild(h);
        });
      }
      var shown = null;
      function draw() {
        X.paint(dom, node);
        var a = node.attrs, f = fileOf(node), key = JSON.stringify([f && f.url, busy, ro()]);
        ['withBorder', 'withBackground', 'stretched'].forEach(function (c) { box.classList.toggle('image-tool--' + c, a[c] === true); });
        dom.classList.toggle('pm-img-empty', !f);
        paintSize(liveW != null ? liveW : sizeW(a.width));
        if (key === shown) return;
        shown = key;
        [].slice.call(frame.querySelectorAll('.hk-rs')).forEach(function (x) { x.remove(); });
        if (up) { up.remove(); up = null; }
        if (f) {
          frame.hidden = false;
          if (img.getAttribute('src') !== f.url) img.src = f.url;
          if (!ro()) handles();
          return;
        }
        frame.hidden = true; img.removeAttribute('src');
        if (ro()) return;
        up = el('div', 'pm-img-up');
        var b = el('button', 'btn pm-img-pick', busy ? T('Lade Bild hoch …') : T('Bild auswählen …')); b.type = 'button'; b.disabled = busy;
        b.addEventListener('click', function (e) { e.preventDefault(); chooseUpload(); });
        up.appendChild(b);
        box.appendChild(up);
      }
      img.addEventListener('mousedown', function (e) { if (e.button !== 0) return; e.preventDefault(); selectMe(); });
      box.addEventListener('mouseenter', function () { draw(); });     // Sperre/Rolle seit dem Aufbau geaendert (setEditable)
      draw();
      return {
        dom: dom, contentDOM: inner, ignoreMutation: X.ignoreOutside(inner),
        stopEvent: function (e) { return !!(e.target && box.contains(e.target)); },
        update: function (n) { if (n.type !== node.type) return false; node = n; draw(); return true; },
        destroy: function () { shown = null; }
      };
    }

    // --- Bloecke einfuegen (Hochladen, Zwischenablage, Immich) ----------------------------------------------------
    // Unter dem Block mit der Schreibmarke (leerer Absatz wird ersetzt); passt der Typ dort nicht (Spalten in Spalten ...),
    // hinter den obersten Block
    // Anker = Block-ID (Hochladen/Kopieren laufen asynchron, Positionen waeren bis dahin veraltet)
    function anchorOf(pos) { var e = ed(); if (!e) return null; var b = X.blockAt(pos != null ? pos : e.state.selection.from); return b && b.node.attrs.id ? b.node.attrs.id : null; }
    function insertNodes(nodes, aid) {
      var e = ed(); if (!e || !nodes.length || ro()) return false;
      var st = e.state, b = (aid && X.findId(aid)) || X.blockAt(st.selection.from), tr = st.tr, frag = M0.Fragment.fromArray(nodes);
      function fits(p) { var $p = tr.doc.resolve(p), i = $p.index(); return $p.parent.canReplace(i, i, frag); }
      var where;
      if (!b) { where = st.doc.content.size; tr.insert(where, nodes); }
      else if (b.node.type.name === 'paragraph' && !b.node.content.size) {
        var $b = st.doc.resolve(b.pos), i0 = $b.index();
        if ($b.parent.canReplace(i0, i0 + 1, frag)) { tr.replaceWith(b.pos, b.pos + b.node.nodeSize, nodes); where = b.pos; }
      }
      if (where == null && b) {
        where = b.pos + b.node.nodeSize;
        if (!fits(where)) { var $t = st.doc.resolve(b.pos); where = $t.depth ? $t.after(1) : where; }
        tr.insert(where, nodes);
      }
      var end = where; nodes.forEach(function (n) { end += n.nodeSize; });
      try { tr.setSelection(S0.Selection.near(tr.doc.resolve(end), -1)); } catch (x) { /* bleibt */ }
      dispatch(tr.scrollIntoView());
      return true;
    }
    function imageBlock(file) { return { type: 'image', data: { file: file, caption: '', withBorder: false, withBackground: false, stretched: false } }; }
    function fileBlock(file) { return { type: 'attaches', data: { file: file, title: file.title || file.name || '' } }; }
    function uploadFiles(files, at) {
      var list = [].slice.call(files || []).slice(0, 20); if (!list.length) return false;
      var cid = Kk.cur() && Kk.cur().id, aid = anchorOf(at);
      toast(T('Wird hochgeladen …'));
      Promise.all(list.map(function (f) {
        var isImg = IMG_TYPES.test(f.type || '');
        return upload(f, isImg ? 'image' : 'file').then(function (file) { return isImg ? imageBlock(file) : fileBlock(file); });
      })).then(function (bl) {
        if (!Kk.cur() || Kk.cur().id !== cid) return;
        insertNodes(X.mk(bl, true), aid);
      }).catch(function (e) { toast(T('Nicht hochgeladen: {err}', { err: e.message })); });
      return true;
    }
    // Saga-Bloecke aus der Zwischenablage: Saga-Format (application/x-saga-blocks, static/pm-clip.js), Editor.js-JSON
    // (klassischer Editor bzw. Kopie aus diesem) oder HTML mit data-saga-block (Bild aus dem klassischen Editor).
    // T4d: Bloecke aus dem klassischen Editor immer hier (dessen HTML kennt das Schema nicht); aus diesem Editor nur mit
    // Dateiverweisen oder Nextcloud-Bloecken - sonst T3 pasteBlocks/ProseMirror (gleiches Ergebnis, ohne Server).
    function clipList(cd) {
      var raw = cd.getData('application/x-saga-blocks'), out = null, own = !!raw;
      if (raw) { try { var sb = JSON.parse(raw); out = isDict(sb) && Array.isArray(sb.blocks) ? sb.blocks : null; } catch (e) { out = null; } }
      if (!out) { raw = cd.getData('application/x-editor-js'); if (raw) { try { out = JSON.parse(raw); } catch (e) { out = null; } } }
      var h = cd.getData('text/html') || '';
      if (!out) {
        if (h && h.indexOf('data-saga-block') >= 0) {
          var n = new DOMParser().parseFromString(h, 'text/html').querySelector('[data-saga-block]');
          try { out = n ? JSON.parse(n.getAttribute('data-saga-block')) : null; } catch (e) { out = null; }
        }
      }
      if (!Array.isArray(out) || !out.length || !out.every(function (b) { return isDict(b) && typeof (b.tool || b.type) === 'string' && isDict(b.data); })) return null;
      var re = M().fileRef, js = JSON.stringify(out); if (!re) return null;
      re.lastIndex = 0;
      // mit Dateiverweisen (Kopie fuer die Zielseite) oder mit Nextcloud-Bloecken (fremde abweisen)
      if (re.test(js) || /"(tool|type)":"nextcloud"/.test(js)) return out;
      if (own || h.indexOf('data-block-type') >= 0) return null;          // aus diesem Editor: T3/ProseMirror
      // klassischer Editor; ein synchronisierter Block allein nimmt pm-struktur.js pasteSynced (wird Kopie), in einer
      // Auswahl mit anderen Bloecken zaehlt sein Inhalt (sonst gaebe es zwei Originale mit derselben sid)
      if (out.length === 1 && (out[0].tool || out[0].type) === 'synced') return null;
      var flat = [];
      out.forEach(function (b) {
        if ((b.tool || b.type) === 'synced' && Array.isArray(b.data.blocks)) flat.push.apply(flat, b.data.blocks.filter(isDict));
        else flat.push(b);
      });
      return flat.length ? flat : null;
    }
    function ncFilter(list) {                 // fremde Nextcloud-Bloecke (auch in Spalten/Toggles) entfernen
      var ok = M().ncOk, dropped = false;
      function rec(bl) {
        return (bl || []).filter(function (b) {
          if (!isDict(b)) return false;
          var t = b.tool || b.type, d = b.data || {};
          if (t === 'nextcloud' && ok && !ok(d)) { dropped = true; return false; }
          if (Array.isArray(d.blocks)) d.blocks = rec(d.blocks);
          if (Array.isArray(d.cols)) d.cols.forEach(function (c) { if (c && Array.isArray(c.blocks)) c.blocks = rec(c.blocks); });
          return true;
        });
      }
      var out = rec(list);
      if (dropped) toast(T('Nextcloud-Dateien anderer Personen lassen sich nicht in andere Seiten kopieren.'));
      return out;
    }
    function pasteSaga(view, e) {
      var cd = e.clipboardData, list = cd && clipList(cd); if (!list) return false;
      e.preventDefault();
      var fix = M().fixRefs, cid = Kk.cur() && Kk.cur().id, aid = anchorOf();
      if (!fix || !Kk.cleanClip) return true;
      fix(list).then(function (bl) {
        if (!Kk.cur() || Kk.cur().id !== cid || ro()) return;
        var b = aid && X.findId(aid), nested = !!(b && X.inNested(b.pos));
        bl = ncFilter(Kk.cleanClip(bl, nested));
        if (!bl.length) return;
        insertNodes(X.mk(bl.map(function (x) { var o = { type: x.tool, data: x.data }; if (x.tunes) o.tunes = x.tunes; return o; }), true), aid);
      }).catch(function (err) { toast(T('Einfügen fehlgeschlagen: {err}', { err: err.message })); });
      return true;
    }
    // Link allein in einem leeren Absatz: Einbetten (wie EmbedTool.pasteConfig); eigener Nextcloud-Link -> Nextcloud-Datei
    function pasteUrl(view, e) {
      var cd = e.clipboardData; if (!cd) return false;
      var txt = String(cd.getData('text/plain') || '').trim(); if (!URL_RE.test(txt)) return false;
      var st = view.state, sel = st.selection; if (!sel.empty) return false;
      var b = X.blockAt(sel.from); if (!b || b.node.type.name !== 'paragraph' || b.node.content.size) return false;
      e.preventDefault();
      var id = X.newId(), ed0 = { url: txt };
      if (M().ytId && M().ytId(txt)) ed0.kind = 'youtube';                    // wie EmbedTool.save
      var node = X.mk([{ id: id, type: 'embed', data: ed0 }])[0];
      var tr = st.tr.replaceWith(b.pos, b.pos + b.node.nodeSize, node);
      try { tr.setSelection(S0.NodeSelection.create(tr.doc, b.pos)); } catch (x) { /* egal */ }
      dispatch(tr);
      var cur = Kk.cur();
      if (root.HK && HK.nc && cur && !(Kk.remoteId && Kk.remoteId(cur.id))) {
        HK.nc.info().then(function (s) {
          if (!s.connected || !s.server || txt.toLowerCase().indexOf(String(s.server).toLowerCase() + '/') !== 0) return null;
          return HK.api('POST', '/api/nextcloud/resolve', { url: txt }).then(function (r) {
            var x = X.findId(id); if (!r.match || !x || x.node.type.name !== 'embed' || ro()) return;
            var d = r.block; d.author = s.me; d.public = false; d.fed = false;
            if (M().ncAdd) M().ncAdd(d);
            var n2 = X.mk([{ id: id, type: 'nextcloud', data: d }])[0];
            dispatch(ed().state.tr.replaceWith(x.pos, x.pos + x.node.nodeSize, n2));
            toast(T('Nextcloud-Datei eingefügt. Alle, die diese Seite sehen, sehen auch diese Datei.'));
          });
        }).catch(function () { /* bleibt Einbetten */ });
      }
      return true;
    }
    function pasteFiles(view, e) {
      var cd = e.clipboardData; if (!cd || !cd.files || !cd.files.length) return false;
      if (cd.getData('application/x-editor-js')) return false;
      // T4d: Text aus Word/Google Docs/Webseiten kommt oft zusaetzlich als Bild der Auswahl - dann zaehlt der Text
      var h = cd.getData('text/html');
      if (h && new DOMParser().parseFromString(h, 'text/html').body.textContent.trim()) return false;
      e.preventDefault();
      uploadFiles(cd.files, view.state.selection.from);
      return true;
    }
    function copyImage(view, e, cut) {
      var sel = view.state.selection;
      if (!(sel instanceof S0.NodeSelection) || sel.node.type.name !== 'image' || !fileOf(sel.node) || !M().clipImage) return false;
      var j = X.blockJson(sel.node);
      e.preventDefault();
      M().clipImage(e.clipboardData, j.data);
      if (cut && !ro()) dispatch(view.state.tr.deleteSelection());
      toast(cut ? T('Bild ausgeschnitten') : T('Bild kopiert'));
      return true;
    }
    function plugin() {
      return new S0.Plugin({
        key: new S0.PluginKey('hkMedien'),
        props: {
          handleDOMEvents: {
            copy: function (v, e) { return copyImage(v, e, false); },
            cut: function (v, e) { return copyImage(v, e, true); }
          },
          // Ausgewaehltes Bild (Klick aufs Bild = Knotenauswahl): Tippen ersetzt es nicht (wie hkGuard bei Karten - das Bild
          // ist kein atom, weil es die Unterschrift icap enthaelt). Backspace/Entf loeschen es weiter bewusst.
          handleTextInput: function (v) {
            var s = v.state.selection;
            return !!(s instanceof S0.NodeSelection && s.node.type.name === 'image');
          },
          handlePaste: function (v, e) {
            if (ro()) return false;
            return pasteSaga(v, e) || pasteFiles(v, e) || pasteUrl(v, e);
          },
          handleDrop: function (v, e, slice, moved) {
            if (moved || ro() || !e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return false;
            var hit = v.posAtCoords({ left: e.clientX, top: e.clientY });
            e.preventDefault();
            uploadFiles(e.dataTransfer.files, hit ? hit.pos : null);
            return true;
          }
        }
      });
    }

    // --- Tasten: Unterschrift und Nachbarn ------------------------------------------------------------------------
    function keys(editor) {
      function prevImage(dir) {
        var st = editor.state, sel = st.selection; if (!sel.empty) return false;
        var $f = sel.$from; if (!$f.parent.isTextblock || $f.parent.type.name === 'icap') return false;
        if (dir < 0 ? $f.parentOffset !== 0 : $f.parentOffset !== $f.parent.content.size) return false;
        var b = X.blockAt($f.pos); if (!b || b.depth !== $f.depth) return false;
        var $b = st.doc.resolve(b.pos), idx = $b.index(), par = $b.parent;
        var nb = dir < 0 ? (idx > 0 ? par.child(idx - 1) : null) : (idx + 1 < par.childCount ? par.child(idx + 1) : null);
        if (!nb || nb.type.name !== 'image') return false;
        var np = dir < 0 ? b.pos - nb.nodeSize : b.pos + b.node.nodeSize, tr = st.tr;
        if (dir < 0 && !$f.parent.content.size && b.node.type.name === 'paragraph') { tr.delete(b.pos, b.pos + b.node.nodeSize); }
        tr.setSelection(S0.NodeSelection.create(tr.doc, np));
        editor.view.dispatch(tr.scrollIntoView());
        return true;
      }
      return {
        // Enter in der Unterschrift: neuer Absatz unter dem Bild (wie die Quelle beim Zitat)
        'Enter': function () {
          var st = editor.state, $f = st.selection.$from;
          if ($f.parent.type.name !== 'icap' || !st.selection.empty) return false;
          var b = X.blockAt($f.pos); if (!b) return false;
          var tr = st.tr, at = b.pos + b.node.nodeSize;
          tr.insert(at, X.schema().nodes.paragraph.create({ id: X.newId() })); X.setSel(tr, at + 1);
          editor.view.dispatch(tr.scrollIntoView());
          return true;
        },
        'Backspace': function () { return prevImage(-1); },
        'Delete': function () { return prevImage(1); }
      };
    }

    // --- Block-Menue: Ausrichtung, Rahmen, Hintergrund, volle Breite, Breite ----------------------------------------
    function alignSub(cur, set) {
      return function (sm) {
        [['left', T('Linksbündig'), '⇤'], ['center', T('Zentriert'), '↔'], ['right', T('Rechtsbündig'), '⇥']].forEach(function (a) {
          sm.appendChild(HK.menuItem(a[1], function () { set(a[0]); }, { icon: a[2], on: (cur || 'center') === a[0] }));
        });
      };
    }
    function settings(m, x) {
      var n = x.node, t = n.type.name, id = n.attrs.id;
      if (t === 'image' && fileOf(n)) {
        var a = n.attrs;
        m.appendChild(HK.menuItem(T('Ausrichtung'), null, { icon: '↔', sub: alignSub(a.align, function (v) { byId(id, { align: v }); }) }));
        [['withBorder', T('Mit Rahmen'), '▢'], ['withBackground', T('Mit Hintergrund'), '▣'], ['stretched', T('Volle Breite'), '⇔']].forEach(function (o) {
          m.appendChild(HK.menuItem(o[1], function () { var y = X.findId(id); if (y) byId(id, (function () { var p = {}; p[o[0]] = y.node.attrs[o[0]] !== true; return p; })()); }, { icon: o[2], on: a[o[0]] === true }));
        });
        if (a.width != null) m.appendChild(HK.menuItem(T('Breite zurücksetzen'), function () { byId(id, { width: null }); }, { icon: '⤢' }));
      } else if (t === 'embed') {
        var d = isDict(n.attrs.data) ? n.attrs.data : {}, yt = M().ytId && M().ytId(String(d.url || '').replace(/&amp;/g, '&'));
        if (!yt) return;
        var setD = function (patch) {
          var y = X.findId(id); if (!y || ro()) return;
          var nd = Object.assign(clone(isDict(y.node.attrs.data) ? y.node.attrs.data : {}), patch);
          Object.keys(nd).forEach(function (k) { if (nd[k] === null) delete nd[k]; });
          dispatch(ed().state.tr.setNodeMarkup(y.pos, null, Object.assign({}, y.node.attrs, { data: nd })));
        };
        m.appendChild(HK.menuItem(T('Ausrichtung'), null, { icon: '↔', sub: alignSub(d.align, function (v) { setD({ align: v }); }) }));
        if (d.width != null) m.appendChild(HK.menuItem(T('Breite zurücksetzen'), function () { setD({ width: null }); }, { icon: '⤢' }));
      }
    }

    // --- Slash: Gruppe "Medien" --------------------------------------------------------------------------------------
    var G = T('Medien');
    function remote() { var c = Kk.cur(); return !!(c && Kk.remoteId && Kk.remoteId(c.id)); }
    X.slashReg({ key: 'media-image', group: G, t: T('Bild'), ic: IC.image, desc: T('Bild hochladen oder einfügen'), kw: 'bild image foto picture upload', order: 70,
      block: { type: 'image', data: { caption: '', withBorder: false, withBackground: false, stretched: false } } });
    X.slashReg({ key: 'media-file', group: G, t: T('Datei'), ic: IC.file, desc: T('Datei zum Herunterladen'), kw: 'datei file anhang attachment pdf upload', order: 71,
      block: { type: 'attaches', data: {} } });
    X.slashReg({ key: 'media-immich', group: G, t: T('Bild aus Immich'), ic: '📷', desc: T('Foto aus deiner Immich-Mediathek'), kw: 'immich foto bild photo', order: 72,
      when: function () { return !!(root.HK && HK.immichPick) && !(Kk.foreign && Kk.foreign()); },
      run: function (editor, at) {
        if (!HK.immichPick) return;
        var cid = Kk.cur() && Kk.cur().id, aid = anchorOf(at);
        HK.immichPick(function (url) {
          if (!Kk.cur() || Kk.cur().id !== cid) return;
          insertNodes(X.mk([imageBlock({ url: url })], true), aid);
        }, function () { /* abgebrochen: nichts eingefuegt */ });
      } });
    X.slashReg({ key: 'media-nextcloud', group: G, t: T('Nextcloud-Datei'), ic: '☁', desc: T('Datei aus deiner Nextcloud'), kw: 'nextcloud cloud datei file', order: 73,
      when: function () { return !!(root.HK && HK.nc) && !remote(); },
      block: { type: 'nextcloud', data: {} } });
    X.slashReg({ key: 'media-embed', group: G, t: T('Einbetten (YouTube, Link)'), ic: '▶', desc: T('Video oder Link einbetten'), kw: 'einbetten embed youtube video link', order: 74,
      block: { type: 'embed', data: {} } });

    // --- Speichern: leere/fremde neue Bloecke weglassen (validate der Editor.js-Werkzeuge) --------------------------
    function validate(b) {
      var d = isDict(b.data) ? b.data : {};
      if (b.type === 'image' || b.type === 'attaches') return !!(isDict(d.file) && d.file.url);
      if (b.type === 'embed') return /^https?:\/\//i.test(String(d.url || ''));
      if (b.type === 'nextcloud') return M().ncOk ? M().ncOk(d) : !!d.fileid;
      return true;
    }

    return {
      edit: ['image'],
      nodeViews: { image: imageView },
      plugins: function () { return [plugin()]; }, early: true,
      keys: keys,
      settings: settings,
      names: { image: T('Bild'), attaches: T('Datei'), embed: T('Einbetten'), nextcloud: T('Nextcloud-Datei') },
      validate: validate,
      api: { uploadFiles: uploadFiles, insertMedia: function (bl) { return insertNodes(X.mk(bl, true)); } }
    };
  });
})(typeof self !== 'undefined' ? self : this);
