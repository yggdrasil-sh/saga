// Bedienung wie Notion: Tastenkuerzel, Block-Aktionsmenue (Cmd+/), Zurueck/Vor, Inhaltsverzeichnis, Emoji per ":"
// Nutzt die Bausteine aus pages.js (window.HKPages.k).
(function () {
  'use strict';
  if (!window.HKPages || !HKPages.k || !document.body.classList.contains('notes-page')) return;
  var K = HKPages.k, $ = HK.$, el = HK.el;
  var isMac = K.isMac, MK = isMac ? '⌘' : 'Strg+', ALT = isMac ? '⌥' : 'Alt+';
  function mod(e) { return isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey; }
  function inEditor() { var a = document.activeElement; return !!(a && $('pg-editor').contains(a) && (a.isContentEditable || a.closest('[contenteditable="true"]'))); }
  function typing() { var a = document.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable); }

  // Editor, in dem der Cursor steht (auch in Toggle/Spalte), und der aktuelle Block darin
  function owner() {
    var a = document.activeElement; if (!a || !$('pg-editor').contains(a)) return null;
    var best = null;
    K.eds().forEach(function (x) { if (x.holder.contains(a) && (!best || best.holder.contains(x.holder))) best = x; });
    return best ? best.ed : K.editor();
  }
  function current() {
    var ed = owner(); if (!ed) return null;
    var i = ed.blocks.getCurrentBlockIndex(), b = i >= 0 ? ed.blocks.getBlockByIndex(i) : null;
    return b ? { ed: ed, i: i, b: b } : null;
  }

  // --- Block umwandeln -------------------------------------------------------------------
  var TYPES = [   // [Bezeichnung, Schluessel, Symbol, Suchwoerter, Ziffer fuer Cmd+Alt+Ziffer]
    ['Text', 'p', '¶', 'text absatz paragraph', 0], ['Überschrift 1', 'h1', 'H1', 'heading titel', 1],
    ['Überschrift 2', 'h2', 'H2', 'heading', 2], ['Überschrift 3', 'h3', 'H3', 'heading', 3], ['Überschrift 4', 'h4', 'H4', 'heading', null],
    ['Checkliste', 'checklist', '☑', 'todo aufgabe haken', 4], ['Aufzählung', 'unordered', '•', 'liste bullet punkte', 5],
    ['Nummerierte Liste', 'ordered', '1.', 'nummer liste', 6], ['Toggle', 'toggle', '▸', 'ausklappen aufklappen', 7],
    ['Code', 'code', '</>', 'code quelltext', 8], ['Zitat', 'quote', '❝', 'zitat quote', null], ['Hinweis (Callout)', 'callout', '💡', 'callout hinweis box', null]
  ];
  var CONVERTIBLE = { paragraph: 1, header: 1, list: 1, quote: 1, code: 1, toggle: 1, callout: 1 };
  var COPYABLE = { paragraph: 1, header: 1, list: 1, quote: 1, code: 1, toggle: 1, callout: 1, delimiter: 1, table: 1, image: 1 };
  function esc(s) { return HK.escapeHtml(String(s || '')); }
  function flat(items, out) {
    (items || []).forEach(function (it) { out.push(typeof it === 'string' ? it : it.content || ''); if (it && it.items) flat(it.items, out); });
    return out;
  }
  function textOf(tool, d) {
    if (tool === 'list') return flat(d.items, []).join('<br>');
    if (tool === 'code') return esc(d.code).replace(/\n/g, '<br>');
    if (tool === 'toggle') return d.title || '';
    return d.text || '';
  }
  function build(key, html) {
    var lines = String(html || '').split(/<br\s*\/?>/i);
    switch (key) {
      case 'p': return ['paragraph', { text: html }];
      case 'h1': case 'h2': case 'h3': case 'h4': return ['header', { text: lines.join(' '), level: +key.slice(1) }];
      case 'checklist': case 'unordered': case 'ordered':
        return ['list', { style: key, meta: {}, items: lines.map(function (c) { return { content: c, meta: key === 'checklist' ? { checked: false } : {}, items: [] }; }) }];
      case 'toggle': return ['toggle', { title: lines.join(' '), open: true, blocks: [] }];
      case 'code': var tmp = document.createElement('div'); tmp.innerHTML = String(html || '').replace(/<br\s*\/?>/gi, '\n'); return ['code', { code: tmp.textContent }];
      case 'quote': return ['quote', { text: html, caption: '', alignment: 'left' }];
      case 'callout': return ['callout', { emoji: '💡', text: html }];
    }
    return null;
  }
  function convert(c, key) {
    if (!CONVERTIBLE[c.b.name]) { HK.toast('Dieser Block lässt sich nicht umwandeln'); return; }
    c.b.save().then(function (s) {
      if (s.tool === 'toggle' && s.data.blocks && s.data.blocks.length) { HK.toast('Der Toggle hat Inhalt – erst ausräumen, dann umwandeln'); return; }
      var r = build(key, textOf(s.tool, s.data)); if (!r) return;
      c.ed.blocks.insert(r[0], r[1], undefined, c.i, true, true);
      try { c.ed.caret.setToBlock(c.i, 'end'); } catch (e) { /* Block ohne Textfeld */ }
    });
  }
  function duplicate(c) {
    if (!COPYABLE[c.b.name]) { HK.toast('Diesen Block bitte über das Block-Menü kopieren'); return; }
    c.b.save().then(function (s) {
      c.ed.blocks.insert(s.tool, s.data, undefined, c.i + 1, true);
      try { c.ed.caret.setToBlock(c.i + 1, 'end'); } catch (e) { /* ohne Textfeld */ }
    });
  }
  function move(c, d) {
    var to = c.i + d; if (to < 0 || to >= c.ed.blocks.getBlocksCount()) return;
    c.ed.blocks.move(to, c.i);
    try { c.ed.caret.setToBlock(to, 'end'); } catch (e) { /* ohne Textfeld */ }
  }
  function blockLink(c) {
    if (c.ed !== K.editor()) { HK.toast('Link zu Blöcken in Toggles/Spalten: bitte den äußeren Block verlinken'); }
    K.copyLink(c.b.id);
  }

  // Cmd+/ : alle Aktionen fuer den aktuellen Block, mit Suche
  function actionMenu(c) {
    HK.openMenu(c.b.holder, function (m) {
      m.appendChild(HK.menuLabel('Umwandeln in'));
      TYPES.forEach(function (t) {
        m.appendChild(HK.menuItem(t[0], function () { convert(c, t[1]); }, { icon: t[2], hint: t[4] != null ? MK + ALT + t[4] : '' }));
      });
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem('Duplizieren', function () { duplicate(c); }, { icon: '⧉', hint: MK + 'D' }));
      m.appendChild(HK.menuItem('Nach oben', function () { move(c, -1); }, { icon: '↑', hint: MK + '⇧↑' }));
      m.appendChild(HK.menuItem('Nach unten', function () { move(c, 1); }, { icon: '↓', hint: MK + '⇧↓' }));
      m.appendChild(HK.menuItem('Link zum Block kopieren', function () { blockLink(c); }, { icon: '🔗' }));
      m.appendChild(el('div', 'menu-sep'));
      m.appendChild(HK.menuItem('Löschen', function () { c.ed.blocks.delete(c.i); }, { icon: '🗑', danger: true }));
    }, { search: 'Aktion suchen …' });
  }

  // --- Tastenkuerzel ---------------------------------------------------------------------
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || HK.menuOpen()) return;
    var c;
    // Navigation (nicht im Peek-Rahmen: der hat keinen eigenen Verlauf)
    if (!K.embed && mod(e) && !e.altKey && !e.shiftKey && (e.key === '[' || e.key === ']' || e.code === 'BracketLeft' || e.code === 'BracketRight')) {
      if (typing() && !inEditor()) return;
      e.preventDefault(); if (e.key === '[' || e.code === 'BracketLeft') history.back(); else history.forward(); return;
    }
    if (!K.embed && mod(e) && e.shiftKey && !e.altKey && e.code === 'KeyU') { e.preventDefault(); K.goParent(); return; }
    if (mod(e) && !e.shiftKey && !e.altKey && e.code === 'KeyL' && K.cur()) {
      var s = window.getSelection(); if (s && !s.isCollapsed && inEditor()) return;     // markierter Text: nicht stoeren
      e.preventDefault(); K.copyLink(); return;
    }
    if (!inEditor()) return;
    // Block-Kuerzel
    if (mod(e) && e.altKey && !e.shiftKey && /^(Digit|Numpad)[0-8]$/.test(e.code)) {
      var n = +e.code.slice(-1), t = TYPES.filter(function (x) { return x[4] === n; })[0];
      if (t && (c = current())) { e.preventDefault(); e.stopPropagation(); convert(c, t[1]); }
      return;
    }
    if (mod(e) && !e.altKey && !e.shiftKey && e.code === 'KeyD' && (c = current())) { e.preventDefault(); e.stopPropagation(); duplicate(c); return; }
    if (mod(e) && e.shiftKey && !e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown') && (c = current())) {
      e.preventDefault(); e.stopPropagation(); move(c, e.key === 'ArrowUp' ? -1 : 1); return;
    }
    if (mod(e) && !e.altKey && (e.key === '/' || e.code === 'Slash') && (c = current())) { e.preventDefault(); e.stopPropagation(); actionMenu(c); }
  }, true);

  // --- Zurueck / Vor als Knoepfe vor dem Pfad ------------------------------------------------
  if (!K.embed && $('pg-crumbs')) {
    var nav = el('span', 'pg-nav');
    [['‹', 'Zurück (' + MK + '[)', function () { history.back(); }], ['›', 'Vor (' + MK + '])', function () { history.forward(); }]].forEach(function (x) {
      var b = el('button', 'icon-btn pg-nav-b', x[0]); b.type = 'button'; b.title = x[1]; b.setAttribute('aria-label', x[1]); b.addEventListener('click', x[2]); nav.appendChild(b);
    });
    $('pg-crumbs').parentNode.insertBefore(nav, $('pg-crumbs'));
  }

  // --- Schwebendes Inhaltsverzeichnis (rechts, ab 3 Ueberschriften) ------------------------------
  var toc = null, tocOn = HK.store('hk-toc') !== '0', tocT = null;
  function drawToc() {
    if (K.embed) return;
    if (!toc) { toc = el('nav', 'hk-toc'); toc.setAttribute('aria-label', 'Inhaltsverzeichnis'); document.body.appendChild(toc); }
    var hs = [].slice.call(document.querySelectorAll('#pg-editor .ce-header')).filter(function (h) { return h.textContent.trim(); });
    toc.textContent = '';
    toc.hidden = !tocOn || hs.length < 3 || $('page').hidden;
    if (toc.hidden) return;
    hs.forEach(function (h) {
      var lv = +h.tagName.slice(1) || 2, a = el('button', 'hk-toc-i l' + lv); a.type = 'button';
      a.appendChild(el('span', 'hk-toc-d')); a.appendChild(el('span', 'hk-toc-t', h.textContent.trim()));
      a.addEventListener('click', function () { h.scrollIntoView({ block: 'start', behavior: 'smooth' }); });
      toc.appendChild(a);
    });
  }
  function schedToc() { clearTimeout(tocT); tocT = setTimeout(drawToc, 300); }
  if (!K.embed) {
    new MutationObserver(schedToc).observe($('pg-editor'), { childList: true, subtree: true, characterData: true });
    new MutationObserver(schedToc).observe($('page'), { attributes: true, attributeFilter: ['hidden'] });
  }

  // --- Emoji per ":" (z. B. ":herz" -> Vorschlaege) -------------------------------------------
  var EMO = ('😀 lachen grinsen smile|😂 lachen traenen lol|🙂 laecheln smile|😉 zwinkern wink|😍 verliebt herzaugen love|🥰 liebe herzen|😘 kuss kiss|' +
    '😎 cool sonnenbrille|🤔 nachdenken denken hmm|😴 schlafen muede sleep|😢 traurig weinen sad|😭 heulen cry|😡 wuetend angry|🤯 kopf explodiert wow|' +
    '😱 schock angst|🥳 party feiern|🤗 umarmen hug|🙈 affe peinlich|🙏 bitte danke beten|👍 daumen gut ok like|👎 daumen schlecht|👏 klatschen applaus|' +
    '💪 stark kraft muskel|👋 winken hallo|✌️ frieden peace|🤝 handschlag deal|👀 augen schauen|🧠 gehirn denken brain|❤️ herz liebe heart|💜 herz lila|' +
    '💙 herz blau|💚 herz gruen|💛 herz gelb|🧡 herz orange|🖤 herz schwarz|💔 gebrochen herz|✨ funkeln glitzer sparkles|⭐ stern star|🌟 stern leuchten|' +
    '🔥 feuer heiss fire|💯 hundert perfekt|✅ erledigt haken check done|☑️ haken checkbox|❌ kreuz nein falsch|⚠️ warnung achtung warning|❗ ausrufezeichen wichtig|' +
    '❓ frage question|💡 idee gluehbirne idea|📌 pin anheften|📍 ort standort|📎 bueroklammer anhang|🔗 link verknuepfung|📝 notiz schreiben memo|' +
    '📄 seite dokument page|📚 buecher lernen books|📖 buch lesen|📅 kalender datum calendar|📆 kalender termin|🗓️ kalender planung|⏰ wecker zeit alarm|' +
    '⏳ sanduhr warten|⌛ zeit abgelaufen|🕐 uhr zeit|📊 diagramm statistik chart|📈 steigend wachstum|📉 fallend|💰 geld money|💶 euro geld|💳 karte zahlen|' +
    '🏠 haus zuhause home|🏢 buero firma office|🏥 krankenhaus|🏫 schule|🛒 einkaufen einkauf shopping|🎁 geschenk gift|🎉 party feier tada|🎂 geburtstag kuchen|' +
    '🎄 weihnachten|🎃 halloween kuerbis|🎮 spiel gaming game|🕹️ joystick retro|🎵 musik note|🎧 kopfhoerer musik|🎬 film movie|📺 fernseher tv|📷 kamera foto|' +
    '📱 handy telefon phone|💻 laptop computer|🖥️ computer bildschirm desktop|⌨️ tastatur keyboard|🖱️ maus|🖨️ drucker|💾 diskette speichern|🗄️ archiv schrank|' +
    '🗂️ ordner register|📁 ordner folder|📂 ordner offen|🗃️ kartei box|📦 paket karton box|📬 post brief mail|✉️ brief mail|📧 email|📞 telefon anruf|' +
    '🔒 schloss sicher lock|🔓 offen unlock|🔑 schluessel key|🔐 schloss schluessel|🛡️ schild sicherheit security|⚙️ zahnrad einstellungen settings|🔧 schraubenschluessel werkzeug|' +
    '🔨 hammer|🛠️ werkzeug tools|🧰 werkzeugkasten|🔌 stecker strom|🔋 batterie akku|💽 festplatte disk|🌐 internet netz web|📡 antenne satellit|🛰️ satellit|' +
    '🐧 linux pinguin tux|🐛 bug fehler kaefer|🧪 test labor|🧬 dna|🔬 mikroskop|🔭 teleskop|🚀 rakete start launch|✈️ flugzeug reise|🚗 auto fahren car|' +
    '🚲 fahrrad|🚆 zug bahn|🗺️ karte reise map|🧭 kompass richtung|🏖️ strand urlaub|⛰️ berg|🌍 welt erde|☀️ sonne wetter|🌙 mond nacht|⛅ wolken|🌧️ regen|' +
    '❄️ schnee kalt|🌈 regenbogen|🌸 bluete kirschbluete sakura|🌹 rose blume|🌻 sonnenblume|🍀 kleeblatt glueck|🌱 pflanze wachsen|🌲 baum wald|🍁 herbst blatt|' +
    '🐱 katze cat|🐶 hund dog|🦊 fuchs|🐼 panda|🦄 einhorn|🐝 biene|🦋 schmetterling|🐉 drache|👻 geist ghost|💀 totenkopf|🤖 roboter robot ki|👾 alien game|' +
    '☕ kaffee coffee|🍵 tee|🍺 bier|🍷 wein|🍕 pizza|🍔 burger|🍣 sushi|🍜 ramen nudeln|🍰 kuchen|🍎 apfel|🥗 salat gesund|🍳 kochen ei|' +
    '🏃 laufen rennen joggen|🏋️ training sport gym|🧘 yoga meditation|⚽ fussball|🎯 ziel target|🏆 pokal sieg|🥇 gold erster|🎓 abschluss studium|' +
    '💼 arbeit job aktentasche|👔 business|👤 person benutzer|👥 personen team|👨‍💻 entwickler programmierer|👩‍💻 entwicklerin|👶 baby kind|👪 familie|' +
    '💬 sprechblase chat kommentar|🗨️ kommentar|📢 ankuendigung megafon|🔔 glocke benachrichtigung|🔕 stumm|🏷️ etikett tag label|🔖 lesezeichen bookmark|' +
    '♻️ recycling muell|🗑️ papierkorb muell|🚫 verboten nein|⛔ stopp|🟢 gruen punkt|🟡 gelb punkt|🔴 rot punkt|🔵 blau punkt|⚫ schwarz punkt|⚪ weiss punkt|' +
    '➡️ pfeil rechts|⬅️ pfeil links|⬆️ pfeil hoch|⬇️ pfeil runter|🔄 aktualisieren wiederholen|🔁 wiederholen schleife|➕ plus|➖ minus|✏️ stift bearbeiten|' +
    '🖊️ kugelschreiber|🖌️ pinsel malen|🎨 kunst farbe palette|🧩 puzzle|🎲 wuerfel|♟️ schach strategie|🏁 ziel flagge|🚩 flagge markierung|💤 schlafen zzz|' +
    '🩺 arzt gesundheit|💊 tablette medizin|🧹 putzen besen|🧺 waesche korb|🛏️ bett|🚿 dusche|🧾 rechnung beleg|🏦 bank|📋 klemmbrett liste|📏 lineal|✂️ schere')
    .split('|').map(function (s) { var i = s.indexOf(' '); return { e: s.slice(0, i), k: s.slice(i + 1) }; });
  var em = { box: null, items: [], idx: 0, range: null, len: 0 };
  function emClose() { if (em.box) em.box.hidden = true; em.items = []; }
  function emPick(i) {
    var it = em.items[i]; if (!it || !em.range) { emClose(); return; }
    var sel = window.getSelection(), r = em.range.cloneRange();
    sel.removeAllRanges(); sel.addRange(r);
    document.execCommand('insertText', false, it.e);       // wie Tippen: Editor merkt die Aenderung
    emClose();
  }
  function emDraw() {
    em.box.textContent = '';
    em.items.forEach(function (it, i) {
      var b = el('button', 'hk-emo-i' + (i === em.idx ? ' on' : '')); b.type = 'button';
      b.appendChild(el('span', 'hk-emo-e', it.e)); b.appendChild(el('span', 'hk-emo-k', it.k.split(' ')[0]));
      b.addEventListener('mousedown', function (ev) { ev.preventDefault(); emPick(i); });
      em.box.appendChild(b);
    });
  }
  $('pg-editor').addEventListener('input', function () {
    var sel = window.getSelection();
    if (!sel || !sel.isCollapsed || !sel.rangeCount || !inEditor()) { emClose(); return; }
    var node = sel.anchorNode, off = sel.anchorOffset;
    if (!node || node.nodeType !== 3) { emClose(); return; }
    var before = node.textContent.slice(0, off), m = before.match(/(^|[\s(])(:([a-zäöüß0-9_+-]{2,20}))$/i);
    if (!m) { emClose(); return; }
    var q = m[3].toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss');
    function rank(x) { var w = x.k.split(' '); return w.indexOf(q) === 0 ? 0 : w.indexOf(q) > 0 ? 1 : (' ' + x.k).indexOf(' ' + q) >= 0 ? 2 : x.k.indexOf(q) >= 0 ? 3 : 9; }
    var hits = EMO.map(function (x, i) { return { x: x, r: rank(x), i: i }; }).filter(function (h) { return h.r < 9; })
      .sort(function (a, b) { return a.r - b.r || a.i - b.i; }).map(function (h) { return h.x; });
    if (!hits.length) { emClose(); return; }
    em.items = hits.slice(0, 8); em.idx = 0;
    em.range = document.createRange(); em.range.setStart(node, off - m[2].length); em.range.setEnd(node, off);
    if (!em.box) { em.box = el('div', 'hk-emo'); em.box.setAttribute('role', 'listbox'); document.body.appendChild(em.box); }
    emDraw(); em.box.hidden = false;
    var rc = em.range.getBoundingClientRect();
    em.box.style.left = Math.min(rc.left, window.innerWidth - 240) + 'px';
    em.box.style.top = (rc.bottom + 6 + 260 > window.innerHeight ? Math.max(8, rc.top - 6 - em.box.offsetHeight) : rc.bottom + 6) + 'px';
  });
  document.addEventListener('keydown', function (e) {
    if (!em.box || em.box.hidden || !em.items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); em.idx = (em.idx + (e.key === 'ArrowDown' ? 1 : em.items.length - 1)) % em.items.length; emDraw(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); emPick(em.idx); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); emClose(); }
  }, true);
  document.addEventListener('mousedown', function (e) { if (em.box && !em.box.contains(e.target)) emClose(); });

  // --- @-Datum, @-Erinnerung, @-Seite -----------------------------------------------------
  var WD = ['sonntag', 'montag', 'dienstag', 'mittwoch', 'donnerstag', 'freitag', 'samstag'];
  var WDE = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  var MON = ['januar', 'februar', 'maerz', 'april', 'mai', 'juni', 'juli', 'august', 'september', 'oktober', 'november', 'dezember'];
  function norm(s) { return String(s || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss').replace(/\s+/g, ' ').trim(); }
  function iso(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function today0() { var n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); }
  function plus(d, n) { return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n); }
  // "heute", "morgen", "uebermorgen", "freitag", "naechsten montag", "in 3 tagen", "12.10.", "12.10.2026", "12. oktober",
  // "2026-10-12" - optional mit Uhrzeit "15:30", "um 15 uhr". Ergebnis {date: 'YYYY-MM-DD', time: 'HH:MM'|null} oder null
  function parseDate(q) {
    q = norm(q); if (!q) return null;
    var time = null, m = q.match(/(?:^|\s)(?:um\s+)?(\d{1,2})[:.](\d{2})(?:\s*uhr)?$/) || q.match(/(?:^|\s)(?:um\s+)?(\d{1,2})\s*uhr$/);
    if (m && (q.indexOf(':') >= 0 || /uhr$/.test(q))) {
      var hh = +m[1], mm = m[2] ? +m[2] : 0; if (hh > 23 || mm > 59) return null;
      time = String(hh).padStart(2, '0') + ':' + String(mm).padStart(2, '0'); q = q.slice(0, m.index).trim();
    }
    var t0 = today0(), d = null;
    if (!q) d = t0;
    else if (q === 'heute' || q === 'today') d = t0;
    else if (q === 'morgen' || q === 'tomorrow') d = plus(t0, 1);
    else if (q === 'uebermorgen') d = plus(t0, 2);
    else if (q === 'gestern' || q === 'yesterday') d = plus(t0, -1);
    else if ((m = q.match(/^in (\d{1,3}) (tag|tage|tagen|woche|wochen|monat|monate|monaten|day|days|week|weeks)$/))) {
      var n = +m[1]; d = /^w/.test(m[2]) ? plus(t0, 7 * n) : /^mo/.test(m[2]) ? new Date(t0.getFullYear(), t0.getMonth() + n, t0.getDate()) : plus(t0, n);
    } else if (q === 'naechste woche' || q === 'next week') d = plus(t0, 7);
    else if ((m = q.match(/^(?:(naechsten?|kommenden?|next)\s+)?([a-z]+)$/))) {
      var wi = WD.indexOf(m[2]); if (wi < 0) wi = WDE.indexOf(m[2]);
      if (wi < 0) return null;
      var diff = (wi - t0.getDay() + 7) % 7; if (diff === 0) diff = 7; d = plus(t0, diff);
    } else if ((m = q.match(/^(\d{4})-(\d{2})-(\d{2})$/))) d = new Date(+m[1], +m[2] - 1, +m[3]);
    else if ((m = q.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?$/))) {
      var y = m[3] ? (+m[3] < 100 ? 2000 + +m[3] : +m[3]) : t0.getFullYear(); d = new Date(y, +m[2] - 1, +m[1]);
      if (d.getMonth() !== +m[2] - 1) return null;
    } else if ((m = q.match(/^(\d{1,2})\.? ([a-z]+)\.?(?: (\d{4}))?$/))) {
      var mi = -1; MON.forEach(function (x, i) { if (mi < 0 && m[2].length >= 3 && x.indexOf(m[2]) === 0) mi = i; });
      if (mi < 0) return null;
      d = new Date(m[3] ? +m[3] : t0.getFullYear(), mi, +m[1]); if (d.getMonth() !== mi) return null;
    } else return null;
    return { date: iso(d), time: time };
  }
  var fDay = new Intl.DateTimeFormat('de-DE', { weekday: 'short', day: 'numeric', month: 'short' });
  var fDayY = new Intl.DateTimeFormat('de-DE', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  // Im Chip immer das feste Datum (der Text wird gespeichert - "Morgen" waere uebermorgen falsch)
  function dateText(d) {
    var p = d.date.split('-'), dt = new Date(+p[0], +p[1] - 1, +p[2]);
    return (dt.getFullYear() === today0().getFullYear() ? fDay : fDayY).format(dt) + (d.time ? ', ' + d.time : '');
  }
  function relText(d) {          // nur fuer die Auswahlliste
    var p = d.date.split('-'), diff = Math.round((new Date(+p[0], +p[1] - 1, +p[2]) - today0()) / 864e5);
    var r = diff === 0 ? 'Heute' : diff === 1 ? 'Morgen' : diff === 2 ? 'Übermorgen' : diff === -1 ? 'Gestern' : '';
    return (r ? r + ' · ' : '') + dateText(d);
  }
  function dateChip(d, remind) {
    var tEl = document.createElement('time');
    tEl.setAttribute('datetime', d.date + (d.time ? 'T' + d.time : '')); tEl.className = remind ? 'hk-remind' : 'hk-date';
    tEl.textContent = (remind ? '⏰ ' : '') + dateText(d);
    return tEl;
  }
  // Vergangene Daten rot - ueber ein Stylesheet, damit der gespeicherte Inhalt unveraendert bleibt
  var pastCss = null;           // Stylesheet-Objekt (ein <style>-Element wuerde die CSP blockieren)
  try { pastCss = new CSSStyleSheet(); document.adoptedStyleSheets = document.adoptedStyleSheets.concat([pastCss]); } catch (e) { pastCss = null; }
  function markPast() {
    var t0 = iso(today0()), seen = {};
    [].forEach.call(document.querySelectorAll('#pg-editor time[datetime]'), function (x) { var v = x.getAttribute('datetime'); if (v.slice(0, 10) < t0) seen[v] = 1; });
    if (!pastCss) return;
    var sel = Object.keys(seen).map(function (v) { return '#pg-editor time[datetime="' + v + '"]'; }).join(',');
    pastCss.replaceSync(sel ? sel + '{color:var(--down);background:color-mix(in srgb,var(--down) 12%,transparent)}' : '');
  }
  new MutationObserver(function () { clearTimeout(markPast.t); markPast.t = setTimeout(markPast, 400); }).observe($('pg-editor'), { childList: true, subtree: true });

  var at = { box: null, items: [], idx: 0, range: null };
  function atClose() { if (at.box) at.box.hidden = true; at.items = []; }
  function blockText(node) {
    var b = node.parentElement && node.parentElement.closest('.ce-block'); if (!b) return '';
    var c = b.cloneNode(true); [].forEach.call(c.querySelectorAll('time'), function (x) { x.remove(); });
    return c.textContent.replace(/\s+/g, ' ').trim();
  }
  function insertNode(n, range) {
    var sel = window.getSelection(); range.deleteContents(); range.insertNode(n);
    var sp = document.createTextNode(' '); n.after(sp);
    var r = document.createRange(); r.setStartAfter(sp); r.collapse(true); sel.removeAllRanges(); sel.addRange(r);
    n.parentElement.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function atPick(i) {
    var it = at.items[i]; if (!it) { atClose(); return; }
    var range = at.range, node = range.startContainer; atClose();
    if (it.page) {
      var a = document.createElement('a'); a.href = '/notes#' + it.page.id; a.textContent = K.label(it.page);
      insertNode(a, range); K.decorate(); return;
    }
    if (it.remind) {
      var cur = K.cur(), txt = blockText(node).replace(/@\S*$/, '').trim();
      var text = (txt || (cur && cur.title) || 'Erinnerung'); if (txt && cur && cur.title) text += ' (' + cur.title + ')';
      HK.api('POST', '/api/tasks', { text: text.slice(0, 300), due: it.d.date }).then(function () {
        HK.toast('Erinnerung als Aufgabe für ' + dateText(it.d) + ' angelegt');
      }).catch(function (err) { HK.toast('Aufgabe nicht angelegt: ' + err.message); });
    }
    insertNode(dateChip(it.d, !!it.remind), range);
  }
  function atDraw() {
    at.box.textContent = '';
    var lastGroup = null;
    at.items.forEach(function (it, i) {
      if (it.group !== lastGroup) { at.box.appendChild(el('div', 'hk-at-g', it.group)); lastGroup = it.group; }
      var b = el('button', 'hk-emo-i' + (i === at.idx ? ' on' : '')); b.type = 'button';
      b.appendChild(el('span', 'hk-emo-e', it.ic)); b.appendChild(el('span', 'hk-at-t', it.t));
      if (it.sub) b.appendChild(el('span', 'hk-emo-k', it.sub));
      b.addEventListener('mousedown', function (ev) { ev.preventDefault(); atPick(i); });
      at.box.appendChild(b);
    });
  }
  var DATE_WORDS = ['heute', 'morgen', 'uebermorgen', 'montag', 'dienstag', 'mittwoch', 'donnerstag', 'freitag', 'samstag', 'sonntag', 'naechste woche'];
  function atOptions(q) {
    var out = [], seen = {}, nq = norm(q);
    var cands = [];
    var full = parseDate(q); if (full && q.trim()) cands.push(full);
    DATE_WORDS.forEach(function (w) { if (!nq || (w.indexOf(nq) === 0 && w !== nq)) { var d = parseDate(w); if (d) cands.push(d); } });
    if (!nq) cands = cands.slice(0, 2);
    cands.forEach(function (d) {
      var k = d.date + (d.time || ''); if (seen[k]) return; seen[k] = 1;
      out.push({ group: 'Datum', ic: '📅', t: relText(d), d: d });
    });
    var dates = out.slice(0, 3); out = dates.slice();
    if (dates.length) out.push({ group: 'Erinnerung (legt Aufgabe an)', ic: '⏰', t: relText(dates[0].d), d: dates[0].d, remind: true });
    var pages = K.pages(), hits;
    if (!nq) hits = K.recents().map(function (id) { return pages.find(function (p) { return p.id === id; }); }).filter(Boolean).slice(0, 4);
    else hits = pages.filter(function (p) { return norm(K.label(p)).indexOf(nq) >= 0; }).slice(0, 5);
    hits.forEach(function (p) { if (!K.cur() || p.id !== K.cur().id) out.push({ group: 'Seite verlinken', ic: K.ti(p.icon) || '📄', t: K.label(p), sub: K.ancestors(p.id).map(K.label).join(' / '), page: p }); });
    return out;
  }
  $('pg-editor').addEventListener('input', function () {
    var sel = window.getSelection();
    if (!sel || !sel.isCollapsed || !sel.rangeCount || !inEditor()) { atClose(); return; }
    var node = sel.anchorNode, off = sel.anchorOffset;
    if (!node || node.nodeType !== 3) { atClose(); return; }
    var before = node.textContent.slice(0, off).replace(/ /g, ' '), m = before.match(/(^|\s)@([^@\n]{0,30})$/);
    if (!m || /^\s/.test(m[2]) || /\s{2,}/.test(m[2])) { atClose(); return; }
    var opts = atOptions(m[2]);
    if (!opts.length) { atClose(); return; }
    at.items = opts; at.idx = 0;
    at.range = document.createRange(); at.range.setStart(node, off - m[2].length - 1); at.range.setEnd(node, off);
    if (!at.box) { at.box = el('div', 'hk-emo hk-at'); at.box.setAttribute('role', 'listbox'); document.body.appendChild(at.box); }
    atDraw(); at.box.hidden = false;
    var rc = at.range.getBoundingClientRect();
    at.box.style.left = Math.min(rc.left, window.innerWidth - 300) + 'px';
    at.box.style.top = (rc.bottom + 6 + at.box.offsetHeight > window.innerHeight ? Math.max(8, rc.top - 6 - at.box.offsetHeight) : rc.bottom + 6) + 'px';
  });
  document.addEventListener('keydown', function (e) {
    if (!at.box || at.box.hidden || !at.items.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); at.idx = (at.idx + (e.key === 'ArrowDown' ? 1 : at.items.length - 1)) % at.items.length; atDraw(); }
    else if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); atPick(at.idx); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); atClose(); }
  }, true);
  document.addEventListener('mousedown', function (e) { if (at.box && !at.box.contains(e.target)) atClose(); });

  // Klick auf einen Datums-Chip: Datum aendern oder entfernen
  $('pg-editor').addEventListener('click', function (e) {
    var tEl = e.target.closest && e.target.closest('time.hk-date, time.hk-remind'); if (!tEl || !$('pg-editor').contains(tEl)) return;
    e.preventDefault();
    HK.openMenu(tEl, function (m) {
      var v = tEl.getAttribute('datetime') || '', wrap = el('div', 'hk-date-edit');
      var inp = el('input', 'hk-in'); inp.type = 'date'; inp.value = v.slice(0, 10);
      var tm = el('input', 'hk-in'); tm.type = 'time'; tm.value = v.slice(11, 16);
      wrap.appendChild(inp); wrap.appendChild(tm); m.appendChild(wrap);
      function apply() {
        if (!inp.value) return;
        var d = { date: inp.value, time: tm.value || null };
        tEl.setAttribute('datetime', d.date + (d.time ? 'T' + d.time : ''));
        tEl.textContent = (tEl.classList.contains('hk-remind') ? '⏰ ' : '') + dateText(d);
        tEl.parentElement.dispatchEvent(new Event('input', { bubbles: true }));
      }
      inp.addEventListener('change', apply); tm.addEventListener('change', apply);
      m.appendChild(HK.menuItem('Datum entfernen', function () { var p = tEl.parentElement; tEl.remove(); p.dispatchEvent(new Event('input', { bubbles: true })); }, { icon: '✕', danger: true }));
    });
  });

  window.HKKeys = {
    toggleToc: function () { tocOn = !tocOn; HK.store('hk-toc', tocOn ? '1' : '0'); drawToc(); HK.toast('Inhaltsverzeichnis ' + (tocOn ? 'an' : 'aus')); },
    convert: function (key) { var c = current(); if (c) convert(c, key); }, actionMenu: function () { var c = current(); if (c) actionMenu(c); },
    emojiCount: EMO.length, parseDate: parseDate, dateChip: dateChip, dateText: dateText
  };
})();
