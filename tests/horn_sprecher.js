// Sprechring ueber den Knoten (static/calls.js hornSpeakers) ohne Browser: Zuordnung des Pegels eines Tonplatzes zum
// Absender. Die Funktionen werden aus calls.js herausgeloest und mit nachgebildeten Empfaengern (getSynchronizationSources,
// getContributingSources) bzw. dem Rahmen-Weg (F.lvOn/F.lv aus horn-frames.js) aufgerufen.
//   node tests/horn_sprecher.js            (CALLS_JS=<pfad> fuer eine Gegenprobe mit einem anderen Stand)
// Faelle: Umhaengen eines Tonplatzes (Belegung nennt schon den neuen Absender, Ton/CSRC noch vom alten), ohne CSRC
// (Belegung wie bisher), Rahmen-Weg mit ok.level (Pegel per CSRC des Rahmens, veraltet -> nichts), ohne ok.level
// (Rueckfall auf den Sprecherplatz des Knotens).
'use strict';
const fs = require('fs');
const path = require('path');
const SRC = fs.readFileSync(process.env.CALLS_JS || path.join(__dirname, '..', 'static', 'calls.js'), 'utf8');
let pass = 0, fail = 0;
function ok(msg, cond, extra) {
  if (cond) pass++; else fail++;
  console.log((cond ? 'OK   ' : 'FAIL ') + msg + (cond || extra === undefined ? '' : ' -> ' + JSON.stringify(extra)));
}
function fn(name) {                                  // Quelltext von "function name(...) {...}" (Klammern zaehlen)
  const i = SRC.indexOf('function ' + name + '(');
  if (i < 0) throw new Error('nicht gefunden: ' + name);
  let d = 0, j = SRC.indexOf('{', i);
  for (; j < SRC.length; j++) {
    if (SRC[j] === '{') d++;
    else if (SRC[j] === '}' && --d === 0) return SRC.slice(i, j + 1);
  }
  throw new Error('kein Ende: ' + name);
}
let tick = null;
const env = {
  live: () => true,
  hornConns: R => R._conns,
  setInterval: f => { tick = f; return 1; },
  clearInterval: () => {},
};
const names = ['rtpAge', 'hornAudioOwner', 'hornSpeakers'].concat(SRC.indexOf('function hornAudioNow(') >= 0 ? ['hornAudioNow'] : []);
// eslint-disable-next-line no-new-func
const mk = new Function(...Object.keys(env), names.map(fn).join('\n') + '\nreturn hornSpeakers;');
const hornSpeakers = mk(...Object.values(env));

const A = 'dev-a', B = 'dev-b';
const now = () => performance.timeOrigin + performance.now();   // Epoch-Zeit wie Chromium
function tile() { return { box: { classList: { toggle() {} } } }; }
function run(conns, spk) {
  const R = { _conns: conns, horn: { tiles: { [A]: tile(), [B]: tile() }, st: {}, speaking: {}, spk: spk || null, plist: false } };
  hornSpeakers(R);
  tick();
  return R.horn;
}
// Tonplatz 0 einer Verbindung ueber WebRTC; owner = CSRC laut Belegung, srcs = [[csrc, alter ms], ...] (gespielte Rahmen)
function rtcConn(owner, level, srcs) {
  const t = now();
  const a = { _i: 0, _tr: { receiver: {
    getSynchronizationSources: () => [{ source: 99, audioLevel: level, timestamp: t }],
    getContributingSources: () => srcs.map(([c, age]) => ({ source: c, timestamp: t - age })),
  } } };
  return { csrc: { 1: A, 2: B }, aLeg: { slots: [{ class: 'audio', csrc: owner }] }, audios: [a] };
}
function frConn(F) {
  const c = rtcConn(2, 0.0001, []);
  c.fr = F;
  return c;
}

// 1. Umhaengen: Belegung schon B (CSRC 2), gespielt wird noch A (CSRC 1) -> Pegel zaehlt fuer A, kein Ring bei B
let H = run([rtcConn(2, 0.5, [[1, 5]])]);
ok('Umhaengen: Belegung nennt B, gespielt noch A -> Pegel bei A, nicht bei B', H.levels[A] === 0.5 && !H.levels[B] && H.speaking[A] && !H.speaking[B], H.levels);
// 2. danach: B ist der zuletzt gespielte (A noch in der 10-s-Liste, aelter)
H = run([rtcConn(2, 0.5, [[1, 400], [2, 5]])]);
ok('... danach: zuletzt gespielt B (A noch in der Liste, aelter) -> Pegel bei B', H.levels[B] === 0.5 && !H.levels[A], H.levels);
// 3. ohne CSRC (Browser ohne getContributingSources-Eintraege): wie bisher laut Belegung
H = run([rtcConn(2, 0.5, [])]);
ok('ohne CSRC: Absender laut Belegung (B)', H.levels[B] === 0.5 && !H.levels[A], H.levels);
// 4. Rahmen-Weg mit ok.level: Pegel aus Byte 3 per CSRC des Rahmens, unabhaengig von Belegung und Attrappen-Pegel
H = run([frConn({ lvOn: true, lv: { 0: { v: 0.4, csrc: 1, at: Date.now() } } })], B);
ok('Rahmen-Weg mit ok.level: Pegel per CSRC des Rahmens (A), kein Rueckfall auf den Sprecherplatz', H.lvSeen === true && H.levels[A] === 0.4 && !H.levels[B] && !H.speaking[B], H.levels);
H = run([frConn({ lvOn: true, lv: { 0: { v: 0.4, csrc: 1, at: Date.now() - 1500 } } })], B);
ok('... Pegel aelter als 1 s: niemand spricht', H.lvSeen === true && !Object.keys(H.levels).length, H.levels);
H = run([frConn({ lvOn: true, lv: { 0: { v: 0.4, csrc: 7, at: Date.now() } } })], B);
ok('... unbekannte CSRC: niemand', !Object.keys(H.levels).length, H.levels);
// 5. Rahmen-Weg ohne ok.level (aelterer Knoten, Byte 3 = 0): kein Pegel, Ring am Sprecherplatz des Knotens
H = run([frConn({ lvOn: false, lv: {} })], B);
ok('Rahmen-Weg ohne ok.level: Rueckfall auf den Sprecherplatz (B)', !H.lvSeen && H.levels[B] === 1 && H.speaking[B], { lvSeen: H.lvSeen, levels: H.levels });

console.log(`\nHORN-SPRECHER ${pass}/${pass + fail}` + (fail ? ' - FEHLER' : ' - ALLES GRUEN'));
process.exit(fail ? 1 : 0);
