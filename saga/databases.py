"""Vorlagen und Datenbanken (Ansichten, Spalten, Zeilen, Berechnungen)."""
import hashlib
import hmac
import json
import secrets
import time
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from db import connect as _db
from fastapi import APIRouter
from saga import nextcloud
from saga.core import SECRET, _api_guard, _bad, _body, _json_load, origin_of, t
from saga.sharing import access
from saga.notes import NOTE_TITLE_MAX, note_limit_error, _FILE_URL, _check_parent, _iter_blocks, _purge_ids, announce_row, tree_changed


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
    Vorlage auf einer foederiert geteilten Seite: uid ist dann der Prinzipal '@name@host').
    Eintrags-Vorlage -> Zeile ihrer Datenbank (fix27 E1): auch ab Rolle 'Bearbeiten' an der Datenbank (_row_tpl).
    Aus einer Vorlage in eine Datenbank (fix27 E4): Titel leer (ausser b.title), Antwort mit fresh/placeholder."""
    owner = uid                              # Duplizieren: der Eigentuemer - oder per Schaltflaeche (via, siehe unten)
    now = time.time()
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            if b.get("via") is None:
                owner, err2 = _row_tpl(c, uid, nid, b)
                err = err if err2 is not None and err2.status_code == 404 else err2   # keine Eintrags-Vorlage: Antwort wie bisher
            else:
                owner, err = _via_button(c, uid, nid, b)
            if err is not None:
                return err
        r = c.execute("SELECT title, icon, tags, body, format, kind, schema, props, cover, cover_pos, wide, parent_id, font, small, is_template "
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
        tpl = 1 if b.get("template") is True and owner == uid else 0   # 2.5 "Duplizieren" einer Vorlage bleibt Vorlage (nur Eigentuemer, R03)
        into_db = bool(parent) and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database'", (parent, owner)).fetchone() is not None
        entry = bool(r[14]) and not tpl and into_db      # E4: neuer Eintrag aus einer Eintrags-Vorlage
        title = str(b.get("title", "" if entry else (r[0] or ""))).strip()[:NOTE_TITLE_MAX]
        props = r[7]
        if parent != r[11]:                     # andere Datenbank (z. B. Vorlagen-Datenbank) -> Eigenschaften nach Name/Typ zuordnen
            props = _map_props(c, owner, r[11], parent, r[7])
        # Alles kopieren, was die Seite ausmacht: auch Titelbild (+ Position), volle Breite, Schrift und kleiner Text
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
        out = {"id": cur.lastrowid, "updated": now}
        if entry:
            out.update(fresh=_fresh_mark(c, owner, uid, cur.lastrowid, now), placeholder=r[0] or "")
        return JSONResponse(out)


def _row_tpl(c, uid, nid, b):
    """fix27 E1: Eintrags-Vorlage nid in ihre eigene Datenbank b["parent_id"] kopieren - ab Rolle 'Bearbeiten' an dieser
    Datenbank (die Vorlage selbst bleibt fuer andere unsichtbar). -> (owner, None) | (None, Fehler)"""
    target = _num(b.get("parent_id"))
    if not target or target <= 0:
        return None, _bad("not found", 404)
    owner, _, err = access(c, uid, target, "edit")
    if err is not None:
        return None, err
    if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND parent_id=? AND is_template=1 AND deleted_at IS NULL",
                     (nid, owner, target)).fetchone():
        return None, _bad("not found", 404)
    return owner, None


# --- fix27 E4: neue Eintraege aus einer Vorlage ohne Aenderung wieder verwerfen -----------------------------------------
FRESH_TTL = 900.0           # so lange darf die anlegende Person einen unveraenderten Eintrag endgueltig loeschen


def _norm_blocks(v):
    """Inhalt ohne Block-IDs und Editor-Zeitstempel (die aendern sich beim Speichern ohne inhaltliche Aenderung)."""
    if isinstance(v, list):
        return [_norm_blocks(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm_blocks(x) for k, x in v.items() if k not in ("id", "time", "version")}
    return v


def _fingerprint(c, owner, nid):
    r = c.execute("SELECT title, icon, tags, body, format, props, cover, kind, parent_id FROM note WHERE id=? AND uid=? "
                  "AND deleted_at IS NULL", (nid, owner)).fetchone()
    if not r:
        return None
    body = r[3] or ""
    if r[4] == "blocks":
        body = _norm_blocks((_json_load(body, {}) or {}).get("blocks") or []) if body else []
    props = {k: v for k, v in (_json_load(r[5], {}) or {}).items() if v not in (None, "", [], False)}
    raw = json.dumps([(r[0] or "").strip(), r[1] or "", r[2] or "", body, r[4], props, r[6] or "", r[7], r[8]],
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _fresh_mark(c, owner, creator, nid, now):
    c.execute("DELETE FROM note_fresh WHERE created < ?", (now - FRESH_TTL,))
    tok = secrets.token_urlsafe(18)
    c.execute("INSERT INTO note_fresh (note_id, uid, creator, tok, fp, created) VALUES (?,?,?,?,?,?)",
              (nid, owner, creator, tok, _fingerprint(c, owner, nid), now))
    return tok


def note_discard(uid, nid, b, origin=None):
    """Kern von POST /api/notes/{nid}/discard {"fresh": "<aus duplicate>"} (auch foederiert, uid = Prinzipal): den eben
    aus einer Vorlage angelegten Eintrag endgueltig loeschen - nur die anlegende Person, nur binnen FRESH_TTL und nur,
    solange er unveraendert ist (Titel, Symbol, Eigenschaften, Inhalt, Titelbild; keine Kommentare, Unterseiten,
    Freigaben). Sonst 409 und der Eintrag bleibt."""
    tok = b.get("fresh") if isinstance(b, dict) else None
    if not isinstance(tok, str) or not 8 <= len(tok) <= 64:
        return _bad(t("ungültige Angabe"))
    now = time.time()
    with _db() as c, c:
        f = c.execute("SELECT uid, creator, tok, fp, created FROM note_fresh WHERE note_id=?", (nid,)).fetchone()
        if not f or f[1] != uid or not hmac.compare_digest(f[2], tok):
            return _bad("not found", 404)
        owner = f[0]
        if owner != uid and access(c, uid, nid, "edit")[2] is not None:     # Zugriff inzwischen entzogen
            return _bad("not found", 404)

        def keep(reason):
            return JSONResponse({"error": t("Der Eintrag wurde geändert und bleibt erhalten."), "deleted": False, "reason": reason},
                                status_code=409)
        if now - f[4] > FRESH_TTL:
            return keep("expired")
        if _fingerprint(c, owner, nid) != f[3]:
            return keep("changed")
        busy = c.execute("SELECT (SELECT count(*) FROM note WHERE uid=? AND parent_id=?) + "
                         "(SELECT count(*) FROM note_comment WHERE owner_uid=? AND note_id=?) + "
                         "(SELECT count(*) FROM note_share WHERE owner_uid=? AND note_id=?) + "
                         "(SELECT count(*) FROM note_publish WHERE uid=? AND note_id=?)",
                         (owner, nid, owner, nid, owner, nid, owner, nid)).fetchone()[0]
        if busy:
            return keep("changed")
        announce_row(c, owner, nid, uid, now, origin)      # vor dem Loeschen: die Datenbank laedt neu
        _purge_ids(c, owner, [nid])
    return JSONResponse({"deleted": True})


@router.post("/api/notes/{nid}/discard")
async def api_note_discard(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    return await run_in_threadpool(note_discard, uid, nid, b, origin_of(request))


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


# --- Formel-Editor (2.5, Paket 1): Formel pruefen + Vorschau --------------------------------------------------------
FX_PREVIEW_ROWS = 5


@router.post("/api/notes/{nid}/formula/check")
async def api_formula_check(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    return await run_in_threadpool(formula_check_api, uid, nid, b)


def formula_check_api(uid, nid, b):
    """{expr, pid?, names?} -> {ok, rtype, expr (mit IDs), display (mit Namen), error: {pos, msg, code} | null,
    preview: [{id, title, value, error}]} - ab Rolle 'Bearbeiten'. names=true (Standard): prop("…") nennt Namen wie im
    Editor; der Server uebersetzt in IDs. Vorschau: erste FX_PREVIEW_ROWS Zeilen mit den Rechten von uid.
    Auch fuer die Foederation (uid = fremde Person)."""
    from saga import dbcalc, formula as fx
    expr = b.get("expr")
    if not isinstance(expr, str):
        return _bad(t("Formel fehlt"))
    pid = b.get("pid") if isinstance(b.get("pid"), str) and len(b.get("pid")) <= 24 else "fx_new"
    names = b.get("names") is not False
    with _db() as c:
        owner, role, err = access(c, uid, nid, "edit")
        if err is not None:
            return err
        r = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        schema = _json_load(r[0], {"props": []})
        try:
            cm, ids = fx.check(schema, pid, expr, names=names)
        except fx.FxSyntax as ex:
            return JSONResponse({"ok": False, "rtype": None, "expr": None, "display": None, "preview": [],
                                 "error": {"pos": ex.pos, "msg": ex.msg, "code": ex.code}})
        props = [p for p in schema.get("props", []) if isinstance(p, dict) and p.get("id") != pid]
        me = {"id": pid, "name": "", "type": "formula", "expr": ids}
        fx.annotate(props + [me], schema.get("title_name"))
        sch = dict(schema, props=props + [me])
        shared = role != "owner"
        rows = [{"id": x[0], "title": x[1] or "", "props": _json_load(x[2], {}), "created": x[3], "updated": x[4]}
                for x in c.execute("SELECT id, title, props, created, updated FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL "
                                   "AND is_template=0" + (" AND archived IS NULL" if shared else "")
                                   + " ORDER BY sort NULLS FIRST, created, id LIMIT ?", (owner, nid, FX_PREVIEW_ROWS)).fetchall()]
        dbcalc.fill(c, owner, sch, rows, viewer=uid)
    prev = [{"id": x["id"], "title": x["title"], "value": x["props"].get(pid), "error": (x.get("fx_err") or {}).get(pid)} for x in rows]
    return JSONResponse({"ok": True, "rtype": cm.rtype, "expr": ids, "display": fx.to_names(schema, ids), "error": None, "preview": prev})
