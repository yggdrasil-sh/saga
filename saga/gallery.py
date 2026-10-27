"""Vorlagengalerie (2.5): mitgelieferte Bundles aus Seiten und Datenbanken als Kopie im eigenen Konto anlegen.

Format und Pruefung: saga/gallery_format.py, docs/GALERIE.md. API-Vertrag: docs/APP-API.md ("Vorlagengalerie").

- Beim Start werden die Bundles aus SAGA_GALLERY_DIR (Standard gallery/ neben app.py) geladen und geprueft; ungueltige
  stehen als Warnung im Log und werden nicht angeboten.
- GET /api/gallery?lang=, GET /api/gallery/{id}?lang= (Vorschau), POST /api/gallery/{id}/use.
- Anlegen in EINER Transaktion mit den Bausteinen von Seiten/Datenbanken (_clean_schema, _clean_props, _rel_link,
  _rel_sync, clean_blocks, sign_buttons) - schlaegt etwas fehl, wird nichts angelegt.
- Admin-Schalter "Vorlagengalerie anbieten" (Einstellung gallery_enabled, Standard an). Aus: alle Endpunkte 404 (wie ein
  aelterer Server - Apps blenden den Eintrag dann aus).
- Foederation: nur auf dem eigenen Server; unter einer foederiert geteilten Elternseite (negative ID) in Etappe 1 nicht."""
import json
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
from db import connect as _db
from saga import gallery_format as gf
from saga.core import E, HERE, _api_guard, _bad, _body, origin_of, t
from saga.databases import sign_buttons
from saga.i18n import get_lang, norm
from saga.notes import (_check_file_refs, _clean_props, _clean_schema, _db_schema, _rel_check, _rel_link, _rel_sync,
                        _schema_stamp, note_limit_error, tree_changed)
from saga.pages import FONTS, _Reply, _icon, _title_in, clean_blocks
from saga.sharing import Access, access

router = APIRouter()

GALLERY_DIR = E.get("SAGA_GALLERY_DIR") or os.path.join(HERE, "gallery")
RATE = max(1, int(E.get("GALLERY_RATE", "10")))      # "Verwenden" je Konto und Minute
# nur Tests: nach dem Anlegen dieses refs abbrechen (Alles-oder-nichts) - wirkt nur mit eigenem SAGA_GALLERY_DIR (Test-Bundles)
_TEST_FAIL = E.get("SAGA_GALLERY_TEST_FAIL", "") if E.get("SAGA_GALLERY_DIR") else ""

BUNDLES, TABLES = {}, {}
_hits = {}


def load(path=None):
    """Bundles (neu) laden. -> Probleme {datei: [fehler]} (auch im Log)."""
    global BUNDLES, TABLES
    b, tb, problems = gf.load_dir(path or GALLERY_DIR)
    for fn, errs in sorted(problems.items()):
        print(f"galerie: {fn} ungueltig, nicht angeboten: {'; '.join(errs[:5])}", flush=True)
    BUNDLES, TABLES = b, tb
    return problems


def enabled():
    return bool(auth.settings().get("gallery_enabled", True))


def _lang(request):
    raw = request.query_params.get("lang")
    return norm(raw) if raw and norm(raw) else get_lang()


def _bundle(gid, lang):
    b = BUNDLES.get(gid)
    if not b:
        return None
    return gf.translate(b, TABLES.get(lang)) if lang != "de" else b


def _summary(b):
    return {"id": b["id"], "title": b["title"], "desc": b.get("desc") or "", "icon": b["icon"], "category": b["category"],
            "order": b.get("order", 0), "needs": list(b.get("needs") or []),
            "contents": [{"kind": it["kind"], "title": it.get("title") or "", "icon": it.get("icon") or "",
                          "example": it.get("example") is True} for it in b["items"]]}


def _gone():
    return _bad("not found", 404)


