// Browser-Reihe fuer das Adressbuch im Web (2.6, Bugs-DB 2027a-4, Feature contacts2): static/contacts.js (Einstellungen >
// Arbeitsbereich > Kontakte), static/settings.js (Navigation, ?settings=contacts), static/chat.js ("Kontakt bearbeiten …"
// im Menue eines Kontakts, #h-Einstieg), static/live.js (contacts.changed). Zwei Konten mit Anzeigenamen, ohne
// gemeinsamen Chat vor dem Lauf (der Kontakt entsteht im Test), Chromium oder Firefox:
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [BR=firefox] [SHOTS=/ordner]
//   [NUR_BILDER=1] [PLAYWRIGHT=/pfad/zu/playwright-core] node tests/browser_kontakte2.js
// Geprueft: Navigation (Eintrag Kontakte unter Arbeitsbereich, ?settings=contacts), Liste (Kontakt aus dem Chat, Suche nach
// Name und Ziffern), Detail (feste Saga-Zeile mit "Nachricht", tel:/mailto:), Bearbeiten (Name fuer mich, Anrede,
// Grussform, Nummer/Mail mit Typ), nationale Nummer ohne/mit Landesvorwahl, 409 bei veraltetem Stand (Eingabe bleibt,
// zweites Speichern gewinnt), doppelter Wert (409 mit "Zum Kontakt"), Neuer Kontakt, Loeschen mit Rueckfrage, Live
// (zweiter Tab: Liste und offenes Detail), "Nachricht" oeffnet den Direkt-Chat, "Kontakt bearbeiten …" im Chat-Dialog,
// nur textContent (Name mit HTML), keine Anmeldenamen anderer, keine waagerechte Rolle am Handy, keine Seitenfehler.
// Bilder (SHOTS): schmal 390x844 und breit 1440x900, hell und dunkel - Navigation, Liste, Detail, Bearbeiten, Chat-Menue.
// NUR_BILDER=1: nur Bilder, ohne Pruefungen - laeuft auch gegen einen Stand ohne diese Web-Teile (Vorher-Bilder: Navigation
// ohne Kontakte, Chat-Menue ohne "Kontakt bearbeiten …"). Kein waitForFunction (CSP ohne eval).
'use strict';
const pw = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const E = process.env, BASE = E.SAGA_BASE, FF = E.BR === 'firefox', NUR = E.NUR_BILDER === '1';
if (!BASE || !E.SAGA_USER || !E.SAGA_PASS || !E.SAGA_USER2 || !E.SAGA_PASS2) { console.error('SAGA_BASE, SAGA_USER/PASS, SAGA_USER2/PASS2 setzen'); process.exit(2); }
const SHOTS = E.SHOTS || '', U1 = E.SAGA_USER, U2 = E.SAGA_USER2, BRN = FF ? 'firefox' : 'chromium';
const res = [], errs = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' [' + BRN + '] ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
function note(s) { console.log('--   ' + s); }
async function shot(p, name) {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p.waitForTimeout(300);
  await p.screenshot({ path: path.join(SHOTS, name + '.png') }).catch(e => note('Bild ' + name + ': ' + e.message));
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 10000, step = 120) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: opts.scheme || 'light',
                                         timezoneId: 'Europe/Berlin' });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  return p;
}
const papi = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin',
  headers: Object.assign({ 'X-Saga': '1', 'X-Saga-Chat': '3' }, b !== undefined ? { 'Content-Type': 'application/json' } : {}), body: b !== undefined ? JSON.stringify(b) : undefined })
  .then(async r => ({ status: r.status, d: await r.json().catch(() => ({})) })), [m, u, b === undefined ? undefined : b]);
