// Browser-Regressionstests fuer die Weboberflaeche (Playwright, headless Chrome) - laufen NICHT im Stack-Testlauf,
// sondern von einem Rechner mit Playwright gegen einen laufenden Test-Stack:
//   SAGA_BASE=http://host:8175 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] node tests/browser_web.js
// Das Konto braucht nichts Besonderes (legt eigene Seiten/Datenbanken an). Ausgabe wie die Python-Tests: OK/FAIL je Pruefung.
// Optional SAGA_USER2/SAGA_PASS2 + SAGA_USER3/SAGA_PASS3 (zwei Konten nur fuer Chats) fuer den Chat-Test "Aus Immich"
// (Immich wird im Browser nachgestellt; der Wiederherstellungsschluessel wird im Temp-Verzeichnis gemerkt).
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

async function session(browser, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1366, height: 860 }, locale: opts.locale || 'de-DE',
    isMobile: !!opts.mobile, hasTouch: !!opts.mobile });
  const p = await ctx.newPage();
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', opts.user || USER);
  await p.fill('input[type=password]', opts.pass || PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.blocks = async id => { const n = await p.api('GET', '/api/notes/' + id); return (JSON.parse(n.body || '{}').blocks || []).map(b => b.type + ':' + (b.data.text || '')); };
  p.open = async id => { await p.goto(BASE + '/notes#' + id); await p.waitForSelector('#pg-editor .ce-paragraph, .db-row, .cal-grid'); await p.waitForTimeout(800); };
  return p;
}

// BUG-011: Enter, das Editor.js nicht selbst behandelt (Fehlerzustand nach "/Ueberschrift", Handy-Tastatur), darf kein
// <div> in den Block setzen (beim Speichern verworfen -> "KuchenMehl"), sondern teilt den Block.
async function enterFallback(p) {
  const id = (await p.api('POST', '/api/notes', { title: 'Enter-Test' })).id;
  await p.open(id);
  await p.click('#pg-editor .ce-paragraph');
  await p.keyboard.type('/Überschrift', { delay: 40 }); await p.keyboard.press('Enter');
  await p.keyboard.type('Kuchen', { delay: 30 });
  await p.evaluate(() => { window.__blk = e => { if (e.key === 'Enter') e.stopPropagation(); }; window.addEventListener('keydown', window.__blk, true); });
  await p.keyboard.press('Enter'); await p.keyboard.type('Mehl', { delay: 30 });
  await p.keyboard.press('ArrowLeft'); await p.keyboard.press('ArrowLeft'); await p.keyboard.press('Enter');
  await p.evaluate(() => window.removeEventListener('keydown', window.__blk, true));
  await p.keyboard.type('Zucker', { delay: 30 }); await p.waitForTimeout(2200);
  const b = await p.blocks(id);
  ok('BUG-011 Enter ohne Editor.js-Behandlung teilt den Block', JSON.stringify(b) === JSON.stringify(['header:Kuchen', 'paragraph:Me', 'paragraph:Zuckerhl']), b);
}

// BUG-012: Neu laden direkt nach dem Tippen (Seite + Namenszelle einer Datenbank) verliert nichts
async function reloadKeeps(p) {
  const id = (await p.api('POST', '/api/notes', { title: 'Reload-Test' })).id;
  await p.open(id);
  await p.click('#pg-editor .ce-paragraph'); await p.keyboard.type('Samstag: Markt', { delay: 15 });
  await p.reload(); await p.waitForTimeout(1500);
  ok('BUG-012 Seite: Text nach sofortigem Neuladen gespeichert', (await p.blocks(id)).some(x => x.includes('Samstag: Markt')));
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Reload-DB' })).id;
  const R = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Kita' })).id;
  await p.open(D);
  await p.locator('.db-row .db-title-in').first().click(); await p.keyboard.press('End'); await p.keyboard.type(' Y');
  await p.reload(); await p.waitForTimeout(1500);
  ok('BUG-012 Datenbank-Zelle: Name nach sofortigem Neuladen gespeichert', (await p.api('GET', '/api/notes/' + R)).title === 'Kita Y');
}

// BUG-013: Zahlen in der Sprache der Oberflaeche ("1.200" de = 1200, "1,200" en = 1200)
async function numbers(p, lang) {
  const de = lang === 'de';
  const unit = await p.evaluate(() => [['1.200'], ['1,5'], ['1.234.567,25'], ['1,234,567.25'], ['2.5'], ['-3'], ['12,'], ['abc'], ['1.2.3'], ['1 200']]
    .map(([s]) => [s, HK.parseNum(s)]));
  const want = de ? [1200, 1.5, 1234567.25, 1234567.25, 2.5, -3, 12, NaN, NaN, 1200] : [1.2, 1.5, 1234567.25, 1234567.25, 2.5, -3, 12, NaN, NaN, 1200];
  // en: "1,5" ist kein Tausender (keine 3er-Gruppe) -> Komma als Dezimalzeichen geduldet; "1.200" = 1,2
  ok('BUG-013 parseNum ' + lang, unit.every(([s, v], i) => Number.isNaN(want[i]) ? v === null || Number.isNaN(v) : v === want[i]), unit);
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Zahlen ' + lang }));
  const s = (await p.api('GET', '/api/notes/' + D.id)).schema; s.props.push({ id: 'num', name: 'Betrag', type: 'number' });
  await p.api('PATCH', '/api/notes/' + D.id, { schema: s });
  const R = (await p.api('POST', '/api/notes', { parent_id: D.id, title: 'Miete' })).id;
  await p.open(D.id);
  const cell = p.locator('.db-row .val-number').first(), inp = cell.locator('input');
  await inp.click(); await p.keyboard.type(de ? '1.200' : '1,200'); await inp.press('Enter'); await p.waitForTimeout(700);
  ok('BUG-013 Zelle "' + (de ? '1.200' : '1,200') + '" = 1200 (' + lang + ')', ((await p.api('GET', '/api/notes/' + R)).props || {}).num === 1200);
}

// BUG-014: Konflikt-Kasten bleibt bei gescrollter Seite unter der festen Kopfleiste sichtbar
async function conflictVisible(p) {
  const blocks = Array.from({ length: 30 }, (_, i) => ({ id: 'b' + i, type: 'paragraph', data: { text: 'Absatz ' + i } }));
  const id = (await p.api('POST', '/api/notes', { title: 'Konflikt-Test' })).id;
  await p.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' });
  await p.open(id);
  await p.locator('#pg-editor .ce-paragraph').nth(4).click(); await p.mouse.wheel(0, 300); await p.waitForTimeout(300);
  const g = await p.api('GET', '/api/notes/' + id);
  await p.api('PATCH', '/api/notes/' + id, { title: 'Konflikt-Test 2', rev: g.rev });
  await p.keyboard.type(' neu'); await p.waitForTimeout(2500);
  const m = await p.evaluate(() => { const c = document.getElementById('pg-conflict'), h = document.querySelector('.np-head');
    return c && { top: c.getBoundingClientRect().top, head: h ? h.getBoundingClientRect().bottom : 0 }; });
  ok('BUG-014 Konflikt-Kasten sichtbar (nicht unter der Kopfleiste)', m && m.top >= m.head - 1, m);
}

