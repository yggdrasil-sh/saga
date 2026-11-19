// 2.5 (Vorgabe 10.11.): Gjallarhorn ist in Web und Admin als "Experimentell" gekennzeichnet - ruhig, ohne Banner ueber
// dem Anruf. Laeuft aus tests/horn_experimentell.py (Admin-Cookie) bzw. in der Testumgebung, Art knoten:
//   UMG_NS=.. UMG_DB=.. tools/testumgebung/umgebung.sh lauf knoten <slot> horn_experimentell.py
// Geprueft (HX_VORHER=1: nur Bilder, fuer den Vergleich mit einem Stand ohne Marke):
//  - Admin -> Anrufe: Marke an der Ueberschrift "Gjallarhorn" und an "Knoten", ein Hinweis-Kasten, keine Konsolenfehler,
//    kein waagrechtes Scrollen bei 390 px; Bilder hell/dunkel (1280) und hell (390)
//  - Web: 1:1 A (1280) <-> B (390) ueber den eingebauten Knoten (calls.horn + horn_all): kleine Marke im Kopf neben dem
//    Weg (Tooltip mit dem Satz), kein Banner; in der Teilnehmerliste der Satz unten; Bilder Kopf und Teilnehmerliste
//   SAGA_BASE SAGA_USER/PASS SAGA_USER2/PASS2 SAGA_ADMIN SAGA_ADMIN_COOKIE SHOTS [HX_PREFIX=nachher] [HX_VORHER=1]
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, ADMIN = E.SAGA_ADMIN, SHOTS = E.SHOTS || '/tmp', PRE = E.HX_PREFIX || 'nachher';
const VORHER = E.HX_VORHER === '1';
if (!BASE || !ADMIN || !E.SAGA_ADMIN_COOKIE || !E.SAGA_USER || !E.SAGA_USER2) { console.error('SAGA_BASE, SAGA_ADMIN(_COOKIE), SAGA_USER/2 setzen'); process.exit(2); }
const SATZ = 'Gjallarhorn ist in Saga 2.5 experimentell – getestet mit bis zu 4 Personen bzw. Geräten je Anruf.';
const res = [];
function ok(name, cond, info) { if (VORHER) return; res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1200))); }
async function until(fn, ms = 20000, step = 150) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const shots = [];
async function shot(p, name, full) { const f = path.join(SHOTS, PRE + '-' + name + '.png'); await p.screenshot({ path: f, fullPage: !!full }); shots.push(path.basename(f)); }
let br;
async function ctxFor(phone, scheme) {
  return br.newContext(phone ? { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, locale: 'de-DE', colorScheme: scheme || 'dark' }
    : { viewport: { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: scheme || 'dark' });
}
async function session(user, pass, name, phone) {
  const ctx = await ctxFor(phone);
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage(); p.__errs = [];
  p.on('pageerror', e => { p.__errs.push(e.message); console.log('  [pageerror ' + name + '] ' + e.message); });
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
const horn = p => p.evaluate(() => window.HKCalls && window.HKCalls.horn && window.HKCalls.horn());

async function adminPage(scheme, phone) {
  const ctx = await ctxFor(phone, scheme), u = new URL(ADMIN);
  await ctx.addCookies([{ name: 'hk_admin', value: E.SAGA_ADMIN_COOKIE, domain: u.hostname, path: '/' }]);
  const p = await ctx.newPage(); p.__errs = [];
  p.on('console', m => { if (m.type() === 'error') p.__errs.push(m.text()); });
  p.on('pageerror', e => p.__errs.push(String(e)));
  const r = await p.goto(ADMIN + '/calls', { waitUntil: 'load' });
  p.__status = r.status();
  return p;
}

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true,
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  try {
    // --- Admin -> Anrufe ------------------------------------------------------------------------------------------------
    for (const [scheme, phone] of [['light', false], ['dark', false], ['light', true]]) {
      const p = await adminPage(scheme, phone), tag = (phone ? '390' : '1280') + '-' + (scheme === 'dark' ? 'dunkel' : 'hell');
      const st = await p.evaluate(() => {
        const h = document.querySelector('#horn h3'), n = [...document.querySelectorAll('#horn h4.horn-h')].find(x => /^Knoten/.test(x.textContent.trim()));
        const box = document.querySelector('#horn .horn-exp-note');
        return { h: h && !!h.querySelector('.adm-tag.exp'), n: n && !!n.querySelector('.adm-tag.exp'), box: box ? box.textContent : '',
                 ow: document.documentElement.scrollWidth - window.innerWidth, tags: document.querySelectorAll('.adm-tag.exp').length };
      });
      ok(`Admin ${tag}: Marke an "Gjallarhorn" und "Knoten", Hinweis-Kasten, keine Konsolenfehler, kein waagrechtes Scrollen`,
        p.__status === 200 && st.h && st.n && st.box.includes(SATZ) && st.box.includes('Größere Anrufe folgen nach 2.5.') && !p.__errs.length && st.ow <= 1,
        { st, errs: p.__errs });
      await shot(p, 'admin-anrufe-' + tag);
      await p.locator('#horn').scrollIntoViewIfNeeded().catch(() => {});
      await p.evaluate(() => { const h = document.querySelector('#horn'); if (h) window.scrollTo(0, h.getBoundingClientRect().top + window.scrollY - 8); });
      await shot(p, 'admin-gjallarhorn-' + tag);
      await p.context().close();
    }

    // --- Web: 1:1 ueber den Knoten ----------------------------------------------------------------------------------------
    const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
    const a = await session(U1, E.SAGA_PASS, 'A', false), b = await session(U2, E.SAGA_PASS2, 'B', true);
    await chatKeys(a, U1); await chatKeys(b, U2);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1000);
    await b.goto(BASE + '/chats'); await b.waitForTimeout(1000);
    await a.click('#ct-call-v');
    await b.waitForSelector('.cl-ring', { timeout: 20000 }); await b.click('.cl-ring .cl-big');
    const up = await until(async () => { const x = await horn(a), y = await horn(b); return x && x.up && y && y.up; }, 30000);
    ok('1:1 A (1280) <-> B (390) laeuft ueber den Knoten', up, [await horn(a), await horn(b)]);
    await until(() => a.evaluate(() => !!document.querySelector('.cl-info .cl-hpath')), 10000);
    await a.waitForTimeout(1500); await b.waitForTimeout(500);
    for (const [q, w] of [[a, '1280'], [b, '390']]) {
      const s = await q.evaluate(() => {
        const x = document.querySelector('.cl-info .cl-exp'), r = x && x.getBoundingClientRect();
        return { has: !!x, txt: x ? x.textContent : '', title: x ? x.title : '', vis: !!(r && r.width && r.height),
                 banner: !!document.querySelector('.cl-win .cl-banner-exp, .cl-win > .cl-exp') };
      });
      ok(`Web ${w}: kleine Marke "Experimentell" im Kopf (sichtbar, Tooltip mit dem Satz), kein Banner`,
        s.has && s.txt === 'Experimentell' && s.vis && s.title.includes(SATZ) && s.title.includes('Diagnose senden') && !s.banner, s);
      await shot(q, 'web-anruf-' + w + '-dunkel');
    }
    await a.click('.cl-ctl .cl-people');
    await a.waitForSelector('.cl-plist', { timeout: 5000 }).catch(() => {});
    const pl = await a.evaluate(() => { const x = document.querySelector('.cl-plist .cl-pl-exp'); return x ? x.textContent : ''; });
    ok('Web 1280: Teilnehmerliste mit dem Satz unten', pl.includes('Experimentell') && pl.includes(SATZ), pl);
    await shot(a, 'web-teilnehmende-1280-dunkel');
    if (await b.$('.cl-ctl .cl-people')) await b.click('.cl-ctl .cl-people');
    else { await b.click('.cl-ctl .cl-more'); await b.click('.cl-moremenu .cl-people'); }
    await b.waitForSelector('.cl-plist', { timeout: 5000 }).catch(() => {});
    const pl2 = await b.evaluate(() => { const x = document.querySelector('.cl-plist .cl-pl-exp'), l = document.querySelector('.cl-plist');
      return { t: x ? x.textContent : '', ow: l ? l.scrollWidth - l.clientWidth : -1 }; });
    ok('Web 390: Teilnehmerliste mit dem Satz, ohne waagrechtes Scrollen', pl2.t.includes(SATZ) && pl2.ow <= 1, pl2);
    await shot(b, 'web-teilnehmende-390-dunkel');
    ok('Web: keine Seitenfehler', !a.__errs.length && !b.__errs.length, [a.__errs, b.__errs]);
  } catch (e) {
    if (VORHER) console.log('Ablauf: ' + String(e && e.stack || e)); else ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    await br.close();
    console.log('Bilder: ' + shots.join(' '));
    if (VORHER) { console.log(`\nHORN-EXPERIMENTELL Bilder ${shots.length} (vorher, ohne Pruefung)`); process.exit(0); }
    const n = res.filter(Boolean).length;
    console.log(`\nHORN-EXPERIMENTELL Browser ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
