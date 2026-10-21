// Admin-Seite "Anrufe" (Saga 2.5, Gjallarhorn P8): Erweitern mit Live-Fortschritt, Port-Pruefung ohne Neuladen,
// Kopieren/Herunterladen der Skripte. Ohne JavaScript funktioniert alles ueber die normalen Formulare.
// Abfragen nur, solange der Dialog offen ist und die Seite sichtbar ist (alle 2 s, ab 2 min alle 5 s, Ende bei
// "Fertig" oder abgelaufenem Code) - keine Dauerabfragen.
(function () {
  'use strict';
  var L = {};
  try { L = JSON.parse((document.getElementById('horn-i18n') || {}).textContent || '{}'); } catch (e) { L = {}; }
  var csrf = (document.querySelector('input[name=csrf]') || {}).value || '';
  function fill(s, p) { return String(s || '').replace(/\{(\w+)\}/g, function (m, k) { return p && k in p ? p[k] : m; }); }
  function api(method, url, body) {
    return fetch(url, { method: method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); });
  }

  // Kopieren / Herunterladen (Ziel: Selektor im naechsten umgebenden Block)
  function target(b, sel) {
    var box = b.closest('details, .horn-pair-out, .adm-card') || document;
    return box.querySelector(sel) || document.querySelector(sel);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy],[data-download]');
    if (!b) return;
    var el = target(b, b.dataset.copy || b.dataset.download);
    if (!el) return;
    var text = el.textContent;
    if (b.dataset.copy) {
      var done = function () { var o = b.textContent; b.textContent = L.copied || '✓'; setTimeout(function () { b.textContent = o; }, 1500); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () {});
      else { var r = document.createRange(); r.selectNodeContents(el); var s = getSelection(); s.removeAllRanges(); s.addRange(r); }
    } else {
      var a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([text], { type: 'text/x-shellscript' }));
      a.download = el.dataset.file || 'gjallarhorn.sh';
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    }
  });

  // Port pruefen ohne Neuladen
  function showCheck(nid, r) {
    var t = document.querySelector('.horn-port-t[data-node="' + nid + '"]');
    if (!t) return;
    var tcp = t.querySelector('.pc-tcp'), udp = t.querySelector('.pc-udp'), ctl = t.querySelector('.pc-ctl');
    if (tcp) {
      if ((r.v4 || r.v6) && r.v4 !== false && r.v6 !== false) tcp.textContent = fill(L.reach_ok, { a: r.v4 ? '✓' : '–', b: r.v6 ? '✓' : '–' });
      else if (r.v4) tcp.textContent = L.reach_v6;
      else if (r.v6) tcp.textContent = L.reach_v4;
      else tcp.textContent = L.reach_no;
    }
    if (udp) udp.textContent = r.udp ? L.udp_ok : L.udp_no;
    if (ctl) ctl.textContent = r.ctl ? L.ctl_ok : L.ctl_no;
  }
  document.querySelectorAll('[data-check]').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.preventDefault();
      var nid = b.dataset.check, d = document.getElementById('n-' + nid);
      if (d) d.open = true;
      var o = b.textContent; b.disabled = true; b.textContent = L.checking || '…';
      api('POST', '/api/calls/nodes/' + encodeURIComponent(nid) + '/check').then(function (r) { showCheck(nid, r); })
        .catch(function (x) { alert((L.err || 'Fehler') + ': ' + x.message); })
        .then(function () { b.disabled = false; b.textContent = o; });
    });
  });

  // Erweitern: Skript erzeugen + Live-Fortschritt
  var f = document.querySelector('.horn-pair-f'), out = document.querySelector('.horn-pair-out');
  if (!f || !out) return;
  var timer = null, started = 0;
  function step(li, s) {
    li.hidden = false;
    li.className = 'hp-' + s;
    var t = li.dataset[s === 'run' && li.dataset.run ? 'run' : s];
    if (t) li.textContent = t;
  }
  function render(st) {
    var seen = {};
    (st.steps || []).forEach(function (x) {
      var li = out.querySelector('.horn-prog li[data-k="' + x.k + '"]');
      if (!li) return;
      seen[x.k] = 1;
      step(li, x.s);
    });
    if (st.expired) step(out.querySelector('.horn-prog li[data-k="done"]'), 'expired');
    return st.done || st.expired;
  }
  function poll() {
    var pid = out.dataset.pid;
    timer = null;
    if (!pid || document.hidden || !out.closest('details').open) { timer = setTimeout(poll, 5000); return; }
    api('GET', '/api/calls/pair/' + encodeURIComponent(pid)).then(function (st) {
      if (!render(st)) timer = setTimeout(poll, Date.now() - started > 120000 ? 5000 : 2000);
    }).catch(function () { timer = setTimeout(poll, 10000); });
  }
  f.addEventListener('submit', function (e) {
    e.preventDefault();
    var btn = f.querySelector('button[type=submit]');
    btn.disabled = true;
    api('POST', f.dataset.api, { name: f.elements.namedItem('name').value.trim(), share: f.elements.namedItem('share').checked, udp: f.elements.namedItem('udp').checked }).then(function (p) {
      out.hidden = false;
      out.dataset.pid = p.id;
      out.querySelector('.horn-script').textContent = p.script_docker;
      out.querySelector('.horn-script').dataset.file = 'gjallarhorn-' + p.name + '.sh';
      out.querySelector('.horn-script-sd').textContent = p.script_systemd;
      out.querySelector('.horn-script-sd').dataset.file = 'gjallarhorn-' + p.name + '-systemd.sh';
      var tm = out.querySelector('.horn-exp');
      if (tm) tm.textContent = new Date(p.expires * 1000).toLocaleTimeString(document.documentElement.lang || undefined, { hour: '2-digit', minute: '2-digit' });
      out.querySelectorAll('.horn-prog li').forEach(function (li, i) { li.hidden = i > 0; li.className = i ? '' : 'hp-run'; });
      f.hidden = true;
      started = Date.now();
      if (timer) clearTimeout(timer);
      poll();
    }).catch(function (x) { alert(x.message); }).then(function () { btn.disabled = false; });
  });
  if (out.dataset.pid) { started = Date.now(); poll(); }      // ohne JS erzeugt (Formular), danach mit JS weiter
})();
