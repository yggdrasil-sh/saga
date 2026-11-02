"""Live mitschreiben ueber Servergrenzen (2.5, P3-E7, Faehigkeit collab1): Stufe 2 fuer Seiten anderer Saga-Server.

Protokoll: docs/APP-API-FED.md "collab1", fuer Clients docs/APP-API-COLLAB.md (fremde Seite = negative ID, sonst gleich).

Heim (Server der Person): Der Web-Client spricht wie lokal WS /api/collab. Je (Konto, fremde Seite) haelt das Heim eine
Weiterleitung (_Relay) mit einer Kopie des Y.Doc (gleicher Aufbau wie beim Eigentuemer, Werte in der Form des Heims:
negative Seiten-IDs, Dateien ueber /api/fed/files, eigene Handles). Updates/Awareness der Clients gehen gesammelt
(hoechstens 50 ms) als signierte Buendel POST /fed/v2/notes/{id}/collab an den Eigentuemer; dessen Antwort bestaetigt
bzw. lehnt je Update ab. Fremde Aenderungen kommen ueber /fed/v2/live (Eintrag type "collab"). Alle SYNC_EVERY s und nach
Fehlern gleicht das Heim per Zustandsvektor ab (sync1) - verlorene/verworfene Buendel schliessen sich so (CRDT idempotent).

Eigentuemer: Je Weiterleitung ein Abo im Raum (saga.collab._Sub mit _RConn statt WebSocket) - Rechte, Validierung
(clean_blocks + pages.body_checks), Materialisierung, JSON-Schreiber, Neupruefung (Freigaben, Gruppen, Sperre) laufen
genau wie lokal. Rechte zusaetzlich je Buendel; Abos ohne Buendel laufen nach WATCH_TTL s ab.

Werte: Seiten-IDs, Datei-Adressen, Erwaehnungen, Nextcloud-Autoren rechnen beide Seiten um, ohne den Aufbau der Updates
zu aendern (saga.yupd) - Uhren, Ursprungs-IDs und Zustandsvektoren sind auf beiden Servern gleich. Titel fremder Seiten
stehen nie im Zustand (wie lokal); zusaetzlich leert der Eigentuemer jeden Verweistext in Updates an das Heim.

Faehigkeit: "collab1-<SCHEMA_HASH>" (gleiches Dokumentmodell, Schema-Version und -Hash) - gemeldet nur, solange der
Admin-Schalter notes.live an ist. Nur wenn beide Seiten sie melden (und live), gibt es Stufe 2; sonst Stufe 1 (merge1)."""
import asyncio
import base64
import json
import re
import secrets
import threading
import time

from fastapi import APIRouter, Request
from pycrdt import get_update

from db import connect as _db
from saga import collab, collab_model as model, nextcloud, people, sharing, yupd
from saga import federation as F
from saga import fed_pages as FP
from saga.core import E, HANDLE_RE, local_ref, resolve_ref

router = APIRouter()

CAP = "collab1-" + model.SCHEMA_HASH
SYNC_EVERY = max(1.0, float(E.get("FED_COLLAB_SYNC", "10")))       # Abgleich je Weiterleitung (s)
GATHER = 0.05                                                       # Heim sammelt so lange vor dem Senden
WATCH_TTL = max(5.0, float(E.get("FED_COLLAB_TTL", "35")))         # Abo beim Eigentuemer ohne Buendel
BUNDLE_MAX = collab.MSG_MAX * 4 // 3 + 64 * 1024                    # Buendel (JSON mit base64) hoechstens
OPS_MAX = 64                                                        # Eintraege je Buendel
BUNDLE_RATE = 25                                                    # Buendel je Sekunde je Seite und Person (Eigentuemer)
VERIFY_RATE = 6000                                                  # signierte Buendel je Minute und Instanz (eigener Topf)
PEER_MAX = max(1, int(E.get("FED_COLLAB_PEER_MAX", "200")))       # Abos je Gegenstelle beim Eigentuemer
PRINCIPAL_MAX = 10                                                  # Abos je fremder Person beim Eigentuemer
RELAY_MAX = max(1, int(E.get("FED_COLLAB_RELAYS", "200")))        # Weiterleitungen je Heim
RELAY_USER_MAX = 10                                                 # Weiterleitungen je Konto
OUT_MAX = 2000                                                      # wartende Rahmen je Gegenstelle (Eigentuemer)
FRAMES_MAX = 20                                                     # Rahmen je Live-Eintrag
TEST_DROP = E.get("FED_COLLAB_TEST_DROP", "")                       # nur Tests: Datei - existiert sie, gehen Rueckmeldungen verloren
_W_RE = re.compile(r"[0-9a-f]{32}")
_LINK_TXT = re.compile(r'(<a href="/notes#-?[0-9]{1,19}">)((?:(?!<a[\s>]|</a>).)+)(</a>)', re.S)   # bis zum naechsten <a: linear

_S = {"stats": {"bundles": 0, "rejected": 0, "dropped": 0, "relays": 0}}


def _b64(b):
    return base64.b64encode(bytes(b)).decode("ascii")


def _unb64(s, limit=collab.MSG_MAX):
    if not isinstance(s, str) or len(s) > limit * 4 // 3 + 8:
        raise ValueError("b64")
    return base64.b64decode(s.encode("ascii"), validate=True)


def enabled_cap():
    """Eigene Faehigkeit collab1-<schema> melden? (Admin-Schalter notes.live)"""
    return collab.enabled()


def usable(p):
    """Live mitschreiben mit Instanz p moeglich (beide Seiten collab1 mit gleichem Schema, live, notes.live hier an)."""
    return bool(p) and F.feature(p, CAP) and F.feature(p, "live")


def rehello_all():
    """notes.live umgeschaltet: Faehigkeiten mit allen verbundenen Instanzen neu austauschen (collab1 an/aus)."""
    if not F.enabled():
        return
    with _db() as c:
        ids = [p["id"] for p in F.peers(c) if p["status"] == "active"]
    for pid in ids:
        F.peer_submit(pid, F.hello, pid)


def _num_id(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float):
        if not v.is_integer():
            return None
        v = int(v)
    return v


