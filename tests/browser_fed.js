// Browser-Tests Foederation 2.3 (Playwright, headless Chrome) - laufen NICHT im Stack-Testlauf, sondern von einem Rechner
// mit Playwright gegen ZWEI verbundene Test-Stacks A und B (Admin hat sie verbunden, Adressbuch an):
//   SAGA_A=http://host:portA SAGA_B=http://host:portB SAGA_USER_A=.. SAGA_PASS_A=.. SAGA_USER_B=.. SAGA_PASS_B=.. \
//   SAGA_FIND_B=<Teil des Anzeigenamens von USER_B> [PLAYWRIGHT=..] node tests/browser_fed.js
// Prueft: Testvektoren saga-chat/v2 mit libsodium.js IM Browser, Zertifikate v2 werden nach dem Einrichten still
// hochgeladen, fremde Schluessel + TOFU (vertrauen/warnen/blockieren), Adressbuch-Treffer in Chat- und Teilen-Suche,
// Schalter "In Adressbuechern verbundener Server erscheinen" unter Einstellungen -> Konto.
'use strict';
const PW = require(process.env.PLAYWRIGHT || 'playwright-core');
const fs = require('fs'), path = require('path'), os = require('os');
const A = process.env.SAGA_A, B = process.env.SAGA_B, FIND = process.env.SAGA_FIND_B;
if (!A || !B || !FIND) { console.error('SAGA_A, SAGA_B, SAGA_USER_A/B, SAGA_PASS_A/B, SAGA_FIND_B setzen'); process.exit(2); }
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
const VEC = JSON.parse(fs.readFileSync(path.join(__dirname, 'vectors', 'chat_v2.json'), 'utf8'));

