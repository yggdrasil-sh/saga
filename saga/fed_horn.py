"""Gjallarhorn ueber Servergrenzen (Saga 2.5, Paket P10; Konzept v2 §2.7, §3.4; Entscheidung Sven 3): Knoten in
foederierten Gruppenanrufen, Teilen je Knoten, Kontingente je verbundenem Server.

Das Heim des Anrufs (fuehrender Server, lead) koordiniert. Ablauf fuer ein Geraet eines anderen Servers B:
- Kandidaten: B fragt das Heim A (Auftrag horn, signiert wie alle Anruf-Auftraege). A antwortet mit Kandidaten auf
  seinen GETEILTEN Knoten (Medien-Token von A, mit Kontingent q/q_legs/q_kbit fuer B) - nur wenn "Teilen" am Knoten an,
  B erlaubt (Erlaubt-Liste, Standard alle mit peer_ok) und kein Notaus. B haengt seine EIGENEN bereiten Knoten an (Token
  von B). Teilen an: Fremde verbinden sich direkt mit dem Knoten von A. Sonst: ueber ihren eigenen Knoten, Kaskade.
- Wahl: das Geraet meldet seinen Knoten bei B (POST /api/calls/{id}/horn mit node), B reicht ihn im selben Auftrag an A
  weiter (node {name, own, url?, pub?}). A traegt am Endpunkt den eigenen Knoten ein bzw. 'f:<B>:<name>' und merkt sich
  Adresse und Knotenschluessel (horn_bridge.FN) fuer die Kaskade.
- Kaskade: A (saga/horn_bridge.py) bittet B (Auftrag horn_cascade), seinen Knoten den Mittelpunkt von A anwaehlen zu
  lassen. B prueft (Anruf mit A als Heim, eigener Endpunkt auf dem Knoten, kein Notaus fuer A) und gibt dem Knoten das
  dial-Token. horn_uncascade baut ab.
- Topologie beim Start (A): Knoten nur, wenn alle beteiligten Server "horn" melden und jeder entweder von A geteilt
  bekommt oder selbst einen bereiten Knoten hat (Auftrag horn_nodes, 60 s zwischengespeichert). Sonst Mesh wie 2.4 mit
  klarer Meldung (call.horn_mesh); Server ohne "horn" (2.3/2.4) bleiben still im Mesh.
Grenzen je verbundenem Server (horn_share, Admin-Bereich "Foederation"): Standard 10 Teilnehmende und 20 Mbit/s je
Richtung (calls.share_legs / calls.share_mbit), Erlaubt-Liste (allowed), Notaus (off: Knoten entfernen sofort alle
Teilnehmenden und Bruecken dieses Servers, keine neuen Token). Das Kontingent gilt fuer direkte Beine und Bruecken."""
import re
import threading
import time

import auth
from db import connect as _db
from saga import calls, chat, fed_calls as FCL, fed_chat as FC, federation as F, horn, horn_bridge, horn_web
from saga.core import t

SHARE_LEGS, SHARE_MBIT = 10, 20
NODES_TTL = 60                          # Antwort auf horn_nodes so lange gueltig
_RN = {}                                # peer_id -> (bereite Knoten dort, zeitpunkt)


# --- Teilen und Kontingente ---------------------------------------------------------------------------------------------
def share_cfg(c, pid):
    """Einstellungen fuer einen verbundenen Server: {allowed, off, legs, kbit} (Standard: erlaubt, an, 10 / 20 Mbit/s)."""
    cf = auth.settings().get("calls") or {}
    r = c.execute("SELECT allowed, legs, mbit, off FROM horn_share WHERE peer_id=?", (pid,)).fetchone()
    legs = (r[1] if r and r[1] else None) or int(cf.get("share_legs") or SHARE_LEGS)
    mbit = (r[2] if r and r[2] else None) or int(cf.get("share_mbit") or SHARE_MBIT)
    return {"allowed": bool(r[0]) if r else True, "off": bool(r[3]) if r else False, "legs": int(legs), "kbit": int(mbit) * 1000}


