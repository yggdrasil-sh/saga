"""Netz-Karte im Admin-Bereich (2.5, Admin -> "Netz"): dieser Server, verbundene Saga-Server (Foederation), Gjallarhorn-
Knoten (eigene, geteilte, von Partnern freigegebene) und die gerade verbundenen Clients - gruppiert nach Grobklasse
(saga.clientos), nur Anzahlen. Dazu laufende Anrufe (Weg Client -> Server -> Knoten -> Partner) und Zaehler der
Foederations-Nachrichten (Pulse an der Kante).

Datenschutz: keine Konten, keine Namen von Personen, keine IP-Adressen (auch nicht von Knoten - nur "IPv4/IPv6 ja/nein"),
keine Schluessel, Tokens oder Anruf-Kennungen (Anrufe nur als kurzer Hash). Partnerdaten nur, was die Foederation ohnehin
liefert: Name, Host, Zustand, letzter Kontakt/Fehler, gemeinsame Faehigkeiten, Version aus /.well-known/saga.

Messungen (Latenz zu Partnern und Knoten, UDP) nie je Seitenaufruf: snapshot() stoesst hoechstens alle PROBE_EVERY
Sekunden EINE Pruefung im Hintergrund an (mit Zeitlimits) und liefert sofort den letzten Stand. Ohne offene Karte wird
nichts geprueft. Der Stand selbst (Datenbank + Speicher) ist SNAP_TTL Sekunden zwischengespeichert."""
import hashlib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from db import connect as _db
from saga import calls, clientos, federation, horn, horn_ops, live, netguard, push_stream
from saga.core import E, title
from version import VERSION_LABEL

PROBE_EVERY = max(5, int(E.get("SAGA_NET_PROBE", "60")))     # Sekunden zwischen zwei Hintergrund-Pruefungen
SNAP_TTL = 2.0
FED_TIMEOUT = 4.0
ECHO_TIMEOUT = 3.0
SLOW_MS = 800                    # ab hier gilt eine Verbindung als langsam
STALE_S = 3600                   # Partner ohne Kontakt seit einer Stunde: langsam (gelb), nicht rot

_lock = threading.Lock()
_P = {"at": 0.0, "running": False, "peers": {}, "horns": {}}
_S = {"at": 0.0, "d": None}


# --- Hintergrund-Pruefung ---------------------------------------------------------------------------------------------
def _probe_peer(p):
    t0 = time.monotonic()
    try:
        info = federation.fetch_info(p["base_url"], timeout=FED_TIMEOUT)
        return {"rtt": round((time.monotonic() - t0) * 1000), "version": info.get("version") or "", "err": "", "at": time.time()}
    except federation.FedError as ex:
        return {"rtt": None, "version": "", "err": str(ex)[:120], "at": time.time()}
    except Exception as ex:  # noqa: BLE001 - nur Anzeige
        return {"rtt": None, "version": "", "err": type(ex).__name__, "at": time.time()}


def _horn_ips(n):
    if not n["builtin"]:
        return [x for x in (n.get("v4"), n.get("v6")) if x], int(n.get("port") or 443)
    u = urlparse(n.get("url") or "")
    if not u.hostname:
        return [], 443
    try:
        ips = sorted({i[4][0] for i in netguard.lookup(u.hostname, u.port or 443, type=socket.SOCK_STREAM)})
    except (OSError, UnicodeError):
        ips = []
    return ips[:2], u.port or 443


def _probe_horn(n):
    ips, port = _horn_ips(n)
    out = {"rtt": None, "udp_rtt": None, "udp": None, "err": "", "at": time.time()}
    for ip in ips:
        t0 = time.monotonic()
        ok, err = horn_ops.echo_check(n["name"], n["pub"], ip, port, timeout=ECHO_TIMEOUT)
        if ok:
            out["rtt"] = round((time.monotonic() - t0) * 1000)
            out["err"] = ""
            if n.get("udp"):
                t1 = time.monotonic()
                out["udp"] = horn_ops.udp_check(ip, horn.udp_port(n), timeout=1.0, tries=1)
                if out["udp"]:
                    out["udp_rtt"] = round((time.monotonic() - t1) * 1000)
            break
        out["err"] = err
    if not ips:
        out["err"] = "noaddr"
    return out


