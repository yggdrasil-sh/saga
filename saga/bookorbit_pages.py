"""BookOrbit (2.5, Phase 2): Markierungen eines Buchs als Saga-Seiten - nur lesen (BookOrbit -> Saga), nie zurueckschreiben.
Anmeldung, Token-Pflege, SSRF-Schutz und Cover kommen aus saga.bookorbit (Phase 1).

Aufbau (2.5, 9.11.): Sammelseite "Buecher" (📚) -> je Buch eine Buchseite (📖, Titel = Buchtitel) -> je Kapitel eine
Kapitelseite (📑, Titel = Kapitelname, Reihenfolge wie im Buch; Teile mit Kapiteln darunter als Unter-Unterseiten).
- Sammelseite: beim ersten Bedarf angelegt, user_kv "bookorbit_notes_home" = {"page", "moved", "x", "listed"}. Geloescht ->
  beim naechsten Bedarf neu; umbenannt/verschoben bleibt sie, wie sie ist. Inhalt: Einleitung + Liste der Buchseiten
  (neueste zuerst). Unveraenderte Liste wird neu aufgebaut, bearbeitete bekommt nur neue Buecher oben dazu.
  Einmalig ("moved"): Buchseiten, die noch auf oberster Ebene liegen, wandern hinein; selbst einsortierte bleiben.
- Buchseite: Kopf (Cover links 25 %, "Autor · In BookOrbit oeffnen"), "Notiz zum Buch", "Kapitel" (Liste der Kapitelseiten
  mit Anzahl Markierungen, Teile eingerueckt), Markierungen ohne Kapitel (bzw. ueber der Obergrenze CH_MAX) unter "Ohne
  Kapitel", zuletzt "Lesezeichen".
- Kapitel kommen aus dem Inhaltsverzeichnis (GET /epub/<id>/info, toc; Tiefe 1 und 2 werden Seiten, tiefere Eintraege
  zaehlen zum Vorfahren der Tiefe 2) und aus den Markierungen (chapterTitle = TOC-Label laut BookOrbit
  chapterTitleForHref; Vergleich ohne Gross-/Kleinschreibung). Kein EPUB/kein TOC -> nur Kapitel aus Markierungen.
- Kapitelseite: Kopfzeile "← Buch · Kapitel n von m" (Seitenverweise), Zitate mit eigener Notiz darunter; leer: Platzhalter.
- Quellen bei BookOrbit: GET /books/<id>, GET /books/<id>/annotations?page=&pageSize=100&sortBy=position (seitenweise,
  Obergrenze ANN_MAX), GET /books/<id>/bookmarks, GET /epub/<id>/info. Liste: GET /annotations/books, mit Suche zusaetzlich
  GET /books/search (ganze Bibliothek), Filter "Gelesen": POST /books/query (readStatus read, neueste zuerst).
  Die Liste ist begrenzt (SAGA_BOOKORBIT_LIST_RATE, 60 je Minute und Konto). Nebenwirkung des GET: einmalig je Konto
  ("moved" fehlt noch) legt er die Sammelseite an und raeumt alte Notizseiten von der obersten Ebene hinein; ebenso einmalig
  ("gal" fehlt noch) stellt er eine bestehende Sammelseite auf den Unterseiten-Block um (sonst beim naechsten Anlegen).
  Keiner dieser Wege verlangt "App Attest" (anders als /notebook/... - das ist nur fuer die offizielle iOS-App).
- Zuordnung ohne Migration: user_kv "bookorbit_page:<bookId>" = {"page", "ch": {Kapitel-Schluessel: Seite}, "v": 2,
  "a"/"n"/"b": schon einmal uebernommene IDs, "x": schon einmal angelegte Kopf-/Abschnittsbloecke}. Jeder uebernommene
  Block traegt data.bo: {"k": Kapitel-Schluessel, "a"/"n"/"b": BookOrbit-ID bzw. "x": Rolle, "h": Pruefsumme}.
- "Aktualisieren" ist idempotent: nur Neues kommt dazu. Was die Person geloescht hat (Kapitelseite, Markierung, Abschnitt),
  kommt nicht wieder; eigene Texte bleiben; geaenderte Markierungen werden nur nachgezogen, solange der Block unveraendert
  ist (h passt). Bei BookOrbit geloeschte Markierungen bleiben in Saga stehen.
- Alte Einseiten-Notizseiten (Phase 2 bis 9.11.: "Notizen: <Titel>", Kapitel als H2) werden beim naechsten Aktualisieren
  umgebaut: je H2-Kapitel wandert der Abschnitt (Zitate, Notizen, eigene Bloecke bis vor den naechsten Saga-Block eines
  anderen Kapitels) unveraendert in die neue Kapitelseite; beim letzten Kapitel endet der Abschnitt nach dessen letztem
  Saga-Block - eigene Bloecke dahinter bleiben als Schlussgedanken auf der Buchseite. Der Titel der alten Seite bleibt.
  Reihenfolge "erst kopieren, dann schneiden" (_build_pages): Kapitelseiten anlegen und je sofort in user_kv merken,
  fehlende Bloecke hineinkopieren, erst dann (mit Revision) aus der Buchseite entfernen, was vollstaendig kopiert ist,
  zuletzt v = 2. Ein Abbruch an beliebiger Stelle erzeugt hoechstens Doppeltes; der naechste Lauf macht weiter.
- Nebenlaeufigkeit: Sperre in der Datenbank je Konto+Buch (bzw. Konto fuer die Sammelseite), Revisionskonflikte -> neu lesen.
- Text aus BookOrbit ist Fremdinhalt: Klartext, maskiert, Laengen begrenzt, danach derselbe Bereiniger wie fuer den Editor
  (sanitize_inline). Ziel nur eigene Seiten (parent_id: eigene, nicht geloeschte Seite; keine Datenbank).
- Vertrag fuer die Apps: docs/APP-API.md "BookOrbit" (Faehigkeiten bookorbit2, bookorbit3)."""
import hashlib
import html as _html
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
from saga.core import E, _api_guard, _bad, _body, account_lang, t
from saga import i18n

router = APIRouter()

ANN_MAX = int(E.get("SAGA_BOOKORBIT_ANN_MAX") or 1000)       # Markierungen je Buch (seitenweise zu je PAGE_SIZE)
CH_MAX = int(E.get("SAGA_BOOKORBIT_CH_MAX") or 150)          # Kapitelseiten je Buch
PAGE_SIZE = 100                                               # Hoechstwert von BookOrbit (pageSize <= 100)
BM_MAX = 200                                                  # Lesezeichen je Buch
TEXT_MAX, NOTE_MAX, CHAPTER_MAX, CH_TITLE_MAX = 3000, 3000, 200, 120
TOC_NODES_MAX = 3000
BOOKS_LIST_MAX = 50
SEARCH_MAX = 20                                               # /books/search: limit <= 20 bei BookOrbit
BUDGET = 190_000                                              # Zeichen JSON (Seite: hoechstens 200 000, saga.pages)
_RATE = (int(E.get("SAGA_BOOKORBIT_NOTES_RATE") or 12), 60.0)   # Anlegen/Aktualisieren je Konto und Minute
_RATE_LIST = (int(E.get("SAGA_BOOKORBIT_LIST_RATE") or 60), 60.0)  # Liste/Suche je Konto und Minute
_KV = "bookorbit_page:"
_KV_HOME = "bookorbit_notes_home"
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")
ICON_BOOK, ICON_HOME, ICON_CH = "📖", "📚", "📑"

