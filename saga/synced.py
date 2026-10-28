"""Synchronisierte Bloecke (2.5, P6-6; Konzept P6 Abschnitt 6, Entscheidung P6-E5 A).

Modell
- Original: Block {type: "synced", id, data: {sid, blocks: [...]}} in Seite A - der Inhalt lebt dort (Versionen, Suche,
  Rueckverweise, Erwaehnungen zaehlen fuer A).
- Kopie: {type: "synced", id, data: {sid}} in Seite B - OHNE Inhalt im gespeicherten body. Beim Lesen setzt der Server
  data.blocks und data.src je Betrachter ein (expand): mit Zugriff auf A den aktuellen Inhalt, ohne Zugriff nur den
  Platzhalter src.noaccess (kein Titel, kein Inhalt, keine Seiten-ID).
- Tabellen (db.py): synced_block (sid -> Seite/Block des Originals, Eigentuemer, letzter Inhalt als Zwischenspeicher, rev
  der Seite dazu, gone_at = Original fehlt) und synced_ref (Seiten mit Kopien) - gepflegt in prepare/after_save in
  derselben Transaktion wie das Speichern; blocks/move fuehrt sie nach (moved).
- sid: 16 Zeichen [A-Za-z0-9]. Ein neues Original darf seine sid mitbringen (Web erzeugt sie), sonst vergibt der Server
  eine. Eine schon vergebene sid mit Original auf einer ANDEREN Seite macht den Block zur Kopie.

data.src beim Lesen (nie gespeichert; docs/APP-API.md "Synchronisierte Bloecke"):
  Original: {original: true, refs: N}                 Kopie mit Zugriff: {note: A, block, role, rev, refs: N}
  Original geloescht (Zugriff): {gone: true, ...}       Kein Zugriff: {noaccess: true}     Unbekannt: {missing: true}

Bearbeiten in der Kopie (Stufe 1 / JSON, Faehigkeit sync1): data.blocks wird per saga.merge in das Original
durchgeschrieben (Basis = Stand von A zu src.rev, aus note_base), sofern die speichernde Person 'Bearbeiten' auf A hat
und A nicht gesperrt ist; sonst ignoriert. Clients/Gegenstellen ohne sync1 (aeltere Apps) -> data.blocks ignoriert.
Danach: A rev+1, Version, Basis, Erwaehnungen, Live-Ereignisse an A und alle Seiten mit Kopien, Webhook page.updated fuer A.

Rechte beim Anlegen: eine NEUE Kopie (sid stand vorher nicht in der Seite) braucht mindestens 'Lesen' auf A - sonst faellt
der Block weg. Veroeffentlichen (publish_blocks): Inhalt nur, wenn A demselben Konto gehoert oder selbst veroeffentlicht ist.
Foederation: Kopien nur auf Seiten derselben Instanz (sids gelten je Server); fremde Betrachter bekommen den Inhalt nach
ihren Rechten auf A (note_get mit dem Prinzipal). Gegenstellen ohne sync1 sehen eine Rueckfall-Karte."""
import json
import re
import secrets
import time

from saga import bus
from saga.core import t
from saga.notes import _iter_blocks, base_of, ensure_ids, keep_base, _snapshot, _check_file_refs
from saga.sharing import RANK, Access, mask_links, viewer

SID_RE = re.compile(r"[A-Za-z0-9]{16}")
_SID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
CONTENT_MAX = 100_000                      # Zeichen JSON je synchronisiertem Inhalt (Zwischenspeicher)
REFS_FIRE_MAX = 200                        # Live-Ereignisse an hoechstens so viele Seiten mit Kopien


def new_sid():
    return "".join(secrets.choice(_SID_CHARS) for _ in range(16))


def _lists(blocks, depth=0):
    """Alle Blocklisten (oberste Ebene, Spalten, Container) -> [liste]."""
    out = [blocks]
    if depth > 8:
        return out
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict) or not isinstance(b.get("data"), dict):
            continue
        d = b["data"]
        if b.get("type") == "columns" and isinstance(d.get("cols"), list):
            for col in d["cols"]:
                if isinstance(col, dict) and isinstance(col.get("blocks"), list):
                    out += _lists(col["blocks"], depth + 1)
        elif b.get("type") in ("toggle", "synced") and isinstance(d.get("blocks"), list):
            out += _lists(d["blocks"], depth + 1)
    return out


