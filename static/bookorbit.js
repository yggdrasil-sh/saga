/* Saga - BookOrbit (2.5, Phase 1): gemeinsame Bausteine fuer das Widget "Lesen" (static/pages.js) und die Karte
   "Lesen" unter "Heute" (static/app.js). Cover kommen immer ueber den Saga-Server (/api/bookorbit/cover/<id>, mit
   X-Saga als Blob) - der Browser spricht nie selbst mit BookOrbit.
   HK.bookorbit.cover(url) -> Promise<objectURL|null>, HK.bookorbit.book(b, opts) -> Element, .goal(g), .streak(s),
   .hint(state) -> Hinweis mit Knopf zu Einstellungen -> BookOrbit.
   Phase 2 (Faehigkeit bookorbit2): .toPage(bookId | {page_id}) -> Markierungen als Seite anlegen/aktualisieren (Meldung,
   danach die Seite oeffnen), .notesList(box) -> Liste "Markierungen als Seite übernehmen" (Einstellungen),
   .bookMenu(anchor, b) -> Menue an einem Buch (Widget/Heute), .isPage(note) -> Seite stammt aus BookOrbit. */
(function () {
  'use strict';
  window.HK = window.HK || {};
  var T = window.T || function (s) { return s; };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var urls = {};
  function cover(url) {
    if (!url || !/^\/api\/bookorbit\/cover\/\d+$/.test(url)) return Promise.resolve(null);
    if (!urls[url]) {
      urls[url] = fetch(url, { credentials: 'same-origin', headers: { 'X-Saga': '1' } })
        .then(function (r) { return r.ok ? r.blob() : null; })
        .then(function (b) { return b ? URL.createObjectURL(b) : null; })
        .catch(function () { delete urls[url]; return null; });
    }
    return urls[url];
  }
  function pct(v) { return Math.round(typeof v === 'number' && isFinite(v) ? Math.max(0, Math.min(100, v)) : 0); }
  // Ein Buch: Cover (oder Platzhalter), Titel, Autor:innen, Fortschrittsbalken mit Prozent
  function book(b, opts) {
    opts = opts || {};
    var row = el('div', 'bo-book' + (opts.compact ? ' bo-compact' : '')), cv = el('span', 'bo-cover');
    cv.setAttribute('aria-hidden', 'true'); cv.textContent = '📖';
    if (b.cover) cover(b.cover).then(function (u) { if (!u) return; var im = el('img'); im.alt = ''; im.src = u; cv.textContent = ''; cv.appendChild(im); });
    row.appendChild(cv);
    var tx = el('span', 'bo-tx');
    tx.appendChild(el('span', 'bo-ti', b.title || T('Unbenannt')));
    if (b.authors && b.authors.length) tx.appendChild(el('span', 'bo-au', b.authors.slice(0, 2).join(', ')));
    var p = pct(b.progress), bar = el('span', 'bo-bar'), fill = el('span', 'bo-fill');
    fill.style.width = p + '%'; bar.appendChild(fill);
    bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', String(p)); bar.setAttribute('aria-label', T('Fortschritt'));
    var pr = el('span', 'bo-pr'); pr.appendChild(bar); pr.appendChild(el('span', 'bo-pc', p + ' %'));
    tx.appendChild(pr); row.appendChild(tx);
    if (opts.menu && b.id && HK.openMenu) {             // Phase 2: Buch antippen -> "Markierungen als Seite"
      row.classList.add('bo-tap'); row.tabIndex = 0; row.setAttribute('role', 'button'); row.setAttribute('aria-haspopup', 'menu');
      row.setAttribute('aria-label', (b.title || T('Unbenannt')) + ' – ' + T('Markierungen als Seite'));
      var pick = function (e) { e.stopPropagation(); e.preventDefault(); bookMenu(row, b); };
      row.addEventListener('click', pick);
      row.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') pick(e); });
    }
    return row;
  }
  function goal(g, box) {
    if (!g) { box.appendChild(el('div', 'wg-note', T('Kein Leseziel verfügbar'))); return; }
    if (g.goal) {
      box.appendChild(el('div', 'wg-v', g.done + ' / ' + g.goal));
      var bar = el('div', 'wg-bar'), f = el('span', 'wg-fill'), p = Math.min(100, g.done / g.goal * 100);
      f.style.width = p.toFixed(1) + '%'; bar.appendChild(f);
      bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', String(g.goal)); bar.setAttribute('aria-valuenow', String(g.done));
      box.appendChild(bar);
      box.appendChild(el('div', 'wg-sub', T('Leseziel {year}: {n} von {goal} Büchern', { year: g.year || '', n: g.done, goal: g.goal })));
    } else {
      box.appendChild(el('div', 'wg-v', String(g.done)));
      box.appendChild(el('div', 'wg-sub', T('{n} {n:Buch|Bücher} gelesen ({year}) – kein Leseziel in BookOrbit gesetzt', { n: g.done, year: g.year || '' })));
    }
  }
  function streak(s, box) {
    if (!s) { box.appendChild(el('div', 'wg-note', T('Keine Lesesträhne verfügbar'))); return; }
    box.appendChild(el('div', 'wg-v', T('{n} {n:Tag|Tage}', { n: s.current })));
    var dots = el('div', 'bo-days'); dots.setAttribute('role', 'img');
    dots.setAttribute('aria-label', T('Letzte 7 Tage: an {n} gelesen', { n: (s.last7 || []).filter(Boolean).length }));
    (s.last7 || []).forEach(function (on) { dots.appendChild(el('i', 'bo-day' + (on ? ' bo-on' : ''))); });
    box.appendChild(dots);
    box.appendChild(el('div', 'wg-sub', T('In Folge gelesen · längste Strähne: {n} {n:Tag|Tage}', { n: s.longest })));
  }
  function hint(state) {
    var box = el('div', 'wg-note bo-hint');
    box.appendChild(el('span', null, state === 'expired' ? T('BookOrbit-Anmeldung abgelaufen – bitte neu anmelden.')
      : state === 'unavailable' ? T('BookOrbit gerade nicht erreichbar.') : T('Noch nicht mit BookOrbit verbunden.')));
    if (state !== 'unavailable') {
      var b = el('button', 'linkbtn', T('Einstellungen öffnen')); b.type = 'button';
      b.addEventListener('click', function (e) { e.stopPropagation(); if (window.HKSettings) window.HKSettings.open('bookorbit'); });
      box.appendChild(document.createTextNode(' ')); box.appendChild(b);
    }
    return box;
  }
  // --- Phase 2: Markierungen als Seite (nur lesen: BookOrbit -> Saga) ----------------------------------------------
  function api(m, url, b) {
    if (HK.api) return HK.api(m, url, b);
    return fetch(url, { method: m, credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); });
  }
  function say(msg) { if (HK.toast) HK.toast(msg); }
  function openNote(id) {
    if (window.HKSettings && window.HKSettings.close) window.HKSettings.close();
    if (location.pathname === '/notes') { if (location.hash === '#' + id && HK.reloadPage) HK.reloadPage(); else location.hash = '#' + id; }
    else if (HK.go) HK.go('/notes#' + id); else location.href = '/notes#' + id;
  }
  function resultText(r) {
    var parts = [];
    if (r.created) parts.push(T('Seite angelegt: {n} {n:Markierung|Markierungen}', { n: r.added }));
    else if (r.added || r.updated) parts.push(T('{n} neu, {m} aktualisiert', { n: r.added, m: r.updated }));
    else parts.push(T('Keine neuen Markierungen'));
    if (r.truncated) parts.push(T('nicht alle übernommen (Obergrenze erreicht)'));
    return parts.join(' – ');
  }
  // bookId (Zahl) oder {page_id: n}; opts.open === false: Seite nicht oeffnen. -> Promise<Antwort>
  var busy = false;
  function toPage(what, opts) {
    opts = opts || {};
    if (busy) return Promise.resolve(null);
    busy = true;
    var body = typeof what === 'number' ? { book_id: what } : what;
    say(T('Hole Markierungen aus BookOrbit …'));
    return api('POST', '/api/bookorbit/notes', body).then(function (r) {
      busy = false; say(resultText(r));
      if (opts.open !== false) openNote(r.page_id);
      return r;
    }, function (e) { busy = false; say(T('BookOrbit: {err}', { err: e.message })); throw e; });
  }
  // Liste in den Einstellungen: Suche (entprellt, auch Buecher ohne Markierungen) und Filter "Gelesen" (2.5, 9.11.)
  function notesList(box) {
    box.textContent = '';
    var bar = el('div', 'bo-nl-bar');
    var q = el('input', 'hk-in bo-nl-q'); q.type = 'search'; q.id = 'hk-bo-q'; q.placeholder = T('Buch suchen …');
    q.setAttribute('aria-label', T('Buch in BookOrbit suchen')); q.autocomplete = 'off'; q.maxLength = 200;
    var chips = el('div', 'chips bo-nl-f'); chips.setAttribute('role', 'group'); chips.setAttribute('aria-label', T('Anzeigen'));
    var mode = '';
    [['', T('Mit Markierungen')], ['read', T('Gelesen##BookOrbit-Filter')]].forEach(function (f) {
      var c = el('button', 'chip chip-btn' + (f[0] === mode ? ' on' : ''), f[1]); c.type = 'button'; c.dataset.f = f[0];
      c.setAttribute('aria-pressed', f[0] === mode ? 'true' : 'false');
      c.addEventListener('click', function () {
        if (mode === f[0]) return;
        mode = f[0];
        Array.prototype.forEach.call(chips.children, function (x) { var on = x.dataset.f === mode; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on ? 'true' : 'false'); });
        load();
      });
      chips.appendChild(c);
    });
    bar.appendChild(q); bar.appendChild(chips); box.appendChild(bar);
    var out = el('div', 'bo-nl-out'); out.setAttribute('aria-live', 'polite'); box.appendChild(out);
    var seq = 0, tm = null;
    q.addEventListener('input', function () { clearTimeout(tm); tm = setTimeout(load, 350); });
    q.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(tm); load(); } });
    function sub(b) {
      var p = [];
      if (b.author) p.push(b.author);
      if (typeof b.count === 'number') p.push(b.count ? T('{n} {n:Markierung|Markierungen}', { n: b.count }) : T('keine Markierungen'));
      if (b.read_at) { var d = new Date(b.read_at); if (!isNaN(d)) p.push(T('gelesen {date}', { date: d.toLocaleDateString(document.documentElement.lang || undefined) })); }
      return p.join(' · ');
    }
    function load() {
      var my = ++seq, term = q.value.trim();
      out.textContent = ''; out.appendChild(el('p', 'hk-hint', T('Lade …')));
      var url = '/api/bookorbit/notes/books?' + (term ? 'q=' + encodeURIComponent(term) + '&' : '') + (mode ? 'filter=' + mode : '');
      api('GET', url.replace(/[?&]$/, '')).then(function (d) {
        if (my !== seq) return;
        out.textContent = '';
        var books = (d && d.books) || [];
        if (!books.length) {
          out.appendChild(el('p', 'hk-hint', mode === 'read' ? (term ? T('Kein gelesenes Buch gefunden.') : T('In BookOrbit ist noch kein Buch als gelesen markiert.'))
            : term ? T('Kein Buch gefunden.') : T('Keine Bücher mit Markierungen gefunden.')));
          return;
        }
        var ul = el('ul', 'bo-nl'); ul.setAttribute('aria-label', mode === 'read' ? T('Gelesene Bücher') : T('Bücher mit Markierungen'));
        books.forEach(function (b) {
          var li = el('li', 'bo-nl-row'), cv = el('span', 'bo-cover'); cv.setAttribute('aria-hidden', 'true'); cv.textContent = '📖';
          cover(b.cover).then(function (u) { if (!u) return; var im = el('img'); im.alt = ''; im.src = u; cv.textContent = ''; cv.appendChild(im); });
          li.appendChild(cv);
          var tx = el('span', 'bo-tx');
          tx.appendChild(el('span', 'bo-ti', b.title || T('Unbenannt')));
          var s2 = sub(b); if (s2) tx.appendChild(el('span', 'bo-au', s2));
          li.appendChild(tx);
          var nm = ': ' + (b.title || '');
          if (b.page_id) {
            var op = el('button', 'btn btn-primary bo-nl-open', T('Öffnen')); op.type = 'button'; op.setAttribute('aria-label', T('Öffnen') + nm);
            op.addEventListener('click', function () { openNote(b.page_id); });
            li.appendChild(op);
          }
          var go = el('button', 'btn bo-nl-go', b.page_id ? T('Aktualisieren') : T('Als Seite')); go.type = 'button';
          go.setAttribute('aria-label', (b.page_id ? T('Aktualisieren') : T('Als Seite')) + nm);
          go.addEventListener('click', function () {
            go.disabled = true;
            toPage(b.id).then(function () { go.disabled = false; }, function () { go.disabled = false; });
          });
          li.appendChild(go);
          ul.appendChild(li);
        });
        out.appendChild(ul);
      }).catch(function (e) { if (my !== seq) return; out.textContent = ''; out.appendChild(el('p', 'hk-hint hk-warn', T('Nicht geladen: {err}', { err: e.message }))); });
    }
    load();
  }
  function bookMenu(anchor, b) {
    if (!HK.openMenu || !HK.menuItem || !b || !b.id) return;
    HK.openMenu(anchor, function (m) {
      m.appendChild(HK.menuItem(T('Markierungen als Seite'), function () { toPage(b.id); }, { ic: 'file' }));
    });
  }
  // Seite aus BookOrbit? Buchseite (data.bo.x = "info") oder Kapitelseite (data.bo.x = "back"), siehe saga/bookorbit_pages.py
  function isPage(n) { return !!(n && n.format === 'blocks' && /"bo":\s*\{[^{}]*"x":\s*"(info|back)"/.test(n.body || '')); }
  window.HK.bookorbit = { cover: cover, book: book, goal: goal, streak: streak, hint: hint, toPage: toPage, notesList: notesList,
                          bookMenu: bookMenu, isPage: isPage };
})();
