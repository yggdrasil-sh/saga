"""Gjallarhorn-Steuerung (Saga 2.5, Paket P3; Konzept v2 §2.1-2.4, §2.6, §2.10): Knotenregister, Saga-Signaturschluessel,
Medien-Token, Kandidaten im Beitritt und Steuerkanal horn-ctl/1 (WebSocket /api/horn/ctl).

Python bleibt die Instanz fuer Rechte und Signale (saga/calls.py). Medienbytes laufen nie durch Python: Geraete senden
Ton und Bild direkt an einen Knoten (gjallarhorn/, Go), Python stellt nur kurzlebige, signierte Medien-Token aus.

Medien-Token (Gegenstueck gjallarhorn/horntoken.go):
    h1.<payload>.<sig>   payload = B64URL(JSON), sig = B64URL(Ed25519(horn_sign, "saga-horn-token/1." + payload))
    JSON {v:1, node, call_ref, peer_ref, leg_ref, role: audio|video|both, pub: [a,v,s], max_kbit, udp_ok,
          dev_class: desktop|mobile, exp: jetzt + 60 s, jti}
  call_ref/peer_ref/leg_ref leitet Saga je (Anruf, Knoten[, Geraet[, Rolle]]) mit HMAC aus einem Geheimnis ab - je Knoten
  andere, zufaellig aussehende Kennungen, keine Konto- oder Geraetenamen. Der Knoten prueft lokal (kein Rueckruf), nimmt
  jedes Token nur einmal (jti) und nur fuer sich selbst (node). Ein Bein je leg_ref; ein zweites ersetzt das erste.
  horn_sign: Ed25519, liegt verschluesselt in den Einstellungen (Bereich "horn", Fernet "enc:" wie alle Geheimnisse).

Steuerkanal horn-ctl/1 (der Knoten verbindet sich AUSGEHEND, gjallarhorn/ctl.go): challenge -> hello (vom Knoten
signiert) -> welcome {node_id, saga_pub, origin, rooms, want, solo, drain, cert}; danach Folgenummern je Richtung.
Saga -> Knoten: open, close, kick, ping, drain, peers, version, cert, acme_txt (Antwort), remove (signiert).
Knoten -> Saga: load (alle 5 s), closed, pong, addr, cert, update, acme_txt (DNS-01, nur der eigene Name).
Zugang: eingebauter Knoten mit dem gemeinsamen Geheimnis SAGA_HORN_KEY von saga-media; gekoppelte Knoten (P6,
"Erweitern", saga/horn_ops.py) nur mit dem beim Koppeln registrierten Knotenschluessel (hello-Signatur ueber die
Challenge) - gesperrte Schluessel (Entfernen) bekommen 4410. Kaskade folgt mit P9, Foederation mit P10 (CASCADE, share
wirken bis dahin nicht)."""
import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from urllib.parse import urlparse
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

import auth
from db import connect as _db
from saga import bus
from saga.core import BASE_URL, E, MEDIA_KEY, SECRET
from version import VERSION, parse as _vparse

router = APIRouter()

HORN_KEY = E.pop("SAGA_HORN_KEY", "")   # von saga-media (eingebauter Knoten); nicht an Kindprozesse weitergeben
PROTO = "horn-ctl/1"
TOKEN_TTL = 60                          # Medien-Token gelten 60 s
READY_S = 30                            # Knoten gilt als bereit, solange seine letzte Lastmeldung juenger ist
SEEN_EVERY = 10                         # Lastmeldung hoechstens so oft in die Datenbank (Bereitschaft fuer alle Prozesse)
CANDIDATES = 3                          # Kandidaten im Beitritt (§2.6)
CASCADE = False                         # P9: ohne Kaskade bleibt ein Anruf auf EINEM Knoten (nur dieser ist Kandidat)
MAX_KBIT = 4000                         # Eingang je Bein (§3.4)
FULL, PENALTY = 0.9, 0.7                # Auslastung: ab 90 % voll, ab 70 % Abzug bei der Wahl
LIMITS = {"tcp": (12, 50), "udp": (25, 100)}   # Endpunkte je Anruf (Video, nur Ton) nach Weg (§1.4, Entscheidung 4)
PUB = {"audio": ["a"], "video": ["v", "s"], "both": ["a", "v", "s"]}

_SK = {"k": None, "pub": ""}
_LOCK = threading.Lock()
_LIVE = {}                              # node_id -> {"load": {...}, "at": t, "db": t}  (nur dieser Prozess)
_CONNS = {}                             # node_id -> _Conn (Steuerverbindung in diesem Prozess)
HOOKS = {}                              # saga/horn_ops.py: acme_txt, addr (gekoppelte Knoten, P6/P7)


