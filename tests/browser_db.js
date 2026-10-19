// Browser-Tests fuer lange Datenbank-Ansichten (2.2: Tabelle/Liste virtualisiert, Galerie/Board schrittweise) -
// wie tests/browser_web.js NICHT im Stack-Testlauf, sondern von einem Rechner mit Playwright gegen einen Test-Stack:
//   SAGA_BASE=http://host:8175 SAGA_USER=konto SAGA_PASS=... [PLAYWRIGHT=/pfad/zu/node_modules/playwright-core] node tests/browser_db.js
// Legt eine Datenbank mit N (Standard 800) Zeilen an und prueft: nur der Ausschnitt im DOM, Scrollhoehe, Scrollen bis
// zum Ende, Tastatur (Enter -> naechste Zeile ueber den Ausschnitt hinaus), Fokus bleibt beim Wegscrollen, Inline-
// Bearbeitung, Haekchen, neue Zeile ausserhalb des Ausschnitts, Summenzeile ueber ALLE Zeilen, Suche, Gruppen,
// Spaltenbreite, Drucken (beforeprint -> alle Zeilen), Liste, Galerie/Board vollstaendig, eingebettete Ansicht, Handy.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core'), ENGINE = process.env.PW_BROWSER || 'chromium';
const BASE = process.env.SAGA_BASE, USER = process.env.SAGA_USER, PASS = process.env.SAGA_PASS, N = +(process.env.SAGA_DB_ROWS || 800);
if (!BASE || !USER || !PASS) { console.error('SAGA_BASE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

async function session(browser, opts = {}) {
  const ctx = await browser.newContext({ viewport: opts.viewport || { width: 1366, height: 860 }, locale: 'de-DE', isMobile: !!opts.mobile, hasTouch: !!opts.mobile });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { console.log('SEITENFEHLER', e.message); res.push(false); });
  await p.goto(BASE + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', USER);
  await p.fill('input[type=password]', PASS);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  p.open = async (id) => { await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector('.db-row, .dbl-row, .dbg-card, .board-card'); await p.waitForTimeout(600); };
  return p;
}
async function until(p, fn, ms = 60000) {        // wie waitForFunction, aber ohne eval (Saga erlaubt kein 'unsafe-eval')
  for (const t0 = Date.now(); Date.now() - t0 < ms; await p.waitForTimeout(100)) if (await p.evaluate(fn)) return true;
  return false;
}
const rowsInDom = (p) => p.evaluate(() => document.querySelectorAll('tr.db-row').length);
const total = (p) => p.evaluate(() => { const t = document.querySelector('table.db-table'); return t ? +t.dataset.rows : -1; });

async function makeDb(p, n, title) {
  const D = (await p.api('POST', '/api/notes', { kind: 'database', title })).id;
  const s = (await p.api('GET', '/api/notes/' + D)).schema;
  const st = s.props.find((x) => x.type === 'select') || { id: 'st', name: 'Status', type: 'select', options: [] };
  if (!s.props.includes(st)) s.props.push(st);
  if (!st.options.length) st.options = [{ id: 'o1', name: 'Offen', color: 'gray' }, { id: 'o2', name: 'Fertig', color: 'green' }];
  s.props.push({ id: 'num', name: 'Betrag', type: 'number' }, { id: 'ok', name: 'Erledigt', type: 'checkbox' });
  s.views = [{ id: 'tab', name: 'Tabelle', type: 'table', calcs: { num: 'sum', title: 'count' }, sorts: [{ pid: 'title', dir: 'asc' }] },
    { id: 'grp', name: 'Gruppiert', type: 'table', group: st.id }, { id: 'lst', name: 'Liste', type: 'list' },
    { id: 'gal', name: 'Galerie', type: 'gallery' }, { id: 'brd', name: 'Board', type: 'board', group_by: st.id },
    { id: 'asc', name: 'Sortiert', type: 'table', sorts: [{ pid: 'title', dir: 'asc' }] }];
  await p.api('PATCH', '/api/notes/' + D, { schema: s });
  let sum = 0;
  for (let i = 0; i < n; i += 25) {
    const batch = [];
    for (let k = i; k < Math.min(n, i + 25); k++) {
      const props = { num: k % 7, [st.id]: k % 3 ? st.options[k % 2].id : undefined };
      sum += k % 7;
      batch.push(['POST', '/api/notes', { parent_id: D, title: 'Zeile ' + String(k).padStart(4, '0') + (k % 50 === 0 ? ' Привет ภาษาไทย' : ''), props }]);
    }
    await p.evaluate(async (b) => Promise.all(b.map(([m, u, x]) => fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(x) }))), batch);
  }
  return { D, sum, st };
}
function view(p, D, vid) { return p.evaluate(([D, vid]) => { localStorage.setItem('hk-db-view-' + D, vid); }, [D, vid]); }

