"""Gelesen-Bestaetigungen und Online-Status in Chats (2.4) - "wie WhatsApp", Vertrag fuer Apps: docs/APP-API.md
("Gelesen-Bestaetigungen und Online-Status"), Server zu Server: docs/APP-API-FED.md (Abschnitt 8).

Einstellungen je Konto (user_kv, Standard an): "Gelesen-Bestaetigungen senden" (chat_receipts) und "Online-Status zeigen"
(chat_online), getrennt. Gegenseitig: wer etwas abschaltet, sendet es nicht und sieht es auch bei anderen nicht.

Gelesen-Bestaetigungen (Ende-zu-Ende): Das Geraet der lesenden Person signiert "gelesen bis Nachricht <id> (Position n)"
mit seinem Geraeteschluessel und versiegelt das fuer den Backup-Schluessel JEDES anderen Mitglieds (crypto_box_seal - alle
Geraete eines Kontos haben ihn). Der Server speichert je Chat, Leser und Konto nur die neueste Bestaetigung und reicht sie
weiter; er sieht nur, DASS jemand eine Bestaetigung geschickt hat (und wann), nicht bis wohin. Abwaegung gegen den Lese-
Zeiger: chat_member.last_read (Ungelesen-Zaehler, Benachrichtigungen, Push) kennt der EIGENE Server ohnehin - die
Bestaetigung verraet ihm nichts Neues, ist aber vom Geraet signiert (der Server kann kein "gelesen" erfinden) und verlaesst
den Server nur versiegelt: andere Saga-Server erfahren den Lese-Zeiger nicht (last_read bleibt lokal wie bisher).
Format: saga-chat/v1 (lokale Chats, chat_id + Benutzername) bzw. saga-chat/v2 (Chats ueber Servergrenzen, chat_ref + Handle).

Last: Bestaetigungen werden je Chat gebuendelt (hoechstens ein Versand je GAP Sekunden: ein Live-Ereignis je Konto mit allen
neuen Bestaetigungen, je Leser hoechstens ein Foederations-Ereignis). Clients schicken hoechstens eine je Chat und 2 s.

Online-Status: aus der Live-Verbindung (saga.live: online = mindestens eine Verbindung, offline nach OFF_GRACE); "zuletzt
online" nur im 5-Minuten-Raster (chat_seen, kein Verlauf, nirgends fuer Admins sichtbar). Nur fuer Personen, mit denen man
einen (angenommenen) Chat hat. Ausgeliefert nur an Konten, die die Person gerade abonniert haben (POST /api/chat/presence,
90 s, erneuert vom Client, solange der Chat bzw. die Liste sichtbar ist). Ueber Servergrenzen: der eigene Server abonniert
beim Server der Person (Auftrag 'online', 90 s), der meldet Wechsel (Auftrag 'online.ev', verwerfbar) - Muster wie bei der
Seiten-Anwesenheit (saga.fed_pages)."""
import math
import threading
import time

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, chat, fed_chat as FC, federation as F, live, sharing
from saga.core import _bad, _body, t

router = APIRouter()

KV_RCPT, KV_ONLINE = "chat_receipts", "chat_online"     # user_kv: '0' = aus (fehlt = an)
GAP = 2.0                          # Buendelung je Chat (Sekunden)
INLINE = 8                         # Live-Ereignis traegt hoechstens so viele Bestaetigungen, sonst nur "more" (Abruf per GET)
SEAL_MIN, SEAL_MAX = 48 + 64, 2048  # versiegelte Bestaetigung (B64-Zeichen): Siegel + Signatur ... Obergrenze
RCPT_RATE = 120                    # Bestaetigungen je Konto und Minute (Clients: hoechstens eine je Chat und 2 s)
FED_RCPT_RATE = 1200               # Bestaetigungen je Instanz und Minute (Auftraege + Ereignisse)
ROUND = 300                        # "zuletzt online": 5-Minuten-Raster
WATCH_TTL = 90                     # Abo des Online-Status (lokal und bei anderen Servern)
WATCH_RENEW = 60                   # Abo bei einem anderen Server spaetestens nach so vielen Sekunden erneuern
RETRY_UNKNOWN = 5                  # ... ohne bekannten Stand (dort nicht erlaubt/unbekannt) schon nach so vielen Sekunden
USERS_MAX = 50                     # Personen je Abfrage
WATCH_MAX = 200                    # gleichzeitig abonnierte Personen je Konto
PRES_RATE = 30                     # Abfragen je Konto und Minute
FED_ONLINE_RATE = 600              # Auftraege 'online' / 'online.ev' je Instanz und Minute


