"""Titelbild-Galerie (2.5): mitgelieferte gemeinfreie Bilder (static/covers, covers.json) plus Bilder, die der Admin
hochlaedt (STATE_DIR/covers, Metadaten in Tabelle cover_gallery, Migration 34).

Ausgeliefert unter /covers/<id>.jpg - oeffentlich (auch fuer veroeffentlichte Seiten und eigene Domains), lange
zwischenspeicherbar, mit ETag. ?w=<px> liefert eine verkleinerte Fassung (Vorschaustreifen); die Apps haengen bei diesem
Pfad nichts an (nur bei /files/...), ein ?w= wird trotzdem angenommen.

Als Titelbild einer Seite (note.cover) gilt genau der Pfad /covers/<id>.jpg einer bekannten ID. Ausgeblendete oder
entfernte Bilder fehlen in Auswahl (GET /api/covers) und Zufall, Seiten, die sie schon nutzen, zeigen sie weiter.
Neue Seiten bekommen ein zufaelliges sichtbares Bild, wenn die Konto-Einstellung random_cover an ist (Standard an).
"""
import hashlib
import io
import json
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from db import clean_line, connect as _db
from saga import i18n
from saga.core import HERE, STATE_DIR, _api_guard, _bad, _body, t

router = APIRouter()

BUILTIN_DIR = os.path.join(HERE, "static", "covers")
UP_DIR = os.path.join(STATE_DIR, "covers")
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
PATH_RE = re.compile(r"/covers/([a-z0-9][a-z0-9-]{0,63})\.jpg")
GROUPS = ("nordisch", "romantik", "holzschnitt", "landschaft")      # Reihenfolge in der Auswahl
UPLOAD_MAX = 20 * 1024 * 1024        # Eingabe je Bild
UPLOAD_FILES = 10                    # Bilder je Formular
WIDTH = 1600                         # gespeicherte Breite (wie die mitgelieferten)
PIXELS_MAX = 80_000_000              # Entpackbombe: mehr Bildpunkte nimmt Saga nicht an
THUMB_MAX = 800                      # ?w= bis hierhin verkleinern, darueber das Original
CACHE = "public, max-age=2592000"     # 30 Tage; Inhalt einer ID aendert sich nicht (neue Bilder = neue ID)
SETTING = "random_cover"             # user_kv: '0' = aus, sonst an


def _group_label(g):
    return {"nordisch": t("Nordisch"), "romantik": t("Romantik"), "holzschnitt": t("Holzschnitt"),
            "landschaft": t("Landschaft")}.get(g, g)


def _load_builtin():
    try:
        with open(os.path.join(BUILTIN_DIR, "covers.json"), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as ex:
        print(f"saga: Titelbild-Galerie nicht lesbar: {ex}", flush=True)
        return [], {}
    out = []
    for c in d.get("covers") or []:
        cid = str(c.get("id") or "")
        if not ID_RE.fullmatch(cid) or cid.startswith("u-") or not os.path.isfile(os.path.join(BUILTIN_DIR, cid + ".jpg")):
            continue
        out.append({"id": cid, "builtin": True, "title_de": c.get("title_de") or "", "title_en": c.get("title_en") or "",
                    "artist": c.get("artist") or "", "date": c.get("date") or "", "url": c.get("url") or "",
                    "source": d.get("source") or "", "group": c.get("group") or "", "pos": _pos(c.get("pos"))})
    return out, {"source": d.get("source") or "", "license": d.get("license") or ""}


def _pos(v, default=50.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, f)) if f == f else default


BUILTIN, META = _load_builtin()           # einmal beim Start
BUILTIN_IDS = frozenset(c["id"] for c in BUILTIN)

_lock = threading.Lock()
_state = None                             # id -> Eintrag (mitgelieferte mit Admin-Aenderungen + hochgeladene)
_order = []                               # IDs in Anzeige-Reihenfolge


def invalidate():
    global _state
    with _lock:
        _state = None


def _rows():
    try:
        with _db() as c:
            return c.execute("SELECT id, builtin, hidden, deleted, title, artist, date, source, url, grp, pos, created "
                             "FROM cover_gallery ORDER BY created, id").fetchall()
    except Exception as ex:  # noqa: BLE001 - Tabelle fehlt (alte Datenbank) o. a.: nur die mitgelieferten
        print(f"saga: Titelbild-Galerie ohne Datenbank-Angaben: {type(ex).__name__}", flush=True)
        return []


