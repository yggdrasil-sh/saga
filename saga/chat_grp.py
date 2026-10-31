"""Gruppen (2.5, Chat-Standard Abschnitt B): Gruppenbild + Beschreibung, Gruppenrechte, Admins loeschen fremde Nachrichten,
angeheftete Nachrichten, Blockieren lokaler Konten, Melden an die Saga-Admins.

Grundsatz Ende-zu-Ende: der Server sieht keinen neuen Klartext.
- Bild und Beschreibung freier Gruppen sind wie der Name verschluesselte Steuer-Nachrichten (kind 'desc' / 'avatar', Inhalt
  {v:1, type:'desc', desc} bzw. {v:1, type:'avatar', file:{...}|null}; das Bild ist ein verschluesselter Anhang wie bei
  Bild-Nachrichten). Der Server merkt sich nur die Folgenummer (chat.desc_seq / chat.avatar_seq) und loescht die vorige.
- Rechte (chat.perm_info / chat.perm_send: 'all' | 'admins') setzt der Server durch: Steuer-Nachrichten (title/desc/avatar)
  nur von Berechtigten, normale Nachrichten bei perm_send 'admins' nur von Admins. Ueber Servergrenzen setzt das Heim durch.
- Admin-Loeschen hinterlaesst einen Grabstein (chat_msg.kind 'tomb', ohne Ciphertext, Schluessel und Anhaenge).
- Angeheftet (chat_pin) wird nur die Kennung - der Inhalt bleibt verschluesselt.
- Meldungen (chat_report): nur der Text, den die meldende Person selbst mitschickt, plus Kennungen/Zeiten/Absender.

Foederation (Faehigkeiten groupinfo2, moddel1, msgpin1, block2, report1; Auftraege an das Heim, Ereignisse vom Heim):
  Auftrag 'perm' {ref, actor, perm_info?, perm_send?}  -> Ereignis 'chat.perm' {ref, perm_info, perm_send, by}
  Auftrag 'pin'  {ref, actor, mid, on}                 -> Ereignis 'chat.pins' {ref, pins:[mid...]}
  Auftrag 'del'  {ref, actor, id, mod:true}            -> Ereignis 'chat.msg.del' {ref, mid, by_admin:true}
  'chat.members' traegt fuer Gegenstellen mit groupinfo2/msgpin1 zusaetzlich perm_info, perm_send, pins."""
import json
import time

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import chat, fed_chat as FC, federation as F, notify, sharing
from saga.core import ADMIN_URL, HANDLE_RE, _bad, _body, resolve_ref, t, user_info

router = APIRouter()

INFO_KINDS = ("title", "desc", "avatar")             # Steuer-Nachrichten (verschluesselt) einer freien Gruppe
INFO_COL = {"title": "title_seq", "desc": "desc_seq", "avatar": "avatar_seq"}
PERMS = ("all", "admins")
PINS_MAX = 3
REASONS = ("spam", "abuse", "illegal", "other")
REPORT_TEXT_MAX = 2000
REPORT_MSGS_MAX = 20
REPORT_RATE_H = 5                  # Meldungen je Konto und Stunde
REPORT_DAY = 20                    # ... und je Tag (Datenbank, ueberlebt Neustarts)
FED_GRP_RATE = 120                 # Auftraege perm/pin je Instanz und Minute


# =====================================================================================================
# Rechte
# =====================================================================================================
def perms(c, cid):
    r = c.execute("SELECT perm_info, perm_send FROM chat WHERE id=?", (cid,)).fetchone()
    return (r[0] or "all", r[1] or "all") if r else ("all", "all")


def info_ok(c, ch, role):
    """Darf ein Mitglied mit dieser Rolle Name/Bild/Beschreibung aendern? (nur freie Gruppen)"""
    return ch["kind"] == "free" and (perms(c, ch["id"])[0] == "all" or role == "admin")


def send_ok(c, ch, role):
    return ch["kind"] != "free" or perms(c, ch["id"])[1] == "all" or role == "admin"


def no_send_msg():
    return t("In dieser Gruppe dürfen nur Admins schreiben.")


def no_info_msg():
    return t("Name, Bild und Beschreibung dieser Gruppe ändern nur Admins.")


def _dm_other(c, ch, uid, mem):
    return next((u for u in mem if u != uid), None) if ch["kind"] == "dm" else None


def send_check(c, ch, uid, role, kind, mem):
    """Senden durch ein Konto DIESES Servers (v1, Stufe 3, Kopien foederierter Chats) -> Fehlerantwort oder None."""
    kind = kind or "msg"
    if kind in INFO_KINDS:
        if ch["kind"] != "free":
            return _bad(t("ungültige Angabe"))
        if not info_ok(c, ch, role):
            return _bad(no_info_msg(), 403)
        if kind != "title" and not home_can(c, ch, "groupinfo2"):
            return _bad(t("Der Server der Gruppe ({host}) ist zu alt für Bild und Beschreibung.", host=FC.home_host(c, ch) or "?"), 409)
        return None
    if not send_ok(c, ch, role):
        return _bad(no_send_msg(), 403)
    other = _dm_other(c, ch, uid, mem)
    if other and not sharing.is_remote(other):        # Direkt-Chat mit einem Konto dieses Servers: gegenseitig gesperrt?
        if blocked_by(c, uid, other):
            return _bad(t("Du hast diese Person gesperrt – erst in „Chats & Geräte“ entsperren."), 403)
        if blocked_by(c, other, uid):
            return _bad(t("Diese Nachricht kann nicht zugestellt werden."), 403)
    return None


