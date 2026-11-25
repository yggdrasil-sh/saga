// Saga 2.6 (Bugs-DB 2029-5): eigener Status im Browser - Verfuegbar / Beschaeftigt / Nicht stoeren. Vertrag: docs/APP-API.md
// "Eigener Status" (GET/PUT /api/account/status, Live status.changed / status.digest). Hier: Stand des eigenen Status
// (HK.userStatus), Eintrag im Benutzermenue (static/settings.js ruft HK.userStatus.menuItem()), Dialog zum Setzen (Zustand,
// Dauer, eigener Text), Punkt am Kontoknopf, ruhige Meldung der Sammelmeldung und Texte fuer den Status anderer
// (static/chat.js). Die Einstellungen "Erreichbarkeit" stehen in static/push.js. Alle Texte per textContent.
(function () {
  'use strict';
  var HK = window.HK; if (!HK) return;
  var el = HK.el, api = HK.api, T = window.T;
  var embed = document.body.classList.contains('embed');
  var NAMES = { available: T('Verfügbar'), busy: T('Beschäftigt'), dnd: T('Nicht stören'), in_call: T('Im Anruf') };
  var STATES = ['available', 'busy', 'dnd'];
  var V = null, feat = null, skew = 0, subs = [], quietTo = null, quietAt = 0;
  var fT = new Intl.DateTimeFormat(HK.locale, { hour: 'numeric', minute: '2-digit' });
  var fWd = new Intl.DateTimeFormat(HK.locale, { weekday: 'short' });
  var fD = new Intl.DateTimeFormat(HK.locale, { day: 'numeric', month: 'numeric' });

  function now() { return Date.now() / 1000 + skew; }
  function day0(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }
  // Ende als kurzer Text: heute "14:30", morgen "morgen 7:00", diese Woche "Fr. 14:30", sonst "24.12., 14:30"
  function when(ts) {
    var d = new Date(ts * 1000), n = new Date(Date.now()), dd = Math.round((day0(d) - day0(n)) / 86400000);
    if (dd <= 0) return fT.format(d);
    if (dd === 1) return T('morgen {time}', { time: fT.format(d) });
    if (dd < 7) return fWd.format(d) + ' ' + fT.format(d);
    return fD.format(d) + ', ' + fT.format(d);
  }
  function until(ts) { return ts ? T('bis {when}', { when: when(ts) }) : ''; }

  // --- Stand -----------------------------------------------------------------------------------------------------
  function take(v) {
    if (!v || typeof v.ver !== 'number' || STATES.indexOf(v.state) < 0) return false;
    if (V && v.ver < V.ver) return false;                          // aelterer Stand (Antworten ueberholt)
    V = v;
    if (typeof v.now === 'number') skew = v.now - Date.now() / 1000;
    if (v.quiet && Date.now() - quietAt > 300000) quietLoad();
    drawMe();
    plan();
    subs.slice().forEach(function (fn) { try { fn(V); } catch (e) { /* egal */ } });
    return true;
  }
  // Ende der Ruhezeit (Mitteilungs-Einstellungen) fuer "Ruhezeit bis 7:00" - die Ansicht nennt nur quiet: true
  function quietLoad() {
    quietAt = Date.now();
    api('GET', '/api/push').then(function (d) {
      var q = d && d.settings && d.settings.quiet;
      quietTo = q && q.on && /^\d\d:\d\d$/.test(q.to || '') ? q.to : null;
      drawMe(); subs.slice().forEach(function (fn) { try { fn(V); } catch (e) { /* egal */ } });
    }).catch(function () { quietTo = null; });
  }
  var planT = 0;
  function plan() {                                                // Ablauf, Ruhezeit und Anruf aendern effective ohne Ereignis an uns
    clearTimeout(planT);
    var ms = 300000;
    if (V && V.until) ms = Math.max(2000, Math.min(ms, (V.until - now()) * 1000 + 2500));
    planT = setTimeout(function () { if (!document.hidden) load(); else plan(); }, ms);
  }
  function load() {
    if (feat === false) return Promise.resolve(null);
    var f = feat ? Promise.resolve(true) : api('GET', '/api/version').then(function (v) {
      feat = (v.features || []).indexOf('userstatus1') >= 0; return feat;
    });
    return f.then(function (ok) {
      if (!ok) return null;
      return api('GET', '/api/account/status').then(function (v) { take(v); return V; });
    }).catch(function () { plan(); return null; });
  }
  // PUT mit ver; 409 = anderswo geaendert: Ansicht uebernehmen und still neu zeigen (Ergebnis {conflict: true})
  function put(url, body) {
    return api('PUT', url, body).then(function (v) { take(v); return { view: V }; }, function (err) {
      if (err.status === 409 && err.data && take(err.data)) return { view: V, conflict: true };
      throw err;
    });
  }
  function set(b) { b = Object.assign({ source: 'manual' }, b); if (V) b.ver = V.ver; return put('/api/account/status', b); }
  function prefs(b) { return put('/api/account/status/prefs', b); }

  // Was gerade wirkt (ehrlich): {state, text} - state = effective, text = Zusatz ("bis 14:30", "Ruhezeit bis 7:00 – ...")
  function shown(v) {
    v = v || V; if (!v) return null;
    var eff = v.effective || v.state, out = { state: eff, hint: '' };
    if (eff === 'dnd' && v.why === 'quiet') out.hint = quietTo ? T('Ruhezeit bis {time} – zählt als Nicht stören', { time: fT.format(new Date('2000-01-01T' + quietTo + ':00')) })
                                                              : T('Ruhezeit – zählt als Nicht stören');
    else if (eff === 'in_call') out.hint = v.state !== 'available' ? T('darunter: {state}', { state: NAMES[v.state] }) : '';
    else if (v.until && v.state === eff) out.hint = until(v.until);
    if (v.source === 'focus' && v.state !== 'available' && v.why !== 'quiet') out.hint = [out.hint, T('vom Gerät (Fokus)')].filter(Boolean).join(' · ');
    return out;
  }
  function dot(state, cls) {
    var d = el('span', 'us-dot us-' + (state || 'available') + (cls ? ' ' + cls : '')); d.setAttribute('aria-hidden', 'true'); return d;
  }
  // Punkt am Kontoknopf (nur, wenn nicht verfuegbar)
  function drawMe() {
    var b = document.getElementById('hk-user'); if (!b) return;
    var s = shown(), d = b.querySelector('.us-me');
    if (!s || s.state === 'available') { if (d) d.remove(); return; }
    if (!d) { d = dot(s.state, 'us-me'); b.insertBefore(d, b.firstChild); }
    d.className = 'us-dot us-me us-' + s.state;
    d.title = NAMES[s.state] + (s.hint ? ' · ' + s.hint : '');
  }

  // --- Benutzermenue: Eintrag mit Punkt + Zustand ---------------------------------------------------------------------
  function menuItem() {
    if (!V) return null;
    var s = shown();
    var it = HK.menuItem(NAMES[s.state], function () { dialog(); }, { icon: ' ', hint: s.hint, hintBelow: !!s.hint });
    it.classList.add('us-mi');
    var ic = it.querySelector('.mi-ic'); if (ic) { ic.textContent = ''; ic.appendChild(dot(s.state)); }
    it.setAttribute('aria-label', T('Status: {state}', { state: NAMES[s.state] }) + (s.hint ? ', ' + s.hint : '') + ' – ' + T('ändern'));
    return it;
  }

  // --- Dialog: Zustand, Dauer, eigener Text ---------------------------------------------------------------------------
  var dlg = null, pf = null;
  function close() {
    if (!dlg) return;
    dlg.remove(); dlg = null; document.removeEventListener('keydown', onKey, true);
    if (pf) { pf.release(); pf = null; }
  }
  function onKey(e) {
    if (e.key === 'Escape' && dlg && !(HK.menuOpen && HK.menuOpen())) { e.preventDefault(); e.stopPropagation(); close(); }
  }
  function tomorrow7() {
    var d = new Date(Date.now()), early = d.getHours() < 5;
    var t = new Date(d.getFullYear(), d.getMonth(), d.getDate() + (early ? 0 : 1), 7, 0, 0, 0);
    return { ts: t.getTime() / 1000, label: early ? T('Bis 7:00') : T('Bis morgen 7:00') };
  }
  function radio(name, value, label, checked, extra) {
    var l = el('label', 'us-opt'), i = el('input'); i.type = 'radio'; i.name = name; i.value = value; i.checked = !!checked;
    l.appendChild(i);
    if (extra) l.appendChild(extra);
    l.appendChild(el('span', null, label));
    return l;
  }
  function dialog(o) {
    o = o || {};
    if (!V) return;
    if (HK.menuOpen && HK.menuOpen()) HK.closeMenu();
    var back = (HK.lastMenuOwner && HK.lastMenuOwner()) || document.getElementById('hk-user');
    if (dlg) close();
    dlg = el('div', 'hk-modal us-modal');
    var card = el('div', 'hk-dialog us-dlg'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true');
    var head = el('div', 'hk-dlg-head'), h = el('h2', null, T('Mein Status')); h.id = 'us-dlg-t';
    card.setAttribute('aria-labelledby', h.id);
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); x.addEventListener('click', close);
    head.appendChild(h); head.appendChild(x); card.appendChild(head);
    var body = el('div', 'hk-dlg-body us-body'); card.appendChild(body);
    var info = el('p', 'hk-hint us-info'); info.id = 'us-info'; info.setAttribute('role', 'status'); body.appendChild(info);

    var fs1 = el('fieldset', 'us-fs us-states'); fs1.appendChild(el('legend', null, T('Status')));
    STATES.forEach(function (s) { fs1.appendChild(radio('us-state', s, NAMES[s], V.state === s, dot(s))); });
    body.appendChild(fs1);

    var fs2 = el('fieldset', 'us-fs us-durs'); fs2.appendChild(el('legend', null, T('Dauer')));
    var t7 = tomorrow7(), keep = V.until && V.state !== 'available' && V.until > now() + 60;
    var DUR = [['h1', T('1 Stunde')], ['t7', t7.label], ['off', T('Bis ich es ausschalte')]];
    if (keep) DUR.unshift(['keep', T('Bis {when} (wie bisher)', { when: when(V.until) })]);
    DUR.forEach(function (d, i) { fs2.appendChild(radio('us-dur', d[0], d[1], keep ? i === 0 : d[0] === 'off')); });
    body.appendChild(fs2);

    var max = (V.limits && V.limits.text) || 80;
    var tl = el('label', 'us-text'), tlt = el('span', null, T('Eigener Text (optional)'));
    var inp = el('input', 'hk-in'); inp.type = 'text'; inp.maxLength = max; inp.id = 'us-text-in'; inp.autocomplete = 'off';
    inp.placeholder = T('z. B. „Im Kundentermin“');
    inp.value = V.source === 'manual' && V.text ? V.text : '';
    var cnt = el('span', 'us-count'); cnt.id = 'us-count'; cnt.setAttribute('aria-live', 'polite');
    inp.setAttribute('aria-describedby', cnt.id);
    function count() { cnt.textContent = T('{n} von {max} Zeichen', { n: inp.value.length, max: max }); cnt.classList.toggle('us-full', inp.value.length >= max); }
    inp.addEventListener('input', count); count();
    tl.appendChild(tlt); tl.appendChild(inp); body.appendChild(tl); body.appendChild(cnt);

    var st = el('div', 'hk-status'); st.setAttribute('role', 'status');
    var bar = el('div', 'hk-ask-bar us-bar');
    var more = el('button', 'btn us-more', T('Erreichbarkeit …')); more.type = 'button';
    more.addEventListener('click', function () { close(); if (window.HKSettings) HKSettings.open('reach'); });
    var no = el('button', 'btn', T('Abbrechen')); no.type = 'button'; no.addEventListener('click', close);
    var yes = el('button', 'btn btn-primary us-save', T('Speichern')); yes.type = 'button';
    if (window.HKSettings && window.HKPush) bar.appendChild(more);
    bar.appendChild(no); bar.appendChild(yes);
    body.appendChild(st); body.appendChild(bar);

    function cur(n) { var c = card.querySelector('input[name="' + n + '"]:checked'); return c ? c.value : ''; }
    function sync() {                                                 // Verfuegbar: keine Dauer
      var avail = cur('us-state') === 'available';
      [].forEach.call(fs2.querySelectorAll('input'), function (i) { i.disabled = avail; });
      fs2.classList.toggle('us-off', avail);
      var s = shown();
      info.textContent = T('Jetzt: {state}', { state: NAMES[s.state] }) + (s.hint ? ' · ' + s.hint : '');
    }
    fs1.addEventListener('change', sync); sync();
    yes.addEventListener('click', function () {
      var s = cur('us-state'), d = cur('us-dur'), b = { state: s, text: inp.value.trim() || null };
      if (s !== 'available') {
        if (d === 'h1') b.dur = 3600;
        else if (d === 't7') b.until = tomorrow7().ts;
        else if (d === 'keep' && V.until) b.until = V.until;
      }
      yes.disabled = true; st.textContent = ''; st.className = 'hk-status';
      set(b).then(function (r) {
        if (r.conflict) { var f = document.activeElement === yes; dialog(); if (f && dlg) dlg.querySelector('.us-save').focus(); return; }   // still neu zeigen
        close();
      }).catch(function (err) { yes.disabled = false; st.textContent = err.message; st.className = 'hk-status err'; });
    });
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); yes.click(); } });

    dlg.appendChild(card);
    dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) close(); });
    document.body.appendChild(dlg);
    document.addEventListener('keydown', onKey, true);
    var first = card.querySelector('input[name="us-state"]:checked') || card.querySelector('input[name="us-state"]');
    pf = HK.panelFocus(card, { trap: true, initial: first, back: o.back || back || undefined });
    if (first) first.focus();
  }

  // --- Status anderer (static/chat.js): {s, until?, text?} -> Text / Punkt ------------------------------------------
  function peerText(st, withText) {
    if (!st || !NAMES[st.s] || st.s === 'available') return '';             // Verfuegbar: nichts Zusaetzliches
    var s = NAMES[st.s] + (st.until && st.s !== 'in_call' ? ' ' + until(st.until) : '');
    return withText && st.text ? s + ' · ' + st.text : s;
  }

  // --- Live --------------------------------------------------------------------------------------------------------
  var callT = 0;
  document.addEventListener('hk-live', function (e) {
    var t = e.detail && e.detail.type, d = (e.detail && e.detail.data) || {};
    if (t === 'status.changed') take(d);
    else if (t === 'status.digest') digest(d);
    else if (t === 'live.hello' || t === 'resync') load();
    else if (t === 'call.state' && V) { clearTimeout(callT); callT = setTimeout(load, 700); }     // "Im Anruf" kommt ohne Ereignis
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden && V && Date.now() / 1000 + skew > (V.now || 0) + 120) load(); });
  // Sammelmeldung nach "Nicht stoeren": ruhige Meldung, nur Zahlen (nie Namen)
  function digest(d) {
    if (embed) return;
    var parts = [];
    if (d.messages > 0) parts.push(T('{n} {n:Nachricht|Nachrichten}', { n: d.messages }));
    if (d.calls > 0) parts.push(T('{n} {n:verpasster Anruf|verpasste Anrufe}', { n: d.calls }));
    if (d.other > 0) parts.push(T('{n} {n:weitere Mitteilung|weitere Mitteilungen}', { n: d.other }));
    if (!parts.length) return;
    var s = parts[0];
    for (var i = 1; i < parts.length; i++) s = T('{a}, {b}##Aufzählung', { a: s, b: parts[i] });
    HK.toast(T('Während „Nicht stören“: {items}', { items: s }), null, null, 9000);
  }

  HK.userStatus = {
    view: function () { return V; }, ok: function () { return !!V; }, load: load, set: set, prefs: prefs,
    on: function (fn) { subs.push(fn); return function () { subs = subs.filter(function (x) { return x !== fn; }); }; },
    dnd: function () { return !!(V && V.effective === 'dnd'); },
    name: function (s) { return NAMES[s] || ''; }, until: until, shown: shown, dot: dot, peerText: peerText,
    menuItem: menuItem, open: dialog, close: close
  };
  if (document.getElementById('hk-user')) load();
})();
