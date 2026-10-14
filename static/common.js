// Saga - gemeinsame Bausteine fuer Dashboard (notes.js) und Notizen-Seite (pages.js)
// Stellt window.HK bereit. Inhalte werden ausschliesslich per textContent eingefuegt.

// Mehrsprachigkeit: T(deutscherText, {platzhalter: wert}) - Woerterbuch aus /i18n/<lang>.js (window.SAGA_I18N,
// nur abweichende Texte; fehlt ein Eintrag, bleibt der deutsche Schluessel). Sprache = <html lang>.
(function () {
  'use strict';
  var dict = window.SAGA_I18N || {}, lang = (document.documentElement.lang || 'de').toLowerCase();
  window.T = function (key, params) {
    var s = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
    if (params) s = s.replace(/\{(\w+)\}/g, function (m, k) { return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m; });
    return s;
  };
  // Datums-/Zahlenformat: Browser-Region, wenn sie zur Sprache passt (en-GB, es-MX …), sonst Standard der Sprache
  var prefs = navigator.languages || [navigator.language || ''];
  window.SAGA_LANG = lang;
  window.SAGA_LOCALE = prefs.filter(function (l) { return String(l).toLowerCase().split('-')[0] === lang; })[0] ||
    ({ de: 'de-DE', en: 'en-US', es: 'es-ES' }[lang] || lang);
})();

