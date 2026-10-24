"""Chats ueber Servergrenzen (2.3, Etappe E2): foederierte Direkt-Chats im Format saga-chat/v2 (saga/chat_v2.py).

Grundsaetze (Vertrag fuer Apps: docs/APP-API-FED.md, Abschnitt 6):
- Jeder Chat hat eine globale Kennung chat_ref ('<32 hex>@<host>') und einen Heimserver: den Server der Person, die den
  Direkt-Chat beginnt. Das Heim ist massgeblich fuer Mitglieder, Empfaengerliste, Reihenfolge (fseq), Frist, Loeschen und
  KI-Schalter. Die Server der Teilnehmer halten Kopien (Ciphertext + versiegelte Schluessel ihrer eigenen Geraete).
  Geraete sprechen nur mit dem eigenen Server. Lokale Chats bleiben v1 (chat.v = 1) und unveraendert.
- Zustellung: Geraet -> eigener Server (prueft Signatur und Empfaenger) -> Heim (synchron, harte Frist FED_EXPRESS_TIMEOUT;
  sonst Warteschlange beim Absender-Server, Kette 'chatup:<id>', bis 3 Tage) -> Heim prueft Absender (Handle gehoert zur
  anfragenden Instanz, Signatur mit dem Geraeteschluessel), Empfaenger exakt, vergibt fseq -> Fan-out an alle Teilnehmer-
  Server (Express, sonst Ausgangskorb mit Kette 'chat:<id>'; auch zurueck an den Absender-Server als Bestaetigung).
  Idempotent ueber msg_id; Luecken (fseq springt) holt der Teilnehmer beim Heim nach, Umsortieren/Wiederholen wird
  abgewiesen und protokolliert.
- Erstkontakt = Nachrichtenanfrage: beim Gegenueber erscheint der Chat als Anfrage (Annehmen/Ablehnen/Sperren), ohne
  Benachrichtigung, Push oder Klingeln. Ablehnen/Sperren erfaehrt die andere Seite nicht (das Heim stellt nur nicht mehr zu;
  schickt es trotzdem, verwirft der eigene Server; ist das Heim der Server der ablehnenden Person, weist es Nachrichten ab).
  Sperren je Person ('~x@host') oder je Instanz ('host'), Raten je Instanz und Absender, Pruefprotokoll (fed_log).
- Anhaenge bleiben beim Absender-Server; die anderen holen sie bei Bedarf (Zwischenkopie), kleine (Vorschaubilder) vorab.
- Der Lese-Zeiger (Ungelesen-Zaehler) bleibt lokal; Gelesen-Bestaetigungen (2.4) gehen nur versiegelt und nur mit der
  Faehigkeit 'receipts' ueber das Heim (saga.chat_status). Tippen geht direkt und verwerfbar, Push loest nur der eigene Server aus. KI im Chat ist in
  foederierten Chats aus, bis jemand sie einschaltet (Systemzeile fuer alle). Trennen: Chats mit Personen bzw. Heim der
  getrennten Instanz werden schreibgeschuetzt (Verlauf bleibt lesbar)."""
import base64
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from urllib import parse as urllib_parse
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import bus, chat, chat_v2 as V, fed_keys as FK, federation as F, fedsig, notify, sharing
from saga.core import MEDIA_FRONT, MEDIA_KEY, _bad, _body, is_raw_ref, resolve_input, t, unnamed
from saga.notes import FILE_MAX

router = APIRouter()

PREFETCH_MAX = 256 * 1024          # kleinere Anhaenge (Vorschaubilder) holt der Server des Empfaengers gleich vorab
# 2.4 (Videos, chatfile2): grosse Anhaenge nur bereichsweise holen. Zwischenkopie lueckenhaft in STATE_DIR/chat/fed/<id>,
# Bitmap der geholten 1-MiB-Einheiten in chat_file_fed.have. Je Abruf hoechstens PART_MAX (4 MiB), signiert je Teil.
UNIT = 1024 * 1024
PART_MAX = 4 * UNIT
RANGE_MIN = FILE_MAX + 64           # darueber: Range-Abruf statt ganzer Datei (Gegenstelle mit chatfile2)
BIG_MAX = 8 << 30                   # hoechstens so grosse Anhaenge anderer Server nimmt dieser Server an (Ankuendigung)
SERVE_MAX = 8 * UNIT                # Python-Ersatzweg (ohne saga-media): je Antwort hoechstens so viel holen und liefern
# Zwischenkopien fremder Anhaenge: Speicher je Instanz begrenzt (zuletzt benutzte bleiben), unbenutzte verfallen - danach
# holt der Server sie bei Bedarf erneut beim Absender-Server (nichts geht verloren, solange die Nachricht dort besteht)
CACHE_PEER_MAX = max(2 * (FILE_MAX + 64), int(F.E.get("FED_CHAT_CACHE_MB", "1024")) * 1024 * 1024)
CACHE_DAYS = max(1, int(F.E.get("FED_CHAT_CACHE_DAYS", "30")))
REQ_RATE_H, REQ_RATE_PEER_H = 20, 200      # Nachrichtenanfragen je Absender bzw. je Instanz und Stunde
MSG_RATE_PEER = 600                # Nachrichten je Instanz und Minute (je Absender: chat.MSG_RATE)
TYPING_RATE = 120                  # Tipp-Meldungen je Instanz und Minute
MSG_IN_RATE = max(60, int(F.E.get("FED_CHAT_IN_RATE", "1200")))    # Nachrichten vom Heim je Instanz und Minute (sonst 'retry')
MEMBERS_RATE = 300                 # Mitgliederlisten vom Heim (chat.members) je Instanz und Minute (sonst 'retry')
RESOLVE_RATE = 60                  # Auftraege 'resolve' (Namen/Gruppen aufloesen) je Instanz und Minute (sonst 429)
LATE = 4 * 86400                   # zugestellte Nachrichten (Warteschlange) duerfen so alt sein
GAP_PAGE = 100
STATES = ("ok", "request", "declined")
_REF = re.compile(r"([0-9a-f]{32})@([a-z0-9.:-]{1,260})")
_H = re.compile(r"(~[a-z2-7]{16})@([a-z0-9.:-]{1,260})")
_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


# =====================================================================================================
# Kleinkram
# =====================================================================================================
def on():
    return F.enabled() and chat.enabled()


def principal(h):
    return "@" + h


def handle_of(u):
    """Mitglied (lokaler Benutzername oder '@~x@host') -> Handle '~x@host' (lokal: eigener Handle) oder None."""
    if sharing.is_remote(u):
        return u[1:] if _H.fullmatch(u[1:]) else None
    return None


def my_handle(c, uid):
    return FK.full_handle(F.own_handle(c, uid))


def split_ref(ref):
    m = _REF.fullmatch(ref or "") if isinstance(ref, str) else None
    return (m.group(1), fedsig.authority(m.group(2))) if m else (None, None)


def split_handle(h):
    m = _H.fullmatch(h or "") if isinstance(h, str) else None
    return (m.group(1), fedsig.authority(m.group(2))) if m else (None, None)


def chain_down(ref):
    return "chat:" + ref.split("@")[0]


def chain_up(ref):
    return "chatup:" + ref.split("@")[0]


def peer_ok(p):
    """Chats mit Instanz p erlaubt? (beide Seiten melden 'chat2', Admin-Schalter "Chat" an, "nur gemeinsame Gruppen" nur
    mit Ausnahme je Instanz)"""
    return F.feature(p, "chat2") and (not sharing.group_scope() or bool(p["opts"].get("scope_exception")))


def by_ref(c, ref, lock=False):
    return chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE ref=?" + (" FOR UPDATE" if lock else ""), (ref,)).fetchone())


def members(c, cid):
    """[(mitglied, state)] - lokale Benutzernamen und fremde Prinzipale '@~x@host'."""
    return [tuple(r) for r in c.execute("SELECT uid, state FROM chat_member WHERE chat_id=? AND left_at IS NULL ORDER BY joined, uid",
                                        (cid,)).fetchall()]


def member_handle(c, u):
    return handle_of(u) if sharing.is_remote(u) else my_handle(c, u)


def home_host(c, ch):
    if ch["home_peer"] is None:
        return F.SELF_HOST
    p = F.peer_by(c, "id", ch["home_peer"])
    return p["host"] if p else None


def _person(c, peer, h, name=None):
    """Anzeigename einer fremden Person (Adressbuch, sonst mitgeschickter Name) zwischenspeichern."""
    n, host = split_handle(h)
    r = c.execute("SELECT name FROM fed_directory WHERE peer_id=? AND handle=?", (peer["id"], n)).fetchone()
    F._person_upsert(c, principal(h), peer["id"], (r[0] if r else "") or name or n)


def label_of(c, u, wire=False):
    """Anzeige einer fremden Person im Chat: {name, host, label} ("Anna Berg · b.example"). Ohne Namen (D01): lesbarer
    Ersatz "Person auf b.example (spqz)" (label ohne Herkunft, die steht schon drin). wire=True: fuer andere Server und
    gespeicherte Systemzeilen - dann wie bisher der rohe Handle statt des (uebersetzten) Ersatzes."""
    h = handle_of(u) or ""
    n, host = split_handle(h)
    r = c.execute("SELECT d.name FROM fed_directory d JOIN fed_peer p ON p.id=d.peer_id WHERE p.host=? AND d.handle=?", (host, n)).fetchone()
    name = (r[0] if r else "") or ""
    if not name:
        r = c.execute("SELECT display FROM fed_person WHERE principal=?", (u,)).fetchone()
        name = (r[0] if r else "") or ""
    if name and is_raw_ref(name):
        name = ""
    if not name:
        if wire or not n:
            name = n or "?"
        else:
            name = unnamed(n, host)
            return {"name": name, "host": host, "label": name}
    return {"name": name, "host": host, "label": f"{name} · {host}"}


def blocked(c, uid, h):
    n, host = split_handle(h)
    return bool(c.execute("SELECT 1 FROM fed_chat_block WHERE uid=? AND target IN (?,?)", (uid, h, host)).fetchone())


def refused(c, mem, sender, h):
    """Wollen die Konten DIESES Servers im Chat (ausser dem Absender) von h nichts mehr? (alle abgelehnt oder h bzw. seine
    Instanz gesperrt) - dann wird nichts gespeichert, vorab geholt oder gemeldet. mem: {mitglied: state}."""
    loc = [(u, st) for u, st in mem.items() if u != sender and not sharing.is_remote(u)]
    return bool(loc) and all(st == "declined" or blocked(c, u, h) for u, st in loc)


def _rate_ok(kind, key, n, window=60.0):
    return F._rate.allow(f"{kind}:{key}", n, window)


def _log_retry(c, peer, event, detail, throttle):
    """Pruefprotokoll fuer ein Ereignis, das mit 'retry' endet (dessen Sicherungspunkt wird zurueckgerollt): nach dem Commit
    in eigener Transaktion."""
    def go():
        with _db() as c2, c2:
            F.log(c2, event, peer, detail, throttle=throttle)
    c.after_commit(go)


def _volatile(peer_id, body, path="/fed/v2/chat", drop=None):
    """Ohne Ausgangskorb (Tippen, Lebenszeichen): sofort im Express-Pool, Fehler egal."""
    def go():
        with _db() as c:
            p = F.peer_by(c, "id", peer_id)
        if not peer_ok(p):
            return
        try:
            F.call_json(p, "POST", path, body=body, timeout=max(1.0, F.EXPRESS_TIMEOUT / 3))
        except F.FedError:
            pass
    # je Instanz begrenzt; ausgelastet: Tippen faellt weg, alles andere (Anruf-Ende, Lebenszeichen) wartet kurz
    F.peer_submit(peer_id, go, drop=body.get("op") == "typing" if drop is None else drop)


def sync_call(p, body, path="/fed/v2/chat"):
    """Synchron an eine Instanz (harte Frist FED_EXPRESS_TIMEOUT). -> (status, antwort) oder (None, fehlertext)."""
    try:
        return F.call_json(p, "POST", path, body=body, timeout=max(1.0, F.EXPRESS_TIMEOUT / 3))
    except F.FedError as ex:
        with _db() as c, c:
            F._touch(c, p, ex)
        return None, str(ex)


# =====================================================================================================
# Schluessel und Empfaenger
# =====================================================================================================
def _cached(c, h):
    r = c.execute("SELECT data FROM fed_chat_keys WHERE handle=?", (h,)).fetchone()
    try:
        return json.loads(r[0]) if r else None
    except ValueError:
        return None


def _cache_entry(c, peer, h, e):
    """Von der Instanz der Person mitgeschickter (selbst geprueften) Eintrag -> Zwischenspeicher."""
    c.execute("INSERT INTO fed_chat_keys (handle, peer_id, data, fetched, stale) VALUES (?,?,?,?,0) ON CONFLICT (handle) DO UPDATE "
              "SET peer_id=EXCLUDED.peer_id, data=EXCLUDED.data, fetched=EXCLUDED.fetched, stale=0", (h, peer["id"], json.dumps(e), time.time()))


def local_entry(c, h):
    n, host = split_handle(h)
    if host != F.SELF_HOST:
        return None
    return FK.keys_local(c, [n]).get(n)


def entries(handles, fresh=False, uid=None):
    """{handle: eintrag|None}: eigene Konten direkt, fremde ueber den Heimserver der Person (Zwischenspeicher, Netzwerk -
    nie in einer Transaktion aufrufen). uid: Personen von Servern, mit denen wir nicht verbunden sind, ueber das Heim eines
    gemeinsamen Chats (E5)."""
    out, by = {}, {}
    for h in handles:
        n, host = split_handle(h)
        if n:
            by.setdefault(host, []).append(h)
    for host, hs in by.items():
        if host == F.SELF_HOST:
            with _db() as c:
                out.update({h: local_entry(c, h) for h in hs})
            continue
        with _db() as c:
            p = F.peer_by(c, "host", host)
        if not FK._peer_ok(p):
            out.update(keys_via_home(uid, hs, fresh) if uid else {h: None for h in hs})
            continue
        r = FK.fetch_remote(p, hs, force=fresh)
        out.update({h: r.get(h) for h in hs})
    return out


def want_of(ents):
    """Empfaengerkennungen: je Person 'backup:<handle>', je aktivem Geraet 'dev:<id>@<host>'."""
    out = set()
    for h, e in ents.items():
        if not e:
            continue
        host = h.rpartition("@")[2]
        out.add("backup:" + h)
        out.update(f"dev:{d['id']}@{host}" for d in e.get("devices") or [] if not d.get("revoked"))
    return out


def _keys_ok(keys):
    return isinstance(keys, dict) and 0 < len(keys) <= chat.MEMBERS_MAX * (chat.DEVICES_MAX + 1) and all(
        isinstance(k, str) and len(k) < 300 and isinstance(v, str) and chat.b64d(v, chat.SEAL_LEN) is not None for k, v in keys.items())


def keys_for_host(keys, host):
    return {r: v for r, v in keys.items() if r.rpartition("@")[2] == host}


def local_keys(c, ch, keys):
    """Versiegelte Schluessel fuer Geraete/Konten DIESES Servers -> [(empfaenger, sealed)] in der lokalen Form (Geraete-ID bzw.
    'backup:<benutzer>') - so arbeiten Abruf, "fuer mich loeschen" und Kontoloeschung wie bei lokalen Chats."""
    out = []
    mem = {u for u, _ in members(c, ch["id"]) if not sharing.is_remote(u)}
    for r, v in keys.items():
        if r.startswith("dev:"):
            did, _, host = r[4:].partition("@")
            if host != F.SELF_HOST or not _HEX32.fullmatch(did):
                continue
            row = c.execute("SELECT uid FROM chat_device WHERE id=?", (did,)).fetchone()
            if row and row[0] in mem:
                out.append((did, v))
        elif r.startswith("backup:"):
            n, host = split_handle(r[7:])
            if host != F.SELF_HOST:
                continue
            uid = F.uid_by_handle(c, n)
            if uid in mem:
                out.append(("backup:" + uid, v))
    return out


def sig_ok(e, ref, m, revoked_ok=False):
    if not e:
        return False
    d = next((x for x in e.get("devices") or [] if x.get("id") == m["sender_device"]), None)
    return bool(d) and (revoked_ok or not d.get("revoked")) and V.verify_message(d["sign_pub"], ref, m)


def msg_in(d):
    """Nachricht (vom Geraet oder einer anderen Instanz) pruefen -> dict oder None (Form, nicht Signatur)."""
    if not isinstance(d, dict):
        return None
    mid, dev, body_ct, sig = d.get("id"), d.get("sender_device"), d.get("body_ct"), d.get("sig")
    reply, ai = d.get("reply_to") or None, d.get("ai") or None
    try:
        ts = int(d.get("ts"))
    except (TypeError, ValueError, OverflowError):
        return None
    kind = d.get("kind") or "msg"
    if not (isinstance(mid, str) and _HEX32.fullmatch(mid) and isinstance(dev, str) and _HEX32.fullmatch(dev)) or kind not in ("msg", "title"):
        return None
    if not isinstance(body_ct, str) or len(body_ct) > chat.MSG_MAX:
        return None
    raw = chat.b64d(body_ct)
    if raw is None or len(raw) < 40 or chat.b64d(sig, 64) is None or not 0 < ts < 2 ** 53:
        return None
    if reply is not None and not (isinstance(reply, str) and _HEX32.fullmatch(reply)):
        return None
    if ai is not None and not (isinstance(ai, str) and _HEX64.fullmatch(ai)):
        return None
    h = d.get("sender_handle")
    out = {"id": mid, "ts": ts, "sender_handle": h if isinstance(h, str) else None, "sender_device": dev, "body_ct": body_ct, "sig": sig,
           "reply_to": reply, "ai": ai}
    if kind == "title":                  # Name einer freien Gruppe (E5) - wie in v1 eine verschluesselte Nachricht
        out["kind"] = "title"
    return out


def files_in(v, peer=None):
    """[{id, size}] einer anderen Instanz pruefen -> Liste oder None. 2.4: mit chatfile2 (Range-Abruf) auch grosse
    Anhaenge (saga-file/v2, Videos) bis BIG_MAX - Zwischenkopien bleiben ueber FED_CHAT_CACHE_MB begrenzt."""
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > chat.FILE_IDS_PER_MSG:
        return None
    mx = BIG_MAX if peer is not None and F.feature(peer, "chatfile2") else FILE_MAX + 64
    out = []
    for f in v:
        if not isinstance(f, dict) or not isinstance(f.get("id"), str) or not chat._FID.fullmatch(f["id"]):
            return None
        s = f.get("size")
        if not isinstance(s, int) or isinstance(s, bool) or not 0 < s <= mx:
            return None
        out.append({"id": f["id"], "size": s})
    return out


def files_for(p, files):
    """Anhaenge fuer Instanz p: ohne chatfile2 (Saga 2.3) nur bis FILE_MAX - groessere koennte sie nicht annehmen (die
    ganze Nachricht ginge sonst verloren); ihre Apps zeigen ohnehin "Mit Saga 2.4 oeffnen"."""
    if not files or F.feature(p, "chatfile2"):
        return files
    return [f for f in files if f["size"] <= FILE_MAX + 64]


def big_files_ok(c, ch):
    """Koennen alle beteiligten Server (Heim und Server aller Mitglieder) grosse Anhaenge (chatfile2)?"""
    hosts = _hosts_of([u for u, _ in members(c, ch["id"])])
    if ch["home_peer"] is not None:
        hp = F.peer_by(c, "id", ch["home_peer"])
        if not hp or not F.feature(hp, "chatfile2"):
            return False
    for host in hosts:
        if host == F.SELF_HOST:
            continue
        p = F.peer_by(c, "host", host)
        if not p or not F.feature(p, "chatfile2"):
            return False
    return True


