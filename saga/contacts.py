"""Kontakte (2.5, "Adressbuch" im Chat): Personen, mit denen ein Konto schon in einem Direkt-Chat oder einer freien Gruppe
war - lokal und ueber Servergrenzen. Sie bleiben auffindbar, auch wenn der Chat geloescht oder verlassen wurde.

Tabelle chat_contact (Migration 28): uid = eigenes Konto, ref = Kennung der anderen Person - lokale Konten als
pseudonymer Such-Handle '~…' (core.local_ref, wie /api/chat/people), Personen anderer Server als Handle '~x@host' (wie
members[].handle). Nie ein Anmeldename eines anderen Kontos. name/avatar nur als Zwischenspeicher fuer die Anzeige (beim
Lesen frisch, wo moeglich). hidden = "Aus Kontakten entfernt" (ein neuer gemeinsamer Chat macht wieder sichtbar).

Eingetragen wird nur aus eigenen Chats (kein Orakel): beim Anlegen eines Direkt-Chats/einer freien Gruppe, beim
Hinzufuegen von Mitgliedern (gegenseitig fuer alle) und bei Beitritt/Annehmen ueber die Foederation. Eigentuemer eines
Eintrags ist nur ein lokales Mitglied im Zustand 'ok' (eine offene Nachrichtenanfrage macht niemanden zum Kontakt).
Benutzergruppen- und Seiten-Chats nicht. Rueckwirkend einmalig beim Start (backfill, idempotent).

Dieses Modul importiert weder saga.chat noch die Foederation (keine Zyklen): Angaben zu Personen anderer Server liefert
saga.fed_chat ueber den Haken REMOTE."""
import re
import time

from saga import avatars
from saga.core import HANDLE_RE, is_raw_ref, labels, local_ref, subtitles, unnamed
from saga.sharing import is_remote

KINDS = ("dm", "free")
LIST_MAX = 200
SCAN_MAX = 1000                       # so viele Eintraege werden fuer eine Suche hoechstens durchgesehen
TOUCH_EVERY = 600                     # last_at bei neuen Nachrichten hoechstens so oft auffrischen (Sekunden)
_RREF = re.compile(r"(~[a-z2-7]{16})@([a-z0-9.:\[\]-]{1,260})")
# 2.5: Personen anderer Server (saga.fed_chat setzt sich ein): REMOTE(c, refs) -> {ref: {name, host, ready, gone, avatar}}
REMOTE = None
_UMAP = {"t": 0.0, "n": None, "m": {}}


def ref_of(u):
    """Mitglied (lokaler Benutzername bzw. '@~x@host') -> Kennung im Kontakt ('~…' bzw. '~x@host') oder None."""
    if not isinstance(u, str) or not u:
        return None
    if is_remote(u):
        return u[1:] if _RREF.fullmatch(u[1:]) else None    # nur pseudonyme Handles (Chats v2), keine alten '@name@host'
    return local_ref(u)


def is_remote_ref(r):
    return bool(_RREF.fullmatch(r or ""))


def _umap(c):
    """Such-Handle -> Benutzername fuer ALLE Konten (auch deaktivierte; core.resolve_ref kennt nur aktive)."""
    n = tuple(c.execute("SELECT COUNT(*), MAX(id) FROM users").fetchone())
    if n != _UMAP["n"] or time.time() - _UMAP["t"] > 300:
        _UMAP.update(t=time.time(), n=n, m={local_ref(r[0]): r[0] for r in c.execute("SELECT username FROM users")})
    return _UMAP["m"]


def local_uid(c, ref):
    """Such-Handle eines Kontakts -> Benutzername (auch deaktiviert) oder None."""
    return _umap(c).get(ref) if HANDLE_RE.fullmatch(ref or "") else None


