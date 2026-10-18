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
from db import clean_line, connect as _db
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from saga import bus
from saga.core import BASE_URL, E, STATE_DIR, account_lang, _api_guard, _bad, _body, _json_load, _session_user, origin_of, t, user_error
from saga.dashboard import visible_tiles
from saga.publish import published_via
from saga.comments import counts as comment_counts
from saga.sharing import RANK, Access, access, mask_links, names, person, restore_links, shared_ids, title_of, unread_notes, viewer
from saga.notes import MAX_JSON_DEPTH, NOTE_BODY_MAX, note_limit_error, NOTE_META_COLS, NOTE_TITLE_MAX, _FILE_URL, _check_file_refs, _check_parent, _clean_props, _clean_schema, _default_schema, _note_meta, _note_text, _purge_old, _rel_check, _rel_link, _rel_sync, _rel_unlink, _schema_merge, announce_row, announce_tree, _schema_srev, _schema_stamp, _snapshot, blocks_text, tree_changed

router = APIRouter()


# --- Notizen als Seiten (Notion-Stil) ----------------------------------------
# Inhalt: Editor.js-Bloecke als JSON (format="blocks") oder alter Klartext (format="text").
# Seiten koennen Unterseiten haben (parent_id). Alles strikt pro Benutzer.
NOTE_BLOCKS_MAX = 200_000          # Zeichen JSON pro Seite
NOTE_ICON_MAX = 8
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "mark", "code", "a", "br", "time"}
_ALLOWED_CLASSES = {"mark": {"cdx-marker"}, "code": {"inline-code"}, "time": {"hk-date", "hk-remind"}}
_TIME_ATTR = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")      # @-Datum im Text: <time datetime="2026-10-10">


_NOTE_HREF = re.compile(r"/notes#[1-9][0-9]{0,17}")      # wie sharing._NOTE_LINK / publish._NOTE_HREF (BUG-090)
# Echte HTML-Elemente ausserhalb der Positivliste werden entfernt; alles andere in spitzen Klammern ist Klartext
_HTML_TAGS = set("html head body title meta link script style noscript template iframe frame frameset object embed applet param "
                 "img picture source video audio track canvas svg math map area form input button select option optgroup "
                 "textarea label fieldset legend output datalist div span p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd table thead "
                 "tbody tfoot tr td th caption col colgroup blockquote pre q cite abbr address article aside footer header "
                 "main nav section figure figcaption details summary dialog menu hr sub sup small big tt kbd samp var del "
                 "ins font center marquee blink base basefont bdi bdo data dfn rp rt ruby wbr xmp plaintext listing image "
                 "slot portal search hgroup acronym strike nobr noembed noframes".split())


