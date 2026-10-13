"""Seiten im Notion-Stil: anlegen, lesen, speichern, verschieben, suchen."""
import io
import json
import os
import re
import threading
import time
from html import escape as html_escape
from html.parser import HTMLParser
from urllib.parse import urlparse
import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from db import connect as _db
from fastapi import APIRouter
from saga.core import E, STATE_DIR, _api_guard, _bad, _body, _json_load, _session_user, t
from saga.dashboard import visible_tiles
from saga.notes import MAX_JSON_DEPTH, NOTE_BODY_MAX, NOTE_MAX_COUNT, NOTE_META_COLS, NOTE_TITLE_MAX, _FILE_URL, _check_file_refs, _check_parent, _clean_props, _clean_schema, _default_schema, _note_meta, _note_text, _purge_old, _rel_check, _rel_link, _rel_sync, _rel_unlink, _snapshot, blocks_text

router = APIRouter()


# --- Notizen als Seiten (Notion-Stil) ----------------------------------------
# Inhalt: Editor.js-Bloecke als JSON (format="blocks") oder alter Klartext (format="text").
# Seiten koennen Unterseiten haben (parent_id). Alles strikt pro Benutzer.
NOTE_BLOCKS_MAX = 200_000          # Zeichen JSON pro Seite
NOTE_ICON_MAX = 8
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "mark", "code", "a", "br", "time"}
_ALLOWED_CLASSES = {"mark": {"cdx-marker"}, "code": {"inline-code"}, "time": {"hk-date", "hk-remind"}}
_TIME_ATTR = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")      # @-Datum im Text: <time datetime="2026-10-10">


_NOTE_HREF = re.compile(r"/notes#[1-9][0-9]{0,8}")


