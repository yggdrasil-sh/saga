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
  var VID = window.SagaChatVideo || null;             // Videos im Chat (2.4, static/chat-video.js)
  var VOICE = window.SagaVoice || null;               // Sprachnachrichten (2.5, static/chat-voice.js)
  var ME = document.body.dataset.uid, MYNAME = document.body.dataset.name || ME;
  var X = null;                                        // SagaChatCrypto, nach sodium.ready
  var K = null;                                        // lokale Schluessel {device:{...}, ident_sk, ident_pk, backup_sk, backup_pk}
  var S = { me: null, chats: [], cur: null, msgs: [], byId: {}, more: false, reply: null, files: [], rc: null, notes: {},
            typing: {}, mentions: {}, people: {}, ai: null, aiPending: null, searchQ: '', loading: false, linkWait: null, ready: false,
            rcpts: {}, rcSent: {}, rcAt: {}, rcT: null, pres: {} };            // (2.4) Gelesen-Bestaetigungen, Online-Status
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
  // alle Eintraege mit diesem Praefix -> {schluessel: wert}
  function kvAll(prefix) {
    return kv('readonly', function (st) {
      var out = { result: {} }, r = st.openCursor(IDBKeyRange.bound(prefix, prefix + '\uffff'));
      r.onsuccess = function () { var cur = r.result; if (cur) { out.result[cur.key] = cur.value; cur.continue(); } };
      return out;
    });
  }
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
  var ovClean = null;                                 // Aufraeumen beim Schliessen (z. B. Video-Player)
  function closeOverlay() {
    if (ovClean) { var fn = ovClean; ovClean = null; try { fn(); } catch (e) { /* egal */ } }
    if (ov) { ov.remove(); ov = null; }
  }
  document.addEventListener('keydown', function (e) {
    // defaultPrevented: Esc hat schon ein Menue im Dialog geschlossen (z. B. Gruppeninfo -> Mitglied ⋯) - Dialog bleibt
    if (e.key === 'Escape' && !e.defaultPrevented && ov && ov.querySelector('.icon-btn') && !HK.menuOpen()) closeOverlay();
  });
  function btn(label, cls, fn) { var b = el('button', 'btn' + (cls ? ' ' + cls : ''), label); b.type = 'button'; if (fn) b.addEventListener('click', fn); return b; }
  function status(box) { var s = el('p', 'hk-status'); s.setAttribute('role', 'status'); box.appendChild(s); return s; }
  function busy(b, on, label) { b.disabled = on; if (label) b.textContent = label; }

  // --- Start --------------------------------------------------------------------------------------------------
  function start() {
    if (!window.sodium || !window.SagaChatCrypto) { fatal(T('Die Verschlüsselungs-Bibliothek konnte nicht geladen werden.')); return; }
    window.sodium.ready.then(function () {
      X = window.SagaChatCrypto(window.sodium);
      if (VID) VID.init({ X: X, api: api, T: T, el: el, run: run, me: function () { return S.me; } });
      if (VOICE && VID) {
        VOICE.init({ el: el, media: VID.media, plain: VID.plain, drop: VID.drop, b64dec: function (x) { return ub(x); } });
        VOICE.mode().then(function (md) { S.vmode = md; micDraw(); });
      }
      return kvGet(KEYS).catch(function () { throw new Error('idb'); });
    }).then(function (keys) {
      K = keys || null;
      return api('GET', '/api/chat/me?device=' + q(K ? K.device.id : ''));
    }).then(function (me) {
      // 2.4 (Web-Runde 2, H4): Schluessel auf diesem Geraet, aber der Server meldet (noch) keine Identitaet - nicht sofort
      // vergessen und "Chats einrichten" zeigen (neuer Tab beim Annehmen eines Anrufs), sondern einmal nachfragen
      if (!me.identity && K) return new Promise(function (r) { setTimeout(r, 1500); }).then(function () {
        return api('GET', '/api/chat/me?device=' + q(K.device.id));
      });
      return me;
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
  function forget() { K = null; S.ready = false; bClear(); if (VID) VID.clear(); return kvDel(KEYS).catch(function () {}); }
  function saveKeys(k) { K = k; return kvSet(KEYS, k); }

  // --- Einrichtung (erstes Geraet) -------------------------------------------------------------------------------
  function setupIntro(reset) {
    var box = overlay(reset ? T('Chats neu beginnen') : T('Chats einrichten'), { close: !!reset });
    box.appendChild(el('p', null, T('Saga-Chats sind Ende-zu-Ende-verschlüsselt: Nachrichten und Anhänge werden auf deinen Geräten ver- und entschlüsselt. Der Server – auch der Admin – sieht nur verschlüsselte Daten.')));
    box.appendChild(el('p', null, T('Dafür bekommst du gleich einen Wiederherstellungsschlüssel. Damit holst du deine Chats auf ein neues Gerät, wenn kein anderes mehr da ist. Ohne ihn sind deine Nachrichten verloren – auch der Admin kann sie nicht wiederherstellen.')));
    if (reset) box.appendChild(el('p', 'ct-danger', T('Achtung: Neu beginnen erzeugt neue Schlüssel. Bisherige Nachrichten sind dann für dich nicht mehr lesbar, deine Gesprächspartner sehen eine geänderte Sicherheitsnummer.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    if (!reset) {
      // D01: Anzeigenamen gleich mit abfragen (optional) - ohne ihn sehen andere nur "Unbekannte Person (…)"
      var acc = null, nmIn = null;
      api('GET', '/api/account').then(function (a) {
        if (!a || !a.can_edit || !box.isConnected) return;
        acc = a;
        var lab = el('label', 'hk-field ct-setup-name'), cap = el('span', null, T('Anzeigename'));
        nmIn = el('input', 'hk-in'); nmIn.type = 'text'; nmIn.maxLength = 120; nmIn.autocomplete = 'name';
        nmIn.value = a.name === a.username ? '' : (a.name || '');
        lab.appendChild(cap); lab.appendChild(nmIn);
        var hint = el('p', 'hk-hint', T('Optional – sehen alle, mit denen du chattest.')); hint.id = 'ct-setup-name-hint';
        nmIn.setAttribute('aria-describedby', hint.id);
        box.insertBefore(lab, bar); box.insertBefore(hint, bar);
      }).catch(function () { /* ohne Feld weiter */ });
      bar.appendChild(btn(T('Schlüssel erzeugen'), 'btn-primary ct-setup-go', function () {
        var v = nmIn ? nmIn.value.trim() : null, old = acc ? (acc.name === acc.username ? '' : (acc.name || '')) : null;
        if (nmIn && v !== old) {
          api('PATCH', '/api/account', { name: v }).then(function (d) { HK.toast(T('✓ Gespeichert')); if (d && d.name) acc.name = d.name; })
            .catch(function (e) { HK.toast(e.message); });
        }
        setupKey(false);
      }));
      return;
    }
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
  // 2.5 (Entscheidung des Maintainers 8.11.): Geraetegrenze erreicht (Server: code device_limit, max, count) - klar sagen,
  // wo man ein altes Geraet entfernt, statt im Bestaetigungsschritt zu haengen
  function isLimit(x) { return !!(x && (x.code === 'device_limit' || (x.data && x.data.code === 'device_limit'))); }
  function limitText(x) {
    var d = (x && x.data) || x || {}, me = (S.me && S.me.limits) || {};
    return T('Du hast schon {n} Chat-Geräte. Entferne ein altes Gerät unter „Chats & Geräte“, dann klappt es.', { n: d.count || me.devices_count || me.devices_max || 10 });
  }
  function limitHint(box, x, back) {
    var p = el('div', 'hk-status err ct-limit');
    p.appendChild(el('p', null, limitText(x)));
    var bar = el('div', 'hk-actions');
    bar.appendChild(btn(T('Chats & Geräte öffnen'), 'btn-primary ct-limit-go', settings));
    if (back) bar.appendChild(btn(T('Zurück'), '', back));
    p.appendChild(bar);
    box.appendChild(p);
    return p;
  }
  function newDevice() {
    var box = overlay(T('Chats auf diesem Gerät'), { close: false });
    var lim = S.me && S.me.limits;
    if (lim && lim.devices_max && lim.devices_count >= lim.devices_max) limitHint(box, { count: lim.devices_count });
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
    // Testrunde Web Nr. 061: Format nennen (Gross-/Kleinschreibung, Bindestriche und Leerzeichen sind egal)
    box.appendChild(el('p', 'hk-hint', T('Ein Wiederherstellungsschlüssel hat 32 Zeichen (A–Z, 2–7).') + ' ' + T('Bindestriche, Leerzeichen und Kleinbuchstaben sind egal.')));
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
          .then(function () { closeOverlay(); HK.toast(T('Dieses Gerät ist jetzt für Chats eingerichtet.')); ready(true); })
          .catch(function (e) {
            if (isLimit(e)) { st.textContent = ''; st.className = 'hk-status'; limitHint(box, e); busy(go, false, T('Entsperren')); return; }
            st.className = 'hk-status err'; st.textContent = e.message; busy(go, false, T('Entsperren'));
          });
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
    }).catch(function (e) {
      if (isLimit(e)) { st.textContent = ''; limitHint(box, e, newDevice); return; }
      st.className = 'hk-status err'; st.textContent = e.message; box.appendChild(btn(T('Zurück'), '', newDevice));
    });
  }
  function stopLink() { if (S.linkWait) { clearInterval(S.linkWait.t); S.linkWait = null; } }
  function linkPoll() {
    var w = S.linkWait; if (!w || w.busy) return;
    w.busy = true;
    api('GET', '/api/chat/link/' + w.id).then(function (r) {
      w.busy = false;
      if (r.state === 'pending') return;
      stopLink();
      if (r.state === 'failed' && isLimit(r)) {          // am anderen Geraet bestaetigt, aber die Grenze ist erreicht
        var fb = overlay(T('Mit anderem Gerät bestätigen'), { close: false });
        limitHint(fb, r, newDevice);
        return;
      }
      if (r.state !== 'approved') { HK.toast(T('Die Anfrage ist abgelaufen oder wurde abgelehnt.')); newDevice(); return; }
      var b = X.parsePlain(X.sealOpen(ub(r.sealed), w.d.enc_pk, w.d.enc_sk));
      if (!eqB(b.ident_pk, ub(S.me.identity.ident_pub, 32))) throw new Error(T('Die übertragenen Schlüssel passen nicht zu deinem Konto.'));
      return saveKeys({ v: 1, device: w.d, ident_sk: b.ident_sk, ident_pk: b.ident_pk, backup_sk: b.backup_sk, backup_pk: b.backup_pk })
        .then(function () { closeOverlay(); HK.toast(T('Dieses Gerät ist jetzt für Chats eingerichtet.')); ready(true); });
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
      ov.classList.add('ct-ov-top');                   // 2.4 (Testrunde W9): auch ueber einem laufenden Vollbild-Anruf
      ov.dataset.link = l.id;                          // auf einem anderen Geraet erledigt -> hier schliessen (chat.link)
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
          .catch(function (err) {
            if (isLimit(err)) { st.textContent = ''; st.className = 'hk-status'; bar.hidden = true; limitHint(box, err); return; }
            st.className = 'hk-status err'; st.textContent = err.message; busy(e.target, false);
          });
      }));
    }).catch(function () {});
  }

  // --- Chats & Geraete ------------------------------------------------------------------------------------------
  function settings() {
    var box = overlay(T('Chats & Geräte'), { wide: true });
    var list = el('ul', 'ct-devs'); box.appendChild(el('h3', null, T('Geräte mit Zugriff auf deine Chats'))); box.appendChild(list);
    api('GET', '/api/chat/me?device=' + q(dev())).then(function (me) {
      S.me = me;
      var act = me.devices.filter(function (d) { return !d.revoked; }), lim = me.limits || {};
      if (lim.devices_max) {                             // 2.5: "8 von 10 Geräten" - an der Grenze rot
        var cnt = el('p', 'hk-hint ct-dev-count' + (act.length >= lim.devices_max ? ' err' : ''),
          T('{n} von {max} Geräten', { n: act.length, max: lim.devices_max }) + (act.length >= lim.devices_max ? ' – ' + T('Entferne ein altes Gerät, um ein neues hinzuzufügen.') : ''));
        list.parentNode.insertBefore(cnt, list);
      }
      var old = Date.now() / 1000 - 60 * 86400;
      act.forEach(function (d) {
        var li = el('li', 'ct-dev'), t = el('div', 'ct-dev-t');
        t.appendChild(el('b', null, d.name || T('Unbekanntes Gerät')));
        if (d.current) t.appendChild(el('span', 'ct-chip', T('dieses Gerät')));
        else if ((d.last_seen || d.created) < old) { li.classList.add('ct-dev-old'); t.appendChild(el('span', 'ct-chip ct-chip-old', T('seit über 60 Tagen nicht genutzt'))); }
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
    if (!K) {                                            // 2.5: von der Einrichtung hierher (Geraetegrenze) - danach weiter
      var nb = el('div', 'hk-actions'); nb.appendChild(btn(T('Dieses Gerät einrichten'), 'btn-primary ct-dev-setup', newDevice));
      box.appendChild(nb);
      return;
    }
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
    // Datenschutz (2.4): Gelesen-Bestaetigungen und Online-Status - je getrennt, gegenseitig
    if (hasFeat('receipts')) {
      box.appendChild(el('h3', null, T('Datenschutz')));
      [['receipts', T('Gelesen-Bestätigungen senden'), T('Aus: Andere sehen nicht, ob du ihre Nachrichten gelesen hast – und du siehst es bei ihnen auch nicht.')],
       ['online', T('Online-Status zeigen'), T('Aus: Deine Chat-Partner sehen nicht, ob und wann du zuletzt online warst – und du siehst es bei ihnen auch nicht.')]].forEach(function (x) {
        var lab = el('label', 'ct-priv ct-priv-' + x[0]), cb = el('input'); cb.type = 'checkbox'; cb.checked = priv()[x[0]] !== false;
        lab.appendChild(cb); var tx = el('span'); tx.appendChild(el('b', null, x[1])); tx.appendChild(el('span', 'hk-hint', x[2])); lab.appendChild(tx);
        cb.addEventListener('change', function () {
          var b = {}; b[x[0]] = cb.checked; cb.disabled = true;
          api('PUT', '/api/chat/privacy', b).then(function (r) {
            if (S.me) S.me.privacy = r; S.rcpts = {}; S.rcSent = {};
            if (S.cur) { loadReceipts(S.cur); drawMsgs(); } presenceSync();
          }).catch(function (e) { cb.checked = !cb.checked; HK.toast(e.message); }).finally(function () { cb.disabled = false; });
        });
        box.appendChild(lab);
      });
    }
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
  // Testrunde Web Nr. 032: "vor 12 Sekunden" in der Liste mitlaufen lassen
  setInterval(function () {
    if (document.hidden) return;
    [].forEach.call(document.querySelectorAll('#ct-list .ct-item-time[data-ts]'), function (x) {
      var v = HK.relTime(+x.dataset.ts - Date.now() / 1000); if (x.textContent !== v) x.textContent = v;
    });
  }, 30000);
  function ready(fresh) {
    // 2.5 (Nr. 063): gesichertes Vertrauen holen - auf einem gerade eingerichteten Geraet VOR dem ersten Laden (TOFU),
    // sonst nebenher (anderes eigenes Geraet hat etwas als ueberprueft markiert)
    if (fresh) { trustPull().then(function () { ready(false); trustSoon(); }); return; }
    loadAI();
    fedCerts().catch(function () {});                      // 2.3: Zertifikate v2 fuer Chats ueber Servergrenzen (still)
    S.ready = true;
    loadList().then(function () { route(); });
    checkLinks();
    if (fresh === undefined) trustPull().then(trustRefresh);
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
          if (st) return { st: st };
          return h2uGet(h).then(function (u) {            // 2.5: noch kein Handle-Vertrauen - aus dem v1-Stand uebernehmen
            return u ? kvGet(trustKey(u)).catch(function () { return null; }).then(function (v1) { return { v1: v1 }; }) : {};
          });
        }).then(function (x) {
          var t;
          if (x.v1) { t = X.tofuMigrate(x.v1, k.ident_pub, k.ver); if (t.store) t.store.migrated = true; }   // nur bei gleichem Schluessel
          else t = X.tofuV2(x.st || null, k.ident_pub, k.ver);
          out[h] = { keys: k, trust: t.result, stale: !!e.stale, stored: t.store || x.st || null };
          if (t.store) return kvSetT(trust2Key(h), t.store).catch(function () {});
        });
      })).then(function () { return out; });
    });
  }
  // 2.5: Handle -> Benutzername aus Chats v1 (members[].handle, nur fuer Clients der Stufe 3) - damit das Vertrauen
  // trust:<benutzer> bei gleichem Identitaetsschluessel zu trust2:<handle> wird (tests/vectors/chat_v3.json, tofu_migrate)
  var H2U = 'h2u:' + ME, h2u = null;
  function h2uLoad() { if (!h2u) h2u = kvGet(H2U).catch(function () { return null; }).then(function (x) { return x || {}; }); return h2u; }
  function h2uGet(h) { return h2uLoad().then(function (m) { return m[h] || null; }); }
  function h2uNote(chats) {
    var add = [];
    (chats || []).forEach(function (c) {
      if (isFed(c)) return;
      (c.members || []).forEach(function (m) { if (!m.me && m.handle && m.username && m.username !== m.handle) add.push([m.handle, m.username]); });
    });
    if (!add.length) return;
    h2uLoad().then(function (m) {
      var ch = false;
      add.forEach(function (x) { if (m[x[0]] !== x[1]) { m[x[0]] = x[1]; ch = true; } });
      if (ch) kvSet(H2U, m).catch(function () {});
    });
  }
  // "Ueberprueft" (Sicherheitsnummer verglichen) bzw. Warnung bestaetigen ("Verstanden": verified false, Warnung weg)
  function fedVerify(h, ident_pub, verified) {
    return kvGet(trust2Key(h)).catch(function () { return null; }).then(function (st) {
      return kvSetT(trust2Key(h), { ident: ident_pub, verified: !!verified, dver: (st && st.ident === ident_pub && st.dver) || 0 });
    });
  }
  // --- 2.5 (Testrunde Web Nr. 063): Vertrauen (trust:/trust2:) verschluesselt auf dem Server sichern -------------------
  // Sonst ist "überprüft" nach der Wiederherstellung auf einem neuen Geraet weg. Verschluesselt mit einem Schluessel aus dem
  // Backup-Schluessel (X.sealTrust) - der Server kann weder lesen noch "überprüft" unterschieben. Zusammenfuehren: was hier
  // fehlt, kommt dazu; gleicher Identitaetsschluessel -> "überprüft" bleibt erhalten; anderer -> der Stand hier gilt (TOFU).
  function trustPrefixes() { return ['trust:' + ME + ':', 'trust2:' + ME + ':']; }
  function trustMerge(loc, rem) {
    if (!rem || typeof rem !== 'object' || typeof rem.ident !== 'string') return null;
    if (!loc) return rem;
    if (loc.ident !== rem.ident) return null;
    var out = null;
    if (rem.verified === true && !loc.verified && !loc.changed) out = Object.assign({}, loc, { verified: true });
    if ((+rem.dver || 0) > (+loc.dver || 0)) out = Object.assign({}, out || loc, { dver: +rem.dver });
    return out;
  }
  function trustLocal() {
    var all = {};
    return Promise.all(trustPrefixes().map(function (p, i) {
      return kvAll(p).then(function (m) { Object.keys(m).forEach(function (k) { all[(i ? 't2:' : 't1:') + k.slice(p.length)] = m[k]; }); });
    })).then(function () { return all; });
  }
  function trustKeyOf(n) { return n.indexOf('t2:') === 0 ? trust2Key(n.slice(3)) : n.indexOf('t1:') === 0 ? trustKey(n.slice(3)) : null; }
  // Stand vom Server holen und hier einfuegen -> {remote: {...} | null, changed: n}
  function trustPull() {
    if (!K || !K.backup_sk) return Promise.resolve({ remote: null, changed: 0 });
    return api('GET', '/api/chat/trust').then(function (r) {
      if (!r || !r.blob) return { remote: null, changed: 0 };
      var rem;
      try { rem = X.openTrust(ME, K.backup_sk, r.blob); } catch (e) { return { remote: null, changed: 0 }; }   // alter Schluessel o. Ae.
      var items = (rem && rem.items) || {}, n = 0;
      return trustLocal().then(function (loc) {
        return Promise.all(Object.keys(items).map(function (name) {
          var k = trustKeyOf(name), m = k && trustMerge(loc[name], items[name]);
          if (!m) return null;
          n++;
          if (name.indexOf('t1:') === 0) { var u = name.slice(3); trust[u] = m; trustP[u] = Promise.resolve(m); }
          else { var f = (S.fk || {})[name.slice(3)]; if (f && f.keys && f.keys.ident_pub === m.ident) f.stored = m; }
          return kvSet(k, m).catch(function () {});
        }));
      }).then(function () { return { remote: items, changed: n }; });
    }).catch(function () { return { remote: null, changed: 0, err: true }; });   // Ladefehler: nicht ueberschreiben
  }
  var trustT = null, trustRun = null;
  function trustSoon() { clearTimeout(trustT); trustT = setTimeout(trustPush, 1500); }
  function trustPush() {
    if (trustRun) { trustSoon(); return; }
    trustRun = trustPull().then(function (p) {
      // Server-Stand nicht geladen (Netz/5xx): nicht mit dem lokalen Stand ueberschreiben - sonst gehen Eintraege
      // anderer eigener Geraete verloren; beim naechsten Anlass erneut
      if (p.err) return null;
      return trustLocal().then(function (loc) {
        if (p.remote && canon(p.remote) === canon(loc)) return null;
        if (!Object.keys(loc).length) return null;
        return api('PUT', '/api/chat/trust', { blob: X.sealTrust(ME, K.backup_sk, { v: 1, at: Date.now(), items: loc }) });
      });
    }).catch(function () {}).then(function () { trustRun = null; });
  }
  function canon(x) {                                 // JSON mit sortierten Schluesseln (Vergleich)
    if (!x || typeof x !== 'object') return JSON.stringify(x);
    if (Array.isArray(x)) return '[' + x.map(canon).join(',') + ']';
    return '{' + Object.keys(x).sort().map(function (k) { return JSON.stringify(k) + ':' + canon(x[k]); }).join(',') + '}';
  }
  function kvSetT(k, v) { return kvSet(k, v).then(function (x) { trustSoon(); return x; }); }
  function trustRefresh(p) { if (p && p.changed) { drawWarn(); drawHead(); } return p; }

  window.SagaChatFed = { certs: fedCerts, keys: fedKeys, verify: fedVerify,
                         safety: function (myHandle, theirHandle, theirIdentPk) { return X.safetyNumberV2(myHandle, K.ident_pk, theirHandle, theirIdentPk); } };

  // --- 2.5: Seitenpanel (static/chatdock.js) und "Seite in Chat teilen" -------------------------------------------
  // Im Panel (/chats?panel=1, eingebettet neben Seiten/Heute) oeffnen sich Seiten-Links im umgebenden Fenster; das
  // umgebende Fenster schickt "Seite teilen" und "Eingabe fokussieren" per postMessage (nur gleiche Herkunft).
  var PANEL = document.body.classList.contains('ct-panel') && window.parent !== window;
  function shareStart(n) {
    if (!n || !(+n.id > 0)) return;
    S.share = { id: +n.id, title: String(n.title || T('Unbenannt')).slice(0, 200) };
    if (S.cur) { closeChat(); if (location.hash) history.replaceState(null, '', location.pathname + location.search); }
    var old = $('ct-share'); if (old) old.remove();
    var bar = el('div', 'ct-share'); bar.id = 'ct-share'; bar.setAttribute('role', 'status');
    bar.appendChild(el('span', 'ct-share-t', T('Chat wählen, um „{title}“ zu teilen', { title: S.share.title })));
    var x = el('button', 'icon-btn ct-share-x', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Abbrechen'));
    x.addEventListener('click', function () { S.share = null; bar.remove(); });
    bar.appendChild(x);
    var list = $('ct-list'); if (list) list.parentNode.insertBefore(bar, list);
  }
  function shareApply() {
    var sh = S.share; S.share = null; var bar = $('ct-share'); if (bar) bar.remove();
    if (!sh) return;
    var t = $('ct-input'), tok = '[[note:' + sh.id + ']] ';
    if (t.value.indexOf('[[note:' + sh.id + ']]') < 0) t.value = (t.value && !/\s$/.test(t.value) ? t.value + ' ' : t.value) + tok;
    autosize(); setTimeout(function () { t.focus(); t.setSelectionRange(t.value.length, t.value.length); }, 0);
  }
  (function () {                                           // /chats?share=ID&title=… (Handy, ohne Panel)
    var q = new URLSearchParams(location.search), id = +q.get('share');
    if (id > 0) {
      S.share = { id: id, title: (q.get('title') || T('Unbenannt')).slice(0, 200) };
      q.delete('share'); q.delete('title'); var rest = q.toString();
      history.replaceState(null, '', location.pathname + (rest ? '?' + rest : '') + location.hash);
      setTimeout(function () { if (S.share && !S.cur) shareStart(S.share); }, 0);
    }
  })();
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || e.source !== window.parent || !e.data || typeof e.data !== 'object') return;
    if (e.data.t === 'saga-share') shareStart(e.data.note);
    else if (e.data.t === 'saga-focus') { var t = S.cur ? $('ct-input') : ($('ct-filter') || $('ct-list')); if (t) t.focus(); }
  });
  // A11Y-32: Esc im Panel (Fokus steckt im Rahmen) schliesst das Panel im Hauptfenster - nur, wenn hier nichts anderes offen ist
  if (PANEL) document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || e.defaultPrevented) return;
    if (document.querySelector('.menu:not([hidden]),.hk-modal,.ct-ov,.hk-ask,.ct-picker:not([hidden])') || !$('ct-reply').hidden || !$('ct-searchbar').hidden) return;
    try { window.parent.postMessage({ t: 'saga-close' }, location.origin); } catch (x) { /* egal */ }
  });
  if (PANEL) document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href]'); if (!a || e.defaultPrevented) return;
    var u; try { u = new URL(a.getAttribute('href'), location.href); } catch (x) { return; }
    if (u.origin !== location.origin || u.pathname === '/chats' || a.hasAttribute('download') || /^\/(files|_m|api)\//.test(u.pathname)) return;
    e.preventDefault();                                    // Seite/Heute im umgebenden Fenster oeffnen
    window.parent.postMessage({ t: 'saga-open', href: u.pathname + u.search + u.hash }, location.origin);
  }, true);

  function route() {
    var h = decodeURIComponent(location.hash.slice(1) || ''), m;
    if ((m = /^c(\d+)$/.exec(h))) openChat(+m[1]);
    else if ((m = /^p(-?\d+)$/.exec(h))) create({ kind: 'page', note_id: +m[1] });      // negativ: Seite eines anderen Servers (2.3)
    else if ((m = /^u(.+)$/.exec(h))) create({ kind: 'dm', user: m[1] });
    else if (h === 'settings') settings();
  }
  window.addEventListener('hashchange', function () { if (S.ready) route(); });

  // isFed: Krypto saga-chat/v2 (an Handles gebunden) - 2.3 Chats ueber Servergrenzen (v 2), 2.5 Stufe 3 auch lokal (v 3).
  // isRemote: wirklich ueber Servergrenzen (Herkunft zeigen, Anfragen, Heim-Server) - nur v 2.
  function isFed(c) { return !!(c && (c.v === 2 || c.v === 3)); }
  function isRemote(c) { return !!(c && c.v === 2); }
  // AD fuer Anhaenge: v1 chat_id, v2 chat_ref; umgestellte Chats (2.5): Nachrichten von vor der Umstellung weiter v1
  function fileCid(c, m) { return isFed(c) && !(m && !m.sender_handle && c.v3) ? c.ref : c.id; }
  function chatName(c) {
    if (c.kind === 'free') return c._title || c.members.filter(function (m) { return !m.me; }).map(function (m) { return m.name; }).join(', ') || T('Gruppen-Chat');
    return c.name || '';
  }
  function freeTitle(c) {
    grpInfo(c);                                         // 2.5 (groupinfo2): Beschreibung und Bild
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
        if (S.cur && S.cur.id === c.id) { S.cur._title = t; drawHead(); if (GI && GI.id === c.id && ov) GI.draw(); }
        drawList();
      }
    }).catch(function () { /* Name bleibt die Mitgliederliste */ });
  }
  function kindIc(c) { return c.kind === 'group' ? '#' : c.kind === 'page' ? '📄' : c.kind === 'free' ? '👥' : ''; }
  function loadList() {
    return api('GET', '/api/chats?device=' + q(dev())).then(function (r) {
      S.chats = r.chats || []; S.hiddenN = r.hidden_count || 0;
      if (S.hidOpen || ($('ct-filter').value || '').trim()) hiddenLoad();
      S.chats.forEach(freeTitle);
      h2uNote(S.chats);
      drawList();
      presenceSoon();
      if (S.cur) {
        var c = S.chats.filter(function (x) { return x.id === S.cur.id; })[0];
        if (!c && !(S.hid || []).some(function (x) { return x.id === S.cur.id; })) closeChat(); else if (c) { S.cur = Object.assign(S.cur, c); drawHead(); }
      }
    }).catch(function (e) { if (e.status === 403) fatal(e.message); });
  }
  function drawList() {
    var ul = $('ct-list'), f = ($('ct-filter').value || '').trim().toLowerCase();
    ul.textContent = '';
    var shown = S.chats.filter(function (c) { return !f || chatName(c).toLowerCase().indexOf(f) >= 0; });
    $('ct-list-empty').hidden = S.chats.length > 0 || !!S.hiddenN;
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
    hiddenSection(ul, f);                                     // 2.5 (chathide1): ausgeblendete Gruppen-/Seiten-Chats
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
      var lav = c._av ? grpAvatar(c, 'ct-av-k', kindIc(c)) : avatar(kindIc(c) || (nm.charAt(0) || '?').toUpperCase(), c.kind === 'dm' ? (c.peer || {}).username : 'c' + c.id, c.kind !== 'dm' ? 'ct-av-k' : '',
                       c.kind === 'dm' ? (c.peer || {}).avatar : null);
      var lp = c.kind === 'dm' ? S.pres[(c.peer || {}).username] : null;
      if (lp && lp.online) { lav.classList.add('ct-on'); b.title = T('online'); }       // (2.4) Online-Status im Direkt-Chat
      b.appendChild(lav);
      var t = el('span', 'ct-item-t');
      var tn = el('span', 'ct-item-n', nm); t.appendChild(tn);
      if (c.pinned) { var pi = el('span', 'ct-pin', '📌'); pi.title = T('Angeheftet'); pi.setAttribute('aria-label', T('Angeheftet')); tn.prepend(pi); }
      t.appendChild(el('span', 'ct-item-s', isRemote(c) && c.kind === 'dm'
        ? (c.request ? T('Anfrage') : T('Direkt')) + ' · ' + ((c.peer || {}).host || '') + (c.readonly ? ' · ' + T('schreibgeschützt') : '') + (c.muted ? ' · 🔕' : '')
        : isRemote(c)                                      // Gruppe/Seiten-Chat ueber Servergrenzen (2.3)
        ? (c.request ? T('Anfrage') : c.kind === 'page' ? T('Seite') : T('Gruppe')) + ' · ' + T('{n} Personen', { n: c.member_count })
          + (c.home_self ? '' : ' · ' + (c.home || '')) + (c.readonly ? ' · ' + T('schreibgeschützt') : '') + (c.muted ? ' · 🔕' : '')
        : (c.kind === 'dm' ? T('Direkt') : c.kind === 'group' ? T('Gruppe') : c.kind === 'page' ? T('Seite') : T('Gruppe')) +
          ' · ' + T('{n} Personen', { n: c.member_count }) + (c.muted ? ' · 🔕' : '')));
      if (c.id !== (S.cur || {}).id && (c._draft || hasDraft(c.id))) t.appendChild(el('span', 'ct-item-s ct-draft', T('Entwurf')));    // 2.5
      b.appendChild(t);
      var side = el('span', 'ct-item-r');
      var tm = el('span', 'ct-item-time', HK.relTime((c.last_at || c.created) - Date.now() / 1000)); tm.dataset.ts = String(c.last_at || c.created);
      side.appendChild(tm);
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
      var c = r.chat; freeTitle(c); h2uNote([c]);
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
  // --- Kontakte (2.5, contacts1): Personen aus Direkt-Chats und freien Gruppen, auch nach Loeschen des Chats ---------------
  // GET /api/chat/contacts -> {contacts: [{ref, name, sub, avatar, remote, host, ready, gone}]}; ref ist lokal der
  // Such-Handle ('~…', wie users[].username), fremd '~x@host' (wie remote[].handle). Nie ein Anmeldename.
  var CONTACTS_MAX = 30;
  function loadContacts() {
    return api('GET', '/api/chat/contacts?limit=' + CONTACTS_MAX).then(function (r) { return r.contacts || []; }, function () { return []; });
  }
  // Knopf "⋯" neben einem Kontakt: Menue "Aus Kontakten entfernen" (ausgeblendet, bis zu einem neuen gemeinsamen Chat)
  function contactMore(li, ref, name, done) {
    var m = el('button', 'icon-btn ct-contact-more', '⋯'); m.type = 'button';
    m.setAttribute('aria-label', T('Optionen für {name}', { name: name })); m.setAttribute('aria-haspopup', 'menu');
    m.addEventListener('click', function (e) {
      e.stopPropagation();
      HK.openMenu(m, function (menu) {
        menu.appendChild(HK.menuItem(T('Aus Kontakten entfernen'), function () {
          api('DELETE', '/api/chat/contacts/' + q(ref)).then(function () {
            li.remove(); HK.toast(T('{name} aus den Kontakten entfernt.', { name: name })); if (done) done();
          }).catch(function (e2) { HK.toast(e2.message); });
        }, { ic: 'x', danger: true }));
      });
    });
    li.classList.add('ct-contact'); li.appendChild(m);
    return m;
  }
  // Eintrag der Personensuche/Kontaktliste: u = {key, name, sub, avatar, remote, gone, contact, ref}; pick(u) beim Klick
  function personRow(u, pick) {
    var li = el('li'), b = el('button', 'ct-person' + (u.remote ? ' ct-remote' : '') + (u.gone ? ' ct-gone' : '')); b.type = 'button';
    b.appendChild(avatar((u.name || '?').charAt(0).toUpperCase(), u.key, '', u.avatar));
    var t = el('span', 'ct-item-t'); t.appendChild(el('span', 'ct-item-n', u.name));          // 2.4 (W4): nie den Anmeldenamen zeigen
    if (u.sub) t.appendChild(el('span', 'ct-item-s', u.sub));
    b.appendChild(t);
    if (u.gone) { b.disabled = true; b.title = T('Gerade nicht erreichbar'); }
    else b.addEventListener('click', function () { pick(u); });
    li.appendChild(b);
    if (u.contact && u.ref) contactMore(li, u.ref, u.name);
    return li;
  }
  // Kontakt (GET /api/chat/contacts) -> Eintrag fuer personRow
  function contactItem(k) {
    var sub = k.remote ? T('Person auf {host}', { host: k.host || k.sub }) : [k.sub || '', k.ready ? '' : T('hat Chats noch nicht eingerichtet')].filter(Boolean).join(' · ');
    if (k.gone) sub = (k.remote ? T('Server {host} nicht verbunden', { host: k.host || k.sub }) : T('Konto nicht mehr aktiv'));
    return { key: k.ref, ref: k.ref, name: k.name, sub: sub, avatar: k.avatar, remote: !!k.remote, gone: !!k.gone, contact: true,
             username: k.ref, handle: k.remote ? k.ref : null };
  }
  // Ueberschrift + Kontakte (ohne Eingabe): zuletzt zuerst, hoechstens CONTACTS_MAX; keep(k) filtert (z. B. schon Mitglied)
  function drawContacts(res, pick, keep) {
    return loadContacts().then(function (ks) {
      ks = ks.filter(function (k) { return !keep || keep(k); });
      res.textContent = '';
      if (!ks.length) return 0;
      res.appendChild(el('li', 'ct-people-h', T('Kontakte')));
      ks.forEach(function (k) { res.appendChild(personRow(contactItem(k), pick)); });
      return ks.length;
    });
  }
  function newChat() {
    var box = overlay(T('Neue Unterhaltung'));
    var inp = el('input', 'hk-in ct-people-q'); inp.type = 'search'; inp.placeholder = T('Person oder Gruppe suchen'); inp.setAttribute('aria-label', T('Person oder Gruppe suchen'));
    box.appendChild(inp);
    var res = el('ul', 'ct-people ct-people-list'); box.appendChild(res);
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
    // Auswahl einer Person (Suche oder Kontakt): Gruppe -> Chip, sonst Direkt-Chat (lokal per user, fremd per handle)
    function choose(u) {
      if (!grp.hidden) {
        var key = u.remote ? u.handle : u.username;
        if (!pick.some(function (p) { return (p.remote ? p.handle : p.username) === key; }))
          pick.push(u.remote ? { username: u.handle, handle: u.handle, name: u.label || u.name, remote: true } : { username: u.username, name: u.name });
        drawChips(); inp.value = ''; showContacts(); inp.focus(); return;
      }
      create(u.remote ? { kind: 'dm', handle: u.handle } : { kind: 'dm', user: u.username });
    }
    function showContacts() { drawContacts(res, choose).catch(function () { res.textContent = ''; }); }
    showContacts();                                     // 2.5: ohne Eingabe die Kontakte (zuletzt zuerst)
    var tmr = null;
    inp.addEventListener('input', function () {
      clearTimeout(tmr);
      tmr = setTimeout(function () {
        var v = inp.value.trim();
        if (!v) { showContacts(); return; }                // 1-2 Zeichen: nur genaue Treffer (Server), z. B. "hd" - Kontakte als Teilwort
        api('GET', '/api/chat/people?q=' + q(v)).then(function (r) {
          if (inp.value.trim() !== v) return;              // inzwischen weitergetippt
          res.textContent = '';
          r.users.forEach(function (u) {                   // Kontakte stehen vorne (contact: true)
            var sub = [u.sub || '', u.ready ? '' : T('hat Chats noch nicht eingerichtet')].filter(Boolean).join(' · ');   // sub: fuer Gleichnamige
            res.appendChild(personRow({ key: u.username, ref: u.username, name: u.name, sub: sub, avatar: u.avatar, contact: !!u.contact, username: u.username }, choose));
          });
          r.groups.forEach(function (g) {
            var li = el('li'), b = el('button', 'ct-person'); b.type = 'button';
            b.appendChild(avatar('#', 'g' + g.name, 'ct-av-k'));
            var t = el('span', 'ct-item-t'); t.appendChild(el('span', 'ct-item-n', g.name)); t.appendChild(el('span', 'ct-item-s', T('Gruppen-Chat für alle in der Gruppe'))); b.appendChild(t);
            b.addEventListener('click', function () { create({ kind: 'group', group: g.name }); });
            li.appendChild(b); res.appendChild(li);
          });
          (r.remote || []).forEach(function (u) {                // 2.3: Adressbuch verbundener Server -> Direkt-Chat ueber Servergrenzen
            // 2.3: in der Gruppe mit Personen anderer Server ist dieser Server das Heim
            res.appendChild(personRow({ key: u.handle, ref: u.handle, name: u.label || u.name, avatar: u.avatar, remote: true, contact: !!u.contact,
                                        handle: u.handle, label: u.label,
                                        sub: u.contact ? T('Person auf {host}', { host: u.host }) : T('Person auf {host} · erste Nachricht als Anfrage', { host: u.host }) }, choose));
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
    return { body: body, verified: ok, key: key };                // key: fuer Reaktionen (2.5)
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
      if (!t) { t = { ident: ident, verified: false, changed: false }; kvSetT(trustKey(u), t).catch(function () {}); }
      else if (t.ident !== ident) { t = { ident: ident, verified: false, changed: true, old: t.ident }; kvSetT(trustKey(u), t).catch(function () {}); }
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
        var t = trust[m.username]; t.changed = false; kvSetT(trustKey(m.username), t).catch(function () {}); drawWarn();
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
      if (isRemote(c)) box.appendChild(el('p', 'hk-hint', T('{name} ist auf {host} – die Nummer gehört zu diesem Server-übergreifenden Chat.', { name: m.name, host: m.host || '' })));
      if (f.stored && f.stored.migrated) box.appendChild(el('p', 'hk-hint ct-migrated', T('Neue Darstellung, gleicher Schlüssel: Die Nummer sieht anders aus als früher, gehört aber zum selben Schlüssel, den du schon kanntest.')));
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
        kvSetT(trustKey(m.username), nt).then(function () { closeOverlay(); drawWarn(); drawHead(); HK.toast(T('Als überprüft markiert.')); });
      }));
    }).catch(function (e) { HK.toast(e.message); });
  }

  // --- Unterhaltung -----------------------------------------------------------------------------------------------
  function closeChat() {
    draftSave(); unfReset();
    vplDrop(); recCancel();
    S.cur = null; S.msgs = []; S.byId = {}; S.seenSeq = 0; S.rc = null; S.reply = null; S.files = []; S.rcpts = {};
    selEnd(true);
    $('ct-chat').hidden = true; $('ct-none').hidden = false; root.classList.remove('ct-open');
    drawList();
  }
  function openChat(cid) {
    if (!S.ready) return Promise.resolve();
    var same = S.cur && S.cur.id === cid;
    return api('GET', '/api/chats/' + cid + '?device=' + q(dev())).then(function (r) {
      var c = r.chat; freeTitle(c); h2uNote([c]);
      if (!same) { draftSave(); selEnd(true); vplDrop(); recCancel(); S.msgs = []; S.byId = {}; S.seenSeq = 0; S.rc = null; S.reply = null; S.files = []; S.fk = null; S.rcpts = {}; drawReply(); drawFiles(); $('ct-input').value = ''; S.mentions = {}; S.people = {}; unfReset(); }
      if (same && S.cur && S.cur._title && !c._title) c._title = S.cur._title;   // bis der (neue) Name entschluesselt ist: kein Flackern
      S.cur = c;
      $('ct-none').hidden = true; $('ct-chat').hidden = false; root.classList.add('ct-open');
      drawHead(); drawList(); drawWarn();
      if (!same) draftLoad(c.id);                        // 2.5: Entwurf dieses Chats (nur dieser Browser)
      if (S.share) shareApply();                         // 2.5: "Seite in Chat teilen" (Seitenpanel/?share=)
      if (GI && GI.id === c.id && ov) GI.draw();         // 2.5: offene Gruppeninfo aktualisieren (live)
      c.members.forEach(function (m) { if (!m.me && !trust[m.username]) trustP[m.username] = kvGet(trustKey(m.username)).then(function (t) { if (t) trust[m.username] = t; drawWarn(); return t; }).catch(function () { return null; }); });
      checkIdents(c);
      presenceSoon();
      return loadMsgs(false).then(function () { loadReceipts(c); });
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
    if (c.kind === 'dm' && isRemote(c)) {                 // 2.3: Person eines anderen Servers - Herkunft, TOFU-Stand
      var fp = c.peer || {}, ff = (S.fk || {})[fp.handle];
      sub.appendChild(document.createTextNode(T('auf {host}', { host: fp.host || '' })));
      if (ff && ff.trust === 'trust' && ff.stored && ff.stored.verified && ff.stored.ident === ff.keys.ident_pub)
        sub.appendChild(el('span', 'ct-chip ct-verified', '✓ ' + T('überprüft')));
      else if (ff && (ff.trust === 'block' || ff.trust === 'reject')) sub.appendChild(el('span', 'ct-chip ct-blocked', '⛔ ' + T('gesperrt')));
      if (c.request) sub.appendChild(el('span', 'ct-chip', T('Anfrage')));
      if (c.readonly) sub.appendChild(el('span', 'ct-chip', '🔒 ' + T('schreibgeschützt')));
      presenceChip(sub, fp.username);
    } else if (c.kind === 'dm') {
      var p = others[0] || c.peer || {}, t = trust[p.username];   // 2.4 (W4): Anmeldename nicht im Kopf zeigen
      var f3 = isFed(c) && (S.fk || {})[p.handle || (c.peer || {}).handle];     // 2.5 Stufe 3: Vertrauen ueber den Handle
      if (f3 ? f3.trust === 'trust' && f3.stored && f3.stored.verified && f3.stored.ident === f3.keys.ident_pub : t && t.verified && !t.changed)
        sub.appendChild(el('span', 'ct-chip ct-verified', '✓ ' + T('überprüft')));
      else if (f3 && (f3.trust === 'block' || f3.trust === 'reject')) sub.appendChild(el('span', 'ct-chip ct-blocked', '⛔ ' + T('gesperrt')));
      presenceChip(sub, p.username);
    } else {
      var mine = String(c.my_handle || '').split('@').slice(1).join('@');
      // Testrunde Web Nr. 058: "du" zuerst statt alphabetisch dazwischen
      sub.appendChild(document.createTextNode(c.members.filter(function (m) { return m.me; }).concat(c.members.filter(function (m) { return !m.me; })).map(function (m) {
        return m.me ? T('du') : m.name + (isRemote(c) && m.host && m.host !== mine ? ' (' + m.host + ')' : '');
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
    x3Info(sub, c);                                       // 2.5: warum (noch) keine Reaktionen
    drawPins(c);                                          // 2.5 (msgpin1): Leiste angeheftete Nachrichten
    document.title = chatName(c) + ' · ' + T('Chats');
    if (window.HKCalls) window.HKCalls.head(c);           // Anruf-Knoepfe (static/calls.js); 2.4: auch Gruppenanruf
    micDraw();
    drawAI();
    drawState(c);
  }
  // Nachrichtenanfrage (Annehmen/Ablehnen/Sperren) bzw. schreibgeschuetzt: statt des Eingabefelds (2.3)
  function drawState(c) {
    var box = $('ct-state');
    if (!box) { box = el('div', 'ct-state'); box.id = 'ct-state'; box.setAttribute('role', 'region'); $('ct-compose').parentNode.insertBefore(box, $('ct-compose')); }
    box.textContent = '';
    var req = isFed(c) && c.request, ro = isFed(c) && c.readonly && !req;
    var mute = !req && !ro && c.can_send === false;      // 2.5 (groupinfo2): nur Admins duerfen schreiben
    box.hidden = !req && !ro && !mute; $('ct-compose').hidden = req || ro || mute;
    if (mute) {
      box.appendChild(el('p', 'hk-hint ct-perm-note', c.blocked ? '⛔ ' + T('Du hast diese Person gesperrt. Entsperren über das Chat-Menü (⋯).')
        : '🔒 ' + T('In dieser Gruppe dürfen nur Admins schreiben.')));
      return;
    }
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
        menu.appendChild(HK.menuItem(T('{name} sperren', { name: p.name || '?' }), function () { HK.closeMenu(); act('block', 'person'); }, { ic: 'ban', danger: true }));
        menu.appendChild(HK.menuItem(T('Ganzen Server {host} sperren', { host: p.host || '?' }), function () { HK.closeMenu(); act('block', 'instance'); }, { ic: 'ban', danger: true }));
      });
    });
    if (c.kind !== 'dm' && p.remote === false) blk.hidden = true;     // eingeladen von einer Person dieses Servers: nur ablehnen
    bar.appendChild(blk); box.appendChild(bar);
  }
  function loadMsgs(older) {
    var c = S.cur; if (!c) return Promise.resolve();
    if (S.loading) { if (!older) S.loadAgain = true; return Promise.resolve(); }   // 2.5: Ereignis waehrend des Ladens nicht verlieren
    S.loading = true;
    var first = S.msgs.length ? S.msgs[0].seq : 0, last = S.msgs.length ? S.msgs[S.msgs.length - 1].seq : 0;
    var url = '/api/chats/' + c.id + '/messages?calls=1&tombs=1&device=' + q(dev()) + (older && first ? '&before=' + first : (!older && last ? '&after=' + last : ''));
    // v2: Schluessel der Absender (selbst geprueft) vor dem Entschluesseln - sonst stuende kurz "Echtheit nicht bestaetigt"
    return (isFed(c) && !S.fk ? fedLoad(c).catch(function () { return null; }) : Promise.resolve()).then(function () { return api('GET', url); }).then(function (r) {
      S.loading = false;
      if (!S.cur || S.cur.id !== c.id) { if (S.loadAgain && S.cur) { S.loadAgain = false; return loadMsgs(false); } return; }
      var lh = locHidden(c.id);                         // 2.5: aelterer Server - nur auf diesem Geraet ausgeblendet
      var add = r.messages.filter(function (m) { return !S.byId[m.id] && !lh[m.id]; }).map(function (m) {
        var d;
        if (m.fstate === 'pending' && S.okIds && S.okIds[m.id]) { m.fstate = null; if (!m.fseq && S.okIds[m.id] !== true) m.fseq = S.okIds[m.id]; }
        if (m.kind === 'call') {                          // Anruf-Eintrag: Metadaten vom Server (nicht verschluesselt, nur wer/wann/wie lange)
          try { m.call = JSON.parse(m.body_ct); } catch (e) { m.call = {}; }
          if (isFed(c) && m.sender && m.sender.username === ME) m.call.from = ME;     // v2: Anrufer steht als Prinzipal/Konto drin
          m.body = null; m.verified = true; S.byId[m.id] = m; return m;
        }
        if (m.kind === 'tomb') { m.body = null; m.verified = true; S.byId[m.id] = m; return m; }    // 2.5: von einem Admin geloescht
        if (m.kind === 'sys') {                           // Systemzeile (2.3, z. B. KI ein-/ausgeschaltet) - Metadaten vom Heim
          try { m.sys = JSON.parse(m.body_ct); } catch (e) { m.sys = {}; }
          m.body = null; m.verified = true; S.byId[m.id] = m; return m;
        }
        // v2-Chat: Nachrichten ohne sender_handle stammen aus der Zeit vor der Umstellung (Seiten-Chat, 2.3) - v1 mit chat_id
        try { d = isFed(c) && m.sender_handle ? decryptV2(c, m) : decrypt(c.id, m, r.devices || {}, r.idents || {}); } catch (e) { d = { body: null, verified: false, error: true }; }
        m.body = d.body; m.verified = d.verified; m.error = !!d.error; m._ai = aiCheck(m);
        if (d.key && m.sender_handle) m._k = d.key;
        if (m.edit && m.body) edApply(c, m);              // 2.5: bearbeitet - neuester Text
        S.byId[m.id] = m; return m;
      });
      if (older) { S.msgs = add.concat(S.msgs); S.more = r.more; }
      else { S.msgs = S.msgs.concat(add); if (!last) S.more = r.more; if (!last || !r.more) S.seenSeq = Math.max(S.seenSeq || 0, r.last_seq || 0); }
      drawMsgs(older ? 'keep' : 'bottom');
      rxOpen(c, add);
      resolveNotes();
      markRead();
      if (!older && r.more && last) return loadMsgs(false);
      if (S.loadAgain) { S.loadAgain = false; return loadMsgs(false); }
    }).catch(function (e) { S.loading = false; S.loadAgain = false; HK.toast(T('Nachrichten nicht geladen: {err}', { err: e.message })); });
  }
  // Gelesen bis zur neuesten geladenen Nachricht - oder bis zum Stand der Unterhaltung beim Laden (S.seenSeq): auch
  // Nachrichten, die dieses Geraet nie lesen kann (vor der Einrichtung geschickt), zaehlen sonst ewig als ungelesen (BUG-050)
  function markRead() {
    var c = S.cur; if (!c || document.hidden) return;
    var seq = Math.max(S.msgs.length ? S.msgs[S.msgs.length - 1].seq : 0, S.seenSeq || 0);
    var want = receiptWant(c), wait = want ? 2000 - (Date.now() - (S.rcAt[c.id] || 0)) : 0;
    if (want && wait > 0) {                              // (2.4) hoechstens eine Bestaetigung je Chat und 2 s
      clearTimeout(S.rcT); S.rcT = setTimeout(markRead, wait + 50); want = null;
    }
    if ((!seq || seq <= (c.last_read || 0)) && !want && !c.unread_mark) return;
    var body = { seq: Math.max(seq, c.last_read || 0) };
    if (want) {
      try { body.receipt = { keys: receiptKeys(c, want) }; } catch (e) { body.receipt = null; }
      if (!body.receipt || !Object.keys(body.receipt.keys).length) delete body.receipt;
      else S.rcAt[c.id] = Date.now();
    }
    if (!body.receipt && seq <= (c.last_read || 0) && !c.unread_mark) return;
    c.last_read = body.seq; c.unread_mark = false;                 // 2.5: "als ungelesen markiert" endet mit dem Lesen
    api('POST', '/api/chats/' + c.id + '/read', body).then(function (r) {
      var x = S.chats.filter(function (y) { return y.id === c.id; })[0]; if (x) { x.unread = 0; x.last_read = body.seq; x.unread_mark = false; drawList(); }
      if (body.receipt) {
        if (r.receipt === 'ok' || r.receipt === 'none') S.rcSent[c.id] = want.n;
        else if (r.receipt === 'off') { if (S.me) S.me.privacy = Object.assign({}, S.me.privacy, { receipts: false }); }
        else if (r.receipt === 'rate') { clearTimeout(S.rcT); S.rcT = setTimeout(markRead, 5000); }
      }
    }).catch(function () {});
  }

  // --- Gelesen-Bestaetigungen und Online-Status (2.4) -----------------------------------------------------------------
  // Bestaetigung "gelesen bis Nachricht id (Position n)": vom Geraet signiert, je Mitglied fuer dessen Backup-Schluessel
  // versiegelt - der Server sieht nur, DASS es eine gibt. Position n = seq (Chat auf diesem Server) bzw. fseq (Heim).
  function priv() { return (S.me && S.me.privacy) || { receipts: true, online: true }; }
  function hasFeat(f) { return !!(S.me && (S.me.features || []).indexOf(f) >= 0); }
  function ordOf(c, m) { return isFed(c) ? (m.fseq || 0) : (m.seq || 0); }
  function rcOk(c) { return hasFeat('receipts') && priv().receipts && !(isFed(c) && (c.request || c.readonly || c.receipts_ok === false)); }
  function receiptWant(c) {
    if (!rcOk(c) || !K) return null;
    for (var i = S.msgs.length - 1; i >= 0; i--) {     // neueste Nachricht mit Position, die nicht von mir ist
      var m = S.msgs[i], n = ordOf(c, m);
      if (!n || (m.sender && m.sender.username === ME && m._ai !== 'ok')) continue;
      return n > (S.rcSent[c.id] || 0) ? { id: m.id, n: n } : null;
    }
    return null;
  }
  function receiptKeys(c, want) {
    var body = { id: want.id, n: want.n, ts: Date.now() }, keys = {};
    var o = { chat: isFed(c) ? c.ref : String(c.id), reader: isFed(c) ? c.my_handle : ME, device: K.device.id, sign_sk: K.device.sign_sk, body: body, v2: isFed(c) };
    if (isFed(c)) {
      c.members.forEach(function (m) {
        var f = (S.fk || {})[m.handle];
        if (m.me || !m.handle || !f || !f.keys || f.trust === 'reject' || f.trust === 'block') return;
        keys['backup:' + m.handle] = X.sealReceipt(Object.assign({ to_pk: f.keys.backup_pk }, o));
      });
    } else if (S.rc && S.rc.cid === c.id) {
      S.rc.raw.forEach(function (p) {
        if (!p.ready || p.username === ME) return;
        keys['backup:' + p.username] = X.sealReceipt(Object.assign({ to_pk: ub(p.backup_pub, 32) }, o));
      });
    } else {                                             // Empfaenger (gepruefte Schluessel) erst laden, dann erneut
      recipients(c.id).then(function () { setTimeout(markRead, 0); }).catch(function () {});
    }
    return keys;
  }
  // Signierendes Geraet einer lesenden Person -> Ed25519-Schluessel (nur gepruefte Geraete) oder null
  function readerKey(c, from, handle, dev) {
    if (isFed(c)) {
      var f = (S.fk || {})[handle];
      if (!f || !f.keys || f.trust === 'reject') return null;
      var d = f.keys.devices.filter(function (x) { return x.id === dev; })[0];
      return d ? ub(d.sign_pub, 32) : null;
    }
    var p = S.rc && S.rc.cid === c.id ? S.rc.raw.filter(function (x) { return x.username === from && x.ready; })[0] : null;
    var dv = p ? p.devices.filter(function (x) { return x.id === dev; })[0] : null;
    return dv ? ub(dv.sign_pub, 32) : null;
  }
  // h2u (optional): Handle -> Benutzername aus Chats v1 - fuer Bestaetigungen, die noch vor der Umstellung auf Stufe 3
  // gesendet wurden (Format v1, signiert mit Chat-Nummer und Benutzername; Nr. 054)
  function takeReceipts(c, list, h2uMap) {
    var changed = false;
    (list || []).forEach(function (x) {
      try {
        var r = X.openReceipt(x.sealed, K.backup_pk, K.backup_sk), pk = readerKey(c, x.from, x.handle, r.dev), v1 = false;
        if (!pk) return;
        if (!X.verifyReceipt(isFed(c) ? c.ref : String(c.id), isFed(c) ? x.handle : x.from, r, pk, isFed(c))) {
          var u = isFed(c) && v3Seq(c) && h2uMap && h2uMap[x.handle];
          if (!u || !X.verifyReceipt(String(c.id), u, r, pk, false)) return;
          v1 = true;
        }
        var b = JSON.parse(r.body), old = S.rcpts[x.from];
        if (typeof b.n !== 'number') return;
        if (v1) { if (!old) { S.rcpts[x.from] = { n: 0, n1: b.n, id: b.id, ts: b.ts }; changed = true; } return; }
        if (old && old.n > b.n) return;
        S.rcpts[x.from] = { n: b.n, n1: old && old.n1, id: b.id, ts: b.ts };
        changed = true;
      } catch (e) { /* nicht lesbar/ungueltig: ignorieren */ }
    });
    return changed;
  }
  // Umstellpunkt eines auf Stufe 3 umgestellten Chats (letzte Nachricht v1) oder 0
  function v3Seq(c) { return (c && c.v3 && +c.v3.seq) || 0; }
  function loadReceipts(c) {
    if (!hasFeat('receipts') || !K || !priv().receipts) return;
    var keysP = isFed(c) ? (S.fk ? Promise.resolve() : fedLoad(c)) : recipients(c.id);
    Promise.all([api('GET', '/api/chats/' + c.id + '/receipts'), keysP, v3Seq(c) ? h2uLoad() : null]).then(function (a) {
      if (!S.cur || S.cur.id !== c.id) return;
      S.rcpts = {};
      if (takeReceipts(c, a[0].receipts, a[2])) drawMsgs();
    }).catch(function () {});
  }
  // Wer hat Nachricht m gelesen? -> [{name, ts}] (ohne mich)
  function readersOf(c, m) {
    // 2.5 (Nr. 054): Nachricht von vor der Umstellung auf Stufe 3 (ohne fseq): gelesen, wenn die Person danach schon
    // etwas bestaetigt hat (Stufe 3 kommt nach allem davor) oder ihre Bestaetigung v1 so weit reicht
    var old = isFed(c) && !m.fseq && m.seq && v3Seq(c) && m.seq <= v3Seq(c), n = old ? m.seq : ordOf(c, m), out = [];
    if (!n) return out;
    c.members.forEach(function (x) {
      var r = !x.me && S.rcpts[x.username];
      if (r && (old ? r.n > 0 || (r.n1 || 0) >= n : r.n >= n)) out.push({ name: x.name || x.username, ts: r.ts });
    });
    return out;
  }
  function tickEl(c, m) {
    var others = c.members.filter(function (x) { return !x.me; }).length, rd = rcOk(c) ? readersOf(c, m) : [];
    var all = others > 0 && rd.length >= others, grp = c.kind !== 'dm';
    var tk = el(grp ? 'button' : 'span', 'ct-tick' + (rd.length ? ' ct-read' : '') + (all ? ' ct-all' : ''), rd.length ? '✓✓' : '✓');
    if (grp && rd.length && !all) tk.appendChild(el('span', 'ct-tick-n', String(rd.length)));
    var lbl = !rd.length ? T('Zugestellt') : grp ? (all ? T('Von allen gelesen') : T('Gelesen von {n}', { n: rd.length }))
      : T('Gelesen {when}', { when: rd[0].ts ? fDay.format(new Date(rd[0].ts)) + ' ' + fTime.format(new Date(rd[0].ts)) : '' });
    tk.title = lbl; tk.setAttribute('aria-label', lbl);
    if (grp) { tk.type = 'button'; tk.addEventListener('click', function () { readBy(c, m); }); }
    return tk;
  }
  function readBy(c, m) {
    var box = overlay(T('Gelesen von'));
    var rd = readersOf(c, m), ul = el('ul', 'ct-devs ct-readby');
    if (!rd.length) ul.appendChild(el('li', 'hk-hint', T('Noch von niemandem gelesen.')));
    rd.sort(function (a, b) { return (b.ts || 0) - (a.ts || 0); }).forEach(function (r) {
      var li = el('li', 'ct-dev'), t = el('div', 'ct-dev-t');
      t.appendChild(el('b', null, r.name));
      if (r.ts) t.appendChild(el('span', 'hk-hint', fDay.format(new Date(r.ts)) + ' ' + fTime.format(new Date(r.ts))));
      li.appendChild(t); ul.appendChild(li);
    });
    box.appendChild(ul);
    box.appendChild(el('p', 'hk-hint', T('Wer Gelesen-Bestätigungen abgeschaltet hat, erscheint hier nicht.')));
  }
  // Online-Status: nur sichtbare Direkt-Chats abonnieren (offener Chat + Liste), alle 60 s erneuern, solange sichtbar
  var presT = null, presAt = 0;
  function presenceSoon() { clearTimeout(presT); presT = setTimeout(presenceSync, 400); }
  function presenceWant() {
    if (!hasFeat('online') || !priv().online) return [];
    var out = [];
    function add(c) {
      if (!c || c.kind !== 'dm' || c.request || (isFed(c) && c.online_ok === false)) return;
      var u = (c.peer || {}).username; if (u && out.indexOf(u) < 0) out.push(u);
    }
    add(S.cur);
    S.chats.filter(function (c) { return !c.archived; }).slice(0, 30).forEach(add);
    return out.slice(0, 50);
  }
  function presenceSync() {
    if (!S.ready || document.hidden) return;
    var users = presenceWant();
    if (!users.length) { if (Object.keys(S.pres).length) { S.pres = {}; drawList(); drawHead(); } return; }
    presAt = Date.now();
    api('POST', '/api/chat/presence', { users: users }).then(function (r) {
      var nx = {};
      Object.keys(r.users || {}).forEach(function (u) { nx[u] = r.users[u]; });
      S.pres = nx;
      // Personen anderer Server: der eigene Server fragt dort erst nach (bzw. erneut, z. B. gleich nach dem Annehmen einer
      // Nachrichtenanfrage) - fehlende eine Minute lang alle 10 s nachfragen, danach im normalen Takt
      var miss = users.some(function (u) { return u.charAt(0) === '@' && !nx[u]; });
      S.presMiss = miss ? (S.presMiss || 0) + 1 : 0;
      clearTimeout(S.presRe); if (miss && S.presMiss <= 6) S.presRe = setTimeout(presenceSync, 10000);
      if (r.enabled === false && S.me) S.me.privacy = Object.assign({}, S.me.privacy, { online: false });
      drawList(); drawHead();
    }).catch(function () {});
  }
  setInterval(function () { if (S.ready && !document.hidden && Date.now() - presAt > 60000) presenceSync(); }, 15000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden && Date.now() - presAt > 30000) presenceSync(); });
  function presenceChip(sub, u) {
    var p = u && S.pres[u];
    if (!p) return;
    if (p.online) { var on = el('span', 'ct-chip ct-online', T('online')); sub.appendChild(on); return; }
    if (p.last) sub.appendChild(el('span', 'ct-chip ct-lastseen', T('zuletzt online {when}', { when: HK.relTime(p.last - Date.now() / 1000) })));
  }
  document.addEventListener('visibilitychange', function () { if (!document.hidden) markRead(); });

  // --- Nachrichten zeichnen (nur textContent; Links nur http/https, Seitenverweise nur mit Zugriff) ------------------
  var TOKEN = /\[\[note:([1-9][0-9]{0,17})\]\]|(https?:\/\/[^\s<>"'`]+[^\s<>"'`.,;:!?)\]])|<@(~[a-z2-7]{16}(?:@[a-z0-9.:\-]{1,260})?)>/g;
  // 2.5 (chatfmt1): Markdown-light ueber static/chat-fmt.js (nur Elemente/textContent); Textstuecke zeichnet textLeaf
  function renderText(box, text) {
    if (window.SagaChatFmt) window.SagaChatFmt.render(box, text, textLeaf); else textLeaf(box, text);
  }
  function textLeaf(box, text) {
    var s = String(text || ''), last = 0, m;
    TOKEN.lastIndex = 0;
    while ((m = TOKEN.exec(s))) {
      if (m.index > last) box.appendChild(document.createTextNode(s.slice(last, m.index)));
      if (m[3]) box.appendChild(mentionEl(m[3]));             // 2.5 (chatmention1): <@kennung>
      else if (m[1]) {
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
  // 2.5: Vorschaukarte fuer verlinkte Seiten (hoechstens 3 je Nachricht) - Titel/Symbol nur mit eigenem Zugriff
  // (/api/chat/notes); ohne Zugriff keine Karte, der Verweis im Text sagt dann "kein Zugriff"
  function pageCards(b, text) {
    var r = /\[\[note:([1-9][0-9]{0,17})\]\]/g, x, seen = {}, n = 0;
    while ((x = r.exec(text)) && n < 3) {
      if (seen[x[1]]) continue; seen[x[1]] = 1; n++;
      var a = el('a', 'ct-pcard'); a.dataset.note = x[1]; a.hidden = true;
      a.appendChild(el('span', 'ct-pcard-ic')); var t = el('span', 'ct-pcard-t'); t.appendChild(el('b', 'ct-pcard-n')); t.appendChild(el('span', 'ct-pcard-s')); a.appendChild(t);
      fillCard(a); b.appendChild(a);
    }
  }
  function fillCard(a) {
    var n = S.notes[a.dataset.note]; if (!n) { a.hidden = true; return; }
    a.hidden = false; a.href = '/notes#' + a.dataset.note;
    var ic = a.querySelector('.ct-pcard-ic'); ic.textContent = '';
    if (n.icon && n.icon.charAt(0) === '/') { var im = el('img'); im.alt = ''; im.src = n.icon; ic.appendChild(im); } else ic.textContent = n.icon || '📄';
    a.querySelector('.ct-pcard-n').textContent = n.title;
    a.querySelector('.ct-pcard-s').textContent = n.updated ? T('Seite · geändert {when}', { when: HK.relTime(n.updated - Date.now() / 1000) }) : T('Seite');
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
      document.querySelectorAll('#ct-msgs .ct-pcard').forEach(fillCard);
    }).catch(function () {});
  }
  function callText(m) {
    var k = m.call || {}, out = k.from === ME, v = k.media === 'video';
    var what = k.group ? (v ? T('Gruppen-Videoanruf') : T('Gruppenanruf')) : v ? T('Videoanruf') : T('Anruf');   // 2.4: Gruppe
    if (k.status === 'ended') {
      var sec = Math.max(0, k.dur | 0), h = Math.floor(sec / 3600), mi = Math.floor(sec % 3600 / 60), p2 = function (x) { return (x < 10 ? '0' : '') + x; };
      return what + ' · ' + (h ? h + ':' + p2(mi) : mi) + ':' + p2(sec % 60) + (k.group && k.n ? ' · ' + T('{n} Personen', { n: k.n }) : '');
    }
    if (k.group) return out ? T('{what} – niemand ist beigetreten', { what: what }) : (v ? T('Verpasster Gruppen-Videoanruf') : T('Verpasster Gruppenanruf'));
    if (k.status === 'declined') return T('{what} abgelehnt', { what: what });
    if (k.status === 'busy') return out ? T('Besetzt') : T('Verpasster Anruf (du hast telefoniert)');
    if (k.status === 'failed') return T('{what} – keine Verbindung', { what: what });
    return out ? T('{what} – nicht angenommen', { what: what }) : (v ? T('Verpasster Videoanruf') : T('Verpasster Anruf'));
  }
  function preview(m) {
    if (!m) return T('Nachricht nicht mehr da');
    if (m.kind === 'call') return '📞 ' + callText(m);
    if (m.error || !m.body) return T('Nicht entschlüsselbar');
    var t = plainMentions(fmtStrip(String(m.body.text || ''))).replace(/\[\[note:\d+\]\]/g, '📄').replace(/\s+/g, ' ').trim();
    if (!t && m.body.preview && m.body.preview.url) t = '🔗 ' + String(m.body.preview.title || m.body.preview.url);
    var vf = (m.body.files || []).filter(function (f) { return f && f.voice; })[0];
    if (!t && vf) t = '🎤 ' + (VOICE ? VOICE.fmt(vf.dur) : '');           // 2.5: Sprachnachricht
    if (!t && (m.body.files || []).length) t = '📎 ' + m.body.files.map(function (f) { return f.n; }).join(', ');
    return t.length > 90 ? t.slice(0, 90) + '…' : t;
  }
  // 2.5 (medien1): Zitat/Antwortleiste bei Anhang ohne Text: kleines Vorschaubild + "Foto"/"Video"/Dateiname
  function quoteMedia(c, m) {
    if (!c || !m || m.error || !m.body || String(m.body.text || '').trim() || m.body.preview) return null;
    var fs = (Array.isArray(m.body.files) ? m.body.files : []).filter(Boolean);
    if (!fs.length || fs[0].voice) return null;        // Sprachnachricht: bleibt bei "🎤 0:12" (preview)
    var f = fs[0], vid = !!(VID && VID.isVideo(f)), img = !vid && IMG.test(f.m || '');
    var label = img ? T('Foto') : vid ? T('Video') : '📎 ' + String(f.n || T('Datei')).slice(0, 90);
    if (fs.length > 1) label += ' ' + T('(+{n} weitere)', { n: fs.length - 1 });
    var thumb = null;
    if ((img && (f.x !== 2 || (f.th && f.th.id && f.th.k))) || (vid && f.th && f.th.id && f.th.k)) {
      thumb = el('img', 'ct-q-th'); thumb.alt = ''; thumb.decoding = 'async';
      var cid = fileCid(c, m), u = memGet('t:' + f.id);
      if (u) thumb.src = u;
      else thumbUrl(cid, f).then(function (x) { thumb.src = x; }, function () { thumb.remove(); });
    }
    return { label: label, thumb: thumb };
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
      if (m.kind === 'call') { var cr = callRow(m, d, mine); if (S.sel) cr.classList.add('ct-nosel'); box.appendChild(cr); lastWho = null; return; }
      if (m.kind === 'sys') { var sr = sysRow(m, d); if (S.sel) sr.classList.add('ct-nosel'); box.appendChild(sr); lastWho = null; return; }
      if (m.kind === 'tomb') { var tr = tombRow(m, d); if (S.sel) tr.classList.add('ct-nosel'); box.appendChild(tr); lastWho = null; return; }
      if (!mine && grpBlocked(c, m)) { var bk = blockedRow(m, d); if (bk) { box.appendChild(bk); lastWho = null; return; } }
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
        var qm = quoteMedia(c, o), qt = qd;
        if (qm) { qd.classList.add('ct-q-media'); if (qm.thumb) qd.appendChild(qm.thumb); qt = el('span', 'ct-q-t'); qd.appendChild(qt); }
        qt.appendChild(el('b', null, o && o._ai === 'ok' ? aiP().name : o && o.sender ? o.sender.name : '…')); qt.appendChild(el('span', null, qm ? qm.label : preview(o)));
        qd.addEventListener('click', function () { var t = box.querySelector('.ct-msg[data-id="' + m.reply_to + '"]'); if (t) { t.scrollIntoView({ block: 'center' }); t.classList.add('flash'); setTimeout(function () { t.classList.remove('flash'); }, 1200); } });
        b.appendChild(qd);
      }
      if (m.error || !m.body) b.appendChild(el('div', 'ct-text ct-err', '🔒 ' + T('Diese Nachricht lässt sich auf diesem Gerät nicht entschlüsseln.')));
      else {
        if (m.body.text) { var tx = el('div', 'ct-text'); renderText(tx, m.body.text); b.appendChild(tx); }
        if (m.body.preview) { var pv = unfCard(c, m); if (pv) b.appendChild(pv); }        // 2.5 (unfurl1): Linkvorschau
        else if (m.body.text) pageCards(b, m.body.text);                                   // 2.5: Vorschaukarte fuer [[note:ID]]
        if (mentionsMe(c, m)) row.classList.add('ct-ment');
        (Array.isArray(m.body.files) ? m.body.files : []).slice(0, 10).forEach(function (f) { b.appendChild(fileEl(fileCid(c, m), f)); });
      }
      if (m._rx && m._rx.length) b.appendChild(rxChips(c, m));
      var meta = el('div', 'ct-meta');
      if (m._ed) { var ed = el('span', 'ct-edited', T('bearbeitet')); var dd = new Date(m._ed * 1000); ed.title = T('Bearbeitet: {time}', { time: fDay.format(dd) + ' ' + fTime.format(dd) }); meta.appendChild(ed); }
      if (m.fstate === 'pending') { var pe = el('span', 'ct-pend', '⏳'); pe.title = T('Wird zugestellt, sobald der andere Server erreichbar ist.'); meta.appendChild(pe); }
      if (m.fstate === 'failed') { row.classList.add('ct-failed'); meta.appendChild(el('span', 'ct-fail', '⚠ ' + T('nicht zugestellt'))); }
      if (m.expires_at) meta.appendChild(el('span', 'ct-exp', '⏱'));
      var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); tm.title = fDay.format(d) + ' ' + fTime.format(d); meta.appendChild(tm);
      if (mine && !m.fstate && !m.error) meta.appendChild(tickEl(c, m));       // (2.4) ✓ zugestellt, ✓✓ gelesen
      b.appendChild(meta);
      row.appendChild(b);
      var act = el('button', 'icon-btn ct-act', '⋯'); act.type = 'button'; act.setAttribute('aria-label', T('Nachricht …')); act.setAttribute('aria-haspopup', 'menu');
      act.addEventListener('click', function () { msgMenu(act, m); });
      if (canReact(c, m)) {                               // 2.5: Reaktion (Schnellleiste)
        var rb = el('button', 'icon-btn ct-act ct-react-b', '☺'); rb.type = 'button'; rb.setAttribute('aria-label', T('Reagieren')); rb.title = T('Reagieren');
        rb.setAttribute('aria-haspopup', 'menu');
        rb.addEventListener('click', function () { rxBar(rb, m); });
        row.appendChild(rb);
      }
      row.appendChild(act);
      selRow(row, m);                                     // 2.5: Auswahlmodus
      box.appendChild(row);
    });
    if (S.aiPending && S.aiPending.cid === c.id) box.appendChild(S.aiPending.el);   // wartende KI-Antwort bleibt unten
    applySearch();
    if (scroll === 'keep') box.scrollTop = box.scrollHeight - prevH + prevTop;
    else if (scroll === 'bottom' || atBottom) box.scrollTop = box.scrollHeight;
  }
  // Bughunt D18: Touch (kein Hover) - "··· ☺" nur an der angetippten Nachricht (.ct-sel), statt dauerhaft unter jeder;
  // Tippen auf Links, Bilder, Dateien, Zitate oder Knoepfe in der Blase bleibt, was es war
  var NOHOVER = window.matchMedia ? window.matchMedia('(hover: none)') : null;
  $('ct-msgs').addEventListener('click', function (e) {
    if (!NOHOVER || !NOHOVER.matches) return;
    var row = e.target.closest('.ct-msg'); if (!row || !row.querySelector('.ct-act')) return;
    if (e.target.closest('a, button, input, textarea, video, audio, .ct-img, .ct-file')) return;
    var on = !row.classList.contains('ct-sel');
    document.querySelectorAll('#ct-msgs .ct-msg.ct-sel').forEach(function (r) { r.classList.remove('ct-sel'); });
    if (on) row.classList.add('ct-sel');
  });
  // Systemzeile (2.3): mittig, z. B. "Anna hat die KI in diesem Chat eingeschaltet"
  // Person in Systemzeilen: Name mit Herkunft (Server), ich selbst als "du"
  function sysWho(x, acc) {
    if (!x) return '';
    if (acc && x.handle && x.handle === (S.cur || {}).my_handle) return T('dich');      // "… hat dich hinzugefuegt"
    var mine = String((S.cur || {}).my_handle || '').split('@').slice(1).join('@');     // eigener Server: ohne Zusatz
    var raw = /^~[a-z2-7]{16}$/.test(String(x.name || ''));                         // D01: Kennung -> "Person auf …" (Herkunft steht drin)
    if (raw) return HK.personName(x.name, x.host && x.host !== mine ? x.host : '');
    return (x.name || '?') + (x.host && x.host !== mine ? ' (' + x.host + ')' : '');
  }
  function sysText(k) {
    if (k.type === 'ai') {
      var who = k.by === (S.cur || {}).my_handle ? T('Du') : (HK.personName(k.name) || '?');
      return k.on ? T('{name} hat die KI in diesem Chat eingeschaltet.', { name: who }) : T('{name} hat die KI in diesem Chat ausgeschaltet.', { name: who });
    }
    if (k.type === 'crypto') return T('Dieser Chat ist jetzt an die stabilen Saga-Adressen gebunden (neue Verschlüsselungsstufe).') +
      (k.verify ? ' ' + T('Bitte die Sicherheitsnummern neu vergleichen – sie werden jetzt anders angezeigt.') : '');     // 2.5
    if (k.type === 'fed') return T('Ab jetzt mit Personen anderer Saga-Server (Ende-zu-Ende verschlüsselt).') + (k.ai_off ? ' ' + T('Die KI ist hier jetzt aus.') : '');
    if (k.type === 'admin') {                            // 2.5 (Gruppeninfo): Admin vergeben/entzogen
      var meA = !!(k.by && k.by.handle && k.by.handle === (S.cur || {}).my_handle), self = !!(k.by && k.target && k.by.handle === k.target.handle);
      var tg = sysWho(k.target, true), byA = k.by && !meA ? sysWho(k.by, false) : '';
      if (self) return k.on ? (meA ? T('Du bist jetzt Admin.') : T('{name} ist jetzt Admin.', { name: byA }))
        : (meA ? T('Du bist kein Admin mehr.') : T('{name} ist kein Admin mehr.', { name: byA }));
      if (k.on) return meA ? T('Du hast {name} zum Admin gemacht.', { name: tg }) : byA ? T('{by} hat {name} zum Admin gemacht.', { by: byA, name: tg })
        : T('{name} ist jetzt Admin.', { name: sysWho(k.target, false) });
      return meA ? T('Du hast {name} die Admin-Rolle entzogen.', { name: tg }) : byA ? T('{by} hat {name} die Admin-Rolle entzogen.', { by: byA, name: tg })
        : T('{name} ist kein Admin mehr.', { name: sysWho(k.target, false) });
    }
    if (k.type === 'perm') {                             // 2.5 (groupinfo2): Gruppenrechte geaendert
      var meP = !!(k.by && k.by.handle && k.by.handle === (S.cur || {}).my_handle), byP = meP ? T('Du') : (k.by ? sysWho(k.by, false) : T('Ein Admin'));
      var parts = [];
      if (k.perm_send) parts.push(k.perm_send === 'admins' ? T('Nur Admins dürfen jetzt schreiben.') : T('Alle dürfen jetzt schreiben.'));
      if (k.perm_info) parts.push(k.perm_info === 'admins' ? T('Nur Admins ändern jetzt Name, Bild und Beschreibung.') : T('Alle dürfen jetzt Name, Bild und Beschreibung ändern.'));
      return T('{name} hat die Gruppenrechte geändert.', { name: byP }) + (parts.length ? ' ' + parts.join(' ') : '');
    }
    if (k.type === 'members') {
      var acc = function (x) { return sysWho(x, true); }, nom = function (x) { return sysWho(x, false); };
      var meBy = !!(k.by && k.by.handle && k.by.handle === (S.cur || {}).my_handle), by = k.by && !meBy ? nom(k.by) : '';
      var add = (k.added || []).map(k.by ? acc : nom).join(', '), rm = (k.removed || []).map(k.by ? acc : nom).join(', ');
      var out = [];
      if (add) out.push(meBy ? T('Du hast {names} hinzugefügt.', { names: add }) : by ? T('{by} hat {names} hinzugefügt.', { by: by, names: add })
                             : (k.added || []).length > 1 ? T('{names} sind jetzt dabei.', { names: add }) : T('{names} ist jetzt dabei.', { names: add }));
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
      if (canReact(S.cur, m)) menu.appendChild(rxQuick(anchor, m));                 // 2.5: Reaktionen oben im Menue
      if (m.body) menu.appendChild(HK.menuItem(T('Antworten'), function () { HK.closeMenu(); S.reply = m; drawReply(); $('ct-input').focus(); }, { ic: 'reply' }));
      if (canEdit(S.cur, m)) menu.appendChild(HK.menuItem(T('Bearbeiten'), function () { HK.closeMenu(); editStart(m); }, { ic: 'edit' }));
      if (selectable(m)) menu.appendChild(HK.menuItem(T('Auswählen'), function () { HK.closeMenu(); selStart(m); }, { ic: 'checkbox' }));     // 2.5
      if (selectable(m)) menu.appendChild(HK.menuItem(T('Weiterleiten'), function () { HK.closeMenu(); fwdPick([m]); }, { ic: 'move' }));     // 2.5 (medien1)
      if (m.body && m.body.text) menu.appendChild(HK.menuItem(T('Text kopieren'), function () {
        HK.closeMenu(); if (navigator.clipboard) navigator.clipboard.writeText(m.body.text).then(function () { HK.toast(T('Kopiert.')); });
      }, { ic: 'copy' }));
      if (m.fstate === 'failed' && m.body && m.sender && m.sender.username === ME) menu.appendChild(HK.menuItem(T('Erneut senden'), function () {
        HK.closeMenu();
        sendBody(S.cur.id, m.body, 'msg', m.reply_to).then(function () {
          return api('DELETE', '/api/chats/' + S.cur.id + '/messages/' + m.id).catch(function () {});
        }).then(function () { dropMsg(m.id); return loadMsgs(false); }).catch(function (e) { HK.toast(T('Nicht gesendet: {err}', { err: e.message })); });
      }, { ic: 'refresh' }));
      grpMenu(menu, m);                                                            // 2.5: Anheften, Admin-Loeschen, Melden, Sperren
      if (m.sender && m.sender.username === ME) menu.appendChild(HK.menuItem(T('Für alle löschen'), function () {
        HK.closeMenu();
        HK.ask({ title: T('Nachricht für alle löschen?'), text: T('Die Nachricht und ihre Anhänge werden vom Server gelöscht. Wer sie schon gelesen hat, kann eine Kopie haben.'), ok: T('Löschen'), danger: true })
          .then(function (y) { if (y) api('DELETE', '/api/chats/' + S.cur.id + '/messages/' + m.id).then(function () { dropMsg(m.id); }).catch(function (e) { HK.toast(e.message); }); });
      }, { ic: 'trash', danger: true }));
    });
  }
  function dropMsg(id) {
    if (!S.byId[id]) return;
    delete S.byId[id]; S.msgs = S.msgs.filter(function (x) { return x.id !== id; });
    if (S.reply && S.reply.id === id) { S.reply = null; drawReply(); }
    if (S.sel) delete S.sel.ids[id];
    drawMsgs(); if (S.sel) selDraw();
  }
  // --- Mehrfachauswahl (2.5): Kopieren, Weiterleiten, In Seite uebernehmen, Loeschen (fuer mich / fuer alle) ----------
  // Start ueber "Auswaehlen" im Nachrichtenmenue bzw. Rechtsklick (Menue) oder langen Druck (Touch); danach waehlt Klick/
  // Antippen weitere aus oder ab, Umschalt+Klick einen Bereich, Leertaste die fokussierte Nachricht, Esc beendet.
  // Systemzeilen, Anrufe und nicht entschluesselte Nachrichten sind nicht auswaehlbar.
  var SEL_FWD_MAX = 50, SEL_DEL_MAX = 100, SEL_HIDE_MAX = 500;
  var fStamp = new Intl.DateTimeFormat(HK.locale, { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  var fDate = new Intl.DateTimeFormat(HK.locale, { day: '2-digit', month: '2-digit', year: 'numeric' });
  function selectable(m) { return !!(m && m.kind === 'msg' && !m.error && m.body); }
  function rowOf(id) { return $('ct-msgs').querySelector('.ct-msg[data-id="' + id + '"]'); }
  function selList() { return S.sel ? S.msgs.filter(function (m) { return S.sel.ids[m.id] && selectable(m); }) : []; }
  function selStart(m) {
    if (!selectable(m) || !S.cur) return;
    if (HK.menuOpen()) HK.closeMenu();
    S.sel = { ids: {}, anchor: m.id }; S.sel.ids[m.id] = 1;
    root.classList.add('ct-selecting');
    drawMsgs(); selDraw();
    var r = rowOf(m.id); if (r) r.focus({ preventScroll: true });
  }
  function selEnd(quiet) {
    if (!S.sel) return;
    S.sel = null; root.classList.remove('ct-selecting'); selDraw();
    if (!quiet && S.cur) drawMsgs();
  }
  function selToggle(m, range) {
    if (!S.sel || !selectable(m)) return;
    var a = S.sel.anchor && S.byId[S.sel.anchor] ? S.msgs.indexOf(S.byId[S.sel.anchor]) : -1;
    if (range && a >= 0) {
      var b = S.msgs.indexOf(m), lo = Math.min(a, b), hi = Math.max(a, b);
      for (var i = lo; i <= hi; i++) if (selectable(S.msgs[i])) S.sel.ids[S.msgs[i].id] = 1;
    } else {
      if (S.sel.ids[m.id]) delete S.sel.ids[m.id]; else S.sel.ids[m.id] = 1;
      S.sel.anchor = m.id;
    }
    selMark(); selDraw();
  }
  function selRow(row, m) {                             // drawMsgs: Zeile im Auswahlmodus
    if (!S.sel) return;
    if (!selectable(m)) { row.classList.add('ct-nosel'); return; }
    var on = !!S.sel.ids[m.id];
    row.setAttribute('role', 'option'); row.setAttribute('aria-selected', on ? 'true' : 'false'); row.tabIndex = 0;
    row.classList.toggle('ct-picked', on);
  }
  function selMark() {
    $('ct-msgs').querySelectorAll('.ct-msg[role="option"]').forEach(function (r) {
      var on = !!(S.sel && S.sel.ids[r.dataset.id]);
      r.classList.toggle('ct-picked', on); r.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }
  function selDraw() {
    var head = document.querySelector('#ct-chat .ct-head'), bar = $('ct-selbar'), act = $('ct-selact'), box = $('ct-msgs');
    if (!S.sel) {
      if (bar) bar.remove(); if (act) act.remove();
      box.setAttribute('role', 'log'); box.removeAttribute('aria-multiselectable');
      return;
    }
    box.setAttribute('role', 'listbox'); box.setAttribute('aria-multiselectable', 'true');
    if (!bar) {
      bar = el('div', 'ct-selbar'); bar.id = 'ct-selbar';
      var x = el('button', 'icon-btn ct-sel-x', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Auswahl beenden')); x.title = T('Auswahl beenden (Esc)');
      x.addEventListener('click', function () { selEnd(); });
      var n = el('span', 'ct-sel-n'); n.id = 'ct-sel-n'; n.setAttribute('role', 'status');
      bar.appendChild(x); bar.appendChild(n);
      var all = btn(T('Alle'), 'small ct-sel-all', function () {
        S.msgs.forEach(function (m) { if (selectable(m)) S.sel.ids[m.id] = 1; }); selMark(); selDraw();
      });
      bar.appendChild(all);
      head.appendChild(bar);
    }
    if (!act) {
      act = el('div', 'ct-selact'); act.id = 'ct-selact'; act.setAttribute('role', 'toolbar'); act.setAttribute('aria-label', T('Ausgewählte Nachrichten'));
      [['copy', '⧉', T('Kopieren'), selCopy], ['fwd', '↪', T('Weiterleiten'), selForward], ['page', '📄', T('In Seite übernehmen'), selToPage],
       ['del', '🗑', T('Löschen'), selDelete]].forEach(function (a) {
        var b = el('button', 'btn ct-sel-' + a[0] + (a[0] === 'del' ? ' btn-danger' : '')); b.type = 'button';
        b.appendChild(el('span', 'ct-sel-ic', a[1])); b.appendChild(el('span', 'ct-sel-t', a[2]));
        b.setAttribute('aria-label', a[2]); b.title = a[2];
        b.addEventListener('click', function () { a[3](); });
        act.appendChild(b);
      });
      $('ct-compose').parentNode.insertBefore(act, $('ct-compose'));
    }
    var k = selList().length;
    $('ct-sel-n').textContent = T('{n} ausgewählt', { n: k });
    act.querySelectorAll('button').forEach(function (b) { b.disabled = !k; });
  }
  function selName(m) { return m._ai === 'ok' ? aiP().name : m.sender ? m.sender.name : T('Gelöschtes Konto'); }
  function attText(f) {
    if (f && f.voice) return T('[Sprachnachricht]');
    if (f && IMG.test(f.m)) return T('[Bild]');
    return T('[Datei: {name}]', { name: String((f && f.n) || T('Datei')).slice(0, 200) });
  }
  function plainText(m) {
    var t = plainMentions(String((m.body && m.body.text) || '')).replace(/\[\[note:([1-9][0-9]{0,17})\]\]/g, function (_, i) {
      var n = S.notes[i]; return '📄 ' + (n ? n.title : T('Seite'));
    });
    var att = (Array.isArray(m.body && m.body.files) ? m.body.files : []).map(attText);
    return [t].concat(att).filter(Boolean).join(' ');
  }
  function stamp(m) { return fStamp.format(new Date(m.created * 1000)); }
  function selCopy() {
    var list = selList(); if (!list.length) return;
    var out = list.length === 1 ? plainText(list[0]) : list.map(function (m) { return selName(m) + ', ' + stamp(m) + ': ' + plainText(m); }).join('\n');
    function done() { HK.toast(T('{n} {n:Nachricht|Nachrichten} kopiert.', { n: list.length })); selEnd(); }
    function fallback() {
      var ta = el('textarea'); ta.value = out; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      var okc = false; try { okc = document.execCommand('copy'); } catch (e) { okc = false; }
      ta.remove();
      if (okc) done(); else HK.toast(T('Kopieren nicht möglich.'));
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(out).then(done, fallback); else fallback();
  }
  // Fehlt der Endpunkt (aelterer Server): FastAPI antwortet 404 mit "detail" bzw. 405 - Saga selbst 404 mit "error"
  function noRoute(e) { return !!e && (e.status === 405 || (e.status === 404 && !!(e.data && e.data.detail))); }
  function chunks(ids, n) { var out = []; for (var i = 0; i < ids.length; i += n) out.push(ids.slice(i, i + n)); return out; }
  function locKey(cid) { return 'ct-hide:' + ME + ':' + cid; }
  function locHidden(cid) {
    S.locHid = S.locHid || {};
    if (!S.locHid[cid]) { var m = {}; try { (JSON.parse(localStorage.getItem(locKey(cid)) || '[]') || []).forEach(function (i) { m[i] = 1; }); } catch (e) { /* egal */ } S.locHid[cid] = m; }
    return S.locHid[cid];
  }
  function locHide(cid, ids) {
    var m = locHidden(cid); ids.forEach(function (i) { m[i] = 1; });
    try { localStorage.setItem(locKey(cid), JSON.stringify(Object.keys(m).slice(-2000))); } catch (e) { /* egal */ }
  }
  function dropMany(ids) {
    var gone = {}; ids.forEach(function (i) { gone[i] = 1; delete S.byId[i]; if (S.sel) delete S.sel.ids[i]; });
    S.msgs = S.msgs.filter(function (x) { return !gone[x.id]; });
    if (S.reply && gone[S.reply.id]) { S.reply = null; drawReply(); }
    drawMsgs(); if (S.sel) selDraw();
  }
  function hideMsgs(c, ids) {                            // -> true (alle meine Geraete) | false (nur hier)
    return chunks(ids, SEL_HIDE_MAX).reduce(function (p, part) {
      return p.then(function () { return api('POST', '/api/chats/' + c.id + '/hide', { ids: part }); });
    }, Promise.resolve()).then(function () { return true; }, function (e) {
      if (!noRoute(e)) throw e;
      locHide(c.id, ids); return false;
    });
  }
  function deleteMsgs(c, ids) {                          // -> {deleted: [...], failed: n}
    var out = { deleted: [], failed: 0 };
    function single(part) {
      return part.reduce(function (p, id) {
        return p.then(function () {
          return api('DELETE', '/api/chats/' + c.id + '/messages/' + id).then(function () { out.deleted.push(id); }, function () { out.failed++; });
        });
      }, Promise.resolve());
    }
    return chunks(ids, SEL_DEL_MAX).reduce(function (p, part) {
      return p.then(function () {
        return api('POST', '/api/chats/' + c.id + '/messages/delete', { ids: part }).then(function (r) {
          out.deleted = out.deleted.concat(r.deleted || []); out.failed += Object.keys(r.failed || {}).length;
        }, function (e) { if (noRoute(e)) return single(part); throw e; });
      });
    }, Promise.resolve()).then(function () { return out; });
  }
  function selDelete() {
    var list = selList(), c = S.cur; if (!list.length || !c) return;
    var own = list.every(function (m) { return m.sender && m.sender.username === ME; }) && !(isFed(c) && c.readonly);
    var body = overlay(T('{n} {n:Nachricht|Nachrichten} löschen', { n: list.length }));
    body.parentNode.classList.add('ct-del-dlg');
    body.appendChild(el('p', null, T('„Für mich löschen“ blendet die Nachrichten auf allen deinen Geräten aus – die anderen sehen sie weiter.')));
    var bar = el('div', 'hk-actions ct-del-acts');
    bar.appendChild(btn(T('Für mich löschen'), 'ct-del-me', function () {
      closeOverlay();
      HK.ask({ title: T('Für dich löschen?'), text: T('{n} {n:Nachricht wird|Nachrichten werden} auf deinen Geräten ausgeblendet.', { n: list.length }), ok: T('Für mich löschen'), danger: true }).then(function (y) {
        if (!y) return;
        var ids = list.map(function (m) { return m.id; });
        hideMsgs(c, ids).then(function (all) {
          dropMany(ids); selEnd();
          HK.toast(all ? T('{n} {n:Nachricht|Nachrichten} für dich gelöscht.', { n: ids.length })
                       : T('{n} {n:Nachricht|Nachrichten} nur auf diesem Gerät ausgeblendet – der Server kann das noch nicht.', { n: ids.length }), null, null, all ? 3500 : 6000);
        }).catch(function (e) { HK.toast(e.message); });
      });
    }));
    if (own) bar.appendChild(btn(T('Für alle löschen'), 'btn-danger ct-del-all', function () {
      closeOverlay();
      HK.ask({ title: T('Für alle löschen?'), text: T('Die Nachrichten und ihre Anhänge werden vom Server gelöscht. Wer sie schon gelesen hat, kann eine Kopie haben.'), ok: T('Für alle löschen'), danger: true }).then(function (y) {
        if (!y) return;
        deleteMsgs(c, list.map(function (m) { return m.id; })).then(function (r) {
          dropMany(r.deleted); selEnd();
          HK.toast(r.failed ? T('{n} gelöscht, {k} nicht.', { n: r.deleted.length, k: r.failed }) : T('{n} {n:Nachricht|Nachrichten} für alle gelöscht.', { n: r.deleted.length }));
        }).catch(function (e) { HK.toast(e.message); });
      });
    }));
    body.appendChild(bar);
    if (!own) body.appendChild(el('p', 'hk-hint ct-del-hint', T('Nur eigene Nachrichten lassen sich für alle löschen.')));
    var f = bar.querySelector('button'); if (f) f.focus();
  }
  // Weiterleiten: Texte im Ziel neu verschluesselt (normaler Sendeweg), Bilder/Dateien hier entschluesselt und neu hochgeladen
  function selForward() { fwdPick(selList()); }
  function fwdPick(list) {                               // Mehrfachauswahl oder (2.5 medien1) eine Nachricht aus ihrem Menue
    if (!list.length) return;
    if (list.length > SEL_FWD_MAX) { HK.toast(T('Höchstens {n} Nachrichten auf einmal weiterleiten.', { n: SEL_FWD_MAX })); return; }
    var body = overlay(T('Weiterleiten an …'));
    var inp = el('input', 'hk-in ct-fwd-q'); inp.type = 'search'; inp.placeholder = T('Chat suchen'); inp.setAttribute('aria-label', T('Chat suchen'));
    var box = el('div', 'ct-fwd-list'); box.setAttribute('role', 'listbox'); box.setAttribute('aria-label', T('Chats'));
    body.appendChild(inp); body.appendChild(box);
    function draw() {
      var qq = inp.value.trim().toLowerCase(); box.textContent = '';
      var cand = S.chats.filter(function (c) {
        return !c.request && !c.readonly && !c.fed_hint && (!qq || String(chatName(c) || '').toLowerCase().indexOf(qq) >= 0);
      }).slice(0, 60);
      if (!cand.length) box.appendChild(el('p', 'hk-hint', T('Kein passender Chat.')));
      cand.forEach(function (c) {
        var b = el('button', 'ct-fwd-item'); b.type = 'button'; b.setAttribute('role', 'option');
        b.appendChild(el('span', 'ct-fwd-ic', kindIc(c) || '💬')); b.appendChild(el('span', 'ct-fwd-n', chatName(c) || T('Chat')));
        b.addEventListener('click', function () { forwardTo(list, c); });
        box.appendChild(b);
      });
    }
    inp.addEventListener('input', draw); draw(); inp.focus();
  }
  function forwardTo(list, target) {
    var name = chatName(target) || T('Chat');
    HK.ask({ title: T('Weiterleiten?'), text: T('{n} {n:Nachricht|Nachrichten} an „{chat}“ weiterleiten?', { n: list.length, chat: name }), ok: T('Weiterleiten') }).then(function (y) {
      if (!y) return;
      closeOverlay();
      var src = S.cur, n = 0, skipped = 0;
      HK.toast(T('Wird weitergeleitet …'), null, null, 60000);
      list.reduce(function (p, m) {
        return p.then(function () { return fwdOne(src, target, m).then(function (sk) { n++; skipped += sk; }); });
      }, Promise.resolve()).then(function () {
        selEnd();
        HK.toast(T('{n} {n:Nachricht|Nachrichten} weitergeleitet.', { n: n }) + (skipped ? ' ' + T('{n} {n:Anhang|Anhänge} nicht weitergeleitet.', { n: skipped }) : ''),
          T('Öffnen'), function () { location.hash = 'c' + target.id; }, 7000);
        if (S.cur && S.cur.id === target.id) loadMsgs(false);
        listSoon();
      }, function (e) { HK.toast(T('Weiterleiten abgebrochen ({n} gesendet): {err}', { n: n, err: e.message }), null, null, 7000); });
    });
  }
  // 2.5 (medien1): grosse Dateien (saga-file/v2) bis BIG_VIEW_MAX hier ganz entschluesseln und im Ziel neu hochladen
  // (uploadFiles waehlt v1/v2 nach Groesse); Sprachnachrichten nur in Chats, die sie koennen (wie das Mikrofon), als v2
  // mit Dauer und Wellenform. Was nicht geht, bleibt ein Hinweis "[Anhang nicht weitergeleitet]".
  function fwdVoiceOk(target, f) {
    var l = x3lim();
    return !!(VOICE && VID && x3(target) && hasFeat('voice') && f.x === 2 && !(l.voice_max_bytes && f.s > l.voice_max_bytes));
  }
  function fwdBigOk(target, f) {
    if (!VID || f.x !== 2 || !(f.s > 0) || f.s > BIG_VIEW_MAX) return false;
    var v1 = v1Max(target);
    return f.s <= v1 || !bigCheck(target, { size: f.s, name: String(f.n || '') }, v1);
  }
  function fwdOne(src, target, m) {
    var skipped = 0, voices = [];
    var files = (Array.isArray(m.body.files) ? m.body.files : []).slice(0, 10).filter(function (f) {
      if (!f) { skipped++; return false; }
      if (f.voice) { if (fwdVoiceOk(target, f)) voices.push(f); else skipped++; return false; }
      if (f.x === 2 ? !fwdBigOk(target, f) : (f.x && f.x !== 1)) { skipped++; return false; }
      return true;
    });
    function plain(f) {
      if (f.x === 2) return VID.plain(f);
      return getBlob(f).then(function (buf) { return run('dec', { cid: fileCid(src, m), blob: buf, key: f.k }, [buf]); }).then(function (r) { return r.plain; });
    }
    var vup = voices.map(function (f) {
      return VID.plain(f).then(function (p) {
        return VID.upload(target.id, new Blob([p], { type: f.m || 'audio/ogg' }));
      }).then(function (u) {
        var e = { id: u.id, k: u.key, n: String(f.n || 'voice.ogg').slice(0, 200), m: f.m || 'audio/ogg; codecs=opus', s: u.size, x: 2, cs: u.cs, voice: true };
        if (f.dur) e.dur = f.dur;
        if (f.wf) e.wf = f.wf;
        return e;
      }).catch(function () { skipped++; return null; });
    });
    return Promise.all(files.map(function (f) {
      return plain(f).then(function (p) {
        return new File([p], String(f.n || T('Datei')).slice(0, 200), { type: f.m || 'application/octet-stream' });
      }).catch(function () { skipped++; return null; });
    })).then(function (got) {
      got = got.filter(Boolean);
      return Promise.all([got.length ? uploadFiles(target.id, got, target) : [], Promise.all(vup)]);
    }).then(function (r) {
      var up = r[0].concat(r[1].filter(Boolean));
      var text = String(m.body.text || '');
      if (skipped) text = (text ? text + '\n' : '') + T('[Anhang nicht weitergeleitet]');
      var body = { v: 1, type: 'text', text: text };
      if (up.length) body.files = up;
      if (Array.isArray(m.body.notes) && m.body.notes.length) body.notes = m.body.notes.slice(0, 20);
      return sendBody(target.id, body, 'msg').then(function () { return skipped; });
    });
  }
  // In Seite uebernehmen: Klartext auf eine (neue oder bestehende) Seite - vorher deutlich: nicht Ende-zu-Ende-verschluesselt
  function escH(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function rid() { return Math.random().toString(36).slice(2, 12); }
  function pageBlocks(list) {
    return list.map(function (m) {
      var t = plainText(m);
      return { id: rid(), type: 'paragraph', data: { text: '<b>' + escH(selName(m)) + '</b> · ' + escH(stamp(m)) + (t ? '<br>' + escH(t).replace(/\n/g, '<br>') : '') } };
    });
  }
  function pageMd(list) { return list.map(function (m) { return '**' + selName(m) + '** · ' + stamp(m) + '\n' + plainText(m); }).join('\n\n'); }
  function appendTo(nid, list, again) {
    return api('GET', '/api/notes/' + nid).then(function (n) {
      var b;
      if ((n.format || 'blocks') === 'blocks') {
        var cur = {}; try { cur = JSON.parse(n.body || '{}') || {}; } catch (e) { cur = {}; }
        cur.blocks = (Array.isArray(cur.blocks) ? cur.blocks : []).concat(pageBlocks(list));
        b = { body: JSON.stringify(cur), format: 'blocks', rev: n.rev };
      } else b = { body: (n.body ? String(n.body).replace(/\s+$/, '') + '\n\n' : '') + pageMd(list), format: 'text', rev: n.rev };
      return api('PATCH', '/api/notes/' + nid, b);
    }).catch(function (e) { if (e.status === 409 && !again) return appendTo(nid, list, true); throw e; });
  }
  function selToPage() {
    var list = selList(), c = S.cur; if (!list.length || !c) return;
    var body = overlay(T('In Seite übernehmen')), pick = null, t = 0;
    body.parentNode.classList.add('ct-page-dlg');
    var w = el('p', 'ct-e2e-warn', '⚠ ' + T('Die Seite ist nicht Ende-zu-Ende-verschlüsselt – der Server kann den Text lesen.'));
    w.setAttribute('role', 'alert'); body.appendChild(w);
    function radio(v, label, on) {
      var l = el('label', 'ct-pg-opt'), r = el('input'); r.type = 'radio'; r.name = 'ct-pg-mode'; r.value = v; r.checked = !!on;
      r.addEventListener('change', sync); l.appendChild(r); l.appendChild(document.createTextNode(' ' + label)); body.appendChild(l); return r;
    }
    var rNew = radio('new', T('Neue Seite'), true);
    var title = el('input', 'hk-in ct-pg-title'); title.value = T('Chat {name} – {date}', { name: chatName(c) || T('Chat'), date: fDate.format(new Date()) });
    title.setAttribute('aria-label', T('Titel der neuen Seite')); title.maxLength = 200; body.appendChild(title);
    var rAdd = radio('add', T('An bestehende Seite anhängen'));
    var sq = el('input', 'hk-in ct-pg-q'); sq.type = 'search'; sq.placeholder = T('Seite suchen'); sq.setAttribute('aria-label', T('Seite suchen'));
    var res = el('div', 'ct-fwd-list ct-pg-list'); res.setAttribute('role', 'listbox'); res.setAttribute('aria-label', T('Seiten'));
    body.appendChild(sq); body.appendChild(res);
    var st = status(body), bar = el('div', 'hk-actions');
    var go = btn(T('Übernehmen'), 'btn-primary ct-pg-go', run);
    bar.appendChild(btn(T('Abbrechen'), null, closeOverlay)); bar.appendChild(go); body.appendChild(bar);
    function sync() {
      var add = rAdd.checked; title.disabled = add; sq.hidden = !add; res.hidden = !add;
      go.disabled = add && !pick;
      if (add && !res.childNodes.length) search();
    }
    function search() {
      clearTimeout(t);
      t = setTimeout(function () {
        api('GET', '/api/chat/pages?q=' + q(sq.value.trim())).catch(function () { return { pages: [] }; }).then(function (r) {
          res.textContent = '';
          (r.pages || []).forEach(function (p) {
            var b = el('button', 'ct-fwd-item' + (pick === p.id ? ' on' : '')); b.type = 'button'; b.setAttribute('role', 'option');
            b.setAttribute('aria-selected', pick === p.id ? 'true' : 'false');
            b.appendChild(el('span', 'ct-fwd-ic', p.icon || '📄')); b.appendChild(el('span', 'ct-fwd-n', p.title));
            b.addEventListener('click', function () { pick = p.id; search(); sync(); });
            res.appendChild(b);
          });
          if (!(r.pages || []).length) res.appendChild(el('p', 'hk-hint', T('Keine Seite gefunden.')));
        });
      }, 150);
    }
    sq.addEventListener('input', search);
    sync();
    function run() {
      busy(go, true); st.textContent = '';
      var p = rNew.checked
        ? api('POST', '/api/notes', { title: title.value.trim() || T('Chat') }).then(function (r) { return appendTo(r.id, list).then(function () { return r.id; }); })
        : appendTo(pick, list).then(function () { return pick; });
      p.then(function (nid) {
        closeOverlay(); selEnd();
        HK.toast(T('{n} {n:Nachricht|Nachrichten} in die Seite übernommen.', { n: list.length }), T('Öffnen'), function () { window.open('/notes#' + nid, 'hk-notes'); }, 8000);
      }, function (e) { busy(go, false); st.textContent = e.message; });
    }
  }
  // Warum keine Reaktionen? (x3_why vom Server) - dezentes (i) im Chat-Kopf mit Erklaerung
  function x3Why(c) {
    var w = c.x3_wait || {}, out = [];
    if (c.x3_why === 'setting') return [T('Reaktionen und Bearbeiten sind auf diesem Server noch ausgeschaltet. Die Verwaltung kann sie einschalten.')];
    if (c.x3_why === 'readonly') return [T('In einem schreibgeschützten Chat gibt es keine Reaktionen und kein Bearbeiten.')];
    if (c.x3_why === 'pending') return [T('Reaktionen und Bearbeiten gibt es, sobald die Anfrage angenommen ist.')];
    if (c.x3_why === 'server') return [T('Reaktionen und Bearbeiten kommen, sobald {host} aktualisiert ist.', { host: (c.x3_hosts || []).join(', ') || T('der andere Server') })];
    if (c.x3_why !== 'devices') return [];
    if (!(w.mine || []).length && !w.others) return [T('Reaktionen und Bearbeiten kommen in Kürze – dieser Chat wird gerade umgestellt.')];
    out.push(T('Reaktionen und Bearbeiten kommen, sobald alle Geräte Saga 2.5 nutzen.'));
    var mine = (w.mine || []).join(', ');
    if (mine && w.others) out.push(T('Noch nicht aktualisiert: {devices} und Geräte von {n} {n:weiteren Person|weiteren Personen}.', { devices: mine, n: w.others }));
    else if (mine) out.push(T('Noch nicht aktualisiert: {devices}.', { devices: mine }));
    else if (w.others) out.push(T('Noch nicht aktualisiert: Geräte von {n} {n:weiteren Person|weiteren Personen}.', { n: w.others }));
    if (w.until) out.push(T('Spätestens am {date}.', { date: fDate.format(new Date(w.until * 1000)) }));
    return out;
  }
  function x3Info(sub, c) {
    if (!c || c.x3_ok === true || !c.x3_why) return;
    var lines = x3Why(c); if (!lines.length) return;
    var b = el('button', 'icon-btn ct-x3i', 'ⓘ'); b.type = 'button'; b.setAttribute('aria-haspopup', 'dialog');
    b.setAttribute('aria-label', T('Warum gibt es hier keine Reaktionen?')); b.title = T('Warum gibt es hier keine Reaktionen?');
    b.addEventListener('click', function () {
      HK.openMenu(b, function (menu) {
        menu.textContent = '';
        var box = el('div', 'ct-x3-pop'); box.setAttribute('role', 'note');
        box.appendChild(el('b', null, T('Reaktionen und Bearbeiten')));
        lines.forEach(function (l) { box.appendChild(el('p', null, l)); });
        menu.appendChild(box);
        if (c.x3_why === 'devices' && (c.x3_wait || {}).mine && c.x3_wait.mine.length)
          menu.appendChild(HK.menuItem(T('Geräte verwalten'), function () { HK.closeMenu(); settings(); }, { ic: 'settings' }));
      });
    });
    sub.appendChild(b);
  }
  // Ereignisse im Nachrichtenbereich: Klick/Umschalt+Klick (Auswahl), Rechtsklick (Menue), langer Druck (Touch), Tastatur
  var lpress = null, lpFired = false, lpAt = 0;
  $('ct-msgs').addEventListener('click', function (e) {
    if (Date.now() - lpAt < 400) { e.preventDefault(); e.stopPropagation(); return; }   // Klick beim Loslassen nach langem Druck
    if (!S.sel || e.target.closest('.ct-older')) return;
    e.preventDefault(); e.stopPropagation();
    var row = e.target.closest('.ct-msg'), m = row && S.byId[row.dataset.id];
    if (m) selToggle(m, e.shiftKey);
  }, true);
  $('ct-msgs').addEventListener('contextmenu', function (e) {
    var row = e.target.closest('.ct-msg'), m = row && S.byId[row.dataset.id];
    if (!m) return;
    if (NOHOVER && NOHOVER.matches) { e.preventDefault(); return; }                   // Touch: langer Druck waehlt aus
    if (!S.sel && (e.target.closest('a, video, audio') || String(window.getSelection ? window.getSelection() : ''))) return;
    e.preventDefault();
    if (S.sel) { selToggle(m, e.shiftKey); return; }
    var act = row.querySelector('.ct-act:not(.ct-react-b)'); if (act) msgMenu(act, m);
  });
  function lpStop() { if (lpress) { clearTimeout(lpress.t); lpress = null; } }
  $('ct-msgs').addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse' || S.sel) return;
    var row = e.target.closest('.ct-msg'), m = row && S.byId[row.dataset.id];
    if (!selectable(m) || e.target.closest('video, audio, input, textarea')) return;
    lpress = { x: e.clientX, y: e.clientY, t: setTimeout(function () {
      lpress = null; lpFired = true; selStart(m);
      if (navigator.vibrate) { try { navigator.vibrate(12); } catch (e2) { /* egal */ } }
    }, 480) };
  });
  $('ct-msgs').addEventListener('pointermove', function (e) { if (lpress && Math.abs(e.clientX - lpress.x) + Math.abs(e.clientY - lpress.y) > 12) lpStop(); });
  ['pointerup', 'pointercancel', 'scroll'].forEach(function (ev) {
    $('ct-msgs').addEventListener(ev, function () { lpStop(); if (lpFired && ev !== 'scroll') { lpFired = false; lpAt = Date.now(); } }, { passive: true });
  });
  $('ct-msgs').addEventListener('keydown', function (e) {
    if (!S.sel) return;
    var row = e.target.closest && e.target.closest('.ct-msg[role="option"]');
    if (!row) return;
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); var m = S.byId[row.dataset.id]; if (m) selToggle(m, e.shiftKey); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      var rows = Array.prototype.slice.call($('ct-msgs').querySelectorAll('.ct-msg[role="option"]')), i = rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1);
      if (rows[i]) { e.preventDefault(); rows[i].focus(); }
    }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && S.sel && !ov && !HK.menuOpen() && !document.querySelector('.hk-ask')) { e.preventDefault(); selEnd(); }
  });
  function drawReply() {
    var r = $('ct-reply'); r.textContent = ''; r.hidden = !S.reply && !S.edit;
    r.classList.toggle('ct-editing', !!S.edit);
    if (S.edit) {                                         // 2.5: Leiste "Bearbeiten · Abbrechen"
      var et = el('div', 'ct-reply-t'); et.appendChild(el('b', null, '✎ ' + T('Bearbeiten'))); et.appendChild(el('span', null, preview(S.edit))); r.appendChild(et);
      var ex = el('button', 'btn small ct-edit-cancel', T('Abbrechen')); ex.type = 'button';
      ex.addEventListener('click', editCancel); r.appendChild(ex);
      return;
    }
    if (!S.reply) return;
    var qm = quoteMedia(S.cur, S.reply);
    if (qm && qm.thumb) r.appendChild(qm.thumb);
    var t = el('div', 'ct-reply-t'); t.appendChild(el('b', null, T('Antwort an {name}', { name: S.reply.sender ? S.reply.sender.name : '' })));
    t.appendChild(el('span', null, qm ? qm.label : preview(S.reply))); r.appendChild(t);
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Antwort abbrechen'));
    x.addEventListener('click', function () { S.reply = null; drawReply(); }); r.appendChild(x);
  }

  // --- Reaktionen und Bearbeiten (2.5, saga/chat_x3.py; Formate tests/vectors/chat_v3.json) -------------------------
  // Nur in Chats mit x3_ok (Format v2, angenommen, alle Server koennen chat3). Reaktionen: je Person eine, signiert vom
  // Geraet, verschluesselt mit einem aus dem Nachrichtenschluessel abgeleiteten Schluessel; pruefen + oeffnen im Worker
  // (X.openReactions). Senden gebuendelt (500 ms: nur die letzte Wahl geht raus). Bearbeiten: v2-Nachricht kind 'edit'
  // ({edit, t}), hoechstens 24 h und 20-mal, kein Verlauf - angezeigt wird der neueste Text mit "bearbeitet".
  function x3(c) { return !!(c && c.x3_ok === true && isFed(c)); }
  function x3lim() { return (S.me && S.me.x3_limits) || { edit_window: 86400, edit_max: 20 }; }
  function canReact(c, m) { return x3(c) && !!m && !!m._k && !!m.body && m.verified && !m.fstate && m.kind !== 'call' && m.kind !== 'sys'; }
  function canEdit(c, m) {
    var l = x3lim();
    return x3(c) && !!m && !!m.body && typeof m.body.text === 'string' && !!m.body.text && m.sender_handle === c.my_handle && !m.ai && m._ai !== 'ok' &&
           !m.fstate && m.kind !== 'call' && m.kind !== 'sys' && Date.now() / 1000 - (m.created || 0) < l.edit_window && (m.edit_n || 0) < l.edit_max;
  }
  function rxPks() {
    var o = {};
    Object.keys(S.fk || {}).forEach(function (h) {
      var f = S.fk[h]; if (!f || !f.keys || f.trust === 'reject') return;
      f.keys.devices.forEach(function (d) { if (!d.revoked) o[h + '|' + d.id] = d.sign_pub; });
    });
    return o;
  }
  var rxDrawT = null;
  function rxDraw() { clearTimeout(rxDrawT); rxDrawT = setTimeout(function () { if (S.cur) drawMsgs(); }, 30); }
  // Neueste (sichtbare) Nachrichten zuerst; viele Reaktionen (ueber RX_SPLIT) auf zwei Worker verteilt
  // (tools/perf/x3_browser.js: 50 x 50 kalt ~250 ms mit einem, ~180 ms mit zwei Workern)
  var RX_FIRST = 250, RX_SPLIT = 500;
  function rxOpen(c, msgs) {
    var jobs = (msgs || []).filter(function (m) { return m._k && (m.rx || []).length; }).reverse()
      .map(function (m) { return { K: m._k, ref: c.ref, mid: m.id, items: m.rx }; });
    if (!jobs.length) return Promise.resolve();
    var pks = rxPks(), t0 = performance.now(), parts = [], cur = [], n = 0, total = 0;
    jobs.forEach(function (j) { total += j.items.length; });
    jobs.forEach(function (j) {                           // Teil 1: bis RX_FIRST Reaktionen der neuesten Nachrichten
      cur.push(j); n += j.items.length;
      if (parts.length === 0 && n >= RX_FIRST && total > RX_SPLIT) { parts.push(cur); cur = []; n = 0; }
    });
    if (cur.length) parts.push(cur);
    if (parts.length === 2 && parts[1].length > 1) {        // Rest halbieren: zweiter Worker parallel
      var h = Math.ceil(parts[1].length / 2); parts = [parts[0], parts[1].slice(0, h), parts[1].slice(h)];
    }
    function apply(part, r) {
      if (!S.cur || S.cur.id !== c.id) return;
      part.forEach(function (j) { var m = S.byId[j.mid]; if (m && !rxT[m.id]) m._rx = (r.rx || {})[j.mid] || []; });
      rxDraw();
    }
    return Promise.all(parts.map(function (part, i) {
      return (i === 1 ? run2 : run)('rx', { jobs: part, pks: pks }).then(function (r) { apply(part, r); });
    })).then(function () { S.rxMs = performance.now() - t0; })  // (Messung, tools/perf)
      .catch(function () { /* Reaktionen bleiben unsichtbar */ });
  }
  function rxName(c, h) {
    if (h === c.my_handle) return T('du');
    var mm = c.members.filter(function (x) { return x.handle === h; })[0];
    return mm ? mm.name : T('Ehemaliges Mitglied');
  }
  function rxChips(c, m) {
    var box = el('div', 'ct-rx'), groups = {}, order = [];
    m._rx.slice().sort(function (a, b) { return a.ts - b.ts; }).forEach(function (x) {
      if (!groups[x.e]) { groups[x.e] = []; order.push(x.e); }
      groups[x.e].push(x.by);
    });
    order.forEach(function (e) {
      var who = groups[e], mine = who.indexOf(c.my_handle) >= 0;
      var b = el('button', 'ct-rx-chip' + (mine ? ' mine' : '')); b.type = 'button';
      b.appendChild(el('span', 'ct-rx-e', e)); b.appendChild(el('span', 'ct-rx-n', String(who.length)));
      var names = who.map(function (h) { return rxName(c, h); }).join(', ');
      b.title = names; b.setAttribute('aria-label', T('{emoji} von {names}', { emoji: e, names: names }));
      b.setAttribute('aria-pressed', mine ? 'true' : 'false');
      if (canReact(c, m)) b.addEventListener('click', function () { react(c, m, mine ? '' : e); });
      else b.disabled = true;
      b.addEventListener('contextmenu', function (ev) { ev.preventDefault(); rxWho(b, c, m, e, who); });
      box.appendChild(b);
    });
    return box;
  }
  function rxWho(anchor, c, m, e, who) {
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      menu.appendChild(el('div', 'menu-label', e));
      who.forEach(function (h) { menu.appendChild(HK.menuItem(rxName(c, h), function () { HK.closeMenu(); }, { icon: ' ' })); });
    });
  }
  function myRx(c, m) { var x = (m._rx || []).filter(function (r) { return r.by === c.my_handle; })[0]; return x ? x.e : ''; }
  function rxQuick(anchor, m) {
    var c = S.cur, row = el('div', 'ct-rq'), cur = myRx(c, m);
    row.setAttribute('role', 'group'); row.setAttribute('aria-label', T('Reagieren'));
    (window.SagaEmoji ? window.SagaEmoji.QUICK : ['👍', '❤️', '😂', '😮', '😢', '🙏']).forEach(function (e) {
      var b = el('button', 'ct-rq-b' + (cur === e ? ' on' : ''), e); b.type = 'button'; b.setAttribute('aria-label', e);
      b.addEventListener('click', function () { HK.closeMenu(); if (window.SagaEmoji) window.SagaEmoji.remember(e); react(c, m, cur === e ? '' : e); });
      row.appendChild(b);
    });
    var more = el('button', 'ct-rq-b ct-rq-more', '+'); more.type = 'button'; more.setAttribute('aria-label', T('Weitere Emojis')); more.title = T('Weitere Emojis');
    more.addEventListener('click', function () {
      HK.closeMenu();
      if (!window.SagaEmoji) return;
      HK.openMenu(anchor, function (menu) {
        menu.textContent = '';
        window.SagaEmoji.picker(menu, function (e) { HK.closeMenu(); react(c, m, cur === e ? '' : e); });
      });
    });
    row.appendChild(more);
    row.addEventListener('keydown', function (ev) {         // Pfeile links/rechts in der Leiste, runter ins Menue
      var bs = Array.prototype.slice.call(row.querySelectorAll('button')), i = bs.indexOf(document.activeElement);
      if (i < 0) return;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { ev.preventDefault(); bs[(i + (ev.key === 'ArrowRight' ? 1 : bs.length - 1)) % bs.length].focus(); }
      else if (ev.key === 'ArrowDown') { var mi = row.parentNode && row.parentNode.querySelector('.menu-item'); if (mi) { ev.preventDefault(); mi.focus(); } }
    });
    return row;
  }
  function rxBar(anchor, m) { HK.openMenu(anchor, function (menu) { menu.textContent = ''; menu.appendChild(rxQuick(anchor, m)); }); }
  var rxT = {};
  function react(c, m, e) {
    if (!canReact(c, m)) return;
    var me = c.my_handle;
    m._rx = (m._rx || []).filter(function (x) { return x.by !== me; });
    if (e) m._rx.push({ by: me, e: e, ts: Date.now() });
    drawMsgs();
    clearTimeout(rxT[m.id]);
    rxT[m.id] = setTimeout(function () {                    // gebuendelt: nur die letzte Wahl
      var r;
      try { r = X.encryptReaction({ K: m._k, chat_ref: c.ref, msg_id: m.id, handle: me, device: K.device.id, sign_sk: K.device.sign_sk, emoji: e }); }
      catch (x) { delete rxT[m.id]; return; }
      api('PUT', '/api/chats/' + c.id + '/messages/' + q(m.id) + '/reaction', { dev: r.dev, ts: r.ts, ct: r.ct, sig: r.sig }).then(function () {
        m.rx = (m.rx || []).filter(function (x) { return x.by !== me; }).concat([r]);
      }).catch(function (err) {
        HK.toast(T('Reaktion nicht gesendet: {err}', { err: err.message }));
        m._rx = null; return rxOpen(c, [m]).then(rxDraw);
      }).finally(function () { delete rxT[m.id]; });
    }, 500);
  }
  function rxMerge(m, items) {
    var by = {};
    (m.rx || []).forEach(function (x) { by[x.by] = x; });
    items.forEach(function (x) { if (!by[x.by] || by[x.by].ts < x.ts) by[x.by] = x; });
    m.rx = Object.keys(by).map(function (k) { return by[k]; });
  }
  function rxLive(c, d) {
    if (d.more) {                                         // viele auf einmal: nur Hinweis -> abrufen
      var mids = (d.mids || []).filter(function (x) { return S.byId[x]; }).slice(0, 100);
      if (!mids.length) return;
      api('GET', '/api/chats/' + c.id + '/reactions?mids=' + mids.map(q).join(',')).then(function (r) {
        var ms = [];
        Object.keys(r.reactions || {}).forEach(function (id) { var m = S.byId[id]; if (m) { m.rx = r.reactions[id]; ms.push(m); } });
        return rxOpen(c, ms);
      }).catch(function () {});
      return;
    }
    var per = {};
    (d.items || []).forEach(function (x) { if (S.byId[x.id]) (per[x.id] = per[x.id] || []).push(x); });
    var ms = Object.keys(per).map(function (id) { var m = S.byId[id]; rxMerge(m, per[id]); return m; });
    if (ms.length) rxOpen(c, ms);
  }
  function edApply(c, m) {
    var e = m.edit; if (!e) return;
    try {
      var d = decryptV2(c, e);
      if (!d.verified || X.editCheck(m, e, d.body)) return;          // nicht bestaetigt: Original bleibt
      if (!m._orig) m._orig = m.body;
      m.body = Object.assign({}, m._orig, { text: d.body.t });
      m._ed = m.edited_at || e.created || 0;
    } catch (x) { /* Bearbeitung nicht lesbar: Original bleibt */ }
  }
  function edLive(c, d) {
    var m = S.byId[d.id]; if (!m || !m.seq) return;
    api('GET', '/api/chats/' + c.id + '/messages?calls=1&limit=1&device=' + q(dev()) + '&before=' + (m.seq + 1)).then(function (r) {
      var x = (r.messages || []).filter(function (y) { return y.id === m.id; })[0];
      if (!x || !S.cur || S.cur.id !== c.id) return;
      m.edit = x.edit; m.edited_at = x.edited_at; m.edit_n = x.edit_n;
      if (m.edit) { edApply(c, m); drawMsgs(); }
    }).catch(function () {});
  }
  function editStart(m) {
    if (!canEdit(S.cur, m)) return;
    S.reply = null; S.edit = m;
    var inp = $('ct-input'); inp.value = m.body.text; autosize(); drawReply(); micDraw();
    inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
  }
  function editCancel() { S.edit = null; $('ct-input').value = ''; autosize(); drawReply(); micDraw(); $('ct-input').focus(); }
  function editSend(c, m, text) {
    if (!text.trim()) {                                   // Testrunde Web Nr. 044: nicht still abbrechen
      HK.toast(T('Eine Nachricht kann nicht leer sein – zum Entfernen „Für alle löschen“ im Menü der Nachricht wählen.')); $('ct-input').focus(); return;
    }
    if (text === (m.body || {}).text) { editCancel(); return; }
    Object.keys(S.mentions).forEach(function (label) { if (text.indexOf(label) >= 0) text = text.split(label).join('[[note:' + S.mentions[label] + ']]'); });
    sending = true; $('ct-send').disabled = true;
    sendV2(c, { edit: m.id, t: text }, null, null, 'edit').then(function () {
      if (!m._orig) m._orig = m.body;
      m.body = Object.assign({}, m._orig, { text: text }); m._ed = Date.now() / 1000; m.edit_n = (m.edit_n || 0) + 1;
      S.edit = null; S.mentions = {}; $('ct-input').value = ''; autosize(); drawReply(); drawMsgs(); micDraw();
    }).catch(function (err) { HK.toast(T('Nicht bearbeitet: {err}', { err: err.message })); })
      .finally(function () { sending = false; $('ct-send').disabled = false; $('ct-input').focus(); });
  }

  // --- Sprachnachrichten (2.5, static/chat-voice.js; Grenzen x3_limits: 15 min, 24 kbit/s) ------------------------------
  // Aufnehmen -> Vorschau (anhoeren, verwerfen, senden) -> als saga-file/v2 hochladen (wie Videos) und als Anhang mit
  // voice/dur/wf senden. Unter 1 s wird verworfen; ab 14:00 Hinweis, bei 15:00 endet die Aufnahme von selbst.
  // Spieler je Anhang bleiben ueber das Neuzeichnen erhalten (VPL), damit eine laufende Wiedergabe nicht abbricht.
  var VPL = {}, REC = null;
  function voiceEl(f) { if (!VPL[f.id]) VPL[f.id] = VOICE.player(f); return VPL[f.id]; }
  function vplDrop() { Object.keys(VPL).forEach(function (k) { try { VPL[k].vmDrop(); } catch (e) { /* egal */ } }); VPL = {}; }
  function micDraw() {
    var b = $('ct-mic'); if (!b) return;
    var c = S.cur;
    b.hidden = !(c && VOICE && VID && S.vmode && x3(c) && hasFeat('voice')) || !!$('ct-input').value.trim() || S.files.length > 0 || !!S.edit;
  }
  function micErr(e) {
    var n = e && e.name;
    if (n === 'NotAllowedError' || n === 'SecurityError') return T('Kein Zugriff auf das Mikrofon – bitte im Browser erlauben.');
    if (n === 'NotFoundError' || n === 'OverconstrainedError' || n === 'NotReadableError') return T('Kein Mikrofon gefunden oder es wird gerade anderweitig benutzt.');
    if (e && e.code === 'unsupported') return e.message;
    return T('Aufnahme nicht möglich: {err}', { err: (e && e.message) || '?' });
  }
  function recStart() {
    var c = S.cur; if (!c || REC || !VOICE) return;
    var l = x3lim(), maxS = l.voice_max_s || 900;
    REC = { c: c, st: 'wait', s: 0, lv: 0, warned: false, maxS: maxS };
    recDraw();
    VOICE.record({ kbps: l.voice_kbps || 24, maxS: maxS,
      onTick: function (sec, lv) {
        if (!REC || REC.st !== 'rec') return;
        REC.s = sec; REC.lv = lv;
        if (!REC.warned && sec >= maxS - 60) { REC.warned = true; HK.toast(T('Noch eine Minute – dann endet die Aufnahme.')); }
        recTick();
      },
      onLimit: function () { if (REC && REC.st === 'rec') { HK.toast(T('Höchstens {n} Minuten – Aufnahme beendet.', { n: Math.round(maxS / 60) })); recStop(); } }
    }).then(function (r) {
      if (!REC || REC.st !== 'wait') { r.cancel(); return; }
      REC.r = r; REC.st = 'rec'; recDraw();
    }).catch(function (e) { REC = null; recDraw(); HK.toast(micErr(e)); });
  }
  function recStop() {
    if (!REC || REC.st !== 'rec') return;
    REC.st = 'stop'; recDraw();
    REC.r.stop().then(function (res) {
      if (!REC) return;
      if (res.dur < 1) { HK.toast(T('Zu kurz – nicht gesendet.')); REC = null; recDraw(); return; }
      REC.res = res; REC.url = URL.createObjectURL(res.blob); REC.st = 'prev'; recDraw();
    }).catch(function (e) { REC = null; recDraw(); HK.toast(micErr(e)); });
  }
  function recCancel() {
    if (!REC) return;
    if (REC.r && REC.st === 'rec') REC.r.cancel();
    if (REC.pl) REC.pl.vmDrop();
    if (REC.url) URL.revokeObjectURL(REC.url);
    REC = null; recDraw();
  }
  function recSend() {
    if (!REC || REC.st !== 'prev') return;
    var R = REC, c = R.c, res = R.res, l = x3lim(), reply = S.reply ? S.reply.id : null;
    if (l.voice_max_bytes && res.blob.size > l.voice_max_bytes) { HK.toast(T('Sprachnachricht zu groß.')); return; }
    R.st = 'send'; recDraw();
    VID.upload(c.id, res.blob, function (p) { R.prog = p; recTick(); }).then(function (u) {
      var f = { id: u.id, k: u.key, n: 'voice.ogg', m: 'audio/ogg; codecs=opus', s: u.size, x: 2, cs: u.cs, voice: true,
                dur: Math.round(res.dur * 10) / 10, wf: b64(res.wf) };
      return sendBody(c.id, { v: 1, type: 'text', text: '', files: [f] }, 'msg', reply);
    }).then(function () {
      if (R.pl) R.pl.vmDrop();
      URL.revokeObjectURL(R.url);
      if (REC === R) { REC = null; recDraw(); }
      S.reply = null; drawReply();
      return loadMsgs(false);
    }).catch(function (e) {
      HK.toast(T('Nicht gesendet: {err}', { err: e.message }));
      if (REC === R) { R.st = 'prev'; recDraw(); }
    });
  }
  function recTick() {
    var t = $('ct-rec-t'), lv = $('ct-rec-lv');
    if (!REC || !t) return;
    if (REC.st === 'send') { t.textContent = Math.round((REC.prog || 0) * 100) + ' %'; return; }
    t.textContent = VOICE.fmt(REC.s) + ' / ' + VOICE.fmt(REC.maxS);
    t.classList.toggle('warn', REC.warned);
    if (lv) lv.style.transform = 'scaleX(' + Math.max(0.04, REC.lv).toFixed(3) + ')';
  }
  function recDraw() {
    var bar = $('ct-rec'), form = $('ct-compose');
    if (!bar) return;
    bar.textContent = '';
    if (!REC) { bar.hidden = true; form.hidden = false; drawState(S.cur || {}); micDraw(); return; }
    bar.hidden = false; form.hidden = true;
    if (REC.st === 'prev' || REC.st === 'send') {
      var f = { id: 'prev', _url: REC.url, dur: REC.res.dur, wf: b64(REC.res.wf) };
      if (!REC.pl) REC.pl = VOICE.player(f);
      bar.appendChild(REC.pl);
      var t2 = el('span', 'ct-rec-t'); t2.id = 'ct-rec-t'; if (REC.st === 'send') bar.appendChild(t2);
      var del = btn('🗑 ' + T('Verwerfen'), 'ct-rec-del', recCancel), go = btn(T('Senden'), 'btn-primary ct-rec-send', recSend);
      del.disabled = go.disabled = REC.st === 'send';
      bar.appendChild(del); bar.appendChild(go);
      if (REC.st === 'prev') go.focus();
      return;
    }
    var dot = el('span', 'ct-rec-dot'); dot.setAttribute('aria-hidden', 'true'); bar.appendChild(dot);
    var t = el('span', 'ct-rec-t', REC.st === 'wait' ? T('Mikrofon …') : VOICE.fmt(REC.s)); t.id = 'ct-rec-t'; t.setAttribute('role', 'timer'); bar.appendChild(t);
    var lvw = el('span', 'ct-rec-lvw'), lv = el('span', 'ct-rec-lv'); lv.id = 'ct-rec-lv'; lvw.appendChild(lv); bar.appendChild(lvw);
    var x = btn('✕ ' + T('Abbrechen'), 'ct-rec-cancel', recCancel), st = btn('■ ' + T('Stopp'), 'btn-primary ct-rec-stop', recStop);
    st.disabled = REC.st !== 'rec';
    bar.appendChild(x); bar.appendChild(st);
    if (REC.st === 'rec') { st.focus(); recTick(); }
  }
  if ($('ct-mic')) $('ct-mic').addEventListener('click', function () { if (S.ready) recStart(); });
  if ($('ct-rec')) $('ct-rec').addEventListener('keydown', function (e) { if (e.key === 'Escape' && REC && REC.st !== 'send') { e.preventDefault(); recCancel(); } });

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
  // 2.5: zweiter Worker nur fuer viele Reaktionen auf einmal (Signaturen parallel); ohne ihn wie run()
  var W2 = null, w2Cb = {};
  function run2(op, d) {
    if (W2 === null && typeof Worker !== 'undefined' && W) {
      try {
        W2 = new Worker('/static/chat-worker.js?v=' + encodeURIComponent(document.body.dataset.v || ''));
        W2.onmessage = function (e) { var x = e.data || {}, cb = w2Cb[x.n]; if (!cb) return; delete w2Cb[x.n]; if (x.ok) cb[0](x); else cb[1](new Error(x.err)); };
        W2.onerror = function (e) { if (e && e.preventDefault) e.preventDefault(); var all = w2Cb; w2Cb = {}; W2 = false; Object.keys(all).forEach(function (k) { all[k][1](new Error('worker')); }); };
      } catch (e) { W2 = false; }
    }
    if (!W2) return run(op, d);
    return new Promise(function (res, rej) { var n = ++wN; w2Cb[n] = [res, rej]; d.op = op; d.n = n; W2.postMessage(d); })
      .catch(function (e) { if (e.message !== 'worker') throw e; return run(op, d); });
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
      if (d.op === 'f2enc' || d.op === 'f2dec') return window.SagaF2Job(X, d);           // Videos (saga-file/v2)
      if (d.op === 'rx') return X.openReactionsAsync(d.jobs, d.pks).then(function (rx) { return { rx: rx }; });                  // Reaktionen (2.5)
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
    if (img && f.x === 2 && !(VID && f.th && f.th.id && f.th.k)) img = false;      // 2.5: grosses Bild ohne Vorschau -> Datei-Knopf
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
    if (f.voice && VOICE && f.x === 2) return voiceEl(f);
    if (VID && VID.isVideo(f) && (!f.x || f.x === 2)) return videoEl(cid, f, name);
    var b = el('button', 'ct-file'); b.type = 'button';
    b.appendChild(el('span', 'ct-file-ic', '📎')); b.appendChild(el('span', 'ct-file-n', name)); b.appendChild(el('span', 'ct-file-s', size(f.s)));
    if (f.x && f.x !== 1 && f.x !== 2) {                // neueres Format: nicht still scheitern
      b.disabled = true; b.appendChild(el('span', 'ct-file-s', T('Mit einer neueren Saga-Version öffnen'))); return b;
    }
    if (f.x === 2 && !VID) { b.disabled = true; return b; }
    b.addEventListener('click', function () {
      if (f.x === 2) return saveBig(f, b);              // 2.5: grosse Datei (saga-file/v2) als Strom speichern
      plainUrl(cid, f).then(function (url) { save(url, name); setTimeout(function () { URL.revokeObjectURL(url); }, 60000); },
        function (e) { HK.toast(T('Anhang nicht geladen: {err}', { err: e.message })); });
    });
    return b;
  }
  // 2.5: grosse Datei (saga-file/v2, kein Video) speichern - blockweise ueber den Strom wie bei Videos; Fortschritt am Knopf
  function saveBig(f, b) {
    if (b.disabled) return;
    var pr = el('span', 'ct-file-s ct-file-prog'); b.disabled = true; b.appendChild(pr);
    VID.save(f, function (x) { pr.textContent = Math.round(x * 100) + ' %'; }).then(null, function (e) {
      if (e && e.name !== 'AbortError') HK.toast(T('Anhang nicht geladen: {err}', { err: e.message }));
    }).then(function () { pr.remove(); b.disabled = false; });
  }
  // 2.5: grosse Datei (saga-file/v2) nur bis BIG_VIEW_MAX ganz im Speicher (Bildansicht, Weiterleiten), sonst Speichern
  var BIG_VIEW_MAX = 100 * 1048576;
  // Video (2.4): nur Poster (lazy wie Bilder, f.th), Dauer, Abspielen erst im Player. Nie ein <video> in der Liste.
  function videoEl(cid, f, name) {
    var fig = el('button', 'ct-img ct-vid'); fig.type = 'button'; fig.setAttribute('aria-label', T('Video abspielen: {name}', { name: name }));
    fig.dataset.fid = f.id;
    var box = fitBox(f.w, f.h) || { w: THUMB_BOX, h: Math.round(THUMB_BOX * 9 / 16) };
    fig.style.width = box.w + 'px'; fig.style.aspectRatio = box.w + ' / ' + box.h;
    var im = el('img'); im.alt = ''; im.decoding = 'async'; fig.appendChild(im);
    fig.appendChild(el('span', 'ct-vid-play', '▶'));
    if (f.dur) fig.appendChild(el('span', 'ct-vid-dur', VID.dur(f.dur)));
    if (f.th && f.th.id && f.th.k) {
      var u = memGet('t:' + f.id);
      if (u) im.src = u;
      else lazy(fig, function (signal) { return thumbUrl(cid, f, signal).then(function (x) { im.src = x; }); }, function () { noPoster(fig, name); });
    } else noPoster(fig, name);
    fig.addEventListener('click', function () { viewVideo(cid, f, name); });
    return fig;
  }
  // Ohne Vorschaubild (das sendende Geraet konnte das Video nicht dekodieren, z. B. H.264 im Browser ohne diesen Codec):
  // Platzhalter mit Dateiname statt einer leeren dunklen Flaeche (2.4, Testrunde W8)
  function noPoster(fig, name) {
    fig.classList.add('ct-vid-noposter');
    if (!fig.querySelector('.ct-vid-ph')) { var ph = el('span', 'ct-vid-ph'); ph.appendChild(el('span', 'ct-vid-ph-ic', '🎬')); ph.appendChild(el('span', 'ct-vid-ph-n', name)); fig.appendChild(ph); }
  }
  function viewVideo(cid, f, name) {
    var body = overlay(name, { wide: true }), wrap = el('div', 'ct-view ct-view-video');
    body.appendChild(wrap);
    var mime = /^video\/[\w.+-]{1,60}$/.test(f.m || '') ? f.m : 'video/mp4';
    var bar = el('div', 'hk-actions'), st = el('span', 'hk-hint ct-view-prog'), sv = btn(T('Speichern'), 'ct-view-save');
    ovClean = VID.play(wrap, f, function () {               // v1 (bis 10 MB): ganz laden, entschluesseln, Blob
      return getBlob(f).then(function (buf) { return run('dec', { cid: cid, blob: buf, key: f.k }, [buf]); })
        .then(function (r) { return URL.createObjectURL(new Blob([r.plain], { type: mime })); });
    }, function () { sv.click(); });
    bar.appendChild(st); bar.appendChild(sv); body.appendChild(bar);
    sv.addEventListener('click', function () {
      sv.disabled = true;
      var p = f.x === 2 ? VID.save(f, function (x) { st.textContent = Math.round(x * 100) + ' %'; })
        : plainUrl(cid, f).then(function (url) { save(url, name); setTimeout(function () { URL.revokeObjectURL(url); }, 60000); });
      p.then(function () { st.textContent = ''; }, function (e) { if (e && e.name !== 'AbortError') HK.toast(T('Anhang nicht geladen: {err}', { err: e.message })); st.textContent = ''; })
        .then(function () { sv.disabled = false; });
    });
  }

  // Original in groß (erst hier geladen). 2.5 (medien1): Zoom (Rad, Spreizen, Doppelklick/-tippen, Ziehen verschiebt),
  // vor/zurueck durch alle Bilder des Chats (Pfeiltasten, Knoepfe, Wischen), "n / m", Herunterladen, Teilen, Bild kopieren
  var viewUrl = null, VZ_MAX = 8;
  function viewable(f) { return !!f && IMG.test(f.m) && !(f.x === 2 && !(VID && f.th && f.th.id && f.th.k)); }   // wie fileEl
  function galList(cid, f, name) {                      // alle Bilder des offenen Chats (aelteste zuerst); sonst nur dieses
    var out = [], at = -1, c = S.cur;
    if (c) S.msgs.forEach(function (m) {
      if (m.kind !== 'msg' || m.error || !m.body || !Array.isArray(m.body.files)) return;
      var mc = fileCid(c, m);
      m.body.files.slice(0, 10).forEach(function (x) {
        if (!viewable(x)) return;
        if (x.id === f.id && at < 0) at = out.length;
        out.push({ cid: mc, f: x, name: String(x.n || T('Datei')).slice(0, 200) });
      });
    });
    if (at < 0) { out = [{ cid: cid, f: f, name: name }]; at = 0; }
    return { list: out, at: at };
  }
  function plainBlob(cid, f) {                          // Klartext eines Bildes als Blob (v1 ganz, v2 bis BIG_VIEW_MAX)
    if (f.x === 2) {
      if (f.s > BIG_VIEW_MAX) return Promise.reject(new Error(T('Zu groß für die Vorschau – bitte speichern.')));
      return VID.plain(f).then(function (p) { return new Blob([p], { type: f.m }); });
    }
    return getBlob(f).then(function (buf) { return run('dec', { cid: cid, blob: buf, key: f.k }, [buf]); })
      .then(function (r) { return new Blob([r.plain], { type: f.m }); });
  }
  function pngBlob(b) {                                 // Zwischenablage kann verlaesslich nur image/png
    if (b.type === 'image/png') return Promise.resolve(b);
    return createImageBitmap(b).then(function (bm) {
      var cv = document.createElement('canvas'); cv.width = bm.width; cv.height = bm.height;
      cv.getContext('2d').drawImage(bm, 0, 0);
      return new Promise(function (res, rej) { cv.toBlob(function (o) { if (o) res(o); else rej(new Error('png')); }, 'image/png'); });
    });
  }
  var canCopyImg = !!(navigator.clipboard && navigator.clipboard.write && window.ClipboardItem);
  var canShareFile = (function () {
    try { return !!(navigator.share && navigator.canShare && navigator.canShare({ files: [new File([new Uint8Array(1)], 'x.png', { type: 'image/png' })] })); }
    catch (e) { return false; }
  })();
  function viewImage(cid, f, name) {
    var g = galList(cid, f, name), cur = null, tok = 0, blob = null;
    var body = overlay(name, { wide: true }), dlg = body.parentNode, h2 = dlg.querySelector('.hk-dlg-head h2');
    dlg.classList.add('ct-gal-dlg');
    var wrap = el('div', 'ct-view ct-gal'), st = el('p', 'hk-status ct-gal-st'), im = el('img');
    st.setAttribute('role', 'status'); im.draggable = false;
    wrap.tabIndex = -1; wrap.appendChild(im); wrap.appendChild(st); body.appendChild(wrap);
    function navBtn(cls, ic, label, d) {
      var b = el('button', 'icon-btn ct-gal-nav ' + cls, ic); b.type = 'button'; b.setAttribute('aria-label', label); b.title = label;
      b.addEventListener('click', function (e) { e.stopPropagation(); go(d); });
      b.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
      wrap.appendChild(b); return b;
    }
    var pv = navBtn('ct-gal-prev', '‹', T('Vorheriges Bild'), -1), nx = navBtn('ct-gal-next', '›', T('Nächstes Bild'), 1);
    var bar = el('div', 'hk-actions ct-gal-bar'), cnt = el('span', 'hk-hint ct-gal-n'); cnt.setAttribute('aria-live', 'polite');
    var sv = btn(T('Herunterladen'), 'ct-view-save'), sh = btn(T('Teilen'), 'ct-gal-share'), cp = btn(T('Bild kopieren'), 'ct-gal-copy');
    sh.hidden = !canShareFile; cp.hidden = !canCopyImg;
    bar.appendChild(cnt); bar.appendChild(sh); bar.appendChild(cp); bar.appendChild(sv); body.appendChild(bar);

    // --- Zoom/Verschieben: transform translate(x,y) scale(s), Ursprung Bildmitte -----------------------------------
    var z = { s: 1, x: 0, y: 0 };
    function clampZ() {
      if (z.s <= 1.001) { z.s = 1; z.x = 0; z.y = 0; return; }
      var w = im.offsetWidth * z.s, h = im.offsetHeight * z.s, W = wrap.clientWidth, H = wrap.clientHeight;
      var mx = Math.max(0, (w - W) / 2), my = Math.max(0, (h - H) / 2);
      z.x = Math.max(-mx, Math.min(mx, z.x)); z.y = Math.max(-my, Math.min(my, z.y));
    }
    function applyZ() {
      clampZ();
      im.style.transform = z.s === 1 ? '' : 'translate(' + z.x.toFixed(1) + 'px,' + z.y.toFixed(1) + 'px) scale(' + z.s.toFixed(3) + ')';
      wrap.classList.toggle('ct-gal-zoomed', z.s > 1);
    }
    function zoomAt(s, cx, cy) {                        // cx/cy: Bildschirmpunkt, der beim Zoomen stehen bleibt
      var r = wrap.getBoundingClientRect(), px = cx - (r.left + r.width / 2), py = cy - (r.top + r.height / 2);
      s = Math.max(1, Math.min(VZ_MAX, s));
      z.x = px - (px - z.x) * s / z.s; z.y = py - (py - z.y) * s / z.s; z.s = s;
      applyZ();
    }
    function zoomCenter(s) { var r = wrap.getBoundingClientRect(); zoomAt(s, r.left + r.width / 2, r.top + r.height / 2); }
    function resetZ() { z.s = 1; z.x = 0; z.y = 0; applyZ(); }
    wrap.addEventListener('wheel', function (e) {
      if (!im.src) return;
      e.preventDefault();
      var d = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
      zoomAt(z.s * Math.exp(-d * 0.0015), e.clientX, e.clientY);
    }, { passive: false });
    var pts = {}, pinch = null, drag = null, tap = null;
    function npts() { return Object.keys(pts).length; }
    function dist() { var k = Object.keys(pts), a = pts[k[0]], b = pts[k[1]]; return Math.hypot(a.x - b.x, a.y - b.y) || 1; }
    function mid() { var k = Object.keys(pts), a = pts[k[0]], b = pts[k[1]]; return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
    wrap.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0 && e.pointerType === 'mouse') return;
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      try { wrap.setPointerCapture(e.pointerId); } catch (er) { /* egal */ }
      if (npts() === 2) { drag = null; pinch = { d: dist(), s: z.s }; }
      else if (npts() === 1) drag = { x0: e.clientX, y0: e.clientY, zx: z.x, zy: z.y, t: Date.now(), moved: false };
    });
    wrap.addEventListener('pointermove', function (e) {
      if (!pts[e.pointerId]) return;
      pts[e.pointerId] = { x: e.clientX, y: e.clientY };
      if (pinch && npts() >= 2) { var m = mid(); zoomAt(pinch.s * dist() / pinch.d, m.x, m.y); return; }
      if (!drag) return;
      var dx = e.clientX - drag.x0, dy = e.clientY - drag.y0;
      if (Math.abs(dx) + Math.abs(dy) > 6) drag.moved = true;
      if (z.s > 1) { z.x = drag.zx + dx; z.y = drag.zy + dy; applyZ(); }
    });
    function up(e) {
      if (!pts[e.pointerId]) return;
      delete pts[e.pointerId];
      if (pinch) { if (npts() < 2) pinch = null; drag = null; return; }
      var d = drag; drag = null;
      if (!d || e.type === 'pointercancel') return;
      var dx = e.clientX - d.x0, dy = e.clientY - d.y0;
      if (z.s === 1 && d.moved && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) { tap = null; go(dx < 0 ? 1 : -1); return; }   // Wischen
      if (d.moved || Date.now() - d.t > 400 || e.target.closest('.ct-gal-nav')) { tap = null; return; }
      var now = Date.now();                            // Doppelklick / Doppeltippen: 1x <-> 2,5x an dieser Stelle
      if (tap && now - tap.t < 320 && Math.abs(tap.x - e.clientX) < 30 && Math.abs(tap.y - e.clientY) < 30) {
        tap = null;
        if (z.s > 1) resetZ(); else zoomAt(2.5, e.clientX, e.clientY);
      } else tap = { t: now, x: e.clientX, y: e.clientY };
    }
    wrap.addEventListener('pointerup', up); wrap.addEventListener('pointercancel', up);
    function onKey(e) {
      if (!body.isConnected || e.defaultPrevented || HK.menuOpen() || document.querySelector('.hk-ask')) return;
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.target && e.target.closest && e.target.closest('input, textarea, select, [contenteditable="true"]')) return;
      var k = e.key;
      if (k === 'ArrowLeft' || k === 'ArrowRight') { e.preventDefault(); go(k === 'ArrowLeft' ? -1 : 1); }
      else if (k === '+' || k === '=') { e.preventDefault(); zoomCenter(z.s * 1.5); }
      else if (k === '-') { e.preventDefault(); zoomCenter(z.s / 1.5); }
      else if (k === '0') { e.preventDefault(); resetZ(); }
    }
    document.addEventListener('keydown', onKey);
    window.addEventListener('resize', applyZ);
    ovClean = function () {
      document.removeEventListener('keydown', onKey); window.removeEventListener('resize', applyZ);
      tok++; if (viewUrl) { URL.revokeObjectURL(viewUrl); viewUrl = null; }
    };

    // --- Anzeigen / Blaettern ----------------------------------------------------------------------------------
    function show(i) {
      var it = g.list[i]; if (!it) return;
      g.at = i; cur = it; blob = null; var my = ++tok;
      if (viewUrl) { URL.revokeObjectURL(viewUrl); viewUrl = null; }
      resetZ();
      h2.textContent = it.name; ov.setAttribute('aria-label', it.name); im.alt = it.name;
      var n = g.list.length;
      cnt.textContent = n > 1 ? (i + 1) + ' / ' + n : ''; cnt.title = n > 1 ? T('Bild {n} von {m}', { n: i + 1, m: n }) : '';
      pv.hidden = nx.hidden = n < 2; pv.disabled = i === 0; nx.disabled = i === n - 1;
      sv.disabled = it.f.x !== 2; sh.disabled = cp.disabled = true;
      st.textContent = T('Lädt …'); st.hidden = false;
      var t = memGet('t:' + it.f.id);
      if (t) im.src = t; else im.removeAttribute('src');
      plainBlob(it.cid, it.f).then(function (b) {
        if (my !== tok || !body.isConnected) return;
        blob = b; viewUrl = URL.createObjectURL(b); im.src = viewUrl; st.hidden = true;
        sv.disabled = false; sh.disabled = cp.disabled = false;
      }, function (e) {
        if (my !== tok || !body.isConnected) return;
        st.textContent = T('Anhang nicht geladen: {err}', { err: e.message });
      });
    }
    function go(d) {
      var i = g.at + d;
      if (i < 0 || i >= g.list.length || i === g.at) return;
      show(i);
    }
    sv.addEventListener('click', function () {
      if (!cur) return;
      if (cur.f.x === 2) return saveBig(cur.f, sv);       // 2.5: grosses Bild (saga-file/v2): Speichern immer als Strom
      if (viewUrl) save(viewUrl, cur.name);
    });
    sh.addEventListener('click', function () {          // ohne await davor: Teilen braucht die Nutzergeste
      if (!blob || !cur) return;
      var file = new File([blob], cur.name, { type: blob.type || cur.f.m });
      navigator.share({ files: [file], title: cur.name }).catch(function (e) {
        if (e && e.name !== 'AbortError') HK.toast(T('Teilen nicht möglich: {err}', { err: e.message }));
      });
    });
    cp.addEventListener('click', function () {
      if (!blob) return;
      var p = pngBlob(blob), done = function () { HK.toast(T('Bild kopiert.')); }, fail = function () { HK.toast(T('Kopieren nicht möglich.')); };
      var w;
      try { w = navigator.clipboard.write([new window.ClipboardItem({ 'image/png': p })]); }   // Promise im Item: Safari braucht die Geste
      catch (e) { w = p.then(function (b) { return navigator.clipboard.write([new window.ClipboardItem({ 'image/png': b })]); }); }
      w.then(done, fail);
    });
    show(g.at);
    wrap.focus({ preventScroll: true });
  }
  function save(url, name) { var a = el('a'); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove(); }
  function size(n) { n = +n || 0; return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n > 1024 ? Math.round(n / 1024) + ' KB' : n + ' B'; }
  function drawFiles() {
    var box = $('ct-files'); box.textContent = ''; box.hidden = !S.files.length;
    S.files.forEach(function (f, i) {
      var vid = VID && VID.isVideo(f);
      var c = el('span', 'ct-chip', (vid ? '🎬 ' : '📎 ') + f.name + ' (' + size(f.size) + ')'), x = el('button', 'linkbtn', '×'); x.type = 'button'; x.setAttribute('aria-label', T('Entfernen'));
      // 2.4 (Testrunde W8): "auf 720p verkleinern" nur, wenn dieser Browser das Video wirklich dekodieren UND als MP4
      // aufnehmen kann (Probe beim Auswaehlen, f._ok); sonst ehrlich "Original"
      if (vid && f._ok === true && VID.canOptimize() && !sending) {
        var lb = el('label', 'ct-chip-opt'), cb = el('input'); cb.type = 'checkbox'; cb.checked = !!f._opt; cb.disabled = !!f._force;
        cb.addEventListener('change', function () { f._opt = cb.checked; drawFiles(); });
        lb.appendChild(cb); lb.appendChild(document.createTextNode(' ' + (f._opt ? T('wird auf 720p verkleinert') : T('Original · auf 720p verkleinern')))); c.appendChild(lb);
      } else if (vid && f._ok === false && !sending) {
        c.appendChild(el('span', 'ct-chip-hint', T('Original – dieser Browser kann das Video nicht abspielen (ohne Vorschaubild)')));
      } else if (vid && f._ok === true && !sending) {
        c.appendChild(el('span', 'ct-chip-hint', T('Original')));
      }
      if (f._prog) c.appendChild(el('span', 'ct-chip-prog', f._prog));
      if (!sending) { x.addEventListener('click', function () { S.files.splice(i, 1); drawFiles(); }); c.appendChild(x); }
      box.appendChild(c);
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
      menu.appendChild(HK.menuItem(T('Bild oder Datei …'), function () { HK.closeMenu(); $('ct-file').click(); }, { ic: 'paperclip' }));
      menu.appendChild(HK.menuItem(T('Aus Immich …'), function () { HK.closeMenu(); immichAttach(); }, { ic: 'camera' }));
    });
  });
  // Anhaenge aus Dateiauswahl, Zwischenablage oder Ziehen und Ablegen (gleiche Pruefungen fuer alle Wege)
  function addFiles(list) {
    var lm = (S.me && S.me.limits) || {}, max = lm.file_max || 10485760;
    var vmax = lm.file2 && VID ? Math.max(max, lm.video_max || 0) : max, opt = VID ? VID.canOptimize() : '';
    var mobile = window.matchMedia && window.matchMedia('(pointer: coarse)').matches, over = false;
    Array.prototype.forEach.call(list || [], function (f) {
      var vid = VID && VID.isVideo(f);
      if (vid && f.size > vmax && opt) f._force = true;    // zu gross: nur verkleinert (720p) moeglich
      else if (f.size > vmax) { HK.toast(T('„{name}“ ist zu groß (höchstens {n} MB).', { name: f.name, n: Math.round(vmax / 1048576) })); return; }
      if (S.files.length >= 10) { over = true; return; }
      if (vid) f._opt = !!opt && (mobile || !!f._force);   // 2.4: vom Handy standardmaessig "optimiert 720p"
      if (vid) VID.probe(f).then(function (m) { f._ok = !!(m && m.w); f._meta = m; }, function () { f._ok = false; })
        .then(function () {
          if (!f._ok) { f._opt = false; if (f._force) { HK.toast(T('„{name}“ ist zu groß und lässt sich in diesem Browser nicht verkleinern.', { name: f.name })); var k = S.files.indexOf(f); if (k >= 0) S.files.splice(k, 1); } }
          drawFiles();
        });
      S.files.push(f);
    });
    if (over) HK.toast(T('Höchstens 10 Anhänge je Nachricht.'));
    drawFiles(); micDraw();
    $('ct-input').focus();                                 // Enter sendet dann, statt die Dateiauswahl erneut zu oeffnen (BUG-099)
  }
  // Anhaengen erlaubt: Chat offen, Eingabe sichtbar (nicht gesperrt/schreibgeschuetzt/Anfrage), nicht beim Bearbeiten
  function canAttach() {
    var form = $('ct-compose');
    return !!S.cur && !S.edit && !!form && !form.hidden && !$('ct-chat').hidden && !sending;
  }
  $('ct-file').addEventListener('change', function () { addFiles(this.files); this.value = ''; });

  // Einfuegen aus der Zwischenablage. WebKit (Safari, WKWebView der Mac-Huelle) liefert Bilder aus der macOS-
  // Zwischenablage teils nur ueber items (getAsFile), files bleibt dann leer - beides lesen, Doppelte vermeiden.
  function clipFiles(dt) {
    var out = [], seen = [];
    Array.prototype.forEach.call(dt.files || [], function (f) { out.push(f); seen.push(f); });
    if (!out.length) Array.prototype.forEach.call(dt.items || [], function (it) {
      if (it.kind !== 'file') return;
      var f = it.getAsFile && it.getAsFile(); if (f && seen.indexOf(f) < 0) { out.push(f); seen.push(f); }
    });
    return out;
  }
  var EXT = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/tiff': 'tiff', 'image/bmp': 'bmp', 'image/heic': 'heic', 'image/avif': 'avif' };
  function pastedName(f, i) {              // Bildschirmfoto o. ae. ohne echten Namen -> "Bild-JJJJ-MM-TT-HHMMSS.png"
    var n = f.name || '';
    if (n && !/^(image|bild|unknown|blob)(\.\w+)?$/i.test(n)) return f;
    if (!/^image\//.test(f.type || '') && n) return f;
    var d = new Date(), p = function (x) { return (x < 10 ? '0' : '') + x; };
    var ext = EXT[f.type] || (/^image\/([\w.+-]+)$/.exec(f.type || '') || [])[1] || (/\.(\w+)$/.exec(n) || [])[1] || 'png';
    var name = T('Bild') + '-' + d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds())
      + (i ? '-' + (i + 1) : '') + '.' + String(ext).replace('jpeg', 'jpg').replace('svg+xml', 'svg');
    try { return new File([f], name, { type: f.type || 'image/png', lastModified: f.lastModified || Date.now() }); } catch (e) { return f; }
  }
  $('ct-input').addEventListener('paste', function (e) {
    var dt = e.clipboardData; if (!dt) return;
    var files = clipFiles(dt); if (!files.length) return;          // reiner Text: normal einfuegen
    // Text dabei (z. B. aus Word/Office, das zusaetzlich ein Bild des Textes mitliefert): nur Text, kein Anhang.
    // Ausnahme Finder: der Text sind nur die Dateinamen -> Dateien anhaengen, Text nicht einfuegen.
    var txt = (dt.getData('text/plain') || '').replace(/\s+/g, ' ').trim();
    var names = files.map(function (f) { return f.name; }).join(' ').replace(/\s+/g, ' ').trim();
    if (txt && txt !== names) return;                              // Browser fuegt den Text normal ein
    e.preventDefault();
    if (!canAttach()) { if (S.edit) HK.toast(T('Beim Bearbeiten lassen sich keine Anhänge hinzufügen.')); return; }
    var k = 0; addFiles(files.map(function (f) { return /^image\//.test(f.type || '') || !f.name ? pastedName(f, k++) : f; }));
  });

  // Ziehen und Ablegen von Dateien auf die Unterhaltung (Text ziehen bleibt normal)
  (function () {
    var chat = $('ct-chat'), depth = 0, zone = null;
    function hasFiles(e) { var ty = e.dataTransfer && e.dataTransfer.types; return !!ty && Array.prototype.indexOf.call(ty, 'Files') >= 0; }
    function show(on) {
      if (on && !zone) { zone = el('div', 'ct-drop'); zone.setAttribute('aria-hidden', 'true'); zone.appendChild(el('span', 'ct-drop-t', T('Zum Anhängen hier ablegen'))); chat.appendChild(zone); }
      if (zone) zone.hidden = !on;
    }
    chat.addEventListener('dragenter', function (e) { if (!hasFiles(e) || !canAttach()) return; e.preventDefault(); depth++; show(true); });
    chat.addEventListener('dragover', function (e) { if (!hasFiles(e) || !canAttach()) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; show(true); });
    chat.addEventListener('dragleave', function (e) { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) show(false); });
    chat.addEventListener('drop', function (e) {
      depth = 0; show(false);
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (!canAttach()) return;
      addFiles(e.dataTransfer.files);
    });
    // Daneben abgelegte Dateien nicht im Fenster oeffnen (WKWebView/Browser wuerde die Chat-Ansicht verlassen)
    window.addEventListener('dragover', function (e) { if (hasFiles(e)) e.preventDefault(); });
    window.addEventListener('drop', function (e) { if (hasFiles(e)) { e.preventDefault(); depth = 0; show(false); } });
  })();

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
        if (kind === 'title' || kind === 'desc' || kind === 'avatar') m.kind = kind;     // 2.5: Beschreibung/Bild wie der Name
        if (kind === 'edit') { m.kind = 'edit'; m.edit_of = body.edit; m.files = []; }     // 2.5: Bearbeitung (saga/chat_x3.py)
        if (extra && extra.ai_reply) m.ai_reply = extra.ai_reply;
        if (extra && extra.mentions && extra.mentions.length) m.mentions = extra.mentions;        // 2.5 (chatmention1)
        if (body.files || body.preview) m.files = fileIds(body);
        return api('POST', '/api/chats/' + c.id + '/messages', m).then(function (r) {
          if (r.pending) HK.toast(T('Der andere Server ist gerade nicht erreichbar – die Nachricht wird zugestellt, sobald er wieder da ist.'), null, null, 6000);
          else delivered(m.id, r.fseq);
          return { m: m, r: r };
        }, function (e) {
          if (e.status === 409 && e.data && e.data.handles && !again) return attempt(true, true);
          throw e;
        });
      });
    }
    return attempt(false, false);
  }
  // (2.3) Heim hat die Nachricht sofort bestaetigt: "wird zugestellt" nicht stehen lassen - auch wenn sie schon (ueber das
  // Live-Ereignis) mit fstate 'pending' geladen wurde oder erst gleich geladen wird (Server 2.3.0 melden das nicht nach)
  function delivered(id, fseq) {
    S.okIds = S.okIds || {}; S.okIds[id] = fseq || true;
    var k = S.byId[id];
    if (k && fseq && !k.fseq) k.fseq = fseq;                  // (2.4) Position des Heims fuer die Gelesen-Haken
    if (k && k.fstate === 'pending') { k.fstate = null; drawMsgs(); }
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
      if (body.files || body.preview) m.files = fileIds(body);
      if (m.mentions && !m.mentions.length) delete m.mentions;
      return api('POST', '/api/chats/' + cid + '/messages', m).then(function (r) { return { m: m, r: r }; }, function (e) {
        if (e.status === 409 && e.data && e.data.chat_changed && !again) {        // 2.5: gerade auf Stufe 3 umgestellt
          return api('GET', '/api/chats/' + cid + '?device=' + q(dev())).then(function (r2) {
            var nc = r2.chat; S.chats = S.chats.map(function (x) { return x.id === cid ? nc : x; });
            if (S.cur && S.cur.id === cid) { S.cur = nc; S.fk = null; drawHead(); }
            if (!isFed(nc)) throw e;
            return sendV2(nc, body, reply, extra, kind);
          });
        }
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
  // 2.5: Grenze fuer das alte Format (v1): file_max, in Chats mit anderen Saga-Servern hoechstens 10 MiB (deren
  // ATTACH_MAX_MB kann kleiner sein); darueber saga-file/v2 bis video_max (wie Videos)
  var FED_V1_MAX = 10485760;
  function remoteChat(ch) { return !!(ch && (ch.fed || (ch.members || []).some(function (m) { return m.remote; }))); }
  function v1Max(ch) {
    var mx = (S.me && S.me.limits && S.me.limits.file_max) || 10485760;
    return remoteChat(ch) ? Math.min(mx, FED_V1_MAX) : mx;
  }
  function bigCheck(ch, file, v1) {                     // -> Fehler oder null; file.size > v1
    var lm = (S.me && S.me.limits) || {}, top = lm.file2 && VID ? Math.max(v1, lm.video_max || 0) : v1;
    if (remoteChat(ch) && ch.big_files === false) return new Error(T('Der Server des Gegenübers kann große Dateien noch nicht (höchstens {n} MB).', { n: Math.round(v1 / 1048576) }));
    if (file.size > top) return new Error(T('„{name}“ ist zu groß (höchstens {n} MB).', { name: file.name, n: Math.round(top / 1048576) }));
    return null;
  }
  function uploadFiles(cid, list, chat) {                // list/chat (2.5): Weiterleiten in einen anderen Chat
    list = list || S.files; chat = chat || (S.cur && S.cur.id === cid ? S.cur : null);
    var ids = (S.me && S.me.limits && S.me.limits.file_ids_per_msg) || 10, slots = ids - list.length;
    var fc = chat ? fileCid(chat) : cid;                  // Verschluesselung: v1 chat_id, v2 chat_ref
    var v1 = v1Max(chat);
    return Promise.all(list.map(function (f) {
      if (VID && VID.isVideo(f)) return uploadVideo(cid, fc, f, slots-- > 0, chat);
      if (f.size > v1) return uploadBig(cid, fc, f, IMG.test(f.type || '') && slots-- > 0, chat, v1);
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
  // 2.5: grosse Datei (kein Video) ueber der v1-Grenze: saga-file/v2 mit fortsetzbarem Upload wie Videos (ohne Poster
  // und Verkleinern). Bilder bis BIG_THUMB_MAX bekommen ein Vorschaubild (v1, eigener Schluessel) wie kleine Bilder.
  var BIG_THUMB_MAX = 64 * 1048576;
  function uploadBig(cid, fc, f, withThumb, ch, v1) {
    var err = bigCheck(ch, f, v1);
    if (err) return Promise.reject(err);
    var mime = f.type || 'application/octet-stream', e = { id: null, k: null, n: f.name.slice(0, 200), m: mime, s: f.size };
    function prog(x) { f._prog = T('Hochladen') + ' ' + Math.round(x * 100) + ' %'; drawFiles(); }
    var th = !withThumb || f.size > BIG_THUMB_MAX ? Promise.resolve(null) : f.arrayBuffer().then(function (buf) {
      return run('make', { bytes: buf, mime: mime }, [buf]);
    }).then(function (t) {
      e.w = t.w; e.h = t.h;
      var thumb = new Blob([t.jpeg], { type: 'image/jpeg' }), tsz = t.jpeg.byteLength;
      return run('enc', { cid: fc, bytes: t.jpeg }, [t.jpeg]).then(function (et) {
        var ct = new Blob([et.blob]);
        return upload(cid, ct).then(function (id) {
          memPut('t:' + id, URL.createObjectURL(thumb)); bPut(id, ct);
          return { id: id, k: et.key, m: 'image/jpeg', w: t.tw, h: t.th, s: tsz };
        });
      });
    }).catch(function () { return null; });
    var orig = VID.upload(cid, f, prog).then(function (r) { e.k = r.key; e.x = 2; e.cs = r.cs; return r.id; });
    return Promise.all([orig, th]).then(function (r) {
      e.id = r[0]; f._prog = '';
      if (r[1]) { e.th = r[1]; var u = mem.get('t:' + r[1].id); if (u) memPut('t:' + e.id, u); }
      return e;
    });
  }
  // Video (2.4): Poster/Dauer/Abmessungen hier erzeugen; optional verkleinern; bis file_max wie bisher (v1), darueber
  // saga-file/v2 mit fortsetzbarem Upload (static/chat-video.js). files[]: {id,k,n,m,s,dur,w,h, x:2,cs bei v2, th:{...}}
  function uploadVideo(cid, fc, f, withPoster, chat) {
    var max = v1Max(chat || (S.cur && S.cur.id === cid ? S.cur : null)), meta = {};
    function prog(label) { return function (x) { f._prog = label + ' ' + Math.round(x * 100) + ' %'; drawFiles(); }; }
    return (f._meta ? Promise.resolve(f._meta) : VID.probe(f)).catch(function () { return {}; }).then(function (m) {
      meta = m || {};
      if (f._opt && f._ok !== false && VID.canOptimize()) return VID.optimize(f, meta, prog(T('Verkleinern'))).catch(function (e) {
        if (f._force) throw e;
        HK.toast(T('Verkleinern nicht möglich – das Original wird gesendet.')); return f;
      });
      return f;
    }).then(function (file) {
      var ch = chat || (S.cur && S.cur.id === cid ? S.cur : null);
      if (file.size > max) { var err = bigCheck(ch, file, max); if (err) throw err; }      // 2.3-Server dabei / ueber video_max
      var e = { id: null, k: null, n: (file.name || f.name).slice(0, 200), m: file.type || f.type || 'video/mp4', s: file.size };
      if (meta.dur) e.dur = meta.dur;
      if (meta.w && meta.h) { e.w = meta.w; e.h = meta.h; }
      var th = Promise.resolve(null);
      if (withPoster && meta.jpeg) {                       // Poster wie Vorschaubilder: v1, eigener Schluessel, <= 30 KB
        var poster = new Blob([meta.jpeg], { type: 'image/jpeg' }), psz = meta.jpeg.byteLength;
        th = run('enc', { cid: fc, bytes: meta.jpeg }, [meta.jpeg]).then(function (et) {
          var ct = new Blob([et.blob]);
          return upload(cid, ct).then(function (id) {
            memPut('t:' + id, URL.createObjectURL(poster)); bPut(id, ct);
            return { id: id, k: et.key, m: 'image/jpeg', w: meta.tw, h: meta.th, s: psz };
          });
        }).catch(function () { return null; });
      }
      var orig = file.size <= max
        ? file.arrayBuffer().then(function (buf) { return run('enc', { cid: fc, bytes: buf }, [buf]); }).then(function (eo) { e.k = eo.key; return upload(cid, eo.blob); })
        : VID.upload(cid, file, prog(T('Hochladen'))).then(function (r) { e.k = r.key; e.x = 2; e.cs = r.cs; return r.id; });
      return Promise.all([orig, th]).then(function (r) {
        e.id = r[0]; f._prog = '';
        if (r[1]) { e.th = r[1]; var u = mem.get('t:' + r[1].id); if (u) memPut('t:' + e.id, u); }
        return e;
      });
    });
  }
  var sending = false;
  $('ct-compose').addEventListener('submit', function (e) {
    e.preventDefault();
    var c = S.cur, inp = $('ct-input'), text = inp.value.replace(/\s+$/, '');
    if (c && S.edit && !sending) { editSend(c, S.edit, text); return; }       // 2.5: Bearbeitung senden
    if (!c || sending || (!text.trim() && !S.files.length)) return;
    var notes = [];
    Object.keys(S.mentions).forEach(function (label) {
      if (text.indexOf(label) >= 0) { text = text.split(label).join('[[note:' + S.mentions[label] + ']]'); notes.push(S.mentions[label]); }
    });
    var pm = applyPeople(text); text = pm.text;              // 2.5: @Person -> <@kennung>, mentions neben dem Chiffrat
    var unf = unfFor(text);
    sending = true; $('ct-send').disabled = true;
    var reply = S.reply ? S.reply.id : null, askAi = aiOn(c) && aiMention(text);
    (S.files.length ? uploadFiles(c.id) : Promise.resolve([])).then(function (files) {
      var body = { v: 1, type: 'text', text: text };
      if (files.length) body.files = files;
      if (notes.length) body.notes = notes;
      return unfAttach(c, body, unf).then(function () { return sendBody(c.id, body, 'msg', reply, pm.refs.length ? { mentions: pm.refs } : null); });
    }).then(function (x) {
      inp.value = ''; autosize(); S.reply = null; S.files = []; S.mentions = {}; S.people = {}; drawReply(); drawFiles(); micDraw();
      draftDrop(c.id); unfReset();
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
    if (e.key === 'Escape' && S.edit) { e.preventDefault(); editCancel(); return; }
    if (e.key === 'ArrowUp' && !this.value && !S.edit && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {     // 2.5: letzte eigene bearbeiten
      var last = S.msgs.slice().reverse().filter(function (x) { return canEdit(S.cur, x); })[0];
      if (last) { e.preventDefault(); editStart(last); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('ct-compose').requestSubmit(); }
  });
  $('ct-input').addEventListener('input', function () {
    autosize(); picker(); aiHint(); micDraw(); draftSoon(); unfSoon();
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
    var ppl = peopleFor(S.cur, m[2]);                     // 2.5 (chatmention1): Mitglieder des Chats zuerst
    pk.t = setTimeout(function () {
      api('GET', '/api/chat/pages?q=' + q(m[2])).catch(function () { return { pages: [] }; }).then(function (r) {
        pk.items = aiItem.concat(ppl, r.pages || []); pk.i = 0; box.textContent = '';
        if (!pk.items.length) { box.hidden = true; return; }
        pk.items.forEach(function (p, i) {
          var b = el('button', 'picker-item' + (i === 0 ? ' on' : ''), p.ai ? '' : (p.icon || '📄') + ' ' + p.title); b.type = 'button'; b.setAttribute('role', 'option');
          if (p.person) { b.textContent = ''; b.classList.add('ct-pick-p'); b.appendChild(avatar((p.title || '?').charAt(0).toUpperCase(), p.key, 'ct-av-s', p.avatar));
                          b.appendChild(el('span', 'ct-pick-n', p.title)); b.appendChild(el('span', 'ct-pick-h', T('erwähnen'))); }
          if (p.ai) {                                    // KI (2.1): "@Marin" fragt die KI (nur auf Zuruf)
            b.classList.add('ct-pick-ai'); b.appendChild(HKAIAvatar(aiP(), 20)); b.appendChild(el('span', 'ct-pick-n', p.title));
            b.appendChild(el('span', 'ct-pick-h', T('KI fragen')));
          }
          b.addEventListener('mousedown', function (e) { e.preventDefault(); pickApply(i); });
          box.appendChild(b);
        });
        box.hidden = false;
      });
    }, aiItem.length || ppl.length ? 0 : 150);
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
    if (p.person) S.people[label] = p.ref;                 // 2.5: Person erwaehnen
    else if (!p.ai) S.mentions[label] = p.id;
    t.value = t.value.slice(0, pk.start) + label + ' ' + t.value.slice(t.selectionStart);
    var pos = pk.start + label.length + 1; t.setSelectionRange(pos, pos);
    $('ct-picker').hidden = true; t.focus(); aiHint();
  }

  // --- Chat-Alltag (2.5, Spezifikation A + C; saga/chat_prefs.py, saga/chat_unfurl.py) ---------------------------------------
  // Stummschalten mit Dauer + Modus (mute2), Anheften (chatpin1), als ungelesen markieren (markunread1), Gruppen-/Seiten-Chats
  // ausblenden (chathide1), Entwuerfe je Chat (nur dieser Browser), Emoji im Eingabefeld, @-Erwaehnung von Personen
  // (chatmention1: Token <@kennung> im verschluesselten Text, mentions neben dem Chiffrat), Linkvorschau (unfurl1: vom
  // Absender geholt, verschluesselt in der Nachricht, Bild als verschluesselter Anhang), Medienuebersicht.
  function fmtStrip(t) { return window.SagaChatFmt ? window.SagaChatFmt.strip(t) : t; }
  function fileIds(body) {
    var a = [];
    (body.files || []).forEach(function (f) { a.push(f.id); if (f.th && f.th.id) a.push(f.th.id); });
    if (body.preview && body.preview.img && body.preview.img.id) a.push(body.preview.img.id);
    return a;
  }
  function patchChat(c, b) {
    return api('PATCH', '/api/chats/' + c.id, b).then(function (r) {
      var nc = r.chat || {};
      ['muted', 'muted_until', 'notify', 'pinned', 'pinned_at', 'unread_mark', 'hidden', 'unread'].forEach(function (k) { if (k in nc) c[k] = nc[k]; });
      if (S.cur && S.cur.id === c.id) Object.assign(S.cur, nc);
      return loadList().then(function () { return nc; });
    }).catch(function (e) { HK.toast(e.message); throw e; });
  }
  // Stummschalten
  var fUntil = new Intl.DateTimeFormat(HK.locale, { weekday: 'short', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  function muteLabel(c) {
    if (!c.muted) return c.notify === 'mentions' ? T('Nur Erwähnungen') : c.notify === 'none' ? T('Mitteilungen aus') : T('Stummschalten');
    if (c.muted_until > 0) return T('Stumm bis {time}', { time: fUntil.format(new Date(c.muted_until * 1000)) });
    return T('Stummgeschaltet');
  }
  function muteSub(sm, c, after) {
    function go(b) { HK.closeMenu(); patchChat(c, b).then(function () { if (after) after(); }).catch(function () {}); }
    var now = Date.now() / 1000, eve = new Date(); eve.setHours(24, 0, 0, 0);
    if (c.muted) sm.appendChild(HK.menuItem(T('Stummschaltung aufheben'), function () { go({ muted_until: 0 }); }, { ic: 'bell' }));
    [[T('1 Stunde'), now + 3600], [T('8 Stunden'), now + 8 * 3600], [T('Bis morgen'), eve.getTime() / 1000 + 8 * 3600],
     [T('1 Woche'), now + 7 * 86400], [T('Für immer'), -1]].forEach(function (x) {
      sm.appendChild(HK.menuItem(x[0], function () { go({ muted_until: x[1] }); }, { on: x[1] === -1 && c.muted_until === -1 }));
    });
    sm.appendChild(el('div', 'menu-sep'));
    sm.appendChild(el('div', 'menu-label', T('Mitteilungen')));
    [['all', T('Alle Nachrichten')], ['mentions', T('Nur Erwähnungen und Antworten')], ['none', T('Keine')]].forEach(function (x) {
      sm.appendChild(HK.menuItem(x[1], function () { go({ notify: x[0] }); }, { on: (c.notify || 'all') === x[0] }));
    });
  }
  function muteMenu(anchor, c, after) { HK.openMenu(anchor, function (menu) { menu.textContent = ''; muteSub(menu, c, after); }); }
  function alltagItems(menu, anchor, c) {
    menu.appendChild(HK.menuItem(muteLabel(c), null, { ic: c.muted ? 'bell-off' : 'bell', sub: function (sm) { muteSub(sm, c); } }));
    menu.appendChild(HK.menuItem(c.pinned ? T('Loslösen') : T('Anheften'), function () {
      HK.closeMenu(); patchChat(c, { pinned: !c.pinned }).catch(function () {});
    }, { ic: 'pin' }));
    if (!c.unread && !c.unread_mark) menu.appendChild(HK.menuItem(T('Als ungelesen markieren'), function () {
      HK.closeMenu(); patchChat(c, { unread_mark: true }).then(function () { if (S.cur && S.cur.id === c.id) { closeChat(); history.replaceState(null, '', location.pathname); } }).catch(function () {});
    }, { ic: 'dot' }));
    if (c.can_hide) menu.appendChild(HK.menuItem(c.hidden ? T('Wieder anzeigen') : T('Für mich ausblenden'), function () {
      HK.closeMenu();
      patchChat(c, { hidden: !c.hidden }).then(function () {
        if (!c.hidden) return;
        HK.toast(T('Ausgeblendet – der Chat kommt zurück, wenn dich jemand erwähnt.'));
        if (S.cur && S.cur.id === c.id) { closeChat(); history.replaceState(null, '', location.pathname); }
      }).catch(function () {});
    }, { ic: c.hidden ? 'eye' : 'eye-off' }));
  }
  // Ausgeblendete Chats: Bereich unten in der Liste (beim Filtern durchsucht)
  function hiddenLoad() {
    if (S.hidT) return S.hidT;
    S.hidT = api('GET', '/api/chats?hidden=1&device=' + q(dev())).then(function (r) {
      S.hid = r.chats || []; S.hid.forEach(freeTitle); S.hidT = null; drawList();
    }).catch(function () { S.hidT = null; });
    return S.hidT;
  }
  function hiddenSection(ul, f) {
    if (!S.hiddenN) return;
    var open = S.hidOpen || !!f, list = (S.hid || []).filter(function (c) { return !f || chatName(c).toLowerCase().indexOf(f) >= 0; });
    if (f && !list.length) return;
    var hli = el('li', 'ct-arch-h'), tg = el('button', 'ct-arch-t'); tg.type = 'button'; tg.id = 'ct-hid-toggle';
    tg.setAttribute('aria-expanded', open ? 'true' : 'false');
    tg.appendChild(el('span', 'ct-arch-ic', open ? '▾' : '▸')); tg.appendChild(el('span', null, T('Ausgeblendet ({n})', { n: S.hiddenN })));
    tg.addEventListener('click', function () { S.hidOpen = !S.hidOpen; if (S.hidOpen) hiddenLoad(); drawList(); var x = $('ct-hid-toggle'); if (x) x.focus(); });
    hli.appendChild(tg); ul.appendChild(hli);
    if (open) list.forEach(function (c) { var li = listItem(c); li.classList.add('ct-hidden-c'); ul.appendChild(li); });
  }
  $('ct-filter').addEventListener('input', function () { if (this.value.trim() && S.hiddenN && !S.hid) hiddenLoad(); });

  // Entwuerfe: je Konto und Chat in localStorage (beim Abmelden geloescht, static/settings.js), nie an den Server
  var DRAFT = 'saga-chat-draft:' + ME + ':', draftT = null;
  function draftKey(cid) { return DRAFT + cid; }
  function draftSave() {
    clearTimeout(draftT); draftT = null;
    var c = S.cur; if (!c || S.edit) return;
    var v = $('ct-input').value;
    try {
      if (v.trim()) localStorage.setItem(draftKey(c.id), JSON.stringify({ t: v.slice(0, 20000), p: S.people || {}, n: S.mentions || {}, at: Date.now() }));
      else localStorage.removeItem(draftKey(c.id));
    } catch (e) { /* ohne Speicher: kein Entwurf */ }
    var x = S.chats.filter(function (y) { return y.id === c.id; })[0];
    if (x && !!x._draft !== !!v.trim()) { x._draft = !!v.trim(); drawList(); }
  }
  function draftSoon() { clearTimeout(draftT); draftT = setTimeout(draftSave, 400); }
  function draftLoad(cid) {
    var d = null;
    try { d = JSON.parse(localStorage.getItem(draftKey(cid)) || 'null'); } catch (e) { d = null; }
    if (!d || typeof d.t !== 'string') return;
    $('ct-input').value = d.t; S.people = d.p && typeof d.p === 'object' ? d.p : {}; S.mentions = d.n && typeof d.n === 'object' ? d.n : {};
    autosize(); micDraw(); unfSoon();
  }
  function draftDrop(cid) {
    try { localStorage.removeItem(draftKey(cid)); } catch (e) { /* egal */ }
    var x = S.chats.filter(function (y) { return y.id === cid; })[0]; if (x && x._draft) { x._draft = false; drawList(); }
  }
  function hasDraft(cid) { try { return !!localStorage.getItem(draftKey(cid)); } catch (e) { return false; } }
  window.addEventListener('pagehide', draftSave);
  try {                                    // 2.5: Kontowechsel ohne Abmelden ueber das Menue - Entwuerfe fremder Konten weg
    for (var di = localStorage.length - 1; di >= 0; di--) {
      var dk = localStorage.key(di); if (dk && dk.indexOf('saga-chat-draft:') === 0 && dk.indexOf(DRAFT) !== 0) localStorage.removeItem(dk);
    }
  } catch (e) { /* ohne Speicher: nichts zu loeschen */ }

  // Emoji im Eingabefeld: dieselbe Auswahl wie bei Reaktionen (static/chat-emoji.js)
  $('ct-emoji').addEventListener('click', function () {
    var b = this, inp = $('ct-input');
    if (!window.SagaEmoji || !S.cur) return;
    var a = inp.selectionStart, z = inp.selectionEnd;
    HK.openMenu(b, function (menu) {
      menu.textContent = '';
      window.SagaEmoji.picker(menu, function (e) {
        HK.closeMenu();
        inp.value = inp.value.slice(0, a) + e + inp.value.slice(z);
        var pos = a + e.length; inp.focus(); inp.setSelectionRange(pos, pos); a = z = pos;
        autosize(); micDraw(); draftSoon();
      });
    });
  });

  // @-Erwaehnung von Personen: Kennung = Handle (Stufe 3/foederiert) bzw. ref (v1), nie der Anmeldename anderer
  function memRef(c, m) { return m.handle || m.ref || ''; }
  function peopleFor(c, qy) {
    if (!c) return [];
    var ql = String(qy || '').toLowerCase();
    return (c.members || []).filter(function (m) { return !m.me && memRef(c, m) && String(m.name || '').toLowerCase().indexOf(ql) >= 0; })
      .slice(0, 6).map(function (m) { return { person: true, title: m.name || '?', ref: memRef(c, m), key: m.username, avatar: m.avatar }; });
  }
  function applyPeople(text) {
    var refs = [];
    Object.keys(S.people || {}).sort(function (a, b) { return b.length - a.length; }).forEach(function (label) {
      if (text.indexOf(label) < 0) return;
      var r = S.people[label]; text = text.split(label).join('<@' + r + '>'); if (refs.indexOf(r) < 0) refs.push(r);
    });
    return { text: text, refs: refs };
  }
  function mentionName(c, r) {
    if (!c) return null;
    if (r === c.me_ref || r === c.my_handle) return MYNAME;
    var m = (c.members || []).filter(function (x) { return x.handle === r || x.ref === r || (x.handle && x.handle.split('@')[0] === r); })[0];
    return m ? m.name : null;
  }
  function mentionEl(r) {
    var c = S.cur, nm = mentionName(c, r), me = c && (r === c.me_ref || r === c.my_handle);
    var sp = el('span', 'ct-mention' + (me ? ' ct-mention-me' : ''), '@' + (nm || T('Unbekannt')));
    sp.dataset.ref = r;
    return sp;
  }
  function plainMentions(t) {
    return String(t || '').replace(/<@(~[a-z2-7]{16}(?:@[a-z0-9.:\-]{1,260})?)>/g, function (_, r) { return '@' + (mentionName(S.cur, r) || T('Unbekannt')); });
  }
  function mentionsMe(c, m) {
    var t = m.body && m.body.text; if (!t || !c) return false;
    return (c.me_ref && t.indexOf('<@' + c.me_ref + '>') >= 0) || (c.my_handle && t.indexOf('<@' + c.my_handle + '>') >= 0);
  }

  // Linkvorschau: erste Adresse im Text; der Server holt (netguard), dieses Geraet verschluesselt sie in die Nachricht
  var U = { url: null, data: null, off: {}, t: null, req: 0 };
  function unfOn() { return hasFeat2('unfurl1'); }
  function hasFeat2(f) { return !!(S.ver && (S.ver.features || []).indexOf(f) >= 0); }
  api('GET', '/api/version').then(function (v) { S.ver = v; }).catch(function () {});
  function firstUrl(t) { var m = /(https?:\/\/[^\s<>"'`]+[^\s<>"'`.,;:!?)\]])/.exec(String(t || '')); return m ? m[1] : null; }
  function unfReset() { clearTimeout(U.t); U.url = null; U.data = null; U.off = {}; U.req++; unfDraw(); }
  function unfSoon() {
    clearTimeout(U.t);
    U.t = setTimeout(function () {
      var u = unfOn() && S.cur ? firstUrl($('ct-input').value) : null;
      if (u === U.url) return;
      U.url = u; U.data = null; unfDraw();
      if (!u || U.off[u]) return;
      var my = ++U.req;
      api('GET', '/api/chat/unfurl?url=' + q(u)).then(function (d) {
        if (my !== U.req || U.url !== u) return;
        U.data = d && (d.title || d.desc) ? d : null; unfDraw();
      }).catch(function () {});
    }, 600);
  }
  function unfFor(text) { var u = firstUrl(text); return u && U.data && U.url === u && !U.off[u] ? U.data : null; }
  function unfDraw() {
    var box = $('ct-unf'); box.textContent = '';
    var d = U.data; box.hidden = !d;
    if (!d) return;
    var card = unfBox(d, d.image ? 'data:' + d.image.mime + ';base64,' + d.image.b64 : null);
    var x = el('button', 'icon-btn ct-unf-x', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Vorschau entfernen')); x.title = T('Vorschau entfernen');
    x.addEventListener('click', function () { U.off[U.url] = true; U.data = null; unfDraw(); $('ct-input').focus(); });
    card.appendChild(x); box.appendChild(card);
  }
  function unfBox(d, imgUrl) {
    var card = el('div', 'ct-unf-card');
    if (imgUrl) { var im = el('img', 'ct-unf-img'); im.alt = ''; im.src = imgUrl; card.appendChild(im); }
    var t = el('div', 'ct-unf-t');
    if (d.site) t.appendChild(el('span', 'ct-unf-site', String(d.site).slice(0, 80)));
    if (d.title) t.appendChild(el('b', 'ct-unf-title', String(d.title).slice(0, 200)));
    if (d.desc) t.appendChild(el('span', 'ct-unf-desc', String(d.desc).slice(0, 300)));
    card.appendChild(t);
    return card;
  }
  // Vorschau in die Nachricht: {url, title, desc, site, img?: {id, k, m, w, h, s}} - Bild wie ein Vorschaubild verschluesselt
  function unfAttach(c, body, d) {
    if (!d) return Promise.resolve();
    body.preview = { url: String(d.url).slice(0, 2000), title: String(d.title || '').slice(0, 200), desc: String(d.desc || '').slice(0, 300), site: String(d.site || '').slice(0, 80) };
    if (!d.image) return Promise.resolve();
    var raw = Uint8Array.from(atob(d.image.b64), function (ch) { return ch.charCodeAt(0); }).buffer, n = raw.byteLength;
    return run('enc', { cid: fileCid(c), bytes: raw }, [raw]).then(function (eo) {
      return upload(c.id, new Blob([eo.blob])).then(function (id) {
        body.preview.img = { id: id, k: eo.key, m: 'image/jpeg', w: d.image.w, h: d.image.h, s: n };
      });
    }).catch(function () { /* ohne Bild */ });
  }
  function unfCard(c, m) {
    var p = m.body.preview || {}, u = String(p.url || '');
    if (!/^https?:\/\//.test(u)) return null;
    var a = el('a', 'ct-unf-msg'); a.href = u; a.target = '_blank'; a.rel = 'noopener noreferrer';
    var card = unfBox(p, null); a.appendChild(card);
    var f = p.img;
    if (f && f.id && f.k) {
      var im = el('img', 'ct-unf-img'); im.alt = ''; card.insertBefore(im, card.firstChild);
      var cid = fileCid(c, m), key = 't:' + f.id, have = memGet(key);
      if (have) im.src = have;
      else lazy(im, function (signal) {
        return getBlob(f, signal).then(function (buf) { return run('dec', { cid: cid, blob: buf, key: f.k }, [buf]); }).then(function (r) {
          var url = URL.createObjectURL(new Blob([r.plain], { type: 'image/jpeg' })); memPut(key, url); im.src = url;
        });
      }, function () { im.remove(); });
    }
    return a;
  }

  // Kachel der Medienuebersicht: Vorschaubild direkt (das Nachladen nach Sichtbarkeit gilt nur im Nachrichtenbereich)
  function mediaTile(cid, f) {
    var name = String(f.n || T('Datei')).slice(0, 200), b = el('button', 'ct-md-tile'); b.type = 'button'; b.setAttribute('aria-label', name);
    var im = el('img'); im.alt = name; b.appendChild(im);
    if (VID && VID.isVideo(f)) b.appendChild(el('span', 'ct-md-play', '▶'));
    thumbUrl(cid, f).then(function (u) { im.src = u; }, function () { b.textContent = '🖼'; });
    b.addEventListener('click', function () { if (VID && VID.isVideo(f)) viewVideo(cid, f, name); else viewImage(cid, f, name); });
    return b;
  }
  // Medienuebersicht: Bilder/Videos, Dateien, Links aus den Nachrichten des Chats (Server hilft mit ?has=file)
  function mediaView(c) {
    var box = overlay(T('Medien, Dateien, Links'), { wide: true });
    var tabs = el('div', 'ct-md-tabs'), list = el('div', 'ct-md-list'), more = btn(T('Ältere laden'), 'small ct-md-more');
    tabs.setAttribute('role', 'tablist');
    var st = { tab: 'media', files: [], links: [], before: 0, done: false, linkBefore: 0, linkDone: false };
    [['media', T('Medien')], ['files', T('Dateien')], ['links', T('Links')]].forEach(function (x) {
      var b = el('button', 'ct-md-tab', x[1]); b.type = 'button'; b.setAttribute('role', 'tab'); b.dataset.tab = x[0];
      b.addEventListener('click', function () { st.tab = x[0]; draw(); });
      tabs.appendChild(b);
    });
    box.appendChild(tabs); box.appendChild(list); box.appendChild(more);
    function decrypt1(m, devs, ids) {
      try { return isFed(c) && m.sender_handle ? decryptV2(c, m) : decrypt(c.id, m, devs || {}, ids || {}); } catch (e) { return null; }
    }
    function load(kind) {
      var linkMode = kind === 'links', b4 = linkMode ? st.linkBefore : st.before;
      var url = '/api/chats/' + c.id + '/messages?device=' + q(dev()) + (linkMode ? '' : '&has=file') + '&limit=100' + (b4 ? '&before=' + b4 : '');
      return api('GET', url).then(function (r) {
        var ms = r.messages || [];
        ms.forEach(function (m) {
          var d = decrypt1(m, r.devices, r.idents); if (!d || !d.body) return;
          m.body = d.body;
          if (linkMode) {
            var t = String(d.body.text || ''), re = /(https?:\/\/[^\s<>"'`]+[^\s<>"'`.,;:!?)\]])/g, x;
            while ((x = re.exec(t))) st.links.push({ url: x[1], m: m });
          } else (d.body.files || []).forEach(function (f) { st.files.push({ f: f, m: m }); });
        });
        if (ms.length) { if (linkMode) st.linkBefore = ms[0].seq; else st.before = ms[0].seq; }
        if (!r.more) { if (linkMode) st.linkDone = true; else st.done = true; }
      });
    }
    function draw() {
      Array.prototype.forEach.call(tabs.children, function (b) { var on = b.dataset.tab === st.tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', on ? 'true' : 'false'); });
      list.textContent = ''; list.className = 'ct-md-list ct-md-' + st.tab;
      var items = st.tab === 'links' ? st.links : st.files.filter(function (x) {
        var med = IMG.test(x.f.m) || (VID && VID.isVideo(x.f)); return st.tab === 'media' ? med : !med && !x.f.voice;
      });
      if (!items.length) list.appendChild(el('p', 'hk-hint', T('Nichts gefunden (in den geladenen Nachrichten).')));
      items.forEach(function (x) {
        if (st.tab === 'links') {
          var a = el('a', 'ct-md-link', x.url); a.href = x.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; list.appendChild(a);
        } else list.appendChild(st.tab === 'media' ? mediaTile(fileCid(c, x.m), x.f) : fileEl(fileCid(c, x.m), x.f));
      });
      var done = st.tab === 'links' ? st.linkDone : st.done;
      more.hidden = done;
    }
    more.addEventListener('click', function () { busy(more, true); load(st.tab).then(function () { busy(more, false); draw(); }, function (e) { busy(more, false); HK.toast(e.message); }); });
    Promise.all([load('media'), load('links')]).then(draw, function (e) { HK.toast(e.message); draw(); });
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
      if (c.kind === 'dm' && others[0]) menu.appendChild(HK.menuItem(T('Sicherheitsnummer'), function () { HK.closeMenu(); safety(others[0]); }, { ic: 'lock' }));
      if (c.kind === 'dm') menu.appendChild(HK.menuItem(T('Mitglieder'), function () { HK.closeMenu(); members(); }, { ic: 'users', hint: String(c.member_count) }));
      else menu.appendChild(HK.menuItem(T('Gruppeninfo …'), function () { HK.closeMenu(); groupInfo(); }, { ic: 'users', hint: String(c.member_count) }));
      if (S.ai && S.ai.chat) menu.appendChild(HK.menuItem(c.ai ? T('{name} hier nicht mehr erlauben', { name: aiP().name }) : T('{name} in diesem Chat erlauben', { name: aiP().name }),
        function () { HK.closeMenu(); aiToggle(c); }, { ic: 'sparkles' }));
      menu.appendChild(HK.menuItem(T('Medien, Dateien, Links'), function () { HK.closeMenu(); mediaView(c); }, { ic: 'image' }));     // 2.5
      alltagItems(menu, anchor, c);                       // 2.5: Stummschalten (Dauer/Modus), Anheften, ungelesen, ausblenden
      // Bughunt V12: die Laufzeiten als Untermenue (wie "Verschieben nach ›"), die aktuelle Wahl als Hinweis
      var mx = (S.me && S.me.limits && S.me.limits.retention_max) || 0;
      var rets = RET.filter(function (r) { return !(mx && (r[0] === 0 || r[0] > mx)); });
      var curR = rets.filter(function (r) { return c.retention_s === r[0]; })[0];
      menu.appendChild(HK.menuItem(T('Nachrichten automatisch löschen nach'), null, { ic: 'timer', hint: curR ? curR[1] : '', sub: function (sm) {
        rets.forEach(function (r) {
          sm.appendChild(HK.menuItem(r[1], function () {
            HK.closeMenu();
            api('PATCH', '/api/chats/' + c.id, { retention_s: r[0] }).then(function () { HK.toast(T('Gilt für neue Nachrichten.')); loadList(); }).catch(function (e) { HK.toast(e.message); });
          }, { on: c.retention_s === r[0] }));
        });
      } }));
      if (c.kind === 'free') {
        if (c.can_rename !== false) menu.appendChild(HK.menuItem(T('Umbenennen …'), function () { HK.closeMenu(); rename(); }, { ic: 'edit' }));
        menu.appendChild(HK.menuItem(T('Gruppe verlassen'), function () { HK.closeMenu(); leaveGroup(c); }, { ic: 'logout', danger: true }));
      }
      manageItems(menu, c);
    });
  });

  // --- Archivieren und Loeschen (2.1) -------------------------------------------------------------------------------
  // Menue der Chatliste (Rechtsklick, Kontextmenue-Taste, Langdruck)
  function chatMenu(anchor, c) {
    HK.openMenu(anchor, function (menu) {
      menu.textContent = '';
      menu.appendChild(HK.menuItem(T('Öffnen'), function () { HK.closeMenu(); location.hash = 'c' + c.id; if (S.cur && S.cur.id === c.id) openChat(c.id); }, { ic: 'external' }));
      alltagItems(menu, anchor, c);                       // 2.5: Stummschalten (Dauer/Modus), Anheften, ungelesen, ausblenden
      manageItems(menu, c);
    });
  }
  function manageItems(menu, c) {
    grpChatItems(menu, c);                              // 2.5: Sperren / Melden im Direkt-Chat
    menu.appendChild(HK.menuItem(c.archived ? T('Aus Archiv holen') : T('Archivieren'), function () { HK.closeMenu(); archive(c, !c.archived); },
      { ic: c.archived ? 'archive-out' : 'archive' }));
    menu.appendChild(HK.menuItem(T('Chat löschen …'), function () { HK.closeMenu(); deleteDialog(c); }, { ic: 'trash', danger: true }));
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
    if (isRemote(c) && c.kind !== 'dm') box.appendChild(el('p', 'hk-hint', c.home_self ? T('Dieser Chat liegt auf diesem Server; Personen anderer Saga-Server sind über ihren Server dabei.')
      : T('Dieser Chat liegt auf {host}. Mitglieder ändert man dort (über diesen Server).', { host: c.home || '?' })));
    var ul = el('ul', 'ct-devs'); box.appendChild(ul);
    c.members.forEach(function (m) {
      var li = el('li', 'ct-dev'); li.appendChild(avatar((m.name || '?').charAt(0).toUpperCase(), m.username, '', m.avatar));
      var t = el('div', 'ct-dev-t'); t.appendChild(el('b', null, m.me ? m.name + ' (' + T('du') + ')' : m.name));
      // 2.4: Anmeldenamen anderer nie zeigen (nur der eigene; Personen anderer Server mit ihrem Server)
      var sub = m.handle && m.host && isRemote(c) && !m.me ? T('auf {host}', { host: m.host }) : (m.me ? m.username : (m.sub || ''));
      if (sub) t.appendChild(el('span', 'hk-hint', sub));
      li.appendChild(t);
      if (!m.me) li.appendChild(btn(T('Sicherheitsnummer'), 'small', function () { safety(m); }));
      if (c.kind === 'free' && c.role === 'admin' && !m.me) li.appendChild(btn(T('Entfernen'), 'btn-danger small', function () {
        api('DELETE', '/api/chats/' + c.id + '/members/' + q(m.username)).then(function () { closeOverlay(); openChat(c.id); loadList(); }).catch(function (e) { HK.toast(e.message); });
      }));
      ul.appendChild(li);
    });
    if (c.kind === 'free' && c.role === 'admin') addSearch(box, c);
  }
  function leaveGroup(c) {
    HK.ask({ title: T('Gruppe verlassen?'), text: T('Du bekommst keine neuen Nachrichten mehr.'), ok: T('Verlassen'), danger: true }).then(function (y) {
      if (y) api('DELETE', '/api/chats/' + c.id + '/members/' + q(ME)).then(function () { closeOverlay(); closeChat(); loadList(); }).catch(function (e) { HK.toast(e.message); });
    });
  }
  // Personensuche "Person hinzufuegen" (freie Gruppe, Admin) - im Dialog Gruppeninfo (und frueher "Mitglieder")
  function addSearch(box, c) {
    function added(c) { if (!GI) closeOverlay(); openChat(c.id); loadList(); }     // Gruppeninfo bleibt offen (zeichnet neu)
    {
      var inp = el('input', 'hk-in'); inp.type = 'search'; inp.placeholder = T('Person hinzufügen'); inp.setAttribute('aria-label', T('Person hinzufügen'));
      var res = el('ul', 'ct-people ct-people-list'); box.appendChild(inp); box.appendChild(res);
      // 2.5: Kontakte - ohne Eingabe als Liste (wer noch nicht Mitglied ist; fremde nur in Gruppen ueber Servergrenzen)
      var isMem = function (ref) { return c.members.some(function (m) { return m.ref === ref || m.handle === ref || m.username === ref; }); };
      // 2.5 (pagechatmembers1): Seiten-Chat - Hinzufuegen teilt die Seite (Rolle waehlbar, Standard Lesen)
      var page = c.kind === 'page', roleSel = null;
      if (page) {
        var pr = el('div', 'ct-gi-prole');
        pr.appendChild(el('p', 'hk-hint ct-gi-phint', T('Die Seite wird mit dieser Person geteilt.')));
        roleSel = el('select', 'hk-in ct-gi-role'); roleSel.setAttribute('aria-label', T('Rolle auf der Seite'));
        [['view', T('Lesen')], ['comment', T('Kommentieren')], ['edit', T('Bearbeiten')]].forEach(function (o) {
          var op = el('option', null, o[1]); op.value = o[0]; roleSel.appendChild(op);
        });
        pr.appendChild(roleSel); box.insertBefore(pr, inp);
      }
      var fedOk = function () { return isFed(c) || page; };
      var addOne = function (u) {
        var body = u.remote ? { handle: u.handle } : { user: u.username };
        if (roleSel) body.role = roleSel.value;
        api('POST', '/api/chats/' + c.id + '/members', body).then(function () {
          added(c);
          if (c._title) sendBody(c.id, { v: 1, type: 'title', title: c._title }, 'title').catch(function () {});   // Name fuer Neue lesbar
        }).catch(function (e) { HK.toast(e.message); });
      };
      var showContacts = function () {
        drawContacts(res, addOne, function (k) { return !isMem(k.ref) && (!k.remote || fedOk()); }).catch(function () { res.textContent = ''; });
      };
      showContacts();
      var tmr = null;
      inp.addEventListener('input', function () {
        clearTimeout(tmr); tmr = setTimeout(function () {
          if (!inp.value.trim()) { showContacts(); return; }
          var v = inp.value.trim();
          api('GET', '/api/chat/people?q=' + q(v)).then(function (r) {
            if (inp.value.trim() !== v) return;
            res.textContent = '';
            if (!r.users.length && !(r.remote || []).length && fedAddr(v)) res.appendChild(fedHint());
            if (!fedOk() && (r.remote || []).length) res.appendChild(el('li', 'hk-hint', T('Personen anderer Saga-Server lassen sich nur zu neuen Gruppen hinzufügen – bitte eine neue Gruppe anlegen.')));
            r.users.filter(function (u) { return !isMem(u.username); }).forEach(function (u) {        // Kontakte zuerst (contact: true)
              res.appendChild(personRow({ key: u.username, ref: u.username, name: u.name, sub: u.sub, avatar: u.avatar, contact: !!u.contact, username: u.username }, addOne));
            });
            if (fedOk()) (r.remote || []).filter(function (u) { return !isMem(u.handle); }).forEach(function (u) {
              res.appendChild(personRow({ key: u.handle, ref: u.handle, name: u.label || u.name, sub: T('Person auf {host}', { host: u.host }), avatar: u.avatar,
                                          remote: true, contact: !!u.contact, handle: u.handle }, addOne));
            });
          }).catch(function () {});
        }, 250);
      });
      return inp;
    }
  }

  // --- Gruppen (2.5, Chat-Standard B; Server: saga/chat_grp.py) ---------------------------------------------------
  // Bild und Beschreibung freier Gruppen sind verschluesselte Steuer-Nachrichten (kind 'desc'/'avatar', wie der Name);
  // Rechte perm_info/perm_send setzt der Server durch (can_send, can_edit_info); Anheften (pins), Admin-Loeschen
  // (Grabstein kind 'tomb'), Blockieren lokaler Konten (members[].blocked) und Melden an die Saga-Admins.
  var GRP_AV = 256, DESC_MAX = 500;
  function grpOpen(c, m) {                               // Steuer-Nachricht entschluesseln (v1 bzw. v2) -> Inhalt oder null
    try {
      if (isFed(c) && m.sender_handle) {
        var f = (S.fk || {})[m.sender_handle];
        var key = m.key_for === 'device' ? X.openKey(m.key, K.device.enc_pk, K.device.enc_sk) : X.openKey(m.key, K.backup_pk, K.backup_sk);
        var d = f && f.keys && f.trust !== 'reject' && f.keys.devices.filter(function (x) { return x.id === m.sender_device; })[0];
        if (!d || !X.verifyMessageV2(c.ref, m, ub(d.sign_pub, 32))) return null;
        return X.decryptMessageV2(c.ref, m, key);
      }
      var b = decrypt(c.id, m, m.device ? { [m.sender_device]: m.device } : {}, m.ident_pub ? { [m.sender.username]: m.ident_pub } : {});
      return b && b.verified ? b.body : null;
    } catch (e) { return null; }
  }
  function grpInfo(c) {
    if (!c || c.kind !== 'free' || (!c.desc_msg && !c.avatar_msg)) return;
    var hs = [c.desc_msg, c.avatar_msg].filter(function (m) { return m && m.sender_handle; }).map(function (m) { return m.sender_handle; });
    (isFed(c) && hs.length ? fedKeys(hs).then(function (fk) { S.fk = Object.assign({}, fk, S.fk || {}); }).catch(function () {}) : Promise.resolve()).then(function () {
      var dsc = c.desc_msg ? grpOpen(c, c.desc_msg) : null, av = c.avatar_msg ? grpOpen(c, c.avatar_msg) : null;
      var x = S.chats.filter(function (y) { return y.id === c.id; })[0], changed = false;
      if (dsc && dsc.type === 'desc') { c._desc = String(dsc.desc || '').slice(0, DESC_MAX); if (x) x._desc = c._desc; changed = true; }
      if (av && av.type === 'avatar') {
        c._avf = av.file && av.file.id ? av.file : null; if (x) x._avf = c._avf; changed = true;
        if (c._avf) thumbUrl(fileCid(c, c.avatar_msg), c._avf).then(function (u) {
          c._av = u; if (x) x._av = u; drawList(); if (S.cur && S.cur.id === c.id) { S.cur._av = u; drawHead(); if (GI && GI.id === c.id && ov) GI.draw(); }
        }).catch(function () {});
      }
      if (changed && S.cur && S.cur.id === c.id) { S.cur._desc = c._desc; if (GI && GI.id === c.id && ov) GI.draw(); }
    });
  }
  function grpAvatar(c, cls, fallback) {                 // Gruppenbild (entschluesselt) oder Symbol
    var a = avatar(fallback, 'c' + c.id, cls);
    if (c._av) { var im = el('img', 'hk-av-img'); im.src = c._av; im.alt = ''; a.classList.add('hk-av-box', 'hk-av-on'); a.appendChild(im); }
    return a;
  }
  // Bild quadratisch zuschneiden (Mitte) und verkleinern -> JPEG-Bytes
  function grpSquare(file) {
    return new Promise(function (res, rej) {
      var url = URL.createObjectURL(file), im = new Image();
      im.onload = function () {
        var s = Math.min(im.naturalWidth, im.naturalHeight), cv = document.createElement('canvas'); cv.width = cv.height = GRP_AV;
        cv.getContext('2d').drawImage(im, (im.naturalWidth - s) / 2, (im.naturalHeight - s) / 2, s, s, 0, 0, GRP_AV, GRP_AV);
        URL.revokeObjectURL(url);
        cv.toBlob(function (b) { if (!b) { rej(new Error(T('Bild nicht lesbar.'))); return; } b.arrayBuffer().then(res, rej); }, 'image/jpeg', 0.85);
      };
      im.onerror = function () { URL.revokeObjectURL(url); rej(new Error(T('Bild nicht lesbar.'))); };
      im.src = url;
    });
  }
  function grpSetAvatar(c, file) {
    return grpSquare(file).then(function (buf) {
      var bytes = buf.slice(0), fc = fileCid(c);
      return run('enc', { cid: fc, bytes: buf }, [buf]).then(function (eo) {
        return upload(c.id, new Blob([eo.blob])).then(function (id) {
          memPut('t:' + id, URL.createObjectURL(new Blob([bytes], { type: 'image/jpeg' })));
          var f = { id: id, k: eo.key, m: 'image/jpeg', n: 'gruppe.jpg', s: bytes.byteLength, w: GRP_AV, h: GRP_AV };
          return sendBody(c.id, { v: 1, type: 'avatar', file: f, files: [f] }, 'avatar');
        });
      });
    }).then(function () { HK.toast(T('Gruppenbild gespeichert.')); return giAfter(c); });
  }
  function grpClearAvatar(c) {
    return sendBody(c.id, { v: 1, type: 'avatar', file: null }, 'avatar').then(function () { c._av = null; c._avf = null; HK.toast(T('Gruppenbild entfernt.')); return giAfter(c); });
  }
  function grpSetDesc(c, text) {
    return sendBody(c.id, { v: 1, type: 'desc', desc: String(text || '').slice(0, DESC_MAX) }, 'desc').then(function () { c._desc = text; return giAfter(c); });
  }
  function grpPerm(c, key, val) {
    var b = {}; b[key] = val;
    return api('PATCH', '/api/chats/' + c.id, b).then(function () { return giAfter(c); }).catch(function (e) { HK.toast(e.message); if (GI) GI.draw(); });
  }
  // Gruppeninfo: Bild, Beschreibung, Rechte (aus groupInfo/draw aufgerufen)
  function grpInfoSection(wrap, c, draw) {
    if (c.kind !== 'free') return;
    var info2 = c.can_info2 === true, edit = c.can_edit_info !== false && !c.readonly;
    // Beschreibung
    var ds = el('div', 'ct-gi-desc');
    if (c._desc) ds.appendChild(el('p', 'ct-gi-desc-t', c._desc));
    else if (info2) ds.appendChild(el('p', 'hk-hint ct-gi-desc-t', T('Keine Beschreibung.')));
    if (info2) {
      var eb = btn(c._desc ? T('Beschreibung bearbeiten') : T('Beschreibung hinzufügen'), 'small ct-gi-desc-b'); eb.dataset.gi = 'desc';
      eb.addEventListener('click', function () {
        ds.textContent = '';
        var ta = el('textarea', 'hk-in ct-gi-desc-in'); ta.maxLength = DESC_MAX; ta.rows = 3; ta.value = c._desc || ''; ta.placeholder = T('Worum geht es in dieser Gruppe?');
        ta.setAttribute('aria-label', T('Beschreibung der Gruppe'));
        var cnt = el('span', 'hk-hint ct-gi-cnt', (ta.value.length) + ' / ' + DESC_MAX);
        ta.addEventListener('input', function () { cnt.textContent = ta.value.length + ' / ' + DESC_MAX; });
        var row = el('div', 'ct-gi-rename'), sv = btn(T('Speichern'), 'btn-primary small ct-gi-desc-save'), st = btn(T('Abbrechen'), 'small');
        row.appendChild(sv); row.appendChild(st); row.appendChild(cnt);
        ds.appendChild(ta); ds.appendChild(row); ta.focus();
        sv.addEventListener('click', function () { busy(sv, true); grpSetDesc(c, ta.value.trim()).catch(function (e) { busy(sv, false); HK.toast(e.message); }); });
        st.addEventListener('click', draw);
      });
      ds.appendChild(eb);
    }
    wrap.appendChild(ds);
    // Rechte (nur Admins schalten)
    if (c.can_perms || (c.perm_info === 'admins' || c.perm_send === 'admins')) {
      wrap.appendChild(el('h3', 'ct-gi-h', T('Gruppenrechte')));
      var pr = el('div', 'ct-gi-perms');
      [['perm_send', T('Nur Admins dürfen Nachrichten senden')], ['perm_info', T('Nur Admins dürfen Name, Bild und Beschreibung ändern und Nachrichten anheften')]].forEach(function (p) {
        var lab = el('label', 'hk-check ct-gi-perm'), cb = el('input'); cb.type = 'checkbox'; cb.checked = c[p[0]] === 'admins'; cb.dataset.gi = p[0];
        cb.disabled = !c.can_perms;
        cb.addEventListener('change', function () { cb.disabled = true; grpPerm(c, p[0], cb.checked ? 'admins' : 'all'); });
        lab.appendChild(cb); lab.appendChild(document.createTextNode(' ' + p[1])); pr.appendChild(lab);
      });
      if (!c.can_perms) pr.appendChild(el('p', 'hk-hint', T('Nur Admins ändern die Rechte.')));
      wrap.appendChild(pr);
    }
    if (!edit && c.perm_info === 'admins') wrap.appendChild(el('p', 'hk-hint ct-gi-ro', T('Name, Bild und Beschreibung ändern nur Admins.')));
  }
  function grpHeadAvatar(hd, c) {                        // Gruppeninfo-Kopf: Bild mit "Bild ändern"
    var av = grpAvatar(c, 'ct-gi-av', kindIc(c) || '👥');
    if (c.kind !== 'free' || c.can_info2 !== true) { hd.appendChild(av); return; }
    var b = el('button', 'ct-gi-avb'); b.type = 'button'; b.dataset.gi = 'avatar'; b.title = T('Gruppenbild ändern'); b.setAttribute('aria-label', T('Gruppenbild ändern'));
    b.appendChild(av); b.appendChild(el('span', 'ct-gi-avcam', '📷'));
    var inp = el('input'); inp.type = 'file'; inp.accept = 'image/png,image/jpeg,image/webp,image/gif'; inp.hidden = true; inp.className = 'ct-gi-avfile';
    inp.addEventListener('change', function () {
      var f = inp.files && inp.files[0]; inp.value = ''; if (!f) return;
      grpSetAvatar(c, f).catch(function (e) { HK.toast(e.message); });
    });
    b.addEventListener('click', function () {
      if (!c._avf) { inp.click(); return; }
      HK.openMenu(b, function (menu) {
        menu.textContent = '';
        menu.appendChild(HK.menuItem(T('Neues Bild wählen …'), function () { HK.closeMenu(); inp.click(); }, { ic: 'camera' }));
        menu.appendChild(HK.menuItem(T('Bild entfernen'), function () { HK.closeMenu(); grpClearAvatar(c).catch(function (e) { HK.toast(e.message); }); }, { ic: 'x', danger: true }));
      });
    });
    hd.appendChild(b); hd.appendChild(inp);
  }
  // Angeheftete Nachrichten: Leiste oben im Chat (Inhalt aus den geladenen, entschluesselten Nachrichten)
  function drawPins(c) {
    var bar = $('ct-pins'); if (!bar) return;
    var ids = (c && c.pins) || [];
    bar.textContent = ''; bar.hidden = !ids.length; if (!ids.length) return;
    var i = Math.min(S.pinI || 0, ids.length - 1), mid = ids[ids.length - 1 - i], m = S.byId[mid];
    var go = el('button', 'ct-pin-go'); go.type = 'button';
    go.appendChild(el('span', 'ct-pin-ic', '📌'));
    var tx = el('span', 'ct-pin-t');
    tx.appendChild(el('b', null, ids.length > 1 ? T('Angeheftet ({i}/{n})', { i: i + 1, n: ids.length }) : T('Angeheftet')));
    tx.appendChild(el('span', 'ct-pin-p', m ? (m.sender ? m.sender.name + ': ' : '') + preview(m) : T('Ältere Nachricht')));
    go.appendChild(tx);
    go.addEventListener('click', function () {
      var row = rowOf(mid);
      if (row) { row.scrollIntoView({ block: 'center' }); row.classList.add('flash'); setTimeout(function () { row.classList.remove('flash'); }, 1200); }
      else HK.toast(T('Die Nachricht ist noch nicht geladen – weiter nach oben blättern.'));
      if (ids.length > 1) { S.pinI = (i + 1) % ids.length; drawPins(c); }
    });
    bar.appendChild(go);
    if (c.can_pin) {
      var x = el('button', 'icon-btn ct-pin-x', '✕'); x.type = 'button'; x.title = T('Lösen'); x.setAttribute('aria-label', T('Anheftung lösen'));
      x.addEventListener('click', function () { pinMsg(c, mid, false); });
      bar.appendChild(x);
    }
  }
  function pinMsg(c, mid, on) {
    return api(on ? 'POST' : 'DELETE', '/api/chats/' + c.id + '/pins/' + mid).then(function (r) {
      c.pins = r.pins || []; if (S.cur && S.cur.id === c.id) S.cur.pins = c.pins; S.pinI = 0; drawPins(c);
      HK.toast(on ? T('Angeheftet.') : T('Gelöst.'));
    }).catch(function (e) { HK.toast(e.message); });
  }
  function tombRow(m, d) {
    var row = el('div', 'ct-call ct-sys ct-tomb'); row.dataset.id = m.id;
    var who = m.sender ? m.sender.name : '';
    row.appendChild(el('span', null, '🗑 ' + (who ? T('Nachricht von {name} – von einem Admin gelöscht', { name: who }) : T('Von einem Admin gelöscht'))));
    var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); row.appendChild(tm);
    return row;
  }
  function tombLive(d) {
    var m = S.byId[d.id]; if (!m) return;
    m.kind = 'tomb'; m.body = null; m.verified = true;
    if (S.cur && (S.cur.pins || []).indexOf(d.id) >= 0) S.cur.pins = S.cur.pins.filter(function (x) { return x !== d.id; });
    if (S.reply && S.reply.id === d.id) { S.reply = null; drawReply(); }
    if (S.sel) delete S.sel.ids[d.id];
    drawMsgs(); drawPins(S.cur);
  }
  // Gesperrte Personen (block2): in Gruppen einklappbar ausgeblendet
  function grpBlocked(c, m) {
    if (!c || c.kind === 'dm' || !m.sender) return false;
    var u = m.sender.username;
    return c.members.some(function (x) { return x.blocked && (x.username === u || x.handle === u || x.handle === m.sender_handle); });
  }
  S.unblocked = S.unblocked || {};
  function blockedRow(m, d) {
    if (S.unblocked[m.id]) return null;
    var row = el('div', 'ct-call ct-sys ct-blockedmsg'); row.dataset.id = m.id;
    row.appendChild(el('span', null, '⛔ ' + T('Nachricht einer gesperrten Person')));
    row.appendChild(btn(T('Anzeigen'), 'small', function () { S.unblocked[m.id] = true; drawMsgs('keep'); }));
    var tm = el('time', null, fTime.format(d)); tm.dateTime = d.toISOString(); row.appendChild(tm);
    return row;
  }
  function blockTarget(c, m) {                            // Handle der Person (nie der Anmeldename)
    if (m.sender_handle) return m.sender_handle;
    var x = c.members.filter(function (y) { return y.username === (m.sender || {}).username; })[0];
    return x ? (x.handle || x.ref) : null;
  }
  function blockPerson(c, target, name) {
    return HK.ask({ title: T('{name} sperren?', { name: name }), text: T('{name} kann dir keine Direktnachrichten mehr schicken und dich nicht anrufen. In Gruppen werden die Nachrichten eingeklappt. {name} erfährt davon nichts.', { name: name }),
      ok: T('Sperren'), danger: true }).then(function (y) {
      if (!y) return;
      return api('POST', '/api/chat/blocks', { target: target }).then(function () { HK.toast(T('{name} ist gesperrt.', { name: name })); if (S.cur) openChat(S.cur.id); })
        .catch(function (e) { HK.toast(e.message); });
    });
  }
  function reportDialog(c, msgs) {
    var box = overlay(T('Melden'));
    box.appendChild(el('p', 'hk-hint', T('Die Meldung geht an die Admins dieses Saga-Servers. Chats sind Ende-zu-Ende-verschlüsselt – die Admins sehen nur, was du hier mitschickst.')));
    var sel = el('select', 'hk-in ct-rep-reason'); sel.setAttribute('aria-label', T('Grund'));
    [['spam', T('Spam')], ['abuse', T('Belästigung oder Beleidigung')], ['illegal', T('Rechtswidrige Inhalte')], ['other', T('Sonstiges')]].forEach(function (o) {
      var op = el('option', null, o[1]); op.value = o[0]; sel.appendChild(op);
    });
    var lab = el('label', 'ct-rep-l', T('Grund')); box.appendChild(lab); box.appendChild(sel);
    var inc = el('label', 'hk-check ct-rep-inc'), cb = el('input'); cb.type = 'checkbox'; cb.checked = true;
    inc.appendChild(cb); inc.appendChild(document.createTextNode(' ' + T('Text der gemeldeten Nachrichten mitschicken')));
    var ta = el('textarea', 'hk-in ct-rep-text'); ta.rows = 4; ta.maxLength = 2000;
    ta.value = msgs.map(function (m) { return (m.sender ? m.sender.name + ': ' : '') + plainText(m); }).join('\n').slice(0, 2000);
    ta.setAttribute('aria-label', T('Mitgeschickter Text'));
    cb.addEventListener('change', function () { ta.hidden = !cb.checked; });
    box.appendChild(inc); box.appendChild(ta);
    box.appendChild(el('p', 'hk-hint', T('Du kannst den Text vor dem Senden ändern oder kürzen.')));
    var bar = el('div', 'hk-actions'); box.appendChild(bar);
    bar.appendChild(btn(T('Abbrechen'), '', closeOverlay));
    var go = btn(T('Melden'), 'btn-danger ct-rep-send', function () {
      busy(go, true);
      api('POST', '/api/chat/report', { chat: c.id, msgs: msgs.map(function (m) { return m.id; }), reason: sel.value, text: cb.checked ? ta.value.trim() : '' })
        .then(function () { closeOverlay(); HK.toast(T('Danke – die Meldung ist bei den Admins.')); })
        .catch(function (e) { busy(go, false); HK.toast(e.message); });
    });
    bar.appendChild(go);
    sel.focus();
  }
  function grpMenu(menu, m) {
    var c = S.cur; if (!c || !m || m.kind !== 'msg') return;
    var mine = m.sender && m.sender.username === ME;
    if (c.can_pin && !m.fstate) {
      var pinned = (c.pins || []).indexOf(m.id) >= 0;
      menu.appendChild(HK.menuItem(pinned ? T('Lösen') : T('Anheften'), function () { HK.closeMenu(); pinMsg(c, m.id, !pinned); }, { ic: 'pin' }));
    }
    if (!mine && c.can_moderate) menu.appendChild(HK.menuItem(T('Löschen (Admin)'), function () {
      HK.closeMenu();
      HK.ask({ title: T('Nachricht für alle löschen?'), text: T('Als Admin löschst du die Nachricht von {name} für alle. Im Chat bleibt der Hinweis „Von einem Admin gelöscht“.', { name: (m.sender || {}).name || '?' }),
        ok: T('Löschen'), danger: true }).then(function (y) {
        if (y) api('DELETE', '/api/chats/' + c.id + '/messages/' + m.id).then(function () { tombLive({ id: m.id }); }).catch(function (e) { HK.toast(e.message); });
      });
    }, { ic: 'trash', danger: true }));
    if (!mine && m.sender) {
      var tg = blockTarget(c, m);
      if (tg && !grpBlocked(c, m) && c.kind !== 'dm') menu.appendChild(HK.menuItem(T('{name} sperren', { name: m.sender.name }), function () { HK.closeMenu(); blockPerson(c, tg, m.sender.name); }, { ic: 'ban' }));
      menu.appendChild(HK.menuItem(T('Melden …'), function () { HK.closeMenu(); reportDialog(c, [m]); }, { ic: 'flag' }));
    }
  }
  // Direkt-Chat: Sperren/Melden im Chat-Menue
  function grpChatItems(menu, c) {
    if (c.kind !== 'dm') return;
    var p = c.members.filter(function (m) { return !m.me; })[0] || {};
    var tg = p.handle || (c.peer || {}).handle || p.ref;      // Handle '~x@host' (so steht er in der Sperrliste)
    if (tg && !isRemote(c) && !p.blocked) menu.appendChild(HK.menuItem(T('{name} sperren', { name: p.name || '?' }), function () { HK.closeMenu(); blockPerson(c, tg, p.name || '?'); }, { ic: 'ban', danger: true }));
    if (p.blocked) menu.appendChild(HK.menuItem(T('Entsperren'), function () {
      HK.closeMenu();
      api('DELETE', '/api/chat/blocks?target=' + q(tg)).then(function () { HK.toast(T('Entsperrt.')); openChat(c.id); }).catch(function (e) { HK.toast(e.message); });
    }, { ic: 'check' }));
    var last = S.msgs.filter(function (m) { return m.kind === 'msg' && m.sender && m.sender.username !== ME && m.body; }).slice(-5);
    if (last.length) menu.appendChild(HK.menuItem(T('Melden …'), function () { HK.closeMenu(); reportDialog(c, last); }, { ic: 'flag' }));
  }

  // --- Gruppeninfo (2.5): Klick auf den Chat-Kopf -----------------------------------------------------------------
  // Name (bearbeitbar bei can_rename), Mitglieder mit Avatar, Anzeigename, Handle (nie Anmeldename), "Admin"/"Du";
  // bei can_manage Hinzufuegen und je Mitglied Admin vergeben/entziehen (can_admin) bzw. entfernen; unten Verlassen,
  // Stummschalten, Archivieren. group/page: nur lesend mit Hinweis. Live-Aenderungen (chat.changed) zeichnen neu.
  var GI = null;                                      // {id, draw} solange der Dialog offen ist
  function giHandle(m) {                              // Handle '~x@host' bzw. '~x' (ref, Chats v1) - nie der Anmeldename
    var h = m.handle || (/^~/.test(m.ref || '') ? m.ref : '');
    return h ? (h.charAt(0) === '@' ? h.slice(1) : h) : '';
  }
  function giAfter(c, r, msg) {
    if (msg) HK.toast(msg);
    return openChat(c.id).then(function () { loadList(); });
  }
  function giAdmin(c, m, on) {
    return api('PATCH', '/api/chats/' + c.id + '/members/' + q(m.username), { admin: on })
      .then(function (r) { return giAfter(c, r, on ? T('{name} ist jetzt Admin.', { name: m.me ? T('Du') : m.name }) : T('Admin-Rolle entzogen.')); })
      .catch(function (e) { HK.toast(e.message); });
  }
  function giRemove(c, m) {
    HK.ask({ title: T('{name} aus der Gruppe entfernen?', { name: m.name }), text: T('{name} bekommt keine neuen Nachrichten dieser Gruppe mehr.', { name: m.name }),
      ok: T('Entfernen'), danger: true }).then(function (y) {
      if (!y) return;
      api('DELETE', '/api/chats/' + c.id + '/members/' + q(m.username)).then(function (r) { giAfter(c, r, T('{name} wurde entfernt.', { name: m.name })); })
        .catch(function (e) { HK.toast(e.message); });
    });
  }
  function giRemovePage(c, m) {
    HK.ask({ title: T('{name} aus dem Seiten-Chat entfernen?', { name: m.name }), text: T('Die Freigabe der Seite für {name} wird entzogen.', { name: m.name }),
      ok: T('Entfernen'), danger: true }).then(function (y) {
      if (!y) return;
      api('DELETE', '/api/chats/' + c.id + '/members/' + q(m.username)).then(function (r) { giAfter(c, r, T('{name} wurde entfernt.', { name: m.name })); })
        .catch(function (e) { HK.toast(e.message); });
    });
  }
  function groupInfo() {
    var c0 = S.cur; if (!c0 || c0.kind === 'dm') { if (c0) members(); return; }
    var box = overlay(T('Gruppeninfo'));
    if (ov) { ov.classList.add('ct-gi-ov'); ov.querySelector('.ct-dlg').classList.add('ct-gi'); }
    var wrap = el('div', 'ct-gi-body'); box.appendChild(wrap);
    function draw() {
      var c = S.cur; if (!c || c.id !== c0.id) { closeOverlay(); return; }
      var ae = document.activeElement, foc = ae && wrap.contains(ae) ? (ae.dataset.gi || (ae.closest('.ct-gi-search') ? 'search' : null)) : null;
      wrap.textContent = '';
      // Kopf: Name (Stift) und Art
      var hd = el('div', 'ct-gi-head');
      grpHeadAvatar(hd, c);                              // 2.5 (groupinfo2): Gruppenbild
      var nm = el('div', 'ct-gi-name'), h3 = el('h3', 'ct-gi-title', chatName(c)); h3.id = 'ct-gi-title'; nm.appendChild(h3);
      nm.appendChild(el('span', 'hk-hint', c.kind === 'free' ? T('Freie Gruppe · {n} {n:Mitglied|Mitglieder}', { n: c.member_count })
        : c.kind === 'group' ? T('Gruppen-Chat „{g}“ · {n} {n:Mitglied|Mitglieder}', { g: c.group || '', n: c.member_count }) : T('Seiten-Chat · {n} {n:Mitglied|Mitglieder}', { n: c.member_count })));
      hd.appendChild(nm);
      if (c.kind === 'free' && c.can_rename) {
        var pen = el('button', 'icon-btn ct-gi-edit', '✎'); pen.type = 'button'; pen.dataset.gi = 'edit';
        pen.title = T('Umbenennen'); pen.setAttribute('aria-label', T('Gruppe umbenennen'));
        pen.addEventListener('click', function () {
          var inp = el('input', 'hk-in ct-gi-in'); inp.maxLength = 80; inp.value = c._title || ''; inp.placeholder = T('Name der Gruppe');
          inp.setAttribute('aria-label', T('Name der Gruppe'));
          var save = btn(T('Speichern'), 'btn-primary small ct-gi-save'), stop = btn(T('Abbrechen'), 'small');
          var row = el('div', 'ct-gi-rename'); row.appendChild(inp); row.appendChild(save); row.appendChild(stop);
          nm.replaceChild(row, h3); pen.hidden = true; inp.focus(); inp.select();
          function go() {
            var tt = inp.value.trim(); if (!tt || tt === c._title) { draw(); return; }
            busy(save, true);
            sendBody(c.id, { v: 1, type: 'title', title: tt }, 'title').then(function () { c._title = tt; drawHead(); loadList(); draw(); })
              .catch(function (e) { busy(save, false); HK.toast(e.message); });
          }
          save.addEventListener('click', go); stop.addEventListener('click', draw);
          inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); go(); } else if (e.key === 'Escape') { e.stopPropagation(); draw(); } });
        });
        hd.appendChild(pen);
      }
      wrap.appendChild(hd);
      if (c.kind === 'free' && c.can_rename) wrap.appendChild(el('p', 'hk-hint ct-gi-note', T('Der Name wird wie eine Nachricht verschlüsselt.')));
      grpInfoSection(wrap, c, draw);                     // 2.5: Beschreibung, Gruppenrechte
      if (c.kind === 'group') wrap.appendChild(el('p', 'hk-hint ct-gi-ro', T('Mitglieder ergeben sich aus der Benutzergruppe – dort verwalten.')));
      if (c.kind === 'page') wrap.appendChild(el('p', 'hk-hint ct-gi-ro', c.can_manage
        ? T('Personen hinzufügen teilt diese Seite mit ihnen. Entfernen entzieht die Freigabe.')
        : T('Mitglieder sind alle, die die Seite sehen dürfen – das legt fest, wem die Seite gehört.')));
      if (isRemote(c)) wrap.appendChild(el('p', 'hk-hint', c.home_self ? T('Dieser Chat liegt auf diesem Server; Personen anderer Saga-Server sind über ihren Server dabei.')
        : T('Dieser Chat liegt auf {host}. Mitglieder ändert man dort (über diesen Server).', { host: c.home || '?' })));
      if (c.readonly) wrap.appendChild(el('p', 'hk-hint', '🔒 ' + (c.readonly_reason || T('schreibgeschützt'))));
      // Mitglieder
      var h4 = el('h3', 'ct-gi-h', T('{n} {n:Mitglied|Mitglieder}', { n: c.member_count })); wrap.appendChild(h4);
      if ((c.kind === 'free' || c.kind === 'page') && c.can_manage) {
        var add = btn('+ ' + T('Mitglied hinzufügen'), 'small ct-gi-add'); add.dataset.gi = 'add';
        // Suchbereich bleibt beim Neuzeichnen (Live-Aenderung) samt Eingabe erhalten
        var slot = GI && GI.slot && GI.slot.cid === c.id ? GI.slot.el : null;
        if (!slot) { slot = el('div', 'ct-gi-search'); slot.hidden = true; if (GI) GI.slot = { cid: c.id, el: slot }; }
        add.addEventListener('click', function () {
          slot.hidden = !slot.hidden;
          if (!slot.hidden && !slot.firstChild) addSearch(slot, c);
          var i = slot.querySelector('input'); if (i && !slot.hidden) i.focus();
        });
        wrap.appendChild(add); wrap.appendChild(slot);
      } else if (GI) GI.slot = null;
      var ul = el('ul', 'ct-devs ct-gi-list'); wrap.appendChild(ul);
      var mine = String(c.my_handle || '').split('@').slice(1).join('@');
      c.members.slice().sort(function (a, b) { return (b.me - a.me) || ((b.admin ? 1 : 0) - (a.admin ? 1 : 0)) || String(a.name).localeCompare(String(b.name)); }).forEach(function (m) {
        var li = el('li', 'ct-dev ct-gi-m'); li.dataset.handle = giHandle(m);
        li.appendChild(avatar((m.name || '?').charAt(0).toUpperCase(), m.username, '', m.avatar));
        var t = el('div', 'ct-dev-t'), nl = el('div', 'ct-gi-nl');
        nl.appendChild(el('b', 'ct-gi-n', m.name || '?'));
        if (m.me) nl.appendChild(el('span', 'ct-chip ct-gi-me', T('Du')));
        if (m.admin) nl.appendChild(el('span', 'ct-chip ct-gi-adm', T('Admin')));
        t.appendChild(nl);
        // 2.4/2.5: nie den Anmeldenamen anderer - Handle (bei Personen anderer Server mit Server), sonst Unterzeile
        var hh = giHandle(m), sub = hh ? (hh.indexOf('@') > 0 && hh.split('@').slice(1).join('@') === mine ? hh.split('@')[0] : hh) : (m.sub || '');
        if (m.handle && m.host && m.host !== mine && isRemote(c)) sub = T('auf {host}', { host: m.host }) + (hh ? ' · ' + hh.split('@')[0] : '');
        if (sub) t.appendChild(el('span', 'hk-hint ct-gi-h2', sub));
        li.appendChild(t);
        var acts = [];
        if (!m.me) acts.push([T('Sicherheitsnummer'), function () { safety(m); }, 'i:lock']);
        if (c.kind === 'free' && c.can_admin && (!m.me || m.admin)) {
          if (!m.admin) acts.push([T('Zum Admin machen'), function () { giAdmin(c, m, true); }, 'i:star']);
          else acts.push([T('Admin entziehen'), function () { giAdmin(c, m, false); }, 'i:star']);
        }
        if (c.kind === 'free' && c.can_manage && !m.me) acts.push([T('Aus Gruppe entfernen'), function () { giRemove(c, m); }, 'i:x', true]);
        var why = null;                                // 2.5: Seiten-Chat - nur direkte Freigaben lassen sich entziehen
        if (c.kind === 'page' && c.can_manage && !m.me) {
          if (m.removable) acts.push([T('Aus Chat entfernen'), function () { giRemovePage(c, m); }, 'i:x', true]);
          else why = m.via === 'group' ? T('Zugriff über eine Gruppe – dort entfernen.') : m.via === 'parent' ? T('Zugriff über eine übergeordnete Seite – dort die Freigabe entziehen.')
            : m.via === 'owner' ? T('Ihr gehört die Seite.') : T('Zugriff nicht nur über diese Seite – im Teilen-Dialog prüfen.');
          if (why) acts.push([T('Aus Chat entfernen'), null, 'i:x', true, why]);
        }
        if (acts.length) {
          var mb = el('button', 'icon-btn ct-gi-more', '⋯'); mb.type = 'button'; mb.dataset.gi = 'm:' + giHandle(m);
          mb.setAttribute('aria-label', T('Aktionen für {name}', { name: m.name })); mb.setAttribute('aria-haspopup', 'menu');
          mb.addEventListener('click', function () {
            HK.openMenu(mb, function (menu) {
              menu.textContent = '';
              acts.forEach(function (a) {
                var it = HK.menuItem(a[0], a[1], { icon: a[2], danger: !!a[3] && !a[4], hint: a[4], hintBelow: !!a[4] });
                if (a[4]) { it.disabled = true; it.setAttribute('aria-disabled', 'true'); it.classList.add('ct-gi-off'); }
                menu.appendChild(it);
              });
            });
          });
          li.appendChild(mb);
        }
        ul.appendChild(li);
      });
      // unten: Verlassen, Stummschalten, Archivieren
      var bar = el('div', 'ct-gi-foot');
      var mbt = btn((c.muted ? '🔕 ' : '🔔 ') + muteLabel(c), 'small ct-gi-mute', function () {        // 2.5: Dauer und Modus
        HK.openMenu(mbt, function (menu) { menu.textContent = ''; muteSub(menu, c, function () { return giAfter(c); }); });
      });
      bar.appendChild(mbt);
      bar.appendChild(btn('🖼 ' + T('Medien, Dateien, Links'), 'small ct-gi-media', function () { mediaView(c); }));                      // 2.5
      bar.appendChild(btn(c.archived ? '⤴ ' + T('Aus Archiv holen') : '🗄 ' + T('Archivieren'), 'small', function () { archive(c, !c.archived).then(function () { if (GI) draw(); }); }));
      if (c.kind === 'free') bar.appendChild(btn('⎋ ' + T('Gruppe verlassen'), 'small btn-danger ct-gi-leave', function () { leaveGroup(c); }));
      wrap.appendChild(bar);
      if (foc) { var f = foc === 'search' ? wrap.querySelector('.ct-gi-search input') : wrap.querySelector('[data-gi="' + foc.replace(/"/g, '') + '"]'); if (f) f.focus(); }
    }
    GI = { id: c0.id, draw: draw };
    var prevClean = ovClean;
    ovClean = function () { GI = null; if (prevClean) prevClean(); };
    draw();
  }
  // Chat-Kopf anklicken (bzw. Enter/Leertaste) -> Gruppeninfo; Direkt-Chat: Mitglieder/Sicherheitsnummer wie bisher
  (function () {
    var ht = document.querySelector('#ct-chat .ct-head-t'); if (!ht) return;
    ht.setAttribute('role', 'button'); ht.tabIndex = 0; ht.classList.add('ct-head-click');
    // A11Y-30: Name = sichtbarer Chat-Name (vorher ueberschrieb "Infos zum Chat" ihn), der Zweck als Beschreibung
    var hint = el('span', null, T('Infos zum Chat')); hint.id = 'ct-head-hint'; hint.hidden = true; ht.parentNode.appendChild(hint);
    ht.setAttribute('aria-labelledby', 'ct-title'); ht.setAttribute('aria-describedby', 'ct-head-hint');
    function go(e) { if (e.target.closest('a, button')) return; if (S.cur) groupInfo(); }
    ht.addEventListener('click', go);
    ht.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(e); } });
  })();

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
    if (t === 'chat.link') {
      if (S.linkWait) linkPoll();
      else if (!d.approved && !d.gone) checkLinks();
      else if (ov && ov.dataset.link && ov.dataset.link === d.id) closeOverlay();   // auf einem anderen Geraet bestaetigt/abgelehnt
      return;
    }
    if (t === 'chat.device' && K && d.id === K.device.id) { forget().then(function () { closeChat(); newDevice(); }); return; }
    if (!S.ready) return;
    if (t === 'resync') { loadList(); if (S.cur) loadMsgs(false); return; }
    if (t === 'chat.message') {
      // Nr. 103: "… tippt" verschwindet, sobald die Nachricht da ist (nicht erst nach 4,5 s)
      // (Stufe 3 nennt Tippende per Kennung statt Benutzername: dann alle - wer weitertippt, meldet sich nach 3 s wieder)
      if (d.sender && d.sender !== ME && S.cur && S.cur.id === d.chat_id && Object.keys(S.typing).length) {
        if (S.typing[d.sender]) delete S.typing[d.sender]; else S.typing = {};
        drawTyping();
      }
      if (S.cur && S.cur.id === d.chat_id) {
        var known = S.byId[d.id];                       // (2.3) ausstehende Nachricht bestaetigt bzw. abgewiesen
        if (known && d.fseq && !known.fseq) known.fseq = d.fseq;      // (2.4) Position des Heims fuer die Gelesen-Haken
        if (known && known.fstate) { known.fstate = d.failed ? 'failed' : null; drawMsgs(); }
        loadMsgs(false);
      }
      listSoon();
    }
    else if (t === 'chat.react' && S.cur && S.cur.id === d.chat_id) rxLive(S.cur, d);       // 2.5: Reaktionen (gebuendelt)
    else if (t === 'chat.edited' && S.cur && S.cur.id === d.chat_id) edLive(S.cur, d);      // 2.5: Nachricht bearbeitet
    else if (t === 'chat.request') listSoon();                     // (2.3) neue Nachrichtenanfrage
    else if (t === 'chat.keys.changed') {                           // (2.3) Schluessel einer Person (bzw. die eigenen) geaendert
      if (d.own) fedCerts().catch(function () {});
      else if (S.cur && isFed(S.cur) && S.cur.members.some(function (m) { return m.handle === d.handle; })) fedLoad(S.cur, true).catch(function () {});
    }
    else if (t === 'chat.deleted') { if (S.cur && S.cur.id === d.chat_id) { if (d.by_admin) tombLive(d); else dropMsg(d.id); } listSoon(); }
    else if (t === 'chat.blocks') { S.blocks = null; listSoon(); if (S.cur) openChat(S.cur.id); }       // 2.5: gesperrt/entsperrt
    else if (t === 'chat.hidden') { if (S.cur && S.cur.id === d.chat_id && Array.isArray(d.ids)) dropMany(d.ids.filter(function (i) { return S.byId[i]; })); listSoon(); }   // 2.5
    else if (t === 'chat.removed') removedLive(d, true);           // (2.1) Chat fuer alle geloescht
    else if (t === 'chat.cleared') {                                // (2.1) Verlauf geleert: fuer mich (anderer Tab) oder fuer alle
      if (d.scope === 'all') removedLive(d, false);
      else { if (S.cur && S.cur.id === d.chat_id) { if (S.cur.kind === 'dm') { closeChat(); history.replaceState(null, '', location.pathname); } else wipeView(); } listSoon(); }
    }
    else if (t === 'chat.read') listSoon();
    else if (t === 'chat.changed') { listSoon(); if (S.cur && S.cur.id === d.chat_id) { S.rc = null; openChat(d.chat_id); } }
    else if (t === 'avatar.changed') listSoon();      // neues Profilbild (Bilder tauscht live.js; Liste fuer neue Adressen)
    else if (t === 'ai.persona.changed') loadAI();     // Name/Bild/Schalter der KI geaendert
    else if (t === 'chat.receipt' && S.cur && S.cur.id === d.chat_id && K) {          // (2.4) viele auf einmal: nur Hinweis -> abrufen
      if (d.more) loadReceipts(S.cur); else if (takeReceipts(S.cur, d.receipts)) drawMsgs();
    }
    else if (t === 'chat.presence') {
      if (d.online || d.last) S.pres[d.username] = { online: !!d.online, last: d.last || null }; else delete S.pres[d.username];
      drawList(); drawHead();
    }
    else if (t === 'chat.privacy') { if (S.me) S.me.privacy = { receipts: !!d.receipts, online: !!d.online }; S.rcpts = {}; presenceSync(); if (S.cur) { loadReceipts(S.cur); drawMsgs(); } }
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
               // Name eines Chats aus der Liste (Kopf des Anruffensters, wenn ein anderer Chat offen ist)
               chatName: function (id) { var c = (S.chats || []).filter(function (x) { return x.id === id; })[0]; return c ? chatName(c) : ''; },
               peerKeysV2: callPeerV2, myHandle: function () { return S.myHandle || (S.cur && S.cur.my_handle) || null; },
               // Anruf vorbei: offenen Chat auffrischen - ist keiner offen (angenommen von einer anderen Seite), den des Anrufs oeffnen
               refresh: function (cid) { if (S.cur && S.cur.id === cid) loadMsgs(false); else if (!S.cur && S.ready && cid) openChat(cid); } } };
  start();
})();