def _probe():
    try:
        with _db() as c:
            ps = [p for p in federation.peers(c) if p["status"] == "active"]
            ns = horn.nodes(c) if horn.horn_on() else []
        jobs = {}
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="netmap") as ex:
            if federation.enabled():
                for p in ps[:64]:
                    jobs[("p", p["id"])] = ex.submit(_probe_peer, p)
            for n in ns[:64]:
                if n["builtin"] and not horn.builtin_on():
                    continue
                jobs[("h", n["id"])] = ex.submit(_probe_horn, n)
        peers, horns = {}, {}
        for (k, i), f in jobs.items():
            try:
                (peers if k == "p" else horns)[i] = f.result()
            except Exception:  # noqa: BLE001
                pass
        with _lock:
            _P.update(peers=peers, horns=horns, at=time.time())
    finally:
        with _lock:
            _P["running"] = False


def probe_if_stale(now=None):
    """Hoechstens alle PROBE_EVERY Sekunden eine Pruefung im Hintergrund (nie mehrere gleichzeitig). -> True, wenn gestartet."""
    now = now or time.time()
    with _lock:
        if _P["running"] or now - _P["at"] < PROBE_EVERY:
            return False
        _P["running"] = True
        _P["at"] = now                   # auch bei Fehlschlag erst nach PROBE_EVERY erneut
    threading.Thread(target=_probe, name="netmap-probe", daemon=True).start()
    return True


# --- Stand ------------------------------------------------------------------------------------------------------------
def _h(x):
    return hashlib.sha256(("saga-net|" + str(x)).encode()).hexdigest()[:10]


def _peer_state(p, pr, now):
    if p["status"] != "active":
        return "pending" if p["status"] in ("pending_in", "pending_out") else "off"
    if not federation.enabled():
        return "off"
    if (pr or {}).get("err") or p.get("last_error"):
        return "err"
    if (pr or {}).get("rtt") is not None and pr["rtt"] >= SLOW_MS:
        return "slow"
    if p.get("last_seen") and now - p["last_seen"] > STALE_S:
        return "slow"
    return "ok"


H_STATE = {"ready": "ok", "full": "slow", "draining": "slow", "draining_upd": "slow", "updating": "slow",
           "nocert": "err", "down": "err", "waiting": "pending", "off": "off"}


