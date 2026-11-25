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
import unicodedata

from saga import avatars, bus
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
    mem = [u for u, st in rows if (st or "ok") == "ok"]       # offene/abgelehnte Anfragen auf keiner Seite (sonst koennte ein
    owners = [u for u in mem if not is_remote(u)]             # fremdes Heim lokale Konten als Kontakte einschleusen)
    focus = set(who) if who is not None else None
    now = now or time.time()
    pairs = [(o, x) for o in owners for x in mem if x != o and (focus is None or o in focus or x in focus) and ref_of(x)]
    if not pairs:
        return 0
    nm, av = _cache(c, {x for _, x in pairs})
    lock(c, {o for o, _ in pairs})                            # 2.6 (2027a): vor chat_contact, gleiche Reihenfolge ueberall
    _upsert(c, [(o, ref_of(x), nm.get(x, ""), av.get(x), now) for o, x in pairs], unhide=True)
    book_ensure(c, [(o, ref_of(x)) for o, x in pairs], now)  # 2.6 (2027a): Adressbuch - je Person ein Kontakt (beidseitig)
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
        "FROM chat ch JOIN chat_member o ON o.chat_id=ch.id JOIN chat_member x ON x.chat_id=ch.id AND x.uid<>o.uid AND x.state='ok' "
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
    _ensure_visible(c, [(o, r) for o, r, _, _ in out])        # 2.6 (2027a): Adressbuch (ausgeblendete nicht)
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


def describe(c, refs, cache=None):
    """Kennungen ('~…' lokal, '~x@host' fremd) -> {ref: {ref, name, sub, avatar, remote, host?, ready, gone}} - Name frisch
    (lokal Anzeigename, nie Anmeldename; fremd ueber den Haken REMOTE), sonst der Zwischenspeicher aus chat_contact (cache:
    {ref: (name, avatar)}). Lokale Kennungen ohne Konto fehlen im Ergebnis."""
    return _describe(c, refs, cache or {})


def _describe(c, refs, cache):
    refs = list(dict.fromkeys(r for r in refs if r))
    loc = {r: local_uid(c, r) for r in refs if not is_remote_ref(r)}
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
    rem = [r for r in refs if is_remote_ref(r)]
    rinfo = REMOTE(c, rem) if (rem and REMOTE is not None) else {}
    out = {}
    for ref in refs:
        name, avc = cache.get(ref) or ("", None)
        if ref in loc:
            u = loc[ref]
            if not u:
                continue                         # Konto geloescht (Eintrag geht mit drop_account; Rest nach Schluesselwechsel)
            dis, ready = st.get(u, (True, False))
            out[ref] = {"ref": ref, "name": lb.get(u) or name or ref, "sub": sb.get(u, ""), "avatar": None if dis else av.get(u),
                        "remote": False, "ready": ready and not dis, "gone": dis}
        elif is_remote_ref(ref):
            ri = rinfo.get(ref) or {}
            host = ref.split("@", 1)[1]
            out[ref] = {"ref": ref, "name": ri.get("name") or name or unnamed(ref.split("@")[0], host), "sub": host, "host": host,
                        "avatar": ri.get("avatar") or avc, "remote": True, "ready": bool(ri.get("ready")),
                        "gone": bool(ri.get("gone", True))}
    return out


def entries(c, uid, q="", limit=50, with_gone=True):
    """Eigene sichtbare Kontakte, zuletzt zuerst; q filtert (Teilwort in Name/Unterzeile, jede Laenge - die Liste ist
    ohnehin schon die eigene). -> [{ref, name, sub, avatar, remote, host?, ready, gone, last_at, contact_id}]
    (contact_id: Eintrag im Adressbuch, 2.6 contacts2 - Name dort ggf. "Name fuer mich", hier nie)"""
    q = re.sub(r"\s+", " ", str(q or "")).strip().lower()[:60]
    rows = c.execute("SELECT ref, name, avatar, last_at FROM chat_contact WHERE uid=? AND hidden=0 ORDER BY last_at DESC, ref LIMIT ?",
                     (uid, SCAN_MAX if q else max(1, min(limit, LIST_MAX)))).fetchall()
    info = _describe(c, [r[0] for r in rows], {r[0]: (r[1], r[2]) for r in rows})
    ids = _ids_for(c, uid, [r[0] for r in rows if r[0] in info])
    out = []
    for ref, _name, _avc, last in rows:
        d = info.get(ref)
        if d is None:
            continue
        if q and q not in d["name"].lower() and q not in (d["sub"] or "").lower():
            continue
        if not with_gone and d["gone"]:
            continue
        out.append(dict(d, last_at=last, contact_id=ids.get(ref)))
        if len(out) >= limit:
            break
    return out


