// Saga 2.6 (Bugs-DB 2581) - Gedaechtnis im Web: Einstellungen > Konto > Gedaechtnis (static/gedaechtnis.js) und
// Admin > KI-Assistent > Embeddings (templates/admin/ai.html, static/admin.js). Vertrag docs/MEMORY.md 1.1/1.2.
// Geprueft: Bereich in der Navigation nach "Saga", Deep-Link ?settings=memory, Schalter aus/an, Datenbank binden und
// wechseln (erkannte Spalten: gefunden / fehlt / falscher Typ, fehlende Pflichtspalte "Inhalt"), Ich-Kern waehlen,
// ⓘ auf/zu, Ausschalten (Bindung weg, Datenbank bleibt), Rueckfall-Hinweis ohne Embeddings; Admin: Verbindung testen
// (Fake-Embedding), Speichern, Schluessel nie im Klartext (nur "gespeichert ✓", auch nicht im HTML/GET), Schluessel
// loeschen, keep_alive nur bei Ollama, Internet-Warnung (gespeichert und Vorab-Hinweis beim Tippen), Fehlerfall Endpunkt
// nicht erreichbar (Test und Konto-Stand); danach Stand "n von m Erinnerungen vorbereitet" bis alles vorbereitet ist, "Jetzt
// vorbereiten" (bei totem Dienst ehrliche Meldung), Rueckfrage beim Wechsel der Datenbank, Ich-Kern-Suche, Nachfragen ohne
// Neuaufbau. Ohne Skriptfehler, hell/dunkel, breit/schmal (390 px, kein waagrechtes Scrollen). Chromium und Firefox (BR=firefox).
// Stufe 3 (2601/2603): Spalte „Sensibel“ (fehlt, ⓘ), Nickerchen-Stand (Hinweis ohne Option „Schnappschuss“, danach Zahl
// und letzter), Schattenmodus-Schalter (an/aus, Server), Stand „n Verknüpfungen · m Spuren“, „Spuren löschen“ mit Rückfrage.
// Voraussetzung: tests/gedaechtnis_web_rig.py setup; tests/fakeai.py laeuft auf FAKE_PORT; auf DEAD_PORT lauscht nichts.
//   SAGA_BASE=http://127.0.0.1:8910 SAGA_ADMIN=http://127.0.0.1:8911 SAGA_ADMIN_COOKIE=... SAGA_USER=gw-web SAGA_PASS=...
//   FAKE_PORT=8915 DEAD_PORT=8919 [SHOTS=/tmp/x] [PRE=nachher] [VORHER=1] [BR=firefox] node tests/browser_gedaechtnis.js
// VORHER=1: nur Bilder vom Stand ohne Gedaechtnis-Oberflaeche (Einstellungen > Saga, Admin > KI-Assistent).
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync } = require('child_process');
const E = process.env, BASE = E.SAGA_BASE, ADM = E.SAGA_ADMIN, USER = E.SAGA_USER, PASS = E.SAGA_PASS, SHOTS = E.SHOTS || '';
const FF = E.BR === 'firefox', SFX = FF ? '-firefox' : '', PRE = E.PRE || (E.VORHER ? 'vorher' : 'nachher');
const FAKE = E.FAKE_PORT || '8915', DEAD = E.DEAD_PORT || '8919';
if (!BASE || !ADM || !USER || !PASS || !E.SAGA_ADMIN_COOKIE) { console.error('SAGA_BASE, SAGA_ADMIN, SAGA_ADMIN_COOKIE, SAGA_USER, SAGA_PASS setzen'); process.exit(2); }
const R = []; const ok = (n, c, x) => { R.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x).slice(0, 900))); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function rig(a) {                                   // tests/gedaechtnis_web_rig.py nap|spuren (gleiche DB wie der Server)
  const out = execFileSync('python', ['tests/gedaechtnis_web_rig.py', a], { encoding: 'utf8', env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }) });
  try { return JSON.parse(out.trim().split('\n').pop()); } catch (e) { return out; }
}
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 8000)) return v; await sleep(200); } }

