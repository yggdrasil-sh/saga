// Editor-Diagnose (2.5, Tiptap-Umbau T6): meldet Round-Trip-Abweichungen UNVERAENDERTER Bloecke an POST /api/diag
// (Vertrag docs/APP-API.md "Editor-Diagnose"), damit Luecken in static/pm-adapter.js im Feldtest auffallen.
//
// Wird von static/pm-editor.js mitgeladen (EXT_SCRIPTS) und meldet sich mit HKPmEditor.use an. Ablauf:
// - Plugin hkDiag merkt sich je Editor-Stand die Block-IDs, die eine Transaktion geaendert hat ("angefasst"; live:
//   ohne die Stände aus Yjs). Alles andere gilt als unveraendert.
// - Beim Speichern (Haken diagSaved aus create().save) bzw. live einige Sekunden nach einer Aenderung, je Knoten
//   einmal:  pass  = gespeicherte Form != geladene (Durchreichung verloren; nur ohne live),
//            norm  = Normalform des Originals weicht ueber die erlaubten Abweichungen hinaus ab (strong->b,
//                    Markenreihenfolge, Listen-Strings -> Objekte, Toggle-Altformat, Schluesselreihenfolge),
//            fresh = frisch aus dem Editor-Knoten serialisiert != Normalform (live ohne Original: != Fixpunkt),
//            err   = Serialisieren wirft.
// - Gemeldet werden nur Knotentyp, Feldpfad (Feldnamen/Indizes, HKPmAdapter.diag) und Art - nie Inhalt, Text, IDs,
//   Dateinamen oder Adressen. Je Sitzung dedupliziert, hoechstens ein POST je 60 s mit hoechstens 20 Eintraegen.
//   Nur mit Feature pmdiag1 in /api/version (Admin-Schalter editor.diag); 403/404 -> fuer diese Sitzung aus.
// Diagnose/Tests: window.HKPmDiag.state(), .flush() (Sendeabstand ignorieren), .tamper = fn(typ, block) -> block
// (verfaelscht die frische Serialisierung, nur fuer Tests).
(function (root) {
  'use strict';
  var GAP = 60000, MAX_ITEMS = 20, MAX_QUEUE = 100, LIVE_DELAY = 4000, FEAT_TTL = 300000;
  var Q = [], SENT = {}, ST = { checked: 0, found: 0, posts: 0, dropped: 0, off: false, last: 0 };
  var FEAT = { on: null, at: 0 }, timer = null, sending = false;
  var api = root.HKPmDiag = { tamper: null, state: function () { return Object.assign({ queued: Q.length, items: Q.slice() }, ST); },
                              flush: function () { return send(true); } };
  if (!root.HKPmEditor || typeof HKPmEditor.use !== 'function') return;

  function features() {
    var now = Date.now();
    if (FEAT.on !== null && now - FEAT.at < FEAT_TTL) return Promise.resolve(FEAT.on);
    return HK.api('GET', '/api/version').then(function (v) {
      FEAT.on = !!(v && (v.features || []).indexOf('pmdiag1') >= 0); FEAT.at = Date.now(); return FEAT.on;
    }, function () { return false; });
  }
  function queue(t, list) {
    list.forEach(function (x) {
      var k = t + '|' + x.p + '|' + x.k;
      if (SENT[k]) return;
      SENT[k] = 1; ST.found++;
      if (Q.length < MAX_QUEUE) Q.push({ t: t, p: x.p, k: x.k }); else ST.dropped++;
    });
    if (Q.length) plan();
  }
  function plan() {
    if (timer || ST.off) return;
    timer = setTimeout(function () { timer = null; send(false); }, Math.max(0, ST.last + GAP - Date.now()));
  }
  function send(force) {
    if (ST.off || sending || !Q.length) return Promise.resolve(false);
    if (!force && Date.now() - ST.last < GAP) { plan(); return Promise.resolve(false); }
    sending = true;
    return features().then(function (on) {
      if (!on) { Q.length = 0; return false; }       // Schalter aus: nichts senden, nichts aufheben
      var items = Q.splice(0, MAX_ITEMS), X = SELF;
      var body = { v: 1, mode: X && X.live() ? 'live' : 'save', sv: X ? X.SC.VERSION : 0, av: String(document.body.dataset.v || '').slice(0, 40), items: items };
      ST.last = Date.now(); ST.posts++;
      return HK.api('POST', '/api/diag', body).then(function () { return true; }, function (e) {
        if (e && (e.status === 403 || e.status === 404)) { ST.off = true; Q.length = 0; }
        else if (e && e.status === 429) Array.prototype.unshift.apply(Q, items);
        return false;
      });
    }).then(function (r) { sending = false; if (Q.length) plan(); return r; }, function () { sending = false; return false; });
  }

  var SELF = null;
  HKPmEditor.use(function (X) {
    SELF = X;
    var AD = X.AD, S0 = X.P.pm.state, KEY = new S0.PluginKey('hkDiag');
    var CHECKED = new WeakSet(), liveT = null;

    // --- angefasste Bloecke: Teilbaeume, die dasselbe Objekt geblieben sind, sind unveraendert -----------------------
    function isBlk(n) { return X.isBlock(n); }
    function diff(o, n, out) {
      if (o === n) return;
      if (isBlk(n) && n.attrs.id) out[n.attrs.id] = 1;
      if (n.isLeaf || n.isTextblock) return;
      var olds = [], same = new Set(), byId = {};
      if (o) o.forEach(function (c) { olds.push(c); same.add(c); if (isBlk(c) && c.attrs.id) byId[c.attrs.id] = c; });
      n.forEach(function (c, off, i) {
        if (same.has(c)) return;
        diff(isBlk(c) ? (c.attrs.id && byId[c.attrs.id]) || null : olds[i] || null, c, out);
      });
    }
    function plugin() {
      return new S0.Plugin({
        key: KEY,
        state: {
          init: function () { return { touched: {} }; },
          apply: function (tr, v, old, now) {
            if (!tr.docChanged) return v;
            var ys = tr.getMeta('y-sync$'); if (ys && ys.isChangeOrigin) return v;     // live: Stand aus Yjs, keine Eingabe
            var t = Object.assign({}, v.touched); diff(old.doc, now.doc, t);
            return { touched: t };
          }
        },
        view: function () {
          return { update: function (view, prev) {
            if (view.state.doc === prev.doc || !X.live()) return;
            clearTimeout(liveT); liveT = setTimeout(function () { check(null); }, LIVE_DELAY);
          }, destroy: function () { clearTimeout(liveT); } };
        }
      });
    }

    // --- Pruefung ---------------------------------------------------------------------------------------------------
    function legacyToggle(b) { return b && b.type === 'toggle' && b.data && typeof b.data === 'object' && 'text' in b.data && !Array.isArray(b.data.blocks); }
    function collect(list, m) {
      (list || []).forEach(function (b) {
        if (!b || typeof b !== 'object') return;
        if (b.id != null && !(b.id in m)) m[b.id] = b;
        var d = b.data || {};
        if (Array.isArray(d.blocks)) collect(d.blocks, m);
        if (Array.isArray(d.cols)) d.cols.forEach(function (c) { if (c && Array.isArray(c.blocks)) collect(c.blocks, m); });
      });
      return m;
    }
    function check(out) {
      var ed = X.ed(), ctx = X.ctx();
      if (!ed || !ctx || ST.off || FEAT.on === false) return;
      var ps = KEY.getState(ed.state); if (!ps) return;
      var live = !!X.live(), touched = ps.touched, resolve = ctx.resolve, opt = { resolve: resolve };
      var origs = !live && out ? collect((X.data0() || {}).blocks, {}) : null, saved = origs ? collect(out.blocks, {}) : null;
      X.walk(ed.state.doc, function (x) {
        var n = x.node, id = n.attrs.id, t = n.type.name, found = [];
        if (!id || touched[id]) return;
        var o = ctx.orig.get(n) || null, o0 = origs ? origs[id] : null;
        if (legacyToggle(o) || legacyToggle(o0)) return;                    // Toggle-Altformat: erlaubte Abweichung
        // pass: gespeicherte Form gegen geladene (dasselbe Objekt = byte-gleich)
        if (o0 && saved && saved[id] !== undefined && saved[id] !== o0 && JSON.stringify(saved[id]) !== JSON.stringify(o0)) {
          AD.diag.paths(AD.diag.shallow(o0), AD.diag.shallow(saved[id]), { html: true }).forEach(function (p) { found.push({ p: p, k: 'pass' }); });
        }
        if (!CHECKED.has(n)) {
          CHECKED.add(n); ST.checked++;
          try {
            var fresh = AD.pmToBlock(n, opt);
            if (typeof api.tamper === 'function') fresh = api.tamper(t, JSON.parse(JSON.stringify(fresh))) || fresh;
            var base = o || o0, ref;
            if (base) {
              ref = AD.pmToBlock(AD.blockToPm(base, { orig: null }), opt);
              found = found.concat(AD.diag.block(base, ref, fresh));
            } else if (live) {                                                // live ohne Original: Fixpunkt
              ref = AD.pmToBlock(AD.blockToPm(fresh, { orig: null }), opt);
              AD.diag.paths(AD.diag.shallow(fresh), AD.diag.shallow(ref)).forEach(function (p) { found.push({ p: p, k: 'fresh' }); });
            }
          } catch (e) { found.push({ p: '', k: 'err' }); }
        }
        if (found.length) queue(t, found);
      });
    }
    function saved(out) { if (out && Array.isArray(out.blocks)) setTimeout(function () { try { check(out); } catch (e) { /* Diagnose darf nie stoeren */ } }, 0); }

    return { plugins: function () { return [plugin()]; }, api: { diagSaved: saved } };
  });
})(typeof self !== 'undefined' ? self : this);
