"""Anrufe ueber Servergrenzen (2.3, Etappe E3): 1:1 aus angenommenen foederierten Direkt-Chats (saga.fed_chat).

- Format saga-call/v2 (wie v1, aber an die foederierten Handles gebunden):
    sig = sign_detached(LP("saga-call/v2/signal", call_id, from_handle, from_device, to_handle, to_device, body), device_sign_sk)
    sealed = B64(crypto_box_seal(UTF8(JSON {"v":2,"body":body,"sig":B64(sig)}), ziel_enc_pk))
  Die Server sehen nur Kennungen und Groessen - keine SDP, keine Kandidaten, keine DTLS-Fingerabdruecke.
- Signalisierung synchron ueber die Foederation (POST /fed/v2/call, harte Frist, KEIN Ausgangskorb): ein Signal, das nicht
  sofort ankommt, ist ohnehin wertlos. Der Server der anrufenden Person fuehrt den Zustand (Klingeln, Annahme, Ende);
  der Server des Gegenuebers spiegelt ihn (eigene Zeile in chat_call mit peer_id) fuer seine Geraete (Klingeln, Push,
  Signale, "verpasst"). Lebenszeichen gehen in beide Richtungen, jede Seite beendet bei Ausfall und meldet es der anderen.
- Jede Seite nutzt ihren eigenen STUN/TURN-Server (GET /api/calls/config des eigenen Servers).
- Nur aus angenommenen Chats: Nachrichtenanfragen klingeln nie. Trennen beendet laufende Anrufe."""
import time

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, calls, chat, fed_chat as FC, federation as F, sharing
from saga.core import _bad, t

router = FC.router                      # gleiche Router-Einbindung wie die Chats (app.py)
OPS = ("ring", "answer", "signal", "end", "decline", "alive")


def peer_ok(p):
    return FC.peer_ok(p) and F.feature(p, "calls2")


def _remote_of(call):
    """Die Person der anderen Instanz in diesem Anruf (Prinzipal) und ob sie anruft."""
    if sharing.is_remote(call["caller"]):
        return call["caller"], True
    return call["callee"], False


# =====================================================================================================
# Eigene Konten (aus saga.calls)
# =====================================================================================================
def start(uid, ch, cid, media, dev, sig):
    """POST /api/calls in einem foederierten Direkt-Chat: beim Server des Gegenuebers klingeln lassen (synchron)."""
    now = time.time()
    with _db() as c, c:
        m = chat._member(c, ch["id"], uid)
        other = next((u for u in chat.members(c, ch["id"]) if sharing.is_remote(u)), None)
        ro = FC._ro_error(c, ch)
        if ro:
            return _bad(ro, 403)
        if not other or not m or m.get("state") != "ok":
            return _bad(t("Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben."), 409)
        h = FC.handle_of(other)
        p = F.peer_by(c, "host", FC.split_handle(h)[1])
        if not peer_ok(p):
            return _bad(t("Anrufe mit {host} sind gerade nicht möglich.", host=p["host"] if p else "?"), 403)
        if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
            return _bad(t("Diesen Anruf gibt es schon."), 409)
        if calls._busy(c, uid):
            return _bad(t("Du telefonierst gerade schon."), 409)
        if not calls._rate("call", uid, calls.CALL_RATE) or not calls._rate("call-h", uid, calls.CALL_RATE_H, 3600) \
                or not calls._rate("pair", uid + "|" + other, calls.PAIR_RATE):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        mh = FC.my_handle(c, uid)
    st, d = FC.sync_call(p, {"op": "ring", "id": cid, "ref": ch["ref"], "from": mh, "from_device": dev, "media": media, "signals": sig},
                         path="/fed/v2/call")
    name = None
    with _db() as c:
        name = FC.label_of(c, other)["name"]
    if st is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"]), 502)
    if st == 409 and isinstance(d, dict) and isinstance(d.get("devices"), list):
        return chat._no_store({"error": t("Die Geräte des Gegenübers haben sich geändert – bitte erneut anrufen."), "devices": d["devices"],
                               "handle": h}, 409)
    if st == 409 and isinstance(d, dict) and d.get("busy"):
        with _db() as c, c:
            c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, peer_id) "
                      "VALUES (?,?,?,?,?,?,'ringing',?,?,?)", (cid, ch["id"], uid, other, dev, media, now, now, p["id"]))
            calls._finish(c, calls._load(c, cid), "busy", now, remote=True)
        return chat._no_store({"ok": False, "busy": True, "error": t("Besetzt – {name} telefoniert gerade.", name=name)}, 409)
    if st == 409 and isinstance(d, dict) and d.get("error") == "request":
        return _bad(t("Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben."), 409)
    if st != 200:
        return _bad(t("{name} ist gerade nicht erreichbar.", name=name), 404 if st == 404 else 409)
    with _db() as c, c:
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, peer_id, nsig) "
                  "VALUES (?,?,?,?,?,?,'ringing',?,?,?,?,?)", (cid, ch["id"], uid, other, dev, media, now, now, now, p["id"], len(sig)))
        calls._OWN.add(cid)
        call = calls._load(c, cid)
        print(f"call: {cid[:8]} klingelt bei {p['host']} ({media}, Chat {ch['id']}, {len(sig)} Geraet(e))", flush=True)
        return chat._no_store({"ok": True, "call": calls._dict(c, call, uid)})