(async () => {
  const br = await (FF ? PW.firefox : PW.chromium).launch(!FF && E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  const errs = [];
  async function ctxFor(o) {
    const ctx = await br.newContext({ viewport: o.narrow ? { width: 390, height: 844 } : { width: 1360, height: 900 },
      deviceScaleFactor: o.narrow ? 2 : 1, isMobile: !!o.narrow && !FF, hasTouch: !!o.narrow, locale: 'de-DE', colorScheme: o.dark ? 'dark' : 'light',
      extraHTTPHeaders: { 'Accept-Language': 'de' } });
    return ctx;
  }
  function watch(pg) { pg.on('pageerror', (e) => errs.push(e.message)); pg.on('console', (m) => { if (m.type() === 'error' && !/favicon|Failed to load resource/.test(m.text())) errs.push(m.text()); }); }
  async function login(ctx) {
    const p = await ctx.newPage(); watch(p);
    await p.goto(BASE + '/login'); await p.fill('input[name=username]', USER); await p.fill('input[name=password]', PASS);
    await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
    return p;
  }
  const api = (p, m, url, b) => p.evaluate(async ([m, url, b]) => {
    const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    const d = await r.json().catch(() => ({})); return { s: r.status, d: d };
  }, [m, url, b]);
  async function shot(pg, name, o) {
    if (!SHOTS || FF) return;
    await sleep(400);
    if (o && o.el) { const h = await pg.$(o.el); if (h) { await h.screenshot({ path: SHOTS + '/' + PRE + '-' + name + '.png' }); return; } }
    await pg.screenshot({ path: SHOTS + '/' + PRE + '-' + name + '.png', fullPage: !!(o && o.full) });
  }
  async function openSet(p, k) {
    await p.goto(BASE + '/dashboard?settings=' + k); await p.waitForSelector('.hk-dialog-set', { timeout: 8000 });
    await sleep(900);
  }
  async function admin(ctx) {
    const u = new URL(ADM);
    await ctx.addCookies([{ name: 'hk_admin', value: E.SAGA_ADMIN_COOKIE, domain: u.hostname, path: '/' }]);
    const a = await ctx.newPage(); watch(a);
    const r = await a.goto(ADM + '/settings/ai', { waitUntil: 'load' });
    a.__status = r.status();
    return a;
  }
  const hscroll = (pg) => pg.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

  // --- Vorbereitung: Sprache, Erste Schritte ---------------------------------------------------------------------------
  const ctx = await ctxFor({});
  const p = await login(ctx);
  await api(p, 'PUT', '/api/account/lang', { lang: 'de' });
  await api(p, 'POST', '/api/first-steps', { answer: 'no' });
  { const st = await api(p, 'GET', '/api/ai/status');
    if (st.d && st.d.intro && (!st.d.consent || st.d.consent.state !== 'granted')) await api(p, 'POST', '/api/ai/consent', { answer: 'no_ideas', version: st.d.intro.version, host: st.d.intro.host }); }

  if (E.VORHER) {
    for (const o of [{ n: 'hell' }, { n: 'dunkel', dark: true }, { n: 'schmal-hell', narrow: true }, { n: 'schmal-dunkel', narrow: true, dark: true }]) {
      const c = await ctxFor(o), q = await login(c);
      await openSet(q, 'saga'); await shot(q, 'einstellungen-ki-' + o.n);
      const a = await admin(c); await shot(a, 'admin-ki-' + o.n, { full: true });
      await c.close();
    }
    ok('Vorher-Bilder', true);
    await br.close();
    console.log(`\nGedaechtnis-Web vorher ${R.filter(Boolean).length}/${R.length}`);
    return;
  }

  const m0 = (await api(p, 'GET', '/api/account/memory')).d;
  const MEM = (m0.databases || []).find((x) => /^Erinnerungen \(Web-Test\)$/.test(x.title)), OTHER = (m0.databases || []).find((x) => /^Lesezeichen/.test(x.title));
  ok('Voraussetzung: nichts gebunden, beide Test-Datenbanken zur Auswahl', m0.db === null && m0.core === null && MEM && OTHER, m0);

  // --- 1. Einstellungen > Gedaechtnis: Navigation, aus --------------------------------------------------------------------
  await openSet(p, 'memory');
  const nav = await p.evaluate(() => [...document.querySelectorAll('.hk-setnav-i')].map((x) => x.dataset.k));
  ok('Navigation: „Gedächtnis“ direkt nach „Saga“ in der Gruppe Konto', nav.indexOf('memory') > 0 && nav.indexOf('memory') === nav.indexOf('saga') + 1, nav);
  const s0 = await p.evaluate(() => {
    const pane = document.getElementById('hk-pane-memory'), sw = document.getElementById('hk-mem-on');
    return { vis: pane && !pane.hidden, cur: (document.querySelector('.hk-setnav [aria-current="page"]') || {}).dataset, h: pane && pane.querySelector('h3').textContent,
      sw: sw && sw.checked, role: sw && sw.getAttribute('role'), sel: !!document.getElementById('hk-mem-db'), txt: pane && pane.textContent };
  });
  ok('Deep-Link ?settings=memory öffnet den Bereich „Gedächtnis“', s0.vis && s0.cur && s0.cur.k === 'memory' && s0.h === 'Gedächtnis', s0);
  ok('Ab Werk aus: Schalter aus (role=switch), keine Auswahl, Hinweis „Aus:“', s0.sw === false && s0.role === 'switch' && !s0.sel && /Aus: Dein Assistent/.test(s0.txt), s0);
  ok('Klartext: kein „Embedding“/„Vektor“ außerhalb von ⓘ', await p.evaluate(() => {
    const pane = document.getElementById('hk-pane-memory').cloneNode(true);
    pane.querySelectorAll('.hk-mem-ibox').forEach((x) => x.remove());
    return !/Embedding|Vektor/i.test(pane.textContent);
  }));
  // ⓘ der Einleitung
  await p.click('.hk-mem-intro .hk-mem-ib');
  const i1 = await p.evaluate(() => { const b = document.querySelector('.hk-mem-intro .hk-mem-ib'), box = document.getElementById(b.getAttribute('aria-controls'));
    return { exp: b.getAttribute('aria-expanded'), hid: box.hidden, txt: box.textContent, label: b.getAttribute('aria-label') }; });
  ok('ⓘ öffnet die Erklärung (aria-expanded, Fachwörter nur dort)', i1.exp === 'true' && !i1.hid && /Embedding/.test(i1.txt) && /memory/.test(i1.txt) && i1.label, i1);
  await p.click('.hk-mem-intro .hk-mem-ib');

  // --- 2. Einschalten, Datenbank binden ------------------------------------------------------------------------------------
  await p.click('label[for=hk-mem-on]');
  await sleep(200);
  const s1 = await p.evaluate(() => ({ focus: document.activeElement && document.activeElement.id, opts: [...document.getElementById('hk-mem-db').options].map((o) => o.textContent),
    core: !!document.getElementById('hk-mem-core') }));
  ok('Einschalten zeigt „Datenbank fürs Gedächtnis“ (Fokus) mit eigenen Datenbanken und „Ich-Kern-Seite“', s1.focus === 'hk-mem-db' && s1.core
     && s1.opts[0] === '– wählen –' && s1.opts.indexOf(MEM.title) > 0 && s1.opts.indexOf(OTHER.title) > 0, s1);
  ok('Nur Einschalten speichert nichts', (await api(p, 'GET', '/api/account/memory')).d.db === null);
  await p.selectOption('#hk-mem-db', String(MEM.id));
  await until(() => p.evaluate(() => !!document.querySelector('.hk-mem-cols')), 6000);
  ok('Fokus bleibt nach dem Binden im Feld „Datenbank fürs Gedächtnis“', await until(() => p.evaluate(() => document.activeElement && document.activeElement.id === 'hk-mem-db'), 3000));
  const cols = await p.evaluate(() => {
    const o = {}; document.querySelectorAll('.hk-mem-col').forEach((li) => { o[li.dataset.f] = { ok: li.classList.contains('ok'), name: li.querySelector('.hk-mem-cn').textContent, st: li.querySelector('.hk-mem-cs').textContent }; });
    return { o, msg: (document.querySelector('#hk-pane-memory .hk-status') || {}).textContent, warn: !!document.querySelector('#hk-pane-memory .hk-mem-warn'),
      fb: (document.querySelector('.hk-mem-fallback') || {}).textContent || '', state: (document.getElementById('hk-mem-state') || {}).textContent || '', h4: [...document.querySelectorAll('#hk-pane-memory h4')].map((h) => h.textContent) };
  });
  ok('Binden: „✓ Gespeichert“, Überschrift „Spalten in „Erinnerungen (Web-Test)““', /Gespeichert/.test(cols.msg) && cols.h4[0] === 'Spalten in „' + MEM.title + '“', cols);
  ok('Spalten: Inhalt, Schlagworte, Projekt, Art, Wichtigkeit, Datum, Letzter Abruf gefunden', ['inhalt', 'schlagworte', 'projekt', 'art', 'wichtigkeit', 'datum', 'letzter_abruf']
     .every((f) => cols.o[f] && cols.o[f].ok && cols.o[f].st === 'gefunden'), cols.o);
  ok('Spalten: Valenz/Erregung „fehlt“, Abrufe „falscher Typ – erwartet: Zahl“', cols.o.valenz.st === 'fehlt' && !cols.o.valenz.ok && cols.o.erregung.st === 'fehlt'
     && cols.o.abrufe.st === 'falscher Typ – erwartet: Zahl' && cols.o.abrufe.name === 'Abrufe', cols.o);
  ok('Ohne Embeddings: Rückfall-Hinweis „ähnliche Wörter“ und „4 Erinnerungen“, keine Warnung', /ähnlichen Wörtern/.test(cols.fb) && /^4 Erinnerungen/.test(cols.state) && !cols.warn, cols);
  const b1 = (await api(p, 'GET', '/api/account/memory')).d;
  ok('Server: Datenbank gebunden', b1.db && b1.db.id === MEM.id && b1.status.source === 'trigramm', b1);
  // ⓘ je Spalte
  await p.click('.hk-mem-col[data-f=abrufe] .hk-mem-ib');
  ok('ⓘ je Spalte erklärt sie („Abrufe“: Saga zählt …)', await p.evaluate(() => { const li = document.querySelector('.hk-mem-col[data-f=abrufe]'), bx = li.querySelector('.hk-mem-ibox');
    return !bx.hidden && /Saga zählt/.test(bx.textContent) && li.querySelector('.hk-mem-ib').getAttribute('aria-label') === 'Wozu „Abrufe“?'; }));
  // Ich-Kern
  await until(() => p.evaluate(() => document.getElementById('hk-mem-core').options.length > 1), 6000);
  const copts = await p.evaluate(() => [...document.getElementById('hk-mem-core').options].map((o) => o.textContent));
  ok('Ich-Kern-Auswahl: eigene Seiten, keine Datenbanken und keine Zeilen', copts[0] === '– keine –' && copts.indexOf('Marin – Ich-Kern (Web-Test)') > 0 && copts.indexOf('Projektnotizen (Web-Test)') > 0
     && copts.indexOf(MEM.title) < 0 && copts.indexOf('Tomaten') < 0 && copts.indexOf('Ein Lesezeichen') < 0, copts);
  ok('Ich-Kern-Auswahl: höchstens 300 Seiten (+ „– keine –“), Hinweis „Suche grenzt ein“, alte Seite fehlt zunächst', copts.length === 301
     && copts.indexOf('Alte Kernseite (Web-Test)') < 0 && /Suche grenzt ein/.test(await p.evaluate(() => document.querySelector('.hk-mem-coren').textContent)), copts.length);
  await p.fill('#hk-mem-core-q', 'alte kern');
  const cq = await until(() => p.evaluate(() => { const o = [...document.getElementById('hk-mem-core').options].map((x) => x.textContent); return o.length === 2 && o; }), 3000);
  ok('Suchfeld: „alte kern“ findet die alte Seite jenseits der 300 (Groß/Klein egal)', cq && cq[1] === 'Alte Kernseite (Web-Test)', cq);
  await shot(p, 'einstellungen-gedaechtnis-suche-hell', { el: '#hk-pane-memory' });
  await p.fill('#hk-mem-core-q', 'xyzxyz');
  ok('Suchfeld ohne Treffer: „Keine Seite passt zur Suche.“', !!await until(() => p.evaluate(() => /Keine Seite passt/.test(document.querySelector('.hk-mem-coren').textContent)), 3000));
  await p.fill('#hk-mem-core-q', 'Marin');
  await until(() => p.evaluate(() => document.getElementById('hk-mem-core').options.length === 2), 3000);
  const coreId = await p.evaluate(() => { const o = [...document.getElementById('hk-mem-core').options].find((x) => x.textContent === 'Marin – Ich-Kern (Web-Test)'); return o && o.value; });
  await p.selectOption('#hk-mem-core', coreId);
  await until(async () => ((await api(p, 'GET', '/api/account/memory')).d.core || {}).id === +coreId, 5000);
  const b2 = (await api(p, 'GET', '/api/account/memory')).d;
  ok('Ich-Kern gebunden (Server) und in der Auswahl', b2.core && b2.core.id === +coreId && await p.evaluate((v) => document.getElementById('hk-mem-core').value === v, coreId), b2.core);
  // 2588: passt nur der gebundene Ich-Kern zur Suche, darf nicht „Keine Seite passt“ dastehen
  await p.fill('#hk-mem-core-q', 'xyzxyz');
  await p.fill('#hk-mem-core-q', 'Marin');
  await new Promise((r) => setTimeout(r, 400));
  const hint2588 = await p.evaluate(() => document.querySelector('.hk-mem-coren').textContent);
  ok('Suche nach dem gebundenen Ich-Kern: kein „Keine Seite passt“ (2588)', !/Keine Seite passt/.test(hint2588), hint2588);
  await shot(p, 'einstellungen-gedaechtnis-hell');

  // --- 3. Wechseln: Rueckfrage (Pfeiltaste wechselt sofort), dann Datenbank ohne Spalte "Inhalt" -------------------------
  const puts = []; p.on('request', (r) => { if (r.method() === 'PUT' && /\/api\/account\/memory$/.test(r.url())) puts.push(r.url()); });
  const dbOpts = await p.evaluate(() => [...document.getElementById('hk-mem-db').options].map((o) => o.value));
  await p.focus('#hk-mem-db');
  await p.keyboard.press(dbOpts.indexOf(String(MEM.id)) < dbOpts.length - 1 ? 'ArrowDown' : 'ArrowUp');
  const ask = await until(() => p.evaluate(() => { const b = document.querySelector('.hk-ask .hk-ask-box'); return b && b.textContent; }), 4000);
  ok('Pfeiltaste in „Datenbank fürs Gedächtnis“: Rückfrage „Gedächtnis auf „…“ umstellen?“', ask && /Gedächtnis umstellen\?/.test(ask) && /neu vorbereitet/.test(ask), ask);
  await shot(p, 'einstellungen-gedaechtnis-rueckfrage-hell');
  await p.click('.hk-ask .hk-ask-bar button:not(.btn-primary)');
  await sleep(600);
  const back = await p.evaluate(() => ({ v: document.getElementById('hk-mem-db').value, dlg: !!document.querySelector('.hk-ask') }));
  const b3 = (await api(p, 'GET', '/api/account/memory')).d;
  ok('Abbrechen: Auswahl zurück, kein PUT, Bindung unverändert', back.v === String(MEM.id) && !back.dlg && !puts.length && b3.db.id === MEM.id, { back, puts, db: b3.db });
  await p.selectOption('#hk-mem-db', String(OTHER.id));
  await until(() => p.evaluate(() => !!document.querySelector('.hk-ask')), 4000);
  await p.click('.hk-ask .hk-ask-bar .btn-primary');
  await until(() => p.evaluate(() => !!document.querySelector('#hk-pane-memory .hk-mem-warn')), 6000);
  const w = await p.evaluate(() => ({ warn: document.querySelector('#hk-pane-memory .hk-mem-warn').textContent, inhalt: document.querySelector('.hk-mem-col[data-f=inhalt]').className,
    val: document.getElementById('hk-mem-db').value, core: document.getElementById('hk-mem-core').value }));
  ok('Wechseln auf Datenbank ohne „Inhalt“: Warnung (Pflichtspalte), Inhalt fehlt, Ich-Kern bleibt', /Ohne Text-Spalte „Inhalt“/.test(w.warn) && /miss/.test(w.inhalt) && w.val === String(OTHER.id) && w.core === coreId, w);
  ok('Server: neue Datenbank, missing_required', (await api(p, 'GET', '/api/account/memory')).d.missing_required.join() === 'inhalt');
  await p.selectOption('#hk-mem-db', String(MEM.id));
  await until(() => p.evaluate(() => !!document.querySelector('.hk-ask')), 4000);
  await p.click('.hk-ask .hk-ask-bar .btn-primary');
  await until(() => p.evaluate(() => !document.querySelector('#hk-pane-memory .hk-mem-warn')), 6000);

  // --- 4. Ausschalten ------------------------------------------------------------------------------------------------------
  await p.click('label[for=hk-mem-on]');
  await until(() => p.evaluate(() => /Ausgeschaltet/.test((document.querySelector('#hk-pane-memory .hk-status') || {}).textContent || '')), 5000);
  const off = (await api(p, 'GET', '/api/account/memory')).d;
  const offUi = await p.evaluate(() => ({ sw: document.getElementById('hk-mem-on').checked, sel: !!document.getElementById('hk-mem-db'), focus: document.activeElement && document.activeElement.id }));
  ok('Ausschalten: Bindung weg (db/core null), Datenbank noch da, Schalter aus mit Fokus', off.db === null && off.core === null && off.databases.some((x) => x.id === MEM.id)
     && !offUi.sw && !offUi.sel && offUi.focus === 'hk-mem-on', { off, offUi });

  // --- 5. Admin > KI-Assistent > Embeddings -------------------------------------------------------------------------------
  const a = await admin(ctx);
  ok(`Admin: Seite lädt (${a.__status}), Karte „Embeddings“`, a.__status === 200 && await a.evaluate(() => { const f = document.getElementById('embeddings'); return f && f.querySelector('h3').textContent === 'Embeddings'; }));
  const a0 = await a.evaluate(() => ({ hint: document.getElementById('emb-key-hint').textContent, clear: document.getElementById('emb-clear').hidden,
    net: document.getElementById('emb-where-net').hidden, keep: document.getElementById('emb-keep').hidden, prov: document.querySelector('#embeddings [name=provider]').value,
    model: document.querySelector('#embeddings [name=model]').value, en: document.querySelector('#embeddings [name=enabled]').checked }));
  ok('Ab Werk: aus, Ollama mit bge-m3, keep_alive sichtbar, kein Schlüssel, keine Internet-Warnung', !a0.en && a0.prov === 'ollama' && a0.model === 'bge-m3' && !a0.keep
     && /optional/.test(a0.hint) && a0.clear && a0.net, a0);
  await a.selectOption('#embeddings [name=provider]', 'openai');
  ok('keep_alive nur bei Ollama (OpenAI-kompatibel: verborgen)', await a.evaluate(() => document.getElementById('emb-keep').hidden));
  // Vorab-Hinweis beim Tippen
  await a.fill('#embeddings [name=url]', 'https://api.openai.com/v1');
  ok('Tippen: Adresse im Internet -> Vorab-Hinweis', await a.evaluate(() => !document.getElementById('emb-where-maybe').hidden));
  await a.fill('#embeddings [name=url]', 'http://ollama.lan:11434');
  ok('Tippen: Adresse im Heimnetz -> kein Hinweis', await a.evaluate(() => document.getElementById('emb-where-maybe').hidden));
  // Fehlerfall: Endpunkt nicht erreichbar
  await a.fill('#embeddings [name=url]', `http://127.0.0.1:${DEAD}/v1`);
  await a.fill('#embeddings [name=model]', 'fake-embed');
  await a.click('#emb-test');
  const t1 = await until(() => a.evaluate(() => { const o = document.getElementById('emb-out'); return !o.hidden && /✗/.test(o.textContent) && { t: o.textContent, c: o.className }; }), 12000);
  ok('Verbindung testen, Endpunkt nicht erreichbar: ✗ mit Klartext, Darstellung Fehler', t1 && /nicht erreichbar|antwortet nicht/.test(t1.t) && /err/.test(t1.c), t1);
  // Erfolgreicher Test + Speichern mit Schluessel
  await a.fill('#embeddings [name=url]', `http://127.0.0.1:${FAKE}/v1`);
  await a.click('#emb-test');
  const t2 = await until(() => a.evaluate(() => { const o = document.getElementById('emb-out'); return /Dimensionen/.test(o.textContent) && o.textContent; }), 12000);
  ok('Verbindung testen mit Fake-Embedding: „✓ fake-embed: 256 Dimensionen“', t2 && /✓ fake-embed: 256 Dimensionen/.test(t2), t2);
  await a.check('#embeddings [name=enabled]');
  await a.fill('#embeddings [name=api_key]', 'sk-test-123');
  await a.click('#emb-save');
  const sv = await until(() => a.evaluate(() => /Gespeichert/.test(document.getElementById('emb-out').textContent) && {
    hint: document.getElementById('emb-key-hint').textContent, key: document.querySelector('#embeddings [name=api_key]').value, clear: document.getElementById('emb-clear').hidden,
    home: !document.getElementById('emb-where-home').hidden, net: !document.getElementById('emb-where-net').hidden }), 10000);
  ok('Speichern: ✓, Schlüssel „gespeichert ✓“, Feld leer, „Schlüssel löschen“ sichtbar, Heimnetz-Hinweis', sv && /gespeichert ✓/.test(sv.hint) && sv.key === '' && !sv.clear && sv.home && !sv.net, sv);
  await a.reload(); await sleep(300);
  const html = await a.content();
  const g = await a.evaluate(async () => { const r = await fetch('/api/ai/embed'); return r.text(); });
  ok('Schlüssel nie im Klartext (HTML der Seite, GET /api/ai/embed: has_key)', !html.includes('sk-test-123') && !g.includes('sk-test-123') && JSON.parse(g).settings.has_key === true);
  ok('Nach Neuladen: Werte gespeichert (an, OpenAI-kompatibel, fake-embed)', await a.evaluate(() => document.querySelector('#embeddings [name=enabled]').checked
     && document.querySelector('#embeddings [name=provider]').value === 'openai' && document.querySelector('#embeddings [name=model]').value === 'fake-embed'));
  // Internet-Warnung nach Speichern (nur Aufloesen der Adresse, keine Verbindung). Bewusst eine oeffentliche IP: die
  // Dokumentations-Adressen aus RFC 5737 (192.0.2.x usw.) gelten in Python (ipaddress.is_global) als nicht oeffentlich und
  // ergaeben "Heimnetz". Beim Speichern loest Saga nur auf - zu 1.1.1.1 entsteht keine Verbindung (kein Test-Knopf).
  await a.fill('#embeddings [name=url]', 'https://1.1.1.1/v1');
  await a.click('#emb-save');
  const nt = await until(() => a.evaluate(() => /Gespeichert/.test(document.getElementById('emb-out').textContent) && !document.getElementById('emb-where-net').hidden
    && document.getElementById('emb-where-net').textContent), 10000);
  ok('Endpunkt im Internet gespeichert: Warnung „Erinnerungen verlassen dann den Server“', nt && /Erinnerungen verlassen dann den Server/.test(nt), nt);
  if (SHOTS && !FF) { await a.evaluate(() => document.getElementById('embeddings').scrollIntoView()); await shot(a, 'admin-embeddings-internet-hell', { el: '#embeddings' }); }
  // Schluessel loeschen, zurueck auf das Fake-Embedding
  await a.fill('#embeddings [name=url]', `http://127.0.0.1:${FAKE}/v1`);
  await a.check('#embeddings [name=clear_key]');
  await a.click('#emb-save');
  const cl = await until(() => a.evaluate(() => /Gespeichert/.test(document.getElementById('emb-out').textContent) && /optional/.test(document.getElementById('emb-key-hint').textContent)
    && document.getElementById('emb-clear').hidden && document.getElementById('emb-where-net').hidden), 10000);
  ok('Schlüssel löschen: Hinweis wieder „optional“, Internet-Warnung weg', !!cl);
  ok('Server: kein Schlüssel mehr', JSON.parse(await a.evaluate(async () => (await fetch('/api/ai/embed')).text())).settings.has_key === false);
  await shot(a, 'admin-embeddings-hell', { el: '#embeddings' });

  // --- 6. Konto mit Embeddings: Stand "n von m eingebettet" ------------------------------------------------------------------
  await sleep(5600);                                           // Einstellungen sind im Server 5 s zwischengespeichert
  await openSet(p, 'memory');
  await p.click('label[for=hk-mem-on]'); await sleep(150);
  await p.selectOption('#hk-mem-db', String(MEM.id));
  const full = await until(() => p.evaluate(() => { const s = document.getElementById('hk-mem-state'); return s && /^4 von 4 Erinnerungen vorbereitet/.test(s.textContent) && s.textContent; }), 20000);
  ok('Stand: „4 von 4 Erinnerungen vorbereitet“ (Fortschritt ohne Neuladen)', !!full, await p.evaluate(() => (document.getElementById('hk-mem-state') || {}).textContent));
  ok('Mit Embeddings kein Rückfall-Hinweis, Knopf „Jetzt vorbereiten“', await p.evaluate(() => !document.querySelector('.hk-mem-fallback') && (document.getElementById('hk-mem-embed') || {}).textContent === 'Jetzt vorbereiten'));
  ok('Klartext mit Embeddings: kein „Embedding“/„Vektor“/„einbetten“ außerhalb von ⓘ', await p.evaluate(() => {
    const pane = document.getElementById('hk-pane-memory').cloneNode(true);
    pane.querySelectorAll('.hk-mem-ibox').forEach((x) => x.remove());
    return !/Embedding|Vektor|einbett|eingebettet/i.test(pane.textContent);
  }));
  await p.click('#hk-mem-state .hk-mem-ib');
  ok('ⓘ am Stand nennt das Modell', await p.evaluate(() => { const b = document.querySelector('#hk-mem-state .hk-mem-ib'); return /fake-embed/.test(document.getElementById(b.getAttribute('aria-controls')).textContent); }));
  // Nachfragen baut nicht neu: offene ⓘ-Erklärung bleibt offen (dasselbe Element)
  await p.evaluate(() => { const li = document.querySelector('.hk-mem-col[data-f=abrufe]'); li.querySelector('.hk-mem-ibox').dataset.mark = 'offen'; });
  await p.click('.hk-mem-col[data-f=abrufe] .hk-mem-ib');
  await p.click('#hk-mem-embed');
  ok('„Jetzt vorbereiten“: Rückmeldung „✓ Wird vorbereitet“', !!await until(() => p.evaluate(() => /✓ Wird vorbereitet/.test((document.querySelector('#hk-pane-memory .hk-status') || {}).textContent || '')), 6000));
  await sleep(3800);                                           // mindestens eine Nachfrage (alle 3 s)
  ok('Nachfragen: offene ⓘ bleibt offen (Bereich nicht neu gebaut)', await p.evaluate(() => { const b = document.querySelector('.hk-mem-ibox[data-mark=offen]'); return !!b && b.isConnected && !b.hidden; }));
  await p.selectOption('#hk-mem-core', coreId); await sleep(600);
  await shot(p, 'einstellungen-gedaechtnis-eingebettet-hell');

  // --- 7. Fehlerfall im Konto: anderes Modell, Endpunkt nicht erreichbar ---------------------------------------------------------
  await a.fill('#embeddings [name=url]', `http://127.0.0.1:${DEAD}/v1`);
  await a.fill('#embeddings [name=model]', 'fake-embed-b');
  await a.click('#emb-save');
  await until(() => a.evaluate(() => /Gespeichert/.test(document.getElementById('emb-out').textContent)), 10000);
  await sleep(5600);
  await openSet(p, 'memory');
  const er = await until(() => p.evaluate(() => { const s = document.getElementById('hk-mem-state'); return s && /^0 von 4/.test(s.textContent) && !document.querySelector('.hk-mem-run:not([hidden])')
    && document.getElementById('hk-pane-memory').textContent; }), 20000);
  ok('Endpunkt weg (anderes Modell): „0 von 4“, Hinweis „sucht Saga solange nach ähnlichen Wörtern“', er && /solange nach ähnlichen Wörtern/.test(er), er && er.slice(-400));
  await p.click('#hk-mem-embed');
  const de = await until(() => p.evaluate(() => { const m = (document.querySelector('#hk-pane-memory .hk-status') || {}).textContent || '';
    return /nicht erreichbar – bitte später/.test(m) && { m, dis: document.getElementById('hk-mem-embed').disabled, warn: !!document.querySelector('#hk-pane-memory .hk-mem-warn:not([hidden])') }; }), 8000);
  ok('„Jetzt vorbereiten“ ohne erreichbaren Dienst: kein ✓, ehrliche Meldung, Knopf wieder frei, Warnung', de && !/✓/.test(de.m) && !de.dis && de.warn, de);
  await shot(p, 'einstellungen-gedaechtnis-fehler-hell');
  // zurueck auf das erreichbare Fake-Embedding fuer die Bilder
  await a.fill('#embeddings [name=url]', `http://127.0.0.1:${FAKE}/v1`);
  await a.fill('#embeddings [name=model]', 'fake-embed');
  await a.click('#emb-save');
  await until(() => a.evaluate(() => /Gespeichert/.test(document.getElementById('emb-out').textContent)), 10000);
  await sleep(5600);

  // --- 8. hell/dunkel, breit/schmal -------------------------------------------------------------------------------------------
  for (const o of [{ n: 'dunkel', dark: true }, { n: 'schmal-hell', narrow: true }, { n: 'schmal-dunkel', narrow: true, dark: true }]) {
    const c = await ctxFor(o), q = await login(c);
    await openSet(q, 'memory');
    await until(() => q.evaluate(() => { const s = document.getElementById('hk-mem-state'); return s && /4 von 4/.test(s.textContent); }), 15000);
    const lay = await q.evaluate(() => {
      const pane = document.getElementById('hk-pane-memory'), r = pane.getBoundingClientRect(), b = pane.closest('.hk-dlg-body');
      const over = [...pane.querySelectorAll('*')].filter((x) => { const q = x.getBoundingClientRect(); return q.width && q.right > r.right + 1; }).map((x) => x.className).slice(0, 5);
      return { vis: !pane.hidden, over, scroll: b.scrollWidth - b.clientWidth };
    });
    ok(`${o.n}: Bereich sichtbar, nichts ragt heraus, kein waagrechtes Scrollen`, lay.vis && !lay.over.length && lay.scroll <= 1 && (await hscroll(q)) <= 1, lay);
    if (o.narrow) {
      // Schalter: Ziel ist die ganze Zeile (label for=); Knoepfe wie ueberall in den Einstellungen (.btn)
      const tgt = await q.evaluate(() => [...document.querySelectorAll('#hk-pane-memory .hk-mem-ib, label[for=hk-mem-on], #hk-mem-db, #hk-mem-core, #hk-mem-embed')]
        .map((x) => { const r = x.getBoundingClientRect(); return [x.id || x.className, Math.round(r.width), Math.round(r.height)]; }).filter((x) => x[2] < 30));
      ok(`${o.n}: Bedienelemente mindestens 30 px hoch (ⓘ am Touchgeraet 44 px)`, !tgt.length, tgt);
    }
    await shot(q, 'einstellungen-gedaechtnis-' + o.n);
    if (o.narrow) {
      await q.evaluate(() => { const b = document.querySelector('.hk-dialog-set .hk-dlg-body'); b.scrollTop = b.scrollHeight; });
      await shot(q, 'einstellungen-gedaechtnis-' + o.n + '-unten');
    }
    const aa = await admin(c);
    const al = await aa.evaluate(() => { const f = document.getElementById('embeddings'), r = f.getBoundingClientRect();
      return { w: r.width, over: [...f.querySelectorAll('*')].filter((x) => { const q = x.getBoundingClientRect(); return q.width && q.right > r.right + 1; }).map((x) => x.tagName + '.' + x.className).slice(0, 5) }; });
    ok(`${o.n}: Admin-Karte ohne Überstand, kein waagrechtes Scrollen`, !al.over.length && (await hscroll(aa)) <= 1, al);
    await shot(aa, 'admin-embeddings-' + o.n, { el: '#embeddings' });
    await c.close();
  }

  // --- 9. Einstellungen > API: Token-Bereich "memory" heisst "Gedaechtnis" ------------------------------------------------
  await openSet(p, 'api');
  const sc = await until(() => p.evaluate(() => { const pane = document.getElementById('hk-pane-api'); if (!pane) return null;
    const ls = [...pane.querySelectorAll('label')].map((l) => l.textContent.trim()); return ls.length ? ls : null; }), 6000);
  ok('API-Token: Bereich „memory“ beschriftet als „Gedächtnis“', sc && sc.indexOf('Gedächtnis') >= 0 && sc.indexOf('memory') < 0, sc);

  // --- 10. Stufe 3: Sensibel, Nickerchen, Schattenmodus (2601/2603) --------------------------------------------------------
  const msgOf = () => p.evaluate(() => (document.querySelector('#hk-pane-memory .hk-status') || {}).textContent || '');
  const z = () => p.evaluate(() => {
    const li = document.querySelector('.hk-mem-col[data-f=sensibel]'), nap = document.getElementById('hk-mem-nap'), sw = document.getElementById('hk-mem-shadow');
    const sst = document.getElementById('hk-mem-shadow-state'), fg = document.getElementById('hk-mem-forget');
    return { sens: li && { cls: li.className, name: li.querySelector('.hk-mem-cn').textContent, st: li.querySelector('.hk-mem-cs').textContent },
      nap: nap && nap.textContent, napWarn: !!nap && nap.classList.contains('hk-mem-warn'), sw: sw && sw.checked, swRole: sw && sw.getAttribute('role'),
      sst: sst && sst.textContent, sstHid: !sst || sst.hidden, fgHid: !fg || fg.closest('.hk-actions').hidden,
      run: !!document.querySelector('.hk-mem-shadow-run:not([hidden])'), h4: [...document.querySelectorAll('#hk-pane-memory h4')].map((h) => h.textContent) };
  });
  await openSet(p, 'memory');
  await until(() => p.evaluate(() => !!document.getElementById('hk-mem-nap')), 8000);
  const z0 = await z();
  ok('Stufe 3: Abschnitte „Nickerchen“ und „Schattenmodus (Probelauf)“', z0.h4.indexOf('Nickerchen') > 0 && z0.h4.indexOf('Schattenmodus (Probelauf)') > z0.h4.indexOf('Nickerchen'), z0.h4);
  ok('Spalte „Sensibel“: fehlt (kein Fehler)', z0.sens && z0.sens.name === 'Sensibel' && z0.sens.st === 'fehlt' && /miss/.test(z0.sens.cls), z0.sens);
  await p.click('.hk-mem-col[data-f=sensibel] .hk-mem-ib');
  const si = await p.evaluate(() => { const li = document.querySelector('.hk-mem-col[data-f=sensibel]'), bx = li.querySelector('.hk-mem-ibox');
    return { hid: bx.hidden, t: bx.textContent, lab: li.querySelector('.hk-mem-ib').getAttribute('aria-label') }; });
  ok('ⓘ an „Sensibel“: Kontrollkästchen, „nur, wenn direkt danach gefragt wird – nie über Verknüpfungen“', !si.hid && /Kontrollkästchen/.test(si.t) && /direkt danach gefragt/.test(si.t) && /nie über Verknüpfungen/.test(si.t) && si.lab === 'Wozu „Sensibel“?', si);
  await p.click('.hk-mem-col[data-f=sensibel] .hk-mem-ib');
  ok('Nickerchen ohne Option: Hinweis „Lege in der Spalte „Art“ die Option „Schnappschuss“ an …“', z0.napWarn && /^Lege in der Spalte „Art“ die Option „Schnappschuss“ an, damit dein Assistent Schnappschüsse ablegen kann\.$/.test(z0.nap), z0);
  ok('Schattenmodus ab Werk aus (role=switch), kein Stand, kein „Spuren löschen“', z0.sw === false && z0.swRole === 'switch' && z0.sstHid && z0.fgHid, z0);
  await shot(p, 'einstellungen-gedaechtnis-stufe3-hell', { el: '#hk-pane-memory' });
  const rn = rig('nap');
  ok('Rig: Option „Schnappschuss“ + ein Schnappschuss', rn && rn.status === 201, rn);
  await openSet(p, 'memory');
  await until(() => p.evaluate(() => !!document.getElementById('hk-mem-nap')), 8000);
  const z1 = await z();
  ok('Nickerchen: „1 Schnappschuss · zuletzt …“ (ohne Hinweis)', !z1.napWarn && /^1 Schnappschuss · zuletzt \S/.test(z1.nap), z1.nap);
  // Schattenmodus an
  await p.click('label[for=hk-mem-shadow]');
  ok('Einschalten: „✓ Schattenmodus an – Saga baut die Verknüpfungen auf.“', !!await until(async () => /✓ Schattenmodus an/.test(await msgOf()), 6000), await msgOf());
  const sh1 = (await api(p, 'GET', '/api/account/memory')).d.shadow;
  ok('Server: Schattenmodus an (since gesetzt)', sh1 && sh1.on === true && !!sh1.since, sh1);
  ok('Fokus bleibt auf dem Schalter', await until(() => p.evaluate(() => document.activeElement && document.activeElement.id === 'hk-mem-shadow'), 3000));
  const built = await until(async () => { const x = await z(); const d = (await api(p, 'GET', '/api/account/memory')).d.shadow; return !x.run && !d.running && x; }, 20000);
  ok('Stand nach dem Aufbau: „n Verknüpfungen · 0 Spuren (30 Tage)“, „Wird aufgebaut …“ weg', built && /^\d+ Verknüpfung(en)? · 0 Spuren \(30 Tage\)$/.test(built.sst) && !built.sstHid && built.fgHid, built);
  const links1 = (await api(p, 'GET', '/api/account/memory')).d.shadow.links;
  rig('spuren');
  await openSet(p, 'memory');
  await until(() => p.evaluate(() => !!document.getElementById('hk-mem-shadow-state')), 8000);
  const z2 = await z();
  ok('Mit Spuren: „… · 2 Spuren (30 Tage)“, Knopf „Spuren löschen“ sichtbar', z2.sw === true && / · 2 Spuren \(30 Tage\)$/.test(z2.sst) && !z2.fgHid, z2);
  await shot(p, 'einstellungen-gedaechtnis-schatten-hell', { el: '#hk-pane-memory' });
  await p.click('#hk-mem-forget');
  const fa = await until(() => p.evaluate(() => { const b = document.querySelector('.hk-ask .hk-ask-box'); return b && b.textContent; }), 4000);
  ok('„Spuren löschen“: Rückfrage „2 Spuren des Schattenmodus jetzt löschen? Die Verknüpfungen bleiben.“', fa && /Spuren löschen\?/.test(fa) && /2 Spuren des Schattenmodus jetzt löschen\? Die Verknüpfungen bleiben\./.test(fa), fa);
  await shot(p, 'einstellungen-gedaechtnis-spuren-rueckfrage-hell');
  await p.click('.hk-ask .hk-ask-bar button:not(.btn-destr)'); await sleep(500);
  ok('Abbrechen: Spuren bleiben', (await api(p, 'GET', '/api/account/memory')).d.shadow.traces === 2);
  await p.click('#hk-mem-forget');
  await until(() => p.evaluate(() => !!document.querySelector('.hk-ask .btn-destr')), 4000);
  await p.click('.hk-ask .hk-ask-bar .btn-destr');
  ok('Löschen: „✓ Spuren gelöscht“', !!await until(async () => /✓ Spuren gelöscht/.test(await msgOf()), 6000), await msgOf());
  const sh2 = (await api(p, 'GET', '/api/account/memory')).d.shadow, z3 = await z();
  ok('Server: 0 Spuren, Verknüpfungen bleiben, Modus bleibt an; Knopf weg', sh2.traces === 0 && sh2.links === links1 && sh2.on && / · 0 Spuren/.test(z3.sst) && z3.fgHid, [sh2, z3]);
  // aus
  await p.click('label[for=hk-mem-shadow]');
  ok('Ausschalten: „✓ Schattenmodus aus – Verknüpfungen und Spuren bleiben.“', !!await until(async () => /✓ Schattenmodus aus/.test(await msgOf()), 6000), await msgOf());
  const sh3 = (await api(p, 'GET', '/api/account/memory')).d.shadow;
  ok('Server: aus, Verknüpfungen bleiben', sh3.on === false && sh3.links === links1, sh3);
  ok('Klartext Stufe 3: kein „Embedding“/„Vektor“ außerhalb von ⓘ', await p.evaluate(() => {
    const pane = document.getElementById('hk-pane-memory').cloneNode(true);
    pane.querySelectorAll('.hk-mem-ibox').forEach((x) => x.remove());
    return !/Embedding|Vektor|einbett|eingebettet/i.test(pane.textContent);
  }));

  // --- Aufraeumen der Bindung ------------------------------------------------------------------------------------------------
  await api(p, 'PUT', '/api/account/memory', { db: null, core: null });
  ok('Keine Skriptfehler', !errs.length, errs);
  await br.close();
  const n = R.filter(Boolean).length;
  console.log(`\nGedaechtnis-Web${FF ? ' (Firefox)' : ''} ${n}/${R.length}`, R.length && n === R.length ? '- ALLES GRUEN' : '- FEHLER');
  process.exit(R.length && n === R.length ? 0 : 1);
})().catch((e) => { console.error('ABBRUCH', e); process.exit(1); });
