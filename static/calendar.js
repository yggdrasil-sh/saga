/* Externe Kalender (CalDAV) in der Oberflaeche: Monatsansicht + Termin-Dialog.
   Gemeinsam fuer den Kalender-Block (Seiten), das Dashboard und die Datenbank-Kalender. */
(function () {
  'use strict';
  var el = HK.el, api = HK.api;
  function ymd(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }  // lokal, nicht UTC
  function addDays(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }
  function parseDay(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  var fTime = new Intl.DateTimeFormat(window.SAGA_LOCALE, { hour: '2-digit', minute: '2-digit' });
  var fMonth = new Intl.DateTimeFormat(window.SAGA_LOCALE, { month: 'long', year: 'numeric' });
  var fDay = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });

  function load(start, end, fresh) { return api('GET', '/api/calendar/events?start=' + ymd(start) + '&end=' + ymd(end) + (fresh ? '&fresh=1' : '')); }
  function accounts() { return api('GET', '/api/settings/calendars').then(function (d) { return d.accounts || []; }); }
  // Termine je Tag: ganztaegige ueber alle Tage (Ende exklusiv), zeitgebundene am Starttag
  function byDay(events) {
    var m = {};
    events.forEach(function (e) {
      if (e.allday) {
        for (var d = parseDay(e.start), end = parseDay(e.end); d < end; d = addDays(d, 1)) (m[ymd(d)] = m[ymd(d)] || []).push(e);
      } else (m[ymd(new Date(e.start))] = m[ymd(new Date(e.start))] || []).push(e);
    });
    Object.keys(m).forEach(function (k) { m[k].sort(function (a, b) { return (a.allday ? 0 : 1) - (b.allday ? 0 : 1) || (a.start < b.start ? -1 : 1); }); });
    return m;
  }
  function label(e) { return (e.allday ? '' : fTime.format(new Date(e.start)) + ' ') + (e.summary || T('(ohne Titel)')); }
  function chip(e, onClick) {
    var b = el('button', 'cal-item cal-ext' + (e.allday ? ' allday' : '')); b.type = 'button';
    b.style.setProperty('--cc', e.color || '#4184e4');
    b.title = label(e) + (e.location ? ' · ' + e.location : '') + ' (' + e.calendar + ')';
    b.appendChild(el('span', 'cal-it', label(e)));
    b.addEventListener('click', function (ev) { ev.stopPropagation(); onClick(e); });
    return b;
  }
  function localInput(iso) { var d = new Date(iso); return ymd(d) + 'T' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0'); }

  // --- Termin-Dialog (neu oder bearbeiten) ---------------------------------------------
  function eventDialog(ev, defaults, onDone) {
    accounts().then(function (accs) {
      var targets = [];
      accs.forEach(function (a) { a.calendars.forEach(function (c) { if (c.show && c.writable !== false) targets.push({ account: a.id, cal: c.href, name: (a.label || a.username) + ' · ' + c.name }); }); });
      if (!ev && !targets.length) { HK.toast(T('Kein beschreibbarer Kalender verbunden – unter Einstellungen → Kalender.')); return; }
      var ro = !!(ev && (ev.series || ev.writable === false));
      var dlg = el('div', 'hk-modal'), card = el('div', 'hk-dialog hk-dialog-s'), head = el('div', 'hk-dlg-head'), body = el('div', 'hk-dlg-body');
      dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true');
      head.appendChild(el('h2', null, ev ? (ro ? T('Termin') : T('Termin bearbeiten')) : T('Neuer Termin')));
      var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); head.appendChild(x);
      card.appendChild(head); card.appendChild(body); dlg.appendChild(card);
      function close() { dlg.remove(); document.removeEventListener('keydown', key); }
      function key(e) { if (e.key === 'Escape' && !HK.menuOpen()) { e.preventDefault(); close(); } }
      x.addEventListener('click', close); dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) close(); });
      document.addEventListener('keydown', key);
      function fld(lbl, inp) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, lbl)); l.appendChild(inp); body.appendChild(l); return inp; }
      function inp(type, v) { var i = el('input', 'hk-in'); i.type = type; i.value = v || ''; i.disabled = ro; return i; }
      var allday = !!(ev ? ev.allday : defaults.allday);
      var t = fld(T('Titel'), inp('text', ev ? ev.summary : '')); t.maxLength = 300; t.placeholder = T('Was steht an?');
      var ad = el('input'); ad.type = 'checkbox'; ad.checked = allday; ad.disabled = ro; fld(T('Ganztägig'), ad);
      var s = fld(T('Beginn'), inp('datetime-local', '')), e2 = fld(T('Ende'), inp('datetime-local', ''));
      function setMode(keep) {
        var sv = s.value, ev2 = e2.value;
        s.type = e2.type = ad.checked ? 'date' : 'datetime-local';
        if (keep && sv) { s.value = ad.checked ? sv.slice(0, 10) : sv.slice(0, 10) + 'T09:00'; e2.value = ad.checked ? (ev2 || sv).slice(0, 10) : (ev2 || sv).slice(0, 10) + 'T10:00'; }
      }
      setMode(false);
      if (ev) {
        if (ev.allday) { s.value = ev.start; e2.value = ymd(addDays(parseDay(ev.end), -1)); }   // Ende inklusive anzeigen
        else { s.value = localInput(ev.start); e2.value = localInput(ev.end); }
      } else {
        var d0 = defaults.date || ymd(new Date());
        if (allday) { s.value = d0; e2.value = d0; } else { s.value = d0 + 'T09:00'; e2.value = d0 + 'T10:00'; }
      }
      ad.addEventListener('change', function () { setMode(true); });
      s.addEventListener('change', function () { if (e2.value < s.value) e2.value = ad.checked ? s.value : s.value.slice(0, 11) + String(Math.min(23, +s.value.slice(11, 13) + 1)).padStart(2, '0') + s.value.slice(13); });
      var calSel = el('select', 'hk-in');
      if (ev) { var o0 = el('option', null, ev.calendar); calSel.appendChild(o0); calSel.disabled = true; }
      else targets.forEach(function (tg, i) { var o = el('option', null, tg.name); o.value = String(i); calSel.appendChild(o); });
      fld(T('Kalender'), calSel);
      var loc = fld(T('Ort'), inp('text', ev ? ev.location : '')); loc.maxLength = 300;
      var desc = el('textarea', 'hk-in hk-ta'); desc.value = ev ? ev.description : ''; desc.disabled = ro; desc.rows = 3; fld(T('Beschreibung'), desc);
      if (ro) body.appendChild(el('p', 'hk-hint', ev.series ? T('🔁 Serientermin – bitte direkt im Kalender (mailcow/Nextcloud) bearbeiten, damit die Serie erhalten bleibt.') : T('Dieser Kalender ist nur lesbar.')));
      var st = el('div', 'hk-status'), bar = el('div', 'hk-actions'); body.appendChild(bar); body.appendChild(st);
      if (!ro) {
        var save = el('button', 'btn', ev ? T('Speichern') : T('Anlegen')); save.type = 'button'; bar.appendChild(save);
        save.addEventListener('click', function () {
          if (!s.value) { st.textContent = T('Bitte einen Beginn angeben.'); st.className = 'hk-status err'; return; }
          var payload = { summary: t.value.trim() || T('Termin'), allday: ad.checked, location: loc.value.trim(), description: desc.value.trim() };
          if (ad.checked) { payload.start = s.value.slice(0, 10); payload.end = ymd(addDays(parseDay((e2.value || s.value).slice(0, 10)), 1)); }
          else { payload.start = new Date(s.value).toISOString(); payload.end = new Date(e2.value || s.value).toISOString(); }
          var tg = ev ? { account: ev.account, cal: ev.cal } : targets[+calSel.value];
          Object.assign(payload, tg);
          if (ev) { payload.href = ev.href; payload.etag = ev.etag; payload.uid = ev.uid; }
          save.disabled = true; st.textContent = T('Speichere …'); st.className = 'hk-status';
          api(ev ? 'PATCH' : 'POST', '/api/calendar/events', payload).then(function () { close(); HK.toast(ev ? T('Termin gespeichert') : T('Termin angelegt')); onDone && onDone(); })
            .catch(function (err) { save.disabled = false; st.textContent = err.message; st.className = 'hk-status err'; });
        });
        if (ev) {
          var del = el('button', 'btn btn-danger', T('Löschen')); del.type = 'button'; bar.appendChild(del);
          del.addEventListener('click', function () {
            if (!confirm(T('Termin „{name}“ löschen?', { name: ev.summary || '' }))) return;
            api('POST', '/api/calendar/events/delete', { account: ev.account, cal: ev.cal, href: ev.href, etag: ev.etag })
              .then(function () { close(); HK.toast(T('Termin gelöscht')); onDone && onDone(); })
              .catch(function (err) { st.textContent = err.message; st.className = 'hk-status err'; });
          });
        }
      }
      var cancel = el('button', 'btn', ro ? T('Schließen') : T('Abbrechen')); cancel.type = 'button'; cancel.addEventListener('click', close); bar.appendChild(cancel);
      document.body.appendChild(dlg); (ro ? cancel : t).focus();
    }).catch(function (err) { HK.toast(T('Kalender nicht geladen: {err}', { err: err.message })); });
  }

  // --- Monatsansicht (Kalender-Block) ---------------------------------------------------
  function month(container) {
    var now = new Date(), m0 = new Date(now.getFullYear(), now.getMonth(), 1);
    function render(fresh) {
      container.textContent = '';
      var head = el('div', 'cal-head');
      function nav(txt, aria, fn) { var b = el('button', 'icon-btn cal-nav', txt); b.type = 'button'; b.setAttribute('aria-label', aria); b.addEventListener('click', fn); return b; }
      head.appendChild(nav('‹', T('Voriger Monat'), function () { m0 = new Date(m0.getFullYear(), m0.getMonth() - 1, 1); render(); }));
      head.appendChild(el('span', 'cal-title', fMonth.format(m0)));
      head.appendChild(nav('›', T('Nächster Monat'), function () { m0 = new Date(m0.getFullYear(), m0.getMonth() + 1, 1); render(); }));
      var tb = el('button', 'db-chip', T('Heute')); tb.type = 'button'; tb.addEventListener('click', function () { m0 = new Date(now.getFullYear(), now.getMonth(), 1); render(); }); head.appendChild(tb);
      var rb = el('button', 'db-chip', '⟳'); rb.type = 'button'; rb.title = T('Aktualisieren (Änderungen aus mailcow/Nextcloud sofort holen)'); rb.setAttribute('aria-label', T('Aktualisieren'));
      rb.addEventListener('click', function () { render(true); }); head.appendChild(rb);
      var nb = el('button', 'db-chip cal-new', T('+ Termin')); nb.type = 'button'; nb.addEventListener('click', function () { eventDialog(null, { date: ymd(now) }, function () { render(true); }); });
      head.appendChild(nb);
      var info = el('span', 'cal-src', T('lädt …')); head.appendChild(info);
      container.appendChild(head);
      var grid = el('div', 'cal-grid'); container.appendChild(grid);
      window.HK.weekdaysShort().forEach(function (w) { grid.appendChild(el('div', 'cal-wd', w)); });
      var start = new Date(m0.getFullYear(), m0.getMonth(), 1 - ((m0.getDay() + 6) % 7));
      var weeks = Math.ceil((((m0.getDay() + 6) % 7) + new Date(m0.getFullYear(), m0.getMonth() + 1, 0).getDate()) / 7), cells = {};
      for (var i = 0; i < weeks * 7; i++) {
        var day = addDays(start, i), ds = ymd(day), cell = el('div', 'cal-day' + (day.getMonth() !== m0.getMonth() ? ' other' : '') + (ds === ymd(now) ? ' today' : ''));
        var top = el('div', 'cal-top'); top.appendChild(el('span', 'cal-num', String(day.getDate())));
        var add = el('button', 'cal-add', '+'); add.type = 'button'; add.title = T('Termin am {date}', { date: day.toLocaleDateString(window.SAGA_LOCALE) }); add.setAttribute('aria-label', add.title);
        add.addEventListener('click', (function (d) { return function () { eventDialog(null, { date: d }, function () { render(true); }); }; })(ds));
        top.appendChild(add); cell.appendChild(top); grid.appendChild(cell); cells[ds] = cell;
      }
      Promise.all([accounts(), load(start, addDays(start, weeks * 7), fresh)]).then(function (r) {
        if (!r[0].length) {
          info.textContent = ''; var cn = el('button', 'btn', T('Kalender verbinden …')); cn.type = 'button';
          cn.addEventListener('click', function () { window.HKSettings && HKSettings.open('calendar'); }); container.insertBefore(cn, grid);
          return;
        }
        var map = byDay(r[1].events);
        Object.keys(map).forEach(function (k) { if (cells[k]) map[k].forEach(function (e) { cells[k].appendChild(chip(e, function (ev) { eventDialog(ev, {}, function () { render(true); }); })); }); });
        info.textContent = r[1].errors.length ? '⚠ ' + r[1].errors.map(function (x) { return x.error; }).join(' · ') : T('{n} {n:Termin|Termine}', { n: r[1].events.length });
        info.classList.toggle('err', !!r[1].errors.length);
      }).catch(function (err) { info.textContent = '⚠ ' + err.message; info.classList.add('err'); });
    }
    render();
    return { refresh: function () { render(true); } };
  }
  // --- Liste: Termine der naechsten Tage (wie in der App) ------------------------------------
  var fAg = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
  function agenda(container, opts) {
    var days = (opts && opts.days) || 14;
    function render(fresh) {
      container.textContent = '';
      var head = el('div', 'cal-head ag-head');
      head.appendChild(el('span', 'ag-title', '📅 ' + T('Termine ({n} Tage)', { n: days })));
      var rb = el('button', 'db-chip', '⟳'); rb.type = 'button'; rb.title = T('Aktualisieren (Änderungen aus mailcow/Nextcloud sofort holen)'); rb.setAttribute('aria-label', T('Aktualisieren'));
      rb.addEventListener('click', function () { render(true); }); head.appendChild(rb);
      var nb = el('button', 'db-chip cal-new', T('+ Termin')); nb.type = 'button';
      nb.addEventListener('click', function () { eventDialog(null, { date: ymd(new Date()) }, function () { render(true); }); });
      head.appendChild(nb); container.appendChild(head);
      var list = el('div', 'ag-list'); list.appendChild(el('div', 'ag-none', T('lädt …'))); container.appendChild(list);
      var t0 = new Date(); t0 = new Date(t0.getFullYear(), t0.getMonth(), t0.getDate());
      Promise.all([accounts(), load(t0, addDays(t0, days), fresh)]).then(function (r) {
        list.textContent = '';
        if (!r[0].length) {
          var cn = el('button', 'linkish', T('Noch kein Kalender verbunden – in den Einstellungen verbinden')); cn.type = 'button';
          cn.addEventListener('click', function () { window.HKSettings && HKSettings.open('calendar'); }); list.appendChild(cn); return;
        }
        var evs = r[1].events.slice().sort(function (a, b) { return a.start < b.start ? -1 : a.start > b.start ? 1 : 0; });
        if (!evs.length) list.appendChild(el('div', 'ag-none', T('Keine Termine')));
        evs.forEach(function (e) {
          var b = el('button', 'ag-ev'); b.type = 'button'; b.style.setProperty('--cc', e.color || '#4184e4');
          b.appendChild(el('span', 'ag-bar'));
          var tx = el('span', 'ag-txt'), sum = el('span', 'ag-sum', e.summary || T('(ohne Titel)'));
          if (e.series) sum.appendChild(el('span', 'ag-rep', ' 🔁'));
          tx.appendChild(sum);
          var d = e.allday ? parseDay(e.start.slice(0, 10)) : new Date(e.start);
          tx.appendChild(el('span', 'ag-sub', fAg.format(d) + (e.allday ? ' · ' + T('ganztägig') : ', ' + fTime.format(d)) + (e.location ? ' · ' + e.location : '')));
          b.appendChild(tx); b.title = e.calendar + (e.series ? ' · ' + T('Serientermin') : '');
          b.addEventListener('click', function () { eventDialog(e, {}, function () { render(true); }); });
          list.appendChild(b);
        });
        if (r[1].errors.length) list.appendChild(el('div', 'ag-none err', '⚠ ' + r[1].errors.map(function (x) { return x.error; }).join(' · ')));
      }).catch(function (err) { list.textContent = '⚠ ' + err.message; });
    }
    render();
    return { refresh: function () { render(true); } };
  }

  // --- Dashboard: Termine heute/morgen in der rechten Leiste -------------------------------
  function railInit() {
    var box = document.getElementById('rail-cal'); if (!box) return;
    var list = document.getElementById('rail-cal-list'), cnt = document.getElementById('rail-cal-count');
    var fDayLong = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'long', day: 'numeric', month: 'long' });
    function draw(fresh) {
      accounts().then(function (accs) {
        if (!accs.length) { box.hidden = true; return null; }          // nichts verbunden: Bereich bleibt unsichtbar
        var t0 = new Date(); t0 = new Date(t0.getFullYear(), t0.getMonth(), t0.getDate());
        return load(t0, addDays(t0, 2), fresh).then(function (d) {
          box.hidden = false; list.textContent = '';
          var map = byDay(d.events), n = 0;
          [[t0, T('Heute')], [addDays(t0, 1), T('Morgen')]].forEach(function (x) {
            var evs = map[ymd(x[0])] || []; n += evs.length;
            var g = el('div', 'rc-group'); g.appendChild(el('div', 'rc-day', x[1] + ' · ' + fDayLong.format(x[0])));
            if (!evs.length) g.appendChild(el('div', 'rc-none', T('Keine Termine')));
            evs.forEach(function (e) {
              var b = el('button', 'rc-ev'); b.type = 'button'; b.style.setProperty('--cc', e.color || '#4184e4');
              b.appendChild(el('span', 'rc-time', e.allday ? T('ganztägig') : fTime.format(new Date(e.start))));
              var tx = el('span', 'rc-txt'); tx.appendChild(el('span', 'rc-sum', e.summary || T('(ohne Titel)')));
              if (e.location) tx.appendChild(el('span', 'rc-loc', e.location));
              b.appendChild(tx); b.title = e.calendar + (e.series ? ' · ' + T('Serientermin') : '');
              b.addEventListener('click', function () { eventDialog(e, {}, function () { draw(true); }); });
              g.appendChild(b);
            });
            list.appendChild(g);
          });
          cnt.textContent = n ? String(n) : '';
          if (d.errors.length) list.appendChild(el('div', 'rc-err', '⚠ ' + d.errors.map(function (x) { return x.error; }).join(' · ')));
        });
      }).catch(function () { box.hidden = true; });
    }
    document.getElementById('rail-cal-add').addEventListener('click', function () { eventDialog(null, { date: ymd(new Date()) }, function () { draw(true); }); });
    draw();
    setInterval(function () { if (!document.hidden) draw(); }, 5 * 60 * 1000);        // Dashboard bleibt lange offen
    document.addEventListener('visibilitychange', function () { if (!document.hidden) draw(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', railInit); else railInit();

  window.HKCal = { ymd: ymd, load: load, byDay: byDay, chip: chip, label: label, eventDialog: eventDialog, month: month, agenda: agenda, fDay: fDay };
})();