def home_send_check(c, ch, p_, kind):
    """Heim: Nachricht einer Person eines anderen Servers (op_send) -> Fehler-Kennung oder None."""
    kind = kind or "msg"
    role = FC._role(c, ch["id"], p_)
    if kind in INFO_KINDS:
        return None if info_ok(c, ch, role) else "not allowed"
    return None if send_ok(c, ch, role) else "not allowed"


# =====================================================================================================
# Steuer-Nachrichten title/desc/avatar: aktuelle Nachricht merken, vorige weg
# =====================================================================================================
def set_info(c, ch, kind, seq):
    """Neue Steuer-Nachricht (in der Transaktion): Folgenummer merken, vorige samt Anhaengen loeschen."""
    col = INFO_COL[kind]
    old = c.execute(f"SELECT {col} FROM chat WHERE id=?", (ch["id"],)).fetchone()[0]
    c.execute(f"UPDATE chat SET {col}=? WHERE id=?", (seq, ch["id"]))
    if old and old != seq:
        gone = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (old,))]
        c.execute("DELETE FROM chat_file WHERE msg_seq=?", (old,))
        c.execute("DELETE FROM chat_msg WHERE seq=?", (old,))
        chat._rm_blobs(c, gone)


def forget_seqs(c, where, args):
    """purge/Kontoloeschung: Verweise auf geloeschte Steuer-Nachrichten loesen (where z. B. 'IN (1,2)')."""
    for col in ("desc_seq", "avatar_seq"):
        c.execute(f"UPDATE chat SET {col}=NULL WHERE {col} {where}", args)


def _info_msg(c, uid, ch, device, seq):
    rows = chat._msgs(c, uid, ch["id"], device, "AND m.seq=?", (seq,), 1)
    if not rows:
        return None
    devs, ids = chat._senders(c, rows)
    out = dict(chat._msg_dict(rows[0], chat.names(c, [rows[0][3]])), device=devs.get(rows[0][4]), ident_pub=ids.get(rows[0][3]))
    if chat.hfmt(ch) and out.get("sender_handle"):   # Format v2: Absender als Handle, ohne v1-Geraetedaten (Anmeldename)
        if out.get("sender") and out["sender"].get("username") != uid:
            out["sender"] = dict(out["sender"], username=out["sender_handle"])
        out.pop("device", None)
        out.pop("ident_pub", None)
    return out


# =====================================================================================================
# Felder im Chat-Objekt
# =====================================================================================================
def _home_peer(c, ch):
    return F.peer_by(c, "id", ch["home_peer"]) if ch.get("home_peer") is not None else None


def home_can(c, ch, cap):
    """Kann das Heim des Chats die Faehigkeit? (eigene Chats: ja)"""
    return ch.get("home_peer") is None or F.feature(_home_peer(c, ch), cap)


def moderator(c, uid, ch, role):
    """Darf uid fremde Nachrichten fuer alle loeschen? Admin der freien Gruppe, Eigentuemer der Seite (Seiten-Chat mit Heim
    hier), Saga-Admin (Gruppen-Chat)."""
    if ch["kind"] == "free":
        return role == "admin"
    if ch["kind"] == "page" and ch["note_id"] and ch.get("home_peer") is None:
        return bool(c.execute("SELECT 1 FROM note WHERE id=? AND uid=?", (ch["note_id"], uid)).fetchone())
    if ch["kind"] == "group":
        u = user_info(uid)
        return bool(u and u.get("is_admin"))
    return False


def pin_ok(c, ch, role):
    if ch["kind"] == "free":
        return perms(c, ch["id"])[0] == "all" or role == "admin"
    return True


def pins_of(c, cid):
    return [r[0] for r in c.execute("SELECT msg_id FROM chat_pin WHERE chat_id=? ORDER BY at, msg_seq", (cid,)).fetchall()]


