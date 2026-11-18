"""Seiten-Link in der Elternseite fuer neue Unterseiten (2.5, Bug 2151 - wie Notion).

Entsteht unter einer Seite eine neue Unterseite (Web "Neue Unterseite", Vorlage, Galerie, Duplizieren, Import, API v1/MCP,
Apps, Foederation), haengt der Server am Ende der Elternseite einen Absatz mit einem Seitenverweis an - dieselbe Form, die
beide Editoren fuer "Verweis"/"[[" speichern: {"type": "paragraph", "data": {"text": '<a href="/notes#ID">Titel</a>'}}
(Editor.js und Tiptap setzen beim Anzeigen den aktuellen Titel und das Symbol ein).

Regeln (link):
- Elternseite: kind 'page', Block-Format, nicht gesperrt, nicht im Papierkorb/Archiv; wer anlegt, hat dort mindestens
  'Bearbeiten'. Datenbanken (Zeilen bleiben Zeilen), Text-Notizen und gesperrte Seiten: nur Seitenbaum, wie bisher.
- Unterseite: direktes Kind der Elternseite, keine Vorlage, nicht im Papierkorb/Archiv.
- Nicht doppelt: steht schon ein Verweis /notes#ID (irgendwo, auch in Spalten/Toggles) oder ein eingebetteter
  Datenbank-Block auf die Unterseite in der Elternseite, passiert nichts. Ebenso, wenn die Elternseite einen
  Unterseiten-Block (Widget subpages) hat - dort erscheint die neue Seite ohnehin (auch beim Verschieben hinein).
- Position: ans Ende (vor einen leeren Schluss-Absatz); after=ID (Duplizieren): direkt hinter den Block mit dem Verweis
  auf das Original, wenn es ihn gibt.
- Gespeichert wie jede serverseitige Inhaltsaenderung (page_blocks.move als Vorbild): rev+1, Version (gedrosselt),
  Basisstand fuer saga.merge, Ereignisse note.saved (Live-Kanal, collab.ingest fuer offene Live-Raeume) und page.content.
  Offene Editoren fuehren zusammen (Stufe 1: merge mit Basis; Stufe 2: ingest) statt zu ueberschreiben.
- Herkunft des Ereignisses: ORIGIN (keine Sitzung) - auch der Tab, der die Unterseite angelegt hat, bekommt die Aenderung
  an der Elternseite (seine eigene Herkunft wuerde ihn auslassen).

retitle (erster Titel, Entscheidung 18.11.): wurde die Unterseite unbenannt angelegt ("Unbenannt", Block-ID "subu-..."),
zieht ihr erster Titel den automatischen Absatz einmal nach (nur unveraendert, nur RETITLE_S nach dem Anlegen - das Web
speichert den Titel beim Tippen in Etappen); spaetere Umbenennungen nicht (Editoren und Apps zeigen ohnehin den aktuellen
Titel, der gespeicherte Text zaehlt fuer Veroeffentlichen, Export und Suche).

unlink (Verschieben in eine andere Elternseite): aus der alten Elternseite fallen nur Absaetze weg, die ausschliesslich
aus dem Verweis auf die verschobene Seite bestehen (Verweise im Fliesstext bleiben) - gleiche Bedingungen fuer die alte
Elternseite (Seite, Block-Format, nicht gesperrt, 'Bearbeiten').

Nicht hier: Bundle-Seiten einer Vorlage untereinander (gallery: nur die oberste neue Seite), Seiten innerhalb eines Imports
(Verweise kommen aus den Daten), Seiten, die der Server selbst anordnet (BookOrbit: link False)."""
import json
import re
import time
from html import escape

from saga import bus
from saga.core import account_lang
from saga.i18n import t_lang
from saga.notes import _iter_blocks, _new_block_id, _snapshot, ensure_ids, keep_base
from saga.sharing import RANK, Access

