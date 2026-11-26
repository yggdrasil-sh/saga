// 2.6 (Bugs-DB 1530): eigenes Logo bei Lesezeichen im Browser. Einstellungen -> Lesezeichen -> "+ Lesezeichen":
// Bild hochladen (Vorschau, Feld Symbol gesperrt), Hinzufuegen -> Liste und Heute-Kachel zeigen das Logo (PNG ueber
// /api/bookmarks/<id>/logo); zu grosse Datei wird schon im Browser abgewiesen (kein Upload); Enter auf "Bild hochladen"
// oeffnet die Dateiauswahl statt zu speichern; Ersetzen/Entfernen. SHOTS=<ordner>: Bilder (Formular, Liste, Heute);
// VORHER=1: nur Bilder des alten Stands (ohne Logo-Zeile). Lokal: HK_TEST_BASE=... CK=<Cookie hk-test-a> node tests/lesezeichen_logo_web.js
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, SHOTS = process.env.SHOTS || '', VORHER = process.env.VORHER === '1';
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(150); } }
const LOGO = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAYAAABccqhmAAAFaUlEQVR42u3doVobURAG0Lvzpe+BRtT1HSpbg0Yha/AIPKYShcYU2Xeoq6jue1RQQw0IUgLJ3vnPkYAIm5n/zt3dbMYAAAAAAAAAALpYuvwjR6e3995O9un3zckiADQ7TBsKi4aH3EBYND7kBsGi6SE3DBaND7lBsGh8yA2C0vyQuwguGh9yp4HS/JA7DZTmh9wQWDQ+5G4JSvND7jRQmh9yQ6A0P+SGQGl+yA2B0vyQGwKl+SE3BMrhhFxl9YfcKaA0P+SGQGl+yA0B5wDAOQCrPyROAaX5ITcEbAHAFsDqD4lTgAkATABWf0icAkwAYAIABIDxH6K2ASYAMAFY/SFxCjABgAkAEACAALD/h4zzACYAMAEAAgAQAIAAGGM4AQij8YlAEwAE23T8p358/eSd5U18+HInADQ+qf7VWJcg2Gh8yA2C0vyQW3/l4ENuHZaDDrn16DIgDDcCSVsIrEsTAJgApCwk1qcJAEwAwPBZAJjD518XT3727fjSgREApDX+498JAlsAwpr/JX+HAKBZ8wsBAUB48wsB5wC2dnV9pwoenJ+5x8IEAA1Wf1OAAAAEACAAAAEACAAmsetdfe4KHC4DDpe+wARA1hRg9RcAhIaA5hcAhIaA5ncOgOYh4HkAAgBBgC0AIACA/gHQ7fvZ6WW2+jQBgAlAykJiXZoAwAQgbSGxHstBh9w6LAcfcutv0+lN8M3BaPzgW4EFARrfZwFsC8BlQEAAAAIAEACAAAAEAAgAQAAAAgAQAIAAAAQAIAAAAQAIAEAAAAIAEADA8Eiwg/JMQIbHzeUFgMZnXzXmqcAaH0EwfRCU5ofc+isHH3LrsBx0yK1HlwFhuA9A2kJgXZoAwAQgZSGxPk0AYAIAhs8CwBw+/7p48rNvx5cOjAAgrfEf/04Q2AIQ1vwv+TsEAM2aXwgIAMKbXwg4B7C1q+s7VfDg/Mw9FiYAaLD6mwIEACAAAAEACABAADCJXe/qc1fgcBlwuPQFJgCypgCrvwAgNAQ0vwAgNAQ0v3MANA8BzwMQAAgCbAEAAQD0D4Bu389OL7PVpwkATABSFhLr0gQAJgBpC4n1WA465NZhOfiQW3+bTm+Cbw5G4wffCiwI0Pg+C2BbAC4DAgIAEACAAAAEACAAQAAAAgAQAIAAAAQAIAAAAQAIAEAAAAIAEACAAACGR4IdlGcCMjxuLi8AND77qjFPBdb4CILpg6A0P+TWXzn4kFuH5aBDbj26DAjDfQDSFgLr0gQAJgApC4n1aQIAEwAwfBYA5vDu+/snP/vz8acDIwBIa/zHvxMEtgCENf9L/g4BQLPmFwICgPDmFwLOAWzt6vpOFTw4P3OPhQkAGqz+pgABAAgAQAAAAgAQAExi17v63BU4XAYcLn2BCYCsKcDqLwAIDQHNLwAIDQHN7xwAzUPA8wAEAIIAWwBAAAD9A6Db97PTy2z1aQIAE4CUhcS6fDYAft+cLN5amNNz/VvSFnLrsRx0yK3DcvAht/42nd4E3xyMxv8/W5/gOzq9vR++ohmNP7qcAGz7WQDbAnAjECAAgJ0DwA1BMFrt/00AYAJ4/VQB1r/6mwDABAAIANsAiBr/TQBgAnj7lAHWt/qbAMAEsL+0Adaz+u88AQgBmLf5bQHAFuBw6QMctv9qDS8COEzf1ZpeDLDffnMOAJwDMAVA2ur/JhOAEIA5mv/NtgBCAOboq5rpxYLmn+h5AEIA1t1HNfOLB80/9vPNQK9hpm8Xgs6Nf5D7AEwDsK4+qc7/HGj+FW0BbAlgXQtjJf7ToPlXMAGYBtD4h7W6FVgQoPGDA0AYoOkFgCBA4wsAgYCGFwBCAc0OAAAAAAAAsI2/O0f/ZEG7bOUAAAAASUVORK5CYII=', 'base64');
(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE', colorScheme: 'light' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [], uploads = [];
  p.on('pageerror', (e) => errs.push(e.message));
  p.on('request', (q) => { if (q.method() === 'POST' && new URL(q.url()).pathname === '/api/files') uploads.push(q.url()); });
  await p.goto(BASE + '/dashboard');
  const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, url, b]);
  await api('POST', '/api/first-steps', { answer: 'no' });
  for (const b of await api('GET', '/api/bookmarks')) await api('DELETE', '/api/bookmarks/' + b.id);
  await api('POST', '/api/bookmarks', { name: 'Wiki', url: 'https://wiki.example.org', category: 'Haus' });
  const made = [];
  try {
    await p.goto(BASE + '/dashboard?t=' + Date.now());
    await p.click('button[data-settings="bookmarks"]');
    const sec = p.locator('.hk-set-bookmarks');
    await sec.waitFor();
    await sec.locator('button', { hasText: '+ Lesezeichen' }).click();
    const form = sec.locator('.hk-cal-form');
    await form.waitFor();
    await form.locator('input[type=url]').fill('https://nas.example.org');
    await form.locator('label:has-text("Name") input').fill('NAS');
    await form.locator('label:has-text("Kategorie") input').fill('Haus');
    await sleep(500);
    if (VORHER) {
      if (SHOTS) await sec.screenshot({ path: SHOTS + '/vorher-formular.png' });
      await form.locator('button', { hasText: 'Hinzufügen' }).click();
      await until(() => sec.locator('.hk-bm-row', { hasText: 'NAS' }).count());
      await sleep(600);
      if (SHOTS) await sec.screenshot({ path: SHOTS + '/vorher-liste.png' });
    } else {
      const row = form.locator('.hk-bm-logo');
      ok('Formular hat die Zeile „Logo“ (Gruppe mit Beschriftung)', await row.count() === 1 && await row.getAttribute('role') === 'group');
      // zu gross: schon im Browser abgewiesen, kein Upload
      await row.locator('input[type=file]').setInputFiles({ name: 'gross.png', mimeType: 'image/png', buffer: Buffer.alloc(2 * 1024 * 1024 + 10, 1) });
      await sleep(400);
      ok('Datei > 2 MB: kein Upload, Hinweis', uploads.length === 0 && /zu groß/.test(await p.locator('.toast').textContent()));
      await row.locator('input[type=file]').setInputFiles({ name: 'x.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"/>') });
      await sleep(300);
      ok('SVG: kein Upload', uploads.length === 0);
      // Enter auf dem Knopf: Dateiauswahl, nicht speichern
      const upBtn = row.locator('button', { hasText: 'Bild hochladen' });
      const fc = p.waitForEvent('filechooser', { timeout: 4000 }).catch(() => null);
      await upBtn.focus(); await p.keyboard.press('Enter');
      const chooser = await fc;
      ok('Enter auf „Bild hochladen“ öffnet die Dateiauswahl (speichert nicht)', chooser && (await api('GET', '/api/bookmarks')).length === 1);
      if (chooser) await chooser.setFiles({ name: 'nas-logo.png', mimeType: 'image/png', buffer: LOGO });
      else await row.locator('input[type=file]').setInputFiles({ name: 'nas-logo.png', mimeType: 'image/png', buffer: LOGO });
      const pv = await until(() => row.locator('.ic img').evaluate((i) => i.complete && i.naturalWidth > 0 ? i.getAttribute('src') : '').catch(() => ''));
      ok(`Vorschau nach dem Upload (${pv})`, /^\/files\/[0-9a-f]{24}\?w=160$/.test(pv || '') && uploads.length === 1);
      const sym = form.locator('label:has-text("Symbol") input');
      ok('Feld Symbol gesperrt, Knöpfe „Ersetzen …“ und „Entfernen“', await sym.isDisabled() && await row.locator('button', { hasText: 'Ersetzen' }).isVisible()
         && await row.locator('button', { hasText: 'Entfernen' }).isVisible());
      await sleep(300);
      if (SHOTS) await sec.screenshot({ path: SHOTS + '/nachher-formular.png' });
      await form.locator('button', { hasText: 'Hinzufügen' }).click();
      const li = sec.locator('.hk-bm-row', { hasText: 'NAS' });
      await until(() => li.count());
      const ls = await until(() => li.locator('.ic img').evaluate((i) => i.complete && i.naturalWidth > 0 ? i.getAttribute('src') : '').catch(() => ''));
      ok(`Liste zeigt das Logo (${ls})`, /^\/api\/bookmarks\/\d+\/logo\?v=[0-9a-f]{8}$/.test(ls || ''));
      await sleep(500);
      if (SHOTS) await sec.screenshot({ path: SHOTS + '/nachher-liste.png' });
    }
    const nas = (await api('GET', '/api/bookmarks')).find((b) => b.name === 'NAS');
    if (nas) made.push(nas.id);
    await p.goto(BASE + '/dashboard?t=' + Date.now());
    const tw = p.locator(`.tw[data-bm="${nas && nas.id}"]`);
    await tw.scrollIntoViewIfNeeded().catch(() => {});
    if (!VORHER) {
      const ts = await until(() => tw.locator('.ic img').evaluate((i) => i.complete && i.naturalWidth > 0 ? i.getAttribute('src') : '').catch(() => ''));
      ok(`Heute-Kachel zeigt das Logo (${ts})`, /\/api\/bookmarks\/\d+\/logo\?v=/.test(ts || ''));
    }
    await sleep(500);
    if (SHOTS) {
      const cat = tw.locator('xpath=ancestor::*[contains(@class,"cat")][1]');
      await ((await cat.count()) ? cat : tw).screenshot({ path: SHOTS + (VORHER ? '/vorher-heute.png' : '/nachher-heute.png') });
    }
    if (!VORHER && nas) {
      // Bearbeiten: Ersetzen-Knopf da, Entfernen -> Speichern -> kein Logo mehr
      await p.click(`button.bm-edit[data-bm="${nas.id}"]`);
      const form = p.locator('.hk-set-bookmarks .hk-cal-form'); await form.waitFor();
      const row = form.locator('.hk-bm-logo');
      ok('Bearbeiten zeigt das gesetzte Logo', await until(() => row.locator('.ic img').evaluate((i) => i.naturalWidth > 0).catch(() => false)));
      await row.locator('button', { hasText: 'Entfernen' }).click();
      ok('Entfernen gibt das Feld Symbol frei', !(await form.locator('label:has-text("Symbol") input').isDisabled()));
      await form.locator('button', { hasText: 'Speichern' }).click();
      const after = await until(async () => { const b = (await api('GET', '/api/bookmarks')).find((x) => x.id === nas.id); return b && b.icon === '' ? b : null; });
      ok('Gespeichert ohne Logo', after && !/\/logo/.test(after.icon_url || ''));
    }
    ok('Keine Skriptfehler', errs.length === 0, errs);
  } finally {
    await sleep(800);                                         // Speichern aus ✎ laedt die Seite neu (closeSettings)
    await p.goto(BASE + '/dashboard?t=' + Date.now()).catch(() => {});
    for (const b of await api('GET', '/api/bookmarks').catch(() => [])) await api('DELETE', '/api/bookmarks/' + b.id).catch(() => {});
    await br.close();
  }
  if (!VORHER) console.log(`\nLESEZEICHEN-LOGO-WEB ${res.filter(Boolean).length}/${res.length}`, res.length && res.every(Boolean) ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(VORHER || (res.length && res.every(Boolean)) ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
