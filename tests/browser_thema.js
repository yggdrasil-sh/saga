const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Hell/Dunkel-Schalter (Design-Runde 3): Automatisch folgt dem System, Hell/Dunkel ueberstimmen es (je Browser,
// localStorage 'hk-theme'), kein Aufblitzen, Kontomenue "Darstellung" mit Haekchen, Strg+Shift+L, Neuladen,
// Chat-Panel-iframe und zweiter Tab ziehen nach, theme-color passt, Einstellungen-Auswahl.
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=...] node tests/browser_thema.js
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const DARK_BG = '#0f141c', LIGHT_BG = '#fdfcfa';
let fails = 0, n = 0;
const ok = (c, m, extra) => { n++; if (c) console.log('OK  ', m); else { fails++; console.log('FEHL', m, extra === undefined ? '' : JSON.stringify(extra)); } };
const ld = BASE.replace(/\/$/, '');
(async () => {
  const b = await chromium.launch({ channel: process.env.PW_CHANNEL || undefined });
  const ctx = await b.newContext({ colorScheme: 'light', locale: 'de-DE', viewport: { width: 1300, height: 850 } });
  // frueh messen: Attribut und --bg, sobald <body> entsteht (vor dem ersten Rendern) und bei DOMContentLoaded
  await ctx.addInitScript(() => {
    const rec = (k) => { const r = document.documentElement; window['__' + k] = { attr: r.getAttribute('data-theme'), bg: getComputedStyle(r).getPropertyValue('--bg').trim() }; };
    new MutationObserver((ms, o) => { if (document.body) { rec('body'); o.disconnect(); } }).observe(document, { childList: true, subtree: true });
    document.addEventListener('DOMContentLoaded', () => rec('dcl'));
  });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(String(e)));
  const st = (pg) => (pg || p).evaluate(() => { const r = document.documentElement, cs = getComputedStyle(r);
    return { attr: r.getAttribute('data-theme'), bg: cs.getPropertyValue('--bg').trim(), side: cs.getPropertyValue('--side-bg').trim(), scheme: cs.colorScheme,
      metas: [...document.querySelectorAll('meta[name=theme-color]')].map(m => m.content), mode: window.SagaTheme && SagaTheme.get(),
      dark: window.HK && HK.isDark ? HK.isDark() : null, body: window.__body, dcl: window.__dcl }; });

  // Anmeldeseite (ohne HK): Automatik und Kuerzel
  await p.goto(ld + '/login'); await p.waitForTimeout(300);
  let s = await st();
  ok(s.attr === null && s.bg === LIGHT_BG && s.scheme === 'light', 'Anmeldung: Automatik hell ohne data-theme', s);
  await p.emulateMedia({ colorScheme: 'dark' }); await p.waitForTimeout(100); s = await st();
  ok(s.attr === null && s.bg === DARK_BG && s.scheme === 'dark', 'Anmeldung: Automatik folgt dem System (dunkel)', s);
  await p.keyboard.press('Control+Shift+L'); await p.waitForTimeout(100); s = await st();
  ok(s.attr === 'light' && s.bg === LIGHT_BG && s.metas.every(m => m === LIGHT_BG), 'Anmeldung: Strg+Shift+L von dunkel -> hell (theme-color mit)', s);
  await p.evaluate(() => SagaTheme.set('auto')); s = await st();
  ok(s.attr === null && s.metas.join() === DARK_BG, 'Anmeldung: zurueck auf Automatik stellt theme-color wieder her', s);

  await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);

  for (const [name, url] of [['Heute', '/'], ['Seiten', '/notes'], ['Chats', '/chats']]) {
    await p.emulateMedia({ colorScheme: 'light' }); await p.evaluate(() => SagaTheme.set('auto'));
    await p.goto(ld + url); await p.waitForTimeout(600); s = await st();
    ok(s.attr === null && s.bg === LIGHT_BG && s.mode === 'auto', name + ': Automatik hell', s);
    await p.emulateMedia({ colorScheme: 'dark' }); await p.waitForTimeout(100); s = await st();
    ok(s.bg === DARK_BG && s.dark === true && s.scheme === 'dark', name + ': Automatik dunkel', s);
    await p.evaluate(() => SagaTheme.set('light')); s = await st();
    ok(s.attr === 'light' && s.bg === LIGHT_BG && s.dark === false && s.scheme === 'light' && s.metas.every(m => m === LIGHT_BG), name + ': Hell ueberstimmt dunkles System', s);
    await p.emulateMedia({ colorScheme: 'light' }); await p.evaluate(() => SagaTheme.set('dark')); s = await st();
    ok(s.attr === 'dark' && s.bg === DARK_BG && s.dark === true && s.scheme === 'dark' && s.metas.every(m => m === DARK_BG), name + ': Dunkel ueberstimmt helles System', s);
    await p.reload(); await p.waitForTimeout(500); s = await st();
    ok(s.attr === 'dark' && s.bg === DARK_BG, name + ': Dunkel bleibt nach Neuladen', s);
    ok(s.body && s.body.attr === 'dark' && s.body.bg === DARK_BG && s.dcl && s.dcl.bg === DARK_BG, name + ': kein Aufblitzen (Attribut und --bg schon beim ersten <body>)', { body: s.body, dcl: s.dcl });
  }
  // Seitenleiste: hell erzwungen -> helle Leistenfarbe auch bei dunklem System
  await p.goto(ld + '/notes'); await p.waitForTimeout(600);
  await p.emulateMedia({ colorScheme: 'dark' }); await p.evaluate(() => SagaTheme.set('light')); s = await st();
  ok(s.side === '#f6f4ef', 'Seiten: --side-bg hell bei erzwungenem Hell', s);
  await p.evaluate(() => SagaTheme.set('dark')); s = await st();
  ok(s.side !== '#f6f4ef' && s.side !== '', 'Seiten: --side-bg dunkel bei erzwungenem Dunkel', s);

  // Ereignis hk-theme (graph.js u. a.)
  const evs = await p.evaluate(() => { const a = []; window.addEventListener('hk-theme', e => a.push(e.detail.mode + ':' + e.detail.dark)); SagaTheme.set('light'); SagaTheme.set('light'); SagaTheme.set('auto'); return a; });
  ok(JSON.stringify(evs) === JSON.stringify(['light:false', 'auto:true']), 'Ereignis hk-theme nur bei Aenderung, mit wirksamem Modus', evs);

  // Kontomenue: Darstellung > Automatisch/Hell/Dunkel mit Haekchen
  await p.evaluate(() => SagaTheme.set('dark'));
  await p.click('#hk-user'); await p.waitForTimeout(200);
  const item = p.locator('.menu .menu-item', { hasText: 'Darstellung' }).first();
  ok(await item.count() === 1 && /Dunkel/.test(await item.textContent()), 'Kontomenue: Eintrag "Darstellung" mit aktuellem Modus');
  await item.click(); await p.waitForTimeout(250);
  const sub = await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item')].filter(b => /^(Automatisch|Hell|Dunkel)$/.test(b.querySelector('.mi-t') && b.querySelector('.mi-t').textContent))
    .map(b => b.querySelector('.mi-t').textContent + (b.classList.contains('on') ? '*' : '') + (b.querySelector('svg') ? '+ic' : '')));
  ok(JSON.stringify(sub) === JSON.stringify(['Automatisch+ic', 'Hell+ic', 'Dunkel*+ic']), 'Untermenue: drei Eintraege mit Symbol, Haekchen am aktiven', sub);
  await p.locator('.menu .menu-item', { hasText: /^Hell$/ }).click(); await p.waitForTimeout(150); s = await st();
  ok(s.attr === 'light' && s.mode === 'light', 'Untermenue: Hell gewaehlt', s);
  ok(await p.evaluate(() => localStorage.getItem('hk-theme')) === 'light', 'gespeichert in localStorage hk-theme');

  // Strg+Shift+L mit Toast (vom wirksamen Modus aus)
  await p.keyboard.press('Control+Shift+L'); await p.waitForTimeout(200); s = await st();
  const toast = await p.evaluate(() => { const t = document.querySelector('.toast'); return t && !t.hidden ? t.textContent : ''; });
  ok(s.attr === 'dark' && /Dunkel/.test(toast), 'Strg+Shift+L: hell -> dunkel, Toast "Dunkel"', { s, toast });
  await p.evaluate(() => SagaTheme.set('auto')); await p.emulateMedia({ colorScheme: 'dark' });
  await p.keyboard.press('Control+Shift+L'); await p.waitForTimeout(150); s = await st();
  ok(s.attr === 'light', 'Strg+Shift+L aus Automatik (System dunkel) -> hell', s);

  // Chat-Panel-iframe (gleiche Origin) und zweiter Tab ziehen ueber 'storage' nach
  await p.evaluate(() => new Promise(r => { const f = document.createElement('iframe'); f.id = 'thema-if'; f.src = '/chats?panel=1'; f.style.cssText = 'width:400px;height:300px'; f.onload = r; document.body.appendChild(f); }));
  await p.waitForTimeout(500);
  const fr = p.frame({ url: /panel=1/ });
  const p2 = await ctx.newPage(); await p2.emulateMedia({ colorScheme: 'dark' }); await p2.goto(ld + '/notes'); await p2.waitForTimeout(500);
  ok((await st(fr)).attr === 'light' && (await st(p2)).attr === 'light', 'iframe und zweiter Tab starten mit gespeicherter Wahl');
  await p.evaluate(() => SagaTheme.set('dark')); await p.waitForTimeout(300);
  let fs = await st(fr), s2 = await st(p2);
  ok(fs.attr === 'dark' && fs.bg === DARK_BG, 'Chat-Panel-iframe zieht ohne Neuladen nach (dunkel)', fs);
  ok(s2.attr === 'dark' && s2.bg === DARK_BG && s2.metas.every(m => m === DARK_BG), 'Zweiter Tab zieht nach (inkl. theme-color)', s2);
  await p2.evaluate(() => SagaTheme.set('auto')); await p.waitForTimeout(300);
  s = await st(); fs = await st(fr);
  ok(s.attr === null && s.bg === DARK_BG && fs.attr === null, 'Automatik aus dem zweiten Tab: erster Tab und iframe folgen wieder dem System', { s, fs });
  await p2.close();

  // Einstellungen: Auswahl Darstellung
  await p.goto(ld + '/notes?settings=account'); await p.waitForTimeout(900);
  const sel = p.locator('#hk-theme');
  ok(await sel.count() === 1 && await sel.inputValue() === 'auto', 'Einstellungen: Auswahl Darstellung (Automatisch)');
  await sel.selectOption('dark'); await p.waitForTimeout(100); s = await st();
  ok(s.attr === 'dark', 'Einstellungen: Auswahl setzt Dunkel', s);
  await p.keyboard.press('Control+Shift+L'); await p.waitForTimeout(100);
  ok(await sel.inputValue() === 'light', 'Einstellungen: Auswahl folgt dem Kuerzel');
  await p.evaluate(() => SagaTheme.set('auto'));

  // CSS: jede Regel aus @media (prefers-color-scheme) gilt auch erzwungen
  for (const f of ['tokens.css', 'style.css', 'chat.css']) {
    const css = await (await p.request.get(ld + '/static/' + f)).text();
    const blocks = css.match(/@media\s*\(prefers-color-scheme:\s*(light|dark)\)\s*\{/g) || [];
    const forced = (css.match(/:root(?::where\()?\[data-theme=(light|dark)\]/g) || []).length;
    const bare = css.split(/@media\s*\(prefers-color-scheme:\s*(?:light|dark)\)\s*\{/).slice(1).filter(x => !/^[^{]*data-theme=/.test(x)).length;
    ok(blocks.length > 0 && bare === 0 && forced >= blocks.length, f + ': ' + blocks.length + ' Farbschema-Bloecke, alle mit data-theme-Gegenstueck', { blocks: blocks.length, forced, bare });
  }
  ok(errs.length === 0, 'keine Skriptfehler', errs);
  console.log(fails ? `FEHLER: ${fails} von ${n}` : `ALLES GRUEN (${n} Pruefungen)`);
  await b.close(); process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
