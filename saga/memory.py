"""Gedaechtnis (2.6, Bugs-DB 2581, Feature memory1; Vertrag docs/MEMORY.md): recall/remember ueber eine gebundene
Datenbank je Konto, Embeddings eines eigenen Endpunkts (Admin "ai_embed", getrennt vom Chat-Anbieter) oder Rueckfall
auf Trigramm-Aehnlichkeit, Ich-Kern als Markdown fuer Claude-Code-Hooks.

- Bindung je Konto in user_kv: memory.db, memory.core, memory.map (Spalten nach Namen), memory.weights. Nur EIGENE
  Datenbank/Seite (mem_vec.note_id ist eindeutig; geteilte Datenbanken gehoeren einer anderen Person). Saga legt nie
  Spalten oder Optionen an; die Datenbank bleibt die Quelle der Wahrheit.
- mem_vec (Migration 42): float32-Vektor (Laenge 1) je Zeile, text_hash des eingebetteten Texts, Modell; dazu die
  Abruf-Zaehler hits/last_hit (Wahrheit) und mirrored. Zeilen ohne Vektor (Rueckfall Trigramme) tragen die Zaehler mit
  model '' und leerem vec. Je Konto Zwischenspeicher im Prozess (numpy-Matrix), Abgleich ueber Anzahl/max(updated).
- Einbetten: Hintergrund-Thread (start/stop), entprellt (DEBOUNCE) ueber den bus (note.saved mit row, notes.tree,
  row.changed) - immer erst nach dem Commit eingereiht. Beim Binden/Modellwechsel/Start: Abgleich des ganzen Bestands
  (Stapel BATCH). recall bettet bis zu RECALL_FILL fehlende Zeilen zusammen mit der Frage ein.
- Bewertung exakt nach docs/MEMORY.md 4 (factors/score, Vektoren tests/vectors/memory_score.json).
- Spiegelung der Zaehler in die Spalten "Abrufe"/"Letzter Abruf": hoechstens alle MIRROR_GAP s je Zeile, als
  System-Schreibvorgang direkt in note.props (jsonb-Zusammenfuehrung nur dieser zwei Schluessel): kein rev, kein
  updated (Seitenbaum-Trigger und "zuletzt geaendert" bleiben still), keine note_version, kein bus.fire (keine Live-
  Ereignisse, keine Webhooks). Aendert eine Person die Spalten selbst (row.changed), gilt ihr Wert als Zaehlerstand.
- Netz wie der KI-Endpunkt (saga/netguard.py): nur der Host der Adresse, interne IPs nur festgehaltene, "im Heimnetz"
  gilt auch fuer https, keine Weiterleitungen. Ollama: native /api/embed mit keep_alive (kein Warmhalten ab Werk).
- Datenschutz: keine Fragen/Inhalte im Log (nur Zahlen und ein Kurz-Hash des Kontos).
REST /api/v1/memory/{core,recall,remember} (Bereich "memory"), Web /api/account/memory, Admin ueber admin.py
(/api/ai/embed...). MCP-Werkzeuge recall/remember in saga/tools.py."""
import hashlib
import ipaddress
import json
import math
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import numpy as np
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import api_v1, bus
from saga.api_v1 import Fail
from saga.core import CA_FILE, TZ, _api_guard, _body, _json_load, t

router = APIRouter()

FIELDS = ("inhalt", "schlagworte", "projekt", "art", "wichtigkeit", "valenz", "erregung", "datum", "letzter_abruf", "abrufe")
DEFAULT_MAP = {"inhalt": "Inhalt", "schlagworte": "Schlagworte", "projekt": "Projekt", "art": "Art",
               "wichtigkeit": "Wichtigkeit", "valenz": "Valenz", "erregung": "Erregung", "datum": "Datum",
               "letzter_abruf": "Letzter Abruf", "abrufe": "Abrufe"}
TYPES = {"inhalt": ("text",), "schlagworte": ("multi_select", "text"), "projekt": ("select",), "art": ("select",),
         "wichtigkeit": ("number",), "valenz": ("number",), "erregung": ("number",), "datum": ("date",),
         "letzter_abruf": ("date",), "abrufe": ("number",)}
WEIGHTS = {"rel": 0.55, "fresh": 0.15, "freq": 0.10, "imp": 0.20, "proj": 0.10, "rel_min": 0.45, "rel_min_trgm": 0.25,
           "score_min": 0.375, "half_life_days": 30}
# score_min 0,375 statt 0,50 (Spec F4): Kalibrierung mit bge-m3 (tools/evals/gedaechtnis_kalibrieren.py, 48 Fragen) -
# relevante Kosinuswerte liegen meist bei 0,54-0,62; mit 0,50 fand die Bewertung nur 41 % der erwarteten Erinnerungen
# unter den ersten 5, mit 0,375 95 % bei 0,17 Fehltreffern je Frage und keinem Treffer fuer Fragen ohne Erinnerung
# (das trennt das Relevanz-Tor rel_min 0,45, bestaetigt). Eigene Werte je Konto: memory.weights.
_W_RANGE = {"rel": (0, 2), "fresh": (0, 2), "freq": (0, 2), "imp": (0, 2), "proj": (0, 2), "rel_min": (0, 1),
            "rel_min_trgm": (0, 1), "score_min": (0, 1), "half_life_days": (1, 3650)}
PROVIDERS = ("ollama", "openai")
LIMIT_MAX = 5
TEXT_MAX = 4000                  # Frage (recall), Rest abgeschnitten
EMBED_TEXT_MAX = 8000            # eingebetteter Text je Zeile
CONTENT_MAX = 2000               # Inhalt in der Antwort (Text-Spalten haben hoechstens 2000 Zeichen)
SIMILAR = 0.90                   # remember: "aehnlich"
CAND_MAX = 50                    # Kandidaten (nach Relevanz) fuer die Bewertung
BATCH = 32
RECALL_FILL = 8
DEBOUNCE = 2.0
MIRROR_GAP = 600.0
RATE = 60                        # recall je Token und Minute
TRGM_MIN = 0.5                   # Trigramm-Aehnlichkeit je Wort (darunter 0)
KW_MAX = 16
KEEP_RE = re.compile(r"(?:-1|0|\d{1,4}[smh]?)")


def _uh(uid):
    """Kurz-Hash eines Kontos fuers Log (kein Name)."""
    return hashlib.sha256(str(uid).encode()).hexdigest()[:8]


