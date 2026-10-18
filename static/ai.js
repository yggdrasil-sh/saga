// Saga - KI-Assistent (1.6): Seitenleiste rechts (Handy: Bottom-Sheet) mit "Frag deine Seiten" und Hilfen fuer die offene
// Seite (Zusammenfassen, Aufgaben vorschlagen, markierten Text verbessern/uebersetzen). Der Assistent aendert nie etwas:
// Ergebnisse sind Vorschlaege, die Person uebernimmt sie per Klick (normale Endpunkte bzw. Editor). Antworten kommen als
// Server-Sent Events ueber POST (fetch + ReadableStream). Alles per textContent; Links nur auf Quellen-Seiten (/notes#id),
// nie aus dem Text des Modells. Erscheint nur, wenn /api/ai/status "enabled" meldet.
(function () {
  'use strict';
  var HK = window.HK, $ = HK.$, el = HK.el, api = HK.api;
  if (!$('pg-editor') || document.body.classList.contains('embed')) return;
  var K = window.HKPages ? window.HKPages.k : null;
  var RANK = { view: 1, comment: 2, edit: 3, owner: 4 };
  var A = { st: null, run: null, sel: null, tab: 'ask', chat: [] };
  var TARGET_NAMES = { de: 'Deutsch', en: 'English', es: 'Español', fr: 'Français', it: 'Italiano', nl: 'Nederlands', pt: 'Português',
    pl: 'Polski', sv: 'Svenska', da: 'Dansk', nb: 'Norsk', fi: 'Suomi', cs: 'Čeština', tr: 'Türkçe', uk: 'Українська', ja: '日本語', zh: '中文' };

  function cur() { return K ? K.cur() : null; }
  // Persona (2.1): Name und Profilbild der KI aus dem Status (Admin-Bereich -> KI-Assistent -> Persona)
  function P() { return (A.st && A.st.persona) || { name: T('Saga KI'), avatar: null }; }
  function av(px, cls) { return window.HKAIAvatar ? HKAIAvatar(P(), px, cls) : el('span', 'ai-spark', '✦'); }
  function who() {                                     // Kopf einer Antwort: Bild + Name
    var w = el('div', 'ai-who'); w.appendChild(av(22)); w.appendChild(el('span', 'ai-who-n', P().name)); return w;
  }
  function canEdit() { var c = cur(); return !!c && RANK[c.role || 'owner'] >= RANK.edit && !c.locked; }

  // --- Antwort als Stream: POST + text/event-stream --------------------------------------------------------
  function stream(url, body, on) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var p = fetch(url, { method: 'POST', credentials: 'same-origin', signal: ctrl ? ctrl.signal : undefined,
                         headers: { 'X-Saga': '1', 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify(body) })
      .then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok || ct.indexOf('text/event-stream') !== 0) {
          return r.json().catch(function () { return {}; }).then(function (d) {
            var e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; throw e;
          });
        }
        var rd = r.body.getReader(), dec = new TextDecoder(), buf = '';
        function emit(chunk) {
          var ev = null, data = '';
          chunk.split('\n').forEach(function (ln) {
            if (ln.indexOf('event:') === 0) ev = ln.slice(6).trim();
            else if (ln.indexOf('data:') === 0) data += ln.slice(5).trim();
          });
          if (ev) { try { on(ev, JSON.parse(data || 'null')); } catch (e) { /* kaputte Zeile */ } }
        }
        function pump() {
          return rd.read().then(function (x) {
            if (x.done) { if (buf.trim()) emit(buf); return; }
            buf += dec.decode(x.value, { stream: true });
            var i;
            while ((i = buf.indexOf('\n\n')) >= 0) { emit(buf.slice(0, i)); buf = buf.slice(i + 2); }
            return pump();
          });
        }
        return pump();
      });
    return { done: p, abort: function () { if (ctrl) ctrl.abort(); } };
  }

  // --- Seitenleiste ------------------------------------------------------------------------------------------
  var panel = el('aside', 'ai-panel'); panel.id = 'ai-panel'; panel.hidden = true;
  panel.setAttribute('role', 'complementary'); panel.setAttribute('aria-label', T('KI-Assistent'));
  var head = el('div', 'ai-head'), grip = el('span', 'ai-grip'); grip.setAttribute('aria-hidden', 'true');
  var htitle = el('h2', 'ai-title');
  function paintHead() {
    htitle.textContent = ''; htitle.appendChild(av(28));
    var tx = el('span', 'ai-title-t'); tx.appendChild(el('span', 'ai-title-n', P().name)); tx.appendChild(el('span', 'ai-title-s', T('KI-Assistent')));
    htitle.appendChild(tx);
  }
  paintHead();
  var close = el('button', 'icon-btn ai-close', '×'); close.type = 'button'; close.setAttribute('aria-label', T('Assistent schließen'));
  head.appendChild(grip); head.appendChild(htitle); head.appendChild(close);
  var tabs = el('div', 'ai-tabs'); tabs.setAttribute('role', 'tablist');
  var tAsk = tabBtn('ask', T('Frag deine Seiten')), tPage = tabBtn('page', T('Diese Seite'));
  tabs.appendChild(tAsk); tabs.appendChild(tPage);
  var body = el('div', 'ai-body'), foot = el('div', 'ai-foot');
  var notice = el('p', 'ai-notice'), quota = el('span', 'ai-quota');
  panel.appendChild(head); panel.appendChild(tabs); panel.appendChild(body); panel.appendChild(foot);
  var scrim = el('div', 'ai-scrim'); scrim.hidden = true;
  document.body.appendChild(scrim); document.body.appendChild(panel);
  close.addEventListener('click', hide);
  scrim.addEventListener('click', hide);
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || panel.hidden || e.defaultPrevented) return;
    if (document.querySelector('.hk-ask') || HK.menuOpen() || !$('pg-modal').hidden) return;
    e.preventDefault(); e.stopPropagation(); hide();
  }, true);

  function tabBtn(k, label) {
    var b = el('button', 'ai-tab', label); b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.k = k;
    b.addEventListener('click', function () { A.tab = k; render(); });
    return b;
  }

  var narrow = window.matchMedia('(max-width: 760px)');
  function show(tab) {
    if (!A.st) return;
    if (window.HKComments && document.body.classList.contains('cm-open')) { var cx = document.querySelector('.cm-close'); if (cx) cx.click(); }
    if (tab) A.tab = tab;
    var hd = document.querySelector('.np-head'), top = hd && !narrow.matches ? Math.max(0, Math.round(hd.getBoundingClientRect().bottom)) : 0;
    panel.style.top = narrow.matches ? '' : top + 'px';
    panel.hidden = false; scrim.hidden = !narrow.matches; document.body.classList.add('ai-open');
    if (btn) btn.setAttribute('aria-expanded', 'true');
    render();
    var f = panel.querySelector('textarea, .ai-act'); if (f && !narrow.matches) f.focus();
  }
  function hide() {
    panel.hidden = true; scrim.hidden = true; document.body.classList.remove('ai-open');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  function renderFoot() {
    foot.textContent = '';
    notice.textContent = A.st ? A.st.notice : '';
    foot.appendChild(notice);
    var q = A.st && A.st.quota ? T('Heute noch {n} von {m} Anfragen', { n: Math.max(0, A.st.quota - A.st.used), m: A.st.quota }) : '';
    quota.textContent = q; quota.hidden = !q;
    foot.appendChild(quota);
  }

  function render() {
    [tAsk, tPage].forEach(function (b) {
      var on = b.dataset.k === A.tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    var pg = cur(), remote = !!pg && typeof pg.id === 'number' && pg.id < 0;   // Seite einer anderen Saga-Instanz: Inhalt liegt dort
    tPage.disabled = !pg || remote;
    tPage.title = remote ? T('Seiten anderer Saga-Instanzen bearbeitet der Assistent nicht.') : '';
    if ((!pg || remote) && A.tab === 'page') A.tab = 'ask';
    body.textContent = '';
    if (A.tab === 'ask') renderAsk(); else renderPage();
    renderFoot();
  }

  // --- Antworttext -> DOM (nie innerHTML): Absaetze, Listen ('- ', '* ', '1. '), **fett**, `code`, Ueberschriften als
  // fette Zeile, Quellen [n] / [1, 2] / [1][2] -> Links auf die Quellseiten (cited sammelt die zitierten Nummern) -------
  function inline(p, s, byN, cited) {
    var re = /(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[\d{1,2}(?:\s*[,;]\s*\d{1,2})*\])/g, last = 0, m;
    while ((m = re.exec(s))) {
      if (m.index > last) p.appendChild(document.createTextNode(s.slice(last, m.index)));
      var x = m[0];
      if (x.charAt(0) === '[') {
        x.slice(1, -1).split(/\s*[,;]\s*/).forEach(function (n, k) {
          var src = byN[+n];
          if (k) p.appendChild(document.createTextNode(' '));
          if (src && src.id) {
            var a = el('a', 'ai-cite', '[' + n + ']'); a.href = '/notes#' + src.id; a.title = src.title || '';
            p.appendChild(a); if (cited) cited[+n] = 1;
          } else if (src && src.url && /^https?:\/\//i.test(src.url)) {      // Webquelle (2.1): Link nach aussen
            var w = el('a', 'ai-cite', '[' + n + ']'); w.href = src.url; w.target = '_blank'; w.rel = 'noopener noreferrer';
            w.title = (src.title || '') + (src.host ? ' – ' + src.host : '');
            p.appendChild(w); if (cited) cited[+n] = 1;
          } else p.appendChild(document.createTextNode('[' + n + ']'));
        });
      } else if (x.charAt(0) === '`') p.appendChild(el('code', null, x.slice(1, -1)));
      else p.appendChild(el('strong', null, x.slice(2, -2)));
      last = re.lastIndex;
    }
    if (last < s.length) p.appendChild(document.createTextNode(s.slice(last)));
  }
  function answerNode(text, sources, cls, cited) {
    var w = el('div', cls || 'ai-answer'), byN = {}, list = null;
    (sources || []).forEach(function (s) { byN[s.n] = s; });
    String(text || '').split('\n').forEach(function (ln) {
      var m = /^\s*(?:[-*•]|(\d{1,2})[.)])\s+(.*)$/.exec(ln);
      if (m) {
        var tag = m[1] ? 'OL' : 'UL';
        if (!list || list.tagName !== tag) { list = el(tag.toLowerCase(), 'ai-list'); w.appendChild(list); }
        var li = el('li'); inline(li, m[2], byN, cited); list.appendChild(li);
        return;
      }
      list = null;
      if (!ln.trim()) return;
      var h = /^\s*#{1,6}\s+(.*)$/.exec(ln);
      var p = el('p', h ? 'ai-p ai-h' : 'ai-p'); inline(p, h ? h[1] : ln, byN, cited); w.appendChild(p);
    });
    return w;
  }
  function sourcesNode(sources, cited) {
    var w = el('div', 'ai-srcs');
    if (!sources || !sources.length) { w.appendChild(el('span', 'ai-srcs-none', T('Keine passenden Seiten gefunden.'))); return w; }
    w.appendChild(el('span', 'ai-srcs-l', T('Quellen:')));
    var any = cited && Object.keys(cited).length;
    sources = sources.slice().sort(function (a, b) { return any ? (cited[b.n] ? 1 : 0) - (cited[a.n] ? 1 : 0) || a.n - b.n : a.n - b.n; });
    sources.forEach(function (s) {
      var link = s.id || (s.url && /^https?:\/\//i.test(s.url));
      var a = el(link ? 'a' : 'span', 'ai-src' + (s.url ? ' ai-src-web' : '') + (any && !cited[s.n] ? ' ai-src-uncited' : ''));
      if (s.id) a.href = '/notes#' + s.id;
      else if (link) { a.href = s.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
      if (any && !cited[s.n]) a.title = T('durchsucht, aber nicht zitiert');
      a.appendChild(el('span', 'ai-src-n', String(s.n)));
      a.appendChild(el('span', 'ai-src-t', s.title || T('Unbenannt')));
      if (s.owner) a.appendChild(el('span', 'ai-src-o', s.owner));
      if (s.url && s.host) a.appendChild(el('span', 'ai-src-o', '↗ ' + s.host));
      w.appendChild(a);
    });
    return w;
  }
  function errNode(msg) { var e = el('p', 'ai-err', msg); e.setAttribute('role', 'alert'); return e; }

  // --- Frag deine Seiten ------------------------------------------------------------------------------------------
  var askTa = el('textarea', 'hk-in ai-ta'); askTa.rows = 2; askTa.setAttribute('aria-label', T('Frage an deine Seiten'));
  function askPh() { askTa.placeholder = T('Frage {name} etwas zu deinen Seiten …', { name: P().name }); }
  askPh();
  var optTasks = switchBox(T('Aufgaben einbeziehen')), optCal = switchBox(T('Kalender einbeziehen')), optWeb = switchBox(T('Im Web suchen'));
  optWeb.input.checked = true;                       // Standard an, wenn der Admin die Websuche erlaubt (2.1)
  optWeb.title = T('Bei Fragen nach Aktuellem (Wetter, Nachrichten …) darf die KI im Web suchen. Suchbegriffe gehen über die Websuche an Suchmaschinen.');
  var sendB = el('button', 'btn btn-primary ai-send', T('Fragen')); sendB.type = 'button';
  var stopB = el('button', 'btn ai-stop', T('Stopp')); stopB.type = 'button'; stopB.hidden = true;
  askTa.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendAsk(); }
  });
  askTa.addEventListener('input', function () { sendB.disabled = !askTa.value.trim() || !!A.run; });
  sendB.addEventListener('click', sendAsk);
  stopB.addEventListener('click', function () { if (A.run) A.run.abort(); });

  function switchBox(label) {
    var l = el('label', 'ai-sw'), i = el('input', 'hk-switch'); i.type = 'checkbox'; i.setAttribute('role', 'switch');
    l.appendChild(i); l.appendChild(el('span', null, label)); l.input = i; return l;
  }

  var chatBox = el('div', 'ai-chat');
  function renderAsk() {
    body.appendChild(chatBox);
    if (!A.chat.length) {
      chatBox.textContent = '';
      var e = el('div', 'ai-empty');
      var hi = el('div', 'ai-hello'); hi.appendChild(av(56)); hi.appendChild(el('p', 'ai-hello-n', P().name)); e.appendChild(hi);
      e.appendChild(el('p', null, T('Frag etwas – {name} sucht in den Seiten, die du sehen darfst, und nennt die Quellen.', { name: P().name })));
      e.appendChild(el('p', 'ai-hint', T('{name} kann nichts ändern: Vorschläge übernimmst du selbst. Chats liest {name} nur, wenn du im Chat mit @{name} fragst; Aufgaben und Kalender nur, wenn du sie einschaltest.', { name: P().name })));
      if (A.st && A.st.web) e.appendChild(el('p', 'ai-hint', T('Bei Fragen nach Aktuellem (Wetter, Nachrichten …) sucht {name} im Web, wenn „Im Web suchen“ an ist: Suchbegriffe gehen über die Websuche an Suchmaschinen.', { name: P().name })));
      chatBox.appendChild(e);
    }
    var form = el('div', 'ai-form');
    var opts = el('div', 'ai-opts'); opts.appendChild(optTasks); opts.appendChild(optCal);
    if (A.st && A.st.web) opts.appendChild(optWeb);
    var bar = el('div', 'ai-bar');
    if (A.st && A.st.history_allowed) {
      var hb = el('button', 'linkbtn ai-hist-b', T('Verlauf')); hb.type = 'button';
      hb.addEventListener('click', openHistory); bar.appendChild(hb);
    }
    bar.appendChild(el('span', 'spacer'));
    bar.appendChild(stopB); bar.appendChild(sendB);
    form.appendChild(askTa); form.appendChild(opts); form.appendChild(bar);
    body.appendChild(form);
    sendB.disabled = !askTa.value.trim() || !!A.run;
  }

  function sendAsk() {
    var q = askTa.value.trim();
    if (!q || A.run) return;
    if (A.chat.length === 0) chatBox.textContent = '';
    var item = el('div', 'ai-item'), qn = el('p', 'ai-q', q), ans = el('div', 'ai-answer ai-pending');
    ans.appendChild(el('span', 'ai-typing', T('{name} denkt nach …', { name: P().name })));
    item.appendChild(qn); item.appendChild(who()); item.appendChild(ans); chatBox.appendChild(item);
    A.chat.push(item);
    askTa.value = ''; item.scrollIntoView({ block: 'end' });
    var text = '', srcs = [], srcNode = null, fin = false, pend = false, info = {};
    busy(true);
    function paint() {                                   // hoechstens einmal je Bild neu zeichnen (lange Antworten, Handys)
      pend = false; if (fin) return;
      var n = answerNode(text, srcs); n.classList.add('ai-live'); item.replaceChild(n, ans); ans = n;
      chatBox.scrollTop = chatBox.scrollHeight;
    }
    var rq = { q: q, tasks: optTasks.input.checked, calendar: optCal.input.checked };
    if (A.st && A.st.web) rq.web = optWeb.input.checked;
    A.run = stream('/api/ai/ask', rq, function (ev, d) {
      if (ev === 'sources') {                            // nach einer Websuche ein zweites Mal: Seiten + Webquellen
        srcs = d.sources || [];
        var sn0 = srcs.length ? sourcesNode(srcs) : null;
        if (srcNode && srcNode.parentNode) { if (sn0) item.replaceChild(sn0, srcNode); else item.removeChild(srcNode); }
        else if (sn0) item.appendChild(sn0);            // gleich zeigen, was durchsucht wird
        srcNode = sn0;
      } else if (ev === 'status') {
        var ty = ans.querySelector('.ai-typing');
        if (ty && d.thinking) ty.textContent = T('Das Modell denkt nach …');
        else if (ty && d.web === 'searching') ty.textContent = T('{name} sucht im Web …', { name: P().name });
        else if (ty && d.web) ty.textContent = T('{name} denkt nach …', { name: P().name });
      } else if (ev === 'delta' || ev === 'reset') {     // reset: das bisher Gezeigte war die Denkphase des Modells
        text = ev === 'reset' ? '' : text + (d.t || '');
        if (!pend) { pend = true; (window.requestAnimationFrame || setTimeout)(paint); }
      } else if (ev === 'done') {
        info = d || {};
        if (d.quota_left != null && A.st.quota) A.st.used = A.st.quota - d.quota_left;
      } else if (ev === 'error') {
        if (d.quota_left != null && A.st.quota) A.st.used = A.st.quota - d.quota_left;
        item.appendChild(errNode(d.error || T('Fehler')));
      }
    });
    A.run.done.then(function () {
      fin = true;
      var cited = {};
      if (text) { var n = answerNode(text, srcs, null, cited); item.replaceChild(n, ans); ans = n; }
      else if (ans.parentNode) item.removeChild(ans);                 // nur Fehler: kein leerer Antwortkasten
      if (info.cut) item.insertBefore(el('p', 'ai-hint', T('Die Antwort wurde abgeschnitten (Längengrenze des Modells).')), ans.nextSibling);
      if (info.notice) item.insertBefore(el('p', 'ai-hint', info.notice), ans.nextSibling);   // z. B. Websuche nicht erreichbar
      var sn = info.no_match ? null : sourcesNode(srcs, cited);
      if (srcNode && srcNode.parentNode) { if (sn) item.replaceChild(sn, srcNode); else item.removeChild(srcNode); }
      else if (sn) item.appendChild(sn);
      srcNode = sn;
    }).catch(function (err) {
      fin = true;
      ans.classList.remove('ai-pending', 'ai-live');
      if (err && err.name === 'AbortError') { ans.appendChild(el('span', 'ai-stopped', ' ' + T('(abgebrochen)'))); return; }
      if (ans.querySelector('.ai-typing')) item.removeChild(ans);
      item.appendChild(errNode(err.message));
    }).then(function () { busy(false); renderFoot(); });
  }
  function busy(on) {
    if (!on) A.run = null;
    stopB.hidden = !on; sendB.disabled = on || !askTa.value.trim();
    [].forEach.call(panel.querySelectorAll('.ai-act'), function (b) { b.disabled = on; });
  }

  // --- Verlauf -----------------------------------------------------------------------------------------------------
  function openHistory() {
    api('GET', '/api/ai/history').then(function (d) {
      var m = $('pg-modal'), bx = $('pg-modal-body');
      $('pg-modal-title').textContent = T('Verlauf des Assistenten');
      bx.textContent = '';
      var top = el('div', 'ai-h-top'), sw = switchBox(T('Verlauf speichern'));
      sw.input.checked = !!d.history;
      sw.input.addEventListener('change', function () {
        var on = sw.input.checked;
        var go = on ? Promise.resolve(true) : HK.ask({ title: T('Verlauf ausschalten?'), text: T('Der gespeicherte Verlauf wird dabei gelöscht.'), ok: T('Ausschalten'), danger: true });
        go.then(function (yes) {
          if (!yes) { sw.input.checked = true; return; }
          api('PUT', '/api/ai/settings', { history: on }).then(function () { A.st.history = on; openHistory(); })
            .catch(function (err) { sw.input.checked = !on; HK.toast(err.message); });
        });
      });
      top.appendChild(sw);
      if (d.items.length) {
        var clr = el('button', 'linkbtn danger', T('Alles löschen')); clr.type = 'button';
        clr.addEventListener('click', function () {
          HK.ask({ title: T('Verlauf löschen?'), text: T('Alle gespeicherten Fragen und Antworten werden gelöscht.'), ok: T('Löschen'), danger: true }).then(function (yes) {
            if (yes) api('DELETE', '/api/ai/history').then(openHistory).catch(function (err) { HK.toast(err.message); });
          });
        });
        top.appendChild(clr);
      }
      bx.appendChild(top);
      bx.appendChild(el('p', 'ai-hint', d.history ? T('Gespeichert werden Fragen, Antworten und die Quellseiten. Quellen ohne Zugriff zeigen keinen Titel mehr.')
        : T('Aus: Fragen und Antworten werden nicht gespeichert.')));
      var list = el('div', 'ai-h-list');
      if (!d.items.length) list.appendChild(el('p', 'ai-hint', T('Noch nichts gespeichert.')));
      d.items.forEach(function (it) {
        var w = el('section', 'ai-h-item');
        var hh = el('div', 'ai-h-head');
        hh.appendChild(el('span', 'ai-h-time', HK.relTime(it.created - Date.now() / 1000)));
        var del = el('button', 'icon-btn ai-h-del', '×'); del.type = 'button'; del.setAttribute('aria-label', T('Eintrag löschen'));
        del.addEventListener('click', function () { api('DELETE', '/api/ai/history/' + it.id).then(function () { w.remove(); }).catch(function (err) { HK.toast(err.message); }); });
        hh.appendChild(del);
        w.appendChild(hh); w.appendChild(el('p', 'ai-q', it.q)); w.appendChild(who()); w.appendChild(answerNode(it.answer, it.sources)); w.appendChild(sourcesNode(it.sources));
        list.appendChild(w);
      });
      bx.appendChild(list);
      m.hidden = false;
    }).catch(function (err) { HK.toast(T('Verlauf nicht geladen: {err}', { err: err.message })); });
  }

  // --- Markierung im Editor merken (Klick in die Seitenleiste hebt sie sonst auf) -----------------------------------
  function blockIds() {
    var box = $('pg-editor'); if (!box) return [];
    var picked = [].slice.call(box.querySelectorAll('.ce-block--selected[data-id]'));
    if (!picked.length) {
      var sel = window.getSelection();
      if (!sel || !sel.rangeCount) return [];
      var r = sel.getRangeAt(0), n = r.commonAncestorContainer;
      if (!box.contains(n.nodeType === 1 ? n : n.parentNode)) return [];
      if (r.collapsed) {
        var one = (n.nodeType === 1 ? n : n.parentNode).closest('.ce-block[data-id]');
        picked = one ? [one] : [];
      } else {
        picked = [].filter.call(box.querySelectorAll('.ce-block[data-id]'), function (b) { return r.intersectsNode(b); });
      }
    }
    // Nur innerste Bloecke (Spalten/Toggle enthalten weitere Bloecke)
    picked = picked.filter(function (b) { return !picked.some(function (o) { return o !== b && b.contains(o); }); });
    return picked.map(function (b) { return b.getAttribute('data-id'); }).slice(0, (A.st && A.st.blocks_max) || 50);
  }
  function remember() {
    var c = cur(); if (!c) return;
    var ids = blockIds();
    if (ids.length) { A.sel = { page: c.id, ids: ids }; if (!panel.hidden && A.tab === 'page') selInfo(); }
  }
  document.addEventListener('selectionchange', function () { clearTimeout(A.selT); A.selT = setTimeout(remember, 120); });
  document.addEventListener('mouseup', function (e) { if ($('pg-editor').contains(e.target)) setTimeout(remember, 0); });
  document.addEventListener('hk-page-open', function () { A.sel = null; A.result = null; if (!panel.hidden) render(); });

  // --- Diese Seite ---------------------------------------------------------------------------------------------------
  var selLine = el('p', 'ai-sel'), resBox = el('div', 'ai-res');
  var target = el('select', 'hk-in ai-target'); target.setAttribute('aria-label', T('Zielsprache'));
  function selInfo() {
    var c = cur(), n = A.sel && c && A.sel.page === c.id ? A.sel.ids.length : 0;
    selLine.textContent = n ? (n === 1 ? T('Markiert: 1 Block') : T('Markiert: {n} Blöcke', { n: n }))
      : T('Für „Verbessern“ und „Übersetzen“ zuerst Text auf der Seite markieren.');
    selLine.classList.toggle('on', !!n);
    [].forEach.call(panel.querySelectorAll('.ai-act[data-sel]'), function (b) { b.disabled = !n || !!A.run; });
  }
  function act(label, icon, fn, needSel) {
    var b = el('button', 'btn ai-act'); b.type = 'button';
    b.appendChild(el('span', 'ai-act-ic', icon)); b.appendChild(el('span', null, label));
    if (needSel) b.dataset.sel = '1';
    b.addEventListener('click', fn); b.disabled = !!A.run;
    return b;
  }
  function renderPage() {
    var c = cur();
    if (!c) { body.appendChild(el('p', 'ai-hint', T('Keine Seite geöffnet.'))); return; }
    var w = el('div', 'ai-page');
    w.appendChild(el('p', 'ai-ptitle', (c.icon ? c.icon + ' ' : '') + (c.title || T('Unbenannt'))));
    var g = el('div', 'ai-acts');
    g.appendChild(act(T('Zusammenfassen'), '≡', function () { runPage('summarize'); }));
    g.appendChild(act(T('Aufgaben vorschlagen'), '☐', function () { runPage('tasks'); }));
    g.appendChild(act(T('Markierten Text verbessern'), '✎', function () { runPage('improve'); }, true));
    var tr = el('div', 'ai-tr');
    if (!target.options.length) {
      var langs = (A.st && A.st.targets) || ['en', 'de', 'es'];
      var first = HK.lang === 'en' ? 'de' : 'en';
      [first].concat(langs.filter(function (x) { return x !== first; })).forEach(function (k) {
        var o = el('option', null, TARGET_NAMES[k] || k); o.value = k; target.appendChild(o);
      });
    }
    tr.appendChild(act(T('Übersetzen nach'), '⇄', function () { runPage('translate'); }, true)); tr.appendChild(target);
    g.appendChild(tr);
    w.appendChild(g); w.appendChild(selLine); w.appendChild(resBox);
    body.appendChild(w);
    selInfo();
  }

  function runPage(action, idsOpt) {
    var c = cur(); if (!c || A.run) return;
    if (c.id < 0) { resBox.textContent = ''; resBox.appendChild(errNode(T('Der Assistent arbeitet nur mit Seiten dieses Servers.'))); return; }   // BUG-052
    var ids = idsOpt || (A.sel && A.sel.page === c.id ? A.sel.ids : []);
    if ((action === 'improve' || action === 'translate') && !ids.length) { selInfo(); return; }
    resBox.textContent = '';
    var out = el('div', 'ai-answer ai-pending'); out.appendChild(el('span', 'ai-typing', T('{name} denkt nach …', { name: P().name })));
    resBox.appendChild(who()); resBox.appendChild(out);
    var text = '', done = null, pid = c.id;
    busy(true);
    var saved = canEdit() && window.HKPages && HKPages.flush ? HKPages.flush() : Promise.resolve();   // markierte Bloecke muessen gespeichert sein
    saved.catch(function () { /* Konflikt: der Server nimmt den gespeicherten Stand */ }).then(function () {
      var rich = action === 'summarize' || action === 'tasks';           // Verbessern/Uebersetzen: Text genau so zeigen
      A.run = stream('/api/ai/page/' + pid, { action: action, blocks: ids, target: target.value }, function (ev, d) {
        if (ev === 'delta' || ev === 'reset') {
          text = ev === 'reset' ? '' : text + (d.t || '');
          var n = rich ? answerNode(text, []) : el('div', 'ai-answer', text); n.classList.add('ai-live'); resBox.replaceChild(n, out); out = n;
        } else if (ev === 'status') {
          var ty = out.querySelector('.ai-typing'); if (ty && d.thinking) ty.textContent = T('Das Modell denkt nach …');
        } else if (ev === 'done') { done = d; if (d.quota_left != null && A.st.quota) A.st.used = A.st.quota - d.quota_left; }
        else if (ev === 'error') {
          if (d.quota_left != null && A.st.quota) A.st.used = A.st.quota - d.quota_left;
          resBox.appendChild(errNode(d.error || T('Fehler')));
        }
      });
      return A.run.done;
    }).then(function () {
      out.classList.remove('ai-live', 'ai-pending');
      if (!done || !cur() || cur().id !== pid) return;
      if (done.truncated) resBox.insertBefore(el('p', 'ai-hint', T('Die Seite ist lang – verwendet wurden Anfang und Ende.')), out);
      if (done.cut) resBox.insertBefore(el('p', 'ai-hint', T('Die Antwort wurde abgeschnitten (Längengrenze des Modells).')), out);
      if (done.notice) resBox.insertBefore(el('p', 'ai-hint', done.notice), out);     // z. B. nur Bild markiert -> ganze Seite
      if (action === 'tasks') taskSuggest(done.tasks || [], out, done.message);
      else if (action === 'improve' || action === 'translate') {
        if (typeof done.text === 'string') { text = done.text; out.textContent = text; }   // ohne Einleitung des Modells
        textSuggest(text, done, out);
      }
      else resBox.appendChild(copyBar(text));
    }).catch(function (err) {
      out.classList.remove('ai-pending');
      if (err && err.name === 'AbortError') return;
      if (out.querySelector('.ai-typing')) out.remove();
      resBox.appendChild(errNode(err.message));
    }).then(function () { busy(false); renderFoot(); selInfo(); });
  }

  function copyBar(text, extra) {
    var bar = el('div', 'ai-rbar');
    (extra || []).forEach(function (b) { bar.appendChild(b); });
    var cp = el('button', 'btn', T('Kopieren')); cp.type = 'button';
    cp.addEventListener('click', function () {
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject()).then(function () { HK.toast(T('Kopiert.')); })
        .catch(function () { HK.toast(T('Kopieren nicht möglich')); });
    });
    bar.appendChild(cp);
    return bar;
  }

  // Aufgaben: Auswahl -> in "Aufgaben" uebernehmen (POST /api/tasks je Eintrag) oder als Checkliste unter die Seite
  function taskSuggest(items, out, msg) {
    out.textContent = '';
    if (!items.length) { out.appendChild(el('p', 'ai-hint', msg || T('Keine Aufgaben gefunden.'))); return; }
    out.classList.add('ai-tasks');
    var boxes = items.map(function (txt) {
      var l = el('label', 'ai-task'), i = el('input'); i.type = 'checkbox'; i.checked = true;
      l.appendChild(i); l.appendChild(el('span', null, txt)); out.appendChild(l); return { i: i, t: txt };
    });
    function chosen() { return boxes.filter(function (b) { return b.i.checked; }).map(function (b) { return b.t; }); }
    var take = el('button', 'btn btn-primary', T('In Aufgaben übernehmen')); take.type = 'button';
    take.addEventListener('click', function () {
      var list = chosen(); if (!list.length) return;
      take.disabled = true;
      list.reduce(function (p, txt) { return p.then(function () { return api('POST', '/api/tasks', { text: txt }); }); }, Promise.resolve())
        .then(function () { HK.toast(list.length === 1 ? T('1 Aufgabe übernommen.') : T('{n} Aufgaben übernommen.', { n: list.length })); })
        .catch(function (err) { HK.toast(T('Nicht übernommen: {err}', { err: err.message })); take.disabled = false; });
    });
    var extra = [take];
    if (canEdit()) {
      var ins = el('button', 'btn', T('Als Checkliste einfügen')); ins.type = 'button';
      ins.addEventListener('click', function () {
        var list = chosen(); if (!list.length) return;
        insertBlock('list', { style: 'checklist', meta: {}, items: list.map(function (x) { return { content: esc(x), meta: { checked: false }, items: [] }; }) });
      });
      extra.push(ins);
    }
    resBox.appendChild(copyBar(items.map(function (x) { return '- ' + x; }).join('\n'), extra));
  }

  // Verbessern/Uebersetzen: Ersetzen (nach Rueckfrage, nur Text-Bloecke), darunter einfuegen, kopieren
  function textSuggest(text, done, out) {
    var extra = [];
    if (canEdit()) {
      var segs = done.segments, blocks = done.blocks || [];
      if (segs && segs.length === blocks.length && blocks.every(function (b) { return b.replaceable; })) {
        var rep = el('button', 'btn btn-primary', blocks.length === 1 ? T('Ersetzen') : T('{n} Blöcke ersetzen', { n: blocks.length })); rep.type = 'button';
        rep.addEventListener('click', function () {
          HK.ask({ title: T('Markierten Text ersetzen?'), text: T('Der Text der markierten Blöcke wird durch den Vorschlag ersetzt (Formatierungen gehen dabei verloren). Frühere Fassungen findest du im Versionsverlauf.'), ok: T('Ersetzen') })
            .then(function (yes) { if (yes) replaceBlocks(blocks, segs); });
        });
        extra.push(rep);
      }
      var below = el('button', 'btn', T('Darunter einfügen')); below.type = 'button';
      below.addEventListener('click', function () {
        var lastId = blocks.length ? blocks[blocks.length - 1].id : null;
        insertBlock('paragraph', { text: esc(text.replace(/\n-{3,}\n/g, '\n\n')).replace(/\n/g, '<br>') }, lastId);
      });
      extra.push(below);
    }
    resBox.appendChild(copyBar(text, extra));
  }

  function esc(s) { return HK.escapeHtml(String(s || '')); }
  function editors() { return K ? [K.editor()].concat(K.eds ? K.eds() : []).filter(Boolean) : []; }
  function findBlock(id) {
    var eds = editors();
    for (var i = 0; i < eds.length; i++) {
      try { var b = eds[i].blocks.getById(id); if (b) return { ed: eds[i], blk: b }; } catch (e) { /* anderer Editor */ }
    }
    return null;
  }
  function replaceBlocks(blocks, segs) {
    var jobs = blocks.map(function (b, i) {
      var f = findBlock(b.id); if (!f) return Promise.resolve(false);
      return f.blk.save().then(function (s) {
        var d = Object.assign({}, s && s.data, { text: esc(segs[i]).replace(/\n/g, '<br>') });
        return f.ed.blocks.update(b.id, d);
      }).then(function () { return true; });
    });
    Promise.all(jobs).then(function (r) {
      var n = r.filter(Boolean).length;
      HK.toast(n === blocks.length ? T('Ersetzt.') : T('Nur teilweise ersetzt – Seite inzwischen geändert?'));
    }).catch(function () { HK.toast(T('Nicht ersetzt.')); });
  }
  function insertBlock(type, data, afterId) {
    var f = afterId ? findBlock(afterId) : null, ed = f ? f.ed : (K && K.editor());
    if (!ed) return;
    var idx = f ? ed.blocks.getBlockIndex(afterId) + 1 : ed.blocks.getBlocksCount();
    try { ed.blocks.insert(type, data, undefined, idx, true); HK.toast(T('Eingefügt.')); } catch (e) { HK.toast(T('Nicht eingefügt.')); }
  }

  // --- Knopf im Kopf + Eintraege im Seitenmenue -----------------------------------------------------------------------
  var btn = null;
  function headBtn() {
    if (btn) return;
    btn = el('button', 'btn ai-btn'); btn.type = 'button'; btn.id = 'ai-btn';
    btn.setAttribute('aria-controls', 'ai-panel'); btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', function () { if (panel.hidden) show(); else hide(); });
    paintBtn();
    var u = $('hk-user'); if (u && u.parentNode) u.parentNode.insertBefore(btn, u);
  }
  function paintBtn() {                                // "Frage Marin" mit kleinem Profilbild (schmal: nur das Bild)
    if (!btn) return;
    var lbl = T('Frage {name}', { name: P().name });
    btn.textContent = ''; btn.title = lbl; btn.setAttribute('aria-label', lbl);
    btn.appendChild(av(20)); btn.appendChild(el('span', 'ai-btn-t', lbl));
  }
  // Admin aendert Name/Bild oder schaltet die KI ein/aus (Live-Ereignis ai.persona.changed): Status neu laden
  function refresh() {
    return api('GET', '/api/ai/status').then(function (d) {
      if (!d || !d.enabled) {
        A.st = null; hide();
        if (btn) { btn.remove(); btn = null; }
        return;
      }
      A.st = d; headBtn(); paintBtn(); paintHead(); askPh();
      if (!panel.hidden) render();
    }).catch(function () { /* naechstes Ereignis */ });
  }
  document.addEventListener('hk-live', function (e) {
    if (e.detail && e.detail.type === 'ai.persona.changed') refresh();
  });
  window.HKAI = {
    // static/pages.js: Untermenue im Seitenmenue ("…"), nur wenn der Assistent an ist
    menu: function (m) {
      if (!A.st) return;
      m.appendChild(HK.menuItem(T('Frage {name}', { name: P().name }), null, { icon: '✦', sub: function (sm) {
        sm.appendChild(HK.menuItem(T('Zusammenfassen'), function () { show('page'); runPage('summarize'); }, { icon: '≡' }));
        sm.appendChild(HK.menuItem(T('Aufgaben vorschlagen'), function () { show('page'); runPage('tasks'); }, { icon: '☐' }));
        sm.appendChild(HK.menuItem(T('Markierten Text verbessern'), function () { show('page'); runPage('improve'); }, { icon: '✎' }));
        sm.appendChild(HK.menuItem(T('Frag deine Seiten'), function () { show('ask'); }, { icon: '?' }));
      } }));
    },
    open: function (tab) { show(tab); },
    state: function () { return A.st; }
  };

  api('GET', '/api/ai/status').then(function (d) {
    if (!d || !d.enabled) return;
    A.st = d; headBtn(); paintHead(); askPh();
  }).catch(function () { /* kein Assistent */ });
})();
