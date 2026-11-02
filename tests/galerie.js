// Vorlagengalerie (2.5) im Web: leerer Start "Mit einer Vorlage starten", Dialog (Kacheln, Filter, Suche, Pfeiltasten,
// Esc), Befehlspalette, Neue-Seite-Menue, Vorschau, Verwenden -> Startseite offen + Hinweis, jede Vorlage einmal anlegen
// (ausser der Abbruch-Testvorlage), schmal (Handy-Breite), hell/dunkel, Konsole ohne Fehler.
// Server mit SAGA_GALLERY_DIR=tests/gallery_fixtures (oder gallery/). Das Konto CK sollte ohne Seiten starten.
// Wetter-Ort beim Anlegen (gal-fix, nur mit gallery/ = Home-Dashboard): Wetter an (Einstellung per Python, SAGA_PY,
// Standard python3 mit tests/pgshim) gegen einen lokalen Fake-Open-Meteo (kein echter Abruf) -> Dialog fragt nach dem Ort,
// Ort waehlen bzw. Ueberspringen; Wetter aus -> keine Frage. "Anstehendes" zeigt die Zeilen der Vorlage.
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/galerie.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const http = require('http'), path = require('path'), cp = require('child_process');
const ROOT = path.dirname(__dirname);
function py(code) {
  return cp.execFileSync(process.env.SAGA_PY || 'python3', ['-c', 'import auth, json, sys\n' + code], { cwd: ROOT, encoding: 'utf8',
    env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }), stdio: ['ignore', 'pipe', 'pipe'] }).trim();
}
// Fake-Open-Meteo: Vorhersage + Ortssuche (nur 127.0.0.1)
const WX = { hits: 0, geo: 0 };
const wxSrv = http.createServer((rq, rs) => {
  const u = new URL(rq.url, 'http://x'); const t0 = new Date();
  let body = {};
  if (u.pathname === '/v1/search') { WX.geo++; body = { results: [{ name: 'Hamburg', admin1: 'Hamburg', country: 'Deutschland', latitude: 53.5507, longitude: 9.993 }] }; }
  else if (u.pathname === '/v1/forecast') {
    WX.hits++;
    const days = [...Array(8)].map((_, i) => new Date(t0.getTime() + i * 864e5).toISOString().slice(0, 10));
    body = { current: { temperature_2m: 12.3, weather_code: 3, wind_speed_10m: 14 }, daily: { time: days, weather_code: [3, 3, 3, 3, 3, 3, 3, 3],
      temperature_2m_max: [13, 14, 15, 16, 17, 18, 19, 20], temperature_2m_min: [6, 7, 8, 9, 10, 11, 12, 13], precipitation_probability_max: [0, 0, 0, 0, 0, 0, 0, 0] } };
  } else { rs.writeHead(404); return rs.end('{}'); }
  rs.writeHead(200, { 'Content-Type': 'application/json' }); rs.end(JSON.stringify(body));
});
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
const BG = {}; const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource.*(404|403)/.test(m.text())) errs.push(m.text()); });
  await p.goto(BASE + '/notes');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  const list = (await api('GET', '/api/gallery')).items || [];
  const usable = list.filter((x) => x.id !== 'abbruch');
  ok(`Galerie hat Vorlagen (${list.map((x) => x.id).join(', ')})`, usable.length > 0, list);
  const tiles = () => p.evaluate(() => [...document.querySelectorAll('#pg-modal .gal-tile')].map((x) => x.dataset.id));
  const modalOpen = () => p.evaluate(() => !document.getElementById('pg-modal').hidden && !!document.querySelector('#pg-modal .gal'));

  // --- leerer Start --------------------------------------------------------------------------------------------
  const mine = await api('GET', '/api/notes');
  if (!mine.length) {
    const vis = await until(() => p.evaluate(() => { const b = document.getElementById('pg-empty-gal'); return b && !b.hidden && b.offsetParent !== null; }));
    ok('Leerer Start: "Mit einer Vorlage starten" sichtbar', vis);
    await p.click('#pg-empty-gal');
  } else {
    ok('Konto hat schon Seiten - leerer Start uebersprungen', true);
    await p.click('#pg-new'); await sleep(300);
    await p.locator('.menu-item', { hasText: 'Aus der Galerie' }).click();
  }
  await until(async () => (await tiles()).length);
  let ids = await tiles();
  ok(`Dialog mit Kacheln (${ids.join(', ')})`, await modalOpen() && ids.length === list.length);
  ok('Suche hat den Fokus', await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('gal-q')));
  // Filter
  const cats = [...new Set(list.map((x) => x.category))];
  const c0 = cats[0], names = { personal: 'Persönlich', work: 'Arbeit', team: 'Team' };
  await p.locator('#pg-modal .gal-cats .chip', { hasText: names[c0] }).click(); await sleep(150);
  ids = await tiles();
  ok(`Filter ${names[c0]}: ${ids.join(', ')}`, ids.length === list.filter((x) => x.category === c0).length
     && await p.locator('#pg-modal .gal-cats .chip.on').getAttribute('aria-pressed') === 'true');
  await p.locator('#pg-modal .gal-cats .chip', { hasText: 'Alle' }).click(); await sleep(150);
  ok('Filter Alle', (await tiles()).length === list.length);
  // Suche
  await p.fill('#pg-modal .gal-q', usable[0].title.slice(0, 6)); await sleep(150);
  ids = await tiles();
  ok(`Suche "${usable[0].title.slice(0, 6)}": ${ids.join(', ')}`, ids.indexOf(usable[0].id) >= 0 && ids.length <= list.length);
  await p.fill('#pg-modal .gal-q', 'zzzzqqq'); await sleep(150);
  ok('Suche ohne Treffer: Hinweis', (await tiles()).length === 0 && await p.locator('#pg-modal .gal-grid .nt-empty').count() === 1);
  await p.fill('#pg-modal .gal-q', ''); await sleep(150);
  // Tastatur: Pfeil runter aus der Suche -> erste Kachel, Pfeil rechts -> zweite, Enter -> Vorschau
  await p.focus('#pg-modal .gal-q'); await p.keyboard.press('ArrowDown');
  const f1 = await p.evaluate(() => document.activeElement.dataset.id);
  await p.keyboard.press('ArrowRight');
  const f2 = await p.evaluate(() => document.activeElement.dataset.id);
  ok(`Pfeiltasten: ${f1} -> ${f2}`, f1 === (await tiles())[0] && (list.length < 2 || f2 === (await tiles())[1]));
  await p.keyboard.press('Enter');
  ok('Enter oeffnet die Vorschau', !!(await until(() => p.locator('#pg-modal .gal-pv').count())));
  await p.keyboard.press('Escape'); await sleep(200);
  ok('Esc schliesst den Dialog', !(await modalOpen()));

  // --- Befehlspalette -------------------------------------------------------------------------------------------
  await p.keyboard.press('Control+k'); await sleep(300);
  await p.keyboard.type('>Vorlagengalerie', { delay: 20 }); await sleep(300);
  await p.keyboard.press('Enter');
  ok('Befehlspalette: "Vorlagengalerie" oeffnet den Dialog', !!(await until(async () => (await modalOpen()) && (await tiles()).length)));

  // --- Vorschau und Verwenden (erste Vorlage) ---------------------------------------------------------------------
  const first = usable.find((x) => x.id === 'alles') || usable[0];
  await p.click(`#pg-modal .gal-tile[data-id="${first.id}"]`);
  await until(() => p.locator('#pg-modal .gal-use').count());
  const pv = await p.evaluate(() => ({ title: (document.querySelector('.gal-pv-title') || {}).textContent, n: document.querySelectorAll('.gal-pv > *').length,
    contents: [...document.querySelectorAll('.gal-contents li')].map((x) => x.textContent), ex: document.querySelector('.gal-act input[type=checkbox]').checked,
    region: document.querySelector('.gal-pv').getAttribute('aria-label') }));
  ok(`Vorschau: ${pv.title}, ${pv.n} Elemente, Enthaelt ${pv.contents.length}`, pv.title && pv.n > 1 && pv.contents.length === first.contents.length && pv.ex && pv.region);
  // alle Hinweise mitschreiben: die Testvorlage "alles" hat eine Datenbank "Aufgaben" ohne Aufgaben-Zuordnung - deren
  // einmaliger Hinweis ("sieht nach Aufgaben aus", pages.js taskHint) darf "Vorlage angelegt" danach ersetzen.
  // Aufrufe von HK.toast direkt mitschreiben: ein MutationObserver meldet gesammelt in einer Microtask - laufen beide
  // Hinweise vorher (beide in Promise-Ketten), saehe er nur noch den letzten Text (zeitabhaengig 19/20)
  await p.evaluate(() => { window.__toasts = []; const orig = window.HK.toast;
    window.HK.toast = function (msg) { window.__toasts.push(String(msg)); return orig.apply(this, arguments); }; });
  await p.click('#pg-modal .gal-use');
  const opened = await until(() => p.evaluate(() => document.getElementById('pg-modal').hidden && /^#\d+$/.test(location.hash) && document.getElementById('pg-title') &&
    document.getElementById('pg-title').value.trim()), 10000);
  ok(`Verwenden: Startseite offen (${opened})`, opened === first.title || (opened && opened.length > 0));
  const toast = await until(() => p.evaluate(() => (window.__toasts || []).find((x) => /Vorlage angelegt/.test(x))));
  ok(`Hinweis "Vorlage angelegt" (${toast})`, /Vorlage angelegt/.test(toast || ''));
  await sleep(1500);

  // --- Neue-Seite-Menue und jede weitere Vorlage einmal (ohne Beispiele) ---------------------------------------------
  for (const x of usable.filter((y) => y.id !== first.id)) {
    await p.click('#pg-new'); await sleep(250);
    const mi = p.locator('.menu-item', { hasText: 'Aus der Galerie' });
    if (!(await mi.count())) { ok('Neue-Seite-Menue: "Aus der Galerie …"', false); break; }
    await mi.click();
    await until(async () => (await tiles()).length);
    await p.click(`#pg-modal .gal-tile[data-id="${x.id}"]`);
    await until(() => p.locator('#pg-modal .gal-use').count());
    await p.uncheck('#pg-modal .gal-act input[type=checkbox]');
    const before = await p.evaluate(() => location.hash);
    await p.click('#pg-modal .gal-use');
    const t = await until(() => p.evaluate((b) => document.getElementById('pg-modal').hidden && location.hash !== b && document.getElementById('pg-title').value.trim(), before), 10000);
    ok(`${x.id}: angelegt und geoeffnet (${t})`, !!t);
    await sleep(1200);
  }

  // --- gal-fix: Wetter-Ort beim Anlegen (Home-Dashboard) -----------------------------------------------------------
  if (usable.some((x) => x.id === 'home-dashboard')) {
    await new Promise((r) => wxSrv.listen(0, '127.0.0.1', r));
    const base = 'http://127.0.0.1:' + wxSrv.address().port;
    let prevHosts = '';
    const openTile = async () => {
      await p.click('#pg-new'); await sleep(250);
      await p.locator('.menu-item', { hasText: 'Aus der Galerie' }).click();
      await until(async () => (await tiles()).length);
      await p.click('#pg-modal .gal-tile[data-id="home-dashboard"]');
      await until(() => p.locator('#pg-modal .gal-use').count());
    };
    const opened = async (before) => until(() => p.evaluate((b) => document.getElementById('pg-modal').hidden && location.hash !== b && /^#\d+$/.test(location.hash) ? +location.hash.slice(1) : 0, before), 10000);
    const wcfg = async (id) => { const n = await api('GET', '/api/notes/' + id); const f = (bl) => bl.reduce((a, b) => a.concat([b], (b.data && b.data.cols || []).reduce((x, c) => x.concat(f(c.blocks || [])), [])), []);
      return f(JSON.parse(n.body).blocks).filter((b) => b.type === 'widget'); };
    try {
      prevHosts = py("print(auth.settings(fresh=True).get('allowed_hosts') or '')");
      py(`auth.save_setting('allowed_hosts', ${JSON.stringify((prevHosts ? prevHosts + ', ' : '') + '127.0.0.1:' + wxSrv.address().port)})
auth.save_setting('weather', {'enabled': True, 'base_url': '${base}/v1/forecast', 'geo_url': '${base}/v1/search', 'api_key': ''})`);
      await sleep(5600);                                   // Einstellungen sind im Server 5 s zwischengespeichert
      // 1) Wetter an: Frage nach dem Ort, Ort waehlen
      await openTile();
      let before = await p.evaluate(() => location.hash);
      await p.click('#pg-modal .gal-use');
      const ask = await until(() => p.locator('#pg-modal .gal-wx .gal-wx-q').count());
      ok('Wetter an: Dialog fragt nach dem Ort (Suchfeld hat den Fokus)', ask && await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('gal-wx-q'))
         && await p.locator('#pg-modal .gal-use').isHidden());
      await p.fill('#pg-modal .gal-wx-q', 'Hamburg'); await p.keyboard.press('Enter');
      const hit = await until(() => p.locator('#pg-modal .gal-wx-hits .menu-item').count());
      ok(`Ortssuche ueber den Server (Fake, ${WX.geo} Abruf)`, hit && WX.geo >= 1);
      await p.locator('#pg-modal .gal-wx-hits .menu-item').first().click();
      let id = await opened(before);
      const w1 = (await wcfg(id)).find((b) => b.data.kind === 'weather');
      ok(`Ort ins Widget geschrieben (${JSON.stringify(w1 && w1.data.cfg)})`, w1 && w1.data.cfg.place === 'Hamburg' && w1.data.cfg.lat === 53.55 && w1.data.cfg.lon === 9.99);
      const wt = await until(async () => { const t = await p.locator('.wg[data-kind="weather"]').textContent().catch(() => ''); return /12/.test(t) ? t : null; }, 10000);
      ok(`Wetter-Widget zeigt Werte vom Fake (${(wt || '').slice(0, 60)})`, !!wt && !/eingerichtet/.test(wt));
      // "Anstehendes" zeigt die Zeilen der Vorlage (Termine/Aufgaben; erwartet = was der Server liefert)
      const up = await until(async () => { const r = await api('GET', '/api/notes/' + id + '/widgets'); const w = Object.values(r.widgets || {}).find((x) => x.kind === 'upcoming'); return w && w.data ? w : null; }, 6000);
      const rows = ((up && up.data.items) || []).filter((x) => x.k === 'row').map((x) => x.title);
      const dom = await until(async () => { const t = await p.locator('.wg[data-kind="upcoming"]').textContent().catch(() => ''); return rows.every((r) => t.indexOf(r) >= 0) ? t : null; }, 8000);
      ok(`Anstehendes zeigt die Vorlagen-Zeilen (${rows.join(', ')})`, up && !up.error && up.data.errors === 0 && !!dom, up);
      await sleep(800);
      // 2) Ueberspringen: Widget bleibt ohne Ort
      await openTile();
      before = await p.evaluate(() => location.hash);
      await p.click('#pg-modal .gal-use');
      await until(() => p.locator('#pg-modal .gal-wx-skip').count());
      await p.click('#pg-modal .gal-wx-skip');
      id = await opened(before);
      const w2 = (await wcfg(id)).find((b) => b.data.kind === 'weather');
      const wt2 = await until(async () => { const t = await p.locator('.wg[data-kind="weather"]').textContent().catch(() => ''); return /eingerichtet/.test(t) ? t : null; }, 8000);
      ok(`Ueberspringen: Widget ohne Ort, Hinweis wie bisher (${wt2})`, w2 && w2.data.cfg.lat == null && !!wt2);
      await sleep(800);
    } catch (e) { ok('Wetter-Ort: ' + e.message, false); }
    finally {
      try { py(`auth.save_setting('allowed_hosts', ${JSON.stringify(prevHosts)})
from db import connect
with connect() as c, c:
    c.execute("DELETE FROM setting WHERE k='weather'")
auth._settings_cache['v'] = None`); } catch (e) { ok('Einstellungen zuruecksetzen', false, e.message); }
      wxSrv.close();
    }
    // 3) Wetter aus: keine Frage
    await sleep(5600);
    await openTile();
    const before = await p.evaluate(() => location.hash);
    await p.click('#pg-modal .gal-use');
    const id3 = await opened(before);
    ok('Wetter aus: ohne Frage angelegt', !!id3 && !(await p.locator('.gal-wx').count()));
    await sleep(800);
  } else ok('Wetter-Ort: Home-Dashboard nicht im Galerie-Ordner - uebersprungen', true);

  // --- schmal, hell/dunkel ---------------------------------------------------------------------------------------
  for (const scheme of ['light', 'dark']) {
    await p.emulateMedia({ colorScheme: scheme });
    await p.setViewportSize({ width: 390, height: 800 });
    await p.keyboard.press('Control+k'); await sleep(250); await p.keyboard.type('>Vorlagengalerie'); await sleep(250); await p.keyboard.press('Enter');
    await until(async () => (await tiles()).length);
    const g = await p.evaluate(() => {
      const box = document.querySelector('#pg-modal .modal-box'), tile = document.querySelector('.gal-tile');
      return { over: box.scrollWidth - box.clientWidth, w: box.getBoundingClientRect().width, bg: getComputedStyle(tile).backgroundColor, fg: getComputedStyle(tile).color };
    });
    await p.click(`#pg-modal .gal-tile[data-id="${first.id}"]`); await until(() => p.locator('#pg-modal .gal-use').count());
    const d = await p.evaluate(() => { const b = document.querySelector('.gal-body'); return { cols: getComputedStyle(b).gridTemplateColumns.split(' ').length, over: document.querySelector('#pg-modal .modal-body').scrollWidth - document.querySelector('#pg-modal .modal-body').clientWidth }; });
    ok(`${scheme}, 390 px: kein seitliches Scrollen (${g.over}/${d.over}), Vorschau einspaltig (${d.cols}), Farben ${g.bg} / ${g.fg}`, g.over <= 1 && d.over <= 1 && d.cols === 1 && g.bg !== g.fg);
    BG[scheme] = g.bg;
    await p.keyboard.press('Escape'); await sleep(200);
  }
  ok("hell und dunkel unterscheiden sich", BG.light !== BG.dark, BG);
  await p.setViewportSize({ width: 1280, height: 900 });

  ok('Keine Seitenfehler / Konsolenfehler', !errs.length, errs);
  // aufraeumen: alle eigenen Seiten in den Papierkorb, Papierkorb leeren
  for (const n of (await api('GET', '/api/notes')).filter((x) => x.parent_id == null)) await api('DELETE', '/api/notes/' + n.id);
  await api('POST', '/api/trash/empty');
  await br.close();
  const g = res.filter(Boolean).length;
  console.log('\nGALERIE-WEB ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
