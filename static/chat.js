// Saga - Chats mit Ende-zu-Ende-Verschluesselung (1.5): Liste, Unterhaltung, Einrichtung, neues Geraet, Chats & Geraete.
// Kryptografie nur ueber static/chat-crypto.js (libsodium.js). Private Schluessel liegen nur in IndexedDB dieses Browsers;
// der Server bekommt Ciphertext, versiegelte Inhaltsschluessel und das mit dem Wiederherstellungsschluessel
// verschluesselte Buendel. Alle Inhalte per textContent (kein innerHTML mit Nachrichtentext). Live ueber live.js
// (Ereignis 'hk-live' am document), sonst Abfrage alle 30 s.
(function () {
  'use strict';
  var HK = window.HK, root = document.getElementById('ct');
  if (!HK || !root) return;
  var $ = HK.$, el = HK.el, api = HK.api, T = window.T;
  var ME = document.body.dataset.uid, MYNAME = document.body.dataset.name || ME;
  var X = null;                                        // SagaChatCrypto, nach sodium.ready
  var K = null;                                        // lokale Schluessel {device:{...}, ident_sk, ident_pk, backup_sk, backup_pk}
  var S = { me: null, chats: [], cur: null, msgs: [], byId: {}, more: false, reply: null, files: [], rc: null, notes: {},
            typing: {}, mentions: {}, searchQ: '', loading: false, linkWait: null, ready: false };
  var RET = [[0, T('Aus')], [3600, T('1 Stunde')], [86400, T('1 Tag')], [604800, T('7 Tage')], [2592000, T('30 Tage')],
             [7776000, T('90 Tage')], [31536000, T('1 Jahr')]];
  var fTime = new Intl.DateTimeFormat(HK.locale, { hour: '2-digit', minute: '2-digit' });
  var fDay = new Intl.DateTimeFormat(HK.locale, { weekday: 'short', day: '2-digit', month: '2-digit', year: 'numeric' });

  // --- IndexedDB (Schluessel, gemerkte Identitaetsschluessel der Gegenueber) ------------------------------------
  var dbP = null;
  function idb() {
    if (!dbP) dbP = new Promise(function (res, rej) {
      var r = indexedDB.open('saga-chat', 1);
      r.onupgradeneeded = function () { r.result.createObjectStore('kv'); };
      r.onsuccess = function () { res(r.result); };
      r.onerror = function () { rej(r.error); };
    });
    return dbP;
  }
  function kv(mode, fn) {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction('kv', mode), st = tx.objectStore('kv'), out = fn(st);
        tx.oncomplete = function () { res(out && 'result' in out ? out.result : undefined); };
        tx.onerror = function () { rej(tx.error); };
      });
    });
  }
  function kvGet(k) { return kv('readonly', function (st) { return st.get(k); }); }
  function kvSet(k, v) { return kv('readwrite', function (st) { st.put(v, k); }); }
  function kvDel(k) { return kv('readwrite', function (st) { st.delete(k); }); }
  var KEYS = 'keys:' + ME;
  function trustKey(u) { return 'trust:' + ME + ':' + u; }

  function deviceName() {
    var ua = navigator.userAgent, b = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : T('Browser');
    var os = /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
    return b + (os ? ' · ' + os : '');
  }
  function b64(x) { return X.b64(x); }
  function ub(s, n) { return X.unb64(s, n); }
  function eqB(a, b) { if (!a || !b || a.length !== b.length) return false; for (var i = 0; i < a.length; i++) if (a[i] !== b[i]) return false; return true; }
  function q(s) { return encodeURIComponent(s); }
  function dev() { return K ? K.device.id : ''; }
  function hue(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function avatar(text, key, cls) {
    var a = el('span', 'ct-av' + (cls ? ' ' + cls : ''), text);
    a.style.setProperty('--ct-h', String(hue(key))); a.setAttribute('aria-hidden', 'true');
    return a;
  }

  // --- Overlay fuer Einrichtung / neues Geraet / Dialoge --------------------------------------------------------
  var ov = null;
  function overlay(title, opts) {
    closeOverlay();
    opts = opts || {};
    ov = el('div', 'hk-modal ct-ov'); ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-modal', 'true'); ov.setAttribute('aria-label', title);
    var card = el('div', 'hk-dialog ct-dlg' + (opts.wide ? ' ct-dlg-wide' : '')), head = el('div', 'hk-dlg-head');
    head.appendChild(el('h2', null, title));
    if (opts.close !== false) {
      var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen'));
      x.addEventListener('click', closeOverlay); head.appendChild(x);
      ov.addEventListener('mousedown', function (e) { if (e.target === ov) closeOverlay(); });
    }
    var body = el('div', 'hk-dlg-body'); card.appendChild(head); card.appendChild(body); ov.appendChild(card);
    document.body.appendChild(ov);
    return body;
  }
  function closeOverlay() { if (ov) { ov.remove(); ov = null; } }
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && ov && ov.querySelector('.icon-btn') && !HK.menuOpen()) closeOverlay();
  });
  function btn(label, cls, fn) { var b = el('button', 'btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; if (fn) b.addEventListener('click', fn); return b; }
  function status(box) { var s = el('p', 'hk-status'); s.setAttribute('role', 'status'); box.appendChild(s); return s; }
  function busy(b, on, label) { b.disabled = on; if (label) b.textContent = label; }

  // --- Start --------------------------------------------------------------------------------------------------
  function start() {
    if (!window.sodium || !window.SagaChatCrypto) { fatal(T('Die Verschlüsselungs-Bibliothek konnte nicht geladen werden.')); return; }
    window.sodium.ready.then(function () {
      X = window.SagaChatCrypto(window.sodium);
      return kvGet(KEYS).catch(function () { throw new Error('idb'); });
    }).then(function (keys) {
      K = keys || null;
      return api('GET', '/api/chat/me?device=' + q(K ? K.device.id : ''));
    }).then(function (me) {
      S.me = me;
      if (!me.identity) { if (K) return forget().then(setupIntro); return setupIntro(); }
      var mine = K && (me.devices || []).filter(function (d) { return d.id === K.device.id && !d.revoked; })[0];
      if (!K || !mine || !eqB(K.ident_pk, ub(me.identity.ident_pub, 32))) return forget().then(newDevice);
      ready();
    }).catch(function (e) {
      if (e && e.message === 'idb') fatal(T('Dieser Browser erlaubt keinen lokalen Speicher (IndexedDB) – im privaten Fenster gehen Chats nicht.'));
      else fatal(T('Chats nicht geladen: {err}', { err: e.message }));
    });
  }
  function fatal(msg) {
    root.textContent = ''; var p = el('p', 'ct-off', msg); p.setAttribute('role', 'alert'); root.appendChild(p);
  }
  function forget() { K = null; S.ready = false; return kvDel(KEYS).catch(function () {}); }
  function saveKeys(k) { K = k; return kvSet(KEYS, k); }

  // --- Einrichtung (erstes Geraet) -------------------------------------------------------------------------------
  function setupIntro(reset) {
    var box = overlay(reset ? T('Chats neu beginnen') : T('Chats einrichten'), { close: !!reset });
    box.appendChild(el('p', null, T('Saga-Chats sind Ende-zu-Ende-verschlüsselt: Nachrichten und Anhänge werden auf deinen Geräten ver- und entschlüsselt. Der Server – auch der Admin – sieht nur verschlüsselte Daten.')));
    box.appendChild(el('p', null, T('Dafür bekommst du gleich einen Wiederherstellungsschlüssel. Damit holst du deine Chats auf ein neues Gerät, wenn kein anderes mehr da ist. Ohne ihn sind deine Nachrichten verloren – auch der Admin kann sie nicht wiederherstellen.')));
    if (reset) box.appendChild(el('p', 'ct-danger', T('Achtung: Neu beginnen erzeugt neue Schlüssel. Bisherige Nachrichten sind dann für dich nicht mehr lesbar, deine Gesprächspartner sehen eine geänderte Sicherheitsnummer.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Schlüssel erzeugen'), 'btn-primary ct-setup-go', function () { setupKey(!!reset); }));
  }
  function setupKey(reset) {
    var id = X.newIdentity(), d = X.newDevice(), rk = X.recoveryKey();
    var box = overlay(T('Dein Wiederherstellungsschlüssel'), { close: false });
    recoveryShow(box, rk);
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Weiter'), 'btn-primary', function () { setupConfirm(reset, id, d, rk); }));
  }
  function recoveryShow(box, rk) {
    box.appendChild(el('p', null, T('Schreibe ihn ab oder speichere ihn an einem sicheren Ort (z. B. im Passwort-Manager). Saga zeigt ihn nur jetzt.')));
    var code = el('div', 'ct-rk', rk); code.id = 'ct-rk'; code.setAttribute('aria-label', T('Wiederherstellungsschlüssel'));
    box.appendChild(code);
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Kopieren'), '', function () {
      (navigator.clipboard ? navigator.clipboard.writeText(rk) : Promise.reject()).then(function () { HK.toast(T('Kopiert.')); }, function () { HK.toast(T('Kopieren nicht möglich – bitte abschreiben.')); });
    }));
    bar.appendChild(btn(T('Als Datei speichern'), '', function () {
      var txt = T('Saga – Wiederherstellungsschlüssel für Chats') + '\n' + T('Konto: {name}', { name: ME }) + '\n\n' + rk + '\n';
      var a = el('a'); a.href = URL.createObjectURL(new Blob([txt], { type: 'text/plain' })); a.download = 'saga-chat-recovery-' + ME + '.txt';
      document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }));
  }
  function setupConfirm(reset, id, d, rk) {
    var last = rk.slice(-4);
    var box = overlay(T('Wiederherstellungsschlüssel bestätigen'), { close: false });
    box.appendChild(el('p', null, T('Gib zur Bestätigung die letzten vier Zeichen deines Wiederherstellungsschlüssels ein.')));
    var inp = el('input', 'hk-in ct-rk-last'); inp.maxLength = 4; inp.autocomplete = 'off'; inp.spellcheck = false; inp.setAttribute('aria-label', T('Letzte vier Zeichen'));
    box.appendChild(inp);
    var st = status(box), bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Zurück'), '', function () { var b = overlay(T('Dein Wiederherstellungsschlüssel'), { close: false }); recoveryShow(b, rk);
      var bb = el('div', 'hk-actions'); b.appendChild(bb); bb.appendChild(btn(T('Weiter'), 'btn-primary', function () { setupConfirm(reset, id, d, rk); })); }));
    var go = btn(T('Einrichten'), 'btn-primary ct-setup-done'); bar.appendChild(go);
    function run() {
      var n = X.normRecovery(new Array(29).join('A') + inp.value);
      if (!n || n.slice(-4) !== last) {
        st.className = 'hk-status err'; st.textContent = T('Das passt nicht – bitte genau die letzten vier Zeichen.'); return;
      }
      busy(go, true, T('Schlüssel werden gesichert …')); st.className = 'hk-status'; st.textContent = '';
      setTimeout(function () {                              // Argon2id dauert einen Moment - erst den Text zeigen
        var bundle;
        try { bundle = X.sealBundle(ME, rk, id.ident_sk, id.backup_sk); } catch (e) { st.className = 'hk-status err'; st.textContent = e.message; busy(go, false, T('Einrichten')); return; }
        var name = deviceName();
        api('PUT', '/api/chat/identity', {
          ident_pub: b64(id.ident_pk), backup_pub: b64(id.backup_pk), backup_sig: b64(X.backupSig(ME, id.ident_sk, id.backup_pk)), bundle: bundle, reset: !!reset,
          device: { id: d.id, name: name, enc_pub: b64(d.enc_pk), sign_pub: b64(d.sign_pk), sig: b64(X.deviceSig(ME, id.ident_sk, d)) }
        }).then(function (r) {
          S.me.identity = r.identity;
          d.name = name;
          return saveKeys({ v: 1, device: d, ident_sk: id.ident_sk, ident_pk: id.ident_pk, backup_sk: id.backup_sk, backup_pk: id.backup_pk });
        }).then(function () { closeOverlay(); HK.toast(T('Chats sind eingerichtet.')); ready(); })
          .catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; busy(go, false, T('Einrichten')); });
      }, 30);
    }
    go.addEventListener('click', run);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    inp.focus();
  }

  // --- Neues Geraet: mit anderem Geraet koppeln oder Wiederherstellungsschluessel --------------------------------
  function newDevice() {
    var box = overlay(T('Chats auf diesem Gerät'), { close: false });
    box.appendChild(el('p', null, T('Deine Chats sind Ende-zu-Ende-verschlüsselt. Damit dieses Gerät sie lesen kann, braucht es deine Schlüssel – wähle einen Weg:')));
    var a = el('div', 'ct-choice');
    var b1 = btn(T('Mit anderem Gerät bestätigen'), 'btn-primary ct-link-go', linkStart);
    a.appendChild(b1); a.appendChild(el('p', 'hk-hint', T('Auf einem Gerät, auf dem deine Chats schon laufen, einen Code bestätigen.')));
    var b2 = btn(T('Wiederherstellungsschlüssel eingeben'), 'ct-rk-go', recoverForm);
    a.appendChild(b2); a.appendChild(el('p', 'hk-hint', T('Der Schlüssel, den du bei der Einrichtung bekommen hast.')));
    box.appendChild(a);
    var lost = el('button', 'linkbtn danger ct-reset', T('Beides nicht mehr vorhanden? Neu beginnen …')); lost.type = 'button';
    lost.addEventListener('click', function () {
      HK.ask({ title: T('Chats neu beginnen?'), text: T('Neue Schlüssel: Bisherige Nachrichten sind dann für dich nicht mehr lesbar, deine anderen Geräte werden abgemeldet und deine Gesprächspartner sehen eine geänderte Sicherheitsnummer.'),
               ok: T('Neu beginnen'), danger: true }).then(function (y) { if (y) setupIntro(true); });
    });
    box.appendChild(lost);
  }
  function recoverForm() {
    var box = overlay(T('Wiederherstellungsschlüssel eingeben'), { close: false });
    var inp = el('input', 'hk-in ct-rk-in'); inp.autocomplete = 'off'; inp.spellcheck = false; inp.placeholder = 'XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX';
    inp.setAttribute('aria-label', T('Wiederherstellungsschlüssel'));
    box.appendChild(inp);
    var st = status(box), bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Zurück'), '', newDevice));
    var go = btn(T('Entsperren'), 'btn-primary ct-rk-ok'); bar.appendChild(go);
    function run() {
      if (!X.normRecovery(inp.value)) { st.className = 'hk-status err'; st.textContent = T('Ein Wiederherstellungsschlüssel hat 32 Zeichen (A–Z, 2–7).'); return; }
      busy(go, true, T('Prüfe …')); st.textContent = '';
      setTimeout(function () {
        var b;
        try { b = X.openBundle(ME, inp.value, S.me.identity.bundle); } catch (e) { b = null; }
        if (!b || !eqB(b.ident_pk, ub(S.me.identity.ident_pub, 32))) {
          st.className = 'hk-status err'; st.textContent = T('Dieser Schlüssel passt nicht.'); busy(go, false, T('Entsperren')); return;
        }
        var d = X.newDevice(); d.name = deviceName();
        api('POST', '/api/chat/devices', { id: d.id, name: d.name, enc_pub: b64(d.enc_pk), sign_pub: b64(d.sign_pk), sig: b64(X.deviceSig(ME, b.ident_sk, d)) })
          .then(function () { return saveKeys({ v: 1, device: d, ident_sk: b.ident_sk, ident_pk: b.ident_pk, backup_sk: b.backup_sk, backup_pk: b.backup_pk }); })
          .then(function () { closeOverlay(); HK.toast(T('Dieses Gerät ist jetzt für Chats eingerichtet.')); ready(); })
          .catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; busy(go, false, T('Entsperren')); });
      }, 30);
    }
    go.addEventListener('click', run);
    inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    inp.focus();
  }
  function linkStart() {
    var d = X.newDevice(); d.name = deviceName();
    var box = overlay(T('Mit anderem Gerät bestätigen'), { close: false });
    var st = status(box);
    st.textContent = T('Anfrage wird gesendet …');
    api('POST', '/api/chat/link', { id: d.id, name: d.name, enc_pub: b64(d.enc_pk), sign_pub: b64(d.sign_pk) }).then(function (r) {
      st.textContent = '';
      box.appendChild(el('p', null, T('Öffne auf einem Gerät, auf dem deine Chats schon laufen, die Seite „Chats“. Dort erscheint diese Anfrage – bestätige sie, wenn derselbe Code angezeigt wird:')));
      var code = el('div', 'ct-code', X.linkCode(ME, d.id, d.enc_pk, d.sign_pk)); code.id = 'ct-link-code'; box.appendChild(code);
      box.appendChild(el('p', 'hk-hint', T('Die Anfrage gilt 10 Minuten.')));
      var bar = el('div', 'hk-actions'); box.appendChild(bar);
      bar.appendChild(btn(T('Abbrechen'), '', function () { stopLink(); api('DELETE', '/api/chat/link/' + r.id).catch(function () {}); newDevice(); }));
      S.linkWait = { id: r.id, d: d, t: setInterval(linkPoll, 3000) };
    }).catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; box.appendChild(btn(T('Zurück'), '', newDevice)); });
  }
  function stopLink() { if (S.linkWait) { clearInterval(S.linkWait.t); S.linkWait = null; } }
  function linkPoll() {
    var w = S.linkWait; if (!w || w.busy) return;
    w.busy = true;
    api('GET', '/api/chat/link/' + w.id).then(function (r) {
      w.busy = false;
      if (r.state === 'pending') return;
      stopLink();
      if (r.state !== 'approved') { HK.toast(T('Die Anfrage ist abgelaufen oder wurde abgelehnt.')); newDevice(); return; }
      var b = X.parsePlain(X.sealOpen(ub(r.sealed), w.d.enc_pk, w.d.enc_sk));
      if (!eqB(b.ident_pk, ub(S.me.identity.ident_pub, 32))) throw new Error(T('Die übertragenen Schlüssel passen nicht zu deinem Konto.'));
      return saveKeys({ v: 1, device: w.d, ident_sk: b.ident_sk, ident_pk: b.ident_pk, backup_sk: b.backup_sk, backup_pk: b.backup_pk })
        .then(function () { closeOverlay(); HK.toast(T('Dieses Gerät ist jetzt für Chats eingerichtet.')); ready(); });
    }).catch(function (e) { if (w) w.busy = false; stopLink(); HK.toast(e.message); newDevice(); });
  }
  // Auf eingerichteten Geraeten: offene Kopplungsanfragen anzeigen
  var linkShown = {};
  function checkLinks() {
    if (!S.ready) return;
    api('GET', '/api/chat/link').then(function (r) {
      var l = (r.links || []).filter(function (x) { return !linkShown[x.id]; })[0];
      if (!l || ov) return;
      linkShown[l.id] = true;
      var box = overlay(T('Neues Gerät bestätigen'));
      box.appendChild(HK.fmtNodes(T('„{name}“ möchte auf deine Chats zugreifen.'), { name: el('b', null, l.name || T('Unbekanntes Gerät')) }));
      box.appendChild(el('p', null, T('Bestätige nur, wenn auf dem neuen Gerät genau dieser Code steht:')));
      var code = el('div', 'ct-code', X.linkCode(ME, l.device_id, ub(l.enc_pub, 32), ub(l.sign_pub, 32))); code.id = 'ct-approve-code'; box.appendChild(code);
      var st = status(box), bar = el('div', 'hk-actions'); box.appendChild(bar);
      bar.appendChild(btn(T('Ablehnen'), 'btn-danger', function () { api('DELETE', '/api/chat/link/' + l.id).catch(function () {}); closeOverlay(); }));
      bar.appendChild(btn(T('Code stimmt – bestätigen'), 'btn-primary ct-approve', function (e) {
        busy(e.target, true);
        var d = { id: l.device_id, enc_pk: ub(l.enc_pub, 32), sign_pk: ub(l.sign_pub, 32) };
        api('POST', '/api/chat/link/' + l.id, { sig: b64(X.deviceSig(ME, K.ident_sk, d)), sealed: b64(X.seal(X.bundlePlain(K.ident_sk, K.backup_sk), d.enc_pk)) })
          .then(function () { closeOverlay(); HK.toast(T('Gerät bestätigt.')); })
          .catch(function (err) { st.className = 'hk-status err'; st.textContent = err.message; busy(e.target, false); });
      }));
    }).catch(function () {});
  }

  // --- Chats & Geraete ------------------------------------------------------------------------------------------
  function settings() {
    var box = overlay(T('Chats & Geräte'), { wide: true });
    var list = el('ul', 'ct-devs'); box.appendChild(el('h3', null, T('Geräte mit Zugriff auf deine Chats'))); box.appendChild(list);
    api('GET', '/api/chat/me?device=' + q(dev())).then(function (me) {
      S.me = me;
      me.devices.filter(function (d) { return !d.revoked; }).forEach(function (d) {
        var li = el('li', 'ct-dev'), t = el('div', 'ct-dev-t');
        t.appendChild(el('b', null, d.name || T('Unbekanntes Gerät')));
        if (d.current) t.appendChild(el('span', 'ct-chip', T('dieses Gerät')));
        t.appendChild(el('span', 'hk-hint', T('angemeldet {when}', { when: fDay.format(new Date(d.created * 1000)) }) +
          (d.last_seen ? ' · ' + T('zuletzt {when}', { when: HK.relTime(d.last_seen - Date.now() / 1000) }) : '')));
        li.appendChild(t);
        li.appendChild(btn(d.current ? T('Von diesem Gerät entfernen') : T('Entfernen'), 'btn-danger small', function () {
          HK.ask({ title: T('Gerät entfernen?'), text: d.current ? T('Die Schlüssel werden aus diesem Browser gelöscht. Danach brauchst du ein anderes Gerät oder den Wiederherstellungsschlüssel.')
            : T('Das Gerät bekommt keine neuen Nachrichten mehr und kann vorhandene nicht mehr abrufen.'), ok: T('Entfernen'), danger: true }).then(function (y) {
            if (!y) return;
            api('DELETE', '/api/chat/devices/' + d.id).then(function () {
              if (d.current) return forget().then(function () { closeOverlay(); location.reload(); });
              settings();
            }).catch(function (e) { HK.toast(e.message); });
          });
        }));
        list.appendChild(li);
      });
    }).catch(function (e) { list.appendChild(el('li', 'hk-status err', e.message)); });
    box.appendChild(el('h3', null, T('Wiederherstellungsschlüssel')));
    box.appendChild(el('p', 'hk-hint', T('Verloren oder womöglich gesehen? Erzeuge einen neuen – der alte gilt dann nicht mehr. Deine Schlüssel und Nachrichten bleiben gleich.')));
    box.appendChild(btn(T('Neuen Wiederherstellungsschlüssel erzeugen'), 'ct-rk-new', function () {
      HK.ask({ title: T('Neuen Wiederherstellungsschlüssel erzeugen?'), text: T('Der bisherige Schlüssel funktioniert danach nicht mehr.'), ok: T('Erzeugen') }).then(function (y) {
        if (!y) return;
        var rk = X.recoveryKey(), b = overlay(T('Neuer Wiederherstellungsschlüssel'), { close: false });
        recoveryShow(b, rk);
        var st = status(b), bar = el('div', 'hk-actions'); b.appendChild(bar);
        bar.appendChild(btn(T('Abbrechen'), '', settings));
        bar.appendChild(btn(T('Übernehmen'), 'btn-primary', function (e) {
          busy(e.target, true, T('Schlüssel werden gesichert …'));
          setTimeout(function () {
            var bundle = X.sealBundle(ME, rk, K.ident_sk, K.backup_sk);
            api('PUT', '/api/chat/bundle', { bundle: bundle, sig: b64(X.bundleUpdateSig(ME, bundle, K.ident_sk)) })
              .then(function () { closeOverlay(); HK.toast(T('Neuer Wiederherstellungsschlüssel gilt ab jetzt.')); })
              .catch(function (err) { st.className = 'hk-status err'; st.textContent = err.message; busy(e.target, false, T('Übernehmen')); });
          }, 30);
        }));
      });
    }));
    box.appendChild(el('h3', null, T('Sicherheit')));
    box.appendChild(el('p', 'hk-hint', T('Private Schlüssel verlassen dieses Gerät nie unverschlüsselt. Auf dem Server liegen nur öffentliche Schlüssel, verschlüsselte Nachrichten und dein mit dem Wiederherstellungsschlüssel verschlüsseltes Schlüssel-Bündel. Vergleiche Sicherheitsnummern, um sicherzugehen, dass niemand dazwischen sitzt.')));
  }

  // --- Bereit: Liste, Hash-Ziel, Live ----------------------------------------------------------------------------
  function ready() {
    S.ready = true;
    loadList().then(function () { route(); });
    checkLinks();
  }
  function route() {
    var h = decodeURIComponent(location.hash.slice(1) || ''), m;
    if ((m = /^c(\d+)$/.exec(h))) openChat(+m[1]);
    else if ((m = /^p(\d+)$/.exec(h))) create({ kind: 'page', note_id: +m[1] });
    else if ((m = /^u(.+)$/.exec(h))) create({ kind: 'dm', user: m[1] });
    else if (h === 'settings') settings();
  }
  window.addEventListener('hashchange', function () { if (S.ready) route(); });

  function chatName(c) {
    if (c.kind === 'free') return c._title || c.members.filter(function (m) { return !m.me; }).map(function (m) { return m.name; }).join(', ') || T('Gruppen-Chat');
    return c.name || '';
  }
  function freeTitle(c) {
    if (c.kind !== 'free' || !c.title_msg) return;
    try {
      var b = decrypt(c.id, c.title_msg, c.title_msg.device ? { [c.title_msg.sender_device]: c.title_msg.device } : {},
                      c.title_msg.ident_pub ? { [c.title_msg.sender.username]: c.title_msg.ident_pub } : {});
      if (b && b.verified && b.body && b.body.type === 'title') c._title = String(b.body.title || '').slice(0, 100);
    } catch (e) { /* Name bleibt die Mitgliederliste */ }
  }
  function kindIc(c) { return c.kind === 'group' ? '#' : c.kind === 'page' ? '📄' : c.kind === 'free' ? '👥' : ''; }
  function loadList() {
    return api('GET', '/api/chats?device=' + q(dev())).then(function (r) {
      S.chats = r.chats || [];
      S.chats.forEach(freeTitle);
      drawList();
      if (S.cur) {
        var c = S.chats.filter(function (x) { return x.id === S.cur.id; })[0];
        if (!c) closeChat(); else { S.cur = Object.assign(S.cur, c); drawHead(); }
      }
    }).catch(function (e) { if (e.status === 403) fatal(e.message); });
  }
  function drawList() {
    var ul = $('ct-list'), f = ($('ct-filter').value || '').trim().toLowerCase();
    ul.textContent = '';
    var shown = S.chats.filter(function (c) { return !f || chatName(c).toLowerCase().indexOf(f) >= 0; });
    $('ct-list-empty').hidden = S.chats.length > 0;
    shown.forEach(function (c) {
      var li = el('li', 'ct-item' + (S.cur && S.cur.id === c.id ? ' on' : '') + (c.unread ? ' unread' : ''));
      var b = el('button', 'ct-item-b'); b.type = 'button'; b.dataset.id = c.id;
      var nm = chatName(c);
      b.appendChild(avatar(kindIc(c) || (nm.charAt(0) || '?').toUpperCase(), c.kind === 'dm' ? (c.peer || {}).username : 'c' + c.id, c.kind !== 'dm' ? 'ct-av-k' : ''));
      var t = el('span', 'ct-item-t');
      t.appendChild(el('span', 'ct-item-n', nm));
      t.appendChild(el('span', 'ct-item-s', (c.kind === 'dm' ? T('Direkt') : c.kind === 'group' ? T('Gruppe') : c.kind === 'page' ? T('Seite') : T('Gruppe')) +
        ' · ' + T('{n} Personen', { n: c.member_count }) + (c.muted ? ' · 🔕' : '')));
      b.appendChild(t);
      var side = el('span', 'ct-item-r');
      side.appendChild(el('span', 'ct-item-time', HK.relTime((c.last_at || c.created) - Date.now() / 1000)));
      if (c.unread) { var n = el('span', 'ct-badge', String(c.unread)); n.setAttribute('aria-label', T('{n} ungelesen', { n: c.unread })); side.appendChild(n); }
      b.appendChild(side);
      b.addEventListener('click', function () { if (location.hash === '#c' + c.id) openChat(c.id); else location.hash = 'c' + c.id; });
      li.appendChild(b); ul.appendChild(li);
    });
  }
  $('ct-filter').addEventListener('input', drawList);
  $('ct-new').addEventListener('click', function () { if (S.ready) newChat(); });
  $('ct-settings').addEventListener('click', function () { if (S.ready) settings(); });
  $('ct-back').addEventListener('click', function () { closeChat(); history.replaceState(null, '', location.pathname); });

  function create(body) {
    return api('POST', '/api/chats', Object.assign({ device: dev() }, body)).then(function (r) {
      closeOverlay();
      var c = r.chat; freeTitle(c);
      if (!S.chats.some(function (x) { return x.id === c.id; })) S.chats.unshift(c);
      history.replaceState(null, '', '#c' + c.id);
      return openChat(c.id).then(function () { return c; });
    }).catch(function (e) { HK.toast(e.message); });
  }

  // --- Neue Unterhaltung ----------------------------------------------------------------------------------------
  function newChat() {
    var box = overlay(T('Neue Unterhaltung'));
    var inp = el('input', 'hk-in ct-people-q'); inp.type = 'search'; inp.placeholder = T('Person oder Gruppe suchen (ab 3 Zeichen)'); inp.setAttribute('aria-label', T('Person oder Gruppe suchen'));
    box.appendChild(inp);
    var res = el('ul', 'ct-people'); box.appendChild(res);
    var pick = [], chips = el('div', 'ct-chips'), name = el('input', 'hk-in'), mk = btn(T('Gruppe anlegen'), 'btn-primary ct-free-go');
    var grp = el('div', 'ct-free'); grp.hidden = true;
    grp.appendChild(el('p', 'hk-hint', T('Freie Gruppe: ausgewählte Personen, der Name wird verschlüsselt.'))); grp.appendChild(chips);
    name.placeholder = T('Name der Gruppe'); name.maxLength = 80; name.setAttribute('aria-label', T('Name der Gruppe'));
    grp.appendChild(name); var gb = el('div', 'hk-actions'); gb.appendChild(mk); grp.appendChild(gb);
    box.appendChild(grp);
    var multi = el('button', 'linkbtn ct-free-toggle', T('+ Gruppe mit mehreren Personen …')); multi.type = 'button'; box.appendChild(multi);
    multi.addEventListener('click', function () { grp.hidden = false; multi.hidden = true; inp.focus(); });
    function drawChips() {
      chips.textContent = '';
      pick.forEach(function (u, i) {
        var c = el('span', 'ct-chip', u.name), x = el('button', 'linkbtn', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Entfernen'));
        x.addEventListener('click', function () { pick.splice(i, 1); drawChips(); }); c.appendChild(x); chips.appendChild(c);
      });
      mk.disabled = !pick.length;
    }
    drawChips();
    var tmr = null;
    inp.addEventListener('input', function () {
      clearTimeout(tmr);
      tmr = setTimeout(function () {
        var v = inp.value.trim();
        if (v.length < 3) { res.textContent = ''; return; }
        api('GET', '/api/chat/people?q=' + q(v)).then(function (r) {
          res.textContent = '';
          r.users.forEach(function (u) {
            var li = el('li'), b = el('button', 'ct-person'); b.type = 'button';
            b.appendChild(avatar((u.name || '?').charAt(0).toUpperCase(), u.username));
            var t = el('span', 'ct-item-t'); t.appendChild(el('span', 'ct-item-n', u.name)); t.appendChild(el('span', 'ct-item-s', u.username + (u.ready ? '' : ' · ' + T('hat Chats noch nicht eingerichtet'))));
            b.appendChild(t);
            b.addEventListener('click', function () {
              if (!grp.hidden) { if (!pick.some(function (p) { return p.username === u.username; })) pick.push(u); drawChips(); inp.value = ''; res.textContent = ''; inp.focus(); }
              else create({ kind: 'dm', user: u.username });
            });
            li.appendChild(b); res.appendChild(li);
          });
          r.groups.forEach(function (g) {
            var li = el('li'), b = el('button', 'ct-person'); b.type = 'button';
            b.appendChild(avatar('#', 'g' + g.name, 'ct-av-k'));
            var t = el('span', 'ct-item-t'); t.appendChild(el('span', 'ct-item-n', g.name)); t.appendChild(el('span', 'ct-item-s', T('Gruppen-Chat für alle in der Gruppe'))); b.appendChild(t);
            b.addEventListener('click', function () { create({ kind: 'group', group: g.name }); });
            li.appendChild(b); res.appendChild(li);
          });
          if (!r.users.length && !r.groups.length) res.appendChild(el('li', 'hk-hint', T('Keine Treffer.')));
        }).catch(function (e) { res.textContent = ''; res.appendChild(el('li', 'hk-status err', e.message)); });
      }, 250);
    });
    mk.addEventListener('click', function () {
      busy(mk, true);
      var title = name.value.trim();
      create({ kind: 'free', members: pick.map(function (p) { return p.username; }) }).then(function (c) {
        if (c && title) return sendBody(c.id, { v: 1, type: 'title', title: title }, 'title').then(function () { loadList(); });
      }).finally(function () { busy(mk, false); });
    });
    inp.focus();
  }

  // --- Entschluesseln, pruefen, Vertrauen (TOFU) ----------------------------------------------------------------
  // -> {body, verified}; wirft, wenn sich die Nachricht nicht oeffnen laesst
  function decrypt(cid, m, devices, idents) {
    var key = m.key_for === 'device' ? X.openKey(m.key, K.device.enc_pk, K.device.enc_sk) : X.openKey(m.key, K.backup_pk, K.backup_sk);
    var body = X.decryptMessage(cid, m, key), u = m.sender ? m.sender.username : null;
    var dv = devices[m.sender_device], ident = u && idents[u];
    var ok = !!(dv && ident && dv.uid === u && X.verifyDevice(u, ub(ident, 32), { id: m.sender_device, enc_pub: dv.enc_pub, sign_pub: dv.sign_pub, sig: dv.sig })
                && X.verifyMessage(cid, m, ub(dv.sign_pub, 32)));
    if (ok && u) noteIdent(u, ident);
    return { body: body, verified: ok };
  }
  // Identitaetsschluessel der Gegenueber merken; Wechsel -> Warnung im Chat (Sicherheitsnummer geaendert)
  var trust = {}, trustP = {};
  function noteIdent(u, ident) {
    if (u === ME) return;
    if (trust[u] && trust[u].ident === ident) return;
    if (!trustP[u]) trustP[u] = kvGet(trustKey(u)).catch(function () { return null; });
    trustP[u].then(function (t) {
      if (!t) { t = { ident: ident, verified: false, changed: false }; kvSet(trustKey(u), t).catch(function () {}); }
      else if (t.ident !== ident) { t = { ident: ident, verified: false, changed: true, old: t.ident }; kvSet(trustKey(u), t).catch(function () {}); }
      trust[u] = t; trustP[u] = Promise.resolve(t);
      if (t.changed) drawWarn();
    });
  }
  function drawWarn() {
    var w = $('ct-warn'), c = S.cur; if (!c) { w.hidden = true; return; }
    var bad = c.members.filter(function (m) { return trust[m.username] && trust[m.username].changed; });
    w.textContent = ''; w.hidden = !bad.length;
    bad.forEach(function (m) {
      var p = el('p');
      p.appendChild(document.createTextNode('⚠ ' + T('Die Sicherheitsnummer von {name} hat sich geändert – z. B. nach „Neu beginnen“ oder bei einem neuen Konto. Prüfe sie, bevor du Vertrauliches schreibst.', { name: m.name }) + ' '));
      p.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
      p.appendChild(btn(T('Verstanden'), 'small', function () {
        var t = trust[m.username]; t.changed = false; kvSet(trustKey(m.username), t).catch(function () {}); drawWarn();
      }));
      w.appendChild(p);
    });
  }
  function safety(m) {
    api('GET', '/api/chat/keys?users=' + q(m.username)).then(function (r) {
      var k = r.users[m.username];
      var box = overlay(T('Sicherheitsnummer mit {name}', { name: m.name }));
      if (!k) { box.appendChild(el('p', null, T('{name} hat Chats noch nicht eingerichtet.', { name: m.name }))); return; }
      var num = X.safetyNumber(ME, K.ident_pk, m.username, ub(k.ident_pub, 32));
      var g = el('div', 'ct-safety'); g.id = 'ct-safety';
      num.split(' ').forEach(function (x) { g.appendChild(el('span', null, x)); });
      box.appendChild(g);
      box.appendChild(el('p', null, T('Vergleiche diese Nummer mit {name} – persönlich, per Anruf oder auf deren Gerät. Stimmt sie überein, liest niemand mit. Sie ist auf beiden Seiten gleich.', { name: m.name })));
      noteIdent(m.username, k.ident_pub);
      var t = trust[m.username];
      if (t && t.verified && t.ident === k.ident_pub) box.appendChild(el('p', 'ct-ok', '✓ ' + T('Als überprüft markiert.')));
      else box.appendChild(btn(T('Als überprüft markieren'), 'btn-primary', function () {
        var nt = { ident: k.ident_pub, verified: true, changed: false };
        trust[m.username] = nt; trustP[m.username] = Promise.resolve(nt);
        kvSet(trustKey(m.username), nt).then(function () { closeOverlay(); drawWarn(); drawHead(); HK.toast(T('Als überprüft markiert.')); });
      }));
    }).catch(function (e) { HK.toast(e.message); });
  }

  // --- Unterhaltung -----------------------------------------------------------------------------------------------
  function closeChat() {
    S.cur = null; S.msgs = []; S.byId = {}; S.rc = null; S.reply = null; S.files = [];
    $('ct-chat').hidden = true; $('ct-none').hidden = false; root.classList.remove('ct-open');
    drawList();
  }
  function openChat(cid) {
    if (!S.ready) return Promise.resolve();
    var same = S.cur && S.cur.id === cid;
    return api('GET', '/api/chats/' + cid + '?device=' + q(dev())).then(function (r) {
      var c = r.chat; freeTitle(c);
      if (!same) { S.msgs = []; S.byId = {}; S.rc = null; S.reply = null; S.files = []; drawReply(); drawFiles(); $('ct-input').value = ''; S.mentions = {}; }
      S.cur = c;
      $('ct-none').hidden = true; $('ct-chat').hidden = false; root.classList.add('ct-open');
      drawHead(); drawList(); drawWarn();
      c.members.forEach(function (m) { if (!m.me && !trust[m.username]) trustP[m.username] = kvGet(trustKey(m.username)).then(function (t) { if (t) trust[m.username] = t; drawWarn(); return t; }).catch(function () { return null; }); });
      return loadMsgs(false);
    }).catch(function (e) {
      if (e.status === 404) { HK.toast(T('Diesen Chat gibt es nicht (mehr) oder du gehörst nicht mehr dazu.')); closeChat(); loadList(); }
      else HK.toast(e.message);
    });
  }
  function drawHead() {
    var c = S.cur; if (!c) return;
    $('ct-title').textContent = (kindIc(c) ? kindIc(c) + ' ' : '') + chatName(c);
    var sub = $('ct-sub'); sub.textContent = '';
    var others = c.members.filter(function (m) { return !m.me; });
    if (c.kind === 'dm') {
      var p = others[0] || c.peer || {}, t = trust[p.username];
      sub.appendChild(document.createTextNode(p.username || ''));
      if (t && t.verified && !t.changed) sub.appendChild(el('span', 'ct-chip ct-verified', '✓ ' + T('überprüft')));
    } else sub.appendChild(document.createTextNode(c.members.map(function (m) { return m.me ? T('du') : m.name; }).join(', ')));
    if (c.retention_s) sub.appendChild(el('span', 'ct-chip', '⏱ ' + (RET.filter(function (r) { return r[0] === c.retention_s; })[0] || [0, ''])[1]));
    if (c.muted) sub.appendChild(el('span', 'ct-chip', '🔕'));
    if (c.kind === 'page' && c.note_id) {
      var a = el('a', 'ct-chip ct-pagelink', T('Seite öffnen')); a.href = '/notes#' + c.note_id; a.target = 'hk-notes'; sub.appendChild(a);
    }
    document.title = chatName(c) + ' · ' + T('Chats');
  }
  function loadMsgs(older) {
    var c = S.cur; if (!c || S.loading) return Promise.resolve();
    S.loading = true;
    var first = S.msgs.length ? S.msgs[0].seq : 0, last = S.msgs.length ? S.msgs[S.msgs.length - 1].seq : 0;
    var url = '/api/chats/' + c.id + '/messages?device=' + q(dev()) + (older && first ? '&before=' + first : (!older && last ? '&after=' + last : ''));
    return api('GET', url).then(function (r) {
      S.loading = false;
      if (!S.cur || S.cur.id !== c.id) return;
      var add = r.messages.filter(function (m) { return !S.byId[m.id]; }).map(function (m) {
        var d;
        try { d = decrypt(c.id, m, r.devices || {}, r.idents || {}); } catch (e) { d = { body: null, verified: false, error: true }; }
        m.body = d.body; m.verified = d.verified; m.error = !!d.error;
        S.byId[m.id] = m; return m;
      });
      if (older) { S.msgs = add.concat(S.msgs); S.more = r.more; }
      else { S.msgs = S.msgs.concat(add); if (!last) S.more = r.more; }
      drawMsgs(older ? 'keep' : 'bottom');
      resolveNotes();
      markRead();
      if (!older && r.more && last) return loadMsgs(false);
    }).catch(function (e) { S.loading = false; HK.toast(T('Nachrichten nicht geladen: {err}', { err: e.message })); });
  }
  function markRead() {
    var c = S.cur; if (!c || document.hidden || !S.msgs.length) return;
    var seq = S.msgs[S.msgs.length - 1].seq;
    if (seq <= (c.last_read || 0)) return;
    c.last_read = seq;
    api('POST', '/api/chats/' + c.id + '/read', { seq: seq }).then(function () {
      var x = S.chats.filter(function (y) { return y.id === c.id; })[0]; if (x) { x.unread = 0; x.last_read = seq; drawList(); }
    }).catch(function () {});
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) markRead(); });

  // --- Nachrichten zeichnen (nur textContent; Links nur http/https, Seitenverweise nur mit Zugriff) ------------------
  var TOKEN = /\[\[note:([1-9][0-9]{0,17})\]\]|(https?:\/\/[^\s<>"'`]+[^\s<>"'`.,;:!?)\]])/g;
  function renderText(box, text) {
    var s = String(text || ''), last = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(s))) {
      if (m.index > last) box.appendChild(document.createTextNode(s.slice(last, m.index)));
      if (m[1]) {
        var n = S.notes[m[1]], a = el('a', 'ct-note' + (n === null ? ' na' : ''));
        a.dataset.note = m[1];
        if (n) { a.href = '/notes#' + m[1]; a.target = 'hk-notes'; a.textContent = (n.icon ? n.icon + ' ' : '📄 ') + n.title; }
        else a.textContent = n === null ? '📄 ' + T('Seite (kein Zugriff)') : '📄 …';
        box.appendChild(a);
      } else {
        var u = el('a', 'ct-url', m[2]); u.href = m[2]; u.target = '_blank'; u.rel = 'noopener noreferrer'; box.appendChild(u);
      }
      last = TOKEN.lastIndex;
    }
    if (last < s.length) box.appendChild(document.createTextNode(s.slice(last)));
  }
  function resolveNotes() {
    var ids = {};
    S.msgs.forEach(function (m) { var t = m.body && m.body.text; if (!t) return; var r = /\[\[note:([1-9][0-9]{0,17})\]\]/g, x; while ((x = r.exec(t))) if (!(x[1] in S.notes)) ids[x[1]] = 1; });
    var want = Object.keys(ids); if (!want.length) return;
    api('GET', '/api/chat/notes?ids=' + want.slice(0, 100).join(',')).then(function (r) {
      want.forEach(function (i) { S.notes[i] = r.notes[i] || null; });
      document.querySelectorAll('#ct-msgs .ct-note').forEach(function (a) {
        var n = S.notes[a.dataset.note]; if (n === undefined) return;
        if (n) { a.href = '/notes#' + a.dataset.note; a.target = 'hk-notes'; a.textContent = (n.icon ? n.icon + ' ' : '📄 ') + n.title; a.classList.remove('na'); }
        else { a.removeAttribute('href'); a.textContent = '📄 ' + T('Seite (kein Zugriff)'); a.classList.add('na'); }
      });
    }).catch(function () {});
  }
  function preview(m) {
    if (!m) return T('Nachricht nicht mehr da');
    if (m.error || !m.body) return T('Nicht entschlüsselbar');
    var t = String(m.body.text || '').replace(/\[\[note:\d+\]\]/g, '📄').replace(/\s+/g, ' ').trim();
    if (!t && (m.body.files || []).length) t = '📎 ' + m.body.files.map(function (f) { return f.n; }).join(', ');
    return t.length > 90 ? t.slice(0, 90) + '…' : t;
  }
  function drawMsgs(scroll) {
    var box = $('ct-msgs'), c = S.cur, atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    var prevH = box.scrollHeight, prevTop = box.scrollTop;
    box.textContent = '';
    if (S.more) {
      var mo = btn(T('Ältere Nachrichten laden'), 'small ct-older', function () { loadMsgs(true); });
      box.appendChild(mo);
    } else {
      box.appendChild(el('p', 'ct-start', '🔒 ' + T('Nachrichten in diesem Chat sind Ende-zu-Ende-verschlüsselt.') +
        (c.kind !== 'dm' ? ' ' + T('Wer neu dazukommt, sieht nur Nachrichten ab dem Beitritt.') : '')));
    }
    var lastDay = '', lastWho = null;
    S.msgs.forEach(function (m) {
      var d = new Date(m.created * 1000), day = fDay.format(d);
      if (day !== lastDay) { box.appendChild(el('div', 'ct-day', day)); lastDay = day; lastWho = null; }
      var mine = m.sender && m.sender.username === ME;
      var row = el('div', 'ct-msg' + (mine ? ' mine' : '')); row.dataset.id = m.id;
      if (!mine && c.kind !== 'dm' && lastWho !== (m.sender || {}).username) box.appendChild(el('div', 'ct-who', m.sender ? m.sender.name : T('Gelöschtes Konto')));
      lastWho = (m.sender || {}).username;
      var b = el('div', 'ct-bubble');
      if (m.reply_to) {
        var o = S.byId[m.reply_to], qd = el('button', 'ct-quote'); qd.type = 'button';
        qd.appendChild(el('b', null, o && o.sender ? o.sender.name : '…')); qd.appendChild(el('span', null, preview(o)));
        qd.addEventListener('click', function () { var t = box.querySelector('.ct-msg[data-id="' + m.reply_to + '"]'); if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('flash'); setTimeout(function () { t.classList.remove('flash'); }, 1200); } });
        b.appendChild(qd);
      }
      if (m.error || !m.body) b.appendChild(el('div', 'ct-text ct-err', '🔒 ' + T('Diese Nachricht lässt sich auf diesem Gerät nicht entschlüsseln.')));
      else {
        if (m.body.text) { var tx = el('div', 'ct-text'); renderText(tx, m.body.text); b.appendChild(tx); }
        (Array.isArray(m.body.files) ? m.body.files : []).slice(0, 10).forEach(function (f) { b.appendChild(fileEl(c.id, f)); });
      }
      var meta = el('div', 'ct-meta');
      if (m.expires_at) meta.appendChild(el('span', 'ct-exp', '⏱'));
      if (!m.verified && !m.error) { var w = el('span', 'ct-unv', '⚠'); w.title = T('Absender nicht bestätigt: Signatur oder Geräte-Schlüssel passen nicht (z. B. nach „Neu beginnen“).'); meta.appendChild(w); }
      var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); tm.title = fDay.format(d) + ' ' + fTime.format(d); meta.appendChild(tm);
      b.appendChild(meta);
      row.appendChild(b);
      var act = el('button', 'icon-btn ct-act', '⋯'); act.type = 'button'; act.setAttribute('aria-label', T('Nachricht …')); act.setAttribute('aria-haspopup', 'menu');
      act.addEventListener('click', function () { msgMenu(act, m); });
      row.appendChild(act);
      box.appendChild(row);
    });
    applySearch();
    if (scroll === 'keep') box.scrollTop = box.scrollHeight - prevH + prevTop;
    else if (scroll === 'bottom' || atBottom) box.scrollTop = box.scrollHeight;
  }
  function msgMenu(anchor, m) {
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      if (m.body) menu.appendChild(HK.menuItem(T('Antworten'), function () { HK.closeMenu(); S.reply = m; drawReply(); $('ct-input').focus(); }, { icon: '↩' }));
      if (m.body && m.body.text) menu.appendChild(HK.menuItem(T('Text kopieren'), function () {
        HK.closeMenu(); if (navigator.clipboard) navigator.clipboard.writeText(m.body.text).then(function () { HK.toast(T('Kopiert.')); });
      }, { icon: '⧉' }));
      if (m.sender && m.sender.username === ME) menu.appendChild(HK.menuItem(T('Für alle löschen'), function () {
        HK.closeMenu();
        HK.ask({ title: T('Nachricht für alle löschen?'), text: T('Die Nachricht und ihre Anhänge werden vom Server gelöscht. Wer sie schon gelesen hat, kann eine Kopie haben.'), ok: T('Löschen'), danger: true })
          .then(function (y) { if (y) api('DELETE', '/api/chats/' + S.cur.id + '/messages/' + m.id).then(function () { dropMsg(m.id); }).catch(function (e) { HK.toast(e.message); }); });
      }, { icon: '🗑', danger: true }));
    });
  }
  function dropMsg(id) {
    if (!S.byId[id]) return;
    delete S.byId[id]; S.msgs = S.msgs.filter(function (x) { return x.id !== id; });
    if (S.reply && S.reply.id === id) { S.reply = null; drawReply(); }
    drawMsgs();
  }
  function drawReply() {
    var r = $('ct-reply'); r.textContent = ''; r.hidden = !S.reply; if (!S.reply) return;
    var t = el('div', 'ct-reply-t'); t.appendChild(el('b', null, T('Antwort an {name}', { name: S.reply.sender ? S.reply.sender.name : '' })));
    t.appendChild(el('span', null, preview(S.reply))); r.appendChild(t);
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Antwort abbrechen'));
    x.addEventListener('click', function () { S.reply = null; drawReply(); }); r.appendChild(x);
  }

  // --- Anhaenge (clientseitig verschluesselt) --------------------------------------------------------------------
  var blobCache = {};
  function fetchFile(cid, f) {
    if (blobCache[f.id]) return blobCache[f.id];
    blobCache[f.id] = fetch('/api/chat/files/' + encodeURIComponent(f.id), { credentials: 'same-origin', headers: { 'X-Saga': '1' } })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        var plain = X.decryptFile(cid, new Uint8Array(buf), f.k);
        var mime = /^image\/(png|jpeg|gif|webp)$/.test(f.m) ? f.m : 'application/octet-stream';    // nie text/html o. ae. als Blob-URL
        return URL.createObjectURL(new Blob([plain], { type: mime }));
      });
    blobCache[f.id].catch(function () { delete blobCache[f.id]; });
    return blobCache[f.id];
  }
  function fileEl(cid, f) {
    var name = String(f.n || T('Datei')).slice(0, 200), img = /^image\/(png|jpeg|gif|webp)$/.test(f.m);
    if (img) {
      var fig = el('button', 'ct-img'); fig.type = 'button'; fig.setAttribute('aria-label', name);
      var im = el('img'); im.alt = name; fig.appendChild(im);
      fetchFile(cid, f).then(function (u) { im.src = u; }, function () { fig.textContent = '🖼 ' + name + ' – ' + T('nicht geladen'); });
      fig.addEventListener('click', function () { fetchFile(cid, f).then(function (u) { save(u, name); }); });
      return fig;
    }
    var b = el('button', 'ct-file'); b.type = 'button';
    b.appendChild(el('span', 'ct-file-ic', '📎')); b.appendChild(el('span', 'ct-file-n', name)); b.appendChild(el('span', 'ct-file-s', size(f.s)));
    b.addEventListener('click', function () { fetchFile(cid, f).then(function (u) { save(u, name); }, function (e) { HK.toast(T('Anhang nicht geladen: {err}', { err: e.message })); }); });
    return b;
  }
  function save(url, name) { var a = el('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  function size(n) { n = +n || 0; return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n > 1024 ? Math.round(n / 1024) + ' KB' : n + ' B'; }
  function drawFiles() {
    var box = $('ct-files'); box.textContent = ''; box.hidden = !S.files.length;
    S.files.forEach(function (f, i) {
      var c = el('span', 'ct-chip', '📎 ' + f.name + ' (' + size(f.size) + ')'), x = el('button', 'linkbtn', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Entfernen'));
      x.addEventListener('click', function () { S.files.splice(i, 1); drawFiles(); }); c.appendChild(x); box.appendChild(c);
    });
  }
  $('ct-attach').addEventListener('click', function () { $('ct-file').click(); });
  $('ct-file').addEventListener('change', function () {
    var max = (S.me && S.me.limits && S.me.limits.file_max) || 10485760;
    Array.prototype.forEach.call(this.files, function (f) {
      if (f.size > max) { HK.toast(T('„{name}“ ist zu groß (höchstens {n} MB).', { name: f.name, n: Math.round(max / 1048576) })); return; }
      if (S.files.length < 10) S.files.push(f);
    });
    this.value = ''; drawFiles();
  });

  // --- Senden ---------------------------------------------------------------------------------------------------
  // Empfaenger vom Server holen und JEDEN Schluessel pruefen (Geraete-Zertifikat + Backup-Signatur gegen den
  // Identitaetsschluessel); ein ungueltiger Schluessel bricht das Senden ab statt still an ihn zu verschluesseln.
  function recipients(cid, fresh) {
    if (!fresh && S.rc && S.rc.cid === cid) return Promise.resolve(S.rc);
    return api('GET', '/api/chats/' + cid + '/recipients').then(function (r) { return useRecipients(cid, r.recipients); });
  }
  function useRecipients(cid, list) {
    var out = [], missing = [];
    list.forEach(function (p) {
      if (!p.ready) { missing.push(p.name); return; }
      var ip = ub(p.ident_pub, 32);
      if (!X.verifyBackup(p.username, ip, p.backup_pub, p.backup_sig)) throw new Error(T('Ungültige Schlüssel von {name} – Senden abgebrochen.', { name: p.name }));
      if (p.username === ME && !eqB(ip, K.ident_pk)) throw new Error(T('Deine Schlüssel auf dem Server passen nicht zu diesem Gerät.'));
      noteIdent(p.username, p.ident_pub);
      out.push({ id: 'backup:' + p.username, pk: ub(p.backup_pub, 32) });
      p.devices.forEach(function (d) {
        if (!X.verifyDevice(p.username, ip, d)) throw new Error(T('Ungültige Schlüssel von {name} – Senden abgebrochen.', { name: p.name }));
        out.push({ id: d.id, pk: ub(d.enc_pub, 32) });
      });
    });
    S.rc = { cid: cid, list: out, missing: missing, raw: list };
    return S.rc;
  }
  // TOFU wirksam machen: Hat sich der Identitaetsschluessel eines Empfaengers gegenueber dem gemerkten Wert geaendert
  // (und ist die Warnung noch nicht mit "Verstanden"/"Als ueberprueft markieren" bestaetigt), wird NICHT gesendet -
  // sonst ginge die erste Nachricht nach einem vom Server untergeschobenen Schluessel schon an den Angreifer.
  function trustCheck(list) {
    return Promise.all((list || []).filter(function (p) { return p.ready && p.username !== ME; }).map(function (p) {
      var pr = trust[p.username] ? Promise.resolve(trust[p.username])
        : (trustP[p.username] || (trustP[p.username] = kvGet(trustKey(p.username)).catch(function () { return null; })));
      return pr.then(function (t) { return t && (t.changed || t.ident !== p.ident_pub) ? p : null; });
    })).then(function (bad) {
      bad = bad.filter(Boolean);
      if (!bad.length) return;
      bad.forEach(function (p) { noteIdent(p.username, p.ident_pub); });
      setTimeout(drawWarn, 50);
      throw new Error(T('Die Sicherheitsnummer von {name} hat sich geändert – erst prüfen oder „Verstanden“ wählen.', { name: bad.map(function (p) { return p.name; }).join(', ') }));
    });
  }
  function sendBody(cid, body, kind, reply) {
    function attempt(rc, again) {
      var m = X.encryptMessage({ chat_id: cid, sender: ME, device: K.device.id, sign_sk: K.device.sign_sk, body: body, reply_to: reply || null, recipients: rc.list });
      m.kind = kind || 'msg';
      if (body.files) m.files = body.files.map(function (f) { return f.id; });
      return api('POST', '/api/chats/' + cid + '/messages', m).then(function (r) { return { m: m, r: r }; }, function (e) {
        if (e.status === 409 && e.data && e.data.recipients && !again) {
          var rc2 = useRecipients(cid, e.data.recipients);
          return trustCheck(rc2.raw).then(function () { return attempt(rc2, true); });
        }
        throw e;
      });
    }
    return recipients(cid).then(function (rc) { return trustCheck(rc.raw).then(function () { return attempt(rc, false); }); });
  }
  function uploadFiles(cid) {
    return Promise.all(S.files.map(function (f) {
      return f.arrayBuffer().then(function (buf) {
        var e = X.encryptFile(cid, new Uint8Array(buf));
        return fetch('/api/chats/' + cid + '/files', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/octet-stream' }, body: e.blob })
          .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; }); })
          .then(function (d) { return { id: d.id, k: e.key, n: f.name.slice(0, 200), m: f.type || 'application/octet-stream', s: f.size }; });
      });
    }));
  }
  var sending = false;
  $('ct-compose').addEventListener('submit', function (e) {
    e.preventDefault();
    var c = S.cur, inp = $('ct-input'), text = inp.value.replace(/\s+$/, '');
    if (!c || sending || (!text.trim() && !S.files.length)) return;
    var notes = [];
    Object.keys(S.mentions).forEach(function (label) {
      if (text.indexOf(label) >= 0) { text = text.split(label).join('[[note:' + S.mentions[label] + ']]'); notes.push(S.mentions[label]); }
    });
    sending = true; $('ct-send').disabled = true;
    var reply = S.reply ? S.reply.id : null;
    (S.files.length ? uploadFiles(c.id) : Promise.resolve([])).then(function (files) {
      var body = { v: 1, type: 'text', text: text };
      if (files.length) body.files = files;
      if (notes.length) body.notes = notes;
      return sendBody(c.id, body, 'msg', reply);
    }).then(function (x) {
      inp.value = ''; autosize(); S.reply = null; S.files = []; S.mentions = {}; drawReply(); drawFiles();
      if (S.rc && S.rc.missing.length) HK.toast(T('{names} hat Chats noch nicht eingerichtet und kann diese Nachricht nicht lesen.', { names: S.rc.missing.join(', ') }), null, null, 6000);
      return loadMsgs(false).then(function () { return x; });
    }).catch(function (err) { HK.toast(T('Nicht gesendet: {err}', { err: err.message })); })
      .finally(function () { sending = false; $('ct-send').disabled = false; inp.focus(); });
  });
  function autosize() { var t = $('ct-input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 200) + 'px'; }
  var typT = 0;
  $('ct-input').addEventListener('keydown', function (e) {
    if (pickerOpen() && pickKey(e)) return;
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('ct-compose').requestSubmit(); }
  });
  $('ct-input').addEventListener('input', function () {
    autosize(); picker();
    if (S.cur && Date.now() - typT > 3000 && this.value) { typT = Date.now(); api('POST', '/api/chats/' + S.cur.id + '/typing').catch(function () {}); }
  });

  // @-Verweis auf eine Seite: "@" + Suchtext -> Liste eigener/geteilter Seiten; eingefuegt wird "@Titel", gesendet [[note:ID]]
  var pk = { items: [], i: 0, start: -1, t: null };
  function pickerOpen() { return !$('ct-picker').hidden; }
  function picker() {
    var t = $('ct-input'), v = t.value.slice(0, t.selectionStart), m = /(^|\s)@([^\s@]{0,30})$/.exec(v);
    var box = $('ct-picker');
    if (!m) { box.hidden = true; return; }
    pk.start = v.length - m[2].length - 1;
    clearTimeout(pk.t);
    pk.t = setTimeout(function () {
      api('GET', '/api/chat/pages?q=' + q(m[2])).then(function (r) {
        pk.items = r.pages || []; pk.i = 0; box.textContent = '';
        if (!pk.items.length) { box.hidden = true; return; }
        pk.items.forEach(function (p, i) {
          var b = el('button', 'picker-item' + (i === 0 ? ' on' : ''), (p.icon || '📄') + ' ' + p.title); b.type = 'button'; b.setAttribute('role', 'option');
          b.addEventListener('mousedown', function (e) { e.preventDefault(); pickApply(i); });
          box.appendChild(b);
        });
        box.hidden = false;
      }).catch(function () { box.hidden = true; });
    }, 150);
  }
  function pickKey(e) {
    var box = $('ct-picker');
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); pk.i = (pk.i + (e.key === 'ArrowDown' ? 1 : pk.items.length - 1)) % pk.items.length;
      Array.prototype.forEach.call(box.children, function (b, i) { b.classList.toggle('on', i === pk.i); }); return true;
    }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pickApply(pk.i); return true; }
    if (e.key === 'Escape') { e.preventDefault(); box.hidden = true; return true; }
    return false;
  }
  function pickApply(i) {
    var p = pk.items[i], t = $('ct-input'); if (!p) return;
    var label = '@' + p.title.replace(/\s+/g, ' ').slice(0, 60);
    S.mentions[label] = p.id;
    t.value = t.value.slice(0, pk.start) + label + ' ' + t.value.slice(t.selectionStart);
    var pos = pk.start + label.length + 1; t.setSelectionRange(pos, pos);
    $('ct-picker').hidden = true; t.focus();
  }

  // --- Kopf: Suche, Menue ---------------------------------------------------------------------------------------
  $('ct-search-btn').addEventListener('click', function () {
    var b = $('ct-searchbar'); b.hidden = !b.hidden;
    if (!b.hidden) $('ct-search').focus(); else { $('ct-search').value = ''; S.searchQ = ''; applySearch(); }
  });
  $('ct-search').addEventListener('input', function () { S.searchQ = this.value.trim().toLowerCase(); applySearch(); });
  function applySearch() {
    var qy = S.searchQ, n = 0;
    document.querySelectorAll('#ct-msgs .ct-msg').forEach(function (r) {
      var m = S.byId[r.dataset.id], hit = !qy || !!(m && m.body && String(m.body.text || '').toLowerCase().indexOf(qy) >= 0);
      r.classList.toggle('ct-dim', !hit); if (qy && hit) n++;
    });
    $('ct-search-n').textContent = qy ? T('{n} Treffer', { n: n }) : '';
  }
  $('ct-more').addEventListener('click', function () {
    var c = S.cur; if (!c) return;
    var anchor = this;
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      var others = c.members.filter(function (m) { return !m.me; });
      if (c.kind === 'dm' && others[0]) menu.appendChild(HK.menuItem(T('Sicherheitsnummer'), function () { HK.closeMenu(); safety(others[0]); }, { icon: '🔐' }));
      menu.appendChild(HK.menuItem(T('Mitglieder'), function () { HK.closeMenu(); members(); }, { icon: '👥', hint: String(c.member_count) }));
      menu.appendChild(HK.menuItem(c.muted ? T('Benachrichtigungen an') : T('Stummschalten'), function () {
        HK.closeMenu(); api('PATCH', '/api/chats/' + c.id, { muted: !c.muted }).then(function () { loadList(); }).catch(function (e) { HK.toast(e.message); });
      }, { icon: c.muted ? '🔔' : '🔕' }));
      HK.menuLabel && menu.appendChild(HK.menuLabel(T('Nachrichten automatisch löschen nach')));
      var mx = (S.me && S.me.limits && S.me.limits.retention_max) || 0;
      RET.forEach(function (r) {
        if (mx && (r[0] === 0 || r[0] > mx)) return;
        menu.appendChild(HK.menuItem(r[1], function () {
          HK.closeMenu();
          api('PATCH', '/api/chats/' + c.id, { retention_s: r[0] }).then(function () { HK.toast(T('Gilt für neue Nachrichten.')); loadList(); }).catch(function (e) { HK.toast(e.message); });
        }, { on: c.retention_s === r[0] }));
      });
      if (c.kind === 'free') {
        menu.appendChild(HK.menuItem(T('Umbenennen …'), function () { HK.closeMenu(); rename(); }, { icon: '✎' }));
        menu.appendChild(HK.menuItem(T('Gruppe verlassen'), function () {
          HK.closeMenu();
          HK.ask({ title: T('Gruppe verlassen?'), text: T('Du bekommst keine neuen Nachrichten mehr.'), ok: T('Verlassen'), danger: true }).then(function (y) {
            if (y) api('DELETE', '/api/chats/' + c.id + '/members/' + q(ME)).then(function () { closeChat(); loadList(); }).catch(function (e) { HK.toast(e.message); });
          });
        }, { icon: '⎋', danger: true }));
      }
    });
  });
  function rename() {
    var c = S.cur, box = overlay(T('Gruppe umbenennen')), inp = el('input', 'hk-in'); inp.maxLength = 80; inp.value = c._title || '';
    inp.setAttribute('aria-label', T('Name der Gruppe')); box.appendChild(inp);
    box.appendChild(el('p', 'hk-hint', T('Der Name wird wie eine Nachricht verschlüsselt.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Speichern'), 'btn-primary', function () {
      var t = inp.value.trim(); if (!t) return;
      sendBody(c.id, { v: 1, type: 'title', title: t }, 'title').then(function () { closeOverlay(); loadList(); }).catch(function (e) { HK.toast(e.message); });
    }));
    inp.focus();
  }
  function members() {
    var c = S.cur, box = overlay(T('Mitglieder'));
    if (c.kind === 'group') box.appendChild(el('p', 'hk-hint', T('Alle Mitglieder der Gruppe „{g}“ – ändert sich mit der Gruppe.', { g: c.group })));
    if (c.kind === 'page') box.appendChild(el('p', 'hk-hint', T('Alle, die die Seite sehen dürfen – ändert sich mit den Freigaben.')));
    var ul = el('ul', 'ct-devs'); box.appendChild(ul);
    c.members.forEach(function (m) {
      var li = el('li', 'ct-dev'); li.appendChild(avatar((m.name || '?').charAt(0).toUpperCase(), m.username));
      var t = el('div', 'ct-dev-t'); t.appendChild(el('b', null, m.me ? m.name + ' (' + T('du') + ')' : m.name)); t.appendChild(el('span', 'hk-hint', m.username)); li.appendChild(t);
      if (!m.me) li.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
      if (c.kind === 'free' && c.role === 'admin' && !m.me) li.appendChild(btn(T('Entfernen'), 'btn-danger small', function () {
        api('DELETE', '/api/chats/' + c.id + '/members/' + q(m.username)).then(function () { closeOverlay(); openChat(c.id); loadList(); }).catch(function (e) { HK.toast(e.message); });
      }));
      ul.appendChild(li);
    });
    if (c.kind === 'free' && c.role === 'admin') {
      var inp = el('input', 'hk-in'); inp.type = 'search'; inp.placeholder = T('Person hinzufügen (ab 3 Zeichen)'); inp.setAttribute('aria-label', T('Person hinzufügen'));
      var res = el('ul', 'ct-people'); box.appendChild(inp); box.appendChild(res);
      var tmr = null;
      inp.addEventListener('input', function () {
        clearTimeout(tmr); tmr = setTimeout(function () {
          if (inp.value.trim().length < 3) { res.textContent = ''; return; }
          api('GET', '/api/chat/people?q=' + q(inp.value.trim())).then(function (r) {
            res.textContent = '';
            r.users.filter(function (u) { return !c.members.some(function (m) { return m.username === u.username; }); }).forEach(function (u) {
              var li = el('li'), b = el('button', 'ct-person', u.name + ' (' + u.username + ')'); b.type = 'button';
              b.addEventListener('click', function () {
                api('POST', '/api/chats/' + c.id + '/members', { user: u.username }).then(function () {
                  closeOverlay(); openChat(c.id); loadList();
                  if (c._title) sendBody(c.id, { v: 1, type: 'title', title: c._title }, 'title').catch(function () {});   // Name fuer Neue lesbar
                }).catch(function (e) { HK.toast(e.message); });
              });
              li.appendChild(b); res.appendChild(li);
            });
          }).catch(function () {});
        }, 250);
      });
    }
  }

  // --- Live (live.js -> 'hk-live'), sonst Abfrage ---------------------------------------------------------------
  var listT = null;
  function listSoon() { clearTimeout(listT); listT = setTimeout(loadList, 300); }
  document.addEventListener('hk-live', function (e) {
    var t = e.detail.type, d = e.detail.data || {};
    if (t === 'chat.link') { if (S.linkWait) linkPoll(); else if (!d.approved && !d.gone) checkLinks(); return; }
    if (t === 'chat.device' && K && d.id === K.device.id) { forget().then(function () { closeChat(); newDevice(); }); return; }
    if (!S.ready) return;
    if (t === 'resync') { loadList(); if (S.cur) loadMsgs(false); return; }
    if (t === 'chat.message') { if (S.cur && S.cur.id === d.chat_id) loadMsgs(false); listSoon(); }
    else if (t === 'chat.deleted') { if (S.cur && S.cur.id === d.chat_id) dropMsg(d.id); listSoon(); }
    else if (t === 'chat.read') listSoon();
    else if (t === 'chat.changed') { listSoon(); if (S.cur && S.cur.id === d.chat_id) { S.rc = null; openChat(d.chat_id); } }
    else if (t === 'chat.typing' && S.cur && S.cur.id === d.chat_id && d.user) {
      S.typing[d.user.username] = { name: d.user.name, until: Date.now() + 4500 }; drawTyping();
    }
  });
  function drawTyping() {
    var now = Date.now(), names = Object.keys(S.typing).filter(function (u) { return S.typing[u].until > now; }).map(function (u) { return S.typing[u].name; });
    $('ct-typing').textContent = names.length ? T('{names} tippt …', { names: names.join(', ') }) : '';
    if (names.length) setTimeout(drawTyping, 1000);
  }
  setInterval(function () {                                 // Rueckfall ohne Live-Kanal (oder Proxy puffert)
    if (!S.ready || document.hidden) return;
    var live = window.HKLive && window.HKLive.state && window.HKLive.state().sse;
    if (!live) { loadList(); if (S.cur) loadMsgs(false); }
  }, 30000);

  window.HKChat = { state: function () { return { ready: S.ready, device: dev(), cur: S.cur && S.cur.id, msgs: S.msgs.length }; } };
  start();
})();
