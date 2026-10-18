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
})();
