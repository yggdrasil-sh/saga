// Browser-Reihe fuer den eigenen Status im Web (2.6, Bugs-DB 2029-5 Teil A): static/userstatus.js (Menue, Dialog, Punkt am
// Kontoknopf, Sammelmeldung), static/push.js ("Erreichbarkeit"), static/chat.js (Status anderer in Liste/Kopf, Hinweis vor
// dem Anruf), static/live.js (Weiterleitung, keine Einblendung bei Nicht stoeren). Zwei Konten, Chromium oder Firefox:
//   SAGA_BASE=http://host:port SAGA_USER=a SAGA_PASS=... SAGA_USER2=b SAGA_PASS2=... [SAGA_USER3=c SAGA_NAME3="Name von c"]
//   [BR=firefox] [SHOTS=/ordner] [NUR_BILDER=1] [SAGA_TEST_TOKEN=... (Server mit SAGA_TEST_HOOKS=1: Testuhr fuer den Ablauf)]
//   [PLAYWRIGHT=/pfad/zu/playwright-core] node tests/browser_status.js
// Geprueft: Menue per Tastatur (Tab/Enter/Escape, Fokus zurueck), Rollen/Namen (Dialog, Radios, Schalter, Kombinationsfeld),
// Setzen mit Dauer und Text (Zaehler), Ablauf (Testuhr bzw. kurze Dauer), 409-Abgleich zweier Tabs, Status des Gegenuebers
// in Liste und Kopf live (<= 2 s per chat.status), auch mit eigenem Online-Status aus, Hinweis vor dem Anruf (dnd: Rueckfrage,
// im Anruf: Hinweis), Sammelmeldung als ruhige Meldung, Einstellungen speichern + neu laden, keine Seitenfehler.
// NUR_BILDER=1: nur die Bilder (hell/dunkel, schmal 390x844/breit 1440x900) - laeuft auch gegen einen Stand ohne diese
// Web-Teile (Vorher-Bilder), dann ohne Pruefungen.
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
  await p.waitForTimeout(250);
  await p.screenshot({ path: path.join(SHOTS, name + '.png') }).catch(e => note('Bild ' + name + ': ' + e.message));
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 15000, step = 100) { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn().catch(() => null); if (v) return v; await sleep(step); } return v; }

async function session(browser, user, pass, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1440, height: 900 }, locale: 'de-DE', colorScheme: opts.scheme || 'dark',
                                         timezoneId: 'Europe/Berlin' });
  const p = await ctx.newPage();
  p.on('pageerror', e => { errs.push(user + ': ' + e.message); console.log('  [pageerror ' + user + '] ' + e.message); });
  await login(p, user, pass);
  return p;
}
async function login(p, user, pass) {
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  // Frage "Erste Schritte" (static/firststeps.js, neue Konten ohne eigene Seiten) vorab beantworten: sie erscheint ~2 s nach
  // jedem Laden, sobald kein Dialog offen ist - sonst liegt sie z. B. direkt nach dem Schliessen des Status-Dialogs ueber
  // dem Kontoknopf und nimmt den Fokus (Bugs-DB 2394). Weggeklickt/Escape speichert nichts - daher per API.
  const fs = await papi(p, 'POST', '/api/first-steps', { answer: 'no' }).catch(e => ({ status: 0, d: { error: e.message } }));
  if ([200, 403, 404].indexOf(fs.status) < 0) note('Erste Schritte beantworten: ' + JSON.stringify(fs));
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
// API aus der Seite (Sitzung des Kontos)
const papi = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin',
  headers: Object.assign({ 'X-Saga': '1' }, b !== undefined ? { 'Content-Type': 'application/json' } : {}), body: b !== undefined ? JSON.stringify(b) : undefined })
  .then(async r => ({ status: r.status, d: await r.json().catch(() => ({})) })), [m, u, b === undefined ? undefined : b]);
