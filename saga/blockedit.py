"""Bloecke einer Seite gezielt aendern (2.5, MCP-Wunsch 2023): Gliederung mit Block-IDs, Block/Abschnitt ersetzen,
einfuegen, loeschen, Kaestchen einer Checkliste umschalten. Reine Funktionen auf Editor.js-Blocklisten (in place) -
Lesen, Rechte, Revision und Speichern macht saga.api_v1 (gleicher Weg wie das Web: pages._note_update).

Regeln
- Ziel per Block-ID (auch in Toggles, Spalten, synchronisierten Originalen) oder per Ueberschrift (Text exakt nach
  Normalisierung: ohne Formatierung, Leerraum zusammengefasst, gross/klein egal). Ueberschrift = Block "header" oder
  Toggle-Ueberschrift (toggle mit level 1-3). Mehrdeutig/fehlt -> EditError mit Kandidaten.
- Abschnitt = Ueberschrift + alle folgenden Bloecke derselben Liste bis zur naechsten Ueberschrift gleicher oder hoeherer
  Ebene (kleinere oder gleiche Zahl). Bei einer Toggle-Ueberschrift: ihr Inhalt.
- Neue Bloecke (aus Markdown) bekommen frische IDs; der erste uebernimmt die ID des ersetzten Blocks (Kommentare am Block
  bleiben haengen). Alle anderen Bloecke bleiben unveraendert (dieselben dicts, dieselben IDs).
- Inhalt von Kopien synchronisierter Bloecke wird hier nie geaendert (EditError mit Hinweis auf das Original)."""
import re
import secrets
import unicodedata
from html import unescape

from saga.core import t
from saga.notes import BLOCK_ID_RE, CONTAINERS, MAX_NEST, _new_block_id, _strip_tags, blocks_text, special_text, SPECIAL_BLOCKS

TEXT_MAX, ITEM_MAX, OUTLINE_MAX, CAND_MAX = 120, 100, 1500, 20
MODES = ("replace_block", "replace_section", "insert_after", "delete")


class EditError(Exception):
    def __init__(self, status, msg, cands=None):
        super().__init__(msg)
        self.status, self.msg, self.cands = status, msg, cands or []


def norm(s):
    """Vergleichstext: ohne HTML/Entitaeten, NFKC, Leerraum zusammengefasst, gross/klein egal, ohne Doppelpunkt am Ende."""
    s = unescape(re.sub(r"<[^>]*>", "", str(s or "")))
    s = unicodedata.normalize("NFKC", s).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().rstrip(":").strip().casefold()


def _short(s, n=TEXT_MAX):
    s = re.sub(r"\s+", " ", _strip_tags(str(s or ""))).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _data(b):
    return b.get("data") if isinstance(b.get("data"), dict) else {}


def heading_level(b):
    """Ebene einer Ueberschrift (header 1-6, Toggle-Ueberschrift 1-3) oder None."""
    d = _data(b)
    lv = d.get("level")
    if not isinstance(lv, int) or isinstance(lv, bool):
        return None
    if b.get("type") == "header":
        return min(max(lv, 1), 6)
    if b.get("type") == "toggle" and 1 <= lv <= 3:
        return lv
    return None


def heading_text(b):
    d = _data(b)
    return d.get("title", "") if b.get("type") == "toggle" else d.get("text", "")


def is_copy(b):
    """Kopie eines synchronisierten Blocks (nicht das Original)."""
    if b.get("type") != "synced":
        return False
    src = _data(b).get("src")
    return not (isinstance(src, dict) and src.get("original"))


def block_text(b):
    typ, d = b.get("type"), _data(b)
    if typ == "toggle":
        return _short(d.get("title"))
    if typ in ("synced", "columns"):
        return ""
    if typ == "list":
        it = (d.get("items") or [""])[0] if isinstance(d.get("items"), list) and d.get("items") else ""
        return _short(it.get("content", "") if isinstance(it, dict) else it)
    if typ == "checklist":
        it = (d.get("items") or [{}])[0] if isinstance(d.get("items"), list) and d.get("items") else {}
        return _short(it.get("text", "") if isinstance(it, dict) else it)
    if typ == "code":
        return _short(str(d.get("code") or "").split("\n", 1)[0])
    if typ == "table":
        rows = d.get("content") if isinstance(d.get("content"), list) else []
        return _short(" | ".join(str(x) for x in rows[0]) if rows and isinstance(rows[0], list) else "")
    if typ == "image":
        return _short(d.get("caption"))
    if typ == "widget":
        return _short(d.get("title") or d.get("kind"))
    if typ in SPECIAL_BLOCKS:
        return _short(special_text(typ, d))
    if "text" in d:
        return _short(d.get("text"))
    return _short(blocks_text([b]))


