// Saga 2.6 (Bugs-DB 2267 K-B; W2 web-saga) - Plan-Karte im Ort „Saga“ (static/saga.js + static/ai-jobs.js): Auftrag im
// Gespräch über den Umschalter „Als Auftrag“, einziges Freigabe-Muster „Passt, los · Ändern · Lieber nicht“ (genau eine
// Karte je Auftrag, keine zweite), Schritte mit Art und Klasse als Wort, „Ich lese dafür“ vom Server, Enter gibt nie frei,
// Screenreader-Text je Schritt, Ändern -> „geändert – bitte neu freigeben“, Freigabe -> Rückmeldung im Verlauf (Ergebnis
// einmal + Rückgängig als Knopf >= 44 px, „Ansehen“ -> Reiter „Aufträge“), Rückgängig, Lieber nicht -> „Abgebrochen – nichts
// ist passiert“, Rückfrage mit Chips, Absage mit „Als Aufgabe anlegen“, Verlauf nach dem Neuladen im jetzigen Stand,
// Tastatur (Umschalter, Reiter mit Pfeilen), Melden-Knopf (2258a), hell/dunkel, schmal, keine Skriptfehler. Der KI-Bereich
// der Seiten hat keinen Reiter „Aufträge“ mehr (HKAI.open('jobs') führt zum Ort „Saga“).
// Bilder (SHOTS=<ordner>): saga-plan-hell/dunkel, saga-rueckmeldung-hell/dunkel, saga-rueckfrage-dunkel, saga-absage-dunkel,
// saga-plan-schmal-dunkel/hell, saga-verlauf-neu-dunkel.
// Chat 18 (Bugs-DB 2515/2508): „geändert – bitte neu freigeben“ auch nach dem Neuladen (edited vom Server); 409 stale ->
// neuer Plan mit Hinweis (nicht still); 409 state -> jetziger Stand statt aktivem „Passt, los“; Rückmeldung ohne
// Live-Ereignis nach live.hello; Kontrast der Knöpfe auf der KI-Karte (dunkel/hell, Kante >= 3:1, Text >= 4,5:1);
// „Verlauf an/aus“ folgt Einstellungen › Saga; KI-Bereich einer Seite -> „… soll sich kümmern …“ -> Ort „Saga“ mit
// context.note. Bilder dazu: saga-409-stale-dunkel, ki-seite-auftrag-dunkel, saga-seite-auftrag-dunkel.
// Voraussetzung: KI an mit Fake-Modell fake-plan (tests/fakeai.py), Stufe „Pläne“, Konto mit Seite „Sommerfest“.
//   SAGA_BASE=http://127.0.0.1:8900 SAGA_USER=kp-web SAGA_PASS=... [SHOTS=/tmp/x] [BR=firefox] node tests/browser_kollegin_plan.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
const FF = E.BR === 'firefox', SFX = FF ? '-firefox' : '';
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const LAST = (sel) => `[...document.querySelectorAll('#sg-feed ${sel}')].pop()`;

