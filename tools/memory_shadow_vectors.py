"""Erzeugt tests/vectors/memory_shadow.json (Bugs-DB 2603, docs/MEMORY.md 12) - Referenz direkt aus den Formeln des
Vertrags, unabhaengig von saga/ (wie tools/memory_score_vectors.py).
Aufruf: python3 tools/memory_shadow_vectors.py > tests/vectors/memory_shadow.json

Inhalt: Startgewicht "aehnlich" (Kosinus/Trigramme), w_eff (lazy, gepinnt), Verstaerkung ueber recall_used (3 Schritte mit
Pausen + neues Paar), Hub-Daempfung, Ausbreitung auf einem festen 9-Knoten-Graph (sensibler Knoten, Schnappschuss, Hub):
A1/A2, via/hop, r*, gesamt, Schatten-Menge samt Reihenfolge, Obergrenze, neu/neu_limit/verdraengt; dazu Gleichstand
(kleinere via-ID) und Rueckweg (kein Echo s -> j -> s); "gemeinsam" mit 2 bzw. 3 Spuren."""
import json
import math
import sys
from datetime import datetime

W = {"rel": 0.55, "fresh": 0.15, "freq": 0.10, "imp": 0.20, "proj": 0.10, "rel_min": 0.45, "rel_min_trgm": 0.25,
     "score_min": 0.375, "half_life_days": 30}
D = 86400
NOW = 1795000000.0
K = {"k": 5, "cos_min": 0.55, "trgm_min": 0.50, "u0": 0.05, "hl_tage": 60, "delta": 0.5, "a_min": 0.02, "cap": 2000,
     "seeds_max": 50, "gemeinsam_g": 0.15, "gemeinsam_u": 0.05, "gemeinsam_spuren": 3, "gemeinsam_tage": 30,
     "genutzt_g": 0.30, "genutzt_u": 0.10, "staerken": 0.2, "u_schritt": 0.05, "u_max": 0.5}
R = 6


def r6(x):
    return round(x, R)


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# --- Bewertung wie docs/MEMORY.md 4 (Kopie aus tools/memory_score_vectors.py) ---------------------------------------
def ts(s):
    if s is None:
        return None
    s = s.split("/")[-1]
    if len(s) == 10:
        return datetime.fromisoformat(s + "T00:00:00+00:00").timestamp()
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def score(rel, c, w=W):
    ref = [x for x in (c.get("last_hit"), ts(c.get("letzter_abruf")), ts(c.get("datum"))) if x is not None]
    ref = max(ref) if ref else c["created"]
    dt = max(0.0, NOW - ref)
    fresh = 0.5 ** (dt / (w["half_life_days"] * D))
    freq = min(1.0, math.log(1 + c.get("hits", 0)) / math.log(21))
    imp = 0.5 if c.get("wichtigkeit") is None else clamp(c["wichtigkeit"] / 10)
    rel = max(0.0, rel)
    return w["rel"] * rel + w["fresh"] * fresh + w["freq"] * freq + w["imp"] * imp


def hit(rel, s, gate, w=W):
    return rel >= gate and s >= w["score_min"]


# --- Formeln 12.2-12.5 ---------------------------------------------------------------------------------------------
def g0_cos(cos):
    return None if cos < K["cos_min"] else 0.2 + 0.4 * clamp((cos - K["cos_min"]) / 0.45)


def g0_trgm(rel):
    return None if rel < K["trgm_min"] else 0.2 + 0.4 * clamp((rel - K["trgm_min"]) / 0.50)


def w_eff(g, u, zuletzt, gepinnt, t):
    if gepinnt:
        return g
    return u + (g - u) * 0.5 ** (max(0.0, t - zuletzt) / (K["hl_tage"] * D))


def reinforce(link, t):
    """link {g, u, treffer, zuletzt, gepinnt} oder None -> neuer Stand."""
    if link is None:
        return {"g": K["genutzt_g"], "u": K["genutzt_u"], "treffer": 1, "zuletzt": t}
    w = w_eff(link["g"], link["u"], link["zuletzt"], link.get("gepinnt", False), t)
    g = w + K["staerken"] * (1 - w)
    u = min(K["u_max"], link["u"] + K["u_schritt"], g)
    return {"g": g, "u": u, "treffer": link["treffer"] + 1, "zuletzt": t}


def hub(n, deg):
    if n <= 1:
        return 1.0
    return math.log(1 + n / (1 + deg)) / math.log(1 + n)


