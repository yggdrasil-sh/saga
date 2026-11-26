// Saga 2.6 (K-D, Bugs-DB 2271) - Tageszeile "Kurz gesagt" auf "Heute" unter der Lagezeile (Vertrag: docs/APP-API-KI.md §13,
// Testvektoren tests/today_vectors.json, Format saga-today/v1). Der Server rechnet ohne Modell und liefert fertige Texte;
// hier wird nur gezeigt: Teile als Knoepfe (oeffnen nur, haken nie ab), daneben ✕ ("Gerade nicht" bis morgen 05:00) und ⋯,
// statt der Zeile die Pille "Ruhe bis morgen · Wieder an" bzw. einmal die Frage "Tageszeile ausblenden?", darunter
// "Wartet auf dich". Keine Saetze im Client, Zustaende nur aus Feldern (line/quiet/ask/waiting), alles per textContent,
// ohne aria-live, nie Fokus beim Laden. 404/Fehler/enabled false -> nichts. Live: ai.today.changed {v}, 400 ms entprellt.
//
// Laeuft frueh (direkt hinter den Slots in templates/index.html), damit die Anfrage parallel zum Rest der Seite startet und
// die zuletzt gemessene Hoehe vorab frei bleibt (kein Seitensprung beim Neuladen). Gezeichnet wird, sobald common.js da ist.
(function () {
  'use strict';
  var box = document.getElementById('hz-kurz-box');
  if (!box) return;                                          // nur auf "Heute" und nur mit saga_on
  var HKEY = 'hk-kurz-h', TZ = '';
  try { TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) { TZ = ''; }
  function url(p) { return p + (TZ ? '?tz=' + encodeURIComponent(TZ) : ''); }
  function req(method, path, body) {
    var h = { 'X-Saga': '1' };
    if (body !== undefined) h['Content-Type'] = 'application/json';
    return fetch(url(path), { method: method, credentials: 'same-origin', headers: h, body: body !== undefined ? JSON.stringify(body) : undefined })
      .then(function (r) {
        if (!r.ok) { var e = new Error('HTTP ' + r.status); e.status = r.status; throw e; }
        return r.json();
      });
  }
  function localDay() { var d = new Date(); return d.getFullYear() + '-' + (d.getMonth() + 1) + '-' + d.getDate(); }

  // Platz der letzten Anzeige (nur Hoehe, kein Inhalt) vorab freihalten - gleiche Fensterbreite, gleicher Tag
  try {
    var m = JSON.parse(localStorage.getItem(HKEY) || 'null');
    if (m && m.w === window.innerWidth && m.d === localDay() && m.h > 0 && m.h < 400) { box.style.minHeight = m.h + 'px'; box.hidden = false; }
  } catch (e) { /* ohne Speicher */ }
  var first = req('GET', '/api/ai/today');
  first.catch(function () { /* ausgewertet in start(); hier nur keine unbehandelte Ablehnung */ });

  function start() {
    var T = window.T || function (s) { return s; }, HK = window.HK;
    var kurz = document.getElementById('hz-kurz'), wait = document.getElementById('hz-wait');
    var cur = null, busy = false, infoOpen = false, quietT = 0;
    function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
    function btn(cls, text) { var b = el('button', cls, text); b.type = 'button'; return b; }

    // --- Ziele: oeffnen nur (bestehende Einstiege), unbekannter type -> kein Knopf ------------------------------------
    var TYPES = { task: 1, row: 1, tasks: 1, events: 1, jobs: 1 };
    function railOn() { var r = document.getElementById('rail'); return !!r && !r.hidden && getComputedStyle(r).display !== 'none' && r.getClientRects().length > 0; }
    function flash(n) {
      if (!n) return;
      try { n.scrollIntoView({ block: 'nearest', behavior: 'auto' }); } catch (e) { n.scrollIntoView(); }
      n.classList.remove('hz-flash'); void n.offsetWidth; n.classList.add('hz-flash');
      setTimeout(function () { n.classList.remove('hz-flash'); }, 1800);
    }
    function taskNode(key) {                                 // Eintrag in der Leiste rechts bzw. im Aufgaben-Kasten
      var sel = '[data-key="' + String(key).replace(/["\\]/g, '') + '"]';
      if (railOn()) { var r = document.querySelector('#rail-list ' + sel); if (r) return r; }
      var nt = document.getElementById('nt'), n = document.querySelector('#nt ' + sel);
      if (n && nt) { nt.open = true; return n; }
      return null;
    }
    function focusIn(n) { var f = n && n.querySelector('.task-more, .rail-more'); if (f) try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); } }
    function go(t) {
      if (!t || !TYPES[t.type]) return;
      if (t.type === 'jobs') { (HK && HK.go ? HK.go : function (u) { location.href = u; })('/saga?tab=jobs'); return; }
      if (t.type === 'events') {
        var cal = document.getElementById('rail-cal');
        if (cal && !cal.hidden && railOn()) { flash(cal); return; }
        (HK && HK.go ? HK.go : function (u) { location.href = u; })('/notes?v=events'); return;
      }
      if (t.type === 'task' || t.type === 'row') {
        var n = taskNode((t.type === 'task' ? 't:' : 'r:') + t.id);
        if (n) { flash(n); focusIn(n); return; }
        if (t.type === 'row') { (HK && HK.go ? HK.go : function (u) { location.href = u; })('/notes#' + encodeURIComponent(t.id)); return; }
      }
      // tasks (overdue/today) bzw. Aufgabe nicht in der Liste: Aufgaben zeigen
      if (railOn()) { flash(document.getElementById('rail-list')); return; }
      var nt = document.getElementById('nt'); if (nt) { nt.open = true; flash(nt); }
    }

    // --- Zeichnen ----------------------------------------------------------------------------------------------------
    function keepH() {                                       // Hoehe fuer das naechste Laden merken (nur Zahl)
      try {
        var h = box.hidden ? 0 : Math.round(box.getBoundingClientRect().height);
        if (h) localStorage.setItem(HKEY, JSON.stringify({ w: window.innerWidth, d: localDay(), h: h }));
        else localStorage.removeItem(HKEY);
      } catch (e) { /* egal */ }
    }
    function lineEl(d) {
      var ln = d.line, row = el('div', 'hz-kurz-row');
      row.setAttribute('role', 'group');
      if (ln.a11y) row.setAttribute('aria-label', ln.a11y);
      var lab = el('span', 'hz-kurz-l', d.label || ''); lab.setAttribute('aria-hidden', 'true'); row.appendChild(lab);
      var parts = el('span', 'hz-kurz-parts');
      var list = ln.parts || [];
      list.forEach(function (p, i) {                          // Trenner am Ende des Teils: bricht nie allein an den Zeilenanfang
        var it = el('span', 'hz-kurz-pi'), t = p && p.target, b;
        if (t && TYPES[t.type]) { b = btn('hz-kurz-p', p.text); b.addEventListener('click', function () { go(t); }); }
        else b = el('span', 'hz-kurz-p is-plain', p && p.text);
        b.dataset.kind = (p && p.kind) || '';
        it.appendChild(b);
        if (i < list.length - 1) { var s = el('span', 'hz-kurz-sep', '·'); s.setAttribute('aria-hidden', 'true'); it.appendChild(s); }
        parts.appendChild(it);
      });
      row.appendChild(parts);
      var ctl = el('span', 'hz-kurz-ctl');
      var x = btn('hz-kurz-x', '✕'); x.setAttribute('aria-label', T('Gerade nicht')); x.title = T('Gerade nicht');
      x.addEventListener('click', function () { act('POST', '/api/ai/today/quiet', undefined, 'undo'); });
      var mo = btn('hz-kurz-m', '⋯'); mo.setAttribute('aria-label', T('Mehr zur Tageszeile')); mo.title = T('Mehr zur Tageszeile');
      mo.setAttribute('aria-haspopup', 'menu'); mo.setAttribute('aria-expanded', 'false');
      mo.addEventListener('click', function () { menu(mo); });
      ctl.appendChild(x); ctl.appendChild(mo); row.appendChild(ctl);
      return row;
    }
    function pillEl(q) {
      var row = el('div', 'hz-kurz-row is-quiet'), pill = el('span', 'hz-kurz-pill');
      pill.appendChild(el('span', 'hz-kurz-pt', q.text || ''));
      var s = el('span', 'hz-kurz-sep', '·'); s.setAttribute('aria-hidden', 'true'); pill.appendChild(s);
      var u = btn('hz-kurz-undo', q.undo || ''); u.addEventListener('click', function () { act('DELETE', '/api/ai/today/quiet', undefined, 'x'); });
      pill.appendChild(u); row.appendChild(pill);
      return row;
    }
    function askEl(a) {
      var row = el('div', 'hz-kurz-row is-ask'); row.setAttribute('role', 'group');
      var q = el('span', 'hz-kurz-q', a.text || ''); q.id = 'hz-kurz-q'; row.setAttribute('aria-labelledby', q.id); row.appendChild(q);
      var y = btn('btn hz-kurz-yes', a.yes || ''), n = btn('btn hz-kurz-no', a.no || '');
      y.addEventListener('click', function () { act('PUT', '/api/ai/today', { on: false, asked: true }, 'main'); });
      n.addEventListener('click', function () { act('PUT', '/api/ai/today', { asked: true }, 'undo'); });
      row.appendChild(y); row.appendChild(n);
      return row;
    }
    function infoEl(d) {
      var p = el('p', 'hz-kurz-info'); p.id = 'hz-kurz-info';
      var ic = el('span', 'hz-kurz-ic', 'ⓘ'); ic.setAttribute('aria-hidden', 'true'); p.appendChild(ic);
      p.appendChild(el('span', 'hz-kurz-it', d.info));
      var c = btn('hz-kurz-ix', '✕'); c.setAttribute('aria-label', T('Schließen')); c.title = T('Schließen');
      c.addEventListener('click', function () { infoOpen = false; draw(cur); var m = kurz.querySelector('.hz-kurz-m'); if (m) m.focus(); });
      p.appendChild(c);
      return p;
    }
    function draw(d) {
      var was = box.hidden || box.style.minHeight !== '';
      kurz.textContent = ''; wait.textContent = ''; kurz.style.minHeight = '';
      var on = !!(d && d.enabled !== false);
      if (on && d.line && (d.line.parts || []).length) kurz.appendChild(lineEl(d));
      else if (on && d.ask) kurz.appendChild(askEl(d.ask));
      else if (on && d.quiet) kurz.appendChild(pillEl(d.quiet));
      if (on && infoOpen && d.info && d.line) kurz.appendChild(infoEl(d));
      else infoOpen = false;
      if (on && d.waiting && d.waiting.text) {
        var a = el('a', 'hz-wait-a', d.waiting.text); a.href = '/saga?tab=jobs';
        wait.appendChild(a);
      }
      kurz.hidden = !kurz.childNodes.length; wait.hidden = !wait.childNodes.length;
      var show = !kurz.hidden || !wait.hidden;
      box.style.minHeight = '';
      if (show && was) { box.classList.remove('is-in'); void box.offsetWidth; box.classList.add('is-in'); }   // Einblenden (nur Deckkraft, 150 ms)
      box.hidden = !show;
      keepH();
      clearTimeout(quietT);                                   // Ruhe endet (05:00): dann neu holen
      if (on && d.quiet && d.quiet.until) {
        var ms = Date.parse(d.quiet.until) - Date.now();
        if (ms > 0 && ms < 26 * 3600e3) quietT = setTimeout(load, ms + 1500);
      }
    }
    function set(d) { cur = d || null; draw(cur); }
    function nothing() { cur = null; draw(null); }

    // --- Aktionen ----------------------------------------------------------------------------------------------------
    function refocus(where) {                                // nach eigener Aktion: Fokus nicht ins Leere fallen lassen
      var f = where === 'undo' ? kurz.querySelector('.hz-kurz-undo, .hz-kurz-no') : where === 'x' ? kurz.querySelector('.hz-kurz-x') : null;
      if (!f) f = document.getElementById('heute');
      if (f) try { f.focus({ preventScroll: true }); } catch (e) { f.focus(); }
    }
    function act(method, path, body, where) {
      if (busy) return; busy = true;
      var hadFocus = box.contains(document.activeElement);
      [].forEach.call(kurz.querySelectorAll('button'), function (b) { b.disabled = true; });
      var h0 = kurz.hidden ? 0 : kurz.getBoundingClientRect().height;
      req(method, path, body).then(function (d) {
        set(d);
        if (h0 && !kurz.hidden && !kurz.querySelector('.hz-kurz-row:not(.is-quiet):not(.is-ask)')) kurz.style.minHeight = Math.round(h0) + 'px';   // Pille/Frage: kein Sprung
        if (hadFocus) refocus(where);
      })
        .catch(function (e) {
          [].forEach.call(kurz.querySelectorAll('button'), function (b) { b.disabled = false; });
          if (HK && HK.toast) HK.toast(T('Das hat nicht geklappt – nichts wurde geändert.'));
          if (e && (e.status === 404 || e.status === 403)) nothing();
        }).then(function () { busy = false; });
    }
    function menu(anchor) {
      if (!HK || !HK.openMenu) return;
      HK.openMenu(anchor, function (m) {
        m.appendChild(HK.menuItem(T('Tageszeile ausblenden'), function () { act('PUT', '/api/ai/today', { on: false }, 'main'); }));
        if (cur && cur.info) m.appendChild(HK.menuItem(T('Warum sehe ich das?'), function () {
          infoOpen = true; draw(cur); var c = document.querySelector('#hz-kurz-info .hz-kurz-ix'); if (c) c.focus();
        }));
      });
    }

    // --- Laden, Live, Rueckkehr --------------------------------------------------------------------------------------
    var gen = 0;
    function take(p) {
      var g = ++gen;
      return p.then(function (d) { if (g === gen && !busy) { if (!cur || !d || d.v !== cur.v || d.enabled === false) set(d); } })
        .catch(function () { if (g === gen) nothing(); });     // 404 (alter Server), 403, Netz: still nichts
    }
    function load() { return take(req('GET', '/api/ai/today')); }
    take(first);
    var lt = 0;
    function soon() { clearTimeout(lt); lt = setTimeout(load, 400); }
    document.addEventListener('hk-live', function (e) {
      var t = e && e.detail && e.detail.type;
      if (t === 'ai.today.changed') {
        var v = e.detail.data && e.detail.data.v;
        if (!cur || !v || v !== cur.v) soon();
      } else if (t === 'resync' || (t === 'live.hello' && !(e.detail.data && e.detail.data.first))) soon();   // nach Wiederverbinden: Ereignisse dazwischen fehlen
    });
    var lastDay = localDay();
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && (lastDay !== localDay() || cur)) { lastDay = localDay(); soon(); }
    });
    setInterval(function () { if (lastDay !== localDay()) { lastDay = localDay(); load(); } }, 60000);   // Tageswechsel
    var rw = 0; window.addEventListener('resize', function () { clearTimeout(rw); rw = setTimeout(keepH, 300); });
    window.HKKurz = { reload: load, show: set };              // Einstellungen › Saga: Schalter zeigt die Antwort sofort
  }
  if (window.T && window.HK) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
