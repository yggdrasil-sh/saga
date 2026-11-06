// Browser-Ende-zu-Ende fuer den Chat-Alltag (2.5, static/chat.js, static/chat-fmt.js; saga/chat_prefs.py) - zwei
// Playwright-Kontexte (zwei Konten) im headless Chrome:
//   SAGA_BASE=... SAGA_USER=konto-a SAGA_PASS=... SAGA_USER2=konto-b SAGA_PASS2=... [SHOTS=/pfad] node tests/browser_chat_alltag.js
//   (Konto B braucht den Anzeigenamen "Bruno Web"; Linkvorschau: Server mit SAGA_UNFURL_TEST_PUBLIC=127.0.0.2)
// Geprueft: Formatierung (fett/kursiv/durch/code/Block/Liste/Zitat) als Elemente, HTML/Skript im Text bleibt Text (kein
// Element, kein Alert, keine Seitenfehler); @-Menue mit Mitglied, Token <@kennung> im verschluesselten Text, Hervorhebung
// beim Erwaehnten, mentions an den Server (nie Anmeldename); Entwurf bleibt beim Chatwechsel und nach Neuladen, nach dem
// Senden weg; Emoji-Auswahl fuegt an der Schreibmarke ein; Linkvorschau erscheint (Testserver), laesst sich entfernen und
// wird dann nicht mitgeschickt; Stummschalten-Untermenue mit Dauer, Anheften (Symbol), hell/dunkel, Handy-Breite.
// Kein waitForFunction (CSP ohne eval). Fuer die Linkvorschau: Server mit SAGA_UNFURL_TEST_PUBLIC=127.0.0.2.
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os'), http = require('http');
const E = process.env, BASE = E.SAGA_BASE;
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', PRE = E.SHOT_PREFIX || 'alltag-';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
async function shot(p, name) { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await p.screenshot({ path: path.join(SHOTS, PRE + name + '.png') }); } }
async function until(fn, ms = 20000, step = 250) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1280, height: 860 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark',
                                         isMobile: !!opts.mobile, hasTouch: !!opts.mobile, deviceScaleFactor: opts.mobile ? 2 : 1 });
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
const lastRow = p => p.evaluate(() => { const r = [...document.querySelectorAll('#ct-msgs .ct-msg')].pop(); return r ? { id: r.dataset.id, html: r.querySelector('.ct-text') ? r.querySelector('.ct-text').innerHTML : '', text: (r.querySelector('.ct-text') || {}).textContent || '', ment: r.classList.contains('ct-ment'), unf: !!r.querySelector('.ct-unf-msg') } : null; });
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'X-Saga-Chat': '3', 'Content-Type': 'application/json' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.json().then(d => ({ s: r.status, d }))), [m, u, b]);
async function send(p, text) {
  const before = (await lastRow(p) || {}).id;
  await p.fill('#ct-input', text); await p.press('#ct-input', 'Enter');
  return until(async () => { const r = await lastRow(p); return r && r.id !== before ? r : null; }, 15000);
}

