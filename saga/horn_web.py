"""Gjallarhorn fuer Clients (Saga 2.5, Paket P5; Konzept v2 §2.6): Knotenwahl beim Beitritt und beim Live-Umschalten.

saga/horn.py fuehrt Register, Token und Steuerkanal. Hier liegt, was die Geraete fuer die Kandidatenwahl und den
Wechsel zwischen Knoten brauchen (Aufruf aus saga/calls.py):

- home(): Knoten, auf dem der Anruf laeuft - unter den BEREITEN Knoten der mit den meisten aktiven Endpunkten. Faellt
  der bisherige Knoten aus (getrennt, Leeren, voll), ist das der Knoten, auf den die ersten Geraete gewechselt sind;
  ohne solchen gibt es keinen (dann der beste bereite).
- candidates(): bis zu horn.CANDIDATES Knoten mit eigenen Medien-Token, der Knoten des Anrufs zuerst ("here", Bonus bei
  der Wahl im Geraet). Mit Kaskade (horn.CASCADE, P9) auch andere Knoten - das Geraet nimmt sie nur, wenn sie deutlich
  besser sind; dann verbindet saga/horn_bridge.py die Knoten. Ohne Kaskade nur der Knoten des Anrufs. avoid = Knoten, die das Geraet gerade nicht erreicht oder die
  es verlassen will (Leeren, Abbruch) - fuer dieses Geraet sofort, ohne auf die naechste Lastmeldung zu warten.
- may_use(): darf das Geraet seinen Endpunkt auf diesen Knoten legen (POST /api/calls/{id}/horn mit node)?
- peer_refs(): Kennung eines Geraets je in Frage kommendem Knoten (Teilnehmerliste "hpn"). Waehrend eines Wechsels
  sind Geraete kurz auf zwei Knoten; jeder Knoten meldet seine eigenen Kennungen (CSRC -> peer_ref), das Geraet ordnet
  sie damit dem Geraet in der Liste zu.
"""
import time

from saga import horn


def _counts(c, cid):
    return {r[0]: r[1] for r in c.execute("SELECT node, COUNT(*) FROM chat_call_leg WHERE call_id=? AND state='active' "
                                          "AND node IS NOT NULL GROUP BY node", (cid,)).fetchall()}


def home(c, cid, ready=None, avoid=()):
    """Knoten-ID, auf dem der Anruf laeuft (bereit, nicht in avoid), oder None."""
    ready = horn.ready_nodes(c) if ready is None else ready
    ok = {n["id"]: n for n in ready if n["name"] not in avoid}
    cnt = [(k, v) for k, v in _counts(c, cid).items() if k in ok]
    if not cnt:
        return None
    cnt.sort(key=lambda kv: (-kv[1], kv[0]))
    return cnt[0][0]


def _clean_avoid(v):
    if not isinstance(v, list):
        return ()
    return tuple(str(x)[:253].strip().lower() for x in v[:horn.CANDIDATES + 2] if isinstance(x, str) and x.strip())


def candidates(c, call, dev, role="member", dev_class="desktop", avoid=(), now=None, only=None, quota=None):
    """Kandidaten fuer dieses Geraet: [{node, url, udp, here, load, peer, tokens, exp}], beste zuerst. P10: only = nur
    diese Knoten (geteilte), quota = Kontingent des verbundenen Servers (im Token)."""
    now = now or time.time()
    ns = [n for n in horn.ready_nodes(c, now) if n["name"] not in avoid and (only is None or n["id"] in only)]
    h = home(c, call["id"], ns)
    if h:
        ns = [n for n in ns if n["id"] == h] if not horn.CASCADE else sorted(ns, key=lambda n: n["id"] != h)
    out = []
    for n in ns[:horn.CANDIDATES]:
        udp = bool(n["udp"])
        toks = {}
        for r in ("audio", "video") + (("both",) if udp else ()):
            pub = [x for x in horn.PUB[r] if not (role == "companion" and x == "a")]
            toks[r] = horn.token(n, call["id"], dev, r, udp, dev_class, pub, now=now, quota=quota)
        out.append({"node": n["name"], "url": n["url"], "udp": udp, "here": n["id"] == h, "load": round(horn.util(n), 3),
                    "peer": horn.peer_ref(call["id"], n["id"], dev), "tokens": toks, "exp": int(now) + horn.TOKEN_TTL})
    return out


def pick(c, cid):
    """Knoten fuer einen neuen Endpunkt (Beitritt): der des Anrufs, sonst der beste bereite, sonst None."""
    ns = horn.ready_nodes(c)
    h = home(c, cid, ns)
    if h:
        return next(n for n in ns if n["id"] == h)
    return ns[0] if ns else None


def may_use(c, cid, n, avoid=()):
    """Darf ein Endpunkt auf Knoten n wechseln? Bereit, nicht gemieden und - ohne Kaskade - der Knoten des Anrufs bzw.
    (laeuft dort nichts mehr) irgendein bereiter."""
    if not n or not horn.ready(n) or n["name"] in avoid:
        return False
    if horn.CASCADE:
        return True
    h = home(c, cid, avoid=avoid)
    return h is None or h == n["id"]


def nodes_for(c, cid):
    """(id, name) aller Knoten, auf denen Endpunkte dieses Anrufs sein koennen: bereite und die der Endpunkte."""
    ids = {n["id"]: n["name"] for n in horn.ready_nodes(c)}
    used = [k for k in _counts(c, cid) if k not in ids]
    for k in used:
        n = horn.node_by_id(c, k)
        if n:
            ids[k] = n["name"]
    return sorted(ids.items())


def peer_refs(cid, dev, nodes):
    """{knotenname: peer_ref} eines Geraets (nodes aus nodes_for)."""
    return {name: horn.peer_ref(cid, nid, dev) for nid, name in nodes}