async function session(browser, base, user, pass) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'de-DE' });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => { p.errs = (p.errs || []).concat(String(e)); });
  await p.goto(base + '/login');
  await p.fill('input[name=username], input#username, input[type=text]', user);
  await p.fill('input[type=password]', pass);
  await Promise.all([p.waitForLoadState('networkidle').catch(() => {}), p.press('input[type=password]', 'Enter')]);
  await p.waitForTimeout(1000);
  p.base = base;
  p.api = (m, u, b) => p.evaluate(async ([m, u, b]) => {
    const r = await fetch(u, { method: m, headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.json().catch(() => ({}));
  }, [m, u, b]);
  return p;
}

const RKF = (base, u) => path.join(os.tmpdir(), 'saga-browser-fed-rk-' + base.replace(/\W+/g, '_') + '-' + u + '.txt');
async function chatKeys(q, user) {
  await q.goto(q.base + '/chats'); await q.waitForTimeout(2500);
  if (await q.$('.ct-setup-go')) {
    await q.click('.ct-setup-go'); await q.waitForSelector('#ct-rk');
    const rk = (await q.textContent('#ct-rk')).trim(); fs.writeFileSync(RKF(q.base, user), rk, { mode: 0o600 });
    await q.click('.hk-actions .btn-primary'); await q.fill('.ct-rk-last', rk.slice(-4));
    await q.click('.ct-setup-done'); await q.waitForSelector('.ct-setup-done', { state: 'detached', timeout: 60000 });
  } else if (await q.$('.ct-rk-go') && fs.existsSync(RKF(q.base, user))) {
    await q.click('.ct-rk-go'); await q.fill('.ct-rk-in', fs.readFileSync(RKF(q.base, user), 'utf8').trim());
    await q.click('.ct-rk-ok'); await q.waitForSelector('.ct-rk-ok', { state: 'detached', timeout: 60000 });
  }
  await q.waitForTimeout(1500);
}

// Testvektoren v2 mit der libsodium.js/chat-crypto.js der Seite (dieselbe Pruefung wie tools/chat_vectors_v2.mjs, Auszug)
async function vectors(p) {
  const r = await p.evaluate(async (v) => {
    await window.sodium.ready;
    const S = window.sodium, C = window.SagaChatCrypto(S), u = C.unb64, b = C.b64, hx = (h) => S.from_hex(h), out = {};
    const ik = S.crypto_sign_seed_keypair(hx(v.identity.ident_seed_hex)), bk = S.crypto_box_seed_keypair(hx(v.identity.backup_seed_hex));
    out.handle = b(C.handleSig(v.handle_cert.handle, ik.privateKey, ik.publicKey, bk.publicKey)) === v.handle_cert.sig;
    const sk = S.crypto_sign_seed_keypair(hx(v.device.sign_seed_hex)), ek = S.crypto_box_seed_keypair(hx(v.device.enc_seed_hex));
    out.device = b(C.deviceSigV2(v.identity.handle, ik.privateKey, { id: v.device.id, enc_pk: ek.publicKey, sign_pk: sk.publicKey })) === v.device.fed_sig;
    out.msgs = Object.values(v.messages).every((m) => {
      const e = C.encryptMessageV2({ chat_ref: m.chat_ref, sender_handle: m.sender_handle, device: m.sender_device, sign_sk: sk.privateKey, plain: m.body_json,
        reply_to: m.reply_to, ai: m.ai, recipients: [], id: m.id, ts: m.ts, key: u(m.key), nonce: u(m.nonce) });
      return e.body_ct === m.body_ct && e.sig === m.sig && C.verifyMessageV2(m.chat_ref, m, sk.publicKey);
    });
    const rc = v.recipient, bkb = S.crypto_box_seed_keypair(hx(rc.identity.backup_seed_hex)), mm = v.messages[rc.message];
    out.sealed = JSON.stringify(C.decryptMessageV2(mm.chat_ref, mm, C.openKey(rc.sealed['backup:' + rc.identity.handle], bkb.publicKey, bkb.privateKey)))
      === JSON.stringify(JSON.parse(mm.body_json));
    out.safety = C.safetyNumberV2(v.safety.handle_a, u(v.safety.ident_a), v.safety.handle_b, u(v.safety.ident_b)) === v.safety.number;
    out.reject = v.reject.filter((x) => x.what === 'message').every((x) => {
      const m = Object.assign({}, v.messages[x.message]); ['sender_handle', 'ai', 'ts'].forEach((k) => { if (k in x) m[k] = x[k]; });
      return !C.verifyMessageV2(x.chat_ref || m.chat_ref, m, sk.publicKey);
    });
    out.tofu = v.tofu.rules.filter((x) => x.step !== 'bad_cert').every((x) => C.tofuV2(x.stored, x.seen).result === x.result);
    return out;
  }, VEC);
  for (const k of Object.keys(r)) ok('Browser-Vektoren v2: ' + k, r[k]);
}

(async () => {
  const browser = await PW.chromium.launch({ channel: process.env.PW_CHANNEL || 'chrome', headless: true });
  try {
    const pa = await session(browser, A, process.env.SAGA_USER_A, process.env.SAGA_PASS_A);
    const pb = await session(browser, B, process.env.SAGA_USER_B, process.env.SAGA_PASS_B);
    await chatKeys(pa, 'a'); await chatKeys(pb, 'b');
    await vectors(pa);
    // Zertifikate v2: nach dem Einrichten/Entsperren still hochgeladen
    const ma = await pa.api('GET', '/api/chat/v2/me'), mb = await pb.api('GET', '/api/chat/v2/me');
    ok('A: Zertifikate v2 hochgeladen (cert_ok, nichts fehlt)', ma.cert_ok && !ma.need, ma);
    ok('B: Zertifikate v2 hochgeladen', mb.cert_ok && !mb.need, mb);
    // Fremde Schluessel + TOFU ueber window.SagaChatFed
    const t1 = await pa.evaluate((h) => window.SagaChatFed.keys([h]).then((r) => ({ trust: r[h].trust, n: r[h].keys && r[h].keys.devices.length, ident: r[h].keys && r[h].keys.ident_pub })), mb.handle);
    ok('A: Schluessel von B geprueft, erster Kontakt -> trust', t1.trust === 'trust' && t1.n >= 1, t1);
    const t2 = await pa.evaluate((h) => window.SagaChatFed.keys([h]).then((r) => r[h].trust), mb.handle);
    ok('A: zweiter Abruf -> trust', t2 === 'trust', t2);
    const t3 = await pa.evaluate((h) => window.SagaChatFed.verify(h, 'AAAA' + 'A'.repeat(40), false).then(() => window.SagaChatFed.keys([h])).then((r) => r[h].trust), mb.handle);
    ok('A: anderer Schluessel gemerkt (nicht ueberprueft) -> warn', t3 === 'warn', t3);
    const t4 = await pa.evaluate((h) => window.SagaChatFed.verify(h, 'BBBB' + 'B'.repeat(40), true).then(() => window.SagaChatFed.keys([h])).then((r) => r[h].trust), mb.handle);
    ok('A: ueberprueft + Wechsel -> block', t4 === 'block', t4);
    await pa.evaluate((h) => window.SagaChatFed.verify(h, '', false).then(() => null), mb.handle);
    const sn = await pa.evaluate(([mh, h, ip]) => window.SagaChatFed.safety(mh, h, window.SagaChatCrypto(window.sodium).unb64(ip, 32)), [ma.handle, mb.handle, t1.ident]);
    ok('A: Sicherheitsnummer v2 (12 Bloecke)', /^(\d{5} ){11}\d{5}$/.test(sn), sn);
    // Chat-Suche: Person von B mit Herkunft
    await pa.goto(A + '/chats'); await pa.waitForTimeout(2000);
    await pa.click('#ct-new'); await pa.fill('.ct-people-q', FIND); await pa.waitForTimeout(1500);
    const hit = await pa.$$eval('.ct-person.ct-remote .ct-item-n', (n) => n.map((x) => x.textContent));
    const hostB = new URL(B).host;
    ok('Chat-Suche: Person von B mit Herkunft', hit.some((x) => x.includes(' · ' + hostB)), hit);
    await pa.screenshot({ path: path.join(os.tmpdir(), 'saga-fed-chat-suche.png') });
    // Teilen-Suche
    const nid = (await pa.api('POST', '/api/notes', { title: 'Browser-Föderation' })).id;
    await pa.goto(A + '/notes#' + nid); await pa.waitForTimeout(1500);
    await pa.click('#pg-share'); await pa.waitForSelector('.sh-q'); await pa.fill('.sh-q', FIND); await pa.waitForTimeout(1500);
    const sh = await pa.$$eval('.sh-hit .sh-sub', (n) => n.map((x) => x.textContent));
    ok('Teilen-Suche: Person von B (Adressbuch)', sh.some((x) => x.includes(hostB)), sh);
    await pa.screenshot({ path: path.join(os.tmpdir(), 'saga-fed-teilen-suche.png') });
    await pa.api('DELETE', '/api/notes/' + nid);
    // Einstellungen -> Konto: Schalter
    await pb.goto(B + '/'); await pb.waitForTimeout(1500);
    await pb.evaluate(() => window.HKSettings.open());
    const sw = await pb.waitForSelector('#hk-fed-dir', { timeout: 15000 }).catch(() => null);
    ok('Einstellungen -> Konto: Schalter "In Adressbüchern verbundener Server erscheinen"', !!sw && await sw.isChecked());
    if (sw) {
      await sw.click(); await pb.waitForTimeout(1200);
      const st = await pb.api('GET', '/api/account/federation');
      ok('Schalter aus -> gespeichert', st.directory === false, st);
      const gone = await (async () => { for (let i = 0; i < 20; i++) { const r = await pa.api('GET', '/api/fed/people?q=' + encodeURIComponent(FIND)); if (!r.people.length) return true; await pa.waitForTimeout(1000); } return false; })();
      ok('A: Person von B nicht mehr auffindbar', gone);
      await sw.click(); await pb.waitForTimeout(1200);
      ok('Schalter wieder an', (await pb.api('GET', '/api/account/federation')).directory === true);
      await pb.screenshot({ path: path.join(os.tmpdir(), 'saga-fed-konto.png') });
    }
    ok('keine JS-Fehler (A)', !(pa.errs || []).length, pa.errs);
    ok('keine JS-Fehler (B)', !(pb.errs || []).length, pb.errs);
  } catch (e) {
    console.error(e); res.push(false);
  } finally {
    await browser.close();
  }
  console.log(`\n${res.filter(Boolean).length}/${res.length} OK – ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
  process.exit(res.every(Boolean) ? 0 : 1);
})();
