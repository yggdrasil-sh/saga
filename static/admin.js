// Admin-Bereich: Rueckfragen vor gefaehrlichen Aktionen (alles andere sind normale Formulare) + Zeiten in Ortszeit
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
  // Seite, die auf etwas wartet (Backup laeuft, Nextcloud-Anmeldung): nach ein paar Sekunden neu laden
  var rl = document.querySelector('[data-reload]');
  if (rl) setTimeout(function () { location.reload(); }, (parseFloat(rl.dataset.reload) || 5) * 1000);
  // Zeitpunkte (<time class="adm-ts" data-ts="<Unix-Sekunden>">) in der Zeitzone des Browsers, mit Kurzname (MESZ, GMT+2 …)
  var lang = document.documentElement.lang || undefined, f = null, zone = '';
  try {
    f = new Intl.DateTimeFormat(lang, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', timeZoneName: 'short' });
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  } catch (e) { f = null; }
  if (f) document.querySelectorAll('time.adm-ts[data-ts]').forEach(function (t) {
    var s = parseFloat(t.dataset.ts);
    if (!isFinite(s) || s <= 0) return;
    var d = new Date(s * 1000);
    t.textContent = f.format(d);
    t.dateTime = d.toISOString();
    if (zone) t.title = zone;
  });
  // Handy: Reiterleiste scrollt waagrecht - aktiven Reiter zeigen, Verlauf am Rand, solange dort noch etwas steht
  var nav = document.querySelector('.adm-nav');
  if (nav) {
    var upd = function () {
      var max = nav.scrollWidth - nav.clientWidth;
      nav.classList.toggle('fade-l', nav.scrollLeft > 2);
      nav.classList.toggle('fade-r', max > 2 && nav.scrollLeft < max - 2);
    };
    var on = nav.querySelector('a.on');
    if (on && nav.scrollWidth > nav.clientWidth) {
      var l = on.getBoundingClientRect().left - nav.getBoundingClientRect().left + nav.scrollLeft;
      if (l + on.offsetWidth > nav.clientWidth - 24) nav.scrollLeft = l + on.offsetWidth - nav.clientWidth + 40;
    }
    nav.addEventListener('scroll', upd, { passive: true });
    window.addEventListener('resize', upd);
    upd();
  }
})();