# =====================================================================================================
# Einstellungen
# =====================================================================================================
def _off(c, key, uids):
    """Welche dieser (lokalen) Konten haben die Einstellung key abgeschaltet?"""
    uids = sorted({u for u in uids if u and not sharing.is_remote(u)})
    if not uids:
        return set()
    return {r[0] for r in c.execute(f"SELECT uid FROM user_kv WHERE k=? AND v='0' AND uid IN ({','.join('?' * len(uids))})", (key, *uids))}


def is_on(c, uid, key):
    return uid not in _off(c, key, [uid])


def prefs(c, uid):
    off = {r[0] for r in c.execute("SELECT k FROM user_kv WHERE uid=? AND k IN (?,?) AND v='0'", (uid, KV_RCPT, KV_ONLINE))}
    return {"receipts": KV_RCPT not in off, "online": KV_ONLINE not in off}


def me_extra(c, uid):
    """chat.STATUS_ME: Zusatzfelder fuer GET /api/chat/me."""
    return {"privacy": prefs(c, uid), "features": ["receipts", "online"] + (["chat3"] if chat.v3_new() else []),   # 2.5: Stufe 3
            "status_limits": {"receipt_gap": GAP, "presence_ttl": WATCH_TTL, "presence_users": USERS_MAX, "last_round": ROUND}}


@router.get("/api/chat/privacy")
def api_privacy(request: Request):
    uid, err = chat._me(request)
    if err:
        return err
    with _db() as c:
        return chat._no_store(prefs(c, uid))


@router.put("/api/chat/privacy")
async def api_privacy_put(request: Request):
    """{receipts?: bool, online?: bool} -> {receipts, online}. Aus heisst gegenseitig: nichts senden, nichts sehen."""
    uid, err = chat._me(request)
    if err:
        return err
    b = await _body(request)
    if not isinstance(b, dict) or not any(k in b for k in ("receipts", "online")) or \
            any(k in b and not isinstance(b[k], bool) for k in ("receipts", "online")):
        return _bad(t("ungültige Angabe"))
    return chat._no_store(await run_in_threadpool(set_prefs, uid, b))


def set_prefs(uid, b):
    with _db() as c, c:
        old = prefs(c, uid)
        for key, name in ((KV_RCPT, "receipts"), (KV_ONLINE, "online")):
            if name not in b:
                continue
            if b[name]:
                c.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, key))
            else:
                c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?,?,'0') ON CONFLICT (uid, k) DO UPDATE SET v='0'", (uid, key))
        new = prefs(c, uid)
        if old["receipts"] and not new["receipts"]:
            # eigene Bestaetigungen zurueckziehen: hier loeschen, andere Server ueber das Heim (Ereignis mit off)
            c.execute("DELETE FROM chat_receipt WHERE reader=?", (uid,))
            _fed_off(c, uid)
        chat._direct(c, [uid], "chat.privacy", new)
    if old["online"] and not new["online"]:
        _hide(uid)
    elif new["online"] and not old["online"]:
        _changed(uid, live.is_online(uid), time.time(), push_only=True)
    return new


# =====================================================================================================
# Gelesen-Bestaetigungen
# =====================================================================================================
_lock = threading.Lock()
_pend = {}             # chat_id -> {leser} (neu seit dem letzten Versand)
_fedpend = {}          # (chat_id, leser) -> versiegelte Bestaetigungen fuer Konten anderer Server (neueste)
_last = {}             # chat_id -> time.monotonic() des letzten Versands


def _keys_ok(keys):
    return isinstance(keys, dict) and 0 < len(keys) <= chat.MEMBERS_MAX and all(
        isinstance(k, str) and k.startswith("backup:") and len(k) < 300 and isinstance(v, str) and SEAL_MIN <= len(v) <= SEAL_MAX
        and chat.b64d(v) is not None for k, v in keys.items())


def _store(c, cid, reader, loc, now=None):
    """loc: {konto hier: versiegelt} -> Zeilen (nur die neueste je Leser und Konto; Konten mit "aus" bekommen nichts)."""
    off = _off(c, KV_RCPT, list(loc))
    rows = [(cid, reader, u, v, now or time.time()) for u, v in sorted(loc.items()) if u not in off and u != reader]
    if rows:
        c.executemany("INSERT INTO chat_receipt (chat_id, reader, viewer, sealed, at) VALUES (?,?,?,?,?) ON CONFLICT (chat_id, viewer, reader) "
                      "DO UPDATE SET sealed=EXCLUDED.sealed, at=EXCLUDED.at", rows)
    return len(rows)