class _Sanitizer(HTMLParser):
    """Positivliste fuer Inline-HTML in Bloecken. Alles andere wird zu Text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            return
        a = dict(attrs)
        keep = ""
        if tag == "a":
            href = (a.get("href") or "").strip()
            if _NOTE_HREF.fullmatch(href):          # interner Seitenverweis, exakt /notes#<zahl>
                keep = f' href="{href}"'
            elif urlparse(href).scheme in ("http", "https", "mailto"):
                keep = f' href="{html_escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
            else:
                return
        if tag == "time":
            dt = (a.get("datetime") or "").strip()
            if not _TIME_ATTR.fullmatch(dt):
                return
            keep = f' datetime="{dt}"'
        cls = [c for c in (a.get("class") or "").split() if c in _ALLOWED_CLASSES.get(tag, set())]
        if cls:
            keep += f' class="{" ".join(cls)}"'
        if tag == "br":
            self.out.append("<br>")
            return
        self.out.append(f"<{tag}{keep}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(html_escape(data, quote=False))

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_inline(s):
    p = _Sanitizer()
    p.feed(s)
    p.close()
    return p.result()


def _sanitize_value(v, key=None):
    if isinstance(v, str):
        return v if key == "code" else sanitize_inline(v)     # Code-Bloecke sind Klartext (Textarea)
    if isinstance(v, list):
        return [_sanitize_value(x, key) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_value(x, k) for k, x in v.items()}
    return v


def _json_depth_ok(obj, limit=MAX_JSON_DEPTH):
    """Iterativ (ohne Rekursion) pruefen, dass JSON nicht absurd tief verschachtelt ist."""
    stack = [(obj, 1)]
    while stack:
        o, d = stack.pop()
        if d > limit:
            return False
        if isinstance(o, dict):
            stack.extend((v, d + 1) for v in o.values())
        elif isinstance(o, list):
            stack.extend((v, d + 1) for v in o)
    return True


class TooLarge(ValueError):
    """Seite zu gross -> HTTP 413."""


def clean_blocks(raw):
    """JSON-String pruefen und bereinigen -> (json_string, plaintext). ValueError bei Unsinn."""
    if len(raw) > NOTE_BLOCKS_MAX:
        raise TooLarge(t("Seite zu gross"))
    d = json.loads(raw)
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError(t("kein Block-Dokument"))
    if not _json_depth_ok(d):
        raise ValueError(t("zu tief verschachtelt"))
    blocks = []
    for b in d["blocks"]:
        if not isinstance(b, dict) or not isinstance(b.get("type"), str) or not isinstance(b.get("data"), dict):
            raise ValueError(t("ungueltiger Block"))
        blocks.append({k: (_sanitize_value(v) if k == "data" else v) for k, v in b.items() if k in ("id", "type", "data")})
    blocks = _flatten_nested_columns(blocks)
    doc = {"time": d.get("time"), "version": d.get("version"), "blocks": blocks}
    return json.dumps(doc, ensure_ascii=False), blocks_text(blocks)


def _flatten_nested_columns(blocks, nested=False):
    """Spalten in Spalten/Toggles kann der Editor nicht darstellen -> innere Spalten-Bloecke aufloesen:
    ihr Inhalt (Spalte 1, dann 2, ...) rueckt an ihre Stelle. Nichts geht verloren."""
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "columns":
            cols = [c for c in (d.get("cols") or []) if isinstance(c, dict)]
            for c in cols:
                c["blocks"] = _flatten_nested_columns(c.get("blocks") if isinstance(c.get("blocks"), list) else [], True)
            if nested:
                for c in cols:
                    out.extend(c["blocks"])
                continue
        elif b.get("type") == "toggle" and isinstance(d.get("blocks"), list):
            d["blocks"] = _flatten_nested_columns(d["blocks"], True)
        out.append(b)
    return out


TAG_MAX_LEN, TAG_MAX_COUNT = 30, 20
_TAG_RE = re.compile(r"[\w\- .äöüÄÖÜß]+")


def _tags(v):
    """Liste von Tags -> bereinigt, ohne Dubletten (Gross/Klein egal), max. 20 a 30 Zeichen."""
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError(t("Tags muessen eine Liste sein"))
    out, seen = [], set()
    for x in v:
        s = re.sub(r"\s+", " ", str(x)).strip().lstrip("#")[:TAG_MAX_LEN]
        if not s:
            continue
        if not _TAG_RE.fullmatch(s):
            raise ValueError(t("unerlaubte Zeichen im Tag"))
        if s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    if len(out) > TAG_MAX_COUNT:
        raise ValueError(t("maximal {n} Tags", n=TAG_MAX_COUNT))
    return out


def _own_image(c, uid, url):
    """True, wenn url = /files/<id> ein eigenes, am Inhalt als Bild erkanntes Upload ist."""
    m = _FILE_URL.fullmatch(str(url or ""))
    r = m and c.execute("SELECT is_image FROM file WHERE id=? AND uid=?", (m.group(1), uid)).fetchone()
    return bool(r and r[0])


def _icon(v, c=None, uid=None):
    """Emoji (max. 8 Zeichen) oder eigenes hochgeladenes Bild (/files/<id>)."""
    s = str(v or "").strip()
    if s.startswith("/files/"):
        if c is None or not _own_image(c, uid, s):
            raise ValueError(t("Icon-Bild muss ein eigenes hochgeladenes Bild sein"))
        return s
    s = s[:NOTE_ICON_MAX]
    if any(ch in s for ch in "<>&\"'"):
        raise ValueError
    return s


@router.get("/api/notes")
def api_notes(request: Request):
    """Seitenbaum (ohne Inhalte)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _purge_old(c, uid)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, archived, locked FROM note WHERE uid=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                         (uid,)).fetchall()
    return JSONResponse([dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10])) for r in rows], headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}")