def spread(sc):
    """Szenario -> Ergebnis (alles, was die Pruefung vergleicht)."""
    nodes = {x["id"]: x for x in sc["knoten"]}
    gate = W["rel_min_trgm"] if sc["quelle"] == "trigramm" else W["rel_min"]
    bad = {i for i, x in nodes.items() if x.get("sensibel") or x.get("schnappschuss")}
    snap = {i for i, x in nodes.items() if x.get("schnappschuss")}
    sens = {i for i, x in nodes.items() if x.get("sensibel")}
    weff = {}
    for a, b, g, u, zul, pin in sc["links"]:
        weff[(a, b)] = w_eff(g, u, zul, pin, sc["now"])
    deg = {i: 0 for i in nodes}
    for (a, b) in weff:
        if a in nodes and b in nodes and a not in bad and b not in bad:
            deg[a] += 1
            deg[b] += 1
    h = {i: hub(sc["N"], deg[i]) for i in nodes}
    adj = {}
    for (a, b), w in weff.items():
        if a in bad or b in bad:
            continue                         # sensible/Schnappschuesse: weder Ziel noch Durchgang
        t_ = w * K["delta"] * math.sqrt(h[a] * h[b])
        adj.setdefault(a, []).append((b, t_))
        adj.setdefault(b, []).append((a, t_))
    seeds = sorted((i for i, x in nodes.items() if x["rel"] >= gate and i not in bad), key=lambda i: (-nodes[i]["rel"], i))
    seeds = seeds[:K["seeds_max"]]
    # A1: je Ziel alle Quellen (fuer den Ausschluss s != k bei A2)
    a1src = {}
    for s in seeds:
        for j, t_ in adj.get(s, []):
            if j != s:
                a1src.setdefault(j, []).append((nodes[s]["rel"] * t_, s))
    a1 = {}
    for j, lst in a1src.items():
        best = max(lst, key=lambda x: (x[0], -x[1]))
        a1[j] = best
    a2 = {}
    for j, lst in a1src.items():
        for k, t_ in adj.get(j, []):
            if k == j:
                continue
            cand = [x for x in lst if x[1] != k]          # einfacher Pfad s -> j -> k, s != k
            if not cand:
                continue
            v = max(cand, key=lambda x: (x[0], -x[1]))[0] * t_
            cur = a2.get(k)
            if cur is None or v > cur[0] or (v == cur[0] and j < cur[1]):
                a2[k] = (v, j)
    akt = {}
    for i in nodes:
        if i in bad:
            akt[i] = (0.0, None, 0)
            continue
        c1, c2 = a1.get(i), a2.get(i)
        best = (0.0, None, 0)
        if c1:
            best = (c1[0], c1[1], 1)
        if c2 and c2[0] > best[0]:
            best = (c2[0], c2[1], 2)
        if best[0] < K["a_min"]:
            best = (0.0, None, 0)
        akt[i] = best
    # Basis (recall wie bisher): Kandidaten = rel >= Tor ohne Schnappschuesse, Treffer, Rang, Limit
    base_items = []
    for i, x in nodes.items():
        if i in snap or x["rel"] < gate:
            continue
        s = score(x["rel"], x)
        if hit(x["rel"], s, gate):
            base_items.append((s, x["rel"], i))
    basis = [i for _s, _r, i in sorted(base_items, key=lambda z: (-z[0], -z[1], z[2]))[:sc["lim"]]]
    # Schatten-Menge: Kandidaten + erreichte Zeilen (A > 0)
    elems = []
    for i, x in nodes.items():
        if i in snap:
            continue
        a = akt[i][0]
        if x["rel"] < gate and a <= 0:
            continue
        rs = min(1.0, max(0.0, x["rel"]) + a)
        g_ = score(rs, x)
        if rs >= gate and g_ >= W["score_min"]:
            elems.append((g_, rs, i))
    elems.sort(key=lambda z: (-z[0], -z[1], z[2]))
    kept, total, cut = [], 0, 0
    for g_, rs, i in elems:
        ln = nodes[i]["titel_len"] + min(nodes[i]["inhalt_len"], 2000)
        if kept and (cut or total + ln > K["cap"]):
            cut += 1
            continue
        kept.append(i)
        total += ln
    neu, neu_limit = [], []
    for i in kept:
        if i in basis:
            continue
        x = nodes[i]
        (neu_limit if hit(x["rel"], score(x["rel"], x), gate) else neu).append(i)
    return {"w_eff": [[a, b, r6(w)] for (a, b), w in sorted(weff.items())],
            "grad": {str(i): deg[i] for i in sorted(nodes)}, "h": {str(i): r6(h[i]) for i in sorted(nodes)},
            "seeds": seeds,
            "akt": {str(i): [r6(akt[i][0]), akt[i][1], akt[i][2]] for i in sorted(nodes)},
            "basis": basis, "elemente": [[i, r6(rs), r6(g_)] for g_, rs, i in elems],
            "schatten": kept, "gekappt": cut, "zeichen": total, "neu": neu, "neu_limit": neu_limit,
            "verdraengt": [i for i in basis if i not in kept], "sensibel": sorted(sens), "schnappschuss": sorted(snap)}