def chat_fields(c, uid, ch, m, d, device=None):
    """chat.GRP (aus chat._chat_dict, zuletzt): perm_info, perm_send, can_send, can_edit_info (can_rename folgt), can_perms,
    can_pin, can_moderate, pins, desc_msg, avatar_msg; members[].blocked (von mir gesperrt)."""
    role = (m or {}).get("role")
    r = c.execute("SELECT perm_info, perm_send, desc_seq, avatar_seq FROM chat WHERE id=?", (ch["id"],)).fetchone()
    pi, ps = (r[0] or "all", r[1] or "all") if r else ("all", "all")
    po = lambda: pi == "all" or role == "admin"          # noqa: E731
    ro = bool(d.get("readonly"))
    free = ch["kind"] == "free"
    info2 = home_can(c, ch, "groupinfo2")
    d.update(perm_info=pi, perm_send=ps,
             can_send=not ro and (not free or ps == "all" or role == "admin"),
             can_edit_info=free and not ro and po(),
             can_perms=free and not ro and role == "admin" and info2,
             can_info2=free and not ro and po() and info2,       # Bild + Beschreibung moeglich (Heim kann groupinfo2)
             can_pin=not ro and (not free or po()) and home_can(c, ch, "msgpin1") and (m or {}).get("state", "ok") == "ok",
             can_moderate=not ro and moderator(c, uid, ch, role) and home_can(c, ch, "moddel1"),
             pins=pins_of(c, ch["id"]))
    d["can_rename"] = bool(d.get("can_rename")) and d["can_edit_info"]
    if free and r:
        for key, seq in (("desc_msg", r[2]), ("avatar_msg", r[3])):
            if seq:
                x = _info_msg(c, uid, ch, device, seq)
                if x:
                    d[key] = x
    mine = blocks_of(c, uid)
    if mine:
        for x in d.get("members", []):
            if not x.get("me") and _member_handle(c, x) in mine:
                x["blocked"] = True


def _member_handle(c, x):
    """Handle eines Eintrags members[] (fuer den Vergleich mit der Sperrliste): '~x@host' - legt keinen Handle an."""
    h = x.get("handle")
    if h:
        return h
    u = x.get("username")
    if not u:
        return None
    if sharing.is_remote(u):
        return FC.handle_of(u)
    r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (u,)).fetchone()
    return FC.FK.full_handle(r[0]) if r else None


# =====================================================================================================
# Gruppenrechte: PATCH /api/chats/{cid} {perm_info, perm_send}
# =====================================================================================================
PATCH_KEYS = ("perm_info", "perm_send")


def patch_fields(b):
    """Feldgruppe 'Rechte' aus dem PATCH-Koerper -> (felder, fehler)."""
    want = {k: b[k] for k in PATCH_KEYS if k in b}
    if any(v not in PERMS for v in want.values()):
        return None, _bad(t("ungültige Angabe"))
    return want, None


def _perm_set(c, ch, want, by):
    """Rechte setzen (in der Transaktion) -> geaendert? Systemzeile im Format v2."""
    pi, ps = perms(c, ch["id"])
    new = {"perm_info": want.get("perm_info", pi), "perm_send": want.get("perm_send", ps)}
    if (new["perm_info"], new["perm_send"]) == (pi, ps):
        return False
    c.execute("UPDATE chat SET perm_info=?, perm_send=? WHERE id=?", (new["perm_info"], new["perm_send"], ch["id"]))
    if chat.hfmt(ch):
        sd = {"type": "perm", "by": FC.who(c, by) if by else None}
        if new["perm_info"] != pi:
            sd["perm_info"] = new["perm_info"]
        if new["perm_send"] != ps:
            sd["perm_send"] = new["perm_send"]
        FC.sysline(c, ch, None, sd)
    chat._changed(c, ch["id"], FC.local_members(c, ch["id"]) if chat.hfmt(ch) else None)
    return True


def patch_local(c, uid, ch, m, want):
    """Lokaler Chat (v1/Stufe 3) bzw. Heim hier -> Fehler oder None. Nur Admins freier Gruppen."""
    if ch["kind"] != "free":
        return _bad(chat.not_free_msg(), 400)
    if m["role"] != "admin":
        return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    if chat.is_fed(ch) and ch["fstate"]:
        return _bad(FC._ro_group(c, ch) or t("ungültige Angabe"), 403)
    if _perm_set(c, ch, want, uid) and chat.is_fed(ch):
        _fan_perm(c, ch, uid)
    return None


def _fan_perm(c, ch, by):
    pi, ps = perms(c, ch["id"])
    FC.fanout(c, ch, {"type": "chat.perm", "perm_info": pi, "perm_send": ps, "by": FC.who(c, by) if by else None}, cap="groupinfo2")


def patch_fed(uid, ch, want):
    """Gruppe mit Heim auf einem anderen Server: Auftrag 'perm' an das Heim (synchron) -> Fehler oder None."""
    with _db() as c:
        if ch["kind"] != "free":
            return _bad(chat.not_free_msg(), 400)
        m = chat._member(c, ch["id"], uid)
        if not m or m["role"] != "admin":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        ro = FC._ro_group(c, ch)
        if ro:
            return _bad(ro, 403)
        p = _home_peer(c, ch)
        if not F.feature(p, "groupinfo2"):
            return _bad(t("Der Server der Gruppe ({host}) ist zu alt für Gruppenrechte.", host=FC.home_host(c, ch) or "?"), 409)
        mh = FC.my_handle(c, uid)
    status, d = FC.sync_call(p, dict({"op": "perm", "ref": ch["ref"], "actor": mh}, **want))
    if status is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"]), 502)
    if status == 403:
        return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    if status != 200 or not isinstance(d, dict):
        return _bad(t("Der andere Saga-Server hat die Änderung abgewiesen ({err}).", err=str((d or {}).get("error") or status)), 409)
    with _db() as c, c:
        ch2 = FC.by_ref(c, ch["ref"], lock=True)
        if ch2:
            _perm_set(c, ch2, {k: d[k] for k in PATCH_KEYS if d.get(k) in PERMS}, uid)
    return None