_mem = threading.Lock()
_ulocks = {}
_calls = {}
_lcalls = {}


def _ulock(uid):
    with _mem:
        lk = _ulocks.get(uid)
        if lk is None:
            lk = _ulocks[uid] = threading.Lock()
        return lk


def _rate_ok(uid, store=None, rate=None):
    store, rate = (_calls, _RATE) if store is None else (store, rate)
    now = time.time()
    with _mem:
        xs = [x for x in store.get(uid, []) if now - x < rate[1]]
        if len(xs) >= rate[0]:
            store[uid] = xs
            return False
        xs.append(now)
        store[uid] = xs
        if len(store) > 5000:
            store.clear()
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


def _short(s, n):
    s = _line(s, 10 * n)
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _san(html):
    from saga.pages import sanitize_inline
    return sanitize_inline(html)


def _h(*parts):
    return hashlib.sha256(json.dumps(list(parts), ensure_ascii=False).encode()).hexdigest()[:16]


def _norm(s):
    """Inline-HTML -> Vergleichsform fuer Saga-eigene Bloecke mit Seitenverweisen: der Editor schreibt beim Speichern den
    aktuellen Seitentitel in den Verweis und ordnet Marken neu - zaehlen sollen nur Ziel und sichtbarer Text."""
    s = re.sub(r'<a\b[^>]*href="(/notes#\d+)"[^>]*>.*?</a>', r" [\1] ", str(s or ""), flags=re.S)
    s = re.sub(r"<[^>]*>", "", s)
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def _hn(text):
    return _h(_norm(text))


def _hl(items):
    def nm(xs):
        return [[_norm(x.get("content") if isinstance(x, dict) else x), nm(x.get("items") if isinstance(x, dict) and isinstance(x.get("items"), list) else [])]
                for x in (xs if isinstance(xs, list) else [])]
    return _h(nm(items))


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


