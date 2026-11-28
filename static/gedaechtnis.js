// Saga 2.6 (Bugs-DB 2581, Feature memory1): Einstellungen > Konto > Gedaechtnis - eine eigene Datenbank als Gedaechtnis
// und eine eigene Seite als Ich-Kern binden. Vertrag: docs/MEMORY.md 1.2 (GET/PUT /api/account/memory,
// POST /api/account/memory/embed). static/settings.js ruft HKMemory.section(body, drop) auf; drop(k) nimmt den Eintrag
// aus der Navigation, wenn der Server das Gedaechtnis nicht kennt (404).
// Eigener Bereich statt Teil von "Saga": der verschwindet, wenn die KI fuer die Person aus ist - das Gedaechtnis wirkt
// aber unabhaengig davon (eigener Assistent ueber MCP bzw. Hooks). Fachwoerter (Embedding, Vektor) nur hinter ⓘ (M3).
// Stufe 3 (Bugs-DB 2601/2603, docs/MEMORY.md 1.2/2/11/12): Spalte "Sensibel", Block "Nickerchen" (Schnappschuesse),
// Block "Schattenmodus (Probelauf)" (Schalter, Stand Verknuepfungen/Spuren, Aufbau, "Spuren loeschen").
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
    ['abrufe', T('Saga zählt, wie oft die Erinnerung gebraucht wurde.')],
    ['sensibel', T('Kontrollkästchen: Angehakte Erinnerungen kommen nur, wenn direkt danach gefragt wird – nie über Verknüpfungen.')]
  ];
  var TYPE = { text: T('Text'), multi_select: T('Mehrfachauswahl'), select: T('Auswahl'), number: T('Zahl'), date: T('Datum'), checkbox: T('Kontrollkästchen') };
  var WANT = { inhalt: 'text', schlagworte: 'multi_select', projekt: 'select', art: 'select', wichtigkeit: 'number', valenz: 'number',
               erregung: 'number', datum: 'date', letzter_abruf: 'date', abrufe: 'number', sensibel: 'checkbox' };
  function when(iso) {                                // "2026-11-28T09:12+01:00" -> Datum/Uhrzeit in der Sprache der Person
    var t = Date.parse(iso || '');
    if (!isFinite(t)) return String(iso || '');
    try { return new Date(t).toLocaleString(window.SAGA_LOCALE || undefined, { dateStyle: 'medium', timeStyle: 'short' }); }
    catch (e) { return new Date(t).toLocaleString(); }
  }

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
    var d = null, pages = null, open = false, poll = null, polls = 0, grace = 0, coreQ = '', embedWait = false, ui = {}, us = {}, shGrace = 0;

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
        d = x; render(); refocus(fid); status(msg, what || T('✓ Gespeichert'), 'ok'); polls = 0; grace = 5;
        if (b.shadow === true) shGrace = 4;            // Aufbau startet im Hintergrund, evtl. erst nach der Antwort
        schedulePoll();
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
      if (d.db) { columns(); state(); nap(); shadow(); }
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
    // 2601: Nickerchen - Schnappschuesse (Option in der Spalte "Art"), die der Assistent vor einer Pause ablegt
    function nap() {
      var sn = d.snapshot; if (!sn) return;
      box.appendChild(el('h4', null, T('Nickerchen')));
      var p = el('p', 'hk-hint hk-mem-nap-intro');
      p.appendChild(document.createTextNode(T('Vor einer Pause kann dein Assistent einen Schnappschuss ablegen – Stimmung, offene Fäden, letzter Gedanke – und beim nächsten Start daran anknüpfen.') + ' '));
      var ni = info([T('Dein Assistent ruft dafür das Werkzeug nap auf; beim Aufwachen (wake) bekommt er den neuesten Schnappschuss und was sich seitdem geändert hat. Schnappschüsse bleiben alle erhalten, tauchen aber nie als Erinnerung auf.')], T('Wie funktioniert das Nickerchen?'));
      p.appendChild(ni.btn); box.appendChild(p); box.appendChild(ni.box);
      var st = el('p', 'hk-mem-state hk-mem-nap'); st.id = 'hk-mem-nap';
      var cols = d.columns || {}, art = cols.art || {};
      if (!sn.found) {
        st.className = 'hk-hint hk-mem-napwarn hk-mem-nap'; st.setAttribute('role', 'note');
        st.textContent = art.ok ? T('Lege in der Spalte „{col}“ die Option „{opt}“ an, damit dein Assistent Schnappschüsse ablegen kann.', { col: art.name || 'Art', opt: sn.option })
          : T('Für Schnappschüsse braucht die Datenbank eine Auswahl-Spalte „{col}“ mit der Option „{opt}“.', { col: art.name || 'Art', opt: sn.option });
      } else if (!sn.count) {
        st.textContent = T('Noch kein Schnappschuss (Option „{opt}“).', { opt: sn.option });
      } else {
        st.textContent = T('{n} {n:Schnappschuss|Schnappschüsse}', { n: sn.count }) + (sn.latest ? ' · ' + T('zuletzt {when}', { when: when(sn.latest) }) : '');
      }
      box.appendChild(st);
    }
    // 2603: Schattenmodus (Probelauf) - misst nur, aendert keine Antwort. Stand/Aufbau aendert updateShadow() (ohne Neuaufbau).
    function shadow() {
      var sh = d.shadow; us = {}; if (!sh) return;
      box.appendChild(el('h4', null, T('Schattenmodus (Probelauf)')));
      var l = el('label', 'hk-avset-row'), sw = el('input', 'hk-switch'); sw.type = 'checkbox'; sw.setAttribute('role', 'switch');
      sw.id = 'hk-mem-shadow'; sw.checked = !!sh.on; l.htmlFor = sw.id;
      l.appendChild(el('span', null, T('Schattenmodus'))); l.appendChild(sw); box.appendChild(l);
      var p = el('p', 'hk-hint hk-mem-shadow-txt');
      p.appendChild(document.createTextNode(T('Saga probiert im Hintergrund aus, ob verknüpfte Erinnerungen deinem Assistenten helfen würden. Antworten ändern sich nicht; gespeichert werden 30 Tage lang nur Zahlen und Stichworte, nie die Fragen.') + ' '));
      var si = info([T('Saga verknüpft ähnliche und oft gemeinsam gebrauchte Erinnerungen und merkt sich bei jedem Abruf, was zusätzlich aufgetaucht wäre. Meldet dein Assistent mit recall_used, was geholfen hat, werden diese Verknüpfungen stärker. Die Auswertung holt er mit shadow_report.'),
                     T('Erinnerungen mit „{name}“ werden nie verknüpft. Ausschalten behält Verknüpfungen und Spuren; Spuren verfallen nach 30 Tagen.', { name: ((d.columns || {}).sensibel || {}).name || 'Sensibel' })], T('Was misst der Schattenmodus?'));
      p.appendChild(si.btn); box.appendChild(p); box.appendChild(si.box);
      us.state = el('p', 'hk-mem-state hk-mem-shadow-state'); us.state.id = 'hk-mem-shadow-state'; box.appendChild(us.state);
      us.run = el('p', 'hk-hint hk-mem-shadow-run', T('Wird aufgebaut …')); box.appendChild(us.run);
      var act = el('div', 'hk-actions'), fg = el('button', 'btn', T('Spuren löschen')); fg.type = 'button'; fg.id = 'hk-mem-forget';
      act.appendChild(fg); box.appendChild(act); us.act = act; us.go = fg;
      sw.addEventListener('change', function () {
        var on = sw.checked; sw.disabled = true;
        put({ shadow: on }, on ? T('✓ Schattenmodus an – Saga baut die Verknüpfungen auf.') : T('✓ Schattenmodus aus – Verknüpfungen und Spuren bleiben.'), sw.id);
      });
      fg.addEventListener('click', function () {
        var n = (d.shadow || {}).traces || 0;
        HK.ask({ title: T('Spuren löschen?'), text: T('{n} {n:Spur|Spuren} des Schattenmodus jetzt löschen? Die Verknüpfungen bleiben.', { n: n }),
                 ok: T('Löschen'), danger: true }).then(function (yes) {
          if (!yes || !fg.isConnected) { refocus(fg.id); return; }
          fg.disabled = true; put({ forget_traces: true }, T('✓ Spuren gelöscht'), fg.id);
        });
      });
      updateShadow();
    }
    function updateShadow() {
      var sh = (d && d.shadow) || {};
      if (!us.state) return;
      us.state.textContent = T('{n} {n:Verknüpfung|Verknüpfungen}', { n: sh.links || 0 }) + ' · ' + T('{n} {n:Spur|Spuren} (30 Tage)', { n: sh.traces || 0 });
      us.state.hidden = !sh.on && !sh.links && !sh.traces;
      us.run.hidden = !sh.running;
      us.act.hidden = !sh.traces;
      us.go.disabled = !sh.traces;
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
      var s = d && d.status, sr = !!(d && d.shadow && d.shadow.running);
      if (!sec.isConnected || !d || !d.db || !s) return;
      var emb = s.source === 'embeddings' && (s.running || embedWait || (grace > 0 && s.embedded < s.rows));
      if (!emb && !sr && shGrace <= 0) return;       // 2603: auch solange die Verknuepfungen aufgebaut werden
      if (!s.running && !sr) grace--;
      if (!sr && shGrace > 0) shGrace--;
      if (++polls > POLL_MAX) { embedWait = false; updateState(); return; }
      poll = setTimeout(function () {
        if (!sec.isConnected) return;
        api('GET', '/api/account/memory').then(function (x) {
          var same = x.db && d.db && x.db.id === d.db.id && (x.status || {}).source === ui.source
            && JSON.stringify(x.columns) === JSON.stringify(d.columns) && ((x.core || {}).id === (d.core || {}).id)
            && JSON.stringify(x.snapshot) === JSON.stringify(d.snapshot) && !!(x.shadow || {}).on === !!(d.shadow || {}).on;
          d = x;
          if (same) { updateState(); updateShadow(); } else render();
          schedulePoll();
        }, function () {});
      }, POLL_MS);
    }
    load().then(function () { schedulePoll(); });
  }

  window.HKMemory = { section: section };
})();