def _walk(v, fs, depth=0):
    """Alle Texte in v (JSON-Wert) durch fs schicken."""
    if depth > 40:
        raise yupd.Reject("tief")
    if isinstance(v, str):
        return fs(v)
    if isinstance(v, list):
        return [_walk(x, fs, depth + 1) for x in v]
    if isinstance(v, dict):
        return {k: _walk(x, fs, depth + 1) for k, x in v.items()}
    return v


# =====================================================================================================================
# Werte umrechnen
# =====================================================================================================================
class _OwnerMap:
    """Eigentuemer: eigene Form <-> Form fuer die Gegenstelle (Erwaehnungen als Prinzipal, Nextcloud-Autor als Platzhalter,
    Verweistexte leer). back: Rueckweg fuer Werte, die nur der Eigentuemer umkehren kann."""

    def __init__(self, peer):
        self.peer = peer
        self.back = {}

    def out(self, c):
        def fn(v, key):
            tag = key[0] if key else None
            if key == ("mention", "u") and isinstance(v, str) and HANDLE_RE.fullmatch(v):
                u = resolve_ref(c, v)
                ext = F._mention_ext(c, u, self.peer) if u else None
                if ext:
                    self._remember(ext, v)
                    return ext
                return v
            if tag == "nextcloud" and isinstance(v, dict) and isinstance(v.get("author"), str) and v["author"]:
                a = v["author"]
                real = resolve_ref(c, a) if HANDLE_RE.fullmatch(a) else a
                nv = dict(v, author=nextcloud.pseudo(real) if real else "")
                self._remember(nv, v)
                v = nv
            if isinstance(v, (str, dict, list)):
                return _walk(v, lambda s: _LINK_TXT.sub(lambda m: m.group(1) + m.group(3), s) if "/notes#" in s else s)
            return v
        return fn

    def _remember(self, ext, orig):
        k = json.dumps(ext, sort_keys=True, ensure_ascii=False)
        self.back[k] = orig
        if len(self.back) > 20000:
            for x in list(self.back)[:10000]:
                self.back.pop(x, None)

    def into(self, c):
        def fn(v, key):
            if isinstance(v, (str, dict)):
                k = json.dumps(v, sort_keys=True, ensure_ascii=False)
                if k in self.back:
                    return self.back[k]
            if key == ("mention", "u") and isinstance(v, str) and v.startswith("@"):
                if people.FED_LOCAL is not None:
                    mine, u = people.FED_LOCAL(c, v)
                    if mine:
                        return local_ref(u) if u else v
                return people.canon_remote(c, v)
            return v
        return fn


class _HomeMap:
    """Heim: Form des Eigentuemers <-> eigene Form (wie GET/PATCH fremder Seiten in saga.federation)."""

    def __init__(self, pid, rid, home_peer):
        self.pid, self.rid, self.peer = pid, rid, home_peer
        self.pref = F.ref(pid, rid)

    def into(self, c):
        pid, pref = self.pid, self.pref

        def conv(v):
            n = _num_id(v)
            return F.ref(pid, n) if F._ok_rid(n) else None

        def fn(v, key):
            tag = key[0] if key else None
            if key == ("page", "id"):
                n = _num_id(v)
                return F.ref(pid, n) if F._ok_rid(n) else 0
            if key == ("mention", "u") and isinstance(v, str):
                mm = re.fullmatch(r"@([^@]+)@(.+)", v)
                if mm and mm.group(2) == F.SELF_HOST:
                    u = F._local_user_by_name(c, mm.group(1))[0]
                    return local_ref(u) if u else v
                return people.canon_remote(c, v) if mm else v
            if tag == "database" and isinstance(v, dict) and "db" in v and v["db"] is not None:
                v = dict(v, db=conv(v["db"]))
            if tag == "buttons" and isinstance(v, dict) and isinstance(v.get("buttons"), list):
                v = dict(v, buttons=[dict(bt, **{k: conv(bt[k]) for k in ("target", "tpl") if bt.get(k) is not None})
                                     if isinstance(bt, dict) else bt for bt in v["buttons"]])
            if isinstance(v, (str, dict, list)):
                return _walk(v, lambda s: F._rw_in(s, pid, pref))
            return v
        return fn

    def out(self, c):
        pid = self.pid

        def conv(v):
            n = _num_id(v)
            if n is not None and n < 0 and F.unref(n)[0] == pid:
                return F.unref(n)[1]
            return None

        def fn(v, key):
            tag = key[0] if key else None
            if key == ("page", "id"):
                n = _num_id(v)
                if n is not None and n < 0 and F.unref(n)[0] == pid:
                    return F.unref(n)[1]
                if n == 0:
                    return 0
                raise yupd.Reject("page")            # Verweis auf eine Seite hier/eines dritten Servers: dort nicht moeglich
            if key == ("mention", "u") and isinstance(v, str):
                if HANDLE_RE.fullmatch(v):
                    u = resolve_ref(c, v)
                    pub = F.fed_name(c, u, self.peer) if u else None
                    if not pub:
                        raise yupd.Reject("mention")
                    return f"@{pub}@{F.SELF_HOST}"
                return v
            if tag == "database" and isinstance(v, dict) and "db" in v and v["db"] is not None:
                v = dict(v, db=conv(v["db"]))
            if tag == "buttons" and isinstance(v, dict) and isinstance(v.get("buttons"), list):
                v = dict(v, buttons=[dict(bt, **{k: conv(bt[k]) for k in ("target", "tpl") if bt.get(k) is not None})
                                     if isinstance(bt, dict) else bt for bt in v["buttons"]])
            if isinstance(v, (str, dict, list)):
                return _walk(v, lambda s: F._rw_out(s, pid))
            return v
        return fn


def _xform(upd, fn, keys, state=None):
    """Update umrechnen. Unbekannter Schluessel: einmal mit dem ganzen Zustand (state()) nachlernen. -> Bytes oder None
    (Luecke: Update haengt an Unbekanntem)."""
    out, open_n = yupd.transform(upd, fn, keys)
    if open_n and state is not None:
        yupd.key_scan(state(), keys)
        out, open_n = yupd.transform(upd, fn, keys)
    return None if open_n else out


# =====================================================================================================================
# Eigentuemer
# =====================================================================================================================
class _RConn:
    """Ersatz fuer die WebSocket-Verbindung eines Abos: Rahmen gehen gesammelt per /fed/v2/live an das Heim."""
    kind = "fed"

    def __init__(self, rs):
        self.rs, self.uid, self.groups = rs, rs.principal, frozenset(rs.groups)
        self.subs = {}
        self.closed = False

    def send(self, data):
        if self.closed:
            return
        if data[:1] == bytes([collab.T_BYE]):
            self.closed = True
            _R.pop(self.rs.w, None)
        _queue_out(self.rs, data)

    def kill(self, code):
        self.closed = True


