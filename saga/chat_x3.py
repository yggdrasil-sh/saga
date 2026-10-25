"""Chat-Grundlagen (2.5, Konzept B1-B3): Reaktionen, Nachricht bearbeiten, Sprachnachrichten - nur in Chats im Format
saga-chat/v2 (Stufe 3 und ueber Servergrenzen), dort nur, wenn alle beteiligten Server 'chat3' koennen (Chat-Feld x3_ok),
nur angenommen und nicht schreibgeschuetzt. Format der Reaktionen: saga/chat_v3.py (Vektoren tests/vectors/chat_v3.json).

- Reaktionen: eine je Person (Handle) und Nachricht, die neueste gilt ("" = zurueckgenommen). Ein Ciphertext je Reaktion,
  verschluesselt mit einem aus dem Nachrichtenschluessel abgeleiteten Schluessel; der Server sieht wer/wann/worauf, nicht
  womit, und prueft die Signatur des Geraets. Kein Push. Live: chat.react je Chat hoechstens einmal je REACT_GAP Sekunden
  gebuendelt (bei vielen nur more + mids, dann GET /api/chats/{id}/reactions). Ueber Servergrenzen wie Gelesen: Auftrag
  'react' an das Heim, Ereignis 'chat.react' vom Heim an die anderen Server.
- Bearbeiten: eine normale v2-Nachricht kind 'edit' mit edit_of (Klartext) und Inhalt {"edit": <msg_id>, "t": <text>},
  versiegelt fuer die aktuellen Empfaenger. Nur der Absender, hoechstens EDIT_MAX-mal, bis EDIT_WINDOW nach dem Original
  (Serverzeit created, ueber Servergrenzen die des Heims), nicht fuer KI-Antworten, ohne Anhaenge/Antwort. Kein Verlauf:
  der Server behaelt nur die neueste Bearbeitung (chat_msg.edit_seq am Original), aeltere samt Schluesseln weg. GET
  .../messages liefert sie am Original (edit, edited_at, edit_n), nie als eigenen Eintrag; Live chat.edited; kein Push,
  kein Ungelesen. Ueber Servergrenzen: Auftrag 'edit' an das Heim, Ereignis 'chat.msg.edit' (Kette des Chats, ohne fseq).
- Sprachnachrichten: ein Anhang (saga-file/v2, Ogg/Opus) mit voice/dur/wf im verschluesselten Inhalt - fuer den Server ein
  normaler Anhang. Hier nur die Grenzen fuer die Clients (GET /api/chat/me: limits.voice_*)."""
import threading
import time

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import bus, chat, chat_v3 as V3, fed_chat as FC, fed_keys as FK, federation as F, sharing
from saga.core import _bad, _body, t

router = APIRouter()

REACT_RATE = 120                   # Reaktionen je Konto und Minute
REACT_PER_MSG = 50                 # hoechstens so viele je Nachricht in GET .../messages
REACT_INLINE = 20                  # mehr in einem Buendel: nur der Hinweis (more, mids)
REACT_GAP = 1.0                    # Sekunden: chat.react je Chat hoechstens so oft
FED_REACT_RATE = 1200              # Reaktionen anderer Server je Instanz und Minute
MIDS_MAX = 100                     # GET /api/chats/{id}/reactions?mids=
VOICE_MAX_S = 15 * 60              # Sprachnachricht: hoechstens 15 Minuten
VOICE_KBPS = 24                    # Opus mono 48 kHz, 24 kbit/s VBR, 20-ms-Rahmen, Ogg
VOICE_MAX_BYTES = 4 * 1024 * 1024  # mit Reserve fuer VBR und Ogg-Kopf (15 min ~ 2,7 MB)
_lock = threading.Lock()
_pend = {}                         # chat_id -> [items]
_last = {}                         # chat_id -> letzte Sendung (monotonic)


