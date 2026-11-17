"""1:1 -> Konferenz (2.5, L9): POST /api/calls/{id}/add in einem laufenden 1:1-Anruf ueber einen Knoten.

Entscheidung E1: Es entsteht ein NEUER freier Gruppen-Chat (kind 'free') mit beiden Personen und den Hinzugefuegten; der
Anruf zieht nahtlos um; das alte 1:1 bekommt den Eintrag "In Gruppenanruf verschoben" (Verweis auf den neuen Chat).
Kein Ad-hoc-Anruf ohne Chat.

Warum ohne Neuaufbau: Unter Grundsatz Y ist ein 1:1 schon ein Raum-Anruf (kind 'group', callee gesetzt, saga/calls.py
_dmh) - ueber einen Knoten (topo 'horn') bzw. nach dem Horn-Fix-Rueckfall als Mesh-Raum (topo 'mesh'). Der Umzug haengt
den Anruf nur um (chat_id = neuer Chat, callee leer = normaler Gruppenanruf). Raum am Knoten, Beine, Frame-Schluessel,
Beginn (answered, Dauer laeuft weiter) und Gastgeber (caller) bleiben. Die Neuen klingeln wie bei L8 (invite_local:
call.invite, Push call.group) und treten mit POST /join bei - dabei ratschen die Geraete ihren Frame-Schluessel
(HORN-E2E §4: fkey nur an den Neuen, K' = R(K)), Neue koennen also nichts Vorheriges entschluesseln.

Atomar: Chat anlegen, Mitglieder, Anruf umhaengen, Eintrag im alten Chat und Klingeln in EINER Transaktion (Zeile des
Anrufs gesperrt); jeder Fehler vorher -> nichts geaendert. Rechte (E5): beide Personen des 1:1 (im neuen Chat beide
Admin - jede darf danach weitere Personen hinzufuegen). Rate: Topf von /invite und /add, dazu die Grenze fuer neue Chats.

Nicht umgezogen (409 mit move_block und fertigem Text, nichts geaendert):
- "direct": 1:1 direkt (kind 'dm', Rueckfall unter Y bzw. 2.3/2.4). Ein Umzug ginge nur mit Neuaufbau der Medien: unter Y
  laeuft ein 1:1 nur dann direkt, wenn ein Geraet keinen Knoten kann bzw. erreicht oder keiner bereit ist - dann gaebe es
  auch keinen Weg auf den Knoten; ein Mesh-Raum im neuen Chat braeuchte den Wechsel kind dm -> group in allen Clients
  (Apps koennen das nicht). Deshalb ein klarer Hinweis statt eines Uebergangs mit Luecke (Variante C, verworfen).
- "fed": Anruf ueber Servergrenzen (foederiertes 1:1). Personen anderer Server in einem lokalen 1:1: einzeln in failed
  (kind 'fed') - der Chat des Anrufs wuerde mitten im Anruf foederiert (Format saga-call/v2).
- "ringing": noch nicht angenommen; "ended".

Vertrag: docs/APP-API.md "Anrufe: Personen hinzufuegen (L8)" -> "1:1 -> Konferenz (L9)"; Feature call_move."""
import secrets
import time

from db import connect as _db
from saga import calls, chat, contacts, sharing
from saga.core import _bad, t
from saga.sharing import names

FEATURE = calls.FEATURE_MOVE
CHATS_PER_HOUR = 60                     # wie POST /api/chats (neue Chats je Konto und Stunde)


def block(c, call):
    """Warum dieser 1:1-Anruf nicht in eine Gruppe umziehen kann -> None (geht) | direct | fed | ringing | ended."""
    if call["state"] == "ended":
        return "ended"
    if call["kind"] != "group":
        return "fed" if call.get("peer_id") else "direct"
    if calls._fedcall(c, call):
        return "fed"
    if call["state"] != "active":
        return "ringing"
    return None


def hint(why):
    return {"direct": t("Dieser Anruf läuft direkt zwischen euren Geräten – jemanden dazuholen geht nur über einen Anruf-Knoten. "
                        "Legt dafür auf und startet einen Gruppenanruf in einer neuen Gruppe."),
            "fed": t("In einem 1:1-Anruf mit einer Person eines anderen Saga-Servers kann man noch niemanden dazuholen – startet "
                     "dafür einen Gruppenanruf in einer Gruppe."),
            "ringing": t("Erst wenn das Gegenüber angenommen hat, kann man jemanden dazuholen."),
            "ended": t("Der Anruf ist schon beendet.")}.get(why, "")


def fields(c, call, uid, d):
    """Anruf-Objekt eines 1:1 (auch direkt): can_move = Personen hinzufuegen zieht in eine neue Gruppe um; sonst
    move_block + move_hint (fertiger Text fuer den Knopf/Hinweis)."""
    why = block(c, call)
    d["can_move"] = why is None
    if why:
        d["move_block"], d["move_hint"] = why, hint(why)


def _err(why, **kw):
    return chat._no_store(dict({"error": hint(why), "dm": True, "move_block": why}, **kw), 409)


def _norm(x):
    w = x.strip()
    lw = w.lower()
    return w, (lw[1:] if lw.startswith("@~") else lw)