// BUG-015: Seitenkopf auf Handy (390 px) und Tablet (820 px) ohne Ueberlauf
async function narrow(browser) {
  for (const [name, vp, mobile] of [['Handy', { width: 390, height: 844 }, true], ['Tablet', { width: 820, height: 1180 }, false]]) {
    const p = await session(browser, { viewport: vp, mobile });
    const id = (await p.api('POST', '/api/notes', { title: 'Projektplan Sommerfest mit langem Namen' })).id;
    await p.api('POST', '/api/notes/' + id + '/comments', { block_id: 'x', body: 'Kommentar' }).catch(() => {});
    await p.open(id); await p.waitForTimeout(700);
    const m = await p.evaluate(() => ({ vw: innerWidth, sw: document.documentElement.scrollWidth,
      more: Math.round(document.getElementById('pg-more').getBoundingClientRect().right) }));
    ok('BUG-015 ' + name + ': kein Ueberlauf, Seitenmenue erreichbar', m.vw === vp.width && m.sw <= m.vw && m.more <= m.vw, m);
    await p.context().close();
  }
}

// BUG-047: sofort nach "+ Neue Seite" > "Leere Seite" getippter Titel geht nicht verloren
async function typeAheadTitle(p) {
  await p.goto(BASE + '/notes'); await p.waitForSelector('#pg-new'); await p.waitForTimeout(800);
  await p.click('#pg-new'); await p.waitForTimeout(150);
  await p.locator('.menu .menu-item, .menu button').filter({ hasText: /Leere Seite/ }).first().click();
  await p.keyboard.type('Rezepte', { delay: 70 }); await p.waitForTimeout(2500);
  const id = await p.evaluate(() => parseInt(location.hash.slice(1), 10));
  ok('BUG-047 Titel sofort nach "Leere Seite" vollstaendig', (await p.api('GET', '/api/notes/' + id)).title === 'Rezepte');
}

// BUG-056: Link auf eigene Seite im Papierkorb -> Hinweis + Wiederherstellen (Unterseite: Hinweis auf den Papierkorb)
async function trashLink(p) {
  const P = (await p.api('POST', '/api/notes', { title: 'Papierkorb-Eltern' })).id;
  const K = (await p.api('POST', '/api/notes', { title: 'Papierkorb-Kind', parent_id: P })).id;
  await p.api('DELETE', '/api/notes/' + P);
  const box = async id => { await p.goto(BASE + '/notes#' + id); await p.waitForTimeout(2000);
    return p.evaluate(() => { const b = document.getElementById('pg-noaccess'); return b && !b.hidden ? b.textContent : ''; }); };
  ok('BUG-056 Unterseite: Hinweis auf den Papierkorb', /Papierkorb/.test(await box(K)));
  ok('BUG-056 Seite selbst: "liegt im Papierkorb" + Wiederherstellen', /liegt im Papierkorb/.test(await box(P)) && await p.locator('#pg-noaccess .pg-na-restore').isVisible());
  await p.click('#pg-noaccess .pg-na-restore'); await p.waitForTimeout(1500);
  ok('BUG-056 Wiederherstellen oeffnet die Seite', (await p.inputValue('#pg-title')) === 'Papierkorb-Eltern');
}

// BUG-049/BUG-001: Datenbank in einer zweiten Sitzung offen - Zeile und neue Spalte aus der ersten kommen live an
async function dbLive(browser, p) {
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Live-DB' })).id;
  const R = (await p.api('POST', '/api/notes', { parent_id: D, title: 'Alt' })).id;
  const q = await session(browser);
  await q.open(D); await q.waitForTimeout(1500);
  await p.api('PATCH', '/api/notes/' + R, { title: 'Neu von nebenan' });
  await q.waitForTimeout(3500);
  const names = await q.evaluate(() => [...document.querySelectorAll('.db-row .db-title-in')].map(x => x.value || x.textContent));
  ok('BUG-049 Zeile aus anderer Sitzung erscheint live', names.some(x => /Neu von nebenan/.test(x)), names);
  const s = (await p.api('GET', '/api/notes/' + D)).schema;
  s.props.push({ id: 'plive', name: 'Spalte live', type: 'text' });
  await p.api('PATCH', '/api/notes/' + D, { schema: s });
  await q.waitForTimeout(3500);
  ok('BUG-049 neue Spalte aus anderer Sitzung erscheint live', /Spalte live/.test(await q.evaluate(() => document.getElementById('pg-db').textContent)));
  await q.context().close();
}

// 2.1: "Verschieben nach" (Untermenue mit langen Pfaden) aus dem Peek-Rahmen: nie links abgeschnitten; ist neben
// dem Seitenmenue kein Platz, tritt es an dessen Stelle (Seitenmenue ausgeblendet, "‹ Verschieben nach" zurueck).
async function peekMove(browser, p) {
  const a = (await p.api('POST', '/api/notes', { title: 'Notion-Import Peek' })).id;
  const b = (await p.api('POST', '/api/notes', { title: 'Projekte und Kundenarchiv 2019 bis 2024', parent_id: a })).id;
  for (let i = 1; i <= 6; i++) await p.api('POST', '/api/notes', { title: 'Sehr lange Unterseite mit Namen Nummer ' + i, parent_id: b });
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title: 'Peek-DB' })).id;
  await p.api('POST', '/api/notes', { parent_id: D, title: 'Entry' });
  for (const [w, narrow] of [[1366, false], [480, true]]) {
    const q = await session(browser, { viewport: { width: w, height: 820 } });
    await q.goto(BASE + '/notes#' + D); await q.waitForSelector('.db-row'); await q.waitForTimeout(800);
    await q.evaluate(() => document.querySelector('.db-row .db-open').click());
    let f = null;
    for (let i = 0; i < 40 && !f; i++) {
      await q.waitForTimeout(250);
      const c = q.frames().find(x => x.url().includes('embed=1'));
      if (c && await c.evaluate(() => !!(window.HKPages && document.querySelector('#pg-more') && document.querySelector('#pg-title'))).catch(() => false)) f = c;
    }
    await q.waitForTimeout(600);
    await f.click('#pg-more'); await q.waitForTimeout(200);
    await f.evaluate(() => [...document.querySelectorAll('.menu-item')].find(x => /Verschieben nach/.test(x.textContent)).click());
    await q.waitForTimeout(300);
    const r = await f.evaluate(() => {
      const m = document.querySelector('.menu:not(.menu-sub)'), s = document.querySelector('.menu-sub'), a = m.getBoundingClientRect(), b = s.getBoundingClientRect();
      return { W: innerWidth, sl: b.left, sr: b.right, covered: getComputedStyle(m).visibility === 'hidden', back: !!s.querySelector('.menu-back'),
        apart: a.right - 2 <= b.left || b.right - 2 <= a.left };
    });
    ok(`2.1 Peek ${w}px: "Verschieben nach" ganz im Rahmen`, r.sl >= 0 && r.sr <= r.W, r);
    ok(`2.1 Peek ${w}px: Seitenmenue ${narrow ? 'ausgeblendet, Zurueck-Eintrag' : 'daneben, nicht darunter'}`, narrow ? r.covered && r.back : !r.covered && r.apart, r);
    if (narrow) {
      await f.click('.menu-back'); await q.waitForTimeout(200);
      ok('2.1 Peek: Zurueck zeigt wieder das Seitenmenue', await f.evaluate(() => document.querySelector('.menu-sub').hidden
        && getComputedStyle(document.querySelector('.menu:not(.menu-sub)')).visibility === 'visible'));
    }
    await q.context().close();
  }
}