def quota(c, pid):
    """Kontingent fuer Token und Bruecken {q, legs, kbit} - None bei Notaus."""
    s = share_cfg(c, pid)
    if s["off"]:
        return None
    return {"q": horn.quota_key(pid), "legs": s["legs"], "kbit": s["kbit"]}


def shared_nodes(c, now=None):
    return [n for n in horn.ready_nodes(c, now) if n["share"]]


def may_share(c, pid):
    s = share_cfg(c, pid)
    return s["allowed"] and not s["off"] and bool(shared_nodes(c))


def _used(c, pid, cid=None, dev=None):
    """Endpunkte dieses Servers auf unseren Knoten (alle laufenden Anrufe), ohne das Geraet selbst."""
    return c.execute("SELECT COUNT(*) FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.peer_id=? AND l.state='active' "
                     "AND k.state<>'ended' AND l.node IS NOT NULL AND l.node NOT LIKE 'f:%' AND NOT (l.call_id=? AND l.device=?)",
                     (pid, cid or "", dev or "")).fetchone()[0]


def own_ready():
    """Zahl unserer bereiten Knoten (Antwort auf horn_nodes)."""
    if not calls.horn_on():
        return 0
    with _db() as c:
        return len(horn.ready_nodes(c))


def set_share(pid, allowed=None, off=None, legs=None, mbit=None, by="admin"):
    """Admin: Einstellungen je Server speichern. Notaus an: sofort an alle Knoten (quota_off)."""
    with _db() as c, c:
        cur = c.execute("SELECT allowed, legs, mbit, off FROM horn_share WHERE peer_id=?", (pid,)).fetchone()
        a = (bool(cur[0]) if cur else True) if allowed is None else bool(allowed)
        o = (bool(cur[3]) if cur else False) if off is None else bool(off)
        lg = (cur[1] if cur else None) if legs is None else (int(legs) if legs else None)
        mb = (cur[2] if cur else None) if mbit is None else (int(mbit) if mbit else None)
        c.execute("INSERT INTO horn_share (peer_id, allowed, legs, mbit, off, updated) VALUES (?,?,?,?,?,?) ON CONFLICT (peer_id) DO UPDATE "
                  "SET allowed=EXCLUDED.allowed, legs=EXCLUDED.legs, mbit=EXCLUDED.mbit, off=EXCLUDED.off, updated=EXCLUDED.updated",
                  (pid, a, lg, mb, o, time.time()))
        was_off = bool(cur[3]) if cur else False
    if o and not was_off:
        notaus(pid)
    threading.Thread(target=push_origins, daemon=True).start()
    print(f"horn: Teilen fuer Server {pid}: {'erlaubt' if a else 'nicht erlaubt'}, {'NOTAUS' if o else 'an'}, "
          f"{lg or 'Standard'} Teilnehmende / {mb or 'Standard'} Mbit/s (von {by})", flush=True)
    return {"allowed": a, "off": o, "legs": lg, "mbit": mb}


def _peer_origin(base):
    from urllib.parse import urlparse
    u = urlparse(base or "")
    return f"{u.scheme}://{u.netloc}" if u.scheme in ("http", "https") and u.netloc else ""


def origins():
    """Herkunft der Seiten verbundener Server, mit denen wir teilen (CORS an unseren Knoten, Steuerkanal 'origins')."""
    if not calls.horn_on():
        return []
    with _db() as c:
        out = []
        for p in F.peers(c):
            if p["status"] == "active" and FCL.peer_horn(p) and share_cfg(c, p["id"])["allowed"] and not share_cfg(c, p["id"])["off"]:
                o = _peer_origin(p["base_url"])
                if o:
                    out.append(o)
    return sorted(set(out))[:64]


def push_origins():
    """Nach einer Aenderung (Teilen, Erlaubt-Liste, Notaus): allen Knoten die erlaubten Herkuenfte schicken und den
    verbundenen Servern die Adressen unserer geteilten Knoten (hello, fuer deren CSP)."""
    lst = origins()
    for nid in list(horn._CONNS):
        horn.send(nid, {"t": "origins", "list": lst})
    if not F.enabled():
        return
    with _db() as c:
        ids = [p["id"] for p in F.peers(c) if p["status"] == "active" and FCL.peer_horn(p)]
    for pid in ids:
        F.peer_submit(pid, F.hello, pid)