# =====================================================================================================
# Speichern und lokal zustellen
# =====================================================================================================
def store(c, ch, m, sender, lkeys, files=(), origin=None, fseq=None, fstate=None, all_keys=None):
    """Nachricht speichern (in der Transaktion). sender: lokaler Benutzername oder '@~x@host'. files: [{id, size}] - mit
    origin (peer) liegen sie bei einer anderen Instanz (Abruf bei Bedarf), sonst sind es eigene Uploads. all_keys (Heim):
    zusaetzlich alle Schluessel der anderen Server (fuer das Nachholen von Luecken). -> seq"""
    now = time.time()
    eff, mx = ch["retention_s"], chat.retention_max()
    eff = min(eff, mx) if eff and mx else (eff or mx)
    kind = m.get("kind") or "msg"
    seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size, "
                    "fseq, sender_handle, ai, fstate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING seq",
                    (m["id"], ch["id"], kind, sender, m["sender_device"], m["ts"], now, None if kind == "title" else (now + eff if eff else None),
                     m["body_ct"], m["sig"], m["reply_to"], len(m["body_ct"]), fseq, m["sender_handle"], m["ai"], fstate)).fetchone()[0]
    rows = dict(lkeys)
    if all_keys:
        rows.update({k: v for k, v in all_keys.items() if not k.endswith("@" + F.SELF_HOST)})
    c.executemany("INSERT INTO chat_msg_key (msg_seq, recipient, sealed) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                  [(seq, k, v) for k, v in sorted(rows.items())])
    ids = [f["id"] for f in files]
    if ids and origin is None:
        c.execute(f"UPDATE chat_file SET msg_seq=? WHERE chat_id=? AND id IN ({','.join('?' * len(ids))})", (seq, ch["id"], *ids))
    elif ids:
        for f in files:
            if c.execute("INSERT INTO chat_file (id, chat_id, uid, size, created, msg_seq) VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                         (f["id"], ch["id"], sender, f["size"], now, seq)).rowcount:
                c.execute("INSERT INTO chat_file_fed (id, peer_id, cached) VALUES (?,?,0) ON CONFLICT DO NOTHING", (f["id"], origin["id"]))
    if kind == "title":                  # neuer Name der Gruppe: alter Titel samt Anhaengen weg (wie v1)
        old = c.execute("SELECT title_seq FROM chat WHERE id=?", (ch["id"],)).fetchone()[0]
        c.execute("UPDATE chat SET title_seq=? WHERE id=?", (seq, ch["id"]))
        if old and old != seq:
            gone = [r[0] for r in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (old,))]
            c.execute("DELETE FROM chat_file WHERE msg_seq=?", (old,))
            c.execute("DELETE FROM chat_msg WHERE seq=?", (old,))
            chat._rm_blobs(c, gone)
        return seq
    c.execute("UPDATE chat SET last_seq=GREATEST(last_seq, ?), last_at=? WHERE id=?", (seq, now, ch["id"]))
    return seq


def deliver(c, ch, seq, mid, sender, kind="msg"):
    """Neue Nachricht fuer die Konten DIESES Servers: Archiv, Benachrichtigung und Push nur in angenommenen Chats (Anfragen
    melden sich still), Live-Ereignis an alle lokalen Mitglieder."""
    if kind == "title":                  # Gruppenname geaendert: nur neu laden
        chat._changed(c, ch["id"], local_members(c, ch["id"]))
        return
    chat.revive(c, ch["id"], sender)
    rows = c.execute("SELECT uid, muted, state FROM chat_member WHERE chat_id=? AND left_at IS NULL", (ch["id"],)).fetchall()
    loc = [r for r in rows if not sharing.is_remote(r[0])]
    if not sharing.is_remote(sender):
        c.execute("UPDATE chat_member SET last_read=GREATEST(last_read, ?) WHERE chat_id=? AND uid=?", (seq, ch["id"], sender))
    for u, muted, st in loc:
        if u == sender or muted or st != "ok" or kind != "msg":
            continue
        notify.create(c, u, "chat.message", sender, data={"chat": ch["id"]}, bundle=ch["id"])
        bus.fire("push.send", c, uid=u, kind="chat.message", actor=sender, data={"chat": ch["id"]})
    chat.direct_person(c, ch, [r[0] for r in loc], "chat.message", {"chat_id": ch["id"], "seq": seq, "id": mid, "sender": sender,
                                                                   **({"kind": kind} if kind != "msg" else {})}, sender, "sender")


def local_members(c, cid):
    return [u for u, _ in members(c, cid) if not sharing.is_remote(u)]


def participants(c, ch, declined=False):
    """Fremde Instanzen mit Mitgliedern in diesem Chat (Heim: Empfaenger des Fan-outs)."""
    hosts = {split_handle(handle_of(u))[1] for u, st in members(c, ch["id"]) if sharing.is_remote(u) and (declined or st != "declined")}
    out = []
    for host in sorted(h for h in hosts if h):
        p = F.peer_by(c, "host", host)
        if p and p["status"] == "active":
            out.append(p)
    return out


def fanout(c, ch, ev, keys=None, declined=True, skip=None):
    """Heim: Ereignis an die Teilnehmer-Server (Kette je Chat, Express). keys: je Server nur die eigenen Empfaenger."""
    for p in participants(c, ch, declined):
        if skip is not None and p["id"] == skip:
            continue
        if not F.feature(p, "chat2"):
            continue
        e = dict(ev, ref=ch["ref"])
        if keys is not None:
            e["keys"] = keys_for_host(keys, p["host"])
        if e.get("files"):
            e["files"] = files_for(p, e["files"])
        F._enqueue(c, p["id"], e, chain=chain_down(ch["ref"]))


def sysline(c, ch, by, data):
    """Systemzeile im Chat (z. B. "KI eingeschaltet von ...") - Klartext-Metadaten wie Anruf-Eintraege (kind 'sys')."""
    now = time.time()
    body = json.dumps(dict(data, v=1), separators=(",", ":"))
    mid = secrets.token_hex(16)
    seq = c.execute("INSERT INTO chat_msg (id, chat_id, kind, sender_uid, sender_device, ts, created, expires_at, body_ct, sig, reply_to, size) "
                    "VALUES (?,?,'sys',?,'',?,?,NULL,?,'',NULL,0) RETURNING seq", (mid, ch["id"], by, int(now * 1000), now, body)).fetchone()[0]
    c.execute("UPDATE chat SET last_seq=GREATEST(last_seq, ?), last_at=? WHERE id=?", (seq, now, ch["id"]))
    chat._direct(c, local_members(c, ch["id"]), "chat.message", {"chat_id": ch["id"], "seq": seq, "id": mid, "sender": by, "kind": "sys"})
    return seq


# =====================================================================================================
# Server-zu-Server: Auftraege (synchron POST /fed/v2/chat, sonst als Ereignis 'chat.op' aus der Warteschlange)
# =====================================================================================================
def _actor(c, peer, d):
    """Handelnde Person einer Anfrage: Handle der anfragenden Instanz -> (handle, prinzipal) oder (None, None)."""
    h = d.get("actor")
    n, host = split_handle(h)
    if not n or host != peer["host"]:
        return None, None
    return h, principal(h)


def _home_chat(c, d, lock=False):
    """Chat, dessen Heim WIR sind."""
    ch = by_ref(c, d.get("ref"), lock) if isinstance(d.get("ref"), str) else None
    return ch if ch and ch["home_peer"] is None and ch["v"] == 2 else None


def op_request(c, peer, d):
    """Heim der anfragenden Person -> wir: Direkt-Chat mit einem unserer Konten (erscheint dort als Nachrichtenanfrage)."""
    ref = d.get("ref")
    hx, rhost = split_ref(ref)
    fr = d.get("from") if isinstance(d.get("from"), dict) else {}
    fh = fr.get("handle")
    fn, fhost = split_handle(fh)
    if not hx or rhost != peer["host"] or d.get("kind") != "dm":
        return 400, {"error": "bad request"}
    if not fn or fhost != peer["host"]:
        F.log(c, "chat.forged", peer, "request from")
        return 403, {"error": "forged"}
    tn, thost = split_handle(d.get("to"))
    uid = F.uid_by_handle(c, tn) if thost == F.SELF_HOST else None
    if not uid:
        return 404, {"error": "unknown person"}
    if not _rate_ok("creq", fh, REQ_RATE_H, 3600) or not _rate_ok("creqp", peer["host"], REQ_RATE_PEER_H, 3600):
        F.log(c, "chat.rate", peer, "request", throttle=(peer["id"], "creq"))
        return 429, {"error": "too many requests"}
    p_ = principal(fh)
    if blocked(c, uid, fh):                      # gesperrt: die andere Seite erfaehrt es nicht
        F.log(c, "chat.blocked", peer, "request", throttle=(peer["id"], "cblk", fh))
        return 200, {"ok": True}
    key = "|".join(sorted([uid, p_]))
    other = by_ref(c, ref)
    if other and other["dm_key"] != key:
        F.log(c, "chat.forged", peer, "request ref reused")
        return 409, {"error": "ref in use"}
    ex = chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE dm_key=?", (key,)).fetchone())
    if ex and ex["ref"] == ref:
        return 200, {"ok": True}
    if ex and ex["home_peer"] is None and not ex["fstate"] and ex["v"] == 2:
        # Es gibt den Chat schon (bei uns zu Hause, z. B. hat unser Konto ihn begonnen): die anfragende Seite nimmt ihn
        c.execute("UPDATE chat_member SET state='ok', left_at=NULL WHERE chat_id=? AND uid=?", (ex["id"], p_))
        return 200, {"existing": ex["ref"]}
    if ex:                                       # alter Chat (dort verloren bzw. schreibgeschuetzt): bleibt lesbar, aber ohne Paar
        c.execute("UPDATE chat SET dm_key=NULL, fstate=CASE WHEN fstate='' THEN 'old' ELSE fstate END WHERE id=?", (ex["id"],))
        chat._changed(c, ex["id"], local_members(c, ex["id"]))
    now = time.time()
    _person(c, peer, fh, F.remote_name(fr.get("name")))
    cid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created, v, ref, home_peer) VALUES ('dm',?,NULL,?,2,?,?)",
                    (key, now, ref, peer["id"])).lastrowid
    c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'request')", (cid, uid, now))
    c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'ok')", (cid, p_, now))
    chat._direct(c, [uid], "chat.request", {"chat_id": cid})
    F.log(c, "chat.request", peer, f"chat={cid}")
    return 200, {"ok": True}


def op_state(c, peer, d, state):
    """Teilnehmer nimmt die Anfrage an bzw. lehnt ab (das Heim stellt dann nicht mehr zu)."""
    ch = _home_chat(c, d)
    h, p_ = _actor(c, peer, d)
    if not ch or not h:
        return 404, {"error": "gone"}
    if not c.execute("UPDATE chat_member SET state=? WHERE chat_id=? AND uid=? AND left_at IS NULL", (state, ch["id"], p_)).rowcount:
        return 404, {"error": "not a member"}
    return 200, {"ok": True, "fseq": ch["fseq"]}


def op_send(c, peer, d):
    """Teilnehmer -> Heim: Nachricht. Prueft Absender (Handle der anfragenden Instanz), Signatur, Empfaenger exakt; vergibt fseq."""
    ch = _home_chat(c, d, lock=True)
    if not ch:
        return 404, {"error": "gone"}
    if ch["fstate"]:
        return 409, {"error": "readonly"}
    m = msg_in(d.get("msg"))
    files = files_in(d.get("files"), peer)
    keys = d.get("keys")
    if not m or files is None or not _keys_ok(keys) or (m.get("kind") == "title" and ch["kind"] != "free"):
        return 400, {"error": "bad message"}
    h = m["sender_handle"]
    n, host = split_handle(h)
    if not n or host != peer["host"]:
        F.log(c, "chat.forged", peer, f"sender {str(h)[:40]}", throttle=(peer["id"], "forged"))
        return 403, {"error": "forged sender"}
    p_ = principal(h)
    if ch["kind"] == "page":                     # Seiten-Chat: Freigaben jetzt pruefen (Entzug wirkt sofort)
        chat.sync(c, ch)
    mem = dict(members(c, ch["id"]))
    if p_ not in mem:
        return 403, {"error": "not a member"}
    if refused(c, mem, p_, h):                   # abgelehnt/gesperrt: verwerfen, der Absender-Server markiert "nicht zugestellt"
        F.log(c, "chat.declined", peer, m["id"][:8], throttle=(peer["id"], "cdecl", ch["ref"]))
        return 403, {"error": "declined"}
    old = c.execute("SELECT chat_id, sig, fseq FROM chat_msg WHERE id=?", (m["id"],)).fetchone()
    if old:
        if old[0] == ch["id"] and old[1] == m["sig"]:
            return 200, {"ok": True, "fseq": old[2], "dup": True}
        F.log(c, "chat.replay", peer, m["id"][:8], throttle=(peer["id"], "replay"))
        return 409, {"error": "duplicate id"}
    if not _rate_ok("cmsg", h, chat.MSG_RATE) or not _rate_ok("cmsgp", peer["host"], MSG_RATE_PEER):
        F.log(c, "chat.rate", peer, "send", throttle=(peer["id"], "cmsg"))
        return 429, {"error": "too many messages"}
    now = time.time()
    if not now - LATE < m["ts"] / 1000 < now + chat.SKEW:
        return 400, {"error": "bad time"}
    se = FK.check_entry(h, d.get("sender_keys"))
    if not se:
        return 400, {"error": "bad keys"}
    if not sig_ok(se, ch["ref"], m):
        F.log(c, "chat.badsig", peer, m["id"][:8], throttle=(peer["id"], "badsig"))
        return 403, {"error": "bad signature"}
    _cache_entry(c, peer, h, se)
    if m["ai"] and not c.execute("SELECT 1 FROM chat_ai WHERE chat_id=?", (ch["id"],)).fetchone():
        return 403, {"error": "ai not allowed"}
    ents = {}
    for u in mem:
        hh = member_handle(c, u)
        ents[hh] = se if hh == h else (local_entry(c, hh) if not sharing.is_remote(u) else _cached(c, hh))
    if set(keys) != want_of(ents):
        return 409, {"error": "recipients", "handles": sorted(ents)}
    if mem[p_] != "ok":                          # wer schreibt, hat angenommen
        c.execute("UPDATE chat_member SET state='ok' WHERE chat_id=? AND uid=?", (ch["id"], p_))
    fseq = c.execute("UPDATE chat SET fseq=fseq+1 WHERE id=? RETURNING fseq", (ch["id"],)).fetchone()[0]
    seq = store(c, ch, m, p_, local_keys(c, ch, keys), files, origin=peer, fseq=fseq, all_keys=keys)
    deliver(c, ch, seq, m["id"], p_, m.get("kind") or "msg")
    fanout(c, ch, {"type": "chat.msg", "fseq": fseq, "msg": m, "files": files, "sender_keys": se}, keys=keys, declined=False)
    _prefetch_after(c, files, peer)
    return 200, {"ok": True, "fseq": fseq}


def drop_msg(c, ch, mid):
    """Nachricht samt Schluesseln, Anhaengen und KI-Beleg loeschen (in der Transaktion). -> seq oder None."""
    r = c.execute("SELECT seq FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg'", (mid, ch["id"])).fetchone()
    if not r:
        return None
    fids = [x[0] for x in c.execute("SELECT id FROM chat_file WHERE msg_seq=?", (r[0],))]
    c.execute("DELETE FROM chat_file WHERE msg_seq=?", (r[0],))
    c.execute("DELETE FROM chat_ai_reply WHERE msg_id=?", (mid,))
    c.execute("DELETE FROM chat_msg WHERE seq=?", (r[0],))
    for fn in DROP_HOOKS:                        # 2.5 (saga.chat_x3): Bearbeitungen gehen mit
        fn(c, ch, mid)
    chat._rm_blobs(c, fids)
    chat._direct(c, local_members(c, ch["id"]), "chat.deleted", {"chat_id": ch["id"], "id": mid, "seq": r[0]})
    return r[0]


def op_del(c, peer, d):
    """Eigene Nachricht fuer alle loeschen (nur, wer sie geschickt hat)."""
    ch = _home_chat(c, d, lock=True)
    h, p_ = _actor(c, peer, d)
    mid = d.get("id")
    if not ch or not h or not isinstance(mid, str) or not _HEX32.fullmatch(mid):
        return 404, {"error": "gone"}
    r = c.execute("SELECT sender_handle FROM chat_msg WHERE id=? AND chat_id=?", (mid, ch["id"])).fetchone()
    if r and r[0] != h:
        F.log(c, "chat.forged", peer, "delete", throttle=(peer["id"], "fdel"))
        return 403, {"error": "not yours"}
    drop_msg(c, ch, mid)
    fanout(c, ch, {"type": "chat.msg.del", "mid": mid})
    return 200, {"ok": True}


def drop_chat(c, ch):
    chat._rm_blobs(c, chat._drop_chat(c, ch["id"]))


def removed_live(c, ch, by):
    chat._direct(c, local_members(c, ch["id"]), "chat.removed", {"chat_id": ch["id"], "kind": ch["kind"], "by": by})


def op_drop(c, peer, d):
    """Direkt-Chat "fuer alle loeschen" bzw. Konto geloescht: das Heim loescht und meldet es allen Teilnehmer-Servern."""
    ch = _home_chat(c, d, lock=True)
    h, p_ = _actor(c, peer, d)
    if not ch or not h:
        return 200, {"ok": True}
    if p_ not in dict(members(c, ch["id"])):
        return 403, {"error": "not a member"}
    if ch["kind"] != "dm" and (ch["kind"] != "free" or _role(c, ch["id"], p_) != "admin"):
        return 403, {"error": "not admin"}
    lab = label_of(c, p_)
    by = {"username": p_, "handle": h, "name": lab["label"]}
    fanout(c, ch, {"type": "chat.removed", "by": {"handle": h, "name": label_of(c, p_, wire=True)["name"]}}, skip=peer["id"])
    chat._drop_notifs(c, local_members(c, ch["id"]), ch["id"])
    removed_live(c, ch, by)
    drop_chat(c, ch)
    F.log(c, "chat.removed", peer, f"chat={ch['id']}")
    return 200, {"ok": True}


def apply_meta(c, ch, d, by):
    """Frist und KI-Schalter uebernehmen (Heim und Teilnehmer). by: lokaler Benutzername oder Prinzipal. -> geaendert?"""
    changed = False
    if "retention_s" in d:
        v = d["retention_s"]
        if isinstance(v, int) and not isinstance(v, bool) and v in chat.RETENTIONS and v != ch["retention_s"]:
            c.execute("UPDATE chat SET retention_s=? WHERE id=?", (v, ch["id"]))
            changed = True
    if isinstance(d.get("ai"), bool):
        had = bool(c.execute("SELECT 1 FROM chat_ai WHERE chat_id=?", (ch["id"],)).fetchone())
        if d["ai"] and not had:
            c.execute("INSERT INTO chat_ai (chat_id, by_uid, at) VALUES (?,?,?) ON CONFLICT (chat_id) DO NOTHING", (ch["id"], by, time.time()))
        elif not d["ai"] and had:
            c.execute("DELETE FROM chat_ai WHERE chat_id=?", (ch["id"],))
        if d["ai"] != had:
            nm = label_of(c, by, wire=True)["name"] if sharing.is_remote(by) else (F.display_of(c, by) or member_handle(c, by).split("@")[0])
            sysline(c, ch, by, {"type": "ai", "on": d["ai"], "by": member_handle(c, by), "name": nm})
            changed = True
    if changed:
        chat._changed(c, ch["id"], local_members(c, ch["id"]))
    return changed


def op_meta(c, peer, d):
    ch = _home_chat(c, d, lock=True)
    h, p_ = _actor(c, peer, d)
    if not ch or not h:
        return 404, {"error": "gone"}
    if p_ not in dict(members(c, ch["id"])):
        return 403, {"error": "not a member"}
    if ch["fstate"]:
        return 409, {"error": "readonly"}
    want = {k: d[k] for k in ("retention_s", "ai") if k in d}
    if apply_meta(c, ch, want, p_):
        fanout(c, ch, dict({"type": "chat.meta", "by": h}, **want))
    return 200, {"ok": True}


def op_typing(c, peer, d):
    """Tippen (verwerfbar): an die lokalen Mitglieder, beim Heim auch an die anderen Teilnehmer (Gruppen, E5)."""
    ch = by_ref(c, d.get("ref")) if isinstance(d.get("ref"), str) else None
    h, p_ = _actor(c, peer, d)
    if not ch or not h or not _rate_ok("ctyp", peer["host"], TYPING_RATE):
        return 200, {"ok": True}
    mem = dict(members(c, ch["id"]))
    if p_ not in mem or mem[p_] != "ok":
        return 200, {"ok": True}
    lab = label_of(c, p_)
    loc = [u for u, st in mem.items() if not sharing.is_remote(u) and st == "ok"]
    chat._direct(c, loc, "chat.typing", {"chat_id": ch["id"], "user": {"username": p_, "handle": h, "name": lab["name"]}})
    if ch["home_peer"] is None and ch["kind"] in ("free", "page"):       # Heim: an die anderen Teilnehmer-Server (verwerfbar)
        for p in participants(c, ch):
            if p["id"] != peer["id"]:
                _volatile(p["id"], {"op": "typing", "ref": ch["ref"], "actor": h})
    return 200, {"ok": True}


OPS = {"request": op_request, "accept": lambda c, p, d: op_state(c, p, d, "ok"), "decline": lambda c, p, d: op_state(c, p, d, "declined"),
       "send": op_send, "del": op_del, "drop": op_drop, "meta": op_meta, "typing": op_typing}


def run_op(c, peer, d):
    if not isinstance(d, dict) or d.get("op") not in OPS:
        return 400, {"error": "bad op"}
    if not chat.enabled():
        return 403, {"error": "chat disabled"}
    return OPS[d["op"]](c, peer, d)


@router.post("/fed/v2/chat")
async def fed_chat(request: Request):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not peer_ok(inp.peer):
        return F._signed(inp, 403, {"error": "chat disabled"})

    def run():
        _prework(inp.peer, inp.data)
        with _db() as c, c:
            return run_op(c, inp.peer, inp.data)
    status, out = await run_in_threadpool(run)
    return F._signed(inp, status, out)


def _prework(peer, d):
    """Netzwerk VOR der Transaktion eines Auftrags (E5): Heim braucht fuer 'send' die Schluessel aller Mitglieder
    (Personen dritter Server), fuer 'pagechat' die aufgeloesten Personen/Gruppen der Seite."""
    if not isinstance(d, dict):
        return
    if d.get("op") == "pagechat" and F._ok_rid(d.get("note")):
        prepare_page(d["note"])
    elif d.get("op") == "send" and isinstance(d.get("ref"), str):
        with _db() as c:
            ch = _home_chat(c, {"ref": d["ref"]})
            miss = [h for h in (member_handle(c, u) for u, _ in members(c, ch["id"])) if h and split_handle(h)[1] not in (F.SELF_HOST, peer["host"])
                    and not _cached(c, h)] if ch and ch["kind"] != "dm" else []
        if miss:
            entries(miss)


