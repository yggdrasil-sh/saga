// Saga 2.6 (Bugs-DB 2267 K-B) - Plan-Karte im Web (static/ai-jobs.js): einziges Freigabe-Muster "Passt, los · Ändern ·
// Lieber nicht", Schritte mit Art und Klasse als Wort (rückgängig / nur löschbar / endgültig), "Ich lese dafür" vom Server,
// Enter gibt nie frei, Screenreader-Text je Schritt, Ändern -> "geändert – bitte neu freigeben", Freigabe -> Rückmeldung
// (Ergebnis zuerst + Rückgängig als Knopf >= 44 px), Lieber nicht -> "Abgebrochen – nichts ist passiert", Rückfrage mit
// Chips, Absage mit "Als Aufgabe anlegen", Melden-Knopf (2258a), hell und dunkel, keine Skriptfehler.
// Bilder (SHOTS=<ordner>): plan-hell/dunkel, rueckmeldung-hell/dunkel, rueckfrage-dunkel, absage-dunkel.
// Voraussetzung: KI an mit Fake-Modell fake-plan (tests/fakeai.py), Stufe "Pläne", Konto mit Seite "Sommerfest".
//   SAGA_BASE=http://127.0.0.1:8900 SAGA_USER=kp-web SAGA_PASS=... [SHOTS=/tmp/x] node tests/browser_kollegin_plan.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
if (!USER || !PASS) { console.error('SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }

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
  async function openJobs() {
    await p.goto(BASE + '/dashboard'); await until(() => p.evaluate(() => !!(window.HKAI && HKAI.state && HKAI.state())));
    await p.evaluate(() => HKAI.open('jobs'));
    return until(() => p.evaluate(() => !!document.querySelector('.kj-root .kj-ta')));
  }
  async function give(text) {
    await p.fill('.kj-ta', text); await p.press('.kj-ta', 'Enter');
    return until(() => p.evaluate(() => !!document.querySelector('.kj-stage .kj-plan, .kj-stage .kj-ask, .kj-stage .kj-refuse')), 15000);
  }
  async function shot(name, sel) {
    if (!SHOTS) return;
    await sleep(600);
    const box = await p.$(sel || '#ai-panel');
    if (box) await box.screenshot({ path: SHOTS + '/' + name + '.png' });
  }
  const st = await api('GET', '/api/ai/status');
  ok('KI an, Aufträge an (status.jobs)', st.d.enabled && st.d.jobs === true, st.d);
  ok('Reiter „Aufträge“ im KI-Bereich', await openJobs());
  const tab = await p.evaluate(() => [...document.querySelectorAll('.ai-tab')].map((b) => [b.textContent, !b.hidden, b.getAttribute('aria-selected')]));
  ok('Reiter sichtbar und gewählt', tab.some((x) => x[0] === 'Aufträge' && x[1] && x[2] === 'true'), tab);
  ok('Platzhalter „Sag … was du brauchst …“', /^Sag .+, was du brauchst …$/.test(await p.getAttribute('.kj-ta', 'placeholder')));

  ok('Plan-Karte erscheint', await give('Kümmer dich um die Grillkohle bis Freitag'));
  const c = await p.evaluate(() => {
    const k = document.querySelector('.kj-plan');
    return { btns: [...k.querySelectorAll('.kj-actions button')].map((b) => b.textContent), state: (k.querySelector('.kj-state') || {}).textContent,
             head: (k.querySelector('.kj-who') || {}).textContent, steps: [...k.querySelectorAll('.kj-step')].map((s) => ({ t: s.querySelector('.kj-step-t').textContent,
               cls: s.querySelector('.kj-cls').textContent, sym: s.querySelector('.kj-sym').textContent, aria: s.getAttribute('aria-label') })),
             read: (k.querySelector('.kj-read') || {}).textContent || '', lead: k.querySelector('.kj-lead').textContent,
             focusInCard: k.contains(document.activeElement), report: !!k.querySelector('.ai-report'),
             gap: (() => { const a = k.querySelector('.kj-go').getBoundingClientRect(), b = k.querySelector('.kj-no').getBoundingClientRect(); return b.left - a.right; })() };
  });
  ok('genau drei Knöpfe: Passt, los · Ändern · Lieber nicht', JSON.stringify(c.btns) === JSON.stringify(['Passt, los', 'Ändern', 'Lieber nicht']), c.btns);
  ok('KI-Kopf mit KI-Marke („… · KI“ bzw. Standardname „Saga KI“) + Zustandswort „Wartet auf dein OK“', /(· KI| KI)$/.test(c.head) && !/KI · KI/.test(c.head) && c.state === 'Wartet auf dein OK', c);
  ok('Schritt mit Art-Symbol ✎ und Klasse als Wort', c.steps.length === 1 && c.steps[0].sym === '✎' && c.steps[0].cls === 'nur löschbar' && /Grillkohle/.test(c.steps[0].t), c.steps);
  ok('Screenreader: „Schritt 1 von 1, Eigenes ändern, nur löschbar: …“', /^Schritt 1 von 1, Eigenes ändern, nur löschbar: Aufgabe anlegen/.test(c.steps[0].aria), c.steps[0].aria);
  ok('„Ich lese dafür: …“ vom Server', /^Ich lese dafür: .*Sommerfest/.test(c.read), c.read);
  ok('Neue Karte nimmt nicht den Fokus', !c.focusInCard);
  ok('„Lieber nicht“ mit Abstand zum Primärknopf (≥ 16 px)', c.gap >= 16, c.gap);
  ok('Melden … an der Plan-Karte (2258a)', c.report);
  const jid = await p.evaluate(() => +document.querySelector('.kj-plan').dataset.job);
  await shot('plan-dunkel');
  await p.focus('.kj-go'); await p.keyboard.press('Enter'); await sleep(500);
  ok('Enter auf „Passt, los“ gibt nicht frei', (await api('GET', '/api/ai/jobs/' + jid)).d.state === 'wartet_ok');
  // Aendern
  await p.click('.kj-edit');
  ok('Ändern macht den Text bearbeitbar', await until(() => p.evaluate(() => !!document.querySelector('.kj-editbox input'))));
  await p.fill('.kj-editbox input[type=text]', 'Grillkohle (3 Säcke) kaufen');
  await p.click('.kj-save');
  ok('nach dem Speichern: „geändert – bitte neu freigeben“', await until(() => p.evaluate(() => /geändert – bitte neu freigeben/.test((document.querySelector('.kj-plan .kj-note') || {}).textContent || ''))));
  ok('neuer plan_v auf dem Server', (await api('GET', '/api/ai/jobs/' + jid)).d.plan.plan_v === 2);
  // hell
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('plan-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  // Passt, los -> Rueckmeldung
  await p.click('.kj-go');
  ok('Freigabe: Pille „Läuft“ (oder schon die Rückmeldung, wenn der Auftrag sofort fertig ist)', await until(() => p.evaluate(() =>
    /Läuft/.test((document.querySelector('.kj-stage .kj-state') || {}).textContent || '') || !!document.querySelector('.kj-stage .kj-report'))));
  const fb = await until(() => p.evaluate(() => { const r = document.querySelector('.kj-stage .kj-report'); return r && { state: r.querySelector('.kj-state').textContent,
    res: r.querySelector('.kj-result').textContent, undo: !!r.querySelector('.kj-undo'), h: r.querySelector('.kj-undo') && r.querySelector('.kj-undo').getBoundingClientRect().height,
    line: (r.querySelector('.kj-change-t') || {}).textContent }; }), 15000);
  ok('Rückmeldung: Ergebnis zuerst („Erledigt: …“)', fb && fb.state === 'Erledigt' && fb.res.trim().length > 0, fb);
  ok('je Änderung eine Zeile mit „Rückgängig“ (Knopf ≥ 44 px)', fb && fb.undo && fb.h >= 44 && /3 Säcke.*angelegt/.test(fb.line), fb);
  await shot('rueckmeldung-dunkel');
  await p.emulateMedia({ colorScheme: 'light' }); await sleep(300); await shot('rueckmeldung-hell'); await p.emulateMedia({ colorScheme: 'dark' });
  const tasks1 = (await api('GET', '/api/tasks')).d;
  const list1 = JSON.stringify(tasks1);
  ok('Aufgabe genau einmal angelegt', (list1.match(/3 Säcke/g) || []).length === 1, list1.slice(0, 300));
  await p.click('.kj-undo');
  ok('nur löschbar: Rückfrage vor dem Löschen', await until(() => p.evaluate(() => !!document.querySelector('.hk-ask-box'))));
  await p.click('.hk-ask-box .btn-destr');
  ok('Rückgängig -> „Zurückgenommen“', await until(() => p.evaluate(() => /Zurückgenommen/.test((document.querySelector('.kj-stage .kj-report') || {}).textContent || ''))));
  ok('Aufgabe wieder weg', await until(async () => !/3 Säcke/.test(JSON.stringify((await api('GET', '/api/tasks')).d))));
  // Lieber nicht
  await give('Erinnere mich Freitag an die Grillkohle');
  await p.click('.kj-no');
  ok('Lieber nicht -> „Abgebrochen – nichts ist passiert.“', await until(() => p.evaluate(() => /Abgebrochen – nichts ist passiert\./.test((document.querySelector('.kj-stage') || {}).textContent || ''))));
  // Absage mit Alternative
  ok('Absage erscheint', await give('Bestell die Kohle in einem Onlineshop'));
  const rf = await p.evaluate(() => { const k = document.querySelector('.kj-refuse'); return k && { t: k.querySelector('.kj-lead').textContent, b: [...k.querySelectorAll('button')].map((x) => x.textContent) }; });
  ok('Absage: Satz + „Als Aufgabe anlegen“ + „Anders formulieren“', rf && rf.t.trim().length > 0 && rf.b.includes('Als Aufgabe anlegen') && rf.b.includes('Anders formulieren'), rf);
  await shot('absage-dunkel');
  await p.click('.kj-refuse .btn-primary');
  ok('„Als Aufgabe anlegen“ -> Plan-Karte', await until(() => p.evaluate(() => !!document.querySelector('.kj-stage .kj-plan'))));
  await p.click('.kj-no'); await sleep(300);
  // Rueckfrage
  await api('POST', '/api/notes', { title: 'Sommerfest' });
  ok('Rückfrage erscheint', await give('Trag bei Sommerfest ein, dass Ben die Grillkohle mitbringt.'));
  const chips = await p.evaluate(() => [...document.querySelectorAll('.kj-ask .kj-chip')].map((x) => x.textContent));
  ok('Rückfrage mit zwei Chips', chips.length === 2, chips);
  await shot('rueckfrage-dunkel');
  await p.click('.kj-ask .kj-chip >> nth=0');
  ok('Chip -> Plan für genau diese Seite', await until(() => p.evaluate(() => !!document.querySelector('.kj-stage .kj-plan .kj-draft'))));
  await p.click('.kj-no'); await sleep(300);
  // Handy: Knoepfe untereinander, Primaerknopf volle Breite
  await p.setViewportSize({ width: 390, height: 844 });
  await give('Erinnere mich Freitag an die Grillkohle');
  const mob = await p.evaluate(() => { const g = document.querySelector('.kj-go').getBoundingClientRect(), k = document.querySelector('.kj-plan').getBoundingClientRect();
    return { w: g.width / k.width, h: g.height, over: document.documentElement.scrollWidth > window.innerWidth + 1 }; });
  ok('Handy: „Passt, los“ volle Breite, ≥ 44 px, kein waagrechter Überlauf', mob.w > 0.8 && mob.h >= 44 && !mob.over, mob);
  await p.click('.kj-no'); await sleep(300);
  ok('keine Skriptfehler', !errs.length, errs);
  await br.close();
  const bad = R.filter((x) => !x[1]).length;
  console.log(`\nKollegin Plan-Karte (Browser) ${R.length - bad}/${R.length}` + (bad ? ' - FEHLER' : ' - ALLES GRUEN'));
  process.exit(bad ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
