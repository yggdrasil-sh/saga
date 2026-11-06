// Saga - Hell/Dunkel je Browser (Design-Runde 3). Laedt synchron im <head> VOR den Stylesheets (kein Inline-Skript
// wegen CSP script-src 'self'), damit data-theme vor dem ersten Rendern steht (kein Aufblitzen).
// localStorage 'hk-theme' = 'light' | 'dark'; fehlt der Schluessel: Automatisch (folgt dem System, wie bisher).
// <html data-theme="light|dark"> nur bei fester Wahl; die CSS-Regeln aus @media (prefers-color-scheme) gelten
// jeweils auch fuer das erzwungene data-theme (static/style.css, static/chat.css).
// window.SagaTheme: get() -> 'auto'|'light'|'dark', set(modus), dark() -> wirksam dunkel?, toggle().
// Ereignis 'hk-theme' auf window bei jeder wirksamen Aenderung (eigene Wahl, anderer Tab/iframe per 'storage',
// Systemwechsel im Automatik-Modus). Strg/Cmd+Shift+L schaltet zwischen Hell und Dunkel.
(function () {
  'use strict';
  var KEY = 'hk-theme', root = document.documentElement, COL = { dark: '#0f141c', light: '#fdfcfa' };
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function read() { var v = null; try { v = localStorage.getItem(KEY); } catch (e) { /* ohne Speicher: Automatisch */ } return v === 'light' || v === 'dark' ? v : 'auto'; }
  var mode = read();
  function sysDark() { return !!(mq && mq.matches); }
  function dark() { return mode === 'auto' ? sysDark() : mode === 'dark'; }
  function metas() {                                  // Browserleiste: fest gewaehlt -> beide theme-color-Eintraege gleich
    var ms = document.querySelectorAll('meta[name="theme-color"]');
    for (var i = 0; i < ms.length; i++) {
      var m = ms[i];
      if (m.dataset.hkOrig == null) m.dataset.hkOrig = m.getAttribute('content') || '';
      m.setAttribute('content', mode === 'auto' ? m.dataset.hkOrig : COL[mode]);
    }
  }
  function apply() {
    if (mode === 'auto') root.removeAttribute('data-theme'); else root.setAttribute('data-theme', mode);
    metas();
  }
  function fire() { try { window.dispatchEvent(new CustomEvent('hk-theme', { detail: { mode: mode, dark: dark() } })); } catch (e) { /* alt */ } }
  function set(m) {
    m = m === 'light' || m === 'dark' ? m : 'auto';
    try { if (m === 'auto') localStorage.removeItem(KEY); else localStorage.setItem(KEY, m); } catch (e) { /* nur fuer diese Seite */ }
    if (m === mode) return;
    mode = m; apply(); fire();
  }
  function toggle() { set(dark() ? 'light' : 'dark'); return mode; }
  apply();
  document.addEventListener('DOMContentLoaded', metas);   // Metas nach dem Skript (falls vorhanden) mitnehmen
  // Andere Tabs und iframes derselben Origin (Chat-Panel, Peek): 'storage' kommt in allen anderen Dokumenten an
  window.addEventListener('storage', function (e) {
    if (e.key !== KEY && e.key !== null) return;
    var m = read(); if (m === mode) return;
    mode = m; apply(); fire();
  });
  if (mq) {
    var onSys = function () { if (mode === 'auto') fire(); };
    if (mq.addEventListener) mq.addEventListener('change', onSys); else if (mq.addListener) mq.addListener(onSys);
  }
  window.addEventListener('keydown', function (e) {
    if (!e.shiftKey || e.altKey || !(e.ctrlKey || e.metaKey) || (e.ctrlKey && e.metaKey) || e.code !== 'KeyL' || e.repeat) return;
    e.preventDefault(); e.stopPropagation();
    var m = toggle(), T = window.T || function (s) { return s; };
    if (window.HK && window.HK.toast) window.HK.toast(m === 'dark' ? T('Dunkel') : T('Hell'));
  }, true);
  window.SagaTheme = { get: function () { return mode; }, set: set, dark: dark, toggle: toggle };
})();
