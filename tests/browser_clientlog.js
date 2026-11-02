// Browser-Test Fehlerberichte und "Diagnose senden" (2.5, static/clientlog.js, Feature clientlog1).
//   SAGA_BASE=http://127.0.0.1:8451 SAGA_USER=konto SAGA_PASS=... node tests/browser_clientlog.js
// Prueft: geworfener Fehler + abgelehnte Promise -> genau EIN (gedrosselter) Bericht mit beiden Eintraegen, ohne
// Seiteninhalt (Titel, E-Mail, Adresse); Wiederholung wird gezaehlt (n), weitere Fehler warten auf die Drossel (30 s) und
// gehen beim Verlassen mit; Fehler im Logger bzw. im Versand -> keine Schleife; Feature fehlt -> kein Versand;
// Einstellungen -> Hilfe -> "Diagnose senden" -> Kennung D-XXXXX sichtbar, Ringpuffer ohne Inhalte; CSP-Verletzungen 0.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 600))); }
const TITLE = 'Probe Kuchenplan ' + Math.random().toString(36).slice(2, 7), MAIL = 'probe.person@example.org', URLP = 'https://cloud.example.org/s/geheim?x=1';
const PROBES = [TITLE, 'Kuchenplan', MAIL, 'example.org', 'geheim'];

async function session(browser, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 820 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  p.csp = []; p.posts = []; p.diags = [];
  p.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) p.csp.push(m.text().slice(0, 200)); });
  ctx.on('request', (r) => {
    if (r.method() !== 'POST') return;
    if (/\/api\/client\/log$/.test(r.url())) p.posts.push({ at: Date.now(), body: r.postData() || '', keepalive: true });
    if (/\/api\/client\/diag$/.test(r.url())) p.diags.push(r.postData() || '');
  });
  if (opts.noFeature) {
    await ctx.route('**/api/version', async (route) => {
      const r = await route.fetch(); const j = await r.json();
      j.features = (j.features || []).filter((f) => f !== 'clientlog1');
      await route.fulfill({ response: r, json: j });
    });
  }
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USER);
  await p.fill('input[type=password]', PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(800);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  return p;
}
function throwBoth(p) {
  return p.evaluate(([title, mail, url]) => {
    // Fehler aus einem Ereignis (laeuft ausserhalb von evaluate -> window.onerror) und eine nicht behandelte Ablehnung
    setTimeout(function seiteLaden() { throw new Error('Seite "' + title + '" fuer ' + mail + ' nicht geladen: ' + url + ' id 4711'); }, 0);
    setTimeout(function () { Promise.reject(new TypeError('Cannot read properties of undefined (reading \'' + 'title' + '\') ' + title)); }, 5);
  }, [TITLE, MAIL, URLP]);
}
const FS = require('fs'), LOG = process.env.STATE_DIR ? process.env.STATE_DIR + '/server.log' : '';
function logLen() { try { return LOG ? FS.statSync(LOG).size : 0; } catch (e) { return 0; } }
function logSince(n) { try { return LOG ? FS.readFileSync(LOG).subarray(n).toString('utf8').split('\n') : []; } catch (e) { return []; } }
const hasProbe = (s) => PROBES.filter((x) => s.toLowerCase().includes(x.toLowerCase()));