const RKF = u => path.join(os.tmpdir(), 'saga-browser-rk-' + BASE.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  // jeder Kontext ist ein neues Chat-Geraet (hoechstens 10 je Konto): alte Geraete dieses Kontos vorher abmelden
  await q.goto(BASE + '/dashboard'); await q.waitForTimeout(500);
  const me = (await papi(q, 'GET', '/api/chat/me')).d || {};
  for (const d of (me.devices || []).filter(d => !d.revoked)) await papi(q, 'DELETE', '/api/chat/devices/' + d.id);
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
async function closeAll(p) { for (let i = 0; i < 3; i++) { await p.keyboard.press('Escape'); await p.waitForTimeout(120); } }
// Einstellungen ueber das Benutzermenue oeffnen (ohne Ziel: breit = Profil, schmal = Liste der Bereiche)
async function openSettingsMenu(p) {
  await p.click('#hk-user'); await p.waitForSelector('.menu:not([hidden]) .menu-item', { timeout: 5000 });
  await p.click('.menu:not([hidden]) .menu-item:has-text("Einstellungen")');
  await p.waitForSelector('.hk-dialog-set', { timeout: 5000 }); await p.waitForTimeout(400);
}
async function openContacts(p) {
  await p.evaluate(() => HKSettings.open('contacts'));
  await p.waitForSelector('#hk-pane-contacts:not([hidden]) .kb', { timeout: 5000 });
  await until(async () => (await p.$$('#hk-pane-contacts .kb-item')).length || /Noch keine|Keine Kontakte/.test(await p.textContent('#hk-pane-contacts .kb-count')), 6000);
}
const pane = p => p.evaluate(() => {
  const s = document.getElementById('hk-pane-contacts'); if (!s) return null;
  return { text: s.textContent, rows: [...s.querySelectorAll('.kb-item')].map(b => ({ id: +b.dataset.id, n: (b.querySelector('.kb-item-n') || {}).textContent, s: (b.querySelector('.kb-item-s') || {}).textContent || '' })),
           name: (s.querySelector('.kb-head .kb-name') || {}).textContent || null, form: !!s.querySelector('.kb-form'),
           tel: [...s.querySelectorAll('.kb-l-tel a')].map(a => [a.getAttribute('href'), a.textContent]),
           mail: [...s.querySelectorAll('.kb-l-mail a')].map(a => [a.getAttribute('href'), a.textContent]),
           saga: !!s.querySelector('.kb-l-saga'), msg: !!s.querySelector('.kb-l-saga .kb-msg'),
           facts: [...s.querySelectorAll('.kb-facts dd')].map(d => d.textContent), note: (s.querySelector('.kb-note.on') || {}).textContent || '',
           err: (s.querySelector('.kb-err') || {}).textContent || '', count: (s.querySelector('.kb-count') || {}).textContent || '',
           imgs: s.querySelectorAll('.kb-list img:not(.hk-av-img), .kb-head img:not(.hk-av-img)').length };
});
async function rowClick(p, name) {
  const rows = await p.$$('#hk-pane-contacts .kb-item');
  for (const r of rows) if (((await r.textContent()) || '').includes(name)) { await r.click(); return true; }
  return false;
}
const detailOpen = p => until(async () => { const x = await pane(p); return x && x.name ? x : null; }, 6000);
async function openNewChat(p) {
  await p.click('#ct-new'); await p.waitForSelector('.ct-ov .ct-people-q');
  await until(async () => (await p.$$('.ct-ov .ct-people-list li.ct-contact')).length, 6000);
}
async function contactMenu(p, name) {
  const rows = await p.$$('.ct-ov .ct-people-list li.ct-contact');
  for (const li of rows) if (((await li.textContent()) || '').includes(name)) {
    await (await li.$('.ct-contact-more')).click();
    await p.waitForSelector('.menu:not([hidden]) .menu-item', { timeout: 4000 });
    return p.evaluate(() => [...document.querySelectorAll('.menu:not([hidden]) .menu-item')].map(x => x.textContent.trim()));
  }
  return null;
}
async function hscroll(p) { return p.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]); }

