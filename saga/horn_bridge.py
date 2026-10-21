"""Gjallarhorn-Kaskade (Saga 2.5, Paket P9; Konzept v2 §2.4, §2.6): Bruecken ("Bifroest") zwischen Knoten eines Anrufs.

Die meisten Anrufe bleiben auf EINEM Knoten (Bonus "Anruf schon hier" bei der Wahl, saga/horn_web.py). Sind Endpunkte
eines Anrufs auf mehreren Knoten, verbindet Saga sie sternfoermig: Mittelpunkt (hub) ist der eigene Knoten mit den meisten
Endpunkten (bleibt, solange dort noch jemand ist), jeder andere Knoten waehlt ihn an (TLS/TCP 443, gjallarhorn/bifrost.go).
Stern statt Masche: keine Schleifen, jede Quelle hoechstens zwei Spruenge.

- Eigene Knoten: Saga signiert beide Kaskaden-Token (dial fuer den waehlenden, accept fuer den Mittelpunkt) und schickt
  dem waehlenden Knoten "cascade" ueber den Steuerkanal. Steht die Bruecke laut Lastmeldung nicht ("up"), alle 8 s neu.
- Knoten anderer Server (P10, foederierte Anrufe, die wir fuehren): Endpunkt-Eintrag node = 'f:<server>:<knotenname>'
  (gemeldet vom Server des Geraets, saga/fed_horn.py, samt Adresse und Knotenschluessel). Saga signiert das accept-Token
  fuer den eigenen Mittelpunkt (mit Kontingent des Servers) und bittet den anderen Server (Auftrag horn_cascade, signiert),
  seinen Knoten waehlen zu lassen - alle 20 s erneut (der andere Server prueft selbst, ob die Bruecke steht).
- Nicht mehr gebraucht (Endpunkte weg, Mittelpunkt gewechselt, Anruf vorbei): uncascade bzw. horn_uncascade.
Der Abgleich laeuft alle 2 s in einem Hintergrund-Thread, sobald ein Knoten verbunden ist; ohne Anrufe auf mehreren
Knoten kostet er eine kleine Abfrage."""
import threading
import time

from db import connect as _db
from saga import horn

EVERY = 2.0                    # Abgleich
RESEND_LOCAL = 8.0             # eigene Bruecke steht nicht: Befehl erneut
RESEND_FED = 20.0              # fremde Bruecke: Auftrag erneut (Token 60 s)
FNODE_TTL = 600                # Angaben zu fremden Knoten (Adresse, Schluessel) so lange

_LOCK = threading.Lock()
_STATE = {"thread": None}
_HUB = {}                      # call_id -> node_id des Mittelpunkts
_BR = {}                       # bridge_id -> {"call", "dial", "hub", "sent", "fed": pid|None, "name"}
FN = {}                        # (call_id, pid, name) -> {"url", "pub", "at"} (P10, saga/fed_horn.py)
FED = {}                       # saga/fed_horn.py: send_cascade(pid, call_id, body), send_uncascade(pid, call_id, bridge),
                               # quota(c, pid) -> {q, legs, kbit}


def start():
    """Hintergrund-Abgleich starten (einmal je Prozess; horn.HOOKS['connected'])."""
    with _LOCK:
        if _STATE["thread"] is not None:
            return
        th = threading.Thread(target=_loop, name="horn-bridge", daemon=True)
        _STATE["thread"] = th
    th.start()


def _loop():
    while True:
        time.sleep(EVERY)
        try:
            reconcile()
        except Exception as ex:  # noqa: BLE001 - naechster Takt
            print(f"horn: Kaskade: {type(ex).__name__}: {ex}", flush=True)


def plan(rows, prev_hub=None):
    """Knotenverteilung eines Anrufs [(node, endpunkte)] -> (hub, [spoke]). Mittelpunkt: der eigene Knoten mit den
    meisten Endpunkten; der bisherige bleibt, solange dort noch jemand ist (kein Pendeln)."""
    local = [(n, k) for n, k in rows if not n.startswith("f:")]
    if not local or len(rows) < 2:
        return None, []
    ids = {n for n, _ in local}
    hub = prev_hub if prev_hub in ids else sorted(local, key=lambda x: (-x[1], x[0]))[0][0]
    return hub, sorted(n for n, _ in rows if n != hub)