// 2.1: Kommentar-Auszug bei Tabelle/Checkliste: Zellen/Punkte getrennt, keine Werkzeugleisten-Texte
async function commentSnippet(p) {
  const id = (await p.api('POST', '/api/notes', { title: 'Auszug-Test' })).id;
  const blocks = [{ id: 'tb1', type: 'table', data: { withHeadings: true, content: [['Name', 'Menge'], ['Milch', '2']] } },
    { id: 'cl1', type: 'list', data: { style: 'checklist', meta: {}, items: [{ content: 'Milch', meta: { checked: true }, items: [] }, { content: 'Brot', meta: { checked: false }, items: [] }] } }];
  await p.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ time: Date.now(), blocks, version: '2.31.0' }) });
  for (const b of ['tb1', 'cl1']) await p.api('POST', `/api/notes/${id}/comments`, { block_id: b, body: 'Kommentar zu ' + b });
  await p.open(id);
  await p.evaluate(() => HKComments.openAll()); await p.waitForTimeout(800);
  const s = await p.evaluate(() => [...document.querySelectorAll('.cm-where-t')].map(x => x.textContent));
  ok('2.1 Kommentar-Auszug Tabelle: Zellen getrennt, ohne Werkzeugleiste', s.some(x => x === 'Name · Menge · Milch · 2'), s);
  ok('2.1 Kommentar-Auszug Checkliste: Punkte getrennt', s.some(x => x === 'Milch · Brot'), s);
}

