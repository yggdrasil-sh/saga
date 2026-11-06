// Design-Runde Web (UX-29/30): Glocke buendelt je Unterhaltung/Seite - "Bert Glocke · 3 neue Nachrichten", "5 verpasste
// Videoanrufe · zuletzt 11:13" mit "Zurueckrufen", "Glocke Seite · 3 neue Kommentare"; Zaehler = Zeilen; Klick markiert die
// ganze Zeile gelesen; live kommende Nachricht landet in der vorhandenen Zeile; Aktionen erst bei Ueberfahren/Fokus;
// zugaenglicher Name je Zeile; Fokusfuehrung (Pfeiltasten, Escape zurueck zur Glocke).
// Daten legt tests/mitteilungen_buendeln.py seed an (gleiche Umgebung: DATABASE_URL, HK_TEST_BASE, BASE_URL, SESSION_SECRET,
// PYTHONPATH=tests/pgshim:.); SAGA_BASE = Server. PY = Python (Standard python3).
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync } = require('child_process');
const E = process.env, BASE = E.SAGA_BASE || 'http://127.0.0.1:8099', PY = E.PY || 'python3';
const py = a => execFileSync(PY, ['tests/mitteilungen_buendeln.py'].concat(a), { encoding: 'utf8' });
const R = []; const ok = (n, c, x) => { R.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };

