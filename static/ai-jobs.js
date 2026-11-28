// Saga 2.6 (Bugs-DB 2267 K-B) - Auftraege der Kollegin im Web: Plan-Karte (einziges Freigabe-Muster "Passt, los ·
// Aendern · Lieber nicht"), Rueckmeldung (Ergebnis zuerst + Rueckgaengig je Aenderung) und der Ort "Auftraege" (Liste in
// 4 Gruppen, Detail mit genauem Zustandswort und Protokoll). Eigenstaendiges Bauteil: window.HKJobs (Einbau-Schnittstelle
// in /home/claude/docs/kb-notizen.md bzw. docs/APP-API-KI.md "Auftraege"). Server: /api/ai/jobs/... (saga/ai_jobs.py).
// Alles per textContent; KI-Text (Entwuerfe) ueber die KI-Markdown-Darstellung (HKAI.md, 2248). Zustandswoerter,
// Klassen und Fehlerarten kommen als Systemtext vom Server. Neue Karten nehmen nie den Fokus; Statuswechsel ueber
// role=status (hoeflich); Enter gibt nie frei.
(function () {
  'use strict';
  var HK = window.HK;
  if (!HK) return;
  var el = HK.el, api = HK.api;
  var KIND_SYM = { read: '◎', own: '✎', out: '↗' };

  // Persona/Darstellung: im KI-Bereich der Seiten aus static/ai.js (HKAI), im Ort "Saga" aus static/saga.js (HKSaga)
  function persona() {
    var st = window.HKAI && HKAI.state ? HKAI.state() : null;
    if (!st && window.HKSaga) st = HKSaga.st;
    return (st && st.persona) || { name: T('Saga KI'), avatar: null };
  }
  function head(stateText) {                     // B1 KI-Kopf: Bild · "Marin · KI" · Zustandswort
    var h = el('div', 'kj-head'), p = persona();
    if (window.HKAIAvatar) h.appendChild(HKAIAvatar(p, 24)); else { var s = el('span', 'kj-spark', '✦'); s.setAttribute('aria-hidden', 'true'); h.appendChild(s); }
    var nm = String(p.name || '');                // Standardname "Saga KI" traegt die KI-Marke schon (kein "Saga KI · KI")
    var lb = window.HKSaga && HKSaga.kiName ? HKSaga.kiName() : null;   // Server-Kennzeichnung (K-H style.label)
    h.appendChild(el('span', 'kj-who', lb || (/(^|\s)(KI|AI|IA|ИИ)$/.test(nm) ? nm : T('{name} · KI', { name: nm }))));
    if (stateText) { var w = el('span', 'kj-state', stateText); h.appendChild(w); }
    return h;
  }
  function md(text) {                            // KI-Markdown-Teilmenge (2248) - sonst Klartext
    if (window.HKAI && HKAI.md) return HKAI.md(text);
    if (window.HKSaga && HKSaga.md) return HKSaga.md(text);
    var d = el('div', 'kj-plain'); d.textContent = text; return d;
  }
  function report(text, id) {                    // "Melden" (2258a) - KI-Bereich oder Ort "Saga"
    if (window.HKAI && HKAI.reportBtn) return HKAI.reportBtn(text, 'job', id);
    if (window.HKSaga && HKSaga.reportBtn) return HKSaga.reportBtn(text, 'job', id);
    return null;
  }
  function btn(label, cls, fn) {
    var b = el('button', 'btn ' + (cls || ''), label); b.type = 'button';
    if (fn) b.addEventListener('click', fn);
    return b;
  }
  function say(live, text) { if (live) { live.textContent = ''; live.textContent = text; } }
  // 2.6 (Bugs-DB 2275 K-G, APP-API-KI 14.1): Herkunft eines Plans aus geteiltem Inhalt - Texte fertig vom Server
  // (origin.text, bei warn ⚠ + warn_text: Symbol UND Text, nie nur Farbe). origin null = kein geteilter Inhalt.
  // 2.6 (Bugs-DB 2274 K-F, APP-API-KI 15.5): kind "dictation" - Mikrofon-Symbol + "Diktiert", bei warn ⚠ + warn_text (in der
  // Anrede der Person, fertig vom Server). Unbekannte Arten: nur text.
  function originNodes(o) {
    var out = [];
    if (!o || typeof o !== 'object' || !o.text) return out;
    var p = el('p', 'kj-origin');
    if (o.kind === 'dictation') {
      p.classList.add('kj-origin-dict');
      var ic = (HK.icon && HK.icon('mic', 15)) || el('span', null, '🎙');
      ic.setAttribute('aria-hidden', 'true'); ic.classList.add('kj-origin-sym'); p.appendChild(ic);
    }
    p.appendChild(document.createTextNode(o.text));
    out.push(p);
    if (o.warn && o.warn_text) {
      var w = el('p', 'kj-warn'), sym = el('span', 'kj-warn-sym', '⚠'); sym.setAttribute('aria-hidden', 'true');
      w.appendChild(sym); w.appendChild(el('span', 'kj-warn-t', o.warn_text)); out.push(w);
    }
    return out;
  }

  // --- SSE: POST /api/ai/jobs ---------------------------------------------------------------------------------------
  function post(body, on) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var p = fetch('/api/ai/jobs', { method: 'POST', credentials: 'same-origin', signal: ctrl ? ctrl.signal : undefined,
      headers: { 'X-Saga': '1', 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify(body) })
      .then(function (r) {
        var ct = r.headers.get('content-type') || '';
        if (!r.ok || ct.indexOf('text/event-stream') !== 0) {
          return r.json().catch(function () { return {}; }).then(function (d) { on('error', { error: d.error || ('HTTP ' + r.status), status: r.status }); });
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

  // --- B3 Plan-Karte ------------------------------------------------------------------------------------------------
  // planCard(plan, opts) -> Element. plan = Ereignis "plan" bzw. detail.plan; opts.onDone(state, job) nach Freigabe/Abbruch,
  // opts.live = Element mit role=status fuer Ansagen.
  function planCard(plan, opts) {
    opts = opts || {};
    var card = el('section', 'kj-card kj-plan'); card.setAttribute('aria-label', T('Plan: {title}', { title: plan.title || '' }));
    card.dataset.job = plan.job;
    var cur = plan, editing = false;
    function paint() {
      card.textContent = '';
      card.appendChild(head(T('Wartet auf dein OK')));
      if (cur.edited) { var ed = el('p', 'kj-note', T('geändert – bitte neu freigeben')); card.appendChild(ed); }
      var lead = el('p', 'kj-lead', cur.say || T('So würde ich es machen:')); lead.tabIndex = -1; card.appendChild(lead);
      var ol = el('ol', 'kj-steps'), n = cur.steps.length;
      cur.steps.forEach(function (s, i) {
        var li = el('li', 'kj-step kj-' + s.klass);
        li.setAttribute('aria-label', T('Schritt {i} von {n}, {kind}, {cls}: {text}', { i: i + 1, n: n, kind: s.kind_text, cls: s.class_text, text: s.text }));
        var sym = el('span', 'kj-sym', KIND_SYM[s.klass] || '•'); sym.setAttribute('aria-hidden', 'true'); li.appendChild(sym);
        var tx = el('span', 'kj-step-t', s.text); li.appendChild(tx);
        li.appendChild(el('span', 'kj-cls', s.class_text));
        if (editing && s.edit && s.edit.length) li.appendChild(editBox(s, i));
        else if (s.wording) { var box = el('div', 'kj-draft'); box.appendChild(md(s.wording)); li.appendChild(box); }
        ol.appendChild(li);
      });
      card.appendChild(ol);
      if (cur.read_text) card.appendChild(el('p', 'kj-read', cur.read_text));
      originNodes(cur.origin).forEach(function (x) { card.appendChild(x); });   // 2275: unter "Ich lese dafür: …"
      var err = el('p', 'kj-err'); err.setAttribute('role', 'alert'); err.hidden = true; card.appendChild(err);
      var bar = el('div', 'kj-actions');
      if (editing) {
        bar.appendChild(btn(T('Speichern'), 'btn-primary kj-save', function () { saveEdit(err); }));
        bar.appendChild(btn(T('Abbrechen'), 'btn-quiet', function () { editing = false; paint(); focusIn('.kj-edit'); }));
      } else {
        var go = btn(T('Passt, los'), 'btn-primary kj-go', function () { approve(err, go); });
        go.addEventListener('keydown', function (e) { if (e.key === 'Enter') e.preventDefault(); });   // Enter gibt nie frei
        bar.appendChild(go);
        if (cur.steps.some(function (s) { return s.edit && s.edit.length; })) bar.appendChild(btn(T('Ändern'), 'kj-edit', function () { editing = true; paint(); focusIn('.kj-editbox input, .kj-editbox textarea'); }));
        var no = btn(T('Lieber nicht'), 'btn-quiet kj-no', function () { cancel(err); });
        bar.appendChild(no);
      }
      card.appendChild(bar);
      var rb = report(planText(), cur.job); if (rb) card.appendChild(rb);
    }
    function focusIn(sel) { var x = card.querySelector(sel); if (x) { if (x.tabIndex < 0 && !x.hasAttribute('tabindex')) x.tabIndex = -1; try { x.focus(); } catch (e) { /* egal */ } } }   // nur nach eigener Aktion
    function planText() { return cur.steps.map(function (s) { return s.text + (s.wording ? '\n' + s.wording : ''); }).join('\n'); }
    var inputs = {};
    function editBox(s, i) {
      var w = el('div', 'kj-editbox'); inputs[i] = {};
      var src = s.fields || {};
      s.edit.forEach(function (k) {
        var lab = el('label', 'kj-field'), name = { text: T('Text'), due: T('Fällig am'), markdown: T('Wortlaut'), summary: T('Titel'),
          start: T('Beginn'), end: T('Ende'), location: T('Ort'), title: T('Titel'), allday: T('ganztägig') }[k] || k;
        lab.appendChild(el('span', null, name));
        var inp;
        if (k === 'markdown') { inp = el('textarea', 'hk-in'); inp.rows = 3; }
        else if (k === 'allday') { inp = el('input'); inp.type = 'checkbox'; }
        else {                                     // ganztaegig (JJJJ-MM-TT): Datumsfeld - datetime-local wuerde den Wert leeren
          inp = el('input', 'hk-in');
          inp.type = k === 'due' ? 'date' : (k === 'start' || k === 'end') ? (String(src[k] || src.start || '').length === 10 ? 'date' : 'datetime-local') : 'text';
        }
        var v = src[k] != null ? src[k] : (k === 'markdown' ? (s.wording || '') : '');
        if (k === 'allday') inp.checked = !!v; else inp.value = v;
        lab.appendChild(inp); w.appendChild(lab); inputs[i][k] = inp;
      });
      return w;
    }
    function saveEdit(err) {
      var edits = [];
      Object.keys(inputs).forEach(function (i) {
        var a = {};
        Object.keys(inputs[i]).forEach(function (k) { var x = inputs[i][k]; a[k] = x.type === 'checkbox' ? x.checked : x.value.trim(); });
        edits.push({ n: +i, args: a });
      });
      api('PUT', '/api/ai/jobs/' + cur.job + '/plan', { plan_v: cur.plan_v, edits: edits }).then(function (d) {
        cur = d; editing = false; inputs = {}; paint(); focusIn('.kj-lead');
        say(opts.live, T('geändert – bitte neu freigeben'));
        if (opts.onEdit) opts.onEdit(cur.job);
      }).catch(function (e) {
        if (e.status === 409) { editing = false; inputs = {}; resync(e, err); return; }
        err.hidden = false; err.textContent = e.message;
      });
    }
    // Bugs-DB 2515: 409 nach "Passt, los"/"Speichern"/"Lieber nicht" - Stand neu holen. stale (Plan anderswo geaendert):
    // den neuen Plan zeigen, aber nie still - "geaendert – bitte neu freigeben" (plan.edited) bleibt stehen, die Meldung
    // darunter auch. state (anderswo freigegeben/abgebrochen): keine Plan-Karte mit aktivem "Passt, los" stehen lassen,
    // sondern den jetzigen Stand (opts.onState, sonst Zustandskarte an Ort und Stelle).
    function resync(e, err) {
      var code = e.data && e.data.code, msg = e.message;
      return load(cur.job).then(function (d) {
        if (!d) { err.hidden = false; err.textContent = msg; return; }
        if (d.state === 'wartet_ok' && d.plan && d.plan.steps) {
          cur = d.plan;
          if (code === 'stale' && !cur.edited) cur = Object.assign({}, cur, { edited: true });   // der Plan ist nicht mehr der gesehene
          paint();
          var e2 = card.querySelector('.kj-err'); if (e2 && msg) { e2.hidden = false; e2.textContent = msg; }
          focusIn('.kj-lead');
          say(opts.live, T('geändert – bitte neu freigeben'));
          return;
        }
        if (opts.onState) { opts.onState(d, card); return; }
        card.textContent = ''; card.classList.add('kj-small');
        card.appendChild(head(d.state_text));
        var lead = el('p', 'kj-lead', d.title || ''); card.appendChild(lead);
        if (d.reason_text) card.appendChild(el('p', 'kj-note', d.reason_text));
        if (opts.onOpen) card.appendChild(btn(T('Ansehen'), 'btn-quiet kj-view', function () { opts.onOpen(cur.job, true); }));
        focusIn('.kj-lead');
        say(opts.live, d.state_text);
        if (opts.onDone) opts.onDone(d.state, cur.job, card);
      });
    }
    function approve(err, go) {
      if (!navigator.onLine) { err.hidden = false; err.textContent = T('Offline – gespeicherter Stand. Freigeben geht, sobald du wieder verbunden bist.'); return; }
      go.disabled = true;
      api('POST', '/api/ai/jobs/' + cur.job + '/approve', { plan_v: cur.plan_v, plan_hash: cur.plan_hash }).then(function (d) {
        card.textContent = ''; card.classList.add('kj-small');
        card.appendChild(head(d.state_text));
        card.appendChild(el('p', 'kj-lead', d.say || ''));
        if (opts.onOpen) card.appendChild(btn(T('Ansehen'), 'btn-quiet kj-view', function () { opts.onOpen(cur.job, true); }));
        focusIn('.kj-lead');                       // der Knopf ist weg: Fokus nicht auf <body> fallen lassen
        say(opts.live, d.state_text);
        if (opts.onDone) opts.onDone('laeuft', cur.job, card);
      }).catch(function (e) {
        go.disabled = false; err.hidden = false; err.textContent = e.message;
        if (e.status === 409) resync(e, err);
      });
    }
    function cancel(err) {
      api('POST', '/api/ai/jobs/' + cur.job + '/cancel', {}).then(function (d) {
        card.textContent = ''; card.classList.add('kj-small');
        // pweb 3: wie die Zustandskarte nach dem Neuladen - Leitzeile = Titel, darunter der Grund
        card.appendChild(head(d.state_text)); card.appendChild(el('p', 'kj-lead', cur.title || d.text || ''));
        if (cur.title && d.text) card.appendChild(el('p', 'kj-note', d.text));
        if (opts.onOpen) card.appendChild(btn(T('Ansehen'), 'btn-quiet kj-view', function () { opts.onOpen(cur.job, true); }));
        focusIn('.kj-lead');
        say(opts.live, d.text || d.state_text);
        if (opts.onDone) opts.onDone('abgebrochen', cur.job, card);
      }).catch(function (e) { if (e.status === 409) { resync(e, err); return; } err.hidden = false; err.textContent = e.message; });
    }
    paint();
    return card;
  }

  function load(id) {
    return api('GET', '/api/ai/jobs/' + id).catch(function () { return null; });
  }
  function when(ts) { return new Date(ts * 1000).toLocaleString(HK.locale || undefined); }

  // --- B4 Rueckmeldung ----------------------------------------------------------------------------------------------
  function feedback(d, opts) {
    opts = opts || {};
    var card = el('section', 'kj-card kj-report'); card.setAttribute('aria-label', d.state_text);
    card.appendChild(head(d.state_text));
    var rep = d.report || { text: d.reason_text || d.state_text, lines: [] };
    var res = el('div', 'kj-result'); res.appendChild(md(rep.text || '')); card.appendChild(res);
    var ul = el('ul', 'kj-changes'), lines = rep.lines || [], one = lines.length === 1;
    lines.forEach(function (ln) {
      // Bugs-DB 2504: das Ergebnis steht schon im Satz oben - je Aenderung nur der Gegenstand (bei einer Aenderung gar
      // nichts) neben "Rueckgaengig"; der Screenreader hoert am Knopf, worum es geht.
      var li = el('li', 'kj-change' + (one ? ' kj-change-one' : '')); li.dataset.change = ln.change;
      if (!one) li.appendChild(el('span', 'kj-change-t', ln.label || ln.text));
      var ch = (d.changes || []).filter(function (c) { return c.id === ln.change; })[0] || {};
      if (ln.undone) li.appendChild(el('span', 'kj-cls', T('Zurückgenommen')));
      else if (ln.can_undo) li.appendChild(undoBtn(d.id, ln.change, ch.undo_class, li, opts, ln.text));
      else if (ch.why === 'changed') li.appendChild(changedNote(ch.version_url, ch.why_text));
      else if (ch.why_text) li.appendChild(el('span', 'kj-cls kj-why', ch.why_text));   // 2515: gone/no_undo/done in Klartext
      if (li.childNodes.length) ul.appendChild(li);
    });
    if (ul.childNodes.length) card.appendChild(ul);
    if (opts.onOpen) card.appendChild(btn(T('Ansehen'), 'btn-quiet kj-view', function () { opts.onOpen(d.id, true); }));
    var rb = rep.text ? report(rep.text, d.id) : null; if (rb) card.appendChild(rb);
    card.dataset.job = d.id;
    return card;
  }
  function changedNote(url, text) {                // "Alte Fassung ansehen" nur mit Weg (2515: Aufgaben/Termine haben keinen)
    var w = el('div', 'kj-changed');
    w.appendChild(el('span', null, text || T('Das wurde seitdem geändert.')));
    if (typeof url === 'string' && url) {
      var b = btn(T('Alte Fassung ansehen'), 'btn-quiet kj-old', function () {
        api('GET', url).then(function (v) {
          var pre = el('div', 'kj-oldtext'); pre.setAttribute('role', 'region'); pre.setAttribute('aria-label', T('Alte Fassung'));
          if (v.title) pre.appendChild(el('strong', null, v.title));
          pre.appendChild(el('pre', null, v.text || ''));
          if (b.nextSibling && b.nextSibling.classList && b.nextSibling.classList.contains('kj-oldtext')) b.parentNode.removeChild(b.nextSibling);
          b.parentNode.insertBefore(pre, b.nextSibling);
        }).catch(function (e) { HK.toast(e.message); });
      });
      w.appendChild(b);
    }
    return w;
  }
  function undoBtn(job, change, undoClass, li, opts, what) {
    var b = btn(T('Rückgängig'), 'kj-undo', function () {
      var go = function () {
        b.disabled = true;
        api('POST', '/api/ai/jobs/' + job + '/undo', { change: change }).then(function (r) {
          var done = el('span', 'kj-cls', r && r.already && r.text ? r.text : T('Zurückgenommen'));
          done.tabIndex = -1; b.replaceWith(done); try { done.focus(); } catch (e) { /* egal */ }   // Fokus nicht auf <body>
          HK.toast(r && r.already && r.text ? r.text : T('Zurückgenommen'));
          if (opts.onChange) opts.onChange(job);
        }).catch(function (e) {
          b.disabled = false;
          if (e.status === 409 && e.data && e.data.code === 'changed') b.replaceWith(changedNote(e.data.version_url, e.data.why_text));
          else HK.toast(e.message);
        });
      };
      if (undoClass === 'delete_only' && HK.ask) {
        HK.ask({ title: T('Wieder löschen?'), text: T('Saga hat das neu angelegt – Rückgängig löscht es wieder.'), ok: T('Löschen'),
                 cancel: T('Behalten'), danger: true }).then(function (yes) { if (yes) go(); });
      } else go();
    });
    if (what) b.setAttribute('aria-label', T('Rückgängig: {what}', { what: what }));
    return b;
  }

  // --- Rueckfrage und Absage (auch fuer den Ort "Saga": derselbe Baustein, Bugs-DB 2269/K1) ---------------------------
  // askCardOf(d, onPick(choice)); refuseCardOf(d, onAlt(alternative), onRephrase())
  function askCardOf(d, onPick) {
    var c = el('div', 'kj-card kj-ask'); c.appendChild(head(''));
    c.appendChild(el('p', 'kj-lead', d.question));
    var chips = el('div', 'kj-chips');
    (d.choices || []).forEach(function (ch) { chips.appendChild(btn(ch, 'kj-chip', function () { onPick(ch); })); });
    c.appendChild(chips); return c;
  }
  function refuseCardOf(d, onAlt, onRephrase) {
    var c = el('div', 'kj-card kj-refuse'); c.appendChild(head(''));
    c.appendChild(el('p', 'kj-lead', d.text || ''));
    var row = el('div', 'kj-actions');
    if (d.alternative && onAlt) row.appendChild(btn(d.alternative.label || T('Als Aufgabe anlegen'), 'btn-primary', function () { onAlt(d.alternative); }));
    if (onRephrase) row.appendChild(btn(T('Anders formulieren'), 'btn-quiet', onRephrase));
    if (row.childNodes.length) c.appendChild(row);
    return c;
  }

  // --- Ort "Auftraege": Eingabe, Liste in 4 Gruppen, Detail ---------------------------------------------------------
  // mount(container, opts) -> {el, refresh, open(id), give(text)}; opts.note = aktuelle Seite (Kontext), opts.examples.
  // opts.form === false (Ort "Saga", Reiter "Auftraege"): ohne eigene Eingabe - Auftraege gibt man im Gespraech;
  // opts.give(text) nimmt dann den Beispiel-Chip des leeren Zustands entgegen.
  function mount(container, opts) {
    opts = opts || {};
    var root = el('div', 'kj-root'); root.setAttribute('aria-label', T('Aufträge'));
    var live = el('p', 'kj-live'); live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
    var form = el('div', 'kj-form'), ta = el('textarea', 'hk-in kj-ta');
    ta.rows = 2; ta.maxLength = 1000; ta.setAttribute('aria-label', T('Auftrag an {name}', { name: persona().name }));
    ta.placeholder = T('Sag {name}, was du brauchst …', { name: persona().name });
    var send = btn(T('Auftrag geben'), 'btn-primary kj-send', function () { give(ta.value); });
    var stop = btn(T('Stopp'), 'kj-stop'); stop.hidden = true;
    ta.addEventListener('keydown', function (e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); give(ta.value); } });
    ta.addEventListener('input', function () { send.disabled = !ta.value.trim() || !!run; });
    var bar = el('div', 'kj-bar'); bar.appendChild(stop); bar.appendChild(send);
    form.appendChild(ta); form.appendChild(bar);
    var stage = el('div', 'kj-stage'), listEl = el('div', 'kj-list'), detailEl = el('div', 'kj-detail');
    detailEl.hidden = true;
    if (opts.form === false) form.hidden = true;
    root.appendChild(form); root.appendChild(live); root.appendChild(stage); root.appendChild(listEl); root.appendChild(detailEl);
    container.appendChild(root);
    var run = null, openId = null, watch = {};
    send.disabled = true;

    function give(text, extra) {
      text = String(text || '').trim();
      if ((!text && !extra) || run) return;
      stage.textContent = '';
      var wait = el('div', 'kj-card kj-wait'); wait.appendChild(head(''));
      var dots = el('p', 'kj-thinking', T('{name} denkt nach …', { name: persona().name })); wait.appendChild(dots);
      stage.appendChild(wait);
      var body = Object.assign({ text: text }, extra || {});
      if (opts.note && !body.context) body.context = { note: opts.note() };
      if (!body.context || body.context.note == null) delete body.context;
      run = post(body, function (ev, d) {
        if (ev === 'status') { if (d && d.text) dots.textContent = d.text; return; }
        stage.textContent = '';
        if (ev === 'plan') { stage.appendChild(planCard(d, { live: live, onDone: onDone, onEdit: refresh })); say(live, T('Plan bereit – wartet auf dein OK')); ta.value = ''; refresh(); }
        else if (ev === 'ask') stage.appendChild(askCard(d, text));
        else if (ev === 'refuse') stage.appendChild(refuseCard(d, text));
        else { var e = el('div', 'kj-card'); e.appendChild(head('')); var p = el('p', 'kj-err', (d && d.error) || T('Das hat nicht geklappt.')); p.setAttribute('role', 'alert'); e.appendChild(p); stage.appendChild(e); }
      });
      stop.hidden = false; send.disabled = true;
      stop.onclick = function () { if (run) run.abort(); };
      run.done.catch(function (e) {
        if (e && e.name === 'AbortError') { stage.textContent = ''; return; }
        stage.textContent = ''; var p = el('p', 'kj-err', T('Das hat nicht geklappt.')); p.setAttribute('role', 'alert'); stage.appendChild(p);
      }).then(function () { run = null; stop.hidden = true; send.disabled = !ta.value.trim(); });
    }
    function askCard(d, text) { return askCardOf(d, function (ch) { give(text, { choice: ch }); }); }
    function refuseCard(d, text) {
      return refuseCardOf(d, function (alt) { give(text || ' ', { recipe: alt.recipe, fields: alt.fields }); },
        function () { stage.textContent = ''; ta.value = text; try { ta.focus(); } catch (e) { /* egal */ } send.disabled = !ta.value.trim(); });
    }
    function onDone(state, job) { if (state === 'laeuft') watch[job] = 1; refresh(); }

    function refresh() {
      return api('GET', '/api/ai/jobs').then(function (d) {
        listEl.textContent = '';
        var total = 0;
        d.groups.forEach(function (g) { total += g.items.length; });
        if (!total) {
          var e = el('div', 'kj-empty');
          e.appendChild(el('p', null, T('Noch keine Aufträge. Gib mir einen, z. B. ‚Erinnere mich Freitag an den Pavillon‘.')));
          e.appendChild(btn(T('Erinnere mich Freitag an den Pavillon'), 'kj-chip', function () {
            if (opts.form === false && opts.give) { opts.give(T('Erinnere mich Freitag an den Pavillon')); return; }
            ta.value = T('Erinnere mich Freitag an den Pavillon'); send.disabled = false; give(ta.value);
          }));
          listEl.appendChild(e); return;
        }
        d.groups.forEach(function (g) {
          if (!g.items.length) return;
          var sec = el('section', 'kj-group kj-g-' + g.key), h = el('h3', 'kj-gh');
          h.appendChild(document.createTextNode(g.label + ' '));
          h.appendChild(el('span', 'kj-count', String(g.items.length)));
          sec.appendChild(h);
          var ul = el('ul', 'kj-items');
          g.items.forEach(function (it) {
            var li = el('li'), b = el('button', 'kj-item kj-s-' + it.state); b.type = 'button';
            // ✓ nur fuer "Erledigt" - Abgebrochen/Zurueckgenommen sind auch "Fertig", aber nicht geschafft (Bugs-DB 2504)
            var sym = el('span', 'kj-isym', g.key === 'nicht_geklappt' ? '!' : g.key === 'laeuft' ? '◷' : it.state === 'erledigt' ? '✓'
              : it.state === 'zurueckgenommen' ? '↶' : it.state === 'abgebrochen' ? '–' : '●');
            sym.setAttribute('aria-hidden', 'true'); b.appendChild(sym);
            var tx = el('span', 'kj-itx'); tx.appendChild(el('span', 'kj-ititle', it.title || T('Auftrag')));
            var ln = !it.line || it.line === it.state_text ? it.state_text : it.line.indexOf(it.state_text) === 0 ? it.line : it.state_text + ' · ' + it.line;
            tx.appendChild(el('span', 'kj-iline', ln));
            b.appendChild(tx); var ch = el('span', 'kj-chev', '›'); ch.setAttribute('aria-hidden', 'true'); b.appendChild(ch);
            b.dataset.job = it.id; b.addEventListener('click', function () { open(it.id, true); });
            li.appendChild(b); ul.appendChild(li);
          });
          sec.appendChild(ul); listEl.appendChild(sec);
        });
      }).catch(function () { /* naechstes Ereignis */ });
    }

    // Stand des offenen Details (2515 Pruefer): neu gezeichnet wird nur, wenn sich daran etwas geaendert hat - sonst
    // klappt z. B. eine geoeffnete "Alte Fassung" bei jedem live.hello/resync zu
    var shownSig = null;
    function sig(d) {
      return JSON.stringify([d.id, d.state, d.sub, d.updated, d.edited, d.plan && d.plan.plan_v, (d.changes || []).map(function (c) {
        return [c.id, c.undone, c.can_undo, c.why]; }), (d.events || []).length]);
    }
    function open(id, focusIt) {
      openId = id;
      return load(id).then(function (d) { if (d && openId === id) draw(id, d, focusIt); });
    }
    function reopen() {                              // Abgleich (Live-Ereignis, hello, resync): nur bei neuem Stand neu zeichnen
      var id = openId;
      if (id == null || detailEl.querySelector('.kj-editbox')) return;
      load(id).then(function (d) { if (d && openId === id && sig(d) !== shownSig) draw(id, d, false); });
    }
    function draw(id, d, focusIt) {     // (Rumpf eingerueckt wie frueher im then von open)
        var keep = focusIt || detailEl.contains(document.activeElement);   // Neuzeichnen ersetzt das fokussierte Element
        // aufgeklappte "Alte Fassung" und Fokus in einer Aenderungszeile ueber das Neuzeichnen retten
        var olds = {}, act = document.activeElement, actLi = act && detailEl.contains(act) && act.closest ? act.closest('.kj-change') : null;
        var actK = actLi && act.classList.length ? [actLi.dataset.change, act.classList[act.classList.length - 1]] : null;
        [].forEach.call(detailEl.querySelectorAll('.kj-change .kj-oldtext'), function (o) { olds[o.closest('.kj-change').dataset.change] = o; });
        shownSig = sig(d);
        detailEl.textContent = ''; detailEl.hidden = false; listEl.hidden = true; form.hidden = true; stage.hidden = true;
        var back = btn('‹ ' + T('Aufträge'), 'btn-quiet kj-back', close); detailEl.appendChild(back);
        var h = el('h3', 'kj-dtitle', d.title || T('Auftrag')); h.tabIndex = -1; detailEl.appendChild(h);
        var stw = el('p', 'kj-dstate'); stw.appendChild(el('strong', null, d.state_text));
        if (d.reason_text) stw.appendChild(document.createTextNode(' – ' + d.reason_text));
        detailEl.appendChild(stw);
        if (d.state === 'wartet_ok' && d.plan && d.plan.steps) {
          detailEl.appendChild(planCard(d.plan, { live: live, onDone: function (s, job) { onDone(s, job); open(job, true); } }));
        } else {
          if (d.state === 'erledigt' || d.state === 'zurueckgenommen') detailEl.appendChild(feedback(d, { onChange: function () { open(id); } }));
          var sp = el('section', 'kj-sec'); sp.appendChild(el('h4', null, T('Plan')));
          var ol = el('ol', 'kj-steps');
          (d.plan.steps || []).forEach(function (s) {
            var li = el('li', 'kj-step kj-' + s.klass); var sym = el('span', 'kj-sym', KIND_SYM[s.klass] || '•'); sym.setAttribute('aria-hidden', 'true');
            li.appendChild(sym); li.appendChild(el('span', 'kj-step-t', s.text)); li.appendChild(el('span', 'kj-cls', s.class_text)); ol.appendChild(li);
          });
          sp.appendChild(ol);
          if (d.approved) sp.appendChild(el('p', 'kj-hint', T('Freigegeben: {when}', { when: when(d.approved.at) })
            + (d.approved.device ? ' · ' + d.approved.device : '')));
          detailEl.appendChild(sp);
        }
        if (d.request && d.request !== d.title) {     // 2509: die Bitte im Wortlaut (Aufgaben haben kein Beschreibungsfeld)
          var bq = el('section', 'kj-sec kj-request'); bq.appendChild(el('h4', null, T('Deine Bitte')));
          bq.appendChild(el('p', null, d.request)); detailEl.appendChild(bq);
        }
        if (d.plan && d.plan.read && d.plan.read.length) {
          var rs = el('section', 'kj-sec'); rs.appendChild(el('h4', null, T('Was ich gelesen habe')));
          rs.appendChild(el('p', null, d.plan.read.map(function (r) { return r.label; }).join(', '))); detailEl.appendChild(rs);
        }
        if (d.state !== 'wartet_ok' && d.plan && d.plan.origin) {   // 2275: Herkunft auch nach der Freigabe (Plan-Karte zeigt sie selbst)
          var os = el('section', 'kj-sec kj-origin-sec'); originNodes(d.plan.origin).forEach(function (x) { os.appendChild(x); }); detailEl.appendChild(os);
        }
        var ev = el('section', 'kj-sec'); ev.appendChild(el('h4', null, T('Protokoll')));
        var ul = el('ul', 'kj-log');
        (d.events || []).forEach(function (e) {
          var li = el('li'); li.appendChild(el('time', 'kj-time', when(e.at)));
          li.appendChild(document.createTextNode(' ' + e.text)); ul.appendChild(li);
        });
        ev.appendChild(ul); detailEl.appendChild(ev);
        Object.keys(olds).forEach(function (c) {
          var b = detailEl.querySelector('.kj-change[data-change="' + c + '"] .kj-old');
          if (b) b.parentNode.insertBefore(olds[c], b.nextSibling);
        });
        var back2 = actK && detailEl.querySelector('.kj-change[data-change="' + actK[0] + '"] .' + actK[1]);
        if (back2 && !focusIt) { try { back2.focus(); } catch (e) { /* egal */ } }
        else if (keep) { try { h.focus(); } catch (e) { /* egal */ } }
        if (d.actions.indexOf('cancel') >= 0 && d.state !== 'wartet_ok') {
          detailEl.appendChild(btn(T('Auftrag abbrechen'), 'kj-cancel', function () {
            api('POST', '/api/ai/jobs/' + id + '/cancel', {}).then(function (r) { say(live, r.text || r.state_text); open(id); refresh(); }).catch(function (e) { HK.toast(e.message); });
          }));
        }
    }
    function close() {
      var last = openId;
      openId = null; shownSig = null; detailEl.hidden = true; detailEl.textContent = ''; listEl.hidden = false; form.hidden = opts.form === false; stage.hidden = false;
      if (opts.form !== false) { try { ta.focus(); } catch (e) { /* egal */ } }   // sofort nicht auf <body>, danach auf den Eintrag
      return refresh().then(function () {
        var b = last != null && listEl.querySelector('.kj-item[data-job="' + last + '"]');
        if (b) try { b.focus(); } catch (e) { /* egal */ }
      });
    }
    function landed(id, x) {                         // Rueckmeldung an Ort und Stelle (Ergebnis zuerst)
      delete watch[id];
      var old = stage.querySelector('[data-job="' + id + '"]');
      var fb = feedback(x, { onOpen: open, onChange: refresh });
      if (old) old.replaceWith(fb); else { stage.textContent = ''; stage.appendChild(fb); }
      say(live, x.report ? x.report.text : x.state_text);
    }
    document.addEventListener('hk-live', function (e) {
      var t = e.detail && e.detail.type;
      if (!root.isConnected) return;
      // 2515: nicht nur am Live-Ereignis haengen - nach (Wieder-)Verbinden der Ereignis-Quelle (live.hello) bzw. resync
      // den Stand einmal neu holen (Ereignisse dazwischen kamen nicht an)
      if (t === 'live.hello' || t === 'resync') {
        // auch das erste hello: zwischen Laden der Seite und Aufbau der Ereignis-Quelle kann ein Auftrag fertig werden (wie
        // live.js Nr. 173) - neu gezeichnet wird nur bei neuem Stand (reopen), also ohne Zuklappen
        refresh();
        reopen();
        Object.keys(watch).forEach(function (id) { load(id).then(function (x) { if (x && x.state !== 'laeuft' && watch[id]) landed(id, x); }); });
        return;
      }
      var d = t === 'ai.job.changed' ? e.detail.data || e.detail : null;
      if (!d) return;
      refresh();
      if (openId === d.id) reopen();
      if (watch[d.id] && d.state !== 'laeuft') {
        load(d.id).then(function (x) { if (x && watch[d.id]) landed(d.id, x); });
      }
    });
    refresh();
    return { el: root, refresh: refresh, open: open, give: give };
  }

  window.HKJobs = { mount: mount, planCard: planCard, feedback: feedback, post: post, askCard: askCardOf, refuseCard: refuseCardOf, head: head };
})();