class _RSub:
    """Abo einer fremden Person (Weiterleitung eines Heims) auf einer Seite hier."""

    def __init__(self, w, peer, principal, name, groups, nid):
        self.w, self.peer, self.principal, self.name, self.groups, self.nid = w, peer, principal, name, groups, nid
        self.map = _OwnerMap(peer)
        self.keys = {}
        self.lock = threading.Lock()
        self.exp = time.time() + WATCH_TTL
        self.sub = None
        self.conn = None


_R = {}                     # w -> _RSub
_OUT = {}                   # peer_id -> {"q": [(rsub, rahmen)], "run": bool}
_OL = threading.Lock()


def _queue_out(rs, data):
    pid = rs.peer["id"]
    with _OL:
        o = _OUT.setdefault(pid, {"q": [], "run": False})
        if len(o["q"]) >= OUT_MAX:
            o["q"].pop(0)                    # aeltester weg - der periodische Abgleich holt ihn nach
            _S["stats"]["dropped"] += 1
        o["q"].append((rs, data))
        if o["run"]:
            return
        o["run"] = True
    if F.peer_submit(pid, _drain, pid) is False:
        with _OL:
            o["run"] = False


def _drain(pid):
    """Im Express-Pool: wartende Rahmen einer Gegenstelle umrechnen und per /fed/v2/live zustellen (in Reihenfolge)."""
    try:
        while True:
            time.sleep(GATHER)
            with _OL:
                o = _OUT.get(pid)
                q, o["q"] = (o["q"], []) if o else ([], [])
                if not q:
                    if o:
                        o["run"] = False
                    return
            with _db() as c:
                p = F.peer_by(c, "id", pid)
                if not p or not F.feature(p, "live"):
                    continue
                items = []
                for rs, data in q:
                    try:
                        kind, nid, parts = collab.parse(data)
                        with rs.lock:
                            if kind in (collab.T_UPDATE, collab.T_SYNC2) and parts:
                                u = _xform(parts[0], rs.map.out(c), rs.keys, lambda n=nid: _room_state(n))
                                if u is None:
                                    continue
                                parts = [u] + parts[1:]
                        f = collab.frame(kind, nid, *parts)
                    except (ValueError, yupd.Reject):
                        continue
                    if items and items[-1]["w"] == rs.w and len(items[-1]["f"]) < FRAMES_MAX:
                        items[-1]["f"].append(_b64(f))
                    else:
                        items.append({"type": "collab", "w": rs.w, "note": rs.nid, "to": rs.name, "f": [_b64(f)]})
            if TEST_DROP and _test_drop():
                continue
            for k in range(0, len(items), FP.LIVE_MAX):
                try:
                    st, d = F.call_json(p, "POST", "/fed/v2/live", body={"items": items[k:k + FP.LIVE_MAX]},
                                        timeout=max(1.0, F.EXPRESS_TIMEOUT))
                except F.FedError:
                    break                     # verwerfbar: das Heim gleicht periodisch ab
                for w in (d.get("gone") or []) if st == 200 and isinstance(d, dict) else []:
                    if isinstance(w, str) and w in _R and _R[w].peer["id"] == pid:
                        _in_loop(_drop_rs, _R[w])
    except Exception as ex:  # noqa: BLE001
        print(f"fed_collab: Zustellen: {type(ex).__name__}: {ex}", flush=True)
        with _OL:
            if pid in _OUT:
                _OUT[pid]["run"] = False


def _room_state(nid):
    room = collab._rooms.get(nid)
    if room is None:
        return b"\x00\x00"
    with room.lock:
        return room.doc.get_update()


def _test_drop():
    import os
    return os.path.exists(TEST_DROP)


def _in_loop(fn, *a):
    loop = collab._S["loop"]
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(lambda: loop.create_task(fn(*a)) if asyncio.iscoroutinefunction(fn) else fn(*a))
    except RuntimeError:
        pass


async def _drop_rs(rs):
    _R.pop(rs.w, None)
    if rs.sub is not None:
        rs.conn.closed = True
        collab._drop_sub(rs.sub)


def _full(room, rs, sv=None):
    """Im Thread: Zustand (bzw. Unterschied zu sv) des Raums in der Form fuer das Heim."""
    with room.lock:
        try:
            u = room.doc.get_update(sv) if sv is not None else room.doc.get_update()
        except ValueError:
            u = room.doc.get_update()
    with _db() as c, rs.lock:
        out = _xform(u, rs.map.out(c), rs.keys, lambda: _room_state(room.nid))
    if out is None:                           # sollte nicht vorkommen (ganzer Zustand): ohne Werte-Umrechnung nicht senden
        raise ValueError("xform")
    return out


def _peer_count(pid, principal):
    n = m = 0
    for rs in _R.values():
        if rs.peer["id"] == pid:
            n += 1
            if rs.principal == principal:
                m += 1
    return n, m


@router.post("/fed/v2/notes/{nid}/collab")
async def owner_bundle(nid: int, request: Request):
    """Buendel einer Weiterleitung (Heim) fuer die Seite nid hier - siehe docs/APP-API-FED.md "collab1"."""
    inp, err = await F._verify(request, need_actor=True, rate=("#collab", VERIFY_RATE))
    if err is not None:
        return err
    if not 0 < nid < 2 ** 63:
        return F._signed(inp, 404, {"error": "not found"})
    if not usable(inp.peer):
        return F._signed(inp, 403, {"error": "collab1 disabled"})
    if len(inp.body or b"") > BUNDLE_MAX:
        return F._signed(inp, 413, {"error": "too large"})
    d = inp.data if isinstance(inp.data, dict) else {}
    w, ops = d.get("w"), d.get("ops")
    if not (isinstance(w, str) and _W_RE.fullmatch(w)) or not isinstance(ops, list) or not ops or len(ops) > OPS_MAX:
        return F._signed(inp, 400, {"error": "bad bundle"})
    pr = inp.principal
    if not F._rate.allow(f"cb:{inp.peer['id']}:{pr}:{nid}", BUNDLE_RATE, window=1.0):
        return F._signed(inp, 429, {"error": "too many"})
    _S["stats"]["bundles"] += 1
    tok = sharing.REMOTE_GROUPS.set({pr: frozenset(inp.groups)})
    try:
        out = await _bundle(inp, nid, w, ops)
    finally:
        sharing.REMOTE_GROUPS.reset(tok)
    return F._signed(inp, out.pop("_status", 200), out)