def op_perm(c, peer, d):
    """Heim: Rechte aendern im Auftrag eines Admins eines anderen Servers."""
    if not F.feature(peer, "groupinfo2"):
        return 403, {"error": "groupinfo2 disabled"}
    if not FC._rate_ok("cgrp2", peer["host"], FED_GRP_RATE):
        return 429, {"error": "too many"}
    ch = FC._home_chat(c, d, lock=True)
    h, p_ = FC._actor(c, peer, d)
    if not ch or not h or ch["kind"] != "free":
        return 404, {"error": "gone"}
    if ch["fstate"]:
        return 409, {"error": "readonly"}
    want = {k: d[k] for k in PATCH_KEYS if k in d}
    if any(v not in PERMS for v in want.values()):
        return 400, {"error": "bad request"}
    if FC._role(c, ch["id"], p_) != "admin":
        F.log(c, "chat.forged", peer, "perm not admin", throttle=(peer["id"], "fperm"))
        return 403, {"error": "not admin"}
    if _perm_set(c, ch, want, p_):
        _fan_perm(c, ch, p_)
    pi, ps = perms(c, ch["id"])
    return 200, {"ok": True, "perm_info": pi, "perm_send": ps}


def _by_of(c, by):
    """Angabe 'by' {handle, name, host} vom Heim -> Mitglied hier (Benutzername/Prinzipal) oder None."""
    h = by.get("handle") if isinstance(by, dict) else None
    n, host = FC.split_handle(h)
    if not n:
        return None
    return F.uid_by_handle(c, n) if host == F.SELF_HOST else FC.principal(h)


def _ev_perm(c, peer, ev):
    if not FC.peer_ok(peer) or not F.feature(peer, "groupinfo2"):
        return "ignored"
    ch = FC._from_home(c, peer, ev, lock=True)
    if not ch or ch["kind"] != "free":
        return "ignored"
    want = {k: ev[k] for k in PATCH_KEYS if ev.get(k) in PERMS}
    _perm_set(c, ch, want, _by_of(c, ev.get("by")))
    return "ok"


# =====================================================================================================
# Admins loeschen fremde Nachrichten (Grabstein)
# =====================================================================================================
def tomb(c, ch, mid):
    """Nachricht durch einen Grabstein ersetzen (in der Transaktion): Ciphertext, Schluessel, Anhaenge, KI-Beleg, Reaktionen,
    Bearbeitungen und Anheftung weg; Zeile bleibt (kind 'tomb', Absender und Zeit). -> seq oder None."""
    r = c.execute("SELECT seq FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg'", (mid, ch["id"])).fetchone()
    if not r:
        return None
    seq = r[0]
    fids = [x[0] for x in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (seq,))]
    c.execute("DELETE FROM chat_file WHERE msg_seq=?", (seq,))
    c.execute("DELETE FROM chat_ai_reply WHERE msg_id=?", (mid,))
    c.execute("DELETE FROM chat_msg_key WHERE msg_seq=?", (seq,))
    c.execute("DELETE FROM chat_reaction WHERE msg_seq=?", (seq,))
    c.execute("DELETE FROM chat_pin WHERE msg_seq=?", (seq,))
    c.execute("DELETE FROM chat_hidden WHERE msg_seq=?", (seq,))
    for fn in FC.DROP_HOOKS:                     # Bearbeitungen (saga.chat_x3)
        fn(c, ch, mid)
    c.execute("UPDATE chat_msg SET kind='tomb', body_ct='', sig='', size=0, ai=NULL WHERE seq=?", (seq,))
    chat._rm_blobs(c, fids)
    uids = FC.local_members(c, ch["id"]) if chat.hfmt(ch) else chat._fresh(c, ch, chat.members(c, ch["id"]))
    chat._direct(c, uids, "chat.deleted", {"chat_id": ch["id"], "id": mid, "seq": seq, "by_admin": True})
    return seq


def mod_delete(c, uid, ch, m, mid):
    """DELETE .../messages/{mid} fuer eine fremde Nachricht (lokal bzw. Heim hier; in der Transaktion) -> Antwort."""
    if not moderator(c, uid, ch, m.get("role")):
        return _bad(t("Nur eigene Nachrichten lassen sich löschen."), 403)
    if not tomb(c, ch, mid):
        return _bad("not found", 404)
    if chat.is_fed(ch):
        FC.fanout(c, ch, {"type": "chat.msg.del", "mid": mid, "by_admin": True})
    print(f"chat: Nachricht in Chat {ch['id']} von einem Admin geloescht", flush=True)
    return chat._no_store({"ok": True, "by_admin": True})