def api_note_get(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        r = c.execute(f"SELECT {NOTE_META_COLS}, body, format, schema, props, cover, cover_pos, created, wide, rev, archived, locked FROM note WHERE id=? AND uid=? AND deleted_at IS NULL",
                      (nid, owner)).fetchone()
    if not r:
        return _bad("not found", 404)
    d = _note_meta(r)
    d.update(body=r[9], format=r[10] or "text", props=_json_load(r[12], {}), cover=r[13] or "",
             cover_pos=50.0 if r[14] is None else r[14], created=r[15], wide=bool(r[16]), rev=r[17], archived=bool(r[18]), locked=bool(r[19]))
    if d["kind"] == "database":
        d["schema"] = _json_load(r[11], {"props": []})
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


@router.post("/api/notes")
async def api_note_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request) or {}
    now = time.time()
    with _db() as c, c:
        if c.execute("SELECT COUNT(*) FROM note WHERE uid=?", (owner,)).fetchone()[0] >= NOTE_MAX_COUNT:
            return _bad(t("maximal {n} Seiten (inkl. Papierkorb)", n=NOTE_MAX_COUNT), 409)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
            icon = _icon(b.get("icon"), c, owner)
            tags = _tags(b.get("tags"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError):
            return _bad(t("ungueltige Angabe"))
        title = str(b.get("title", "")).strip()[:NOTE_TITLE_MAX]
        kind = "database" if b.get("kind") == "database" else "page"
        schema = json.dumps(_default_schema(), ensure_ascii=False) if kind == "database" else None
        props = None
        if parent and b.get("props"):
            ps = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (parent, owner)).fetchone()
            if ps:
                try:
                    props = json.dumps(_clean_props(_json_load(ps[0], {"props": []}), b["props"], check=_rel_check(c, owner)), ensure_ascii=False)
                except (ValueError, TypeError) as ex:
                    return _bad(str(ex) or t("ungueltige Werte"))
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, json.dumps({"blocks": []}), "blocks", icon, parent, now,
                         json.dumps(tags, ensure_ascii=False), kind, schema, props, now, now))
        if props and ps:                                   # zweiseitige Relationen: Gegenseite nachziehen
            _rel_sync(c, owner, _json_load(ps[0], {"props": []}), cur.lastrowid, {}, json.loads(props))
        return JSONResponse({"id": cur.lastrowid, "updated": now})