async function main() {
  const browser = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || undefined, headless: true });
  try {
    // --- Fehler + Ablehnung -> ein Bericht ------------------------------------------------------------------------
    const p = await session(browser);
    const nid = (await p.api('POST', '/api/notes', { title: TITLE })).id;
    await p.goto(BASE + '/notes#' + nid); await p.waitForTimeout(1500);
    ok('Logger geladen (window.HKLog) vor den anderen Skripten', await p.evaluate(() => !!window.HKLog && [...document.scripts].findIndex((s) => /clientlog\.js/.test(s.src)) === 0));
    await throwBoth(p); await p.waitForTimeout(150); await throwBoth(p);         // zweimal derselbe Fehler -> n = 2
    await p.waitForTimeout(3500);
    ok('genau ein Bericht nach Fehler + Ablehnung (' + p.posts.length + ')', p.posts.length === 1, p.posts.map((x) => x.body.slice(0, 200)));
    const b = p.posts[0] ? JSON.parse(p.posts[0].body) : {};
    const kinds = (b.items || []).map((x) => x.k).sort();
    ok('Bericht: beide Arten (error, unhandled), dedupliziert mit n', JSON.stringify(kinds) === '["error","unhandled"]' && (b.items || []).every((x) => x.n === 2), b.items);
    ok('Bericht: kein Seiteninhalt (Titel, E-Mail, Adresse)', p.posts[0] && !hasProbe(p.posts[0].body).length, hasProbe(p.posts[0] ? p.posts[0].body : ''));
    const er = (b.items || []).find((x) => x.k === 'error') || {};
    ok('Bericht: Kopf und Ort (app web, sid, screen pages, Stack static/…:Zeile:Spalte, keine Zahl aus dem Text)',
      b.app === 'web' && /^[A-Za-z0-9]{16}$/.test(b.sid || '') && er.screen === 'pages' && !/4711/.test(er.msg || '')
      && /<url>/.test(er.msg || '') && /<email>/.test(er.msg || ''), { b: { app: b.app, sid: b.sid, os: b.os, ver: b.ver }, er });
    // --- Hostnamen mit beliebiger Endung und Dateinamen (wie saga/clientlog.py) -----------------------------------------
    const hs = await p.evaluate(() => [
      'Error: Hostname cloud.mueller.family not verified', 'getaddrinfo ENOTFOUND nas.fritz.box.', 'Error: router.lan:443',
      'Error: xn--mller-kva.example', 'Error: Hochladen: Arzt_Brief.pdf (50 %)',
      'TypeError: e.target.value is undefined', 'org.json.JSONException: x', 'at saveNow (static/pages.js:120:15)', 'Error: pm-editor.js not loaded'
    ].map((m) => window.HKLog.scrub(m)));
    ok('Bereinigung im Browser: Hostnamen -> <host>, Dateien -> <file>', hs.slice(0, 4).every((x) => /<host>/.test(x) && !/mueller|fritz|router|xn--/.test(x))
      && /<file>/.test(hs[4]) && !/Arzt|pdf/.test(hs[4]), hs.slice(0, 5));
    ok('Bereinigung im Browser: Bezeichner und Skripte bleiben', hs[5] === 'TypeError: e.target.value is undefined' && hs[6] === 'org.json.JSONException: x'
      && hs[7] === 'at saveNow (static/pages.js:120:15)' && hs[8] === 'Error: pm-editor.js not loaded', hs.slice(5));
    // --- Drossel: neuer Fehler wartet, geht beim Verlassen mit ---------------------------------------------------------
    const log0 = logLen();
    await p.evaluate(() => { setTimeout(function spaeter() { throw new RangeError('maximum call stack size exceeded'); }, 0); });
    await p.waitForTimeout(3000);
    ok('weiterer Fehler innerhalb von 30 s: noch nicht gesendet (Drossel)', p.posts.length === 1, p.posts.length);
    await p.goto(BASE + '/'); await p.waitForTimeout(1500);
    // keepalive-Anfragen aus dem entladenen Dokument sieht Playwright nicht immer - dann zaehlt die Zeile im Server-Log
    const inLog = logSince(log0).filter((l) => l.startsWith('clientlog ') && l.includes(b.sid || '-') && /RangeError: maximum call stack size exceeded/.test(l)).length;
    ok('beim Verlassen der Seite geht der Rest mit (keepalive)', (p.posts.length === 2 && /RangeError: maximum call stack size exceeded/.test(p.posts[1].body)) || inLog === 1,
      { posts: p.posts.length, inLog, log: LOG || 'STATE_DIR nicht gesetzt' });
    // --- keine Schleife -----------------------------------------------------------------------------------------------
    const before = p.posts.length;
    await p.route('**/api/client/log', (r) => r.fulfill({ status: 500, body: 'x' }));
    await p.evaluate(() => {
      // Fehler, deren stack-Getter wirft (Logger darf nicht selbst stolpern), ein Fehler "aus clientlog.js" und eine
      // Ablehnung mit Getter, der wirft
      const e1 = new Error('stack wirft'); Object.defineProperty(e1, 'stack', { get() { throw new Error('getter'); } });
      setTimeout(() => { throw e1; }, 0);
      const e2 = new Error('eigener Fehler'); e2.stack = 'Error: eigener Fehler\n    at x (' + location.origin + '/static/clientlog.js?v=1:10:5)';
      setTimeout(() => { throw e2; }, 0);
      const bad = {}; Object.defineProperty(bad, 'message', { get() { throw new Error('boese'); } });
      setTimeout(() => { Promise.reject(bad); }, 0);
      // Logger kaputtmachen: JSON.stringify wirft beim Versand
      const js = JSON.stringify; window.__js = js;
      JSON.stringify = function (v) { if (v && v.items) throw new Error('kaputt'); return js.apply(JSON, arguments); };
    });
    await p.waitForTimeout(2500);
    const errs = await p.evaluate(() => { JSON.stringify = window.__js; return window.HKLog._pending().map((x) => x.msg); });
    ok('Fehler im Logger: keine Schleife, eigener Fehler nicht gesammelt (' + errs.length + ' wartend)',
      errs.length <= 3 && !errs.some((m) => /eigener Fehler|kaputt|getter|boese/.test(m)), errs);
    ok('Versand scheitert (500): kein erneuter Versuch in Folge', p.posts.length - before <= 1, p.posts.length - before);
    await p.unroute('**/api/client/log');
    ok('CSP: keine Verletzungen (' + p.csp.length + ')', p.csp.length === 0, p.csp);

    // --- Diagnose senden ----------------------------------------------------------------------------------------------
    await p.goto(BASE + '/notes#' + nid); await p.waitForTimeout(1500);
    await p.evaluate(() => { HKLog.ev('save', { st: 'conflict', s: 409 }); setTimeout(() => { throw new Error('Diagnose-Probe'); }, 0); });
    await p.waitForTimeout(300);
    await p.click('#hk-user'); await p.click('.menu-item:has-text("Einstellungen")');
    await p.click('#hk-tab-help'); await p.waitForTimeout(600);
    ok('Einstellungen: Reiter Hilfe mit "Diagnose senden"', await p.locator('#hk-pane-help button:has-text("Diagnose senden")').isVisible());
    await p.fill('.hk-diag-note', 'Beim Speichern hing es kurz.');
    await p.click('#hk-pane-help button:has-text("Diagnose senden")');
    await p.waitForSelector('.hk-diag-id', { timeout: 8000 }).catch(() => {});
    const id = await p.locator('.hk-diag-id').textContent().catch(() => '');
    ok('Diagnose gesendet: Kennung D-XXXXX sichtbar (' + id + ')', /^D-[2-9A-Z]{5}$/.test(id || ''));
    const d = p.diags[0] ? JSON.parse(p.diags[0]) : {};
    const kinds2 = [...new Set((d.ev || []).map((x) => x.k))];
    ok('Diagnose: Ringpuffer mit Fehlern, Netz (Muster), Ansicht, Speichern, Freitext', ['err', 'net', 'view', 'save'].every((k) => kinds2.includes(k))
      && (d.ev || []).some((x) => x.k === 'net' && x.p === '/api/notes/:id') && d.note === 'Beim Speichern hing es kurz.', { kinds2, note: d.note });
    ok('Diagnose: keine Inhalte im Ringpuffer', p.diags[0] && !hasProbe(p.diags[0]).length && !/#\d|\/notes\/\d/.test(p.diags[0]), hasProbe(p.diags[0] || ''));
    ok('CSP (Einstellungen): keine Verletzungen', p.csp.length === 0, p.csp);
    await p.api('DELETE', '/api/notes/' + nid);
    await p.context().close();

    // --- ohne Feature: kein Versand -----------------------------------------------------------------------------------
    const q = await session(browser, { noFeature: true });
    await q.goto(BASE + '/notes'); await q.waitForTimeout(1200);
    await throwBoth(q); await q.waitForTimeout(3500);
    await q.goto(BASE + '/'); await q.waitForTimeout(1200);
    ok('Feature fehlt: kein Bericht (' + q.posts.length + ')', q.posts.length === 0, q.posts.length);
    await q.click('#hk-user'); await q.click('.menu-item:has-text("Einstellungen")');
    await q.click('#hk-tab-help'); await q.waitForTimeout(800);
    ok('Feature fehlt: Hilfe zeigt "abgeschaltet", kein Knopf', await q.locator('#hk-pane-help:has-text("abgeschaltet")').isVisible()
      && !(await q.locator('#hk-pane-help button:has-text("Diagnose senden")').count()));
    ok('Feature fehlt: keine Diagnose gesendet', q.diags.length === 0);
    await q.context().close();
  } finally {
    await browser.close();
  }
  console.log('\nBROWSER-CLIENTLOG ' + res.filter(Boolean).length + '/' + res.length + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
}
main().catch((e) => { console.error(e); process.exit(1); });