def mod_delete_fed(c, uid, ch, m, mid):
    """Fremde Nachricht in einer Gruppe mit Heim auf einem anderen Server: hier Grabstein, Auftrag 'del' (mod) ans Heim."""
    if not moderator(c, uid, ch, m.get("role")):
        return _bad(t("Nur eigene Nachrichten lassen sich löschen."), 403)
    if not F.feature(_home_peer(c, ch), "moddel1"):
        return _bad(t("Der Server der Gruppe ({host}) ist zu alt, um fremde Nachrichten zu löschen.", host=FC.home_host(c, ch) or "?"), 409)
    if not tomb(c, ch, mid):
        return _bad("not found", 404)
    F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "del", "ref": ch["ref"], "id": mid, "actor": FC.my_handle(c, uid),
                                                                   "mod": True}}, chain=FC.chain_up(ch["ref"]))
    return chat._no_store({"ok": True, "by_admin": True})


def op_del_mod(c, peer, ch, h, p_, mid):
    """Heim: Auftrag 'del' fuer eine fremde Nachricht (mod) -> (status, antwort)."""
    if not F.feature(peer, "moddel1") or not moderator(c, p_, ch, FC._role(c, ch["id"], p_)):
        F.log(c, "chat.forged", peer, "delete", throttle=(peer["id"], "fdel"))
        return 403, {"error": "not yours"}
    if tomb(c, ch, mid):
        FC.fanout(c, ch, {"type": "chat.msg.del", "mid": mid, "by_admin": True}, skip=peer["id"])
    return 200, {"ok": True}


# =====================================================================================================
# Angeheftete Nachrichten: POST/DELETE /api/chats/{cid}/pins/{mid}
# =====================================================================================================
def _pin_set(c, ch, mid, on, by):
    """-> 'ok' | 'same' | 'full' | 'gone'. Nur normale Nachrichten des Chats."""
    r = c.execute("SELECT seq FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg' AND (expires_at IS NULL OR expires_at>?)",
                  (mid, ch["id"], time.time())).fetchone()
    if on:
        if not r:
            return "gone"
        c.execute("SELECT id FROM chat WHERE id=? FOR UPDATE", (ch["id"],))
        if c.execute("SELECT 1 FROM chat_pin WHERE chat_id=? AND msg_seq=?", (ch["id"], r[0])).fetchone():
            return "same"
        if c.execute("SELECT COUNT(*) FROM chat_pin WHERE chat_id=?", (ch["id"],)).fetchone()[0] >= PINS_MAX:
            return "full"
        c.execute("INSERT INTO chat_pin (chat_id, msg_seq, msg_id, by_uid, at) VALUES (?,?,?,?,?)", (ch["id"], r[0], mid, by, time.time()))
        return "ok"
    n = c.execute("DELETE FROM chat_pin WHERE chat_id=? AND msg_id=?", (ch["id"], mid)).rowcount
    return "ok" if n else "same"


def _pins_live(c, ch):
    chat._changed(c, ch["id"], FC.local_members(c, ch["id"]) if chat.hfmt(ch) else None)


def _pin_answer(c, ch, r):
    if r == "full":
        return _bad(t("Höchstens {n} angeheftete Nachrichten – erst eine lösen.", n=PINS_MAX), 409)
    if r == "gone":
        return _bad("not found", 404)
    return chat._no_store({"ok": True, "pins": pins_of(c, ch["id"])})


def pin(uid, cid, mid, on):
    if not isinstance(mid, str) or not chat._HEX32.fullmatch(mid):
        return _bad("not found", 404)
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if m.get("state", "ok") != "ok":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if not pin_ok(c, ch, m["role"]):
            return _bad(t("Anheften dürfen in dieser Gruppe nur Admins."), 403)
        if chat.is_fed(ch):
            ro = FC._ro_error(c, ch)
            if ro:
                return _bad(ro, 403)
        remote_home = chat.is_fed(ch) and ch["home_peer"] is not None
        if not remote_home:
            r = _pin_set(c, ch, mid, on, uid)
            if r == "ok":
                _pins_live(c, ch)
                if chat.is_fed(ch):
                    FC.fanout(c, ch, {"type": "chat.pins", "pins": pins_of(c, ch["id"])}, cap="msgpin1")
            return _pin_answer(c, ch, r)
        p = _home_peer(c, ch)
        if not F.feature(p, "msgpin1"):
            return _bad(t("Der Server der Gruppe ({host}) ist zu alt zum Anheften.", host=FC.home_host(c, ch) or "?"), 409)
        mh = FC.my_handle(c, uid)
    status, d = FC.sync_call(p, {"op": "pin", "ref": ch["ref"], "actor": mh, "mid": mid, "on": bool(on)})
    if status is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"]), 502)
    if status == 409 and isinstance(d, dict) and d.get("error") == "full":
        return _bad(t("Höchstens {n} angeheftete Nachrichten – erst eine lösen.", n=PINS_MAX), 409)
    if status == 404:
        return _bad("not found", 404)
    if status == 403:
        return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    if status != 200 or not isinstance(d, dict):
        return _bad(t("Der andere Saga-Server hat die Änderung abgewiesen ({err}).", err=str((d or {}).get("error") or status)), 409)
    with _db() as c, c:
        ch2 = FC.by_ref(c, ch["ref"], lock=True)
        if ch2:
            _apply_pins(c, ch2, d.get("pins"))
            return chat._no_store({"ok": True, "pins": pins_of(c, ch2["id"])})
    return chat._no_store({"ok": True, "pins": []})