@router.patch("/api/notes/{nid}")
async def api_note_update(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    sets, args = [], []
    with _db() as c, c:
        old = c.execute("SELECT title, body, format, rev, locked FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not old:
            return _bad("not found", 404)
        if old[4] and b.get("locked") is not False and ("body" in b or "title" in b):     # gesperrt: Inhalt/Titel bleiben unveraendert
            return JSONResponse({"error": t("Seite ist gesperrt – erst entsperren")}, status_code=423)
        # Konflikterkennung: wer Titel/Inhalt speichert und die gekannte Revision mitschickt, ueberschreibt
        # keine zwischenzeitliche Aenderung (anderer Tab, Peek, spaeter: andere Person). Ohne 'rev' wie bisher.
        content = "body" in b or "title" in b
        if content and b.get("rev") is not None:
            try:
                want = int(b["rev"])
            except (TypeError, ValueError):
                return _bad(t("ungueltige Revision"))
            if want != old[3]:
                return JSONResponse({"error": "conflict", "rev": old[3]}, status_code=409)
        try:
            if "pinned" in b:
                sets.append("pinned=?"); args.append(1 if b["pinned"] else 0)
            if "template" in b:
                sets.append("is_template=?"); args.append(1 if b["template"] else 0)
            if "wide" in b:
                sets.append("wide=?"); args.append(1 if b["wide"] else 0)
            if "locked" in b:
                sets.append("locked=?"); args.append(1 if b["locked"] else 0)
            if "schema" in b:
                me = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
                if me[0] != "database":
                    raise ValueError(t("nur Datenbanken haben ein Schema"))
                new_schema = _clean_schema(b["schema"])
                old_s = _json_load((c.execute("SELECT schema FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone() or [None])[0], {"props": []})
                if old_s.get("locked") and new_schema.get("locked"):     # gesperrt: nur Entsperren ist erlaubt
                    strip = lambda s: json.dumps([s.get("props"), s.get("views"), s.get("title_name")], sort_keys=True)
                    if strip(new_schema) != strip(_clean_schema(old_s)):
                        raise ValueError(t("Die Datenbank ist gesperrt – zum Ändern erst unter „Ansicht“ entsperren"))
                old_by = {p["id"]: p for p in old_s.get("props", [])}
                old_rel = {k: p.get("target") for k, p in old_by.items() if p.get("type") == "relation"}
                for rp in new_schema["props"]:          # Relation nur auf eigene, aktive Datenbanken
                    if rp["type"] == "relation" and old_rel.get(rp["id"]) != rp["target"] and not c.execute(
                            "SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL", (rp["target"], owner)).fetchone():
                        raise ValueError(t("Ziel-Datenbank der Relation nicht gefunden"))
                # Zweiseitige Relationen: Kopplung (pair) stellt NUR der Server her - vom Browser nur uebernehmen, was schon so bestand
                raw_by = {str(p.get("id")): p for p in b["schema"].get("props", []) if isinstance(p, dict)}
                my_title = c.execute("SELECT title FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                for rp in new_schema["props"]:
                    if rp["type"] != "relation":
                        continue
                    prev = old_by.get(rp["id"]) or {}
                    if prev.get("pair") and prev.get("target") == rp["target"]:
                        rp["pair"] = prev["pair"]                # bestehende Kopplung bleibt, egal was der Browser schickt
                    else:
                        rp.pop("pair", None)
                    if prev.get("pair") and prev.get("target") != rp["target"]:      # Ziel gewechselt: alte Gegenspalte entfernen
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], True)
                    want = raw_by.get(rp["id"], {}).get("two_way")
                    if want is True and not rp.get("pair") and rp["target"] != nid:
                        rp["pair"] = _rel_link(c, owner, nid, my_title, rp)
                    elif want is False and rp.get("pair"):
                        _rel_unlink(c, owner, nid, rp["target"], rp.pop("pair"), True)
                new_ids = {p["id"] for p in new_schema["props"]}
                for k, prev in old_by.items():                                    # Spalte geloescht: Gegenseite bleibt, nur entkoppelt
                    if k not in new_ids and prev.get("type") == "relation" and prev.get("pair"):
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], False)
                sets.append("schema=?"); args.append(json.dumps(new_schema, ensure_ascii=False))
                # Werte, die nach Typ-/Optionsaenderung nicht mehr passen, aus allen Zeilen entfernen
                # (Mehrfachauswahl: nur geloeschte Optionen herausfiltern, nicht die ganze Liste verwerfen)
                by_id = {p["id"]: p for p in new_schema["props"]}
                for rid, rp in c.execute("SELECT id, props FROM note WHERE uid=? AND parent_id=?", (owner, nid)).fetchall():
                    old_p, keep = _json_load(rp, {}), {}
                    for k, v in old_p.items():
                        pr = by_id.get(k)
                        if pr and pr["type"] == "multi_select" and isinstance(v, list):
                            v = [x for x in v if x in {o["id"] for o in pr.get("options", [])}]
                        try:
                            keep.update(_clean_props(new_schema, {k: v}, check=_rel_check(c, owner)))   # Relation: nur IDs aus dem (neuen) Ziel
                        except (ValueError, TypeError):
                            pass
                    if keep != old_p:
                        c.execute("UPDATE note SET props=? WHERE id=? AND uid=?", (json.dumps(keep, ensure_ascii=False), rid, owner))
            if "props" in b:
                ps = c.execute("SELECT d.schema, n.props FROM note n JOIN note d ON d.id=n.parent_id AND d.uid=n.uid "
                               "WHERE n.id=? AND n.uid=? AND d.kind='database'", (nid, owner)).fetchone()
                if not ps:
                    raise ValueError(t("Seite ist keine Datenbank-Zeile"))
                merged = _clean_props(_json_load(ps[0], {"props": []}), b["props"], _json_load(ps[1], {}), check=_rel_check(c, owner))
                _rel_sync(c, owner, _json_load(ps[0], {"props": []}), nid, _json_load(ps[1], {}), merged)
                sets.append("props=?"); args.append(json.dumps(merged, ensure_ascii=False))
            if "title" in b:
                sets.append("title=?"); args.append(str(b["title"]).strip()[:NOTE_TITLE_MAX])
            if "icon" in b:
                sets.append("icon=?"); args.append(_icon(b["icon"], c, owner))
            if "cover" in b:
                cv = b["cover"]
                if cv in (None, ""):
                    sets.append("cover=?"); args.append(None)
                elif _own_image(c, owner, cv):
                    sets.append("cover=?"); args.append(cv)
                else:
                    raise ValueError(t("Titelbild muss ein eigenes hochgeladenes Bild sein"))
            if "cover_pos" in b:
                sets.append("cover_pos=?"); args.append(max(0.0, min(100.0, float(b["cover_pos"]))))
            if "tags" in b:
                sets.append("tags=?"); args.append(json.dumps(_tags(b["tags"]), ensure_ascii=False))
            if "body" in b:
                fmt = b.get("format", "blocks")
                if fmt == "blocks":
                    body, _ = clean_blocks(str(b["body"]))
                    _check_file_refs(owner, json.loads(body)["blocks"])
                elif fmt == "text":
                    body = str(b["body"])
                    if len(body) > NOTE_BODY_MAX:
                        return _bad(t("Notiz zu lang (max. {n} Zeichen)", n=NOTE_BODY_MAX), 413)
                else:
                    raise ValueError(t("unbekanntes Format"))
                sets += ["body=?", "format=?"]; args += [body, fmt]
            if "parent_id" in b:
                newpar = _check_parent(c, owner, nid, b["parent_id"])
                if newpar is not None:          # wie beim Verschieben: keine normale Seite in eine Datenbank
                    cur_par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                    if c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (newpar, owner)).fetchone()[0] == "database" and cur_par != newpar:
                        raise ValueError(t("Seiten koennen nicht in eine Datenbank verschoben werden"))
                sets.append("parent_id=?"); args.append(newpar)
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(str(ex) if str(ex) else t("ungueltige Angabe"), 413 if isinstance(ex, TooLarge) else 400)
        if not sets:
            return _bad(t("nichts zu aendern"))
        now = time.time()
        if "body" in b:
            _snapshot(c, owner, nid, old, now)
        if content:
            sets.append("rev=rev+1")
        c.execute(f"UPDATE note SET {', '.join(sets)}, updated=? WHERE id=? AND uid=?", (*args, now, nid, owner))
    return JSONResponse({"ok": True, "updated": now, "rev": old[3] + (1 if content else 0)})


@router.post("/api/notes/{nid}/move")
async def api_note_move(nid: int, request: Request):
    """Seite im Baum verschieben: neue Eltern (oder oberste Ebene) und Position unter den Geschwistern.
    Die Geschwister werden danach sauber durchnummeriert (sort = 1..n)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    b = await _body(request) or {}
    try:
        index = max(0, int(b.get("index", 0)))
    except (TypeError, ValueError):
        return _bad(t("ungueltige Position"))
    with _db() as c, c:
        me = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        try:
            parent = _check_parent(c, owner, nid, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(str(ex) or t("ungueltige Angabe"))
        if parent is not None:
            pk = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (parent, owner)).fetchone()[0]
            if pk == "database" and me[0] != parent:
                return _bad(t("Seiten koennen nicht in eine Datenbank verschoben werden"))
        sib = [r[0] for r in c.execute(
            "SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id<>? AND "
            + ("parent_id=?" if parent is not None else "parent_id IS NULL") + " ORDER BY sort NULLS FIRST, created",
            (owner, nid, parent) if parent is not None else (owner, nid))]
        sib.insert(min(index, len(sib)), nid)
        c.execute("UPDATE note SET parent_id=? WHERE id=? AND uid=?", (parent, nid, owner))
        c.executemany("UPDATE note SET sort=? WHERE id=? AND uid=?", [(i + 1, x, owner) for i, x in enumerate(sib)])
    return JSONResponse({"ok": True, "parent_id": parent, "index": sib.index(nid)})


@router.post("/api/notes/{nid}/archive")
async def api_note_archive(nid: int, request: Request):
    """Archivieren (archived=true) samt aktiver Unterseiten - gemeinsamer Zeitstempel, damit Zurueckholen genau
    diese Seiten zurueckbringt. Archivierte Seiten fehlen in Baum und Suche, bleiben aber per Link erreichbar."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid
    b = await _body(request) or {}
    on = bool(b.get("archived", True))
    with _db() as c, c:
        me = c.execute("SELECT archived, parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if on:
            if me[0] is not None:
                return JSONResponse({"ok": True, "archived": 0})
            now = time.time()
            n = sum(c.execute("UPDATE note SET archived=? WHERE id=? AND uid=? AND archived IS NULL", (now, i, owner)).rowcount for i in ids)
            return JSONResponse({"ok": True, "archived": n})
        if me[0] is None:
            return JSONResponse({"ok": True, "restored": 0})
        n = sum(c.execute("UPDATE note SET archived=NULL WHERE id=? AND uid=? AND archived=?", (i, owner, me[0])).rowcount for i in ids)
        if me[1]:             # Eltern noch archiviert (und keine Datenbank): Seite nach oben holen, sonst waere sie unsichtbar
            par = c.execute("SELECT archived, kind FROM note WHERE id=? AND uid=?", (me[1], owner)).fetchone()
            if par and par[0] is not None and par[1] != "database":
                c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, owner))
        return JSONResponse({"ok": True, "restored": n})


