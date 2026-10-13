// Admin-Bereich: nur Rueckfragen vor gefaehrlichen Aktionen (alles andere sind normale Formulare)
(function () {
  'use strict';
  document.querySelectorAll('form[data-confirm]').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      var b = e.submitter;
      if (b && b.dataset.confirm) return;                // Knopf hat eigene Rueckfrage
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    });
  });
  document.querySelectorAll('button[data-confirm]').forEach(function (b) {
    b.addEventListener('click', function (e) { if (!confirm(b.dataset.confirm)) e.preventDefault(); });
  });
})();
