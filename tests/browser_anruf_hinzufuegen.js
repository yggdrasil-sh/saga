// Browser-Ende-zu-Ende fuer 2.5 L8 "Person hinzufuegen" (static/calls.js, Feld ueber der Teilnehmerliste) - drei
// Playwright-Browser im headless Chromium mit Kamera/Mikrofon-Attrappe:
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... SAGA_USER3=d SAGA_PASS3=... \
//   SAGA_USER4=c (Mitglied ohne Browser) SAGA_NAME3='Dora …' [KNOTEN=1] [SHOTS=/pfad] [SHOT_PREFIX=nachher|vorher] [VORHER=1] \
//   [PLAYWRIGHT=...] [PW_CHANNEL=chromium] node tests/browser_anruf_hinzufuegen.js
// KNOTEN=1: Server mit Gjallarhorn und bereitem Knoten (calls.horn, calls.horn_from 3) - der Gruppenanruf laeuft ueber
// den Knoten (topo horn); ohne: direkt (Mesh). Selbst signierter Knoten: Browser mit --ignore-certificate-errors.
// Geprueft: freie Gruppe A, B, C - A (Admin) und B im Anruf: A hat in der Teilnehmerliste "Person hinzufuegen" (aria-label),
// B (kein Admin) nicht; Feld mit Fokus im Suchfeld, Hinweis; Suche nach D (nicht im Chat) -> Klick: Hinweis "wird angerufen",
// D klingelt (Leiste), nimmt an und ist verbunden (Mesh: Verbindungen zu A und B geprueft; Knoten: verbunden, Frame-
// Schluessel von A und B bekannt), A sieht drei Personen; Escape schliesst das Feld, Fokus zurueck auf den Knopf.
// Seiten-Chat (A besitzt die Seite, geteilt mit B): Hinzufuegen von D fragt "D bekommt Zugriff auf die Seite „…“" ->
// Abbrechen: nichts geteilt; Teilen und anrufen: Seite geteilt (Lesen), D klingelt. Bilder: Liste mit Knopf, Feld
// (dunkel/hell, schmal 390 px), Rueckfrage im Seiten-Chat. VORHER=1: nur das Bild der Liste (Stand ohne L8).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_USER2 || !E.SAGA_USER3 || !E.SAGA_USER4) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2, SAGA_USER3/PASS3, SAGA_USER4 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PRE = (E.SHOT_PREFIX || 'nachher') + (E.KNOTEN === '1' ? '-knoten' : '-mesh'), OLD = !!E.VORHER, KN = E.KNOTEN === '1';
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PRE + '-' + name + '.png') }); } }
const BROWSERS = [];
async function session(user, pass, name) {
  const origin = new URL(BASE).origin;
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--unsafely-treat-insecure-origin-as-secure=' + origin,
           '--autoplay-policy=no-user-gesture-required'].concat(KN ? ['--ignore-certificate-errors'] : []) });
  BROWSERS.push(br);
  const ctx = await br.newContext({ viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: 'dark', ignoreHTTPSErrors: true });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.__name = name;
  p.on('pageerror', e => console.log('  [pageerror ' + name + '] ' + e.message));
  if (E.DEBUG) p.on('console', m => console.log('  [console ' + name + '] ' + m.text()));
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(BASE + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(800);
}
async function liveOk(...ps) { for (const p of ps) await until(() => p.evaluate(() => !!(window.HKLive && window.HKLive.state().sse)), 15000, 200); }
const room = p => p.evaluate(() => window.HKCalls && window.HKCalls.room());
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn());
const dev = p => p.evaluate(() => window.HKChat.state().device);
async function until(fn, ms = 20000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
async function api(p, m, url, body) { return p.evaluate(([m, url, body]) => window.HK.api(m, url, body).then(r => ({ ok: true, r }), e => ({ ok: false, status: e.status, data: e.data, msg: e.message })), [m, url, body]); }
async function openList(p) {
  if (await p.$('.cl-plist:not(.cl-addp)')) return true;
  if (await p.$('.cl-ctl .cl-people')) await p.click('.cl-ctl .cl-people');
  else if (await p.$('.cl-ctl .cl-more')) { await p.click('.cl-ctl .cl-more'); await p.waitForSelector('.cl-moremenu .cl-people', { timeout: 3000 }); await p.click('.cl-moremenu .cl-people'); }
  return !!(await until(() => p.$('.cl-plist:not(.cl-addp)'), 4000));
}
async function theme(p, light) {
  await p.emulateMedia({ colorScheme: light ? 'light' : 'dark' });
  await p.evaluate(l => { document.documentElement.dataset.theme = l ? 'light' : 'dark'; }, light);
  await p.waitForTimeout(300);
}
const toastHas = (p, re) => until(() => p.evaluate(src => { const t = [...document.querySelectorAll('.toast')].find(x => !x.hidden && new RegExp(src).test(x.textContent)); return t ? t.textContent : null; }, re.source), 6000);
async function startCall(a, b, cid) {
  for (const p of [a, b]) await p.goto(BASE + '/chats#c' + cid);
  await a.waitForSelector('#ct-chat:not([hidden])'); await liveOk(a, b);
  await until(() => a.evaluate(() => { const v = document.getElementById('ct-call-a'); return v && !v.hidden && /Gruppen/.test(v.title) && v; }), 10000);
  await a.click('#ct-call-a');
  await b.waitForSelector('.cl-ring', { timeout: 15000 });
  await b.click('.cl-ring .cl-big');
  return until(async () => { const r = await room(b); return r && r.inRoom && r; }, 20000);
}
async function hangup(...ps) { for (const p of ps) { const h = await p.$('.cl-ctl .cl-red'); if (h) await h.click().catch(() => {}); } }
async function search(a, name) {
  await a.fill('.cl-addp .cl-add-q', name.split(' ')[0]);
  return until(() => a.evaluate(nm => [...document.querySelectorAll('.cl-addp .cl-add-p')].some(b => b.querySelector('b').textContent === nm), name), 6000);
}
const pickRow = (a, name) => a.evaluate(nm => { const b = [...document.querySelectorAll('.cl-addp .cl-add-p')].find(x => x.querySelector('b').textContent === nm); if (b) b.click(); return !!b; }, name);

(async () => {
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2, U3 = E.SAGA_USER3, U4 = E.SAGA_USER4;
  const ND = E.SAGA_NAME3 || 'Dora Neu';
  try {
    const a = await session(U1, E.SAGA_PASS, 'A'), b = await session(U2, E.SAGA_PASS2, 'B'), d = await session(U3, E.SAGA_PASS3, 'D');
    await chatKeys(a, U1); await chatKeys(b, U2); await chatKeys(d, U3);
    const dA = await dev(a);
    ok('drei Konten mit Chat-Schluesseln', dA && await dev(b) && await dev(d));
    const mk = await api(a, 'POST', '/api/chats', { kind: 'free', members: [U2, U4], device: dA });
    ok('freie Gruppe A, B, C angelegt (D nicht dabei)', mk.ok, mk);
    const gid = mk.r.chat.id;
    await d.goto(BASE + '/chats'); await liveOk(d);
    const rb = await startCall(a, b, gid);
    ok('A ruft an, B tritt bei', !!rb, rb);
    const ra = await until(async () => { const r = await room(a); return r && r.inRoom && r; }, 10000);
    ok(`Anruf laeuft ${KN ? 'ueber den Knoten' : 'direkt'} (topo ${ra && ra.topo})`, ra && (KN ? ra.topo === 'horn' : ra.topo !== 'horn'), ra && ra.topo);
    await a.waitForTimeout(1200);
    ok('A: Teilnehmerliste geoeffnet', await openList(a));
    await openList(b);
    if (OLD) {
      await a.waitForTimeout(600); await shot(a, 'liste-dunkel');
      ok('vorher: Bild der Liste (kein "Person hinzufuegen")', !(await a.$('.cl-add-go')));
    } else {
      const go = await until(() => a.evaluate(() => { const x = document.querySelector('.cl-plist .cl-add-go'); return x && x.getAttribute('aria-label'); }), 5000);
      ok(`A (Admin): Knopf "Person hinzufuegen" in der Teilnehmerliste (${go})`, go === 'Person hinzufügen');
      ok('B (kein Admin): kein Knopf', await until(() => b.evaluate(() => !!document.querySelector('.cl-plist') && !document.querySelector('.cl-add-go')), 4000));
      await shot(a, 'liste-dunkel');
      await a.focus('.cl-plist .cl-add-go'); await a.keyboard.press('Enter');
      const fq = await until(() => a.evaluate(() => { const i = document.activeElement; return i && i.matches('.cl-addp .cl-add-q') && document.querySelector('.cl-add-hint').textContent; }), 4000);
      ok(`Feld "Person hinzufuegen" offen, Fokus im Suchfeld, Hinweis (${fq})`, !!fq && /klingeln/.test(fq), fq);
      ok(`Suche nach "${ND}" findet die Person`, await search(a, ND));
      await theme(a, false); await shot(a, 'feld-dunkel');
      await theme(a, true); await shot(a, 'feld-hell');
      await a.setViewportSize({ width: 390, height: 844 }); await a.waitForTimeout(700); await shot(a, 'feld-schmal-hell');
      await a.setViewportSize({ width: 1280, height: 800 }); await theme(a, false); await a.waitForTimeout(500);
      if (!(await a.$('.cl-addp'))) { await openList(a); await a.click('.cl-plist .cl-add-go'); await search(a, ND); }
      ok('Klick auf die Person', await pickRow(a, ND));
      const t1 = await toastHas(a, /wird angerufen/);
      ok(`A: Hinweis "wird angerufen" (${t1})`, !!t1);
      const ringD = await until(() => d.$('.cl-ring'), 15000);
      ok('D klingelt (Leiste), obwohl D eben noch nicht im Chat war', !!ringD);
      await shot(d, 'd-klingelt');
      await d.click('.cl-ring .cl-big');
      const rd = await until(async () => { const r = await room(d); return r && r.inRoom && r; }, 20000);
      ok('D nimmt an und ist im Anruf', !!rd, rd);
      if (KN) {
        const hd = await until(async () => { const h = await horn(d); return h && h.up && h.known.length >= 2 && h; }, 30000, 300);
        ok(`D ueber den Knoten verbunden, Frame-Schluessel von A und B bekannt (${hd && hd.known.length})`, !!hd, hd && { up: hd.up, known: hd.known, errs: hd.errs });
      } else {
        const ld = await until(async () => { const r = await room(d); const L = (r && r.links || []).filter(x => !x.me); return L.length >= 2 && L.every(x => x.connected && x.verified) && L; }, 30000, 300);
        ok(`D direkt mit A und B verbunden, Gegenstellen geprueft (${ld && ld.length})`, !!ld, ld || (await room(d)).links);
      }
      const n3 = await until(async () => { const r = await room(a); return r && new Set(r.legs).size === 3 && r; }, 15000);
      ok('A sieht drei Endpunkte', !!n3);
      const ad = await until(() => a.evaluate(() => !!document.querySelector('.cl-addp')), 1000);
      if (ad) {
        await a.focus('.cl-addp .cl-add-q'); await a.keyboard.press('Escape');
        const back = await until(() => a.evaluate(() => !document.querySelector('.cl-addp') && document.activeElement && document.activeElement.matches('.cl-add-go')), 3000);
        ok('Escape schliesst das Feld, Fokus zurueck auf "Person hinzufuegen"', back);
      } else ok('Feld bleibt nach dem Hinzufuegen offen', false);
      await hangup(d, b, a); await a.waitForTimeout(1500);

      // --- Seiten-Chat: Rueckfrage "teilt die Seite" -------------------------------------------------------------------------
      const np = await api(a, 'POST', '/api/notes', { title: 'Projektplan Sommer' });
      const nid = np.r && (np.r.id || (np.r.note && np.r.note.id));
      await api(a, 'PUT', '/api/notes/' + nid + '/shares', { type: 'user', target: U2, role: 'view' });
      const pc = await api(a, 'POST', '/api/chats', { kind: 'page', note_id: nid, device: dA });
      ok('Seiten-Chat angelegt (Seite mit B geteilt)', pc.ok, pc);
      const rp = await startCall(a, b, pc.r.chat.id);
      ok('Anruf im Seiten-Chat, B tritt bei', !!rp);
      await a.waitForTimeout(1000); await openList(a);
      await until(() => a.$('.cl-plist .cl-add-go'), 5000); await a.click('.cl-plist .cl-add-go');
      const hint = await until(() => a.evaluate(() => { const h = document.querySelector('.cl-add-hint'); return h && h.textContent; }), 3000);
      ok(`Hinweis im Feld: Hinzufuegen teilt die Seite (${hint})`, /teilt die Seite „Projektplan Sommer“/.test(hint || ''), hint);
      const rows = await until(() => a.evaluate(() => { const r = [...document.querySelectorAll('.cl-addp .cl-add-p b')].map(x => x.textContent); return r.length && r; }), 5000);
      ok(`Auswahl ohne Personen, die schon im Anruf sind (${rows})`, rows && rows.includes(ND) && !rows.includes(E.SAGA_NAME2 || 'Bert Browser'), rows);
      await search(a, ND); await pickRow(a, ND);
      const q1 = await until(() => a.evaluate(() => { const x = document.querySelector('.cl-add-ask'); return x && x.textContent; }), 5000);
      ok(`Rueckfrage: "${ND} bekommt Zugriff auf die Seite „Projektplan Sommer“" (${q1})`, q1 && q1.includes(ND + ' bekommt Zugriff auf die Seite „Projektplan Sommer“'), q1);
      const top = await a.evaluate(() => { const r = document.querySelector('.cl-add-ask').getBoundingClientRect(); const e = document.elementFromPoint(r.x + r.width / 2, r.y + 10); return !!(e && e.closest('.cl-add-ask')) && document.activeElement.matches('.cl-add-ask .cl-hint-go'); });
      ok('... liegt ueber dem Anruffenster, Fokus auf "Teilen und anrufen"', top);
      await shot(a, 'seite-rueckfrage');
      await a.click('.cl-add-ask .cl-hint-no');
      await a.waitForTimeout(800);
      const sh0 = await api(a, 'GET', '/api/notes/' + nid + '/shares');
      const has = r => ((r && r.shares) || []).some(x => x.name === ND && x.role === 'view');
      ok('Abbrechen: nichts geteilt, D klingelt nicht', sh0.ok && !has(sh0.r) && !(await d.$('.cl-ring')), sh0);
      await pickRow(a, ND);
      await until(() => a.$('.cl-add-ask'), 5000); await a.keyboard.press('Enter');
      const t2 = await toastHas(a, /wird angerufen/);
      const ring2 = await until(() => d.$('.cl-ring'), 15000);
      const sh1 = await api(a, 'GET', '/api/notes/' + nid + '/shares');
      ok(`Teilen und anrufen: Seite geteilt, D klingelt (${t2})`, !!t2 && !!ring2 && has(sh1.r), sh1);
      if (ring2) await d.click('.cl-ring .cl-red');
      await hangup(b, a);
    }
    await a.waitForTimeout(800);
  } catch (e) {
    console.log('FEHLER ' + (e && e.stack || e));
    res.push(false);
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
  }
  console.log(`\nBROWSER-ANRUF-HINZUFUEGEN${KN ? ' (KNOTEN)' : ''} ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) && res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) && res.length ? 0 : 1);
})();