def _apply_pins(c, ch, lst):
    """Liste vom Heim uebernehmen (nur Kennungen von Nachrichten, die es hier gibt) -> geaendert?"""
    if not isinstance(lst, list):
        return False
    want = [x for x in lst[:PINS_MAX] if isinstance(x, str) and chat._HEX32.fullmatch(x)]
    cur = pins_of(c, ch["id"])
    rows = {r[0]: r[1] for r in c.execute(f"SELECT id, seq FROM chat_msg WHERE chat_id=? AND kind='msg' AND id IN ({','.join('?' * len(want))})",
                                          (ch["id"], *want)).fetchall()} if want else {}
    new = [x for x in want if x in rows]
    if new == cur:
        return False
    c.execute("DELETE FROM chat_pin WHERE chat_id=?", (ch["id"],))
    now = time.time()
    for i, x in enumerate(new):
        c.execute("INSERT INTO chat_pin (chat_id, msg_seq, msg_id, by_uid, at) VALUES (?,?,?,NULL,?)", (ch["id"], rows[x], x, now + i * 1e-3))
    _pins_live(c, ch)
    return True


@router.post("/api/chats/{cid}/pins/{mid}")
async def api_pin(cid: int, mid: str, request: Request):
    """Nachricht anheften (msgpin1): hoechstens 3 je Chat; freie Gruppe mit perm_info 'admins' nur Admins. -> {ok, pins}"""
    uid, err = chat._me(request)
    if err:
        return err
    return await run_in_threadpool(pin, uid, cid, mid, True)


@router.delete("/api/chats/{cid}/pins/{mid}")
async def api_unpin(cid: int, mid: str, request: Request):
    uid, err = chat._me(request)
    if err:
        return err
    return await run_in_threadpool(pin, uid, cid, mid, False)


def op_pin(c, peer, d):
    """Heim: Anheften im Auftrag einer Person eines anderen Servers."""
    if not F.feature(peer, "msgpin1"):
        return 403, {"error": "msgpin1 disabled"}
    if not FC._rate_ok("cgrp2", peer["host"], FED_GRP_RATE):
        return 429, {"error": "too many"}
    ch = FC._home_chat(c, d, lock=True)
    h, p_ = FC._actor(c, peer, d)
    mid = d.get("mid")
    if not ch or not h or not isinstance(mid, str) or not chat._HEX32.fullmatch(mid) or not isinstance(d.get("on"), bool):
        return 404, {"error": "gone"}
    mem = dict(FC.members(c, ch["id"]))
    if mem.get(p_) != "ok":
        return 403, {"error": "not a member"}
    if ch["fstate"]:
        return 409, {"error": "readonly"}
    if not pin_ok(c, ch, FC._role(c, ch["id"], p_)):
        F.log(c, "chat.forged", peer, "pin not allowed", throttle=(peer["id"], "fpin"))
        return 403, {"error": "not allowed"}
    r = _pin_set(c, ch, mid, d["on"], p_)
    if r == "full":
        return 409, {"error": "full"}
    if r == "gone":
        return 404, {"error": "unknown message"}
    if r == "ok":
        _pins_live(c, ch)
        FC.fanout(c, ch, {"type": "chat.pins", "pins": pins_of(c, ch["id"])}, cap="msgpin1")
    return 200, {"ok": True, "pins": pins_of(c, ch["id"])}


def _ev_pins(c, peer, ev):
    if not FC.peer_ok(peer) or not F.feature(peer, "msgpin1"):
        return "ignored"
    ch = FC._from_home(c, peer, ev, lock=True)
    if not ch:
        return "ignored"
    _apply_pins(c, ch, ev.get("pins"))
    return "ok"


# --- chat.members (Heim -> Teilnehmer): Rechte und Anheftungen fuer neue Kopien ---------------------------------------
def members_extra(c, ch, p, e):
    if ch["kind"] == "free" and F.feature(p, "groupinfo2"):
        e["perm_info"], e["perm_send"] = perms(c, ch["id"])
    if F.feature(p, "msgpin1"):
        e["pins"] = pins_of(c, ch["id"])


def members_apply(c, peer, ch, ev):
    if not ch:
        return
    if ch["kind"] == "free" and F.feature(peer, "groupinfo2") and (ev.get("perm_info") in PERMS or ev.get("perm_send") in PERMS):
        pi, ps = perms(c, ch["id"])
        npi, nps = (ev["perm_info"] if ev.get("perm_info") in PERMS else pi), (ev["perm_send"] if ev.get("perm_send") in PERMS else ps)
        if (npi, nps) != (pi, ps):
            c.execute("UPDATE chat SET perm_info=?, perm_send=? WHERE id=?", (npi, nps, ch["id"]))
    if F.feature(peer, "msgpin1") and "pins" in ev:
        _apply_pins(c, ch, ev.get("pins"))