def node(i, rel, tl, il, wi=5, datum=None, hits=0, last_hit=None, sens=False, snap=False, created=None):
    return {"id": i, "rel": rel, "titel_len": tl, "inhalt_len": il, "wichtigkeit": wi, "datum": datum, "hits": hits,
            "last_hit": last_hit, "created": created if created is not None else NOW - 200 * D, "sensibel": sens,
            "schnappschuss": snap}


TODAY = "2026-11-17"
G9 = {"name": "9 Knoten: Seeds 1-3, Ziel 4 (hop 1), 5 (hop 2 ueber 4), sensibel 6, 7 nur ueber 6/Hub, Schnappschuss 8, Hub 9",
      "now": NOW, "quelle": "embeddings", "lim": 2, "N": 8,
      "knoten": [node(1, 0.80, 40, 700, 8, TODAY),
                 node(2, 0.62, 30, 900, 6, TODAY),
                 node(3, 0.58, 20, 300, 5, TODAY),
                 node(4, 0.30, 25, 400, 10, TODAY, hits=20, last_hit=NOW - 3600),
                 node(5, 0.43, 15, 200, 6, TODAY),
                 node(6, 0.50, 10, 100, 2, "2025-01-01", sens=True),
                 node(7, 0.40, 10, 100, 5, TODAY),
                 node(8, 0.90, 30, 300, 9, TODAY, snap=True),
                 node(9, 0.20, 10, 2400, 5, TODAY)],
      "links": [[1, 3, 0.5, 0.05, NOW - 5 * D, False],
                [1, 4, 0.9, 0.05, NOW - 1 * D, False],
                [1, 6, 0.9, 0.05, NOW, False],
                [1, 8, 0.9, 0.05, NOW, False],
                [1, 9, 0.5, 0.05, NOW - 60 * D, False],
                [2, 9, 0.4, 0.05, NOW - 30 * D, False],
                [3, 9, 0.4, 0.05, NOW - 120 * D, False],
                [4, 5, 0.6, 0.05, NOW - 400 * D, True],
                [4, 9, 0.3, 0.05, NOW, False],
                [5, 9, 0.3, 0.05, NOW, False],
                [6, 7, 0.9, 0.05, NOW, False],
                [7, 9, 0.6, 0.05, NOW, False]]}
TIE = {"name": "Gleichstand: zwei Seeds (gleiche rel, gleiche Kanten) -> kleinere via-ID",
       "now": NOW, "quelle": "embeddings", "lim": 5, "N": 4,
       "knoten": [node(11, 0.70, 10, 100), node(12, 0.70, 10, 100), node(13, 0.35, 10, 100, 9), node(14, 0.10, 10, 100)],
       "links": [[11, 13, 0.6, 0.05, NOW, False], [12, 13, 0.6, 0.05, NOW, False], [11, 14, 0.2, 0.05, NOW, False],
                 [12, 14, 0.2, 0.05, NOW, False]]}
BACK = {"name": "Rueckweg: Seed 21 nur mit 22 verbunden - kein Echo 21 -> 22 -> 21; 23 ueber 22 (hop 2)",
        "now": NOW, "quelle": "trigramm", "lim": 5, "N": 3,
        "knoten": [node(21, 0.40, 10, 100), node(22, 0.10, 10, 100), node(23, 0.12, 10, 100)],
        "links": [[21, 22, 0.8, 0.05, NOW, True], [22, 23, 0.8, 0.05, NOW, True]]}


def gemeinsam(now, spuren, aktuell, vorhanden):
    """Paare der aktuellen Basis-Treffer ohne Link, die in >= 3 Spuren der letzten 30 Tage (aktuelle mit) gemeinsam stehen."""
    have = {tuple(sorted(p)) for p in vorhanden}
    recent = [set(s["treffer"]) for s in spuren if s["at"] >= now - K["gemeinsam_tage"] * D] + [set(aktuell)]
    ids = sorted(set(aktuell))
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if (a, b) in have:
                continue
            if sum(1 for s in recent if a in s and b in s) >= K["gemeinsam_spuren"]:
                out.append([a, b])
    return out