def _build():
    now = time.time()
    with _lock:
        pp, ph, pat = dict(_P["peers"]), dict(_P["horns"]), _P["at"]
    fed_on = federation.enabled()
    with _db() as c:
        ps = federation.peers(c)
        ns = horn.nodes(c) if horn.horn_on() else []
        checks = {r[0]: r[1] for r in c.execute("SELECT id, checks FROM horn_node WHERE removed IS NULL").fetchall()} if ns else {}
        remote = []
        if horn.horn_on():
            from saga import fed_horn
            remote = fed_horn.remote_shared(c)
        crow = c.execute("SELECT k.id, k.media, k.topo, k.state, k.peer_id, l.device, l.peer_id, l.node FROM chat_call k "
                         "JOIN chat_call_leg l ON l.call_id=k.id WHERE k.state<>'ended' AND l.state='active' "
                         "ORDER BY k.created LIMIT 2000").fetchall()
    peers, horns, clients, cl_calls, edges = [], [], [], [], []
    me = {"id": "self", "name": title(), "host": federation.SELF_HOST, "version": VERSION_LABEL, "fed": fed_on,
          "horn": horn.horn_on(), "calls": calls.enabled()}
    host2peer = {}
    for p in ps:
        pr = pp.get(p["id"]) or {}
        st = _peer_state(p, pr, now)
        nid = f"p{p['id']}"
        host2peer[p["host"]] = nid
        peers.append({"id": nid, "name": p.get("name") or p["host"], "host": p["host"], "status": p["status"], "state": st,
                      "last_seen": p.get("last_seen"), "last_error": (p.get("last_error") or "")[:200] or None,
                      "probe_error": pr.get("err") or None, "version": pr.get("version") or "", "rtt": pr.get("rtt"),
                      "protocol": p.get("protocol"), "caps": sorted(federation.caps_of(p))})
        edges.append({"a": "self", "b": nid, "kind": "fed", "state": st, "rtt": pr.get("rtt")})
    own_ids = set()
    for n in ns:
        st_key, since = horn_ops._status(n, now)
        live_ = horn._LIVE.get(n["id"]) or {}
        ld = live_.get("load") or n.get("load") or {}
        ck = checks.get(n["id"]) or {}
        pr = ph.get(n["id"]) or {}
        hid = "h" + _h(n["id"])
        own_ids.add(n["id"])
        udp_ok = pr.get("udp") if pr.get("udp") is not None else (live_.get("udp_check") == "ok" if live_.get("udp_check") else None)
        state = H_STATE.get(st_key, "err")
        if state == "ok" and pr.get("err") and pr.get("rtt") is None and not n["builtin"]:
            state = "slow"                      # Steuerung verbunden, Echo von hier aber nicht erreichbar
        horns.append({"id": hid, "name": n["name"], "kind": "shared" if n["share"] else "own", "builtin": bool(n["builtin"]),
                      "owner": "self", "status": st_key, "state": state, "since": since, "version": n.get("version") or "",
                      "tcp": bool(ck.get("v4") or ck.get("v6")) or n["id"] in horn._CONNS, "udp": bool(n["udp"]), "udp_ok": udp_ok,
                      "v4": bool(n.get("v4") or ck.get("v4")), "v6": bool(n.get("v6") or ck.get("v6")),
                      "rtt": pr.get("rtt"), "udp_rtt": pr.get("udp_rtt"), "probe_error": pr.get("err") or None,
                      "peers": int(ld.get("peers") or 0), "util": round(100 * horn.util(n))})
        edges.append({"a": "self", "b": hid, "kind": "horn", "state": state, "rtt": pr.get("rtt"), "udp": udp_ok})
    for r in remote:
        pid = host2peer.get(r["host"])
        for u in r["urls"][:8]:
            hid = "r" + _h(r["host"] + "|" + u)
            horns.append({"id": hid, "name": u.split("/")[0], "kind": "remote", "builtin": False, "owner": pid or "self",
                          "status": "remote", "state": "ok", "version": "", "tcp": True, "udp": None, "udp_ok": None,
                          "v4": None, "v6": None, "rtt": None, "udp_rtt": None, "peers": None, "util": None, "from": r["name"]})
            edges.append({"a": pid or "self", "b": hid, "kind": "share", "state": "ok", "rtt": None})
    # Clients: Live-Kanal, Push-Verbindungen, Anruf-Teilnehmer je Grobklasse
    agg = {}

    def add(cls, k, n=1):
        cls = cls if cls in clientos.CLASSES else "other"
        agg.setdefault(cls, {"live": 0, "push": 0, "call": 0})[k] += n
    for cls, n in live.os_counts().items():
        add(cls, "live", n)
    for cls, n in push_stream.os_counts().items():
        add(cls, "push", n)
    callmap = {}
    for cid, media, topo, cstate, cpeer, dev, lpeer, node in crow:
        k = callmap.get(cid)
        if k is None:
            k = callmap[cid] = {"id": _h(cid), "media": media, "topo": topo, "state": cstate, "node": None, "local": 0,
                                "remote": 0, "os": {}, "peers": set()}
            if cpeer:
                k["peers"].add(f"p{cpeer}")
        if node and node in own_ids and not k["node"]:
            k["node"] = "h" + _h(node)
        if lpeer:
            k["remote"] += 1
            k["peers"].add(f"p{lpeer}")
        else:
            k["local"] += 1
            cls = clientos.call_class(dev, now)
            k["os"][cls] = k["os"].get(cls, 0) + 1
            add(cls, "call")
    for cls in clientos.CLASSES:
        if cls in agg:
            a = agg[cls]
            gid = "os:" + cls
            clients.append({"id": gid, "cls": cls, "live": a["live"], "push": a["push"], "call": a["call"],
                            "n": a["live"] + a["push"] + a["call"]})
            edges.append({"a": gid, "b": "self", "kind": "client", "state": "ok", "rtt": None})
    for k in callmap.values():
        k["peers"] = sorted(x for x in k["peers"] if any(p["id"] == x for p in peers))
        cl_calls.append(k)
    act = {f"p{pid}": dict(v) for pid, v in list(federation.ACTIVITY.items())}
    return {"at": now, "probe_at": pat or None, "probe_every": PROBE_EVERY, "self": me, "peers": peers, "horns": horns,
            "clients": clients, "calls": cl_calls, "edges": edges, "act": act}


def snapshot(force=False):
    """Stand fuer GET /api/net (zwischengespeichert, stoesst ggf. eine Hintergrund-Pruefung an)."""
    probe_if_stale()
    now = time.time()
    with _lock:
        d = _S["d"]
        if d is not None and not force and now - _S["at"] < SNAP_TTL:
            return d
    d = _build()
    with _lock:
        _S.update(at=now, d=d)
    return d
