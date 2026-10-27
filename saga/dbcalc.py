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
import time
from datetime import datetime, timezone

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

    def __init__(self, labels=None, target=None):
        self._labels_fn, self._target_fn = labels, target
        self._labels, self._targets = {}, {}

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
        return fx.day_of(v, tz) if isinstance(v, str) else None
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


def fill_core(schema, rows, tz=None, now=None, labels=None, target=None, rollups=True, untitled=None, deadline=None, lang=None):
    """Formeln/Rollups in rows eintragen - ohne Datenbank: labels(ids) -> {id: titel} (nur sichtbare Seiten),
    target(db_id) -> (schema, zeilen) der Ziel-Datenbank (nur sichtbare Zeilen) oder None."""
    if not has_calc(schema) or not rows:
        return rows
    if tz is None:                                      # ohne Angabe der Anfrage: Zeitzone des Servers (TZ)
        from saga.core import TZ
        tz = TZ
    now = now or datetime.now(timezone.utc)
    src = _Source(labels, target)
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
    return fill_core(schema, rows, tz=tz, now=now, labels=labels, target=target, rollups=rollups)


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