def _check_items(b):
    """Punkte einer Checkliste/Liste -> [(index "2.1", item-dict oder str, eltern-liste, pos)] in Lesereihenfolge."""
    d = _data(b)
    out = []
    if b.get("type") == "checklist":
        for i, it in enumerate(d.get("items") if isinstance(d.get("items"), list) else [], 1):
            out.append((str(i), it, d["items"], i - 1))
        return out

    def walk(lst, pre, depth):
        if depth > MAX_NEST or not isinstance(lst, list):
            return
        for i, it in enumerate(lst, 1):
            idx = f"{pre}{i}"
            out.append((idx, it, lst, i - 1))
            if isinstance(it, dict):
                walk(it.get("items"), idx + ".", depth + 1)
    walk(d.get("items"), "", 0)
    return out


def is_checklist(b):
    return b.get("type") == "checklist" or (b.get("type") == "list" and _data(b).get("style") == "checklist")


def _item_text(b, it):
    if isinstance(it, str):
        return it
    if not isinstance(it, dict):
        return ""
    return it.get("text", "") if b.get("type") == "checklist" else it.get("content", "")


def _item_checked(b, it):
    if not isinstance(it, dict):
        return False
    return bool(it.get("checked")) if b.get("type") == "checklist" else bool((it.get("meta") or {}).get("checked"))


def walk(blocks, chain=(), depth=0, col=None):
    """-> [(block, liste, index, ahnen (Bloecke von aussen nach innen), spalte)] fuer alle Bloecke in Lesereihenfolge."""
    out = []
    if depth > MAX_NEST or not isinstance(blocks, list):
        return out
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        out.append((b, blocks, i, chain, col))
        d = _data(b)
        if b.get("type") == "columns" and isinstance(d.get("cols"), list):
            for ci, c in enumerate(d["cols"]):
                if isinstance(c, dict) and isinstance(c.get("blocks"), list):
                    out += walk(c["blocks"], chain + (b,), depth + 1, ci + 1)
        elif b.get("type") in CONTAINERS and isinstance(d.get("blocks"), list):
            out += walk(d["blocks"], chain + (b,), depth + 1)
    return out


def outline(blocks):
    """Knappe Gliederung: [{id, type, text, level?, style?, items?, parent?, col?, synced?}] (flach, verschachtelte
    Bloecke mit parent = ID des Toggles/der Spalten/des synchronisierten Blocks)."""
    out = []
    for b, _lst, _i, chain, col in walk(blocks):
        if len(out) >= OUTLINE_MAX:
            out.append({"more": t("Gliederung gekürzt: weitere Blöcke nicht aufgeführt.")})
            break
        e = {"id": b.get("id"), "type": b.get("type")}
        txt = block_text(b)
        if txt:
            e["text"] = txt
        lv = heading_level(b)
        if lv is not None:
            e["level"] = lv
        d = _data(b)
        if b.get("type") == "list":
            e["style"] = d.get("style") or "unordered"
        if b.get("type") == "synced":
            e["synced"] = "copy" if is_copy(b) else "original"
        if b.get("type") == "widget" and d.get("kind"):
            e["kind"] = d.get("kind")
        if b.get("type") in ("list", "checklist"):
            chk = is_checklist(b)
            items = []
            for idx, it, _l, _p in _check_items(b)[:200]:
                x = {"index": idx, "text": _short(_item_text(b, it), ITEM_MAX)}
                if chk:
                    x["checked"] = _item_checked(b, it)
                items.append(x)
            e["items"] = items
        if chain:
            e["parent"] = chain[-1].get("id")
            if col is not None:
                e["col"] = col
            if any(is_copy(a) for a in chain):
                e["in_copy"] = True
        out.append(e)
    return out


