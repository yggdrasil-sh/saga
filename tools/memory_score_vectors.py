"""Erzeugt tests/vectors/memory_score.json (Bugs-DB 2581, docs/MEMORY.md 4) - Referenz direkt aus der Formel, unabhaengig
von saga/memory.py. Aufruf: python3 tools/memory_score_vectors.py > tests/vectors/memory_score.json"""
import json, math
from datetime import datetime
W = {"rel": 0.55, "fresh": 0.15, "freq": 0.10, "imp": 0.20, "proj": 0.10, "rel_min": 0.45, "rel_min_trgm": 0.25,
     "score_min": 0.375, "half_life_days": 30}
NOW = 1795000000.0          # 2026-11-17T...Z
def ts(s):
    if s is None: return None
    s = s.split("/")[-1]
    if len(s) == 10:
        return datetime.fromisoformat(s + "T00:00:00+00:00").timestamp()
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
def calc(c, w):
    ref = [x for x in (c.get("last_hit"), ts(c.get("letzter_abruf")), ts(c.get("datum"))) if x is not None]
    ref = max(ref) if ref else c["created"]
    dt = max(0.0, c["now"] - ref)
    fresh = 0.5 ** (dt / (w["half_life_days"] * 86400))
    freq = min(1.0, math.log(1 + c.get("hits", 0)) / math.log(21))
    imp = 0.5 if c.get("wichtigkeit") is None else max(0.0, min(1.0, c["wichtigkeit"] / 10))
    pq, pr = c.get("projekt_q"), c.get("projekt_row")
    proj = 1.0 if pq and pr and pq.strip().casefold() == pr.strip().casefold() else 0.0
    rel = max(0.0, c["rel"])
    score = w["rel"]*rel + w["fresh"]*fresh + w["freq"]*freq + w["imp"]*imp + w["proj"]*proj
    rmin = w["rel_min_trgm"] if c.get("quelle") == "trigramm" else w["rel_min"]
    hit = rel >= rmin and score >= w["score_min"]
    return {"rel": round(rel, 6), "fresh": round(fresh, 6), "freq": round(freq, 6), "imp": round(imp, 6), "proj": proj,
            "score": round(score, 6), "hit": hit}, score
