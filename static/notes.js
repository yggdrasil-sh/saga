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
  var state = { tasks: { open: [], done: [], items: [], sources: [] }, showDone: false };
  var TS = HK.tasks;                                   // 2.5 (Paket 7): Inbox + Aufgabenquellen (common.js)
  function srcOf(task) { return (state.tasks.sources || []).find(function (x) { return x.db === task.src; }) || {}; }
  function isRow(task) { return task.kind === 'row'; }

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

  function patchTask(task, body) {
    return TS.patch(typeof task === 'object' ? task : { kind: 'task', id: task }, body)
      .catch(function (err) { toast(T('Nicht gespeichert: {err}', { err: err.message })); });
  }

  // Abhaken mit "Rueckgaengig" (8 s, wie in den Apps); der Server nimmt dabei auch die naechste Wiederholung zurueck
  function complete(task) {
    var closing = !task.done;
    patchTask(task, { done: closing }).then(function (d) {
      if (closing && d) {
        var msg = d.next_due ? T('Erledigt. Nächstes Mal: {date}', { date: dueLabel(d.next_due).text }) : T('Aufgabe erledigt');
        var back = { done: false };
        if (isRow(task) && d.prev !== undefined) back.value = d.prev;     // Zeile: vorherigen Wert wiederherstellen (z. B. "Läuft")
        toast(msg, T('Rückgängig'), function () { patchTask(task, back).then(loadTasks); }, 8000);
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
      if (save && v && v !== task.text) patchTask(task, { text: v }).then(loadTasks);
      else loadTasks();
    }
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  var rowDue = $('row-due'), rowDueTask = null;
  // Web-Runde 2 (028): direkt unter <body> - in der Seitenleiste (transform) bezog sich position:fixed auf die Leiste,
  // der Datumswaehler oeffnete am Fensterrand statt an der Aufgabe
  if (rowDue.parentNode !== document.body) document.body.appendChild(rowDue);
  rowDue.addEventListener('change', function () {
    if (rowDueTask && rowDue.value) patchTask(rowDueTask, { due: rowDue.value }).then(loadTasks);
    rowDueTask = null;
  });
  function pickDate(task, anchor) {
    rowDueTask = task; rowDue.value = task.due || '';
    var row = (anchor.closest && anchor.closest('li')) || anchor, r = row.getBoundingClientRect();
    rowDue.style.left = Math.max(8, r.left) + 'px'; rowDue.style.top = r.bottom + 'px'; rowDue.style.right = 'auto'; rowDue.style.bottom = 'auto';
    try { if (rowDue.showPicker) { rowDue.showPicker(); return; } } catch (e) { /* aeltere Browser */ }
    rowDue.focus(); rowDue.click();
  }

  function rowMenu(task, btn) {
    if (isRow(task)) { srcMenu(task, btn); return; }
    openMenu(btn, function (m) {
      m.appendChild(menuLabel(T('Priorität')));
      var seg = el('div', 'menu-seg');
      [1, 2, 3].forEach(function (p) {
        var b = el('button', 'menu-seg-btn p' + p + (task.prio === p ? ' on' : ''), PRIO[p]); b.type = 'button';
        b.addEventListener('click', function () { closeMenu(); if (task.prio !== p) patchTask(task, { prio: p }).then(loadTasks); });
        seg.appendChild(b);
      });
      m.appendChild(seg);
      repeatMenu(task.repeat, function (k) { patchTask(task, { repeat: k }).then(loadTasks); })(m);
      m.appendChild(menuLabel(T('Fällig')));
      m.appendChild(menuItem(task.due ? T('Datum ändern …') : T('Datum wählen …'), function () { pickDate(task, btn); }));
      if (task.due && !task.repeat) m.appendChild(menuItem(T('Datum entfernen'), function () { patchTask(task, { due: null }).then(loadTasks); }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(menuItem(T('Aufgabe löschen'), function () { api('DELETE', '/api/tasks/' + task.id).then(loadTasks, loadTasks); }, { danger: true }));
    });
  }

  // Quell-Zeile (Datenbank): Prioritaet/Datum nur, wenn die Quelle sie zuordnet; Loeschen und Wiederholen gibt es hier nicht
  function srcMenu(task, btn) {
    var src = srcOf(task), mp = src.map || {};
    openMenu(btn, function (m) {
      m.appendChild(menuItem(T('Eintrag öffnen'), function () { window.location.href = '/notes#' + task.id; }, { icon: '↗' }));
      if (!task.edit) { m.appendChild(menuLabel(T('Nur lesen – du kannst diesen Eintrag nicht ändern.'))); return; }
      if (mp.prio) {
        m.appendChild(menuLabel(T('Priorität')));
        var seg = el('div', 'menu-seg');
        [1, 2, 3].forEach(function (p) {
          var b = el('button', 'menu-seg-btn p' + p + (task.prio === p ? ' on' : ''), PRIO[p]); b.type = 'button';
          b.addEventListener('click', function () { closeMenu(); if (task.prio !== p) patchTask(task, { prio: p }).then(loadTasks); });
          seg.appendChild(b);
        });
        m.appendChild(seg);
      }
      if (mp.due) {
        m.appendChild(menuLabel(T('Fällig')));
        m.appendChild(menuItem(task.due ? T('Datum ändern …') : T('Datum wählen …'), function () { pickDate(task, btn); }));
        if (task.due) m.appendChild(menuItem(T('Datum entfernen'), function () { patchTask(task, { due: null }).then(loadTasks); }));
      }
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
    if (isRow(task) && !task.edit) { cb.disabled = true; cb.title = T('Nur lesen – du kannst diesen Eintrag nicht ändern.'); }
    if (isRow(task)) li.classList.add('is-src');
    li.appendChild(cb);

    var d = task.due ? dueLabel(task.due) : null;
    if (compact) {
      var main = el('div', 'rail-main');
      main.appendChild(el('span', 'rail-text', task.text));
      if (d || task.repeat || isRow(task)) {
        var m = el('span', 'rail-meta');
        if (isRow(task)) m.appendChild(TS.badge(task, state.tasks));
        if (d) m.appendChild(el('span', 'task-due ' + d.cls, d.text + (task.time ? ' ' + task.time : '')));
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
    if (!task.done && (!isRow(task) || task.edit)) { span.title = T('Klicken zum Bearbeiten'); span.addEventListener('click', function () { editText(task, span); }); }
    li.appendChild(span);
    var info = el('span', 'task-info');
    if (isRow(task)) info.appendChild(TS.badge(task, state.tasks));
    if (task.repeat) info.appendChild(el('span', 'rep', '↻ ' + REP[task.repeat]));
    if (d) info.appendChild(el('span', 'task-due' + (!task.done && d.cls ? ' ' + d.cls : ''), d.text + (task.time ? ' ' + task.time : '')));
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

  function openList() { return state.tasks.items || state.tasks.open; }
  function badge() {
    var b = $('nt-badge'), n = openList().length;
    var over = openList().filter(function (x) { return x.overdue; }).length;
    b.hidden = !n;
    b.textContent = over ? T('{n} offen · {over} überfällig', { n: n, over: over }) : T('{n} offen', { n: n });
    b.classList.toggle('alert', over > 0);
    $('rail-count').textContent = n ? String(n) : '';
    $('rail-tab-count').textContent = n ? String(n) : '';
    $('rail-show').classList.toggle('alert', over > 0);
  }

  function renderTasks() {
    var ul = $('task-list'); ul.textContent = '';
    openList().forEach(function (t) { ul.appendChild(taskItem(t, false)); });
    $('task-empty').hidden = openList().length > 0;
    var dn = state.tasks.done.length, tog = $('task-toggle-done');
    tog.hidden = !dn; $('task-clear').hidden = !dn || !state.showDone;
    tog.textContent = state.showDone ? T('Erledigte ausblenden') : T('Erledigte anzeigen ({n})', { n: dn });
    var dl = $('task-done'); dl.textContent = ''; dl.hidden = !state.showDone || !dn;
    if (state.showDone) state.tasks.done.forEach(function (t) { dl.appendChild(taskItem(t, false)); });
    var rl = $('rail-list'); rl.textContent = '';
    openList().forEach(function (t) { rl.appendChild(taskItem(t, true)); });
    $('rail-empty').hidden = openList().length > 0;
    targets();
    badge();
  }

  function loadTasks() {
    return TS.load().then(function (d) { state.tasks = d; renderTasks(); }).catch(function () {});
  }
  // Ziel der neuen Aufgabe (Inbox oder Aufgabenquelle mit Bearbeiten-Recht), je Eingabe gemerkt
  var tgtPanel = null, tgtRail = null;
  function targets() {
    [['task-add', 'panel'], ['rail-add', 'rail']].forEach(function (x) {
      var f = $(x[0]); if (!f) return;
      var old = f.querySelector('.tsrc-target'), s = TS.targetSelect(state.tasks, x[1]);
      if (old) { if (!s.hidden || !old.hidden) s.value = old.value || s.value; old.replaceWith(s); } else f.appendChild(s);
      if (x[1] === 'panel') tgtPanel = s; else tgtRail = s;
    });
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

  function addTask(text, extra, sel) {
    var tg = sel && !sel.hidden ? sel.value : '';
    if (tg && extra) { delete extra.repeat; }
    return TS.add(text, tg, extra);
  }
  $('task-add').addEventListener('submit', function (e) {
    e.preventDefault();
    var txt = $('task-text'), v = txt.value.trim();
    if (!v) return;
    // Nr. 160: "… morgen" im Text setzt kein Datum - einmal je Browser zeigen, wo die Faelligkeit hingehoert
    var dayWord = !$('task-due').value && /(^|\s)(heute|morgen|übermorgen|today|tomorrow|hoy|mañana|сегодня|завтра|послезавтра|วันนี้|พรุ่งนี้)\s*$/i.test(v);
    addTask(v, { due: $('task-due').value || null, prio: add.prio, repeat: add.repeat }, tgtPanel)
      .then(function () {
        txt.value = ''; resetAdd(); txt.focus();
        var seen = false; try { seen = localStorage.getItem('saga-task-due-tip') === '1'; } catch (e) { /* privat */ }
        if (dayWord && !seen) {
          toast(T('Tipp: Ein Tag im Text setzt kein Fälligkeitsdatum – dafür das Kalender-Symbol neben dem Eingabefeld nutzen.'), null, null, 8000);
          try { localStorage.setItem('saga-task-due-tip', '1'); } catch (e) { /* privat */ }
        }
        return loadTasks();
      })
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
    addTask(v, null, tgtRail).then(function () { inp.value = ''; return loadTasks(); })
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

  // Quellen aendern sich auch anderswo (Board, andere Personen): beim Zurueckkehren in den Tab neu laden
  document.addEventListener('visibilitychange', function () { if (!document.hidden) loadTasks(); });
  loadTasks();
})();
