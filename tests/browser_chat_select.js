// Browser-Ende-zu-Ende fuer die Mehrfachauswahl im Chat (2.5, static/chat.js, saga/chat.py) und den Hinweis, warum es keine
// Reaktionen gibt (x3_why) - zwei Playwright-Kontexte (zwei Konten) im headless Chrome:
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] [SHOT_PREFIX=ca-web-] node tests/browser_chat_select.js
// Die beiden Konten brauchen noch keine Chats miteinander (der Test legt den Direkt-Chat an).
// Geprueft: Auswaehlen ueber das Nachrichtenmenue, Klick waehlt weitere ab/an, Umschalt+Klick einen Bereich, Leertaste,
// aria-selected, Kopfzeile "n ausgewaehlt", Esc beendet; Kopieren ("Name, Zeit: Text" je Zeile, eine Nachricht nur der Text,
// Anhaenge als [Bild]); Loeschen-Dialog (fremde dabei: nur "Fuer mich" + Hinweis; nur eigene: auch "Fuer alle"), fuer mich
// (Server blendet aus, die andere Seite sieht sie weiter), fuer alle (live bei der anderen Seite weg); Weiterleiten in einen
// anderen Chat mit Rueckfrage (Text und Bild neu verschluesselt/hochgeladen); In Seite uebernehmen (Warnhinweis, neue Seite
// und an bestehende anhaengen, Inhalt "Name · Zeit" + Text); Hinweis (i) im Kopf nach x3_why; Handy-Breite mit langem Druck
// (Touch), hell/dunkel; keine Seitenfehler. Kein waitForFunction (CSP ohne eval).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'chatsel-';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PRE + name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 800 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark',
                                         isMobile: !!opts.mobile, hasTouch: !!opts.mobile, deviceScaleFactor: opts.mobile ? 2 : 1 });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
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
const rows = p => p.evaluate(() => [...document.querySelectorAll('#ct-msgs .ct-msg')].map(r => ({
  id: r.dataset.id, t: (r.querySelector('.ct-text') || {}).textContent || '', img: !!r.querySelector('.ct-img'), mine: r.classList.contains('mine'),
  role: r.getAttribute('role'), sel: r.getAttribute('aria-selected'), picked: r.classList.contains('ct-picked') })));
