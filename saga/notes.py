"""Grundlagen der Seiten, gemeinsam genutzt von Seiten, Datenbanken, Dateien und Export:
Bloecke, Eigenschaften, Relationen, Versionen, Papierkorb-Fristen, Datei-Pfade."""
import functools
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


# 2.4 (Testrunde W6): Bloecke, deren Daten nur technische Werte enthalten (Ansicht "month", Aktion "task", Art "youtube",
# Ansichtsnamen einer eingebetteten Datenbank) - fuer Suche, Vorschau und Export nur das, was man auf der Seite sieht.
SPECIAL_BLOCKS = ("embed", "buttons", "calendar", "tasks", "database", "immich", "verweis", "widget")
# Seite gestalten (2.5): Farben nur aus dieser Palette (wie COLORS in static/pages.js), Block-Farbe (tunes.hkstyle) nur fuer
# diese Typen. Gemeinsame Grundlage fuer saga.pages (Bereinigung) und saga.publish (Klassen c-<farbe>/bg-<farbe>).
PALETTE = ("gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red")
STYLE_TYPES = ("paragraph", "header", "list", "quote", "toggle", "callout", "checklist")


def special_text(typ, d, lines=False, title=None):
    """Klartext eines besonderen Blocks (siehe SPECIAL_BLOCKS). lines=True (Versionsvorschau): kurze Platzhalter fuer
    Bloecke ohne eigenen Text; title(nid) liefert dort den Titel einer eingebetteten Datenbank (nur mit Zugriff)."""
    d = d if isinstance(d, dict) else {}
    if typ == "embed":
        return _strip_tags(str(d.get("title") or "")) or str(d.get("url") or "")
    if typ == "buttons":
        bs = d.get("buttons") if isinstance(d.get("buttons"), list) else []
        return " ".join(" ".join(x for x in (str(b.get("icon") or ""), _strip_tags(str(b.get("label") or ""))) if x)
                        for b in bs if isinstance(b, dict)).strip()
    if typ == "widget":                     # 2.5 (saga.page_widgets): nur der Titel - Einstellungen sind technische Werte
        ti = _strip_tags(str(d.get("title") or "")).strip() if isinstance(d.get("title"), str) else ""
        return ("▣ " + (ti or t("Widget"))) if lines else ti
    if not lines:
        return ""
    if typ == "database" and title is not None and isinstance(d.get("db"), int):
        name = title(d["db"])
        if name:
            return "▦ " + name
    return {"calendar": "🗓 " + t("Kalender"), "tasks": "☑ " + t("Aufgaben"), "database": "▦ " + t("Datenbank"),
            "immich": "🖼 Immich"}.get(typ, "")


def blocks_text(blocks, lines=False, title=None):
    """Klartext aus Bloecken (fuer Suche und Vorschau). lines=True: ein Block je Zeile (Vorschau, BUG-092)."""
    if lines:
        return "\n".join(t for t in (blocks_text([b]) if b.get("type") not in SPECIAL_BLOCKS else special_text(b.get("type"), b.get("data"), True, title)
                                      for b in blocks if isinstance(b, dict)) if t)
    parts = []

    def walk(v, key=None):
        if isinstance(v, str):
            if key == "code":                 # Code-Bloecke woertlich durchsuchbar lassen
                parts.append(v)
            elif key not in ("style", "level", "alignment", "language", "checked", "url", "extension",
                             "size", "emoji", "withBorder", "withBackground", "stretched", "withHeadings", "open", "align"):
                parts.append(_strip_tags(v))
        elif isinstance(v, list):
            for x in v:
                walk(x, key)
        elif isinstance(v, dict):
            if v.get("type") == "nextcloud" and isinstance(v.get("data"), dict):     # verschachtelt: nur der Name
                walk(v["data"].get("name"))
                return
            if v.get("type") in SPECIAL_BLOCKS and isinstance(v.get("data"), dict):  # verschachtelt (Spalten, Ausklappbar)
                parts.append(special_text(v["type"], v["data"]))
                return
            for k, x in v.items():
                if k not in ("type", "id", "tunes"):     # technische Felder verschachtelter Bloecke
                    walk(x, k)

    for b in blocks:
        if b.get("type") == "nextcloud":                 # Nextcloud-Datei: nur der Name (keine Adressen, Kennungen)
            walk((b.get("data") or {}).get("name") if isinstance(b.get("data"), dict) else None)
            continue
        if b.get("type") in SPECIAL_BLOCKS:
            parts.append(special_text(b.get("type"), b.get("data")))
            continue
        n0 = len(parts)
        walk(b.get("data", {}))
        if b.get("type") in ("attaches", "image"):      # Dateiname steht in file.name, title, caption - nur einmal
            seen, uniq = set(), []
            for x in parts[n0:]:
                k = x.strip()
                if k and k not in seen:
                    seen.add(k)
                    uniq.append(x)
            parts[n0:] = uniq
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _note_text(fmt, body, lines=False, title=None):
    if fmt == "blocks":
        try:
            return blocks_text(json.loads(body).get("blocks", []), lines, title)
        except (ValueError, AttributeError):
            return ""
    return body or ""