# =====================================================================================================
# Blockieren (block2): auch Konten dieses Servers
# =====================================================================================================
def blocks_of(c, uid):
    return {r[0] for r in c.execute("SELECT target FROM fed_chat_block WHERE uid=?", (uid,)).fetchall()}


def blocked_by(c, uid, other):
    """Hat uid das Konto other (dieses Servers) gesperrt?"""
    r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (other,)).fetchone()
    if not r:
        return False
    return bool(c.execute("SELECT 1 FROM fed_chat_block WHERE uid=? AND target=?", (uid, FC.FK.full_handle(r[0]))).fetchone())


def blocked_pair(c, a, b):
    """Gesperrt in eine der beiden Richtungen? (Direkt-Chat beginnen, hinzufuegen, anrufen: chat.may_contact)"""
    return blocked_by(c, a, b) or blocked_by(c, b, a)


def block_target(c, uid, raw):
    """Eingabe -> (ziel fuer fed_chat_block, fehler). Lokale Konten: Such-Handle '~x' bzw. Handle '~x@<dieser Server>' ->
    '~h@<dieser Server>' (stabiler Handle); andere Server: '~x@host' bzw. 'host' (wie bisher)."""
    raw = str(raw or "").strip().lower()[:300]
    if HANDLE_RE.fullmatch(raw):
        u = resolve_ref(c, raw)
        if not u or u == uid:
            return None, _bad("not found", 404)
        return FC.my_handle(c, u), None
    n, host = FC.split_handle(raw)
    if n:
        if host == F.SELF_HOST:
            u = F.uid_by_handle(c, n)
            if not u or u == uid:
                return None, _bad("not found", 404)
            return f"{n}@{host}", None
        return f"{n}@{host}", None
    if raw and raw != F.SELF_HOST and "." in raw and "@" not in raw and "~" not in raw and len(raw) <= 260:
        return raw, None
    return None, _bad(t("ungültige Angabe"))


@router.post("/api/chat/blocks")
async def api_block(request: Request):
    """Person sperren (block2): {target} - Handle '~x' / '~x@host' (auch Konten dieses Servers) oder Server 'host'.
    Gesperrte Konten dieses Servers koennen keinen Direkt-Chat beginnen, nicht schreiben und nicht anrufen; in Gruppen
    blendet der Client ihre Nachrichten einklappbar aus (members[].blocked)."""
    uid, err = chat._me(request)
    if err:
        return err
    b = await _body(request) or {}
    with _db() as c, c:
        tg, err = block_target(c, uid, b.get("target"))
        if err:
            return err
        c.execute("INSERT INTO fed_chat_block (uid, target, created) VALUES (?,?,?) ON CONFLICT DO NOTHING", (uid, tg, time.time()))
        chat._direct(c, [uid], "chat.blocks", {"target": tg, "blocked": True})     # eigene Tabs/Geraete: Liste neu laden
    return chat._no_store({"ok": True, "target": tg})


def block_label(c, tg):
    """Eintrag der Sperrliste fuer ein Konto dieses Servers (GET /api/chat/blocks) -> dict oder None."""
    n, host = FC.split_handle(tg)
    if not n or host != F.SELF_HOST:
        return None
    u = F.uid_by_handle(c, n)
    nm = (sharing.names(c, [u]).get(u) if u else None) or t("Gelöschtes Konto")
    return {"target": tg, "kind": "person", "name": nm, "host": host, "label": nm, "local": True}


# =====================================================================================================
# Melden (report1)
# =====================================================================================================
def _sender_info(c, ch, uid, sender, sh):
    """Absender einer gemeldeten Nachricht fuer den Admin-Bereich: Handle und Name; Konten dieses Servers mit Anmeldenamen
    (nur dort sichtbar)."""
    if not sender:
        return {"handle": None, "name": t("Gelöschtes Konto")}
    if sharing.is_remote(sender):
        lab = FC.label_of(c, sender, wire=True)
        return {"handle": FC.handle_of(sender), "name": lab["name"], "host": lab["host"]}
    return {"handle": sh or FC.my_handle(c, sender), "name": sharing.names(c, [sender]).get(sender) or sender, "uid": sender}