// 2.1: Chat-Platzhalter auf dem Handy kurz; "Aus Immich" im Chat (Immich-Antworten nachgestellt, Upload verschluesselt)
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');
// Chat-Schluessel im (frischen) Browser: beim ersten Mal einrichten und den Wiederherstellungsschluessel in einer
// Datei im Temp-Verzeichnis merken, bei spaeteren Laeufen damit entsperren; am Ende werden die Test-Geraete entfernt.
const RKF = u => require('path').join(require('os').tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  const fs = require('fs');
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
async function chatDevicesGone(q) {
  const me = await q.api('GET', '/api/chat/me');
  for (const d of (me.devices || []).filter(d => !d.revoked)) await q.api('DELETE', '/api/chat/devices/' + d.id);
}
async function chatImmich(browser) {
  const m = await session(browser, { viewport: { width: 390, height: 800 }, mobile: true });
  await m.goto(BASE + '/chats'); await m.waitForTimeout(1500);
  ok('2.1 Chat-Platzhalter auf dem Handy kurz', await m.evaluate(() => { const i = document.getElementById('ct-input'); return !i || i.placeholder === 'Nachricht …'; }));
  await m.context().close();
  const E = process.env;
  if (!E.SAGA_USER2 || !E.SAGA_USER3) { console.log('--   Chat mit Immich: SAGA_USER2/3 (+ _PASS) fehlen - uebersprungen'); return; }
  const two = await session(browser, { user: E.SAGA_USER3, pass: E.SAGA_PASS3 }); await chatKeys(two, E.SAGA_USER3);
  await chatDevicesGone(two); await two.context().close();     // Empfaenger braucht nur seine Schluessel (Backup-Schluessel)
  const p = await session(browser, { user: E.SAGA_USER2, pass: E.SAGA_PASS2 });
  const ID = '5e1f0000-0000-4000-8000-000000000001';
  await p.route('**/api/settings/immich', r => r.fulfill({ json: { available: true, connected: true, name: 'Test', url: 'https://immich.example' } }));
  await p.route('**/api/immich/assets', r => r.fulfill({ json: { items: [{ id: ID, name: 'Strand.HEIC', mime: 'image/heic' }], next: null } }));
  const asked = [];
  await p.route('**/api/immich/thumb/**', r => { asked.push(r.request().url()); return r.fulfill({ body: PNG, contentType: 'image/png' }); });
  const up = [];
  p.on('request', r => { if (r.method() === 'POST' && /\/api\/chat/.test(r.url()) && /file/.test(r.url())) up.push(r.postDataBuffer()); });
  await chatKeys(p, E.SAGA_USER2);
  if (await p.$('.ct-ov')) { ok('2.1 Chat: Schluessel des Testkontos verfuegbar', false, 'Wiederherstellungsschluessel fehlt (' + RKF(E.SAGA_USER2) + ')'); await p.context().close(); return; }
  await p.click('#ct-new'); await p.fill('.ct-people-q', E.SAGA_USER3); await p.waitForSelector('.ct-person');
  await p.click('.ct-person'); await p.waitForSelector('#ct-chat:not([hidden])');
  await p.waitForTimeout(800);
  await p.click('#ct-attach'); await p.waitForTimeout(200);
  const items = await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item')].map(x => x.textContent));
  ok('2.1 Chat: Anhaengen bietet "Aus Immich"', items.some(x => /Aus Immich/.test(x)) && items.some(x => /Bild oder Datei/.test(x)), items);
  await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item')].find(x => /Aus Immich/.test(x.textContent)).click());
  await p.waitForSelector('.imm-tile'); await p.click('.imm-tile');
  await p.waitForSelector('#ct-files .ct-chip'); await p.waitForTimeout(300);
  ok('2.1 Chat: Immich-Bild als grosse Vorschau geholt (HEIC -> preview)', asked.some(u => u.includes(ID) && u.includes('size=preview')), asked);
  ok('2.1 Chat: Anhang-Chip "Strand.png"', /Strand\.png/.test(await p.textContent('#ct-files')));
  const n0 = await p.evaluate(() => document.querySelectorAll('.ct-img img').length);
  await p.fill('#ct-input', 'Foto aus Immich');
  await Promise.all([p.waitForResponse(r => /\/api\/chats\/\d+\/messages$/.test(r.url()) && r.request().method() === 'POST', { timeout: 30000 }).catch(() => {}),
    p.click('#ct-send')]);
  const shown = () => p.evaluate(n => { const a = document.querySelectorAll('.ct-img img'); return a.length > n && a[a.length - 1].naturalWidth > 0; }, n0);
  for (let i = 0; i < 60 && !await shown(); i++) await p.waitForTimeout(250);
  ok('2.1 Chat: Bild gesendet und wieder entschluesselt angezeigt', await shown(),
    await p.evaluate(() => ({ msgs: document.getElementById('ct-msgs').innerHTML.slice(-600), toast: (document.querySelector('.toast') || {}).textContent })));
  ok('2.1 Chat: Upload ist verschluesselt (keine PNG-Bytes)', up.length > 0 && up.every(b => b && b.indexOf(PNG.slice(0, 8)) < 0 && b.indexOf(PNG.slice(16, 40)) < 0), up.length);
  await chatDevicesGone(p); await p.context().close();
}

// 2.1 Passkeys im echten Browser: virtueller Authenticator (CDP WebAuthn.addVirtualAuthenticator) - anlegen auf
// /security (Wiederherstellungscodes), Anmeldung ohne Passwort, Passwort + Passkey als zweiter Faktor, wieder loeschen.
// Eigenes Wegwerf-Konto SAGA_PK_USER/SAGA_PK_PASS (bekommt kurz einen Passkey). Passkeys brauchen einen sicheren
// Kontext: SAGA_BASE mit https:// oder http://localhost (und BASE_URL des Stacks genau so) - sonst uebersprungen.
async function passkeys(browser) {
  const E = process.env, U = E.SAGA_PK_USER, PW = E.SAGA_PK_PASS;
  const secure = /^https:\/\//.test(BASE) || /^http:\/\/localhost(:\d+)?$/.test(BASE.replace(/\/$/, ''));
  if (!U || !PW || !secure) { console.log('--   Passkeys: SAGA_PK_USER/SAGA_PK_PASS fehlen oder SAGA_BASE nicht https/localhost - uebersprungen'); return; }
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  const cdp = await ctx.newCDPSession(p);
  await cdp.send('WebAuthn.enable');
  await cdp.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'internal', hasResidentKey: true,
    hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
  async function pwLogin() {
    await p.goto(BASE + '/login');
    await p.fill('input[name=username]', U); await p.fill('input[name=password]', PW);
    await Promise.all([p.waitForNavigation().catch(() => {}), p.press('input[name=password]', 'Enter')]);
  }
  const acct = () => p.evaluate(() => fetch('/api/account/security', { headers: { 'X-Saga': '1' } }).then(r => r.ok ? r.json() : null));
  await pwLogin();
  ok('2.1 Passkey: Konto ohne 2FA -> Dashboard', !/\/login/.test(p.url()), p.url());
  await p.goto(BASE + '/security'); await p.waitForSelector('#hk-security .mfa-block');
  await p.fill('#hk-security input[aria-label="Name des Passkeys"]', 'Browser-Test');
  await p.click('#hk-security .mfa-block button:has-text("Passkey hinzufügen")');
  await p.waitForSelector('.mfa-codes li', { timeout: 15000 }).catch(() => {});
  ok('2.1 Passkey: angelegt, 10 Wiederherstellungscodes angezeigt', (await p.$$('.mfa-codes li')).length === 10,
    await p.textContent('#hk-security').catch(() => ''));
  await p.click('.mfa-codes-box button:has-text("Ich habe sie gespeichert")');
  ok('2.1 Passkey: in der Liste mit Namen', /Browser-Test/.test(await p.textContent('#hk-security')));
  await p.goto(BASE + '/logout'); await p.goto(BASE + '/login');
  // Autofill (conditional mediation): der virtuelle Authenticator bestaetigt sofort -> Anmeldung ohne Klick.
  // Sonst (Browser ohne Autofill) ueber den Knopf "Mit Passkey anmelden".
  const auto = await p.waitForURL(u => !/\/login/.test(String(u)), { timeout: 6000 }).then(() => true, () => false);
  if (!auto) await Promise.all([p.waitForURL(u => !/\/login/.test(String(u)), { timeout: 15000 }).catch(() => {}), p.click('#pk-go')]);
  ok('2.1 Passkey: Anmeldung ohne Passwort (' + (auto ? 'Autofill' : 'Knopf') + ')', !/\/login/.test(p.url()) && !!(await acct()), p.url());
  // Ab hier ohne Autofill (wie ein Browser ohne conditional mediation): Knopf-Weg und Passwort-Anmeldung pruefen
  await p.addInitScript(() => { if (window.PublicKeyCredential) PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false); });
  await p.goto(BASE + '/logout'); await p.goto(BASE + '/login');
  await p.waitForSelector('#pk-box:not([hidden])');
  await Promise.all([p.waitForURL(u => !/\/login/.test(String(u)), { timeout: 15000 }).catch(() => {}), p.click('#pk-go')]);
  ok('2.1 Passkey: Knopf "Mit Passkey anmelden"', !/\/login/.test(p.url()) && !!(await acct()), p.url());
  await p.goto(BASE + '/logout');
  await pwLogin();
  ok('2.1 Passkey: Passwort -> Zwischenschritt', /\/login\/2fa/.test(p.url()), p.url());
  await Promise.all([p.waitForURL(u => !/\/login/.test(String(u)), { timeout: 15000 }).catch(() => {}), p.click('#pk-go')]);
  ok('2.1 Passkey: als zweiter Faktor bestaetigt -> angemeldet', !/\/login/.test(p.url()) && !!(await acct()), p.url());
  await p.goto(BASE + '/security'); await p.waitForSelector('#hk-security .hk-tok');
  await p.click('#hk-security .hk-tok button:has-text("Löschen")');
  await p.click('.hk-ask .btn-destr'); await p.waitForTimeout(1200);
  const s = await acct();
  ok('2.1 Passkey: geloescht -> 2FA wieder aus', s && !s.enabled && !s.passkeys.length, s);
  await ctx.close();
}