def _pid(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 2 ** 63 else None


# --- Abruf bei BookOrbit -----------------------------------------------------------------------------------------------
def _get(uid, path, max_bytes=2 * 1024 * 1024, method="GET", body=None):
    """Aufruf bei BookOrbit (Praefix /api/v1 setzt saga.bookorbit) -> (status, json|None). Fehler der Verbindung -> Fail."""
    try:
        st, _, data = bo._api(uid, method, path, body, max_bytes)
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


def _soft(fn, *a):
    """Beiwerk (TOC, Lesezeichen, Suche): nicht erreichbar -> None; nicht verbunden/abgelaufen -> weiterreichen."""
    try:
        return fn(*a)
    except Fail as f:
        if f.state in ("none", "expired"):
            raise
        return None


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
    st, d = _get(uid, f"/books/{bid}/bookmarks")
    if st != 200 or not isinstance(d, list):
        return []
    out = []
    for x in d[:BM_MAX]:
        bmid = _int(x.get("id")) if isinstance(x, dict) else None
        if bmid is not None:
            out.append({"id": bmid, "title": x.get("title") if isinstance(x.get("title"), str) else "",
                        "note": x.get("note") if isinstance(x.get("note"), str) else None, "at": x.get("createdAt")})
    return out


def _toc(uid, bid):
    """Inhaltsverzeichnis (EPUB) -> (eintraege, alias). eintraege in Buchreihenfolge: {key, label, depth (1|2), parent}.
    alias: Label (casefold) -> key, auch fuer tiefere Eintraege (zaehlen zum Vorfahren der Tiefe 2). Gleiche Label an
    mehreren Stellen bekommen eigene Seiten; Markierungen damit landen beim ersten."""
    st, d = _get(uid, f"/epub/{bid}/info", 4 * 1024 * 1024)
    toc = d.get("toc") if st == 200 and isinstance(d, dict) else None
    out, alias, keys, count = [], {}, set(), [0]

    def visit(items, depth, parent, anc, level):
        if not isinstance(items, list) or level > 12:
            return
        for it in items:
            if count[0] >= TOC_NODES_MAX:
                return
            count[0] += 1
            if not isinstance(it, dict):
                continue
            lab = _line(it.get("label"), CHAPTER_MAX) if isinstance(it.get("label"), str) else ""
            if not lab:
                visit(it.get("children"), depth, parent, anc, level + 1)
                continue
            if depth <= 2:
                key, n = _ckey(lab), 1
                while key in keys:
                    n += 1
                    key = _ckey(f"{lab}\x00{n}")
                keys.add(key)
                me = {"key": key, "label": lab, "depth": depth, "parent": parent["key"] if parent else None}
                out.append(me)
                alias.setdefault(lab.casefold(), key)
                visit(it.get("children"), depth + 1, me if depth == 1 else parent, me, level + 1)
            else:
                if anc:
                    alias.setdefault(lab.casefold(), anc["key"])
                visit(it.get("children"), depth + 1, parent, anc, level + 1)

    visit(toc.get("children") if isinstance(toc, dict) else None, 1, None, None, 0)
    return out, alias


def _chapters(toc, alias, anns, legacy, have):
    """TOC + Kapitel aus Markierungen + Kapitel alter Seiten -> (eintraege [hoechstens CH_MAX, vorhandene Seiten immer],
    alias, abgeschnitten). legacy: [(key, label)]; have: Schluessel mit schon zugeordneter Seite."""
    out, alias, keys = list(toc), dict(alias), {e["key"] for e in toc}
    for k, label in legacy:
        cf = label.casefold()
        if k in keys or cf in alias:
            continue
        out.append({"key": k, "label": label, "depth": 1, "parent": None})
        keys.add(k)
        alias[cf] = k
    for a in anns:
        cf = a["chapter"].casefold()
        if not cf or cf in alias:
            continue
        k = _ckey(a["chapter"])
        if k in keys:
            alias[cf] = k
            continue
        out.append({"key": k, "label": a["chapter"], "depth": 1, "parent": None})
        keys.add(k)
        alias[cf] = k
    keep, n = [], 0
    for e in out:
        if e["key"] in have or n < CH_MAX:
            keep.append(e)
            n += 0 if e["key"] in have else 1
    return keep, alias, len(keep) < len(out)


# --- Bloecke ----------------------------------------------------------------------------------------------------------
def t_lang(lang, key, **kw):
    """Text in der Sprache des Kontos (gespeicherter Seiteninhalt), nicht der Anfrage."""
    return i18n.t_lang(lang, key, **kw)


def _i18n_keys():
    """Nie aufgerufen - nennt tools/i18n.py die Schluessel, die hier nur ueber t_lang benutzt werden."""
    return (t("S. {n}"), t("In BookOrbit öffnen"), t("Notiz zum Buch"), t("Lesezeichen"), t("Unbenannt"), t("Bücher##Sammelseite"),
            t("Kapitel##Buchseite"), t("Ohne Kapitel"), t("Kapitel {n} von {m}"), t("{n} {n:Markierung|Markierungen}"),
            t("Noch keine Markierungen – schreib hier deine Notizen zum Kapitel."),
            t("Deine Bücher aus BookOrbit: je Buch eine Seite mit Kapiteln, Markierungen und Lesezeichen. Neue Bücher kommen oben dazu."),
            t("Deine Bücher aus BookOrbit: je Buch eine Seite mit Kapiteln, Markierungen und Lesezeichen. Neue Bücher kommen vorne dazu."))


def _gray(html):
    return f'<span class="hk-c-gray">{html}</span>'


def _quote(a, k, lang):
    text = _san(_plain(a["text"], TEXT_MAX))
    cap = [t_lang(lang, "S. {n}", n=a["page"])] if a["page"] else []
    day = _day(a["at"], lang)
    if day:
        cap.append(day)
    caption = _san(_esc(" · ".join(cap), quote=False))
    b = {"type": "quote", "data": {"text": text, "caption": caption, "alignment": "left",
                                   "bo": {"k": k, "a": a["id"], "h": _h(text, caption)}}}
    col = _color(a["color"])
    if col:
        b["tunes"] = {"hkstyle": {"c": col, "bg": True}}
    return b


def _note(a, k):
    text = _san(_plain(a["note"], NOTE_MAX))
    return {"type": "paragraph", "data": {"text": text, "bo": {"k": k, "n": a["id"], "h": _h(text)}}}


def _chapter(title, k):
    text = _san(_esc(title, quote=False))
    return {"type": "header", "data": {"text": text, "level": 2, "bo": {"k": k, "c": 1}}}


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


def _head(base, bid, book, cover_url, lang):
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


def _section(lang, key, x, k):
    return {"type": "header", "data": {"text": _san(_esc(t_lang(lang, key), quote=False)), "level": 2, "bo": {"k": k, "x": x}}}


def _bm_header(lang):
    return _section(lang, "Lesezeichen", "bmh", "_bm")


def _link(pid, label):
    return f'<a href="/notes#{int(pid)}">{_esc(label, quote=False)}</a>'


def _nav(book_pid, book_title, part, n, m, lang):
    """Kopfzeile der Kapitelseite: "← Buch [· Teil] · Kapitel n von m"."""
    text = _gray("← ") + _link(book_pid, book_title)
    if part:
        text += _gray(" · ") + _link(part[0], part[1])
    text += _gray(_esc(" · " + t_lang(lang, "Kapitel {n} von {m}", n=n, m=m), quote=False))
    text = _san(text)
    return {"type": "paragraph", "data": {"text": text, "bo": {"k": "_nav", "x": "back", "h": _hn(text)}}}


def _placeholder(lang):
    text = _san(_gray(_esc(t_lang(lang, "Noch keine Markierungen – schreib hier deine Notizen zum Kapitel."), quote=False)))
    return {"type": "paragraph", "data": {"text": text, "bo": {"k": "_ph", "x": "ph", "h": _hn(text)}}}


def _list(items, x, k):
    return {"type": "list", "data": {"style": "unordered", "meta": {}, "items": items, "bo": {"k": k, "x": x, "h": _hl(items)}}}


def _bo(b):
    d = b.get("data") if isinstance(b, dict) else None
    v = d.get("bo") if isinstance(d, dict) else None
    return v if isinstance(v, dict) else None


def _xi(blocks, x):
    return next((i for i, b in enumerate(blocks) if (_bo(b) or {}).get("x") == x), None)


def _size(b):
    return len(json.dumps(b, ensure_ascii=False)) + 2


def _quotes_on(blocks):
    return sum(1 for b in blocks if _int((_bo(b) or {}).get("a")) is not None)


# --- Zusammenfuehren ----------------------------------------------------------------------------------------------------
def merge(blocks, anns, bms, book, seen, head, lang, keyof=None, chapter_page=False):
    """Bestehende Bloecke + Stand aus BookOrbit -> (bloecke, hinzugefuegt, aktualisiert, voll). Nur oberste Ebene: Bloecke,
    die die Person in Spalten/Toggles verschoben hat, zaehlen als vorhanden (IDs in seen) und werden nicht angefasst.
    keyof(a) -> Kapitel-Schluessel. chapter_page: Kapitelseite (keine Ueberschriften; Neues nach dem letzten Zitat bzw.
    unter der Kopfzeile). Sonst Buchseite: Markierungen ohne Kapitel unter "Ohne Kapitel" vor den Lesezeichen."""
    keyof = keyof or (lambda a: _ckey(a["chapter"]))
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
            if m and (m.get("k") == k if not chapter_page else (_int(m.get("a")) or _int(m.get("n")))):
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
        if chapter_page:                           # Kapitelseite: unter der Kopfzeile (eigene Texte bleiben darunter)
            i = _xi(blocks, "back")
            return 0 if i is None else i + 1
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
    if book and book["note"]:
        bn = _booknote(book, lang)
        if "bn" in xs:
            refresh(_xi(blocks, "bn"), bn[1], "text")
        elif "bn" not in seen["x"]:
            pos = next((i + 1 for i, b in reversed(list(enumerate(blocks))) if (_bo(b) or {}).get("k") == "_head"), 0)
            if add(pos, bn):
                seen["x"].extend(["bnh", "bn"])
    # Markierungen (+ eigene Notiz darunter), je Kapitel
    new_ch = {}                                # Kapitel, die neu entstehen -> Liste der Bloecke (in Buchreihenfolge)
    pending = {}                               # Kapitel vorhanden: Einfuegestelle -> Bloecke
    for a in anns:
        k = keyof(a)
        q = _quote(a, k, lang)
        nb = _note(a, k) if a["note"] and a["note"].strip() else None
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
            if chapter_page or k in new_ch:
                hd = []
            elif a["chapter"]:
                hd = [_chapter(a["chapter"], k)]
            else:
                hd = [_section(lang, "Ohne Kapitel", "mh", k)] if "mh" not in xs and "mh" not in seen["x"] else []
                if hd:
                    seen["x"].append("mh")
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


def _put_list(blocks, items, x, k, xseen, at, head=None):
    """Saga-Liste (Kapitel auf der Buchseite, Buecher auf der Sammelseite) einsetzen bzw. nachziehen -> (bloecke, geaendert).
    Unveraendert (h passt): neu aufbauen. Von der Person bearbeitet: nur Eintraege mit neuem Ziel (/notes#id) anfuegen
    (add_new: oben bei den Buechern, sonst unten). Geloescht (x in xseen, Block weg): nicht wieder anlegen."""
    i = _xi(blocks, x)
    if i is not None:
        b = blocks[i]
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") != "list" or not isinstance(d.get("items"), list):
            return blocks, False
        if _bo(b).get("h") == _hl(d["items"]):
            if _hl(items) == _bo(b)["h"]:
                return blocks, False
            nb = dict(b, data=dict(d, items=items, bo=dict(_bo(b), h=_hl(items))))
            return blocks[:i] + [nb] + blocks[i + 1:], True
        have = json.dumps(d["items"])
        flat = []

        def walk(xs):
            for it in xs:
                flat.append(it)
                walk(it.get("items") or [])
        walk(items)
        ids = set(re.findall(r"/notes#(\d+)", have))
        new = [dict(it, items=[]) for it in flat if (m := re.search(r"/notes#(\d+)", it["content"])) and m.group(1) not in ids]
        if not new:
            return blocks, False
        its = (new + d["items"]) if x == "books" else (d["items"] + new)
        return blocks[:i] + [dict(b, data=dict(d, items=its))] + blocks[i + 1:], True
    if x in xseen or not items:
        return blocks, False
    xseen.append(x)
    add = ([head] if head else []) + [_list(items, x, k)]
    return blocks[:at] + add + blocks[at:], True


# --- Zuordnung (user_kv) ----------------------------------------------------------------------------------------------
def _kv_get(c, uid, bid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, _KV + str(bid))).fetchone()
    try:
        v = json.loads(r[0]) if r else None
    except (TypeError, ValueError):
        v = None
    v = v if isinstance(v, dict) else {}
    out = {"page": _pid(v.get("page")), "v": 2 if v.get("v") == 2 else 1}
    for k in ("a", "n", "b"):
        out[k] = [x for x in (v.get(k) if isinstance(v.get(k), list) else []) if _int(x) is not None][: ANN_MAX * 4]
    out["x"] = [x for x in (v.get("x") if isinstance(v.get("x"), list) else []) if isinstance(x, str)][:20]
    ch = v.get("ch") if isinstance(v.get("ch"), dict) else {}
    out["new"] = [x for x in (v.get("new") if isinstance(v.get("new"), list) else []) if isinstance(x, str)][: CH_MAX * 3]
    out["ch"] = {k: p for k, p in list(ch.items())[: CH_MAX * 3] if isinstance(k, str) and re.fullmatch(r"c[0-9a-f]{12}", k) and _pid(p)}
    return out


