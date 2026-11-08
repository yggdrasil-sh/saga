// Browser-Ende-zu-Ende: Anhaenge per Einfuegen und Ablegen im Chat (2.5, static/chat.js addFiles/paste/drop) - zwei
// Playwright-Kontexte (zwei frische Konten) im headless Chromium:
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] node tests/browser_chat_einfuegen.js
//   (PW_CHANNEL=chromium; PW_ENGINE=webkit nimmt Playwright-WebKit, falls installiert)
// Geprueft: synthetisches paste mit DataTransfer + PNG-Datei (namenlos "image.png") -> Anhang in #ct-files mit Namen
// "Bild-JJJJ-MM-TT-HHMMSS.png", Senden -> Bild beim Empfaenger; reiner Text-Paste fuegt Text ein, kein Anhang; Text und
// Bild zugleich -> beides; Bild nur ueber items (files leer, wie WebKit) -> Anhang; beim Bearbeiten kein Anhang; Drop mit
// Datei -> Ablageflaeche sichtbar, Anhang; 11. Datei -> Hinweis "Hoechstens 10"; keine Seitenfehler.
// Kein waitForFunction (CSP ohne eval).
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, 'einfuegen-' + name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: 'de-DE', colorScheme: 'dark' });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  p.on('dialog', d => { errs.push(user + ': dialog ' + d.message()); d.dismiss().catch(() => {}); });
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
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'X-Saga-Chat': '3', 'Content-Type': 'application/json' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.json().then(d => ({ s: r.status, d }))), [m, u, b]);
const chips = p => p.evaluate(() => { const b = document.getElementById('ct-files'); return { hidden: b.hidden, names: [...b.querySelectorAll('.ct-chip')].map(c => c.firstChild.textContent.replace(/ \([^()]*\)$/, '')) }; });
const clearFiles = async p => { for (let i = 0; i < 12; i++) { const x = await p.$('#ct-files .ct-chip .linkbtn'); if (!x) break; await x.click(); } };
const toastText = p => p.evaluate(() => { const t = document.querySelector('.toast'); return t && !t.hidden ? t.textContent : ''; });

// Im Browser: paste-Ereignis mit DataTransfer. mode: 'file' (files), 'items' (nur items, files leer - wie WebKit),
// 'text' (nur Text), 'both' (Text + Bild)
const PASTE = ([mode, txt]) => {
  const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEUlEQVR4nGM4uSQGK2IYWhIAv5xyQfgn2UgAAAAASUVORK5CYII='), c => c.charCodeAt(0));
  const f = new File([png], 'image.png', { type: 'image/png' });
  const t = document.getElementById('ct-input'); t.focus();
  let dt;
  if (mode === 'items') {
    // WebKit-Fall nachbauen: files leer, Bild nur ueber items[].getAsFile()
    const item = { kind: 'file', type: 'image/png', getAsFile: () => f };
    dt = { files: [], items: [item], types: ['Files'], getData: () => '' };
  } else {
    dt = new DataTransfer();
    if (mode !== 'text') dt.items.add(f);
    if (txt) dt.setData('text/plain', txt);
  }
  const ev = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: mode === 'items' ? null : dt });
  if (mode === 'items') Object.defineProperty(ev, 'clipboardData', { value: dt });
  const notHandled = t.dispatchEvent(ev);
  // synthetisches paste fuegt nie selbst ein: unbehandelten Text wie der Browser einfuegen
  if (notHandled && txt) t.setRangeText(txt, t.selectionStart, t.selectionEnd, 'end');
  return { prevented: !notHandled, value: t.value };
};
const DROP = ([n, name]) => {
  const chat = document.getElementById('ct-chat'), dt = new DataTransfer();
  for (let i = 0; i < n; i++) dt.items.add(new File(['x' + i], (name || 'datei') + '-' + i + '.txt', { type: 'text/plain' }));
  const r = chat.getBoundingClientRect(), o = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: r.left + 50, clientY: r.top + 100 };
  chat.dispatchEvent(new DragEvent('dragenter', o));
  const over = !chat.dispatchEvent(new DragEvent('dragover', o));
  const z = document.querySelector('#ct-chat .ct-drop'), zone = !!z && !z.hidden && getComputedStyle(z).display !== 'none';
  const drop = !chat.dispatchEvent(new DragEvent('drop', o));
  const z2 = document.querySelector('#ct-chat .ct-drop');
  return { over, zone, drop, after: !z2 || z2.hidden };
};

