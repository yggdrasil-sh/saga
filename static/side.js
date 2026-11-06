// Seitenleiste fuer Dashboard und Chats (gleiches Aussehen wie /notes): Seitenbaum, Favoriten, Zuletzt, Suche,
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
  function row(p, depth, withKids) {
    var li = el('li', 'pg-node'); li.setAttribute('role', 'treeitem');
    var r = el('div', 'pg-row'); r.style.paddingLeft = (0.35 + depth * 1.05) + 'rem';
    var ks = withKids && p.kind !== 'database' ? kids(p.id) : [];
    var tw = el('button', 'pg-twisty' + (ks.length ? '' : ' leaf'), ks.length ? '▸' : ''); tw.type = 'button'; tw.tabIndex = ks.length ? 0 : -1;
    if (openState[p.id]) tw.classList.add('open');
    tw.setAttribute('aria-label', openState[p.id] ? T('Zuklappen') : T('Aufklappen'));
    tw.addEventListener('click', function (e) {
      e.stopPropagation(); openState[p.id] = !openState[p.id]; store('hk-pg-open', JSON.stringify(openState));
      var old = li.querySelector(':scope > .pg-sub'); if (old) li.removeChild(old);
      tw.classList.toggle('open', !!openState[p.id]);
      if (openState[p.id] && ks.length) li.appendChild(sub(ks, depth + 1));
    });
    var go = el('button', 'pg-go'); go.type = 'button'; go.appendChild(ico(p.icon)); go.appendChild(el('span', 'pg-name', label(p)));
    go.addEventListener('click', function () { open(p.id); });
    r.appendChild(tw); r.appendChild(go); li.appendChild(r);
    if (openState[p.id] && ks.length) li.appendChild(sub(ks, depth + 1));
    return li;
  }
  function sub(list, depth) { var u = el('ul', 'pg-sub'); u.setAttribute('role', 'group'); list.forEach(function (p) { u.appendChild(row(p, depth, true)); }); return u; }
  function favRow(p) {
    var b = el('button', 'pg-fav'); b.type = 'button'; b.appendChild(ico(p.icon)); b.appendChild(el('span', 'pg-name', label(p)));
    b.addEventListener('click', function () { open(p.id); }); var li = el('li'); li.appendChild(b); return li;
  }
  function render() {
    var q = ($('sh-search').value || '').trim().toLowerCase(), tree = $('sh-tree'); tree.textContent = '';
    if (q) {
      pages.filter(function (p) { return !p.archived && label(p).toLowerCase().indexOf(q) >= 0; }).slice(0, 40)
        .forEach(function (p) { tree.appendChild(row(p, 0, false)); });
      if (!tree.firstChild) tree.appendChild(el('li', 'pg-tree-empty', T('Keine Treffer')));
    } else {
      kids(null).forEach(function (p) { tree.appendChild(row(p, 0, true)); });
      if (!tree.firstChild) tree.appendChild(el('li', 'pg-tree-empty', T('Noch keine Seiten')));
    }
    var favs = pages.filter(function (p) { return p.pinned && !p.archived; });
    $('sh-favs').hidden = !favs.length; var fl = $('sh-fav-list'); fl.textContent = ''; favs.forEach(function (p) { fl.appendChild(favRow(p)); });
    var rec = []; try { rec = (JSON.parse(store('hk-pg-recent') || '[]') || []).map(byId).filter(Boolean).slice(0, 5); } catch (e) { rec = []; }
    $('sh-recent').hidden = !rec.length; var rl = $('sh-recent-list'); rl.textContent = ''; rec.forEach(function (p) { rl.appendChild(favRow(p)); });
  }
  $('sh-search').addEventListener('input', render);
  $('sh-search').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return; var first = $('sh-tree').querySelector('.pg-go'); if (first) { e.preventDefault(); first.click(); }
  });
  function load() {
    return HK.api('GET', '/api/notes').then(function (d) {
      pages = Array.isArray(d) ? d : ((d && d.notes) || []); render();
    }).catch(function () { /* Seitenleiste bleibt leer, Navigation geht trotzdem */ });
  }
  load();
  // Seiten geaendert (anderer Tab, anderes Geraet): Baum neu laden
  var lt = 0; document.addEventListener('hk-live', function (e) {
    var t = e && e.detail && e.detail.type; if (t && /^notes?\./.test(t)) { clearTimeout(lt); lt = setTimeout(load, 400); }
  });
})();