class _Sanitizer(HTMLParser):
    """Positivliste fuer Inline-HTML in Bloecken. Alles andere wird zu Text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in _ALLOWED_TAGS:
            if tag not in _HTML_TAGS:           # "<bob@x.de>", "<3 Herz>": Klartext, kein HTML -> als Text behalten (BUG-039)
                self.out.append(html_escape(self.get_starttag_text() or "", quote=False))
            return
        a = dict(attrs)
        keep = ""
        if tag == "a":
            if "a" in self.stack:              # keine Verweise in Verweisen (wie der Browser): aeusseren erst schliessen -
                self.handle_endtag("a")        # sonst verdeckte sharing.mask_links den Text des inneren Verweises nicht
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
    if p.rawdata.startswith("<") and ">" not in p.rawdata:     # "a<b c Ende": unvollstaendiger Tag am Ende ist Text (BUG-039)
        p.handle_data(p.rawdata)
        p.rawdata = ""
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


def clean_blocks(raw, limit=NOTE_BLOCKS_MAX):
    """JSON-String pruefen und bereinigen -> (json_string, plaintext). ValueError bei Unsinn."""
    if len(raw) > limit:
        raise TooLarge(t("Seite zu groß"))
    d = json.loads(raw)
    if not isinstance(d, dict) or not isinstance(d.get("blocks"), list):
        raise ValueError(t("kein Block-Dokument"))
    if not _json_depth_ok(d):
        raise ValueError(t("zu tief verschachtelt"))
    blocks = []
    for b in d["blocks"]:
        if not isinstance(b, dict) or not isinstance(b.get("type"), str) or not isinstance(b.get("data"), dict):
            raise ValueError(t("ungültiger Block"))
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
        raise ValueError(t("Tags müssen eine Liste sein"))
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


def _may_edit(acc, role):
    """Fuer _rel_sync: der Eigentuemer darf alle Zielzeilen nachziehen, andere nur die, die sie bearbeiten duerfen."""
    if role == "owner":
        return None
    return lambda rid: RANK.get(acc.role(rid), 0) >= RANK["edit"]


def _rel_check_for(c, owner, acc, role, old_props=None):
    """Relationen: nur Eintraege, die es in der Ziel-Datenbank des Eigentuemers gibt - fuer Nicht-Eigentuemer zusaetzlich
    nur solche, die sie selbst sehen duerfen (keine IDs aus nicht geteilten Datenbanken). Schon verknuepfte Eintraege
    bleiben erhalten, auch wenn die bearbeitende Person sie nicht sieht."""
    base = _rel_check(c, owner)
    if role == "owner":
        return base
    keep = {x for v in (old_props or {}).values() if isinstance(v, list) for x in v if isinstance(x, int)}
    return lambda target, ids: {i for i in base(target, ids) if i in keep or acc.role(i)}


@router.get("/api/notes")
def api_notes(request: Request):
    """Seitenbaum (ohne Inhalte)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        _purge_old(c, uid, every=3600)             # Papierkorb-Frist: hoechstens stuendlich je Konto (nicht bei jedem Baum)
        rows = c.execute(f"SELECT {NOTE_META_COLS}, archived, locked FROM note WHERE uid=? AND deleted_at IS NULL ORDER BY sort NULLS FIRST, created",
                         (uid,)).fetchall()
        sh = shared_ids(c, uid)                    # Symbol im Baum: Seite ist (selbst) freigegeben
        un = unread_notes(c, uid, [uid])           # Punkt im Baum: neue Kommentare von anderen
    return JSONResponse([dict(_note_meta(r), archived=bool(r[9]), locked=bool(r[10]), shared=r[0] in sh, unread=r[0] in un) for r in rows],
                        headers={"Cache-Control": "no-store"})