@router.get("/api/gallery")
def api_gallery(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not enabled():
        return _gone()
    lang = _lang(request)
    items = [_summary(_bundle(gid, lang)) for gid in BUNDLES]
    items.sort(key=lambda x: (x["order"], x["id"]))
    return JSONResponse({"items": items, "lang": lang}, headers={"Cache-Control": "no-store"})


def _preview_blocks(blocks, b):
    """Vorschau ohne Anlegen: Verweise auf Seiten -> Titel ohne Ziel (Link zeigt auf nichts), Datenbank-Bloecke und Widgets
    bekommen statt der ID den Namen der Datenbank (preview_db)."""
    names = {}
    for it in b["items"]:
        names[it["ref"]] = it.get("title") or ""
        for sub in ("templates", "rows"):
            for e in it.get(sub) or []:
                if e.get("ref"):
                    names[e["ref"]] = e.get("title") or ""

    def fix(v):
        if isinstance(v, str):
            m = gf.REF_FULL.fullmatch(v)
            if m:
                return {"preview_ref": m.group(1), "title": names.get(m.group(1), "")}
            return gf.REF_ANY.sub("0", v) if "$ref:" in v else v
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    return fix(blocks or [])


@router.get("/api/gallery/{gid}")
def api_gallery_one(gid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not enabled():
        return _gone()
    lang = _lang(request)
    b = _bundle(gid, lang)
    if not b:
        return _gone()
    out = _summary(b)
    root = next(it for it in b["items"] if it["ref"] == b["root"])
    out["preview"] = {"title": root.get("title") or "", "icon": root.get("icon") or "", "kind": root["kind"],
                      "wide": bool(root.get("wide")), "blocks": _preview_blocks(root.get("body"), b)}
    out["databases"] = [{"title": it.get("title") or "", "icon": it.get("icon") or "",
                         "props": [{"id": p["id"], "name": p.get("name") or "", "type": p["type"]} for p in it["schema"]["props"]],
                         "views": [{"id": v["id"], "name": v.get("name") or "", "type": v["type"]} for v in it["schema"]["views"]],
                         "rows": len([r for r in it.get("rows") or [] if r.get("example") is not True]),
                         "examples": len([r for r in it.get("rows") or [] if r.get("example") is True]) if not it.get("example") else len(it.get("rows") or []),
                         "templates": [tp.get("title") or "" for tp in it.get("templates") or []]}
                        for it in b["items"] if it["kind"] == "database"]
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


def _rate_ok(uid):
    now = time.time()
    h = [x for x in _hits.get(uid, []) if now - x < 60]
    if len(h) >= RATE:
        _hits[uid] = h
        return False
    _hits[uid] = h + [now]
    if len(_hits) > 20000:
        _hits.clear()
    return True


@router.post("/api/gallery/{gid}/use")
async def api_gallery_use(gid: str, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if not enabled():
        return _gone()
    if not _rate_ok(uid):
        return _bad(t("Zu viele Vorlagen in kurzer Zeit – bitte gleich noch einmal versuchen."), 429)
    b = await _body(request)
    if b is None:
        b = {}
    lang = _lang(request)
    try:
        return await run_in_threadpool(use, uid, gid, b, lang, origin_of(request))
    except _Reply as r:
        return r.resp


def _bad_reply(msg, code=400):
    return _Reply(_bad(msg, code))


def use(uid, gid, b, lang, origin=None):
    """Bundle gid fuer uid anlegen (b: parent_id, examples) -> {"root", "created"} bzw. Fehlerantwort. Alles in einer
    Transaktion: jeder Fehler wirft _Reply, 'with c:' rollt zurueck."""
    if gid not in BUNDLES:
        return _gone()
    pid_raw = b.get("parent_id")
    if pid_raw is not None:
        if isinstance(pid_raw, bool):
            return _bad(t("ungültige Angabe"))
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError, OverflowError):
            return _bad(t("ungültige Angabe"))
        if pid < 0:
            return _bad(t("Vorlagen lassen sich (noch) nicht unter Seiten anderer Saga-Server anlegen."))
    else:
        pid = None
    examples = b.get("examples", True) is not False
    bundle = _bundle(gid, lang)
    keep = _keep(bundle, examples)
    now = time.time()
    with _db() as c, c:
        acc = Access(c, uid)
        owner = uid
        if pid is not None:
            owner, _, err = access(c, uid, pid, "edit", acc)
            if err is not None:                          # Fix27 #16: Lesen/Kommentieren -> verstaendliche Meldung (404 bleibt 404)
                return _bad(t("Unter dieser Seite kannst du keine Vorlage anlegen – du darfst sie nur lesen oder kommentieren."), 403) \
                    if getattr(err, "status_code", 0) == 403 else err
            pk = c.execute("SELECT kind FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (pid, owner)).fetchone()
            if not pk:
                return _gone()
            if pk[0] == "database":
                return _bad(t("Vorlagen lassen sich nicht in eine Datenbank einfügen."))
        ids, order = {}, []
        try:
            _insert_all(c, uid, owner, bundle, keep, examples, pid, now, ids, order)
            _fill(c, uid, owner, bundle, keep, examples, ids)
        except _Reply:
            raise
        except (ValueError, TypeError, LookupError, OverflowError, AttributeError, KeyError) as ex:
            print(f"galerie: {gid} fuer {uid} abgebrochen: {type(ex).__name__}: {ex}", flush=True)
            raise _bad_reply(t("Die Vorlage konnte nicht angelegt werden: {err}", err=str(ex)[:200]))
        tree_changed(c, owner, order, "created", origin)
        if pid is not None and owner != uid:          # unter einer geteilten Seite: wer sie offen hat, sieht die neuen Seiten
            from saga.notes import announce_shared
            announce_shared(c, owner, ids[bundle["root"]], order)
    return JSONResponse({"root": ids[bundle["root"]], "created": order})


def _keep(bundle, examples):
    """refs, die angelegt werden (ohne Beispiele: ohne example-Seiten/-Zeilen)."""
    keep = set()
    for it in bundle["items"]:
        ex = it.get("example") is True
        if ex and not examples:
            continue
        keep.add(it["ref"])
        for tp in it.get("templates") or []:
            keep.add(tp["ref"])
    return keep


def _rows(it, examples_kept):
    """Zeilen einer Datenbank, die angelegt werden: [(index, zeile)]"""
    ex_db = it.get("example") is True
    return [(i, r) for i, r in enumerate(it.get("rows") or []) if examples_kept or not (ex_db or r.get("example") is True)]


def _insert(c, uid, owner, title, icon, parent, sort, kind="page", tpl=0, wide=False, font=None, small=False):
    lim = note_limit_error(c, owner, parent)
    if lim:
        raise _bad_reply(lim, 409)
    schema = json.dumps({"props": [], "views": [{"id": "tabelle", "name": t("Tabelle"), "type": "table"}]}) if kind == "database" else None
    cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated, "
                    "updated_by, created_by, is_template, wide, font, small) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (owner, _title_in(title), json.dumps({"blocks": []}), "blocks", _icon(icon), parent, sort, "[]", kind,
                     schema, None, sort, sort, uid, uid, tpl, 1 if wide else 0, font if font in FONTS else None, 1 if small else 0))
    return cur.lastrowid


def _insert_all(c, uid, owner, bundle, keep, examples, pid, now, ids, order):
    """Phase 1: alle Seiten, Datenbanken, Vorlagen und Zeilen leer anlegen (IDs vergeben)."""
    n = 0
    for it in bundle["items"]:
        if it["ref"] not in keep:
            continue
        parent = ids[it["parent"]] if it.get("parent") else pid
        n += 1
        nid = _insert(c, uid, owner, it.get("title"), it.get("icon"), parent, now + n * 1e-3, it["kind"],
                      wide=it.get("wide") is True, font=it.get("font"), small=it.get("small") is True)
        ids[it["ref"]] = nid
        order.append(nid)
        if _TEST_FAIL and _TEST_FAIL == it["ref"]:
            raise ValueError("Testabbruch")
        if it["kind"] != "database":
            continue
        for tp in it.get("templates") or []:
            n += 1
            ids[tp["ref"]] = _insert(c, uid, owner, tp.get("title"), tp.get("icon"), nid, now + n * 1e-3, tpl=1)
            order.append(ids[tp["ref"]])
        for i, r in _rows(it, examples):
            n += 1
            rid = _insert(c, uid, owner, r.get("title"), r.get("icon"), nid, now + n * 1e-3)
            ids[r.get("ref") or f"#{it['ref']}#{i}"] = rid
            order.append(rid)
    return ids


def _body_json(c, owner, nid, blocks, ids, examples=True):
    raw = json.dumps({"blocks": gf.resolve(gf.drop_examples(blocks or [], examples), ids)}, ensure_ascii=False)
    body, _ = clean_blocks(raw)
    _check_file_refs(owner, json.loads(body)["blocks"])
    # Schaltflaechen mit Vorlage: Vorlage und Ziel sind eben in diesem Vorgang angelegt worden (Bundle erlaubt nur refs) -
    # darum signiert der Server sie fuer den Eigentuemer, auch wenn eine Person mit "Bearbeiten" unter seiner Seite anlegt
    if '"buttons"' in body:
        body = sign_buttons(body, nid, owner, owner)
    return body


def _fill(c, uid, owner, bundle, keep, examples, ids):
    """Phase 2: refs ersetzen und Inhalte schreiben - Schemas, Gegenspalten, Werte der Zeilen, Inhalte."""
    dbs = [it for it in bundle["items"] if it["kind"] == "database" and it["ref"] in keep]
    # 1. Schemas (ohne Gegenspalten)
    two_way = {}
    for it in dbs:
        nid = ids[it["ref"]]
        raw = gf.resolve(it["schema"], ids)
        dflt = next((tp["ref"] for tp in it.get("templates") or [] if tp.get("default") is True), None)
        if raw.get("default_tpl") is None and dflt:
            raw["default_tpl"] = ids[dflt]
        sc = _clean_schema(raw)
        for p in raw.get("props") or []:
            if p.get("type") == "relation" and p.get("target") is None:
                raise ValueError(t("Ziel-Datenbank der Relation nicht gefunden"))
        sc["props"] = [p for p in sc["props"] if p["type"] != "relation" or p["target"] in ids.values()]
        two_way[nid] = [p["id"] for p in raw.get("props") or [] if p.get("type") == "relation" and p.get("two_way") is True]
        _schema_stamp({"props": []}, sc, uid)
        c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(sc, ensure_ascii=False), nid, owner))
    # 2. zweiseitige Relationen: Gegenspalte in der Ziel-Datenbank (liest/schreibt deren Schema in der Datenbank)
    for it in dbs:
        nid = ids[it["ref"]]
        if not two_way.get(nid):
            continue
        sc = _db_schema(c, owner, nid)
        old = json.loads(json.dumps(sc))
        for p in sc["props"]:
            if p["id"] in two_way[nid] and p["type"] == "relation" and not p.get("pair") and p["target"] != nid:
                p["pair"] = _rel_link(c, owner, nid, _title_in(it.get("title")), p, actor=uid)
        _schema_stamp(old, sc, uid)
        c.execute("UPDATE note SET schema=? WHERE id=? AND uid=?", (json.dumps(sc, ensure_ascii=False), nid, owner))
    # 3. Werte von Vorlagen und Zeilen (Relationen: nur angelegte Zeilen; Gegenseite nachziehen)
    check = _rel_check(c, owner)
    for it in dbs:
        nid = ids[it["ref"]]
        sc = _db_schema(c, owner, nid)
        entries = [(tp["ref"], tp) for tp in it.get("templates") or []] + \
                  [(r.get("ref") or f"#{it['ref']}#{i}", r) for i, r in _rows(it, examples)]
        for ref, e in entries:
            rid = ids[ref]
            if e.get("props"):
                raw = gf.resolve(e["props"], ids)
                raw = {k: ([x for x in v if x is not None] if isinstance(v, list) else v) for k, v in raw.items()}
                cur = json.loads(c.execute("SELECT props FROM note WHERE id=? AND uid=?", (rid, owner)).fetchone()[0] or "{}")
                props = _clean_props(sc, raw, cur, check=check)
                _rel_sync(c, owner, sc, rid, cur, props)
                c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(props, ensure_ascii=False), rid, owner))
            if e.get("body"):
                c.execute("UPDATE note SET body=? WHERE id=? AND uid=?", (_body_json(c, owner, rid, e["body"], ids, examples), rid, owner))
    # 4. Inhalte der Seiten und Datenbanken
    for it in bundle["items"]:
        if it["ref"] in keep and it.get("body"):
            nid = ids[it["ref"]]
            c.execute("UPDATE note SET body=? WHERE id=? AND uid=?", (_body_json(c, owner, nid, it["body"], ids, examples), nid, owner))