def _ev_op(c, peer, ev):
    """Auftrag aus der Warteschlange eines Teilnehmer-Servers (Heim war nicht erreichbar)."""
    if not peer_ok(peer):
        return "ignored"
    d = ev.get("op_body") if isinstance(ev.get("op_body"), dict) else {}
    status, out = run_op(c, peer, d)
    if status == 429:
        return "retry"
    if status != 200 and d.get("op") == "send" and isinstance(d.get("ref"), str):
        m = d.get("msg") if isinstance(d.get("msg"), dict) else {}
        if isinstance(m.get("id"), str) and _HEX32.fullmatch(m["id"]):
            F._enqueue(c, peer["id"], {"type": "chat.reject", "ref": d["ref"], "mid": m["id"], "reason": str(out.get("error") or status)[:40],
                                       "handles": out.get("handles")}, chain=chain_down(d["ref"]))
    return "ok" if status == 200 else "ignored"


F.EV2_HANDLERS["chat.op"] = _ev_op


# =====================================================================================================
# Teilnehmer-Server: Ereignisse vom Heim (Kette 'chat:<id>')
# =====================================================================================================
def _from_home(c, peer, ev, lock=False):
    """Chat zu ev['ref'], wenn peer wirklich dessen Heim ist - sonst None (und Pruefprotokoll)."""
    ref = ev.get("ref")
    hx, host = split_ref(ref)
    if not hx:
        return None
    if host != peer["host"]:
        F.log(c, "chat.forged", peer, f"not home of {hx[:8]}", throttle=(peer["id"], "nothome"))
        return None
    ch = by_ref(c, ref, lock)
    if not ch or ch["home_peer"] != peer["id"]:
        return None
    return ch


def _sender_of(c, ch, h):
    """Absender-Handle -> lokaler Benutzername bzw. Prinzipal (nur Mitglieder des Chats), sonst None."""
    n, host = split_handle(h)
    if not n:
        return None
    u = F.uid_by_handle(c, n) if host == F.SELF_HOST else principal(h)
    if not u or not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=?", (ch["id"], u)).fetchone():
        return None
    return u


def apply_msg(c, peer, ch, ev, jump=False):
    """Nachricht vom Heim uebernehmen -> 'ok' | 'dup' | 'gap' | 'keys' (Schluessel erst beim Server der Person holen) |
    'declined' | 'ignored'. Prueft Reihenfolge (fseq lueckenlos, nichts
    Aelteres), Wiederholungen (msg_id) und die Signatur des Absender-Geraets (eigene Konten: eigene Schluessel; Konten
    des Heims: von dort mitgeschickt; andere: Zwischenspeicher)."""
    fseq = ev.get("fseq")
    m = msg_in(ev.get("msg"))
    files = files_in(ev.get("files"), peer)
    keys = ev.get("keys")
    if not isinstance(fseq, int) or isinstance(fseq, bool) or not 0 < fseq < 2 ** 62 or not m or files is None or not _keys_ok(keys):
        return "ignored"
    sender = _sender_of(c, ch, m["sender_handle"])
    if not sender:
        F.log(c, "chat.forged", peer, f"sender {str(m['sender_handle'])[:40]}", throttle=(peer["id"], "fsender"))
        return "ignored"
    if sharing.is_remote(sender) and refused(c, dict(members(c, ch["id"])), sender, m["sender_handle"]):
        # abgelehnt/gesperrt: verwerfen - kein Speichern, kein Vorababruf, kein Ereignis. fseq bleibt stehen: nimmt die Person
        # spaeter doch an, holt ihr Server den Verlauf beim Heim (fill_gap); bis dahin keine Lueckensuche
        return "declined"
    old = c.execute("SELECT seq, chat_id, sig, fseq, fstate FROM chat_msg WHERE id=?", (m["id"],)).fetchone()
    if old:
        if old[1] != ch["id"] or old[2] != m["sig"]:
            F.log(c, "chat.replay", peer, m["id"][:8], throttle=(peer["id"], "replay"))
            return "ignored"
        if fseq > ch["fseq"] + 1 and not jump:
            return "gap"
        c.execute("UPDATE chat_msg SET fseq=?, fstate=NULL WHERE seq=?", (fseq, old[0]))
        c.execute("UPDATE chat SET fseq=GREATEST(fseq, ?) WHERE id=?", (fseq, ch["id"]))
        ch["fseq"] = max(ch["fseq"], fseq)
        if old[3] is None or old[4]:                         # bisher ausstehend (bzw. nicht zugestellt): Anzeige auffrischen
            chat._direct(c, local_members(c, ch["id"]), "chat.message", {"chat_id": ch["id"], "seq": old[0], "id": m["id"], "sender": sender,
                                                                         "fseq": fseq})     # 2.4: Position fuer Gelesen-Haken
        return "dup"
    if fseq <= ch["fseq"]:
        F.log(c, "chat.reorder", peer, f"{ch['fseq']} >= {fseq}", throttle=(peer["id"], "reorder"))
        return "ignored"
    if fseq > ch["fseq"] + 1 and not jump:
        return "gap"
    n, host = split_handle(m["sender_handle"])
    if host == F.SELF_HOST:
        e = local_entry(c, m["sender_handle"])
    elif host == peer["host"]:
        e = FK.check_entry(m["sender_handle"], ev.get("sender_keys"))
        if e:
            _cache_entry(c, peer, m["sender_handle"], e)
    else:
        e = _cached(c, m["sender_handle"])
        if not sig_ok(e, ch["ref"], m, revoked_ok=True):
            # Person eines dritten Servers (Gruppe, E5): Schluessel vom Heim mitgeschickt - selbst geprueft (Zertifikate);
            # zwischengespeichert nur, wenn wir mit ihrem Server nicht selbst verbunden sind (sonst gilt der Abruf dort)
            e2 = FK.check_entry(m["sender_handle"], ev.get("sender_keys"))
            tp = F.peer_by(c, "host", host)
            if FK._peer_ok(tp) and not e:
                # mit ihrem Server selbst verbunden, Schluessel aber noch nicht hier: es zaehlt nur der Abruf dort (E8) - das Heim
                # koennte sonst mit selbst erzeugten Schluesseln Nachrichten dieser Person faelschen. Abruf, dann erneut.
                return "keys"
            if e2 and FK._peer_ok(tp) and e and e2["ident_pub"] != e.get("ident_pub"):
                # mit ihrem Server selbst verbunden: einen ANDEREN Identitaetsschluessel nimmt nur der Abruf dort an - das
                # Heim kann so keine Person eines dritten Servers mit eigenen Schluesseln faelschen (neue Geraete ja)
                F.log(c, "chat.keymismatch", peer, str(m["sender_handle"])[:40], throttle=(peer["id"], "keymm"))
                e2 = None
            if e2:
                e = e2
                if not FK._peer_ok(tp):
                    _cache_entry(c, peer, m["sender_handle"], e2)
    c.execute("UPDATE chat SET fseq=? WHERE id=?", (fseq, ch["id"]))
    ch["fseq"] = fseq
    if not sig_ok(e, ch["ref"], m, revoked_ok=True):
        F.log(c, "chat.badsig", peer, m["id"][:8], throttle=(peer["id"], "badsig"))
        return "ignored"
    origin = peer if host == peer["host"] else (F.peer_by(c, "host", host) if host != F.SELF_HOST else None)
    if host != F.SELF_HOST and (origin is None or (origin["id"] != peer["id"] and not peer_ok(origin))):
        origin = peer                    # Anhang einer Person eines Servers, mit dem wir nicht verbunden sind: ueber das Heim
    seq = store(c, ch, m, sender, local_keys(c, ch, keys), files if origin else [], origin=origin, fseq=fseq)
    deliver(c, ch, seq, m["id"], sender, m.get("kind") or "msg")
    if origin:
        _prefetch_after(c, files, origin)
    return "ok"


def _ev_msg(c, peer, ev):
    if not peer_ok(peer):                # Chat mit dieser Instanz aus (Admin-Schalter) oder Rueckfall auf Protokoll 1
        return "ignored"
    if not _rate_ok("cmsgin", peer["host"], MSG_IN_RATE):
        # Flut vom Heim: nicht verwerfen - die Gegenstelle stellt spaeter erneut zu (Reihenfolge der Kette bleibt)
        _log_retry(c, peer, "chat.rate", "msg in", (peer["id"], "cmsgin"))
        return "retry"
    ch = _from_home(c, peer, ev, lock=True)
    if not ch:
        return "ignored"
    cep = ev.get("cep") if isinstance(ev.get("cep"), str) and len(ev["cep"]) <= 40 else ""
    fep = c.execute("SELECT fep FROM chat WHERE id=?", (ch["id"],)).fetchone()[0]
    if cep and cep != fep:
        fs = ev.get("fseq")
        if fep and isinstance(fs, int) and not isinstance(fs, bool) and 0 < fs <= ch["fseq"]:
            # Heim hat ein Backup zurueckgespielt (neue Ketten-Epoche): seine Zaehlung beginnt wieder tiefer - weiterlesen
            F.log(c, "chat.epoch", peer, f"{ch['fseq']} -> {fs}")
            c.execute("UPDATE chat SET fseq=? WHERE id=?", (fs - 1, ch["id"]))
            ch["fseq"] = fs - 1
        c.execute("UPDATE chat SET fep=? WHERE id=?", (cep, ch["id"]))
    r = apply_msg(c, peer, ch, ev)
    if r == "keys":                      # Schluessel der Person bei ihrem Server holen, dann stellt das Heim erneut zu
        h = (ev.get("msg") or {}).get("sender_handle")
        tp = F.peer_by(c, "host", split_handle(h)[1])
        c.after_commit(lambda: F.peer_submit(tp["id"], entries, [h]))
        return "retry"
    if r == "gap":                       # Luecke: erst beim Heim nachholen, dann stellt das Heim dieses Ereignis erneut zu
        ref, upto, have = ch["ref"], ev.get("fseq"), ch["fseq"]

        def gap():
            with _db() as c2, c2:
                F.log(c2, "chat.gap", peer, f"{have} -> {upto}", throttle=(peer["id"], "gap", ref))
            fill_gap(peer["id"], ref, upto)
        c.after_commit(lambda: F.peer_submit(peer["id"], gap))         # (Pruefprotokoll erst dort: 'retry' rollt diese Transaktion zurueck)
        return "retry"
    return "ok" if r in ("ok", "dup") else "ignored"


def _ev_del(c, peer, ev):
    if not peer_ok(peer):
        return "ignored"
    ch = _from_home(c, peer, ev, lock=True)
    mid = ev.get("mid")
    if not ch or not isinstance(mid, str) or not _HEX32.fullmatch(mid):
        return "ignored"
    drop_msg(c, ch, mid)
    return "ok"


def _ev_removed(c, peer, ev):
    ch = _from_home(c, peer, ev, lock=True)
    if not ch:
        return "ignored"
    by = ev.get("by") if isinstance(ev.get("by"), dict) else {}
    h = by.get("handle")
    n, host = split_handle(h)
    if n and host == F.SELF_HOST:
        u = F.uid_by_handle(c, n)
        bd = {"username": u, "name": sharing.names(c, [u]).get(u) or u} if u else None
    else:
        bd = {"username": principal(h), "handle": h, "name": F.remote_name(by.get("name")) or (n or "?")} if n else None
    chat._drop_notifs(c, local_members(c, ch["id"]), ch["id"])
    removed_live(c, ch, bd)
    drop_chat(c, ch)
    F.log(c, "chat.removed", peer, f"chat={ch['id']}")
    return "ok"


def _ev_meta(c, peer, ev):
    if not peer_ok(peer):
        return "ignored"
    ch = _from_home(c, peer, ev, lock=True)
    if not ch:
        return "ignored"
    n, host = split_handle(ev.get("by"))
    by = (F.uid_by_handle(c, n) if host == F.SELF_HOST else principal(ev["by"])) if n else None
    if not by:
        return "ignored"
    apply_meta(c, ch, {k: ev[k] for k in ("retention_s", "ai") if k in ev}, by)
    return "ok"


def _ev_reject(c, peer, ev):
    """Heim hat eine Nachricht aus unserer Warteschlange abgewiesen (z. B. Empfaenger geaendert): als nicht zugestellt markieren."""
    ch = _from_home(c, peer, ev)
    mid = ev.get("mid")
    if not ch or not isinstance(mid, str):
        return "ignored"
    r = c.execute("UPDATE chat_msg SET fstate='failed' WHERE id=? AND chat_id=? AND fseq IS NULL RETURNING seq, sender_uid", (mid, ch["id"])).fetchone()
    if r:
        chat._direct(c, [r[1]], "chat.message", {"chat_id": ch["id"], "seq": r[0], "id": mid, "sender": r[1], "failed": True})
        F.log(c, "chat.rejected", peer, f"{mid[:8]} {str(ev.get('reason'))[:30]}")
    return "ok"


def _prework_events(peer, evs):
    """Vor der Transaktion: Absender von Nachrichten, die von einem Server stammen, mit dem wir selbst verbunden sind, und
    deren Schluessel noch nicht hier liegen -> dort holen (sonst 'keys' -> 'retry' und das Heim stellt spaeter erneut zu)."""
    need = set()
    with _db() as c:
        for ev in evs[:F.EVENTS_MAX]:
            if not isinstance(ev, dict) or ev.get("type") != "chat.msg" or not isinstance(ev.get("msg"), dict):
                continue
            sh = ev["msg"].get("sender_handle")
            n, host = split_handle(sh)
            if not n or host in (F.SELF_HOST, peer["host"]) or _cached(c, sh):
                continue
            ch = by_ref(c, ev.get("ref")) if isinstance(ev.get("ref"), str) else None
            if not ch or ch["home_peer"] != peer["id"] or not _sender_of(c, ch, sh):
                continue
            if FK._peer_ok(F.peer_by(c, "host", host)):
                need.add(sh)
    if need:
        # nur EIN Server je Sendung (Abruf bis 6 s): ein haengender Dritt-Server darf die Anfrage nicht beliebig lange
        # aufhalten - die uebrigen holt der Hintergrund nach 'keys' (-> 'retry')
        first = split_handle(sorted(need)[0])[1]
        entries(sorted(h for h in need if split_handle(h)[1] == first)[:20])


F.EV2_PREWORK.append(_prework_events)
F.EV2_HANDLERS.update({"chat.msg": _ev_msg, "chat.msg.del": _ev_del, "chat.removed": _ev_removed, "chat.meta": _ev_meta,
                       "chat.reject": _ev_reject})


def _submit(fn, *args):
    try:
        F._express.submit(fn, *args)
    except RuntimeError:
        pass


# --- Luecken nachholen ------------------------------------------------------------------------------------------------
def gap_items(c, ch, peer, after, limit=GAP_PAGE):
    """Heim: Nachrichten mit fseq > after fuer die Instanz peer (nur ihre Empfaenger-Schluessel)."""
    out = []
    rows = c.execute("SELECT seq, id, sender_handle, sender_device, ts, body_ct, sig, reply_to, ai, fseq, kind FROM chat_msg WHERE chat_id=? AND fseq>? "
                     "AND kind IN ('msg','title') AND (expires_at IS NULL OR expires_at>?) ORDER BY fseq LIMIT ?",
                     (ch["id"], after, time.time(), limit)).fetchall()
    for seq, mid, sh, dev, ts, body, sig, reply, ai, fseq, kind in rows:
        keys = {r: v for r, v in c.execute("SELECT recipient, sealed FROM chat_msg_key WHERE msg_seq=?", (seq,)).fetchall()
                if r.rpartition("@")[2] == peer["host"]}
        files = files_for(peer, [{"id": f, "size": s} for f, s in c.execute("SELECT id, size FROM chat_file WHERE msg_seq=?", (seq,)).fetchall()])
        it = {"fseq": fseq, "msg": {"id": mid, "ts": ts, "sender_handle": sh, "sender_device": dev, "body_ct": body, "sig": sig,
                                    "reply_to": reply, "ai": ai, **({"kind": "title"} if kind == "title" else {})}, "keys": keys, "files": files}
        if split_handle(sh)[1] == F.SELF_HOST:
            it["sender_keys"] = local_entry(c, sh)
        elif split_handle(sh)[1] != peer["host"]:            # Person eines dritten Servers: Schluessel ueber das Heim (E5)
            it["sender_keys"] = _cached(c, sh)
        out.append(it)
    return out


@router.get("/fed/v2/chat/msgs")
async def fed_chat_msgs(request: Request, ref: str = "", after: int = 0):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not peer_ok(inp.peer):
        return F._signed(inp, 403, {"error": "chat disabled"})

    def run():
        with _db() as c:
            ch = _home_chat(c, {"ref": ref})
            if not ch:
                return 404, {"error": "gone"}
            # (auch nach Ablehnen: nimmt die Person spaeter doch an, holt ihr Server so den Verlauf - nur ihre Schluessel)
            if not any(sharing.is_remote(u) and split_handle(handle_of(u))[1] == inp.peer["host"] for u, st in members(c, ch["id"])):
                return 403, {"error": "not a participant"}
            items = gap_items(c, ch, inp.peer, max(0, min(after, 2 ** 62)), GAP_PAGE + 1)
            return 200, {"items": items[:GAP_PAGE], "more": len(items) > GAP_PAGE, "top": ch["fseq"]}
    status, out = await run_in_threadpool(run)
    return F._signed(inp, status, out)


def fill_gap(peer_id, ref, upto=None):
    """Teilnehmer: fehlende Nachrichten beim Heim holen (Netzwerk). upto: bis vor diese fseq (das Ereignis kommt erneut).
    Was das Heim nicht mehr hat (geloescht/abgelaufen), gilt als erledigt. -> True, wenn die Luecke zu ist."""
    for _ in range(50):
        with _db() as c:
            p = F.peer_by(c, "id", peer_id)
            ch = by_ref(c, ref)
        if not ch or not peer_ok(p) or ch["home_peer"] != peer_id:
            return False
        try:
            status, d = F.call_json(p, "GET", "/fed/v2/chat/msgs", f"ref={quote(ref, safe='')}&after={ch['fseq']}", timeout=10)
        except F.FedError as ex:
            with _db() as c, c:
                F._touch(c, p, ex)
            return False
        if status != 200 or not isinstance(d, dict) or not isinstance(d.get("items"), list):
            return False
        need = set()                     # Personen von Servern, mit denen wir verbunden sind: Schluessel dort (nicht vom Heim)
        with _db() as c:
            for it in d["items"][:GAP_PAGE + 1]:
                sh = (it.get("msg") or {}).get("sender_handle") if isinstance(it, dict) and isinstance(it.get("msg"), dict) else None
                n_, host_ = split_handle(sh)
                if n_ and host_ not in (F.SELF_HOST, p["host"]) and not _cached(c, sh) and FK._peer_ok(F.peer_by(c, "host", host_)):
                    need.add(sh)
        if need:
            entries(sorted(need))
        with _db() as c, c:
            ch = by_ref(c, ref, lock=True)
            if not ch:
                return False
            for it in sorted((x for x in d["items"] if isinstance(x, dict) and isinstance(x.get("fseq"), int)), key=lambda x: x["fseq"]):
                if upto and it["fseq"] >= upto:
                    break
                if it["fseq"] > ch["fseq"] and apply_msg(c, p, ch, it, jump=True) == "keys":
                    return False             # Schluessel nicht zu bekommen: Luecke bleibt offen (spaeter erneut)
            top = d.get("top") if isinstance(d.get("top"), int) else 0
            if not d.get("more"):
                end = min(top, upto - 1) if upto else top
                if end > ch["fseq"]:
                    c.execute("UPDATE chat SET fseq=? WHERE id=?", (end, ch["id"]))
                return True
    return False


# --- Anhaenge: beim Absender-Server, Abruf bei Bedarf (Zwischenkopie) ----------------------------------------------------
def origin_check(c, peer, fid):
    """Darf Instanz peer den Anhang fid hier abrufen? -> (None, None) oder (Groesse, Herkunft-peer_id|None). Nur Anhaenge
    aus geteilten v2-Chats, an denen peer beteiligt ist; fremde Anhaenge reicht nur das Heim weiter (an andere als den
    Absender-Server)."""
    r = c.execute("SELECT f.chat_id, f.msg_seq, (SELECT x.peer_id FROM chat_file_fed x WHERE x.id=f.id), f.size FROM chat_file f WHERE f.id=?",
                  (fid,)).fetchone()
    ch = chat._load(c, r[0]) if r and r[1] else None
    if ch and r[2] is not None and (ch["home_peer"] is not None or r[2] == peer["id"]):
        return None, None
    if not ch or ch["v"] != 2:
        return None, None
    x = c.execute("SELECT 1 FROM chat_msg WHERE seq=? AND (expires_at IS NULL OR expires_at>?)", (r[1], time.time())).fetchone()
    host = peer["host"]
    part = ch["home_peer"] == peer["id"] or any(sharing.is_remote(u) and split_handle(handle_of(u))[1] == host and st != "declined"
                                                for u, st in members(c, ch["id"]))
    if not x or not part:
        return None, None
    return r[3], r[2]


