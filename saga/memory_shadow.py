"""Schattenmodus Phase 1 - nur messen (2.6, Bugs-DB 2603, Vertrag docs/MEMORY.md 12/13, Vektoren
tests/vectors/memory_shadow.json aus tools/memory_shadow_vectors.py, Reihe tests/gedaechtnis_schatten.py).

Ab Werk aus (user_kv memory.shadow fehlt). Eingeschaltet rechnet jeder GEZAEHLTE recall nach dem Commit der Zaehler mit,
was Verknuepfungen am Ergebnis aendern wuerden (Ausbreitung <= 2 Schritte mit Hub-Daempfung, Gesamtwert ohne Limit,
Obergrenze fuer eingespielten Text) und legt eine Spur mem_trace an - die Antwort von recall bleibt unveraendert. Fehler
im Schattenteil werden gefangen (Log nur der Typ).

- mem_link (a < b): entstehen "aehnlich" (neuer/geaenderter Vektor: beste K Nachbarn ueber der Schwelle; Nachziehen beim
  Einschalten bzw. CLI "verknuepfen"), "gemeinsam" (Paar der Basis-Treffer in >= 3 Spuren der letzten 30 Tage) und ueber
  recall_used (einzige Verstaerkung - Auftauchen allein aendert nie ein Gewicht). Gewicht klingt lazy bis zur Untergrenze
  ab (w_eff, nie geschrieben). Nie zu Schnappschuessen/sensiblen Zeilen.
- Je Konto ein Zwischenspeicher der Links (numpy, gerichtete Kanten), Abgleich ueber count/max(zuletzt)/max(angelegt) -
  hoechstens alle LINK_CHECK_S (eigene Schreibzugriffe verwerfen ihn sofort; Schreibzugriffe anderer Prozesse, z. B. CLI,
  wirken im recall spaetestens nach 5 s; der Bericht liest immer frisch); zaehlt mit den Vektoren in SAGA_MEMORY_CACHE_MB.
- mem_trace: nie der Fragetext - HMAC-SHA256 (Schluessel aus SECRET_KEY) und <= 8 Stichworte; 30 Tage (Aufraeumen
  stuendlich im Hintergrund von saga.memory), Links zu geloeschten Zeilen taeglich weg.
Reine Formeln (g0_*, w_eff, reinforce, hub, spread, cap, classify) ohne Datenbank - gegen die Vektoren geprueft."""
import hashlib
import hmac
import json
import math
import threading
import time
import unicodedata
from collections import OrderedDict

import numpy as np

from db import connect as _db

K = 5                            # Nachbarn je neuem Vektor ("aehnlich")
COS_MIN = 0.55
TRGM_MIN = 0.50
U0 = 0.05
HL = 60 * 86400                  # Halbwertszeit der Gewichte
DELTA = 0.5
A_MIN = 0.02
CAP = 2000                       # Obergrenze eingespielter Text (Zeichen)
SEEDS_MAX = 50
G_GEM, U_GEM, GEM_N, GEM_DAYS = 0.15, 0.05, 3, 30
G_USED, U_USED, STRENGTH, U_STEP, U_MAX = 0.30, 0.10, 0.2, 0.05, 0.5
TRACE_KEEP = 30 * 86400
USED_WINDOW = 2 * 3600
BLOCK = 256
TRACE_SHADOW_MAX = 30
GRAPH_LINKS_MAX = 20000
REPORT_MAX = 15000
HER = {"aehnlich": "a", "gemeinsam": "g", "hand": "h"}


def _uh(uid):
    return hashlib.sha256(str(uid).encode()).hexdigest()[:8]


# --- reine Formeln (docs/MEMORY.md 12.2-12.5) -------------------------------------------------------------------------
def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def g0_cos(cos):
    return None if cos < COS_MIN else 0.2 + 0.4 * clamp((cos - COS_MIN) / 0.45)


def g0_trgm(rel):
    return None if rel < TRGM_MIN else 0.2 + 0.4 * clamp((rel - TRGM_MIN) / 0.50)


def w_eff(g, u, zuletzt, gepinnt, t):
    """Gewicht zum Zeitpunkt t (Skalar oder numpy-Arrays)."""
    if isinstance(g, np.ndarray):
        dt = np.maximum(0.0, t - zuletzt)
        w = u + (g - u) * np.power(0.5, dt / HL)
        return np.where(gepinnt, g, w)
    if gepinnt:
        return g
    return u + (g - u) * 0.5 ** (max(0.0, t - zuletzt) / HL)


def reinforce(link, t):
    """recall_used: link {g, u, treffer, zuletzt, gepinnt} oder None (neu, Herkunft gemeinsam) -> neuer Stand."""
    if link is None:
        return {"g": G_USED, "u": U_USED, "treffer": 1, "zuletzt": t}
    w = w_eff(link["g"], link["u"], link["zuletzt"], link.get("gepinnt", False), t)
    g = min(1.0, w + STRENGTH * (1 - w))
    u = min(U_MAX, link["u"] + U_STEP, g)
    return {"g": g, "u": max(0.0, u), "treffer": int(link["treffer"]) + 1, "zuletzt": t}


def hub(n, deg):
    """Hub-Daempfung (Skalar oder Array deg)."""
    if n <= 1:
        return np.ones_like(deg, dtype=np.float64) if isinstance(deg, np.ndarray) else 1.0
    if isinstance(deg, np.ndarray):
        return np.log1p(n / (1.0 + deg)) / math.log1p(n)
    return math.log1p(n / (1.0 + deg)) / math.log1p(n)