def _upsert(c, rows, unhide):
    """rows: [(uid, ref, name, avatar, at)]"""
    tail = ", hidden=0" if unhide else ""
    for uid, ref, name, av, at in rows:
        c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,?,?,?,?,0) "
                  "ON CONFLICT (uid, ref) DO UPDATE SET name=CASE WHEN EXCLUDED.name<>'' THEN EXCLUDED.name ELSE chat_contact.name END, "
                  "avatar=COALESCE(EXCLUDED.avatar, chat_contact.avatar), first_at=LEAST(chat_contact.first_at, EXCLUDED.first_at), "
                  "last_at=GREATEST(chat_contact.last_at, EXCLUDED.last_at)" + tail, (uid, ref, name or "", av, at, at))


def _cache(c, us):
    """Anzeigename/Bild zum Zwischenspeichern: lokal Anzeigename (nie Anmeldename), fremd der gemerkte Anzeigename."""
    loc = [u for u in us if not is_remote(u)]
    rem = [u for u in us if is_remote(u)]
    nm = {u: ("" if n == unnamed(local_ref(u)) else n) for u, n in (labels(c, loc) if loc else {}).items()}   # Ersatz nie speichern
    if rem:
        ph = ",".join("?" * len(rem))
        nm.update({r[0]: "" if is_raw_ref(r[1] or "") else (r[1] or "") for r in
                   c.execute(f"SELECT principal, display FROM fed_person WHERE principal IN ({ph})", tuple(rem))})
    return nm, avatars.urls(c, loc) if loc else {}


def link(c, cid, who=None, now=None):
    """Mitglieder des Chats cid gegenseitig als Kontakte eintragen (Direkt-Chat, freie Gruppe). who: nur Paare, an denen
    eines dieser Mitglieder beteiligt ist (neu hinzugefuegt / angenommen); None = alle Paare (neuer Chat). Macht
    ausgeblendete Kontakte wieder sichtbar. -> Anzahl Eintraege"""
    r = c.execute("SELECT kind FROM chat WHERE id=?", (cid,)).fetchone()
    if not r or r[0] not in KINDS:
        return 0
    rows = c.execute("SELECT uid, state FROM chat_member WHERE chat_id=? AND left_at IS NULL", (cid,)).fetchall()
    mem = [u for u, _ in rows]
    owners = [u for u, st in rows if not is_remote(u) and (st or "ok") == "ok"]
    focus = set(who) if who is not None else None
    now = now or time.time()
    pairs = [(o, x) for o in owners for x in mem if x != o and (focus is None or o in focus or x in focus) and ref_of(x)]
    if not pairs:
        return 0
    nm, av = _cache(c, {x for _, x in pairs})
    _upsert(c, [(o, ref_of(x), nm.get(x, ""), av.get(x), now) for o, x in pairs], unhide=True)
    return len(pairs)


