"""Seitenbaum schlank und als Abgleich (2.2): GET /api/notes mit Parametern. Ohne Parameter bleibt /api/notes genau wie
bisher (saga/pages.py, byte-gleich). Vertrag fuer Apps: docs/APP-API-LIVE.md, Abschnitt "Seitenbaum abgleichen".

- rows=0      ohne Datenbank-Eintraege (Seiten, deren Elternseite eine Datenbank ist); rows=only: nur diese
- fields=a,b  nur diese Felder je Seite (id immer); unbekannte Namen werden ignoriert
- delta=1     Antwort als Objekt {cursor, full: true, notes: [...], removed: []} - Liste wie bisher (Reihenfolge: sort,
              ohne sort zuerst, dann created, bei Gleichstand id)
- since=<c>   nur Geaendertes seit Stand c: {cursor, full: false, notes: [geaendert/neu], removed: [ids]}; jede Seite in
              notes traegt "after" = ID der Seite, hinter der sie jetzt in der Liste steht (null = ganz vorn), notes
              selbst in Listen-Reihenfolge. Ist c zu alt, unbekannt oder von einer anderen Datenbank (Backup
              zurueckgespielt, Neustart) oder hat sich sehr viel geaendert, kommt full: true mit allem.

Der Stand (cursor) ist "<kennung>.<xmin>": xmin = aelteste beim Lesen noch laufende Transaktion. Der Trigger (db.py,
TREE_SEQ_SQL) merkt je Seite die Transaktion der letzten Aenderung; alles, was ein Abruf noch nicht sehen konnte, hat
eine Transaktion >= xmin und kommt beim naechsten Abgleich (manches doppelt - das schadet nicht). Die Position einer
Seite haengt nur an ihren eigenen Werten (sort, created, id) - aendert sie sich, wird die Seite selbst gemeldet; die
Reihenfolge der uebrigen bleibt. Der Baum enthaelt nur eigene Seiten des Kontos (geteilte kommen wie bisher aus
/api/shared); auch der Abgleich liest nur Seiten mit uid = Konto, "removed" nennt nur IDs eigener (frueherer) Seiten."""
import re
import time
from db import connect as _db
from saga.notes import NOTE_META_COLS, _note_meta, _purge_old
from saga.sharing import shared_ids, unread_notes

FIELDS = ("id", "title", "icon", "parent_id", "updated", "tags", "pinned", "template", "kind", "archived", "locked", "shared",
          "unread")
TOMB_DAYS = 30                       # Grabsteine geloeschter Seiten: so lange kann ein Client per since nachholen
_CURSOR = re.compile(r"([0-9a-f]{12})\.([0-9]{1,19})")
_PRUNED = [0.0]
_COLS = f"{NOTE_META_COLS}, archived, locked"
_KEY = "sort NULLS FIRST, created, id"
SMALL = 2000                         # bis zu so vielen geaenderten Seiten: Freigabe/Kommentar-Punkt nur fuer diese pruefen
MAX_DELTA = 5000                     # mehr geaenderte Seiten: ganzer Baum (einfacher fuer Clients, kaum teurer)
_FILTER_AS = {"1": "", "0": " AND ({a}.parent_id IS NULL OR {a}.parent_id NOT IN (SELECT id FROM note WHERE uid=? AND kind='database'))",
              "only": " AND {a}.parent_id IN (SELECT id FROM note WHERE uid=? AND kind='database')"}
_FILTER = {k: v.format(a="note") for k, v in _FILTER_AS.items()}
FEW = 50                             # bis zu so vielen Aenderungen: Vorgaenger je Seite per Index, sonst Fensterfunktion
_IKEY = "{a}.sort IS NOT NULL, COALESCE({a}.sort, 0), {a}.created, {a}.id"      # = sort NULLS FIRST, created, id
_IDESC = "q.sort IS NOT NULL DESC, COALESCE(q.sort, 0) DESC, q.created DESC, q.id DESC"


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


def _entry(r, sh, un):
    return dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10]), shared=r[0] in sh, unread=r[0] in un)