async function table(p, D, sum) {
  await view(p, D, 'tab'); await p.open(D);
  const t0 = await total(p), dom = await rowsInDom(p);
  ok('Tabelle: alle ' + N + ' Zeilen gezaehlt, nur Ausschnitt im DOM (' + dom + ')', t0 === N && dom > 5 && dom < 140, { t0, dom });
  const h = await p.evaluate(() => document.querySelector('table.db-table tbody').offsetHeight);
  ok('Tabelle: Scrollhoehe fuer alle Zeilen (' + h + ' px)', h > N * 25 && h < N * 90, h);
  // Summenzeile rechnet ueber ALLE Zeilen, nicht nur die eingesetzten
  const calc = await p.evaluate(() => [...document.querySelectorAll('.db-calc-v')].map((x) => x.textContent));
  ok('Summenzeile: Anzahl und Summe ueber alle Zeilen', calc.includes(String(N)) && calc.some((x) => x.replace(/\D/g, '') === String(sum)), { calc, sum });
  // Mitte und Ende per Scrollen
  await p.evaluate(() => { const b = document.querySelector('table.db-table tbody'); window.scrollTo(0, b.getBoundingClientRect().top + window.scrollY + b.offsetHeight / 2); });
  await p.waitForTimeout(400);
  const mid = await p.evaluate(() => [...document.querySelectorAll('tr.db-row .db-title-in')].map((x) => x.value).filter((v) => /Zeile 0[34]/.test(v)).length);
  ok('Scrollen in die Mitte: Zeilen der Mitte eingesetzt', mid > 5, mid);
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await p.waitForTimeout(500);
  const last = await p.evaluate(() => { const r = document.querySelectorAll('tr.db-row'); return r.length && r[r.length - 1].querySelector('.db-title-in').value; });
  ok('Scrollen ans Ende: letzte Zeile da', last && last.indexOf('Zeile ' + String(N - 1).padStart(4, '0')) === 0, last);
  ok('Am Ende weiterhin nur ein Ausschnitt im DOM', (await rowsInDom(p)) < 140);
  // Tastatur: Enter springt 40x in die naechste Zeile - ueber den Ausschnitt hinaus, Fokus geht nie verloren
  await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(300);
  await p.locator('tr.db-row .db-title-in').nth(3).click();
  let lost = 0;
  for (let i = 0; i < 40; i++) { await p.keyboard.press('Enter'); if (!(await p.evaluate(() => document.activeElement.classList.contains('db-title-in')))) lost++; }
  const now = await p.evaluate(() => document.activeElement.value);
  ok('Enter 40x: Fokus wandert Zeile fuer Zeile (jetzt ' + now + ')', !lost && now.indexOf('Zeile 0043') === 0, { lost, now });
  // Bearbeiten, dann weit wegscrollen: die Zeile mit dem Fokus bleibt, die Eingabe wird beim Verlassen gespeichert
  await p.keyboard.press('End'); await p.keyboard.type(' bearbeitet');
  const rid = await p.evaluate(() => +document.activeElement.closest('tr').dataset.id);
  await p.mouse.wheel(0, 9000); await p.waitForTimeout(300); await p.mouse.wheel(0, 9000); await p.waitForTimeout(400);
  const still = await p.evaluate((rid) => { const a = document.activeElement; return a.classList.contains('db-title-in') && +a.closest('tr').dataset.id === rid && a.isConnected; }, rid);
  ok('Wegscrollen beim Tippen: Feld bleibt im DOM und fokussiert', still);
  await p.keyboard.type('!'); await p.evaluate(() => document.activeElement.blur()); await p.waitForTimeout(800);
  const saved = (await p.api('GET', '/api/notes/' + rid)).title;
  ok('Eingabe nach dem Wegscrollen gespeichert', saved === 'Zeile 0043 bearbeitet!', saved);
  // Haekchen in einer Zeile weit unten
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight * 0.8)); await p.waitForTimeout(400);
  const cb = p.locator('tr.db-row input[type=checkbox]').nth(2), cbid = await cb.evaluate((x) => +x.closest('tr').dataset.id);
  await cb.check(); await p.waitForTimeout(700);
  ok('Haekchen unten in der Tabelle gespeichert', ((await p.api('GET', '/api/notes/' + cbid)).props || {}).ok === true);
  // Spaltenbreite ziehen (Rand im Kopf) bleibt erhalten
  const th = p.locator('th.db-th').nth(1), bx = await th.boundingBox(), rs = th.locator('.db-rs');
  await p.evaluate(() => window.scrollTo(0, 0)); await p.waitForTimeout(200);
  const rb = await rs.boundingBox();
  await p.mouse.move(rb.x + 2, rb.y + 5); await p.mouse.down(); await p.mouse.move(rb.x + 82, rb.y + 5, { steps: 4 }); await p.mouse.up(); await p.waitForTimeout(900);
  const w = (((await p.api('GET', '/api/notes/' + D)).schema.views || [])[0].widths || {});
  ok('Spaltenbreite ziehen gespeichert', Object.values(w).some((x) => x > bx.width + 40), { w, alt: bx.width });
  // Suche findet auch Zeilen ausserhalb des Ausschnitts
  await p.fill('input.db-search', 'Zeile 0799'); await p.waitForTimeout(700);
  ok('Suche ueber alle Zeilen', (await total(p)) === (N > 799 ? 1 : 0) && (await rowsInDom(p)) === (N > 799 ? 1 : 0));
  await p.fill('input.db-search', 'привет'); await p.waitForTimeout(700);
  ok('Suche ru (Gross/Klein)', (await total(p)) === Math.ceil(N / 50));
  await p.fill('input.db-search', ''); await p.waitForTimeout(700);
  // Drucken: vorher alle Zeilen, danach wieder nur der Ausschnitt
  await p.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
  const pr = await rowsInDom(p);
  await p.evaluate(() => window.dispatchEvent(new Event('afterprint'))); await p.waitForTimeout(300);
  ok('Drucken: alle ' + N + ' Zeilen im DOM, danach wieder Ausschnitt', pr === N && (await rowsInDom(p)) < 140, pr);
  if (ENGINE === 'chromium') {
    const pdf = await p.pdf({ format: 'A4' }).catch((e) => String(e));
    ok('PDF-Druck (headless) ohne Fehler', typeof pdf !== 'string' && pdf.length > 20000, typeof pdf === 'string' ? pdf : pdf.length);
  }
}

