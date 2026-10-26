"""Vorlagen und Datenbanken (Ansichten, Spalten, Zeilen, Berechnungen)."""
import hashlib
import hmac
import json
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from db import connect as _db
from fastapi import APIRouter
from saga import nextcloud
from saga.core import SECRET, _api_guard, _bad, _body, _json_load, origin_of
from saga.sharing import access
from saga.notes import NOTE_TITLE_MAX, note_limit_error, _FILE_URL, _check_parent, _iter_blocks, announce_row, tree_changed


router = APIRouter()


# --- Vorlagen ---------------------------------------------------------------------
def _map_props(c, owner, src_parent, dst_parent, props_json):
    """Eigenschaften einer Vorlage in eine ANDERE Datenbank uebertragen: nach Name + Typ der Spalte,
    Auswahl-Optionen nach Namen. Was es im Ziel nicht gibt, faellt weg (statt falscher IDs)."""
    def schema(nid):
        if not nid:
            return []
        row = c.execute("SELECT schema, kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
        return (_json_load(row[0], {}) or {}).get("props", []) if row and row[1] == "database" else []
    src, dst = schema(src_parent), schema(dst_parent)
    vals = _json_load(props_json, {}) or {}
    if not dst or not isinstance(vals, dict):
        return None
    out = {}
    by_id = {p.get("id"): p for p in src}
    for pid, v in vals.items():
        sp = by_id.get(pid)
        if not sp:
            continue
        dp = next((p for p in dst if p.get("name", "").strip().lower() == sp.get("name", "").strip().lower() and p.get("type") == sp.get("type")), None)
        if not dp or dp.get("type") in ("relation", "created", "edited"):
            continue
        if dp.get("type") in ("select", "multi_select"):
            names = {o.get("id"): o.get("name", "").strip().lower() for o in sp.get("options", [])}
            dst_ids = {o.get("name", "").strip().lower(): o.get("id") for o in dp.get("options", [])}
            ids = v if isinstance(v, list) else [v]
            mapped = [dst_ids[names[i]] for i in ids if i in names and names[i] in dst_ids]
            if not mapped:
                continue
            v = mapped if dp["type"] == "multi_select" else mapped[0]
        out[dp["id"]] = v
    return json.dumps(out, ensure_ascii=False) if out else None


@router.post("/api/notes/{nid}/duplicate")
async def api_note_duplicate(nid: int, request: Request):
    """Kopie einer eigenen Seite (Vorlage -> neue Seite). Unterseiten werden nicht kopiert."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    return await run_in_threadpool(note_duplicate, uid, nid, b, origin_of(request))


def note_duplicate(uid, nid, b, origin=None):
    """Kern von POST /api/notes/{nid}/duplicate - auch fuer Personen anderer Instanzen (fed_pages, Schaltflaeche mit
    Vorlage auf einer foederiert geteilten Seite: uid ist dann der Prinzipal '@name@host')."""
    owner = uid                              # Duplizieren: der Eigentuemer - oder per Schaltflaeche (via, siehe unten)
    now = time.time()
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            if b.get("via") is None:
                return err
            owner, err = _via_button(c, uid, nid, b)
            if err is not None:
                return err
        r = c.execute("SELECT title, icon, tags, body, format, kind, schema, props, cover, cover_pos, wide, parent_id, font, small "
                      "FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        lim = note_limit_error(c, owner, parent)
        if lim:
            return _bad(lim, 409)
        title = str(b.get("title", r[0] or "")).strip()[:NOTE_TITLE_MAX]
        props = r[7]
        if parent != r[11]:                     # andere Datenbank (z. B. Vorlagen-Datenbank) -> Eigenschaften nach Name/Typ zuordnen
            props = _map_props(c, owner, r[11], parent, r[7])
        # Alles kopieren, was die Seite ausmacht: auch Titelbild (+ Position), volle Breite, Schrift und kleiner Text
        tpl = 1 if b.get("template") is True and owner == uid else 0   # 2.5 "Duplizieren" einer Vorlage bleibt Vorlage (nur Eigentuemer, R03)
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, cover, cover_pos, wide, "
                        "font, small, is_template, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, r[3], r[4], r[1], parent, now, r[2], r[5] or "page", r[6], props, r[8], r[9], r[10], r[12], r[13] or 0,
                         tpl, now, now))
        # Nextcloud-Bloecke: Signatur gilt je Seite; die Kopie stammt aus einer Seite des Eigentuemers (auch per via, R05)
        new_body = nextcloud.adopt(r[3], r[4], owner, cur.lastrowid)
        new_body = resign_copy(new_body, r[4], nid, cur.lastrowid, owner)
        if new_body != r[3]:
            c.execute("UPDATE note SET body=? WHERE id=? AND uid=?", (new_body, cur.lastrowid, owner))
        if owner != uid:                        # per Schaltflaeche auf einer geteilten Seite: wer hat angelegt, Live-Hinweis
            c.execute("UPDATE note SET created_by=?, updated_by=? WHERE id=? AND uid=?", (uid, uid, cur.lastrowid, owner))
            if parent:
                announce_row(c, owner, cur.lastrowid, uid, now, origin)
        tree_changed(c, owner, [cur.lastrowid], "created", origin)
        return JSONResponse({"id": cur.lastrowid, "updated": now})


# --- Schaltflaechen mit Vorlage auf geteilten Seiten (2.5) ---------------------------------------------------------
# Bughunt S01/R01: Wer die via-Seite selbst besitzt oder bearbeiten darf, koennte sich eine Schaltflaeche {tpl, target}
# einbauen und so jede Vorlage des Eigentuemers kopieren (und lesen). Darum signiert der SERVER Schaltflaechen mit Vorlage,
# wenn der Eigentuemer der Seite speichert; speichert jemand anderes, bleibt eine Signatur nur fuer genau das Paar
# (Vorlage -> Ziel) erhalten, das der Eigentuemer schon so hatte. Ohne gueltige Signatur gibt es keinen via-Weg.
def _btn_key():
    return hashlib.sha256(("hk-button-tpl|" + SECRET).encode()).digest()


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def btn_sig(nid, owner, tpl, target):
    msg = f"{int(nid)}|{owner}|{int(tpl)}|{int(target)}"
    return hmac.new(_btn_key(), msg.encode(), hashlib.sha256).hexdigest()[:32]


def _tpl_buttons(doc):
    """Schaltflaechen "Neuer Eintrag" mit Vorlage (auch in Spalten/Toggles) -> [(button-dict, tpl, target)]"""
    out = []
    for blk in _iter_blocks((doc or {}).get("blocks", []) if isinstance(doc, dict) else []):
        if blk.get("type") != "buttons":
            continue
        for bt in (blk.get("data") or {}).get("buttons") or []:
            if isinstance(bt, dict) and bt.get("action") == "db":
                tpl, target = _num(bt.get("tpl")), _num(bt.get("target"))
                if tpl and target and tpl > 0 and target > 0:
                    out.append((bt, tpl, target))
    return out


def sign_buttons(body, nid, owner, uid, old_body=None):
    """Beim Speichern einer Seite (Format blocks): Signaturen der Vorlagen-Schaltflaechen setzen/entfernen (s. o.).
    -> neuer Inhalt (oder der alte, wenn sich nichts aendert)."""
    if '"buttons"' not in str(body or "") and '"sig"' not in str(body or ""):
        return body
    try:
        doc = json.loads(body)
        found = _tpl_buttons(doc)
    except (ValueError, AttributeError, RecursionError):
        return body
    allowed, existing = set(), set()
    if uid != owner and old_body and '"buttons"' in str(old_body):
        try:
            for bt, tpl, target in _tpl_buttons(json.loads(old_body)):
                existing.add((tpl, target))
                if hmac.compare_digest(str(bt.get("sig") or ""), btn_sig(nid, owner, tpl, target)):
                    allowed.add((tpl, target))
        except (ValueError, AttributeError, RecursionError):
            pass
    changed = False
    if uid != owner:
        # Andere als der Eigentuemer fuehren keine NEUE Vorlage ein (sonst signierte der Eigentuemer sie beim naechsten
        # Speichern unbemerkt mit): neue Paare verlieren die Vorlage und legen wie ohne Vorlage einen leeren Eintrag an
        keep = []
        for bt, tpl, target in found:
            if (tpl, target) in existing:
                keep.append((bt, tpl, target))
            else:
                bt["tpl"] = None; bt.pop("sig", None); changed = True
        found = keep
    for bt, tpl, target in found:
        want = btn_sig(nid, owner, tpl, target) if (uid == owner or (tpl, target) in allowed) else None
        if want and bt.get("sig") != want:
            bt["sig"] = want; changed = True
        elif not want and "sig" in bt:
            bt.pop("sig"); changed = True
    # Signaturen an Schaltflaechen ohne Vorlage/Ziel (von Hand eingefuegt) nie stehen lassen
    for blk in _iter_blocks(doc.get("blocks", []) if isinstance(doc, dict) else []):
        if blk.get("type") == "buttons":
            for bt in (blk.get("data") or {}).get("buttons") or []:
                if isinstance(bt, dict) and "sig" in bt and not any(bt is f[0] for f in found):
                    bt.pop("sig"); changed = True
    return json.dumps(doc, ensure_ascii=False) if changed else body


def resign_copy(body, fmt, src_id, dst_id, owner):
    """Kopie einer Seite des Eigentuemers (Duplikat/Vorlage): gueltige Signaturen fuer die neue Seite umschreiben."""
    if fmt != "blocks" or '"sig"' not in str(body or ""):
        return body
    try:
        doc = json.loads(body)
        changed = False
        for bt, tpl, target in _tpl_buttons(doc):
            ok = hmac.compare_digest(str(bt.get("sig") or ""), btn_sig(src_id, owner, tpl, target))
            if ok:
                bt["sig"] = btn_sig(dst_id, owner, tpl, target)
            else:
                bt.pop("sig", None)
            changed = True
        return json.dumps(doc, ensure_ascii=False) if changed else body
    except (ValueError, AttributeError, RecursionError):
        return body


def _via_button(c, uid, nid, b):
    """Schaltflaeche "Neuer Eintrag" mit Vorlage auf einer geteilten Seite (2.5): Die Vorlage gehoert dem Eigentuemer
    und liegt oft in einer Datenbank, die nicht geteilt ist. Erlaubt nur, wenn die Seite b["via"] dem Eigentuemer von
    Vorlage und Ziel gehoert, die Person sie sehen und das Ziel bearbeiten darf und die Seite GENAU diese Schaltflaeche
    (Vorlage nid -> Ziel) mit gueltiger Signatur des Eigentuemers enthaelt. -> (owner, None) | (None, Fehler)"""
    nf = _bad("not found", 404)
    via, target = _num(b.get("via")), _num(b.get("parent_id"))
    if not via or not target:
        return None, nf
    owner, _, err = access(c, uid, target, "edit")
    if err is not None:
        return None, err
    vowner, _, err = access(c, uid, via, "view")
    if err is not None or vowner != owner:
        return None, nf
    if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND is_template=1 AND deleted_at IS NULL", (nid, owner)).fetchone():
        return None, nf
    r = c.execute("SELECT body, format FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (via, owner)).fetchone()
    if not r or r[1] != "blocks":
        return None, nf
    try:
        want = btn_sig(via, owner, nid, target)
        for bt, tpl, tg in _tpl_buttons(_json_load(r[0], {})):
            if tpl == nid and tg == target and hmac.compare_digest(str(bt.get("sig") or ""), want):
                return owner, None
    except (ValueError, AttributeError, RecursionError):
        pass
    return None, nf


def _first_image(body, fmt):
    """Erstes Bild im Seiteninhalt (Galerie-Vorschau, wenn die Zeile kein Titelbild hat)."""
    if fmt != "blocks":
        return ""
    doc = _json_load(body, {})
    try:
        blocks = list(_iter_blocks(doc.get("blocks", []) if isinstance(doc, dict) else []))
    except ValueError:
        return ""
    for b in blocks:
        if b.get("type") == "image":
            u = str(((b.get("data") or {}).get("file") or {}).get("url", ""))
            if _FILE_URL.fullmatch(u):
                return u
    return ""