def hide(c, uid, ref):
    """"Aus Kontakten entfernen" (alte API, contacts1): raus aus den Chat-Vorschlaegen. 2.6 (2027a): der Kontakt im
    Adressbuch geht nur mit, wenn er leer ist (keine eigenen Felder, keine Nummern/Mails) - sonst bleibt er dort."""
    lock(c, [uid])
    n = c.execute("UPDATE chat_contact SET hidden=1 WHERE uid=? AND ref=? AND hidden=0", (uid, ref)).rowcount
    if n:
        r = c.execute("SELECT contact_id FROM contact_addr WHERE uid=? AND kind=? AND value=? AND fixed=1",
                      (uid, addr_kind(ref), ref)).fetchone()
        if r and _empty(c, r[0]):
            c.execute("DELETE FROM contact WHERE id=? AND uid=?", (r[0], uid))
            changed(c, uid, r[0], deleted=True)
    return n


def drop_account(c, uid):
    """Kontoloeschung: eigene Kontakte (auch das Adressbuch) und das Konto als Kontakt bei allen anderen weg. Kontakte
    anderer mit eigenen Feldern bleiben ohne die Saga-Adresse (origin manual)."""
    c.execute("DELETE FROM contact WHERE uid=?", (uid,))               # contact_addr: ON DELETE CASCADE
    _drop_fixed(c, "value=?", (local_ref(uid),), "saga")
    return c.execute("DELETE FROM chat_contact WHERE uid=? OR ref=?", (uid, local_ref(uid))).rowcount


def drop_host(c, host):
    """Instanz entfernt: alle Kontakte dort weg (wie ihr Adressbuch); Adressbuch-Eintraege mit eigenen Feldern bleiben
    ohne die Fed-Adresse."""
    tail = "@" + host
    _drop_fixed(c, "right(value, ?)=?", (len(tail), tail), "fed")
    return c.execute("DELETE FROM chat_contact WHERE right(ref, ?)=?", (len(tail), tail)).rowcount


def drop_remote(c, ref):
    """Person eines anderen Servers geloescht."""
    _drop_fixed(c, "value=?", (ref,), "fed")
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
        k = c.execute("INSERT INTO chat_contact (uid, ref, name, avatar, first_at, last_at, hidden) VALUES (?,?,?,NULL,?,?,?) "
                      "ON CONFLICT (uid, ref) DO NOTHING", (uid, ref, str(x.get("name") or "")[:200], at("first_at"), at("last_at"),
                                                            1 if x.get("hidden") is True else 0)).rowcount
        if k and x.get("hidden") is not True:
            book_ensure(c, [(uid, ref)])                  # 2.6 (2027a): Adressbuch
        n += k
    return n


# =====================================================================================================================
# 2.6 (Bugs-DB 2027a): Adressbuch - Tabellen contact / contact_addr (Migration 38), API in saga/contact_book.py
# =====================================================================================================================
# Grundsatz: ein Kontakt vergibt KEINE Rechte (has() liest nur chat_contact). Saga-/Fed-Adressen (kind saga/fed, fixed=1)
# entstehen nur aus chat_contact - also aus einem gemeinsamen Chat (kein Orakel), nie aus einer Eingabe. Nummern und
# Mails stehen nie im Log. Das Adressbuch verlaesst den Server nie (kein fed_*-Modul liest es).
FIXED_KINDS = ("saga", "fed")
FILL_BOOK = "contact_fill"
LOCK_NS = 424244                      # Advisory-Lock je Konto (zweiter Wert hashtext(uid))