(async () => {
  const eng = E.PW_ENGINE === 'webkit' ? pw.webkit : pw.chromium;
  const browser = await eng.launch(E.PW_ENGINE === 'webkit' ? { headless: true } : { channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    const ppl = await api(a, 'GET', '/api/chat/people?q=' + encodeURIComponent(U2));
    const bref = ((ppl.d.users || []).filter(u => u.username === U2)[0] || (ppl.d.users || [])[0] || {}).username;
    const dm = await api(a, 'POST', '/api/chats', { kind: 'dm', user: bref });
    ok('Direkt-Chat angelegt', dm.s === 200, dm.s);
    const dmId = dm.d.chat.id;
    await a.goto(BASE + '/chats#c' + dmId); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);

    // --- Bild einfuegen (files) ---
    const p1 = await a.evaluate(PASTE, ['file', '']);
    let c1 = await chips(a);
    ok('Paste mit PNG: Ereignis abgefangen, Anhang in #ct-files', p1.prevented && !c1.hidden && c1.names.length === 1, [p1, c1]);
    ok('namenloses Bild heisst "Bild-JJJJ-MM-TT-HHMMSS.png"', /^📎 Bild-\d{4}-\d\d-\d\d-\d{6}\.png$/.test(c1.names[0] || ''), c1.names);
    ok('Fokus bleibt im Eingabefeld', await a.evaluate(() => document.activeElement && document.activeElement.id === 'ct-input'));
    await shot(a, 'paste');
    const fname = (c1.names[0] || '').replace(/^📎 /, '');
    await a.press('#ct-input', 'Enter');
    const sent = await until(async () => (await chips(a)).hidden, 15000);
    ok('Senden leert die Anhangsleiste', sent);
    await b.goto(BASE + '/chats#c' + dmId); await b.waitForSelector('#ct-chat:not([hidden])');
    const got = await until(() => b.evaluate(() => { const i = [...document.querySelectorAll('#ct-msgs .ct-img')].pop(); const im = i && i.querySelector('img');
      return i && im && im.complete && im.naturalWidth > 0 ? { name: i.getAttribute('aria-label'), w: im.naturalWidth } : null; }), 20000);
    ok('Empfaenger sieht das Bild (entschluesselt, 8 px breit, gleicher Name)', got && got.w === 8 && got.name === fname, got);
    await shot(b, 'empfaenger');

    // --- reiner Text ---
    await a.fill('#ct-input', '');
    const p2 = await a.evaluate(PASTE, ['text', 'nur Text']);
    c1 = await chips(a);
    ok('Text-Paste: nicht abgefangen, Text eingefuegt, kein Anhang', !p2.prevented && p2.value === 'nur Text' && c1.hidden, [p2, c1]);

    // --- Text und Bild zugleich ---
    await a.fill('#ct-input', 'vorher ');
    await a.evaluate(() => { const t = document.getElementById('ct-input'); t.setSelectionRange(t.value.length, t.value.length); });
    const p3 = await a.evaluate(PASTE, ['both', 'aus dem Browser']);
    c1 = await chips(a);
    ok('Text + Bild: Text eingefuegt und Bild angehaengt', p3.prevented && (await a.inputValue('#ct-input')) === 'vorher aus dem Browser' && c1.names.length === 1, [p3, c1, await a.inputValue('#ct-input')]);
    await clearFiles(a); await a.fill('#ct-input', '');

    // --- WebKit-Fall: nur items ---
    const p4 = await a.evaluate(PASTE, ['items', '']);
    c1 = await chips(a);
    ok('Bild nur ueber items (files leer): Anhang', p4.prevented && c1.names.length === 1 && /Bild-.*\.png$/.test(c1.names[0] || ''), [p4, c1]);
    await clearFiles(a);

    // --- beim Bearbeiten kein Anhang ---
    await a.fill('#ct-input', 'zum Bearbeiten'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(1500);
    await a.fill('#ct-input', ''); await a.press('#ct-input', 'ArrowUp'); await a.waitForTimeout(300);
    const editing = await a.evaluate(() => document.getElementById('ct-reply').classList.contains('ct-editing'));
    await a.evaluate(PASTE, ['file', '']);
    c1 = await chips(a);
    ok('Bearbeiten: Bild-Paste haengt nichts an, Hinweis', editing && c1.hidden && /Bearbeiten/.test(await toastText(a)), [editing, c1, await toastText(a)]);
    const d0 = await a.evaluate(DROP, [1, 'edit']);
    ok('Bearbeiten: Ablegen haengt nichts an', !d0.zone && (await chips(a)).hidden, d0);
    await a.press('#ct-input', 'Escape'); await a.waitForTimeout(300);

    // --- Ziehen und Ablegen ---
    const d1 = await a.evaluate(DROP, [2, 'abgelegt']);
    c1 = await chips(a);
    ok('Drop: Ablageflaeche sichtbar, danach weg, zwei Anhaenge', d1.over && d1.zone && d1.drop && d1.after && c1.names.length === 2 && /abgelegt-0\.txt/.test(c1.names[0]), [d1, c1]);
    // Text ziehen bleibt normal (kein Abfangen)
    const tx = await a.evaluate(() => { const chat = document.getElementById('ct-chat'), dt = new DataTransfer(); dt.setData('text/plain', 'x');
      return chat.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt })); });
    ok('Text ziehen wird nicht abgefangen', tx === true);

    // --- 11. Datei ---
    await a.evaluate(DROP, [8, 'mehr']);
    c1 = await chips(a);
    ok('zehn Anhaenge erreicht', c1.names.length === 10, c1.names.length);
    await a.evaluate(PASTE, ['file', '']);
    c1 = await chips(a);
    const tt = await toastText(a);
    ok('11. Datei: nicht angehaengt, Hinweis "Höchstens 10 Anhänge"', c1.names.length === 10 && /Höchstens 10 Anhänge/.test(tt), [c1.names.length, tt]);
    await shot(a, 'zehn');
    await clearFiles(a);
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    console.log('FEHLER ' + (e && e.stack || e)); res.push(false);
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-CHAT-EINFUEGEN ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
