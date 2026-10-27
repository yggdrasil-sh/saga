// 2.5 (P6-1): Diagramme (Mermaid) im abgeschotteten Rahmen zeichnen - siehe static/mermaid-frame.html.
// Nachricht vom Eltern-Fenster: {hk: 'mmd', id, src, dark} -> Antwort {hk: 'mmd', id, svg} oder {hk: 'mmd', id, error}.
(function () {
  'use strict';
  var MAX_SRC = 20000, MAX_SVG = 2000000, theme = null, busy = Promise.resolve();
  function setup(dark) {
    var t = dark ? 'dark' : 'default';
    if (theme === t) return;
    theme = t;
    window.mermaid.initialize({
      startOnLoad: false, securityLevel: 'strict', theme: t, htmlLabels: false, maxTextSize: MAX_SRC, maxEdges: 500,
      flowchart: { htmlLabels: false }, class: { htmlLabels: false }, state: { htmlLabels: false }, deterministicIds: true,
      fontFamily: 'Helvetica, Arial, sans-serif'
    });
  }
  var n = 0;
  window.addEventListener('message', function (e) {
    var d = e.data;
    if (e.source !== window.parent || !d || d.hk !== 'mmd' || typeof d.src !== 'string') return;
    var id = d.id;
    busy = busy.then(function () {
      if (d.src.length > MAX_SRC) throw new Error('too large');
      setup(!!d.dark);
      n += 1;
      return window.mermaid.render('hkm' + n, d.src);
    }).then(function (r) {
      var svg = r && typeof r.svg === 'string' ? r.svg : '';
      if (!svg || svg.length > MAX_SVG) throw new Error('empty');
      window.parent.postMessage({ hk: 'mmd', id: id, svg: svg }, '*');
    }).catch(function (err) {
      window.parent.postMessage({ hk: 'mmd', id: id, error: String((err && err.message) || err).slice(0, 300) }, '*');
      var junk = document.querySelectorAll('body > [id^="dhkm"], body > svg[id^="hkm"]');      // Reste eines Fehlschlags
      for (var i = 0; i < junk.length; i++) junk[i].remove();
    });
  });
  window.parent.postMessage({ hk: 'mmd', ready: true }, '*');
})();