class Graph:
    """Links eines Kontos als Arrays: ids (sortiert), je Link a/b (IDs und Index), g, u, zuletzt, gepinnt, herkunft, treffer;
    gerichtete Kanten src/dst/lk (jeder Link in beide Richtungen). Unveraenderlich - with_links() liefert eine Kopie mit
    geaenderten/neuen Links (eigene Schreibzugriffe ohne Neuladen aller Links)."""
    __slots__ = ("ids", "a", "b", "ai", "bi", "g", "u", "zul", "pin", "her", "tr", "src", "dst", "lk", "stamp", "pos")

    def __init__(self, rows, stamp=None):
        rows = list(rows)
        cols = list(zip(*rows)) if rows else [()] * 8
        n = len(rows)
        self.a = np.fromiter(cols[0], dtype=np.int64, count=n)
        self.b = np.fromiter(cols[1], dtype=np.int64, count=n)
        self.g = np.fromiter(cols[2], dtype=np.float64, count=n)
        self.u = np.fromiter(cols[3], dtype=np.float64, count=n)
        self.zul = np.fromiter(cols[4], dtype=np.float64, count=n)
        self.pin = np.fromiter((bool(x) for x in cols[5]), dtype=bool, count=n)
        self.her = list(cols[6]) if len(cols) > 6 and n else ["aehnlich"] * n
        self.tr = np.fromiter(cols[7], dtype=np.int64, count=n) if len(cols) > 7 and n else np.zeros(n, dtype=np.int64)
        self.stamp = stamp
        self._derive()

    def _derive(self):
        n = len(self.a)
        self.ids = np.unique(np.concatenate([self.a, self.b])) if n else np.zeros(0, dtype=np.int64)
        self.ai = np.searchsorted(self.ids, self.a)
        self.bi = np.searchsorted(self.ids, self.b)
        self.src = np.concatenate([self.ai, self.bi])
        self.dst = np.concatenate([self.bi, self.ai])
        self.lk = np.concatenate([np.arange(n), np.arange(n)])
        self.pos = None

    def with_links(self, rows, stamp):
        """Kopie mit rows [(a, b, g, u, zuletzt, gepinnt, herkunft, treffer)] (vorhandene Paare ersetzt, neue angehaengt)."""
        G = Graph.__new__(Graph)
        G.a, G.b, G.g, G.u, G.zul, G.pin, G.tr = (x.copy() for x in (self.a, self.b, self.g, self.u, self.zul, self.pin, self.tr))
        G.her = list(self.her)
        G.stamp = stamp
        if self.pos is None:
            self.pos = {(int(x), int(y)): k for k, (x, y) in enumerate(zip(self.a, self.b))}
        pos = dict(self.pos)
        new = []
        for r in rows:
            k = pos.get((int(r[0]), int(r[1])))
            if k is None:
                pos[(int(r[0]), int(r[1]))] = len(G.her) + len(new)
                new.append(r)
                continue
            G.g[k], G.u[k], G.zul[k], G.pin[k], G.her[k], G.tr[k] = r[2], r[3], r[4], bool(r[5]), r[6], r[7]
        if new:
            add = Graph(new)
            for k in ("a", "b", "g", "u", "zul", "pin", "tr"):
                setattr(G, k, np.concatenate([getattr(G, k), getattr(add, k)]))
            G.her += add.her
            G._derive()
        else:
            G.ids, G.ai, G.bi, G.src, G.dst, G.lk = self.ids, self.ai, self.bi, self.src, self.dst, self.lk
        G.pos = pos
        return G

    def nbytes(self):
        return int(sum(getattr(self, k).nbytes for k in ("ids", "a", "b", "ai", "bi", "g", "u", "zul", "pin", "tr", "src", "dst", "lk"))
                   + 64 * len(self.her) + (200 * len(self.pos) if self.pos else 0))

    def idx(self, node_ids):
        """Knoten-IDs -> Index (-1 = nicht im Graph)."""
        q = np.asarray(list(node_ids), dtype=np.int64)
        if not len(self.ids) or not len(q):
            return np.full(len(q), -1, dtype=np.int64)
        i = np.searchsorted(self.ids, q)
        i = np.minimum(i, len(self.ids) - 1)
        return np.where(self.ids[i] == q, i, -1)


def degrees(G, valid):
    """Grad je Knoten: Links zu lebenden, nicht sensiblen Nicht-Schnappschuessen (valid: bool je Knoten)."""
    ok = valid[G.ai] & valid[G.bi]
    n = len(G.ids)
    return (np.bincount(G.ai[ok], minlength=n) + np.bincount(G.bi[ok], minlength=n)).astype(np.float64)


def transfer(G, weff, h):
    """Uebertrag je gerichteter Kante t = w_eff * delta * sqrt(h_i h_j)."""
    return weff[G.lk] * DELTA * np.sqrt(h[G.src] * h[G.dst])


def _group_best(tgt, val, via):
    """-> (eindeutige Ziele, bester Wert, beste via, zweitbester Wert) - Gleichstand: kleinere via."""
    if not len(tgt):
        z = np.zeros(0)
        return np.zeros(0, dtype=np.int64), z, np.zeros(0, dtype=np.int64), z
    o = np.lexsort((via, -val, tgt))
    t_, v_, w_ = tgt[o], val[o], via[o]
    first = np.ones(len(t_), dtype=bool)
    first[1:] = t_[1:] != t_[:-1]
    fi = np.nonzero(first)[0]
    sec = np.zeros(len(fi))
    nxt = fi + 1
    has2 = (nxt < len(t_)) & (np.append(t_[1:], -1)[fi] == t_[fi])
    sec[has2] = v_[nxt[has2]]
    return t_[fi], v_[fi], w_[fi], sec


def spread(G, t, valid, seed_rel):
    """Ausbreitung (12.5). seed_rel: rel je Knoten (0 = kein Seed; Seeds sind gueltig). -> (A, via-ID oder -1, hop)
    je Knoten des Graphen. A1 von Seeds s != j; A2 ueber einfache Pfade s -> j -> k (s != k, j nicht sensibel); sensible
    und Schnappschuesse (valid False) nie Ziel, nie Durchgang."""
    n = len(G.ids)
    A = np.zeros(n)
    via = np.full(n, -1, dtype=np.int64)
    hop = np.zeros(n, dtype=np.int64)
    if not n:
        return A, via, hop
    ok = valid[G.src] & valid[G.dst] & (G.src != G.dst)
    m1 = ok & (seed_rel[G.src] > 0)
    tg, bv, bw, sv = _group_best(G.dst[m1], seed_rel[G.src[m1]] * t[m1], G.src[m1])
    a1 = np.zeros(n)
    a1src = np.full(n, -1, dtype=np.int64)
    a1sec = np.zeros(n)
    a1[tg], a1src[tg], a1sec[tg] = bv, bw, sv
    m2 = ok & (a1[G.src] > 0)
    s2, d2 = G.src[m2], G.dst[m2]
    base = np.where(a1src[s2] == d2, a1sec[s2], a1[s2])
    v2 = base * t[m2]
    keep = v2 > 0
    tg2, bv2, bw2, _ = _group_best(d2[keep], v2[keep], s2[keep])
    a2 = np.zeros(n)
    a2via = np.full(n, -1, dtype=np.int64)
    a2[tg2], a2via[tg2] = bv2, bw2
    use1 = (a1 >= a2) & (a1 > 0)
    use2 = ~use1 & (a2 > 0)
    A = np.where(use1, a1, np.where(use2, a2, 0.0))
    vi = np.where(use1, a1src, np.where(use2, a2via, -1))
    hop = np.where(use1, 1, np.where(use2, 2, 0))
    low = A < A_MIN
    A[low], vi[low], hop[low] = 0.0, -1, 0
    A[~valid] = 0.0
    vi[~valid], hop[~valid] = -1, 0
    via = np.where(vi >= 0, G.ids[np.maximum(vi, 0)], -1)
    return A, via, hop