def _b64u(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def horn_on():
    """Feature-Flag calls.horn (Standard aus, gleiche Quelle wie saga/calls.py)."""
    c = auth.settings().get("calls") or {}
    return bool(c.get("horn")) and bool(c.get("enabled", True))


def builtin_on():
    """Admin-Schalter "Eingebauten Knoten fuer Anrufe nutzen" (calls.horn_builtin, Standard an). Aus: saga-media startet
    ihn nicht, Anrufe laufen nur ueber gekoppelte Knoten (keiner bereit: Mesh 2.4)."""
    return bool((auth.settings().get("calls") or {}).get("horn_builtin", True))


def version_ok(v):
    """horn-ctl/1: Knoten der eigenen und der vorigen Minor-Version (neuere auch - sie folgen der Zielversion)."""
    a, b = _vparse(v), _vparse(VERSION)
    return a[0] == b[0] and a[1] >= b[1] - 1


# --- Saga-Signaturschluessel horn_sign ------------------------------------------------------------------------------
def _signer():
    """Ed25519-Schluessel von Saga (einmal erzeugt, verschluesselt in den Einstellungen). Mehrere Prozesse an derselben
    Datenbank: wer zuerst schreibt, gewinnt; alle lesen danach dasselbe."""
    if _SK["k"] is not None:
        return _SK["k"]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    with _LOCK:
        if _SK["k"] is not None:
            return _SK["k"]
        for _ in range(2):
            seed = _seed(auth.settings(fresh=True).get("horn") or {})
            if seed:
                k = Ed25519PrivateKey.from_private_bytes(seed)
                _SK.update(k=k, pub=base64.b64encode(k.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode())
                return k
            seed = secrets.token_bytes(32)
            pub = base64.b64encode(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
            v = json.dumps({"sign_sk": auth._enc(base64.b64encode(seed).decode()), "sign_pk": pub})
            with _db() as c, c:
                row = c.execute("SELECT v FROM setting WHERE k='horn'").fetchone()
                if row is None:
                    c.execute("INSERT INTO setting (k, v) VALUES ('horn', ?) ON CONFLICT (k) DO NOTHING", (v,))
                elif not _seed(_dec_horn(row[0])):          # unlesbar (SECRET_KEY gewechselt): neu
                    c.execute("UPDATE setting SET v=? WHERE k='horn' AND v=?", (v, row[0]))
            auth._settings_cache["v"] = None
        raise RuntimeError("horn_sign nicht lesbar")


def _dec_horn(raw):
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return {"sign_sk": auth._dec(d.get("sign_sk"))} if isinstance(d, dict) else {}


def _seed(h):
    try:
        s = base64.b64decode(h.get("sign_sk") or "", validate=True)
    except ValueError:
        return None
    return s if len(s) == 32 else None


def saga_pub():
    _signer()
    return _SK["pub"]


# --- Kennungen und Medien-Token -------------------------------------------------------------------------------------
_REFK = hashlib.sha256(("saga-horn-ref|" + SECRET).encode()).digest()


def _ref(*parts):
    return _b64u(hmac.new(_REFK, "|".join(parts).encode(), hashlib.sha256).digest()[:16])


def call_ref(cid, node_id):
    return _ref("call", cid, node_id)


def peer_ref(cid, node_id, dev):
    return _ref("peer", cid, node_id, dev)


def leg_ref(cid, node_id, dev, role):
    return _ref("leg", cid, node_id, dev, role)


def token(node, cid, dev, role, udp_ok=False, dev_class="desktop", pub=None, max_kbit=MAX_KBIT, now=None):
    """Medien-Token fuer ein Bein (role audio | video | both) dieses Geraets auf dem Knoten."""
    body = {"v": 1, "node": node["name"], "call_ref": call_ref(cid, node["id"]), "peer_ref": peer_ref(cid, node["id"], dev),
            "leg_ref": leg_ref(cid, node["id"], dev, role), "role": role, "pub": PUB[role] if pub is None else pub,
            "max_kbit": int(max_kbit), "udp_ok": bool(udp_ok), "dev_class": "mobile" if dev_class == "mobile" else "desktop",
            "exp": int(now or time.time()) + TOKEN_TTL, "jti": _b64u(secrets.token_bytes(12))}
    p = _b64u(json.dumps(body, separators=(",", ":")).encode())
    return "h1." + p + "." + _b64u(_signer().sign(("saga-horn-token/1." + p).encode()))


# --- Knotenregister -------------------------------------------------------------------------------------------------
N_COLS = "id, name, pub, builtin, url, udp, share, version, state, load, seen, v4, v6, port, cert, upd, drain, link_mbit"


def _nrow(r):
    return dict(zip([x.strip() for x in N_COLS.split(",")], r)) if r else None


def nodes(c):
    return [_nrow(r) for r in c.execute(f"SELECT {N_COLS} FROM horn_node WHERE removed IS NULL ORDER BY builtin DESC, name").fetchall()]


def node_by_id(c, nid):
    return _nrow(c.execute(f"SELECT {N_COLS} FROM horn_node WHERE id=? AND removed IS NULL", (nid,)).fetchone()) if nid else None


def util(n):
    """Auslastung = max(cpu, Upload / link_mbit, Beine / max_legs) aus der letzten Lastmeldung (§2.6)."""
    ld = (_LIVE.get(n["id"]) or {}).get("load") or n.get("load") or {}
    try:
        u = max(float(ld.get("cpu") or 0), float(ld.get("legs") or 0) / max(1, int(ld.get("max_legs") or 48)))
        if n.get("link_mbit"):
            u = max(u, float(ld.get("up") or 0) / max(1, int(n["link_mbit"])))
    except (TypeError, ValueError):
        u = 0.0
    return 1.0 if ld.get("state") in ("voll", "draining", "update", "fehler") or n.get("drain") else u


def cert_ok(n):
    """Gekoppelte Knoten brauchen ein Zertifikat (Browser pruefen es), "self" nur mit HORN_TLS_SELF (Tests)."""
    return bool(n.get("builtin")) or ((n.get("cert") or {}).get("state") in ("ok", "self"))


def ready(n, now=None):
    """Knoten nimmt neue Beine an: verbunden (Lastmeldung juenger als READY_S), Zustand ok, nicht voll, nicht im Leeren,
    passende Version, Zertifikat da; der eingebaute nur mit dem Admin-Schalter."""
    now = now or time.time()
    live = _LIVE.get(n["id"])
    seen = max(n.get("seen") or 0, (live or {}).get("at") or 0)
    if n.get("builtin") and not builtin_on():
        return False
    return (n["state"] == "ok" and now - seen < READY_S and util(n) < FULL and bool(n.get("url")) and not n.get("drain")
            and version_ok(n.get("version")) and cert_ok(n))


def ready_nodes(c, now=None):
    """Bereite Knoten, beste zuerst (kleine Auslastung; ab PENALTY Abzug)."""
    ns = [n for n in nodes(c) if ready(n, now)]
    ns.sort(key=lambda n: (util(n) + (0.5 if util(n) >= PENALTY else 0), not n["builtin"], n["name"]))
    return ns


def any_ready(c):
    return horn_on() and bool(ready_nodes(c))


def call_node(c, cid):
    """Knoten, auf dem der Anruf laeuft (die meisten aktiven Endpunkte), oder None."""
    r = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND state='active' AND node IS NOT NULL GROUP BY node "
                  "ORDER BY COUNT(*) DESC, node LIMIT 1", (cid,)).fetchone()
    return r[0] if r else None


def call_node_ready(c, cid, ns=None):
    """Bereiter Knoten des Anrufs mit den meisten Endpunkten - None, wenn keiner bereit ist (Leeren, Entfernen, weg).
    Beim Umzug folgen so alle dem ersten, der schon auf dem neuen Knoten ist."""
    rows = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND state='active' AND node IS NOT NULL GROUP BY node "
                     "ORDER BY COUNT(*) DESC, node", (cid,)).fetchall()
    if not rows:
        return None
    ok = {n["id"] for n in (ready_nodes(c) if ns is None else ns)}
    return next((r[0] for r in rows if r[0] in ok), None)


def note_full(c):
    """Ein Anruf wurde begrenzt, weil kein Knoten frei war (Erweitern-Hinweis im Admin-Bereich)."""
    try:
        c.execute("UPDATE horn_node SET stats=jsonb_set(COALESCE(stats, '{}'::jsonb), '{full}', to_jsonb(?::double precision)) "
                  "WHERE removed IS NULL", (time.time(),))
    except Exception as ex:  # noqa: BLE001 - nur ein Hinweis
        print(f"horn: note_full: {type(ex).__name__}", flush=True)


def limits(c, cid=None, node=None, udp=None):
    """(Video, nur Ton) Endpunkte je Anruf ueber Knoten: TCP 12/50, mit UDP am Knoten des Anrufs 25/100. Der Admin kann
    nur senken (calls.horn_video / calls.horn_audio)."""
    if udp is None:
        n = node or (node_by_id(c, call_node(c, cid)) if cid else None)
        udp = bool(n and n["udp"])
    v, a = LIMITS["udp" if udp else "tcp"]
    cf = auth.settings().get("calls") or {}
    try:
        v = max(2, min(v, int(cf.get("horn_video") or v)))
        a = max(2, min(a, int(cf.get("horn_audio") or a)))
    except (TypeError, ValueError):
        pass
    return v, a


def candidates(c, call, dev, role="member", dev_class="desktop", now=None):
    """Bis zu CANDIDATES Knoten fuer dieses Geraet, je mit eigenen Medien-Token (Ton- und Bild-Leitung fuer TCP-Wege,
    dazu 'both' fuer den UDP-Weg). Laeuft der Anruf schon auf einem Knoten, kommt dieser zuerst (ohne Kaskade: nur er).
    Begleitgeraet: ohne Ton senden."""
    now = now or time.time()
    ns = ready_nodes(c, now)
    cur = call_node_ready(c, call["id"], ns)              # Knoten im Leeren/entfernt: neuer Knoten (Umzug, P5 schaltet)
    if cur:
        ns = [n for n in ns if n["id"] == cur] if not CASCADE else sorted(ns, key=lambda n: n["id"] != cur)
    out = []
    for n in ns[:CANDIDATES]:
        udp = bool(n["udp"])
        toks = {}
        for r in ("audio", "video") + (("both",) if udp else ()):
            pub = [x for x in PUB[r] if not (role == "companion" and x == "a")]
            toks[r] = token(n, call["id"], dev, r, udp, dev_class, pub, now=now)
        out.append({"node": n["name"], "url": n["url"], "udp": udp, "here": n["id"] == cur, "load": round(util(n), 3),
                    "peer": peer_ref(call["id"], n["id"], dev), "tokens": toks, "exp": int(now) + TOKEN_TTL})
    return out


def node_named(c, name):
    r = c.execute(f"SELECT {N_COLS} FROM horn_node WHERE name=? AND removed IS NULL", (name,)).fetchone()
    return _nrow(r)


def set_leg_node(c, cid, dev, node_id):
    c.execute("UPDATE chat_call_leg SET node=? WHERE call_id=? AND device=? AND state='active'", (node_id, cid, dev))


def leg_node(c, cid, dev):
    r = c.execute("SELECT node FROM chat_call_leg WHERE call_id=? AND device=?", (cid, dev)).fetchone()
    return r[0] if r else None


# --- Befehle an Knoten (nach dem Commit) ----------------------------------------------------------------------------
def _later(c, fn):
    after = getattr(c, "after_commit", None)
    after(fn) if after is not None else fn()


def send(node_id, msg):
    """Nachricht an einen verbundenen Knoten (aus jedem Thread). Ohne Verbindung in diesem Prozess: verworfen - der
    Knoten raeumt leere Raeume selbst ab und gleicht beim Verbinden ab (welcome.rooms)."""
    k = _CONNS.get(node_id)
    if k is None:
        return False
    try:
        k.loop.call_soon_threadsafe(k.q.put_nowait, msg)
    except RuntimeError:
        return False
    return True


def room_open(c, cid, node_id, max_peers=None):
    _later(c, lambda: send(node_id, {"t": "open", "call_ref": call_ref(cid, node_id), **({"max_peers": max_peers} if max_peers else {})}))


def room_close(c, cid):
    """Anruf vorbei: Raum auf allen Knoten des Anrufs schliessen."""
    ids = [r[0] for r in c.execute("SELECT DISTINCT node FROM chat_call_leg WHERE call_id=? AND node IS NOT NULL", (cid,)).fetchall()]
    for nid in ids:
        _later(c, lambda nid=nid: send(nid, {"t": "close", "call_ref": call_ref(cid, nid)}))


def kick(c, cid, dev, node_id):
    """Endpunkt hat den Anruf verlassen bzw. wurde entfernt: Beine am Knoten sofort beenden (keine neuen mit alten Token)."""
    if node_id:
        _later(c, lambda: send(node_id, {"t": "kick", "call_ref": call_ref(cid, node_id), "peer_ref": peer_ref(cid, node_id, dev)}))


def _active_rooms(node_id):
    with _db() as c:
        rows = c.execute("SELECT DISTINCT l.call_id FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.node=? "
                         "AND l.state='active' AND k.state<>'ended'", (node_id,)).fetchall()
    return [call_ref(r[0], node_id) for r in rows]


# --- Steuerkanal horn-ctl/1 -----------------------------------------------------------------------------------------
class _Conn:
    def __init__(self, ws, node_id):
        self.ws, self.node_id = ws, node_id
        self.loop = asyncio.get_running_loop()
        self.q = asyncio.Queue(maxsize=10000)
        self.n = 0
        self.builtin, self.name, self.close_code = True, "", 0


def _origin():
    u = urlparse(BASE_URL)
    return f"{u.scheme}://{u.netloc}"


def _verify_hello(h, nonce):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = base64.b64decode(h.get("pub") or "", validate=True)
        sig = base64.b64decode(h.get("sig") or "", validate=True)
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, (PROTO + " hello\n" + nonce).encode())
    except (ValueError, InvalidSignature):
        return False
    return len(pub) == 32