NOTE_BLOCKS_MAX = 200_000                  # wie saga.pages (Zeichen JSON je Seite)
ORIGIN = "sublink"                         # Herkunft der Ereignisse (keine Sitzung/kein Tab, siehe oben)
AUTO_ID, AUTO_UNTITLED = "sub-", "subu-"   # Block-ID des automatischen Absatzes (zufaellige IDs haben nie "-"); subu = unbenannt angelegt
RETITLE_S = 600.0                          # so lange nach dem Anlegen zieht der erste Titel den Verweistext nach
_TEXT_ICON = re.compile(r"[^/<>\"]{1,16}")


def wanted(b):
    """Anfrage-Schalter: "link": false = keinen Verweis anlegen (der Client fuegt ihn selbst ein, z. B. "[[" -> neue Seite,
    oder bettet die neue Datenbank ein). Alles andere: anlegen."""
    return not (isinstance(b, dict) and b.get("link") is False)


def _row(c, nid):
    return c.execute("SELECT uid, kind, format, body, rev, locked, archived, title, updated_by FROM note "
                     "WHERE id=? AND deleted_at IS NULL FOR UPDATE", (nid,)).fetchone()


def _may(c, uid, parent, row, acc=None):
    """Elternseite tauglich und beschreibbar fuer uid?"""
    if not row or row[1] != "page" or row[2] != "blocks" or row[5] or row[6] is not None:
        return False
    if uid == row[0]:
        return True
    return RANK.get((acc or Access(c, uid)).role(parent), 0) >= RANK["edit"]


def _ref(nid):
    """Verweis auf nid, wie er im gespeicherten JSON steht (nach der Zahl ein maskiertes Anfuehrungszeichen, vgl.
    saga.pageindex)."""
    return '/notes#' + str(int(nid)) + '\\"'


def _has_ref(body, blocks, child):
    """Verweis /notes#child oder eingebettete Datenbank child irgendwo in der Seite?"""
    if _ref(child) in (body or ""):
        return True
    return any(b.get("type") == "database" and isinstance(b.get("data"), dict) and b["data"].get("db") == child
               for b in _iter_blocks(blocks))


def _has_subpages(blocks):
    """Unterseiten-Block (Widget subpages) in der Seite? Dann erscheint die neue Seite dort ohnehin (Entscheidung 18.11.)."""
    return any(b.get("type") == "widget" and isinstance(b.get("data"), dict) and b["data"].get("kind") == "subpages"
               for b in _iter_blocks(blocks))


def _top_with(blocks, nid):
    """Index des obersten Blocks, der einen Verweis auf nid enthaelt (auch in Spalten/Toggles), oder -1."""
    r = _ref(nid)
    for i, b in enumerate(blocks):
        if isinstance(b, dict) and r in json.dumps(b, ensure_ascii=False):
            return i
    return -1


def _empty_para(b):
    if not isinstance(b, dict) or b.get("type") != "paragraph":
        return False
    tx = (b.get("data") or {}).get("text") if isinstance(b.get("data"), dict) else ""
    return not re.sub(r"<br\s*/?>|&nbsp;|\s", "", str(tx or ""))


def _write(c, uid, nid, row, doc, now, origin):
    body = json.dumps(doc, ensure_ascii=False)
    if len(body) > NOTE_BLOCKS_MAX:
        return False
    owner, rev = row[0], row[4]
    _snapshot(c, owner, nid, (row[7], row[3], row[2]), now, author=row[8], by=uid)
    c.execute("UPDATE note SET body=?, rev=rev+1, updated_by=?, updated=? WHERE id=? AND uid=?", (body, uid, now, nid, owner))
    keep_base(c, nid, rev + 1, row[7], body, "blocks", now, (rev, row[7], row[3], row[2]))
    bus.fire("note.saved", c, note_id=nid, owner=owner, rev=rev + 1, by=uid, updated=now, origin=origin or ORIGIN)
    bus.fire("page.content", c, owner=owner, note_id=nid, by=uid)
    return True


