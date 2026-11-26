// Browser-Reihe MCP mit OAuth (2.6, Bugs-DB 2264): Zustimmungsseite "App verbinden" und Einstellungen "Verbundene Apps".
// Ablauf wie mit dem claude.ai-Connector: Client registrieren (fetch), Autorisierung im Browser -> Anmeldung (Formular)
// -> Zustimmung (ohne Konsolen-/CSP-Fehler, hell/dunkel, en) -> "Erlauben": die Umleitung zur redirect_uri eines
// anderen Origins geht durch (CSP form-action) -> Code gegen Token tauschen -> /mcp einmal benutzen -> Einstellungen ->
// Verbundene Apps (Name, Rechte, zuletzt benutzt; hell/dunkel; Handy-Breite ohne waagrechtes Scrollen) -> Trennen.
// In der Testumgebung (Konto um-anna, UMG_PW): tools/testumgebung/umgebung.sh lauf server <slot> browser_oauth.js
'use strict';
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const E = process.env, BASE = (E.BASE_URL || 'http://127.0.0.1:8080').replace(/\/$/, ''), SHOTS = E.SHOTS || '/tmp/oauth-shots';
const USER = E.OA_USER || 'um-anna', PW = E.UMG_PW;
const RU = 'https://claude.ai/api/mcp/auth_callback';      // nie erreicht: der Browser bekommt die Antwort aus page.route
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info).slice(0, 600))); }
const b64u = (b) => b.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  const reg = await (await fetch(BASE + '/oauth/register', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_name: 'Claude', redirect_uris: [RU], token_endpoint_auth_method: 'none', grant_types: ['authorization_code', 'refresh_token'] }) })).json();
  ok('Client registriert', /^sgc_/.test(reg.client_id || ''), reg);
  const ver = b64u(crypto.randomBytes(48)), ch = b64u(crypto.createHash('sha256').update(ver).digest());
  const authUrl = BASE + '/oauth/authorize?' + new URLSearchParams({ response_type: 'code', client_id: reg.client_id, redirect_uri: RU,
    state: 'br-1', code_challenge: ch, code_challenge_method: 'S256', resource: BASE + '/mcp' });
  const br = await chromium.launch({ channel: E.PW_CHANNEL || 'chromium', headless: true, args: ['--no-proxy-server'] });
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, colorScheme: 'light', locale: 'de', extraHTTPHeaders: { 'Accept-Language': 'de' } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push(String(e)));
  let landed = null;
  // Die Umleitung zur App: page.route faengt Folgen einer Umleitung nicht ab - die Anfrage selbst zeigt, dass Chrome sie
  // abgeschickt hat (CSP form-action liess sie durch); das Netz der Testumgebung erreicht claude.ai nie
  ctx.on('request', (r) => { if (r.url().startsWith(RU)) landed = r.url(); });
  await p.goto(authUrl);
  ok('Ohne Sitzung: Anmeldeseite', p.url().includes('/login?next='), p.url());
  await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PW);
  await Promise.all([p.waitForNavigation(), p.click('button[type=submit]')]);
  ok('Nach der Anmeldung: Zustimmungsseite', /\/oauth\/authorize\?r=/.test(p.url()), p.url());
  const txt = await p.textContent('main');
  ok('Zustimmung nennt Client, Konto, Saga-Adresse, Ruecksprung-Host', txt.includes('„Claude“') && txt.includes('Anna Ahorn') && txt.includes(BASE) && txt.includes('claude.ai'), txt.slice(0, 300));
  const boxes = await p.$$eval('input[name=scope][type=checkbox]', (x) => x.map((i) => i.value));
  ok(`Zustimmung: ${boxes.length} Rechte zum Abwaehlen, kein tel`, boxes.length === 12 && !boxes.some((s) => s.startsWith('tel')), boxes);
  ok('Zustimmung ohne Konsolen-/CSP-Fehler', !errs.length, errs);
  await p.screenshot({ path: path.join(SHOTS, 'oauth-zustimmung-hell.png'), fullPage: true });
  await p.emulateMedia({ colorScheme: 'dark' });
  await p.screenshot({ path: path.join(SHOTS, 'oauth-zustimmung-dunkel.png'), fullPage: true });
  await p.emulateMedia({ colorScheme: 'light' });
  await p.click('details.oa-info summary');
  ok('ⓘ erklaert OAuth/MCP', (await p.textContent('details.oa-info')).includes('OAuth'));
  // Person nimmt "Dateien hochladen" und "Kalender schreiben" weg
  await p.uncheck('input[value="write:files"]'); await p.uncheck('input[value="write:calendar"]');
  await p.click('button[value=allow]', { noWaitAfter: true });
  for (let i = 0; i < 50 && !landed; i++) await p.waitForTimeout(100);
  const back = landed ? new URL(landed) : null;
  ok('Erlauben: Umleitung zur App (anderer Origin) geht durch, mit Code, state, iss', back && /^sgk_/.test(back.searchParams.get('code') || '')
     && back.searchParams.get('state') === 'br-1' && back.searchParams.get('iss') === BASE, landed);
  ok('Erlauben ohne CSP-Fehler (form-action)', !errs.some((e) => /Content Security Policy|form-action/.test(e)), errs);
  errs.length = 0;
  const tok = await (await fetch(BASE + '/oauth/token', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code: back ? back.searchParams.get('code') : '', redirect_uri: RU, code_verifier: ver,
      client_id: reg.client_id, resource: BASE + '/mcp' }) })).json();
  ok('Token: ohne write:files und write:calendar', /^sgo_/.test(tok.access_token || '') && !/write:files|write:calendar/.test(tok.scope) && /write:pages/.test(tok.scope), tok);
  const mr = await fetch(BASE + '/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
    Authorization: 'Bearer ' + tok.access_token }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }) });
  ok('/mcp tools/list mit dem Token', mr.status === 200);
  // englische Fassung der Zustimmung (zweite Anfrage) - nur das Bild
  const p2 = await (await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'en', storageState: await ctx.storageState() })).newPage();
  await p2.goto(authUrl.replace('state=br-1', 'state=br-en'));
  await p2.goto(BASE + '/lang?l=en&next=' + encodeURIComponent(new URL(p2.url()).pathname + new URL(p2.url()).search));
  ok('Zustimmung auf Englisch', (await p2.textContent('main')).includes('wants to access your account'), (await p2.textContent('main')).slice(0, 200));
  await p2.screenshot({ path: path.join(SHOTS, 'oauth-zustimmung-en.png'), fullPage: true });
  await p2.goto(BASE + '/lang?l=de&next=/');
  // Einstellungen -> Verbundene Apps
  await p.goto(BASE + '/?settings=oauth').catch(() => {});
  errs.length = 0;
  await p.goto(BASE + '/?settings=oauth');
  await p.waitForSelector('.hk-set-oauth .oa-app', { timeout: 15000 }).catch(() => {});
  const pane = await p.textContent('.hk-set-oauth').catch(() => '');
  ok('Verbundene Apps: Claude mit Rechten und "zuletzt benutzt"', pane.includes('Claude') && pane.includes('Seiten schreiben') && pane.includes('zuletzt benutzt')
     && !pane.includes('Dateien hochladen') && pane.includes('claude.ai'), pane.slice(0, 400));
  ok('Verbundene Apps: Adresse fuer Claude', (await p.$eval('.hk-set-oauth .hk-tokval', (i) => i.value).catch(() => '')) === BASE + '/mcp');
  ok('Einstellungen ohne Konsolenfehler', !errs.length, errs);
  await p.click('.hk-set-oauth details.oa-how summary').catch(() => {});
  const card = await p.$('.hk-dialog-set');
  await (card || p).screenshot({ path: path.join(SHOTS, 'oauth-verbundene-apps-hell.png') });
  await p.emulateMedia({ colorScheme: 'dark' });
  await (card || p).screenshot({ path: path.join(SHOTS, 'oauth-verbundene-apps-dunkel.png') });
  await p.emulateMedia({ colorScheme: 'light' });
  await p.setViewportSize({ width: 390, height: 844 });
  await p.waitForTimeout(300);
  const wide = await p.evaluate(() => { const b = document.querySelector('.hk-set-oauth'); return b ? b.scrollWidth - b.clientWidth : -1; });
  ok(`Handy-Breite: kein waagrechtes Scrollen (${wide})`, wide >= 0 && wide <= 1, wide);
  await p.setViewportSize({ width: 1280, height: 900 });
  // Trennen
  p.once('dialog', (d) => d.accept());
  await p.click('.hk-set-oauth .oa-app .btn-danger');
  await p.waitForFunction(() => !document.querySelector('.hk-set-oauth .oa-app'), null, { timeout: 8000 }).catch(() => {});
  ok('Trennen: Eintrag weg', !(await p.$('.hk-set-oauth .oa-app')));
  const mr2 = await fetch(BASE + '/mcp', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + tok.access_token },
    body: JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/list' }) });
  ok('Trennen: Token sofort ungueltig (401)', mr2.status === 401, mr2.status);
  await br.close();
  console.log(`\nBrowser-OAuth ${res.filter(Boolean).length}/${res.length}`, res.length && res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(res.length && res.every(Boolean) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
