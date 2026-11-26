// Saga 2.6 (Bugs-DB 2269 K-I, Konzept K10/K19/M4/M7, EV-28, RT-39) - Kennenlernen-Karte, Ort "Saga", "Meine Daten":
// - KI an, noch keine Einwilligung: "Heute" zeigt die Karte GENAU EINMAL ueber "Weitermachen" (drei Saetze, Satz 2 nennt
//   "passende Ausschnitte aus deinen Seiten, Aufgaben und Terminen" und den Host des Modell-Endpunkts mit der vom Server
//   gebildeten Art, nie die Saga-Domain; ⓘ mit "Ende-zu-Ende"); "Jetzt nicht" -> weg, auch nach dem Neuladen.
// - Seitenleiste: Eintrag "Saga" direkt unter "Heute"; Chat-Liste ohne Saga/Marin; leere Chat-Seite (2030) ohne Saga.
// - Ort "Saga": vor der Einwilligung nur die Karte (keine Eingabe), Leiste B6 oben; "Ja, mit Ideen" -> Eingabe + Beispiele,
//   Karte nie wieder (auch nicht auf Heute); Frage -> Antwort mit "Marin · KI" + Bild/KI-Marke.
// - Erste Schritte (2031): mit KI zusaetzlich "Marin kennenlernen", ohne KI vollstaendig ohne KI-Zeile.
// - Einstellungen › Saga › Meine Daten: Widerruf (haelt den laufenden Auftrag an: wartet_dich 'einwilligung'), Export JSON
//   und Markdown, Alles loeschen mit Bestaetigung, "Was mein Admin sehen kann".
// - KI aus (Konto ohne Gruppe): kein Eintrag, keine Karte, keine Einstellungen › Saga, /saga -> /dashboard.
// Richtet sich selbst ein: tests/kollegin_einwilligung.py --browser-setup/--browser-teardown, Fake-Modell tests/fakeai.py.
// Env: SAGA_BASE, BASE_URL (Saga-Domain), HK_FAKEAI_PORT (Standard 8925), PY (python), SHOTS=/ordner (Bilder hell/dunkel,
// Desktop/schmal), NUR_BILDER=1 (nur Bilder vom Stand ohne K-I: Heute/Chats/Einstellungen - fuer "vorher").
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright');
const { execFileSync, spawn } = require('child_process');
const path = require('path');
const E = process.env; const BASE = E.SAGA_BASE || 'http://127.0.0.1:8920';
const PORT = E.HK_FAKEAI_PORT || '8925'; const PY = E.PY || 'python';
const HERE = __dirname;
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x).slice(0, 600))); };
const errs = [];
const api = (p, m, u, b) => p.evaluate(([m, u, b]) => fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Saga': '1' },
  body: b ? JSON.stringify(b) : undefined }).then(r => r.text().then(t => { let d = null; try { d = JSON.parse(t); } catch (e) { d = t; } return { s: r.status, d, h: r.headers.get('content-type') }; })), [m, u, b]);
const until = async (fn, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, 150)); } return null; };
const py = (...a) => JSON.parse(execFileSync(PY, [path.join(HERE, 'kollegin_einwilligung.py'), ...a], { env: E, encoding: 'utf8' }).trim().split('\n').pop());
const host = (() => { try { return new URL(E.BASE_URL || BASE).host; } catch (e) { return ''; } })();

async function login(br, u, pw, opts) {
  const ctx = await br.newContext(Object.assign({ viewport: { width: 1440, height: 900 }, locale: 'de-DE' }, opts || {}));
  if (opts && opts.theme) await ctx.addInitScript(t => { try { localStorage.setItem('hk-theme', t); } catch (e) { /* egal */ } }, opts.theme);
  const p = await ctx.newPage(); p.on('pageerror', e => { errs.push(e.message); console.log('[err]', e.message); });
  await p.goto(BASE + '/login'); await p.fill('input[name=username]', u); await p.fill('input[name=password]', pw);
  await Promise.all([p.waitForNavigation(), p.press('input[name=password]', 'Enter')]);
  return { ctx, p };
}
async function shot(p, name) { if (E.SHOTS) { await p.waitForTimeout(250); await p.screenshot({ path: E.SHOTS + '/' + name + '.png' }); } }
const VIEWS = [['desktop', { width: 1440, height: 900 }], ['schmal', { width: 390, height: 844 }]];