@router.get("/api/notes/{nid}")
def api_note_get(nid: int, request: Request):
    """Seite lesen (ab Rolle 'view'). Fuer alle: role, owner{name, username, me}, shared, Kommentar-Zaehler."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return note_get(uid, nid)


def note_get(uid, nid):
    """Fuer die Weboberflaeche und die Foederation (uid = fremde Person '@name@host')."""
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        r = c.execute(f"SELECT {NOTE_META_COLS}, body, format, schema, props, cover, cover_pos, created, wide, rev, archived, locked, updated_by, created_by "
                      "FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not r:
            return _bad("not found", 404)
        mine = role == "owner"
        pub = mine and c.execute("SELECT p.slug, n.name FROM note_publish p LEFT JOIN public_name n ON n.uid=p.uid "
                                 "WHERE p.note_id=? AND p.uid=? AND p.active=1", (nid, owner)).fetchone()
        via = mine and published_via(c, owner, nid)      # ueber eine Elternseite mit "Unterseiten mit veroeffentlichen"
        shared = bool(c.execute("SELECT 1 FROM note_share WHERE note_id=? LIMIT 1", (nid,)).fetchone()) if mine else True
        per, n_open, n_unread = comment_counts(c, uid, nid)
        n_res = c.execute("SELECT COUNT(*) FROM note_comment WHERE note_id=? AND thread_id IS NULL AND resolved_at IS NOT NULL",
                          (nid,)).fetchone()[0]
        nm = names(c, [owner, r[20], r[21]])
        if not mine:                               # Verweise auf Seiten ohne Zugriff: Titel nie ausliefern
            body = mask_links(r[9], r[10], viewer(acc))
            parent_hidden = r[3] is not None and not acc.role(r[3])
    d = _note_meta(r)
    d["published"] = bool(pub)                     # Seitenkopf: Hinweis "Oeffentlich" (Einzelheiten: /api/notes/{nid}/publish)
    d["public_url"] = f"{BASE_URL}/@{pub[1]}/{pub[0]}" if pub and pub[1] else None
    d["published_via"] = via or None
    d.update(body=r[9], format=r[10] or "text", props=_json_load(r[12], {}), cover=r[13] or "",
             cover_pos=50.0 if r[14] is None else r[14], created=r[15], wide=bool(r[16]), rev=r[17], archived=bool(r[18]), locked=bool(r[19]))
    if d["kind"] == "database":
        d["schema"] = _json_load(r[11], {"props": []})
    # Teilen (1.3) - nur Erweiterungen, bestehende Felder bleiben wie sie sind
    d.update(role=role, owner=person(c, owner, uid, nm), shared=shared, comment_counts=per, comments_open=n_open,
             comments_unread=n_unread, comments_resolved=n_res, updated_by=person(c, r[20], uid, nm) if r[20] else None,
             created_by=person(c, r[21], uid, nm) if r[21] and r[21] != owner else None)      # angelegt von jemand anderem
    if not mine:
        d["pinned"] = False                        # Favoriten sind die des Eigentuemers
        d["body"] = body
        if parent_hidden:
            d["parent_id"] = None                  # Elternseite nicht freigegeben: nicht einmal ihre ID
    return JSONResponse(d, headers={"Cache-Control": "no-store"})


@router.post("/api/notes")
async def api_note_add(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner, role = uid, "owner"
    b = await _body(request) or {}
    now = time.time()
    with _db() as c, c:
        acc = Access(c, uid)
        if b.get("parent_id") is not None:
            # Unterseite: braucht 'edit' auf der Elternseite und gehoert deren Eigentuemer (zaehlt bei ihm)
            try:
                pid = int(b["parent_id"])
            except (TypeError, ValueError, OverflowError):
                return _bad(t("ungültige Angabe"))
            owner, role, err = access(c, uid, pid, "edit", acc)
            if err is not None:
                return err
        lim = note_limit_error(c, owner, pid if b.get("parent_id") is not None else None)
        if lim:
            return _bad(lim, 409)
        try:
            parent = _check_parent(c, owner, None, b.get("parent_id"))
            icon = _icon(b.get("icon"), c, owner)
            tags = _tags(b.get("tags"))
            title = _title_in(b.get("title"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError):
            return _bad(t("ungültige Angabe"))
        kind = "database" if b.get("kind") == "database" else "page"
        schema = json.dumps(_default_schema(account_lang(owner)), ensure_ascii=False) if kind == "database" else None
        props = None
        if parent and b.get("props"):
            ps = c.execute("SELECT schema FROM note WHERE id=? AND uid=? AND kind='database'", (parent, owner)).fetchone()
            if ps:
                try:
                    props = json.dumps(_clean_props(_json_load(ps[0], {"props": []}), b["props"], check=_rel_check_for(c, owner, acc, role)),
                                       ensure_ascii=False)
                except (ValueError, TypeError) as ex:
                    return _bad(user_error(ex, t("ungültige Werte")))
        cur = c.execute("INSERT INTO note (uid, title, body, format, icon, parent_id, sort, tags, kind, schema, props, created, updated, updated_by, created_by) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (owner, title, json.dumps({"blocks": []}), "blocks", icon, parent, now,
                         json.dumps(tags, ensure_ascii=False), kind, schema, props, now, now, uid, uid))
        if props and ps:                                   # zweiseitige Relationen: Gegenseite nachziehen
            _rel_sync(c, owner, _json_load(ps[0], {"props": []}), cur.lastrowid, {}, json.loads(props), _may_edit(acc, role))
        if parent:
            announce_row(c, owner, cur.lastrowid, uid, now, origin_of(request))      # neue Zeile (BUG-049)
        tree_changed(c, owner, [cur.lastrowid], "created", origin_of(request))
        return JSONResponse({"id": cur.lastrowid, "updated": now})


def _title_in(v):
    """Seitentitel aus einer Anfrage: null -> leer, Liste/Objekt/Wahrheitswert -> Fehler statt "None"/"['x']" (BUG-083);
    Steuer-, Bidi- und unsichtbare Zeichen raus (BUG-038)."""
    if v is None:
        return ""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)):
        raise ValueError(t("ungültige Angabe"))
    return clean_line(v).strip()[:NOTE_TITLE_MAX]


class _Reply(Exception):
    """Fehlerantwort mitten in einer Transaktion: als Ausnahme geworfen, damit 'with c:' zurueckrollt
    (ein 'return' wuerde die bis dahin gemachten Aenderungen committen - Teil-Commit trotz 4xx)."""

    def __init__(self, resp):
        super().__init__()
        self.resp = resp


SHRINK_MIN, SHRINK_RATIO = 400, 0.5     # Sicherheitsnetz: Version erzwingen, wenn eine Seite (> 400 Zeichen) leer/viel kleiner wird


def _shrinks(old_body, body, fmt):
    if len(old_body or "") <= SHRINK_MIN:
        return False
    if fmt == "blocks":
        empty = not _json_load(body, {}).get("blocks")
    else:
        empty = not body.strip()
    return empty or len(body) < len(old_body) * SHRINK_RATIO


@router.patch("/api/notes/{nid}")
async def api_note_update(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request)
    if b is None:
        return _bad("bad request")
    origin = origin_of(request)
    try:                                     # Datenbank-Arbeit und Bereinigung im Threadpool (Event-Loop bleibt frei)
        return await run_in_threadpool(_note_update, uid, nid, b, origin)
    except _Reply as r:
        return r.resp


OWNER_FIELDS = ("pinned", "template", "locked", "parent_id")      # Favorit, Vorlage, Sperre, Verschieben: nur der Eigentuemer


def _conflict(c, uid, nid, rev):
    """409 mit Revision und - wenn es jemand anderes war - wer zuletzt geaendert hat ("X hat diese Seite gerade geaendert")."""
    r = c.execute("SELECT updated_by FROM note WHERE id=?", (nid,)).fetchone()
    by = r[0] if r and r[0] and r[0] != uid else None
    return JSONResponse({"error": "conflict", "rev": rev, "by": names(c, [by]).get(by) if by else None}, status_code=409)


def _note_update(uid, nid, b, origin=None):
    sets, args = [], []
    with _db() as c, c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "edit", acc)
        if err is not None:
            return err
        if role != "owner" and any(k in b for k in OWNER_FIELDS):
            return _bad(t("Das darf nur der Eigentümer der Seite."), 403)
        old = c.execute("SELECT title, body, format, rev, locked, updated_by FROM note WHERE id=? AND uid=? AND deleted_at IS NULL",
                        (nid, owner)).fetchone()
        if not old:
            return _bad("not found", 404)
        if old[4] and b.get("locked") is not False and ("body" in b or "title" in b):     # gesperrt: Inhalt/Titel bleiben unveraendert
            return JSONResponse({"error": t("Seite ist gesperrt – erst entsperren")}, status_code=423)
        # Konflikterkennung: wer Titel/Inhalt speichert und die gekannte Revision mitschickt, ueberschreibt
        # keine zwischenzeitliche Aenderung (anderer Tab, Peek, andere Person). Ohne 'rev' wie bisher.
        content = "body" in b or "title" in b
        check_rev = content and b.get("rev") is not None
        if check_rev:
            try:
                want = int(b["rev"])
            except (TypeError, ValueError, OverflowError):
                return _bad(t("ungültige Revision"))
            if want != old[3]:
                return _conflict(c, uid, nid, old[3])
        # "Meine Version behalten" (overwrite, ohne rev): trotzdem nur gegen die eben gelesene und als Version gesicherte
        # Fassung schreiben - eine Aenderung, die genau dazwischen eintrifft, wuerde sonst ungesichert ueberschrieben
        # Speichern ohne rev (aeltere Apps, Zeilen umbenennen) ueber den Stand einer anderen Person: vorher sichern
        foreign = content and not check_rev and bool(old[5]) and old[5] != uid
        atomic = check_rev or (content and bool(b.get("overwrite"))) or foreign
        shrink, srev = False, None
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
                # BUG-001: Spalten/Optionen, die der Client (Stand srev) nicht kennen konnte, nicht loeschen
                _schema_merge(old_s, new_schema, _schema_srev(b["schema"]), uid)
                if role != "owner" and bool(old_s.get("locked")) != bool(new_schema.get("locked")):
                    raise _Reply(_bad(t("Das darf nur der Eigentümer der Seite."), 403))     # Sperre der Datenbank: wie die Seiten-Sperre
                if old_s.get("locked") and new_schema.get("locked"):     # gesperrt: nur Entsperren ist erlaubt
                    strip = lambda s: json.dumps([s.get("props"), s.get("views"), s.get("title_name")], sort_keys=True)
                    if strip(new_schema) != strip(_clean_schema(old_s)):
                        raise ValueError(t("Die Datenbank ist gesperrt – zum Ändern erst unter „Ansicht“ entsperren"))
                old_by = {p["id"]: p for p in old_s.get("props", [])}
                old_rel = {k: p.get("target") for k, p in old_by.items() if p.get("type") == "relation"}

                def rel_ok(target):
                    """Relation anlegen/koppeln/entkoppeln schreibt in die Ziel-Datenbank: Nicht-Eigentuemer brauchen dort 'edit'."""
                    if role != "owner" and RANK.get(acc.role(target), 0) < RANK["edit"]:
                        raise _Reply(_bad(t("Diese Relation kann nur der Eigentümer ändern."), 403))
                for rp in new_schema["props"]:          # Relation nur auf eigene, aktive Datenbanken
                    if rp["type"] == "relation" and old_rel.get(rp["id"]) != rp["target"]:
                        rel_ok(rp["target"])
                        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND kind='database' AND deleted_at IS NULL",
                                         (rp["target"], owner)).fetchone():
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
                        rel_ok(prev["target"])
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], True)
                    want = raw_by.get(rp["id"], {}).get("two_way")
                    if want is True and not rp.get("pair") and rp["target"] != nid:
                        rel_ok(rp["target"])
                        rp["pair"] = _rel_link(c, owner, nid, my_title, rp, actor=uid)
                    elif want is False and rp.get("pair"):
                        rel_ok(rp["target"])
                        _rel_unlink(c, owner, nid, rp["target"], rp.pop("pair"), True)
                new_ids = {p["id"] for p in new_schema["props"]}
                for k, prev in old_by.items():                                    # Spalte geloescht: Gegenseite bleibt, nur entkoppelt
                    if k not in new_ids and prev.get("type") == "relation" and prev.get("pair"):
                        rel_ok(prev["target"])
                        _rel_unlink(c, owner, nid, prev["target"], prev["pair"], False)
                # Gegenspalten (_rel_link/_rel_unlink) koennen dieses Schema nicht veraendert haben (nur Ziel-Datenbanken)
                _schema_stamp(old_s, new_schema, uid)
                srev = new_schema["srev"]
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
                merged = _clean_props(_json_load(ps[0], {"props": []}), b["props"], _json_load(ps[1], {}), check=_rel_check_for(c, owner, acc, role, _json_load(ps[1], {})))
                _rel_sync(c, owner, _json_load(ps[0], {"props": []}), nid, _json_load(ps[1], {}), merged, _may_edit(acc, role))
                sets.append("props=?"); args.append(json.dumps(merged, ensure_ascii=False))
            if "title" in b:
                sets.append("title=?"); args.append(_title_in(b["title"]))
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
                    raw = str(b["body"])
                    if role != "owner":
                        # verdeckte Verweistexte (Seiten ohne Zugriff) wieder einsetzen; die Laenge zaehlt erst danach
                        # (der Platzhalter kann laenger sein als der Titel - Spielraum je Verweis)
                        body, _ = clean_blocks(raw, NOTE_BLOCKS_MAX + 64 * raw.count("/notes#"))
                        body = restore_links(old[1] if old[2] == "blocks" else "", body, viewer(acc), title_of(c, owner))
                        if len(body) > NOTE_BLOCKS_MAX:
                            raise TooLarge(t("Seite zu groß"))
                    else:
                        body, _ = clean_blocks(raw)
                    _check_file_refs(owner, json.loads(body)["blocks"])
                elif fmt == "text":
                    body = str(b["body"])
                    if len(body) > NOTE_BODY_MAX:
                        raise _Reply(_bad(t("Notiz zu lang (max. {n} Zeichen)", n=NOTE_BODY_MAX), 413))
                else:
                    raise ValueError(t("unbekanntes Format"))
                sets += ["body=?", "format=?"]; args += [body, fmt]
                shrink = _shrinks(old[1], body, fmt)
            if "parent_id" in b:
                newpar = _check_parent(c, owner, nid, b["parent_id"])
                if newpar is not None:          # wie beim Verschieben: keine normale Seite in eine Datenbank
                    cur_par = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()[0]
                    if c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (newpar, owner)).fetchone()[0] == "database" and cur_par != newpar:
                        raise ValueError(t("Seiten können nicht in eine Datenbank verschoben werden"))
                sets.append("parent_id=?"); args.append(newpar)
        except LookupError:
            raise _Reply(_bad("not found", 404))
        except (ValueError, TypeError, OverflowError, AttributeError) as ex:
            raise _Reply(_bad(user_error(ex, t("ungültige Angabe")), 413 if isinstance(ex, TooLarge) else 400))
        if not sets:
            raise _Reply(_bad(t("nichts zu ändern")))
        now = time.time()
        if "body" in b or (content and (b.get("overwrite") or foreign)):
            # leer/viel kleiner: alten Stand immer sichern. "Meine Version behalten" nach einem Konflikt (overwrite) oder
            # Speichern ohne rev ueber fremden Stand: die ueberschriebene Fassung bleibt als Version erhalten - mit Autor
            # (erzwungene Sicherungen von Nicht-Eigentuemern gedrosselt, s. _snapshot)
            _snapshot(c, owner, nid, old, now, force=shrink or bool(b.get("overwrite")) or foreign, author=old[5], by=uid)
        if content:
            sets.append("rev=rev+1")
            sets.append("updated_by=?"); args.append(uid)            # fuer den Konflikt-Hinweis bei anderen
        # Atomar: mit Revision nur schreiben, wenn sie seit dem Lesen niemand erhoeht hat (sonst 409, alles zurueck)
        n = c.execute(f"UPDATE note SET {', '.join(sets)}, updated=? WHERE id=? AND uid=?" + (" AND rev=?" if atomic else ""),
                      (*args, now, nid, owner, *((old[3],) if atomic else ()))).rowcount
        if atomic and n == 0:
            cur = c.execute("SELECT rev FROM note WHERE id=? AND uid=?", (nid, owner)).fetchone()
            raise _Reply(_conflict(c, uid, nid, cur[0] if cur else None))
        # Live-Kanal (1.4): wer die Seite gerade offen hat, laedt still neu bzw. bekommt einen Hinweis (nach dem Commit)
        bus.fire("note.saved", c, note_id=nid, owner=owner, rev=old[3] + (1 if content else 0), by=uid, updated=now, origin=origin)
        if "props" in b or "title" in b or "icon" in b:
            announce_row(c, owner, nid, uid, now, origin)     # Datenbank-Zeile: auch an Betrachter der Tabelle (BUG-049)
        # Seitenbaum (notes.tree): nur Felder, die /api/notes zeigt (bzw. Titelbild) - Titel nur, wenn er sich aendert
        if ("parent_id" in b or any(k in b for k in ("icon", "cover", "tags", "pinned", "template", "locked"))
                or ("title" in b and _title_in(b["title"]) != old[0])):
            tree_changed(c, owner, [nid], "moved" if "parent_id" in b else "updated", origin)
    out = {"ok": True, "updated": now, "rev": old[3] + (1 if content else 0)}
    if srev is not None:
        out["srev"] = srev                   # neuer Schema-Stand (Client kann ihn fuer die naechste Aenderung uebernehmen)
    return JSONResponse(out)


@router.post("/api/notes/{nid}/move")
async def api_note_move(nid: int, request: Request):
    """Seite im Baum verschieben: neue Eltern (oder oberste Ebene) und Position unter den Geschwistern.
    Die Geschwister werden danach sauber durchnummeriert (sort = 1..n)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid                              # Verschieben: v1 nur der Eigentuemer
    b = await _body(request) or {}
    try:
        index = max(0, int(b.get("index", 0)))
    except (TypeError, ValueError):
        return _bad(t("ungültige Position"))
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
        me = c.execute("SELECT parent_id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone()
        if not me:
            return _bad("not found", 404)
        try:
            parent = _check_parent(c, owner, nid, b.get("parent_id"))
        except LookupError:
            return _bad("not found", 404)
        except (ValueError, TypeError) as ex:
            return _bad(user_error(ex, t("ungültige Angabe")))
        if parent is not None:
            pk = c.execute("SELECT kind FROM note WHERE id=? AND uid=?", (parent, owner)).fetchone()[0]
            if pk == "database" and me[0] != parent:
                return _bad(t("Seiten können nicht in eine Datenbank verschoben werden"))
        sib = [r[0] for r in c.execute(
            "SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id<>? AND "
            + ("parent_id=?" if parent is not None else "parent_id IS NULL") + " ORDER BY sort NULLS FIRST, created",
            (owner, nid, parent) if parent is not None else (owner, nid))]
        sib.insert(min(index, len(sib)), nid)
        c.execute("UPDATE note SET parent_id=? WHERE id=? AND uid=?", (parent, nid, owner))
        c.executemany("UPDATE note SET sort=? WHERE id=? AND uid=?", [(i + 1, x, owner) for i, x in enumerate(sib)])
        if me[0] != parent:                  # andere Elternseite: geerbte Freigaben aendern sich (BUG-033)
            announce_tree(c, owner, nid, [nid], uid, time.time(), False, origin=origin_of(request))
        tree_changed(c, owner, [nid], "moved", origin_of(request))       # auch nur umsortiert
    return JSONResponse({"ok": True, "parent_id": parent, "index": sib.index(nid)})


@router.post("/api/notes/{nid}/archive")
async def api_note_archive(nid: int, request: Request):
    """Archivieren (archived=true) samt aktiver Unterseiten - gemeinsamer Zeitstempel, damit Zurueckholen genau
    diese Seiten zurueckbringt. Archivierte Seiten fehlen in Baum und Suche, bleiben aber per Link erreichbar."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid                              # Archivieren: nur der Eigentuemer
    b = await _body(request) or {}
    on = bool(b.get("archived", True))
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
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
            announce_tree(c, owner, nid, ids, uid, now, True, origin=origin_of(request))
            tree_changed(c, owner, ids, "archived", origin_of(request))
            return JSONResponse({"ok": True, "archived": n})
        if me[0] is None:
            return JSONResponse({"ok": True, "restored": 0})
        n = sum(c.execute("UPDATE note SET archived=NULL WHERE id=? AND uid=? AND archived=?", (i, owner, me[0])).rowcount for i in ids)
        if me[1]:             # Eltern noch archiviert (und keine Datenbank): Seite nach oben holen, sonst waere sie unsichtbar
            par = c.execute("SELECT archived, kind FROM note WHERE id=? AND uid=?", (me[1], owner)).fetchone()
            if par and par[0] is not None and par[1] != "database":
                c.execute("UPDATE note SET parent_id=NULL WHERE id=? AND uid=?", (nid, owner))
        announce_tree(c, owner, nid, ids, uid, time.time(), False, origin=origin_of(request))
        tree_changed(c, owner, ids, "restored", origin_of(request))
        return JSONResponse({"ok": True, "restored": n})


@router.delete("/api/notes/{nid}")
def api_note_delete(nid: int, request: Request):
    """Verschiebt die Seite samt aktiver Unterseiten in den Papierkorb (gemeinsamer Zeitstempel)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    owner = uid                              # Loeschen: nur der Eigentuemer
    with _db() as c, c:
        _, _, err = access(c, uid, nid, "owner")
        if err is not None:
            return err
        ids = [r[0] for r in c.execute(
            "WITH RECURSIVE t(id) AS (SELECT id FROM note WHERE id=? AND uid=? AND deleted_at IS NULL "
            "UNION SELECT n.id FROM note n JOIN t ON n.parent_id=t.id WHERE n.uid=? AND n.deleted_at IS NULL) SELECT id FROM t",
            (nid, owner, owner))]
        if not ids:
            return _bad("not found", 404)
        now = time.time()
        c.executemany("UPDATE note SET deleted_at=? WHERE id=? AND uid=?", [(now, i, owner) for i in ids])
        announce_tree(c, owner, nid, ids, uid, now, True, origin=origin_of(request))
        tree_changed(c, owner, ids, "trashed", origin_of(request))
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
    hits = await run_in_threadpool(_note_search, uid, q, tag)      # laedt und parst alle Seiten: nicht im Event-Loop
    return JSONResponse(hits, headers={"Cache-Control": "no-store"})


def _search_hit(r, q, tag):
    """r = (NOTE_META_COLS..., body, format) -> Treffer oder None"""
    d = _note_meta(r)
    tags_l = [x.lower() for x in d["tags"]]
    if tag and tag not in tags_l:
        return None
    if q:
        text = _note_text(r[10], r[9])
        i = text.lower().find(q)
        if not (q in (r[1] or "").lower() or i >= 0 or any(q in x for x in tags_l)):
            return None
        if i >= 0:
            s = max(0, i - 40)
            d["snippet"] = ("…" if s else "") + text[s:i + len(q) + 60] + ("…" if i + len(q) + 60 < len(text) else "")
    return d


def _note_search(uid, q, tag):
    """Eigene Seiten, danach fuer mich freigegebene (Mengen-Abfrage je Eigentuemer, mit role/owner) - hoechstens 50."""
    hits = []
    with _db() as c:
        for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND archived IS NULL ORDER BY updated DESC", (uid,)):
            d = _search_hit(r, q, tag)
            if d:
                hits.append(d)
                if len(hits) >= 50:
                    return hits
        acc = Access(c, uid)
        vis = viewer(acc)
        owners = acc.owners()
        nm = names(c, owners)
        for o in owners:
            roles = acc.tree(o)
            ids = list(roles)
            for k in range(0, len(ids), 500):
                part = ids[k:k + 500]
                for r in c.execute(f"SELECT {NOTE_META_COLS}, body, format FROM note WHERE uid=? AND deleted_at IS NULL AND id IN "
                                   f"({','.join('?' * len(part))}) ORDER BY updated DESC", (o, *part)):
                    r = tuple(r[:9]) + (mask_links(r[9], r[10], vis), r[10])      # Suche nur im sichtbaren Text
                    d = _search_hit(r, q, tag)
                    if d:
                        d.update(role=roles[r[0]], pinned=False, owner={"name": nm.get(o) or o, "username": o, "me": False,
                                                                             "avatar": nm.avatar(o)})
                        if d["parent_id"] not in roles:
                            d["parent_id"] = None
                        hits.append(d)
                        if len(hits) >= 50:
                            return hits
    return hits


@router.get("/api/notes/{nid}/backlinks")
def api_note_backlinks(nid: int, request: Request):
    """Seiten des Eigentuemers, die auf diese Seite verweisen (Rueckverweise) - fuer andere nur die, die sie sehen duerfen."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        acc = Access(c, uid)
        owner, role, err = access(c, uid, nid, "view", acc)
        if err is not None:
            return err
        if not c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, owner)).fetchone():
            return _bad("not found", 404)
        # Im gespeicherten JSON steht der Link als href=\"/notes#<id>\" - der Backslash vor dem
        # Anfuehrungszeichen grenzt die Zahl ab (#1 trifft nicht #12).
        rows = c.execute(f"SELECT {NOTE_META_COLS} FROM note WHERE uid=? AND id<>? AND format='blocks' AND deleted_at IS NULL "
                         "AND instr(body, ?) > 0 ORDER BY updated DESC",
                         (owner, nid, f'/notes#{nid}\\"')).fetchall()
        if role != "owner":
            vis = acc.tree(owner)
            rows = [r for r in rows if r[0] in vis]
    out = [_note_meta(r) for r in rows]
    if role != "owner":
        for d in out:
            if d["parent_id"] not in vis:
                d["parent_id"] = None
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


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