def hello_extra(pid):
    """hello an Server pid: Adressen unserer Knoten mit "Teilen an" (nur wenn er sie nutzen darf) - dessen Seiten brauchen
    sie in connect-src, die Geraete sprechen sie direkt an."""
    if not calls.horn_on():
        return {}
    with _db() as c:
        s = share_cfg(c, pid)
        urls = [] if not s["allowed"] or s["off"] else sorted({n["url"] for n in horn.nodes(c) if n["share"] and n.get("url")})
    return {"horn_shared": urls[:8]}


def hello_in(pid, d):
    """Adressen der geteilten Knoten des Servers pid merken (fuer unsere CSP)."""
    if not isinstance(d, dict) or not isinstance(d.get("horn_shared"), list):
        return
    urls = sorted({u for u in d["horn_shared"][:8] if _https(u)})
    cur = auth.settings(fresh=True).get("horn_remote") or {}
    cur = dict(cur) if isinstance(cur, dict) else {}
    if cur.get(str(pid)) == urls:
        return
    if urls:
        cur[str(pid)] = urls
    else:
        cur.pop(str(pid), None)
    auth.save_setting("horn_remote", cur)


def remote_urls():
    """Adressen geteilter Knoten verbundener Server (CSP connect-src unserer Seiten)."""
    cur = auth.settings().get("horn_remote") or {}
    return sorted({u for v in (cur.values() if isinstance(cur, dict) else []) for u in (v if isinstance(v, list) else []) if _https(u)})


def notaus(pid):
    """Alle Knoten: Teilnehmende und Bruecken dieses Servers sofort entfernen (3 min gesperrt, danach gibt Saga ohnehin
    keine Token mehr)."""
    q = horn.quota_key(pid)
    for nid in list(horn._CONNS):
        horn.send(nid, {"t": "quota_off", "q": q, "secs": 180})


def admin_state(c):
    """Fuer die Seite "Foederation": je Server {allowed, off, legs, mbit, used}."""
    out = {}
    for r in c.execute("SELECT id FROM fed_peer").fetchall():
        s = share_cfg(c, r[0])
        row = c.execute("SELECT legs, mbit FROM horn_share WHERE peer_id=?", (r[0],)).fetchone()
        out[r[0]] = {"allowed": s["allowed"], "off": s["off"], "legs": row[0] if row else None, "mbit": row[1] if row else None,
                     "used": _used(c, r[0])}
    return out


# --- Topologie beim Start (Heim) ------------------------------------------------------------------------------------------
def _nodes_of(pid, now=None):
    v = _RN.get(pid)
    return v[0] if v and (now or time.time()) - v[1] < NODES_TTL * 10 else 0


def prefetch(chat_id):
    """Vor dem Start eines Gruppenanrufs (ausserhalb der Transaktion): bereite Knoten der beteiligten Server abfragen,
    deren Stand aelter als NODES_TTL ist und die wir nicht ohnehin teilen wuerden. Parallel, harte Frist."""
    if not calls.horn_on():
        return
    now = time.time()
    with _db() as c:
        ch = chat._load(c, chat_id)
        if not ch or not chat.is_fed(ch) or ch["home_peer"] is not None:
            return
        ps = [p for p in FC.participants(c, ch) if FCL.peer_horn(p) and now - (_RN.get(p["id"]) or (0, 0))[1] > NODES_TTL
              and not may_share(c, p["id"])]
    ths = [threading.Thread(target=_probe, args=(p,), daemon=True) for p in ps[:20]]
    for th in ths:
        th.start()
    for th in ths:
        th.join(4)


def prefetch_ref(ref):
    """Wie prefetch, fuer einen Start ueber die Foederation (Chat per ref)."""
    with _db() as c:
        ch = FC.by_ref(c, ref)
    if ch:
        prefetch(ch["id"])


def _probe(p):
    st, d = FC.sync_call(p, {"op": "horn_nodes"}, path="/fed/v2/call")
    if st == 200 and isinstance(d, dict) and isinstance(d.get("nodes"), int):
        _RN[p["id"]] = (max(0, d["nodes"]), time.time())


