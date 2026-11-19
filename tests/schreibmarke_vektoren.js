// Bugs-DB 2177/2180: Vektoren fuer mapOffset (static/pages.js) - Schreibmarke bei fremder Aenderung im selben Block.
// Ohne Browser und Server: die Funktion wird aus dem Quelltext gelesen und in einer Sandbox ausgefuehrt.
// Schreibweise: "|" markiert die Schreibmarke in alt (vorher) und erwartet (nachher); neu ist alt nach der fremden Aenderung.
// Aufruf: node tests/schreibmarke_vektoren.js
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'pages.js'), 'utf8');
function grab(head) {                                   // Funktion bzw. Zeile ab head (Klammern zaehlen)
  const i = src.indexOf(head);
  if (i < 0) throw new Error('nicht gefunden: ' + head);
  if (!head.startsWith('function')) return src.slice(i, src.indexOf('\n', i));
  let d = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') d++;
    else if (src[k] === '}' && --d === 0) return src.slice(i, k + 1);
  }
  throw new Error('kein Ende: ' + head);
}
const box = {};
const anchor = src.includes('var ANCHOR_MIN') ? grab('var ANCHOR_MIN') : '';
vm.runInNewContext([anchor, grab('function surLo('), grab('function mapOffset('), 'this.mapOffset = mapOffset;'].join('\n'), box);

const res = [];
function ok(n, c, i) { res.push(!!c); console.log((c ? 'OK  ' : 'FAIL') + ' ' + n + (c ? '' : ' -> ' + JSON.stringify(i))); }
// alt/erwartet mit "|"; asArray: wie Tiptap (static/pm-editor.js seqOf) ein Eintrag je Position
function vec(name, alt, neu, want, asArray) {
  const off = alt.indexOf('|'), a = alt.replace('|', ''), w = want.indexOf('|');
  if (want.replace('|', '') !== neu) throw new Error('Vektor ' + name + ': erwartet passt nicht zu neu');
  const got = asArray ? box.mapOffset(a.split(''), neu.split(''), off) : box.mapOffset(a, neu, off);
  ok(`${name}: ${JSON.stringify(alt)} -> ${JSON.stringify(want)}${asArray ? ' (Folge)' : ''}`, got === w,
     { got: neu.slice(0, got) + '|' + neu.slice(got) });
}

// --- vor bzw. hinter der Marke (Praefix/Suffix) --------------------------------------------------------------------
vec('davor', 'Alt: En|de', 'Neu: Ende', 'Neu: En|de');
vec('dahinter', 'Anfang Mi|tte', 'Anfang Mitte und mehr', 'Anfang Mi|tte und mehr');
vec('leer', '|', 'Neu', '|Neu');
// --- Bugs-DB 2177: Mittelstueck um die Marke, Anker links und rechts ------------------------------------------------
vec('formatiert', 'Gruss 😀 scho|ene Welt', 'Gruss 😀 <b>schoene</b> Welt', 'Gruss 😀 <b>scho|ene</b> Welt');
vec('beidseits', 'Anfang Mit|te Schluss', 'Vorne 😀 Anfang Mitte Schluss hinten', 'Vorne 😀 Anfang Mit|te Schluss hinten');
vec('gemischt', 'Anfang Mit|te Schluss', 'Vorne Anfang <i>Mitte</i> Schluss 😀 hinten', 'Vorne Anfang <i>Mit|te</i> Schluss 😀 hinten');
vec('beidseits', 'Anfang Mit|te Schluss', 'Vorne 😀 Anfang Mitte Schluss hinten', 'Vorne 😀 Anfang Mit|te Schluss hinten', true);
vec('Ersatzzeichen', 'a😀|b', 'x😀by', 'x😀|by');
// --- Bugs-DB 2180: Text um die Marke ersetzt - ein, zwei zufaellig gleiche Zeichen sind kein Anker -------------------
vec('ersetzt', 'wo|rld', 'there', 'there|');
vec('ersetzt', 'Hallo wo|rld', 'Hallo there', 'Hallo there|');
vec('ersetzt', 'Hallo wo|rld!', 'Hallo there!', 'Hallo there|!');
vec('ersetzt', 'wo|rld', 'there', 'there|', true);
vec('ersetzt (2 gleiche)', 'wo|rld', 'thorny', 'thorny|');
vec('ersetzt', 'Anfang Mit|te Schluss', 'Vorne Anfang Schluss hinten', 'Vorne Anfang Schluss hinten|');
// --- kurzes Mittelstueck: zaehlt, wenn es ganz erhalten ist -------------------------------------------------------
vec('kurz erhalten', 'a|b', 'xaby', 'xa|by');
vec('drei gleiche', 'abc|def', 'ZZabcQQ', 'ZZabc|QQ');

const n = res.filter(Boolean).length;
console.log(`SCHREIBMARKE-VEKTOREN ${n}/${res.length}` + (n === res.length ? ' - ALLES GRUEN' : ' - FEHLER'));
process.exit(n === res.length ? 0 : 1);
