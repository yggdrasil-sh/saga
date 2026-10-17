// Saga - Chat-Bilder im Hintergrund (2.1): Anhaenge entschluesseln und Vorschaubilder erzeugen, ohne den UI-Thread
// zu blockieren. Gestartet von static/chat.js (new Worker('/static/chat-worker.js?v=..')). Gleiche Kryptografie wie
// im Fenster (static/chat-crypto.js ueber libsodium.js); Schluessel kommen je Auftrag mit und werden nicht gemerkt.
// Auftraege: {n, op, ...} -> Antwort {n, ok, ...} bzw. {n, ok:false, err}
//   dec   {cid, blob, key}                -> {plain}                    Anhang entschluesseln
//   thumb {cid, blob, key, mime, enc}     -> {jpeg, w, h, tw, th, ct?}  entschluesseln + Vorschaubild (ct: mit key neu
//                                                                       verschluesselt, fuer den Platten-Cache)
//   make  {bytes, mime}                   -> {jpeg, w, h, tw, th}       Vorschaubild zum Senden
//   enc   {cid, bytes}                    -> {blob, key}                Anhang verschluesseln (Senden)
'use strict';
var V = (self.location.search.match(/v=([^&]+)/) || [])[1] || '';
importScripts('/static/vendor/libsodium/libsodium-sumo.js?v=' + V, '/static/vendor/libsodium/libsodium-wrappers.js?v=' + V,
  '/static/chat-crypto.js?v=' + V);
var X = null, ready = self.sodium.ready.then(function () { X = self.SagaChatCrypto(self.sodium); });
var MAX = 320, Q = 0.7;

function thumbOf(bytes, mime) {
  if (typeof OffscreenCanvas === 'undefined' || typeof createImageBitmap === 'undefined') return Promise.reject(new Error('no-canvas'));
  return createImageBitmap(new Blob([bytes], { type: mime || 'image/jpeg' }), { imageOrientation: 'from-image' }).then(function (bm) {
    var w = bm.width, h = bm.height, f = Math.min(1, MAX / Math.max(w, h, 1));
    var tw = Math.max(1, Math.round(w * f)), th = Math.max(1, Math.round(h * f));
    var cv = new OffscreenCanvas(tw, th), g = cv.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, tw, th);          // Transparenz auf Weiss (JPEG)
    g.imageSmoothingQuality = 'high';
    g.drawImage(bm, 0, 0, tw, th); bm.close();
    return cv.convertToBlob({ type: 'image/jpeg', quality: Q }).then(function (b) { return b.arrayBuffer(); })
      .then(function (jpeg) { return { jpeg: jpeg, w: w, h: h, tw: tw, th: th }; });
  });
}

self.onmessage = function (e) {
  var d = e.data || {}, n = d.n;
  function done(r, tr) { r.n = n; r.ok = true; self.postMessage(r, tr || []); }
  ready.then(function () {
    if (d.op === 'dec') {
      var p = X.decryptFile(d.cid, new Uint8Array(d.blob), d.key);
      return done({ plain: p.buffer }, [p.buffer]);
    }
    if (d.op === 'thumb') {
      var plain = X.decryptFile(d.cid, new Uint8Array(d.blob), d.key);
      return thumbOf(plain, d.mime).then(function (t) {
        if (d.enc) t.ct = X.encryptFile(d.cid, new Uint8Array(t.jpeg), { key: X.unb64(d.key, 32) }).blob.buffer;
        done(t, [t.jpeg].concat(t.ct ? [t.ct] : []));
      });
    }
    if (d.op === 'make') return thumbOf(new Uint8Array(d.bytes), d.mime).then(function (t) { done(t, [t.jpeg]); });
    if (d.op === 'enc') {
      var r = X.encryptFile(d.cid, new Uint8Array(d.bytes));
      return done({ blob: r.blob.buffer, key: r.key }, [r.blob.buffer]);
    }
    throw new Error('op');
  }).catch(function (err) { self.postMessage({ n: n, ok: false, err: String(err && err.message || err) }); });
};