def report(uid, b):
    if not isinstance(b, dict):
        return _bad(t("ungültige Anfrage"))
    reason, text, ids = b.get("reason"), b.get("text") or "", b.get("msgs") or []
    if reason not in REASONS or not isinstance(text, str) or not isinstance(ids, list) or len(ids) > REPORT_MSGS_MAX:
        return _bad(t("ungültige Angabe"))
    if any(not isinstance(x, str) or not chat._HEX32.fullmatch(x) for x in ids):
        return _bad(t("ungültige Angabe"))
    text = F._clean_text(text.replace("\n", " "), REPORT_TEXT_MAX) if text else ""
    now = time.time()
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, b.get("chat"), do_sync=False)
        if err:
            return err
        if c.execute("SELECT COUNT(*) FROM chat_report WHERE reporter=? AND created>?", (uid, now - 86400)).fetchone()[0] >= REPORT_DAY \
                or not chat._rate("report", uid, REPORT_RATE_H, 3600):
            return _bad(t("Zu viele Meldungen in kurzer Zeit – bitte später erneut."), 429)
        items = []
        if ids:
            rows = c.execute(f"SELECT id, ts, sender_uid, sender_handle FROM chat_msg WHERE chat_id=? AND kind='msg' AND id IN "
                             f"({','.join('?' * len(ids))})", (ch["id"], *sorted(set(ids)))).fetchall()
            if len(rows) != len(set(ids)):
                return _bad("not found", 404)
            for mid, ts, su, sh in sorted(rows, key=lambda r: r[1]):
                if su == uid:
                    return _bad(t("Eigene Nachrichten lassen sich nicht melden."), 400)
                items.append({"id": mid, "ts": ts, "sender": _sender_info(c, ch, uid, su, sh)})
        elif ch["kind"] != "dm":
            return _bad(t("Bitte mindestens eine Nachricht auswählen."), 400)
        rid = c.execute("INSERT INTO chat_report (reporter, chat_id, chat_kind, chat_ref, reason, text, items, created) VALUES (?,?,?,?,?,?,?,?) "
                        "RETURNING id", (uid, ch["id"], ch["kind"], ch.get("ref"), reason, text, json.dumps(items), now)).fetchone()[0]
        for a in [r[0] for r in c.execute("SELECT username FROM users WHERE is_admin=1 AND disabled=0").fetchall()]:
            notify.create(c, a, "chat.report", None, data={"report": rid})
        print(f"chat: Meldung {rid} ({reason}, {len(items)} Nachrichten, Chat {ch['id']})", flush=True)
    return chat._no_store({"ok": True, "id": rid})


@router.post("/api/chat/report")
async def api_report(request: Request):
    """Melden (report1): {chat, msgs:[ids], reason: spam|abuse|illegal|other, text?} - text ist der Klartext, den die
    meldende Person selbst mitschickt (freiwillig, z. B. die gemeldeten Nachrichten). Geht an die Saga-Admins."""
    uid, err = chat._me(request)
    if err:
        return err
    b = await _body(request)
    return await run_in_threadpool(report, uid, b)


def _notif(c, uid, item, d):
    item["access"] = True
    item["system"] = True
    item["title"] = t("Neue Meldung im Chat")
    item["text"] = t("Jemand hat Nachrichten in einem Chat gemeldet. Details im Admin-Bereich unter „Meldungen“.")
    item["url"] = (ADMIN_URL + "/reports") if ADMIN_URL else None


notify.RENDER["chat.report"] = _notif


# --- Admin-Bereich ---------------------------------------------------------------------------------------------------
def admin_list(state="open", limit=200):
    with _db() as c:
        rows = c.execute("SELECT id, reporter, chat_id, chat_kind, chat_ref, reason, text, items, created, state, done_by, done_at FROM chat_report "
                         + ("WHERE state=? " if state else "") + "ORDER BY created DESC LIMIT ?", ((state, limit) if state else (limit,))).fetchall()
        nm = sharing.names(c, [r[1] for r in rows if r[1]])
        out = []
        for r in rows:
            try:
                items = json.loads(r[7])
            except ValueError:
                items = []
            out.append({"id": r[0], "reporter": r[1], "reporter_name": nm.get(r[1]) if r[1] else None, "chat_id": r[2], "chat_kind": r[3],
                        "chat_ref": r[4], "reason": r[5], "text": r[6], "items": items, "created": r[8], "state": r[9], "done_by": r[10],
                        "done_at": r[11]})
        return out


def admin_counts():
    with _db() as c:
        return dict(c.execute("SELECT state, COUNT(*) FROM chat_report GROUP BY state").fetchall())


def admin_done(rid, by, state="done"):
    with _db() as c, c:
        if state == "delete":
            return c.execute("DELETE FROM chat_report WHERE id=?", (rid,)).rowcount > 0
        return c.execute("UPDATE chat_report SET state=?, done_by=?, done_at=? WHERE id=?", (state, by, time.time(), rid)).rowcount > 0


def reason_label(r):
    return {"spam": t("Spam"), "abuse": t("Belästigung oder Beleidigung"), "illegal": t("Rechtswidrige Inhalte"), "other": t("Sonstiges")}.get(r, r)


# --- Kontoloeschung ---------------------------------------------------------------------------------------------------
def account_gone(c, uid):
    c.execute("UPDATE chat_report SET reporter=NULL WHERE reporter=?", (uid,))
    c.execute("UPDATE chat_pin SET by_uid=NULL WHERE by_uid=?", (uid,))


# --- Einhaengen ---------------------------------------------------------------------------------------------------
chat.GRP = __import__("sys").modules[__name__]
FC.OPS.update({"perm": op_perm, "pin": op_pin})
F.EV2_HANDLERS["chat.perm"] = _ev_perm
F.EV2_HANDLERS["chat.pins"] = _ev_pins
FC.MEMBERS_HOOKS.append(members_extra)
FC.APPLY_MEMBERS_HOOKS.append(members_apply)
_ = auth
