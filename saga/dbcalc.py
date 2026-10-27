"""Gemeinsamer Rechenkern der Datenbanken (2.5, Paket 1): Aggregation, Formeln, Rollups, Gruppen nach Werten.

- aggregate(): Kennzahl ueber Zeilen wie die Fusszeile der Tabelle im Web (static/pages.js calcNum) - Widgets
  (saga.page_widgets) und kuenftig die Diagramm-Ansicht rechnen nur hierueber.
- reduce(): alle Fusszeilen-Rechnungen (CALC_OPS) als Zahl/Datum/Liste - Grundlage der Rollups.
- fill(): Werte aller Eigenschaften "formula" und "rollup" in rows[i]["props"] eintragen (nur im Speicher, nie
  gespeichert), Fehler je Zelle in rows[i]["fx_err"] = {pid: code}. Aufruf an jeder Lesestelle VOR dbquery.run, damit
  Filter/Sortierung/Gruppen darauf wirken. Rollups sehen nur verknuepfte Zeilen, die der Betrachter sehen darf
  (Eigentuemer: eigene, nicht archivierte; sonst sharing.Access.tree) - sonst verriete ein Rollup Inhalte einer nicht
  geteilten Ziel-Datenbank.
- Zeitbudget je Anfrage FILL_BUDGET Sekunden: danach bleiben die restlichen Werte leer (fx_err "time").
- legacy_schema()/legacy_rows(): Darstellung fuer Gegenstellen ohne Faehigkeit formula1 (aeltere Saga-Instanzen):
  Formel/Rollup als nur-lesbare Text-Spalte, Original unter "fx_orig" (kommt beim Zurueckschreiben unveraendert
  zurueck und wird in notes._clean_schema wiederhergestellt)."""
import math
import re
import time
from datetime import date, datetime, timedelta, timezone

from saga import dbquery, formula as fx
from saga.i18n import get_lang, t

FILL_BUDGET = 2.0
AGGS = ("count", "sum", "avg", "min", "max", "pct_checked")


