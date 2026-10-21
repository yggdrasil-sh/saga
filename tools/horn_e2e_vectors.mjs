// Gegenprobe Frame-E2E saga-horn/v1 mit dem Web-Kern (static/frame-e2e.js, WebCrypto von Node) gegen
// tests/vectors/horn_e2e.json, dazu der Schluesselplan mit eingespeister Uhr (Start, Beitritt = Ratsche, Verlassen =
// Neuschluessel nach 1,5 s, alter Schluessel 3 s, neue Sitzung, Nachforderung). Aufruf: node tools/horn_e2e_vectors.mjs
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const require = createRequire(import.meta.url);
const X = require(path.join(ROOT, 'static', 'frame-e2e.js'));
const V = JSON.parse(readFileSync(path.join(ROOT, 'tests', 'vectors', 'horn_e2e.json'), 'utf8'));
const fails = [];
let n = 0;
const ok = (name, cond, info) => { n++; if (!cond) fails.push(name + (info !== undefined ? ' ' + JSON.stringify(info) : '')); };

const st = await X.selftest(V);
ok(`Vektoren (${st.n} Pruefungen)`, st.ok, st.fails);

// --- Schluesselplan: A sendet, B empfaengt ---------------------------------------------------------------------------
const VK = X.unhex(V.frames[1].plain);                     // VP8-Zwischenbild als Nutzlast
const need = [];
const A = new X.Horn(), B = new X.Horn({ onNeed: (from, nn) => need.push([from, nn]) });
let t = 0, ts = 1000;
const send = async () => { ts += 3000; return A.encrypt(VK, 'video', 'delta', 0x11111111, ts, t); };
const recv = async (f) => { const r = await B.decrypt('A', f, 'video', 'delta', t); return typeof r === 'string' ? r : (X.hex(r) === V.frames[1].plain ? 'ok' : 'falsch'); };

ok('ohne eigenen Schluessel: keine Ausgabe', (await A.encrypt(VK, 'video', 'delta', 1, 1, t)) === 'nokey');
const k0 = await A.own('start', t);
ok('Start: n=0, kid=0, 32 Byte, Sitzung 16 hex', k0.n === 0 && k0.kid === 0 && k0.key.length === 32 && /^[0-9a-f]{16}$/.test(k0.s));
let f = await send();
ok('B ohne Schluessel: nokey (kein Klartext)', (await recv(f)) === 'nokey');
ok('B: fkey einsetzen', await B.peerKey('A', k0.n, k0.key, k0.s, t));
ok('B: entschluesselt', (await recv(f)) === 'ok');
ok('B: derselbe Rahmen erneut -> replay', (await recv(f)) === 'replay');

// Beitritt: A ratscht sofort; B leitet beim ersten Rahmen selbst ab (kein fkey an B)
t = 1000;
const k1 = await A.own('ratchet', t);
ok('Ratsche: n=1 = HKDF(K0)', k1.n === 1 && X.hex(k1.key) === X.hex(await X.ratchet(k0.key)));
f = await send();
ok('Ratsche sofort aktiv (kid 1 im Trailer)', f[f.length - 1] === 1);
ok('B: leitet K1 beim ersten Rahmen ab', (await recv(f)) === 'ok');
const C = new X.Horn();                                     // der Neue bekommt nur K1
await C.peerKey('A', k1.n, k1.key, k1.s, t);
ok('Neuer: entschluesselt kid 1', typeof (await C.decrypt('A', f, 'video', 'delta', t)) !== 'string');
ok('Neuer: kann K0 nicht aus K1 gewinnen (anderes FK)', X.hex((await X.frameKey(k1.key)).raw) !== X.hex((await X.frameKey(k0.key)).raw));

// Verlassen: Neuschluessel, Umschalten erst nach 1,5 s
t = 2000;
const k2 = await A.own('rekey', t);
ok('Neuschluessel: n=2, zufaellig (nicht die Ratsche)', k2.n === 2 && X.hex(k2.key) !== X.hex(await X.ratchet(k1.key)));
f = await send();
ok('vor 1,5 s: weiter mit kid 1', f[f.length - 1] === 1);
await B.peerKey('A', k2.n, k2.key, k2.s, t);
ok('B: alter Schluessel bis zum Umschalten', (await recv(f)) === 'ok');
t = 3499; f = await send();
ok('1499 ms: noch kid 1', f[f.length - 1] === 1);
t = 3500; f = await send();
ok('1500 ms: kid 2', f[f.length - 1] === 2);
ok('B: kid 2', (await recv(f)) === 'ok');
// verspaeteter Rahmen von A mit kid 1 (andere SSRC, eigenes Fenster)
const late = await X.encryptFrame((await X.frameKey(k1.key)).ck, VK, 3, 1, 0x33333333, 5, 0);
t = 6499;
ok('B: verspaeteter Rahmen mit altem Schluessel innerhalb 3 s', (await recv(late)) === 'ok');
const late2 = await X.encryptFrame((await X.frameKey(k1.key)).ck, VK, 3, 1, 0x33333333, 6, 1);
t = 6500;
ok('B: nach 3 s ab dem ersten Rahmen mit kid 2 -> nokey', (await recv(late2)) === 'nokey');

