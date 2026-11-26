/* Zustimmungsseite "App verbinden" (2.6, Bugs-DB 2264, templates/oauth.html): Bestaetigen mit Passkey - dieselben
   Endpunkte wie Einstellungen > Sicherheit (/api/account/security/options und /confirm, setzt die frische Bestaetigung
   der Sitzung), danach die Seite neu laden (zeigt dann die Rechte). Texte aus data-t-* der Seite. */
(function () {
  'use strict';
  var box = document.getElementById('oa-pk'), PK = window.SagaPasskey;
  if (!box || !PK) return;
  var go = document.getElementById('oa-pk-go'), st = box.querySelector('.pk-status'), er = box.querySelector('.pk-err');
  function show(el, txt) { el.textContent = txt || ''; el.hidden = !txt; }
  box.hidden = false;
  var bad = PK.originProblem(box.dataset.origin, box.dataset.tOrigin);
  if (!PK.supported() || bad) { go.disabled = true; show(er, bad || box.dataset.tNosupport); return; }
  go.addEventListener('click', function () {
    go.disabled = true; show(er, ''); show(st, box.dataset.tBusy);
    PK.post('/api/account/security/options')
      .then(function (d) { return PK.get(d.options); })
      .then(function (cred) { return PK.post('/api/account/security/confirm', { passkey: cred }); })
      .then(function () { location.reload(); })
      .catch(function (e) { go.disabled = false; show(st, ''); show(er, PK.cancelled(e) ? box.dataset.tFail : (e.message || box.dataset.tFail)); });
  });
})();