def addr_kind(ref):
    return "fed" if is_remote_ref(ref) else "saga"


def lock(c, uids):
    """Adressbuch je Konto sperren (bis zum Ende der Transaktion) - immer sortiert (keine Verklemmung)."""
    for u in sorted({u for u in uids if u and not is_remote(u)}):
        c.execute(f"SELECT pg_advisory_xact_lock({LOCK_NS}, hashtext(?))", (u,))


def changed(c, uid, cid, deleted=False):
    """Live contacts.changed {id, deleted?} nach dem Commit - nur an die eigenen Verbindungen (ohne Inhalt)."""
    bus.emit_after(c, "direct", {"uids": [uid], "typ": "contacts.changed",
                                 "data": dict({"id": cid}, **({"deleted": True} if deleted else {}))})


def _new_fixed(c, uid, ref, at):
    """Kontakt (origin chat) mit fester Adresse ref, falls ref noch keinen hat. -> neue contact.id oder None"""
    kind = addr_kind(ref)
    if c.execute("SELECT 1 FROM contact_addr WHERE uid=? AND kind=? AND value=?", (uid, kind, ref)).fetchone():
        return None
    cid = c.execute("INSERT INTO contact (uid, origin, created, updated, ver) VALUES (?, 'chat', ?, ?, 1)", (uid, at, at)).lastrowid
    if not c.execute("INSERT INTO contact_addr (contact_id, uid, kind, value, type, fixed, pos, created) VALUES (?,?,?,?,NULL,1,0,?) "
                     "ON CONFLICT (uid, kind, value) DO NOTHING", (cid, uid, kind, ref, at)).lastrowid:   # (rowcount ist mit RETURNING immer 1)
        c.execute("DELETE FROM contact WHERE id=?", (cid,))           # gleichzeitig angelegt (andere Transaktion)
        return None
    return cid


def book_ensure(c, pairs, now=None):
    """[(uid, ref)] aus chat_contact: fehlt der Kennung ref ein Kontakt im Adressbuch von uid, einen anlegen (origin chat,
    feste Adresse). Gleiche Transaktion wie chat_contact. -> Anzahl neuer Kontakte"""
    pairs = [(u, r) for u, r in dict.fromkeys(pairs) if u and not is_remote(u) and (is_remote_ref(r) or HANDLE_RE.fullmatch(r or ""))]
    if not pairs:
        return 0
    lock(c, {u for u, _ in pairs})
    now = now or time.time()
    n = 0
    for u, r in pairs:
        cid = _new_fixed(c, u, r, now)
        if cid:
            n += 1
            changed(c, u, cid)
    return n


def _ensure_visible(c, pairs):
    vis = []
    for u, r in pairs:
        row = c.execute("SELECT first_at FROM chat_contact WHERE uid=? AND ref=? AND hidden=0", (u, r)).fetchone()
        if row:
            vis.append((u, r))
    return book_ensure(c, vis)


def book_fill(c):
    """Bestand (Upgrade 37 -> 38): je sichtbarem chat_contact ein Kontakt; ausgeblendete ohne. Idempotent. -> Anzahl neu"""
    rows = c.execute("SELECT cc.uid, cc.ref, cc.first_at FROM chat_contact cc WHERE cc.hidden=0 AND NOT EXISTS "
                     "(SELECT 1 FROM contact_addr a WHERE a.uid=cc.uid AND a.kind IN ('saga','fed') AND a.value=cc.ref) "
                     "ORDER BY cc.uid, cc.first_at, cc.ref").fetchall()
    n = 0
    for u, r, first in rows:
        if is_remote(u) or not (is_remote_ref(r) or HANDLE_RE.fullmatch(r or "")):
            continue
        if _new_fixed(c, u, r, first or time.time()):
            n += 1
    return n