async def _bundle(inp, nid, w, ops):
    pr, peer = inp.principal, inp.peer
    rs = _R.get(w)
    if rs is not None and (rs.peer["id"] != peer["id"] or rs.principal != pr or rs.nid != nid):
        return {"_status": 403, "error": "foreign"}          # Kennung einer anderen Weiterleitung
    out, res = {"ok": True}, []
    first = ops[0] if isinstance(ops[0], dict) else {}
    if first.get("k") == "sub":
        r = await _op_sub(inp, nid, w, rs, first)
        if "error" in r:
            return dict(r, _status=r.pop("_status", 404))
        out.update(r)
        rs = _R[w]
        ops = ops[1:]
    elif rs is None:
        return {"lost": True}                              # Eigentuemer neu gestartet / abgelaufen: Heim meldet sich neu an
    rs.exp = time.time() + WATCH_TTL
    rs.groups = frozenset(inp.groups)
    rs.conn.groups = rs.groups
    sub = rs.sub
    # Rechte je Buendel (wie presence2): weg -> bye; Rolle/Sperre geaendert -> hello
    chk = (await asyncio.to_thread(collab._recheck, [(sub, pr, nid)])).get(sub)
    if chk is None:
        await _drop_rs(rs)
        return {"bye": "access"}
    role, locked = chk
    ro = collab.RANK.get(role, 0) < collab.RANK["edit"] or locked
    if sub.ro != "schema" and (bool(ro) != bool(sub.ro) or role != sub.role):
        sub.ro, sub.role = ro, role
        out["hello"] = {"role": role, "ro": bool(ro), "why": "locked" if locked else None, "update": True}
    room = collab._rooms.get(nid)
    if room is None or sub not in room.subs:
        await _drop_rs(rs)
        return {"bye": "gone"}
    full_needed = False
    for op in ops:
        if not isinstance(op, dict):
            continue
        k = op.get("k")
        if k == "u":
            ref = str(op.get("r") or "")[:40]
            r = await _op_update(rs, room, op.get("u"))
            res.append(dict(r, r=ref))
            if "j" in r:
                full_needed = True
                if r["j"] == "gone":
                    await _drop_rs(rs)
                    out["bye"] = "gone"
                    break
        elif k == "aw":
            a = collab._aw_clean(json.dumps(op.get("a")).encode() if op.get("a") is not None else b"", sub)
            now = time.monotonic()
            if a is not None and now - sub.aw_at >= collab.AW_EVERY:
                sub.aw_at = now
                sub.aw = a or None
                collab._fanout(room, collab._aw_frame(sub), sub)
        elif k == "s":
            try:
                sv = _unb64(op.get("sv"), 1 << 20)
                out["sync2"] = _b64(await asyncio.to_thread(_full, room, rs, sv))
                out["sv"] = _b64(await collab._snap(room, state=True))
            except ValueError:
                full_needed = True
        elif k == "x":
            await _drop_rs(rs)
            out["bye"] = "unsub"
            break
    if res:
        out["res"] = res
    if full_needed and "bye" not in out:
        out["full"] = _b64(await asyncio.to_thread(_full, room, rs))
    return out


async def _op_sub(inp, nid, w, rs, op):
    pr = inp.principal
    if rs is None:
        n, m = _peer_count(inp.peer["id"], pr)
        if n >= PEER_MAX or m >= PRINCIPAL_MAX:
            return {"error": "limit", "_status": 429}
    chk = await asyncio.to_thread(collab._sub_check, pr, nid, op.get("schema"))
    if isinstance(chk, str):
        return {"error": chk}
    role, ro, _owner = chk
    room = await collab._get_room(nid)
    if room is None:
        return {"error": "format"}
    if rs is not None and rs.sub is not None:
        await _drop_rs(rs)
    rs = _RSub(w, inp.peer, pr, inp.name, frozenset(inp.groups), nid)
    rs.conn = _RConn(rs)
    caps = frozenset(F.peer_caps(inp.peer)) & frozenset(("inline2", "cols6", "merge1", "sync1"))
    s = collab._Sub(rs.conn, nid, role, ro, caps or frozenset(("inline2", "cols6", "merge1")))
    s.name, s.color = await asyncio.to_thread(collab._display, pr)
    rs.sub = s
    rs.conn.subs[nid] = s
    _R[w] = rs
    room.subs[s] = True
    sv = None
    if op.get("gen") == room.gen and op.get("sv"):
        try:
            sv = _unb64(op["sv"], 1 << 20)
        except ValueError:
            sv = None
    info = {"proto": collab.PROTO, "gen": room.gen, "role": role, "ro": bool(ro),
            "why": ro if isinstance(ro, str) else ("locked" if ro and role in ("owner", "edit") else None),
            "schema": {"model": model.MODEL, "ver": model.SCHEMA_VER, "hash": model.SCHEMA_HASH}}
    aw = [_b64(collab._aw_frame(o)) for o in list(room.subs) if o is not s and o.aw is not None]
    try:
        st = await asyncio.to_thread(_full, room, rs, sv)
    except ValueError:
        await _drop_rs(rs)
        return {"error": "format"}
    return {"hello": info, "sync2": _b64(st), "sv": _b64(await collab._snap(room, state=True)), "aw": aw, "full_sync": sv is None}