# =====================================================================================================
# Wann gibt es die Funktionen?
# =====================================================================================================
def x3_ok(c, ch, state="ok"):
    """Reaktionen/Bearbeiten/Sprachnachrichten moeglich? Einstellung chat_v3 an (Standard aus), Format v2, angenommen,
    nicht schreibgeschuetzt, alle Server chat3."""
    if not x3_on():
        return False
    if not chat.hfmt(ch) or (state or "ok") != "ok" or ch.get("fstate"):
        return False
    if ch["v"] == 3:
        return True
    ps = [F.peer_by(c, "id", ch["home_peer"])] if ch["home_peer"] is not None else FC.participants(c, ch)
    return all(p and F.feature(p, "chat3") for p in ps)


def chat_flags(c, ch, d):
    """fed_chat.EXTRA_HOOKS: x3_ok im Chat-Objekt; ist es false, sagt x3_why warum (fuer den Hinweis in den Clients)."""
    d["x3_ok"] = x3_ok(c, ch, d.get("state")) and not d.get("readonly")
    if not d["x3_ok"]:
        d.update(_why_v2(c, ch, d))


def _why_v2(c, ch, d):
    """Warum (noch) keine Reaktionen in einem Chat im Format v2 -> {x3_why, x3_hosts?}."""
    if not x3_on():
        return {"x3_why": "setting"}
    if (d.get("state") or "ok") != "ok":
        return {"x3_why": "pending"}                 # Nachrichtenanfrage noch nicht angenommen
    if d.get("readonly") or ch.get("fstate"):
        return {"x3_why": "readonly"}
    if ch["home_peer"] is not None:
        p = F.peer_by(c, "id", ch["home_peer"])
        hosts = [] if p and F.feature(p, "chat3") else [(p or {}).get("host") or d.get("home") or "?"]
    else:
        hosts = [p["host"] for p in FC.participants(c, ch) if not F.feature(p, "chat3")]
    return {"x3_why": "server", "x3_hosts": sorted(set(hosts))}


def v1_flags(c, uid, ch, mem, d):
    """Lokaler Chat v1 (saga.chat._chat_dict): keine Reaktionen. x3_why: 'setting' (Stufe 3 aus), 'devices' (Umstellung
    wartet auf Geraete; x3_wait = {mine: [Namen EIGENER Geraete], others: Anzahl anderer Personen, until: Stichtag}; wartet
    nichts mehr, kommt sie beim naechsten stuendlichen Lauf: mine [] und others 0). 'pending' bleibt der Nachrichtenanfrage
    vorbehalten (so lesen es die Apps)."""
    d["x3_ok"] = False
    if not x3_on():
        d["x3_why"] = "setting"
        return
    loc = sorted({u for u in mem if u and not sharing.is_remote(u)})
    now = time.time()
    _, until = chat.v3_deadline()
    if not loc:
        d.update(x3_why="devices", x3_wait={"mine": [], "others": 0, "until": int(until)})
        return
    ph = ",".join("?" * len(loc))
    old = c.execute(f"SELECT uid, name FROM chat_device WHERE uid IN ({ph}) AND revoked_at IS NULL AND COALESCE(last_seen, created)>? "
                    "AND caps NOT LIKE '%\"chat3\"%' ORDER BY COALESCE(last_seen, created) DESC", (*loc, now - chat.ACTIVE_S)).fetchall()
    cert = {r[0] for r in c.execute(f"SELECT f.uid FROM chat_fed_cert f JOIN chat_identity i ON i.uid=f.uid AND i.ident_pub=f.ident_pub "
                                    f"WHERE f.uid IN ({ph})", tuple(loc))}
    nocert = set(loc) - cert
    if not nocert and (not old or now >= until):         # Umstellung beim naechsten Lauf
        old = []
    mine = [(n or "").strip()[:80] or t("Gerät ohne Namen") for u, n in old if u == uid][:20]
    others = len(({u for u, _ in old} | nocert) - {uid})
    d["x3_why"] = "devices"
    d["x3_wait"] = {"mine": mine, "others": others, "until": int(until)}


def x3_on():
    """Reaktionen/Bearbeiten/Sprachnachrichten haengen an der Einstellung chat_v3 (2.5, Standard aus)."""
    return bool(auth.settings().get("chat_v3"))


