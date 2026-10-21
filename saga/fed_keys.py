"""Schluesselverzeichnis fuer Chats ueber Servergrenzen (2.3, Protokoll 2, Faehigkeit 'keys'; Format saga/chat_v2.py).

- Eigene Konten: das Geraet (nur es kennt ident_sk) signiert das Handle-Zertifikat und je Geraet das Geraete-Zertifikat
  v2 und laedt sie hoch (PUT /api/chat/v2/cert). GET /api/chat/v2/me sagt, was fehlt. Der Server prueft jede Signatur.
- Andere Instanzen holen die oeffentlichen Schluessel signiert: GET /fed/v2/keys?handles=~a,~b (nur mit Faehigkeit 'keys'
  auf beiden Seiten und Admin-Schalter "Chat" je Instanz; "nur gemeinsame Gruppen" nur mit Ausnahme).
- Geraete fragen nur den eigenen Server: GET /api/chat/v2/keys?handles=~a@b.example,... - der holt beim Heimserver der
  Person (signiert), prueft die Zertifikate selbst, speichert sie zwischen (fed_chat_keys) und liefert sie aus. Aendern
  sich Schluessel, meldet der Heimserver 'chat.keys.changed' (Kette 'keys'); der Zwischenspeicher gilt dann als veraltet.
  Die Clients pruefen die Zertifikate trotzdem selbst und merken sich den Identitaetsschluessel je Handle (TOFU: Warnung bei
  Wechsel, "ueberprueft" blockiert) - ein boeser Server kann so keine fremden Schluessel unterschieben."""
import json
import re
import time

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, chat, chat_v2 as V, federation as F, sharing
from saga.core import _api_guard, _bad, _body, t

router = APIRouter()

CACHE_TTL = 3600                 # fremde Schluessel so lange ohne Nachfrage verwenden
HANDLES_MAX = 20                 # je Anfrage
_HANDLE = re.compile(r"(~[a-z2-7]{16})@([a-z0-9.:-]{1,260})")


def full_handle(h):
    return f"{h}@{F.SELF_HOST}"


# =====================================================================================================
# Eigene Konten: Zertifikate v2
# =====================================================================================================
def _v2_state(c, uid):
    """-> (handle, identitaet, cert-zeile, geraete [(id, enc, sign, revoked, fed_sig)])"""
    h = full_handle(F.own_handle(c, uid))
    ident = chat._identity(c, uid)
    cert = c.execute("SELECT handle, ident_pub, sig, dset FROM chat_fed_cert WHERE uid=?", (uid,)).fetchone()
    devs = c.execute("SELECT d.id, d.enc_pub, d.sign_pub, d.revoked_at, f.fed_sig FROM chat_device d LEFT JOIN chat_device_fed f "
                     "ON f.device_id=d.id AND f.uid=d.uid WHERE d.uid=? ORDER BY d.created", (uid,)).fetchall()
    return h, ident, cert, devs


def _entry(h, ident, cert, devs):
    """Oeffentliche Schluessel v2 eines Kontos (nur mit gueltigem Handle-Zertifikat) - oder None."""
    if not ident or not cert or cert[0] != h or cert[1] != ident["ident_pub"] or \
            not V.verify_handle(h, ident["ident_pub"], ident["backup_pub"], cert[2]):
        return None
    ds = _dset(h, ident["ident_pub"], cert[3])
    out = []
    for did, enc, sign, rev, fs in devs:
        if fs and V.verify_device(h, ident["ident_pub"], did, enc, sign, fs):
            out.append({"id": did, "enc_pub": enc, "sign_pub": sign, "fed_sig": fs, "revoked": bool(rev) or (ds is not None and did not in ds["ids"])})
    e = {"v": 2, "handle": h, "ident_pub": ident["ident_pub"], "backup_pub": ident["backup_pub"], "sig": cert[2], "devices": out}
    if ds:
        e["dset"] = ds
    return e


def _dset(h, ident_pub, raw):
    """Gespeicherte signierte Geraete-Liste (saga-chat/v2/devices) -> {ver, ids, sig} oder None."""
    try:
        d = json.loads(raw) if raw else None
    except ValueError:
        return None
    return d if V.verify_devices(h, ident_pub, d) else None