def _synced(doc):
    """Alle synced-Bloecke eines Dokuments (auch in Spalten/Ausklappbar) in Dokument-Reihenfolge."""
    try:
        return [b for b in _iter_blocks(doc.get("blocks") or []) if b.get("type") == "synced" and isinstance(b.get("data"), dict)]
    except (ValueError, RecursionError):
        return []


def sids_in(body):
    """sids aller synced-Bloecke eines gespeicherten Inhalts."""
    if not body or '"synced"' not in body:
        return []
    try:
        return [b["data"].get("sid") for b in _synced(json.loads(body)) if isinstance(b["data"].get("sid"), str)]
    except (ValueError, AttributeError):
        return []


def _flatten_nested(doc):
    """Synchronisierte Bloecke IN synchronisierten Bloecken gibt es nicht: innere Originale -> ihr Inhalt rueckt an ihre
    Stelle, innere Kopien fallen weg (nichts Eigenes geht verloren)."""
    for b in _synced(doc):
        inner = b["data"].get("blocks")
        if not isinstance(inner, list):
            continue
        out = []
        for x in inner:
            if isinstance(x, dict) and x.get("type") == "synced":
                xd = x.get("data") if isinstance(x.get("data"), dict) else {}
                out.extend(y for y in (xd.get("blocks") or []) if isinstance(y, dict) and y.get("type") != "synced")
            else:
                out.append(x)
        b["data"]["blocks"] = out