@router.delete("/api/notes/{nid}")
def api_note_delete(nid: int, request: Request):
    """Verschiebt die Seite samt aktiver Unterseiten in den Papierkorb (gemeinsamer Zeitstempel)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c, c:
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if not ids:
            return _bad("not found", 404)
        now = time.time()
        c.executemany("UPDATE note SET deleted_at=? WHERE id=? AND uid=?", [(now, i, owner) for i in ids])
    return JSONResponse({"deleted": len(ids), "trash": True})


@router.post("/api/notes/search")
async def api_note_search(request: Request):
    """Volltextsuche in Titel, Tags und Inhalt (POST: Suchbegriff landet nicht im Log).
    'tag' (exakt, Gross/Klein egal) filtert zusaetzlich nach einem Tag."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    q = str(b.get("q", "")).strip().lower()[:100]
    tag = str(b.get("tag", "")).strip().lower()[:TAG_MAX_LEN]
    if not q and not tag:
        return JSONResponse([])
    hits = []
    with _db() as c:
        for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND archived IS NULL ORDER BY updated DESC", (uid,)):
            d = _note_meta(r)
            tags_l = [x.lower() for x in d["tags"]]
            if tag and tag not in tags_l:
                continue
            if q:
                text = _note_text(r[10], r[9])
                i = text.lower().find(q)
                if not (q in (r[1] or "").lower() or i >= 0 or any(q in x for x in tags_l)):
                    continue
                if i >= 0:
                    s = max(0, i - 40)
                    d["snippet"] = ("…" if s else "") + text[s:i + len(q) + 60] + ("…" if i + len(q) + 60 < len(text) else "")
            hits.append(d)
            if len(hits) >= 50:
                break
    return JSONResponse(hits, headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}/backlinks")