def take(uid, ch, m, rc):
    """chat.RECEIPT: Bestaetigung aus POST /api/chats/{cid}/read -> 'ok' | 'off' (eigene Einstellung aus) | 'none' (niemand
    zu benachrichtigen, Anfrage noch offen, schreibgeschuetzt) | 'rate' | 'bad'."""
    keys = rc.get("keys") if isinstance(rc, dict) else None
    if not _keys_ok(keys):
        return "bad"
    if not chat._rate("rcpt", uid, RCPT_RATE):
        return "rate"
    cid = ch["id"]
    with _db() as c, c:
        if not is_on(c, uid, KV_RCPT):
            return "off"
        if (m or {}).get("state", "ok") != "ok":
            return "none"                         # Nachrichtenanfrage: die andere Seite erfaehrt nicht, ob gelesen wurde
        mem = chat.members(c, cid)
        if uid not in mem:
            return "none"
        loc, rem = {}, {}
        if chat.hfmt(ch):                         # v2-Format (2.3 foederiert, 2.5 Stufe 3): Empfaenger 'backup:<handle>'
            if ch["fstate"]:
                return "none"
            states = dict(FC.members(c, cid))
            by_h = {FC.member_handle(c, u): u for u in mem if u != uid and states.get(u) == "ok"}
            for k, v in keys.items():
                u = by_h.get(k[7:])
                if u is None:
                    continue
                if sharing.is_remote(u):
                    rem[k] = v
                else:
                    loc[u] = v
        else:
            others = set(mem) - {uid}
            loc = {k[7:]: v for k, v in keys.items() if k[7:] in others}
        if not loc and not rem:
            return "none"
        _store(c, cid, uid, loc)
        if rem:
            with _lock:
                _fedpend[(cid, uid)] = rem
        c.after_commit(lambda: _schedule(cid, uid))
    return "ok"


def _schedule(cid, reader):
    """Versand buendeln: der erste sofort, weitere innerhalb von GAP Sekunden gesammelt danach."""
    with _lock:
        p = _pend.get(cid)
        if p is not None:
            p.add(reader)
            return
        _pend[cid] = {reader}
        wait = _last.get(cid, -GAP) + GAP - time.monotonic()
        if len(_last) > 20000:
            now = time.monotonic()
            for k in [k for k, v in _last.items() if now - v > 60 and k not in _pend]:
                _last.pop(k, None)
    if wait > 0:
        tm = threading.Timer(wait, _flush, (cid,))
        tm.daemon = True
        tm.start()
    else:
        _flush(cid)


def _flush(cid):
    with _lock:
        readers = _pend.pop(cid, set())
        _last[cid] = time.monotonic()
        fedp = {r: _fedpend.pop((cid, r)) for r in readers if (cid, r) in _fedpend}
    if not readers:
        return
    try:
        with _db() as c, c:
            ch = chat._load(c, cid)
            if not ch:
                return
            mem = chat.members(c, cid)
            rd = [r for r in readers if r in mem]
            if not rd:
                return
            loc = [u for u in mem if not sharing.is_remote(u)]
            off = _off(c, KV_RCPT, loc)
            fed = chat.hfmt(ch)
            hs = {r: FC.member_handle(c, r) for r in rd} if fed else {}
            per = {}
            for reader, viewer, sealed in c.execute(f"SELECT reader, viewer, sealed FROM chat_receipt WHERE chat_id=? AND reader IN "
                                                    f"({','.join('?' * len(rd))})", (cid, *rd)).fetchall():
                if viewer in off or viewer not in mem or reader == viewer or reader in off:
                    continue
                it = {"from": chat.person(c, reader, viewer, ch), "sealed": sealed}
                if fed:
                    it["handle"] = hs.get(reader)
                per.setdefault(viewer, []).append(it)
            if per:                                # ein Ereignis fuer den Verteiler, je Konto eigene Daten; viele auf einmal (grosse
                # Gruppe): nur der Hinweis - wer den Chat offen hat, holt sie mit GET /receipts (Bandbreite, Live-Puffer)
                bus.emit_after(c, "direct_each", {"items": [(v, "chat.receipt", {"chat_id": cid, "receipts": its} if len(its) <= INLINE
                                                             else {"chat_id": cid, "receipts": [], "more": True}) for v, its in sorted(per.items())]})
            for reader, rem in fedp.items():
                if chat.is_fed(ch) and reader in rd and not sharing.is_remote(reader) and reader not in off:
                    _fed_send(c, ch, hs.get(reader), rem)
    except Exception as ex:  # noqa: BLE001 - Datenbank gerade weg: diese Buendelung entfaellt (die Zeilen liegen, GET liefert sie)
        print(f"chat_status: Versand Chat {cid}: {type(ex).__name__}: {ex}", flush=True)