def note_nodes(pid, d):
    """Angabe 'horn_nodes' in einem Auftrag merken (Start/Beitritt von einem anderen Server)."""
    if isinstance(d, dict) and isinstance(d.get("horn_nodes"), int):
        _RN[pid] = (max(0, d["horn_nodes"]), time.time())


def fed_ok(c, ch, starter_pid=None):
    """Gruppenanruf in einem foederierten Chat ueber Knoten? -> (ja, hinweis). Hinweis nur, wenn Knoten moeglich
    waeren, aber ein beteiligter Server keinen nutzen kann (Fall c: kein eigener Knoten und wir teilen nicht)."""
    ps = FC.participants(c, ch)
    if any(not FCL.peer_horn(p) for p in ps):
        return False, None                              # Server ohne horn (2.3/2.4): still Mesh
    none = [p["host"] for p in ps if not may_share(c, p["id"]) and _nodes_of(p["id"]) <= 0]
    if none:
        return False, t("Teilnehmende von {hosts} können keinen Gjallarhorn-Knoten nutzen (dort ist keiner eingerichtet und dieser Server teilt seine Knoten nicht). Der Anruf läuft deshalb direkt zwischen den Geräten (höchstens {v} mit Video bzw. {a} nur mit Ton).",  # noqa: E501
                        hosts=", ".join(none), v=calls.limit("video"), a=calls.limit("audio"))
    return True, None


