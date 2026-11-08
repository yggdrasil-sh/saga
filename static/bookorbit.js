/* Saga - BookOrbit (2.5, Phase 1): gemeinsame Bausteine fuer das Widget "Lesen" (static/pages.js) und die Karte
   "Lesen" unter "Heute" (static/app.js). Cover kommen immer ueber den Saga-Server (/api/bookorbit/cover/<id>, mit
   X-Saga als Blob) - der Browser spricht nie selbst mit BookOrbit.
   HK.bookorbit.cover(url) -> Promise<objectURL|null>, HK.bookorbit.book(b, opts) -> Element, .goal(g), .streak(s),
   .hint(state) -> Hinweis mit Knopf zu Einstellungen -> BookOrbit. */
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
  window.HK.bookorbit = { cover: cover, book: book, goal: goal, streak: streak, hint: hint };
})();
