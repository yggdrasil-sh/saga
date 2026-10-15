/* Passkeys (WebAuthn) im Browser: Hilfen fuer Anmeldeseite, Zwischenschritt nach dem Passwort und Einstellungen
   (window.SagaPasskey). Die Pruefung macht der Server (saga/mfa.py mit py_webauthn) - hier nur JSON <-> ArrayBuffer
   und navigator.credentials. Laeuft ohne common.js (Anmeldeseiten), Texte kommen aus data-t-* der Seite. */
(function () {
  'use strict';
  function b64u(buf) {
    var b = new Uint8Array(buf), s = '';
    for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function unb64u(s) {
    s = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var r = atob(s), b = new Uint8Array(r.length);
    for (var i = 0; i < r.length; i++) b[i] = r.charCodeAt(i);
    return b.buffer;
  }
  function creds(list) { return (list || []).map(function (c) { return Object.assign({}, c, { id: unb64u(c.id) }); }); }
  function getOpts(o) {
    var p = Object.assign({}, o); p.challenge = unb64u(o.challenge);
    if (o.allowCredentials) p.allowCredentials = creds(o.allowCredentials);
    return p;
  }
  function createOpts(o) {
    var p = Object.assign({}, o); p.challenge = unb64u(o.challenge);
    p.user = Object.assign({}, o.user, { id: unb64u(o.user.id) });
    if (o.excludeCredentials) p.excludeCredentials = creds(o.excludeCredentials);
    return p;
  }
  function toJSON(c) {
    var r = c.response, out = { id: c.id, rawId: b64u(c.rawId), type: c.type, authenticatorAttachment: c.authenticatorAttachment || null,
      clientExtensionResults: c.getClientExtensionResults ? c.getClientExtensionResults() : {}, response: { clientDataJSON: b64u(r.clientDataJSON) } };
    if (r.attestationObject) {
      out.response.attestationObject = b64u(r.attestationObject);
      if (r.getTransports) out.response.transports = r.getTransports();
    } else {
      out.response.authenticatorData = b64u(r.authenticatorData);
      out.response.signature = b64u(r.signature);
      out.response.userHandle = r.userHandle ? b64u(r.userHandle) : null;
    }
    return out;
  }
  // Eingebettete App-Ansicht (WKWebView der iOS/macOS-App, Android-WebView): WebAuthn geht dort nur mit Associated
  // Domains fuer genau diesen Server - bei selbst gehosteten Servern also nicht. Die Anmeldung der Apps laeuft ohnehin im
  // System-Browserfenster (ASWebAuthenticationSession/Custom Tab), dort gehen Passkeys.
  function inApp() { return /(Saga|Hlidskjalf)App\//.test(navigator.userAgent) || /; wv\)/.test(navigator.userAgent); }
  function supported() { return !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.get) && !inApp(); }
  // Seite ueber eine andere Adresse geoeffnet als BASE_URL: Passkeys wuerden still scheitern -> klare Meldung
  function originProblem(expected, text) {
    if (!expected || location.origin === expected) return '';
    return String(text || '').replace(/\{here\}/g, location.origin).replace(/\{base\}/g, expected);
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
                        body: JSON.stringify(body || {}) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (d) {
          if (!r.ok) { var e = new Error(d.error || ('HTTP ' + r.status)); e.status = r.status; e.data = d; throw e; }
          return d;
        });
      });
  }
  function get(options, extra) { return navigator.credentials.get(Object.assign({ publicKey: getOpts(options) }, extra || {})).then(toJSON); }
  function create(options) { return navigator.credentials.create({ publicKey: createOpts(options) }).then(toJSON); }
  // Passkey ist beim Server unbekannt (geloescht): dem Passwort-Manager Bescheid geben, wo der Browser das kann
  function forget(rpId, credId) {
    try { if (PublicKeyCredential.signalUnknownCredential) PublicKeyCredential.signalUnknownCredential({ rpId: rpId, credentialId: credId }).catch(function () {}); } catch (e) { /* aelterer Browser */ }
  }
  function cancelled(e) { return e && (e.name === 'NotAllowedError' || e.name === 'AbortError'); }
  window.SagaPasskey = { supported: supported, inApp: inApp, get: get, create: create, post: post, originProblem: originProblem, forget: forget,
                         cancelled: cancelled, b64u: b64u, unb64u: unb64u };

  // --- Anmeldeseite (data-mode=login: ohne Passwort) und Zwischenschritt (data-mode=2fa) ---------------------------
  var box = document.getElementById('pk-box');
  if (!box) return;
  var mode = box.dataset.mode, base = box.dataset.base || '', go = document.getElementById('pk-go');
  var err = box.querySelector('.pk-err'), st = box.querySelector('.pk-status'), ctl = null;
  function showErr(m) { err.textContent = m || ''; err.hidden = !m; }
  function busy(on) { go.disabled = on; st.hidden = !on; st.textContent = on ? box.dataset.tBusy : ''; }
  if (!supported()) {
    if (mode === '2fa') { box.hidden = false; go.disabled = true; showErr(box.dataset.tNosupport); }
    return;
  }
  box.hidden = false;
  var prob = originProblem(box.dataset.origin, box.dataset.tOrigin);
  if (prob) { go.disabled = true; showErr(prob); return; }
  function start(conditional) {
    return post(base + '/login/passkey/options', { mode: mode }).then(function (d) {
      var extra = {};
      if (conditional) { ctl = new AbortController(); extra = { mediation: 'conditional', signal: ctl.signal }; }
      return get(d.options, extra).then(function (cred) {
        busy(true);
        return post(base + '/login/passkey', { credential: cred }).then(function (r) { location.href = r.next || '/'; })
          .catch(function (e) { if (e.data && e.data.unknown) forget(d.options.rpId, cred.id); throw e; });
      });
    });
  }
  function fail(e) { busy(false); showErr(cancelled(e) ? box.dataset.tFail : (e && e.message) || box.dataset.tFail); }
  go.addEventListener('click', function () {
    if (ctl) { ctl.abort(); ctl = null; }
    showErr(''); busy(true);
    start(false).catch(fail);
  });
  if (mode === '2fa') go.focus();
  // Autofill (conditional mediation): gespeicherte Passkeys erscheinen in der Vorschlagsliste beim Benutzernamen
  if (mode === 'login' && PublicKeyCredential.isConditionalMediationAvailable) {
    PublicKeyCredential.isConditionalMediationAvailable().then(function (ok) {
      if (ok) start(true).catch(function (e) { if (!cancelled(e)) fail(e); });
    }).catch(function () {});
  }
})();