async function others(p, D, st) {
  // neue Zeile ueber "+ Neu" unten: steht (sortiert nach Name, leer = zuerst) oben ausserhalb des Ausschnitts -> eingesetzt + Fokus
  await view(p, D, 'tab'); await p.open(D);
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await p.waitForTimeout(400);
  await p.click('button.db-new-row'); await p.waitForTimeout(1200);
  const nf = await p.evaluate(() => { const a = document.activeElement; return a && a.classList.contains('db-title-in') && a.value === '' && a.closest('tr') && a.closest('tr').dataset.id; });
  ok('"+ Neu": neue Zeile ausserhalb des Ausschnitts eingesetzt und fokussiert', !!nf && (await total(p)) === N + 1, nf);
  await p.keyboard.type('Aaa neu'); await p.keyboard.press('Enter'); await p.waitForTimeout(600);
  ok('Neue Zeile: Name gespeichert, Enter geht weiter', (await p.api('GET', '/api/notes/' + nf)).title === 'Aaa neu' &&
     await p.evaluate(() => document.activeElement.classList.contains('db-title-in')));
  // gruppiert: Koepfe mit Anzahl, Reihenfolge
  await view(p, D, 'grp'); await p.open(D);
  const g = await p.evaluate(() => [...document.querySelectorAll('tr.db-grow')].map((x) => x.textContent));
  ok('Gruppierte Tabelle: Gruppenkoepfe (Ausschnitt) mit Anzahl', g.length >= 1 && /\d/.test(g[0]) && (await total(p)) === N + 1, g);
  const seen = new Set();                    // einmal durchscrollen: jeder Gruppenkopf kommt vorbei
  for (let y = 0, H = await p.evaluate(() => document.documentElement.scrollHeight); y < H; y += 700) {
    await p.evaluate((y) => window.scrollTo(0, y), y); await p.waitForTimeout(60);
    (await p.evaluate(() => [...document.querySelectorAll('tr.db-grow')].map((x) => x.textContent))).forEach((x) => seen.add(x));
  }
  const g2 = [...seen];
  const used = st.options.slice(0, 2);       // nur die ersten beiden Optionen belegt - leere Gruppen zeigt die Tabelle nicht
  ok('Gruppierte Tabelle: beim Durchscrollen alle belegten Gruppen (Ohne, ' + used.map((o) => o.name).join(', ') + ')',
     g2.length === 3 && used.every((o) => g2.some((x) => x.indexOf(o.name) >= 0)) && g2.some((x) => /^Ohne/.test(x)), g2);
  // Liste
  await view(p, D, 'lst'); await p.open(D);
  const l = await p.evaluate(() => ({ n: document.querySelectorAll('.dbl-row').length, t: +document.querySelector('.dbl').dataset.rows }));
  ok('Liste: nur Ausschnitt im DOM, alle gezaehlt', l.t === N + 1 && l.n < 140 && l.n > 5, l);
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await p.waitForTimeout(500);
  ok('Liste: Ende erreichbar, "+ Neu" sichtbar', await p.evaluate(() => { const b = document.querySelector('.dbl .db-new-row').getBoundingClientRect(); return b.top < innerHeight && b.bottom > 0; }));
  await p.locator('.dbl-row').last().press('Enter'); await p.waitForTimeout(900);
  ok('Liste: Eintrag per Tastatur oeffnen', await p.evaluate(() => !!document.querySelector('.peek, .pg-peek, #pg-peek, [class*=peek]')));
  // Galerie und Board: am Ende alle Karten da (schrittweise eingesetzt)
  await view(p, D, 'gal'); await p.goto(BASE + '/notes?t=1#' + D);
  await until(p, () => !!document.querySelector('.dbg') && document.querySelector('.dbg').dataset.done === '1');
  ok('Galerie: alle Karten', (await p.evaluate(() => document.querySelectorAll('.dbg-card').length)) === N + 1);
  ok('Galerie: "+ Neu" am Ende', await p.evaluate(() => document.querySelector('.dbg').lastElementChild.classList.contains('dbg-new')));
  await view(p, D, 'brd'); await p.goto(BASE + '/notes?t=2#' + D);
  await until(p, () => { const l = document.querySelectorAll('.board-cards'); return l.length > 0 && [...l].every((x) => x.dataset.done === '1'); });
  ok('Board: alle Karten in ihren Spalten', (await p.evaluate(() => document.querySelectorAll('.board-card').length)) === N + 1);
}

