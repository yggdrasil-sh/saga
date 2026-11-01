/* Daten-Werkzeuge im Tiptap-Editor (2.5, Tiptap-Umbau T4b): Datenbank-Ansicht, Aufgaben, Kalender, Widget,
 * Schaltflaechen - ueber die Bruecke HKTools.fromEditorJs (static/pm-tools.js) mit den unveraenderten Klassen aus
 * static/pages.js (HKPages.k.tools). Laden nach pages.js und pm-tools.js; vorher geladen, holt HKTools.setup die
 * Registrierung beim ersten HKTools.nodeViews()/get() nach.
 *
 * Was die Bruecke hier umbiegt:
 *   - Schreibstellen: DatabaseTool.mark / ButtonsTool.changed (heute S.dirtyBody + schedule) und block.dispatchChange
 *     (Widget, Kalender) -> genau eine Transaktion ueber ctx.change(neue Daten)
 *   - isolate() bleibt im Werkzeug (stopPropagation), zusaetzlich stopEvent der Node-View: Tasten/Ziehen/Einfuegen
 *     erreichen ProseMirror nicht
 *   - Regel 0: Felder, die save() nicht kennt, bleiben erhalten (known); Widget behaelt sie selbst
 *   - Schaltflaechen: Signaturen (databases.sign_buttons: sig) und unbekannte Felder je Schaltflaeche bleiben stehen,
 *     solange Aktion/Ziel/Vorlage gleich sind - der Server prueft und setzt sie beim Speichern wie bisher
 *   - Rollen, fremde Seiten, "Nur fuer den Eigentuemer" (Aufgaben/Kalender): wie heute aus dem Seitenzustand in pages.js;
 *     ctx.readOnly sperrt zusaetzlich das Schreiben ins Dokument
 *   - update(data) (Live/Undo/Rebase): Widget spielt neue Daten ein und laesst ein offenes Panel offen; die anderen
 *     bauen neu auf, verschoben solange ein Eingabefeld im Werkzeug, ein Menue oder ein Dialog offen ist
 */
(function (root) {
  'use strict';
  function isDict(v) { return !!v && typeof v === 'object' && !Array.isArray(v); }
  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }

  function setup() {
    var H = root.HKTools, k = root.HKPages && root.HKPages.k, W = k && k.tools;
    if (!H || !W || !W.DatabaseTool) return false;

    H.register('database', H.fromEditorJs(W.DatabaseTool, {
      known: ['db', 'view', 'views', 'hide_title', 'hide_tabs'],
      hook: function (inst, notify) { inst.mark = function () { notify(); }; },
      // Alte Einbettung ohne eigene Ansichten: makeDbView legt sie nach dem Laden einmal im Block ab (wie in Editor.js) -
      // das ist kein Bedienschritt, also kein Undo-Schritt
      meta: function (base, out) {
        if (base.views && base.views.length) return undefined;
        var same = ['db', 'view', 'hide_title', 'hide_tabs'].every(function (k) { return JSON.stringify(base[k] || null) === JSON.stringify(out[k] || null); });
        return same ? { history: false } : undefined;
      }
    }));

    H.register('tasks', H.fromEditorJs(W.TasksTool, {
      known: [],
      live: function () { return true; }                 // Inhalt kommt vom Server (Aufgaben des Betrachters), data ist leer
    }));

    H.register('calendar', H.fromEditorJs(W.CalendarTool, {
      known: ['view', 'days'],
      live: function (inst, d) {
        inst.view = d.view === 'list' ? 'list' : 'month';
        inst.days = Math.max(1, Math.min(62, +d.days || 14));
        inst.draw();
        return true;
      }
    }));

    H.register('buttons', H.fromEditorJs(W.ButtonsTool, {
      known: ['buttons'],
      hook: function (inst, notify) { inst.changed = function () { inst.draw(); notify(); }; },
      carry: function (base, out) {
        var old = Array.isArray(base.buttons) ? base.buttons : [];
        (out.buttons || []).forEach(function (b, i) {
          var o = old[i];
          if (!isDict(o) || !isDict(b)) return;
          if ((o.action || '') !== (b.action || '') || (o.target || null) !== (b.target || null) || (o.tpl || null) !== (b.tpl || null)) return;
          Object.keys(o).forEach(function (key) { if (!(key in b)) b[key] = clone(o[key]); });
        });
        return out;
      }
    }));

    H.register('widget', H.fromEditorJs(W.WidgetTool, {
      known: null,                                        // WidgetTool haelt unbekannte Felder selbst (Regel 0)
      live: function (inst, d) {
        var nd = clone(d);
        if (!nd.kind) nd.kind = 'metric';
        if (W.wgKinds.indexOf(nd.kind) >= 0) { nd.v = 1; nd.cfg = Object.assign(W.wgDefaults(nd.kind), isDict(nd.cfg) ? nd.cfg : {}); }
        inst.data = nd;
        if (inst.panel) {                                 // Panel bleibt offen: Felder neu fuellen, Fokus an dieselbe Stelle
          var fields = [].slice.call(inst.panel.querySelectorAll('input,select,textarea'));
          var at = fields.indexOf(document.activeElement);
          inst.fill();
          if (at >= 0) { var f2 = inst.panel.querySelectorAll('input,select,textarea')[at]; if (f2) try { f2.focus(); } catch (e) { /* egal */ } }
          inst.update();                                  // Vorschau ueber den Server wie beim eigenen Einrichten
        } else {
          inst.pv = null; inst.draw();
          W.wgLoad();                                     // gespeicherte Werte neu holen (die Aenderung kam vom Server)
        }
        return true;
      },
      destroy: function (inst) { clearTimeout(inst.pvT); }
    }));
    return true;
  }

  if (root.HKTools) root.HKTools.setup(setup);
  else if (root.console) console.warn('pm-tools-daten.js: pm-tools.js zuerst laden');
})(typeof self !== 'undefined' ? self : this);
