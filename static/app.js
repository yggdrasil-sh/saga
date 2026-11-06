// Saga - "Heute" (/dashboard, Design-Runde 05): Begruessung, Lagezeile, Schnellaktionen, "Weitermachen" (zuletzt
// geoeffnete Seiten), kompakte Lesezeichen. Aufgaben/Termine rechts fuellen static/notes.js und static/calendar.js;
// beide melden ihre Zahlen fuer die Lagezeile per Ereignis "hk-heute". Alles per textContent.
(function () {
  'use strict';
  var HK = window.HK, H = { 'X-Saga': '1' };
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function store(k) { try { return HK ? HK.store(k) : localStorage.getItem(k); } catch (e) { return null; } }
  function wraps() { return [].slice.call(document.querySelectorAll('.tw')).filter(function (w) { return !w.hidden && !w.closest('section').hidden; }); }
  function open(w) { if (w) window.open(w.querySelector('a.tile').href, '_blank', 'noopener'); }
  function renumber() {
    document.querySelectorAll('.num').forEach(function (n) { n.textContent = ''; });
    wraps().slice(0, 9).forEach(function (w, i) { w.querySelector('.num').textContent = i + 1; });
  }
  function typing(t) { return t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable); }

  // --- Begruessung (Uhr des Browsers) und Datum -------------------------------------------------------------------
  var greet = $('hz-greet'), fD = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'long', day: 'numeric', month: 'long' });
  function tick() {
    var d = new Date(), h = d.getHours(), n = greet ? greet.dataset.first : '';
    if (greet) greet.textContent = h >= 5 && h < 11 ? T('Guten Morgen, {name}', { name: n })
      : h >= 11 && h < 18 ? T('Guten Tag, {name}', { name: n }) : T('Guten Abend, {name}', { name: n });
    if ($('hz-date')) $('hz-date').textContent = fD.format(d);
  }
  tick();
  setInterval(tick, 60000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });

  // --- Lagezeile: "2 Termine heute · 6 Aufgaben überfällig" (nur Teile > 0) ---------------------------------------
  var lage = window.HKHeute = window.HKHeute || {};
  function drawLage() {
    var box = $('hz-parts'); if (!box) return;
    var parts = [];
    if (lage.events) parts.push([T('{n} {n:Termin|Termine} heute', { n: lage.events }), '']);
    if (lage.over) parts.push([T('{n} {n:Aufgabe|Aufgaben} überfällig', { n: lage.over }), ' alert']);
    if (lage.today) parts.push([T('{n} heute fällig', { n: lage.today }), '']);
    box.textContent = '';
    if (lage.tasks == null && lage.events == null) return;          // noch nichts geladen: nur das Datum
    if (!parts.length) parts.push([T('Nichts Dringendes heute'), ' calm']);
    parts.forEach(function (p) {
      box.appendChild(el('span', 'hz-sep', ' · '));
      box.appendChild(el('span', 'hz-part' + p[1], p[0]));
    });
  }
  document.addEventListener('hk-heute', drawLage);
  drawLage();

  // --- Schnellaktionen ----------------------------------------------------------------------------------------------
  function railVisible() { var r = $('rail'); return !!r && getComputedStyle(r).display !== 'none'; }
  function newTask() {
    if (railVisible()) { $('rail-text').focus(); return; }
    var nt = $('nt'); if (nt) { nt.open = true; nt.scrollIntoView({ block: 'nearest' }); $('task-text').focus(); }
  }
  function newEvent() {
    var box = $('rail-cal'), add = $('rail-cal-add');
    if (box && !box.hidden && add) add.click();                     // Kalender verbunden: Dialog wie "+" bei den Terminen
    else location.href = '/notes?v=events';
  }
  function askAI() { if (window.HKAI && HKAI.state()) HKAI.open('ask'); }
  if ($('hz-task')) $('hz-task').addEventListener('click', newTask);
  if ($('hz-event')) $('hz-event').addEventListener('click', newEvent);
  if ($('hz-ai')) $('hz-ai').addEventListener('click', askAI);
  // KI-Assistent (static/ai.js) an/aus: Knopf "Frage Marin" nur, wenn er an ist
  function aiBtn() {
    var b = $('hz-ai'); if (!b) return;
    var st = window.HKAI && HKAI.state && HKAI.state();
    b.hidden = !st;
    if (st) $('hz-ai-t').textContent = T('Frage {name}', { name: (st.persona && st.persona.name) || T('Saga KI') });
  }
  document.addEventListener('hk-ai', aiBtn);
  aiBtn();

  // --- Weitermachen: zuletzt geoeffnete Seiten (wie bisher "Zuletzt" in der Seitenleiste: localStorage hk-pg-recent,
  //     gepflegt von static/pages.js, abgeglichen mit /api/notes) ---------------------------------------------------
  function isImg(i) { return typeof i === 'string' && (i.indexOf('/files/') === 0 || i.indexOf('/api/fed/files/') === 0); }
  function recent() {
    var box = $('hz-cards'); if (!box || !HK) return;
    var ids = [];
    try { ids = (JSON.parse(store('hk-pg-recent') || '[]') || []).filter(function (x) { return typeof x === 'number'; }); } catch (e) { ids = []; }
    function empty(on) { $('hz-recent-empty').hidden = !on; box.hidden = on; }
    if (!ids.length) { empty(true); return; }
    HK.api('GET', '/api/notes').then(function (d) {
      var pages = Array.isArray(d) ? d : ((d && d.notes) || []), by = {};
      pages.forEach(function (p) { by[p.id] = p; });
      var list = ids.map(function (id) { return by[id]; }).filter(function (p) { return p && !p.archived; }).slice(0, 6);
      box.textContent = '';
      list.forEach(function (p) {
        var a = el('a', 'hz-card'); a.href = '/notes#' + p.id;
        var ic = el('span', 'hz-card-ic');
        if (isImg(p.icon)) { var im = el('img'); im.alt = ''; im.decoding = 'async'; im.src = /^\/files\/[0-9a-f]{24}$/.test(p.icon) ? p.icon + '?w=160' : p.icon; ic.appendChild(im); }
        else ic.textContent = p.icon || (p.kind === 'database' ? '▦' : '📄');
        ic.setAttribute('aria-hidden', 'true');
        a.appendChild(ic);
        a.appendChild(el('span', 'hz-card-t', p.title || T('Unbenannt')));
        if (p.updated) a.appendChild(el('span', 'hz-card-s', HK.relTime(p.updated - Date.now() / 1000)));
        box.appendChild(a);
      });
      empty(!list.length);
    }).catch(function () { empty(true); });
  }
  recent();
  var rt = 0; document.addEventListener('hk-live', function (e) {
    var t = e && e.detail && e.detail.type; if (t && /^notes?\./.test(t)) { clearTimeout(rt); rt = setTimeout(recent, 400); }
  });
  window.addEventListener('pageshow', function (e) { if (e.persisted) recent(); });      // zurueck aus /notes (bfcache)

  // --- Icon-Fallback: Lesezeichen erst ueber den Server (icon_url), dann direkt von der Seite (data-d = icon_direct,
  //     CSP img-src erlaubt genau diese Origins), zuletzt die Initiale -----------------------------------------------
  document.querySelectorAll('.ic img').forEach(function (img) {
    var rest = (img.dataset.d || '').split(' ').filter(function (u) { return u && u !== img.getAttribute('src'); });
    function fail() {
      if (rest.length) { img.src = rest.shift(); return; }
      var p = img.parentNode; p.classList.remove('has-img'); p.textContent = img.dataset.l;
    }
    img.addEventListener('error', fail);
    if (img.complete && !img.naturalWidth && img.getAttribute('src')) fail();     // Fehler schon vor diesem Skript
  });

  // --- Tastenkuerzel ------------------------------------------------------------------------------------------------
  // Strg/⌘+K: Schnellsuche (auf /notes); "/": Suche der Seitenleiste (schmal: Schnellsuche). 1-9 oeffnet Lesezeichen,
  // N neue Seite, V Vorlage, E Termin, M KI-Assistent (T = Aufgabe: static/notes.js).
  function sideSearch() {
    var s = $('sh-search');
    if (s && s.getClientRects().length) { s.focus(); s.select(); } else location.href = '/notes?qs=1';
  }
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && (e.key === 'k' || e.key === 'K')) {
      if (document.querySelector('[aria-modal="true"]:not([hidden]), dialog[open]')) return;
      e.preventDefault(); location.href = '/notes?qs=1'; return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey || typing(t)) return;
    if (HK && HK.menuOpen && HK.menuOpen()) return;
    var k = e.key;
    if (k === '/') { e.preventDefault(); sideSearch(); return; }
    if (/^[1-9]$/.test(k)) { e.preventDefault(); open(wraps()[+k - 1]); return; }
    if (k === 'n' || k === 'N') { e.preventDefault(); location.href = '/notes?new=1'; return; }
    if (k === 'v' || k === 'V') { e.preventDefault(); location.href = '/notes?gallery=1'; return; }
    if (k === 'e' || k === 'E') { e.preventDefault(); newEvent(); return; }
    if ((k === 'm' || k === 'M') && !$('hz-ai').hidden) { e.preventDefault(); askAI(); }
  });
  // Tastatur in den Lesezeichen: Ziffern sichtbar, solange der Fokus dort ist (sonst nur beim Ueberfahren)
  var cats = document.querySelector('.cats');
  if (cats) {
    cats.addEventListener('focusin', function () { cats.classList.add('kbd'); });
    cats.addEventListener('focusout', function (e) { if (!cats.contains(e.relatedTarget)) cats.classList.remove('kbd'); });
  }

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

  // --- Eigene Lesezeichen (Dialog aus settings.js) ---------------------------
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
  if (document.querySelector('.dot')) { status(); setInterval(status, 60000); }
  renumber();
})();
