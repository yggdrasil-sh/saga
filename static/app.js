(function () {
  'use strict';
  var q = document.getElementById('q');
  var H = { 'X-Saga': '1' };
  var WEB = q.dataset.web === '1';

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function wraps() { return [].slice.call(document.querySelectorAll('.tw')).filter(function (w) { return !w.hidden && !w.closest('section').hidden; }); }
  function open(w) {
    if (!w) return;
    var a = w.querySelector('a.tile');
    if (a.target === 'hk-notes') window.open(a.href, 'hk-notes'); else window.open(a.href, '_blank', 'noopener');
  }
  // Touch-Geraete: Tastenkuerzel-Hinweis "( / )" im Suchfeld weglassen
  if (window.matchMedia && matchMedia('(hover: none)').matches) q.placeholder = q.placeholder.replace(/\s*\(\s*\/\s*\)\s*$/, '');
  function renumber() {
    document.querySelectorAll('.num').forEach(function (n) { n.textContent = ''; });
    wraps().slice(0, 9).forEach(function (w, i) { w.querySelector('.num').textContent = i + 1; });
  }

  // --- Uhr & Datum (Zeitzone des Browsers) ---------------------------------
  var fT = new Intl.DateTimeFormat(window.SAGA_LOCALE, { hour: '2-digit', minute: '2-digit' });
  var fD = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  function tick() {
    var d = new Date();
    $('clk-time').textContent = fT.format(d);
    $('clk-date').textContent = fD.format(d);
  }
  tick();
  setTimeout(function () { tick(); setInterval(tick, 60000); }, (60 - new Date().getSeconds()) * 1000 + 50);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });

  // --- Icon-Fallback auf Initiale ------------------------------------------
  document.querySelectorAll('.ic img').forEach(function (img) {
    img.addEventListener('error', function () { var p = img.parentNode; p.classList.remove('has-img'); p.textContent = img.dataset.l; });
  });

  // --- Websuche (SearXNG ueber /api/search) --------------------------------
  var web = { q: '', page: 1, busy: false };

  function webHide() {
    if (!WEB) return;
    $('web').hidden = true; $('web-list').textContent = ''; $('web-meta').textContent = '';
    $('web-more').hidden = true; web.q = '';
  }
  function webGoUpdate(v) {
    if (!WEB) return;
    $('web-go').hidden = !v;
    $('web-go-q').textContent = v;
  }
  function chip(text, onClick) {
    var b = el('button', 'chip chip-btn', text); b.type = 'button';
    b.addEventListener('click', onClick); return b;
  }
  function renderMeta(d) {
    var m = $('web-meta'); m.textContent = '';
    (d.answers || []).forEach(function (a) { m.appendChild(el('div', 'web-answer', a)); });
    var tips = (d.corrections || []).concat(d.suggestions || []);
    if (tips.length) {
      var row = el('div', 'web-tips'); row.appendChild(el('span', 'web-tips-l', T('Meintest du:')));
      tips.forEach(function (t) { row.appendChild(chip(t, function () { q.value = t; webSearch(t, 1); })); });
      m.appendChild(row);
    }
    if (d.unresponsive && d.unresponsive.length)
      m.appendChild(el('div', 'web-warn', T('Nicht geantwortet: {list}', { list: d.unresponsive.join(', ') })));
  }
  function renderResults(list) {
    var ol = $('web-list');
    list.forEach(function (r) {
      var li = el('li', 'web-item');
      var a = el('a', 'web-title', r.title); a.href = r.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      li.appendChild(a);
      li.appendChild(el('div', 'web-host', r.host + (r.engines && r.engines.length ? '  ·  ' + r.engines.join(', ') : '')));
      if (r.content) li.appendChild(el('p', 'web-snip', r.content));
      ol.appendChild(li);
    });
  }
  function webSearch(text, page) {
    if (!WEB || web.busy) return;
    text = (text || '').trim(); if (!text) return;
    web.busy = true; web.q = text; web.page = page;
    var sec = $('web'); sec.hidden = false; webGoUpdate('');
    if (page === 1) { $('web-list').textContent = ''; $('web-meta').textContent = ''; $('web-meta').appendChild(el('div', 'web-load', T('Suche läuft …'))); }
    $('web-more').hidden = true;
    fetch('/api/search', { method: 'POST', credentials: 'same-origin',
      headers: Object.assign({ 'Content-Type': 'application/json' }, H),
      body: JSON.stringify({ q: text, page: page }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (page === 1) $('web-meta').textContent = '';
        if (!x.ok) { $('web-meta').appendChild(el('div', 'web-warn', x.d.error || T('Fehler bei der Suche'))); return; }
        if (page === 1) renderMeta(x.d);
        renderResults(x.d.results || []);
        if (page === 1 && !(x.d.results || []).length) $('web-meta').appendChild(el('div', 'web-warn', T('Keine Ergebnisse.')));
        $('web-more').hidden = !(x.d.results || []).length || page >= 10;
        if (page === 1) sec.scrollIntoView({ block: 'start', behavior: 'smooth' });
      })
      .catch(function () { $('web-meta').textContent = ''; $('web-meta').appendChild(el('div', 'web-warn', T('Suche nicht erreichbar.'))); })
      .then(function () { web.busy = false; });
  }
  if (WEB) {
    $('web-go').addEventListener('click', function () { webSearch(q.value, 1); });
    $('web-more').addEventListener('click', function () { webSearch(web.q, web.page + 1); });
    $('web-close').addEventListener('click', function () { webHide(); q.focus(); });
  }

  // --- Seiten-Suche (Volltext wie in der Seitenleiste; Treffer nach den Kacheln) ------------------
  var pq = { t: null, n: 0 };
  function pageHits(v) {
    var sec = $('q-pages'), box = $('q-pages-list'), n = ++pq.n;
    clearTimeout(pq.t);
    if (!sec) return;
    if (v.length < 2) { sec.hidden = true; box.textContent = ''; renumber(); return; }
    pq.t = setTimeout(function () {
      fetch('/api/notes/search', { method: 'POST', credentials: 'same-origin',
        headers: Object.assign({ 'Content-Type': 'application/json' }, H), body: JSON.stringify({ q: v }) })
        .then(function (r) { return r.ok ? r.json() : []; })
        .then(function (hits) {
          if (n !== pq.n) return;                       // inzwischen weitergetippt
          box.textContent = '';
          (hits || []).slice(0, 8).forEach(function (h) {
            var w = el('div', 'tw tw-page'), a = el('a', 'tile');
            a.href = '/notes#' + h.id; a.target = 'hk-notes';
            var icon = typeof h.icon === 'string' && h.icon && h.icon.indexOf('/') < 0 && h.icon.length <= 8 ? h.icon : '📄';
            a.appendChild(el('span', 'ic ic-emoji', icon));
            var tx = el('span', 'txt'); tx.appendChild(el('span', 'nm', h.title || T('Unbenannt')));
            if (h.snippet) tx.appendChild(el('span', 'host', h.snippet));
            a.appendChild(tx); a.appendChild(el('span', 'num'));
            w.appendChild(a); box.appendChild(w);
          });
          sec.hidden = !box.children.length;
          renumber();
        }).catch(function () {});
    }, 220);
  }

  // --- Kachel-Suche ---------------------------------------------------------
  q.addEventListener('input', function () {
    var v = q.value.trim().toLowerCase();
    document.querySelectorAll('section.cat').forEach(function (sec) {
      var any = false;
      sec.querySelectorAll('.tw').forEach(function (w) { var m = !v || w.dataset.q.indexOf(v) > -1; w.hidden = !m; if (m) any = true; });
      sec.hidden = !any;
    });
    webGoUpdate(q.value.trim());
    renumber();
    pageHits(q.value.trim());
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      var hits = wraps();
      if (WEB && (e.shiftKey || (!hits.length && q.value.trim()))) webSearch(q.value, 1);
      else open(hits[0]);
    }
    if (e.key === 'Escape') {
      if (WEB && !$('web').hidden) { webHide(); return; }
      q.value = ''; q.dispatchEvent(new Event('input')); q.blur();
    }
  });

  // --- Tastenkuerzel --------------------------------------------------------
  document.addEventListener('keydown', function (e) {
    // Nie reagieren, waehrend irgendwo getippt wird (Suchfeld, Aufgaben, Notizen …)
    var t = e.target;
    if (t === q || e.metaKey || e.ctrlKey || e.altKey) return;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    if (e.key === '/') { e.preventDefault(); q.focus(); return; }
    if (e.key === 'Escape' && WEB && !$('web').hidden) { webHide(); return; }
    if (/^[1-9]$/.test(e.key)) { e.preventDefault(); open(wraps()[+e.key - 1]); }
  });

  // --- Favoriten ------------------------------------------------------------
  document.querySelectorAll('button.star').forEach(function (b) {
    b.addEventListener('click', function () {
      b.disabled = true;
      fetch('/api/fav', { method: 'POST', credentials: 'same-origin',
        headers: Object.assign({ 'Content-Type': 'application/json' }, H),
        body: JSON.stringify({ group: b.dataset.g }) })
        .then(function (r) { if (r.ok) location.reload(); else b.disabled = false; })
        .catch(function () { b.disabled = false; });
    });
  });

  // --- Eigene Lesezeichen + Konto (Dialog aus settings.js) -----------------
  function settings(fn) { if (window.HKSettings) fn(window.HKSettings); }
  document.querySelectorAll('[data-settings]').forEach(function (b) {
    b.addEventListener('click', function () { settings(function (S) { S.open(b.dataset.settings); }); });
  });
  var bmAdd = $('bm-add');
  if (bmAdd) bmAdd.addEventListener('click', function () { settings(function (S) { S.editBookmark(null); }); });
  document.querySelectorAll('button.bm-edit').forEach(function (b) {
    b.addEventListener('click', function () { settings(function (S) { S.editBookmark(b.dataset.bm); }); });
  });

  // --- Status-Punkte --------------------------------------------------------
  var LABEL = { up: T('online'), warn: T('langsam'), down: T('nicht erreichbar') };
  function status() {
    fetch('/api/status', { credentials: 'same-origin', headers: H })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        document.querySelectorAll('.tw').forEach(function (w) {
          var s = d[w.dataset.g], dot = w.querySelector('.dot');
          if (!dot) return;                       // eigene Lesezeichen: keine Statuspruefung
          dot.className = 'dot ' + (s ? s.state : 'unknown');
          dot.title = s ? LABEL[s.state] + (s.ms != null ? ' · ' + s.ms + ' ms' : '') + (s.code ? ' · HTTP ' + s.code : '') : T('Status wird geprüft');
        });
      }).catch(function () {});
  }
  status();
  setInterval(status, 60000);
  renumber();
})();
