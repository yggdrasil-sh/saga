// Saga - Aufgaben im Dashboard (pro Benutzer, serverseitig gespeichert). Notizen: /notes (pages.js)
// Alle Inhalte werden ausschliesslich per textContent eingefuegt (kein HTML aus Benutzereingaben).
(function () {
  'use strict';
  var HK = window.HK, $ = HK.$, el = HK.el, api = HK.api, store = HK.store, fUpd = HK.fUpd;
  var toast = HK.toast, openMenu = HK.openMenu, closeMenu = HK.closeMenu, menuItem = HK.menuItem, menuLabel = HK.menuLabel;

  // Datum: lokales "heute" als YYYY-MM-DD, Anzeige "Fr., 9. Okt."
  function todayIso() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  var fDue = new Intl.DateTimeFormat(window.SAGA_LOCALE, { weekday: 'short', day: 'numeric', month: 'short' });
  function dueLabel(iso) {
    var t = todayIso();
    if (iso === t) return { text: T('heute'), cls: 'today' };
    var p = iso.split('-'), d = new Date(+p[0], +p[1] - 1, +p[2]);
    return { text: fDue.format(d), cls: iso < t ? 'overdue' : '' };
  }

  var panel = $('nt');
  if (!panel) return;
  var state = { tasks: { open: [], done: [] }, showDone: false };

  // --- Panel ---------------------------------------------------------------
  panel.addEventListener('toggle', function () { store('hk-nt-open', panel.open ? '1' : '0'); });
  if (store('hk-nt-open') === '1') panel.open = true;

  // --- Aufgaben --------------------------------------------------------------
  var PRIO = { 1: T('Hoch'), 2: T('Normal'), 3: T('Niedrig') };
  var REP = { daily: T('täglich'), weekdays: T('werktags'), weekly: T('wöchentlich'), monthly: T('monatlich'), yearly: T('jährlich') };
  var REP_ORDER = ['daily', 'weekdays', 'weekly', 'monthly', 'yearly'];

  function repeatMenu(current, pick) {
    return function (m) {
      m.appendChild(menuLabel(T('Wiederholen')));
      REP_ORDER.forEach(function (k) { m.appendChild(menuItem(REP[k], function () { pick(k); }, { on: current === k })); });
      if (current) m.appendChild(menuItem(T('Nicht wiederholen'), function () { pick(null); }));
    };
  }

  function patchTask(id, body) {
    body.today = todayIso();
    return api('PATCH', '/api/tasks/' + id, body).catch(function (err) { toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }

  // Abhaken mit "Rueckgaengig" (8 s, wie in den Apps); der Server nimmt dabei auch die naechste Wiederholung zurueck
  function complete(task) {
    var closing = !task.done;
    patchTask(task.id, { done: closing }).then(function (d) {
      if (closing && d) {
        var msg = d.next_due ? T('Erledigt. Nächstes Mal: {date}', { date: dueLabel(d.next_due).text }) : T('Aufgabe erledigt');
        toast(msg, T('Rückgängig'), function () { patchTask(task.id, { done: false }).then(loadTasks); }, 8000);
      }
      return loadTasks();
    });
  }

  function editText(task, span) {
    var inp = el('input', 'task-edit'); inp.type = 'text'; inp.maxLength = 500; inp.value = task.text;
    span.replaceWith(inp); inp.focus(); inp.select();
    var done = false;
    function finish(save) {
      if (done) return; done = true;
      var v = inp.value.trim();
      if (save && v && v !== task.text) patchTask(task.id, { text: v }).then(loadTasks);
      else loadTasks();
    }
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  var rowDue = $('row-due'), rowDueTask = null;
  rowDue.addEventListener('change', function () {
    if (rowDueTask && rowDue.value) patchTask(rowDueTask.id, { due: rowDue.value }).then(loadTasks);
    rowDueTask = null;
  });
  function pickDate(task, anchor) {
    rowDueTask = task; rowDue.value = task.due || '';
    var r = anchor.getBoundingClientRect();
    rowDue.style.left = r.left + 'px'; rowDue.style.top = r.bottom + 'px';
    try { if (rowDue.showPicker) { rowDue.showPicker(); return; } } catch (e) { /* aeltere Browser */ }
    rowDue.focus(); rowDue.click();
  }

  function rowMenu(task, btn) {
    openMenu(btn, function (m) {
      m.appendChild(menuLabel(T('Priorität')));
      var seg = el('div', 'menu-seg');
      [1, 2, 3].forEach(function (p) {
        var b = el('button', 'menu-seg-btn p' + p + (task.prio === p ? ' on' : ''), PRIO[p]); b.type = 'button';
        b.addEventListener('click', function () { closeMenu(); if (task.prio !== p) patchTask(task.id, { prio: p }).then(loadTasks); });
        seg.appendChild(b);
      });
      m.appendChild(seg);
      repeatMenu(task.repeat, function (k) { patchTask(task.id, { repeat: k }).then(loadTasks); })(m);
      m.appendChild(menuLabel(T('Fällig')));
      m.appendChild(menuItem(task.due ? T('Datum ändern …') : T('Datum wählen …'), function () { pickDate(task, btn); }));
      if (task.due && !task.repeat) m.appendChild(menuItem(T('Datum entfernen'), function () { patchTask(task.id, { due: null }).then(loadTasks); }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(menuItem(T('Aufgabe löschen'), function () { api('DELETE', '/api/tasks/' + task.id).then(loadTasks, loadTasks); }, { danger: true }));
    });
  }

  // Eine Zeile fuer Panel (full) oder rechte Leiste (compact)
  function taskItem(task, compact) {
    var li = el('li', (compact ? 'rail-item' : 'task') + ' p' + (task.prio || 2) + (task.done ? ' is-done' : ''));
    var cb = el('button', 'task-check'); cb.type = 'button';
    cb.setAttribute('aria-label', (task.done ? T('Wieder öffnen: {text}', { text: task.text }) : T('Erledigt: {text}', { text: task.text })) + (task.prio === 1 ? ' ' + T('(Priorität hoch)') : ''));
    if (task.prio === 1 || task.prio === 3) cb.title = T('Priorität:') + ' ' + (task.prio === 1 ? T('Hoch') : T('Niedrig'));   // roter/gestrichelter Kreis erklaert
    cb.textContent = task.done ? '✓' : '';
    cb.addEventListener('click', function () { cb.disabled = true; complete(task); });
    li.appendChild(cb);

    var d = task.due ? dueLabel(task.due) : null;
    if (compact) {
      var main = el('div', 'rail-main');
      main.appendChild(el('span', 'rail-text', task.text));
      if (d || task.repeat) {
        var m = el('span', 'rail-meta');
        if (d) m.appendChild(el('span', 'task-due ' + d.cls, d.text));
        if (task.repeat) m.appendChild(el('span', 'rep', '↻'));
        main.appendChild(m);
      }
      li.appendChild(main);
      // wie im Aufgaben-Kasten: "⋯" fuer Datum, Prioritaet, Wiederholung, Loeschen; Klick auf den Text oeffnet dasselbe (BUG-094)
      var rm = el('button', 'task-more rail-more', '⋯'); rm.type = 'button';
      rm.setAttribute('aria-label', T('Optionen für {text}', { text: task.text })); rm.setAttribute('aria-haspopup', 'menu');
      rm.addEventListener('click', function () { rowMenu(task, rm); });
      main.addEventListener('click', function () { rowMenu(task, rm); });
      li.appendChild(rm);
      return li;
    }

    var span = el('span', 'task-text', task.text);
    if (!task.done) { span.title = T('Klicken zum Bearbeiten'); span.addEventListener('click', function () { editText(task, span); }); }
    li.appendChild(span);
    var info = el('span', 'task-info');
    if (task.repeat) info.appendChild(el('span', 'rep', '↻ ' + REP[task.repeat]));
    if (d) info.appendChild(el('span', 'task-due' + (!task.done && d.cls ? ' ' + d.cls : ''), d.text));
    li.appendChild(info);
    var more = el('button', 'task-more', '⋯'); more.type = 'button';
    more.setAttribute('aria-label', T('Optionen für {text}', { text: task.text })); more.setAttribute('aria-haspopup', 'menu');
    if (task.done) {
      more.textContent = '×'; more.setAttribute('aria-label', T('Löschen: {text}', { text: task.text }));
      more.addEventListener('click', function () { api('DELETE', '/api/tasks/' + task.id).then(loadTasks, loadTasks); });
    } else {
      more.addEventListener('click', function () { rowMenu(task, more); });
    }
    li.appendChild(more);
    return li;
  }

  function badge() {
    var b = $('nt-badge'), n = state.tasks.open.length, t = todayIso();
    var over = state.tasks.open.filter(function (x) { return x.due && x.due < t; }).length;
    b.hidden = !n;
    b.textContent = over ? T('{n} offen · {over} überfällig', { n: n, over: over }) : T('{n} offen', { n: n });
    b.classList.toggle('alert', over > 0);
    $('rail-count').textContent = n ? String(n) : '';
    $('rail-tab-count').textContent = n ? String(n) : '';
    $('rail-show').classList.toggle('alert', over > 0);
  }

  function renderTasks() {
    var ul = $('task-list'); ul.textContent = '';
    state.tasks.open.forEach(function (t) { ul.appendChild(taskItem(t, false)); });
    $('task-empty').hidden = state.tasks.open.length > 0;
    var dn = state.tasks.done.length, tog = $('task-toggle-done');
    tog.hidden = !dn; $('task-clear').hidden = !dn || !state.showDone;
    tog.textContent = state.showDone ? T('Erledigte ausblenden') : T('Erledigte anzeigen ({n})', { n: dn });
    var dl = $('task-done'); dl.textContent = ''; dl.hidden = !state.showDone || !dn;
    if (state.showDone) state.tasks.done.forEach(function (t) { dl.appendChild(taskItem(t, false)); });
    var rl = $('rail-list'); rl.textContent = '';
    state.tasks.open.forEach(function (t) { rl.appendChild(taskItem(t, true)); });
    $('rail-empty').hidden = state.tasks.open.length > 0;
    badge();
  }

  function loadTasks() {
    return api('GET', '/api/tasks').then(function (d) { state.tasks = d; renderTasks(); }).catch(function () {});
  }

  // --- Eingabezeile im Panel: Prioritaet, Wiederholung, Datum ---------------
  var add = { prio: 2, repeat: null };
  function setPrio(p) {
    add.prio = p;
    $('task-prio-chip').hidden = p === 2;
    $('task-prio-chip').className = 'mark prio-mark p' + p;
    $('task-prio-label').textContent = p === 2 ? '' : PRIO[p];
  }
  function setRep(k) {
    add.repeat = k;
    $('task-rep-chip').hidden = !k;
    $('task-rep-label').textContent = k ? '↻ ' + REP[k] : '';
  }
  function setDue(iso) {
    $('task-due').value = iso || '';
    $('task-due-chip').hidden = !iso;
    $('task-due-label').textContent = iso ? dueLabel(iso).text : '';
  }
  function resetAdd() { setPrio(2); setRep(null); setDue(''); }

  $('task-prio-btn').addEventListener('click', function () {   // Klick wechselt: normal -> hoch -> niedrig -> normal
    setPrio(add.prio === 2 ? 1 : add.prio === 1 ? 3 : 2); $('task-text').focus();
  });
  $('task-prio-clear').addEventListener('click', function () { setPrio(2); $('task-text').focus(); });
  $('task-rep-btn').addEventListener('click', function () {
    openMenu(this, repeatMenu(add.repeat, function (k) { setRep(k); $('task-text').focus(); }));
  });
  $('task-rep-clear').addEventListener('click', function () { setRep(null); $('task-text').focus(); });
  $('task-due-btn').addEventListener('click', function () {
    var d = $('task-due');
    try { if (d.showPicker) { d.showPicker(); return; } } catch (e) { /* aeltere Browser */ }
    d.focus(); d.click();
  });
  $('task-due').addEventListener('change', function () { setDue(this.value); $('task-text').focus(); });
  $('task-due-clear').addEventListener('click', function () { setDue(''); $('task-text').focus(); });

  function addTask(text, extra) {
    return api('POST', '/api/tasks', Object.assign({ text: text, today: todayIso() }, extra || {}));
  }
  $('task-add').addEventListener('submit', function (e) {
    e.preventDefault();
    var txt = $('task-text'), v = txt.value.trim();
    if (!v) return;
    addTask(v, { due: $('task-due').value || null, prio: add.prio, repeat: add.repeat })
      .then(function () { txt.value = ''; resetAdd(); txt.focus(); return loadTasks(); })
      .catch(function (err) { toast(T('Aufgabe nicht gespeichert: {err}', { err: err.message })); });
  });
  // Formular mit mehreren Feldern und ohne Submit-Button sendet bei Enter laut HTML-Standard
  // NICHT von selbst ab -> Enter explizit behandeln.
  $('task-text').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); $('task-add').requestSubmit(); }
    if (e.key === 'Escape' && !HK.menuOpen()) { e.preventDefault(); e.stopPropagation(); this.value = ''; resetAdd(); this.blur(); }
  });

  // --- Rechte Leiste ---------------------------------------------------------
  $('rail-add').addEventListener('submit', function (e) {
    e.preventDefault();
    var inp = $('rail-text'), v = inp.value.trim();
    if (!v) return;
    addTask(v).then(function () { inp.value = ''; return loadTasks(); })
      .catch(function (err) { toast(T('Aufgabe nicht gespeichert: {err}', { err: err.message })); });
  });
  function setRail(on) {
    document.body.classList.toggle('rail-on', on);
    $('rail-show').hidden = on;
    store('hk-rail', on ? '1' : '0');
  }
  // Breiter Bildschirm: Aufgaben stehen nur einmal da - entweder in der Leiste oder (Leiste eingeklappt) im Hauptbereich
  // mit allen Funktionen (Prioritaet, Datum, Erledigte). Einklappen bzw. "Alle Aufgaben" klappt den Kasten dort auf.
  function railVisible() { return getComputedStyle($('rail')).display !== 'none'; }
  function toMain() { setRail(false); panel.open = true; }
  $('rail-hide').addEventListener('click', toMain);
  $('rail-all').addEventListener('click', function () { toMain(); $('task-text').focus(); });
  $('rail-show').addEventListener('click', function () { setRail(true); });
  setRail(store('hk-rail') !== '0');

  $('task-toggle-done').addEventListener('click', function () { state.showDone = !state.showDone; renderTasks(); });
  $('task-clear').addEventListener('click', function () {
    if (!confirm(T('Alle erledigten Aufgaben endgültig löschen?'))) return;
    api('POST', '/api/tasks/clear-done').then(function () { state.showDone = false; return loadTasks(); });
  });

  // --- Tastenkuerzel "t" -----------------------------------------------------
  document.addEventListener('keydown', function (e) {
    var t = e.target;
    if ((e.key !== 't' && e.key !== 'n') || e.metaKey || e.ctrlKey || e.altKey) return;
    if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;
    e.preventDefault();
    if (e.key === 'n') { window.open('/notes', 'hk-notes'); return; }   // immer derselbe Notizen-Tab
    if (railVisible()) { $('rail-text').focus(); return; }
    panel.open = true; $('task-text').focus();
  });

  loadTasks();
})();
