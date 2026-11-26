// Saga 2.6 (Bugs-DB 2270 K-C) - Service Worker (static/sw.js) bei Auftrags-Mitteilungen der Kollegin (Nutzlast 3,
// `jobpush1`), ohne Browser: sw.js laeuft in einem Fake-`self` (node:vm), gefuettert mit den entschluesselten Nutzlasten
// aus tests/vectors/push_kollegin.json (cases[].msg). Geprueft:
// - Titel/Text: mit detail t1/t2 vom Server, ohne detail der neutrale Satz (texts[lang].title/body) - nie mehr.
// - Knoepfe nach K15 Geraet "web" (k15): nie "Passt, los"/"Rueckgaengig" (auch wenn ein Token dafuer mitkommt), nur
//   "Spaeter"/"Gerade nicht" mit Token und "Ansehen"; Woerter = texts[lang].buttons; Klartext-Wege ohne Knoepfe/Kennung.
// - Klick/"Ansehen" = Deep-Link (deeplinks: /saga?tab=jobs&job=<id>), Fenster wiederverwenden.
// - Knopf -> POST /api/ai/jobs/<id>/act {action, atok, pv} mit Sitzung (credentials same-origin) und X-Saga: 1;
//   200 -> kein Fenster; 404 -> nichts; 409/410/403 -> Fenster mit url (nur eigene /saga?tab=jobs-Adressen);
//   Netzfehler -> einmal wiederholen, dann Fenster; verweigert der Browser das Fenster: Hinweis texts[lang].hint.
// - Stilles update/removed schliesst die Mitteilung; andere Arten (Chat) unveraendert; pushsubscriptionchange mit caps.
// Aufruf: node tests/sw_job_actions.js   (keine Abhaengigkeiten, kein Server)
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..');
const V = JSON.parse(fs.readFileSync(path.join(ROOT, 'tests/vectors/push_kollegin.json'), 'utf8'));
const SRC = fs.readFileSync(path.join(ROOT, 'static/sw.js'), 'utf8').replace(/__SAGA_V__/g, 'test');
const ORIGIN = 'https://saga.example.org';
const R = []; const ok = (n, c, x) => { R.push([n, !!c]); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' ' + JSON.stringify(x === undefined ? '' : x))); };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// --- Fake-Umgebung -------------------------------------------------------------------------------------------------
function world(opts) {
  opts = opts || {};
  const L = {}, shown = [], fetches = [], opened = [], focused = [], closedTags = [];
  const wins = (opts.windows || []).map((u) => ({ url: u, frameType: 'top-level', focus() { focused.push(u); return Promise.resolve(this); },
    navigate(x) { opened.push('nav:' + x); return Promise.resolve(this); }, postMessage() {} }));
  let fetchPlan = (opts.fetch || []).slice();
  const reg = {
    showNotification: (title, o) => { shown.push({ title, o }); return Promise.resolve(); },
    getNotifications: (f) => Promise.resolve(shown.filter((n) => !f || !f.tag || (n.o && n.o.tag === f.tag)).map((n) => ({ close() { closedTags.push(n.o.tag); } }))),
    pushManager: { subscribe: () => Promise.resolve({ toJSON: () => ({ endpoint: 'https://push.example/x', keys: { p256dh: 'p', auth: 'a' } }) }) },
  };
  const self = {
    location: { origin: ORIGIN },
    addEventListener: (t, fn) => { (L[t] = L[t] || []).push(fn); },
    skipWaiting() {}, registration: reg,
    clients: { matchAll: () => Promise.resolve(wins), claim: () => Promise.resolve(),
      openWindow: (u) => { opened.push(u); return Promise.resolve(opts.noWindow ? null : { url: u }); } },
  };
  function fetch(url, init) {
    fetches.push({ url, init });
    const step = fetchPlan.length ? fetchPlan.shift() : { status: 200, body: { ok: true } };
    if (step === 'net') return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({ ok: step.status >= 200 && step.status < 300, status: step.status, json: () => Promise.resolve(step.body || {}) });
  }
  // MessageChannel wird nur von navAsk gebraucht (offene Fenster ohne Antwort -> navigate)
  class MC { constructor() { this.port1 = {}; this.port2 = {}; } }
  const ctx = { self, fetch, caches: { open: () => Promise.reject(new Error('x')), keys: () => Promise.resolve([]) }, importScripts() {},
    MessageChannel: MC, Notification: { maxActions: opts.maxActions === undefined ? 2 : opts.maxActions }, URL, URLSearchParams, setTimeout, clearTimeout,
    Promise, JSON, Date, Math, String, Array, Object, Number, Error, TypeError, console, Map, Uint8Array, Response: function () {} };
  ctx.self.Notification = ctx.Notification;
  vm.createContext(ctx);
  vm.runInContext(SRC, ctx, { filename: 'sw.js' });
  async function fire(type, ev) {
    const waits = [];
    ev.waitUntil = (p) => waits.push(p);
    (L[type] || []).forEach((fn) => fn(ev));
    for (let i = 0; i < waits.length; i++) await waits[i];
    await new Promise((r) => setTimeout(r, 0));
    return ev;
  }
  return {
    ctx, shown, fetches, opened, focused, closedTags,
    push: (msg) => fire('push', { data: { json: () => JSON.parse(JSON.stringify(msg)) } }),
    click: (n, action) => fire('notificationclick', { action: action || '', notification: { data: n.o.data, close() {} } }),
    fire,
  };
}