def _ip(v, fam):
    import ipaddress
    try:
        a = ipaddress.ip_address(str(v or "").strip())
    except ValueError:
        return ""
    return str(a) if a.version == fam and not (a.is_multicast or a.is_unspecified) else ""


def _clean_hello(h):
    name = str(h.get("name") or "").strip().lower()[:253]
    url = str(h.get("url") or "").strip().rstrip("/")[:300]
    pu = urlparse(url)
    if not name or pu.scheme != "https" or not pu.netloc:
        return None
    cert = h.get("cert") if isinstance(h.get("cert"), dict) else {}
    upd = h.get("upd") if isinstance(h.get("upd"), dict) else {}
    return {"name": name, "url": url, "pub": str(h.get("pub")), "udp": bool(h.get("udp")), "version": str(h.get("version") or "")[:40],
            "max_legs": int(h.get("max_legs") or 0) if str(h.get("max_legs") or "0").isdigit() else 0,
            "v4": _ip(h.get("v4"), 4), "v6": _ip(h.get("v6"), 6), "cert": _clean_cert(cert), "upd": _clean_upd(upd)}


def _clean_cert(d):
    st = str(d.get("state") or "")[:16]
    out = {"mode": str(d.get("mode") or "")[:16], "state": st}
    try:
        if d.get("not_after"):
            out["not_after"] = int(d["not_after"])
    except (TypeError, ValueError):
        pass
    if d.get("err"):
        out["err"] = str(d["err"])[:300]
    return out


