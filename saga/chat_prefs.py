"""Chat-Alltag (2.5, Spezifikation A + C1): Stand je Person und Chat und die EINE Stelle, die ueber Mitteilungen entscheidet.

Stand je Person (Tabelle chat_pref, Migration 30), gesetzt ueber PATCH /api/chats/{cid}:
- muted_until (Feature mute2): Zeitpunkt (Unix-Sekunden), -1 = fuer immer, 0/NULL = aus. muted (bool) bleibt fuer aeltere
  Clients - abgeleitet ("jetzt stumm"); chat_member.muted ist der Spiegel davon (Anrufe, Archiv, Ungelesen-Zaehler und
  aeltere Versionen lesen ihn). Abgelaufene Fristen setzt expire() zurueck (vor Listen/Zaehlern und stuendlich).
- notify (mute2): all | mentions | none. "mentions" = nur bei Erwaehnung und bei Antworten auf eigene Nachrichten.
- pinned_at (chatpin1): angeheftet seit; hoechstens PIN_MAX je Person; /api/chats sortiert angeheftete oben.
- unread_mark (markunread1): als ungelesen markiert - zaehlt als 1 ungelesen, bis der Chat gelesen wird (POST .../read).
- hide_at (chathide1): Gruppen-/Seiten-Chat "fuer mich ausgeblendet" - nicht in /api/chats (ausser ?hidden=1), keine
  Mitteilungen, zaehlt nicht als ungelesen; kommt zurueck bei Erwaehnung, beim Oeffnen (POST /api/chats) oder hidden:false.
- mention_seq (chatmention1): neueste Nachricht, in der ich erwaehnt wurde (Feld mention_seq im Chat-Objekt).

Erwaehnungen (C1): Clients schicken neben dem Chiffrat mentions:[kennungen] (Handle '~x@host', lokale Kennung '~x' oder -
nur Chat v1 - der Benutzername aus der Mitgliederliste). Der Server nimmt nur aktuelle Mitglieder (sonst still ignoriert),
nie den Absender, hoechstens MENTIONS_MAX. Ueber Servergrenzen gehen sie als Feld mentions (Handles) mit dem Auftrag bzw.
Ereignis - nur an Gegenstellen mit chatmention1; jede Seite prueft die Mitgliedschaft selbst und meldet nur eigenen Konten.

Mitteilung je Nachricht: on_message() (Benachrichtigung + Push); push_ok() prueft beim Zustellen erneut. Keine zyklischen
Importe: nur db, bus, notify, sharing, core.
"""
import time

from saga import bus, notify, sharing
from saga.core import HANDLE_RE, local_ref, t

PIN_MAX = 5
MENTIONS_MAX = 50
NOTIFY = ("all", "mentions", "none")
HIDE_KINDS = ("group", "page")
FEATURES = ("mute2", "chatpin1", "markunread1", "chathide1", "chatmention1", "chatfmt1")
_COLS = "muted_until, notify, pinned_at, unread_mark, hide_at, mention_seq"


def _now():
    return time.time()


def active(muted_until, now=None):
    """Ist eine Stummschaltung jetzt wirksam?"""
    mu = muted_until or 0
    return mu == -1 or mu > (now or _now())


def get(c, cid, uid):
    r = c.execute(f"SELECT {_COLS} FROM chat_pref WHERE chat_id=? AND uid=?", (cid, uid)).fetchone()
    if not r:
        return {"muted_until": 0, "notify": "all", "pinned_at": None, "unread_mark": False, "hide_at": None, "mention_seq": 0}
    return {"muted_until": r[0] or 0, "notify": r[1] if r[1] in NOTIFY else "all", "pinned_at": r[2], "unread_mark": bool(r[3]),
            "hide_at": r[4], "mention_seq": r[5] or 0}


def _set(c, cid, uid, **kw):
    cols = ", ".join(kw)
    c.execute(f"INSERT INTO chat_pref (chat_id, uid, {cols}) VALUES (?,?,{','.join('?' * len(kw))}) ON CONFLICT (chat_id, uid) "
              f"DO UPDATE SET {', '.join(f'{k}=EXCLUDED.{k}' for k in kw)}", (cid, uid, *kw.values()))