def me_extra(orig):
    def fn(c, uid):
        out = dict(orig(c, uid)) if orig else {}
        if not x3_on():
            return out
        out["features"] = list(out.get("features") or []) + ["react", "edit", "voice"]
        out["x3_limits"] = {"edit_window": V3.EDIT_WINDOW, "edit_max": V3.EDIT_MAX, "emoji_max": V3.EMOJI_MAX, "react_ct_max": V3.REACT_CT_MAX,
                            "react_rate": REACT_RATE, "voice_max_s": VOICE_MAX_S, "voice_kbps": VOICE_KBPS, "voice_max_bytes": VOICE_MAX_BYTES}
        return out
    return fn


def _peers3(c, ch, skip=None):
    """Teilnehmer-Server mit chat3 (Heim: Fan-out)."""
    return [p for p in FC.participants(c, ch) if F.feature(p, "chat3") and p["id"] != skip]


# =====================================================================================================
# Reaktionen
# =====================================================================================================
def _r_in(b):
    """{dev, ts, ct, sig} pruefen (Form) -> dict oder None."""
    if not isinstance(b, dict):
        return None
    dev, ct, sig = b.get("dev"), b.get("ct"), b.get("sig")
    ts = b.get("ts")
    if not (isinstance(dev, str) and chat._HEX32.fullmatch(dev) and isinstance(ct, str) and isinstance(sig, str)):
        return None
    if not isinstance(ts, int) or isinstance(ts, bool) or not 0 < ts < 2 ** 53 or len(ct) > V3.REACT_CT_MAX or chat.b64d(sig, 64) is None:
        return None
    raw = chat.b64d(ct)
    if raw is None or len(raw) < 24 + 16 + 2:
        return None
    return {"dev": dev, "ts": ts, "ct": ct, "sig": sig}


def _upsert(c, ch, seq, mid, r):
    """Neueste Reaktion je Person speichern (aeltere ts verdraengt nichts) -> gespeichert?"""
    n = c.execute("INSERT INTO chat_reaction (msg_seq, reactor, dev, ts, ct, sig, at) VALUES (?,?,?,?,?,?,?) ON CONFLICT (msg_seq, reactor) "
                  "DO UPDATE SET dev=EXCLUDED.dev, ts=EXCLUDED.ts, ct=EXCLUDED.ct, sig=EXCLUDED.sig, at=EXCLUDED.at "
                  "WHERE EXCLUDED.ts >= chat_reaction.ts", (seq, r["by"], r["dev"], r["ts"], r["ct"], r["sig"], time.time())).rowcount
    if n:
        item = dict(r, id=mid)
        cid = ch["id"]
        c.after_commit(lambda: _schedule(cid, item))
    return bool(n)


def _schedule(cid, item):
    with _lock:
        p = _pend.get(cid)
        if p is not None:
            p.append(item)
            return
        _pend[cid] = [item]
        wait = _last.get(cid, -REACT_GAP) + REACT_GAP - time.monotonic()
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
        items = _pend.pop(cid, [])
        _last[cid] = time.monotonic()
    if not items:
        return
    try:
        with _db() as c:
            uids = FC.local_members(c, cid)
        by = {}
        for it in items:                           # je Person und Nachricht nur die neueste
            k = (it["id"], it["by"])
            if k not in by or it["ts"] >= by[k]["ts"]:
                by[k] = it
        its = list(by.values())
        data = {"chat_id": cid, "items": its} if len(its) <= REACT_INLINE else {"chat_id": cid, "items": [], "more": True,
                                                                              "mids": sorted({x["id"] for x in its})}
        if uids:
            bus.emit("direct", {"uids": uids, "typ": "chat.react", "data": data})
    except Exception as ex:  # noqa: BLE001 - Datenbank gerade weg: GET liefert sie trotzdem
        print(f"chat_x3: chat.react {cid}: {type(ex).__name__}: {ex}", flush=True)


def _target(c, ch, mid):
    """Nachricht, auf die man reagieren kann (normale Nachricht des Chats, nicht abgelaufen) -> seq oder None."""
    if not isinstance(mid, str) or not chat._HEX32.fullmatch(mid):
        return None
    r = c.execute("SELECT seq FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg' AND (expires_at IS NULL OR expires_at>?)",
                  (mid, ch["id"], time.time())).fetchone()
    return r[0] if r else None