def _fed_send(c, ch, h, rem, off=False):
    """Bestaetigung (bzw. Zuruecknahme) einer Person dieses Servers an die anderen Server: als Heim direkt an die Teilnehmer-
    Server (Kette des Chats, nach den Nachrichten), sonst als Auftrag an das Heim (Kette Richtung Heim)."""
    if not h:
        return
    if ch["home_peer"] is None:
        for p in FC.participants(c, ch):
            if not F.feature(p, "receipts"):
                continue
            ev = {"type": "chat.receipt", "ref": ch["ref"], "by": h}
            if off:
                ev["off"] = True
            else:
                k = FC.keys_for_host(rem, p["host"])
                if not k:
                    continue
                ev["keys"] = k
            F._enqueue(c, p["id"], ev, chain=FC.chain_down(ch["ref"]))
        return
    p = F.peer_by(c, "id", ch["home_peer"])
    if not F.feature(p, "receipts"):
        return
    body = {"op": "receipt", "ref": ch["ref"], "actor": h}
    if off:
        body["off"] = True
    else:
        body["keys"] = rem
    F._enqueue(c, p["id"], {"type": "chat.op", "op_body": body}, chain=FC.chain_up(ch["ref"]))


def _fed_off(c, uid):
    """Gelesen-Bestaetigungen abgeschaltet: in allen Chats ueber Servergrenzen zuruecknehmen."""
    if not F.enabled():
        return
    h = FC.my_handle(c, uid)
    for r in c.execute(f"SELECT {', '.join('ch.' + x.strip() for x in chat.C_COLS.split(','))} FROM chat ch JOIN chat_member m ON m.chat_id=ch.id "
                       "WHERE m.uid=? AND m.left_at IS NULL AND ch.v=2 AND ch.ref IS NOT NULL", (uid,)).fetchall():
        ch = chat._row(r)
        if not ch["fstate"]:
            _fed_send(c, ch, h, None, off=True)


@router.get("/api/chats/{cid}/receipts")
def api_receipts(cid: int, request: Request):
    """Neueste Gelesen-Bestaetigung je anderem Mitglied, versiegelt fuer mein Konto: {chat_id, enabled, receipts:[{from,
    handle?, sealed}]}. Eigene Einstellung aus: enabled false, keine Bestaetigungen (gegenseitig)."""
    uid, err = chat._me(request)
    if err:
        return err
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if not is_on(c, uid, KV_RCPT):
            return chat._no_store({"chat_id": ch["id"], "enabled": False, "receipts": []})
        rows = c.execute("SELECT reader, sealed FROM chat_receipt WHERE chat_id=? AND viewer=?", (ch["id"], uid)).fetchall()
        gone = [r[0] for r in rows if r[0] not in mem]
        if gone:                                   # ausgetreten/entfernt: deren Bestaetigungen hier aufraeumen
            c.execute(f"DELETE FROM chat_receipt WHERE chat_id=? AND reader IN ({','.join('?' * len(gone))})", (ch["id"], *gone))
        off = _off(c, KV_RCPT, [r[0] for r in rows])
        fed = chat.hfmt(ch)
        out = []
        for reader, sealed in rows:
            if reader in mem and reader != uid and reader not in off:
                it = {"from": chat.person(c, reader, uid, ch), "sealed": sealed}
                if fed:
                    it["handle"] = FC.member_handle(c, reader)
                out.append(it)
    return chat._no_store({"chat_id": ch["id"], "enabled": True, "receipts": out})


# --- Foederation: Auftrag 'receipt' (beim Heim) und Ereignis 'chat.receipt' (vom Heim) ---------------------------------
def _fed_keys_in(keys):
    return keys if _keys_ok(keys) else None


def op_receipt(c, peer, d):
    """Heim: Bestaetigung einer Person des anfragenden Servers -> hier speichern, an die anderen Teilnehmer-Server weiter."""
    if not F.feature(peer, "receipts"):
        return 403, {"error": "receipts disabled"}
    if not FC._rate_ok("crcpt", peer["host"], FED_RCPT_RATE):
        return 429, {"error": "too many"}
    ch = FC._home_chat(c, d)
    h, p_ = FC._actor(c, peer, d)
    if not ch or not h:
        return 200, {"ok": True}
    mem = dict(FC.members(c, ch["id"]))
    if mem.get(p_) != "ok":
        return 200, {"ok": True}
    if d.get("off") is True:
        c.execute("DELETE FROM chat_receipt WHERE chat_id=? AND reader=?", (ch["id"], p_))
        for p in FC.participants(c, ch):
            if p["id"] != peer["id"] and F.feature(p, "receipts"):
                F._enqueue(c, p["id"], {"type": "chat.receipt", "ref": ch["ref"], "by": h, "off": True}, chain=FC.chain_down(ch["ref"]))
        return 200, {"ok": True}
    keys = _fed_keys_in(d.get("keys"))
    if keys is None:
        return 400, {"error": "bad keys"}
    _take_in(c, ch, p_, keys)
    for p in FC.participants(c, ch):
        if p["id"] == peer["id"] or not F.feature(p, "receipts"):
            continue
        k = FC.keys_for_host(keys, p["host"])
        if k:
            F._enqueue(c, p["id"], {"type": "chat.receipt", "ref": ch["ref"], "by": h, "keys": k}, chain=FC.chain_down(ch["ref"]))
    return 200, {"ok": True}


