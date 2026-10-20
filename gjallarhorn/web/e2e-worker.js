// Dummy-Frame-E2E fuer die Horn-Probe: haengt je Rahmen einen Trailer gleicher
// Groesse wie im echten Verfahren an (IV 12 Byte + kid 1 Byte) und entfernt ihn
// beim Empfang. Es wird nichts verschluesselt, aber die Bytes stimmen.
'use strict';
const TRAILER = 13;

function makeTransform(op) {
  let n = 0;
  return new TransformStream({
    transform(frame, ctl) {
      const src = new Uint8Array(frame.data);
      if (op === 'send') {
        const d = new Uint8Array(src.length + TRAILER);
        d.set(src, 0);
        n = (n + 1) >>> 0;
        new DataView(d.buffer).setUint32(src.length + 8, n); // Rahmenzaehler im IV
        d[d.length - 1] = 0; // kid
        frame.data = d.buffer;
      } else if (src.length > TRAILER) {
        frame.data = src.slice(0, src.length - TRAILER).buffer;
      }
      ctl.enqueue(frame);
    },
  });
}

function pipe(op, readable, writable) {
  readable.pipeThrough(makeTransform(op)).pipeTo(writable).catch(() => {});
}

// RTCRtpScriptTransform (Chrome, Firefox, Safari)
self.onrtctransform = (ev) => {
  const t = ev.transformer;
  pipe(t.options.op, t.readable, t.writable);
};

// createEncodedStreams (aeltere Chromium)
self.onmessage = (ev) => {
  const { op, readable, writable } = ev.data;
  if (readable && writable) pipe(op, readable, writable);
};
