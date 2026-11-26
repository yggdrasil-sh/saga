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
// Voraussetzung: KI an mit Fake-Modell fake-plan (tests/fakeai.py), Stufe „Pläne“, Konto mit Seite „Sommerfest“.
//   SAGA_BASE=http://127.0.0.1:8900 SAGA_USER=kp-web SAGA_PASS=... [SHOTS=/tmp/x] node tests/browser_kollegin_plan.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const LAST = (sel) => `[...document.querySelectorAll('#sg-feed ${sel}')].pop()`;

(async () => {
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
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
    await p.screenshot({ path: SHOTS + '/' + name + '.png' });
  }
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
  await p.focus('.kj-plan .kj-go'); await p.keyboard.press('Enter'); await sleep(500);
  ok('Enter auf „Passt, los“ gibt nicht frei', (await api('GET', '/api/ai/jobs/' + jid)).d.state === 'wartet_ok');
  // Aendern
  await p.click('.kj-plan .kj-edit');
  ok('Ändern macht den Text bearbeitbar', await until(() => p.evaluate(() => !!document.querySelector('.kj-editbox input'))));
  await p.fill('.kj-editbox input[type=text]', 'Grillkohle (3 Säcke) kaufen');
  await p.click('.kj-save');
  ok('nach dem Speichern: „geändert – bitte neu freigeben“', await until(() => p.evaluate(() => /geändert – bitte neu freigeben/.test((document.querySelector('.kj-plan .kj-note') || {}).textContent || ''))));
  ok('neuer plan_v auf dem Server', (await api('GET', '/api/ai/jobs/' + jid)).d.plan.plan_v === 2);
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
