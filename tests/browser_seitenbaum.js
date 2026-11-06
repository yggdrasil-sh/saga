const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
// Seitenbaum als echter Baum (Design-Runde A11Y-11/12): ein Tab-Stopp je Baum, Pfeiltasten/Home/End/Enter/Buchstabe,
// aria-expanded/-level/-setsize/-posinset/-current, Name = Seitenname, Fokus bleibt nach Neuaufbau, "Mit mir geteilt",
// Favoriten und die globale Seitenleiste (Heute).
//   SAGA_BASE=http://127.0.0.1:8099 SAGA_USER=a SAGA_PASS=… SAGA_USER2=b SAGA_PASS2=… [PLAYWRIGHT=…] node tests/browser_seitenbaum.js
// Zwei Konten (Passwort-Anmeldung); die Reihe legt ihre Seiten selbst an und wirft sie am Ende in den Papierkorb.
// Aufbau lokal: nach db.wait_ready() auth.create_user('baum-a', pw, 'A') und auth.create_user('baum-b', pw, 'B').
const BASE = process.env.SAGA_BASE || process.env.HK_TEST_BASE, U1 = process.env.SAGA_USER, P1 = process.env.SAGA_PASS;
const U2 = process.env.SAGA_USER2, P2 = process.env.SAGA_PASS2;
if (!BASE || !U1 || !P1 || !U2 || !P2) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS, SAGA_USER2, SAGA_PASS2 setzen'); process.exit(2); }
const bad = []; let n = 0;
const ok = (c, m) => { n++; if (!c) { bad.push(m); console.log('  FEHLT: ' + m); } else console.log('  ok: ' + m); };
const wait = ms => new Promise(r => setTimeout(r, ms));
async function login(b, u, pw) {
  const ctx = await b.newContext({ viewport: { width: 1400, height: 900 } });
  const p = await ctx.newPage(); p.errs = []; p.on('pageerror', e => p.errs.push(String(e)));
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', pw);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  p.api = (m, url, body) => p.evaluate(async ([m, url, body]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const t = await r.text(); try { return JSON.parse(t); } catch (e) { return t; }
  }, [m, url, body]);
  return p;
}
async function until(f, ms) { const t0 = Date.now(); while (Date.now() - t0 < (ms || 4000)) { if (await f()) return true; await wait(100); } return false; }
(async () => {
  const b = await chromium.launch();
  const a = await login(b, U1, P1), q = await login(b, U2, P2);
  const tag = String(Date.now()).slice(-5);
  // Seiten: Wurzel R (Kinder Apfel > Apfel-Kern, Birne, Kirsche), Favorit F; von B geteilt G
  const mk = (pg, title, parent) => pg.api('POST', '/api/notes', parent ? { title, parent_id: parent } : { title }).then(d => d.id);
  const R = await mk(a, 'Baumtest ' + tag), AP = await mk(a, 'Apfel', R), AK = await mk(a, 'Apfel-Kern', AP), BI = await mk(a, 'Birne', R), KI = await mk(a, 'Kirsche', R);
  const F = await mk(a, 'Favorit ' + tag); await a.api('PATCH', '/api/notes/' + F, { pinned: true });
  await a.api('PUT', '/api/notes/' + BI + '/shares', { type: 'user', target: U2, role: 'view' });     // eigene Seite geteilt
  const G = await mk(q, 'Von B ' + tag); await q.api('PUT', '/api/notes/' + G + '/shares', { type: 'user', target: U1, role: 'view' });
  await a.goto(BASE + '/notes');
  await a.evaluate(r => { localStorage.setItem('hk-pg-open', JSON.stringify({ [r]: true })); }, R);   // nur R offen
  await a.reload(); await until(() => a.evaluate(id => !!document.querySelector('#pg-tree [role=treeitem][data-id="' + id + '"]'), R));
  await wait(400);

  console.log('Tab-Stopps');
  const stops = await a.evaluate(() => ['pg-tree', 'pg-fav-list', 'pg-shared-tree'].map(id => {
    const ul = document.getElementById(id);
    return [...ul.querySelectorAll('button, a[href], [tabindex]')].filter(x => x.tabIndex >= 0).length;
  }));
  ok(stops[0] === 1, 'eigener Baum: genau ein Tab-Stopp (' + stops[0] + ')');
  ok(stops[1] === 1, 'Favoriten: genau ein Tab-Stopp (' + stops[1] + ')');
  ok(stops[2] === 1, 'Mit mir geteilt: genau ein Tab-Stopp (' + stops[2] + ')');
  // Tab vom Suchfeld: wenige Schritte bis in den Baum, ein Schritt wieder hinaus
  await a.focus('#pg-search');
  let steps = 0, inTree = false;
  for (; steps < 25 && !inTree; steps++) { await a.keyboard.press('Tab'); inTree = await a.evaluate(() => !!document.activeElement.closest('#pg-tree')); }
  ok(inTree && steps <= 10, 'Tab erreicht den Baum nach wenigen Schritten (' + steps + ')');
  await a.keyboard.press('Tab');
  ok(await a.evaluate(() => !document.activeElement.closest('#pg-tree')), 'ein weiterer Tab verlaesst den Baum');
  // Reihenfolge der Seitenleiste folgt der Optik (A11Y-12): Suche -> Navigation -> Favoriten -> Baum -> Neue Seite -> Geteilt -> Mehr
  const order = await a.evaluate(() => {
    const want = ['pg-search', 'pg-mainnav', 'pg-fav-list', 'pg-tree', 'pg-new', 'pg-shared-tree', 'pg-qs-btn'].map(id => document.getElementById(id));
    for (let i = 1; i < want.length; i++) if (!(want[i - 1].compareDocumentPosition(want[i]) & Node.DOCUMENT_POSITION_FOLLOWING)) return i;
    return 0;
  });
  ok(order === 0, 'Seitenleiste: DOM-Reihenfolge = Optik (' + order + ')');

  console.log('aria-Attribute');
  const at = await a.evaluate(([R, AP, AK, BI]) => {
    const li = id => document.querySelector('#pg-tree [role=treeitem][data-id="' + id + '"]');
    const g = (id, k) => li(id) && li(id).getAttribute(k);
    return { rExp: g(R, 'aria-expanded'), rLvl: g(R, 'aria-level'), apExp: g(AP, 'aria-expanded'), apLvl: g(AP, 'aria-level'),
      apPos: g(AP, 'aria-posinset') + '/' + g(AP, 'aria-setsize'), biExp: g(BI, 'aria-expanded'), biName: g(BI, 'aria-label'), biDesc: g(BI, 'aria-description'),
      apName: g(AP, 'aria-label'), leafTw: li(BI).querySelector('.pg-twisty').getAttribute('aria-hidden'), kern: !!li(AK),
      inner: [...li(R).querySelectorAll('.pg-row button')].every(x => x.tabIndex === -1), ic: li(R).querySelector('.pg-ic').getAttribute('aria-hidden') };
  }, [R, AP, AK, BI]);
  console.log('   ', JSON.stringify(at));
  ok(at.rExp === 'true' && at.rLvl === '1', 'Wurzel: aria-expanded=true, aria-level=1');
  ok(at.apExp === 'false' && at.apLvl === '2' && at.apPos === '1/3', 'Kind mit Kindern: aria-expanded=false, Ebene 2, 1 von 3');
  ok(at.biExp === null, 'Blatt: kein aria-expanded');
  ok(at.biName === 'Birne' && /Geteilt|Shared/.test(at.biDesc || ''), 'Name = nur Seitenname, „Geteilt“ als Beschreibung');
  ok(at.apName === 'Apfel', 'Name ohne Symbol und Zusaetze');
  ok(at.leafTw === 'true', 'Blatt-Twisty aria-hidden');
  ok(at.inner && at.ic === 'true', 'Twisty/„+“ tabindex=-1, Symbol aria-hidden');
  ok(!at.kern, 'zugeklappter Ast: Enkel nicht im DOM');

  console.log('Tasten');
  const foc = () => a.evaluate(() => { const li = document.activeElement.closest('[role=treeitem]'); return li ? li.getAttribute('aria-label') : '(' + document.activeElement.tagName + ')'; });
  await a.evaluate(r => document.querySelector('#pg-tree [role=treeitem][data-id="' + r + '"]').focus(), R);
  await a.keyboard.press('ArrowDown'); ok(await foc() === 'Apfel', '↓: erstes Kind');
  await a.keyboard.press('ArrowRight'); await wait(100);
  ok(await a.evaluate(id => document.querySelector('[data-id="' + id + '"]').getAttribute('aria-expanded'), AP) === 'true' && await foc() === 'Apfel', '→: klappt auf, Fokus bleibt');
  await a.keyboard.press('ArrowRight'); ok(await foc() === 'Apfel-Kern', '→ (offen): erstes Kind');
  await a.keyboard.press('ArrowDown'); ok(await foc() === 'Birne', '↓: naechste sichtbare Zeile');
  await a.keyboard.press('ArrowUp'); await a.keyboard.press('ArrowLeft'); ok(await foc() === 'Apfel', '←: zum Elternteil');
  await a.keyboard.press('ArrowLeft'); await wait(100);
  ok(await a.evaluate(id => document.querySelector('[data-id="' + id + '"]').getAttribute('aria-expanded'), AP) === 'false', '← (offen): klappt zu');
  ok(await a.evaluate(([r, ap]) => { const o = JSON.parse(localStorage.getItem('hk-pg-open') || '{}'); return !!o[r] && !o[ap]; }, [R, AP]), 'Auf-/Zuklappen bleibt gemerkt (S.open)');
  await a.keyboard.press('k'); ok(await foc() === 'Kirsche', 'Buchstabe springt');
  await a.keyboard.press('Home'); const first = await foc();
  await a.keyboard.press('End'); const last = await foc();
  const ends = await a.evaluate(() => { const l = [...document.querySelectorAll('#pg-tree [role=treeitem]')].filter(x => x.getClientRects().length); return [l[0].getAttribute('aria-label'), l[l.length - 1].getAttribute('aria-label')]; });
  ok(first === ends[0] && last === ends[1], 'Home/End: erste/letzte sichtbare Zeile');
  // Enter oeffnet; nach Neuaufbau bleibt der Fokus auf derselben Seite, aria-current an der offenen Seite
  await a.evaluate(id => document.querySelector('#pg-tree [data-id="' + id + '"]').focus(), BI);
  await a.keyboard.press('Enter');
  ok(await until(() => a.evaluate(() => document.getElementById('pg-title').value === 'Birne')), 'Enter oeffnet die Seite');
  await wait(600);
  ok(await foc() === 'Birne', 'Fokus bleibt nach Neuaufbau auf derselben Seite');
  ok(await a.evaluate(id => document.querySelector('#pg-tree [data-id="' + id + '"]').getAttribute('aria-current'), BI) === 'page', 'aria-current=page an der offenen Seite');
  ok(await a.evaluate(id => document.querySelector('#pg-tree [data-id="' + id + '"]').tabIndex, BI) === 0, 'offene Seite ist der Tab-Stopp');
  // Neuaufbau von aussen (Titel aendern -> renderTree)
  await a.evaluate(() => document.querySelector('#pg-tree [role=treeitem][aria-label="Apfel"]').focus());
  await a.api('PATCH', '/api/notes/' + KI, { title: 'Kirsche 2' });
  await a.evaluate(() => window.HKPages.k.treeChanged());                 // wie das Live-Ereignis notes.tree
  const rebuilt = await until(() => a.evaluate(() => !!document.querySelector('#pg-tree [role=treeitem][aria-label="Kirsche 2"]')), 5000);
  ok(rebuilt && await foc() === 'Apfel', 'Fokus bleibt nach Live-Neuaufbau auf derselben Seite');

  console.log('Mit mir geteilt / Favoriten');
  const sh = await a.evaluate(g => { const li = document.querySelector('#pg-shared-tree [role=treeitem][data-id="' + g + '"]');
    return li && { name: li.getAttribute('aria-label'), desc: li.getAttribute('aria-description'), lvl: li.getAttribute('aria-level') }; }, G);
  ok(sh && sh.name === 'Von B ' + tag && sh.lvl === '1' && !!sh.desc, 'geteilte Seite: Name, Ebene, Eigentuemer als Beschreibung');
  await a.evaluate(g => document.querySelector('#pg-shared-tree [data-id="' + g + '"]').focus(), G);
  await a.keyboard.press('Enter');
  ok(await until(() => a.evaluate(t => document.getElementById('pg-title').value === t, 'Von B ' + tag)), 'geteilte Seite per Enter geoeffnet');
  const fv = await a.evaluate(f => { const li = document.querySelector('#pg-fav-list [role=treeitem][data-id="' + f + '"]'); return li && [li.getAttribute('aria-label'), li.getAttribute('aria-level')]; }, F);
  ok(fv && fv[0] === 'Favorit ' + tag && fv[1] === '1', 'Favorit als Baumzeile');
  await a.evaluate(f => document.querySelector('#pg-fav-list [data-id="' + f + '"]').focus(), F);
  await a.keyboard.press('Enter');
  ok(await until(() => a.evaluate(t => document.getElementById('pg-title').value === t, 'Favorit ' + tag)), 'Favorit per Enter geoeffnet');

  console.log('Globale Seitenleiste (Heute)');
  await a.goto(BASE + '/dashboard'); await until(() => a.evaluate(id => !!document.querySelector('#sh-tree [data-id="' + id + '"]'), R));
  const gs = await a.evaluate(() => { const ul = document.getElementById('sh-tree');
    return { stops: [...ul.querySelectorAll('button, a[href], [tabindex]')].filter(x => x.tabIndex >= 0).length, role: ul.getAttribute('role') }; });
  ok(gs.stops === 1 && gs.role === 'tree', 'Heute: Baum mit einem Tab-Stopp');
  await a.focus('#sh-search'); await a.keyboard.press('ArrowDown');
  ok(await a.evaluate(() => !!document.activeElement.closest('#sh-tree [role=treeitem]')), 'Suche ↓ springt in den Baum');
  await a.evaluate(r => document.querySelector('#sh-tree [data-id="' + r + '"]').focus(), R);
  const exp0 = await a.evaluate(r => document.querySelector('#sh-tree [data-id="' + r + '"]').getAttribute('aria-expanded'), R);
  await a.keyboard.press(exp0 === 'true' ? 'ArrowLeft' : 'ArrowRight'); await wait(100);
  const exp1 = await a.evaluate(r => document.querySelector('#sh-tree [data-id="' + r + '"]').getAttribute('aria-expanded'), R);
  ok(exp0 && exp1 && exp0 !== exp1, 'Heute: ←/→ klappt auf bzw. zu');
  if (exp1 !== 'true') { await a.keyboard.press('ArrowRight'); await wait(100); }
  await a.keyboard.press('ArrowDown'); ok(await foc() === 'Apfel', 'Heute: ↓ zum Kind');
  await Promise.all([a.waitForURL(/\/notes/), a.keyboard.press('Enter')]);
  ok(await until(() => a.evaluate(() => document.getElementById('pg-title').value === 'Apfel')), 'Heute: Enter oeffnet die Seite auf /notes');

  // Aufraeumen
  await a.api('DELETE', '/api/notes/' + R); await a.api('DELETE', '/api/notes/' + F); await q.api('DELETE', '/api/notes/' + G);
  const errs = a.errs.concat(q.errs);
  ok(!errs.length, 'keine Seitenfehler: ' + errs.join(' | '));
  console.log(n - bad.length + '/' + n + (bad.length ? '\nFEHLER:\n- ' + bad.join('\n- ') : '\nALLES GRUEN'));
  await b.close();
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
