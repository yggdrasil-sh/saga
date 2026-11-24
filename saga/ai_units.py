"""KI-Einheiten und Spuren (Saga 2.6, Bugs-DB 2266 K-A5, Konzept K21, M8): wie viel die KI je Person und Tag liest und
schreibt - in Einheiten, die ueberall gleich gerechnet werden - und wer gerade ans Modell darf.

Einheiten (K21): 1 Einheit ~ 4 Zeichen (ungefaehr ein Token, gleich fuer jedes Modell). Vorher schaetzt Saga
  estimate(messages, max_tokens) = ceil(Zeichen/4) + max_tokens und belegt den Pool (reserve), nachher rechnet sie ab
  (settle): ceil(Zeichen Eingabe/4) + ceil(Zeichen Ausgabe/4), der Rest geht zurueck. Ohne ein Stueck Antwort kostet eine
  Anfrage 0 (wie beim Anfragen-Zaehler). Echte Tokens des Endpunkts (Ollama prompt_eval_count/eval_count, OpenAI usage mit
  stream_options.include_usage) stehen nur zur Anzeige in real_in/real_out - gerechnet wird mit Zeichen.
Pools je Person und Tag (fest): ask 60 % (Fragen, Seiten-Hilfen, Chat-Zuruf), job 30 % (Auftraege der Kollegin),
  idea 10 % (Ideen); stt kommt mit K-F. Tagesbudget = Admin-Kontingent "Anfragen je Person und Tag" x PER_REQUEST
  x Anteil; Kontingent 0 = unbegrenzt (dann wird nur gemessen).
Uebergang ohne Bruch (Abweichung c): die Frage-Spur bleibt beim Anfragen-Zaehler (saga.ai: ai_usage, _take_quota,
  quota_left - unveraendert in /api/ai/status und 'done'); fuer ask wird hier nur gemessen, reserve lehnt nie ab.
  Durchgesetzt wird nur in job und idea: reserve -> None = "zurueckstellen, nicht verwerfen" (Aufrufer: saga/jobs.py).
  Ein laufender Auftrag (job_running=True) darf den Tagespool um genau einen Schritt ueberziehen, damit er nicht mitten
  im Plan haengt: der Schritt geht noch, solange der Pool nicht voll ist, der naechste nicht mehr.
Tabelle ai_units(uid, day, pool, units, real_in, real_out): NUR Zahlen, keine Inhalte. 90 Tage, Kontoloeschung ueber
  USER_TABLES. Admin (M8): admin_view() - Server mit weniger als 5 aktiven Konten zeigen nur Summen, sonst nur Anzahlen
  je Ampelfarbe (gruen/gelb/rot) aus den begrenzten Pools, nie Kennungen oder Einzelwerte. Stellgroesse bleibt "Anfragen/Tag", Einheiten sind ein kleiner Messwert.

Spuren (lane_take/lane_free, ein threading.Lock fuer beide): AI_MAX_PARALLEL (PARALLEL) gilt fuer Fragen wie bisher -
  eine Anfrage je Person (409), hoechstens PARALLEL Fragen gleichzeitig (503); Hintergrund (bg: Auftraege, Ideen) zaehlt
  dabei NICHT mit, Fragen werden also nie wegen Hintergrund abgelehnt, solange PARALLEL >= 2. Hintergrund bekommt
  hoechstens BG_MAX = 1 Platz und nur, wenn weniger als PARALLEL-1 Fragen laufen (bei PARALLEL 1 und 2: nur ohne
  laufende Frage). Ein laufender Hintergrund-Aufruf wird nie abgebrochen; kommen danach Fragen dazu, laufen kurz
  PARALLEL Fragen + 1 Hintergrund. Einzige Ausnahme: PARALLEL = 1 - dann belegt der Hintergrund den einen Platz und
  eine Frage bekommt solange 503 (sonst kaemen Auftraege dort nie dran). saga.ai._gate laeuft ueber dieselbe Buchhaltung.

Tests: tests/kollegin_budget.py (dazu tests/fakeai.py: fake-usage, fake-nousage)."""
import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import auth
from db import connect as _db
from saga import testclock
from saga.core import E, TZ

PER_REQUEST = 4000                                   # Einheiten je Anfrage des Kontingents (Tagesbudget = quota x 4000)
PCT = {"ask": 60, "job": 30, "idea": 10}             # feste Aufteilung in Prozent (ganzzahlig gerechnet)
SPLIT = {k: v / 100 for k, v in PCT.items()}
POOLS = tuple(PCT)
KEEP_DAYS = 90
MIN_ACCOUNTS = 5                                     # ab so vielen aktiven Konten: Ampel je Person (sonst nur Summen)
LIGHTS = ((0.8, "gruen"), (1.0, "gelb"))             # Anteil des Tagesbudgets: < 80 % gruen, < 100 % gelb, sonst rot

PARALLEL = max(1, int(E.get("AI_MAX_PARALLEL", "4")))   # = saga.ai.PARALLEL
BG_MAX = 1
STALE = 1860                                         # s: Eintrag gilt als verwaist (laengste Antwort 30 min + 1 min)