def parse_range(v, size):
    """'a-b' (Query range, mitsigniert) -> (a, b) oder None. Hoechstens PART_MAX, b wird aufs Dateiende gekuerzt."""
    m = re.fullmatch(r"(\d{1,15})-(\d{1,15})", v or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if b < a or b - a + 1 > PART_MAX or a >= size:
        return None
    return a, min(b, size - 1)


def _read_part(fid, a, b):
    for d in (chat.CHAT_DIR, chat.FED_DIR):
        try:
            with open(os.path.join(d, fid), "rb") as f:
                f.seek(a)
                data = f.read(b - a + 1)
            if len(data) == b - a + 1:
                return data
        except OSError:
            pass
    return None


@router.get("/fed/v2/chat/files/{fid}")
async def fed_chat_file(fid: str, request: Request):
    """Anhang fuer eine beteiligte Instanz (signiert). 2.4 (chatfile2): ?range=a-b (Teil der signierten Anfrage) -> 206 mit
    Content-Digest ueber genau diesen Teil, Saga-Size und Content-Range. Ohne range nur Anhaenge bis FILE_MAX (wie 2.3).
    Im Container beantwortet saga-media Range-Abrufe eigener Anhaenge selbst (Python prueft und signiert nur)."""
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not peer_ok(inp.peer) or not chat._FID.fullmatch(fid):
        return F._signed(inp, 404, {"error": "not found"})
    rq = request.query_params.get("range")

    def run():
        with _db() as c:
            size, remote = origin_check(c, inp.peer, fid)
        if size is None:
            return None
        if rq is not None:
            rg = parse_range(rq, size)
            if rg is None:
                return 416
            if remote is not None:                       # Heim: Anhang eines dritten Servers (E5) - Teil selbst holen
                if not (fetch_range(fid, *rg) if size > RANGE_MIN else fetch_file(fid)):
                    return None
                touch(fid)
            data = _read_part(fid, *rg)
            return None if data is None else (data, rg, size)
        if size > FILE_MAX + 64:
            return 413                                    # grosse Anhaenge nur bereichsweise (chatfile2)
        if remote is not None and not fetch_file(fid):    # Heim: Anhang eines dritten Servers erst selbst holen (E5)
            return None
        if remote is not None:
            touch(fid)
        try:
            with open(os.path.join(chat.CHAT_DIR, fid), "rb") as f:
                return f.read(FILE_MAX + 128)
        except OSError:
            return None
    data = await run_in_threadpool(run)
    if data is None:
        return F._signed(inp, 404, {"error": "not found"})
    if data in (413, 416):
        return F._signed(inp, data, {"error": "range required" if data == 413 else "bad range"})
    if isinstance(data, tuple):
        body, (a, b), size = data
        return F._signed(inp, 206, body, media="application/octet-stream",
                         extra={"Saga-Size": str(size), "Content-Range": f"bytes {a}-{b}/{size}"})
    return F._signed(inp, 200, data, media="application/octet-stream")


def fetch_file(fid):
    """Anhang einer anderen Instanz holen und als Zwischenkopie ablegen (Netzwerk). -> True, wenn er jetzt hier liegt."""
    path = os.path.join(chat.CHAT_DIR, fid)
    if os.path.isfile(path):
        return True
    with _db() as c:
        r = c.execute("SELECT x.peer_id, f.size FROM chat_file_fed x JOIN chat_file f ON f.id=x.id WHERE x.id=? AND f.msg_seq IS NOT NULL",
                      (fid,)).fetchone()
        p = F.peer_by(c, "id", r[0]) if r else None
    if not peer_ok(p):
        return False
    try:
        status, _, data = F.call(p, "GET", f"/fed/v2/chat/files/{fid}", limit=min(r[1], FILE_MAX + 64) + 1024, timeout=20)
    except F.FedError as ex:
        with _db() as c, c:
            F._touch(c, p, ex)
        return False
    # genau die angekuendigte Groesse (wie beim Absender gespeichert) - sonst koennte ein "Vorschaubild" beliebig gross sein
    if status != 200 or len(data) < 40 or len(data) > FILE_MAX + 64 or len(data) != r[1]:
        return False
    os.makedirs(chat.CHAT_DIR, mode=0o700, exist_ok=True)
    tmp = path + ".part-" + secrets.token_hex(4)
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
        f.write(data)
    with _db() as c, c:
        if not c.execute("SELECT 1 FROM chat_file WHERE id=?", (fid,)).fetchone():       # inzwischen geloescht
            os.remove(tmp)
            return False
        os.replace(tmp, path)
        c.execute("UPDATE chat_file_fed SET cached=1 WHERE id=?", (fid,))
    cache_trim(p["id"], keep=fid)
    return True


def touch(fid):
    """Zwischenkopie wurde gelesen (zuletzt benutzt - bleibt beim Begrenzen am laengsten)."""
    for d in (chat.CHAT_DIR, chat.FED_DIR):
        try:
            os.utime(os.path.join(d, fid))
            return
        except OSError:
            pass


def _evict(c, fid):
    for d in (chat.CHAT_DIR, chat.FED_DIR):
        try:
            os.remove(os.path.join(d, fid))
        except FileNotFoundError:
            pass
    c.execute("UPDATE chat_file_fed SET cached=0, have=NULL WHERE id=?", (fid,))


def _units(size):
    return max(1, -(-int(size) // UNIT))


def _popcount(have):
    return sum(bin(x).count("1") for x in bytes(have or b""))


def cache_trim(peer_id, keep=None):
    """Zwischenkopien der Anhaenge einer Instanz auf CACHE_PEER_MAX begrenzen (am laengsten unbenutzte zuerst). Lueckenhafte
    Kopien (2.4) zaehlen mit ihren geholten 1-MiB-Einheiten. -> Anzahl entfernter."""
    with _db() as c:
        if not peer_ok(F.peer_by(c, "id", peer_id)):    # getrennt/abgeschaltet: nicht neu zu holen - Kopien bleiben
            return 0
        rows = c.execute("SELECT x.id, CASE WHEN x.cached=1 THEN f.size ELSE 0 END, x.have FROM chat_file_fed x JOIN chat_file f ON f.id=x.id "
                         "WHERE x.peer_id=? AND (x.cached=1 OR x.have IS NOT NULL)", (peer_id,)).fetchall()
    rows = [(fid, size or min(_popcount(have) * UNIT, 1 << 62)) for fid, size, have in rows]
    total = sum(r[1] for r in rows)
    if total <= CACHE_PEER_MAX:
        return 0
    def used(fid):
        for d in (chat.CHAT_DIR, chat.FED_DIR):
            try:
                return os.stat(os.path.join(d, fid)).st_mtime
            except OSError:
                pass
        return 0.0
    n = 0
    with _db() as c, c:
        for fid, size in sorted(rows, key=lambda r: used(r[0])):
            if total <= CACHE_PEER_MAX:
                break
            if fid == keep:
                continue
            _evict(c, fid)
            total -= size
            n += 1
        if n:
            p = F.peer_by(c, "id", peer_id)
            F.log(c, "chat.cache", p, f"evicted={n}", throttle=(peer_id, "cache"))
    return n


def _prefetch_after(c, files, origin):
    small = [f["id"] for f in files if f["size"] <= PREFETCH_MAX]
    big = [f["id"] for f in files if f["size"] > RANGE_MIN] if F.feature(origin, "chatfile2") else []
    if small or big:                     # (je Instanz begrenzt: eine langsame Gegenstelle belegt nicht den ganzen Pool)
        c.after_commit(lambda: F.peer_submit(origin["id"], _prefetch, small, big))


def _prefetch(fids, big=()):
    for f in fids:
        try:
            fetch_file(f)
        except OSError as ex:
            print(f"fed_chat: Anhang vorab: {type(ex).__name__}", flush=True)
    for f in big:                        # 2.4: Videos - Anfang und Ende (moov vorn oder hinten) vorab: schnellerer Start
        try:
            warm(f)
        except OSError as ex:
            print(f"fed_chat: Video vorab: {type(ex).__name__}", flush=True)


# --- 2.4: grosse Anhaenge bereichsweise (chatfile2) --------------------------------------------------------------------
# Lueckenhafte Zwischenkopie STATE_DIR/chat/fed/<id> (sparse, volle Laenge), Bitmap chat_file_fed.have: Bit u (Byte u>>3,
# Bit u&7, niedrigstes zuerst) = 1-MiB-Einheit u liegt geprueft vor. Im Container holt saga-media die Bytes selbst:
# /internal/media/fed-need liefert den Plan (signierte Anfragen je Teil, erlaubte Adressen), saga-media laedt, schreibt,
# rechnet den Digest und meldet ihn an /internal/media/fed-got (Python prueft nur die Signatur der Antwort). Ohne
# saga-media laedt Python (fetch_range) - gleiche Regeln.
_plans = {}                              # Nonce -> (fid, von, bis, peer_id, Ablauf)
_plans_lock = threading.Lock()
PLAN_TTL = 120


def ranged(fid):
    """Grosser Anhang einer Instanz mit chatfile2, noch nicht ganz hier? -> {"size": n} oder None."""
    with _db() as c:
        r = c.execute("SELECT x.peer_id, f.size FROM chat_file_fed x JOIN chat_file f ON f.id=x.id WHERE x.id=? AND f.msg_seq IS NOT NULL",
                      (fid,)).fetchone()
        p = F.peer_by(c, "id", r[0]) if r else None
    if not r or r[1] <= RANGE_MIN or not p or not F.feature(p, "chatfile2"):
        return None
    return {"size": r[1]}


def _have(c, fid, lock=False):
    r = c.execute("SELECT have FROM chat_file_fed WHERE id=?" + (" FOR UPDATE" if lock else ""), (fid,)).fetchone()
    return bytearray(r[0] or b"") if r else None


def _missing(have, size, a, b):
    """Fehlende 1-MiB-Einheiten fuer den Byte-Bereich a..b des Blobs."""
    return [u for u in range(a // UNIT, b // UNIT + 1) if not (u >> 3 < len(have) and have[u >> 3] >> (u & 7) & 1)]


def _dial(base):
    """Erlaubte Adressen der Gegenstelle (wie netguard: oeffentlich oder im Admin freigegeben) -> ['ip:port', ...]."""
    u = urlparse(base)
    host, port = u.hostname, u.port or (443 if u.scheme == "https" else 80)
    out = []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    for ai in infos:
        ip = ai[4][0]
        if F._ip_ok(ip, host, port):
            a = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            if a not in out:
                out.append(a)
    return out


def plan(fid, a, b, limit=2 * PART_MAX):
    """Was fehlt fuer a..b? -> {size, have(b64), path, fetch:[{from,to,url,headers,nonce,dial}]} oder None.
    Geplant wird ab der ersten fehlenden Einheit hoechstens limit Bytes (zusammenhaengende Teile bis PART_MAX)."""
    rg = ranged(fid)
    if not rg or os.path.isfile(os.path.join(chat.CHAT_DIR, fid)):    # inzwischen ganz hier: normaler Weg
        return None
    size = rg["size"]
    a, b = max(0, a), min(b, size - 1)
    with _db() as c:
        have = _have(c, fid)
        r = c.execute("SELECT peer_id FROM chat_file_fed WHERE id=?", (fid,)).fetchone()
        p = F.peer_by(c, "id", r[0]) if r else None
    if have is None or not peer_ok(p):
        return None
    out = {"size": size, "have": base64.b64encode(bytes(have)).decode(), "path": "fed/" + fid, "unit": UNIT, "fetch": []}
    miss = _missing(have, size, a, b) if a <= b else []
    if not miss:
        return out
    if F.RESTORE_TEST:
        return None
    priv, _ = F.own_key()
    netloc = urlparse(p["base_url"]).netloc
    dial = _dial(p["base_url"])
    path = f"/fed/v2/chat/files/{fid}"
    budget, i = limit, 0
    while i < len(miss) and budget > 0:
        j = i
        while j + 1 < len(miss) and miss[j + 1] == miss[j] + 1 and (miss[j + 1] - miss[i] + 1) * UNIT <= PART_MAX:
            j += 1
        fr, to = miss[i] * UNIT, min((miss[j] + 1) * UNIT, size) - 1
        q = f"range={fr}-{to}"
        hdr, nonce = fedsig.sign_request(priv, F.SELF_URL, "GET", netloc, path, q, b"")
        hdr.update({"Accept": "application/octet-stream", "User-Agent": f"Saga/{F.VERSION}"})
        out["fetch"].append({"from": fr, "to": to, "url": p["base_url"] + path + "?" + q, "headers": hdr, "nonce": nonce, "dial": dial})
        with _plans_lock:
            now = time.time()
            if len(_plans) > 5000:
                for k in [k for k, v in _plans.items() if v[4] < now]:
                    _plans.pop(k, None)
            _plans[nonce] = (fid, fr, to, p["id"], now + PLAN_TTL)
        budget -= to - fr + 1
        i = j + 1
    return out


def _mark(fid, fr, to, size):
    """Einheiten fr..to als geprueft eintragen -> (have, komplett). Komplett: Datei ins Blob-Verzeichnis (wie bis 2.3)."""
    done = False
    with _db() as c, c:
        have = _have(c, fid, lock=True)
        if have is None:
            return None, False
        n = _units(size)
        if len(have) < (n + 7) // 8:
            have.extend(b"\0" * ((n + 7) // 8 - len(have)))
        for u in range(fr // UNIT, to // UNIT + 1):
            have[u >> 3] |= 1 << (u & 7)
        done = all(have[u >> 3] >> (u & 7) & 1 for u in range(n))
        if done:
            try:
                os.replace(os.path.join(chat.FED_DIR, fid), os.path.join(chat.CHAT_DIR, fid))
                c.execute("UPDATE chat_file_fed SET cached=1, have=NULL WHERE id=?", (fid,))
            except OSError:
                done = False
        if not done:
            c.execute("UPDATE chat_file_fed SET have=? WHERE id=?", (bytes(have), fid))
    return have, done


def got(nonce, status, headers, dig, length):
    """saga-media hat einen geplanten Teil geladen und geschrieben -> {ok, have, complete} bzw. {ok: False, error}."""
    with _plans_lock:
        pl = _plans.pop(str(nonce or ""), None)
    if not pl or pl[4] < time.time():
        return {"ok": False, "error": "unknown"}
    fid, fr, to, peer_id = pl[:4]
    with _db() as c:
        p = F.peer_by(c, "id", peer_id)
        r = c.execute("SELECT size FROM chat_file WHERE id=?", (fid,)).fetchone()
    if not p or not r:
        return {"ok": False, "error": "gone"}
    try:
        fedsig.verify_response(fedsig.unb64u(p["public_key"]), p["base_url"], status, {k.lower(): v for k, v in (headers or {}).items()},
                               None, nonce, dig=str(dig or ""))
    except fedsig.SigError as ex:
        with _db() as c, c:
            F.log(c, "chat.filesig", p, str(ex), throttle=(p["id"], "filesig"))
        return {"ok": False, "error": "signature"}
    if status != 206 or length != to - fr + 1:
        return {"ok": False, "error": f"HTTP {status}"}
    have, done = _mark(fid, fr, to, r[0])
    if have is None:
        return {"ok": False, "error": "gone"}
    if done or fr == 0:
        F.peer_submit(peer_id, cache_trim, peer_id, fid)
    return {"ok": True, "have": base64.b64encode(bytes(have)).decode(), "complete": done}


def fetch_range(fid, a, b):
    """Python-Ersatzweg (ohne saga-media, Heim fuer dritte Server, Vorababruf): fehlende Teile von a..b holen. -> True,
    wenn a..b jetzt hier liegt."""
    rg = ranged(fid)
    if not rg:
        return os.path.isfile(os.path.join(chat.CHAT_DIR, fid))
    for _ in range(64):
        pl = plan(fid, a, b, limit=PART_MAX)
        if pl is None:
            return os.path.isfile(os.path.join(chat.CHAT_DIR, fid))
        if not pl["fetch"]:
            return True
        ch_ = pl["fetch"][0]
        with _db() as c:
            r = c.execute("SELECT peer_id FROM chat_file_fed WHERE id=?", (fid,)).fetchone()
            p = F.peer_by(c, "id", r[0]) if r else None
        if not p:
            return False
        try:
            status, rh, data = F._send("GET", ch_["url"], dict(ch_["headers"]), b"", ch_["to"] - ch_["from"] + 1 + 1024, F.TIMEOUT)
        except F.FedError as ex:
            with _db() as c, c:
                F._touch(c, p, ex)
            with _plans_lock:
                _plans.pop(ch_["nonce"], None)
            return False
        if status != 206 or len(data) != ch_["to"] - ch_["from"] + 1:
            with _plans_lock:
                _plans.pop(ch_["nonce"], None)
            return False
        os.makedirs(chat.FED_DIR, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(chat.FED_DIR, fid), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(fd).st_size != pl["size"]:
                os.ftruncate(fd, pl["size"])
            os.pwrite(fd, data, ch_["from"])
        finally:
            os.close(fd)
        res = got(ch_["nonce"], status, rh, fedsig.digest(data), len(data))
        if not res.get("ok"):
            return False
    return False


def warm(fid):
    """Vorababruf eines grossen Anhangs: erste und letzte 1-MiB-Einheit (moov vorn oder hinten). Im Container ueber
    saga-media (die Bytes laufen nicht durch Python), sonst fetch_range."""
    rg = ranged(fid)
    if not rg:
        return False
    size = rg["size"]
    spans = [(0, min(UNIT, size) - 1)] + ([((_units(size) - 1) * UNIT, size - 1)] if size > UNIT else [])
    if MEDIA_FRONT:
        try:
            import httpx
            host = F.E.get("HOST") or "127.0.0.1"
            host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
            port = F.E.get("PORT") or "8080"
            for a, b in spans:
                httpx.post(f"http://{host if ':' not in host else '[' + host + ']'}:{port}/internal/media/warm",
                           params={"fid": fid, "from": a, "to": b, "size": size}, headers={"X-Saga-Media-Key": MEDIA_KEY}, timeout=30, trust_env=False)
            return True
        except Exception as ex:  # noqa: BLE001  (Vorabruf ist nur Beschleunigung)
            print(f"fed_chat: Vorababruf ueber saga-media: {type(ex).__name__}", flush=True)
    return all(fetch_range(fid, a, b) for a, b in spans)


def serve_range(fid, part, request, headers):
    """Python-Ersatzweg fuer GET/HEAD /api/chat/files/{fid} einer lueckenhaften Zwischenkopie: Range auswerten, Fehlendes
    holen (hoechstens SERVE_MAX je Antwort - der Client fordert den Rest an), 206 liefern."""
    from fastapi.responses import Response
    size = part["size"]
    h = dict(headers, **{"Accept-Ranges": "bytes"})
    h.pop("ETag", None)
    h["ETag"] = f'"{fid}"'
    rh = (request.headers.get("range") or "").strip()
    a, b, status = 0, size - 1, 200
    m = re.fullmatch(r"bytes=(\d*)-(\d*)(,.*)?", rh)
    if m and (m.group(1) or m.group(2)):
        if not m.group(1):
            n = int(m.group(2))
            if n == 0:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Cache-Control": "no-store"})
            a, b = max(0, size - n), size - 1
        else:
            a = int(m.group(1))
            b = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if a >= size or b < a:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Cache-Control": "no-store"})
        status = 206
    if request.method == "HEAD":
        h["Content-Length"] = str(b - a + 1)
        if status == 206:
            h["Content-Range"] = f"bytes {a}-{b}/{size}"
        return Response(status_code=status, headers=h, media_type="application/octet-stream")
    b = min(b, a + SERVE_MAX - 1)
    if not fetch_range(fid, a, b):
        return Response(status_code=502, headers={"Cache-Control": "no-store", "Retry-After": "5"})
    data = _read_part(fid, a, b)
    if data is None:
        return Response(status_code=502, headers={"Cache-Control": "no-store", "Retry-After": "5"})
    touch(fid)
    h["Content-Range"] = f"bytes {a}-{b}/{size}"
    return Response(data, status_code=206, headers=h, media_type="application/octet-stream")


# --- Fuer saga-media (gleicher Container, Geheimnis im Kopf) -----------------------------------------------------------
def _internal_ok(request):
    return bool(MEDIA_KEY) and secrets.compare_digest(request.headers.get("x-saga-media-key", ""), MEDIA_KEY)


@router.post("/internal/media/fed-need")
async def api_fed_need(request: Request):
    """{fid, from, to} -> Plan (siehe plan). saga-media hat die Berechtigung des Clients schon (authz, fed)."""
    if not _internal_ok(request):
        return _bad("not found", 404)
    b = await _body(request) or {}
    fid, a, z = b.get("fid"), b.get("from"), b.get("to")
    if not isinstance(fid, str) or not chat._FID.fullmatch(fid) or not isinstance(a, int) or not isinstance(z, int) or a < 0 or z < a:
        return _bad("bad request", 400)
    pl = await run_in_threadpool(plan, fid, a, z)
    if pl is None:
        return _bad("not found", 404)
    return chat._no_store(pl)


@router.post("/internal/media/fed-got")
async def api_fed_got(request: Request):
    """{nonce, status, headers, digest, length} -> {ok, have, complete}."""
    if not _internal_ok(request):
        return _bad("not found", 404)
    b = await _body(request) or {}
    st, ln, hd = b.get("status"), b.get("length"), b.get("headers")
    if not isinstance(st, int) or not isinstance(ln, int) or not isinstance(hd, dict):
        return _bad("bad request", 400)
    return chat._no_store(await run_in_threadpool(got, b.get("nonce"), st, {str(k): str(v) for k, v in hd.items()}, b.get("digest"), ln))


@router.post("/internal/media/fed-serve")
async def api_fed_serve(request: Request):
    """saga-media beantwortet GET /fed/v2/chat/files/{fid}?range=a-b eines eigenen Anhangs selbst: Python prueft hier die
    Signatur der Anfrage und die Berechtigung und signiert die Antwort ueber den von saga-media berechneten Digest.
    {method, path, query, headers, fid, digest, from, to, size} -> {ok, headers} | {pass: true} | {ok: false, status, headers, body}."""
    if not _internal_ok(request):
        return _bad("not found", 404)
    b = await _body(request) or {}
    fid, hd = b.get("fid"), b.get("headers")
    if not isinstance(fid, str) or not chat._FID.fullmatch(fid) or not isinstance(hd, dict):
        return _bad("bad request", 400)

    def run():
        with _db() as c:
            if c.execute("SELECT 1 FROM chat_file_fed WHERE id=?", (fid,)).fetchone():
                return {"pass": True}                   # Zwischenkopie eines fremden Anhangs: Python wie bisher (Heim, E5)
        heads = {str(k).lower(): str(v) for k, v in hd.items()}
        inp, err = F._verify_sync("GET", str(b.get("path") or ""), str(b.get("query") or ""), heads, b"", ("active",))
        if err is not None:
            return {"ok": False, "status": err.status_code, "headers": dict(err.headers), "body": base64.b64encode(err.body).decode()}

        def fail(code, msg):
            r = F._signed(inp, code, {"error": msg})
            return {"ok": False, "status": code, "headers": {k: v for k, v in r.headers.items() if k.lower() != "content-length"},
                    "body": base64.b64encode(r.body).decode()}
        if not peer_ok(inp.peer):
            return fail(404, "not found")
        with _db() as c:
            size, remote = origin_check(c, inp.peer, fid)
        if size is None or remote is not None:
            return fail(404, "not found")
        rg = parse_range(urllib_parse.parse_qs(str(b.get("query") or "")).get("range", [""])[0], size)
        if rg is None or rg != (b.get("from"), b.get("to")) or b.get("size") != size:
            return fail(416, "bad range")
        priv, _ = F.own_key()
        h = fedsig.sign_response(priv, F.SELF_URL, 206, None, inp.nonce, dig=str(b.get("digest") or ""))
        h.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Saga-Size": str(size),
                  "Content-Range": f"bytes {rg[0]}-{rg[1]}/{size}", "Content-Type": "application/octet-stream"})
        return {"ok": True, "headers": h}
    return chat._no_store(await run_in_threadpool(run))


# =====================================================================================================
# Fuer die eigenen Konten (aufgerufen aus saga.chat: Endpunkte /api/chats/... bei Chats mit v=2)
# =====================================================================================================
def to_home(ch, body):
    """Auftrag an das Heim: synchron (harte Frist) - ist es nicht erreichbar oder wartet schon etwas in der Warteschlange
    dieses Chats, in die Warteschlange (Reihenfolge je Chat, bis 3 Tage). -> (status, antwort) bzw. (None, None) = wartet."""
    with _db() as c:
        p = F.peer_by(c, "id", ch["home_peer"])
        queued = bool(c.execute("SELECT 1 FROM fed_outbox WHERE peer_id=? AND chain=? LIMIT 1", (ch["home_peer"], chain_up(ch["ref"]))).fetchone())
    if not p:
        return 404, {"error": "gone"}
    if not queued:
        status, d = sync_call(p, body)
        if status is not None and status != 429 and status < 500:
            return status, d if isinstance(d, dict) else {}
    with _db() as c, c:
        F._enqueue(c, p["id"], {"type": "chat.op", "op_body": body}, chain=chain_up(ch["ref"]), express=False)
    return None, None


def _ro_error(c, ch):
    """Warum hier gerade nicht geschrieben werden kann - oder None."""
    if ch["kind"] in ("free", "page"):                  # Gruppen (E5): nur das Heim zaehlt (Teilnehmer fallen beim Trennen raus)
        return _ro_group(c, ch)
    if ch["fstate"] == "gone":
        return t("Das Konto der anderen Person gibt es nicht mehr.")
    if ch["fstate"]:
        return t("Diese Unterhaltung ist schreibgeschützt – die Verbindung zum anderen Saga-Server wurde getrennt.")
    for p in ([F.peer_by(c, "id", ch["home_peer"])] if ch["home_peer"] else participants(c, ch)):
        if not peer_ok(p):
            return t("Chats mit {host} sind gerade nicht möglich (vom Admin abgeschaltet oder Verbindung getrennt).", host=p["host"] if p else "?")
    return None


def create_dm(uid, h, device=""):
    """Direkt-Chat mit einer Person einer anderen Instanz ('~x@host'). Unser Server wird das Heim; beim Gegenueber erscheint
    der Chat als Nachrichtenanfrage. Gibt es dort schon einen gemeinsamen Chat, wird der genommen. -> Antwort (JSONResponse)"""
    n, host = split_handle(str(h or "").strip().lower())
    if not n or not on():
        return _bad(t("ungültige Angabe"))
    h = f"{n}@{host}"
    p_ = principal(h)
    key = "|".join(sorted([uid, p_]))
    with _db() as c, c:
        p = F.peer_by(c, "host", host)
        if not peer_ok(p):
            return _bad(t("Mit Personen auf {host} sind Chats nicht möglich.", host=host), 403)
        if blocked(c, uid, h):
            return _bad(t("Du hast diese Person bzw. ihren Server gesperrt – erst in „Chats & Geräte“ entsperren."), 403)
        ex = chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE dm_key=?", (key,)).fetchone())
        if ex and not ex["fstate"]:               # schreibgeschuetzt (z. B. nach Trennen + neu Verbinden): neuer Chat, alter bleibt lesbar
            return _reopen(c, uid, ex, device)
        if c.execute("SELECT COUNT(*) FROM chat WHERE created_by=? AND created>?", (uid, time.time() - 3600)).fetchone()[0] >= 60:
            return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
        mh, name = my_handle(c, uid), F.display_of(c, uid) or ""
    ref = secrets.token_hex(16) + "@" + F.SELF_HOST
    status, d = sync_call(p, {"op": "request", "ref": ref, "kind": "dm", "from": {"handle": mh, "name": name}, "to": h})
    if status is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=host), 502)
    if status == 404:
        return _bad(t("Diese Person gibt es auf {host} nicht (mehr).", host=host), 404)
    if status == 429:
        return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
    if status != 200 or not isinstance(d, dict):
        return _bad(t("Mit dieser Person ist kein Chat möglich."), 403)
    use, home = ref, None
    if isinstance(d.get("existing"), str) and split_ref(d["existing"])[1] == host:
        use, home = d["existing"], p["id"]
    now = time.time()
    with _db() as c, c:
        ex = chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE dm_key=? FOR UPDATE", (key,)).fetchone())
        if ex and ex["ref"] != use:              # gleichzeitig von beiden Seiten begonnen: die kleinere Kennung gilt ueberall
            if ex["v"] == 2 and not ex["fstate"] and ex["ref"] < use:
                return _reopen(c, uid, ex, device)
            if c.execute("SELECT 1 FROM chat_msg WHERE chat_id=? LIMIT 1", (ex["id"],)).fetchone():
                c.execute("UPDATE chat SET dm_key=NULL, fstate=CASE WHEN fstate='' THEN 'old' ELSE fstate END WHERE id=?", (ex["id"],))
            else:
                drop_chat(c, ex)
            ex = None
        if ex:
            return _reopen(c, uid, ex, device)
        _person(c, p, h)
        cid = c.execute("INSERT INTO chat (kind, dm_key, created_by, created, v, ref, home_peer) VALUES ('dm',?,?,?,2,?,?)",
                        (key, uid, now, use, home)).lastrowid
        c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'ok')", (cid, uid, now))
        c.execute("INSERT INTO chat_member (chat_id, uid, joined, state) VALUES (?,?,?,'ok')", (cid, p_, now))
        F.log(c, "chat.started", p, f"chat={cid}" + (" (existing)" if home else ""))
        ch = chat._load(c, cid)
        chat._changed(c, cid, [uid])
        out = _reopen(c, uid, ch, device)
    if home:
        F.peer_submit(p["id"], fill_gap, p["id"], use, None)
    return out


def _reopen(c, uid, ch, device=""):
    """Vorhandener Direkt-Chat wird (wieder) geoeffnet: abgelehnt/ausgeblendet -> wieder da (Annehmen beim Heim)."""
    m = chat._member(c, ch["id"], uid)
    if not m:
        return _bad("not found", 404)
    if m.get("state") != "ok":
        c.execute("UPDATE chat_member SET state='ok' WHERE chat_id=? AND uid=?", (ch["id"], uid))
        if ch["home_peer"] is not None:
            F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "accept", "ref": ch["ref"], "actor": my_handle(c, uid)}},
                       chain=chain_up(ch["ref"]))
            hp, ref = ch["home_peer"], ch["ref"]
            c.after_commit(lambda: F.peer_submit(hp, fill_gap, hp, ref, None))
    c.execute("UPDATE chat_pref SET hidden=0 WHERE chat_id=? AND uid=? AND hidden<>0", (ch["id"], uid))
    mem = chat.members(c, ch["id"])
    return chat._no_store({"chat": chat._chat_dict(c, uid, ch, mem, chat._member(c, ch["id"], uid), chat._my_device(c, uid, str(device or "")), full=True)})


def send(uid, cid, b):
    """POST /api/chats/{cid}/messages fuer foederierte Chats: {id, ts, sender_device, body_ct, sig, reply_to?, ai?, ai_reply?,
    keys: {'dev:<id>@host'|'backup:<handle>': sealed}, files: [ids]}. Signatur saga-chat/v2 (chat_ref, eigener Handle)."""
    m = msg_in(dict(b, kind=b.get("kind") or "msg"))
    keys, fids, ai_ref = b.get("keys"), b.get("files") or [], b.get("ai_reply")
    if not m or not isinstance(fids, list) or len(fids) > chat.FILE_IDS_PER_MSG or any(not isinstance(f, str) or not chat._FID.fullmatch(f) for f in fids):
        if isinstance(b.get("body_ct"), str) and len(b["body_ct"]) > chat.MSG_MAX:
            return _bad(t("Nachricht zu groß."), 413)
        return _bad(t("ungültige Angabe"))
    if ai_ref is not None and not (isinstance(ai_ref, str) and _HEX32.fullmatch(ai_ref) and m["ai"]):
        return _bad(t("ungültige Angabe"))
    if not _keys_ok(keys):
        return _bad(t("ungültige Angabe"))
    if abs(m["ts"] / 1000 - time.time()) > chat.SKEW:
        return _bad(t("Die Uhrzeit dieses Geräts weicht zu stark ab."), 400)
    with _db() as c, c:                                  # Seiten-Chat mit Heim hier: Freigaben jetzt pruefen (Entzug wirkt sofort)
        ch0 = chat._load(c, cid)
        if ch0 and ch0["kind"] == "page" and ch0["home_peer"] is None and chat.hfmt(ch0):
            chat.sync(c, ch0)
    with _db() as c:
        ch, mm, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        ro = _ro_error(c, ch)
        if ro:
            return _bad(ro, 403)
        if m.get("kind") == "title" and ch["kind"] != "free":
            return _bad(t("ungültige Angabe"))
        mh = my_handle(c, uid)
        m["sender_handle"] = mh
        mine = local_entry(c, mh)
        handles = [member_handle(c, u) for u in mem]
        if not c.execute("SELECT 1 FROM chat_device WHERE id=? AND uid=? AND revoked_at IS NULL", (m["sender_device"], uid)).fetchone():
            return _bad(t("Dieses Gerät ist für Chats nicht (mehr) angemeldet."), 403)
        if not mine or not any(d["id"] == m["sender_device"] for d in mine["devices"]):
            return _bad(t("Dieses Gerät hat noch kein Zertifikat für Chats über Servergrenzen – bitte die Seite neu laden."), 403)
        if not sig_ok(mine, ch["ref"], m):
            return _bad(t("Ungültige Signatur."), 400)
        if c.execute("SELECT COUNT(*) FROM chat_msg WHERE sender_uid=? AND created>?", (uid, time.time() - 60)).fetchone()[0] >= chat.MSG_RATE:
            return _bad(t("Zu viele Nachrichten in kurzer Zeit – bitte kurz warten."), 429)
        if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m["id"],)).fetchone():
            return _bad(t("Diese Nachricht gibt es schon."), 409)
        if m["ai"] and not c.execute("SELECT 1 FROM chat_ai WHERE chat_id=?", (ch["id"],)).fetchone():
            return _bad(t("Die KI ist in diesem Chat nicht erlaubt."), 403)
        if ai_ref and not c.execute("SELECT 1 FROM chat_ai_reply WHERE id=? AND chat_id=? AND uid=? AND msg_id IS NULL AND created>? AND sha256=?",
                                    (ai_ref, ch["id"], uid, time.time() - chat.AI_REPLY_TTL, m["ai"])).fetchone():
            return _bad(t("Diese KI-Antwort gibt es nicht (mehr) oder sie wurde schon gesendet."), 409)
        files = []
        if fids:
            files = [{"id": r[0], "size": r[1]} for r in c.execute(
                f"SELECT id, size FROM chat_file WHERE chat_id=? AND uid=? AND msg_seq IS NULL AND id IN ({','.join('?' * len(fids))})",
                (ch["id"], uid, *fids)).fetchall()]
            if len(files) != len(set(fids)):
                return _bad(t("Anhang nicht gefunden."), 400)
            if any(f["size"] > FILE_MAX + 64 for f in files) and not big_files_ok(c, ch):     # 2.4: 2.3-Server dabei
                return _bad(t("Der Server des Gegenübers kann große Videos noch nicht (höchstens {n} MB).", n=FILE_MAX // 1048576), 409)
    ents = entries(handles, uid=uid)                 # Netzwerk: Schluessel der anderen (Zwischenspeicher)
    if set(keys) != want_of(ents):
        return chat._no_store({"error": t("Die Mitglieder oder ihre Geräte haben sich geändert – bitte erneut senden."), "handles": handles}, 409)
    home = ch["home_peer"] is None
    with _db() as c, c:
        ch = chat._load(c, ch["id"])
        c.execute("SELECT 1 FROM chat WHERE id=? FOR UPDATE", (ch["id"],))
        if c.execute("SELECT 1 FROM chat_msg WHERE id=?", (m["id"],)).fetchone():
            return _bad(t("Diese Nachricht gibt es schon."), 409)
        if ai_ref and not c.execute("SELECT 1 FROM chat_ai_reply WHERE id=? AND msg_id IS NULL FOR UPDATE", (ai_ref,)).fetchone():
            return _bad(t("Diese KI-Antwort gibt es nicht (mehr) oder sie wurde schon gesendet."), 409)
        if mm.get("state") != "ok":                 # wer schreibt, nimmt die Anfrage an
            c.execute("UPDATE chat_member SET state='ok' WHERE chat_id=? AND uid=?", (ch["id"], uid))
            if not home:
                F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "accept", "ref": ch["ref"], "actor": mh}}, chain=chain_up(ch["ref"]))
        fseq = c.execute("UPDATE chat SET fseq=fseq+1 WHERE id=? RETURNING fseq", (ch["id"],)).fetchone()[0] if home else None
        seq = store(c, ch, m, uid, local_keys(c, ch, keys), files, fseq=fseq, fstate=None if home else "pending", all_keys=keys if home else None)
        if ai_ref:
            c.execute("UPDATE chat_ai_reply SET msg_id=? WHERE id=?", (m["id"], ai_ref))
        deliver(c, ch, seq, m["id"], uid, m.get("kind") or "msg")
        if home:
            fanout(c, ch, {"type": "chat.msg", "fseq": fseq, "msg": m, "files": files, "sender_keys": mine}, keys=keys, declined=False)
    out = {"ok": True, "seq": seq, "fseq": fseq, "created": time.time()}
    if home:
        print(f"chat: Nachricht in Chat {ch['id']} (foederiert, Heim, {len(keys)} Empfaenger)", flush=True)
        return chat._no_store(out)
    status, d = to_home(ch, {"op": "send", "ref": ch["ref"], "msg": m, "keys": keys, "files": files, "sender_keys": mine})
    with _db() as c, c:
        if status == 200:
            # Die Nachricht wurde oben schon als 'pending' gemeldet (deliver -> chat.message): Apps haben sie mit "wird
            # zugestellt" geladen. Bestaetigung wie aus der Warteschlange als Ereignis melden - sonst bleibt die Anzeige haengen
            # (kam die Bestaetigung ueber den Fan-out des Heims schon an, hat apply_msg das bereits erledigt).
            if c.execute("UPDATE chat_msg SET fseq=COALESCE(fseq, ?), fstate=NULL WHERE seq=? AND fstate IS NOT NULL RETURNING seq",
                         (d.get("fseq"), seq)).fetchone():
                chat._direct(c, local_members(c, ch["id"]), "chat.message", {"chat_id": ch["id"], "seq": seq, "id": m["id"], "sender": uid,
                                                                             "fseq": d.get("fseq")})
            out["fseq"] = d.get("fseq")
        elif status is None:
            out["pending"] = True
        elif status == 409 and d.get("error") == "recipients":
            c.execute("DELETE FROM chat_msg WHERE seq=?", (seq,))
            c.execute("UPDATE chat_file SET msg_seq=NULL WHERE msg_seq=?", (seq,))
            # schon gemeldet (deliver): wieder entfernen - die App versiegelt neu und sendet unter neuer Kennung
            chat._direct(c, local_members(c, ch["id"]), "chat.deleted", {"chat_id": ch["id"], "id": m["id"], "seq": seq})
            if ai_ref:
                c.execute("UPDATE chat_ai_reply SET msg_id=NULL WHERE id=?", (ai_ref,))
            return chat._no_store({"error": t("Die Mitglieder oder ihre Geräte haben sich geändert – bitte erneut senden."),
                                   "handles": handles, "fresh": True}, 409)
        else:
            c.execute("UPDATE chat_msg SET fstate='failed' WHERE seq=?", (seq,))
            chat._direct(c, [uid], "chat.message", {"chat_id": ch["id"], "seq": seq, "id": m["id"], "sender": uid, "failed": True})
            if status == 404:
                c.execute("UPDATE chat SET fstate='old', dm_key=NULL WHERE id=? AND fstate=''", (ch["id"],))
                chat._changed(c, ch["id"], [uid])
            return _bad(t("Nicht zugestellt: der andere Saga-Server hat die Nachricht abgewiesen ({err}).", err=str(d.get("error") or status)), 409)
    print(f"chat: Nachricht in Chat {ch['id']} (foederiert, an {('Warteschlange' if out.get('pending') else 'Heim')})", flush=True)
    return chat._no_store(out)


