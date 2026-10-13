(function () {
  'use strict';
  var q = document.getElementById('q');
  var H = { 'X-Saga': '1' };
  var WEB = q.dataset.web === '1';

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function wraps() { return [].slice.call(document.querySelectorAll('.tw')).filter(function (w) { return !w.hidden && !w.closest('section').hidden; }); }
  function open(w) { if (w) window.open(w.querySelector('a.tile').href, '_blank', 'noopener'); }
  function renumber() {
    document.querySelectorAll('.num').forEach(function (n) { n.textContent = ''; });
    wraps().slice(0, 9).forEach(function (w, i) { w.querySelector('.num').textContent = i + 1; });
  }

  // --- Uhr & Datum (Zeitzone des Browsers) ---------------------------------
  var fT = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' });
  var fD = new Intl.DateTimeFormat('de-DE', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
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
      var row = el('div', 'web-tips'); row.appendChild(el('span', 'web-tips-l', 'Meintest du:'));
      tips.forEach(function (t) { row.appendChild(chip(t, function () { q.value = t; webSearch(t, 1); })); });
      m.appendChild(row);
    }
    if (d.unresponsive && d.unresponsive.length)
      m.appendChild(el('div', 'web-warn', 'Nicht geantwortet: ' + d.unresponsive.join(', ')));
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
    if (page === 1) { $('web-list').textContent = ''; $('web-meta').textContent = ''; $('web-meta').appendChild(el('div', 'web-load', 'Suche läuft …')); }
    $('web-more').hidden = true;
    fetch('/api/search', { method: 'POST', credentials: 'same-origin',
      headers: Object.assign({ 'Content-Type': 'application/json' }, H),
      body: JSON.stringify({ q: text, page: page }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (x) {
        if (page === 1) $('web-meta').textContent = '';
        if (!x.ok) { $('web-meta').appendChild(el('div', 'web-warn', x.d.error || 'Fehler bei der Suche')); return; }
        if (page === 1) renderMeta(x.d);
        renderResults(x.d.results || []);
        if (page === 1 && !(x.d.results || []).length) $('web-meta').appendChild(el('div', 'web-warn', 'Keine Ergebnisse.'));
        $('web-more').hidden = !(x.d.results || []).length || page >= 10;
        if (page === 1) sec.scrollIntoView({ block: 'start', behavior: 'smooth' });
      })
      .catch(function () { $('web-meta').textContent = ''; $('web-meta').appendChild(el('div', 'web-warn', 'Suche nicht erreichbar.')); })
      .then(function () { web.busy = false; });
  }
  if (WEB) {
    $('web-go').addEventListener('click', function () { webSearch(q.value, 1); });
    $('web-more').addEventListener('click', function () { webSearch(web.q, web.page + 1); });
    $('web-close').addEventListener('click', function () { webHide(); q.focus(); });
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
  var LABEL = { up: 'online', warn: 'langsam', down: 'nicht erreichbar' };
  function status() {
    fetch('/api/status', { credentials: 'same-origin', headers: H })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        document.querySelectorAll('.tw').forEach(function (w) {
          var s = d[w.dataset.g], dot = w.querySelector('.dot');
          if (!dot) return;                       // eigene Lesezeichen: keine Statuspruefung
          dot.className = 'dot ' + (s ? s.state : 'unknown');
          dot.title = s ? LABEL[s.state] + (s.ms != null ? ' · ' + s.ms + ' ms' : '') + (s.code ? ' · HTTP ' + s.code : '') : 'Status wird geprüft';
        });
      }).catch(function () {});
  }
  status();
  setInterval(status, 60000);
  renumber();
})();
