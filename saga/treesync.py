"""Seitenbaum schlank und als Abgleich (2.2): GET /api/notes mit Parametern. Ohne Parameter bleibt /api/notes genau wie
bisher (saga/pages.py, byte-gleich). Vertrag fuer Apps: docs/APP-API-LIVE.md, Abschnitt "Seitenbaum abgleichen".

- rows=0      ohne Datenbank-Eintraege (Seiten, deren Elternseite eine Datenbank ist); rows=only: nur diese
- fields=a,b  nur diese Felder je Seite (id immer); unbekannte Namen werden ignoriert
- delta=1     Antwort als Objekt {cursor, full: true, notes: [...], removed: []} - jede Seite zusaetzlich mit sort und
              created (Reihenfolge wie im Baum: sort aufsteigend, ohne sort zuerst, dann created, dann id)
- since=<c>   nur Geaendertes seit Stand c: {cursor, full: false, notes: [geaendert/neu], removed: [ids]}. Ist c zu alt,
              unbekannt oder von einer anderen Datenbank (Backup zurueckgespielt, Neustart), kommt full: true mit allem.

Der Stand (cursor) ist "<kennung>.<xmin>": xmin = aelteste beim Lesen noch laufende Transaktion. Der Trigger (db.py,
TREE_SEQ_SQL) merkt je Seite die Transaktion der letzten Aenderung; alles, was ein Abruf noch nicht sehen konnte, hat
eine Transaktion >= xmin und kommt beim naechsten Abgleich (manches doppelt - das schadet nicht). Der Baum enthaelt nur
eigene Seiten des Kontos (geteilte kommen wie bisher aus /api/shared); auch der Abgleich liest nur Seiten mit uid = Konto,
"removed" nennt nur IDs eigener (frueherer) Seiten."""
import re
import time
from db import connect as _db
from saga.notes import NOTE_META_COLS, _note_meta, _purge_old
from saga.sharing import shared_ids, unread_notes

FIELDS = ("id", "title", "icon", "parent_id", "updated", "tags", "pinned", "template", "kind", "archived", "locked", "shared",
          "unread", "sort", "created")
TOMB_DAYS = 30                       # Grabsteine geloeschter Seiten: so lange kann ein Client per since nachholen
_CURSOR = re.compile(r"([0-9a-f]{12})\.([0-9]{1,19})")
_PRUNED = [0.0]
_COLS = f"{NOTE_META_COLS}, archived, locked, sort, created"
_ORDER = "ORDER BY sort NULLS FIRST, created, id"
SMALL = 2000                         # bis zu so vielen geaenderten Seiten: Freigabe/Kommentar-Punkt nur fuer diese pruefen


def parse_fields(s):
    if not s:
        return None
    want = {f.strip() for f in str(s).split(",")}
    return tuple(f for f in FIELDS if f in want or f == "id")


def _state(c):
    """-> (kennung, grenze, xmin) oder None (kein Abgleich verfuegbar). Eine eigene Anweisung VOR dem Lesen der Seiten."""
    try:
        c.execute("SAVEPOINT tseq_state")
        r = c.execute("SELECT (SELECT v FROM note_tseq_meta WHERE k='epoch'), (SELECT v FROM note_tseq_meta WHERE k='horizon'), "
                      "pg_snapshot_xmin(pg_current_snapshot())::text::bigint").fetchone()
        c.execute("RELEASE SAVEPOINT tseq_state")
    except Exception:              # Tabelle fehlt (Postgres < 13, keine Rechte): immer ganzer Baum, ohne Stand
        c.execute("ROLLBACK TO SAVEPOINT tseq_state")
        return None
    if not r or not r[0]:
        return None
    return r[0], int(r[1] or 0), int(r[2])


def _prune():
    """Alte Grabsteine entfernen (hoechstens stuendlich je Prozess, eigene Verbindung); die Grenze merkt sich die groesste
    entfernte Transaktion - aeltere Stände bekommen danach full."""
    now = time.time()
    if now - _PRUNED[0] < 3600:
        return
    _PRUNED[0] = now
    with _db() as c, c:
        r = c.execute("WITH d AS (DELETE FROM note_tseq WHERE gone=1 AND at < ? RETURNING xid) SELECT max(xid) FROM d",
                      (now - TOMB_DAYS * 86400,)).fetchone()
        if r and r[0] is not None:
            c.execute("INSERT INTO note_tseq_meta (k, v) VALUES ('horizon', ?) ON CONFLICT (k) DO UPDATE "
                      "SET v = GREATEST(note_tseq_meta.v::bigint, EXCLUDED.v::bigint)::text", (str(r[0]),))


def _db_ids(c, uid):
    return {r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND kind='database'", (uid,))}


def _entry(r, sh, un, extra):
    d = dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10]), shared=r[0] in sh, unread=r[0] in un)
    if extra:
        d["sort"] = r[11]
        d["created"] = r[12]
    return d


def tree(c, uid, rows="1", fields=None, delta=False, since=None):
    """-> Liste (ohne delta/since) bzw. Objekt {cursor, full, notes, removed}."""
    _purge_old(c, uid, every=3600)
    st = _state(c) if (delta or since is not None) else None
    if st:
        try:
            _prune()
        except Exception:          # Aufraeumen ist Nebensache - der Abruf selbst darf daran nicht scheitern
            pass
    cursor = f"{st[0]}.{st[2]}" if st else None
    dbs = _db_ids(c, uid) if rows != "1" else None

    def keep(r):
        if dbs is None:
            return True
        is_row = r[3] in dbs
        return is_row if rows == "only" else not is_row

    m = _CURSOR.fullmatch(str(since or "")) if since is not None else None
    ok = bool(st and m and m.group(1) == st[0] and st[1] < int(m.group(2)) <= st[2])
    if ok:
        x = int(m.group(2))
        ids = [r[0] for r in c.execute("SELECT id FROM note_tseq WHERE uid=? AND xid >= ?", (uid, x))]
        res = c.execute(f"SELECT {_COLS} FROM note WHERE uid=? AND deleted_at IS NULL AND id = ANY(?) {_ORDER}",
                        (uid, ids)).fetchall() if ids else []
        if len(res) <= SMALL:
            got = [r[0] for r in res]
            sh = {r[0] for r in c.execute("SELECT DISTINCT note_id FROM note_share WHERE owner_uid=? AND note_id = ANY(?)",
                                          (uid, got))} if got else set()
            un = unread_notes(c, uid, [uid]) & set(got) if got else set()
        else:
            sh, un = shared_ids(c, uid), unread_notes(c, uid, [uid])
        present = set()
        notes = []
        for r in res:
            if keep(r):
                present.add(r[0])
                notes.append(_entry(r, sh, un, True))
        removed = sorted(set(ids) - present)
        out = {"cursor": cursor, "full": False, "notes": notes, "removed": removed}
    else:
        res = c.execute(f"SELECT {_COLS} FROM note WHERE uid=? AND deleted_at IS NULL "
                        + (_ORDER if (delta or since is not None) else "ORDER BY sort NULLS FIRST, created"), (uid,)).fetchall()
        sh, un = shared_ids(c, uid), unread_notes(c, uid, [uid])
        ext = delta or since is not None
        notes = [_entry(r, sh, un, ext) for r in res if keep(r)]
        out = {"cursor": cursor, "full": True, "notes": notes, "removed": []} if ext else notes
    if fields:
        lst = out["notes"] if isinstance(out, dict) else out
        lst[:] = [{k: d[k] for k in fields if k in d} for d in lst]
    return out