(async () => {
  const S = JSON.parse(py(['seed']).trim().split('\n').pop());
  const br = await chromium.launch({ channel: E.PW_CHANNEL || undefined });
  const ctx = await br.newContext({ viewport: { width: 1360, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(e.message));
  try {
    await p.goto(BASE + '/login'); await p.fill('input[name=username]', S.user); await p.fill('input[name=password]', S.pass);
    await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
    await p.goto(BASE + '/notes'); await p.waitForTimeout(2500);
    const badge = await p.textContent('#hk-bell .hk-bell-n');
    ok('Zaehler an der Glocke = Zeilen (4: Bert, Cora, Anrufe, Seite)', badge === '4', badge);
    ok('... Glocke nennt es', /4 ungelesen/.test(await p.getAttribute('#hk-bell', 'aria-label')));
    await p.focus('#hk-bell'); await p.keyboard.press('Enter'); await p.waitForTimeout(1500);
    const rows = await p.$$eval('.lv-item', l => l.map(b => ({ t: b.querySelector('.lv-text').textContent, g: b.dataset.g || '', n: b.dataset.n || '',
      a: b.getAttribute('aria-label'), page: !!b.querySelector('.lv-page') })));
    if (E.SHOT) await p.screenshot({ path: E.SHOT, clip: await p.$eval('#lv-panel', x => { const r = x.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; }) });
    ok('vier Zeilen statt neun Mitteilungen', rows.length === 4, rows.map(r => r.t));
    const rb = rows.find(r => r.g === 'chat:' + S.dm_b), rc = rows.find(r => r.g === 'chat:' + S.dm_c);
    const rk = rows.find(r => r.g === 'call:' + S.dm_b), rp = rows.find(r => r.g === 'page:' + S.page);
    ok('Bert: "Bert Glocke · 3 neue Nachrichten" (ohne doppelte Ortszeile)', rb && rb.t === 'Bert Glocke · 3 neue Nachrichten' && !rb.page && rb.n === '3', rb);
    ok('Cora einzeln wie bisher', rc && rc.t === 'Neue Nachricht von Cora Glocke' && rc.page, rc);
    ok('Anrufe: "Bert Glocke · 5 verpasste Videoanrufe · zuletzt HH:MM"', rk && /^Bert Glocke · 5 verpasste Videoanrufe · zuletzt \d\d:\d\d$/.test(rk.t), rk);
    ok('Seite: "Glocke Seite · 3 neue Kommentare"', rp && rp.t === 'Glocke Seite · 3 neue Kommentare', rp);
    ok('zugaenglicher Name: Satz, Zeit, Ungelesen', rb && /^Bert Glocke · 3 neue Nachrichten, .+, Ungelesen$/.test(rb.a), rb && rb.a);
    ok('Oeffnen per Tastatur: Fokus auf der ersten Zeile', await p.evaluate(() => document.activeElement.classList.contains('lv-item')));
    // Aktionen: erst beim Ueberfahren / mit dem Fokus in der Zeile
    const op = sel => p.$eval(sel, x => getComputedStyle(x).opacity);
    const li = '.lv-li:has(.lv-item[data-g="chat:' + S.dm_b + '"])';
    await p.focus('.lv-item[data-g="page:' + S.page + '"]');
    await p.mouse.move(5, 850); await p.waitForTimeout(250);
    const op0 = await p.$(li + ' .lv-reply') ? await op(li + ' .lv-reply') : 'fehlt';
    ok('"Antworten" da, aber unsichtbar ohne Ueberfahren', op0 === '0', op0);
    await p.hover(li + ' .lv-item'); await p.waitForTimeout(250);
    ok('... beim Ueberfahren sichtbar', await op(li + ' .lv-reply') === '1');
    await p.mouse.move(5, 850); await p.focus(li + ' .lv-item'); await p.waitForTimeout(250);
    ok('... mit dem Fokus in der Zeile sichtbar (Tastatur)', await op(li + ' .lv-reply') === '1');
    await p.keyboard.press('Tab');
    ok('... per Tab erreichbar, Name nennt den Chat', await p.evaluate(() => document.activeElement.getAttribute('aria-label')) === 'Antworten: Bert Glocke');
    // Pfeiltasten und Escape (bestehende Fokusfuehrung)
    await p.focus('.lv-item'); await p.keyboard.press('ArrowDown');
    ok('Pfeil runter: naechste Zeile', await p.evaluate(() => document.activeElement === document.querySelectorAll('.lv-item')[1]));
    // Zurueckrufen (wie im Chat: HKCalls.start mit dem Chat und Video)
    const kli = '.lv-li:has(.lv-item[data-g="call:' + S.dm_b + '"])';
    ok('"Zurückrufen" an den verpassten Anrufen', !!(await p.$(kli + ' .lv-callback')));
    await p.evaluate(() => { window.HKCalls.start = function (c, v) { window.__cb = [c && c.id, v]; }; });
    // Live: Bert kommentiert noch einmal (ueber den Server) -> dieselbe Seiten-Zeile, 4 Kommentare
    py(['comment', 'gb-wb', String(S.page), 'b7']); await p.waitForTimeout(2500);
    const rp2 = await p.$$eval('.lv-item[data-g="page:' + S.page + '"]', l => l.map(b => b.querySelector('.lv-text').textContent));
    ok('live: neuer Kommentar landet in der Zeile seiner Seite', rp2.length === 1 && rp2[0] === 'Glocke Seite · 4 neue Kommentare', rp2);
    ok('... Zaehler bleibt 4 (Zeilen)', await p.textContent('#hk-bell .hk-bell-n') === '4');
    await p.hover(kli + ' .lv-item'); await p.click(kli + ' .lv-callback'); await p.waitForTimeout(1500);
    ok('Zurückrufen startet den Anruf im Chat mit Video', await p.evaluate(() => JSON.stringify(window.__cb)) === JSON.stringify([S.dm_b, true]), await p.evaluate(() => window.__cb));
    ok('... Anrufe-Zeile gelesen, Zaehler 3', await p.textContent('#hk-bell .hk-bell-n') === '3');
    // Klick auf eine gebuendelte Zeile: alle gelesen
    await p.click('#hk-bell'); await p.waitForTimeout(1200);
    await p.click('.lv-item[data-g="page:' + S.page + '"]'); await p.waitForTimeout(1500);
    const st = await p.evaluate(() => HK.api('GET', '/api/notifications?limit=0&group=1'));
    ok('Klick auf "4 neue Kommentare": alle vier gelesen (Server)', st.unread_groups === 2 && st.unread === 4, st);
    ok('... Zaehler 2', await p.textContent('#hk-bell .hk-bell-n') === '2');
    await p.click('#hk-bell'); await p.waitForTimeout(1200);
    await p.keyboard.press('Escape'); await p.waitForTimeout(300);
    ok('Escape: zu, Fokus zurueck auf der Glocke', await p.evaluate(() => document.activeElement.id === 'hk-bell' && document.getElementById('lv-panel').hidden));
    ok('keine Skriptfehler', !errs.length, errs);
  } finally {
    await br.close();
    try { py(['clean']); } catch (e) { /* Aufraeumen best effort */ }
  }
  const n = R.filter(Boolean).length;
  console.log('\nBROWSER-GLOCKE ' + n + '/' + R.length + (n === R.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(n === R.length ? 0 : 1);
})().catch(e => { console.error(e); try { py(['clean']); } catch (x) { /* */ } process.exit(1); });