def api_note_backlinks(nid: int, request: Request):
    """Eigene Seiten, die auf diese Seite verweisen (Rueckverweise)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid      # Schritt A: Eigentuemer = ich. Schritt B: zentrale Zugriffspruefung (Freigaben)
    with _db() as c:
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        # Im gespeicherten JSON steht der Link als href=\"/notes#<id>\" - der Backslash vor dem
        # Anfuehrungszeichen grenzt die Zahl ab (#1 trifft nicht #12).
        rows = c.execute(f"SELECT {NOTE_META_COLS} FROM note WHERE uid=? AND id<>? AND format='blocks' AND deleted_at IS NULL "
                         "AND instr(body, ?) > 0 ORDER BY updated DESC",
                         (owner, nid, f'/notes#{nid}\\"')).fetchall()
    return JSONResponse([_note_meta(r) for r in rows], headers={"Cache-Control": "no-store"})


@router.get("/api/services")
def api_services(request: Request):
    """Dienste, die der Benutzer sehen darf, und seine eigenen Lesezeichen (own=true) - fuer Apps und Verweise in Seiten."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    out = [{"name": s["name"], "url": s["url"], "host": s["host"], "category": s["category"], "icon": s.get("icon") or "",
            "own": bool(s.get("own")), "key": s["key"]} for s in visible_tiles(uid).values()]
    out.sort(key=lambda s: (s["category"].lower(), s["name"].lower()))
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


