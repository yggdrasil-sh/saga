"""Diagnose-Speicher (2.5, Admin -> Diagnose): Meldungen der Clients zusaetzlich zur Log-Zeile in der Datenbank.

Quellen (je angenommenem POST genau ein Eintrag, Inhalt exakt wie die Log-Zeile - schon bereinigt, Konto nur als Hash):
  clientlog  - POST /api/client/log   (saga/clientlog.py)
  clientdiag - POST /api/client/diag  (saga/clientlog.py, ref = Kennung D-XXXXX)
  pmdiag     - POST /api/diag         (saga/pmdiag.py)
  calldiag   - POST /api/calls/diag   (saga/calldiag.py, ref = Anruf-ID gekuerzt)
Die Log-Zeilen bleiben unveraendert. Keine IP-Adressen, keine Namen, keine Konto-IDs.

Schreiben bremst die Anfrage nicht: record() legt den Eintrag nur in einen begrenzten Puffer (ohne Datenbank); ein
Hintergrund-Thread schreibt gesammelt (executemany). Datenbankfehler werden geloggt und der Stapel verworfen - die Anfrage
merkt davon nichts. Aufbewahrung diag.keep_days (Standard 14, 1-90) und Obergrenze diag.max_rows (Standard 200 000):
purge() im stuendlichen Wartungslauf (app.py _gc_loop), zusaetzlich nach vielen neuen Eintraegen aus dem Schreib-Thread.
Die Schalter der Quellen (diag.clientlog, editor.diag, calls.diag) gelten weiter: aus -> 404, also auch nichts gespeichert."""
import collections
import json
import re
import threading
import time

import auth
from db import connect as _db

KINDS = ("clientlog", "clientdiag", "pmdiag", "calldiag")
KEEP_DEF, KEEP_MIN, KEEP_MAX = 14, 1, 90
ROWS_DEF, ROWS_MIN, ROWS_MAX = 200000, 1000, 2000000
BUF_MAX = 5000                                      # Puffer im Speicher (aelteste fallen weg, wenn die DB haengt)
FLUSH_S = 1.0
PURGE_EVERY = 5000                                  # nach so vielen neuen Eintraegen sofort aufraeumen (Deckel)

_buf = collections.deque(maxlen=BUF_MAX)
_lock = threading.Lock()                            # ein Schreibvorgang zur Zeit (Thread und flush() aus dem Admin-Bereich)
_wake = threading.Event()
_start_lock = threading.Lock()                      # nur fuer das Starten des Threads (record() wartet nie auf die DB)
_st = {"t": None, "stop": False, "since_purge": 0, "dropped": 0}


def cfg():
    d = auth.settings().get("diag") or {}
    return {"keep_days": clamp(d.get("keep_days"), KEEP_DEF, KEEP_MIN, KEEP_MAX),
            "max_rows": clamp(d.get("max_rows"), ROWS_DEF, ROWS_MIN, ROWS_MAX)}


def clamp(v, dflt, lo, hi):
    try:
        v = int(v)
    except (TypeError, ValueError):
        return dflt
    return max(lo, min(hi, v))


def record(kind, rec, ref=None, client=None):
    """Eintrag vormerken (blockiert nie, wirft nie). rec = genau das Objekt der Log-Zeile."""
    try:
        if kind not in KINDS or not isinstance(rec, dict):
            return
        if len(_buf) >= BUF_MAX:
            _st["dropped"] += 1
        _buf.append((time.time(), kind, (str(ref)[:64] if ref else None), (str(rec.get("acct"))[:32] if rec.get("acct") else None),
                     (str(client)[:80] if client else None), json.dumps(rec, separators=(",", ":"), ensure_ascii=False)))
        _start()
        _wake.set()
    except Exception as ex:  # noqa: BLE001 - Diagnose darf die Anfrage nie stoeren
        print(f"diagstore: record: {type(ex).__name__}", flush=True)


def flush():
    """Puffer in die Datenbank schreiben -> Anzahl. Fehler: Stapel verwerfen, loggen, nie werfen."""
    with _lock:
        rows = []
        while _buf:
            try:
                rows.append(_buf.popleft())
            except IndexError:
                break
        if not rows:
            return 0
        try:
            with _db() as c, c:
                c.executemany("INSERT INTO diag_event (ts, kind, ref, acct, client, data) VALUES (?, ?, ?, ?, ?, ?::jsonb)", rows)
        except Exception as ex:  # noqa: BLE001 - z. B. Datenbank kurz weg: Meldungen verwerfen (stehen ja im Log)
            print(f"diagstore: {len(rows)} Meldung(en) nicht gespeichert: {type(ex).__name__}", flush=True)
            return 0
        _st["since_purge"] += len(rows)
    if _st["since_purge"] >= PURGE_EVERY:
        try:
            purge()
        except Exception as ex:  # noqa: BLE001
            print(f"diagstore: purge: {type(ex).__name__}", flush=True)
    return len(rows)


def purge(now=None):
    """Abgelaufene Eintraege und alles ueber der Obergrenze (aelteste zuerst) loeschen -> Anzahl."""
    k = cfg()
    now = time.time() if now is None else now
    n = 0
    with _db() as c, c:
        n += c.execute("DELETE FROM diag_event WHERE ts < ?", (now - k["keep_days"] * 86400,)).rowcount or 0
        row = c.execute("SELECT id FROM diag_event ORDER BY id DESC OFFSET ? LIMIT 1", (k["max_rows"],)).fetchone()
        if row:
            n += c.execute("DELETE FROM diag_event WHERE id <= ?", (row[0],)).rowcount or 0
    _st["since_purge"] = 0
    return n


def delete_all():
    flush()
    with _db() as c, c:
        return c.execute("DELETE FROM diag_event").rowcount or 0