def _clean_upd(d):
    out = {"state": str(d.get("state") or "")[:16], "want": str(d.get("want") or "")[:40], "have": str(d.get("have") or "")[:40]}
    if str(d.get("code") or "") in ("nokey", "nowatch", "sig", "download", "wait24"):
        out["code"] = d["code"]
    if d.get("err"):
        out["err"] = str(d["err"])[:300]
    return out


def register_builtin(h, now=None):
    """Eingebauten Knoten eintragen bzw. aktualisieren (genau einer; Name und Schluessel duerfen sich aendern)."""
    now = now or time.time()
    with _db() as c, c:
        r = c.execute("SELECT id FROM horn_node WHERE builtin=1 AND removed IS NULL").fetchone()
        clash = c.execute("SELECT id FROM horn_node WHERE name=? AND builtin=0 AND removed IS NULL", (h["name"],)).fetchone()
        if clash:
            return None
        if r:
            nid = r[0]
            c.execute("UPDATE horn_node SET name=?, pub=?, url=?, udp=?, version=?, state='ok', seen=?, cert=CAST(? AS JSONB) WHERE id=?",
                      (h["name"], h["pub"], h["url"], h["udp"], h["version"], now, json.dumps(h["cert"]), nid))
        else:
            nid = secrets.token_hex(8)
            c.execute("DELETE FROM horn_node WHERE name=? AND removed IS NOT NULL", (h["name"],))
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, udp, version, state, created, seen, cert) "
                      "VALUES (?,?,?,1,?,?,?,'ok',?,?,CAST(? AS JSONB))",
                      (nid, h["name"], h["pub"], h["url"], h["udp"], h["version"], now, now, json.dumps(h["cert"])))
    print(f"horn: eingebauter Knoten {h['name']} verbunden ({h['url']}, UDP {'ja' if h['udp'] else 'nein'})", flush=True)
    return nid