def _entries():
    global _state, _order
    with _lock:
        if _state is not None:
            return _state, _order
    st, order = {}, []
    for c in BUILTIN:
        st[c["id"]] = dict(c, hidden=False, deleted=False, title="")
        order.append(c["id"])
    for r in _rows():
        cid = r[0]
        if cid in st:                     # Admin-Aenderung an einem mitgelieferten Bild
            e = st[cid]
            e["hidden"] = bool(r[2])
            for k, v in (("title", r[4]), ("artist", r[5]), ("group", r[9])):
                if v:
                    e[k] = v
            if r[10] is not None:
                e["pos"] = _pos(r[10])
        elif not r[1] and cid.startswith("u-"):
            st[cid] = {"id": cid, "builtin": False, "hidden": bool(r[2]), "deleted": bool(r[3]), "title": r[4] or "",
                       "title_de": "", "title_en": "", "artist": r[5] or "", "date": r[6] or "", "source": r[7] or "",
                       "url": r[8] or "", "group": r[9] or "", "pos": _pos(r[10]), "created": r[11]}
            order.append(cid)
    with _lock:
        _state, _order = st, order
    return st, order


def get(cid):
    return _entries()[0].get(cid)


def visible():
    st, order = _entries()
    return [st[i] for i in order if not st[i]["hidden"] and not st[i]["deleted"]]


def all_entries():
    st, order = _entries()
    return [st[i] for i in order]


def path_of(cid):
    return f"/covers/{cid}.jpg"


def cover_id(url):
    """'/covers/<id>.jpg' einer bekannten ID (auch ausgeblendet/entfernt, solange die Datei da ist) -> id, sonst None."""
    m = PATH_RE.fullmatch(str(url or ""))
    return m.group(1) if m and get(m.group(1)) else None


def is_cover(url):
    return cover_id(url) is not None


def builtin_path(url):
    """Nur mitgelieferte Bilder (gibt es auf jedem Saga-Server gleich): Pfad oder ''. Fuer Foederation und Vorlagen."""
    s = str(url or "").strip()
    if ID_RE.fullmatch(s) and s in BUILTIN_IDS:
        return path_of(s)
    m = PATH_RE.fullmatch(s)
    return s if m and m.group(1) in BUILTIN_IDS else ""


def title_of(e, lang=None):
    lang = lang or i18n.get_lang()
    if e.get("title"):
        return e["title"]
    return (e.get("title_de") if lang == "de" else e.get("title_en")) or e.get("title_de") or e.get("title_en") or e["id"]


def pick_random():
    """(pfad, pos) eines zufaelligen sichtbaren Bildes oder (None, None)."""
    v = visible()
    if not v:
        return None, None
    e = secrets.choice(v)
    return path_of(e["id"]), e["pos"]


def pos_of(url, default=50.0):
    e = get(cover_id(url) or "")
    return e["pos"] if e else default


# --- Konto-Einstellung "Neue Seiten mit zufaelligem Titelbild" ---------------------------------------------------
def random_on(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, SETTING)).fetchone()
    return not (r and r[0] == "0")


def set_random(uid, on):
    with _db() as c, c:
        c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)", (uid, SETTING, "1" if on else "0"))


def auto(c, uid):
    """Titelbild fuer eine neue Seite: (pfad, pos) oder (None, None), wenn die Einstellung aus ist oder nichts sichtbar."""
    if not uid or not random_on(c, uid):
        return None, None
    return pick_random()


# --- Ausliefern ---------------------------------------------------------------------------------------------------
_thumbs = {}                              # (id, w) -> bytes (wenige KB je Bild)
_THUMB_KEEP = 200


def file_of(cid):
    e = get(cid)
    if not e:
        return None
    p = os.path.join(BUILTIN_DIR if e["builtin"] else UP_DIR, cid + ".jpg")
    return p if os.path.isfile(p) else None


def _thumb(cid, p, w, etag):
    k = (cid, w, etag)
    if k in _thumbs:
        return _thumbs[k]
    from PIL import Image
    with Image.open(p) as im:
        im = im.convert("RGB")
        if im.width > w:
            im = im.resize((w, max(1, round(im.height * w / im.width))), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True, progressive=True)
    data = out.getvalue()
    if len(_thumbs) >= _THUMB_KEEP:
        _thumbs.clear()
    _thumbs[k] = data
    return data