(async () => {
  const browser = await (FF ? pw.firefox : pw.chromium).launch(FF ? {} : { channel: E.PW_CHANNEL || undefined, args: ['--no-sandbox'] });
  try {
    const A = await session(browser, U1, E.SAGA_PASS), B = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(A, U1); await chatKeys(B, U2);
    const nB = (await papi(B, 'GET', '/api/chat/me')).d.name, nA = (await papi(A, 'GET', '/api/chat/me')).d.name;
    const rB = (((await papi(A, 'GET', '/api/chat/people?q=' + encodeURIComponent(nB))).d.users || []).find(u => u.name === nB) || {}).username;
    if (!NUR) ok('Personensuche liefert B als Handle', /^~/.test(rB || ''), rB);
    let r = await papi(A, 'POST', '/api/chats', { kind: 'dm', user: rB });
    if (!NUR) ok('Direkt-Chat A-B (' + r.status + ')', r.status === 200);
    const feat = ((await papi(A, 'GET', '/api/version')).d.features || []).indexOf('contacts2') >= 0;
    const web = await A.evaluate(() => !!window.HKContacts);
    note('contacts2=' + feat + ', static/contacts.js=' + web);
    // Aufraeumen frueherer Laeufe: nur manuelle Testkontakte von A
    // und eigene Felder des Chat-Kontakts B leeren, Landesvorwahl entfernen
    if (feat) {
      for (const c of ((await papi(A, 'GET', '/api/contacts?limit=200')).d.contacts || [])) {
        if (c.origin === 'manual') await papi(A, 'DELETE', '/api/contacts/' + c.id);
        else await papi(A, 'PATCH', '/api/contacts/' + c.id, { ver: c.ver, own_name: null, form: null, salutation: null, addrs: [] });
      }
      await papi(A, 'PUT', '/api/contacts/settings', { cc: null });
    }

    if (!NUR && web) {
      // --- Navigation ----------------------------------------------------------------------------------------------------
      await A.goto(BASE + '/dashboard'); await A.waitForTimeout(1200);
      await openSettingsMenu(A);
      const nav = await A.evaluate(() => {
        const g = [...document.querySelectorAll('.hk-setnav-g')].map(x => ({ h: (x.querySelector('.hk-setnav-h') || {}).textContent, k: [...x.querySelectorAll('.hk-setnav-i')].map(b => b.dataset.k) }));
        return g;
      });
      const ws = (nav || []).find(g => g.h === 'Arbeitsbereich');
      ok('Navigation: Kontakte unter Arbeitsbereich', ws && ws.k.indexOf('contacts') >= 0, nav);
      await A.click('#hk-tab-contacts');
      await until(async () => (await A.$$('#hk-pane-contacts .kb-item')).length, 6000);
      const fk = await A.evaluate(() => document.activeElement && document.activeElement.id);
      ok('Klick auf Kontakte: Bereich sichtbar, Fokus auf der Ueberschrift', fk === 'hk-pane-h-contacts', fk);
      let x = await pane(A);
      ok('Liste: B aus dem gemeinsamen Chat', x && x.rows.some(w => w.n === nB), x && x.rows);
      ok('Keine Anmeldenamen im Bereich', x && !x.text.includes(U2) && !x.text.includes(U1));
      await closeAll(A);
      await A.goto(BASE + '/dashboard?settings=contacts'); await A.waitForTimeout(1500);
      ok('?settings=contacts oeffnet den Bereich', await until(async () => !!(await A.$('#hk-pane-contacts:not([hidden]) .kb-item')), 6000));
      await closeAll(A);

      // --- Detail, Bearbeiten -------------------------------------------------------------------------------------------
      await openContacts(A);
      await rowClick(A, nB);
      x = await detailOpen(A);
      ok('Detail B: Name, feste Saga-Zeile mit "Nachricht", noch keine Nummer', x && x.name === nB && x.saga && x.msg && !x.tel.length, x);
      await A.click('#hk-pane-contacts .kb-edit');
      await A.waitForSelector('#hk-pane-contacts .kb-form');
      ok('Formular: Hinweis zur festen Saga-Adresse, Platzhalter = Name der Person', await A.evaluate(() => !!document.querySelector('#hk-pane-contacts .kb-fixed') &&
         document.querySelector('#hk-pane-contacts .kb-own').placeholder), nB);
      await A.fill('#hk-pane-contacts .kb-own', 'Benni');
      await A.selectOption('#hk-pane-contacts .kb-form-sel', 'informal');
      await A.fill('#hk-pane-contacts .kb-sal', 'Lieber Ben');
      // nationale Nummer ohne Landesvorwahl -> Fehler (Eingabe bleibt)
      await A.fill('#hk-pane-contacts .kb-fs-tel .kb-addr-v', '0170 1234567');
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && y.err ? y : null; }, 5000);
      ok('Nationale Nummer ohne Landesvorwahl: Fehlermeldung ohne die Nummer, Formular bleibt', x && x.form && /Landesvorwahl/.test(x.err) && !x.err.includes('0170'), x && x.err);
      await A.fill('#hk-pane-contacts .kb-fs-tel .kb-addr-v', '+49 (0) 170 / 123 45-67');
      await A.click('#hk-pane-contacts .kb-add-mail');
      await A.fill('#hk-pane-contacts .kb-fs-mail .kb-addr-v', 'ben@Example.ORG');
      await A.selectOption('#hk-pane-contacts .kb-fs-mail .kb-addr-t', 'work');
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && !y.form && y.name ? y : null; }, 6000);
      ok('Gespeichert: Name fuer mich, tel:/mailto:, Anrede du, Grussform', x && x.name === 'Benni' && x.tel.length === 1 && x.tel[0][0] === 'tel:+491701234567'
         && x.tel[0][1] === '+49 1701234567' && x.mail.length === 1 && x.mail[0][0] === 'mailto:ben@example.org' && x.facts.indexOf('du') >= 0 && x.facts.indexOf('Lieber Ben') >= 0, x);
      const cB = ((await papi(A, 'GET', '/api/contacts?q=Benni')).d.contacts || [])[0] || {};
      ok('Server: own_name, form, Mail-Typ work', cB.own_name === 'Benni' && cB.form === 'informal' && (cB.addrs || []).some(a => a.kind === 'email' && a.type === 'work'), cB);
      const bSees = await papi(B, 'GET', '/api/contacts?limit=200');
      ok('B sieht nichts von A\'s Eintraegen', !JSON.stringify(bSees.d).includes('Benni') && !JSON.stringify(bSees.d).includes('1701234567'));

      // --- 409: anderswo geaendert -----------------------------------------------------------------------------------
      await A.click('#hk-pane-contacts .kb-edit'); await A.waitForSelector('#hk-pane-contacts .kb-form');
      r = await papi(A, 'PATCH', '/api/contacts/' + cB.id, { ver: cB.ver, salutation: 'Hallo Ben' });
      ok('Zwischendurch per API geaendert (' + r.status + ')', r.status === 200);
      await A.fill('#hk-pane-contacts .kb-sal', 'Servus Ben');
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && y.note ? y : null; }, 5000);
      const kept = await A.evaluate(() => document.querySelector('#hk-pane-contacts .kb-sal').value);
      ok('409: Hinweis, Formular bleibt, Eingabe steht noch', x && x.form && /anderswo/.test(x.note) && kept === 'Servus Ben', [x && x.note, kept]);
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && !y.form && y.name ? y : null; }, 6000);
      ok('Zweites Speichern gewinnt', x && x.facts.indexOf('Servus Ben') >= 0, x && x.facts);

      // --- Landesvorwahl, Neuer Kontakt, doppelter Wert --------------------------------------------------------------
      await A.click('#hk-pane-contacts .kb-back');
      await A.waitForSelector('#hk-pane-contacts .kb-cc-in');
      await A.fill('#hk-pane-contacts .kb-cc-in', '49'); await A.click('#hk-pane-contacts .kb-cc-save');
      ok('Landesvorwahl gespeichert', await until(async () => (await papi(A, 'GET', '/api/contacts/settings')).d.cc === '49', 4000));
      await A.click('#hk-pane-contacts .kb-new'); await A.waitForSelector('#hk-pane-contacts .kb-form');
      await A.fill('#hk-pane-contacts .kb-own', 'Carla Test');
      await A.fill('#hk-pane-contacts .kb-fs-tel .kb-addr-v', '0170 1234567');      // = Nummer von B
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && y.note ? y : null; }, 5000);
      ok('Doppelter Wert: Hinweis mit "Zum Kontakt", ohne die Nummer', x && x.form && !x.note.includes('1234567') && !!(await A.$('#hk-pane-contacts .kb-jump')), x && x.note);
      await A.fill('#hk-pane-contacts .kb-fs-tel .kb-addr-v', '0171 7654321');
      await A.selectOption('#hk-pane-contacts .kb-fs-tel .kb-addr-t', 'home');
      await A.click('#hk-pane-contacts .kb-save');
      x = await until(async () => { const y = await pane(A); return y && !y.form && y.name ? y : null; }, 6000);
      ok('Neuer Kontakt mit nationaler Nummer (Landesvorwahl 49)', x && x.name === 'Carla Test' && x.tel[0] && x.tel[0][0] === 'tel:+491717654321' && !x.saga, x);
      await A.click('#hk-pane-contacts .kb-back');
      await A.fill('#hk-pane-contacts .kb-q', '0171 765');
      x = await until(async () => { const y = await pane(A); return y && y.rows.length === 1 && y.rows[0].n === 'Carla Test' ? y : null; }, 5000);
      ok('Suche nach Ziffern der Nummer', !!x, await pane(A));
      await A.fill('#hk-pane-contacts .kb-q', 'benn');
      x = await until(async () => { const y = await pane(A); return y && y.rows.length === 1 && y.rows[0].n === 'Benni' ? y : null; }, 5000);
      ok('Suche nach Name fuer mich', !!x, await pane(A));
      await A.fill('#hk-pane-contacts .kb-q', '');
      await A.waitForTimeout(500);
      // "Zum Kontakt" springt
      await A.click('#hk-pane-contacts .kb-new'); await A.waitForSelector('#hk-pane-contacts .kb-form');
      await A.fill('#hk-pane-contacts .kb-own', 'Doppelt');
      await A.fill('#hk-pane-contacts .kb-fs-tel .kb-addr-v', '+49 171 7654321');
      await A.click('#hk-pane-contacts .kb-save');
      await A.waitForSelector('#hk-pane-contacts .kb-jump', { timeout: 5000 }).catch(() => {});
      await A.click('#hk-pane-contacts .kb-jump').catch(() => {});
      x = await detailOpen(A);
      ok('"Zum Kontakt" oeffnet den vorhandenen Kontakt', x && x.name === 'Carla Test', x && x.name);

      // --- nur textContent ------------------------------------------------------------------------------------------
      r = await papi(A, 'POST', '/api/contacts', { own_name: '<img src=x onerror=alert(1)> Xaver', addrs: [{ kind: 'email', value: 'xaver@example.org' }] });
      ok('Kontakt mit HTML im Namen angelegt (' + r.status + ')', r.status === 201);
      const xid = r.d.id;
      await A.click('#hk-pane-contacts .kb-back');
      x = await until(async () => { const y = await pane(A); return y && y.rows.some(w => /Xaver/.test(w.n)) ? y : null; }, 5000);
      ok('Name mit HTML erscheint als Text (kein <img>)', x && x.imgs === 0 && x.rows.some(w => w.n === '<img src=x onerror=alert(1)> Xaver'), x && x.rows);

      // --- Live: zweiter Tab -----------------------------------------------------------------------------------------
      const A2 = await A.context().newPage();
      A2.on('pageerror', e => { errs.push('A2: ' + e.message); });
      await A2.goto(BASE + '/dashboard'); await A2.waitForTimeout(1500);
      await openContacts(A2);
      ok('Zweiter Tab: Live-Kanal steht', await until(() => A2.evaluate(() => !!(window.HKLive && HKLive.state().sse)), 10000));
      r = await papi(A, 'POST', '/api/contacts', { own_name: 'Live Lotte', addrs: [{ kind: 'email', value: 'lotte@example.org' }] });
      ok('Live: neuer Kontakt erscheint im zweiten Tab (<= 3 s)', await until(async () => ((await pane(A2)) || { rows: [] }).rows.some(w => w.n === 'Live Lotte'), 3000));
      const lid = r.d.id;
      await rowClick(A2, 'Live Lotte'); await detailOpen(A2);
      await papi(A, 'DELETE', '/api/contacts/' + lid);
      ok('Live: offenes Detail -> nach dem Loeschen zurueck zur Liste', await until(async () => { const y = await pane(A2); return y && !y.name && y.rows.length && !y.rows.some(w => w.n === 'Live Lotte'); }, 4000));
      await A2.close();

      // --- Loeschen mit Rueckfrage -----------------------------------------------------------------------------------
      await rowClick(A, 'Xaver'); await detailOpen(A);
      await A.click('#hk-pane-contacts .kb-del');
      await A.waitForSelector('.hk-ask .btn-destr', { timeout: 4000 });
      const askT = await A.textContent('.hk-ask');
      await A.click('.hk-ask .btn:not(.btn-destr)');
      ok('Rueckfrage abbrechen: Kontakt bleibt', (await papi(A, 'GET', '/api/contacts/' + xid)).status === 200 && /löschen/.test(askT), askT);
      await A.click('#hk-pane-contacts .kb-del');
      await A.waitForSelector('.hk-ask .btn-destr'); await A.click('.hk-ask .btn-destr');
      ok('Loeschen: zurueck in der Liste, Server 404', await until(async () => { const y = await pane(A); return y && !y.name && !y.rows.some(w => /Xaver/.test(w.n)); }, 5000)
         && (await papi(A, 'GET', '/api/contacts/' + xid)).status === 404);

      // --- "Nachricht" ----------------------------------------------------------------------------------------------
      await rowClick(A, 'Benni'); await detailOpen(A);
      await A.click('#hk-pane-contacts .kb-msg');
      ok('"Nachricht" (vom Dashboard) oeffnet den Direkt-Chat auf /chats', await until(async () => /\/chats#c\d+$/.test(A.url()) && !!(await A.$('#ct-chat:not([hidden])')), 15000), A.url());
      const head = await A.evaluate(() => (document.querySelector('#ct-chat .ct-head-t') || {}).textContent || '');
      ok('Chat-Kopf zeigt den Namen der Person (nicht "Name fuer mich")', head.includes(nB) && !head.includes('Benni'), head);
      // auf /chats: Einstellungen schliessen und Hash
      await A.evaluate(() => { location.hash = ''; });
      await A.waitForTimeout(500);
      await A.evaluate(() => HKContacts.open(0));
      await A.waitForSelector('#hk-pane-contacts .kb-item'); await rowClick(A, 'Benni'); await detailOpen(A);
      await A.click('#hk-pane-contacts .kb-msg');
      ok('"Nachricht" auf /chats: Einstellungen zu, Chat offen', await until(async () => !(await A.$('.hk-dialog-set')) && /#c\d+$/.test(A.url()), 8000), A.url());

      // --- Chat-Dialog: "Kontakt bearbeiten …" ---------------------------------------------------------------------
      await A.goto(BASE + '/chats'); await A.waitForTimeout(1500);
      await openNewChat(A);
      const items = await contactMenu(A, nB);
      ok('Menue des Kontakts: "Kontakt bearbeiten …" vor "Aus Kontakten entfernen"', items && items[0] === 'Kontakt bearbeiten …' && items.indexOf('Aus Kontakten entfernen') > 0, items);
      ok('Chat-Liste zeigt den Namen der Person, nicht "Name fuer mich"', !(await A.textContent('.ct-ov')).includes('Benni'));
      await A.click('.menu:not([hidden]) .menu-item:has-text("Kontakt bearbeiten")');
      x = await until(async () => { const y = await pane(A); return y && y.form ? y : null; }, 5000);
      const own = await A.evaluate(() => (document.querySelector('#hk-pane-contacts .kb-own') || {}).value);
      ok('"Kontakt bearbeiten …" oeffnet Einstellungen > Kontakte im Formular', x && own === 'Benni' && !(await A.$('.ct-ov')), own);
      await closeAll(A);
    }

    // --- Bilder: schmal/breit, hell/dunkel -----------------------------------------------------------------------------
    if (SHOTS && !FF || NUR) {
      for (const [scheme, narrow] of [['light', false], ['dark', false], ['light', true], ['dark', true]]) {
        const tag = (narrow ? 'schmal' : 'breit') + '-' + (scheme === 'dark' ? 'dunkel' : 'hell');
        const q = await session(browser, U1, E.SAGA_PASS, { scheme, viewport: narrow ? { width: 390, height: 844 } : { width: 1440, height: 900 } });
        await q.goto(BASE + '/dashboard'); await q.waitForTimeout(1500);
        await openSettingsMenu(q);
        await shot(q, 'nav-' + tag);
        if (!NUR) {
          const w = await hscroll(q);
          if (web) {
            await openContacts(q);
            await shot(q, 'liste-' + tag);
            if (narrow) { const w2 = await hscroll(q); ok('Handy ' + scheme + ': Liste ohne waagerechte Rolle', w2[0] <= w2[1], w2); }
            await rowClick(q, 'Benni'); await detailOpen(q);
            await shot(q, 'detail-' + tag);
            await q.click('#hk-pane-contacts .kb-edit'); await q.waitForSelector('#hk-pane-contacts .kb-form');
            await shot(q, 'bearbeiten-' + tag);
            if (narrow) { const w3 = await hscroll(q); ok('Handy ' + scheme + ': Formular ohne waagerechte Rolle', w3[0] <= w3[1], w3); }
            await q.evaluate(() => document.querySelector('#hk-pane-contacts .kb-form').scrollIntoView({ block: 'end' }));
            await shot(q, 'bearbeiten-unten-' + tag);
          }
          ok((narrow ? 'Handy ' : 'Breit ') + scheme + ': Einstellungen ohne waagerechte Rolle', w[0] <= w[1], w);
        }
        await closeAll(q);
        await chatKeys(q, U1);
        await openNewChat(q);
        await contactMenu(q, nB);
        await shot(q, 'chatmenue-' + tag);
        await q.context().close();
      }
    }
    if (!NUR) ok('Keine Seitenfehler', !errs.length, errs);
  } catch (e) {
    ok('Ablauf ohne Ausnahme', false, String(e && e.stack || e));
  } finally {
    await browser.close();
  }
  const bad = res.filter(x => !x).length;
  console.log(NUR ? 'NUR BILDER: fertig' : bad ? 'FEHLER: ' + bad + ' von ' + res.length : 'ALLES GRUEN (' + res.length + ' Pruefungen)');
  process.exit(NUR ? 0 : bad ? 1 : 0);
})();