def link(c, uid, parent, child, after=None, origin=None, acc=None):
    """Verweis auf child am Ende von parent anlegen (in der Transaktion von c; Regeln siehe oben). -> True, wenn angelegt.
    Fehler werden nicht weitergereicht: das Anlegen der Unterseite bleibt in jedem Fall bestehen."""
    if not parent or not child or (isinstance(parent, int) and parent < 0):
        return False
    sp = getattr(c, "savepoint", None)
    try:
        if sp is None:
            return _link(c, uid, parent, child, after, origin, acc)
        with sp():
            return _link(c, uid, parent, child, after, origin, acc)
    except Exception as ex:  # noqa: BLE001 - der Verweis ist Beiwerk, die neue Seite zaehlt
        print(f"sublink: Verweis {parent} -> {child}: {type(ex).__name__}: {ex}", flush=True)
        return False


def _link(c, uid, parent, child, after, origin, acc):
    row = _row(c, parent)
    if not _may(c, uid, parent, row, acc):
        return False
    ch = c.execute("SELECT title, icon, is_template, archived, parent_id, uid FROM note WHERE id=? AND deleted_at IS NULL",
                   (child,)).fetchone()
    if not ch or ch[2] or ch[3] is not None or ch[4] != parent or ch[5] != row[0]:
        return False
    doc = json.loads(ensure_ids(row[3], "blocks", parent) or '{"blocks": []}')
    if not isinstance(doc, dict) or not isinstance(doc.get("blocks"), list):
        return False
    blocks = doc["blocks"]
    if _has_ref(row[3], blocks, child) or _has_subpages(blocks):
        return False
    text = auto_text(child, ch[1], ch[0], row[0])
    ids = {b.get("id") for b in _iter_blocks(blocks)}
    pre = AUTO_UNTITLED if not (ch[0] or "").strip() else AUTO_ID
    bid = pre + _new_block_id()[:8]
    while bid in ids:
        bid = pre + _new_block_id()[:8]
    new = {"id": bid, "type": "paragraph", "data": {"text": text}}
    at = _top_with(blocks, after) + 1 if after else 0
    if at <= 0:                            # ans Ende - vor einen leeren Schluss-Absatz (bleibt zum Weiterschreiben)
        at = len(blocks) - 1 if blocks and _empty_para(blocks[-1]) else len(blocks)
    blocks.insert(at, new)
    return _write(c, uid, parent, row, doc, time.time(), origin)


def auto_text(child, icon, title, owner):
    """Text des automatischen Verweis-Absatzes: <a href="/notes#ID">Symbol Titel</a> (Text-Symbol, leer = "Unbenannt"
    in der Sprache des Eigentuemers)."""
    icon = icon if isinstance(icon, str) and _TEXT_ICON.fullmatch(icon or "") else ""
    label = (title or "").strip() or t_lang(account_lang(owner), "Unbenannt")
    return f'<a href="/notes#{int(child)}">{escape((icon + " " if icon else "") + label, quote=False)}</a>'


def retitle(c, uid, child, old_title, new_title, origin=None):
    """Erster Titel (Entscheidung 18.11.): eine unbenannt angelegte Unterseite bekommt ihren Titel -> der automatische
    Verweis-Absatz in der Elternseite (ID "subu-...") wird auf "Symbol Titel" nachgezogen - nur, solange er noch genau
    der automatische Text zum bisherigen Titel ist (also unveraendert) und nur in den ersten RETITLE_S Sekunden nach dem
    Anlegen (das Web speichert den Titel beim Tippen in Etappen; spaetere Umbenennungen bleiben unberuehrt).
    In der Transaktion von c; Fehler werden nicht weitergereicht. -> True, wenn geschrieben."""
    if not (new_title or "").strip() or (new_title or "").strip() == (old_title or "").strip():
        return False
    sp = getattr(c, "savepoint", None)
    try:
        if sp is None:
            return _retitle(c, uid, child, old_title, new_title, origin)
        with sp():
            return _retitle(c, uid, child, old_title, new_title, origin)
    except Exception as ex:  # noqa: BLE001
        print(f"sublink: Titel nachziehen {child}: {type(ex).__name__}: {ex}", flush=True)
        return False


