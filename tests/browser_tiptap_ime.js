// Tiptap-Umbau T6 (Doppellauf): Eingabe ueber IME (Japanisch, Thai) per CDP Input.imeSetComposition im Tiptap-Editor
// ohne Live (Stufe 1): Text landet an der Schreibmarke, Komposition wird nicht doppelt eingefuegt, gespeichert (GET =
// Anzeige), keine Seitenfehler und CSP-Verletzungen = 0 (securitypolicyviolation). Thai: Vokal-/Tonzeichen ueber dem
// Grundzeichen (kombinierende Zeichen) bleiben in der Reihenfolge.
// Braucht Admin editor.engine = choice (der Test stellt die Konto-Wahl selbst auf Tiptap und am Ende zurueck).
// Lokal: HK_TEST_BASE=http://127.0.0.1:8099 CK=<Cookie hk-test-a> node tests/browser_tiptap_ime.js
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright');
const D = require('./lib/editor_dom');
const BASE = process.env.HK_TEST_BASE, CK = process.env.CK;
if (!BASE || !CK) { console.error('HK_TEST_BASE, CK setzen'); process.exit(2); }
const res = []; function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const RUN = Date.now().toString(36).slice(-5);

(async () => {
  const br = await PW.chromium.launch(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {});
  const ctx = await br.newContext({ viewport: { width: 1200, height: 900 }, locale: 'de-DE' });
  await ctx.addCookies([{ name: 'saga', value: CK, domain: new URL(BASE).hostname, path: '/' }]);
  const p = await ctx.newPage(); const errs = [];
  p.on('pageerror', (e) => errs.push(e.message));
  await p.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => window.__csp.push(e.violatedDirective + ' ' + e.blockedURI));
  });
  await p.goto(BASE + '/notes'); await sleep(500);
  const api = (m, u, b) => p.evaluate(([m, u, b]) => HK.api(m, u, b), [m, u, b]);
  const was = await api('GET', '/api/account/editor');
  ok('Admin erlaubt die Wahl (editor.engine = choice)', was.choice === true, was);
  await D.setEditor(p, 'tiptap');
  const P = (id, t) => ({ id, type: 'paragraph', data: { text: t } });
  const id = (await api('POST', '/api/notes', { title: 'IME ' + RUN })).id;
  await api('PATCH', '/api/notes/' + id, { body: JSON.stringify({ blocks: [P('j1', 'Start'), P('t1', 'ไทย'), P('m1', 'vorne hinten')] }), format: 'blocks' });
  const get = async () => JSON.parse((await api('GET', '/api/notes/' + id)).body || '{}').blocks || [];
  async function saved(fn, ms = 10000) { const t = Date.now(); let b; while (Date.now() - t < ms) { b = await get(); if (fn(b)) return b; await sleep(250); } return b; }
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(D.block('m1')); await sleep(900);
  ok('Tiptap-Editor geladen', (await D.isEditor(p)) === 'tiptap', await D.isEditor(p));
  const cdp = await ctx.newCDPSession(p);
  const txt = (bid) => D.textOfBlock(p, bid);

  // 1) Japanisch: Kana-Komposition, Umwandlung in Kanji, Bestaetigen
  await D.focus(p, 'j1', 'end');
  await cdp.send('Input.imeSetComposition', { text: 'に', selectionStart: 1, selectionEnd: 1 });
  await cdp.send('Input.imeSetComposition', { text: 'にほ', selectionStart: 2, selectionEnd: 2 });
  await cdp.send('Input.imeSetComposition', { text: 'にほん', selectionStart: 3, selectionEnd: 3 });
  await cdp.send('Input.imeSetComposition', { text: '日本', selectionStart: 2, selectionEnd: 2 });
  await cdp.send('Input.insertText', { text: '日本' });
  await sleep(300);
  ok('Japanisch: "Start日本" (Komposition nicht doppelt)', (await txt('j1')) === 'Start日本', await txt('j1'));
  // danach normal weitertippen: Schreibmarke steht hinter der Komposition
  await p.keyboard.type('語', { delay: 20 });
  ok('... Weitertippen hinter der Komposition', (await txt('j1')) === 'Start日本語', await txt('j1'));
  let b = await saved((l) => l[0] && l[0].data.text === 'Start日本語');
  ok('Japanisch gespeichert', b[0] && b[0].data.text === 'Start日本語', b[0]);

  // 2) Thai: Grundzeichen + Vokal oben + Tonzeichen (kombinierend), als eine Komposition
  await D.focus(p, 't1', 'end');
  await cdp.send('Input.imeSetComposition', { text: 'ก', selectionStart: 1, selectionEnd: 1 });
  await cdp.send('Input.imeSetComposition', { text: 'กิ', selectionStart: 2, selectionEnd: 2 });
  await cdp.send('Input.imeSetComposition', { text: 'กิ่', selectionStart: 3, selectionEnd: 3 });
  await cdp.send('Input.insertText', { text: 'กิ่' });
  await cdp.send('Input.insertText', { text: 'ง' });
  await sleep(300);
  const THAI = 'ไทยกิ่ง';
  ok('Thai: kombinierende Zeichen in Reihenfolge ("' + THAI + '")', (await txt('t1')) === THAI, [...(await txt('t1') || '')].map((c) => c.codePointAt(0).toString(16)));
  b = await saved((l) => l[1] && l[1].data.text === THAI);
  ok('Thai gespeichert', b[1] && b[1].data.text === THAI, b[1]);

  // 3) Komposition mitten im Text (Schreibmarke zwischen "vorne " und "hinten")
  await D.focus(p, 'm1', 'start');
  for (let i = 0; i < 6; i++) await p.keyboard.press('ArrowRight');
  await cdp.send('Input.imeSetComposition', { text: 'か', selectionStart: 1, selectionEnd: 1 });
  await cdp.send('Input.imeSetComposition', { text: 'かな', selectionStart: 2, selectionEnd: 2 });
  await cdp.send('Input.insertText', { text: '仮名' });
  await sleep(300);
  ok('Mitten im Text: "vorne 仮名hinten"', (await txt('m1')) === 'vorne 仮名hinten', await txt('m1'));
  b = await saved((l) => l[2] && l[2].data.text === 'vorne 仮名hinten');
  ok('... gespeichert, andere Bloecke unveraendert', b[2] && b[2].data.text === 'vorne 仮名hinten' && b[0].data.text === 'Start日本語' && b[1].data.text === THAI && b.length === 3, b);

  // 4) abgebrochene Komposition (leerer Text) hinterlaesst nichts
  await D.focus(p, 'j1', 'end');
  await cdp.send('Input.imeSetComposition', { text: 'て', selectionStart: 1, selectionEnd: 1 });
  await cdp.send('Input.imeSetComposition', { text: '', selectionStart: 0, selectionEnd: 0 });
  await sleep(1500);
  ok('Abgebrochene Komposition: Text unveraendert', (await txt('j1')) === 'Start日本語' && (await get())[0].data.text === 'Start日本語', await txt('j1'));

  // 5) nach Neuladen identisch
  await p.goto(BASE + '/notes?t=' + Date.now() + '#' + id); await p.waitForSelector(D.block('m1')); await sleep(700);
  ok('Neu geladen: Anzeige = gespeichert', (await txt('j1')) === 'Start日本語' && (await txt('t1')) === THAI && (await txt('m1')) === 'vorne 仮名hinten',
    [await txt('j1'), await txt('t1'), await txt('m1')]);

  const csp = await p.evaluate(() => window.__csp);
  ok('CSP-Verletzungen = 0 (securitypolicyviolation)', !csp.length, csp);
  ok('Keine Seitenfehler', !errs.length, errs);
  await api('DELETE', '/api/notes/' + id);
  await D.setEditor(p, was.tiptap ? 'tiptap' : 'editorjs');
  await br.close();
  const n = res.filter(Boolean).length;
  console.log('\nTIPTAP-IME ' + n + '/' + res.length + (n === res.length ? ' ALLES GRUEN' : ' FEHLER'));
  process.exit(n === res.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