class Fail(Exception):
    """Lässt sich nicht rechnen (Widgets: error-Code wie page_widgets.ERRORS)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _round2(x):
    return dbquery.js_round(x * 100) / 100


def _n(v):
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def _vals(q, p, rows):
    out = []
    for r in rows:
        v = q.prop_value(p, r) if p is not None else r.get("title")
        if p is not None and isinstance(p.get("type"), str) and p["type"] in dbquery.AUTO and dbquery.js_truthy(v):
            v = q.iso_day(v)
        out.append(v)
    return out


def _nums(vals):
    nums = []
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            nums.append(float(v))
        elif isinstance(v, str) and dbquery.js_trim(v) != "":
            x = dbquery.js_num(v)
            if x == x:
                nums.append(x)
    return nums


def aggregate(op, pid, rows, schema):
    """op aus AGGS (+ 'checked'), pid: Eigenschaft-ID ('' / 'title' = Titel). rows: [{'title','props','created','updated'}].
    -> Zahl oder None ('–' in der Fusszeile). Unbekannte Eigenschaft: Fail('bad_cfg')."""
    q = dbquery.Query(schema)
    p = None
    if pid and pid != "title":
        p = q.prop_by_id(pid)
        if p is dbquery.UNDEF or not isinstance(p, dict):
            raise Fail("bad_cfg")
    vals = _vals(q, p, rows)
    n = len(vals)
    nums = _nums(vals)
    if op == "count":
        return n
    if op == "checked":
        return sum(1 for v in vals if v is True)
    if op == "pct_checked":
        return int(dbquery.js_round(sum(1 for v in vals if v is True) / n * 100)) if n else None
    if not nums:
        return None
    s = 0.0
    for x in nums:                                 # wie reduce in JavaScript: der Reihe nach
        s += x
    if op == "sum":
        r = _round2(s)
    elif op == "avg":
        r = _round2(s / len(nums))
    elif op == "min":
        r = _round2(min(nums))
    elif op == "max":
        r = _round2(max(nums))
    else:
        raise Fail("bad_cfg")
    if math.isinf(r) or r != r:
        return None
    return _n(r)


def _empty(v):
    return v is None or v is dbquery.UNDEF or v == "" or v is False or (isinstance(v, list) and not v)


def reduce(op, vals, show=None):
    """Fusszeilen-Rechnung (CALC_OPS + 'show') ueber fertige Werte -> Zahl | 'JJJJ-MM-TT' | Liste | None.
    show(v) -> [texte] fuer 'show' (Original anzeigen)."""
    n = len(vals)
    filled = [v for v in vals if not _empty(v)]
    if op == "count":
        return n
    if op == "filled":
        return len(filled)
    if op == "empty":
        return n - len(filled)
    if op == "pct_filled":
        return int(dbquery.js_round(len(filled) / n * 100)) if n else None
    if op == "unique":
        u = set()
        for v in filled:
            for x in (v if isinstance(v, list) else [v]):
                u.add(dbquery.js_str(x))
        return len(u)
    if op == "checked":
        return sum(1 for v in vals if v is True)
    if op == "unchecked":
        return sum(1 for v in vals if v is not True)
    if op == "pct_checked":
        return int(dbquery.js_round(sum(1 for v in vals if v is True) / n * 100)) if n else None
    if op in ("earliest", "latest"):
        days = sorted(v[:10] for v in filled if isinstance(v, str) and len(v) >= 10 and v[4:5] == "-" and v[:4].isdigit()
                      and v[5:7].isdigit() and v[7:8] == "-" and v[8:10].isdigit())
        return (days[0] if op == "earliest" else days[-1]) if days else None
    if op == "show":
        out = []
        for v in vals:
            out.extend(show(v) if show else ([] if _empty(v) else [dbquery.js_str(v)]))
            if len(out) >= fx.LIST_MAX:
                break
        return out[:fx.LIST_MAX]
    nums = _nums(vals)
    if not nums:
        return None
    s = 0.0
    for x in nums:
        s += x
    if op == "sum":
        r = s
    elif op == "avg":
        r = s / len(nums)
    elif op == "median":
        xs = sorted(nums)
        m = len(xs) // 2
        r = xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2
    elif op == "min":
        r = min(nums)
    elif op == "max":
        r = max(nums)
    elif op == "range":
        r = max(nums) - min(nums)
    else:
        return None
    r = _round2(r)
    return None if math.isinf(r) or r != r else _n(r)


# =====================================================================================================
# Formeln und Rollups fuellen
# =====================================================================================================
def has_calc(schema):
    return isinstance(schema, dict) and any(isinstance(p, dict) and p.get("type") in ("formula", "rollup")
                                            for p in (schema.get("props") or []))


def _props_of(schema):
    return [p for p in (schema.get("props") or []) if isinstance(p, dict)] if isinstance(schema, dict) else []


def _num_in(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        return None if x != x or math.isinf(x) else x
    if isinstance(v, str) and v.strip():
        x = dbquery.js_num(v)
        return None if x != x or math.isinf(x) else x
    return None


class _Source:
    """Was fill() von aussen braucht: Titel verknuepfter Seiten und Zeilen einer Ziel-Datenbank (je Anfrage gemerkt)."""

    def __init__(self, labels=None, target=None, people=None):
        self._labels_fn, self._target_fn, self._people_fn = labels, target, people
        self._labels, self._targets, self._people = {}, {}, {}

    def people(self, canons):
        """Personen -> Anzeigenamen (nie der Anmeldename); ohne Quelle (oeffentliche Seiten): keine."""
        need = [x for x in canons if x not in self._people]
        if need:
            got = self._people_fn(need) if self._people_fn else {}
            for x in need:
                self._people[x] = got.get(x)
        return [self._people[x] for x in canons if self._people.get(x)]

    def labels(self, ids):
        need = [i for i in ids if i not in self._labels]
        if need:
            got = self._labels_fn(need) if self._labels_fn else {}
            for i in need:
                self._labels[i] = got.get(i)
        return {i: self._labels[i] for i in ids if self._labels.get(i) is not None}

    def target(self, dbid):
        if dbid not in self._targets:
            self._targets[dbid] = self._target_fn(dbid) if self._target_fn else None
        return self._targets[dbid]


def _in_value(p, r, src, calc_q):
    """Gespeicherter Wert -> Formel-Wert (Zahl/Text/bool/FDate/Liste/None)."""
    typ = p.get("type")
    pv = r.get("props") if isinstance(r.get("props"), dict) else {}
    v = pv.get(p.get("id"))
    tz = src.tz
    if typ in ("text", "url"):
        return v if isinstance(v, str) else (None if v is None else dbquery.js_str(v))
    if typ == "number":
        return _num_in(v)
    if typ == "calc":
        x = calc_q.calc_value(p, r)
        return None if x is None else float(x)
    if typ == "checkbox":
        return v is True
    if typ == "date":
        return fx.prop_date(v, tz) if isinstance(v, str) else None
    if typ == "created":
        return fx.stamp_of(r.get("created"), tz)
    if typ == "edited":
        return fx.stamp_of(r.get("updated"), tz)
    if typ == "select":
        o = next((o for o in (p.get("options") or []) if isinstance(o, dict) and o.get("id") == v), None)
        return o.get("name") if o else None
    if typ == "multi_select":
        names = {o.get("id"): o.get("name") for o in (p.get("options") or []) if isinstance(o, dict)}
        return [names[x] for x in (v if isinstance(v, list) else []) if isinstance(x, str) and names.get(x) is not None]
    if typ == "relation":
        ids = [int(x) for x in (v if isinstance(v, list) else []) if isinstance(x, int) and not isinstance(x, bool)]
        lab = src.labels(ids)
        return [(lab[i] or src.untitled) for i in ids if i in lab]
    if typ == "person":                                  # 2.5: Anzeigenamen (nie Anmeldenamen)
        return src.people([x for x in (v if isinstance(v, list) else []) if isinstance(x, str)])
    if typ == "files":
        return [str(x.get("name") or "") for x in (v if isinstance(v, list) else []) if isinstance(x, dict) and x.get("name")]
    return None


def _rollup_in(rtype, out, tz):
    """Ausgegebener Wert eines Rollups (props) -> Formel-Wert (Zahl, Tag, Liste von Texten)."""
    if out is None:
        return None
    if rtype == "number":
        return float(out) if isinstance(out, (int, float)) and not isinstance(out, bool) else None
    if rtype == "date":
        return fx.day_of(out, tz) if isinstance(out, str) else None
    if rtype == "list":
        return list(out) if isinstance(out, list) else None
    return out


class _Fill:
    def __init__(self, schema, src, tz, now, lang, rollups, deadline):
        self.schema, self.src, self.tz, self.now, self.lang = schema, src, tz, now, lang
        self.rollups, self.deadline = rollups, deadline
        self.props = _props_of(schema)
        self.by_id = {p.get("id"): p for p in self.props}
        self.plan = fx.plan_of(schema)
        self.q = dbquery.Query(schema)

    def needed(self):
        need = set()
        for cm in self.plan.formulas.values():
            need |= cm.refs
        return need

    def run(self, rows):
        src = self.src
        src.tz = self.tz
        rolls = [p for p in self.props if p.get("type") == "rollup"]
        fxs = [p for p in self.props if p.get("type") == "formula"]
        need = self.needed()
        rel_need = [p for p in self.props if p.get("id") in need and p.get("type") == "relation"]
        if rel_need:                                   # Titel aller verknuepften Seiten mit EINER Abfrage
            ids = set()
            for r in rows:
                pv = r.get("props") if isinstance(r.get("props"), dict) else {}
                for p in rel_need:
                    v = pv.get(p["id"])
                    if isinstance(v, list):
                        ids.update(x for x in v if isinstance(x, int) and not isinstance(x, bool))
            src.labels(sorted(ids))
        roll_ctx = {}
        if rolls:
            for p in rolls:
                roll_ctx[p["id"]] = self._rollup_ctx(p) if self.rollups else None
        timed_out = False
        for r in rows:
            pv = r.get("props")
            if not isinstance(pv, dict):
                pv = r["props"] = {}
            errs = {}
            if not timed_out and time.monotonic() > self.deadline:
                timed_out = True
            if timed_out:
                for p in rolls + fxs:
                    pv.pop(p["id"], None)
                    errs[p["id"]] = "time"
                r["fx_err"] = errs
                continue
            vals = {}
            for p in rolls:
                pv.pop(p["id"], None)
                ctx = roll_ctx.get(p["id"])
                if ctx is None:
                    continue
                try:
                    out = self._rollup(p, ctx, pv)
                except fx.FxError as ex:
                    errs[p["id"]] = ex.code
                    continue
                if out is not None:
                    pv[p["id"]] = out
            for pid in need:
                if pid == "title":
                    vals["title"] = r.get("title") if isinstance(r.get("title"), str) else ""
                    continue
                p = self.by_id.get(pid)
                if p is None or p.get("type") == "formula":
                    continue
                if p.get("type") == "rollup":
                    vals[pid] = _rollup_in(self.plan.rtype.get(pid), pv.get(pid), self.tz)
                    continue
                vals[pid] = _in_value(p, r, src, self.q)
            ctx = fx.Ctx(vals, self.tz, self.now, self.lang, r.get("id"))
            for p in fxs:
                pid = p["id"]
                pv.pop(pid, None)
                err = self.plan.errors.get(pid)
                if err is not None:
                    errs[pid] = err.code
                    vals[pid] = None
                    continue
                cm = self.plan.formulas.get(pid)
                if cm is None:
                    continue
                try:
                    v = cm.fn(ctx)
                    out = fx.out_value(cm.rtype, v)
                except fx.FxError as ex:
                    errs[pid] = ex.code
                    vals[pid] = None
                    continue
                except (RecursionError, MemoryError, OverflowError, ValueError, TypeError, ZeroDivisionError):
                    errs[pid] = "num"
                    vals[pid] = None
                    continue
                vals[pid] = v
                if out is not None:
                    pv[pid] = out
            if errs:
                r["fx_err"] = errs
            else:
                r.pop("fx_err", None)

    # --- Rollups --------------------------------------------------------------------------------------------------
    def _rollup_ctx(self, p):
        rel = self.by_id.get(p.get("rel"))
        if not rel or rel.get("type") != "relation":
            return None
        got = self.src.target(rel.get("target"))
        if got is None:
            return None
        tschema, trows = got
        tgt = p.get("target") or "title"
        tp = None if tgt == "title" else next((x for x in _props_of(tschema) if x.get("id") == tgt), None)
        if tgt != "title" and (tp is None or tp.get("type") == "rollup"):
            return None
        if tp is not None and tp.get("type") == "formula" and not getattr(self.src, "_tfilled", {}).get(rel.get("target")):
            # Formeln der Ziel-Datenbank mitrechnen (ohne deren Rollups: keine Ketten ueber Datenbank-Grenzen)
            _Fill(tschema, self.src, self.tz, self.now, self.lang, False, self.deadline).run(trows)
            self.src._tfilled = dict(getattr(self.src, "_tfilled", {}), **{rel.get("target"): True})
        byid = {r["id"]: r for r in trows}
        return (rel, tp, tschema, byid)

    def _rollup(self, p, ctx, pv):
        rel, tp, tschema, byid = ctx
        ids = [x for x in (pv.get(rel["id"]) or []) if isinstance(x, int) and not isinstance(x, bool)] \
            if isinstance(pv.get(rel["id"]), list) else []
        linked = [byid[i] for i in ids if i in byid]
        tq = dbquery.Query(tschema)
        if tp is None:
            vals = [r.get("title") or "" for r in linked]
        else:
            vals = _vals(tq, tp, linked)
        fn = p.get("fn") if p.get("fn") in fx.ROLLUP_FNS else "count"

        def show(v):
            if _empty(v):
                return []
            if tp is not None and tp.get("type") in ("select", "multi_select"):
                names = {o.get("id"): o.get("name") for o in (tp.get("options") or []) if isinstance(o, dict)}
                return [names[x] for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and names.get(x)]
            if tp is not None and tp.get("type") == "relation" and isinstance(v, list):
                ids2 = [x for x in v if isinstance(x, int) and not isinstance(x, bool)]
                lab = self.src.labels(ids2)
                return [(lab[i] or self.src.untitled) for i in ids2 if i in lab]
            if tp is not None and tp.get("type") == "person":          # 2.5: Anzeigenamen, nie Anmeldenamen
                return self.src.people([x for x in (v if isinstance(v, list) else []) if isinstance(x, str)])
            if tp is not None and tp.get("type") == "files":
                return [str(x.get("name") or "") for x in (v if isinstance(v, list) else []) if isinstance(x, dict) and x.get("name")]
            if v is True:
                return ["✓"]
            if isinstance(v, list):
                return [dbquery.js_str(x) for x in v if not _empty(x)]
            if tp is None and v == "":
                return [self.src.untitled]
            return [dbquery.js_str(v)]
        if tp is None and fn == "show":
            return reduce(fn, [v or self.src.untitled for v in vals])
        return reduce(fn, vals, show)


def fill_core(schema, rows, tz=None, now=None, labels=None, target=None, rollups=True, untitled=None, deadline=None, lang=None,
              people=None):
    """Formeln/Rollups in rows eintragen - ohne Datenbank: labels(ids) -> {id: titel} (nur sichtbare Seiten),
    target(db_id) -> (schema, zeilen) der Ziel-Datenbank (nur sichtbare Zeilen) oder None. people(kennungen) ->
    {kennung: anzeigename} (2.5; ohne: Personen zaehlen als leer - z. B. oeffentliche Seiten)."""
    if not has_calc(schema) or not rows:
        return rows
    if tz is None:                                      # ohne Angabe der Anfrage: Zeitzone des Servers (TZ)
        from saga.core import TZ
        tz = TZ
    now = now or datetime.now(timezone.utc)
    src = _Source(labels, target, people)
    src.untitled = untitled or t("Unbenannt")
    _Fill(schema, src, tz, now, lang or get_lang(), rollups,
          deadline if deadline is not None else time.monotonic() + FILL_BUDGET).run(rows)
    return rows


def _visible(c, owner, viewer):
    """-> None (alles des Eigentuemers ausser Archiv) oder Menge der sichtbaren Seiten-IDs (Freigabe-Baum)."""
    if viewer == owner:
        return None
    from saga.sharing import Access
    return set(Access(c, viewer).tree(owner))


def db_source(c, owner, viewer):
    """labels/target fuer fill_core mit den Rechten von viewer (Eigentuemer oder Freigabe, auch foederiert)."""
    vis_cache = {}

    def vis():
        if "v" not in vis_cache:
            vis_cache["v"] = _visible(c, owner, viewer)
        return vis_cache["v"]

    def labels(ids):
        ids = [i for i in ids if 0 < i < 2 ** 63]
        if not ids:
            return {}
        v = vis()
        if v is None:
            got = c.execute("SELECT id, title FROM note WHERE id = ANY(?) AND uid=? AND deleted_at IS NULL AND archived IS NULL",
                            (list(ids), owner)).fetchall()
        else:
            ok = [i for i in ids if i in v]
            got = c.execute("SELECT id, title FROM note WHERE id = ANY(?)", (ok,)).fetchall() if ok else []
        return {i: tl or "" for i, tl in got}

    def target(dbid):
        if not isinstance(dbid, int) or isinstance(dbid, bool) or not 0 < dbid < 2 ** 63:
            return None
        v = vis()
        if v is not None and dbid not in v:
            return None                                   # Ziel-Datenbank nicht sichtbar: nichts verraten
        d = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (dbid, owner)).fetchone()
        if not d:
            return None
        from saga.core import _json_load
        rows = []
        for r in c.execute("SELECT id, title, props, updated, created FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL "
                           "AND is_template=0 AND archived IS NULL", (owner, dbid)).fetchall():
            if v is not None and r[0] not in v:
                continue
            rows.append({"id": r[0], "title": r[1] or "", "props": _json_load(r[2], {}), "updated": r[3], "created": r[4]})
        return _json_load(d[0], {"props": []}), rows
    return labels, target


def fill(c, owner, schema, rows, viewer=None, tz=None, now=None, rollups=True):
    """Formeln/Rollups einer Datenbank von owner fuer viewer (Standard: owner) in rows eintragen."""
    if not has_calc(schema) or not rows:
        return rows
    labels, target = db_source(c, owner, owner if viewer is None else viewer)
    return fill_core(schema, rows, tz=tz, now=now, labels=labels, target=target, rollups=rollups, people=people_source(c))


def people_source(c):
    """people(kennungen) -> {kennung: anzeigename} fuer fill_core (sharing.names: nie der Anmeldename)."""
    def people(canons):
        from saga.sharing import names
        return dict(names(c, list(canons)))
    return people


# Gruppen nach Formel-/Rollup-Werten: eine Logik mit dbquery (Query.groups -> dbquery.value_groups)
groupable = dbquery.groupable
value_groups = dbquery.value_groups


# =====================================================================================================
# Aeltere Gegenstellen (ohne formula1): Formel/Rollup als nur-lesbarer Text
# =====================================================================================================
_ORIG_KEYS = ("type", "expr", "rel", "target", "fn", "fmt", "show", "rtype")


def legacy_schema(schema):
    if not has_calc(schema):
        return schema
    out = []
    for p in schema.get("props") or []:
        if isinstance(p, dict) and p.get("type") in ("formula", "rollup"):
            q = {k: v for k, v in p.items() if k not in _ORIG_KEYS}
            q["type"] = "text"
            q["fx_orig"] = {k: p[k] for k in _ORIG_KEYS if k in p}
            p = q
        out.append(p)
    return dict(schema, props=out)


def legacy_value(v):
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "✓" if v else None
    if isinstance(v, (int, float)):
        return dbquery.js_num_str(v)
    if isinstance(v, list):
        return ", ".join(dbquery.js_str(x) for x in v) or None
    return None


def legacy_rows(schema, rows):
    pids = [p.get("id") for p in (schema.get("props") or []) if isinstance(p, dict) and p.get("type") in ("formula", "rollup")]
    if not pids:
        return rows
    for r in rows:
        pv = r.get("props")
        if isinstance(pv, dict):
            for pid in pids:
                if pid in pv:
                    s = legacy_value(pv[pid])
                    if s is None:
                        pv.pop(pid)
                    else:
                        pv[pid] = s
        r.pop("fx_err", None)
    return rows


# --- 2.5 (Paket 5): Abhaengigkeiten - "blockiert" ------------------------------------------------------------------
def done_test(schema):
    """-> f(props) -> bool | None: Ist eine Zeile erledigt? Status-Eigenschaft (Gruppe done), sonst das erste Haekchen.
    None = die Datenbank hat keins von beidem (dann gilt nichts als erledigt)."""
    props = _props_of(schema)
    st = next((p for p in props if dbquery.is_status(p)), None)
    if st is not None:
        return lambda pv: dbquery.status_group(st, pv.get(st["id"])) == "done"
    cb = next((p for p in props if p.get("type") == "checkbox"), None)
    if cb is not None:
        return lambda pv: pv.get(cb["id"]) is True
    return lambda pv: False


def mark_blocked(c, owner, dbid, schema, rows):
    """rows[i]["blocked"] = True, wenn ein Eintrag der Relation mit Rolle blocked_by (Selbst-Relation) nicht erledigt ist.
    Nur gelesen, nie gespeichert. Ohne solche Relation: nichts."""
    bp = next((p for p in _props_of(schema) if p.get("type") == "relation" and p.get("role") == "blocked_by"), None)
    if bp is None or not rows:
        return
    want = set()
    for r in rows:
        v = (r.get("props") or {}).get(bp["id"])
        if isinstance(v, list):
            want.update(x for x in v if isinstance(x, int) and not isinstance(x, bool))
    if not want:
        return
    import json as _json
    done = done_test(schema)
    state = {}
    for i, pr in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND id = ANY(?)",
                           (owner, dbid, list(want))).fetchall():
        try:
            pv = _json.loads(pr) if pr else {}
        except ValueError:
            pv = {}
        state[i] = done(pv if isinstance(pv, dict) else {})
    for r in rows:
        v = (r.get("props") or {}).get(bp["id"])
        if isinstance(v, list) and any(state.get(x) is False for x in v):
            r["blocked"] = True


# =====================================================================================================
# 2.5 (Paket 4): Diagramme - EINE Logik fuer die Datenbank-Ansicht "chart" und das Widget "chart"
# =====================================================================================================
# Vertrag fuer alle Clients: docs/APP-API.md "Diagramme". Gruppiert wird nach einer Eigenschaft (x, bei Datum in Eimern),
# optional zusaetzlich nach einer zweiten (series); gerechnet wird je Gruppe NUR ueber aggregate() - dieselben Zahlen wie
# Fusszeile, Kennzahl-Widget und Rollups. Die Clients zeichnen nur (Web: static/pages.js chartSvg, Apps nativ).
CHART_TYPES = ("bar", "hbar", "line", "area", "pie", "donut", "number")
CHART_BUCKETS = ("day", "week", "month", "quarter", "year")
CHART_SORTS = ("x", "y_desc", "y_asc", "manual")
CHART_FMTS = ("num", "pct", "eur")
CHART_CATS_MAX, CHART_SERIES_MAX = 50, 10
CHART_GAPS_MAX = 400                      # Datums-Eimer: Luecken nur auffuellen, wenn es danach hoechstens so viele sind
OTHER_KEY = "__other__"
STATUS_COLORS = {"todo": "gray", "doing": "blue", "done": "green"}
_CHART_PID = re.compile(r"[a-z0-9_]{1,24}|title")


def _spec_pid(v):
    return v if isinstance(v, str) and _CHART_PID.fullmatch(v) else ""


def clean_chart_spec(c):
    """Diagramm-Einstellungen bereinigen (Ansicht view.chart und Widget cfg) -> neues dict (nur bekannte Felder)."""
    c = c if isinstance(c, dict) else {}
    typ = c.get("type") if c.get("type") in CHART_TYPES else "bar"
    out = {"type": typ, "x": _spec_pid(c.get("x")), "agg": c.get("agg") if c.get("agg") in AGGS else "count",
           "y": _spec_pid(c.get("y")), "sort": c.get("sort") if c.get("sort") in CHART_SORTS else "x",
           "fmt": c.get("fmt") if c.get("fmt") in CHART_FMTS else "num"}
    if out["agg"] == "count":
        out["y"] = ""
    if c.get("bucket") in CHART_BUCKETS:
        out["bucket"] = c["bucket"]
    if c.get("status") is True:
        out["status"] = True
    if typ in ("bar", "hbar", "line", "area") and _spec_pid(c.get("series")) and c.get("series") != "title":
        out["series"] = c["series"]
    lim = c.get("limit")
    if isinstance(lim, int) and not isinstance(lim, bool):
        out["limit"] = max(3, min(CHART_CATS_MAX, lim))
    if typ in ("line", "area") and c.get("cumulative") is True:
        out["cumulative"] = True
    if typ in ("bar", "hbar", "area") and c.get("stack") is True and out.get("series"):
        out["stack"] = True
    if c.get("empty") == "show":
        out["empty"] = "show"
    g = c.get("goal")
    if isinstance(g, (int, float)) and not isinstance(g, bool) and g == g and abs(g) < 1e12:
        out["goal"] = _n(g)
    if out["sort"] == "manual" and isinstance(c.get("order"), list):
        order = [x[:64] for x in c["order"][:CHART_CATS_MAX] if isinstance(x, str) and x and "<" not in x]
        if order:
            out["order"] = order
    if out["agg"] == "pct_checked":
        out["fmt"] = "pct"
    return out


def _bucket(d, b):
    """Datum -> (Anfang des Eimers als date, Schluessel)."""
    if b == "week":
        s = d - timedelta(days=d.weekday())                  # Woche ab Montag (wie dbquery.rel_date)
        y, w, _ = s.isocalendar()
        return s, f"{y}-W{w:02d}"
    if b == "month":
        s = d.replace(day=1)
        return s, f"{s.year:04d}-{s.month:02d}"
    if b == "quarter":
        s = d.replace(month=(d.month - 1) // 3 * 3 + 1, day=1)
        return s, f"{s.year:04d}-Q{(s.month - 1) // 3 + 1}"
    if b == "year":
        s = d.replace(month=1, day=1)
        return s, f"{s.year:04d}"
    return d, d.isoformat()


def _bucket_next(s, b):
    if b == "day":
        return s + timedelta(days=1)
    if b == "week":
        return s + timedelta(days=7)
    m = s.month + {"month": 1, "quarter": 3, "year": 12}[b]
    return s.replace(year=s.year + (m - 1) // 12, month=(m - 1) % 12 + 1)


class _Axis:
    """Gruppierung nach einer Eigenschaft: keys(row) -> Liste von (schluessel, label, farbe, sortwert). Leer: [(None, ...)]."""

    def __init__(self, schema, q, pid, spec, labels, untitled, tz, people=None):
        self.q, self.tz, self.untitled, self.labels_fn, self.people = q, tz, untitled, labels, people
        self.bucket = spec.get("bucket") or "month"
        if pid == "title":
            self.p = {"id": "title", "type": "text"}
        else:
            p = q.prop_by_id(pid)
            if p is dbquery.UNDEF or not isinstance(p, dict):
                raise Fail("bad_cfg")
            self.p = p
        t = self.p.get("type")
        rt = self.p.get("rtype") if t in ("formula", "rollup") else None
        self.status = spec.get("status") is True and dbquery.is_status(self.p)
        if t in ("date", "created", "edited") or rt == "date":
            self.kind = "date"
        elif t in ("select", "multi_select"):
            self.kind = "select"
        elif t == "relation":
            self.kind = "relation"
        elif t == "person":                                  # 2.5: je Person (Anzeigename aus people, nie Anmeldename)
            if people is None:                               # veroeffentlichte Seite: Personen nie oeffentlich
                raise Fail("no_access")
            self.kind = "person"
        elif t == "checkbox" or rt == "checkbox":
            self.kind = "checkbox"
        elif t in ("number", "calc") or rt == "number":
            self.kind = "number"
        elif t in ("text", "url") or rt in ("text", "list"):
            self.kind = "text"
        else:
            raise Fail("bad_cfg")
        self.opts = {o.get("id"): (i, o) for i, o in enumerate(o for o in (self.p.get("options") or []) if isinstance(o, dict))}
        self._lab = {}

    def _day(self, v):
        t = self.p.get("type")
        if t in ("created", "edited"):
            try:
                return datetime.fromtimestamp(float(v), self.tz).date()
            except (TypeError, ValueError, OverflowError, OSError):
                return None
        if not isinstance(v, str) or len(v) < 10:
            return None
        sp = self.q.day_span(v)
        if sp is None:
            return None
        try:
            return date.fromordinal(sp[0] + 719163)
        except (ValueError, OverflowError):
            return None

    def prefetch(self, rows):
        if self.kind != "relation" or not self.labels_fn:
            return
        ids = set()
        for r in rows:
            v = self.q.prop_value(self.p, r) if self.p["id"] != "title" else None
            if isinstance(v, list):
                ids.update(x for x in v if isinstance(x, int) and not isinstance(x, bool))
        self._lab = self.labels_fn(sorted(ids)) if ids else {}

    def keys(self, r):
        p, k = self.p, self.kind
        v = r.get("title") if p["id"] == "title" else self.q.prop_value(p, r)
        if k == "date":
            d = self._day(v)
            if d is None:
                return [(None,)]
            s, key = _bucket(d, self.bucket)
            return [(key, key, None, s.toordinal())]
        if k == "select":
            if self.status:
                g = dbquery.status_group(p, v)
                if g is None:
                    return [(None,)]
                return [(g, {"todo": t("Zu erledigen"), "doing": t("In Arbeit"), "done": t("Erledigt")}[g], STATUS_COLORS[g],
                         dbquery.STATUS_GROUPS.index(g))]
            vals = v if isinstance(v, list) else ([v] if isinstance(v, str) else [])
            out = []
            for x in dict.fromkeys(x for x in vals if isinstance(x, str)):
                hit = self.opts.get(x)
                if hit:
                    out.append((x, str(hit[1].get("name") or ""), hit[1].get("color") if hit[1].get("color") in _OPT_COLORS else None, hit[0]))
            return out or [(None,)]
        if k == "relation":
            ids = [x for x in (v if isinstance(v, list) else []) if isinstance(x, int) and not isinstance(x, bool)]
            out = [(str(i), self._lab.get(i) or self.untitled, None, (self._lab.get(i) or self.untitled).lower()) for i in dict.fromkeys(ids)
                   if i in self._lab]
            return out or [(None,)]
        if k == "person":                                    # eine Zeile zaehlt bei jeder eingetragenen Person
            out = []
            for ref in dict.fromkeys(x for x in (v if isinstance(v, list) else []) if isinstance(x, str)):
                e = self.people.get(ref) if isinstance(self.people.get(ref), dict) else {}
                nm = e.get("name") if isinstance(e.get("name"), str) and e.get("name") else t("Gelöschte Person")
                out.append((ref, nm, None, nm.lower()))
            return out or [(None,)]
        if k == "checkbox":
            on = v is True
            return [("true" if on else "false", t("Ja") if on else t("Nein"), None, 0 if on else 1)]
        if k == "number":
            x = _num_in(v)
            if x is None:
                return [(None,)]
            return [(dbquery.js_num_str(x), dbquery.js_num_str(x), None, x)]
        # Text, URL, Liste (Formel/Rollup): jeder Eintrag einer Liste zaehlt einzeln
        vals = v if isinstance(v, list) else [v]
        out = []
        for x in vals:
            if x is None or x is dbquery.UNDEF or isinstance(x, (dict, list)):
                continue
            s = " ".join(dbquery.js_str(x).split())[:120]
            if s:
                out.append((s, s, None, s.lower()))
        return list({o[0]: o for o in out}.values()) or [(None,)]


_OPT_COLORS = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")


def _group(axis, rows, empty_show):
    """-> geordnetes dict schluessel -> {label, color, sort, rows}. Leere Werte unter None (nur mit empty_show)."""
    out = {}
    for r in rows:
        for k in axis.keys(r):
            key = k[0]
            if key is None and not empty_show:
                continue
            g = out.get(key)
            if g is None:
                g = out[key] = {"label": t("Leer") if key is None else k[1], "color": None if key is None else k[2],
                                "sort": (1, 0) if key is None else (0, k[3]), "rows": []}
            g["rows"].append(r)
    if axis.kind == "select" and empty_show:                 # leere Optionen/Status-Gruppen auch zeigen
        if axis.status:
            for i, g in enumerate(dbquery.STATUS_GROUPS):
                out.setdefault(g, {"label": {"todo": t("Zu erledigen"), "doing": t("In Arbeit"), "done": t("Erledigt")}[g],
                                   "color": STATUS_COLORS[g], "sort": (0, i), "rows": []})
        else:
            for oid, (i, o) in axis.opts.items():
                if isinstance(oid, str):
                    out.setdefault(oid, {"label": str(o.get("name") or ""), "color": o.get("color") if o.get("color") in _OPT_COLORS else None,
                                         "sort": (0, i), "rows": []})
    return out


def _agg(spec, rows, schema):
    return aggregate(spec["agg"], spec.get("y") or "", rows, schema)


def chart(schema, rows, spec, labels=None, tz=None, today=None, untitled=None, with_ids=False, people=None):
    """Diagramm aus den (schon nach der Ansicht gefilterten) Zeilen -> Ergebnis (siehe docs/APP-API.md "Diagramme").
    Fail('bad_cfg') bei unbekannter/ungeeigneter Eigenschaft (auch Datei-Spalten als Achse). people: {kennung: {name}} der
    Personen in rows (people.out_values); None = keine Personen-Achse moeglich (veroeffentlichte Seite -> Fail('no_access')). with_ids: je Kategorie die Zeilen-IDs (Web: Klick auf einen
    Balken zeigt diese Zeilen) als "ids" (Liste je cats-Eintrag)."""
    spec = clean_chart_spec(spec)
    tz = tz or timezone.utc
    untitled = untitled or t("Unbenannt")
    q = dbquery.Query(schema, tz=tz, today=today)
    typ = spec["type"]
    if spec["agg"] != "count" and not spec.get("y"):
        raise Fail("bad_cfg")
    total = _agg(spec, rows, schema)
    res = {"type": typ, "x_kind": "cat", "cats": [], "series": [], "total": total, "fmt": spec["fmt"], "other": 0,
           "truncated": False, "rows": len(rows)}
    if "goal" in spec:
        res["goal"] = spec["goal"]
    if typ == "number":
        return res
    if not spec.get("x"):
        raise Fail("bad_cfg")
    ax = _Axis(schema, q, spec["x"], spec, labels, untitled, tz, people)
    ax.prefetch(rows)
    empty_show = spec.get("empty") == "show"
    groups = _group(ax, rows, empty_show)
    limit = spec.get("limit") or (CHART_CATS_MAX if ax.kind == "date" else 20)
    if ax.kind == "date":
        res["x_kind"], res["bucket"] = "date", ax.bucket
        dated = sorted((k for k in groups if k is not None), key=lambda k: groups[k]["sort"])
        if dated and typ in ("line", "area", "bar", "hbar"):          # Zeitachse ohne Luecken (leere Eimer = 0 bzw. null)
            s, end = date.fromordinal(groups[dated[0]]["sort"][1]), date.fromordinal(groups[dated[-1]]["sort"][1])
            fill, n = [], 0
            while s <= end and n <= CHART_GAPS_MAX:
                fill.append(s)
                try:
                    s = _bucket_next(s, ax.bucket)
                except (OverflowError, ValueError):          # Eimer nach 9999-12-31 gibt es nicht (sonst 500)
                    break
                n += 1
            if n <= CHART_GAPS_MAX:
                for d in fill:
                    _, key = _bucket(d, ax.bucket)
                    groups.setdefault(key, {"label": key, "color": None, "sort": (0, d.toordinal()), "rows": []})
    keys = list(groups)
    for g in groups.values():
        g["y"] = _agg(spec, g["rows"], schema)
    yv = lambda k: groups[k]["y"] if isinstance(groups[k]["y"], (int, float)) else float("-inf")   # noqa: E731
    natural = sorted(keys, key=lambda k: groups[k]["sort"])
    if len(keys) > limit:
        if ax.kind == "date":                                # Zeitachse: die juengsten Eimer behalten
            keep = natural[-limit:]
            res["truncated"] = True
        else:                                                # Top-N nach Wert, Rest als "Andere"
            top = sorted(keys, key=lambda k: (-yv(k), groups[k]["sort"]))[:limit - 1]
            rest = [k for k in keys if k not in top]
            other_rows, seen = [], set()
            for k in rest:
                for r in groups[k]["rows"]:
                    if id(r) not in seen:
                        seen.add(id(r))
                        other_rows.append(r)
            res["other"] = len(rest)
            groups[OTHER_KEY] = {"label": t("Andere"), "color": None, "sort": (2, 0), "rows": other_rows,
                                 "y": _agg(spec, other_rows, schema)}
            keep = top + [OTHER_KEY]
        keys = keep
    sort = spec["sort"]
    if sort == "y_desc":
        keys = sorted(keys, key=lambda k: (k == OTHER_KEY, -yv(k), groups[k]["sort"]))
    elif sort == "y_asc":
        keys = sorted(keys, key=lambda k: (k == OTHER_KEY, yv(k), groups[k]["sort"]))
    elif sort == "manual" and spec.get("order"):
        pos = {k: i for i, k in enumerate(spec["order"])}
        keys = sorted(keys, key=lambda k: (k == OTHER_KEY, pos.get(k, len(pos)), groups[k]["sort"]))
    else:
        keys = sorted(keys, key=lambda k: groups[k]["sort"])
    res["cats"] = [{"key": k, "label": groups[k]["label"], "color": groups[k]["color"]} for k in keys]
    if with_ids:
        res["ids"] = [[r.get("id") for r in groups[k]["rows"]] for k in keys]
    if ax.kind == "date":                                    # Anfang des Eimers als Tag (Clients formatieren selbst)
        for cat in res["cats"]:
            g = groups[cat["key"]]
            if cat["key"] is not None and cat["key"] != OTHER_KEY:
                cat["start"] = date.fromordinal(g["sort"][1]).isoformat()
    # Reihen
    if spec.get("series") and typ in ("bar", "hbar", "line", "area"):
        sx = _Axis(schema, q, spec["series"], dict(spec, status=spec.get("status"), bucket=spec.get("bucket")), labels, untitled, tz, people)
        sx.prefetch(rows)
        sg = _group(sx, rows, empty_show)
        skeys = sorted(sg, key=lambda k: sg[k]["sort"])
        if len(skeys) > CHART_SERIES_MAX:
            tot = {k: len(sg[k]["rows"]) for k in skeys}
            top = sorted(skeys, key=lambda k: (-tot[k], sg[k]["sort"]))[:CHART_SERIES_MAX - 1]
            rest_rows, seen = [], set()
            for k in skeys:
                if k not in top:
                    for r in sg[k]["rows"]:
                        if id(r) not in seen:
                            seen.add(id(r))
                            rest_rows.append(r)
            skeys = [k for k in skeys if k in top] + [OTHER_KEY]
            sg[OTHER_KEY] = {"label": t("Andere"), "color": None, "sort": (2, 0), "rows": rest_rows}
            res["truncated"] = True
        for sk in skeys:
            ids = {id(r) for r in sg[sk]["rows"]}
            vals = [_agg(spec, [r for r in groups[k]["rows"] if id(r) in ids], schema) for k in keys]
            res["series"].append({"key": sk, "label": sg[sk]["label"], "color": sg[sk]["color"], "values": vals})
    else:
        res["series"].append({"key": "", "label": "", "color": None, "values": [groups[k]["y"] for k in keys]})
    if spec.get("cumulative") and typ in ("line", "area"):
        for s in res["series"]:
            acc, out = 0, []
            for v in s["values"]:
                acc = _n(_round2(acc + (v if isinstance(v, (int, float)) else 0)))
                out.append(acc)
            s["values"] = out
    if spec.get("stack"):
        res["stack"] = True
    if spec.get("cumulative") and typ in ("line", "area"):
        res["cumulative"] = True
    return res