def _tags_load(s):
    if not s or s == "[]":
        return []
    if isinstance(s, str) and len(s) <= 1024:      # dieselben Schlagwort-Listen wiederholen sich (Listen mit vielen Seiten)
        return list(_tags_parsed(s))
    try:
        v = json.loads(s)
        return [str(x) for x in v] if isinstance(v, list) else []
    except ValueError:
        return []


@functools.lru_cache(maxsize=4096)
def _tags_parsed(s):
    try:
        v = json.loads(s)
        return tuple(str(x) for x in v) if isinstance(v, list) else ()
    except ValueError:
        return ()


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
VERSION_EVERY, VERSION_MAX = 300, 50     # 2.4: hoechstens alle 5 Minuten eine Sicherung beim Speichern, max. 50 pro Seite


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
        # 2.5: Datenbanken, deren Rollups ueber eine Relation auf diese Datenbank zeigen, rechnen neu (ohne Zeilen-ID)
        for did, sch in c.execute("SELECT id, schema FROM note WHERE uid=? AND kind='database' AND deleted_at IS NULL AND id<>? "
                                  "AND schema LIKE '%\"rollup\"%'", (owner, r[0])).fetchall():
            props = [p for p in (_json_load(sch, {}).get("props") or []) if isinstance(p, dict)]
            rels = {p.get("id") for p in props if p.get("type") == "relation" and p.get("target") == r[0]}
            if any(p.get("type") == "rollup" and p.get("rel") in rels for p in props):
                bus.fire("note.saved", c, note_id=did, owner=owner, by=by, updated=now, origin=origin)


def share_targets(c, owner, ids):
    """Freigaben (target_type, target) auf den Seiten ids oder einer Seite darueber - ohne Ruecksicht auf Papierkorb/
    Archiv/Vorlage (lieber einmal zu oft "neu laden" als einen Empfaenger vergessen). Nur Personen und Gruppen.
    Vor einer Aenderung der Kette (Verschieben, Elternseite weg) aufgerufen: wer die Seiten bis eben sehen durfte."""
    ids = [int(i) for i in ids if i is not None]
    if not ids or not c.execute("SELECT 1 FROM note_share WHERE owner_uid=? LIMIT 1", (owner,)).fetchone():
        return set()
    marks = ",".join("?" * len(ids))
    chain = [r[0] for r in c.execute(
        f"WITH RECURSIVE t(id, parent_id, depth) AS (SELECT id, parent_id, 0 FROM note WHERE uid=? AND id IN ({marks}) "
        "UNION SELECT n.id, n.parent_id, t.depth + 1 FROM note n JOIN t ON n.id=t.parent_id WHERE n.uid=? AND t.depth < 64) "
        "SELECT DISTINCT id FROM t", (owner, *ids, owner)).fetchall()]
    if not chain:
        return set()
    return {(tt, tg) for tt, tg in c.execute(
        f"SELECT DISTINCT target_type, target FROM note_share WHERE owner_uid=? AND note_id IN ({','.join('?' * len(chain))})",
        (owner, *chain)).fetchall() if tt in ("user", "group")}


