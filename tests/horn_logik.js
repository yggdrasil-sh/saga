// Gjallarhorn P5: Entscheidungen des Web-Clients (static/horn.js) ohne Browser - Punktzahl der Kandidaten (§2.6),
// Umschalter mit Hysterese (sofort bei Abbruch und Leeren, "besser" nur >= 25 % und >= 30 ms fuer 20 s, Verweildauer 60 s,
// hoechstens 3 Wechsel je 10 min), UDP -> TCP bei Verlust, Rueckkehr auf UDP, Schliessen des alten Beins
// (make-before-break), Kachelansicht (Spalten, Last-N, Sprecheransicht, hoechstens 25), Qualitaetspunkt und Wegnamen.
//   node tests/horn_logik.js
'use strict';
const H = require('../static/horn.js');
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }

// --- Punktzahl -----------------------------------------------------------------------------------------------------
const m = (rtts, udp) => ({ ok: true, rtts, udp: udp === undefined ? null : udp });
ok('Median und Jitter', H.median([5, 1, 3]) === 3 && H.median([1, 2, 3, 4]) === 2.5 && H.jitter([10, 20, 10]) === 10 && H.jitter([7]) === 0);
const sTcp = H.score(m([20, 20, 20, 20, 20]), { udp: false, here: false, load: 0 });
ok('TCP-Kandidat: rtt 20 + UDP fehlt 15 = 35', sTcp === 35, sTcp);
ok('Bonus "Anruf schon hier": 35 - 25 = 10', H.score(m([20, 20, 20]), { udp: false, here: true, load: 0 }) === 10);
ok('UDP-Kandidat mit UDP-Probe: kein Abzug (20)', H.score(m([20, 20, 20], 18), { udp: true, here: false }) === 20);
ok('UDP-Kandidat ohne UDP-Probe am Geraet: Abzug (35)', H.score(m([20, 20, 20], null), { udp: true }) === 35);
ok('Jitter zaehlt doppelt: [20,40,20] -> 20 + 2*20 + 15 = 75', H.score(m([20, 40, 20]), { udp: false }) === 75);
ok('Last: 50 % kein Abzug, 80 % +20 ms, 95 % voll (+1000)', H.loadMs(0.5) === 0 && Math.abs(H.loadMs(0.8) - 20) < 1e-9 && H.loadMs(0.95) === 1000);
ok('nicht erreichbar = unendlich', H.score({ ok: false }, {}) === Infinity && H.score(null, {}) === Infinity);
const r = H.rank([{ node: 'a', here: true }, { node: 'b' }, { node: 'c' }], { a: m([60, 60, 60]), b: m([20, 20, 20]), c: { ok: false } });
ok('Rangfolge: b (35) vor a (60+15-25=50) vor c (unerreichbar)', r.map(x => x.cand.node).join() === 'b,a,c', r.map(x => [x.cand.node, x.score]));
const r2 = H.rank([{ node: 'a', here: true }, { node: 'b' }], { a: m([40, 40]), b: m([20, 20]) });
ok('Bonus haelt den Anruf auf einem Knoten: a (40+15-25=30) vor b (35)', r2[0].cand.node === 'a', r2.map(x => [x.cand.node, x.score]));
ok('besser: 100 -> 70 ja (30 ms, 30 %), 100 -> 80 nein, 40 -> 9 ja, 40 -> 15 nein (< 30 ms)',
   H.better(100, 70) && !H.better(100, 80) && H.better(40, 9) && !H.better(40, 15) && H.better(Infinity, 50) && !H.better(50, Infinity));

// --- Umschalter ------------------------------------------------------------------------------------------------------
let S = new H.Switcher({ node: 'a', path: 'tcp', now: 0 });
ok('Verbindung weg (failed): sofort', (S.conn(1000, 'failed') || {}).why === 'lost');
ok('disconnected <= 2 s: noch nicht; > 2 s: wechseln', S.conn(1000, 'disconnected') === null && S.conn(2900, 'disconnected') === null
   && (S.conn(3100, 'disconnected') || {}).why === 'lost');