def _kv_put(c, uid, bid, v):
    c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?,?,?)", (uid, _KV + str(bid), json.dumps(v, separators=(",", ":"))))


def _maps(c, uid):
    """-> {bookId: kv} fuer alle Zuordnungen des Kontos (nur gelesen, ohne Pruefung der Seiten)."""
    out = {}
    for k, v in c.execute("SELECT k, v FROM user_kv WHERE uid=? AND k LIKE ?", (uid, _KV + "%")).fetchall():
        try:
            d = json.loads(v)
        except (TypeError, ValueError):
            continue
        if isinstance(d, dict) and k[len(_KV):].isdigit():
            out[int(k[len(_KV):])] = d
    return out


def _alive(c, uid, ids):
    ids = [i for i in ids if _pid(i)]
    if not ids:
        return set()
    return {r[0] for r in c.execute(f"SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND id IN ({','.join('?' * len(ids))})",
                                    (uid, *ids)).fetchall()}


def _pages_of(c, uid):
    """bookId -> page_id (Buchseite) fuer alle zugeordneten, noch vorhandenen eigenen Seiten."""
    ids = {b: d.get("page") for b, d in _maps(c, uid).items() if _pid(d.get("page"))}
    alive = _alive(c, uid, list(ids.values()))
    return {b: p for b, p in ids.items() if p in alive}


def _book_of_page(c, uid, nid):
    """Buch zu einer Buch- oder Kapitelseite (oder None)."""
    for b, d in _maps(c, uid).items():
        ch = d.get("ch") if isinstance(d.get("ch"), dict) else {}
        if d.get("page") == nid or nid in ch.values():
            return b
    return None


def _page_ok(c, uid, nid):
    return nid and c.execute("SELECT 1 FROM note WHERE id=? AND uid=? AND deleted_at IS NULL AND kind='page'", (nid, uid)).fetchone()


# --- Seiten lesen/schreiben ---------------------------------------------------------------------------------------------
def _read(uid, nid):
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
    return blocks, r[2]


def _apply(uid, nid, fn, state):
    """Seite lesen, fn(bloecke) -> (neue bloecke | None, ergebnis), mit Revision speichern; Konflikt -> neu lesen (state =
    veraenderliche Merkliste, wird dann zurueckgesetzt). -> ergebnis."""
    from saga.api_v1 import _relay
    from saga.api_v1 import Fail as ApiFail
    from saga.pages import _Reply, _note_update
    for attempt in range(3):
        blocks, rev = _read(uid, nid)
        snap = json.loads(json.dumps(state))
        out, res = fn(blocks)
        if out is None:
            return res
        try:
            try:
                _relay(_note_update(uid, nid, {"body": json.dumps({"blocks": out}, ensure_ascii=False), "format": "blocks", "rev": rev}))
            except _Reply as rp:
                _relay(rp.resp)
        except ApiFail as f:
            if f.status == 409 and attempt < 2:      # dazwischen gespeichert: neu lesen und noch einmal zusammenfuehren
                state.clear()
                state.update(snap)
                continue
            raise Fail(f.status if f.status < 500 else 502, str(f))
        return res
    raise Fail(409, t("Seite wurde gerade geändert – bitte noch einmal versuchen"))


def _create(uid, title, icon, parent):
    from saga.api_v1 import _relay
    from saga.api_v1 import Fail as ApiFail
    from saga.pages import note_create
    try:
        return _relay(note_create(uid, {"title": title[:200], "icon": icon, "parent_id": parent, "cover": None}))["id"]
    except ApiFail as f:
        raise Fail(f.status if f.status < 500 else 502, str(f))


def _first(uid, nid, parent):
    """Neue Buchseite als erste Unterseite der Sammelseite (steht sie schon vorne, passiert nichts)."""
    from saga.pages import note_move
    sib = _siblings(uid, parent)
    if sib and sib[0] != nid:
        try:
            note_move(uid, nid, {"parent_id": parent, "index": 0})
        except Exception:  # noqa: BLE001 - Reihenfolge ist Beiwerk
            pass


def _trash(uid, nid):
    from saga.pages import note_trash
    try:
        note_trash(uid, nid)
    except Exception:  # noqa: BLE001 - Aufraeumen nach Fehler
        pass


def _order_after(uid, nid, parent, prev):
    """Neue Kapitelseite in Buchreihenfolge einsortieren: direkt nach prev (vorheriges Kapitel derselben Ebene) bzw. als
    erste, wenn es kein vorheriges gibt. Steht sie schon richtig, passiert nichts."""
    from saga.pages import note_move
    with _db() as c:
        sib = [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND parent_id=? "
                                       "ORDER BY sort NULLS FIRST, created, id", (uid, parent)).fetchall()]
    if nid not in sib:
        return
    rest = [x for x in sib if x != nid]
    want = rest.index(prev) + 1 if prev in rest else 0
    if sib.index(nid) != want:
        try:
            note_move(uid, nid, {"parent_id": parent, "index": want})
        except Exception:  # noqa: BLE001 - Reihenfolge ist Beiwerk
            pass


# --- Sammelseite "Buecher" ----------------------------------------------------------------------------------------------
def _home_get(c, uid):
    r = c.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, _KV_HOME)).fetchone()
    try:
        v = json.loads(r[0]) if r else {}
    except (TypeError, ValueError):
        v = {}
    v = v if isinstance(v, dict) else {}
    return {"page": _pid(v.get("page")), "moved": v.get("moved") is True,
            "x": [x for x in (v.get("x") if isinstance(v.get("x"), list) else []) if isinstance(x, str)][:10],
            "listed": [x for x in (v.get("listed") if isinstance(v.get("listed"), list) else []) if _pid(x)][-3000:]}