def _take_in(c, ch, reader, keys):
    loc = {}
    for rcpt, v in FC.local_keys(c, ch, {k: v for k, v in keys.items() if k.startswith("backup:")}):
        loc[rcpt[7:]] = v
    states = dict(FC.members(c, ch["id"]))
    loc = {u: v for u, v in loc.items() if states.get(u) == "ok"}     # Anfrage noch offen: nichts zeigen
    if _store(c, ch["id"], reader, loc):
        cid = ch["id"]
        c.after_commit(lambda: _schedule(cid, reader))


def _ev_receipt(c, peer, ev):
    """Teilnehmer-Server: Bestaetigung vom Heim (Person des Heims oder eines anderen Teilnehmer-Servers)."""
    if not FC.peer_ok(peer) or not F.feature(peer, "receipts"):
        return "ignored"
    if not FC._rate_ok("crcpt", peer["host"], FED_RCPT_RATE):
        return "retry"
    ch = FC._from_home(c, peer, ev)
    if not ch:
        return "ignored"
    by = FC._sender_of(c, ch, ev.get("by"))
    if not by or not sharing.is_remote(by):
        return "ignored"
    if dict(FC.members(c, ch["id"])).get(by) != "ok":
        return "ignored"
    if ev.get("off") is True:
        c.execute("DELETE FROM chat_receipt WHERE chat_id=? AND reader=?", (ch["id"], by))
        return "ok"
    keys = _fed_keys_in(ev.get("keys"))
    if keys is None:
        return "ignored"
    _take_in(c, ch, by, keys)
    return "ok"


# =====================================================================================================
# Online-Status
# =====================================================================================================
_wl = threading.Lock()
_watch = {}            # Person (Konto hier oder Prinzipal) -> {abonnierendes Konto hier: ablauf}
_rstate = {}           # Prinzipal -> {"online", "last", "exp"} (vom Server der Person gemeldet)
_rsub = {}             # Prinzipal -> letzte Abo-Erneuerung beim Server der Person
_fsub = {}             # Konto hier -> {peer_id: ablauf} (Abos anderer Server)
_alias = {}            # (Person, abonnierendes Konto) -> Kennung der Anfrage (2.5: Handle '~x@host' aus Chats der Stufe 3)


def _round(ts):
    return float(math.floor(ts / ROUND) * ROUND) if ts else None


def _shared(c, uid, targets, v1=False):
    """Mit welchen dieser Personen hat uid einen angenommenen Chat (beide aktiv, beide 'ok')? v1 (2.5): nur Chats, in denen
    Mitglieder den Anmeldenamen ohnehin kennen (nicht Stufe 3) - fuer Anfragen per Benutzername (kein Orakel)."""
    targets = sorted({x for x in targets if x and x != uid})
    if not targets:
        return set()
    return {r[0] for r in c.execute(
        "SELECT DISTINCT b.uid FROM chat_member a JOIN chat_member b ON b.chat_id=a.chat_id" + (" JOIN chat ch ON ch.id=a.chat_id" if v1 else "") +
        " WHERE a.uid=? AND a.left_at IS NULL AND a.state='ok' AND b.left_at IS NULL AND b.state='ok' AND b.uid IN "
        f"({','.join('?' * len(targets))})" + (" AND (ch.v<>3 OR ch.v3_seq IS NOT NULL)" if v1 else ""), (uid, *targets))}


def _asked(c, users):
    """Anfrage-Kennungen -> {konto|prinzipal: kennung}: Handle eines Kontos hier ('~x@<dieser Server>', 2.5) wird zum Konto."""
    out = {}
    for x in users:
        m = chat._FULL_H.fullmatch(x)
        if m and F.fedsig.authority(m.group(2)) == F.SELF_HOST:
            u = F.uid_by_handle(c, m.group(1))
            if u:
                out[u] = x
        else:
            out[x] = x
    return out


def _as(u, viewer):
    """Kennung, unter der viewer die Person u abonniert hat (Ereignis chat.presence)."""
    return _alias.get((u, viewer), u)


def _local_state(c, uids):
    """{konto: {online, last}} fuer Konten dieses Servers (Einstellung bereits geprueft)."""
    uids = sorted(uids)
    seen = {r[0]: r[1] for r in c.execute(f"SELECT uid, at FROM chat_seen WHERE uid IN ({','.join('?' * len(uids))})", tuple(uids))} if uids else {}
    return {u: ({"online": True, "last": None} if live.is_online(u) else {"online": False, "last": _round(seen.get(u))}) for u in uids}


