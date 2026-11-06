// Design-Runde 12 (MOB-01): Tab-Leiste unten am Handy. "Seiten" oeffnet auf /notes die Schublade, "Suchen" die
// Schnellsuche, "Marin" das KI-Panel (erscheint erst, wenn der Assistent-Knopf da ist; Name wie dort).
// Bei offener Bildschirmtastatur blendet sich die Leiste aus (body.kb-open).
(function () {
  'use strict';
  var bar = document.getElementById('mob-tabs'); if (!bar) return;
  var $ = function (id) { return document.getElementById(id); };
  bar.addEventListener('click', function (e) {
    var t = e.target.closest('[data-act]'); if (!t) return;
    var a = t.dataset.act, side = $('pg-side-btn'), qs = $('pg-qs-btn'), ai = $('ai-btn');
    if (a === 'pages' && document.body.classList.contains('notes-page') && side) { e.preventDefault(); side.click(); }
    else if (a === 'search' && qs) { e.preventDefault(); qs.click(); }
    else if (a === 'ai' && ai) { e.preventDefault(); ai.click(); }
  });
  // Marin: Knopf entsteht erst, wenn ai.js den Status kennt
  var aiTab = bar.querySelector('[data-act="ai"]'), tries = 0;
  (function look() {
    var ai = $('ai-btn');
    if (ai) { aiTab.hidden = false; var n = (ai.getAttribute('aria-label') || '').replace(/^\S+\s+/, ''); if (n) aiTab.querySelector('.mob-tab-ai').textContent = n; return; }
    if (++tries < 40) setTimeout(look, 500);
  })();
  var vv = window.visualViewport;
  if (vv) vv.addEventListener('resize', function () { document.body.classList.toggle('kb-open', vv.height < window.innerHeight * 0.75); });
})();