def cap(elems, lengths):
    """Obergrenze (Praefix): elems [(gesamt, r*, id)] sortiert -> (behaltene ids, gekappt, zeichen)."""
    kept, total, cut = [], 0, 0
    for _g, _r, i in elems:
        ln = lengths[i]
        if kept and (cut or total + ln > CAP):
            cut += 1
            continue
        kept.append(i)
        total += ln
    return kept, cut, total


def classify(kept, basis, hit_plain):
    """-> (neu, neu_limit, verdraengt). hit_plain[id] = ohne A ein Treffer."""
    bs = set(basis)
    neu = [i for i in kept if i not in bs and not hit_plain.get(i)]
    lim = [i for i in kept if i not in bs and hit_plain.get(i)]
    ks = set(kept)
    return neu, lim, [i for i in basis if i not in ks]


def gemeinsam_pairs(aktuell, spuren, vorhanden):
    """Paare der aktuellen Basis-Treffer ohne Link, die in >= GEM_N Spuren (spuren: Listen von IDs, aktuelle mit) stehen."""
    ids = sorted(set(aktuell))
    have = {tuple(sorted(p)) for p in vorhanden}
    sets = [set(s) for s in spuren]
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if (a, b) not in have and sum(1 for s in sets if a in s and b in s) >= GEM_N:
                out.append((a, b))
    return out


# --- Frage: HMAC + Stichworte (nie der Text) -----------------------------------------------------------------------
def _key():
    from saga.core import SECRET
    return hashlib.sha256(b"saga-memory-trace/v1\0" + SECRET.encode()).digest()


def frage_hash(uid, text):
    norm = " ".join(unicodedata.normalize("NFKC", str(text)).casefold().split())
    return hmac.new(_key(), (str(uid) + "\n" + norm).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def stichworte(text):
    from saga.memory import keywords
    return ", ".join(keywords(text)[:8])[:160]


# --- Link-Zwischenspeicher ---------------------------------------------------------------------------------------------
_lcache = OrderedDict()
_llock = threading.Lock()


def _lstamp(c, uid):
    r = c.execute("SELECT count(*), COALESCE(max(zuletzt), 0), COALESCE(max(angelegt), 0) FROM mem_link WHERE uid=?", (uid,)).fetchone()
    return (int(r[0]), float(r[1]), float(r[2]))


LINK_CHECK_S = 5.0               # Stempel der Links hoechstens so oft pruefen (eigene Schreibzugriffe verwerfen sofort)
_lchecked = {}


def graph_of(c, uid, fresh=False):
    with _llock:
        G = _lcache.get(uid)
        if G is not None and not fresh and time.time() - _lchecked.get(uid, 0) < LINK_CHECK_S:
            _lcache.move_to_end(uid)
            return G
    st = _lstamp(c, uid)
    with _llock:
        G = _lcache.get(uid)
        if G is not None and G.stamp == st:
            _lcache.move_to_end(uid)
            _lchecked[uid] = time.time()
            return G
    rows = c.execute("SELECT a, b, gewicht, untergrenze, zuletzt, gepinnt, herkunft, treffer FROM mem_link WHERE uid=? ORDER BY a, b",
                     (uid,)).fetchall()
    G = Graph(rows, st)
    from saga import memory
    with _llock:
        _lchecked[uid] = time.time()
        _lcache[uid] = G
        _lcache.move_to_end(uid)
        total = sum(x.nbytes() for x in _lcache.values()) + memory.cache_total()[0]
        while total > memory.CACHE_MAX and len(_lcache) > 1:
            _u, x = _lcache.popitem(last=False)
            total -= x.nbytes()
    return G


def forget(uid=None):
    with _llock:
        if uid is None:
            _lcache.clear()
            _lchecked.clear()
        else:
            _lcache.pop(uid, None)
            _lchecked.pop(uid, None)


def _apply(uid, rows, n_new, now):
    """Eigene Schreibzugriffe in den Zwischenspeicher uebernehmen (Stempel mitfuehren) statt alle Links neu zu laden."""
    with _llock:
        G = _lcache.get(uid)
        if G is None or G.stamp is None:
            return
        st = (G.stamp[0] + n_new, max(G.stamp[1], now), max(G.stamp[2], now) if n_new else G.stamp[2])
        try:
            _lcache[uid] = G.with_links(rows, st)
        except Exception:  # noqa: BLE001 - im Zweifel neu laden
            _lcache.pop(uid, None)


def cache_bytes():
    with _llock:
        return sum(x.nbytes() for x in _lcache.values())


# --- Auswertung je recall (aus saga.memory.recall Schritt 3, nach dem Commit der Zaehler) -------------------------------
def evaluate(c, uid, ctx):
    """ctx (siehe saga.memory.recall) -> {"neu": [ids], "ms": float} oder None bei Fehler (gefangen, Log nur der Typ)."""
    t0 = time.perf_counter()
    try:
        out = _evaluate(c, uid, ctx)
    except Exception as ex:  # noqa: BLE001 - der Schattenteil darf recall nie stoeren
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"gedaechtnis: Schatten {type(ex).__name__}", flush=True)
        return None
    out["ms"] = (time.perf_counter() - t0) * 1000
    return out


