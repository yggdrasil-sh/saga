// Bugs-DB 2361: "/" -> "Widget: Diagramm" auf einer Seite, die schon Diagramm-Widgets hat (Spalten: links drei Diagramme,
// rechts Schaltflaechen + eingebettete Datenbank, darunter ein Absatz auf oberster Ebene). Erwartet: Klick auf den Eintrag
// legt ein NEUES Widget an (der Absatz "/di" ist weg), das Einstellungs-Panel geht am neuen Widget auf, die vorhandenen
// Widgets bleiben unveraendert und ohne Panel. Auch: Enter statt Klick, eine andere Widget-Art (Uhr) mit nur einem
// vorhandenen Widget, und das Neuladen einer Seite mit vorhandenen Widgets oeffnet kein Panel.
// Tiptap-Editor (Standard seit M3), Chromium und WebKit (Safari; PW_BROWSERS=chromium,webkit), je ohne und mit
// "gleichzeitig bearbeiten" (notes.live; WS_LIVE=0 laesst den Live-Durchgang weg - der setzt den Schalter per Python in
// der DB des Laufs, also nur in der Testumgebung mit DATABASE_URL).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Sitzungs-Cookie hk-test-a> node tests/browser_widget_slash_neu.js
process.env.SAGA_EDITOR = process.env.SAGA_EDITOR || 'tiptap';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const path = require('path');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK, SHOTS = process.env.SHOTS || '';
const ENGINES = (process.env.PW_BROWSERS || 'chromium,webkit').split(',').map((s) => s.trim()).filter(Boolean);
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const LIVE = process.env.WS_LIVE !== '0' && !!process.env.DATABASE_URL;
function setLive(on) {                               // Admin-Schalter notes.live (Einstellungen 5 s zwischengespeichert)
  require('child_process').execFileSync('python', ['-c', 'import auth, sys; n = dict(auth.settings(fresh=True).get("notes") or {}); n["live"] = sys.argv[1] == "1"; auth.save_setting("notes", n)', on ? '1' : '0'],
    { env: Object.assign({}, process.env, { PYTHONPATH: 'tests/pgshim:.' }), stdio: 'ignore' });
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, ms) { const t0 = Date.now(); for (;;) { const v = await fn(); if (v || Date.now() - t0 > (ms || 6000)) return v; await sleep(150); } }
const P = (bid, t) => ({ id: bid, type: 'paragraph', data: { text: t || '' } });
const W = (bid, kind, cfg, t) => ({ id: bid, type: 'widget', data: { v: 1, kind: kind, cfg: cfg, title: t } });

// Zustand nach dem Klick: Widgets (ID, Art, Panel?), wem das Panel gehoert, Absatztexte der obersten Ebene
const STATE = () => {
  const ws = [...document.querySelectorAll('#pg-editor [data-block-type="widget"]')].map((x) => ({ id: x.getAttribute('data-block-id'),
    kind: (x.querySelector('.wg') || {}).getAttribute ? x.querySelector('.wg').getAttribute('data-kind') : null,
    panel: !!x.querySelector('.wg-panel') || !!x.querySelector('.wg-blk.wg-editing') }));
  const owner = ws.filter((w) => w.panel).map((w) => w.id);
  const panels = document.querySelectorAll('.wg-panel').length;
  const doc = window.HKPages && HKPages.k.doc ? HKPages.k.doc.blocks() : [];
  const tops = doc.filter((b) => b.parent == null).map((b) => b.type + ':' + (b.type === 'paragraph' ? (b.el.textContent || '').trim() : ''));
  return { ws, owner, panels, tops, mode: window.HKPmLive ? HKPmLive.state().mode : null, menu: !!document.querySelector('.pm-pop:not([hidden])') };
};