def expire(c, uid=None, now=None):
    """Abgelaufene Stummschaltungen beenden (muted_until -> NULL, Spiegel chat_member.muted -> 0). uid=None: alle."""
    now = now or _now()
    q, a = "SELECT chat_id, uid FROM chat_pref WHERE muted_until>0 AND muted_until<=?", [now]
    if uid is not None:
        q, a = q + " AND uid=?", a + [uid]
    rows = c.execute(q + " LIMIT 5000", tuple(a)).fetchall()
    for cid, u in rows:
        c.execute("UPDATE chat_pref SET muted_until=NULL WHERE chat_id=? AND uid=?", (cid, u))
        c.execute("UPDATE chat_member SET muted=0 WHERE chat_id=? AND uid=?", (cid, u))
    return len(rows)


def muted_now(c, cid, uid):
    """Jetzt stumm? (Spiegel chat_member.muted, abgelaufene Frist zaehlt nicht mehr) - fuer Anrufe u. ae."""
    r = c.execute("SELECT m.muted, p.muted_until FROM chat_member m LEFT JOIN chat_pref p ON p.chat_id=m.chat_id AND p.uid=m.uid "
                  "WHERE m.chat_id=? AND m.uid=?", (cid, uid)).fetchone()
    if not r or not r[0]:
        return False
    return not (r[1] and r[1] > 0 and r[1] <= _now())


def unmuted_sql(m="m"):
    """SQL-Bedingung "nicht stumm" fuer Alias m (chat_member): Spiegel aus oder Frist abgelaufen."""
    return (f"({m}.muted=0 OR EXISTS (SELECT 1 FROM chat_pref mp WHERE mp.chat_id={m}.chat_id AND mp.uid={m}.uid "
            f"AND mp.muted_until>0 AND mp.muted_until<={_now()!r}))")


# --- Chat-Objekt ---------------------------------------------------------------------------------------------------
def fields(c, uid, ch, d):
    """Zusatzfelder fuer /api/chats und /api/chats/{cid} (d wird ergaenzt): muted (abgeleitet), muted_until, notify, pinned,
    pinned_at, unread_mark, hidden, can_hide, mention_seq, me_ref; unread mindestens 1 bei unread_mark."""
    p = get(c, ch["id"], uid)
    on = active(p["muted_until"]) or (bool(d.get("muted")) and not p["muted_until"])     # alter Stand ohne chat_pref
    d.update(muted=on, muted_until=(p["muted_until"] or (-1 if on else 0)) if on else 0, notify=p["notify"],
             pinned=p["pinned_at"] is not None, pinned_at=p["pinned_at"], unread_mark=p["unread_mark"],
             hidden=p["hide_at"] is not None, can_hide=ch["kind"] in HIDE_KINDS, mention_seq=p["mention_seq"],
             me_ref=d.get("my_handle") or local_ref(uid))     # eigene Kennung in Erwaehnungs-Token <@kennung> (nie Anmeldename)
    if p["unread_mark"] and not d.get("unread"):
        d["unread"] = 1
    return d


def sort_key(d):
    """Chatliste: angeheftete oben (in der Reihenfolge des Anheftens), dann neueste zuerst."""
    return (0, d["pinned_at"]) if d.get("pinned") else (1, -(d.get("last_at") or 0))


# --- PATCH /api/chats/{cid} ----------------------------------------------------------------------------------------
PATCH_KEYS = ("muted", "muted_until", "notify", "pinned", "unread_mark", "hidden")


