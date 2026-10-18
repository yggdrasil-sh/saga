"""Grundlagen der Seiten, gemeinsam genutzt von Seiten, Datenbanken, Dateien und Export:
Bloecke, Eigenschaften, Relationen, Versionen, Papierkorb-Fristen, Datei-Pfade."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import date
from html import unescape as html_unescape
from urllib.parse import quote, urlparse
from db import connect as _db
from saga.core import E, SECRET, STATE_DIR, _json_load, t


NOTE_TITLE_MAX, NOTE_BODY_MAX = 200, 20000
NOTE_MAX_COUNT = int(E.get("NOTE_MAX_COUNT", "5000"))   # Seiten pro Konto (ohne Datenbank-Eintraege und Papierkorb)
# Datenbank-Eintraege zaehlen getrennt (sonst ist ein Konto mit einer groesseren Tabelle nach ~5.000 Zeilen "voll")
NOTE_ROW_MAX_COUNT = int(E.get("NOTE_ROW_MAX_COUNT", str(max(50000, 10 * NOTE_MAX_COUNT))))
# Sicherheitsnetz gegen Anlegen+Loeschen im Kreis: alles zusammen inkl. Papierkorb (leert sich nach TRASH_DAYS)
NOTE_TOTAL_MAX = 2 * (NOTE_MAX_COUNT + NOTE_ROW_MAX_COUNT)


def note_limit_error(c, owner, parent):
    """Fehlertext, wenn `owner` keine weitere Seite (parent = Elternseite oder None) anlegen darf, sonst None.
    Bis NOTE_MAX_COUNT Notizen insgesamt (inkl. Papierkorb, Index-Zaehlung) wie bisher ohne weitere Pruefung; erst
    darueber wird getrennt gezaehlt: normale Seiten (ohne Papierkorb) gegen NOTE_MAX_COUNT, Eintraege einer Datenbank
    (ohne Papierkorb) gegen NOTE_ROW_MAX_COUNT, alles inkl. Papierkorb gegen NOTE_TOTAL_MAX. Damit ist jede Grenze
    hoechstens lockerer als frueher (nichts, was bisher ging, wird abgelehnt)."""
    total = c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (owner,)).fetchone()[0]
    if total < NOTE_MAX_COUNT:
        return None
    if total >= NOTE_TOTAL_MAX:
        return t("maximal {n} Seiten (inkl. Papierkorb)", n=NOTE_TOTAL_MAX)
    dbs = "SELECT id FROM note WHERE uid=? AND kind='database'"
    is_row = parent is not None and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database'",
                                              (parent, owner)).fetchone() is not None
    if is_row:
        n = c.execute(f"SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL AND parent_id IN ({dbs})",
                      (owner, owner)).fetchone()[0]
        return t("maximal {n} Datenbank-Einträge", n=NOTE_ROW_MAX_COUNT) if n >= NOTE_ROW_MAX_COUNT else None
    n = c.execute(f"SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL "
                  f"AND (parent_id IS NULL OR parent_id NOT IN ({dbs}))", (owner, owner)).fetchone()[0]
    return t("maximal {n} Seiten", n=NOTE_MAX_COUNT) if n >= NOTE_MAX_COUNT else None


MAX_NEST, MAX_JSON_DEPTH = 8, 48


def _iter_blocks(blocks, depth=0):
    """Alle Bloecke inkl. verschachtelter (Spalten, Toggles) - fuer Pruefungen, Export, Vorschau."""
    if depth > MAX_NEST:
        raise ValueError(t("zu tief verschachtelt"))
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        yield b
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            for col in d.get("cols") if isinstance(d.get("cols"), list) else []:
                if isinstance(col, dict):
                    yield from _iter_blocks(col.get("blocks"), depth + 1)
        elif b.get("type") == "toggle":
            yield from _iter_blocks(d.get("blocks"), depth + 1)


def _strip_tags(s):
    return html_unescape(re.sub(r"<[^>]*>", " ", s))


def blocks_text(blocks, lines=False):
    """Klartext aus Bloecken (fuer Suche und Vorschau). lines=True: ein Block je Zeile (Vorschau, BUG-092)."""
    if lines:
        return "\n".join(t for t in (blocks_text([b]) for b in blocks if isinstance(b, dict)) if t)
    parts = []

    def walk(v, key=None):
        if isinstance(v, str):
            if key == "code":                 # Code-Bloecke woertlich durchsuchbar lassen
                parts.append(v)
            elif key not in ("style", "level", "alignment", "language", "checked", "url", "extension",
                             "size", "emoji", "withBorder", "withBackground", "stretched", "withHeadings", "open"):
                parts.append(_strip_tags(v))
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k not in ("type", "id", "tunes"):     # technische Felder verschachtelter Bloecke
                    walk(x, k)

    for b in blocks:
        walk(b.get("data", {}))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _note_text(fmt, body, lines=False):
    if fmt == "blocks":
        try:
            return blocks_text(json.loads(body).get("blocks", []), lines)
        except (ValueError, AttributeError):
            return ""
    return body or ""


def _tags_load(s):
    try:
        v = json.loads(s) if s else []
        return [str(x) for x in v] if isinstance(v, list) else []
    except ValueError:
        return []


def _check_parent(c, uid, nid, parent):
    """parent muss eigene Seite sein und darf nicht nid selbst oder eine Unterseite von nid sein."""
    if parent is None:
        return None
    parent = int(parent)
    if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (parent, uid)).fetchone():
        raise LookupError
    if nid is not None:
        sub = {r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT CAST(? AS BIGINT) UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=?) SELECT id FROM t",
            (nid, uid))}
        if parent in sub:
            raise ValueError(t("Seite kann nicht unter sich selbst oder eine eigene Unterseite verschoben werden"))
    return parent


NOTE_META_COLS = "id, title, icon, parent_id, updated, tags, pinned, is_template, kind"


def _note_meta(r):
    return {"id": r[0], "title": r[1], "icon": r[2] or "", "parent_id": r[3], "updated": r[4], "tags": _tags_load(r[5]), "pinned": bool(r[6]), "template": bool(r[7]), "kind": r[8] or "page"}


# --- Papierkorb ----------------------------------------------------------------
TRASH_DAYS = 30
VERSION_EVERY, VERSION_MAX = 600, 50     # hoechstens alle 10 Minuten eine Sicherung, max. 50 pro Seite


def _purge_ids(c, uid, ids):
    """Endgueltig loeschen - samt Versionen, Veroeffentlichung, Freigaben und Kommentaren (im Papierkorb bleiben sie
    gespeichert, wirken aber nicht: Seiten im Papierkorb sind fuer niemanden ausser dem Eigentuemer erreichbar)."""
    tree_changed(c, uid, ids, "deleted")                 # vor dem Loeschen (Empfaenger aus den Freigaben bestimmen)
    c.executemany("DELETE FROM note_version WHERE uid=? AND note_id=?", [(uid, i) for i in ids])
    c.executemany("DELETE FROM note_publish WHERE uid=? AND note_id=?", [(uid, i) for i in ids])
    c.executemany("DELETE FROM note_share WHERE owner_uid=? AND note_id=?", [(uid, i) for i in ids])
    c.executemany("DELETE FROM note_comment_seen WHERE thread_id IN (SELECT id FROM note_comment WHERE owner_uid=? AND note_id=?)",
                  [(uid, i) for i in ids])
    c.executemany("DELETE FROM note_comment WHERE owner_uid=? AND note_id=?", [(uid, i) for i in ids])
    c.executemany("DELETE FROM note WHERE uid=? AND id=?", [(uid, i) for i in ids])


def note_chain(c, uid, nid):
    """Ids von nid aufwaerts bis zur obersten Seite des Kontos uid - endet VOR der ersten geloeschten/archivierten Seite
    oder Vorlage. nid selbst geloescht/archiviert/Vorlage/fremd -> []. (Veroeffentlichen und Teilen: was vererbt wird.)"""
    rows = c.execute(
        "WITH RECURSIVE t(id, parent_id, bad, depth) AS ("
        " SELECT id, parent_id, (deleted_at IS NOT NULL OR archived IS NOT NULL OR is_template<>0), 0 FROM note WHERE id=? AND uid=?"
        " UNION ALL SELECT n.id, n.parent_id, (n.deleted_at IS NOT NULL OR n.archived IS NOT NULL OR n.is_template<>0), t.depth + 1"
        " FROM note n JOIN t ON n.id=t.parent_id WHERE n.uid=? AND t.depth < 64)"
        " SELECT id, bad FROM t ORDER BY depth", (nid, uid, uid)).fetchall()
    out = []
    for i, bad in rows:
        if bad:
            break
        out.append(i)
    return out


def announce_row(c, owner, row_id, by, now, origin=None):
    """Zeile einer Datenbank geaendert/angelegt/geloescht: auch wer die Datenbank offen hat, bekommt note.changed
    (mit row = Zeile; rev None, die Datenbank selbst ist unveraendert) - die Tabelle laedt dann neu (BUG-049)."""
    from saga import bus
    r = c.execute("SELECT d.id FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid WHERE n.id=? AND n.uid=? "
                  "AND d.kind='database'", (row_id, owner)).fetchone()
    if r:
        bus.fire("note.saved", c, note_id=r[0], owner=owner, by=by, updated=now, origin=origin, row=row_id)


def tree_changed(c, owner, ids, op, origin=None):
    """Live-Ereignis notes.tree (Seitenbaum geaendert, nur IDs): an alle Verbindungen des Eigentuemers und an Personen,
    die eine betroffene Seite (oder eine darueber) geteilt bekommen haben - gebuendelt in saga.live. op: created, updated,
    moved, trashed, restored, archived, deleted, shared. Nach dem Commit (bei Rollback nie)."""
    from saga import bus
    ids = [int(i) for i in ids if i is not None]
    if ids:
        bus.fire("notes.tree", c, owner=owner, ids=ids, op=op, origin=origin)


def announce_tree(c, owner, root, ids, by, now, gone, rev=None, origin=None):
    """Live-Kanal (BUG-033): jede Seite eines Teilbaums melden (geloescht/archiviert: "weg"; wiederhergestellt,
    verschoben, Version zurueck: geaendert) und "Mit mir geteilt" bei allen neu laden lassen, mit denen der Teilbaum (oder eine Seite darueber) geteilt ist."""
    from saga import bus
    for i in ids:
        bus.fire("note.saved", c, note_id=i, owner=owner, by=by, updated=now, gone=gone, rev=rev if i == root else None, origin=origin)
    announce_row(c, owner, root, by, now, origin)
    chain = set(ids) | set(note_chain(c, owner, root))
    if not chain:
        return
    marks = ",".join("?" * len(chain))
    for tt, tg in c.execute(f"SELECT DISTINCT target_type, target FROM note_share WHERE owner_uid=? AND note_id IN ({marks})",
                            (owner, *chain)).fetchall():
        if tt in ("user", "group"):
            bus.emit_after(c, "shared.changed", {"owner": owner, "note_id": root, "target_type": tt, "target": tg})


_PURGED = {}               # uid -> Zeitpunkt der letzten Papierkorb-Frist (nur fuer _purge_old(..., every=...))


def _purge_old(c, uid, every=0):
    """Seiten, die laenger als TRASH_DAYS im Papierkorb liegen, endgueltig loeschen (Index note_trash).
    every > 0: hoechstens alle `every` Sekunden je Konto (Seitenbaum: dort sind Papierkorb-Seiten ohnehin unsichtbar,
    eine Stunde Verzug aendert nichts; die Papierkorb-Liste selbst prueft immer)."""
    now = time.time()
    if every and now - _PURGED.get(uid, 0) < every:
        return
    _PURGED[uid] = now
    old = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NOT NULL AND deleted_at < ?",
                                   (uid, now - TRASH_DAYS * 86400))]
    if old:
        _purge_ids(c, uid, old)


# --- Versionsverlauf -------------------------------------------------------------
FORCED_EVERY = 60          # erzwungene Sicherungen durch Nicht-Eigentuemer: hoechstens eine je Seite und Person je 60 s
VERSION_KEEP_OWN = 10      # beim Kuerzen nie loeschen: die neuesten 10 Fassungen des Eigentuemers (bzw. ohne Autor = alt)


def _snapshot(c, uid, nid, old, now, force=False, author=None, by=None):
    """Alten Stand (title, body, format) sichern - hoechstens alle VERSION_EVERY Sekunden. author: wer diesen Stand
    geschrieben hat (note.updated_by; None = unbekannt bzw. der Eigentuemer). by: wer gerade speichert.
    Erzwingt ein Nicht-Eigentuemer die Sicherung (Konflikt, leeren/kuerzen, Speichern ohne rev), gilt eine Drossel: eine
    je Seite und Person je FORCED_EVERY - ausser der bisherige Stand stammt von jemand anderem (dann immer). So kann
    niemand mit "Bearbeiten" den Verlauf des Eigentuemers mit eigenen Sicherungen ueberschwemmen."""
    last = c.execute("SELECT MAX(created) FROM note_version WHERE uid=? AND note_id=?", (uid, nid)).fetchone()[0]
    if force and by and by != uid and author == by:
        if c.execute("SELECT 1 FROM note_version WHERE uid=? AND note_id=? AND author=? AND created>? LIMIT 1",
                     (uid, nid, by, now - FORCED_EVERY)).fetchone():
            force = False
    if not force and last and now - last < VERSION_EVERY:
        return
    c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
              (nid, uid, old[0], old[1], old[2], now, author))
    c.execute("DELETE FROM note_version WHERE uid=? AND note_id=? AND id NOT IN "
              "(SELECT id FROM note_version WHERE uid=? AND note_id=? ORDER BY created DESC LIMIT ?) AND id NOT IN "
              "(SELECT id FROM note_version WHERE uid=? AND note_id=? AND (author IS NULL OR author=?) ORDER BY created DESC LIMIT ?)",
              (uid, nid, uid, nid, VERSION_MAX, uid, nid, uid, VERSION_KEEP_OWN))


def _disposition(name):
    """Download-Dateiname: HTTP-Header sind Latin-1 -> ASCII-Ersatz + RFC 5987 (UTF-8) fuer moderne Browser."""
    ascii_name = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


# --- Dateien & Bilder in Seiten ----------------------------------------------------
# Ablage: STATE_DIR/files/<id> (zufaellige 24-Hex-ID), Metadaten in Tabelle 'file'.
# Bilder (am INHALT erkannt) werden angezeigt, alles andere nur als Download.
FILES_DIR = os.path.join(STATE_DIR, "files")
FILE_MAX = int(E.get("ATTACH_MAX_MB", "10")) * 1024 * 1024
FILE_QUOTA = int(E.get("ATTACH_QUOTA_MB", "500")) * 1024 * 1024
_FILE_URL = re.compile(r"/files/([0-9a-f]{24})")


def _check_file_refs(uid, blocks):
    """Bild-/Datei-Bloecke duerfen nur auf EIGENE hochgeladene Dateien zeigen."""
    ids = set()
    for b in _iter_blocks(blocks):                        # auch in Spalten und Toggles
        if b.get("type") in ("image", "attaches"):
            url = ((b.get("data") or {}).get("file") or {}).get("url", "")
            m = _FILE_URL.fullmatch(str(url))
            if not m:
                raise ValueError(t("Bild/Datei muss aus Saga hochgeladen sein"))
            ids.add(m.group(1))
    if ids:
        with _db() as c:
            own = {r[0] for r in c.execute(f"SELECT id FROM file WHERE uid=? AND id IN ({','.join('?' * len(ids))})", (uid, *ids))}
        if ids - own:
            raise ValueError(t("unbekannte Datei"))


# --- Datenbanken (Notion-Stil) ------------------------------------------------------
# Eine Datenbank ist eine Seite mit kind='database' und einem Schema (Eigenschaften).
# Ihre Zeilen sind normale Unterseiten mit props = {eigenschaft_id: wert}.
PROP_TYPES = {"text", "number", "select", "multi_select", "date", "checkbox", "url", "created", "edited", "relation", "calc"}
CALC_PROP_OPS = {"div", "pct", "mul", "add", "sub"}     # "Berechnet": A / B, A in % von B, A * B, A + B, A - B (nie gespeichert)


def _calc_value(p, pv):
    """Wert einer Rechen-Eigenschaft aus zwei Zahlen-Eigenschaften der Zeile (oder None)."""
    def num(k):
        v = pv.get(k)
        try:
            return None if v in (None, "") else float(v)
        except (TypeError, ValueError):
            return None
    a, b, op = num(p.get("a")), num(p.get("b")), p.get("op", "div")
    if op in ("add", "sub"):
        if a is None and b is None:
            return None
        a, b = a or 0.0, b or 0.0
    elif a is None or b is None:
        return None
    if op in ("div", "pct"):
        if b == 0:
            return None
        v = a / b * (100 if op == "pct" else 1)
    else:
        v = a * b if op == "mul" else a + b if op == "add" else a - b
    return round(v, 4)
AUTO_TYPES = {"created", "edited"}          # Zeitstempel der Zeile, nie gespeicherte Werte
NUM_FMT, NUM_SHOW = {"percent", "euro"}, {"bar", "ring"}
OPT_COLORS = {"gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}
_PID = re.compile(r"[a-z0-9_]{1,24}")


def _default_schema(lang=None):
    """Neue Datenbank. lang: Sprache der gespeicherten Namen (Konto-Sprache, siehe core.account_lang)."""
    from saga.i18n import set_lang, _cur
    tok = set_lang(lang) if lang else None
    try:
        return {"props": [
            {"id": "status", "name": t("Status"), "type": "select", "options": [
                {"id": "todo", "name": t("Offen"), "color": "gray"}, {"id": "doing", "name": t("In Arbeit"), "color": "blue"},
                {"id": "done", "name": t("Erledigt"), "color": "green"}]},
            {"id": "datum", "name": t("Datum"), "type": "date"}],
            "views": [{"id": "tabelle", "name": t("Tabelle"), "type": "table"},
                      {"id": "board", "name": t("Board"), "type": "board", "group_by": "status"}]}
    finally:
        if tok is not None:
            _cur.reset(tok)


VIEW_TYPES = {"table", "board", "list", "gallery", "calendar"}
CALC_OPS = {"count", "filled", "empty", "pct_filled", "unique", "sum", "avg", "median", "min", "max", "range",
            "checked", "unchecked", "pct_checked", "earliest", "latest"}       # Fusszeile der Tabelle (je Spalte)
FILTER_OPS = {"contains", "not_contains", "is", "is_not", "eq", "gt", "lt", "before", "after",
              "is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month"}


def _clean_views(raw, props):
    """Ansichten (max. 10): Typ, Gruppierung (nur Auswahl), Sortierung, Filter - nur auf existierende
    Eigenschaften. Verweise auf geloeschte Spalten fallen dadurch automatisch weg."""
    pids = {p["id"]: p for p in props}
    out, seen = [], set()
    for v in (raw if isinstance(raw, list) else [])[:10]:
        if not isinstance(v, dict):
            continue
        vid = str(v.get("id", ""))
        if not _PID.fullmatch(vid) or vid in seen:
            raise ValueError(t("ungültige Ansicht"))
        seen.add(vid)
        view = {"id": vid, "name": str(v.get("name", "")).strip()[:40] or t("Ansicht"),
                "type": v.get("type") if v.get("type") in VIEW_TYPES else "table"}
        g = v.get("group_by")
        if g in pids and pids[g]["type"] == "select":
            view["group_by"] = g
        # Sortierung: bis zu 3 Stufen ("sorts"); altes Einzelfeld "sort" wird uebernommen
        raw_sorts = v.get("sorts") if isinstance(v.get("sorts"), list) else ([v["sort"]] if isinstance(v.get("sort"), dict) else [])
        sorts = [{"pid": s["pid"], "dir": "desc" if s.get("dir") == "desc" else "asc"} for s in raw_sorts[:3]
                 if isinstance(s, dict) and (s.get("pid") == "title" or s.get("pid") in pids)]
        if sorts:
            view["sorts"] = sorts
        db_ = v.get("date_by")                               # Kalender: nach welchem Datum
        if db_ in pids and pids[db_]["type"] in ("date", "created", "edited"):
            view["date_by"] = db_
        gr = v.get("group")                                  # Gruppierung fuer Tabelle/Liste
        if gr in pids and pids[gr]["type"] == "select":
            view["group"] = gr
        if isinstance(v.get("hidden"), list):                # ausgeblendete Eigenschaften
            hid = [h for h in v["hidden"] if h in pids]
            if hid:
                view["hidden"] = hid
        if isinstance(v.get("widths"), dict):                # Spaltenbreiten (px)
            w = {k: max(60, min(int(x), 800)) for k, x in v["widths"].items()
                 if (k == "title" or k in pids) and isinstance(x, (int, float))}
            if w:
                view["widths"] = w
        if isinstance(v.get("order"), list):                 # Reihenfolge der Eigenschaften in dieser Ansicht
            order = list(dict.fromkeys(x for x in v["order"] if x in pids))
            if order:
                view["order"] = order
        if v.get("wrap") is True:
            view["wrap"] = True
        if v.get("open") in ("center", "page", "left"):    # Eintraege oeffnen als (Standard: Seitenansicht rechts)
            view["open"] = v["open"]
        if v.get("ext") is True:                            # Kalender-Ansicht: externe Termine einblenden
            view["ext"] = True
        if v.get("card") in ("s", "l"):
            view["card"] = v["card"]
        fl = [{"pid": f["pid"], "op": f["op"], "value": "" if f.get("value") is None else str(f["value"])[:200]}
              for f in (v.get("filters") or [])[:100]
              if isinstance(f, dict) and (f.get("pid") == "title" or f.get("pid") in pids) and f.get("op") in FILTER_OPS][:10]   # erst pruefen, dann begrenzen
        if fl:
            view["filters"] = fl
        cr = [{"pid": f["pid"], "op": f["op"], "value": "" if f.get("value") is None else str(f["value"])[:200], "color": f["color"]}
              for f in (v.get("colors") or [])[:100]          # bedingte Farbe: wie Filter + erlaubte Farbe
              if isinstance(f, dict) and (f.get("pid") == "title" or f.get("pid") in pids) and f.get("op") in FILTER_OPS
              and f.get("color") in OPT_COLORS][:10]
        if cr:
            view["colors"] = cr
        if isinstance(v.get("calcs"), dict):                 # Berechnung je Spalte (Fusszeile)
            cc = {k: op for k, op in list(v["calcs"].items())[:40] if (k == "title" or k in pids) and op in CALC_OPS}
            if cc:
                view["calcs"] = cc
        out.append(view)
    return out or [{"id": "tabelle", "name": t("Tabelle"), "type": "table"}]


def _clean_schema(raw):
    """Schema pruefen: max. 30 Eigenschaften, eindeutige IDs, bekannte Typen, Optionen fuer Auswahlen."""
    if not isinstance(raw, dict) or not isinstance(raw.get("props"), list):
        raise ValueError(t("ungültiges Schema"))
    out, seen = [], set()
    for p in raw["props"][:30]:
        pid, typ = str(p.get("id", "")), str(p.get("type", ""))
        if not _PID.fullmatch(pid) or pid in seen or typ not in PROP_TYPES:
            raise ValueError(t("ungültige Eigenschaft"))
        seen.add(pid)
        prop = {"id": pid, "name": str(p.get("name", "")).strip()[:60] or t("Eigenschaft"), "type": typ}
        if typ in ("select", "multi_select"):
            opts, oseen = [], set()
            for o in (p.get("options") or [])[:50]:
                oid = str(o.get("id", ""))
                if not _PID.fullmatch(oid) or oid in oseen:
                    raise ValueError(t("ungültige Option"))
                oseen.add(oid)
                col = o.get("color") if o.get("color") in OPT_COLORS else "gray"
                opts.append({"id": oid, "name": str(o.get("name", "")).strip()[:40] or t("Option"), "color": col})
            prop["options"] = opts
        if typ == "relation":                       # Ziel-Datenbank (Besitz prueft der Aufrufer)
            tg = p.get("target")
            if not (isinstance(tg, int) or (isinstance(tg, str) and tg.isdigit())):
                raise ValueError(t("Relation braucht eine Ziel-Datenbank"))
            prop["target"] = int(tg)
            if isinstance(p.get("pair"), str) and _PID.fullmatch(p["pair"]):
                prop["pair"] = p["pair"]                # Gegenspalte (zweiseitig) - stellt nur der Server her
        if typ == "calc":                           # Rechnung: Zahlen-Spalten pruefen wir nach der Schleife
            prop["op"] = p.get("op") if p.get("op") in CALC_PROP_OPS else "div"
            for k in ("a", "b"):
                if isinstance(p.get(k), str) and _PID.fullmatch(p[k]):
                    prop[k] = p[k]
        if typ in ("number", "calc"):
            if p.get("fmt") in NUM_FMT:
                prop["fmt"] = p["fmt"]
            if p.get("show") in NUM_SHOW:
                prop["show"] = p["show"]
        if isinstance(p.get("width"), (int, float)):
            prop["width"] = max(60, min(int(p["width"]), 600))
        out.append(prop)
    nums = {p["id"] for p in out if p["type"] == "number"}
    for p in out:                                    # Rechnung nur aus vorhandenen Zahlen-Spalten
        if p["type"] == "calc":
            for k in ("a", "b"):
                if p.get(k) not in nums:
                    p.pop(k, None)
    res = {"props": out, "views": _clean_views(raw.get("views"), out)}
    tn = re.sub(r"\s+", " ", str(raw.get("title_name") or "")).strip()[:60]
    if tn and tn != "Name":                          # eigener Name der ersten Spalte
        res["title_name"] = tn
    if raw.get("locked") is True:                    # gesperrt: Spalten/Ansichten unveraenderlich, Eintraege frei
        res["locked"] = True
    return res


# Schema-Revision (BUG-001): Clients schicken immer das ganze Schema. Wer einen alten Stand hat, wuerde Spalten/Optionen,
# die inzwischen jemand anderes angelegt hat, samt Werten aller Zeilen loeschen. Darum fuehrt der Server "srev" (wird
# vom Client mit dem Schema zurueckgeschickt) und merkt sich je Spalte/Option, ab welcher srev und von wem sie stammt.
SCHEMA_SINCE_KEEP = 500          # so viele Schema-Aenderungen lang bleibt die Herkunft einer Spalte/Option gemerkt


def _actor_tag(uid):
    """Kurzes, nicht umkehrbares Kennzeichen des Kontos (kein Anmeldename im Schema)."""
    return hmac.new(SECRET.encode(), str(uid).encode(), hashlib.sha256).hexdigest()[:12]


def _schema_keys(s):
    keys = set()
    for p in s.get("props", []):
        keys.add(p["id"])
        for o in p.get("options") or []:
            keys.add(p["id"] + "/" + o["id"])
    return keys


def _schema_srev(s):
    v = s.get("srev") if isinstance(s, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _schema_merge(old, new, known, uid):
    """Spalten/Optionen, die im neuen Schema fehlen, aber erst nach dem Stand des Clients (known) von jemand
    anderem angelegt wurden, gelten nicht als geloescht - sie bleiben samt Werten erhalten."""
    since = old.get("_since") if isinstance(old.get("_since"), dict) else {}
    me = _actor_tag(uid)

    def unknown(key):
        v = since.get(key)
        return isinstance(v, list) and len(v) == 2 and isinstance(v[0], int) and v[0] > known and v[1] != me
    old_props = [p for p in old.get("props", []) if isinstance(p, dict) and p.get("id")]
    by_id = {p["id"]: p for p in new["props"]}
    for p in new["props"]:
        op = next((x for x in old_props if x["id"] == p["id"]), None)
        if op and "options" in p and isinstance(op.get("options"), list):
            have = {o["id"] for o in p["options"]}
            for o in op["options"]:
                if isinstance(o, dict) and o.get("id") not in have and unknown(p["id"] + "/" + str(o.get("id"))) and len(p["options"]) < 50:
                    p["options"].append(o)
    for i, op in enumerate(old_props):
        if op["id"] not in by_id and unknown(op["id"]) and len(new["props"]) < 30:
            new["props"].insert(min(i, len(new["props"])), op)
            by_id[op["id"]] = op
    return new


def _schema_stamp(old, new, uid):
    """srev hochzaehlen und Herkunft neuer Spalten/Optionen vermerken (new wird veraendert und zurueckgegeben)."""
    n = _schema_srev(old) + 1
    since = old.get("_since") if isinstance(old.get("_since"), dict) else {}
    old_keys = _schema_keys({"props": [p for p in old.get("props", []) if isinstance(p, dict) and p.get("id")]})
    me, out = _actor_tag(uid), {}
    for k in _schema_keys(new):
        v = since.get(k)
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
            if v[0] > n - SCHEMA_SINCE_KEEP:
                out[k] = v
        elif k not in old_keys:
            out[k] = [n, me]
    new["srev"] = n
    if out:
        new["_since"] = out
    else:
        new.pop("_since", None)
    return new


def _rel_check(c, uid):
    """Pruefer fuer Relationen: nur eigene, aktive Eintraege, die wirklich in der Ziel-Datenbank liegen."""
    def check(target, ids):
        if not target or not ids:
            return set()
        return {r[0] for r in c.execute(
            f"SELECT id FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
            (uid, target, *ids))}
    return check


def _db_schema(c, uid, dbid):
    r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (dbid, uid)).fetchone()
    return _json_load(r[0], {"props": [], "views": []}) if r else None


def _rel_mirror_set(c, uid, target_db, row_id, pid, other_id, add):
    """In Zeile row_id (muss in target_db liegen) die Relation pid um other_id ergaenzen bzw. davon loesen."""
    r = c.execute("SELECT props FROM note WHERE id=? AND uid=? AND parent_id=? AND deleted_at IS NULL", (row_id, uid, target_db)).fetchone()
    if not r:
        return
    props = _json_load(r[0], {})
    vals = [x for x in (props.get(pid) or []) if isinstance(x, int) and not isinstance(x, bool)]
    if add and other_id not in vals and len(vals) < 50:
        vals.append(other_id)
    elif not add and other_id in vals:
        vals.remove(other_id)
    else:
        return
    if vals:
        props[pid] = vals
    else:
        props.pop(pid, None)
    c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(props, ensure_ascii=False), row_id, uid))


def _rel_sync(c, uid, schema, row_id, old_props, new_props, may=None):
    """Zweiseitige Relationen: hinzugefuegte/entfernte Verknuepfungen auf der Gegenseite nachziehen.
    may(zeile) -> bool: nur diese Zielzeilen anfassen (Teilen: Bearbeiter nur in Zeilen, die er selbst bearbeiten darf -
    in anderen bleibt die Gegenseite, wie sie ist)."""
    for p in schema.get("props", []):
        if p.get("type") != "relation" or not p.get("pair"):
            continue
        old, new = set(old_props.get(p["id"]) or []), set(new_props.get(p["id"]) or [])
        for tid in new - old:
            if may is None or may(tid):
                _rel_mirror_set(c, uid, p["target"], tid, p["pair"], row_id, True)
        for tid in old - new:
            if may is None or may(tid):
                _rel_mirror_set(c, uid, p["target"], tid, p["pair"], row_id, False)


def _rel_link(c, uid, nid, title, rp, actor=None):
    """Gegenspalte in der Ziel-Datenbank anlegen und bestehende Verknuepfungen nachtragen; liefert deren ID."""
    ts = _db_schema(c, uid, rp["target"])
    if ts is None:
        raise ValueError(t("Ziel-Datenbank der Relation nicht gefunden"))
    if len(ts.get("props", [])) >= 30:
        raise ValueError(t("Die Ziel-Datenbank hat schon 30 Eigenschaften"))
    used = {p["id"] for p in ts["props"]}
    mid = next(x for x in ("r" + secrets.token_hex(4) for _ in range(50)) if x not in used)
    old = json.loads(json.dumps(ts))
    ts["props"].append({"id": mid, "name": (title or t("Verknüpft"))[:60], "type": "relation", "target": nid, "pair": rp["id"]})
    _schema_stamp(old, ts, uid if actor is None else actor)
    c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(ts, ensure_ascii=False), rp["target"], uid))
    for row_id, pr in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL", (uid, nid)).fetchall():
        for tid in _json_load(pr, {}).get(rp["id"]) or []:
            _rel_mirror_set(c, uid, rp["target"], tid, mid, row_id, True)
    return mid


def _rel_unlink(c, uid, nid, target, mpid, delete):
    """Gegenspalte mpid in target loesen: delete=True entfernt sie samt Werten, sonst nur die Kopplung."""
    ts = _db_schema(c, uid, target)
    if ts is None:
        return
    mp = next((p for p in ts.get("props", []) if p.get("id") == mpid and p.get("type") == "relation" and p.get("target") == nid), None)
    if not mp:
        return
    if delete:
        ts["props"] = [p for p in ts["props"] if p is not mp]
        for row_id, pr in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=?", (uid, target)).fetchall():
            d = _json_load(pr, {})
            if mpid in d:
                d.pop(mpid)
                c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(d, ensure_ascii=False), row_id, uid))
    else:
        mp.pop("pair", None)
    c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?",
              (json.dumps(_schema_stamp(ts, _clean_schema(ts), uid), ensure_ascii=False), target, uid))


def _clean_props(schema, raw, base=None, check=None):
    """Zeilenwerte gegen das Schema pruefen; unbekannte Eigenschaften werden verworfen."""
    if not isinstance(raw, dict):
        raise ValueError(t("ungültige Werte"))
    out = dict(base or {})
    by_id = {p["id"]: p for p in schema.get("props", [])}
    for pid, v in raw.items():
        p = by_id.get(pid)
        if not p:
            continue
        typ = p["type"]
        if typ in AUTO_TYPES or typ == "calc":   # automatisch/berechnet - nie gespeichert, nicht faelschbar
            out.pop(pid, None)
            continue
        if v in (None, "", []):
            out.pop(pid, None)
            continue
        if typ == "text":
            v = str(v)[:2000]
        elif typ == "number":
            v = float(v)
            if v != v or abs(v) > 1e15:
                raise ValueError(t("ungültige Zahl"))
            v = int(v) if v == int(v) else v
        elif typ == "select":
            if v not in {o["id"] for o in p.get("options", [])}:
                raise ValueError(t("unbekannte Option"))
        elif typ == "multi_select":
            ok_ids = {o["id"] for o in p.get("options", [])}
            if not isinstance(v, list) or any(x not in ok_ids for x in v):
                raise ValueError(t("unbekannte Option"))
            v = list(dict.fromkeys(v))[:20]
        elif typ == "date":
            v = date.fromisoformat(str(v)).isoformat()
        elif typ == "checkbox":
            v = bool(v)
            if not v:
                out.pop(pid, None)
                continue
        elif typ == "relation":
            if not isinstance(v, list):
                raise ValueError(t("Relation muss eine Liste sein"))
            ids = list(dict.fromkeys(int(x) for x in v if not isinstance(x, bool)))[:50]
            if any(not 0 < x < 2 ** 63 for x in ids):   # passt nicht in SQLite -> sonst OverflowError (500)
                raise ValueError(t("ungültige Eintrags-ID"))
            if check is not None:                   # fremde/erfundene IDs still verwerfen
                ok = check(p.get("target"), ids)
                ids = [x for x in ids if x in ok]
            if not ids:
                out.pop(pid, None)
                continue
            v = ids
        elif typ == "url":
            v = str(v).strip()[:500]
            if urlparse(v).scheme not in ("http", "https", "mailto"):
                raise ValueError(t("URL muss mit http(s):// oder mailto: beginnen"))
        out[pid] = v
    return out
