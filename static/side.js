// Seitenleiste fuer Dashboard und Chats (gleiches Aussehen wie /notes): Seitenbaum, Favoriten, Suche,
// Ein-/Ausblenden (Strg/⌘+\, wie /notes gemerkt) und Herein-Schweben am linken Rand. Seiten oeffnen sich auf /notes.
(function () {
  'use strict';
  var HK = window.HK, T = (HK && HK.t) || function (s) { return s; };
  var side = document.getElementById('pg-side');
  if (!HK || !side || !side.classList.contains('shell-side') || document.body.classList.contains('embed')) return;
  var body = document.body, $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function store(k, v) { try { return HK.store(k, v); } catch (e) { return null; } }
  function isImg(i) { return typeof i === 'string' && (i.indexOf('/files/') === 0 || i.indexOf('/api/fed/files/') === 0); }
  function ico(icon) {
    if (isImg(icon)) { var im = el('img', 'pg-ic ic-img'); im.alt = ''; im.decoding = 'async'; im.src = /^\/files\/[0-9a-f]{24}$/.test(icon) ? icon + '?w=160' : icon; return im; }
    return el('span', 'pg-ic', icon || '📄');
  }
  function label(p) { return p && p.title ? p.title : T('Unbenannt'); }
  function open(id) { location.href = '/notes#' + id; }

  // --- Ein/aus, Schweben ------------------------------------------------------------------------------------------
  var fine = window.matchMedia('(hover: hover) and (pointer: fine)'), wide = window.matchMedia('(min-width: 761px)');
  function setSide(show) {
    peek(false, true);
    body.classList.toggle('side-hidden', !show);
    var b = $('pg-side-btn'); if (b) b.setAttribute('aria-pressed', show ? 'true' : 'false');
    store('hk-pg-side', show ? '1' : '0');
  }
  var peekT = 0;
  function peek(on, now) {
    clearTimeout(peekT);
    if (!on) { if (now) body.classList.remove('side-peek'); else peekT = setTimeout(function () { body.classList.remove('side-peek'); }, 280); return; }
    if (fine.matches && wide.matches && body.classList.contains('side-hidden')) body.classList.add('side-peek');
  }
  setSide(store('hk-pg-side') !== '0');
  var zone = el('div', 'pg-peek-zone'); zone.setAttribute('aria-hidden', 'true'); body.appendChild(zone);
  [zone, document.querySelector('.np-head'), side].forEach(function (x) {
    if (!x) return;
    x.addEventListener('mouseenter', function () { peek(true); });
    x.addEventListener('mouseleave', function () { if (body.classList.contains('side-peek')) peek(false); });
  });
  var tb = $('pg-side-btn'); if (tb) tb.addEventListener('click', function () { setSide(body.classList.contains('side-hidden')); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && body.classList.contains('side-peek')) { peek(false, true); return; }
    if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === '\\' || e.code === 'Backslash')) { e.preventDefault(); setSide(body.classList.contains('side-hidden')); }
  });

  // --- Chat-Liste in der Seitenleiste (Chats-Seite, Bildschirm breit) ------------------------------------------------
  // Die Liste (#ct-side mit Filter, "Neu", Unterhaltungen, "Chats & Geraete") wird samt ihrer Knoten umgesetzt - chat.js
  // zeichnet unveraendert in #ct-list weiter. Auf schmalen Bildschirmen (Seitenleiste dort aus) steht sie wie bisher links.
  (function () {
    var slot = $('sh-chats'), head = $('sh-chats-h'), cs = $('ct-side'), box = $('ct');
    if (!slot || !cs || !box) return;
    var wideMq = window.matchMedia('(min-width: 761px)');
    function place() {
      var inSide = wideMq.matches;
      if (inSide) slot.appendChild(cs); else box.insertBefore(cs, box.firstChild);
      slot.hidden = head.hidden = !inSide; body.classList.toggle('chats-in-side', inSide);
    }
    place();
    if (wideMq.addEventListener) wideMq.addEventListener('change', place); else if (wideMq.addListener) wideMq.addListener(place);
  })();

  // --- Seiten -----------------------------------------------------------------------------------------------------
  var pages = [], openState = {};
  try { openState = JSON.parse(store('hk-pg-open') || '{}') || {}; } catch (e) { openState = {}; }
  function kids(id) { return pages.filter(function (p) { return (p.parent_id == null ? null : p.parent_id) === id && !p.archived; }); }
  function byId(id) { for (var i = 0; i < pages.length; i++) if (pages[i].id === id) return pages[i]; return null; }
  // A11Y-11: echter Baum (static/treenav.js) - ein Tab-Stopp, Pfeiltasten; Name = nur der Seitenname
  var TN = HK.treeNav;
  function row(p, depth, withKids, pos, size) {
    var li = el('li', 'pg-node');
    var r = el('div', 'pg-row'); r.style.paddingLeft = (0.35 + depth * 1.05) + 'rem';
    var ks = withKids && p.kind !== 'database' ? kids(p.id) : [];
    if (TN) TN.item(li, { id: p.id, level: depth + 1, pos: pos, size: size, expanded: ks.length ? !!openState[p.id] : null, name: label(p),
                          desc: [p.template ? T('Vorlage') : '', p.shared ? T('Geteilt') : ''].filter(Boolean).join(', ') });
    else li.setAttribute('role', 'treeitem');
    var tw = el('button', 'pg-twisty' + (ks.length ? '' : ' leaf'), ks.length ? '▸' : ''); tw.type = 'button'; tw.tabIndex = -1;
    if (openState[p.id]) tw.classList.add('open');
    if (ks.length) tw.setAttribute('aria-label', openState[p.id] ? T('Zuklappen') : T('Aufklappen')); else tw.setAttribute('aria-hidden', 'true');
    tw.addEventListener('click', function (e) {
      e.stopPropagation(); if (!ks.length) return;
      openState[p.id] = !openState[p.id]; store('hk-pg-open', JSON.stringify(openState));
      var old = li.querySelector(':scope > .pg-sub'); if (old) li.removeChild(old);
      tw.classList.toggle('open', !!openState[p.id]);
      tw.setAttribute('aria-label', openState[p.id] ? T('Zuklappen') : T('Aufklappen'));
      li.setAttribute('aria-expanded', openState[p.id] ? 'true' : 'false');
      if (openState[p.id]) li.appendChild(sub(ks, depth + 1));
    });
    var go = el('div', 'pg-go'); go.setAttribute('data-tn-go', '');
    var ic = ico(p.icon); ic.setAttribute('aria-hidden', 'true'); go.appendChild(ic); go.appendChild(el('span', 'pg-name', label(p)));
    go.addEventListener('click', function () { open(p.id); });
    r.appendChild(tw); r.appendChild(go); li.appendChild(r);
    if (openState[p.id] && ks.length) li.appendChild(sub(ks, depth + 1));
    return li;
  }
  function sub(list, depth) {
    var u = el('ul', 'pg-sub'); u.setAttribute('role', 'group');
    list.forEach(function (p, i) { u.appendChild(row(p, depth, true, i + 1, list.length)); }); return u;
  }
  function favRow(p, i, n) {
    var b = el('div', 'pg-fav'); b.setAttribute('data-tn-go', '');
    var ic = ico(p.icon); ic.setAttribute('aria-hidden', 'true'); b.appendChild(ic); b.appendChild(el('span', 'pg-name', label(p)));
    b.addEventListener('click', function () { open(p.id); });
    var li = el('li', 'pg-fav-node');
    if (TN) TN.item(li, { id: p.id, level: 1, pos: i + 1, size: n, name: label(p) }); else li.setAttribute('role', 'treeitem');
    li.appendChild(b); return li;
  }
  function keep(ul, build) { if (TN) TN.keep(ul, build); else build(); }
  function render() {
    var q = ($('sh-search').value || '').trim().toLowerCase(), tree = $('sh-tree');
    keep(tree, function () {
      tree.textContent = '';
      if (q) {
        var hits = pages.filter(function (p) { return !p.archived && label(p).toLowerCase().indexOf(q) >= 0; }).slice(0, 40);
        hits.forEach(function (p, i) { tree.appendChild(row(p, 0, false, i + 1, hits.length)); });
        if (!tree.firstChild) tree.appendChild(el('li', 'pg-tree-empty', T('Keine Treffer')));
      } else {
        var top = kids(null);
        top.forEach(function (p, i) { tree.appendChild(row(p, 0, true, i + 1, top.length)); });
        if (!tree.firstChild) tree.appendChild(el('li', 'pg-tree-empty', T('Noch keine Seiten')));
      }
    });
    var favs = pages.filter(function (p) { return p.pinned && !p.archived; });
    $('sh-favs').hidden = !favs.length; var fl = $('sh-fav-list');
    keep(fl, function () { fl.textContent = ''; favs.forEach(function (p, i) { fl.appendChild(favRow(p, i, favs.length)); }); });
  }
  $('sh-search').addEventListener('input', render);
  $('sh-search').addEventListener('keydown', function (e) {
    var t = $('sh-tree'), first;
    if (e.key === 'ArrowDown' && TN) { first = TN.items(t)[0]; if (first) { e.preventDefault(); TN.focus(t, first); } return; }   // in den Baum
    if (e.key !== 'Enter') return; first = t.querySelector('.pg-go'); if (first) { e.preventDefault(); first.click(); }
  });
  function load() {
    return HK.api('GET', '/api/notes').then(function (d) {
      pages = Array.isArray(d) ? d : ((d && d.notes) || []); render();
    }).catch(function () { /* Seitenleiste bleibt leer, Navigation geht trotzdem */ });
  }
  load();

  // --- Schnellsuche (UX-10): Strg/⌘+K auch hier als Overlay (static/qs.js, wie auf /notes); Seiten oeffnen auf /notes ---------
  var shared = null;
  function allPages() { return pages.filter(function (p) { return !p.archived; }).concat(shared || []); }
  function anyById(id) { return byId(id) || (shared || []).filter(function (p) { return p.id === id; })[0] || null; }
  function ancestors(id) { var out = [], p = anyById(id), guard = 0; while (p && p.parent_id && guard++ < 50) { p = anyById(p.parent_id); if (p) out.unshift(p); } return out; }
  function go(url) { location.href = url; }
  var mk = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent) ? '⌘' : T('Strg') + '+';
  function actions() {
    var a = [], chats = !!side.querySelector('a[href="/chats"]'), cur = body.classList.contains('chats-page') ? 'chats' : 'today';
    function add(t, ic, fn, o) { o = o || {}; a.push({ t: t, ic: ic, fn: fn, kw: o.kw, kbd: o.kbd }); }
    add(T('Neue Seite'), '＋', function () { go('/notes?new=blank'); }, { kw: 'new page anlegen erstellen', kbd: mk + (mk === '⌘' ? '⌥N' : 'Alt+N') });
    add(T('Vorlagengalerie'), '🖼', function () { go('/notes?gallery=1'); }, { kw: 'gallery vorlagen templates galerie vorlage' });
    add(T('Aufgaben'), '✓', function () { go('/notes?v=tasks'); }, { kw: 'tasks todo' });
    add(T('Termine'), '🗓', function () { go('/notes?v=events'); }, { kw: 'events kalender calendar' });
    add(T('Graph'), '✳', function () { go('/notes?graph=1'); }, { kw: 'graph netz verbindungen links obsidian' });
    add(T('Archiv anzeigen'), '📦', function () { go('/notes?archive=1'); }, { kw: 'archiv archive' });
    add(T('Papierkorb'), '🗑', function () { go('/notes?trash=1'); }, { kw: 'trash geloescht' });
    add(T('Seitenleiste ein/aus'), '◧', function () { setSide(body.classList.contains('side-hidden')); }, { kw: 'sidebar', kbd: mk + '\\' });
    if (window.HKSettings) add(T('Einstellungen'), '⚙', function () { HKSettings.open(); }, { kw: 'settings kalender immich nextcloud schluessel' });
    if (window.HKShortcuts) add(T('Tastenkürzel'), '⌨', function () { HKShortcuts.open(); }, { kw: 'shortcuts tastatur kuerzel hotkeys keyboard hilfe', kbd: mk + '/' });
    if (cur !== 'today') add(T('Heute'), '⌂', function () { go('/dashboard'); }, { kw: 'home dashboard start today übersicht' });
    if (chats && cur !== 'chats') add(T('Chats'), '💬', function () { go('/chats'); }, { kw: 'chat nachrichten messages' });
    return a;
  }
  var QS = HK.quickSearch ? HK.quickSearch({
    pages: allPages, byId: anyById, label: label, icon: function (p) { return isImg(p.icon) ? '' : (p.icon || ''); },
    recents: function () { try { return (JSON.parse(store('hk-pg-recent') || '[]') || []).filter(function (x) { return typeof x === 'number'; }); } catch (e) { return []; } },
    tags: function (p) { return p && Array.isArray(p.tags) ? p.tags.filter(function (t) { return typeof t === 'string'; }) : []; },
    path: function (p) { var an = ancestors(p.id); return (p.owner ? p.owner.name + (an.length ? ' · ' : '') : '') + an.map(label).join(' / '); },
    grpIc: function () { var s = el('span', 'ic-grp'); s.setAttribute('aria-hidden', 'true'); return s; },
    open: open,
    create: function (title) {
      HK.api('POST', '/api/notes', { title: title }).then(function (d) { open(d.id); })
        .catch(function (err) { HK.toast(T('Seite nicht angelegt: {err}', { err: err.message })); });
    },
    actions: actions,
    web: body.dataset.web === '1',
    // Mit mir geteilte Seiten beim ersten Oeffnen nachladen (der Seitenbaum kommt schon beim Laden)
    ready: function () {
      if (shared) return null;
      return HK.api('GET', '/api/shared').then(function (d) { shared = Array.isArray(d) ? d : []; }, function () { shared = []; });
    }
  }) : null;
  function otherModal() {
    return [].some.call(document.querySelectorAll('[aria-modal="true"], dialog[open]'), function (d) {
      return !d.closest('#pg-qs') && !d.closest('[hidden]') && d.getClientRects().length;
    });
  }
  if (QS) {
    HK.openQuickSearch = function (o) { QS.open(o); };
    var qb = $('sh-qs-btn');
    if (qb) qb.addEventListener('click', function (e) { e.preventDefault(); QS.open(); });
    document.addEventListener('keydown', function (e) {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || (e.key || '').toLowerCase() !== 'k') return;
      if (!QS.isOpen() && otherModal()) return;          // anderer Dialog offen (Einstellungen, Rueckfrage, Chat-Dialoge)
      e.preventDefault(); QS.open({ key: true });
    });
  }
  // Seiten geaendert (anderer Tab, anderes Geraet): Baum neu laden
  var lt = 0; document.addEventListener('hk-live', function (e) {
    var t = e && e.detail && e.detail.type; if (t && /^notes?\./.test(t)) { clearTimeout(lt); lt = setTimeout(load, 400); }
  });
})();