// Verlassen kurz nach einem Beitritt, Beitritt waehrend des Wechsels
t = 7000;
await A.own('rekey', t);                                    // n=3, wartet bis 8500
const k4 = await A.own('ratchet', t + 100);                 // Beitritt: der WARTENDE wird geratscht (n=4), Umschaltzeit bleibt
ok('Beitritt waehrend des Wechsels: n=4, nur der Neueste fuer Nachzuegler', k4.n === 4 && A.ownKeys().length === 1 && A.ownKeys()[0].n === 4);
t = 8400; f = await send();
ok('vor der Umschaltzeit weiter kid 2', f[f.length - 1] === 2);
t = 8500; f = await send();
ok('dann kid 4', f[f.length - 1] === 4);

// Nachforderung: B sieht nur Unlesbares (kid 4 fehlt, n=3 nie bekommen)
ok('B: kid 4 ohne n=3 -> Ratsche aus n=2 passt nicht -> auth', (await recv(f)) === 'auth');
t = 10600; await recv(await send());
ok('B meldet need nach >= 2 s', need.length === 1 && need[0][0] === 'A' && need[0][1] === 2, need);
t = 11000; await recv(await send());
ok('need hoechstens alle 5 s', need.length === 1);
const [kk] = A.ownKeys();
await B.peerKey('A', kk.n, kk.key, kk.s, t);
ok('nach fkey wieder lesbar', (await recv(await send())) === 'ok');

// neue Sitzung des Senders (Neustart): Empfaenger setzt zurueck, n beginnt bei 0
const A2 = new X.Horn(), j0 = await A2.own('start', t);
ok('neue Sitzung, anderes s', j0.s !== A.s && j0.n === 0);
ok('B: fkey n=0 der neuen Sitzung wird angenommen', await B.peerKey('A', j0.n, j0.key, j0.s, t));
ok('B: veraltetes fkey derselben Sitzung (n < neuestes - 1) abgelehnt', (await B.peerKey('A', 0, j0.key, j0.s, t)) === true && !(await (async () => { await B.peerKey('A', 5, k0.key, j0.s, t); return B.peerKey('A', 1, k1.key, j0.s, t); })()));

// Codec, leere Rahmen
ok('Video ohne VP8 -> codec', (await A.encrypt(VK, 'video', 'delta', 1, 1, t, 'video/H264')) === 'codec');
ok('leerer Rahmen -> verworfen', (await A.encrypt(new Uint8Array(0), 'audio', undefined, 1, 1, t)) === 'short');
ok('Video-Rahmen "empty" -> verworfen', (await A.encrypt(VK, 'video', 'empty', 1, 1, t)) === 'codec');

// --- Leistung (nur Hinweis; massgeblich ist die Messung im Browser) --------------------------------------------------
const big = new Uint8Array(30000); big[0] = 0x50;
const P = new X.Horn(), Q = new X.Horn(); const pk = await P.own('start', 0); await Q.peerKey('p', pk.n, pk.key, pk.s, 0);
let te = 0, td = 0; const N = 2000;
for (let i = 0; i < N; i++) {
  const a = performance.now(); const e = await P.encrypt(big, 'video', 'key', 7, i * 3000, 0); const b = performance.now();
  const d = await Q.decrypt('p', e, 'video', 'key', 0); const c = performance.now();
  te += b - a; td += c - b; if (typeof d === 'string') { ok('Leistung: Rahmen ' + i, false, d); break; }
}
console.log(`PERF node 30-KB-Rahmen: verschluesseln ${(te / N).toFixed(3)} ms, entschluesseln ${(td / N).toFixed(3)} ms`);

console.log(fails.length ? fails.map((x) => 'FAIL ' + x).join('\n') : '');
console.log(`HORN-E2E-NODE ${n - fails.length}/${n}`, fails.length ? '- FEHLER' : '- ALLES GRUEN');
process.exit(fails.length ? 1 : 0);
