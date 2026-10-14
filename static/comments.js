// Saga - Kommentare an Bloecken (1.3), dezent: am Block nur eine kleine Zahl am Rand (nur bei offenen Unterhaltungen),
// die Unterhaltung in einer Seitenleiste rechts (Handy: Bottom-Sheet). Antworten, eigene bearbeiten/loeschen, "Erledigt"
// (zugeklappt, ausgeblendet; Schalter "Erledigte zeigen"), ungelesen = kleiner Punkt. Alles per textContent.
// Braucht static/pages.js (window.HKPages.k) und die Ereignisse 'hk-page-open' / 'hk-page-ready'.
(function () {
  'use strict';
  var HK = window.HK, $ = HK.$, el = HK.el, api = HK.api;
  if (!window.HKPages || !$('pg-editor')) return;
  var K = window.HKPages.k;
  var RANK = { view: 1, comment: 2, edit: 3, owner: 4 };
  var C = { id: null, threads: [], nres: 0, resolved: false, block: null, open: false, load: 0, expanded: {}, replyTo: null, editing: null };
  var MAX = 2000;

  function cur() { return K.cur(); }
  // Rolle entzogen/gesenkt (403/404): Seite gleich im neuen Zustand zeigen (Etikett, nur Lesen bzw. "Kein Zugriff")
  function lost(err) { if (err && (err.status === 403 || err.status === 404) && K.recheck) K.recheck(); }
  function role() { var c = cur(); return c ? (c.role || 'owner') : null; }
  function can() { return RANK[role()] >= RANK.comment; }
  function ro() { return RANK[role()] < RANK.edit; }

  // --- Seitenleiste / Bottom-Sheet ------------------------------------------------------------
  var panel = el('aside', 'cm-panel'); panel.id = 'cm-panel'; panel.hidden = true;
  panel.setAttribute('role', 'complementary'); panel.setAttribute('aria-label', T('Kommentare'));
  var head = el('div', 'cm-head'), grip = el('span', 'cm-grip'); grip.setAttribute('aria-hidden', 'true');
  var htitle = el('h2', 'cm-title', T('Kommentare'));
  var swl = el('label', 'cm-sw'), sw = el('input', 'hk-switch'); sw.type = 'checkbox'; sw.setAttribute('role', 'switch');
  swl.appendChild(el('span', null, T('Erledigte zeigen'))); swl.appendChild(sw);
  var close = el('button', 'icon-btn cm-close', '×'); close.type = 'button'; close.setAttribute('aria-label', T('Kommentare schließen'));
  head.appendChild(grip); head.appendChild(htitle); head.appendChild(swl); head.appendChild(close);
  var ctx = el('div', 'cm-ctx'), list = el('div', 'cm-list'), foot = el('div', 'cm-foot');
  panel.appendChild(head); panel.appendChild(ctx); panel.appendChild(list); panel.appendChild(foot);
  var scrim = el('div', 'cm-scrim'); scrim.hidden = true;
  document.body.appendChild(scrim); document.body.appendChild(panel);
  close.addEventListener('click', hide);
  scrim.addEventListener('click', hide);
  sw.addEventListener('change', function () { C.resolved = sw.checked; reload().then(render); });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || panel.hidden || e.defaultPrevented) return;
    if (document.querySelector('.hk-ask') || HK.menuOpen() || !$('pg-modal').hidden) return;
    e.preventDefault(); e.stopPropagation(); hide();
  }, true);

  var narrow = window.matchMedia('(max-width: 760px)');
  function show() {
    // Desktop: unter der Kopfleiste (Benutzermenue bleibt erreichbar); Handy: Bottom-Sheet ueber einem Schleier
    var hd = document.querySelector('.np-head'), top = hd && !narrow.matches ? Math.max(0, Math.round(hd.getBoundingClientRect().bottom)) : 0;
    panel.style.top = narrow.matches ? '' : top + 'px';
    panel.hidden = false; C.open = true; document.body.classList.add('cm-open');
    scrim.hidden = !narrow.matches;
    if (narrow.matches) document.body.style.setProperty('--cm-sheet-h', Math.round(panel.getBoundingClientRect().height) + 'px');
  }
  function hide() {
    panel.hidden = true; scrim.hidden = true; scrim.style.clipPath = ''; C.open = false; C.block = null; C.replyTo = C.editing = null;
    document.body.classList.remove('cm-open'); markBlocks();
  }

  // --- Daten ------------------------------------------------------------------------------------
  function reload() {
    var c = cur(); if (!c) return Promise.resolve();
    var id = c.id, n = ++C.load;
    return api('GET', '/api/notes/' + id + '/comments' + (C.resolved ? '?resolved=1' : '')).then(function (d) {
      if (n !== C.load || !cur() || cur().id !== id) return;
      C.threads = d.threads || []; C.nres = d.resolved_count || 0; C.loaded = true;
      var open = C.threads.filter(function (t) { return !t.resolved_at; });
      c.comments_open = open.length;
      c.comments_unread = open.filter(function (t) { return t.unread; }).length;
      headBtn(); placeBubbles();
    }).catch(function (err) { if (C.open) HK.toast(T('Kommentare nicht geladen: {err}', { err: err.message })); });
  }
  function openThreads() { return C.threads.filter(function (t) { return !t.resolved_at; }); }

  // --- Kopfzeile "💬 3" ---------------------------------------------------------------------------
  function headBtn() {
    var b = $('pg-cm-btn'), c = cur(); if (!b) return;
    var n = c ? (c.comments_open || 0) : 0;
    b.hidden = !n; b.textContent = '';
    b.appendChild(svgBubble()); b.appendChild(el('span', 'cm-b-n', String(n)));
    if (c && c.comments_unread) b.appendChild(el('span', 'cm-dot'));
    var tt = n === 1 ? T('1 offene Unterhaltung') : T('{n} offene Unterhaltungen', { n: n });
    if (c && c.comments_unread) tt += ' · ' + (c.comments_unread === 1 ? T('1 ungelesen') : T('{n} ungelesen', { n: c.comments_unread }));
    b.title = tt;
    b.setAttribute('aria-label', b.title);
  }
  $('pg-cm-btn').addEventListener('click', function () { if (C.open && !C.block) hide(); else openAll(); });

  // --- Zahl am Block (nur offene Unterhaltungen) ------------------------------------------------------
  function blockEl(bid) {
    try { return $('pg-editor').querySelector('.ce-block[data-id="' + CSS.escape(bid) + '"]'); } catch (e) { return null; }
  }
  function placeBubbles() {
    var box = $('pg-editor'); if (!box) return;
    var per = {}, unread = {};
    openThreads().forEach(function (t) { if (!t.block_id) return; per[t.block_id] = (per[t.block_id] || 0) + 1; if (t.unread) unread[t.block_id] = 1; });
    var c = cur(); if (c && c.comment_counts && !C.loaded) per = c.comment_counts;        // vor dem ersten Laden: Zahlen vom Server
    [].forEach.call(box.querySelectorAll('.ce-block[data-id]'), function (b) {
      var bid = b.getAttribute('data-id'), n = per[bid] || 0, bub = null;
      for (var i = 0; i < b.children.length; i++) if (b.children[i].classList.contains('cm-bubble')) bub = b.children[i];
      b.classList.toggle('cm-has', !!n);              // Handy: Platz fuer die Zahl am rechten Rand
      if (!n) { if (bub) bub.remove(); return; }
      if (!bub) {
        bub = el('button', 'cm-bubble'); bub.type = 'button'; bub.contentEditable = 'false'; bub.dataset.mutationFree = 'true';
        bub.addEventListener('mousedown', function (e) { e.preventDefault(); });
        bub.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); openBlock(bid); });
        b.appendChild(bub);
      }
      var txt = String(n);
      if (bub.dataset.n !== txt) { bub.dataset.n = txt; bub.textContent = ''; bub.appendChild(svgBubble()); bub.appendChild(el('span', 'cm-bubble-n', txt)); }
      bub.classList.toggle('unread', !!unread[bid]);
      bub.setAttribute('aria-label', n === 1 ? T('1 Unterhaltung zu diesem Block') : T('{n} Unterhaltungen zu diesem Block', { n: n }));
      bub.title = bub.getAttribute('aria-label');
    });
    markBlocks();
  }
  function markBlocks() {
    [].forEach.call(document.querySelectorAll('#pg-editor .ce-block.cm-focus'), function (b) { b.classList.remove('cm-focus'); });
    if (C.open && C.block) { var b = blockEl(C.block); if (b) b.classList.add('cm-focus'); }
  }
  function svgBubble() {
    var NS = 'http://www.w3.org/2000/svg', s = document.createElementNS(NS, 'svg'), p = document.createElementNS(NS, 'path');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true'); s.setAttribute('class', 'cm-bubble-ic');
    p.setAttribute('d', 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z'); s.appendChild(p); return s;
  }
  // Inhalt veraendert (Bloecke neu gezeichnet, verschoben, Spalten geladen) -> Zahlen nachziehen
  var moT = null;
  new MutationObserver(function (ms) {
    if (ms.every(function (m) { return [].every.call(m.addedNodes, function (n) { return n.classList && (n.classList.contains('cm-bubble') || n.classList.contains('cm-add')); }); })) return;
    clearTimeout(moT); moT = setTimeout(placeBubbles, 250);
  }).observe($('pg-editor'), { childList: true, subtree: true });

  // Kleiner Knopf 💬+ am Block unter dem Zeiger - fuer alle, die kommentieren duerfen (auch Eigentuemer/Bearbeiter);
  // auf Touch-Geraeten nur bei schreibgeschuetzter Seite (Lesen/Kommentieren) nach dem Antippen
  var addBtn = el('button', 'cm-add'); addBtn.type = 'button'; addBtn.contentEditable = 'false'; addBtn.dataset.mutationFree = 'true';
  addBtn.appendChild(svgBubble()); addBtn.appendChild(el('span', 'cm-add-p', '+'));
  addBtn.setAttribute('aria-label', T('Kommentieren')); addBtn.title = T('Kommentieren');
  addBtn.addEventListener('mousedown', function (e) { e.preventDefault(); });
  addBtn.addEventListener('click', function (e) { e.preventDefault(); e.stopPropagation(); var b = addBtn.parentNode; if (b && b.dataset) openBlock(b.getAttribute('data-id')); });
  function hoverBlock(e, tap) {
    if (!cur() || !can() || (tap && !ro())) { if (addBtn.parentNode && tap) addBtn.remove(); return; }
    var b = e.target && e.target.closest ? e.target.closest('.ce-block[data-id]') : null;
    if (!b || !$('pg-editor').contains(b) || addBtn.parentNode === b) return;
    if (b.classList.contains('cm-has')) { if (addBtn.parentNode) addBtn.remove(); return; }   // Block mit Zahl: die Zahl oeffnet ihn
    b.appendChild(addBtn);
  }
  // Maus: beim Ueberfahren; Touch (kein Hover): erst beim Antippen - sonst springt der Knopf nach dem Scrollen zum Block,
  // der zufaellig unter dem alten Zeiger liegt
  var canHover = window.matchMedia('(hover: hover)');
  $('pg-editor').addEventListener('pointerover', function (e) { if (e.pointerType === 'mouse' && canHover.matches) hoverBlock(e, false); });
  $('pg-editor').addEventListener('click', function (e) { if (!canHover.matches) hoverBlock(e, true); });

  // --- Darstellung der Unterhaltungen ------------------------------------------------------------
  var URL_RE = /\bhttps?:\/\/[^\s<>"']+/g;
  function linkify(node, text) {                       // Klartext -> Text + Links (nur http/https), sonst nichts
    var last = 0, m;
    URL_RE.lastIndex = 0;
    while ((m = URL_RE.exec(text))) {
      var u = m[0].replace(/[).,;:!?\]]+$/, ''), end = m.index + u.length;
      if (m.index > last) node.appendChild(document.createTextNode(text.slice(last, m.index)));
      var a = el('a', 'cm-link', u); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer'; node.appendChild(a);
      last = end; URL_RE.lastIndex = end;
    }
    if (last < text.length) node.appendChild(document.createTextNode(text.slice(last)));
  }
  var fFull = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  function when(ts) { var s = el('time', 'cm-time', HK.relTime(ts - Date.now() / 1000)); s.dateTime = new Date(ts * 1000).toISOString(); s.title = fFull.format(new Date(ts * 1000)); return s; }
  function snippet(bid) {
    if (!bid) return T('Ganze Seite');
    var b = blockEl(bid); if (!b) return null;
    var c = b.querySelector('.ce-block__content') || b, s = (c.textContent || '').replace(/\s+/g, ' ').trim();
    if (!s) s = b.querySelector('img') ? T('Bild') : T('Block');
    return s.length > 70 ? s.slice(0, 69) + '…' : s;
  }
  function editor(value, ph, okText, onOk, onCancel) {
    var w = el('div', 'cm-editor'), ta = el('textarea', 'hk-in cm-ta'); ta.maxLength = MAX; ta.rows = 2; ta.placeholder = ph; ta.value = value || '';
    ta.setAttribute('aria-label', ph);
    var bar = el('div', 'cm-ebar'), cnt = el('span', 'cm-cnt');
    var ok = el('button', 'btn btn-primary cm-send', okText); ok.type = 'button';
    function upd() { var n = ta.value.length; cnt.textContent = n > MAX - 200 ? T('{n} von {max}', { n: n, max: MAX }) : ''; ok.disabled = !ta.value.trim(); grow(); }
    function grow() { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight + 2, 220) + 'px'; }
    function go() { var v = ta.value.trim(); if (!v) return; ok.disabled = true; onOk(v).then(function () { ok.disabled = false; }, function () { ok.disabled = false; }); }
    ta.addEventListener('input', upd);
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); go(); }
      if (e.key === 'Escape' && onCancel) { e.preventDefault(); e.stopPropagation(); onCancel(); }
    });
    ok.addEventListener('click', go);
    bar.appendChild(cnt);
    if (onCancel) { var cb = el('button', 'btn cm-cancel', T('Abbrechen')); cb.type = 'button'; cb.addEventListener('click', onCancel); bar.appendChild(cb); }
    bar.appendChild(ok);
    w.appendChild(ta); w.appendChild(bar); upd();
    w.focusLater = function () { if (C.quiet) return; setTimeout(function () { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }, 0); };
    return w;
  }
  function comment(t, cm, first, dot) {
    var c = el('div', 'cm-c'), h = el('div', 'cm-c-h');
    h.appendChild(el('b', 'cm-author', cm.author.me ? T('Du') : cm.author.name));
    if (dot) { var d = el('span', 'cm-dot'); d.title = T('Neu'); h.appendChild(d); }     // ungelesen: Punkt neben dem Autor
    h.appendChild(when(cm.created));
    if (cm.edited && !cm.deleted) h.appendChild(el('span', 'cm-edited', T('bearbeitet')));
    c.appendChild(h);
    if (C.editing === cm.id) {
      var ed = editor(cm.body, T('Kommentar bearbeiten'), T('Speichern'), function (v) {
        return api('PATCH', '/api/comments/' + cm.id, { body: v }).then(function () { C.editing = null; return reload(); }).then(render)
          .catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); throw err; });
      }, function () { C.editing = null; render(); });
      c.appendChild(ed); ed.focusLater(); return c;
    }
    var mod = role() === 'owner', mine = cm.mine && can() && !cm.deleted;
    var others = first && t.comments.slice(1).some(function (x) { return !x.author.username || x.author.username !== cm.author.username; });
    function del() {
      var whole = first && !others;
      HK.ask({ title: whole ? T('Unterhaltung löschen?') : T('Kommentar löschen?'),
               text: whole ? T('Der erste Kommentar und alle Antworten werden gelöscht.')
                 : others ? T('Der Kommentar wird gelöscht, die Antworten bleiben stehen.') : T('Der Kommentar wird gelöscht.'),
               ok: T('Löschen'), danger: true }).then(function (yes) {
        if (!yes) return;
        api('DELETE', '/api/comments/' + cm.id).then(reload).then(render).catch(function (err) { HK.toast(T('Nicht gelöscht: {err}', { err: err.message })); });
      });
    }
    function delAll() {                                // Eigentuemer: ganze Unterhaltung (mit fremden Antworten)
      HK.ask({ title: T('Unterhaltung löschen?'), text: T('Der erste Kommentar und alle Antworten werden gelöscht – auch die von anderen.'),
               ok: T('Löschen'), danger: true }).then(function (yes) {
        if (!yes) return;
        api('DELETE', '/api/comments/' + cm.id + '?all=1').then(reload).then(render).catch(function (err) { HK.toast(T('Nicht gelöscht: {err}', { err: err.message })); });
      });
    }
    var items = [];
    if (mine) items.push([T('Bearbeiten'), function () { C.editing = cm.id; C.replyTo = null; render(); }, '✎']);
    if (!cm.deleted && (mine || mod)) items.push([T('Löschen'), del, '🗑', true]);
    if (first && mod && (others || cm.deleted)) items.push([T('Unterhaltung löschen'), delAll, '🗑', true]);
    if (items.length) {                                // Aktionen ruhig in einem ⋯-Menue je Kommentar
      var mb = el('button', 'icon-btn cm-more'); mb.type = 'button'; mb.textContent = '⋯';
      mb.setAttribute('aria-label', T('Aktionen für diesen Kommentar')); mb.setAttribute('aria-haspopup', 'menu'); mb.title = T('Aktionen');
      mb.addEventListener('click', function (e) {
        e.stopPropagation();
        HK.openMenu(mb, function (m) { items.forEach(function (it) { m.appendChild(HK.menuItem(it[0], it[1], { icon: it[2], danger: !!it[3] })); }); });
      });
      h.appendChild(mb);
    }
    if (cm.deleted) { c.appendChild(el('p', 'cm-text cm-deleted', T('Kommentar gelöscht'))); return c; }
    var p = el('p', 'cm-text'); linkify(p, cm.body); c.appendChild(p);
    return c;
  }
  function thread(t) {
    var w = el('section', 'cm-thread' + (t.resolved_at ? ' resolved' : '') + (t.unread ? ' unread' : '')); w.dataset.id = t.id;
    var sn = snippet(t.block_id), gone = t.block_id && (!t.block_exists || sn === null);
    if (!C.block) {                                    // in der Blockansicht steht der Block schon oben - kein doppeltes Zitat
      var top = el('button', 'cm-where'); top.type = 'button';
      top.appendChild(el('span', 'cm-where-t' + (gone ? ' gone' : ''), gone ? T('Block entfernt') : sn));
      if (!gone && t.block_id) top.title = T('Zum Block springen');
      top.addEventListener('click', function () { if (!gone && t.block_id) { K.showBlock(t.block_id); } });
      w.appendChild(top);
    }
    var by = t.resolved_by ? T('Erledigt von {name}', { name: t.resolved_by.me ? T('dir') : t.resolved_by.name }) : T('✓ Erledigt');
    if (t.resolved_at && !C.expanded[t.id]) {          // erledigt: zugeklappt
      var r = el('button', 'cm-res-line'); r.type = 'button';
      r.appendChild(el('span', null, t.resolved_by ? '✓ ' + by : by));
      r.appendChild(el('span', 'cm-res-n', t.comments.length === 1 ? T('1 Kommentar') : T('{n} Kommentare', { n: t.comments.length })));
      r.addEventListener('click', function () { C.expanded[t.id] = true; render(); });
      w.appendChild(r); return w;
    }
    if (t.resolved_at) w.appendChild(el('div', 'cm-res-state', t.resolved_by ? '✓ ' + by : by));   // aufgeklappt: Zustand bleibt sichtbar
    var lastOther = -1;
    if (t.unread) t.comments.forEach(function (cm, i) { if (!cm.mine) lastOther = i; });
    t.comments.forEach(function (cm, i) { w.appendChild(comment(t, cm, i === 0, i === lastOther)); });
    if (can()) {
      var bar = el('div', 'cm-tbar');
      if (C.replyTo === t.id) {
        var ed = editor('', T('Antworten'), T('Antworten'), function (v) {
          return api('POST', '/api/notes/' + cur().id + '/comments', { thread_id: t.id, body: v }).then(function () { C.replyTo = null; return reload(); }).then(render)
            .catch(function (err) { HK.toast(T('Nicht gesendet: {err}', { err: err.message })); lost(err); throw err; });
        }, function () { C.replyTo = null; render(); });
        w.appendChild(ed); ed.focusLater();
      } else {
        var rb = el('button', 'cm-tbtn', T('Antworten')); rb.type = 'button';
        rb.addEventListener('click', function () { C.replyTo = t.id; C.editing = null; render(); });
        bar.appendChild(rb);
      }
      var done = el('button', 'cm-tbtn cm-resolve', t.resolved_at ? T('Wieder öffnen') : T('✓ Erledigt##Aktion')); done.type = 'button';
      done.title = t.resolved_at ? T('Unterhaltung wieder öffnen') : T('Als erledigt markieren – die Unterhaltung wird zugeklappt');
      done.addEventListener('click', function () {
        var was = t.resolved_at;
        api('POST', '/api/comments/' + t.id + '/resolve', { resolved: !was }).then(function () {
          if (!was) {
            t.resolved_at = Date.now() / 1000; placeBubbles();            // Zahl am Block sofort weg, nicht erst nach dem Neuladen
            HK.toast(T('Unterhaltung erledigt'), T('Rückgängig'), function () {
              api('POST', '/api/comments/' + t.id + '/resolve', { resolved: false }).then(reload).then(render);
            });
          }
          delete C.expanded[t.id]; return reload();
        }).then(render).catch(function (err) { HK.toast(T('Nicht gespeichert: {err}', { err: err.message })); lost(err); });
      });
      bar.appendChild(done);
      w.appendChild(bar);
    }
    return w;
  }
  function render() {
    if (!C.open) return;
    var c = cur(); if (!c) { hide(); return; }
    var all = C.threads.filter(function (t) { return C.resolved || !t.resolved_at; });
    var shown = C.block ? all.filter(function (t) { return t.block_id === C.block; }) : all;
    htitle.textContent = T('Kommentare');
    if (openThreads().length) htitle.appendChild(el('span', 'cm-count', String(openThreads().length)));
    sw.checked = C.resolved; swl.hidden = !C.nres && !C.resolved;
    swl.title = T('{n} erledigt', { n: C.nres });
    ctx.textContent = ''; ctx.hidden = !C.block;
    if (C.block) {                                     // Blockansicht: nur der Weg zurueck (der Block ist hervorgehoben)
      var allb = el('button', 'cm-tbtn cm-back', '← ' + T('Alle Unterhaltungen')); allb.type = 'button';
      allb.addEventListener('click', function () { C.block = null; markBlocks(); hole(); render(); });
      ctx.appendChild(allb);
    }
    list.textContent = '';
    shown.forEach(function (t) { list.appendChild(thread(t)); });
    if (!shown.length) list.appendChild(el('p', 'cm-empty', emptyText()));
    if (narrow.matches) list.appendChild(swl); else if (swl.parentNode !== head) head.insertBefore(swl, close);   // Handy: Schalter ans Listenende
    foot.textContent = ''; foot.hidden = false;
    if (!can()) foot.appendChild(el('p', 'cm-foot-ro', T('Du kannst Kommentare lesen, aber nicht schreiben.')));
    if (can()) {
      var bid = C.block || '';
      var ed = editor('', C.block ? T('Kommentar zu diesem Block') : T('Kommentar zur ganzen Seite'), T('Kommentieren'), function (v) {
        return api('POST', '/api/notes/' + c.id + '/comments', { block_id: bid, body: v }).then(reload).then(render)
          .catch(function (err) { HK.toast(T('Nicht gesendet: {err}', { err: err.message })); lost(err); throw err; });
      });
      foot.appendChild(ed);
      if (C.block && !shown.length) ed.focusLater();
    }
    // gesehen: sichtbare, ungelesene Unterhaltungen als gelesen merken (der Punkt verschwindet beim naechsten Zeichnen)
    shown.filter(function (t) { return t.unread && !t.resolved_at; }).forEach(function (t) {
      api('POST', '/api/comments/' + t.id + '/seen').then(function () {
        t.unread = false; var p = cur(); if (p) p.comments_unread = Math.max(0, (p.comments_unread || 1) - 1); headBtn(); placeBubbles();
      }).catch(function () {});
    });
  }

  function emptyText() {
    var touch = !canHover.matches;
    if (C.block) return !can() ? T('Keine Kommentare zu diesem Block.') : C.nres && !C.resolved ? T('Keine offenen Kommentare zu diesem Block.') : T('Noch keine Kommentare zu diesem Block.');
    if (C.nres && !C.resolved) return C.nres === 1 ? T('Keine offenen Kommentare · 1 erledigt') : T('Keine offenen Kommentare · {n} erledigt', { n: C.nres });
    if (!can()) return T('Noch keine Kommentare.');
    if (touch && role() === 'comment') return T('Noch keine Kommentare. Tippe auf einen Absatz, um ihn zu kommentieren.');
    return T('Noch keine Kommentare. Kommentiere einen Absatz über 💬 am rechten Rand oder schreibe unten einen Kommentar zur ganzen Seite.');
  }
  // Handy: den Block oberhalb des Bottom-Sheets halten (er bleibt sichtbar, waehrend man schreibt)
  function scroller(n) {
    while (n && n !== document.body) { var o = getComputedStyle(n).overflowY; if ((o === 'auto' || o === 'scroll') && n.scrollHeight > n.clientHeight) return n; n = n.parentElement; }
    return document.scrollingElement || document.documentElement;
  }
  function keepVisible(bid) {
    if (!narrow.matches || !bid) return;
    setTimeout(function () {
      var b = blockEl(bid); if (!b || panel.hidden) return;
      var r = b.getBoundingClientRect(), top = panel.getBoundingClientRect().top, hd = document.querySelector('.np-head');
      var minTop = hd ? hd.getBoundingClientRect().bottom + 8 : 8, want = Math.max(minTop, top - r.height - 16);
      if (Math.abs(r.top - want) > 4) scroller(b).scrollBy({ top: r.top - want, behavior: 'smooth' });
      setTimeout(hole, 450);
    }, 60);
  }
  // Der hervorgehobene Block bleibt ueber dem Schleier sichtbar: Aussparung im Schleier (clip-path mit Loch)
  function hole() {
    var b = C.open && C.block && narrow.matches && !scrim.hidden ? blockEl(C.block) : null;
    if (!b) { scrim.style.clipPath = ''; return; }
    var r = b.getBoundingClientRect(), w = innerWidth, h = innerHeight, p = 6;
    var x0 = Math.max(0, r.left - p), y0 = Math.max(0, r.top - p), x1 = Math.min(w, r.right + p), y1 = Math.min(h, r.bottom + p);
    scrim.style.clipPath = 'path(evenodd, "M0 0H' + w + 'V' + h + 'H0Z M' + x0 + ' ' + y0 + 'H' + x1 + 'V' + y1 + 'H' + x0 + 'Z")';
  }
  window.addEventListener('scroll', function () { if (C.open && C.block) hole(); }, { capture: true, passive: true });
  window.addEventListener('resize', function () { if (C.open) hole(); });
  function openBlock(bid) {
    if (!cur()) return;
    C.block = bid || null; C.replyTo = C.editing = null; show(); markBlocks(); keepVisible(C.block);
    reload().then(render); render();
  }
  function openAll() {
    if (!cur()) return;
    C.block = null; C.replyTo = C.editing = null; show(); markBlocks();
    reload().then(render); render();
  }

  document.addEventListener('hk-page-open', function (e) {
    var id = e.detail && e.detail.id, same = id === C.id;
    if (!same) { C.id = id; C.threads = []; C.nres = 0; C.loaded = false; C.expanded = {}; if (C.open) hide(); }
    if (addBtn.parentNode) addBtn.remove();
    headBtn();
    var c = cur(), redraw = C.open && same;            // dieselbe Seite neu geoeffnet (z. B. Rolle geaendert): Leiste neu zeichnen
    if (c && (c.comments_open || 0) > 0) reload().then(function () { if (redraw) render(); }); else { placeBubbles(); if (redraw) render(); }
  });
  document.addEventListener('hk-page-ready', function () { placeBubbles(); });

  // Live (1.4, static/live.js): Kommentare anderer - Zahlen sofort; die offene Leiste wird neu gezeichnet, ohne dass
  // angefangene Eingaben (neuer Kommentar, Antwort, Bearbeiten) oder die Scroll-Position verloren gehen
  function liveRefresh() {
    return reload().then(function () {
      if (!C.open) return;
      // angefangene Antwort/Bearbeitung in der Liste: nicht neu zeichnen (die Unterhaltung koennte inzwischen erledigt,
      // ausgeblendet oder geloescht sein - der Text waere weg). Zahlen hat reload() schon aktualisiert; das naechste
      // Zeichnen (Senden/Abbrechen) zeigt den neuen Stand.
      if ([].some.call(list.querySelectorAll('textarea'), function (ta) { return ta.value.trim(); })) return;
      var keep = [].map.call(panel.querySelectorAll('textarea'), function (ta) {
        return { v: ta.value, a: ta.selectionStart, b: ta.selectionEnd, f: document.activeElement === ta };
      }), top = list.scrollTop;
      C.quiet = true;                                  // kein focusLater: Fokus bleibt, wo getippt wird (Seite, andere Leiste)
      try { render(); } finally { C.quiet = false; }
      var now = [].slice.call(panel.querySelectorAll('textarea'));
      var pairs = now.length === keep.length ? now.map(function (ta, i) { return [ta, keep[i]]; })
        : (now.length && keep.length ? [[now[now.length - 1], keep[keep.length - 1]]] : []);
      pairs.forEach(function (p) {
        if (!p[1].v) return;
        p[0].value = p[1].v; p[0].dispatchEvent(new Event('input', { bubbles: true }));
        if (p[1].f) { p[0].focus(); try { p[0].setSelectionRange(p[1].a, p[1].b); } catch (e) { /* egal */ } }
      });
      list.scrollTop = top;
    });
  }

  window.HKComments = { openBlock: openBlock, openAll: openAll, refresh: function () { return reload(); }, live: liveRefresh };
})();