def book_fill_once():
    """Einmalig beim Start (Migration 38), nach backfill_once: Adressbuch aus chat_contact fuellen; mehrere Prozesse
    gleichzeitig: Advisory-Lock + Merker contact_fill."""
    from db import connect
    with connect() as c, c:
        c.execute("SELECT pg_advisory_xact_lock(424245)")
        if c.execute("SELECT 1 FROM setting WHERE k=?", (FILL_BOOK,)).fetchone():
            return None
        n = book_fill(c)
        c.execute("INSERT INTO setting (k, v) VALUES (?, ?) ON CONFLICT (k) DO NOTHING", (FILL_BOOK, str(int(time.time()))))
        if n:
            print(f"contacts: {n} Kontakt(e) ins Adressbuch uebernommen", flush=True)
        return n


def _empty(c, cid):
    """Kontakt ohne eigene Felder und ohne private Adressen (nur feste)?"""
    r = c.execute("SELECT own_name, form, salutation FROM contact WHERE id=?", (cid,)).fetchone()
    if not r:
        return False
    if any(x not in (None, "") for x in r):
        return False
    return not c.execute("SELECT 1 FROM contact_addr WHERE contact_id=? AND fixed=0", (cid,)).fetchone()


def _drop_fixed(c, where, args, kind):
    """Feste Adressen (Person/Instanz weg) bei allen Konten entfernen: leere Kontakte gehen mit, Kontakte mit eigenen
    Feldern bleiben als manual (ohne diese Adresse)."""
    rows = c.execute(f"DELETE FROM contact_addr WHERE kind=? AND fixed=1 AND {where} RETURNING contact_id, uid",
                     (kind, *args)).fetchall()
    now = time.time()
    for cid, u in sorted({(r[0], r[1]) for r in rows}):
        if c.execute("SELECT 1 FROM contact_addr WHERE contact_id=? AND fixed=1", (cid,)).fetchone():
            c.execute("UPDATE contact SET updated=?, ver=ver+1 WHERE id=?", (now, cid))
            changed(c, u, cid)
        elif _empty(c, cid):
            c.execute("DELETE FROM contact WHERE id=?", (cid,))
            changed(c, u, cid, deleted=True)
        else:
            c.execute("UPDATE contact SET origin='manual', updated=?, ver=ver+1 WHERE id=?", (now, cid))
            changed(c, u, cid)
    return len(rows)


def _ids_for(c, uid, refs):
    """Kennungen aus chat_contact -> {ref: contact.id} (Adressbuch-Eintrag mit dieser festen Adresse)."""
    refs = [r for r in refs if r]
    if not refs:
        return {}
    ph = ",".join("?" * len(refs))
    return {r[0]: r[1] for r in c.execute(f"SELECT value, contact_id FROM contact_addr WHERE uid=? AND fixed=1 AND value IN ({ph})",
                                          (uid, *refs))}


# --- Weichen fuer spaetere Schritte (K-E Kollegin, Anrufer-Erkennung/Status-Ausnahmen 3.0-A) --------------------------
def owned(c, uid, ids):
    """Welche dieser Kontakt-IDs gehoeren uid? -> set (fremde/unbekannte fehlen)"""
    ids = [int(i) for i in ids if (isinstance(i, int) and not isinstance(i, bool)) or (isinstance(i, str) and i.isdigit())][:1000]
    if not ids:
        return set()
    ph = ",".join("?" * len(ids))
    return {r[0] for r in c.execute(f"SELECT id FROM contact WHERE uid=? AND id IN ({ph})", (uid, *ids))}


def contact_for(c, uid, kind, value):
    """Kontakt von uid mit genau dieser Adresse (kind saga|fed|e164|email, Wert schon normalisiert) -> id oder None."""
    if kind not in ("saga", "fed", "e164", "email") or not value:
        return None
    r = c.execute("SELECT contact_id FROM contact_addr WHERE uid=? AND kind=? AND value=?", (uid, kind, value)).fetchone()
    return r[0] if r else None


def addresses(c, uid, cid):
    """Adressen eines eigenen Kontakts, feste zuerst. -> [{id, kind, value, type, fixed}] ([] bei fremder ID)"""
    return [{"id": r[0], "kind": r[1], "value": r[2], "type": r[3], "fixed": bool(r[4])} for r in c.execute(
        "SELECT id, kind, value, type, fixed FROM contact_addr WHERE uid=? AND contact_id=? ORDER BY fixed DESC, pos, id", (uid, cid))]


