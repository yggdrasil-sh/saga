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

(async () => {
  const browser = await chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true });
  try {
    if (process.env.SAGA_ONLY === 'passkeys') { await passkeys(browser); throw null; }     // nur die Passkey-Pruefungen
    if (process.env.SAGA_ONLY === 'avatars') { await avatars(browser); throw null; }       // nur die Profilbild-Pruefungen
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
    await narrow(browser);
    await passkeys(browser);
    await avatars(browser);
  } catch (e) { if (e !== null) { console.error(e); res.push(false); } }
  await browser.close();
  console.log(`${res.filter(Boolean).length}/${res.length} ok`);
  process.exit(res.every(Boolean) ? 0 : 1);
})();