def delete_msg(uid, ch, mid):
    """Eigene Nachricht fuer alle loeschen: hier sofort, beim Heim (das es allen meldet) synchron bzw. ueber die Warteschlange."""
    with _db() as c, c:
        r = c.execute("SELECT sender_uid FROM chat_msg WHERE id=? AND chat_id=? AND kind='msg'", (mid, ch["id"])).fetchone()
        if not r:
            return _bad("not found", 404)
        if r[0] != uid:
            return _bad(t("Nur eigene Nachrichten lassen sich löschen."), 403)
        drop_msg(c, ch, mid)
        mh = my_handle(c, uid)
        if ch["home_peer"] is None:
            fanout(c, ch, {"type": "chat.msg.del", "mid": mid})
        else:
            F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "del", "ref": ch["ref"], "id": mid, "actor": mh}},
                       chain=chain_up(ch["ref"]))
    return chat._no_store({"ok": True})


def delete_all(uid, ch):
    """Direkt-Chat fuer beide loeschen: hier sofort; das Heim loescht ihn bei allen (Heim = wir: Ereignis an die anderen)."""
    with _db() as c, c:
        mh = my_handle(c, uid)
        by = {"username": uid, "name": sharing.names(c, [uid]).get(uid, uid)}
        if ch["home_peer"] is None:     # D01: an andere Server nie den (uebersetzten) Ersatz - Anzeigename oder Handle
            fanout(c, ch, {"type": "chat.removed", "by": {"handle": mh, "name": F.display_of(c, uid) or mh.split("@")[0]}})
        else:
            F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "drop", "ref": ch["ref"], "actor": mh}}, chain=chain_up(ch["ref"]))
        chat._drop_notifs(c, local_members(c, ch["id"]), ch["id"])
        removed_live(c, ch, by)
        drop_chat(c, ch)
    print(f"chat: foederierter Chat {ch['id']} von {uid} fuer alle geloescht", flush=True)
    return chat._no_store({"ok": True, "scope": "all", "removed": True})


def set_meta(uid, ch, want):
    """Frist ({retention_s}) bzw. KI ({ai}) fuer alle: beim Heim (synchron, sonst Warteschlange). -> (fehler oder None, wartet)"""
    with _db() as c:
        ro = _ro_error(c, ch)
        mh = my_handle(c, uid)
    if ro:
        return _bad(ro, 403), False
    if ch["home_peer"] is None:
        with _db() as c, c:
            ch2 = by_ref(c, ch["ref"], lock=True)
            if apply_meta(c, ch2, want, uid):
                fanout(c, ch2, dict({"type": "chat.meta", "by": mh}, **want))
        return None, False
    status, d = to_home(ch, dict({"op": "meta", "ref": ch["ref"], "actor": mh}, **want))
    if status is None:
        return None, True
    if status != 200:
        return _bad(t("Der andere Saga-Server hat die Änderung abgewiesen ({err}).", err=str((d or {}).get("error") or status)), 409), False
    with _db() as c, c:
        ch2 = by_ref(c, ch["ref"], lock=True)
        if ch2:
            apply_meta(c, ch2, want, uid)
    return None, False


def typing(uid, ch):
    with _db() as c:
        mh = my_handle(c, uid)
        ps = [ch["home_peer"]] if ch["home_peer"] is not None else [p["id"] for p in participants(c, ch)]
    for pid in ps:
        _volatile(pid, {"op": "typing", "ref": ch["ref"], "actor": mh})