@router.put("/api/chats/{cid}/messages/{mid}/reaction")
async def api_react(cid: int, mid: str, request: Request):
    """Reaktion setzen bzw. zuruecknehmen: {dev, ts, ct, sig} (saga-chat/v3, siehe Moduldoku). Eine je Person und Nachricht."""
    uid, err = chat._me(request)
    if err:
        return err
    r = _r_in(await _body(request))
    if not r:
        return _bad(t("ungültige Angabe"))
    return await run_in_threadpool(react, uid, cid, mid, r)


def react(uid, cid, mid, r):
    if abs(r["ts"] / 1000 - time.time()) > chat.SKEW:
        return _bad(t("Die Uhrzeit dieses Geräts weicht zu stark ab."), 400)
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if not x3_ok(c, ch, m.get("state")) or FC._ro_error(c, ch):
            return _bad(t("Reaktionen gibt es in diesem Chat (noch) nicht."), 409)
        seq = _target(c, ch, mid)
        if not seq:
            return _bad("not found", 404)
        d = c.execute("SELECT sign_pub FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (r["dev"], uid)).fetchone()
        if not d:
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        r = dict(r, by=FC.my_handle(c, uid))
        if not V3.verify_reaction(d[0], ch["ref"], mid, r):
            return _bad(t("Ungültige Signatur."), 400)
        if not chat._rate("react", uid, REACT_RATE):
            return _bad(t("zu schnell"), 429)
        _upsert(c, ch, seq, mid, r)
        if chat.is_fed(ch):
            mine = FC.local_entry(c, r["by"])
            if ch["home_peer"] is None:
                for p in _peers3(c, ch):
                    F._enqueue(c, p["id"], {"type": "chat.react", "ref": ch["ref"], "mid": mid, "r": r, "sender_keys": mine},
                               chain=FC.chain_down(ch["ref"]))
            else:
                F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "react", "ref": ch["ref"], "mid": mid, "r": r,
                                                                             "actor": r["by"], "sender_keys": mine}},
                           chain=FC.chain_up(ch["ref"]))
    return chat._no_store({"ok": True})


def _entry_of(c, peer, h, sent):
    """Schluessel einer Person fuer die Pruefung: eigene Konten hier, Personen des sendenden Servers aus seiner Angabe
    (selbst geprueft), dritte aus dem Zwischenspeicher bzw. der Angabe des Heims."""
    n, host = FC.split_handle(h)
    if host == F.SELF_HOST:
        return FC.local_entry(c, h)
    e = FC._cached(c, h)
    if host == peer["host"] or not e:
        e2 = FK.check_entry(h, sent)
        if e2 and (not e or e2["ident_pub"] == e.get("ident_pub")):
            return e2
    return e


def _r_fed(d):
    r = _r_in(d.get("r") if isinstance(d.get("r"), dict) else None)
    by = (d.get("r") or {}).get("by") if isinstance(d.get("r"), dict) else None
    return dict(r, by=by) if r and isinstance(by, str) else None


def _r_check(c, peer, ch, d):
    """Reaktion eines anderen Servers pruefen -> (r, seq, prinzipal) oder None."""
    r, mid = _r_fed(d), d.get("mid")
    if not r:
        return None
    u = FC._sender_of(c, ch, r["by"])
    if not u or dict(FC.members(c, ch["id"])).get(u) != "ok":
        return None
    seq = _target(c, ch, mid)
    e = _entry_of(c, peer, r["by"], d.get("sender_keys"))
    dv = next((x for x in (e or {}).get("devices") or [] if x.get("id") == r["dev"] and not x.get("revoked")), None)
    if not seq or not dv or not V3.verify_reaction(dv["sign_pub"], ch["ref"], mid, r):
        F.log(c, "chat.badreact", peer, str(mid)[:8], throttle=(peer["id"], "badreact"))
        return None
    return r, seq, u


