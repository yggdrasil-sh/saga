// Saga - Emoji-Auswahl fuer Reaktionen (2.5): lokal, ohne CDN und ohne Bilder (System-Schrift). Zuletzt benutzte
// Emojis je Browser in localStorage (nur Bequemlichkeit, darf fehlen). window.SagaEmoji.picker(box, onPick) baut die
// Auswahl in box: Reiter je Gruppe, Raster mit Tastatur (Pfeile, Enter), Klick -> onPick(emoji).
(function () {
  'use strict';
  var T = window.T || function (s) { return s; };
  var CATS = [
    ['😀', 'Smileys', '😀 😃 😄 😁 😆 😅 🤣 😂 🙂 😉 😊 😇 🥰 😍 🤩 😘 😗 😚 😋 😛 😜 🤪 😝 🤗 🤭 🤫 🤔 🤐 🤨 😐 😑 😶 😏 😒 🙄 😬 😌 😔 😪 😴 😷 🤒 🤕 🤢 🤧 🥵 🥶 🥴 😵 🤯 🤠 🥳 😎 🤓 🧐 😕 😟 🙁 😮 😯 😲 😳 🥺 😦 😧 😨 😰 😥 😢 😭 😱 😖 😣 😞 😓 😩 😫 🥱 😤 😡 😠 🤬 😈 💀 💩 🤡 👻 👽 🤖'],
    ['👍', 'Gesten', '👍 👎 👌 🤌 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐️ 🖖 👋 👏 🙌 👐 🤲 🤝 🙏 ✍️ 💪 🦾 🫶 👀 👁️ 🧠 🫡 🤷 🤦 🙋 🙆 🙅 💁 🙇'],
    ['❤️', 'Herzen', '❤️ 🧡 💛 💚 💙 💜 🤎 🖤 🤍 💔 ❣️ 💕 💞 💓 💗 💖 💘 💝 💟 ♥️ 💯 💢 💥 💫 💦 💨 🕳️ 💬 💭 💤'],
    ['🐶', 'Natur', '🐶 🐱 🐭 🐹 🐰 🦊 🐻 🐼 🐨 🐯 🦁 🐮 🐷 🐸 🐵 🙈 🙉 🙊 🐔 🐧 🐦 🦆 🦉 🐺 🐴 🦄 🐝 🦋 🐌 🐞 🐢 🐍 🐙 🐬 🐳 🐟 🌸 🌹 🌻 🌷 🌱 🌲 🌳 🍀 🍁 🍂 🌍 🌙 ⭐ 🌟 ☀️ ⛅ 🌧️ ⛈️ ❄️ ☃️ 🌈 🔥 💧 🌊'],
    ['🍕', 'Essen', '🍎 🍐 🍊 🍋 🍌 🍉 🍇 🍓 🫐 🍒 🍑 🥭 🍍 🥥 🥝 🍅 🥑 🥦 🥕 🌽 🌶️ 🥔 🥐 🍞 🥨 🧀 🥚 🍳 🥞 🥓 🍔 🍟 🍕 🌭 🥪 🌮 🌯 🥗 🍝 🍜 🍣 🍱 🍰 🎂 🧁 🍪 🍫 🍬 🍭 🍩 🍿 ☕ 🍵 🥤 🍺 🍻 🥂 🍷 🍸 🍹'],
    ['⚽', 'Aktivitäten', '⚽ 🏀 🏈 ⚾ 🎾 🏐 🏉 🎱 🏓 🏸 🥅 ⛳ 🏹 🎣 🥊 🎽 ⛸️ 🎿 🏂 🏆 🥇 🥈 🥉 🏅 🎖️ 🎗️ 🎫 🎟️ 🎪 🎭 🎨 🎬 🎤 🎧 🎼 🎹 🥁 🎷 🎺 🎸 🎻 🎲 🧩 🎮 🎯 🎳 🎉 🎊 🎈 🎁'],
    ['🚗', 'Reisen', '🚗 🚕 🚙 🚌 🚎 🏎️ 🚓 🚑 🚒 🚚 🚜 🚲 🛴 🛵 🏍️ 🚨 🚃 🚄 🚅 🚆 🚇 🚊 ✈️ 🛫 🛬 🚀 🛸 🚁 ⛵ 🚤 🚢 ⚓ ⛽ 🚧 🗺️ 🗽 🗼 🏰 🏯 🏟️ 🎡 🎢 ⛲ 🏖️ 🏝️ 🏔️ ⛺ 🏠 🏡 🏢 🏥 🏦 🏨 🏫 ⛪ 🌆 🌃'],
    ['💡', 'Objekte', '⌚ 📱 💻 ⌨️ 🖥️ 🖨️ 🖱️ 💾 💿 📷 📸 📹 🎥 📞 ☎️ 📺 📻 ⏰ ⏳ 🔋 🔌 💡 🔦 🕯️ 💸 💵 💶 💰 💳 💎 🔧 🔨 🛠️ ⚙️ 🔩 🧲 🔑 🗝️ 🔒 🔓 🚪 🛏️ 🛋️ 🚿 🛁 🧹 🧺 🧻 🎀 📦 📫 📬 ✉️ 📝 📅 📆 📌 📎 ✂️ 📚 📖 🔖 🔍'],
    ['✅', 'Symbole', '✅ ☑️ ✔️ ❌ ❎ ➕ ➖ ➗ ✖️ ❓ ❔ ❗ ❕ ‼️ ⁉️ ⚠️ 🚫 ⛔ 🔞 ♻️ 🔰 ⭕ 🆗 🆕 🆒 🆓 🆙 🔝 🔜 ▶️ ⏸️ ⏹️ ⏺️ ⏭️ ⏮️ 🔀 🔁 🔂 ➡️ ⬅️ ⬆️ ⬇️ ↩️ ↪️ 🔃 🔄 🎵 🎶 ™️ ©️ ®️ 〰️ ➰ 🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟥 🟩 🟦 🏁 🚩 🏳️ 🏴']
  ];
  var LABELS = { Smileys: T('Smileys'), Gesten: T('Gesten'), Herzen: T('Herzen'), Natur: T('Natur'), Essen: T('Essen und Trinken'),
                 'Aktivitäten': T('Aktivitäten'), Reisen: T('Reisen und Orte'), Objekte: T('Objekte'), Symbole: T('Symbole') };
  var RKEY = 'saga-emoji-recent';
  function recent() {
    try { var r = JSON.parse(localStorage.getItem(RKEY) || '[]'); return Array.isArray(r) ? r.filter(function (x) { return typeof x === 'string'; }).slice(0, 24) : []; }
    catch (e) { return []; }
  }
  function remember(e) {
    try { var r = recent().filter(function (x) { return x !== e; }); r.unshift(e); localStorage.setItem(RKEY, JSON.stringify(r.slice(0, 24))); }
    catch (x) { /* ohne Speicher: keine "Zuletzt"-Liste */ }
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function picker(box, onPick) {
    box.textContent = '';
    box.classList.add('em-pick');
    var tabs = el('div', 'em-tabs'), grid = el('div', 'em-grid');
    tabs.setAttribute('role', 'tablist'); grid.setAttribute('role', 'grid');
    var groups = [], rc = recent();
    if (rc.length) groups.push(['🕘', T('Zuletzt benutzt'), rc]);
    CATS.forEach(function (c) { groups.push([c[0], LABELS[c[1]] || c[1], c[2].split(' ')]); });
    var cur = 0;
    function show(i) {
      cur = i; grid.textContent = '';
      Array.prototype.forEach.call(tabs.children, function (t, k) { t.setAttribute('aria-selected', k === i ? 'true' : 'false'); t.classList.toggle('on', k === i); });
      grid.setAttribute('aria-label', groups[i][1]);
      groups[i][2].forEach(function (e) {
        var b = el('button', 'em-b', e); b.type = 'button'; b.setAttribute('aria-label', e); b.dataset.e = e;
        b.addEventListener('click', function () { remember(e); onPick(e); });
        grid.appendChild(b);
      });
    }
    groups.forEach(function (g, i) {
      var t = el('button', 'em-tab', g[0]); t.type = 'button'; t.setAttribute('role', 'tab'); t.title = g[1]; t.setAttribute('aria-label', g[1]);
      t.addEventListener('click', function () { show(i); });
      tabs.appendChild(t);
    });
    grid.addEventListener('keydown', function (ev) {
      var bs = Array.prototype.slice.call(grid.querySelectorAll('.em-b')), i = bs.indexOf(document.activeElement);
      if (i < 0) return;
      var cols = Math.max(1, Math.round(grid.clientWidth / (bs[0].offsetWidth || 36))), j = i;
      if (ev.key === 'ArrowRight') j = i + 1; else if (ev.key === 'ArrowLeft') j = i - 1;
      else if (ev.key === 'ArrowDown') j = i + cols; else if (ev.key === 'ArrowUp') j = i - cols; else return;
      ev.preventDefault(); ev.stopPropagation();
      if (bs[Math.max(0, Math.min(bs.length - 1, j))]) bs[Math.max(0, Math.min(bs.length - 1, j))].focus();
    });
    box.appendChild(tabs); box.appendChild(grid);
    show(0);
    var f = grid.querySelector('.em-b'); if (f) setTimeout(function () { f.focus(); }, 0);
  }
  window.SagaEmoji = { picker: picker, remember: remember, QUICK: ['👍', '❤️', '😂', '😮', '😢', '🙏'] };
})();
