/* Ort "Saga" (2.6, Bugs-DB 2269 K-I, Konzept K10/K11/K19, M1/M3/M4/M7): Kennenlernen-Karte (auf "Heute" einmal ueber
   "Weitermachen", im Ort "Saga" solange keine Einwilligung), das Gespraech mit der KI (/saga, eigene Tabelle, kein
   E2E-Chat) und Einstellungen > Saga > "Meine Daten". Alle Texte zum Anbieter/Host kommen fertig vom Server
   (/api/ai/status: endpoint, consent, intro) - der Client baut nie selbst "im Heimnetz" oder "lokal".
   Einhaengepunkt fuer andere Bauteile (Plan-Karte/Rueckmeldung, Rolle kb): Container #sg-cards und
   HKSaga.mount(node) (haengt an den Verlauf an), Ereignisse auf document: "saga:status" {status}, "saga:turn" {turn},
   "saga:consent" {consent}. Alles per textContent, keine Links aus Modelltext. */
(function () {
  'use strict';
  var HK = window.HK;
  if (!HK || !HK.api) return;
  var el = HK.el, api = HK.api, $ = function (id) { return document.getElementById(id); };
  var S = window.HKSaga = { st: null };
  var onPage = !!$('sg-feed'), onToday = !!$('hz-saga-intro');
  var sideOn = !!$('pg-nav-saga');                    // Server: Faehigkeit ai (sonst fehlt der Eintrag und alles hier)

  function P() { return (S.st && S.st.persona) || { name: T('Saga KI'), avatar: null }; }
  function kiName() {                                 // K-H (2268): Kennzeichnung bildet der Server (style.label; Standardname ohne "· KI")
    var lb = S.st && S.st.style && S.st.style.label;
    if (lb) return lb;
    return P().custom_name === false ? P().name : T('{name} · KI', { name: P().name });
  }
  function av(px) { return window.HKAIAvatar ? HKAIAvatar(P(), px) : el('span', 'ai-av', '✦'); }
  function emit(name, detail) { document.dispatchEvent(new CustomEvent(name, { detail: detail })); }
  function hostLabel() { return (S.st && S.st.endpoint && S.st.endpoint.label) || (S.st && S.st.host) || '–'; }
  function consentOk() { return !!(S.st && S.st.consent && S.st.consent.state === 'granted'); }

  S.load = function () {
    return api('GET', '/api/ai/status').then(function (d) {
      S.st = d && d.enabled ? d : null;
      emit('saga:status', { status: S.st });
      return S.st;
    });
  };

  // --- B1 KI-Kopf: Bild + "{Persona} · KI" (K11), nie ✦ ohne "· KI" ---------------------------------------------------
  function head(px, extra) {
    var h = el('div', 'sg-who');
    h.appendChild(av(px || 24));
    h.appendChild(el('span', 'sg-who-n', kiName()));
    if (extra) h.appendChild(el('span', 'sg-who-x', extra));
    return h;
  }

  // --- ⓘ: Fachwoerter nur hier (M3) ---------------------------------------------------------------------------------
  function infoBtn(lines, label) {
    var wrap = el('span', 'sg-info');
    var b = el('button', 'sg-info-b', 'ⓘ'); b.type = 'button';
    b.setAttribute('aria-label', label || T('Mehr dazu')); b.setAttribute('aria-expanded', 'false');
    var box = el('div', 'sg-info-box'); box.hidden = true;
    var id = 'sg-i-' + Math.random().toString(36).slice(2, 8); box.id = id; b.setAttribute('aria-controls', id);
    (lines || []).forEach(function (l) { box.appendChild(el('p', null, l)); });
    b.addEventListener('click', function () {
      box.hidden = !box.hidden; b.setAttribute('aria-expanded', box.hidden ? 'false' : 'true');
    });
    wrap.appendChild(b);
    return { btn: wrap, box: box };
  }

  // --- Kennenlernen-Karte (K19) -------------------------------------------------------------------------------------
  // opts.later(): "Jetzt nicht" gedrueckt; opts.done(consent): Einwilligung erteilt. Knoepfe: Ja, mit Ideen · Ja, ohne Ideen
  // (ohne Faehigkeit ai_ideas: Ja, einverstanden) · Jetzt nicht; darunter "Mehr einstellen".
  S.introCard = function (opts) {
    opts = opts || {};
    var it = (S.st && S.st.intro) || {};
    var card = el('section', 'sg-card sg-intro');
    card.setAttribute('aria-label', T('{name} kennenlernen', { name: P().name }));
    card.appendChild(head(36));
    var lines = it.lines || [];
    lines.forEach(function (l, i) {
      var p = el('p', 'sg-intro-l' + (i === 1 ? ' sg-intro-where' : ''), l);
      if (i === 1) {
        var inf = infoBtn(it.details, T('Was genau rausgeht'));
        p.appendChild(document.createTextNode(' ')); p.appendChild(inf.btn);
        card.appendChild(p);
        if (it.note) card.appendChild(el('p', 'sg-intro-note', it.note));
        card.appendChild(inf.box);
        return;
      }
      card.appendChild(p);
    });
    if (it.question) card.appendChild(el('p', 'sg-intro-q', it.question));
    var bar = el('div', 'sg-intro-bar'), err = el('p', 'sg-err'); err.hidden = true; err.setAttribute('role', 'alert');
    function go(answer, b) {
      [].forEach.call(bar.querySelectorAll('button'), function (x) { x.disabled = true; });
      api('POST', '/api/ai/consent', { answer: answer, version: it.version, host: it.host }).then(function (r) {
        if (S.st) S.st.consent = r.consent;
        emit('saga:consent', { consent: r.consent });
        if (answer === 'later') { if (opts.later) opts.later(); }
        else if (opts.done) opts.done(r.consent);
      }).catch(function (e) {
        [].forEach.call(bar.querySelectorAll('button'), function (x) { x.disabled = false; });
        err.textContent = e.message; err.hidden = false;
        if (e.status === 409) S.load().then(function () { if (opts.reload) opts.reload(); });
        if (b) b.focus();
      });
    }
    function btn(label, cls, answer) {
      var b = el('button', 'btn ' + cls, label); b.type = 'button';
      b.addEventListener('click', function () { go(answer, b); });
      bar.appendChild(b); return b;
    }
    if (it.ideas_allowed) {
      btn(T('Ja, mit Ideen'), 'btn-primary', 'ideas');
      btn(T('Ja, ohne Ideen'), '', 'no_ideas');
    } else btn(T('Ja, einverstanden'), 'btn-primary', 'no_ideas');
    var no = btn(T('Jetzt nicht'), 'btn-quiet sg-later', 'later'); no.classList.add('linkbtn');
    card.appendChild(bar); card.appendChild(err);
    var more = el('button', 'linkbtn sg-more-set', T('Mehr einstellen')); more.type = 'button';
    more.addEventListener('click', function () { openSettings(); });
    card.appendChild(more);
    return card;
  };

  function openSettings() {
    if (window.HKSettings && HKSettings.open) HKSettings.open('saga');
    else location.href = '/dashboard?settings=saga';
  }

  // --- Heute: Karte einmal ueber "Weitermachen" (R1) ----------------------------------------------------------------
  function today() {
    var slot = $('hz-saga-intro');
    S.load().then(function (st) {
      if (!st || !st.consent || ['none', 'stale'].indexOf(st.consent.state) < 0 || !st.intro) return;
      slot.textContent = '';
      slot.appendChild(S.introCard({
        later: function () { slot.hidden = true; slot.textContent = ''; },
        done: function () {
          slot.textContent = '';
          var ok = el('div', 'sg-card sg-intro-done'); ok.setAttribute('role', 'status');
          ok.appendChild(head(24));
          var p = el('p', null, T('Schön, dass wir uns kennen. Du findest mich in der Seitenleiste unter „Saga“.'));
          ok.appendChild(p);
          var a = el('a', 'btn', T('Zu Saga')); a.href = '/saga'; ok.appendChild(a);
          slot.appendChild(ok);
        },
        reload: today }));
      slot.hidden = false;
    }).catch(function () { /* KI nicht erreichbar: keine Karte */ });
  }

  // --- Gespraech (Ort "Saga") ---------------------------------------------------------------------------------------
  function stream(url, body, on) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var p = fetch(url, { method: 'POST', credentials: 'same-origin', signal: ctrl ? ctrl.signal : undefined,
                         headers: { 'X-Saga': '1', 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify(body) })
      .then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok || ct.indexOf('text/event-stream') !== 0) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            var e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; e.data = d; throw e;
          });
        }
        var rd = r.body.getReader(), dec = new TextDecoder(), buf = '';
        function one(chunk) {
          var ev = null, data = '';
          chunk.split('\n').forEach(function (ln) {
            if (ln.indexOf('event:') === 0) ev = ln.slice(6).trim();
            else if (ln.indexOf('data:') === 0) data += ln.slice(5).trim();
          });
          if (ev) { try { on(ev, JSON.parse(data || 'null')); } catch (e) { /* kaputte Zeile */ } }
        }
        function pump() {
          return rd.read().then(function (x) {
            if (x.done) { if (buf.trim()) one(buf); return; }
            buf += dec.decode(x.value, { stream: true });
            var i;
            while ((i = buf.indexOf('\n\n')) >= 0) { one(buf.slice(0, i)); buf = buf.slice(i + 2); }
            return pump();
          });
        }
        return pump();
      });
    return { done: p, abort: function () { if (ctrl) ctrl.abort(); } };
  }
  // Antworttext -> DOM (feste KI-Markdown-Teilmenge wie static/ai.js: Absaetze, Listen, **fett**, `code`, [n] -> Quelle)
  function inline(p, s, byN) {
    var re = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[0-9]{1,2}(?:\s*[,;]\s*[0-9]{1,2})*\])/g, last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) p.appendChild(document.createTextNode(s.slice(last, m.index)));
      var x = m[0];
      if (x.charAt(0) === '[') {
        x.slice(1, -1).split(/\s*[,;]\s*/).forEach(function (n, k) {
          var src = byN[+n];
          if (k) p.appendChild(document.createTextNode(' '));
          if (src && src.id) { var a = el('a', 'ai-cite', '[' + n + ']'); a.href = '/notes#' + src.id; a.title = src.title || ''; p.appendChild(a); }
          else p.appendChild(document.createTextNode('[' + n + ']'));
        });
      } else if (x.charAt(0) === '`') p.appendChild(el('code', null, x.slice(1, -1)));
      else p.appendChild(el('strong', null, x.slice(2, -2)));
      last = re.lastIndex;
    }
    if (last < s.length) p.appendChild(document.createTextNode(s.slice(last)));
  }
  function answer(text, sources) {
    var w = el('div', 'sg-answer'), byN = {}, list = null;
    (sources || []).forEach(function (s) { if (s && s.n) byN[s.n] = s; });
    String(text || '').split('\n').forEach(function (ln) {
      var m = /^\s*(?:[-*•]|([0-9]{1,2})[.)])\s+(.*)$/.exec(ln);
      if (m) {
        var tag = m[1] ? 'ol' : 'ul';
        if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag, 'ai-list'); w.appendChild(list); }
        var li = el('li'); if (m[1]) li.value = +m[1]; inline(li, m[2], byN); list.appendChild(li); return;
      }
      list = null;
      if (!ln.trim()) return;
      var p = el('p', 'ai-p'); inline(p, ln.replace(/^\s*#{1,6}\s+/, ''), byN); w.appendChild(p);
    });
    return w;
  }
  function srcLine(sources) {
    if (!sources || !sources.length) return null;
    var w = el('div', 'sg-srcs'); w.appendChild(el('span', 'sg-srcs-l', T('aus:')));
    sources.forEach(function (s) {
      if (!s || !s.id) return;
      var a = el('a', 'sg-src', s.title || T('Unbenannt')); a.href = '/notes#' + s.id; w.appendChild(a);
    });
    return w;
  }
  function reportBtn(text) {
    if (!HK.report) return null;
    var b = el('button', 'btn btn-quiet sg-report', T('Melden …')); b.type = 'button';
    var sent = false;
    b.addEventListener('click', function () {
      if (sent) return;
      HK.report({ title: T('KI-Antwort melden'), include: T('Antwort der KI mitschicken'), text: text,
        hint: T('Die Meldung geht an die Admins dieses Saga-Servers – mit dem Text, den du hier mitschickst.'),
        send: function (r) { return api('POST', '/api/ai/report', { where: 'ask', reason: r.reason, text: r.text }); } })
        .then(function (r) { if (!r) return; sent = true; b.setAttribute('aria-disabled', 'true'); b.textContent = T('Gemeldet'); });
    });
    return b;
  }
  function timeOf(ts) {
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  }
  function turnNode(tn) {
    if (tn.role === 'me') {
      var me = el('div', 'sg-me'); me.appendChild(el('p', 'sg-me-t', tn.text)); return me;
    }
    var c = el('article', 'sg-card sg-ai');
    c.appendChild(head(24, tn.created ? timeOf(tn.created) : ''));
    var srcs = (tn.fields && tn.fields.sources) || [];
    c.appendChild(answer(tn.text, srcs));
    var sl = srcLine(srcs); if (sl) c.appendChild(sl);
    if (tn.zug !== 'no_match') { var rb = reportBtn(tn.text); if (rb) c.appendChild(rb); }
    c.dataset.n = tn.n || '';
    return c;
  }
  S.mount = function (node) {                         // Einhaengepunkt (kb): Karte an den Verlauf anhaengen
    var feed = $('sg-feed'); if (!feed || !node) return null;
    feed.hidden = false; var e = $('sg-empty'); if (e) e.remove();
    feed.appendChild(node); return node;
  };

  var G = { run: null, turns: [] };
  function empty() {
    var feed = $('sg-feed'); feed.textContent = ''; feed.hidden = false;
    var w = el('div', 'sg-empty'); w.id = 'sg-empty';
    w.appendChild(av(56));
    w.appendChild(el('p', 'sg-empty-t', T('Sag mir, was du brauchst – zum Beispiel:')));
    var chips = el('div', 'sg-chips');
    [T('Was steht diese Woche an?'), T('Fass meine Notizen zum Sommerfest zusammen'), T('Welche Aufgaben sind offen?')].forEach(function (q) {
      var b = el('button', 'sg-chip', q); b.type = 'button';
      b.addEventListener('click', function () { $('sg-ta').value = q; send(); });
      chips.appendChild(b);
    });
    w.appendChild(chips);
    feed.appendChild(w);
  }
  function renderFeed() {
    var feed = $('sg-feed');
    if (!G.turns.length) { empty(); return; }
    feed.textContent = ''; feed.hidden = false;
    G.turns.forEach(function (tn) { feed.appendChild(turnNode(tn)); });
  }
  function keepLine(d) {
    var k = $('sg-keep');
    k.textContent = '';
    k.appendChild(document.createTextNode(d.history ? T('Verlauf an – das Gespräch bleibt gespeichert.')
      : T('Verlauf aus – das Gespräch verschwindet nach 24 Stunden.') + ' '));
    var b = el('button', 'linkbtn', T('Ändern')); b.type = 'button'; b.addEventListener('click', openSettings);
    k.appendChild(b); k.hidden = false;
  }
  function busy(on) {
    $('sg-stop').hidden = !on; $('sg-send').disabled = on; $('sg-ta').readOnly = on;
    $('sg-status').textContent = on ? T('{name} denkt nach …', { name: P().name }) : '';
    $('sg-feed').setAttribute('aria-busy', on ? 'true' : 'false');
  }
  function send() {
    var ta = $('sg-ta'), q = ta.value.trim();
    if (!q || G.run) return;
    var feed = $('sg-feed'), e = $('sg-empty'); if (e) e.remove();
    feed.appendChild(turnNode({ role: 'me', text: q }));
    ta.value = '';
    var card = el('article', 'sg-card sg-ai sg-live'); card.appendChild(head(24));
    var body = el('div', 'sg-answer'); card.appendChild(body); feed.appendChild(card);
    var text = '', srcs = [], fail = null;
    busy(true);
    G.run = stream('/api/ai/thread', { q: q }, function (ev, d) {
      if (ev === 'sources') srcs = (d && d.sources) || [];
      else if (ev === 'reset') { text = ''; body.textContent = ''; }
      else if (ev === 'delta') { text += d.t || ''; body.textContent = text; }
      else if (ev === 'error') fail = new Error((d && d.error) || T('Der Assistent ist gerade nicht erreichbar.'));
      else if (ev === 'done') {
        var tn = { role: 'ai', n: d.turn, text: d.text || text, zug: d.no_match ? 'no_match' : 'answer', created: Date.now() / 1000,
                   read: d.read || [], fields: { sources: srcs } };
        var node = turnNode(tn); card.replaceWith(node); G.turns.push({ role: 'me', text: q }, tn);
        emit('saga:turn', { turn: tn, q: q });
      }
    });
    G.run.done.then(function () { if (fail) throw fail; }).catch(function (err) {
      if (err && err.name === 'AbortError') { card.classList.add('sg-stopped'); return; }
      var p = el('p', 'sg-err', err.message); p.setAttribute('role', 'alert');
      card.appendChild(p); card.classList.remove('sg-live');
      if (err.status === 403 && err.data && err.data.consent === 'needed') S.load().then(page);
    }).then(function () { G.run = null; busy(false); try { ta.focus({ preventScroll: true }); } catch (x) { ta.focus(); } });
  }
  function moreMenu() {
    var b = $('sg-more'); b.hidden = false;
    b.addEventListener('click', function () {
      HK.openMenu(b, function (m) {
        m.appendChild(HK.menuItem(T('Gespräch leeren'), function () {
          HK.ask({ title: T('Gespräch leeren?'), text: T('Alle Beiträge dieses Gesprächs werden gelöscht.'), ok: T('Leeren'), danger: true })
            .then(function (yes) {
              if (!yes) return;
              api('DELETE', '/api/ai/thread').then(function () { G.turns = []; renderFeed(); }).catch(function (e) { HK.toast(e.message); });
            });
        }, { ic: 'trash' }));
        m.appendChild(HK.menuItem(T('Einstellungen › Saga'), openSettings, { ic: 'settings' }));
      });
    });
  }
  function bar() {                                     // B6: fest oben, einzeilig, nicht wegklickbar, antippbar (ⓘ)
    var b = $('sg-bar'), it = (S.st && S.st.intro) || {};
    b.textContent = '';
    if (!it.bar) { b.hidden = true; return; }
    var inf = infoBtn(it.details, T('Was genau rausgeht'));
    var line = el('p', 'sg-bar-t'), tx = el('span', 'sg-bar-tx', it.bar); tx.title = it.bar;
    tx.addEventListener('click', function () { inf.btn.firstChild.click(); });   // antippbar wie ⓘ
    line.appendChild(tx); line.appendChild(inf.btn);
    b.appendChild(line); b.appendChild(inf.box); b.hidden = false;
  }
  function page() {
    var st = S.st;
    var avBox = $('sg-av'); avBox.textContent = ''; avBox.appendChild(av(36));
    $('sg-title').textContent = kiName();
    bar();
    var slot = $('sg-intro'), form = $('sg-ask');
    if (!st) { slot.hidden = false; slot.textContent = ''; slot.appendChild(el('p', 'sg-err', T('Der KI-Assistent ist für dich nicht eingeschaltet.'))); form.hidden = true; return; }
    $('sg-ta').placeholder = T('Schreib {name} …', { name: P().name });
    $('sg-ta').setAttribute('aria-label', T('Nachricht an {name}', { name: P().name }));
    if (!consentOk()) {                                // vor der Einwilligung: nur die Karte, keine Eingabe (nichts ans Modell)
      form.hidden = true; $('sg-feed').hidden = true; $('sg-keep').hidden = true;
      slot.textContent = '';
      slot.appendChild(S.introCard({ done: function () { S.load().then(page); }, reload: function () { page(); },
        later: function () { location.href = '/dashboard'; } }));
      slot.hidden = false;
      return;
    }
    slot.hidden = true; slot.textContent = '';
    form.hidden = false;
    api('GET', '/api/ai/thread').then(function (d) {
      G.turns = d.turns || []; renderFeed(); keepLine(d);
    }).catch(function (e) { HK.toast(e.message); });
  }
  function pageInit() {
    $('sg-ask').addEventListener('submit', function (e) { e.preventDefault(); send(); });
    $('sg-ta').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });
    $('sg-stop').addEventListener('click', function () { if (G.run) G.run.abort(); });
    moreMenu();
    S.load().then(page).catch(function (e) { HK.toast(e.message); });
  }

  // --- Einstellungen > Saga (+ "Meine Daten", M7) -------------------------------------------------------------------
  // Der Abschnitt entsteht sofort (die Navigation der Einstellungen zaehlt die Bereiche beim Oeffnen), gefuellt wird er,
  // sobald /api/ai/prefs da ist. Ohne Faehigkeit ai (kein Eintrag "Saga" in der Seitenleiste) gibt es ihn nicht.
  function row(label, ctl, hint) {
    var l = el('label', 'hk-row sg-set-row'); l.appendChild(ctl); l.appendChild(el('span', null, label));
    if (hint) l.appendChild(el('small', 'hk-hint', hint));
    return l;
  }
  function check(on, fn) {
    var i = el('input'); i.type = 'checkbox'; i.checked = !!on;
    i.addEventListener('change', function () { fn(i.checked, i); });
    return i;
  }
  function put(body, ctl) {
    return api('PUT', '/api/ai/prefs', body).catch(function (e) {
      HK.toast(e.message); if (ctl && ctl.type === 'checkbox') ctl.checked = !ctl.checked; throw e;
    });
  }
  function fillSettings(sec, d) {
    var box = sec.querySelector('.sg-set-body'); box.textContent = '';
    if (!d || d.enabled === false) { box.remove(); return; }           // KI aus: den Abschnitt blendet settings.js aus
    var c = d.consent || {}, name = P().name;
    var st = el('p', 'hk-hint sg-set-state');
    if (c.state === 'granted') st.textContent = T('Du nutzt {name} seit {date}.', { name: name, date: new Date(c.at * 1000).toLocaleDateString() });
    else if (c.state === 'withdrawn') st.textContent = T('Du hast die Einwilligung widerrufen: {name} erledigt keine Aufträge, bringt keine Ideen, und das Gespräch im Ort „Saga“ ist aus. Fragen an anderen Stellen, etwa „Frag deine Seiten“, gehen weiter an {host} – nur, wenn du sie stellst.', { name: name, host: hostLabel() });
    else if (c.state === 'stale') st.textContent = T('Der Admin hat die KI umgestellt – lerne {name} noch einmal kennen, bevor es weitergeht.', { name: name });
    else st.textContent = T('Du hast {name} noch nicht kennengelernt.', { name: name });
    box.appendChild(st);
    if (c.state !== 'granted') {
      var go = el('a', 'btn', T('{name} kennenlernen', { name: name })); go.href = '/saga'; box.appendChild(go);
    }
    if (c.state === 'granted' && d.ideas_allowed) {
      var sel = el('select', 'hk-in'); sel.setAttribute('aria-label', T('Ideen von {name}', { name: name }));
      [['off', T('Aus')], ['today', T('Nur auf Heute')]].forEach(function (o) {
        var op = el('option', null, o[1]); op.value = o[0]; op.selected = d.ideas === o[0]; sel.appendChild(op);
      });
      sel.addEventListener('change', function () { put({ ideas: sel.value }).catch(function () { sel.value = d.ideas; }); });
      var lab = el('label', 'hk-row sg-set-row'); lab.appendChild(el('span', null, T('Ideen von {name}', { name: name }))); lab.appendChild(sel);
      box.appendChild(lab);
    }
    if (d.history_allowed) box.appendChild(row(T('Verlauf speichern'), check(d.history, function (v, i) { put({ history: v }, i); }),
      T('Aus: Das Gespräch verschwindet nach 24 Stunden. Ausschalten löscht den gespeicherten Verlauf.')));
    var srcH = el('h4', null, T('Was {name} lesen darf', { name: name })); box.appendChild(srcH);
    var SRC = [['pages', T('Seiten (eigene und für dich geteilte)')], ['tasks', T('Aufgaben')], ['calendar', T('Termine')],
               ['chat', T('Chat-Nachrichten, wenn du die KI im Chat rufst')]];
    SRC.forEach(function (s) {
      box.appendChild(row(s[1], check((d.sources || {})[s[0]] !== false, function (v, i) { var o = {}; o[s[0]] = v; put({ sources: o }, i); })));
    });
    // --- Meine Daten ---
    var md = el('div', 'sg-mydata'); md.id = 'sg-mydata';
    md.appendChild(el('h4', null, T('Meine Daten')));
    md.appendChild(el('p', 'hk-hint', T('Hier siehst und steuerst du, was {name} über dich gespeichert hat: Einwilligung, Gespräch, Aufträge und Verlauf.', { name: name })));
    var acts = el('div', 'sg-md-acts');
    if (c.state === 'granted' || c.state === 'stale') {
      var wd = el('button', 'btn', T('Einwilligung widerrufen')); wd.type = 'button';
      wd.addEventListener('click', function () {
        HK.ask({ title: T('Einwilligung widerrufen?'), text: T('Danach erledigt {name} keine Aufträge mehr (laufende halten an), bringt keine Ideen, und das Gespräch im Ort „Saga“ ist aus. Fragen an anderen Stellen, etwa „Frag deine Seiten“, gehen weiter an {host} – nur, wenn du sie stellst. Du kannst {name} jederzeit wieder kennenlernen.', { name: name, host: hostLabel() }),
                 ok: T('Widerrufen'), danger: true }).then(function (yes) {
          if (!yes) return;
          api('DELETE', '/api/ai/consent').then(function (r) {
            if (S.st) S.st.consent = r.consent;
            emit('saga:consent', { consent: r.consent });
            HK.toast(r.paused ? T('Widerrufen – {n} Aufträge angehalten.', { n: r.paused }) : T('Widerrufen.'));
            loadSettings(sec);
          }).catch(function (e) { HK.toast(e.message); });
        });
      });
      acts.appendChild(wd);
    }
    [['json', T('Verlauf und Aufträge exportieren (JSON)')], ['md', T('Als Text exportieren (Markdown)')]].forEach(function (f) {
      var a = el('button', 'btn sg-export', f[1]); a.type = 'button';
      a.addEventListener('click', function () {      // X-Saga-Kopf: ueber fetch + Blob statt eines nackten Links
        fetch('/api/ai/mydata?format=' + f[0], { credentials: 'same-origin', headers: { 'X-Saga': '1' } }).then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          var cd = r.headers.get('content-disposition') || '', m = /filename="([^"]+)"/.exec(cd);
          return r.blob().then(function (b) {
            var u = URL.createObjectURL(b), x = document.createElement('a'); x.href = u; x.download = m ? m[1] : 'saga-ki.' + f[0];
            document.body.appendChild(x); x.click(); x.remove(); setTimeout(function () { URL.revokeObjectURL(u); }, 5000);
          });
        }).catch(function (err) { HK.toast(err.message); });
      });
      acts.appendChild(a);
    });
    var del = el('button', 'btn btn-destr', T('Alles löschen')); del.type = 'button';
    del.addEventListener('click', function () {
      HK.ask({ title: T('Alles löschen?'), text: T('Gelöscht werden deine Aufträge mit ihrem Protokoll, das Gespräch mit {name} und dein Verlauf. Nachrichten, die schon gesendet wurden, bleiben bei den Empfängern. Das lässt sich nicht rückgängig machen.', { name: name }),
               ok: T('Alles löschen'), danger: true }).then(function (yes) {
        if (!yes) return;
        api('POST', '/api/ai/mydata/delete', { confirm: true }).then(function () { HK.toast(T('Gelöscht.')); loadSettings(sec); })
          .catch(function (e) { HK.toast(e.message); });
      });
    });
    acts.appendChild(del);
    md.appendChild(acts);
    var adm = el('details', 'sg-admin'); adm.appendChild(el('summary', null, T('Was mein Admin sehen kann')));
    [T('Dein Admin betreibt diesen Server. Technisch kann er alles lesen, was auf dem Server liegt – auch deine Seiten, Aufgaben und Termine, deine Aufträge und das Gespräch mit {name}, solange es gespeichert ist.', { name: name }),
     T('Deine Chats sind Ende-zu-Ende verschlüsselt – die kann er nicht lesen. Ausnahme: Nachrichten, in denen du die KI im Chat rufst, gehen im Klartext an die KI.'),
     T('In der Admin-Seite sieht er nur Zahlen (Anfragen pro Tag), keine Inhalte; auf Servern mit weniger als 5 Konten nur Summen.'),
     T('Was du {name} gibst, sieht auch der Dienst, der die KI betreibt: {where}.', { name: name, where: (S.st && S.st.endpoint && S.st.endpoint.label) || '–' })]
      .forEach(function (s) { adm.appendChild(el('p', null, s)); });
    md.appendChild(adm);
    box.appendChild(md);
  }
  function loadSettings(sec) {
    Promise.all([S.st ? Promise.resolve(S.st) : S.load(), api('GET', '/api/ai/prefs')]).then(function (r) { fillSettings(sec, r[1]); })
      .catch(function (e) { var b = sec.querySelector('.sg-set-body'); b.textContent = ''; b.appendChild(el('p', 'hk-hint', e.message)); });
  }
  // Der Abschnitt "Saga" selbst (Ueberschrift, Anrede, Lebhaftigkeit) kommt aus static/settings.js (K-H, 2268); K-I haengt
  // seinen Teil darunter an. Fehlt der Abschnitt (aeltere settings.js), entsteht er hier.
  S.settingsSection = function (body) {
    if (!sideOn && !onPage) return;
    var sec = body.querySelector('.hk-set-saga');
    if (!sec) { sec = el('section', 'hk-set hk-set-saga'); sec.appendChild(el('h3', null, T('Saga'))); body.appendChild(sec); }
    var b = el('div', 'sg-set-body'); b.appendChild(el('p', 'hk-hint', T('Lädt …'))); sec.appendChild(b);
    loadSettings(sec);
  };

  if (onPage) pageInit();
  else if (onToday && sideOn) setTimeout(today, 300);
})();