def serve(name, request):
    m = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,63})\.jpg", name or "")
    p = file_of(m.group(1)) if m else None
    if not p:
        return Response(status_code=404, headers={"Cache-Control": "no-cache"})
    st = os.stat(p)
    w = request.query_params.get("w") or ""
    w = int(w) if w.isdigit() else 0
    w = 0 if w <= 0 or w > THUMB_MAX else max(64, (w + 63) // 64 * 64)      # Stufen, damit der Speicher klein bleibt
    etag = '"' + hashlib.sha1(f"{name}|{st.st_size}|{int(st.st_mtime)}|{w}".encode()).hexdigest()[:20] + '"'
    head = {"Cache-Control": CACHE, "ETag": etag, "X-Content-Type-Options": "nosniff"}
    inm = request.headers.get("if-none-match") or ""
    if etag in [x.strip() for x in inm.split(",")] or inm.strip() == "*":
        return Response(status_code=304, headers=head)
    if w:
        try:
            return Response(_thumb(m.group(1), p, w, etag), media_type="image/jpeg", headers=head)
        except Exception as ex:  # noqa: BLE001 - kaputtes Bild: Original ausliefern
            print(f"saga: Titelbild-Vorschau {name}: {type(ex).__name__}", flush=True)
    with open(p, "rb") as f:
        data = f.read()
    return Response(data, media_type="image/jpeg", headers=head)


@router.get("/covers/{name}")
def covers_file(name: str, request: Request):
    return serve(name, request)


def listing(uid=None):
    """Inhalt von GET /api/covers: sichtbare Bilder in Anzeige-Reihenfolge, gruppiert nach group."""
    lang = i18n.get_lang()
    items = []
    for e in visible():
        items.append({"id": e["id"], "url": path_of(e["id"]), "thumb": path_of(e["id"]) + "?w=384", "title": title_of(e, lang),
                      "artist": e["artist"], "date": e["date"], "source_url": e["url"], "source": e["source"],
                      "group": e["group"], "pos": e["pos"], "builtin": e["builtin"]})
    seen = []
    for g in list(GROUPS) + [x["group"] for x in items]:
        if g and g not in seen and any(x["group"] == g for x in items):
            seen.append(g)
    if any(not x["group"] for x in items):
        seen.append("")
    out = {"covers": items, "groups": [{"id": g, "label": _group_label(g) if g else t("Weitere")} for g in seen],
           "license": META.get("license", ""), "source": META.get("source", "")}
    if uid:
        with _db() as c:
            out["random_cover"] = random_on(c, uid)
    return out


def info(url):
    """Nachweis zu einem Titelbild der Galerie (auch ausgeblendete) - fuer 'Ueber dieses Bild'."""
    e = get(cover_id(url) or "")
    if not e:
        return None
    return {"id": e["id"], "title": title_of(e), "artist": e["artist"], "date": e["date"], "source_url": e["url"],
            "source": e["source"], "group": e["group"], "pos": e["pos"]}


@router.get("/api/covers")
def api_covers(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    return JSONResponse(listing(uid))


@router.get("/api/account/pages")
def api_account_pages(request: Request):
    """Seiten-Einstellungen des Kontos (2.5): random_cover = neue Seiten mit zufaelligem Titelbild (Standard an)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        return JSONResponse({"random_cover": random_on(c, uid)})


@router.put("/api/account/pages")
async def api_account_pages_put(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    if not isinstance(b.get("random_cover"), bool):
        return _bad(t("Ungültige Eingabe."))
    set_random(uid, b["random_cover"])
    return JSONResponse({"ok": True, "random_cover": b["random_cover"]})


# --- Admin: hochladen, bearbeiten, ausblenden, entfernen ---------------------------------------------------------
def _clean(v, n=200):
    return clean_line(str(v or "")).strip()[:n]


def _clean_url(v):
    s = _clean(v, 500)
    return s if re.match(r"https?://[^\s<>\"']+$", s) else ""


def _clean_group(v):
    s = _clean(v, 40).lower()
    s = re.sub(r"[^\w -]", "", s).strip()
    return s


def process(data):
    """Bild am Inhalt pruefen, drehen (EXIF), auf WIDTH verkleinern, als JPEG ohne Metadaten -> (bytes, w, h).
    ValueError mit Meldung bei Unsinn."""
    if not data:
        raise ValueError(t("Kein Bild gewählt."))
    if len(data) > UPLOAD_MAX:
        raise ValueError(t("Das Bild ist zu groß (höchstens {n} MB).", n=UPLOAD_MAX // 1048576))
    from PIL import Image, ImageOps
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:  # noqa: BLE001 - HEIC optional
        pass
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP", "GIF", "HEIF", "TIFF", "BMP"):
                raise ValueError
            if im.width * im.height > PIXELS_MAX or im.width < 64 or im.height < 64:
                raise ValueError
            im.seek(0)
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB")
            if im.width > WIDTH:
                im = im.resize((WIDTH, max(1, round(im.height * WIDTH / im.width))), Image.LANCZOS)
            out = io.BytesIO()
            im.save(out, "JPEG", quality=85, optimize=True, progressive=True)      # ohne exif=: keine Metadaten/GPS
            return out.getvalue(), im.width, im.height
    except ValueError:
        raise ValueError(t("Kein unterstütztes Bild (JPEG, PNG, WebP, HEIC; mindestens 64 × 64 Pixel)."))
    except Exception:  # noqa: BLE001 - PIL: kaputt, unbekannt, Bombe
        raise ValueError(t("Kein unterstütztes Bild (JPEG, PNG, WebP, HEIC; mindestens 64 × 64 Pixel)."))


def add_upload(data, title="", artist="", date="", source="", url="", group="", pos=50.0, by=""):
    """Hochgeladenes Bild aufnehmen -> id. Gleiches Bild (gleicher Inhalt) zweimal: dieselbe ID, Angaben neu."""
    jpg, _, _ = process(data)
    cid = "u-" + hashlib.sha256(jpg).hexdigest()[:12]
    os.makedirs(UP_DIR, mode=0o750, exist_ok=True)
    p = os.path.join(UP_DIR, cid + ".jpg")
    if not os.path.isfile(p):
        tmp = p + f".{os.getpid()}.{secrets.token_hex(4)}.tmp"
        with open(tmp, "wb") as f:
            f.write(jpg)
        os.replace(tmp, p)
    with _db() as c, c:
        c.execute("INSERT INTO cover_gallery (id, builtin, hidden, deleted, title, artist, date, source, url, grp, pos, created, created_by) "
                  "VALUES (?,0,0,0,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET hidden=0, deleted=0, title=excluded.title, "
                  "artist=excluded.artist, date=excluded.date, source=excluded.source, url=excluded.url, grp=excluded.grp, pos=excluded.pos",
                  (cid, _clean(title), _clean(artist), _clean(date, 60), _clean(source), _clean_url(url), _clean_group(group),
                   _pos(pos), time.time(), by))
    invalidate()
    return cid


def update(cid, title=None, artist=None, group=None, pos=None):
    """Angaben aendern (mitgeliefert: als Admin-Aenderung, leer = wie mitgeliefert)."""
    e = get(cid)
    if not e or e.get("deleted"):
        return False
    vals = {"title": _clean(title) if title is not None else None, "artist": _clean(artist) if artist is not None else None,
            "grp": _clean_group(group) if group is not None else None, "pos": _pos(pos) if pos is not None else None}
    with _db() as c, c:
        _ensure_row(c, cid, e)
        for k, v in vals.items():
            if v is not None:
                c.execute(f"UPDATE cover_gallery SET {k}=? WHERE id=?", (v if k == "pos" else (v or None), cid))
    invalidate()
    return True


def _ensure_row(c, cid, e):
    if e["builtin"]:
        c.execute("INSERT INTO cover_gallery (id, builtin, hidden, deleted, created) VALUES (?,1,0,0,?) ON CONFLICT (id) DO NOTHING",
                  (cid, time.time()))


def set_hidden(cid, hidden):
    e = get(cid)
    if not e or e.get("deleted"):
        return False
    with _db() as c, c:
        _ensure_row(c, cid, e)
        c.execute("UPDATE cover_gallery SET hidden=? WHERE id=?", (1 if hidden else 0, cid))
    invalidate()
    return True


def usage(cid):
    """Seiten (auch im Papierkorb), die dieses Bild als Titelbild nutzen."""
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM note WHERE cover=?", (path_of(cid),)).fetchone()[0]


def remove(cid):
    """Hochgeladenes Bild entfernen -> ('soft', n) solange n Seiten es nutzen (Datei bleibt, nicht mehr waehlbar),
    ('gone', 0) endgueltig geloescht, (None, 0) unbekannt/mitgeliefert."""
    e = get(cid)
    if not e or e["builtin"]:
        return None, 0
    n = usage(cid)
    with _db() as c, c:
        if n:
            c.execute("UPDATE cover_gallery SET deleted=1, hidden=1 WHERE id=?", (cid,))
        else:
            c.execute("DELETE FROM cover_gallery WHERE id=?", (cid,))
    if not n:
        try:
            os.remove(os.path.join(UP_DIR, cid + ".jpg"))
        except OSError:
            pass
        for k in [k for k in _thumbs if k[0] == cid]:
            _thumbs.pop(k, None)
    invalidate()
    return ("soft", n) if n else ("gone", 0)