def _home(uid, lang, make=True):
    """Sammelseite (eigene, nicht geloeschte Seite) -> id bzw. None. Fehlt sie und make: neu anlegen (oberste Ebene).
    Einmalig: Buchseiten, die noch auf oberster Ebene liegen, wandern hinein. Danach Liste der Buchseiten nachziehen."""
    from saga.api_v1 import _relay
    from saga.pages import note_move
    with _db() as lk, lk:
        lk.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"bo-home:{uid}",))
        with _db() as c:
            v = _home_get(c, uid)
            hid = v["page"] if _page_ok(c, uid, v["page"]) else None
            pages = _pages_of(c, uid)
        made = False
        if hid is None:
            if not make:
                return None
            hid = _create(uid, t_lang(lang, "Bücher##Sammelseite"), ICON_HOME, None)
            v.update(page=hid, x=[], listed=[])
            made = True
        if not v["moved"]:
            with _db() as c:
                loose = [p for p in pages.values() if p != hid and c.execute(
                    "SELECT 1 FROM note WHERE id=? AND uid=? AND parent_id IS NULL AND deleted_at IS NULL", (p, uid)).fetchone()]
            for p in sorted(loose):
                try:
                    _relay(note_move(uid, p, {"parent_id": hid, "index": 0}))
                except Exception:  # noqa: BLE001 - Aufraeumen ist Beiwerk, die Seite bleibt dann oben
                    pass
            v["moved"] = True
        try:
            _home_sync(uid, hid, v, lang, made)
        except Fail:
            pass                                  # Liste ist Beiwerk (z. B. Seite gesperrt)
        with _db() as c, c:
            c.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?,?,?)", (uid, _KV_HOME, json.dumps(v, separators=(",", ":"))))
        return hid


INTRO_OLD = "Deine Bücher aus BookOrbit: je Buch eine Seite mit Kapiteln, Markierungen und Lesezeichen. Neue Bücher kommen oben dazu."
INTRO = "Deine Bücher aus BookOrbit: je Buch eine Seite mit Kapiteln, Markierungen und Lesezeichen. Neue Bücher kommen vorne dazu."


def _intro(lang, key=INTRO):
    return _san(_gray(_esc(t_lang(lang, key), quote=False)))


def _gallery():
    """Unterseiten-Block (saga.page_widgets, Art subpages): Buchseiten als Cover-Galerie (Cover = erstes Bild der Buchseite)."""
    return {"type": "widget", "data": {"v": 1, "kind": "subpages", "size": "m", "cfg": {"view": "gallery", "image": "content"}}}


def _has_gallery(blocks):
    from saga.notes import _iter_blocks
    try:
        return any(b.get("type") == "widget" and isinstance(b.get("data"), dict) and b["data"].get("kind") == "subpages"
                   for b in _iter_blocks(blocks))
    except ValueError:
        return True                                   # zu tief verschachtelt: lieber nichts einfuegen


def _home_sync(uid, hid, v, lang, made):
    """Inhalt der Sammelseite (2.5, 9.11. abends): Einleitung + Unterseiten-Block (Galerie, live aus dem Seitenbaum) statt
    der frueheren Liste. Einmalig ("gal" in x): unveraenderte Liste (h passt) -> durch den Block ersetzt; bearbeitete Liste
    bleibt stehen (gehoert jetzt der Person, wird nicht mehr nachgezogen), der Block kommt davor; hatte die Person die Liste
    geloescht, kommt auch kein Block. Danach: geloeschter Block kommt nicht wieder. listed wird nicht mehr gebraucht."""
    def fn(blocks):
        xs = v["x"]
        ch = False
        if made and "intro" not in xs:
            text = _intro(lang)
            blocks = [{"type": "paragraph", "data": {"text": text, "bo": {"k": "_home", "x": "intro", "h": _hn(text)}}}] + list(blocks)
            xs.append("intro")
            ch = True
        ii = _xi(blocks, "intro")
        if ii is not None and not made:               # alte Einleitung ("oben dazu"), unveraendert -> neuer Text
            b = blocks[ii]
            old = _intro(lang, INTRO_OLD)
            if _bo(b).get("h") == _hn((b.get("data") or {}).get("text") or "") and _hn(old) == _bo(b).get("h"):
                text = _intro(lang)
                blocks = blocks[:ii] + [dict(b, data=dict(b["data"], text=text, bo=dict(_bo(b), h=_hn(text))))] + blocks[ii + 1:]
                ch = True
        if "gal" in xs:
            return (blocks if ch else None), None
        xs.append("gal")
        v["listed"] = []
        if _has_gallery(blocks):                      # schon da (z. B. selbst eingefuegt)
            return blocks, None
        i = _xi(blocks, "books")
        if i is not None:
            b = blocks[i]
            d = b.get("data") if isinstance(b.get("data"), dict) else {}
            if b.get("type") == "list" and _bo(b).get("h") == _hl(d.get("items")):
                return blocks[:i] + [_gallery()] + blocks[i + 1:], None      # unveraenderte Liste -> Block
            return blocks[:i] + [_gallery()] + blocks[i:], None              # bearbeitet: bleibt, Block davor
        if "books" in xs:                             # Liste hatte die Person geloescht: keinen Block aufdraengen
            return (blocks if ch else None), None
        at = (_xi(blocks, "intro") + 1) if _xi(blocks, "intro") is not None else len(blocks)
        return blocks[:at] + [_gallery()] + blocks[at:], None

    st = dict(v)
    before = list(v["x"])
    try:
        _apply(uid, hid, fn, v)
    except Fail:
        v.clear()
        v.update(st, x=before)                         # nichts gespeichert: naechstes Mal noch einmal
        raise
    if "gal" in v["x"] and "gal" not in before:        # einmalig: Buecher neueste zuerst (nur, wenn nie umsortiert)
        _newest_first(uid, hid)


def _siblings(uid, parent):
    with _db() as c:
        return [r[0] for r in c.execute("SELECT id FROM note WHERE uid=? AND deleted_at IS NULL AND parent_id=? "
                                        "ORDER BY sort NULLS FIRST, created", (uid, parent)).fetchall()]


def _newest_first(uid, hid):
    """Buchseiten unter der Sammelseite: frueher kamen neue Buecher ans Ende des Seitenbaums (die Liste zeigte sie trotzdem
    zuerst). Stehen sie noch genau in Anlege-Reihenfolge (nie selbst umsortiert), tauschen sie die Plaetze in umgekehrter
    Reihenfolge - eigene Seiten dazwischen behalten ihren Platz. Sonst bleibt alles, wie es ist."""
    from saga.pages import note_move
    with _db() as c:
        books = set(_pages_of(c, uid).values())
        sib = _siblings(uid, hid)
        mine = [x for x in sib if x in books]
        made = dict(c.execute("SELECT id, created FROM note WHERE id = ANY(?)", (mine,)).fetchall()) if mine else {}
    if len(mine) < 2 or mine != sorted(mine, key=lambda x: (made.get(x) or 0, x)):
        return
    rev = iter(sorted(mine, key=lambda x: (made.get(x) or 0, x), reverse=True))
    want = [next(rev) if x in books else x for x in sib]
    for k, nid in enumerate(want):
        cur = _siblings(uid, hid)
        if k < len(cur) and cur[k] == nid:
            continue
        try:
            note_move(uid, nid, {"parent_id": hid, "index": k})
        except Exception:  # noqa: BLE001 - Reihenfolge ist Beiwerk
            return


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