@router.post("/api/chat/presence")
async def api_presence(request: Request):
    """Online-Status abonnieren: {users: [benutzername | '@~x@host', ...] (hoechstens 50), leave?: true} -> {enabled, ttl,
    users: {name: {online, last}}}. Nur Personen mit einem gemeinsamen angenommenen Chat und eingeschaltetem Online-Status
    erscheinen; Aenderungen kommen als Live-Ereignis chat.presence, solange das Abo laeuft (ttl Sekunden, vorher erneuern)."""
    uid, err = chat._me(request)
    if err:
        return err
    b = await _body(request)
    users = b.get("users") if isinstance(b, dict) else None
    if not isinstance(users, list) or len(users) > USERS_MAX or any(not isinstance(u, str) or not 0 < len(u) <= 300 for u in users):
        return _bad(t("ungültige Angabe"))
    if not chat._rate("presence", uid, PRES_RATE):
        return _bad(t("zu schnell"), 429)
    return chat._no_store(await run_in_threadpool(presence, uid, list(dict.fromkeys(users)), b.get("leave") is True))


def presence(uid, users, leave=False):
    now = time.time()
    if leave:
        with _db() as c:
            users = list(_asked(c, users))
        with _wl:
            for u in users:
                _alias.pop((u, uid), None)
                w = _watch.get(u)
                if w:
                    w.pop(uid, None)
                    if not w:
                        _watch.pop(u, None)
        return {"ok": True}
    with _db() as c:
        if not is_on(c, uid, KV_ONLINE):
            _unwatch(uid)
            return {"enabled": False, "ttl": WATCH_TTL, "users": {}}
        asked = _asked(c, users)
        by_h = [u for u, x in asked.items() if x != u]
        ok = _shared(c, uid, by_h) | _shared(c, uid, [u for u in asked if u not in by_h and not sharing.is_remote(u)], v1=True) \
            | _shared(c, uid, [u for u in asked if sharing.is_remote(u)])
        loc = [u for u in ok if not sharing.is_remote(u)]
        loc = [u for u in loc if u not in _off(c, KV_ONLINE, loc)]
        rem = [u for u in ok if sharing.is_remote(u)]
        out = _local_state(c, loc)
        renew = {}
        if rem and F.enabled():
            me_h = FC.my_handle(c, uid)
            for u in rem:
                h = FC.handle_of(u)
                n, host = FC.split_handle(h)
                p = F.peer_by(c, "host", host) if n else None
                if not p or not F.feature(p, "online") or not FC.peer_ok(p):
                    continue
                st = _rstate.get(u)
                if st and st["exp"] > now and st.get("online") is not None:
                    out[u] = {"online": st["online"], "last": st["last"]}
                known = bool(st) and st["exp"] > now and st.get("online") is not None
                # unbekannt bzw. dort (noch) nicht erlaubt, z. B. Nachrichtenanfrage gerade angenommen: bald erneut fragen
                if now - _rsub.get(u, 0) >= (WATCH_RENEW if known else RETRY_UNKNOWN):
                    ref = c.execute("SELECT ch.ref FROM chat ch JOIN chat_member a ON a.chat_id=ch.id AND a.uid=? AND a.left_at IS NULL AND a.state='ok' "
                                    "JOIN chat_member b ON b.chat_id=ch.id AND b.uid=? AND b.left_at IS NULL AND b.state='ok' "
                                    "WHERE ch.v=2 AND ch.ref IS NOT NULL AND ch.fstate='' ORDER BY (ch.kind='dm') DESC LIMIT 1", (uid, u)).fetchone()
                    if ref and me_h:
                        renew.setdefault(p["id"], {"actor": me_h, "items": []})["items"].append({"h": h, "ref": ref[0]})
                        _rsub[u] = now
    with _wl:
        for u in list(out) + rem:
            _watch.setdefault(u, {})[uid] = now + WATCH_TTL
            if asked.get(u, u) != u:
                _alias[(u, uid)] = asked[u]
            else:
                _alias.pop((u, uid), None)
        mine = [(w[uid], u) for u, w in _watch.items() if uid in w]
        if len(mine) > WATCH_MAX:                  # zu viele: die am laengsten nicht erneuerten vergessen
            for _, u in sorted(mine)[:len(mine) - WATCH_MAX]:
                _watch[u].pop(uid, None)
                _alias.pop((u, uid), None)
        if len(_alias) > 50000:
            for k in [k for k in _alias if k[1] not in _watch.get(k[0], {})]:
                _alias.pop(k, None)
    for pid, body in renew.items():
        F.peer_submit(pid, _subscribe, pid, body)      # nicht verwerfen: sonst erst nach WATCH_RENEW erneut
    return {"enabled": True, "ttl": WATCH_TTL, "users": {asked.get(u, u): st for u, st in out.items()}}