def op_react(c, peer, d):
    """Heim: Reaktion einer Person des anfragenden Servers."""
    if not F.feature(peer, "chat3"):
        return 403, {"error": "chat3 disabled"}
    if not FC._rate_ok("creact", peer["host"], FED_REACT_RATE):
        return 429, {"error": "too many"}
    ch = FC._home_chat(c, d, lock=True)
    h, p_ = FC._actor(c, peer, d)
    if not ch or not h or not x3_ok(c, ch):
        return 200, {"ok": True}
    got = _r_check(c, peer, ch, d)
    if not got or got[0]["by"] != h:
        return 403, {"error": "bad reaction"}
    r, seq, _ = got
    _upsert(c, ch, seq, d["mid"], r)
    for p in _peers3(c, ch, skip=peer["id"]):
        F._enqueue(c, p["id"], {"type": "chat.react", "ref": ch["ref"], "mid": d["mid"], "r": r, "sender_keys": d.get("sender_keys")},
                   chain=FC.chain_down(ch["ref"]))
    return 200, {"ok": True}


def _ev_react(c, peer, ev):
    """Teilnehmer-Server: Reaktion vom Heim."""
    if not FC.peer_ok(peer) or not F.feature(peer, "chat3"):
        return "ignored"
    if not FC._rate_ok("creact", peer["host"], FED_REACT_RATE):
        return "retry"
    ch = FC._from_home(c, peer, ev)
    if not ch or not x3_ok(c, ch):                     # Einstellung chat_v3 aus (o. a.): weder speichern noch ausliefern
        return "ignored"
    got = _r_check(c, peer, ch, ev)
    if not got:
        return "ignored"
    _upsert(c, ch, got[1], ev["mid"], got[0])
    return "ok"


@router.get("/api/chats/{cid}/reactions")
def api_reactions(cid: int, request: Request, mids: str = ""):
    """Reaktionen zu Nachrichten (?mids=a,b, hoechstens 100): {reactions: {mid: [{by, dev, ts, ct, sig}]}}."""
    uid, err = chat._me(request)
    if err:
        return err
    want = [x for x in dict.fromkeys(x.strip() for x in mids.split(",")) if chat._HEX32.fullmatch(x)][:MIDS_MAX]
    with _db() as c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        out = {}
        if want and chat.hfmt(ch):
            seqs = {r[0]: r[1] for r in c.execute(f"SELECT seq, id FROM chat_msg WHERE chat_id=? AND kind='msg' AND id IN ({','.join('?' * len(want))})",
                                                  (ch["id"], *want)).fetchall()}
            out = _rx(c, seqs)
    return chat._no_store({"chat_id": cid, "reactions": out})


def _rx(c, seqs):
    """{seq: id} -> {id: [reaktionen]} (hoechstens REACT_PER_MSG je Nachricht, aelteste zuerst)."""
    if not seqs:
        return {}
    out = {}
    for s, by, dev, ts, ct, sig in c.execute(f"SELECT msg_seq, reactor, dev, ts, ct, sig FROM chat_reaction WHERE msg_seq IN "
                                             f"({','.join('?' * len(seqs))}) ORDER BY msg_seq, at", tuple(seqs)).fetchall():
        lst = out.setdefault(seqs[s], [])
        if len(lst) < REACT_PER_MSG:
            lst.append({"by": by, "dev": dev, "ts": ts, "ct": ct, "sig": sig})
    return out


# =====================================================================================================
# Bearbeiten
# =====================================================================================================
def _orig(c, ch, mid, lock=False):
    r = c.execute("SELECT seq, sender_uid, sender_handle, created, edit_n, edit_seq, ai, kind, expires_at FROM chat_msg WHERE id=? AND chat_id=?"
                  + (" FOR UPDATE" if lock else ""), (mid, ch["id"])).fetchone()
    return dict(zip(("seq", "sender_uid", "sender_handle", "created", "edit_n", "edit_seq", "ai", "kind", "expires_at"), r)) if r else None