# --- Bewertung (docs/MEMORY.md 4) ----------------------------------------------------------------------------------
def ts_of(v):
    """Datumswert einer Eigenschaft -> Zeitstempel (Tag = 00:00 UTC, Zeitraum -> Ende) oder None."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().split("/")[-1].strip()
    try:
        if len(s) == 10:
            return datetime.fromisoformat(s + "T00:00:00+00:00").timestamp()
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def weights(raw=None):
    """Gespeicherte Abweichungen -> volle Gewichte/Schwellen (Unbekanntes/Ungueltiges faellt weg)."""
    w = dict(WEIGHTS)
    for k, v in (raw or {}).items() if isinstance(raw, dict) else ():
        if k in _W_RANGE and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
            lo, hi = _W_RANGE[k]
            if lo <= v <= hi:
                w[k] = float(v)
    return w


def factors(rel, now, created, hits=0, last_hit=None, letzter_abruf=None, datum=None, wichtigkeit=None,
            projekt_q=None, projekt_row=None, w=None):
    """Faktoren einer Zeile (alle 0..1)."""
    w = w or WEIGHTS
    ref = [x for x in (last_hit, ts_of(letzter_abruf), ts_of(datum)) if x is not None]
    ref = max(ref) if ref else (created or now)
    dt = max(0.0, now - ref)
    fresh = 0.5 ** (dt / (float(w["half_life_days"]) * 86400))
    freq = min(1.0, math.log(1 + max(0, int(hits or 0))) / math.log(21))
    try:
        imp = 0.5 if wichtigkeit is None else max(0.0, min(1.0, float(wichtigkeit) / 10))
    except (TypeError, ValueError):
        imp = 0.5
    pq = projekt_q.strip().casefold() if isinstance(projekt_q, str) else ""
    pr = projekt_row.strip().casefold() if isinstance(projekt_row, str) else ""
    proj = 1.0 if pq and pq == pr else 0.0
    return {"rel": max(0.0, float(rel)), "fresh": fresh, "freq": freq, "imp": imp, "proj": proj}


def score(f, w=None):
    w = w or WEIGHTS
    return sum(f[k] * w[k] for k in ("rel", "fresh", "freq", "imp", "proj"))


def is_hit(f, s, w=None, quelle="embeddings"):
    w = w or WEIGHTS
    return f["rel"] >= (w["rel_min_trgm"] if quelle == "trigramm" else w["rel_min"]) and s >= w["score_min"]


def rank(items, limit):
    """[(score, rel, id, ...)] -> beste limit: score ab, rel ab, id auf."""
    return sorted(items, key=lambda x: (-x[0], -x[1], x[2]))[:limit]


# --- Bindung und Spalten ---------------------------------------------------------------------------------------------
_bound = {}                      # uid -> db-ID (fuer die bus-Zuhoerer; Wahrheit bleibt user_kv)
_bound_lock = threading.Lock()
_bound_loaded = [False]


def _kv(c, uid):
    return {k: v for k, v in c.execute("SELECT k, v FROM user_kv WHERE uid=? AND k LIKE 'memory.%'", (uid,)).fetchall()}


def _int_or_none(v):
    try:
        i = int(v)
        return i if 0 < i < 2 ** 63 else None
    except (TypeError, ValueError):
        return None


def binding(uid, c=None):
    """-> {db, core, map (voll), map_raw, weights (voll), weights_raw}."""
    if c is None:
        with _db() as c2:
            return binding(uid, c2)
    kv = _kv(c, uid)
    mraw = _json_load(kv.get("memory.map"), {})
    wraw = _json_load(kv.get("memory.weights"), {})
    mp = dict(DEFAULT_MAP)
    mp.update({k: str(v)[:100] for k, v in mraw.items() if k in FIELDS and isinstance(v, str) and v.strip()})
    return {"db": _int_or_none(kv.get("memory.db")), "core": _int_or_none(kv.get("memory.core")), "map": mp, "map_raw": mraw,
            "weights": weights(wraw), "weights_raw": wraw}


def _load_bound():
    if _bound_loaded[0]:
        return
    try:
        with _db() as c:
            rows = c.execute("SELECT uid, v FROM user_kv WHERE k='memory.db'").fetchall()
    except Exception:  # noqa: BLE001 - Datenbank (noch) nicht da
        return
    with _bound_lock:
        for uid, v in rows:
            if _int_or_none(v):
                _bound[uid] = _int_or_none(v)
        _bound_loaded[0] = True


def bound_uids():
    _load_bound()
    with _bound_lock:
        return dict(_bound)


def columns(schema, mp):
    """Schema + Zuordnung -> {feld: eigenschaft|None}, {feld: info}."""
    props = [p for p in (schema or {}).get("props") or [] if isinstance(p, dict)]
    cols, info = {}, {}
    for f in FIELDS:
        name = mp.get(f) or DEFAULT_MAP[f]
        low = name.strip().casefold()
        p = next((x for x in props if str(x.get("name") or "").strip().casefold() == low), None)
        ok = bool(p) and p.get("type") in TYPES[f]
        cols[f] = p if ok else None
        info[f] = {"name": name, "found": bool(p), "type": p.get("type") if p else None, "ok": ok}
    return cols, info


def _opt_name(p, v):
    for o in p.get("options") or []:
        if isinstance(o, dict) and o.get("id") == v:
            return o.get("name") or ""
    return None


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def fields_of(cols, title, props):
    """Zeile -> Werte nach Feld (Auswahl als Optionsname)."""
    props = props if isinstance(props, dict) else {}

    def val(f):
        p = cols.get(f)
        return props.get(p["id"]) if p else None
    tags = val("schlagworte")
    if cols.get("schlagworte") and cols["schlagworte"].get("type") == "text":
        tags = [x.strip() for x in str(tags or "").split(",") if x.strip()]
    else:
        tags = [n for n in (_opt_name(cols["schlagworte"], x) for x in tags) if n] if isinstance(tags, list) and cols.get("schlagworte") else []
    out = {"titel": str(title or ""), "inhalt": str(val("inhalt") or ""), "schlagworte": tags,
           "projekt": _opt_name(cols["projekt"], val("projekt")) if cols.get("projekt") and val("projekt") else None,
           "art": _opt_name(cols["art"], val("art")) if cols.get("art") and val("art") else None}
    for f in ("wichtigkeit", "valenz", "erregung", "abrufe"):
        out[f] = _num(val(f))
    for f in ("datum", "letzter_abruf"):
        v = val(f)
        out[f] = v if isinstance(v, str) else None
    return out


def embed_text(f):
    parts = [f["titel"].strip(), f["inhalt"].strip(), ", ".join(f["schlagworte"]), (f["projekt"] or "").strip()]
    return "\n".join(p for p in parts if p)[:EMBED_TEXT_MAX]


def text_hash(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _db_state(c, uid, dbid):
    """Gebundene Datenbank -> (schema, title) oder None (weg, Papierkorb, nicht eigene)."""
    r = c.execute("SELECT schema, title FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (dbid, uid)).fetchone()
    return (_json_load(r[0], {"props": []}), r[1] or "") if r else None


_ROWS_SQL = ("SELECT id, title, props, created FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND archived IS NULL "
             "AND is_template=0")


# --- Embedding-Endpunkt ------------------------------------------------------------------------------------------------
class EmbedError(Exception):
    def __init__(self, code, msg=""):
        super().__init__(msg or code)
        self.code = code


def ecfg():
    return dict(auth.DEFAULTS["ai_embed"], **(auth.settings().get("ai_embed") or {}))


def configured(c=None):
    c = c if c is not None else ecfg()
    return bool(c.get("enabled") and c.get("url") and c.get("model") and c.get("provider") in PROVIDERS)


def model_key(c=None):
    """Kennung der Vektoren (anderes Modell/Anbieter/Adresse = neu rechnen)."""
    c = c if c is not None else ecfg()
    return str(c.get("model") or "")[:200] if configured(c) else ""


def norm_url(url, provider):
    from saga import ai
    u = str(url or "").strip().rstrip("/")
    suf = ("/api/embed", "/api/embeddings", "/api", "/v1") if provider == "ollama" else ("/embeddings",)
    for s in suf:
        if u.endswith(s):
            u = u[: -len(s)]
            break
    return ai.norm_url(u, "openai")


def _ipn(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return ""
    return str(a.ipv4_mapped) if getattr(a, "ipv4_mapped", None) else str(a)


def where(c=None):
    """home (alle festgehaltenen IPs privat) | internet | None."""
    from saga import ai_saga
    c = c if c is not None else ecfg()
    pins = [x for x in c.get("pinned") or [] if x]
    if not c.get("url") or not pins:
        return None
    return "home" if ai_saga.home_ips(pins) and not ai_saga.left_home(c) else "internet"


def ip_checker(c):
    """Wie saga.ai.ip_checker (Bugs-DB 2494): nur der Host der Adresse; http oder "im Heimnetz" -> nur festgehaltene,
    interne IPs; sonst oeffentliche oder festgehaltene. Loest ein Heimnetz-Name oeffentlich auf: Markierung left_home in
    der Einstellung ai_embed (ai_saga.mark_left_home) -> where() sagt "internet", bis der Admin neu speichert."""
    from saga import ai_saga
    from saga.netguard import is_public
    pu = urlparse(c.get("url") or "")
    host, plain = (pu.hostname or "").lower(), pu.scheme == "http"
    pinned = {_ipn(x) for x in c.get("pinned") or []}
    home = ai_saga.home_ips(pinned)

    def ok(ip, h, _port):
        if not host or str(h or "").lower().strip("[]") != host.strip("[]"):
            return False
        if home and not ai_saga.home_ips([_ipn(ip) or ip]):
            ai_saga.mark_left_home(c, _ipn(ip) or ip, key="ai_embed")     # Admin-Hinweis "im Internet" (wie beim KI-Endpunkt)
            return False
        if plain or home:
            return _ipn(ip) in pinned and not is_public(ip)
        return is_public(ip) or _ipn(ip) in pinned
    return ok


_clients = {}
_clients_lock = threading.Lock()


def _verify():
    import os
    import ssl
    try:
        return ssl.create_default_context(cafile=CA_FILE) if os.path.isfile(CA_FILE) else True
    except (OSError, ssl.SSLError):
        return True


def _client(c):
    from saga.netguard import guarded_client
    key = json.dumps([c.get("url"), sorted(c.get("pinned") or [])])
    with _clients_lock:
        cl = _clients.get(key)
        if cl is None:
            for old in _clients.values():
                try:
                    old.close()
                except Exception:  # noqa: BLE001
                    pass
            _clients.clear()
            cl = guarded_client(ip_checker(c), transport_kw={"verify": _verify()}, follow_redirects=False,
                                timeout=httpx.Timeout(10.0))
            _clients[key] = cl
        return cl


_EPOOL = [None]
_EPOOL_LOCK = threading.Lock()
EMBED_WORKERS = 4                # gleichzeitige Aufrufe des Endpunkts (alle Konten); wer laenger wartet, faellt unter die Frist


def embed(texts, c=None, timeout=None):
    """Texte -> float32-Matrix (n x dim, Zeilen Laenge 1). EmbedError bei jedem Problem. timeout = GESAMTFRIST in s
    (Verbindung, Senden, Antwort zusammen; httpx begrenzt nur je Lesevorgang): der Aufruf laeuft in einem eigenen Thread,
    nach der Frist -> EmbedError("timeout"); der Thread endet spaetestens mit dem httpx-Zeitlimit (= Frist)."""
    import concurrent.futures
    c = c if c is not None else ecfg()
    if not configured(c):
        raise EmbedError("not_configured")
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    tmo = float(timeout if timeout is not None else max(2, min(120, int(c.get("timeout") or 15))))
    with _EPOOL_LOCK:
        if _EPOOL[0] is None:
            _EPOOL[0] = concurrent.futures.ThreadPoolExecutor(EMBED_WORKERS, thread_name_prefix="gedaechtnis-embed")
        pool = _EPOOL[0]
    fut = pool.submit(_embed_http, list(texts), c, tmo)
    try:
        return fut.result(timeout=tmo)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise EmbedError("timeout")


def _embed_http(texts, c, tmo):
    c = c if c is not None else ecfg()
    base = c["url"].rstrip("/")
    h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Saga"}
    if c.get("api_key"):
        h["Authorization"] = "Bearer " + c["api_key"]
    if c.get("provider") == "ollama":
        url, body = base + "/api/embed", {"model": c["model"], "input": list(texts), "truncate": True}
        ka = str(c.get("keep_alive") or "").strip()
        if ka and KEEP_RE.fullmatch(ka):
            body["keep_alive"] = int(ka) if ka.lstrip("-").isdigit() else ka
    else:
        url, body = base + "/embeddings", {"model": c["model"], "input": list(texts)}
    try:
        r = _client(c).post(url, json=body, headers=h, timeout=httpx.Timeout(tmo, connect=min(tmo, 5.0)))
    except httpx.TimeoutException:
        raise EmbedError("timeout")
    except httpx.HTTPError as ex:
        from saga.netguard import Blocked
        raise EmbedError("blocked" if isinstance(ex.__cause__, Blocked) or isinstance(ex, Blocked) else "unreachable")
    if r.status_code in (301, 302, 303, 307, 308):
        raise EmbedError("redirect")
    if r.status_code != 200:
        raise EmbedError("http", str(r.status_code))
    if len(r.content) > 256 * 1024 * 1024:
        raise EmbedError("bad_response")
    try:
        d = r.json()
        if c.get("provider") == "ollama":
            vecs = d["embeddings"]
        else:
            data = sorted(d["data"], key=lambda x: int(x.get("index", 0)))
            vecs = [x["embedding"] for x in data]
        m = np.asarray(vecs, dtype=np.float32)
    except (ValueError, KeyError, TypeError):
        raise EmbedError("bad_response")
    if m.ndim != 2 or m.shape[0] != len(texts) or not 8 <= m.shape[1] <= 8192 or not np.isfinite(m).all():
        raise EmbedError("bad_response")
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


_qcache = OrderedDict()          # (modell, text) -> Vektor der Frage
_qlock = threading.Lock()


def _qget(key):
    with _qlock:
        v = _qcache.get(key)
        if v is not None:
            _qcache.move_to_end(key)
        return v


def _qput(key, v):
    with _qlock:
        _qcache[key] = v
        while len(_qcache) > 256:
            _qcache.popitem(last=False)


# --- Zwischenspeicher je Konto -----------------------------------------------------------------------------------------
class _Vecs:
    __slots__ = ("model", "ids", "mat", "stamp")

    def __init__(self, model, ids, mat, stamp):
        self.model, self.ids, self.mat, self.stamp = model, ids, mat, stamp

    def nbytes(self):
        return int(self.mat.nbytes + self.ids.nbytes)


_vcache = OrderedDict()          # uid -> _Vecs, zuletzt benutzt hinten (LRU)
_vlock = threading.Lock()
# Obergrenze aller Vektor-Zwischenspeicher im Prozess: SAGA_MEMORY_CACHE_MB (Standard 256). 5 000 Erinnerungen x 1024 Dim.
# = 20 MB je Konto -> etwa ein Dutzend grosse Gedaechtnisse oder eines mit ~60 000 Eintraegen. Verdraengt wird das am
# laengsten unbenutzte Konto; das kostet beim naechsten recall nur ein Nachladen (~130 ms bei 5 000). Das gerade geladene
# Konto bleibt immer (auch wenn es allein groesser ist). Trigramm-Indizes: hoechstens WCACHE_MAX Konten.
CACHE_MAX = max(16, int(__import__("os").environ.get("SAGA_MEMORY_CACHE_MB", "256") or 256)) * 1024 * 1024
WCACHE_MAX = 64


def _vput(uid, v):
    with _vlock:
        _vcache[uid] = v
        _vcache.move_to_end(uid)
        total = sum(x.nbytes() for x in _vcache.values())
        while total > CACHE_MAX and len(_vcache) > 1:
            _old, x = _vcache.popitem(last=False)
            total -= x.nbytes()


def cache_total():
    with _vlock:
        return sum(x.nbytes() for x in _vcache.values()), len(_vcache)


def _stamp(c, uid, model):
    r = c.execute("SELECT count(*), COALESCE(max(updated), 0) FROM mem_vec WHERE uid=? AND model=? AND dim>0", (uid, model)).fetchone()
    return (int(r[0]), float(r[1]))


def vectors(c, uid, model):
    """Vektoren des Kontos (aus dem Speicher; neu geladen, wenn sich mem_vec geaendert hat)."""
    st = _stamp(c, uid, model)
    with _vlock:
        v = _vcache.get(uid)
        if v is not None and v.model == model and v.stamp == st:
            _vcache.move_to_end(uid)
            return v
    rows = c.execute("SELECT note_id, dim, vec FROM mem_vec WHERE uid=? AND model=? AND dim>0 ORDER BY note_id", (uid, model)).fetchall()
    dims = {r[1] for r in rows}
    if len(dims) > 1:                    # gemischte Dimensionen (Modell gleich benannt, aber anders): nur die haeufigste
        best = max(dims, key=lambda d: sum(1 for r in rows if r[1] == d))
        rows = [r for r in rows if r[1] == best]
    dim = rows[0][1] if rows else 0
    mat = np.empty((len(rows), dim), dtype=np.float32)
    for i, r in enumerate(rows):
        mat[i] = np.frombuffer(bytes(r[2]), dtype="<f4")
    v = _Vecs(model, np.array([r[0] for r in rows], dtype=np.int64), mat, st)
    _vput(uid, v)
    return v


def cache_bytes(uid):
    with _vlock:
        v = _vcache.get(uid)
    return v.nbytes() if v is not None else 0


def forget(uid=None):
    with _vlock:
        if uid is None:
            _vcache.clear()
        else:
            _vcache.pop(uid, None)
    with _wlock:
        if uid is None:
            _wcache.clear()
        else:
            _wcache.pop(uid, None)


# --- Trigramme (Rueckfall) -----------------------------------------------------------------------------------------
_STOP = set("""aber alle allem allen aller alles als also am an ander andere anderem anderen anderer anderes auch auf aus bei
bin bis bist da damit dann das dass dein deine dem den denn der des dessen dich die dies diese diesem diesen dieser dieses
dir doch dort du durch ein eine einem einen einer eines er es etwas euch euer für hab habe haben hat hatte hier hin ich ihm
ihn ihr ihre im in ist ja jede jedem jeden jeder jedes jetzt kann kein keine man mein meine mich mir mit muss nach nicht
nichts noch nun nur ob oder ohne sehr sein seine sich sie sind so solche soll sondern sonst über um und uns unser unter
viel vom von vor war waren warum was weil welche wenn wer werde werden wie wieder will wir wird wo wurde zu zum zur zwar
the and for with that this from have has was were are you your what which when where who how why not but can could
would should about into over than then them they their there these those its our out also just like some any all""".split())
_WORD = re.compile(r"[^\W_]+", re.U)


def words(s):
    s = unicodedata.normalize("NFKC", str(s or "")).casefold()
    return [w for w in _WORD.findall(s) if len(w) >= 3]


def trigrams(w):
    p = "  " + w + " "
    return frozenset(p[i:i + 3] for i in range(len(p) - 2))


def keywords(q):
    out = []
    for w in words(q):
        if w not in _STOP and w not in out:
            out.append(w)
    return out[:KW_MAX]


class _Words:
    __slots__ = ("ids", "word_rows", "tri_words", "wtri", "stamp")


_wcache = OrderedDict()
_wlock = threading.Lock()


def _rows_stamp(c, uid, dbid):
    r = c.execute("SELECT count(*), COALESCE(max(updated), 0) FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL "
                  "AND archived IS NULL AND is_template=0", (uid, dbid)).fetchone()
    return (dbid, int(r[0]), float(r[1]))


def word_index(c, uid, dbid, cols):
    st = _rows_stamp(c, uid, dbid) + (json.dumps({k: (v or {}).get("id") for k, v in cols.items()}),)
    with _wlock:
        x = _wcache.get(uid)
        if x is not None and x.stamp == st:
            _wcache.move_to_end(uid)
            return x
    x = _Words()
    x.ids, x.word_rows, x.tri_words, x.wtri, x.stamp = [], {}, {}, {}, st
    for nid, title, props, _cr in c.execute(_ROWS_SQL, (uid, dbid)).fetchall():
        i = len(x.ids)
        x.ids.append(nid)
        for w in set(words(embed_text(fields_of(cols, title, _json_load(props, {}))))):
            x.word_rows.setdefault(w, set()).add(i)
            if w not in x.wtri:
                x.wtri[w] = trigrams(w)
                for g in x.wtri[w]:
                    x.tri_words.setdefault(g, set()).add(w)
    with _wlock:
        _wcache[uid] = x
        _wcache.move_to_end(uid)
        while len(_wcache) > WCACHE_MAX:
            _wcache.popitem(last=False)
    return x


def trgm_rel(x, q):
    """-> {zeilen-index: rel} (nur > 0)."""
    kws = keywords(q)
    if not kws:
        return {}
    acc = {}
    for kw in kws:
        a = trigrams(kw)
        cand = set()
        for g in a:
            cand |= x.tri_words.get(g, set())
        best = {}
        for w in cand:
            b = x.wtri[w]
            sim = len(a & b) / len(a | b)
            if sim >= TRGM_MIN:
                for i in x.word_rows[w]:
                    if sim > best.get(i, 0.0):
                        best[i] = sim
        for i, s in best.items():
            acc[i] = acc.get(i, 0.0) + s
    return {i: s / len(kws) for i, s in acc.items()}


# --- Schreiben in mem_vec ----------------------------------------------------------------------------------------------
def _upsert(c, uid, rows, model, mat):
    """rows [(id, hash, abrufe, letzter_abruf)], mat (n x dim) -> Vektoren speichern (Zaehler bleiben; neu: aus den Spalten)."""
    now = time.time()
    for (nid, h, ab, la), v in zip(rows, mat):
        c.execute("INSERT INTO mem_vec (note_id, uid, model, dim, vec, text_hash, hits, last_hit, updated) VALUES (?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT (note_id) DO UPDATE SET uid=EXCLUDED.uid, model=EXCLUDED.model, dim=EXCLUDED.dim, vec=EXCLUDED.vec, "
                  "text_hash=EXCLUDED.text_hash, updated=EXCLUDED.updated",
                  (nid, uid, model, int(v.shape[0]), v.astype("<f4").tobytes(), h, max(0, int(ab or 0)), ts_of(la), now))


def _drop(c, uid, ids):
    ids = [int(i) for i in ids]
    if ids:
        c.execute("DELETE FROM mem_vec WHERE uid=? AND note_id = ANY(?)", (uid, ids))


# --- Abgleich (Hintergrund, CLI) -------------------------------------------------------------------------------------
_running = {}                    # uid -> Beginn eines Abgleichs
_errors = {}                     # uid -> {"code", "at"}: letzter Fehler des Endpunkts (status.error, ohne Geheimnisse)
ERROR_TTL = 3600


def _note_error(uid, ex):
    _errors[uid] = {"code": ex.code, "detail": str(ex) if ex.code == "http" else "", "at": time.time()}


def last_error(uid):
    e = _errors.get(uid)
    if not e or time.time() - e["at"] > ERROR_TTL:
        return None
    return _embed_msg(EmbedError(e["code"], e["detail"]))


def _live_rows(c, uid, dbid, cols, ids=None):
    sql, args = _ROWS_SQL, [uid, dbid]
    if ids is not None:
        sql += " AND id = ANY(?)"
        args.append([int(i) for i in ids])
    out = []
    for nid, title, props, created in c.execute(sql, tuple(args)).fetchall():
        f = fields_of(cols, title, _json_load(props, {}))
        txt = embed_text(f)
        out.append((nid, txt, text_hash(txt), f))
    return out


def sync(uid, ids=None, log=None, c_embed=None):
    """Vektoren abgleichen: ids None = ganzer Bestand (Zeilen ausserhalb weg), sonst nur diese Zeilen.
    -> {"rows", "embedded", "dropped", "failed"}."""
    ec = c_embed if c_embed is not None else ecfg()
    model = model_key(ec)
    with _db() as c:
        b = binding(uid, c)
        st = _db_state(c, uid, b["db"]) if b["db"] else None
        if st is None:
            with c:
                if ids is None:
                    c.execute("DELETE FROM mem_vec WHERE uid=?", (uid,))
                else:
                    _drop(c, uid, ids)
            forget(uid)
            return {"rows": 0, "embedded": 0, "dropped": 0, "failed": 0}
        cols, _info = columns(st[0], b["map"])
        live = _live_rows(c, uid, b["db"], cols, ids)
        have = {r[0]: (r[1], r[2]) for r in c.execute(
            "SELECT note_id, model, text_hash FROM mem_vec WHERE uid=?" + ("" if ids is None else " AND note_id = ANY(?)"),
            (uid,) if ids is None else (uid, [int(i) for i in ids])).fetchall()}
    live_ids = {r[0] for r in live}
    gone = [i for i in have if i not in live_ids]
    if ids is not None:
        gone += [int(i) for i in ids if int(i) not in live_ids and int(i) not in have]
    dropped = 0
    if gone:
        with _db() as c, c:
            _drop(c, uid, gone)
            dropped = len(gone)
    need = [r for r in live if have.get(r[0]) != (model, r[2])] if model else []
    done = failed = 0
    if need:
        _running[uid] = time.time()
        try:
            for i in range(0, len(need), BATCH):
                part = need[i:i + BATCH]
                try:
                    mat = embed([r[1] for r in part], ec)
                    _errors.pop(uid, None)
                except EmbedError as ex:
                    _note_error(uid, ex)
                    failed += len(need) - i
                    print(f"gedaechtnis: Konto {_uh(uid)}: Einbetten gescheitert ({ex.code}) nach {done}/{len(need)}", flush=True)
                    break
                with _db() as c, c:
                    _upsert(c, uid, [(r[0], r[2], r[3]["abrufe"], r[3]["letzter_abruf"]) for r in part], model, mat)
                done += len(part)
                if (log is not None or len(need) > BATCH) and (done == len(need) or done % (BATCH * 10) == 0):
                    msg = f"gedaechtnis: Konto {_uh(uid)}: {done}/{len(need)} eingebettet"
                    log(msg) if log is not None else print(msg, flush=True)
        finally:
            _running.pop(uid, None)
    return {"rows": len(live), "embedded": done, "dropped": dropped, "failed": failed}


def status(uid):
    """Konto -> {source, rows, embedded, stale, running, model}."""
    ec = ecfg()
    model = model_key(ec)
    with _db() as c:
        b = binding(uid, c)
        st = _db_state(c, uid, b["db"]) if b["db"] else None
        if st is None:
            return {"source": "embeddings" if model else "trigramm", "rows": 0, "embedded": 0, "stale": 0, "running": False,
                    "model": model or None}
        cols, _ = columns(st[0], b["map"])
        live = _live_rows(c, uid, b["db"], cols)
        have = {r[0]: (r[1], r[2]) for r in c.execute("SELECT note_id, model, text_hash FROM mem_vec WHERE uid=?", (uid,)).fetchall()}
    emb = sum(1 for r in live if model and have.get(r[0]) == (model, r[2]))
    out = {"source": "embeddings" if model else "trigramm", "rows": len(live), "embedded": emb,
           "stale": (len(live) - emb) if model else 0, "running": uid in _running, "model": model or None}
    err = last_error(uid) if model else None
    if err:
        out["error"] = err                       # letzter Fehler des Endpunkts (Einbett-Lauf oder recall), kurz, ohne Geheimnisse
    return out


# --- Hintergrund: Warteschlange, entprellt ---------------------------------------------------------------------------
_pending = {}                    # uid -> {"ids": set, "all": bool, "adopt": set, "due": t}
_cv = threading.Condition()
_thread = [None]
_stop = threading.Event()


def schedule(uid, ids=(), whole=False, adopt=(), delay=DEBOUNCE):
    """adopt: Zeilen-IDs oder {id: geaenderte Eigenschaften} (row.changed) - nur geaenderte Zaehler-Spalten zaehlen."""
    with _cv:
        p = _pending.setdefault(uid, {"ids": set(), "all": False, "adopt": {}, "due": 0})
        p["ids"].update(int(i) for i in ids)
        for i in adopt:
            keys = adopt[i] if isinstance(adopt, dict) else None
            i = int(i)
            if i in p["adopt"] and (p["adopt"][i] is None or keys is None):
                p["adopt"][i] = None
            else:
                p["adopt"][i] = set(p["adopt"].get(i) or ()) | set(keys or ()) if keys is not None else None
        p["all"] = p["all"] or whole
        p["due"] = time.time() + delay
        _cv.notify()


def schedule_all(delay=DEBOUNCE):
    for uid in bound_uids():
        schedule(uid, whole=True, delay=delay)


def _after(c, fn):
    after = getattr(c, "after_commit", None)
    if after is None:
        fn()
    else:
        after(fn)


def _on_saved(c, note_id=None, owner=None, row=None, gone=None, **_):
    if not _bound_loaded[0] or owner not in _bound:
        return
    if row is not None and note_id == _bound.get(owner):
        _after(c, lambda: schedule(owner, [row]))
    elif note_id == _bound.get(owner) and row is None:
        _after(c, lambda: schedule(owner, whole=True))      # Datenbank selbst (Spalten umbenannt, Papierkorb ...)


def _on_tree(c, owner=None, ids=(), op=None, **_):
    if not _bound_loaded[0] or owner not in _bound or op in ("created", "updated", "shared"):
        return
    ids = list(ids or ())
    if _bound.get(owner) in ids:
        _after(c, lambda: schedule(owner, whole=True))
    elif ids:
        _after(c, lambda: schedule(owner, ids))


def _on_row_changed(c, owner=None, note_id=None, changed=(), **_):
    """Person aendert "Abrufe"/"Letzter Abruf" selbst -> neuer Zaehlerstand (die Spiegelung feuert kein row.changed)."""
    if not _bound_loaded[0] or owner not in _bound or not changed:
        return
    keys = set(changed)
    _after(c, lambda: schedule(owner, adopt={note_id: keys}, delay=0.2))


bus.on("note.saved", _on_saved)
bus.on("notes.tree", _on_tree)
bus.on("row.changed", _on_row_changed)


def _adopt(uid, ids):
    """ids: Zeilen-IDs (alle Zaehler-Spalten uebernehmen) oder {id: geaenderte Eigenschaften} - dann nur die Spalten, die die
    Person geaendert hat. Sonst setzte jede andere Aenderung der Zeile (Titel, Inhalt) die Zaehler auf den bis zu 10 min
    alten Spiegel-Stand zurueck."""
    keys_of = ids if isinstance(ids, dict) else {int(i): None for i in ids}
    with _db() as c, c:
        b = binding(uid, c)
        st = _db_state(c, uid, b["db"]) if b["db"] else None
        if st is None:
            return
        cols, _ = columns(st[0], b["map"])
        if not cols.get("abrufe") and not cols.get("letzter_abruf"):
            return
        for nid, props in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=? AND id = ANY(?)",
                                    (uid, b["db"], [int(i) for i in keys_of])).fetchall():
            f = fields_of(cols, "", _json_load(props, {}))
            r = c.execute("SELECT hits, last_hit, mirrored FROM mem_vec WHERE note_id=? AND uid=?", (nid, uid)).fetchone()
            if not r:
                continue
            ks = keys_of.get(nid)

            def mine(fld):
                return bool(cols.get(fld)) and (ks is None or cols[fld]["id"] in ks)
            hits = int(f["abrufe"]) if mine("abrufe") and f["abrufe"] is not None else r[0]
            lh = ts_of(f["letzter_abruf"]) if mine("letzter_abruf") and f["letzter_abruf"] else r[1]
            if hits != r[0] or (lh is not None and r[1] is not None and abs(lh - r[1]) > 60):
                c.execute("UPDATE mem_vec SET hits=?, last_hit=?, mirrored=? WHERE note_id=? AND uid=?",
                          (max(0, hits), lh, time.time(), nid, uid))


def _iso(ts_):
    return datetime.fromtimestamp(ts_, TZ).isoformat(timespec="minutes")


def mirror(c, uid, rows, cols, dbid, now=None):
    """Zaehler [(note_id, hits, last_hit)] in die Spalten schreiben - System-Schreibvorgang (siehe Modul-Text)."""
    now = now or time.time()
    for nid, hits, lh in rows:
        patch = {}
        if cols.get("abrufe"):
            patch[cols["abrufe"]["id"]] = int(hits or 0)
        if cols.get("letzter_abruf") and lh:
            patch[cols["letzter_abruf"]["id"]] = _iso(lh)
        if patch:
            c.execute("UPDATE note SET props = (COALESCE(saga_jsonb(props), '{}'::jsonb) || ?::jsonb)::text "
                      "WHERE id=? AND uid=? AND parent_id=? AND deleted_at IS NULL", (json.dumps(patch), nid, uid, dbid))
        c.execute("UPDATE mem_vec SET mirrored=? WHERE note_id=? AND uid=?", (now, nid, uid))


def flush_mirror(now=None, gap=MIRROR_GAP):
    """Faellige Spiegelungen (gezaehlt nach der letzten Spiegelung, die >= gap her ist) gebuendelt schreiben -> Anzahl."""
    now = now or time.time()
    with _db() as c:
        rows = c.execute("SELECT note_id, uid, hits, last_hit FROM mem_vec WHERE last_hit IS NOT NULL AND last_hit > COALESCE(mirrored, 0) "
                         "AND COALESCE(mirrored, 0) <= ? ORDER BY uid LIMIT 2000", (now - gap,)).fetchall()
    by = {}
    for nid, uid, hits, lh in rows:
        by.setdefault(uid, []).append((nid, hits, lh))
    n = 0
    for uid, lst in by.items():
        with _db() as c, c:
            b = binding(uid, c)
            st = _db_state(c, uid, b["db"]) if b["db"] else None
            if st is None:
                continue
            cols, _ = columns(st[0], b["map"])
            mirror(c, uid, lst, cols, b["db"], now)
            n += len(lst)
    return n


def _work_one(uid, p):
    if p["adopt"]:
        _adopt(uid, p["adopt"])
    if p["all"]:
        sync(uid)
    elif p["ids"]:
        sync(uid, p["ids"])


def _loop():
    last_flush = time.time()
    while not _stop.is_set():
        job = None
        with _cv:
            now = time.time()
            due = [(p["due"], u) for u, p in _pending.items()]
            if due:
                d, u = min(due)
                if d <= now:
                    job = (u, _pending.pop(u))
                else:
                    _cv.wait(min(d - now, 30))
            else:
                _cv.wait(30)
        if job is not None:
            try:
                _work_one(*job)
            except Exception as ex:  # noqa: BLE001 - Hintergrund darf nie sterben
                print(f"gedaechtnis: Abgleich {type(ex).__name__}: {str(ex)[:200]}", flush=True)
        if time.time() - last_flush > 60:
            last_flush = time.time()
            try:
                flush_mirror()
            except Exception as ex:  # noqa: BLE001
                print(f"gedaechtnis: Spiegeln {type(ex).__name__}: {str(ex)[:200]}", flush=True)


def start():
    _load_bound()
    if _thread[0] is not None:
        return
    _stop.clear()
    th = threading.Thread(target=_loop, name="gedaechtnis", daemon=True)
    _thread[0] = th
    th.start()
    schedule_all(delay=20)               # Bestand nach dem Start abgleichen (Modell/Texte geaendert, CLI)


def stop():
    _stop.set()
    with _cv:
        _cv.notify_all()
    th = _thread[0]
    _thread[0] = None
    if th is not None:
        th.join(timeout=5)
    try:
        flush_mirror(gap=0)
    except Exception:  # noqa: BLE001
        pass


# --- recall / remember / core ----------------------------------------------------------------------------------------
def _not_bound():
    return Fail(409, t("Kein Gedächtnis gebunden – bitte unter Einstellungen › Gedächtnis eine eigene Datenbank wählen."),
                "not_bound")


def _db_gone():
    return Fail(404, t("Die Gedächtnis-Datenbank ist nicht erreichbar (Papierkorb, gelöscht oder außerhalb der Seitenbäume dieses Tokens)."), "not_found")


def _core_gone():
    return Fail(404, t("Die Ich-Kern-Seite ist nicht erreichbar (Papierkorb, gelöscht oder außerhalb der Seitenbäume dieses Tokens)."), "not_found")


def _check(c, tok, uid, nid, minimum, gone=_db_gone):
    """Rechte des Tokens (roots, Rolle) und Eigentum -> sonst Fail (404 mit deutschem Text wie andere Saga-Endpunkte)."""
    if tok is not None:
        try:
            owner, _role = api_v1._guard(c, tok, nid, minimum)
        except Fail as f:
            if f.status == 404:
                raise gone()
            raise
        if owner != uid:
            raise gone()
    elif not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone():
        raise gone()


def _rate(tok):
    from saga import apitok
    if tok is None:
        return
    now = time.time()
    with apitok._lock:
        wait = apitok._take(("memory", tok.id), RATE, now)
    if wait:
        raise Fail(429, t("Zu viele Anfragen – bitte kurz warten."), extra={"retry_after": wait})


def _missing(c, uid, dbid, model, n):
    return [r[0] for r in c.execute(
        "SELECT n.id FROM note n LEFT JOIN mem_vec v ON v.note_id = n.id AND v.model = ? AND v.dim > 0 WHERE n.uid=? AND n.parent_id=? "
        "AND n.deleted_at IS NULL AND n.archived IS NULL AND n.is_template=0 AND v.note_id IS NULL ORDER BY n.id LIMIT ?",
        (model, uid, dbid, n)).fetchall()]


def recall(uid, text, projekt=None, limit=None, tok=None, now=None, count=True):
    """-> dict wie docs/MEMORY.md 5 (ohne kontext). Zaehlt die Treffer (count) und spiegelt faellige."""
    t0 = time.perf_counter()
    if not isinstance(text, str) or not text.strip():
        raise Fail(400, t("„text“ fehlt."))
    if projekt is not None and not isinstance(projekt, str):
        raise Fail(400, t("ungültige Angabe") + " (projekt)")
    if isinstance(limit, bool):
        raise Fail(400, t("ungültige Angabe") + " (limit)")
    try:
        limit = LIMIT_MAX if limit is None else int(limit)
    except (TypeError, ValueError):
        raise Fail(400, t("ungültige Angabe") + " (limit)")
    if not 1 <= limit <= LIMIT_MAX:
        raise Fail(400, t("„limit“: 1 bis {n}.", n=LIMIT_MAX))
    cut = len(text) > TEXT_MAX
    text = text[:TEXT_MAX]
    now = now or time.time()
    ec = ecfg()
    model = model_key(ec)
    ms = {"embed": 0.0, "suche": 0.0}
    hint = None
    # Schritt 1 (kurz, Verbindung danach zurueck in den Pool): Bindung, Rechte, Spalten, fehlende Vektoren
    with _db() as c:
        b = binding(uid, c)
        if not b["db"]:
            raise _not_bound()
        _check(c, tok, uid, b["db"], "view")
        st = _db_state(c, uid, b["db"])
        if st is None:
            raise _db_gone()
        cols, _info = columns(st[0], b["map"])
        fill_rows, more = [], False
        if model:
            fill = _missing(c, uid, b["db"], model, RECALL_FILL + 1)
            more = len(fill) > RECALL_FILL
            fill_rows = _live_rows(c, uid, b["db"], cols, fill[:RECALL_FILL]) if fill else []
    w = b["weights"]
    quelle, qv = "trigramm", None
    # Schritt 2 (ohne Datenbank-Verbindung): Frage einbetten - Gesamtfrist query_timeout_ms; danach mit der Restzeit die
    # bis zu RECALL_FILL Zeilen ohne Vektor (scheitert das, holt sie der Hintergrund)
    if model:
        tmo = max(0.2, min(10.0, int(ec.get("query_timeout_ms") or 1200) / 1000))
        te = time.perf_counter()
        qv = _qget((model, text))
        if qv is None:
            try:
                qv = embed([text], ec, timeout=tmo)[0]
                _qput((model, text), qv)
                _errors.pop(uid, None)
            except EmbedError as ex:
                hint = "embed_timeout" if ex.code == "timeout" else "embed_error"
                _note_error(uid, ex)
        rest = tmo - (time.perf_counter() - te)
        if qv is not None and fill_rows and rest >= 0.1:
            try:
                mat = embed([r[1] for r in fill_rows], ec, timeout=rest)
                with _db() as c, c:
                    _upsert(c, uid, [(r[0], r[2], r[3]["abrufe"], r[3]["letzter_abruf"]) for r in fill_rows], model, mat)
                hint = "nachgeholt"
            except EmbedError:
                more = True
        elif fill_rows:
            more = True
        ms["embed"] = (time.perf_counter() - te) * 1000
        if more:
            schedule(uid, whole=True, delay=0.5)
    # Schritt 3: Suche, Bewertung, Zaehler
    with _db() as c:
        ts = time.perf_counter()
        cand = {}
        if qv is not None:
            v = vectors(c, uid, model)
            if len(v.ids) and v.mat.shape[1] == qv.shape[0]:
                quelle = "embeddings"
                rel = v.mat @ qv
                idx = np.nonzero(rel >= w["rel_min"])[0]
                if len(idx) > CAND_MAX:
                    idx = idx[np.argsort(-rel[idx])[:CAND_MAX]]
                cand = {int(v.ids[i]): float(rel[i]) for i in idx}
        if quelle == "trigramm":
            x = word_index(c, uid, b["db"], cols)
            rr = trgm_rel(x, text)
            best = sorted(((s, i) for i, s in rr.items() if s >= w["rel_min_trgm"]), reverse=True)[:CAND_MAX]
            cand = {x.ids[i]: s for s, i in best}
        items = []
        if cand:
            for nid, title, props, created, hits, lh in c.execute(
                    "SELECT n.id, n.title, n.props, n.created, v.hits, v.last_hit FROM note n LEFT JOIN mem_vec v ON v.note_id = n.id "
                    "AND v.uid = n.uid WHERE n.uid=? AND n.parent_id=? AND n.id = ANY(?) AND n.deleted_at IS NULL "
                    "AND n.archived IS NULL AND n.is_template=0", (uid, b["db"], list(cand))).fetchall():
                f = fields_of(cols, title, _json_load(props, {}))
                h = hits if hits is not None else int(f["abrufe"] or 0)
                fa = factors(cand[nid], now, created, h, lh, f["letzter_abruf"], f["datum"], f["wichtigkeit"], projekt,
                             f["projekt"], w)
                s = score(fa, w)
                if is_hit(fa, s, w, quelle):
                    items.append((s, fa["rel"], nid, fa, f, hits is not None))
        top = rank(items, limit)
        ms["suche"] = (time.perf_counter() - ts) * 1000
        if count and top:
            with c:
                for s, _r, nid, _fa, f, has in top:
                    if has:
                        c.execute("UPDATE mem_vec SET hits=hits+1, last_hit=? WHERE note_id=? AND uid=?", (now, nid, uid))
                    else:
                        c.execute("INSERT INTO mem_vec (note_id, uid, model, dim, vec, text_hash, hits, last_hit, updated) "
                                  "VALUES (?,?,'',0,?,'',?,?,?) ON CONFLICT (note_id) DO UPDATE SET hits=mem_vec.hits+1, last_hit=EXCLUDED.last_hit",
                                  (nid, uid, b"", int(f["abrufe"] or 0) + 1, now, now))
                due = c.execute("SELECT note_id, hits, last_hit FROM mem_vec WHERE uid=? AND note_id = ANY(?) AND "
                                "(mirrored IS NULL OR mirrored <= ?)", (uid, [x[2] for x in top], now - MIRROR_GAP)).fetchall()
                if due:
                    mirror(c, uid, due, cols, b["db"], now)
    out = []
    for s, _r, nid, fa, f, _h in top:
        wi = f["wichtigkeit"]
        out.append({"id": nid, "titel": f["titel"], "inhalt": f["inhalt"][:CONTENT_MAX], "projekt": f["projekt"], "art": f["art"],
                    "schlagworte": f["schlagworte"], "datum": f["datum"],
                    "wichtigkeit": (int(wi) if wi is not None and wi == int(wi) else wi),
                    "score": round(s, 4), "gruende": {k: round(v, 4) for k, v in fa.items()}})
    ms["gesamt"] = (time.perf_counter() - t0) * 1000
    res = {"quelle": quelle, "modell": model or None, "abgeschnitten": cut, "treffer": out,
           "ms": {k: round(v, 1) for k, v in ms.items()}}
    if hint:
        res["hinweis"] = hint
    return res


def _opt_match(p, v):
    low = str(v).strip().casefold()
    for o in p.get("options") or []:
        if isinstance(o, dict) and str(o.get("name") or "").strip().casefold() == low:
            return o.get("id")
    return None


def _opt_names(p):
    return [str(o.get("name") or "") for o in p.get("options") or [] if isinstance(o, dict)][:100]


def remember(uid, a, tok=None):
    """Neue Erinnerung -> (201, dict wie docs/MEMORY.md 5)."""
    from saga.notes import clean_date
    if not isinstance(a, dict):
        raise Fail(400, t("ungültige Angabe"))
    titel = str(a.get("titel") or "").strip()[:300] if isinstance(a.get("titel"), (str, type(None))) else None
    inhalt = a.get("inhalt")
    if not titel:
        raise Fail(400, t("„titel“ fehlt."))
    if not isinstance(inhalt, str) or not inhalt.strip():
        raise Fail(400, t("„inhalt“ fehlt."))
    if len(inhalt) > CONTENT_MAX:
        raise Fail(400, t("„inhalt“: höchstens {n} Zeichen.", n=CONTENT_MAX))
    with _db() as c:
        b = binding(uid, c)
        if not b["db"]:
            raise _not_bound()
        _check(c, tok, uid, b["db"], "edit")
        st = _db_state(c, uid, b["db"])
        if st is None:
            raise _db_gone()
    cols, _info = columns(st[0], b["map"])
    if not cols.get("inhalt"):
        raise Fail(400, t("Die Gedächtnis-Datenbank hat keine Text-Spalte „{name}“.", name=b["map"]["inhalt"]), "missing_column")
    props, ignored, unknown, options = {cols["inhalt"]["id"]: inhalt.strip()}, [], {}, {}
    for f in ("projekt", "art"):
        v = a.get(f)
        if v in (None, ""):
            continue
        if not isinstance(v, str):
            raise Fail(400, t("ungültige Angabe") + f" ({f})")
        if not cols.get(f):
            ignored.append(f)
            continue
        oid = _opt_match(cols[f], v)
        if oid is None:
            unknown[f] = [v.strip()[:100]]
            options[f] = _opt_names(cols[f])
        else:
            props[cols[f]["id"]] = oid
    tags = a.get("schlagworte")
    if isinstance(tags, str):
        tags = [x for x in (y.strip() for y in tags.split(",")) if x]
    if tags not in (None, []):
        if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(x, str) and x.strip() for x in tags):
            raise Fail(400, t("ungültige Angabe") + " (schlagworte)")
        if not cols.get("schlagworte"):
            ignored.append("schlagworte")
        elif cols["schlagworte"]["type"] == "text":
            props[cols["schlagworte"]["id"]] = ", ".join(x.strip() for x in tags)
        else:
            ids, bad = [], []
            for x in tags:
                oid = _opt_match(cols["schlagworte"], x)
                (bad.append(x.strip()[:100]) if oid is None else ids.append(oid))
            if bad:
                unknown["schlagworte"] = bad
                options["schlagworte"] = _opt_names(cols["schlagworte"])
            else:
                props[cols["schlagworte"]["id"]] = list(dict.fromkeys(ids))
    for f, lo, hi in (("wichtigkeit", 0, 10), ("valenz", -1, 1), ("erregung", 0, 1)):
        v = a.get(f)
        if v is None:
            continue
        x = _num(v)
        if x is None or not lo <= x <= hi:
            raise Fail(400, t("„{k}“: Zahl von {a} bis {b}.", k=f, a=lo, b=hi))
        if not cols.get(f):
            ignored.append(f)
        else:
            props[cols[f]["id"]] = int(x) if x == int(x) else x
    d = a.get("datum")
    if d is not None:
        try:
            d = clean_date(d) if isinstance(d, str) else None
        except ValueError:
            d = None
        if d is None:
            raise Fail(400, t("ungültiges Datum"))
    if cols.get("datum"):
        props[cols["datum"]["id"]] = d or datetime.now(TZ).date().isoformat()
    elif d is not None:
        ignored.append("datum")
    if cols.get("abrufe"):
        props[cols["abrufe"]["id"]] = 0
    if unknown:
        raise Fail(400, t("Unbekannte Auswahl-Werte – Saga legt keine neuen an. Bitte einen vorhandenen Wert nehmen oder weglassen."),
                   "unknown_option", extra={"unbekannt": unknown, "optionen": options})
    if tok is not None:
        # Bereich "memory" reicht fuer genau diese Zeile in der gebundenen Datenbank (Rechte/roots pruefte _check, die
        # Zeilen-Pruefungen von row_create laufen mit write:db fuer diesen einen Aufruf)
        from saga import apitok
        wt = apitok.Tok(tok.id, tok.uid, tok.name, tok.prefix, set(tok.scopes) | {"read:db", "write:db"}, tok.roots, tok.expires,
                        grant_id=getattr(tok, "grant_id", None))
        _st, d2 = api_v1.row_create(wt, b["db"], {"title": titel, "properties": props})
        nid = d2["id"]
    else:
        from saga.pages import note_create
        r = note_create(uid, {"title": titel, "parent_id": b["db"], "props": props})
        dd = json.loads(r.body)
        if r.status_code >= 400:
            raise Fail(r.status_code, dd.get("error") or "error")
        nid = dd["id"]
    ec = ecfg()
    model = model_key(ec)
    out = {"id": nid, "eingebettet": False, "aehnlich": []}
    with _db() as c:
        row = _live_rows(c, uid, b["db"], cols, [nid])
    if not row:
        return 201, out
    nid_, txt, h, f = row[0]
    sims = []
    if model:
        try:
            mat = embed([txt], ec)
            with _db() as c, c:
                _upsert(c, uid, [(nid, h, f["abrufe"], f["letzter_abruf"])], model, mat)
            out["eingebettet"] = True
            with _db() as c:
                v = vectors(c, uid, model)
            if len(v.ids) and v.mat.shape[1] == mat.shape[1]:
                rel = v.mat @ mat[0]
                sims = [(float(rel[i]), int(v.ids[i])) for i in np.nonzero(rel >= SIMILAR)[0] if int(v.ids[i]) != nid]
        except EmbedError as ex:
            print(f"gedaechtnis: Konto {_uh(uid)}: Einbetten neuer Erinnerung gescheitert ({ex.code})", flush=True)
            _note_error(uid, ex)
            schedule(uid, [nid], delay=30)
    else:
        with _db() as c:
            x = word_index(c, uid, b["db"], cols)
        sims = [(s, x.ids[i]) for i, s in trgm_rel(x, txt).items() if s >= SIMILAR and x.ids[i] != nid]
    sims = sorted(sims, key=lambda z: (-z[0], z[1]))[:5]
    if sims:
        with _db() as c:
            titles = dict(c.execute("SELECT id, title FROM note WHERE uid=? AND id = ANY(?)", (uid, [s[1] for s in sims])).fetchall())
        out["aehnlich"] = [{"id": i, "titel": titles.get(i) or "", "rel": round(s, 4)} for s, i in sims if i in titles]
        if out["aehnlich"]:
            out["hinweis"] = t("Vielleicht lieber eine dieser Erinnerungen ergänzen.")
    if ignored:
        out["ignoriert"] = ignored
    return 201, out


def core(uid, tok=None):
    with _db() as c:
        b = binding(uid, c)
        if not b["core"]:
            raise Fail(409, t("Kein Ich-Kern gebunden – bitte unter Einstellungen › Gedächtnis eine eigene Seite wählen."),
                       "not_bound")
        _check(c, tok, uid, b["core"], "view", _core_gone)
    if tok is not None:
        _st, d = api_v1.page_read(tok, b["core"], "markdown")
    else:
        from saga.page_tools import blocks_to_md
        with _db() as c:
            r = c.execute("SELECT id, title, body, format, updated FROM note WHERE id=? AND uid=? AND deleted_at IS NULL",
                          (b["core"], uid)).fetchone()
        if not r:
            raise _core_gone()
        md = blocks_to_md(_json_load(r[2], {}).get("blocks") or [], lambda h: h) if r[3] == "blocks" else (r[2] or "")
        d = {"id": r[0], "title": r[1], "markdown": md, "updated": r[4]}
    return {"id": d.get("id") or b["core"], "title": d.get("title") or "", "markdown": d.get("markdown") or "", "updated": d.get("updated")}


# --- Rahmen fuer Hooks / MCP -------------------------------------------------------------------------------------------
def frame(obj, label="memory"):
    from saga import tools
    return tools._data(obj, label)


def recall_context(res):
    """Treffer als kurzer Text im saga-data-Rahmen ("" ohne Treffer)."""
    if not res.get("treffer"):
        return ""
    lines = [t("Erinnerungen aus Saga (Gedächtnis), passend zur Eingabe:")]
    for n, x in enumerate(res["treffer"], 1):
        meta = " · ".join(str(v) for v in (x.get("projekt"), x.get("art"), x.get("datum")) if v)
        lines.append(f"{n}. {x['titel']}" + (f" [{meta}]" if meta else "") + f" (id {x['id']}, score {x['score']})")
        if x.get("inhalt"):
            lines.append("   " + x["inhalt"][:800].replace("\n", "\n   "))
    return frame("\n".join(lines))


def core_context(d):
    return frame(f"# {d['title']}\n\n{d['markdown']}", "memory:core")


# --- REST /api/v1/memory -----------------------------------------------------------------------------------------------
def _resp(st, d, headers=None):
    h = {"Cache-Control": "no-store"}
    h.update(headers or {})
    return JSONResponse(d, status_code=st, headers=h)


def _fail(f):
    h = {"Retry-After": str(f.extra["retry_after"])} if f.status == 429 and f.extra.get("retry_after") else None
    extra = {k: v for k, v in f.extra.items() if k != "retry_after"}
    return _resp(f.status, dict(extra, error=f.msg, code=f.code), h)


@router.get("/api/v1/memory/core")
async def v1_core(request: Request):
    from saga.apitok import authed
    tok, err = authed(request, scope="memory")
    if err is not None:
        return err
    try:
        d = await run_in_threadpool(core, tok.uid, tok)
    except Fail as f:
        return _fail(f)
    d["kontext"] = core_context(d)
    return _resp(200, d)


@router.post("/api/v1/memory/recall")
async def v1_recall(request: Request):
    from saga.apitok import authed
    tok, err = authed(request, scope="memory")
    if err is not None:
        return err
    b = await _body(request)
    if b is None:
        return _resp(400, {"error": t("ungültige Anfrage"), "code": "bad_request"})
    try:
        _rate(tok)
        d = await run_in_threadpool(recall, tok.uid, b.get("text"), b.get("projekt"), b.get("limit"), tok)
    except Fail as f:
        return _fail(f)
    d["kontext"] = recall_context(d)
    return _resp(200, d)


@router.post("/api/v1/memory/remember")
async def v1_remember(request: Request):
    from saga.apitok import authed
    tok, err = authed(request, write=True, scope="memory")
    if err is not None:
        return err
    b = await _body(request)
    if b is None:
        return _resp(400, {"error": t("ungültige Anfrage"), "code": "bad_request"})
    try:
        st, d = await run_in_threadpool(remember, tok.uid, b, tok)
    except Fail as f:
        return _fail(f)
    return _resp(st, d)


# --- MCP (saga/tools.py) ---------------------------------------------------------------------------------------------
def mcp_recall(tok, a):
    _rate(tok)
    lim = a.get("limit")
    if isinstance(lim, bool):
        raise Fail(400, t("ungültige Angabe") + " (limit)")
    d = recall(tok.uid, a.get("text"), a.get("projekt"), lim, tok)
    d.pop("ms", None)
    return frame(d)


def mcp_remember(tok, a):
    _st, d = remember(tok.uid, a, tok)
    return frame(d)


# --- Web: Bindung je Konto ---------------------------------------------------------------------------------------------
def _title(c, nid):
    r = c.execute("SELECT title FROM note WHERE id=?", (nid,)).fetchone()
    return (r[0] or "") if r else ""


def account_view(uid):
    with _db() as c:
        b = binding(uid, c)
        st = _db_state(c, uid, b["db"]) if b["db"] else None
        cols, info = columns(st[0] if st else {"props": []}, b["map"])
        core_ok = b["core"] and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (b["core"], uid)).fetchone()
        dbs = [{"id": r[0], "title": r[1] or ""} for r in c.execute(
            "SELECT id, title FROM note WHERE uid=? AND kind='database' AND deleted_at IS NULL AND archived IS NULL AND is_template=0 "
            "ORDER BY updated DESC LIMIT 200", (uid,)).fetchall()]
        out = {"db": {"id": b["db"], "title": st[1]} if st else None,
               "core": {"id": b["core"], "title": _title(c, b["core"])} if core_ok else None,
               "map": b["map"], "columns": info if st else {}, "missing_required": [] if (not st or cols.get("inhalt")) else ["inhalt"],
               "weights": b["weights"], "weights_default": dict(WEIGHTS), "databases": dbs}
    out["status"] = status(uid)
    return out


def _set_kv(c, uid, k, v):
    if v is None:
        c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, k))
    else:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, k, v))


def account_put(uid, b):
    """-> (status, dict). Siehe docs/MEMORY.md 1.2."""
    from saga.sharing import Access
    if not isinstance(b, dict):
        return 400, {"error": t("ungültige Anfrage"), "code": "bad_request"}
    rebind = False
    with _db() as c, c:
        acc = Access(c, uid)
        for k in ("db", "core"):
            if k not in b or b[k] is None:
                continue
            i = _int_or_none(b[k]) if not isinstance(b[k], bool) else None
            if i is None or not acc.role(i):
                return 404, {"error": t("Datenbank oder Seite nicht gefunden."), "code": "not_found"}
            if acc.owner(i) != uid:
                return 403, {"error": t("Nur eigene Datenbanken und Seiten – geteilte gehen nicht."), "code": "forbidden"}
            r = c.execute("SELECT kind FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (i, uid)).fetchone()
            if not r:
                return 404, {"error": t("Datenbank oder Seite nicht gefunden."), "code": "not_found"}
            if k == "db" and r[0] != "database":
                return 400, {"error": t("Das ist keine Datenbank."), "code": "bad_request"}
        if "map" in b and b["map"] is not None:
            m = b["map"]
            if not isinstance(m, dict) or any(k not in FIELDS or not isinstance(v, str) or not v.strip() or len(v) > 100
                                              for k, v in m.items()):
                return 400, {"error": t("ungültige Angabe") + " (map)", "code": "bad_request"}
        if "weights" in b and b["weights"] is not None:
            w = b["weights"]
            if not isinstance(w, dict) or any(k not in _W_RANGE or isinstance(v, bool) or not isinstance(v, (int, float))
                                              or not math.isfinite(v) or not _W_RANGE[k][0] <= v <= _W_RANGE[k][1]
                                              for k, v in w.items()):
                return 400, {"error": t("ungültige Angabe") + " (weights)", "code": "bad_request"}
        old = binding(uid, c)
        if "db" in b:
            _set_kv(c, uid, "memory.db", None if b["db"] is None else str(int(b["db"])))
            rebind = (b["db"] or None) != old["db"]
        if "core" in b:
            _set_kv(c, uid, "memory.core", None if b["core"] is None else str(int(b["core"])))
        if "map" in b:
            m = {k: v.strip() for k, v in (b["map"] or {}).items() if v.strip() != DEFAULT_MAP[k]}
            _set_kv(c, uid, "memory.map", json.dumps(m, ensure_ascii=False) if m else None)
            rebind = rebind or m != {k: v for k, v in old["map_raw"].items() if k in FIELDS}
        if "weights" in b:
            w = {k: float(v) for k, v in (b["weights"] or {}).items() if float(v) != WEIGHTS[k]}
            _set_kv(c, uid, "memory.weights", json.dumps(w) if w else None)
    _load_bound()
    with _bound_lock:
        nb = binding(uid)["db"]
        if nb:
            _bound[uid] = nb
        else:
            _bound.pop(uid, None)
    if rebind:
        forget(uid)
        schedule(uid, whole=True, delay=0.5)
    return 200, account_view(uid)


def bind_restored(uid, dbid):
    """Persoenliches Backup hat eine Bindung eingespielt (saga.userbackup._import_memory): Zuhoerer + Abgleich."""
    _load_bound()
    with _bound_lock:
        _bound[uid] = dbid
    forget(uid)
    schedule(uid, whole=True, delay=1)


@router.get("/api/account/memory")
def api_memory_get(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _resp(401, {"error": "unauthorized"})
    return _resp(200, account_view(uid))


@router.put("/api/account/memory")
async def api_memory_put(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _resp(401, {"error": "unauthorized"})
    b = await _body(request)
    st, d = await run_in_threadpool(account_put, uid, b)
    return _resp(st, d)


PROBE_S = 3.0                    # "Jetzt vorbereiten": Probe-Aufruf des Endpunkts (Gesamtfrist)
PROBE_RATE = 6                   # je Konto und Minute (jeder Aufruf belegt einen der EMBED_WORKERS Plaetze bis zu PROBE_S)


@router.post("/api/account/memory/embed")
async def api_memory_embed(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _resp(401, {"error": "unauthorized"})
    from saga import apitok                      # Pruefer 2581: sonst koennte ein Konto den Endpunkt (und die 4 Plaetze) fluten
    with apitok._lock:
        wait = apitok._take(("memory-embed", uid), PROBE_RATE, time.time())
    if wait:
        return _resp(429, {"error": t("Zu viele Anfragen – bitte kurz warten."), "code": "rate_limited"}, {"Retry-After": str(wait)})
    if not (await run_in_threadpool(binding, uid))["db"]:
        f = _not_bound()
        return _resp(409, {"error": f.msg, "code": f.code})
    ec = ecfg()
    if configured(ec):
        # Knopf "Jetzt vorbereiten": erst kurz pruefen, ob der Dienst antwortet - sonst sofort ehrlich melden (statt
        # "wird vorbereitet" und eines Hintergrundlaufs, der gleich scheitert)
        try:
            await run_in_threadpool(embed, ["Saga"], ec, PROBE_S)
            _errors.pop(uid, None)
        except EmbedError as ex:
            _note_error(uid, ex)
            return _resp(503, {"error": _embed_msg(ex), "code": "embed_unreachable", "status": await run_in_threadpool(status, uid)})
    schedule(uid, whole=True, delay=0)
    return _resp(200, {"ok": True, "queued": 1 if configured(ec) else 0, "status": await run_in_threadpool(status, uid)})


# --- Admin (admin.py: /api/ai/embed...) --------------------------------------------------------------------------------
def admin_stats():
    model = model_key()
    with _db() as c:
        n_acc = len(bound_uids())
        models = {r[0] or "-": r[1] for r in c.execute("SELECT model, count(*) FROM mem_vec WHERE dim>0 GROUP BY model").fetchall()}
        rows = 0
        for uid, dbid in bound_uids().items():
            rows += c.execute("SELECT count(*) FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND archived IS NULL "
                              "AND is_template=0", (uid, dbid)).fetchone()[0]
    emb = models.get(model, 0) if model else 0
    cb, cn = cache_total()
    return {"accounts": n_acc, "rows": rows, "embedded": emb, "stale": max(0, rows - emb) if model else 0,
            "running": bool(_running), "models": models,
            "cache": {"mb": round(cb / 1048576, 1), "accounts": cn, "max_mb": CACHE_MAX // 1048576}}


def admin_view():
    c = ecfg()
    s = {k: c.get(k) for k in ("enabled", "provider", "url", "model", "keep_alive", "timeout", "query_timeout_ms")}
    s["has_key"] = bool(c.get("api_key"))
    try:
        stats = admin_stats()
    except Exception:  # noqa: BLE001 - Anzeige
        stats = None
    return {"settings": s, "configured": configured(c), "where": where(c), "stats": stats}


def admin_values(b, cur):
    """JSON des Admins -> (werte, fehler). Leerer Schluessel = bisheriger."""
    if not isinstance(b, dict):
        return None, t("ungültige Anfrage")
    prov = b.get("provider") if b.get("provider") in PROVIDERS else "ollama"
    url = str(b.get("url") or "").strip()
    if url:
        url, err = norm_url(url, prov)
        if err:
            return None, err
    ka = str(b.get("keep_alive") if b.get("keep_alive") is not None else cur.get("keep_alive") or "").strip()[:8]
    if ka and not KEEP_RE.fullmatch(ka):
        return None, t("keep_alive: z. B. 5m, 30m, 2h oder -1")

    def num(k, lo, hi, d):
        try:
            return max(lo, min(hi, int(b.get(k) if b.get(k) is not None else cur.get(k) or d)))
        except (TypeError, ValueError):
            return d
    v = {"enabled": b.get("enabled") is True, "provider": prov, "url": url or "", "model": str(b.get("model") or "").strip()[:200],
         "api_key": str(b.get("api_key") or "").strip()[:500], "keep_alive": ka, "timeout": num("timeout", 2, 120, 15),
         "query_timeout_ms": num("query_timeout_ms", 200, 10000, 1200), "pinned": list(cur.get("pinned") or [])}
    if v["enabled"] and (not v["url"] or not v["model"]):
        return None, t("Zum Einschalten Adresse und Modell angeben.")
    return v, None


def admin_save(b):
    """-> (status, dict)."""
    from saga import ai
    cur = ecfg()
    v, err = admin_values(b, cur)
    if err:
        return 400, {"error": err}
    if v["url"]:
        ips, err = ai.resolve(v["url"])
        if err:
            return 400, {"error": err}
        v["pinned"] = ips
    else:
        v["pinned"] = []
    clear = b.get("clear_key") is True
    auth.save_setting("ai_embed", v)
    if clear:
        with _db() as c, c:
            st = json.loads(c.execute("SELECT v FROM setting WHERE k='ai_embed'").fetchone()[0])
            st["api_key"] = ""
            c.execute("UPDATE setting SET v=? WHERE k='ai_embed'", (json.dumps(st, ensure_ascii=False),))
        auth._settings_cache["v"] = None
    new = ecfg()
    if model_key(cur) != model_key(new) or cur.get("url") != new.get("url") or cur.get("provider") != new.get("provider"):
        forget()
        with _qlock:
            _qcache.clear()
        schedule_all(delay=1)
    return 200, admin_view()


def admin_test(b):
    """Verbindungstest mit ungespeicherten Werten -> {ok, lines, dim, ms}."""
    from saga import ai
    cur = ecfg()
    v, err = admin_values(dict(b or {}, enabled=True), cur)
    if err:
        return {"ok": False, "lines": [f"✗ {err}"]}
    if not v["api_key"]:
        v["api_key"] = cur.get("api_key") or ""
    ips, err = ai.resolve(v["url"])
    if err:
        return {"ok": False, "lines": [f"✗ {err}"]}
    lines = [t("✓ {host} aufgelöst: {ips}", host=urlparse(v["url"]).hostname, ips=", ".join(ips[:4]))]
    v["pinned"] = ips
    if where(v) == "internet":
        lines.append(t("Hinweis: Der Endpunkt liegt im Internet – Erinnerungen verlassen dann den Server."))
    t0 = time.perf_counter()
    try:
        m = embed(["Saga Verbindungstest"], v)
    except EmbedError as ex:
        lines.append("✗ " + _embed_msg(ex))
        return {"ok": False, "lines": lines}
    ms = (time.perf_counter() - t0) * 1000
    lines.append(t("✓ {model}: {dim} Dimensionen in {ms} ms", model=v["model"], dim=m.shape[1], ms=int(ms)))
    return {"ok": True, "lines": lines, "dim": int(m.shape[1]), "ms": round(ms, 1)}


def _embed_msg(ex):
    return {"timeout": t("Der Embedding-Endpunkt antwortet nicht rechtzeitig."),
            "blocked": t("Verbindung zum Embedding-Endpunkt nicht erlaubt (Adresse geändert? Neu speichern)."),
            "unreachable": t("Embedding-Endpunkt nicht erreichbar."),
            "redirect": t("Der Embedding-Endpunkt leitet weiter – Weiterleitungen sind nicht erlaubt."),
            "http": t("Der Embedding-Endpunkt antwortet mit Fehler {code}.", code=str(ex)),
            "bad_response": t("Unerwartete Antwort des Embedding-Endpunkts (kein Embedding-Modell?)."),
            "not_configured": t("Kein Embedding-Endpunkt eingetragen.")}.get(ex.code, ex.code)


# --- Kommandozeile: python run.py gedaechtnis einbetten [--konto <name>] --------------------------------------------
def cli(args):
    import sys
    if not args or args[0] != "einbetten":
        sys.exit("Aufruf: python run.py gedaechtnis einbetten [--konto <name>]")
    konto = None
    if "--konto" in args:
        i = args.index("--konto")
        if i + 1 >= len(args):
            sys.exit("Aufruf: python run.py gedaechtnis einbetten [--konto <name>]")
        konto = args[i + 1]
    _load_bound()
    uids = [konto] if konto else sorted(bound_uids())
    if not configured():
        print("Hinweis: kein Embedding-Endpunkt eingetragen (Admin › KI › Embeddings) - recall nutzt Trigramme.")
    for uid in uids:
        if not binding(uid)["db"]:
            print(f"{uid}: kein Gedaechtnis gebunden")
            continue
        r = sync(uid, log=print)
        print(f"{uid}: {r['rows']} Zeilen, {r['embedded']} neu eingebettet, {r['dropped']} entfernt, {r['failed']} gescheitert")