def _send(call, body):
    """Synchron an die andere Instanz des Anrufs -> (status, antwort) bzw. (None, fehler)."""
    with _db() as c:
        p = F.peer_by(c, "id", call["peer_id"])
    if not peer_ok(p):
        return None, "gone"
    return FC.sync_call(p, dict(body, id=call["id"]), path="/fed/v2/call")


def actor(uid):
    with _db() as c:
        return FC.my_handle(c, uid)


def answer(uid, call, dev, sig):
    """Annehmen auf dem Server des Gegenuebers: erst beim Server des Anrufers (er fuehrt den Zustand), dann hier."""
    st, d = _send(call, {"op": "answer", "actor": actor(uid), "device": dev, "signals": sig})
    now = time.time()
    with _db() as c, c:
        cur = calls._load(c, call["id"], lock=True)
        if st != 200 or not cur or cur["state"] != "ringing":
            if cur and cur["state"] == "ringing" and st is not None:
                calls._finish(c, cur, "gone", now, remote=True)
            return chat._no_store({"error": t("Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen."),
                                   "call": calls._dict(c, cur or call, uid)}, 409)
        c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=?, nsig=nsig+1 WHERE id=?",
                  (dev, now, now, now, call["id"]))
        calls._emit(c, [uid], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "device": dev})
        print(f"call: {call['id'][:8]} angenommen (foederiert)", flush=True)
        return chat._no_store({"ok": True, "call": calls._dict(c, calls._load(c, call["id"]), uid)})


def signal(uid, call, dev, sig):
    """Signal an die Geraete der anderen Instanz (synchron, sonst verloren - der Client wiederholt bzw. legt auf)."""
    st, d = _send(call, {"op": "signal", "actor": actor(uid), "device": dev, "signals": sig})
    if st == 409:
        return chat._no_store({"error": t("Der Anruf ist beendet."), "state": "ended"}, 409)
    if st != 200:
        return _bad(t("Die andere Seite ist gerade nicht erreichbar."), 502)
    now = time.time()
    with _db() as c, c:
        c.execute("UPDATE chat_call SET nsig=nsig+?, " + ("seen_a" if uid == call["caller"] else "seen_b") + "=? WHERE id=?",
                  (len(sig), now, call["id"]))
    return chat._no_store({"ok": True, "n": len(sig)})


def alive(uid, call):
    with _db() as c:
        mh = FC.my_handle(c, uid)
    FC._volatile(call["peer_id"], {"op": "alive", "id": call["id"], "actor": mh}, path="/fed/v2/call")


def ended(c, call, reason):
    """Anruf hier beendet (Auflegen, Ablehnen, Zeitablauf): der anderen Instanz melden (nach dem Commit, verwerfbar)."""
    loc = call["callee"] if sharing.is_remote(call["caller"]) else call["caller"]
    try:
        mh = FC.my_handle(c, loc)
    except F.FedError:
        return
    body = {"op": "decline" if reason in ("declined", "busy") and sharing.is_remote(call["caller"]) else "end", "id": call["id"], "actor": mh,
            "reason": reason}
    pid = call["peer_id"]
    c.after_commit(lambda: FC._volatile(pid, body, path="/fed/v2/call"))


# =====================================================================================================
# Server-zu-Server
# =====================================================================================================
def _sealed(m, allowed=None):
    return calls._sealed_map(m, allowed)


