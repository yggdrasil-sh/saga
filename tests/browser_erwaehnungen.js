// Saga 2.5 P6-5/P6-6 im Web: @-Menue mit Personen (mit Zugriff, "teilen und erwaehnen"), Chip mit aktuellem Namen,
// Erwaehnung in Kommentaren, synchronisierte Bloecke (Block-Menue "Als synchronisierten Block kopieren", Einfuegen auf einer
// anderen Seite, Rahmen, "Original oeffnen", Anzahl Kopien, Bearbeiten in der Kopie schreibt ins Original, nur lesend fuer Lesende).
// Braucht zwei Konten: CK (Anna) und CK2 (Bert, Anzeigename beliebig) als signierte saga-Cookies, NAME2 = Anzeigename von Bert,
// HANDLE2 = Handle von Bert (~...). Lokal: HK_TEST_BASE=http://127.0.0.1:8360 CK=… CK2=… NAME2=… HANDLE2=… node tests/browser_erwaehnungen.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, CK2 = process.env.CK2, NAME2 = process.env.NAME2, HANDLE2 = process.env.HANDLE2;
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms = 10000) { const t = Date.now(); let v; while (Date.now() - t < ms) { v = await fn(); if (v) return v; await sleep(150); } return v; }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const mkCtx = async (ck) => {
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE', permissions: ['clipboard-read', 'clipboard-write'] });
    await ctx.addCookies([{ name: 'saga', value: ck, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage();
    p.on('pageerror', (e) => errs.push(e.message));
    p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused|TypeError|ReferenceError/.test(m.text())) errs.push(m.text().slice(0, 200)); });
    return p;
  };
  const errs = [];
  const p = await mkCtx(CK), q = await mkCtx(CK2);
  await p.goto(BASE + '/notes'); await q.goto(BASE + '/notes');
  const apiOf = (pg) => (m, url, b) => pg.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const api = apiOf(p), api2 = apiOf(q);
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
  const mk = async (title, blocks) => {
    const id = (await api('POST', '/api/notes', { title })).id;
    await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks }), format: 'blocks' });
    return id;
  };
  const blocksOf = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body).blocks;
  const open = async (pg, id, sel) => { await pg.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await pg.waitForSelector(sel || '.ce-block'); await sleep(1500); };
  const saved = async (id, fn) => until(async () => fn(await blocksOf(id)), 12000);
  const made = [];
  const typeAt = async (pg, bid, text) => {
    await pg.click(`.ce-block[data-id="${bid}"] .ce-paragraph`);
    await pg.evaluate((bid) => { const e = document.querySelector(`.ce-block[data-id="${bid}"] .ce-paragraph`), r = document.createRange();
      r.selectNodeContents(e); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }, bid);
    await pg.keyboard.type(text, { delay: 30 });
  };

  // --- 1. @-Menue: Person ohne Zugriff -> teilen und erwaehnen, dann Chip -----------------------------------------------
  const n = await mk('Erwähnungen', [P('m1', 'Hallo ')]); made.push(n);
  await open(p, n, '.ce-block[data-id="m1"]');
  const q2 = NAME2.slice(0, 4);
  await typeAt(p, 'm1', ' @' + q2);
  const grp = await until(() => p.evaluate(() => { const b = document.querySelector('.hk-at:not([hidden])'); return b && [...b.querySelectorAll('.hk-at-g')].map((x) => x.textContent); }), 8000);
  ok('@-Menue: Gruppe Personen vor den anderen', grp && grp[0] === 'Personen', grp);
  const share = await p.evaluate((nm) => [...document.querySelectorAll('.hk-at .hk-emo-i')].findIndex((x) => x.textContent.includes(nm) && /teilen und erwähnen/.test(x.textContent)), NAME2);
  ok('Bert ohne Zugriff: „kein Zugriff – teilen und erwähnen“', share >= 0, share);
  if (share >= 0) {
    await p.evaluate((i) => document.querySelectorAll('.hk-at .hk-emo-i')[i].dispatchEvent(new MouseEvent('mousedown', { bubbles: true })), share);
    await p.waitForSelector('.hk-share-mention', { timeout: 5000 }).catch(() => null);
    ok('Rueckfrage „Seite teilen und erwähnen?“', await p.$('.hk-share-mention'));
    await p.evaluate(() => [...document.querySelectorAll('.menu .menu-item, .menu button')].find((x) => /Teilen und erwähnen/.test(x.textContent)).click());
  }
  let bl = await saved(n, (b) => /hk-mention/.test(b[0].data.text) && b);
  ok(`Erwaehnung gespeichert mit Handle (${bl && bl[0].data.text})`, bl && bl[0].data.text.includes(`data-u="${HANDLE2}"`));
  const sh = await api('GET', '/api/notes/' + n + '/shares');
  ok('Seite mit Bert geteilt', JSON.stringify(sh).includes(NAME2), sh);
  // Chip zeigt den aktuellen Namen (people), auch wenn der gespeicherte Text anders lautet
  await api('PATCH', '/api/notes/' + n, { body: JSON.stringify({ blocks: [P('m1', `Hallo <span class="hk-mention" data-u="${HANDLE2}">Alter Name</span> da`)] }), format: 'blocks' });
  await open(p, n, '.hk-mention');
  const chip = await p.evaluate(() => { const s = document.querySelector('.hk-mention'); return s && { name: s.dataset.name, ce: s.contentEditable, vis: getComputedStyle(s, '::before').content }; });
  ok(`Chip mit aktuellem Namen (${chip && chip.vis})`, chip && chip.name === '@' + NAME2 && chip.vis.includes(NAME2) && chip.ce === 'false', chip);
  // Person mit Zugriff erscheint direkt (ohne Rueckfrage)
  await typeAt(p, 'm1', ' @' + q2);
  const direct = await until(() => p.evaluate((nm) => [...document.querySelectorAll('.hk-at:not([hidden]) .hk-emo-i')].some((x) => x.textContent.includes(nm) && !/teilen/.test(x.textContent)), NAME2), 8000);
  ok('Bert jetzt mit Zugriff: ohne „teilen“', direct);
  await p.keyboard.press('Escape');

  // --- 2. Kommentar mit Erwaehnung ----------------------------------------------------------------------------------
  const cm = await api('POST', '/api/notes/' + n + '/comments', { block_id: 'm1', body: `Frage an <@${HANDLE2}> bitte` });
  ok('Kommentar mit Token angelegt', cm && cm.comment, cm);
  await open(p, n, '.ce-block');
  await p.evaluate(() => window.HKComments && HKComments.openBlock('m1'));
  const cchip = await until(() => p.evaluate(() => { const s = document.querySelector('.cm-text .hk-mention'); return s && s.textContent; }), 6000);
  ok(`Kommentar zeigt Chip (${cchip})`, cchip === '@' + NAME2, cchip);

  // --- 3. Synchronisierter Block --------------------------------------------------------------------------------------
  const a = await mk('Sync Original', [P('s0', 'Oben'), P('s1', 'Geteilter Inhalt')]); made.push(a);
  const b = await mk('Sync Ziel', [P('t0', 'Ziel')]); made.push(b);
  await open(p, a, '.ce-block[data-id="s1"]');
  await p.hover('.ce-block[data-id="s1"]'); await p.click('.ce-block[data-id="s1"] .ce-paragraph');
  await p.click('.ce-toolbar__settings-btn');
  const item = await until(() => p.evaluate(() => [...document.querySelectorAll('.ce-settings .ce-popover-item, .ce-popover--opened .ce-popover-item')].some((x) => /Als synchronisierten Block kopieren/.test(x.textContent))), 5000);
  ok('Block-Menue: „Als synchronisierten Block kopieren“', item);
  if (item) await p.evaluate(() => [...document.querySelectorAll('.ce-popover-item')].find((x) => /Als synchronisierten Block kopieren/.test(x.textContent)).click());
  bl = await saved(a, (bb) => bb.find((x) => x.type === 'synced') && bb);
  const orig = bl && bl.find((x) => x.type === 'synced');
  ok('Original gespeichert (sid + Inhalt)', orig && orig.data.sid && orig.data.sid.length === 16 && orig.data.blocks[0].data.text === 'Geteilter Inhalt', orig);
  ok('Rahmen „Original“ im Editor', await p.$('.hk-sync.orig'));
  const sid = orig && orig.data.sid;
  // Kopie einfuegen (wie Einfuegen aus der Zwischenablage: Editor.js-JSON)
  await open(p, b, '.ce-block[data-id="t0"]');
  await p.click('.ce-block[data-id="t0"] .ce-paragraph');
  await p.evaluate((sid) => {
    const dt = new DataTransfer(); dt.setData('application/x-editor-js', JSON.stringify([{ tool: 'synced', data: { sid } }])); dt.setData('text/plain', 'x');
    document.querySelector('.ce-block[data-id="t0"] .ce-paragraph').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, sid);
  bl = await saved(b, (bb) => bb.find((x) => x.type === 'synced') && bb);
  const kopie = bl && bl.find((x) => x.type === 'synced');
  ok('Kopie gespeichert und gelesen mit Inhalt', kopie && kopie.data.sid === sid && kopie.data.src && kopie.data.src.note === a, kopie);
  await open(p, b, '.hk-sync.copy');
  const bar = await p.evaluate(() => { const w = document.querySelector('.hk-sync.copy'); return w && { lab: w.querySelector('.hk-sync-lab').textContent, go: !!w.querySelector('.hk-sync-go'), txt: w.querySelector('.hk-sync-body').textContent, border: getComputedStyle(w).borderTopWidth }; });
  ok(`Kopie: Rahmen, „Synchronisiert“, Inhalt, „Original öffnen“ (${bar && bar.lab})`, bar && bar.lab === 'Synchronisiert' && bar.go && bar.txt.includes('Geteilter Inhalt') && bar.border !== '0px', bar);
  // In der Kopie bearbeiten -> Original
  await p.click('.hk-sync-body .ce-paragraph');
  await p.evaluate(() => { const e = document.querySelector('.hk-sync-body .ce-paragraph'), r = document.createRange(); r.selectNodeContents(e); r.collapse(false); const s = getSelection(); s.removeAllRanges(); s.addRange(r); });
  await p.keyboard.type(' plus Kopie', { delay: 25 });
  const ob = await saved(a, (bb) => { const o = bb.find((x) => x.type === 'synced'); return o && /plus Kopie/.test(o.data.blocks[0].data.text) && o; });
  ok('Bearbeiten in der Kopie schreibt ins Original', ob, ob);
  // Anzahl Kopien am Original
  await open(p, a, '.hk-sync.orig');
  const nref = await p.evaluate(() => { const x = document.querySelector('.hk-sync.orig .hk-sync-n'); return x && x.textContent; });
  ok(`Original: „auf 1 weiteren Seiten“ (${nref})`, nref && nref.includes('1'), nref);
  // Original oeffnen
  await open(p, b, '.hk-sync-go');
  await p.click('.hk-sync-go');
  const jumped = await until(() => p.evaluate((a) => location.hash.startsWith('#' + a), a), 6000);
  ok('„Original öffnen“ springt zur Seite des Originals', jumped);

  // --- 4. Bert: Lesen auf der Kopie-Seite und dem Original -> nur lesend; ohne Zugriff auf A -> Platzhalter --------------
  await api('PUT', '/api/notes/' + b + '/shares', { type: 'user', target: HANDLE2, role: 'edit' });
  await open(q, b, '.hk-sync');
  const noacc = await q.evaluate(() => { const w = document.querySelector('.hk-sync'); return w && { lab: w.querySelector('.hk-sync-lab').textContent, txt: w.textContent }; });
  ok(`Bert ohne Zugriff auf das Original: Platzhalter (${noacc && noacc.lab})`, noacc && /kein Zugriff/.test(noacc.lab) && !noacc.txt.includes('Geteilter Inhalt') && !noacc.txt.includes('Sync Original'), noacc);
  await api('PUT', '/api/notes/' + a + '/shares', { type: 'user', target: HANDLE2, role: 'view' });
  await open(q, b, '.hk-sync-body .ce-paragraph');
  const ro = await q.evaluate(() => { const w = document.querySelector('.hk-sync'); return w && { lab: w.querySelector('.hk-sync-lab').textContent, ro: w.classList.contains('locked') }; });
  ok(`Bert nur Lesen auf dem Original: nur lesend (${ro && ro.lab})`, ro && ro.ro && /nur lesend/.test(ro.lab), ro);
  await q.click('.hk-sync-body .ce-paragraph');
  await q.keyboard.type('XYZ', { delay: 25 });
  await sleep(2500);
  const ob2 = (await blocksOf(a)).find((x) => x.type === 'synced');
  ok('Bert kann den Inhalt nicht aendern', ob2 && !/XYZ/.test(JSON.stringify(ob2)), ob2);
  const bb = await api2('GET', '/api/notes/' + b);
  ok('Bert liest die Seite ohne Fehler', bb && bb.id === b, bb);

  ok('keine Skriptfehler/CSP-Verstoesse', !errs.length, errs);
  for (const id of made) await api('DELETE', '/api/notes/' + id);
  await br.close();
  console.log(`\nBROWSER-ERWAEHNUNGEN ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