def request_action(uid, cid, action, scope="person"):
    """Nachrichtenanfrage: accept | decline | block (scope person|instance). Ablehnen/Sperren erfaehrt die andere Seite nicht."""
    if action not in ("accept", "decline", "block") or scope not in ("person", "instance"):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if ch["v"] != 2 or ch["kind"] not in ("dm", "free"):
            return _bad(t("ungültige Angabe"))
        other = next((u for u in mem if sharing.is_remote(u)), None)
        if ch["kind"] == "free":
            # Gruppe (E5/E8): gesperrt wird genau, wer eingeladen hat (nie irgendein anderes Mitglied) - war das eine Person
            # dieses Servers, wird nur abgelehnt
            inv = inviter(c, ch, uid)
            other = inv if inv and sharing.is_remote(inv) else None
        mh = my_handle(c, uid)
        if action == "accept":
            return _reopen(c, uid, ch)
        targets = [handle_of(other)] if other else []
        if action == "block" and other:
            tg = handle_of(other) if scope == "person" else split_handle(handle_of(other))[1]
            c.execute("INSERT INTO fed_chat_block (uid, target, created) VALUES (?,?,?) ON CONFLICT DO NOTHING", (uid, tg, time.time()))
            if scope == "instance":                  # alle offenen Anfragen von dort gleich mit ablehnen
                host = split_handle(handle_of(other))[1]
                for (cid2, u2) in c.execute("SELECT m.chat_id, o.uid FROM chat_member m JOIN chat ch ON ch.id=m.chat_id JOIN chat_member o "
                                            "ON o.chat_id=m.chat_id AND o.uid LIKE '@%' WHERE m.uid=? AND m.state='request' AND ch.v=2",
                                            (uid,)).fetchall():
                    if split_handle(handle_of(u2))[1] == host and cid2 != ch["id"]:
                        _decline(c, uid, chat._load(c, cid2), mh)
            F.log(c, "chat.block", None, scope, host=split_handle(handle_of(other))[1])
        _decline(c, uid, ch, mh)
        return chat._no_store({"ok": True, "state": "declined", "blocked": action == "block" and bool(other), "targets": targets})


def inviter(c, ch, uid):
    """Wer uid in die Gruppe ch eingeladen hat (lokaler Benutzername bzw. Prinzipal) - aeltere Kopien ohne Angabe: die
    Person, die die Gruppe angelegt hat."""
    r = c.execute("SELECT by_uid FROM chat_invite WHERE chat_id=? AND uid=?", (ch["id"], uid)).fetchone()
    return r[0] if r else ch["created_by"]


def _set_inviter(c, ch, uid, by):
    if by and by != uid:
        c.execute("INSERT INTO chat_invite (chat_id, uid, by_uid) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE SET by_uid=EXCLUDED.by_uid",
                  (ch["id"], uid, by))


def _decline(c, uid, ch, mh):
    c.execute("UPDATE chat_member SET state='declined' WHERE chat_id=? AND uid=?", (ch["id"], uid))
    chat._drop_notifs(c, [uid], ch["id"])
    if ch["home_peer"] is not None:
        F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "decline", "ref": ch["ref"], "actor": mh}}, chain=chain_up(ch["ref"]))
    chat._direct(c, [uid], "chat.changed", {"chat_id": ch["id"]})


@router.get("/api/chat/blocks")
def api_blocks(request: Request):
    """Gesperrte Personen ('~x@host') und Server ('host')."""
    uid, err = chat._me(request)
    if err:
        return err
    with _db() as c:
        out = []
        for tg, at in c.execute("SELECT target, created FROM fed_chat_block WHERE uid=? ORDER BY created DESC", (uid,)).fetchall():
            if tg.startswith("~"):
                lab = label_of(c, principal(tg))
                out.append({"target": tg, "kind": "person", "name": lab["name"], "host": lab["host"], "label": lab["label"], "created": at})
            else:
                out.append({"target": tg, "kind": "instance", "name": tg, "host": tg, "label": tg, "created": at})
    return chat._no_store({"blocks": out})


@router.delete("/api/chat/blocks")
def api_unblock(request: Request, target: str = ""):
    uid, err = chat._me(request)
    if err:
        return err
    with _db() as c, c:
        n = c.execute("DELETE FROM fed_chat_block WHERE uid=? AND target=?", (uid, target.strip().lower())).rowcount
    return chat._no_store({"ok": True, "deleted": n})


@router.post("/api/chats/{cid}/request")
async def api_request(cid: int, request: Request):
    """Nachrichtenanfrage beantworten: {action: 'accept'|'decline'|'block', scope?: 'person'|'instance'}."""
    uid, err = chat._me(request)
    if err:
        return err
    b = await _body(request) or {}
    return await run_in_threadpool(request_action, uid, cid, b.get("action"), b.get("scope") or "person")


def recipients(c, uid, ch, mem):
    """GET /api/chats/{cid}/recipients bei foederierten Chats: Handles der Mitglieder (Schluessel: /api/chat/v2/keys)."""
    out = []
    for u in mem:
        h = member_handle(c, u)
        lab = label_of(c, u) if sharing.is_remote(u) else {"name": sharing.names(c, [u]).get(u, u), "host": F.SELF_HOST}
        out.append({"username": chat.person(c, u, uid, ch), "handle": h, "name": lab["name"], "host": lab["host"], "me": u == uid})
    return chat._no_store({"chat_id": ch["id"], "v": 2, "ref": ch["ref"], "handles": [x["handle"] for x in out], "recipients_v2": out})


def chat_extra(c, uid, ch, d, mem, m):
    """Zusatzfelder fuer foederierte Chats in /api/chats und /api/chats/{cid}."""
    hh = home_host(c, ch)
    ro = _ro_error(c, ch)
    v3 = ch["v"] == 3            # 2.5: lokaler Chat der Stufe 3 - Clients der Stufe 2 sehen ihn als v: 2 (gleiches Format)
    d.update(v=3 if v3 and chat.CHAT_LEVEL.get() >= 3 else 2, ref=ch["ref"], fed=not v3, home=hh, home_self=ch["home_peer"] is None,
             my_handle=my_handle(c, uid), state=(m or {}).get("state") or "ok", request=(m or {}).get("state") == "request",
             readonly=bool(ro), readonly_reason=ro,
             big_files=big_files_ok(c, ch))      # 2.4: grosse Anhaenge (saga-file/v2 ueber ATTACH_MAX_MB) moeglich (alle mit chatfile2)
    out = []
    av = sharing.names(c, mem)
    for x in d["members"]:
        u = x["username"]
        if sharing.is_remote(u):
            lab = label_of(c, u)
            x.update(name=lab["name"], label=lab["label"], host=lab["host"], handle=handle_of(u), remote=True)
        else:
            x.update(handle=my_handle(c, u), host=F.SELF_HOST)
            if u != uid:                            # 2.5: andere Konten nur als Handle (Endpunkte nehmen ihn an)
                x["username"] = x["handle"]
        out.append(x)
    d["members"] = out
    _title_v2(d, uid)
    if v3:
        _extra_v3(c, uid, ch, d, out)
        for fn in EXTRA_HOOKS:
            fn(c, ch, d)
        return d
    if ch["kind"] == "page" and ch["home_peer"] is not None:     # Kopie eines Seiten-Chats einer fremden Seite (E5)
        d.update(name=ch["group_name"] or t("Seite"), group=None)
    if ch["kind"] in ("free", "page"):
        d["delete_for_all"] = "chat" if ch["kind"] == "free" and (m or {}).get("role") == "admin" else None
        inv = inviter(c, ch, uid) if ch["kind"] == "free" else None
        if inv and (ch["home_peer"] is not None or (m or {}).get("state") == "request"):
            lab = label_of(c, inv) if sharing.is_remote(inv) else {"name": sharing.names(c, [inv]).get(inv) or inv, "host": F.SELF_HOST,
                                                                  "label": sharing.names(c, [inv]).get(inv) or inv}
            d["invited_by"] = {"name": lab["name"], "host": lab["host"], "label": lab["label"], "remote": sharing.is_remote(inv)}
    if ch["kind"] == "dm":
        o = next((x for x in out if x.get("remote")), None) or {}
        d["name"] = o.get("name") or d.get("name")
        d["peer"] = {"username": o.get("username"), "handle": o.get("handle"), "name": o.get("name") or "?", "label": o.get("label"),
                     "host": o.get("host"), "avatar": av.avatar(o.get("username")) if o.get("username") else None}
        d["delete_for_all"] = "chat"
    for fn in EXTRA_HOOKS:                          # 2.4 (saga.chat_status): receipts_ok / online_ok
        fn(c, ch, d)
    return d


EXTRA_HOOKS = []
DROP_HOOKS = []                    # 2.5: (c, ch, msg_id) nach dem Loeschen einer Nachricht (saga.chat_x3)


def _extra_v3(c, uid, ch, d, members_):
    """Stufe 3 (2.5): lokaler Chat ohne Personen anderer Server - Direkt-Chat mit dem Gegenueber als Handle, Name der freien
    Gruppe ohne v1-Absenderdaten. Loeschen "fuer alle" wie bei lokalen Chats (d bleibt aus chat._chat_dict)."""
    if ch["kind"] == "dm":
        o = next((x for x in members_ if not x.get("me")), None) or {}
        d["peer"] = {"username": o.get("username"), "handle": o.get("handle"), "name": o.get("name") or d.get("name") or "?",
                     "label": o.get("name") or d.get("name") or "?", "host": F.SELF_HOST, "avatar": o.get("avatar")}


def _title_v2(d, uid):
    """Name einer freien Gruppe im Format v2 (2.5): Absender als Handle, ohne v1-Geraetedaten (enthielten den Anmeldenamen)."""
    tm = d.get("title_msg")
    if tm and tm.get("sender_handle"):
        if tm.get("sender") and tm["sender"].get("username") != uid:
            tm["sender"] = dict(tm["sender"], username=tm["sender_handle"])
        tm.pop("device", None)
        tm.pop("ident_pub", None)


# =====================================================================================================
# E5: freie Gruppen und Seiten-Chats mit Mitgliedern mehrerer Server
# =====================================================================================================
# Heim einer freien Gruppe = Server der Person, die sie anlegt; Heim eines Seiten-Chats = Server der Seite. Das Heim fuehrt
# die Mitgliederliste (Ereignis 'chat.members' an alle Teilnehmer-Server, Kette 'chat:<id>', mit Systemzeile inkl. Herkunft).
# Mitglieder aendern Teilnehmer-Server ueber das Heim (Auftraege 'members' / 'leave'). Personen dritter Server: Schluessel und
# Anhaenge ueber das Heim (der Heimserver der Person ist dort verbunden), Zertifikate prueft jede App selbst (TOFU).
# Gruppen anderer Server in Seiten-Chats: deren Server meldet die Mitglieder (Auftrag 'resolve') - nur, wenn die Gruppe Ziel
# einer Freigabe dieses Servers ist; ebenso Handle zu einem oeffentlichen Namen nur fuer Personen mit Freigabe.
RESOLVE_TTL = 600                 # Mitglieder fremder Gruppen / Handles hoechstens so lange zwischenspeichern
RESOLVE_NEG_TTL = 30              # "kein Handle"/leere Gruppe nur kurz (die Freigabe ist dort evtl. noch nicht angekommen)
_res = {"pending": set(), "last": {}}


def groups_ok(p):
    """Gruppen- und Seiten-Chats mit Instanz p: Chat erlaubt (peer_ok) und beide melden 'chatgroups'."""
    return peer_ok(p) and F.feature(p, "chatgroups")