def _evaluate(c, uid, x):
    from saga import memory
    w, quelle, now = x["w"], x["quelle"], x["now"]
    gate = w["rel_min_trgm"] if quelle == "trigramm" else w["rel_min"]
    snaps, sens = x["snaps"], x["sens"]
    bad = snaps | sens
    cand, scored = x["cand"], x["scored"]
    G = graph_of(c, uid)
    nodes = G.ids
    # lebende Zeilen (Index der Suche) und exakte rel je Knoten des Graphen (vektorisiert)
    relg = np.zeros(len(nodes))
    if quelle == "embeddings":
        v, rel = x["v"], x["rel"]
        live = v.ids
        if len(nodes) and len(v.ids):
            i = np.minimum(np.searchsorted(v.ids, nodes), len(v.ids) - 1)
            hitm = v.ids[i] == nodes
            relg[hitm] = np.maximum(0.0, rel[i[hitm]])
        n_live = len(v.ids) - (int(np.isin(v.ids, np.fromiter(snaps, dtype=np.int64, count=len(snaps))).sum()) if snaps else 0)
    else:
        wi, rr = x["x"], x["rr"]
        live = np.asarray(sorted(wi.ids), dtype=np.int64)
        if rr and len(nodes):
            ri = list(rr)
            k = G.idx([wi.ids[i] for i in ri])
            for kk, i in zip(k, ri):
                if kk >= 0:
                    relg[kk] = max(0.0, float(rr[i]))
        n_live = len(wi.ids)
    n_rows = max(0, x["n_rows"] - len(snaps)) if x.get("n_rows") is not None else n_live
    A = via = hop = None
    akt = {}
    reach = set()
    if len(nodes):
        alive = np.isin(nodes, live)
        badarr = np.isin(nodes, np.fromiter(bad, dtype=np.int64, count=len(bad))) if bad else np.zeros(len(nodes), dtype=bool)
        valid = alive & ~badarr
        weff = w_eff(G.g, G.u, G.zul, G.pin, now)
        h = hub(n_rows, degrees(G, valid))
        t = transfer(G, weff, h)
        seeds = sorted(((r, i) for i, r in cand.items() if i not in sens and r >= gate), key=lambda z: (-z[0], z[1]))[:SEEDS_MAX]
        seed_rel = np.zeros(len(nodes))
        si = G.idx([i for _r, i in seeds])
        for (r, _i), k in zip(seeds, si):
            if k >= 0 and valid[k]:
                seed_rel[k] = r
        A, via, hop = spread(G, t, valid, seed_rel)
        need = np.nonzero((A > 0) & (np.minimum(1.0, relg + A) >= gate))[0]
        ci = G.idx(list(cand))
        need = np.union1d(need, ci[ci >= 0]).astype(np.int64)
        for k in need:
            if A[k] > 0:
                akt[int(nodes[k])] = (float(A[k]), int(via[k]), int(hop[k]))
        reach = {int(nodes[k]) for k in need if A[k] > 0} - set(cand) - set(snaps)
    rels = {i: float(relg[int(G.idx([i])[0])]) for i in reach}
    rows = {}
    if reach:
        for nid, title, props, created, hits, lh in c.execute(
                "SELECT n.id, n.title, n.props, n.created, v.hits, v.last_hit FROM note n LEFT JOIN mem_vec v ON v.note_id = n.id "
                "AND v.uid = n.uid WHERE n.uid=? AND n.parent_id=? AND n.id = ANY(?) AND n.deleted_at IS NULL "
                "AND n.archived IS NULL AND n.is_template=0", (uid, x["db"], sorted(reach))).fetchall():
            f = memory.fields_of(x["cols"], title, memory._json_load(props, {}))
            h_ = hits if hits is not None else int(f["abrufe"] or 0)
            rows[nid] = (f, created, h_, lh)
    for nid, (s, fa, f, created, h_, lh) in scored.items():
        rows[nid] = (f, created, h_, lh)
    elems, plain, lengths, base_rel = [], {}, {}, {}
    proj = x["projekt"]
    for nid, (f, created, h_, lh) in rows.items():
        if nid in snaps:
            continue
        r0 = cand[nid] if nid in cand else rels.get(nid, 0.0)
        a = akt.get(nid, (0.0, -1, 0))[0] if nid not in sens else 0.0
        rs = min(1.0, max(0.0, r0) + a)
        fa = memory.factors(rs, now, created, h_, lh, f["letzter_abruf"], f["datum"], f["wichtigkeit"], proj, f["projekt"], w)
        g_ = memory.score(fa, w)
        base_rel[nid] = r0
        if nid in scored:
            plain[nid] = memory.is_hit(scored[nid][1], scored[nid][0], w, quelle)
        else:
            fp = memory.factors(r0, now, created, h_, lh, f["letzter_abruf"], f["datum"], f["wichtigkeit"], proj, f["projekt"], w)
            plain[nid] = memory.is_hit(fp, memory.score(fp, w), w, quelle)
        lengths[nid] = len(f["titel"]) + len(f["inhalt"][:2000])
        if rs >= gate and g_ >= w["score_min"]:
            elems.append((g_, rs, nid))
    elems.sort(key=lambda z: (-z[0], -z[1], z[2]))
    kept, cut, total = cap(elems, lengths)
    basis = [b[2] for b in x["top"]]
    neu, neu_limit, verdraengt = classify(kept, basis, plain)
    ge = {i: (g_, rs) for g_, rs, i in elems}
    sh = [i for i in kept[:TRACE_SHADOW_MAX]] + [i for i in neu if i not in kept[:TRACE_SHADOW_MAX]]

    def entry(i):
        a = akt.get(i, (0.0, -1, 0))
        return [i, round(base_rel.get(i, 0.0), 4), round(a[0], 4), round(ge[i][0], 4), a[1] if a[1] >= 0 else None, a[2]]
    daten = {"w": {"tor": gate, "score_min": w["score_min"]}, "projekt": bool(proj),
             "basis": [[b[2], round(b[1], 4), round(b[0], 4)] for b in x["top"]], "schatten": [entry(i) for i in sh],
             "neu_limit": neu_limit, "verdraengt": verdraengt, "ms": round((time.perf_counter() - x["t_shadow"]) * 1000, 1)}
    text = x["text"]
    with c:
        c.execute("INSERT INTO mem_trace (uid, at, quelle, frage_hash, stichworte, lim, treffer, neu, genutzt, gekappt, zeichen, daten) "
                  "VALUES (?,?,?,?,?,?,CAST(? AS JSONB),CAST(? AS JSONB),'[]'::jsonb,?,?,CAST(? AS JSONB))",
                  (uid, now, quelle, frage_hash(uid, text), stichworte(text), int(x["lim"]), json.dumps(basis), json.dumps(neu),
                   min(cut, 32767), int(total), json.dumps(daten)))
        made = _gemeinsam(c, uid, [i for i in basis if i not in bad], now)
    if made:
        _apply(uid, [(a, b, G_GEM, U_GEM, now, False, "gemeinsam", 0) for a, b in made], len(made), now)
    return {"neu": neu}