async def _op_update(rs, room, raw):
    sub = rs.sub
    try:
        upd = _unb64(raw)
    except ValueError:
        return {"j": "invalid"}
    if len(upd) <= 2:
        return {"a": 1}
    if sub.ro:
        return {"j": "readonly"}

    def into():
        with _db() as c, rs.lock:
            return _xform(upd, rs.map.into(c), rs.keys, lambda: _room_state(room.nid))
    try:
        u2 = await asyncio.to_thread(into)
    except (ValueError, yupd.Reject):
        return {"j": "invalid"}
    if u2 is None:
        return {"j": "pending"}
    now = time.monotonic()
    if now - sub.n_at >= 1:
        sub.n, sub.n_at = 0, now
    sub.n += 1
    if sub.n > collab.UPD_RATE:
        await asyncio.sleep(0.05)
    try:
        def run():
            with room.lock:
                return collab._apply_update(room, sub, u2)
        changed, upd_out, corr = await asyncio.to_thread(run)
    except collab.Rejected as ex:
        collab._S["stats"]["rejected"] += 1
        _S["stats"]["rejected"] += 1
        if ex.code == "readonly":
            sub.ro = True
        return {"j": ex.code}
    except Exception as ex:  # noqa: BLE001
        print(f"fed_collab: Update {room.nid}: {type(ex).__name__}: {ex}", flush=True)
        return {"j": "retry"}
    if changed:
        collab._fanout(room, collab.frame(collab.T_UPDATE, room.nid, upd_out), sub)
        if corr is not None:
            collab._fanout(room, collab.frame(collab.T_UPDATE, room.nid, corr), None)
        collab._schedule_mat(room)
    return {"a": 1}


async def _owner_periodic():
    """Abgelaufene Abos fremder Personen schliessen, Rechte alle CHECK_EVERY s neu pruefen."""
    last = 0.0
    while True:
        await asyncio.sleep(2.0)
        try:
            now = time.time()
            if _R and not await asyncio.to_thread(collab.enabled):      # notes.live aus: alle fremden Abos beenden
                for rs in list(_R.values()):
                    _queue_out(rs, collab.frame(collab.T_BYE, rs.nid, {"reason": "off"}))
                    await _drop_rs(rs)
            for rs in [x for x in list(_R.values()) if x.exp < now]:
                await _drop_rs(rs)
            if now - last >= collab.CHECK_EVERY and _R:
                last = now
                await collab.recheck(uids={x.principal for x in _R.values()})
        except Exception as ex:  # noqa: BLE001
            print(f"fed_collab: Aufraeumen: {type(ex).__name__}: {ex}", flush=True)


# =====================================================================================================================
# Heim
# =====================================================================================================================
class _Relay:
    def __init__(self, uid, nid, pid, rid):
        self.uid, self.nid, self.pid, self.rid = uid, nid, pid, rid
        self.w = secrets.token_hex(16)
        self.doc = None
        self.keys = {}
        self.lock = threading.Lock()
        self.subs = {}
        self.role, self.ro, self.why, self.gen = None, True, None, ""
        self.name = ""
        self.map = None
        self.ready = asyncio.Event()
        self.err = None
        self.pend = []                       # [(op, (sub, client_ref) | None)]
        self.aw = None                       # juengste eigene Awareness (wird nur einmal je Buendel gesendet)
        self.ev = asyncio.Event()
        self.closed = False
        self.task = None
        self.last_sync = time.monotonic()
        self.need_sync = False
        self.remote_aw = {}                  # sid -> letzter AW-Rahmen des Eigentuemers (fuer neue lokale Abos)
        self.n = 0


_H = {}                     # (uid, nid) -> _Relay
_HW = {}                    # w -> _Relay


def _peer_of(pid):
    with _db() as c:
        return F.peer_by(c, "id", pid)


def _post(relay, ops):
    """Im Thread: Buendel an den Eigentuemer -> (status, json). FedError/_Fail bei Netz/Namen."""
    with _db() as c:
        p = F._home_peer(c, relay.pid)
        actor = F._home_actor(c, relay.uid, relay.pid)
    relay.name = actor["name"]
    return F.call_json(p, "POST", f"/fed/v2/notes/{relay.rid}/collab", body={"w": relay.w, "ops": ops}, actor=actor,
                       timeout=max(2.0, F.EXPRESS_TIMEOUT))


def _send(conn, data):
    conn.send(data)


def _fan(relay, data, skip=None):
    for s in list(relay.subs):
        if s is not skip:
            s.conn.send(data)


async def _start(relay, sv=None):
    """Beim Eigentuemer anmelden (sub) und Zustand holen. -> None oder Fehlercode."""
    op = {"k": "sub", "schema": {"model": model.MODEL, "ver": model.SCHEMA_VER, "hash": model.SCHEMA_HASH},
          "caps": "inline2,cols6,merge1,sync1"}
    if sv is not None and relay.gen:
        op.update(gen=relay.gen, sv=_b64(sv))
    try:
        st, d = await asyncio.to_thread(_post, relay, [op])
    except F._Fail:
        return "federated"
    except F.FedError:
        return "unavailable"
    if st == 403 and isinstance(d, dict) and d.get("error") == "collab1 disabled":
        return "federated"
    if st != 200 or not isinstance(d, dict) or not isinstance(d.get("hello"), dict):
        return str((d or {}).get("error") or "notfound")[:20] if isinstance(d, dict) else "notfound"
    h = d["hello"]
    if not (isinstance(h.get("schema"), dict) and h["schema"].get("hash") == model.SCHEMA_HASH):
        return "federated"
    try:
        full = _unb64(d.get("sync2"), collab.STATE_MAX)
    except ValueError:
        return "federated"
    pid, rid = relay.pid, relay.rid
    hp = _peer_of(pid)

    def build():
        with relay.lock:
            if relay.map is None:
                relay.map = _HomeMap(pid, rid, hp)
            with _db() as c:
                u = _xform(full, relay.map.into(c), relay.keys, (lambda: relay.doc.get_update()) if relay.doc else None)
            if u is None:
                raise ValueError("luecke")
            if relay.doc is not None and d.get("full_sync") and relay.gen and str(h.get("gen") or "") != relay.gen:
                raise ValueError("gen")             # Zustand beim Eigentuemer neu aufgebaut: Clients neu laden (Stufe 1)
            if relay.doc is None:
                relay.doc = model.new_doc()
            sv0 = relay.doc.get_state()
            relay.doc.apply_update(u)
            return relay.doc.get_update(sv0)
    try:
        diff = await asyncio.to_thread(build)
    except (ValueError, yupd.Reject):
        return "federated"
    relay.gen = str(h.get("gen") or "")[:40]
    relay.role = h.get("role") if h.get("role") in collab.RANK else "view"
    relay.ro = bool(h.get("ro"))
    relay.why = h.get("why") if h.get("why") in ("locked", "schema") else None
    relay.last_sync = time.monotonic()
    for a in (d.get("aw") or [])[:200]:
        _remote_frame(relay, a, initial=True)
    if relay.subs and len(diff) > 2:
        _fan(relay, collab.frame(collab.T_UPDATE, relay.nid, diff))
    return None


