// Schnellsuche (Strg/⌘+K) als gemeinsames Modul (UX-10): dieselbe Oberflaeche auf /notes (static/pages.js) und auf den
// Seiten mit globaler Seitenleiste (Heute, Chats: static/side.js). Seiten, "Zuletzt geoeffnet", Volltext-Treffer,
// ">"-Befehle, "+ Neue Seite" und "Im Web suchen" (SearXNG). Was eine Seite oeffnet, anlegt oder als Befehl anbietet,
// gibt der Aufrufer mit (cfg); die Markierung #pg-qs legt das Modul selbst an, wenn das Template sie nicht hat.
//   var Q = HK.quickSearch({ pages, byId, recents, label, icon, path, tags, owner, grpIc, open, create, actions, web })
//   Q.open({ key: true })  -> Fokus beim Schliessen zurueck an den Ausloeser (Tastatur: an das, was vorher Fokus hatte)
(function () {
  'use strict';
  var HK = window.HK; if (!HK) return;
  var T = window.T || function (s) { return s; }, el = HK.el;

  function markup() {
    var ov = document.getElementById('pg-qs');
    if (ov) return ov;
    ov = el('div', 'qs'); ov.id = 'pg-qs'; ov.hidden = true;
    var box = el('div', 'qs-box'); box.setAttribute('role', 'dialog'); box.setAttribute('aria-modal', 'true'); box.setAttribute('aria-label', T('Schnellsuche'));
    var q = el('input'); q.type = 'text'; q.id = 'pg-qs-q'; q.placeholder = T('Seite oder Befehl suchen …'); q.autocomplete = 'off'; q.setAttribute('aria-label', T('Seite suchen'));
    var list = el('div', 'qs-list'); list.id = 'pg-qs-list'; list.setAttribute('role', 'listbox');
    box.appendChild(q); box.appendChild(list); ov.appendChild(box); document.body.appendChild(ov);
    return ov;
  }

  function quickSearch(cfg) {
    var ov = markup(), inp = document.getElementById('pg-qs-q'), list = document.getElementById('pg-qs-list');
    var qs = { items: [], idx: 0 };
    function F(name, dflt) { return typeof cfg[name] === 'function' ? cfg[name] : dflt; }
    var label = F('label', function (p) { return p && p.title ? p.title : T('Unbenannt'); });
    var icon = F('icon', function (p) { return p && p.icon; });
    var tags = F('tags', function () { return []; });
    var path = F('path', function () { return ''; });
    var search = F('search', function (q) { return HK.api('POST', '/api/notes/search', { q: q }); });

    function isOpen() { return !ov.hidden; }
    function open(o) {
      o = o || {};
      if (!ov.hidden) { inp.focus(); return; }
      var go = function () {
        ov.hidden = false; inp.value = ''; qs.web = null; render();
        // Per Tastatur geoeffnet: Fokus spaeter an das, was ihn vorher hatte - ohne Fokus an den Hauptbereich (body), nicht an
        // einen zuletzt geschlossenen Menueknopf (Pruefung UX-10)
        var a = document.activeElement, back = o.back || (o.key ? (a && a !== document.body ? a : document.body) : null);
        qs.pf = HK.panelFocus(ov.querySelector('.qs-box'), { trap: true, initial: '#pg-qs-q', back: back || undefined }); inp.focus();
      };
      if (cfg.ready) { var r = cfg.ready(); if (r && r.then) { r.then(function () { if (ov.hidden) go(); else render(); }, go); return; } }
      go();
    }
    function close() { ov.hidden = true; clearTimeout(qs.timer); qs.token = (qs.token || 0) + 1; if (qs.pf) { qs.pf.release(); qs.pf = null; } }
    function item(text, sub, fn, keep) {
      var b = el('button', 'picker-item'); b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(el('span', 'picker-t', text)); if (sub) b.appendChild(el('span', 'picker-sub', sub));
      b.addEventListener('click', function () { if (!keep) close(); fn(); });
      qs.items.push({ b: b, fn: fn, keep: keep }); list.appendChild(b);
      return b;
    }
    function grp() { return cfg.grpIc ? cfg.grpIc() : document.createTextNode(''); }
    function render() {
      var raw = inp.value.trim(), q = raw.toLowerCase();
      list.textContent = ''; qs.items = []; qs.idx = 0;
      var cmdOnly = q.charAt(0) === '>'; if (cmdOnly) q = q.slice(1).trim();
      var all = (cfg.pages ? cfg.pages() : []) || [];
      var rec = (cfg.recents ? cfg.recents() : []).map(cfg.byId).filter(Boolean);
      var hits = cmdOnly ? [] : q ? all.filter(function (p) { return (label(p) + ' ' + tags(p).join(' ')).toLowerCase().indexOf(q) >= 0; })
                                       .sort(function (a, b) { return (rec.indexOf(b) >= 0) - (rec.indexOf(a) >= 0) || b.updated - a.updated; })
                   : rec.concat(all.filter(function (p) { return rec.indexOf(p) < 0; }).sort(function (a, b) { return b.updated - a.updated; }));
      if (!q && !cmdOnly) list.appendChild(el('div', 'menu-label', rec.length ? T('Zuletzt geöffnet') : T('Zuletzt bearbeitet')));
      hits.slice(0, q ? 8 : 10).forEach(function (p) {
        var b = item((icon(p) || '📄') + ' ' + label(p), path(p), function () { cfg.open(p.id); }), ps = b.querySelector('.picker-sub');
        if (p.owner && ps) { ps.insertBefore(document.createTextNode(' '), ps.firstChild); ps.insertBefore(grp(), ps.firstChild); }
      });
      var acts = (cfg.actions ? cfg.actions() : []).filter(function (a) { return !q || (a.t + ' ' + (a.kw || '')).toLowerCase().indexOf(q) >= 0; });
      var cmdAt = qs.items.length, cmdLabel = null;
      if (acts.length && (q || cmdOnly)) {
        list.appendChild(cmdLabel = el('div', 'menu-label', T('Befehle')));
        acts.slice(0, cmdOnly ? 30 : 6).forEach(function (a) {
          if (a.ic !== 'grp') { item(a.ic + ' ' + a.t, a.kbd || '', a.fn); return; }
          var t = item(' ' + a.t, a.kbd || '', a.fn).querySelector('.picker-t'); t.insertBefore(grp(), t.firstChild);   // einfarbiges Symbol
        });
      } else if (!q) list.appendChild(el('div', 'pg-qs-hint', T('Tipp: „>“ zeigt alle Befehle')));
      if (q && !cmdOnly && cfg.create) item(T('+ Neue Seite „{name}“', { name: raw }), '', function () { cfg.create(raw); });
      if (q && !cmdOnly && cfg.web) item('🌐 ' + T('Im Web suchen: „{q}“', { q: raw }), '', function () { web(raw, 1); }, true);
      if (!qs.items.length) list.appendChild(el('div', 'pg-noresult', T('Nichts gefunden.')));
      qs.items.forEach(function (it, i) { it.b.classList.toggle('on', i === 0); });
      // Volltext (wie "Seiten durchsuchen"): Treffer im Inhalt nachladen, vor den Befehlen einfuegen
      var token = qs.token = (qs.token || 0) + 1; clearTimeout(qs.timer);
      if (!q || cmdOnly || q.length < 2) return;
      qs.timer = setTimeout(function () {
        search(q).then(function (res) {
          if (token !== qs.token || ov.hidden) return;
          var seen = {}; hits.forEach(function (p) { seen[p.id] = 1; });
          var extra = (res || []).filter(function (h) { return !seen[h.id] && cfg.byId(h.id); }).slice(0, 6);
          if (!extra.length) return;
          var at = cmdLabel ? cmdAt : qs.items.length - 1, before = cmdLabel || (qs.items[at] ? qs.items[at].b : null);
          list.insertBefore(el('div', 'menu-label', T('Im Inhalt gefunden')), before);
          var add = extra.map(function (h) {
            var b = el('button', 'picker-item qs-hit'); b.type = 'button'; b.setAttribute('role', 'option');
            b.appendChild(el('span', 'picker-t', (icon(h) || '📄') + ' ' + label(h)));
            if (h.snippet) b.appendChild(el('span', 'pg-hit-s', h.snippet));
            var fn = function () { cfg.open(h.id); };
            b.addEventListener('click', function () { close(); fn(); });
            list.insertBefore(b, before); return { b: b, fn: fn };
          });
          // Ohne Titel-Treffer stand die Auswahl auf "+ Neue Seite" -> jetzt den ersten Inhalts-Treffer waehlen
          var curItem = qs.items[qs.idx], wasNew = !hits.length && qs.idx === qs.items.length - 1;
          Array.prototype.splice.apply(qs.items, [at, 0].concat(add));
          qs.idx = wasNew ? at : Math.max(0, qs.items.indexOf(curItem)); highlight();
        }).catch(function () {});
      }, 200);
    }
    // Websuche (SearXNG ueber /api/search, POST: Begriffe nicht im Access-Log): Ergebnisse ersetzen die Liste; Enter/Klick
    // oeffnet den Treffer in einem neuen Tab, "Mehr laden" haengt die naechste Seite an, Tippen fuehrt zurueck zu den Seiten
    function web(text, page) {
      if (!text || (qs.web && qs.web.busy)) return;
      if (page === 1) { list.textContent = ''; qs.items = []; qs.idx = 0; list.appendChild(el('div', 'menu-label', T('Web · „{q}“', { q: text }))); }
      var more = list.querySelector('.qs-web-more'); if (more) { qs.items = qs.items.filter(function (it) { return it.b !== more; }); more.remove(); }
      var load = el('div', 'pg-qs-hint', T('Suche läuft …')); list.appendChild(load);
      qs.web = { q: text, page: page, busy: true };
      fetch('/api/search', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
        body: JSON.stringify({ q: text, page: page }) })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (x) {
          load.remove(); if (ov.hidden || !qs.web || qs.web.q !== text) return;
          if (!x.ok) { list.appendChild(el('div', 'pg-noresult', x.d.error || T('Fehler bei der Suche'))); return; }
          if (page === 1) (x.d.answers || []).forEach(function (a) { list.appendChild(el('div', 'qs-web-answer', a)); });
          var start = qs.items.length;
          (x.d.results || []).forEach(function (r) {
            var b = el('a', 'picker-item qs-hit qs-web'); b.href = r.url; b.target = '_blank'; b.rel = 'noopener noreferrer'; b.setAttribute('role', 'option');
            b.appendChild(el('span', 'picker-t', r.title)); b.appendChild(el('span', 'qs-web-host', r.host));
            if (r.content) b.appendChild(el('span', 'pg-hit-s', r.content));
            qs.items.push({ b: b, fn: function () { b.click(); }, keep: true }); list.appendChild(b);
          });
          if (page === 1 && !(x.d.results || []).length) list.appendChild(el('div', 'pg-noresult', T('Keine Ergebnisse.')));
          if ((x.d.results || []).length && page < 10) {
            var m = el('button', 'picker-item qs-web-more'); m.type = 'button'; m.setAttribute('role', 'option');
            m.appendChild(el('span', 'picker-t', T('Mehr laden'))); var mf = function () { web(text, page + 1); };
            m.addEventListener('click', mf); qs.items.push({ b: m, fn: mf, keep: true }); list.appendChild(m);
          }
          if (page === 1) { qs.idx = 0; highlight(); } else if (qs.items[start]) { qs.idx = start; highlight(); }
        })
        .catch(function () { load.remove(); list.appendChild(el('div', 'pg-noresult', T('Suche nicht erreichbar.'))); })
        .then(function () { if (qs.web && qs.web.q === text) qs.web.busy = false; });
    }
    function highlight() { qs.items.forEach(function (it, i) { it.b.classList.toggle('on', i === qs.idx); if (i === qs.idx) it.b.scrollIntoView({ block: 'nearest' }); }); }
    inp.addEventListener('input', render);
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); qs.idx = Math.min(qs.idx + 1, qs.items.length - 1); highlight(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); qs.idx = Math.max(qs.idx - 1, 0); highlight(); }
      else if (e.key === 'Enter') { e.preventDefault(); var it = qs.items[qs.idx]; if (it) { if (!it.keep) close(); it.fn(); } }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    });
    // A11Y-25/UX-26: Esc ueberall im Dialog, Klick daneben schliesst
    ov.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !ov.hidden) { e.preventDefault(); e.stopPropagation(); close(); } });
    ov.addEventListener('mousedown', function (e) { if (e.target === ov) close(); });
    return { open: open, close: close, isOpen: isOpen, render: render };
  }
  HK.quickSearch = quickSearch;
})();
