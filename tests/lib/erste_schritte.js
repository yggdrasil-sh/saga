// Frage "Möchtest du eine kurze Einführung?" (Erste Schritte, static/firststeps.js) fuer frische Testkonten vorab mit
// "Nein" beantworten - sonst liegt der Dialog (.hk-ask) ueber der Oberflaeche (Anruf-Knoepfe, Chat-Einrichtung) und
// Browser-Reihen brechen ab (Bugs-DB 2428). Nach der Anmeldung im selben Kontext aufrufen; wirkt ab dem naechsten Laden.
// Python-Reihen mit eigenen Konten nehmen stattdessen tests/testkonten.py (ohne_einfuehrung, direkt in der DB).
// Den Dialog selbst pruefen tests/erste_schritte.py und tests/browser_leere_zustaende.js - dort NICHT verwenden.
//
//   const { ohneEinfuehrung } = require('./lib/erste_schritte');
//   await ohneEinfuehrung(p);                     // -> HTTP-Status (200; 403 = auf dem Server abgeschaltet; 0 = Fehler)
'use strict';

async function ohneEinfuehrung(p) {
  return p.evaluate(() => fetch('/api/first-steps', { method: 'POST', credentials: 'same-origin',
    headers: { 'X-Saga': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ answer: 'no' }) })
    .then(r => r.status, () => 0)).catch(() => 0);
}

module.exports = { ohneEinfuehrung };