def _edit_why(c, ch, o, sender, sender_handle, now):
    """Darf sender (Konto oder Prinzipal) das Original o bearbeiten? -> None oder Fehlertext."""
    if not o or o["kind"] != "msg" or (o["expires_at"] and o["expires_at"] <= now):
        return "not found"
    if o["sender_uid"] != sender or not o["sender_handle"] or o["sender_handle"] != sender_handle:
        return t("Nur eigene Nachrichten lassen sich bearbeiten.")
    if o["ai"] or c.execute("SELECT 1 FROM chat_ai_reply WHERE msg_id=(SELECT id FROM chat_msg WHERE seq=?)", (o["seq"],)).fetchone():
        return t("KI-Antworten lassen sich nicht bearbeiten.")
    if now - (o["created"] or 0) > V3.EDIT_WINDOW:
        return t("Nachrichten lassen sich nur bis 24 Stunden nach dem Senden bearbeiten.")
    if (o["edit_n"] or 0) >= V3.EDIT_MAX:
        return t("Diese Nachricht wurde schon {n}-mal bearbeitet.", n=V3.EDIT_MAX)
    return None


def _edit_in(b):
    m = FC.msg_in(dict(b, kind="msg")) if isinstance(b, dict) else None
    eo = b.get("edit_of") if isinstance(b, dict) else None
    if not m or not (isinstance(eo, str) and chat._HEX32.fullmatch(eo)) or m["reply_to"] or m["ai"]:
        return None
    m["edit_of"] = eo
    return m


def store_edit(c, ch, o, m, sender, lkeys, all_keys=None):
    """Bearbeitung speichern (in der Transaktion, Original gesperrt): neue Zeile kind 'edit', die vorige samt Schluesseln weg,
    Original zeigt auf die neueste. -> seq"""
    now = time.time()
    seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size, "
                    "sender_handle, edit_of) VALUES (?,?,'edit',?,?,?,?,?,?,?,NULL,?,?,?) RETURNING seq",
                    (m["id"], ch["id"], sender, m["sender_device"], m["ts"], now, o["expires_at"], m["body_ct"], m["sig"], len(m["body_ct"]),
                     m["sender_handle"], m["edit_of"])).fetchone()[0]
    rows = dict(lkeys)
    if all_keys:
        rows.update({k: v for k, v in all_keys.items() if not k.endswith("@" + F.SELF_HOST)})
    c.executemany("INSERT INTO chat_msg_key (msg_seq, recipient, sealed) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                  [(seq, k, v) for k, v in sorted(rows.items())])
    if o["edit_seq"]:
        c.execute("DELETE FROM chat_msg WHERE seq=? AND kind='edit'", (o["edit_seq"],))      # kein Verlauf
    c.execute("UPDATE chat_msg SET edit_seq=?, edited_at=?, edit_n=edit_n+1 WHERE seq=?", (seq, now, o["seq"]))
    chat._direct(c, FC.local_members(c, ch["id"]), "chat.edited", {"chat_id": ch["id"], "id": m["edit_of"], "seq": o["seq"], "edit_seq": seq})
    return seq


def send_edit(uid, cid, b):
    """POST /api/chats/{cid}/messages mit kind 'edit' (Format v2, siehe Moduldoku)."""
    m = _edit_in(b)
    keys = b.get("keys") if isinstance(b, dict) else None
    if not m or b.get("files") or b.get("ai_reply") or not FC._keys_ok(keys):
        return _bad(t("ungültige Angabe"))
    now = time.time()
    if abs(m["ts"] / 1000 - now) > chat.SKEW:
        return _bad(t("Die Uhrzeit dieses Geräts weicht zu stark ab."), 400)
    with _db() as c:
        ch, mm, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if not x3_ok(c, ch, mm.get("state")):
            return _bad(t("Bearbeiten gibt es in diesem Chat (noch) nicht."), 409)
        ro = FC._ro_error(c, ch)
        if ro:
            return _bad(ro, 403)
        mh = FC.my_handle(c, uid)
        m["sender_handle"] = mh
        why = _edit_why(c, ch, _orig(c, ch, m["edit_of"]), uid, mh, now)
        if why:
            return _bad(why, 404 if why == "not found" else 403)
        mine = FC.local_entry(c, mh)
        if not c.execute("SELECT 1 FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (m["sender_device"], uid)).fetchone() \
                or not mine or not FC.sig_ok(mine, ch["ref"], m):
            return _bad(t("Ungültige Signatur."), 400)
        if c.execute("SELECT COUNT(*) FROM chat_msg WHERE sender_uid=? AND created>?", (uid, now - 60)).fetchone()[0] >= chat.MSG_RATE:
            return _bad(t("Zu viele Nachrichten in kurzer Zeit – bitte kurz warten."), 429)
        if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m["id"],)).fetchone():
            return _bad(t("Diese Nachricht gibt es schon."), 409)
        handles = [FC.member_handle(c, u) for u in mem]
    ents = FC.entries(handles, uid=uid)                 # Netzwerk: Schluessel der anderen (Zwischenspeicher)
    if set(keys) != FC.want_of(ents):
        return chat._no_store({"error": t("Die Mitglieder oder ihre Geräte haben sich geändert – bitte erneut senden."), "handles": handles}, 409)
    home = ch["home_peer"] is None
    with _db() as c, c:
        ch = chat._load(c, ch["id"])
        o = _orig(c, ch, m["edit_of"], lock=True)
        why = _edit_why(c, ch, o, uid, mh, time.time())
        if why:
            return _bad(why, 404 if why == "not found" else 403)
        if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m["id"],)).fetchone():
            return _bad(t("Diese Nachricht gibt es schon."), 409)
        seq = store_edit(c, ch, o, m, uid, FC.local_keys(c, ch, keys), keys if home else None)
        if chat.is_fed(ch):
            ev = {"type": "chat.msg.edit", "msg": m, "sender_keys": mine}
            if home:
                for p in _peers3(c, ch):
                    F._enqueue(c, p["id"], dict(ev, ref=ch["ref"], keys=FC.keys_for_host(keys, p["host"])), chain=FC.chain_down(ch["ref"]))
            else:
                F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "edit", "ref": ch["ref"], "msg": m, "keys": keys,
                                                                             "sender_keys": mine}}, chain=FC.chain_up(ch["ref"]))
        n = o["edit_n"] + 1
    print(f"chat: Bearbeitung in Chat {ch['id']} ({n}.)", flush=True)
    return chat._no_store({"ok": True, "seq": seq, "edit_n": n})


