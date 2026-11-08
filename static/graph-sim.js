// Saga - gemeinsame Kraefte-Simulation (2.5): Barnes-Hut-Quadtree fuer die Abstossung. Genutzt vom Seiten-Graph
// (static/graph.js) und von der Netz-Karte im Admin-Bereich (static/admin-net.js). Keine Fremdbibliothek (CSP).
// Knoten: {x, y, vx, vy}. quad(knoten) -> Baum; repel(baum, knoten, staerke) addiert die Abstossung auf vx/vy.
(function () {
  'use strict';
  function quad(nodes) {
    var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, i, n;
    for (i = 0; i < nodes.length; i++) { n = nodes[i]; if (n.x < x0) x0 = n.x; if (n.y < y0) y0 = n.y; if (n.x > x1) x1 = n.x; if (n.y > y1) y1 = n.y; }
    var root = cell(x0, y0, Math.max(x1 - x0, y1 - y0) + 1);
    for (i = 0; i < nodes.length; i++) ins(root, nodes[i], 0);
    return root;
  }
  function cell(x, y, s) { return { x: x, y: y, s: s, m: 0, cx: 0, cy: 0, n: null, k: null }; }
  function ins(q, n, d) {
    if (!q.k && !q.n) { q.n = n; q.m = 1; q.cx = n.x; q.cy = n.y; return; }
    if (!q.k) {
      if (d > 40) { q.m++; return; }
      q.k = [null, null, null, null]; var o = q.n; q.n = null; q.m = 0; q.cx = 0; q.cy = 0;
      put(q, o, d);
    }
    put(q, n, d);
  }
  function put(q, n, d) {
    var h = q.s / 2, i = (n.x >= q.x + h ? 1 : 0) + (n.y >= q.y + h ? 2 : 0);
    if (!q.k[i]) q.k[i] = cell(q.x + (i & 1 ? h : 0), q.y + (i & 2 ? h : 0), h);
    ins(q.k[i], n, d + 1);
    q.cx = (q.cx * q.m + n.x) / (q.m + 1); q.cy = (q.cy * q.m + n.y) / (q.m + 1); q.m++;
  }
  function repel(q, nd, f0) {
    if (!q || !q.m) return;
    if (q.n === nd && !q.k) return;
    var dx = q.cx - nd.x, dy = q.cy - nd.y, d2 = dx * dx + dy * dy + 0.01;
    if (!q.k || (q.s * q.s) / d2 < 0.81) {           // weit weg (theta 0.9): ganze Zelle als ein Koerper
      if (d2 > 1e6) return;
      var f = f0 * q.m / d2; nd.vx -= dx * f; nd.vy -= dy * f; return;
    }
    repel(q.k[0], nd, f0); repel(q.k[1], nd, f0); repel(q.k[2], nd, f0); repel(q.k[3], nd, f0);
  }

  window.SagaForce = { quad: quad, repel: repel };
})();