def tree_changed(c, owner, ids, op, origin=None, before=None):
    """Live-Ereignis notes.tree (Seitenbaum geaendert, nur IDs): an alle Verbindungen des Eigentuemers und an Personen,
    die eine betroffene Seite (oder eine darueber) geteilt bekommen haben - gebuendelt in saga.live. op: created, updated,
    moved, trashed, restored, archived, deleted, shared. Nach dem Commit (bei Rollback nie).
    before: share_targets() VOR einer Aenderung der Kette (Verschieben) - wer dadurch den Zugriff verliert, bekommt
    die IDs als "weg" (wie bei trashed)."""
    from saga import bus
    ids = [int(i) for i in ids if i is not None]
    if ids:
        bus.fire("notes.tree", c, owner=owner, ids=ids, op=op, origin=origin, before=sorted(before or ()))


def announce_tree(c, owner, root, ids, by, now, gone, rev=None, origin=None, before=None):
    """Live-Kanal (BUG-033): jede Seite eines Teilbaums melden (geloescht/archiviert: "weg"; wiederhergestellt,
    verschoben, Version zurueck: geaendert) und "Mit mir geteilt" bei allen neu laden lassen, mit denen der Teilbaum
    (oder eine Seite darueber) geteilt ist - JETZT oder (before, s. share_targets) vor dem Verschieben."""
    from saga import bus
    for i in ids:
        bus.fire("note.saved", c, note_id=i, owner=owner, by=by, updated=now, gone=gone, rev=rev if i == root else None, origin=origin)
    announce_row(c, owner, root, by, now, origin)
    announce_shared(c, owner, root, ids, before)


def announce_shared(c, owner, root, ids, before=None):
    """shared.changed an alle mit Freigabe auf dem Teilbaum oder einer Seite darueber (jetzt und - before - vorher)."""
    from saga import bus
    for tt, tg in sorted(share_targets(c, owner, set(ids) | {root}) | set(before or ())):
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


CHECKPOINT_MIN = 60        # 2.4: Sicherung bei Pause/Verlassen hoechstens einmal je Minute und Seite


def _latest(c, uid, nid):
    return c.execute("SELECT title, body, format, created FROM note_version WHERE uid=? AND note_id=? ORDER BY created DESC LIMIT 1",
                     (uid, nid)).fetchone()


def _forced_throttled(c, uid, nid, author, by, now):
    """Drossel fuer Nicht-Eigentuemer: eine eigene Sicherung je Seite und Person je FORCED_EVERY."""
    return bool(by and by != uid and author == by and c.execute(
        "SELECT 1 FROM note_version WHERE uid=? AND note_id=? AND author=? AND created>? LIMIT 1", (uid, nid, by, now - FORCED_EVERY)).fetchone())


def _insert_version(c, uid, nid, st, now, author):
    c.execute("INSERT INTO note_version (note_id, uid, title, body, format, created, author) VALUES (?,?,?,?,?,?,?)",
              (nid, uid, st[0], st[1], st[2], now, author))
    c.execute("DELETE FROM note_version WHERE uid=? AND note_id=? AND id NOT IN "
              "(SELECT id FROM note_version WHERE uid=? AND note_id=? ORDER BY created DESC LIMIT ?) AND id NOT IN "
              "(SELECT id FROM note_version WHERE uid=? AND note_id=? AND (author IS NULL OR author=?) ORDER BY created DESC LIMIT ?)",
              (uid, nid, uid, nid, VERSION_MAX, uid, nid, uid, VERSION_KEEP_OWN))


def _snapshot(c, uid, nid, old, now, force=False, author=None, by=None):
    """Alten Stand (title, body, format) sichern - hoechstens alle VERSION_EVERY Sekunden. author: wer diesen Stand
    geschrieben hat (note.updated_by; None = unbekannt bzw. der Eigentuemer). by: wer gerade speichert.
    Erzwingt ein Nicht-Eigentuemer die Sicherung (Konflikt, leeren/kuerzen, Speichern ohne rev), gilt eine Drossel: eine
    je Seite und Person je FORCED_EVERY - ausser der bisherige Stand stammt von jemand anderem (dann immer). So kann
    niemand mit "Bearbeiten" den Verlauf des Eigentuemers mit eigenen Sicherungen ueberschwemmen.
    2.4: ist der Stand schon die neueste Version (z. B. nach einer Sicherung beim Verlassen), keine zweite."""
    lv = _latest(c, uid, nid)
    if lv and tuple(lv[:3]) == tuple(old[:3]):
        return
    if force and _forced_throttled(c, uid, nid, author, by, now):
        force = False
    if not force and lv and now - lv[3] < VERSION_EVERY:
        return
    _insert_version(c, uid, nid, old, now, author)