def reconcile(now=None):
    """Ein Abgleich: gewuenschte Bruecken aufbauen, ueberzaehlige abbauen."""
    now = now or time.time()
    with _db() as c:
        rows = c.execute("SELECT l.call_id, l.node, COUNT(*) FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id "
                         "WHERE k.topo='horn' AND k.state<>'ended' AND k.lead IS NULL AND l.state='active' AND l.node IS NOT NULL "
                         "GROUP BY l.call_id, l.node").fetchall()
        by = {}
        for cid, node, n in rows:
            by.setdefault(cid, []).append((node, n))
        want = {}
        for cid, lst in by.items():
            hub, spokes = plan(lst, _HUB.get(cid))
            if not hub:
                _HUB.pop(cid, None)
                continue
            _HUB[cid] = hub
            h = horn.node_by_id(c, hub)
            if not h or not h.get("url"):
                continue
            for sp in spokes:
                if sp.startswith("f:"):
                    _, pid, name = sp.split(":", 2)
                    bid = horn.bridge_id(cid, sp, hub)
                    want[bid] = {"call": cid, "dial": sp, "hub": hub, "fed": int(pid), "name": name}
                else:
                    n = horn.node_by_id(c, sp)
                    if n and n.get("url"):
                        want[horn.bridge_id(cid, sp, hub)] = {"call": cid, "dial": sp, "hub": hub, "fed": None, "name": n["name"]}
        for cid in [k for k in _HUB if k not in by]:
            _HUB.pop(cid, None)
        jobs = []
        for bid, w in want.items():
            cur = _BR.get(bid)
            if cur is None:
                cur = _BR[bid] = dict(w, sent=0.0)
            if w["fed"] is None:
                st = ((horn._LIVE.get(w["dial"]) or {}).get("bridges") or {}).get(bid) or {}
                if st.get("st") == "up" or now - cur["sent"] < RESEND_LOCAL:
                    continue
                cmd = _local_cmd(c, bid, w, now)
                if cmd:
                    cur["sent"] = now
                    jobs.append(("local", w["dial"], cmd))
            else:
                if now - cur["sent"] < RESEND_FED:
                    continue
                body = _fed_body(c, bid, w, now)
                if body:
                    cur["sent"] = now
                    jobs.append(("fed", w["fed"], body))
        for bid in [k for k in _BR if k not in want]:
            old = _BR.pop(bid)
            if old["fed"] is None:
                jobs.append(("unlocal", old["dial"], {"t": "uncascade", "bridge": bid}))
            else:
                jobs.append(("unfed", old["fed"], {"call": old["call"], "bridge": bid}))
    for kind, to, body in jobs:
        if kind in ("local", "unlocal"):
            horn.send(to, body)
            if kind == "local":
                print(f"horn: Kaskade {body['bridge'][:8]}: {body['peer']} <- Knoten {to[:8]}", flush=True)
        elif kind == "fed" and FED.get("send_cascade"):
            FED["send_cascade"](to, body)
        elif kind == "unfed" and FED.get("send_uncascade"):
            FED["send_uncascade"](to, body["call"], body["bridge"])
    for k in [k for k, v in FN.items() if now - v["at"] > FNODE_TTL]:
        FN.pop(k, None)
    return want


def _local_cmd(c, bid, w, now):
    d, h = horn.node_by_id(c, w["dial"]), horn.node_by_id(c, w["hub"])
    if not d or not h or not d.get("pub") or not h.get("pub"):
        return None
    cid = w["call"]
    return {"t": "cascade", "bridge": bid, "url": h["url"], "peer": h["name"], "peer_pub": h["pub"],
            "token": horn.ctoken("accept", bid, h["name"], horn.call_ref(cid, h["id"]), d["name"], d["pub"], now=now),
            "mine": horn.ctoken("dial", bid, d["name"], horn.call_ref(cid, d["id"]), h["name"], h["pub"], now=now)}


def _fed_body(c, bid, w, now):
    """Auftrag an den Server des fremden Knotens: dessen Knoten soll unseren Mittelpunkt anwaehlen."""
    fn = FN.get((w["call"], w["fed"], w["name"]))
    h = horn.node_by_id(c, w["hub"])
    if not fn or not h or not h.get("pub") or not FED.get("quota"):
        return None
    q = FED["quota"](c, w["fed"])
    if q is None:                                      # Notaus: keine Bruecke zu diesem Server
        return None
    return {"call": w["call"], "bridge": bid, "node": w["name"], "url": h["url"], "peer": h["name"], "peer_pub": h["pub"],
            "token": horn.ctoken("accept", bid, h["name"], horn.call_ref(w["call"], h["id"]), w["name"], fn["pub"], quota=q, now=now)}


def ended(cid):
    """Anruf vorbei (horn.room_close): Bruecken vergessen (die Knoten schliessen sie mit dem Raum)."""
    _HUB.pop(cid, None)
    for bid in [k for k, v in _BR.items() if v["call"] == cid]:
        old = _BR.pop(bid)
        if old["fed"] is not None and FED.get("send_uncascade"):
            threading.Thread(target=FED["send_uncascade"], args=(old["fed"], cid, bid), daemon=True).start()
    for k in [k for k in FN if k[0] == cid]:
        FN.pop(k, None)
    if FED.get("ended"):
        FED["ended"](cid)


def status(cid=None):
    """Diagnose (Tests): gewuenschte Bruecken mit Stand am waehlenden Knoten."""
    out = []
    for bid, v in list(_BR.items()):
        if cid and v["call"] != cid:
            continue
        st = ((horn._LIVE.get(v["dial"]) or {}).get("bridges") or {}).get(bid) or {}
        hst = ((horn._LIVE.get(v["hub"]) or {}).get("bridges") or {}).get(bid) or {}
        out.append({"bridge": bid, "call": v["call"], "dial": v["dial"], "hub": v["hub"], "fed": v["fed"], "st": st.get("st") or "",
                    "hub_st": hst.get("st") or "", "why": st.get("why") or ""})
    return out


horn.HOOKS["connected"] = start
horn.HOOKS["ended"] = ended