class NoBudget(Exception):
    """Pool job/idea fuer heute voll: Auftrag zurueckstellen, nicht verwerfen (saga.ai.stream_metered)."""


# --- Einheiten ---------------------------------------------------------------------------------------
def units_of(chars):
    return math.ceil(max(0, int(chars or 0)) / 4)


def chars_of(messages):
    return sum(len(str((m or {}).get("content") or "")) for m in messages or [])


def estimate(messages, max_tokens):
    """Vorab-Schaetzung: ceil(Zeichen aller Nachrichten / 4) + max_tokens (die Antwort darf so lang werden)."""
    return units_of(chars_of(messages)) + max(0, int(max_tokens or 0))


def cost(in_chars, out_chars):
    """Abrechnung: ceil(Eingabe/4) + ceil(Ausgabe/4); ohne Antwort 0."""
    return 0 if int(out_chars or 0) <= 0 else units_of(in_chars) + units_of(out_chars)


def today():
    return datetime.fromtimestamp(testclock.now(), TZ).date().isoformat()


def _cfg(cfg):
    return cfg if cfg is not None else (auth.settings().get("ai") or {})


def budget(uid, pool, cfg=None):
    """Tagesbudget eines Pools in Einheiten (None = unbegrenzt, nur gemessen). uid fuer spaetere Regeln je Gruppe."""
    if pool not in PCT:
        raise ValueError(f"unbekannter Pool: {pool}")
    quota = int(_cfg(cfg).get("quota") or 0)
    return quota * PER_REQUEST * PCT[pool] // 100 if quota > 0 else None


@dataclass
class Res:
    """Eine Belegung (reserve -> settle). Gebucht wird immer auf den Tag der Belegung."""
    uid: str
    pool: str
    day: str
    est: int
    settled: bool = False
    units: int = 0


def _ensure(c, uid, day, pool):
    if c.execute("INSERT INTO ai_units (uid, day, pool, units, real_in, real_out) VALUES (?, ?, ?, 0, 0, 0) "
                 "ON CONFLICT (uid, day, pool) DO NOTHING", (uid, day, pool)).rowcount:
        old = (date.fromisoformat(day) - timedelta(days=KEEP_DAYS)).isoformat()      # einmal am Tag: alte Zeilen weg
        c.execute("DELETE FROM ai_units WHERE uid=? AND day<?", (uid, old))


def reserve(uid, pool, est, *, job_running=False, cfg=None):
    """Schaetzung est im Pool belegen -> Res oder None (Pool job/idea voll: zurueckstellen, nicht verwerfen).
    ask lehnt nie ab (nur gemessen, Abweichung c). job_running: laufender Auftrag darf genau einen Schritt ueberziehen."""
    if pool not in PCT:
        raise ValueError(f"unbekannter Pool: {pool}")
    est, day = max(0, int(est or 0)), today()
    cap = None if pool == "ask" else budget(uid, pool, cfg)
    with _db() as c, c:
        _ensure(c, uid, day, pool)
        if cap is None:
            c.execute("UPDATE ai_units SET units = units + ? WHERE uid=? AND day=? AND pool=?", (est, uid, day, pool))
        else:
            # eine Anweisung: Zeilensperre, Bedingung wird nach dem Warten neu geprueft (gleichzeitige Schritte)
            r = c.execute("UPDATE ai_units SET units = units + ? WHERE uid=? AND day=? AND pool=? "
                          "AND (units + ? <= ? OR (? = 1 AND units < ?)) RETURNING units",
                          (est, uid, day, pool, est, cap, 1 if job_running else 0, cap)).fetchone()
            if r is None:
                return None
    return Res(uid, pool, day, est)


def settle(res, in_chars, out_chars, real_in=None, real_out=None):
    """Nachher abrechnen: Kosten aus Zeichen (ohne Antwort 0), Rest der Schaetzung zurueck; echte Tokens nur dazuzaehlen.
    Mehrfacher Aufruf zaehlt einmal. -> abgerechnete Einheiten."""
    if res is None or res.settled:
        return res.units if res else 0
    res.settled = True
    res.units = cost(in_chars, out_chars)
    ri = max(0, int(real_in or 0)) if res.units else 0
    ro = max(0, int(real_out or 0)) if res.units else 0
    with _db() as c, c:
        c.execute("UPDATE ai_units SET units = GREATEST(0, units + ?), real_in = real_in + ?, real_out = real_out + ? "
                  "WHERE uid=? AND day=? AND pool=?", (res.units - res.est, ri, ro, res.uid, res.day, res.pool))
    return res.units


def cancel(res):
    """Belegung ohne Modellaufruf zurueckgeben (= settle ohne Antwort)."""
    return settle(res, 0, 0)


