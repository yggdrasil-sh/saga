// Meeting-Ansicht (MEETING-SPEC.md): reine Logik in static/meeting.js gegen die gemeinsamen Testvektoren
// tests/meeting_vectors.json (dieselben Faelle prueft iOS und Android) und einige Zusatzfaelle - ohne Browser.
//   node tests/meeting_logik.js
'use strict';
const M = require('../static/meeting.js');
const V = require('./meeting_vectors.json');
const res = [];
function ok(name, cond, info) { res.push(!!cond); console.log((cond ? 'OK  ' : 'FAIL') + ' ' + name + (cond || info === undefined ? '' : ' -> ' + JSON.stringify(info))); }
const pick = t => ({ id: t.id, kind: t.kind, dev: t.dev, video: t.video, devName: t.devName });
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// --- meetingTiles / pickLayout / arrange (§1, §2, §4, §5) ----------------------------------------------------------
V.meetingTiles.forEach(c => {
  const tiles = M.meetingTiles(c.roster, c.media, c.prefs);
  const got = tiles.map(pick);
  ok(`Kacheln: ${c.name}`, eq(got, c.expect), got);
  const lay = M.pickLayout(tiles, 'auto');
  ok(`  Layout Auto = ${c.layout}`, lay === c.layout, lay);
  const a = M.arrange(tiles, lay, { pin: c.pin || null, speaker: c.speaker || null, spokeAt: (c.prefs || {}).spokeAt, now: (c.prefs || {}).now });
  const ga = { stage: a.stage, strip: a.strip, grid: a.grid, float: a.float };
  ok(`  Buehne/Streifen/Raster`, eq(ga, c.arrange), ga);
  if (c.hidden) { const h = M.hiddenNoVideo(c.roster, c.media, c.prefs); ok(`  ohne Video ausgeblendet: ${c.hidden}`, eq(h, c.hidden), h); }
});
// Stummsymbol und Sprechring an der Personenkachel: Mikro irgendeines Geraets, Pegel = Maximum
{
  const t = M.meetingTiles([{ device: 'a', who: 'x', name: 'X' }, { device: 'a2', who: 'x', name: 'X', role: 'companion' }, { device: 'm', who: 'me', me: true }],
    { a: { mic: false, level: 0.5 }, a2: { mic: true, level: 0.2 }, m: {} }, { self: 'm' });
  ok('Person: Mikro an, wenn irgendein Geraet an ist; Pegel nur von Geraeten mit Mikro (0,2)', t[0].mic === true && t[0].level === 0.2, t[0]);
}

// --- pickLayout ---------------------------------------------------------------------------------------------------
V.pickLayout.forEach(c => {
  const tiles = [];
  for (let i = 0; i < c.screens; i++) tiles.push({ id: 's' + i, kind: 'screen' });
  for (let i = 0; i < c.people; i++) tiles.push({ id: 'p' + i, kind: 'person', me: i === 0 });
  const got = M.pickLayout(tiles, c.choice, { reduced: !!c.reduced });
  ok(`pickLayout: ${c.name} -> ${c.expect}`, got === c.expect, got);
});

// --- Galerie, Seiten, Streifen (§2.2, §2.3, §8) ----------------------------------------------------------------------
V.galleryGrid.forEach(c => {
  const g = M.galleryGrid(c.n, c.w, c.h);
  ok(`Galerie ${c.w}x${c.h}, ${c.n} Kacheln: ${c.cols} Spalten, ${c.perPage} je Seite, ${c.video} mit Video`, g.cols === c.cols && g.perPage === c.perPage && g.video === c.video, g);
});
V.pages.forEach(c => {
  const list = Array.from({ length: c.n }, (_, i) => i), p = M.pageOf(list, c.page, c.perPage);
  ok(`Seiten: ${c.n} Kacheln, ${c.perPage} je Seite, Seite ${c.page} -> ${c.pages} Seiten, ${c.items} auf der Seite`, p.pages === c.pages && p.items.length === c.items, p);
});
V.stripSide.forEach(c => {
  const s = M.stripSide(c.w, c.h, c.layout);
  ok(`Streifen ${c.w}x${c.h} ${c.layout}: ${c.side} ${c.size} px`, s.side === c.side && s.size === c.size, s);
});
{
  const order = [{ id: 'a', who: 'a', video: true }, { id: 'b', who: 'b', video: true }, { id: 'c', who: 'c', video: true }, { id: 's', kind: 'screen', video: true },
    { id: 'me', who: 'me', me: true, video: true }, { id: 'd', who: 'd', video: true }];
  const v = M.videoFor(order, 3, { pin: 'd', speaker: 'c', spokeAt: { b: 5, a: 1 } });
  ok('Video-Hoechstzahl 3: Pin (d), ich, Sprecher (c) - der Bildschirm immer dazu', eq(Object.keys(v).sort(), ['c', 'd', 'me', 's']), v);
  const v2 = M.videoFor(order, 2, { spokeAt: { b: 5, a: 1 } });
  ok('... ohne Pin und Sprecher: ich, dann zuletzt gesprochen (b vor a)', eq(Object.keys(v2).sort(), ['b', 'me', 's']), v2);
  ok('Videos gleichzeitig: Handy 4, Tablet 6, Desktop 9, gross 16', M.videoBudget(390, 844) === 4 && M.videoBudget(800, 1000) === 6 && M.videoBudget(1280, 720) === 9 && M.videoBudget(2560, 1440) === 16);
}
// Galerie-Reihenfolge: Pin -> Bildschirm -> Video -> Sprecher (30 s) -> alphabetisch, ich zuletzt
{
  const t = [{ id: 'me', who: 'me', name: 'Ich', me: true }, { id: 'z', who: 'z', name: 'Zoe' }, { id: 'b', who: 'b', name: 'bert' }, { id: 'v', who: 'v', name: 'Vera', video: true },
    { id: 'r', who: 'r', name: 'Rolf' }, { id: 's', who: 's', name: 'S', kind: 'screen', video: true }, { id: 'p', who: 'p', name: 'Pia' }];
  const o = M.galleryOrder(t, { pin: 'p', now: 40000, spokeAt: { r: 20000, z: 1000 } }).map(x => x.id);
  ok('Galerie-Reihenfolge: Pin, Bildschirm, Video, Sprecher der letzten 30 s, Rest alphabetisch, ich zuletzt', eq(o, ['p', 's', 'v', 'r', 'b', 'z', 'me']), o);
}

