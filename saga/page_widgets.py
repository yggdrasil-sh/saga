"""Widgets in Seiten (2.5, Etappe 1): Uhr, Countdown, Fortschritt, Kennzahl, Zitat als Block "widget".

Block: {"type": "widget", "data": {"v": 1, "kind", "size", "style", "accent", "title", "cfg": {...}}} - Vertrag fuer alle
Clients in docs/APP-API.md ("Widgets in Seiten"). Nicht zu verwechseln mit den Home-Bildschirm-Widgets der Apps
(saga.app_api, /api/app/...).

- clean_data(): Bereinigung beim Speichern (aus saga.pages._clean_design, auch verschachtelt in Spalten/Toggles). Bekannte
  Arten streng (Typen, Bereiche, Laengen, Unbekanntes in cfg faellt weg), unbekannte Arten bleiben wie sie sind
  (Vorwaertskompatibilitaet). title und Zitat-Texte sind Klartext (Clients zeigen sie nie als HTML).
- Rechnen (progress mit src=db, metric, quote): immer mit den Rechten des Betrachters an der Quell-Datenbank
  (sharing.access). Ohne Zugriff: error "no_access" ohne Werte und ohne Namen. Zeilen ueber dbquery.run (gleiche Semantik
  wie die Web-Ansicht), Aggregation wie die Fusszeile der Datenbank-Tabelle im Web (static/pages.js calcNum).
- GET /api/notes/{nid}/widgets (ab 'Lesen'), POST /api/notes/{nid}/widgets/preview (ab 'Bearbeiten').
  Zwischenspeicher 60 s je (Seite, Block, Konto, Einstellungen); die Rechte (Seite und Quelle) prueft jeder Abruf neu.
- Foederation: saga.fed_pages leitet /fed/v2/notes/{rid}/widgets weiter (Faehigkeit widgets1).
- fix27 E3: metric/progress mit cfg.src "tasks" = Saga-Aufgaben der BETRACHTENDEN Person (task-Tabelle): metric agg
  open | due_today | overdue | done_week, progress = erledigt diese Woche / (erledigt + offen faellig diese Woche).
  Woche = Montag bis Sonntag in der Zeitzone der Anfrage. Personen anderer Instanzen und Veroeffentlichen: no_access.
- fix27 #3: der Zwischenspeicher haengt am Stand der Quelle (Zeilen/Schema der Datenbank, Aufgaben) - jede Aenderung
  rechnet beim naechsten Abruf sofort neu."""
import hashlib
import json
import random
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from html import unescape as html_unescape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import clean_line, connect as _db
from saga import dbcalc, dbquery, people
from saga.core import TZ, _api_guard, _bad, _body, _json_load, t
from saga.notes import PALETTE, _iter_blocks
from saga.sharing import Access, access, is_remote

router = APIRouter()