def _gemeinsam(c, uid, basis, now):
    if len(basis) < 2:
        return []
    ids = sorted(set(basis))
    have = c.execute("SELECT a, b FROM mem_link WHERE uid=? AND a = ANY(?) AND b = ANY(?)", (uid, ids, ids)).fetchall()
    have = {(int(a), int(b)) for a, b in have}
    miss = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:] if (a, b) not in have]
    if not miss:
        return []
    cond = " OR ".join(["treffer @> CAST(? AS JSONB)"] * len(miss))
    spuren = [r[0] for r in c.execute(f"SELECT treffer FROM mem_trace WHERE uid=? AND at >= ? AND ({cond})",
                                      (uid, now - GEM_DAYS * 86400, *[json.dumps([a, b]) for a, b in miss])).fetchall()]
    spuren = [s if isinstance(s, list) else json.loads(s) for s in spuren]
    made = []
    for a, b in gemeinsam_pairs(ids, spuren, have):
        if c.execute("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) "
                     "VALUES (?,?,?,?,?,0,?,?,'gemeinsam') ON CONFLICT DO NOTHING", (a, b, uid, G_GEM, U_GEM, now, now)).rowcount:
            made.append((a, b))
    return made


# --- Verknuepfungen "aehnlich" (neuer Vektor, Nachziehen) ---------------------------------------------------------
_bf = {}                         # uid -> Beginn des Nachziehens (running)
_bf_lock = threading.Lock()


def running(uid):
    with _bf_lock:
        return uid in _bf


def on(uid, c=None):
    from saga import memory
    if c is None:
        with _db() as c2:
            return on(uid, c2)
    return memory.shadow_since(c, uid) is not None


def schedule_backfill(uid, delay=1.0):
    """Nachziehen im Hintergrund (saga.memory-Warteschlange); running sofort an (Einstellungen: "Wird aufgebaut ...")."""
    from saga import memory
    with _bf_lock:
        _bf.setdefault(uid, time.time())
    memory.schedule(uid, backfill=True, delay=delay)


def _ctx(c, uid):
    from saga import memory
    b = memory.binding(uid, c)
    st = memory._db_state(c, uid, b["db"]) if b["db"] else None
    if st is None:
        return None
    cols, _ = memory.columns(st[0], b["map"])
    snaps, sens = memory.excluded(c, uid, b["db"], cols, b["map"])
    return b, cols, snaps, sens


def _pairs_vectors(v, src_ids, bad):
    """Beste K Nachbarn (cos >= COS_MIN) fuer src_ids gegen alle gueltigen Vektoren -> {(a, b): g0}."""
    ids = v.ids
    ok = ~np.isin(ids, np.fromiter(bad, dtype=np.int64, count=len(bad))) if bad else np.ones(len(ids), dtype=bool)
    M, mids = v.mat[ok], ids[ok]
    if not len(mids):
        return {}
    pos = {int(i): k for k, i in enumerate(mids)}
    rows = [pos[int(i)] for i in src_ids if int(i) in pos]
    out = {}
    for s in range(0, len(rows), BLOCK):
        blk = rows[s:s + BLOCK]
        S = M[blk] @ M.T
        S[np.arange(len(blk)), blk] = -1.0
        kk = min(K, S.shape[1] - 1) if S.shape[1] > 1 else 0
        if kk <= 0:
            continue
        top = np.argpartition(-S, kk - 1, axis=1)[:, :kk]
        for r, row in enumerate(blk):
            for j in top[r]:
                cs = float(S[r, j])
                g = g0_cos(cs)
                if g is None or j == row:
                    continue
                a, b = sorted((int(mids[row]), int(mids[j])))
                out[(a, b)] = max(out.get((a, b), 0.0), g)
    return out


def _pairs_trgm(c, uid, b, cols, src_ids, bad):
    from saga import memory
    xw = memory.word_index(c, uid, b["db"], cols, memory.snap_option(cols, b["map"]))
    live = memory._live_rows(c, uid, b["db"], cols, sorted(int(i) for i in src_ids))
    out = {}
    for nid, txt, _h, _f in live:
        if nid in bad:
            continue
        best = sorted(((s, xw.ids[i]) for i, s in memory.trgm_rel(xw, txt).items()
                       if xw.ids[i] != nid and xw.ids[i] not in bad and s >= TRGM_MIN), key=lambda z: (-z[0], z[1]))[:K]
        for s, j in best:
            a, b_ = sorted((int(nid), int(j)))
            out[(a, b_)] = max(out.get((a, b_), 0.0), g0_trgm(s))
    return out


def _insert_pairs(uid, pairs, now=None):
    now = now or time.time()
    made = []
    items = sorted(pairs.items())
    for s in range(0, len(items), 500):
        with _db() as c, c:
            for (a, b), g in items[s:s + 500]:
                if c.execute("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) "
                             "VALUES (?,?,?,?,?,0,?,?,'aehnlich') ON CONFLICT DO NOTHING", (a, b, uid, g, U0, now, now)).rowcount:
                    made.append((a, b, g, U0, now, False, "aehnlich", 0))
    if len(made) > 500:
        forget(uid)                              # Nachziehen: beim naechsten recall neu laden
    elif made:
        _apply(uid, made, len(made), now)
    return len(made)


