// Saga 2.6 (Bugs-DB 2027a, Feature contacts2): Adressbuch im Browser - Einstellungen > Arbeitsbereich > Kontakte.
// Vertrag: docs/APP-API.md "Kontakte (2.6, Bugs-DB 2027a, contacts2)" (GET/POST/PATCH/DELETE /api/contacts, Landesvorwahl
// /api/contacts/settings, Live contacts.changed). static/settings.js ruft HKContacts.section(body) auf; static/chat.js
// oeffnet einen Kontakt mit HKContacts.open(id, {edit: true}) ("Kontakt bearbeiten …" im Menue eines Kontakts).
// Ansichten im Bereich: Liste (Suche, "+ Neuer Kontakt", Landesvorwahl) -> Detail (feste Saga-Zeile mit "Nachricht",
// tel:/mailto:, Anrede, Grussform, Bearbeiten, Loeschen) -> Formular (Name fuer mich, Anrede, Grussform, Nummern/Mails mit
// Typ; 409: neuer Stand, Eingabe bleibt). Ein Kontakt vergibt keine Rechte; Saga-Adressen kommen nur aus gemeinsamen
// Chats und sind hier nicht aenderbar. Alle Texte per textContent.
(function () {
  'use strict';
  var HK = window.HK; if (!HK || !document.getElementById('hk-user')) return;
  var el = HK.el, api = HK.api, T = window.T;
  var TYPES = [['mobile', T('Mobil')], ['home', T('Privat')], ['work', T('Arbeit')], ['other', T('Sonstige')]];
  var LIMIT = 200, KIND_MAX = 10;
  // Landesvorwahlen fuer den Vorschlag aus der Region des Browsers (nur ein Vorschlag - eingestellt wird erst per Klick)
  var CC = { DE: '49', AT: '43', CH: '41', LI: '423', LU: '352', NL: '31', BE: '32', FR: '33', IT: '39', ES: '34', PT: '351',
             GB: '44', IE: '353', DK: '45', SE: '46', NO: '47', FI: '358', PL: '48', CZ: '420', HU: '36', GR: '30', TR: '90',
             RU: '7', UA: '380', US: '1', CA: '1', MX: '52', BR: '55', AR: '54', TH: '66', JP: '81', CN: '86', IN: '91', AU: '61' };
  // Laendervorwahlen mit 1 bzw. 2 Ziffern (der Rest hat 3; E.164-Vorwahlen sind praefixfrei) - nur fuer die Anzeige
  var CC1 = /^[17]/, CC2 = /^(2[07]|3[0-469]|4[0-13-9]|5[1-8]|6[0-6]|8[1246]|9[0-58])/;

  function typeName(t) { for (var i = 0; i < TYPES.length; i++) if (TYPES[i][0] === t) return TYPES[i][1]; return ''; }
  // +491701234567 -> "+49 1701234567" (Laendervorwahl abgesetzt, Rest wie gespeichert)
  function showNum(v) {
    var d = String(v || '').replace(/^\+/, ''); if (!/^\d+$/.test(d)) return String(v || '');
    var n = CC1.test(d) ? 1 : CC2.test(d) ? 2 : 3;
    return '+' + d.slice(0, n) + ' ' + d.slice(n);
  }
  function region() {
    var l = (navigator.languages && navigator.languages[0]) || navigator.language || '';
    var m = /[-_]([A-Za-z]{2})\b/.exec(l); return m ? m[1].toUpperCase() : '';
  }
  function ic(name, cls, px) { return HK.iconBox ? HK.iconBox('i:' + name, cls || 'kb-ic', px || 16) : el('span', cls || 'kb-ic'); }
  function hue(s) { var h = 0; s = String(s || ''); for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360; return h; }
  function avatar(c, px) {
    var a = el('span', 'kb-av' + (c.saga ? '' : ' kb-av-m'), (c.name || '?').trim().charAt(0).toUpperCase() || '?');
    a.style.setProperty('--kb-h', String(hue(c.saga ? c.saga.ref : c.id))); a.setAttribute('aria-hidden', 'true');
    if (px) { a.style.width = a.style.height = px + 'px'; }
    return c.saga && c.saga.avatar ? HK.avatarImg(a, c.saga.avatar, px || 36) : a;
  }
  function sagaSub(s) {
    if (!s) return '';
    if (s.gone) return s.remote ? T('Server {host} nicht verbunden', { host: s.host || s.sub }) : T('Konto nicht mehr aktiv');
    if (s.remote) return T('Person auf {host}', { host: s.host || s.sub });
    return [s.sub || '', s.ready === false ? T('hat Chats noch nicht eingerichtet') : ''].filter(Boolean).join(' · ');
  }
  function firstOf(c, kind) { var a = (c.addrs || []).filter(function (x) { return x.kind === kind; })[0]; return a ? a.value : ''; }
  // Unterzeile in der Liste: Saga-Angabe, sonst erste Nummer/Mail (die nicht schon der Name ist)
  function listSub(c) {
    var parts = [];
    if (c.saga) parts.push(sagaSub(c.saga) || T('Saga'));
    var n = firstOf(c, 'e164'), m = firstOf(c, 'email');
    if (n && showNum(n) !== c.name && n !== c.name) parts.push(showNum(n));
    else if (m && m !== c.name) parts.push(m);
    return parts.filter(Boolean).join(' · ');
  }
  function say(box, text, kind) {                      // eigene Klassen (z. B. kb-err) bleiben
    box.textContent = text || ''; box.classList.add('hk-status'); box.classList.remove('ok', 'err');
    if (text) box.classList.add(kind || 'err');
  }

  // Direkt-Chat mit der festen Saga-Adresse: auf der Chat-Seite ueber den Hash (#u lokal, #h fremd), sonst dorthin wechseln
  function message(s) {
    var h = (s.remote ? 'h' : 'u') + encodeURIComponent(s.ref);
    if (location.pathname === '/chats' && window.HKChat) {
      if (window.HKSettings) HKSettings.close();
      if (location.hash === '#' + h) location.hash = ''; location.hash = h;
    } else HK.go('/chats#' + h);
  }

  var pending = null;                                   // HKContacts.open(): Ziel fuer den naechsten Aufbau des Bereichs
  var live = [];                                        // offene Bereiche (fuer contacts.changed)

  function section(body) {
    var sec = el('section', 'hk-set hk-set-contacts'); body.appendChild(sec);
    sec.appendChild(el('h3', null, T('Kontakte')));
    var box = el('div', 'kb'); sec.appendChild(box);
    var S = { view: 'list', q: '', list: null, total: 0, cur: null, cc: undefined, seq: 0, dirty: false };
    var me = { sec: sec, changed: changed };
    live.push(me);

    // --- Liste -----------------------------------------------------------------------------------------------------
    var listBox = null, listUl = null, countP = null, qIn = null;
    function showList(focusQ) {
      S.view = 'list'; S.cur = null; box.textContent = '';
      box.appendChild(el('p', 'hk-hint', T('Dein Adressbuch – nur für dich sichtbar. Personen aus gemeinsamen Chats kommen von selbst dazu; Nummern, E-Mail-Adressen und die Anrede trägst du selbst ein. Kontakte verlassen diesen Server nie, und die Person erfährt nichts davon.')));
      var bar = el('div', 'kb-bar');
      qIn = el('input', 'hk-in kb-q'); qIn.type = 'search'; qIn.placeholder = T('Kontakte durchsuchen');
      qIn.setAttribute('aria-label', T('Kontakte durchsuchen')); qIn.value = S.q; qIn.maxLength = 100;
      var add = el('button', 'btn btn-primary kb-new'); add.type = 'button';
      add.appendChild(ic('plus', 'mi-ic', 16)); add.appendChild(el('span', null, T('Neuer Kontakt')));
      bar.appendChild(qIn); bar.appendChild(add); box.appendChild(bar);
      countP = el('p', 'hk-hint kb-count'); countP.setAttribute('role', 'status'); box.appendChild(countP);
      listUl = el('ul', 'kb-list'); listUl.setAttribute('aria-label', T('Kontakte')); box.appendChild(listUl);
      listBox = box;
      var tm = null;
      // entprellt (der Server sieht fuer eine Suche bis zu 5000 Kontakte durch), gleicher Begriff fragt nicht erneut
      qIn.addEventListener('input', function () {
        clearTimeout(tm);
        tm = setTimeout(function () { var v = qIn.value.trim(); if (v === S.q) return; S.q = v; loadList(); }, 350);
      });
      qIn.addEventListener('keydown', function (e) { if (e.key === 'Escape' && qIn.value) { e.preventDefault(); e.stopPropagation(); qIn.value = ''; S.q = ''; loadList(); } });
      add.addEventListener('click', function () { showForm(null); });
      ccPart(box);
      if (S.list) drawList(); else loadList();
      if (focusQ) setTimeout(function () { try { qIn.focus({ preventScroll: true }); } catch (e) { qIn.focus(); } }, 0);
    }
    function loadList() {
      var n = ++S.seq;
      return api('GET', '/api/contacts?limit=' + LIMIT + (S.q ? '&q=' + encodeURIComponent(S.q) : '')).then(function (r) {
        if (n !== S.seq) return;
        S.list = r.contacts || []; S.total = r.total || S.list.length;
        if (S.view === 'list') drawList();
      }).catch(function (e) {
        if (n !== S.seq || S.view !== 'list' || !listUl) return;
        listUl.textContent = ''; say(countP, T('Nicht geladen: {err}', { err: e.message }));
      });
    }
    function drawList() {
      if (!listUl) return;
      listUl.textContent = '';
      var L = S.list || [];
      countP.classList.remove('hk-status', 'ok', 'err'); countP.textContent = '';
      if (!L.length) {
        countP.textContent = S.q ? T('Keine Kontakte gefunden.') : T('Noch keine Kontakte. Wer mit dir chattet, erscheint hier – oder lege selbst einen an.');
        return;
      }
      countP.textContent = S.q ? T('{n} {n:Treffer|Treffer}', { n: S.total }) : T('{n} {n:Kontakt|Kontakte}', { n: S.total });
      L.forEach(function (c) {
        var li = el('li', 'kb-row'), b = el('button', 'kb-item' + (c.saga && c.saga.gone ? ' kb-gone' : '')); b.type = 'button';
        b.dataset.id = String(c.id);
        b.appendChild(avatar(c));
        var t = el('span', 'kb-item-t'); t.appendChild(el('span', 'kb-item-n', c.name || T('Ohne Namen')));
        var sub = listSub(c); if (sub) t.appendChild(el('span', 'kb-item-s', sub));
        b.appendChild(t);
        b.appendChild(ic('chevron-right', 'kb-go', 15));
        b.addEventListener('click', function () { showDetail(c.id, false); });
        li.appendChild(b); listUl.appendChild(li);
      });
      if (S.total > L.length) listUl.appendChild(el('li', 'hk-hint kb-more', T('Es werden die ersten {n} angezeigt – die Suche findet alle.', { n: L.length })));
    }

    // --- Landesvorwahl ----------------------------------------------------------------------------------------------
    function ccPart(into) {
      var wrap = el('div', 'kb-cc'); into.appendChild(wrap);
      wrap.appendChild(el('h4', null, T('Landesvorwahl')));
      var p = el('p', 'hk-hint', T('Für Nummern ohne + (z. B. 0170 …): Saga setzt diese Landesvorwahl davor.')); p.id = 'kb-cc-hint';
      wrap.appendChild(p);
      var row = el('div', 'kb-cc-row'), pl = el('span', 'kb-cc-plus', '+');
      var inp = el('input', 'hk-in kb-cc-in'); inp.type = 'text'; inp.inputMode = 'numeric'; inp.maxLength = 4; inp.placeholder = '49';
      inp.setAttribute('aria-label', T('Landesvorwahl')); inp.setAttribute('aria-describedby', p.id); inp.autocomplete = 'off';
      var sv = el('button', 'btn kb-cc-save', T('Speichern')); sv.type = 'button';
      var sug = el('button', 'linkbtn kb-cc-sug'); sug.type = 'button'; sug.hidden = true;
      row.appendChild(pl); row.appendChild(inp); row.appendChild(sv); row.appendChild(sug); wrap.appendChild(row);
      var st = el('p', 'hk-status'); st.setAttribute('role', 'status'); wrap.appendChild(st);
      function fill() {
        inp.value = S.cc || '';
        var r = CC[region()];
        sug.hidden = !!S.cc || !r;
        if (r) sug.textContent = T('Vorschlag: +{cc} übernehmen', { cc: r });
        sug.onclick = function () { save(r); };
      }
      function save(v) {
        sv.disabled = true;
        api('PUT', '/api/contacts/settings', { cc: v ? String(v).replace(/^\+/, '') : null }).then(function (r) {
          S.cc = r.cc || null; fill(); say(st, S.cc ? T('Gespeichert: +{cc}', { cc: S.cc }) : T('Landesvorwahl entfernt.'), 'ok');
        }).catch(function (e) { say(st, e.message); }).then(function () { sv.disabled = false; });
      }
      sv.addEventListener('click', function () { save(inp.value.trim()); });
      inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); save(inp.value.trim()); } });
      if (S.cc !== undefined) fill();
      else api('GET', '/api/contacts/settings').then(function (r) { S.cc = r.cc || null; if (wrap.isConnected) fill(); }).catch(function () { wrap.hidden = true; });
    }

    // --- Detail -----------------------------------------------------------------------------------------------------
    function backBtn(label, fn) {
      var b = el('button', 'linkbtn kb-back'); b.type = 'button';
      b.appendChild(ic('chevron-left', 'mi-ic', 16)); b.appendChild(el('span', null, label));
      b.addEventListener('click', fn); return b;
    }
    function head(c) {
      var h = el('div', 'kb-head');
      h.appendChild(avatar(c, 56));
      var t = el('div', 'kb-head-t'), n = el('h4', 'kb-name', c.name || T('Ohne Namen')); n.tabIndex = -1;
      t.appendChild(n);
      var sub = c.saga ? sagaSub(c.saga) : T('Selbst angelegt');
      if (sub) t.appendChild(el('p', 'hk-hint kb-head-s', sub));
      h.appendChild(t); return h;
    }
    function showDetail(id, edit) {
      S.view = 'detail'; box.textContent = '';
      box.appendChild(backBtn(T('Alle Kontakte'), function () { showList(true); }));
      var st = el('p', 'hk-status'); box.appendChild(st);
      say(st, T('Lädt …'), 'ok');
      return api('GET', '/api/contacts/' + id).then(function (c) {
        if (S.view !== 'detail') return;
        if (edit) showForm(c); else drawDetail(c);
      }).catch(function (e) {
        if (S.view !== 'detail') return;
        say(st, e.status === 404 ? T('Kontakt nicht gefunden.') : T('Nicht geladen: {err}', { err: e.message }));
      });
    }
    function line(cls, icon, main, sub, href) {
      var li = el('li', 'kb-line ' + cls);
      li.appendChild(ic(icon, 'kb-line-ic', 18));
      var t = el('span', 'kb-line-t'), m;
      if (href) { m = el('a', 'kb-line-v', main); m.href = href; if (/^mailto:/.test(href)) m.rel = 'noopener'; }
      else m = el('span', 'kb-line-v', main);
      t.appendChild(m); if (sub) t.appendChild(el('span', 'kb-line-s', sub));
      li.appendChild(t); return li;
    }
    function drawDetail(c) {
      S.view = 'detail'; S.cur = c; S.dirty = false; box.textContent = '';
      box.appendChild(backBtn(T('Alle Kontakte'), function () { showList(true); }));
      box.appendChild(head(c));
      var ul = el('ul', 'kb-lines'); ul.setAttribute('aria-label', T('Adressen'));
      (c.addrs || []).forEach(function (a) {
        if (a.kind === 'saga' || a.kind === 'fed') {
          var s = c.saga && c.saga.ref === a.value ? c.saga : null;
          var li = line('kb-l-saga', 'message', T('Saga'), a.kind === 'fed' ? T('auf einem anderen Saga-Server') : T('aus einem gemeinsamen Chat'));
          if (s) {
            var go = el('button', 'btn kb-msg', T('Nachricht')); go.type = 'button';
            go.setAttribute('aria-label', T('Nachricht an {name}', { name: c.name }));
            if (s.gone) { go.disabled = true; go.title = T('Gerade nicht erreichbar'); }
            go.addEventListener('click', function () { message(s); });
            li.appendChild(go);
          }
          ul.appendChild(li);
        } else if (a.kind === 'e164') {
          ul.appendChild(line('kb-l-tel', 'smartphone', showNum(a.value), typeName(a.type) || T('Telefon'), 'tel:' + a.value));
        } else if (a.kind === 'email') {
          ul.appendChild(line('kb-l-mail', 'mail', a.value, typeName(a.type) || T('E-Mail'), 'mailto:' + a.value));
        }
      });
      if (ul.childNodes.length) box.appendChild(ul);
      var dl = el('dl', 'kb-facts');
      function fact(k, v) { dl.appendChild(el('dt', null, k)); dl.appendChild(el('dd', v ? null : 'kb-none', v || '—')); }
      if (c.saga && c.own_name) fact(T('Name für mich'), c.own_name);
      fact(T('Anrede'), c.form === 'informal' ? T('du##Anrede') : c.form === 'formal' ? T('Sie##Anrede') : '');
      fact(T('Grußform'), c.salutation || '');
      box.appendChild(dl);
      var bar = el('div', 'hk-actions kb-actions');
      var ed = el('button', 'btn btn-primary kb-edit', T('Bearbeiten')), del = el('button', 'btn btn-danger kb-del', T('Kontakt löschen'));
      ed.type = del.type = 'button'; bar.appendChild(ed); bar.appendChild(del); box.appendChild(bar);
      var st = el('p', 'hk-status'); st.setAttribute('role', 'status'); box.appendChild(st);
      ed.addEventListener('click', function () { showForm(c); });
      del.addEventListener('click', function () {
        HK.ask({ title: T('Kontakt „{name}“ löschen?', { name: c.name }),
                 text: c.saga ? T('Nummern, E-Mail-Adressen und Anrede gehen verloren, und die Person verschwindet aus deinen Chat-Vorschlägen. Bestehende Chats bleiben. Ein neuer gemeinsamer Chat legt wieder einen leeren Kontakt an.')
                              : T('Der Kontakt mit allen Nummern und E-Mail-Adressen wird gelöscht.'),
                 ok: T('Löschen'), danger: true }).then(function (yes) {
          if (!yes) return;
          del.disabled = true;
          api('DELETE', '/api/contacts/' + c.id).then(function () {
            HK.toast(T('Kontakt gelöscht.')); S.list = null; showList(true);
          }).catch(function (e) { del.disabled = false; say(st, e.message); });
        });
      });
      focusName();
    }
    function focusName() {
      setTimeout(function () { var n = box.querySelector('.kb-name'); if (n && sec.isConnected) try { n.focus({ preventScroll: true }); } catch (e) { n.focus(); } }, 0);
    }

    // --- Formular (Bearbeiten / Neu) --------------------------------------------------------------------------------
    function field(label, input, hint) {
      var l = el('label', 'hk-field'); l.appendChild(el('span', null, label)); l.appendChild(input);
      if (hint) { var h = el('small', 'hk-hint kb-fhint', hint); h.id = 'kb-h-' + (++fseq); input.setAttribute('aria-describedby', h.id); l.appendChild(h); }
      return l;
    }
    var fseq = 0;
    function addrList(kind, items, cc) {
      var tel = kind === 'e164';
      var fs = el('fieldset', 'kb-fs kb-fs-' + (tel ? 'tel' : 'mail'));
      fs.appendChild(el('legend', null, tel ? T('Telefonnummern') : T('E-Mail-Adressen')));
      var ul = el('ul', 'kb-addrs'); fs.appendChild(ul);
      var more = el('button', 'linkbtn kb-add-' + (tel ? 'tel' : 'mail'), tel ? T('+ Nummer') : T('+ E-Mail-Adresse')); more.type = 'button';
      fs.appendChild(more);
      function row(a) {
        var li = el('li', 'kb-addr'), n = ul.childNodes.length + 1;
        var inp = el('input', 'hk-in kb-addr-v'); inp.type = tel ? 'tel' : 'email'; inp.maxLength = tel ? 40 : 254;
        inp.value = a ? (tel ? showNum(a.value) : a.value) : ''; inp.autocomplete = 'off';
        inp.placeholder = tel ? (cc() ? T('z. B. 0170 1234567') : T('z. B. +49 170 1234567')) : T('name@beispiel.de');
        inp.setAttribute('aria-label', (tel ? T('Telefonnummer {n}', { n: n }) : T('E-Mail-Adresse {n}', { n: n })));
        var sel = el('select', 'hk-in kb-addr-t'); sel.setAttribute('aria-label', tel ? T('Art der Nummer {n}', { n: n }) : T('Art der Adresse {n}', { n: n }));
        var o0 = el('option', null, '—'); o0.value = ''; sel.appendChild(o0);
        TYPES.forEach(function (t) { var o = el('option', null, t[1]); o.value = t[0]; sel.appendChild(o); });
        sel.value = a ? (a.type || '') : (tel ? 'mobile' : '');
        var rm = el('button', 'icon-btn kb-addr-rm'); rm.type = 'button'; rm.setAttribute('aria-label', T('Entfernen'));
        rm.appendChild(ic('x', 'mi-ic', 16));
        rm.addEventListener('click', function () {
          var next = li.nextSibling || li.previousSibling; li.remove(); renum(); upd();
          var f = next && next.querySelector('.kb-addr-v'); (f || more).focus();
        });
        li.appendChild(inp); li.appendChild(sel); li.appendChild(rm); ul.appendChild(li);
        return inp;
      }
      function renum() {
        [].forEach.call(ul.childNodes, function (li, i) {
          li.querySelector('.kb-addr-v').setAttribute('aria-label', tel ? T('Telefonnummer {n}', { n: i + 1 }) : T('E-Mail-Adresse {n}', { n: i + 1 }));
          li.querySelector('.kb-addr-t').setAttribute('aria-label', tel ? T('Art der Nummer {n}', { n: i + 1 }) : T('Art der Adresse {n}', { n: i + 1 }));
        });
      }
      function upd() { more.hidden = ul.childNodes.length >= KIND_MAX; }
      items.forEach(row); upd();
      more.addEventListener('click', function () { row(null).focus(); upd(); });
      return { el: fs, values: function () {
        return [].map.call(ul.childNodes, function (li) {
          return { kind: kind, value: li.querySelector('.kb-addr-v').value.trim(), type: li.querySelector('.kb-addr-t').value || null };
        }).filter(function (a) { return a.value; });
      }, set: function (list) { ul.textContent = ''; list.forEach(row); upd(); } };
    }
    function showForm(c) {
      var isNew = !c, base = c;
      S.view = 'form'; S.dirty = false; box.textContent = '';
      box.appendChild(backBtn(isNew ? T('Alle Kontakte') : T('Zurück zum Kontakt'), cancel));
      var h = el('h4', 'kb-name kb-form-h', isNew ? T('Neuer Kontakt') : T('{name} bearbeiten', { name: c.name })); h.tabIndex = -1;
      box.appendChild(h);
      var f = el('form', 'kb-form'); f.noValidate = true; box.appendChild(f);
      if (c && c.saga) f.appendChild(el('p', 'hk-hint kb-fixed', T('Die Saga-Adresse stammt aus einem gemeinsamen Chat und lässt sich nicht ändern.')));
      var nm = el('input', 'hk-in kb-own'); nm.type = 'text'; nm.maxLength = 120; nm.value = c ? (c.own_name || '') : '';
      nm.autocomplete = 'off';
      if (c && c.saga && !c.own_name) nm.placeholder = c.name || '';
      f.appendChild(field(c && c.saga ? T('Name für mich') : T('Name'), nm,
        c && c.saga ? T('Nur in deinem Adressbuch – in Chats steht weiter der Name, den die Person selbst gewählt hat.') : null));
      var fm = el('select', 'hk-in kb-form-sel');
      [['', '—'], ['informal', T('du##Anrede')], ['formal', T('Sie##Anrede')]].forEach(function (x) { var o = el('option', null, x[1]); o.value = x[0]; fm.appendChild(o); });
      fm.value = c ? (c.form || '') : '';
      f.appendChild(field(T('Anrede'), fm));
      var sal = el('input', 'hk-in kb-sal'); sal.type = 'text'; sal.maxLength = 80; sal.value = c ? (c.salutation || '') : '';
      sal.placeholder = T('z. B. Liebe Anna'); sal.autocomplete = 'off';
      f.appendChild(field(T('Grußform'), sal));
      var ccv = function () { return S.cc; };
      var tl = c ? c.addrs.filter(function (a) { return a.kind === 'e164'; }) : [];
      var tels = addrList('e164', tl.length ? tl : [null], ccv);                 // ohne Nummer: ein leeres Feld
      var mails = addrList('email', c ? c.addrs.filter(function (a) { return a.kind === 'email'; }) : [], ccv);
      f.appendChild(tels.el); f.appendChild(mails.el);
      if (S.cc === undefined) api('GET', '/api/contacts/settings').then(function (r) { S.cc = r.cc || null; }).catch(function () {});
      var note = el('div', 'kb-note'); note.setAttribute('role', 'status'); f.appendChild(note);
      var bar = el('div', 'hk-actions'), sv = el('button', 'btn btn-primary kb-save', isNew ? T('Anlegen') : T('Speichern')), cc = el('button', 'btn kb-cancel', T('Abbrechen'));
      sv.type = 'submit'; cc.type = 'button'; bar.appendChild(sv); bar.appendChild(cc); f.appendChild(bar);
      var st = el('p', 'hk-status kb-err'); st.setAttribute('role', 'alert'); f.appendChild(st);
      f.addEventListener('input', function () { S.dirty = true; });
      f.addEventListener('change', function () { S.dirty = true; });
      cc.addEventListener('click', cancel);
      function cancel() { if (isNew) showList(true); else drawDetail(base); }
      function noteText(text, jump) {
        note.textContent = ''; note.className = 'kb-note' + (text ? ' on' : '');
        if (!text) return;
        note.appendChild(el('span', null, text));
        if (jump) {
          var a = el('button', 'linkbtn kb-jump', T('Zum Kontakt')); a.type = 'button';
          a.addEventListener('click', function () { showDetail(jump, false); }); note.appendChild(document.createTextNode(' ')); note.appendChild(a);
        }
      }
      f.addEventListener('submit', function (e) {
        e.preventDefault();
        say(st, ''); noteText('');
        var own = nm.value.trim();
        var addrs = tels.values().concat(mails.values());
        if ((isNew || !base.saga) && !own) { say(st, T('Bitte einen Namen eingeben.')); nm.focus(); return; }
        if ((isNew || !base.saga) && !addrs.length) { say(st, T('Bitte mindestens eine Telefonnummer oder E-Mail-Adresse eingeben.')); var fi = f.querySelector('.kb-addr-v'); (fi || nm).focus(); return; }
        var body = { own_name: own || null, form: fm.value || null, salutation: sal.value.trim() || null, addrs: addrs };
        if (!isNew) body.ver = base.ver;
        sv.disabled = true;
        (isNew ? api('POST', '/api/contacts', body) : api('PATCH', '/api/contacts/' + base.id, body)).then(function (r) {
          S.list = null; S.dirty = false;
          HK.toast(isNew ? T('Kontakt angelegt.') : T('Kontakt gespeichert.'));
          drawDetail(r);
        }).catch(function (err) {
          sv.disabled = false;
          var d = err.data || {};
          if (err.status === 409 && d.contact && !isNew) {
            // anderswo geaendert: neuen Stand uebernehmen, Eingabe bleibt stehen - erneut "Speichern" schreibt sie
            base = d.contact; S.cur = d.contact;
            noteText(T('Dieser Kontakt wurde inzwischen anderswo geändert. Der neue Stand ist geladen, deine Eingaben stehen noch hier – prüfe sie und speichere erneut.'));
            return;
          }
          if (err.status === 409 && d.contact_id) {
            noteText(err.message || T('Diese Angabe steht schon bei einem anderen Kontakt.'), d.contact_id !== (base && base.id) ? d.contact_id : null);
            return;
          }
          if (err.status === 404) { say(st, T('Kontakt nicht gefunden.')); return; }
          say(st, err.message);
        });
      });
      setTimeout(function () { try { (isNew ? nm : h).focus({ preventScroll: true }); } catch (e2) { (isNew ? nm : h).focus(); } }, 0);
    }

    // --- Live (contacts.changed {id, deleted?}) -----------------------------------------------------------------------
    var liveT = null;
    function changed(d) {
      if (!sec.isConnected) return false;
      clearTimeout(liveT);
      liveT = setTimeout(function () {
        if (!sec.isConnected) return;
        S.list = null;
        if (S.view === 'list') loadList();
        else if (S.view === 'detail' && S.cur && d && (d.resync || d.id === S.cur.id)) {
          if (d.deleted) { HK.toast(T('Dieser Kontakt wurde gelöscht.')); showList(true); }
          else api('GET', '/api/contacts/' + S.cur.id).then(function (c) { if (S.view === 'detail' && S.cur && S.cur.id === c.id) drawDetail(c); })
            .catch(function (e) { if (e.status === 404 && S.view === 'detail') showList(true); });
        }
        // Formular: nichts ueberschreiben - veraltetes ver meldet der Server beim Speichern (409, neuer Stand)
      }, 250);
      return true;
    }

    // Start: Ziel aus HKContacts.open() oder die Liste
    var p = pending; pending = null;
    if (p && p.id) showDetail(p.id, !!p.edit); else showList(false);
  }

  document.addEventListener('hk-live', function (e) {
    var t = e.detail && e.detail.type, d = (e.detail && e.detail.data) || {};
    // Neuaufbau des Live-Kanals (live.hello nach Unterbrechung) bzw. resync: Ereignisse koennen gefehlt haben -> neu laden
    if (t === 'live.hello' && !d.first || t === 'resync') d = { resync: true };
    else if (t !== 'contacts.changed') return;
    live = live.filter(function (x) { return x.changed(d); });
  });

  // Kontakt direkt oeffnen (z. B. aus dem Chat): Einstellungen > Kontakte, Detail bzw. Formular
  function open(id, opts) {
    if (!window.HKSettings) return;
    pending = { id: +id || 0, edit: !!(opts && opts.edit) };
    HKSettings.open('contacts');
    pending = null;
  }

  window.HKContacts = { section: section, open: open, showNum: showNum };
})();
