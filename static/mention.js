// 2.5 (P6-5/P6-6): @Personen im Editor und in Kommentaren, synchronisierte Bloecke (Werkzeug "synced").
// Wird VOR static/pages.js geladen (Werkzeug window.HKSyncedTool) und nutzt zur Laufzeit dessen Bausteine (window.HKPages.k).
// Alles ueber DOM-APIs/textContent - nie Namen als HTML.
//
// Erwaehnung: <span class="hk-mention" data-u="REF">Name</span>, REF = Handle '~…' bzw. Prinzipal '@name@host'. Der Text
// ist nur Rueckfall - angezeigt wird der aktuelle Name aus dem Woerterbuch "people" (GET Seite) per CSS (data-name), damit
// der gespeicherte Inhalt unveraendert bleibt.
// Synchronisierter Block: Original {sid, blocks}, Kopie {sid} - der Server setzt beim Lesen blocks + src ein.
(function () {
  'use strict';
  function K() { return window.HKPages && window.HKPages.k; }
  var el = function (t, c, x) { return HK.el(t, c, x); };
  var PEOPLE = {};                                          // ref -> {name, me, gone, remote}

  // --- Anzeige der Erwaehnungen ------------------------------------------------------------------------------------------
  function setPeople(pm) {
    if (pm && typeof pm === 'object') Object.keys(pm).forEach(function (r) { if (pm[r] && typeof pm[r] === 'object') PEOPLE[r] = pm[r]; });
    decorate();
  }
  function decorate(root) {
    [].forEach.call((root || document).querySelectorAll('span.hk-mention[data-u]'), function (s) {
      var p = PEOPLE[s.getAttribute('data-u')];
      s.contentEditable = 'false';
      s.classList.toggle('me', !!(p && p.me));
      s.classList.toggle('gone', !!(p && p.gone));
      if (p && p.name) {                                    // Schriftgroesse des Umfelds (Ueberschrift, Absatz) fuer ::before
        s.dataset.name = '@' + p.name; s.title = p.name;
        if (s.parentElement) s.style.setProperty('--hk-fs', getComputedStyle(s.parentElement).fontSize);
      } else { delete s.dataset.name; }
    });
  }
  new MutationObserver(function () { clearTimeout(decorate.t); decorate.t = setTimeout(function () { decorate(); }, 120); })
    .observe(document.documentElement, { childList: true, subtree: true });

  // --- @-Menue: Personen ---------------------------------------------------------------------------------------------
  // static/keys.js fragt hier nach (HKMention.options(q, cb)) und ruft pick(item, range) fuer Personen auf.
  var cache = { id: null, q: null, list: [], t: 0 }, shareHits = { q: null, list: [] };
  function curId() { var k = K(), c = k && k.cur(); return c ? c.id : null; }
  function load(q) {
    var id = curId(); if (!id) return Promise.resolve([]);
    if (cache.id === id && cache.q === q && Date.now() - cache.t < 15000) return Promise.resolve(cache.list);
    return HK.api('GET', '/api/notes/' + id + '/people?q=' + encodeURIComponent(q || '')).then(function (d) {
      cache = { id: id, q: q, list: d.people || [], t: Date.now() };
      (d.people || []).forEach(function (p) { PEOPLE[p.ref] = Object.assign({}, PEOPLE[p.ref] || {}, p); });
      return cache.list;
    }, function () { return []; });
  }
  function shareTargets(q) {
    var k = K(), c = k && k.cur();
    if (!c || c.role !== 'owner' || q.length < 2 || c.id < 0) return Promise.resolve([]);       // teilen darf nur der Eigentuemer
    if (shareHits.q === q) return Promise.resolve(shareHits.list);
    return HK.api('GET', '/api/share/targets?q=' + encodeURIComponent(q)).then(function (d) {
      shareHits = { q: q, list: (d.users || []).map(function (u) { return { ref: u.username, name: u.name, sub: u.sub, share: true }; }) };
      return shareHits.list;
    }, function () { return []; });
  }
  function options(q) {
    var nq = String(q || '').toLowerCase();
    return Promise.all([load(nq), shareTargets(nq)]).then(function (r) {
      var have = {}, out = [];
      r[0].slice(0, 6).forEach(function (p) { have[p.ref] = 1; out.push({ group: T('Personen'), ic: p.me ? '🙋' : '👤', t: p.name + (p.me ? ' (' + T('ich') + ')' : ''), person: p }); });
      r[1].forEach(function (p) {
        if (have[p.ref] || out.length >= 9) return;
        out.push({ group: T('Personen'), ic: '🔒', t: p.name, sub: T('kein Zugriff – teilen und erwähnen'), person: p });
      });
      return out;
    });
  }
  function chip(p) {
    var s = document.createElement('span');
    s.className = 'hk-mention'; s.setAttribute('data-u', p.ref); s.textContent = p.name; s.contentEditable = 'false';
    PEOPLE[p.ref] = Object.assign({}, PEOPLE[p.ref] || {}, { name: p.name, me: !!p.me });
    return s;
  }
  // Person ohne Zugriff: erst fragen und teilen (Lesen/Kommentieren), dann erwaehnen
  function shareThen(p, done) {
    var k = K(), c = k && k.cur(); if (!c) return;
    var box = el('div', 'hk-share-mention'), sel = el('select', 'hk-in');
    [['view', T('Lesen##Rolle')], ['comment', T('Kommentieren##Rolle')]].forEach(function (o) { var x = el('option', null, o[1]); x.value = o[0]; sel.appendChild(x); });
    box.appendChild(el('p', null, T('{name} hat keinen Zugriff auf diese Seite. Seite teilen und erwähnen?', { name: p.name })));
    box.appendChild(sel);
    HK.openMenu(document.activeElement || document.body, function (m) {
      m.appendChild(box);
      m.appendChild(HK.menuItem(T('Teilen und erwähnen'), function () {
        HK.api('PUT', '/api/notes/' + c.id + '/shares', { type: 'user', target: p.ref, role: sel.value }).then(function () {
          cache.t = 0; done();
          HK.toast(T('✓ Geteilt mit {name} ({role})', { name: p.name, role: sel.options[sel.selectedIndex].textContent }));
        }).catch(function (err) { HK.toast(T('Nicht geteilt: {err}', { err: err.message })); });
      }, { icon: '@' }));
      m.appendChild(HK.menuItem(T('Abbrechen'), function () {}, { icon: '✕' }));
    });
  }
  function pick(it, range, insert) {
    var p = it.person;
    if (p.share) { shareThen(p, function () { insert(chip(p), range); }); return; }
    insert(chip(p), range);
  }

  // --- Kommentare: Token "<@ref>" <-> Chips ------------------------------------------------------------------------------
  var TOKEN = /<@(~[a-z2-7]{16}|@[a-z0-9~][a-z0-9_~-]{1,31}@[a-z0-9.:-]{1,260})>/g;
  function commentNodes(text, pm) {
    if (pm) setPeople(pm);
    var frag = document.createDocumentFragment(), last = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(text))) {
      if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
      var p = PEOPLE[m[1]], s = el('span', 'hk-mention' + (p && p.me ? ' me' : '') + (p && p.gone ? ' gone' : ''), '@' + (p ? p.name : '?'));
      frag.appendChild(s); last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    return frag;
  }
  // Textfeld eines Kommentars: "@" + Name -> Auswahl, eingefuegt als Token (gespeichert), angezeigt als Chip
  function commentInput(ta) {
    var box = null, items = [], idx = 0;
    function close() { if (box) box.hidden = true; items = []; }
    ta.addEventListener('input', function () {
      var v = ta.value.slice(0, ta.selectionStart), m = v.match(/(^|\s)@([^@\s<>]{0,30})$/);
      if (!m) { close(); return; }
      var at = ta.selectionStart - m[2].length - 1;
      load(m[2].toLowerCase()).then(function (list) {
        items = list.slice(0, 6); idx = 0;
        if (!items.length) { close(); return; }
        if (!box) { box = el('div', 'hk-emo hk-at'); box.setAttribute('role', 'listbox'); document.body.appendChild(box); }
        box.textContent = '';
        items.forEach(function (p, i) {
          var b = el('button', 'hk-emo-i' + (i === idx ? ' on' : '')); b.type = 'button';
          b.appendChild(el('span', 'hk-emo-e', '👤')); b.appendChild(el('span', 'hk-at-t', p.name));
          b.addEventListener('mousedown', function (ev) { ev.preventDefault(); put(p, at); });
          box.appendChild(b);
        });
        var r = ta.getBoundingClientRect(); box.hidden = false;
        box.style.left = r.left + 'px'; box.style.top = Math.min(r.bottom + 4, window.innerHeight - box.offsetHeight - 8) + 'px';
      });
    });
    function put(p, at) {
      var tok = '<@' + p.ref + '> ', end = ta.selectionStart;
      ta.value = ta.value.slice(0, at) + tok + ta.value.slice(end); ta.selectionStart = ta.selectionEnd = at + tok.length;
      close(); ta.dispatchEvent(new Event('input', { bubbles: true })); ta.focus();
    }
    ta.addEventListener('keydown', function (e) {
      if (!box || box.hidden || !items.length) return;
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); var at = ta.value.slice(0, ta.selectionStart).lastIndexOf('@'); put(items[idx], at); }
      else if (e.key === 'Escape') { e.stopPropagation(); close(); }
    });
    ta.addEventListener('blur', function () { setTimeout(close, 150); });
  }

  // --- Synchronisierter Block -----------------------------------------------------------------------------------------
  var SYNC_IC = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/></svg>';
  function SyncedTool(o) {
    var d = o.data || {};
    this.sid = typeof d.sid === 'string' ? d.sid : null;
    this.src = d.src && typeof d.src === 'object' ? d.src : (Array.isArray(d.blocks) ? { original: true, refs: 0 } : {});
    this.blocks = Array.isArray(d.blocks) ? d.blocks : [];
    this.readOnly = o.readOnly; this.ed = null;
    this.copy = !this.src.original;
    if (this.sid) INST[this.sid] = this;
    this.locked = this.copy && (this.src.noaccess || this.src.missing || this.src.gone || this.src.role === 'view' || this.src.role === 'comment');
  }
  SyncedTool.toolbox = { title: T('Synchronisierter Block'), icon: SYNC_IC };
  SyncedTool.isReadOnlySupported = true;
  SyncedTool.sanitize = { blocks: true };
  SyncedTool.prototype.render = function () {
    var self = this, k = K(), w = el('div', 'hk-sync' + (this.copy ? ' copy' : ' orig') + (this.locked ? ' locked' : ''));
    var bar = el('div', 'hk-sync-bar'); bar.contentEditable = 'false';
    var ic = el('span', 'hk-sync-ic'); ic.innerHTML = SYNC_IC; bar.appendChild(ic);
    var lab;
    if (this.src.noaccess) lab = '🔒 ' + T('Synchronisierter Block – kein Zugriff');
    else if (this.src.missing) lab = T('Synchronisierter Block nicht gefunden');
    else if (this.src.gone) lab = T('Original gelöscht – letzter Stand');
    else if (this.copy && this.locked) lab = T('Synchronisiert – nur lesend');
    else if (this.copy) lab = T('Synchronisiert');
    else lab = T('Original');
    bar.appendChild(el('span', 'hk-sync-lab', lab));
    var refs = this.src.refs || 0;
    if (refs && !this.src.noaccess) bar.appendChild(el('span', 'hk-sync-n', T('auf {n} weiteren Seiten', { n: refs })));
    if (this.copy && this.src.note != null && !this.src.noaccess) {
      var jb = el('button', 'linkbtn hk-sync-go', T('Original öffnen')); jb.type = 'button';
      jb.addEventListener('click', function () { location.hash = '#' + self.src.note + (self.src.block ? ':' + encodeURIComponent(self.src.block) : ''); });
      bar.appendChild(jb);
    }
    if (this.src.gone && !this.readOnly && k && !k.readOnly()) {
      var tb = el('button', 'linkbtn', T('Als normale Blöcke übernehmen')); tb.type = 'button';
      tb.addEventListener('click', function () { self.unwrap(); });
      bar.appendChild(tb);
    }
    w.appendChild(bar);
    this.body = el('div', 'hk-sync-body'); w.appendChild(this.body);
    this.pending = this.copy && !this.src.note && !this.src.noaccess && !this.src.missing && !this.blocks.length;
    if (this.pending) {                                   // gerade eingefuegte Kopie: Inhalt setzt der Server nach dem Speichern ein
      this.body.appendChild(el('p', 'hk-sync-wait', T('Synchronisierter Block – Inhalt erscheint nach dem Speichern')));
    } else if (!this.src.noaccess && !this.src.missing) {
      ['keydown', 'keyup', 'keypress', 'input', 'paste', 'cut', 'copy', 'drop'].forEach(function (ev) { self.body.addEventListener(ev, function (e) { e.stopPropagation(); }); });
      this.ed = k && k.nested ? k.nested(this.body, this.blocks, T("Inhalt – '/' für Blöcke")) : null;
      if (this.locked) guard(this.body);
    }
    this.w = w;
    return w;
  };
  SyncedTool.prototype.unwrap = function () {
    var k = K(), self = this; if (!this.api && !this.block) return;
    var ed = k.editor(), i = ed.blocks.getBlockIndex(this.blockId);
    (this.blocks || []).forEach(function (b, n) { ed.blocks.insert(b.type, b.data, undefined, i + 1 + n, false); });
    ed.blocks.delete(i);
    k.dirty();
    HK.toast(T('Inhalt übernommen – der Block ist nicht mehr synchronisiert'));
    void self;
  };
  SyncedTool.prototype.rendered = function () { this.blockId = this.block && this.block.id; };
  SyncedTool.prototype.save = function () {
    var self = this, out = { sid: this.sid };
    if (this.src && this.src.rev != null && this.copy) out.src = { rev: this.src.rev };      // Basis fuers Durchschreiben
    if (this.src.noaccess || this.src.missing || this.pending || !this.ed || (this.copy && this.locked)) {
      if (!this.copy) out.blocks = this.blocks;
      return out;
    }
    return this.ed.isReady.then(function () { return self.ed.save(); }).then(function (o) { self.blocks = o.blocks; out.blocks = o.blocks; return out; });
  };
  SyncedTool.prototype.destroy = function () {
    var k = K(); if (this.ed && k && k.kill) k.kill(this.ed); this.ed = null;
    if (this.sid && INST[this.sid] === this) delete INST[this.sid];
  };
  // Kopie nur lesend (Lesen/Kommentieren auf dem Original, gesperrt, Original geloescht): Eingaben im Inhalt abfangen
  function guard(box) {
    var stop = function (e) { e.preventDefault(); e.stopPropagation(); HK.toast(T('Synchronisierter Block – nur lesend. Bearbeiten im Original.')); };
    ['beforeinput', 'paste', 'drop', 'cut'].forEach(function (ev) { box.addEventListener(ev, stop, true); });
    box.addEventListener('keydown', function (e) {
      if (/^(Arrow|Page|Home|End|Escape|Shift|Control|Alt|Meta|Tab$)/.test(e.key) || ((e.metaKey || e.ctrlKey) && /^[acf]$/i.test(e.key))) return;
      stop(e);
    }, true);
    box.classList.add('ro');
  }
  // Antwort auf das Speichern (PATCH): neuer Stand der Originale -> Basis der Kopien; abgelehnt/veraltet -> Hinweis + neu laden
  function saved(d) {
    var revs = d.synced_rev || {}, bad = false, k0 = K();
    if (Object.keys(INST).some(function (s) { return INST[s].pending; }) && k0 && k0.apply) setTimeout(function () { k0.apply(); }, 0);
    Object.keys(d.synced || {}).forEach(function (sid) {
      var r = d.synced[sid], it = INST[sid];
      if (it && it.src && typeof revs[sid] === 'number') it.src.rev = revs[sid];
      if (r === 'stale' || r === 'denied' || r === 'locked' || r === 'gone') bad = r;
      if (r === 'merged') HK.toast(T('Synchronisierter Block mit einer gleichzeitigen Änderung im Original zusammengeführt'));
    });
    if (bad) {
      HK.toast(bad === 'stale' ? T('Synchronisierter Block nicht übernommen – das Original hat sich geändert. Neu geladen.')
                               : T('Synchronisierter Block nicht übernommen – keine Bearbeitungsrechte auf das Original.'));
      var k = K(); if (k && k.apply) setTimeout(function () { k.apply(); }, 0);
    }
  }
  SyncedTool.prototype.constructor = SyncedTool;
  function SyncedToolCtor(o) { SyncedTool.call(this, o); this.api = o.api; this.block = o.block; }
  SyncedToolCtor.prototype = SyncedTool.prototype;
  SyncedToolCtor.toolbox = SyncedTool.toolbox; SyncedToolCtor.isReadOnlySupported = true; SyncedToolCtor.sanitize = SyncedTool.sanitize;

  // Block-Menue (⋮⋮): "In synchronisierten Block umwandeln" (Original) bzw. "Synchronisierte Kopie kopieren"
  function SyncTune(o) { this.api = o.api; this.block = o.block; }
  SyncTune.isTune = true;
  SyncTune.prototype.render = function () {
    var self = this, k = K(); if (!k || k.readOnly() || !this.block) return [];
    var cur = k.cur(); if (!cur || cur.id < 0) return [];          // Seiten anderer Instanzen: sids gelten je Server
    var name = this.block.name;
    if (name === 'synced') {
      return { icon: SYNC_IC, title: T('Synchronisierten Block kopieren'), closeOnActivate: true, onActivate: function () { copyRef(self.block); } };
    }
    if (name === 'columns' || name === 'database') return [];
    return { icon: SYNC_IC, title: T('Als synchronisierten Block kopieren'), closeOnActivate: true, onActivate: function () { wrap(self.api, self.block); } };
  };
  function sidNew() {
    var a = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', b = new Uint8Array(16), s = '';
    crypto.getRandomValues(b); for (var i = 0; i < 16; i++) s += a[b[i] % 62]; return s;
  }
  function wrap(eapi, blk) {
    var i = eapi.blocks.getBlockIndex(blk.id);
    blk.save().then(function (s) {
      if (!s) return;
      var sid = sidNew();
      eapi.blocks.insert('synced', { sid: sid, blocks: [{ type: s.tool, data: s.data, tunes: s.tunes }] }, undefined, i, true, true);
      var k = K(); if (k) { k.dirty(); k.save(); }         // Original sofort speichern - die Kopie anderswo braucht es
      clip(sid, T('Synchronisierter Block angelegt und kopiert – auf einer anderen Seite einfügen'));
    });
  }
  function copyRef(blk) {
    blk.save().then(function (s) {
      var sid = s && s.data && s.data.sid;
      if (!sid) { HK.toast(T('Erst speichern, dann kopieren')); return; }
      clip(sid, T('Synchronisierte Kopie in der Zwischenablage – auf einer anderen Seite einfügen'));
    });
  }
  function clip(sid, msg) {
    var json = JSON.stringify([{ tool: 'synced', data: { sid: sid } }]);
    var h = function (e) { e.preventDefault(); e.stopImmediatePropagation(); e.clipboardData.setData('application/x-editor-js', json);
                           e.clipboardData.setData('text/plain', T('Synchronisierter Block')); };
    document.addEventListener('copy', h, true);
    var ok = false; try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    document.removeEventListener('copy', h, true);
    HK.toast(ok ? msg : T('Kopieren nicht möglich'));
    LAST.sid = sid; LAST.at = Date.now();
  }
  var LAST = { sid: null, at: 0 }, INST = {};

  window.HKMention = { options: options, pick: pick, setPeople: setPeople, decorate: decorate, commentNodes: commentNodes,
                       commentInput: commentInput, saved: saved, people: function () { return PEOPLE; }, last: LAST };
  window.HKSyncedTool = SyncedToolCtor;
  window.HKSyncTune = SyncTune;
})();