KINDS = ("clock", "countdown", "progress", "metric", "quote")
SIZES = ("s", "m", "l")
STYLES = ("card", "plain")
AGGS = dbcalc.AGGS
TASK_AGGS = ("open", "due_today", "overdue", "done_week")     # fix27 E3: metric mit src "tasks"
FMTS = ("num", "pct", "eur")
ERRORS = ("no_access", "gone", "bad_cfg", "too_big")
TITLE_MAX = 80
QUOTE_ITEMS_MAX, QUOTE_LEN = 20, 300
ID_MAX = 64                        # Ansicht-/Eigenschaft-IDs
WIDGETS_MAX = 50                   # gerechnete Widgets je Seite und Abruf; weitere: too_big
CACHE_TTL = 60.0
NUM_MAX = 1e12
_BID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_TZ_NAME = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_HM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class _Fail(Exception):
    """Widget laesst sich nicht rechnen -> error (einer von ERRORS)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


# =====================================================================================================
# Bereinigung
# =====================================================================================================
def _plain(v, limit):
    """Text aus data (schon durch pages.sanitize_inline gelaufen) -> Klartext ohne Tags, ohne Steuerzeichen, gekuerzt."""
    if not isinstance(v, str):
        return ""
    s = html_unescape(re.sub(r"<[^>]*>", "", v))
    s = re.sub(r"\s+", " ", clean_line(s)).strip()
    return s[:limit]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < NUM_MAX


def _n(v):
    """Zahl fuer JSON: ganze Zahlen ohne ',0'."""
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def _id(v):
    return v[:ID_MAX] if isinstance(v, str) and not re.search(r"[\x00-\x1f<>\"'&]", v) else ""


def _db_id(v):
    """Quell-Datenbank: ID dieses Servers (> 0). Fremde/negative IDs: None (in Etappe 1 nicht vorgesehen)."""
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 63 else None


@lru_cache(maxsize=512)
def tz_ok(name):
    if not isinstance(name, str) or not _TZ_NAME.fullmatch(name) or ".." in name:
        return False
    try:
        ZoneInfo(name)
        return True
    except (ValueError, KeyError, OSError):
        return False


def _day_ok(s):
    if not isinstance(s, str) or not _DAY.fullmatch(s):
        return False
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def _clean_cfg(kind, c):
    c = c if isinstance(c, dict) else {}
    if kind == "clock":
        return {"tz": c["tz"] if tz_ok(c.get("tz")) else "", "h24": c.get("h24") is not False, "sec": c.get("sec") is True,
                "analog": c.get("analog") is True}
    if kind == "countdown":
        return {"date": c["date"] if _day_ok(c.get("date")) else "",
                "time": c["time"] if isinstance(c.get("time"), str) and _HM.fullmatch(c["time"]) else "",
                "dir": "since" if c.get("dir") == "since" else "until", "unit": "auto" if c.get("unit") == "auto" else "days"}
    if kind == "progress":
        if c.get("src") == "tasks":
            return {"src": "tasks"}
        if c.get("src") == "db":
            out = {"src": "db", "db": _db_id(c.get("db")), "view": _id(c.get("view")),
                   "mode": "sum" if c.get("mode") == "sum" else "checked", "prop": _id(c.get("prop"))}
            if out["mode"] == "sum" and _num(c.get("goal")) and c["goal"] > 0:
                out["goal"] = _n(c["goal"])
            return out
        mx = c.get("max")
        return {"src": "fixed", "value": _n(c["value"]) if _num(c.get("value")) else 0, "max": _n(mx) if _num(mx) and mx > 0 else 100}
    if kind == "metric":
        if c.get("src") == "tasks":
            out = {"src": "tasks", "agg": c["agg"] if c.get("agg") in TASK_AGGS else "open", "fmt": "num"}
            if _num(c.get("goal")):
                out["goal"] = _n(c["goal"])
            return out
        out = {"db": _db_id(c.get("db")), "view": _id(c.get("view")), "agg": c.get("agg") if c.get("agg") in AGGS else "count",
               "prop": _id(c.get("prop")), "fmt": c.get("fmt") if c.get("fmt") in FMTS else "num"}
        if out["agg"] == "count":
            out["prop"] = ""
        if _num(c.get("goal")):
            out["goal"] = _n(c["goal"])
        return out
    if kind == "quote":
        pick = "open" if c.get("pick") == "open" else "daily"
        if c.get("src") == "db":
            return {"src": "db", "db": _db_id(c.get("db")), "view": _id(c.get("view")), "prop": _id(c.get("prop")), "pick": pick}
        items = [x for x in (_plain(x, QUOTE_LEN) for x in (c.get("items") if isinstance(c.get("items"), list) else [])) if x]
        return {"src": "fixed", "items": items[:QUOTE_ITEMS_MAX], "pick": pick}
    return c


def clean_data(d):
    """data eines Widget-Blocks bereinigen (Strings sind schon durch sanitize_inline gelaufen) -> neues dict."""
    d = d if isinstance(d, dict) else {}
    kind = d.get("kind")
    if kind not in KINDS:                       # unbekannte (neuere) Art: unveraendert behalten
        return d
    out = {"v": 1, "kind": kind}
    if d.get("size") in SIZES:
        out["size"] = d["size"]
    if d.get("style") in STYLES:
        out["style"] = d["style"]
    if d.get("accent") in PALETTE:
        out["accent"] = d["accent"]
    ti = _plain(d.get("title"), TITLE_MAX)
    if ti:
        out["title"] = ti
    out["cfg"] = _clean_cfg(kind, d.get("cfg"))
    return out


def fix_ids(blocks):
    """Widget-Bloecke brauchen eine eindeutige id (Werte werden je Block-id geliefert) - auch in Spalten/Toggles.
    Fehlt sie, ist sie ungueltig oder doppelt: neue id."""
    seen = set()
    for b in _iter_blocks(blocks):
        if b.get("type") != "widget":
            continue
        bid = b.get("id")
        if not isinstance(bid, str) or not _BID.fullmatch(bid) or bid in seen:
            bid = secrets.token_urlsafe(8)[:10].replace("-", "x").replace("_", "y")
            b["id"] = bid
        seen.add(bid)
    return blocks


def server_kind(d):
    """Rechnet der Server dieses Widget (Wert aus GET .../widgets)?"""
    if not isinstance(d, dict):
        return False
    k, c = d.get("kind"), d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
    return k in ("metric", "quote") or (k == "progress" and c.get("src") in ("db", "tasks"))


# =====================================================================================================
# Aggregation - im gemeinsamen Rechenkern (saga.dbcalc, wie die Fusszeile im Web: static/pages.js calcNum)
# =====================================================================================================
def aggregate(op, pid, rows, schema):
    """Siehe dbcalc.aggregate (Fehler als _Fail fuer die Widgets)."""
    try:
        return dbcalc.aggregate(op, pid, rows, schema)
    except dbcalc.Fail as f:
        raise _Fail(f.code)


# =====================================================================================================
# Rechnen
# =====================================================================================================
def _W(kind, value=None, mx=None, text=None, fmt=None, error=None, as_of=None):
    return {"kind": kind, "value": value, "max": mx, "text": text, "fmt": fmt,
            "as_of": as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "error": error}


def _labels_fn(c, viewer, owner):
    def labels(ids):
        """Titel verknuepfter Eintraege (Filter auf Relationen) - nur, was der Betrachter kennt (wie app_api)."""
        if viewer == owner:
            got = c.execute("SELECT id, title FROM note WHERE id = ANY(?) AND uid=? AND deleted_at IS NULL AND archived IS NULL",
                            (list(ids), owner)).fetchall()
        else:
            vis = Access(c, viewer).tree(owner)
            ok = [i for i in ids if i in vis]
            got = c.execute("SELECT id, title FROM note WHERE id = ANY(?)", (ok,)).fetchall() if ok else []
        return {i: tl or "" for i, tl in got}
    return labels


def _source(c, uid, dbid, acc, public_owner=None):
    """-> (eigentuemer, schema, shared). Recht des Betrachters an der Quell-DB; public_owner: Veroeffentlichen (nur eigene DB)."""
    if dbid is None:
        raise _Fail("bad_cfg")
    if public_owner is not None:
        r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL AND archived IS NULL",
                      (dbid, public_owner)).fetchone()
        if not r:
            raise _Fail("gone")
        return public_owner, _json_load(r[0], {"props": []}), True
    owner, role, err = access(c, uid, dbid, "view", acc)
    if err is not None:
        raise _Fail("no_access")                   # weder Name noch Hinweis, ob es die Datenbank gibt
    r = c.execute("SELECT kind, schema, deleted_at FROM note WHERE id=? AND uid=?", (dbid, owner)).fetchone()
    if not r or r[2] is not None:
        raise _Fail("gone")
    if r[0] != "database":
        raise _Fail("bad_cfg")
    return owner, _json_load(r[1], {"props": []}), role != "owner"


def _rows(c, uid, owner, dbid, schema, shared, view, tz, public=False):
    """Zeilen der Quell-DB nach der Ansicht (dbquery.run, wie GET .../rows?view=...). Fremd: ohne archivierte Zeilen.
    public: veroeffentlichte Seite - Personen-/Datei-Werte gar nicht (auch nicht ueber Formeln)."""
    where = "uid=? AND parent_id=? AND deleted_at IS NULL AND is_template=0" + (" AND archived IS NULL" if shared else "")
    n = c.execute(f"SELECT count(*) FROM note WHERE {where}", (owner, dbid)).fetchone()[0]
    if n > dbquery.LIMIT_MAX:
        raise _Fail("too_big")
    data = [{"id": r[0], "title": r[1] or "", "props": _json_load(r[2], {}), "updated": r[3], "created": r[4]}
            for r in c.execute(f"SELECT id, title, props, updated, created FROM note WHERE {where} ORDER BY sort NULLS FIRST, created, id",
                               (owner, dbid)).fetchall()]
    args = {"tz": tz}
    if view:
        args["view"] = view
    if public:
        from saga.publish import _private_off
        for r in data:
            r["props"] = _private_off(schema, r["props"])
    if dbcalc.has_calc(schema):                     # 2.5: Formeln/Rollups mit den Rechten des Betrachters
        dbcalc.fill(c, owner, schema, data, viewer=uid, tz=tz)
    pm = people.out_values(c, schema, data, uid)    # 2.5: Personen wie im Web (Filter "mir zugewiesen"/"enthaelt")
    try:
        page, _, _ = dbquery.run(schema, data, args, labels=_labels_fn(c, uid, owner), untitled=t("Unbenannt"), people=pm,
                                 me=people.to_ref(c, uid))
    except dbquery.JsError:
        raise _Fail("bad_cfg")
    return page


def _pick(items, bid, day, pick):
    if not items:
        return None
    if pick == "open":
        return items[random.SystemRandom().randrange(len(items))]
    h = hashlib.sha256(f"{day.isoformat()}|{bid}".encode()).digest()
    return items[int.from_bytes(h[:8], "big") % len(items)]


def _text_of(v):
    """Wert einer Zeile als Zitat-Text (Klartext, wie gespeichert); Listen, Objekte, Haekchen: kein Text."""
    if v is None or v is dbquery.UNDEF or isinstance(v, (dict, list, bool)):
        return ""
    if isinstance(v, (int, float)):
        return dbquery.js_num_str(v)
    return re.sub(r"\s+", " ", clean_line(str(v))).strip()[:QUOTE_LEN * 4]


def task_stats(c, uid, today, tz=None):
    """Saga-Aufgaben von uid (fix27 E3): {open, due_today, overdue, done_week, open_week}. today: Datum in der Zeitzone
    der Anfrage; Woche Montag bis Sonntag. done_week nach done_at (Zeitpunkt) in derselben Zeitzone - Wiederholungen
    zaehlen je erledigtem Durchgang (die Folgeaufgabe ist eine neue, offene Aufgabe)."""
    mon = today - timedelta(days=today.weekday())
    sun = mon + timedelta(days=6)
    out = {"open": 0, "due_today": 0, "overdue": 0, "done_week": 0, "open_week": 0}
    for done, due, done_at in c.execute("SELECT done, due, done_at FROM task WHERE uid=?", (uid,)).fetchall():
        if not done:
            out["open"] += 1
            if due and _day_ok(due):
                if due == today.isoformat():
                    out["due_today"] += 1
                elif due < today.isoformat():
                    out["overdue"] += 1
                if mon.isoformat() <= due <= sun.isoformat():
                    out["open_week"] += 1
        elif done_at:
            d = datetime.fromtimestamp(done_at, tz or TZ).date()
            if mon <= d <= sun:
                out["done_week"] += 1
    return out


def compute(c, uid, bid, d, tz=None, acc=None, public_owner=None, today=None):
    """Wert eines Widgets (W, siehe Modulkopf) mit den Rechten von uid. d: bereinigtes data."""
    kind = d.get("kind") if isinstance(d, dict) else None
    if kind not in KINDS:
        return _W(kind if isinstance(kind, str) else None, error="bad_cfg")
    cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
    tz = tz or TZ
    today = today or datetime.now(tz).date()
    try:
        if kind == "progress" and cfg.get("src") not in ("db", "tasks"):
            return _W(kind, cfg.get("value", 0), cfg.get("max", 100))
        if kind in ("clock", "countdown"):
            return _W(kind)
        if kind == "quote" and cfg.get("src") != "db":
            return _W(kind, text=_pick(cfg.get("items") or [], bid, today, cfg.get("pick")))
        if cfg.get("src") == "tasks" and kind in ("metric", "progress"):
            if public_owner is not None or uid is None or is_remote(uid):
                raise _Fail("no_access")            # Aufgaben gehoeren der betrachtenden Person dieses Servers
            st = task_stats(c, uid, today, tz)
            if kind == "progress":
                return _W(kind, st["done_week"], st["done_week"] + st["open_week"])
            agg = cfg.get("agg") if cfg.get("agg") in TASK_AGGS else "open"
            return _W(kind, st[agg], cfg.get("goal"), fmt="num")
        acc = acc or (Access(c, uid) if public_owner is None else None)
        owner, schema, shared = _source(c, uid, cfg.get("db"), acc, public_owner)
        rows = _rows(c, uid if public_owner is None else public_owner, owner, cfg["db"], schema, shared, cfg.get("view") or "", tz,
                     public=public_owner is not None)
        if kind == "progress":
            if cfg.get("mode") == "sum":
                if not _num(cfg.get("goal")):
                    raise _Fail("bad_cfg")
                v = aggregate("sum", cfg.get("prop") or "", rows, schema) if cfg.get("prop") else None
                if not cfg.get("prop"):
                    raise _Fail("bad_cfg")
                pf = next((x.get("fmt") for x in (schema.get("props") or []) if isinstance(x, dict) and x.get("id") == cfg["prop"]), None)   # Fix27 #10
                return _W(kind, v if v is not None else 0, cfg["goal"], fmt={"euro": "eur", "percent": "pct"}.get(pf))
            if not cfg.get("prop"):
                raise _Fail("bad_cfg")
            return _W(kind, aggregate("checked", cfg["prop"], rows, schema), len(rows))
        if kind == "metric":
            agg = cfg.get("agg") or "count"
            if agg != "count" and not cfg.get("prop"):
                raise _Fail("bad_cfg")
            v = aggregate(agg, cfg.get("prop") or "", rows, schema)
            return _W(kind, v, cfg.get("goal"), fmt="pct" if agg == "pct_checked" else cfg.get("fmt") or "num")
        # quote aus der Datenbank: Text der Eigenschaft (oder Titel) je Zeile
        pid = cfg.get("prop") or ""
        q = dbquery.Query(schema)
        p = None
        if pid and pid != "title":
            p = q.prop_by_id(pid)
            if p is dbquery.UNDEF or not isinstance(p, dict):
                raise _Fail("bad_cfg")
        items = [x for x in (_text_of(q.prop_value(p, r) if p is not None else r.get("title")) for r in rows) if x]
        return _W(kind, text=_pick(items, bid, today, cfg.get("pick")))
    except _Fail as f:
        return _W(kind, error=f.code)


# --- Zwischenspeicher: 60 s je (Konto, Seite, Block, Einstellungen, Zeitzone, Tag) ----------------------------------
_CACHE = {}
_CL = threading.Lock()


def _cached(key, fn):
    now = time.monotonic()
    with _CL:
        hit = _CACHE.get(key)
        if hit and hit[0] > now:
            return hit[1]
    w = fn()
    with _CL:
        if len(_CACHE) > 5000:
            for k in [k for k, v in _CACHE.items() if v[0] <= now] or list(_CACHE)[:2500]:
                _CACHE.pop(k, None)
        _CACHE[key] = (now + CACHE_TTL, w)
    return w


def _db_ver(c, owner, dbid):
    """Stand einer Quell-Datenbank fuer den Zwischenspeicher: Zeilen (Anzahl, juengste Aenderung, archivierte) und die
    Datenbank selbst (Schema/Ansichten). Papierkorb, Wiederherstellen, Archiv und jede Speicherung aendern ihn."""
    r = c.execute("SELECT count(*), coalesce(max(updated), 0), count(archived) FROM note WHERE uid=? AND parent_id=? "
                  "AND deleted_at IS NULL AND is_template=0", (owner, dbid)).fetchone()
    d = c.execute("SELECT updated, schema FROM note WHERE id=? AND uid=?", (dbid, owner)).fetchone()
    return (r[0], r[1], r[2], d[0] if d else None, hashlib.sha256((d[1] or "").encode()).hexdigest()[:16] if d else None)


def _tz_param(params):
    v = params.get("tz") if params else None
    if not v:
        return None, None
    if not tz_ok(v):
        raise ValueError("tz")
    return ZoneInfo(v), v


def page_widgets(uid, nid, params):
    """Werte aller vom Server gerechneten Widgets einer gespeicherten Seite (fuer Web/Apps und die Foederation)."""
    try:
        tz, tzn = _tz_param(params)
    except ValueError:
        return _bad("bad parameter: tz")
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        r = c.execute("SELECT body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        out = {}
        if r[1] == "blocks" and '"widget"' in (r[0] or ""):
            doc = _json_load(r[0], {})
            try:
                blocks = [b for b in _iter_blocks(doc.get("blocks") or []) if b.get("type") == "widget" and server_kind(b.get("data"))]
            except ValueError:
                blocks = []
            today = datetime.now(tz or TZ).date()
            for i, b in enumerate(blocks):
                bid = b.get("id")
                if not isinstance(bid, str) or not bid or bid in out:
                    continue
                d = b["data"]
                if i >= WIDGETS_MAX:
                    out[bid] = _W(d.get("kind"), error="too_big")
                    continue
                cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
                if cfg.get("pick") == "open" and d.get("kind") == "quote":
                    out[bid] = compute(c, uid, bid, d, tz, acc, today=today)
                    continue
                if cfg.get("src") == "tasks":                 # fix27 E3: Aufgaben - klein, immer frisch gerechnet
                    out[bid] = compute(c, uid, bid, d, tz, acc, today=today)
                    continue
                ver = None
                if cfg.get("src") == "db" or d.get("kind") == "metric":
                    try:                                      # Recht und Bestand der Quelle immer frisch (nicht aus dem Speicher)
                        src_owner, _, _ = _source(c, uid, _db_id(cfg.get("db")), acc)
                    except _Fail as f:
                        out[bid] = _W(d.get("kind"), error=f.code)
                        continue
                    ver = _db_ver(c, src_owner, cfg["db"])     # fix27 #3: Aenderung an der Quelle -> neu rechnen
                key = (uid, nid, bid, json.dumps(d, sort_keys=True, ensure_ascii=False), tzn, today.isoformat(), ver)
                out[bid] = _cached(key, lambda d=d, bid=bid: compute(c, uid, bid, d, tz, acc, today=today))
    return JSONResponse({"widgets": out}, headers={"Cache-Control": "no-store"})


def preview(uid, nid, block, params):
    """Vorschau beim Einrichten: ein (noch nicht gespeicherter) Widget-Block, ab Rolle 'Bearbeiten' an der Seite."""
    try:
        tz, _ = _tz_param(params)
    except ValueError:
        return _bad("bad parameter: tz")
    if not isinstance(block, dict) or block.get("type") != "widget" or not isinstance(block.get("data"), dict):
        return _bad(t("ungültiger Block"))
    if len(json.dumps(block, ensure_ascii=False)) > 20000:
        return _bad(t("ungültiger Block"))
    d = clean_data(block["data"])                # Texte werden ohnehin Klartext (_plain), alles andere feste Werte
    bid = block.get("id") if isinstance(block.get("id"), str) and _BID.fullmatch(block["id"]) else ""
    with _db() as c:
        acc = Access(c, uid)
        _, _, err = access(c, uid, nid, "edit", acc)
        if err is not None:
            return err
        w = compute(c, uid, bid, d, tz, acc)
    return JSONResponse({"widget": w}, headers={"Cache-Control": "no-store"})


def value_for(uid, b, tz=None):
    """W eines Blocks fuer den Markdown-Export (eigene Verbindung, Rechte von uid)."""
    if not isinstance(b, dict) or not server_kind(b.get("data")):
        return None
    with _db() as c:
        return compute(c, uid, b.get("id") or "", b["data"], tz)


# =====================================================================================================
# Darstellung als Text (Markdown-Export, Veroeffentlichen)
# =====================================================================================================
def kind_name(kind):
    return {"clock": t("Uhr"), "countdown": t("Countdown"), "progress": t("Fortschritt"), "metric": t("Kennzahl"),
            "quote": t("Zitat")}.get(kind, t("Widget"))


def fmt_num(v, fmt=None):
    """Zahl in der Sprache der Anfrage (Fix27 #10: Prozent ganzzahlig, Euro mit 2 Nachkommastellen)."""
    from saga import i18n
    return i18n.fmt_num(v, fmt)


def countdown_days(cfg, today):
    """Tagesstand eines Countdowns -> (tage, 'until'|'since') oder None (kein Datum)."""
    if not _day_ok(cfg.get("date")):
        return None
    n = (date.fromisoformat(cfg["date"]) - today).days
    return (max(0, n), "until") if cfg.get("dir") != "since" else (max(0, -n), "since")


def countdown_text(cfg, today):
    cd = countdown_days(cfg, today)
    if cd is None:
        return None
    n, how = cd
    if n == 0:
        return t("heute")
    return t("noch {n} {n:Tag|Tage}", n=n) if how == "until" else t("seit {n} {n:Tag|Tagen}", n=n)


def value_text(d, w, today, tz=None):
    """Wert als kurzer Text (ohne Titel) oder None."""
    kind, cfg = d.get("kind"), d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
    if kind == "clock":
        from saga import i18n
        return i18n.day_str(datetime.now(tz or TZ))
    if kind == "countdown":
        return countdown_text(cfg, today)
    if w is None or w.get("error"):
        return None
    if kind == "progress":
        v, mx = w.get("value"), w.get("max")
        pct = round(v / mx * 100) if _num(v) and _num(mx) and mx > 0 else 0
        return f"{fmt_num(v, w.get('fmt'))} / {fmt_num(mx, w.get('fmt'))} ({fmt_num(pct, 'pct')})"
    if kind == "metric":
        s = fmt_num(w.get("value"), w.get("fmt"))
        return s + (f" / {fmt_num(w['max'], w.get('fmt'))}" if _num(w.get("max")) else "")
    if kind == "quote":
        return w.get("text")
    return None


def md_text(b, w=None):
    """Eine Markdown-Zeile: **<Titel oder Art>:** <Wert> (Stand-Datum bei Werten aus einer Datenbank)."""
    from saga import i18n
    d = b.get("data") if isinstance(b.get("data"), dict) else {}
    if d.get("kind") not in KINDS:
        return ""
    cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
    today = datetime.now(TZ).date()
    if d.get("kind") in ("progress", "quote") and cfg.get("src") != "db":
        w = compute(None, None, b.get("id") or "", d, today=today)
    v = value_text(d, w, today)
    head = "**" + (d.get("title") or kind_name(d.get("kind"))).replace("*", "\\*") + ":**"
    if v is None:
        return head + " –"
    line = head + " " + v.replace("\n", " ").replace("\u00a0", " ")
    if server_kind(d):
        line += " (" + t("Stand {date}", date=i18n.day_str(today)) + ")"
    return line


# =====================================================================================================
# Endpunkte
# =====================================================================================================
@router.get("/api/notes/{nid}/widgets")
def api_widgets(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return page_widgets(uid, nid, request.query_params)


@router.post("/api/notes/{nid}/widgets/preview")
async def api_widget_preview(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(preview, uid, nid, b.get("block"), dict(request.query_params))