def _retitle(c, uid, child, old_title, new_title, origin):
    ch = c.execute("SELECT parent_id, icon, created, uid FROM note WHERE id=? AND deleted_at IS NULL", (child,)).fetchone()
    if not ch or not ch[0] or time.time() - (ch[2] or 0) > RETITLE_S:
        return False
    parent = ch[0]
    row = _row(c, parent)
    if not _may(c, uid, parent, row) or AUTO_UNTITLED not in (row[3] or "") or _ref(child) not in (row[3] or ""):
        return False
    doc = json.loads(ensure_ids(row[3], "blocks", parent) or '{"blocks": []}')
    if not isinstance(doc, dict) or not isinstance(doc.get("blocks"), list):
        return False
    before = {auto_text(child, ic, old_title, row[0]) for ic in (ch[1], "")}
    hit = 0
    for b in _iter_blocks(doc["blocks"]):
        d = b.get("data") if isinstance(b.get("data"), dict) else None
        if (b.get("type") == "paragraph" and d is not None and str(b.get("id") or "").startswith(AUTO_UNTITLED)
                and d.get("text") in before):
            d["text"] = auto_text(child, ch[1], new_title, row[0])
            hit += 1
    return bool(hit) and _write(c, uid, parent, row, doc, time.time(), origin)


_AUTO_TEXT = re.compile(r'^(?:\s|&nbsp;|\u00a0|<br\s*/?>)*<a href="/notes#([1-9][0-9]{0,17})">[^<]*</a>(?:\s|&nbsp;|\u00a0|<br\s*/?>)*$')


def _auto_of(b):
    """Automatischer Verweis-Absatz? -> Seiten-ID oder None (ID sub-/subu-, Absatz nur aus dem Verweis)."""
    if not isinstance(b, dict) or b.get("type") != "paragraph" or not str(b.get("id") or "").startswith(("sub-", "subu-")):
        return None
    m = _AUTO_TEXT.match(str((b.get("data") or {}).get("text") or "") if isinstance(b.get("data"), dict) else "")
    return int(m.group(1)) if m else None


def _refs(blocks, child, skip_id):
    """Verweise/Datenbank-Bloecke auf child ausserhalb des Blocks skip_id (Anzahl Bloecke)."""
    r, n = _ref(child), 0
    for b in _iter_blocks(blocks):
        if b.get("id") == skip_id:
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "database" and d.get("db") == child:
            n += 1
        elif b.get("type") not in ("columns", "toggle", "synced") and r in json.dumps(d, ensure_ascii=False):
            n += 1
    return n


