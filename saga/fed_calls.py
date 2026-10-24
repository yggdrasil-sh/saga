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
- Nur aus angenommenen Chats: Nachrichtenanfragen klingeln nie. Trennen beendet laufende Anrufe.
- 2.4 (E0, saga-call v3 im Body): ring/answer tragen optional "caps" (Hinweis, 2.3-Server ignorieren es); weitere
  offer/answer laufen als "signal" in beide Richtungen, begrenzt wie lokal (calls.sig_budget, Rate je Geraet).
- 2.4 (E4/E5, Cap "calls3", Vertrag docs/APP-API-FED.md Abschnitt 7.2): Raum-Modell ueber Servergrenzen. Den Anruf fuehrt
  ein Server (lead): 1:1 der Server der anrufenden Person (wie 2.3), Gruppe das Heim des Chats. Er haelt die massgebliche
  Liste der Endpunkte (je Geraet), die anderen Server spiegeln sie (Auftrag 'roster') fuer ihre Konten. Neue Auftraege:
  'join' (Geraet tritt bei, beim Gruppen-Start mit start:true), 'leave', 'roster', 'invite'; 'signal' traegt im Raum den
  Endpunkt des Absenders mit (leg) und laeuft sternfoermig: Geraet -> eigener Server -> fuehrender Server -> Server des
  Zielgeraets, synchron und ohne Ausgangskorb, versiegelt je Zielgeraet (kein Server liest etwas). Mit 2.3-Servern (nur
  calls2) bleibt alles 1:1 mit einem Geraet je Seite; Gruppenanrufe gibt es dann in dem Chat nicht."""
import json
import time

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from db import connect as _db
from saga import bus, calls, chat, fed_chat as FC, federation as F, sharing
from saga.core import _bad, t

router = FC.router                      # gleiche Router-Einbindung wie die Chats (app.py)
OPS = ("ring", "answer", "signal", "end", "decline", "alive", "join", "leave", "roster", "invite", "dmhorn")


def peer_ok(p):
    return FC.peer_ok(p) and F.feature(p, "calls2")


def peer3(p):
    """2.4: Raum-Funktionen (Mehrgeraete, Gruppen) mit Instanz p - beide melden 'calls3'."""
    return peer_ok(p) and F.feature(p, "calls3")


def peer_horn(p):
    """2.5 Gjallarhorn: beide Server melden 'horn' (Feature-Flag calls.horn) - Knoten-Anrufe mit Frame-E2E. Die Signale
    fkey/fkey-ack brauchen nichts Eigenes: sie laufen versiegelt im Auftrag 'signal' wie jedes Anruf-Signal."""
    return peer3(p) and F.feature(p, "horn")


def peer_horn_dm(p):
    """2.5: 1:1 aus foederierten Direkt-Chats ueber Knoten (Auftrag dmhorn) - beide Server melden 'horn_dm'."""
    return peer_horn(p) and F.feature(p, "horn_dm")


def _remote_of(call):
    """Die Person der anderen Instanz in diesem Anruf (Prinzipal) und ob sie anruft."""
    if sharing.is_remote(call["caller"]):
        return call["caller"], True
    return call["callee"], False


# =====================================================================================================
# Eigene Konten (aus saga.calls)
# =====================================================================================================
def start(uid, ch, cid, media, dev, sig, caps="[]"):
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
        dname = calls._dev_name(c, dev)
    st, d = FC.sync_call(p, {"op": "ring", "id": cid, "ref": ch["ref"], "from": mh, "from_device": dev, "media": media, "signals": sig,
                                "caps": calls.caps_out(caps), "name": dname},
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
        v = 3 if calls._has(caps, "multi") and peer3(p) else 1      # 2.4: Raum nur, wenn beide Server calls3 melden
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, peer_id, nsig, caps_a, v) "
                  "VALUES (?,?,?,?,?,?,'ringing',?,?,?,?,?,?,?)", (cid, ch["id"], uid, other, dev, media, now, now, now, p["id"], len(sig), caps, v))
        calls._leg_put(c, cid, dev, uid, "member", None, caps, name=calls._dev_name(c, dev), now=now)
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


def answer(uid, call, dev, sig, caps="[]"):
    """Annehmen auf dem Server des Gegenuebers: erst beim Server des Anrufers (er fuehrt den Zustand), dann hier."""
    with _db() as c:
        dname = calls._dev_name(c, dev)
    st, d = _send(call, {"op": "answer", "actor": actor(uid), "device": dev, "signals": sig, "caps": calls.caps_out(caps), "name": dname})
    now = time.time()
    with _db() as c, c:
        cur = calls._load(c, call["id"], lock=True)
        if st != 200 or not cur or cur["state"] != "ringing":
            if cur and cur["state"] == "ringing" and st is not None:
                calls._finish(c, cur, "gone", now, remote=True)
            return chat._no_store({"error": t("Der Anruf ist schon beendet oder wurde auf einem anderen Gerät angenommen."),
                                   "call": calls._dict(c, cur or call, uid)}, 409)
        c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=?, nsig=nsig+1, caps_b=? WHERE id=?",
                  (dev, now, now, now, caps, call["id"]))
        calls._leg_put(c, call["id"], dev, uid, "member", None, caps, name=calls._dev_name(c, dev), now=now)
        calls._emit(c, [uid], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "device": dev})
        if call["v"] == 3:
            calls.emit_roster(c, calls._load(c, call["id"]), fed=False)
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


def alive(uid, call, dev=""):
    with _db() as c:
        mh = FC.my_handle(c, uid)
    body = {"op": "alive", "id": call["id"], "actor": mh}
    if dev:
        body["device"] = dev                       # 2.4: Lebenszeichen je Endpunkt (2.3-Server ignorieren es)
    FC._volatile(call.get("lead") or call["peer_id"], body, path="/fed/v2/call")


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
# 2.4 (calls3): Raum ueber Servergrenzen - eigene Konten
# =====================================================================================================
OPS3 = ("join", "leave", "roster", "invite")


def _name(d):
    n = d.get("name")
    return n[:80] if isinstance(n, str) else ""


def _h(c, who):
    """Konto hier bzw. Prinzipal -> Handle."""
    return FC.handle_of(who) if sharing.is_remote(who) else FC.my_handle(c, who)


def _who(c, h):
    """Handle -> Konto hier (wenn es auf diesem Server liegt) bzw. Prinzipal; None bei Unsinn."""
    n, host = FC.split_handle(h)
    if not n:
        return None
    if host == F.SELF_HOST:
        return F.uid_by_handle(c, n)
    return FC.principal(h)


def _leg_out(c, x):
    return {"device": x["device"], "who": _h(c, x["who"]), "role": x["role"], "state": x["state"], "caps": calls.caps_out(x["caps"]),
            "name": x["name"] or "", **({"replaces": x["replaces"]} if x.get("replaces") else {})}


def _peers_of(c, call):
    """Andere Server eines Anrufs, den wir fuehren (nur calls3): 1:1 die Gegenstelle, Gruppe alle Teilnehmer-Server."""
    if call["kind"] != "group":
        p = F.peer_by(c, "id", call["peer_id"]) if call["peer_id"] else None
        return [p] if peer3(p) else []
    ch = chat._load(c, call["chat_id"])
    return [p for p in FC.participants(c, ch) if peer3(p)] if ch else []


def can3(c, call):
    """Koennen alle beteiligten Server Raum-Funktionen (calls3)?"""
    if call.get("lead") is not None:
        return peer3(F.peer_by(c, "id", call["lead"]))
    if call["kind"] != "group":
        return peer3(F.peer_by(c, "id", call["peer_id"])) if call["peer_id"] else True
    ch = chat._load(c, call["chat_id"])
    return not group_block(c, ch) if ch else False


def group_block(c, ch):
    """Gruppenanruf in einem foederierten Chat moeglich? -> None oder Hinweis (Server ohne calls3: "kann noch nicht")."""
    if ch["home_peer"] is not None:
        p = F.peer_by(c, "id", ch["home_peer"])
        return None if peer3(p) else t("Der Server {host} kann noch keine Gruppenanrufe (braucht Saga 2.4).", host=(p or {}).get("host") or "?")
    bad = [p["host"] for p in FC.participants(c, ch) if not peer3(p)]
    return t("Der Server {host} kann noch keine Gruppenanrufe (braucht Saga 2.4).", host=", ".join(bad)) if bad else None


def chat_flags(c, ch):
    """Fuer /api/chats/{cid}: kann in diesem foederierten Chat ein Gruppenanruf beginnen? -> (ja, hinweis)."""
    why = group_block(c, ch)
    return why is None, why


def _err(st, d, host):
    if st is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=host), 502)
    if isinstance(d, dict) and d.get("join_block") == "full":
        return chat._no_store({"error": t("Der Anruf ist voll (höchstens {n} Geräte).", n=d.get("limit") or "?"), "join_block": "full",
                               "limit": d.get("limit")}, 409)
    if isinstance(d, dict) and d.get("join_block") == "old":
        return chat._no_store({"error": t("Beitreten geht nicht: Ein Gerät im Anruf braucht Saga 2.4."), "join_block": "old"}, 409)
    if isinstance(d, dict) and d.get("join_block") == "ended" or st == 404:
        return chat._no_store({"error": t("Der Anruf ist schon beendet."), "join_block": "ended"}, 409)
    if isinstance(d, dict) and d.get("running"):
        return chat._no_store({"error": t("In diesem Chat läuft schon ein Anruf – tritt ihm bei."), "running": d["running"]}, 409)
    if st == 409 and isinstance(d, dict) and d.get("legs") is not None:
        return chat._no_store({"error": t("Die Teilnehmenden haben sich geändert – bitte erneut beitreten.")}, 409)
    return _bad(t("Beitreten ging nicht ({code}).", code=st), 409 if st in (400, 403, 409) else 502)


def group_start(uid, ch, cid, media, dev, caps):
    """Gruppenanruf in einem foederierten Chat, dessen Heim ein anderer Server ist: dort anlegen (synchron), hier spiegeln."""
    with _db() as c:
        p = F.peer_by(c, "id", ch["home_peer"])
        mh = FC.my_handle(c, uid)
        name = calls._dev_name(c, dev)
    if not peer3(p):
        return _bad(t("Der Server {host} kann noch keine Gruppenanrufe (braucht Saga 2.4).", host=(p or {}).get("host") or "?"), 409)
    st, d = FC.sync_call(p, {"op": "join", "start": True, "id": cid, "ref": ch["ref"], "actor": mh, "device": dev, "role": "member", "media": media,
                             "caps": calls.caps_out(caps), "name": name, "signals": {}, **_horn_hint(caps)}, path="/fed/v2/call")
    if st != 200:
        return _err(st, d, p["host"])
    now = time.time()
    topo = "horn" if (d or {}).get("topo") == "horn" and peer_horn(p) else "mesh"
    _mesh_in(cid, d)
    with _db() as c, c:
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, caps_a, kind, v, topo, "
                  "lead, peer_id) VALUES (?,?,?,'',?,?,'ringing',?,?,?,?,'group',3,?,?,?) ON CONFLICT (id) DO NOTHING",
                  (cid, ch["id"], uid, dev, media, now, now, now, caps, topo, p["id"], p["id"]))
        calls._leg_put(c, cid, dev, uid, "member", None, caps, name=name, now=now)
        call = calls._load(c, cid)
        _apply_legs(c, call, p, (d or {}).get("legs"))
        calls.emit_roster(c, call, fed=False)
        print(f"call: {cid[:8]} Gruppenanruf beim Heim {p['host']}{' (ueber Knoten)' if topo == 'horn' else ''}", flush=True)
        out = calls._dict(c, calls._load(c, cid), uid)
    if topo == "horn":                                 # 2.5 P10: Kandidaten (geteilte Knoten des Heims und eigene)
        out["horn"], err = _horn_block(uid, call, dev, "member")
        if err:
            return err
    return chat._no_store({"ok": True, "call": out})


def dm_horn_start(uid, ch, cid, media, dev, caps, dev_class="desktop"):
    """2.5: POST /api/calls {via: "horn"} in einem foederierten Direkt-Chat (calls.horn_all an). Wie 2.3 fuehrt der Server
    der anrufenden Person den Anruf: erst beim Server des Gegenuebers anklingeln lassen (Auftrag dmhorn, synchron - dort
    entsteht die Kopie und es klingelt), dann hier den Raum-Anruf ueber Knoten anlegen (kind 'group', topo 'horn', callee =
    Gegenueber). Danach laeuft alles wie ein foederierter Gruppenanruf ueber Knoten (join/leave/roster/horn, P10).
    Geht es nicht: 409 fallback direct (nichts angelegt), der Client ruft wie bisher direkt an."""
    if not (calls._has(caps, "horn") and calls._has(caps, "multi")):
        return calls._fallback("caps")
    now = time.time()
    with _db() as c:
        m = chat._member(c, ch["id"], uid)
        other = next((u for u in chat.members(c, ch["id"]) if sharing.is_remote(u)), None)
        ro = FC._ro_error(c, ch)
        if ro:
            return _bad(ro, 403)
        if not other or not m or m.get("state") != "ok":
            return _bad(t("Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben."), 409)
        h = FC.handle_of(other)
        p = F.peer_by(c, "host", FC.split_handle(h)[1])
        if not peer_horn_dm(p):
            return calls._fallback("fed")
        if not calls.horn.any_ready(c) or not FH.fed_ok(c, ch)[0]:
            return calls._fallback("node")
        if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
            return _bad(t("Diesen Anruf gibt es schon."), 409)
        if calls._busy(c, uid):
            return _bad(t("Du telefonierst gerade schon."), 409)
        if not calls._rate("call", uid, calls.CALL_RATE) or not calls._rate("call-h", uid, calls.CALL_RATE_H, 3600) \
                or not calls._rate("pair", uid + "|" + other, calls.PAIR_RATE):
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        mh = FC.my_handle(c, uid)
        dname = calls._dev_name(c, dev)
        name = FC.label_of(c, other)["name"]
    st, d = FC.sync_call(p, {"op": "dmhorn", "id": cid, "ref": ch["ref"], "from": mh, "from_device": dev, "media": media,
                             "caps": calls.caps_out(caps), "name": dname, **_horn_hint(caps)}, path="/fed/v2/call")
    d = d if isinstance(d, dict) else {}
    if st is None:                                     # Zeitablauf: dort kann die Kopie schon klingeln - verwerfbar beenden
        FC._volatile(p["id"], {"op": "end", "id": cid, "actor": mh, "reason": "cancelled"}, path="/fed/v2/call", drop=False)
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"]), 502)
    if st == 409 and d.get("busy"):
        return chat._no_store({"ok": False, "busy": True, "error": t("Besetzt – {name} telefoniert gerade.", name=name)}, 409)
    if st == 409 and d.get("error") == "request":
        return _bad(t("Anrufe gehen erst, wenn beide die Unterhaltung angenommen haben."), 409)
    if st == 409 and d.get("horn_block") in ("peer", "off", "node"):
        return calls._fallback(d["horn_block"])
    if st == 429:
        return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
    if st != 200:
        return calls._fallback("fed")                  # alles andere: direkt wie bisher (der Server dort hat nichts angelegt)
    with _db() as c, c:
        _RUNG.add(cid)                                 # dort klingelt es schon (Auftrag dmhorn) - die erste Liste nicht nochmal
        call = calls.group_create(c, ch, cid, media, uid, dev, caps, now, name=dname, dm_peer=other, ring=False)
        out = calls._dict(c, call, uid)
        out["horn"] = calls.horn_block(c, call, dev, "member", dev_class)
        print(f"call: {cid[:8]} 1:1 ueber Knoten klingelt bei {p['host']} ({media})", flush=True)
    return chat._no_store({"ok": True, "call": out})


def dm_ended(c, call, reason):
    """2.5: Kopie eines 1:1-Anrufs ueber Knoten (wir fuehren ihn nicht): das Gegenueber hier lehnt ab bzw. ist besetzt ->
    dem fuehrenden Server melden (nach dem Commit, verwerfbar) - dort endet der Anruf fuer die anrufende Person."""
    try:
        mh = _h(c, call["callee"])
    except F.FedError:
        return
    body = {"op": "end", "id": call["id"], "actor": mh, "reason": reason}
    c.after_commit(lambda pid=call["lead"], body=body: FC._volatile(pid, body, path="/fed/v2/call", drop=False))


def join(uid, call, dev, role, rep, caps, sig):
    """POST /join auf einem Server, der den Anruf NICHT fuehrt: beim fuehrenden Server eintragen (synchron), hier spiegeln."""
    with _db() as c:
        p = F.peer_by(c, "id", call["lead"])
        mh = FC.my_handle(c, uid)
        name = calls._dev_name(c, dev)
    if not peer3(p):
        return chat._no_store({"error": t("Beitreten geht nicht: Ein Gerät im Anruf braucht Saga 2.4."), "join_block": "old"}, 409)
    body = {"op": "join", "id": call["id"], "actor": mh, "device": dev, "role": role, "caps": calls.caps_out(caps), "name": name, "signals": sig,
            **_horn_hint(caps)}
    if rep:
        body["replaces"] = rep
    st, d = FC.sync_call(p, body, path="/fed/v2/call")
    if st != 200:
        return _err(st, d, p["host"])
    now = time.time()
    with _db() as c, c:
        cur = calls._load(c, call["id"], lock=True)
        if not cur or cur["state"] == "ended":
            return chat._no_store({"error": t("Der Anruf ist schon beendet."), "join_block": "ended"}, 409)
        role = (d or {}).get("role") if (d or {}).get("role") in calls.ROLES else role
        calls._leg_put(c, cur["id"], dev, uid, role, None, caps, rep, name=name, now=now)
        c.execute("UPDATE chat_call_invite SET state='joined' WHERE call_id=? AND who=? AND state='ringing'", (cur["id"], uid))
        _apply_legs(c, cur, p, (d or {}).get("legs"), keep=dev)
        cur = calls._load(c, cur["id"])
        calls.emit_roster(c, cur, fed=False)
        out = calls._dict(c, cur, uid)
    if calls.is_horn(cur):                             # 2.5 P10: Kandidaten (geteilte Knoten des Heims und eigene)
        out["horn"], err = _horn_block(uid, cur, dev, role)
        if err:
            return err
    return chat._no_store({"ok": True, "call": out})


def _horn_block(uid, call, dev, role):
    """Kandidaten nach dem Eintragen beim Heim. Unerwarteter Fehler: das Geraet sofort wieder austragen (hier und beim Heim),
    sonst bleibt ein Bein ohne Person im Anruf (Nr. 108). -> (block, fehlerantwort)"""
    try:
        return FH.block(uid, call, dev, role)[0], None
    except Exception as ex:  # noqa: BLE001
        print(f"call: {call['id'][:8]} Knoten-Kandidaten fehlgeschlagen ({type(ex).__name__}: {ex}) - Geraet ausgetragen", flush=True)
        try:
            leave(uid, call, dev)
        except Exception as ex2:  # noqa: BLE001
            print(f"call: {call['id'][:8]} austragen fehlgeschlagen ({type(ex2).__name__})", flush=True)
        return None, _bad(t("Beitreten ging nicht – bitte noch einmal versuchen."), 502)


def leave(uid, call, dev):
    """Eigenes Geraet verlaesst einen Anruf, den ein anderer Server fuehrt: dort melden, hier sofort austragen."""
    with _db() as c:
        p = F.peer_by(c, "id", call["lead"])
        mh = FC.my_handle(c, uid)
    st, d = FC.sync_call(p, {"op": "leave", "id": call["id"], "actor": mh, "device": dev}, path="/fed/v2/call") if peer3(p) else (None, "")
    now = time.time()
    with _db() as c, c:
        cur = calls._load(c, call["id"], lock=True)
        if cur and cur["state"] != "ended":
            if calls.is_horn(cur):
                FH.left(c, cur, dev)                   # 2.5 P10: Beine an unserem Knoten beenden
            c.execute("UPDATE chat_call_leg SET state='left', left_at=?, reason='hangup' WHERE call_id=? AND device=?", (now, cur["id"], dev))
            if st is None and not any(x["who"] == uid for x in calls._legs(c, cur["id"])) and cur["kind"] == "dm":
                calls._finish(c, cur, "hangup", now, by=uid, remote=True)      # fuehrender Server weg: hier beenden
            else:
                calls.emit_roster(c, cur, fed=False)
    return chat._no_store({"ok": True})


def invite(uid, call, whos):
    """Fremde Mitglieder klingeln lassen: fuehren wir den Anruf, an ihre Server; sonst an den fuehrenden Server."""
    with _db() as c:
        hs = [FC.handle_of(u) for u in whos if FC.handle_of(u)]
        mh = FC.my_handle(c, uid)
        if call.get("lead") is not None:
            targets = {call["lead"]: hs}
        else:
            targets = {}
            for h in hs:
                p = F.peer_by(c, "host", FC.split_handle(h)[1])
                if peer3(p):
                    targets.setdefault(p["id"], []).append(h)
    for pid, lst in targets.items():
        FC._volatile(pid, {"op": "invite", "id": call["id"], "actor": mh, "who": lst}, path="/fed/v2/call", drop=False)


def forward(call, who, dev, fwd, leg):
    """Signale eines Endpunkts an andere Server weiterreichen (synchron, ohne Ausgangskorb). -> alle angekommen?"""
    with _db() as c:
        body = {"op": "signal", "id": call["id"], "actor": _h(c, who if leg is None else leg["who"]), "device": dev}
        if leg is not None:
            body["leg"] = _leg_out(c, leg)
        peers = {pid: F.peer_by(c, "id", pid) for pid in fwd}
    good = True
    for pid, sig in fwd.items():
        p = peers.get(pid)
        if not peer_ok(p):
            good = False
            continue
        st, d = FC.sync_call(p, dict(body, signals=sig), path="/fed/v2/call")
        good = good and st == 200
    return good


def roster_out(c, call, legs, ring=None):
    """Fuehrender Server: Teilnehmerliste an die anderen Server (nach dem Commit, verwerfbar). ring: Prinzipale, deren
    Server sie klingeln lassen soll (Gruppenstart, invite) - die Antwort meldet, wer Saga 2.4 braucht."""
    peers = _peers_of(c, call)
    if not peers:
        return
    ch = chat._load(c, call["chat_id"])
    inv = dict(c.execute("SELECT who, state FROM chat_call_invite WHERE call_id=?", (call["id"],)).fetchall()) if call["kind"] == "group" else {}
    if ring is None and call["kind"] == "group":       # erste Liste nach dem Start: alle eingeladenen fremden Mitglieder
        ring = [u for u, st in inv.items() if st == "ringing" and sharing.is_remote(u) and call["id"] not in _RUNG]
        _RUNG.add(call["id"])
        if len(_RUNG) > 5000:
            _RUNG.clear()
    # rv: Stand der Liste - Auftraege an eine Instanz laufen parallel (PEER_SLOTS), eine aeltere Liste darf eine neuere
    # nicht ueberschreiben
    base = {"op": "roster", "id": call["id"], "ref": (ch or {}).get("ref"), "kind": call["kind"], "media": call["media"], "state": call["state"],
            "rv": time.time(),
            "created": call["created"], "from": _h(c, call["caller"]), "from_device": call["caller_device"], "legs": [_leg_out(c, x) for x in legs],
            "topo": call.get("topo") or "mesh", **({"horn_mesh": HORN_MESH[call["id"]]} if HORN_MESH.get(call["id"]) else {})}
    g = calls.gone(c, call["id"])
    if g:
        base["gone"] = g                               # 2.5: Grund fuer Abgaenge (Notaus) - der andere Server reicht ihn weiter
    for p in peers:
        mine = [FC.handle_of(u) for u in (ring or []) if FC.split_handle(FC.handle_of(u) or "")[1] == p["host"]]
        body = dict(base, ring=mine, invites={FC.handle_of(u): st for u, st in inv.items()
                                              if sharing.is_remote(u) and FC.split_handle(FC.handle_of(u) or "")[1] == p["host"]})
        c.after_commit(lambda p=p, body=body: F.peer_submit(p["id"], _roster_send, p["id"], body))


_RUNG = set()
_RV = {}                                # Anruf -> Stand der zuletzt uebernommenen Teilnehmerliste (op_roster)


def _roster_send(pid, body):
    with _db() as c:
        p = F.peer_by(c, "id", pid)
    if not peer3(p):
        return
    try:
        st, d = F.call_json(p, "POST", "/fed/v2/call", body=body, timeout=max(1.0, F.EXPRESS_TIMEOUT / 3))
    except F.FedError:
        return
    old = [h for h in (d or {}).get("old") or [] if isinstance(h, str)][:chat.MEMBERS_MAX] if st == 200 and isinstance(d, dict) else []
    if old:                                            # Personen dort ohne Geraet mit "multi": "braucht Saga 2.4"
        with _db() as c, c:
            for h in old:
                n, host = FC.split_handle(h)
                if n and host == p["host"]:
                    c.execute("UPDATE chat_call_invite SET state='old' WHERE call_id=? AND who=? AND state='ringing'", (body["id"], FC.principal(h)))
            call = calls._load(c, body["id"])
            if call and call["state"] != "ended":
                calls.emit_roster(c, call, fed=False)


def group_ended(c, call, reason):
    """Gruppenanruf beendet (wir fuehren ihn): allen anderen Servern melden (nach dem Commit, verwerfbar)."""
    try:
        mh = _h(c, call["caller"])
    except F.FedError:
        return
    for p in _peers_of(c, call):
        body = {"op": "end", "id": call["id"], "actor": mh, "reason": reason}
        c.after_commit(lambda pid=p["id"], body=body: FC._volatile(pid, body, path="/fed/v2/call", drop=False))


def _apply_legs(c, call, peer, legs, keep=None, gone=None):
    """Teilnehmerliste des fuehrenden Servers hier uebernehmen: Endpunkte anlegen/aktualisieren, fehlende austragen.
    Eigene Geraete nur, wenn sie wirklich zum genannten Konto gehoeren."""
    if not isinstance(legs, list):
        return
    now = time.time()
    seen = set()
    for x in legs[:chat.MEMBERS_MAX]:
        if not isinstance(x, dict):
            continue
        dev, who = x.get("device"), _who(c, x.get("who"))
        if not (isinstance(dev, str) and FC._HEX32.fullmatch(dev)) or not who or x.get("state", "active") != "active":
            continue
        local = not sharing.is_remote(who)
        if local and not c.execute("SELECT 1 FROM chat_device WHERE id=? AND uid=?", (dev, who)).fetchone():
            continue
        if not local and FC.split_handle(x.get("who"))[1] == F.SELF_HOST:
            continue
        seen.add(dev)
        cur = c.execute("SELECT who, state, role FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], dev)).fetchone()
        role = x.get("role") if x.get("role") in calls.ROLES else "member"
        rep = x.get("replaces") if isinstance(x.get("replaces"), str) and FC._HEX32.fullmatch(x["replaces"]) else None
        if cur and cur[0] == who and cur[1] == "active":
            c.execute("UPDATE chat_call_leg SET role=?, replaces=?, caps=? WHERE call_id=? AND device=?",
                      (role, rep, calls.caps_in(x.get("caps")), call["id"], dev))
            continue
        calls._leg_put(c, call["id"], dev, who, role, None if local else peer["id"], calls.caps_in(x.get("caps")), rep, name=_name(x), now=now)
    for x in calls._legs(c, call["id"]):
        if x["device"] not in seen and x["device"] != keep:
            why = (gone or {}).get(x["device"]) if isinstance(gone, dict) else None
            why = why if why in calls.GONE_REASONS else "roster"     # 2.5: Grund des fuehrenden Servers (Notaus) uebernehmen
            c.execute("UPDATE chat_call_leg SET state='left', left_at=?, reason=? WHERE call_id=? AND device=?", (now, why, call["id"], x["device"]))


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
    caps = calls.caps_in(d.get("caps"))
    c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, peer_id, caps_a, v, lead) "
              "VALUES (?,?,?,?,?,?,'ringing',?,?,?,?,?,?,?)", (cid, ch["id"], p_, callee, dev, media, now, now, now, peer["id"], caps,
                                                           3 if calls._has(caps, "multi") and peer3(peer) else 1, peer["id"]))
    calls._leg_put(c, cid, dev, p_, "member", peer["id"], caps, name=_name(d), now=now)
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
    c.execute("UPDATE chat_call SET state='active', callee_device=?, answered=?, seen_a=?, seen_b=?, caps_b=? WHERE id=?",
              (dev, now, now, now, calls.caps_in(d.get("caps")), call["id"]))
    calls._leg_put(c, call["id"], dev, rem, "member", peer["id"], calls.caps_in(d.get("caps")), name=_name(d), now=now)
    seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                    (call["id"], dev, call["caller_device"], sig[call["caller_device"]], now)).fetchone()[0]
    c.execute("UPDATE chat_call SET nsig=nsig+1 WHERE id=?", (call["id"],))
    calls._emit(c, [call["caller"]], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": call["caller_device"],
                                                     "sealed": sig[call["caller_device"]]})
    calls._emit(c, [call["caller"]], "call.state", {"call_id": call["id"], "chat_id": call["chat_id"], "state": "active", "device": dev})
    if call["v"] == 3:
        calls.emit_roster(c, calls._load(c, call["id"]))
    return 200, {"ok": True}


def op_signal(c, peer, d):
    cur = calls._load(c, d.get("id"), lock=True) if isinstance(d.get("id"), str) else None
    if cur and (cur["v"] == 3 or cur["kind"] == "group"):
        return _room_signal_in(c, peer, d, cur)
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
    now = time.time()
    if call["nsig"] + len(sig) > calls.sig_budget(call, now) or not F._rate.allow("csig:" + dev, calls.SIG_RATE):
        return 429, {"error": "too many signals"}
    for k, v in sorted(sig.items()):
        seq = c.execute("INSERT INTO chat_call_signal (call_id, from_device, to_device, sealed, created) VALUES (?,?,?,?,?) RETURNING seq",
                        (call["id"], dev, k, v, now)).fetchone()[0]
        calls._emit(c, [to_uid], "call.signal", {"call_id": call["id"], "seq": seq, "from_device": dev, "to_device": k, "sealed": v})
    c.execute(f"UPDATE chat_call SET nsig=nsig+?, {seen}=? WHERE id=?", (len(sig), now, call["id"]))
    return 200, {"ok": True}


def op_end(c, peer, d):
    cur = calls._load(c, d.get("id"), lock=True) if isinstance(d.get("id"), str) else None
    if cur and calls._dmh(cur) and cur["lead"] is None and cur["state"] == "ringing" and sharing.is_remote(cur["callee"]):
        h = d.get("actor")                             # 2.5: 1:1 ueber Knoten, das Gegenueber dort lehnt ab / ist besetzt
        n, host = FC.split_handle(h)
        if not n or host != peer["host"] or FC.principal(h) != cur["callee"]:
            F.log(c, "call.forged", peer, "end", throttle=(peer["id"], "cforged4"))
            return 403, {"error": "not callee"}
        reason = d.get("reason") if d.get("reason") in ("declined", "busy") else "declined"
        calls._finish(c, cur, reason, time.time(), by=cur["callee"], remote=True)
        return 200, {"ok": True}
    if cur and cur["kind"] == "group":                 # 2.4: nur der fuehrende Server beendet einen Gruppenanruf
        if cur["lead"] != peer["id"]:
            return 403, {"error": "not lead"}
        if cur["state"] != "ended":
            cur["_connected"] = cur["state"] == "active"
            calls._finish(c, cur, d.get("reason") if d.get("reason") in ("ended", "timeout", "cancelled", "gone") else "ended",
                          time.time(), remote=True)
        return 200, {"ok": True}
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
    cur = calls._load(c, d.get("id")) if isinstance(d.get("id"), str) else None
    if cur and cur["kind"] == "group":                 # 2.4: Endpunkt eines anderen Servers im Gruppenanruf, den wir fuehren
        h = d.get("actor")
        n, host = FC.split_handle(h)
        if cur["lead"] is not None or not n or host != peer["host"] or cur["state"] == "ended":
            return 200, {"state": "ended" if not cur or cur["state"] == "ended" else cur["state"]}
        dev = d.get("device") if isinstance(d.get("device"), str) else ""
        c.execute("UPDATE chat_call_leg SET seen=? WHERE call_id=? AND device=? AND who=? AND peer_id=? AND state='active'",
                  (time.time(), cur["id"], dev, FC.principal(h), peer["id"]))
        return 200, {"state": cur["state"]}
    call, rem, is_caller = _call_of(c, peer, d, lock=False)
    if call and call["state"] != "ended":
        now = time.time()
        c.execute("UPDATE chat_call SET " + ("seen_a" if is_caller else "seen_b") + "=? WHERE id=?", (now, call["id"]))
        dev = d.get("device") if isinstance(d.get("device"), str) else ""
        q = "UPDATE chat_call_leg SET seen=? WHERE call_id=? AND who=? AND state='active'" + (" AND device=?" if dev else "")
        c.execute(q, (now, call["id"], rem) + ((dev,) if dev else ()))
        return 200, {"state": call["state"]}
    return 200, {"state": "ended"}


HANDLERS = {"ring": op_ring, "answer": op_answer, "signal": op_signal, "end": op_end, "decline": op_end, "alive": op_alive,
            "join": None, "leave": None, "roster": None, "invite": None}       # 2.4 (calls3), unten eingetragen


# =====================================================================================================
# 2.4 (calls3): Server-zu-Server - Raum
# =====================================================================================================
def _resp(r):
    """JSONResponse aus saga.calls -> (status, daten)."""
    try:
        return r.status_code, json.loads(r.body)
    except (ValueError, AttributeError):
        return 400, {"error": "bad request"}


def _actor3(c, peer, d):
    h = d.get("actor")
    n, host = FC.split_handle(h)
    if not n or host != peer["host"]:
        F.log(c, "call.forged", peer, d.get("op") or "?", throttle=(peer["id"], "cforged3"))
        return None, None
    return h, FC.principal(h)


def _post_forward(call, who, dev, fwd, leg, skip=None):
    fwd = {pid: v for pid, v in fwd.items() if pid != skip}
    if not fwd:
        return None
    return lambda: forward(call, who, dev, fwd, leg)


def op_join(c, peer, d):
    """Geraet einer Person der anfragenden Instanz tritt einem Anruf bei, den WIR fuehren (start: Gruppenanruf anlegen)."""
    h, p_ = _actor3(c, peer, d)
    cid, dev = d.get("id"), d.get("device")
    if not h or not (isinstance(cid, str) and FC._HEX32.fullmatch(cid) and isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return 400, {"error": "bad request"}
    if not calls.enabled():
        return 403, {"error": "calls disabled"}
    caps = calls.caps_in(d.get("caps"))
    now = time.time()
    call = calls._load(c, cid, lock=True)
    if call is None:
        if not d.get("start"):
            return 404, {"error": "not found"}
        ch = FC._home_chat(c, d)
        media = d.get("media")
        if not ch or ch["kind"] == "dm" or media not in calls.MEDIA:
            return 404, {"error": "unknown chat"}
        mem = dict(FC.members(c, ch["id"]))
        if mem.get(p_) != "ok":
            return 403, {"error": "not a member"}
        if not calls._has(caps, "multi"):
            return 409, {"join_block": "old"}
        run = c.execute("SELECT id FROM chat_call WHERE chat_id=? AND kind='group' AND state<>'ended' LIMIT 1", (ch["id"],)).fetchone()
        if run:
            return 409, {"running": run[0]}
        if not F._rate.allow("cring:" + h, calls.CALL_RATE):
            return 429, {"error": "too many calls"}
        FH.note_nodes(peer["id"], d)                   # 2.5 P10: hat der Server des Startenden eigene Knoten?
        call = calls.group_create(c, ch, cid, media, p_, dev, caps, now, peer_id=peer["id"], name=_name(d))
        return 200, {"ok": True, "role": "member", "legs": [_leg_out(c, x) for x in calls._legs(c, cid)], "topo": call.get("topo") or "mesh",
                     **({"horn_mesh": HORN_MESH[cid]} if HORN_MESH.get(cid) else {})}
    if call["lead"] is not None:
        return 404, {"error": "not lead"}
    if call["kind"] == "group":
        if dict(FC.members(c, call["chat_id"])).get(p_) != "ok":
            return 403, {"error": "not a member"}
    elif p_ not in (call["caller"], call["callee"]):
        return 404, {"error": "not found"}
    role = d.get("role") if d.get("role") in calls.ROLES else "member"
    rep = d.get("replaces") if isinstance(d.get("replaces"), str) and FC._HEX32.fullmatch(d["replaces"]) else None
    FH.note_nodes(peer["id"], d)
    r = calls.join_local(c, call, p_, dev, role, rep, caps, d.get("signals"), now, peer_id=peer["id"], name=_name(d))
    if not isinstance(r, dict):
        st, out = _resp(r)
        return st, out
    legs = calls._legs(c, cid)
    return 200, {"ok": True, "role": r["leg"]["role"], "legs": [_leg_out(c, x) for x in legs]}, _post_forward(call, p_, dev, r["fwd"], r["leg"], skip=peer["id"])


def op_leave(c, peer, d):
    h, p_ = _actor3(c, peer, d)
    call = calls._load(c, d.get("id"), lock=True) if isinstance(d.get("id"), str) else None
    if not h or not call or call["lead"] is not None:
        return 404, {"error": "not found"}
    leg = c.execute("SELECT who, peer_id, state FROM chat_call_leg WHERE call_id=? AND device=?", (call["id"], d.get("device"))).fetchone()
    if not leg or leg[0] != p_ or leg[1] != peer["id"]:
        return 404, {"error": "not found"}
    if leg[2] == "active" and call["state"] != "ended":
        calls._leave(c, call, d["device"], time.time(), "hangup", by=p_)
    return 200, {"ok": True}


def op_roster(c, peer, d):
    """Teilnehmerliste vom fuehrenden Server (wir spiegeln den Anruf fuer unsere Konten). Gruppenanruf noch unbekannt:
    nur, wenn die Instanz das Heim des Chats ist. Klingelt bei den genannten eigenen Mitgliedern; Antwort: wer davon
    Saga 2.4 braucht."""
    cid = d.get("id")
    if not (isinstance(cid, str) and FC._HEX32.fullmatch(cid)):
        return 400, {"error": "bad request"}
    now = time.time()
    call = calls._load(c, cid, lock=True)
    if call is None:
        ch = FC.by_ref(c, d.get("ref")) if isinstance(d.get("ref"), str) else None
        if d.get("kind") != "group" or not ch or ch["home_peer"] != peer["id"] or d.get("media") not in calls.MEDIA:
            return 404, {"error": "unknown"}
        if d.get("state") == "ended":
            return 200, {"ok": True}
        st_ = _who(c, d.get("from"))
        fd = d.get("from_device") if isinstance(d.get("from_device"), str) and FC._HEX32.fullmatch(d.get("from_device") or "") else None
        if not st_ or not fd:
            return 400, {"error": "bad request"}
        topo = "horn" if d.get("topo") == "horn" and peer_horn(peer) else "mesh"      # 2.5 P10: ueber Knoten
        c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, kind, v, topo, lead, peer_id) "
                  "VALUES (?,?,?,'',?,?,?,?,?,?,'group',3,?,?,?) ON CONFLICT (id) DO NOTHING",
                  (cid, ch["id"], st_, fd, d["media"], "active" if d.get("state") == "active" else "ringing", now, now, now, topo, peer["id"], peer["id"]))
        calls._OWN.add(cid)
        call = calls._load(c, cid, lock=True)
    if call["lead"] != peer["id"]:
        F.log(c, "call.forged", peer, "roster", throttle=(peer["id"], "croster"))
        return 403, {"error": "not lead"}
    if call["state"] == "ended":
        return 200, {"ok": True, "state": "ended"}
    _mesh_in(cid, d)
    rv = d.get("rv") if isinstance(d.get("rv"), (int, float)) else None
    if rv is not None and rv < _RV.get(cid, 0):
        return 200, {"ok": True, "stale": True}            # aeltere Liste (kam nach einer neueren an)
    if rv is not None:
        _RV[cid] = rv
        if len(_RV) > 5000:
            _RV.clear()
    _apply_legs(c, call, peer, d.get("legs"), gone=d.get("gone"))
    if call["kind"] == "group":
        upd = {"seen_b": now}
        if d.get("state") == "active" and call["state"] == "ringing":
            upd.update(state="active", answered=now)
        c.execute("UPDATE chat_call SET " + ", ".join(f"{k}=?" for k in upd) + " WHERE id=?", tuple(upd.values()) + (cid,))
        if upd.get("state") == "active":               # wie lokal (join_local): andere eigene Geraete hoeren auf zu klingeln
            cur = calls._load(c, cid)
            calls._emit(c, calls._audience(c, cur), "call.state", {"call_id": cid, "chat_id": cur["chat_id"], "state": "active", "kind": "group",
                                                                   **calls._dmf(cur)})
        for h, st in (d.get("invites") or {}).items() if isinstance(d.get("invites"), dict) else ():
            u = _who(c, h)
            if u and not sharing.is_remote(u) and st in ("ringing", "declined", "joined", "old", "missed"):
                c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,?,?) ON CONFLICT (call_id, who) DO NOTHING",
                          (cid, u, st, now))
    call = calls._load(c, cid)
    old = []
    ring = [u for u in (_who(c, h) for h in (d.get("ring") or [])[:chat.MEMBERS_MAX] if isinstance(h, str)) if u and not sharing.is_remote(u)]
    if ring and call["kind"] == "group":
        mem = dict(FC.members(c, call["chat_id"]))
        go = []
        for u in ring:
            if mem.get(u) != "ok":
                continue
            st = "ringing" if calls._can_multi(c, u) else "old"
            c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,?,?) ON CONFLICT (call_id, who) DO UPDATE "
                      "SET state=EXCLUDED.state", (cid, u, st, now))
            (go if st == "ringing" else old).append(u)
        calls.ring_group(c, call, go)
    calls.emit_roster(c, call, fed=False)
    return 200, {"ok": True, "old": [FC.my_handle(c, u) for u in old]}


def op_invite(c, peer, d):
    """Mitglieder (erneut) klingeln lassen: vom fuehrenden Server an uns (eigene Konten) bzw. von einem anderen Server an
    den fuehrenden Server (der verteilt)."""
    h, p_ = _actor3(c, peer, d)
    call = calls._load(c, d.get("id"), lock=True) if isinstance(d.get("id"), str) else None
    if not h or not call or call["kind"] != "group" or call["state"] == "ended":
        return 404, {"error": "not found"}
    whos = [x for x in (_who(c, x) for x in (d.get("who") or [])[:chat.MEMBERS_MAX] if isinstance(x, str)) if x]
    if call["lead"] == peer["id"]:
        n, _ = calls.invite_local(c, call, [u for u in whos if not sharing.is_remote(u)])
        return 200, {"ok": True, "n": n}
    if call["lead"] is not None or dict(FC.members(c, call["chat_id"])).get(p_) != "ok":
        return 403, {"error": "not allowed"}
    n, remote = calls.invite_local(c, call, whos)
    if remote:
        return 200, {"ok": True, "n": n}, (lambda: _invite_on(call, remote))
    return 200, {"ok": True, "n": n}


def _invite_on(call, remote):
    with _db() as c:
        targets = {}
        for u in remote:
            h = FC.handle_of(u)
            p = F.peer_by(c, "host", FC.split_handle(h or "")[1]) if h else None
            if peer3(p):
                targets.setdefault(p["id"], []).append(h)
        mh = _h(c, call["caller"])
    for pid, lst in targets.items():
        FC._volatile(pid, {"op": "invite", "id": call["id"], "actor": mh, "who": lst}, path="/fed/v2/call", drop=False)


def _room_signal_in(c, peer, d, call):
    """op 'signal' im Raum. Vom fuehrenden Server: Absender-Endpunkt (leg) uebernehmen, an eigene Geraete zustellen. An uns
    als fuehrenden Server: Absender muss ein Endpunkt dieser Instanz sein; eigene Ziele zustellen, andere weiterreichen.
    Vom fuehrenden Server kommen auch Signale von Personen dritter Server (actor = deren Handle) - der hat sie geprueft."""
    dev = d.get("device")
    if not (isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return 400, {"error": "bad request"}
    if call["state"] == "ended":
        return 409, {"error": "ended"}
    now = time.time()
    legs = {x["device"]: x for x in calls._legs(c, call["id"])}
    if call["lead"] == peer["id"]:                     # vom fuehrenden Server: er hat den Absender geprueft
        lg = d.get("leg") if isinstance(d.get("leg"), dict) else None
        if dev not in legs and lg and lg.get("device") == dev:
            _apply_one(c, call, peer, lg, now)
            legs = {x["device"]: x for x in calls._legs(c, call["id"])}
        x = legs.get(dev)
        ringing_dm = call["kind"] == "dm" and call["state"] == "ringing" and dev == call["caller_device"]
        if not x and not ringing_dm:
            return 403, {"error": "device"}
        local = {k for k, v in legs.items() if v["peer_id"] is None} | (calls._active_devices(c, call["callee"]) if ringing_dm else set())
        sig = _sealed(d.get("signals"), local)
        if sig is None:
            return 400, {"error": "bad signals"}
        if call["nsig"] + len(sig) > calls.pairs_budget(call, len(legs) + 1, now) * 2 or not F._rate.allow("csig:" + dev, calls.SIG_RATE):
            return 429, {"error": "too many signals"}
        fwd, n = calls._route(c, call, dev, sig, legs, now)
        c.execute("UPDATE chat_call SET nsig=nsig+?" + (", seen_b=?" if call["kind"] == "group" else "") + " WHERE id=?",
                  (len(sig),) + ((now,) if call["kind"] == "group" else ()) + (call["id"],))
        return 200, {"ok": True, "n": n}
    if call["lead"] is not None:
        return 403, {"error": "not lead"}
    h, p_ = _actor3(c, peer, d)
    if not h:
        return 403, {"error": "forged"}
    x = legs.get(dev)
    if not x or x["peer_id"] != peer["id"] or x["who"] != p_:
        F.log(c, "call.forged", peer, "signal", throttle=(peer["id"], "csig3"))
        return 403, {"error": "device"}
    if not F._rate.allow("csig:" + dev, calls.SIG_RATE):
        return 429, {"error": "too many signals"}
    r = calls._room_signal(c, call, p_, dev, d.get("signals"), legs, now, peer_id=peer["id"])
    if not isinstance(r, dict):
        return _resp(r)
    c.execute("UPDATE chat_call_leg SET seen=? WHERE call_id=? AND device=?", (now, call["id"], dev))
    return 200, {"ok": True, "n": r["n"]}, _post_forward(call, p_, dev, r["fwd"], r["leg"], skip=peer["id"])


def _apply_one(c, call, peer, x, now):
    who = _who(c, x.get("who"))
    if not who or not sharing.is_remote(who):
        return
    calls._leg_put(c, call["id"], x["device"], who, x.get("role") if x.get("role") in calls.ROLES else "member", peer["id"],
                   calls.caps_in(x.get("caps")), None, name=_name(x), now=now)


def op_dmhorn(c, peer, d):
    """2.5: 1:1 ueber Knoten aus einem foederierten Direkt-Chat - die anfragende Instanz fuehrt den Anruf (wie 2.3), wir
    legen die Kopie an (kind 'group', topo 'horn', callee = unser Konto, lead = anfragende Instanz) und lassen klingeln
    (call.invite dm: true, Push call.group). Danach wie ein foederierter Gruppenanruf ueber Knoten: Annehmen = join beim
    fuehrenden Server, Ablehnen = end an ihn (dm_ended). 409 horn_block peer|off: nichts angelegt, dort ruft man direkt an."""
    cid, media, dev = d.get("id"), d.get("media"), d.get("from_device")
    if not (isinstance(cid, str) and FC._HEX32.fullmatch(cid) and media in calls.MEDIA and isinstance(dev, str) and FC._HEX32.fullmatch(dev)):
        return 400, {"error": "bad request"}
    h = d.get("from")
    n, host = FC.split_handle(h)
    if not n or host != peer["host"]:
        F.log(c, "call.forged", peer, "dmhorn", throttle=(peer["id"], "cforged5"))
        return 403, {"error": "forged"}
    if not peer_horn_dm(peer):
        return 403, {"error": "horn disabled"}
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
    if not calls.horn_on():
        return 409, {"horn_block": "off"}
    if not calls._can_horn(c, callee):
        return 409, {"horn_block": "peer"}
    if not F._rate.allow("cring:" + h, calls.CALL_RATE) or not F._rate.allow("cringp:" + peer["host"], calls.CALL_RATE * 20):
        return 429, {"error": "too many calls"}
    if c.execute("SELECT 1 FROM chat_call WHERE id=?", (cid,)).fetchone():
        return 409, {"error": "duplicate id"}
    if calls._busy_other(c, callee, cid):
        return 409, {"busy": True}
    now = time.time()
    caps = calls.caps_in(d.get("caps"))
    FH.note_nodes(peer["id"], d)                       # hat der Server der anrufenden Person eigene Knoten?
    c.execute("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, seen_a, seen_b, caps_a, kind, v, topo, "
              "lead, peer_id) VALUES (?,?,?,?,?,?,'ringing',?,?,?,?,'group',3,'horn',?,?)",
              (cid, ch["id"], p_, callee, dev, media, now, now, now, caps, peer["id"], peer["id"]))
    calls._leg_put(c, cid, dev, p_, "member", peer["id"], caps, name=_name(d), now=now)
    c.execute("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES (?,?,'ringing',?) ON CONFLICT (call_id, who) DO NOTHING",
              (cid, callee, now))
    calls._OWN.add(cid)
    call = calls._load(c, cid)
    calls.ring_group(c, call, [callee])
    calls.emit_roster(c, call, fed=False)
    print(f"call: {cid[:8]} 1:1 ueber Knoten klingelt (von {peer['host']}, {media})", flush=True)
    return 200, {"ok": True}


HANDLERS.update(join=op_join, leave=op_leave, roster=op_roster, invite=op_invite, dmhorn=op_dmhorn)


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
    if d["op"] in OPS3 and not peer3(inp.peer):
        return F._signed(inp, 403, {"error": "calls3 disabled"})
    if d["op"] in OPS_HORN and not peer_horn(inp.peer):
        return F._signed(inp, 403, {"error": "horn disabled"})
    if d["op"] == "join" and d.get("start") and isinstance(d.get("ref"), str):
        await run_in_threadpool(FH.prefetch_ref, d["ref"])       # 2.5 P10: Knoten der anderen Server (vor der Transaktion)

    def run():
        with _db() as c, c:
            return HANDLERS[d["op"]](c, inp.peer, d)
    r = await run_in_threadpool(run)
    status, out = r[0], r[1]
    if len(r) > 2 and r[2] is not None:            # 2.4: Weiterleiten an andere Server (nach dem Commit, synchron)
        await run_in_threadpool(r[2])
    return F._signed(inp, status, out)


def _peer_gone(c, p):
    """Verbindung getrennt: laufende Anrufe mit der Instanz enden sofort; in Gruppenanrufen, die wir fuehren, verlassen
    deren Endpunkte den Anruf."""
    now = time.time()
    for r in c.execute(f"SELECT {calls.C_COLS} FROM chat_call WHERE (peer_id=? OR lead=?) AND state<>'ended' FOR UPDATE", (p["id"], p["id"])).fetchall():
        call = calls._row(r)
        call["_connected"] = call["state"] == "active"
        calls._finish(c, call, "gone", now, remote=True)
    for cid, dev in c.execute("SELECT l.call_id, l.device FROM chat_call_leg l JOIN chat_call k ON k.id=l.call_id WHERE l.peer_id=? "
                              "AND l.state='active' AND k.state<>'ended' AND k.lead IS NULL", (p["id"],)).fetchall():
        call = calls._load(c, cid, lock=True)
        if call and call["state"] != "ended":
            calls._leave(c, call, dev, now, "gone")


F.PEER_GONE_HOOKS.append(_peer_gone)


# =====================================================================================================
# 2.5 P10: Gjallarhorn ueber Servergrenzen (saga/fed_horn.py)
# =====================================================================================================
OPS_HORN = ()                           # von saga.fed_horn gesetzt
HORN_MESH = {}                          # call_id -> Hinweis "ohne Knoten" (Fall c), auch in Kopien


def _horn_hint(caps):
    """Beim Start/Beitritt an das Heim: wie viele eigene Knoten bereit sind (Topologie-Entscheidung dort)."""
    if not calls.horn_on() or not calls._has(caps, "horn"):
        return {}
    return {"horn_nodes": FH.own_ready()}


def _mesh_in(cid, d):
    if isinstance(d, dict) and isinstance(d.get("horn_mesh"), str):
        HORN_MESH[cid] = d["horn_mesh"][:500]
        if len(HORN_MESH) > 5000:
            HORN_MESH.clear()


def horn_fed_ok(c, ch):
    return FH.fed_ok(c, ch)


def horn_prefetch(chat_id):
    return FH.prefetch(chat_id)


def horn_block(uid, call, dev, role, dev_class="desktop", avoid=(), node=None):
    return FH.block(uid, call, dev, role, dev_class, avoid, node)


calls.FED = __import__("sys").modules[__name__]
from saga import fed_horn as FH  # noqa: E402  (registriert seine Auftraege in HANDLERS)
