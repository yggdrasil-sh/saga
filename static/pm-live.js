// Gleichzeitig bearbeiten live (Saga 2.5, Tiptap-Umbau Etappe T5): Provider fuer WS /api/collab (docs/APP-API-COLLAB.md)
// und Bindung des Tiptap-Editors (static/pm-editor.js) an das gemeinsame Y.Doc. Meldet sich mit HKPmEditor.use an und
// uebernimmt editor.render(data) (api.liveRender): zuerst wird die Seite wie bisher aus Block-JSON gezeigt (Stufe 1),
// nach dem Abgleich (sync2) wird derselbe Editor mit Collaboration neu aufgebaut. Schreibmarke bleibt (Block-ID + Abstand).
//
// - Eine Verbindung je Tab, Seiten ueber sub/unsub; Wiederverbinden mit Wartezeit (1, 2, 4 ... 30 s), ping alle 25 s.
// - Rueckfall Stufe 1: live1 fehlt, Schliesscodes 4403/4400/4426, Verbindung weg, bye, Fehler beim sub, mehrfaches
//   reject. Dann Editor ohne Collaboration aus dem aktuellen Stand; nicht bestaetigte Live-Aenderungen speichert
//   pages.js per PATCH merge:1. Live wird nie zusaetzlich per PATCH gespeichert (pages.js fragt editor.live()).
// - Schema-Handshake (R1): Pruefsumme HKPmSchema.hash() beim sub; weicht sie ab (oder schreibt y-tiptap beim Laden
//   etwas um, z. B. unbekannte Knoten loeschen) -> nur lesen + Hinweis "Neu laden", dieser Client sendet NICHTS.
// - Rollen Lesen/Kommentieren, gesperrte Seite: nicht bearbeitbar, keine Updates (Awareness ja).
// - Fremde Cursor: CollaborationCaret mit eigener render-Funktion (CSP style-src 'self': nur Klassen hk-caret
//   hk-c-<palette>), Name/Farbe setzt der Server (Anzeigename, nie Anmeldename).
// - Undo: y-undo der Collaboration-Erweiterung (nur eigene Aenderungen; fremde kommen mit Herkunft REMOTE).
(function () {
  'use strict';
  if (!window.HKPmEditor || document.body.dataset.editor !== 'tiptap') return;
  var V = document.body.dataset.v || '';
  var REMOTE = 'hk-remote';                           // Herkunft eingehender Updates (nicht senden, nicht ins Undo)
  var KIND = { HELLO: 1, SUB: 2, UNSUB: 3, SYNC1: 4, SYNC2: 5, UPDATE: 6, AW: 7, REJECT: 8, BYE: 9, ERROR: 10, PING: 11, PONG: 12, ACK: 13 };
  var PALETTE = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  var PERMANENT = { 4403: 'off', 4400: 'origin', 4426: 'proto' };
  var X = null, YB = null, Y = null;                 // Bausteine des Kern-Editors, window.SagaY, SagaY.Y
  var FEAT = { live: null, at: 0 };                  // GET /api/version -> features enthaelt live1
  var W = { ws: null, open: false, fails: 0, off: false, delay: 1000, timer: null, ping: null, pong: null, hello: null, hold: 0 };
  var PG = null;                                      // offene Seite: {nid, doc, info, live, ro, lock, unacked, ...}
  var ST = { sent: 0, recv: 0, acked: 0, rejects: 0, loadDrops: 0, dropped: 0, binds: 0, fallbacks: 0, aw: 0 };
  var BOX = document.getElementById('pg-editor');

  function K() { return window.HKPages && HKPages.k; }
  function CB() { return X && X.cb(); }
  function curPage() { var c = CB(); return c && c.cur ? c.cur() : null; }
  function log() { if (window.HKPmLive && HKPmLive.debug && window.console) console.log.apply(console, ['pm-live'].concat([].slice.call(arguments))); }

  // =================================================================================================================
  // Laden: Live-Bundle (nach saga-pm.js), eigenes CSS, Schalter
  // =================================================================================================================
  var yLoading = null;
  function loadY() {
    if (window.SagaY) { YB = window.SagaY; Y = YB.Y; return Promise.resolve(); }
    if (yLoading) return yLoading;
    yLoading = new Promise(function (ok, fail) {
      var s = document.createElement('script'); s.async = false; s.src = '/static/vendor/tiptap/saga-y.js?v=' + V;
      s.onload = function () { YB = window.SagaY; Y = YB && YB.Y; if (Y) ok(); else fail(new Error('SagaY')); };
      s.onerror = function () { yLoading = null; fail(new Error('saga-y.js')); };
      document.body.appendChild(s);
    });
    return yLoading;
  }
  (function css() { var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/static/pm-live.css?v=' + V; document.head.appendChild(l); })();
  function features() {
    var now = Date.now();
    if (FEAT.live !== null && now - FEAT.at < 30000) return Promise.resolve(FEAT.live);
    return HK.api('GET', '/api/version').then(function (v) {
      FEAT.live = !!(v && (v.features || []).indexOf('live1') >= 0); FEAT.at = Date.now();
      if (FEAT.live) W.off = false;
      return FEAT.live;
    }, function () { return false; });
  }
  function eligible(c, data) {
    return !!(c && c.format === 'blocks' && typeof c.id === 'number' && c.id > 0 && c.kind !== 'database' && !W.off &&
              !(window.HKPmLive && HKPmLive.disabled) && !window.__hkNoLive && !legacy(data && data.blocks, 0));
  }
  // Seiten, die live (noch) anders aussaehen als mit Stufe 1, bleiben bei Stufe 1:
  // - Toggle im Altformat {text} (Editor.js vor 2.5): der Editor zeigt ihn umgewandelt (Titel + erster Absatz), der
  //   Live-Zustand fuehrt ihn unveraendert - bis die Seite einmal gespeichert ist;
  // - Kopie eines synchronisierten Blocks (data.src): ihren Inhalt setzt der Server nur beim GET ein, im Live-Zustand
  //   steht sie als synced_ref ohne Inhalt (APP-API-COLLAB 5.3) - Anzeige und Durchschreiben (sync1) gingen verloren.
  function legacy(list, dp) {
    if (!Array.isArray(list) || dp > 12) return false;
    return list.some(function (b) {
      var d = b && b.data; if (!d || typeof d !== 'object') return false;
      if (b.type === 'toggle' && typeof d.text === 'string' && !Array.isArray(d.blocks)) return true;
      if (b.type === 'synced' && (d.src || !Array.isArray(d.blocks))) return true;
      if (legacy(d.blocks, dp + 1)) return true;
      return Array.isArray(d.cols) && d.cols.some(function (c) { return c && legacy(c.blocks, dp + 1); });
    });
  }
  function schemaInfo() { var SC = X.SC; return { model: SC.MODEL, ver: SC.VERSION, hash: SC.hash() }; }

  // =================================================================================================================
  // Rahmen (art u8, note_id int64 BE, Teile varuint-Laenge + Bytes)
  // =================================================================================================================
  var TE = new TextEncoder(), TD = new TextDecoder();
  function vu(n, out) { while (n > 127) { out.push((n & 127) | 128); n = Math.floor(n / 128); } out.push(n); }
  function frame(kind, nid) {
    var parts = [].slice.call(arguments, 2).map(function (p) { return p instanceof Uint8Array ? p : TE.encode(JSON.stringify(p)); });
    var head = [], len = 9; parts.forEach(function (p) { var h = []; vu(p.length, h); p.h = h; len += h.length + p.length; });
    var b = new Uint8Array(len), dv = new DataView(b.buffer);
    b[0] = kind; dv.setBigInt64(1, BigInt(nid)); var i = 9;
    parts.forEach(function (p) { b.set(p.h, i); i += p.h.length; b.set(p, i); i += p.length; });
    void head;
    return b;
  }
  function parse(buf) {
    var b = new Uint8Array(buf); if (b.length < 9) throw new Error('kurz');
    var dv = new DataView(b.buffer, b.byteOffset), kind = b[0], nid = Number(dv.getBigInt64(1)), i = 9, parts = [];
    while (i < b.length) {
      var n = 0, s = 1, x;
      do { x = b[i++]; n += (x & 127) * s; s *= 128; } while (x & 128 && i < b.length);
      if (i + n > b.length) throw new Error('laenge');
      parts.push(b.subarray(i, i + n)); i += n;
    }
    return { kind: kind, nid: nid, parts: parts };
  }
  function js(p) { try { return p && p.length ? JSON.parse(TD.decode(p)) : null; } catch (e) { return null; } }

  // =================================================================================================================
  // Verbindung
  // =================================================================================================================
  function send(kind, nid) {
    if (!W.ws || !W.open) return false;
    try { W.ws.send(frame.apply(null, arguments)); return true; } catch (e) { return false; }
  }
  function connect() {
    if (W.ws || W.off || Date.now() < W.hold) return;
    clearTimeout(W.timer); W.timer = null;
    var ws;
    try { ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/collab'); }
    catch (e) { failed(0); return; }
    ws.binaryType = 'arraybuffer';
    W.ws = ws; W.open = false; W.hello = null;
    ws.onopen = function () { if (W.ws !== ws) return; W.open = true; beat(); };
    ws.onmessage = function (e) {
      if (W.ws !== ws || typeof e.data === 'string') return;
      var m; try { m = parse(e.data); } catch (er) { return; }
      beat();
      try { onFrame(m); } catch (er) { if (window.console) console.error('pm-live', er); }
    };
    ws.onclose = function (e) { if (W.ws !== ws) return; closed(e.code); };
    ws.onerror = function () { /* onclose folgt */ };
  }
  function beat() {                                    // ping bei Ruhe alle 25 s; ohne pong in 10 s: Verbindung tot
    clearTimeout(W.ping);
    W.ping = setTimeout(function () {
      if (!send(KIND.PING, 0)) return;
      clearTimeout(W.pong); W.pong = setTimeout(function () { if (W.ws) { try { W.ws.close(); } catch (e) { /* weg */ } closed(1006); } }, 10000);
    }, 25000);
  }
  function closed(code) {
    var ws = W.ws; W.ws = null; W.open = false; W.hello = null;
    clearTimeout(W.ping); clearTimeout(W.pong);
    if (ws) { ws.onclose = ws.onmessage = ws.onopen = null; }
    log('zu', code);
    if (PERMANENT[code]) {
      W.off = true; FEAT.live = null;                  // erst nach neuem GET /api/version wieder versuchen
      if (PG) toStufe1('closed-' + code, true);
      return;
    }
    if (PG) toStufe1('closed-' + code, false);
    failed(code);
  }
  function failed(code) {
    W.fails++;
    var d = code === 4429 ? 5000 + Math.random() * 25000 : code === 1012 ? 1000 + Math.random() * 4000 : W.delay;
    W.delay = Math.min(30000, W.delay * 2);
    if (!PG || !PG.want) return;                       // ohne offene Seite erst beim naechsten Oeffnen
    clearTimeout(W.timer);
    W.timer = setTimeout(function () { W.timer = null; if (PG && PG.want) connect(); }, Math.max(d, W.hold - Date.now()));
  }

  function onFrame(m) {
    var k = m.kind, p = m.parts;
    if (k === KIND.PONG) { clearTimeout(W.pong); return; }
    if (k === KIND.HELLO && m.nid === 0) {
      W.hello = js(p[0]) || {}; W.fails = 0; W.delay = 1000;
      if (PG && PG.want) subscribe();
      return;
    }
    if (!PG || m.nid !== PG.nid) return;
    if (k === KIND.HELLO) return onHello(js(p[0]) || {});
    if (k === KIND.SYNC1) return;                      // frischer Zustand (sub ohne Vektor): nichts nachzureichen
    if (k === KIND.SYNC2 || k === KIND.UPDATE) return onRemote(p[0], k === KIND.SYNC2);
    if (k === KIND.ACK) return onAck(p[0] ? TD.decode(p[0]) : '');
    if (k === KIND.REJECT) return onReject(js(p[0]) || {}, p[1]);
    if (k === KIND.BYE) return onBye((js(p[0]) || {}).reason);
    if (k === KIND.ERROR) return onError((js(p[0]) || {}).code);
    if (k === KIND.AW) return onAw(js(p[0]) || {});
  }

  // =================================================================================================================
  // Seite abonnieren
  // =================================================================================================================
  function newDoc() {
    var d = new Y.Doc();
    while (d.clientID === 1) d = new Y.Doc();         // 1 = deterministischer Anfangszustand des Servers
    d.on('update', function (u, origin) { onLocal(d, u, origin); });
    return d;
  }
  function startPage(nid) {
    PG = { nid: nid, want: true, doc: null, info: null, live: false, ro: true, lock: false, unacked: [], held: [], seq: 0, rej: [],
           synced: false, aw: new Map(), prov: null, loading: false };
    var mine = PG;
    features().then(function (on) {                 // Live-Bundle nur laden, wenn live ueberhaupt an ist
      if (PG !== mine || !on) { if (PG === mine) mine.want = false; return null; }
      return loadY().then(function () { return true; });
    }).then(function (go) {
      if (PG !== mine || !go) return;
      var sc = schemaInfo(), srv = W.hello && W.hello.schema;
      if (srv && srv.hash !== sc.hash) { schemaLock(); return; }
      if (W.ws && W.open && W.hello) subscribe(); else connect();
    }).catch(function () { if (PG === mine) mine.want = false; });
  }
  function subscribe() {
    if (!PG || !PG.want || PG.subbed) return;
    var sc = schemaInfo(), srv = W.hello && W.hello.schema;
    if (srv && (srv.hash !== sc.hash || srv.model !== sc.model || srv.ver !== sc.ver)) { schemaLock(); return; }
    PG.doc = newDoc(); PG.synced = false; PG.unacked = []; PG.subbed = true;
    send(KIND.SUB, PG.nid, { schema: sc, caps: 'inline2,cols6,merge1,sync1' });
  }
  function stopPage() {
    var pg = PG; PG = null;
    if (!pg) return;
    if (pg.live && pg.held.length) { PG = pg; flushHeld(pg); PG = null; }
    if (pg.subbed) send(KIND.UNSUB, pg.nid);
    destroyProv(pg);
    clearTimeout(pg.awT); clearTimeout(pg.retryT); clearTimeout(pg.goT); clearTimeout(pg.leaveT); clearTimeout(pg.fixT);
    if (BOX) BOX.classList.remove('pm-live');
    hint(false);
  }
  function onHello(h) {
    if (h.update) {                                     // Rolle/Sperre waehrend des Abos geaendert
      var ro = !!h.ro || !(h.role === 'owner' || h.role === 'edit');
      if (PG.info) { PG.info.role = h.role; PG.info.ro = h.ro; PG.info.why = h.why; }
      if (ro !== PG.ro) { PG.ro = ro; if (PG.live) rebind(null, false); }
      return;
    }
    PG.info = h;
    if (h.why === 'schema') { schemaLock(); return; }
    PG.ro = !!h.ro || !(h.role === 'owner' || h.role === 'edit');
  }
  function onRemote(u, isSync2) {
    if (!u || !PG.doc) return;
    ST.recv++;
    Y.applyUpdate(PG.doc, u, REMOTE);
    if (PG.doc.store.pendingStructs || PG.doc.store.pendingDs) send(KIND.SYNC1, PG.nid, Y.encodeStateVector(PG.doc));   // Luecke
    if (isSync2 && !PG.synced) { PG.synced = true; goLive(); }
  }

  // =================================================================================================================
  // Editor umschalten (Stufe 1 <-> live), Schreibmarke ueber Block-ID + Abstand
  // =================================================================================================================
  function caret() {
    var ed = X.ed(); if (!ed) return null;
    var sel = ed.state.selection, b = X.blockAt(sel.head);
    return b && b.node.attrs.id ? { id: b.node.attrs.id, off: sel.head - b.pos, focus: ed.view.hasFocus() } : null;
  }
  function restore(c) {
    var ed = X.ed(); if (!c || !ed) return;
    var x = X.findId(c.id); if (!x) return;
    try {
      var tr = ed.state.tr; X.setSel(tr, Math.min(x.pos + c.off, x.pos + x.node.nodeSize - 1));
      tr.setMeta('addToHistory', false); ed.view.dispatch(tr);
      if (c.focus) ed.view.focus();
    } catch (e) { /* Block anders gebaut */ }
  }
  // Seite ist geladen (Stufe 1) und der Zustand ist da: eigene, noch nicht gespeicherte Eingaben zuerst per PATCH
  // speichern (der Server uebernimmt sie in den Raum und schickt sie als update), dann live binden
  function goLive() {
    var pg = PG, k = K();
    (k && k.save ? k.save() : Promise.resolve()).catch(function () {}).then(function () {
      if (PG !== pg || !pg.synced || pg.lock) return;
      var c = curPage(); if (!c || c.id !== pg.nid) return;
      // waehrenddessen weitergetippt (bzw. Speichern/Laden laeuft): erst wenn alles gespeichert ist - der Neuaufbau aus
      // dem Live-Zustand wuerde Eingaben verwerfen, die der Server noch nicht hat. Danach kommt der Stand als update.
      if (k && k.busy && k.busy()) { clearTimeout(pg.goT); pg.goT = setTimeout(goLive, 400); return; }
      bind();
    });
  }
  function bind(keepCaret) {
    var pg = PG, c = keepCaret || caret(), cb = CB();
    var editable = !pg.ro && !(cb && cb.readOnly && cb.readOnly());
    destroyProv(pg);
    pg.prov = makeProv(pg);
    var exts = [YB.Collaboration.configure({ document: pg.doc, field: 'prosemirror' }), caretFix(), noMigration(), syncedGuard()];
    exts.push(YB.CollaborationCaret.configure({ provider: pg.prov, user: { name: '', color: '#000000' }, render: renderCaret, selectionRender: renderSel }));
    X.live({ editable: editable, exts: exts });
    pg.loading = true; var drops = ST.loadDrops;
    try {
      X.build({ blocks: (X.ctx() && X.saveNow().blocks) || [] });
      // eine leere Transaktion: y-tiptap gleicht dabei PM -> Yjs ab - schreibt es hier etwas, passt das Schema nicht (R1)
      var ed = X.ed(); if (ed) ed.view.dispatch(ed.state.tr.setMeta('hkLiveProbe', true));
    } finally { pg.loading = false; }
    if (ST.loadDrops !== drops) { schemaLock(); return; }
    passthrough();
    pg.known = {}; X.ed().state.doc.descendants(function (n) { if (VAL[n.type.name] && n.attrs.id) pg.known[n.attrs.id] = 1; return true; });
    pg.live = true; ST.binds++;
    if (BOX) BOX.classList.add('pm-live');
    restore(c);
    aw();
    log('live', pg.nid, editable);
  }
  // y-tiptap 3.0.12 haelt die eigene Schreibmarke nach einer fremden Aenderung im SELBEN Absatz an ihrer absoluten Stelle
  // (Heuristik "nach Strukturaenderung falsch aufgeloest" greift, sobald sich der Absatztext aendert) - fremder Text vor der
  // Marke schob sie so mitten in die eigene Eingabe. Korrektur: Auswahl aus den relativen Positionen von vor der fremden
  // Aenderung (binding.beforeTransactionSelection), wenn sie sauber aufloesen. Undo/Redo und Knoten-Auswahl unveraendert.
  function caretFix() {
    var S0 = X.P.pm.state, YT = YB.ytiptap;
    return X.P.core.Extension.create({ name: 'hkLiveCaret', addProseMirrorPlugins: function () {
      return [new S0.Plugin({ key: new S0.PluginKey('hkLiveCaret'), appendTransaction: function (trs, o, n) {
        var tr = trs[trs.length - 1], m = tr.getMeta('y-sync$');
        if (!m || !m.isChangeOrigin || m.isUndoRedoOperation || !tr.docChanged) return null;
        var ys = YT.ySyncPluginKey.getState(n), bd = ys && ys.binding, rel = bd && bd.beforeTransactionSelection;
        if (!rel || rel.type !== 'text' || !rel.anchor || !rel.head || !(n.selection instanceof S0.TextSelection)) return null;
        try {
          var an = YT.relativePositionToAbsolutePosition(ys.doc, ys.type, rel.anchor, bd.mapping);
          var hd = YT.relativePositionToAbsolutePosition(ys.doc, ys.type, rel.head, bd.mapping);
          if (an == null || hd == null || YT.isMisresolvedTextPosition(ys.doc, rel.anchor, an) || YT.isMisresolvedTextPosition(ys.doc, rel.head, hd)) return null;
          if (an === n.selection.anchor && hd === n.selection.head) return null;
          var ra = n.doc.resolve(an), rh = n.doc.resolve(hd);
          if (!ra.parent.isTextblock || !rh.parent.isTextblock) return null;
          return n.tr.setSelection(S0.TextSelection.between(ra, rh)).setMeta('addToHistory', false).setMeta('hkRemote', true);
        } catch (e) { return null; }
      } })];
    } });
  }
  // y-tiptap 3.0.12 schreibt bei Strukturaenderungen (Block verschieben, Spalten) Positions-Migrationen in eine eigene Wurzel
  // "__tiptap_position_migrations:..." - der Server erlaubt nur prosemirror/title/meta (APP-API-COLLAB 5.1, reject type)
  // und der Zustand wuerde mit jeder Verschiebung wachsen. Die Migration wird vor dem Abgleich PM -> Yjs verworfen
  // (Meta mit snapshot null: ySync setzt positionMigration dann auf null). Folge: relative Positionen ANDERER (Cursor) in
  // einem verschobenen Block loesen nach der Verschiebung neu auf - die eigene Schreibmarke haelt ProseMirror selbst.
  function noMigration() {
    var S0 = X.P.pm.state, YT = YB.ytiptap;
    return X.P.core.Extension.create({ name: 'hkLiveNoMigration', addProseMirrorPlugins: function () {
      return [new S0.Plugin({ key: new S0.PluginKey('hkLiveNoMig'), appendTransaction: function (trs, o, n) {
        var ys = YT.ySyncPluginKey.getState(n);
        if (!ys || !ys.positionMigration) return null;
        return n.tr.setMeta(YT.ySyncPluginKey, { snapshot: null, prevSnapshot: null }).setMeta('addToHistory', false);
      } })];
    } });
  }
  // Durchreichung (T-E4) auch live: Bloecke, deren Inhalt dem zuletzt geladenen Stand (GET bzw. Stufe-1-Stand) gleicht,
  // an ihr Original binden - save() liefert sie dann byte-gleich (Rueckfall Stufe 1, Fassade). Nur bei gleicher Normalform.
  function likeIds() { return PG && PG.like || null; }
  function passthrough() {
    var ed = X.ed(), cb = CB(), c = cb && cb.cur ? cb.cur() : null, src = null;
    try { src = c && typeof c.body === 'string' ? JSON.parse(c.body || '{"blocks":[]}') : null; } catch (e) { src = null; }
    if (!ed || !src || !Array.isArray(src.blocks)) return;
    var byId = {}; src.blocks.forEach(function (b) { if (b && b.id != null) byId[b.id] = b; });
    var AD = X.AD, sch = ed.schema, ctx = X.ctx();
    // Schluesselreihenfolge geaenderter Bloecke wie geladen (pm-adapter like); Vorlage an der offenen Seite, ctx haelt
    // nur die Funktion (ein alter Editor haelt so keinen alten Stand fest)
    if (PG) PG.like = AD.blockIds(src.blocks);
    ctx.like = likeIds;
    ed.state.doc.forEach(function (n) {
      var b = n.attrs && n.attrs.id != null ? byId[n.attrs.id] : null; if (!b) return;
      try {
        var want = AD.pmToBlock(sch.nodeFromJSON(AD.blockToPm(b, { orig: null })), {}), have = AD.pmToBlock(n, {});
        // Nextcloud-Autor steht im Live-Zustand als Handle (~..., Server-Maske), im GET fuer die eigene Person als Name
        if (want.type === 'nextcloud' && have.data && want.data && /^~/.test(String(have.data.author || '')) && want.data.author) {
          have = Object.assign({}, have, { data: Object.assign({}, have.data, { author: want.data.author }) });
        }
        if (AD.stable(want) !== AD.stable(have)) return;
        AD.bind(sch.topNodeType.create(null, [n]), { blocks: [b] }, ctx);
        X.markOrig([b]);
      } catch (e) { /* nicht vergleichbar: ohne Durchreichung */ }
    });
  }
  // Neue Kopie eines synchronisierten Blocks (synced_ref): ihren Inhalt und die Kopie-Leiste liefert nur die Antwort auf
  // ein PATCH (HKMention.saved) - diese eigene Aenderung geht deshalb nicht live hinaus, sondern die Seite wechselt vorher
  // auf Stufe 1 (pages.js speichert sie wie bisher).
  // Neue Bloecke, die beim Speichern wegfielen (validate: Bild/Datei ohne Datei, Einbetten ohne Link ...), lehnte der
  // Server ab (forbidden). Solange es so einen Block gibt, haelt onLocal die eigenen Updates zurueck und schickt sie
  // gesammelt, sobald er fertig (Datei da) oder geloescht ist; beim Verlassen der Seite wird er vorher entfernt.
  var VAL = { image: 1, attaches: 1, embed: 1, nextcloud: 1 };
  function refs(doc) { var n = 0; doc.descendants(function (x) { if (x.type.name === 'synced_ref') n++; return true; }); return n; }
  function media(doc) { var n = 0; doc.descendants(function (x) { if (VAL[x.type.name]) n++; return true; }); return n; }
  function syncedGuard() {
    var S0 = X.P.pm.state;
    return X.P.core.Extension.create({ name: 'hkLiveSynced', addProseMirrorPlugins: function () {
      return [new S0.Plugin({ key: new S0.PluginKey('hkLiveSynced'), appendTransaction: function (trs, o, n) {
        var pg = PG; if (!pg || !pg.live || pg.leave) return null;
        if (!trs.some(function (t) { var m = t.getMeta('y-sync$'); return t.docChanged && !(m && m.isChangeOrigin); })) return null;
        if (refs(n.doc) > refs(o.doc)) pg.leave = 'synced';
        return null;
      } })];
    } });
  }
  // Widget frisch per "/" eingefuegt: data {kind} - Stufe 1 ergaenzt v/cfg der Server beim Speichern, live waere das
  // reject sanitize. Wie WidgetTool (pages.js) bzw. pm-tools-daten.js (live) ergaenzen, als eigene Transaktion nach dem
  // Einfuegen (die Node-View ist dann schon da und oeffnet ihr Einrichten-Panel wie gewohnt).
  function rawWidgets(doc) {
    var W_ = K() && K().tools, out = [];
    if (!W_ || !W_.wgKinds) return out;
    doc.descendants(function (n, pos) {
      if (n.type.name !== 'widget') return true;
      var d = n.attrs.data || {}, kind = d.kind || 'metric';
      if (W_.wgKinds.indexOf(kind) >= 0 && (d.v !== 1 || !d.cfg || typeof d.cfg !== 'object' || !d.kind)) out.push({ pos: pos, node: n, kind: kind });
      return false;
    });
    return out;
  }
  function fixWidgets() {
    var ed = X.ed(), W_ = K() && K().tools, list = ed ? rawWidgets(ed.state.doc) : [];
    if (!list.length) return false;
    var tr = ed.state.tr;
    list.forEach(function (x) {
      var d = JSON.parse(JSON.stringify(x.node.attrs.data || {}));
      d.kind = x.kind; d.v = 1; d.cfg = Object.assign(W_.wgDefaults(x.kind), d.cfg && typeof d.cfg === 'object' ? d.cfg : {});
      tr.setNodeMarkup(x.pos, null, Object.assign({}, x.node.attrs, { data: d }));
    });
    tr.setMeta('addToHistory', false);
    ed.view.dispatch(tr);
    return true;
  }
  // nur Bloecke, die es beim Binden noch nicht gab (vorhandene fremde Nextcloud-Bloecke u. ae. sind kein "unfertig")
  function unfinished() {
    var ed = X.ed(), pg = PG, hit = false; if (!ed || !pg || !pg.known) return false;
    if (rawWidgets(ed.state.doc).length) {
      if (!pg.fixT) pg.fixT = setTimeout(function () { pg.fixT = null; if (PG === pg && pg.live) fixWidgets(); }, 0);
      return true;
    }
    ed.state.doc.descendants(function (n) {
      if (hit) return false;
      if (VAL[n.type.name] && !pg.known[n.attrs.id] && !X.validate(X.blockJson(n))) hit = true;
      return !VAL[n.type.name];
    });
    return hit;
  }
  // beim Verlassen: unfertige neue Bloecke entfernen (wie Stufe 1, die sie nie speichert), Zurueckgehaltenes senden
  function flushHeld(pg) {
    if (!pg.held.length) return;
    var ed = X.ed();
    try {
      fixWidgets();
      if (ed && unfinished()) {
        var tr = ed.state.tr, del = [];
        ed.state.doc.descendants(function (n, pos) { if (VAL[n.type.name] && !pg.known[n.attrs.id] && !X.validate(X.blockJson(n))) { del.push([pos, n.nodeSize]); return false; } return true; });
        del.reverse().forEach(function (d) { tr.delete(d[0], d[0] + d[1]); });
        if (del.length) { tr.setMeta('addToHistory', false); ed.view.dispatch(tr); }
      }
    } catch (e) { /* weg */ }
    if (pg.held.length && !unfinished()) { var u = Y.mergeUpdates(pg.held); pg.held = []; sendUpdate(pg, u); }
  }
  function rebind(full, keep) {                       // Zustand des Servers uebernehmen (reject, Rolle), Editor neu
    var pg = PG, c = caret();
    var rest = keep ? pg.unacked.slice().concat(pg.held.map(function (u) { return { u: u }; })) : [];
    pg.held = [];
    var nd = newDoc();
    if (full) Y.applyUpdate(nd, full, REMOTE); else Y.applyUpdate(nd, Y.encodeStateAsUpdate(pg.doc), REMOTE);
    var lost = false;
    if (rest.length) {                                 // eigene unbestaetigte Aenderungen, die nicht am Abgelehnten haengen
      var probe = new Y.Doc(); Y.applyUpdate(probe, Y.encodeStateAsUpdate(nd));
      rest.forEach(function (x) { Y.applyUpdate(probe, x.u); });
      if (probe.store.pendingStructs || probe.store.pendingDs) lost = true;
      else rest.forEach(function (x) { Y.applyUpdate(nd, x.u, REMOTE); });
    }
    var oldDoc = pg.doc; pg.doc = nd; pg.unacked = [];
    try { oldDoc.destroy(); } catch (e) { /* egal */ }
    bind(c);
    if (rest.length && !lost && PG === pg && !pg.ro && !pg.lock) {
      var u = Y.mergeUpdates(rest.map(function (x) { return x.u; }));
      if (unfinished()) pg.held = [u]; else sendUpdate(pg, u);
    }
    return !lost;
  }
  // Rueckfall auf Stufe 1: derselbe Stand ohne Collaboration; nicht bestaetigte Aenderungen speichert pages.js (PATCH merge)
  function toStufe1(why, final, dirty) {
    var pg = PG; if (!pg) return;
    var wasLive = pg.live, pend = pg.unacked.length > 0 || !!dirty;
    if (final && pg.subbed) send(KIND.UNSUB, pg.nid);
    pg.live = false; pg.subbed = false; pg.synced = false;
    if (final) pg.want = false;
    destroyProv(pg);
    if (BOX) BOX.classList.remove('pm-live');
    if (!wasLive) return;
    ST.fallbacks++;
    var c = caret(), json = X.raw();
    X.live(null);
    X.build(json);
    X.unmarkInvalid(json.blocks);
    restore(c);
    if (pg.held.length) pend = true;
    pg.unacked = []; pg.held = [];
    if (pend) { var k = K(); if (k && k.dirty) k.dirty(); }
    log('stufe1', why, pend);
  }
  // Abweichendes Schema (R1) bzw. y-tiptap wollte beim Laden umschreiben: nur lesen, nichts senden, Hinweis "Neu laden"
  function schemaLock() {
    var pg = PG; if (!pg) return;
    pg.lock = true; pg.want = false;
    if (pg.subbed) { send(KIND.UNSUB, pg.nid); pg.subbed = false; }
    destroyProv(pg);
    var json = null;
    if (pg.live || X.live()) { pg.live = false; X.live(null); }
    var data = X.ctx() ? X.saveNow() : null;
    var cb = CB(), c = cb && cb.cur ? cb.cur() : null;
    try { if (c && typeof c.body === 'string') json = JSON.parse(c.body || '{"blocks":[]}'); } catch (e) { json = null; }
    X.lock(true);
    X.build(json || data || { blocks: [] });
    var t = document.getElementById('pg-title'); if (t) t.readOnly = true;
    if (BOX) BOX.classList.remove('pm-live');
    hint(true);
    log('schema');
  }
  function hint(on) {
    var bar = document.getElementById('pm-live-hint');
    if (!on) { if (bar) bar.remove(); return; }
    var page = document.getElementById('page'); if (bar || !page) return;
    bar = HK.el('div', 'pg-conflict pm-live-hint'); bar.id = 'pm-live-hint'; bar.setAttribute('role', 'alert');
    bar.appendChild(HK.el('span', 'pgc-t', T('Diese Seite nutzt eine andere Editor-Version – bitte neu laden. Bis dahin nur lesen.')));
    var b = HK.el('button', 'btn btn-primary', T('Neu laden')); b.type = 'button';
    b.addEventListener('click', function () { location.reload(); });
    bar.appendChild(b); page.insertBefore(bar, page.firstChild);
  }

  // =================================================================================================================
  // Eigene Aenderungen senden, Bestaetigung, Ablehnung
  // =================================================================================================================
  function onLocal(d, u, origin) {
    if (origin === REMOTE) return;
    var pg = PG;
    if (!pg || pg.doc !== d) return;
    if (pg.loading) { ST.loadDrops++; return; }       // beim Laden geschrieben (R1): nie hinaus
    if (pg.leave) {                                    // s. syncedGuard: nicht senden, auf Stufe 1 wechseln
      ST.dropped++;
      if (!pg.leaveT) pg.leaveT = setTimeout(function () {
        if (PG !== pg) return;
        toStufe1(pg.leave, true, true);
      }, 0);
      return;
    }
    if (!pg.live || pg.ro || pg.lock || !W.open) { ST.dropped++; return; }
    if (pg.held.length || unfinished()) {
      pg.held.push(u);
      if (unfinished()) { ST.held = (ST.held || 0) + 1; return; }
      u = Y.mergeUpdates(pg.held); pg.held = [];
    }
    sendUpdate(pg, u);
  }
  function sendUpdate(pg, u) {
    var ref = 'u' + (++pg.seq);
    pg.unacked.push({ ref: ref, u: u });
    ST.sent++;
    send(KIND.UPDATE, pg.nid, u, TE.encode(ref));
  }
  function onAck(ref) {
    var i = PG.unacked.findIndex(function (x) { return x.ref === ref; });
    if (i >= 0) PG.unacked.splice(0, i + 1);           // in Reihenfolge bestaetigt
    ST.acked++;
  }
  function onReject(r, full) {
    var pg = PG, code = r.code || '';
    ST.rejects++;
    log('reject', code, r.ref);
    if (!full) return;
    if (code === 'pending') {                          // Luecke: Server-Stand dazu, alles Unbestaetigte zusammen neu senden
      Y.applyUpdate(pg.doc, full, REMOTE);
      if (pg.unacked.length) { var all = Y.mergeUpdates(pg.unacked.map(function (x) { return x.u; })); pg.unacked = []; sendUpdate(pg, all); }
      return;
    }
    if (code === 'retry') {
      Y.applyUpdate(pg.doc, full, REMOTE);
      clearTimeout(pg.retryT);
      pg.retryT = setTimeout(function () {
        if (PG !== pg || !pg.unacked.length) return;
        var all = Y.mergeUpdates(pg.unacked.map(function (x) { return x.u; })); pg.unacked = []; sendUpdate(pg, all);
      }, 1000 + Math.random() * 4000);
      return;
    }
    if (code === 'readonly') {
      pg.ro = true;
      if (pg.live) rebind(full, false);
      HK.toast(T('Änderung nicht übernommen – du kannst diese Seite gerade nicht bearbeiten.'));
      return;
    }
    // Inhalt abgelehnt (type/sanitize/signature/forbidden/size/title/invalid/client): Server-Stand, eigene unabhaengige
    // Eingaben behalten; mehrfach in kurzer Zeit -> Stufe 1 (dort greifen die gewohnten Pruefungen beim Speichern)
    var now = Date.now();
    pg.rej = pg.rej.filter(function (t) { return now - t < 60000; }); pg.rej.push(now);
    pg.unacked = pg.unacked.filter(function (x) { return x.ref !== r.ref; });
    if (pg.rej.length >= 3) {
      if (pg.live) rebind(full, false);
      toStufe1('rejects', true);
      HK.toast(T('Änderung nicht übernommen.'));
      return;
    }
    var kept = pg.live ? rebind(full, code !== 'client') : true;
    HK.toast(kept ? T('Änderung nicht übernommen.') : T('Änderung nicht übernommen – deine letzten Eingaben ließen sich nicht behalten.'));
  }
  function onBye(reason) {
    var pg = PG;
    log('bye', reason);
    pg.subbed = false;
    if (reason === 'off') { W.off = true; FEAT.live = null; }
    toStufe1('bye-' + reason, reason !== 'restart');
    if (reason === 'restart' && PG === pg) { pg.want = true; }      // nach dem Neustart neu verbinden (closed -> failed)
  }
  function onError(code) {
    log('error', code);
    if (code === 'nosub') return;
    PG.subbed = false;
    toStufe1('error-' + code, true);
  }

  // =================================================================================================================
  // Awareness (eigenes, kleines Gegenstueck zu y-protocols/awareness: der Server spricht sein eigenes aw-Format)
  // =================================================================================================================
  var AW_BASE = 0x100000000;                          // Schluessel fremder Zustaende: nie gleich einer Yjs-clientID
  function makeProv(pg) {
    var L = {}, states = new Map(), id = pg.doc.clientID;
    function emit(ev, a) { (L[ev] || []).slice().forEach(function (f) { try { f(a, 'local'); } catch (e) { /* Hoerer kaputt */ } }); }
    var awareness = {
      clientID: id, states: states, meta: new Map(),
      getStates: function () { return states; },
      getLocalState: function () { return states.get(id) || null; },
      setLocalState: function (s) { if (s == null) states.delete(id); else states.set(id, s); emit('change', { added: [], updated: [id], removed: [] }); emit('update', { added: [], updated: [id], removed: [] }); aw(); },
      setLocalStateField: function (k, v) { var s = Object.assign({}, states.get(id) || {}); s[k] = v; awareness.setLocalState(s); },
      on: function (ev, f) { (L[ev] = L[ev] || []).push(f); },
      off: function (ev, f) { L[ev] = (L[ev] || []).filter(function (x) { return x !== f; }); },
      destroy: function () { L = {}; states.clear(); },
      _remote: function (sid, st) {
        var key = AW_BASE + sid, had = states.has(key);
        if (st) states.set(key, st); else states.delete(key);
        var ch = { added: !had && st ? [key] : [], updated: had && st ? [key] : [], removed: had && !st ? [key] : [] };
        emit('change', ch); emit('update', ch);
      }
    };
    pg.aw.forEach(function (st, sid) { states.set(AW_BASE + sid, st); });
    // provider fuer CollaborationCaret/UniqueID: kein "synced"-Ereignis (UniqueID vergaebe sonst IDs im ganzen Dokument)
    return { awareness: awareness, on: function () {}, off: function () {}, doc: pg.doc };
  }
  function destroyProv(pg) { if (pg && pg.prov) { try { pg.prov.awareness.destroy(); } catch (e) { /* egal */ } pg.prov = null; } }
  function onAw(m) {
    var pg = PG, s = m.state, sid = m.sid;
    if (typeof sid !== 'number') return;
    var st = null;
    if (s && s.cursor) {
      var pal = PALETTE.indexOf(s.color) >= 0 ? s.color : 'gray';
      st = { cursor: s.cursor, user: { name: String(s.name || '').slice(0, 80), color: '#000000', pal: pal } };
    }
    if (st) pg.aw.set(sid, st); else pg.aw.delete(sid);
    if (pg.prov) pg.prov.awareness._remote(sid, st);
  }
  function aw() {                                     // eigene Awareness gebuendelt (hoechstens alle 100 ms) senden
    var pg = PG; if (!pg || !pg.live || pg.awT) return;
    pg.awT = setTimeout(function () {
      pg.awT = null;
      if (PG !== pg || !pg.live || !pg.prov) return;
      var ls = pg.prov.awareness.getLocalState() || {}, ed = X.ed(), b = null;
      try { var cb = CB(); b = ed && ed.view.hasFocus() && X.ed() ? (curFocus()) : null; void cb; } catch (e) { b = null; }
      var msg = { cursor: ls.cursor || null, block: b };
      var sig = JSON.stringify(msg); if (sig === pg.awSig) return;
      pg.awSig = sig; ST.aw++;
      send(KIND.AW, pg.nid, msg);
    }, 100);
  }
  function curFocus() { var ed = X.ed(), sel = ed.state.selection, b = X.blockAt(sel.head); return b && b.node.attrs.id ? String(b.node.attrs.id) : null; }
  function renderCaret(u) {
    var pal = PALETTE.indexOf(u.pal) >= 0 ? u.pal : 'gray';
    var c = document.createElement('span'); c.className = 'hk-caret hk-c-' + pal;
    var l = document.createElement('span'); l.className = 'hk-caret-l';
    var n = document.createElement('span'); n.className = 'hk-caret-n'; n.textContent = u.name || '?';
    l.appendChild(n); c.appendChild(l);
    return c;
  }
  function renderSel(u) { var pal = PALETTE.indexOf(u.pal) >= 0 ? u.pal : 'gray'; return { 'class': 'hk-csel hk-bg-' + pal }; }

  // =================================================================================================================
  // Einstieg: editor.render(data) aus pages.js (ueber pm-editor.js api.liveRender)
  // =================================================================================================================
  function liveRender(data) {
    stopPage();
    X.live(null); X.lock(false);
    var t = document.getElementById('pg-title'); if (t && t.readOnly && !(CB() && CB().readOnly && CB().readOnly())) t.readOnly = false;
    X.build(data);
    var c = curPage();
    if (eligible(c, data)) startPage(c.id);
    return Promise.resolve();
  }
  function liveDestroy() { stopPage(); if (X) { X.live(null); X.lock(false); } }

  HKPmEditor.use(function (x) {
    X = x;
    return { api: { liveRender: liveRender, liveDestroy: liveDestroy } };
  });

  // Tests/Diagnose
  window.HKPmLive = {
    debug: false, disabled: false,
    state: function () {
      return { mode: PG ? (PG.lock ? 'lock' : PG.live ? 'live' : 'stufe1') : 'aus', nid: PG && PG.nid, ro: !!(PG && PG.ro), live: !!(PG && PG.live),
               lock: !!(PG && PG.lock), unacked: PG ? PG.unacked.length : 0, held: PG ? PG.held.length : 0, ws: !!(W.ws && W.open), off: W.off,
               role: PG && PG.info ? PG.info.role : null, me: PG && PG.info ? PG.info.name : null, stats: Object.assign({}, ST) };
    },
    doc: function () { return PG && PG.doc; },
    // Verbindung kappen (Tests): ms lang kein Wiederverbinden
    kill: function (ms) { W.hold = Date.now() + (ms || 0); if (W.ws) { var ws = W.ws; try { ws.close(); } catch (e) { /* weg */ } closed(1006); } }
  };
})();