D = 86400
base = {"now": NOW, "created": NOW - 400 * D, "hits": 0}
cases = [
 ("neu, sehr relevant, wichtig", dict(rel=0.9, last_hit=None, datum="2026-11-17", wichtigkeit=8)),
 ("alles fehlt: Anlage vor 400 Tagen, imp neutral", dict(rel=0.7)),
 ("Halbwertszeit genau 30 Tage ueber last_hit", dict(rel=0.6, last_hit=NOW - 30 * D, wichtigkeit=5)),
 ("Letzter Abruf (Spalte) juenger als last_hit zaehlt", dict(rel=0.6, last_hit=NOW - 60 * D, letzter_abruf="2026-11-16T10:00+01:00")),
 ("Datum als Zeitraum: Ende zaehlt", dict(rel=0.6, datum="2026-10-01/2026-11-10")),
 ("Datum in der Zukunft: fresh 1", dict(rel=0.5, datum="2027-01-01", wichtigkeit=6)),
 ("20 Abrufe = freq voll", dict(rel=0.5, hits=20, last_hit=NOW - 2 * D, wichtigkeit=4)),
 ("100 Abrufe begrenzt auf 1", dict(rel=0.5, hits=100, last_hit=NOW - 2 * D)),
 ("3 Abrufe", dict(rel=0.55, hits=3, last_hit=NOW - 10 * D, wichtigkeit=3)),
 ("Projekt-Bonus (Gross/Klein, Leerraum egal)", dict(rel=0.5, projekt_q=" saga ", projekt_row="Saga", wichtigkeit=3, datum="2026-11-01")),
 ("Projekt verschieden: kein Bonus", dict(rel=0.5, projekt_q="Homelab", projekt_row="Saga", wichtigkeit=3, datum="2026-11-01")),
 ("Relevanz-Tor: wichtig+frisch, aber rel 0,44", dict(rel=0.44, wichtigkeit=10, last_hit=NOW - 60, hits=20, datum="2026-11-17")),
 ("Relevanz-Tor genau 0,45: Treffer", dict(rel=0.45, wichtigkeit=10, last_hit=NOW - 60, datum="2026-11-17")),
 ("Score-Tor: rel 0,5, alt, unwichtig", dict(rel=0.5, wichtigkeit=0, datum="2025-01-01")),
 ("Score-Tor knapp darunter (0,375)", dict(rel=0.5, wichtigkeit=3, datum="2026-09-15")),
 ("Score-Tor knapp darueber (0,375)", dict(rel=0.5, wichtigkeit=3, datum="2026-10-05")),
 ("negativer Kosinus -> 0", dict(rel=-0.3, wichtigkeit=10)),
 ("Wichtigkeit > 10 begrenzt", dict(rel=0.6, wichtigkeit=14, datum="2026-11-17")),
 ("Trigramm: 0,3 ueber dem Tor 0,25", dict(rel=0.3, quelle="trigramm", wichtigkeit=10, datum="2026-11-17", hits=5)),
 ("Trigramm: 0,24 unter dem Tor", dict(rel=0.24, quelle="trigramm", wichtigkeit=10, datum="2026-11-17")),
]
out = {"note": "Bewertung Gedaechtnis (Bugs-DB 2581, docs/MEMORY.md 4) - Referenz unabhaengig von saga/memory.py; Zahlen auf 6 Stellen",
       "weights": W, "now": NOW, "cases": [], "custom": [], "rank": []}
for name, c in cases:
    cc = dict(base, **c)
    exp, _ = calc(cc, W)
    out["cases"].append({"name": name, "in": cc, "out": exp})
# eigene Gewichte
W2 = dict(W, rel=0.8, imp=0.0, half_life_days=7, score_min=0.4)
for name, c in [("eigene Gewichte: H 7 Tage, imp 0", dict(rel=0.6, last_hit=NOW - 7 * D, wichtigkeit=10))]:
    cc = dict(base, **c); exp, _ = calc(cc, W2)
    out["custom"].append({"name": name, "weights": W2, "in": cc, "out": exp})
# Reihenfolge, Begrenzung, Gleichstand
items = [
 dict(id=1, rel=0.80, wichtigkeit=5, datum="2026-11-10"), dict(id=2, rel=0.70, wichtigkeit=9, datum="2026-11-16"),
 dict(id=3, rel=0.95, wichtigkeit=2, datum="2025-06-01"), dict(id=4, rel=0.46, wichtigkeit=10, datum="2026-11-17", hits=20, last_hit=NOW),
 dict(id=5, rel=0.44, wichtigkeit=10, datum="2026-11-17", hits=20, last_hit=NOW), dict(id=7, rel=0.85, wichtigkeit=8, datum="2026-11-15"),
 dict(id=6, rel=0.85, wichtigkeit=8, datum="2026-11-15"), dict(id=8, rel=0.50, wichtigkeit=1, datum="2024-01-01"),
 dict(id=9, rel=0.65, wichtigkeit=6, datum="2026-11-12", projekt_row="Saga"),
]
for lim, pq in ((5, None), (3, None), (5, "Saga"), (1, None)):
    sc = []
    for it in items:
        cc = dict(base, **{k: v for k, v in it.items() if k != "id"}, projekt_q=pq)
        e, s = calc(cc, W)
        if e["hit"]: sc.append((-s, -cc["rel"], it["id"]))
    order = [x[2] for x in sorted(sc)][:lim]
    out["rank"].append({"limit": lim, "projekt": pq, "items": [dict(base, **it) for it in items], "order": order})
print(json.dumps(out, ensure_ascii=False, indent=1))