ok('disconnected kurz, dann connected: zurueckgesetzt', (S.conn(4000, 'connected'), S.conn(5000, 'disconnected')) === null && S.conn(6500, 'disconnected') === null);
ok('Leeren: sofort', S.draining().act === 'switch');
S = new H.Switcher({ node: 'a', path: 'tcp', now: 0 });
const t0 = 100000;     // schon laenger als 60 s auf a
let got = null, at = 0;
for (let t = t0; t <= t0 + 25000 && !got; t += 1000) { got = S.observe(t, 100, [{ node: 'b', score: 60 }]); at = t; }
ok(`besser fuer 20 s am Stueck: Wechsel nach ${(at - t0) / 1000} s`, got && got.to === 'b' && at - t0 === 20000, got);
S = new H.Switcher({ node: 'a', path: 'tcp', now: 0 });
let flap = null;
for (let t = t0; t <= t0 + 40000; t += 1000) { const x = S.observe(t, 100, [{ node: 'b', score: (t / 1000) % 15 === 0 ? 95 : 60 }]); if (x) flap = flap || t; }
ok('Unterbrechung setzt die 20 s zurueck (alle 15 s kurz nicht besser): kein Wechsel', flap === null, flap);
S = new H.Switcher({ node: 'a', path: 'tcp', now: 0 });
ok('nur wenig besser (100 -> 80): nie', [...Array(40).keys()].every(i => S.observe(t0 + i * 1000, 100, [{ node: 'b', score: 80 }]) === null));
S = new H.Switcher({ node: 'a', path: 'tcp', now: t0 });
let first = null;
for (let t = t0; t <= t0 + 90000 && !first; t += 1000) if (S.observe(t, 100, [{ node: 'b', score: 50 }])) first = t;
ok(`Mindestverweildauer 60 s nach dem Beitritt (Wechsel erst nach ${(first - t0) / 1000} s)`, first - t0 === 60000, first && first - t0);
// hoechstens 3 Wechsel je 10 min
S = new H.Switcher({ node: 'a', path: 'tcp', now: 0 });
const moves = []; let node = 'a', t = 0;
for (; t < 1200000; t += 1000) {
  const other = node === 'a' ? 'b' : 'a';
  const x = S.observe(t, 100, [{ node: other, score: 40 }]);
  if (x) { moves.push(t); node = x.to; S.moved(t, node, 'tcp', 'better'); }
}
const in10 = moves.filter(x => x < 600000 + moves[0]).length;
ok(`Hin und her: hoechstens 3 Wechsel je 10 min (${moves.length} in 20 min, ${in10} in den ersten 10 min ab dem ersten)`,
   in10 <= 3 && moves.every((x, i) => i < 3 || x - moves[i - 3] >= 600000) && moves.every((x, i) => i === 0 || x - moves[i - 1] >= 60000), moves);
ok('Abbruch zaehlt nicht gegen die Grenze (sofort, auch nach 3 Wechseln)', (S.conn(t, 'failed') || {}).why === 'lost');
// UDP
S = new H.Switcher({ node: 'a', path: 'udp', now: 0 });
ok('UDP-Verlust 25 % fuer < 3 s: bleiben, ab 3 s: TCP am selben Knoten',
   S.udp(1000, 0.25) === null && S.udp(3500, 0.25) === null && (S.udp(4000, 0.25) || {}).path === 'tcp');