def _legacy_segments(blocks):
    """Alte Einseiten-Notizseite: je H2-Kapitel (data.bo.c, Schluessel k) ein Abschnitt -> [(k, label, start, ende)].
    Regel: ein Abschnitt reicht von seiner Ueberschrift bis vor den naechsten Saga-Block eines anderen Kapitels (bzw. die
    naechste Kapitel-Ueberschrift); eigene Bloecke darin wandern mit. Nur beim letzten Abschnitt endet er nach dem letzten
    Saga-Block des Kapitels - eigene Bloecke dahinter (Schlussgedanken zum Buch) bleiben auf der Buchseite."""
    segs, i = [], 0
    while i < len(blocks):
        m = _bo(blocks[i])
        if m and m.get("c") and blocks[i].get("type") == "header" and not m.get("x") and isinstance(m.get("k"), str) and m["k"] != "c0":
            k, j = m["k"], i + 1
            while j < len(blocks):
                mj = _bo(blocks[j])
                if mj and (mj.get("c") or mj.get("k") != k):
                    break
                j += 1
            segs.append([k, _line(_norm(blocks[i]["data"].get("text")), CHAPTER_MAX) or "…", i, j])
            i = j
        else:
            i += 1
    if segs:
        k, _, i, j = segs[-1]
        segs[-1][3] = max([x + 1 for x in range(i + 1, j) if (_bo(blocks[x]) or {}).get("k") == k] or [i + 1])
    return [tuple(x) for x in segs]


def _sig(b):
    """Inhalt eines Blocks fuer den Abgleich - so, wie der Server ihn speichern wuerde (clean_blocks), damit Bereinigen beim
    Speichern nicht als Aenderung zaehlt."""
    from saga.pages import clean_blocks
    try:
        c = json.loads(clean_blocks(json.dumps({"blocks": [b]}, ensure_ascii=False))[0]).get("blocks") or []
        b = c[0] if c and isinstance(c[0], dict) else b
    except Exception:  # noqa: BLE001 - dann roh vergleichen
        pass
    return json.dumps([b.get("type"), b.get("data")], sort_keys=True, ensure_ascii=False)


def _has(blocks, b):
    """Steht Block b (aus der alten Seite) schon in blocks? Nur bei gleichem Inhalt (Art + Daten) - die Block-ID allein reicht
    nicht: hat die Person den Block nach dem Kopieren noch geaendert, wird er erneut kopiert (lieber doppelt als verloren)."""
    sig = _sig(b)
    return any(isinstance(x, dict) and _sig(x) == sig for x in blocks)


_STEP = None                                     # Testhaken (tests/bookorbit_seiten.py): _STEP(name) darf werfen = Abbruch


def _step(name):
    if _STEP:
        _STEP(name)


def build(uid, book_id=None, parent_id=None, page_id=None):
    """-> dict fuer die Antwort. Fehler: Fail."""
    link = _need_link(uid)
    with _db() as c:
        if page_id is not None:                         # "Aus BookOrbit aktualisieren" auf der Seite: Buch ueber die Zuordnung
            book_id = _book_of_page(c, uid, page_id)
            if book_id is None or not _alive(c, uid, [page_id]):
                raise Fail(404, t("Diese Seite stammt nicht aus BookOrbit"))
        if parent_id is not None and not _page_ok(c, uid, parent_id):
            raise Fail(404, t("Zielseite nicht gefunden (nur eigene Seiten)"))
    lang = account_lang(uid)
    with _ulock(uid):
        book = _book(uid, book_id)
        anns, total, cut = _annotations(uid, book_id)
        bms = _soft(_bookmarks, uid, book_id) or []
        toc, alias = _soft(_toc, uid, book_id) or ([], {})
        base = link[0]
        # Sperre ueber Prozesse hinweg (je Konto und Buch): Zuordnung lesen, Seiten anlegen, zusammenfuehren und Zuordnung
        # schreiben laufen nacheinander - zwei gleichzeitige Aufrufe legen nie zwei Seiten an. Die Sperre haelt die offene
        # Transaktion dieser eigenen Verbindung bis zum Ende des Blocks (Muster wie saga/contacts.py).
        with _db() as lk, lk:
            lk.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (f"bo:{uid}:{book_id}",))
            try:
                res = _build_locked(uid, book_id, parent_id, book, anns, bms, toc, alias, base, lang)
            except Fail:
                raise
            except Exception as ex:  # noqa: BLE001 - nie ein halber Stand ohne Antwort; der naechste Lauf macht weiter
                import logging
                logging.getLogger("saga").exception("bookorbit build")
                raise Fail(502, t("Unerwarteter Fehler: {detail}", detail=type(ex).__name__))
    try:
        _home(uid, lang)                                # Liste der Sammelseite nachziehen (eigene Sperre)
    except Fail:
        pass
    full = res.pop("_full")
    res.update(book_id=book_id, total=total, truncated=bool(cut or full), url=f"/notes#{res['page_id']}", ok=True)
    return res


def _build_locked(uid, book_id, parent_id, book, anns, bms, toc, alias, base, lang):
    with _db() as c:
        kv = _kv_get(c, uid, book_id)
        nid = kv["page"] if _page_ok(c, uid, kv["page"]) else None
    created = False
    if nid is None:
        kv = {"page": None, "v": 2, "a": [], "n": [], "b": [], "x": [], "ch": {}, "new": []}
        home = parent_id is None
        if home:                                # 2.5 (9.11.): nicht mehr auf oberster Ebene, sondern in "Buecher"
            parent_id = _home(uid, lang)
        nid = _create(uid, book["title"] or t_lang(lang, "Unbenannt"), ICON_BOOK, parent_id)
        kv["page"] = nid
        created = True
        if home and parent_id:                  # neue Buecher vorne (Unterseiten-Galerie zeigt die Baum-Reihenfolge)
            _first(uid, nid, parent_id)
    seen = {k: list(kv[k]) for k in ("a", "n", "b", "x")}
    title = book["title"] or t_lang(lang, "Unbenannt")
    try:
        return _build_pages(uid, book_id, nid, created, kv, seen, book, anns, bms, toc, alias, base, lang, title)
    except BaseException:
        if created:                              # neue Buchseite (ohne fremden Inhalt) samt Kapitelseiten wieder weg
            _trash(uid, nid)
        raise


def _kv_save(uid, book_id, nid, seen, kv):
    with _db() as c, c:
        _kv_put(c, uid, book_id, dict(seen, page=nid, ch=kv["ch"], v=kv["v"], new=kv["new"]))


