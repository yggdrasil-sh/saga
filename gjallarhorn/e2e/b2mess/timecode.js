// Zeitcode im Bild: 2 Zeilen x 12 Bloecke (24 Bit, ms mod 2^24) schwarz/weiss am oberen Rand, Lesen per Canvas.
'use strict';
const TC = {
  bits: 24, rows: 2, cols: 12,
  draw(g, w, h, ms) {
    const bw = w / this.cols, bh = Math.round(h / 9);
    const v = Math.floor(ms) & 0xffffff;
    for (let i = 0; i < this.bits; i++) {
      g.fillStyle = (v >> i) & 1 ? '#fff' : '#000';
      g.fillRect((i % this.cols) * bw, ((i / this.cols) | 0) * bh, bw, bh);
    }
  },
  _cv: null,
  read(src, w, h) {   // src: video oder VideoFrame
    const cv = this._cv || (this._cv = new OffscreenCanvas(96, 40));
    const g = cv.getContext('2d', { willReadFrequently: true });
    g.drawImage(src, 0, 0, 96, Math.round(96 * h / w));
    const ch = Math.round(96 * h / w), bh = ch / 9, bw = 96 / this.cols;
    const d = g.getImageData(0, 0, 96, Math.ceil(bh * 2)).data;
    let v = 0;
    for (let i = 0; i < this.bits; i++) {
      const x = Math.floor((i % this.cols) * bw + bw / 2), y = Math.floor(((i / this.cols) | 0) * bh + bh / 2);
      const o = (y * 96 + x) * 4;
      if (d[o] + d[o + 1] + d[o + 2] > 384) v |= 1 << i;
    }
    return v;
  },
  // Abstand jetzt - Zeitcode (mod 2^24)
  lag(code, ms) { let d = (Math.floor(ms) & 0xffffff) - code; if (d < 0) d += 0x1000000; return d; },
};
