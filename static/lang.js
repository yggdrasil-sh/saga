/* Sprachumschalter (Anmeldeseiten, Admin-Bereich; Formular aus saga.core.lang_switch): die Auswahl sofort abschicken.
   Ohne JavaScript bleibt der Knopf "Sprache wechseln" daneben sichtbar. */
(function () {
  'use strict';
  var forms = document.querySelectorAll('form.lang-sw');
  for (var i = 0; i < forms.length; i++) {
    (function (f) {
      var sel = f.querySelector('select'), go = f.querySelector('.lang-sw-go');
      if (!sel) return;
      if (go) go.hidden = true;
      sel.addEventListener('change', function () { f.submit(); });
    })(forms[i]);
  }
  // 2.5: nach dem Abmelden (/logout leitet immer auf /login?bye=1, auch ueber direkte Links in security.html, login2fa
  // und nach OIDC): Chat-Entwuerfe (localStorage saga-chat-draft:<konto>:<chat>, static/chat.js) hier mit loeschen
  if (/[?&]bye=1(&|$)/.test(location.search)) try {
    for (var j = localStorage.length - 1; j >= 0; j--) { var k = localStorage.key(j); if (k && k.indexOf('saga-chat-draft:') === 0) localStorage.removeItem(k); }
  } catch (e) { /* ohne Speicher: nichts zu loeschen */ }
})();