def op_ring(c, peer, d):
    cid, media, dev = d.get("id"), d.get("media"), d.get("from_device")
    if not (isinstance(cid, str) and FC._HEX32.fullmatch(cid) and media in calls.MEDIA and isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return 400, {"error": "bad request"}
    h = d.get("from")
    n, host = FC.split_handle(h)
    if not n or host != peer["host"]:
        F.log(c, "call.forged", peer, "ring", throttle=(peer["id"], "cforged"))
        return 403, {"error": "forged"}
    ch = FC._from_home(c, peer, {"ref": d.get("ref")}) if FC.split_ref(d.get("ref"))[1] == peer["host"] else FC._home_chat(c, d)
    p_ = FC.principal(h)
    mem = dict(FC.members(c, ch["id"])) if ch else {}
    if not ch or ch["kind"] != "dm" or p_ not in mem:
        return 404, {"error": "unknown chat"}
    callee = next((u for u in mem if not sharing.is_remote(u)), None)
    if not callee or mem[callee] != "ok" or mem[p_] != "ok" or ch["fstate"] or FC.blocked(c, callee, h):
        return 409, {"error": "request"}                       # Anfragen (und Gesperrte) klingeln nicht
    if not calls.enabled():
        return 403, {"error": "calls disabled"}
    sig = _sealed(d.get("signals"))
    if sig is None:
        return 400, {"error": "bad signals"}
    e = FC.local_entry(c, FC.my_handle(c, callee))
    devs = {x["id"] for x in (e or {}).get("devices", []) if not x.get("revoked")} & calls._active_devices(c, callee)
    if set(sig) != devs:
        return 409, {"error": "devices", "devices": [x for x in (e or {}).get("devices", []) if not x.get("revoked")]}
    if not F._rate.allow("cring:" + h, calls.CALL_RATE) or not F._rate.allow("cringp:" + peer["host"], calls.CALL_RATE * 20):
        return 429, {"error": "too many calls"}
    if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
        return 409, {"error": "duplicate id"}
    now = time.time()
    c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, peer_id) "
              "VALUES (?,?,?,?,?,?,'ringing',?,?,?,?)", (cid, ch["id"], p_, callee, dev, media, now, now, now, peer["id"]))
    calls._OWN.add(cid)
    call = calls._load(c, cid)
    if calls._busy_other(c, callee, cid):
        calls._finish(c, call, "busy", now, remote=True)
        return 409, {"busy": True}
    c.executemany("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?)",
                  [(cid, dev, k, v, now) for k, v in sorted(sig.items())])
    c.execute("UPDATE chat_call SET nsig=? WHERE id=?", (len(sig), cid))
    lab = FC.label_of(c, p_)
    av = sharing.names(c, [p_]).avatar(p_)
    calls._emit(c, [callee], "call.ring", {"call_id": cid, "chat_id": ch["id"], "media": media, "from_device": dev, "ring_s": calls.ring_s(),
                                           "from": {"username": p_, "handle": h, "name": lab["name"], "avatar": av}})
    muted = c.execute("SELECT muted FROM chat_member WHERE chat_id=? AND uid=?", (ch["id"], callee)).fetchone()
    if not (muted and muted[0]):
        bus.fire("push.send", c, uid=callee, kind="call", actor=p_, data={"chat": ch["id"], "call": cid})
    print(f"call: {cid[:8]} klingelt (von {peer['host']}, {media})", flush=True)
    return 200, {"ok": True}


def _call_of(c, peer, d, lock=True):
    """Anruf dieser Instanz, an dem die handelnde Person (actor) als Gegenueber beteiligt ist -> (anruf, prinzipal, ruft_an)."""
    call = calls._load(c, d.get("id"), lock) if isinstance(d.get("id"), str) else None
    h = d.get("actor")
    n, host = FC.split_handle(h)
    if not call or call["peer_id"] != peer["id"] or not n or host != peer["host"]:
        return None, None, None
    rem, is_caller = _remote_of(call)
    if rem != FC.principal(h):
        F.log(c, "call.forged", peer, d.get("op") or "?", throttle=(peer["id"], "cforged2"))
        return None, None, None
    return call, rem, is_caller