function testServer() {
  return new Promise(resolve => {
    const srv = http.createServer((req, rs) => {
      if (req.url === '/artikel') { rs.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' }); rs.end('<html><head><meta property="og:title" content="Ein Artikel zum Test"><meta property="og:description" content="Kurz und knapp"><meta property="og:site_name" content="Testblatt"></head><body></body></html>'); }
      else { rs.writeHead(404); rs.end(); }
    });
    srv.listen(0, '127.0.0.2', () => resolve(srv));
  });
}

(async () => {
  const browser = await chromium.launch({ channel: E.PW_CHANNEL || 'chrome', headless: true });
  const U1 = E.SAGA_USER, U2 = E.SAGA_USER2;
  const srv = await testServer().catch(() => null);
  try {
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2, { scheme: 'light' });
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    // Direkt-Chat A -> B, zweiter Chat (Seiten-Chat) fuer den Entwurfswechsel
    // genau nach dem Anmeldenamen suchen: der Anzeigename "Bruno Web" kann in einer Test-DB mehrfach vorkommen (dann traf
    // der erste Treffer ein fremdes Konto, B sah den Chat nicht - "Empfaenger: Nachricht hervorgehoben" scheiterte)
    const ppl = await api(a, 'GET', '/api/chat/people?q=' + encodeURIComponent(U2));
    const bref = ((ppl.d.users || [])[0] || {}).username;
    const dm = await api(a, 'POST', '/api/chats', { kind: 'dm', user: bref });
    const pg = await api(a, 'POST', '/api/notes', { title: 'Alltag-Seite' });
    const pc = await api(a, 'POST', '/api/chats', { kind: 'page', note_id: pg.d.id });
    ok('Chats angelegt', dm.s === 200 && pc.s === 200, [dm.s, pc.s]);
    const dmId = dm.d.chat.id, pcId = pc.d.chat.id;
    await a.goto(BASE + '/chats#c' + dmId); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);

    // --- Formatierung, XSS-sicher ---
    const r1 = await send(a, '*fett* _kursiv_ ~weg~ `x<y`\n- eins\n- zwei\n> zitiert\n```\n<b>block</b>\n```');
    ok('Formatierung: strong/em/s/code/ul/blockquote/pre', r1 && /<strong[^>]*>fett<\/strong>/.test(r1.html) && /<em[^>]*>kursiv<\/em>/.test(r1.html) && /<s[^>]*>weg<\/s>/.test(r1.html)
       && /<code class="ct-f-code">x&lt;y<\/code>/.test(r1.html) && /<ul class="ct-f-list"><li>eins<\/li><li>zwei<\/li><\/ul>/.test(r1.html)
       && /<blockquote/.test(r1.html) && /<pre class="ct-f-pre"><code>&lt;b&gt;block&lt;\/b&gt;<\/code><\/pre>/.test(r1.html), r1 && r1.html);
    const r2 = await send(a, '<img src=x onerror="alert(1)"> *<script>alert(2)</script>* [x](javascript:alert(3)) a_b_c 2*3*4');
    const xss = await a.evaluate(id => { const t = document.querySelector('#ct-msgs .ct-msg[data-id="' + id + '"] .ct-text'); return { img: t.querySelectorAll('img').length, script: t.querySelectorAll('script').length, a: [...t.querySelectorAll('a')].map(x => x.getAttribute('href')), strong: t.querySelectorAll('strong').length, text: t.textContent }; }, r2.id);
    ok('XSS: HTML/Skript bleibt Text, keine javascript:-Links, a_b_c/2*3*4 unformatiert', xss.img === 0 && xss.script === 0 && !xss.a.some(h => /^javascript/i.test(h || ''))
       && xss.text.includes('<img src=x onerror="alert(1)">') && xss.text.includes('a_b_c 2*3*4') && xss.strong === 1, xss);
    await shot(a, 'format');

    // --- @-Menue + Erwaehnung ---
    await a.fill('#ct-input', ''); await a.type('#ct-input', 'Hallo @Bru');
    const pick = await until(() => a.evaluate(() => { const x = document.querySelector('#ct-picker:not([hidden]) .ct-pick-p'); return x ? x.textContent : null; }), 6000);
    ok('@-Menue zeigt das Mitglied', pick && pick.includes('Bruno Web'), pick);
    await shot(a, 'at-menu');
    await a.keyboard.press('Enter');
    const val = await a.inputValue('#ct-input');
    ok('Auswahl setzt "@Bruno Web "', val === 'Hallo @Bruno Web ', val);
    let sentBody = null;
    a.on('request', rq => { if (rq.method() === 'POST' && /\/api\/chats\/\d+\/messages$/.test(rq.url())) { try { sentBody = JSON.parse(rq.postData()); } catch (e) { /* egal */ } } });
    await a.type('#ct-input', 'schau mal');
    const r3 = await send(a, await a.inputValue('#ct-input'));
    ok('mentions an den Server: genau eine Kennung (Handle), kein Anmeldename', sentBody && Array.isArray(sentBody.mentions) && sentBody.mentions.length === 1
       && /^~[a-z2-7]{16}/.test(sentBody.mentions[0]) && !JSON.stringify(sentBody.mentions).includes(U2), sentBody && sentBody.mentions);
    ok('Absender sieht "@Bruno Web" als Erwaehnung', r3 && /class="ct-mention"[^>]*>@Bruno Web</.test(r3.html), r3 && r3.html);
    await b.goto(BASE + '/chats#c' + dmId); await b.waitForSelector('#ct-chat:not([hidden])');
    const rb = await until(async () => { const r = await lastRow(b); return r && r.id === r3.id ? r : null; });
    ok('Empfaenger: Nachricht hervorgehoben (ct-ment, ct-mention-me)', rb && rb.ment && /ct-mention-me/.test(rb.html), rb);
    await shot(b, 'mention-light');

    // --- Emoji ---
    await a.fill('#ct-input', 'AB'); await a.focus('#ct-input');
    await a.evaluate(() => { const i = document.querySelector('#ct-input'); i.setSelectionRange(1, 1); });
    await a.click('#ct-emoji'); await a.waitForSelector('.menu .em-b');
    await a.click('.menu .em-grid .em-b >> nth=0');
    const ev = await a.inputValue('#ct-input');
    ok('Emoji an der Schreibmarke eingefuegt', ev.length > 2 && ev.startsWith('A') && ev.endsWith('B') && ev !== 'AB', ev);

    // --- Entwurf ---
    await a.fill('#ct-input', 'Entwurf fuer Bruno');
    await a.type('#ct-input', '!'); await a.waitForTimeout(600);
    await a.goto(BASE + '/chats#c' + pcId); await a.waitForTimeout(1200);
    const inPc = await a.inputValue('#ct-input');
    ok('anderer Chat: Eingabe leer', inPc === '', inPc);
    const badge = await a.evaluate(id => { const x = document.querySelector('.ct-item-b[data-id="' + id + '"] .ct-draft'); return x ? x.textContent : null; }, dmId);
    ok('Liste: Hinweis "Entwurf"', badge === 'Entwurf', badge);
    await a.click('.ct-item-b[data-id="' + dmId + '"]'); await a.waitForTimeout(1200);
    ok('zurueck: Entwurf wieder da', (await a.inputValue('#ct-input')) === 'Entwurf fuer Bruno!');
    await a.reload(); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(1500);
    ok('nach Neuladen: Entwurf wieder da', (await a.inputValue('#ct-input')) === 'Entwurf fuer Bruno!');
    const ls = await a.evaluate(() => Object.keys(localStorage).filter(k => k.indexOf('saga-chat-draft:') === 0));
    ok('Entwurf nur in localStorage (je Konto)', ls.length === 1 && ls[0].indexOf('saga-chat-draft:' + E.SAGA_USER + ':') === 0, ls);
    await a.press('#ct-input', 'Enter'); await a.waitForTimeout(1500);
    ok('nach dem Senden: Entwurf weg', (await a.evaluate(() => Object.keys(localStorage).filter(k => k.indexOf('saga-chat-draft:') === 0).length)) === 0);

    // --- Linkvorschau ---
    const ver = await api(a, 'GET', '/api/version');
    if (srv && (ver.d.features || []).includes('unfurl1')) {
      const url = 'http://127.0.0.2:' + srv.address().port + '/artikel';
      await a.fill('#ct-input', 'Lies ' + url); await a.type('#ct-input', ' ');
      const card = await until(() => a.evaluate(() => { const c = document.querySelector('#ct-unf:not([hidden]) .ct-unf-title'); return c ? c.textContent : null; }), 8000);
      ok('Vorschau erscheint beim Schreiben', card === 'Ein Artikel zum Test', card);
      await shot(a, 'unfurl');
      await a.click('#ct-unf .ct-unf-x');
      ok('Vorschau entfernt', await a.evaluate(() => document.querySelector('#ct-unf').hidden));
      sentBody = null;
      const r4 = await send(a, await a.inputValue('#ct-input'));
      ok('entfernte Vorschau wird nicht mitgeschickt', r4 && !r4.unf && sentBody && !(sentBody.files || []).length, r4);
      await a.fill('#ct-input', 'Nochmal ' + url); await a.type('#ct-input', ' ');
      await until(() => a.evaluate(() => !!document.querySelector('#ct-unf:not([hidden]) .ct-unf-title')), 8000);
      const r5 = await send(a, await a.inputValue('#ct-input'));
      ok('Vorschau in der Nachricht (verschluesselt mitgeschickt)', r5 && r5.unf, r5);
      const rb5 = await until(async () => { const r = await lastRow(b); return r && r.id === r5.id ? r : null; });
      ok('Empfaenger sieht die Vorschau', rb5 && rb5.unf, rb5);
    } else ok('Linkvorschau-Testserver (SAGA_UNFURL_TEST_PUBLIC=127.0.0.2, Feature unfurl1)', false, ver.d.features);

    // --- Stummschalten / Anheften ueber das Menue ---
    await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
    const items = await a.evaluate(() => [...document.querySelectorAll('.menu .menu-item .mi-t')].map(x => x.textContent));
    ok('Kopfmenue: Stummschalten, Anheften, Medien', items.includes('Stummschalten') && items.includes('Anheften') && items.includes('Medien, Dateien, Links'), items);
    await a.click('.menu .menu-item:has-text("Stummschalten")'); await a.waitForTimeout(300);
    const subs = await a.evaluate(() => [...document.querySelectorAll('.menu-sub .menu-item .mi-t, .menu .menu-item .mi-t')].map(x => x.textContent));
    ok('Untermenue: Dauer + Modus', subs.includes('1 Stunde') && subs.includes('Für immer') && subs.includes('Nur Erwähnungen und Antworten'), subs);
    await shot(a, 'mute-menu');
    await a.click('.menu-item:has-text("8 Stunden")'); await a.waitForTimeout(800);
    const st = await api(a, 'GET', '/api/chats/' + dmId);
    ok('8 Stunden stumm (muted_until ~ +8 h)', st.d.chat.muted && Math.abs(st.d.chat.muted_until - Date.now() / 1000 - 8 * 3600) < 120, st.d.chat.muted_until);
    await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
    await a.click('.menu .menu-item:has-text("Anheften")'); await a.waitForTimeout(1000);
    ok('angeheftet: 📌 in der Liste', !!(await a.$('.ct-item-b[data-id="' + dmId + '"] .ct-pin')));
    await a.click('#ct-more'); await a.waitForSelector('.menu .menu-item');
    await a.click('.menu .menu-item:has-text("Medien, Dateien, Links")'); await a.waitForSelector('.ct-md-tabs');
    await a.click('.ct-md-tab[data-tab="links"]'); await a.waitForTimeout(500);
    const links = await a.evaluate(() => [...document.querySelectorAll('.ct-md-link')].map(x => x.textContent));
    ok('Medienuebersicht: Links aus den Nachrichten', links.some(l => l.includes('/artikel')) || !srv, links);
    await shot(a, 'media');
    await a.keyboard.press('Escape');
    await api(a, 'PATCH', '/api/chats/' + dmId, { muted_until: 0, pinned: false });
    // Handy-Breite
    const m = await session(browser, U2, E.SAGA_PASS2, { mobile: true, viewport: { width: 390, height: 800 } });
    await chatKeys(m, U2);
    await m.goto(BASE + '/chats#c' + dmId); await m.waitForSelector('#ct-chat:not([hidden])'); await m.waitForTimeout(1500);
    const sw = await m.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    ok('Handy-Breite: kein waagrechtes Scrollen', sw);
    await shot(m, 'mobile');
  } catch (e) {
    console.log(e); res.push(false);
  } finally {
    if (srv) srv.close();
    await browser.close();
  }
  ok('keine Seitenfehler/Dialoge', errs.length === 0, errs);
  const n = res.filter(Boolean).length;
  console.log('\nBROWSER-CHAT-ALLTAG ' + n + '/' + res.length + ' - ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
