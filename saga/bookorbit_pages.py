"""BookOrbit (2.5, Phase 2): Markierungen eines Buchs als Saga-Seite "Notizen: <Titel>" - nur lesen (BookOrbit -> Saga),
nie zurueckschreiben. Anmeldung, Token-Pflege, SSRF-Schutz und Cover kommen aus saga.bookorbit (Phase 1).

- GET  /api/bookorbit/notes/books?q=   Buecher mit Markierungen (GET /annotations/books, hoechstens 50) + vorhandene Seite.
- POST /api/bookorbit/notes {book_id, parent_id?} bzw. {page_id}  Seite anlegen bzw. aktualisieren -> {page_id, added, ...}.
- Quellen bei BookOrbit: GET /books/<id> (Titel, Autor:innen, eigene Buch-Notiz personalNote), GET /books/<id>/annotations
  ?page=&pageSize=100&sortBy=position (seitenweise, Obergrenze ANN_MAX), GET /books/<id>/bookmarks. Keiner dieser Wege
  verlangt bei BookOrbit "App Attest" (anders als /notebook/... - das ist nur fuer die offizielle iOS-App).
- Zuordnung ohne Migration: user_kv "bookorbit_page:<bookId>" = {"page": <id>, "a": [...], "n": [...], "b": [...], "x": [...]}
  (Seite + alle schon einmal uebernommenen Markierungen/Notizen/Lesezeichen). In der Seite traegt jeder uebernommene Block
  data.bo: {"k": Kapitel-Schluessel, "a"/"n"/"b": BookOrbit-ID, "h": Pruefsumme des Inhalts, wie Saga ihn geschrieben hat}.
- "Aktualisieren" ist idempotent: nur Neues wird eingefuegt (beim eigenen Kapitel, sonst vor den Lesezeichen bzw. am Ende).
  Was die Person in Saga geloescht hat (ID bekannt, Block weg), kommt nicht wieder. Bei BookOrbit geloeschte Markierungen
  bleiben in Saga stehen. Geaenderte Markierungen werden nur nachgezogen, solange der Block unveraendert ist (h passt) -
  eigene Ergaenzungen und Bearbeitungen bleiben immer.
- Text aus BookOrbit ist Fremdinhalt: Klartext, maskiert, Laengen begrenzt, danach derselbe Bereiniger wie fuer den Editor
  (clean_blocks/sanitize_inline). Ziel nur eigene Seiten (parent_id: eigene, nicht geloeschte Seite; keine Datenbank).
- Vertrag fuer die Apps: docs/APP-API.md "BookOrbit" (Faehigkeit bookorbit2)."""
import hashlib
import json
import re
import threading
import time
from datetime import datetime
from html import escape as _esc
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import connect as _db
from saga import bookorbit as bo
from saga.core import E, _api_guard, _bad, _body, account_lang, t, t_account
from saga import i18n

router = APIRouter()

ANN_MAX = int(E.get("SAGA_BOOKORBIT_ANN_MAX") or 1000)       # Markierungen je Buch (seitenweise zu je PAGE_SIZE)
PAGE_SIZE = 100                                               # Hoechstwert von BookOrbit (pageSize <= 100)
BM_MAX = 200                                                  # Lesezeichen je Buch
TEXT_MAX, NOTE_MAX, CHAPTER_MAX = 3000, 3000, 200
BOOKS_LIST_MAX = 50
BUDGET = 190_000                                              # Zeichen JSON (Seite: hoechstens 200 000, saga.pages)
_RATE = (int(E.get("SAGA_BOOKORBIT_NOTES_RATE") or 12), 60.0)   # Anlegen/Aktualisieren je Konto und Minute
_KV = "bookorbit_page:"
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")

_mem = threading.Lock()
_ulocks = {}
_calls = {}


def _ulock(uid):
    with _mem:
        lk = _ulocks.get(uid)
        if lk is None:
            lk = _ulocks[uid] = threading.Lock()
        return lk


def _rate_ok(uid):
    now = time.time()
    with _mem:
        xs = [x for x in _calls.get(uid, []) if now - x < _RATE[1]]
        if len(xs) >= _RATE[0]:
            _calls[uid] = xs
            return False
        xs.append(now)
        _calls[uid] = xs
        if len(_calls) > 5000:
            _calls.clear()
        return True


class Fail(Exception):
    def __init__(self, status, msg, state=None):
        super().__init__(msg)
        self.status, self.msg, self.state = status, msg, state