def strip_view(blocks):
    """Lese-Zusaetze vor dem Speichern entfernen: data.src (nie gespeichert) und eingesetzter Inhalt von Kopien."""
    for b, *_ in walk(blocks):
        if b.get("type") != "synced":
            continue
        d = _data(b)
        if is_copy(b):
            b["data"] = {k: v for k, v in d.items() if k not in ("blocks", "src")}
        else:
            d.pop("src", None)


def _copy_hint(b):
    src = _data(b).get("src") if isinstance(_data(b).get("src"), dict) else {}
    if src.get("note"):
        return t("Synchronisierter Block: Inhalt nur im Original ändern (Seite {id}).", id=src["note"])
    return t("Synchronisierter Block: Inhalt nur im Original ändern.")


def find_block(blocks, bid):
    """-> (block, liste, index, ahnen). EditError 404, wenn es die ID nicht gibt; 400 in/als Kopie."""
    if not isinstance(bid, str) or not BLOCK_ID_RE.fullmatch(bid):
        raise EditError(400, t("ungültige Block-ID"))
    for b, lst, i, chain, _col in walk(blocks):
        if b.get("id") == bid:
            for a in chain + (b,):
                if is_copy(a):
                    raise EditError(400, _copy_hint(a))
            return b, lst, i, chain
    raise EditError(404, t("Block {id} nicht gefunden – Block-IDs mit get_page (blocks: true) lesen.", id=bid[:40]))


def find_heading(blocks, text):
    want = norm(text)
    if not want:
        raise EditError(400, t("Überschrift fehlt"))
    heads = [(b, lst, i, chain) for b, lst, i, chain, _c in walk(blocks) if heading_level(b) is not None
             and not any(is_copy(a) for a in chain)]
    hits = [h for h in heads if norm(heading_text(h[0])) == want]
    if len(hits) == 1:
        return hits[0]
    cands = [{"id": h[0].get("id"), "level": heading_level(h[0]), "text": _short(heading_text(h[0]))} for h in (hits or heads)][:CAND_MAX]
    if hits:
        raise EditError(400, t("Überschrift ist mehrdeutig – bitte block_id angeben."), cands)
    raise EditError(404, t("Überschrift nicht gefunden."), cands)


def section_end(lst, i):
    """Ende (exklusiv) des Abschnitts der Ueberschrift lst[i] in derselben Liste."""
    lv = heading_level(lst[i])
    j = i + 1
    while j < len(lst):
        lj = heading_level(lst[j]) if isinstance(lst[j], dict) else None
        if lj is not None and lj <= lv:
            break
        j += 1
    return j


def used_ids(blocks):
    return {b.get("id") for b, *_ in walk(blocks) if isinstance(b.get("id"), str)}


def fresh_ids(new, used, keep=None):
    """Neue Bloecke: frische, eindeutige IDs (auch verschachtelt); der erste bekommt keep (ID des ersetzten Blocks)."""
    for n, (b, *_r) in enumerate(walk(new)):
        if n == 0 and keep and b is new[0]:
            b["id"] = keep
            continue
        bid = _new_block_id()
        while bid in used:
            bid = _new_block_id() + secrets.choice("xyz")
        b["id"] = bid
        used.add(bid)