def op_answer(c, peer, d):
    call, rem, is_caller = _call_of(c, peer, d)
    dev = d.get("device")
    if not call or is_caller or not (isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return 404, {"error": "not found"}
    now = time.time()
    if call["state"] != "ringing" or call["created"] + calls.ring_s() < now:
        return 409, {"error": "ended", "state": call["state"]}
    sig = _sealed(d.get("signals"), {call["caller_device"]})
    if sig is None:
        return 400, {"error": "bad signals"}
    c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=? WHERE id=?", (dev, now, now, now, call["id"]))
    seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                    (call["id"], dev, call["caller_device"], sig[call["caller_device"]], now)).fetchone()[0]
    c.execute("UPDATE chat_call SET nsig=nsig+1 WHERE id=?", (call["id"],))
    calls._emit(c, [call["caller"]], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": call["caller_device"],
                                                     "sealed": sig[call["caller_device"]]})
    calls._emit(c, [call["caller"]], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "device": dev})
    return 200, {"ok": True}


def op_signal(c, peer, d):
    call, rem, is_caller = _call_of(c, peer, d)
    dev = d.get("device")
    if not call:
        return 404, {"error": "not found"}
    if call["state"] == "ended":
        return 409, {"error": "ended"}
    if is_caller:                                  # Anrufer der anderen Instanz -> Geraete unseres Kontos
        if dev != call["caller_device"]:
            return 403, {"error": "device"}
        allowed = {call["callee_device"]} if call["callee_device"] else calls._active_devices(c, call["callee"])
        to_uid, seen = call["callee"], "seen_a"
    else:
        if call["state"] != "active" or dev != call["callee_device"]:
            return 403, {"error": "device"}
        allowed, to_uid, seen = {call["caller_device"]}, call["caller"], "seen_b"
    sig = _sealed(d.get("signals"), allowed)
    if sig is None:
        return 400, {"error": "bad signals"}
    if call["nsig"] + len(sig) > calls.SIGNALS_MAX or not F._rate.allow("csig:" + dev, calls.SIG_RATE):
        return 429, {"error": "too many signals"}
    now = time.time()
    for k, v in sorted(sig.items()):
        seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                        (call["id"], dev, k, v, now)).fetchone()[0]
        calls._emit(c, [to_uid], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": k, "sealed": v})
    c.execute(f"UPDATE chat_call SET nsig=nsig+?, {seen}=? WHERE id=?", (len(sig), now, call["id"]))
    return 200, {"ok": True}


def op_end(c, peer, d):
    call, rem, is_caller = _call_of(c, peer, d)
    if not call:
        return 200, {"ok": True}
    if call["state"] == "ended":
        return 200, {"ok": True, "already": True}
    reason = d.get("reason") if d.get("reason") in ("hangup", "declined", "busy", "cancelled", "failed", "lost", "timeout", "gone") else "hangup"
    if call["state"] == "ringing" and reason == "hangup":      # Auflegen waehrend es klingelt: abgebrochen bzw. abgelehnt
        reason = "cancelled" if is_caller else "declined"
    call["_connected"] = call["state"] == "active"
    calls._finish(c, call, reason, time.time(), by=rem, remote=True)
    return 200, {"ok": True}


def op_alive(c, peer, d):
    call, rem, is_caller = _call_of(c, peer, d, lock=False)
    if call and call["state"] != "ended":
        c.execute("UPDATE chat_call SET " + ("seen_a" if is_caller else "seen_b") + "=? WHERE id=?", (time.time(), call["id"]))
        return 200, {"state": call["state"]}
    return 200, {"state": "ended"}


HANDLERS = {"ring": op_ring, "answer": op_answer, "signal": op_signal, "end": op_end, "decline": op_end, "alive": op_alive}


@router.post("/fed/v2/call")
async def fed_call(request: Request):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not peer_ok(inp.peer):
        return F._signed(inp, 403, {"error": "calls disabled"})
    d = inp.data if isinstance(inp.data, dict) else {}
    if d.get("op") not in HANDLERS:
        return F._signed(inp, 400, {"error": "bad op"})

    def run():
        with _db() as c, c:
            return HANDLERS[d["op"]](c, inp.peer, d)
    status, out = await run_in_threadpool(run)
    return F._signed(inp, status, out)


def _peer_gone(c, p):
    """Verbindung getrennt: laufende Anrufe mit der Instanz enden sofort."""
    now = time.time()
    for r in c.execute(f"SELECT {calls.C_COLS} FROM chat_call WHERE peer_id=? AND state<>'ended' FOR UPDATE", (p["id"],)).fetchall():
        call = calls._row(r)
        call["_connected"] = call["state"] == "active"
        calls._finish(c, call, "gone", now, remote=True)


F.PEER_GONE_HOOKS.append(_peer_gone)
calls.FED = __import__("sys").modules[__name__]
