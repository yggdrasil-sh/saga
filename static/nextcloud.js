/* Saga - Nextcloud-Dateien in Seiten (2.3): Einstellungen -> Reiter "Nextcloud" (Konto verknuepfen per Login Flow v2)
   und Dateiauswahl (Zuletzt, Ordner, Suche). Vorschau/Download einer eingefuegten Datei laufen ueber Saga mit dem Konto
   der Person, die sie eingefuegt hat (saga/nextcloud.py). Alle Texte per textContent. Braucht static/common.js.
   HK.nc.info()              -> Promise {enabled, connected, server, user, me, ...} (zwischengespeichert, info(true) = neu)
   HK.nc.pick(onPick, onCancel, {copy: bool, note})  onPick({mode: 'link', block}) bzw. ({mode: 'copy', file})
   HKNextcloud.section(body) -> Abschnitt fuer die Einstellungen */
(function () {
  'use strict';
  var HK = window.HK; if (!HK) return;
  var el = HK.el, api = HK.api;
  var cached = null;
  function info(fresh) {
    if (!cached || fresh) cached = api('GET', '/api/settings/nextcloud').catch(function (e) { cached = null; throw e; });
    return cached;
  }
  function size(n) {
    if (n == null) return '';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0; n = +n;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return (i ? n.toFixed(1) : String(n)) + ' ' + u[i];
  }
  function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ''; }
  function icon(it) {
    if (it.dir || it.kind === 'folder') return '📁';
    var m = it.mime || '';
    if (/^image\//.test(m)) return '🖼';
    if (m === 'application/pdf') return '📕';
    if (/^video\//.test(m)) return '🎞';
    if (/^audio\//.test(m)) return '🎵';
    if (/spreadsheet|excel|csv/.test(m)) return '📊';
    if (/presentation|powerpoint/.test(m)) return '📽';
    if (/zip|compressed|tar/.test(m)) return '🗜';
    return '📄';
  }
  var blobs = [];
  function thumb(img, fileid) {          // Vorschau mit Schutz-Header (X-Saga) -> blob:-Adresse
    fetch('/api/nextcloud/thumb/' + encodeURIComponent(fileid) + '?w=160', { credentials: 'same-origin', headers: { 'X-Saga': '1' } })
      .then(function (r) { if (!r.ok) throw 0; return r.blob(); })
      .then(function (b) { var u = URL.createObjectURL(b); blobs.push(u); img.src = u; img.hidden = false; })
      .catch(function () { img.remove(); });
  }

  function pick(onPick, onCancel, opts) {
    opts = opts || {};
    var dlg = el('div', 'hk-modal nc-modal'), card = el('div', 'hk-dialog nc-card'), head = el('div', 'hk-dlg-head');
    dlg.setAttribute('role', 'dialog'); dlg.setAttribute('aria-modal', 'true'); dlg.setAttribute('aria-label', T('Datei aus Nextcloud'));
    head.appendChild(el('h2', null, '☁ ' + T('Datei aus Nextcloud')));
    var x = el('button', 'icon-btn', '✕'); x.type = 'button'; x.setAttribute('aria-label', T('Schließen')); head.appendChild(x);
    var bar = el('div', 'imm-bar'), tRecent = el('button', 'imm-tab on', T('Zuletzt')), tDirs = el('button', 'imm-tab', T('Ordner'));
    tRecent.type = tDirs.type = 'button';
    var q = el('input', 'imm-q'); q.type = 'search'; q.placeholder = T('Dateiname suchen …'); q.setAttribute('aria-label', T('Dateiname suchen …'));
    bar.appendChild(tRecent); bar.appendChild(tDirs); bar.appendChild(q);
    var crumb = el('div', 'nc-crumb'), list = el('ul', 'nc-list'), info_ = el('p', 'hk-hint imm-info');
    list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', T('Dateien'));
    var foot = el('div', 'nc-foot');
    card.appendChild(head); card.appendChild(bar); card.appendChild(crumb); card.appendChild(info_); card.appendChild(list); card.appendChild(foot);
    dlg.appendChild(card); document.body.appendChild(dlg);
    var picked = false, path = '/', mode = 'recent';
    function close() {
      if (!picked && onCancel) onCancel();
      document.removeEventListener('keydown', onKey); dlg.remove();
      blobs.forEach(function (u) { URL.revokeObjectURL(u); }); blobs = [];
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    x.addEventListener('click', close);
    dlg.addEventListener('mousedown', function (e) { if (e.target === dlg) close(); });
    function tab(t) { tRecent.classList.toggle('on', t === 'recent'); tDirs.classList.toggle('on', t === 'dirs'); }

    // Auswahl: Hinweis + "Als Verweis einfuegen" / "Als Kopie uebernehmen"
    function choose(it) {
      foot.textContent = '';
      var sel = el('div', 'nc-sel');
      sel.appendChild(el('b', null, icon(it) + ' ' + it.name));
      if (!it.dir) sel.appendChild(el('span', 'nc-meta', [size(it.size), when(it.mtime)].filter(Boolean).join(' · ')));
      foot.appendChild(sel);
      foot.appendChild(el('p', 'hk-status nc-warn', '👁 ' + T('Alle, die diese Seite sehen, sehen auch diese Datei.') + ' ' +
        T('Vorschau und Download laufen über dein Nextcloud-Konto, solange es verknüpft ist.')));
      var acts = el('div', 'hk-actions'), link = el('button', 'btn btn-primary', T('Als Verweis einfügen')); link.type = 'button';
      if (opts.link !== false) acts.appendChild(link);
      else foot.lastChild.textContent = T('Auf Seiten anderer Saga-Server lassen sich Nextcloud-Dateien nur als Kopie einfügen.');
      var copy = null;
      if (opts.copy !== false && !it.dir) {
        copy = el('button', opts.link === false ? 'btn btn-primary' : 'btn', T('Als Kopie übernehmen')); copy.type = 'button'; acts.appendChild(copy);
        copy.title = T('Die Datei wird nach Saga kopiert und folgt dann den Saga-Rechten (auch offline, im Backup und Export).');
      }
      var msg = el('p', 'hk-status'); foot.appendChild(acts); foot.appendChild(msg);
      link.addEventListener('click', function () {
        picked = true;
        var b = { inst: it.inst, fileid: it.fileid, kind: it.kind, name: it.name, mime: it.mime, size: it.size, mtime: it.mtime, public: false, fed: false };
        close(); onPick({ mode: 'link', block: b });
      });
      if (copy) copy.addEventListener('click', function () {
        copy.disabled = link.disabled = true; msg.textContent = T('Übernehme „{name}“ …', { name: it.name });
        api('POST', '/api/nextcloud/import', opts.note ? { fileid: it.fileid, note: opts.note } : { fileid: it.fileid })
          .then(function (r) { picked = true; close(); onPick({ mode: 'copy', file: r.file, image: /^image\//.test(r.file.mime || '') }); })
          .catch(function (e) { msg.textContent = T('Nicht übernommen: {err}', { err: e.message }); copy.disabled = link.disabled = false; });
      });
      (opts.link === false ? copy || x : link).focus();
    }
    function row(it) {
      var li = el('li', 'nc-row'), b = el('button', 'nc-item'); b.type = 'button'; b.setAttribute('role', 'option');
      var img = el('img', 'nc-th'); img.alt = ''; img.hidden = true;
      b.appendChild(el('span', 'nc-ic', icon(it))); if (it.preview && !it.dir) { b.appendChild(img); thumb(img, it.fileid); }
      var tx = el('span', 'nc-tx'); tx.appendChild(el('span', 'nc-name', it.name));
      tx.appendChild(el('span', 'nc-meta', it.dir ? T('Ordner') : [size(it.size), when(it.mtime)].filter(Boolean).join(' · ')));
      if (mode !== 'dirs' && it.path) tx.appendChild(el('span', 'nc-path', it.path.replace(/\/[^/]*$/, '') || '/'));
      b.appendChild(tx);
      b.addEventListener('click', function () {
        if (it.dir && mode === 'dirs') { path = it.path; load(); return; }
        Array.prototype.forEach.call(list.querySelectorAll('.nc-item.on'), function (n) { n.classList.remove('on'); });
        b.classList.add('on'); choose(it);
      });
      li.appendChild(b); return li;
    }
    function show(items) {
      list.textContent = ''; foot.textContent = '';
      info_.textContent = items.length ? '' : T('Nichts gefunden.');
      items.forEach(function (it) { list.appendChild(row(it)); });
    }
    function crumbs() {
      crumb.textContent = '';
      if (mode !== 'dirs') return;
      var parts = path.split('/').filter(Boolean), acc = '';
      function link(label, p) {
        var b = el('button', 'linkish', label); b.type = 'button';
        b.addEventListener('click', function () { path = p; load(); }); crumb.appendChild(b);
      }
      link('☁', '/');
      parts.forEach(function (p) { acc += '/' + p; crumb.appendChild(document.createTextNode(' / ')); link(p, acc); });
      if (parts.length) {                                     // aktuellen Ordner selbst einfuegen
        var here = el('button', 'btn nc-here', T('Diesen Ordner einfügen')); here.type = 'button';
        here.addEventListener('click', function () {
          api('POST', '/api/nextcloud/resolve', { url: '' , fileid: crumb.dataset.fileid || '' }).then(function (r) { choose(r.item); })
            .catch(function (e) { info_.textContent = e.message; });
        });
        here.hidden = !crumb.dataset.fileid; crumb.appendChild(here);
      }
    }
    function load() {
      info_.textContent = T('Lade …'); list.textContent = ''; foot.textContent = '';
      var p;
      if (mode === 'dirs') {
        crumb.dataset.fileid = ''; crumbs();
        p = api('GET', '/api/nextcloud/list?path=' + encodeURIComponent(path)).then(function (d) { show(d.items); });
        // Datei-ID des Ordners fuer "Diesen Ordner einfuegen" (aus der Liste des Elternordners)
        if (path !== '/') {
          var parent = path.replace(/\/[^/]+$/, '') || '/';
          api('GET', '/api/nextcloud/list?path=' + encodeURIComponent(parent)).then(function (d) {
            var me = d.items.filter(function (i) { return i.path === path; })[0];
            if (me) { crumb.dataset.fileid = me.fileid; var h = crumb.querySelector('.nc-here'); if (h) h.hidden = false; }
          }).catch(function () {});
        }
      } else {
        crumb.textContent = mode === 'search' ? T('Suche: „{q}“', { q: q.value.trim() }) : '';
        p = api('GET', '/api/nextcloud/search?q=' + encodeURIComponent(mode === 'search' ? q.value.trim() : '')).then(function (d) { show(d.items); });
      }
      p.catch(function (e) { info_.textContent = e.message; });
    }
    tRecent.addEventListener('click', function () { mode = 'recent'; tab('recent'); q.value = ''; load(); });
    tDirs.addEventListener('click', function () { mode = 'dirs'; tab('dirs'); q.value = ''; load(); });
    q.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter') return; e.preventDefault();
      if (!q.value.trim()) { mode = 'recent'; tab('recent'); } else { mode = 'search'; tab(''); }
      load();
    });
    info(true).then(function (s) {
      if (!s.enabled) { bar.hidden = true; info_.textContent = T('Nextcloud-Dateien sind auf diesem Server abgeschaltet.'); return; }
      if (!s.connected) {
        bar.hidden = true; info_.textContent = T('Noch nicht mit Nextcloud verbunden.') + ' ';
        var go = el('button', 'btn', T('Einstellungen öffnen')); go.type = 'button';
        go.addEventListener('click', function () { close(); if (window.HKSettings) window.HKSettings.open('nextcloud'); });
        if (!window.HKSettings) go.hidden = true;
        info_.appendChild(go); return;
      }
      load(); setTimeout(function () { q.focus(); }, 50);
    }).catch(function (e) { info_.textContent = e.message; });
  }

  // --- Einstellungen -> Nextcloud ------------------------------------------------------------------------
  function field(label, input) { var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input); return l; }
  function section(body) {
    var sec = el('section', 'hk-set hk-set-nextcloud'); body.appendChild(sec);
    sec.appendChild(el('h3', null, 'Nextcloud'));
    sec.appendChild(el('p', 'hk-hint', T('Verknüpfe deine eigene Nextcloud, um Dateien daraus in Seiten einzufügen („/“-Menü → „Nextcloud-Datei“ oder einen Nextcloud-Link einfügen). Saga bekommt ein eigenes App-Passwort – dein Passwort sieht Saga nie.')));
    var box = el('div'); sec.appendChild(box);
    var timer = null;
    function poll() { clearTimeout(timer); timer = setTimeout(function () { if (document.contains(sec)) load(); }, 2500); }
    function load() {
      info(true).then(function (s) {
        box.textContent = '';
        var msg = el('p', 'hk-status');
        if (!s.enabled) { box.appendChild(el('p', 'hk-status err', T('Nextcloud-Dateien sind auf diesem Server abgeschaltet.'))); }
        if (s.flow && s.flow.state === 'wait' && s.flow.login) {
          box.appendChild(el('p', null, T('Nextcloud gefunden. Melde dich dort an und erlaube den Zugriff – Saga bekommt ein eigenes App-Passwort (dein Passwort sieht Saga nie).')));
          var a = el('a', 'btn btn-primary', T('In Nextcloud anmelden') + ' ↗'); a.href = s.flow.login; a.target = '_blank'; a.rel = 'noopener noreferrer';
          var cancel = el('button', 'btn', T('Abbrechen')); cancel.type = 'button';
          cancel.addEventListener('click', function () { api('POST', '/api/settings/nextcloud/cancel').then(load); });
          var bar = el('div', 'hk-actions'); bar.appendChild(a); bar.appendChild(cancel); box.appendChild(bar);
          poll(); return;
        }
        if (s.flow && s.flow.message) { msg.textContent = s.flow.message; msg.className = 'hk-status err'; }
        if (s.connected) {
          box.appendChild(el('p', null, T('✅ Verbunden als {name}', { name: s.user || '' }) + ' (' + String(s.server || '').replace(/^https?:\/\//, '') + ')'));
          var dl = el('dl', 'ub-dl');
          dl.appendChild(el('dt', null, T('Verknüpft seit'))); dl.appendChild(el('dd', null, when(s.since) || '–'));
          dl.appendChild(el('dt', null, T('Zuletzt genutzt'))); dl.appendChild(el('dd', null, when(s.used) || '–'));
          box.appendChild(dl);
          box.appendChild(el('p', 'hk-hint', T('Alle, die eine Seite sehen, sehen auch die Nextcloud-Dateien, die du dort eingefügt hast. Trennst du die Verknüpfung, zeigen deine eingefügten Dateien nur noch eine Karte ohne Inhalt.')));
          var off = el('button', 'btn', T('Trennen')); off.type = 'button';
          off.addEventListener('click', function () {
            if (!confirm(T('Nextcloud trennen? Saga widerruft das App-Passwort; deine eingefügten Dateien zeigen dann keinen Inhalt mehr.'))) return;
            off.disabled = true; api('DELETE', '/api/settings/nextcloud').then(load).catch(function (e) { msg.textContent = e.message; off.disabled = false; });
          });
          var bar2 = el('div', 'hk-actions'); bar2.appendChild(off); box.appendChild(bar2); box.appendChild(msg);
          return;
        }
        if (!s.enabled) return;
        var url = el('input', 'hk-in'); url.type = 'url'; url.placeholder = 'https://cloud.example.org'; url.value = s.suggest || '';
        box.appendChild(field(T('Nextcloud-Adresse'), url));
        var go = el('button', 'btn btn-primary', T('Verbinden')); go.type = 'button';
        var bar3 = el('div', 'hk-actions'); bar3.appendChild(go); box.appendChild(bar3); box.appendChild(msg);
        go.addEventListener('click', function () {
          msg.textContent = T('Prüfe …'); msg.className = 'hk-status'; go.disabled = true;
          // Tab sofort oeffnen (Popup-Sperre), Adresse folgt, sobald Saga den Login-Link hat
          var win = window.open('', '_blank');
          api('POST', '/api/settings/nextcloud', { url: url.value.trim() }).then(function (r) {
            if (win) { try { win.opener = null; win.location.href = r.flow; } catch (e) { win = null; } }
            load();
          }).catch(function (e) { if (win) win.close(); msg.textContent = e.message; msg.className = 'hk-status err'; go.disabled = false; });
        });
      }).catch(function (e) { box.textContent = T('Nicht geladen: {err}', { err: e.message }); });
    }
    load();
  }

  HK.nc = { info: info, pick: pick, icon: icon, size: size };
  window.HKNextcloud = { section: section };
})();