def _canon(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


# --- Speichern ---------------------------------------------------------------------------------------------------------
def prepare(c, owner, nid, uid, old_body, body, caps, acc=None):
    """Vor dem Schreiben von body in nid (pages._note_update, Format blocks): Kopien auf {sid} reduzieren, neue Originale
    registrieren, Durchschreiben vormerken. -> (neuer body, plan). plan wird nach dem UPDATE an after_save gegeben."""
    plan = {"through": [], "originals": {}, "copies": set(), "gone": [], "nid": nid, "owner": owner}
    if '"synced"' not in body and '"synced"' not in (old_body or ""):
        return body, plan
    acc = acc or Access(c, uid)
    doc = json.loads(body)
    _flatten_nested(doc)
    old_sids = set(sids_in(old_body))
    blocks = _synced(doc)
    rows = {}
    sids = sorted({b["data"].get("sid") for b in blocks if isinstance(b["data"].get("sid"), str) and SID_RE.fullmatch(b["data"]["sid"])})
    if sids:
        rows = {r[0]: r[1:] for r in c.execute(f"SELECT sid, owner, note_id, block_id, rev, gone_at FROM synced_block WHERE sid IN "
                                               f"({','.join('?' * len(sids))}) FOR UPDATE", tuple(sids)).fetchall()}
    drop, seen_orig, seen_any = set(), set(), set()
    for b in blocks:
        d = b["data"]
        srev = d.pop("srev", None)
        sid = d.get("sid")
        if isinstance(sid, str) and sid in seen_any:
            drop.add(id(b))                              # dieselbe sid zweimal auf einer Seite: nur die erste bleibt
            continue
        if isinstance(sid, str):
            seen_any.add(sid)
        has = isinstance(d.get("blocks"), list)
        if not (isinstance(sid, str) and SID_RE.fullmatch(sid)):
            if has:                                      # neues Original ohne sid: Server vergibt sie
                sid = new_sid()
                while sid in rows:
                    sid = new_sid()
                d["sid"] = sid
            else:
                drop.add(id(b))                          # Kopie ohne gueltige sid: weg
                continue
        r = rows.get(sid)
        if r is None:
            if has and sid not in seen_orig:            # neues Original (sid vom Client)
                seen_orig.add(sid)
                plan["originals"][sid] = (b.get("id") or "", d["blocks"])
                rows[sid] = (owner, nid, b.get("id") or "", 0, None)
            else:
                d.pop("blocks", None)                    # unbekannte Kopie: bleibt als Verweis (Anzeige "nicht gefunden")
            continue
        r_owner, r_nid, r_bid, r_rev, r_gone = r
        if r_nid == nid and sid not in seen_orig and has:
            seen_orig.add(sid)                           # Original dieser Seite
            plan["originals"][sid] = (b.get("id") or "", d["blocks"])
            continue
        if r_gone is not None and has and sid not in seen_orig and "sync1" in caps and (
                uid == r_owner or RANK.get(acc.role(r_nid) or "", 0) >= RANK["edit"]):
            seen_orig.add(sid)                           # Original war weg (z. B. ausgeschnitten): diese Seite wird seine Heimat
            plan["originals"][sid] = (b.get("id") or "", d["blocks"])
            continue
        # Kopie
        if sid not in old_sids and r_nid != nid and not acc.role(r_nid) and uid != r_owner:
            drop.add(id(b))                              # neue Kopie ohne Lesezugriff auf das Original
            continue
        if has and "sync1" in caps and r_nid != nid and r_gone is None:
            plan["through"].append((sid, d["blocks"], srev if isinstance(srev, int) and not isinstance(srev, bool) else None))
        d.pop("blocks", None)
        for k in [k for k in d if k != "sid"]:
            d.pop(k)
        if r_nid != nid:
            plan["copies"].add(sid)
    if drop:
        for lst in _lists(doc["blocks"]):
            lst[:] = [x for x in lst if id(x) not in drop]
    # Originale, die bisher in dieser Seite standen und jetzt fehlen
    if old_sids:
        mine = {r[0] for r in c.execute(f"SELECT sid FROM synced_block WHERE note_id=? AND gone_at IS NULL AND sid IN "
                                        f"({','.join('?' * len(old_sids))})", (nid, *sorted(old_sids))).fetchall()}
        plan["gone"] = sorted(mine - set(plan["originals"]))
    return json.dumps(doc, ensure_ascii=False), plan


def after_save(c, plan, uid, rev, now, origin=None):
    """Nach dem UPDATE der Seite: Tabellen nachfuehren, Durchschreiben ausfuehren, Seiten mit Kopien benachrichtigen.
    -> {sid: Ergebnis} fuer die Antwort (nur Durchschreiben: "ok" | "merged" | "denied" | "locked" | "gone" | "stale")."""
    nid, owner = plan["nid"], plan["owner"]
    changed = []
    for sid, (bid, blocks) in plan["originals"].items():
        content = json.dumps(blocks, ensure_ascii=False)
        r = c.execute("SELECT content, note_id, gone_at FROM synced_block WHERE sid=?", (sid,)).fetchone()
        if r is None:
            c.execute("INSERT INTO synced_block (sid, owner, note_id, block_id, content, rev, updated) VALUES (?,?,?,?,?,?,?)",
                      (sid, owner, nid, bid, content[:CONTENT_MAX], rev, now))
        else:
            c.execute("UPDATE synced_block SET owner=?, note_id=?, block_id=?, content=?, rev=?, updated=?, gone_at=NULL WHERE sid=?",
                      (owner, nid, bid, content[:CONTENT_MAX], rev, now, sid))
            if r[0] != content or r[1] != nid or r[2] is not None:
                changed.append(sid)
    for sid in plan["gone"]:
        c.execute("UPDATE synced_block SET gone_at=?, updated=? WHERE sid=? AND note_id=?", (now, now, sid, nid))
        changed.append(sid)
    have = {r[0] for r in c.execute("SELECT sid FROM synced_ref WHERE note_id=?", (nid,)).fetchall()}
    for sid in plan["copies"] - have:
        c.execute("INSERT INTO synced_ref (sid, note_id) VALUES (?,?) ON CONFLICT DO NOTHING", (sid, nid))
    for sid in have - plan["copies"]:
        c.execute("DELETE FROM synced_ref WHERE sid=? AND note_id=?", (sid, nid))
    res = {}
    for sid, blocks, srev in plan["through"]:
        res[sid] = write_through(c, uid, sid, blocks, srev, now, origin, skip=nid)
    if changed:
        notify_refs(c, changed, uid, now, origin, skip=nid)
    return res


def _find(doc, sid):
    for b in _synced(doc):
        if b["data"].get("sid") == sid and isinstance(b["data"].get("blocks"), list):
            return b
    return None


def write_through(c, uid, sid, blocks, srev, now, origin=None, skip=None):
    """Bearbeiteten Inhalt einer Kopie in das Original schreiben (Stufe-1-Merge)."""
    from saga import mentions, merge, nextcloud
    from saga.databases import sign_buttons
    from saga.sharing import names
    from saga.core import local_ref
    from saga.sharing import is_remote
    r = c.execute("SELECT note_id, rev, gone_at, content FROM synced_block WHERE sid=? FOR UPDATE", (sid,)).fetchone()
    if not r or r[2] is not None:
        return "gone"
    a = r[0]
    acc = Access(c, uid)
    role = acc.role(a)
    if RANK.get(role or "", 0) < RANK["edit"]:
        return "denied"
    owner_a = acc.owner(a)
    row = c.execute("SELECT title, body, format, rev, locked, updated_by FROM note WHERE id=? AND uid=? AND deleted_at IS NULL FOR UPDATE",
                    (a, owner_a)).fetchone()
    if not row or row[2] != "blocks":
        return "gone"
    if row[4]:
        return "locked"
    if srev is None:
        return "stale"                                   # ohne Stand des Originals (src.rev beim Lesen) kein Durchschreiben
    doc = json.loads(ensure_ids(row[1], "blocks", a) or '{"blocks": []}')
    orig = _find(doc, sid)
    if orig is None:
        return "gone"
    theirs = orig["data"]["blocks"]
    mine = [x for x in blocks if isinstance(x, dict) and x.get("type") != "synced"]
    if uid != owner_a:                                   # verdeckte Verweistexte (Seiten ohne Zugriff) wieder einsetzen
        from saga.sharing import restore_links, title_of
        mine = json.loads(restore_links(json.dumps({"blocks": theirs}, ensure_ascii=False), json.dumps({"blocks": mine}, ensure_ascii=False),
                                        viewer(acc), title_of(c, owner_a)))["blocks"]
    if _canon(mine) == _canon(theirs):
        return "ok"
    base = None
    if srev >= row[3]:
        base = theirs                                    # seit dem Lesen unveraendert: einfach uebernehmen
    else:
        bs = base_of(c, a, srev)
        if bs and bs[2] == "blocks":
            ob = _find(json.loads(ensure_ids(bs[1], "blocks", a) or '{"blocks": []}'), sid)
            base = ob["data"]["blocks"] if ob else None
    info = {"conflicts": [], "lost": []}
    if base is None:
        return "stale"                                   # Basis zu alt (nicht mehr in note_base): nichts ueberschreiben
    else:
        by = {"by": uid if is_remote(uid) else local_ref(uid), "name": names(c, [uid]).get(uid) or ""}
        out, info = merge.merge_doc({"blocks": base}, {"blocks": mine}, {"blocks": theirs}, None, by, now)
        merged = out["blocks"]
    try:
        _check_file_refs(owner_a, merged)
    except ValueError as ex:
        raise ValueError(t("Bilder und Dateien im synchronisierten Block müssen dem Eigentümer des Originals gehören.")) from ex
    orig["data"]["blocks"] = merged
    new_body = mentions.strip_foreign(c, owner_a, a, row[1], json.dumps(doc, ensure_ascii=False))
    if '"buttons"' in new_body or '"sig"' in new_body:
        new_body = sign_buttons(new_body, a, owner_a, uid, row[1])
    if '"nextcloud"' in new_body:
        try:
            new_body = nextcloud.check_body(uid, a, row[1], row[2], new_body)
        except nextcloud.Rejected:
            return "denied"
    _snapshot(c, owner_a, a, row[:3], now, force=bool(info.get("conflicts") or info.get("lost")), author=row[5], by=uid)
    c.execute("UPDATE note SET body=?, rev=rev+1, updated_by=?, updated=? WHERE id=? AND uid=?", (new_body, uid, now, a, owner_a))
    keep_base(c, a, row[3] + 1, row[0], new_body, "blocks", now, (row[3], row[0], row[1], row[2]))
    mentions.sync(c, owner_a, a, row[1], new_body, uid)
    c.execute("UPDATE synced_block SET content=?, rev=?, updated=? WHERE sid=?",
              (json.dumps(merged, ensure_ascii=False)[:CONTENT_MAX], row[3] + 1, now, sid))
    bus.fire("note.saved", c, note_id=a, owner=owner_a, rev=row[3] + 1, by=uid, updated=now, origin=origin)
    bus.fire("page.content", c, owner=owner_a, note_id=a, by=uid)        # Webhooks page.updated (A)
    notify_refs(c, [sid], uid, now, origin, skip=skip)
    return "merged" if info.get("conflicts") else "ok"


def notify_refs(c, sids, uid, now, origin=None, skip=None):
    """Live-Ereignis an alle Seiten mit Kopien dieser sids (ihre Revision bleibt - "updated" ist neuer)."""
    if not sids:
        return
    ph = ",".join("?" * len(sids))
    for nid, owner in c.execute(f"SELECT DISTINCT r.note_id, n.uid FROM synced_ref r JOIN note n ON n.id=r.note_id WHERE r.sid IN ({ph}) "
                                f"AND n.deleted_at IS NULL LIMIT {REFS_FIRE_MAX}", tuple(sids)).fetchall():
        if nid != skip:
            bus.fire("note.saved", c, note_id=nid, owner=owner, by=uid, updated=now, origin=origin, synced=True)


def moved(c, a, b, owner_b, moved_blocks, rev_b, now):
    """blocks/move: Originale wandern mit (synced_block.note_id = b), Kopien ebenso (synced_ref)."""
    sids_orig, sids_copy = {}, set()
    try:
        for x in _iter_blocks(moved_blocks):
            if x.get("type") != "synced" or not isinstance(x.get("data"), dict) or not isinstance(x["data"].get("sid"), str):
                continue
            if isinstance(x["data"].get("blocks"), list):
                sids_orig[x["data"]["sid"]] = x.get("id") or ""
            else:
                sids_copy.add(x["data"]["sid"])
    except (ValueError, RecursionError):
        return
    for sid, bid in sids_orig.items():
        c.execute("UPDATE synced_block SET note_id=?, owner=?, block_id=?, rev=?, updated=? WHERE sid=? AND note_id=?",
                  (b, owner_b, bid, rev_b, now, sid, a))
        c.execute("DELETE FROM synced_ref WHERE sid=? AND note_id=?", (sid, b))     # Kopie in b war bisher Verweis auf a
    for sid in sids_copy:
        r = c.execute("SELECT note_id FROM synced_block WHERE sid=?", (sid,)).fetchone()
        still = c.execute("SELECT body FROM note WHERE id=?", (a,)).fetchone()
        if not still or sid not in sids_in(still[0]):
            c.execute("DELETE FROM synced_ref WHERE sid=? AND note_id=?", (sid, a))
        if r and r[0] != b:
            c.execute("INSERT INTO synced_ref (sid, note_id) VALUES (?,?) ON CONFLICT DO NOTHING", (sid, b))
    if sids_orig:
        notify_refs(c, list(sids_orig), None, now)


# --- Lesen -------------------------------------------------------------------------------------------------------------
def _current(c, cache, a_nid):
    """(owner, rev, doc|None, deleted, locked) der Seite des Originals - einmal je Anfrage."""
    if a_nid not in cache:
        r = c.execute("SELECT uid, rev, body, format, deleted_at, locked FROM note WHERE id=?", (a_nid,)).fetchone()
        if not r:
            cache[a_nid] = (None, None, None, True, False)
        else:
            try:
                doc = json.loads(ensure_ids(r[2], r[3], a_nid)) if r[3] == "blocks" else None
            except ValueError:
                doc = None
            cache[a_nid] = (r[0], r[1], doc, r[4] is not None, bool(r[5]))
    return cache[a_nid]


def expand(c, body, nid, uid, acc=None, for_fed=False):
    """Gelesener Inhalt der Seite nid fuer uid: Kopien bekommen Inhalt und src (je nach Rechten auf das Original),
    Originale src (original, refs). -> body (unveraendert, wenn es keine synced-Bloecke gibt)."""
    if not body or '"synced"' not in body:
        return body
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    blocks = _synced(doc)
    if not blocks:
        return body
    acc = acc or Access(c, uid)
    sids = sorted({b["data"].get("sid") for b in blocks if isinstance(b["data"].get("sid"), str) and SID_RE.fullmatch(b["data"]["sid"])})
    rows = {r[0]: r[1:] for r in c.execute(f"SELECT sid, owner, note_id, block_id, content, rev, gone_at FROM synced_block WHERE sid IN "
                                           f"({','.join('?' * len(sids))})", tuple(sids)).fetchall()} if sids else {}
    refs = dict(c.execute(f"SELECT r.sid, COUNT(*) FROM synced_ref r JOIN note n ON n.id=r.note_id WHERE n.deleted_at IS NULL AND r.sid IN "
                          f"({','.join('?' * len(sids))}) GROUP BY r.sid", tuple(sids)).fetchall()) if sids else {}
    cache, seen = {}, set()
    vis = viewer(acc)
    for b in blocks:
        d = b["data"]
        sid = d.get("sid")
        r = rows.get(sid) if isinstance(sid, str) else None
        has = isinstance(d.get("blocks"), list)
        if r is None:
            d["src"] = {"original": True, "refs": 0} if has else {"missing": True}
            if not has:
                d.pop("blocks", None)
            continue
        r_owner, a, r_bid, content, r_rev, gone = r
        if a == nid and has and sid not in seen:
            seen.add(sid)
            d["src"] = {"original": True, "refs": int(refs.get(sid, 0))}
            continue
        if sid in seen or a == nid:
            b["data"] = {"sid": sid, "src": {"missing": True}}
            continue
        seen.add(sid)
        # Kopie: Inhalt nur mit Zugriff auf das Original
        role = acc.role(a)
        own_a, rev_a, doc_a, deleted, locked = _current(c, cache, a)
        inner = None
        if not deleted and doc_a is not None:
            ob = _find(doc_a, sid)
            inner = ob["data"]["blocks"] if ob else None
        is_gone = gone is not None or inner is None
        allowed = bool(role) or uid == r_owner
        if not allowed:
            b["data"] = {"sid": sid, "src": {"noaccess": True}}
            continue
        if inner is None:
            try:
                inner = json.loads(content or "[]")
            except ValueError:
                inner = []
        part = json.dumps({"blocks": inner}, ensure_ascii=False)
        if uid != own_a and "/notes#" in part:           # Verweise im Inhalt: nur Titel, die der Betrachter sehen darf
            part = mask_links(part, "blocks", vis)
        src = {"note": a, "block": r_bid or "", "role": "owner" if uid == r_owner else (role or "view"),
               "rev": rev_a if not is_gone else r_rev, "refs": int(refs.get(sid, 0))}
        if is_gone:
            src["gone"] = True
            src["role"] = "view"
        elif locked and src["role"] != "owner":
            src["role"] = "view"
        b["data"] = {"sid": sid, "blocks": json.loads(part)["blocks"], "src": src}
    return json.dumps(doc, ensure_ascii=False)


def publish_blocks(c, uid, sid):
    """Veroeffentlichen einer Seite von uid: Inhalt einer Kopie -> Blockliste oder None (nicht zeigen). Nur, wenn das
    Original uid selbst gehoert oder seine Seite veroeffentlicht ist - und es nicht geloescht ist."""
    if not (isinstance(sid, str) and SID_RE.fullmatch(sid)):
        return None
    r = c.execute("SELECT owner, note_id, gone_at FROM synced_block WHERE sid=?", (sid,)).fetchone()
    if not r or r[2] is not None:
        return None
    n = c.execute("SELECT body, format, deleted_at, uid FROM note WHERE id=?", (r[1],)).fetchone()
    if not n or n[2] is not None or n[1] != "blocks":
        return None
    if n[3] != uid and not c.execute("SELECT 1 FROM note_publish WHERE note_id=? AND active=1", (r[1],)).fetchone():
        from saga.publish import published_via
        if not published_via(c, n[3], r[1]):
            return None
    try:
        ob = _find(json.loads(n[0]), sid)
    except ValueError:
        return None
    return ob["data"]["blocks"] if ob else None


def drop_note(c, nid):
    """Seite endgueltig geloescht: Originale gelten als geloescht (letzter Inhalt bleibt fuer die Kopien)."""
    c.execute("UPDATE synced_block SET gone_at=? WHERE note_id=? AND gone_at IS NULL", (time.time(), nid))
