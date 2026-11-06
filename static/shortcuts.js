// Tastenkuerzel-Uebersicht (UX-41) und globale Kuerzel, die auf allen Hauptseiten gelten (Heute, Seiten, Chats):
//   Strg/⌘+/   Uebersicht oeffnen - im Editor bleibt Strg/⌘+/ das Blockmenue (static/keys.js, laeuft vorher und
//              verbraucht die Taste); "?" ausserhalb von Eingabefeldern oeffnet die Uebersicht ueberall
//   Strg/⌘+Alt+N  neue leere Seite (auf /notes direkt, sonst ueber /notes?new=blank)
// Die Liste traegt nur Kuerzel, die der Code wirklich kennt (keys.js, pages.js, side.js, app.js, notes.js, ai.js, chat.js,
// pm-editor.js, editor25.js). Mac zeigt ⌘ ⌥ ⇧ ⌃, sonst Strg/Alt/Shift.
(function () {
  'use strict';
  var HK = window.HK; if (!HK || document.body.classList.contains('embed')) return;
  var T = window.T || function (s) { return s; }, el = HK.el;
  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  var MAC = { Mod: '⌘', Alt: '⌥', Shift: '⇧', Ctrl: '⌃', Enter: '↩', Esc: 'Esc' };
  var PC = { Mod: T('Strg'), Alt: 'Alt', Shift: 'Shift', Ctrl: T('Strg'), Enter: 'Enter', Esc: 'Esc' };
  function keyName(k) { var m = isMac ? MAC : PC; return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : k; }
  function combo(keys) {
    var w = el('span', 'kz-keys');
    keys.forEach(function (k, i) {
      if (i && !isMac) w.appendChild(el('span', 'kz-plus', '+'));
      w.appendChild(el('kbd', null, keyName(k)));
    });
    w.setAttribute('aria-label', keys.map(function (k) { return keyName(k); }).join(isMac ? ' ' : ' + '));
    return w;
  }
  function groups() {
    return [
      [T('Allgemein'), [
        [['Mod', 'K'], T('Schnellsuche: Seiten, Befehle („>“), Web')],
        [['Mod', '/'], T('Tastenkürzel anzeigen (im Editor: Blockmenü)')],
        [['?'], T('Tastenkürzel anzeigen (außerhalb von Eingabefeldern)')],
        [['Mod', 'Alt', 'N'], T('Neue leere Seite')],
        [['Mod', '\\'], T('Seitenleiste ein/aus')],
        [['Esc'], T('Dialog, Menü oder Suche schließen')]
      ]],
      [T('Navigation'), [
        [['Mod', '['], T('Zurück')],
        [['Mod', ']'], T('Vor')],
        [['Mod', 'Shift', 'U'], T('Übergeordnete Seite')],
        [['/'], T('Seiten durchsuchen (Seitenleiste)')],
        [['1–9'], T('Lesezeichen öffnen (Heute)')],
        [['N'], T('Neue Seite (Heute)')],
        [['V'], T('Neue Seite aus Vorlage (Heute)')],
        [['E'], T('Neuer Termin (Heute)')],
        [['T'], T('Neue Aufgabe (Heute)')],
        [['Ctrl', 'Shift', 'J'], T('Vorschau: nächster Eintrag')],
        [['Ctrl', 'Shift', 'K'], T('Vorschau: vorheriger Eintrag')]
      ]],
      [T('Seiten und Editor'), [
        [['Mod', 'Alt', 'L'], T('Link zur Seite kopieren')],
        [['/'], T('Block einfügen (im leeren Absatz)')],
        [['Mod', '/'], T('Blockmenü: umwandeln, duplizieren, löschen …')],
        [['Mod', 'Alt', '0–8'], T('Block umwandeln: Text, Überschrift 1–3, Checkliste, Aufzählung, Nummerierte Liste, Toggle, Code')],
        [['Mod', 'D'], T('Block duplizieren')],
        [['Mod', 'Shift', '↑ ↓'], T('Block verschieben')],
        [['Mod', 'B'], T('Fett')],
        [['Mod', 'I'], T('Kursiv')],
        [['Mod', 'U'], T('Unterstrichen')],
        [['Mod', 'Shift', 'S'], T('Durchgestrichen')],
        [['Mod', 'E'], T('Code im Text')],
        [['Mod', 'Shift', 'H'], T('Hervorheben (zuletzt benutzte Farbe)')],
        [['Mod', 'Enter'], T('Codeblock verlassen, Kommentar senden')],
        [[':'], T('Emoji einfügen (z. B. „:herz“)')],
        [['@'], T('Datum, Erinnerung oder Seite einfügen')],
        [['Mod', 'Z'], T('Rückgängig')],
        [['Mod', 'Shift', 'Z'], T('Wiederholen')]
      ]],
      [T('Chats'), [
        [['Enter'], T('Nachricht senden')],
        [['Shift', 'Enter'], T('Neue Zeile')],
        [['↑'], T('Letzte eigene Nachricht bearbeiten (leeres Eingabefeld)')]
      ]],
      ['Marin', [
        [['Mod', 'J'], T('Marin öffnen oder schließen')],
        [['M'], T('Marin fragen (Heute)')]
      ]]
    ];
  }

  var dlg = null, pf = null;
  function close() {
    if (!dlg) return;
    dlg.remove(); dlg = null; document.removeEventListener('keydown', onKey, true);
    if (pf) { pf.release(); pf = null; }
  }
  function onKey(e) {
    if (e.key === 'Escape' && !HK.menuOpen()) { e.preventDefault(); e.stopPropagation(); close(); }
  }
  function open(o) {
    o = o || {};
    if (dlg) { var c = dlg.querySelector('.icon-btn'); if (c) c.focus(); return; }
    if (HK.menuOpen && HK.menuOpen()) HK.closeMenu();
    var a = document.activeElement, back = o.back || (o.key ? (a && a !== document.body ? a : document.body) : null);
    dlg = el('div', 'hk-modal kz-modal');
    var card = el('div', 'hk-dialog kz-dialog'); card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true');
    var head = el('div', 'hk-dlg-head'), h = el('h2', null, T('Tastenkürzel')); h.id = 'kz-title';
    card.setAttribute('aria-labelledby', h.id);
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); x.addEventListener('click', close);
    head.appendChild(h); head.appendChild(x); card.appendChild(head);
    var body = el('div', 'hk-dlg-body kz-body');
    body.appendChild(el('p', 'hk-hint', isMac ? T('⌘ = Befehlstaste, ⌥ = Wahltaste, ⇧ = Umschalttaste, ⌃ = Control.')
                                              : T('Auf dem Mac gilt ⌘ statt Strg.')));
    groups().forEach(function (g) {
      var sec = el('section', 'kz-group'), gh = el('h3', null, g[0]); sec.appendChild(gh);
      var dl = el('dl', 'kz-list');
      g[1].forEach(function (r) {
        var row = el('div', 'kz-row'), dt = el('dt'), dd = el('dd', null, r[1]);
        dt.appendChild(combo(r[0])); row.appendChild(dd); row.appendChild(dt); dl.appendChild(row);
      });
      sec.appendChild(dl); body.appendChild(sec);
    });
    body.tabIndex = 0;                        // Liste per Tastatur scrollbar
    body.setAttribute('aria-label', T('Tastenkürzel'));
    card.appendChild(body); dlg.appendChild(card);
    dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) close(); });
    document.body.appendChild(dlg);
    document.addEventListener('keydown', onKey, true);
    pf = HK.panelFocus(card, { trap: true, initial: x, back: back || undefined });
    x.focus();
  }
  window.HKShortcuts = { open: open, close: close, isOpen: function () { return !!dlg; } };

  function mod(e) { return isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey; }
  function typing(t) { return t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable); }
  function otherModal() {
    return [].some.call(document.querySelectorAll('[aria-modal="true"], dialog[open]'), function (d) {
      return !d.closest('.kz-modal') && !d.closest('[hidden]') && d.getClientRects().length;
    });
  }
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.isComposing) return;
    var t = e.target;
    // Strg/⌘+/ (deutsche Tastatur: Strg+Umschalt+7) - im Editor hat static/keys.js das Blockmenue schon geoeffnet
    if (mod(e) && !e.altKey && (e.key === '/' || e.code === 'Slash' && !e.shiftKey)) {
      if (dlg) { e.preventDefault(); close(); return; }
      if (otherModal()) return;
      e.preventDefault(); open({ key: true }); return;
    }
    if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(t) && !(HK.menuOpen && HK.menuOpen()) && !dlg && !otherModal()) {
      e.preventDefault(); open({ key: true }); return;
    }
    // Strg/⌘+Alt+N: neue leere Seite. AltGr (= Strg+Alt unter Windows) mit einem Zeichen auf N (z. B. "ń") nicht abfangen.
    if (mod(e) && e.altKey && !e.shiftKey && e.code === 'KeyN') {
      if (!isMac && e.key && e.key.length === 1 && e.key.toLowerCase() !== 'n') return;
      if (otherModal() || dlg) return;
      e.preventDefault();
      if (HK.newBlankPage) HK.newBlankPage(); else location.href = '/notes?new=blank';
    }
  });
})();
