// Saga - Formatierung von Chat-Nachrichten (2.5, Chat-Alltag C2, chatfmt1): Markdown-light NUR fuer die Darstellung.
// *fett*, _kursiv_, ~durch~, `code`, ```block```, Zeilen "- " = Liste, "> " = Zitat. Der Text bleibt Text (E2E unveraendert,
// aeltere Clients zeigen die Zeichen). Sicher: ausschliesslich createElement/textContent - nie innerHTML, nie Attribute aus
// dem Text. leaf(box, text) zeichnet die Textstuecke (Links, Seitenverweise, Erwaehnungen - static/chat.js).
// window.SagaChatFmt = { render(box, text, leaf), strip(text) }
(function () {
  'use strict';
  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  var MARK = { '*': 'strong', '_': 'em', '~': 's' };
  // Marke nur am Wortrand: davor Anfang/Leerraum/Klammer, danach Ende/Leerraum/Satzzeichen; Inhalt ohne Zeilenumbruch,
  // beginnt und endet nicht mit Leerraum (so bleiben a_b_c, 2*3*4 und Adressen mit ~ unberuehrt)
  var INL = /(^|[\s(\[{"'])([*_~])([^\s*_~\n](?:[^\n]*?[^\s\n])?)\2(?=$|[\s)\]}"'.,;:!?])/;
  var CODE = /`([^`\n]+)`/;

  function inline(box, s, leaf, depth) {
    if (!s) return;
    var mc = CODE.exec(s);
    if (mc) {
      inline(box, s.slice(0, mc.index), leaf, depth);
      var c = el('code', 'ct-f-code'); c.textContent = mc[1]; box.appendChild(c);
      inline(box, s.slice(mc.index + mc[0].length), leaf, depth);
      return;
    }
    var m = depth < 4 ? INL.exec(s) : null;
    if (!m) { leaf(box, s); return; }
    var start = m.index + m[1].length;
    if (start > 0) leaf(box, s.slice(0, start));
    var w = el(MARK[m[2]], 'ct-f-' + MARK[m[2]]);
    inline(w, m[3], leaf, depth + 1);
    box.appendChild(w);
    inline(box, s.slice(m.index + m[0].length), leaf, depth);
  }
  function lines(box, text, leaf) {
    var ls = text.split('\n'), i = 0, first = true;
    function nl() { if (!first) box.appendChild(document.createTextNode('\n')); first = false; }
    while (i < ls.length) {
      var l = ls[i];
      if (/^> ?/.test(l) && /^>/.test(l)) {
        var q = el('blockquote', 'ct-f-quote'), qs = [];
        while (i < ls.length && /^>/.test(ls[i])) { qs.push(ls[i].replace(/^> ?/, '')); i++; }
        lines(q, qs.join('\n'), leaf);
        if (!first && box.lastChild && box.lastChild.nodeType === 3) box.lastChild.textContent = box.lastChild.textContent.replace(/\n$/, '');
        box.appendChild(q); first = true; continue;
      }
      if (/^[-•] /.test(l)) {
        var ul = el('ul', 'ct-f-list');
        while (i < ls.length && /^[-•] /.test(ls[i])) { var li = el('li'); inline(li, ls[i].slice(2), leaf, 0); ul.appendChild(li); i++; }
        box.appendChild(ul); first = true; continue;
      }
      nl(); inline(box, l, leaf, 0); i++;
    }
  }
  function render(box, text, leaf) {
    var s = String(text || ''), re = /```(?:[a-zA-Z0-9_+-]{0,20}\n)?([\s\S]*?)```/g, last = 0, m;
    leaf = leaf || function (b, t) { b.appendChild(document.createTextNode(t)); };
    while ((m = re.exec(s))) {
      var before = s.slice(last, m.index).replace(/\n$/, '');
      if (before) lines(box, before, leaf);
      var pre = el('pre', 'ct-f-pre'), code = el('code'); code.textContent = m[1].replace(/\n$/, ''); pre.appendChild(code); box.appendChild(pre);
      last = re.lastIndex; if (s.charAt(last) === '\n') last++;
    }
    if (last < s.length) lines(box, s.slice(last), leaf);
  }
  // Fuer Vorschauen (Liste, Zitat): Marken entfernen
  function strip(text) {
    return String(text || '').replace(/```(?:[a-zA-Z0-9_+-]{0,20}\n)?/g, '').replace(/`([^`\n]+)`/g, '$1')
      .replace(new RegExp(INL.source, 'g'), function (_, a, mk, inner) { return a + inner; }).replace(/^(> ?|[-•] )/gm, '');
  }
  window.SagaChatFmt = { render: render, strip: strip };
})();
