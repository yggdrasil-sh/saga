// Saga - persoenliches Backup (2.3): Einstellungen -> Reiter "Mein Backup" (static/settings.js ruft HKBackup.section(body)
// auf). Eigenes Ziel (Nextcloud per Login Flow v2 oder WebDAV), eigener age-Schluessel, Auswahl, Zeitplan, "Jetzt sichern",
// Backups im Ziel, Einspielen ins eigene Konto. Der private Schluessel wird nur einmal angezeigt (Erzeugen) bzw. nur fuer
// den Einspiel-Vorgang mitgeschickt. Alle Texte per textContent. Braucht static/common.js.
(function () {
  'use strict';
  var HK = window.HK; if (!HK || !document.getElementById('hk-user')) return;
  var el = HK.el, api = HK.api;
  var CATS = [['pages', T('Seiten und Unterseiten (inkl. Datenbanken mit Einträgen)')], ['templates', T('Vorlagen')], ['archive', T('Archiv')],
              ['trash', T('Papierkorb')], ['versions', T('Versionen')], ['files', T('Eigene Dateien und Bilder')],
              ['comments', T('Eigene Kommentare auf eigenen Seiten')],
              ['comments_others', T('Kommentare anderer Personen auf meinen Seiten')], ['tasks', T('Aufgaben')], ['bookmarks', T('Lesezeichen')],
              ['settings', T('Profil und Einstellungen')]];
  var WD = [T('Montag'), T('Dienstag'), T('Mittwoch'), T('Donnerstag'), T('Freitag'), T('Samstag'), T('Sonntag')];

  function size(n) {
    if (n == null) return '?';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0; n = +n;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : String(n)) + ' ' + u[i];
  }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : '–'; }
  function field(label, input) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input); return l; }
  function input(type, ph, v) { var i = el('input', 'hk-in'); i.type = type; if (ph) i.placeholder = ph; if (v != null) i.value = v; return i; }
  function button(txt, primary) { var b = el('button', primary ? 'btn btn-primary' : 'btn', txt); b.type = 'button'; return b; }
  function bar() { var b = el('div', 'hk-actions'); for (var i = 0; i < arguments.length; i++) b.appendChild(arguments[i]); return b; }
  function sw(label, on, hint) {
    var l = el('label', 'hk-avset-row'), i = el('input', 'hk-switch'); i.type = 'checkbox'; i.setAttribute('role', 'switch'); i.checked = !!on;
    var t = el('span', null, label); if (hint) { t.appendChild(el('br')); t.appendChild(el('small', 'hk-hint', hint)); }
    l.appendChild(t); l.appendChild(i); l.input = i; return l;
  }
  function msgBox() { return el('p', 'hk-status'); }
  function say(m, text, ok) { m.textContent = text || ''; m.className = 'hk-status' + (text ? (ok ? ' ok' : ' err') : ''); }

  function section(body) {
    var sec = el('section', 'hk-set hk-set-backup'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Mein Backup')));
    sec.appendChild(el('p', 'hk-hint', T('Sichere deinen eigenen Workspace verschlüsselt in deine eigene Nextcloud oder ein anderes WebDAV-Ziel. Enthalten ist nur, was dir gehört – was andere mit dir geteilt haben, bleibt außen vor. Lesbar nur mit deinem privaten Schlüssel, auch ohne Saga.')));
    var box = el('div', 'ub'); sec.appendChild(box);
    var timer = null, S = null;
    function poll(ms) { clearTimeout(timer); timer = setTimeout(function () { if (document.contains(sec)) load(); }, ms); }
    function load() {
      return api('GET', '/api/account/backup?pages=1').then(function (s) { S = s; draw(); })
        .catch(function (e) { box.textContent = T('Nicht geladen: {err}', { err: e.message }); });
    }

    function draw() {
      var s = S; box.textContent = '';
      if (!s.allowed) { box.appendChild(el('p', 'hk-status err', T('Persönliche Backups sind auf diesem Server abgeschaltet.'))); return; }
      box.appendChild(statusPart(s));
      box.appendChild(targetPart(s));
      box.appendChild(keyPart(s));
      box.appendChild(contentPart(s));
      box.appendChild(planPart(s));
      box.appendChild(restorePart(s));
      if (s.running || s.flow.state === 'wait') poll(3000);
    }

    // --- Status + Jetzt sichern ---
    function statusPart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Status')));
      var dl = el('dl', 'ub-dl');
      function row(k, v) { dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', null, v)); }
      row(T('Letztes erfolgreiches Backup'), s.last_ok ? when(s.last_ok) : T('noch keines'));
      row(T('Nächster Lauf'), s.next ? when(s.next) : T('Zeitplan aus'));
      w.appendChild(dl);
      var last = s.runs[0];
      if (s.running && last) w.appendChild(el('p', 'hk-status', '⏳ ' + T('Backup läuft:') + ' ' + (last.step || '…')));
      else if (last && last.finished && !last.ok) w.appendChild(el('p', 'hk-status err', '✗ ' + last.error));
      var m = msgBox(), go = button(T('Jetzt sichern'), true);
      go.disabled = !s.ready || s.running;
      go.addEventListener('click', function () {
        go.disabled = true; say(m, T('Gestartet …'), true);
        api('POST', '/api/account/backup/run').then(function () { poll(1500); }).catch(function (e) { say(m, e.message); go.disabled = false; });
      });
      var show = button(T('Backups im Ziel anzeigen'));
      show.disabled = !s.target;
      var list = el('div', 'ub-remote');
      show.addEventListener('click', function () { remoteList(list, m); });
      w.appendChild(bar(go, show)); w.appendChild(m);
      if (!s.ready) w.appendChild(el('p', 'hk-hint', T('Zum Sichern fehlt noch: ein Ziel und mindestens ein öffentlicher Schlüssel.')));
      if (s.runs.length) {
        var ul = el('ul', 'ub-runs');
        s.runs.slice(0, 5).forEach(function (r) {
          var txt = when(r.started) + ' · ' + (r.ok ? '✓ ' + size(r.size) : r.finished ? '✗ ' + r.error : '⏳ ' + (r.step || '…'));
          ul.appendChild(el('li', r.ok ? null : 'ub-bad', txt));
        });
        w.appendChild(ul);
      }
      w.appendChild(list);
      return w;
    }

    function remoteList(list, m) {
      list.textContent = T('Lade …');
      api('GET', '/api/account/backup/remote').then(function (d) {
        list.textContent = '';
        if (!d.items.length) { list.appendChild(el('p', 'hk-hint', T('Noch keine Backups im Ziel.'))); return; }
        var ul = el('ul', 'ub-runs');
        d.items.forEach(function (x) {
          var li = el('li');
          li.appendChild(el('code', null, x.name));
          li.appendChild(document.createTextNode(' · ' + size(x.size) + (x.complete ? '' : ' · ' + T('unvollständig'))));
          if (x.complete) {
            var a = el('a', 'ub-link', T('Herunterladen')); a.href = '#';
            a.addEventListener('click', function (e) {          // mit X-Saga (API), dann als Datei speichern
              e.preventDefault(); say(m, T('Lade …'), true);
              fetch('/api/account/backup/remote/' + encodeURIComponent(x.name), { credentials: 'same-origin', headers: { 'X-Saga': '1' } })
                .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.blob(); })
                .then(function (b) {
                  var u = URL.createObjectURL(b), d = el('a'); d.href = u; d.download = x.name; document.body.appendChild(d); d.click(); d.remove();
                  setTimeout(function () { URL.revokeObjectURL(u); }, 60000); say(m, '');
                }).catch(function (err) { say(m, err.message); });
            });
            li.appendChild(document.createTextNode(' · ')); li.appendChild(a);
          }
          ul.appendChild(li);
        });
        list.appendChild(ul);
      }).catch(function (e) { list.textContent = ''; say(m, e.message); });
    }

    // --- Ziel ---
    function targetPart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Ziel')));
      var m = msgBox();
      if (s.flow.state === 'wait' && s.flow.login) {
        w.appendChild(el('p', null, T('Nextcloud gefunden. Melde dich dort an und erlaube den Zugriff – Saga bekommt ein eigenes App-Passwort (dein Passwort sieht Saga nie).')));
        var a = el('a', 'btn btn-primary', T('In Nextcloud anmelden') + ' ↗'); a.href = s.flow.login; a.target = '_blank'; a.rel = 'noopener noreferrer';
        var cancel = button(T('Abbrechen'));
        cancel.addEventListener('click', function () { api('POST', '/api/account/backup/flow/cancel').then(load); });
        w.appendChild(bar(a, cancel));
        return w;
      }
      if (s.flow.message) say(m, s.flow.message);
      else if (s.flow.state === 'ok') say(m, T('Nextcloud verbunden.'), true);
      if (s.target) {
        var t = s.target;
        w.appendChild(el('p', null, (t.kind === 'nextcloud' ? 'Nextcloud' : 'WebDAV') + ' · ' + t.address + (t.user ? ' · ' + t.user : '') +
          ' · ' + T('Ordner') + ' „' + t.folder + '“' + (t.flow ? ' · ' + T('App-Passwort (Login Flow)') : '')));
        if (!t.allowed) w.appendChild(el('p', 'hk-status err', T('Das Ziel ist nicht (mehr) erlaubt – bitte neu verbinden.')));
        var test = button(T('Verbindung prüfen')), off = button(T('Trennen'));
        test.addEventListener('click', function () {
          say(m, T('Prüfe …'), true);
          api('POST', '/api/account/backup/test').then(function (d) {
            say(m, T('Verbindung in Ordnung. Frei im Ziel: {free}.', { free: d.free == null ? T('unbekannt') : size(d.free) }), true);
          }).catch(function (e) { say(m, e.message); });
        });
        off.addEventListener('click', function () {
          HK.ask({ title: T('Ziel trennen?'), text: T('Vorhandene Backups im Ziel bleiben erhalten. Ein App-Passwort aus dem Nextcloud-Login wird widerrufen.'), ok: T('Trennen'), danger: true })
            .then(function (yes) { if (yes) api('DELETE', '/api/account/backup/target').then(load).catch(function (e) { say(m, e.message); }); });
        });
        w.appendChild(bar(test, off)); w.appendChild(m);
        return w;
      }
      w.appendChild(el('p', 'hk-hint', T('Nextcloud: nur die Adresse eingeben – die Anmeldung läuft dann in Nextcloud. Anderes WebDAV: Adresse des Ordners plus Benutzer und Passwort. Nur https-Adressen im Internet (interne Server nur, wenn der Admin sie freigibt).')));
      var url = input('url', 'https://cloud.example.org'), user = input('text', ''), pw = input('password', ''), folder = input('text', '', 'Saga-Backup');
      user.autocomplete = 'off'; pw.autocomplete = 'new-password';
      w.appendChild(field(T('Adresse'), url));
      w.appendChild(field(T('Benutzer'), user));
      w.appendChild(field(T('Passwort'), pw));
      w.appendChild(field(T('Ordner'), folder));
      var go = button(T('Verbinden'), true);
      go.addEventListener('click', function () {
        say(m, T('Prüfe …'), true); go.disabled = true;
        api('POST', '/api/account/backup/connect', { url: url.value.trim(), user: user.value.trim(), password: pw.value, folder: folder.value.trim() })
          .then(function (d) {
            pw.value = '';
            if (d.flow) window.open(d.flow, '_blank', 'noopener');
            load();
          }).catch(function (e) { say(m, e.message); go.disabled = false; });
      });
      w.appendChild(bar(go)); w.appendChild(m);
      return w;
    }

    // --- Schluessel ---
    function keyPart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Verschlüsselung')));
      var m = msgBox();
      if (s.keys.length) {
        var ul = el('ul', 'ub-runs');
        s.keys.forEach(function (k) {
          var li = el('li'); li.appendChild(el('code', null, k.short)); li.appendChild(document.createTextNode(' · ' + k.fp + ' '));
          var rm = el('button', 'btn ub-sm', T('Entfernen')); rm.type = 'button';
          rm.addEventListener('click', function () {
            HK.ask({ title: T('Schlüssel entfernen?'), text: T('Neue Backups sind dann nicht mehr mit ihm lesbar. Ältere Backups bleiben mit ihm lesbar.'), ok: T('Entfernen'), danger: true })
              .then(function (yes) { if (yes) api('DELETE', '/api/account/backup/key/' + encodeURIComponent(k.fp)).then(load); });
          });
          li.appendChild(rm); ul.appendChild(li);
        });
        w.appendChild(ul);
      } else w.appendChild(el('p', 'hk-hint', T('Noch kein Schlüssel – ohne Schlüssel sichert Saga nicht.')));
      var gen = button(T('Schlüsselpaar erzeugen')), area = el('div');
      gen.disabled = !s.age_ok;
      gen.addEventListener('click', function () {
        api('POST', '/api/account/backup/key/generate').then(function (d) { showNewKey(area, d, m); gen.hidden = true; }).catch(function (e) { say(m, e.message); });
      });
      w.appendChild(bar(gen)); w.appendChild(area);
      var pub = el('textarea', 'hk-in ub-key'); pub.rows = 2; pub.placeholder = 'age1…'; pub.spellcheck = false;
      w.appendChild(field(T('Eigenen öffentlichen Schlüssel eintragen'), pub));
      var add = button(T('Hinzufügen'));
      add.addEventListener('click', function () {
        api('POST', '/api/account/backup/key', { key: pub.value }).then(function () { pub.value = ''; load(); }).catch(function (e) { say(m, e.message); });
      });
      w.appendChild(bar(add)); w.appendChild(m);
      return w;
    }

    function showNewKey(area, d, m) {
      area.textContent = '';
      area.appendChild(el('p', null, T('Neuer privater Schlüssel – wird nur jetzt angezeigt und von Saga nicht gespeichert:')));
      var ta = el('textarea', 'hk-in ub-key'); ta.readOnly = true; ta.rows = 4; ta.value = d.private; ta.spellcheck = false;
      area.appendChild(ta);
      var dl = el('a', 'btn', T('Als Datei herunterladen'));
      dl.href = URL.createObjectURL(new Blob([d.private], { type: 'text/plain' })); dl.download = d.filename;
      area.appendChild(bar(dl));
      area.appendChild(el('p', 'hk-hint', T('Lege ihn an einem sicheren Ort ab (Passwort-Manager, Ausdruck) – nicht auf diesem Server. Ohne ihn lassen sich deine Backups nicht wiederherstellen.')));
      var last = input('text', ''); last.maxLength = 6; last.autocomplete = 'off';
      area.appendChild(field(T('Zur Bestätigung: die letzten 6 Zeichen des privaten Schlüssels'), last));
      var ok = button(T('Schlüssel ist sicher abgelegt – eintragen'), true), drop = button(T('Verwerfen'));
      ok.addEventListener('click', function () {
        api('POST', '/api/account/backup/key/confirm', { last6: last.value.trim() }).then(function () { ta.value = ''; URL.revokeObjectURL(dl.href); load(); })
          .catch(function (e) { say(m, e.message); });
      });
      drop.addEventListener('click', function () { api('POST', '/api/account/backup/key/confirm', { drop: true }).then(load); });
      area.appendChild(bar(ok, drop));
    }

    // --- Inhalt: Kategorien + ausgeschlossene Seitenbaeume ---
    function contentPart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Was gesichert wird')));
      var sws = {};
      CATS.forEach(function (c) {
        var hint = c[0] === 'comments_others' ? T('Standard aus – die Kommentare gehören den Personen, die sie geschrieben haben.') : '';
        var r = sw(c[1], s.cats[c[0]], hint); sws[c[0]] = r.input; w.appendChild(r);
      });
      w.appendChild(el('p', 'hk-hint', T('Nie enthalten: Seiten und Dateien anderer Personen (auch „Mit mir geteilt“) und Chats – sie sind Ende-zu-Ende-verschlüsselt und gehören allen Beteiligten.')));
      w.appendChild(el('h4', null, T('Seiten nicht sichern')));
      var ex = (s.exclude || []).slice(), byId = {};
      (s.pages || []).forEach(function (p) { byId[p.id] = p; });
      var list = el('ul', 'ub-runs');
      function drawEx() {
        list.textContent = '';
        if (!ex.length) list.appendChild(el('li', 'hk-hint', T('Keine – alle eigenen Seiten werden gesichert.')));
        ex.forEach(function (id, i) {
          var p = byId[id], li = el('li', null, (p ? (p.icon ? p.icon + ' ' : '') + (p.title || T('Unbenannt')) : '#' + id) + ' ' + T('(mit Unterseiten)') + ' ');
          var rm = el('button', 'btn ub-sm', '✕'); rm.type = 'button'; rm.setAttribute('aria-label', T('Entfernen'));
          rm.addEventListener('click', function () { ex.splice(i, 1); drawEx(); });
          li.appendChild(rm); list.appendChild(li);
        });
      }
      drawEx(); w.appendChild(list);
      var sel = el('select', 'hk-in'), o0 = el('option', null, T('– Seite wählen –')); o0.value = ''; sel.appendChild(o0);
      var kids = {};
      (s.pages || []).forEach(function (p) { var k = byId[p.parent] ? p.parent : 0; (kids[k] = kids[k] || []).push(p); });
      (function walk(k, d) {
        (kids[k] || []).forEach(function (p) {
          var o = el('option', null, '  '.repeat(d) + (p.icon ? p.icon + ' ' : '') + (p.title || T('Unbenannt'))); o.value = String(p.id); sel.appendChild(o);
          if (d < 12) walk(p.id, d + 1);
        });
      })(0, 0);
      sel.addEventListener('change', function () { var v = +sel.value; if (v && ex.indexOf(v) < 0) { ex.push(v); drawEx(); } sel.value = ''; });
      w.appendChild(field(T('Ausschließen'), sel));
      var m = msgBox(), save = button(T('Speichern'), true);
      save.addEventListener('click', function () {
        var cats = {}; Object.keys(sws).forEach(function (k) { cats[k] = sws[k].checked; });
        api('PUT', '/api/account/backup/settings', { cats: cats, exclude: ex }).then(function (d) { S = Object.assign(S, d); say(m, T('Gespeichert.'), true); })
          .catch(function (e) { say(m, e.message); });
      });
      w.appendChild(bar(save)); w.appendChild(m);
      return w;
    }

    // --- Zeitplan ---
    function planPart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Zeitplan und Aufbewahrung')));
      var sel = el('select', 'hk-in');
      [['off', T('aus')], ['weekly', T('wöchentlich')], ['daily', T('täglich')]].forEach(function (o) {
        var x = el('option', null, o[1]); x.value = o[0]; x.disabled = o[0] === 'daily' && s.limits.max_schedule === 'weekly'; sel.appendChild(x);
      });
      sel.value = s.schedule;
      var tm = input('time', '', s.time), wd = el('select', 'hk-in');
      WD.forEach(function (n, i) { var x = el('option', null, n); x.value = String(i); wd.appendChild(x); });
      wd.value = String(s.weekday);
      var keep = input('number', '', s.keep); keep.min = 1; keep.max = 60;
      var wdf = field(T('Wochentag'), wd);
      function vis() { wdf.hidden = sel.value !== 'weekly'; }
      sel.addEventListener('change', vis); vis();
      w.appendChild(field(T('Automatisch sichern'), sel));
      w.appendChild(field(T('Uhrzeit'), tm));
      w.appendChild(wdf);
      w.appendChild(field(T('Backups behalten'), keep));
      w.appendChild(el('p', 'hk-hint', T('Die neuesten Backups bleiben im Ziel, ältere löscht Saga. Höchstens {mb} MB je Backup.', { mb: s.limits.max_mb })));
      var m = msgBox(), save = button(T('Speichern'), true);
      save.addEventListener('click', function () {
        api('PUT', '/api/account/backup/settings', { schedule: sel.value, time: tm.value, weekday: +wd.value, keep: +keep.value })
          .then(function (d) { S = d; say(m, T('Gespeichert.'), true); }).catch(function (e) { say(m, e.message); });
      });
      w.appendChild(bar(save)); w.appendChild(m);
      return w;
    }

    // --- Einspielen ---
    function restorePart(s) {
      var w = el('div', 'ub-part');
      w.appendChild(el('h4', null, T('Backup einspielen')));
      w.appendChild(el('p', 'hk-hint', T('Spielt ein persönliches Backup in DEIN Konto ein – als neue Seiten (Verweise, Datenbanken und Bilder werden angepasst). Bestehende Seiten bleiben unverändert.')));
      var file = el('input', 'hk-in'); file.type = 'file'; file.accept = '.age,.zip';
      w.appendChild(field(T('Backup-Datei'), file));
      var remote = el('select', 'hk-in'), r0 = el('option', null, T('– oder aus dem eigenen Ziel –')); r0.value = ''; remote.appendChild(r0);
      if (s.target) {
        api('GET', '/api/account/backup/remote').then(function (d) {
          d.items.filter(function (x) { return x.complete; }).forEach(function (x) { var o = el('option', null, x.name); o.value = x.name; remote.appendChild(o); });
        }).catch(function () {});
        w.appendChild(field(T('Aus dem Ziel'), remote));
      }
      var key = el('textarea', 'hk-in ub-key'); key.rows = 3; key.placeholder = 'AGE-SECRET-KEY-1…'; key.spellcheck = false; key.autocomplete = 'off';
      w.appendChild(field(T('Privater Schlüssel'), key));
      w.appendChild(el('p', 'hk-hint', T('Der private Schlüssel geht nur für diesen Vorgang an den Server und wird nicht gespeichert. Für ein schon entschlüsseltes .zip nicht nötig.')));
      var fo = sw(T('In eine neue Seite „Wiederhergestellt <Datum>“'), true), tr = sw(T('Papierkorb mit einspielen'), false),
          ta = sw(T('Aufgaben übernehmen'), false), bm = sw(T('Lesezeichen übernehmen'), false), ko = sw(T('Chat-Kontakte übernehmen'), false);
      [fo, tr, ta, bm, ko].forEach(function (x) { w.appendChild(x); });
      var m = msgBox(), go = button(T('Einspielen'), true);
      go.addEventListener('click', function () {
        var f = file.files && file.files[0];
        if (!f && !remote.value) { say(m, T('Bitte eine Backup-Datei wählen.')); return; }
        var opts = { key: key.value, folder: fo.input.checked, trash: tr.input.checked, tasks: ta.input.checked, bookmarks: bm.input.checked, contacts: ko.input.checked };
        go.disabled = true; say(m, f ? T('Lade hoch …') : T('Hole aus dem Ziel …'), true);
        var first = f ? fetch('/api/account/backup/restore/upload', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/octet-stream' }, body: f })
          .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); })
          .then(function (d) { opts.token = d.token; }) : Promise.resolve((opts.remote = remote.value));
        first.then(function () { say(m, T('Spiele ein …'), true); return api('POST', '/api/account/backup/restore', opts); })
          .then(function (d) {
            key.value = ''; go.disabled = false;
            say(m, T('Eingespielt: {pages} Seiten, {files} Dateien.', { pages: d.pages, files: d.files }), true);
            if (window.HKNotes && HKNotes.reloadTree) HKNotes.reloadTree();
          }).catch(function (e) { say(m, e.message); go.disabled = false; });
      });
      w.appendChild(bar(go)); w.appendChild(m);
      return w;
    }

    load();
  }

  window.HKBackup = { section: section };
})();