(function () {
  'use strict';
  var LOC = window.SAGA_LOCALE;
  // Wochentage kurz, Montag zuerst (Kalender-Raster)
  function weekdaysShort() {
    var f = new Intl.DateTimeFormat(LOC, { weekday: 'short' }), out = [];
    for (var i = 0; i < 7; i++) out.push(f.format(new Date(2024, 0, 1 + i)).replace(/\.$/, ''));    // 1.1.2024 = Montag
    return out;
  }
  // relative Angabe ("vor 5 Minuten", "in 2 days") - n Sekunden (negativ = Vergangenheit)
  var rtf = window.Intl && Intl.RelativeTimeFormat ? new Intl.RelativeTimeFormat(LOC, { numeric: 'auto' }) : null;
  function relTime(sec) {
    var a = Math.abs(sec), u = 'second', v = sec;
    if (a >= 86400 * 30) { u = 'month'; v = sec / (86400 * 30); } else if (a >= 86400 * 7) { u = 'week'; v = sec / (86400 * 7); }
    else if (a >= 86400) { u = 'day'; v = sec / 86400; } else if (a >= 3600) { u = 'hour'; v = sec / 3600; } else if (a >= 60) { u = 'minute'; v = sec / 60; }
    v = Math.round(v);
    return rtf ? rtf.format(v, u) : String(v) + ' ' + u;
  }
  var H = { 'X-Saga': '1' };
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  // API: Session-Cookie + eigener Header (erzwingt CORS-Preflight -> keine Cross-Site-Requests)
  function api(method, url, body) {
    var h = Object.assign({}, H);
    if (body !== undefined) h['Content-Type'] = 'application/json';
    return fetch(url, { method: method, credentials: 'same-origin', headers: h,
                        body: body !== undefined ? JSON.stringify(body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
          return d;
        });
      });
  }

  // Einstellungen pro Browser (offen/zu, zuletzt geoeffnete Seite …)
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  var fUpd = new Intl.DateTimeFormat(LOC, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  function escapeHtml(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  // Kurze Rueckmeldung unten in der Mitte, optional mit Aktion (z.B. "Rueckgaengig"); ms: Anzeigedauer (sonst 3,5 bzw. 7 s)
  var toastEl = el('div', 'toast'); toastEl.setAttribute('role', 'status'); toastEl.hidden = true;
  document.body.appendChild(toastEl);
  var toastT = null;
  function toast(msg, actionLabel, onAction, ms) {
    toastEl.textContent = msg; toastEl.hidden = false;
    if (actionLabel) {
      var b = el('button', 'toast-action', actionLabel); b.type = 'button';
      b.addEventListener('click', function () { toastEl.hidden = true; onAction(); });
      toastEl.appendChild(b);
    }
    clearTimeout(toastT); toastT = setTimeout(function () { toastEl.hidden = true; }, ms || (actionLabel ? 7000 : 3500));
  }

  // Popup-Menues im Notion-Stil: Symbol + Text + Zusatzinfo, seitliche Untermenues, optionales Suchfeld,
  // Tastatur (Pfeile, Enter, Esc). Ein Hauptmenue + ein Untermenue, beide am <body>.
  var menuEl = el('div', 'menu'); menuEl.setAttribute('role', 'menu'); menuEl.hidden = true;
  var subEl = el('div', 'menu menu-sub'); subEl.setAttribute('role', 'menu'); subEl.hidden = true;
  document.body.appendChild(menuEl); document.body.appendChild(subEl);
  var menuOwner = null, subOwner = null;
  function closeSub() { subEl.hidden = true; subEl.textContent = ''; if (subOwner) { subOwner.classList.remove('sub-open'); subOwner.setAttribute('aria-expanded', 'false'); } subOwner = null; }
  function closeMenu() { closeSub(); menuEl.hidden = true; menuEl.textContent = ''; if (menuOwner) menuOwner.setAttribute('aria-expanded', 'false'); menuOwner = null; }
  function place(m, r, side) {
    var mw = m.offsetWidth, mh = m.offsetHeight, W = window.innerWidth, H = window.innerHeight, left, top;
    if (side) {                                  // Untermenue: rechts neben dem Eintrag, sonst links
      left = r.right + 4 + mw <= W - 8 ? r.right + 4 : r.left - mw - 4;
      top = Math.min(r.top - 6, H - mh - 8);
    } else {                                     // Hauptmenue: unter dem Knopf, sonst darueber
      left = r.left < W / 2 ? r.left : r.right - mw;
      top = r.bottom + 6 + mh > H ? r.top - mh - 6 : r.bottom + 6;
    }
    m.style.left = Math.min(Math.max(8, left), W - mw - 8) + 'px'; m.style.top = Math.max(8, top) + 'px';
  }
  function addSearch(m, placeholder) {
    var s = el('input', 'menu-search'); s.type = 'text'; s.placeholder = placeholder; s.setAttribute('aria-label', placeholder);
    s.addEventListener('input', function () {
      var q = s.value.trim().toLowerCase();
      [].forEach.call(m.querySelectorAll('.menu-item'), function (b) { b.hidden = !!q && b.textContent.toLowerCase().indexOf(q) < 0; });
      [].forEach.call(m.querySelectorAll('.menu-label, .menu-sep, .menu-hint'), function (x) { x.hidden = !!q; });
    });
    s.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); var f = visibleItems(m)[0]; if (f) f.click(); }
    });
    m.insertBefore(s, m.firstChild); return s;
  }
  function openMenu(anchor, build, opts) {
    if (menuOwner === anchor) { closeMenu(); return; }
    closeMenu(); build(menuEl);
    var search = opts && opts.search ? addSearch(menuEl, opts.search) : null;
    menuEl.hidden = false; menuOwner = anchor; anchor.setAttribute('aria-expanded', 'true');
    place(menuEl, anchor.getBoundingClientRect(), false);
    var first = search || menuEl.querySelector('button'); if (first) first.focus();
  }
  function openSub(item, build, opts) {
    if (subOwner === item) return;
    closeSub(); build(subEl);
    if (opts && opts.search) addSearch(subEl, opts.search);
    subEl.hidden = false; subOwner = item; item.classList.add('sub-open'); item.setAttribute('aria-expanded', 'true');
    place(subEl, item.getBoundingClientRect(), true);
  }
  // menuItem(text, aktion, {icon, hint, on, danger, sub: function(menu){...}, subSearch: 'Platzhalter'})
  function menuItem(label, onClick, opts) {
    opts = opts || {};
    var b = el('button', 'menu-item' + (opts.on ? ' on' : '') + (opts.danger ? ' danger' : '') + (opts.sub ? ' has-sub' : ''));
    b.type = 'button'; b.setAttribute('role', 'menuitem');
    if (opts.icon) b.appendChild(el('span', 'mi-ic', opts.icon));
    b.appendChild(el('span', 'mi-t', label));
    if (opts.hint) b.appendChild(el('span', 'mi-hint', opts.hint));
    if (opts.sub) {
      b.appendChild(el('span', 'mi-arrow', '›')); b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
      var openIt = function () { openSub(b, opts.sub, { search: opts.subSearch }); };
      b.addEventListener('mouseenter', openIt);
      b.addEventListener('click', function () { openIt(); var f = subEl.querySelector('input.menu-search, .menu-item'); if (f) f.focus(); });
    } else {
      b.addEventListener('mouseenter', function () { if (menuEl.contains(b)) closeSub(); });   // anderes Hauptmenue-Element -> Untermenue zu
      b.addEventListener('click', function () { closeMenu(); if (onClick) onClick(); });
    }
    return b;
  }
  function menuLabel(text) { return el('div', 'menu-label', text); }
  function visibleItems(m) { return [].filter.call(m.querySelectorAll('.menu-item'), function (b) { return !b.hidden && !b.disabled; }); }
  document.addEventListener('mousedown', function (e) {
    if (menuOwner && !menuEl.contains(e.target) && !subEl.contains(e.target) && !menuOwner.contains(e.target)) closeMenu();
  });
  document.addEventListener('keydown', function (e) {
    if (!menuOwner) return;
    var inSub = subEl.contains(document.activeElement), m = inSub ? subEl : menuEl;
    var a = document.activeElement, onItem = a && a.classList && a.classList.contains('menu-item'), onSearch = a && a.classList && a.classList.contains('menu-search');
    if (e.key === 'Escape') {
      e.preventDefault();
      if (!subEl.hidden && subOwner) { var so = subOwner; closeSub(); so.focus(); }
      else { var o = menuOwner; closeMenu(); o.focus(); }
      return;
    }
    if (!onItem && !onSearch) return;                       // Eingabefelder/Auswahllisten im Menue nicht stoeren
    var items = visibleItems(m), i = items.indexOf(a);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); if (!items.length) return;
      var n = e.key === 'ArrowDown' ? (i + 1) % items.length : (i <= 0 ? items.length - 1 : i - 1);
      items[n].focus();
    } else if (e.key === 'ArrowRight' && onItem && a.classList.contains('has-sub')) {
      e.preventDefault(); a.click();
    } else if (e.key === 'ArrowLeft' && inSub && subOwner) {
      e.preventDefault(); var so2 = subOwner; closeSub(); so2.focus();
    }
  });
  window.addEventListener('resize', closeMenu);
  // Scrollen der Seite schliesst Menues - Scrollen INNERHALB eines Menues (lange Listen) nicht
  window.addEventListener('scroll', function (e) {
    var tg = e.target;                         // kann auch das Fenster/Dokument selbst sein (kein Element)
    if (!(tg instanceof Element) || (!menuEl.contains(tg) && !subEl.contains(tg))) closeMenu();
  }, true);

  // Waagrecht scrollende Leisten (Reiter auf dem Handy): Verlauf am Rand, solange dort noch etwas steht,
  // und den aktiven Eintrag sichtbar machen. Rueckgabe: Funktion zum Nachziehen (z. B. nach Reiterwechsel).
  function scrollFade(bar, active) {
    if (!bar) return function () {};
    function upd() {
      var max = bar.scrollWidth - bar.clientWidth;
      bar.classList.toggle('fade-l', bar.scrollLeft > 2);
      bar.classList.toggle('fade-r', max > 2 && bar.scrollLeft < max - 2);
    }
    function reveal(a) {
      a = a || (active && bar.querySelector(active));
      if (a && bar.scrollWidth > bar.clientWidth) {
        var l = a.getBoundingClientRect().left - bar.getBoundingClientRect().left + bar.scrollLeft, r = l + a.offsetWidth;
        if (l < bar.scrollLeft + 24) bar.scrollLeft = Math.max(0, l - 24);
        else if (r > bar.scrollLeft + bar.clientWidth - 24) bar.scrollLeft = r - bar.clientWidth + 24;
      }
      upd();
    }
    bar.addEventListener('scroll', upd, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(function () { upd(); }).observe(bar);     // faellt mit der Leiste weg
    else window.addEventListener('resize', upd);
    reveal();
    return reveal;
  }

  // Adresse zur Anzeige: Umbruch nur nach "/" (je Abschnitt ein Stueck, dazwischen <wbr> = U+200B nur in der
  // Darstellung - beim Kopieren bleibt die Adresse unveraendert). Zu lange Abschnitte duerfen notfalls umbrechen.
  function urlNode(url, tag, cls) {
    var s = String(url || ''), o = /^[a-z][a-z0-9+.-]*:\/\/[^\/]*\/?/i.exec(s), head = o ? o[0] : '';   // "https://host/" bleibt ganz
    var n = el(tag || 'span', cls), parts = (head ? [head] : []).concat(s.slice(head.length).replace(/\//g, '/\n').split('\n'))
      .filter(function (p) { return p; });
    n.classList.add('hk-url');
    parts.forEach(function (p, i) { n.appendChild(el('span', 'hk-url-seg', p)); if (i < parts.length - 1) n.appendChild(document.createElement('wbr')); });
    return n;
  }
  // Uebersetzten Text mit Platzhaltern als Knoten: fmtNodes(T('Neue Adresse: {url} (bisher {old})'), {url: node|text, ...})
  function fmtNodes(s, params) {
    var f = document.createDocumentFragment(), re = /\{(\w+)\}/g, last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) f.appendChild(document.createTextNode(s.slice(last, m.index)));
      var v = Object.prototype.hasOwnProperty.call(params, m[1]) ? params[m[1]] : m[0];
      f.appendChild(v instanceof Node ? v : document.createTextNode(String(v)));
      last = re.lastIndex;
    }
    if (last < s.length) f.appendChild(document.createTextNode(s.slice(last)));
    return f;
  }

  // Fehler direkt am Feld: Feld markiert (aria-invalid), Text darunter. Ohne Fehler steht dort der Hilfetext (oder
  // nichts). -> set(fehler); set.hint(text) tauscht den Hilfetext.
  var fmSeq = 0;
  function fieldMsg(inp, box, hint) {
    box.id = box.id || 'hk-fm-' + (++fmSeq);
    inp.setAttribute('aria-describedby', box.id);
    var cur = '';
    function draw() {
      inp.classList.toggle('is-err', !!cur);
      if (cur) inp.setAttribute('aria-invalid', 'true'); else inp.removeAttribute('aria-invalid');
      box.textContent = cur || hint || ''; box.className = 'hk-fieldmsg' + (cur ? ' err' : ''); box.hidden = !cur && !hint;
    }
    function set(err) { cur = err || ''; draw(); }
    set.hint = function (h) { hint = h || ''; draw(); };
    draw();
    return set;
  }

  // Einheitliche Rueckfrage statt confirm(): Titel, Text, [Abbrechen] [Bestaetigen]. -> Promise<true|false>.
  // opts: {title, text (Text oder Knoten), ok, cancel, danger}. Esc/Klick daneben = Abbrechen; Fokus bleibt im Dialog,
  // bei destruktiven Aktionen startet er auf "Abbrechen". Liegt ueber allen anderen Dialogen.
  var askOpen = null;
  function ask(opts) {
    if (askOpen) askOpen(false);
    return new Promise(function (resolve) {
      var prev = document.activeElement;
      var ov = el('div', 'hk-ask'), box = el('div', 'hk-ask-box');
      var uid = 'hk-ask-' + Date.now();
      box.setAttribute('role', 'alertdialog'); box.setAttribute('aria-modal', 'true');
      box.setAttribute('aria-labelledby', uid + '-t'); box.setAttribute('aria-describedby', uid + '-d');
      var h = el('h2', 'hk-ask-t', opts.title); h.id = uid + '-t';
      var p = el('p', 'hk-ask-d'); p.id = uid + '-d';
      if (opts.text instanceof Node) p.appendChild(opts.text); else p.textContent = opts.text || '';
      var bar = el('div', 'hk-ask-bar');
      var no = el('button', 'btn', opts.cancel || window.T('Abbrechen')); no.type = 'button';
      var yes = el('button', 'btn ' + (opts.danger ? 'btn-destr' : 'btn-primary'), opts.ok); yes.type = 'button';
      bar.appendChild(no); bar.appendChild(yes);
      box.appendChild(h); box.appendChild(p); box.appendChild(bar); ov.appendChild(box);
      function done(v) {
        if (askOpen !== done) return;
        askOpen = null; window.removeEventListener('keydown', key, true); ov.remove();
        if (prev && prev.focus && document.contains(prev)) { try { prev.focus(); } catch (e) { /* egal */ } }
        resolve(v);
      }
      function key(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); done(false); }
        else if (e.key === 'Tab') { e.preventDefault(); (document.activeElement === no ? yes : no).focus(); }
      }
      no.addEventListener('click', function () { done(false); });
      yes.addEventListener('click', function () { done(true); });
      ov.addEventListener('mousedown', function (e) { if (e.target === ov) done(false); });
      window.addEventListener('keydown', key, true);
      askOpen = done; document.body.appendChild(ov);
      (opts.danger ? no : yes).focus();
    });
  }

  window.HK = { scrollFade: scrollFade, $: $, el: el, t: window.T, lang: window.SAGA_LANG, locale: LOC, weekdaysShort: weekdaysShort, relTime: relTime, api: api, store: store, fUpd: fUpd, escapeHtml: escapeHtml, toast: toast,
                openMenu: openMenu, closeMenu: closeMenu, menuItem: menuItem, menuLabel: menuLabel,
                menuOpen: function () { return !!menuOwner; }, closeSub: closeSub, ask: ask, urlNode: urlNode, fmtNodes: fmtNodes, fieldMsg: fieldMsg };
})();
