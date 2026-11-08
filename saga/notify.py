"""Benachrichtigungen (1.4): "Anna hat eine Seite mit dir geteilt", "Bert hat deine Seite kommentiert" ...

- Eine Zeile je Empfaenger in notification (db.py). Gespeichert werden nur Kennungen (Seite, Kommentar, wer) und kleine
  Angaben (Rolle, Unterhaltung, Anzahl) - Titel, Namen und Kommentar-Auszug ermittelt _render() erst beim Lesen, und nur,
  wenn der Empfaenger die Seite JETZT sehen darf (sharing.Access). Nach Entzug: "Kein Zugriff mehr" statt Titel.
- Arten (KINDS): share.added, share.role, comment.new (neuer Kommentar auf eigener Seite), comment.reply (Antwort in
  einer Unterhaltung, an der man beteiligt ist), publish.withdrawn (Admin hat eine Veroeffentlichung zurueckgezogen),
  db.assigned (2.5: in einer Personen-Eigenschaft einer Datenbank-Zeile eingetragen, gebuendelt je Zeile),
  page.mention (2.5, P6-5: in einer Seite erwaehnt - einmal je Seite und Person, saga.mentions) und comment.mention
  (in einem Kommentar erwaehnt, Token "<@ref>" - statt comment.new/comment.reply fuer diesen Kommentar).
- Nie an sich selbst; je Art abschaltbar (user_kv 'notify_off'); mehrere Kommentare derselben Unterhaltung innerhalb von
  10 Minuten werden zu EINER (ungelesenen) Benachrichtigung gebuendelt (Zaehler n, zuletzt Schreibende vorn).
- Ausgeloest ueber saga.bus (sharing/comments/publish melden, hier wird angelegt - in derselben Transaktion);
  live zugestellt ueber den Live-Kanal (saga.live, Ereignis "notification") erst nach dem Commit; aufs Handy/Desktop
  ueber saga.push (bus "notification.created", 1.7).
- Aufbewahrung 90 Tage (purge() beim Start und taeglich), Kontoloeschung: delete_account()."""
import json
import time
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from db import connect as _db
from saga import bus
from saga.core import _api_guard, _bad, _body, _json_load, t, ref_of
from saga.sharing import Access, is_remote, names
from saga.sharing import unread_count as pages_unread

router = APIRouter()

KINDS = ("share.added", "share.role", "comment.new", "comment.reply", "publish.withdrawn", "chat.message", "call.missed",
         "db.assigned", "page.mention", "comment.mention", "chat.mention")   # abschaltbar, in dieser Reihenfolge (2.5: Datenbank-Zeile, Erwaehnungen)
FUTURE = ()
RETENTION_DAYS = 90
BUNDLE_SEC = 600                    # Kommentare derselben Unterhaltung innerhalb 10 Minuten -> eine Benachrichtigung
EXCERPT = 120
PAGE_MAX = 100
GROUP_MAX = 1000                    # Freigabe an eine Gruppe: hoechstens so viele Mitglieder benachrichtigen
RENDER = {}                         # Art -> fn(c, uid, eintrag, daten): Titel/Link fuer Arten ohne Seite (saga.chat meldet sich an)
# Von saga.federation gesetzt: fed_render(c, uid, fed) -> (zugriff, {id, title, icon}, url) fuer Benachrichtigungen zu Seiten
# anderer Instanzen (data.fed = {peer, note, title, comment, thread, block}); ohne Foederation: kein Zugriff
FED_RENDER = None
ROLE_KEYS = {"view": "Lesen##Rolle", "comment": "Kommentieren##Rolle", "edit": "Bearbeiten##Rolle"}
# Von saga.mentions gesetzt: fn(c, owner, note_id, comment_id, author) -> Personen, die im Kommentar erwaehnt sind (mit Zugriff)
COMMENT_MENTIONED = None
COMMENT_TEXT = None                 # fn(c, body) -> Text mit '@Name' statt Token (saga.mentions)
# Design-Runde (UX-29/30): Zeilen der Glocke je Gespraechseinheit buendeln (?group=1) - Art -> Einheit
GROUP_KINDS = {"chat.message": "chat", "call.missed": "call", "comment.new": "page", "comment.reply": "page",
               "comment.mention": "page"}
