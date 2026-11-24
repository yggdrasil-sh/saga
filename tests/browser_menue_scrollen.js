const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Menues und Scrollen (Bugs-DB 2255): Ein offenes Menue schliesst nur, wenn beim Scrollen sein Ausloeser wandert - nicht,
// wenn ein fremder Container scrollt (frueher schloss jedes scroll-Ereignis ausserhalb des Menues, auch das verspaetete
// Zuruecksetzen von #pg-modal-body). Scrollen im Menue selbst schliesst nie; Scrollen der Seite bzw. des Containers mit
// dem Ausloeser schliesst wie bisher. Ohne Server-Daten: Testaufbau im DOM der Seiten-Ansicht, Menue ueber HK.openMenu.
//   SAGA_BASE=… SAGA_USER=konto SAGA_PASS=… node tests/browser_menue_scrollen.js
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS;
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const bad = []; let n = 0;
const ok = (c, m) => { n++; if (!c) { bad.push(m); console.log('  FEHLT: ' + m); } else console.log('  ok: ' + m); };
const frames = p => p.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
(async () => {
  const b = await chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await b.newContext({ viewport: { width: 1200, height: 800 }, locale: 'de-DE' });
  const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  await p.goto(BASE + '/notes');
  for (let i = 0; i < 50 && !(await p.evaluate(() => !!(window.HK && window.HK.openMenu))); i++) await new Promise(r => setTimeout(r, 100));
  // Aufbau: zwei scrollbare Kaesten - A mit dem Ausloeser, B fremd; dazu ein hoher Abstandhalter fuer das Fenster
  await p.evaluate(() => {
    const mk = (id, withBtn) => {
      const box = document.createElement('div'); box.id = id;
      box.style.cssText = 'position:fixed;top:60px;width:260px;height:200px;overflow:auto;background:#eee;z-index:5;left:' + (id === 'tA' ? 20 : 320) + 'px';
      const inner = document.createElement('div'); inner.style.height = '1200px';
      if (withBtn) { const b = document.createElement('button'); b.id = 'tBtn'; b.textContent = 'Menü'; b.style.marginTop = '20px'; inner.appendChild(b); }
      box.appendChild(inner); document.body.appendChild(box);
    };
    mk('tA', true); mk('tB', false);
    const sp = document.createElement('div'); sp.id = 'tSpace'; sp.style.height = '3000px'; document.body.appendChild(sp);
    window.__openT = () => window.HK.openMenu(document.getElementById('tBtn'), m => {
      for (let i = 0; i < 40; i++) m.appendChild(window.HK.menuItem('Eintrag ' + i, function () {}));
    });
  });
  const isOpen = () => p.evaluate(() => window.HK.menuOpen());
  const scroll = (sel, y) => p.evaluate(([s, y]) => { const e = s === 'window' ? null : document.querySelector(s);
    if (e) e.scrollTop = y; else window.scrollTo(0, y); }, [sel, y]);

  console.log('Fremder Container');
  await p.evaluate(() => window.__openT()); await frames(p);
  ok(await isOpen(), 'Menü offen');
  await scroll('#tB', 300); await frames(p);
  ok(await isOpen(), 'Scrollen eines fremden Containers schließt nichts');
  await scroll('#tB', 0); await frames(p);
  ok(await isOpen(), 'verspätetes Zurücksetzen (Scrollstand 0) eines fremden Containers schließt nichts');

  console.log('Im Menü');
  await p.evaluate(() => { const m = document.querySelector('.menu:not(.menu-sub)'); m.style.maxHeight = '200px'; m.style.overflow = 'auto'; m.scrollTop = 150; });
  await frames(p);
  ok(await isOpen(), 'Scrollen im Menü selbst schließt nicht');

  console.log('Container mit dem Auslöser');
  await scroll('#tA', 120); await frames(p);
  ok(!(await isOpen()), 'Auslöser wandert mit seinem Container -> Menü zu');

  console.log('Seite');
  await scroll('#tA', 0); await frames(p);
  await p.evaluate(() => { document.getElementById('tA').style.position = 'absolute'; document.getElementById('tB').style.position = 'absolute'; });
  await p.evaluate(() => window.__openT()); await frames(p);
  ok(await isOpen(), 'Menü wieder offen');
  await scroll('window', 400); await frames(p);
  ok(!(await isOpen()), 'Seite scrollt, Auslöser wandert -> Menü zu');
  await scroll('window', 0); await frames(p);
  await p.evaluate(() => window.__openT()); await frames(p);
  await p.evaluate(() => window.dispatchEvent(new Event('scroll'))); await frames(p);
  ok(await isOpen(), 'scroll-Ereignis ohne Bewegung (Fenster) schließt nicht');
  await p.evaluate(() => window.HK.closeMenu());

  ok(errs.length === 0, 'keine Skriptfehler (' + errs.join(' | ') + ')');
  await b.close();
  console.log(bad.length ? `\n${n - bad.length}/${n} ok - FEHLER: ${bad.join('; ')}` : `\nMenüs und Scrollen ${n}/${n} - ALLES GRUEN`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
