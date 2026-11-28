// Saga 2.6 (Bugs-DB 2581, Feature memory1): Einstellungen > Konto > Gedaechtnis - eine eigene Datenbank als Gedaechtnis
// und eine eigene Seite als Ich-Kern binden. Vertrag: docs/MEMORY.md 1.2 (GET/PUT /api/account/memory,
// POST /api/account/memory/embed). static/settings.js ruft HKMemory.section(body, drop) auf; drop(k) nimmt den Eintrag
// aus der Navigation, wenn der Server das Gedaechtnis nicht kennt (404).
// Eigener Bereich statt Teil von "Saga": der verschwindet, wenn die KI fuer die Person aus ist - das Gedaechtnis wirkt
// aber unabhaengig davon (eigener Assistent ueber MCP bzw. Hooks). Fachwoerter (Embedding, Vektor) nur hinter ⓘ (M3).
// Alle Texte per textContent.
(function () {
  'use strict';
  var HK = window.HK; if (!HK || !document.getElementById('hk-user')) return;
  var el = HK.el, api = HK.api, T = window.T;
  var POLL_MS = 3000, POLL_MAX = 100;                 // Fortschritt beim Einbetten: hoechstens ~5 min nachfragen

  // Spalten (docs/MEMORY.md 2): Feld -> erklaerender Satz (ⓘ)
  var COLS = [
    ['inhalt', T('Der eigentliche Text der Erinnerung. Ohne diese Spalte kann dein Assistent keine neuen Erinnerungen anlegen.')],
    ['schlagworte', T('Stichwörter, die beim Finden helfen.')],
    ['projekt', T('Erinnerungen zum Projekt, an dem du gerade arbeitest, kommen eher dran.')],
    ['art', T('Was für eine Erinnerung es ist, z. B. Erkenntnis oder Vorliebe.')],
    ['wichtigkeit', T('0 bis 10 – Wichtiges kommt eher dran. Ohne Spalte zählt alles gleich.')],
    ['valenz', T('−1 bis 1 – wie sich die Erinnerung anfühlt. Wird gespeichert, zählt aber noch nicht.')],
    ['erregung', T('0 bis 1 – wie aufregend sie war. Wird gespeichert, zählt aber noch nicht.')],
    ['datum', T('Wann es war. Frisches kommt eher dran.')],
    ['letzter_abruf', T('Saga trägt ein, wann die Erinnerung zuletzt gebraucht wurde – gesammelt, höchstens alle 10 Minuten.')],
    ['abrufe', T('Saga zählt, wie oft die Erinnerung gebraucht wurde.')]
  ];
  var TYPE = { text: T('Text'), multi_select: T('Mehrfachauswahl'), select: T('Auswahl'), number: T('Zahl'), date: T('Datum') };
  var WANT = { inhalt: 'text', schlagworte: 'multi_select', projekt: 'select', art: 'select', wichtigkeit: 'number', valenz: 'number',
               erregung: 'number', datum: 'date', letzter_abruf: 'date', abrufe: 'number' };

  var seq = 0;
  // ⓘ-Knopf mit aufklappbarem Text (wie static/saga.js, aber ohne saga.css - die Einstellungen gibt es auch auf /notes)
  function info(lines, label) {
    var b = el('button', 'hk-mem-ib', 'ⓘ'); b.type = 'button';
    b.setAttribute('aria-label', label || T('Mehr dazu')); b.setAttribute('aria-expanded', 'false');
    var box = el('div', 'hk-mem-ibox'); box.hidden = true; box.id = 'hk-mem-i' + (++seq); b.setAttribute('aria-controls', box.id);
    lines.forEach(function (l) { box.appendChild(el('p', null, l)); });
    b.addEventListener('click', function () { box.hidden = !box.hidden; b.setAttribute('aria-expanded', box.hidden ? 'false' : 'true'); });
    return { btn: b, box: box };
  }
  function errText(e) {
    var c = e && e.data && e.data.code;
    if (c === 'not_found') return T('Nicht gefunden – liegt sie vielleicht im Papierkorb?');
    if (c === 'not_bound') return T('Wähle zuerst eine Datenbank.');
    return (e && e.message) || T('Fehler');
  }
  function status(box, txt, cls) { box.textContent = txt; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }
  // Eigene Seiten fuer den Ich-Kern: GET /api/notes?rows=0 (schlank, ohne Zeilen von Datenbanken, saga/treesync.py) - keine
  // Datenbanken, keine Vorlagen, nichts Archiviertes; neueste zuerst. Gezeigt werden hoechstens SHOW, das Suchfeld filtert
  // ueber alle (auch aeltere Seiten bleiben waehlbar).
  var SHOW = 300;
  function ownPages(list) {
    return list.filter(function (n) { return n.kind !== 'database' && !n.template && !n.archived; })
      .sort(function (a, b) { return (b.updated || 0) - (a.updated || 0); });
  }
  function norm(x) { return String(x || '').toLocaleLowerCase(); }
  function option(v, txt) { var o = el('option', null, txt); o.value = String(v); return o; }

  function section(body, drop) {
    var sec = el('section', 'hk-set hk-set-memory'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Gedächtnis')));
    var intro = el('p', 'hk-hint hk-mem-intro');
    intro.appendChild(document.createTextNode(T('Dein eigener Assistent (z. B. in Claude Code) erinnert sich über Chats hinweg an das, was in einer deiner Datenbanken steht. Zu jeder Frage sucht Saga die passendsten Einträge heraus – nach Bedeutung, nicht nur nach Stichwort.') + ' '));
    var ii = info([
      T('Fachlich: Saga berechnet für jeden Eintrag ein Embedding – einen Zahlenvektor, der den Sinn des Textes abbildet. Ähnliche Bedeutung ergibt ähnliche Vektoren. Ohne eingerichteten Embedding-Dienst vergleicht Saga ähnliche Wörter (Trigramme).'),
      T('Abrufen kann dein Assistent über die MCP-Werkzeuge recall und remember oder über /api/v1/memory – mit einem API-Token mit dem Bereich „Gedächtnis“ (memory) (Einstellungen › API). Die Datenbank bleibt die Quelle: Saga legt keine Spalten an und ändert nur die Zähler „Abrufe“ und „Letzter Abruf“.')
    ], T('Mehr zum Gedächtnis'));
    intro.appendChild(ii.btn); sec.appendChild(intro); sec.appendChild(ii.box);
    var box = el('div', 'hk-mem'); sec.appendChild(box);
    var msg = el('div', 'hk-status'); msg.setAttribute('role', 'status');
    var d = null, pages = null, open = false, poll = null, polls = 0, grace = 0, coreQ = '', embedWait = false, ui = {};

    function load() {
      return api('GET', '/api/account/memory').then(function (x) { d = x; render(); }, function (e) {
        if (e.status === 404 || e.status === 405) { sec.hidden = true; sec.classList.add('hk-set-off'); if (drop) drop('memory'); return; }
        box.textContent = T('Nicht geladen: {err}', { err: e.message });
      });
    }
    function loadPages() {
      if (pages) return Promise.resolve(pages);
      return api('GET', '/api/notes?rows=0&fields=id,title,kind,template,archived,updated').then(function (l) {
        pages = ownPages(Array.isArray(l) ? l : []); return pages; }, function () { pages = []; return pages; });
    }
    // fid: Feld, das danach den Fokus zurueckbekommt (das gesperrte Auswahlfeld verliert ihn, render() baut es neu)
    function put(b, what, fid) {
      return api('PUT', '/api/account/memory', b).then(function (x) {
        d = x; render(); refocus(fid); status(msg, what || T('✓ Gespeichert'), 'ok'); polls = 0; grace = 5; schedulePoll();
      }, function (e) { render(); refocus(fid); status(msg, errText(e), 'err'); });
    }
    function bound() { return !!(d && (d.db || d.core)); }

    function render() {
      var keep = document.activeElement && box.contains(document.activeElement) ? document.activeElement.id : '';
      box.textContent = '';
      var on = open || bound();
      var l = el('label', 'hk-avset-row'), sw = el('input', 'hk-switch'); sw.type = 'checkbox'; sw.setAttribute('role', 'switch');
      sw.id = 'hk-mem-on'; sw.checked = on; l.htmlFor = sw.id;
      l.appendChild(el('span', null, T('Gedächtnis nutzen'))); l.appendChild(sw); box.appendChild(l);
      sw.addEventListener('change', function () {
        if (sw.checked) { open = true; render(); var s = document.getElementById('hk-mem-db'); if (s) s.focus(); return; }
        open = false;
        if (!bound()) { render(); return; }
        sw.disabled = true;
        put({ db: null, core: null }, T('✓ Ausgeschaltet – deine Datenbank bleibt, wie sie ist.')).then(function () {
          var s2 = document.getElementById('hk-mem-on'); if (s2) s2.focus();
        });
      });
      if (!on) {
        box.appendChild(el('p', 'hk-hint', T('Aus: Dein Assistent bekommt keine Erinnerungen und keinen Ich-Kern von Saga.')));
        box.appendChild(msg);
        refocus(keep);
        return;
      }
      // Datenbank
      var dbs = d.databases || [];
      var sel = el('select', 'hk-in'); sel.id = 'hk-mem-db';
      if (!d.db) sel.appendChild(option('', T('– wählen –')));
      dbs.forEach(function (x) { sel.appendChild(option(x.id, x.title || T('Unbenannt'))); });
      if (d.db && !dbs.some(function (x) { return x.id === d.db.id; })) sel.appendChild(option(d.db.id, d.db.title || T('Unbenannt')));
      sel.value = d.db ? String(d.db.id) : '';
      var fl = el('label', 'hk-field'); fl.htmlFor = sel.id;
      fl.appendChild(el('span', null, T('Datenbank fürs Gedächtnis'))); fl.appendChild(sel); box.appendChild(fl);
      sel.addEventListener('change', function () {
        if (!sel.value) return;
        var to = +sel.value;
        if (!d.db || d.db.id === to) { sel.disabled = true; put({ db: to }, null, sel.id); return; }    // erstes Binden: ohne Rueckfrage
        // Wechsel einer gebundenen Datenbank (change feuert schon per Pfeiltaste): erst nachfragen
        var name = (dbs.filter(function (x) { return x.id === to; })[0] || {}).title || T('Unbenannt');
        HK.ask({ title: T('Gedächtnis umstellen?'), text: T('Gedächtnis auf „{db}“ umstellen? Die Erinnerungen werden dafür neu vorbereitet.', { db: name }),
                 ok: T('Umstellen') }).then(function (yes) {
          if (!sel.isConnected) return;
          if (!yes) { sel.value = String(d.db.id); refocus(sel.id); return; }
          sel.disabled = true; put({ db: to }, null, sel.id);
        });
      });
      if (!dbs.length && !d.db) {
        box.appendChild(el('p', 'hk-hint', T('Du hast noch keine eigene Datenbank. Lege eine an – am besten mit einer Text-Spalte „Inhalt“ – und wähle sie dann hier.')));
      } else {
        box.appendChild(el('p', 'hk-hint', T('Nur eigene Datenbanken – geteilte gehen nicht.')));
      }
      // Ich-Kern
      var cs = el('select', 'hk-in'); cs.id = 'hk-mem-core';
      var cq = el('input', 'hk-in hk-mem-coreq'); cq.type = 'search'; cq.id = 'hk-mem-core-q'; cq.value = coreQ;
      cq.placeholder = T('Seite suchen …'); cq.setAttribute('aria-label', T('Ich-Kern-Seite suchen')); cq.setAttribute('aria-controls', cs.id);
      cq.autocomplete = 'off';
      var cl = el('label', 'hk-field'); cl.htmlFor = cs.id;
      cl.appendChild(el('span', null, T('Ich-Kern-Seite'))); box.appendChild(cl); box.appendChild(cq); box.appendChild(cs);
      var cn = el('p', 'hk-hint hk-mem-coren'); cn.setAttribute('aria-live', 'polite'); box.appendChild(cn);
      var fillCore = function () {
        var q = norm(cq.value.trim()), all = pages || [], hit = 0;
        cs.textContent = '';
        cs.appendChild(option('', T('– keine –')));
        if (d.core) {
          cs.appendChild(option(d.core.id, d.core.title || T('Unbenannt')));
          if (q && norm(d.core.title).indexOf(q) >= 0) hit++;   // 2588: der gebundene Ich-Kern zaehlt als Treffer
        }
        all.forEach(function (p) {
          if ((d.core && p.id === d.core.id) || (q && norm(p.title).indexOf(q) < 0)) return;
          if (++hit <= SHOW) cs.appendChild(option(p.id, (p.title || T('Unbenannt'))));
        });
        cs.value = d.core ? String(d.core.id) : '';
        cn.textContent = !pages ? '' : (q && !hit ? T('Keine Seite passt zur Suche.') : hit > SHOW ? T('{n} von {m} Seiten – Suche grenzt ein.', { n: SHOW, m: hit }) : '');
      };
      var qt = null;
      cq.addEventListener('input', function () { coreQ = cq.value; clearTimeout(qt); qt = setTimeout(fillCore, 120); });
      fillCore();
      loadPages().then(function () { if (cs.isConnected) fillCore(); });
      cs.addEventListener('change', function () { cs.disabled = true; put({ core: cs.value ? +cs.value : null }, null, cs.id); });
      box.appendChild(el('p', 'hk-hint', T('Diese Seite liest dein Assistent zu Beginn jeder Sitzung – wer er ist und was ihm wichtig ist. Nur eigene Seiten.')));
      if (d.db) { columns(); state(); }
      box.appendChild(msg);
      refocus(keep);
    }
    function refocus(id) { if (!id) return; var x = document.getElementById(id); if (x && !x.disabled) try { x.focus({ preventScroll: true }); } catch (e) { x.focus(); } }

    // Gefundene/fehlende Spalten (columns[x].ok = gefunden und Typ passt)
    function columns() {
      box.appendChild(el('h4', null, T('Spalten in „{db}“', { db: d.db.title || T('Unbenannt') })));
      var ul = el('ul', 'hk-mem-cols'); ul.setAttribute('aria-label', T('Erkannte Spalten'));
      var cols = d.columns || {};
      COLS.forEach(function (c) {
        var ci = cols[c[0]] || { name: c[0], found: false, ok: false };
        var li = el('li', 'hk-mem-col ' + (ci.ok ? 'ok' : ci.found ? 'miss wrong' : 'miss')); li.dataset.f = c[0];
        var mk = el('span', 'hk-mem-mk', ci.ok ? '✓' : ci.found ? '!' : '–'); mk.setAttribute('aria-hidden', 'true'); li.appendChild(mk);
        var nm = el('span', 'hk-mem-cn', ci.name); li.appendChild(nm);
        var st = ci.ok ? T('gefunden') : ci.found ? T('falscher Typ – erwartet: {type}', { type: TYPE[WANT[c[0]]] || WANT[c[0]] }) : T('fehlt');
        li.appendChild(el('span', 'hk-mem-cs', st));
        var ib = info([c[1]], T('Wozu „{name}“?', { name: ci.name }));
        li.appendChild(ib.btn); li.appendChild(ib.box);
        ul.appendChild(li);
      });
      box.appendChild(ul);
      if ((d.missing_required || []).length) {
        var w = el('p', 'hk-hint hk-mem-warn', T('Ohne Text-Spalte „{name}“ kann dein Assistent keine neuen Erinnerungen anlegen. Finden geht trotzdem – dann nur über den Titel.', { name: (cols.inhalt && cols.inhalt.name) || 'Inhalt' }));
        w.setAttribute('role', 'note'); box.appendChild(w);
      } else {
        box.appendChild(el('p', 'hk-hint', T('Fehlende Spalten sind kein Fehler – sie zählen dann einfach nicht mit. Saga legt keine Spalten an.')));
      }
    }

    // Stand: "n von m Erinnerungen vorbereitet" bzw. Rueckfall ohne Embeddings. Die Teile, die sich beim Nachfragen
    // aendern (Zahl, "wird vorbereitet", Warnung, Knopf), haelt ui fest - updateState() aendert nur sie (offene ⓘ und
    // Auswahllisten bleiben offen).
    function state() {
      var s = d.status || {};
      ui = { source: s.source };
      box.appendChild(el('h4', null, T('Stand')));
      var p = el('p', 'hk-mem-state'); p.id = 'hk-mem-state';
      ui.text = document.createTextNode('');
      p.appendChild(ui.text);
      if (s.source === 'trigramm') {
        box.appendChild(p);
        var fb = el('p', 'hk-hint hk-mem-fallback');
        fb.appendChild(document.createTextNode(T('Auf diesem Server ist die Suche nach Bedeutung nicht eingerichtet. Saga sucht deshalb nach ähnlichen Wörtern – das findet weniger, funktioniert aber.') + ' '));
        var fi = info([T('Der Admin kann unter Admin › KI-Assistent › Embeddings einen Embedding-Dienst eintragen (z. B. Ollama im eigenen Netz). Danach bereitet Saga deine Erinnerungen von selbst vor.')], T('Was fehlt?'));
        fb.appendChild(fi.btn); box.appendChild(fb); box.appendChild(fi.box);
        updateState();
        return;
      }
      var si = info([T('Vorbereitet heißt: Saga hat für die Erinnerung ein Embedding berechnet (Modell: {model}) und findet sie nach Bedeutung. Neue und geänderte Erinnerungen bereitet Saga nach ein paar Sekunden von selbst vor.', { model: s.model || '?' })], T('Was heißt vorbereitet?'));
      p.appendChild(si.btn); box.appendChild(p); box.appendChild(si.box);
      ui.run = el('p', 'hk-hint hk-mem-run', T('Wird gerade vorbereitet …'));
      ui.warn = el('p', 'hk-hint hk-mem-warn', T('Der Dienst für die Suche nach Bedeutung ist gerade nicht erreichbar. Saga sucht solange nach ähnlichen Wörtern.'));
      ui.pend = el('p', 'hk-hint hk-mem-pend', T('Noch nicht alle Erinnerungen sind vorbereitet. Ist der Dienst gerade nicht erreichbar, sucht Saga solange nach ähnlichen Wörtern.'));
      box.appendChild(ui.run); box.appendChild(ui.warn); box.appendChild(ui.pend);
      var act = el('div', 'hk-actions'), go = el('button', 'btn', T('Jetzt vorbereiten')); go.type = 'button'; go.id = 'hk-mem-embed';
      ui.go = go;
      act.appendChild(go); box.appendChild(act);
      go.addEventListener('click', function () {
        go.disabled = true; embedWait = true;
        status(msg, T('Wird vorbereitet …'));
        api('POST', '/api/account/memory/embed').then(function (x) {
          if (x && x.status) d.status = x.status;
          updateState();
          status(msg, T('✓ Wird vorbereitet – das kann ein paar Minuten dauern.'), 'ok'); polls = 0; grace = 5; schedulePoll();
        }, function (e) {
          embedWait = false;
          if (e.data && e.data.status) d.status = e.data.status;
          updateState();
          status(msg, e.data && e.data.code === 'embed_unreachable' ? T('Der Dienst für die Suche nach Bedeutung ist gerade nicht erreichbar – bitte später noch einmal.') : errText(e), 'err');
        });
      });
      updateState();
    }
    function updateState() {
      var s = (d && d.status) || {};
      if (!ui.text) return;
      if (s.source === 'trigramm') { ui.text.textContent = T('{n} {n:Erinnerung|Erinnerungen}', { n: s.rows || 0 }) + ' '; return; }
      ui.text.textContent = T('{n} von {m} Erinnerungen vorbereitet', { n: s.embedded || 0, m: s.rows || 0 }) + ' ';
      ui.run.hidden = !s.running;
      ui.warn.hidden = !s.error;
      ui.pend.hidden = !!(s.error || s.running || !s.rows || s.embedded >= s.rows);
      if (!s.running && embedWait && (s.error || s.embedded >= s.rows)) {
        embedWait = false;
        if (s.error) status(msg, T('Der Dienst für die Suche nach Bedeutung ist gerade nicht erreichbar – bitte später noch einmal.'), 'err');
      }
      ui.go.disabled = !!s.running || (embedWait && !s.error);
    }
    // Nachfragen, solange Saga vorbereitet; nach einer Aenderung kurz auch ohne "running" (der Auftrag startet entprellt).
    // Nur der Stand wird aktualisiert - neu gebaut wird nur, wenn sich mehr geaendert hat (andere Datenbank, Quelle, Spalten).
    function schedulePoll() {
      clearTimeout(poll);
      var s = d && d.status;
      if (!sec.isConnected || !d || !d.db || !s || s.source !== 'embeddings') return;
      if (!s.running && !embedWait && (grace <= 0 || !(s.embedded < s.rows))) return;
      if (!s.running) grace--;
      if (++polls > POLL_MAX) { embedWait = false; updateState(); return; }
      poll = setTimeout(function () {
        if (!sec.isConnected) return;
        api('GET', '/api/account/memory').then(function (x) {
          var same = x.db && d.db && x.db.id === d.db.id && (x.status || {}).source === ui.source
            && JSON.stringify(x.columns) === JSON.stringify(d.columns) && ((x.core || {}).id === (d.core || {}).id);
          d = x;
          if (same) updateState(); else render();
          schedulePoll();
        }, function () {});
      }, POLL_MS);
    }
    load().then(function () { schedulePoll(); });
  }

  window.HKMemory = { section: section };
})();