def touch(c, cid, sender, now=None):
    """Neue Nachricht im Chat: last_at auffrischen (Sortierung "zuletzt zuerst") - im Direkt-Chat beide Richtungen, in
    freien Gruppen die Kontakte des Absenders. Hoechstens alle TOUCH_EVERY Sekunden je Eintrag (billig)."""
    r = c.execute("SELECT kind FROM chat WHERE id=?", (cid,)).fetchone()
    if not r or r[0] not in KINDS or not sender:
        return
    now = now or time.time()
    mem = [u for (u,) in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL AND state='ok'", (cid,))]
    if sender not in mem:
        return
    others = [ref_of(u) for u in mem if u != sender and ref_of(u)]
    if not is_remote(sender) and others:
        c.execute(f"UPDATE chat_contact SET last_at=? WHERE uid=? AND last_at<? AND ref IN ({','.join('?' * len(others))})",
                  (now, sender, now - TOUCH_EVERY, *others))
    sref = ref_of(sender)
    if r[0] == "dm" and sref:
        for u in mem:
            if u != sender and not is_remote(u):
                c.execute("UPDATE chat_contact SET last_at=? WHERE uid=? AND ref=? AND last_at<?", (now, u, sref, now - TOUCH_EVERY))


def backfill(c, now=None):
    """Rueckwirkend aus vorhandenen Direkt-Chats und freien Gruppen (auch verlassenen): Paare, die gleichzeitig Mitglied
    waren. Ausgeblendete bleiben ausgeblendet. Idempotent. -> Anzahl Paare"""
    rows = c.execute(
        "SELECT o.uid, x.uid, COALESCE(MIN(LEAST(o.joined, x.joined)), 0), COALESCE(MAX(COALESCE(ch.last_at, ch.created)), 0) "
        "FROM chat ch JOIN chat_member o ON o.chat_id=ch.id JOIN chat_member x ON x.chat_id=ch.id AND x.uid<>o.uid "
        "WHERE ch.kind IN ('dm','free') AND o.uid NOT LIKE '@%' AND o.state='ok' "
        "AND x.joined <= COALESCE(o.left_at, x.joined) AND o.joined <= COALESCE(x.left_at, o.joined) "
        "GROUP BY o.uid, x.uid").fetchall()
    out = []
    for o, x, first, last in rows:
        r = ref_of(x)
        if r:
            out.append((o, r, first or last, last or first))
    nm, av = _cache(c, {x for _, x, _, _ in rows})
    rmap = {ref_of(x): x for _, x, _, _ in rows}
    for o, r, first, last in out:
        x = rmap.get(r)
        c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,?,?,?,?,0) "
                  "ON CONFLICT (uid, ref) DO UPDATE SET first_at=LEAST(chat_contact.first_at, EXCLUDED.first_at), "
                  "last_at=GREATEST(chat_contact.last_at, EXCLUDED.last_at)", (o, r, nm.get(x, ""), av.get(x), first, last))
    return len(out)


FILL_KEY = "chat_contact_fill"


def backfill_once():
    """Einmalig beim Start (Migration 28): rueckwirkend fuellen; mehrere Prozesse gleichzeitig: Advisory-Lock + Merker."""
    from db import connect
    with connect() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(424243)")
        if c.execute("SELECT 1 FROM setting WHERE k=?", (FILL_KEY,)).fetchone():
            return None
        n = backfill(c)
        c.execute("INSERT INTO setting (k, v) VALUES (?, ?) ON CONFLICT (k) DO NOTHING", (FILL_KEY, str(int(time.time()))))
        return n


def hidden_refs(c, uid):
    return {r[0] for r in c.execute("SELECT ref FROM chat_contact WHERE uid=? AND hidden<>0", (uid,))}


def has(c, uid, other):
    """Ist das lokale Konto other ein (sichtbarer) Kontakt von uid? (Kontakte umgehen "nur gemeinsame Gruppen")"""
    return bool(other) and not is_remote(other) and bool(
        c.execute("SELECT 1 FROM chat_contact WHERE uid=? AND ref=? AND hidden=0", (uid, local_ref(other))).fetchone())