async function embedded(p, D) {
  const id = (await p.api('POST', '/api/notes', { title: 'Eingebettet lang' })).id;
  await p.api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [{ id: 'p1', type: 'paragraph', data: { text: 'Davor' } },
    { id: 'd1', type: 'database', data: { db: D, view: 'tab' } }, { id: 'p2', type: 'paragraph', data: { text: 'Danach' } }] }), format: 'blocks' });
  await p.goto(BASE + '/notes?t=3#' + id); await p.waitForSelector('.embed-db tr.db-row'); await p.waitForTimeout(800);
  const e = await p.evaluate(() => ({ dom: document.querySelectorAll('.embed-db tr.db-row').length, t: +document.querySelector('.embed-db table.db-table').dataset.rows }));
  ok('Eingebettete Ansicht: virtualisiert', e.t === N + 1 && e.dom < 140, e);
  await p.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await p.waitForTimeout(600);
  ok('Eingebettete Ansicht: Absatz danach erreichbar, letzte Zeile da', await p.evaluate(() => {
    const r = document.querySelectorAll('.embed-db tr.db-row'), last = r[r.length - 1].querySelector('.db-title-in').value;
    return /Zeile 0\d{3}/.test(last) && [...document.querySelectorAll('.ce-paragraph')].some((x) => x.textContent === 'Danach');
  }));
}

async function small(p) {
  const { D } = await makeDb(p, 40, 'Klein');
  await view(p, D, 'tab'); await p.open(D);
  ok('Kurze Tabelle (40): alle Zeilen im DOM wie bisher, ohne Platzhalter', (await rowsInDom(p)) === 40 && !(await p.$('tr.db-vpad')));
}

(async () => {
  const browser = await PW[ENGINE].launch({ headless: true, channel: ENGINE === 'chromium' ? (process.env.PW_CHANNEL || 'chrome') : undefined });
  try {
    const p = await session(browser);
    const t0 = Date.now(), { D, sum, st } = await makeDb(p, N, 'Lange DB ' + N);
    console.log('Datenbank mit ' + N + ' Zeilen angelegt (' + Math.round((Date.now() - t0) / 1000) + ' s)');
    await table(p, D, sum);
    await others(p, D, st);
    await embedded(p, D);
    await small(p);
    const m = await session(browser, { viewport: { width: 390, height: 844 }, mobile: true });
    await view(m, D, 'tab'); await m.open(D);
    const mm = await m.evaluate(() => ({ dom: document.querySelectorAll('tr.db-row').length, over: document.documentElement.scrollWidth - innerWidth }));
    ok('Handy: virtualisiert, Seite ohne seitlichen Ueberlauf', mm.dom < 140 && mm.over <= 1, mm);
    await m.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight)); await m.waitForTimeout(500);
    ok('Handy: Ende erreichbar', await m.evaluate(() => { const r = document.querySelectorAll('tr.db-row'); return /Zeile/.test(r[r.length - 1].querySelector('.db-title-in').value); }));
  } finally { await browser.close(); }
  const n = res.filter(Boolean).length;
  console.log('\n' + n + '/' + res.length + ' ' + (n === res.length ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})();