def apply(blocks, new, mode, block_id=None, heading=None):
    """Aenderung in place. new = Bloecke aus dem Markdown (fuer delete leer). -> {"mode", "target", "ids": neue IDs}."""
    if (block_id is None) == (heading is None):
        raise EditError(400, t("Genau eines angeben: block_id oder heading."))
    if mode is None:
        mode = "replace_block" if block_id is not None else "replace_section"
    if mode not in MODES:
        raise EditError(400, t("Unbekannter Modus: {m}", m=str(mode)[:30]))
    b, lst, i, _chain = find_block(blocks, block_id) if block_id is not None else find_heading(blocks, heading)
    target = b.get("id")
    used = used_ids(blocks)
    if mode != "delete" and not new and mode != "replace_section":
        raise EditError(400, t("Markdown fehlt (zum Entfernen mode „delete“)."))
    lv = heading_level(b)
    if mode in ("replace_section",) and lv is None:
        raise EditError(400, t("Der Block ist keine Überschrift – replace_section geht nur mit einer Überschrift."))
    if mode == "replace_block":
        fresh_ids(new, used, keep=target)
        lst[i:i + 1] = new
    elif mode == "insert_after":
        fresh_ids(new, used)
        if heading is not None:                  # nach dem ganzen Abschnitt (Ende der Toggle-Ueberschrift: in ihr)
            if b.get("type") == "toggle":
                inner = _data(b).setdefault("blocks", [])
                inner.extend(new)
            else:
                j = section_end(lst, i)
                lst[j:j] = new
        else:
            lst[i + 1:i + 1] = new
    elif mode == "delete":
        if heading is not None and b.get("type") != "toggle":
            del lst[i:section_end(lst, i)]       # per Ueberschrift: der ganze Abschnitt
        else:
            del lst[i]
    else:                                        # replace_section
        own_head = bool(new) and heading_level(new[0]) is not None and new[0].get("type") == "header"
        if b.get("type") == "toggle":
            if own_head:                         # eigene erste Ueberschrift: neuer Titel der Toggle-Ueberschrift
                _data(b)["title"] = _data(new[0]).get("text", "")
                new = new[1:]
            fresh_ids(new, used)
            _data(b)["blocks"] = new
        else:
            j = section_end(lst, i)
            if own_head:
                fresh_ids(new, used, keep=target)
                lst[i:j] = new
            else:
                fresh_ids(new, used)
                lst[i + 1:j] = new
    return {"mode": mode, "target": target, "ids": [x.get("id") for x in new]}


def set_checkbox(blocks, checked, block_id=None, item=None, text=None):
    """Kaestchen setzen. Ziel: block_id + item ("2.1") oder text (eindeutig; ohne block_id in allen Checklisten der Seite).
    -> {"block_id", "item", "text", "checked", "changed"}."""
    if not isinstance(checked, bool):
        raise EditError(400, t("„checked“ muss true oder false sein."))
    if (item is None) == (text is None):
        raise EditError(400, t("Genau eines angeben: item oder text."))
    if block_id is not None:
        b = find_block(blocks, block_id)[0]
        if not is_checklist(b):
            raise EditError(400, t("Der Block ist keine Checkliste. Kontrollkästchen in Datenbank-Einträgen mit update_row setzen."))
        lists = [b]
    else:
        if item is not None:
            raise EditError(400, t("item braucht block_id der Checkliste."))
        lists = [b for b, _l, _i, chain, _c in walk(blocks) if is_checklist(b) and not any(is_copy(a) for a in chain + (b,))]
    cands = [(b, idx, it, lst, pos) for b in lists for idx, it, lst, pos in _check_items(b)]
    if item is not None:
        hit = [x for x in cands if x[1] == str(item).strip()]
        if not hit:
            raise EditError(404, t("Punkt {i} nicht gefunden.", i=str(item)[:20]),
                            [{"item": x[1], "text": _short(_item_text(x[0], x[2]), ITEM_MAX)} for x in cands][:CAND_MAX])
    else:
        want = norm(text)
        if not want:
            raise EditError(400, t("Text fehlt"))
        hit = [x for x in cands if norm(_item_text(x[0], x[2])) == want]
        if not hit:
            hit = [x for x in cands if want in norm(_item_text(x[0], x[2]))]
        if len(hit) != 1:
            show = hit or cands
            raise EditError(400 if hit else 404, t("Punkt ist mehrdeutig – bitte block_id und item angeben.") if hit
                            else t("Punkt nicht gefunden."),
                            [{"block_id": x[0].get("id"), "item": x[1], "text": _short(_item_text(x[0], x[2]), ITEM_MAX)} for x in show][:CAND_MAX])
    b, idx, it, lst, pos = hit[0]
    was = _item_checked(b, it)
    if b.get("type") == "checklist":
        if not isinstance(it, dict):
            it = lst[pos] = {"text": str(it), "checked": False}
        it["checked"] = checked
    else:
        if not isinstance(it, dict):
            it = lst[pos] = {"content": str(it), "meta": {}, "items": []}
        meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
        meta["checked"] = checked
        it["meta"] = meta
    return {"block_id": b.get("id"), "item": idx, "text": _short(_item_text(b, it), ITEM_MAX), "checked": checked, "changed": was != checked}