// --- erwartete Knoepfe aus den Vektoren (k15, Geraet web) ----------------------------------------------------------
function category(msg) {
  const a = (msg.actions || []).map((x) => x.a);
  if (msg.state === 'needs') return a.includes('approve') ? 'saga.job.needs.ok' : 'saga.job.needs';
  if (msg.state === 'done') return a.includes('undo') ? 'saga.job.done' : 'saga.job.done.plain';
  return 'saga.job.failed';
}
function webButtons(msg) {
  const k = V.k15.find((r) => r.cat === category(msg) && r.device === 'web' && r.locked === false && r.content === (msg.detail ? 'on' : 'off'));
  const toks = (msg.actions || []).map((x) => x.a);
  return k ? k.buttons.map((b) => b.a).filter((a) => a === 'open' || toks.includes(a)) : null;
}
const DEEP = {}; V.deeplinks.forEach((d) => { DEEP[d.link] = d.route; });

(async () => {
  ok('Vektoren: Format saga-push-kollegin/1', V.format === 'saga-push-kollegin/1', V.format);
  const webK15 = V.k15.filter((r) => r.device === 'web');
  ok('k15 Geraet web: nie approve/undo (Vertrag)', webK15.length && webK15.every((r) => r.buttons.every((b) => b.a !== 'approve' && b.a !== 'undo')), webK15.length);
  const jobCases = V.cases.filter((c) => c.msg && /^job\.(needs_you|done|failed)$/.test(c.msg.kind));
  ok('Vektoren: Auftrags-Faelle vorhanden (E2E und Klartext)', jobCases.length >= 10 && jobCases.some((c) => c.msg.detail) && jobCases.some((c) => !c.msg.job), jobCases.length);

  for (const c of jobCases) {
    const m = c.msg, lang = (m.detail && m.detail.lang) || m.lang, tx = V.texts[lang];
    const w = world(); await w.push(m);
    const n = w.shown[0] || { o: {} }, o = n.o;
    const acts = (o.actions || []).map((x) => x.action);
    if (m.detail) ok(`${c.name}: Titel/Text = t1/t2 vom Server`, n.title === m.detail.t1 && o.body === m.detail.t2, [n.title, o.body]);
    else {
      const neutral = m.state === 'needs' ? tx.title.needs : tx.title.feedback;
      ok(`${c.name}: ohne detail neutral („${neutral}“ / „${tx.body}“)`, n.title === neutral && o.body === tx.body && m.title === neutral, [n.title, o.body]);
    }
    ok(`${c.name}: nie Passt, los/Rückgängig im Web`, !acts.includes('approve') && !acts.includes('undo') && !Object.keys(o.data.tok || {}).some((a) => a === 'approve' || a === 'undo'), acts);
    ok(`${c.name}: Knöpfe nach K15 web (${(webButtons(m) || []).join('+')})`, same(acts, webButtons(m)), { acts, want: webButtons(m) });
    ok(`${c.name}: Wörter der Knöpfe aus texts.${lang}.buttons`, (o.actions || []).every((x) => x.title === tx.buttons[x.action]), o.actions);
    ok(`${c.name}: tag ${m.tag}, Ziel ${m.url}`, o.tag === m.tag && o.data.url === ORIGIN + m.url, [o.tag, o.data.url]);
    if (!m.job) ok(`${c.name}: Klartext-Weg - keine Kennung, nur „Ansehen“`, o.data.job === null && same(acts, ['open']) && o.data.url === ORIGIN + '/saga?tab=jobs', o.data);
    if (m.silent) ok(`${c.name}: ohne Ton (silent)`, o.silent === true, o.silent);
    else ok(`${c.name}: mit Ton`, !o.silent, o.silent);
    // Klick auf die Mitteilung = Ansehen per Deep-Link
    const w2 = world(); await w2.push(m); await w2.click(w2.shown[0], '');
    ok(`${c.name}: Klick öffnet ${m.url}`, same(w2.opened, [ORIGIN + m.url]) && !w2.fetches.length, w2.opened);
  }
  // Deep-Link-Tabelle: saga://job/<id> <-> /saga?tab=jobs&job=<id>, saga://jobs <-> /saga?tab=jobs
  const d42 = jobCases.find((c) => c.msg.job === 42);
  ok('Deep-Link: url der Nutzlast = Route von saga://job/42', d42 && d42.msg.url === DEEP['saga://job/42'], DEEP);
  ok('Deep-Link: Klartext-url = Route von saga://jobs', jobCases.filter((c) => !c.msg.job).every((c) => c.msg.url === DEEP['saga://jobs']));

  // --- Knoepfe -> act -------------------------------------------------------------------------------------------
  const done = V.cases.find((c) => c.name === 'done_undo_webpush').msg;
  const needs = V.cases.find((c) => c.name === 'needs_you_webpush').msg;
  const qTok = done.actions.find((x) => x.a === 'quiet').t, lTok = needs.actions.find((x) => x.a === 'later').t;
  async function press(msg, action, wopts) {
    const w = world(wopts); await w.push(msg); await w.click(w.shown[0], action); return w;
  }
  let w = await press(done, 'quiet', { fetch: [{ status: 200, body: V.tokens.responses['200_quiet'] }] });
  const f = w.fetches[0] || { init: { headers: {} } }, fb = f.init.body ? JSON.parse(f.init.body) : {};
  ok('Gerade nicht: POST /api/ai/jobs/42/act', w.fetches.length === 1 && f.url === '/api/ai/jobs/42/act' && f.init.method === 'POST', w.fetches);
  ok('act: Sitzung (credentials same-origin) + X-Saga: 1 + JSON', f.init.credentials === 'same-origin' && f.init.headers['X-Saga'] === '1' && f.init.headers['Content-Type'] === 'application/json', f.init);
  ok('act: {action, atok, pv} aus der Mitteilung', same(fb, { action: 'quiet', atok: qTok, pv: done.pv }), fb);
  ok('act 200: kein Fenster', !w.opened.length, w.opened);
  w = await press(needs, 'later', { fetch: [{ status: 200, body: V.tokens.responses['200_later'] }] });
  ok('Später: act later mit seinem Token, kein Fenster', w.fetches.length === 1 && JSON.parse(w.fetches[0].init.body).atok === lTok && JSON.parse(w.fetches[0].init.body).action === 'later' && !w.opened.length, w.fetches);
  w = await press(done, 'quiet', { fetch: [{ status: 409, body: V.tokens.responses['409'] }] });
  ok('act 409 (stale) -> Fenster mit url der Antwort', same(w.opened, [ORIGIN + V.tokens.responses['409'].url]), w.opened);
  w = await press(done, 'quiet', { fetch: [{ status: 410, body: V.tokens.responses['410'] }] });
  ok('act 410 (used) -> Fenster mit dem Auftrag', same(w.opened, [ORIGIN + done.url]), w.opened);
  w = await press(done, 'quiet', { fetch: [{ status: 403, body: V.tokens.responses['403'] }] });
  ok('act 403 (device) -> App/Fenster öffnen', same(w.opened, [ORIGIN + done.url]), w.opened);
  w = await press(done, 'quiet', { fetch: [{ status: 404, body: {} }] });
  ok('act 404 -> nichts (Mitteilung ist weg)', !w.opened.length && w.fetches.length === 1, w.opened);
  w = await press(done, 'quiet', { fetch: ['net', { status: 200, body: {} }] });
  ok('Netzfehler: einmal wiederholen, dann 200 -> kein Fenster', w.fetches.length === 2 && !w.opened.length, [w.fetches.length, w.opened]);
  w = await press(done, 'quiet', { fetch: ['net', 'net'] });
  ok('Netzfehler zweimal -> Fenster (wie 409)', w.fetches.length === 2 && same(w.opened, [ORIGIN + done.url]), [w.fetches.length, w.opened]);
  w = await press(done, 'quiet', { fetch: [{ status: 409, body: { code: 'state', url: 'https://evil.example/x' } }] });
  ok('409 mit fremder url -> nur /saga?tab=jobs der eigenen Origin', same(w.opened, [ORIGIN + '/saga?tab=jobs']), w.opened);
  w = await press(done, 'quiet', { fetch: [{ status: 409, body: V.tokens.responses['409'] }], noWindow: true });
  ok('Fenster verweigert -> Hinweis „Bitte in Saga ansehen“ mit Ziel', w.shown.length === 2 && w.shown[1].title === V.texts.de.hint && w.shown[1].o.data.url === ORIGIN + V.tokens.responses['409'].url, w.shown.map((x) => x.title));
  w = await press(done, 'open');
  ok('„Ansehen“ = Deep-Link ohne act', same(w.opened, [ORIGIN + done.url]) && !w.fetches.length, w.opened);
  w = await press(done, 'undo');
  ok('erzwungenes „undo“ (Token kam mit) -> nie act undo, nur Fenster', !w.fetches.length && same(w.opened, [ORIGIN + done.url]), w.fetches);
  w = await press(done, '', { windows: [ORIGIN + done.url] });
  ok('Fenster mit dem Auftrag offen -> fokussieren statt neu öffnen', same(w.focused, [ORIGIN + done.url]) && !w.opened.length, [w.focused, w.opened]);
  w = await press(done, '', { windows: [ORIGIN + '/notes'] });
  ok('anderes Saga-Fenster offen -> dorthin führen', same(w.opened, ['nav:' + ORIGIN + done.url]), w.opened);
  w = world({ maxActions: 1 }); await w.push(done);
  ok('Notification.maxActions = 1 -> nur der erste Knopf', same((w.shown[0].o.actions || []).map((x) => x.action), ['quiet']), w.shown[0].o.actions);
  // Woerter der Knoepfe/des Hinweises im Service Worker = texts der Vektoren (alle Sprachen)
  const TX = world().ctx.JOB_TX || {};
  ok('Service Worker: Knöpfe/Hinweis in de/en/es/ru/th wie texts der Vektoren', Object.keys(V.texts).every((l) => TX[l] && TX[l].later === V.texts[l].buttons.later
    && TX[l].quiet === V.texts[l].buttons.quiet && TX[l].open === V.texts[l].buttons.open && TX[l].hint === V.texts[l].hint) && Object.keys(V.texts).length === 5, Object.keys(TX));
  // Thai: Woerter aus texts.th
  const th = V.cases.find((c) => c.name === 'gross_th_webpush');
  if (th) { w = world(); await w.push(th.msg); ok('Thai: Knöpfe auf Thai', (w.shown[0].o.actions || []).every((x) => x.title === V.texts.th.buttons[x.action]) && w.shown[0].o.actions.length === 2, w.shown[0].o.actions); }

  // --- stilles update (removed) schliesst die Auftrags-Mitteilung --------------------------------------------------
  const upd = V.cases.find((c) => c.name === 'update_removed_webpush').msg;
  w = world(); await w.push(done); await w.push(upd);
  ok('update removed: Mitteilung job-42 geschlossen, nichts Neues', same(w.closedTags, ['job-42']) && w.shown.length === 1, [w.closedTags, w.shown.length]);
  // --- Regression: andere Arten unveraendert ------------------------------------------------------------------------
  w = world(); await w.push({ title: 'Saga', body: 'Neue Nachricht', url: '/chats#c1', tag: 'chat:1', kind: 'chat.message', ts: 1, v: 2 });
  ok('Chat-Mitteilung wie bisher (ohne Knöpfe)', w.shown.length === 1 && w.shown[0].title === 'Saga' && !w.shown[0].o.actions && w.shown[0].o.data.url === ORIGIN + '/chats#c1', w.shown[0]);
  await w.click(w.shown[0], '');
  ok('Chat-Klick öffnet wie bisher', same(w.opened, [ORIGIN + '/chats#c1']), w.opened);
  // --- Abo-Erneuerung meldet die Faehigkeiten mit ---------------------------------------------------------------------
  w = world();
  await w.fire('pushsubscriptionchange', { oldSubscription: { options: { applicationServerKey: new Uint8Array([1, 2]) } } });
  const sub = w.fetches[0] ? JSON.parse(w.fetches[0].init.body) : {};
  ok('pushsubscriptionchange: caps ["detail","actions"] (ohne update)', same(sub.caps, ['detail', 'actions']) && sub.type === 'webpush', sub);

  const bad = R.filter((r) => !r[1]);
  console.log(`\n${R.length - bad.length}/${R.length} OK`);
  process.exit(bad.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
