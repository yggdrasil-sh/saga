/* Einstellungen -> "API" (2.5, Paket 1): API-Tokens (Bereiche, Seitenbaeume, Laufzeit), Webhooks und Hinweise zum
   MCP-Server. Server: saga/apitok.py, saga/webhooks.py, saga/mcp.py. Neue Tokens/Webhooks verlangen eine frische
   Bestaetigung (Passwort, Code oder Passkey) - der Klartext erscheint genau einmal. */
(function () {
  'use strict';
  var el = HK.el, api = HK.api;
  var fDate = new Intl.DateTimeFormat(window.SAGA_LOCALE, { day: '2-digit', month: '2-digit', year: 'numeric' });
  function when(t) { return fDate.format(new Date(t * 1000)); }
  function button(txt, cls, fn) { var b = el('button', 'btn' + (cls ? ' ' + cls : ''), txt); b.type = 'button'; if (fn) b.addEventListener('click', fn); return b; }
  function status(box, txt, cls) { box.textContent = txt || ''; box.className = 'hk-status' + (cls ? ' ' + cls : ''); }
  var SCOPE_TXT = {
    'read:pages': T('Seiten lesen'), 'write:pages': T('Seiten schreiben'), 'read:db': T('Datenbanken lesen'), 'write:db': T('Datenbanken schreiben'),
    'read:tasks': T('Aufgaben lesen'), 'write:tasks': T('Aufgaben schreiben'), 'read:files': T('Dateien lesen'), 'write:files': T('Dateien hochladen'),
    'read:comments': T('Kommentare lesen'), 'write:comments': T('Kommentare schreiben'), 'webhooks': T('Webhooks verwalten'), 'mcp': T('MCP-Server (KI-Werkzeuge)')
  };
  function copyRow(value, label) {
    var row = el('div', 'hk-actions'), inp = el('input', 'hk-in hk-tokval'); inp.type = 'text'; inp.readOnly = true; inp.value = value;
    inp.setAttribute('aria-label', label);
    row.appendChild(inp);
    row.appendChild(button(T('Kopieren'), '', function () {
      inp.select();
      (navigator.clipboard ? navigator.clipboard.writeText(value) : Promise.reject()).then(function () { HK.toast(T('Kopiert')); },
        function () { try { document.execCommand('copy'); HK.toast(T('Kopiert')); } catch (e) { /* markiert */ } });
    }));
    return row;
  }

  function section(body) {
    var sec = el('section', 'hk-set hk-set-api'); body.appendChild(sec);
    var wrap = el('div'); wrap.id = 'hk-api-tokens'; sec.appendChild(wrap);
    wrap.appendChild(el('h3', null, T('API & Integrationen')));
    wrap.appendChild(el('p', 'hk-hint', T('Mit einem API-Token greifen Skripte, Automationen und KI-Werkzeuge auf deine Seiten zu – nur mit den Bereichen, die du auswählst, und nie mit mehr Rechten als dein Konto. Der Token wird nur einmal angezeigt.')));
    var confirmBox = el('div', 'mfa-confirm'); confirmBox.hidden = true; wrap.appendChild(confirmBox);
    var off = el('p', 'hk-hint'); off.hidden = true; wrap.appendChild(off);
    var list = el('div', 'hk-tok-list api-list'); wrap.appendChild(list);
    var out = el('div'); wrap.appendChild(out);
    var form = el('div', 'api-form'); wrap.appendChild(form);
    var hooks = el('div', 'api-hooks'); wrap.appendChild(hooks);
    var mcpBox = el('details', 'hk-more api-mcp'); wrap.appendChild(mcpBox);
    var S = null;

    function confirmIdentity() {
      return new Promise(function (resolve, reject) {
        confirmBox.textContent = ''; confirmBox.hidden = false;
        confirmBox.appendChild(el('p', null, T('Zur Sicherheit bitte bestätigen, dass du es bist.')));
        if (S && S.sso) {
          confirmBox.appendChild(el('p', 'hk-hint', T('Dein Konto meldet sich über SSO an: bitte ab- und wieder anmelden, dann innerhalb von 5 Minuten erneut versuchen.')));
          confirmBox.appendChild(button(T('Abbrechen'), '', function () { confirmBox.hidden = true; reject(new Error(T('Abgebrochen.'))); }));
          return;
        }
        var pw = el('input', 'hk-in'); pw.type = 'password'; pw.autocomplete = 'current-password'; pw.maxLength = 200;
        var lab = el('label', 'hk-field'); lab.appendChild(el('span', null, T('Passwort oder Code aus der Authenticator-App'))); lab.appendChild(pw);
        confirmBox.appendChild(lab);
        var st = el('div', 'hk-status'), bar = el('div', 'hk-actions');
        var okb = button(T('Bestätigen'), 'btn-primary');
        bar.appendChild(okb);
        bar.appendChild(button(T('Abbrechen'), '', function () { confirmBox.hidden = true; confirmBox.textContent = ''; reject(new Error(T('Abgebrochen.'))); }));
        confirmBox.appendChild(bar); confirmBox.appendChild(st);
        function send() {
          var v = pw.value.trim(); if (!v) { pw.focus(); return; }
          okb.disabled = true; status(st, T('Wird geprüft …'));
          api('POST', '/api/account/security/confirm', /^\d{6}$/.test(v) ? { code: v } : { password: pw.value })
            .then(function () { confirmBox.hidden = true; confirmBox.textContent = ''; resolve(); })
            .catch(function (e) { okb.disabled = false; status(st, e.message, 'err'); pw.select(); });
        }
        okb.addEventListener('click', send);
        pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); send(); } });
        pw.focus();
      });
    }
    function guarded(fn) {
      return fn().catch(function (e) {
        if (e.status === 403 && e.data && e.data.reauth) return confirmIdentity().then(fn);
        throw e;
      });
    }

    function load() {
      return api('GET', '/api/account/api-tokens').then(function (d) { S = d; render(); loadHooks(); })
        .catch(function (e) { list.textContent = T('Nicht geladen: {err}', { err: e.message }); });
    }

    function render() {
      list.textContent = ''; form.textContent = '';
      off.hidden = S.enabled;
      off.textContent = T('Die API ist auf diesem Server für dein Konto nicht freigegeben.');
      if (!S.tokens.length) list.appendChild(el('p', 'hk-hint', T('Noch keine API-Tokens.')));
      S.tokens.forEach(function (k) {
        var row = el('div', 'hk-tok api-tok');
        row.appendChild(el('strong', null, k.name));
        row.appendChild(el('code', null, k.prefix + '…'));
        var info = k.scopes.map(function (s) { return SCOPE_TXT[s] || s; }).join(', ');
        if (k.roots) info += ' · ' + T('nur: {list}', { list: k.roots.map(function (r) { return r.title; }).join(', ') });
        var state = k.revoked ? T('widerrufen') : k.expired ? T('abgelaufen') : (k.expires ? T('gültig bis {date}', { date: when(k.expires) }) : T('ohne Ablauf'));
        row.appendChild(el('span', 'hk-hint', info + ' · ' + state + ' · ' + (k.last_used ? T('zuletzt benutzt {date}', { date: when(k.last_used) }) : T('noch nie benutzt'))));
        if (!k.revoked) {
          row.appendChild(button(T('Widerrufen'), 'btn-danger', function () {
            if (!confirm(T('API-Token „{name}“ widerrufen? Programme, die ihn nutzen, verlieren sofort den Zugriff.', { name: k.name }))) return;
            api('DELETE', '/api/account/api-tokens/' + k.id).then(load).catch(function (e) { HK.toast(e.message); });
          }));
        }
        list.appendChild(row);
      });
      if (!S.enabled) return;
      // Neuer Token
      form.appendChild(el('h4', null, T('Neuer API-Token')));
      var nm = el('input', 'hk-in api-name'); nm.type = 'text'; nm.maxLength = 60; nm.placeholder = T('Name, z. B. „n8n“ oder „Claude“');
      var lab = el('label', 'hk-field'); lab.appendChild(el('span', null, T('Name'))); lab.appendChild(nm); form.appendChild(lab);
      var box = el('fieldset', 'api-scopes'); box.appendChild(el('legend', null, T('Bereiche')));
      S.scopes.forEach(function (s) {
        if ((s === 'webhooks' && !S.webhooks) || (s === 'mcp' && !S.mcp)) return;
        var l = el('label', 'hk-check'), cb = el('input'); cb.type = 'checkbox'; cb.value = s;
        l.appendChild(cb); l.appendChild(document.createTextNode(' ' + (SCOPE_TXT[s] || s))); box.appendChild(l);
      });
      form.appendChild(box);
      var days = el('select', 'hk-in api-days');
      [[30, T('30 Tage')], [90, T('90 Tage')], [180, T('180 Tage')], [365, T('1 Jahr')]].forEach(function (o) {
        if (S.max_days && o[0] > S.max_days) return;
        var x = el('option', null, o[1]); x.value = o[0]; if (o[0] === S.default_days) x.selected = true; days.appendChild(x);
      });
      if (S.unlimited) { var x = el('option', null, T('ohne Ablauf')); x.value = '0'; days.appendChild(x); }
      var dl = el('label', 'hk-field'); dl.appendChild(el('span', null, T('Gültig'))); dl.appendChild(days); form.appendChild(dl);
      var roots = el('input', 'hk-in api-roots'); roots.type = 'text'; roots.placeholder = T('leer = alle Seiten, sonst Seiten-IDs, z. B. 12, 40');
      var rl = el('label', 'hk-field'); rl.appendChild(el('span', null, T('Nur diese Seitenbäume'))); rl.appendChild(roots); form.appendChild(rl);
      form.appendChild(el('p', 'hk-hint', T('Tipp für KI-Werkzeuge: nur die nötigen Seitenbäume und möglichst nur Lese-Bereiche freigeben – Inhalte geteilter Seiten können Anweisungen an das KI-Modell enthalten.')));
      var st = el('div', 'hk-status'), bar = el('div', 'hk-actions');
      var go = button(T('Token erzeugen'), 'btn-primary api-create'); bar.appendChild(go); form.appendChild(bar); form.appendChild(st);
      go.addEventListener('click', function () {
        var scopes = [].map.call(box.querySelectorAll('input:checked'), function (c) { return c.value; });
        var r = roots.value.split(/[\s,;]+/).filter(Boolean).map(function (x) { var m = /(\d+)\s*$/.exec(x); return m ? +m[1] : NaN; });
        if (r.some(isNaN)) { status(st, T('Seitenbäume: nur Seiten-IDs (Zahlen) angeben.'), 'err'); return; }
        var b = { name: nm.value.trim(), scopes: scopes, days: +days.value, roots: r.length ? r : null };
        go.disabled = true; status(st, '');
        guarded(function () { return api('POST', '/api/account/api-tokens', b); }).then(function (d) {
          out.textContent = '';
          var nb = el('div', 'hk-newtok api-newtok');
          nb.appendChild(el('p', null, '🔑 ' + T('Neuer API-Token „{name}“ – nur jetzt sichtbar!', { name: d.name })));
          nb.appendChild(copyRow(d.token, T('API-Token')));
          nb.appendChild(el('p', 'hk-hint', T('Verwendung: Kopfzeile „Authorization: Bearer <Token>“. Anleitung: docs/API.md.')));
          out.appendChild(nb);
          load();
        }).catch(function (e) { status(st, e.message, 'err'); }).then(function () { go.disabled = false; });
      });
      // MCP
      mcpBox.textContent = '';
      if (S.mcp) {
        mcpBox.hidden = false;
        mcpBox.appendChild(el('summary', null, T('MCP-Server für KI-Werkzeuge')));
        mcpBox.appendChild(el('p', 'hk-hint', T('Adresse für Claude und andere MCP-Clients (Streamable HTTP). Token mit Bereich „MCP-Server“ und den gewünschten Datenbereichen anlegen.')));
        mcpBox.appendChild(copyRow(location.origin + '/mcp', 'MCP'));
        var pre = el('pre', 'api-snippet');
        pre.textContent = JSON.stringify({ mcpServers: { saga: { type: 'http', url: location.origin + '/mcp', headers: { Authorization: 'Bearer sga_…' } } } }, null, 2);
        mcpBox.appendChild(pre);
      } else mcpBox.hidden = true;
    }

    // --- Webhooks ---------------------------------------------------------------------------------------------
    function loadHooks() {
      hooks.textContent = '';
      if (!S.webhooks || !S.enabled) return;
      api('GET', '/api/account/webhooks').then(function (d) {
        hooks.appendChild(el('h4', null, T('Webhooks')));
        hooks.appendChild(el('p', 'hk-hint', T('Saga meldet Änderungen per HTTP-POST an deine Adresse (signiert mit HMAC-SHA256). Die Meldung enthält nur IDs, Titel und wer es war – Inhalte holt der Empfänger per API.')));
        if (!d.hooks.length) hooks.appendChild(el('p', 'hk-hint', T('Noch keine Webhooks.')));
        d.hooks.forEach(function (h) {
          var row = el('div', 'hk-tok api-hook');
          row.appendChild(el('strong', null, h.name || h.url));
          row.appendChild(el('code', null, h.url));
          var last = h.deliveries && h.deliveries[0];
          row.appendChild(el('span', 'hk-hint', (h.active ? T('aktiv') : T('aus')) + ' · ' + h.events.length + ' ' + T('Ereignisse') +
            (last ? ' · ' + T('zuletzt: {s}', { s: last.status + (last.code ? ' ' + last.code : '') }) : '') + (h.last_error ? ' · ' + h.last_error : '')));
          row.appendChild(button(T('Testen'), '', function () { api('POST', '/api/account/webhooks/' + h.id + '/test').then(function () { HK.toast(T('Test gesendet')); setTimeout(loadHooks, 2000); }); }));
          row.appendChild(button(h.active ? T('Ausschalten') : T('Einschalten'), '', function () {
            api('PATCH', '/api/account/webhooks/' + h.id, { active: !h.active }).then(loadHooks).catch(function (e) { HK.toast(e.message); });
          }));
          row.appendChild(button(T('Löschen'), 'btn-danger', function () {
            if (!confirm(T('Webhook „{name}“ löschen?', { name: h.name || h.url }))) return;
            api('DELETE', '/api/account/webhooks/' + h.id).then(loadHooks);
          }));
          hooks.appendChild(row);
        });
        var url = el('input', 'hk-in api-hook-url'); url.type = 'url'; url.maxLength = 500; url.placeholder = 'https://example.org/saga-hook';
        var ul = el('label', 'hk-field'); ul.appendChild(el('span', null, T('Adresse'))); ul.appendChild(url); hooks.appendChild(ul);
        var ev = el('fieldset', 'api-events'); ev.appendChild(el('legend', null, T('Ereignisse')));
        d.events.forEach(function (e) {
          var l = el('label', 'hk-check'), cb = el('input'); cb.type = 'checkbox'; cb.value = e; cb.checked = true;
          l.appendChild(cb); l.appendChild(document.createTextNode(' ' + e)); ev.appendChild(l);
        });
        hooks.appendChild(ev);
        var st = el('div', 'hk-status'), bar = el('div', 'hk-actions'), add = button(T('Webhook anlegen'), 'btn-primary');
        bar.appendChild(add); hooks.appendChild(bar); hooks.appendChild(st);
        add.addEventListener('click', function () {
          var b = { url: url.value.trim(), events: [].map.call(ev.querySelectorAll('input:checked'), function (c) { return c.value; }) };
          add.disabled = true; status(st, '');
          guarded(function () { return api('POST', '/api/account/webhooks', b); }).then(function (r) {
            out.textContent = '';
            var nb = el('div', 'hk-newtok');
            nb.appendChild(el('p', null, T('Geheimnis für die Signatur – nur jetzt sichtbar!')));
            nb.appendChild(copyRow(r.secret, T('Webhook-Geheimnis')));
            out.appendChild(nb);
            loadHooks();
          }).catch(function (e) { status(st, e.message, 'err'); }).then(function () { add.disabled = false; });
        });
      }).catch(function () { /* ohne Webhooks */ });
    }

    load();
  }

  window.HKApi = { section: section };
})();