def _remote_frame(relay, b64, initial=False):
    """Rahmen vom Eigentuemer (Live-Kanal bzw. Antwort) -> an die lokalen Abos. Im Event-Loop."""
    try:
        kind, _nid, parts = collab.parse(_unb64(b64, collab.STATE_MAX))
    except ValueError:
        return
    if kind == collab.T_AW and parts:
        try:
            a = json.loads(parts[0])
            sid = int(a.get("sid"))
            st = a.get("state")
        except (ValueError, TypeError, AttributeError):
            return
        if st is not None and not isinstance(st, dict):
            return
        if isinstance(st, dict):
            st = {k: st[k] for k in ("cursor", "sel", "block", "typing", "name", "color") if k in st}
            st["me"] = False
            if st.get("color") not in model.PALETTE:
                st["color"] = "gray"
            st["name"] = str(st.get("name") or "")[:120]
            if len(json.dumps(st)) > collab.AW_MAX + 400:
                return
        f = collab.frame(collab.T_AW, relay.nid, {"sid": sid & 0xFFFFFFFF, "state": st})
        if st is None:
            relay.remote_aw.pop(sid, None)
        else:
            relay.remote_aw[sid] = f
            if len(relay.remote_aw) > 500:
                relay.remote_aw.pop(next(iter(relay.remote_aw)))
        if not initial:
            _fan(relay, f)
        return
    if kind == collab.T_HELLO and parts:
        try:
            h = json.loads(parts[0])
        except ValueError:
            return
        if isinstance(h, dict) and h.get("update"):
            _hello_update(relay, h)
        return
    if kind == collab.T_BYE:
        try:
            reason = str(json.loads(parts[0]).get("reason") or "gone")[:20] if parts else "gone"
        except (ValueError, AttributeError):
            reason = "gone"
        _close(relay, "federated" if reason == "off" else reason)


def _hello_update(relay, h):
    role = h.get("role") if h.get("role") in collab.RANK else relay.role
    ro = bool(h.get("ro"))
    relay.role, relay.ro, relay.why = role, ro, ("locked" if h.get("why") == "locked" else None)
    for s in list(relay.subs):
        if s.ro == "schema":
            continue
        s.ro, s.role = ro, role
        s.conn.send(collab.frame(collab.T_HELLO, relay.nid, {"proto": collab.PROTO, "gen": relay.gen, "role": role, "ro": ro,
                                                             "why": relay.why, "update": True}))


def _close(relay, reason="gone"):
    """Weiterleitung schliessen: lokale Abos bekommen bye (federated: Client faellt auf Stufe 1 zurueck)."""
    if relay.closed:
        return
    relay.closed = True
    _H.pop((relay.uid, relay.nid), None)
    _HW.pop(relay.w, None)
    for s in list(relay.subs):
        s.conn.send(collab.frame(collab.T_BYE, relay.nid, {"reason": reason}))
        relay.subs.pop(s, None)
        if s.conn.subs.get(relay.nid) is s:
            s.conn.subs.pop(relay.nid, None)
    relay.ev.set()


def _apply_remote(relay, upd):
    """Im Thread (unter relay.lock): Update des Eigentuemers (seine Form) -> Doc des Heims. -> (diff, luecke?)"""
    with _db() as c:
        u = _xform(upd, relay.map.into(c), relay.keys, lambda: relay.doc.get_update())
    if u is None:
        return None, True
    sv0 = relay.doc.get_state()
    relay.doc.apply_update(u)
    try:
        rest = get_update(u, relay.doc.get_state())
        gap = bool(rest) and collab._rvu(rest, 0)[0] > 0
    except Exception:  # noqa: BLE001
        gap = True
    return relay.doc.get_update(sv0), gap


def live_in(c, peer, it):
    """fed_pages.COLLAB_LIVE: Eintrag type collab vom Eigentuemer (im Thread). -> [w, die es hier nicht gibt]."""
    w = it.get("w")
    if not isinstance(w, str) or not _W_RE.fullmatch(w):
        return []
    relay = _HW.get(w)
    if relay is None or relay.pid != peer["id"] or relay.closed or (relay.name and it.get("to") != relay.name) \
            or it.get("note") != relay.rid or relay.doc is None:
        return [w]
    frames = it.get("f")
    if not isinstance(frames, list):
        return []
    for b in frames[:FRAMES_MAX]:
        try:
            raw = _unb64(b, collab.STATE_MAX)
            kind, _nid, parts = collab.parse(raw)
        except ValueError:
            continue
        if kind == collab.T_UPDATE and parts:
            try:
                with relay.lock:
                    diff, gap = _apply_remote(relay, parts[0])
            except (ValueError, yupd.Reject):
                diff, gap = None, True
            if gap:
                relay.need_sync = True
                _in_loop(relay.ev.set)
            if diff and len(diff) > 2:
                f = collab.frame(collab.T_UPDATE, relay.nid, diff)
                _in_loop(_fan, relay, f)
        else:
            _in_loop(_remote_frame, relay, b)
    return []


async def handle(conn, kind, nid, parts):
    """saga.collab: Nachricht eines Clients zu einer Seite eines anderen Servers (negative ID)."""
    if kind == collab.T_SUB:
        return await _h_sub(conn, nid, parts)
    sub = conn.subs.get(nid)
    relay = _H.get((conn.uid, nid))
    if relay is None or relay.closed or sub not in relay.subs:
        conn.send(collab.frame(collab.T_BYE, nid, {"reason": "gone"}))
        if sub is not None:
            collab._drop_sub(sub)
        return
    if kind == collab.T_UNSUB:
        collab._drop_sub(sub)
        return
    if kind == collab.T_SYNC1:
        sv = parts[0] if parts else None

        def diff():
            with relay.lock:
                try:
                    return relay.doc.get_update(sv) if sv else relay.doc.get_update()
                except ValueError:
                    return relay.doc.get_update()
        conn.send(collab.frame(collab.T_SYNC2, nid, await asyncio.to_thread(diff)))
        return
    if kind in (collab.T_SYNC2, collab.T_UPDATE):
        upd = parts[0] if parts else b""
        ref = (parts[1] if len(parts) > 1 else b"").decode("ascii", "replace")[:40]
        if not upd or len(upd) <= 2:
            return
        if sub.ro or relay.ro:
            conn.send(collab.frame(collab.T_REJECT, nid, {"code": "readonly", "ref": ref}, await _relay_full(relay)))
            return
        if len(upd) > collab.MSG_MAX:
            conn.send(collab.frame(collab.T_REJECT, nid, {"code": "size", "ref": ref}, await _relay_full(relay)))
            return
        relay.pend.append(({"k": "u", "u": upd}, (sub, ref)))
        relay.ev.set()
        return
    if kind == collab.T_AW:
        now = time.monotonic()
        if now - sub.aw_at < collab.AW_EVERY:
            return
        sub.aw_at = now
        a = collab._aw_clean(parts[0] if parts else b"", sub)
        if a is None:
            return
        sub.aw = a or None
        _fan(relay, collab._aw_frame(sub), sub)
        relay.aw = a
        relay.ev.set()
        return
    conn.send(collab.frame(collab.T_ERROR, nid, {"code": "kind"}))