def _unwatch(uid):
    with _wl:
        for u in [u for u, w in _watch.items() if uid in w]:
            _watch[u].pop(uid, None)
            if not _watch[u]:
                _watch.pop(u, None)


def _viewers(c, target):
    """Konten hier, die target gerade abonniert haben und es (noch) sehen duerfen."""
    now = time.time()
    with _wl:
        w = _watch.get(target) or {}
        vs = [v for v, exp in w.items() if exp > now]
        for v in [v for v, exp in w.items() if exp <= now]:
            w.pop(v, None)
        if not w:
            _watch.pop(target, None)
    if not vs:
        return []
    off = _off(c, KV_ONLINE, vs)
    return [v for v in vs if v not in off and target in _shared(c, v, [target])]


def on_online(payload):
    """live.ONLINE (im Thread): Konto online/offline -> "zuletzt online" speichern, Abos hier und anderer Server melden."""
    uid, on, at = payload.get("uid"), bool(payload.get("online")), payload.get("at") or time.time()
    if not uid:
        return []
    try:
        return _changed(uid, on, at)
    except Exception as ex:  # noqa: BLE001 - Datenbank gerade weg: Status kommt mit der naechsten Abfrage
        print(f"chat_status: online {type(ex).__name__}: {ex}", flush=True)
        return []


def _changed(uid, on, at, push_only=False):
    out = []
    with _db() as c, c:
        if not push_only:
            c.execute("INSERT INTO chat_seen (uid, at) VALUES (?,?) ON CONFLICT (uid) DO UPDATE SET at=EXCLUDED.at", (uid, _round(at)))
        if not is_on(c, uid, KV_ONLINE):
            return []
        data = {"username": uid, "online": on, "last": None if on else _round(at)}
        out = [(v, "chat.presence", dict(data, username=_as(uid, v))) for v in _viewers(c, uid)]
        subs = _fed_subs(uid)
        h = FC.my_handle(c, uid) if subs else None
    if push_only:
        for v, typ, d in out:
            bus.emit("direct", {"uids": [v], "typ": typ, "data": d})
    for pid in subs:
        _push(pid, [{"h": h, "online": on, "last": None if on else _round(at)}])
    return out


def _hide(uid):
    """Online-Status abgeschaltet: Abos dieser Person beenden (hier: Ereignis ohne Status, andere Server: 'hidden')."""
    _unwatch(uid)
    with _db() as c:
        vs = _viewers(c, uid)
        subs = _fed_subs(uid)
        h = FC.my_handle(c, uid) if subs else None
    with _wl:
        _watch.pop(uid, None)
        _fsub.pop(uid, None)
    for v in vs:
        bus.emit("direct", {"uids": [v], "typ": "chat.presence", "data": {"username": _as(uid, v), "online": False, "last": None}})
    for pid in subs:
        _push(pid, [{"h": h, "hidden": True}])


def _fed_subs(uid):
    now = time.time()
    with _wl:
        s = _fsub.get(uid) or {}
        for pid in [p for p, exp in s.items() if exp <= now]:
            s.pop(pid, None)
        if not s:
            _fsub.pop(uid, None)
        return sorted(s)


def _push(pid, items):
    FC._volatile(pid, {"op": "online.ev", "items": items}, drop=True)


def _subscribe(pid, body):
    """Abo beim Server der Person erneuern (Express-Pool) -> Stand uebernehmen, Wechsel an die Abos hier melden."""
    with _db() as c:
        p = F.peer_by(c, "id", pid)
    if not F.feature(p, "online"):
        return
    status, d = FC.sync_call(p, dict(body, op="online"))
    if status != 200 or not isinstance(d, dict):
        return
    got = d.get("users") if isinstance(d.get("users"), dict) else {}
    items = []
    for it in body["items"]:
        e = got.get(it["h"])
        items.append(dict(e, h=it["h"]) if isinstance(e, dict) else {"h": it["h"], "hidden": True})
    with _db() as c, c:
        _apply(c, p, items)


