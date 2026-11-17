"""Personen zu einem laufenden Gruppenanruf holen (2.5, L8): POST /api/calls/{id}/add - auch von ausserhalb des Chats.

Entscheidungen (E2, E5, E7): Wer schon Mitglied des Chats ist, klingelt (erneut) wie bei POST /invite. Wer nicht Mitglied
ist, wird es zuerst - ueber dieselbe Funktion wie "Person hinzufuegen" in der Gruppeninfo (chat.member_add, also mit
denselben Rechten, Schluesseln, Personen anderer Server und Seitenfreigaben) - und klingelt danach. Keine Anruf-Gaeste
ohne Chat. Klingeln duerfen alle Mitglieder; neue Mitglieder hinzufuegen nur, wer es im Chat darf (chat.add_right).
Seiten-Chats: Hinzufuegen teilt die Seite - nur mit confirm_page (sonst 409 page_confirm mit Titel, der Client fragt).
1:1-Anrufe: 409 {dm: true} (L9 "1:1 -> Konferenz" kommt eigens). Voll (alle Plaetze belegt, jedes Geraet zaehlt, auch ein
zweites zum Teilen): 409 {full: true} - es gibt keinen Umzug eines laufenden direkten Anrufs auf einen Knoten.
Ueber Servergrenzen: Mitglied wird man ueber das Heim des Chats (chat.member_add -> fed_chat), geklingelt wird ueber den
fuehrenden Server (wie /invite, saga/fed_calls.py add_ring).

Vertrag: docs/APP-API.md "Anrufe: Personen hinzufuegen (L8)"; Feature call_add in GET /api/calls/config."""
import json
import time

from fastapi import APIRouter, Request

from db import connect as _db
from saga import calls, chat
from saga.core import _bad, _body, t
from saga.sharing import names

router = APIRouter()

FEATURE = calls.FEATURE_ADD
ADD_MAX = 10                            # neue Mitglieder je Anfrage (Klingeln bekannter Mitglieder wie /invite bis 50)
KIND = {403: "rights", 404: "unreachable", 409: "conflict", 502: "server", 400: "invalid"}


def _status(r):
    """Antwort von chat.member_add -> (status, daten)."""
    try:
        d = json.loads(bytes(r.body).decode() or "{}")
    except (ValueError, AttributeError):
        d = {}
    return r.status_code, d if isinstance(d, dict) else {}


def _spec(x, c=None, uid=None):
    """Eingabe (Benutzername | Handle '~x@host' | Prinzipal '@~x@host') -> Angabe fuer chat.member_add. Mit c: Handle
    eines Kontos dieses Servers als {user} (wie die Gruppeninfo; {handle} meint dort Personen anderer Server)."""
    v = x.strip()
    w = v.lower()
    if w.startswith("@~"):
        w = w[1:]
    m = chat._FULL_H.fullmatch(w)
    if m:
        if c is not None and (chat.FED is None or chat.FED.fedsig.authority(m.group(2)) == chat.FED.F.SELF_HOST):
            u = chat.who_in(c, uid, w)
            if u:
                return {"user": u}
        return {"handle": w}
    return {"user": v}


def _label(c, uid, x):
    """Anzeigename einer Person, die (noch) nicht Mitglied ist (Bestaetigung im Seiten-Chat) - nie der Anmeldename anderer."""
    sp = _spec(x)
    if "handle" in sp:
        u = chat.who_in(c, uid, sp["handle"])
        if not u and calls.FED is not None:
            return calls.FED.FC.label_of(c, "@" + sp["handle"])["name"]
    else:
        u = chat.who_in(c, uid, sp["user"].lower())
    return (names(c, [u]).get(u) if u else None) or t("Diese Person")


def _local(c, uid, x):
    """Konto dieses Servers hinter der Eingabe (fuer die Pruefung 'entfernt') oder ''."""
    sp = _spec(x)
    return chat.who_in(c, uid, sp.get("handle") or sp["user"].lower()) or ""


def _page_title(c, ch):
    r = c.execute("SELECT title FROM note WHERE id=?", (ch.get("note_id"),)).fetchone()
    return (r[0] if r else "") or t("Unbenannt")


def seats(c, call, legs=None):
    """Belegte und moegliche Plaetze (Endpunkte = Geraete, auch Begleitgeraete) -> {used, limit, left, topo}."""
    legs = calls._legs(c, call["id"]) if legs is None else legs
    lim = calls.climit(c, call)
    return {"used": len(legs), "limit": lim, "left": max(0, lim - len(legs)), "topo": call.get("topo") or "mesh"}


def full_msg(s):
    return t("Der Anruf ist voll – alle {n} Plätze sind belegt (jedes Gerät zählt, auch ein zweites zum Teilen). "
             "Erst wenn jemand auflegt, kann noch jemand dazukommen.", n=s["limit"])


