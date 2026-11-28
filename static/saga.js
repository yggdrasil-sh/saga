/* Ort "Saga" (2.6, Bugs-DB 2269 K-I, Konzept K10/K11/K19, M1/M3/M4/M7): Kennenlernen-Karte (auf "Heute" einmal ueber
   "Weitermachen", im Ort "Saga" solange keine Einwilligung), das Gespraech mit der KI (/saga, eigene Tabelle, kein
   E2E-Chat) und Einstellungen > Saga > "Meine Daten". Alle Texte zum Anbieter/Host kommen fertig vom Server
   (/api/ai/status: endpoint, consent, intro) - der Client baut nie selbst "im Heimnetz" oder "lokal".
   Einhaengepunkt fuer andere Bauteile (Plan-Karte/Rueckmeldung, Rolle kb): Container #sg-cards und
   HKSaga.mount(node) (haengt an den Verlauf an), Ereignisse auf document: "saga:status" {status}, "saga:turn" {turn},
   "saga:consent" {consent}. Alles per textContent, keine Links aus Modelltext.
   web-saga (W2, UX §2/R3/R4, K1/K10): Reiter "Gespraech / Auftraege"; im Gespraech gibt der Umschalter "Als Auftrag" die
   Bitte als Auftrag (POST /api/ai/jobs mit thread: true). Plan-Karte, Rueckfrage, Absage und Rueckmeldung sind DIESELBEN
   Bausteine wie im Ort "Auftraege" (static/ai-jobs.js, HKJobs) - es gibt nur ein Freigabe-Muster. Zuege mit job_id zeigt
   der Verlauf im jeweils aktuellen Stand (Plan-Karte, Rueckmeldung mit Rueckgaengig oder Zustand + "Ansehen").
   kg-web (2.6, Bugs-DB 2275 K-G, APP-API-KI 14.5): Teilen-Ziel der PWA. static/sw.js legt Geteiltes einmal lesbar im Cache
   "saga-share" ab und leitet nach /saga?share=<schluessel>; hier die Auswahl "Als Seite speichern" / "Als Aufgabe
   speichern" (POST /api/capture, auch ohne KI) / "{name} soll sich kuemmern …" (nur mit aishare1, Auftraegen, ai_act und
   Einwilligung) -> Zeile "Geteilt: host ×" ueber dem Feld (wie 2508), Auftrag mit source "share" + shared (from "web").
   /saga?share_failed=1 (Server-Rueckfall ohne Service Worker): nur ein Hinweis - der Inhalt kam nie hier an. */
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
      var p = el('p', 'sg-intro-l' + (i === 1 ? ' sg-intro-where' : ''), i === 1 ? null : l);
      if (i === 1) {
        var inf = infoBtn(it.details, T('Was genau rausgeht'));
        // Bugs-DB 2503: ⓘ haengt am letzten Wort (schmal nie allein in einer eigenen Zeile)
        var m = /^([\s\S]*\s)(\S+)$/.exec(l), tail = el('span', 'sg-nowrap');
        if (m) p.appendChild(document.createTextNode(m[1]));
        tail.appendChild(document.createTextNode((m ? m[2] : l) + '\u00a0')); tail.appendChild(inf.btn); p.appendChild(tail);
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
  var SRC_SHOW = 3, srcSeq = 0;                       // 2542: hoechstens 3 Quellen, der Rest aufklappbar
  function srcLine(sources) {
    var list = (sources || []).filter(function (s) { return s && s.id; });
    if (!list.length) return null;
    var w = el('div', 'sg-srcs'); w.appendChild(el('span', 'sg-srcs-l', T('aus:')));
    function link(s) { var a = el('a', 'sg-src', s.title || T('Unbenannt')); a.href = '/notes#' + s.id; return a; }
    list.slice(0, SRC_SHOW).forEach(function (s) { w.appendChild(link(s)); });
    var rest = list.slice(SRC_SHOW);
    if (!rest.length) return w;
    var more = el('span', 'sg-srcs-more'); more.id = 'sg-srcs-' + (++srcSeq); more.hidden = true;
    rest.forEach(function (s) { more.appendChild(link(s)); });
    var b = el('button', 'sg-srcs-b'); b.type = 'button'; b.setAttribute('aria-controls', more.id);
    function label(open) {                           // Name beginnt mit dem sichtbaren Text (WCAG 2.5.3) und nennt "Quellen"
      b.textContent = open ? T('weniger') : T('{n} weitere', { n: rest.length });
      b.setAttribute('aria-label', open ? T('Weniger Quellen anzeigen') : T('{n} weitere {n:Quelle|Quellen} anzeigen', { n: rest.length }));
      b.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    label(false);
    b.addEventListener('click', function () {
      var open = more.hidden; more.hidden = !open; label(open);
      if (open && more.firstChild) more.firstChild.focus();      // Tastatur: weiter in den aufgeklappten Quellen, dann "weniger"
    });
    w.appendChild(more); w.appendChild(b);
    return w;
  }
  function reportBtn(text, where, id) {             // where: 'ask' (Antwort) | 'job' (Plan/Rueckmeldung, id = Auftrag)
    if (!HK.report) return null;
    var b = el('button', 'btn btn-quiet sg-report', T('Melden …')); b.type = 'button';
    var sent = false;
    b.addEventListener('click', function () {
      if (sent) return;
      HK.report({ title: T('KI-Antwort melden'), include: T('Antwort der KI mitschicken'), text: text,
        hint: T('Die Meldung geht an die Admins dieses Saga-Servers – mit dem Text, den du hier mitschickst.'),
        send: function (r) {
          var body = { where: where === 'job' ? 'job' : 'ask', reason: r.reason, text: r.text }; if (where === 'job' && id) body.page = id;
          return api('POST', '/api/ai/report', body);
        } })
        .then(function (r) { if (!r) return; sent = true; b.setAttribute('aria-disabled', 'true'); b.textContent = T('Gemeldet'); });
    });
    return b;
  }
  function timeOf(ts) {
    try { return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (e) { return ''; }
  }
  function turnNode(tn) {
    if (tn.role === 'me') {
      var me = el('div', 'sg-me' + (tn.zug === 'job' ? ' sg-me-job' : ''));
      if (tn.zug === 'job') me.appendChild(el('span', 'sg-me-tag', T('Auftrag')));
      me.appendChild(el('p', 'sg-me-t', tn.text));
      var shf = tn.fields && tn.fields.shared;            // 2275: Auftrag zu geteiltem Inhalt (nur Host, nie der Text)
      if (shf) me.appendChild(el('p', 'sg-me-shared', shareLabel(shf.host, shf.title)));
      return me;
    }
    if (jobsOn() && (tn.zug === 'plan' && tn.job_id || tn.zug === 'ask' || tn.zug === 'refuse')) return jobTurn(tn);
    var c = el('article', 'sg-card sg-ai');
    c.appendChild(head(24, tn.created ? timeOf(tn.created) : ''));
    var srcs = (tn.fields && tn.fields.sources) || [];
    c.appendChild(answer(tn.text, srcs));
    var sl = srcLine(srcs); if (sl) c.appendChild(sl);
    if (tn.zug !== 'no_match' && tn.zug !== 'plan') { var rb = reportBtn(tn.text); if (rb) c.appendChild(rb); }
    c.dataset.n = tn.n || '';
    return c;
  }
  S.kiName = kiName;                                  // fuer static/ai-jobs.js (Kopf B1, Darstellung, Melden)
  S.md = function (text) { return answer(text, []); };
  S.reportBtn = reportBtn;
  S.mount = function (node) {                         // Einhaengepunkt (kb): Karte an den Verlauf anhaengen
    var feed = $('sg-feed'); if (!feed || !node) return null;
    feed.hidden = false; var e = $('sg-empty'); if (e) e.remove();
    feed.appendChild(node); return node;
  };

  var G = { run: null, turns: [], mode: 'ask', tab: 'talk', jobs: null, note: null, share: null };
  function shareLabel(host, title) {                 // "Geteilt: schule.example" (Titel nur, wenn es keinen Host gibt)
    return host ? T('Geteilt: {what}', { what: host }) : title ? T('Geteilt: {what}', { what: '„' + title + '“' }) : T('Geteilt');
  }

  // --- Auftraege im Gespraech (web-saga, UX R3/R4; Bausteine aus static/ai-jobs.js) ---------------------------------
  function jobsOn() { return !!(S.st && S.st.jobs && window.HKJobs); }
  function live() { return $('sg-status'); }
  function focusSoft(node) {                         // nach eigener Aktion: Fokus nicht auf <body> fallen lassen
    var x = node && (node.querySelector('.kj-lead, .kj-result') || node);
    if (!x) return;
    if (!x.hasAttribute('tabindex')) x.tabIndex = -1;
    try { x.focus({ preventScroll: true }); } catch (e) { x.focus(); }
  }
  function jobWrap(id) {
    var w = el('div', 'sg-job'); if (id) w.dataset.job = id; return w;
  }
  function stateCard(d) {                            // Laeuft / Wartet auf dich / Abgebrochen / Hat nicht geklappt
    var c = el('section', 'kj-card kj-small sg-jobstate'); c.setAttribute('aria-label', d.state_text);
    c.appendChild(HKJobs.head(d.state_text));
    c.appendChild(el('p', 'kj-lead', d.title || d.reason_text || ''));
    if (d.reason_text && d.title) c.appendChild(el('p', 'kj-note', d.reason_text));
    var b = el('button', 'btn btn-quiet kj-view', T('Ansehen')); b.type = 'button';
    b.addEventListener('click', function () { openJob(d.id); });
    c.appendChild(b);
    return c;
  }
  function jobNode(d, w) {                           // aktueller Stand eines Auftrags -> genau ein Baustein
    if (d.state === 'wartet_ok' && d.plan && d.plan.steps) return planCardFor(d.plan, w);
    if (d.state === 'erledigt' || d.state === 'zurueckgenommen')
      return HKJobs.feedback(d, { onOpen: openJob, onChange: function () { if (w) refreshJob(w); } });
    return stateCard(d);
  }
  function planCardFor(plan, w) {
    return HKJobs.planCard(plan, { live: live(), onOpen: openJob, onEdit: function () { if (G.jobs) G.jobs.refresh(); },
      onDone: function (state) { if (w) w.dataset.state = state; if (G.jobs) G.jobs.refresh(); },
      // 2515: 409 state (anderswo freigegeben/abgebrochen) - der Baustein im jetzigen Stand statt der Plan-Karte
      onState: function (d) {
        if (!w) return;
        var n = jobNode(d, w); w.textContent = ''; w.appendChild(n); w.dataset.state = d.state; focusSoft(n);
        live().textContent = d.state_text;
        if (G.jobs) G.jobs.refresh();
      } });
  }
  function refreshJob(w) {
    var id = w.dataset.job; if (!id) return Promise.resolve();
    return api('GET', '/api/ai/jobs/' + id).then(function (d) {
      var had = w.contains(document.activeElement), was = w.dataset.state;
      var n = jobNode(d, w); w.textContent = ''; w.appendChild(n); w.dataset.state = d.state;
      if (had) focusSoft(n);
      if (was && was !== d.state) live().textContent = d.state_text;   // Zeile unter dem Feld nicht beim alten "Laeuft" lassen
    }).catch(function (e) {
      if (e.status === 404) { w.textContent = ''; var c = el('div', 'kj-card kj-small'); c.appendChild(HKJobs.head(''));
        c.appendChild(el('p', 'kj-lead', T('Diesen Auftrag gibt es nicht mehr.'))); w.appendChild(c); w.dataset.state = 'weg'; }
    });
  }
  function askNode(d, q, sh) {                       // sh: geteilter Inhalt geht mit der Antwort erneut mit (14.1)
    return HKJobs.askCard(d, function (ch) { giveJob(q, sh ? { choice: ch, source: 'share', shared: sh } : { choice: ch }, ch); });
  }
  function refuseNode(d, q) {
    return HKJobs.refuseCard(d, function (alt) { giveJob(q || ' ', { recipe: alt.recipe, fields: alt.fields }, alt.label || T('Als Aufgabe anlegen')); },
      function () { setMode('job'); var ta = $('sg-ta'); ta.value = q || ''; try { ta.focus(); } catch (e) { /* egal */ } });
  }
  function jobTurn(tn) {                             // gespeicherter Zug (GET /api/ai/thread)
    var f = tn.fields || {};
    if (tn.zug === 'ask') return askNode({ question: tn.text, choices: f.choices || [] }, f.q || '');
    if (tn.zug === 'refuse') return refuseNode({ text: tn.text, alternative: f.alternative }, f.q || '');
    var w = jobWrap(tn.job_id), ph = el('div', 'kj-card kj-small'); ph.appendChild(HKJobs.head(''));
    ph.appendChild(el('p', 'kj-lead', tn.text || '')); w.appendChild(ph);
    refreshJob(w);
    return w;
  }
  function giveJob(text, extra, said) {
    text = String(text || '');
    var sh = !extra && G.share ? G.share : null;       // 2275: Geteiltes geht nur mit einem neuen Auftrag (nie mit recipe)
    if ((!text.trim() && !extra && !sh) || G.run) return;
    var feed = $('sg-feed'), e = $('sg-empty'); if (e) e.remove(); feed.hidden = false;
    var shBody = sh ? sharedBody(sh) : (extra && extra.shared) || null;
    feed.appendChild(turnNode({ role: 'me', zug: 'job', text: said || text.trim() || T('Kümmere dich darum.'),
                                fields: shBody ? { shared: { host: hostOf(shBody.url), title: shBody.title } } : null }));
    var wait = el('article', 'kj-card kj-wait sg-live'); wait.appendChild(HKJobs.head(''));
    var dots = el('p', 'kj-thinking', T('{name} denkt nach …', { name: P().name })); wait.appendChild(dots);
    feed.appendChild(wait);
    var ta = $('sg-ta'); if (!extra) ta.value = '';
    busy(true);
    var body = Object.assign({ text: sh ? text.trim() : text, thread: true }, extra || {}), out = null;
    if (G.note && !body.recipe && !body.context) body.context = { note: G.note.id };   // 2508: Auftrag zu einer offenen Seite
    if (sh) { body.source = 'share'; body.shared = shBody; }
    G.run = HKJobs.post(body, function (ev, d) {
      if (ev === 'status') { if (d && d.text) dots.textContent = d.text; return; }
      var node;
      if (sh && (ev === 'plan' || ev === 'ask' || ev === 'refuse')) { G.share = null; setMode(G.mode); }   // einmal verbraucht (Zeile + Platzhalter)
      if (ev === 'plan') {
        node = jobWrap(d.job); node.dataset.state = 'wartet_ok'; node.appendChild(planCardFor(d, node));
        out = T('Plan bereit – wartet auf dein OK');
        if (G.jobs) G.jobs.refresh();
      } else if (ev === 'ask') node = askNode(d, text, shBody);
      else if (ev === 'refuse') node = refuseNode(d, text);
      else {
        node = el('div', 'kj-card'); node.appendChild(HKJobs.head(''));
        var p = el('p', 'kj-err', (d && d.error) || T('Das hat nicht geklappt.')); p.setAttribute('role', 'alert'); node.appendChild(p);
        if (d && d.status === 403) S.load().then(function () { if (!consentOk()) page(); });
      }
      wait.replaceWith(node);
    });
    G.run.done.catch(function (err) {
      if (err && err.name === 'AbortError') { wait.classList.add('sg-stopped'); dots.textContent = T('Abgebrochen – nichts ist passiert.'); dots.classList.remove('kj-thinking'); return; }
      var p = el('p', 'kj-err', T('Das hat nicht geklappt.')); p.setAttribute('role', 'alert'); wait.replaceWith(p);
    }).then(function () {
      G.run = null; busy(false); if (out) live().textContent = out;
      try { ta.focus({ preventScroll: true }); } catch (x) { ta.focus(); }
    });
  }
  function setMode(m) {
    G.mode = m === 'job' && jobsOn() ? 'job' : 'ask';
    var b = $('sg-mode'), ta = $('sg-ta'), job = G.mode === 'job';
    if (b) b.setAttribute('aria-pressed', job ? 'true' : 'false');
    $('sg-send').textContent = job ? T('Auftrag geben') : T('Senden');
    ta.placeholder = job ? (G.share ? T('Was soll {name} damit tun?', { name: P().name }) : T('Sag {name}, was du brauchst …', { name: P().name }))
      : T('Schreib {name} …', { name: P().name });
    ta.setAttribute('aria-label', job ? T('Auftrag an {name}', { name: P().name }) : T('Nachricht an {name}', { name: P().name }));
    noteLine();
    shareLine();
  }
  // 2508: aus dem KI-Bereich einer Seite ("{name} soll sich kümmern …" -> /saga?note=<id>): Auftraege im Gespraech gehen mit
  // context.note an den Server (der prueft das Leserecht). Zeile ueber dem Eingabefeld, mit × wieder weg.
  function noteLine() {
    var f = $('sg-ask'), ln = $('sg-note');
    if (!f) return;
    if (!G.note || G.mode !== 'job') { if (ln) ln.hidden = true; return; }
    if (!ln) { ln = el('p', 'sg-note'); ln.id = 'sg-note'; f.insertBefore(ln, f.firstChild); }
    ln.textContent = ''; ln.hidden = false;
    ln.appendChild(el('span', 'sg-note-t', T('Zur Seite „{title}“', { title: G.note.title || T('Unbenannt') })));
    var x = el('button', 'icon-btn sg-note-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Ohne diese Seite'));
    x.addEventListener('click', function () { G.note = null; noteLine(); try { $('sg-ta').focus(); } catch (e) { /* egal */ } });
    ln.appendChild(x);
  }
  var NOTE_Q = /[?&]note=(\d{1,18})\b/.exec(location.search);      // vor dem ersten replaceState merken (auch ueber das Kennenlernen)
  // 2275: Teilen-Ziel - Schluessel des Service Workers bzw. Rueckfall des Servers (vor dem ersten replaceState merken)
  var SHARE_Q = /[?&]share=([0-9a-f]{32})(?:&|$)/.exec(location.search) || (/[?&]share_failed=1(?:&|$)/.test(location.search) ? 'failed' : null);
  var SHARE_TTL = 10 * 60 * 1000;
  function hostOf(url) {                             // wie der Server (ai_plan.share_host, nur Anzeige): klein, ohne Port und
    var h;                                           // Punkte am Rand, "www." nur weg, wenn danach noch ein Punkt kommt
    try { var u = new URL(url); if (!/^https?:$/.test(u.protocol)) return ''; h = u.hostname; } catch (e) { return ''; }
    h = h.replace(/^\[|\]$/g, '').replace(/^\.+|\.+$/g, '').toLowerCase();
    if (h.indexOf('www.') === 0 && (h.match(/\./g) || []).length >= 2) h = h.slice(4);
    return h.slice(0, 253);
  }
  S.hostOf = hostOf;                                 // fuer den Test (Vergleich mit dem Server)
  function sharedBody(sh) {                          // POST /api/ai/jobs shared (14.1) - leere Felder weglassen
    var b = { from: 'web' };
    if (sh.text) b.text = sh.text;
    if (sh.url) b.url = sh.url;
    if (sh.title) b.title = sh.title;
    return b;
  }
  // Zeile "Geteilt: host ×" ueber dem Feld (nur bei "Als Auftrag"), darunter share_notice (RT-17: vor dem Senden sichtbar)
  function shareLine() {
    var f = $('sg-ask'), ln = $('sg-shared');
    if (!f) return;
    if (!G.share || G.mode !== 'job') { if (ln) ln.hidden = true; return; }
    if (!ln) { ln = el('div', 'sg-shared'); ln.id = 'sg-shared'; f.insertBefore(ln, $('sg-ta')); }
    ln.textContent = ''; ln.hidden = false;
    var row = el('p', 'sg-note');
    row.appendChild(el('span', 'sg-note-t', shareLabel(G.share.host, G.share.title)));
    var x = el('button', 'icon-btn sg-note-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Geteiltes weglassen'));
    x.addEventListener('click', function () { G.share = null; setMode(G.mode); try { $('sg-ta').focus(); } catch (e) { /* egal */ } });
    row.appendChild(x); ln.appendChild(row);
    if (S.st && S.st.share_notice) ln.appendChild(el('p', 'sg-shared-n', S.st.share_notice));
  }
  function shareTake(key) {                          // einmal lesbar: holen und sofort loeschen; aelter als 10 min = weg
    if (!window.caches) return Promise.resolve(null);
    return caches.open('saga-share').then(function (c) {
      var k = '/_share/' + key;
      return c.match(k).then(function (r) {
        return c.delete(k).then(function () { return r ? r.json() : null; });
      });
    }).then(function (d) {
      var now = Date.now();
      if (!d || typeof d !== 'object' || !(+d.at) || now - d.at > SHARE_TTL || d.at > now + 60000) return null;
      var str = function (v, n) { return typeof v === 'string' ? v.slice(0, n) : ''; };
      var url = str(d.url, 2000); if (!/^https?:\/\/[^\s<>"]+$/i.test(url)) url = '';
      var sh = { title: str(d.title, 200).trim(), text: str(d.text, 20000).trim(), url: url };
      sh.host = hostOf(sh.url);
      return sh.title || sh.text || sh.url ? sh : null;
    }).catch(function () { return null; });
  }
  function canJobShare(feat) {                       // F10: "kuemmern" nur mit aishare1, Auftraegen, ai_act und Einwilligung
    return !!(feat && jobsOn() && consentOk() && S.st.caps && S.st.caps.indexOf('ai_act') >= 0);
  }
  function shareCard(sh, err, feat) {
    var old = $('sg-share'); if (old) old.remove();
    var c = el('section', 'sg-card sg-share'); c.id = 'sg-share'; c.setAttribute('aria-label', T('Geteilter Inhalt'));
    var talk = $('sg-talk'); talk.insertBefore(c, talk.firstChild); talk.hidden = false;
    function close() { c.remove(); try { $('sg-ta').focus(); } catch (e) { /* egal */ } }
    if (err) {
      var p = el('p', 'sg-err', err); p.setAttribute('role', 'alert'); c.appendChild(p);
      var bx = el('div', 'sg-share-acts'), cl = el('button', 'btn btn-quiet sg-share-drop', T('Schließen')); cl.type = 'button';
      cl.addEventListener('click', close); bx.appendChild(cl); c.appendChild(bx);
      return c;
    }
    c.appendChild(el('h2', 'sg-share-h', T('Was soll damit passieren?')));
    var pv = el('div', 'sg-share-prev');
    if (sh.title) pv.appendChild(el('p', 'sg-share-ti', sh.title));
    if (sh.host) pv.appendChild(el('p', 'sg-share-host', sh.host));
    if (sh.text) pv.appendChild(el('p', 'sg-share-tx', sh.text.slice(0, 400)));
    c.appendChild(pv);
    var acts = el('div', 'sg-share-acts'), msg = el('p', 'sg-err'); msg.setAttribute('role', 'alert'); msg.hidden = true;
    function save(kind, b) {
      [].forEach.call(acts.querySelectorAll('button'), function (x) { x.disabled = true; });
      api('POST', '/api/capture', { kind: kind, title: sh.title, text: sh.text, url: sh.url }).then(function (r) {
        acts.remove(); msg.remove();
        var d = el('p', 'sg-share-done'); d.setAttribute('role', 'status'); d.tabIndex = -1;
        d.appendChild(document.createTextNode((kind === 'page' ? T('Im Eingang gespeichert.') : T('Aufgabe angelegt.')) + ' '));
        var a = el('a', 'sg-share-open', kind === 'page' ? T('Öffnen') : T('Aufgaben ansehen'));
        a.href = kind === 'page' ? '/notes#' + (+r.id) : '/notes?v=tasks'; d.appendChild(a);
        c.appendChild(d); try { d.focus(); } catch (e) { /* egal */ }
      }).catch(function (e) {
        [].forEach.call(acts.querySelectorAll('button'), function (x) { x.disabled = false; });
        msg.textContent = e.message; msg.hidden = false; if (b) b.focus();
      });
    }
    function btn(label, cls, fn) {
      var b = el('button', 'btn ' + cls, label); b.type = 'button';
      b.addEventListener('click', function () { fn(b); }); acts.appendChild(b); return b;
    }
    btn(T('Als Seite speichern'), 'sg-share-page', function (b) { save('page', b); });
    btn(T('Als Aufgabe speichern'), 'sg-share-task', function (b) { save('task', b); });
    if (canJobShare(feat)) btn(T('{name} soll sich kümmern …', { name: P().name }), 'sg-share-job', function () {
      G.share = sh; c.remove();
      tab('talk', false); setMode('job');
      var ta = $('sg-ta'); try { ta.focus({ preventScroll: true }); } catch (e) { ta.focus(); }
    });
    btn(T('Verwerfen'), 'btn-quiet linkbtn sg-share-drop', close);
    c.appendChild(acts); c.appendChild(msg);
    return c;
  }
  function shareFromUrl() {
    var m = SHARE_Q; SHARE_Q = null;
    if (!m) return;
    try { history.replaceState(null, '', '/saga'); } catch (e) { /* egal */ }
    if (m === 'failed') { shareCard(null, T('Das Teilen hat nicht geklappt – bitte teile den Inhalt noch einmal.')); return; }
    var feat = fetch('/api/version', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      .then(function (v) { return (v.features || []).indexOf('aishare1') >= 0; }).catch(function () { return false; });
    Promise.all([shareTake(m[1]), feat]).then(function (x) {
      if (!x[0]) shareCard(null, T('Der geteilte Inhalt ist nicht mehr da – er lässt sich nur einmal und höchstens 10 Minuten lang öffnen. Bitte teile ihn noch einmal.'));
      else shareCard(x[0], null, x[1]);
    });
  }
  // 2.6 (2270 K-C): /saga?tab=jobs&job=<id> (Mitteilung, Glocke, saga://job/<id>) oeffnet das Detail des Auftrags
  var JOB_Q = /[?&]tab=jobs\b/.test(location.search) && /[?&]job=(\d{1,18})(?:&|$)/.exec(location.search);
  function jobFromUrl() {
    var m = JOB_Q; JOB_Q = null;
    if (m && jobsOn() && G.tab === 'jobs' && G.jobs) G.jobs.open(+m[1], true);
  }
  function noteFromUrl() {
    var m = NOTE_Q; NOTE_Q = null;
    if (!m || !jobsOn()) return;
    api('GET', '/api/notes/' + m[1]).then(function (n) {
      G.note = { id: +m[1], title: (n && n.title) || '' };
      tab('talk', false); setMode('job');
      var ta = $('sg-ta'); try { ta.focus({ preventScroll: true }); } catch (e) { ta.focus(); }
    }).catch(function () { HK.toast(T('Diese Seite kannst du nicht öffnen.')); });
  }
  function submit() { if (G.mode === 'job') giveJob($('sg-ta').value.trim()); else send(); }   // mit G.share auch ohne eigene Worte
  function openJob(id) { tab('jobs', false); if (G.jobs && id != null) G.jobs.open(id, true); }
  S.openJob = function (id) { if (onPage && jobsOn()) openJob(id); };   // Glocke auf derselben Seite (static/live.js, 2270)
  function tab(k, focusTab) {
    if (!jobsOn()) k = 'talk';
    G.tab = k;
    $('sg-talk').hidden = k !== 'talk'; $('sg-jobs').hidden = k !== 'jobs';
    [$('sg-tab-talk'), $('sg-tab-jobs')].forEach(function (b) {
      var on = b.dataset.k === k; b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1; b.classList.toggle('on', on);
      if (on && focusTab) b.focus();
    });
    if (k === 'jobs') {
      if (!G.jobs) G.jobs = HKJobs.mount($('sg-jobs'), { form: false, give: function (text) {
        tab('talk', false); if (!consentOk()) return;      // ohne Einwilligung: nur die Kennenlernen-Karte, nichts ans Modell
        setMode('job'); $('sg-ta').value = text; submit(); } });
      else G.jobs.refresh();
    }
    try { history.replaceState(null, '', k === 'jobs' ? '/saga?tab=jobs' : '/saga'); } catch (e) { /* egal */ }
  }
  function tabsInit() {
    var tb = $('sg-tabs');
    tb.setAttribute('role', 'tablist'); tb.setAttribute('aria-label', T('Saga'));
    [['sg-tab-talk', 'sg-talk'], ['sg-tab-jobs', 'sg-jobs']].forEach(function (x) {
      var b = $(x[0]), pn = $(x[1]);
      b.setAttribute('role', 'tab'); b.setAttribute('aria-controls', x[1]);
      pn.setAttribute('role', 'tabpanel'); pn.setAttribute('aria-labelledby', x[0]);
      b.addEventListener('click', function () { tab(b.dataset.k, false); });
      b.addEventListener('keydown', function (e) {       // Pfeile/Pos1/Ende zwischen den Reitern (APG Tabs)
        if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].indexOf(e.key) < 0) return;
        e.preventDefault();
        var nx = e.key === 'Home' ? 'talk' : e.key === 'End' ? 'jobs' : b.dataset.k === 'talk' ? 'jobs' : 'talk';
        tab(nx, true);
      });
    });
    // 2515: Bausteine im Verlauf, deren Auftrag noch nicht am Ende ist (End-Zustaende und "weg" nie neu holen)
    var ENDS = ['erledigt', 'zurueckgenommen', 'abgebrochen', 'fehlgeschlagen', 'weg'];
    function openJobs() {
      return [].filter.call(document.querySelectorAll('#sg-feed .sg-job[data-job]'), function (w) {
        return ENDS.indexOf(w.dataset.state) < 0 && !w.querySelector('.kj-editbox');
      });
    }
    document.addEventListener('visibilitychange', function () {   // wieder sichtbar - offene Auftraege abgleichen
      if (!document.hidden) openJobs().forEach(refreshJob);
    });
    document.addEventListener('hk-live', function (e) {   // ai.job.changed: Baustein im Verlauf auf den neuen Stand
      var t = e.detail && e.detail.type;
      if (t === 'live.hello' || t === 'resync') {        // 2515: nach (Wieder-)Verbinden einmal neu holen - Ereignisse dazwischen fehlen
        // nur offene Auftraege - End-Zustaende aendern sich nicht von selbst. Auch beim ersten hello: zwischen Laden der Seite
        // und Aufbau der Ereignis-Quelle kann ein freigegebener Auftrag schon fertig sein (sonst fehlt die Rueckmeldung)
        openJobs().forEach(refreshJob);
        return;
      }
      var d = t === 'ai.job.changed' ? (e.detail.data || {}) : null;
      if (!d || !d.id) return;
      [].forEach.call(document.querySelectorAll('#sg-feed .sg-job[data-job="' + d.id + '"]'), function (w) {
        if (w.dataset.state === d.state && d.state !== 'erledigt') return;
        if (w.querySelector('.kj-editbox')) return;      // "Aendern" offen: nicht unter der Hand neu zeichnen
        refreshJob(w);
      });
    });
  }
  function empty() {
    var feed = $('sg-feed'); feed.textContent = ''; feed.hidden = false;
    var w = el('div', 'sg-empty'); w.id = 'sg-empty';
    w.appendChild(av(56));
    w.appendChild(el('p', 'sg-empty-t', T('Sag mir, was du brauchst – zum Beispiel:')));
    var chips = el('div', 'sg-chips');
    [T('Was steht diese Woche an?'), T('Fass meine Notizen zum Sommerfest zusammen'), T('Welche Aufgaben sind offen?')].forEach(function (q) {
      var b = el('button', 'sg-chip', q); b.type = 'button';
      b.addEventListener('click', function () { setMode('ask'); $('sg-ta').value = q; send(); });
      chips.appendChild(b);
    });
    w.appendChild(chips);
    if (jobsOn()) {                                      // UX §3: Beispiel fuer einen Auftrag (zeigt nur den Plan)
      w.appendChild(el('p', 'sg-empty-t', T('Oder gib mir einen Auftrag:')));
      var jc = el('div', 'sg-chips'), jb = el('button', 'sg-chip sg-chip-job', T('Erinnere mich Freitag an den Pavillon')); jb.type = 'button';
      jb.addEventListener('click', function () { setMode('job'); $('sg-ta').value = jb.textContent; submit(); });
      jc.appendChild(jb); w.appendChild(jc);
    }
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
    $('sg-stop').hidden = !on; $('sg-send').disabled = on; $('sg-ta').readOnly = on; $('sg-mode').disabled = on;
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
  // pweb 1: ohne Einwilligung (Widerruf, Endpunkt gewechselt) bleiben vorhandene Auftraege erreichbar - Liste, Detail,
  // Abbrechen, Rueckgaengig (der Server erlaubt das ohne Einwilligung); Gespraech und "Als Auftrag" bleiben gesperrt.
  function jobsNote(text) {
    var box = $('sg-jobs'), n = $('sg-jobs-note');
    if (!n) { n = el('p', 'sg-jobs-note'); n.id = 'sg-jobs-note'; box.insertBefore(n, box.firstChild); }
    n.textContent = text || ''; n.hidden = !text;
  }
  function pausedJobs() {
    if (!jobsOn()) return;
    api('GET', '/api/ai/jobs').then(function (d) {
      var n = 0; Object.keys(d.counts || {}).forEach(function (k) { n += d.counts[k] || 0; });
      if (!n || consentOk()) return;
      var c = (S.st && S.st.consent) || {}, name = P().name;
      jobsNote(c.state === 'withdrawn' ? T('Deine Aufträge sind angehalten, weil du die Einwilligung widerrufen hast. Ansehen, abbrechen und rückgängig machen geht weiter – neue Aufträge erst, wenn du {name} wieder kennenlernst.', { name: name })
        : c.state === 'stale' ? T('Der Admin hat die KI umgestellt – deine Aufträge warten, bis du {name} noch einmal kennenlernst.', { name: name })
        : T('Neue Aufträge gibst du, sobald du {name} kennengelernt hast.', { name: name }));
      $('sg-tabs').hidden = false;
      if (/[?&]tab=jobs\b/.test(location.search)) { tab('jobs', false); jobFromUrl(); }
    }).catch(function () { /* keine Liste: nur die Karte */ });
  }
  function page() {
    var st = S.st;
    var avBox = $('sg-av'); avBox.textContent = ''; avBox.appendChild(av(36));
    $('sg-title').textContent = kiName();
    bar();
    var slot = $('sg-intro'), form = $('sg-ask');
    if (!st) {                                         // 2275: zum Teilen auch ohne KI hier (nur Seite / Aufgabe)
      slot.hidden = false; slot.textContent = ''; form.hidden = true; $('sg-title').textContent = T('Saga');
      slot.appendChild(el('p', SHARE_Q ? 'hk-hint' : 'sg-err', T('Der KI-Assistent ist für dich nicht eingeschaltet.')));
      return;
    }
    $('sg-mode').hidden = !jobsOn(); setMode(G.mode);
    if (!consentOk()) {                                // vor der Einwilligung: nur die Karte, keine Eingabe (nichts ans Modell)
      form.hidden = true; $('sg-feed').hidden = true; $('sg-keep').hidden = true; $('sg-tabs').hidden = true; tab('talk', false);
      slot.textContent = '';
      slot.appendChild(S.introCard({ done: function () {        // Bugs-DB 2503: danach steht der Fokus im Eingabefeld
          S.load().then(function () { page(); var ta = $('sg-ta'); if (!$('sg-ask').hidden) try { ta.focus({ preventScroll: true }); } catch (e) { ta.focus(); } });
        }, reload: function () { page(); },
        later: function () { location.href = '/dashboard'; } }));
      slot.hidden = false;
      pausedJobs();
      return;
    }
    slot.hidden = true; slot.textContent = ''; jobsNote('');
    form.hidden = false;
    $('sg-tabs').hidden = !jobsOn();
    tab(jobsOn() && /[?&]tab=jobs\b/.test(location.search) ? 'jobs' : G.tab, false);
    jobFromUrl();
    noteFromUrl();
    api('GET', '/api/ai/thread').then(function (d) {
      G.turns = d.turns || []; renderFeed(); keepLine(d);
    }).catch(function (e) { HK.toast(e.message); });
  }
  function pageInit() {
    $('sg-ask').addEventListener('submit', function (e) { e.preventDefault(); submit(); });
    $('sg-ta').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    });
    $('sg-mode').addEventListener('click', function () { setMode(G.mode === 'job' ? 'ask' : 'job'); });
    tabsInit();
    $('sg-stop').addEventListener('click', function () { if (G.run) G.run.abort(); });
    moreMenu();
    // 2515: "Verlauf an/aus" unter dem Gespraech folgt der Einstellung (Einstellungen › Saga auf derselben Seite)
    document.addEventListener('saga:prefs', function () {
      if (!consentOk() || $('sg-ask').hidden) return;
      api('GET', '/api/ai/thread').then(keepLine).catch(function () { /* naechstes Laden */ });
    });
    S.load().then(page).catch(function (e) { HK.toast(e.message); }).then(shareFromUrl);
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
    if (d.history_allowed) box.appendChild(row(T('Verlauf speichern'), check(d.history, function (v, i) {
      put({ history: v }, i).then(function (r) { emit('saga:prefs', { prefs: r }); }).catch(function () { /* Hinweis kam schon */ });   // 2515
    }),
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
