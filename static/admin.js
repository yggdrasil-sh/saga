// Admin-Bereich: Rueckfragen vor gefaehrlichen Aktionen (alles andere sind normale Formulare) + Zeiten in Ortszeit
(function () {
  'use strict';
  // 2.6 (Bugs-DB 2267 K-B): Selbsttest der KI (Admin -> KI-Assistent -> Auftraege) - nur ein Vorschlag fuer die Stufe
  var stb = document.getElementById('ai-selftest'), sto = document.getElementById('ai-selftest-out');
  if (stb && sto) {
    stb.addEventListener('click', function () {
      var f = stb.form, csrf = f && f.querySelector('input[name=csrf]');
      stb.disabled = true; sto.textContent = stb.dataset.busy || '…';
      fetch('/api/ai/selftest', { method: 'POST', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': csrf ? csrf.value : '', 'Content-Type': 'application/json' }, body: '{}' })
        .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || stb.dataset.fail); return d; }); })
        .then(function (d) {
          sto.textContent = d.text + (d.note ? ' · ' + d.note : '');
          var sel = f.querySelector('select[name=level]');
          if (sel && d.level) sel.dataset.suggest = d.level;
        })
        .catch(function (e) { sto.textContent = (e && e.message) || stb.dataset.fail; })
        .then(function () { stb.disabled = false; });
    });
  }
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
  // Titelbild-Galerie (2.5): Position als Vorschau (Schieberegler -> object-position), gewaehltes Bild vor dem Hochladen zeigen
  //  (style-src 'self': Position per CSSOM statt style-Attribut)
  document.querySelectorAll('img[data-pos]').forEach(function (im) { im.style.objectPosition = 'center ' + im.dataset.pos + '%'; });
  document.querySelectorAll('input[type=range][data-pos-for]').forEach(function (r) {
    var im = document.getElementById(r.dataset.posFor); if (!im) return;
    var upd = function () { im.style.objectPosition = 'center ' + r.value + '%'; };
    r.addEventListener('input', upd); upd();
  });
  document.querySelectorAll('input[type=file][data-cover-file]').forEach(function (inp) {
    var im = document.getElementById(inp.dataset.coverFile); if (!im) return;
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0];
      if (!f || !/^image\//.test(f.type) || f.size > 20 * 1048576) { im.hidden = true; im.removeAttribute('src'); return; }
      var rd = new FileReader();                         // data: (img-src 'self' data:) - blob: erlaubt die CSP nicht
      rd.onload = function () { im.src = rd.result; im.hidden = false; };
      rd.readAsDataURL(f);
    });
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
  // Handy und mittlere Breiten: Reiterleiste scrollt waagrecht - aktiven Reiter zeigen, Verlauf am Rand, solange dort noch etwas steht
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
      if (l + on.offsetWidth > nav.clientWidth - 24) nav.scrollLeft = Math.max(0, l - (nav.clientWidth - on.offsetWidth) / 2);   // mittig, kein halber Reiter am Rand
    }
    nav.addEventListener('scroll', upd, { passive: true });
    window.addEventListener('resize', upd);
    upd();
  }
})();