async def _relay_full(relay):
    def run():
        with relay.lock:
            return relay.doc.get_update()
    return await asyncio.to_thread(run)


async def _h_sub(conn, nid, parts):
    opts = collab._js(parts[0]) if parts else {}
    pid, rid = F.unref(nid)
    p = await asyncio.to_thread(_peer_of, pid)
    if not usable(p) or not F._ok_rid(rid):
        conn.send(collab.frame(collab.T_ERROR, nid, {"code": "federated"}))     # Stufe 1
        return
    key = (conn.uid, nid)
    relay = _H.get(key)
    if relay is None or relay.closed:
        if len(_H) >= RELAY_MAX or sum(1 for k in _H if k[0] == conn.uid) >= RELAY_USER_MAX:
            conn.send(collab.frame(collab.T_ERROR, nid, {"code": "limit"}))
            return
        relay = _Relay(conn.uid, nid, pid, rid)
        _H[key] = relay
        _HW[relay.w] = relay
        _S["stats"]["relays"] += 1
        err = await _start(relay)
        if err is None and relay.doc is None:
            err = "federated"
        relay.err = err
        relay.ready.set()
        if err is not None:
            _H.pop(key, None)
            _HW.pop(relay.w, None)
            relay.closed = True
            conn.send(collab.frame(collab.T_ERROR, nid, {"code": err if err in ("notfound", "format", "limit") else "federated"}))
            return
        relay.task = asyncio.get_running_loop().create_task(_pump(relay))
    else:
        await relay.ready.wait()
        if relay.err is not None or relay.closed:
            conn.send(collab.frame(collab.T_ERROR, nid, {"code": "federated"}))
            return
    sc = opts.get("schema")
    ro = relay.ro
    if not (isinstance(sc, dict) and sc.get("model") == model.MODEL and sc.get("ver") == model.SCHEMA_VER and sc.get("hash") == model.SCHEMA_HASH):
        ro = "schema"
    caps = frozenset(x for x in str(opts.get("caps") or "").split(",") if x) & frozenset(("inline2", "cols6", "merge1", "sync1"))
    s = collab._Sub(conn, nid, relay.role, ro, caps or frozenset(("inline2", "cols6", "merge1")))
    s.name, s.color = await asyncio.to_thread(collab._display, conn.uid)
    if relay.closed:
        conn.send(collab.frame(collab.T_ERROR, nid, {"code": "federated"}))
        return
    conn.subs[nid] = s
    relay.subs[s] = True
    why = ro if isinstance(ro, str) else relay.why
    conn.send(collab.frame(collab.T_HELLO, nid, {"proto": collab.PROTO, "gen": relay.gen, "role": relay.role, "ro": bool(ro), "why": why,
                                                 "schema": {"model": model.MODEL, "ver": model.SCHEMA_VER, "hash": model.SCHEMA_HASH},
                                                 "me": id(s) & 0xFFFFFFFF, "name": s.name, "color": s.color, "remote": True}))
    sv = parts[1] if len(parts) > 1 and opts.get("gen") == relay.gen else None

    def snap():
        with relay.lock:
            st = relay.doc.get_state()
            try:
                return st, relay.doc.get_update(sv) if sv is not None else relay.doc.get_update()
            except ValueError:
                return st, relay.doc.get_update()
    st, u = await asyncio.to_thread(snap)
    conn.send(collab.frame(collab.T_SYNC1, nid, st))
    conn.send(collab.frame(collab.T_SYNC2, nid, u))
    for f in list(relay.remote_aw.values()):
        conn.send(f)
    for o in list(relay.subs):
        if o is not s and o.aw is not None:
            conn.send(collab._aw_frame(o))


def dropped(sub):
    """saga.collab._drop_sub fuer fremde Seiten: lokales Abo weg; letztes -> Weiterleitung beim Eigentuemer abmelden."""
    relay = _H.get((sub.uid, sub.nid))
    if relay is None or sub not in relay.subs:
        return
    relay.subs.pop(sub, None)
    if sub.aw is not None:
        sub.aw = None
        _fan(relay, collab._aw_frame(sub), sub)
    if not relay.subs and not relay.closed:
        relay.closed = True
        _H.pop((relay.uid, relay.nid), None)
        _HW.pop(relay.w, None)
        relay.ev.set()

        def bye():
            try:
                _post(relay, [{"k": "x"}])
            except (F.FedError, F._Fail):
                pass                          # verwerfbar: das Abo laeuft beim Eigentuemer ab
        try:
            F.peer_submit(relay.pid, bye, drop=True)
        except Exception:  # noqa: BLE001
            pass


