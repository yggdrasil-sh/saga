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
  Datenbank-Block auf die Unterseite in der Elternseite, passiert nichts.
- Position: ans Ende (vor einen leeren Schluss-Absatz); after=ID (Duplizieren): direkt hinter den Block mit dem Verweis
  auf das Original, wenn es ihn gibt.
- Gespeichert wie jede serverseitige Inhaltsaenderung (page_blocks.move als Vorbild): rev+1, Version (gedrosselt),
  Basisstand fuer saga.merge, Ereignisse note.saved (Live-Kanal, collab.ingest fuer offene Live-Raeume) und page.content.
  Offene Editoren fuehren zusammen (Stufe 1: merge mit Basis; Stufe 2: ingest) statt zu ueberschreiben.
- Herkunft des Ereignisses: ORIGIN (keine Sitzung) - auch der Tab, der die Unterseite angelegt hat, bekommt die Aenderung
  an der Elternseite (seine eigene Herkunft wuerde ihn auslassen).

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
    if _has_ref(row[3], blocks, child):
        return False
    icon = ch[1] if isinstance(ch[1], str) and _TEXT_ICON.fullmatch(ch[1] or "") else ""
    label = (ch[0] or "").strip() or t_lang(account_lang(row[0]), "Unbenannt")
    text = f'<a href="/notes#{int(child)}">{escape((icon + " " if icon else "") + label, quote=False)}</a>'
    ids = {b.get("id") for b in _iter_blocks(blocks)}
    bid = _new_block_id()
    while bid in ids:
        bid = _new_block_id()
    new = {"id": bid, "type": "paragraph", "data": {"text": text}}
    at = _top_with(blocks, after) + 1 if after else 0
    if at <= 0:                            # ans Ende - vor einen leeren Schluss-Absatz (bleibt zum Weiterschreiben)
        at = len(blocks) - 1 if blocks and _empty_para(blocks[-1]) else len(blocks)
    blocks.insert(at, new)
    return _write(c, uid, parent, row, doc, time.time(), origin)


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