def entries(c, uid, q="", limit=50, with_gone=True):
    """Eigene sichtbare Kontakte, zuletzt zuerst; q filtert (Teilwort in Name/Unterzeile, jede Laenge - die Liste ist
    ohnehin schon die eigene). -> [{ref, name, sub, avatar, remote, host?, ready, gone, last_at}]"""
    q = re.sub(r"\s+", " ", str(q or "")).strip().lower()[:60]
    rows = c.execute("SELECT ref, name, avatar, last_at FROM chat_contact WHERE uid=? AND hidden=0 ORDER BY last_at DESC, ref LIMIT ?",
                     (uid, SCAN_MAX if q else max(1, min(limit, LIST_MAX)))).fetchall()
    loc = {r[0]: local_uid(c, r[0]) for r in rows if not is_remote_ref(r[0])}
    us = [u for u in loc.values() if u]
    st = {}
    if us:
        ph = ",".join("?" * len(us))
        st = {r[0]: (bool(r[1]), bool(r[2])) for r in c.execute(
            f"SELECT u.username, u.disabled, (i.uid IS NOT NULL) FROM users u LEFT JOIN chat_identity i ON i.uid=u.username "
            f"WHERE u.username IN ({ph})", tuple(us))}
    lb = labels(c, us) if us else {}
    sb = subtitles(c, us, lb) if us else {}
    av = avatars.urls(c, us) if us else {}
    rem = [r[0] for r in rows if is_remote_ref(r[0])]
    rinfo = REMOTE(c, rem) if (rem and REMOTE is not None) else {}
    out = []
    for ref, name, avc, last in rows:
        if ref in loc:
            u = loc[ref]
            if not u:
                continue                         # Konto geloescht (Eintrag geht mit drop_account; Rest nach Schluesselwechsel)
            dis, ready = st.get(u, (True, False))
            d = {"ref": ref, "name": lb.get(u) or name or ref, "sub": sb.get(u, ""), "avatar": None if dis else av.get(u),
                 "remote": False, "ready": ready and not dis, "gone": dis, "last_at": last}
        else:
            ri = rinfo.get(ref) or {}
            host = ref.split("@", 1)[1]
            d = {"ref": ref, "name": ri.get("name") or name or unnamed(ref.split("@")[0], host), "sub": host, "host": host,
                 "avatar": ri.get("avatar") or avc, "remote": True, "ready": bool(ri.get("ready")), "gone": bool(ri.get("gone", True)),
                 "last_at": last}
        if q and q not in d["name"].lower() and q not in (d["sub"] or "").lower():
            continue
        if not with_gone and d["gone"]:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def hide(c, uid, ref):
    return c.execute("UPDATE chat_contact SET hidden=1 WHERE uid=? AND ref=? AND hidden=0", (uid, ref)).rowcount


def drop_account(c, uid):
    """Kontoloeschung: eigene Kontakte und das Konto als Kontakt bei allen anderen weg."""
    return c.execute("DELETE FROM chat_contact WHERE uid=? OR ref=?", (uid, local_ref(uid))).rowcount


def drop_host(c, host):
    """Instanz entfernt: alle Kontakte dort weg (wie ihr Adressbuch)."""
    tail = "@" + host
    return c.execute("DELETE FROM chat_contact WHERE right(ref, ?)=?", (len(tail), tail)).rowcount


def drop_remote(c, ref):
    """Person eines anderen Servers geloescht."""
    return c.execute("DELETE FROM chat_contact WHERE ref=?", (ref,)).rowcount


def export(c, uid):
    """Fuer das persoenliche Backup / den Datenexport: eigene Kontakte (Kennung, Name, Zeiten, ausgeblendet)."""
    return [{"ref": r[0], "name": r[1], "remote": is_remote_ref(r[0]), "first_at": r[2], "last_at": r[3], "hidden": bool(r[4])}
            for r in c.execute("SELECT ref, name, first_at, last_at, hidden FROM chat_contact WHERE uid=? ORDER BY last_at DESC", (uid,))]


def restore(c, uid, items, may_local=None):
    """Aus einem Backup einspielen (nur ins eigene Konto, vorhandene bleiben). Lokale Kennungen nur, wenn may_local(ref)
    zustimmt (sonst liesse sich mit einem gebastelten Backup "nur gemeinsame Gruppen" umgehen). -> Anzahl"""
    n = 0
    for x in (items if isinstance(items, list) else [])[:5000]:
        if not isinstance(x, dict):
            continue
        ref = str(x.get("ref") or "")
        if not (is_remote_ref(ref) or HANDLE_RE.fullmatch(ref)):
            continue
        if not is_remote_ref(ref) and (local_uid(c, ref) in (None, uid) or (may_local is not None and not may_local(ref))):
            continue
        at = lambda k: float(x[k]) if isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) else time.time()  # noqa: E731
        n += c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,?,NULL,?,?,?) "
                       "ON CONFLICT (uid, ref) DO NOTHING", (uid, ref, str(x.get("name") or "")[:200], at("first_at"), at("last_at"),
                                                             1 if x.get("hidden") is True else 0)).rowcount
    return n
