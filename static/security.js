/* Einstellungen -> Konto -> "Anmeldung & Sicherheit": Passkeys, Authenticator-App (TOTP), Wiederherstellungscodes
   (Server: saga/mfa.py). Auch eigenstaendig auf /security (Einrichtung, wenn der Admin einen zweiten Faktor verlangt).
   Aenderungen verlangen eine frische Anmeldung - sonst fragt der Abschnitt nach Passwort, Code oder Passkey. */
(function () {
  'use strict';
  var el = HK.el, api = HK.api, PK = window.SagaPasskey;
  var fDate = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  function when(t) { return fDate.format(new Date(t * 1000)); }
  function button(txt, cls, fn) { var b = el('button', 'btn' + (cls ? ' ' + cls : ''), txt); b.type = 'button'; if (fn) b.addEventListener('click', fn); return b; }
  function status(box, txt, cls) { box.textContent = txt || ''; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }
  function field(label, input) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input); return l; }
  function originMsg(S) {
    return PK && S.origin ? PK.originProblem(S.origin, T('Diese Seite wurde über {here} geöffnet, Saga ist aber für {base} eingerichtet (BASE_URL). Passkeys gehen nur über {base}.')) : '';
  }

  function section(host, opts) {
    opts = opts || {};
    var wrap = el('div', 'mfa'); wrap.id = 'hk-security'; host.appendChild(wrap);
    if (!opts.noTitle) wrap.appendChild(el('h4', null, T('Anmeldung & Sicherheit')));
    var confirmBox = el('div', 'mfa-confirm'); confirmBox.hidden = true; wrap.appendChild(confirmBox);
    var codesBox = el('div', 'mfa-codes-box'); codesBox.hidden = true; wrap.appendChild(codesBox);
    var body = el('div'); wrap.appendChild(body);
    var S = null;

    function load() {
      return api('GET', '/api/account/security').then(function (s) { S = s; render(); if (opts.onChange) opts.onChange(s); })
        .catch(function (e) { body.textContent = T('Nicht geladen: {err}', { err: e.message }); });
    }

    // Aenderungen nur mit frischer Anmeldung bzw. Bestaetigung (2.4: Faktor hinzufuegen, sobald einer da ist, Entfernen,
    // neue Codes): bei 403 {reauth} erst bestaetigen (Passwort, Code oder Passkey), dann wiederholen
    function guarded(fn) {
      return fn().catch(function (e) {
        if (e.status === 403 && e.data && e.data.reauth) return confirmIdentity().then(fn);
        throw e;
      });
    }
    function confirmIdentity() {
      return new Promise(function (resolve, reject) {
        confirmBox.textContent = ''; confirmBox.hidden = false;
        confirmBox.appendChild(el('p', null, T('Zur Sicherheit bitte bestätigen, dass du es bist.')));
        var m = (S && S.methods) || ['password'];
        var pw = el('input', 'hk-in'); pw.type = 'password'; pw.autocomplete = 'current-password'; pw.maxLength = 200;
        var label = m.indexOf('totp') >= 0 ? T('Passwort oder Code aus der Authenticator-App') : T('Passwort');
        confirmBox.appendChild(field(label, pw));
        var st = el('div', 'hk-status'), bar = el('div', 'hk-actions');
        var ok = button(T('Bestätigen'), 'btn-primary'), cancel = button(T('Abbrechen'));
        bar.appendChild(ok);
        if (m.indexOf('passkey') >= 0 && PK && PK.supported() && !originMsg(S)) {
          bar.appendChild(button('🔑 ' + T('Mit Passkey bestätigen'), '', function () {
            status(st, T('Warte auf den Passkey …'));
            api('POST', '/api/account/security/options').then(function (d) { return PK.get(d.options); })
              .then(function (cred) { return api('POST', '/api/account/security/confirm', { passkey: cred }); })
              .then(done).catch(function (e) { status(st, PK.cancelled(e) ? T('Abgebrochen.') : e.message, 'err'); });
          }));
        }
        bar.appendChild(cancel); confirmBox.appendChild(bar); confirmBox.appendChild(st);
        function done() { confirmBox.hidden = true; confirmBox.textContent = ''; resolve(); }
        function send() {
          var v = pw.value.trim();
          if (!v) { pw.focus(); return; }
          ok.disabled = true; status(st, T('Wird geprüft …'));
          api('POST', '/api/account/security/confirm', /^\d{6}$/.test(v) ? { code: v } : { password: pw.value })
            .then(done).catch(function (e) { ok.disabled = false; status(st, e.message, 'err'); pw.select(); });
        }
        ok.addEventListener('click', send);
        pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });
        cancel.addEventListener('click', function () { confirmBox.hidden = true; confirmBox.textContent = ''; reject(new Error(T('Abgebrochen.'))); });
        pw.focus();
      });
    }

    // Wiederherstellungscodes: nur jetzt sichtbar
    function showCodes(codes) {
      if (!codes || !codes.length) return;
      codesBox.textContent = ''; codesBox.hidden = false;
      codesBox.appendChild(el('h5', null, T('Deine Wiederherstellungscodes')));
      codesBox.appendChild(el('p', null, T('Damit kommst du in dein Konto, wenn Passkey oder Handy fehlen. Jeder Code gilt einmal. Speichere sie an einem sicheren Ort (z. B. im Passwort-Manager) – Saga zeigt sie nur jetzt.')));
      var list = el('ol', 'mfa-codes'); list.setAttribute('aria-label', T('Wiederherstellungscodes'));
      codes.forEach(function (c) { list.appendChild(el('li', null, c)); });
      codesBox.appendChild(list);
      // Datei/Zwischenablage mit Konto und Datum - wer mehrere Konten hat, findet die richtigen Codes wieder
      var txt = T('Saga – Wiederherstellungscodes') + '\n' + T('Konto: {user}', { user: S.user || '' }) + ' · ' + location.origin + '\n' +
        T('Erstellt: {date}', { date: new Date().toLocaleString(window.SAGA_LOCALE) }) + '\n\n' + codes.join('\n') + '\n';
      var bar = el('div', 'hk-actions');
      bar.appendChild(button(T('Kopieren'), '', function () {
        (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(function () { HK.toast(T('Kopiert')); },
          function () { HK.toast(T('Kopieren nicht möglich – bitte abschreiben.')); });
      }));
      bar.appendChild(button(T('Als Datei speichern'), '', function () {
        var a = el('a'); a.href = URL.createObjectURL(new Blob([txt], { type: 'text/plain' })); a.download = 'saga-recovery-codes' + (S.user ? '-' + String(S.user).replace(/[^\w.-]+/g, '_') : '') + '.txt';
        document.body.appendChild(a); a.click(); a.remove(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
      }));
      bar.appendChild(button(T('Ich habe sie gespeichert'), 'btn-primary', function () { codesBox.hidden = true; codesBox.textContent = ''; }));
      codesBox.appendChild(bar);
      codesBox.scrollIntoView({ block: 'nearest' });
    }

    function render() {
      body.textContent = '';
      if (!S.supported) { body.appendChild(el('p', 'hk-hint', T('Zwei-Faktor-Anmeldung regelt dein Anmeldedienst (SSO).'))); return; }
      body.appendChild(el('p', 'hk-hint', T('Schütze dein Konto zusätzlich zum Passwort. Mit einem Passkey meldest du dich per Fingerabdruck, Gesicht oder Geräte-PIN an – ganz ohne Passwort. Alternativ fragt Saga nach dem Passwort einen Code aus einer Authenticator-App ab.')));
      var state = el('p', 'mfa-state ' + (S.enabled ? 'on' : S.required ? 'warn' : 'off'),
        S.enabled ? T('✓ Zweiter Faktor ist eingeschaltet.') : S.required ? T('Dein Admin verlangt einen zweiten Faktor – bitte richte einen Passkey oder eine Authenticator-App ein.') : T('Zweiter Faktor ist aus.'));
      state.setAttribute('role', 'status'); body.appendChild(state);
      passkeys(); totp(); recovery();
    }

    // Hinweis, wenn der letzte Faktor geht (Bughunt 2: sonst war 2FA danach still aus)
    function last(text, isLast) {
      if (!isLast) return text;
      var n = el('span'); n.appendChild(document.createTextNode(text)); n.appendChild(el('br')); n.appendChild(el('br'));
      n.appendChild(el('strong', 'mfa-last', T('Das ist dein letzter zweiter Faktor: Danach ist die Zwei-Faktor-Anmeldung aus und deine Wiederherstellungscodes verfallen.')));
      return n;
    }
    function passkeys() {
      var box = el('div', 'mfa-block'); body.appendChild(box);
      box.appendChild(el('h5', null, T('Passkeys')));
      box.appendChild(el('p', 'hk-hint', T('Ein Passkey liegt im Passwort-Manager deines Geräts (z. B. iCloud-Schlüsselbund, Google Passwortmanager) oder auf einem Sicherheitsschlüssel. Er funktioniert nur mit dieser Saga – Phishing-Seiten können ihn nicht abgreifen.')));
      var list = el('div', 'hk-tok-list'); box.appendChild(list);
      if (!S.passkeys.length) list.appendChild(el('p', 'hk-hint', T('Noch kein Passkey.')));
      S.passkeys.forEach(function (p) {
        var row = el('div', 'hk-tok');
        row.appendChild(el('strong', null, p.name));
        if (p.synced) row.appendChild(el('span', 'hk-tok-me', T('synchronisiert')));
        row.appendChild(el('span', 'hk-hint', T('angelegt {date}', { date: when(p.created) }) + ' · ' +
          (p.last_used ? T('zuletzt benutzt {date}', { date: when(p.last_used) }) : T('noch nie benutzt'))));
        row.appendChild(button(T('Umbenennen'), '', function () {
          var name = prompt(T('Neuer Name für den Passkey:'), p.name);
          if (!name || !name.trim()) return;
          api('PATCH', '/api/account/passkeys/' + p.id, { name: name.trim() }).then(load).catch(function (e) { HK.toast(e.message); });
        }));
        row.appendChild(button(T('Löschen'), 'btn-danger', function () {
          HK.ask({ title: T('Passkey löschen?'), ok: T('Löschen'), danger: true,
                   text: last(T('„{name}“ kann sich danach nicht mehr bei Saga anmelden. Im Passwort-Manager des Geräts bleibt er stehen – dort kannst du ihn ebenfalls entfernen.', { name: p.name }),
                              S.passkeys.length === 1 && !S.totp) })
            .then(function (yes) {
              if (!yes) return;
              guarded(function () { return api('DELETE', '/api/account/passkeys/' + p.id); }).then(load).catch(function (e) { HK.toast(e.message); });
            });
        }));
        list.appendChild(row);
      });
      var st = el('div', 'hk-status');
      var prob = S.passkey_problem || (PK && PK.inApp() ? T('In der App-Ansicht lassen sich keine Passkeys anlegen – bitte Saga dafür im Browser öffnen. Anmelden mit Passkey geht danach auch in der App.') :
        !PK || !PK.supported() ? T('Dieser Browser kann keine Passkeys verwenden.') : originMsg(S));
      if (prob) { box.appendChild(el('p', 'hk-status err', prob)); return; }
      var bar = el('div', 'hk-actions'), nm = el('input', 'hk-in'); nm.type = 'text'; nm.maxLength = 60;
      nm.placeholder = T('Name, z. B. „iPhone“ oder „Laptop“'); nm.setAttribute('aria-label', T('Name des Passkeys'));
      var add = button(T('+ Passkey hinzufügen'), S.passkeys.length ? '' : 'btn-primary', function () {
        add.disabled = true; status(st, T('Warte auf den Passkey …'));
        guarded(function () { return api('POST', '/api/account/passkeys/options'); })
          .then(function (d) { return PK.create(d.options); })
          .then(function (cred) { return guarded(function () { return api('POST', '/api/account/passkeys', { credential: cred, name: nm.value.trim() }); }); })
          .then(function (d) { HK.toast(T('Passkey gespeichert')); return load().then(function () { showCodes(d.recovery); }); })
          .catch(function (e) {
            add.disabled = false;
            status(st, PK.cancelled(e) ? T('Abgebrochen – es wurde kein Passkey angelegt.') :
              (e && e.name === 'InvalidStateError' ? T('Dieses Gerät hat schon einen Passkey für dein Konto.') : e.message), 'err');
          });
      });
      bar.appendChild(nm); bar.appendChild(add); box.appendChild(bar); box.appendChild(st);
    }

    function totp() {
      var box = el('div', 'mfa-block'); body.appendChild(box);
      box.appendChild(el('h5', null, T('Authenticator-App')));
      if (S.totp) {
        var row = el('div', 'hk-tok');
        row.appendChild(el('strong', null, T('✓ Eingerichtet')));
        if (S.totp_since) row.appendChild(el('span', 'hk-hint', T('seit {date}', { date: when(S.totp_since) })));
        row.appendChild(button(T('Entfernen'), 'btn-danger', function () {
          HK.ask({ title: T('Authenticator-App entfernen?'), ok: T('Entfernen'), danger: true,
                   text: last(T('Saga fragt danach keinen Code aus der App mehr ab.'), !S.passkeys.length) }).then(function (yes) {
            if (!yes) return;
            guarded(function () { return api('DELETE', '/api/account/totp'); }).then(load).catch(function (e) { HK.toast(e.message); });
          });
        }));
        box.appendChild(row);
        return;
      }
      box.appendChild(el('p', 'hk-hint', T('Apps wie Aegis, 2FAS, Google Authenticator oder Microsoft Authenticator zeigen alle 30 Sekunden einen neuen 6-stelligen Code. Saga fragt ihn nach dem Passwort ab.')));
      var area = el('div'), st = el('div', 'hk-status'), bar = el('div', 'hk-actions');
      var go = button(T('Einrichten'), '', function () {
        go.disabled = true; status(st, '');
        guarded(function () { return api('POST', '/api/account/totp/setup'); }).then(function (d) { bar.hidden = true; setup(d); })
          .catch(function (e) { go.disabled = false; status(st, e.message, 'err'); });
      });
      bar.appendChild(go); box.appendChild(bar); box.appendChild(area); box.appendChild(st);
      function setup(d) {
        area.textContent = '';
        var qr = el('div', 'hk-qr mfa-qr'), img = el('img'); img.src = d.qr; img.width = 200; img.height = 200;
        img.alt = T('QR-Code für die Authenticator-App');
        var txt = el('div');
        txt.appendChild(el('p', null, T('1. QR-Code mit der Authenticator-App scannen.')));
        txt.appendChild(el('p', 'hk-hint', T('Kein Scannen möglich? Diesen Schlüssel von Hand eingeben (zeitbasiert, 6 Ziffern, 30 Sekunden):')));
        var key = el('code', 'mfa-key', d.secret); txt.appendChild(key);
        var link = el('a', 'mfa-link', T('Auf diesem Gerät öffnen')); link.href = d.uri; txt.appendChild(el('br')); txt.appendChild(link);
        qr.appendChild(img); qr.appendChild(txt); area.appendChild(qr);
        var code = el('input', 'hk-in mfa-code'); code.inputMode = 'numeric'; code.autocomplete = 'one-time-code'; code.maxLength = 7; code.placeholder = '123456';
        area.appendChild(field(T('2. Angezeigten Code eingeben'), code));
        var b2 = el('div', 'hk-actions'), ok = button(T('Bestätigen'), 'btn-primary'), cancel = button(T('Abbrechen'));
        b2.appendChild(ok); b2.appendChild(cancel); area.appendChild(b2);
        function send() {
          ok.disabled = true; status(st, T('Wird geprüft …'));
          guarded(function () { return api('POST', '/api/account/totp/confirm', { code: code.value.replace(/\s+/g, '') }); })
            .then(function (r) { HK.toast(T('Authenticator-App eingerichtet')); return load().then(function () { showCodes(r.recovery); }); })
            .catch(function (e) { ok.disabled = false; status(st, e.message, 'err'); code.select(); });
        }
        ok.addEventListener('click', send);
        code.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });
        cancel.addEventListener('click', function () { area.textContent = ''; bar.hidden = false; go.disabled = false; status(st, ''); });
        code.focus();
      }
    }

    function recovery() {
      if (!S.enabled) return;
      var box = el('div', 'mfa-block'); body.appendChild(box);
      box.appendChild(el('h5', null, T('Wiederherstellungscodes')));
      var low = S.recovery_left <= 3;
      box.appendChild(el('p', low ? 'hk-status err' : 'hk-hint', T('Noch {n} von {total} Codes übrig.', { n: S.recovery_left, total: S.recovery_total }) + ' ' +
        T('Damit kommst du in dein Konto, wenn Passkey oder Handy fehlen.')));
      var bar = el('div', 'hk-actions'), st = el('div', 'hk-status');
      bar.appendChild(button(T('Neue Codes erzeugen'), low ? 'btn-primary' : '', function () {
        HK.ask({ title: T('Neue Wiederherstellungscodes?'), ok: T('Neu erzeugen'), text: T('Die bisherigen Codes gelten danach nicht mehr.') }).then(function (yes) {
          if (!yes) return;
          guarded(function () { return api('POST', '/api/account/recovery'); })
            .then(function (d) { return load().then(function () { showCodes(d.recovery); }); })
            .catch(function (e) { status(st, e.message, 'err'); });
        });
      }));
      box.appendChild(bar); box.appendChild(st);
    }

    load();
    return { reload: load };
  }

  window.HKSecurity = { section: section };

  // Eigene Seite /security (Einrichtung nach der Anmeldung, wenn der Admin einen zweiten Faktor verlangt)
  var page = document.getElementById('security-page');
  if (page) {
    var next = document.getElementById('sec-next'), need = page.dataset.need === '1';
    section(document.getElementById('security-host'), { noTitle: true, onChange: function (s) {
      if (!next) return;
      var blocked = need && !s.enabled;
      next.classList.toggle('disabled', blocked); next.setAttribute('aria-disabled', blocked ? 'true' : 'false');
      next.textContent = blocked ? T('Erst einen zweiten Faktor einrichten') : T('Weiter');
    } });
    if (next) next.addEventListener('click', function (e) { if (next.getAttribute('aria-disabled') === 'true') e.preventDefault(); });
  }
})();