// 2.1 Profilbilder: Einstellungen -> Konto -> Profilbild hochladen (mit Zuschneiden), danach Bild statt Initialen in der
// Kopfzeile, im Teilen-Dialog und bei Kommentaren; kein Abruf bei fremden Servern (Gravatar laeuft nur ueber Saga).
async function avatars(browser) {
  const p = await session(browser), host = new URL(BASE).host, foreign = [];
  p.on('request', r => { const u = new URL(r.url()); if (!['data:', 'blob:'].includes(u.protocol) && u.host !== host) foreign.push(r.url()); });
  const loaded = sel => p.evaluate(s => [...document.querySelectorAll(s)].filter(x => x.classList.contains('hk-av-on')
    && x.querySelector('img.hk-av-img') && x.querySelector('img.hk-av-img').naturalWidth > 0).length, sel);
  const file = require('path').join(require('os').tmpdir(), 'saga-browser-avatar.png');
  const b64 = await p.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 900; c.height = 600; const g = c.getContext('2d');
    g.fillStyle = '#2a7'; g.fillRect(0, 0, 900, 600); g.fillStyle = '#fff'; g.beginPath(); g.arc(450, 260, 120, 0, 7); g.fill();
    return c.toDataURL('image/png').split(',')[1];
  });
  require('fs').writeFileSync(file, Buffer.from(b64, 'base64'));
  await p.goto(BASE + '/'); await p.waitForTimeout(800);
  await p.click('#hk-user'); await p.click('.menu-item:has-text("Einstellungen")');
  await p.waitForSelector('.hk-avsec'); await p.waitForTimeout(600);
  await p.setInputFiles('.hk-avsec input[type=file]', file);
  await p.waitForSelector('.hk-crop-stage');
  ok('2.1 Profilbild: Zuschneide-Dialog (quadratisch)', await p.evaluate(() => { const r = document.querySelector('.hk-crop-stage').getBoundingClientRect(); return Math.abs(r.width - r.height) < 2; }));
  await p.locator('.hk-crop input[type=range]').fill('1.5');
  await p.click('.hk-crop .btn-primary');
  for (let i = 0; i < 30 && !/Gespeichert/.test(await p.textContent('.hk-avsec .hk-status').catch(() => '') || ''); i++) await p.waitForTimeout(500);
  await p.waitForTimeout(1000);
  const st = await p.api('GET', '/api/account/avatar');
  ok('2.1 Profilbild: hochgeladen (Quelle: eigenes Bild)', st.source === 'upload' && /^\/api\/avatar\//.test(st.url || ''), st);
  ok('2.1 Profilbild: Vorschau und Kopfzeile zeigen das Bild', await loaded('.hk-avset-pic') === 1 && await loaded('#hk-user .hk-user-av') === 1);
  await p.keyboard.press('Escape');
  const nid = (await p.api('POST', '/api/notes', { title: 'Profilbild-Test' })).id;
  await p.api('POST', '/api/notes/' + nid + '/comments', { body: 'Kommentar mit Bild', block_id: '' });
  await p.open(nid);
  await p.click('#pg-cm-btn').catch(() => {}); await p.waitForTimeout(1200);
  ok('2.1 Profilbild: bei Kommentaren', await loaded('.cm-c .cm-av') >= 1);
  await p.keyboard.press('Escape');
  await p.goto(BASE + '/'); await p.waitForTimeout(800);
  await p.click('#hk-user'); await p.click('.menu-item:has-text("Einstellungen")');
  await p.waitForSelector('.hk-avsec'); await p.waitForTimeout(600);
  await p.click('.hk-avsec button:has-text("Eigenes Bild entfernen")');
  await p.waitForTimeout(1200);
  const after = await p.api('GET', '/api/account/avatar');
  ok('2.1 Profilbild: entfernt -> SSO-Bild, Gravatar oder Initialen', !after.upload && after.source !== 'upload', after);
  ok('2.1 Profilbild: keine Anfragen an fremde Server', foreign.length === 0, foreign.slice(0, 3));
  await p.api('DELETE', '/api/notes/' + nid);
  await p.context().close();
}

// 2.1 Anwesenheit mit Profilbild (static/live.js): wer spaeter kommt, erscheint beim anderen sofort (neu melden nach
// Kanal-Aufbau); unveraenderte Meldungen zeichnen nicht neu (kein Flackern), Aenderungen (bearbeitet) schon.
async function presence(browser) {
  const E = process.env;
  if (!E.SAGA_USER2 || !E.SAGA_USER3) { console.log('--   Anwesenheit: SAGA_USER2/3 (+ _PASS) fehlen - uebersprungen'); return; }
  const a = await session(browser, { user: E.SAGA_USER2, pass: E.SAGA_PASS2 }), b = await session(browser, { user: E.SAGA_USER3, pass: E.SAGA_PASS3 });
  const until = async (p, fn, ms) => { for (const t = Date.now(); Date.now() - t < ms; await p.waitForTimeout(150)) if (await p.evaluate(fn).catch(() => false)) return true; return false; };
  const up = await a.evaluate(async () => {
    const c = document.createElement('canvas'); c.width = c.height = 200; const g = c.getContext('2d'); g.fillStyle = '#c33'; g.fillRect(0, 0, 200, 200);
    const blob = await new Promise(r => c.toBlob(r, 'image/png'));
    return (await fetch('/api/account/avatar', { method: 'PUT', headers: { 'X-Saga': '1', 'Content-Type': 'image/png' }, body: blob })).status;
  });
  const nid = (await a.api('POST', '/api/notes', { title: 'Anwesenheit-Test' })).id;
  await a.api('PUT', '/api/notes/' + nid + '/shares', { type: 'user', target: E.SAGA_USER3, role: 'edit' });
  await a.goto(BASE + '/notes#' + nid); await a.waitForSelector('#pg-editor');
  await b.goto(BASE + '/notes#' + nid);
  ok('2.1 Anwesenheit: Profilbild der anderen Person', up === 200 && await until(b, () => {
    const x = document.querySelector('#pg-presence .lv-av.hk-av-on img.hk-av-img'); return !!x && x.naturalWidth > 0; }, 15000), up);
  ok('2.1 Anwesenheit: spaeter Gekommene erscheint sofort (nicht erst nach 20 s)', await until(a, () => !!document.querySelector('#pg-presence .lv-av'), 8000));
  await b.evaluate(() => {
    window.__rm = 0; window.__img = document.querySelector('#pg-presence img.hk-av-img');
    new MutationObserver(ms => ms.forEach(m => m.removedNodes.forEach(n => { if (n.nodeType === 1 && (n.matches('img') || n.querySelector('img'))) window.__rm++; })))
      .observe(document.querySelector('#page .page-bar'), { childList: true, subtree: true });
  });
  await b.waitForTimeout(45000);                                           // zwei Meldungen (alle 20 s)
  const fl = await b.evaluate(() => ({ rm: window.__rm, same: window.__img === document.querySelector('#pg-presence img.hk-av-img') }));
  ok('2.1 Anwesenheit: unveraendert -> kein Neuzeichnen (kein Flackern)', fl.rm === 0 && fl.same, fl);
  await a.click('#pg-editor .ce-paragraph').catch(() => {}); await a.keyboard.type('Hallo', { delay: 50 });
  ok('2.1 Anwesenheit: Aenderung (bearbeitet) wird gezeichnet', await until(b, () => !!document.querySelector('#pg-presence .lv-av.editing'), 25000));
  await a.api('DELETE', '/api/account/avatar'); await a.api('DELETE', '/api/notes/' + nid);
  await a.context().close(); await b.context().close();
}