def main():
    out = {"note": "Schattenmodus Phase 1 (Bugs-DB 2603, docs/MEMORY.md 12) - Referenz unabhaengig von saga/; Zahlen auf 6 Stellen",
           "weights": W, "konst": K, "now": NOW}
    out["start"] = [{"quelle": q, "rel": x, "g0": None if f(x) is None else r6(f(x))}
                    for q, f, xs in (("embeddings", g0_cos, (0.54, 0.55, 0.775, 1.0, 0.9)), ("trigramm", g0_trgm, (0.49, 0.5, 0.75, 1.0)))
                    for x in xs]
    out["w_eff"] = []
    for name, g, u, dt, pin in (("dt 0", 0.6, 0.05, 0, False), ("dt H_L", 0.6, 0.05, 60 * D, False),
                                ("dt 2 H_L", 0.6, 0.05, 120 * D, False), ("dt 10 H_L: nahe Untergrenze", 0.6, 0.2, 600 * D, False),
                                ("gepinnt: g", 0.6, 0.05, 365 * D, True), ("Zeit rueckwaerts: wie dt 0", 0.5, 0.05, -5 * D, False)):
        out["w_eff"].append({"name": name, "in": {"g": g, "u": u, "zuletzt": NOW - dt, "gepinnt": pin, "t": NOW},
                             "out": r6(w_eff(g, u, NOW - dt, pin, NOW))})
    st = {"g": 0.4, "u": 0.05, "treffer": 0, "zuletzt": NOW - 10 * D, "gepinnt": False}
    steps, cur = [], dict(st)
    for t in (NOW, NOW + 30 * D, NOW + 150 * D):
        nxt = reinforce(cur, t)
        steps.append({"t": t, "out": {k: (r6(v) if isinstance(v, float) and k != "zuletzt" else v) for k, v in nxt.items()}})
        cur = dict(nxt, gepinnt=False)
    out["verstaerken"] = {"start": st, "schritte": steps,
                          "neu": {"t": NOW, "out": {k: (r6(v) if isinstance(v, float) and k != "zuletzt" else v)
                                                    for k, v in reinforce(None, NOW).items()}}}
    out["hub"] = [{"N": n, "deg": d, "h": r6(hub(n, d))} for n, d in ((40, 0), (40, 2), (40, 39), (1, 0), (0, 0), (8, 6))]
    out["ausbreitung"] = []
    for sc in (G9, TIE, BACK):
        out["ausbreitung"].append(dict(sc, out=spread(sc)))
    out["gemeinsam"] = []
    for name, spuren, akt, have in (
            ("2 Spuren (eine alt + aktuelle): kein Link", [{"at": NOW - D, "treffer": [1, 2, 7]}], [1, 2, 3], []),
            ("3 Spuren: Link 1-2, 1-3 nur zweimal", [{"at": NOW - D, "treffer": [2, 1]}, {"at": NOW - 2 * D, "treffer": [1, 2, 3]}],
             [1, 2, 3], []),
            ("aelter als 30 Tage zaehlt nicht", [{"at": NOW - 31 * D, "treffer": [1, 2]}, {"at": NOW - 2 * D, "treffer": [1, 2]}],
             [1, 2], []),
            ("vorhandener Link: nichts Neues", [{"at": NOW - D, "treffer": [1, 2]}, {"at": NOW - 2 * D, "treffer": [1, 2]}],
             [2, 1], [[1, 2]])):
        out["gemeinsam"].append({"name": name, "now": NOW, "spuren": spuren, "aktuell": akt, "vorhanden": have,
                                 "out": gemeinsam(NOW, spuren, akt, have)})
    g9 = out["ausbreitung"][0]["out"]
    # Abdeckung des 9-Knoten-Graphen (sonst Szenario anpassen)
    assert g9["neu"] and g9["neu_limit"] and g9["verdraengt"] and g9["gekappt"], g9
    assert g9["akt"]["6"][0] == 0 and g9["akt"]["8"][0] == 0 and any(v[2] == 2 for v in g9["akt"].values()), g9["akt"]
    assert out["ausbreitung"][1]["out"]["akt"]["13"][1] == 11
    assert out["ausbreitung"][2]["out"]["akt"]["21"][0] == 0 and out["ausbreitung"][2]["out"]["akt"]["23"][2] == 2
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
