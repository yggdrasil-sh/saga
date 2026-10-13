/* Saga - Bilder aus Immich waehlen (Zuletzt, Alben, Suche nach Bildinhalt).
   HK.immichPick(function (url, name) {...}) - das gewaehlte Foto wird serverseitig als eigene Datei uebernommen. */
(function () {
  'use strict';
  function el(t, c, txt) { var e = document.createElement(t); if (c) e.className = c; if (txt != null) e.textContent = txt; return e; }
  function api(m, p, b) {
    return fetch(p, { method: m, credentials: 'same-origin', headers: { 'X-Saga': '1', 'Content-Type': 'application/json' },
      body: b ? JSON.stringify(b) : undefined }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok) throw new Error(d.error || ('HTTP ' + r.status)); return d; });
    });
  }
  var blobs = [];
  function thumb(img, id) {          // Vorschau mit Schutz-Header laden -> blob:-Adresse
    fetch('/api/immich/thumb/' + id, { credentials: 'same-origin', headers: { 'X-Saga': '1' } })
      .then(function (r) { if (!r.ok) throw 0; return r.blob(); })
      .then(function (b) { var u = URL.createObjectURL(b); blobs.push(u); img.src = u; img.classList.add('ok'); })
      .catch(function () { img.classList.add('err'); });
  }
  var io = 'IntersectionObserver' in window ? new IntersectionObserver(function (es) {
    es.forEach(function (e) { if (e.isIntersecting) { io.unobserve(e.target); thumb(e.target, e.target.dataset.id); } });
  }, { rootMargin: '300px' }) : null;
  function lazy(img, id) { img.dataset.id = id; if (io) io.observe(img); else thumb(img, id); }

  function pick(onPick, onCancel) {
    var dlg = el('div', 'hk-modal imm-modal'), card = el('div', 'hk-dialog imm-card'), head = el('div', 'hk-dlg-head');
    dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true'); dlg.setAttribute('aria-label', 'Bild aus Immich');
    head.appendChild(el('h2', null, '📷 Immich'));
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', 'Schließen'); head.appendChild(x);
    var bar = el('div', 'imm-bar'), tRecent = el('button', 'imm-tab on', 'Zuletzt'), tAlbums = el('button', 'imm-tab', 'Alben');
    tRecent.type = tAlbums.type = 'button';
    var q = el('input', 'imm-q'); q.type = 'search'; q.placeholder = 'Suchen … (z. B. „Strand“, „Katze“)';
    bar.appendChild(tRecent); bar.appendChild(tAlbums); bar.appendChild(q);
    var crumb = el('div', 'imm-crumb'), grid = el('div', 'imm-grid'), more = el('button', 'btn imm-more', 'Mehr laden'), info = el('p', 'hk-hint imm-info');
    more.type = 'button'; more.hidden = true;
    card.appendChild(head); card.appendChild(bar); card.appendChild(crumb); card.appendChild(info); card.appendChild(grid); card.appendChild(more);
    dlg.appendChild(card); document.body.appendChild(dlg);
    var state = { album: null, albumName: '', q: '', page: 1, busy: false }, picked = false;
    function close() {
      if (!picked && onCancel) onCancel();
      document.removeEventListener('keydown', onKey); dlg.remove();
      blobs.forEach(function (u) { URL.revokeObjectURL(u); }); blobs = [];
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    x.addEventListener('click', close);
    dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) close(); });

    function tab(t) { tRecent.classList.toggle('on', t === 'recent'); tAlbums.classList.toggle('on', t === 'albums'); }
    function choose(it, tile) {
      if (state.busy) return; state.busy = true; tile.classList.add('busy'); info.textContent = 'Übernehme „' + (it.name || 'Foto') + '“ …';
      api('POST', '/api/immich/import', { id: it.id }).then(function (r) { picked = true; close(); onPick(r.url, r.name); })
        .catch(function (e) { state.busy = false; tile.classList.remove('busy'); info.textContent = 'Nicht übernommen: ' + e.message; });
    }
    function loadAssets(reset) {
      if (reset) { state.page = 1; grid.textContent = ''; }
      more.hidden = true; info.textContent = state.page === 1 ? 'Lade …' : '';
      var b = { page: state.page }; if (state.album) b.album = state.album; if (state.q) b.q = state.q;
      api('POST', '/api/immich/assets', b).then(function (d) {
        info.textContent = d.items.length || state.page > 1 ? '' : 'Keine Fotos gefunden.';
        d.items.forEach(function (it, k) {
          var tile = el('button', 'imm-tile'); tile.type = 'button'; tile.title = it.name || '';
          var img = el('img'); img.alt = it.name || ''; tile.appendChild(img);
          if (it.mime === 'image/gif') tile.appendChild(el('span', 'imm-gif', 'GIF'));
          tile.addEventListener('click', function () { choose(it, tile); });
          grid.appendChild(tile);
          if (state.page === 1 && k < 24) thumb(img, it.id); else lazy(img, it.id);   // erster Bildschirm sofort, Rest beim Scrollen
        });
        if (d.next) { state.page = d.next; more.hidden = false; }
      }).catch(function (e) { info.textContent = e.message; });
    }
    function showRecent() { tab('recent'); state.album = null; state.q = ''; q.value = ''; crumb.textContent = ''; grid.className = 'imm-grid'; loadAssets(true); }
    function showAlbums() {
      tab('albums'); state.album = null; state.q = ''; crumb.textContent = ''; grid.textContent = ''; grid.className = 'imm-grid albums'; more.hidden = true; info.textContent = 'Lade …';
      api('GET', '/api/immich/albums').then(function (d) {
        info.textContent = d.albums.length ? '' : 'Keine Alben.';
        d.albums.forEach(function (a) {
          var tile = el('button', 'imm-tile imm-album'); tile.type = 'button';
          var img = el('img'); img.alt = ''; tile.appendChild(img);
          var cap = el('span', 'imm-cap'); cap.appendChild(el('b', null, a.name)); cap.appendChild(el('small', null, a.count + ' Fotos')); tile.appendChild(cap);
          if (a.thumb) thumb(img, a.thumb);
          tile.addEventListener('click', function () {
            state.album = a.id; state.albumName = a.name; grid.className = 'imm-grid';
            crumb.textContent = ''; var back = el('button', 'linkish', '← Alben'); back.type = 'button'; back.addEventListener('click', showAlbums);
            crumb.appendChild(back); crumb.appendChild(document.createTextNode('  ' + a.name)); loadAssets(true);
          });
          grid.appendChild(tile);
        });
      }).catch(function (e) { info.textContent = e.message; });
    }
    tRecent.addEventListener('click', showRecent);
    tAlbums.addEventListener('click', showAlbums);
    q.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      state.q = q.value.trim(); grid.className = 'imm-grid';
      if (!state.q) return showRecent();
      tab(''); crumb.textContent = 'Suche: „' + state.q + '“' + (state.album ? ' in ' + state.albumName : ''); loadAssets(true);
    });
    more.addEventListener('click', function () { loadAssets(false); });

    api('GET', '/api/settings/immich').then(function (s) {
      if (!s.connected) {
        bar.hidden = true; info.textContent = 'Noch nicht mit Immich verbunden. ';
        var go = el('button', 'btn', 'Einstellungen öffnen'); go.type = 'button';
        go.addEventListener('click', function () { close(); if (window.HKSettings) window.HKSettings.open('immich'); });
        info.appendChild(go); return;
      }
      showRecent(); setTimeout(function () { q.focus(); }, 50);
    }).catch(function (e) { info.textContent = e.message; });
  }
  window.HK = window.HK || {};
  window.HK.immichPick = pick;
})();