def register_paired(h, now=None):
    """Gekoppelten Knoten am Schluessel erkennen -> (node_id, None) oder (None, Schliesscode). Gesperrt: 4410."""
    now = now or time.time()
    with _db() as c, c:
        if c.execute("SELECT 1 FROM horn_ban WHERE pub=?", (h["pub"],)).fetchone():
            return None, 4410
        r = c.execute("SELECT id, name, v4, v6 FROM horn_node WHERE pub=? AND builtin=0 AND removed IS NULL", (h["pub"],)).fetchone()
        if not r:
            return None, 4401
        if r[1] != h["name"]:
            return None, 4409
        c.execute("UPDATE horn_node SET url=?, udp=?, version=?, state='ok', seen=?, cert=CAST(? AS JSONB), upd=CAST(? AS JSONB) "
                  "WHERE id=?", (h["url"], h["udp"], h["version"], now, json.dumps(h["cert"]), json.dumps(h["upd"]), r[0]))
    if (h["v4"] or h["v6"]) and (h["v4"], h["v6"]) != (r[2] or "", r[3] or "") and HOOKS.get("addr"):
        threading.Thread(target=HOOKS["addr"], args=(r[0], h["v4"], h["v6"]), daemon=True).start()
    print(f"horn: Knoten {h['name']} verbunden ({h['url']}, Version {h['version']}, UDP {'ja' if h['udp'] else 'nein'})", flush=True)
    return r[0], None