async function vorher(br, cfg) {          // Stand ohne K-I: dieselben Ansichten als Bilder
  for (const th of ['dark', 'light']) for (const [vn, vp] of VIEWS) {
    const { ctx, p } = await login(br, cfg.a, cfg.pw, { viewport: vp, theme: th });
    await api(p, 'POST', '/api/first-steps', { answer: 'no' });
    await p.goto(BASE + '/dashboard'); await p.waitForTimeout(1500);
    if (vn === 'schmal') { const b = await p.$('#pg-side-btn'); if (b && await b.isVisible()) { await b.click(); await p.waitForTimeout(400); } }
    await shot(p, `vorher-heute-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`);
    await ctx.close();
  }
}

(async () => {
  const fk = spawn(PY, [path.join(HERE, 'fakeai.py'), PORT], { stdio: 'ignore' });
  await new Promise(r => setTimeout(r, 800));
  const cfg = py('--browser-setup');
  await new Promise(r => setTimeout(r, 5600));     // Einstellungen im Server 5 s zwischengespeichert
  const br = await chromium.launch(E.PW_CHANNEL ? { channel: E.PW_CHANNEL } : {});
  try {
    if (E.NUR_BILDER) { await vorher(br, cfg); return; }
    // ================= KI an, noch keine Einwilligung =================
    let { ctx, p } = await login(br, cfg.a, cfg.pw, { theme: 'dark' });
    await p.goto(BASE + '/dashboard');
    // Erste Schritte (2031): Frage nach der Einfuehrung kommt zuerst - mit "Ja" anlegen und die Zeile pruefen
    const fsq = await until(() => p.evaluate(() => { const h = document.querySelector('.hk-ask .hk-ask-d'); return h && h.textContent; }), 8000);
    ok('Erste Schritte: Frage erscheint (KI an)', !!fsq, fsq);
    if (fsq) { await p.click('.hk-ask .hk-ask-bar .btn-primary'); await p.waitForTimeout(1500); }
    let fs = await api(p, 'GET', '/api/first-steps');
    const keysA = (fs.d.items || []).map(i => i.key);
    ok('Erste Schritte mit KI: letzter Punkt „ai“ (Marin kennenlernen), offen', keysA[keysA.length - 1] === 'ai' && !fs.d.items[keysA.length - 1].done, fs.d.items);
    if (fs.d.page) {
      const pg = await api(p, 'GET', '/api/notes/' + fs.d.page);
      ok('Seite „Erste Schritte“: Zeile „Marin kennenlernen (Seitenleiste › Saga)“', /Marin kennenlernen \(Seitenleiste › Saga\)/.test(JSON.stringify(pg.d)), null);
    }
    await p.goto(BASE + '/dashboard');
    const card = await until(() => p.evaluate(() => { const s = document.getElementById('hz-saga-intro'); return s && !s.hidden && s.querySelector('.sg-intro') ? s.textContent : null; }), 8000);
    const pos = await p.evaluate(() => {
      const s = document.getElementById('hz-saga-intro'), r = document.getElementById('hz-recent');
      return { before: !!(s && r && (s.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING)), n: document.querySelectorAll('.sg-intro').length,
        btns: [].map.call(document.querySelectorAll('#hz-saga-intro .sg-intro-bar .btn'), b => b.textContent),
        lines: [].map.call(document.querySelectorAll('#hz-saga-intro .sg-intro-l'), x => x.textContent),
        head: (document.querySelector('#hz-saga-intro .sg-who-n') || {}).textContent, av: !!document.querySelector('#hz-saga-intro .sg-who .ai-av') };
    });
    ok('Heute: Kennenlernen-Karte genau einmal, über „Weitermachen“', card && pos.n === 1 && pos.before, pos);
    ok('Karte: B1-Kopf „Marin · KI“ mit Bild/KI-Marke', pos.head === 'Marin · KI' && pos.av, pos.head);
    ok('Karte: drei Sätze, Satz 2 nennt Ausschnitte aus Seiten, Aufgaben und Terminen und den Host (im Heimnetz)',
      pos.lines.length === 3 && /passende Ausschnitte aus deinen Seiten, Aufgaben und Terminen gehen an 127\.0\.0\.1:\d+ \(im Heimnetz\)/.test(pos.lines[1])
      && /ohne dein OK/.test(pos.lines[2]), pos.lines);
    ok('EV-28: Host ist der Modell-Endpunkt, nie die Saga-Domain', !host || card.indexOf(host) < 0, host);
    ok('Knöpfe: Ja, mit Ideen · Ja, ohne Ideen · Jetzt nicht', pos.btns.join('|') === 'Ja, mit Ideen|Ja, ohne Ideen|Jetzt nicht', pos.btns);
    await p.click('#hz-saga-intro .sg-info-b');
    const det = await p.evaluate(() => { const b = document.querySelector('#hz-saga-intro .sg-info-box'); return b && !b.hidden ? b.textContent : null; });
    ok('ⓘ öffnet die Details (nicht Ende-zu-Ende verschlüsselt, was rausgeht) - Fachwörter nur dort (M3)', /Ende-zu-Ende/.test(det || '') && !/Ende-zu-Ende/.test(pos.lines.join(' ')), det);
    ok('ⓘ: Widerruf ehrlich - stoppt Aufträge/Ideen/Gespräch, Fragen an anderen Stellen gehen weiter an die KI', /keine Aufträge mehr/.test(det || '') && /Frag deine Seiten/.test(det || '') && /nur, wenn du sie stellst/.test(det || ''), det);
    await shot(p, 'nachher2-kennenlernen-details-desktop-dunkel');
    { const L = await login(br, cfg.a, cfg.pw, { theme: 'light' }); await L.p.goto(BASE + '/dashboard');
      await until(() => L.p.evaluate(() => { const s = document.getElementById('hz-saga-intro'); return s && !s.hidden; }), 6000);
      await L.p.click('#hz-saga-intro .sg-info-b'); await shot(L.p, 'nachher2-kennenlernen-details-desktop-hell'); await L.ctx.close(); }
    await p.click('#hz-saga-intro .sg-info-b');
    for (const th of ['dark', 'light']) for (const [vn, vp] of VIEWS) {
      const L = await login(br, cfg.a, cfg.pw, { viewport: vp, theme: th });
      await L.p.goto(BASE + '/dashboard');
      await until(() => L.p.evaluate(() => { const s = document.getElementById('hz-saga-intro'); return s && !s.hidden; }), 6000);
      await shot(L.p, `nachher-heute-karte-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`);
      if (vn === 'schmal') { const b = await L.p.$('#pg-side-btn'); if (b && await b.isVisible()) { await b.click(); await L.p.waitForTimeout(400); await shot(L.p, `nachher-seitenleiste-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`); } }
      await L.ctx.close();
    }
    // Seitenleiste
    const side = await p.evaluate(() => {
      const rows = [].map.call(document.querySelectorAll('#pg-mainnav .pg-nav-row'), r => r.textContent.trim());
      return { rows, href: (document.getElementById('pg-nav-saga') || {}).getAttribute ? document.getElementById('pg-nav-saga').getAttribute('href') : null };
    });
    ok('Seitenleiste: „Saga“ direkt unter „Heute“, Ziel /saga', side.rows[0] === 'Heute' && side.rows[1] === 'Saga' && side.href === '/saga', side);
    // Jetzt nicht -> weg, auch nach dem Neuladen
    await p.click('#hz-saga-intro .sg-later'); await p.waitForTimeout(500);
    const gone = await p.evaluate(() => { const s = document.getElementById('hz-saga-intro'); return s.hidden && !s.querySelector('.sg-intro'); });
    await p.reload(); await p.waitForTimeout(2200);
    const gone2 = await p.evaluate(() => !document.querySelector('.sg-intro'));
    ok('„Jetzt nicht“: Karte weg und auf Heute nicht wieder (genau einmal)', gone && gone2, { gone, gone2 });
    let st = await api(p, 'GET', '/api/ai/status');
    ok('nach „Jetzt nicht“: keine Einwilligung (later)', st.d.consent && st.d.consent.state === 'later', st.d.consent);

    // Chats: Saga nicht in der Chat-Liste, leere Chat-Seite ohne Saga (2030)
    await p.goto(BASE + '/chats'); await p.waitForTimeout(2000);
    const ch = await p.evaluate(() => {
      const list = document.getElementById('ct-list'), side = document.getElementById('sh-chats');
      const txt = (list ? list.textContent : '') + ' ' + (side ? side.textContent : '');
      const main = document.querySelector('main') ? document.querySelector('main').textContent : '';
      return { inList: /Marin|Saga KI/.test(txt), sideSaga: !!document.getElementById('pg-nav-saga'), mainSaga: /Marin|Kennenlernen|kennenlernen/.test(main) };
    });
    ok('Chats: Eintrag „Saga“ in der Seitenleiste, aber nicht in der Chat-Liste; leere Chat-Seite ohne Saga', ch.sideSaga && !ch.inList && !ch.mainSaga, ch);

    // ================= Ort "Saga" =================
    await p.goto(BASE + '/saga');
    await until(() => p.evaluate(() => !!document.querySelector('#sg-intro .sg-intro')), 6000);
    const sp = await p.evaluate(() => ({ cards: document.querySelectorAll('.sg-intro').length, form: !document.getElementById('sg-ask').hidden,
      bar: (document.querySelector('#sg-bar .sg-bar-t') || {}).textContent || '', title: document.getElementById('sg-title').textContent,
      cur: (document.querySelector('#pg-nav-saga[aria-current="page"]') || null) !== null }));
    ok('Saga vor der Einwilligung: genau eine Karte, keine Eingabe (nichts ans Modell)', sp.cards === 1 && !sp.form, sp);
    ok('Leiste B6 fest oben: „Was du hier schreibst, liest die KI auf 127.0.0.1:… (im Heimnetz)“', /^Was du hier schreibst, liest die KI auf 127\.0\.0\.1:\d+ \(im Heimnetz\)/.test(sp.bar), sp.bar);
    ok('Kopf „Marin · KI“, Seitenleiste markiert „Saga“', sp.title === 'Marin · KI' && sp.cur, sp);
    for (const th of ['dark', 'light']) for (const [vn, vp] of VIEWS) {
      const L = await login(br, cfg.a, cfg.pw, { viewport: vp, theme: th });
      await L.p.goto(BASE + '/saga'); await until(() => L.p.evaluate(() => !!document.querySelector('#sg-intro .sg-intro')), 6000);
      await shot(L.p, `nachher-saga-karte-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`); await L.ctx.close();
    }
    await p.click('#sg-intro .sg-intro-bar .btn-primary');
    await until(() => p.evaluate(() => !document.getElementById('sg-ask').hidden), 6000);
    const after = await p.evaluate(() => ({ cards: document.querySelectorAll('.sg-intro').length, chips: document.querySelectorAll('.sg-chip').length,
      empty: (document.querySelector('.sg-empty-t') || {}).textContent }));
    ok('„Ja, mit Ideen“: Karte weg, Eingabe + leerer Zustand mit 3 Beispielen', after.cards === 0 && after.chips === 3 && /Sag mir, was du brauchst/.test(after.empty || ''), after);
    st = await api(p, 'GET', '/api/ai/prefs');
    ok('Einwilligung erteilt, Ideen „Nur auf Heute“', st.d.consent.state === 'granted' && st.d.ideas === 'today', st.d);
    fs = await api(p, 'GET', '/api/first-steps');
    ok('Erste Schritte: „ai“ jetzt erledigt', (fs.d.items || []).some(i => i.key === 'ai' && i.done), fs.d.items);
    await p.reload(); await p.waitForTimeout(1500);
    const n1 = await p.evaluate(() => document.querySelectorAll('.sg-intro').length);
    await p.goto(BASE + '/dashboard'); await p.waitForTimeout(2000);
    const n2 = await p.evaluate(() => document.querySelectorAll('.sg-intro').length);
    ok('nach der Einwilligung: Karte nie wieder (Saga und Heute)', n1 === 0 && n2 === 0, { n1, n2 });
    await p.goto(BASE + '/saga'); await until(() => p.evaluate(() => !document.getElementById('sg-ask').hidden), 6000);
    await p.fill('#sg-ta', 'Was steht zum Sommerfest an?'); await p.press('#sg-ta', 'Enter');
    const ans = await until(() => p.evaluate(() => { const c = [].slice.call(document.querySelectorAll('#sg-feed .sg-ai')).pop();
      return c && !c.classList.contains('sg-live') ? { head: (c.querySelector('.sg-who-n') || {}).textContent, av: !!c.querySelector('.ai-av'), t: (c.querySelector('.sg-answer') || {}).textContent } : null; }), 20000);
    ok('Gespräch: Antwort mit „Marin · KI“ und Bild/KI-Marke', ans && ans.head === 'Marin · KI' && ans.av && ans.t, ans);
    const th0 = await api(p, 'GET', '/api/ai/thread');
    ok('Gespräch gespeichert in ai_turn (2 Züge, Verlauf aus -> 24 h)', (th0.d.turns || []).length === 2 && th0.d.keep === 86400, th0.d);
    for (const th of ['dark', 'light']) for (const [vn, vp] of VIEWS) {
      const L = await login(br, cfg.a, cfg.pw, { viewport: vp, theme: th });
      await L.p.goto(BASE + '/saga'); await until(() => L.p.evaluate(() => document.querySelectorAll('#sg-feed .sg-ai').length > 0), 6000);
      await shot(L.p, `nachher-saga-gespraech-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`); await L.ctx.close();
    }

    for (const th of ['dark', 'light']) {             // "Frag deine Seiten": derselbe Host-Hinweis mit ⓘ (nicht je Antwort)
      const L = await login(br, cfg.a, cfg.pw, { theme: th });
      await L.p.goto(BASE + '/dashboard');
      await until(() => L.p.evaluate(() => !!(window.HKAI && HKAI.open)), 6000);
      await L.p.waitForTimeout(800);
      await L.p.evaluate(() => HKAI.open('ask'));
      const foot = await until(() => L.p.evaluate(() => { const n = document.querySelector('.ai-notice'); return n && n.textContent ? { t: n.textContent, i: !!n.querySelector('.ai-info-b') } : null; }), 6000);
      if (th === 'dark') ok('Frag deine Seiten: „Was du hier fragst, liest die KI auf … (im Heimnetz)“ mit ⓘ', foot && /Was du hier fragst, liest die KI auf 127\.0\.0\.1:\d+ \(im Heimnetz\)/.test(foot.t) && foot.i, foot);
      const ib = await L.p.$('.ai-info-b'); if (ib) await ib.click();
      await shot(L.p, `nachher2-frag-deine-seiten-hinweis-desktop-${th === 'dark' ? 'dunkel' : 'hell'}`); await L.ctx.close();
    }
    {                                                  // schmal: Tab "Saga" statt Seitenleiste (M1)
      const L = await login(br, cfg.a, cfg.pw, { viewport: { width: 390, height: 844 }, theme: 'dark' });
      await L.p.goto(BASE + '/dashboard'); await L.p.waitForTimeout(800);
      const tab = await L.p.evaluate(() => { const t = document.querySelector('#mob-tabs [data-act="saga"]'); return t && t.getClientRects().length ? { href: t.getAttribute('href'), t: t.textContent.trim() } : null; });
      await L.p.goto(BASE + '/saga'); await L.p.waitForTimeout(800);
      const nav = await L.p.evaluate(() => ({ cur: !!document.querySelector('#mob-tabs [data-act="saga"][aria-current="page"]'),
        head: getComputedStyle(document.querySelector('.np-head')).display !== 'none' }));
      ok('schmal: Tab „Saga“ führt zu /saga, dort markiert, Kopfzeile sichtbar', tab && tab.href === '/saga' && tab.t === 'Saga' && nav.cur && nav.head, { tab, nav });
      await L.ctx.close();
    }

    // ================= Einstellungen › Saga › Meine Daten =================
    await p.evaluate(() => window.HKSettings.open('saga'));
    await until(() => p.evaluate(() => !!document.getElementById('sg-mydata')), 6000);
    const md = await p.evaluate(() => {
      const b = document.getElementById('sg-mydata');
      return { nav: !!document.getElementById('hk-tab-saga'), btns: [].map.call(b.querySelectorAll('.btn'), x => x.textContent),
        admin: (b.querySelector('.sg-admin summary') || {}).textContent, pane: !document.getElementById('hk-pane-saga').hidden };
    });
    ok('Einstellungen › Saga › Meine Daten: Widerrufen, Export, Alles löschen, „Was mein Admin sehen kann“',
      md.nav && md.pane && md.btns.indexOf('Einwilligung widerrufen') >= 0 && md.btns.indexOf('Verlauf und Aufträge exportieren (JSON)') >= 0
      && md.btns.indexOf('Alles löschen') >= 0 && md.admin === 'Was mein Admin sehen kann', md);
    await p.click('#sg-mydata .sg-admin summary');
    await shot(p, 'nachher-einstellungen-meine-daten-desktop-dunkel');
    for (const [vn, vp] of VIEWS) {
      const L = await login(br, cfg.a, cfg.pw, { viewport: vp, theme: 'light' });
      await L.p.goto(BASE + '/saga'); await L.p.waitForTimeout(800);
      await L.p.evaluate(() => window.HKSettings.open('saga'));
      await until(() => L.p.evaluate(() => !!document.getElementById('sg-mydata')), 6000);
      await L.p.evaluate(() => { const d = document.querySelector('#sg-mydata .sg-admin'); if (d) d.open = true; const m = document.getElementById('sg-mydata'); if (m) m.scrollIntoView(); });
      await shot(L.p, `nachher-einstellungen-meine-daten-${vn}-hell`); await L.ctx.close();
    }
    const ex = await api(p, 'GET', '/api/ai/mydata?format=json');
    const exm = await api(p, 'GET', '/api/ai/mydata?format=md');
    ok('Export JSON (Einwilligungen, Gespräch, Aufträge) und Markdown', ex.s === 200 && ex.d.consent.length >= 2 && ex.d.conversation.length === 2
      && ex.d.jobs.some(j => j.id === cfg.job) && exm.s === 200 && /^text\/markdown/.test(exm.h || '') && /## Gespräch/.test(exm.d), { s: ex.s, ms: exm.s });
    // Widerruf -> laufender Auftrag haelt an
    { const L = await login(br, cfg.a, cfg.pw, { theme: 'light' }); await L.p.goto(BASE + '/saga'); await L.p.waitForTimeout(800);
      await L.p.evaluate(() => window.HKSettings.open('saga'));
      await until(() => L.p.evaluate(() => !!document.getElementById('sg-mydata')), 6000);
      await L.p.click('#sg-mydata .btn:not(.btn-destr):not(.sg-export)'); await until(() => L.p.$('.hk-ask'), 3000);
      await shot(L.p, 'nachher2-widerruf-dialog-desktop-hell'); await L.p.keyboard.press('Escape'); await L.ctx.close(); }
    await p.click('#sg-mydata .btn:not(.btn-destr):not(.sg-export)');
    await until(() => p.$('.hk-ask'), 3000);
    const wdT = await p.evaluate(() => document.querySelector('.hk-ask .hk-ask-d').textContent);
    ok('Widerruf-Dialog ehrlich: keine Aufträge/Ideen/Gespräch, „Frag deine Seiten“ geht weiter an den Host, nur wenn du fragst',
      /keine Aufträge mehr/.test(wdT) && /Gespräch im Ort „Saga“ ist aus/.test(wdT) && /Frag deine Seiten/.test(wdT) && /127\.0\.0\.1:\d+ \(im Heimnetz\)/.test(wdT), wdT);
    await shot(p, 'nachher2-widerruf-dialog-desktop-dunkel');
    await p.click('.hk-ask .hk-ask-bar .btn-destr');
    await until(() => p.evaluate(() => { const b = document.getElementById('sg-mydata'); return b && ![].some.call(b.querySelectorAll('.btn'), x => x.textContent === 'Einwilligung widerrufen'); }), 6000);
    st = await api(p, 'GET', '/api/ai/status');
    const js = py('--job-state', String(cfg.job));
    await p.waitForTimeout(400); await shot(p, 'nachher2-widerrufen-zustand-desktop-dunkel');
    { const L = await login(br, cfg.a, cfg.pw, { theme: 'light' }); await L.p.goto(BASE + '/saga'); await L.p.waitForTimeout(800);
      await L.p.evaluate(() => window.HKSettings.open('saga'));
      await until(() => L.p.evaluate(() => !!document.querySelector('.sg-set-state')), 6000); await L.p.waitForTimeout(300);
      const stT = await L.p.evaluate(() => document.querySelector('.sg-set-state').textContent);
      ok('Einstellungen nach Widerruf: Zustand ehrlich beschrieben', /keine Aufträge/.test(stT) && /Frag deine Seiten/.test(stT), stT);
      await shot(L.p, 'nachher2-widerrufen-zustand-desktop-hell');
      await L.ctx.close(); }
    ok('RT-36: Widerruf -> withdrawn, laufender Auftrag wartet_dich „einwilligung“', st.d.consent.state === 'withdrawn' && js.state === 'wartet_dich' && js.reason === 'einwilligung', { c: st.d.consent, js });
    // Alles loeschen mit Bestaetigung
    await p.click('#sg-mydata .btn-destr'); await until(() => p.$('.hk-ask'), 3000);
    const askT = await p.evaluate(() => document.querySelector('.hk-ask .hk-ask-t').textContent);
    await p.click('.hk-ask .hk-ask-bar .btn-destr'); await p.waitForTimeout(1200);
    const ex2 = await api(p, 'GET', '/api/ai/mydata?format=json');
    ok('Alles löschen (mit Rückfrage): Aufträge und Gespräch weg, Einwilligungs-Protokoll bleibt',
      askT === 'Alles löschen?' && ex2.d.jobs.length === 0 && ex2.d.conversation.length === 0 && ex2.d.consent.length >= 3, { askT, j: ex2.d.jobs.length, c: ex2.d.conversation.length });
    await ctx.close();

    // ================= KI aus =================
    ({ ctx, p } = await login(br, cfg.off, cfg.pw, { theme: 'dark' }));
    await p.goto(BASE + '/dashboard');
    const fq = await until(() => p.evaluate(() => { const h = document.querySelector('.hk-ask .hk-ask-d'); return h && h.textContent; }), 8000);
    ok('KI aus: Erste Schritte (2031) fragt ohne KI-Hinweis', fq && !/KI|Marin/.test(fq), fq);
    if (fq) { await p.click('.hk-ask .hk-ask-bar .btn-primary'); await p.waitForTimeout(1500); }
    fs = await api(p, 'GET', '/api/first-steps');
    const keysO = (fs.d.items || []).map(i => i.key);
    let pgO = fs.d.page ? await api(p, 'GET', '/api/notes/' + fs.d.page) : { d: '' };
    ok('KI aus: Erste Schritte vollständig ohne KI-Zeile', keysO.length >= 4 && keysO.indexOf('ai') < 0 && JSON.stringify(keysA.slice(0, -1)) === JSON.stringify(keysO)
      && !/kennenlernen/.test(JSON.stringify(pgO.d)), { keysO, keysA });
    await p.goto(BASE + '/dashboard'); await p.waitForTimeout(2000);
    const off = await p.evaluate(() => ({ nav: !!document.getElementById('pg-nav-saga'), slot: !!document.getElementById('hz-saga-intro'), card: !!document.querySelector('.sg-intro') }));
    await p.evaluate(() => window.HKSettings.open('account')); await p.waitForTimeout(600);
    const offSet = await p.evaluate(() => { const b = document.getElementById('hk-tab-saga'); return !!b && b.getClientRects().length > 0; });
    const offTab = await p.evaluate(() => !!document.querySelector('#mob-tabs [data-act="saga"]'));
    ok('KI aus: kein Eintrag „Saga“, kein Tab „Saga“, keine Karte, keine Einstellungen › Saga', !off.nav && !offTab && !off.slot && !off.card && !offSet, { off, offSet, offTab });
    await p.keyboard.press('Escape');
    for (const th of ['dark', 'light']) for (const [vn, vp] of VIEWS) {
      const L = await login(br, cfg.off, cfg.pw, { viewport: vp, theme: th });
      await L.p.goto(BASE + '/dashboard'); await L.p.waitForTimeout(1500);
      await shot(L.p, `nachher-heute-ki-aus-${vn}-${th === 'dark' ? 'dunkel' : 'hell'}`); await L.ctx.close();
    }
    await p.goto(BASE + '/saga'); await p.waitForTimeout(500);
    ok('KI aus: /saga führt zu Heute', /\/dashboard$/.test(new URL(p.url()).pathname), p.url());
    await ctx.close();
    ok('keine Skriptfehler', errs.length === 0, errs);
  } catch (e) {
    ok('Abbruch', false, e && e.stack);
  } finally {
    await br.close();
    try { py('--browser-teardown'); } catch (e) { console.log('teardown', e.message); }
    fk.kill();
    if (!E.NUR_BILDER) {
      const n = R.filter(r => r[1]).length;
      console.log(`\nKollegin Kennenlernen (Browser) ${n}/${R.length}` + (n === R.length ? ' - ALLES GRUEN' : ' - FEHLER'));
      process.exit(n === R.length ? 0 : 1);
    }
  }
})().catch(e => { console.log('FAIL Abbruch', e && e.stack); process.exit(1); });