@router.post("/api/calls/{cid}/add")
async def api_add(cid: str, request: Request):
    """Personen in den laufenden Gruppenanruf holen {uids: [benutzername | Handle | Prinzipal], confirm_page?, role?} ->
    {ok, n, added, failed, pending, invites, invites_more, seats, rv, now}. Mitglieder klingeln (wie /invite), andere werden
    zuerst Mitglied (chat.member_add). Fehler ohne jede Aenderung: 403 (keine Rechte bzw. entfernte Person ohne Gastgeber),
    409 page_confirm (Seiten-Chat ohne Bestaetigung) | full | dm | ended, 429 (Rate wie /invite). Scheitern alle Neuen,
    kommt der Fehler der ersten (Status und Text wie POST /api/chats/{id}/members)."""
    uid, err = calls._me(request)
    if err:
        return err
    b = await _body(request) or {}
    raw = [u.strip() for u in (b.get("uids") or []) if isinstance(u, str) and 0 < len(u.strip()) < 300][:chat.MEMBERS_MAX]
    raw = list(dict.fromkeys(raw))
    role = b.get("role") or "view"
    if not raw or role not in chat.PAGE_ADD_ROLES:
        return _bad(t("ungültige Angabe"))
    with _db() as c:
        call, err = calls._mine(c, uid, cid)
        if err:
            return err
        if call["kind"] != "group" or calls._dmh(call):
            return chat._no_store({"error": t("In einem 1:1-Anruf kann man noch niemanden dazuholen – beginnt dafür einen Gruppenanruf "
                                              "in einer Gruppe."), "dm": True}, 409)
        if call["state"] == "ended":
            return chat._no_store({"error": t("Der Anruf ist schon beendet."), "ended": True}, 409)
        ch, m, mem, err = chat._chat_for(c, uid, call["chat_id"], do_sync=False)
        if err:
            return err
        known, new = [], []
        for x in raw:
            u = chat.member_target(c, uid, mem, x)
            (known.append(u) if u else new.append(x))
        new = new[:ADD_MAX]
        legs = calls._legs(c, call["id"])
        st = seats(c, call, legs)
        inside = {y["who"] for y in legs}
        if st["left"] <= 0 and (new or any(u not in inside for u in known)):
            return chat._no_store({"error": full_msg(st), "full": True, "seats": st}, 409)
        if new:
            can, why = chat.add_right(c, uid, ch, m)
            if not can:
                return chat._no_store({"error": why, "rights": True}, 403)
        if uid != call["caller"] and calls._any_removed(c, call, known + [u for u in (_local(c, uid, x) for x in new) if u]):
            return chat._no_store({"error": t("Nur wer den Anruf begonnen hat, kann entfernte Personen erneut klingeln lassen."),
                                   "removed": True}, 403)
        if new and ch["kind"] == "page" and b.get("confirm_page") is not True:
            nm = [_label(c, uid, x) for x in new]
            title = _page_title(c, ch)
            msg = (t("{name} bekommt Zugriff auf die Seite „{title}“.", name=nm[0], title=title) if len(nm) == 1 else
                   t("{names} bekommen Zugriff auf die Seite „{title}“.", names=", ".join(nm), title=title))
            return chat._no_store({"error": msg, "page_confirm": True, "title": title, "names": nm}, 409)
    if not calls._rate("invite", uid, calls.CALL_RATE):        # derselbe Topf wie /invite
        return _bad(t("Zu viele Anrufe in kurzer Zeit – bitte kurz warten."), 429)
    with _db() as c:
        specs = {x: _spec(x, c, uid) for x in new}
    added, failed, first = [], [], None
    for x in new:
        sp = specs[x]
        if ch["kind"] == "page":
            sp["role"] = role
        s, d = _status(await chat.member_add(uid, ch["id"], sp))
        if s == 200:
            added.append(x)
        else:
            failed.append({"who": x, "error": d.get("error") or t("Hinzufügen hat nicht geklappt."), "kind": KIND.get(s, "conflict")})
            first = first or (s, d)
    if new and not added and not known:
        s, d = first
        return chat._no_store(dict(d, failed=failed), s)
    with _db() as c, c:
        call = calls._load(c, cid, lock=True)
        if not call or call["state"] == "ended":
            return chat._no_store({"error": t("Der Anruf ist schon beendet."), "ended": True, "added": added, "failed": failed}, 409)
        mem = chat.members(c, call["chat_id"])
        lead = call.get("lead") is not None and calls.FED is not None
        fresh, pending, via_lead = [], [], []
        for x in added:
            u = chat.member_target(c, uid, mem, x)
            lu = "" if u else _local(c, uid, x)
            if u:
                fresh.append(u)
            elif lead and lu:
                via_lead.append(lu)        # Kopie: das Heim hat das Konto aufgenommen, die Liste hier kommt gleich nach
            else:
                pending.append(x)          # Person anderer Server (Seiten-Chat): kommt dazu, sobald ihr Server bestaetigt
        who = list(dict.fromkeys(known + fresh))
        n, remote = calls.invite_local(c, call, who)
        new_remote = [u for u in remote if u in fresh]
        if new_remote and not lead and calls._fedcall(c, call):
            calls.FED.add_ring(c, call, new_remote)           # neue Server bekommen die Liste (Kopie des Anrufs) und klingeln
            remote = [u for u in remote if u not in new_remote]
        else:
            new_remote = []
        rv = calls._rv()
        lst, more = calls.invites(c, call, uid)
        st = seats(c, call)
    if (remote or via_lead) and calls.FED is not None:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(calls.FED.add_invite, uid, call, remote, via_lead)
    n += len(remote) + len(via_lead) + len(new_remote)
    print(f"call: {cid[:8]} hinzufuegen ({len(known)} klingeln, {len(added)} neu, {len(failed)} nicht, {len(pending)} warten)", flush=True)
    return chat._no_store({"ok": True, "n": n, "added": added, "failed": failed, "pending": pending, "invites": lst,
                           "invites_more": more, "seats": st, "rv": rv, "now": round(time.time(), 3)})