@router.get("/api/chat/v2/me")
def api_me(request: Request):
    """Eigener Stand v2: Handle, ob das Handle-Zertifikat passt, welche aktiven Geraete noch kein Zertifikat v2 haben."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c, c:
        h, ident, cert, devs = _v2_state(c, uid)
    ok = bool(ident and cert and cert[0] == h and cert[1] == ident["ident_pub"]
              and V.verify_handle(h, ident["ident_pub"], ident["backup_pub"], cert[2]))
    dl = [{"id": d[0], "fed_sig": d[4], "ok": bool(ok and d[4] and V.verify_device(h, ident["ident_pub"], d[0], d[1], d[2], d[4]))}
          for d in devs if not d[3]]
    # signierte Geraete-Liste: muss genau die aktiven Geraete mit Zertifikat v2 enthalten (nach Hinzufuegen/Entfernen neu)
    ds = _dset(h, ident["ident_pub"], cert[3]) if ok else None
    act = sorted(d["id"] for d in dl if d["ok"])
    set_ok = bool(ds) and sorted(ds["ids"]) == act
    return chat._no_store({"v": 2, "handle": h, "host": F.SELF_HOST, "identity": bool(ident), "cert_ok": ok,
                           "devices": dl, "missing": [d["id"] for d in dl if not d["ok"]],
                           "device_set": ds, "device_set_ok": set_ok, "device_set_want": act,
                           "need": bool(ident) and (not ok or any(not d["ok"] for d in dl) or not set_ok),
                           "federation": F.enabled()})


@router.put("/api/chat/v2/cert")
async def api_cert(request: Request):
    """{handle_sig?, devices?: {device_id: fed_sig}, device_set?: {ver, ids, sig}} - vom Geraet signierte Zertifikate v2 und
    die signierte Liste der aktiven Geraete (saga/chat_v2.py). device_set: ver groesser als bisher, ids = genau die aktiven
    Geraete mit gueltigem Zertifikat v2 (nach den Zertifikaten aus derselben Anfrage)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if not isinstance(b, dict):
        return _bad(t("ungültige Angabe"))
    devs_in = b.get("devices") or {}
    if not isinstance(devs_in, dict) or len(devs_in) > chat.DEVICES_MAX * 3:
        return _bad(t("ungültige Angabe"))

    def run():
        with _db() as c, c:
            h, ident, cert, devs = _v2_state(c, uid)
            if not ident:
                return _bad("not found", 404)
            now, changed = time.time(), False
            if b.get("handle_sig") is not None:
                if not V.verify_handle(h, ident["ident_pub"], ident["backup_pub"], b.get("handle_sig")):
                    return _bad(t("Ungültige Signatur."), 403)
                c.execute("INSERT INTO chat_fed_cert (uid, handle, ident_pub, sig, created) VALUES (?,?,?,?,?) ON CONFLICT (uid) DO UPDATE "
                          "SET handle=EXCLUDED.handle, ident_pub=EXCLUDED.ident_pub, sig=EXCLUDED.sig, created=EXCLUDED.created",
                          (uid, h, ident["ident_pub"], b["handle_sig"], now))
                changed = True
            known = {d[0]: d for d in devs}
            for did, fs in devs_in.items():
                d = known.get(did)
                if not d or not isinstance(fs, str) or not V.verify_device(h, ident["ident_pub"], did, d[1], d[2], fs):
                    c.rollback()
                    return _bad(t("Ungültige Signatur des Geräts."), 403)
                c.execute("INSERT INTO chat_device_fed (device_id, uid, fed_sig, created) VALUES (?,?,?,?) ON CONFLICT (device_id) "
                          "DO UPDATE SET uid=EXCLUDED.uid, fed_sig=EXCLUDED.fed_sig, created=EXCLUDED.created", (did, uid, fs, now))
                changed = True
            ds = b.get("device_set")
            if ds is not None:
                h, ident, cert, devs = _v2_state(c, uid)
                if not cert or cert[1] != ident["ident_pub"] or not V.verify_devices(h, ident["ident_pub"], ds):
                    c.rollback()
                    return _bad(t("Ungültige Signatur."), 403)
                act = sorted(d[0] for d in devs if not d[3] and d[4] and V.verify_device(h, ident["ident_pub"], d[0], d[1], d[2], d[4]))
                old = _dset(h, ident["ident_pub"], cert[3])
                if sorted(ds["ids"]) != act or (old and ds["ver"] <= old["ver"]):
                    c.rollback()
                    return _bad(t("Die Geräte-Liste passt nicht (mehr) – bitte neu laden."), 409)
                c.execute("UPDATE chat_fed_cert SET dset=? WHERE uid=?", (json.dumps({"ver": ds["ver"], "ids": sorted(ds["ids"]), "sig": ds["sig"]}), uid))
                changed = True
            if changed:
                _announce(c, uid)
        return api_me(request)
    return await run_in_threadpool(run)


def keys_local(c, names):
    """{'~x': eintrag|None} fuer eigene Konten (aktiv, Chat eingeschaltet, gueltiges Zertifikat)."""
    out = {}
    for n in names:
        uid = F.uid_by_handle(c, n)
        if not uid or not chat.enabled():
            out[n] = None
            continue
        out[n] = _entry(*_v2_state(c, uid))
    return out