async def move(uid, cid, raw):
    """POST /api/calls/{id}/add im 1:1 {uids} -> Antwort wie /add (L8) plus moved {from_chat, chat_id} und call (Anruf-
    Objekt nach dem Umzug), bzw. Fehler ohne Aenderung. None: der Anruf ist inzwischen schon ein Gruppenanruf (die andere
    Person war schneller) - weiter wie L8 im neuen Chat."""
    from saga import call_add as CA
    now = time.time()
    with _db() as c, c:
        call = calls._load(c, cid, lock=True)
        if not call or not calls._part(c, uid, call):
            return _bad("not found", 404)
        if call["kind"] == "group" and not calls._dmh(call):
            return None
        why = block(c, call)
        if why:
            return _err(why, **({"ended": True} if why == "ended" else {}))
        old = chat._load(c, call["chat_id"])
        peer = call["callee"] if call["caller"] == uid else call["caller"]
        if not old or old["kind"] != "dm" or not peer:
            return _bad("not found", 404)
        fed = chat.FED is not None
        want, failed, src = [], [], {}                     # src: Konto -> Eingabe (wie gesendet, fuer added/failed)
        for x in raw:
            w, lw = _norm(x)
            m = chat._FULL_H.fullmatch(lw)
            if m and fed and chat.FED.fedsig.authority(m.group(2)) != chat.FED.F.SELF_HOST:
                failed.append({"who": w, "kind": "fed", "error": t("Personen anderer Saga-Server kann man nicht in einen 1:1-Anruf holen – "
                                                                    "erst in der neuen Gruppe hinzufügen.")})
                continue
            u = chat.who_in(c, uid, lw if m else w)
            if u in (uid, peer):
                continue                                   # schon im Anruf
            if not u or sharing.is_remote(u) or not chat.may_contact(c, uid, u):
                failed.append({"who": w, "kind": "unreachable", "error": t("Mit diesem Konto ist kein Chat möglich.")})
                continue
            if u not in src:
                src[u] = w
                want.append(u)
        want = want[:CA.ADD_MAX]
        if want and chat.is_v3(old):                       # Stufe 3: der neue Chat muss es auch sein (Format des Anrufs bleibt)
            nc = chat.no_cert(c, want)
            nm0 = names(c, sorted(nc))
            failed += [{"who": src[u], "kind": "old", "error": t("{name} braucht eine neuere Saga-Version, um dazuzukommen.",
                                                                  name=nm0.get(u) or t("Diese Person"))} for u in want if u in nc]
            want = [u for u in want if u not in nc]
        if not want:
            if failed:
                f = failed[0]
                return chat._no_store({"error": f["error"], "failed": failed, "dm": True, **({"fed": True} if f["kind"] == "fed" else {})},
                                      409 if f["kind"] in ("fed", "old") else 404)
            return chat._no_store({"error": t("Diese Person ist schon im Anruf."), "dm": True, "already": True}, 409)
        if len(want) + 2 > chat.MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=chat.MEMBERS_MAX), 409)
        legs = calls._legs(c, cid)
        st = CA.seats(c, call, legs)
        if st["left"] <= 0:
            return chat._no_store({"error": CA.full_msg(st), "full": True, "seats": st, "dm": True}, 409)
        if c.execute("SELECT COUNT(*) FROM chat WHERE created_by=? AND created>?", (uid, now - 3600)).fetchone()[0] >= CHATS_PER_HOUR:
            return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
        if not calls._rate("invite", uid, calls.CALL_RATE):  # derselbe Topf wie /invite und /add
            return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
        # --- ab hier wird geschrieben (eine Transaktion) -------------------------------------------------------------
        nid = c.execute("INSERT INTO chat (kind, created_by, created) VALUES ('free',?,?) RETURNING id", (uid, now)).fetchone()[0]
        for u, role in [(uid, "admin"), (peer, "admin")] + [(u, "member") for u in want]:
            c.execute("INSERT INTO chat_member (chat_id, uid, role, joined) VALUES (?,?,?,?)", (nid, u, role, now))
        if chat.is_v3(old):                                # wie make_v3, aber ohne Einstellung/Bereitschaft (oben geprueft)
            c.execute("UPDATE chat SET v=3, ref=? WHERE id=?", (secrets.token_hex(16) + "@" + chat.FED.F.SELF_HOST, nid))
        contacts.link(c, nid)
        c.execute("UPDATE chat_call SET chat_id=?, callee='' WHERE id=?", (nid, cid))
        calls._log(c, call, "ended", now, extra={"moved": nid})   # altes 1:1: "In Gruppenanruf verschoben" (Dauer bis jetzt)
        new = calls._load(c, cid)
        chat._changed(c, nid)
        chat._changed(c, old["id"])
        data = {"call_id": cid, "from_chat": old["id"], "chat_id": nid, "kind": "group", "media": new["media"]}
        calls.bus.emit_after(c, "direct_each", {"items": [(v, "call.move", dict(data, by=calls._pp(c, new, uid, v)))
                                                          for v in sorted({uid, peer})]})
        calls.emit_roster(c, new)
        n, _ = calls.invite_local(c, new, want)
        rv = calls._rv()
        lst, more = calls.invites(c, new, uid)
        st = CA.seats(c, new)
        d = calls._dict(c, new, uid)
    added = [src[u] for u in want]
    print(f"call: {cid[:8]} 1:1 -> Gruppe (Chat {old['id']} -> {nid}, {len(want)} neu, {len(failed)} nicht, {n} klingeln)", flush=True)
    return chat._no_store({"ok": True, "n": n, "added": added, "failed": failed, "pending": [], "request": [], "invites": lst,
                           "invites_more": more, "seats": st, "rv": rv, "now": round(time.time(), 3),
                           "moved": {"from_chat": old["id"], "chat_id": nid}, "call": d})


calls.MOVE = __import__("sys").modules[__name__]
