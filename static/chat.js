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
            typing: {}, mentions: {}, ai: null, aiPending: null, searchQ: '', loading: false, linkWait: null, ready: false };
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
  function avatar(text, key, cls, url) {
    var a = el('span', 'ct-av' + (cls ? ' ' + cls : ''), text);
    a.style.setProperty('--ct-h', String(hue(key))); a.setAttribute('aria-hidden', 'true');
    return HK.avatarImg(a, url, 36);                  // Profilbild ueber den Initialen (falls vorhanden)
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
      if (K && !eqB(K.ident_pk, ub(me.identity.ident_pub, 32))) return forget().then(function () { signedOut(true); });
      if (K && !mine) return forget().then(function () { signedOut(false); });
      if (!K) return newDevice();
      ready();
    }).catch(function (e) {
      if (e && e.message === 'idb') fatal(T('Dieser Browser erlaubt keinen lokalen Speicher (IndexedDB) – im privaten Fenster gehen Chats nicht.'));
      else fatal(T('Chats nicht geladen: {err}', { err: e.message }));
    });
  }
  // Dieses Geraet wurde anderswo abgemeldet ("Neu beginnen" auf einem anderen Geraet bzw. Geraet entfernt): erklaeren,
  // statt still leer zu bleiben oder erst beim Senden zu scheitern (BUG-057); danach wie ein neues Geraet verbinden.
  function signedOut(reset) {
    var box = overlay(reset ? T('Chats wurden neu begonnen') : T('Dieses Gerät wurde abgemeldet'), { close: false });
    box.appendChild(el('p', null, reset
      ? T('Auf einem anderen Gerät wurden deine Chats mit neuen Schlüsseln neu begonnen. Dieses Gerät ist dadurch abgemeldet, bisherige Nachrichten sind hier nicht mehr lesbar.')
      : T('Dieses Gerät wurde aus deinen Chat-Geräten entfernt.')));
    box.appendChild(el('p', null, T('Verbinde es neu – mit einem anderen Gerät oder mit deinem Wiederherstellungsschlüssel.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Weiter'), 'btn-primary ct-signedout-go', newDevice));
  }
  // Laufender Tab: beim Zurueckkehren und vor dem Senden pruefen, ob dieses Geraet noch gilt -> true/false
  function checkDevice() {
    if (!K || !S.ready) return Promise.resolve(true);
    return api('GET', '/api/chat/me?device=' + q(K.device.id)).then(function (me) {
      var mine = (me.devices || []).filter(function (d) { return d.id === K.device.id && !d.revoked; })[0];
      var reset = !!me.identity && !eqB(K.ident_pk, ub(me.identity.ident_pub, 32));
      if (me.identity && mine && !reset) return true;
      return forget().then(function () { closeChat(); if (me.identity) signedOut(reset); else start(); return false; });
    }).catch(function () { return true; });                  // Netzfehler: nicht abmelden
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) checkDevice(); });
  function fatal(msg) {
    root.textContent = ''; var p = el('p', 'ct-off', msg); p.setAttribute('role', 'alert'); root.appendChild(p);
  }
  function forget() { K = null; S.ready = false; bClear(); return kvDel(KEYS).catch(function () {}); }
  function saveKeys(k) { K = k; return kvSet(KEYS, k); }

  // --- Einrichtung (erstes Geraet) -------------------------------------------------------------------------------
  function setupIntro(reset) {
    var box = overlay(reset ? T('Chats neu beginnen') : T('Chats einrichten'), { close: !!reset });
    box.appendChild(el('p', null, T('Saga-Chats sind Ende-zu-Ende-verschlüsselt: Nachrichten und Anhänge werden auf deinen Geräten ver- und entschlüsselt. Der Server – auch der Admin – sieht nur verschlüsselte Daten.')));
    box.appendChild(el('p', null, T('Dafür bekommst du gleich einen Wiederherstellungsschlüssel. Damit holst du deine Chats auf ein neues Gerät, wenn kein anderes mehr da ist. Ohne ihn sind deine Nachrichten verloren – auch der Admin kann sie nicht wiederherstellen.')));
    if (reset) box.appendChild(el('p', 'ct-danger', T('Achtung: Neu beginnen erzeugt neue Schlüssel. Bisherige Nachrichten sind dann für dich nicht mehr lesbar, deine Gesprächspartner sehen eine geänderte Sicherheitsnummer.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    if (!reset) { bar.appendChild(btn(T('Schlüssel erzeugen'), 'btn-primary ct-setup-go', function () { setupKey(false); })); return; }
    // Neu beginnen: nur mit Passwort bzw. frischer Anmeldung (Schutz bei fremder Sitzung)
    var go = btn(T('Schlüssel erzeugen'), 'btn-primary ct-setup-go'); go.disabled = true; bar.appendChild(go);
    var st = status(box);
    api('GET', '/api/chat/me').then(function (me) {
      if (!me.reauth) { go.disabled = false; go.addEventListener('click', function () { setupKey(true); }); return; }
      if (me.reauth === 'login') {
        box.insertBefore(el('p', 'ct-reauth', T('Zur Sicherheit bitte zuerst neu anmelden. Danach hast du 5 Minuten Zeit, um neu zu beginnen.')), bar);
        go.remove();
        bar.appendChild(btn(T('Neu anmelden'), 'btn-primary ct-reauth-login', function () { location.href = '/login/sso?reauth=1&next=' + encodeURIComponent('/chats'); }));
        return;
      }
      // Bestaetigen mit Passwort, Code der Authenticator-App (6 Ziffern) oder Passkey (2.1)
      var m = me.reauth_methods || ['password'], PK = window.SagaPasskey;
      var lab = el('label', 'ct-reauth', m.indexOf('totp') >= 0 ? T('Zur Sicherheit: dein Passwort oder ein Code aus der Authenticator-App') : T('Zur Sicherheit: dein Passwort'));
      var pw = el('input', 'hk-in ct-reauth-pw'); pw.type = 'password'; pw.autocomplete = 'current-password'; pw.maxLength = 200;
      lab.appendChild(pw); box.insertBefore(lab, bar);
      go.disabled = false;
      if (m.indexOf('passkey') >= 0 && PK && PK.supported()) {
        bar.appendChild(btn('🔑 ' + T('Mit Passkey bestätigen'), 'ct-reauth-pk', function () {
          st.className = 'hk-status'; st.textContent = T('Warte auf den Passkey …');
          api('POST', '/api/chat/reauth/options').then(function (d) {
            var prob = PK.originProblem(d.origin, T('Diese Seite wurde über {here} geöffnet, Saga ist aber für {base} eingerichtet (BASE_URL). Passkeys gehen nur über {base}.'));
            if (prob) throw new Error(prob);
            return PK.get(d.options);
          }).then(function (cred) { return api('POST', '/api/chat/reauth', { passkey: cred }); })
            .then(function () { pw.value = ''; setupKey(true); })
            .catch(function (e) { st.className = 'hk-status err'; st.textContent = PK.cancelled(e) ? T('Abgebrochen.') : e.message; });
        }));
      }
      function check() {
        if (!pw.value) { pw.focus(); return; }
        busy(go, true, T('Wird geprüft …')); st.className = 'hk-status'; st.textContent = '';
        var v = pw.value.trim();
        api('POST', '/api/chat/reauth', m.indexOf('totp') >= 0 && /^\d{6}$/.test(v) ? { code: v } : { password: pw.value }).then(function () { pw.value = ''; setupKey(true); })
          .catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; busy(go, false, T('Schlüssel erzeugen')); pw.select(); });
      }
      go.addEventListener('click', check);
      pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); check(); } });
      pw.focus();
    }).catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; });
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
    // Sperren (2.3): Personen bzw. ganze Server anderer Saga-Instanzen - von dort kommen keine Nachrichtenanfragen mehr
    var bh = el('h3', null, T('Gesperrt (andere Saga-Server)')), bl = el('ul', 'ct-devs ct-blocks');
    api('GET', '/api/chat/blocks').then(function (r) {
      if (!(r.blocks || []).length) { bl.appendChild(el('li', 'hk-hint', T('Niemand gesperrt.'))); return; }
      r.blocks.forEach(function (x) {
        var li = el('li', 'ct-dev'), t = el('div', 'ct-dev-t');
        t.appendChild(el('b', null, x.kind === 'instance' ? x.host : x.label));
        t.appendChild(el('span', 'hk-hint', x.kind === 'instance' ? T('ganzer Server') : T('Person')));
        li.appendChild(t);
        li.appendChild(btn(T('Entsperren'), 'small', function () {
          api('DELETE', '/api/chat/blocks?target=' + q(x.target)).then(function () { HK.toast(T('Entsperrt.')); li.remove(); }).catch(function (e) { HK.toast(e.message); });
        }));
        bl.appendChild(li);
      });
    }).catch(function () { bh.hidden = true; });
    box.appendChild(bh); box.appendChild(bl);
    box.appendChild(el('h3', null, T('Sicherheit')));
    box.appendChild(el('p', 'hk-hint', T('Private Schlüssel verlassen dieses Gerät nie unverschlüsselt. Auf dem Server liegen nur öffentliche Schlüssel, verschlüsselte Nachrichten und dein mit dem Wiederherstellungsschlüssel verschlüsseltes Schlüssel-Bündel. Vergleiche Sicherheitsnummern, um sicherzugehen, dass niemand dazwischen sitzt.')));
  }

  // --- Bereit: Liste, Hash-Ziel, Live ----------------------------------------------------------------------------
  function ready() {
    loadAI();
    fedCerts().catch(function () {});                      // 2.3: Zertifikate v2 fuer Chats ueber Servergrenzen (still)
    S.ready = true;
    loadList().then(function () { route(); });
    checkLinks();
  }
  // --- Foederation 2.3 (saga-chat/v2): Handle-Zertifikat und Geraete-Zertifikate v2 signieren, fremde Schluessel mit TOFU ----
  // Nur dieses Geraet kennt ident_sk. Geraete-Zertifikate v2 bekommt jedes aktive Geraet, dessen v1-Zertifikat zum eigenen
  // Identitaetsschluessel passt (der Server kann so keine fremden Geraete unterschieben).
  function fedCerts() {
    if (!K || !K.ident_sk || !X) return Promise.resolve(null);
    var v;
    return api('GET', '/api/chat/v2/me').then(function (r) {
      v = r;
      if (v && v.handle) S.myHandle = v.handle;
      if (!v || !v.need || !v.handle) return null;
      return api('GET', '/api/chat/me?device=' + q(dev()));          // frischer Stand (auch gerade angemeldete Geraete)
    }).then(function (me) {
      if (!me) return v;
      var body = {};
      if (!v.cert_ok) body.handle_sig = b64(X.handleSig(v.handle, K.ident_sk, K.ident_pk, K.backup_pk));
      var devs = {};
      if (v.missing.indexOf(K.device.id) >= 0) devs[K.device.id] = b64(X.deviceSigV2(v.handle, K.ident_sk, K.device));   // dieses Geraet
      (me.devices || []).forEach(function (d) {
        if (d.revoked || d.id === K.device.id || v.missing.indexOf(d.id) < 0) return;
        if (!X.verifyDevice(ME, K.ident_pk, d)) return;            // nur Geraete mit gueltigem v1-Zertifikat
        devs[d.id] = b64(X.deviceSigV2(v.handle, K.ident_sk, { id: d.id, enc_pk: ub(d.enc_pub, 32), sign_pk: ub(d.sign_pub, 32) }));
      });
      if (Object.keys(devs).length) body.devices = devs;
      if (!body.handle_sig && !body.devices) return v;
      return api('PUT', '/api/chat/v2/cert', body);
    }).then(function (r) {
      // signierte Liste der aktiven Geraete (Review 2.3): neu, sobald Geraete dazukommen oder wegfallen
      if (!r || !r.handle || r.device_set_ok || !r.cert_ok || !(r.device_set_want || []).length) return r;
      var ver = ((r.device_set || {}).ver || 0) + 1;
      return api('PUT', '/api/chat/v2/cert', { device_set: X.deviceSetSig(r.handle, K.ident_sk, ver, r.device_set_want) });
    });
  }
  function trust2Key(h) { return 'trust2:' + ME + ':' + h; }
  // Schluessel zu foederierten Handles holen (ueber den eigenen Server), Zertifikate selbst pruefen, TOFU anwenden.
  // -> {handle: {keys: {...} | null, trust: 'trust'|'warn'|'block'|'reject'|'missing', safety?}}
  function fedKeys(handles, fresh) {
    return api('GET', '/api/chat/v2/keys?handles=' + q(handles.join(',')) + (fresh ? '&fresh=1' : '')).then(function (r) {
      var out = {};
      return Promise.all(handles.map(function (h) {
        var e = (r.keys || {})[h];
        if (!e) { out[h] = { keys: null, trust: 'missing' }; return null; }
        var k = X.checkKeysV2(h, e);
        if (!k) { out[h] = { keys: null, trust: 'reject' }; return null; }
        if (h === S.myHandle) {                            // eigene Schluessel: muessen zu diesem Geraet passen
          out[h] = { keys: k, trust: eqB(k.ident_pk, K.ident_pk) ? 'trust' : 'reject' };
          return null;
        }
        return kvGet(trust2Key(h)).catch(function () { return null; }).then(function (st) {
          var t = X.tofuV2(st || null, k.ident_pub, k.ver);
          out[h] = { keys: k, trust: t.result, stale: !!e.stale, stored: t.store || st || null };
          if (t.store) return kvSet(trust2Key(h), t.store).catch(function () {});
        });
      })).then(function () { return out; });
    });
  }
  // "Ueberprueft" (Sicherheitsnummer verglichen) bzw. Warnung bestaetigen ("Verstanden": verified false, Warnung weg)
  function fedVerify(h, ident_pub, verified) {
    return kvGet(trust2Key(h)).catch(function () { return null; }).then(function (st) {
      return kvSet(trust2Key(h), { ident: ident_pub, verified: !!verified, dver: (st && st.ident === ident_pub && st.dver) || 0 });
    });
  }
  window.SagaChatFed = { certs: fedCerts, keys: fedKeys, verify: fedVerify,
                         safety: function (myHandle, theirHandle, theirIdentPk) { return X.safetyNumberV2(myHandle, K.ident_pk, theirHandle, theirIdentPk); } };

  function route() {
    var h = decodeURIComponent(location.hash.slice(1) || ''), m;
    if ((m = /^c(\d+)$/.exec(h))) openChat(+m[1]);
    else if ((m = /^p(-?\d+)$/.exec(h))) create({ kind: 'page', note_id: +m[1] });      // negativ: Seite eines anderen Servers (2.3)
    else if ((m = /^u(.+)$/.exec(h))) create({ kind: 'dm', user: m[1] });
    else if (h === 'settings') settings();
  }
  window.addEventListener('hashchange', function () { if (S.ready) route(); });

  function isFed(c) { return !!(c && c.v === 2); }          // 2.3: Chat ueber Servergrenzen (saga-chat/v2, saga/fed_chat.py)
  function fileCid(c) { return isFed(c) ? c.ref : c.id; }   // AD fuer Anhaenge: v1 chat_id, v2 chat_ref
  function chatName(c) {
    if (c.kind === 'free') return c._title || c.members.filter(function (m) { return !m.me; }).map(function (m) { return m.name; }).join(', ') || T('Gruppen-Chat');
    return c.name || '';
  }
  function freeTitle(c) {
    if (c.kind !== 'free' || !c.title_msg) return;
    if (isFed(c)) { freeTitleV2(c); return; }
    try {
      var b = decrypt(c.id, c.title_msg, c.title_msg.device ? { [c.title_msg.sender_device]: c.title_msg.device } : {},
                      c.title_msg.ident_pub ? { [c.title_msg.sender.username]: c.title_msg.ident_pub } : {});
      if (b && b.verified && b.body && b.body.type === 'title') c._title = String(b.body.title || '').slice(0, 100);
    } catch (e) { /* Name bleibt die Mitgliederliste */ }
  }
  // Gruppe ueber Servergrenzen (2.3): Name als v2-Nachricht - Absender-Schluessel selbst pruefen (TOFU), dann anzeigen
  function freeTitleV2(c) {
    var m = c.title_msg;
    if (!m.sender_handle) return;
    fedKeys([m.sender_handle]).then(function (fk) {
      var f = fk[m.sender_handle], d = f && f.keys && f.trust !== 'reject' && f.keys.devices.filter(function (x) { return x.id === m.sender_device; })[0];
      if (!d || !X.verifyMessageV2(c.ref, m, ub(d.sign_pub, 32))) return;
      var key = m.key_for === 'device' ? X.openKey(m.key, K.device.enc_pk, K.device.enc_sk) : X.openKey(m.key, K.backup_pk, K.backup_sk);
      var b = X.decryptMessageV2(c.ref, m, key);
      if (b && b.type === 'title') {
        var t = String(b.title || '').slice(0, 100), x = S.chats.filter(function (y) { return y.id === c.id; })[0];
        c._title = t; if (x) x._title = t;
        if (S.cur && S.cur.id === c.id) { S.cur._title = t; drawHead(); }
        drawList();
      }
    }).catch(function () { /* Name bleibt die Mitgliederliste */ });
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
    // Nachrichtenanfragen (2.3, Personen anderer Saga-Server) oben, eigener Bereich - bis zur Annahme ohne Benachrichtigung
    var reqs = shown.filter(function (c) { return c.request; });
    if (reqs.length) {
      var rh = el('li', 'ct-req-h'); rh.id = 'ct-req-h';
      rh.appendChild(el('span', null, T('Nachrichtenanfragen ({n})', { n: reqs.length }))); ul.appendChild(rh);
      reqs.forEach(function (c) { ul.appendChild(listItem(c)); });
    }
    shown = shown.filter(function (c) { return !c.request; });
    // Archiv (2.1): archivierte Chats unten in einem aufklappbaren Bereich (beim Filtern gleich mit angezeigt)
    var act = shown.filter(function (c) { return !c.archived; }), arch = shown.filter(function (c) { return c.archived; });
    act.forEach(function (c) { ul.appendChild(listItem(c)); });
    if (!arch.length) return;
    var open = S.archOpen || !!f, nUn = arch.reduce(function (s, c) { return s + (c.muted ? 0 : c.unread || 0); }, 0);
    var hli = el('li', 'ct-arch-h'), tg = el('button', 'ct-arch-t'); tg.type = 'button'; tg.id = 'ct-arch-toggle';
    tg.setAttribute('aria-expanded', open ? 'true' : 'false');
    tg.appendChild(el('span', 'ct-arch-ic', open ? '▾' : '▸')); tg.appendChild(el('span', null, T('Archiviert ({n})', { n: arch.length })));
    if (nUn) { var nb = el('span', 'ct-badge ct-badge-q', String(nUn)); nb.setAttribute('aria-label', T('{n} ungelesen', { n: nUn })); tg.appendChild(nb); }
    tg.addEventListener('click', function () { S.archOpen = !S.archOpen; drawList(); var x = $('ct-arch-toggle'); if (x) x.focus(); });
    hli.appendChild(tg); ul.appendChild(hli);
    if (open) arch.forEach(function (c) { ul.appendChild(listItem(c)); });
  }
  function listItem(c) {
      var li = el('li', 'ct-item' + (S.cur && S.cur.id === c.id ? ' on' : '') + (c.unread ? ' unread' : '') + (c.archived ? ' ct-archived' : ''));
      var b = el('button', 'ct-item-b'); b.type = 'button'; b.dataset.id = c.id;
      var nm = chatName(c);
      b.appendChild(avatar(kindIc(c) || (nm.charAt(0) || '?').toUpperCase(), c.kind === 'dm' ? (c.peer || {}).username : 'c' + c.id, c.kind !== 'dm' ? 'ct-av-k' : '',
                           c.kind === 'dm' ? (c.peer || {}).avatar : null));
      var t = el('span', 'ct-item-t');
      t.appendChild(el('span', 'ct-item-n', nm));
      t.appendChild(el('span', 'ct-item-s', isFed(c) && c.kind === 'dm'
        ? (c.request ? T('Anfrage') : T('Direkt')) + ' · ' + ((c.peer || {}).host || '') + (c.readonly ? ' · ' + T('schreibgeschützt') : '') + (c.muted ? ' · 🔕' : '')
        : isFed(c)                                         // Gruppe/Seiten-Chat ueber Servergrenzen (2.3)
        ? (c.request ? T('Anfrage') : c.kind === 'page' ? T('Seite') : T('Gruppe')) + ' · ' + T('{n} Personen', { n: c.member_count })
          + (c.home_self ? '' : ' · ' + (c.home || '')) + (c.readonly ? ' · ' + T('schreibgeschützt') : '') + (c.muted ? ' · 🔕' : '')
        : (c.kind === 'dm' ? T('Direkt') : c.kind === 'group' ? T('Gruppe') : c.kind === 'page' ? T('Seite') : T('Gruppe')) +
          ' · ' + T('{n} Personen', { n: c.member_count }) + (c.muted ? ' · 🔕' : '')));
      b.appendChild(t);
      var side = el('span', 'ct-item-r');
      side.appendChild(el('span', 'ct-item-time', HK.relTime((c.last_at || c.created) - Date.now() / 1000)));
      if (c.unread) { var n = el('span', 'ct-badge', String(c.unread)); n.setAttribute('aria-label', T('{n} ungelesen', { n: c.unread })); side.appendChild(n); }
      b.appendChild(side);
      // Rechtsklick, Kontextmenue-Taste (Shift+F10) und Langdruck: Archivieren, Stummschalten, Loeschen (2.1)
      var lpT = null, lpAt = 0, touching = false;
      function lpRecent() { return lpAt && Date.now() - lpAt < 1200; }
      b.addEventListener('click', function (e) {
        if (lpRecent()) { e.preventDefault(); return; }                 // nach Langdruck kein Oeffnen
        if (location.hash === '#c' + c.id) openChat(c.id); else location.hash = 'c' + c.id;
      });
      b.setAttribute('aria-haspopup', 'menu');
      b.addEventListener('contextmenu', function (e) {
        e.preventDefault(); clearTimeout(lpT);
        if (lpRecent()) return;                                          // Langdruck hat das Menue schon geoeffnet
        if (touching) lpAt = Date.now();
        chatMenu(b, c);
      });
      b.addEventListener('touchstart', function () {
        touching = true; clearTimeout(lpT);
        lpT = setTimeout(function () { lpAt = Date.now(); chatMenu(b, c); }, 520);
      }, { passive: true });
      ['touchend', 'touchmove', 'touchcancel'].forEach(function (ev) {
        b.addEventListener(ev, function () { clearTimeout(lpT); if (ev !== 'touchmove') setTimeout(function () { touching = false; }, 0); }, { passive: true });
      });
      li.appendChild(b);
      return li;
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
  // name@server, @name@server oder E-Mail-artig: Hinweis statt "Keine Treffer" - Personen anderer Saga-Server findet die
  // Suche ueber ihren Anzeigenamen (Adressbuch der verbundenen Server, 2.3)
  function fedAddr(v) { return /^@?[^@\s]+@[^@\s]+[.:][^@\s]+$/.test(v); }
  function fedHint() {
    var li = el('li', 'hk-hint ct-fed-hint', T('Personen anderer, verbundener Saga-Server findest du über ihren Anzeigenamen oder name@server (nicht über die E-Mail-Adresse).') + ' ' +
      T('Personen auf diesem Server findest du über Namen oder Benutzernamen, nicht über die E-Mail-Adresse.') + ' ');
    var a = el('a', null, T('Anleitung: Föderation'));
    a.href = T('https://github.com/yggdrasil-sh/saga/blob/main/docs/ANLEITUNG.md#mit-anderen-saga-instanzen-teilen-föderation');
    a.target = '_blank'; a.rel = 'noopener';
    li.appendChild(a); return li;
  }
  function newChat() {
    var box = overlay(T('Neue Unterhaltung'));
    var inp = el('input', 'hk-in ct-people-q'); inp.type = 'search'; inp.placeholder = T('Person oder Gruppe suchen'); inp.setAttribute('aria-label', T('Person oder Gruppe suchen'));
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
        if (!v) { res.textContent = ''; return; }          // 1-2 Zeichen: nur genaue Treffer (Server), z. B. "hd"
        api('GET', '/api/chat/people?q=' + q(v)).then(function (r) {
          res.textContent = '';
          r.users.forEach(function (u) {
            var li = el('li'), b = el('button', 'ct-person'); b.type = 'button';
            b.appendChild(avatar((u.name || '?').charAt(0).toUpperCase(), u.username, '', u.avatar));
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
          (r.remote || []).forEach(function (u) {                // 2.3: Adressbuch verbundener Server -> Direkt-Chat ueber Servergrenzen
            var li = el('li'), b = el('button', 'ct-person ct-remote'); b.type = 'button';
            b.appendChild(avatar((u.name || '?').charAt(0).toUpperCase(), u.handle, '', u.avatar));
            var t = el('span', 'ct-item-t'); t.appendChild(el('span', 'ct-item-n', u.label || u.name));
            t.appendChild(el('span', 'ct-item-s', T('Person auf {host} · erste Nachricht als Anfrage', { host: u.host }))); b.appendChild(t);
            b.addEventListener('click', function () {
              if (!grp.hidden) {                                  // 2.3: Gruppe mit Personen anderer Server (Heim = dieser Server)
                if (!pick.some(function (p) { return p.handle === u.handle; })) pick.push({ username: u.handle, handle: u.handle, name: u.label || u.name, remote: true });
                drawChips(); inp.value = ''; res.textContent = ''; inp.focus(); return;
              }
              create({ kind: 'dm', handle: u.handle });
            });
            li.appendChild(b); res.appendChild(li);
          });
          if (!r.users.length && !r.groups.length && !(r.remote || []).length) res.appendChild(fedAddr(v) ? fedHint() : el('li', 'hk-hint', T('Keine Treffer.')));
        }).catch(function (e) { res.textContent = ''; res.appendChild(el('li', 'hk-status err', e.message)); });
      }, 250);
    });
    mk.addEventListener('click', function () {
      busy(mk, true);
      var title = name.value.trim();
      var loc = pick.filter(function (p) { return !p.remote; }).map(function (p) { return p.username; });
      var rem = pick.filter(function (p) { return p.remote; }).map(function (p) { return p.handle; });
      create(rem.length ? { kind: 'free', members: loc, handles: rem } : { kind: 'free', members: loc }).then(function (c) {
        if (c && title) return sendBody(c.id, { v: 1, type: 'title', title: title }, 'title').then(function () { loadList(); });
      }).finally(function () { busy(mk, false); });
    });
    inp.focus();
  }

  // --- Entschluesseln, pruefen, Vertrauen (TOFU) ----------------------------------------------------------------
  // v2 (Chats ueber Servergrenzen): Absender-Geraet aus den selbst geprueften Schluesseln (S.fk, TOFU) -> {body, verified}
  function decryptV2(c, m) {
    var key = m.key_for === 'device' ? X.openKey(m.key, K.device.enc_pk, K.device.enc_sk) : X.openKey(m.key, K.backup_pk, K.backup_sk);
    var body = X.decryptMessageV2(c.ref, m, key), f = (S.fk || {})[m.sender_handle], ok = false;
    if (f && f.keys && f.trust !== 'reject') {
      var d = f.keys.devices.filter(function (x) { return x.id === m.sender_device; })[0];
      ok = !!d && X.verifyMessageV2(c.ref, m, ub(d.sign_pub, 32));
    }
    return { body: body, verified: ok };
  }
  // Schluessel der Mitglieder eines foederierten Chats laden (ueber den eigenen Server, selbst gepruefte Zertifikate + TOFU)
  function fedLoad(c, fresh) {
    var hs = c.members.map(function (m) { return m.handle; }).filter(Boolean);
    return fedKeys(hs, fresh).then(function (fk) {
      if (S.cur && S.cur.id === c.id) { S.fk = fk; drawWarn(); drawHead(); }
      return fk;
    });
  }
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
  // Beim Oeffnen eines Chats die aktuellen Identitaetsschluessel der anderen holen und mit dem gemerkten Stand vergleichen:
  // "Sicherheitsnummer geaendert" erscheint sofort, nicht erst nach einer neuen Nachricht oder dem Oeffnen der
  // Sicherheitsnummer (BUG-016). Nur Konten mit eingerichteten Chats liefern einen Schluessel.
  function checkIdents(c) {
    if (isFed(c)) { fedLoad(c).catch(function () {}); return; }
    var users = c.members.filter(function (m) { return !m.me; }).map(function (m) { return m.username; }).slice(0, 60);
    if (!users.length) return;
    api('GET', '/api/chat/keys?users=' + users.map(q).join(',')).then(function (r) {
      if (!S.cur || S.cur.id !== c.id) return;
      Object.keys(r.users || {}).forEach(function (u) { var k = r.users[u]; if (k && k.ident_pub && users.indexOf(u) >= 0) noteIdent(u, k.ident_pub); });
    }).catch(function () { /* Pruefung beim Senden greift trotzdem */ });
  }
  function drawWarn() {
    var w = $('ct-warn'), c = S.cur; if (!c) { w.hidden = true; return; }
    if (isFed(c)) { drawWarnV2(c, w); return; }
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
  // 2.3: TOFU fuer Personen anderer Server - Warnung (Schluessel gewechselt), Blockade ("ueberprueft" und gewechselt),
  // ungueltig (Zertifikat/Geraete-Liste). Senden geht erst nach Pruefen bzw. "Verstanden" (Warnung) wieder.
  function drawWarnV2(c, w) {
    w.textContent = '';
    var n = 0;
    c.members.filter(function (m) { return !m.me && m.handle; }).forEach(function (m) {
      var f = (S.fk || {})[m.handle]; if (!f) return;
      var p = el('p', 'ct-warn-' + f.trust);
      if (f.trust === 'warn') {
        p.appendChild(document.createTextNode('⚠ ' + T('Die Sicherheitsnummer von {name} hat sich geändert – z. B. nach „Neu beginnen“ oder bei einem neuen Konto. Prüfe sie, bevor du Vertrauliches schreibst.', { name: m.name }) + ' '));
        p.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
        p.appendChild(btn(T('Verstanden'), 'small ct-warn-ok', function () {
          fedVerify(m.handle, f.keys.ident_pub, false).then(function () { return fedLoad(c); });
        }));
      } else if (f.trust === 'block') {
        p.appendChild(document.createTextNode('⛔ ' + T('Du hattest {name} als überprüft markiert – jetzt meldet der Server einen anderen Schlüssel. Senden ist gesperrt, bis du die Sicherheitsnummer erneut verglichen hast.', { name: m.name }) + ' '));
        p.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
      } else if (f.trust === 'reject') {
        p.appendChild(document.createTextNode('⛔ ' + T('Die Schlüssel von {name} sind ungültig (Zertifikat oder Geräte-Liste) – Senden ist gesperrt.', { name: m.name })));
      } else return;
      n++; w.appendChild(p);
    });
    w.hidden = !n;
  }
  function safetyV2(m) {
    var c = S.cur;
    fedKeys([m.handle]).then(function (fk) {
      var f = fk[m.handle], box = overlay(T('Sicherheitsnummer mit {name}', { name: m.name }));
      if (!f.keys) { box.appendChild(el('p', null, T('{name} hat Chats noch nicht eingerichtet.', { name: m.name }))); return; }
      var g = el('div', 'ct-safety'); g.id = 'ct-safety';
      X.safetyNumberV2(c.my_handle, K.ident_pk, m.handle, f.keys.ident_pk).split(' ').forEach(function (x) { g.appendChild(el('span', null, x)); });
      box.appendChild(g);
      box.appendChild(el('p', null, T('Vergleiche diese Nummer mit {name} – persönlich, per Anruf oder auf deren Gerät. Stimmt sie überein, liest niemand mit. Sie ist auf beiden Seiten gleich.', { name: m.name })));
      box.appendChild(el('p', 'hk-hint', T('{name} ist auf {host} – die Nummer gehört zu diesem Server-übergreifenden Chat.', { name: m.name, host: m.host || '' })));
      if (f.stored && f.stored.verified && f.stored.ident === f.keys.ident_pub && f.trust === 'trust') box.appendChild(el('p', 'ct-ok', '✓ ' + T('Als überprüft markiert.')));
      else box.appendChild(btn(T('Als überprüft markieren'), 'btn-primary ct-verify', function () {
        fedVerify(m.handle, f.keys.ident_pub, true).then(function () { closeOverlay(); HK.toast(T('Als überprüft markiert.')); return fedLoad(c); });
      }));
    }).catch(function (e) { HK.toast(e.message); });
  }
  function safety(m) {
    if (isFed(S.cur) && m.handle) { safetyV2(m); return; }
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
    S.cur = null; S.msgs = []; S.byId = {}; S.seenSeq = 0; S.rc = null; S.reply = null; S.files = [];
    $('ct-chat').hidden = true; $('ct-none').hidden = false; root.classList.remove('ct-open');
    drawList();
  }
  function openChat(cid) {
    if (!S.ready) return Promise.resolve();
    var same = S.cur && S.cur.id === cid;
    return api('GET', '/api/chats/' + cid + '?device=' + q(dev())).then(function (r) {
      var c = r.chat; freeTitle(c);
      if (!same) { S.msgs = []; S.byId = {}; S.seenSeq = 0; S.rc = null; S.reply = null; S.files = []; S.fk = null; drawReply(); drawFiles(); $('ct-input').value = ''; S.mentions = {}; }
      S.cur = c;
      $('ct-none').hidden = true; $('ct-chat').hidden = false; root.classList.add('ct-open');
      drawHead(); drawList(); drawWarn();
      c.members.forEach(function (m) { if (!m.me && !trust[m.username]) trustP[m.username] = kvGet(trustKey(m.username)).then(function (t) { if (t) trust[m.username] = t; drawWarn(); return t; }).catch(function () { return null; }); });
      checkIdents(c);
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
    var hav = $('ct-head-av'); if (hav) hav.remove();
    if (c.kind === 'dm') {                                // Direkt-Chat: Profilbild des Gegenuebers im Kopf
      var pp = others[0] || c.peer || {}, ht = document.querySelector('#ct-chat .ct-head-t');
      hav = avatar((chatName(c) || '?').charAt(0).toUpperCase(), pp.username, 'ct-av-h', pp.avatar || (c.peer || {}).avatar);
      hav.id = 'ct-head-av'; if (ht) ht.parentNode.insertBefore(hav, ht);
    }
    if (c.kind === 'dm' && isFed(c)) {                    // 2.3: Person eines anderen Servers - Herkunft, TOFU-Stand
      var fp = c.peer || {}, ff = (S.fk || {})[fp.handle];
      sub.appendChild(document.createTextNode(T('auf {host}', { host: fp.host || '' })));
      if (ff && ff.trust === 'trust' && ff.stored && ff.stored.verified && ff.stored.ident === ff.keys.ident_pub)
        sub.appendChild(el('span', 'ct-chip ct-verified', '✓ ' + T('überprüft')));
      else if (ff && (ff.trust === 'block' || ff.trust === 'reject')) sub.appendChild(el('span', 'ct-chip ct-blocked', '⛔ ' + T('gesperrt')));
      if (c.request) sub.appendChild(el('span', 'ct-chip', T('Anfrage')));
      if (c.readonly) sub.appendChild(el('span', 'ct-chip', '🔒 ' + T('schreibgeschützt')));
    } else if (c.kind === 'dm') {
      var p = others[0] || c.peer || {}, t = trust[p.username];
      sub.appendChild(document.createTextNode(p.username || ''));
      if (t && t.verified && !t.changed) sub.appendChild(el('span', 'ct-chip ct-verified', '✓ ' + T('überprüft')));
    } else {
      var mine = String(c.my_handle || '').split('@').slice(1).join('@');
      sub.appendChild(document.createTextNode(c.members.map(function (m) {
        return m.me ? T('du') : m.name + (isFed(c) && m.host && m.host !== mine ? ' (' + m.host + ')' : '');
      }).join(', ')));
      if (isFed(c) && c.request) sub.appendChild(el('span', 'ct-chip', T('Anfrage')));
      if (isFed(c) && c.readonly) sub.appendChild(el('span', 'ct-chip', '🔒 ' + T('schreibgeschützt')));
    }
    if (c.retention_s) sub.appendChild(el('span', 'ct-chip', '⏱ ' + (RET.filter(function (r) { return r[0] === c.retention_s; })[0] || [0, ''])[1]));
    if (c.muted) sub.appendChild(el('span', 'ct-chip', '🔕'));
    if (c.archived) sub.appendChild(el('span', 'ct-chip ct-chip-arch', '🗄 ' + T('Archiviert')));
    if (c.kind === 'page' && c.note_id) {
      var a = el('a', 'ct-chip ct-pagelink', T('Seite öffnen')); a.href = '/notes#' + c.note_id; a.target = 'hk-notes'; sub.appendChild(a);
    }
    document.title = chatName(c) + ' · ' + T('Chats');
    if (window.HKCalls) window.HKCalls.head(c);           // Anruf-Knoepfe (static/calls.js) nur im Direkt-Chat
    drawAI();
    drawState(c);
  }
  // Nachrichtenanfrage (Annehmen/Ablehnen/Sperren) bzw. schreibgeschuetzt: statt des Eingabefelds (2.3)
  function drawState(c) {
    var box = $('ct-state');
    if (!box) { box = el('div', 'ct-state'); box.id = 'ct-state'; box.setAttribute('role', 'region'); $('ct-compose').parentNode.insertBefore(box, $('ct-compose')); }
    box.textContent = '';
    var req = isFed(c) && c.request, ro = isFed(c) && c.readonly && !req;
    box.hidden = !req && !ro; $('ct-compose').hidden = req || ro;
    if ($('ct-call-a') && (req || ro)) { $('ct-call-a').hidden = true; $('ct-call-v').hidden = true; }
    if (ro) { box.appendChild(el('p', 'hk-hint', '🔒 ' + (c.readonly_reason || T('Diese Unterhaltung ist schreibgeschützt.')))); return; }
    if (!req) return;
    var p = c.kind === 'dm' ? (c.peer || {}) : (c.invited_by || {});
    box.appendChild(el('p', null, c.kind === 'dm'
      ? T('{name} ({host}) möchte dir schreiben. Bis du annimmst, bekommst du keine Benachrichtigungen und keine Anrufe – und {name} erfährt nicht, ob du die Nachrichten gelesen hast.', { name: p.name || '?', host: p.host || '?' })
      : T('{name} ({host}) hat dich in diese Gruppe eingeladen. Bis du annimmst, bekommst du keine Benachrichtigungen.', { name: p.name || '?', host: p.host || c.home || '?' })));
    var bar = el('div', 'hk-actions');
    function act(a, scope) {
      return api('POST', '/api/chats/' + c.id + '/request', { action: a, scope: scope || 'person' }).then(function (r) {
        if (a === 'accept') { HK.toast(T('Anfrage angenommen.')); return openChat(c.id).then(loadList); }
        HK.toast(a === 'block' ? T('Gesperrt – von dort kommen keine Anfragen mehr.') : T('Anfrage abgelehnt.'));
        closeChat(); history.replaceState(null, '', location.pathname); return loadList();
      }).catch(function (e) { HK.toast(e.message); });
    }
    bar.appendChild(btn(T('Anfrage annehmen'), 'btn-primary ct-req-accept', function () { act('accept'); }));
    bar.appendChild(btn(T('Ablehnen'), 'ct-req-decline', function () { act('decline'); }));
    var blk = btn(T('Sperren …'), 'btn-danger ct-req-block');
    blk.addEventListener('click', function () {
      HK.openMenu(blk, function (menu) {
        menu.textContent = '';
        menu.appendChild(HK.menuItem(T('{name} sperren', { name: p.name || '?' }), function () { HK.closeMenu(); act('block', 'person'); }, { icon: '⛔', danger: true }));
        menu.appendChild(HK.menuItem(T('Ganzen Server {host} sperren', { host: p.host || '?' }), function () { HK.closeMenu(); act('block', 'instance'); }, { icon: '⛔', danger: true }));
      });
    });
    if (c.kind !== 'dm' && p.remote === false) blk.hidden = true;     // eingeladen von einer Person dieses Servers: nur ablehnen
    bar.appendChild(blk); box.appendChild(bar);
  }
  function loadMsgs(older) {
    var c = S.cur; if (!c || S.loading) return Promise.resolve();
    S.loading = true;
    var first = S.msgs.length ? S.msgs[0].seq : 0, last = S.msgs.length ? S.msgs[S.msgs.length - 1].seq : 0;
    var url = '/api/chats/' + c.id + '/messages?calls=1&device=' + q(dev()) + (older && first ? '&before=' + first : (!older && last ? '&after=' + last : ''));
    // v2: Schluessel der Absender (selbst geprueft) vor dem Entschluesseln - sonst stuende kurz "Echtheit nicht bestaetigt"
    return (isFed(c) && !S.fk ? fedLoad(c).catch(function () { return null; }) : Promise.resolve()).then(function () { return api('GET', url); }).then(function (r) {
      S.loading = false;
      if (!S.cur || S.cur.id !== c.id) return;
      var add = r.messages.filter(function (m) { return !S.byId[m.id]; }).map(function (m) {
        var d;
        if (m.kind === 'call') {                          // Anruf-Eintrag: Metadaten vom Server (nicht verschluesselt, nur wer/wann/wie lange)
          try { m.call = JSON.parse(m.body_ct); } catch (e) { m.call = {}; }
          if (isFed(c) && m.sender && m.sender.username === ME) m.call.from = ME;     // v2: Anrufer steht als Prinzipal/Konto drin
          m.body = null; m.verified = true; S.byId[m.id] = m; return m;
        }
        if (m.kind === 'sys') {                           // Systemzeile (2.3, z. B. KI ein-/ausgeschaltet) - Metadaten vom Heim
          try { m.sys = JSON.parse(m.body_ct); } catch (e) { m.sys = {}; }
          m.body = null; m.verified = true; S.byId[m.id] = m; return m;
        }
        // v2-Chat: Nachrichten ohne sender_handle stammen aus der Zeit vor der Umstellung (Seiten-Chat, 2.3) - v1 mit chat_id
        try { d = isFed(c) && m.sender_handle ? decryptV2(c, m) : decrypt(c.id, m, r.devices || {}, r.idents || {}); } catch (e) { d = { body: null, verified: false, error: true }; }
        m.body = d.body; m.verified = d.verified; m.error = !!d.error; m._ai = aiCheck(m);
        S.byId[m.id] = m; return m;
      });
      if (older) { S.msgs = add.concat(S.msgs); S.more = r.more; }
      else { S.msgs = S.msgs.concat(add); if (!last) S.more = r.more; if (!last || !r.more) S.seenSeq = Math.max(S.seenSeq || 0, r.last_seq || 0); }
      drawMsgs(older ? 'keep' : 'bottom');
      resolveNotes();
      markRead();
      if (!older && r.more && last) return loadMsgs(false);
    }).catch(function (e) { S.loading = false; HK.toast(T('Nachrichten nicht geladen: {err}', { err: e.message })); });
  }
  // Gelesen bis zur neuesten geladenen Nachricht - oder bis zum Stand der Unterhaltung beim Laden (S.seenSeq): auch
  // Nachrichten, die dieses Geraet nie lesen kann (vor der Einrichtung geschickt), zaehlen sonst ewig als ungelesen (BUG-050)
  function markRead() {
    var c = S.cur; if (!c || document.hidden) return;
    var seq = Math.max(S.msgs.length ? S.msgs[S.msgs.length - 1].seq : 0, S.seenSeq || 0);
    if (!seq || seq <= (c.last_read || 0)) return;
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
  function callText(m) {
    var k = m.call || {}, out = k.from === ME, v = k.media === 'video';
    var what = v ? T('Videoanruf') : T('Anruf');
    if (k.status === 'ended') {
      var sec = Math.max(0, k.dur | 0), h = Math.floor(sec / 3600), mi = Math.floor(sec % 3600 / 60), p2 = function (x) { return (x < 10 ? '0' : '') + x; };
      return what + ' · ' + (h ? h + ':' + p2(mi) : mi) + ':' + p2(sec % 60);
    }
    if (k.status === 'declined') return T('{what} abgelehnt', { what: what });
    if (k.status === 'busy') return out ? T('Besetzt') : T('Verpasster Anruf (du hast telefoniert)');
    if (k.status === 'failed') return T('{what} – keine Verbindung', { what: what });
    return out ? T('{what} – nicht angenommen', { what: what }) : (v ? T('Verpasster Videoanruf') : T('Verpasster Anruf'));
  }
  function preview(m) {
    if (!m) return T('Nachricht nicht mehr da');
    if (m.kind === 'call') return '📞 ' + callText(m);
    if (m.error || !m.body) return T('Nicht entschlüsselbar');
    var t = String(m.body.text || '').replace(/\[\[note:\d+\]\]/g, '📄').replace(/\s+/g, ' ').trim();
    if (!t && (m.body.files || []).length) t = '📎 ' + m.body.files.map(function (f) { return f.n; }).join(', ');
    return t.length > 90 ? t.slice(0, 90) + '…' : t;
  }
  function drawMsgs(scroll) {
    var box = $('ct-msgs'), c = S.cur, atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
    var prevH = box.scrollHeight, prevTop = box.scrollTop;
    lazyReset();
    box.textContent = '';
    if (S.more) {
      var mo = btn(T('Ältere Nachrichten laden'), 'small ct-older', function () { loadMsgs(true); });
      box.appendChild(mo);
    } else {
      box.appendChild(el('p', 'ct-start', '🔒 ' + T('Nachrichten in diesem Chat sind Ende-zu-Ende-verschlüsselt.') +
        (c.kind !== 'dm' ? ' ' + T('Wer neu dazukommt, sieht nur Nachrichten ab dem Beitritt.') : '')));
      if (c.cleared_seq) box.appendChild(el('p', 'ct-start ct-cleared', T('Der bisherige Verlauf wurde gelöscht.')));     // (2.1)
      else if (!S.msgs.length && S.seenSeq > 0) box.appendChild(el('p', 'ct-start', T('Frühere Nachrichten sind auf diesem Gerät nicht lesbar – z. B. vor seiner Einrichtung geschickt oder abgelaufen.')));
    }
    var lastDay = '', lastWho = null;
    S.msgs.forEach(function (m) {
      var d = new Date(m.created * 1000), day = fDay.format(d);
      if (day !== lastDay) { box.appendChild(el('div', 'ct-day', day)); lastDay = day; lastWho = null; }
      var isAI = m._ai === 'ok', mine = !isAI && m.sender && m.sender.username === ME;
      if (m.kind === 'call') { box.appendChild(callRow(m, d, mine)); lastWho = null; return; }
      if (m.kind === 'sys') { box.appendChild(sysRow(m, d)); lastWho = null; return; }
      var row = el('div', 'ct-msg' + (mine ? ' mine' : '') + (isAI ? ' ct-ai' : '')); row.dataset.id = m.id;
      if (isAI) {                                         // KI-Antwort (2.1): Bild + Name der KI, wer gefragt hat
        if (lastWho !== '#ai:' + (m.sender || {}).username) box.appendChild(aiWho(m));
      } else if (!mine && c.kind !== 'dm' && lastWho !== (m.sender || {}).username) {   // Absender in Gruppen: kleines Profilbild + Name
        var who = el('div', 'ct-who'), sn = m.sender ? m.sender.name : T('Gelöschtes Konto');
        who.appendChild(avatar((sn || '?').charAt(0).toUpperCase(), (m.sender || {}).username || '', 'ct-av-s', m.sender && m.sender.avatar));
        who.appendChild(el('span', null, sn)); box.appendChild(who);
      }
      lastWho = (isAI ? '#ai:' : '') + (m.sender || {}).username;
      var b = el('div', 'ct-bubble');
      if (m._ai === 'bad') {                              // gibt sich als KI-Antwort aus, Beleg passt nicht
        var ub2 = el('div', 'ct-unv', '⚠ ' + T('Als KI-Antwort ausgegeben, aber nicht bestätigt'));
        ub2.setAttribute('role', 'note'); b.appendChild(ub2);
      }
      if (!m.verified && !m.error) {                      // Signatur/Geraete-Schluessel passen nicht: deutlich kennzeichnen
        row.classList.add('ct-unverified');
        var uv = el('div', 'ct-unv', '⚠ ' + T('Echtheit nicht bestätigt'));
        uv.title = T('Absender nicht bestätigt: Signatur oder Geräte-Schlüssel passen nicht (z. B. nach „Neu beginnen“).');
        uv.setAttribute('role', 'note'); b.appendChild(uv);
      }
      if (m.reply_to) {
        var o = S.byId[m.reply_to], qd = el('button', 'ct-quote'); qd.type = 'button';
        qd.appendChild(el('b', null, o && o._ai === 'ok' ? aiP().name : o && o.sender ? o.sender.name : '…')); qd.appendChild(el('span', null, preview(o)));
        qd.addEventListener('click', function () { var t = box.querySelector('.ct-msg[data-id="' + m.reply_to + '"]'); if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('flash'); setTimeout(function () { t.classList.remove('flash'); }, 1200); } });
        b.appendChild(qd);
      }
      if (m.error || !m.body) b.appendChild(el('div', 'ct-text ct-err', '🔒 ' + T('Diese Nachricht lässt sich auf diesem Gerät nicht entschlüsseln.')));
      else {
        if (m.body.text) { var tx = el('div', 'ct-text'); renderText(tx, m.body.text); b.appendChild(tx); }
        (Array.isArray(m.body.files) ? m.body.files : []).slice(0, 10).forEach(function (f) { b.appendChild(fileEl(fileCid(c), f)); });
      }
      var meta = el('div', 'ct-meta');
      if (m.fstate === 'pending') { var pe = el('span', 'ct-pend', '⏳'); pe.title = T('Wird zugestellt, sobald der andere Server erreichbar ist.'); meta.appendChild(pe); }
      if (m.fstate === 'failed') { row.classList.add('ct-failed'); meta.appendChild(el('span', 'ct-fail', '⚠ ' + T('nicht zugestellt'))); }
      if (m.expires_at) meta.appendChild(el('span', 'ct-exp', '⏱'));
      var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); tm.title = fDay.format(d) + ' ' + fTime.format(d); meta.appendChild(tm);
      b.appendChild(meta);
      row.appendChild(b);
      var act = el('button', 'icon-btn ct-act', '⋯'); act.type = 'button'; act.setAttribute('aria-label', T('Nachricht …')); act.setAttribute('aria-haspopup', 'menu');
      act.addEventListener('click', function () { msgMenu(act, m); });
      row.appendChild(act);
      box.appendChild(row);
    });
    if (S.aiPending && S.aiPending.cid === c.id) box.appendChild(S.aiPending.el);   // wartende KI-Antwort bleibt unten
    applySearch();
    if (scroll === 'keep') box.scrollTop = box.scrollHeight - prevH + prevTop;
    else if (scroll === 'bottom' || atBottom) box.scrollTop = box.scrollHeight;
  }
  // Systemzeile (2.3): mittig, z. B. "Anna hat die KI in diesem Chat eingeschaltet"
  // Person in Systemzeilen: Name mit Herkunft (Server), ich selbst als "du"
  function sysWho(x, acc) {
    if (!x) return '';
    if (acc && x.handle && x.handle === (S.cur || {}).my_handle) return T('dich');      // "… hat dich hinzugefuegt"
    var mine = String((S.cur || {}).my_handle || '').split('@').slice(1).join('@');     // eigener Server: ohne Zusatz
    return (x.name || '?') + (x.host && x.host !== mine ? ' (' + x.host + ')' : '');
  }
  function sysText(k) {
    if (k.type === 'ai') {
      var who = k.by === (S.cur || {}).my_handle ? T('Du') : (k.name || '?');
      return k.on ? T('{name} hat die KI in diesem Chat eingeschaltet.', { name: who }) : T('{name} hat die KI in diesem Chat ausgeschaltet.', { name: who });
    }
    if (k.type === 'fed') return T('Ab jetzt mit Personen anderer Saga-Server (Ende-zu-Ende verschlüsselt).') + (k.ai_off ? ' ' + T('Die KI ist hier jetzt aus.') : '');
    if (k.type === 'members') {
      var acc = function (x) { return sysWho(x, true); }, nom = function (x) { return sysWho(x, false); };
      var meBy = !!(k.by && k.by.handle && k.by.handle === (S.cur || {}).my_handle), by = k.by && !meBy ? nom(k.by) : '';
      var add = (k.added || []).map(k.by ? acc : nom).join(', '), rm = (k.removed || []).map(k.by ? acc : nom).join(', ');
      var out = [];
      if (add) out.push(meBy ? T('Du hast {names} hinzugefügt.', { names: add }) : by ? T('{by} hat {names} hinzugefügt.', { by: by, names: add })
                             : T('{names} ist jetzt dabei.', { names: add }));
      if (rm) {
        var left = k.by && (k.removed || []).length === 1 && (k.removed[0] || {}).handle === k.by.handle;
        if (k.reason === 'disconnected') out.push(T('{names} ist nicht mehr dabei – die Verbindung zu diesem Saga-Server wurde getrennt.', { names: (k.removed || []).map(nom).join(', ') }));
        else if (k.reason === 'gone') out.push(T('{names} ist nicht mehr dabei (Konto gelöscht).', { names: (k.removed || []).map(nom).join(', ') }));
        else if (left) out.push(meBy ? T('Du hast die Gruppe verlassen.') : T('{names} hat die Gruppe verlassen.', { names: nom(k.removed[0]) }));
        else out.push(meBy ? T('Du hast {names} entfernt.', { names: rm }) : by ? T('{by} hat {names} entfernt.', { by: by, names: rm })
                           : T('{names} ist nicht mehr dabei.', { names: (k.removed || []).map(nom).join(', ') }));
      }
      return out.join(' ') || T('Hinweis');
    }
    return T('Hinweis');
  }
  function sysRow(m, d) {
    var k = m.sys || {}, row = el('div', 'ct-call ct-sys'); row.dataset.id = m.id;
    row.appendChild(el('span', null, sysText(k)));
    var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); row.appendChild(tm);
    return row;
  }
  // Anruf-Eintrag (2.1): mittig, mit Richtung, Ergebnis, Uhrzeit und "Zurueckrufen"
  function callRow(m, d, mine) {
    var k = m.call || {}, missed = !mine && (k.status === 'missed' || k.status === 'busy');
    var row = el('div', 'ct-call' + (missed ? ' missed' : '')); row.dataset.id = m.id;
    row.appendChild(el('span', 'ct-call-ic', (k.media === 'video' ? '🎥' : '📞') + (mine ? '↗' : '↙')));
    row.appendChild(el('span', null, callText(m)));
    var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); row.appendChild(tm);
    if (missed && S.cur && S.cur.kind === 'dm' && window.HKCalls && !$('ct-call-a').hidden)
      row.appendChild(btn(T('Zurückrufen'), 'small ct-call-back', function () { window.HKCalls.start(S.cur, k.media === 'video'); }));
    return row;
  }
  function msgMenu(anchor, m) {
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      if (m.body) menu.appendChild(HK.menuItem(T('Antworten'), function () { HK.closeMenu(); S.reply = m; drawReply(); $('ct-input').focus(); }, { icon: '↩' }));
      if (m.body && m.body.text) menu.appendChild(HK.menuItem(T('Text kopieren'), function () {
        HK.closeMenu(); if (navigator.clipboard) navigator.clipboard.writeText(m.body.text).then(function () { HK.toast(T('Kopiert.')); });
      }, { icon: '⧉' }));
      if (m.fstate === 'failed' && m.body && m.sender && m.sender.username === ME) menu.appendChild(HK.menuItem(T('Erneut senden'), function () {
        HK.closeMenu();
        sendBody(S.cur.id, m.body, 'msg', m.reply_to).then(function () {
          return api('DELETE', '/api/chats/' + S.cur.id + '/messages/' + m.id).catch(function () {});
        }).then(function () { dropMsg(m.id); return loadMsgs(false); }).catch(function (e) { HK.toast(T('Nicht gesendet: {err}', { err: e.message })); });
      }, { icon: '↻' }));
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
  // 2.1 (Chat-Bilder schnell): Im Verlauf nur Vorschaubilder (files[].th, 320 px JPEG, eigener Schluessel), Platzhalter
  // in richtiger Groesse aus w/h; Original erst beim Oeffnen. Laden nur fuer sichtbare Nachrichten (IntersectionObserver,
  // hoechstens 4 gleichzeitig, Abbruch beim Wegscrollen). Entschluesseln und Vorschaubild-Erzeugung im Worker
  // (static/chat-worker.js; ohne Worker im Fenster). Caches: entschluesselte Vorschaubilder als Blob-URL im Speicher (LRU),
  // Ciphertext in IndexedDB "saga-chat-blobs-<konto>" (200 MB, LRU; Schluessel nur in der verschluesselten Nachricht),
  // geleert bei "Abmelden", "Neu beginnen"/Geraet entfernt. Alte Nachrichten ohne th: Original laden, selbst ein
  // Vorschaubild erzeugen und (mit dem Dateischluessel verschluesselt) als "lt:<id>" cachen. Format: tests/chat_vectors.json "image".
  var IMG = /^image\/(png|jpeg|gif|webp)$/, THUMB_BOX = 280, MEM_MAX = 300, BLOB_MAX = 200 * 1048576, PAR = 4;

  // Worker: {n, op, ...} -> {n, ok, ...}; faellt bei fehlendem Worker/OffscreenCanvas auf das Fenster zurueck
  var W = null, wN = 0, wCb = {};
  function worker() {
    if (W === false || typeof Worker === 'undefined') return null;
    if (!W) {
      try {
        W = new Worker('/static/chat-worker.js?v=' + encodeURIComponent(document.body.dataset.v || ''));
        W.onmessage = function (e) { var d = e.data || {}, cb = wCb[d.n]; if (!cb) return; delete wCb[d.n]; if (d.ok) cb[0](d); else cb[1](new Error(d.err)); };
        W.onerror = function (e) {
          if (e && e.preventDefault) e.preventDefault();
          var all = wCb; wCb = {}; W = false;
          Object.keys(all).forEach(function (k) { all[k][1](new Error('worker')); });
        };
      } catch (e) { W = false; return null; }
    }
    return W;
  }
  function run(op, d, tr) {
    var w = worker();
    var p = w ? new Promise(function (res, rej) { var n = ++wN; wCb[n] = [res, rej]; d.op = op; d.n = n; w.postMessage(d, tr || []); })
      : Promise.reject(new Error('worker'));
    return p.catch(function (e) {
      if (e.message !== 'worker' && e.message !== 'no-canvas') throw e;
      d.op = op; return mainJob(d);
    });
  }
  function mainJob(d) {
    return Promise.resolve().then(function () {
      var v2 = typeof d.cid === 'string', dec = v2 ? X.decryptFileV2 : X.decryptFile, enc = v2 ? X.encryptFileV2 : X.encryptFile;   // v2: chat_ref
      if (d.op === 'dec') return { plain: dec(d.cid, new Uint8Array(d.blob), d.key).buffer };
      if (d.op === 'enc') { var r = enc(d.cid, new Uint8Array(d.bytes)); return { blob: r.blob.buffer, key: r.key }; }
      var bytes = d.op === 'thumb' ? dec(d.cid, new Uint8Array(d.blob), d.key) : new Uint8Array(d.bytes);
      return mainThumb(bytes, d.mime).then(function (t) {
        if (d.op === 'thumb' && d.enc) t.ct = enc(d.cid, new Uint8Array(t.jpeg), { key: ub(d.key, 32) }).blob.buffer;
        return t;
      });
    });
  }
  function mainThumb(bytes, mime) {
    return createImageBitmap(new Blob([bytes], { type: mime || 'image/jpeg' })).then(function (bm) {
      var w = bm.width, h = bm.height, f = Math.min(1, 320 / Math.max(w, h, 1));
      var cv = document.createElement('canvas'); cv.width = Math.max(1, Math.round(w * f)); cv.height = Math.max(1, Math.round(h * f));
      var g = cv.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, cv.width, cv.height); g.drawImage(bm, 0, 0, cv.width, cv.height);
      if (bm.close) bm.close();
      return new Promise(function (res, rej) { cv.toBlob(function (b) { if (b) res(b); else rej(new Error('jpeg')); }, 'image/jpeg', 0.7); })
        .then(function (b) { return b.arrayBuffer(); })
        .then(function (jpeg) { return { jpeg: jpeg, w: w, h: h, tw: cv.width, th: cv.height }; });
    });
  }

  // Platten-Cache: nur Ciphertext (Blob), LRU nach letztem Zugriff
  var bdbP = null, bUsed = -1;
  function bdbName() { return 'saga-chat-blobs-' + ME; }
  function bdb() {
    if (!bdbP) bdbP = new Promise(function (res, rej) {
      var r = indexedDB.open(bdbName(), 1);
      r.onupgradeneeded = function () { var db = r.result; db.createObjectStore('data'); db.createObjectStore('meta', { keyPath: 'id' }).createIndex('at', 'at'); };
      r.onsuccess = function () { var db = r.result; db.onversionchange = function () { db.close(); bdbP = null; bUsed = -1; }; res(db); };
      r.onerror = function () { rej(r.error); };
      r.onblocked = function () { rej(new Error('blocked')); };
    });
    bdbP.catch(function () { bdbP = null; });
    return bdbP;
  }
  function btx(stores, mode, fn) {
    return bdb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(stores, mode), out = fn(tx);
        tx.oncomplete = function () { res(out && 'result' in out ? out.result : out); };
        tx.onerror = tx.onabort = function () { rej(tx.error); };
      });
    });
  }
  function bGet(id) {
    return btx(['data', 'meta'], 'readwrite', function (tx) {
      var m = tx.objectStore('meta'), g = m.get(id);
      g.onsuccess = function () { var x = g.result; if (x && Date.now() - x.at > 600000) { x.at = Date.now(); m.put(x); } };
      return tx.objectStore('data').get(id);
    }).then(function (b) { return b ? (b.arrayBuffer ? b.arrayBuffer() : b) : null; }).catch(function () { return null; });
  }
  function bPut(id, blob) {                            // blob: Blob (Kopie ist beim Aufruf schon gemacht)
    if (!blob || blob.size > BLOB_MAX / 10) return Promise.resolve();
    return (bUsed < 0 ? btx('meta', 'readonly', function (tx) {
      var n = { result: 0 }, c = tx.objectStore('meta').openCursor();
      c.onsuccess = function () { var x = c.result; if (x) { n.result += x.value.size || 0; x.continue(); } };
      return n;
    }).then(function (n) { bUsed = n; }) : Promise.resolve()).then(function () {
      return btx(['data', 'meta'], 'readwrite', function (tx) {
        tx.objectStore('data').put(blob, id); tx.objectStore('meta').put({ id: id, size: blob.size, at: Date.now() });
      });
    }).then(function () { bUsed += blob.size; if (bUsed > BLOB_MAX) return bEvict(); }).catch(function () {});
  }
  function bEvict() {
    return btx(['data', 'meta'], 'readwrite', function (tx) {
      var c = tx.objectStore('meta').index('at').openCursor(), d = tx.objectStore('data');
      c.onsuccess = function () {
        var x = c.result; if (!x || bUsed <= BLOB_MAX * 0.8) return;
        bUsed -= x.value.size || 0; d.delete(x.value.id); x.delete(); x.continue();
      };
    });
  }
  function bClear() {
    mem.forEach(function (u) { URL.revokeObjectURL(u); }); mem.clear();
    var p = bdbP; bdbP = null; bUsed = -1;
    return (p ? p.then(function (db) { db.close(); }, function () {}) : Promise.resolve()).then(function () {
      try { indexedDB.deleteDatabase(bdbName()); } catch (e) { /* egal */ }
    });
  }
  // Speicher-Cache: Blob-URLs entschluesselter Vorschaubilder (LRU)
  var mem = new Map();
  function memGet(k) { var u = mem.get(k); if (u) { mem.delete(k); mem.set(k, u); } return u; }
  function memPut(k, u) {
    var old = mem.get(k); if (old && old !== u) URL.revokeObjectURL(old);
    mem.delete(k); mem.set(k, u);
    while (mem.size > MEM_MAX) { var f = mem.keys().next().value; URL.revokeObjectURL(mem.get(f)); mem.delete(f); }
  }

  // Ciphertext holen: Platten-Cache, sonst Server (immutable, private) und in den Platten-Cache
  function getBlob(f, signal, keep) {
    return bGet(f.id).then(function (buf) {
      if (buf) return buf;
      return fetch('/api/chat/files/' + encodeURIComponent(f.id), { credentials: 'same-origin', headers: { 'X-Saga': '1' }, signal: signal })
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
        .then(function (b) { if (keep !== false) bPut(f.id, new Blob([b])); return b; });
    });
  }
  var inflight = {};
  function thumbUrl(cid, f, signal) {
    var key = 't:' + f.id, u = memGet(key);
    if (u) return Promise.resolve(u);
    if (inflight[key]) return inflight[key];
    var jpeg = function (buf) { return new Blob([buf], { type: 'image/jpeg' }); }, p;
    if (f.th && f.th.id && f.th.k) {
      p = getBlob(f.th, signal).then(function (buf) { return run('dec', { cid: cid, blob: buf, key: f.th.k }, [buf]); })
        .then(function (r) { return jpeg(r.plain); });
    } else {
      p = bGet('lt:' + f.id).then(function (ct) {
        if (ct) return run('dec', { cid: cid, blob: ct, key: f.k }, [ct]).then(function (r) { return jpeg(r.plain); });
        return getBlob(f, signal, false).then(function (buf) { return run('thumb', { cid: cid, blob: buf, key: f.k, mime: f.m, enc: true }); })
          .then(function (t) { if (t.ct) bPut('lt:' + f.id, new Blob([t.ct])); return jpeg(t.jpeg); });
      });
    }
    p = p.then(function (b) { var url = URL.createObjectURL(b); memPut(key, url); return url; });
    inflight[key] = p;
    p.then(function () { delete inflight[key]; }, function () { delete inflight[key]; });
    return p;
  }
  function plainUrl(cid, f) {                          // Original (Oeffnen/Speichern): nie text/html o. ae. als Blob-URL
    return getBlob(f).then(function (buf) { return run('dec', { cid: cid, blob: buf, key: f.k }, [buf]); }).then(function (r) {
      return URL.createObjectURL(new Blob([r.plain], { type: IMG.test(f.m) ? f.m : 'application/octet-stream' }));
    });
  }

  // Sichtbarkeit: nur sichtbare (plus Vorlauf) Bilder laden, hoechstens PAR gleichzeitig, neueste zuerst
  var queue = [], active = 0, io = null, dims = {};
  function lazyIO() {
    if (!io && window.IntersectionObserver) io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        var j = e.target._job; if (!j || j.done) return;
        j.vis = e.isIntersecting;
        if (j.vis && !j.queued && !j.running) { j.queued = true; queue.push(j); }
        else if (!j.vis && j.queued) { j.queued = false; queue.splice(queue.indexOf(j), 1); }
        else if (!j.vis && j.running && j.ac) j.ac.abort();
      });
      pump();
    }, { root: $('ct-msgs'), rootMargin: '600px 0px' });
    return io;
  }
  function lazy(node, fn, fail) {
    var j = { el: node, fn: fn, fail: fail, vis: !window.IntersectionObserver };
    node._job = j;
    if (lazyIO()) io.observe(node); else { j.queued = true; queue.push(j); pump(); }
  }
  function lazyReset() { if (io) io.disconnect(); queue = []; }
  function pump() {
    while (active < PAR && queue.length) {
      var j = queue.pop(); j.queued = false;
      if (!j.el.isConnected || j.done) continue;
      startJob(j);
    }
  }
  function startJob(j) {
    j.running = true; active++;
    j.ac = window.AbortController ? new AbortController() : null;
    j.fn(j.ac && j.ac.signal).then(function () { j.done = true; if (io) io.unobserve(j.el); }, function (e) {
      if (e && e.name === 'AbortError') { if (j.vis && j.el.isConnected) { j.queued = true; queue.unshift(j); } return; }
      j.done = true; if (io) io.unobserve(j.el); j.fail(e);
    }).then(function () { j.running = false; active--; pump(); });
  }
  function fitBox(w, h) {
    w = +w || 0; h = +h || 0; if (w <= 0 || h <= 0) return null;
    var s = Math.min(1, THUMB_BOX / w, THUMB_BOX / h);
    return { w: Math.max(48, Math.round(w * s)), h: Math.max(32, Math.round(h * s)) };
  }
  function fileEl(cid, f) {
    var name = String(f.n || T('Datei')).slice(0, 200), img = IMG.test(f.m);
    if (img) {
      var fig = el('button', 'ct-img'); fig.type = 'button'; fig.setAttribute('aria-label', name);
      var box = fitBox(f.w, f.h) || dims[f.id];
      var size2 = function (b) { fig.classList.remove('ct-img-nodim'); fig.style.width = b.w + 'px'; fig.style.aspectRatio = b.w + ' / ' + b.h; };
      if (box) size2(box); else fig.classList.add('ct-img-nodim');
      var im = el('img'); im.alt = name; im.decoding = 'async'; fig.appendChild(im);
      if (!box) im.addEventListener('load', function () {      // alte Nachricht ohne w/h: Groesse vom Vorschaubild merken
        var b = fitBox(im.naturalWidth, im.naturalHeight); if (b) { dims[f.id] = b; size2(b); }
      });
      var u = memGet('t:' + f.id);
      if (u) im.src = u;
      else lazy(fig, function (signal) { return thumbUrl(cid, f, signal).then(function (x) { im.src = x; }); },
        function () { fig.classList.add('ct-img-err'); fig.textContent = '🖼 ' + name + ' – ' + T('nicht geladen'); });
      fig.addEventListener('click', function () { viewImage(cid, f, name); });
      return fig;
    }
    var b = el('button', 'ct-file'); b.type = 'button';
    b.appendChild(el('span', 'ct-file-ic', '📎')); b.appendChild(el('span', 'ct-file-n', name)); b.appendChild(el('span', 'ct-file-s', size(f.s)));
    b.addEventListener('click', function () {
      plainUrl(cid, f).then(function (url) { save(url, name); setTimeout(function () { URL.revokeObjectURL(url); }, 60000); },
        function (e) { HK.toast(T('Anhang nicht geladen: {err}', { err: e.message })); });
    });
    return b;
  }
  // Original in groß (erst hier geladen); Speichern ueber den Knopf
  var viewUrl = null;
  function viewImage(cid, f, name) {
    if (viewUrl) { URL.revokeObjectURL(viewUrl); viewUrl = null; }
    var body = overlay(name, { wide: true }), wrap = el('div', 'ct-view'), st = el('p', 'hk-status', T('Lädt …'));
    st.setAttribute('role', 'status'); wrap.appendChild(st); body.appendChild(wrap);
    var bar = el('div', 'hk-actions'), sv = btn(T('Speichern'), 'ct-view-save'); sv.disabled = true; bar.appendChild(sv); body.appendChild(bar);
    var t = memGet('t:' + f.id), im = el('img'); im.alt = name;
    if (t) { im.src = t; wrap.appendChild(im); }
    plainUrl(cid, f).then(function (url) {
      if (!body.isConnected) { URL.revokeObjectURL(url); return; }
      viewUrl = url; im.src = url; if (!im.parentNode) wrap.appendChild(im); st.remove();
      sv.disabled = false; sv.addEventListener('click', function () { save(url, name); });
    }, function (e) { st.textContent = T('Anhang nicht geladen: {err}', { err: e.message }); });
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
  // „Aus Immich“: nur wenn Immich fuer diese Person eingerichtet ist (wie beim Bild-Einfuegen auf Seiten). Das Bild
  // kommt ueber den Immich-Proxy des Servers (nur durchgereicht, nicht gespeichert) und wird dann wie jeder andere
  // Anhang auf diesem Geraet verschluesselt hochgeladen.
  var immichOn = null;
  function immichCheck() {
    if (!HK.immichPick) return Promise.resolve(false);
    if (immichOn !== null) return Promise.resolve(immichOn);
    return api('GET', '/api/settings/immich').then(function (s) { immichOn = !!s.connected; return immichOn; }, function () { return false; });
  }
  function immichBlob(it, max) {
    function get(sz) {
      return fetch('/api/immich/thumb/' + encodeURIComponent(it.id) + '?size=' + sz, { credentials: 'same-origin', headers: { 'X-Saga': '1' }, cache: 'no-store' })
        .then(function (r) { if (!r.ok) { var e = new Error(r.status === 413 ? T('Bild zu groß') : 'HTTP ' + r.status); e.status = r.status; throw e; } return r.blob(); });
    }
    // GIFs im Original (Animation), sonst die groesste Vorschau (JPEG/WebP; HEIC-Originale koennen Browser nicht anzeigen)
    var p = it.mime === 'image/gif' ? get('original').catch(function () { return get('preview'); }) : get('preview');
    return p.then(function (b) { return b.size > max && b.type !== 'image/gif' ? shrink(b, max) : b; })
      .then(function (b) {
        if (b.size > max) throw new Error(T('„{name}“ ist zu groß (höchstens {n} MB).', { name: it.name || T('Foto'), n: Math.round(max / 1048576) }));
        var base = String(it.name || 'immich').replace(/\.[^.]+$/, '').slice(0, 120) || 'immich';
        var ext = { 'image/gif': '.gif', 'image/png': '.png', 'image/webp': '.webp' }[b.type] || '.jpg';
        return new File([b], base + ext, { type: b.type || 'image/jpeg' });
      });
  }
  function shrink(b, max) {                       // zu grosses Bild auf diesem Geraet verkleinern (JPEG)
    return createImageBitmap(b).then(function (bm) {
      var f = Math.min(1, Math.sqrt(max / b.size) * 0.9), cv = document.createElement('canvas');
      cv.width = Math.max(1, Math.round(bm.width * f)); cv.height = Math.max(1, Math.round(bm.height * f));
      cv.getContext('2d').drawImage(bm, 0, 0, cv.width, cv.height);
      return new Promise(function (res) { cv.toBlob(function (o) { res(o || b); }, 'image/jpeg', 0.85); });
    });
  }
  function immichAttach() {
    var max = (S.me && S.me.limits && S.me.limits.file_max) || 10485760;
    HK.immichPick(function () {}, null, { select: function (it) {
      if (S.files.length >= 10) throw new Error(T('Höchstens 10 Anhänge je Nachricht.'));
      return immichBlob(it, max).then(function (f) { S.files.push(f); drawFiles(); $('ct-input').focus(); });
    } });
  }
  // Platzhalter: auf schmalen Bildschirmen kurz (der lange Hinweis brach um und wurde abgeschnitten)
  (function () {
    var inp = $('ct-input'), long = inp.placeholder, mq = window.matchMedia ? window.matchMedia('(max-width: 760px)') : null;
    function ph() { inp.placeholder = mq && mq.matches ? T('Nachricht …') : long; }
    ph(); if (mq) { if (mq.addEventListener) mq.addEventListener('change', ph); else if (mq.addListener) mq.addListener(ph); }
  })();
  immichCheck();                                          // vorab: die Dateiauswahl muss direkt im Klick geoeffnet werden
  $('ct-attach').addEventListener('click', function () {
    if (!immichOn) return $('ct-file').click();
    HK.openMenu(this, function (menu) {
      menu.appendChild(HK.menuItem(T('Bild oder Datei …'), function () { HK.closeMenu(); $('ct-file').click(); }, { icon: '📎' }));
      menu.appendChild(HK.menuItem(T('Aus Immich …'), function () { HK.closeMenu(); immichAttach(); }, { icon: '📷' }));
    });
  });
  $('ct-file').addEventListener('change', function () {
    var max = (S.me && S.me.limits && S.me.limits.file_max) || 10485760;
    Array.prototype.forEach.call(this.files, function (f) {
      if (f.size > max) { HK.toast(T('„{name}“ ist zu groß (höchstens {n} MB).', { name: f.name, n: Math.round(max / 1048576) })); return; }
      if (S.files.length < 10) S.files.push(f);
    });
    this.value = ''; drawFiles();
    $('ct-input').focus();                                 // Enter sendet dann, statt die Dateiauswahl erneut zu oeffnen (BUG-099)
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
  // 2.3: Senden in Chats ueber Servergrenzen (saga-chat/v2): Empfaenger = Geraete aus den selbst geprueften Schluesseln
  // aller Mitglieder (signierte Geraete-Liste, TOFU); passt das dem Server/Heim nicht (409), einmal frisch holen.
  function sendV2(c, body, reply, extra, kind) {
    var hs = c.members.map(function (m) { return m.handle; }).filter(Boolean);
    function attempt(fresh, again) {
      // Mitglieder geaendert (Gruppe/Seiten-Chat, 2.3): erst die aktuelle Liste holen, dann die Schluessel
      return (again ? api('GET', '/api/chats/' + c.id + '?device=' + q(dev())).then(function (r) {
        if (r && r.chat) { c.members = r.chat.members; hs = c.members.map(function (m) { return m.handle; }).filter(Boolean); }
      }).catch(function () {}) : Promise.resolve()).then(function () { return fedKeys(hs, fresh); }).then(function (fk) {
        if (S.cur && S.cur.id === c.id) { S.fk = fk; drawWarn(); }
        var bad = c.members.filter(function (m) { return !m.me && fk[m.handle] && ['warn', 'block', 'reject'].indexOf(fk[m.handle].trust) >= 0; });
        if (bad.length) throw new Error(T('Die Sicherheitsnummer von {name} hat sich geändert – erst prüfen oder „Verstanden“ wählen.', { name: bad.map(function (m) { return m.name; }).join(', ') }));
        var mine = fk[c.my_handle];
        if (!mine || !mine.keys || mine.trust !== 'trust') throw new Error(T('Deine Schlüssel für Chats über Servergrenzen sind noch nicht bereit – bitte die Seite neu laden.'));
        var ents = hs.filter(function (h) { return fk[h] && fk[h].keys; }).map(function (h) { return fk[h].keys; });
        S.rc = { cid: c.id, missing: c.members.filter(function (m) { return !m.me && !(fk[m.handle] || {}).keys; }).map(function (m) { return m.name; }) };
        var m = X.encryptMessageV2({ chat_ref: c.ref, sender_handle: c.my_handle, device: K.device.id, sign_sk: K.device.sign_sk, body: body,
                                     reply_to: reply || null, ai: (extra && extra.ai) || null, recipients: X.recipientsV2(ents) });
        m.v = 2;
        if (kind === 'title') m.kind = 'title';
        if (extra && extra.ai_reply) m.ai_reply = extra.ai_reply;
        if (body.files) m.files = body.files.reduce(function (a, f) { a.push(f.id); if (f.th && f.th.id) a.push(f.th.id); return a; }, []);
        return api('POST', '/api/chats/' + c.id + '/messages', m).then(function (r) {
          if (r.pending) HK.toast(T('Der andere Server ist gerade nicht erreichbar – die Nachricht wird zugestellt, sobald er wieder da ist.'), null, null, 6000);
          return { m: m, r: r };
        }, function (e) {
          if (e.status === 409 && e.data && e.data.handles && !again) return attempt(true, true);
          throw e;
        });
      });
    }
    return attempt(false, false);
  }
  function sendBody(cid, body, kind, reply, extra) {
    var fc = S.cur && S.cur.id === cid ? S.cur : S.chats.filter(function (x) { return x.id === cid; })[0];
    if (isFed(fc)) return sendV2(fc, body, reply, extra, kind).catch(function (e) {
      return checkDevice().then(function (okDev) { if (!okDev) { e = new Error(T('Dieses Gerät ist für Chats abgemeldet.')); } throw e; });
    });
    function attempt(rc, again) {
      var m = X.encryptMessage({ chat_id: cid, sender: ME, device: K.device.id, sign_sk: K.device.sign_sk, body: body, reply_to: reply || null, recipients: rc.list });
      m.kind = kind || 'msg';
      if (extra) Object.keys(extra).forEach(function (k) { m[k] = extra[k]; });   // z. B. ai_reply (nicht signiert, nur Verweis)
      if (body.files) m.files = body.files.reduce(function (a, f) { a.push(f.id); if (f.th && f.th.id) a.push(f.th.id); return a; }, []);
      return api('POST', '/api/chats/' + cid + '/messages', m).then(function (r) { return { m: m, r: r }; }, function (e) {
        if (e.status === 409 && e.data && e.data.recipients && !again) {
          var rc2 = useRecipients(cid, e.data.recipients);
          return trustCheck(rc2.raw).then(function () { return attempt(rc2, true); });
        }
        throw e;
      });
    }
    return recipients(cid).then(function (rc) { return trustCheck(rc.raw).then(function () { return attempt(rc, false); }); })
      .catch(function (e) {                                  // Senden scheitert, weil dieses Geraet nicht mehr gilt? Erklaeren.
        return checkDevice().then(function (okDev) { if (!okDev) { e = new Error(T('Dieses Gerät ist für Chats abgemeldet.')); } throw e; });
      });
  }
  // Bilder (2.1): Vorschaubild (320 px, JPEG 70, ohne Metadaten) im Worker erzeugen, getrennt verschluesseln und
  // hochladen; files[] bekommt w/h des Originals und th:{id,k,m,w,h,s}. Scheitert das Vorschaubild, geht das Bild ohne th.
  function upload(cid, blob) {
    return fetch('/api/chats/' + cid + '/files', { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/octet-stream' }, body: blob })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d.id; }); });
  }
  function uploadFiles(cid) {
    var ids = (S.me && S.me.limits && S.me.limits.file_ids_per_msg) || 10, slots = ids - S.files.length;
    var fc = S.cur && S.cur.id === cid ? fileCid(S.cur) : cid;          // Verschluesselung: v1 chat_id, v2 chat_ref
    return Promise.all(S.files.map(function (f) {
      var mime = f.type || 'application/octet-stream', withThumb = IMG.test(mime) && slots-- > 0;
      return f.arrayBuffer().then(function (buf) {
        var tp = withThumb ? run('make', { bytes: buf, mime: mime }).catch(function () { return null; }) : Promise.resolve(null);
        return tp.then(function (t) {
          var e = { id: null, k: null, n: f.name.slice(0, 200), m: mime, s: f.size };
          var th = Promise.resolve(null);
          if (t) {
            var thumb = new Blob([t.jpeg], { type: 'image/jpeg' }), tsz = t.jpeg.byteLength;
            e.w = t.w; e.h = t.h;
            th = run('enc', { cid: fc, bytes: t.jpeg }, [t.jpeg]).then(function (et) {
              var ct = new Blob([et.blob]);
              return upload(cid, ct).then(function (id) {
                memPut('t:' + id, URL.createObjectURL(thumb)); bPut(id, ct);     // eigene Nachricht: sofort, ohne Nachladen
                return { id: id, k: et.key, m: 'image/jpeg', w: t.tw, h: t.th, s: tsz };
              });
            });
          }
          var orig = run('enc', { cid: fc, bytes: buf }, [buf]).then(function (eo) { e.k = eo.key; return upload(cid, eo.blob); });
          return Promise.all([orig, th]).then(function (r) {
            e.id = r[0];
            if (r[1]) { e.th = r[1]; var u = mem.get('t:' + r[1].id); if (u) memPut('t:' + e.id, u); }
            return e;
          });
        });
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
    var reply = S.reply ? S.reply.id : null, askAi = aiOn(c) && aiMention(text);
    (S.files.length ? uploadFiles(c.id) : Promise.resolve([])).then(function (files) {
      var body = { v: 1, type: 'text', text: text };
      if (files.length) body.files = files;
      if (notes.length) body.notes = notes;
      return sendBody(c.id, body, 'msg', reply);
    }).then(function (x) {
      inp.value = ''; autosize(); S.reply = null; S.files = []; S.mentions = {}; drawReply(); drawFiles();
      if (S.rc && S.rc.missing.length) HK.toast(T('{names} hat Chats noch nicht eingerichtet und kann diese Nachricht nicht lesen.', { names: S.rc.missing.join(', ') }), null, null, 6000);
      aiHint();
      return loadMsgs(false).then(function () { if (askAi && x && x.m) askAI(c, text, x.m.id); return x; });
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
    autosize(); picker(); aiHint();
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
    var aiItem = aiOn(S.cur) && aiP().name.toLowerCase().indexOf(m[2].toLowerCase()) === 0 ? [{ ai: true, title: aiP().name }] : [];
    pk.t = setTimeout(function () {
      api('GET', '/api/chat/pages?q=' + q(m[2])).catch(function () { return { pages: [] }; }).then(function (r) {
        pk.items = aiItem.concat(r.pages || []); pk.i = 0; box.textContent = '';
        if (!pk.items.length) { box.hidden = true; return; }
        pk.items.forEach(function (p, i) {
          var b = el('button', 'picker-item' + (i === 0 ? ' on' : ''), p.ai ? '' : (p.icon || '📄') + ' ' + p.title); b.type = 'button'; b.setAttribute('role', 'option');
          if (p.ai) {                                    // KI (2.1): "@Marin" fragt die KI (nur auf Zuruf)
            b.classList.add('ct-pick-ai'); b.appendChild(HKAIAvatar(aiP(), 20)); b.appendChild(el('span', 'ct-pick-n', p.title));
            b.appendChild(el('span', 'ct-pick-h', T('KI fragen')));
          }
          b.addEventListener('mousedown', function (e) { e.preventDefault(); pickApply(i); });
          box.appendChild(b);
        });
        box.hidden = false;
      });
    }, aiItem.length ? 0 : 150);
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
    if (!p.ai) S.mentions[label] = p.id;
    t.value = t.value.slice(0, pk.start) + label + ' ' + t.value.slice(t.selectionStart);
    var pos = pk.start + label.length + 1; t.setSelectionRange(pos, pos);
    $('ct-picker').hidden = true; t.focus(); aiHint();
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
      if (S.ai && S.ai.chat) menu.appendChild(HK.menuItem(c.ai ? T('{name} hier nicht mehr erlauben', { name: aiP().name }) : T('{name} in diesem Chat erlauben', { name: aiP().name }),
        function () { HK.closeMenu(); aiToggle(c); }, { icon: '✦' }));
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
      manageItems(menu, c);
    });
  });

  // --- Archivieren und Loeschen (2.1) -------------------------------------------------------------------------------
  // Menue der Chatliste (Rechtsklick, Kontextmenue-Taste, Langdruck)
  function chatMenu(anchor, c) {
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      menu.appendChild(HK.menuItem(T('Öffnen'), function () { HK.closeMenu(); location.hash = 'c' + c.id; if (S.cur && S.cur.id === c.id) openChat(c.id); }, { icon: '↗' }));
      menu.appendChild(HK.menuItem(c.muted ? T('Benachrichtigungen an') : T('Stummschalten'), function () {
        HK.closeMenu(); api('PATCH', '/api/chats/' + c.id, { muted: !c.muted }).then(function () { loadList(); }).catch(function (e) { HK.toast(e.message); });
      }, { icon: c.muted ? '🔔' : '🔕' }));
      manageItems(menu, c);
    });
  }
  function manageItems(menu, c) {
    menu.appendChild(HK.menuItem(c.archived ? T('Aus Archiv holen') : T('Archivieren'), function () { HK.closeMenu(); archive(c, !c.archived); },
      { icon: c.archived ? '⤴' : '🗄' }));
    menu.appendChild(HK.menuItem(T('Chat löschen …'), function () { HK.closeMenu(); deleteDialog(c); }, { icon: '🗑', danger: true }));
  }
  function archive(c, on) {
    return api('PATCH', '/api/chats/' + c.id, { archived: on }).then(function () {
      c.archived = on; drawList(); if (S.cur && S.cur.id === c.id) { S.cur.archived = on; drawHead(); }
      HK.toast(on ? T('Chat archiviert – er kommt mit der nächsten Nachricht zurück.') : T('Chat aus dem Archiv geholt.'));
      loadList();
    }).catch(function (e) { HK.toast(e.message); });
  }
  function deleteDialog(c) {
    var nm = chatName(c), all = c.delete_for_all, box = overlay(T('„{name}“ löschen?', { name: nm }));
    var fs = el('fieldset', 'ct-del'); fs.appendChild(el('legend', 'ct-del-l', T('Für wen löschen?')));
    function opt(val, label, text, dis) {
      var lab = el('label', 'ct-del-o' + (dis ? ' dis' : '')), r = el('input'); r.type = 'radio'; r.name = 'ct-del'; r.value = val; r.disabled = !!dis;
      var t = el('span', 'ct-del-t'); t.appendChild(el('b', null, label)); t.appendChild(el('span', 'hk-hint', text));
      lab.appendChild(r); lab.appendChild(t); fs.appendChild(lab); return r;
    }
    var peer = (c.peer || {}).name || nm;
    var rMe = opt('me', T('Nur für mich'), c.kind === 'dm'
      ? T('Der Verlauf verschwindet nur bei dir, auf allen deinen Geräten. {name} behält ihn. Der Chat erscheint wieder, sobald eine neue Nachricht kommt.', { name: peer })
      : T('Der Verlauf verschwindet nur bei dir, auf allen deinen Geräten; der Chat wird archiviert. Die anderen behalten ihn.'));
    var leave = null;
    if (c.kind === 'free') {
      var ll = el('label', 'ct-del-sub'); leave = el('input'); leave.type = 'checkbox';
      ll.appendChild(leave); ll.appendChild(el('span', null, T('Gruppe außerdem verlassen'))); fs.appendChild(ll);
    }
    var allText = all === 'history' ? T('Alle Nachrichten und Anhänge werden für alle Mitglieder gelöscht. Der Chat selbst bleibt, solange es die Gruppe bzw. Seite gibt.')
      : c.kind === 'dm' ? T('Der Chat wird für {name} und dich gelöscht – mit allen Nachrichten, Anhängen und Anrufen.', { name: peer })
      : T('Die Gruppe wird für alle Mitglieder gelöscht – mit allen Nachrichten, Anhängen und Anrufen.');
    var why = c.kind === 'free' ? T('Nur Admins dieser Gruppe können sie für alle löschen.')
      : c.kind === 'group' ? T('Den Verlauf für alle löschen können nur Saga-Admins.')
      : c.kind === 'page' ? T('Den Verlauf für alle löschen kann nur, wem die Seite gehört.') : '';
    var rAll = opt('all', T('Für alle'), all ? allText : why, !all);
    rMe.checked = true;
    box.appendChild(fs);
    var warn = el('p', 'ct-danger ct-del-warn'); warn.setAttribute('role', 'note');
    warn.textContent = '⚠ ' + T('Das lässt sich nicht rückgängig machen. Wer Nachrichten schon gesehen hat, kann Kopien davon haben.');
    box.appendChild(warn);
    var st = status(box), bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Abbrechen'), '', closeOverlay));
    var go = btn(T('Löschen'), 'btn-danger ct-del-go'); bar.appendChild(go);
    function sync() {
      if (leave) { leave.disabled = !rMe.checked; leave.parentNode.classList.toggle('dis', !rMe.checked); }
      go.textContent = rAll.checked ? (all === 'history' ? T('Verlauf für alle löschen') : T('Für alle löschen')) : T('Für mich löschen');
    }
    fs.addEventListener('change', sync); sync();
    go.addEventListener('click', function () {
      var scope = rAll.checked ? 'all' : 'me', quit = scope === 'me' && leave && leave.checked;
      busy(go, true); st.className = 'hk-status'; st.textContent = '';
      api('DELETE', '/api/chats/' + c.id + '?scope=' + scope).then(function (r) {
        if (quit) return api('DELETE', '/api/chats/' + c.id + '/members/' + q(ME)).then(function () { return { removed: true, left: true }; });
        return r;
      }).then(function (r) {
        closeOverlay();
        var cur = S.cur && S.cur.id === c.id;
        if (r.removed || (scope === 'me' && c.kind === 'dm')) {
          S.chats = S.chats.filter(function (x) { return x.id !== c.id; });
          if (cur) { closeChat(); history.replaceState(null, '', location.pathname); }
        } else if (cur) wipeView();
        HK.toast(r.left ? T('Verlauf gelöscht, Gruppe verlassen.') : scope === 'all' ? (r.removed ? T('Chat für alle gelöscht.') : T('Verlauf für alle gelöscht.'))
          : c.kind === 'dm' ? T('Chat für dich gelöscht.') : T('Verlauf für dich gelöscht, Chat archiviert.'));
        loadList();
      }).catch(function (e) { st.className = 'hk-status err'; st.textContent = e.message; busy(go, false); sync(); });
    });
    rMe.focus();
  }
  // Offener Chat wurde geleert: Ansicht leeren, Hinweis "Verlauf geloescht", Neues nachladen
  function wipeView() {
    var c = S.cur; if (!c) return;
    c.cleared_seq = Math.max(c.cleared_seq || 0, c.last_seq || 1);
    S.msgs = []; S.byId = {}; S.seenSeq = 0; S.more = false; S.reply = null; drawReply();
    drawMsgs('bottom'); loadMsgs(false);
  }
  // Fremdes Loeschen live (chat.removed / chat.cleared mit scope 'all'): Chat bzw. Verlauf weg, Hinweis mit Namen
  function removedLive(d, whole) {
    var mine = d.by && d.by.username === ME, cur = S.cur && S.cur.id === d.chat_id;
    if (whole) {
      S.chats = S.chats.filter(function (x) { return x.id !== d.chat_id; });
      if (cur) { closeChat(); history.replaceState(null, '', location.pathname); }
      drawList();
    } else if (cur) wipeView();
    if (!mine && d.by) HK.toast(whole ? T('{name} hat den Chat gelöscht.', { name: d.by.name || d.by.username }) : T('{name} hat den Verlauf gelöscht.', { name: d.by.name || d.by.username }));
    listSoon();
  }
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
    if (isFed(c) && c.kind !== 'dm') box.appendChild(el('p', 'hk-hint', c.home_self ? T('Dieser Chat liegt auf diesem Server; Personen anderer Saga-Server sind über ihren Server dabei.')
      : T('Dieser Chat liegt auf {host}. Mitglieder ändert man dort (über diesen Server).', { host: c.home || '?' })));
    var ul = el('ul', 'ct-devs'); box.appendChild(ul);
    c.members.forEach(function (m) {
      var li = el('li', 'ct-dev'); li.appendChild(avatar((m.name || '?').charAt(0).toUpperCase(), m.username, '', m.avatar));
      var t = el('div', 'ct-dev-t'); t.appendChild(el('b', null, m.me ? m.name + ' (' + T('du') + ')' : m.name));
      t.appendChild(el('span', 'hk-hint', m.handle && m.host ? T('auf {host}', { host: m.host }) : m.username)); li.appendChild(t);
      if (!m.me) li.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
      if (c.kind === 'free' && c.role === 'admin' && !m.me) li.appendChild(btn(T('Entfernen'), 'btn-danger small', function () {
        api('DELETE', '/api/chats/' + c.id + '/members/' + q(m.username)).then(function () { closeOverlay(); openChat(c.id); loadList(); }).catch(function (e) { HK.toast(e.message); });
      }));
      ul.appendChild(li);
    });
    if (c.kind === 'free' && c.role === 'admin') {
      var inp = el('input', 'hk-in'); inp.type = 'search'; inp.placeholder = T('Person hinzufügen'); inp.setAttribute('aria-label', T('Person hinzufügen'));
      var res = el('ul', 'ct-people'); box.appendChild(inp); box.appendChild(res);
      var tmr = null;
      inp.addEventListener('input', function () {
        clearTimeout(tmr); tmr = setTimeout(function () {
          if (!inp.value.trim()) { res.textContent = ''; return; }
          var v = inp.value.trim();
          api('GET', '/api/chat/people?q=' + q(v)).then(function (r) {
            res.textContent = '';
            if (!r.users.length && !(r.remote || []).length && fedAddr(v)) res.appendChild(fedHint());
            if (isFed(c)) (r.remote || []).filter(function (u) { return !c.members.some(function (m) { return m.handle === u.handle; }); }).forEach(function (u) {
              var li = el('li'), b = el('button', 'ct-person ct-remote', (u.label || u.name)); b.type = 'button';
              b.addEventListener('click', function () {
                api('POST', '/api/chats/' + c.id + '/members', { handle: u.handle }).then(function () {
                  closeOverlay(); openChat(c.id); loadList();
                  if (c._title) sendBody(c.id, { v: 1, type: 'title', title: c._title }, 'title').catch(function () {});
                }).catch(function (e) { HK.toast(e.message); });
              });
              li.appendChild(b); res.appendChild(li);
            });
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

  // --- KI im Chat (2.1, saga/ai_chat.py): nur auf Zuruf -----------------------------------------------------------
  // Die KI liest nur Nachrichten mit @Name: dieses Geraet schickt die Nachricht und hoechstens AI_CTX Nachrichten davor
  // im Klartext an den Server, bekommt die Antwort als Stream und verschluesselt sie wie jede Nachricht fuer alle
  // (mit ai_reply = Beleg des Servers). Andere Clients zeigen sie als KI-Antwort, wenn der SHA-256 zum Text passt.
  var AI_CTX = 12;
  function aiP() { return (S.ai && S.ai.persona) || { name: T('Saga KI'), avatar: null }; }
  function aiOn(c) { return !!(c && c.ai && S.ai && S.ai.enabled && S.ai.chat); }
  function aiMention(text) {
    var n = aiP().name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    try { return new RegExp('(^|[\\s(\\["\'])@' + n + '(?![\\p{L}\\p{N}_])', 'iu').test(text || ''); } catch (e) { return false; }
  }
  function loadAI() {
    return api('GET', '/api/ai/status').then(function (d) { S.ai = d && d.enabled ? d : null; }, function () { S.ai = null; })
      .then(function () { if (S.cur) { drawAI(); drawMsgs(); } aiHint(); });
  }
  function aiCheck(m) {
    var b = m.body;
    if (!b || !b.ai) return null;
    if (typeof m.ai === 'string') {                  // v2: KI-Hash ist Teil der signierten Nachricht
      try { return typeof b.text === 'string' && window.sodium.to_hex(window.sodium.crypto_hash_sha256(window.sodium.from_string(b.text.trim()))) === m.ai ? 'ok' : 'bad'; }
      catch (e) { return 'bad'; }
    }
    if (!m.ai || typeof b.text !== 'string' || b.ai.reply !== m.ai.ref || !window.sodium) return 'bad';
    try {
      var h = window.sodium.to_hex(window.sodium.crypto_hash_sha256(window.sodium.from_string(b.text.trim())));
      return h === m.ai.sha256 ? 'ok' : 'bad';
    } catch (e) { return 'bad'; }
  }
  function aiWho(m) {
    var w = el('div', 'ct-who ct-ai-who');
    w.appendChild(HKAIAvatar(aiP(), 22)); w.appendChild(el('span', null, aiP().name));
    if (m) {                                         // Fragender geloescht (Beleg anonymisiert, Absender fehlt): neutral benennen
      var by = !m.sender ? T('Gelöschtes Konto') : m.sender.username === ME ? T('dir') : (m.sender.name || T('Gelöschtes Konto'));
      w.appendChild(el('span', 'ct-ai-by', T('gefragt von {name}', { name: by })));
    }
    return w;
  }
  function drawAI() {
    var n = $('ct-ai-note'), c = S.cur; if (!n) return;
    n.textContent = ''; n.hidden = !aiOn(c); if (n.hidden) return;
    n.appendChild(HKAIAvatar(aiP(), 20));
    n.appendChild(el('span', null, S.ai.web
      ? T('{name} ist in diesem Chat erlaubt – nur auf Zuruf: Nachrichten mit @{name} und bis zu {n} Nachrichten davor gehen im Klartext an die KI ({host}) und Suchbegriffe über die Websuche an Suchmaschinen. Alles andere bleibt Ende-zu-Ende-verschlüsselt.',
        { name: aiP().name, n: AI_CTX, host: S.ai.host || '?' })
      : T('{name} ist in diesem Chat erlaubt – nur auf Zuruf: Nachrichten mit @{name} und bis zu {n} Nachrichten davor gehen im Klartext an die KI ({host}). Alles andere bleibt Ende-zu-Ende-verschlüsselt.',
        { name: aiP().name, n: AI_CTX, host: S.ai.host || '?' })));
  }
  function aiHint() {
    var h = $('ct-ai-hint'); if (!h) return;
    var on = aiOn(S.cur) && aiMention($('ct-input').value);
    h.hidden = !on; if (!on) return;
    h.textContent = ''; h.appendChild(HKAIAvatar(aiP(), 16));
    h.appendChild(el('span', null, S.ai.web
      ? T('Geht an {name}: diese Nachricht und bis zu {n} davor, im Klartext an {host} – und Suchbegriffe über die Websuche.', { name: aiP().name, n: AI_CTX, host: S.ai.host || '?' })
      : T('Geht an {name}: diese Nachricht und bis zu {n} davor, im Klartext an {host}.', { name: aiP().name, n: AI_CTX, host: S.ai.host || '?' })));
  }
  function aiToggle(c) {
    var on = !c.ai, P = aiP();
    (on ? HK.ask({ title: T('{name} in diesem Chat erlauben?', { name: P.name }),
      text: T('Dann kann jede Person im Chat {name} mit @{name} fragen. Nur diese Nachricht und bis zu {n} Nachrichten davor gehen im Klartext an die KI ({host}) – der Server speichert sie nicht. Alle Mitglieder sehen einen Hinweis.', { name: P.name, n: AI_CTX, host: (S.ai && S.ai.host) || '?' }) +
        (S.ai && S.ai.web ? ' ' + T('Bei Fragen nach Aktuellem darf {name} im Web suchen; die Suchbegriffe gehen über die Websuche an Suchmaschinen.', { name: P.name }) : ''),
      ok: T('Erlauben') }) : Promise.resolve(true)).then(function (y) {
      if (!y) return;
      return api('PUT', '/api/chats/' + c.id + '/ai', { allowed: on }).then(function () {
        c.ai = on; drawAI(); aiHint(); HK.toast(on ? T('{name} ist in diesem Chat erlaubt.', { name: P.name }) : T('{name} ist hier nicht mehr erlaubt.', { name: P.name }));
      });
    }).catch(function (e) { HK.toast(e.message); });
  }
  function aiHistory(qid) {
    var out = [];
    for (var i = S.msgs.length - 1; i >= 0 && out.length < AI_CTX; i--) {
      var m = S.msgs[i];
      if (m.id === qid || m.kind !== 'msg' || !m.body || !m.body.text) continue;
      out.unshift({ name: m._ai === 'ok' ? aiP().name : (m.sender ? m.sender.name : '?'), text: String(m.body.text).slice(0, 600) });
    }
    return out;
  }
  function aiStream(url, body, on) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json', 'Accept': 'text/event-stream' }, body: JSON.stringify(body) })
      .then(function (r) {
        if (!r.ok || (r.headers.get('content-type') || '').indexOf('text/event-stream') !== 0)
          return r.json().catch(function () { return {}; }).then(function (d) { throw new Error(d.error || ('HTTP ' + r.status)); });
        var rd = r.body.getReader(), dec = new TextDecoder(), buf = '';
        function emit(chunk) {
          var ev = null, data = '';
          chunk.split('\n').forEach(function (ln) { if (ln.indexOf('event:') === 0) ev = ln.slice(6).trim(); else if (ln.indexOf('data:') === 0) data += ln.slice(5).trim(); });
          if (ev) { try { on(ev, JSON.parse(data || 'null') || {}); } catch (e) { /* kaputte Zeile */ } }
        }
        function pump() {
          return rd.read().then(function (x) {
            if (x.done) { if (buf.trim()) emit(buf); return; }
            buf += dec.decode(x.value, { stream: true });
            var i; while ((i = buf.indexOf('\n\n')) >= 0) { emit(buf.slice(0, i)); buf = buf.slice(i + 2); }
            return pump();
          });
        }
        return pump();
      });
  }
  function askAI(c, text, qid) {
    if (S.aiPending) return;
    var wrap = el('div', 'ct-ai-wait'), row = el('div', 'ct-msg ct-ai'), b = el('div', 'ct-bubble');
    var tx = el('div', 'ct-text ct-ai-typing', T('{name} denkt nach …', { name: aiP().name }));
    b.appendChild(tx); row.appendChild(b); wrap.appendChild(aiWho(null)); wrap.appendChild(row);
    S.aiPending = { cid: c.id, el: wrap };
    var box = $('ct-msgs'); box.appendChild(wrap); box.scrollTop = box.scrollHeight;
    var acc = '', done = null, err = null;
    aiStream('/api/chats/' + c.id + '/ai', { q: text, msg_id: qid, history: aiHistory(qid) }, function (ev, d) {
      if (ev === 'delta') { acc += d.t || ''; tx.classList.remove('ct-ai-typing'); tx.textContent = acc; if (S.cur && S.cur.id === c.id) box.scrollTop = box.scrollHeight; }
      else if (ev === 'reset') acc = '';
      else if (ev === 'status' && d.web && !acc) tx.textContent = d.web === 'searching' ? T('{name} sucht im Web …', { name: aiP().name }) : T('{name} denkt nach …', { name: aiP().name });
      else if (ev === 'done') done = d;
      else if (ev === 'error') err = d.error || T('Fehler');
    }).then(function () {
      if (err) throw new Error(err);
      if (!done || !done.reply || !done.text) throw new Error(T('Keine Antwort bekommen.'));
      return sendBody(c.id, { v: 1, type: 'text', text: done.text, ai: { reply: done.reply.id, name: aiP().name } }, 'msg', qid,
                      { ai_reply: done.reply.id, ai: done.reply.sha256 });      // v2: Hash wird mitsigniert (v1: ignoriert)
    }).then(function () {
      S.aiPending = null; wrap.remove();
      if (S.cur && S.cur.id === c.id) return loadMsgs(false);
    }).catch(function (e) {
      S.aiPending = null; wrap.remove();
      HK.toast(T('{name} konnte nicht antworten: {err}', { name: aiP().name, err: e.message }));
    });
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
    if (t === 'chat.message') {
      if (S.cur && S.cur.id === d.chat_id) {
        var known = S.byId[d.id];                       // (2.3) ausstehende Nachricht bestaetigt bzw. abgewiesen
        if (known && known.fstate) { known.fstate = d.failed ? 'failed' : null; drawMsgs(); }
        loadMsgs(false);
      }
      listSoon();
    }
    else if (t === 'chat.request') listSoon();                     // (2.3) neue Nachrichtenanfrage
    else if (t === 'chat.keys.changed') {                           // (2.3) Schluessel einer Person (bzw. die eigenen) geaendert
      if (d.own) fedCerts().catch(function () {});
      else if (S.cur && isFed(S.cur) && S.cur.members.some(function (m) { return m.handle === d.handle; })) fedLoad(S.cur, true).catch(function () {});
    }
    else if (t === 'chat.deleted') { if (S.cur && S.cur.id === d.chat_id) dropMsg(d.id); listSoon(); }
    else if (t === 'chat.removed') removedLive(d, true);           // (2.1) Chat fuer alle geloescht
    else if (t === 'chat.cleared') {                                // (2.1) Verlauf geleert: fuer mich (anderer Tab) oder fuer alle
      if (d.scope === 'all') removedLive(d, false);
      else { if (S.cur && S.cur.id === d.chat_id) { if (S.cur.kind === 'dm') { closeChat(); history.replaceState(null, '', location.pathname); } else wipeView(); } listSoon(); }
    }
    else if (t === 'chat.read') listSoon();
    else if (t === 'chat.changed') { listSoon(); if (S.cur && S.cur.id === d.chat_id) { S.rc = null; openChat(d.chat_id); } }
    else if (t === 'avatar.changed') listSoon();      // neues Profilbild (Bilder tauscht live.js; Liste fuer neue Adressen)
    else if (t === 'ai.persona.changed') loadAI();     // Name/Bild/Schalter der KI geaendert
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

  // Anrufe (static/calls.js): Schluessel dieses Geraets und gepruefte Geraete des Gegenuebers (TOFU wie beim Senden)
  function callPeer(u, name) {
    return api('GET', '/api/chat/keys?users=' + q(u)).then(function (r) {
      var k = (r.users || {})[u];
      if (!k) throw new Error(T('{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.', { name: name || u }));
      return trustCheck([{ ready: true, username: u, name: name || u, ident_pub: k.ident_pub }]).then(function () {
        var ip = ub(k.ident_pub, 32);
        noteIdent(u, k.ident_pub);
        return { username: u, ident_pub: k.ident_pub, devices: (k.devices || []).filter(function (d) { return !d.revoked && X.verifyDevice(u, ip, d); })
          .map(function (d) { return { id: d.id, enc_pk: ub(d.enc_pub, 32), sign_pk: ub(d.sign_pub, 32) }; }) };
      });
    });
  }
  // 2.3: Gegenueber auf einem anderen Server - Schluessel ueber den eigenen Server, selbst geprueft (signierte Geraete-Liste, TOFU)
  function callPeerV2(h, name) {
    return fedKeys([h]).then(function (fk) {
      var f = fk[h];
      if (!f || !f.keys) throw new Error(T('{name} hat Chats noch auf keinem Gerät eingerichtet – Anrufe gehen erst danach.', { name: name || h }));
      if (f.trust !== 'trust') throw new Error(T('Die Sicherheitsnummer von {name} hat sich geändert – erst prüfen oder „Verstanden“ wählen.', { name: name || h }));
      return { handle: h, ident_pub: f.keys.ident_pub, devices: f.keys.devices.filter(function (d) { return !d.revoked; })
        .map(function (d) { return { id: d.id, enc_pk: ub(d.enc_pub, 32), sign_pk: ub(d.sign_pub, 32) }; }) };
    });
  }
  window.HKChat = { state: function () { return { ready: S.ready, device: dev(), cur: S.cur && S.cur.id, msgs: S.msgs.length, ai: aiOn(S.cur),
                                                   aiMsgs: S.msgs.filter(function (m) { return m._ai === 'ok'; }).length }; },
    callApi: { ready: function () { return !!(S.ready && K && X); }, me: function () { return ME; }, crypto: function () { return X; },
               device: function () { return K.device; }, peerKeys: callPeer, cur: function () { return S.cur; },
               peerKeysV2: callPeerV2, myHandle: function () { return S.myHandle || (S.cur && S.cur.my_handle) || null; },
               // Anruf vorbei: offenen Chat auffrischen - ist keiner offen (angenommen von einer anderen Seite), den des Anrufs oeffnen
               refresh: function (cid) { if (S.cur && S.cur.id === cid) loadMsgs(false); else if (!S.cur && S.ready && cid) openChat(cid); } } };
  start();
})();