def link_new(uid, ids):
    """Neue/geaenderte Zeilen -> Verknuepfungen "aehnlich" (nur bei eingeschaltetem Modus) -> Anzahl neuer Links."""
    from saga import memory
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    with _db() as c:
        if not on(uid, c):
            return 0
        cx = _ctx(c, uid)
        if cx is None:
            return 0
        b, cols, snaps, sens = cx
        model = memory.model_key()
        bad = snaps | sens
        src = [i for i in ids if i not in bad]
        if not src:
            return 0
        if model:
            v = memory.vectors(c, uid, model)
            pairs = _pairs_vectors(v, src, bad) if len(v.ids) else {}
        else:
            pairs = _pairs_trgm(c, uid, b, cols, src, bad)
    return _insert_pairs(uid, pairs)


def backfill(uid, log=None):
    """Ganzer Bestand: Verknuepfungen "aehnlich" nachziehen (Bloecke a BLOCK) -> {"rows", "links"}. Log nur Zahlen."""
    from saga import memory
    with _bf_lock:
        _bf.setdefault(uid, time.time())
    try:
        with _db() as c:
            cx = _ctx(c, uid)
            if cx is None:
                return {"rows": 0, "links": 0}
            b, cols, snaps, sens = cx
            model = memory.model_key()
            bad = snaps | sens
            if model:
                v = memory.vectors(c, uid, model)
                src = [int(i) for i in v.ids if int(i) not in bad]
                pairs = _pairs_vectors(v, src, bad) if src else {}
            else:
                xw = memory.word_index(c, uid, b["db"], cols, memory.snap_option(cols, b["map"]))
                src = [i for i in xw.ids if i not in bad]
                pairs = {}
                for s in range(0, len(src), BLOCK):
                    pairs.update({k: max(v_, pairs.get(k, 0.0)) for k, v_ in _pairs_trgm(c, uid, b, cols, src[s:s + BLOCK], bad).items()})
        n = _insert_pairs(uid, pairs)
        msg = f"gedaechtnis: Konto {_uh(uid)}: Verknuepfungen nachgezogen - {len(src)} Zeilen, {len(pairs)} Paare, {n} neu"
        log(msg) if log is not None else print(msg, flush=True)
        return {"rows": len(src), "links": n}
    finally:
        with _bf_lock:
            _bf.pop(uid, None)


# --- recall_used (12.4) -------------------------------------------------------------------------------------------
def used(uid, ids, now=None):
    from saga import memory
    now = now or time.time()
    off = {"ok": True, "schatten": False, "verstaerkt": 0, "neu": 0, "ignoriert": []}
    with _db() as c:
        if not on(uid, c):
            return off
        cx = _ctx(c, uid)
        if cx is None:
            raise memory._not_bound()
        b, cols, snaps, sens = cx
        alive = {r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND parent_id=? AND id = ANY(?) AND deleted_at IS NULL "
                                         "AND archived IS NULL AND is_template=0", (uid, b["db"], ids)).fetchall()}
        with c:
            tr = c.execute("SELECT id, treffer, genutzt FROM mem_trace WHERE uid=? AND at >= ? ORDER BY at DESC, id DESC FOR UPDATE",
                           (uid, now - USED_WINDOW)).fetchall()
            tr = [(t_, (h if isinstance(h, list) else json.loads(h or "[]")), (g if isinstance(g, list) else json.loads(g or "[]")))
                  for t_, h, g in tr]
            valid, ign, mark, hit_of = [], [], {}, {}
            for i in ids:
                hit = next((t_ for t_, h, _g in tr if i in h), None) if i in alive else None
                if hit is None:
                    ign.append(i)
                    continue
                valid.append(i)
                hit_of[i] = hit
                mark.setdefault(hit, []).append(i)
            gn = {t_: g for t_, _h, g in tr}
            # je Spur und Paar hoechstens einmal verstaerken: beide ids schon unter genutzt ihrer Spur -> Paar uebersprungen
            prev = {i for i in valid if i in gn[hit_of[i]]}
            for t_, lst in mark.items():
                c.execute("UPDATE mem_trace SET genutzt=CAST(? AS JSONB) WHERE id=?", (json.dumps(list(dict.fromkeys(gn[t_] + lst))), t_))
            pv = sorted(i for i in valid if i not in sens and i not in snaps)
            nv = nn = 0
            done = []
            for k, a in enumerate(pv):
                for b_ in pv[k + 1:]:
                    if a in prev and b_ in prev:
                        continue
                    r = c.execute("SELECT gewicht, untergrenze, treffer, zuletzt, gepinnt FROM mem_link WHERE a=? AND b=? AND uid=? FOR UPDATE",
                                  (a, b_, uid)).fetchone()
                    if r:
                        x = reinforce({"g": r[0], "u": r[1], "treffer": r[2], "zuletzt": r[3], "gepinnt": bool(r[4])}, now)
                        c.execute("UPDATE mem_link SET gewicht=?, untergrenze=?, treffer=?, zuletzt=? WHERE a=? AND b=? AND uid=?",
                                  (x["g"], x["u"], x["treffer"], x["zuletzt"], a, b_, uid))
                        her = c.execute("SELECT herkunft FROM mem_link WHERE a=? AND b=? AND uid=?", (a, b_, uid)).fetchone()[0]
                        done.append((a, b_, x["g"], x["u"], now, bool(r[4]), her, x["treffer"]))
                        nv += 1
                    else:
                        x = reinforce(None, now)
                        k = c.execute("INSERT INTO mem_link (a, b, uid, gewicht, untergrenze, treffer, zuletzt, angelegt, herkunft) "
                                      "VALUES (?,?,?,?,?,?,?,?,'gemeinsam') ON CONFLICT DO NOTHING",
                                      (a, b_, uid, x["g"], x["u"], x["treffer"], now, now)).rowcount
                        if k:
                            done.append((a, b_, x["g"], x["u"], now, False, "gemeinsam", x["treffer"]))
                        nn += k
    if done:
        _apply(uid, done, nn, now)
    return {"ok": True, "schatten": True, "verstaerkt": nv, "neu": nn, "ignoriert": ign}