// Testuhr (saga/testclock.py, nur mit SAGA_TEST_HOOKS=1 und Token)
async function hook(p, body) {
  if (!E.SAGA_TEST_TOKEN) return null;
  try {
    const r = await fetch(BASE + p, { method: 'POST', headers: { 'X-Saga-Test': E.SAGA_TEST_TOKEN, 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    return r.ok ? await r.json().catch(() => ({})) : null;
  } catch (e) { return null; }
}
async function openChat(p, user) {
  await p.goto(BASE + '/chats#u' + encodeURIComponent(user)); await p.waitForSelector('#ct-chat:not([hidden])', { timeout: 30000 }); await p.waitForTimeout(1200);
}
const usView = p => p.evaluate(() => window.HK && HK.userStatus ? HK.userStatus.view() : null);
const ae = p => p.evaluate(() => { const a = document.activeElement; return a ? (a.id || a.className || a.tagName) + '' : ''; });
async function menuOpen(p) {
  await p.click('#hk-user'); await p.waitForSelector('.menu:not([hidden]) .menu-item', { timeout: 5000 });
}
async function closeAll(p) { for (let i = 0; i < 3; i++) { await p.keyboard.press('Escape'); await p.waitForTimeout(120); } }
// Status per Dialog setzen: state, dur (h1|t7|off|keep), text
async function setViaDialog(p, state, dur, text) {
  await menuOpen(p); await p.click('.menu .us-mi'); await p.waitForSelector('.us-dlg');
  await p.check('.us-dlg input[name=us-state][value=' + state + ']');
  if (dur) await p.check('.us-dlg input[name=us-dur][value=' + dur + ']');
  if (text !== undefined) await p.fill('#us-text-in', text);
  const resp = p.waitForResponse(r => r.url().indexOf('/api/account/status') >= 0 && r.request().method() === 'PUT', { timeout: 10000 }).catch(() => null);
  await p.click('.us-dlg .us-save');
  const r = await resp;
  return r ? r.status() : 0;
}
const peerKey = (p) => p.evaluate(() => fetch('/api/chats', { headers: { 'X-Saga': '1' } }).then(r => r.json())
  .then(d => ((d.chats || []).filter(c => c.kind === 'dm')[0] || {}).peer || {}).then(x => x.username || ''));
const chip = p => p.evaluate(() => { const c = document.querySelector('#ct-sub .us-chip'); return c ? c.textContent : ''; });
const badge = p => p.evaluate(() => { const b = document.querySelector('#ct-list .ct-item .us-badge'); return b ? b.className : ''; });

// =====================================================================================================================
// Bilder (vorher/nachher): hell + dunkel, schmal + breit
// =====================================================================================================================
async function bilder(browser) {
  const MODES = [['breit', { width: 1440, height: 900 }], ['schmal', { width: 390, height: 844 }]];
  for (const scheme of ['hell', 'dunkel']) {
    for (const [mw, vp] of MODES) {
      const tag = scheme + '-' + mw, cs = scheme === 'hell' ? 'light' : 'dark';
      const a = await session(browser, U1, E.SAGA_PASS, { viewport: vp, scheme: cs }), b = await session(browser, U2, E.SAGA_PASS2, { viewport: vp, scheme: cs });
      await chatKeys(a, U1); await chatKeys(b, U2);
      // A: Nicht stoeren mit Text (Server-API gibt es auch im Vorher-Stand)
      await papi(a, 'PUT', '/api/account/status', { state: 'dnd', dur: 7200, text: 'Im Kundentermin', source: 'manual' });
      await a.goto(BASE + '/chats'); await a.waitForTimeout(1800);
      await menuOpen(a).catch(() => {}); await shot(a, tag + '-1-menue-offen');
      const hasDlg = await a.$('.menu .us-mi');
      if (hasDlg) { await a.click('.menu .us-mi'); await a.waitForSelector('.us-dlg'); await shot(a, tag + '-1b-status-dialog'); }
      await closeAll(a);
      // B: Liste mit Status, Kopf mit Text
      await b.goto(BASE + '/chats'); await b.waitForTimeout(2500); await shot(b, tag + '-2-chatliste');
      await openChat(b, U1); await b.waitForTimeout(1500); await shot(b, tag + '-3-chatkopf');
      // B: Hinweis vor dem Anruf (nur mit dem neuen Web - der alte Stand riefe wirklich an)
      if (await b.evaluate(() => !!(window.HK && HK.userStatus))) {
        await b.evaluate(() => { const x = document.getElementById('ct-call-a'); if (x) { x.hidden = false; x.click(); } });
        await b.waitForSelector('.hk-ask', { timeout: 4000 }).catch(() => {});
        await shot(b, tag + '-5-hinweis-anruf');
        await closeAll(b);
      } else await shot(b, tag + '-5-hinweis-anruf');
      // B: Einstellungen -> Mitteilungen ("Erreichbarkeit")
      await b.goto(BASE + '/dashboard?settings=reach'); await b.waitForSelector('#us-reach', { timeout: 6000 }).catch(() => {}); await b.waitForTimeout(600);
      if (!(await b.$('#us-reach'))) {                                  // Vorher: Mitteilungen mit den Ruhezeiten
        await b.evaluate(() => { const t = document.getElementById('hk-tab-push'); if (t) t.click(); });
        await b.waitForTimeout(1500);
        await b.evaluate(() => { const q = [...document.querySelectorAll('.hk-set-push h4')].filter(h => /Ruhezeiten/.test(h.textContent))[0]; if (q) q.scrollIntoView({ block: 'start' }); });
      } else await b.evaluate(() => document.getElementById('us-reach').scrollIntoView({ block: 'start' }));
      await shot(b, tag + '-4-einstellungen-erreichbarkeit');
      await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
      await a.context().close(); await b.context().close();
    }
  }
}

(async () => {
  const BT = FF ? pw.firefox : pw.chromium;
  const browser = FF ? await BT.launch({ headless: true }) : await BT.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true });
  try {
    if (NUR) { await bilder(browser); ok('Bilder erstellt', true); return; }
    const a = await session(browser, U1, E.SAGA_PASS), b = await session(browser, U2, E.SAGA_PASS2);
    await chatKeys(a, U1); await chatKeys(b, U2);
    ok('Chat-Schluessel in beiden Browsern bereit', !(await a.$('.ct-ov')) && !(await b.$('.ct-ov')));
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
    await papi(b, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
    await papi(a, 'PUT', '/api/account/status/prefs', { quiet_dnd: true, quiet_digest: false, in_call: 'knock', show_call: true, busy: 'show', repeat: true, repeat_s: 180, mention_break: false, exceptions: [] });
    // Direkt-Chat A <-> B (eine Nachricht, damit er bei beiden in der Liste steht)
    await openChat(a, U2); await a.fill('#ct-input', 'Hallo Ben, kurze Frage zum Termin.'); await a.press('#ct-input', 'Enter'); await a.waitForTimeout(1200);
    await openChat(b, U1);
    ok('B: Direkt-Chat offen, noch kein Status von A', !(await chip(b)) && !(await badge(b)));

    // --- 1. Menue per Tastatur: Tab/Enter/Escape, Fokus zurueck -----------------------------------------------------------
    await a.goto(BASE + '/chats'); await a.waitForTimeout(1500);
    ok('A: eigener Status geladen (userstatus1)', !!(await until(() => usView(a), 8000)));
    await a.focus('#hk-user'); await a.keyboard.press('Enter'); await a.waitForTimeout(250);
    const mi = await a.evaluate(() => { const b = document.querySelector('.menu:not([hidden]) .us-mi'); return b ? { t: b.textContent, label: b.getAttribute('aria-label'), role: b.getAttribute('role'), focus: document.activeElement === b, dot: !!b.querySelector('.us-dot.us-available') } : null; });
    ok('Menue: Eintrag "Verfügbar" mit Punkt, Rolle menuitem, Name nennt den Status, Fokus darauf', mi && /Verfügbar/.test(mi.t) && mi.role === 'menuitem' && /Status: Verfügbar/.test(mi.label || '') && mi.focus && mi.dot, mi);
    await a.keyboard.press('Enter'); await a.waitForTimeout(250);
    const dlgRole = await a.getByRole('dialog', { name: 'Mein Status' }).count();
    ok('Dialog: Rolle dialog mit Namen "Mein Status", Fokus auf dem gewaehlten Zustand', dlgRole === 1 && await a.evaluate(() => document.activeElement && document.activeElement.name === 'us-state' && document.activeElement.checked), await ae(a));
    ok('Dialog: drei Radios (Verfügbar/Beschäftigt/Nicht stören) und Dauer-Gruppe', await a.getByRole('radio', { name: 'Nicht stören' }).count() === 1 &&
       await a.getByRole('radio', { name: 'Beschäftigt' }).count() === 1 && await a.getByRole('radio', { name: 'Bis ich es ausschalte' }).count() === 1 &&
       await a.getByRole('group', { name: 'Dauer' }).count() === 1);
    ok('Dialog: Dauer bei "Verfügbar" gesperrt', await a.evaluate(() => [...document.querySelectorAll('.us-dlg input[name=us-dur]')].every(i => i.disabled)));
    let inside = true; for (let i = 0; i < 12; i++) { await a.keyboard.press('Tab'); if (!(await a.evaluate(() => document.querySelector('.us-dlg').contains(document.activeElement)))) inside = false; }
    ok('Dialog: Tab bleibt im Dialog', inside);
    await a.keyboard.press('Escape'); await a.waitForTimeout(250);
    ok('Dialog: Escape schliesst, Fokus zurueck am Kontoknopf', !(await a.$('.us-dlg')) && await ae(a) === 'hk-user', await ae(a));

    // --- 2. Setzen: Nicht stoeren, 1 Stunde, Text (Zaehler) -> B sieht es live in Liste und Kopf ---------------------------
    await menuOpen(a); await a.click('.menu .us-mi'); await a.waitForSelector('.us-dlg');
    await a.check('.us-dlg input[name=us-state][value=dnd]'); await a.check('.us-dlg input[name=us-dur][value=h1]');
    await a.fill('#us-text-in', 'Im Kundentermin');
    ok('Dialog: Zaehler "15 von 80 Zeichen"', /15 von 80 Zeichen/.test(await a.textContent('#us-count')), await a.textContent('#us-count'));
    ok('Dialog: Textfeld hoechstens 80 Zeichen', await a.evaluate(() => document.getElementById('us-text-in').maxLength) === 80);
    const t0 = Date.now();
    const pr = a.waitForResponse(r => r.url().indexOf('/api/account/status') >= 0 && r.request().method() === 'PUT');
    await a.click('.us-dlg .us-save');
    const putR = await pr; const t1 = Date.now();
    const v1 = (await papi(a, 'GET', '/api/account/status')).d;
    ok('PUT 200, Server: dnd, Ende ~1 h, Text, source manual', putR.status() === 200 && v1.state === 'dnd' && v1.text === 'Im Kundentermin' && v1.source === 'manual' &&
       Math.abs(v1.until - v1.now - 3600) < 30, { st: putR.status(), state: v1.state, text: v1.text, d: v1.until - v1.now });
    ok('A: Dialog zu, Punkt am Kontoknopf (Nicht stören)', !(await a.$('.us-dlg')) && !!(await a.$('#hk-user .us-me.us-dnd')));
    const ual = await a.getAttribute('#hk-user', 'aria-label');
    ok('A: Kontoknopf nennt den Status im Namen (aria-label)', /Benutzermenü/.test(ual) && /Status: Nicht stören/.test(ual), ual);
    await menuOpen(a);
    const mi2 = await a.evaluate(() => { const b = document.querySelector('.menu .us-mi'); return b ? b.textContent : ''; });
    ok('Menue: "Nicht stören" mit Ablaufzeit ("bis …")', /Nicht stören/.test(mi2) && /bis \d{1,2}:\d\d/.test(mi2), mi2);
    await shot(a, 'status-1-menue');
    await closeAll(a);
    const seen = await until(async () => { const c = await chip(b); return /Nicht stören/.test(c) ? c : null; }, 5000, 50); const t2 = Date.now();
    ok('B: Kopf zeigt "Nicht stören bis … · Im Kundentermin" (live)', seen && /bis \d{1,2}:\d\d/.test(seen) && /Im Kundentermin/.test(seen), seen || await chip(b));
    ok('B: Anzeige <= 2 s nach dem Speichern (chat.status)', seen && t2 - t1 <= 2000, { ms: t2 - t1, put_ms: t1 - t0 });
    ok('B: Abzeichen am Profilbild in der Liste (dnd)', /us-dnd/.test(await badge(b)), await badge(b));
    await b.click('#ct-sub .us-chip'); await b.waitForTimeout(200);
    const tst = await b.evaluate(() => { const t = document.querySelector('.toast'); return t && !t.hidden ? t.textContent : ''; });
    ok('B: Status-Chip ist ein Knopf - Tippen zeigt den vollen Text (Gruppeninfo bleibt zu)', /Nicht stören bis/.test(tst) && /Im Kundentermin/.test(tst) &&
       await b.evaluate(() => document.querySelector('#ct-sub .us-chip').tagName === 'BUTTON') && !(await b.$('.ct-ov')), tst);
    ok('B: Listeneintrag nennt den Status fuer Bildschirmleser', await b.evaluate(() => { const s = document.querySelector('#ct-list .ct-item .ct-item-n .sr-only'); return !!s && /Nicht stören/.test(s.textContent); }));
    await shot(b, 'status-2-liste-kopf');

    // --- 3. Kein Einblenden bei eigenem Nicht stoeren; B schreibt (zaehlt fuer die Sammelmeldung) ----------------------------
    await a.goto(BASE + '/dashboard'); await a.waitForTimeout(1500);
    await a.evaluate(() => { window.__toasts = []; const o = HK.toast; HK.toast = function (m) { window.__toasts.push(String(m)); return o.apply(this, arguments); }; });
    await b.fill('#ct-input', 'Melde dich, wenn du Zeit hast.'); await b.press('#ct-input', 'Enter'); await b.waitForTimeout(3000);
    ok('A (Nicht stören): keine Einblendung "Neue Nachricht" (Glocke bleibt)', await a.evaluate(() => !window.__toasts.some(t => /Neue Nachricht|neue Nachrichten/.test(t))), await a.evaluate(() => window.__toasts));

    // --- 4. Hinweis vor dem Anruf (B ruft A an, A: Nicht stoeren) ---------------------------------------------------------------
    await b.evaluate(() => { window.__calls = []; if (window.HKCalls) window.HKCalls.start = function (c, v) { window.__calls.push([c && c.id, !!v]); }; });
    await b.evaluate(() => { const x = document.getElementById('ct-call-a'); x.hidden = false; x.click(); });
    await b.waitForSelector('.hk-ask', { timeout: 4000 }).catch(() => {});
    const ask = await b.evaluate(() => { const d = document.querySelector('.hk-ask-box'); return d ? { role: d.getAttribute('role'), t: d.textContent } : null; });
    ok('B: Rueckfrage "… möchte gerade nicht gestört werden. Trotzdem anrufen?" (alertdialog)', ask && ask.role === 'alertdialog' && /möchte gerade nicht gestört werden\. Trotzdem anrufen\?/.test(ask.t), ask);
    await shot(b, 'status-3-hinweis-anruf');
    await b.click('.hk-ask .btn:not(.btn-primary)'); await b.waitForTimeout(300);
    ok('B: Abbrechen -> kein Anruf', await b.evaluate(() => window.__calls.length === 0));
    await b.evaluate(() => document.getElementById('ct-call-v').click()); await b.waitForSelector('.hk-ask');
    await b.click('.hk-ask .btn-primary'); await b.waitForTimeout(300);
    ok('B: Anrufen -> HKCalls.start mit Video', await b.evaluate(() => window.__calls.length === 1 && window.__calls[0][1] === true), await b.evaluate(() => window.__calls));

    // --- 5. Ablauf (Testuhr: +1 h, Takt) - A wieder verfuegbar, Sammelmeldung, B: Status weg ---------------------------------
    const clk = await hook('/_test/clock', { advance: 3601 });
    if (clk) {
      await hook('/_test/tick');
      note('Testuhr +3601 s, Takt');
    } else {
      note('keine Testhaken - Ablauf mit kurzer Dauer (60 s, Ablauf-Schleife 30 s)');
      await papi(a, 'PUT', '/api/account/status', { state: 'dnd', dur: 60, source: 'manual' });
      await sleep(61000);
    }
    const back = await until(async () => { const v = await usView(a); return v && v.state === 'available' ? v : null; }, clk ? 8000 : 45000, 200);
    ok('A: nach Ablauf "Verfügbar" (status.changed), Punkt am Kontoknopf weg', back && back.source === 'system' && !(await a.$('#hk-user .us-me')), back);
    const dg = await until(() => a.evaluate(() => window.__toasts.filter(t => /Während „Nicht stören“/.test(t))[0] || null), 8000, 200);
    ok('A: Sammelmeldung als ruhige Meldung, nur Zahlen ("Während „Nicht stören“: 1 Nachricht")', dg && /1 Nachricht/.test(dg) && !/Anna|Ben/.test(dg), dg || await a.evaluate(() => window.__toasts));
    await shot(a, 'status-4-sammelmeldung');
    ok('B: Status von A weg (chat.status)', !!(await until(async () => !(await chip(b)) && !(await badge(b)), 6000)));
    if (clk) await hook('/_test/clock', { reset: true });

    // --- 6. Eigener Online-Status aus: Status des Gegenuebers bleibt sichtbar ------------------------------------------------
    await papi(b, 'PUT', '/api/chat/privacy', { online: false });
    await b.waitForTimeout(1500);
    await papi(a, 'PUT', '/api/account/status', { state: 'busy', source: 'manual', text: 'Bin gleich wieder da' });
    const busy = await until(async () => { const c = await chip(b); return /Beschäftigt/.test(c) ? c : null; }, 5000, 100);
    ok('B (Online-Status aus): sieht "Beschäftigt · Bin gleich wieder da" trotzdem', busy && /Bin gleich wieder da/.test(busy), busy || await chip(b));
    await b.reload(); await b.waitForSelector('#ct-chat:not([hidden])', { timeout: 30000 });
    const busy2 = await until(async () => { const c = await chip(b); return /Beschäftigt/.test(c) ? c : null; }, 8000, 200);
    ok('B (Online-Status aus): auch nach dem Neuladen (Abo fragt weiter nach status)', !!busy2 && !(await b.$('#ct-sub .ct-online')), busy2);
    await papi(b, 'PUT', '/api/chat/privacy', { online: true });

    // --- 7. Im Anruf (Ereignis nachgestellt - echter Anruf: Reihe browser_anklopfen.js): Hinweis ohne Rueckfrage ------------
    const pk = await peerKey(b);
    // chat.status nach dem Absenden einer Abo-Anfrage gewinnt gegen deren (aeltere) Antwort
    await b.evaluate(k => { const ev = (t, d) => document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: t, data: d } }));
      ev('chat.privacy', { receipts: true, online: true }); ev('chat.status', { username: k, status: { s: 'in_call' } }); }, pk);
    await b.waitForTimeout(1500);
    ok('B: Live-Status nach dem Absenden bleibt gegen die Abo-Antwort (nachgestellt)', /Im Anruf/.test(await chip(b)), await chip(b));
    await b.evaluate(k => document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'chat.status', data: { username: k, status: { s: 'in_call' } } } })), pk);
    ok('B: Kopf zeigt "Im Anruf" (nachgestellt)', /Im Anruf/.test(await chip(b)), await chip(b));
    await b.evaluate(() => { window.__toasts = []; const o = HK.toast; HK.toast = function (m) { window.__toasts.push(String(m)); return o.apply(this, arguments); };
      window.__rtc = window.RTCPeerConnection; window.RTCPeerConnection = undefined; document.getElementById('ct-call-a').click(); });
    await b.waitForTimeout(400);
    const tt = await b.evaluate(() => window.__toasts);
    ok('B: "… telefoniert gerade – dein Anruf klopft an." ohne Rueckfrage', tt.some(t => /telefoniert gerade – dein Anruf klopft an\./.test(t)) && !(await b.$('.hk-ask')), tt);
    await b.evaluate(() => { window.RTCPeerConnection = window.__rtc; });
    await b.evaluate(k => document.dispatchEvent(new CustomEvent('hk-live', { detail: { type: 'chat.status', data: { username: k, status: null } } })), pk);
    ok('B: chat.status null entfernt die Anzeige', !(await chip(b)) && !(await badge(b)));

    // --- 8. 409: zwei Tabs, einer ohne Live-Kanal (veralteter Stand) -------------------------------------------------------
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });
    const a2 = await a.context().newPage(); a2.on('pageerror', e => errs.push('a2: ' + e.message));
    await a2.route('**/api/events**', r => r.abort());
    await a2.goto(BASE + '/dashboard'); await until(() => usView(a2), 8000);
    const verOld = (await usView(a2)).ver;
    await a.goto(BASE + '/dashboard'); await a.waitForTimeout(1200);
    ok('Tab 1: Beschäftigt gesetzt', await setViaDialog(a, 'busy', 'off', '') === 200);
    ok('Tab 2: Stand veraltet (kein Live-Kanal)', (await usView(a2)).ver === verOld);
    const st409 = await setViaDialog(a2, 'dnd', 'off', 'Konzentriert');
    await a2.waitForTimeout(400);
    const after = await a2.evaluate(() => { const d = document.querySelector('.us-dlg'); if (!d) return null; const c = d.querySelector('input[name=us-state]:checked');
      return { checked: c && c.value, info: d.querySelector('.us-info').textContent, err: (d.querySelector('.hk-status.err') || {}).textContent || '' }; });
    ok('Tab 2: 409 -> Ansicht uebernommen, Dialog still neu gezeigt (Beschäftigt, keine Fehlermeldung)', st409 === 409 && after && after.checked === 'busy' && /Beschäftigt/.test(after.info) && !after.err, { st409, after });
    ok('Server: weiter Beschäftigt', (await papi(a, 'GET', '/api/account/status')).d.state === 'busy');
    await a2.check('.us-dlg input[name=us-state][value=dnd]');
    const pr2 = a2.waitForResponse(r => r.url().indexOf('/api/account/status') >= 0 && r.request().method() === 'PUT'); await a2.click('.us-dlg .us-save');
    ok('Tab 2: zweiter Versuch mit aktuellem Stand -> 200, Nicht stören', (await pr2).status() === 200 && (await papi(a, 'GET', '/api/account/status')).d.state === 'dnd');
    ok('Tab 1: gleicht live ab (status.changed)', !!(await until(async () => { const v = await usView(a); return v && v.state === 'dnd'; }, 5000)));
    await a2.close();
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });

    // --- 9. Fokus-Status: "vom Gerät (Fokus)", ohne Text ------------------------------------------------------------------
    await papi(a, 'PUT', '/api/account/status', { state: 'dnd', source: 'focus' });
    await until(async () => { const v = await usView(a); return v && v.source === 'focus'; }, 5000);
    await menuOpen(a);
    const fm = await a.evaluate(() => (document.querySelector('.menu .us-mi') || {}).textContent || '');
    ok('Menue: Fokus-Status als "vom Gerät (Fokus)"', /Nicht stören/.test(fm) && /vom Gerät \(Fokus\)/.test(fm), fm);
    await closeAll(a);
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });

    // --- 10. Einstellungen "Erreichbarkeit": speichern + neu laden ------------------------------------------------------------
    await menuOpen(a); await a.click('.menu .us-mi'); await a.waitForSelector('.us-dlg');
    await a.click('.us-dlg .us-more'); await a.waitForSelector('#us-reach', { timeout: 8000 });
    await a.waitForTimeout(400);
    ok('Erreichbarkeit: aus dem Status-Dialog erreichbar, Fokus auf der Ueberschrift', await a.evaluate(() => { const h = document.querySelector('#us-reach h4'); return !!h && document.activeElement === h && !document.getElementById('hk-pane-push').hidden; }));
    ok('Erreichbarkeit: Schalter mit Namen (Rolle switch)', await a.getByRole('switch', { name: /Andere sehen „Im Anruf“/ }).count() === 1 &&
       await a.getByRole('switch', { name: /Ruhezeiten zählen als „Nicht stören“/ }).count() === 1 && await a.getByRole('combobox', { name: /Ausnahme hinzufügen/ }).count() === 1);
    await shot(a, 'status-5-erreichbarkeit');
    const saveR = () => a.waitForResponse(r => r.url().indexOf('/api/account/status/prefs') >= 0, { timeout: 8000 });
    let w = saveR(); await a.getByRole('switch', { name: /Andere sehen „Im Anruf“/ }).click(); await w;
    w = saveR(); await a.selectOption('#us-reach select[data-k=in_call]', 'busy'); await w;
    w = saveR(); await a.selectOption('#us-reach select[data-k=busy]', 'silent'); await w;
    w = saveR(); await a.fill('#us-repeat-s', '120'); await a.press('#us-repeat-s', 'Tab'); await w;
    w = saveR(); await a.getByRole('switch', { name: /Erwähnungen kommen durch/ }).click(); await w;
    // Grenze: 10 Sekunden -> Hinweis, nichts gespeichert
    await a.fill('#us-repeat-s', '10'); await a.press('#us-repeat-s', 'Tab'); await a.waitForTimeout(300);
    ok('Erreichbarkeit: Sekunden ausserhalb der Grenzen -> Hinweis, Wert zurueck', /von 30 bis 3600/.test(await a.textContent('#us-reach .hk-status')) && await a.inputValue('#us-repeat-s') === '120');
    // Ausnahme: Person per Suche
    const nm3 = E.SAGA_NAME3 || 'Cora';
    await a.fill('#us-exc-q', nm3.split(' ')[0]);
    const opt = await until(() => a.evaluate(() => { const o = document.querySelector('#us-exc-res [role=option]'); return o ? o.textContent : null; }), 6000);
    ok('Ausnahmen: Suche findet die Person', !!opt && opt.indexOf(nm3.split(' ')[0]) >= 0, opt);
    w = saveR(); await a.press('#us-exc-q', 'ArrowDown'); await a.press('#us-exc-q', 'Enter'); await w; await a.waitForTimeout(300);
    ok('Ausnahmen: Person als Eintrag, Fokus zurueck im Suchfeld', await a.evaluate(n => [...document.querySelectorAll('#us-reach .us-exc-i')].some(li => li.textContent.indexOf(n) >= 0), nm3.split(' ')[0]) && await ae(a) === 'us-exc-q');
    const pv = (await papi(a, 'GET', '/api/account/status')).d;
    ok('Server: Einstellungen gespeichert', pv.prefs.show_call === false && pv.prefs.in_call === 'busy' && pv.prefs.busy === 'silent' && pv.prefs.repeat_s === 120 && pv.prefs.mention_break === true &&
       pv.exceptions.length === 1 && pv.exceptions[0].type === 'user' && /^~/.test(pv.exceptions[0].ref), pv.prefs);
    await shot(a, 'status-6-erreichbarkeit-gespeichert');
    await a.reload(); await a.waitForTimeout(800);
    await a.goto(BASE + '/dashboard?settings=reach'); await a.waitForSelector('#us-reach', { timeout: 8000 }); await a.waitForTimeout(300);
    const re = await a.evaluate(() => ({ show: document.querySelector('#us-reach input[data-k=show_call]').checked, ic: document.querySelector('#us-reach select[data-k=in_call]').value,
      busy: document.querySelector('#us-reach select[data-k=busy]').value, rs: document.getElementById('us-repeat-s').value,
      men: document.querySelector('#us-reach input[data-k=mention_break]').checked, exc: document.querySelectorAll('#us-reach .us-exc-i').length }));
    ok('Erreichbarkeit: nach dem Neuladen wie gespeichert', !re.show && re.ic === 'busy' && re.busy === 'silent' && re.rs === '120' && re.men && re.exc === 1, re);
    // anderes Geraet fuegt eine Ausnahme hinzu, waehrend hier bearbeitet wird (keine Neuzeichnung) - Entfernen ueberschreibt sie nicht
    const nm4 = (E.SAGA_USER4 ? 'Dora' : '');
    const d4 = nm4 ? ((await papi(a, 'GET', '/api/share/targets?q=' + nm4)).d.users || [])[0] : null;
    if (d4) {
      const cur = (await papi(a, 'GET', '/api/account/status')).d.exceptions.map(x => ({ type: 'user', ref: x.ref }));
      const r4 = await papi(a, 'PUT', '/api/account/status/prefs', { exceptions: cur.concat([{ type: 'user', ref: d4.username }]) });
      const live4 = await until(async () => { const v = await usView(a); return v && v.exceptions.length === 2; }, 5000);
      ok('Zweites Geraet: Ausnahme per API dazu, Seite gleicht live ab', r4.status === 200 && live4, { st: r4.status, err: r4.d.error, cur, d4: d4.username, view: (await usView(a) || {}).exceptions });
    } else note('kein viertes Konto - Pruefung "zweites Geraet" uebersprungen');
    w = saveR(); await a.click('#us-reach .us-exc-rm'); await w; await a.waitForTimeout(200);
    const left = (await papi(a, 'GET', '/api/account/status')).d.exceptions;
    ok('Ausnahmen: Entfernen per Knopf (Name nennt die Person)' + (d4 ? ', Ausnahme vom anderen Geraet bleibt' : ''),
       d4 ? left.length === 1 && left[0].ref === d4.username : left.length === 0, left);
    if (d4) await papi(a, 'PUT', '/api/account/status/prefs', { exceptions: [] });
    await closeAll(a);
    await papi(a, 'PUT', '/api/account/status/prefs', { show_call: true, in_call: 'knock', busy: 'show', repeat_s: 180, mention_break: false });

    // --- 12. XSS (Pruefer): Status-Text anderer nur als Text - Kopf (Text + title), Liste (title, sr-only) ---------------------
    const XS = '<img src=x onerror="window.__usx=1">&amp;<b>x</b>';
    await openChat(b, U1);
    await papi(a, 'PUT', '/api/account/status', { state: 'dnd', dur: 3600, source: 'manual', text: XS });
    const xs = await until(async () => { const c = await chip(b); return c.indexOf('<img') >= 0 ? c : null; }, 6000, 100);
    const xr = await b.evaluate(x => { const c = document.querySelector('#ct-sub .us-chip'), it = document.querySelector('#ct-list .ct-item-b');
      return { el: !!document.querySelector('#ct-sub .us-chip img, #ct-sub .us-chip b, #ct-list img[src=x]'), t: c && c.title.indexOf(x) >= 0,
               fired: !!window.__usx, lt: it ? it.title : '' }; }, XS);
    ok('XSS: Status-Text im Kopf woertlich, kein Element, kein Skript', !!xs && xs.indexOf(XS) >= 0 && !xr.el && xr.t && !xr.fired, { xs, xr });
    ok('XSS: Liste zeigt keinen fremden Text im title', xr.lt.indexOf('<img') < 0, xr.lt);
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });

    // --- 13. Aelterer Server ohne userstatus1 (Pruefer, nachgestellt): nichts sichtbar, keine Aufrufe, keine Fehler ----------
    const o = await b.context().newPage(), oErr = [], oReq = [];
    o.on('pageerror', e => oErr.push(e.message));
    o.on('request', r => { if (r.url().indexOf('/api/account/status') >= 0) oReq.push(r.method() + ' ' + r.url()); });
    const strip = async (r, fn) => { const rs = await r.fetch(); let j = {}; try { j = await rs.json(); } catch (e) { return r.fulfill({ response: rs }); } fn(j); return r.fulfill({ response: rs, json: j }); };
    await o.route('**/api/version', r => strip(r, j => { j.features = (j.features || []).filter(f => f !== 'userstatus1'); }));
    await o.route('**/api/chat/me', r => strip(r, j => { j.features = (j.features || []).filter(f => f !== 'userstatus1'); }));
    await o.route('**/api/chat/presence', r => strip(r, j => { delete j.status; }));
    await o.route('**/api/events**', r => r.abort());                 // alter Server schickt kein chat.status
    await papi(a, 'PUT', '/api/account/status', { state: 'dnd', dur: 3600, source: 'manual', text: 'Alt' });
    await o.goto(BASE + '/chats#u' + encodeURIComponent(U1)); await o.waitForSelector('#ct-chat:not([hidden])', { timeout: 30000 }); await o.waitForTimeout(2500);
    await o.click('#hk-user'); await o.waitForSelector('.menu:not([hidden]) .menu-item', { timeout: 5000 });
    const oo = await o.evaluate(() => ({ mi: !!document.querySelector('.menu .us-mi'), chip: !!document.querySelector('.us-chip, .us-badge, #hk-user .us-me') }));
    ok('Alter Server: kein Status-Eintrag, keine Anzeige, kein /api/account/status, keine Fehler', !oo.mi && !oo.chip && !oReq.length && !oErr.length, { oo, oReq, oErr });
    await o.close();
    await papi(a, 'PUT', '/api/account/status', { state: 'available', source: 'manual' });

    // --- 11. Schmale Ansicht: Menue erreichbar ------------------------------------------------------------------------------
    const m = await session(browser, U1, E.SAGA_PASS, { viewport: { width: 390, height: 844 }, scheme: 'light' });
    await chatKeys(m, U1); await m.goto(BASE + '/chats'); await m.waitForTimeout(1500);
    const vis = await m.evaluate(() => { const b = document.getElementById('hk-user'); if (!b) return false; const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(b).visibility !== 'hidden'; });
    await menuOpen(m);
    ok('Schmal: Kontoknopf sichtbar, Status im Menue', vis && !!(await m.$('.menu .us-mi')));
    await m.click('.menu .us-mi'); await m.waitForSelector('.us-dlg');
    ok('Schmal: Dialog passt in die Breite', await m.evaluate(() => document.querySelector('.us-dlg').getBoundingClientRect().right <= window.innerWidth + 1 && document.documentElement.scrollWidth <= window.innerWidth + 1));
    await shot(m, 'status-7-schmal-dialog');
    await closeAll(m); await m.context().close();
    ok('keine Seitenfehler', errs.length === 0, errs);
  } catch (e) {
    console.log(e); res.push(false);
  } finally {
    await hook('/_test/clock', { reset: true });
    await browser.close();
  }
  console.log(`\nBROWSER-STATUS ${BRN} ${res.filter(Boolean).length}/${res.length} - ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