def _loop():
    while not _st["stop"]:
        _wake.wait(30)
        _wake.clear()
        time.sleep(FLUSH_S)                         # kurz sammeln: mehrere Meldungen in einem Schreibvorgang
        try:
            flush()
        except Exception as ex:  # noqa: BLE001 - naechster Durchlauf
            print(f"diagstore: {type(ex).__name__}", flush=True)


def _start():
    t = _st["t"]
    if t is None or not t.is_alive():
        with _start_lock:
            if _st["t"] is None or not _st["t"].is_alive():
                _st["stop"] = False
                _st["t"] = threading.Thread(target=_loop, name="saga-diagstore", daemon=True)
                _st["t"].start()


def stop():
    """Beim Beenden: Rest schreiben."""
    _st["stop"] = True
    _wake.set()
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass


# --- Lesen (Admin-Bereich) ----------------------------------------------------------------------------------------------
SINCE = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": None}


def _where(kind=None, since=None, q=None, now=None):
    w, a = [], []
    if kind in KINDS:
        w.append("kind = ?")
        a.append(kind)
    s = SINCE.get(since or "all")
    if s:
        w.append("ts >= ?")
        a.append((time.time() if now is None else now) - s)
    q = norm_q(q)
    if q:
        # Kennung D-XXXXX (exakt), Anfang einer Anruf-ID (Hex) oder Konto-Hash
        w.append("(ref = ? OR ref LIKE ? OR acct = ?)")
        a += [q, q + "%" if _HEX.fullmatch(q) else q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"), q]
    return (" WHERE " + " AND ".join(w)) if w else "", a, q


_DID = re.compile(r"[Dd]-[0-9A-Za-z]{5}")
_HEX = re.compile(r"[0-9a-f]{4,32}")


def norm_q(q):
    """Suchtext: D-Kennung in Grossbuchstaben (wie vergeben), sonst klein (Anruf-ID und Konto-Hash sind Hex)."""
    q = " ".join(str(q or "").split())[:64]
    return q.upper() if _DID.fullmatch(q) else q.lower()


def _row(r):
    d = r[6]
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = {}
    return {"id": r[0], "ts": r[1], "kind": r[2], "ref": r[3], "acct": r[4], "client": r[5], "data": d}


COLS = "id, ts, kind, ref, acct, client, data"


def query(kind=None, since=None, q=None, page=1, per=100):
    """-> (zeilen, gesamt). Exakte Treffer der Kennung zuerst, sonst neueste zuerst."""
    w, a, q = _where(kind, since, q)
    order = "ORDER BY (coalesce(ref, '') = ?) DESC, ts DESC, id DESC" if q else "ORDER BY ts DESC, id DESC"
    oa = [q] if q else []
    page = max(1, int(page or 1))
    with _db() as c:
        total = c.execute("SELECT count(*) FROM diag_event" + w, a).fetchone()[0]
        rows = c.execute(f"SELECT {COLS} FROM diag_event{w} {order} LIMIT ? OFFSET ?", a + oa + [per, (page - 1) * per]).fetchall()
    return [_row(r) for r in rows], total


def get(eid):
    with _db() as c:
        r = c.execute(f"SELECT {COLS} FROM diag_event WHERE id = ?", (eid,)).fetchone()
    return _row(r) if r else None


def export(kind=None, since=None, q=None, batch=2000):
    """JSONL-Zeilen der aktuellen Filterung (neueste zuerst), seitenweise ueber die id."""
    w, a, _q = _where(kind, since, q, now=time.time())
    last = None
    while True:
        ww, aa = w, list(a)
        if last is not None:
            ww = (w + " AND " if w else " WHERE ") + "id < ?"
            aa.append(last)
        with _db() as c:
            rows = c.execute(f"SELECT {COLS} FROM diag_event{ww} ORDER BY id DESC LIMIT ?", aa + [batch]).fetchall()
        if not rows:
            return
        for r in rows:
            yield json.dumps(_row(r), separators=(",", ":"), ensure_ascii=False) + "\n"
        last = rows[-1][0]


def stats():
    with _db() as c:
        r = c.execute("SELECT count(*), min(ts), pg_total_relation_size('diag_event') FROM diag_event").fetchone()
    return {"rows": r[0], "oldest": r[1], "bytes": r[2] or 0}


def summary(e):
    """Kurzfassung fuer die Tabelle (reiner Text, wird im Template escaped)."""
    d = e.get("data") or {}
    k = e.get("kind")
    try:
        if k == "clientlog":
            it = (d.get("items") or [{}])[0]
            s = f"{it.get('k', '')}: {it.get('msg', '')}"
            if len(d.get("items") or []) > 1:
                s += f" (+{len(d['items']) - 1})"
        elif k == "clientdiag":
            s = f"{d.get('n', 0)} ev"
            errs = sum(1 for x in d.get("ev") or [] if x.get("k") == "err")
            if errs:
                s += f", {errs} err"
            if d.get("user_note"):
                s += " - " + str(d["user_note"])
        elif k == "pmdiag":
            it = (d.get("items") or [{}])[0]
            s = f"{d.get('mode', '')} {d.get('n', 0)}x - {it.get('type', '')} {it.get('path', '')} {it.get('kind', '')}"
        elif k == "calldiag":
            n = d.get("n") or [0, 0]
            s = f"{d.get('path', '')}/{d.get('transport', '')} - {n[0]} samples, {n[1]} events"
        else:
            s = ""
    except Exception:  # noqa: BLE001 - Anzeige darf an komischen Daten nicht scheitern
        s = ""
    s = " ".join(str(s).split())
    return s[:160] + ("…" if len(s) > 160 else "")