// 2.1 Chat-Bilder schnell: Vorschaubild (th) beim Senden, im Verlauf nur das Vorschaubild (Platzhalter in Endgroesse),
// Original erst beim Oeffnen; alte Nachrichten ohne th -> eigenes Vorschaubild im Cache; Cache ueberlebt Neuladen und
// wird beim Abmelden geleert. Konten SAGA_USER2 (sendet) und SAGA_USER3 (empfaengt).
async function chatThumbs(browser) {
  const E = process.env;
  if (!E.SAGA_USER2 || !E.SAGA_USER3) { console.log('--   Chat-Bilder: SAGA_USER2/3 (+ _PASS) fehlen - uebersprungen'); return; }
  const file = require('path').join(require('os').tmpdir(), 'saga-browser-chatbild.png');
  const a = await session(browser, { user: E.SAGA_USER2, pass: E.SAGA_PASS2 });
  require('fs').writeFileSync(file, Buffer.from(await a.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 1600; c.height = 1200; const g = c.getContext('2d');
    for (let i = 0; i < 40; i++) { g.fillStyle = 'hsl(' + i * 9 + ',70%,50%)'; g.fillRect(i * 40, 0, 40, 1200); }
    return c.toDataURL('image/png').split(',')[1];
  }), 'base64'));
  await chatKeys(a, E.SAGA_USER2);
  const b = await session(browser, { user: E.SAGA_USER3, pass: E.SAGA_PASS3 }); await chatKeys(b, E.SAGA_USER3);
  if (await a.$('.ct-ov') || await b.$('.ct-ov')) { ok('2.1 Chat-Bilder: Schluessel der Testkonten verfuegbar', false); return; }
  const ups = [];
  a.on('response', r => { if (r.request().method() === 'POST' && /\/api\/chats\/\d+\/files$/.test(r.url())) r.json().then(d => ups.push(d.size), () => ups.push(-1)); });
  await a.click('#ct-new'); await a.fill('.ct-people-q', E.SAGA_USER3); await a.waitForSelector('.ct-person');
  await a.click('.ct-person'); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(800);
  // Direkt-Chat gibt es je Paar nur einmal: Nachrichten frueherer Laeufe (alle von USER2) erst entfernen
  const old = await a.evaluate(async uu => {
    const [u, me] = uu.split('|'), H = { headers: { 'X-Saga': '1' } }, cs = (await (await fetch('/api/chats', H)).json()).chats || [];
    const c = cs.find(x => x.kind === 'dm' && (x.members || []).some(m => m.username === u)); if (!c) return 0;
    let n = 0;
    for (;;) {
      const r = await (await fetch('/api/chats/' + c.id + '/messages?limit=200', H)).json();
      const ms = (r.messages || []).filter(m => m.kind !== 'call' && m.sender && m.sender.username === me);
      let k = 0;
      for (const m of ms) k += (await fetch('/api/chats/' + c.id + '/messages/' + m.id, { method: 'DELETE', headers: { 'X-Saga': '1' } })).ok ? 1 : 0;
      n += k; if (!k || n > 1000) break;
    }
    return n;
  }, [E.SAGA_USER3, E.SAGA_USER2].join('|')).catch(() => 0);
  if (old) { await a.reload(); await a.waitForTimeout(2000); await a.click('.ct-item-b'); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(800); }
  const send = async text => {
    await a.setInputFiles('#ct-file', file); await a.fill('#ct-input', text);
    await Promise.all([a.waitForResponse(r => /\/api\/chats\/\d+\/messages$/.test(r.url()) && r.request().method() === 'POST', { timeout: 30000 }).catch(() => {}),
      a.click('#ct-send')]);
    await a.waitForTimeout(1500);
  };
  await send('Bild mit Vorschau');
  ok('2.1 Chat-Bilder: Original + Vorschaubild hochgeladen (2 Blobs, Vorschau klein)', ups.length === 2 && Math.min(...ups) * 3 < Math.max(...ups), ups);
  // "alte" Nachricht ohne th nachstellen: Vorschaubild-Erzeugung beim Sender schlaegt fehl
  await a.evaluate(() => { window.createImageBitmap = () => Promise.reject(new Error('aus')); });
  await a.route('**/static/chat-worker.js*', r => r.fulfill({ status: 404, body: '' }));
  await a.reload(); await a.waitForTimeout(2500);
  await a.evaluate(() => { window.createImageBitmap = () => Promise.reject(new Error('aus')); });
  await a.click('.ct-item-b'); await a.waitForSelector('#ct-chat:not([hidden])'); await a.waitForTimeout(800);
  ups.length = 0; await send('Bild ohne Vorschau');
  ok('2.1 Chat-Bilder: ohne Vorschaubild gesendet (alte Clients) – 1 Blob', ups.length === 1, ups);
  // Empfaenger: nur Vorschaubilder laden, Platzhalter vorab in Endgroesse
  const got = [];
  b.on('request', r => { if (/\/api\/chat\/files\//.test(r.url())) got.push(r.url()); });
  await b.goto(BASE + '/chats'); await b.waitForTimeout(2000);
  await b.click('.ct-item-b'); await b.waitForSelector('#ct-chat:not([hidden])');
  for (let i = 0; i < 80 && (await b.$$('.ct-img')).length < 2; i++) await b.waitForTimeout(100);
  const box0 = await b.evaluate(() => [...document.querySelectorAll('.ct-img')].map(f => ({ w: f.style.width, ar: f.style.aspectRatio })));
  ok('2.1 Chat-Bilder: Platzhalter mit Breite/Seitenverhaeltnis vor dem Laden', box0.length === 2 && box0[0].w === '280px' && /280 \/ 210/.test(box0[0].ar), box0);
  const loadedAll = () => b.evaluate(() => [...document.querySelectorAll('.ct-img img')].filter(i => i.naturalWidth > 0).map(i => i.naturalWidth));
  for (let i = 0; i < 60 && (await loadedAll()).length < 2; i++) await b.waitForTimeout(250);
  const nw = await loadedAll();
  ok('2.1 Chat-Bilder: Verlauf zeigt Vorschaubilder (≤ 320 px), auch fuer die Nachricht ohne th', nw.length === 2 && nw.every(w => w <= 320), nw);
  ok('2.1 Chat-Bilder: Neue Nachricht – nur das Vorschaubild geladen (2 Abrufe: th + Original der alten)', got.length === 2, got.length);
  got.length = 0;
  await b.locator('.ct-img').first().click(); await b.waitForSelector('.ct-view img');
  for (let i = 0; i < 40 && !(await b.evaluate(() => { const i = document.querySelector('.ct-view img'); return i && i.naturalWidth === 1600; })); i++) await b.waitForTimeout(250);
  ok('2.1 Chat-Bilder: Antippen oeffnet das Original (1600 px)', await b.evaluate(() => document.querySelector('.ct-view img').naturalWidth === 1600) && got.length === 1, got.length);
  await b.keyboard.press('Escape');
  got.length = 0;
  await b.reload(); await b.waitForTimeout(2000); await b.click('.ct-item-b'); await b.waitForSelector('#ct-chat:not([hidden])');
  for (let i = 0; i < 40 && (await loadedAll()).length < 2; i++) await b.waitForTimeout(250);
  ok('2.1 Chat-Bilder: nach Neuladen aus dem Cache (IndexedDB, nur Ciphertext) – kein Abruf', (await loadedAll()).length === 2 && got.length === 0, got);
  const stored = await b.evaluate(() => new Promise(res => {
    const r = indexedDB.open('saga-chat-blobs-' + document.body.dataset.uid);
    r.onsuccess = () => { const q = r.result.transaction('data').objectStore('data').getAllKeys(); q.onsuccess = () => { r.result.close(); res(q.result); }; };
    r.onerror = () => res(null);
  }));
  ok('2.1 Chat-Bilder: Cache enthaelt th-Blob und eigenes Vorschaubild (lt:) der alten Nachricht', Array.isArray(stored) && stored.some(k => /^lt:/.test(k)) && stored.length >= 2, stored);
  await b.keyboard.press('Escape'); await b.waitForTimeout(300);
  await b.click('#hk-user', { force: true }); await b.click('.menu-item:has-text("Abmelden")'); await b.waitForURL(/\/login/);
  const left = await b.evaluate(() => indexedDB.databases ? indexedDB.databases().then(l => l.map(d => d.name)) : []);
  ok('2.1 Chat-Bilder: Abmelden leert den Bild-Cache', !left.some(n => /^saga-chat-blobs-/.test(n)), left);
  await chatDevicesGone(a); await a.context().close(); await b.context().close();
}

// 2.1 Bildbloecke kopieren/ausschneiden/einfuegen (Strg/⌘+C/X/V): Klick markiert das Bild, Zwischenablage mit image/png
// + HTML (data-saga-block) + Editor.js-Block; Einfuegen in anderer Seite/anderem Tab mit gleicher Datei, auf einer
// fremden Seite (SAGA_USER2 gibt Bearbeiten frei) als Kopie fuer deren Eigentuemer.
async function imageClipboard(browser) {
  const E = process.env, mod = process.platform === 'darwin' ? 'Meta' : 'Control';
  const p = await session(browser);
  await p.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: BASE });
  const s = await p.evaluate(async () => {
    const H = { 'X-Saga': '1', 'Content-Type': 'application/json' };
    const cv = document.createElement('canvas'); cv.width = 90; cv.height = 60; const g = cv.getContext('2d'); g.fillStyle = '#2a6'; g.fillRect(0, 0, 90, 60);
    const b = await new Promise(res => cv.toBlob(res, 'image/jpeg', 0.9));
    const mk = async t => (await (await fetch('/api/notes', { method: 'POST', headers: H, body: JSON.stringify({ title: t }) })).json()).id;
    const a = await mk('Bildquelle'), z = await mk('Bildziel');
    const fd = new FormData(); fd.append('image', b, 'gruen.jpg');
    const up = await (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
    const blocks = [{ type: 'paragraph', data: { text: 'Oben' } }, { type: 'image', data: { file: { url: up.file.url }, caption: 'Grün', withBorder: true, withBackground: false, stretched: false } },
      { type: 'paragraph', data: { text: 'Unten' } }];
    await fetch('/api/notes/' + a, { method: 'PATCH', headers: H, body: JSON.stringify({ body: JSON.stringify({ blocks }) }) });
    await fetch('/api/notes/' + z, { method: 'PATCH', headers: H, body: JSON.stringify({ body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'Ziel' } }, { type: 'paragraph', data: { text: '' } }] }) }) });
    return { a, z, url: up.file.url };
  });
  const blocks = async id => JSON.parse((await p.api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  await p.open(s.a);
  await p.click('#pg-editor .image-tool__image-picture'); await p.waitForTimeout(300);
  ok('2.1 Bild kopieren: Klick aufs Bild markiert den Block', await p.evaluate(() => document.querySelectorAll('#pg-editor .pg-picked').length === 1));
  await p.keyboard.press(mod + '+c'); await p.waitForTimeout(1200);
  const clip = await p.evaluate(async () => {
    try { const it = await navigator.clipboard.read(); const t = it[0].types; const h = t.includes('text/html') ? await (await it[0].getType('text/html')).text() : '';
      return { types: t, saga: h.includes('data-saga-block') }; } catch (e) { return { err: String(e) }; }
  });
  ok('2.1 Bild kopieren: Zwischenablage hat image/png (andere Programme) + HTML mit Saga-Block', clip.types && clip.types.includes('image/png') && clip.saga, clip);
  const p2 = await p.context().newPage();
  await p2.goto(BASE + '/notes#' + s.z); await p2.waitForSelector('#pg-editor .ce-paragraph'); await p2.waitForTimeout(900);
  await p2.locator('#pg-editor .ce-paragraph').nth(1).click(); await p2.keyboard.press(mod + '+v'); await p2.waitForTimeout(2500);
  const zb = await blocks(s.z);
  ok('2.1 Bild einfuegen: andere Seite im anderen Tab – gleiche Datei, Unterschrift/Rahmen bleiben, leerer Absatz ersetzt',
    zb.length === 2 && zb[1].type === 'image' && zb[1].data.file.url === s.url && zb[1].data.caption === 'Grün' && zb[1].data.withBorder, zb.map(b => b.type));
  await p2.close(); await p.bringToFront();
  await p.click('#pg-editor .image-tool__image-picture'); await p.waitForTimeout(300);
  await p.keyboard.press(mod + '+x'); await p.waitForTimeout(2500);
  let ab = await blocks(s.a);
  ok('2.1 Bild ausschneiden: Block aus der Seite entfernt (gespeichert)', ab.length === 2 && !ab.some(b => b.type === 'image'), ab.map(b => b.type));
  await p.locator('#pg-editor .ce-paragraph').first().click(); await p.keyboard.press('End');
  await p.keyboard.press(mod + '+v'); await p.waitForTimeout(2500);
  ab = await blocks(s.a);
  ok('2.1 Bild einfuegen: in derselben Seite wieder an der Schreibmarke', ab.length === 3 && ab[1].type === 'image' && ab[1].data.file.url === s.url, ab.map(b => b.type));
  if (E.SAGA_USER2) {
    const q = await session(browser, { user: E.SAGA_USER2, pass: E.SAGA_PASS2 });
    const f = (await q.api('POST', '/api/notes', { title: 'Fremde Seite' })).id;
    await q.api('PATCH', '/api/notes/' + f, { body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'fremd' } }] }) });
    await q.api('PUT', '/api/notes/' + f + '/shares', { type: 'user', target: USER, role: 'edit' });
    await p.click('#pg-editor .image-tool__image-picture'); await p.waitForTimeout(300);
    await p.keyboard.press(mod + '+c'); await p.waitForTimeout(800);
    await p.open(f);
    await p.locator('#pg-editor .ce-paragraph').first().click(); await p.keyboard.press('End');
    await p.keyboard.press(mod + '+v'); await p.waitForTimeout(2500);
    const fb = JSON.parse((await q.api('GET', '/api/notes/' + f)).body || '{}').blocks || [], img = fb.find(b => b.type === 'image');
    const st = img ? await q.evaluate(async u => (await fetch(u)).status, img.data.file.url) : 0;
    ok('2.1 Bild einfuegen: fremde Seite – Kopie fuer deren Eigentuemer (eigene Datei, sichtbar)', img && img.data.file.url !== s.url && st === 200, { fb, st });
    await q.api('DELETE', '/api/notes/' + f); await q.context().close();
  }
  for (const id of [s.a, s.z]) await p.api('DELETE', '/api/notes/' + id);
  await p.context().close();
}

