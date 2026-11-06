// Saga 2.5 (Design-Runde UX-20/21/22) - EIN Dialog "Teilen" mit Reitern "Personen" | "Im Web":
// Kopfzeile "Teilen" oeffnet "Personen", Chip "Oeffentlich" oeffnet "Im Web" (Seitenmenue ohne doppeltes Teilen, UX-18);
// Pfeiltasten wechseln den Reiter; Knopf "Einladen" erst mit eindeutigem Vorschlag aktiv, Enter laedt den ersten ein;
// Ausschalten fragt nach (kein roter Knopf mehr); Esc gibt den Fokus an den jeweiligen Ausloeser zurueck; 390 px ohne
// seitlichen Ueberlauf; keine Skriptfehler. Braucht ein zweites und drittes Konto (SAGA_PEER, SAGA_PEER2) zum Einladen.
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=konto SAGA_PASS=... SAGA_PEER=konto2 SAGA_PEER2=konto3 [SHOTS=/tmp/x] node tests/browser_teilen_dialog.js
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
const PEER = process.env.SAGA_PEER, PEER2 = process.env.SAGA_PEER2, SHOTS = process.env.SHOTS || '';
if (!BASE || !USER || !PASS || !PEER || !PEER2) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS, SAGA_PEER, SAGA_PEER2 setzen'); process.exit(2); }
(async () => {
  const b = await chromium.launch({ channel: process.env.PW_CHANNEL || undefined }), res = [], errs = [];
  const ok = (c, m) => { res.push([!!c, m]); console.log((c ? 'OK   ' : 'FAIL ') + m); };
  async function login(ctx) {
    const p = await ctx.newPage(); p.on('pageerror', e => errs.push(String(e)));
    await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
    await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
    return p;
  }
  const api = (p, m, u, bd) => p.evaluate(([m, u, bd]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
    body: bd ? JSON.stringify(bd) : undefined }).then(r => r.json().then(d => ({ s: r.status, d }))), [m, u, bd]);
  const shot = async (p, n) => { if (SHOTS) await p.screenshot({ path: SHOTS + '/' + n + '.png' }); };
  const ctx = await b.newContext({ viewport: { width: 1300, height: 900 }, locale: 'de-DE' });
  const p = await login(ctx);
  const nid = (await api(p, 'POST', '/api/notes', { title: 'Teilen-Dialog Probe' })).d.id;
  ok(nid > 0, 'Seite angelegt');
  await p.goto(BASE + '/notes#' + nid); await p.waitForSelector('#pg-share:not([hidden])'); await p.waitForTimeout(800);
  const st = () => p.evaluate(() => {
    const m = document.getElementById('pg-modal'), sel = m.querySelector('.shp-tab[aria-selected=true]'), a = document.activeElement;
    const inv = m.querySelector('.sh-invite');
    return { open: !m.hidden, tab: sel ? sel.id : '', title: document.getElementById('pg-modal-title').textContent,
      tablist: !!m.querySelector('[role=tablist] [role=tab]'), panels: m.querySelectorAll('[role=tabpanel]').length,
      act: a ? (a.id || a.className) : '', inv: inv ? inv.disabled : null, end: !!m.querySelector('.pub-end, .btn-danger'),
      web: !!m.querySelector('.shp-web:not([hidden])'), visPanel: (m.querySelector('[role=tabpanel]:not([hidden])') || {}).id || '' };
  });
  // 1) Kopfzeile "Teilen" -> Reiter "Personen"
  await p.focus('#pg-share'); await p.click('#pg-share'); await p.waitForSelector('.shp-tabs'); await p.waitForTimeout(300);
  let s = await st();
  ok(s.open && s.title === 'Teilen' && s.tablist && s.panels === 2, 'Teilen: ein Dialog mit Reiterliste und zwei Reiterflaechen');
  ok(s.tab === 'shp-tab-people' && s.visPanel === 'shp-panel-people', 'Teilen: Reiter "Personen" aktiv');
  ok(s.act.indexOf('sh-q') >= 0, 'Teilen: Fokus im Suchfeld');
  ok(s.inv === true, 'Einladen: ohne Auswahl deaktiviert');
  ok(!s.web, 'Personen: ohne Veroeffentlichung keine Zeile "Oeffentlich im Web"');
  // 2) Einladen-Knopf: eindeutiger Vorschlag -> aktiv, Klick laedt ein
  await p.fill('.sh-q', PEER); await p.waitForSelector('.sh-hit'); await p.waitForTimeout(150);
  s = await st(); ok(s.inv === false, 'Einladen: mit eindeutigem Vorschlag aktiv');
  await shot(p, 'personen-vorschlag');
  await p.click('.sh-invite'); await p.waitForTimeout(700);
  let names = await p.$$eval('.sh-list .sh-item:not(.sh-owner) .sh-name', n => n.map(x => x.textContent.toLowerCase()));
  ok(names.length === 1, 'Einladen-Knopf: Freigabe angelegt (' + names.join(',') + ')');
  s = await st(); ok(s.inv === true && (await p.inputValue('.sh-q')) === '', 'Einladen: danach Feld leer und Knopf wieder aus');
  // 3) Enter laedt den ersten Vorschlag ein
  await p.fill('.sh-q', PEER2); await p.waitForSelector('.sh-hit'); await p.waitForTimeout(150);
  await p.press('.sh-q', 'Enter'); await p.waitForTimeout(700);
  names = await p.$$eval('.sh-list .sh-item:not(.sh-owner) .sh-name', n => n.map(x => x.textContent));
  ok(names.length === 2, 'Enter im Feld: ersten Vorschlag eingeladen');
  // 4) Pfeiltasten zwischen den Reitern
  await p.focus('#shp-tab-people'); await p.keyboard.press('ArrowRight'); await p.waitForTimeout(200);
  s = await st(); ok(s.tab === 'shp-tab-web' && s.act === 'shp-tab-web' && s.visPanel === 'shp-panel-web', 'Pfeil rechts: Reiter "Im Web" aktiv und fokussiert');
  await p.keyboard.press('ArrowLeft'); await p.waitForTimeout(200);
  s = await st(); ok(s.tab === 'shp-tab-people' && s.act === 'shp-tab-people', 'Pfeil links: zurueck zu "Personen"');
  await p.keyboard.press('End'); await p.waitForTimeout(100); s = await st(); ok(s.tab === 'shp-tab-web', 'Ende: letzter Reiter');
  // 5) Veroeffentlichen im Reiter "Im Web" (bei Bedarf erst der oeffentliche Name)
  const nameBtn = await p.$('#shp-panel-web .pub-namerow');
  if (nameBtn) {
    await p.fill('#shp-panel-web input.hk-in', 'td' + Date.now().toString(36)); await p.waitForTimeout(700);
    await p.click('#shp-panel-web .btn-primary'); await p.waitForSelector('#shp-panel-web .pub-main-sw');
  }
  s = await st(); ok(!s.end, 'Im Web: kein roter Knopf "Veroeffentlichung beenden"');
  await p.click('#shp-panel-web .pub-main-sw .hk-switch'); await p.waitForTimeout(900);
  ok(await p.isChecked('#shp-panel-web .pub-main-sw .hk-switch'), 'Im Web: Schalter veroeffentlicht die Seite');
  s = await st(); ok(!s.end, 'Im Web (veroeffentlicht): weiterhin kein roter Knopf');
  await shot(p, 'web-veroeffentlicht');
  await p.click('#shp-tab-people'); await p.waitForTimeout(200);
  s = await st(); ok(s.web, 'Personen: Zeile "Oeffentlich im Web" sichtbar');
  await shot(p, 'personen-oeffentlich');
  await p.click('.shp-web-go'); await p.waitForTimeout(200);
  s = await st(); ok(s.tab === 'shp-tab-web', '"aendern" wechselt zum Reiter "Im Web"');
  // 6) Ausschalten fragt nach; Abbrechen laesst die Seite oeffentlich
  await p.click('#shp-panel-web .pub-main-sw .hk-switch'); await p.waitForSelector('.hk-ask'); await p.waitForTimeout(200);
  ok(await p.$('.hk-ask'), 'Ausschalten: Rueckfrage erscheint');
  await shot(p, 'web-rueckfrage');
  await p.click('.hk-ask .hk-ask-bar .btn:not(.btn-destr):not(.btn-primary)'); await p.waitForTimeout(300);
  ok(await p.isChecked('#shp-panel-web .pub-main-sw .hk-switch') && (await st()).open, 'Abbrechen: bleibt oeffentlich, Dialog offen');
  // 7) Esc -> Fokus zurueck an "Teilen"
  await p.keyboard.press('Escape'); await p.waitForTimeout(250);
  s = await st(); ok(!s.open && s.act === 'pg-share', 'Esc: Dialog zu, Fokus zurueck an "Teilen"');
  // 8) Chip "Oeffentlich" -> Reiter "Im Web", Esc -> Fokus zurueck an den Chip
  await p.waitForSelector('#pg-public:not([hidden])');
  await p.focus('#pg-public'); await p.click('#pg-public'); await p.waitForSelector('.shp-tabs'); await p.waitForTimeout(300);
  s = await st(); ok(s.open && s.tab === 'shp-tab-web' && s.visPanel === 'shp-panel-web', 'Chip "Oeffentlich": oeffnet Reiter "Im Web"');
  ok(s.act === 'shp-tab-web', 'Chip: Fokus auf dem aktiven Reiter');
  // Fokusfalle: Tab vom letzten Element springt an den Anfang des Dialogs
  for (let i = 0; i < 25; i++) await p.keyboard.press('Tab');
  ok(await p.evaluate(() => document.getElementById('pg-modal').contains(document.activeElement)), 'Fokusfalle: Tab bleibt im Dialog');
  await p.keyboard.press('Escape'); await p.waitForTimeout(250);
  s = await st(); ok(!s.open && s.act === 'pg-public', 'Esc: Fokus zurueck an den Chip');
  // 9) Design-Runde UX-18: steht "Teilen" in der Kopfzeile, fehlen "Teilen …"/"Veröffentlichen …" im Seitenmenue
  //    (der Dialog hat den Reiter "Im Web"); im Peek (kein Kopfzeilen-Knopf) stehen sie weiter im Menue
  await p.click('#pg-more'); await p.waitForTimeout(200);
  ok(!(await p.$('.menu:not([hidden]) .menu-item:has-text("Veröffentlichen")')) && !(await p.$('.menu:not([hidden]) .menu-item:has-text("Teilen …")')),
     'Seitenmenue: kein doppeltes Teilen/Veroeffentlichen neben dem Kopfzeilen-Knopf');
  await p.keyboard.press('Escape'); await p.waitForTimeout(250);
  s = await st(); ok(!s.open && s.act === 'pg-more', 'Esc: Fokus zurueck an das Seitenmenue');
  // 10) Ausschalten bestaetigen: Seite privat, Zeile im Reiter "Personen" weg
  await p.click('#pg-share'); await p.waitForSelector('.shp-tabs'); await p.click('#shp-tab-web');
  await p.click('#shp-panel-web .pub-main-sw .hk-switch'); await p.waitForSelector('.hk-ask');
  await p.click('.hk-ask .btn-destr'); await p.waitForTimeout(900);
  ok(!(await p.isChecked('#shp-panel-web .pub-main-sw .hk-switch')), 'Bestaetigt: Veroeffentlichung beendet');
  await p.click('#shp-tab-people'); await p.waitForTimeout(150);
  s = await st(); ok(!s.web, 'Personen: Zeile "Oeffentlich im Web" wieder weg');
  ok(await p.evaluate(() => document.getElementById('pg-public').hidden), 'Chip "Oeffentlich" ausgeblendet');
  await p.keyboard.press('Escape');
  // 11) Handy 390 px: normales Modal, kein seitlicher Ueberlauf
  const mc = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'de-DE' });
  const m = await login(mc);
  await m.goto(BASE + '/notes#' + nid); await m.waitForSelector('#pg-share:not([hidden])'); await m.waitForTimeout(800);
  await m.click('#pg-share'); await m.waitForSelector('.shp-tabs'); await m.waitForTimeout(400);
  const over = () => m.evaluate(() => { const bx = document.querySelector('#pg-modal .modal-box');
    return { doc: document.documentElement.scrollWidth - innerWidth, box: bx.scrollWidth - bx.clientWidth, right: Math.round(bx.getBoundingClientRect().right - innerWidth) }; });
  let o = await over(); ok(o.doc <= 0 && o.box <= 0 && o.right <= 0, '390 px Personen: kein Ueberlauf ' + JSON.stringify(o));
  if (SHOTS) await m.screenshot({ path: SHOTS + '/handy-personen.png' });
  await m.click('#shp-tab-web'); await m.waitForTimeout(300);
  o = await over(); ok(o.doc <= 0 && o.box <= 0 && o.right <= 0, '390 px Im Web: kein Ueberlauf ' + JSON.stringify(o));
  if (SHOTS) await m.screenshot({ path: SHOTS + '/handy-web.png' });
  // Aufraeumen
  await api(p, 'DELETE', '/api/notes/' + nid);
  ok(!errs.length, 'keine Skriptfehler ' + errs.join(' | '));
  await b.close();
  const bad = res.filter(r => !r[0]).length; console.log('\n' + (res.length - bad) + '/' + res.length + ' ok'); process.exit(bad ? 1 : 0);
})();
