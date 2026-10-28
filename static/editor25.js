// Saga 2.5 (P6-1/P6-4): Editor-Werkzeuge - Inline-Farben, Mathe (KaTeX, Block + Inline), Mermaid (abgeschotteter Rahmen),
// Vorschaubilder fuer die Apps, Mehrfachauswahl-Leiste und "Verschieben nach ..." (Bloecke auf eine andere Seite).
// Wird VOR static/pages.js geladen; die Werkzeuge registriert pages.js (window.HKColor, HKMathTool, HKMathInline, HKMoveTune).
// Zur Laufzeit nutzt es die Bausteine aus pages.js (window.HKPages.k). Alles ueber DOM-APIs/textContent, keine
// style-Attribute im HTML (CSP style-src 'self'); KaTeX zeichnet ueber CSSOM, Mermaid-Ausgabe wird nur als <img> gezeigt.
(function () {
  'use strict';
  var el = function (tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  var COLORS = ['gray', 'brown', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink', 'red'];
  var V = document.body.dataset.v || '';
  function K() { return window.HKPages && window.HKPages.k; }
  function ro() { var k = K(); return !!(k && k.readOnly()); }
  function changed(blk) { if (blk && blk.dispatchChange) blk.dispatchChange(); }
  function editableOf(node) {
    var n = node && node.nodeType === 3 ? node.parentElement : node;
    return n && n.closest ? n.closest('[contenteditable="true"]') : null;
  }
  function inputOn(node) { var e = editableOf(node); if (e) e.dispatchEvent(new Event('input', { bubbles: true })); }
  var COLOR_NAME = function (c) { var k = K(); return (k && k.colorNames && k.colorNames[c]) || c; };

  // --- SHA-256 (render.h = erste 16 Hex-Zeichen, wie saga/pages.py src_hash) -------------------------------------------
  // crypto.subtle gibt es nur in sicheren Kontexten (https, localhost) - sonst eigene kleine Umsetzung.
  function sha256hex(str) {
    var bytes = new TextEncoder().encode(str);
    if (window.crypto && crypto.subtle && window.isSecureContext) {
      return crypto.subtle.digest('SHA-256', bytes).then(function (b) {
        return Array.prototype.map.call(new Uint8Array(b), function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
      });
    }
    return Promise.resolve(sha256js(bytes));
  }
  function sha256js(m) {
    var K2 = [], H = [], i, j, isP = function (n) { for (var f = 2; f * f <= n; f++) if (n % f === 0) return false; return true; };
    for (i = 2, j = 0; j < 64; i++) if (isP(i)) { if (j < 8) H[j] = (Math.pow(i, 1 / 2) * 4294967296) | 0; K2[j++] = (Math.pow(i, 1 / 3) * 4294967296) | 0; }
    var l = m.length, w = [], words = new Uint32Array(((l + 9 + 63) >> 6) << 4);
    for (i = 0; i < l; i++) words[i >> 2] |= m[i] << (24 - (i % 4) * 8);
    words[l >> 2] |= 0x80 << (24 - (l % 4) * 8);
    words[words.length - 1] = l * 8;
    var r = function (x, n) { return (x >>> n) | (x << (32 - n)); };
    for (j = 0; j < words.length; j += 16) {
      var a = H.slice(0, 8);
      for (i = 0; i < 64; i++) {
        if (i < 16) w[i] = words[j + i] | 0;
        else w[i] = (r(w[i - 2], 17) ^ r(w[i - 2], 19) ^ (w[i - 2] >>> 10)) + w[i - 7] + (r(w[i - 15], 7) ^ r(w[i - 15], 18) ^ (w[i - 15] >>> 3)) + w[i - 16] | 0;
        var t1 = a[7] + (r(a[4], 6) ^ r(a[4], 11) ^ r(a[4], 25)) + ((a[4] & a[5]) ^ (~a[4] & a[6])) + K2[i] + w[i] | 0;
        var t2 = (r(a[0], 2) ^ r(a[0], 13) ^ r(a[0], 22)) + ((a[0] & a[1]) ^ (a[0] & a[2]) ^ (a[1] & a[2])) | 0;
        a = [t1 + t2 | 0].concat(a.slice(0, 7)); a[4] = a[4] + t1 | 0;
      }
      for (i = 0; i < 8; i++) H[i] = H[i] + a[i] | 0;
    }
    return H.map(function (x) { return ('00000000' + (x >>> 0).toString(16)).slice(-8); }).join('');
  }
  function srcHash(s) { return sha256hex(String(s || '')).then(function (h) { return h.slice(0, 16); }); }

  // --- Bibliotheken nur bei Bedarf (lokal unter /static/vendor, keine CDNs) ----------------------------------------------
  var katexP = null;
  function loadKatex() {
    if (window.katex) return Promise.resolve(window.katex);
    if (katexP) return katexP;
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = '/static/vendor/katex/katex.min.css?v=' + V; document.head.appendChild(l);
    katexP = new Promise(function (ok, fail) {
      var s = document.createElement('script'); s.src = '/static/vendor/katex/katex.min.js?v=' + V;
      s.onload = function () { ok(window.katex); }; s.onerror = function () { katexP = null; fail(new Error('katex')); };
      document.body.appendChild(s);
    });
    return katexP;
  }
  var KOPT = { throwOnError: false, trust: false, strict: 'warn', maxSize: 20, maxExpand: 500 };
  function renderTex(tex, box, display) {      // katex.render baut DOM ueber CSSOM (kein innerHTML, keine style-Attribute)
    return loadKatex().then(function (k) {
      box.textContent = '';
      try { k.render(String(tex || ''), box, Object.assign({ displayMode: !!display }, KOPT)); return true; } catch (e) { box.textContent = String(e.message || e); return false; }
    });
  }

  // Mermaid: EIN abgeschotteter Rahmen fuer die ganze Seite (sandbox="allow-scripts" OHNE allow-same-origin)
  var MM = { frame: null, ready: null, seq: 0, wait: {} };
  function mermaidFrame() {
    if (MM.ready) return MM.ready;
    MM.ready = new Promise(function (ok, fail) {
      var f = el('iframe', 'hk-mmd-frame'); f.setAttribute('sandbox', 'allow-scripts'); f.setAttribute('aria-hidden', 'true'); f.tabIndex = -1;
      f.setAttribute('referrerpolicy', 'no-referrer'); f.title = 'mermaid';
      f.src = '/static/mermaid-frame.html?v=' + V;
      var to = setTimeout(function () { MM.ready = null; fail(new Error('timeout')); }, 20000);
      window.addEventListener('message', function (e) {
        var d = e.data;
        if (!MM.frame || e.source !== MM.frame.contentWindow || !d || d.hk !== 'mmd') return;
        if (d.ready) { clearTimeout(to); ok(f); return; }
        var w = MM.wait[d.id]; if (!w) return;
        delete MM.wait[d.id];
        if (typeof d.svg === 'string') w.ok(d.svg); else w.fail(new Error(String(d.error || 'mermaid')));
      });
      MM.frame = f; document.body.appendChild(f);
    });
    return MM.ready;
  }
  function renderMermaid(src) {
    return mermaidFrame().then(function (f) {
      return new Promise(function (ok, fail) {
        var id = ++MM.seq; MM.wait[id] = { ok: ok, fail: fail };
        setTimeout(function () { if (MM.wait[id]) { delete MM.wait[id]; fail(new Error('timeout')); } }, 15000);
        f.contentWindow.postMessage({ hk: 'mmd', id: id, src: String(src || '') }, '*');
      });
    }).then(function (svg) { var x = fixSvg(svg); x.svg = svg; return x; });
  }
  // SVG-Text (aus dem Rahmen) -> {url: blob-URL, w, h}. Nur als <img> gezeigt (Skripte/Ereignisse in Bildern laufen nie);
  // nur das Wurzel-Element bekommt feste Masse (als Text - kein DOMParser, der wuerde die Stile des SVG gegen die CSP pruefen)
  function fixSvg(svg) {
    var m = /^\s*(?:<\?xml[^>]*>\s*)?<svg\b([^>]*)>/i.exec(svg);
    if (!m || !/<\/svg>\s*$/i.test(svg)) throw new Error('svg');
    var attrs = m[1], vb = /\bviewBox="([^"]*)"/i.exec(attrs), w = 0, h = 0;
    if (vb) { var n = vb[1].trim().split(/[\s,]+/).map(Number); w = n[2]; h = n[3]; }
    if (!(w > 0 && h > 0)) { w = 600; h = 400; }
    w = Math.min(4000, Math.ceil(w)); h = Math.min(4000, Math.ceil(h));
    attrs = attrs.replace(/\s(width|height|style)="[^"]*"/gi, '');
    if (!/\bxmlns="/.test(attrs)) attrs += ' xmlns="http://www.w3.org/2000/svg"';
    var txt = '<svg' + attrs + ' width="' + w + '" height="' + h + '">' + svg.slice(m[0].length);
    return { url: URL.createObjectURL(new Blob([txt], { type: 'image/svg+xml' })), w: w, h: h };
  }
  // Bild (URL) -> PNG-Blob in doppelter Aufloesung (hoechstens 1 MB, sonst einfache Aufloesung)
  function toPng(url, w, h, bg) {
    return new Promise(function (ok, fail) {
      var img = new Image();
      img.onload = function () {
        var tryScale = function (sc) {
          var c = document.createElement('canvas'); c.width = Math.round(w * sc); c.height = Math.round(h * sc);
          var g = c.getContext('2d'); if (bg) { g.fillStyle = bg; g.fillRect(0, 0, c.width, c.height); }
          g.drawImage(img, 0, 0, c.width, c.height);
          try {
            c.toBlob(function (b) {
              if (!b) return fail(new Error('png'));
              if (b.size > 1048576 && sc > 1) return tryScale(1);
              if (b.size > 1048576) return fail(new Error('gross'));
              ok(b);
            }, 'image/png');
          } catch (e) { fail(e); }                         // Canvas verunreinigt (aeltere Browser): kein Vorschaubild
        };
        tryScale(2);
      };
      img.onerror = function () { fail(new Error('img')); };
      img.src = url;
    });
  }
  function upload(blob, name) {
    var k = K(), fd = new FormData(); fd.append('image', blob, name || 'vorschau.png');
    return fetch('/api/files', { method: 'POST', credentials: 'same-origin', headers: Object.assign({}, (k && k.uph) || { 'X-Saga': '1' }), body: fd })
      .then(function (r) { return r.json(); }).then(function (d) { if (!d || !d.success || !d.file) throw new Error((d && d.error) || 'upload'); return d.file.url; });
  }
  // Vorschaubild fuer die Apps (data.render): wenn der Quelltext 3 s ruht und das gespeicherte Bild nicht dazu passt
  function scheduleRender(tool) {
    clearTimeout(tool.rT);
    if (ro()) return;
    tool.rT = setTimeout(function () {
      var src = tool.source(); if (!src.trim()) return;
      srcHash(src).then(function (h) {
        var r = tool.data.render;
        if (r && r.h === h && r.png && !r.stale) return;
        return tool.makePng().then(function (x) {
          if (!x || tool.source() !== src) return;
          return upload(x.blob, tool.pngName).then(function (url) {
            if (tool.source() !== src) return;
            tool.data.render = { png: url, h: h, w: x.w, hgt: x.h };
            tool.markRender();
            changed(tool.block);
          });
        });
      }).catch(function () { /* kein Vorschaubild: Apps zeigen den Quelltext */ });
    }, 3000);
  }

  // --- Mathe-Block (type "math", data {tex, render?}) -----------------------------------------------------------------
  var SIGMA = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M18 5H6l6 7-6 7h12"/></svg>';
  function MathTool(o) { this.api = o.api; this.block = o.block; this.data = Object.assign({}, o.data || {}); this.data.tex = String(this.data.tex || ''); this.pngName = 'formel.png'; }
  MathTool.toolbox = { title: T('Formel (Mathe)'), icon: SIGMA };
  MathTool.isReadOnlySupported = true;
  MathTool.enableLineBreaks = true;
  MathTool.sanitize = { tex: true };
  MathTool.prototype.source = function () { return this.ta ? this.ta.value : this.data.tex; };
  MathTool.prototype.render = function () {
    var self = this, w = el('div', 'hk-mathblk'), view = el('div', 'hk-math-view'), ta = el('textarea', 'hk-math-ta');
    ta.value = this.data.tex; ta.placeholder = T('TeX, z. B. \\frac{a}{b} = \\sqrt{x^2}'); ta.spellcheck = false; ta.rows = 2;
    ta.setAttribute('aria-label', T('Formel (TeX)')); ta.readOnly = ro();
    var stale = el('div', 'hk-math-stale');
    this.w = w; this.ta = ta; this.view = view; this.st = stale;
    view.setAttribute('role', 'img');
    var t = null;
    ta.addEventListener('input', function () { clearTimeout(t); t = setTimeout(function () { self.paint(); }, 200); changed(self.block); scheduleRender(self); });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' || ((e.metaKey || e.ctrlKey) && e.key === 'Enter')) { e.preventDefault(); ta.blur(); }
      e.stopPropagation();                          // Enter/Backspace gehoeren der Formel, nicht dem Editor
    });
    ta.addEventListener('focus', function () { w.classList.add('editing'); });
    ta.addEventListener('blur', function () { w.classList.remove('editing'); self.paint(); });
    view.addEventListener('click', function () { if (!ro()) { w.classList.add('editing'); ta.focus(); } });
    w.appendChild(view); w.appendChild(ta); w.appendChild(stale);
    if (!this.data.tex) w.classList.add('editing');
    this.paint(); this.markRender();
    if (!ro()) scheduleRender(this);             // fehlendes/veraltetes Vorschaubild beim Oeffnen im Web nachholen
    return w;
  };
  MathTool.prototype.paint = function () {
    var tex = this.source(), v = this.view;
    v.setAttribute('aria-label', tex);
    if (!tex.trim()) { v.textContent = T('Leere Formel – klicken zum Bearbeiten'); v.classList.add('empty'); return; }
    v.classList.remove('empty');
    renderTex(tex, v, true).catch(function () { v.textContent = tex; });
  };
  MathTool.prototype.markRender = function () {
    var self = this, r = this.data.render;
    if (!this.st) return;
    if (!r) { this.st.textContent = ''; return; }
    srcHash(this.source()).then(function (h) { self.st.textContent = r.h !== h || r.stale ? T('Vorschaubild für die Apps veraltet') : ''; });
  };
  MathTool.prototype.makePng = function () {
    var tex = this.source();
    return loadKatex().then(function (k) {
      var box = el('div', 'hk-math-measure');
      document.body.appendChild(box);
      try { k.render(tex, box, Object.assign({ displayMode: true, output: 'mathml' }, KOPT)); } catch (e) { box.remove(); return null; }
      var m = box.querySelector('math'); if (!m) { box.remove(); return null; }
      var rc = box.getBoundingClientRect(), w = Math.ceil(rc.width) + 16, h = Math.ceil(rc.height) + 12;
      var xml = new XMLSerializer().serializeToString(m);
      box.remove();
      var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h + '"><foreignObject x="0" y="0" width="' + w + '" height="' + h + '">' +
        '<div xmlns="http://www.w3.org/1999/xhtml" style="font-size:20px;color:#1d2430;padding:6px 8px">' + xml + '</div></foreignObject></svg>';
      return toPng('data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg), w, h, '#ffffff').then(function (b) { return { blob: b, w: w, h: h }; });
    });
  };
  MathTool.prototype.save = function () {
    var d = Object.assign({}, this.data, { tex: this.source() });
    if (!d.render) delete d.render;
    return d;
  };
  MathTool.prototype.validate = function () { return true; };

  // --- Inline-Mathe <code class="hk-math">TeX</code> -------------------------------------------------------------------
  // Der Text bleibt TeX (so speichern es alle Clients); dargestellt als Chip, gerendert im Popover (Klick bzw. Fokus).
  function MathInline(o) { this.api = o.api; }
  MathInline.isInline = true;
  MathInline.title = T('Formel');
  MathInline.sanitize = { code: { class: true } };
  MathInline.prototype.render = function () { var b = el('button', 'ce-inline-tool hk-imath-tool', '∑'); b.type = 'button'; b.title = T('Als Formel (TeX)'); return b; };
  MathInline.prototype.surround = function (range) {
    if (!range) return;
    var cur = this.api.selection.findParentTag('CODE', 'hk-math');
    if (cur) {                                        // schon Formel: wieder normaler Text
      var p = cur.parentNode; while (cur.firstChild) p.insertBefore(cur.firstChild, cur); p.removeChild(cur); inputOn(p); return;
    }
    var txt = range.toString(); if (!txt.trim()) return;
    var c = el('code', 'hk-math', txt);
    range.deleteContents(); range.insertNode(c);
    var s = window.getSelection(), r = document.createRange(); r.setStartAfter(c); r.collapse(true); s.removeAllRanges(); s.addRange(r);
    inputOn(c);
    openMathPop(c);
  };
  MathInline.prototype.checkState = function () { return !!this.api.selection.findParentTag('CODE', 'hk-math'); };

  var pop = null;
  function closeMathPop() { if (pop) { pop.remove(); pop = null; } }
  function openMathPop(code) {
    closeMathPop();
    var p = el('div', 'hk-mpop'), view = el('div', 'hk-mpop-v'), r = code.getBoundingClientRect();
    p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', T('Formel'));
    p.appendChild(view);
    var inp = null;
    if (!ro() && editableOf(code)) {
      inp = el('input', 'hk-mpop-in'); inp.type = 'text'; inp.value = code.textContent; inp.spellcheck = false; inp.setAttribute('aria-label', T('Formel (TeX)'));
      inp.addEventListener('input', function () { code.textContent = inp.value; renderTex(inp.value, view, false); inputOn(code); });
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); closeMathPop(); } });
      p.appendChild(inp);
    }
    document.body.appendChild(p);
    p.style.left = Math.max(8, Math.min(r.left, window.innerWidth - p.offsetWidth - 8)) + 'px';
    p.style.top = Math.min(r.bottom + 6, window.innerHeight - p.offsetHeight - 8) + 'px';
    pop = p;
    renderTex(code.textContent, view, false).catch(function () { view.textContent = code.textContent; });
    if (inp) setTimeout(function () { inp.focus(); inp.select(); }, 0);
  }
  document.addEventListener('click', function (e) {
    var t = e.target, c = t && t.closest ? t.closest('#pg-editor code.hk-math') : null;
    if (c) { openMathPop(c); return; }
    if (pop && !pop.contains(t)) closeMathPop();
  });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && pop) closeMathPop(); });

  // --- Inline-Farbe (Text: span.hk-c-*, Hintergrund: mark.hk-bg-*; span immer aussen) ----------------------------------
  var LAST_KEY = 'hk-last-color';
  function lastColor() { try { var v = JSON.parse(localStorage.getItem(LAST_KEY) || 'null'); if (v && COLORS.indexOf(v.c) >= 0) return v; } catch (e) { /* aus */ } return null; }
  function setLast(v) { try { localStorage.setItem(LAST_KEY, JSON.stringify(v)); } catch (e) { /* privat */ } }
  function isColored(n, bg) { return n && n.nodeType === 1 && (bg ? n.tagName === 'MARK' && /\bhk-bg-/.test(n.className) : n.tagName === 'SPAN' && /\bhk-c-/.test(n.className)); }
  function unwrap(n) { var p = n.parentNode; while (n.firstChild) p.insertBefore(n.firstChild, n); p.removeChild(n); }
  function stripIn(frag, bg) {
    [].slice.call(frag.querySelectorAll(bg ? 'mark' : 'span')).forEach(function (n) { if (isColored(n, bg)) unwrap(n); });
  }
  // Auswahl aus einem umgebenden gefaerbten Element herausloesen (vorne/hinten bleibt gefaerbt)
  function splitOut(anc, range) {
    var before = document.createRange(), after = document.createRange();
    before.setStart(anc, 0); before.setEnd(range.startContainer, range.startOffset);
    after.setStart(range.endContainer, range.endOffset); after.setEnd(anc, anc.childNodes.length);
    var fb = before.extractContents(), fa = after.extractContents();
    if (fb.textContent) { var b = anc.cloneNode(false); b.appendChild(fb); anc.parentNode.insertBefore(b, anc); }
    if (fa.textContent) { var a = anc.cloneNode(false); a.appendChild(fa); anc.parentNode.insertBefore(a, anc.nextSibling); }
    var first = anc.firstChild, last = anc.lastChild;
    unwrap(anc);
    return [first, last];
  }
  function ancestorColored(node, bg, root) {
    for (var n = node && node.nodeType === 3 ? node.parentNode : node; n && n !== root; n = n.parentNode) if (isColored(n, bg)) return n;
    return null;
  }
  // Farbe auf eine Auswahl anwenden: st = {c, bg} oder {c: null, bg} (entfernen)
  function applyColor(range, st) {
    if (!range || range.collapsed) return false;
    var root = editableOf(range.commonAncestorContainer); if (!root) return false;
    var bg = !!st.bg;
    var anc = ancestorColored(range.commonAncestorContainer, bg, root);
    if (anc) {                                         // in einer gefaerbten Stelle: herausloesen, dann neu faerben
      var ends = splitOut(anc, range);
      if (!ends[0]) return false;
      range = document.createRange(); range.setStartBefore(ends[0]); range.setEndAfter(ends[1]);
    }
    var frag = range.extractContents();
    stripIn(frag, bg);
    var node = frag;
    if (st.c) {
      var w = el(bg ? 'mark' : 'span', (bg ? 'hk-bg-' : 'hk-c-') + st.c);
      if (!bg) {                                       // span aussen: Hintergrund-Marken bleiben innen
        w.appendChild(frag);
      } else {
        // Textfarbe innerhalb der Markierung? Dann kommt sie nach aussen (feste Reihenfolge span > mark)
        var inner = [].slice.call(frag.querySelectorAll('span')).filter(function (n) { return isColored(n, false); });
        if (inner.length === 1 && inner[0].textContent === frag.textContent) {
          var sp = el('span', inner[0].className); unwrap(inner[0]); w.appendChild(frag); sp.appendChild(w); w = sp;
        } else w.appendChild(frag);
      }
      node = w;
    }
    var first = node.nodeType === 11 ? node.firstChild : node, last = node.nodeType === 11 ? node.lastChild : node;
    range.insertNode(node);
    root.normalize();
    try {
      var s = window.getSelection(), r = document.createRange();
      if (first && first.parentNode && last && last.parentNode) { r.setStartBefore(first); r.setEndAfter(last); s.removeAllRanges(); s.addRange(r); }
    } catch (e) { /* Auswahl egal */ }
    root.dispatchEvent(new Event('input', { bubbles: true }));
    if (st.c) setLast({ c: st.c, bg: bg });
    return true;
  }
  function HKColor(o) { this.api = o.api; this.btn = null; }
  HKColor.isInline = true;
  HKColor.title = T('Farbe');
  HKColor.sanitize = { span: { class: true, 'data-u': true }, mark: { class: true } };
  HKColor.prototype.render = function () {
    var b = el('button', 'ce-inline-tool hk-color-tool'); b.type = 'button'; b.title = T('Text- und Hintergrundfarbe');
    b.appendChild(el('span', 'hk-color-a', 'A'));
    this.btn = b; return b;
  };
  HKColor.prototype.surround = function (range) { if (range) openPalette(range.cloneRange(), range.getBoundingClientRect()); };
  HKColor.prototype.checkState = function () {
    var on = !!(this.api.selection.findParentTag('SPAN') || this.api.selection.findParentTag('MARK'));
    if (this.btn) this.btn.classList.toggle('ce-inline-tool--active', on);
    return on;
  };
  var pal = null;
  function closePalette() { if (pal) { pal.remove(); pal = null; } }
  function openPalette(range, rect) {
    closePalette();
    var p = el('div', 'hk-cpal'); p.setAttribute('role', 'menu'); p.setAttribute('aria-label', T('Farbe'));
    p.addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); });   // Auswahl im Text bleibt
    var pick = function (st) { closePalette(); var s = window.getSelection(); s.removeAllRanges(); s.addRange(range); applyColor(range, st); };
    var row = function (label, bg) {
      p.appendChild(el('div', 'hk-cpal-l', label));
      var r = el('div', 'hk-cpal-r');
      var none = el('button', 'hk-cpal-b', '∅'); none.type = 'button'; none.title = T('Standard'); none.setAttribute('aria-label', T('Standard') + ' – ' + label);
      none.addEventListener('click', function () { pick({ c: null, bg: bg }); }); r.appendChild(none);
      COLORS.forEach(function (c) {
        var b = el('button', 'hk-cpal-b ' + (bg ? 'hk-bg-' : 'hk-c-') + c, 'A'); b.type = 'button'; b.dataset.c = c; b.dataset.bg = bg ? '1' : '';
        var name = bg ? T('{farbe} (Hintergrund)', { farbe: COLOR_NAME(c) }) : COLOR_NAME(c);
        b.title = name; b.setAttribute('aria-label', name);
        b.addEventListener('click', function () { pick({ c: c, bg: bg }); }); r.appendChild(b);
      });
      p.appendChild(r);
    };
    var last = lastColor();
    if (last) {
      var lb = el('button', 'hk-cpal-last ' + (last.bg ? 'hk-bg-' : 'hk-c-') + last.c, T('Zuletzt: {farbe}', { farbe: last.bg ? T('{farbe} (Hintergrund)', { farbe: COLOR_NAME(last.c) }) : COLOR_NAME(last.c) }));
      lb.type = 'button'; lb.title = (K() && K().isMac ? '⌘' : T('Strg') + '+') + '⇧H';
      lb.addEventListener('click', function () { pick(last); }); p.appendChild(lb);
    }
    row(T('Text'), false); row(T('Hintergrund'), true);
    document.body.appendChild(p);
    p.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - p.offsetWidth - 8)) + 'px';
    p.style.top = (rect.bottom + 8 + p.offsetHeight > window.innerHeight ? Math.max(8, rect.top - p.offsetHeight - 8) : rect.bottom + 8) + 'px';
    pal = p;
    var f = p.querySelector('button'); if (f) f.focus({ preventScroll: true });
  }
  document.addEventListener('mousedown', function (e) { if (pal && !pal.contains(e.target)) closePalette(); }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && pal) { closePalette(); return; }
    // Strg/Cmd+Umschalt+H: zuletzt benutzte Farbe auf die Auswahl
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && !e.altKey && (e.key === 'h' || e.key === 'H')) {
      var s = window.getSelection(), last = lastColor();
      if (!s || !s.rangeCount || s.isCollapsed || !last || ro()) return;
      var r = s.getRangeAt(0), box = document.getElementById('pg-editor');
      if (!box || !box.contains(r.commonAncestorContainer)) return;
      e.preventDefault(); applyColor(r, last);
    }
  }, true);

  // --- Mermaid im Code-Block (static/code.js ruft das auf) --------------------------------------------------------------
  // tool: HKCode-Instanz mit source(), data, block, mmdBox (Vorschau), markRender()
  function mermaidPaint(tool) {
    clearTimeout(tool.mT);
    tool.mT = setTimeout(function () {
      var src = tool.source(), box = tool.mmdBox, my = (tool.mSeq = (tool.mSeq || 0) + 1);
      if (!box) return;
      if (!src.trim()) { box.textContent = ''; box.appendChild(el('div', 'hk-mmd-msg', T('Leeres Diagramm'))); return; }
      renderMermaid(src).then(function (x) {
        if (my !== tool.mSeq) { URL.revokeObjectURL(x.url); return; }
        if (tool.mUrl) URL.revokeObjectURL(tool.mUrl);
        tool.mUrl = x.url; tool.mSize = x;
        box.textContent = '';
        var img = el('img', 'hk-mmd-img'); img.alt = T('Diagramm##Mermaid'); img.src = x.url; img.width = x.w; img.height = x.h; img.decoding = 'async';
        box.appendChild(img);
      }).catch(function (err) {
        if (my !== tool.mSeq) return;
        box.textContent = '';
        box.appendChild(el('div', 'hk-mmd-msg err', T('Diagramm nicht lesbar: {err}', { err: String(err.message || err).slice(0, 160) })));
      });
      scheduleRender(tool);
    }, 350);
  }
  function mermaidPng(tool) {
    return renderMermaid(tool.source()).then(function (x) {
      return toPng(x.url, x.w, x.h, '#ffffff').then(function (b) { URL.revokeObjectURL(x.url); return { blob: b, w: x.w, h: x.h }; },
                                                     function (e) { URL.revokeObjectURL(x.url); throw e; });
    });
  }
  function renderStale(tool, out) {
    var r = tool.data.render;
    if (!out) return;
    if (!r) { out.textContent = ''; return; }
    srcHash(tool.source()).then(function (h) { out.textContent = r.h !== h || r.stale ? T('Vorschaubild für die Apps veraltet') : ''; });
  }

  // --- Mehrfachauswahl-Leiste (G7) ----------------------------------------------------------------------------------------
  var TEXT_TYPES = { paragraph: 1, header: 1, list: 1, quote: 1 };
  var bar = null, barSel = null;
  function edFor(redactor) {          // .codex-editor__redactor -> {ed, top}
    var k = K(); if (!k || !redactor) return null;
    var holder = redactor.closest('.codex-editor'); holder = holder && holder.parentElement;
    if (!holder) return null;
    if (holder.id === 'pg-editor') return { ed: k.editor(), top: true };
    var x = k.eds().filter(function (e) { return e.holder === holder; })[0];
    return x ? { ed: x.ed, top: false } : null;
  }
  function currentSelection() {
    var box = document.getElementById('pg-editor'); if (!box) return null;
    var sel = [].slice.call(box.querySelectorAll('.ce-block--selected'));
    if (sel.length < 2) return null;
    var groups = new Map();
    sel.forEach(function (b) { var r = b.parentElement; if (!groups.has(r)) groups.set(r, []); groups.get(r).push(b); });
    var best = null;                    // die Gruppe mit den meisten Bloecken (oberste Ebene bei Gleichstand)
    groups.forEach(function (list, r) { if (!best || list.length > best.list.length || (list.length === best.list.length && r.closest('#pg-editor > .codex-editor') === r.parentElement)) best = { r: r, list: list }; });
    if (!best || best.list.length < 2) return null;
    var e = edFor(best.r); if (!e || !e.ed) return null;
    var ids = best.list.map(function (b) { return b.dataset.id; }).filter(Boolean);
    return ids.length >= 2 ? { ed: e.ed, top: e.top, ids: ids, holders: best.list } : null;
  }
  function clearSel() {
    [].forEach.call(document.querySelectorAll('#pg-editor .ce-block--selected'), function (b) { b.classList.remove('ce-block--selected'); });
    hideBar();
  }
  function hideBar() { if (bar) bar.hidden = true; barSel = null; }
  function updateBar() {
    var s = currentSelection();
    if (!s || ro()) { hideBar(); return; }
    barSel = s;
    if (!bar) buildBar();
    bar.querySelector('.hk-sel-n').textContent = T('{n} Blöcke', { n: s.ids.length });
    var types = blockTypes(s);
    bar.querySelector('[data-a="convert"]').disabled = !types.every(function (t) { return TEXT_TYPES[t]; });
    bar.hidden = false;
  }
  function blockTypes(s) { return s.ids.map(function (id) { var b = s.ed.blocks.getById(id); return b ? b.name : ''; }); }
  function buildBar() {
    bar = el('div', 'hk-sel-bar'); bar.setAttribute('role', 'toolbar'); bar.setAttribute('aria-label', T('Ausgewählte Blöcke')); bar.hidden = true;
    bar.appendChild(el('span', 'hk-sel-n'));
    var add = function (a, label, icon) {
      var b = el('button', 'hk-sel-b'); b.type = 'button'; b.dataset.a = a; b.title = label; b.setAttribute('aria-label', label);
      b.appendChild(el('span', 'hk-sel-ic', icon)); b.appendChild(el('span', 'hk-sel-t', label));
      b.addEventListener('click', function (e) { var s = barSel || currentSelection(); if (s) act(a, s, b, e); });
      bar.appendChild(b);
    };
    add('delete', T('Löschen'), '🗑'); add('move', T('Verschieben nach …'), '↪'); add('convert', T('Umwandeln in …'), '⇄');
    add('color', T('Farbe'), 'A'); add('dup', T('Duplizieren'), '⧉'); add('copy', T('Kopieren'), '⎘'); add('link', T('Link kopieren'), '🔗');
    var x = el('button', 'hk-sel-x', '✕'); x.type = 'button'; x.title = T('Auswahl aufheben (Esc)'); x.setAttribute('aria-label', x.title);
    x.addEventListener('click', clearSel); bar.appendChild(x);
    // Klicks auf die Leiste duerfen die Auswahl von Editor.js nicht aufheben
    bar.addEventListener('mousedown', function (e) { e.preventDefault(); e.stopPropagation(); });
    document.body.appendChild(bar);
  }
  function saveBlocks(s) {
    return Promise.all(s.ids.map(function (id) { var b = s.ed.blocks.getById(id); return b ? b.save().then(function (x) { return x && { id: id, tool: x.tool, data: x.data, tunes: x.tunes }; }) : null; }))
      .then(function (l) { return l.filter(Boolean); });
  }
  function dirty() { var k = K(); if (k && k.dirty) k.dirty(); }
  function act(a, s, btn) {
    var k = K(); if (!k || ro()) return;
    if (a === 'delete') return delBlocks(s);
    if (a === 'dup') return dupBlocks(s);
    if (a === 'copy') return copyBlocks(s);
    if (a === 'link') return linkBlocks(s);
    if (a === 'color') return colorMenu(s, btn);
    if (a === 'convert') return convertMenu(s, btn);
    if (a === 'move') return moveDialog(s, btn);
  }
  function delBlocks(s) {
    var ed = s.ed;
    saveBlocks(s).then(function (saved) {
      var idx = saved.map(function (x) { return ed.blocks.getBlockIndex(x.id); });
      idx.slice().sort(function (a, b) { return b - a; }).forEach(function (i) { if (i >= 0) ed.blocks.delete(i); });
      clearSel(); dirty();
      HK.toast(T('{n} Blöcke gelöscht', { n: saved.length }), T('Rückgängig'), function () {
        saved.forEach(function (x, j) {
          var nb = ed.blocks.insert(x.tool, x.data, undefined, Math.min(idx[j], ed.blocks.getBlocksCount()), false, false, x.id);
          if (x.tunes && x.tunes.hkstyle) K().styleBlock(nb, x.tunes.hkstyle);
        });
        dirty();
      }, 10000);
    });
  }
  function dupBlocks(s) {
    var ed = s.ed;
    saveBlocks(s).then(function (saved) {
      var at = Math.max.apply(null, saved.map(function (x) { return ed.blocks.getBlockIndex(x.id); })) + 1;
      saved.forEach(function (x, j) {
        var nb = ed.blocks.insert(x.tool, JSON.parse(JSON.stringify(x.data)), undefined, at + j, false);
        if (x.tunes && x.tunes.hkstyle) K().styleBlock(nb, x.tunes.hkstyle);
      });
      clearSel(); dirty();
      HK.toast(T('{n} Blöcke dupliziert', { n: saved.length }));
    });
  }
  function plain(html) { var d = new DOMParser().parseFromString('<!doctype html><body>' + String(html || ''), 'text/html'); return d.body.textContent || ''; }
  function blockText(x) {          // Markdown-artig (Zwischenablage text/plain)
    var d = x.data || {};
    if (x.tool === 'header') return new Array(Math.min(6, d.level || 2) + 1).join('#') + ' ' + plain(d.text);
    if (x.tool === 'list') {
      var out = [], walk = function (items, depth) {
        (items || []).forEach(function (it, i) {
          var c = typeof it === 'string' ? it : it.content, mk = d.style === 'ordered' ? (i + 1) + '.' : d.style === 'checklist' ? ((it.meta || {}).checked ? '- [x]' : '- [ ]') : '-';
          out.push(new Array(depth + 1).join('  ') + mk + ' ' + plain(c)); if (it && it.items) walk(it.items, depth + 1);
        });
      };
      walk(d.items, 0); return out.join('\n');
    }
    if (x.tool === 'quote') return '> ' + plain(d.text);
    if (x.tool === 'code') return '```' + (d.language || '') + '\n' + (d.code || '') + '\n```';
    if (x.tool === 'math') return '$$\n' + (d.tex || '') + '\n$$';
    if (typeof d.text === 'string') return plain(d.text);
    return '';
  }
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function blockHtml(x) {
    var d = x.data || {};
    if (x.tool === 'header') { var l = Math.min(6, d.level || 2); return '<h' + l + '>' + esc(plain(d.text)) + '</h' + l + '>'; }
    if (x.tool === 'code' || x.tool === 'math') return '<pre>' + esc(x.tool === 'math' ? d.tex || '' : d.code || '') + '</pre>';
    var t = blockText(x); return t ? '<p>' + esc(t).replace(/\n/g, '<br>') + '</p>' : '';
  }
  function copyBlocks(s) {
    saveBlocks(s).then(function (saved) {
      var json = JSON.stringify(saved.map(function (x) { return { tool: x.tool, data: x.data }; }));
      var text = saved.map(blockText).filter(Boolean).join('\n\n'), html = saved.map(blockHtml).join('');
      var h = function (e) { e.preventDefault(); e.stopImmediatePropagation(); e.clipboardData.setData('application/x-editor-js', json);
                             e.clipboardData.setData('text/html', html); e.clipboardData.setData('text/plain', text); };
      document.addEventListener('copy', h, true);
      var ok = false; try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
      document.removeEventListener('copy', h, true);
      if (!ok && navigator.clipboard) navigator.clipboard.writeText(text);
      HK.toast(T('{n} Blöcke kopiert', { n: saved.length }));
    });
  }
  function linkBlocks(s) {
    var k = K(), c = k.cur(); if (!c) return;
    var links = s.ids.map(function (id) { return location.origin + '/notes#' + c.id + ':' + encodeURIComponent(id); }).join('\n');
    var done = function () { HK.toast(T('Links zu {n} Blöcken kopiert', { n: s.ids.length })); };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(links).then(done, function () { window.prompt(T('Link kopieren:'), links); });
    else window.prompt(T('Link kopieren:'), links);
  }
  function colorMenu(s, btn) {
    var k = K();
    HK.openMenu(btn, function (m) {
      var set = function (st) {
        var n = 0;
        s.holders.forEach(function (h) { var c = h.querySelector('.ce-block__content'); if (c && k.styleAt(c, st)) n++; });
        if (n < s.ids.length) HK.toast(T('{n} Blöcke ohne Farbe (nicht färbbar)', { n: s.ids.length - n }));
        clearSel();
      };
      m.appendChild(HK.menuItem(T('Standard'), function () { set(null); }, { icon: 'A' }));
      COLORS.forEach(function (c) {
        var it = HK.menuItem(COLOR_NAME(c), function () { set({ c: c }); }, { icon: 'A' }); it.querySelector('.mi-ic').classList.add('hk-c-' + c); m.appendChild(it);
      });
      COLORS.forEach(function (c) {
        var it = HK.menuItem(T('{farbe} (Hintergrund)', { farbe: COLOR_NAME(c) }), function () { set({ c: c, bg: true }); }, { icon: 'A' });
        it.querySelector('.mi-ic').classList.add('hk-bg-' + c); m.appendChild(it);
      });
    });
  }
  // Umwandeln: Text der Bloecke (Listen: je Punkt) -> Zieltyp. Liste: alle Punkte in EINE Liste, sonst je Text ein Block
  function textsOf(x) {
    var d = x.data || {}, out = [];
    if (x.tool === 'list') {
      var walk = function (items) { (items || []).forEach(function (it) { out.push(typeof it === 'string' ? it : it.content || ''); if (it && it.items) walk(it.items); }); };
      walk(d.items);
    } else out.push(d.text || '');
    return out;
  }
  function convertMenu(s, btn) {
    var targets = [['paragraph', T('Text'), '¶'], ['h1', T('Überschrift 1'), 'H1'], ['h2', T('Überschrift 2'), 'H2'], ['h3', T('Überschrift 3'), 'H3'],
                   ['unordered', T('Aufzählung'), '•'], ['ordered', T('Nummerierte Liste'), '1.'], ['checklist', T('Checkliste'), '☑'], ['quote', T('Zitat'), '❝']];
    HK.openMenu(btn, function (m) {
      targets.forEach(function (t) { m.appendChild(HK.menuItem(t[1], function () { convert(s, t[0]); }, { icon: t[2] })); });
    });
  }
  function convert(s, target) {
    var ed = s.ed;
    saveBlocks(s).then(function (saved) {
      if (!saved.every(function (x) { return TEXT_TYPES[x.tool]; })) { HK.toast(T('Nur Textblöcke lassen sich umwandeln')); return; }
      var at = Math.min.apply(null, saved.map(function (x) { return ed.blocks.getBlockIndex(x.id); }));
      var list = { unordered: 1, ordered: 1, checklist: 1 }[target], out = [];
      if (list) {
        var items = []; saved.forEach(function (x) { textsOf(x).forEach(function (t) { items.push({ content: t, meta: target === 'checklist' ? { checked: false } : {}, items: [] }); }); });
        out.push({ tool: 'list', data: { style: target, meta: {}, items: items }, id: saved[0].id, st: saved[0].tunes && saved[0].tunes.hkstyle });
      } else {
        saved.forEach(function (x) {
          var t = textsOf(x);
          t.forEach(function (tx, j) {
            var keepId = t.length === 1 || j === 0 ? x.id : undefined, st = x.tunes && x.tunes.hkstyle;
            if (target === 'paragraph') out.push({ tool: 'paragraph', data: { text: tx }, id: keepId, st: st });
            else if (target === 'quote') out.push({ tool: 'quote', data: { text: tx, caption: '', alignment: 'left' }, id: keepId, st: st });
            else out.push({ tool: 'header', data: { text: tx, level: +target.slice(1) }, id: keepId, st: st });
          });
        });
      }
      saved.map(function (x) { return ed.blocks.getBlockIndex(x.id); }).sort(function (a, b) { return b - a; }).forEach(function (i) { if (i >= 0) ed.blocks.delete(i); });
      out.forEach(function (x, j) {
        var nb = ed.blocks.insert(x.tool, x.data, undefined, at + j, false, false, x.id);
        if (x.st) K().styleBlock(nb, x.st);
      });
      clearSel(); dirty();
    });
  }

  // --- Verschieben nach ... (P6-4) / Kopieren nach ... (andere Saga-Server) ---------------------------------------------
  function targetPages() {
    var k = K(), cur = k.cur(), own = (k.pages() || []).map(function (p) { return { p: p, own: true }; });
    var sh = (k.shared ? k.shared() : []).filter(function (p) { return p.role === 'edit' || p.role === 'owner'; }).map(function (p) { return { p: p, own: false }; });
    var all = own.concat(sh).filter(function (x) { return x.p.id !== (cur && cur.id) && x.p.kind !== 'database' && !x.p.archived; });
    var rec = k.recents(), pos = {}; rec.forEach(function (id, i) { pos[id] = i; });
    all.sort(function (a, b) { var ia = a.p.id in pos ? pos[a.p.id] : 1e9, ib = b.p.id in pos ? pos[b.p.id] : 1e9; return ia - ib; });
    return all;
  }
  function moveDialog(s, anchor) {
    var k = K(), cur = k.cur(); if (!cur) return;
    var opts = { start: false };
    HK.openMenu(anchor, function (m) {
      m.classList.add('hk-move-menu');
      m.appendChild(HK.menuLabel(T('Verschieben nach …')));
      var tg = el('button', 'menu-item hk-move-pos'); tg.type = 'button'; tg.setAttribute('role', 'menuitemcheckbox'); tg.setAttribute('aria-checked', 'false');
      tg.appendChild(el('span', 'mi-ic', '⤒')); tg.appendChild(el('span', 'mi-t', T('Am Anfang der Seite einfügen')));
      tg.addEventListener('click', function (e) { e.stopPropagation(); opts.start = !opts.start; tg.classList.toggle('on', opts.start); tg.setAttribute('aria-checked', opts.start ? 'true' : 'false'); });
      m.appendChild(tg);
      targetPages().forEach(function (x) {
        var copy = k.remoteId(cur.id) || k.remoteId(x.p.id);       // ueber Servergrenzen: nur kopieren
        var hint = (k.ancestors(x.p.id) || []).map(k.label).join(' / ');
        if (copy) hint = (hint ? hint + ' · ' : '') + T('wird kopiert');
        m.appendChild(HK.menuItem(k.label(x.p), function () { (copy ? copyTo : moveTo)(s, x.p, opts.start); }, { icon: k.ti(x.p.icon) || '📄', hint: hint }));
      });
    }, { search: T('Seite suchen …') });
  }
  function prevSibling(ed, id) { var i = ed.blocks.getBlockIndex(id); var b = i > 0 ? ed.blocks.getBlockByIndex(i - 1) : null; return b ? b.id : null; }
  function moveTo(s, page, atStart) {
    var k = K(), cur = k.cur(), src = cur.id, ids = s.ids.slice(), before = prevSibling(s.ed, ids[0]);
    var call = function (retry) {
      return k.save().then(function () {
        return HK.api('POST', '/api/notes/' + src + '/blocks/move', { ids: ids, to: page.id, rev_a: k.cur().rev, at: atStart ? 'start' : 'end' });
      }).catch(function (err) {
        if (err.status === 409 && !retry) return k.apply().then(function () { return call(true); });
        throw err;
      });
    };
    clearSel();
    call(false).then(function (d) {
      if (d.files_copied) HK.toast(T('{n} Dateien für die Zielseite kopiert', { n: d.files_copied }));
      return k.apply().then(function () {
        HK.toast(T('Verschoben nach „{name}“', { name: k.label(page) }), T('Rückgängig'), function () {
          HK.api('GET', '/api/notes/' + page.id).then(function (n) {
            var back = { ids: d.ids, to: src, rev_a: n.rev };
            if (before) back.after = before; else back.at = 'start';
            return HK.api('POST', '/api/notes/' + page.id + '/blocks/move', back);
          }).then(function () { return k.apply(); }).then(function () { HK.toast(T('Zurückgeholt')); })
            .catch(function (err) { HK.toast(T('Nicht verschoben: {err}', { err: err.message })); });
        }, 10000);
        var t = document.querySelector('.toast');
        if (t) { var o = el('button', 'toast-action', T('Öffnen')); o.type = 'button'; o.addEventListener('click', function () { t.hidden = true; location.hash = '#' + page.id; }); t.appendChild(o); }
      });
    }).catch(function (err) {
      HK.toast(err.data && err.data.federated ? T('Zwischen verschiedenen Saga-Servern lassen sich Blöcke nicht verschieben – nur kopieren.')
        : T('Nicht verschoben: {err}', { err: err.message }));
    });
  }
  var FILE_REF = /\/files\/[0-9a-f]{24}/g;
  function copyTo(s, page, atStart) {
    var k = K();
    saveBlocks(s).then(function (saved) {
      var json = JSON.stringify(saved.map(function (x) { var o = { type: x.tool, data: x.data }; if (x.tunes && x.tunes.hkstyle) o.tunes = { hkstyle: x.tunes.hkstyle }; return o; }));
      var refs = {};
      (json.match(FILE_REF) || []).forEach(function (r) { refs[r] = 1; });
      return Promise.all(Object.keys(refs).map(function (r) {
        return HK.api('POST', '/api/files/copy', { url: r, note: page.id }).then(function (d) { refs[r] = d.url; });
      })).then(function () {
        var blocks = JSON.parse(json.replace(FILE_REF, function (r) { return refs[r]; }));
        var put = function (retry) {
          return HK.api('GET', '/api/notes/' + page.id).then(function (n) {
            var doc = {}; try { doc = JSON.parse(n.body || '{}'); } catch (e) { doc = {}; }
            if (!Array.isArray(doc.blocks)) doc.blocks = [];
            doc.blocks = atStart ? blocks.concat(doc.blocks) : doc.blocks.concat(blocks);
            return HK.api('PATCH', '/api/notes/' + page.id, { body: JSON.stringify(doc), format: 'blocks', rev: n.rev });
          }).catch(function (err) { if (err.message === 'conflict' && !retry) return put(true); throw err; });
        };
        return put(false);
      }).then(function () { clearSel(); HK.toast(T('Kopiert nach „{name}“', { name: k.label(page) })); });
    }).catch(function (err) { HK.toast(T('Nicht kopiert: {err}', { err: err.message })); });
  }
  // Block-Menue (⋮⋮): "Verschieben nach ..." fuer EINEN Block
  function HKMoveTune(o) { this.api = o.api; this.block = o.block; }
  HKMoveTune.isTune = true;
  HKMoveTune.prototype.render = function () {
    var self = this;
    if (ro()) return [];
    var k = K(), cur = k && k.cur(), copy = !!(cur && k.remoteId(cur.id));
    return { icon: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12h12M12 6l6 6-6 6M20 4v16"/></svg>',
             title: copy ? T('Kopieren nach …') : T('Verschieben nach …'), closeOnActivate: true,
             onActivate: function () {
               var h = self.block && self.block.holder; if (!h) return;
               var redactor = h.parentElement, e = edFor(redactor); if (!e) return;
               var c = h.querySelector('.ce-block__content') || h;
               setTimeout(function () { moveDialog({ ed: e.ed, top: e.top, ids: [self.block.id], holders: [h] }, c); }, 0);
             } };
  };

  // Auswahl beobachten: Editor.js markiert Bloecke mit .ce-block--selected (Ziehen, Umschalt+Pfeile, Strg+A)
  var upd = null;
  function later() { clearTimeout(upd); upd = setTimeout(updateBar, 60); }
  ['mouseup', 'keyup'].forEach(function (ev) { document.addEventListener(ev, later); });
  var mo = new MutationObserver(function (list) {
    for (var i = 0; i < list.length; i++) if (list[i].target.classList && list[i].target.classList.contains('ce-block')) { later(); return; }
  });
  document.addEventListener('hk-page-ready', function () {
    var box = document.getElementById('pg-editor');
    if (box) { mo.disconnect(); mo.observe(box, { attributes: true, attributeFilter: ['class'], subtree: true }); }
    hideBar();
  });
  document.addEventListener('hk-page-open', hideBar);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && bar && !bar.hidden) clearSel(); });
  // Umschalt+Klick auf ⋮⋮: Block zur Auswahl hinzufuegen/entfernen (statt Menue)
  var hovered = null;
  document.addEventListener('mouseover', function (e) { var b = e.target && e.target.closest ? e.target.closest('#pg-editor .ce-block') : null; if (b) hovered = b; }, true);
  window.addEventListener('mousedown', function (e) {
    if (!e.shiftKey || ro()) return;
    var t = e.target && e.target.closest ? e.target.closest('.ce-toolbar__settings-btn') : null;
    if (!t || !hovered) return;
    var ui = t.closest('.codex-editor'); if (!ui || hovered.parentElement.closest('.codex-editor') !== ui) return;
    e.preventDefault(); e.stopPropagation();
    hovered.classList.toggle('ce-block--selected');
    later();
  }, true);
  window.addEventListener('click', function (e) {
    if (e.shiftKey && e.target && e.target.closest && e.target.closest('#pg-editor .ce-toolbar__settings-btn')) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  window.HKColor = HKColor;
  window.HKMathTool = MathTool;
  window.HKMathInline = MathInline;
  window.HKMoveTune = HKMoveTune;
  window.HKEd25 = { mermaidPaint: mermaidPaint, mermaidPng: mermaidPng, renderStale: renderStale, scheduleRender: scheduleRender,
                    applyColor: applyColor, srcHash: srcHash, sha256js: function (s) { return sha256js(new TextEncoder().encode(s)); },
                    renderMermaid: renderMermaid, selection: currentSelection, updateBar: updateBar, moveDialog: moveDialog };
})();
