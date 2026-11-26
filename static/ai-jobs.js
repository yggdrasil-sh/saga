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

  function persona() {
    var st = window.HKAI && HKAI.state ? HKAI.state() : null;
    return (st && st.persona) || { name: T('Saga KI'), avatar: null };
  }
  function head(stateText) {                     // B1 KI-Kopf: Bild · "Marin · KI" · Zustandswort
    var h = el('div', 'kj-head'), p = persona();
    if (window.HKAIAvatar) h.appendChild(HKAIAvatar(p, 24)); else { var s = el('span', 'kj-spark', '✦'); s.setAttribute('aria-hidden', 'true'); h.appendChild(s); }
    var nm = String(p.name || '');                // Standardname "Saga KI" traegt die KI-Marke schon (kein "Saga KI · KI")
    h.appendChild(el('span', 'kj-who', /(^|\s)(KI|AI|IA|ИИ)$/.test(nm) ? nm : T('{name} · KI', { name: nm })));
    if (stateText) { var w = el('span', 'kj-state', stateText); h.appendChild(w); }
    return h;
  }
  function md(text) {                            // KI-Markdown-Teilmenge (2248) - sonst Klartext
    if (window.HKAI && HKAI.md) return HKAI.md(text);
    var d = el('div', 'kj-plain'); d.textContent = text; return d;
  }
  function btn(label, cls, fn) {
    var b = el('button', 'btn ' + (cls || ''), label); b.type = 'button';
    if (fn) b.addEventListener('click', fn);
    return b;
  }
  function say(live, text) { if (live) { live.textContent = ''; live.textContent = text; } }

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
      if (window.HKAI && HKAI.reportBtn) card.appendChild(HKAI.reportBtn(planText(), 'job', cur.job));
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
      }).catch(function (e) { err.hidden = false; err.textContent = e.message; });
    }
    function approve(err, go) {
      if (!navigator.onLine) { err.hidden = false; err.textContent = T('Offline – gespeicherter Stand. Freigeben geht, sobald du wieder verbunden bist.'); return; }
      go.disabled = true;
      api('POST', '/api/ai/jobs/' + cur.job + '/approve', { plan_v: cur.plan_v, plan_hash: cur.plan_hash }).then(function (d) {
        card.textContent = ''; card.classList.add('kj-small');
        card.appendChild(head(d.state_text));
        card.appendChild(el('p', 'kj-lead', d.say || ''));
        focusIn('.kj-lead');                       // der Knopf ist weg: Fokus nicht auf <body> fallen lassen
        say(opts.live, d.state_text);
        if (opts.onDone) opts.onDone('laeuft', cur.job, card);
      }).catch(function (e) {
        go.disabled = false; err.hidden = false; err.textContent = e.message;
        if (e.status === 409) load(cur.job).then(function (d) { if (d && d.plan && d.state === 'wartet_ok') { cur = d.plan; paint(); } });
      });
    }
    function cancel(err) {
      api('POST', '/api/ai/jobs/' + cur.job + '/cancel', {}).then(function (d) {
        card.textContent = ''; card.classList.add('kj-small');
        card.appendChild(head(d.state_text)); card.appendChild(el('p', 'kj-lead', d.text || ''));
        focusIn('.kj-lead');
        say(opts.live, d.text || d.state_text);
        if (opts.onDone) opts.onDone('abgebrochen', cur.job, card);
      }).catch(function (e) { err.hidden = false; err.textContent = e.message; });
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
    var ul = el('ul', 'kj-changes');
    (rep.lines || []).forEach(function (ln) {
      var li = el('li', 'kj-change');
      li.appendChild(el('span', 'kj-change-t', ln.text));
      var ch = (d.changes || []).filter(function (c) { return c.id === ln.change; })[0] || {};
      if (ln.undone) li.appendChild(el('span', 'kj-cls', T('Zurückgenommen')));
      else if (ln.can_undo) li.appendChild(undoBtn(d.id, ln.change, ch.undo_class, li, opts));
      else if (ch.why === 'changed') li.appendChild(changedNote(ch.version_url));
      ul.appendChild(li);
    });
    if (rep.lines && rep.lines.length) card.appendChild(ul);
    if (opts.onOpen) card.appendChild(btn(T('Ansehen'), 'btn-quiet kj-view', function () { opts.onOpen(d.id, true); }));
    if (window.HKAI && HKAI.reportBtn && rep.text) card.appendChild(HKAI.reportBtn(rep.text, 'job', d.id));
    return card;
  }
  function changedNote(url) {
    var w = el('div', 'kj-changed');
    w.appendChild(el('span', null, T('Das wurde seitdem geändert.')));
    if (url) {
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
  function undoBtn(job, change, undoClass, li, opts) {
    var b = btn(T('Rückgängig'), 'kj-undo', function () {
      var go = function () {
        b.disabled = true;
        api('POST', '/api/ai/jobs/' + job + '/undo', { change: change }).then(function () {
          b.replaceWith(el('span', 'kj-cls', T('Zurückgenommen')));
          HK.toast(T('Zurückgenommen'));
          if (opts.onChange) opts.onChange(job);
        }).catch(function (e) {
          b.disabled = false;
          if (e.status === 409 && e.data && e.data.code === 'changed') b.replaceWith(changedNote(e.data.version_url));
          else HK.toast(e.message);
        });
      };
      if (undoClass === 'delete_only' && HK.ask) {
        HK.ask({ title: T('Wieder löschen?'), text: T('Saga hat das neu angelegt – Rückgängig löscht es wieder.'), ok: T('Löschen'),
                 cancel: T('Behalten'), danger: true }).then(function (yes) { if (yes) go(); });
      } else go();
    });
    return b;
  }

  // --- Ort "Auftraege": Eingabe, Liste in 4 Gruppen, Detail ---------------------------------------------------------
  // mount(container, opts) -> {el, refresh, open(id), give(text)}; opts.note = aktuelle Seite (Kontext), opts.examples
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
    function askCard(d, text) {
      var c = el('div', 'kj-card kj-ask'); c.appendChild(head(''));
      c.appendChild(el('p', 'kj-lead', d.question));
      var chips = el('div', 'kj-chips');
      (d.choices || []).forEach(function (ch) { chips.appendChild(btn(ch, 'kj-chip', function () { give(text, { choice: ch }); })); });
      c.appendChild(chips); return c;
    }
    function refuseCard(d, text) {
      var c = el('div', 'kj-card kj-refuse'); c.appendChild(head(''));
      c.appendChild(el('p', 'kj-lead', d.text || ''));
      var row = el('div', 'kj-actions');
      if (d.alternative) row.appendChild(btn(d.alternative.label || T('Als Aufgabe anlegen'), 'btn-primary', function () {
        give(text || ' ', { recipe: d.alternative.recipe, fields: d.alternative.fields });
      }));
      row.appendChild(btn(T('Anders formulieren'), 'btn-quiet', function () { stage.textContent = ''; ta.value = text; try { ta.focus(); } catch (e) { /* egal */ } send.disabled = !ta.value.trim(); }));
      c.appendChild(row); return c;
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
          e.appendChild(btn(T('Erinnere mich Freitag an den Pavillon'), 'kj-chip', function () { ta.value = T('Erinnere mich Freitag an den Pavillon'); send.disabled = false; give(ta.value); }));
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
            var sym = el('span', 'kj-isym', g.key === 'nicht_geklappt' ? '!' : g.key === 'laeuft' ? '◷' : g.key === 'fertig' ? '✓' : '●');
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

    function open(id, focusIt) {
      openId = id;
      return load(id).then(function (d) {
        if (!d) return;
        var keep = focusIt || detailEl.contains(document.activeElement);   // Neuzeichnen ersetzt das fokussierte Element
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
        if (d.plan && d.plan.read && d.plan.read.length) {
          var rs = el('section', 'kj-sec'); rs.appendChild(el('h4', null, T('Was ich gelesen habe')));
          rs.appendChild(el('p', null, d.plan.read.map(function (r) { return r.label; }).join(', '))); detailEl.appendChild(rs);
        }
        var ev = el('section', 'kj-sec'); ev.appendChild(el('h4', null, T('Protokoll')));
        var ul = el('ul', 'kj-log');
        (d.events || []).forEach(function (e) {
          var li = el('li'); li.appendChild(el('time', 'kj-time', when(e.at)));
          li.appendChild(document.createTextNode(' ' + e.text)); ul.appendChild(li);
        });
        ev.appendChild(ul); detailEl.appendChild(ev);
        if (keep) { try { h.focus(); } catch (e) { /* egal */ } }
        if (d.actions.indexOf('cancel') >= 0 && d.state !== 'wartet_ok') {
          detailEl.appendChild(btn(T('Auftrag abbrechen'), 'kj-cancel', function () {
            api('POST', '/api/ai/jobs/' + id + '/cancel', {}).then(function (r) { say(live, r.text || r.state_text); open(id); refresh(); }).catch(function (e) { HK.toast(e.message); });
          }));
        }
      });
    }
    function close() {
      var last = openId;
      openId = null; detailEl.hidden = true; detailEl.textContent = ''; listEl.hidden = false; form.hidden = false; stage.hidden = false;
      try { ta.focus(); } catch (e) { /* egal */ }                    // sofort nicht auf <body>, danach auf den Eintrag
      return refresh().then(function () {
        var b = last != null && listEl.querySelector('.kj-item[data-job="' + last + '"]');
        if (b) try { b.focus(); } catch (e) { /* egal */ }
      });
    }
    document.addEventListener('hk-live', function (e) {
      var d = e.detail && e.detail.type === 'ai.job.changed' ? e.detail.data || e.detail : null;
      if (!d || !root.isConnected) return;
      refresh();
      if (openId === d.id) open(d.id);
      if (watch[d.id] && d.state !== 'laeuft') {            // Rueckmeldung an Ort und Stelle (Ergebnis zuerst)
        delete watch[d.id];
        load(d.id).then(function (x) {
          if (!x) return;
          var old = stage.querySelector('[data-job="' + d.id + '"]');
          var fb = feedback(x, { onOpen: open, onChange: refresh });
          if (old) old.replaceWith(fb); else { stage.textContent = ''; stage.appendChild(fb); }
          say(live, x.report ? x.report.text : x.state_text);
        });
      }
    });
    refresh();
    return { el: root, refresh: refresh, open: open, give: give };
  }

  window.HKJobs = { mount: mount, planCard: planCard, feedback: feedback, post: post };
})();