# --- Aufraeumen (aus saga.memory._loop) ----------------------------------------------------------------------------
def cleanup(now=None, links=False):
    """Spuren aelter als 30 Tage weg; links=True zusaetzlich Links, deren a/b nicht mehr in note existiert."""
    now = now or time.time()
    with _db() as c, c:
        nt = c.execute("DELETE FROM mem_trace WHERE at < ?", (now - TRACE_KEEP,)).rowcount
        nl = 0
        if links:
            nl = c.execute("DELETE FROM mem_link l WHERE NOT EXISTS (SELECT 1 FROM note n WHERE n.id = l.a) "
                           "OR NOT EXISTS (SELECT 1 FROM note n WHERE n.id = l.b)").rowcount
    if nl:
        forget()
    if nt or nl:
        print(f"gedaechtnis: Schatten aufgeraeumt - {nt} Spuren, {nl} Links", flush=True)
    return nt, nl


# --- Graph (docs/MEMORY.md 10.4) -----------------------------------------------------------------------------------
def graph_part(c, uid, nodes, now=None):
    """-> {"links", "links_total", "hits24"} fuer Knoten des Graphen (eine Abfrage je Teil)."""
    now = now or time.time()
    rows = c.execute("SELECT a, b, gewicht, untergrenze, zuletzt, gepinnt, herkunft FROM mem_link WHERE uid=?", (uid,)).fetchall()
    lst = []
    for a, b, g, u, zul, pin, her in rows:
        if a in nodes and b in nodes:
            lst.append((w_eff(float(g), float(u), float(zul), bool(pin), now), a, b, her, pin))
    total = len(lst)
    if total > GRAPH_LINKS_MAX:
        lst = sorted(lst, key=lambda z: (-z[0], z[1], z[2]))[:GRAPH_LINKS_MAX]
    lst.sort(key=lambda z: (z[1], z[2]))
    hits = {}
    for at, neu in c.execute("SELECT at, neu FROM mem_trace WHERE uid=? AND at >= ? AND neu <> '[]'::jsonb", (uid, now - 86400)).fetchall():
        for i in (neu if isinstance(neu, list) else json.loads(neu or "[]")):
            if i in nodes:
                n, last = hits.get(i, (0, 0))
                hits[i] = (n + 1, max(last, at))
    return {"links": [[a, b, round(w, 3), HER.get(her, "a"), 1 if pin else 0] for w, a, b, her, pin in lst], "links_total": total,
            "hits24": [[i, n, int(last)] for i, (n, last) in sorted(hits.items())]}


# --- Auswertung (docs/MEMORY.md 13) --------------------------------------------------------------------------------
def _median(xs):
    xs = sorted(xs)
    if not xs:
        return 0
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def _p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(math.ceil(0.9 * len(xs))) - 1)] if xs else 0


def sample_idx(m, n):
    """Gleichmaessig verteilte Stichproben-Indizes (deterministisch)."""
    n = min(n, m)
    if n <= 0:
        return []
    if n == 1:
        return [0]
    return sorted({int(round(i * (m - 1) / (n - 1))) for i in range(n)})


def report(uid, tage, n, now=None):
    """Auswertung (docs/MEMORY.md 13) - Texte ("(gelöscht)") in der Kontosprache."""
    from saga import memory
    from saga.core import account_lang
    with memory._in_lang(account_lang(uid)):
        return _report(uid, tage, n, now)