def dedupe(c, old_body, body):
    """Aeltere Apps (iOS/Android/Linux "/Seite" und "/Datenbank", Stand 2.5-rc) legen die Unterseite an und setzen danach
    SELBST einen Verweis bzw. Datenbank-Block in die Elternseite - zusammen mit dem automatischen Absatz stuende sie doppelt
    da. Beim Speichern der Elternseite (Stufe 1, auch zusammengefuehrt) faellt der automatische Absatz deshalb weg, wenn
    - er schon gespeichert war und in diesem Speichern ein anderer Verweis auf dieselbe Seite neu dazukommt (nur in den
      ersten RETITLE_S Sekunden nach dem Anlegen der Unterseite), oder
    - er im gespeicherten Stand fehlt (veralteter Client schickt ihn noch einmal mit) und die Seite anderswo verlinkt ist.
    -> body (unveraendert oder ohne diese Absaetze). Live-Raeume (collab, trusted) laufen nicht hierueber."""
    if AUTO_ID not in body and AUTO_UNTITLED not in body:
        return body
    try:
        doc = json.loads(body)
        old = json.loads(old_body) if old_body else {}
    except ValueError:
        return body
    blocks = doc.get("blocks") if isinstance(doc, dict) else None
    if not isinstance(blocks, list):
        return body
    oblocks = old.get("blocks") if isinstance(old, dict) and isinstance(old.get("blocks"), list) else []
    old_ids = {b.get("id") for b in _iter_blocks(oblocks)}
    drop = set()
    for b in _iter_blocks(blocks):
        child = _auto_of(b)
        if child is None or not _refs(blocks, child, b.get("id")):
            continue
        if b.get("id") not in old_ids:
            drop.add(b.get("id"))
            continue
        if _refs(blocks, child, b.get("id")) > _refs(oblocks, child, b.get("id")):
            r = c.execute("SELECT created FROM note WHERE id=?", (child,)).fetchone()
            if r and time.time() - (r[0] or 0) <= RETITLE_S:
                drop.add(b.get("id"))
    if not drop:
        return body

    def walk(lst, depth=0):
        if depth > 8 or not isinstance(lst, list):
            return lst
        out = [x for x in lst if not (isinstance(x, dict) and x.get("id") in drop)]
        for x in out:
            d = x.get("data") if isinstance(x, dict) and isinstance(x.get("data"), dict) else {}
            if x.get("type") == "columns" and isinstance(d.get("cols"), list):
                for col in d["cols"]:
                    if isinstance(col, dict) and isinstance(col.get("blocks"), list):
                        col["blocks"] = walk(col["blocks"], depth + 1)
            elif x.get("type") in ("toggle", "synced") and isinstance(d.get("blocks"), list):
                d["blocks"] = walk(d["blocks"], depth + 1)
        return out
    doc["blocks"] = walk(blocks)
    return json.dumps(doc, ensure_ascii=False)


_ONLY = r'^(?:\s|&nbsp;| |<br\s*/?>)*<a href="/notes#{id}"(?:\s[^>]*)?>[^<]*</a>(?:\s|&nbsp;| |<br\s*/?>)*$'


def unlink(c, uid, parent, child, origin=None, acc=None):
    """Verschieben: Absaetze, die nur aus dem Verweis auf child bestehen, aus parent entfernen. -> Anzahl entfernter."""
    if not parent or not child or (isinstance(parent, int) and parent < 0):
        return 0
    sp = getattr(c, "savepoint", None)
    try:
        if sp is None:
            return _unlink(c, uid, parent, child, origin, acc)
        with sp():
            return _unlink(c, uid, parent, child, origin, acc)
    except Exception as ex:  # noqa: BLE001
        print(f"sublink: Verweis entfernen {parent} -> {child}: {type(ex).__name__}: {ex}", flush=True)
        return 0


def _unlink(c, uid, parent, child, origin, acc):
    row = _row(c, parent)
    if not _may(c, uid, parent, row, acc) or _ref(child) not in (row[3] or ""):
        return 0
    doc = json.loads(ensure_ids(row[3], "blocks", parent) or '{"blocks": []}')
    if not isinstance(doc, dict) or not isinstance(doc.get("blocks"), list):
        return 0
    only = re.compile(_ONLY.replace("{id}", str(int(child))))
    gone = 0

    def keep(b):
        nonlocal gone
        if isinstance(b, dict) and b.get("type") == "paragraph" and isinstance(b.get("data"), dict) \
                and only.match(str(b["data"].get("text") or "")):
            gone += 1
            return False
        return True

    def walk(lst, depth=0):
        if depth > 8 or not isinstance(lst, list):
            return lst
        out = [b for b in lst if keep(b)]
        for b in out:
            d = b.get("data") if isinstance(b, dict) and isinstance(b.get("data"), dict) else {}
            if b.get("type") == "columns" and isinstance(d.get("cols"), list):
                for col in d["cols"]:
                    if isinstance(col, dict) and isinstance(col.get("blocks"), list):
                        col["blocks"] = walk(col["blocks"], depth + 1)
            elif b.get("type") == "toggle" and isinstance(d.get("blocks"), list):
                d["blocks"] = walk(d["blocks"], depth + 1)
        return out
    doc["blocks"] = walk(doc["blocks"])
    if not gone:
        return 0
    return gone if _write(c, uid, parent, row, doc, time.time(), origin) else 0