def _build_pages(uid, book_id, nid, created, kv, seen, book, anns, bms, toc, alias, base, lang, title):
    """Ablauf (jeder Schritt wiederaufsetzbar, ein Abbruch erzeugt hoechstens Doppeltes, nie Verlust):
    1. Kapitel bestimmen (TOC, Markierungen, Abschnitte einer alten Einseiten-Seite, schon zugeordnete Kapitelseiten).
    2. Fehlende Kapitelseiten anlegen - Zuordnung je Seite sofort in user_kv ("new" = angelegt, noch nicht eingerichtet),
       ein Wiederanlauf verwendet sie weiter.
    3. Alte Einseiten-Seite (v != 2): erst kopieren - je Abschnitt die noch fehlenden Bloecke in die Kapitelseite -, dann
       schneiden: aus der Buchseite (mit Revision) nur Abschnitte entfernen, deren Bloecke alle in der Kapitelseite stehen.
       Erst wenn kein solcher Abschnitt mehr uebrig ist: v = 2.
    4. Markierungen je Kapitelseite, 5. Buchseite (Kopf, Notiz, Kapitel-Liste, Ohne Kapitel, Lesezeichen)."""
    added = updated = 0
    full = False
    kv.setdefault("new", [])
    legacy = []
    migrate = kv["v"] != 2 and not created
    if migrate:
        legacy = [(k, lab) for k, lab, _, _ in _legacy_segments(_read(uid, nid)[0])]
    with _db() as c:
        alive = _alive(c, uid, list(kv["ch"].values()))
        titles = {r[0]: r[1] for r in c.execute(f"SELECT id, title FROM note WHERE id IN ({','.join('?' * len(alive)) or 'NULL'})",
                                                 tuple(alive)).fetchall()} if alive else {}
    chapters, alias, ch_cut = _chapters(toc, alias, anns, legacy, set(kv["ch"]))
    keys = {e["key"] for e in chapters}
    for k, p in kv["ch"].items():                     # zugeordnete Kapitelseite ohne Kapitel (mehr): weiter fuehren
        if p in alive and k not in keys:
            chapters.append({"key": k, "label": _line(titles.get(p), CHAPTER_MAX) or "…", "depth": 1, "parent": None})
            keys.add(k)
    remap = {k: alias.get(lab.casefold(), k) for k, lab in legacy}
    # 2. Kapitelseiten: vorhandene behalten, geloeschte nicht wieder, neue in Buchreihenfolge anlegen
    pages = {k: p for k, p in kv["ch"].items() if p in alive}
    new_pages = set()
    prev_of = {}
    for e in chapters:
        k = e["key"]
        parent = pages.get(e["parent"]) if e["depth"] == 2 and e["parent"] else None
        parent = parent or nid
        if k in pages or k in kv["ch"]:              # vorhanden bzw. von der Person geloescht
            prev_of[parent] = pages.get(k, prev_of.get(parent))
            continue
        p = _create(uid, _short(e["label"], CH_TITLE_MAX) or "…", ICON_CH, parent)
        pages[k] = kv["ch"][k] = p
        kv["new"] = [x for x in kv["new"] if x != k] + [k]
        _kv_save(uid, book_id, nid, seen, kv)          # sofort merken: ein Wiederanlauf legt sie nicht noch einmal an
        new_pages.add(k)
        _step("created")
        if not created:
            _order_after(uid, p, parent, prev_of.get(parent))
        prev_of[parent] = p
    fresh_keys = set(kv["new"]) & set(pages)
    order = [e for e in chapters if e["key"] in pages]
    pos = {e["key"]: i + 1 for i, e in enumerate(order)}
    label = {e["key"]: e["label"] for e in chapters}

    def nav_of(e):
        part = None
        if e["depth"] == 2 and e["parent"] in pages:
            part = (pages[e["parent"]], _short(label[e["parent"]], CH_TITLE_MAX))
        return _nav(nid, title, part, pos[e["key"]], len(order), lang)
    entry = {e["key"]: e for e in order}
    # 3. Alte Einseiten-Seite: erst kopieren, dann schneiden
    if migrate:
        book_blocks = _read(uid, nid)[0]
        for k0, lab, i, j in _legacy_segments(book_blocks):
            k = remap.get(k0, k0)
            if k not in pages:
                continue                               # kein Kapitelseite (Obergrenze/geloescht): Abschnitt bleibt
            seg = [b for b in book_blocks[i + 1:j] if isinstance(b, dict)]

            def copy(blocks, seg=seg, k=k):
                miss = [b for b in seg if not _has(blocks, b)]
                if not miss:
                    return None, None
                if not blocks:
                    return [nav_of(entry[k])] + miss, None
                at = _xi(blocks, "back")
                at = 0 if at is None else at + 1
                return blocks[:at] + miss + blocks[at:], None
            _apply(uid, pages[k], copy, {})
            _step("copied")
        left = []

        def cut(blocks):
            left.clear()
            segs = _legacy_segments(blocks)
            out, last, drop = [], 0, False
            for k0, lab, i, j in segs:
                k = remap.get(k0, k0)
                if k not in pages:
                    continue
                target = _read(uid, pages[k])[0]
                if not all(_has(target, b) for b in blocks[i + 1:j] if isinstance(b, dict)):
                    left.append(k)                     # (noch) nicht vollstaendig kopiert: Abschnitt bleibt diesmal
                    continue
                out.extend(blocks[last:i])
                last, drop = j, True
            out.extend(blocks[last:])
            return (out if drop else None), None
        _apply(uid, nid, cut, {})
        _step("cut")
        if not left:
            kv["v"] = 2
            _kv_save(uid, book_id, nid, seen, kv)
    # 4. Markierungen je Kapitelseite
    keyof = lambda a: alias.get(a["chapter"].casefold()) if a["chapter"] else None   # noqa: E731
    by_k, rest = {}, []
    for a in anns:
        k = keyof(a)
        if k and k in pages:
            by_k.setdefault(k, []).append(a)
        elif k and k in kv["ch"]:
            continue                                   # Kapitelseite geloescht: Markierungen dieses Kapitels nicht mehr
        else:
            rest.append(a)
    counts = {}
    for e in order:
        k, p = e["key"], pages[e["key"]]
        nav = nav_of(e)
        mine = by_k.get(k, [])

        def fn(blocks, k=k, nav=nav, mine=mine, fresh=k in fresh_keys):
            nonlocal added, updated, full
            ch = False
            blocks = list(blocks)
            if fresh and not blocks:
                blocks = [nav]
                ch = True
            else:
                i = _xi(blocks, "back")
                if i is not None and _bo(blocks[i]).get("h") == _hn(blocks[i]["data"].get("text")) and _bo(blocks[i]).get("h") != nav["data"]["bo"]["h"]:
                    blocks[i] = dict(blocks[i], data=dict(blocks[i]["data"], text=nav["data"]["text"], bo=nav["data"]["bo"]))
                    ch = True
            todo = [a for a in mine if a["id"] not in seen["a"]]
            j = _xi(blocks, "ph")
            if todo and j is not None and _bo(blocks[j]).get("h") == _hn(blocks[j]["data"].get("text")):
                del blocks[j]                      # Platzhalter (unveraendert) weicht der ersten Markierung
                ch = True
            out, a1, u1, f1 = merge(blocks, mine, [], None, seen, None, lang, keyof=lambda a, k=k: k, chapter_page=True)
            if fresh and not _quotes_on(out) and _xi(out, "ph") is None and not any(not _bo(b) for b in out):
                i = _xi(out, "back")
                out.insert(0 if i is None else i + 1, _placeholder(lang))
                ch = True
            added, updated, full = added + a1, updated + u1, full or f1
            counts[k] = _quotes_on(out)
            return (out if ch or a1 or u1 else None), None
        try:
            _apply(uid, p, fn, seen)
        except Fail as f:
            if f.status not in (403, 404, 423):       # gesperrt/weg: diese Kapitelseite auslassen
                raise
            counts.setdefault(k, 0)
        if k in kv["new"]:
            kv["new"] = [x for x in kv["new"] if x != k]
    # 5. Buchseite: Kopf, Notiz zum Buch, Kapitel-Liste, Markierungen ohne Kapitel, Lesezeichen
    items, by = [], {}
    for e in order:
        n = counts.get(e["key"], 0)
        html = _link(pages[e["key"]], _short(e["label"], CH_TITLE_MAX))
        if n:
            html += _gray(_esc(" · " + t_lang(lang, "{n} {n:Markierung|Markierungen}", n=n), quote=False))
        it = {"content": _san(html), "meta": {}, "items": []}
        if e["depth"] == 2 and e["parent"] in by:
            by[e["parent"]]["items"].append(it)
        else:
            items.append(it)
            by[e["key"]] = it
    head = _head(base, book_id, book, _store_cover(uid, book_id), lang) if created else None

    def book_fn(blocks):
        nonlocal added, updated, full
        out, a1, u1, f1 = merge(blocks, rest, bms, book, seen, head, lang)
        at = next((i + 1 for i, b in reversed(list(enumerate(out))) if (_bo(b) or {}).get("k") == "_head"), 0)
        out, ch = _put_list(out, items, "chl", "_ch", seen["x"], at, head=_section(lang, "Kapitel##Buchseite", "chh", "_ch"))
        added, updated, full = added + a1, updated + u1, full or f1
        return (out if a1 or u1 or ch or created else None), None

    _apply(uid, nid, book_fn, seen)
    _kv_save(uid, book_id, nid, seen, kv)
    return {"page_id": nid, "created": created, "added": added, "updated": updated, "chapters": len(order),
            "chapters_added": len(new_pages), "_full": full or ch_cut}


