// Saga 2.5 - Anruf-Huelle: ein Anruf (static/calls.js) lebt im Dokument der Chat-Seite - dort liegen die Chat-Schluessel
// und die WebRTC-Verbindungen. Ein Wechsel zu einer anderen Saga-Seite wuerde das Dokument und damit den Anruf beenden.
// Solange ein Anruf laeuft oder aufgebaut wird, wird die Chat-Seite darum zur Huelle: Saga-Seiten (gleiche Herkunft,
// feste Pfade, siehe HK.shellTarget in common.js) oeffnen sich in einem bildschirmfuellenden Rahmen, die Anruf-Oberflaeche
// (Leiste, Vollbild, Bild-im-Bild) bleibt darueber bedienbar. Adresszeile und Titel folgen der Seite im Rahmen,
// Zurueck/Vor gehen ueber die gemeinsame Verlaufsliste des Browsers. Chats zeigt die Huelle selbst (Rahmen ausblenden).
// Endet der Anruf, bleibt die Seite im Rahmen stehen (kein Neuladen - Entwuerfe bleiben); der naechste Wechsel aus dem
// Rahmen geht wieder ganz normal im obersten Fenster (common.js). Was sich nicht abfangen laesst, faengt der
// beforeunload-Dialog in calls.js. Der Server erlaubt das Einbetten nur fuer Rahmen gleicher Herkunft (saga.core.SecHeaders).
(function () {
  'use strict';
  var HK = window.HK, T = (HK && HK.t) || function (s) { return s; };
  if (!HK || window.HKShell || !HK.shellTarget || document.body.classList.contains('embed') || location.pathname !== '/chats' || (HK.shell && HK.shell())) return;
  var body = document.body, F = null, on = false, title0 = document.title, syncT = 0;
  function busy() { return !!(window.HKCalls && window.HKCalls.busy && window.HKCalls.busy()); }
  function rel(l) { return l.pathname + l.search + l.hash; }
  function isChats(t) { return /^\/chats(?:[?#]|$)/.test(t); }   // ?panel=1 laesst HK.shellTarget gar nicht durch
  function hashOf(t) { var i = t.indexOf('#'); return i >= 0 ? t.slice(i) : ''; }
  function routeChats() {                                   // chat.js/calls.js: Unterhaltung aus dem Hash oeffnen
    if (!location.hash) return;
    var ev; try { ev = new HashChangeEvent('hashchange'); } catch (e) { ev = document.createEvent('Event'); ev.initEvent('hashchange', false, false); }
    window.dispatchEvent(ev);
  }

  function frame() {
    if (F) return F;
    F = document.createElement('iframe'); F.id = 'hk-shell-frame'; F.className = 'hk-shell-frame'; F.hidden = true;
    F.title = T('Saga-Seite während des Anrufs');
    F.setAttribute('allow', 'clipboard-write; microphone; camera; display-capture; fullscreen; autoplay; picture-in-picture');
    F.addEventListener('load', loaded);
    body.appendChild(F);
    return F;
  }
  function loaded() {
    var w = F && F.contentWindow, href;
    try { href = w.location.href; } catch (e) { href = null; }     // fremde Herkunft bzw. Fehlerseite (Einbetten verweigert)
    if (href === 'about:blank') return;
    if (href === null) { failed(); return; }
    try { w.addEventListener('hashchange', sync); w.addEventListener('popstate', sync); } catch (e) { /* egal */ }
    sync();
  }
  function failed() {
    HK.toast(T('Diese Seite lässt sich während des Anrufs nicht hier öffnen.'));
    if (F) { F.remove(); F = null; }
    if (on) { show(false); history.replaceState(null, '', '/chats'); }
  }
  // Adresszeile und Titel der Seite im Rahmen nachziehen (Laden, Hash, eigene pushState/replaceState der Seiten)
  function sync() {
    if (!on || !F) return;
    var w = F.contentWindow, l, t;
    try { l = w.location; if (l.protocol === 'about:') return; t = w.document.title; } catch (e) { return; }
    if (l.origin !== location.origin) return;
    var u = rel(l);
    if (u !== rel(location)) history.replaceState({ hkShell: 1 }, '', u);
    else if (!(history.state && history.state.hkShell)) history.replaceState({ hkShell: 1 }, '', u);
    if (t && document.title !== t) document.title = t;
  }
  function show(v) {
    if (v && !on) title0 = document.title;
    on = v;
    body.classList.toggle('hk-shell-on', v);
    if (F) F.hidden = !v;
    clearInterval(syncT);
    if (v) { syncT = setInterval(sync, 700); sync(); }
    else document.title = title0;
  }
  function open(t) {
    var fresh = !F; frame();
    if (fresh) F.src = t;
    else { try { F.contentWindow.location.replace(t); } catch (e) { F.src = t; } }
    var was = on;
    show(true);
    if (was) history.replaceState({ hkShell: 1 }, '', t); else history.pushState({ hkShell: 1 }, '', t);
    try { F.focus(); } catch (e) { /* egal */ }
  }
  // Chats: Huelle zeigen. drop = die Chat-Seite wurde im Rahmen geladen (Rahmen verwerfen, kein neuer Verlaufseintrag)
  function home(t, drop) {
    if (drop && F) { F.remove(); F = null; }
    var u = '/chats' + hashOf(t || '');
    if (on) show(false);
    if (drop) history.replaceState(null, '', u);
    else if (u !== rel(location)) history.pushState(null, '', u);
    routeChats();
    // /chats?share=ID&title=… (Handy: "Seite in Chat teilen" aus einer Seite im Rahmen)
    var q = new URLSearchParams((/\?([^#]*)/.exec(t || '') || [])[1] || ''), sid = +q.get('share');
    if (sid > 0 && window.HKChat && window.HKChat.share) window.HKChat.share({ id: sid, title: q.get('title') || '' });
  }
  function take(t) {
    if (!t) return false;
    if (isChats(t)) {
      if (!on && !busy()) return false;                     // ohne Anruf wie immer (Hash-Wechsel bzw. Laden)
      home(t); return true;
    }
    if (!busy()) return false;                              // kein Anruf: normal laden
    open(t); return true;
  }

  // Links in der Huelle (Seitenleiste, Kopf, Tab-Leiste, Mitteilungen): nur gewoehnliche Klicks - Strg/Cmd/Umschalt/
  // Mittelklick, download und target (ausser _self/_top) bleiben beim Browser. Wer selbst preventDefault ruft, gewinnt.
  window.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest && e.target.closest('a[href]'); if (!a || a.hasAttribute('download')) return;
    var tg = (a.getAttribute('target') || '').toLowerCase();
    if (tg && tg !== '_self' && tg !== '_top' && tg !== '_parent' && tg !== String(window.name || '').toLowerCase()) return;
    var t = HK.shellTarget(a.getAttribute('href'));
    if (t && take(t)) e.preventDefault();
  });
  // Zurueck/Vor: Eintraege der Huelle tragen {hkShell: 1}; die Schritte innerhalb des Rahmens erledigt der Browser
  window.addEventListener('popstate', function (e) {
    var st = e.state;
    if (st && st.hkShell) {
      if (!F) { var t = HK.shellTarget(rel(location)); if (t && !isChats(t)) { frame(); F.src = t; show(true); } return; }
      if (!on) show(true);
      sync();
    } else if (on) show(false);
  });

  window.HKShell = {
    on: function () { return on; },
    busy: busy,
    take: take,
    // aus dem Rahmen (common.js): Chats -> Huelle zeigen (true = erledigt)
    chats: function (t, drop) { t = HK.shellTarget(t); if (!t || !isChats(t)) return false; home(t, !!drop); return true; },
    // aus dem Rahmen: im obersten Fenster laden (nach dem Anruf, Anmeldung) - nur Adressen dieser Saga oder http(s)
    leave: function (url) {
      var u; try { u = new URL(String(url || ''), location.href); } catch (e) { return; }
      if (!/^https?:$/.test(u.protocol)) return;
      location.href = u.href;
    },
    url: function () { try { return F && on ? rel(F.contentWindow.location) : ''; } catch (e) { return ''; } }
  };
})();