# --- Persoenliches Backup / Datenexport (2027a-3) --------------------------------------------------------------------
E164_RE = re.compile(r"\+[1-9]\d{7,14}")
# wie contact_book.norm_email (Bugs-DB 2399): keine Trenn-/Steuerzeichen, damit ein Wert genau ein Empfaenger bleibt
EMAIL_RE = re.compile(r'[^\s@\x00-\x1f\x7f,;?&#<>"\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]+'
                      r'@[^\s@A-Z\x00-\x1f\x7f,;?&#<>"\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]+')
_MAIL_CATS = ("Cc", "Cf", "Cs", "Zs", "Zl", "Zp")       # Steuer- (auch C1), Format-, Ersatz- und Trennzeichen


def mail_chars_ok(s):
    """Keine Steuer-, unsichtbaren Format- oder Trennzeichen (vollstaendig nach Unicode-Kategorie; Bugs-DB 2399)."""
    return not any(unicodedata.category(ch) in _MAIL_CATS for ch in s)


FORMS = ("informal", "formal")
TYPES = ("mobile", "home", "work", "other")
BOOK_MANUAL_MAX = 2000
BOOK_KIND_MAX = 10


def book_export(c, uid):
    """Adressbuch fuer daten.json -> [{own_name, origin, form, salutation, created, updated, addrs: [{kind, value, type,
    fixed}]}] - feste Adressen als Handle (nie Anmeldename)."""
    out, idx = [], {}
    for r in c.execute("SELECT id, own_name, origin, form, salutation, created, updated FROM contact WHERE uid=? ORDER BY id", (uid,)):
        idx[r[0]] = {"own_name": r[1], "origin": r[2], "form": r[3], "salutation": r[4], "created": r[5], "updated": r[6], "addrs": []}
        out.append(idx[r[0]])
    for r in c.execute("SELECT contact_id, kind, value, type, fixed FROM contact_addr WHERE uid=? ORDER BY contact_id, fixed DESC, pos, id",
                       (uid,)):
        if r[0] in idx:
            idx[r[0]]["addrs"].append({"kind": r[1], "value": r[2], "type": r[3], "fixed": bool(r[4])})
    return out


def _bk_text(v, mx):
    return re.sub(r"\s+", " ", v).strip()[:mx] or None if isinstance(v, str) else None


def _bk_private(addrs):
    out, seen = [], set()
    for a in addrs if isinstance(addrs, list) else []:
        if not isinstance(a, dict) or a.get("kind") not in ("e164", "email") or not isinstance(a.get("value"), str):
            continue
        k, v = a["kind"], a["value"].strip()
        if not (E164_RE.fullmatch(v) if k == "e164" else (len(v) <= 254 and EMAIL_RE.fullmatch(v) and mail_chars_ok(v))) or (k, v) in seen:
            continue
        seen.add((k, v))
        out.append((k, v, a.get("type") if a.get("type") in TYPES else None))
    return out


def _bk_add_addrs(c, uid, cid, items, now):
    """Private Adressen anhaengen (vorhandene Werte im Konto - auch bei anderen Kontakten - werden uebersprungen,
    Grenze je Art beachtet). -> Anzahl"""
    have = {k: n for k, n in c.execute("SELECT kind, COUNT(*) FROM contact_addr WHERE contact_id=? AND fixed=0 GROUP BY kind", (cid,))}
    pos = c.execute("SELECT COALESCE(MAX(pos), 0) FROM contact_addr WHERE contact_id=?", (cid,)).fetchone()[0]
    n = 0
    for k, v, typ in items:
        if have.get(k, 0) >= BOOK_KIND_MAX:
            continue
        pos += 1
        if c.execute("INSERT INTO contact_addr (contact_id, uid, kind, value, type, fixed, pos, created) VALUES (?,?,?,?,?,0,?,?) "
                     "ON CONFLICT (uid, kind, value) DO NOTHING", (cid, uid, k, v, typ, pos, now)).lastrowid:
            have[k] = have.get(k, 0) + 1
            n += 1
    return n