// --- Sprecher-Hysterese (§2.3, S5) ---------------------------------------------------------------------------------
V.speakerHysteresis.forEach(c => {
  const S = new M.SpeakerPick(), at = {};
  let prev = null, changes = 0;
  for (let t = 0; t <= c.until; t += c.step) {
    const lv = {};
    c.tones.forEach(x => { if (t >= x.from && t < x.to) lv[x.who] = Math.max(lv[x.who] || 0, x.level); });
    const s = S.update(t, lv, c.me);
    if (s !== prev) { changes++; prev = s; }
    at[t] = s;
  }
  const bad = c.expect.filter(e => at[e.t] !== e.stage);
  ok(`Hysterese: ${c.name}`, !bad.length && changes === c.changes, { bad: bad.map(e => [e.t, e.stage, at[e.t]]), changes });
});
{
  const S = new M.SpeakerPick();
  S.update(0, { a: 0.5 }, 'me'); S.update(330, { a: 0.5 }, 'me');
  ok('Sprechring: > 0,03 fuer >= 300 ms', S.speaking.a === true);
  S.update(660, {}, 'me'); const tail = S.speaking.a === true; S.update(990, {}, 'me');
  ok('Sprechring: Nachlauf 600 ms (nach 330 ms noch an, nach 660 ms aus)', tail && !S.speaking.a, S.speaking);
  S.forget('a');
  ok('Person geht: nicht mehr auf der Buehne', S.cur === null);
}

// --- Leisten ausblenden, eigenes Bild, Einstellungen ---------------------------------------------------------------
V.hideControls.forEach((c, i) => {
  ok(`hideControls #${i + 1} -> ${c.expect}`, M.hideControls(c.idle, c.phase, c.video, c.sr, c.menu, c.notice, c.over) === c.expect);
});
ok('Leisten blenden nach 4 s aus', M.K.IDLE_MS === 4000);
V.selfPipSize.forEach(c => {
  const s = M.selfPipSize(c.w, c.h, c.ar, c.size);
  ok(`eigenes Bild ${c.w}x${c.h}, Verhaeltnis ${c.ar}, ${c.size}: ${c.expect.join('x')}`, s.w === c.expect[0] && s.h === c.expect[1], s);
});
ok('Layout-Wahl: gespeichert, Uebernahme aus saga-horn-view (grid -> gallery), sonst Auto',
   M.layoutPref('speaker') === 'speaker' && M.layoutPref(null, 'grid') === 'gallery' && M.layoutPref(null, 'speaker') === 'speaker' && M.layoutPref('x', null) === 'auto');
ok('Fensterklassen: Handy hochkant, Handy quer, Tablet, Desktop, gross',
   M.windowClass(390, 844) === 'phone' && M.windowClass(844, 390) === 'phone-land' && M.windowClass(800, 1000) === 'tablet' && M.windowClass(1280, 720) === 'desktop' && M.windowClass(1920, 1080) === 'large');

const n = res.filter(Boolean).length;
console.log(`\nMEETING-LOGIK ${n}/${res.length} - ${n === res.length ? 'ALLES GRUEN' : 'FEHLER'}`);
process.exit(n === res.length ? 0 : 1);