def _changes(c, uid, rows, ids, xid):
    """Geaenderte Seiten (in Listen-Reihenfolge) -> (zeilen, {id: vorgaenger in der ganzen gefilterten Liste}).
    Welche Seiten geaendert sind, liest dieselbe Anweisung erneut (xid): sonst koennte eine Seite, die sich zwischen dem
    Lesen der IDs und dieser Anweisung bewegt, Vorgaenger ("after") einer gemeldeten Seite sein, ohne selbst gemeldet zu
    werden - der Client sortierte dann hinter ihre alte Position (bis zum naechsten ganzen Baum)."""
    flt = _FILTER[rows]
    p = (uid, uid) if flt else (uid,)
    if len(ids) <= FEW:
        # Wenige: je Seite der Vorgaenger ueber den Index note_tree_order (rueckwaerts ab der eigenen Position) - in
        # DERSELBEN Anweisung wie die Seiten selbst (ein Datenstand)
        fq, fn = _FILTER_AS[rows].format(a="q"), _FILTER_AS[rows].format(a="n")
        res = c.execute(f"SELECT {_COLS}, (SELECT q.id FROM note q WHERE q.uid=n.uid AND q.deleted_at IS NULL{fq} "
                        f"AND ({_IKEY.format(a='q')}) < ({_IKEY.format(a='n')}) ORDER BY {_IDESC} LIMIT 1) AS prev "
                        f"FROM note n WHERE n.uid=? AND n.deleted_at IS NULL{fn} "
                        "AND n.id IN (SELECT t.id FROM note_tseq t WHERE t.uid=n.uid AND t.xid >= ?) "
                        "ORDER BY n.sort NULLS FIRST, n.created, n.id",
                        (*((uid,) if flt else ()), *p, xid)).fetchall()
    else:
        # Viele: Fensterfunktion ueber die ganze Liste (eine Anweisung, ein Datenstand)
        res = c.execute(f"SELECT * FROM (SELECT {_COLS}, lag(id) OVER (ORDER BY {_KEY}) AS prev, sort, created FROM note "
                        f"WHERE uid=? AND deleted_at IS NULL{flt}) w "
                        f"WHERE id IN (SELECT t.id FROM note_tseq t WHERE t.uid=? AND t.xid >= ?) ORDER BY {_KEY}",
                        (*p, uid, xid)).fetchall()
    return res, {r[0]: r[11] for r in res}


def tree(c, uid, rows="1", fields=None, delta=False, since=None):
    """-> Liste (ohne delta/since) bzw. Objekt {cursor, full, notes, removed}."""
    _purge_old(c, uid, every=3600)
    ext = delta or since is not None
    st = _state(c) if ext else None
    if st:
        try:
            _prune()
        except Exception:          # Aufraeumen ist Nebensache - der Abruf selbst darf daran nicht scheitern
            pass
    cursor = f"{st[0]}.{st[2]}" if st else None
    m = _CURSOR.fullmatch(str(since or "")) if since is not None else None
    out = None
    if st and m and m.group(1) == st[0] and st[1] < int(m.group(2)) <= st[2]:
        ids = [r[0] for r in c.execute("SELECT id FROM note_tseq WHERE uid=? AND xid >= ?", (uid, int(m.group(2))))]
        if len(ids) <= MAX_DELTA:
            res, prev = _changes(c, uid, rows, ids, int(m.group(2))) if ids else ([], {})
            got = [r[0] for r in res]
            if len(got) <= SMALL:
                sh = {r[0] for r in c.execute("SELECT DISTINCT note_id FROM note_share WHERE owner_uid=? AND note_id = ANY(?)",
                                              (uid, got))} if got else set()
                un = unread_notes(c, uid, [uid]) & set(got) if got else set()
            else:
                sh, un = shared_ids(c, uid), unread_notes(c, uid, [uid])
            notes = [dict(_entry(r, sh, un), after=prev.get(r[0])) for r in res]
            out = {"cursor": cursor, "full": False, "notes": notes, "removed": sorted(set(ids) - set(got))}
    if out is None:
        # Eintraege gleich in der Abfrage ausfiltern (sonst liest rows=0 bei 50.000 Eintraegen alles und wirft es weg).
        # Ohne delta/since: Reihenfolge exakt wie bisher (ORDER BY ohne id)
        flt = _FILTER[rows]
        res = c.execute(f"SELECT {_COLS} FROM note WHERE uid=? AND deleted_at IS NULL{flt} ORDER BY "
                        + (_KEY if ext else "sort NULLS FIRST, created"), (uid, uid) if flt else (uid,)).fetchall()
        sh, un = shared_ids(c, uid), unread_notes(c, uid, [uid])
        notes = [_entry(r, sh, un) for r in res]
        out = {"cursor": cursor, "full": True, "notes": notes, "removed": []} if ext else notes
    if fields:
        keys = fields + ("after",)
        lst = out["notes"] if isinstance(out, dict) else out
        lst[:] = [{k: d[k] for k in keys if k in d} for d in lst]
    return out