const rowByText = async (p, t) => (await rows(p)).filter(r => r.t === t).pop();
const rowSel = id => '#ct-msgs .ct-msg[data-id="' + id + '"]';
const selN = p => p.evaluate(() => { const n = document.querySelector('#ct-sel-n'); return n ? n.textContent : null; });
async function menuOf(p, id) {
  await p.hover(rowSel(id)); await p.click(rowSel(id) + ' .ct-act:not(.ct-react-b)'); await p.waitForSelector('.menu .menu-item');
}
async function send(p, text) { await p.fill('#ct-input', text); await p.press('#ct-input', 'Enter'); return until(() => rowByText(p, text), 15000); }
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'X-Saga-Chat': '3', 'Content-Type': 'application/json' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.json().then(d => ({ s: r.status, d }))), [m, u, b]);
async function confirmAsk(p) { await p.waitForSelector('.hk-ask .btn-primary, .hk-ask .btn-destr'); await p.click('.hk-ask .btn-primary, .hk-ask .btn-destr'); }

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    // Ziel fuers Weiterleiten: Seiten-Chat einer eigenen Seite
    const pg = await api(a, 'POST', '/api/notes', { title: 'Weiterleit-Ziel' });
    await a.goto(BASE + '/chats#p' + pg.d.id); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    const target = await a.evaluate(() => document.querySelector('#ct-title').textContent);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1200);
    const m1 = await send(a, 'Erste Nachricht'), m2 = await send(a, 'Zweite Nachricht'), m3 = await send(a, 'Dritte Nachricht');
    await a.setInputFiles('#ct-file', { name: 'punkt.png', mimeType: 'image/png', buffer: PNG });
    await a.waitForTimeout(400); await a.fill('#ct-input', 'Mit Bild'); await a.press('#ct-input', 'Enter');
    const m4 = await until(async () => { const r = await rowByText(a, 'Mit Bild'); return r && r.img ? r : null; }, 20000);
    ok('A: vier Nachrichten gesendet (eine mit Bild)', m1 && m2 && m3 && m4, { m1, m4 });
    await b.goto(BASE + '/chats'); await b.waitForTimeout(1200); await b.click('#ct-list .ct-item-b'); await b.waitForSelector('#ct-chat:not([hidden])');
    await until(() => rowByText(b, 'Mit Bild'), 15000);
    const mb = await send(b, 'Antwort von B');
    const mbA = await until(() => rowByText(a, 'Antwort von B'), 15000);
    ok('B antwortet, A sieht es', !!mb && !!mbA);

    // --- Auswahlmodus ---------------------------------------------------------------------------------------------
    await menuOf(a, m1.id);
    const items = await a.$$eval('.menu .menu-item', x => x.map(i => i.textContent));
    ok('Nachrichtenmenue hat "Auswählen"', items.some(t => /Auswählen/.test(t)), items);
    await a.click('.menu .menu-item:has-text("Auswählen")');
    ok('Auswahlmodus: Kopfzeile "1 ausgewählt", Aktionsleiste statt Eingabefeld', (await until(() => selN(a), 3000)) === '1 ausgewählt'
       && await a.isVisible('#ct-selact') && !(await a.isVisible('#ct-compose')), await selN(a));
    await a.click(rowSel(m3.id), { modifiers: ['Shift'] });
    ok('Umschalt+Klick waehlt den Bereich (3)', (await selN(a)) === '3 ausgewählt', await selN(a));
    await a.click(rowSel(m2.id) + ' .ct-bubble');
    let r = await rows(a);
    ok('Klick waehlt ab; aria-selected stimmt', (await selN(a)) === '2 ausgewählt' && r.find(x => x.id === m2.id).sel === 'false'
       && r.find(x => x.id === m1.id).sel === 'true' && r.find(x => x.id === m1.id).role === 'option', r.slice(0, 4));
    await a.focus(rowSel(m4.id)); await a.keyboard.press(' ');
    ok('Leertaste waehlt die fokussierte Nachricht', (await selN(a)) === '3 ausgewählt', await selN(a));
    await a.click(rowSel(m4.id) + ' .ct-img');                 // im Auswahlmodus oeffnet ein Bild nicht, sondern waehlt ab
    ok('Klick aufs Bild waehlt ab (kein Bildbetrachter)', (await selN(a)) === '2 ausgewählt' && !(await a.$('.ct-ov')), await selN(a));
    await a.click(rowSel(m4.id) + ' .ct-bubble');
    await shot(a, 'auswahl-dunkel');
    // Kopieren
    await a.click('#ct-selact .ct-sel-copy');
    const clip = await until(() => a.evaluate(() => navigator.clipboard.readText().catch(() => '')), 3000);
    const lines = (clip || '').split('\n');
    ok('Kopieren: je Zeile "Name, Zeit: Text", Bild als [Bild]', lines.length === 3 && /^.+, \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}: Erste Nachricht$/.test(lines[0])
       && /: Dritte Nachricht$/.test(lines[1]) && /: Mit Bild \[Bild\]$/.test(lines[2]), clip);
    ok('Nach dem Kopieren endet die Auswahl', !(await a.$('#ct-selbar')) && await a.isVisible('#ct-compose'));
    await menuOf(a, m2.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.click('#ct-selact .ct-sel-copy');
    const clip1 = await until(() => a.evaluate(() => navigator.clipboard.readText().catch(() => '')).then(t => t === 'Zweite Nachricht' ? t : null), 3000);
    ok('Kopieren einer Nachricht: nur der Text', clip1 === 'Zweite Nachricht', clip1);
    // Esc beendet
    await menuOf(a, m2.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.keyboard.press('Escape');
    ok('Esc beendet die Auswahl', !(await a.$('#ct-selbar')));

    // --- Loeschen ---------------------------------------------------------------------------------------------------
    await menuOf(a, m2.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.click(rowSel(mbA.id) + ' .ct-bubble');
    await a.click('#ct-selact .ct-sel-del'); await a.waitForSelector('.ct-del-dlg');
    const dlg = await a.evaluate(() => ({ t: document.querySelector('.ct-del-dlg').textContent, all: !!document.querySelector('.ct-del-all'), me: !!document.querySelector('.ct-del-me') }));
    ok('Loeschen-Dialog mit fremder Nachricht: nur "Für mich", Hinweis', dlg.me && !dlg.all && /Nur eigene Nachrichten lassen sich für alle löschen/.test(dlg.t), dlg);
    await shot(a, 'loeschen-dunkel');
    await a.click('.ct-del-me'); await confirmAsk(a);
    const goneA = await until(async () => { const x = await rows(a); return !x.some(y => y.id === m2.id || y.id === mbA.id) ? x : null; }, 5000);
    ok('Fuer mich geloescht: bei A weg', !!goneA);
    ok('B sieht beide weiter', !!(await rowByText(b, 'Zweite Nachricht')) && !!(await rowByText(b, 'Antwort von B')));
    await a.goto(BASE + '/chats'); await a.waitForTimeout(800);
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    ok('Nach Neuladen bleibt es ausgeblendet (Server)', !(await rowByText(a, 'Zweite Nachricht')) && !!(await rowByText(a, 'Erste Nachricht')));
    await menuOf(a, m3.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.click('#ct-selact .ct-sel-del'); await a.waitForSelector('.ct-del-dlg');
    ok('Nur eigene: "Für alle löschen" angeboten', !!(await a.$('.ct-del-all')) && !(await a.$('.ct-del-hint')));
    await a.click('.ct-del-all'); await confirmAsk(a);
    const goneB = await until(async () => !(await rowByText(b, 'Dritte Nachricht')), 15000);
    ok('Fuer alle geloescht: live auch bei B weg', goneB && !(await rowByText(a, 'Dritte Nachricht')));
    const toast = await a.evaluate(() => (document.querySelector('.toast') || document.querySelector('#toast') || {}).textContent || '');
    ok('Toast mit Anzahl', /1 Nachricht für alle gelöscht/.test(toast), toast);

    // --- Weiterleiten -----------------------------------------------------------------------------------------------
    await menuOf(a, m1.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.click(rowSel(m4.id) + ' .ct-bubble');
    await a.click('#ct-selact .ct-sel-fwd'); await a.waitForSelector('.ct-fwd-q');
    await a.fill('.ct-fwd-q', 'Weiterleit');
    const cands = await until(async () => { const x = await a.$$eval('.ct-fwd-item', b => b.map(i => i.textContent)); return x.length === 1 ? x : null; }, 3000);
    ok('Weiterleiten: Suche in der Chat-Liste', cands && /Weiterleit-Ziel/.test(cands[0]), cands);
    await a.click('.ct-fwd-item');
    await a.waitForSelector('.hk-ask');
    const askT = await a.textContent('.hk-ask');
    ok('Rueckfrage "2 Nachrichten an … weiterleiten?"', /2 Nachrichten an „.*Weiterleit-Ziel.*“ weiterleiten\?/.test(askT), askT);
    await confirmAsk(a);
    const ft = await until(() => a.evaluate(() => { const t = document.querySelector('.toast'); return t && /Nachrichten weitergeleitet/.test(t.textContent) ? t.textContent : null; }), 30000);
    ok('Toast "2 Nachrichten weitergeleitet." mit "Öffnen"', ft && /2 Nachrichten weitergeleitet/.test(ft) && /Öffnen/.test(ft), ft);
    await a.click('.toast .toast-action');
    await a.waitForTimeout(1500);
    const fw = await until(async () => { const x = await rows(a); return x.some(y => y.t === 'Erste Nachricht') && x.some(y => y.t === 'Mit Bild' && y.img) ? x : null; }, 15000);
    ok('Im Ziel-Chat: Text und Bild (neu hochgeladen, lesbar)', !!fw && (await a.textContent('#ct-title')) === target, (await rows(a)));
    const imgOk = await until(() => a.evaluate(() => { const i = [...document.querySelectorAll('#ct-msgs .ct-img img')].pop(); return i && i.complete && i.naturalWidth > 0; }), 10000);
    ok('Weitergeleitetes Bild laesst sich entschluesseln', !!imgOk);

    // --- In Seite uebernehmen -----------------------------------------------------------------------------------------
    await a.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    const e1 = await rowByText(a, 'Erste Nachricht'), e4 = await rowByText(a, 'Mit Bild');
    await menuOf(a, e1.id); await a.click('.menu .menu-item:has-text("Auswählen")'); await a.click(rowSel(e4.id) + ' .ct-bubble');
    await a.click('#ct-selact .ct-sel-page'); await a.waitForSelector('.ct-page-dlg');
    const warn = await a.textContent('.ct-e2e-warn'), ttl = await a.inputValue('.ct-pg-title');
    ok('Seiten-Dialog: Warnhinweis, Titel "Chat <Name> – <Datum>"', /nicht Ende-zu-Ende-verschlüsselt – der Server kann den Text lesen/.test(warn) && /^Chat .+ – \d{2}\.\d{2}\.\d{4}$/.test(ttl), { warn, ttl });
    await a.click('.ct-pg-go');
    const pt = await until(() => a.evaluate(() => { const t = document.querySelector('.toast'); return t && /Seite übernommen/.test(t.textContent) ? t.textContent : null; }), 10000);
    ok('Toast "2 Nachrichten in die Seite übernommen." mit "Öffnen"', pt && /2 Nachrichten/.test(pt) && /Öffnen/.test(pt), pt);
    const found = await api(a, 'GET', '/api/chat/pages?q=' + encodeURIComponent(ttl));
    const nid = ((found.d.pages || [])[0] || {}).id;
    const note = nid ? await api(a, 'GET', '/api/notes/' + nid) : { d: {} };
    const blocks = (JSON.parse(note.d.body || '{"blocks":[]}').blocks || []).map(x => x.data && x.data.text);
    ok('Neue Seite: je Nachricht ein Absatz "<b>Name</b> · Zeit" + Text, Bild als Hinweis', blocks.length === 2 && /^<b>.+<\/b> · \d{2}\.\d{2}\.\d{4}, \d{2}:\d{2}<br>Erste Nachricht$/.test(blocks[0])
       && /<br>Mit Bild \[Bild\]$/.test(blocks[1]), blocks);
    await menuOf(a, e1.id); await a.click('.menu .menu-item:has-text("Auswählen")');
    await a.click('#ct-selact .ct-sel-page'); await a.waitForSelector('.ct-page-dlg');
    await a.check('input[name=ct-pg-mode][value=add]');
    ok('Anhaengen: "Übernehmen" erst nach Wahl einer Seite', await a.isDisabled('.ct-pg-go'));
    await a.fill('.ct-pg-q', 'Weiterleit-Ziel');
    await until(async () => (await a.$$('.ct-pg-list .ct-fwd-item')).length === 1, 5000);
    await a.click('.ct-pg-list .ct-fwd-item');
    await until(async () => !(await a.isDisabled('.ct-pg-go')), 3000);
    await a.click('.ct-pg-go');
    await until(() => a.evaluate(() => { const t = document.querySelector('.toast'); return t && /1 Nachricht in die Seite übernommen/.test(t.textContent); }), 10000);
    const n2 = await api(a, 'GET', '/api/notes/' + pg.d.id);
    const b2 = (JSON.parse(n2.d.body || '{"blocks":[]}').blocks || []).map(x => x.data && x.data.text);
    ok('An bestehende Seite angehaengt', b2.length === 1 && /<br>Erste Nachricht$/.test(b2[0]), b2);

    // --- Hinweis (i) nach x3_why -----------------------------------------------------------------------------------
    const ch = await api(a, 'GET', '/api/chats/' + (await a.evaluate(() => { const r = [...document.querySelectorAll('#ct-list .ct-item-b')].find(x => !/Weiterleit-Ziel/.test(x.textContent)); return r ? r.dataset.id : 0; })));
    const why = ch.d.chat && ch.d.chat.x3_why;
    ok('Chat-Objekt: x3_ok false mit x3_why', ch.d.chat && ch.d.chat.x3_ok === false && !!why, ch.d.chat && { ok: ch.d.chat.x3_ok, why });
    ok('Kopf: (i) sichtbar', await a.isVisible('#ct-sub .ct-x3i'));
    await a.click('#ct-sub .ct-x3i'); await a.waitForSelector('.menu .ct-x3-pop');
    const pop = await a.textContent('.menu .ct-x3-pop');
    const want = { setting: /noch ausgeschaltet/, devices: /sobald alle Geräte Saga 2\.5 nutzen/, pending: /in Kürze|Anfrage/, server: /sobald .+ aktualisiert ist/ }[why] || /./;
    ok('Hinweis passt zu x3_why (' + why + ')', want.test(pop), pop);
    await shot(a, 'hinweis-dunkel');
    await a.keyboard.press('Escape');

    // --- Handy-Breite, hell, langer Druck ----------------------------------------------------------------------------
    const h = await session(browser, U1, E.SAGA_PASS, { viewport: { width: 390, height: 844 }, mobile: true, scheme: 'light' });
    await chatKeys(h, U1);
    await h.goto(BASE + '/chats#u' + encodeURIComponent(U2)); await h.waitForSelector('#ct-chat:not([hidden])'); await h.waitForTimeout(1800);
    const hr = await until(() => rowByText(h, 'Erste Nachricht'), 10000);
    const box = await h.locator(rowSel(hr.id) + ' .ct-bubble').boundingBox();
    const cdp = await h.context().newCDPSession(h);
    const pt0 = { x: Math.round(box.x + box.width / 2), y: Math.round(box.y + box.height / 2) };
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [pt0] });
    await h.waitForTimeout(700);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    ok('Handy: langer Druck startet die Auswahl', (await until(() => selN(h), 3000)) === '1 ausgewählt', await selN(h));
    await h.waitForTimeout(500);
    const mr = await rowByText(h, 'Mit Bild');
    if (mr) await h.tap(rowSel(mr.id) + ' .ct-bubble');
    ok('Handy: Antippen waehlt weitere', (await selN(h)) === '2 ausgewählt', await selN(h));
    const fit = await h.evaluate(() => ({ sw: document.documentElement.scrollWidth, w: innerWidth, bar: [...document.querySelectorAll('#ct-selact button')].every(b => b.getBoundingClientRect().right <= innerWidth + 1) }));
    ok('Handy: kein waagrechtes Scrollen, Aktionsleiste passt (390 px)', fit.sw <= fit.w && fit.bar, fit);
    await shot(h, 'auswahl-hell-handy');
    await h.click('#ct-selact .ct-sel-del'); await h.waitForSelector('.ct-del-dlg');
    await shot(h, 'loeschen-hell-handy');
    await h.click('.ct-dlg .hk-dlg-head .icon-btn');
    await h.click('#ct-selbar .ct-sel-x');
    ok('Handy: x beendet die Auswahl', !(await h.$('#ct-selbar')));
    await h.click('#ct-sub .ct-x3i'); await h.waitForSelector('.menu .ct-x3-pop');
    await shot(h, 'hinweis-hell-handy');
    // Desktop hell (Auswahl) fuer den Vergleich
    await a.emulateMedia({ colorScheme: 'light' });
    await menuOf(a, e1.id); await a.click('.menu .menu-item:has-text("Auswählen")'); await a.click(rowSel(e4.id) + ' .ct-bubble');
    await shot(a, 'auswahl-hell');
    await a.click('#ct-selact .ct-sel-del'); await a.waitForSelector('.ct-del-dlg');
    await shot(a, 'loeschen-hell');
    await a.click('.ct-dlg .hk-dlg-head .icon-btn'); await a.keyboard.press('Escape');
    // Handy dunkel
    await h.emulateMedia({ colorScheme: 'dark' }); await h.keyboard.press('Escape');
    await h.waitForTimeout(300);
    const hr2 = await rowByText(h, 'Erste Nachricht');
    const b3 = await h.locator(rowSel(hr2.id) + ' .ct-bubble').boundingBox();
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: Math.round(b3.x + 20), y: Math.round(b3.y + b3.height / 2) }] });
    await h.waitForTimeout(700);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await until(() => selN(h), 3000);
    await shot(h, 'auswahl-dunkel-handy');
    ok('Keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e).slice(0, 800));
  } finally {
    await browser.close();
  }
  const n = res.filter(Boolean).length;
  console.log(`\nBROWSER-CHAT-AUSWAHL ${n}/${res.length} OK – ` + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
