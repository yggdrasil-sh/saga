// Saga - gemeinsame Bausteine fuer Dashboard (notes.js) und Notizen-Seite (pages.js)
// Stellt window.HK bereit. Inhalte werden ausschliesslich per textContent eingefuegt.

// Mehrsprachigkeit: T(deutscherText, {platzhalter: wert}) - Woerterbuch aus /i18n/<lang>.js (window.SAGA_I18N,
// nur abweichende Texte; fehlt ein Eintrag, bleibt der deutsche Schluessel). Sprache = <html lang>.
(function () {
  'use strict';
  var dict = window.SAGA_I18N || {}, lang = (document.documentElement.lang || 'de').toLowerCase();
  window.T = function (key, params) {
    var s = Object.prototype.hasOwnProperty.call(dict, key) ? dict[key] : key;
    if (s === key && key.indexOf('##') > 0) s = key.slice(0, key.indexOf('##'));     // 'Lesen##Rolle': Kontext nur im Schluessel
    if (params) {
      // Pluralformen {n:eins|wenige|viele} (Reihenfolge wie PLURAL_FORMS in saga/i18n) vor den Platzhaltern
      s = s.replace(/\{(\w+):([^{}]*)\}/g, function (m, k, forms) {
        if (!Object.prototype.hasOwnProperty.call(params, k)) return m;
        var f = forms.split('|'), i = PLURAL_ORDER.indexOf(pluralCat(params[k]));
        return f[Math.min(i < 0 ? f.length - 1 : i, f.length - 1)];
      });
      s = s.replace(/\{(\w+)\}/g, function (m, k) { return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : m; });
    }
    return s;
  };
  // CLDR-Kardinalregeln: de/en/es eins|mehrere, ru eins|wenige|viele, th keine Formen
  var PLURAL_ORDER = { ru: ['one', 'few', 'many'], th: ['other'] }[lang] || ['one', 'other'];
  function pluralCat(n) {
    var f = Number(n);
    if (lang === 'th' || !isFinite(f) || f !== Math.floor(f)) return 'other';
    var i = Math.abs(f);
    if (lang === 'ru') {
      if (i % 10 === 1 && i % 100 !== 11) return 'one';
      if (i % 10 >= 2 && i % 10 <= 4 && !(i % 100 >= 12 && i % 100 <= 14)) return 'few';
      return 'many';
    }
    return i === 1 ? 'one' : 'other';
  }
  // Datums-/Zahlenformat: Browser-Region, wenn sie zur Sprache passt (en-GB, es-MX …), sonst Standard der Sprache
  var prefs = navigator.languages || [navigator.language || ''];
  window.SAGA_LANG = lang;
  window.SAGA_LOCALE = prefs.filter(function (l) { return String(l).toLowerCase().split('-')[0] === lang; })[0] ||
    ({ de: 'de-DE', en: 'en-US', es: 'es-ES', ru: 'ru-RU', th: 'th-TH' }[lang] || lang);
  // Thai: gregorianischer Kalender statt buddhistischer Jahreszaehlung (th-TH -> th-TH-u-ca-gregory)
  if (lang === 'th' && !/-u-.*ca-/.test(window.SAGA_LOCALE)) window.SAGA_LOCALE += '-u-ca-gregory';
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
    // erst auf die Einheit runden, dann pruefen, ob die naechste passt: 59,6 s = "vor 1 Minute", nicht "vor 60 Sekunden" (BUG-099)
    var U = [['second', 1, 60], ['minute', 60, 60], ['hour', 3600, 24], ['day', 86400, 7], ['week', 86400 * 7, 30 / 7], ['month', 86400 * 30, Infinity]];
    var u = 'second', v = Math.round(sec);
    for (var i = 0; i < U.length; i++) {
      u = U[i][0]; v = Math.round(sec / U[i][1]);
      if (U[i][0] === 'week' ? Math.abs(sec) < 86400 * 30 : Math.abs(v) < U[i][2]) break;
    }
    return rtf ? rtf.format(v, u) : String(v) + ' ' + u;
  }
  // Kennung dieses Tabs (nur im Speicher, je Laden neu): der Live-Kanal schickt eigene Aenderungen an alle anderen
  // Tabs, nur nicht an den ausloesenden zurueck (X-Saga-Tab bei Anfragen, ?tab= beim Live-Kanal)
  var TAB = (function () {
    var a = new Uint8Array(12);
    try { crypto.getRandomValues(a); } catch (e) { for (var i = 0; i < a.length; i++) a[i] = Math.floor(Math.random() * 256); }
    return Array.prototype.map.call(a, function (x) { return ('0' + x.toString(16)).slice(-2); }).join('');
  })();
  var H = { 'X-Saga': '1', 'X-Saga-Tab': TAB, 'X-Saga-Chat': '3' };       // X-Saga-Chat: 2 = saga-chat/v2 (2.3), 3 = Stufe 3 (Krypto ueber Handles, 2.5)
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  // API: Session-Cookie + eigener Header (erzwingt CORS-Preflight -> keine Cross-Site-Requests)
  // Beim Verlassen/Neuladen der Seite (leaving) mit keepalive: so ueberlebt das letzte Speichern das Entladen (BUG-012).
  // keepalive erlaubt hoechstens 64 KB je Anfrage - groessere Inhalte gehen normal (dann warnt beforeunload, s. pages.js).
  var leaving = false, leaveT = null;
  // Grenze gilt in Bytes: Umlaute/Emoji/CJK sind in UTF-8 2-4 Bytes - sonst lehnt fetch die keepalive-Anfrage ab
  function utf8Len(s) { return s.length < 15000 ? s.length * 3 : (window.TextEncoder ? new TextEncoder().encode(s).length : s.length * 3); }
  function api(method, url, body) {
    var h = Object.assign({}, H), data = body !== undefined ? JSON.stringify(body) : undefined;
    if (body !== undefined) h['Content-Type'] = 'application/json';
    return fetch(url, { method: method, credentials: 'same-origin', headers: h, body: data,
                        keepalive: leaving && method !== 'GET' && (!data || utf8Len(data) < 60000) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) { var e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; e.data = d; throw e; }   // Konflikt: e.data.by
          return d;
        });
      });
  }

  // Seite wird verlassen (beforeunload/pagehide): offenes Eingabefeld einer Datenbank-Zelle abschliessen (sein blur
  // speichert), danach laufen Speicher-Anfragen kurz mit keepalive. Wird das Verlassen abgebrochen, endet der Modus wieder.
  function leave() {
    leaving = true; clearTimeout(leaveT); leaveT = setTimeout(function () { leaving = false; }, 3000);
    var a = document.activeElement;
    if (a && a.matches && a.matches('.val-in, .db-title-in')) a.blur();
  }
  window.addEventListener('beforeunload', leave, true);
  window.addEventListener('pagehide', leave, true);

  // Zahl aus einer Eingabe in der Sprache der Oberflaeche lesen: de/es "1.200,5", en "1,200.5" (BUG-013). Ein einzelnes
  // Trennzeichen gilt als Tausenderpunkt nur bei 3er-Gruppen ("1.200" de = 1200), sonst als Komma ("1.5" = 1,5).
  // Ergebnis: Zahl oder NaN.
  var DEC = (function () {
    try { var p = new Intl.NumberFormat(LOC).formatToParts(1.5).filter(function (x) { return x.type === 'decimal'; })[0]; return p ? p.value : '.'; }
    catch (e) { return '.'; }
  })();
  function parseNum(s) {
    s = String(s == null ? '' : s).replace(/[\s'’]/g, '').replace(/^−/, '-');
    if (!s) return NaN;
    if (/^[+-]?\d+$/.test(s)) return Number(s);
    var hasDot = s.indexOf('.') >= 0, hasComma = s.indexOf(',') >= 0;
    if (hasDot && hasComma) {                                  // beide: das hintere ist das Dezimalzeichen
      var d = s.lastIndexOf('.') > s.lastIndexOf(',') ? '.' : ',', g = d === '.' ? ',' : '.';
      var re = new RegExp('^[+-]?\\d{1,3}(\\' + g + '\\d{3})+\\' + d + '\\d*$');
      return re.test(s) ? Number(s.split(g).join('').replace(d, '.')) : NaN;
    }
    if (!hasDot && !hasComma) return /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(s) ? Number(s) : NaN;
    var c = hasDot ? '.' : ',', n = s.split(c).length - 1;
    var grouped = new RegExp('^[+-]?\\d{1,3}(\\' + c + '\\d{3})+$').test(s);
    if (grouped && (c !== DEC || n > 1)) return Number(s.split(c).join(''));        // Tausender-Trennung
    if (n === 1 && /^[+-]?(\d+[.,]\d*|[.,]\d+)$/.test(s)) return Number(s.replace(c, '.'));     // Dezimalzeichen
    return NaN;
  }
  // Zahl zum Bearbeiten in der Sprache der Oberflaeche (ohne Tausendertrennung): de 1234,5 / en 1234.5
  function editNum(v) {
    if (v == null || v === '' || isNaN(Number(v))) return v == null ? '' : String(v);
    var s = String(Number(v));
    return /e/i.test(s) ? s : s.replace('.', DEC);
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
  function closeSub() { subEl.hidden = true; subEl.textContent = ''; menuEl.classList.remove('menu-covered'); if (subOwner) { subOwner.classList.remove('sub-open'); subOwner.setAttribute('aria-expanded', 'false'); } subOwner = null; }
  var lastOwner = null;     // zuletzt geschlossenes Menue: Ausloeser (Dialoge geben den Fokus dorthin zurueck)
  function closeMenu() { closeSub(); menuEl.hidden = true; menuEl.textContent = ''; if (menuOwner) { menuOwner.setAttribute('aria-expanded', 'false'); lastOwner = menuOwner; } menuOwner = null; }
  function place(m, r, side) {
    var mw = m.offsetWidth, mh = m.offsetHeight, W = window.innerWidth, H = window.innerHeight, left, top;
    if (side) {                                  // Untermenue: rechts neben dem Eintrag, sonst links
      if (r.right + 4 + mw <= W - 8) left = r.right + 4;
      else if (r.left - mw - 4 >= 8) left = r.left - mw - 4;
      else return false;                         // passt nirgends daneben (schmales Fenster, Peek-Rahmen) -> openSub
      top = Math.min(r.top - 6, H - mh - 8);
    } else {                                     // Hauptmenue: unter dem Knopf, sonst darueber
      left = r.left < W / 2 ? r.left : r.right - mw;
      top = r.bottom + 6 + mh > H ? r.top - mh - 6 : r.bottom + 6;
    }
    // linker/oberer Rand hat Vorrang: lieber rechts/unten scrollen als links abgeschnitten (max-width/-height im CSS)
    m.style.left = Math.max(8, Math.min(left, W - mw - 8)) + 'px'; m.style.top = Math.max(8, Math.min(top, H - mh - 8)) + 'px';
    return true;
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
    menuEl.classList.toggle('menu-top', !!(anchor.closest && anchor.closest('.modal, .hk-modal')));   // aus einem Dialog: darueber
    menuEl.classList.toggle('menu-call', !!(anchor.closest && anchor.closest('.cl-win')));           // aus dem Anruffenster (z 200)
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
    if (place(subEl, item.getBoundingClientRect(), true)) return;
    if (opts && opts.hover) { closeSub(); return; }             // nur beim Ueberfahren: Menue nicht ungefragt austauschen
    // Kein Platz neben dem Menue: Untermenue tritt an die Stelle des Hauptmenues (das wird ausgeblendet, statt
    // darueber offen zu liegen); oben "‹ <Eintrag>" fuehrt zurueck (wie Esc / Pfeil links).
    var back = el('button', 'menu-back'); back.type = 'button';
    back.appendChild(el('span', 'mi-ic', '‹')); back.appendChild(el('span', 'mi-t', (item.querySelector('.mi-t') || item).textContent));
    back.addEventListener('click', function () { var so = subOwner; closeSub(); if (so) so.focus(); });
    subEl.insertBefore(back, subEl.firstChild);
    var mr = menuEl.getBoundingClientRect();
    menuEl.classList.add('menu-covered');
    place(subEl, { left: mr.left, right: mr.right, top: mr.top - 6, bottom: mr.top - 6 }, false);
    subEl.style.top = Math.max(8, Math.min(mr.top, window.innerHeight - subEl.offsetHeight - 8)) + 'px';
  }
  // menuItem(text, aktion, {icon, hint, hintBelow, on, danger, sub: function(menu){...}, subSearch: 'Platzhalter'})
  function menuItem(label, onClick, opts) {
    opts = opts || {};
    var b = el('button', 'menu-item' + (opts.on ? ' on' : '') + (opts.danger ? ' danger' : '') + (opts.sub ? ' has-sub' : ''));
    b.type = 'button'; b.setAttribute('role', 'menuitem');
    if (opts.icon) b.appendChild(el('span', 'mi-ic', opts.icon));
    if (opts.hint && opts.hintBelow) {                 // Bughunt D13: langer Hinweis als zweite Zeile (sonst abgeschnitten)
      var tx = el('span', 'mi-tx'); tx.appendChild(el('span', 'mi-t', label)); tx.appendChild(el('span', 'mi-hint', opts.hint)); b.appendChild(tx);
    } else {
      b.appendChild(el('span', 'mi-t', label));
      if (opts.hint) b.appendChild(el('span', 'mi-hint', opts.hint));
    }
    if (opts.sub) {
      b.appendChild(el('span', 'mi-arrow', '›')); b.setAttribute('aria-haspopup', 'menu'); b.setAttribute('aria-expanded', 'false');
      var openIt = function () { openSub(b, opts.sub, { search: opts.subSearch }); };
      b.addEventListener('mouseenter', function () { openSub(b, opts.sub, { search: opts.subSearch, hover: true }); });
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

  // Profilbild der KI (2.1, Persona aus /api/ai/status bzw. /api/ai/persona): neutrales Symbol, darueber das Bild,
  // sobald es geladen ist (404/Fehler = Symbol bleibt). px = Anzeigegroesse.
  function aiAvatar(persona, px, cls) {
    var box = document.createElement('span'); box.className = 'ai-av' + (cls ? ' ' + cls : '');
    box.setAttribute('aria-hidden', 'true'); box.textContent = '✦';
    if (px) box.style.setProperty('--ai-av', px + 'px');
    var url = persona && persona.avatar;
    if (typeof url === 'string' && url.indexOf('/api/ai/avatar') === 0) {
      var img = document.createElement('img'); img.alt = ''; img.decoding = 'async'; img.draggable = false;
      img.addEventListener('load', function () { box.classList.add('ai-av-on'); });
      img.addEventListener('error', function () { img.remove(); box.classList.remove('ai-av-on'); });
      img.src = url + '&s=' + Math.min(256, Math.round((px || 24) * Math.min(2, window.devicePixelRatio || 1)));
      box.appendChild(img);
    }
    return box;
  }
  window.HKAIAvatar = aiAvatar;

  // Profilbild (saga/avatars.py) ueber die Initialen legen: box = vorhandenes Initialen-Element, url = person.avatar (oder
  // leer = Initialen bleiben). Das Bild erscheint erst nach dem Laden; 404/Fehler = Initialen bleiben. px = Anzeigegroesse.
  function avatarImg(box, url, px) {
    if (!box || !url || typeof url !== 'string' || url.indexOf('/api/avatar/') !== 0) return box;
    var img = document.createElement('img'); img.className = 'hk-av-img'; img.alt = ''; img.decoding = 'async';
    img.setAttribute('aria-hidden', 'true'); img.draggable = false;
    box.classList.add('hk-av-box');
    img.addEventListener('load', function () { box.classList.add('hk-av-on'); });
    var src = url + (url.indexOf('?') < 0 ? '?' : '&') + 's=' + Math.min(256, Math.round((px || 32) * Math.min(2, window.devicePixelRatio || 1)));
    var tries = 0;
    // Einmal nachfassen: beim Seitenwechsel oder kurz nach dem Start scheitert ein Bild manchmal nur voruebergehend
    // (viele gleichzeitige Anfragen, Sitzung wird gerade erneuert) - sonst blieben bis zum naechsten Neuzeichnen Initialen
    img.addEventListener('error', function () {
      if (tries++ === 0 && img.isConnected) { setTimeout(function () { if (img.isConnected) img.src = src + '&r=1'; }, 1500); return; }
      img.remove(); box.classList.remove('hk-av-on');
    });
    img.src = src;
    box.appendChild(img);
    return box;
  }

  // D01: rohe Kennung ('~' + 16 Zeichen, ggf. '@host') nie zeigen - lesbarer Ersatz wie auf dem Server (saga/core.unnamed).
  // Der Server liefert Namen schon so; das hier faengt Kennungen ab, die als Name in Systemzeilen u. Ae. stehen.
  function personName(n, host) {
    var m = /^~([a-z2-7]{16})(?:@([^@\s]+))?$/.exec(String(n || '').trim());
    if (!m) return n;
    var tag = m[1].slice(-4), h = m[2] || host || '';
    return h ? T('Person auf {host} ({tag})', { host: h, tag: tag }) : T('Unbekannte Person ({tag})', { tag: tag });
  }

  window.HK = { personName: personName, tab: TAB, avatarImg: avatarImg, parseNum: parseNum, editNum: editNum, scrollFade: scrollFade, $: $, el: el, t: window.T, lang: window.SAGA_LANG, locale: LOC, weekdaysShort: weekdaysShort, relTime: relTime, api: api, store: store, fUpd: fUpd, escapeHtml: escapeHtml, toast: toast,
                openMenu: openMenu, closeMenu: closeMenu, lastMenuOwner: function () { return lastOwner; }, menuItem: menuItem, menuLabel: menuLabel,
                menuOpen: function () { return !!menuOwner; }, closeSub: closeSub, ask: ask, urlNode: urlNode, fmtNodes: fmtNodes, fieldMsg: fieldMsg };
})();