(async () => {
  const RUNS = [];
  for (const eng of ENGINES) { RUNS.push([eng, false]); if (LIVE) RUNS.push([eng, true]); }
  let liveNow = false;
  for (const [eng0, live] of RUNS) {
    const eng = eng0 + (live ? '/live' : '');
    if (live !== liveNow) { setLive(live); liveNow = live; await sleep(5500); }
    let br;
    try { br = await PW[eng0].launch(eng0 === 'chromium' && process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}); }
    catch (e) { ok(`${eng}: Browser startet`, false, String(e.message).split('\n')[0]); continue; }
    const ctx = await br.newContext({ viewport: { width: 1280, height: 900 }, locale: 'de-DE' });
    await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
    const p = await ctx.newPage(); const errs = [];
    p.on('pageerror', (e) => errs.push(e.message));
    const rejects = [];                                 // live: abgelehnte Updates (pm-live-Protokoll mit HKPmLive.debug)
    p.on('console', (m) => { const t = m.text(); if (/^pm-live reject/.test(t)) rejects.push(t); });
    await p.goto(BASE + '/notes');
    await D.useEditor(p);
    const api = (m, url, b) => p.evaluate(async ([m, url, b]) => {
      const r = await fetch(url, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.json().catch(() => ({}));
    }, [m, url, b]);
    const body = async (id) => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{"blocks":[]}').blocks;
    const flat = (bl, out) => { (bl || []).forEach((b) => { out.push(b); if (b.type === 'columns') (b.data.cols || []).forEach((c) => flat(c.blocks, out)); }); return out; };

    const db = (await api('POST', '/api/notes', { title: 'WS ' + eng + ' Ausgaben', kind: 'database' })).id;
    await api('PATCH', '/api/notes/' + db, { schema: { props: [
      { id: 'kat', name: 'Kategorie', type: 'select', options: [{ id: 'essen', name: 'Essen', color: 'red' }, { id: 'reise', name: 'Reise', color: 'blue' }] },
      { id: 'betrag', name: 'Betrag', type: 'number' }], views: [{ id: 'tab', name: 'Tabelle', type: 'table' }] } });
    for (const [t, k, b] of [['Pizza', 'essen', 12], ['Zug', 'reise', 40], ['Brot', 'essen', 3]]) await api('POST', '/api/notes', { title: t, parent_id: db, props: { kat: k, betrag: b } });
    const CH = (bid, type, t) => W(bid, 'chart', { db: db, view: '', type: type, x: 'kat', agg: 'count' }, t);
    const BTN = { id: 'bt', type: 'buttons', data: { buttons: [{ label: 'Neu', icon: '✅' }, { label: 'Journal' }] } };
    const DBE = { id: 'dbe', type: 'database', data: { db: db, view: null, views: null } };
    const LAYOUT = [{ id: 'cc', type: 'columns', data: { cols: [{ blocks: [CH('c1', 'bar', 'Eins'), CH('c2', 'donut', 'Zwei'), CH('c3', 'hbar', 'Drei')] }, { blocks: [BTN, DBE] }] } }, P('pz')];
    const OLD = ['c1', 'c2', 'c3'];

    // Seite anlegen, oeffnen, im Absatz "pz" den Suchtext tippen; pick: 'click' (Maus auf den Eintrag) oder 'enter'
    async function slashCase(tag, blocks, q, title, pick, kind, old, edit) {
      const id = (await api('POST', '/api/notes', { title: 'WS ' + eng + ' ' + tag })).id;
      await api('PATCH', '/api/notes/' + id, { format: 'blocks', body: JSON.stringify({ blocks }) });
      const before = flat(await body(id), []).filter((b) => b.type === 'widget').map((b) => JSON.stringify(b.data));
      await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
      await until(() => p.evaluate((n) => document.querySelectorAll('#pg-editor [data-block-type="widget"] .wg').length >= n, old.length), 10000);
      if (blocks.some((b) => b.type === 'columns')) await until(() => p.locator('#pg-editor [data-block-type="database"] .db-bar, #pg-editor [data-block-type="database"] table').count(), 8000);
      if (live) {
        const m = await until(() => p.evaluate(() => window.HKPmLive && HKPmLive.state().mode === 'live'), 10000);
        ok(`${eng} ${tag}: Seite live verbunden`, !!m, await p.evaluate(() => window.HKPmLive ? HKPmLive.state().mode : null));
        await until(() => p.evaluate((n) => document.querySelectorAll('#pg-editor [data-block-type="widget"] .wg').length >= n, old.length), 10000);
      }
      await sleep(900);
      const s0 = await p.evaluate(STATE);
      ok(`${eng} ${tag}: nach dem Laden kein Panel offen (${s0.panels})`, s0.panels === 0, s0);
      if (live) await p.evaluate(() => { HKPmLive.debug = true; });
      const rj0 = rejects.length;
      await p.locator(D.text('pz')).first().click(); await sleep(150);
      await p.keyboard.type(q, { delay: 40 }); await sleep(500);
      const it = p.locator(`.pm-pop:not([hidden]) .hk-emo-i:has(.hk-at-t:text-is("${title}"))`).first();
      const seen = await until(() => it.count(), 4000);
      ok(`${eng} ${tag}: "${title}" im Menue`, !!seen, await p.evaluate((s) => [...document.querySelectorAll(s)].map((x) => x.textContent.trim()), D.UI_ITEMS));
      if (pick === 'click') {                            // echte Maus (mousedown/mouseup/click) auf die Mitte des Eintrags
        const bb = await it.boundingBox();
        await p.mouse.move(bb.x + bb.width / 2, bb.y + bb.height / 2); await sleep(80);
        await p.mouse.down(); await sleep(60); await p.mouse.up();
      } else {
        for (let i = 0; i < 40; i++) {                   // Pfeiltasten bis zum Eintrag, dann Enter
          if (await p.evaluate((t) => { const b = document.querySelector('.pm-pop:not([hidden]) .hk-emo-i.on .hk-at-t'); return b && b.textContent === t; }, title)) break;
          await p.keyboard.press('ArrowDown'); await sleep(30);
        }
        await p.keyboard.press('Enter');
      }
      await sleep(1400);
      const s = await p.evaluate(STATE);
      const neu = s.ws.filter((w) => old.indexOf(w.id) < 0);
      if (SHOTS) await p.screenshot({ path: path.join(SHOTS, `slash-neu-${eng.replace('/', '-')}-${tag}.png`) }).catch(() => {});
      ok(`${eng} ${tag}: neues Widget (${kind}) angelegt (${s.ws.map((w) => w.id + ':' + w.kind).join(', ')})`, neu.length === 1 && neu[0].kind === kind, s);
      ok(`${eng} ${tag}: Absatz "${q}" ist weg (${s.tops.join(' | ')})`, !s.tops.some((t) => t.indexOf(q) >= 0), s.tops);
      ok(`${eng} ${tag}: genau ein Panel, am neuen Widget (Panel bei ${s.owner.join(',') || '-'})`, s.panels === 1 && s.owner.length === 1 && neu.length === 1 && s.owner[0] === neu[0].id, s);
      ok(`${eng} ${tag}: vorhandene Widgets ohne Panel`, !s.ws.some((w) => old.indexOf(w.id) >= 0 && w.panel), s.ws);
      if (edit && neu.length === 1 && s.owner[0] === neu[0].id) {   // im Panel einrichten: Datenbank, Gruppierung, Summe des Betrags
        const pan = p.locator('.wg-panel');
        await pan.locator('select').filter({ has: p.locator('option', { hasText: 'WS ' + eng + ' Ausgaben' }) }).first().selectOption(String(db), { timeout: 8000 }).catch(() => {});
        const row = (l) => p.locator('.wg-panel .wg-row', { has: p.locator('.wg-l', { hasText: new RegExp('^' + l + '$') }) }).locator('select');
        let steps = [['Gruppieren nach', 'kat'], ['Rechenart', 'sum'], ['Eigenschaft', 'betrag'], ['Rechenart', 'count'],   // zurueck auf Anzahl: y "" (Server)
          ['Rechenart', 'sum'], ['Eigenschaft', 'betrag'], ['Sortierung', 'y_desc'], ['Sortierung', 'x']], bad = null;
        for (const [l, v] of steps) {
          if (!(await until(() => row(l).count(), 8000))) {
            bad = { fehlt: l, zeilen: await p.evaluate(() => [...document.querySelectorAll('.wg-panel .wg-l')].map((x) => x.textContent)), rejects: rejects.slice(rj0) }; break;
          }
          const e = await row(l).selectOption(v, { timeout: 5000 }).then(() => null, (x) => String(x.message).split('\n')[0]);
          if (e) { bad = { schritt: l, fehler: e, rejects: rejects.slice(rj0) }; break; }
          await sleep(400);
        }
        ok(`${eng} ${tag}: Panel-Schritte (Datenbank, Gruppierung, Rechenart, Eigenschaft, Sortierung) moeglich`, !bad, bad);
        const pv = await until(() => p.locator(`#pg-editor [data-block-id="${neu[0].id}"] .ch-svg rect.ch-f`).count(), 8000);
        ok(`${eng} ${tag}: im Panel eingerichtet, Vorschau mit Balken (${pv})`, pv === 2);
        const s3 = await p.evaluate(STATE);
        ok(`${eng} ${tag}: Panel bleibt am neuen Widget`, s3.panels === 1 && s3.owner[0] === neu[0].id, s3);
      }
      if (live) ok(`${eng} ${tag}: kein Update abgelehnt`, rejects.length === rj0, rejects.slice(rj0));
      await p.keyboard.press('Escape'); await sleep(200);
      if (await p.locator('.wg-panel .wg-done').count()) await p.locator('.wg-panel .wg-done').first().click();
      await sleep(2500);
      const after = flat(await body(id), []).filter((b) => b.type === 'widget');
      const oldAfter = after.filter((b) => old.indexOf(b.id) >= 0).map((b) => JSON.stringify(b.data));
      ok(`${eng} ${tag}: gespeichert ${after.length} Widgets, alte Daten unveraendert`, after.length === old.length + 1 && JSON.stringify(oldAfter) === JSON.stringify(before), { before, oldAfter, n: after.length });
      if (edit) {
        const nw = after.find((b) => old.indexOf(b.id) < 0), c = (nw && nw.data.cfg) || {};
        ok(`${eng} ${tag}: neues Diagramm gespeichert (db/kat/sum/betrag)`, c.db === db && c.x === 'kat' && c.agg === 'sum' && c.y === 'betrag' && c.sort === 'x', nw);
      }
      // Neu laden: vorhandene Widgets (jetzt auch das neue, ohne cfg-Aenderung mit Vorgaben) oeffnen kein Panel
      await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id);
      await until(() => p.evaluate((n) => document.querySelectorAll('#pg-editor [data-block-type="widget"] .wg').length >= n, old.length + 1), 10000);
      if (live) await until(() => p.evaluate(() => window.HKPmLive && HKPmLive.state().mode === 'live'), 10000);
      await sleep(1200);
      const s2 = await p.evaluate(STATE);
      ok(`${eng} ${tag}: nach Neuladen kein Panel offen (${s2.panels})`, s2.panels === 0, s2);
      await api('DELETE', '/api/notes/' + id);
    }

    if (!live) {                                         // Vorgaben des Web (wgDefaults) = was der Server aus {kind} macht
      const kinds = await p.evaluate(() => HKPages.k.tools.wgKinds);
      const vid = (await api('POST', '/api/notes', { title: 'WS ' + eng + ' Vorgaben' })).id;
      await api('PATCH', '/api/notes/' + vid, { format: 'blocks', body: JSON.stringify({ blocks: kinds.map((k, i) => ({ id: 'k' + i, type: 'widget', data: { kind: k } })) }) });
      const srv = await body(vid), sk = (o) => JSON.stringify(o, Object.keys(o || {}).sort());
      const web = await p.evaluate((ks) => ks.map((k) => HKPages.k.tools.wgDefaults(k)), kinds);
      const diff = kinds.filter((k, i) => { const b = srv.find((x) => x.id === 'k' + i); return !b || sk(b.data.cfg) !== sk(web[i]); })
        .map((k) => [k, web[kinds.indexOf(k)], (srv.find((x) => x.id === 'k' + kinds.indexOf(k)) || {}).data]);
      ok(`${eng}: Widget-Vorgaben des Web = Bereinigung des Servers (${kinds.length} Arten)`, !diff.length, diff);
      await api('DELETE', '/api/notes/' + vid);
      // wgChartClean (vor jedem "changed" eines Diagramms) = Bereinigung des Servers (clean_blocks -> page_widgets._clean_cfg
      // -> dbcalc.clean_chart_spec), sonst lehnt live der Server ab ("sanitize") oder Einstellungen gehen verloren.
      // Zufaellige cfgs (fester Startwert) mit allen Feldern und Grenzfaellen; dazu: Serverergebnis bleibt im Web unveraendert.
      let seed = 2361; const rnd = () => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
      const VALS = { type: ['bar', 'hbar', 'line', 'area', 'pie', 'donut', 'number', 'x', 3, null], x: ['', 'title', 'kat', 'd1', 5, 'Foo', 'a-b', 'x'.repeat(25), null],
        agg: ['count', 'sum', 'avg', 'min', 'max', 'pct_checked', 'bad', null], y: ['', 'betrag', 'title', 2, 'N!'], sort: ['x', 'y_desc', 'y_asc', 'manual', '', null, 'z'],
        fmt: ['num', 'pct', 'eur', '', 'usd'], bucket: ['day', 'week', 'month', 'quarter', 'year', '', 'hour'], status: [true, false, 'true', 1],
        series: ['', 'title', 'kat', 's2', 4, 'Bad'], limit: [3, 2, 50, 51, 20, 7.5, '10', true, -1, 100, 1e20], cumulative: [true, false, 1], stack: [true, false, 'x'],
        empty: ['show', '', 'hide', true], goal: [0, 1.5, -3, 1e12, 1e11, '5', true, 7],
        order: [['essen', 'reise'], [], ['a', '<b>', '', 3], ['x'.repeat(70)], Array.from({ length: 60 }, (_, i) => 'k' + i), 'a', ['ä'.repeat(70), 'b']],
        db: [null, 0, db, -1, 3.5, true, String(db)], view: ['', 'tab', 3, 'a<b', 'v'.repeat(80), 'a&b', 'ä'.repeat(70)], prop: ['', 'x'], foo: [1] };
      const cfgs = Array.from({ length: 360 }, () => { const c = {}; for (const k in VALS) if (rnd() < 0.55) c[k] = VALS[k][Math.floor(rnd() * VALS[k].length)]; return c; });
      const sk2 = (o) => JSON.stringify(o, Object.keys(o || {}).sort());
      let cdiff = [], cfix = [], nc = 0;
      for (let part = 0; part < cfgs.length; part += 120) {
        const cs = cfgs.slice(part, part + 120), cid = (await api('POST', '/api/notes', { title: 'WS ' + eng + ' Diagramm-cfg' })).id;
        await api('PATCH', '/api/notes/' + cid, { format: 'blocks', body: JSON.stringify({ blocks: cs.map((c, i) => ({ id: 'q' + i, type: 'widget', data: { v: 1, kind: 'chart', cfg: c } })) }) });
        const sv = await body(cid);
        const r = await p.evaluate(([cs, sv]) => cs.map((c, i) => {
          const b = sv.find((x) => x.id === 'q' + i), want = b && b.data.cfg;
          return [c, HKPages.k.wgChartClean(JSON.parse(JSON.stringify(c))), want, want ? HKPages.k.wgChartClean(JSON.parse(JSON.stringify(want))) : null];
        }), [cs, sv]);
        r.forEach(([c, got, want, again]) => { nc++; if (!want || sk2(got) !== sk2(want)) cdiff.push({ c, got, want }); if (want && sk2(again) !== sk2(want)) cfix.push({ want, again }); });
        await api('DELETE', '/api/notes/' + cid);
      }
      ok(`${eng}: wgChartClean = Bereinigung des Servers (${nc} zufaellige cfgs)`, nc === cfgs.length && !cdiff.length, cdiff.slice(0, 3));
      ok(`${eng}: gespeicherte Diagramm-cfgs bleiben im Web unveraendert (kein Datenverlust beim naechsten Aendern)`, nc === cfgs.length && !cfix.length, cfix.slice(0, 3));
    }
    await slashCase('spalten-klick', LAYOUT, '/di', 'Widget: Diagramm', 'click', 'chart', OLD, true);
    await slashCase('spalten-enter', LAYOUT, '/di', 'Widget: Diagramm', 'enter', 'chart', OLD);
    await slashCase('spalten-uhr', LAYOUT, '/uhr', 'Widget: Uhr', 'click', 'clock', OLD);
    await slashCase('eins-diagramm', [CH('c1', 'bar', 'Eins'), P('pz')], '/di', 'Widget: Diagramm', 'click', 'chart', ['c1']);
    await slashCase('eins-uhr', [CH('c1', 'bar', 'Eins'), P('pz')], '/uhr', 'Widget: Uhr', 'click', 'clock', ['c1']);

    ok(`${eng}: keine Seitenfehler`, !errs.length, errs);
    await api('DELETE', '/api/notes/' + db);
    await br.close();
  }
  if (liveNow) setLive(false);
  const g = res.filter(Boolean).length;
  console.log('\nWIDGET-SLASH-NEU ' + g + '/' + res.length + (g === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
  process.exit(g === res.length ? 0 : 1);
})().catch((e) => { console.log('FEHLER', e.message); process.exit(1); });