# --- Heim: Auftraege von anderen Servern ---------------------------------------------------------------------------------
def _leg_of(c, peer, d):
    """(anruf, endpunkt) fuer einen Auftrag eines anderen Servers zu einem Anruf, den wir fuehren."""
    h, p_ = FCL._actor3(c, peer, d)
    call = calls._load(c, d.get("id")) if isinstance(d.get("id"), str) else None
    if not h or not call or call["lead"] is not None or call["state"] == "ended" or not calls.is_horn(call):
        return None, None
    dev = d.get("device")
    if not (isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return None, None
    r = c.execute("SELECT who, peer_id, state, role, node FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], dev)).fetchone()
    if not r or r[0] != p_ or r[1] != peer["id"] or r[2] != "active":
        return None, None
    return call, {"device": dev, "role": r[3], "node": r[4]}


def _https(u):
    """Knotenadresse: genau ein Ursprung https://host[:port] (horn.https_origin)."""
    return bool(horn.https_origin(u))


CTOK_MAX = 4096                         # Hoechstlaenge eines Kaskaden-Tokens (wie der Knoten, bifrost.go ctokMaxLen)
_CTOK = re.compile(r"c1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", re.A)


def ctok_ok(tok):
    """Fremdes Kaskaden-Token: geht unveraendert in eine Kopfzeile der Waehlanfrage unseres Knotens - nur Base64url und
    Punkte, feste Hoechstlaenge (kein CR/LF)."""
    return isinstance(tok, str) and 8 <= len(tok) <= CTOK_MAX and bool(_CTOK.fullmatch(tok))


def op_horn(c, peer, d):
    """Kandidaten auf unseren geteilten Knoten fuer ein Geraet eines anderen Servers; mit node: dessen Wahl eintragen."""
    note_nodes(peer["id"], d)
    call, leg = _leg_of(c, peer, d)
    if not call:
        return 404, {"error": "not found"}
    if not F._rate.allow("chorn:" + leg["device"], 40):
        return 429, {"error": "too many"}
    q = quota(c, peer["id"])
    nd = d.get("node") if isinstance(d.get("node"), dict) else None
    if nd is not None:
        name = str(nd.get("name") or "")[:253].strip().lower()
        if nd.get("own"):
            pub = str(nd.get("pub") or "")
            if q is None or not name or not _https(nd.get("url")) or len(pub) != 44:
                return 409, {"error": "node"}
            horn_bridge.FN[(call["id"], peer["id"], name)] = {"url": nd["url"], "pub": pub, "at": time.time()}
            c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=?", (f"f:{peer['id']}:{name}", call["id"], leg["device"]))
        else:
            n = horn.node_named(c, name)
            if not n or not n["share"] or not horn.ready(n) or not may_share(c, peer["id"]):
                return 409, {"error": "node"}
            if _used(c, peer["id"], call["id"], leg["device"]) >= q["legs"]:
                return 409, {"error": "quota"}
            horn.set_leg_node(c, call["id"], leg["device"], n["id"])
    share, why = q is not None and may_share(c, peer["id"]), None
    if q is None:
        why = "off"
    elif share and _used(c, peer["id"], call["id"], leg["device"]) >= q["legs"]:
        share, why = False, "quota"
    elif not share:
        why = "noshare"
    cands = []
    if share:
        avoid = horn_web._clean_avoid(d.get("avoid"))
        dcl = "mobile" if d.get("dev_class") == "mobile" else "desktop"
        cands = horn_web.candidates(c, call, leg["device"], leg["role"], dcl, avoid, only={n["id"] for n in shared_nodes(c)}, quota=q)
    assigned = None
    if nd is None and not leg["node"]:                 # Beitritt: Knoten vormerken wie lokal (das Geraet nimmt meist den ersten)
        of = d.get("own_first") if isinstance(d.get("own_first"), dict) else None
        if cands:
            n = horn.node_named(c, cands[0]["node"])
            if n:
                horn.set_leg_node(c, call["id"], leg["device"], n["id"])
                assigned = {"name": n["name"], "own": False}
        elif q is not None and of and _https(of.get("url")) and len(str(of.get("pub") or "")) == 44 and of.get("name"):
            name = str(of["name"])[:253].strip().lower()
            horn_bridge.FN[(call["id"], peer["id"], name)] = {"url": of["url"], "pub": str(of["pub"]), "at": time.time()}
            c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=?", (f"f:{peer['id']}:{name}", call["id"], leg["device"]))
            assigned = {"name": name, "own": True}
    hub = horn.call_node(c, call["id"])
    hn = horn.node_by_id(c, hub) if hub else None
    return 200, {"ok": True, "candidates": cands, "share": share, "why": why, "cascade": q is not None, "here": hn["name"] if hn else None,
                 **({"assigned": assigned} if assigned else {})}


def op_horn_nodes(c, peer, d):
    """Wie viele bereite Knoten haben wir? (Topologie-Entscheidung des Heims)"""
    if share_cfg(c, peer["id"])["off"] or not calls.horn_on():
        return 200, {"nodes": 0}
    return 200, {"nodes": len(horn.ready_nodes(c))}


def _dial_ok(u):
    """Darf unser Knoten diese Adresse anwaehlen? Oeffentliche Adressen immer, interne nur wie bei der Foederation
    (Admin: "Freigegebene interne Hosts") - das Heim soll unseren Knoten nicht ins eigene Netz schicken koennen.
    -> gepruefte IP-Adressen (der Knoten waehlt genau diese, kein erneutes DNS: Rebinding) oder [] (abgelehnt)."""
    import socket
    from urllib.parse import urlparse
    try:
        p = urlparse(u)
        host, port = p.hostname, p.port or 443
        ips = sorted({i[4][0].split("%")[0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (OSError, ValueError, UnicodeError):
        return []
    return ips[:8] if ips and all(F._ip_ok(ip, host, port) for ip in ips) else []


def op_horn_cascade(c, peer, d):
    """Vom Heim: unser Knoten soll dessen Mittelpunkt anwaehlen (Kaskade). Wir pruefen und geben das dial-Token."""
    call = calls._load(c, d.get("id")) if isinstance(d.get("id"), str) else None
    if not call or call["lead"] != peer["id"] or call["state"] == "ended" or not calls.is_horn(call):
        return 404, {"error": "not found"}
    if share_cfg(c, peer["id"])["off"]:
        return 403, {"error": "off"}
    bid, name, peer_name, ppub, tok = (d.get(k) for k in ("bridge", "node", "peer", "peer_pub", "token"))
    if not all(isinstance(x, str) and 8 <= len(x) <= 4096 for x in (bid, name, peer_name, ppub, tok)) or not _https(d.get("url")) \
            or len(ppub) != 44 or len(bid) > 64 or not ctok_ok(tok):
        return 400, {"error": "bad request"}
    n = horn.node_named(c, name[:253])
    if not n or n["id"] not in horn._CONNS:
        return 409, {"error": "node"}
    ips = _dial_ok(d["url"])
    if not ips:
        F.log(c, "call.forged", peer, "horn_cascade", throttle=(peer["id"], "chorncasc"))
        return 403, {"error": "address"}
    if not c.execute("SELECT 1 FROM chat_call_leg WHERE call_id=? AND node=? AND state='active' AND peer_id IS NULL LIMIT 1",
                     (call["id"], n["id"])).fetchone():
        return 409, {"error": "no legs"}
    st = (((horn._LIVE.get(n["id"]) or {}).get("bridges") or {}).get(bid) or {}).get("st") or ""
    if st == "up":
        return 200, {"ok": True, "st": st}
    mine = horn.ctoken("dial", bid, n["name"], horn.call_ref(call["id"], n["id"]), peer_name, ppub)
    msg = {"t": "cascade", "bridge": bid, "url": d["url"], "ips": ips, "peer": peer_name, "peer_pub": ppub, "token": tok, "mine": mine}
    c.after_commit(lambda: horn.send(n["id"], msg))
    _MINE.setdefault(call["id"], set()).add((n["id"], bid))
    print(f"horn: Kaskade {bid[:8]} fuer {peer['host']}: Knoten {n['name']} -> {peer_name}", flush=True)
    return 200, {"ok": True, "st": st or "dial"}


_MINE = {}                              # call_id -> {(node_id, bridge)} (Kopien: Bruecken unserer Knoten zum Heim)


def op_horn_uncascade(c, peer, d):
    call = calls._load(c, d.get("id")) if isinstance(d.get("id"), str) else None
    bid = d.get("bridge")
    if not call or call["lead"] != peer["id"] or not isinstance(bid, str):
        return 404, {"error": "not found"}
    for nid, b in list(_MINE.get(call["id"], ())):
        if b == bid:
            c.after_commit(lambda nid=nid: horn.send(nid, {"t": "uncascade", "bridge": bid}))
            _MINE[call["id"]].discard((nid, b))
    return 200, {"ok": True}


def send_cascade(pid, body):
    FC._volatile(pid, {"op": "horn_cascade", "id": body["call"], **{k: body[k] for k in ("bridge", "node", "url", "peer", "peer_pub", "token")}},
                 path="/fed/v2/call", drop=False)


def send_uncascade(pid, cid, bid):
    FC._volatile(pid, {"op": "horn_uncascade", "id": cid, "bridge": bid}, path="/fed/v2/call", drop=False)


horn_bridge.FED.update(send_cascade=send_cascade, send_uncascade=send_uncascade, quota=quota)
FCL.HANDLERS.update(horn=op_horn, horn_nodes=op_horn_nodes, horn_cascade=op_horn_cascade, horn_uncascade=op_horn_uncascade)
FCL.OPS_HORN = ("horn", "horn_nodes", "horn_cascade", "horn_uncascade")
MESH = FCL.HORN_MESH


# --- Kopie (anderer Server fuehrt den Anruf): eigene Geraete ---------------------------------------------------------------
def block(uid, call, dev, role, dev_class="desktop", avoid=(), node=None):
    """Kandidaten fuer ein eigenes Geraet in einem Anruf, den ein anderer Server fuehrt: geteilte Knoten des Heims (dessen
    Token) und eigene bereite Knoten. node = Wahl des Geraets (Name): eigener Knoten -> Endpunkt hier und beim Heim
    melden (mit Adresse und Knotenschluessel fuer die Kaskade); Knoten des Heims -> dort eintragen lassen.
    -> (block, fehler)"""
    with _db() as c:
        p = F.peer_by(c, "id", call["lead"])
        mh = FC.my_handle(c, uid)
        own = horn.ready_nodes(c) if calls.horn_on() else []
        own = [n for n in own if n["name"] not in avoid]
        mine = horn.node_named(c, node) if node else None
        off = share_cfg(c, call["lead"])["off"]
    if not FCL.peer_horn(p):
        return None, t("Der Server {host} kann keine Anrufe über Knoten (braucht Saga 2.5).", host=(p or {}).get("host") or "?")
    body = {"op": "horn", "id": call["id"], "actor": mh, "device": dev, "dev_class": dev_class, "avoid": list(avoid), "horn_nodes": len(own)}
    if not node and own:                               # Vorschlag fuer die Vormerkung beim Heim: unser Knoten des Anrufs, sonst der beste
        with _db() as c:
            h = horn_web.home(c, call["id"])
        f = next((n for n in own if n["id"] == h), own[0])
        body["own_first"] = {"name": f["name"], "url": f["url"], "pub": f["pub"]}
    nd = None
    if node:
        if mine is not None:
            if not horn.ready(mine):
                return None, t("ungültige Angabe")
            nd = {"name": mine["name"], "own": True, "url": mine["url"], "pub": mine["pub"]}
        else:
            nd = {"name": node[:253], "own": False}
        body["node"] = nd
    st, d = FC.sync_call(p, body, path="/fed/v2/call") if not off else (403, {"error": "off"})
    if node and st != 200:
        return None, t("ungültige Angabe")
    asg = (d or {}).get("assigned") if st == 200 and isinstance(d, dict) and isinstance((d or {}).get("assigned"), dict) else None
    with _db() as c, c:
        if node:
            v = mine["id"] if mine is not None else f"f:{call['lead']}:{node[:253]}"
            c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=? AND state='active'", (v, call["id"], dev))
        elif asg and isinstance(asg.get("name"), str):  # vom Heim vorgemerkt (Beitritt)
            n = horn.node_named(c, asg["name"][:253]) if asg.get("own") else None
            v = n["id"] if n else (None if asg.get("own") else f"f:{call['lead']}:{asg['name'][:253]}")
            if v:
                c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=? AND state='active'", (v, call["id"], dev))
        theirs = (d or {}).get("candidates") if st == 200 and isinstance(d, dict) else []
        theirs = [x for x in theirs or [] if isinstance(x, dict) and x.get("node") and _https(x.get("url")) and isinstance(x.get("tokens"), dict)][:horn.CANDIDATES]
        for x in theirs:
            x["home"] = True
        cur = horn_web.home(c, call["id"])
        ours = horn_web.candidates(c, call, dev, role, dev_class, avoid) if own and (d or {}).get("cascade", True) is not False else []
        share = bool(theirs)
        for x in ours:
            x["here"] = bool(cur) and not share and x["node"] == (horn.node_by_id(c, cur) or {}).get("name")
        cands = (theirs + ours)[:horn.CANDIDATES + 1]
        info = {"share": share, "why": (d or {}).get("why") if isinstance(d, dict) else "gone"}
    if not cands:
        why = info["why"]
        msg = t("Der Server {host} teilt gerade keinen Knoten mit diesem Server und hier ist keiner bereit.", host=p["host"]) \
            if why in ("noshare", "off", None) else t("Das Kontingent für Teilnehmende dieses Servers auf den Knoten von {host} ist erschöpft.", host=p["host"]) \
            if why == "quota" else t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"])
        return {"e2e": "saga-horn/v1", "ttl": horn.TOKEN_TTL, "candidates": [], "fed": info, "error": msg}, None
    return {"e2e": "saga-horn/v1", "ttl": horn.TOKEN_TTL, "candidates": cands, "fed": info}, None


def left(c, call, dev):
    """Eigenes Geraet hat einen Anruf verlassen, den ein anderer Server fuehrt: Beine an unserem Knoten beenden."""
    r = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], dev)).fetchone()
    if r and r[0] and not r[0].startswith("f:"):
        horn.kick(c, call["id"], dev, r[0])


def _ended(cid):
    _MINE.pop(cid, None)
    MESH.pop(cid, None)


horn_bridge.FED["ended"] = _ended
horn.HOOKS["origins"] = origins
horn.HOOKS["csp"] = remote_urls
horn.HOOKS["share_changed"] = push_origins
F.HELLO_EXTRA.append(hello_extra)
F.HELLO_IN.append(hello_in)