def _announce(c, uid):
    """Schluessel eines Kontos geaendert: verbundenen Instanzen 'chat.keys.changed' (Kette 'keys'); 2.5 auch den Konten
    DIESES Servers mit einem gemeinsamen Chat im Format v2 (Stufe 3 bzw. foederiert) als Live-Ereignis {handle} - deren
    Apps holen die Schluessel frisch und pruefen TOFU neu (auch ohne Foederation)."""
    r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
    if not r:
        return
    others = [u for (u,) in c.execute(
        "SELECT DISTINCT o.uid FROM chat_member m JOIN chat_member o ON o.chat_id=m.chat_id JOIN chat ch ON ch.id=m.chat_id "
        "WHERE m.uid=? AND m.left_at IS NULL AND o.left_at IS NULL AND o.uid<>m.uid AND o.uid NOT LIKE '@%' AND ch.v IN (2,3)",
        (uid,)).fetchall()]
    if others:
        bus.emit_after(c, "direct", {"uids": sorted(others), "typ": "chat.keys.changed", "data": {"handle": full_handle(r[0])}})
    if not F.enabled():
        return
    for p in F.peers(c):
        if F.feature(p, "keys"):
            F._enqueue(c, p["id"], {"type": "chat.keys.changed", "handle": r[0]}, chain="keys")


def _on_keys_changed(c, uid, **_):
    """Aus saga.chat (Geraet neu/entfernt, Identitaet neu): ungueltig gewordene Zertifikate weg, andere Instanzen informieren."""
    ident = chat._identity(c, uid)
    if not ident:
        c.execute("DELETE FROM chat_fed_cert WHERE uid=?", (uid,))
    else:
        c.execute("DELETE FROM chat_fed_cert WHERE uid=? AND ident_pub<>?", (uid, ident["ident_pub"]))
    _announce(c, uid)


bus.on("chat.keys.changed", _on_keys_changed)


# =====================================================================================================
# Server-zu-Server
# =====================================================================================================
def _peer_ok(p):
    return F.feature(p, "keys") and (not sharing.group_scope() or p["opts"].get("scope_exception"))


@router.get("/fed/v2/keys")
async def fed_keys(request: Request, handles: str = ""):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not _peer_ok(inp.peer):
        return F._signed(inp, 403, {"error": "keys disabled"})
    names = [h for h in dict.fromkeys(x.strip() for x in handles.split(",")) if F.PSEUDO_RE.fullmatch(h)][:HANDLES_MAX]

    def run():
        with _db() as c, c:
            return keys_local(c, names)
    return F._signed(inp, 200, {"keys": await run_in_threadpool(run)})


def _ev_changed(c, peer, ev):
    h = ev.get("handle")
    if not isinstance(h, str) or not F.PSEUDO_RE.fullmatch(h):
        return "ignored"
    c.execute("UPDATE fed_chat_keys SET stale=1 WHERE handle=? AND peer_id=?", (f"{h}@{peer['host']}", peer["id"]))
    return "ok"


F.EV2_HANDLERS["chat.keys.changed"] = _ev_changed


def check_entry(handle, e):
    """Eintrag einer anderen Instanz pruefen: Handle-Zertifikat und je Geraet das Zertifikat v2 (ungueltige Geraete fallen
    weg). -> bereinigter Eintrag oder None."""
    if not isinstance(e, dict) or e.get("handle") != handle:
        return None
    ip, bp, sig = e.get("ident_pub"), e.get("backup_pub"), e.get("sig")
    if not all(isinstance(x, str) for x in (ip, bp, sig)) or not V.verify_handle(handle, ip, bp, sig):
        return None
    ds = e.get("dset") if V.verify_devices(handle, ip, e.get("dset")) else None
    devs = []
    for d in (e.get("devices") or [])[:chat.DEVICES_MAX * 5]:
        if not isinstance(d, dict):
            continue
        did, enc, sign, fs = d.get("id"), d.get("enc_pub"), d.get("sign_pub"), d.get("fed_sig")
        if all(isinstance(x, str) for x in (did, enc, sign, fs)) and V.verify_device(handle, ip, did, enc, sign, fs):
            devs.append({"id": did, "enc_pub": enc, "sign_pub": sign, "fed_sig": fs,
                         "revoked": bool(d.get("revoked")) or (ds is not None and did not in ds["ids"])})
    out = {"v": 2, "handle": handle, "ident_pub": ip, "backup_pub": bp, "sig": sig, "devices": devs}
    if ds:
        out["dset"] = {"ver": ds["ver"], "ids": sorted(ds["ids"]), "sig": ds["sig"]}
    return out