async def _pump(relay):
    """Je Weiterleitung: Buendel sammeln (GATHER), senden, Antwort verteilen; periodischer Abgleich (sync1)."""
    backoff = 0.0
    while not relay.closed:
        try:
            await asyncio.wait_for(relay.ev.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        relay.ev.clear()
        if relay.closed:
            return
        if backoff:
            await asyncio.sleep(backoff)
        due = time.monotonic() - relay.last_sync >= SYNC_EVERY or relay.need_sync
        if not relay.pend and relay.aw is None and not due:
            continue
        await asyncio.sleep(GATHER)
        if relay.closed:
            return
        items, size = [], 0
        while relay.pend and len(items) < OPS_MAX - 2:
            n = len(relay.pend[0][0].get("u") or b"")
            if items and size + n > collab.MSG_MAX:          # Buendel hoechstens ~ eine WS-Nachricht (BUNDLE_MAX beim Eigentuemer)
                break
            items.append(relay.pend.pop(0))
            size += n
        try:
            ok = await _bundle_out(relay, items, due)
            backoff = 0.0 if ok else min(10.0, max(1.0, backoff * 2))
        except Exception as ex:  # noqa: BLE001
            print(f"fed_collab: Weiterleitung {relay.nid}: {type(ex).__name__}: {ex}", flush=True)
            backoff = min(10.0, max(1.0, backoff * 2))
        if relay.pend:
            relay.ev.set()


async def _bundle_out(relay, items, due):
    """Ein Buendel senden und die Antwort verteilen. -> True, wenn der Eigentuemer es angenommen hat."""
    ops, origin, rejects = [], [], []

    def out_conv():
        res = []
        with relay.lock, _db() as c:
            for op, org in items:
                try:
                    u = _xform(op["u"], relay.map.out(c), relay.keys, lambda: relay.doc.get_update())
                except (ValueError, yupd.Reject):
                    res.append((None, org, "forbidden"))
                    continue
                res.append((u, org, "pending" if u is None else None))
        return res
    conv = await asyncio.to_thread(out_conv) if items else []
    for (u, org, why), (op, _) in zip(conv, items):
        if why:
            rejects.append((org, why))
            continue
        ops.append({"k": "u", "u": _b64(u), "r": f"h{len(ops)}"})
        origin.append((org, op["u"]))
    if relay.aw is not None:
        ops.append({"k": "aw", "a": relay.aw})
        relay.aw = None
    sync_sv = None
    if due:
        def sv_():
            with relay.lock:
                return relay.doc.get_state()
        sync_sv = await asyncio.to_thread(sv_)
        ops.append({"k": "s", "sv": _b64(sync_sv)})
    full = None
    if rejects:
        full = await _relay_full(relay)
        for (sub, ref), why in rejects:
            sub.conn.send(collab.frame(collab.T_REJECT, relay.nid, {"code": why, "ref": ref}, full))
    if not ops:
        return True
    try:
        st, d = await asyncio.to_thread(_post, relay, ops)
    except (F.FedError, F._Fail):
        st, d = 0, None
    if st == 200 and isinstance(d, dict) and d.get("lost"):
        err = await _start(relay, await _relay_sv(relay))        # Eigentuemer kennt die Weiterleitung nicht mehr: neu anmelden
        if err is not None:
            _close(relay, "federated")
            return False
        try:                                                   # und dasselbe Buendel einmal erneut
            st, d = await asyncio.to_thread(_post, relay, ops)
        except (F.FedError, F._Fail):
            st, d = 0, None
        if isinstance(d, dict) and d.get("lost"):
            st, d = 0, None
    if st != 200 or not isinstance(d, dict):
        relay.need_sync = True
        if st in (403, 404) and isinstance(d, dict):
            _close(relay, "federated" if st == 403 else "gone")
            return False
        full = full or await _relay_full(relay)
        for (sub, ref), _u in origin:
            sub.conn.send(collab.frame(collab.T_REJECT, relay.nid, {"code": "retry", "ref": ref}, full))
        return False
    if isinstance(d.get("hello"), dict):
        _hello_update(relay, d["hello"])
    res = {x.get("r"): x for x in d.get("res") or [] if isinstance(x, dict)}
    acc = []
    owner_full = None
    if d.get("full"):
        try:
            owner_full = _unb64(d["full"], collab.STATE_MAX)
        except ValueError:
            owner_full = None
    for i, ((sub, ref), u) in enumerate(origin):
        r = res.get(f"h{i}") or {}
        if r.get("a"):
            acc.append((sub, ref, u))
        else:
            rejects.append(((sub, ref), str(r.get("j") or "retry")[:20]))

    def apply_all():
        out = []
        with relay.lock:
            if owner_full is not None:
                _apply_remote(relay, owner_full)
            for sub, ref, u in acc:
                sv0 = relay.doc.get_state()
                relay.doc.apply_update(u)
                out.append((sub, ref, relay.doc.get_update(sv0)))
            diff = None
            if d.get("sync2"):
                try:
                    diff, gap = _apply_remote(relay, _unb64(d["sync2"], collab.STATE_MAX))
                except (ValueError, yupd.Reject):
                    diff = None
            return out, diff, relay.doc.get_update() if rejects else None
    out, diff, fullh = await asyncio.to_thread(apply_all)
    for sub, ref, u in out:
        if ref:
            sub.conn.send(collab.frame(collab.T_ACK, relay.nid, ref.encode()))
        if len(u) > 2:
            _fan(relay, collab.frame(collab.T_UPDATE, relay.nid, u), sub)
    _send_rejects(relay, rejects, fullh, conv)
    if diff and len(diff) > 2:
        _fan(relay, collab.frame(collab.T_UPDATE, relay.nid, diff))
    if due:
        relay.last_sync = time.monotonic()
        relay.need_sync = False
    if d.get("bye"):
        _close(relay, "federated" if d["bye"] == "off" else str(d["bye"])[:20])
    return True


def _send_rejects(relay, rejects, fullh, conv):
    """Ablehnungen des Eigentuemers (nach den lokal abgelehnten, die schon gemeldet sind) an die Clients."""
    local = sum(1 for x in conv if x[2])
    for (sub, ref), why in rejects[local:]:
        if fullh is not None:
            sub.conn.send(collab.frame(collab.T_REJECT, relay.nid, {"code": why, "ref": ref}, fullh))
            if why == "readonly":
                sub.ro = True


async def _relay_sv(relay):
    def run():
        with relay.lock:
            return relay.doc.get_state()
    return await asyncio.to_thread(run)


# =====================================================================================================================
# Start, Einbindung
# =====================================================================================================================
_T = {"task": None}


def start():
    if _T["task"] is None:
        _T["task"] = asyncio.get_running_loop().create_task(_owner_periodic())


def stats():
    return dict(_S["stats"], owner_subs=len(_R), relays=len(_H))


collab.FED = __import__(__name__, fromlist=["handle"])
F.COLLAB_CAP = CAP
F.CAPS2_FLAG[CAP] = enabled_cap
FP.COLLAB_LIVE = live_in
