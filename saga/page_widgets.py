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
  rechnet beim naechsten Abruf sofort neu.
- 2.5 Paket 4 (Etappe 2, Faehigkeit widgets2): weather (saga.weather, Server ruft Open-Meteo ab), habit (Datenbank: Zeile je
  Tag, Haekchen je Gewohnheit; Abhaken ueber saga.widgets_api), upcoming (IMMER Daten der betrachtenden Person: CalDAV,
  Saga-Aufgaben, Datums-Eigenschaften gewaehlter Datenbanken - nie zwischengespeichert, nie veroeffentlicht/foederiert),
  chart (saga.dbcalc.chart - dieselbe Logik wie die Datenbank-Ansicht "chart"). Diese Arten tragen im W zusaetzlich
  "data" (strukturiert je Art, Vertrag docs/APP-API.md)."""
import hashlib
import json
import random
import re
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
from saga.notes import PALETTE, _iter_blocks, ensure_ids, fix_block_ids
from saga.sharing import Access, access, is_remote

router = APIRouter()

KINDS = ("clock", "countdown", "progress", "metric", "quote", "weather", "habit", "upcoming", "chart")
KINDS2 = ("weather", "habit", "upcoming", "chart")            # 2.5 Paket 4: W mit "data", Foederation braucht widgets2
SIZES = ("s", "m", "l")
STYLES = ("card", "plain")
AGGS = dbcalc.AGGS
TASK_AGGS = ("open", "due_today", "overdue", "done_week")     # fix27 E3: metric mit src "tasks"
FMTS = ("num", "pct", "eur")
ERRORS = ("no_access", "gone", "bad_cfg", "too_big", "disabled", "unavailable")     # disabled/unavailable: Wetter
HABITS_MAX, HABIT_RANGES = 6, (7, 14, 30)
UP_DBS_MAX, UP_DAYS_MAX = 5, 14
PLACE_MAX = 80
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
    if kind == "weather":
        lat, lon = c.get("lat"), c.get("lon")
        ok = _num(lat) and _num(lon) and -90 <= lat <= 90 and -180 <= lon <= 180
        d = c.get("days")
        return {"place": _plain(c.get("place"), PLACE_MAX), "lat": round(float(lat), 2) if ok else None,
                "lon": round(float(lon), 2) if ok else None, "units": "imperial" if c.get("units") == "imperial" else "metric",
                "days": d if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 7 else 5}
    if kind == "habit":
        hs = list(dict.fromkeys(x for x in (_id(h) for h in (c.get("habits") if isinstance(c.get("habits"), list) else [])) if x))
        out = {"db": _db_id(c.get("db")), "view": _id(c.get("view")), "date": _id(c.get("date")), "habits": hs[:HABITS_MAX],
               "range": c.get("range") if c.get("range") in HABIT_RANGES else 7}
        g = c.get("goal")
        if isinstance(g, int) and not isinstance(g, bool) and 1 <= g <= 7:
            out["goal"] = g
        return out
    if kind == "upcoming":
        d, lim = c.get("days"), c.get("limit")
        dbs = []
        for x in (c.get("dbs") if isinstance(c.get("dbs"), list) else [])[:UP_DBS_MAX * 2]:
            if isinstance(x, dict) and _db_id(x.get("db")) and _id(x.get("date")):
                dbs.append({"db": x["db"], "view": _id(x.get("view")), "date": _id(x["date"])})
        return {"days": d if isinstance(d, int) and not isinstance(d, bool) and 1 <= d <= UP_DAYS_MAX else 7,
                "cal": c.get("cal") is not False, "tasks": c.get("tasks") is not False, "overdue": c.get("overdue") is not False,
                "dbs": dbs[:UP_DBS_MAX], "limit": lim if isinstance(lim, int) and not isinstance(lim, bool) and 5 <= lim <= 30 else 10}
    if kind == "chart":
        return dict(dbcalc.clean_chart_spec(c), db=_db_id(c.get("db")), view=_id(c.get("view")))
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
    """Eindeutige ids (Werte werden je Block-id geliefert). Seit 2.5 (P3-E1) fuer alle Bloecke: notes.fix_block_ids."""
    fix_block_ids(blocks)
    return blocks


def server_kind(d):
    """Rechnet der Server dieses Widget (Wert aus GET .../widgets)?"""
    if not isinstance(d, dict):
        return False
    k, c = d.get("kind"), d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
    return k in ("metric", "quote") or k in KINDS2 or (k == "progress" and c.get("src") in ("db", "tasks"))


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
def _W(kind, value=None, mx=None, text=None, fmt=None, error=None, as_of=None, data=None):
    w = {"kind": kind, "value": value, "max": mx, "text": text, "fmt": fmt,
         "as_of": as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "error": error}
    if kind in KINDS2:                       # 2.5 Paket 4: strukturierte Werte (null bei Fehler)
        w["data"] = None if error else data
    return w


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


def _source(c, uid, dbid, acc, public_owner=None, roles=None):
    """-> (eigentuemer, schema, shared). Recht des Betrachters an der Quell-DB; public_owner: Veroeffentlichen (nur eigene DB).
    roles (dict, optional): bekommt roles[dbid] = Rolle des Betrachters (Gewohnheiten: abhaken ab 'Bearbeiten')."""
    if dbid is None:
        raise _Fail("bad_cfg")
    if public_owner is not None:
        r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL AND archived IS NULL",
                      (dbid, public_owner)).fetchone()
        if not r:
            raise _Fail("gone")
        if roles is not None:
            roles[dbid] = "view"
        return public_owner, _json_load(r[0], {"props": []}), True
    owner, role, err = access(c, uid, dbid, "view", acc)
    if err is not None:
        raise _Fail("no_access")                   # weder Name noch Hinweis, ob es die Datenbank gibt
    r = c.execute("SELECT kind, schema, deleted_at FROM note WHERE id=? AND uid=?", (dbid, owner)).fetchone()
    if not r or r[2] is not None:
        raise _Fail("gone")
    if r[0] != "database":
        raise _Fail("bad_cfg")
    if roles is not None:
        roles[dbid] = role
    return owner, _json_load(r[1], {"props": []}), role != "owner"


def _rows(c, uid, owner, dbid, schema, shared, view, tz, filters=None, public=False):
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
    if filters is not None:                         # eingebettete Ansicht (eigene Filter im Block, Diagramm-Endpunkt)
        args["filters"] = filters
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


# =====================================================================================================
# 2.5 Paket 4: Wetter, Gewohnheiten, Anstehendes, Diagramm
# =====================================================================================================
def _weather(cfg, public_owner):
    from saga import weather
    if cfg.get("lat") is None or cfg.get("lon") is None:
        raise _Fail("bad_cfg")
    days = cfg.get("days", 5)
    if public_owner is not None:                       # Besucher loesen nie einen Abruf aus: nur der letzte Stand
        if not weather.enabled():
            raise _Fail("disabled")
        data = weather.cached(cfg["lat"], cfg["lon"], cfg.get("units") or "metric", days)
        if data is None:
            raise _Fail("unavailable")
    else:
        try:
            data = weather.forecast(cfg["lat"], cfg["lon"], cfg.get("units") or "metric", days)
        except weather.WeatherError as ex:
            raise _Fail(ex.code)
    data["place"] = cfg.get("place") or ""
    as_of = data.pop("as_of", None)
    return _W("weather", data["now"].get("t"), text=data["place"] or None, as_of=as_of, data=data)


def _prop_of(schema, pid, types):
    p = next((x for x in (schema.get("props") or []) if isinstance(x, dict) and x.get("id") == pid), None)
    if p is None or p.get("type") not in types:
        raise _Fail("bad_cfg")
    return p


def row_day(q, p, r):
    """Tag (JJJJ-MM-TT, in der Zeitzone der Anfrage) einer Datums-Eigenschaft einer Zeile oder None (auch mit Uhrzeit)."""
    v = q.prop_value(p, r)
    if p.get("type") in ("created", "edited"):
        d = q.iso_day(v) if isinstance(v, (int, float)) else None
        return d if d and _day_ok(d) else None
    if not isinstance(v, str) or len(v) < 10:
        return None
    sp = q.day_span(v)
    if sp is None:
        return None
    try:
        return date.fromordinal(sp[0] + 719163).isoformat()
    except (ValueError, OverflowError):
        return None


def habit_grid(schema, rows, cfg, today, tz):
    """Gewohnheiten (P4-E1): je Gewohnheit Tage-Raster des Zeitraums, Serie (Tage in Folge bis heute bzw. gestern) und
    Quote (ganze Prozent der Tage im Zeitraum). Mehrere Zeilen am selben Tag = ODER."""
    q = dbquery.Query(schema, tz=tz, today=today)
    dp = _prop_of(schema, cfg.get("date"), ("date",))
    hp = [_prop_of(schema, h, ("checkbox",)) for h in cfg.get("habits") or []]
    if not hp:
        raise _Fail("bad_cfg")
    rng = cfg.get("range") if cfg.get("range") in HABIT_RANGES else 7
    days = [(today - timedelta(days=rng - 1 - i)).isoformat() for i in range(rng)]
    done = {p["id"]: set() for p in hp}
    for r in rows:
        day = row_day(q, dp, r)
        if day is None or day > today.isoformat():
            continue
        pv = r.get("props") if isinstance(r.get("props"), dict) else {}
        for p in hp:
            if pv.get(p["id"]) is True:
                done[p["id"]].add(day)
    out = []
    mon = today - timedelta(days=today.weekday())
    for p in hp:
        ds = done[p["id"]]
        grid = [x in ds for x in days]
        k, cur = 0, today if today.isoformat() in ds else today - timedelta(days=1)
        while cur.isoformat() in ds and k < 3660:
            k += 1
            cur -= timedelta(days=1)
        week = sum(1 for i in range(7) if (mon + timedelta(days=i)).isoformat() in ds)
        out.append({"pid": p["id"], "name": str(p.get("name") or ""), "done": grid, "streak": k,
                    "pct": int(dbquery.js_round(sum(grid) / rng * 100)), "week": week})
    data = {"days": days, "habits": out, "today": today.isoformat()}
    if cfg.get("goal"):
        data["goal"] = cfg["goal"]
    return data


def _upcoming(c, uid, cfg, today, tz, acc):
    """Anstehendes (P4-E3): IMMER die Daten der betrachtenden Person - eigene Kalender (CalDAV), eigene Saga-Aufgaben,
    Zeilen gewaehlter Datenbanken mit ihren Rechten. Nie zwischengespeichert (persoenlich)."""
    days = cfg.get("days") or 7
    end = today + timedelta(days=days)                    # [heute, heute + days)
    t0, t1 = today.isoformat(), end.isoformat()
    items, errors = [], 0
    if cfg.get("cal", True):
        from saga import calendars
        try:
            evs, errs = calendars._cal_collect(uid, today, end)
        except Exception:                                 # Kalender sind Beiwerk - Fehler zaehlen, Widget bleibt
            evs, errs = [], [1]
        errors += len(errs)
        for e in evs:
            st = str(e.get("start") or "")
            items.append({"k": "event", "title": str(e.get("summary") or "")[:200], "start": st, "end": str(e.get("end") or "") or None,
                          "allday": bool(e.get("allday")), "color": e.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(e.get("color") or "")) else None,
                          "cal": str(e.get("calendar") or "")[:80], "note": None, "task": None, "overdue": False})
    if cfg.get("tasks", True):
        for tid, text, due in c.execute("SELECT id, text, due FROM task WHERE uid=? AND done=0 AND due IS NOT NULL", (uid,)).fetchall():
            if not _day_ok(due) or due >= t1 or (due < t0 and not cfg.get("overdue", True)):
                continue
            items.append({"k": "task", "title": str(text or "")[:200], "start": due, "end": None, "allday": True, "color": None,
                          "cal": None, "note": None, "task": tid, "overdue": due < t0})
    for src in cfg.get("dbs") or []:
        try:
            owner, schema, shared = _source(c, uid, src.get("db"), acc)
            rows = _rows(c, uid, owner, src["db"], schema, shared, src.get("view") or "", tz)
            dp = _prop_of(schema, src.get("date"), ("date", "created", "edited"))
        except _Fail:
            errors += 1
            continue
        q = dbquery.Query(schema, tz=tz, today=today)
        done = dbcalc.done_test(schema)
        for r in rows:
            day = row_day(q, dp, r)
            if day is None or day >= t1:
                continue
            pv = r.get("props") if isinstance(r.get("props"), dict) else {}
            late = day < t0
            if late and (not cfg.get("overdue", True) or done(pv)):
                continue
            if late:
                sp = q.day_span(q.prop_value(dp, r))
                if sp and sp[1] >= today.toordinal() - 719163:   # Zeitraum laeuft noch: nicht ueberfaellig
                    late = False
            raw = q.prop_value(dp, r)
            st = raw.split("/")[0] if isinstance(raw, str) else day
            en = raw.split("/")[1] if isinstance(raw, str) and "/" in raw else None
            items.append({"k": "row", "title": str(r.get("title") or "")[:200] or t("Unbenannt"), "start": st if isinstance(st, str) else day,
                          "end": en, "allday": not (isinstance(st, str) and "T" in st), "color": None, "cal": None,
                          "note": r.get("id"), "task": None, "overdue": late})
    items.sort(key=lambda x: (x["start"][:10], 0 if x["allday"] else 1, x["start"], x["title"]))
    lim = cfg.get("limit") or 10
    data = {"items": items[:lim], "more": max(0, len(items) - lim), "errors": errors, "days": days, "from": t0}
    return _W("upcoming", len(items), data=data)


def compute(c, uid, bid, d, tz=None, acc=None, public_owner=None, today=None, public_labels=None):
    """Wert eines Widgets (W, siehe Modulkopf) mit den Rechten von uid. d: bereinigtes data.
    public_labels(ids) (Veroeffentlichen): Titel verknuepfter Seiten nur, soweit sie selbst oeffentlich erreichbar sind."""
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
        if kind == "weather":
            return _weather(cfg, public_owner)
        if kind == "upcoming":
            if public_owner is not None or uid is None or is_remote(uid):
                raise _Fail("no_access")            # persoenlich: nie veroeffentlicht, nie ueber Servergrenzen
            return _upcoming(c, uid, cfg, today, tz, acc or Access(c, uid))
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
        pub = public_owner is not None
        if kind == "habit":
            roles = {}
            owner, schema, shared = _source(c, uid, cfg.get("db"), acc, public_owner, roles)
            rows = _rows(c, uid if public_owner is None else public_owner, owner, cfg["db"], schema, shared, cfg.get("view") or "", tz,
                         public=pub)
            data = habit_grid(schema, rows, cfg, today, tz)
            data["can_check"] = public_owner is None and roles.get(cfg["db"]) in ("owner", "edit")
            return _W(kind, sum(1 for h in data["habits"] if h["done"][-1]), len(data["habits"]), data=data)
        rows = _rows(c, uid if public_owner is None else public_owner, owner, cfg["db"], schema, shared, cfg.get("view") or "", tz,
                     public=pub)
        if kind == "chart":
            viewer = uid if public_owner is None else public_owner
            try:
                lab = public_labels if pub else _labels_fn(c, viewer, owner)
                res = dbcalc.chart(schema, rows, cfg, labels=lab or (lambda ids: {}), tz=tz, today=today, untitled=t("Unbenannt"))
            except dbcalc.Fail as f:
                raise _Fail(f.code)
            return _W(kind, res["total"], cfg.get("goal"), fmt=res["fmt"], data=res)
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


def page_widgets(uid, nid, params, kinds2=True):
    """Werte aller vom Server gerechneten Widgets einer gespeicherten Seite (fuer Web/Apps und die Foederation).
    kinds2=False (Gegenstelle ohne widgets2): Arten aus KINDS2 fehlen in der Antwort (Clients: "nur auf dem Ursprungsserver")."""
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
            doc = _json_load(ensure_ids(r[0], r[1], nid), {})      # dieselben ids wie GET /api/notes/{id}
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
                if not kinds2 and d.get("kind") in KINDS2:
                    continue
                if i >= WIDGETS_MAX:
                    out[bid] = _W(d.get("kind"), error="too_big")
                    continue
                cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
                if cfg.get("pick") == "open" and d.get("kind") == "quote":
                    out[bid] = compute(c, uid, bid, d, tz, acc, today=today)
                    continue
                if d.get("kind") in ("upcoming", "weather"):  # persoenlich bzw. eigener Zwischenspeicher (saga.weather)
                    out[bid] = compute(c, uid, bid, d, tz, acc, today=today)
                    continue
                if cfg.get("src") == "tasks":                 # fix27 E3: Aufgaben - klein, immer frisch gerechnet
                    out[bid] = compute(c, uid, bid, d, tz, acc, today=today)
                    continue
                ver = None
                if cfg.get("src") == "db" or d.get("kind") in ("metric", "habit", "chart"):
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
            "quote": t("Zitat"), "weather": t("Wetter"), "habit": t("Gewohnheiten"), "upcoming": t("Anstehendes"),
            "chart": t("Diagramm")}.get(kind, t("Widget"))


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
    data = w.get("data") if isinstance(w.get("data"), dict) else None
    if data is None:
        return None
    if kind == "weather":
        u = "°F" if data.get("units") == "imperial" else "°C"
        now = data.get("now") or {}
        parts = [(data.get("place") + ": " if data.get("place") else "") + (fmt_num(now.get("t")) + " " + u if _num(now.get("t")) else "–")]
        for x in (data.get("days") or [])[1:]:
            parts.append(f"{x.get('d', '')[5:]} {fmt_num(x.get('min'))}–{fmt_num(x.get('max'))} {u}")
        return " · ".join(parts)
    if kind == "habit":
        return " · ".join(t("{name}: {pct} % ({n} {n:Tag|Tage} in Folge)", name=h.get("name") or "", pct=h.get("pct", 0), n=h.get("streak", 0))
                          for h in data.get("habits") or [])
    if kind == "upcoming":
        return t("{n} {n:Eintrag|Einträge} in den nächsten {d} {d:Tag|Tagen}", n=len(data.get("items") or []) + (data.get("more") or 0), d=data.get("days") or 7)
    if kind == "chart":
        if data.get("type") == "number" or not data.get("cats"):
            return fmt_num(data.get("total"), data.get("fmt"))
        vals = (data.get("series") or [{}])[0].get("values") or []
        return ", ".join(f"{c.get('label')}: {fmt_num(v, data.get('fmt'))}" for c, v in zip(data["cats"][:12], vals))
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



# =====================================================================================================
# 2.5 Paket 4: Gewohnheit abhaken, Ortssuche (Wetter), Diagramm-Ansicht einer Datenbank
# =====================================================================================================
def _page_block(c, uid, nid, bid, acc):
    """Widget-Block bid der gespeicherten Seite nid (Recht: Lesen) -> (data, fehlerantwort)."""
    owner, role, err = access(c, uid, nid, "view", acc)
    if err is not None:
        return None, err
    r = c.execute("SELECT body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
    if not r or r[1] != "blocks":
        return None, _bad("not found", 404)
    doc = _json_load(ensure_ids(r[0], r[1], nid), {})
    try:
        b = next((x for x in _iter_blocks(doc.get("blocks") or []) if x.get("id") == bid and x.get("type") == "widget"), None)
    except ValueError:
        b = None
    if b is None or not isinstance(b.get("data"), dict):
        return None, _bad("not found", 404)
    return b["data"], None


def habit_check(uid, nid, bid, b, params=None):
    """POST /api/notes/{nid}/widgets/{bid}/habit {"pid", "day", "on"}: Haekchen einer Gewohnheit an einem Tag setzen.
    Recht: Lesen an der Seite UND Bearbeiten an der Quell-Datenbank. Tag: heute bis heute - (Zeitraum - 1) in tz. Setzt das
    Haekchen in der Zeile mit diesem Datum (Ansicht des Widgets; an: erste passende Zeile, sonst neue Zeile mit Titel =
    Datum, ohne Standard-Vorlage; aus: in allen Zeilen dieses Tages) ueber den normalen Speicherweg (Live, Versionen).
    -> {"widget": W}."""
    from saga import pages
    try:
        tz, _ = _tz_param(params or {})
    except ValueError:
        return _bad("bad parameter: tz")
    if not isinstance(bid, str) or not _BID.fullmatch(bid) or not isinstance(b, dict):
        return _bad(t("ungültige Angabe"))
    pid, day, on = b.get("pid"), b.get("day"), b.get("on")
    if not isinstance(pid, str) or not _day_ok(day) or not isinstance(on, bool):
        return _bad(t("ungültige Angabe"))
    tz = tz or TZ
    today = datetime.now(tz).date()
    with _db() as c:
        acc = Access(c, uid)
        d, err = _page_block(c, uid, nid, bid, acc)
        if err is not None:
            return err
        cfg = d.get("cfg") if isinstance(d.get("cfg"), dict) else {}
        if d.get("kind") != "habit" or pid not in (cfg.get("habits") or []) or not cfg.get("date"):
            return _bad(t("ungültige Angabe"))
        rng = cfg.get("range") if cfg.get("range") in HABIT_RANGES else 7
        if not (today - timedelta(days=rng - 1)).isoformat() <= day <= today.isoformat():
            return _bad(t("ungültige Angabe"))
        roles = {}
        try:
            owner, schema, shared = _source(c, uid, cfg.get("db"), acc, None, roles)
            _prop_of(schema, cfg["date"], ("date",))
            _prop_of(schema, pid, ("checkbox",))
            rows = _rows(c, uid, owner, cfg["db"], schema, shared, cfg.get("view") or "", tz)
        except _Fail as f:
            return _bad(t("Dafür fehlt dir die Berechtigung.") if f.code == "no_access" else t("ungültige Angabe"),
                        403 if f.code == "no_access" else 400)
        if roles.get(cfg["db"]) not in ("owner", "edit"):
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        q = dbquery.Query(schema, tz=tz, today=today)
        dp = _prop_of(schema, cfg["date"], ("date",))
        same = [r for r in rows if row_day(q, dp, r) == day]
    if on and not any((r.get("props") or {}).get(pid) is True for r in same):
        if same:
            res = pages._note_update(uid, same[0]["id"], {"props": {pid: True}})
        else:
            res = pages.note_create(uid, {"parent_id": cfg["db"], "title": day, "props": {cfg["date"]: day, pid: True}})
        if res.status_code != 200:
            return res
    elif not on:
        for r in same:
            if (r.get("props") or {}).get(pid) is True:
                res = pages._note_update(uid, r["id"], {"props": {pid: False}})
                if res.status_code != 200:
                    return res
    with _db() as c:
        w = compute(c, uid, bid, d, tz, Access(c, uid), today=today)
    return JSONResponse({"widget": w}, headers={"Cache-Control": "no-store"})


def db_chart(uid, dbid, params):
    """GET /api/notes/{db}/chart?view=<vid>[&tz=][&ids=1][&chart=<spec-json>&filters=<json>]: Diagramm einer Ansicht vom Typ
    "chart" (Recht: Lesen, wie /rows). chart/filters: Spec und Filter direkt (eingebettete Datenbank mit Block-Ansichten).
    ids=1: zusaetzlich "ids" (Zeilen je Kategorie, fuer den Klick auf einen Balken im Web).
    -> {"chart": Ergebnis, "view": vid} oder {"chart": null, "error": code}."""
    try:
        tz, _ = _tz_param(params)
    except ValueError:
        return _bad("bad parameter: tz")
    vid = params.get("view") or ""
    if len(vid) > 64:
        return _bad("bad parameter: view")
    spec = filters = None
    try:                                             # eingebettete Datenbank mit eigenen Ansichten: Spec und Filter im Aufruf
        if params.get("chart"):
            spec = json.loads(params["chart"]) if len(params["chart"]) <= 4000 else None
            if not isinstance(spec, dict):
                raise ValueError
        if params.get("filters"):
            filters = json.loads(params["filters"]) if len(params["filters"]) <= 8000 else None
            if not isinstance(filters, list) or len(filters) > 50:
                raise ValueError
    except ValueError:
        return _bad("bad parameter: chart/filters")
    tz = tz or TZ
    today = datetime.now(tz).date()
    with _db() as c:
        acc = Access(c, uid)
        _, _, err = access(c, uid, dbid, "view", acc)
        if err is not None:
            return err
        try:
            owner, schema, shared = _source(c, uid, dbid, acc)
        except _Fail as f:
            return _bad("not found", 404) if f.code in ("gone", "bad_cfg") else _bad("not found", 404)
        view = dbquery.view_of(schema, vid)
        if spec is not None:
            view = {"id": vid or (view or {}).get("id"), "type": "chart", "chart": spec}
        if not view or view.get("type") != "chart" or not isinstance(view.get("chart"), dict):
            return JSONResponse({"chart": None, "error": "bad_cfg", "view": view.get("id") if view else None},
                                headers={"Cache-Control": "no-store"})
        try:
            rows = _rows(c, uid, owner, dbid, schema, shared, "" if spec is not None else view["id"], tz,
                         filters if filters is not None else ([] if spec is not None else None))
            res = dbcalc.chart(schema, rows, view["chart"], labels=_labels_fn(c, uid, owner), tz=tz, today=today, untitled=t("Unbenannt"),
                               with_ids=params.get("ids") == "1")
            res["as_of"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return JSONResponse({"chart": res, "view": view["id"], "error": None}, headers={"Cache-Control": "no-store"})
        except (_Fail, dbcalc.Fail) as f:
            return JSONResponse({"chart": None, "error": f.code, "view": view["id"]}, headers={"Cache-Control": "no-store"})


_geo_hits = {}
_geo_lock = threading.Lock()
GEO_PER_MIN = 30


def _geo_allow(uid):
    now = time.monotonic()
    with _geo_lock:
        if len(_geo_hits) > 5000:
            _geo_hits.clear()
        start, n = _geo_hits.get(uid, (now, 0))
        if now - start >= 60:
            start, n = now, 0
        _geo_hits[uid] = (start, n + 1)
        return n + 1 <= GEO_PER_MIN


def geocode(uid, q):
    """GET /api/widgets/geocode?q=: Ortssuche fuer das Wetter-Widget (nur wenn der Admin Wetter eingeschaltet hat)."""
    from saga import i18n, weather
    q = re.sub(r"\s+", " ", clean_line(q or "")).strip()
    if not 2 <= len(q) <= 80:
        return _bad(t("ungültige Angabe"))
    if not weather.enabled():
        return JSONResponse({"error": "disabled", "results": []}, status_code=409)
    if not _geo_allow(uid):
        return JSONResponse({"error": t("Zu viele Anfragen – bitte kurz warten.")}, status_code=429, headers={"Retry-After": "60"})
    try:
        res = weather.geocode(q, i18n.get_lang())
    except weather.WeatherError as ex:
        return JSONResponse({"error": ex.code, "results": []}, status_code=502 if ex.code == "unavailable" else 409)
    return JSONResponse({"results": res, "error": None}, headers={"Cache-Control": "no-store"})


@router.post("/api/notes/{nid}/widgets/{bid}/habit")
async def api_widget_habit(nid: int, bid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(habit_check, uid, nid, bid, b, dict(request.query_params))


@router.get("/api/notes/{nid}/chart")
def api_db_chart(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return db_chart(uid, nid, request.query_params)


@router.get("/api/widgets/geocode")
def api_geocode(request: Request, q: str = ""):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return geocode(uid, q)


# =====================================================================================================
# Foederation: W.data einer anderen Instanz pruefen (nur bekannte Felder, Typen, Laengen)
# =====================================================================================================
def _fnum(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e15 else None


def _fint(v, lo=-10 ** 9, hi=10 ** 9):
    return int(v) if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else None


def _fstr(v, n):
    return re.sub(r"\s+", " ", clean_line(v)).strip()[:n] if isinstance(v, str) else ""


def _fcolor(v):
    return v if v in PALETTE else None


def clean_remote_data(kind, d):
    """W.data von einer anderen Saga-Instanz -> bereinigte Kopie oder None. upcoming kommt nie ueber Servergrenzen."""
    if not isinstance(d, dict):
        return None
    if kind == "weather":
        now = d.get("now") if isinstance(d.get("now"), dict) else {}
        days = []
        for x in (d.get("days") if isinstance(d.get("days"), list) else [])[:8]:
            if isinstance(x, dict) and _day_ok(x.get("d")):
                days.append({"d": x["d"], "min": _fnum(x.get("min")), "max": _fnum(x.get("max")), "code": _fint(x.get("code"), 0, 999),
                             "rain": _fint(x.get("rain"), 0, 100)})
        return {"now": {"t": _fnum(now.get("t")), "code": _fint(now.get("code"), 0, 999), "wind": _fnum(now.get("wind"))},
                "days": days, "units": "imperial" if d.get("units") == "imperial" else "metric", "stale": d.get("stale") is True,
                "place": _fstr(d.get("place"), PLACE_MAX)}
    if kind == "habit":
        days = [x for x in (d.get("days") if isinstance(d.get("days"), list) else [])[:31] if _day_ok(x)]
        hs = []
        for h in (d.get("habits") if isinstance(d.get("habits"), list) else [])[:HABITS_MAX]:
            if not isinstance(h, dict) or not _id(h.get("pid")):
                continue
            done = [x is True for x in (h.get("done") if isinstance(h.get("done"), list) else [])[:len(days)]]
            hs.append({"pid": _id(h["pid"]), "name": _fstr(h.get("name"), 60), "done": done + [False] * (len(days) - len(done)),
                       "streak": _fint(h.get("streak"), 0, 100000) or 0, "pct": _fint(h.get("pct"), 0, 100) or 0,
                       "week": _fint(h.get("week"), 0, 7) or 0})
        out = {"days": days, "habits": hs, "today": d["today"] if _day_ok(d.get("today")) else (days[-1] if days else None),
               "can_check": d.get("can_check") is True}
        if _fint(d.get("goal"), 1, 7):
            out["goal"] = d["goal"]
        return out
    if kind == "chart":
        if d.get("type") not in dbcalc.CHART_TYPES:
            return None
        cats = []
        for x in (d.get("cats") if isinstance(d.get("cats"), list) else [])[:dbcalc.CHART_CATS_MAX + 1]:
            if isinstance(x, dict):
                cat = {"key": _fstr(x.get("key"), 130) if x.get("key") is not None else None, "label": _fstr(x.get("label"), 130),
                       "color": _fcolor(x.get("color"))}
                if _day_ok(x.get("start")):
                    cat["start"] = x["start"]
                cats.append(cat)
        series = []
        for x in (d.get("series") if isinstance(d.get("series"), list) else [])[:dbcalc.CHART_SERIES_MAX]:
            if isinstance(x, dict):
                vals = [_fnum(v) for v in (x.get("values") if isinstance(x.get("values"), list) else [])[:len(cats)]]
                series.append({"key": _fstr(x.get("key"), 130) if x.get("key") is not None else None, "label": _fstr(x.get("label"), 130),
                               "color": _fcolor(x.get("color")), "values": vals + [None] * (len(cats) - len(vals))})
        out = {"type": d["type"], "x_kind": "date" if d.get("x_kind") == "date" else "cat", "cats": cats, "series": series,
               "total": _fnum(d.get("total")), "fmt": d.get("fmt") if d.get("fmt") in dbcalc.CHART_FMTS else "num",
               "other": _fint(d.get("other"), 0, 10 ** 6) or 0, "truncated": d.get("truncated") is True, "rows": _fint(d.get("rows"), 0, 10 ** 7) or 0}
        if d.get("bucket") in dbcalc.CHART_BUCKETS:
            out["bucket"] = d["bucket"]
        for k in ("stack", "cumulative"):
            if d.get(k) is True:
                out[k] = True
        if _fnum(d.get("goal")) is not None:
            out["goal"] = d["goal"]
        if isinstance(d.get("as_of"), str) and re.fullmatch(r"[0-9T:\-Z+.]{10,32}", d["as_of"]):
            out["as_of"] = d["as_of"]
        return out
    return None