def checkpoint(c, uid, nid, by, now=None):
    """2.4: AKTUELLEN Stand als Version sichern (Pause beim Bearbeiten, Seite verlassen) - nur, wenn er sich von der
    neuesten Version unterscheidet und die letzte Sicherung mindestens CHECKPOINT_MIN alt ist (Drossel fuer
    Nicht-Eigentuemer wie bei _snapshot). -> True, wenn eine Version angelegt wurde."""
    now = now or time.time()
    cur = c.execute("SELECT title, body, format, updated_by FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()
    if not cur:
        return False
    lv = _latest(c, uid, nid)
    if lv and (tuple(lv[:3]) == tuple(cur[:3]) or now - lv[3] < CHECKPOINT_MIN):
        return False
    if _forced_throttled(c, uid, nid, cur[3], by, now):
        return False
    _insert_version(c, uid, nid, cur, now, cur[3])
    return True


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
PROP_TYPES = {"text", "number", "select", "multi_select", "date", "checkbox", "url", "created", "edited", "relation", "calc",
              "formula", "rollup"}         # 2.5: formula/rollup rechnet der Server beim Lesen (saga.dbcalc), nie gespeichert
FX_TYPES = {"formula", "rollup"}
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
FX_EXPR_MAX = 2000                          # = saga.formula.EXPR_MAX
ROLLUP_FNS = {"count", "filled", "empty", "pct_filled", "unique", "sum", "avg", "median", "min", "max", "range",
              "checked", "unchecked", "pct_checked", "earliest", "latest", "show"}


class FormulaError(ValueError):
    """Formel einer Eigenschaft fehlerhaft (beim Speichern des Schemas): Text mit Name und Position."""

    def __init__(self, prop, err):
        super().__init__(t("Formel „{name}“: {msg} (Zeichen {pos})", name=prop.get("name") or prop.get("id"), msg=err.msg, pos=err.pos + 1))
        self.pid, self.pos, self.msg, self.code = prop.get("id"), err.pos, err.msg, err.code
NUM_FMT, NUM_SHOW = {"percent", "euro"}, {"bar", "ring"}
OPT_COLORS = {"gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red"}
_PID = re.compile(r"[a-z0-9_]{1,24}")


def _default_schema(lang=None):
    """Neue Datenbank. lang: Sprache der gespeicherten Namen (Konto-Sprache, siehe core.account_lang)."""
    from saga.i18n import set_lang, _cur
    tok = set_lang(lang) if lang else None
    try:
        return {"props": [
            {"id": "status", "name": t("Status"), "type": "select", "status": True, "options": [      # 2.5: Status-Gruppen (P5-E2)
                {"id": "todo", "name": t("Offen"), "color": "gray", "group": "todo"},
                {"id": "doing", "name": t("In Arbeit"), "color": "blue", "group": "doing"},
                {"id": "done", "name": t("Erledigt"), "color": "green", "group": "done"}]},
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
              "is_empty", "not_empty", "checked", "unchecked", "today", "this_week", "this_month",
              # 2.5 (Paket 5, relfilter1): weitere relative Datumsfilter und Status-Gruppe - Bedeutung in saga/dbquery.py
              "yesterday", "tomorrow", "last_week", "next_week", "last_month", "next_month", "this_year", "overdue",
              "next_days", "past_days", "on_or_before", "on_or_after", "status_group"}
STATUS_GROUPS = ("todo", "doing", "done")
REL_ROLES = {"parent": "children", "children": "parent", "blocked_by": "blocking", "blocking": "blocked_by"}   # 2.5: Selbst-Relationen
SUB_MODES = ("nested", "flat", "parents_only")


def _groupable(p):
    """Gruppieren nach Auswahl oder (2.5) nach Formel/Rollup mit Ergebnis Zahl/Text/Haekchen/Datum (nur lesend)."""
    return p["type"] == "select" or (p["type"] in FX_TYPES and p.get("rtype") in ("number", "text", "checkbox", "date"))


def formula_check(new_schema, old_schema=None):
    """Neue oder geaenderte Formeln pruefen (Syntax, Typen, Kreise, Grenzen) -> FormulaError (400 mit Position).
    Formeln, die erst durch andere Aenderungen ungueltig werden (Spalte geloescht, Typ geaendert - auch von aelteren
    Apps), bleiben gespeichert und zeigen beim Lesen je Zelle einen Fehler (fx_err)."""
    old = {p.get("id"): p.get("expr") for p in (old_schema or {}).get("props", []) if isinstance(p, dict) and p.get("type") == "formula"}
    changed = [p for p in new_schema.get("props", []) if p["type"] == "formula" and old.get(p["id"]) != p.get("expr")]
    if not changed:
        return
    from saga import formula as fx
    from saga.i18n import get_lang
    plan = fx._plan_cached(fx._key(new_schema["props"], new_schema.get("title_name")), get_lang())
    for p in changed:
        err = plan.errors.get(p["id"])
        if err is not None:
            raise FormulaError(p, err)


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
        if g in pids and _groupable(pids[g]):
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
        if gr in pids and _groupable(pids[gr]):
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
        if isinstance(v.get("wrap_cols"), list):              # 2.5: Zeilenumbruch nur in diesen Spalten
            wc = list(dict.fromkeys(x for x in v["wrap_cols"] if x == "title" or x in pids))[:30]
            if wc:
                view["wrap_cols"] = wc
        fz = v.get("freeze")                                 # 2.5: so viele Spalten ab links fixiert (inkl. Name)
        if isinstance(fz, int) and not isinstance(fz, bool) and 1 <= fz <= 3:
            view["freeze"] = fz
        if v.get("range") == "week" and view["type"] == "calendar":      # 2.5: Kalender als Woche (Standard: Monat)
            view["range"] = "week"
        if v.get("group_status") is True:                    # 2.5: Board/Gruppen nach Status-Gruppe statt Option
            view["group_status"] = True
        sb = v.get("sub")                                    # 2.5: Unteraufgaben verschachtelt anzeigen
        if isinstance(sb, dict) and sb.get("parent") in pids and pids[sb["parent"]].get("type") == "relation" \
                and pids[sb["parent"]].get("role") == "parent":
            view["sub"] = {"parent": sb["parent"], "mode": sb.get("mode") if sb.get("mode") in SUB_MODES else "nested"}
        if v.get("open") in ("center", "page", "left"):    # Eintraege oeffnen als (Standard: Seitenansicht rechts)
            view["open"] = v["open"]
        if v.get("ext") is True:                            # Kalender-Ansicht: externe Termine einblenden
            view["ext"] = True
        if v.get("card") in ("s", "l"):
            view["card"] = v["card"]
        fl = [dict({"pid": f["pid"], "op": f["op"], "value": "" if f.get("value") is None else str(f["value"])[:200]},
                   **({"self": True} if f.get("self") is True else {}))           # 2.5: "diese Seite" (Vorlagen, P5-E3)
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
        if isinstance(p, dict) and isinstance(p.get("fx_orig"), dict) and p["fx_orig"].get("type") in FX_TYPES:
            p = dict(p, **p["fx_orig"])              # aeltere Gegenstelle (ohne formula1) sah eine Text-Spalte: Original zurueck
        if not isinstance(p, dict):
            raise ValueError(t("ungültige Eigenschaft"))
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
                opt = {"id": oid, "name": str(o.get("name", "")).strip()[:40] or t("Option"), "color": col}
                if typ == "select" and p.get("status") is True:      # 2.5: Status-Gruppe (ohne gueltige Angabe: todo)
                    opt["group"] = o["group"] if o.get("group") in STATUS_GROUPS else "todo"
                opts.append(opt)
            prop["options"] = opts
            if typ == "select" and p.get("status") is True:
                prop["status"] = True
        if typ == "relation":                       # Ziel-Datenbank (Besitz prueft der Aufrufer)
            tg = p.get("target")
            if not (isinstance(tg, int) or (isinstance(tg, str) and tg.isdigit())):
                raise ValueError(t("Relation braucht eine Ziel-Datenbank"))
            prop["target"] = int(tg)
            if isinstance(p.get("pair"), str) and _PID.fullmatch(p["pair"]):
                prop["pair"] = p["pair"]                # Gegenspalte (zweiseitig) - stellt nur der Server her
            if p.get("role") in REL_ROLES:              # 2.5: Unteraufgaben/Abhaengigkeiten (nur Selbst-Relation, s. u.)
                prop["role"] = p["role"]
        if typ == "calc":                           # Rechnung: Zahlen-Spalten pruefen wir nach der Schleife
            prop["op"] = p.get("op") if p.get("op") in CALC_PROP_OPS else "div"
            for k in ("a", "b"):
                if isinstance(p.get(k), str) and _PID.fullmatch(p[k]):
                    prop[k] = p[k]
        if typ == "formula":                        # Ausdruck mit Eigenschafts-IDs (prop("id")) - geprueft nach der Schleife
            ex = p.get("expr")
            if not isinstance(ex, str) or len(ex) > FX_EXPR_MAX:
                raise ValueError(t("Formel zu lang (höchstens {n} Zeichen)", n=FX_EXPR_MAX))
            prop["expr"] = ex
        if typ == "rollup":                         # Relation dieser Datenbank, Eigenschaft der Ziel-Datenbank, Rechnung
            for k in ("rel", "target"):
                if isinstance(p.get(k), str) and (_PID.fullmatch(p[k]) or (k == "target" and p[k] == "title")):
                    prop[k] = p[k]
            prop["fn"] = p.get("fn") if p.get("fn") in ROLLUP_FNS else "count"
        if typ == "date":                           # 2.5 (datetime1): Eingabe mit Uhrzeit / als Zeitraum anbieten
            for k in ("time", "range"):
                if p.get(k) is True:
                    prop[k] = True
        if typ in ("number", "calc", "formula", "rollup"):
            if p.get("fmt") in NUM_FMT:
                prop["fmt"] = p["fmt"]
            if p.get("show") in NUM_SHOW:
                prop["show"] = p["show"]
        if isinstance(p.get("width"), (int, float)):
            prop["width"] = max(60, min(int(p["width"]), 600))
        out.append(prop)
    rels = {p["id"] for p in out if p["type"] == "relation"}
    for p in out:                                    # Rollup nur ueber eine Relation dieser Datenbank
        if p["type"] == "rollup" and p.get("rel") not in rels:
            p.pop("rel", None)
    from saga.formula import annotate
    annotate(out, raw.get("title_name"))            # Ergebnistyp (rtype) setzt der Server
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
    dt = raw.get("default_tpl")                      # 2.5: Standardvorlage fuer "+ Neu" (ID einer Vorlage dieser Datenbank)
    if isinstance(dt, int) and not isinstance(dt, bool) and 0 < dt < 2 ** 62:
        res["default_tpl"] = dt
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
    in anderen bleibt die Gegenseite, wie sie ist). 2.5: Gegenseite mit Rolle 'parent' (Unteraufgaben) hat hoechstens
    einen Wert - ein neuer Elterneintrag ersetzt den alten (der verliert das Kind)."""
    for p in schema.get("props", []):
        if p.get("type") != "relation" or not p.get("pair"):
            continue
        old, new = set(old_props.get(p["id"]) or []), set(new_props.get(p["id"]) or [])
        for tid in new - old:
            if may is None or may(tid):
                if p.get("role") == "children":     # nur bei Selbst-Relationen gesetzt
                    _rel_reparent(c, uid, p, tid, row_id)
                _rel_mirror_set(c, uid, p["target"], tid, p["pair"], row_id, True)
        for tid in old - new:
            if may is None or may(tid):
                _rel_mirror_set(c, uid, p["target"], tid, p["pair"], row_id, False)


def _rel_reparent(c, uid, p, child, new_parent):
    """Kind bekommt einen neuen Elterneintrag (Gegenseite 'parent' hat max. 1 Wert): alten Elterneintrag loesen."""
    r = c.execute("SELECT props FROM note WHERE id=? AND uid=? AND parent_id=? AND deleted_at IS NULL", (child, uid, p["target"])).fetchone()
    if not r:
        return
    cur = [x for x in (_json_load(r[0], {}).get(p["pair"]) or []) if isinstance(x, int) and not isinstance(x, bool)]
    for old in cur:
        if old != new_parent:
            _rel_mirror_set(c, uid, p["target"], child, p["pair"], old, False)
            _rel_mirror_set(c, uid, p["target"], old, p["id"], child, False)


SUB_DEPTH_MAX = 10


def _self_rel_check(c, uid, schema, row_id, props):
    """2.5: Selbst-Relationen mit Rolle - kein Selbstbezug, Unteraufgaben ohne Kreis und hoechstens SUB_DEPTH_MAX tief.
    row_id None = neue Zeile (kann noch keinen Kreis bilden). ValueError -> 400."""
    rels = [p for p in schema.get("props", []) if p.get("type") == "relation" and p.get("role") in REL_ROLES]
    if not rels or not props:
        return
    dbid = rels[0]["target"]
    for p in rels:
        if row_id is not None and row_id in (props.get(p["id"]) or []):
            raise ValueError(t("Ein Eintrag kann nicht mit sich selbst verknüpft sein"))
    par = next((p for p in rels if p["role"] == "parent"), None)
    kids = next((p for p in rels if p["role"] == "children"), None)
    if not par and not kids:
        return
    cache = {}

    def parent_of(i):
        if i not in cache:
            r = c.execute("SELECT props FROM note WHERE id=? AND uid=? AND parent_id=? AND deleted_at IS NULL", (i, uid, dbid)).fetchone()
            v = (_json_load(r[0], {}).get(par["id"] if par else kids["pair"]) or []) if r else []
            cache[i] = v[0] if v and isinstance(v[0], int) else None
        return cache[i]

    def ancestors(i):
        out, cur = [], i
        while cur is not None and len(out) <= SUB_DEPTH_MAX + 1:
            cur = parent_of(cur)
            if cur is None or cur in out:
                break
            out.append(cur)
        return out
    new_par = (props.get(par["id"]) or [None])[0] if par else None
    up = ([new_par] + ancestors(new_par)) if new_par is not None else []
    if row_id is not None and row_id in up:
        raise ValueError(t("Unteraufgaben dürfen keinen Kreis bilden"))
    if len(up) >= SUB_DEPTH_MAX:
        raise ValueError(t("Unteraufgaben höchstens {n} Ebenen tief", n=SUB_DEPTH_MAX))
    if kids and row_id is not None:                  # neue Kinder duerfen keine Vorfahren dieser Zeile sein
        anc = up if par else ancestors(row_id)
        if set(props.get(kids["id"]) or []) & set(anc):
            raise ValueError(t("Unteraufgaben dürfen keinen Kreis bilden"))


def _self_rel_backfill(c, uid, dbid, a, b):
    """Neu gekoppelte Selbst-Relation (Spalte a, Gegenspalte b in derselben Datenbank): Werte von a nach b spiegeln."""
    s = _db_schema(c, uid, dbid) or {}
    pa = next((p for p in s.get("props", []) if p.get("id") == a), {})
    pb = next((p for p in s.get("props", []) if p.get("id") == b), {})
    rows = c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL ORDER BY id", (uid, dbid)).fetchall()
    taken = set()
    for row_id, pr in rows:
        d = _json_load(pr, {})
        vals = [x for x in (d.get(a) or []) if isinstance(x, int) and not isinstance(x, bool) and x != row_id]
        if pa.get("role") == "parent":
            vals = vals[:1]
            if vals != (d.get(a) or []):
                if vals:
                    d[a] = vals
                else:
                    d.pop(a, None)
                c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(d, ensure_ascii=False), row_id, uid))
        for tid in vals:
            if pb.get("role") == "parent":           # Kind hat schon einen Elterneintrag: der erste gewinnt
                if tid in taken:
                    _rel_mirror_set(c, uid, dbid, row_id, a, tid, False)
                    continue
                taken.add(tid)
            _rel_mirror_set(c, uid, dbid, tid, b, row_id, True)


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


_DATE_PART = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]([0-9]{2}):([0-9]{2})(?::[0-9]{2}(?:\.[0-9]{1,9})?)?(Z|[+-][0-9]{2}:?[0-9]{2}))?")


def _date_part(s):
    """Ein Teil eines Datumswerts -> (normalisiert, Vergleichsschluessel) | ValueError.
    'JJJJ-MM-TT' oder Zeitpunkt mit Offset (Pflicht) -> 'JJJJ-MM-TTTHH:MM+hh:mm' (Sekunden fallen weg, Z = +00:00)."""
    m = _DATE_PART.fullmatch(s.strip())
    if not m:
        try:                                      # wie bisher (date.fromisoformat): auch '20261027'
            dd = date.fromisoformat(s.strip())
        except ValueError:
            raise ValueError(t("ungültiges Datum"))
        return dd.isoformat(), (dd.toordinal() * 1440, False)
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        dd = date(y, mo, d)
    except ValueError:
        raise ValueError(t("ungültiges Datum"))
    if m.group(4) is None:
        return dd.isoformat(), (dd.toordinal() * 1440, False)
    hh, mi, off = int(m.group(4)), int(m.group(5)), m.group(6)
    if not 1900 <= y <= 9998:                     # Uhrzeit: Zeitzonen-Regeln vor 1900 (Ortszeit) weichen je Client ab
        raise ValueError(t("ungültiges Datum"))
    if off == "Z":
        off = "+00:00"
    elif ":" not in off:
        off = off[:3] + ":" + off[3:]
    oh, om = int(off[1:3]), int(off[4:6])
    if hh > 23 or mi > 59 or oh > 18 or om > 59 or (oh == 18 and om):
        raise ValueError(t("ungültiges Datum"))
    sg = -1 if off[0] == "-" else 1
    if off == "-00:00":
        off = "+00:00"
    return f"{dd.isoformat()}T{hh:02d}:{mi:02d}{off}", (dd.toordinal() * 1440 + hh * 60 + mi - sg * (oh * 60 + om), True)


def clean_date(v, old=None):
    """Datumswert (2.5, P5-E1): Tag 'JJJJ-MM-TT', Zeitpunkt mit Offset 'JJJJ-MM-TTTHH:MM+01:00' oder Zeitraum 'start/end'
    (Ende >= Start; Tag/Zeitpunkt gemischt erlaubt). Alte Apps schreiben nur den Tag zurueck: ist der gespeicherte Wert
    (old) ein Zeitpunkt bzw. Zeitraum mit demselben Starttag, bleibt er erhalten (Uhrzeit/Ende gehen nicht verloren)."""
    s = str(v).strip()
    parts = s.split("/")
    if len(parts) > 2 or len(s) > 60:
        raise ValueError(t("ungültiges Datum"))
    a, ka = _date_part(parts[0])
    if len(parts) == 2:
        b, kb = _date_part(parts[1])
        # beide Zeitpunkte: nach Zeitpunkt (Offsets gelten); sonst nach Kalendertag (Tag ohne Zeitzone)
        if (kb[0] < ka[0]) if (ka[1] and kb[1]) else (b[:10] < a[:10]):
            raise ValueError(t("Ende liegt vor dem Beginn"))
        return a if a == b else f"{a}/{b}"
    if not ka[1] and isinstance(old, str) and len(old) > 10 and old[:10] == a:
        try:
            clean_date(old)
            return old                                  # alte App: nur den Tag geschickt, Tag unveraendert -> Wert behalten
        except ValueError:
            pass
    return a


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
        if typ in AUTO_TYPES or typ == "calc" or typ in FX_TYPES:   # automatisch/berechnet - nie gespeichert, nicht faelschbar
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
            v = clean_date(v, (base or {}).get(pid))
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
            if p.get("role") == "parent":           # 2.5: Unteraufgabe hat hoechstens einen Elterneintrag
                ids = ids[:1]
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


SELF_TOKENS = ("@self", "$self")


def self_filters(body, fmt, new_id):
    """2.5 (P5-E3): Filter "diese Seite" in eingebetteten Datenbank-Bloecken (data.views[].filters, auch verschachtelt)
    auf die Seite new_id setzen - beim Anlegen aus einer Eintrags-Vorlage, beim Duplizieren und beim Galerie-Import.
    Merker self:true bleibt (die naechste Kopie wird wieder umgeschrieben). Ohne solche Filter: body unveraendert."""
    if fmt != "blocks" or not isinstance(body, str) or not any(x in body for x in ('"self"',) + SELF_TOKENS):
        return body
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    changed = False
    for b in _iter_blocks(doc.get("blocks") if isinstance(doc, dict) else None):
        if b.get("type") != "database" or not isinstance(b.get("data"), dict):
            continue
        for v in b["data"].get("views") or []:
            for f in (v.get("filters") if isinstance(v, dict) and isinstance(v.get("filters"), list) else []):
                if isinstance(f, dict) and (f.get("self") is True or f.get("value") in SELF_TOKENS):
                    f["value"], f["self"] = str(new_id), True
                    changed = True
    return json.dumps(doc, ensure_ascii=False) if changed else body
