// Saga - Tastatur fuer Seitenbaeume (WAI-ARIA Tree-Muster, Design-Runde A11Y-11): EIN Tab-Stopp je Baum (wandernder
// tabindex), Pfeiltasten, Home/End, Enter/Leertaste oeffnet, Buchstabe springt. Gilt fuer /notes (pages.js: eigener
// Baum, "Mit mir geteilt", Favoriten) und die globale Seitenleiste (side.js).
// Erwartet: <ul role=tree> mit <li role=treeitem data-id> (aufklappbare Knoten tragen aria-expanded), darin eine Zeile
// mit [data-tn-go] (oeffnen) und - bei Knoten mit Kindern - .pg-twisty (auf-/zuklappen, baut den Ast selbst neu).
// HK.treeNav(ul)             einmal je Baum: Tasten und Fokus verdrahten
// HK.treeNav.item(li, o)     Attribute einer Zeile: {level, pos, size, expanded (null = Blatt), current, name, desc}
// HK.treeNav.keep(ul, build) Baum neu aufbauen; lag der Fokus darin, landet er danach auf derselben Seite
(function () {
  'use strict';
  var HK = window.HK; if (!HK) return;
  function items(ul) {                                   // sichtbare Zeilen in Lesereihenfolge (zugeklappte Aeste fehlen im DOM)
    return [].filter.call(ul.querySelectorAll('[role="treeitem"]'), function (li) { return li.getClientRects().length > 0; });
  }
  function itemOf(ul, node) { var li = node && node.closest && node.closest('[role="treeitem"]'); return li && ul.contains(li) ? li : null; }
  function byId(ul, id) {
    if (id == null) return null;
    var all = ul.querySelectorAll('[role="treeitem"][data-id]');
    for (var i = 0; i < all.length; i++) if (all[i].dataset.id === String(id)) return all[i];
    return null;
  }
  function setActive(ul, li) {
    if (!li) return;
    [].forEach.call(ul.querySelectorAll('[role="treeitem"]'), function (x) { x.tabIndex = x === li ? 0 : -1; });
    if (li.dataset.id) ul._tnActive = li.dataset.id;
  }
  function focusItem(ul, li) {
    if (!li) return;
    setActive(ul, li); li.focus({ preventScroll: true });
    if (li.scrollIntoView) li.firstElementChild ? li.firstElementChild.scrollIntoView({ block: 'nearest' }) : li.scrollIntoView({ block: 'nearest' });
  }
  // genau eine Zeile mit tabindex 0: zuletzt aktive, sonst die offene Seite, sonst die erste
  function refresh(ul, prefer) {
    var li = byId(ul, prefer) || byId(ul, ul._tnActive) || ul.querySelector('[role="treeitem"][aria-current="page"]') || items(ul)[0] || ul.querySelector('[role="treeitem"]');
    if (li) setActive(ul, li);
    // Bedienelemente in den Zeilen (Twisty, "+", ...) sind keine eigenen Tab-Stopps
    [].forEach.call(ul.querySelectorAll('button, a[href], [tabindex]:not([role="treeitem"])'), function (b) { b.tabIndex = -1; });
  }
  function typeAhead(ul, li, ch) {
    var list = items(ul), i = list.indexOf(li), n = list.length; ch = ch.toLowerCase();
    for (var k = 1; k <= n; k++) {
      var x = list[(i + k) % n], name = (x.getAttribute('aria-label') || x.textContent || '').trim().toLowerCase();
      if (name.charAt(0) === ch) return x;
    }
    return null;
  }
  function onKey(ul, e) {
    var li = itemOf(ul, e.target); if (!li || e.ctrlKey || e.metaKey || e.altKey) return;
    var onItem = e.target === li, list, i, ex = li.getAttribute('aria-expanded'), tw;
    switch (e.key) {
      case 'ArrowDown': case 'ArrowUp':
        list = items(ul); i = list.indexOf(li);
        var to = list[e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1)];
        if (to) focusItem(ul, to); break;
      case 'Home': list = items(ul); if (list[0]) focusItem(ul, list[0]); break;
      case 'End': list = items(ul); if (list.length) focusItem(ul, list[list.length - 1]); break;
      case 'ArrowRight':
        if (ex === 'false') { tw = li.querySelector('.pg-twisty'); if (tw) tw.click(); focusItem(ul, li); }
        else if (ex === 'true') { var first = li.querySelector('[role="group"] [role="treeitem"]'); if (first) focusItem(ul, first); }
        break;
      case 'ArrowLeft':
        if (ex === 'true') { tw = li.querySelector('.pg-twisty'); if (tw) tw.click(); focusItem(ul, li); }
        else { var up = li.parentElement && li.parentElement.closest('[role="treeitem"]'); if (up && ul.contains(up)) focusItem(ul, up); }
        break;
      case 'Enter': case ' ':
        if (!onItem && e.target.matches('button, a, input')) return;   // Twisty/"+" mit der Maus fokussiert: wie gewohnt
        var go = li.querySelector('[data-tn-go]'); if (go) go.click(); break;
      default:
        if (e.key && e.key.length === 1 && /\S/.test(e.key)) {
          var hit = typeAhead(ul, li, e.key); if (!hit) return; focusItem(ul, hit); break;
        }
        return;
    }
    e.preventDefault(); e.stopPropagation();
  }
  function treeNav(ul) {
    if (!ul || ul._tn) return; ul._tn = true;
    ul.addEventListener('keydown', function (e) { onKey(ul, e); });
    ul.addEventListener('focusin', function (e) { var li = itemOf(ul, e.target); if (li) setActive(ul, li); });
    refresh(ul);
  }
  function item(li, o) {
    li.setAttribute('role', 'treeitem'); li.tabIndex = -1;
    if (o.id != null) li.dataset.id = String(o.id);
    if (o.level) li.setAttribute('aria-level', String(o.level));
    if (o.size) { li.setAttribute('aria-setsize', String(o.size)); li.setAttribute('aria-posinset', String(o.pos || 1)); }
    if (o.expanded == null) li.removeAttribute('aria-expanded'); else li.setAttribute('aria-expanded', o.expanded ? 'true' : 'false');
    if (o.current) li.setAttribute('aria-current', 'page'); else li.removeAttribute('aria-current');
    if (o.name != null) li.setAttribute('aria-label', o.name);
    if (o.desc) li.setAttribute('aria-description', o.desc); else li.removeAttribute('aria-description');
    return li;
  }
  function keep(ul, build) {
    var a = document.activeElement, had = !!(ul && a && ul.contains(a)), li = had ? itemOf(ul, a) : null, id = li && li.dataset.id;
    build();
    if (!ul) return;
    treeNav(ul); refresh(ul, id);
    if (had && !ul.contains(document.activeElement)) {
      var t = byId(ul, id) || ul.querySelector('[role="treeitem"][tabindex="0"]');
      if (t) t.focus({ preventScroll: true });
    }
  }
  treeNav.item = item; treeNav.keep = keep; treeNav.refresh = refresh; treeNav.focus = focusItem; treeNav.items = items;
  HK.treeNav = treeNav;
})();