S = new H.Switcher({ node: 'a', path: 'udp', now: 0 });
ok('UDP-Verlust springt (25 %, 5 %, 25 %): zurueckgesetzt', S.udp(0, 0.25) === null && S.udp(2000, 0.05) === null && S.udp(4000, 0.25) === null && S.udp(6500, 0.25) === null);
S.moved(10000, 'a', 'tcp', 'udp-loss');
let back = null;
for (let x = 10000; x < 60000 && !back; x += 1000) if (S.udp(x, 0, true)) back = x;
ok(`UDP wieder ok fuer 30 s: zurueck (nach ${(back - 10000) / 1000} s)`, back - 10000 === 30000, back);
S.moved(back, 'a', 'udp', 'udp'); S.moved(back + 5000, 'a', 'tcp', 'udp-loss');
let back2 = null;
for (let x = back + 5000; x < back + 600000 && !back2; x += 1000) if (S.udp(x, 0, true)) back2 = x;
ok(`Rueckkehr auf UDP hoechstens 1x je 5 min (naechste nach ${Math.round((back2 - back) / 1000)} s)`, back2 - back >= 300000, back2 && back2 - back);
ok('UDP-Probe scheitert: keine Rueckkehr', (() => { const s = new H.Switcher({ node: 'a', path: 'tcp' }); for (let x = 0; x < 400000; x += 1000) if (s.udp(x, 0, false)) return false; return true; })());

// --- make-before-break ---------------------------------------------------------------------------------------------
ok('altes Bein: nicht vor den ersten Rahmen, nach 2 s ohne Wartende, sonst spaetestens nach 15 s',
   !H.closeOld(5000, 0, 0) && !H.closeOld(1900, 0 + 1, 0) && H.closeOld(2001, 1, 0) && !H.closeOld(14000, 1, 2) && H.closeOld(15001, 1, 2));

// --- Kachelansicht -------------------------------------------------------------------------------------------------
ok('Spalten Desktop: 1/2/3/4/5 fuer 1/4/9/16/25, Handy 2', [1, 4, 9, 16, 25, 5, 10, 17].map(n => H.cols(n, 1280)).join() === '1,2,3,4,5,3,4,5'
   && H.cols(1, 390) === 1 && H.cols(12, 390) === 2);
ok('Last-N: Desktop 9, Handy 4', H.lastN(false) === 9 && H.lastN(true) === 4);
const devs = Array.from({ length: 30 }, (_, i) => 'd' + i), mv = {}; devs.slice(0, 9).forEach(d => { mv[d] = true; });
const tl = H.tiles(devs, mv, 'd12', 'grid');
ok('bis 25 Kacheln, Rest als "+5", 9 bewegt, 16 Standbild', tl.list.length === 25 && tl.more === 5 && tl.list.filter(x => x.moving).length === 9
   && tl.list.filter(x => !x.moving).length === 16 && tl.list[0].dev === 'd0');
const sp = H.tiles(devs.slice(0, 6), mv, 'd4', 'speaker');
ok('Sprecheransicht: Sprecher zuerst und gross', sp.list[0].dev === 'd4' && sp.list[0].big && sp.list.slice(1).every(x => !x.big) && sp.list.length === 6);
ok('Sprecheransicht ohne bekannten Sprecher: der erste', H.tiles(['x', 'y'], {}, null, 'speaker').list[0].dev === 'x');

// --- Qualitaet, Weg ------------------------------------------------------------------------------------------------
ok('Qualitaet: gut / mittel / schwach', H.quality({ rtt: 40, loss: 0 }) === 'good' && H.quality({ rtt: 250 }) === 'mid' && H.quality({ rtt: 40, loss: 0.03 }) === 'mid'
   && H.quality({ rtt: 40, freezes: 4 }) === 'bad' && H.quality({ rtt: 500 }) === 'bad' && H.quality(null) === '');
ok('Weg: udp -> UDP, tcp -> TCP, turn -> TLS', H.pathName('udp') === 'UDP' && H.pathName('tcp') === 'TCP' && H.pathName('turn') === 'TLS');

console.log(`\nHORN-LOGIK ${res.filter(Boolean).length}/${res.length}` + (res.every(Boolean) ? ' - ALLES GRUEN' : ' - FEHLER'));
process.exit(res.every(Boolean) ? 0 : 1);
