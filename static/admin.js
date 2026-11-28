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
  // 2.6 (Bugs-DB 2581): Admin -> KI-Assistent -> Embeddings (docs/MEMORY.md 1.1). Speichern/Testen als JSON mit CSRF;
  // der gespeicherte Schluessel kommt nie zurueck (nur "gesetzt"). Texte stehen uebersetzt in data-* der Vorlage.
  var ef = document.querySelector('form[data-embed]');
  if (ef) (function (f) {
    var D = f.dataset, $ = function (id) { return document.getElementById(id); };
    var inp = function (n) { return f.querySelector('[name=' + n + ']'); };
    var out = $('emb-out'), save = $('emb-save'), test = $('emb-test');
    var saved = { url: inp('url').value.trim(), where: $('emb-where-net').hidden ? ($('emb-where-home').hidden ? null : 'home') : 'internet' };
    function csrf() { var c = f.querySelector('input[name=csrf]'); return c ? c.value : ''; }
    function vals() {
      return { enabled: inp('enabled').checked, provider: inp('provider').value, url: inp('url').value.trim(), model: inp('model').value.trim(),
               api_key: inp('api_key').value.trim(), clear_key: inp('clear_key').checked, keep_alive: inp('keep_alive').value.trim(),
               timeout: parseInt(inp('timeout').value, 10) || null, query_timeout_ms: parseInt(inp('query_timeout_ms').value, 10) || null };
    }
    function send(method, url, body) {
      return fetch(url, { method: method, credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf(), 'Content-Type': 'application/json' },
                          body: JSON.stringify(body) })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); });
    }
    function show(txt, cls) { out.hidden = false; out.textContent = txt; out.className = 'adm-test' + (cls ? ' ' + cls : ''); }
    // Grobe Einschaetzung beim Tippen (genau prueft der Server beim Speichern: where = home | internet)
    function looksPublic(u) {
      var h;
      try { h = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(u) ? u : 'http://' + u).hostname.toLowerCase(); } catch (e) { return false; }
      h = h.replace(/^\[|\]$/g, '');
      if (!h) return false;
      if (/^\d+\.\d+\.\d+\.\d+$/.test(h)) {
        var p = h.split('.').map(Number);
        return !(p[0] === 10 || p[0] === 127 || (p[0] === 172 && p[1] >= 16 && p[1] <= 31) || (p[0] === 192 && p[1] === 168) ||
                 (p[0] === 169 && p[1] === 254) || (p[0] === 100 && p[1] >= 64 && p[1] <= 127));
      }
      if (h.indexOf(':') >= 0) return !(h === '::1' || /^f[cd]/.test(h) || /^fe[89ab]/.test(h));
      if (h.indexOf('.') < 0) return false;
      return !/\.(lan|local|localhost|internal|intranet|home|home\.arpa|corp)$/.test(h);
    }
    function where() {
      var u = inp('url').value.trim(), same = u === saved.url;
      $('emb-where-net').hidden = !(same && saved.where === 'internet');
      $('emb-where-home').hidden = !(same && saved.where === 'home');
      $('emb-where-maybe').hidden = same || !u || !looksPublic(u);
    }
    function keep() { $('emb-keep').hidden = inp('provider').value === 'openai'; }
    function apply(d) {
      var s = d.settings || {};
      saved = { url: s.url || '', where: d.where || null };
      // 2581: alle Felder vom Server uebernehmen (so steht da, was wirklich gespeichert ist - z. B. Adresse normalisiert)
      inp('enabled').checked = !!s.enabled;
      if (s.provider) inp('provider').value = s.provider;
      inp('model').value = s.model || '';
      inp('keep_alive').value = s.keep_alive || '';
      if (s.timeout != null) inp('timeout').value = s.timeout;
      if (s.query_timeout_ms != null) inp('query_timeout_ms').value = s.query_timeout_ms;
      keep();
      inp('url').value = s.url || ''; inp('api_key').value = ''; inp('clear_key').checked = false;
      $('emb-key-hint').textContent = s.has_key ? D.keySet : D.keyNone;
      $('emb-clear').hidden = !s.has_key;
      var st = d.stats, sp = $('emb-stats');
      if (st && sp) sp.textContent = D.stats.replace('{a}', st.accounts).replace('{e}', st.embedded).replace('{r}', st.rows) + (st.running ? ' · ' + D.running : '');
      where();
    }
    function busy(on) { save.disabled = test.disabled = on; }
    function doSave() {
      busy(true); show(D.busy);
      send('PUT', '/api/ai/embed', vals()).then(function (d) { apply(d); show(D.saved, 'ok'); })
        .catch(function (e) { show(D.fail.replace('{err}', e.message), 'err'); }).then(function () { busy(false); });
    }
    save.addEventListener('click', doSave);
    test.addEventListener('click', function () {
      busy(true); show(D.busy);
      send('POST', '/api/ai/embed/test', vals()).then(function (d) { show((d.lines || []).join('\n'), d.ok ? 'ok' : 'err'); })
        .catch(function (e) { show('✗ ' + e.message, 'err'); }).then(function () { busy(false); });
    });
    f.addEventListener('submit', function (e) { e.preventDefault(); doSave(); });
    f.addEventListener('keydown', function (e) { if (e.key === 'Enter' && e.target.tagName === 'INPUT' && e.target.type !== 'checkbox') { e.preventDefault(); doSave(); } });
    inp('provider').addEventListener('change', keep);
    inp('url').addEventListener('input', where);
    keep(); where();
  })(ef);
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