# --- Fremdinhalt -> Inline-HTML ---------------------------------------------------------------------------------------
def _plain(s, n):
    """Klartext aus BookOrbit -> maskiertes Inline-HTML (Zeilenumbrueche als <br>), hoechstens n Zeichen."""
    s = _CTRL.sub("", str(s or "").replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(s) > n:
        s = s[: n - 1].rstrip() + "…"
    s = re.sub(r"\n{3,}", "\n\n", s)
    return _esc(s, quote=False).replace("\n", "<br>")


def _line(s, n):
    return re.sub(r"\s+", " ", _CTRL.sub("", str(s or ""))).strip()[:n]


def _san(html):
    from saga.pages import sanitize_inline
    return sanitize_inline(html)


def _h(*parts):
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()[:16]


def _ckey(title):
    return "c" + hashlib.sha256(title.encode()).hexdigest()[:12] if title else "c0"


def _color(hexv):
    """Farbe der Markierung (#RRGGBB) -> naechste Farbe der Saga-Palette (oder None)."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hexv or "").strip())
    if not m:
        return None
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 0.12:
        return "gray"
    d = mx - mn
    if mx == r:
        hue = (60 * ((g - b) / d)) % 360
    elif mx == g:
        hue = 60 * ((b - r) / d) + 120
    else:
        hue = 60 * ((r - g) / d) + 240
    for lim, name in ((15, "red"), (38, "orange"), (68, "yellow"), (165, "green"), (250, "blue"), (290, "purple"), (340, "pink")):
        if hue < lim:
            return name
    return "red"


def _day(iso, lang):
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return i18n.day_str(d, lang)


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 31 else None


# --- Abruf bei BookOrbit -----------------------------------------------------------------------------------------------
def _get(uid, path, max_bytes=2 * 1024 * 1024):
    """GET bei BookOrbit -> (status, json|None). Fehler der Verbindung -> Fail."""
    try:
        st, _, data = bo._api(uid, "GET", path, None, max_bytes)
    except bo._Err as e:
        raise _state_fail(e.code)
    if st in (bo.NETERR, bo.TOOBIG, bo.REDIRECT) or st >= 500 or st == 429:
        raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
    if st == 401:
        raise _state_fail("expired")
    try:
        v = json.loads(data) if st == 200 else None
    except (ValueError, UnicodeDecodeError):
        raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
    return st, v


def _state_fail(code):
    if code == "not_connected":
        return Fail(409, t("BookOrbit nicht verbunden (Einstellungen → BookOrbit)"), "none")
    if code == "expired":
        return Fail(409, t("BookOrbit-Anmeldung abgelaufen – bitte neu anmelden."), "expired")
    if code == "blocked":
        return Fail(502, t("Diese Adresse zeigt auf einen internen Server, der nicht freigegeben ist"), "blocked")
    return Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")


def _need_link(uid):
    with _db() as c:
        r = bo._link(c, uid)
    if not r:
        raise _state_fail("not_connected")
    if r[3] != "ok" or not r[2]:
        raise _state_fail("expired")
    return r


def _book(uid, bid):
    st, d = _get(uid, f"/books/{bid}")
    if st in (400, 403, 404) or not isinstance(d, dict):
        raise Fail(404, t("Buch nicht gefunden"))
    authors = [_line(a.get("name"), 120) for a in (d.get("authors") if isinstance(d.get("authors"), list) else [])
               if isinstance(a, dict) and isinstance(a.get("name"), str)][:5]
    note = d.get("personalNote")
    return {"title": _line(d.get("title"), 300), "authors": [a for a in authors if a],
            "note": note if isinstance(note, str) and note.strip() else None}


def _annotations(uid, bid):
    """Alle Markierungen (Reihenfolge im Buch), seitenweise, hoechstens ANN_MAX -> (liste, gesamt, abgeschnitten)."""
    out, total, page = [], 0, 1
    while len(out) < ANN_MAX:
        st, d = _get(uid, f"/books/{bid}/annotations?page={page}&pageSize={PAGE_SIZE}&sortBy=position&sortDir=asc", 4 * 1024 * 1024)
        if st in (403, 404):
            raise Fail(404, t("Buch nicht gefunden"))
        if st != 200 or not isinstance(d, dict) or not isinstance(d.get("items"), list):
            raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
        total = _int(d.get("total")) or total
        items = d["items"]
        for x in items:
            aid = _int(x.get("id")) if isinstance(x, dict) else None
            if aid is None or not isinstance(x.get("text"), str):
                continue
            out.append({"id": aid, "text": x["text"], "note": x.get("note") if isinstance(x.get("note"), str) else None,
                        "chapter": _line(x.get("chapterTitle"), CHAPTER_MAX) if isinstance(x.get("chapterTitle"), str) else "",
                        "color": x.get("color"), "page": _int(x.get("pageno")), "at": x.get("highlightedAt") or x.get("createdAt")})
            if len(out) >= ANN_MAX:
                break
        if len(items) < PAGE_SIZE or page * PAGE_SIZE >= (total or 0):
            break
        page += 1
    seen, uniq = set(), []
    for a in out:                                     # doppelte IDs (Seiten verschoben sich waehrend des Abrufs) nur einmal
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq, max(total, len(uniq)), (total or 0) > len(uniq)


def _bookmarks(uid, bid):
    try:
        st, d = _get(uid, f"/books/{bid}/bookmarks")
    except Fail as f:
        if f.state in ("none", "expired"):
            raise
        return []
    if st != 200 or not isinstance(d, list):
        return []
    out = []
    for x in d[:BM_MAX]:
        bmid = _int(x.get("id")) if isinstance(x, dict) else None
        if bmid is not None:
            out.append({"id": bmid, "title": x.get("title") if isinstance(x.get("title"), str) else "",
                        "note": x.get("note") if isinstance(x.get("note"), str) else None, "at": x.get("createdAt")})
    return out


# --- Bloecke ----------------------------------------------------------------------------------------------------------
def _quote(a, lang):
    text = _san(_plain(a["text"], TEXT_MAX))
    cap = [t_lang(lang, "S. {n}", n=a["page"])] if a["page"] else []
    day = _day(a["at"], lang)
    if day:
        cap.append(day)
    caption = _san(_esc(" · ".join(cap), quote=False))
    b = {"type": "quote", "data": {"text": text, "caption": caption, "alignment": "left",
                                   "bo": {"k": _ckey(a["chapter"]), "a": a["id"], "h": _h(text, caption)}}}
    col = _color(a["color"])
    if col:
        b["tunes"] = {"hkstyle": {"c": col, "bg": True}}
    return b


def _note(a):
    text = _san(_plain(a["note"], NOTE_MAX))
    return {"type": "paragraph", "data": {"text": text, "bo": {"k": _ckey(a["chapter"]), "n": a["id"], "h": _h(text)}}}


def _chapter(title):
    text = _san(_esc(title, quote=False))
    return {"type": "header", "data": {"text": text, "level": 2, "bo": {"k": _ckey(title), "c": 1}}}


def _bm(x, lang):
    parts = [_plain(x["title"], 300) or _esc(t_lang(lang, "Lesezeichen"), quote=False)]
    day = _day(x["at"], lang)
    if day:
        parts.append(_esc(day, quote=False))
    text = "🔖 " + " · ".join(parts)
    if x["note"] and x["note"].strip():
        text += "<br>" + _plain(x["note"], NOTE_MAX)
    text = _san(text)
    return {"type": "paragraph", "data": {"text": text, "bo": {"k": "_bm", "b": x["id"], "h": _h(text)}}}


def t_lang(lang, key, **kw):
    """Text in der Sprache des Kontos (gespeicherter Seiteninhalt), nicht der Anfrage."""
    return i18n.t_lang(lang, key, **kw)


def _i18n_keys():
    """Nie aufgerufen - nennt tools/i18n.py die Schluessel, die hier nur ueber t_lang/t_account benutzt werden."""
    return (t("S. {n}"), t("In BookOrbit öffnen"), t("Notiz zum Buch"), t("Lesezeichen"), t("Notizen: {title}"), t("Unbenannt"))


def _head(uid, base, bid, book, cover_url, lang):
    out = []
    if cover_url:
        out.append({"type": "image", "data": {"file": {"url": cover_url}, "caption": "", "withBorder": False, "withBackground": False,
                                              "stretched": False, "width": 25, "align": "left", "bo": {"k": "_head", "x": "cover"}}})
    link = f"{base}/book/{bid}"
    who = _esc(", ".join(book["authors"]), quote=False) if book["authors"] else ""
    text = (f"<i>{who}</i> · " if who else "") + f'<a href="{_esc(link, quote=True)}">{_esc(t_lang(lang, "In BookOrbit öffnen"), quote=False)}</a>'
    out.append({"type": "paragraph", "data": {"text": _san(text), "bo": {"k": "_head", "x": "info"}}})
    return out


def _booknote(book, lang):
    text = _san(_plain(book["note"], NOTE_MAX * 2))
    return [{"type": "header", "data": {"text": _san(_esc(t_lang(lang, "Notiz zum Buch"), quote=False)), "level": 3,
                                        "bo": {"k": "_head", "x": "bnh"}}},
            {"type": "paragraph", "data": {"text": text, "bo": {"k": "_head", "x": "bn", "h": _h(text)}}}]


def _bm_header(lang):
    return {"type": "header", "data": {"text": _san(_esc(t_lang(lang, "Lesezeichen"), quote=False)), "level": 2,
                                       "bo": {"k": "_bm", "x": "bmh"}}}


def _bo(b):
    d = b.get("data") if isinstance(b, dict) else None
    v = d.get("bo") if isinstance(d, dict) else None
    return v if isinstance(v, dict) else None


def _size(b):
    return len(json.dumps(b, ensure_ascii=False)) + 2


# --- Zusammenfuehren ----------------------------------------------------------------------------------------------------
def merge(blocks, anns, bms, book, seen, head, lang):
    """Bestehende Bloecke + Stand aus BookOrbit -> (bloecke, hinzugefuegt, aktualisiert, voll). Nur oberste Ebene: Bloecke,
    die die Person in Spalten/Toggles verschoben hat, zaehlen als vorhanden (IDs in seen) und werden nicht angefasst."""
    blocks = [b for b in blocks if isinstance(b, dict)]
    size = sum(_size(b) for b in blocks) + 100
    added = updated = 0
    full = False
    by_a, by_n, by_b, xs = {}, {}, {}, set()
    for i, b in enumerate(blocks):
        m = _bo(b)
        if not m:
            continue
        for key, dct in (("a", by_a), ("n", by_n), ("b", by_b)):
            if _int(m.get(key)) is not None:
                dct[m[key]] = i
        if isinstance(m.get("x"), str):
            xs.add(m["x"])

    def unchanged(b, *keys):
        d = b["data"]
        return _bo(b).get("h") == _h(*[d.get(k) if isinstance(d.get(k), str) else "" for k in keys])

    def refresh(i, nb, *keys):
        nonlocal updated
        old = blocks[i]
        if old.get("type") != nb["type"] or not unchanged(old, *keys):
            return
        if all(old["data"].get(k) == nb["data"][k] for k in keys):    # Farbe (tunes) allein ist Gestaltung der Person
            return
        d = dict(old["data"])
        for k in keys:
            d[k] = nb["data"][k]
        d["bo"] = dict(_bo(old), h=nb["data"]["bo"]["h"])
        x = dict(old, data=d)
        if "tunes" in nb and not old.get("tunes"):     # eigene Farbe der Person nie ueberschreiben
            x["tunes"] = nb["tunes"]
        blocks[i] = x
        updated += 1

    ins = []                                   # (position, [bloecke]) - Positionen beziehen sich auf die alte Liste

    def place_after_chapter(k):
        """Einfuegestelle fuer ein neues Element des Kapitels k: nach dem letzten Saga-Block dieses Kapitels (und den
        direkt folgenden eigenen Bloecken der Person bis zum naechsten Saga-Block); None = Kapitel nicht (mehr) da."""
        last = None
        for i, b in enumerate(blocks):
            m = _bo(b)
            if m and m.get("k") == k:
                last = i
        if last is None:
            return None
        j = last + 1
        while j < len(blocks) and not _bo(blocks[j]):
            j += 1
        if j >= len(blocks):                       # am Ende der Seite: direkt nach dem Saga-Block (Eigenes bleibt darunter)
            j = last + 1
        return j

    def tail():
        for i, b in enumerate(blocks):
            m = _bo(b)
            if m and m.get("k") == "_bm":
                return i
        return len(blocks)

    def add(pos, bl):
        nonlocal size, full
        n = sum(_size(b) for b in bl)
        if size + n > BUDGET:
            full = True
            return False
        size += n
        ins.append((pos, bl))
        return True

    # Kopf (nur beim Anlegen bzw. wenn noch nie da gewesen)
    if head and "info" not in xs and "info" not in seen["x"]:
        add(0, head)
        seen["x"].extend(x for x in ("cover", "info") if any(_bo(b).get("x") == x for b in head))
    if book["note"]:
        bn = _booknote(book, lang)
        if "bn" in xs:
            refresh(next(i for i, b in enumerate(blocks) if (_bo(b) or {}).get("x") == "bn"), bn[1], "text")
        elif "bn" not in seen["x"]:
            pos = next((i + 1 for i, b in reversed(list(enumerate(blocks))) if (_bo(b) or {}).get("k") == "_head"), 0)
            if add(pos, bn):
                seen["x"].extend(["bnh", "bn"])
    # Markierungen (+ eigene Notiz darunter), je Kapitel
    new_ch = {}                                # Kapitel, die neu entstehen -> Liste der Bloecke (in Buchreihenfolge)
    pending = {}                               # Kapitel vorhanden: Einfuegestelle -> Bloecke
    for a in anns:
        k = _ckey(a["chapter"])
        q = _quote(a, lang)
        nb = _note(a) if a["note"] and a["note"].strip() else None
        if a["id"] in by_a:
            refresh(by_a[a["id"]], q, "text", "caption")
            if nb is not None:
                if a["id"] in by_n:
                    refresh(by_n[a["id"]], nb, "text")
                elif a["id"] not in seen["n"]:      # Notiz erst spaeter in BookOrbit geschrieben: unter das Zitat
                    if add(by_a[a["id"]] + 1, [nb]):
                        seen["n"].append(a["id"])
                        added += 1
            continue
        if a["id"] in seen["a"]:                    # in Saga entfernt (oder in eine Spalte verschoben): nicht wieder
            continue
        bl = [q] + ([nb] if nb else [])
        pos = place_after_chapter(k)
        if pos is None:
            hd = [] if k in new_ch or not a["chapter"] else [_chapter(a["chapter"])]
            n = sum(_size(b) for b in bl + hd)
            if size + n > BUDGET:
                full = True
                break
            new_ch.setdefault(k, []).extend(hd + bl)
            size += n
        else:
            if size + sum(_size(b) for b in bl) > BUDGET:
                full = True
                break
            pending.setdefault(pos, []).extend(bl)
            size += sum(_size(b) for b in bl)
        seen["a"].append(a["id"])
        if nb:
            seen["n"].append(a["id"])
        added += 1
    ins = [x for x in ins if x[1]]
    for pos, bl in pending.items():
        ins.append((pos, bl))
    t0 = tail()
    if new_ch:
        chunk = []
        for k, bl in new_ch.items():
            chunk.extend(bl)
        ins.append((t0, chunk))
    # Lesezeichen (eigener Abschnitt am Ende)
    new_bm = []
    for x in bms:
        nb = _bm(x, lang)
        if x["id"] in by_b:
            refresh(by_b[x["id"]], nb, "text")
        elif x["id"] not in seen["b"]:
            if size + _size(nb) > BUDGET:
                full = True
                break
            size += _size(nb)
            new_bm.append(nb)
            seen["b"].append(x["id"])
    if new_bm:
        if "bmh" not in xs:
            new_bm.insert(0, _bm_header(lang))
            ins.append((len(blocks), new_bm))
        else:
            last = max(i for i, b in enumerate(blocks) if (_bo(b) or {}).get("k") == "_bm")
            ins.append((last + 1, new_bm))
    # von hinten einfuegen, damit die Positionen der alten Liste gelten (gleiche Stelle: Reihenfolge wie angelegt)
    out = list(blocks)
    for pos, bl in sorted(enumerate(ins), key=lambda e: (e[1][0] if e[1][0] >= 0 else len(blocks), e[0]), reverse=True):
        p = bl[0] if bl[0] >= 0 else len(blocks)
        out[p:p] = bl[1]
    return out, added, updated, full


# --- Zuordnung (user_kv) ----------------------------------------------------------------------------------------------
def _kv_get(c, uid, bid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, _KV + str(bid))).fetchone()
    try:
        v = json.loads(r[0]) if r else None
    except (TypeError, ValueError):
        v = None
    v = v if isinstance(v, dict) else {}
    pg = v.get("page")
    out = {"page": pg if isinstance(pg, int) and not isinstance(pg, bool) and 0 < pg < 2 ** 63 else None}
    for k in ("a", "n", "b"):
        out[k] = [x for x in (v.get(k) if isinstance(v.get(k), list) else []) if _int(x) is not None][: ANN_MAX * 4]
    out["x"] = [x for x in (v.get("x") if isinstance(v.get("x"), list) else []) if isinstance(x, str)][:10]
    return out


def _kv_put(c, uid, bid, v):
    c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?,?,?)", (uid, _KV + str(bid), json.dumps(v, separators=(",", ":"))))


def _pages_of(c, uid):
    """bookId -> page_id fuer alle zugeordneten, noch vorhandenen eigenen Seiten."""
    out = {}
    rows = c.execute("SELECT k, v FROM user_kv WHERE uid=? AND k LIKE ?", (uid, _KV + "%")).fetchall()
    ids = {}
    for k, v in rows:
        try:
            pid = json.loads(v).get("page")
        except (TypeError, ValueError, AttributeError):
            continue
        if isinstance(pid, int) and k[len(_KV):].isdigit():
            ids[int(k[len(_KV):])] = pid
    if ids:
        alive = {r[0] for r in c.execute(f"SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                                         (uid, *ids.values())).fetchall()}
        out = {b: p for b, p in ids.items() if p in alive}
    return out


def _page_ok(c, uid, nid):
    return nid and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL AND kind='page'", (nid, uid)).fetchone()


# --- Anlegen / Aktualisieren ------------------------------------------------------------------------------------------
def _store_cover(uid, bid):
    try:
        data, img = bo.cover(uid, bid)
    except Exception:  # noqa: BLE001 - Cover ist Beiwerk
        return None
    if not data:
        return None
    from saga.files import store_upload
    ext = {"image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(img, "jpg")
    res, err = store_upload(uid, f"cover-{bid}.{ext}", data, img)
    return res["file"]["url"] if res and not err else None


def build(uid, book_id=None, parent_id=None, page_id=None):
    """-> dict fuer die Antwort. Fehler: Fail."""
    from saga.api_v1 import _relay
    from saga.api_v1 import Fail as ApiFail
    from saga.pages import _Reply, _note_update, note_create
    link = _need_link(uid)
    with _db() as c:
        if page_id is not None:                         # "Aus BookOrbit aktualisieren" auf der Seite: Buch ueber die Zuordnung
            book_id = next((b for b, p in _pages_of(c, uid).items() if p == page_id), None)
            if book_id is None:
                raise Fail(404, t("Diese Seite stammt nicht aus BookOrbit"))
        if parent_id is not None and not _page_ok(c, uid, parent_id):
            raise Fail(404, t("Zielseite nicht gefunden (nur eigene Seiten)"))
    lang = account_lang(uid)
    with _ulock(uid):
        book = _book(uid, book_id)
        anns, total, cut = _annotations(uid, book_id)
        bms = _bookmarks(uid, book_id)
        base = link[0]
        # Sperre ueber Prozesse hinweg (je Konto und Buch): Zuordnung lesen, Seite anlegen, zusammenfuehren und Zuordnung
        # schreiben laufen nacheinander - zwei gleichzeitige Aufrufe legen nie zwei Seiten an. Die Sperre haelt die offene
        # Transaktion dieser eigenen Verbindung bis zum Ende des Blocks (Muster wie saga/contacts.py).
        with _db() as lk, lk:
            lk.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"bo:{uid}:{book_id}",))
            with _db() as c:
                kv = _kv_get(c, uid, book_id)
                nid = kv["page"] if _page_ok(c, uid, kv["page"]) else None
            created = False
            if nid is None:
                kv = {"page": None, "a": [], "n": [], "b": [], "x": []}
                title = t_account(uid, "Notizen: {title}", title=book["title"] or t_lang(lang, "Unbenannt"))[:200]
                try:
                    nid = _relay(note_create(uid, {"title": title, "icon": "📖", "parent_id": parent_id, "cover": None}))["id"]
                except ApiFail as f:
                    raise Fail(f.status if f.status < 500 else 502, str(f))
                created = True
            head = _head(uid, base, book_id, book, _store_cover(uid, book_id) if created else None, lang) if created else None
            for attempt in range(3):
                with _db() as c:
                    r = c.execute("SELECT body, format, rev FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()
                if not r:
                    raise Fail(404, "not found")
                if r[1] != "blocks":
                    raise Fail(409, t("Diese Seite ist keine Block-Seite"))
                try:                                 # nicht lesbarer Inhalt: nie mit leerer Liste weiterschreiben (Datenverlust)
                    doc = json.loads(r[0] or "{}") or {}
                    blocks = doc.get("blocks") if isinstance(doc, dict) else None
                    if blocks is None and isinstance(doc, dict) and "blocks" not in doc:
                        blocks = []
                    if not isinstance(blocks, list):
                        raise ValueError
                except ValueError:
                    raise Fail(409, t("Diese Seite ist keine Block-Seite"))
                seen = {k: list(kv[k]) for k in ("a", "n", "b", "x")}
                out, added, updated, full = merge(blocks, anns, bms, book, seen, head, lang)
                if not added and not updated and not (head and created):
                    with _db() as c, c:
                        _kv_put(c, uid, book_id, dict(seen, page=nid))
                    break
                try:
                    try:
                        _relay(_note_update(uid, nid, {"body": json.dumps({"blocks": out}, ensure_ascii=False), "format": "blocks", "rev": r[2]}))
                    except _Reply as rp:
                        _relay(rp.resp)
                except ApiFail as f:
                    if f.status == 409 and attempt < 2:      # dazwischen gespeichert: neu lesen und noch einmal zusammenfuehren
                        continue
                    if created:
                        from saga.pages import note_trash
                        note_trash(uid, nid)
                    raise Fail(f.status if f.status < 500 else 502, str(f))
                with _db() as c, c:
                    _kv_put(c, uid, book_id, dict(seen, page=nid))
                break
            else:
                raise Fail(409, t("Seite wurde gerade geändert – bitte noch einmal versuchen"))
    return {"ok": True, "page_id": nid, "book_id": book_id, "created": created, "added": added, "updated": updated,
            "total": total, "truncated": bool(cut or full), "url": f"/notes#{nid}"}


def books(uid, q=None):
    _need_link(uid)
    qs = f"?status=active&limit={BOOKS_LIST_MAX}" + (f"&q={quote(q)}" if q else "")
    st, d = _get(uid, "/annotations/books" + qs)
    if st != 200 or not isinstance(d, list):
        raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
    with _db() as c:
        pages = _pages_of(c, uid)
    out = []
    for x in d[:BOOKS_LIST_MAX]:
        bid = _int(x.get("bookId")) if isinstance(x, dict) else None
        if bid is None:
            continue
        n = x.get("count")
        out.append({"id": bid, "title": _line(x.get("bookTitle"), 300) or None, "author": _line(x.get("author"), 200) or None,
                    "count": n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0,
                    "cover": f"/api/bookorbit/cover/{bid}", "page_id": pages.get(bid)})
    return {"state": "ok", "books": out}


# --- Endpunkte ---------------------------------------------------------------------------------------------------------
def _fail(f):
    return JSONResponse({"error": f.msg, **({"state": f.state} if f.state else {})}, status_code=f.status,
                        headers={"Cache-Control": "no-store"})


@router.get("/api/bookorbit/notes/books")
async def api_books(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    q = _line(request.query_params.get("q"), 200) or None
    from starlette.concurrency import run_in_threadpool
    try:
        return JSONResponse(await run_in_threadpool(books, uid, q), headers={"Cache-Control": "no-store"})
    except Fail as f:
        return _fail(f)


def _id_in(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 2 ** 31:
        raise ValueError
    return v


@router.post("/api/bookorbit/notes")
async def api_notes(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    b = await _body(request) or {}
    if not isinstance(b, dict):
        return _bad(t("ungültige Angabe"))
    try:
        book_id, parent_id = _id_in(b.get("book_id")), _id_in(b.get("parent_id"))
        page_id = b.get("page_id")
        if page_id is not None and (isinstance(page_id, bool) or not isinstance(page_id, int) or not 0 < page_id < 2 ** 63):
            raise ValueError
    except ValueError:
        return _bad(t("ungültige Angabe"))
    if (book_id is None) == (page_id is None):
        return _bad(t("book_id oder page_id angeben"))
    if not _rate_ok(uid):
        return _bad(t("Zu viele Anfragen – bitte eine Minute warten."), 429)
    from starlette.concurrency import run_in_threadpool
    try:
        return JSONResponse(await run_in_threadpool(build, uid, book_id, parent_id, page_id), headers={"Cache-Control": "no-store"})
    except Fail as f:
        return _fail(f)