# --- Buecherliste (Einstellungen) ---------------------------------------------------------------------------------------
def _row(bid, title, author, count, pages, read_at=None):
    out = {"id": bid, "title": _line(title, 300) or None, "author": _line(author, 200) or None, "count": count,
           "cover": f"/api/bookorbit/cover/{bid}", "page_id": pages.get(bid)}
    if read_at is not None:
        out["read_at"] = read_at
    return out


def _authors(au):
    return ", ".join(_line(a, 120) for a in au[:5] if isinstance(a, str)) if isinstance(au, list) else ""


def _read_books(uid, q):
    """Fertig gelesene Buecher (readStatus "read"), zuletzt beendet zuerst, hoechstens BOOKS_LIST_MAX."""
    body = {"filter": {"type": "group", "join": "AND", "rules": [{"type": "rule", "field": "readStatus", "operator": "includesAny",
                                                                    "value": ["read"]}]},
            "sort": [{"field": "finishedAt", "dir": "desc"}], "pagination": {"page": 0, "size": BOOKS_LIST_MAX}, "collapseSeries": False}
    if q:
        body["q"] = q[:200]
    st, d = _get(uid, "/books/query", 4 * 1024 * 1024, "POST", body)
    if st != 200 or not isinstance(d, dict) or not isinstance(d.get("items"), list):
        raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
    return d["items"][:BOOKS_LIST_MAX]


def books(uid, q=None, flt=None):
    _need_link(uid)
    qs = f"?status=active&limit={BOOKS_LIST_MAX}" + (f"&q={quote(q)}" if q else "")
    st, d = _get(uid, "/annotations/books" + qs)
    if st != 200 or not isinstance(d, list):
        raise Fail(502, t("BookOrbit gerade nicht erreichbar."), "unavailable")
    with _db() as c:
        pages = _pages_of(c, uid)
    counts, out = {}, []
    for x in d[:BOOKS_LIST_MAX]:
        bid = _int(x.get("bookId")) if isinstance(x, dict) else None
        if bid is None:
            continue
        n = x.get("count")
        counts[bid] = n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0
        out.append(_row(bid, x.get("bookTitle"), x.get("author"), counts[bid], pages))
    full = len(d) >= BOOKS_LIST_MAX                    # Liste voll: Anzahl anderer Buecher unbekannt (null)
    if flt == "read":
        res = []
        for x in _read_books(uid, q):
            bid = _int(x.get("id")) if isinstance(x, dict) else None
            if bid is None:
                continue
            rs = x.get("readStatus") if isinstance(x.get("readStatus"), dict) else {}
            fin = rs.get("finishedAt") if isinstance(rs.get("finishedAt"), str) and re.fullmatch(r"[0-9T:.\-+Z ]{10,40}", rs["finishedAt"]) else None
            res.append(_row(bid, x.get("title"), _authors(x.get("authors")), counts.get(bid, None if full else 0), pages, fin or ""))
        return {"state": "ok", "filter": "read", "books": res}
    if q:                                       # 2.5 (9.11.): mit Suchbegriff auch Buecher ohne Markierungen (ganze Bibliothek)
        have = {b["id"] for b in out}
        r = _soft(_get, uid, f"/books/search?q={quote(q)}&limit={SEARCH_MAX}")
        rows = r[1] if r and r[0] == 200 else None
        rows = rows.get("books") if isinstance(rows, dict) else rows
        for x in (rows if isinstance(rows, list) else [])[:SEARCH_MAX]:
            bid = _int(x.get("id")) if isinstance(x, dict) else None
            if bid is None or bid in have:
                continue
            have.add(bid)
            out.append(_row(bid, x.get("title"), _authors(x.get("authors")), None if full else 0, pages))
    else:                                       # ohne Suche: lose Buchseiten von frueher einmalig in die Sammelseite;
        try:                                    # 2.5 (9.11. abends): Sammelseite einmalig auf die Galerie umstellen
            with _db() as c:
                hv = _home_get(c, uid)
            if pages and not hv["moved"]:
                _home(uid, account_lang(uid))
            elif pages and hv["page"] and "gal" not in hv["x"]:   # nur umstellen: selbst geloeschte Sammelseite nicht neu anlegen
                _home(uid, account_lang(uid), make=False)
        except Exception:  # noqa: BLE001
            pass
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
    flt = request.query_params.get("filter") or None
    if flt not in (None, "read"):
        return _bad(t("ungültige Angabe"))
    if not _rate_ok(uid, _lcalls, _RATE_LIST):
        return _bad(t("Zu viele Anfragen – bitte eine Minute warten."), 429)
    from starlette.concurrency import run_in_threadpool
    try:
        return JSONResponse(await run_in_threadpool(books, uid, q, flt), headers={"Cache-Control": "no-store"})
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
