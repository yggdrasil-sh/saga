/* Erste Schritte (2.6, Bugs-DB 2031): beim ersten Anmelden die Frage nach einer kurzen Einfuehrung (Ja -> Seite mit
   Haekchenliste, Nein -> nichts), spaeter unter Einstellungen > Hilfe wieder abrufbar. Abgehakt wird auf dem Server
   (saga/firststeps.py) - jeder Abruf von GET /api/first-steps hakt neu Erledigtes ab. Ganz ohne KI. */
(function () {
  'use strict';
  if (!window.HK || !HK.api) return;
  var api = HK.api, el = HK.el, asked = false;
  var F = window.HKFirstSteps = { st: null };

  function open(page) {                                // neue Seite: Liste neu laden, dann oeffnen
    if (location.pathname === '/notes') { location.hash = '#' + page; location.reload(); }
    else HK.go('/notes#' + page);
  }
  function answer(yes) {
    return api('POST', '/api/first-steps', { answer: yes ? 'yes' : 'no' }).then(function (r) {
      F.st = Object.assign({}, F.st || {}, { ask: false, state: F.st && F.st.state === 'yes' ? 'yes' : (yes ? 'yes' : 'no'), page: r.page || null });
      return r;
    });
  }
  function ask() {
    if (asked) return;
    asked = true;
    HK.ask({ title: T('Möchtest du eine kurze Einführung?'),
      text: T('Saga legt dir eine Seite „Erste Schritte“ mit ein paar Häkchen an: Profilbild, Chats, App, erste Seite und Aufgabe. Was du erledigt hast, hakt Saga selbst ab – und die Seite kannst du jederzeit löschen.'),
      ok: T('Ja, gern'), cancel: T('Nein, danke'), dismiss: null }).then(function (yes) {
      if (yes === null) return null;                   // weggeklickt/Escape: nichts speichern, beim naechsten Laden wieder
      return answer(!!yes).then(function (r) { if (yes && r.page) open(r.page); });
    }).catch(function (e) { HK.toast(e.message); });
  }
  F.check = function () {
    return api('GET', '/api/first-steps').then(function (d) {
      F.st = d;
      document.dispatchEvent(new CustomEvent('hk-first-steps', { detail: d }));
      // nicht in eingebetteten Ansichten und nicht ueber anderen Dialogen
      if (d.ask && !document.querySelector('.hk-ask, .hk-modal') && !document.body.classList.contains('embed')) ask();
      return d;
    }).catch(function () { return null; });
  };
  // Einstellungen > Hilfe: "Erste Schritte öffnen" (legt die Seite neu an, wenn sie fehlt)
  F.section = function (sec) {
    var box = el('div', 'hk-fs'); box.hidden = true; sec.appendChild(box);
    (F.st ? Promise.resolve(F.st) : api('GET', '/api/first-steps')).then(function (d) {
      F.st = d;
      if (!d || !d.enabled) return;
      box.hidden = false;
      box.appendChild(el('h4', null, T('Erste Schritte')));
      var n = (d.items || []).filter(function (x) { return x.done; }).length, all = (d.items || []).length;
      box.appendChild(el('p', 'hk-hint', d.page ? T('Deine Seite „Erste Schritte“: {n} von {all} erledigt.', { n: n, all: all })
        : T('Eine Seite mit ein paar Häkchen für den Anfang – Saga hakt Erledigtes selbst ab.')));
      var b = el('button', 'btn hk-fs-open', d.page ? T('Erste Schritte öffnen') : T('Erste Schritte anlegen')); b.type = 'button';
      b.addEventListener('click', function () {
        b.disabled = true;
        answer(true).then(function (r) { if (window.HKSettings) HKSettings.close(); if (r.page) open(r.page); })
          .catch(function (e) { b.disabled = false; HK.toast(e.message); });
      });
      box.appendChild(b);
    }).catch(function () { box.remove(); });
  };
  function start() { setTimeout(F.check, 600); }      // nach dem Aufbau der Seite (Fokus, andere Dialoge)
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