def _report(uid, tage, n, now=None):
    from saga import memory
    from saga.core import t
    now = now or time.time()
    with _db() as c:
        since = memory.shadow_since(c, uid)
        cx = _ctx(c, uid)
        tr = c.execute("SELECT id, at, stichworte, treffer, neu, genutzt, gekappt, zeichen, daten FROM mem_trace WHERE uid=? AND at >= ? "
                       "ORDER BY at, id", (uid, now - tage * 86400)).fetchall()
        G = graph_of(c, uid, fresh=True)
        sens = cx[3] if cx else frozenset()
        snaps = cx[2] if cx else frozenset()
        live = set()
        if cx:
            live = {r[0] for r in c.execute(memory._ROWS_SQL.replace("id, title, props, created", "id"), (uid, cx[0]["db"])).fetchall()}

        def J(v):
            return v if isinstance(v, (list, dict)) else json.loads(v or "null")
        tr = [(i, at, sw, J(h) or [], J(nu) or [], J(gn) or [], int(gk or 0), int(z or 0), J(d) or {})
              for i, at, sw, h, nu, gn, gk, z, d in tr]
        nt = len(tr)
        out = {"schatten": since is not None, "seit": memory._iso(since) if since else None, "tage": tage}
        mit = sum(1 for x in tr if x[3])
        gen = sum(1 for x in tr if x[5])
        out["abrufe"] = {"n": nt, "mit_treffern": mit, "genutzt_gemeldet": gen, "quote_genutzt": round(gen / mit, 4) if mit else 0}
        zs = [x[7] for x in tr if x[8].get("schatten")]
        out["schatten_treffer"] = {
            "abrufe_mit_neu": sum(1 for x in tr if x[4]),
            "neu_je_abruf": round(sum(len(x[4]) for x in tr) / nt, 4) if nt else 0,
            "neu_limit_je_abruf": round(sum(len(x[8].get("neu_limit") or []) for x in tr) / nt, 4) if nt else 0,
            "verdraengt": sum(len(x[8].get("verdraengt") or []) for x in tr),
            "gekappt": sum(1 for x in tr if x[6] > 0), "zeichen_median": _median(zs), "zeichen_p90": _p90(zs)}
        # Kanten (ohne die zu geloeschten Zeilen)
        if len(G.g):
            keep = np.isin(G.ids[G.ai], list(live)) & np.isin(G.ids[G.bi], list(live)) if live else np.zeros(len(G.g), dtype=bool)
        else:
            keep = np.zeros(0, dtype=bool)
        idx = np.nonzero(keep)[0]
        we = w_eff(G.g, G.u, G.zul, G.pin, now)[idx] if len(idx) else np.zeros(0)
        her = {"aehnlich": 0, "gemeinsam": 0, "hand": 0}
        for k in idx:
            her[G.her[k]] = her.get(G.her[k], 0) + 1
        fae = [[round(i / 10, 1), round((i + 1) / 10, 1), int(((we >= i / 10) & ((we < (i + 1) / 10) if i < 9 else (we <= 1.0))).sum())]
               for i in range(10)]
        out["kanten"] = {"n": int(len(idx)), "herkunft": her, "gepinnt": int(G.pin[idx].sum()) if len(idx) else 0,
                         "gewicht": {"faecher": fae, "median": round(float(np.median(we)), 4) if len(we) else 0,
                                     "p90": round(float(_p90(list(we))), 4) if len(we) else 0},
                         "verstaerkt": int((G.tr[idx] > 0).sum()) if len(idx) else 0}
        # Hubs: Grad ueber gueltige Knoten
        valid = np.isin(G.ids, list(live - set(snaps) - set(sens))) if len(G.ids) and live else np.zeros(len(G.ids), dtype=bool)
        deg = degrees(G, valid) if len(G.ids) else np.zeros(0)
        nrows = len(live - set(snaps))
        sx = set(sens)                   # sensible Zeilen: nie mit Titel/Vorschau im Bericht (nur in "sensibel" gezaehlt)
        hubs = sorted(((int(deg[k]), int(G.ids[k])) for k in np.nonzero(deg > 0)[0] if int(G.ids[k]) not in sx),
                      key=lambda z: (-z[0], z[1]))[:5]
        # oft feuernde Kanten (via, id) aus neu-Eintraegen
        fire = {}
        for x in tr:
            ent = {e[0]: e for e in x[8].get("schatten") or []}
            for i in x[4]:
                e = ent.get(i)
                if e and e[4] is not None and e[4] not in sx and i not in sx:
                    fire[(e[4], i)] = fire.get((e[4], i), 0) + 1
        top_f = sorted(fire.items(), key=lambda z: (-z[1], z[0]))[:5]
        # Ausreisser
        aus = []
        sets = [[e[0] for e in x[8].get("schatten") or []] for x in tr if x[8].get("schatten")]
        if len(sets) >= 4:
            cnt = {}
            for s in sets:
                for i in set(s):
                    cnt[i] = cnt.get(i, 0) + 1
            for i, k in sorted(cnt.items(), key=lambda z: (-z[1], z[0])):
                if k / len(sets) >= 0.25 and len(aus) < 5 and i not in sx:
                    aus.append({"art": "dauerbrenner", "id": i, "anteil": round(k / len(sets), 4)})
        strong = [(float(we[j]), int(G.ids[G.ai[k]]), int(G.ids[G.bi[k]])) for j, k in enumerate(idx) if we[j] >= 0.9 and not G.pin[k]
                  and int(G.ids[G.ai[k]]) not in sx and int(G.ids[G.bi[k]]) not in sx]
        for wv, a, b in sorted(strong, key=lambda z: (-z[0], z[1], z[2])):
            if len(aus) < 5:
                aus.append({"art": "starke_kante", "a": a, "b": b, "gewicht": round(wv, 4)})
        for x in tr:
            if x[6] > 0 and len(aus) < 5:
                aus.append({"art": "gekappt", "am": memory._iso(x[1]), "gekappt": x[6], "zeichen": x[7]})
        out["sensibel"] = len(sens)
        # Stichproben
        def shown(x, i):                 # Eintrag "waere": weder die Zeile noch ihr Vorgaenger sensibel
            e = next((e for e in x[8].get("schatten") or [] if e[0] == i), None)
            return i not in sx and not (e and e[4] in sx)
        with_neu = [x for x in tr if any(shown(x, i) for i in x[4])]
        samp = [with_neu[i] for i in sample_idx(len(with_neu), n)]
        need = set()
        for _w, i in hubs:
            need.add(i)
        for (a, b), _k in top_f:
            need |= {a, b}
        for a in aus:
            need |= {a.get("id"), a.get("a"), a.get("b")} - {None}
        for x in samp:
            need |= {e[0] for e in x[8].get("basis") or [] if e[0] not in sx} | {i for i in x[4] if shown(x, i)}
            need |= {e[4] for e in x[8].get("schatten") or [] if e[0] in x[4] and e[4] is not None and shown(x, e[0])}
        info = {}
        if need:
            for nid, title, props in c.execute("SELECT id, title, props FROM note WHERE uid=? AND id = ANY(?) AND deleted_at IS NULL",
                                               (uid, sorted(need))).fetchall():
                f = memory.fields_of(cx[1], title, memory._json_load(props, {})) if cx else {"titel": title or "", "inhalt": ""}
                info[nid] = (f["titel"], f["inhalt"])
    gone = t("(gelöscht)")

    def ti(i):
        return info[i][0] if i in info else gone

    def ref(i):
        return {"id": i, "titel": ti(i)} if i is not None else None
    out["hubs"] = [{"id": i, "titel": ti(i), "grad": d, "daempfung": round(float(hub(nrows, d)), 4)} for d, i in hubs]
    lw = {}
    if len(G.g):
        weff_all = w_eff(G.g, G.u, G.zul, G.pin, now)
        for k in range(len(G.g)):
            lw[(int(G.ids[G.ai[k]]), int(G.ids[G.bi[k]]))] = float(weff_all[k])
    out["oft_feuernd"] = [{"a": ref(a), "b": ref(b), "anteil": round(k / nt, 4) if nt else 0,
                           "gewicht": round(lw.get(tuple(sorted((a, b))), 0.0), 4)} for (a, b), k in top_f]
    for a in aus:
        if a["art"] == "dauerbrenner":
            a["titel"] = ti(a["id"])
        elif a["art"] == "starke_kante":
            a["a"], a["b"] = ref(a["a"]), ref(a["b"])
    out["ausreisser"] = aus
    st = []
    for x in samp:
        ent = {e[0]: e for e in x[8].get("schatten") or []}
        waere = []
        for i in x[4]:
            if not shown(x, i):
                continue
            e = ent.get(i) or [i, 0, 0, 0, None, 0]
            waere.append({"id": i, "titel": ti(i), "vorschau": (info[i][1][:120] if i in info else ""), "rel": e[1], "akt": e[2],
                          "gesamt": e[3], "ueber": ref(e[4])})
        st.append({"am": memory._iso(x[1]), "stichworte": x[2],
                   "treffer": [{"id": e[0], "titel": ti(e[0]), "rel": e[1], "score": e[2]} for e in x[8].get("basis") or []
                               if e[0] not in sx],
                   "waere": waere})
    out["stichproben"] = st
    while st and len(json.dumps(out, ensure_ascii=False)) > REPORT_MAX:
        st.pop()
    return out
