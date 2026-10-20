// Gegenprobe der Testvektoren saga-file/v2 (tests/vectors/file_v2.json, erzeugt mit PyNaCl) mit libsodium.js aus
// static/vendor/libsodium und static/chat-crypto.js - genau dem Code, den Browser und Service Worker benutzen.
// Aufruf: node tools/file_vectors_v2.mjs
import { createRequire } from 'module';
import Module from 'module';
import { readFileSync } from 'fs';
import { createHash } from 'crypto';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const SUMO = join(ROOT, 'static/vendor/libsodium/libsodium-sumo.js');
const orig = Module._resolveFilename;
Module._resolveFilename = function (req, ...a) { return req === 'libsodium-sumo' ? SUMO : orig.call(this, req, ...a); };
const sodium = require(join(ROOT, 'static/vendor/libsodium/libsodium-wrappers.js'));
await sodium.ready;
const C = require(join(ROOT, 'static/chat-crypto.js'))(sodium);
const v = JSON.parse(readFileSync(join(ROOT, 'tests/vectors/file_v2.json'), 'utf8'));
const res = [];
const ok = (n, c) => { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n); };
const hx = (b) => sodium.to_hex(b), CS = v.cs;
const plain = (n) => { const p = new Uint8Array(n); for (let k = 0; k < n; k++) p[k] = k % 251; return p; };
const sha = (b) => createHash('sha256').update(b).digest('hex');

ok('format', v.format === 'saga-file/v2' && C.F2.CS === CS && C.F2.HEADER === v.header_len);
ok('lp', hx(C.lp(...v.lp_example.fields_hex.map((h) => sodium.from_hex(h)))) === v.lp_example.hex);
const blobs = {};
for (const [name, c] of Object.entries(v.cases)) {
  const prefix = sodium.from_hex(c.prefix_hex);
  const e = C.encryptFileF2(plain(c.size), { key: C.unb64(c.key, 32), prefix });
  blobs[name] = e.blob;
  ok(name + ': Kopf', hx(e.blob.subarray(0, 48)) === c.header_hex && hx(C.f2Header(c.size, prefix)) === c.header_hex);
  const info = C.f2Parse(e.blob.subarray(0, 48));
  ok(name + ': Kopf gelesen', info.size === c.size && info.n === c.blocks && info.cs === CS && hx(info.prefix) === c.prefix_hex);
  ok(name + ': AD/Nonce', hx(C.f2Ad(0, c.blocks, CS, c.size)) === c.ad0_hex && hx(C.f2Nonce(prefix, 0)) === c.nonce0_hex
    && hx(C.f2Ad(c.blocks - 1, c.blocks, CS, c.size)) === c.ad_last_hex && hx(C.f2Nonce(prefix, c.blocks - 1)) === c.nonce_last_hex);
  ok(name + ': SHA-256 + Groesse', sha(e.blob) === c.blob_sha256 && e.blob.length === c.blob_size && C.f2Size(c.size) === c.blob_size);
  const d = C.decryptFileF2(e.blob, c.key);
  ok(name + ': entschluesseln', d.length === c.size && sha(d) === sha(plain(c.size)));
}
function mutate(blob, r) {
  let b = blob.slice();
  const dv = () => new DataView(b.buffer, b.byteOffset);
  if (r.op === 'swap_blocks') {
    const bi = C.f2Off(r.i), bj = C.f2Off(r.j), x = b.slice(bi, bi + CS + 16), y = b.slice(bj, bj + CS + 16);
    b.set(y, bi); b.set(x, bj);
  } else if (r.op === 'drop_last_block') b = b.slice(0, C.f2Off(C.f2Blocks(r.size) - 1));
  else if (r.op === 'drop_last_block_fix_size') {
    const n = C.f2Blocks(r.size); b = b.slice(0, C.f2Off(n - 1)); b.set(new Uint8Array(8), 16); dv().setUint32(20, (n - 1) * CS, false);
  } else if (r.op === 'set_size') { b.set(new Uint8Array(8), 16); dv().setUint32(20, r.value, false); }
  else if (r.op === 'set_cs') dv().setUint32(8, r.value, false);
  else if (r.op === 'set_byte') b[r.offset] = r.value;
  else if (r.op === 'flip') b[r.offset] ^= 1;
  else if (r.op === 'truncate') b = b.slice(0, r.length);
  else throw new Error(r.op);
  return b;
}
for (const r of v.reject) {
  let bad = false;
  try { C.decryptFileF2(mutate(blobs[r.case], r), v.cases[r.case].key); } catch (e) { bad = true; }
  ok('abgelehnt: ' + r.name, bad);
}
for (const r of v.ranges) {
  const x = C.f2Range(r.size, r.a, r.b);
  ok(`Bereich ${r.a}-${r.b}`, x.i === r.first_block && x.j === r.last_block && x.from === r.cipher_from && x.to === r.cipher_to);
}
const c = v.cases.rest_block, b = blobs.rest_block, info = C.f2Parse(b.subarray(0, 48));
const blk = C.f2Open(C.unb64(c.key, 32), info, 1, b.subarray(C.f2Off(1), C.f2Off(1) + CS + 16));
ok('Block 1 allein entschluesselt', sha(blk) === sha(plain(c.size).subarray(CS, 2 * CS)));
let wrongI = false;
try { C.f2Open(C.unb64(c.key, 32), info, 0, b.subarray(C.f2Off(1), C.f2Off(1) + CS + 16)); } catch (e) { wrongI = true; }
ok('Block an falscher Stelle abgelehnt', wrongI);
console.log(`\n${res.filter(Boolean).length}/${res.length} OK – ` + (res.every(Boolean) ? 'ALLES GRUEN' : 'FEHLER'));
process.exit(res.every(Boolean) ? 0 : 1);