def _apply(c, peer, items):
    """Stand von Personen des Servers peer uebernehmen; Aenderungen an die Abos hier (Live-Ereignis chat.presence)."""
    now = time.time()
    for it in items[:USERS_MAX]:
        if not isinstance(it, dict):
            continue
        n, host = FC.split_handle(it.get("h"))
        if not n or host != peer["host"]:
            continue
        pr = FC.principal(it["h"])
        if it.get("hidden") is True:
            st = {"online": None, "last": None, "exp": now + WATCH_TTL}
        else:
            last = it.get("last")
            last = _round(float(last)) if isinstance(last, (int, float)) and not isinstance(last, bool) and 0 < last < 2 ** 40 else None
            st = {"online": it.get("online") is True, "last": last, "exp": now + WATCH_TTL}
        old = _rstate.get(pr)
        _rstate[pr] = st
        if old and old.get("online") == st["online"] and old.get("last") == st["last"]:
            continue
        vs = _viewers(c, pr)
        if vs:
            chat._direct(c, vs, "chat.presence", {"username": pr, "online": bool(st["online"]), "last": st["last"]})
    if len(_rstate) > 20000:
        for k in [k for k, v in list(_rstate.items()) if v["exp"] < now]:
            _rstate.pop(k, None)


def op_online(c, peer, d):
    """Server der Person: Abo eines anderen Servers fuer Personen hier (je gemeinsamer Chat geprueft) -> {users, ttl}."""
    if not F.feature(peer, "online"):
        return 403, {"error": "online disabled"}
    if not FC._rate_ok("conl", peer["host"], FED_ONLINE_RATE):
        return 429, {"error": "too many"}
    h, p_ = FC._actor(c, peer, d)
    items = d.get("items")
    if not h or not isinstance(items, list) or len(items) > USERS_MAX:
        return 400, {"error": "bad items"}
    now, out, want = time.time(), {}, {}
    for it in items:
        if not isinstance(it, dict) or not isinstance(it.get("h"), str) or not isinstance(it.get("ref"), str):
            continue
        n, host = FC.split_handle(it["h"])
        if not n or host != F.SELF_HOST:
            continue
        u = F.uid_by_handle(c, n)
        ch = FC.by_ref(c, it["ref"])
        if not u or not ch or ch["v"] != 2:
            continue
        mem = dict(FC.members(c, ch["id"]))
        if mem.get(u) != "ok" or mem.get(p_) != "ok":
            continue
        want[u] = it["h"]
    off = _off(c, KV_ONLINE, list(want))
    on = [u for u in want if u not in off]
    with _wl:
        for u in want:
            if u in on and d.get("leave") is not True:
                _fsub.setdefault(u, {})[peer["id"]] = now + WATCH_TTL
            elif u in _fsub:
                _fsub[u].pop(peer["id"], None)
    if d.get("leave") is True:
        return 200, {"ok": True}
    st = _local_state(c, on)
    for u in on:
        out[want[u]] = st[u]
    return 200, {"users": out, "ttl": WATCH_TTL}


def op_online_ev(c, peer, d):
    """Abonnierender Server: Wechsel von Personen des meldenden Servers (verwerfbar)."""
    if not F.feature(peer, "online"):
        return 403, {"error": "online disabled"}
    if not FC._rate_ok("conl", peer["host"], FED_ONLINE_RATE):
        return 429, {"error": "too many"}
    items = d.get("items")
    if not isinstance(items, list):
        return 400, {"error": "bad items"}
    _apply(c, peer, items)
    return 200, {"ok": True}


def _peer_gone(c, p):
    with _wl:
        for u in list(_fsub):
            _fsub[u].pop(p["id"], None)
            if not _fsub[u]:
                _fsub.pop(u, None)
        for pr in [k for k in list(_rstate) if k.rpartition("@")[2] == p["host"]]:
            _rstate.pop(pr, None)
            _rsub.pop(pr, None)


def chat_flags(c, ch, d):
    """fed_chat.EXTRA_HOOKS: koennen die anderen Server dieses Chats Bestaetigungen bzw. Online-Status (2.4)? Teilnehmer:
    das Heim; Heim: mindestens ein Teilnehmer-Server. Fehlt das Feld (lokaler Chat), gilt true. 2.5: Stufe 3 (lokal) true."""
    if ch["v"] == 3:
        d["receipts_ok"] = d["online_ok"] = True
        return
    if ch["home_peer"] is not None:
        ps = [F.peer_by(c, "id", ch["home_peer"])]
    else:
        ps = FC.participants(c, ch)
    d["receipts_ok"] = any(F.feature(p, "receipts") for p in ps if p)
    d["online_ok"] = any(F.feature(p, "online") for p in ps if p)


# --- Einhaengen (kein zyklischer Import: chat, live, fed_chat kennen dieses Modul nicht) -----------------------------
chat.RECEIPT = take
chat.STATUS_ME = me_extra
live.ONLINE = on_online
FC.OPS.update({"receipt": op_receipt, "online": op_online, "online.ev": op_online_ev})
F.EV2_HANDLERS["chat.receipt"] = _ev_receipt
F.PEER_GONE_HOOKS.append(_peer_gone)
FC.EXTRA_HOOKS.append(chat_flags)