GROUP_SCAN = 500                    # hoechstens so viele Zeilen je Abfrage zu Gruppen zusammenfassen
GROUP_NAMES = 3                     # "(von A, B, C …)"
SEQS_MAX = 200                      # 2.5: so viele gebuendelte Chat-Nachrichten (seq) merkt sich ein Eintrag (Loeschen zieht ab)
CHAT_KINDS = ("chat.message", "chat.mention")


# --- Einstellungen je Art ----------------------------------------------------------------------------
def _off(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k='notify_off'", (uid,)).fetchone()
    return set(_json_load(r[0], [])) if r else set()


def wants(c, uid, kind):
    return kind not in _off(c, uid)


# --- Anlegen -----------------------------------------------------------------------------------------
def create(c, uid, kind, actor=None, note_id=None, comment_id=None, data=None, bundle=None):
    """Benachrichtigung fuer uid anlegen (in der Transaktion von c) -> id oder None (an sich selbst / abgeschaltet).
    bundle: Kennung der Unterhaltung - eine ungelesene Benachrichtigung derselben Art und Unterhaltung aus den letzten
    10 Minuten wird fortgeschrieben (n+1, neuester Kommentar, Schreibende) statt eine neue anzulegen."""
    if not uid or (actor and uid == actor):
        return None
    if is_remote(uid):            # Person einer anderen Instanz: dort benachrichtigen (saga.federation, signiertes Ereignis)
        bus.fire("fed.notify", c, uid=uid, kind=kind, actor=actor, note_id=note_id, comment_id=comment_id, data=data or {})
        return None
    if not wants(c, uid, kind):
        return None
    now = time.time()
    data = dict(data or {})
    seq = data.pop("seq", None)       # 2.5: Chat - welche Nachrichten gebuendelt sind (data.seqs), fuer drop_seq()
    old = None
    if seq is not None:
        data["seqs"] = [seq]
    if bundle is not None:
        data.update(thread=bundle, n=1, actors=[actor] if actor else [])
        for rid, raw in c.execute("SELECT id, data FROM notification WHERE uid=? AND kind=? AND note_id=? AND read_at IS NULL AND created>? "
                                  "ORDER BY id DESC LIMIT 20", (uid, kind, note_id, now - BUNDLE_SEC)).fetchall() if note_id is not None else \
                c.execute("SELECT id, data FROM notification WHERE uid=? AND kind=? AND note_id IS NULL AND read_at IS NULL AND created>? "
                          "ORDER BY id DESC LIMIT 20", (uid, kind, now - BUNDLE_SEC)).fetchall():
            d = _json_load(raw, {})
            if d.get("thread") == bundle:
                # fortschreiben: alte Zeile weg, neue oben (Reihenfolge = id, Blaettern mit ?before=<id> bleibt stabil)
                actors = [a for a in d.get("actors", []) if a != actor]
                data.update(n=int(d.get("n") or 1) + 1, actors=([actor] if actor else []) + actors[:4])
                if seq is not None:
                    data["seqs"] = ([s for s in d.get("seqs") or [] if s != seq] + [seq])[-SEQS_MAX:]
                c.execute("DELETE FROM notification WHERE id=?", (rid,))
                old = rid
                break
    nid = c.execute("INSERT INTO notification (uid, kind, actor_uid, note_id, comment_id, data, created) VALUES (?,?,?,?,?,?,?)",
                    (uid, kind, actor, note_id, comment_id, json.dumps(data), now)).lastrowid
    bus.emit_after(c, "notification", {"uid": uid, "id": nid, "replaces": old})
    bus.fire("notification.created", c, uid=uid, id=nid, kind=kind, actor=actor, replaces=old)    # saga.push (Handy/Desktop)
    return nid


def chat_unread(c, uid, kind, chat):
    """2.5: (Anzahl, juengste Person) der ungelesenen Benachrichtigungen dieser Art zu einem Chat (Summe der Buendel-Zaehler)."""
    n, who = 0, None
    for actor, raw in c.execute("SELECT actor_uid, data FROM notification WHERE uid=? AND kind=? AND note_id IS NULL AND read_at IS NULL "
                                "ORDER BY id DESC LIMIT 50", (uid, kind)).fetchall():
        if str(_json_load(raw, {}).get("chat")) == str(chat):
            n += int(_json_load(raw, {}).get("n") or 1)
            who = who or actor
    return n, who


def drop_seq(c, uids, chat, seq):
    """2.5: Chat-Nachricht seq wurde fuer alle geloescht -> in den Glocken-Eintraegen dieser Konten (chat.message/chat.mention
    mit dieser Nachricht im Buendel) n verringern bzw. den Eintrag entfernen, wenn er leer wird. Live-Ereignis
    "notification.changed" je Eintrag (nach dem Commit). -> [(uid, id, kind, entfernt, war_ungelesen)].
    Eintraege von vor 2.5 (ohne seqs) bleiben, wie sie sind."""
    uids = sorted({u for u in uids or () if u and not is_remote(u)})
    if not uids or seq is None:
        return []
    out = []
    rows = c.execute(f"SELECT id, uid, kind, data, read_at FROM notification WHERE uid IN ({','.join('?' * len(uids))}) "
                     "AND kind IN (?,?) AND note_id IS NULL AND data LIKE ?", (*uids, *CHAT_KINDS, '%"seqs": [%')).fetchall()
    for rid, u, kind, raw, read_at in rows:
        d = _json_load(raw, {})
        if str(d.get("chat")) != str(chat) or seq not in (d.get("seqs") or []):
            continue
        d["seqs"] = [s for s in d["seqs"] if s != seq]
        n = int(d.get("n") or 1) - 1
        if n <= 0:
            c.execute("DELETE FROM notification WHERE id=?", (rid,))
        else:
            d["n"] = n
            c.execute("UPDATE notification SET data=? WHERE id=?", (json.dumps(d), rid))
        out.append((u, rid, kind, n <= 0, read_at is None))
        bus.emit_after(c, "notification.changed", {"uid": u, "id": rid, "removed": n <= 0, "group_key": group_key(kind, None, d)})
    return out


def group_members(c, name, limit=GROUP_MAX):
    """Aktive Konten einer Gruppe (lokal oder aus LDAP/OIDC)."""
    return [r[0] for r in c.execute(
        "SELECT u.username FROM users u WHERE u.disabled=0 AND (EXISTS (SELECT 1 FROM user_group ug JOIN groups g ON g.id=ug.group_id "
        "WHERE ug.user_id=u.id AND g.name=?) OR EXISTS (SELECT 1 FROM user_ext_group e WHERE e.user_id=u.id AND e.name=?)) "
        "ORDER BY u.username LIMIT ?", (name, name, limit))]


def _on_share(c, owner, note_id, target_type, target, role, old_role=None, actor=None, **_):
    """Freigabe angelegt (old_role None) oder Rolle geaendert."""
    if old_role == role or target_type not in ("user", "group"):     # andere Instanzen: saga.federation
        return
    kind = "share.role" if old_role else "share.added"
    who = [target] if target_type == "user" else group_members(c, target)
    for u in who:
        create(c, u, kind, actor or owner, note_id, data={"role": role})


def _on_comment(c, note_id, owner, comment_id, thread_id, author, **_):
    """Neuer Kommentar: Beteiligte der Unterhaltung -> comment.reply, Eigentuemer der Seite (wenn nicht beteiligt) ->
    comment.new. Nur, wer die Seite gerade sehen darf."""
    parts = {r[0] for r in c.execute("SELECT DISTINCT uid FROM note_comment WHERE (id=? OR thread_id=?) AND uid IS NOT NULL",
                                     (thread_id, thread_id))} - {author}
    ment = COMMENT_MENTIONED(c, owner, note_id, comment_id, author) if COMMENT_MENTIONED else []
    for u in ment:                     # 2.5 (P6-5): erwaehnt -> comment.mention statt comment.new/comment.reply
        create(c, u, "comment.mention", author, note_id, comment_id)
    parts -= set(ment)
    for u in sorted(parts):
        if u == owner or Access(c, u).role(note_id):
            create(c, u, "comment.reply", author, note_id, comment_id, bundle=thread_id)
    if owner and owner != author and owner not in parts and owner not in ment:
        create(c, owner, "comment.new", author, note_id, comment_id, bundle=thread_id)


def _on_assigned(c, owner, note_id, people, actor=None, notify=True, **_):
    """2.5 (Paket 1): Person(en) in einer Datenbank-Zeile eingetragen (saga.dbvals.assigned) -> db.assigned, gebuendelt je
    Zeile (mehrere Eintragungen derselben Zeile innerhalb von 10 Minuten = eine Mitteilung). Nur mit Zugriff auf die Zeile;
    abschaltbar je Konto (Art) und je Eigenschaft (notify: false)."""
    if not notify:
        return
    for u in people:
        if u == actor:
            continue
        if is_remote(u) or u == owner or Access(c, u).role(note_id):
            create(c, u, "db.assigned", actor, note_id, bundle=f"row:{note_id}")


def _on_withdrawn(c, note_id, owner, **_):
    """Admin hat eine Veroeffentlichung zurueckgezogen - der Eigentuemer erfaehrt es (ohne Namen des Admins)."""
    create(c, owner, "publish.withdrawn", None, note_id)


bus.on("share.set", _on_share)
bus.on("comment.added", _on_comment)
bus.on("row.assigned", _on_assigned)
bus.on("publish.withdrawn", _on_withdrawn)


# --- Aufraeumen ----------------------------------------------------------------------------------------
def purge(c=None, days=RETENTION_DAYS):
    """Benachrichtigungen aelter als 90 Tage loeschen -> Anzahl."""
    if c is None:
        with _db() as c2, c2:
            return purge(c2, days)
    return c.execute("DELETE FROM notification WHERE created < ?", (time.time() - days * 86400,)).rowcount


def delete_account(c, uid):
    """Kontoloeschung: eigene Benachrichtigungen weg (auch ueber USER_TABLES), als Ausloeser anonymisieren -
    ein spaeteres Konto mit gleichem Namen taucht nie als "hat geteilt" auf. Gebuendelte Namen ebenso."""
    n = c.execute("DELETE FROM notification WHERE uid=?", (uid,)).rowcount
    c.execute("UPDATE notification SET actor_uid=NULL WHERE actor_uid=?", (uid,))
    for rid, raw in c.execute("SELECT id, data FROM notification WHERE data LIKE ?", ('%"actors"%' + json.dumps(uid) + "%",)).fetchall():
        d = _json_load(raw, {})
        if uid in d.get("actors", []):
            d["actors"] = [a for a in d["actors"] if a != uid]
            c.execute("UPDATE notification SET data=? WHERE id=?", (json.dumps(d), rid))
    return n


def count_unread(c, uid):
    return c.execute("SELECT COUNT(*) FROM notification WHERE uid=? AND read_at IS NULL", (uid,)).fetchone()[0]


def group_key(kind, note_id, d):
    """Design-Runde (UX-29/30): Gespraechseinheit einer Benachrichtigung -> 'chat:<id>' (Nachrichten eines Chats),
    'call:<chat>' (verpasste Anrufe eines Chats), 'page:<seite>' bzw. 'page:<instanz>:<seite>' (Kommentare und
    Erwaehnungen in Kommentaren auf einer Seite) oder None (wird nicht gebuendelt)."""
    g = GROUP_KINDS.get(kind)
    if not g:
        return None
    if g != "page":
        ch = d.get("chat")
        return f"{g}:{ch}" if ch not in (None, "") else None
    fed = d.get("fed") if isinstance(d.get("fed"), dict) else None
    if fed:
        return f"page:{fed.get('peer')}:{fed.get('note')}" if fed.get("note") not in (None, "") else None
    return f"page:{note_id}" if note_id else None


def _unread_keys(c, uid, limit=2000):
    """(id, Schluessel) der ungelesenen Benachrichtigungen (neueste zuerst)."""
    return [(r[0], group_key(r[1], r[2], _json_load(r[3], {}))) for r in c.execute(
        "SELECT id, kind, note_id, data FROM notification WHERE uid=? AND read_at IS NULL ORDER BY id DESC LIMIT ?", (uid, limit))]


def count_unread_groups(c, uid):
    """Ungelesene Zeilen der gebuendelten Glocke (eine je Gespraechseinheit, sonst je Benachrichtigung)."""
    return len({k or ("id", rid) for rid, k in _unread_keys(c, uid)})


# --- Ausgabe (je Empfaenger) ---------------------------------------------------------------------------
N_COLS = "id, kind, actor_uid, note_id, comment_id, data, created, read_at"


def _text(kind, actor, n, role, access):
    """Kurzer Satz (ohne Titel - der steht in einer eigenen Zeile) in der Sprache der Anfrage. Die Weboberflaeche setzt
    ihn mit denselben Schluesseln selbst zusammen; Apps koennen 'text' direkt anzeigen."""
    who = actor or t("Gelöschtes Konto")
    if kind == "share.added":
        return t("{name} hat eine Seite mit dir geteilt ({role})", name=who, role=t(ROLE_KEYS.get(role, "Lesen##Rolle")))
    if kind == "share.role":
        return t("{name} hat deine Rolle geändert: {role}", name=who, role=t(ROLE_KEYS.get(role, "Lesen##Rolle")))
    if kind == "comment.new":
        return t("{n} neue Kommentare auf deiner Seite", n=n) if n > 1 else t("{name} hat deine Seite kommentiert", name=who)
    if kind == "comment.reply":
        return t("{n} neue Antworten in einer Unterhaltung", n=n) if n > 1 else t("{name} hat in einer Unterhaltung geantwortet", name=who)
    if kind == "comment.mention":
        return t("{name} hat dich in einem Kommentar erwähnt", name=who)
    if kind == "page.mention":
        return t("{name} hat dich erwähnt", name=who)
    if kind == "publish.withdrawn":
        return t("Ein Admin hat deine Veröffentlichung zurückgezogen")
    if kind == "chat.message":
        return t("{n} neue Nachrichten", n=n) if n > 1 else t("Neue Nachricht von {name}", name=who)
    if kind == "call.missed":
        return t("Verpasster Anruf von {name}", name=who)
    if kind == "chat.mention":
        return t("{name} hat dich in einem Chat erwähnt", name=who)
    if kind == "db.assigned":
        return t("{name} hat dich eingetragen", name=who) if n <= 1 else t("{name} hat dich {n}-mal eingetragen", name=who, n=n)
    return kind


def _render(c, uid, rows, acc=None):
    """Zeilen -> Eintraege fuer GENAU diesen Empfaenger: Titel/Auszug nur mit Zugriff (jetzt), sonst access=false."""
    acc = acc or Access(c, uid)
    nids = sorted({r[3] for r in rows if r[3]})
    cids = sorted({r[4] for r in rows if r[4]})
    notes = {}
    if nids:
        notes = {r[0]: r[1:] for r in c.execute(f"SELECT id, title, icon, deleted_at FROM note WHERE id IN ({','.join('?' * len(nids))})",
                                                tuple(nids))}
    cms = {}
    if cids:
        cms = {r[0]: r[1:] for r in c.execute(f"SELECT id, note_id, body, block_id, thread_id FROM note_comment WHERE id IN ({','.join('?' * len(cids))})",
                                              tuple(cids))}
    datas = [_json_load(r[5], {}) for r in rows]
    who = names(c, [r[2] for r in rows if r[2]] + [a for d in datas for a in d.get("actors", [])])
    out = []
    for r, d in zip(rows, datas):
        rid, kind, actor, nid, cid, _, created, read_at = r
        n = notes.get(nid)
        access = bool(n and n[2] is None and acc.role(nid))
        fed = d.get("fed") if isinstance(d.get("fed"), dict) else None
        if fed:                               # Seite einer anderen Instanz (Titel kam mit dem signierten Ereignis)
            access, fnote, furl = FED_RENDER(c, uid, fed) if FED_RENDER else (False, None, None)
        a_name = who.get(actor) if actor else None
        item = {"id": rid, "kind": kind, "created": created, "read": read_at is not None,
                "actor": {"name": a_name, "username": ref_of(c, actor, uid), "avatar": who.avatar(actor)} if a_name else None,
                "count": int(d.get("n") or 1), "access": access, "note": None, "url": None}
        if kind.startswith("share."):
            item["role"] = d.get("role")
        if len(d.get("actors", [])) > 1:
            item["actors"] = [who[a] for a in d["actors"] if a in who]
        if fed:
            if access:
                item["note"], item["url"] = fnote, furl
                if fed.get("comment"):
                    item.update(comment_id=fed.get("comment"), thread_id=fed.get("thread") or fed.get("comment"), block_id=fed.get("block") or "")
            item["remote"] = True
            n = (fnote["title"], "", None) if access else None
        elif access:
            item["note"] = {"id": nid, "title": n[0] or "", "icon": n[1] or ""}
            item["url"] = f"/notes#{nid}"
            cm = cms.get(cid)
            if cm and cm[0] == nid:
                body = cm[1] or ""
                if body and "<@" in body and COMMENT_TEXT:
                    body = COMMENT_TEXT(c, body)
                if body:
                    item["excerpt"] = body[:EXCERPT] + ("…" if len(body) > EXCERPT else "")
                item.update(comment_id=cid, thread_id=cm[3] or cid, block_id=cm[2] or "")
                if cm[2]:
                    item["url"] = f"/notes#{nid}:{cm[2]}"
            elif kind == "page.mention" and isinstance(d.get("block"), str) and d["block"]:
                item["block_id"] = d["block"]          # 2.5 (P6-5): Sprung zum Block mit der Erwaehnung
                item["url"] = f"/notes#{nid}:{d['block']}"
        item["group_key"] = group_key(kind, nid, d)
        item["text"] = _text(kind, a_name, item["count"], d.get("role"), access)
        item["title"] = (n[0] or t("Unbenannt")) if access else t("Kein Zugriff mehr")
        if kind in RENDER:
            RENDER[kind](c, uid, item, d)
        out.append(item)
    return out


def _group_text(g):
    """Satz fuer eine gebuendelte Zeile (mehr als eine Benachrichtigung) - ohne Titel (der steht in 'title')."""
    n, unit = g["count"], g["group_key"].split(":", 1)[0]
    if unit == "chat":
        if (g.get("chat") or {}).get("kind") not in (None, "dm") and g.get("actors"):
            return t("{n} neue Nachrichten (von {names})", n=n, names=_names_short(g["actors"]))
        return t("{n} neue Nachrichten", n=n)
    if unit == "call":
        return t("{n} verpasste Videoanrufe", n=n) if g.get("media") == "video" else t("{n} verpasste Anrufe", n=n)
    if g.get("mention"):
        return t("{n} neue Kommentare (mit Erwähnung)", n=n)
    return t("{n} neue Kommentare", n=n)


def _names_short(names_):
    return ", ".join(names_[:GROUP_NAMES]) + (" …" if len(names_) > GROUP_NAMES else "")


def grouped(items):
    """Design-Runde (UX-29/30): Eintraege (neueste zuerst) je Gespraechseinheit und Lesestand zu EINER Zeile buendeln.
    Die Zeile ist der neueste Eintrag (alle bisherigen Felder) plus ids/counts (alle gebuendelten Benachrichtigungen, neueste
    zuerst), count (Summe), first (Zeit der aeltesten), actors (Namen, neueste zuerst) und text fuer die ganze Gruppe.
    Eintraege ohne group_key bleiben einzeln (ids = [id])."""
    out, at = [], {}
    for it in items:
        k = it.get("group_key")
        nm = it.get("actors") or ([it["actor"]["name"]] if it.get("actor") else [])
        g = at.get((k, it["read"])) if k else None
        if g is None:
            g = dict(it, ids=[it["id"]], counts=[it["count"]], first=it["created"])
            g["actors"] = list(nm)
            if it["kind"] == "comment.mention":
                g["mention"] = True
            if k:
                at[(k, it["read"])] = g
            out.append(g)
            continue
        g["ids"].append(it["id"])
        g["counts"].append(it["count"])
        g["count"] += it["count"]
        g["first"] = it["created"]
        g["actors"] += [a for a in nm if a not in g["actors"]]
        if it["kind"] == "comment.mention":
            g["mention"] = True
        if g.get("media") != it.get("media"):
            g["media"] = "audio" if g.get("media") or it.get("media") else None    # gemischt: "Anrufe"
    for g in out:
        if len(g["ids"]) > 1:
            g["text"] = _group_text(g)
        if len(g["actors"]) <= 1:
            g.pop("actors")                          # wie bisher: actors nur bei mehreren Personen
    return out


def _group_rows(raw, limit):
    """Zeilen (neueste zuerst, hoechstens GROUP_SCAN+1) -> (zeilen, more, next) fuer hoechstens limit Gruppen. Bricht vor
    der ersten Gruppe ab, die nicht mehr passt; aeltere Mitglieder schon gezeigter Gruppen kommen dann mit der naechsten
    Seite (?before=next) - die Weboberflaeche fuegt sie der vorhandenen Zeile hinzu."""
    capped = len(raw) > GROUP_SCAN
    raw = raw[:GROUP_SCAN]
    keys = set()
    for r in raw:
        k = group_key(r[1], r[3], _json_load(r[5], {}))
        kk = (k, r[7] is not None) if k else ("id", r[0])
        if kk in keys:
            continue
        if len(keys) == limit:
            return [x for x in raw if x[0] > r[0]], True, r[0] + 1
        keys.add(kk)
    return raw, capped, (raw[-1][0] if capped and raw else None)


def _locked(c, uid, item, d):
    """2.4: harte Anmelde-Sperre nach sehr vielen Fehlversuchen von vielen Adressen (auth.note_fail)."""
    item["access"] = True
    item["system"] = True
    item["text"] = t("Jemand hat sehr oft versucht, sich mit falschem Passwort bei deinem Konto anzumelden ({n} Versuche von {ips} Adressen). Die Anmeldung mit Passwort ist für eine Stunde gesperrt; Passkeys und bestehende Sitzungen gehen weiter. Wähle ein sicheres Passwort – ein Admin kann die Sperre aufheben.",
                     n=d.get("fails", 0), ips=d.get("ips", 0))
    item["title"] = t("Anmeldung gesperrt")
    item["url"] = "/security"


RENDER["login.locked"] = _locked


def item(c, uid, rid):
    """Eine Benachrichtigung fuer den Live-Kanal (oder None, wenn es sie nicht mehr gibt)."""
    r = c.execute(f"SELECT {N_COLS} FROM notification WHERE id=? AND uid=?", (rid, uid)).fetchone()
    return _render(c, uid, [r])[0] if r else None


# --- Endpunkte -------------------------------------------------------------------------------------------
@router.get("/api/notifications")
def api_notifications(request: Request, unread: int = 0, before: int = 0, limit: int = 30, counts: int = 0, group: int = 0):
    """Liste (neueste zuerst): ?unread=1 nur ungelesene, ?before=<id> naechste Seite, ?limit= (hoechstens 100; 0 = nur Zahlen).
    -> {items, unread, more, next}; ?counts=1 zusaetzlich comments_unread (Seiten mit neuen Kommentaren).
    ?group=1 (Design-Runde): je Gespraechseinheit eine Zeile (grouped(); limit zaehlt Zeilen) und unread_groups."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    limit = max(0, min(PAGE_MAX, limit))
    with _db() as c:
        sql = f"SELECT {N_COLS} FROM notification WHERE uid=?"
        args = [uid]
        if unread:
            sql += " AND read_at IS NULL"
        if before > 0:
            sql += " AND id < ?"
            args.append(min(before, 2 ** 63 - 1))
        if group and limit:
            rows, more, nxt = _group_rows(c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, GROUP_SCAN + 1)).fetchall(), limit)
            items = grouped(_render(c, uid, rows))
        else:
            rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall() if limit else []
            more = len(rows) > limit
            rows = rows[:limit]
            nxt = rows[-1][0] if more and rows else None
            items = _render(c, uid, rows)
        out = {"items": items, "unread": count_unread(c, uid), "more": more, "next": nxt}
        if group:
            out["unread_groups"] = count_unread_groups(c, uid)
        if counts:
            out["comments_unread"] = pages_unread(c, uid)       # Startseite: Seiten mit neuen Kommentaren (Knopf "Seiten")
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.post("/api/notifications/read")
async def api_notifications_read(request: Request):
    """Als gelesen markieren: {ids: [..]} oder {all: true}; Design-Runde: auch {groups: ["chat:12", ...]} (alle
    ungelesenen dieser Gespraechseinheiten, group_key), mit ids kombinierbar. -> {ok, unread, unread_groups}"""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    now = time.time()
    with _db() as c, c:
        if b.get("all") is True:
            c.execute("UPDATE notification SET read_at=? WHERE uid=? AND read_at IS NULL", (now, uid))
            ids = "all"
        else:
            groups = b.get("groups")
            ids = b.get("ids", [] if groups is not None else None)
            if not isinstance(ids, list) or len(ids) > 500:
                return _bad(t("ungültige Angabe"))
            if groups is not None and (not isinstance(groups, list) or len(groups) > 100
                                       or any(not isinstance(g, str) or not g or len(g) > 200 for g in groups)):
                return _bad(t("ungültige Angabe"))
            try:
                ids = sorted({int(x) for x in ids if 0 < int(x) < 2 ** 63})
            except (TypeError, ValueError):
                return _bad(t("ungültige Angabe"))
            if groups:
                gs = set(groups)
                ids = sorted(set(ids) | {rid for rid, k in _unread_keys(c, uid, 5000) if k in gs})
            if ids:
                c.execute(f"UPDATE notification SET read_at=? WHERE uid=? AND read_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                          (now, uid, *ids))
        n, ng = count_unread(c, uid), count_unread_groups(c, uid)
        bus.emit_after(c, "notification.read", {"uid": uid, "ids": ids, "unread": n, "unread_groups": ng})    # andere Tabs/Apps
    return JSONResponse({"ok": True, "unread": n, "unread_groups": ng})


def _settings(c, uid):
    off = _off(c, uid)
    return {"kinds": [{"kind": k, "on": k not in off} for k in KINDS]}


@router.get("/api/notifications/settings")
def api_notify_settings(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        return JSONResponse(_settings(c, uid), headers={"Cache-Control": "no-store"})


@router.put("/api/notifications/settings")
async def api_notify_settings_put(request: Request):
    """Je Art an/aus: {"share.added": false, ...} (auch {"settings": {...}}); unbekannte Arten -> 400."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    b = b.get("settings") if isinstance(b.get("settings"), dict) else b
    if not b or any(k not in KINDS or not isinstance(v, bool) for k, v in b.items()):
        return _bad(t("ungültige Angabe"))
    with _db() as c, c:
        off = _off(c, uid)
        for k, v in b.items():
            (off.discard if v else off.add)(k)
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, 'notify_off', ?)", (uid, json.dumps(sorted(off))))
        return JSONResponse(_settings(c, uid))