def _gone(nid):
    with _db() as c, c:
        c.execute("UPDATE horn_node SET state='getrennt' WHERE id=? AND removed IS NULL AND state<>'getrennt'", (nid,))


def _day(now):
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _store_load(nid, ld, now, stats=None):
    with _db() as c, c:
        st = "ok" if ld.get("state") in (None, "ok", "voll") else str(ld.get("state"))[:16]
        if stats is not None:
            c.execute("UPDATE horn_node SET load=CAST(? AS JSONB), seen=?, state=?, stats=CAST(? AS JSONB) WHERE id=? AND removed IS NULL",
                      (json.dumps(ld), now, st, json.dumps(stats), nid))
        else:
            c.execute("UPDATE horn_node SET load=CAST(? AS JSONB), seen=?, state=? WHERE id=? AND removed IS NULL",
                      (json.dumps(ld), now, st, nid))


def _set_json(nid, col, v):
    with _db() as c, c:
        c.execute(f"UPDATE horn_node SET {col}=CAST(? AS JSONB) WHERE id=?", (json.dumps(v), nid))


def _load_stats(nid):
    with _db() as c:
        r = c.execute("SELECT stats FROM horn_node WHERE id=?", (nid,)).fetchone()
    return (r[0] if r and isinstance(r[0], dict) else {}) or {}


def _stats_add(st, ld, n, now):
    """Auslastung je Tag zaehlen (busy = ueber PENALTY), hoechstens 8 Tage. -> st (geaendert)."""
    d = st.setdefault("d", {})
    k = _day(now)
    v = d.get(k) or [0, 0]
    v = [int(v[0]) + (1 if util(dict(n, load=ld)) >= PENALTY else 0), int(v[1]) + 1]
    d[k] = v
    for old in sorted(d)[:-8]:
        d.pop(old, None)
    return st


_LOAD_KEYS = ("cpu", "mem", "up", "down", "legs", "calls", "peers", "udp_share", "fwd_p95_us", "drops", "down_kbit", "max_legs", "state")


def solo():
    """Genau ein gekoppelter oder eingebauter Knoten bereit bzw. verbunden? (Update: auf anruffreie Zeit warten)"""
    with _db() as c:
        ns = [n for n in nodes(c) if n["id"] in _CONNS and not n.get("drain")]
    return len(ns) <= 1


def broadcast_peers():
    """Nach jeder Verbindungsaenderung: allen gekoppelten Knoten sagen, ob sie allein sind."""
    s1 = solo()
    for nid, k in list(_CONNS.items()):
        if not k.builtin:
            send(nid, {"t": "peers", "solo": s1})


def emit_move(c, node_id, why):
    """Anrufe auf diesem Knoten: allen Personen mit Endpunkt dort sagen, dass sie umziehen sollen (Leeren, Entfernen,
    Update). Die Geraete holen mit POST /api/calls/{id}/horn neue Kandidaten (Umschalten: Web-Client P5, Apps)."""
    rows = c.execute("SELECT l.call_id, k.chat_id, l.who FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id "
                     "WHERE l.node=? AND l.state='active' AND k.state<>'ended'", (node_id,)).fetchall()
    by = {}
    for cid, chat_id, who in rows:
        by.setdefault((cid, chat_id), set()).add(who)
    name = (c.execute("SELECT name FROM horn_node WHERE id=?", (node_id,)).fetchone() or [""])[0]
    for (cid, chat_id), uids in by.items():
        bus.emit_after(c, "direct", {"uids": sorted(u for u in uids if u and not u.startswith("@")), "typ": "call.horn_move",
                                     "data": {"call_id": cid, "chat_id": chat_id, "node": name, "why": why}})
    return len(by)


def _move_later(nid, why):
    try:
        with _db() as c, c:
            n = emit_move(c, nid, why)
        if n:
            print(f"horn: Knoten {nid}: {n} Anruf(e) ziehen um ({why})", flush=True)
    except Exception as ex:  # noqa: BLE001
        print(f"horn: Umzug {nid}: {type(ex).__name__}: {ex}", flush=True)


def _acme_mode():
    try:
        from saga import cloudflare
        return "dns01" if cloudflare.configured() else "alpn"
    except Exception:  # noqa: BLE001
        return "alpn"


def _node_drain(nid):
    with _db() as c:
        r = c.execute("SELECT drain FROM horn_node WHERE id=?", (nid,)).fetchone()
    return bool(r and r[0])