def _role(c, cid, u):
    r = c.execute("SELECT role FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (cid, u)).fetchone()
    return r[0] if r else None


def who(c, u):
    """Mitglied fuer andere Server und Systemzeilen: {handle, name, host} - nie der Anmeldename."""
    if not u:
        return None
    if sharing.is_remote(u):
        lab = label_of(c, u)
        return {"handle": handle_of(u), "name": lab["name"], "host": lab["host"]}
    h = my_handle(c, u)
    return {"handle": h, "name": F.display_of(c, u) or h.split("@")[0], "host": F.SELF_HOST}


def member_list(c, ch):
    out = []
    for u, st in members(c, ch["id"]):
        w = who(c, u)
        if w and w["handle"]:
            w["role"] = _role(c, ch["id"], u) or "member"
            out.append(w)
    return out


def _page_title(c, nid):
    r = c.execute("SELECT title FROM note WHERE id=?", (nid,)).fetchone()
    return F._clean_text(r[0] if r else "", 300)


def _hosts_of(us):
    return {split_handle(handle_of(u))[1] for u in us if sharing.is_remote(u) and handle_of(u)}


def push_members(c, ch, sysd=None, extra_hosts=()):
    """Heim: aktuelle Mitgliederliste an alle Teilnehmer-Server (auch an die von eben entfernten Mitgliedern)."""
    ev = {"type": "chat.members", "ref": ch["ref"], "kind": ch["kind"], "members": member_list(c, ch)}
    if ch["kind"] == "page":
        ev.update(note=ch["note_id"], title=_page_title(c, ch["note_id"]))
    if sysd:
        ev["sys"] = sysd
    hosts = _hosts_of([u for u, _ in members(c, ch["id"])]) | set(extra_hosts)
    for host in sorted(h for h in hosts if h):
        p = F.peer_by(c, "host", host)
        if p and p["status"] == "active" and F.feature(p, "chatgroups"):
            F._enqueue(c, p["id"], dict(ev), chain=chain_down(ch["ref"]))


def members_changed(c, ch, added, removed, by=None, reason=None):
    """Heim (aus chat.sync bzw. den Gruppen-Auftraegen): Systemzeile mit Herkunft + neue Liste an die anderen Server."""
    if ch["home_peer"] is not None or ch.get("v") != 2 or not (added or removed):
        return
    sysd = {"type": "members", "added": [x for x in (who(c, u) for u in added) if x], "removed": [x for x in (who(c, u) for u in removed) if x],
            "by": who(c, by)}
    if reason:
        sysd["reason"] = reason
    sysline(c, ch, by, sysd)
    push_members(c, ch, sysd, extra_hosts=_hosts_of(removed))


def upgrade_page_chat(c, ch):
    """Seiten-Chat bekommt Personen anderer Server: ab jetzt saga-chat/v2 mit globaler Kennung (aeltere Nachrichten bleiben
    v1 - ohne sender_handle - und fuer die Mitglieder hier lesbar). KI in Chats ueber Servergrenzen standardmaessig aus."""
    if ch["kind"] != "page" or ch.get("v") == 2 or ch.get("home_peer") is not None:
        return
    ref = ch.get("ref") if ch.get("v") == 3 else None    # 2.5: Stufe 3 hat schon das Format v2 und eine chat_ref
    ref = ref or secrets.token_hex(16) + "@" + F.SELF_HOST
    # 2.5: Umstellpunkt merken - Nachrichten bis dahin sind v1 (Altbestand mit Anmeldenamen, siehe core.knows)
    c.execute("UPDATE chat SET v=2, ref=?, v3_seq=CASE WHEN v=1 AND last_seq>0 THEN last_seq ELSE v3_seq END, "
              "v3_at=CASE WHEN v=1 AND last_seq>0 THEN ? ELSE v3_at END WHERE id=?", (ref, time.time(), ch["id"]))
    ch.update(v=2, ref=ref)
    had = c.execute("DELETE FROM chat_ai WHERE chat_id=?", (ch["id"],)).rowcount
    sysline(c, ch, None, {"type": "fed", "on": True, **({"ai_off": True} if had else {})})
    F.log(c, "chat.upgraded", None, f"chat={ch['id']}", host=F.SELF_HOST)


def _schedule_resolve(c, nid):
    if nid in _res["pending"] or time.monotonic() - _res["last"].get(nid, -1e9) < 30:
        return
    c.after_commit(lambda: _resolve_bg(nid))         # (ohne Commit, z. B. reine Lese-Verbindung: nichts vormerken)


def _resolve_bg(nid):
    if nid in _res["pending"]:
        return
    _res["pending"].add(nid)
    _submit(_resolve_run, nid)


def _resolve_run(nid):
    try:
        resolve_page(nid)
        with _db() as c, c:
            ch = chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE kind='page' AND note_id=? AND home_peer IS NULL", (nid,)).fetchone())
            if ch:
                chat.sync(c, ch)
    except Exception as ex:  # noqa: BLE001 - Hintergrund: beim naechsten Zugriff erneut
        print(f"fed_chat: Seiten-Chat {nid} aufloesen: {type(ex).__name__}: {ex}", flush=True)
    finally:
        _res["pending"].discard(nid)
        _res["last"][nid] = time.monotonic()


def _remote_targets(c, nid):
    """Freigaben der Kette an Personen/Gruppen anderer Server -> [(typ, peer, name)] (nur Server mit Gruppen-Chats)."""
    r = c.execute("SELECT uid FROM note WHERE id=?", (nid,)).fetchone()
    if not r or sharing.is_remote(r[0]):
        return []
    chain = chat.note_chain(c, r[0], nid)
    if not chain:
        return []
    out = []
    for tt, tg in c.execute(f"SELECT DISTINCT target_type, target FROM note_share WHERE owner_uid=? AND target_type IN ('remote','remote_group') "
                            f"AND note_id IN ({','.join('?' * len(chain))})", (r[0], *chain)).fetchall():
        p, name = F._target_peer(c, tg)
        if p and groups_ok(p):
            out.append((tt, p, name))
    return out


def page_remote(c, ch):
    """chat.entitled: Personen anderer Server mit Zugriff auf die Seite (Heim) - aus dem Zwischenspeicher; Fehlendes oder
    Veraltetes wird im Hintergrund aufgeloest (danach erneuter Abgleich)."""
    if not on() or ch["kind"] != "page" or ch.get("home_peer") is not None:
        return set()
    out, stale, now = set(), False, time.time()
    for tt, p, name in _remote_targets(c, ch["note_id"]):
        if tt == "remote":
            if F.PSEUDO_RE.fullmatch(name):
                out.add(principal(f"{name}@{p['host']}"))
                continue
            r = c.execute("SELECT handle, fetched FROM fed_name_handle WHERE principal=?", (f"@{name}@{p['host']}",)).fetchone()
            if r and r[0]:
                out.add(principal(f"{r[0]}@{p['host']}"))
            stale = stale or not r or now - r[1] > (RESOLVE_TTL if r[0] else RESOLVE_NEG_TTL)
        else:
            r = c.execute("SELECT members, fetched FROM fed_group_members WHERE peer_id=? AND grp=?", (p["id"], name)).fetchone()
            if r:
                for m in F._jl(r[0], list):
                    if isinstance(m, dict) and isinstance(m.get("handle"), str) and F.PSEUDO_RE.fullmatch(m["handle"]):
                        out.add(principal(f"{m['handle']}@{p['host']}"))
            stale = stale or not r or now - r[1] > (RESOLVE_TTL if F._jl(r[0], list) else RESOLVE_NEG_TTL)
    if stale and hasattr(c, "after_commit"):
        _schedule_resolve(c, ch["note_id"])
    return out


def resolve_page(nid, only_stale=False):
    """Personen (oeffentliche Namen -> Handles) und Gruppen anderer Server einer Seite bei deren Servern aufloesen (Netzwerk)."""
    now = time.time()
    with _db() as c:
        by = {}
        for tt, p, name in _remote_targets(c, nid):
            if tt == "remote" and F.PSEUDO_RE.fullmatch(name):
                continue
            if only_stale:
                if tt == "remote":
                    r = c.execute("SELECT handle, fetched FROM fed_name_handle WHERE principal=?", (f"@{name}@{p['host']}",)).fetchone()
                    pos = bool(r and r[0])
                else:
                    r = c.execute("SELECT members, fetched FROM fed_group_members WHERE peer_id=? AND grp=?", (p["id"], name)).fetchone()
                    pos = bool(r and F._jl(r[0], list))
                if r and now - r[1] <= (RESOLVE_TTL if pos else RESOLVE_NEG_TTL):
                    continue
            d = by.setdefault(p["id"], {"p": p, "names": set(), "groups": set()})
            d["names" if tt == "remote" else "groups"].add(name)
    for pid, d in by.items():
        p = d["p"]
        status, out = sync_call(p, {"op": "resolve", "names": sorted(d["names"])[:50], "groups": sorted(d["groups"])[:20]})
        if status != 200 or not isinstance(out, dict):
            continue
        with _db() as c, c:
            for n in d["names"]:
                e = (out.get("names") or {}).get(n) if isinstance(out.get("names"), dict) else None
                h = e.get("handle") if isinstance(e, dict) and isinstance(e.get("handle"), str) and F.PSEUDO_RE.fullmatch(e["handle"]) else None
                c.execute("INSERT INTO fed_name_handle (principal, peer_id, handle, name, fetched) VALUES (?,?,?,?,?) ON CONFLICT (principal) DO UPDATE "
                          "SET peer_id=EXCLUDED.peer_id, handle=EXCLUDED.handle, name=EXCLUDED.name, fetched=EXCLUDED.fetched",
                          (f"@{n}@{p['host']}", pid, h, F.remote_name((e or {}).get("name")) if isinstance(e, dict) else "", now))
                if h:
                    _person(c, p, f"{h}@{p['host']}", F.remote_name(e.get("name")))
            for g in d["groups"]:
                lst = (out.get("groups") or {}).get(g) if isinstance(out.get("groups"), dict) else None
                mem = []
                for m in (lst if isinstance(lst, list) else [])[:chat.MEMBERS_MAX]:
                    if isinstance(m, dict) and isinstance(m.get("handle"), str) and F.PSEUDO_RE.fullmatch(m["handle"]):
                        mem.append({"handle": m["handle"], "name": F.remote_name(m.get("name"))})
                        _person(c, p, f"{m['handle']}@{p['host']}", F.remote_name(m.get("name")))
                c.execute("INSERT INTO fed_group_members (peer_id, grp, members, fetched) VALUES (?,?,?,?) ON CONFLICT (peer_id, grp) DO UPDATE "
                          "SET members=EXCLUDED.members, fetched=EXCLUDED.fetched", (pid, g, json.dumps(mem), now))


def _share_changed(c, owner, note_id, target_type, target, **_):
    """Freigabe an eine Person/Gruppe eines anderen Servers gesetzt oder geaendert: deren Aufloesung neu holen (ein frueher
    gemerktes "kein Handle" kann von vor der Ankunft der Freigabe dort stammen)."""
    if target_type not in ("remote", "remote_group") or not isinstance(target, str):
        return
    if target_type == "remote":
        c.execute("DELETE FROM fed_name_handle WHERE principal=?", (target,))
    else:
        name, _, host = target.rpartition("@")
        p = F.peer_by(c, "host", host) if name and host else None
        if p:
            c.execute("DELETE FROM fed_group_members WHERE peer_id=? AND grp=?", (p["id"], name))
    _res["last"].pop(note_id, None)


bus.on("share.set", _share_changed)


def prepare_page(nid):
    """Vor dem Oeffnen/Anlegen eines Seiten-Chats (ausserhalb jeder Transaktion): Veraltetes aufloesen."""
    if on():
        try:
            resolve_page(nid, only_stale=True)
        except Exception as ex:  # noqa: BLE001 - dann eben mit dem bisherigen Stand
            print(f"fed_chat: Seiten-Chat {nid} vorbereiten: {type(ex).__name__}: {ex}", flush=True)


def chat_dropping(c, ch):
    """Seiten-Chat ueber Servergrenzen wird geloescht (Seite endgueltig weg, Konto geloescht): die anderen Server auch."""
    if ch and chat.is_fed(ch) and ch["home_peer"] is None:
        fanout(c, ch, {"type": "chat.removed", "by": None})


# --- Teilnehmer-Server: Mitgliederliste vom Heim -----------------------------------------------------------------------
def apply_members(c, peer, ev, create=True):
    """chat.members vom Heim uebernehmen (Teilnehmer-Server): Kopie anlegen (wenn eigene Konten dabei sind), Mitglieder und
    Rollen angleichen, Systemzeile. -> Chat oder None."""
    ref = ev.get("ref")
    hx, host = split_ref(ref)
    kind = ev.get("kind")
    lst = ev.get("members")
    if hx and host != peer["host"]:
        F.log(c, "chat.forged", peer, f"members: not home of {hx[:8]}", throttle=(peer["id"], "nothome-m"))
        return None
    if not hx or kind not in ("free", "page") or not isinstance(lst, list) or len(lst) > chat.MEMBERS_MAX + 5:
        return None
    want, roles, mine = {}, {}, set()
    for m in lst:
        if not isinstance(m, dict):
            continue
        n, h_host = split_handle(m.get("handle"))
        if not n:
            continue
        if h_host == F.SELF_HOST:
            u = F.uid_by_handle(c, n)
            if not u:
                continue
            mine.add(u)
        else:
            u = principal(f"{n}@{h_host}")
            nm = F.remote_name(m.get("name"))
            if h_host == peer["host"] or not c.execute("SELECT 1 FROM fed_person WHERE principal=?", (u,)).fetchone():
                F._person_upsert(c, u, peer["id"], nm or n)
        want[u] = m
        roles[u] = "admin" if m.get("role") == "admin" else "member"
    ch = by_ref(c, ref, lock=True)
    if ch and ch["home_peer"] != peer["id"]:
        F.log(c, "chat.forged", peer, "members: not home", throttle=(peer["id"], "nothome"))
        return None
    now = time.time()
    if not ch:
        if not mine or not create:
            return None
        if not _rate_ok("cgrp", peer["host"], REQ_RATE_PEER_H, 3600):
            F.log(c, "chat.rate", peer, "group", throttle=(peer["id"], "cgrp"))
            return None
        by = (ev.get("sys") or {}).get("by") if isinstance(ev.get("sys"), dict) else None
        bn, bh = split_handle((by or {}).get("handle") if isinstance(by, dict) else None)
        creator = principal(f"{bn}@{bh}") if bn and bh != F.SELF_HOST else None
        title = F._clean_text(ev.get("title"), 300) if kind == "page" else None
        note = F.ref(peer["id"], ev["note"]) if kind == "page" and F._ok_rid(ev.get("note")) else None
        cid = c.execute("INSERT INTO chat (kind, note_id, group_name, created_by, created, v, ref, home_peer) VALUES (?,?,?,?,?,2,?,?)",
                        (kind, note, title, creator, now, ref, peer["id"])).lastrowid
        ch = chat._load(c, cid)
        F.log(c, "chat.group", peer, f"chat={cid} {kind}")
    elif kind == "page" and isinstance(ev.get("title"), str):
        c.execute("UPDATE chat SET group_name=? WHERE id=?", (F._clean_text(ev["title"], 300), ch["id"]))
    cur = dict(members(c, ch["id"]))
    sby = ev["sys"].get("by") if isinstance(ev.get("sys"), dict) and isinstance(ev["sys"].get("by"), dict) else {}
    ibn, ibh = split_handle(sby.get("handle"))
    inv = (F.uid_by_handle(c, ibn) if ibh == F.SELF_HOST else principal(f"{ibn}@{ibh}")) if ibn else None
    if inv and not sharing.is_remote(inv) and cur.get(inv) != "ok":
        inv = None                           # "eingeladen von einer Person hier" nur, wenn sie wirklich Mitglied ist (sonst
                                             # koennte das Heim so das Sperren verhindern) - sonst wie bisher die Erstellerin
    for u in want:
        if u in cur:
            c.execute("UPDATE chat_member SET role=? WHERE chat_id=? AND uid=?", (roles[u], ch["id"], u))
            continue
        st = "ok"
        if not sharing.is_remote(u) and kind == "free":
            hs = [handle_of(x) for x in (ch["created_by"], inv) if x and sharing.is_remote(x)]
            st = "declined" if any(blocked(c, u, h) for h in hs) else "request"     # Erstkontakt: Nachrichtenanfrage (still)
            _set_inviter(c, ch, u, inv or ch["created_by"])
        elif not sharing.is_remote(u) and not _inbound(c, peer, u):
            st = "request"                       # Seiten-Chat ohne Freigabe dieses Servers an die Person: nur als stille Anfrage
        c.execute("INSERT INTO chat_member (chat_id, uid, role, joined, last_read, state) VALUES (?,?,?,?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE "
                  "SET left_at=NULL, role=EXCLUDED.role, joined=EXCLUDED.joined, last_read=EXCLUDED.last_read, state=EXCLUDED.state",
                  (ch["id"], u, roles[u], now, ch["last_seq"], st))
        if st == "request":
            chat._direct(c, [u], "chat.request", {"chat_id": ch["id"]})
        elif st == "declined":
            F._enqueue(c, peer["id"], {"type": "chat.op", "op_body": {"op": "decline", "ref": ref, "actor": my_handle(c, u)}}, chain=chain_up(ref))
    for u in cur:
        if u not in want:
            c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid=?", (now, ch["id"], u))
    if isinstance(ev.get("sys"), dict):
        sd = ev["sys"]
        clean = {"type": "members", "added": _sys_people(sd.get("added")), "removed": _sys_people(sd.get("removed")),
                 "by": (_sys_people([sd.get("by")]) or [None])[0]}
        if sd.get("reason") in ("disconnected", "gone", "access"):
            clean["reason"] = sd["reason"]
        sysline(c, ch, None, clean)
    left = [u for u in local_members(c, ch["id"])]
    chat._changed(c, ch["id"], set(left) | {u for u in cur if not sharing.is_remote(u)})
    if not left:                                           # keine eigenen Konten mehr dabei: Kopie weg
        drop_chat(c, ch)
        return None
    return chat._load(c, ch["id"])


def _inbound(c, peer, u):
    """Hat die Instanz peer etwas mit u (oder einer Gruppe von u) geteilt?"""
    gs = sorted(sharing.groups_of(c, u))
    sql = "SELECT 1 FROM fed_inbound WHERE peer_id=? AND ((target_type='user' AND target=?)" + \
          (f" OR (target_type='group' AND target IN ({','.join('?' * len(gs))}))" if gs else "") + ") LIMIT 1"
    return bool(c.execute(sql, (peer["id"], u, *gs)).fetchone())


def _sys_people(lst):
    out = []
    for x in (lst if isinstance(lst, list) else [])[:chat.MEMBERS_MAX]:
        if isinstance(x, dict):
            h = x.get("handle") if isinstance(x.get("handle"), str) and _H.fullmatch(x["handle"]) else None
            # Herkunft: aus dem Handle (nicht frei waehlbar), sonst die Angabe des Heims (bereinigt)
            out.append({"name": F.remote_name(x.get("name")), "host": split_handle(h)[1] if h else F._clean_text(x.get("host"), 260),
                        "handle": h})
    return out


def _ev_members(c, peer, ev):
    if not groups_ok(peer):
        return "ignored"
    if not _rate_ok("cmemin", peer["host"], MEMBERS_RATE):
        _log_retry(c, peer, "chat.rate", "members in", (peer["id"], "cmemin"))
        return "retry"
    apply_members(c, peer, ev)
    return "ok"


F.EV2_HANDLERS["chat.members"] = _ev_members


# --- Heim: Auftraege der Teilnehmer-Server -----------------------------------------------------------------------------
def _add_member(c, ch, u, role="member"):
    c.execute("INSERT INTO chat_member (chat_id, uid, role, joined, last_read, state) VALUES (?,?,?,?,?,'ok') ON CONFLICT (chat_id, uid) DO UPDATE "
              "SET left_at=NULL, role=EXCLUDED.role, joined=EXCLUDED.joined, last_read=EXCLUDED.last_read, state='ok'",
              (ch["id"], u, role, time.time(), ch["last_seq"]))


def _remove_member(c, ch, u):
    c.execute("UPDATE chat_member SET left_at=?, role='member' WHERE chat_id=? AND uid=?", (time.time(), ch["id"], u))
    rest = [x for x, _ in members(c, ch["id"])]
    if rest and not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND left_at IS NULL AND role='admin'", (ch["id"],)).fetchone():
        c.execute("UPDATE chat_member SET role='admin' WHERE chat_id=? AND uid=?", (ch["id"], rest[0]))
    return rest


def _member_of(c, h):
    """Handle ('~x@host') -> Mitglied hier (lokaler Benutzername bzw. Prinzipal) oder None (unbekannt/nicht verbunden)."""
    n, host = split_handle(h)
    if not n:
        return None
    if host == F.SELF_HOST:
        return F.uid_by_handle(c, n)
    p = F.peer_by(c, "host", host)
    return principal(f"{n}@{host}") if p and groups_ok(p) else None


def op_members(c, peer, d):
    """Teilnehmer -> Heim: Mitglieder einer freien Gruppe hinzufuegen/entfernen (nur Admins der Gruppe)."""
    ch = _home_chat(c, d, lock=True)
    h, p_ = _actor(c, peer, d)
    if not ch or not h or ch["kind"] != "free":
        return 404, {"error": "gone"}
    if ch["fstate"]:
        return 409, {"error": "readonly"}
    if _role(c, ch["id"], p_) != "admin":
        return 403, {"error": "not admin"}
    added, removed, cur = [], [], dict(members(c, ch["id"]))
    for m in (d.get("add") or [])[:chat.MEMBERS_MAX]:
        u = _member_of(c, m.get("handle")) if isinstance(m, dict) else None
        if not u:
            return 409, {"error": "unknown member"}
        if u not in cur:
            if not sharing.is_remote(u) and blocked(c, u, h):
                return 403, {"error": "blocked"}
            added.append(u)
            if sharing.is_remote(u):
                tp = F.peer_by(c, "host", split_handle(handle_of(u))[1])
                _person(c, tp, handle_of(u), F.remote_name(m.get("name")))
    for x in (d.get("remove") or [])[:chat.MEMBERS_MAX]:
        u = _member_of(c, x)
        if u and u in cur:
            removed.append(u)
    if len(cur) + len(added) - len(removed) > chat.MEMBERS_MAX:
        return 409, {"error": "too many"}
    for u in added:
        _add_member(c, ch, u)
        if not sharing.is_remote(u):         # Konto hier, eingeladen von einer Person eines anderen Servers: Erstkontakt = Anfrage
            c.execute("UPDATE chat_member SET state='request' WHERE chat_id=? AND uid=?", (ch["id"], u))
            _set_inviter(c, ch, u, p_)
            chat._direct(c, [u], "chat.request", {"chat_id": ch["id"]})
    for u in removed:
        _remove_member(c, ch, u)
    members_changed(c, ch, added, removed, by=p_)
    chat._changed(c, ch["id"], local_members(c, ch["id"]))
    return 200, {"ok": True}


def op_leave(c, peer, d):
    """Teilnehmer -> Heim: Person verlaesst die Gruppe."""
    ch = _home_chat(c, d, lock=True)
    h, p_ = _actor(c, peer, d)
    if not ch or not h or ch["kind"] != "free":
        return 200, {"ok": True}
    if p_ not in dict(members(c, ch["id"])):
        return 200, {"ok": True}
    rest = _remove_member(c, ch, p_)
    if not rest:
        drop_chat(c, ch)
        return 200, {"ok": True}
    members_changed(c, ch, [], [p_], by=p_)
    chat._changed(c, ch["id"], local_members(c, ch["id"]))
    return 200, {"ok": True}


def op_resolve(c, peer, d):
    """Heim eines Seiten-Chats -> wir: Handles zu oeffentlichen Namen und Mitglieder von Gruppen - nur fuer Personen bzw.
    Gruppen, mit denen diese Instanz etwas geteilt hat (Ziel einer Freigabe)."""
    names, groups = d.get("names") or [], d.get("groups") or []
    if not isinstance(names, list) or not isinstance(groups, list) or len(names) > 50 or len(groups) > 20:
        return 400, {"error": "bad request"}
    if not _rate_ok("cres", peer["host"], RESOLVE_RATE):
        F.log(c, "chat.rate", peer, "resolve", throttle=(peer["id"], "cres"))
        return 429, {"error": "too many requests"}
    out_n, out_g = {}, {}
    for n in names:
        if not isinstance(n, str) or not F.is_name(n):
            continue
        u = F._local_user_by_name(c, n)[0]
        if u and c.execute("SELECT 1 FROM fed_inbound WHERE peer_id=? AND target_type='user' AND target=? LIMIT 1", (peer["id"], u)).fetchone():
            out_n[n] = {"handle": F.own_handle(c, u), "name": F.display_of(c, u) or ""}
        else:
            out_n[n] = None
    for g in groups:
        if not isinstance(g, str) or auth.norm_group(g) != g:
            continue
        if not c.execute("SELECT 1 FROM fed_inbound WHERE peer_id=? AND target_type='group' AND target=? LIMIT 1", (peer["id"], g)).fetchone():
            out_g[g] = None
            continue
        mem = []
        for u in notify.group_members(c, g, chat.MEMBERS_MAX + 1)[:chat.MEMBERS_MAX]:
            if c.execute("SELECT 1 FROM users WHERE username=? AND disabled=0", (u,)).fetchone():
                mem.append({"handle": F.own_handle(c, u), "name": F.display_of(c, u) or ""})
        out_g[g] = mem
    F.log(c, "chat.resolve", peer, f"names={len(out_n)} groups={len(out_g)}")
    return 200, {"names": out_n, "groups": out_g}


def op_pagechat(c, peer, d):
    """Heim der lesenden Person -> wir (Server der Seite): Seiten-Chat oeffnen. Rolle wie beim Lesen der Seite (Prinzipal
    mit oeffentlichem Namen bzw. Handle, Gruppen signiert bestaetigt). -> Kennung + Mitgliederliste."""
    rid = d.get("note")
    h, p_ = _actor(c, peer, d)
    pub = d.get("pub") if isinstance(d.get("pub"), str) and F.is_name(d.get("pub")) else None
    groups = d.get("groups") if isinstance(d.get("groups"), list) else []
    if not h or not F._ok_rid(rid) or len(groups) > 50 or any(not isinstance(g, str) or auth.norm_group(g) != g for g in groups):
        return 400, {"error": "bad request"}
    acc_p = f"@{pub}@{peer['host']}" if pub else p_
    tok = sharing.REMOTE_GROUPS.set({acc_p: frozenset(f"{g}@{peer['host']}" for g in groups)})
    try:
        role = sharing.Access(c, acc_p).role(rid)
    finally:
        sharing.REMOTE_GROUPS.reset(tok)
    if not role or not c.execute("SELECT 1 FROM note WHERE id=? AND deleted_at IS NULL", (rid,)).fetchone():
        return 404, {"error": "not found"}
    now = time.time()
    if pub:
        c.execute("INSERT INTO fed_name_handle (principal, peer_id, handle, name, fetched) VALUES (?,?,?,'',?) ON CONFLICT (principal) DO UPDATE "
                  "SET handle=EXCLUDED.handle, fetched=EXCLUDED.fetched", (acc_p, peer["id"], h.split("@")[0], now))
    for g in groups:                     # ueber eine Gruppe berechtigt: in die zwischengespeicherte Mitgliederliste aufnehmen
        r = c.execute("SELECT members FROM fed_group_members WHERE peer_id=? AND grp=?", (peer["id"], g)).fetchone()
        if r is not None or c.execute("SELECT 1 FROM note_share WHERE target_type='remote_group' AND target=? LIMIT 1", (f"{g}@{peer['host']}",)).fetchone():
            lst = F._jl(r[0], list) if r else []
            if not any(isinstance(m, dict) and m.get("handle") == h.split("@")[0] for m in lst) and len(lst) < chat.MEMBERS_MAX:
                lst.append({"handle": h.split("@")[0], "name": ""})
            c.execute("INSERT INTO fed_group_members (peer_id, grp, members, fetched) VALUES (?,?,?,?) ON CONFLICT (peer_id, grp) DO UPDATE "
                      "SET members=EXCLUDED.members", (peer["id"], g, json.dumps(lst), now if r is None else 0))
    ch = chat._row(c.execute(f"SELECT {chat.C_COLS} FROM chat WHERE kind='page' AND note_id=? FOR UPDATE", (rid,)).fetchone())
    if not ch:
        cid = c.execute("INSERT INTO chat (kind, note_id, created_by, created, v, ref) VALUES ('page',?,NULL,?,2,?)",
                        (rid, now, secrets.token_hex(16) + "@" + F.SELF_HOST)).lastrowid
        ch = chat._load(c, cid)
    mem = chat.sync(c, ch)
    ch = chat._load(c, ch["id"])
    if p_ not in mem or not chat.is_fed(ch):
        return 403, {"error": "not a member"}
    return 200, {"ref": ch["ref"], "kind": "page", "note": rid, "title": _page_title(c, rid), "members": member_list(c, ch)}


OPS.update({"members": op_members, "leave": op_leave, "resolve": op_resolve, "pagechat": op_pagechat})


def keys_of_members(c, ch, peer, handles):
    """Heim: Schluessel von Mitgliedern fuer einen Teilnehmer-Server (Personen dritter Server - der Teilnehmer ist mit deren
    Server evtl. nicht verbunden). Nur Mitglieder dieses Chats; Eintraege aus dem Zwischenspeicher bzw. eigene."""
    mem = {member_handle(c, u) for u, _ in members(c, ch["id"])}
    out = {}
    for h in handles:
        if h in mem:
            out[h] = local_entry(c, h) if split_handle(h)[1] == F.SELF_HOST else _cached(c, h)
    return out


@router.get("/fed/v2/chat/keys")
async def fed_chat_keys(request: Request, ref: str = "", handles: str = ""):
    inp, err = await F._verify(request)
    if err is not None:
        return err
    if not groups_ok(inp.peer):
        return F._signed(inp, 403, {"error": "chat disabled"})
    want = [h for h in dict.fromkeys(x.strip() for x in handles.split(",")) if _H.fullmatch(h)][:20]

    def run():
        with _db() as c:
            ch = _home_chat(c, {"ref": ref})
            if not ch or not any(sharing.is_remote(u) and split_handle(handle_of(u))[1] == inp.peer["host"] for u, _ in members(c, ch["id"])):
                return 404, {"error": "gone"}
            miss = [h for h in want if split_handle(h)[1] not in (F.SELF_HOST, inp.peer["host"]) and not _cached(c, h)]
        if miss:
            entries(miss)                      # Heim holt fehlende beim Server der Person (Zwischenspeicher)
        with _db() as c:
            return 200, {"keys": keys_of_members(c, ch, inp.peer, want)}
    status, out = await run_in_threadpool(run)
    return F._signed(inp, status, out)


def keys_via_home(uid, handles, fresh=False):
    """Schluessel von Personen eines Servers, mit dem wir nicht verbunden sind: ueber das Heim eines gemeinsamen Chats (E5).
    -> {handle: eintrag|None} (Eintraege selbst geprueft, zwischengespeichert mit dem Heim als Quelle)."""
    out = {h: None for h in handles}
    with _db() as c:
        rows = c.execute("SELECT DISTINCT ch.ref, ch.home_peer FROM chat ch JOIN chat_member m ON m.chat_id=ch.id AND m.uid=? AND m.left_at IS NULL "
                         "WHERE ch.v=2 AND ch.home_peer IS NOT NULL AND ch.fstate=''", (uid,)).fetchall()
        cands = []
        for ref, hp in rows:
            ch = by_ref(c, ref)
            mem = {u for u, _ in members(c, ch["id"])}
            hs = [h for h in handles if principal(h) in mem]
            if hs:
                cands.append((F.peer_by(c, "id", hp), ref, hs))
        cache = {h: _cached(c, h) for h in handles}
    for p, ref, hs in cands:
        hs = [h for h in hs if out[h] is None]
        if not hs:
            continue
        if not fresh and all(cache.get(h) for h in hs):
            out.update({h: dict(cache[h], source="cache") for h in hs})
            continue
        if not groups_ok(p):
            continue
        try:
            status, d = F.call_json(p, "GET", "/fed/v2/chat/keys", f"ref={quote(ref, safe='')}&handles=" + quote(",".join(hs), safe=","), timeout=6)
        except F.FedError:
            out.update({h: dict(cache[h], source="cache", stale=True) for h in hs if cache.get(h)})
            continue
        if status != 200 or not isinstance(d, dict) or not isinstance(d.get("keys"), dict):
            continue
        with _db() as c, c:
            for h in hs:
                e = FK.check_entry(h, d["keys"].get(h))
                if e:
                    _cache_entry(c, p, h, e)
                    out[h] = dict(e, fetched=time.time(), source="home")
    return out


# --- Fuer die eigenen Konten: Gruppen anlegen, Mitglieder, Seiten-Chats fremder Seiten -----------------------------
def _ro_group(c, ch):
    if ch["fstate"]:
        return t("Diese Unterhaltung ist schreibgeschützt – die Verbindung zum anderen Saga-Server wurde getrennt.")
    if ch["home_peer"] is not None and not groups_ok(F.peer_by(c, "id", ch["home_peer"])):
        return t("Chats mit {host} sind gerade nicht möglich (vom Admin abgeschaltet oder Verbindung getrennt).", host=home_host(c, ch) or "?")
    return None


def create_group(uid, b):
    """POST /api/chats {kind:'free', members:[benutzer], handles:['~x@host'], device} - freie Gruppe mit Personen anderer
    Server. Unser Server wird das Heim; bei den anderen erscheint die Gruppe als Nachrichtenanfrage."""
    if not on():
        return _bad(t("ungültige Angabe"))
    raw_l, raw_h = b.get("members") or [], b.get("handles") or []
    if not isinstance(raw_l, list) or not isinstance(raw_h, list) or len(raw_l) + len(raw_h) > chat.MEMBERS_MAX:
        return _bad(t("Höchstens {n} Mitglieder je Chat.", n=chat.MEMBERS_MAX), 409)
    now = time.time()
    with _db() as c, c:
        # 2.4: Handle ('~…') aus der Personensuche; Benutzername nur, wenn bekannt bzw. erlaubt (sonst wie unbekannt)
        loc = sorted({resolve_input(c, uid, str(x)[:100]) or "~" for x in raw_l} - {uid})
        rem = []
        for h in dict.fromkeys(str(x).strip().lower() for x in raw_h):
            n, host = split_handle(h)
            if not n:
                return _bad(t("ungültige Angabe"))
            if host == F.SELF_HOST:
                u = F.uid_by_handle(c, n)
                if not u:
                    return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
                if u != uid:
                    loc.append(u)
                continue
            p = F.peer_by(c, "host", host)
            if not groups_ok(p):
                return _bad(t("Mit Personen auf {host} sind Gruppen-Chats nicht möglich.", host=host), 403)
            if blocked(c, uid, f"{n}@{host}"):
                return _bad(t("Du hast diese Person bzw. ihren Server gesperrt – erst in „Chats & Geräte“ entsperren."), 403)
            rem.append((p, f"{n}@{host}"))
        loc = sorted(set(loc))
        bad = [u for u in loc if not chat.may_contact(c, uid, u)]
        if bad or not (loc or rem):
            return _bad(t("Mit diesem Konto ist kein Chat möglich.") if bad else t("ungültige Angabe"), 400)
        if len(loc) + len(rem) + 1 > chat.MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=chat.MEMBERS_MAX), 409)
        if c.execute("SELECT COUNT(*) FROM chat WHERE created_by=? AND created>?", (uid, now - 3600)).fetchone()[0] >= 60:
            return _bad(t("Zu viele neue Chats in kurzer Zeit – bitte kurz warten."), 429)
        ref = secrets.token_hex(16) + "@" + F.SELF_HOST
        cid = c.execute("INSERT INTO chat (kind, created_by, created, v, ref) VALUES ('free',?,?,2,?)", (uid, now, ref)).lastrowid
        ch = chat._load(c, cid)
        _add_member(c, ch, uid, "admin")
        added = []
        for u in loc:
            _add_member(c, ch, u)
            added.append(u)
        for p, h in rem:
            _person(c, p, h)
            _add_member(c, ch, principal(h))
            added.append(principal(h))
        members_changed(c, ch, added, [], by=uid)
        chat._changed(c, cid, [uid] + loc)
        F.log(c, "chat.group", None, f"chat={cid} remote={len(rem)}", host=F.SELF_HOST)
        mem = chat.members(c, cid)
        return chat._no_store({"chat": chat._chat_dict(c, uid, ch, mem, chat._member(c, cid, uid), chat._my_device(c, uid, str(b.get("device") or "")),
                                                       full=True)})