def check(b, ch):
    """Felder pruefen (vor jeder Aenderung) -> Fehlertext oder None."""
    if "muted" in b and not isinstance(b["muted"], bool):
        return t("ungültige Angabe")
    if "muted_until" in b:
        v = b["muted_until"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not (v in (0, -1) or 0 < v < 2 ** 40):
            return t("ungültige Angabe")
    if "notify" in b and b["notify"] not in NOTIFY:
        return t("ungültige Angabe")
    for k in ("pinned", "unread_mark", "hidden"):
        if k in b and not isinstance(b[k], bool):
            return t("ungültige Angabe")
    if b.get("hidden") is True and ch["kind"] not in HIDE_KINDS:
        return t("Ausblenden gibt es nur für Gruppen- und Seiten-Chats.")
    return None


def patch(c, uid, ch, b):
    """Felder uebernehmen (nach check) -> (status, fehler) oder (None, None). Live chat.changed an meine Geraete macht der
    Aufrufer (geaendert = True)."""
    cid, now = ch["id"], _now()
    if not any(k in b for k in PATCH_KEYS):
        return None, None, False
    if "muted_until" in b or "muted" in b:
        mu = b["muted_until"] if "muted_until" in b else (-1 if b["muted"] else 0)
        mu = int(mu) if mu in (0, -1) else float(mu)
        if mu > 0 and mu <= now:
            mu = 0
        _set(c, cid, uid, muted_until=mu or None)
        c.execute("UPDATE chat_member SET muted=? WHERE chat_id=? AND uid=?", (1 if mu else 0, cid, uid))
    if "notify" in b:
        _set(c, cid, uid, notify=None if b["notify"] == "all" else b["notify"])
    if "pinned" in b:
        cur = get(c, cid, uid)
        if b["pinned"] and cur["pinned_at"] is None:
            n = c.execute("SELECT COUNT(*) FROM chat_pref p JOIN chat_member m ON m.chat_id=p.chat_id AND m.uid=p.uid AND m.left_at IS NULL "
                          "WHERE p.uid=? AND p.pinned_at IS NOT NULL", (uid,)).fetchone()[0]
            if n >= PIN_MAX:
                return 409, t("Höchstens {n} Chats anheften.", n=PIN_MAX), False
            _set(c, cid, uid, pinned_at=now)
        elif not b["pinned"]:
            _set(c, cid, uid, pinned_at=None)
    if "unread_mark" in b:
        _set(c, cid, uid, unread_mark=1 if b["unread_mark"] else 0)
    if "hidden" in b:
        _set(c, cid, uid, hide_at=now if b["hidden"] else None)
    return None, None, True


def on_read(c, cid, uid):
    """Chat gelesen: Markierung "ungelesen" weg."""
    c.execute("UPDATE chat_pref SET unread_mark=0 WHERE chat_id=? AND uid=? AND unread_mark<>0", (cid, uid))


def on_open(c, cid, uid):
    """Chat bewusst geoeffnet (POST /api/chats): ausgeblendeten Gruppen-/Seiten-Chat wieder zeigen."""
    c.execute("UPDATE chat_pref SET hide_at=NULL WHERE chat_id=? AND uid=? AND hide_at IS NOT NULL", (cid, uid))


def unread_extra_sql():
    """Zusatzbedingungen fuer Ungelesen-Zaehler (Alias m = chat_member): ausgeblendete zaehlen nicht."""
    return ("AND NOT EXISTS (SELECT 1 FROM chat_pref hp WHERE hp.chat_id=m.chat_id AND hp.uid=m.uid AND hp.hide_at IS NOT NULL) ")


def marked_count(c, uid):
    """Als ungelesen markierte Chats ohne echte ungelesene Nachricht (zaehlen je 1) - nicht stumm, nicht archiviert, nicht
    ausgeblendet."""
    return c.execute(
        "SELECT COUNT(*) FROM chat_pref p JOIN chat_member m ON m.chat_id=p.chat_id AND m.uid=p.uid AND m.left_at IS NULL AND " + unmuted_sql() +
        " AND m.state='ok' JOIN chat ch ON ch.id=p.chat_id WHERE p.uid=? AND p.unread_mark<>0 AND p.archived IS NULL AND p.hide_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM chat_msg x WHERE x.chat_id=p.chat_id AND x.kind='msg' AND x.seq>m.last_read "
        "AND (x.sender_uid IS NULL OR x.sender_uid<>p.uid))", (uid,)).fetchone()[0]


# --- Erwaehnungen --------------------------------------------------------------------------------------------------
def mentions_in(v):
    """Feld mentions aus der Anfrage -> (liste, fehler). Fehlt es: []. Kein Liste -> Fehler; ungueltige Eintraege fallen weg."""
    if v is None:
        return [], None
    if not isinstance(v, list):
        return None, t("ungültige Angabe")
    out = []
    for x in v[:MENTIONS_MAX * 2]:
        if isinstance(x, str) and 0 < len(x) <= 300 and x not in out:
            out.append(x)
    return out[:MENTIONS_MAX], None


def resolve(c, cid, refs, sender=None):
    """Kennungen -> lokale Konten DIESES Servers, die jetzt Mitglied des Chats sind (ohne Absender). Fremde Handles
    (anderer Server) zaehlen hier nicht - deren Server meldet seinen Konten selbst."""
    if not refs:
        return []
    mem = [r[0] for r in c.execute("SELECT uid FROM chat_member WHERE chat_id=? AND left_at IS NULL", (cid,))]
    loc = [u for u in mem if not sharing.is_remote(u)]
    by_ref = {local_ref(u): u for u in loc}
    from saga import federation as F                     # spaet: saga.federation ist gross (keine Zyklen beim Import)
    out = []
    for r in refs:
        r = r.strip()
        u = None
        if r in loc:                                     # Chat v1: Benutzername aus der Mitgliederliste
            u = r
        elif HANDLE_RE.fullmatch(r):
            u = by_ref.get(r) or F.uid_by_handle(c, r)
        elif "@" in r and HANDLE_RE.fullmatch(r.split("@", 1)[0]):
            n, _, host = r.partition("@")              # voller Handle (Stufe 3 auch ohne Foederation)
            if F.fedsig.authority(host) == F.SELF_HOST:
                u = F.uid_by_handle(c, n)
        if u and u in loc and u != sender and u not in out:
            out.append(u)
    return out


def handles_of(c, uids):
    """Lokale Konten -> volle Handles '~x@host' (fuer Auftraege/Ereignisse ueber Servergrenzen)."""
    from saga import federation as F
    if not F.enabled():
        return []
    return [f"{F.own_handle(c, u)}@{F.SELF_HOST}" for u in uids if not sharing.is_remote(u)]


# --- Die Entscheidung ------------------------------------------------------------------------------------------------
def decide(p, mentioned, reply_mine, now=None):
    """-> None | 'chat.message' | 'chat.mention' fuer EIN Mitglied mit Stand p (get()). Reihenfolge: stumm -> nichts;
    notify none -> nichts; ausgeblendet -> nur Erwaehnung; notify mentions -> nur Erwaehnung/Antwort auf eigene Nachricht."""
    if active(p["muted_until"], now) or p["notify"] == "none":
        return None
    if mentioned:
        return "chat.mention"
    if p["hide_at"] is not None:
        return None
    if p["notify"] == "mentions":
        return "chat.message" if reply_mine else None
    return "chat.message"


# 2.5 (saga.chat_grp setzt es ein, block2): (c, uid, sender) -> True, wenn uid den Absender gesperrt hat (lokal oder fremd)
BLOCKED = None


def on_message(c, cid, sender, seq, members, mentioned=(), reply_to=None, states=None):
    """Neue Nachricht: je lokalem Mitglied (ausser Absender, nur angenommene) Benachrichtigung + Push nach decide();
    Erwaehnte bekommen mention_seq und einen ausgeblendeten Chat zurueck. states: {uid: state} (Nachrichtenanfragen still).
    -> {uid: art} (fuer Tests/Protokoll)."""
    now = _now()
    expire(c, now=now)
    mine_reply = None
    if reply_to:
        r = c.execute("SELECT sender_uid FROM chat_msg WHERE id=? AND chat_id=?", (reply_to, cid)).fetchone()
        mine_reply = r[0] if r else None
    out = {}
    for u in members:
        if u == sender or sharing.is_remote(u) or (states and states.get(u, "ok") != "ok"):
            continue
        if BLOCKED is not None and BLOCKED(c, u, sender):
            continue                                     # gesperrt: keine Mitteilung, auch keine Erwaehnung (Chat bleibt, wie er ist)
        men = u in mentioned
        if men:
            c.execute("INSERT INTO chat_pref (chat_id, uid, mention_seq) VALUES (?,?,?) ON CONFLICT (chat_id, uid) DO UPDATE SET "
                      "mention_seq=GREATEST(chat_pref.mention_seq, EXCLUDED.mention_seq), hide_at=NULL", (cid, u, seq))
        kind = decide(get(c, cid, u), men, mine_reply == u, now)
        if not kind:
            continue
        out[u] = kind
        notify.create(c, u, kind, sender, data={"chat": cid}, bundle=cid)
        # Handy/Desktop (saga.push): nur die Chat-Kennung, nie Inhalt
        bus.fire("push.send", c, uid=u, kind=kind, actor=sender, data={"chat": cid})
    return out


def push_ok(c, cid, uid, kind):
    """Beim Zustellen (saga.push.message) erneut: noch Mitglied, nicht (mehr) stumm/aus; Nachricht nicht in ausgeblendetem
    Chat. Erwaehnungen auch in ausgeblendeten Chats."""
    if not c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND left_at IS NULL", (cid, uid)).fetchone():
        return False
    p = get(c, cid, uid)
    if active(p["muted_until"]) or p["notify"] == "none":
        return False
    if c.execute("SELECT 1 FROM chat_member WHERE chat_id=? AND uid=? AND muted<>0", (cid, uid)).fetchone() and not p["muted_until"]:
        return False                                     # Spiegel aus einer aelteren Version (ohne chat_pref)
    return kind == "chat.mention" or p["hide_at"] is None


from saga import push as _push  # noqa: E402 - saga.push kennt den Chat nicht (keine Zyklen)
_push.CHAT_PUSH_OK = push_ok