def used(uid, day=None):
    """-> {pool: {units, real_in, real_out}} fuer einen Tag (Standard heute)."""
    with _db() as c:
        rows = c.execute("SELECT pool, units, real_in, real_out FROM ai_units WHERE uid=? AND day=?", (uid, day or today())).fetchall()
    out = {p: {"units": 0, "real_in": 0, "real_out": 0} for p in POOLS}
    for p, u, ri, ro in rows:
        out[p] = {"units": int(u), "real_in": int(ri), "real_out": int(ro)}
    return out


def left(uid, cfg=None):
    """Fuer /api/ai/status (Messwert, alle Pools zusammen): {left, of} - beide None bei unbegrenztem Kontingent."""
    cfg = _cfg(cfg)
    of = sum(budget(uid, p, cfg) or 0 for p in POOLS) if int(cfg.get("quota") or 0) > 0 else None
    if of is None:
        return {"left": None, "of": None}
    n = sum(v["units"] for v in used(uid).values())
    return {"left": max(0, of - n), "of": of}


LIMITED = ("job", "idea")                            # Pools, deren Einheiten durchgesetzt werden (Fragen: nur gemessen)


def light(units, of):
    if not of:
        return "gruen"
    r = units / of
    return next((name for lim, name in LIGHTS if r < lim), "rot")


def admin_view(cfg=None, accounts=None):
    """Admin-Bereich (M8, K21), nur Zahlen von heute: {day, accounts, people, units: {pool: n}, total, real_in, real_out,
    lights}. lights = None bei weniger als MIN_ACCOUNTS aktiven Konten (nur Summen), sonst nur Anzahlen je Farbe
    {gruen, gelb, rot} - nie eine Kennung oder Einheiten einer Person (K21: Admin sieht keine Einzelpersonen). Die Ampel
    rechnet nur mit den Pools, die wirklich begrenzt werden (LIMITED: Auftraege, Ideen) - Fragen sind nur gemessen und
    werden ueber "Anfragen/Tag" begrenzt. accounts: Zahl aktiver Konten (Standard: aus der Datenbank)."""
    cfg, day = _cfg(cfg), today()
    with _db() as c:
        rows = c.execute("SELECT uid, pool, units, real_in, real_out FROM ai_units WHERE day=?", (day,)).fetchall()
        if accounts is None:
            accounts = int(c.execute("SELECT count(*) FROM users WHERE disabled=0").fetchone()[0])
    sums, per, lim = {p: 0 for p in POOLS}, {}, {}
    rin = rout = 0
    for uid, pool, u, ri, ro in rows:
        sums[pool] = sums.get(pool, 0) + int(u)
        per[uid] = per.get(uid, 0) + int(u)
        if pool in LIMITED:
            lim[uid] = lim.get(uid, 0) + int(u)
        rin, rout = rin + int(ri), rout + int(ro)
    people = len([u for u, n in per.items() if n > 0])
    out = {"day": day, "accounts": accounts, "people": people, "units": sums, "total": sum(sums.values()),
           "real_in": rin, "real_out": rout, "lights": None}
    if accounts >= MIN_ACCOUNTS:
        of = sum(budget("", p, cfg) or 0 for p in LIMITED) if int(cfg.get("quota") or 0) > 0 else None
        counts = {"gruen": 0, "gelb": 0, "rot": 0}
        for n in lim.values():
            if n > 0:
                counts[light(n, of)] += 1
        out["lights"] = counts
    return out


# --- Spuren --------------------------------------------------------------------------------------------
_lane_lock = threading.Lock()
LANES = {"ask": {}, "bg": {}}                        # Spur -> {schluessel: Beginn (monotonic)}


def _prune(now, stale):
    for lane in LANES.values():
        for k in [k for k, t0 in lane.items() if now - t0 > stale]:
            lane.pop(k, None)


def lane_try(lane, key, *, stale=None, parallel=None):
    """Platz belegen -> None (belegt) oder Grund: 'busy' (dieser Schluessel laeuft schon), 'full' (Spur voll).
    Pruefen und Belegen unter einem Lock (Fragen aus der Event-Schleife, Hintergrund aus Worker-Threads)."""
    if lane not in LANES:
        raise ValueError(f"unbekannte Spur: {lane}")
    p = max(1, int(parallel or PARALLEL))
    now = time.monotonic()
    with _lane_lock:
        _prune(now, stale or STALE)
        asks, bg = LANES["ask"], LANES["bg"]
        if key in LANES[lane]:
            return "busy"
        if lane == "ask":
            if len(asks) >= p or (p == 1 and bg):
                return "full"
        elif len(bg) >= BG_MAX or len(asks) >= max(1, p - 1):
            return "full"
        LANES[lane][key] = now
    return None


def lane_take(lane, key, *, stale=None, parallel=None):
    """Wie lane_try -> True, wenn belegt. Danach MUSS lane_free folgen."""
    return lane_try(lane, key, stale=stale, parallel=parallel) is None


def lane_free(lane, key):
    with _lane_lock:
        LANES[lane].pop(key, None)


def lanes():
    """Momentaufnahme {ask: n, bg: n} (Admin, Tests)."""
    with _lane_lock:
        return {k: len(v) for k, v in LANES.items()}