def book_restore(c, uid, items):
    """Aus daten.json -> contacts einspielen (nach chat_contacts). Feste Adressen nur dort, wo ein sichtbarer chat_contact
    (uid, ref) besteht: dann leere Felder fuellen und fehlende private Adressen ergaenzen. Sonst: mit privaten Daten als
    manual ohne Saga-Adresse, ohne verworfen. Manuelle: anlegen ausser gleicher Kontakt (gleiches own_name + gleiche
    Adressmenge); schon vorhandene Werte (contact_addr_one) werden uebersprungen. Zweimal einspielen = keine Dubletten.
    -> Anzahl angelegter oder ergaenzter Kontakte"""
    if not isinstance(items, list):
        return 0
    lock(c, [uid])
    now = time.time()
    sig = {}
    for cid, own in c.execute("SELECT id, own_name FROM contact WHERE uid=?", (uid,)).fetchall():
        sig[cid] = [own or "", set()]
    for cid, k, v in c.execute("SELECT contact_id, kind, value FROM contact_addr WHERE uid=? AND fixed=0", (uid,)).fetchall():
        if cid in sig:
            sig[cid][1].add((k, v))
    sigs = {(o, frozenset(a)) for o, a in sig.values()}
    manual = c.execute("SELECT COUNT(*) FROM contact WHERE uid=? AND origin='manual'", (uid,)).fetchone()[0]
    n = 0
    for x in items[:BOOK_MANUAL_MAX + 5000]:
        if not isinstance(x, dict):
            continue
        own = _bk_text(x.get("own_name"), 120)
        form = x.get("form") if x.get("form") in FORMS else None
        sal = _bk_text(x.get("salutation"), 80)
        priv = _bk_private(x.get("addrs"))
        refs = [str(a.get("value") or "") for a in (x.get("addrs") if isinstance(x.get("addrs"), list) else [])
                if isinstance(a, dict) and a.get("kind") in FIXED_KINDS]
        target = None
        for ref in refs:
            if not (is_remote_ref(ref) or HANDLE_RE.fullmatch(ref)):
                continue
            if c.execute("SELECT 1 FROM chat_contact WHERE uid=? AND ref=? AND hidden=0", (uid, ref)).fetchone():
                book_ensure(c, [(uid, ref)], now)
                target = contact_for(c, uid, addr_kind(ref), ref)
                if target:
                    break
        if target:
            r = c.execute("SELECT own_name, form, salutation FROM contact WHERE id=?", (target,)).fetchone()
            fill = (r[0] or own, r[1] or form, r[2] or sal)
            k = _bk_add_addrs(c, uid, target, priv, now)
            if k or fill != tuple(r):
                c.execute("UPDATE contact SET own_name=?, form=?, salutation=?, updated=?, ver=ver+1 WHERE id=?", (*fill, now, target))
                changed(c, uid, target)
                n += 1
            continue
        if not (own or form or sal or priv):
            continue                                      # ohne gemeinsamen Chat und ohne eigene Daten: verwerfen
        key = (own or "", frozenset((k, v) for k, v, _ in priv))
        if key in sigs or manual >= BOOK_MANUAL_MAX:
            continue
        free = [a for a in priv if not c.execute("SELECT 1 FROM contact_addr WHERE uid=? AND kind=? AND value=?", (uid, a[0], a[1])).fetchone()]
        if priv and not free:
            continue                                      # alle Adressen schon bei anderen Kontakten
        at = lambda k: float(x[k]) if isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) else now  # noqa: E731
        cid = c.execute("INSERT INTO contact (uid, origin, own_name, form, salutation, created, updated, ver) VALUES (?, 'manual', ?,?,?,?,?,1)",
                        (uid, own, form, sal, at("created"), now)).lastrowid
        _bk_add_addrs(c, uid, cid, free, now)
        sigs.add(key)
        manual += 1
        changed(c, uid, cid)
        n += 1
    return n