// Review v21: Bloecke aus der Zwischenablage (application/x-editor-js bzw. data-saga-block) gehen an ed.blocks.insert, das
// nichts bereinigt -> vorher nur bekannte Werkzeuge des Ziel-Editors und Inline-HTML nach der Positivliste des Servers.
async function pasteSanitize(browser) {
  const p = await session(browser);
  const s = await p.evaluate(async () => {
    const H = { 'X-Saga': '1', 'Content-Type': 'application/json' };
    const cv = document.createElement('canvas'); cv.width = 40; cv.height = 30; cv.getContext('2d').fillRect(0, 0, 40, 30);
    const b = await new Promise(res => cv.toBlob(res, 'image/png'));
    const fd = new FormData(); fd.append('image', b, 'x.png');
    const up = await (await fetch('/api/files', { method: 'POST', headers: { 'X-Saga': '1' }, body: fd })).json();
    const z = (await (await fetch('/api/notes', { method: 'POST', headers: H, body: JSON.stringify({ title: 'Einfuegen bereinigt' }) })).json()).id;
    await fetch('/api/notes/' + z, { method: 'PATCH', headers: H, body: JSON.stringify({ body: JSON.stringify({ blocks: [{ type: 'paragraph', data: { text: 'Ziel' } }, { type: 'paragraph', data: { text: '' } }] }) }) });
    return { z, url: up.file.url };
  });
  await p.open(s.z);
  const unit = await p.evaluate(() => window.HKPages.k.cleanClip([
    { tool: 'paragraph', data: { text: 'a<img src=x onerror="window.__xss=9">b <a href="javascript:alert(1)">j</a> <a href="https://example.org/">h</a>' } },
    { tool: 'unbekannt', data: { text: 'x' } }, { tool: 'marker', data: {} },
    { tool: 'embed', data: { url: 'javascript:alert(1)' } }]));
  ok('Einfuegen: Bereinigung (unbekannte/Inline-Werkzeuge weg, onerror/javascript: weg, https-Verweis bleibt)',
    unit.length === 2 && unit[0].data.text === 'ab j <a href="https://example.org/" target="_blank" rel="noopener noreferrer">h</a>'
    && unit[1].tool === 'embed' && unit[1].data.url === '', unit);
  await p.locator('#pg-editor .ce-paragraph').nth(1).click();
  await p.evaluate(({ url }) => {
    window.__xss = 0;
    const blocks = [
      { tool: 'image', data: { file: { url }, caption: 'Bild <img src=x onerror="window.__xss=1"><b>fett</b>', withBorder: false, withBackground: false, stretched: false } },
      { tool: 'paragraph', data: { text: 'Text <a href="javascript:window.__xss=2">klick</a><script>window.__xss=3</script> <span onclick="x">s</span>' } },
      { tool: 'gibtsnicht', data: { text: 'boese' } },
      { tool: 'toggle', data: { title: 'T<img src=x onerror="window.__xss=4">', open: true, blocks: [
        { type: 'paragraph', data: { text: 'innen <iframe src="about:blank"></iframe>ok' } },
        { type: 'columns', data: { cols: [{ blocks: [{ type: 'paragraph', data: { text: 'aus <img src=x onerror="window.__xss=5">Spalte' } }] }] } }] } }];
    const dt = new DataTransfer(); dt.setData('application/x-editor-js', JSON.stringify(blocks));
    const t = document.querySelectorAll('#pg-editor .ce-paragraph')[1];
    t.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, s);
  await p.waitForTimeout(3000);
  const dom = await p.evaluate(() => ({ xss: window.__xss, bad: document.querySelectorAll('#pg-editor [onerror], #pg-editor script, #pg-editor iframe, #pg-editor [onclick], #pg-editor a[href^="javascript"]').length }));
  ok('Einfuegen: kein Skript laeuft, nichts Gefaehrliches im Editor (auch im Ausklappbaren)', dom.xss === 0 && dom.bad === 0, dom);
  const bl = JSON.parse((await p.api('GET', '/api/notes/' + s.z)).body || '{}').blocks || [];
  const tg = bl.find(b => b.type === 'toggle') || { data: {} };
  ok('Einfuegen: bekannte Bloecke eingefuegt, unbekannte weg, Spalten im Ausklappbaren aufgeloest',
    JSON.stringify(bl.map(b => b.type)) === JSON.stringify(['paragraph', 'image', 'paragraph', 'toggle'])
    && bl[1].data.caption === 'Bild <b>fett</b>' && bl[2].data.text.indexOf('klick') >= 0
    && JSON.stringify((tg.data.blocks || []).map(b => b.type + ':' + b.data.text)) === JSON.stringify(['paragraph:innen ok', 'paragraph:aus Spalte']), bl);
  await p.api('DELETE', '/api/notes/' + s.z);
  await p.context().close();
}

(async () => {
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true });
  try {
    if (process.env.SAGA_ONLY === 'passkeys') { await passkeys(browser); throw null; }     // nur die Passkey-Pruefungen
    if (process.env.SAGA_ONLY === 'avatars') { await avatars(browser); await presence(browser); throw null; }   // nur die Profilbild-Pruefungen
    if (process.env.SAGA_ONLY === 'chatimg') { await chatThumbs(browser); throw null; }                         // nur Chat-Bilder
    if (process.env.SAGA_ONLY === 'imgclip') { await imageClipboard(browser); throw null; }                     // nur Bild kopieren/einfuegen
    if (process.env.SAGA_ONLY === 'paste') { await pasteSanitize(browser); throw null; }                        // nur Einfuegen bereinigt
    const p = await session(browser);
    await enterFallback(p);
    await reloadKeeps(p);
    await numbers(p, 'de');
    await conflictVisible(p);
    await typeAheadTitle(p);
    await trashLink(p);
    await dbLive(browser, p);
    await peekMove(browser, p);
    await commentSnippet(p);
    await p.context().close();
    await chatImmich(browser);
    await chatThumbs(browser);
    await imageClipboard(browser);
    await pasteSanitize(browser);
    await narrow(browser);
    await passkeys(browser);
    await avatars(browser);
    await presence(browser);
  } catch (e) { if (e !== null) { console.error(e); res.push(false); } }
  await browser.close();
  console.log(`${res.filter(Boolean).length}/${res.length} ok`);
  process.exit(res.every(Boolean) ? 0 : 1);
})();