def fetch_remote(p, handles, force=False):
    """Schluessel fuer Handles einer Instanz: Zwischenspeicher oder signiert holen. -> {handle: eintrag|None}"""
    now, out, want = time.time(), {}, []
    with _db() as c:
        cache = {r[0]: r[1:] for r in c.execute(f"SELECT handle, data, fetched, stale FROM fed_chat_keys WHERE peer_id=? AND handle IN "
                                                f"({','.join('?' * len(handles))})", (p["id"], *handles)).fetchall()}
    for h in handles:
        r = cache.get(h)
        if r and not force and not r[2] and now - r[1] < CACHE_TTL:
            out[h] = dict(json.loads(r[0]), fetched=r[1], source="cache")
        else:
            want.append(h)
    if not want:
        return out
    try:
        status, d = F.call_json(p, "GET", "/fed/v2/keys", "handles=" + ",".join(h.split("@")[0] for h in want), timeout=6)
        if status == 403:                    # die andere Seite hat es abgeschaltet: nichts mehr ausliefern
            with _db() as c, c:
                c.execute("DELETE FROM fed_chat_keys WHERE peer_id=?", (p["id"],))
            return dict(out, **{h: None for h in want})
        if status != 200 or not isinstance(d, dict) or not isinstance(d.get("keys"), dict):
            raise F.FedError(f"HTTP {status}", status)
    except F.FedError:
        for h in want:                       # nicht erreichbar: letzter bekannter Stand (als veraltet markiert)
            r = cache.get(h)
            out[h] = dict(json.loads(r[0]), fetched=r[1], source="cache", stale=True) if r else None
        return out
    with _db() as c, c:
        r = c.execute("SELECT status FROM fed_peer WHERE id=? FOR SHARE", (p["id"],)).fetchone()
        if not r or r[0] != "active":                    # in der Zwischenzeit getrennt
            return dict(out, **{h: None for h in want})
        for h in want:
            e = check_entry(h, d["keys"].get(h.split("@")[0]))
            if e is None:
                c.execute("DELETE FROM fed_chat_keys WHERE handle=?", (h,))
                out[h] = None
                continue
            c.execute("INSERT INTO fed_chat_keys (handle, peer_id, data, fetched, stale) VALUES (?,?,?,?,0) ON CONFLICT (handle) DO UPDATE "
                      "SET peer_id=EXCLUDED.peer_id, data=EXCLUDED.data, fetched=EXCLUDED.fetched, stale=0", (h, p["id"], json.dumps(e), now))
            out[h] = dict(e, fetched=now, source="remote")
    return out


@router.get("/api/chat/v2/keys")
def api_keys(request: Request, handles: str = "", fresh: int = 0):
    """Oeffentliche Schluessel v2 zu foederierten Handles ('~x@host', hoechstens 20) - eigene Instanz direkt, fremde ueber
    den Heimserver der Person (signiert, zwischengespeichert). Die Clients pruefen die Zertifikate selbst (TOFU)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not chat._rate("keys2", uid, 60):
        return _bad(t("zu schnell"), 429)
    want = [h for h in dict.fromkeys(x.strip().lower() for x in handles.split(",")) if _HANDLE.fullmatch(h)][:HANDLES_MAX]
    out, by = {}, {}
    for h in want:
        name, host = _HANDLE.fullmatch(h).groups()
        by.setdefault(F.fedsig.authority(host), []).append(h)
    for host, hs in by.items():
        if host == F.SELF_HOST:
            with _db() as c, c:
                # 2.5: Schluessel eigener Konten nur fuer sich selbst und Personen mit einem (frueheren) gemeinsamen Chat -
                # wie /api/chat/keys seit 2.4; das Ziel einer Chat-Anlage ist danach Mitglied
                known = {r[0] for r in c.execute("SELECT DISTINCT f.handle FROM chat_member m1 JOIN chat_member m2 ON m2.chat_id=m1.chat_id "
                                                 "JOIN fed_handle f ON f.uid=m2.uid WHERE m1.uid=?", (uid,)).fetchall()}
                mine = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
                if mine:
                    known.add(mine[0])
                hs_ok = [h for h in hs if h.split("@")[0] in known]
                loc = keys_local(c, [h.split("@")[0] for h in hs_ok])
            out.update({h: None for h in hs})
            hs = hs_ok
            out.update({h: (dict(loc[h.split("@")[0]], source="local") if loc[h.split("@")[0]] else None) for h in hs})
            continue
        with _db() as c:
            p = F.peer_by(c, "host", host)
        if not _peer_ok(p):                  # nicht verbunden: ueber das Heim eines gemeinsamen Chats (E5, saga.fed_chat)
            out.update(VIA_HOME(uid, hs, bool(fresh)) if VIA_HOME is not None else {h: None for h in hs})
            continue
        out.update(fetch_remote(p, hs, force=bool(fresh)))
    return chat._no_store({"keys": out})


VIA_HOME = None              # saga.fed_chat.keys_via_home(uid, handles, fresh) -> {handle: eintrag|None}