@router.websocket("/api/horn/ctl")
async def ctl(ws: WebSocket):
    """Steuerverbindung eines Knotens: eingebauter Knoten (X-Saga-Horn-Key von saga-media, nur mit Feature-Flag) oder
    gekoppelter Knoten (registrierter Knotenschluessel, P6)."""
    key = ws.headers.get("x-saga-horn-key", "")
    builtin = bool(key)
    if builtin and (not HORN_KEY or not hmac.compare_digest(key.encode(), HORN_KEY.encode()) or not horn_on()):
        await ws.close(code=4403)
        return
    await ws.accept()
    nonce = _b64u(secrets.token_bytes(24))
    try:
        await ws.send_json({"t": "challenge", "nonce": nonce, "proto": PROTO})
        h = await asyncio.wait_for(ws.receive_json(), 10)
    except Exception:  # noqa: BLE001 - Verbindung weg / kein JSON
        await _close(ws, 4400)
        return
    hh = _clean_hello(h) if isinstance(h, dict) and h.get("t") == "hello" and h.get("proto") == PROTO else None
    if hh is None or not _verify_hello(h, nonce):
        await _close(ws, 4401)
        return
    if builtin:
        nid, code = await asyncio.to_thread(register_builtin, hh), 4409
    else:
        nid, code = await asyncio.to_thread(register_paired, hh)
    if nid is None:
        await _close(ws, code)
        return
    pub = await asyncio.to_thread(saga_pub)
    rooms = await asyncio.to_thread(_active_rooms, nid)
    k = _Conn(ws, nid)
    k.builtin, k.name = builtin, hh["name"]
    old = _CONNS.get(nid)
    _CONNS[nid] = k
    if old is not None:
        old.q.put_nowait(None)
    stats = {} if builtin else await asyncio.to_thread(_load_stats, nid)
    _LIVE[nid] = {"load": {"max_legs": hh["max_legs"], "legs": 0, "state": "ok"}, "at": time.time(), "db": time.time(), "stats": stats,
                  "drain_ev": False}
    welcome = {"t": "welcome", "n": 0, "node_id": nid, "saga_pub": pub, "origin": _origin(), "rooms": rooms, "proto": PROTO}
    if not builtin:
        drain = await asyncio.to_thread(_node_drain, nid)
        welcome.update(want=VERSION, solo=await asyncio.to_thread(solo), drain=drain, cert=await asyncio.to_thread(_acme_mode))
    await ws.send_json(welcome)
    if not builtin:
        await asyncio.to_thread(broadcast_peers)

    async def writer():
        while True:
            m = await k.q.get()
            if m is None:
                return
            k.n += 1
            m["n"] = k.n
            await ws.send_json(m)

    async def pinger():
        while True:
            await asyncio.sleep(20)
            k.q.put_nowait({"t": "ping"})

    wt, pt = asyncio.create_task(writer()), asyncio.create_task(pinger())
    last = 0
    acme_n = []
    try:
        while not wt.done():
            m = await asyncio.wait_for(ws.receive_json(), 60)
            if not isinstance(m, dict) or m.get("n") != last + 1:
                break                                  # Luecke oder Wiederholung: Verbindung zu (der Knoten verbindet neu)
            last = m["n"]
            t = m.get("t")
            if t == "load" and isinstance(m.get("load"), dict):
                ld = {x: m["load"].get(x) for x in _LOAD_KEYS if x in m["load"]}
                now = time.time()
                live = _LIVE.setdefault(nid, {"db": 0})
                live.update(load=ld, at=now)
                if ld.get("state") == "draining" and not live.get("drain_ev"):
                    live["drain_ev"] = True
                    asyncio.get_running_loop().run_in_executor(None, _move_later, nid, "update" if not builtin else "drain")
                elif ld.get("state") != "draining":
                    live["drain_ev"] = False
                if now - live.get("db", 0) >= SEEN_EVERY:
                    live["db"] = now
                    st = None
                    if not builtin:
                        st = _stats_add(live.setdefault("stats", {}), ld, {"id": nid, "link_mbit": None}, now)
                    await asyncio.to_thread(_store_load, nid, ld, now, st)
            elif t == "closed":
                pass                                   # Knoten hat einen leeren Raum selbst geschlossen
            elif builtin:
                continue
            elif t == "cert":
                await asyncio.to_thread(_set_json, nid, "cert", _clean_cert(m))
            elif t == "update":
                await asyncio.to_thread(_set_json, nid, "upd", _clean_upd(m))
            elif t == "addr" and HOOKS.get("addr"):
                v4, v6 = _ip(m.get("v4"), 4), _ip(m.get("v6"), 6)
                if v4 or v6:
                    asyncio.get_running_loop().run_in_executor(None, HOOKS["addr"], nid, v4, v6)
            elif t == "acme_txt":
                now = time.time()
                acme_n[:] = [x for x in acme_n if now - x < 3600] + [now]
                rid = str(m.get("rid") or "")[:20]
                if len(acme_n) > 20 or not HOOKS.get("acme_txt"):
                    ok, err = False, "rate" if len(acme_n) > 20 else "nicht verfügbar"
                else:
                    ok, err = await asyncio.to_thread(HOOKS["acme_txt"], nid, str(m.get("op") or ""), str(m.get("name") or "")[:300],
                                                      str(m.get("value") or "")[:100])
                k.q.put_nowait({"t": "acme_txt", "rid": rid, "ok": ok, **({"err": err} if err else {})})
    except Exception:  # noqa: BLE001 - Verbindung weg, Zeitueberschreitung, kein JSON
        pass
    finally:
        wt.cancel()
        pt.cancel()
        if _CONNS.get(nid) is k:
            _CONNS.pop(nid, None)
            _LIVE.pop(nid, None)
            await asyncio.to_thread(_gone, nid)
            print(f"horn: Knoten {hh['name']} getrennt", flush=True)
            if not builtin:
                await asyncio.to_thread(broadcast_peers)
        await _close(ws, k.close_code or 1000)


