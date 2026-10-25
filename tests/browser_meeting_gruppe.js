// Browser-Ende-zu-Ende Meeting-Ansicht in der Gruppe (MEETING-SPEC.md S4, S6, S10, A1) - vier Personen, je ein eigener
// Chromium mit Kamera/Mikrofon-Attrappe. Laeuft ueber Mesh (lokaler Test-Stack) und ueber einen Knoten (Testlabor, Gruppe
// ab calls.horn_from) - geprueft wird, was der jeweilige Weg zeigt:
//   SAGA_BASE=https://… SAGA_USERS=u1,u2,u3,u4 SAGA_PASS=… [SHOTS=/pfad] [SHOT_PREFIX=meet-lab-] [CHROME=/usr/bin/chromium] \
//   [PLAYWRIGHT=…/playwright-core] node tests/browser_meeting_gruppe.js
// Person 3 schaut mit 1280x720 zu, Person 4 mit Handy-Breite (390x844, Touch). Ablauf: Gruppe anlegen, Videoanruf,
// alle treten bei; Mesh: Auto = Galerie mit vier Kacheln (eigene zuletzt, nicht schwebend); Knoten: Kacheln je Person;
// Leisten blenden nach 4 s aus; Person 2 teilt den Bildschirm: Praesentation (Mesh) bzw. Bildschirm gross (Knoten), Hinweis-
// Chip unten links statt Banner, kein Kamera-Bild-im-Bild; Layout-Knopf; Sprecher per Kuerzel P; Handy ohne waagrechtes
// Scrollen. Screenshots hell/dunkel, Desktop und Handy (SHOTS).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, USERS = (E.SAGA_USERS || '').split(',').filter(Boolean);
if (!BASE || USERS.length < 4 || !E.SAGA_PASS) { console.error('SAGA_BASE, SAGA_USERS=u1,u2,u3,u4, SAGA_PASS setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PFX = E.SHOT_PREFIX || 'meet-gruppe-';
const res = [], INFO = {};
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 1500))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PFX + name + '.png') }); } }
async function until(fn, ms = 20000, step = 200) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const sleep = ms => new Promise(r => setTimeout(r, ms));
const SCREEN_HOOK = () => {
  try { localStorage.setItem('saga-screen-hint', '0'); localStorage.removeItem('saga-call-layout'); } catch (e) { /* egal */ }
  const md = navigator.mediaDevices; if (!md) return;
  md.getDisplayMedia = async () => {
    const c = document.createElement('canvas'); c.width = 1280; c.height = 720;
    const g = c.getContext('2d'), cols = ['#e6e6e6', '#e6e600', '#00e6e6', '#00e600', '#e600e6', '#e60000', '#0000e6'];
    let n = 0;
    setInterval(() => { cols.forEach((x, i) => { g.fillStyle = x; g.fillRect(i * c.width / 7, 0, c.width / 7 + 1, c.height); }); g.fillStyle = '#000'; g.font = '48px sans-serif'; g.fillText('Demo ' + (n++ % 100), 40, 680); }, 100);
    const s = c.captureStream(15); s.getVideoTracks()[0].contentHint = 'detail';
    return s;
  };
};
const BROWSERS = [];
async function session(user, i) {
  const br = await chromium.launch({ headless: true, ...(E.CHROME ? { executablePath: E.CHROME } : { channel: E.PW_CHANNEL || 'chrome' }),
    args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required',
           ...(BASE.startsWith('http:') ? ['--unsafely-treat-insecure-origin-as-secure=' + new URL(BASE).origin] : [])] });
  BROWSERS.push(br);
  const phone = i === 3;
  const ctx = await br.newContext({ viewport: phone ? { width: 390, height: 844 } : i === 2 ? { width: 1280, height: 720 } : { width: 1100, height: 700 },
    hasTouch: phone, isMobile: phone, locale: 'de-DE', colorScheme: 'dark' });
  await ctx.grantPermissions(['camera', 'microphone'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('  [pageerror ' + user + '] ' + e.message));
  await p.addInitScript(SCREEN_HOOK);
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', E.SAGA_PASS);
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
const st = p => p.evaluate(() => {
  const r = window.HKCalls && window.HKCalls.room(), h = window.HKCalls && window.HKCalls.horn && window.HKCalls.horn();
  const w = document.querySelector('.cl-win'), op = s => { const x = w && w.querySelector(s); return x ? +getComputedStyle(x).opacity : -1; };
  const share = w && w.querySelector('.cl-share');
  return r && { mesh: r.links.filter(l => l.connected).length, horn: !!(h && h.up), hornTiles: h ? h.tiles.length : 0, meet: r.meet, tiles: r.tiles,
    idle: !!w && w.classList.contains('idle'), top: op('.cl-top'), ctl: op('.cl-ctl'), cls: w ? w.className : '',
    layoutBtn: !!(w && w.querySelector('.cl-ctl .cl-layout')), bar: share && !share.hidden ? share.textContent : '', barCls: share ? share.className : '',
    chip: (w && w.querySelector('.cl-mchip:not([hidden])') || {}).textContent || '', tv2: !!(w && w.querySelector('.cl-tile.has-small, .cl-win.has-small')),
    scrollX: document.documentElement.scrollWidth > innerWidth + 1, selfFloat: !!(w && w.querySelector('.cl-local') && getComputedStyle(w.querySelector('.cl-local')).display !== 'none' && !w.querySelector('.cl-local').hidden) };
});

(async () => {
  try {
    const ps = [];
    for (let i = 0; i < 4; i++) ps.push(await session(USERS[i], i));
    for (let i = 0; i < 4; i++) await chatKeys(ps[i], USERS[i]);
    const d0 = await ps[0].evaluate(() => window.HKChat.state().device);
    const mk = await ps[0].evaluate(([m, d]) => window.HK.api('POST', '/api/chats', { kind: 'free', members: m, device: d }).then(r => r, e => ({ err: e.message })), [USERS.slice(1, 4), d0]);
    ok('freie Gruppe mit vier Personen angelegt', mk && mk.chat, mk);
    const gid = mk.chat.id;
    for (const p of ps) await p.goto(BASE + '/chats#c' + gid);
    await ps[0].waitForSelector('#ct-chat:not([hidden])'); await liveOk(...ps);
    await until(() => ps[0].evaluate(() => { const v = document.getElementById('ct-call-v'); return v && !v.hidden && /Gruppe/.test(v.title); }), 10000);
    await ps[0].click('#ct-call-v');
    await sleep(1500);
    const hint = await ps[0].evaluate(() => { const h = document.querySelector('.cl-hint'); return h && h.textContent; });
    if (hint) { console.log('  Hinweis vor dem Start: ' + hint.slice(0, 200)); const go = await ps[0].$('.cl-hint .cl-hint-go'); if (go) await go.click(); }
    if (E.DEBUG) await ps[0].screenshot({ path: path.join(os.tmpdir(), 'gruppe-start.png') });
    for (let i = 1; i < 4; i++) { await ps[i].waitForSelector('.cl-ring', { timeout: 30000 }); await ps[i].click('.cl-ring .cl-big'); await sleep(500); }
    const all = await until(async () => { const s = await Promise.all(ps.map(st)); return s.every(x => x && (x.horn || x.mesh === 3)) && s; }, 60000, 500);
    ok('alle vier verbunden (' + (all && all[0].horn ? 'Knoten' : 'Mesh') + ')', all, await Promise.all(ps.map(st)));
    const horn = !!(all && all[0].horn); INFO.weg = horn ? 'knoten' : 'mesh';
    await sleep(2500);
    let s3 = await st(ps[2]);
    if (horn) ok('Knoten: je andere Person eine Kachel (3), eigene Kachel im Raster', s3.hornTiles === 3 && /horn-self/.test(s3.cls), s3);
    else ok('S4: Auto = Galerie mit vier Kacheln, eigene zuletzt, nicht schwebend', s3.meet && s3.meet.layout === 'gallery' && s3.meet.grid.length === 4
         && s3.meet.grid[3] === s3.meet.els.find(x => x.me).id && !s3.selfFloat, s3.meet);
    ok('Layout-Knopf sichtbar (Gruppe)', s3.layoutBtn, s3);
    await ps[2].mouse.move(600, 300);
    const idle = await until(async () => { const x = await st(ps[2]); return x.idle && x.top === 0 && x.ctl === 0 && x; }, 8000);
    ok('A1: Leisten nach 4 s ausgeblendet', idle, await st(ps[2]));
    await shot(ps[2], '1-gruppe-ausgeblendet-dunkel');
    await ps[2].mouse.move(620, 320);
    await until(async () => !(await st(ps[2])).idle, 3000);
    await shot(ps[2], '2-gruppe-dunkel');
    await ps[2].emulateMedia({ colorScheme: 'light' }); await shot(ps[2], '2-gruppe-hell'); await ps[2].emulateMedia({ colorScheme: 'dark' });
    await shot(ps[3], '3-gruppe-handy-dunkel');
    ok('A3: Handy ohne waagrechtes Scrollen', !(await st(ps[3])).scrollX);

    // Person 2 teilt den Bildschirm
    await ps[1].mouse.move(500, 300);
    await ps[1].click('.cl-ctl .cl-round[aria-label="Bildschirm teilen"]');
    const sh = await until(async () => { const x = await st(ps[2]); return (horn ? /horn-scr/.test(x.cls) : x.meet && x.meet.layout === 'presentation' && /:scr$/.test(x.meet.stage || '')) && x; }, 20000);
    ok('S6: Betrachtende sehen den Bildschirm gross (' + (horn ? 'Knoten' : 'Praesentation') + ')', sh, await st(ps[2]));
    const s6 = await st(ps[2]);
    ok('S6: Hinweis-Chip "… teilt den Bildschirm" (kein Banner oben), kein Kamera-Bild-im-Bild',
       (horn ? /teilt den Bildschirm/.test(s6.bar) && /cap/.test(s6.barCls) : /teilt den Bildschirm/.test(s6.chip) && !s6.bar) && !s6.tv2, s6);
    const own = await st(ps[1]);
    ok('S6: Teilende sieht die rote Leiste "Du teilst deinen Bildschirm"', /Du teilst deinen Bildschirm/.test(own.bar) && /\bme\b/.test(own.barCls), own.bar);
    await ps[2].mouse.move(600, 300); await until(async () => (await st(ps[2])).idle, 8000);
    await shot(ps[2], '4-bildschirm-ausgeblendet-dunkel');
    await ps[2].mouse.move(610, 310); await sleep(400);
    await shot(ps[2], '5-bildschirm-dunkel');
    await ps[2].emulateMedia({ colorScheme: 'light' }); await shot(ps[2], '5-bildschirm-hell'); await ps[2].emulateMedia({ colorScheme: 'dark' });
    await shot(ps[1], '6-teilende-dunkel');
    await sleep(800);
    await shot(ps[3], '7-bildschirm-handy-dunkel');
    await ps[3].emulateMedia({ colorScheme: 'light' }); await shot(ps[3], '7-bildschirm-handy-hell');
    ok('S10: Handy beim Teilen ohne waagrechtes Scrollen', !(await st(ps[3])).scrollX);
    // Sprecher per Kuerzel P
    await ps[2].keyboard.press('p'); await sleep(800);
    const sp = await st(ps[2]);
    ok('Kuerzel P: Sprecher gewaehlt', (await ps[2].evaluate(() => localStorage.getItem('saga-call-layout'))) === 'speaker'
       && (horn ? /horn-strip/.test(sp.cls) : sp.meet && sp.meet.layout === 'speaker'), sp);
    await shot(ps[2], '8-sprecher-dunkel');
    await ps[2].keyboard.press('a');
    await ps[0].click('.cl-ctl .cl-red').catch(() => {});
    for (let i = 1; i < 4; i++) await ps[i].click('.cl-ctl .cl-red').catch(() => {});
  } catch (e) {
    ok('Ablauf', false, String(e && e.stack || e));
  } finally {
    for (const br of BROWSERS) await br.close().catch(() => {});
    console.log('INFO ' + JSON.stringify(INFO));
    const n = res.filter(Boolean).length;
    console.log(`\nBROWSER-MEETING-GRUPPE ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
    process.exit(n === res.length ? 0 : 1);
  }
})();
