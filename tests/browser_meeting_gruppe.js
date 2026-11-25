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
const CTLFIT = () => { const bs = [...document.querySelectorAll('.cl-win .cl-ctl > .cl-btn')].map(b => b.getBoundingClientRect());
  const red = document.querySelector('.cl-win .cl-ctl .cl-red'), rr = red && red.getBoundingClientRect();
  return { n: bs.length, inside: bs.every(r => r.left >= 0 && r.right <= innerWidth + 0.5), oneRow: bs.every(r => Math.abs(r.top - bs[0].top) < 2),
           red: !!rr && rr.left >= 0 && rr.right <= innerWidth && rr.width >= 44, more: !!document.querySelector('.cl-win .cl-ctl .cl-more') }; };
// Bugs-DB 2196: Handy quer (Hoehe <= 500) - Teilnehmerliste als Blatt links der senkrechten Leiste. Treffer-Test: an der Mitte jedes
// sichtbaren Knopfs der Leiste (und oben rechts) liegt bei offener Liste der Knopf selbst (elementFromPoint); ganz sichtbare Eintraege
// (2.6, Bugs-DB 2263: ohne den frueheren Hinweis "Experimentell" unter der Liste - geprueft per exp = 0)
const PLHIT = () => {
  const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); };
  const w = document.querySelector('.cl-win'), c = document.querySelector('.cl-win .cl-ctl'), p = document.querySelector('.cl-win .cl-plist');
  if (!c) return null;
  const bs = [...document.querySelectorAll('.cl-win .cl-ctl .cl-btn, .cl-win .cl-tb .cl-btn')]
    .filter(b => !b.hidden && b.getClientRects().length && getComputedStyle(b).display !== 'none' && getComputedStyle(b).visibility !== 'hidden');
  const hit = bs.map(b => { const r = b.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, e = document.elementFromPoint(x, y);
    return { k: b.getAttribute('aria-label') || b.className, ok: !!e && (e === b || b.contains(e)), y }; }).filter(h => h.y >= 0 && h.y <= innerHeight);
  let n = 0, all = 0;
  const ul = p && p.querySelector('.cl-plist-l:not(.cl-away-l)');
  if (ul) { const ur = ul.getBoundingClientRect(), lis = [...ul.children].filter(li => li.getClientRects().length); all = lis.length;
    n = lis.filter(li => { const r = li.getBoundingClientRect(); return r.top >= ur.top - 1 && r.bottom <= ur.bottom + 1; }).length; }
  return { vw: innerWidth, vh: innerHeight, ctl: R(c), plist: p ? R(p) : null, side: !!w && w.classList.contains('plist-side'), btns: hit.length,
           blocked: hit.filter(h => !h.ok).map(h => h.k), items: n, all, scroll: ul ? getComputedStyle(ul).overflowY : '',
           exp: p ? p.querySelectorAll('.cl-pl-exp, .cl-exp').length + (/experimentell/i.test(p.textContent) ? 1 : 0) : 0 };
};
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
    const cid = await until(() => ps[0].evaluate(() => { const r = window.HKCalls.room(); return r && r.id; }), 20000);
    for (let i = 1; i < 4; i++) {
      // wie tests/browser_horn_web.js: ohne Klingeln (Knoten-Gruppe) ueber HKCalls.join beitreten
      const rang = await ps[i].waitForSelector('.cl-ring', { timeout: 20000 }).then(() => true, () => false);
      if (rang) await ps[i].click('.cl-ring .cl-big');
      else { console.log('  (Person ' + (i + 1) + ': kein Klingeln - tritt ueber HKCalls.join bei)'); await ps[i].evaluate(id => window.HKCalls.join(id, 'member', true), cid); }
      await sleep(500);
    }
    const all = await until(async () => { const s = await Promise.all(ps.map(st)); return s.every(x => x && (x.horn || x.mesh === 3)) && s; }, 60000, 500);
    ok('alle vier verbunden (' + (all && all[0].horn ? 'Knoten' : 'Mesh') + ')', all, await Promise.all(ps.map(st)));
    const horn = !!(all && all[0].horn); INFO.weg = horn ? 'knoten' : 'mesh';
    await sleep(2500);
    let s3 = await st(ps[2]);
    if (horn) {
      ok('Knoten: je andere Person eine Kachel (3), eigene Kachel im Raster', s3.hornTiles === 3 && /horn-self/.test(s3.cls), s3);
      const left = await Promise.all(ps.map(p => p.evaluate(() => document.querySelectorAll('.cl-mtile').length + (document.querySelector('.cl-win.meet') ? 100 : 0))));
      ok('Knoten: keine Kacheln der Mesh-Ansicht uebrig (Anruf begann ggf. als Mesh)', left.every(n => n === 0), left);
    }
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
    await ps[3].tap('.cl-win .cl-stage').catch(() => {}); await sleep(300);
    const cf = await ps[3].evaluate(CTLFIT);
    ok('Handy 390 px: Bedienleiste einzeilig im Fenster, Auflegen ganz sichtbar', cf.inside && cf.oneRow && cf.red, cf);
    if (horn) {
      const above = await ps[3].evaluate(() => { const g = document.querySelector('.cl-win.horn-strip .cl-grid'), c = document.querySelector('.cl-win .cl-ctl > .cl-btn');
        return !g || !c || g.getBoundingClientRect().bottom <= c.getBoundingClientRect().top + 1; });
      ok('Knoten Handy: Streifen endet ueber der Leiste', above);
      // Bugs-DB 2192: Handy quer - Raster der Knoten-Galerie endet links der senkrechten Leiste (72 px) und nutzt die Hoehe
      await ps[2].setViewportSize({ width: 844, height: 390 }); await sleep(800); await ps[2].mouse.move(300, 200); await sleep(300);
      const gq = await ps[2].evaluate(() => { const g = document.querySelector('.cl-win.horn.tiles .cl-grid'), c = document.querySelector('.cl-win .cl-ctl');
        if (!g || !c) return null; const gr = g.getBoundingClientRect(), cr = c.getBoundingClientRect();
        const ts = [...g.querySelectorAll('.cl-tile')].filter(x => x.getClientRects().length).map(x => x.getBoundingClientRect());
        return { grid: [gr.left, gr.top, gr.width, gr.height].map(Math.round), ctlL: Math.round(cr.left), ctlW: Math.round(cr.width), vh: innerHeight, n: ts.length,
                 free: ts.filter(r => r.right <= cr.left + 1).length }; });
      ok('Knoten Handy quer 844x390: Galerie links der Leiste, Hoehe genutzt', gq && gq.ctlW <= 80 && gq.grid[0] + gq.grid[2] <= gq.ctlL + 1 && gq.n >= 3
         && gq.free === gq.n && gq.grid[3] >= gq.vh * 0.7, gq);
      await shot(ps[2], '2b-gruppe-handy-quer-dunkel');
      await ps[2].setViewportSize({ width: 1280, height: 720 }); await sleep(600);
    }

    // Bugs-DB 2196: Teilnehmerliste quer 844x390 (Galerie) - zu/offen, hell/dunkel; danach 1000x497 (Labor-Fenster, >= 900 px breit:
    // quer trotzdem Blatt statt Seitenleiste ueber der Leiste) und zurueck auf 1280x720 (Mesh: Seitenleiste wie bisher)
    const plQuer = async (tag, shots) => {
      const q = ps[2];
      await q.setViewportSize({ width: 844, height: 390 }); await sleep(800); await q.mouse.move(300, 200); await sleep(300);
      if (shots) { await shot(q, tag + '-quer-844x390-zu-dunkel'); await q.emulateMedia({ colorScheme: 'light' }); await shot(q, tag + '-quer-844x390-zu-hell'); await q.emulateMedia({ colorScheme: 'dark' }); }
      await q.click('.cl-ctl .cl-people'); await sleep(700); await q.mouse.move(310, 210); await sleep(200);
      const a = await q.evaluate(PLHIT); console.log('  Lage Teilnehmerliste quer (' + tag + '): ' + JSON.stringify(a));
      ok('Bugs-DB 2196 (' + tag + '): Liste quer 844x390 links der Leiste, kein Leisten-Knopf verdeckt, >= 3 Eintraege ganz sichtbar, rollbar',
         a && a.plist && a.btns >= 4 && a.blocked.length === 0 && a.plist[0] + a.plist[2] <= a.ctl[0] + 1 && a.plist[1] >= 0 && a.plist[1] + a.plist[3] <= a.vh
         && a.all >= 3 && a.items >= Math.min(a.all, 3) && a.plist[3] >= a.vh * 0.7 && a.scroll === 'auto' && a.exp === 0, a);
      if (shots) { await shot(q, tag + '-quer-844x390-offen-dunkel'); await q.emulateMedia({ colorScheme: 'light' }); await shot(q, tag + '-quer-844x390-offen-hell'); await q.emulateMedia({ colorScheme: 'dark' }); }
      await q.setViewportSize({ width: 1000, height: 497 }); await sleep(800); await q.mouse.move(320, 220); await sleep(300);
      const b = await q.evaluate(PLHIT);
      ok('Bugs-DB 2196 (' + tag + '): quer 1000x497 Blatt statt Seitenleiste, kein Leisten-Knopf verdeckt', b && b.plist && !b.side && b.blocked.length === 0
         && b.plist[0] + b.plist[2] <= b.ctl[0] + 1, b);
      if (shots) await shot(q, tag + '-quer-1000x497-offen-dunkel');
      await q.setViewportSize({ width: 1280, height: 720 }); await sleep(900); await q.mouse.move(600, 300); await sleep(300);
      const d = await q.evaluate(PLHIT);
      ok('Bugs-DB 2196 (' + tag + '): 1280x720 wieder wie bisher (Mesh Seitenleiste, Knoten Liste rechts), Leiste frei',
         d && d.plist && d.side === !horn && d.blocked.length === 0, d);
      await q.click('.cl-ctl .cl-people'); await sleep(400);
    };
    await plQuer('2c-galerie', true);

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
    const selfP = await ps[1].evaluate(() => { const v = document.querySelector('.cl-local'); return !!v && !v.hidden && getComputedStyle(v).display !== 'none'; });
    ok('§7: Teilende hat ihr Bild als Kachel, kein schwebendes Eigenbild darueber', !selfP);
    await shot(ps[1], '6-teilende-dunkel');
    await sleep(800);
    await shot(ps[3], '7-bildschirm-handy-dunkel');
    await ps[3].emulateMedia({ colorScheme: 'light' }); await shot(ps[3], '7-bildschirm-handy-hell');
    ok('S10: Handy beim Teilen ohne waagrechtes Scrollen', !(await st(ps[3])).scrollX);
    await plQuer('7b-' + (horn ? 'bildschirm' : 'praesentation'), true);
    // Sprecher per Kuerzel P
    await ps[2].keyboard.press('p'); await sleep(800);
    const sp = await st(ps[2]);
    ok('Kuerzel P: Sprecher gewaehlt', (await ps[2].evaluate(() => localStorage.getItem('saga-call-layout'))) === 'speaker'
       && (horn ? /horn-strip/.test(sp.cls) : sp.meet && sp.meet.layout === 'speaker'), sp);
    await shot(ps[2], '8-sprecher-dunkel');
    if (horn) {
      // Pruefung 2192: Handy quer ueber den Knoten - Bildschirm, danach Sprecher (ohne Teilen) und Teilnehmerliste; nichts unter der Leiste
      const QG = sel => ps[2].evaluate(s => { const R = e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height].map(Math.round); };
        const c = document.querySelector('.cl-win .cl-ctl'), b = document.querySelector(s), g = document.querySelector('.cl-win.horn-strip .cl-grid');
        const l = document.querySelector('.cl-win .cl-local'), lv = !!l && !l.hidden && getComputedStyle(l).display !== 'none' && l.getClientRects().length > 0;
        const p = document.querySelector('.cl-win .cl-plist');
        return { vh: innerHeight, vw: innerWidth, ctl: c ? R(c) : null, big: b ? R(b) : null, grid: g ? R(g) : null, local: lv ? R(l) : null, plist: p ? R(p) : null,
                 cls: (document.querySelector('.cl-win') || {}).className }; }, sel);
      await ps[2].setViewportSize({ width: 844, height: 390 }); await sleep(800); await ps[2].mouse.move(300, 200); await sleep(300);
      const q1 = await QG('.cl-win.horn-scr video.cl-hscreen'); console.log('  Lage Bildschirm quer: ' + JSON.stringify(q1));
      ok('Pruefung 2192: Knoten-Gruppe Bildschirm quer 844x390 - Bildschirm hoch, links der Leiste, Streifen 120', q1.big && q1.big[3] >= q1.vh * 0.7
         && q1.big[0] + q1.big[2] <= q1.ctl[0] + 1 && q1.grid && q1.grid[2] === 120 && q1.grid[0] + q1.grid[2] <= q1.ctl[0] + 1, q1);
      await shot(ps[2], '9a-bildschirm-quer-844x390-dunkel');
      await ps[1].mouse.move(500, 300); await ps[1].click('.cl-share-stop').catch(() => {});
      await until(async () => !/horn-scr/.test((await st(ps[2])).cls) && /horn-strip/.test((await st(ps[2])).cls), 10000);
      await ps[2].mouse.move(320, 210); await sleep(800);
      const q2 = await QG('.cl-win.horn-strip:not(.horn-scr) .cl-hbig'); console.log('  Lage Sprecher quer: ' + JSON.stringify(q2));
      ok('Pruefung 2192: Knoten-Gruppe Sprecher quer 844x390 - Sprecher hoch, links der Leiste, Streifen 120, eigenes Bild nicht ueber dem Sprecher',
         q2.big && q2.big[3] >= q2.vh * 0.7 && q2.big[0] + q2.big[2] <= q2.ctl[0] + 1 && q2.grid && q2.grid[2] === 120
         && q2.grid[0] + q2.grid[2] <= q2.ctl[0] + 1 && (!q2.local || q2.local[2] * q2.local[3] < q2.big[2] * q2.big[3] * 0.25), q2);
      await shot(ps[2], '9b-sprecher-quer-844x390-dunkel');
      await ps[2].click('.cl-ctl .cl-people').catch(() => {}); await sleep(600);
      const q3 = await QG('.cl-win .cl-plist'); console.log('  Lage Teilnehmerliste quer: ' + JSON.stringify(q3));
      ok('Pruefung 2192: Teilnehmerliste quer 844x390 sichtbar und im Fenster', q3.plist && q3.plist[3] >= 120 && q3.plist[1] >= 0
         && q3.plist[1] + q3.plist[3] <= q3.vh, q3);
      const q4 = await ps[2].evaluate(PLHIT); console.log('  Treffer Teilnehmerliste Sprecher quer: ' + JSON.stringify(q4));
      ok('Bugs-DB 2196: Knoten-Sprecher quer 844x390 - Liste links der Leiste, kein Leisten-Knopf verdeckt, >= 3 Eintraege ganz sichtbar',
         q4 && q4.plist && q4.btns >= 4 && q4.blocked.length === 0 && q4.plist[0] + q4.plist[2] <= q4.ctl[0] + 1 && q4.all >= 3 && q4.items >= 3 && q4.exp === 0, q4);
      await shot(ps[2], '9c-teilnehmer-quer-844x390-dunkel');
      await ps[2].emulateMedia({ colorScheme: 'light' }); await shot(ps[2], '9c-teilnehmer-quer-844x390-hell'); await ps[2].emulateMedia({ colorScheme: 'dark' });
      await ps[2].click('.cl-ctl .cl-people').catch(() => {}); await sleep(300);
      await ps[2].setViewportSize({ width: 1280, height: 720 }); await sleep(600);
    }
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
