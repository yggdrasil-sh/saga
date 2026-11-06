// Saga 2.5 - Chats im Seitenpanel rechts (neben Seiten und "Heute"): Reiter "Marin | Chat" zusammen mit dem
// KI-Assistenten, schnelles Antworten aus Benachrichtigungen und "Seite in Chat teilen".
// Die Chats sind Ende-zu-Ende-verschluesselt - Lesen und Schreiben geht nur mit den Schluesseln im Browser. Darum
// laedt das Panel die echte Chat-Ansicht (/chats?panel=1, gleiche Herkunft, kompakt) in einem iframe; dieses Skript
// steuert sie nur von aussen (Unterhaltung waehlen ueber den Hash, Teilen/Fokus per postMessage). Auf schmalen
// Bildschirmen (Handy) oeffnet sich stattdessen /chats.
(function () {
  'use strict';
  var HK = window.HK, T = (HK && HK.t) || function (s) { return s; };
  if (!HK || document.body.classList.contains('embed') || window.HKChatDock) return;
  var body = document.body, narrow = window.matchMedia('(max-width: 760px)');
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  var D = { frame: null, loaded: false, queue: [], unread: 0, hash: '' };

  // --- Knopf unten rechts (neben dem KI-Knopf) --------------------------------------------------------------------
  var btn = el('button', 'cd-btn'); btn.type = 'button'; btn.title = T('Chats'); btn.setAttribute('aria-label', T('Chats öffnen'));
  btn.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.4-4.3A7.5 7.5 0 1 1 20 11.5z"/></svg>';
  var badge = el('span', 'cd-badge'); badge.hidden = true; btn.appendChild(badge);
  btn.addEventListener('click', function () { open(D.hash || '', {}); });
  body.appendChild(btn);
  function aiBtnPlace() { body.classList.toggle('cd-with-ai', !!document.querySelector('.ai-btn:not([hidden])')); }
  [0, 900, 2500].forEach(function (ms) { setTimeout(aiBtnPlace, ms); });

  // --- Panel --------------------------------------------------------------------------------------------------------
  var panel = el('aside', 'cd-panel'); panel.id = 'cd-panel'; panel.hidden = true;
  panel.setAttribute('role', 'complementary'); panel.setAttribute('aria-label', T('Chats'));
  var head = el('div', 'cd-head'), sw = el('div', 'cd-sw-slot'), spacer = el('span', 'cd-spacer');
  var full = el('a', 'icon-btn cd-full', '↗'); full.href = '/chats'; full.title = T('In der Chat-Ansicht öffnen'); full.setAttribute('aria-label', full.title);
  full.addEventListener('click', function (e) {
    e.preventDefault(); var h = ''; try { h = D.frame ? D.frame.contentWindow.location.hash : ''; } catch (x) { h = ''; }
    location.href = '/chats' + (h || '');
  });
  var close = el('button', 'icon-btn cd-close', '×'); close.type = 'button'; close.setAttribute('aria-label', T('Chats schließen'));
  close.addEventListener('click', function () { hide(); });
  head.appendChild(sw); head.appendChild(spacer); head.appendChild(full); head.appendChild(close);
  var frameBox = el('div', 'cd-body');
  panel.appendChild(head); panel.appendChild(frameBox);
  body.appendChild(panel);

  // Reiter "Marin | Chat" - in beiden Panels gleich, nur wenn der KI-Assistent an ist
  function hasAI() { return !!(window.HKAI && window.HKAI.state && window.HKAI.state()); }
  function aiName() { var s = hasAI() && window.HKAI.state(); return (s && s.persona && s.persona.name) || T('Saga KI'); }
  function switcher(active) {
    var w = el('div', 'cd-sw'); w.setAttribute('role', 'tablist');
    [['ai', '✦ ' + aiName()], ['chat', '💬 ' + T('Chat')]].forEach(function (x) {
      var b = el('button', 'cd-sw-b' + (x[0] === active ? ' on' : ''), x[1]); b.type = 'button'; b.setAttribute('role', 'tab');
      b.setAttribute('aria-selected', x[0] === active ? 'true' : 'false');
      b.addEventListener('click', function () {
        if (x[0] === active) return;
        if (x[0] === 'ai') { hide(); window.HKAI.open(); } else { aiClose(); open(D.hash || '', {}); }
      });
      w.appendChild(b);
    });
    return w;
  }
  function drawSwitch() {
    sw.textContent = '';
    if (hasAI()) sw.appendChild(switcher('chat')); else sw.appendChild(el('h2', 'cd-title', T('Chats')));
  }
  function aiPanelSwitch() {                                       // Reiterleiste oben im KI-Panel
    var ap = document.getElementById('ai-panel'); if (!ap || !hasAI()) return;
    var old = ap.querySelector(':scope > .cd-sw'); if (old) old.remove();
    ap.insertBefore(switcher('ai'), ap.firstChild);
  }
  function aiClose() { if (body.classList.contains('ai-open')) { var x = document.querySelector('.ai-close'); if (x) x.click(); } }
  // KI geht auf -> Chat-Panel zu (nur eins rechts); Reiter ins KI-Panel
  new MutationObserver(function () {
    if (body.classList.contains('ai-open')) { aiPanelSwitch(); if (!panel.hidden) hide(); }
  }).observe(body, { attributes: true, attributeFilter: ['class'] });

  function ensureFrame(hash) {
    if (D.frame) return false;
    D.frame = el('iframe', 'cd-frame'); D.frame.title = T('Chats');
    D.frame.setAttribute('allow', 'clipboard-write; microphone; camera');
    D.frame.addEventListener('load', function () {
      D.loaded = true; var q = D.queue; D.queue = []; q.forEach(post);
    });
    D.frame.src = '/chats?panel=1' + (hash ? '#' + hash : '');
    frameBox.appendChild(D.frame);
    return true;
  }
  function post(m) {
    if (!D.loaded) { D.queue.push(m); return; }
    try { D.frame.contentWindow.postMessage(m, location.origin); } catch (e) { /* Rahmen weg */ }
  }
  function open(hash, opts) {
    opts = opts || {};
    if (narrow.matches) {                                          // Handy: volle Chat-Ansicht
      var q = opts.share ? '?share=' + encodeURIComponent(opts.share.id) + '&title=' + encodeURIComponent(opts.share.title || '') : '';
      location.href = '/chats' + q + (hash ? '#' + hash : ''); return;
    }
    aiClose();
    drawSwitch();
    if (hash) D.hash = hash;
    var fresh = ensureFrame(hash);
    if (!fresh && hash) {
      try { var w = D.frame.contentWindow; if (w.location.hash !== '#' + hash) w.location.hash = hash; } catch (e) { /* egal */ }
    }
    var was = !panel.hidden;
    panel.hidden = false; body.classList.add('cd-open'); btn.setAttribute('aria-expanded', 'true');
    if (!was) { D.pf = HK.panelFocus(panel, { initial: false, back: btn }); opts.focus = true; }   // A11Y-32: Fokus ins Eingabefeld, zurueck an den Knopf
    if (opts.share) post({ t: 'saga-share', note: opts.share });
    if (opts.focus) setTimeout(function () { post({ t: 'saga-focus' }); try { D.frame.focus(); } catch (e) { /* egal */ } }, fresh ? 1500 : 250);
  }
  function hide() {
    panel.hidden = true; body.classList.remove('cd-open'); btn.setAttribute('aria-expanded', 'false');
    if (D.pf) { D.pf.release(); D.pf = null; }
    try { var h = D.frame && D.frame.contentWindow.location.hash; D.hash = h ? h.slice(1) : D.hash; } catch (e) { /* egal */ }
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden && !document.querySelector('.hk-ask') && !(HK.menuOpen && HK.menuOpen())) hide();
  });

  // Links aus dem Panel (Seiten, Heute) im Hauptfenster oeffnen - Seiten ohne Neuladen, wenn /notes offen ist
  window.addEventListener('message', function (e) {
    if (e.origin === location.origin && D.frame && e.source === D.frame.contentWindow && e.data && e.data.t === 'saga-close') { hide(); return; }
    if (e.origin !== location.origin || !D.frame || e.source !== D.frame.contentWindow || !e.data || e.data.t !== 'saga-open') return;
    var href = String(e.data.href || ''); if (!/^\/(?![\/\\])/.test(href) || /[\\\s]/.test(href)) return;   // nur Pfade dieser Saga
    var m = /^\/notes#(\d+)$/.exec(href), K = window.HKPages && window.HKPages.k;
    if (m && K && location.pathname === '/notes') { K.open(+m[1]); return; }
    location.href = href;
  });

  // --- Ungelesen am Knopf -------------------------------------------------------------------------------------------
  var uT = 0;
  function unread() {
    HK.api('GET', '/api/chats/unread').then(function (d) {
      D.unread = (d && d.unread) || 0; badge.hidden = !D.unread; badge.textContent = D.unread > 99 ? '99+' : String(D.unread);
      btn.title = D.unread ? T('{n} ungelesene Nachrichten', { n: D.unread }) : T('Chats');
    }).catch(function () { /* still */ });
  }
  document.addEventListener('hk-live', function (e) {
    var t = e && e.detail && e.detail.type;
    if (t && (/^chat\./.test(t) || t === 'resync')) { clearTimeout(uT); uT = setTimeout(unread, 700); }
  });
  unread();

  window.HKChatDock = {
    ok: function () { return !narrow.matches; },
    open: function (hash, opts) { open(String(hash || '').replace(/^#/, ''), opts); },
    share: function (note) { open('', { share: note }); },
    showing: function (hash) {                                     // diese Unterhaltung ist gerade im Panel zu sehen
      if (panel.hidden || !D.frame) return false;
      try { return D.frame.contentWindow.location.hash === '#' + String(hash || '').replace(/^#/, ''); } catch (e) { return false; }
    },
    close: hide
  };
})();