def _edit_fed(c, peer, ch, d, home):
    """Bearbeitung eines anderen Servers pruefen und speichern -> (status, antwort)."""
    m = _edit_in(d.get("msg") if isinstance(d.get("msg"), dict) else None)
    keys = d.get("keys")
    if not m or not FC._keys_ok(keys):
        return 400, {"error": "bad edit"}
    h = m["sender_handle"]
    sender = FC._sender_of(c, ch, h)
    n, host = FC.split_handle(h)
    if not sender or (home and host != peer["host"]):
        F.log(c, "chat.forged", peer, f"edit sender {str(h)[:40]}", throttle=(peer["id"], "fedit"))
        return 403, {"error": "forged sender"}
    if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m["id"],)).fetchone():
        return 200, {"ok": True, "dup": True}
    o = _orig(c, ch, m["edit_of"], lock=True)
    why = _edit_why(c, ch, o, sender, h, time.time()) if home else (None if o and o["sender_handle"] == h and o["kind"] == "msg" else "not found")
    if why:
        return 403, {"error": "edit refused"}
    e = _entry_of(c, peer, h, d.get("sender_keys"))
    if not FC.sig_ok(e, ch["ref"], m, revoked_ok=not home):
        F.log(c, "chat.badsig", peer, m["id"][:8], throttle=(peer["id"], "badsig"))
        return 403, {"error": "bad signature"}
    if home:                                       # Empfaenger exakt (aktuelle Mitglieder), wie beim Senden
        ents = {}
        for u, _ in FC.members(c, ch["id"]):
            hh = FC.member_handle(c, u)
            ents[hh] = e if hh == h else (FC.local_entry(c, hh) if not sharing.is_remote(u) else FC._cached(c, hh))
        if set(keys) != FC.want_of(ents):
            return 409, {"error": "recipients", "handles": sorted(ents)}
    store_edit(c, ch, o, m, sender, FC.local_keys(c, ch, keys), keys if home else None)
    if home:
        for p in _peers3(c, ch, skip=peer["id"]):
            F._enqueue(c, p["id"], {"type": "chat.msg.edit", "ref": ch["ref"], "msg": m, "keys": FC.keys_for_host(keys, p["host"]),
                                    "sender_keys": d.get("sender_keys")}, chain=FC.chain_down(ch["ref"]))
    return 200, {"ok": True}