# Dienst-Symbole (selfh.st, wie die Kacheln im Web) fuer die Apps: einmal vom CDN holen, als PNG ablegen.
# Die Apps brauchen so kein Internet und kein SVG; ein fehlendes Symbol wird einen Tag lang nicht erneut gesucht.
SVC_ICON_DIR = os.path.join(STATE_DIR, "svc-icons")
SVC_ICON_CDN = (E.get("ICON_CDN", "https://cdn.jsdelivr.net/gh/selfhst/icons")).rstrip("/")   # leer = keine Symbole aus dem Internet
_svc_icon_lock = threading.Lock()


def _svc_icon_fetch(name):
    if not SVC_ICON_CDN:
        return None
    with httpx.Client(timeout=10, follow_redirects=False) as cl:
        r = cl.get(f"{SVC_ICON_CDN}/png/{name}.png")
        if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and len(r.content) < 2_000_000:
            return r.content
        r = cl.get(f"{SVC_ICON_CDN}/webp/{name}.webp")
        if r.status_code == 200 and r.content[8:12] == b"WEBP" and len(r.content) < 2_000_000:
            from PIL import Image          # fehlte bisher (NameError beim WebP-Ersatz); nur hier gebraucht
            im = Image.open(io.BytesIO(r.content))
            im.thumbnail((256, 256))
            out = io.BytesIO()
            im.save(out, "PNG")
            return out.getvalue()
    return None


def svc_icon_path(name):
    """-> (pfad, None) oder (None, http-status). Einmal vom CDN holen und als PNG ablegen."""
    os.makedirs(SVC_ICON_DIR, exist_ok=True)
    path, miss = os.path.join(SVC_ICON_DIR, name + ".png"), os.path.join(SVC_ICON_DIR, name + ".missing")
    with _svc_icon_lock:
        if not os.path.isfile(path):
            if os.path.isfile(miss) and time.time() - os.path.getmtime(miss) < 86400:
                return None, 404
            try:
                data = _svc_icon_fetch(name)
            except (httpx.HTTPError, OSError, ValueError):
                return None, 502                        # nicht merken: CDN kurz weg
            if not data:
                open(miss, "w").close()
                return None, 404
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
    return path, None


@router.get("/api/services/icon/{name}")
def api_service_icon(name: str, request: Request):
    if not _session_user(request) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return PlainTextResponse("not found", status_code=404)
    path, code = svc_icon_path(name)
    if not path:
        return PlainTextResponse("not found", status_code=code)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=604800"})