def close_conn(nid, code=4410, delay=0.5):
    """Steuerverbindung eines Knotens beenden (Entfernen); vorher eingereihte Nachrichten gehen noch hinaus."""
    k = _CONNS.get(nid)
    if k is None:
        return False
    k.close_code = code

    def later():
        k.q.put_nowait(None)
        asyncio.ensure_future(_close(k.ws, code))
    try:
        k.loop.call_soon_threadsafe(k.loop.call_later, delay, later)
    except RuntimeError:
        return False
    return True


def sign_remove(node_id, name):
    """Signiertes "remove" (der Knoten loescht dann seine Schluessel)."""
    return base64.b64encode(_signer().sign(f"saga-horn-remove/1\n{node_id}\n{name}".encode())).decode()


async def _close(ws, code):
    try:
        await ws.close(code=code)
    except Exception:  # noqa: BLE001 - schon zu
        pass


@router.get("/internal/horn/conf")
def internal_conf(request: Request):
    """Fuer saga-media: soll der eingebaute Knoten laufen? (Feature-Flag calls.horn und Admin-Schalter horn_builtin)"""
    if not MEDIA_KEY or not hmac.compare_digest(request.headers.get("x-saga-media-key", "").encode(), MEDIA_KEY.encode()):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"on": horn_on() and builtin_on()}, headers={"Cache-Control": "no-store"})


def status():
    """Diagnose (Tests, spaeter Admin-Seite P8): Knoten mit Bereitschaft und Last."""
    with _db() as c:
        return [{"id": n["id"], "name": n["name"], "builtin": n["builtin"], "url": n["url"], "udp": n["udp"], "state": n["state"],
                 "ready": ready(n), "util": round(util(n), 3), "connected": n["id"] in _CONNS, "drain": bool(n.get("drain")),
                 "version": n["version"], "cert": n["cert"], "upd": n["upd"],
                 "load": (_LIVE.get(n["id"]) or {}).get("load") or n["load"]} for n in nodes(c)]


# --- CSP: Geraete sprechen die Knoten direkt an (Leg-Aufbau, spaeter Probe) ------------------------------------------
_CSP = {"t": 0.0, "v": ""}


def _csp_extra():
    now = time.time()
    if now - _CSP["t"] < 10:
        return _CSP["v"]
    v = ""
    try:
        if horn_on():
            with _db() as c:
                us = sorted({n["url"] for n in nodes(c) if n.get("url")})
            v = "".join(f" {u} {'wss' + u[5:]}" for u in us if u.startswith("https://"))
    except Exception:  # noqa: BLE001 - Datenbank kurz weg: ohne Zusatz
        v = _CSP["v"]
    _CSP.update(t=now, v=v)
    return v


class NodeCsp:
    """ASGI: ergaenzt connect-src der HTML-Seiten um die Adressen der Knoten (nur mit Feature-Flag; sonst unveraendert)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path", "").startswith(("/api/", "/static/", "/fed/", "/internal/")):
            return await self.app(scope, receive, send)

        async def snd(m):
            if m["type"] == "http.response.start":
                hs = m.get("headers") or []
                for i, (k, v) in enumerate(hs):
                    if k.lower() == b"content-security-policy" and b"connect-src 'self'" in v:
                        extra = await asyncio.to_thread(_csp_extra) if time.time() - _CSP["t"] >= 10 else _CSP["v"]
                        if extra:
                            hs = list(hs)
                            hs[i] = (k, v.replace(b"connect-src 'self'", b"connect-src 'self'" + extra.encode(), 1))
                            m = dict(m, headers=hs)
                        break
            await send(m)
        return await self.app(scope, receive, snd)
