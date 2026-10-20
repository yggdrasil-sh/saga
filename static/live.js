// Saga - Live-Kanal (1.4): Glocke mit Benachrichtigungen, Live-Aktualisierung (Kommentare, "Mit mir geteilt",
// ungelesen-Punkte), Anwesenheit im Seitenkopf, stilles Neuladen bzw. Hinweis bei Aenderungen anderer.
// Server-Sent Events ueber /api/events (kein eigener Port); fehlt EventSource oder bricht der Kanal ab (Proxy puffert,
// Verbindungsgrenze), fragt die Seite alle 60 s ab. Laeuft auf Dashboard und Seiten, nicht im Peek-Iframe.
// Alle Inhalte per textContent. Braucht static/common.js; auf der Seiten-Ansicht static/pages.js + comments.js.
(function () {
  'use strict';
  var HK = window.HK; if (!HK) return;
  var $ = HK.$, el = HK.el, api = HK.api;
  var userBtn = $('hk-user');
  if (!userBtn || document.body.classList.contains('embed')) return;
  var NOTES = document.body.classList.contains('notes-page');
  function K() { return NOTES && window.HKPages ? window.HKPages.k : null; }
  var POLL = 60000, PING = 20000, EDIT_MS = 30000;
  var ROLE = { view: T('Lesen##Rolle'), comment: T('Kommentieren##Rolle'), edit: T('Bearbeiten##Rolle') };
  var LABEL = {
    'share.added': T('Jemand teilt eine Seite mit mir'),
    'share.role': T('Meine Rolle auf einer geteilten Seite ändert sich'),
    'comment.new': T('Neue Kommentare auf meinen Seiten'),
    'comment.reply': T('Antworten in Unterhaltungen, an denen ich beteiligt bin'),
    'publish.withdrawn': T('Ein Admin zieht eine Veröffentlichung von mir zurück'),
    'chat.message': T('Neue Chat-Nachrichten (ohne Inhalt; stummgeschaltete Chats nie)'),
    'call.missed': T('Verpasste Anrufe')
  };
  var narrow = window.matchMedia('(max-width: 760px)');
  var NS = 'http://www.w3.org/2000/svg';
  function svg(paths, cls) {
    var s = document.createElementNS(NS, 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('aria-hidden', 'true');
    s.setAttribute('class', cls || ''); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', '1.8'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    paths.forEach(function (d) { var p = document.createElementNS(NS, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  // Farbe je Person (Anfangsbuchstabe auf farbigem Kreis) - gleiche Person, gleiche Farbe
  function hue(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function avatar(name, key, cls, url) {
    var a = el('span', 'lv-av' + (cls ? ' ' + cls : ''), String(name || '?').trim().charAt(0).toUpperCase() || '?');
    a.style.setProperty('--lv-h', String(hue(key || name))); a.setAttribute('aria-hidden', 'true');
    return HK.avatarImg(a, url, 30);                  // Profilbild ueber den Initialen (falls vorhanden)
  }

  // =========================================================================================================
  // Glocke + Liste (Desktop: Popover unter der Glocke, Handy: Bottom-Sheet)
  // =========================================================================================================
  var N = { items: [], unread: 0, more: false, next: null, loaded: false, open: false, busy: false };
  var bell = el('button', 'btn hk-bell'); bell.type = 'button'; bell.id = 'hk-bell';
  bell.setAttribute('aria-haspopup', 'dialog'); bell.setAttribute('aria-expanded', 'false'); bell.setAttribute('aria-controls', 'lv-panel');
  bell.appendChild(svg(['M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9', 'M10.3 21a1.94 1.94 0 0 0 3.4 0'], 'hk-bell-ic'));
  var badge = el('span', 'hk-bell-n'); badge.hidden = true; bell.appendChild(badge);
  userBtn.parentNode.insertBefore(bell, userBtn);

  var panel = el('div', 'lv-panel'); panel.id = 'lv-panel'; panel.hidden = true;
  panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', T('Benachrichtigungen'));
  var phead = el('div', 'lv-head'), grip = el('span', 'lv-grip'); grip.setAttribute('aria-hidden', 'true');
  var ptitle = el('h2', 'lv-title-h', T('Benachrichtigungen'));
  var allRead = el('button', 'linkbtn lv-allread', T('Alle gelesen')); allRead.type = 'button';
  var gear = el('button', 'icon-btn lv-gear'); gear.type = 'button'; gear.title = T('Einstellungen'); gear.setAttribute('aria-label', T('Benachrichtigungen einstellen'));
  gear.appendChild(svg(['M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z'], 'lv-gear-ic'));
  var pclose = el('button', 'icon-btn lv-close', '×'); pclose.type = 'button'; pclose.setAttribute('aria-label', T('Schließen'));
  phead.appendChild(grip); phead.appendChild(ptitle); phead.appendChild(allRead); phead.appendChild(gear); phead.appendChild(pclose);
  var plist = el('ul', 'lv-list'), pfoot = el('div', 'lv-foot'), pstate = el('p', 'lv-state');
  pstate.setAttribute('role', 'status');
  var more = el('button', 'btn lv-more', T('Ältere laden')); more.type = 'button'; pfoot.appendChild(more);
  panel.appendChild(phead); panel.appendChild(pstate); panel.appendChild(plist); panel.appendChild(pfoot);
  var scrim = el('div', 'lv-scrim'); scrim.hidden = true;
  document.body.appendChild(scrim); document.body.appendChild(panel);

  function badgeDraw() {
    var n = N.unread || 0;
    badge.hidden = !n; badge.textContent = n > 99 ? '99+' : String(n);
    var lbl = T('Benachrichtigungen') + (n ? ' – ' + (n === 1 ? T('1 ungelesen') : T('{n} ungelesen', { n: n })) : '');
    bell.setAttribute('aria-label', lbl); bell.title = lbl;
    bell.classList.toggle('has-unread', !!n);
    allRead.disabled = !n;
  }
  function ring() {
    bell.classList.remove('ring'); void bell.offsetWidth; bell.classList.add('ring');
    setTimeout(function () { bell.classList.remove('ring'); }, 1200);
  }

  function sentence(it) {
    // dieselben Schluessel wie saga/notify.py (_text) - hier mit fettem Namen
    var name = el('b', null, it.actor ? it.actor.name : T('Gelöschtes Konto')), n = it.count || 1, s;
    if (it.kind === 'share.added') s = T('{name} hat eine Seite mit dir geteilt ({role})');
    else if (it.kind === 'share.role') s = T('{name} hat deine Rolle geändert: {role}');
    else if (it.kind === 'comment.new') s = n > 1 ? T('{n} neue Kommentare auf deiner Seite') : T('{name} hat deine Seite kommentiert');
    else if (it.kind === 'comment.reply') s = n > 1 ? T('{n} neue Antworten in einer Unterhaltung') : T('{name} hat in einer Unterhaltung geantwortet');
    else if (it.kind === 'comment.mention') s = T('{name} hat dich erwähnt');
    else if (it.kind === 'publish.withdrawn') s = T('Ein Admin hat deine Veröffentlichung zurückgezogen');
    else if (it.kind === 'chat.message') s = n > 1 ? T('{n} neue Nachrichten') : T('Neue Nachricht von {name}');
    else if (it.kind === 'call.missed') s = T('Verpasster Anruf von {name}');
    else if (it.kind === 'backup.failed') return document.createTextNode(it.text || T('Backup fehlgeschlagen'));   // Text vom Server (Admins)
    else return document.createTextNode(it.text || it.kind);
    return HK.fmtNodes(s, { name: name, role: ROLE[it.role] || '', n: n });
  }
  var fFull = new Intl.DateTimeFormat(HK.locale, { dateStyle: 'medium', timeStyle: 'short' });
  function itemNode(it) {
    var li = el('li', 'lv-li'), b = el('button', 'lv-item' + (it.read ? '' : ' unread')); b.type = 'button';
    if (it.kind === 'publish.withdrawn' || it.kind === 'backup.failed') { var sh = el('span', 'lv-av lv-av-sys', '🛡'); sh.setAttribute('aria-hidden', 'true'); b.appendChild(sh); }
    else b.appendChild(avatar(it.actor ? it.actor.name : '?', it.actor ? it.actor.username : '', it.actor ? '' : 'lv-av-gone', it.actor && it.actor.avatar));
    var body = el('span', 'lv-body');
    var tx = el('span', 'lv-text'); tx.appendChild(sentence(it)); body.appendChild(tx);
    var ic = it.note && it.note.icon && it.note.icon.charAt(0) !== '/' ? it.note.icon : '📄';
    if (it.kind === 'backup.failed') body.appendChild(el('span', 'lv-page', '🗄 ' + (it.title || T('Backups'))));
    else if (it.chat) body.appendChild(el('span', 'lv-page' + (it.access ? '' : ' gone'), it.access ? '💬 ' + (it.title || T('Chat')) : '🔒 ' + T('Kein Zugriff mehr')));
    else body.appendChild(el('span', 'lv-page' + (it.access ? '' : ' gone'), it.access ? ic + ' ' + (it.note.title || T('Unbenannt')) : '🔒 ' + T('Kein Zugriff mehr')));
    if (it.excerpt) body.appendChild(el('span', 'lv-ex', it.excerpt));
    var tm = el('span', 'lv-time', HK.relTime(it.created - Date.now() / 1000)); tm.title = fFull.format(new Date(it.created * 1000));
    body.appendChild(tm);
    b.appendChild(body);
    if (!it.read) { var d = el('span', 'lv-dot'); d.setAttribute('aria-label', T('Ungelesen')); b.appendChild(d); }
    b.addEventListener('click', function () { activate(it); });
    li.appendChild(b);
    return li;
  }
  function draw() {
    if (!N.open) return;
    var focusId = document.activeElement && document.activeElement.closest ? (document.activeElement.closest('.lv-item') || {}).dataset : null;
    plist.textContent = '';
    N.items.forEach(function (it) { var li = itemNode(it); li.firstChild.dataset.id = String(it.id); plist.appendChild(li); });
    pstate.hidden = !!N.items.length || !N.loaded;
    pstate.textContent = '';
    if (N.loaded && !N.items.length) {
      pstate.appendChild(el('b', null, T('Keine Benachrichtigungen')));
      pstate.appendChild(el('span', null, T('Hier erscheint, wenn jemand etwas mit dir teilt oder auf deinen Seiten kommentiert.')));
    }
    pfoot.hidden = !N.more;
    if (focusId && focusId.id) { var f = plist.querySelector('.lv-item[data-id="' + focusId.id + '"]'); if (f) f.focus(); }
  }
  function load(older) {
    if (N.busy) return Promise.resolve();
    N.busy = true;
    var url = '/api/notifications?limit=30' + (older && N.next ? '&before=' + N.next : '');
    return api('GET', url).then(function (d) {
      N.items = older ? N.items.concat(d.items) : d.items;
      N.more = !!d.more; N.next = d.next; N.unread = d.unread; N.loaded = true;
      badgeDraw(); draw();
    }).catch(function (err) {
      if (N.open && !N.items.length) { pstate.hidden = false; pstate.textContent = T('Nicht geladen: {err}', { err: err.message }); }
    }).then(function () { N.busy = false; });
  }
  function count() {
    return api('GET', '/api/notifications?limit=0' + (NOTES ? '' : '&counts=1')).then(function (d) {
      var grew = d.unread > N.unread;
      N.unread = d.unread; badgeDraw();
      if (!NOTES && d.comments_unread != null) pagesBadge(d.comments_unread);
      return grew;
    });
  }
  function markRead(ids) {
    var body = ids === 'all' ? { all: true } : { ids: ids };
    N.items.forEach(function (it) { if (ids === 'all' || ids.indexOf(it.id) >= 0) it.read = true; });
    draw();
    return api('POST', '/api/notifications/read', body).then(function (d) { N.unread = d.unread; badgeDraw(); }).catch(function () {});
  }
  function place() {
    if (narrow.matches) { panel.style.top = panel.style.right = panel.style.left = ''; return; }
    var r = bell.getBoundingClientRect();
    panel.style.left = '';
    panel.style.top = Math.round(r.bottom + 8) + 'px';
    panel.style.right = Math.max(8, Math.round(window.innerWidth - r.right)) + 'px';
  }
  function openPanel() {
    N.open = true; panel.hidden = false; bell.setAttribute('aria-expanded', 'true'); scrim.hidden = !narrow.matches;
    document.body.classList.add('lv-open');
    place(); draw(); load();
    setTimeout(function () { var f = plist.querySelector('.lv-item') || pclose; if (!narrow.matches || f !== pclose) f.focus(); }, 30);
  }
  function closePanel(back) {
    if (!N.open) return;
    N.open = false; panel.hidden = true; scrim.hidden = true; bell.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('lv-open');
    if (back) bell.focus();
  }
  bell.addEventListener('click', function () { if (N.open) closePanel(true); else openPanel(); });
  pclose.addEventListener('click', function () { closePanel(true); });
  scrim.addEventListener('click', function () { closePanel(true); });
  allRead.addEventListener('click', function () { markRead('all'); });
  more.addEventListener('click', function () { load(true); });
  gear.addEventListener('click', function () {
    closePanel(false);
    if (!window.HKSettings) return;
    HKSettings.open('account');
    var n = 0, t = setInterval(function () {             // Abschnitt kommt nach dem Laden des Kontos
      var s = $('hk-notify-set');
      if (s || ++n > 30) { clearInterval(t); if (s) { s.scrollIntoView({ block: 'start' }); var f = s.querySelector('input'); if (f) f.focus(); } }
    }, 100);
  });
  document.addEventListener('mousedown', function (e) {
    if (N.open && !narrow.matches && !panel.contains(e.target) && !bell.contains(e.target)) closePanel(false);
  });
  document.addEventListener('keydown', function (e) {
    if (!N.open) return;
    if (e.key === 'Escape' && !document.querySelector('.hk-ask')) { e.preventDefault(); e.stopPropagation(); closePanel(true); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {                 // Pfeiltasten durch die Liste
      var items = [].slice.call(plist.querySelectorAll('.lv-item')), i = items.indexOf(document.activeElement);
      if (i < 0 && !panel.contains(document.activeElement)) return;
      e.preventDefault();
      var n = e.key === 'ArrowDown' ? Math.min(items.length - 1, i + 1) : Math.max(0, i - 1);
      if (items[n]) items[n].focus();
    }
  }, true);
  window.addEventListener('resize', function () { if (N.open) { scrim.hidden = !narrow.matches; place(); } });

  // Eintrag oeffnen: als gelesen merken, Seite (und ggf. die Unterhaltung) zeigen
  function activate(it) {
    if (!it.read) markRead([it.id]);
    closePanel(false);
    if (it.kind === 'backup.failed') {                   // Admin-Bereich (eigener Port) in einem neuen Tab
      if (it.url) { var aw = window.open(it.url, '_blank', 'noopener'); if (aw) aw.focus(); }
      return;
    }
    if ((it.kind === 'chat.message' || it.kind === 'call.missed') && it.access && it.url) {           // Chat (1.5): im Chat-Tab oeffnen
      if (location.pathname === '/chats') { location.hash = it.url.split('#')[1] || ''; return; }
      var cw = window.open(it.url, 'hk-chats'); if (cw) cw.focus(); return;
    }
    if (!it.access || !it.note) { HK.toast(T('Kein Zugriff mehr')); return; }
    var k = K();
    if (!k) { var w = window.open(it.url || '/notes#' + it.note.id, 'hk-notes'); if (w) w.focus(); return; }
    var show = function () {
      if (it.block_id) k.showBlock(it.block_id);
      if (it.comment_id && window.HKComments) HKComments.openBlock(it.block_id || null);
    };
    if (k.cur() && k.cur().id === it.note.id) show();
    else k.open(it.note.id).then(function () { setTimeout(show, 350); });
  }

  // Dashboard: Zahl am Knopf "Seiten" (Seiten mit neuen Kommentaren)
  function pagesBadge(n) {
    var a = document.querySelector('.btn-notes'); if (!a) return;
    var b = a.querySelector('.btn-badge');
    if (!n) { if (b) b.remove(); return; }
    if (!b) { b = el('span', 'btn-badge'); a.appendChild(b); }
    b.textContent = '💬 ' + n; b.title = T('Neue Kommentare ({n})', { n: n });
  }
  var pbT = null;
  function pagesBadgeSoon() { if (NOTES) return; clearTimeout(pbT); pbT = setTimeout(function () { count().catch(function () {}); }, 1500); }

  // =========================================================================================================
  // Live-Kanal (SSE) mit Rueckfall auf Abfrage
  // =========================================================================================================
  var L = { es: null, hello: false, down: false, fails: 0, pollT: null, retryT: null, polls: 0, stopped: false };
  function parse(e) { try { return JSON.parse(e.data || '{}'); } catch (x) { return {}; } }
  function on(es, name, fn) {
    es.addEventListener(name, function (e) {
      if (e.lastEventId) L.lastId = e.lastEventId;                       // fuer den Neuaufbau (?last=, Nachholen)
      try { fn(parse(e)); } catch (x) { if (window.console) console.warn('live', name, x); }
    });
  }
  function connect() {
    clearTimeout(L.retryT);
    if (L.stopped) return;
    if (!window.EventSource) { startPoll(); return; }
    if (L.es) { L.es.close(); L.es = null; }
    var qs = (HK.tab ? '&tab=' + encodeURIComponent(HK.tab) : '') + (L.lastId ? '&last=' + encodeURIComponent(L.lastId) : '');
    var es = new EventSource('/api/events' + (qs ? '?' + qs.slice(1) : '')); L.es = es; L.hello = false;
    var helloT = setTimeout(function () { if (!L.hello && L.es === es) fallback('timeout'); }, 15000);   // Proxy puffert?
    on(es, 'hello', function () {
      clearTimeout(helloT); L.hello = true; L.fails = 0; stopPoll();
      if (L.first) { L.first = false; L.down = false; count().catch(function () {}); }
      else if (L.down) { L.down = false; resync(); }
      // Anwesenheit neu melden: wer kam, waehrend der Kanal noch aufgebaut wurde, erscheint sonst erst beim naechsten Ping
      if (NOTES && P.id) ping();
    });
    on(es, 'reset', resync);
    on(es, 'notification', onNotification);
    on(es, 'note.changed', function (d) {
      var k = K(); if (k) k.remote(d);
      try { document.dispatchEvent(new CustomEvent('hk-note-changed', { detail: d })); } catch (e) { /* alter Browser */ }
    });
    on(es, 'comments.changed', onComments);
    on(es, 'shared.changed', onShared);
    // (2.2) Seitenbaum anderswo geaendert (anderer Tab/Geraet, Freigabe durch andere): Baum nachladen (entprellt)
    on(es, 'notes.tree', function (d) { var k = K(); if (k && k.treeChanged) k.treeChanged(d); });
    on(es, 'presence', function (d) { if (d.note_id === P.id) drawPresence(d.users || []); });
    // Chat (1.5): an die Chat-Seite weiterreichen (Ereignis 'hk-live'), Zahl am Knopf "Chats" aktualisieren
    // Anrufe (2.1, static/calls.js): call.ring / call.signal / call.state ebenso ueber 'hk-live'
    // 2.4 (E4/E5): call.roster (Teilnehmerliste) und call.invite (Gruppenanruf klingelt)
    ['chat.message', 'chat.deleted', 'chat.read', 'chat.typing', 'chat.changed', 'chat.link', 'chat.device', 'call.ring', 'call.signal',
     'call.state', 'call.roster', 'call.invite', 'avatar.changed', 'ai.persona.changed', 'chat.cleared', 'chat.removed'].forEach(function (n) {   // (2.1) Loeschen
      on(es, n, function (d) { chatEvent(n, d); });
    });
    on(es, 'avatar.changed', swapAvatar);
    on(es, 'bye', function (d) {
      es.close(); if (L.es === es) L.es = null;
      if (d.reason === 'account') { L.stopped = true; stopPoll(); return; }
      // refresh: Verbindung ist eine Stunde alt (Sitzung wird beim Neuaufbau frisch geprueft) - gleich wieder verbinden.
      // auth (z. B. Passwort in diesem Tab geaendert, neue Sitzung): neu versuchen; ist die Sitzung wirklich weg, endet
      // es mit 401 -> Abfrage -> 401 -> Schluss.
      if (d.reason === 'refresh') { L.down = true; L.retryT = setTimeout(connect, 300); return; }
      fallback(d.reason === 'limit' ? 'limit' : 'closed');
    });
    es.onerror = function () {
      if (L.es !== es) return;
      L.down = true; L.fails++;
      // CLOSED: Server hat abgelehnt (401/503) - kein automatischer Neuaufbau. Sonst baut der Browser selbst neu auf.
      if (es.readyState === 2 || (!L.hello && L.fails >= 3)) fallback('error');
    };
  }
  function fallback(why) {
    if (L.es) { L.es.close(); L.es = null; }
    L.down = true;
    startPoll();
    clearTimeout(L.retryT);
    L.retryT = setTimeout(connect, why === 'limit' ? 10 * 60000 : why === 'closed' ? 5000 : 5 * 60000);   // spaeter wieder mit SSE versuchen
  }
  function startPoll() {
    if (L.pollT || L.stopped) return;
    L.pollT = setInterval(pollOnce, POLL);
    pollOnce();
  }
  function stopPoll() { if (L.pollT) { clearInterval(L.pollT); L.pollT = null; } }
  function pollOnce() {
    if (document.hidden) return;
    L.polls++;
    chatsBadgeSoon();
    count().then(function (grew) {
      if (grew && N.open) load();
      var k = K();
      if (k && (grew || L.polls % 3 === 0)) { k.reloadList(); if (window.HKComments && k.cur()) HKComments.live(); }
    }).catch(function (err) { if (err.status === 401) { L.stopped = true; stopPoll(); } });
  }
  // Neues/entferntes Profilbild einer Person (2.1, Bughunt 2): alle ihre Bilder auf der Seite austauschen - die Adresse
  // ohne Version (path) ist je Person fest, die Groesse (s=) bleibt
  function swapAvatar(d) {
    if (!d || !d.path) return;
    [].forEach.call(document.querySelectorAll('img.hk-av-img'), function (img) {
      var src = img.getAttribute('src') || '', i = src.indexOf('?');
      if ((i < 0 ? src : src.slice(0, i)) !== d.path) return;
      var box = img.parentNode, s = /[?&]s=(\d+)/.exec(src);
      if (!d.avatar) { img.remove(); if (box) box.classList.remove('hk-av-on'); return; }
      img.src = d.avatar + (d.avatar.indexOf('?') < 0 ? '?' : '&') + 's=' + (s ? s[1] : 64);
    });
  }
  function chatEvent(n, d) {
    document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: n, data: d } }));
    if (n === 'chat.message' || n === 'chat.read' || n === 'chat.deleted' || n === 'chat.changed' || n === 'chat.cleared' || n === 'chat.removed') chatsBadgeSoon();
  }
  var cbT = null;
  function chatsBadgeSoon() {
    var a = document.getElementById('hk-chats'); if (!a) return;
    clearTimeout(cbT);
    cbT = setTimeout(function () {
      api('GET', '/api/chats/unread').then(function (d) {
        var b = a.querySelector('.btn-badge');
        if (!d.unread) { if (b) b.remove(); return; }
        if (!b) { b = el('span', 'btn-badge'); a.appendChild(b); }
        b.textContent = String(d.unread); b.title = T('{n} ungelesene Nachrichten', { n: d.unread });
      }).catch(function () {});
    }, 800);
  }
  // Nach Unterbrechung/Neustart des Servers: alles abgleichen
  function resync() {
    document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'resync', data: {} } }));
    chatsBadgeSoon();
    count().then(function () { if (N.open) load(); }).catch(function () {});
    var k = K();
    if (k) { k.reloadList(); if (window.HKComments && k.cur()) HKComments.live(); ping(); }
  }

  function onNotification(d) {
    if (d.item) {
      var it = d.item;
      N.items = N.items.filter(function (x) { return x.id !== it.id && x.id !== d.replaces; });
      if (N.loaded) N.items.unshift(it);
      if (!it.read) ring();
      if (it.kind && it.kind.indexOf('comment.') === 0) pagesBadgeSoon();
    } else if (d.read) {
      N.items.forEach(function (x) { if (d.read === 'all' || (d.read.indexOf && d.read.indexOf(x.id) >= 0)) x.read = true; });
    }
    if (d.unread != null) N.unread = d.unread;
    badgeDraw(); draw();
  }
  function onComments(d) {
    var k = K();
    if (!k) { pagesBadgeSoon(); return; }
    var n = d.counts || {};
    k.setUnread(d.note_id, (n.unread || 0) > 0);
    if (k.cur() && k.cur().id === d.note_id && window.HKComments) HKComments.live();
  }
  function onShared() {
    var k = K(); if (!k) return;
    var c = k.cur(), id = c && c.id, role = c && c.role;
    k.reloadList().then(function () {
      // offene fremde Seite: Rolle geaendert oder Zugriff weg -> neu oeffnen (mit ungespeicherten Aenderungen greift
      // beim Speichern der vorhandene Weg "Kein Zugriff mehr" / "Nur noch lesen")
      if (!id || !k.foreign() || k.busy() || !k.cur() || k.cur().id !== id) return;
      var now = k.byId(id);
      if (!now || (now.role && now.role !== role)) k.open(id);
    });
  }

  // =========================================================================================================
  // Anwesenheit (nur Seiten-Ansicht): alle 20 s melden, Avatare im Seitenkopf
  // =========================================================================================================
  var P = { id: null, timer: null, lastInput: 0, sent: null, hiddenAt: 0, users: [] };
  function editingNow() {
    var k = K(), c = k && k.cur(); if (!c) return false;
    var r = c.role || 'owner'; if (r !== 'owner' && r !== 'edit') return false;
    return (k.busy() && Date.now() - P.lastInput < EDIT_MS * 2) || Date.now() - P.lastInput < EDIT_MS;
  }
  function ping() {
    var id = P.id, k = K(); if (!id || !k) return;
    if (document.hidden && Date.now() - P.hiddenAt > 60000) return;          // lange im Hintergrund: Anwesenheit laeuft aus
    var since = Date.now() - (P.at || 0);
    if (P.atId === id && since < 1500) {                                    // gerade erst gemeldet: kurz danach noch einmal
      if (!P.pend) P.pend = setTimeout(function () { P.pend = null; ping(); }, 1600 - since);
      return;
    }
    P.at = Date.now(); P.atId = id;
    var ed = editingNow(); P.sent = ed;
    api('POST', '/api/presence', { note_id: id, editing: ed }).then(function (d) {
      if (P.id !== id || d.skipped) return;
      drawPresence(d.users || []);
      var c = k.cur();                     // ohne Live-Kanal erkennt die Seite fremde Aenderungen hierueber
      if (c && c.id === id && d.rev != null && c.rev != null && d.rev > c.rev) k.remote({ id: id, rev: d.rev, updated: d.updated, by: d.by });
    }).catch(function (err) { if (err.status === 404 && P.id === id) drawPresence([]); });
  }
  function leave(id) {
    if (!id) return;
    try {
      fetch('/api/presence', { method: 'POST', keepalive: true, credentials: 'same-origin',
                               headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ note_id: id, leave: true }) })
        .catch(function () {});
    } catch (e) { /* Seite wird geschlossen */ }
  }
  function drawPresence(users) {
    P.users = users;
    var bar = document.querySelector('#page .page-bar'); if (!bar) return;
    var box = $('pg-presence');
    var others = users.filter(function (u) { return !u.me; });
    if (!others.length) { if (box) box.remove(); return; }
    // unveraendert: nicht neu zeichnen (sonst flackern die Profilbilder bei jeder Meldung kurz zu Initialen)
    var sig = JSON.stringify(others.map(function (u) { return [u.username, u.name, !!u.editing, u.avatar || '']; }));
    if (box && box.dataset.sig === sig) return;
    if (!box) {
      box = el('div', 'lv-pres'); box.id = 'pg-presence'; box.setAttribute('role', 'group');
      var anchor = $('pg-cm-btn') || $('pg-share'); bar.insertBefore(box, anchor);
    }
    box.textContent = ''; box.dataset.sig = sig;
    box.setAttribute('aria-label', T('Gerade auf dieser Seite'));
    others.slice(0, 4).forEach(function (u) {
      var a = avatar(u.name, u.username, u.editing ? 'editing' : '', u.avatar);
      a.removeAttribute('aria-hidden'); a.setAttribute('role', 'img');
      var t = u.editing ? T('{name} bearbeitet gerade', { name: u.name }) : T('{name} ist auch hier', { name: u.name });
      a.title = t; a.setAttribute('aria-label', t);
      box.appendChild(a);
    });
    if (others.length > 4) {
      var rest = others.slice(4).map(function (u) { return u.name; }).join(', ');
      var m = el('span', 'lv-av lv-av-more', '+' + (others.length - 4)); m.title = rest; m.setAttribute('aria-label', rest); box.appendChild(m);
    }
    // auf breiten Bildschirmen: kurzer Text neben den Avataren
    var ed = others.filter(function (u) { return u.editing; });
    var txt = others.length === 1 ? (ed.length ? T('{name} bearbeitet gerade', { name: others[0].name }) : T('{name} ist auch hier', { name: others[0].name }))
      : (ed.length ? T('{n} Personen hier, {m} bearbeiten', { n: others.length, m: ed.length }) : T('{n} Personen sind auch hier', { n: others.length }));
    box.appendChild(el('span', 'lv-pres-t', txt));
  }
  function restart() { clearInterval(P.timer); P.timer = setInterval(ping, PING); }
  if (NOTES) {
    document.addEventListener('hk-page-open', function (e) {
      var id = e.detail && e.detail.id;
      if (P.id && P.id !== id) { leave(P.id); drawPresence([]); }
      P.id = id || null; ping(); restart();          // auch Seiten anderer Saga-Server (negative ID, 2.3: Abo beim Eigentuemer)
    });
    document.addEventListener('input', function (e) {
      var pg = $('page'); if (!pg || !e.target || !pg.contains(e.target)) return;
      var was = P.sent; P.lastInput = Date.now();
      if (!was && editingNow() && !P.soon) { P.soon = setTimeout(function () { P.soon = null; ping(); }, 1500); }   // "bearbeitet gerade" bald zeigen
    }, true);
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) P.hiddenAt = Date.now(); else { ping(); restart(); if (L.down && !L.es && !L.stopped) connect(); }
    });
    window.addEventListener('pagehide', function () { leave(P.id); });
    // Box nach dem Neuzeichnen des Seitenkopfs (Seite gewechselt/neu geladen) wieder anbringen
    document.addEventListener('hk-page-ready', function () { if (P.users.length) drawPresence(P.users); });
  }

  // =========================================================================================================
  // Einstellungen -> Konto: "Benachrichtigungen" je Art (static/settings.js ruft das auf)
  // =========================================================================================================
  function settingsSection(box) {
    var wrap = el('div', 'hk-notify-set'); wrap.id = 'hk-notify-set'; box.appendChild(wrap);
    wrap.appendChild(el('h4', null, T('Benachrichtigungen')));
    wrap.appendChild(el('p', 'hk-hint', T('Erscheinen in Saga unter der Glocke – sofort, solange Saga offen ist.')));
    var list = el('div', 'lv-set-list'), st = el('div', 'hk-status'); st.setAttribute('role', 'status');
    wrap.appendChild(list); wrap.appendChild(st);
    api('GET', '/api/notifications/settings').then(function (s) {
      (s.kinds || []).forEach(function (k) {
        var row = el('label', 'lv-set-row'), sw = el('input', 'hk-switch'); sw.type = 'checkbox'; sw.setAttribute('role', 'switch'); sw.checked = !!k.on;
        row.appendChild(el('span', 'lv-set-t', LABEL[k.kind] || k.kind)); row.appendChild(sw); list.appendChild(row);
        sw.addEventListener('change', function () {
          var b = {}; b[k.kind] = sw.checked; sw.disabled = true; st.textContent = ''; st.className = 'hk-status';
          api('PUT', '/api/notifications/settings', b).then(function () { st.textContent = T('✓ Gespeichert'); st.className = 'hk-status ok'; })
            .catch(function (err) { sw.checked = !sw.checked; st.textContent = err.message; st.className = 'hk-status err'; })
            .then(function () { sw.disabled = false; });
        });
      });
    }).catch(function () { wrap.remove(); });
  }

  window.HKLive = { settingsSection: settingsSection, open: openPanel, state: function () { return { sse: !!(L.es && L.hello), poll: !!L.pollT, unread: N.unread }; } };
  badgeDraw();
  count().catch(function () {});
  // Live-Kanal erst kurz nach dem Laden: die Seite holt zuerst ihre eigenen Daten (und Werkzeuge, die auf "kein Netzverkehr
  // mehr" warten, kommen zur Ruhe); die Zahl an der Glocke gleicht das erste hello noch einmal ab
  L.first = true;
  function later() { setTimeout(connect, 2000); }
  if (document.readyState === 'complete') later(); else window.addEventListener('load', later);
})();