def op_edit(c, peer, d):
    if not F.feature(peer, "chat3"):
        return 403, {"error": "chat3 disabled"}
    ch = FC._home_chat(c, d, lock=True)
    if not ch:
        return 404, {"error": "gone"}
    if not x3_ok(c, ch):
        return 409, {"error": "chat3 not possible"}
    return _edit_fed(c, peer, ch, d, home=True)


def _ev_edit(c, peer, ev):
    if not FC.peer_ok(peer) or not F.feature(peer, "chat3"):
        return "ignored"
    ch = FC._from_home(c, peer, ev, lock=True)
    if not ch or not x3_ok(c, ch):                     # Einstellung chat_v3 aus (o. a.): weder speichern noch ausliefern
        return "ignored"
    st, _ = _edit_fed(c, peer, ch, ev, home=False)
    return "ok" if st == 200 else "ignored"


# =====================================================================================================
# Nachrichtenliste: Reaktionen und neueste Bearbeitung am Original
# =====================================================================================================
def messages_extra(c, uid, ch, msgs, device):
    """chat.X3 (GET .../messages): rx je Nachricht, edit (vollstaendige Bearbeitung mit Schluessel), edited_at, edit_n."""
    seqs = {m["seq"]: m["id"] for m in msgs if m.get("kind") == "msg"}
    if not seqs:
        return
    rx = _rx(c, seqs)
    ed = {r[0]: r[1:] for r in c.execute(f"SELECT seq, edit_seq, edited_at, edit_n FROM chat_msg WHERE seq IN ({','.join('?' * len(seqs))}) "
                                         "AND edit_seq IS NOT NULL", tuple(seqs)).fetchall()}
    edits = {}
    if ed:
        es = [v[0] for v in ed.values()]
        rows = chat._msgs(c, uid, ch["id"], device, f"AND m.kind='edit' AND m.seq IN ({','.join('?' * len(es))})", tuple(es), len(es))
        nm = chat.names(c, [r[3] for r in rows])
        for r in rows:
            e = chat._msg_dict(r, nm)
            if e.get("sender") and e["sender"]["username"] != uid and e.get("sender_handle") and not sharing.is_remote(e["sender"]["username"]):
                e["sender"] = dict(e["sender"], username=e["sender_handle"])
            edits[r[0]] = e
    for m in msgs:
        if m.get("kind") != "msg":
            continue
        if m["id"] in rx:
            m["rx"] = rx[m["id"]]
        x = ed.get(m["seq"])
        if x:
            m.update(edited_at=x[1], edit_n=x[2])
            e = edits.get(x[0])
            if e:
                m["edit"] = dict(e, edit_of=m["id"])


def _drop_edits(c, ch, mid):
    """Original geloescht (saga.fed_chat.drop_msg): Bearbeitungen gehen mit."""
    c.execute("DELETE FROM chat_msg WHERE chat_id=? AND kind='edit' AND edit_of=?", (ch["id"], mid))


# --- Einhaengen ---------------------------------------------------------------------------------------------------
chat.X3 = __import__("sys").modules[__name__]
chat.STATUS_ME = me_extra(chat.STATUS_ME)
FC.EXTRA_HOOKS.append(chat_flags)
FC.DROP_HOOKS.append(_drop_edits)
FC.OPS.update({"react": op_react, "edit": op_edit})
F.EV2_HANDLERS["chat.react"] = _ev_react
F.EV2_HANDLERS["chat.msg.edit"] = _ev_edit