(async () => {
  const br = await (FF ? PW.firefox : PW.chromium).launch(!FF && E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: 'dark' });
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); return { s: r.status, d: d };
  }, [m, url, b]);
  await api('PUT', '/api/account/lang', { lang: 'de' });
  await api('POST', '/api/first-steps', { answer: 'no' });
  { const st = await api('GET', '/api/ai/status');            // K-I (2269): Kollegin erst nach der Einwilligung (Kennenlernen)
    if (st.d && st.d.intro && (!st.d.consent || st.d.consent.state !== 'granted')) await api('POST', '/api/ai/consent', { answer: 'no_ideas', version: st.d.intro.version, host: st.d.intro.host }); }
  await api('DELETE', '/api/ai/thread');
  const cnt = () => p.evaluate(() => document.querySelectorAll('#sg-feed .kj-plan, #sg-feed .kj-ask, #sg-feed .kj-refuse, #sg-feed .kj-report').length);
  async function openSaga() {
    await p.goto(BASE + '/saga');
    return until(() => p.evaluate(() => !document.getElementById('sg-ask').hidden && !!document.querySelector('#sg-feed > *')));
  }
  async function jobMode(on) {
    const pressed = await p.getAttribute('#sg-mode', 'aria-pressed');
    if ((pressed === 'true') !== on) await p.click('#sg-mode');
  }
  async function give(text) {                                   // im Gespräch, Umschalter „Als Auftrag“ an
    const n0 = await cnt();
    await jobMode(true); await p.fill('#sg-ta', text); await p.press('#sg-ta', 'Enter');
    return until(async () => (await cnt()) > n0 && await p.evaluate(() => !document.querySelector('#sg-feed .kj-wait')), 15000);
  }
  async function shot(name) {
    if (!SHOTS) return;
    await sleep(600);
    await p.screenshot({ path: SHOTS + '/' + name + SFX + '.png' });
  }
  // 2515: Kontrast aller Knöpfe einer KI-Karte - Kante gegen die Kartenfläche (>= 3:1, WCAG 1.4.11), Text gegen seinen
  // Hintergrund (Knopf oder Karte, >= 4,5:1). Primärknopf: Fläche gegen Karte.
  const contrast = (sel) => p.evaluate((sel) => {
    const rgb = (c) => { const m = String(c).match(/[\d.]+/g).map(Number); return { v: /^color\(/.test(c) ? m.slice(0, 3).map((x) => x * 255) : m.slice(0, 3), a: m.length > 3 ? m[3] : 1 }; };
    const lum = (c) => { const v = rgb(c).v.map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
    const cr = (a, b) => { const x = lum(a), y = lum(b); return Math.round((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) * 100) / 100; };
    const bgOf = (n) => { for (; n; n = n.parentElement) { const c = getComputedStyle(n).backgroundColor; if (rgb(c).a > 0.5) return c; } return 'rgb(255,255,255)'; };
    return [...document.querySelectorAll(sel)].filter((b) => b.offsetParent).map((b) => {
      const cs = getComputedStyle(b), card = bgOf(b.closest('.kj-card, .sg-card') || b.parentElement), own = rgb(cs.backgroundColor).a > 0.5 ? cs.backgroundColor : card;
      const edge = parseFloat(cs.borderTopWidth) > 0 && rgb(cs.borderTopColor).a > 0.5 ? cs.borderTopColor : own;
      return { t: b.textContent.trim(), edge: cr(edge, card), text: cr(cs.color, own) };
    });
  }, sel);
  const lowC = (xs) => xs.filter((x) => x.edge < 3 || x.text < 4.5);
  const st = await api('GET', '/api/ai/status');
  ok('KI an, Aufträge an (status.jobs)', st.d.enabled && st.d.jobs === true, st.d);
  // Der KI-Bereich der Seiten hat keinen Reiter „Aufträge“ mehr - HKAI.open('jobs') führt zum Ort „Saga“
  await p.goto(BASE + '/dashboard'); await until(() => p.evaluate(() => !!(window.HKAI && HKAI.state && HKAI.state())));
  await p.evaluate(() => HKAI.open('ask'));
  const tabsAi = await p.evaluate(() => [...document.querySelectorAll('#ai-panel .ai-tab')].map((b) => b.textContent));
  ok('KI-Bereich: Reiter „Frag deine Seiten“, kein Reiter „Aufträge“ (UX §2: Aufträge wohnen im Ort „Saga“)', tabsAi.indexOf('Aufträge') < 0 && tabsAi.indexOf('Frag deine Seiten') >= 0, tabsAi);
  await Promise.all([p.waitForNavigation(), p.evaluate(() => HKAI.open('jobs'))]);
  ok('HKAI.open(\'jobs\') -> /saga?tab=jobs', /\/saga\?tab=jobs$/.test(p.url()), p.url());
  await until(() => p.evaluate(() => !document.getElementById('sg-tabs').hidden && !!document.querySelector('#sg-jobs .kj-root')));
  const T0 = await p.evaluate(() => ({ tabs: [...document.querySelectorAll('#sg-tabs [role=tab]')].map((b) => [b.textContent, b.getAttribute('aria-selected')]),
    mode: !document.getElementById('sg-mode').hidden, pressed: document.getElementById('sg-mode').getAttribute('aria-pressed'), jobs: !document.getElementById('sg-jobs').hidden }));
  ok('Reiter „Gespräch / Aufträge“ (role=tab), mit ?tab=jobs ist „Aufträge“ gewählt', JSON.stringify(T0.tabs) === JSON.stringify([['Gespräch', 'false'], ['Aufträge', 'true']]) && T0.jobs, T0);
  ok('Umschalter „Als Auftrag“ sichtbar, aus (aria-pressed=false)', T0.mode && T0.pressed === 'false', T0);
  ok('Ort „Saga“ öffnet (Gespräch)', await openSaga());
  // Tastatur: Reiter mit Pfeilen, Umschalter mit Leertaste
  await p.focus('#sg-tab-jobs'); await p.keyboard.press('ArrowLeft');
  const kb = await p.evaluate(() => ({ f: document.activeElement.id, sel: document.getElementById('sg-tab-talk').getAttribute('aria-selected'), talk: !document.getElementById('sg-talk').hidden }));
  ok('Tastatur: Pfeil links wechselt zu „Gespräch“ (Fokus, aria-selected, Bereich sichtbar)', kb.f === 'sg-tab-talk' && kb.sel === 'true' && kb.talk, kb);
  await p.focus('#sg-ta');
  let reached = false;
  for (let i = 0; i < 4 && !reached; i++) { await p.keyboard.press('Tab'); reached = await p.evaluate(() => document.activeElement.id === 'sg-mode'); }
  await p.keyboard.press('Space');
  const md = await p.evaluate(() => ({ pressed: document.getElementById('sg-mode').getAttribute('aria-pressed'), send: document.getElementById('sg-send').textContent,
    ph: document.getElementById('sg-ta').placeholder, lab: document.getElementById('sg-ta').getAttribute('aria-label') }));
  ok('Tastatur: Tab erreicht „Als Auftrag“, Leertaste schaltet an -> Knopf „Auftrag geben“, Platzhalter „Sag …, was du brauchst …“',
    reached && md.pressed === 'true' && md.send === 'Auftrag geben' && /^Sag .+, was du brauchst …$/.test(md.ph) && /^Auftrag an /.test(md.lab), md);
  const an = await p.getByRole('button', { name: 'Als Auftrag', exact: true, pressed: true }).count();
  ok('pweb 2: zugänglicher Name „Als Auftrag“ ohne den Haken (Zustand über aria-pressed)', an === 1, an);

  ok('Plan-Karte erscheint im Verlauf', await give('Kümmer dich um die Grillkohle bis Freitag'));
  const c = await p.evaluate((L) => {
    const k = eval(L);
    return { btns: [...k.querySelectorAll('.kj-actions button')].map((b) => b.textContent), state: (k.querySelector('.kj-state') || {}).textContent,
             head: (k.querySelector('.kj-who') || {}).textContent, steps: [...k.querySelectorAll('.kj-step')].map((s) => ({ t: s.querySelector('.kj-step-t').textContent,
               cls: s.querySelector('.kj-cls').textContent, sym: s.querySelector('.kj-sym').textContent, aria: s.getAttribute('aria-label') })),
             read: (k.querySelector('.kj-read') || {}).textContent || '', lead: k.querySelector('.kj-lead').textContent,
             focusInCard: k.contains(document.activeElement), report: !!k.querySelector('.sg-report, .ai-report'),
             me: ([...document.querySelectorAll('#sg-feed .sg-me-job')].pop() || {}).textContent,
             gap: (() => { const a = k.querySelector('.kj-go').getBoundingClientRect(), b = k.querySelector('.kj-no').getBoundingClientRect(); return b.left - a.right; })() };
  }, LAST('.kj-plan'));
  ok('Bitte steht als eigener Zug mit Kennzeichen „Auftrag“', /^Auftrag.*Grillkohle bis Freitag$/.test(c.me || ''), c.me);
  ok('genau drei Knöpfe: Passt, los · Ändern · Lieber nicht', JSON.stringify(c.btns) === JSON.stringify(['Passt, los', 'Ändern', 'Lieber nicht']), c.btns);
  ok('KI-Kopf „Marin · KI“ + Zustandswort „Wartet auf dein OK“', /(· KI| KI)$/.test(c.head) && !/KI · KI/.test(c.head) && c.state === 'Wartet auf dein OK', c);
  ok('Schritt mit Art-Symbol ✎ und Klasse als Wort', c.steps.length === 1 && c.steps[0].sym === '✎' && c.steps[0].cls === 'nur löschbar' && /Grillkohle/.test(c.steps[0].t), c.steps);
  ok('Screenreader: „Schritt 1 von 1, Eigenes ändern, nur löschbar: …“', /^Schritt 1 von 1, Eigenes ändern, nur löschbar: Aufgabe anlegen/.test(c.steps[0].aria), c.steps[0].aria);
  ok('„Ich lese dafür: …“ vom Server', /^Ich lese dafür: .*Sommerfest/.test(c.read), c.read);
  ok('Neue Karte nimmt nicht den Fokus (Fokus bleibt im Eingabefeld)', !c.focusInCard && await p.evaluate(() => document.activeElement.id === 'sg-ta'));
  ok('„Lieber nicht“ mit Abstand zum Primärknopf (≥ 16 px)', c.gap >= 16, c.gap);
  ok('Melden … an der Plan-Karte (2258a)', c.report);
  const jid = await p.evaluate((L) => +eval(L).closest('.sg-job').dataset.job, LAST('.kj-plan'));
  const one = await p.evaluate((id) => ({ plans: document.querySelectorAll(`.kj-plan[data-job="${id}"]`).length, cards: document.getElementById('sg-cards').children.length,
    statusLine: document.getElementById('sg-status').textContent }), jid);
  ok('Ein Freigabe-Muster: genau eine Plan-Karte für den Auftrag, keine zweite (#sg-cards leer)', one.plans === 1 && one.cards === 0, one);
  ok('Ansage über role=status: „Plan bereit – wartet auf dein OK“', one.statusLine === 'Plan bereit – wartet auf dein OK', one.statusLine);
  await shot('saga-plan-dunkel');
  { const cd = await contrast('#sg-feed .kj-plan .btn');
    ok('2515: Knöpfe der Plan-Karte dunkel: Kante >= 3:1, Text >= 4,5:1 (' + JSON.stringify(cd) + ')', cd.length >= 3 && !lowC(cd).length, lowC(cd));
    await p.emulateMedia({ colorScheme: 'light' }); await sleep(200);
    const cl = await contrast('#sg-feed .kj-plan .btn'); await p.emulateMedia({ colorScheme: 'dark' });
    ok('2515: Knöpfe der Plan-Karte hell: Kante >= 3:1, Text >= 4,5:1', cl.length >= 3 && !lowC(cl).length, cl); }
  await p.focus('.kj-plan .kj-go'); await p.keyboard.press('Enter'); await sleep(500);
  ok('Enter auf „Passt, los“ gibt nicht frei', (await api('GET', '/api/ai/jobs/' + jid)).d.state === 'wartet_ok');
  // Aendern
  await p.click('.kj-plan .kj-edit');
  ok('Ändern macht den Text bearbeitbar', await until(() => p.evaluate(() => !!document.querySelector('.kj-editbox input'))));
  await p.fill('.kj-editbox input[type=text]', 'Grillkohle (3 Säcke) kaufen');
  await p.click('.kj-save');
  ok('nach dem Speichern: „geändert – bitte neu freigeben“', await until(() => p.evaluate(() => /geändert – bitte neu freigeben/.test((document.querySelector('.kj-plan .kj-note') || {}).textContent || ''))));
  ok('neuer plan_v auf dem Server', (await api('GET', '/api/ai/jobs/' + jid)).d.plan.plan_v === 2);
  await openSaga();
  ok('2515: nach dem Neuladen steht „geändert – bitte neu freigeben“ weiter an der Plan-Karte (edited vom Server)', await until(() => p.evaluate((id) =>
    /geändert – bitte neu freigeben/.test((document.querySelector(`#sg-feed .sg-job[data-job="${id}"] .kj-plan .kj-note`) || {}).textContent || ''), jid)));
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('saga-plan-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  // Passt, los -> Rueckmeldung im Verlauf
  await p.click('.kj-plan .kj-go');
  const fb = await until(() => p.evaluate((id) => { const w = document.querySelector(`#sg-feed .sg-job[data-job="${id}"]`), r = w && w.querySelector('.kj-report');
    return r && { state: r.querySelector('.kj-state').textContent, res: r.querySelector('.kj-result').textContent, undo: !!r.querySelector('.kj-undo'),
      h: r.querySelector('.kj-undo') && r.querySelector('.kj-undo').getBoundingClientRect().height, aria: r.querySelector('.kj-undo') && r.querySelector('.kj-undo').getAttribute('aria-label'),
      times: (r.textContent.match(/3 Säcke/g) || []).length, lineT: r.querySelectorAll('.kj-change-t').length, view: !!r.querySelector('.kj-view'),
      status: document.getElementById('sg-status').textContent }; }, jid), 15000);
  ok('Rückmeldung im Verlauf an Stelle der Plan-Karte: „Erledigt“, Ergebnis zuerst', fb && fb.state === 'Erledigt' && /3 Säcke/.test(fb.res), fb);
  ok('2504: Ergebnis steht genau einmal (keine zweite Zeile mit demselben Satz)', fb && fb.times === 1 && fb.lineT === 0, fb);
  ok('Rückgängig als Knopf ≥ 44 px, Name für Screenreader „Rückgängig: …“', fb && fb.undo && fb.h >= 44 && /^Rückgängig: .*3 Säcke.*angelegt/.test(fb.aria || ''), fb);
  ok('Zeile unter dem Feld meldet den neuen Stand („Erledigt“, nicht mehr „Läuft“)', fb && fb.status === 'Erledigt', fb && fb.status);
  ok('Verweis auf den Ort „Aufträge“: Knopf „Ansehen“', fb && fb.view);
  await shot('saga-rueckmeldung-dunkel');
  { const cd = await contrast(`#sg-feed .sg-job[data-job="${jid}"] .kj-report .btn`);
    ok('2515: Knöpfe der Rückmeldung dunkel (Rückgängig, Ansehen): Kante >= 3:1, Text >= 4,5:1 (' + JSON.stringify(cd) + ')', cd.length >= 2 && !lowC(cd).length, lowC(cd)); }
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('saga-rueckmeldung-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  const list1 = JSON.stringify((await api('GET', '/api/tasks')).d);
  ok('Aufgabe genau einmal angelegt', (list1.match(/3 Säcke/g) || []).length === 1, list1.slice(0, 300));
  // Verlauf nach dem Neuladen: derselbe Auftrag im jetzigen Stand (Rueckmeldung, nicht wieder die Plan-Karte)
  await openSaga();
  const re = await until(() => p.evaluate((id) => { const w = document.querySelector(`#sg-feed .sg-job[data-job="${id}"]`);
    return w && w.querySelector('.kj-report') && { plan: !!w.querySelector('.kj-plan'), undo: !!w.querySelector('.kj-undo'), me: !!document.querySelector('#sg-feed .sg-me-job') }; }, jid));
  ok('nach dem Neuladen: Bitte + Rückmeldung mit Rückgängig (jetziger Stand, keine Plan-Karte)', re && !re.plan && re.undo && re.me, re);
  await shot('saga-verlauf-neu-dunkel');
  await p.click(`#sg-feed .sg-job[data-job="${jid}"] .kj-undo`);
  ok('nur löschbar: Rückfrage vor dem Löschen', await until(() => p.evaluate(() => !!document.querySelector('.hk-ask-box'))));
  await p.click('.hk-ask-box .btn-destr');
  ok('Rückgängig -> „Zurückgenommen“ im Verlauf', await until(() => p.evaluate((id) => /Zurückgenommen/.test((document.querySelector(`#sg-feed .sg-job[data-job="${id}"]`) || {}).textContent || ''), jid)));
  ok('Aufgabe wieder weg', await until(async () => !/3 Säcke/.test(JSON.stringify((await api('GET', '/api/tasks')).d))));
  ok('Fokus nach Rückgängig nicht auf <body>', await p.evaluate(() => document.activeElement && document.activeElement !== document.body));
  // Ansehen -> Detail im Reiter "Auftraege"
  await p.click(`#sg-feed .sg-job[data-job="${jid}"] .kj-view`);
  const det = await until(() => p.evaluate(() => { const d = document.querySelector('#sg-jobs .kj-detail:not([hidden])'); return d && !document.getElementById('sg-jobs').hidden && d.querySelector('.kj-dstate') && {
    title: d.querySelector('.kj-dtitle').textContent, state: d.querySelector('.kj-dstate strong').textContent, focus: document.activeElement === d.querySelector('.kj-dtitle'),
    tab: document.getElementById('sg-tab-jobs').getAttribute('aria-selected') }; }));
  ok('„Ansehen“ öffnet das Detail im Reiter „Aufträge“ (Fokus auf dem Titel)', det && det.tab === 'true' && /3 Säcke/.test(det.title) && det.state === 'Zurückgenommen' && det.focus, det);
  await p.click('#sg-tab-talk');
  // Lieber nicht
  await give('Erinnere mich Freitag an die Grillkohle');
  await p.locator('#sg-feed .kj-plan .kj-no').last().click();
  ok('Lieber nicht -> „Abgebrochen – nichts ist passiert.“', await until(() => p.evaluate((L) => /Abgebrochen – nichts ist passiert\./.test((eval(L) || {}).textContent || ''), LAST('.sg-job'))));
  const ln = await p.evaluate((L) => { const w = eval(L); return { id: +w.dataset.job, lead: w.querySelector('.kj-lead').textContent, note: (w.querySelector('.kj-note') || {}).textContent }; }, LAST('.sg-job'));
  const lnT = (await api('GET', '/api/ai/jobs/' + ln.id)).d.title;
  ok('pweb 3: nach „Lieber nicht“ Leitzeile = Titel, darunter der Grund (wie nach dem Neuladen)', ln.lead === lnT && ln.note === 'Abgebrochen – nichts ist passiert.', { ln, lnT });
  // Absage mit Alternative
  ok('Absage erscheint', await give('Bestell die Kohle in einem Onlineshop'));
  const rf = await p.evaluate((L) => { const k = eval(L); return k && { t: k.querySelector('.kj-lead').textContent, b: [...k.querySelectorAll('button')].map((x) => x.textContent) }; }, LAST('.kj-refuse'));
  ok('Absage: Satz + „Als Aufgabe anlegen“ + „Anders formulieren“', rf && rf.t.trim().length > 0 && rf.b.includes('Als Aufgabe anlegen') && rf.b.includes('Anders formulieren'), rf);
  await shot('saga-absage-dunkel');
  const n1 = await cnt();
  await p.locator('#sg-feed .kj-refuse .btn-primary').last().click();
  ok('„Als Aufgabe anlegen“ -> Plan-Karte im Verlauf, Bitte „Als Aufgabe anlegen“', await until(async () => (await cnt()) > n1 && p.evaluate(() =>
    !!document.querySelector('#sg-feed .sg-job:last-child .kj-plan') && /Als Aufgabe anlegen$/.test(([...document.querySelectorAll('#sg-feed .sg-me-job')].pop() || {}).textContent || '')), 15000));
  await p.locator('#sg-feed .kj-plan .kj-no').last().click(); await sleep(300);
  // Rueckfrage
  await api('POST', '/api/notes', { title: 'Sommerfest' });
  ok('Rückfrage erscheint', await give('Trag bei Sommerfest ein, dass Ben die Grillkohle mitbringt.'));
  const chips = await p.evaluate((L) => [...eval(L).querySelectorAll('.kj-chip')].map((x) => x.textContent), LAST('.kj-ask'));
  ok('Rückfrage mit zwei Chips', chips.length === 2, chips);
  await shot('saga-rueckfrage-dunkel');
  const n2 = await cnt();
  await p.locator('#sg-feed .kj-ask .kj-chip').last().click();
  ok('Chip -> Plan für genau diese Seite', await until(async () => (await cnt()) > n2 && p.evaluate(() => !!document.querySelector('#sg-feed .sg-job:last-child .kj-plan .kj-draft')), 15000));
  await p.locator('#sg-feed .kj-plan .kj-no').last().click(); await sleep(300);
  // ohne Umschalter: normale Frage, kein Auftrag
  const nj = (await api('GET', '/api/ai/jobs')).d.counts;
  await jobMode(false); await p.fill('#sg-ta', 'Was steht zum Sommerfest an?'); await p.press('#sg-ta', 'Enter');
  await until(() => p.evaluate(() => { const c = [...document.querySelectorAll('#sg-feed .sg-ai')].pop(); return c && !c.classList.contains('sg-live'); }), 15000);
  ok('Umschalter aus: Frage geht ins Gespräch, kein neuer Auftrag', JSON.stringify((await api('GET', '/api/ai/jobs')).d.counts) === JSON.stringify(nj));
  // --- 2515: Konflikte beim Freigeben und Abgleich ohne Live-Ereignis (Ereignis-Quelle gesperrt) ----------------------
  await p.route('**/api/events**', (r) => r.abort());
  await openSaga();
  const jobOf = () => p.evaluate((L) => +eval(L).closest('.sg-job').dataset.job, LAST('.kj-plan'));
  const W = (id) => `#sg-feed .sg-job[data-job="${id}"]`;
  await give('Erinnere mich Freitag an den Pavillon');
  const j2 = await jobOf(), pl2 = (await api('GET', '/api/ai/jobs/' + j2)).d.plan;
  await api('PUT', '/api/ai/jobs/' + j2 + '/plan', { plan_v: pl2.plan_v, edits: [{ n: 0, args: { text: 'Pavillon abholen (anderes Gerät)' } }] });
  await p.click(W(j2) + ' .kj-go');
  const s409 = await until(() => p.evaluate((sel) => { const k = document.querySelector(sel + ' .kj-plan');
    return k && /anderes Gerät/.test(k.textContent) && { note: (k.querySelector('.kj-note') || {}).textContent, err: (k.querySelector('.kj-err:not([hidden])') || {}).textContent,
      go: !!k.querySelector('.kj-go:not([disabled])'), focus: k.contains(document.activeElement), status: document.getElementById('sg-status').textContent }; }, W(j2)));
  ok('2515: 409 stale -> neuer Plan mit „geändert – bitte neu freigeben“ + Meldung (nicht still), Fokus in der Karte', s409
    && /geändert – bitte neu freigeben/.test(s409.note || '') && /geändert/.test(s409.err || '') && s409.go && s409.focus, s409);
  await shot('saga-409-stale-dunkel');
  const pl3 = (await api('GET', '/api/ai/jobs/' + j2)).d.plan;
  await api('POST', '/api/ai/jobs/' + j2 + '/approve', { plan_v: pl3.plan_v, plan_hash: pl3.plan_hash });   // anderes Gerät gibt frei
  await p.click(W(j2) + ' .kj-go');
  const sst = await until(() => p.evaluate((sel) => { const w = document.querySelector(sel);
    return w && !w.querySelector('.kj-go') && w.querySelector('.kj-state') && { state: w.querySelector('.kj-state').textContent, ds: w.dataset.state }; }, W(j2)));
  ok('2515: 409 state (anderswo freigegeben) -> keine Plan-Karte mit „Passt, los“, jetziger Stand', sst && ['Läuft', 'Erledigt'].indexOf(sst.state) >= 0, sst);
  await give('Erinnere mich Samstag an die Lampions');
  const j3 = await jobOf(), pl4 = (await api('GET', '/api/ai/jobs/' + j3)).d.plan;
  await api('POST', '/api/ai/jobs/' + j3 + '/approve', { plan_v: pl4.plan_v, plan_hash: pl4.plan_hash });
  await until(async () => (await api('GET', '/api/ai/jobs/' + j3)).d.state === 'erledigt', 15000); await sleep(800);
  const still = await p.evaluate((sel) => !!document.querySelector(sel + ' .kj-plan .kj-go'), W(j3));
  await p.unroute('**/api/events**');
  const gets = []; const onReq = (r) => { if (r.method() === 'GET' && /\/api\/ai\/jobs\/\d+$/.test(r.url())) gets.push(+r.url().split('/').pop()); };
  p.on('request', onReq);
  await p.evaluate(() => document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'live.hello', data: { first: true } } })));
  await sleep(800); const g1 = gets.length;
  const fe = await p.evaluate(() => { const ws = [...document.querySelectorAll('#sg-feed .sg-job[data-job]')];
    return { all: ws.length, open: ws.filter((w) => ['erledigt', 'zurueckgenommen', 'abgebrochen', 'fehlgeschlagen', 'weg'].indexOf(w.dataset.state) < 0).map((w) => +w.dataset.job) }; });
  await p.evaluate(() => document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'live.hello', data: { first: false } } })));
  const fb3 = await until(() => p.evaluate((sel) => !!document.querySelector(sel + ' .kj-report'), W(j3)), 8000);
  await sleep(500); p.off('request', onReq);
  ok('2515: Rückmeldung ohne Live-Ereignis – nach dem (Wieder-)Verbinden (live.hello) neu geholt', still && fb3, { still, fb3 });
  ok('2515: erstes hello holt nichts; hello holt nur offene Aufträge (' + JSON.stringify({ g1, gets, fe }) + ')', g1 === 0 && fe.all > fe.open.length
    && gets.length === fe.open.length && gets.every((x) => fe.open.indexOf(x) >= 0) && gets.indexOf(j3) >= 0, { g1, gets, fe });
  // --- 2515: „Verlauf an/aus“ folgt Einstellungen › Saga ----------------------------------------------------------------
  await openSaga();
  const keep0 = await p.evaluate(() => document.getElementById('sg-keep').textContent);
  await p.evaluate(() => window.HKSettings && HKSettings.open('saga'));
  const hasBox = await until(() => p.evaluate(() => [...document.querySelectorAll('.sg-set-row')].some((r) => /Verlauf speichern/.test(r.textContent))), 6000);
  if (hasBox) {
    await p.evaluate(() => { const r = [...document.querySelectorAll('.sg-set-row')].find((x) => /Verlauf speichern/.test(x.textContent)); r.querySelector('input[type=checkbox]').click(); });
    const keep1 = await until(() => p.evaluate((k0) => { const t = document.getElementById('sg-keep').textContent; return t !== k0 && t; }, keep0), 6000);
    ok('2515: „Verlauf an/aus“ im Gespräch wechselt nach der Einstellung (ohne Neuladen)', keep1 && /Verlauf (an|aus)/.test(keep1) && keep1.slice(0, 11) !== keep0.slice(0, 11), { keep0, keep1 });
    await p.evaluate(() => { const r = [...document.querySelectorAll('.sg-set-row')].find((x) => /Verlauf speichern/.test(x.textContent)); r.querySelector('input[type=checkbox]').click(); });
    await sleep(800);
  } else ok('2515: Einstellungen › Saga mit „Verlauf speichern“ (Admin: Verlauf erlaubt)', false, keep0);
  await p.keyboard.press('Escape'); await sleep(300);
  // --- 2508: KI-Bereich einer Seite -> „… soll sich kümmern …“ -> Ort „Saga“ mit context.note --------------------------
  const pg = (await api('POST', '/api/notes', { title: 'Pavillon-Liste' })).d;
  await p.goto(BASE + '/notes#' + pg.id);
  await until(() => p.evaluate((id) => !!(window.HKAI && HKAI.state() && window.HKPages && HKPages.k && HKPages.k.cur() && HKPages.k.cur().id === id), pg.id), 10000);
  await p.evaluate(() => HKAI.open('page'));
  const jb = await until(() => p.evaluate(() => { const b = document.querySelector('#ai-panel .ai-act-job'); return b && b.offsetParent && b.textContent; }), 6000);
  ok('2508: KI-Bereich „Diese Seite“: Einstieg „… soll sich kümmern …“', jb && /soll sich kümmern …$/.test(jb), jb);
  await shot('ki-seite-auftrag-dunkel');
  const posted = [];
  p.on('request', (r) => { if (r.method() === 'POST' && /\/api\/ai\/jobs$/.test(r.url())) posted.push(r.postData()); });
  await Promise.all([p.waitForNavigation(), p.click('#ai-panel .ai-act-job')]);
  const sg = await until(() => p.evaluate(() => { const n = document.getElementById('sg-note');
    return n && !n.hidden && { t: n.textContent, mode: document.getElementById('sg-mode').getAttribute('aria-pressed'), focus: document.activeElement.id, url: location.pathname + location.search }; }), 8000);
  ok('2508: Ort „Saga“: „Zur Seite „Pavillon-Liste““, „Als Auftrag“ an, Fokus im Eingabefeld', sg && /Zur Seite „Pavillon-Liste“/.test(sg.t) && sg.mode === 'true' && sg.focus === 'sg-ta' && sg.url === '/saga', sg);
  await shot('saga-seite-auftrag-dunkel');
  await give('Kümmer dich um die Grillkohle bis Freitag');
  const ctxOk = posted.some((x) => { try { return (JSON.parse(x).context || {}).note === pg.id; } catch (e) { return false; } });
  const rd = await p.evaluate((L) => (eval(L).querySelector('.kj-read') || {}).textContent || '', LAST('.kj-plan'));
  ok('2508: Auftrag geht mit context.note an den Server, der Plan liest die Seite', ctxOk && /Pavillon-Liste/.test(rd), { posted, rd });
  await p.locator('#sg-feed .kj-plan .kj-no').last().click(); await sleep(300);
  await p.click('#sg-note .sg-note-x');
  ok('2508: × nimmt die Seite wieder weg', await p.evaluate(() => document.getElementById('sg-note').hidden));
  // Handy: Knoepfe untereinander, Primaerknopf volle Breite, kein Ueberlauf
  await p.setViewportSize({ width: 390, height: 844 });
  await give('Erinnere mich Freitag an die Grillkohle');
  const mob = await p.evaluate((L) => { const k = eval(L), g = k.querySelector('.kj-go').getBoundingClientRect(), r = k.getBoundingClientRect(), t = document.getElementById('sg-tab-jobs').getBoundingClientRect(),
    m = document.getElementById('sg-mode').getBoundingClientRect();
    return { w: g.width / r.width, h: g.height, tab: t.height, mode: m.height, over: document.documentElement.scrollWidth > window.innerWidth + 1 }; }, LAST('.kj-plan'));
  ok('Handy: „Passt, los“ volle Breite, ≥ 44 px; Reiter und Umschalter ≥ 44 px; kein waagrechter Überlauf', mob.w > 0.8 && mob.h >= 44 && mob.tab >= 44 && mob.mode >= 44 && !mob.over, mob);
  await shot('saga-plan-schmal-dunkel');
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('saga-plan-schmal-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  await p.locator('#sg-feed .kj-plan .kj-no').last().click(); await sleep(300);
  ok('keine Skriptfehler', !errs.length, errs);
  await br.close();
  const bad = R.filter((x) => !x[1]).length;
  console.log(`\nKollegin Plan-Karte (Browser) ${R.length - bad}/${R.length}` + (bad ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