def open_v3(uid, cid, handle):
    """2.5: freie Gruppe der Stufe 3 bekommt eine Person eines anderen Servers - die Krypto bleibt (schon saga-chat/v2 mit
    chat_ref), der Chat wird ab jetzt foederiert (v=2, Heim = dieser Server, KI aus, Systemzeile wie bei Seiten-Chats).
    Nur, wenn das Hinzufuegen dort moeglich ist (Admin, verbundene Instanz mit Gruppen); dann wie group_add."""
    n, host = split_handle(str(handle or "").strip().lower())
    with _db() as c, c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if not n or host == F.SELF_HOST or ch["v"] != 3 or ch["kind"] != "free":
            return _bad(t("ungültige Angabe"))
        if m["role"] != "admin":
            return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
        if not groups_ok(F.peer_by(c, "host", host)):
            return _bad(t("Mit Personen auf {host} sind Gruppen-Chats nicht möglich.", host=host), 403)
        if not c.execute("UPDATE chat SET v=2 WHERE id=? AND v=3", (ch["id"],)).rowcount:
            return _bad(t("ungültige Angabe"))
        had = c.execute("DELETE FROM chat_ai WHERE chat_id=?", (ch["id"],)).rowcount
        sysline(c, dict(ch, v=2), None, {"type": "fed", "on": True, **({"ai_off": True} if had else {})})
        F.log(c, "chat.upgraded", None, f"chat={ch['id']} (Stufe 3)", host=F.SELF_HOST)
        chat._changed(c, ch["id"], local_members(c, ch["id"]))
    return group_add(uid, cid, "", handle)


def group_add(uid, cid, who_, handle):
    """POST /api/chats/{cid}/members bei Gruppen ueber Servergrenzen: {user} (Konto hier) oder {handle} ('~x@host')."""
    with _db() as c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if not chat.is_fed(ch):
            return _bad(t("Personen anderer Saga-Server lassen sich nur zu neuen Gruppen hinzufügen – bitte eine neue Gruppe anlegen."), 409)
        if ch["kind"] != "free":
            return _bad(t("Mitglieder ergeben sich hier aus der Gruppe bzw. den Freigaben der Seite."), 400)
        ro = _ro_group(c, ch)
        if ro:
            return _bad(ro, 403)
        if handle:
            n, host = split_handle(handle.strip().lower())
            if not n:
                return _bad(t("ungültige Angabe"))
            target = F.uid_by_handle(c, n) if host == F.SELF_HOST else principal(f"{n}@{host}")
            if not target:
                return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
            if sharing.is_remote(target):
                p = F.peer_by(c, "host", host)
                if not groups_ok(p):
                    return _bad(t("Mit Personen auf {host} sind Gruppen-Chats nicht möglich.", host=host), 403)
        else:
            target = who_
            if not chat.may_contact(c, uid, target):
                return _bad(t("Mit diesem Konto ist kein Chat möglich."), 404)
        if target in mem:
            return _bad(t("Ist schon Mitglied."), 409)
        if len(mem) >= chat.MEMBERS_MAX:
            return _bad(t("Höchstens {n} Mitglieder je Chat.", n=chat.MEMBERS_MAX), 409)
        mh = my_handle(c, uid)
        th = member_handle(c, target)
        home = ch["home_peer"] is None
    if home:
        with _db() as c, c:
            ch = by_ref(c, ch["ref"], lock=True)
            if _role(c, ch["id"], uid) != "admin":
                return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
            if sharing.is_remote(target):
                _person(c, F.peer_by(c, "host", split_handle(th)[1]), th)
            _add_member(c, ch, target)
            members_changed(c, ch, [target], [], by=uid)
            chat._changed(c, ch["id"], local_members(c, ch["id"]))
            return chat._no_store({"chat": chat._chat_dict(c, uid, ch, chat.members(c, ch["id"]), chat._member(c, ch["id"], uid), None, full=True)})
    with _db() as c:
        nm = label_of(c, target, wire=True)["name"] if sharing.is_remote(target) else (F.display_of(c, target) or "")
        p = F.peer_by(c, "id", ch["home_peer"])
    status, d = sync_call(p, {"op": "members", "ref": ch["ref"], "actor": mh, "add": [{"handle": th, "name": nm}]})
    return _home_result(uid, ch, status, d)


def _home_result(uid, ch, status, d):
    if status is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=_home_host_of(ch)), 502)
    if status == 403:
        return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
    if status != 200:
        return _bad(t("Der andere Saga-Server hat die Änderung abgewiesen ({err}).", err=str((d or {}).get("error") or status)), 409)
    with _db() as c:
        ch2 = chat._load(c, ch["id"])
        m = chat._member(c, ch["id"], uid)
        if not ch2 or not m:
            return chat._no_store({"ok": True})
        return chat._no_store({"chat": chat._chat_dict(c, uid, ch2, chat.members(c, ch["id"]), m, None, full=True)})


def _home_host_of(ch):
    with _db() as c:
        return home_host(c, ch) or "?"


def group_remove(uid, cid, who_):
    """DELETE /api/chats/{cid}/members/{who} bei Gruppen ueber Servergrenzen: verlassen (who = ich) bzw. entfernen (Admin)."""
    with _db() as c:
        ch, m, mem, err = chat._chat_for(c, uid, cid, do_sync=False)
        if err:
            return err
        if ch["kind"] != "free":
            return _bad(t("Diesen Chat kann man nicht verlassen – nur stummschalten."), 400)
        target = who_ if who_ in mem else next((u for u in mem if u.lower() == who_), None)
        if not target:
            return _bad("not found", 404)
        mh = my_handle(c, uid)
        th = member_handle(c, target)
        home = ch["home_peer"] is None
    if home:
        with _db() as c, c:
            ch = by_ref(c, ch["ref"], lock=True)
            if target != uid and _role(c, ch["id"], uid) != "admin":
                return _bad(t("Dafür fehlt dir die Berechtigung."), 403)
            rest = _remove_member(c, ch, target)
            if not rest:
                fanout(c, ch, {"type": "chat.removed", "by": who(c, uid)})
                drop_chat(c, ch)
                return chat._no_store({"ok": True})
            members_changed(c, ch, [], [target], by=uid)
            chat._changed(c, ch["id"], local_members(c, ch["id"]) + [target])
        return chat._no_store({"ok": True})
    if target == uid:                                    # verlassen: hier sofort, beim Heim (synchron, sonst Warteschlange)
        with _db() as c, c:
            c.execute("UPDATE chat_member SET left_at=? WHERE chat_id=? AND uid=?", (time.time(), ch["id"], uid))
            chat._changed(c, ch["id"], [uid])
            if not local_members(c, ch["id"]):
                drop_chat(c, ch)
        to_home(ch, {"op": "leave", "ref": ch["ref"], "actor": mh})
        return chat._no_store({"ok": True})
    with _db() as c:
        p = F.peer_by(c, "id", ch["home_peer"])
    status, d = sync_call(p, {"op": "members", "ref": ch["ref"], "actor": mh, "remove": [th]})
    if status == 200:
        return chat._no_store({"ok": True})
    return _home_result(uid, ch, status, d)


def open_remote_page_chat(uid, nid, device=""):
    """POST /api/chats {kind:'page', note_id: <negativ>} - Seiten-Chat einer Seite eines anderen Servers (dessen Server ist
    das Heim). Die Rolle prueft der Server der Seite."""
    pid, rid = F.unref(nid)
    with _db() as c:
        p = F.peer_by(c, "id", pid)
        if not p or p["status"] != "active" or not F.enabled():
            return _bad("not found", 404)
        if not groups_ok(p):
            return _bad(t("Seiten-Chats mit {host} sind nicht möglich (ältere Saga-Version oder vom Admin abgeschaltet).", host=p["host"]), 404)
        mh = my_handle(c, uid)
        try:
            act = F._home_actor(c, uid, pid)
        except F._Fail as f:
            return f.resp
        pub = F.fed_name(c, uid, p, pseudo=False)
    status, d = sync_call(p, {"op": "pagechat", "note": rid, "actor": mh, "pub": pub, "groups": act["groups"]})
    if status is None:
        return _bad(t("Der Server {host} ist gerade nicht erreichbar.", host=p["host"]), 502)
    if status != 200 or not isinstance(d, dict):
        return _bad("not found", 404) if status == 404 else _bad(t("Mit dieser Person ist kein Chat möglich."), 403)
    with _db() as c, c:
        ch = apply_members(c, p, dict(d, type="chat.members"))
        if not ch:
            return _bad("not found", 404)
        out = _reopen(c, uid, ch, device)
    F.peer_submit(p["id"], fill_gap, p["id"], ch["ref"], None)
    return out


def account_gone(c, uid):
    """Kontoloeschung (aus chat.delete_account, vor dem Loeschen): foederierte Chats bei allen beenden."""
    try:
        mh = my_handle(c, uid)
    except F.FedError:
        return
    nm = F.display_of(c, uid) or ""
    for (cid,) in c.execute("SELECT m.chat_id FROM chat_member m JOIN chat ch ON ch.id=m.chat_id WHERE m.uid=? AND ch.v=2", (uid,)).fetchall():
        ch = chat._load(c, cid)
        if not ch or ch["fstate"] in ("readonly", "gone"):
            continue
        if ch["kind"] in ("free", "page"):                   # Gruppen (E5): nur diese Person geht, die Gruppe bleibt
            if ch["home_peer"] is None:
                if ch["kind"] == "page" and c.execute("SELECT 1 FROM note WHERE id=? AND uid=?", (ch["note_id"], uid)).fetchone():
                    continue                                 # eigene Seite: chat_dropping (Seiten-Chats eigener Seiten)
                rest = _remove_member(c, ch, uid)
                if rest:
                    members_changed(c, ch, [], [uid], reason="gone")
                else:
                    fanout(c, ch, {"type": "chat.removed", "by": {"handle": mh, "name": nm}})
            elif ch["kind"] == "free":
                F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "leave", "ref": ch["ref"], "actor": mh}},
                           chain=chain_up(ch["ref"]))
            continue
        if ch["home_peer"] is None:
            fanout(c, ch, {"type": "chat.removed", "by": {"handle": mh, "name": nm}})
        else:
            F._enqueue(c, ch["home_peer"], {"type": "chat.op", "op_body": {"op": "drop", "ref": ch["ref"], "actor": mh}}, chain=chain_up(ch["ref"]))


def _peer_gone(c, p):
    """Verbindung getrennt (saga.federation.cleanup_peer): Chats mit Heim bzw. Personen dort schreibgeschuetzt, Wartendes
    als nicht zugestellt, Anrufe enden (saga.fed_calls)."""
    tail = "@" + p["host"]
    c.execute("DELETE FROM fed_name_handle WHERE peer_id=?", (p["id"],))
    c.execute("DELETE FROM fed_group_members WHERE peer_id=?", (p["id"],))
    # Gruppen mit Heim hier (E5): Mitglieder der getrennten Instanz raus (Systemzeile, Liste an die anderen Server)
    for (cid,) in c.execute("SELECT DISTINCT ch.id FROM chat ch JOIN chat_member m ON m.chat_id=ch.id AND m.uid LIKE '@%' AND m.left_at IS NULL "
                            "WHERE ch.v=2 AND ch.home_peer IS NULL AND ch.kind IN ('free','page') AND right(m.uid, ?)=?",
                            (len(tail), tail)).fetchall():
        ch = chat._load(c, cid)
        gone = [u for u, _ in members(c, cid) if sharing.is_remote(u) and u.endswith(tail)]
        for u in gone:
            _remove_member(c, ch, u)
        members_changed(c, ch, [], gone, reason="disconnected")
        chat._changed(c, cid, local_members(c, cid))
    rows = c.execute("SELECT DISTINCT ch.id FROM chat ch LEFT JOIN chat_member m ON m.chat_id=ch.id AND m.uid LIKE '@%' "
                     "WHERE ch.v=2 AND ch.fstate='' AND (ch.home_peer=? OR (right(m.uid, ?)=? AND NOT (ch.home_peer IS NULL "
                     "AND ch.kind IN ('free','page'))))", (p["id"], len(tail), tail)).fetchall()
    for (cid,) in rows:
        c.execute("UPDATE chat SET fstate='readonly' WHERE id=?", (cid,))
        c.execute("UPDATE chat_msg SET fstate='failed' WHERE chat_id=? AND fstate='pending'", (cid,))
        chat._changed(c, cid, local_members(c, cid))
    if rows:
        F.log(c, "chat.readonly", p, f"chats={len(rows)}")


def _person_gone(c, peer, name, deleted=False):
    """Person einer anderen Instanz geloescht: Direkt-Chats mit ihr bleiben lesbar, sind aber schreibgeschuetzt."""
    c.execute("DELETE FROM fed_name_handle WHERE principal=?", (f"@{name}@{peer['host']}",))
    if not deleted or not F.PSEUDO_RE.fullmatch(name or ""):
        return
    p_ = principal(f"{name}@{peer['host']}")
    for (cid,) in c.execute("SELECT ch.id FROM chat ch JOIN chat_member m ON m.chat_id=ch.id WHERE ch.v=2 AND ch.kind='dm' AND m.uid=? "
                            "AND ch.fstate=''", (p_,)).fetchall():
        c.execute("UPDATE chat SET fstate='gone' WHERE id=?", (cid,))
        chat._changed(c, cid, local_members(c, cid))


def _keys_changed(c, peer, ev):
    """Schluessel einer fremden Person geaendert: Zwischenspeicher veraltet (saga.fed_keys) + Live-Ereignis an unsere
    Konten mit Chats mit ihr (Apps pruefen TOFU neu)."""
    r = FK._ev_changed(c, peer, ev)
    h = ev.get("handle")
    if r == "ok":
        full = f"{h}@{peer['host']}"
        uids = [u for (u,) in c.execute("SELECT DISTINCT m.uid FROM chat_member m JOIN chat_member o ON o.chat_id=m.chat_id WHERE o.uid=? "
                                        "AND m.uid NOT LIKE '@%'", (principal(full),)).fetchall()]
        if uids:
            chat._direct(c, uids, "chat.keys.changed", {"handle": full})
    return r


F.EV2_HANDLERS["chat.keys.changed"] = _keys_changed


def _local_keys_changed(c, uid, **_):
    """Eigene Schluessel geaendert: andere Konten DIESES Servers mit foederierten Chats nicht betroffen; die Apps des
    Kontos selbst laden ihre Zertifikate neu (chat.keys.changed mit eigenem Handle)."""
    r = c.execute("SELECT handle FROM fed_handle WHERE uid=?", (uid,)).fetchone()
    if r:
        chat._direct(c, [uid], "chat.keys.changed", {"handle": FK.full_handle(r[0]), "own": True})


bus.on("chat.keys.changed", _local_keys_changed)


def periodic():
    """Warteschlange: was laenger als die Aufbewahrung des Ausgangskorbs wartet, gilt als nicht zugestellt; verwaiste
    Verweise auf fremde Anhaenge weg."""
    now = time.time()
    if now - _st["t"] < 300:
        return
    _st["t"] = now
    with _db() as c, c:
        rows = c.execute("UPDATE chat_msg SET fstate='failed' WHERE fstate='pending' AND created<? RETURNING chat_id, sender_uid, id, seq",
                         (now - F.OUTBOX_KEEP - 600,)).fetchall()
        for cid, u, mid, seq in rows:
            chat._direct(c, [u], "chat.message", {"chat_id": cid, "seq": seq, "id": mid, "sender": u, "failed": True})
        c.execute("DELETE FROM chat_file_fed x WHERE NOT EXISTS (SELECT 1 FROM chat_file f WHERE f.id=x.id)")
        # Zwischenkopien, die CACHE_DAYS niemand gelesen hat, verfallen (werden bei Bedarf neu geholt)
        old = now - CACHE_DAYS * 86400
        okp = {}
        for (fid, xp) in c.execute("SELECT id, peer_id FROM chat_file_fed WHERE cached=1").fetchall():
            if xp not in okp:                # nur, was sich beim Absender-Server neu holen laesst (nach Trennen: bleibt)
                okp[xp] = peer_ok(F.peer_by(c, "id", xp))
            if not okp[xp]:
                continue
            try:
                stale = os.stat(os.path.join(chat.CHAT_DIR, fid)).st_mtime < old
            except OSError:
                stale = True
            if stale:
                _evict(c, fid)
        pids = [r[0] for r in c.execute("SELECT DISTINCT peer_id FROM chat_file_fed WHERE cached=1").fetchall()]
    for pid in pids:
        cache_trim(pid)


_st = {"t": 0.0}
F.PERIODIC.append(periodic)
F.PEER_GONE_HOOKS.append(_peer_gone)
F.PERSON_GONE_HOOKS.append(_person_gone)
chat.FED = sys.modules[__name__]
FK.VIA_HOME = keys_via_home
