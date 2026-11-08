// Saga - Linien-Symbole fuer Bedienelemente (Design-Runde VD-17/18/19, AE-07, VD-44)
// Selbst gezeichnet im 24er-Raster (Lucide-Stil: fill=none, stroke=currentColor, Strich 1.75, runde Enden/Ecken),
// keine externe Bibliothek und kein CDN (CSP, offline). Emoji bleiben nur Inhalt (Seiten-Icons, Callouts, Reaktionen).
// HK.icon('trash', 16) -> <svg> (per createElementNS, ohne innerHTML) oder null bei unbekanntem Namen.
// Kurzform je Element: Pfad ("M..."), "c cx cy r" (Kreis), "r x y b h rx" (Rechteck).
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var LIST3 = 'M9 6h11M9 12h11M9 18h11|c 4.5 6 .9|c 4.5 12 .9|c 4.5 18 .9';
  var FILE = 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z|M14 3v5h5';
  var BUBBLE = 'M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z';
  var I = {
    // allgemein
    plus: 'M12 5v14M5 12h14',
    x: 'M6.5 6.5l11 11M17.5 6.5l-11 11',
    check: 'M5 12.5l4.5 4.5L19 7',
    minus: 'M5 12h14',
    'chevron-right': 'M9.5 6l6 6-6 6',
    'chevron-left': 'M14.5 6l-6 6 6 6',
    'arrow-up': 'M12 19V5M6 11l6-6 6 6',
    'arrow-down': 'M12 5v14M6 13l6 6 6-6',
    external: 'M14 4h6v6M20 4l-9 9|M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4',
    search: 'c 11 11 7|m20 20-3.5-3.5',
    trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
    archive: 'r 3 4 18 4 1|M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8M10 12h4',
    book: 'M5 5.5A2.5 2.5 0 0 1 7.5 3H19v14H7.5A2.5 2.5 0 0 0 5 19.5z|M5 19.5A2.5 2.5 0 0 0 7.5 22H19v-5|M9 7h6',
    star: 'M12 3.6l2.6 5.2 5.8.9-4.2 4.1 1 5.7L12 16.8l-5.2 2.7 1-5.7-4.2-4.1 5.8-.9z',
    home: 'M3.5 11L12 4l8.5 7|M6 9.5V20h4.5v-5h3v5H18V9.5',
    width: 'M3 12h18M7 8l-4 4 4 4M17 8l4 4-4 4',
    type: 'M5 7.5V5h14v2.5M12 5v14M9 19h6',
    'type-small': 'M8 10V8.5h8V10M12 8.5V17M10 17h4',
    image: 'r 3 4 18 16 2|c 9 9.5 1.8|M21 15.5l-5-5L5 20',
    lock: 'r 5 11 14 10 2|M8 11V7.5a4 4 0 0 1 8 0V11',
    unlock: 'r 5 11 14 10 2|M8 11V7.5a4 4 0 0 1 7.8-1.3',
    copy: 'r 8 8 12 12 2|M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2',
    move: 'M4 19v-6a4 4 0 0 1 4-4h12M15 4l5 5-5 5',
    template: 'r 3 3 18 18 2|M3 9h18M9 9v12',
    graph: 'c 6 6 2.4|c 18 8 2.4|c 10 18 2.4|M8.4 6.4l7.2 1.2M6.8 8.3l2.4 7.4M16.6 10l-5 6',
    history: 'M3.5 12a8.5 8.5 0 1 0 2.4-5.9M3.5 4v4.5H8|M12 7.5V12l3 2',
    users: 'c 9 8 3.5|M2.5 20a6.5 6.5 0 0 1 13 0|M16 4.6a3.5 3.5 0 0 1 0 6.8M18 14.3a6.5 6.5 0 0 1 3.5 5.7',
    user: 'c 12 8 4|M4 21a8 8 0 0 1 16 0',
    send: 'M21 3L10 14|M21 3l-7 18-4-7-7-4z',
    comment: BUBBLE,
    message: BUBBLE + '|M8.5 12h.01M12 12h.01M15.5 12h.01',
    sparkles: 'M11 3.5l1.7 4.8 4.8 1.7-4.8 1.7L11 16.5l-1.7-4.8L4.5 10l4.8-1.7z|M18.5 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
    globe: 'c 12 12 9|M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18',
    sun: 'c 12 12 4|M12 3v1.5M12 19.5V21M5.6 5.6l1.1 1.1M17.3 17.3l1.1 1.1M3 12h1.5M19.5 12H21M5.6 18.4l1.1-1.1M17.3 6.7l1.1-1.1',   // Hell/Dunkel
    moon: 'M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z',
    monitor: 'r 3 4 18 12 2|M8 20h8M12 16v4',
    download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
    upload: 'M12 15V4M7 9l5-5 5 5M5 20h14',
    link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7|M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
    file: FILE,
    'file-zip': FILE + '|M10.5 7h1M10.5 10h1M10.5 13h1|r 9.5 15.5 3 3 .6',
    database: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z|M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6|M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
    settings: 'M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1|c 15 6 2|c 9 12 2|c 17 18 2',
    shield: 'M12 3l7.5 3v5.5c0 4.5-3.2 8-7.5 9.5-4.3-1.5-7.5-5-7.5-9.5V6z',
    smartphone: 'r 7 2.5 10 19 2|M11 18.5h2',
    logout: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3|M10 8l-4 4 4 4M6 12h10',
    bell: 'M6 9a6 6 0 0 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9|M10 20a2.2 2.2 0 0 0 4 0',
    clock: 'c 12 12 9|M12 7v5l3 2',
    edit: 'M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z|M13.5 6.5l4 4',
    keyboard: 'r 2.5 6 19 12 2|M6.5 10h.01M10 10h.01M14 10h.01M17.5 10h.01M8 14h8',
    help: 'c 12 12 9|M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6|M12 17h.01',
    // Rechnen / Zahlen
    sigma: 'M17 5H7l6 7-6 7h10',
    function: 'M15.5 4.5a3 3 0 0 0-3.4 2.4l-2.2 10.2a3 3 0 0 1-3.4 2.4|M8.5 10h7',
    hash: 'M5 9h15M4 15h15M10 3.5L8 20.5M16 3.5l-2 17',
    percent: 'M19 5L5 19|c 7 7 2.2|c 17 17 2.2',
    euro: 'M17 6.5a6.5 6.5 0 1 0 0 11|M4 10h9M4 14h9',
    pie: 'c 12 12 9|M12 3v9h9',
    progress: 'r 3 9 18 6 3|M6.5 12h6',
    // Eigenschaftstypen der Datenbank
    text: 'M4 6h16M4 12h16M4 18h10',
    select: 'c 12 12 9|M8.5 10.5l3.5 3.5 3.5-3.5',
    tag: 'M3.5 5.2v5.3a2 2 0 0 0 .6 1.4l7.5 7.5a2 2 0 0 0 2.8 0l5-5a2 2 0 0 0 0-2.8L11.9 4.1a2 2 0 0 0-1.4-.6H5.2a1.7 1.7 0 0 0-1.7 1.7z|c 8 8 1.3',
    status: 'c 12 12 9|M12 7a5 5 0 0 1 0 10z',
    circle: 'c 12 12 8',
    calendar: 'r 3 5 18 16 2.5|M3 10h18M8 3v4M16 3v4',
    checkbox: 'r 4 4 16 16 3|M8.5 12l2.5 2.5 5-5',
    paperclip: 'M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8',
    mail: 'r 3 5 18 14 2|M3.5 6.5L12 13l8.5-6.5',
    relation: 'M7 17L17 7M8 7h9v9',
    rollup: 'M14 4h6v6M20 4l-8 8|M4 9h5M4 14h8M4 19h16',
    swap: 'M4 8h15M15 4l4 4-4 4|M20 16H5M9 12l-4 4 4 4',
    range: 'M4 6v12M20 6v12|M8 12h8M10.5 9.5L8 12l2.5 2.5M13.5 9.5L16 12l-2.5 2.5',
    // Ansichten
    table: 'r 3 4 18 16 2|M3 10h18M3 15h18M9 10v10',
    board: 'r 3 4 5 14 1.5|r 9.5 4 5 9 1.5|r 16 4 5 11 1.5',
    gallery: 'r 3 3 7.5 7.5 1.5|r 13.5 3 7.5 7.5 1.5|r 3 13.5 7.5 7.5 1.5|r 13.5 13.5 7.5 7.5 1.5',
    list: LIST3,
    timeline: 'M3 4v16|M7 7h8M10 12h10M7 17h6',
    chart: 'M4 20h16|M7 16v-4M12 16V7M17 16v-6',
    // Bloecke / Editor
    repeat: 'M4 11V9a4 4 0 0 1 4-4h12M17 2l3 3-3 3|M20 13v2a4 4 0 0 1-4 4H4M7 22l-3-3 3-3',
    refresh: 'M20 11a8 8 0 0 0-14.6-4.5L4 8M4 4v4h4|M4 13a8 8 0 0 0 14.6 4.5L20 16M20 20v-4h-4',
    columns: 'r 3 4 18 16 2|M12 4v16',
    rows: 'r 3 4 18 16 2|M3 12h18',
    'cols-even': 'M3 12h7M14 12h7M6 9l-3 3 3 3M18 9l3 3-3 3|M12 5v14',
    merge: 'M8 4v5l4 4 4-4V4|M12 13v7',
    'list-ordered': 'M10 6h10M10 12h10M10 18h10|M4 4.5h1.5v4M4 8.5h3|M4 14.5c0-.8.7-1.3 1.5-1.3s1.5.5 1.5 1.3c0 1.1-3 2.3-3 3.5h3',
    'align-center': 'M4 6h16M7 12h10M5 18h14',
    pilcrow: 'M13 4v16M17 4v16|M19 4H9.5a4.5 4.5 0 0 0 0 9H13',
    quote: 'M9 7c-2.2 1-3.5 3-3.5 6v4h4v-4h-3.5|M18 7c-2.2 1-3.5 3-3.5 6v4h4v-4h-3.5',
    bulb: 'M9 18h6M10 21h4|M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z',
    at: 'c 12 12 3.5|M15.5 12v1.5a2.5 2.5 0 0 0 5 0V12a8.5 8.5 0 1 0-3.4 6.8',
    code: 'M8 7l-5 5 5 5M16 7l5 5-5 5',
    maximize: 'M15 4h5v5M9 20H4v-5M20 4l-6 6M4 20l6-6',
    droplet: 'M12 3.5s6 6.2 6 10.5a6 6 0 0 1-12 0c0-4.3 6-10.5 6-10.5z',
    'arrow-left': 'M19 12H5M11 6l-6 6 6 6',
    'arrow-right': 'M5 12h14M13 6l6 6-6 6',
    'row-above': 'M12 3.5v6M9 6.5h6|r 3 13 18 7 1.5',
    'row-below': 'r 3 4 18 7 1.5|M12 14.5v6M9 17.5h6',
    'col-left': 'M3.5 12h6M6.5 9v6|r 13 3 7 18 1.5',
    'col-right': 'r 4 3 7 18 1.5|M14.5 12h6M17.5 9v6',
    'table-head': 'r 3 4 18 16 2|M3 9.5h18',
    'table-headcol': 'r 3 4 18 16 2|M9.5 4v16',
    // Chat
    reply: 'M9 14L4 9l5-5|M4 9h10a6 6 0 0 1 6 6v4',
    pin: 'M12 16.5V21|M9 3.5h6l-1 5 3 3v2H7v-2l3-3z',
    ban: 'c 12 12 9|M5.6 5.6l12.8 12.8',
    flag: 'M5 21V4|M5 4h12l-2 4 2 4H5',
    camera: 'r 3 6.5 18 13.5 2|c 12 13 3.5|M8.5 6.5L10 4h4l1.5 2.5',
    dot: 'c 12 12 3.5',
    'bell-off': 'M6 9a6 6 0 0 1 12 0c0 6 2.5 7.5 2.5 7.5h-17S6 15 6 9|M10 20a2.2 2.2 0 0 0 4 0|M3.5 3.5l17 17',
    eye: 'M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7S2.5 12 2.5 12z|c 12 12 3',
    'eye-off': 'M2.5 12S6 5 12 5s9.5 7 9.5 7-3.5 7-9.5 7S2.5 12 2.5 12z|c 12 12 3|M3.5 3.5l17 17',
    'archive-out': 'r 3 4 18 4 1|M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8|M12 17v-6M9.5 13.5L12 11l2.5 2.5',
    timer: 'c 12 13.5 7.5|M12 9.5v4l2.5 1.5|M10 2.5h4'
  };
  function make(tag, attrs) { var n = document.createElementNS(NS, tag); for (var k in attrs) n.setAttribute(k, attrs[k]); return n; }
  function icon(name, size) {
    var def = Object.prototype.hasOwnProperty.call(I, name) ? I[name] : null;
    if (!def) return null;
    size = size || 16;
    var s = make('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.75',
                          'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', 'class': 'hk-i' });
    def.split('|').forEach(function (part) {
      var a = part.split(' ');
      if (a[0] === 'c') s.appendChild(make('circle', { cx: a[1], cy: a[2], r: a[3] }));
      else if (a[0] === 'r') s.appendChild(make('rect', { x: a[1], y: a[2], width: a[3], height: a[4], rx: a[5] || 0 }));
      else s.appendChild(make('path', { d: part }));
    });
    return s;
  }
  // Symbol-Box: "i:name" bzw. Symbolname -> <span class="cls hk-ib"><svg></span>; sonst Text wie bisher (Rueckwaerts-
  // kompatibel). Inhalte nie per innerHTML.
  function iconBox(spec, cls, size) {
    var nm = typeof spec === 'string' && spec.indexOf('i:') === 0 ? spec.slice(2) : null;
    var sp = document.createElement('span'); sp.className = (cls || '') + (nm ? ' hk-ib' : '');
    if (nm) { var s = icon(nm, size); if (s) sp.appendChild(s); }
    else if (spec != null) sp.textContent = spec;
    return sp;
  }
  if (window.HK) { window.HK.icon = icon; window.HK.iconBox = iconBox; window.HK.iconNames = function () { return Object.keys(I); }; }
})();
