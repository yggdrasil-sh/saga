"""Seiten veroeffentlichen: oeffentliche Adressen /@<name>/<slug> ohne Anmeldung, oeffentlicher Name je Konto,
Schnittstelle fuer Web und Apps, Startseite fuer Besucher (vom Admin gewaehlt).

Grundsaetze:
 - Jeder Fehlerfall (Name oder Seite unbekannt, nicht veroeffentlicht, geloescht, archiviert, Konto gesperrt,
   Veroeffentlichen abgeschaltet, falsche Unterseite, fremdes Bild) liefert DIESELBE neutrale 404-Seite.
   So laesst sich nicht ausprobieren, ob es einen Namen oder eine Seite gibt.
 - Die Seite wird serverseitig als HTML gerendert, ohne JavaScript (CSP ohne Skripte). Inline-HTML laeuft beim
   Ausgeben noch einmal durch eine strenge Positivliste (gespeichert ist es schon bereinigt - doppelt haelt besser).
 - Unterseiten nur mit children=1 und nur, wenn sie ueber parent_id wirklich unter der veroeffentlichten Seite
   liegen: gleiches Konto, die ganze Kette weder geloescht noch archiviert.
 - Bilder nur, wenn genau diese Seite (Inhalt, Titelbild, Symbol) sie benutzt.
 - Verweise auf andere Seiten werden zu oeffentlichen Links, wenn das Ziel ebenfalls oeffentlich erreichbar ist -
   sonst zu reinem Text. Aufgaben, Kalender, Immich, Schaltflaechen und Dateianhaenge entfallen.
 - Geloeschte Seite: die Veroeffentlichung bleibt gespeichert, liefert aber 404; nach dem Wiederherstellen ist sie
   wieder da. Beim endgueltigen Loeschen (notes._purge_ids) und beim Loeschen des Kontos verschwindet sie.
"""
import importlib
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from html import escape
from html.parser import HTMLParser
from urllib.parse import quote, urlparse
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
import psycopg
import auth
from db import connect as _db
from saga import avatars, bus, i18n, nextcloud
from saga.core import (ASSET_V, BASE_URL, E, PUBLIC_HOSTS, TZ, _api_guard, _bad, _body, _json_load, seen_lang, server_lang, t, title,
                       tmpl, user_lang)
from saga.files import _thumb, _thumb_mime, _thumb_w
from saga.notes import FILES_DIR, MAX_NEST, PALETTE, STYLE_TYPES, _FILE_URL, _calc_value, _clean_views, _iter_blocks, note_chain
from saga.sharing import access, edit_shared

router = APIRouter()

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")         # oeffentlicher Name: 2-32 Zeichen, klein
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,59}")         # Adresse der Seite: 2-60 Zeichen, klein
NAME_MAX, SLUG_MAX = 32, 60
# Gesperrte Namen: Pfade von Saga, technische Namen und solche, die nach "offiziell" aussehen
RESERVED = frozenset("""
admin administrator api app apps static login logout register registration settings p i18n healthz health well-known
saga root system files file notes note pages page manifest favicon robots sitemap help support about info www mail
email public user users me home index search status auth oauth sso oidc ldap pair handoff docs doc assets account
accounts null undefined none anonymous guest owner webmaster postmaster hostmaster security abuse noreply no-reply
hlidskjalf yggdrasil official staff team moderator mod calendar tasks bookmarks share
""".split())
PROJECT_URL = {"de": "https://yggdrasil-sh.github.io/saga/", "en": "https://yggdrasil-sh.github.io/saga/en/",
               "es": "https://yggdrasil-sh.github.io/saga/es/"}
PUBLIC_RATE = max(10, int(E.get("PUBLIC_RATE", "120")))   # Seitenaufrufe je Minute und Adresse (Bilder: das Fuenffache)
DB_ROWS_MAX = 500                                         # Eintraege einer Datenbank auf der oeffentlichen Seite
DB_BLOCKS_MAX = 20                                        # Datenbank-Bloecke je oeffentlicher Seite

CSP = ("default-src 'none'; img-src 'self'; style-src 'self'; script-src 'none'; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
_FILE_H = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox",
           "Referrer-Policy": "no-referrer", "Cache-Control": "public, max-age=300", "Content-Disposition": "inline",
           "X-Robots-Tag": "noindex"}


# =====================================================================================================
# Einstellungen (Admin) und Namen
# =====================================================================================================
def enabled():
    """Admin -> Allgemein -> "Veroeffentlichen erlauben" (Standard: an)."""
    return bool(auth.settings().get("publish_enabled", True))


def public_home():
    try:
        return int(auth.settings().get("public_home") or 0)
    except (TypeError, ValueError):
        return 0


_TRANS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue", "æ": "ae", "Æ": "ae",
                        "ø": "o", "Ø": "o", "œ": "oe", "Œ": "oe", "ð": "d", "þ": "th", "ł": "l", "Ł": "l", "đ": "d"})


def slugify(s, maxlen=SLUG_MAX):
    """Text -> a-z0-9 und Bindestriche (ae/oe/ue/ss, Akzente weg), hoechstens maxlen Zeichen."""
    s = str(s or "").translate(_TRANS)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen].strip("-")


def name_problem(name, uid=None):
    """None = gueltig, sonst Fehlertext (Pruefung auf frei: Aufrufer). uid: der eigene Anmeldename ist als oeffentlicher
    Name nicht erlaubt (2.4, Web-Runde 2 H3) - er stuende sonst in oeffentlichen Adressen und bei anderen Saga-Servern."""
    if not NAME_RE.fullmatch(name or ""):
        return t("2–32 Zeichen: a–z, 0–9, - und _ (klein, am Anfang ein Buchstabe oder eine Ziffer).")
    if name in RESERVED:
        return t("Dieser Name ist reserviert.")
    if uid and name == str(uid).strip().lower():
        return t("Bitte nicht den Anmeldenamen verwenden – der öffentliche Name ist für alle sichtbar, auch für andere Saga-Server.")
    return None


def get_name(c, uid):
    r = c.execute("SELECT name FROM public_name WHERE uid=?", (uid,)).fetchone()
    return r[0] if r else None


def _unique(c, base, maxlen, taken):
    """base, sonst base-2, base-3 ... (gekuerzt auf maxlen), bis taken(x) False ist."""
    if not taken(base):
        return base
    for n in range(2, 10000):
        x = base[:maxlen - len(str(n)) - 1].rstrip("-_") + "-" + str(n)
        if not taken(x):
            return x
    return base[:maxlen - 8] + os.urandom(4).hex()


def suggest_name(c, uid):
    """Vorschlag aus dem ANZEIGENAMEN (nie der Anmeldename), bei Kollision mit Zahl."""
    r = c.execute("SELECT display_name FROM users WHERE username=?", (uid,)).fetchone()
    base = slugify(r[0] if r else "", NAME_MAX)
    if len(base) < 2 or base == uid or base in RESERVED or not NAME_RE.fullmatch(base):
        base = "autor" if i18n.get_lang() == "de" else "autora" if i18n.get_lang() == "es" else "author"
    return _unique(c, base, NAME_MAX, lambda x: x in RESERVED or bool(
        c.execute("SELECT 1 FROM public_name WHERE name=? AND uid<>?", (x, uid)).fetchone()) or _retired(c, x, uid))


def _retired(c, name, uid):
    """Name war schon ueber Instanzgrenzen bekannt (Foederation) und gehoerte einem anderen Konto: nie neu vergeben -
    sonst bekaeme das neue Konto die Freigaben anderer Instanzen an den alten Namen."""
    return bool(c.execute("SELECT 1 FROM fed_name_retired WHERE name=? AND (uid IS NULL OR uid<>?)", (name, uid)).fetchone())


def set_name(uid, name):
    """-> (name, None) oder (None, fehlertext). Eindeutig ueber alle Konten."""
    name = str(name or "").strip().lower()
    p = name_problem(name, uid)
    if p and not (name_problem(name) is None and _same_as_before(uid, name)):
        return None, p
    with _db() as c, c:
        if c.execute("SELECT 1 FROM public_name WHERE name=? AND uid<>?", (name, uid)).fetchone() or _retired(c, name, uid):
            return None, t("Dieser Name ist schon vergeben.")
        old = get_name(c, uid)
        if old is not None:
            c.execute("UPDATE public_name SET name=? WHERE uid=?", (name, uid))
        else:
            c.execute("INSERT INTO public_name (uid, name, created) VALUES (?,?,?)", (uid, name, time.time()))
        if old != name:              # Foederation: andere Instanzen schreiben auf den neuen Namen um (saga.federation, rename)
            importlib.import_module("saga.federation")          # Zuhoerer sicher angemeldet (auch ausserhalb der App, z. B. Werkzeuge)
            bus.fire("public_name.renamed", c, uid=uid, old=old, new=name)
    return name, None


def _same_as_before(uid, name):
    """Bisheriger (frueher selbst gewaehlter) Name bleibt speicherbar, auch wenn er der Anmeldename ist."""
    with _db() as c:
        return get_name(c, uid) == name


def _url(name, slug, nid=None):
    return f"/@{name}/{slug}" + (f"/{nid}" if nid else "")


# =====================================================================================================
# Rate-Limit fuer oeffentliche Pfade (im Speicher, je Adresse; fester Minuten-Takt)
# =====================================================================================================
class _Rate:
    def __init__(self):
        self.hits, self.lock = {}, threading.Lock()

    def allow(self, key, limit, window=60.0):
        now = time.monotonic()
        with self.lock:
            start, n = self.hits.get(key, (now, 0))
            if now - start >= window:
                start, n = now, 0
            if len(self.hits) > 20000:
                self.hits.clear()
            self.hits[key] = (start, n + 1)
            return n + 1 <= limit


_rate = _Rate()


def _client(request):
    return (request.client.host if request.client else "") or "?"


# =====================================================================================================
# Inline-HTML: strenge Positivliste fuer die oeffentliche Ausgabe
# =====================================================================================================
_INLINE = {"b": "strong", "strong": "strong", "i": "em", "em": "em", "u": "u", "s": "s", "mark": "mark", "code": "code",
           "a": "a", "time": "time", "br": "br"}
_TIME_ATTR = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")
_NOTE_HREF = re.compile(r"/notes#([1-9][0-9]{0,17})")
EXT_REL = "noopener noreferrer nofollow"


def ext_href(href):
    """Externer Link: nur http, https, mailto -> bereinigte Adresse oder None."""
    href = str(href or "").strip()
    try:
        u = urlparse(href)
    except ValueError:
        return None
    if u.scheme not in ("http", "https", "mailto") or (u.scheme != "mailto" and not u.netloc):
        return None
    return href


class _PubSanitizer(HTMLParser):
    """Erlaubt: fett, kursiv, unterstrichen, durchgestrichen, Markierung, Code, Link, Datum, Zeilenumbruch.
    Attribute werden NEU geschrieben (nie uebernommen). Links auf Seiten: ueber link(note_id) -> URL oder None."""

    def __init__(self, link):
        super().__init__(convert_charrefs=True)
        self.link, self.out, self.stack, self.skip = link, [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        out_tag = _INLINE.get(tag)
        if not out_tag:
            return
        if out_tag == "br":
            self.out.append("<br>")
            return
        a, keep = dict(attrs), ""
        if out_tag == "a":
            href = (a.get("href") or "").strip()
            m = _NOTE_HREF.fullmatch(href)
            url = self.link(int(m.group(1))) if m else ext_href(href)
            if not url:                              # Ziel nicht oeffentlich / unerlaubtes Schema: nur der Text bleibt
                self.stack.append(("a", False))
                return
            keep = f' href="{escape(url, quote=True)}"' + ("" if m else f' rel="{EXT_REL}" target="_blank"')
        elif out_tag == "time":
            dt = (a.get("datetime") or "").strip()
            keep = f' datetime="{dt}"' if _TIME_ATTR.fullmatch(dt) else ""
        self.out.append(f"<{out_tag}{keep}>")
        self.stack.append((out_tag, True))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        out_tag = _INLINE.get(tag)
        if not out_tag or out_tag == "br" or not any(x[0] == out_tag for x in self.stack):
            return
        while self.stack:
            x, emit = self.stack.pop()
            if emit:
                self.out.append(f"</{x}>")
            if x == out_tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self):
        while self.stack:
            x, emit = self.stack.pop()
            if emit:
                self.out.append(f"</{x}>")
        return "".join(self.out)


def inline(s, link=lambda _nid: None):
    p = _PubSanitizer(link)
    p.feed(str(s or ""))
    p.close()
    return p.result()


def _plain(s):
    """Inline-HTML -> reiner Text (fuer Titel, alt-Texte)."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", inline(s))).strip()


# =====================================================================================================
# Zugriff: Name -> Konto, Veroeffentlichung, Kette ueber parent_id
# =====================================================================================================
PUB_COLS = "note_id, slug, children, indexable, profile, created, updated"


def _pub_dict(r):
    return {"note_id": r[0], "slug": r[1], "children": bool(r[2]), "indexable": bool(r[3]), "profile": bool(r[4]),
            "created": r[5], "updated": r[6]}


def _owner_by_name(c, name):
    if not NAME_RE.fullmatch(name or ""):
        return None
    r = c.execute("SELECT p.uid FROM public_name p JOIN users u ON u.username=p.uid WHERE p.name=? AND u.disabled=0",
                  (name,)).fetchone()
    return r[0] if r else None


def _chain(c, uid, nid):
    """Ids von nid aufwaerts bis zur obersten Seite - endet VOR der ersten geloeschten/archivierten Seite oder Vorlage.
    nid selbst geloescht/archiviert/Vorlage/fremd -> []. (Gemeinsam mit dem Teilen: notes.note_chain)"""
    return note_chain(c, uid, nid)


class Ctx:
    """Eine oeffentliche Seite im Aufbau: Konto, Name, Veroeffentlichung, Links, benutzte Bilder."""

    def __init__(self, c, uid, name, pub, base):
        self.c, self.uid, self.name, self.pub, self.base = c, uid, name, pub, base
        self.pubs = {r[0]: _pub_dict(r) for r in c.execute(f"SELECT {PUB_COLS} FROM note_publish WHERE uid=? AND active=1", (uid,))}
        self._chains, self._urls, self.images, self.n_db = {}, {}, set(), 0

    def chain(self, nid):
        if nid not in self._chains:
            if len(self._chains) > 2000:
                return []
            self._chains[nid] = _chain(self.c, self.uid, nid)
        return self._chains[nid]

    def seed(self, nid, parent):
        """nid ist ein aktives (weder geloeschtes noch archiviertes) Kind von parent: Kette ohne Abfrage ableiten."""
        if nid not in self._chains:
            self._chains[nid] = [nid] + self.chain(parent)

    def allowed(self, nid):
        """Darf nid unter DIESER Veroeffentlichung gezeigt werden (die Seite selbst oder erlaubte Unterseite)?"""
        ch = self.chain(nid)
        root = self.pub["note_id"]
        return bool(ch) and (ch[0] == root or (self.pub["children"] and root in ch))

    def url_for(self, nid):
        """Oeffentliche Adresse einer Seite desselben Kontos oder None (dann wird ein Verweis zu Text)."""
        if nid in self._urls:
            return self._urls[nid]
        url, ch = None, self.chain(nid)
        root = self.pub["note_id"]
        if ch:
            if ch[0] == root:
                url = _url(self.name, self.pub["slug"])
            elif self.pub["children"] and root in ch:
                url = _url(self.name, self.pub["slug"], nid)
            elif nid in self.pubs:
                url = _url(self.name, self.pubs[nid]["slug"])
            else:
                for anc in ch[1:]:
                    p = self.pubs.get(anc)
                    if p and p["children"]:
                        url = _url(self.name, p["slug"], nid)
                        break
        self._urls[nid] = url
        return url

    def img(self, src, w=1280):
        """/files/<id> (eigenes Bild des Kontos) -> Adresse unter dieser Seite, sonst None."""
        m = _FILE_URL.fullmatch(str(src or ""))
        if not m or not self.c.execute("SELECT 1 FROM file WHERE id=? AND uid=? AND is_image=1", (m.group(1), self.uid)).fetchone():
            return None
        self.images.add(m.group(1))
        return f"{self.base}/f/{m.group(1)}?w={w}"


# =====================================================================================================
# Bloecke -> HTML (alles escaped; Inline-HTML ueber inline())
# =====================================================================================================
_LIST_TYPE = {"lower-roman": "i", "upper-roman": "I", "lower-alpha": "a", "upper-alpha": "A"}
_EMOJI_OK = re.compile(r"[^<>&\"'\s]{1,8}")
_CALLOUT = ["💡", "⚠️", "✅", "❗", "ℹ️", "🔥", "📌"]           # wie CALLOUT_EMOJI in static/pages.js (Farbe je Art)


def _day(iso):
    """'2026-10-14' -> Datum im Format der Sprache (wie i18n.fmt_date)."""
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso)
    return i18n.day_str(d)


_DIM = {}                       # Bild-ID -> (Breite, Hoehe) oder None; nur der Dateikopf wird gelesen


def _img_size(fid):
    """Pixelmasse eines eigenen Bildes (fuer srcset/width/height), zwischengespeichert. Nicht lesbar -> None."""
    if fid not in _DIM:
        if len(_DIM) > 5000:
            _DIM.clear()
        try:
            from PIL import Image
            with Image.open(os.path.join(FILES_DIR, fid)) as im:          # liest nur den Kopf, dekodiert nichts
                _DIM[fid] = (int(im.size[0]), int(im.size[1]))
        except Exception:  # noqa: BLE001 - kein Rasterbild, zu gross, kaputt: dann ohne Masse
            _DIM[fid] = None
    return _DIM[fid]


def _blocks(ctx, blocks, depth=0):
    if depth > MAX_NEST or not isinstance(blocks, list):
        return ""
    out = []
    for b in blocks:
        if isinstance(b, dict) and isinstance(b.get("data"), dict):
            try:
                out.append(_styled(_block(ctx, b.get("type"), b["data"], depth), b))
            except (TypeError, ValueError, AttributeError, KeyError):
                continue                            # kaputter Block: weglassen statt Fehlerseite
    return "\n".join(x for x in out if x)


_FIRST_TAG = re.compile(r"<([a-z][a-z0-9]*)((?:\s[^<>]*)?)>")


def _styled(html, b):
    """Block-Farbe (2.5, G3, tunes.hkstyle): Klasse c-<farbe> bzw. bg-<farbe> am ersten Element des Blocks. Nur Werte
    der Palette (der Server hat sie beim Speichern schon bereinigt - hier trotzdem pruefen: aeltere Daten)."""
    st = (b.get("tunes") or {}).get("hkstyle") if isinstance(b.get("tunes"), dict) else None
    if not html or b.get("type") not in STYLE_TYPES or not isinstance(st, dict) or st.get("c") not in PALETTE:
        return html
    return _add_cls(html, ("bg-" if st.get("bg") is True else "c-") + st["c"])


def _add_cls(html, cls):
    """Klasse(n) am ersten Element ergaenzen (cls: nur feste Klassennamen, nie Eingaben)."""
    m = _FIRST_TAG.match(html)
    if not m:
        return html
    attrs = m.group(2)
    if ' class="' in attrs:
        attrs = attrs.replace(' class="', f' class="{cls} ', 1)
    else:
        attrs += f' class="{cls}"'
    return f"<{m.group(1)}{attrs}>" + html[m.end():]


def _size_cls(d, stretched=False):
    """Breite/Ausrichtung von Bild und Player (2.5, G2) als Klassen - die CSP erlaubt keine style-Attribute.
    iw-NN = NN % der Inhaltsbreite (10-100, 5er-Schritte), al-left|al-center|al-right. Volle Breite (stretched) gewinnt."""
    out = []
    w = d.get("width")
    if not stretched and isinstance(w, (int, float)) and not isinstance(w, bool) and w == w:
        out += ["iw", "iw-%d" % (round(min(100, max(10, w)) / 5) * 5)]      # Klassen in 5-%-Schritten (static/public.css)
    if d.get("align") in ("left", "center", "right") and not stretched:
        out.append("al-" + d["align"])
    return " ".join(out)


def _col_cls(cols):
    """Spaltenbreiten (2.5, G1): alle Spalten mit Gewicht -> Klassen pw-NN (Anteil in %, 15-85, 5er-Schritte) und fw am Rahmen.
    Sonst gleich breit wie bisher (None)."""
    ws = [c.get("w") for c in cols]
    if len(cols) < 2 or not all(isinstance(w, (int, float)) and not isinstance(w, bool) and w == w and w > 0 for w in ws):
        return None
    tot = sum(ws)
    return ["pw-%d" % (round(min(85, max(15, w / tot * 100)) / 5) * 5) for w in ws]


def _items(ctx, items, style, depth):
    if depth > MAX_NEST or not isinstance(items, list) or not items:
        return ""
    tag = "ol" if style == "ordered" else "ul"
    out = [f"<{tag}" + (' class="pub-check"' if style == "checklist" else "") + ">"]
    for it in items:
        if isinstance(it, str):
            it = {"content": it, "items": []}
        if not isinstance(it, dict):
            continue
        txt = inline(it.get("content", ""), ctx.url_for)
        sub = _items(ctx, it.get("items"), style, depth + 1)
        if style == "checklist":
            on = bool((it.get("meta") or {}).get("checked")) if isinstance(it.get("meta"), dict) else False
            box = (f'<input type="checkbox" disabled{" checked" if on else ""} aria-disabled="true" '
                   f'aria-label="{escape(t("Erledigt") if on else t("Offen"))}">')
            out.append(("<li class=\"done\">" if on else "<li>") + f"{box}<span>{txt}</span>{sub}</li>")
        else:
            out.append(f"<li>{txt}{sub}</li>")
    out.append(f"</{tag}>")
    return "".join(out)


def _block(ctx, typ, d, depth):
    if typ == "paragraph":
        txt = inline(d.get("text", ""), ctx.url_for)
        return f"<p>{txt}</p>" if txt.strip() else '<p class="pub-gap"></p>'
    if typ == "header":
        try:
            lvl = min(max(int(d.get("level", 2)), 1), 4) + 1          # h1 ist der Seitentitel
        except (TypeError, ValueError):
            lvl = 3
        return f"<h{lvl}>{inline(d.get('text', ''), ctx.url_for)}</h{lvl}>"
    if typ == "list":
        style = d.get("style") if d.get("style") in ("ordered", "checklist") else "unordered"
        html = _items(ctx, d.get("items"), style, depth)
        meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
        if style == "ordered" and html:
            attrs = ""
            try:
                start = int(meta.get("start") or 1)
                if 1 < start < 100000:
                    attrs += f' start="{start}"'
            except (TypeError, ValueError):
                pass
            if meta.get("counterType") in _LIST_TYPE:
                attrs += f' type="{_LIST_TYPE[meta["counterType"]]}"'
            html = "<ol" + attrs + html[3:]
        return html
    if typ == "quote":
        cap = inline(d.get("caption", ""), ctx.url_for)
        cls = ' class="center"' if d.get("alignment") == "center" else ""
        return (f"<blockquote{cls}><p>{inline(d.get('text', ''), ctx.url_for)}</p>"
                + (f"<footer>{cap}</footer>" if cap.strip() else "") + "</blockquote>")
    if typ == "code":
        lang = re.sub(r"[^a-z0-9+#-]", "", str(d.get("language") or "").lower())[:20]
        lab = f'<span class="pub-code-lang">{escape(lang)}</span>' if lang and lang != "plain" else ""
        return f'<div class="pub-code">{lab}<pre><code>{escape(str(d.get("code", "")), quote=False)}</code></pre></div>'
    if typ == "delimiter":
        return "<hr>"
    if typ == "image":
        src = ((d.get("file") or {}) if isinstance(d.get("file"), dict) else {}).get("url", "")
        if not ctx.img(src):
            return ""
        fid = _FILE_URL.fullmatch(src).group(1)
        cap = inline(d.get("caption", ""), ctx.url_for)
        cls = " ".join(c for c, k in (("border", "withBorder"), ("bg", "withBackground"), ("wide", "stretched")) if d.get(k))
        sz = _size_cls(d, bool(d.get("stretched")))
        cls = (cls + " " + sz).strip() if sz else cls
        base = f"{ctx.base}/f/{fid}"
        # srcset mit den WIRKLICHEN Breiten (kleinere Bilder liefert ?w= unveraendert) und width/height: sonst rechnet
        # der Browser ein 900 px breites Bild mit "1280w" kleiner, als es ist (bzw. vergroessert kleine Bilder)
        dim = _img_size(fid)
        srcset, seen = [], set()
        for cw in (640, 1280, 2000):
            dw = min(cw, dim[0]) if dim else cw
            if dw not in seen:
                seen.add(dw)
                srcset.append(f"{base}?w={cw} {dw}w")
        wh = f' width="{dim[0]}" height="{dim[1]}"' if dim else ""
        return (f'<figure class="pub-img {cls}"><img src="{base}?w=1280" srcset="{", ".join(srcset)}" '
                f'sizes="(max-width: 1032px) 100vw, 992px"{wh} alt="{escape(_plain(d.get("caption", "")))}" '
                f'loading="lazy">' + (f"<figcaption>{cap}</figcaption>" if cap.strip() else "") + "</figure>")
    if typ == "table":
        rows = [r for r in (d.get("content") or []) if isinstance(r, list)][:500]
        if not rows:
            return ""
        out, head = ['<div class="pub-tablewrap"><table class="pub-table">'], bool(d.get("withHeadings"))
        for i, r in enumerate(rows):
            cell = "th" if head and i == 0 else "td"
            out.append("<tr>" + "".join(f"<{cell}>{inline(str(x), ctx.url_for)}</{cell}>" for x in r[:50]) + "</tr>")
        out.append("</table></div>")
        return "".join(out)
    if typ == "columns":
        cols = [c for c in (d.get("cols") or []) if isinstance(c, dict)][:6]
        inner = [_blocks(ctx, c.get("blocks"), depth + 1) for c in cols]
        if not any(inner):
            return ""
        pw = _col_cls(cols)
        if pw:
            return (f'<div class="pub-cols n{len(cols)} fw">'
                    + "".join(f'<div class="pub-col {w}">{x}</div>' for w, x in zip(pw, inner)) + "</div>")
        return f'<div class="pub-cols n{len(cols)}">' + "".join(f'<div class="pub-col">{x}</div>' for x in inner) + "</div>"
    if typ == "callout":
        emo = str(d.get("emoji") or "💡")
        emo = emo if _EMOJI_OK.fullmatch(emo) else "💡"
        kind = _CALLOUT.index(emo) if emo in _CALLOUT else 0
        return (f'<aside class="pub-callout k{kind}"><span class="pub-callout-ic" aria-hidden="true">{escape(emo)}</span>'
                f"<div>{inline(d.get('text', ''), ctx.url_for)}</div></aside>")
    if typ == "toggle":
        body = _blocks(ctx, d["blocks"], depth + 1) if isinstance(d.get("blocks"), list) else (
            f"<p>{inline(d.get('text', ''), ctx.url_for)}</p>" if d.get("text") else "")
        op = " open" if d.get("open") is not False else ""
        summ = inline(d.get("title", ""), ctx.url_for) or "…"
        lv = d.get("level")
        if isinstance(lv, int) and not isinstance(lv, bool) and 1 <= lv <= 3:       # 2.5 (G10): Toggle-Ueberschrift
            summ = f"<h{lv + 1}>{summ}</h{lv + 1}>"                                    # h1 ist der Seitentitel (wie header)
            return f'<details class="pub-toggle th"{op}><summary>{summ}</summary>{body}</details>'
        return f'<details class="pub-toggle"{op}><summary>{summ}</summary>{body}</details>'
    if typ == "embed":
        url = str(d.get("url") or "").replace("&amp;", "&")
        if not re.fullmatch(r"https?://\S+", url) or not ext_href(url):
            return ""
        host = urlparse(url).hostname or url
        name = _plain(d.get("title", "")) or (("YouTube" if d.get("kind") == "youtube" else host.removeprefix("www.")))
        ic = "▶" if d.get("kind") == "youtube" else "🔗"
        sz = _size_cls(d) if d.get("kind") == "youtube" else ""       # 2.5 (G2): nur Player, Link-Karten unveraendert
        return (f'<p class="pub-embed{" " + sz if sz else ""}"><a href="{escape(url, quote=True)}" rel="{EXT_REL}" target="_blank">'
                f'<span aria-hidden="true">{ic}</span> <span>{escape(name)}</span> <small>{escape(host.removeprefix("www."))}</small></a></p>')
    if typ == "nextcloud":
        return _nc_block(ctx, d)
    if typ == "database":
        try:
            dbid = int(d.get("db"))
        except (TypeError, ValueError):
            return ""
        return _database(ctx, dbid, d.get("views") if isinstance(d.get("views"), list) else None, d.get("view"),
                         caption=not d.get("hide_title"))
    return ""          # unbekannt, privat oder interaktiv (Aufgaben, Kalender, Immich, Schaltflaechen, Dateien): weglassen


def _nc_block(ctx, d):
    """Nextcloud-Datei: nur, wenn der Autor "auf veroeffentlichter Seite zeigen" eingeschaltet hat (und die Signatur fuer
    diese Seite stimmt). Vorschau/Download ueber die Adresse dieser Seite - nie die Adresse der Nextcloud."""
    nid = getattr(ctx, "nid", None)
    if not nid or d.get("public") is not True or not nextcloud.sig_ok(nid, d):
        return ""
    fid, author = str(d.get("fileid") or ""), str(d.get("author") or "")
    if not nextcloud.FILEID_RE.match(fid):
        return ""
    name = _plain(d.get("name", "")) or t("Nextcloud-Datei")
    q = f"a={quote(author, safe='')}"
    base = f"{ctx.base}/nc/{fid}"
    if d.get("kind") == "image":
        return (f'<figure class="pub-img"><img src="{escape(base)}?{q}&amp;w=1280" alt="{escape(name)}" loading="lazy">'
                f"<figcaption>{escape(name)}</figcaption></figure>")
    if d.get("kind") == "folder":
        return f'<p class="pub-embed pub-nc"><span><span aria-hidden="true">📁</span> <span>{escape(name)}</span></span></p>'
    return (f'<p class="pub-embed pub-nc"><a href="{escape(base)}?{q}&amp;dl=1" rel="nofollow" download>'
            f'<span aria-hidden="true">📄</span> <span>{escape(name)}</span> <small>{escape(t("Herunterladen"))}</small></a></p>')


# =====================================================================================================
# Datenbanken: nur lesend, als einfache Tabelle (Titel + sichtbare Eigenschaften der Ansicht)
# =====================================================================================================
def _iso_ts(ts):
    try:
        return date.fromtimestamp(float(ts)).isoformat() if ts else ""
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _prop_value(p, row):
    if p["type"] == "calc":
        return _calc_value(p, row["props"])
    if p["type"] == "created":
        return row["created"]
    if p["type"] == "edited":
        return row["updated"]
    return row["props"].get(p["id"])


def _rel_date(op, iso):
    """Relative Datumsfilter wie im Editor ("diese Woche" = Mo-So)."""
    try:
        x = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return False
    a = date.today()
    if op == "this_month":
        a = a.replace(day=1)
        b = (a + timedelta(days=32)).replace(day=1)
    elif op == "this_week":
        a = a - timedelta(days=a.weekday())
        b = a + timedelta(days=7)
    else:
        b = a + timedelta(days=1)
    return a <= x < b


def _match(p, f, row):
    """Filter einer Ansicht - gleiche Bedeutung wie matchFilter() in static/pages.js."""
    v = row["title"] if f["pid"] == "title" else _prop_value(p, row)
    if p["type"] in ("created", "edited") and v:
        v = _iso_ts(v)
    empty = v is None or v == "" or v is False or (isinstance(v, list) and not v)
    op, fv = f["op"], f.get("value")
    if op == "is_empty":
        return empty
    if op == "not_empty":
        return not empty
    if op == "checked":
        return bool(v)
    if op == "unchecked":
        return not v
    if op in ("today", "this_week", "this_month"):
        return not empty and _rel_date(op, v)
    if fv in ("", None):
        return True                                   # unvollstaendiger Filter zaehlt nicht
    try:
        if p["type"] == "relation":
            fv = int(fv)
        if op in ("contains", "not_contains"):
            hit = (fv in v) if isinstance(v, list) else str(fv).lower() in str(v or "").lower()
            return hit if op == "contains" else not hit
        if op == "is":
            return v == fv
        if op == "is_not":
            return v != fv
        if op == "eq":
            return (v is not None and float(v) == float(fv)) if p["type"] in ("number", "calc") else v == fv
        if op == "gt":
            return v is not None and float(v) > float(fv)
        if op == "lt":
            return v is not None and float(v) < float(fv)
        if op == "before":
            return bool(v) and str(v) < str(fv)
        if op == "after":
            return bool(v) and str(v) > str(fv)
    except (TypeError, ValueError):
        return False
    return True


def _sort_key(p, row):
    v = row["title"] if p is None else _prop_value(p, row)
    if isinstance(v, list):
        v = len(v)
    if v is None or v == "":
        return (1, 0, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (0, float(v), "")
    return (0, 0, str(v).lower())


def _fmt_prop(ctx, p, v):
    """Wert einer Eigenschaft -> HTML (escaped). Relationen nur auf oeffentlich erreichbare Eintraege."""
    if v is None or v == "" or v == []:
        return ""
    typ = p["type"]
    if typ in ("number", "calc"):
        if isinstance(v, float):
            v = round(v, 2)
            v = int(v) if v == int(v) else v
        s = f"{v} %" if p.get("fmt") == "percent" else f"{v} €" if p.get("fmt") == "euro" else str(v)
        return escape(s)
    if typ in ("select", "multi_select"):
        opts = {o["id"]: o for o in p.get("options", [])}
        chips = [opts[x] for x in (v if isinstance(v, list) else [v]) if x in opts]
        return " ".join(f'<span class="opt opt-{escape(o.get("color", "gray"))}">{escape(o["name"])}</span>' for o in chips)
    if typ == "date":
        return escape(_day(v))
    if typ in ("created", "edited"):
        return escape(i18n.fmt_date(v, TZ))
    if typ == "checkbox":
        return "✓" if v else ""
    if typ == "url":
        u = ext_href(v)
        if not u:
            return escape(str(v))
        shown = re.sub(r"^(https?://|mailto:)(www\.)?", "", u)[:60]
        return f'<a href="{escape(u, quote=True)}" rel="{EXT_REL}" target="_blank">{escape(shown)}</a>'
    if typ == "relation":
        out = []
        for x in v if isinstance(v, list) else []:
            if isinstance(x, int) and not isinstance(x, bool):
                url = ctx.url_for(x)
                if url:
                    out.append(f'<a href="{escape(url, quote=True)}">{escape(_title_of(ctx, x))}</a>')
        return ", ".join(out)
    return escape(str(v))


def _title_of(ctx, nid):
    r = ctx.c.execute("SELECT title FROM note WHERE id=? AND uid=?", (nid, ctx.uid)).fetchone()
    return (r[0] if r else "") or t("Unbenannt")


def _visible_props(schema, view):
    props = [p for p in schema.get("props", []) if isinstance(p, dict) and p.get("id")]
    hidden = set(view.get("hidden") or [])
    order = [x for x in (view.get("order") or [])]
    by_id = {p["id"]: p for p in props}
    ordered = [by_id[x] for x in order if x in by_id] + [p for p in props if p["id"] not in order]
    return [p for p in ordered if p["id"] not in hidden]


def _database(ctx, dbid, views=None, view_id=None, caption=False):
    """Eintraege einer Datenbank (gleiches Konto, weder geloescht noch archiviert) als Tabelle."""
    ctx.n_db += 1
    if ctx.n_db > DB_BLOCKS_MAX:                # viele Datenbank-Bloecke auf einer Seite: Aufwand je Aufruf begrenzen
        return ""
    r = ctx.c.execute("SELECT title, icon, schema FROM note WHERE id=? AND uid=? AND kind='database' "
                      "AND deleted_at IS NULL AND archived IS NULL", (dbid, ctx.uid)).fetchone()
    if not r:
        return ""
    schema = _json_load(r[2], {"props": [], "views": []})
    if not isinstance(schema.get("props"), list):
        return ""
    try:
        vs = _clean_views(views, schema["props"]) if views else (schema.get("views") or [])
    except (ValueError, TypeError, AttributeError):
        vs = schema.get("views") or []
    view = next((v for v in vs if isinstance(v, dict) and v.get("id") == view_id), None) or (vs[0] if vs else {})
    cols = _visible_props(schema, view)
    rows = []
    for x in ctx.c.execute("SELECT id, title, props, created, updated FROM note WHERE uid=? AND parent_id=? "
                           "AND deleted_at IS NULL AND archived IS NULL AND is_template=0 ORDER BY sort NULLS FIRST, created LIMIT ?",
                           (ctx.uid, dbid, DB_ROWS_MAX)).fetchall():
        rows.append({"id": x[0], "title": x[1] or "", "props": _json_load(x[2], {}), "created": x[3], "updated": x[4]})
        ctx.seed(x[0], dbid)
    by_id = {p["id"]: p for p in schema["props"] if isinstance(p, dict)}
    for f in view.get("filters") or []:
        p = {"type": "text"} if f.get("pid") == "title" else by_id.get(f.get("pid"))
        if p:
            rows = [x for x in rows if _match(p, f, x)]
    for s in reversed(view.get("sorts") or []):
        p = None if s.get("pid") == "title" else by_id.get(s.get("pid"))
        if s.get("pid") != "title" and p is None:
            continue
        rows.sort(key=lambda x, p=p: _sort_key(p, x), reverse=s.get("dir") == "desc")
    tname = schema.get("title_name") or t("Name")
    out = ['<div class="pub-db">']
    if caption:
        ic = escape(r[1]) if r[1] and not str(r[1]).startswith("/files/") else ""
        out.append(f'<div class="pub-db-title">{ic + " " if ic else ""}{escape(r[0] or t("Unbenannt"))}</div>')
    if not rows:
        out.append(f'<p class="pub-muted">{escape(t("Keine Einträge."))}</p></div>')
        return "".join(out)
    out.append('<div class="pub-tablewrap"><table class="pub-table pub-dbt"><thead><tr>'
               f"<th>{escape(tname)}</th>" + "".join(f"<th>{escape(p.get('name', ''))}</th>" for p in cols) + "</tr></thead><tbody>")
    for x in rows:
        url = ctx.url_for(x["id"])
        name = escape(x["title"] or t("Unbenannt"))
        cell = f'<a href="{escape(url, quote=True)}">{name}</a>' if url else name
        out.append(f"<tr><td>{cell}</td>" + "".join(f"<td>{_fmt_prop(ctx, p, _prop_value(p, x))}</td>" for p in cols) + "</tr>")
    out.append("</tbody></table></div></div>")
    return "".join(out)


# =====================================================================================================
# Seite -> Vorlage templates/public.html
# =====================================================================================================
NOTE_COLS = "id, title, icon, body, format, kind, schema, props, cover, cover_pos, wide, parent_id, created, updated, font, small"


def _note(c, uid, nid):
    r = c.execute(f"SELECT {NOTE_COLS} FROM note WHERE id=? AND uid=? AND deleted_at IS NULL AND archived IS NULL "
                  "AND is_template=0", (nid, uid)).fetchone()
    return dict(zip([k.strip() for k in NOTE_COLS.split(",")], r)) if r else None


def _text_body(s):
    """Alte Klartext-Notiz -> Absaetze."""
    paras = [x.strip() for x in re.split(r"\n{2,}", str(s or "")) if x.strip()]
    return "\n".join("<p>" + escape(x, quote=False).replace("\n", "<br>") + "</p>" for x in paras)


def _icon_html(ctx, icon, cls, default=""):
    icon = str(icon or "") or default
    if icon.startswith("/files/"):
        src = ctx.img(icon, 160)
        return f'<img class="{cls}" src="{escape(src, quote=True)}" alt="">' if src else ""
    return f'<span class="{cls}" aria-hidden="true">{escape(icon)}</span>' if icon and _EMOJI_OK.fullmatch(icon) else ""


def render_page(ctx, row):
    """-> Werte fuer die Vorlage. ctx.base = Adresse dieser (Unter-)Seite (Bilder liegen darunter)."""
    ctx.nid = row["id"]                            # Nextcloud-Bloecke: Signatur gilt fuer genau diese Seite
    v = {"title": row["title"] or t("Unbenannt"), "wide": bool(row["wide"]), "props": [], "subpages": [], "crumbs": [],
         "font": row.get("font") if row.get("font") in ("serif", "mono") else "", "small": bool(row.get("small"))}   # 2.5 (G9)
    # Absenderzeile "@name · aktualisiert 14.10.2026" (Datum in der Sprache des Besuchers)
    upd = row["updated"] or row["created"]
    when = escape(i18n.fmt_date(upd, TZ)) if upd else ""
    iso = datetime.fromtimestamp(upd, TZ).date().isoformat() if upd else ""
    v["updated_html"] = escape(t("aktualisiert {date}")).replace("{date}", f'<time datetime="{iso}">{when}</time>') if upd else ""
    v["icon"] = _icon_html(ctx, row["icon"], "pub-icon")
    v["cover"] = ""
    if row["cover"]:
        src = ctx.img(row["cover"], 2000)
        if src:
            pos = 50.0 if row["cover_pos"] is None else float(row["cover_pos"])
            v["cover"] = src
            v["cover_cls"] = "cp-%d" % (round(max(0.0, min(100.0, pos)) / 10) * 10)
    # Datenbank-Zeile: Eigenschaften unter dem Titel (sichtbar wie in der ersten Ansicht der Datenbank)
    if row["parent_id"]:
        par = ctx.c.execute("SELECT kind, schema FROM note WHERE id=? AND uid=?", (row["parent_id"], ctx.uid)).fetchone()
        if par and par[0] == "database":
            schema = _json_load(par[1], {"props": [], "views": []})
            views = schema.get("views") or [{}]
            rowd = {"title": row["title"], "props": _json_load(row["props"], {}), "created": row["created"], "updated": row["updated"]}
            for p in _visible_props(schema, views[0] if isinstance(views[0], dict) else {}):
                val = _fmt_prop(ctx, p, _prop_value(p, rowd))
                if val:
                    v["props"].append((p.get("name", ""), val))
    # Inhalt
    if row["format"] == "blocks":
        doc = _json_load(row["body"], {})
        v["content"] = _blocks(ctx, doc.get("blocks") if isinstance(doc, dict) else [])
    else:
        v["content"] = _text_body(row["body"])
    if row["kind"] == "database":
        v["content"] += _database(ctx, row["id"])
    elif ctx.pub["children"]:
        for cid, ctitle, cicon in ctx.c.execute(
                "SELECT id, title, icon FROM note WHERE uid=? AND parent_id=? AND deleted_at IS NULL AND archived IS NULL "
                "AND is_template=0 ORDER BY sort NULLS FIRST, created LIMIT 200", (ctx.uid, row["id"])).fetchall():
            ctx.seed(cid, row["id"])
            url = ctx.url_for(cid)
            if url:
                base, ctx.base = ctx.base, url      # Bild-Symbol der Unterseite liegt unter IHRER Adresse (dort erlaubt)
                ic = _icon_html(ctx, cicon, "pub-sub-ic", "📄")
                ctx.base = base
                v["subpages"].append({"url": url, "title": ctitle or t("Unbenannt"), "icon": ic})
    # Pfad (nur Unterseiten): veroeffentlichte Seite > ... > diese Seite
    root = ctx.pub["note_id"]
    if row["id"] != root:
        ch = ctx.chain(row["id"])
        if root in ch:
            for anc in reversed(ch[1:ch.index(root) + 1]):
                v["crumbs"].append({"url": ctx.url_for(anc), "title": _title_of(ctx, anc)})
    return v


def _page_images(row):
    """Bilder, die genau diese Seite benutzt (Titelbild, Symbol, Bild-Bloecke - auch in Spalten/Ausklappbar)."""
    ids = set()
    for x in (row["cover"], row["icon"]):
        m = _FILE_URL.fullmatch(str(x or ""))
        if m:
            ids.add(m.group(1))
    if row["format"] == "blocks":
        doc = _json_load(row["body"], {})
        try:
            for b in _iter_blocks(doc.get("blocks") if isinstance(doc, dict) else []):
                if b.get("type") == "image":
                    f = (b.get("data") or {}).get("file") if isinstance(b.get("data"), dict) else None
                    m = _FILE_URL.fullmatch(str((f or {}).get("url", "") if isinstance(f, dict) else ""))
                    if m:
                        ids.add(m.group(1))
        except ValueError:
            pass
    return ids


# =====================================================================================================
# Antworten: Sprache des Besuchers, Kopfzeilen, neutrale 404
# =====================================================================================================
def _visitor_lang(request):
    """Oeffentliche Seiten: Sprache des Besuchers (Umschalter/Cookie, sonst Accept-Language), sonst Standardsprache
    des Servers."""
    return (i18n.norm(request.cookies.get(i18n.COOKIE)) or i18n.from_header(request.headers.get("accept-language", ""))
            or i18n.norm(auth.settings().get("default_lang")) or i18n.DEFAULT)


def _headers(indexable=False, cache="public, max-age=60"):
    h = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
         "Cache-Control": cache, "Vary": "Accept-Language"}
    if not indexable:
        h["X-Robots-Tag"] = "noindex, nofollow"
    return h


def _common():
    lang = i18n.get_lang()
    return {"v": ASSET_V, "site": title(), "project_url": PROJECT_URL.get(lang, PROJECT_URL["en"])}


def content_lang(uid):
    """Sprache des Inhalts (lang am Artikel): Kontosprache des Besitzers, sonst Standard des Servers, sonst die zuletzt
    gesehene Sprache seiner Weboberflaeche; unbekannt -> '' (dann gilt die Sprache des Besuchers)."""
    try:
        return user_lang(uid) or server_lang() or seen_lang(uid) or ""
    except Exception:  # noqa: BLE001 - Datenbank gerade weg: ohne eigene Angabe
        return ""


def not_found():
    """Neutrale 404 (fuer jeden Grund gleich) mit naechstem Schritt: zur Startseite fuer Besucher, falls gesetzt,
    sonst zur Anmeldung."""
    home = bool(enabled() and public_home())
    html = tmpl.get_template("public.html").render(mode="nf", indexable=False, next_home=home,
                                                   next_url="/" if home else BASE_URL + "/login", **_common())
    return HTMLResponse(html, status_code=404, headers=_headers(False, "no-store"))


def _too_many():
    html = tmpl.get_template("public.html").render(mode="busy", indexable=False, **_common())
    h = _headers(False, "no-store")
    h["Retry-After"] = "60"
    return HTMLResponse(html, status_code=429, headers=h)


def _cache(request, cache):
    """Mit Cookie (angemeldet): die SessionMiddleware haengt Set-Cookie an jede Antwort - so etwas darf kein geteilter
    Zwischenspeicher (Proxy/CDN) aufheben und anderen Besuchern ausliefern. Dann nur 'private'."""
    if not request.headers.get("cookie"):
        return cache
    return cache.replace("public", "private") if "public" in cache else "private, " + cache


def _canon_base(request):
    """Aufruf ueber eine eigene Domain aus PUBLIC_HOSTS: canonical auf diese Domain - sonst immer BASE_URL
    (der Host-Kopf entscheidet nur zwischen festen, vom Admin eingetragenen Namen)."""
    fwd = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    host = fwd or (request.headers.get("host") or "").lower()
    if host and host in PUBLIC_HOSTS:
        return urlparse(BASE_URL).scheme + "://" + host
    return BASE_URL


def _lower_redirect(request, name, slug=None, rest=""):
    """P10: Grossbuchstaben in Name/Adresse -> 301 auf die kleingeschriebene Adresse, wenn diese gueltig aussieht.
    Rein syntaktisch (ohne Datenbank) - verraet also nichts darueber, ob es Name oder Seite gibt."""
    ln, ls = name.lower(), (slug.lower() if slug is not None else None)
    if ln == name and ls == slug:
        return None
    if not NAME_RE.fullmatch(ln) or (ls is not None and not SLUG_RE.fullmatch(ls)):
        return None
    url = "/@" + ln + ("/" + ls if ls is not None else "") + rest
    return RedirectResponse(url + ("?" + request.url.query if request.url.query else ""), status_code=301)


def _page_response(request, ctx, row, home=False):
    v = render_page(ctx, row)
    idx = ctx.pub["indexable"]
    canon = _canon_base(request) + (ctx.base if not home else "/")
    html = tmpl.get_template("public.html").render(mode="page", page=v, indexable=idx, home=home, canonical=canon,
                                                   login_url=BASE_URL + "/login", owner=ctx.name, owner_avatar=avatars.public_url(ctx.c, ctx.uid),
                                                   content_lang=content_lang(ctx.uid), **_common())
    if home:                                     # "/" ist fuer Angemeldete das Dashboard: nie aus einem Zwischenspeicher
        h = _headers(idx, _cache(request, "no-cache"))
        h["Vary"] = "Accept-Language, Cookie"
        return HTMLResponse(html, headers=h)
    # Bughunt WV09: der angemeldete Eigentuemer (Vorschau aus der App) sieht Aenderungen sofort - sonst bis zu 60 s alt
    own = _session_uid(request) == ctx.uid
    h = _headers(idx, "private, no-cache" if own else _cache(request, "public, max-age=60"))
    if own:
        h["Vary"] = "Accept-Language, Cookie"
    return HTMLResponse(html, headers=h)


def _session_uid(request):
    try:
        return request.session.get("uid")
    except (AssertionError, AttributeError):       # ohne SessionMiddleware (eigene Domain ohne Sitzung)
        return None


class _Lang:
    """Sprache des Besuchers fuer die Dauer einer Anfrage setzen."""

    def __init__(self, request):
        self.request = request

    def __enter__(self):
        self.tok = i18n.set_lang(_visitor_lang(self.request))

    def __exit__(self, *a):
        i18n._cur.reset(self.tok)
        return False


def _limited(request, kind="p"):
    return not _rate.allow((kind, _client(request)), PUBLIC_RATE * (5 if kind == "f" else 1))


def _resolve(c, name, slug):
    """name + slug -> Ctx der veroeffentlichten Seite oder None (jeder Grund gleich behandelt)."""
    if not enabled() or not SLUG_RE.fullmatch(slug or ""):
        return None
    uid = _owner_by_name(c, name)
    if not uid:
        return None
    r = c.execute(f"SELECT {PUB_COLS} FROM note_publish WHERE uid=? AND slug=? AND active=1", (uid, slug)).fetchone()
    if not r:
        return None
    ctx = Ctx(c, uid, name, _pub_dict(r), _url(name, slug))
    return ctx if ctx.chain(ctx.pub["note_id"]) else None


def _sub_id(ctx, nid):
    """Unterseiten-ID aus der Adresse -> int, wenn erlaubt (children=1, wirklich darunter), sonst None."""
    if not re.fullmatch(r"[1-9][0-9]{0,17}", nid or "") or not ctx.pub["children"]:
        return None
    n = int(nid)
    if n == ctx.pub["note_id"] or not ctx.allowed(n):
        return None
    return n


# =====================================================================================================
# Oeffentliche Adressen (ohne Anmeldung)
# =====================================================================================================
GET_HEAD = ["GET", "HEAD"]                 # HEAD wie GET (Link-Vorschauen, Monitoring); FastAPI ergaenzt HEAD nicht selbst


@router.api_route("/@{name}", methods=GET_HEAD)
def public_profile(name: str, request: Request):
    """Profilseite (profile=1) - sonst eine schlichte Liste der oeffentlichen Seiten des Kontos."""
    if _limited(request):
        return _too_many()
    if (go := _lower_redirect(request, name)) is not None:
        return go
    with _Lang(request), _db() as c:
        if not enabled():
            return not_found()
        uid = _owner_by_name(c, name)
        if not uid:
            return not_found()
        r = c.execute(f"SELECT {PUB_COLS} FROM note_publish WHERE uid=? AND profile=1 AND active=1 ORDER BY updated DESC LIMIT 1", (uid,)).fetchone()
        if r:
            pub = _pub_dict(r)
            ctx = Ctx(c, uid, name, pub, _url(name, pub["slug"]))
            row = _note(c, uid, pub["note_id"])
            if row:
                return _page_response(request, ctx, row)
        items, ctx = [], None
        for p in [_pub_dict(x) for x in c.execute(f"SELECT {PUB_COLS} FROM note_publish WHERE uid=? AND active=1 ORDER BY created DESC LIMIT 500", (uid,))]:
            row = _note(c, uid, p["note_id"])
            if row:
                if ctx is None:                         # ein Ctx fuer alle (sonst je Eintrag alle Veroeffentlichungen laden)
                    ctx = Ctx(c, uid, name, p, "")
                ctx.pub, ctx.base = p, _url(name, p["slug"])
                items.append({"url": ctx.base, "title": row["title"] or t("Unbenannt"), "icon": _icon_html(ctx, row["icon"], "pub-sub-ic", "📄")})
        if not items:
            return not_found()
        html = tmpl.get_template("public.html").render(mode="list", items=items, owner=name, owner_avatar=avatars.public_url(c, uid),
                                                       indexable=False, **_common())
        return HTMLResponse(html, headers=_headers(False, _cache(request, "public, max-age=60")))


@router.api_route("/@{name}/{slug}", methods=GET_HEAD)
def public_page(name: str, slug: str, request: Request):
    if _limited(request):
        return _too_many()
    if (go := _lower_redirect(request, name, slug)) is not None:
        return go
    with _Lang(request), _db() as c:
        ctx = _resolve(c, name, slug)
        row = ctx and _note(c, ctx.uid, ctx.pub["note_id"])
        return _page_response(request, ctx, row) if row else not_found()


@router.api_route("/@{name}/{slug}/{nid}", methods=GET_HEAD)
def public_subpage(name: str, slug: str, nid: str, request: Request):
    if _limited(request):
        return _too_many()
    if (go := _lower_redirect(request, name, slug, "/" + nid)) is not None:
        return go
    with _Lang(request), _db() as c:
        ctx = _resolve(c, name, slug)
        sub = ctx and _sub_id(ctx, nid)
        row = sub and _note(c, ctx.uid, sub)
        if not row:
            return not_found()
        ctx.base = _url(name, slug, sub)
        return _page_response(request, ctx, row)


def _file(request, ctx, row, fid):
    if not row or not re.fullmatch(r"[0-9a-f]{24}", fid or "") or fid not in _page_images(row):
        return not_found()
    r = ctx.c.execute("SELECT mime FROM file WHERE id=? AND uid=? AND is_image=1", (fid, ctx.uid)).fetchone()
    path = os.path.join(FILES_DIR, fid)
    if not r or not os.path.isfile(path):
        return not_found()
    w = _thumb_w(request)
    tp = _thumb(fid, path, r[0], w) if w else None
    h = dict(_FILE_H, **{"Cache-Control": _cache(request, _FILE_H["Cache-Control"])})
    return FileResponse(tp, media_type=_thumb_mime(tp), headers=h) if tp else FileResponse(path, media_type=r[0], headers=h)


@router.api_route("/@{name}/{slug}/f/{fid}", methods=GET_HEAD)
def public_file(name: str, slug: str, fid: str, request: Request):
    """Bild der veroeffentlichten Seite - nur, wenn genau diese Seite es benutzt."""
    if _limited(request, "f"):
        return _too_many()
    with _Lang(request), _db() as c:
        ctx = _resolve(c, name, slug)
        return _file(request, ctx, ctx and _note(c, ctx.uid, ctx.pub["note_id"]), fid) if ctx else not_found()


@router.api_route("/@{name}/{slug}/{nid}/f/{fid}", methods=GET_HEAD)
def public_subfile(name: str, slug: str, nid: str, fid: str, request: Request):
    """Bild einer erlaubten Unterseite."""
    if _limited(request, "f"):
        return _too_many()
    with _Lang(request), _db() as c:
        ctx = _resolve(c, name, slug)
        sub = ctx and _sub_id(ctx, nid)
        return _file(request, ctx, _note(c, ctx.uid, sub), fid) if sub else not_found()


def _nc(request, name, slug, nid, fid):
    if _limited(request, "f"):
        return _too_many()
    q = request.query_params
    try:
        w, dl = int(q.get("w") or 0), q.get("dl") == "1"
    except ValueError:
        return not_found()
    with _Lang(request), _db() as c:
        ctx = _resolve(c, name, slug)
        if not ctx:
            return not_found()
        page = _sub_id(ctx, nid) if nid is not None else ctx.pub["note_id"]
        row = page and _note(c, ctx.uid, page)
        d, acct = nextcloud.locate(c, page, ctx.uid, fid, q.get("a") or "", "public") if row else (None, None)
    if not d or not acct:
        return not_found()
    return nextcloud.deliver(acct, d, "download" if dl else "preview", w, cache="public, max-age=300")


@router.api_route("/@{name}/{slug}/nc/{fid}", methods=GET_HEAD)
def public_nc(name: str, slug: str, fid: str, request: Request):
    """Nextcloud-Datei der veroeffentlichten Seite (nur Bloecke mit Schalter "auf veroeffentlichter Seite zeigen")."""
    return _nc(request, name, slug, None, fid)


@router.api_route("/@{name}/{slug}/{nid}/nc/{fid}", methods=GET_HEAD)
def public_subnc(name: str, slug: str, nid: str, fid: str, request: Request):
    return _nc(request, name, slug, nid, fid)


def home_response(request):
    """'/' ohne Anmeldung: die vom Admin gewaehlte Startseite (falls gesetzt, noch veroeffentlicht und Veroeffentlichen
    erlaubt) - sonst None (dann wie bisher zur Anmeldung)."""
    nid = public_home()
    if not nid or not enabled():
        return None
    if _limited(request):
        return _too_many()
    with _Lang(request), _db() as c:
        r = c.execute(f"SELECT {PUB_COLS}, uid FROM note_publish WHERE note_id=? AND active=1", (nid,)).fetchone()
        name = r and get_name(c, r[7])
        if not name or not _owner_by_name(c, name):
            return None
        pub = _pub_dict(r)
        ctx = Ctx(c, r[7], name, pub, _url(name, pub["slug"]))
        row = ctx.chain(nid) and _note(c, r[7], nid)
        return _page_response(request, ctx, row, home=True) if row else None


# =====================================================================================================
# Schnittstelle (Web und Apps): Sitzung + X-Saga, Schreiben nur mit Content-Type: application/json
# =====================================================================================================
def _json_only(request):
    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    return None if ct == "application/json" else _bad(t("ungültige Anfrage"), 415)


def _flag(v):
    return v is True or v == 1 or (isinstance(v, str) and v.lower() in ("1", "true", "yes", "on"))


def _free_slug(c, uid, base, nid=None):
    base = base if SLUG_RE.fullmatch(base or "") else (slugify(t("Unbenannt")) or "page")
    return _unique(c, base, SLUG_MAX, lambda x: bool(
        c.execute("SELECT 1 FROM note_publish WHERE uid=? AND slug=? AND note_id<>?", (uid, x, nid or 0)).fetchone()))


def published_via(c, uid, nid):
    """Ist die Seite ueber eine Elternseite mit "Unterseiten mit veroeffentlichen" oeffentlich?
    -> {note_id, title, url} der naechsten solchen Elternseite (wie Ctx.url_for), sonst None."""
    name = get_name(c, uid)
    if not name:
        return None
    ch = _chain(c, uid, nid)
    if len(ch) < 2:
        return None
    pubs = {r[0]: r[1] for r in c.execute("SELECT note_id, slug FROM note_publish WHERE uid=? AND active=1 AND children=1", (uid,))}
    for anc in ch[1:]:
        if anc in pubs:
            tr = c.execute("SELECT title FROM note WHERE id=? AND uid=?", (anc, uid)).fetchone()
            return {"note_id": anc, "title": (tr[0] if tr else "") or t("Unbenannt"), "url": BASE_URL + _url(name, pubs[anc], nid)}
    return None


def state(c, uid, nid, note_title=""):
    """Veroeffentlichung einer eigenen Seite als JSON (auch wenn nicht veroeffentlicht: Vorschlaege bzw. die
    gemerkten Einstellungen einer beendeten Veroeffentlichung)."""
    name = get_name(c, uid)
    r = c.execute(f"SELECT {PUB_COLS}, active, withdrawn_at, withdrawn_by FROM note_publish WHERE note_id=? AND uid=?",
                  (nid, uid)).fetchone()
    active = bool(r and r[7])
    out = {"enabled": enabled(), "name": name, "published": active, "home": False, "url": None,
           "profile_url": BASE_URL + f"/@{name}" if name else None, "published_via": published_via(c, uid, nid),
           "withdrawn_at": None, "withdrawn_by": None, "shared_edit": edit_shared(c, uid, nid)}
    if r:
        p = _pub_dict(r)
        out.update({k: p[k] for k in ("slug", "children", "indexable", "profile", "created", "updated")})
        if active:
            out["url"] = BASE_URL + _url(name, p["slug"]) if name else None
            out["home"] = public_home() == nid
        else:                                          # beendet: Adresse + Einstellungen bleiben fuer diese Seite reserviert
            out.update(withdrawn_at=r[8], withdrawn_by=r[9])
    else:
        out.update(slug=_free_slug(c, uid, slugify(note_title), nid), children=False, indexable=False, profile=False)
    other = c.execute("SELECT p.note_id, n.title FROM note_publish p JOIN note n ON n.id=p.note_id AND n.uid=p.uid "
                      "WHERE p.uid=? AND p.profile=1 AND p.active=1 AND p.note_id<>? LIMIT 1", (uid, nid)).fetchone()
    out["profile_other"] = {"note_id": other[0], "title": other[1] or t("Unbenannt")} if other else None
    if not name:
        out["suggested_name"] = suggest_name(c, uid)
    return out


def _own_note(c, uid, nid):
    return c.execute("SELECT title FROM note WHERE id=? AND uid=? AND deleted_at IS NULL", (nid, uid)).fetchone()


def _not_mine(c, uid, nid):
    """Veroeffentlichen darf nur der Eigentuemer: geteilte Seite -> 403, sonst 404 (Existenz bleibt verborgen)."""
    _, _, err = access(c, uid, nid, "owner")
    return err if err is not None else _bad("not found", 404)


@router.get("/api/notes/{nid}/publish")
def api_publish_get(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = _own_note(c, uid, nid)
        if not r:
            return _not_mine(c, uid, nid)
        return JSONResponse(state(c, uid, nid, r[0]), headers={"Cache-Control": "no-store"})


@router.put("/api/notes/{nid}/publish")
async def api_publish_put(nid: int, request: Request):
    """Veroeffentlichen bzw. Einstellungen aendern: {slug?, children?, indexable?, profile?, name?}.
    Ohne oeffentlichen Namen: 409 mit need_name (oder 'name' gleich mitschicken)."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request)
    if b is None:
        return _bad(t("ungültige Anfrage"))
    return await run_in_threadpool(_publish_put, uid, nid, b)


def _publish_put(uid, nid, b):
    if not enabled():
        return _bad(t("Veröffentlichen ist auf diesem Server abgeschaltet."), 403)
    with _db() as c:
        r = _own_note(c, uid, nid)
        if not r:
            return _not_mine(c, uid, nid)
        if c.execute("SELECT is_template FROM note WHERE id=? AND uid=?", (nid, uid)).fetchone()[0]:
            return _bad(t("Vorlagen können nicht veröffentlicht werden."))
        has_name = bool(get_name(c, uid))
    if not has_name:
        if b.get("name"):
            try:
                _, err = set_name(uid, b["name"])
            except psycopg.errors.UniqueViolation:      # gleichzeitig vergeben: 409 statt Serverfehler
                return JSONResponse({"error": t("Dieser Name ist schon vergeben."), "need_name": True}, status_code=409)
            if err:
                return JSONResponse({"error": err, "need_name": True}, status_code=400)
        else:
            with _db() as c:
                sug = suggest_name(c, uid)
            return JSONResponse({"error": t("Bitte zuerst einen öffentlichen Namen wählen."), "need_name": True,
                                 "suggested_name": sug}, status_code=409)
    now = time.time()
    try:
        with _db() as c, c:
            cur = c.execute(f"SELECT {PUB_COLS} FROM note_publish WHERE note_id=? AND uid=?", (nid, uid)).fetchone()
            old = _pub_dict(cur) if cur else {"slug": None, "children": False, "indexable": False, "profile": False}
            if "slug" in b and str(b.get("slug") or "").strip():
                slug = str(b["slug"]).strip().lower()
                if not SLUG_RE.fullmatch(slug):
                    return _bad(t("Adresse: 2–60 Zeichen, a–z, 0–9, - und _ (klein)."))
                if c.execute("SELECT 1 FROM note_publish WHERE uid=? AND slug=? AND note_id<>?", (uid, slug, nid)).fetchone():
                    return _bad(t("Diese Adresse benutzt schon eine andere deiner Seiten."), 409)
            else:
                slug = old["slug"] or _free_slug(c, uid, slugify(r[0]), nid)
            fl = {k: (_flag(b[k]) if k in b else old[k]) for k in ("children", "indexable", "profile")}
            replaced = None
            if fl["profile"]:
                replaced = c.execute("SELECT p.note_id, n.title FROM note_publish p JOIN note n ON n.id=p.note_id AND n.uid=p.uid "
                                     "WHERE p.uid=? AND p.profile=1 AND p.active=1 AND p.note_id<>? LIMIT 1", (uid, nid)).fetchone()
            if cur:                                     # auch nach dem Beenden: wieder aktiv, mit den gemerkten Einstellungen
                c.execute("UPDATE note_publish SET slug=?, children=?, indexable=?, profile=?, updated=?, active=1, "
                          "withdrawn_at=NULL, withdrawn_by=NULL WHERE note_id=? AND uid=?",
                          (slug, fl["children"], fl["indexable"], fl["profile"], now, nid, uid))
            else:
                c.execute("INSERT INTO note_publish (note_id, uid, slug, children, indexable, profile, created, updated) "
                          "VALUES (?,?,?,?,?,?,?,?)", (nid, uid, slug, fl["children"], fl["indexable"], fl["profile"], now, now))
            if fl["profile"]:                       # hoechstens eine Profilseite je Konto
                c.execute("UPDATE note_publish SET profile=0 WHERE uid=? AND note_id<>?", (uid, nid))
    except psycopg.errors.UniqueViolation:
        return _bad(t("Diese Adresse benutzt schon eine andere deiner Seiten."), 409)
    with _db() as c:
        out = state(c, uid, nid, r[0])
    if replaced:                                        # Hinweis im Dialog: "ersetzt die bisherige Profilseite"
        out["replaced_profile"] = {"note_id": replaced[0], "title": replaced[1] or t("Unbenannt")}
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


def unpublish(note_id, uid=None, by="user"):
    """Veroeffentlichung beenden (Benutzer) bzw. zurueckziehen (Admin, by='admin'). Die Zeile bleibt als inaktiv
    stehen: Adresse und Einstellungen sind fuer diese Seite reserviert und gelten beim erneuten Veroeffentlichen wieder.
    Endgueltiges Loeschen der Seite und des Kontos entfernt sie. War sie die Startseite fuer Besucher, wird diese geleert."""
    with _db() as c, c:
        n = c.execute("UPDATE note_publish SET active=0, withdrawn_at=?, withdrawn_by=? WHERE active=1 AND note_id=?"
                      + (" AND uid=?" if uid else ""),
                      (time.time(), by, note_id, uid) if uid else (time.time(), by, note_id)).rowcount
        if n and by == "admin":                  # Benachrichtigung an den Eigentuemer (saga.notify)
            owner = c.execute("SELECT uid FROM note_publish WHERE note_id=?", (note_id,)).fetchone()
            if owner:
                bus.fire("publish.withdrawn", c, note_id=note_id, owner=owner[0])
    if n and public_home() == note_id:
        auth.save_setting("public_home", 0)
    return n


@router.delete("/api/notes/{nid}/publish")
def api_publish_delete(nid: int, request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        r = _own_note(c, uid, nid)
        if not r:
            return _not_mine(c, uid, nid)
    n = unpublish(nid, uid, "user")
    with _db() as c:
        return JSONResponse(dict(state(c, uid, nid, r[0]), ok=True, removed=bool(n)), headers={"Cache-Control": "no-store"})


@router.get("/api/account/public-name")
def api_public_name_get(request: Request):
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    with _db() as c:
        name = get_name(c, uid)
        n = c.execute("SELECT COUNT(*) FROM note_publish WHERE uid=? AND active=1", (uid,)).fetchone()[0]
        out = {"name": name, "url": BASE_URL + f"/@{name}" if name else None, "enabled": enabled(), "published": n,
               "suggested": suggest_name(c, uid) if not name else name,
               "is_login": bool(name) and name == str(uid).lower(),      # 2.4: frueher selbst gewaehlt -> Hinweis
               "fed_loss": importlib.import_module("saga.federation").rename_loss(c, uid)}   # 2.5: Freigaben aelterer Server
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.put("/api/account/public-name")
async def api_public_name_put(request: Request):
    """{name, check?}: check=true prueft nur (gueltig und frei), ohne zu speichern."""
    uid = _api_guard(request)
    if not uid:
        return _bad("unauthorized", 401)
    if (bad := _json_only(request)) is not None:
        return bad
    b = await _body(request) or {}
    name = str(b.get("name") or "").strip().lower()
    if b.get("check"):
        p = name_problem(name, uid)
        if not p:
            with _db() as c:
                if c.execute("SELECT 1 FROM public_name WHERE name=? AND uid<>?", (name, uid)).fetchone() or _retired(c, name, uid):
                    p = t("Dieser Name ist schon vergeben.")
        return JSONResponse({"ok": not p, "available": not p, "name": name, **({"error": p} if p else {})},
                            headers={"Cache-Control": "no-store"})
    try:
        name, err = await run_in_threadpool(set_name, uid, name)
    except psycopg.errors.UniqueViolation:
        name, err = None, t("Dieser Name ist schon vergeben.")
    if err:
        return _bad(err, 409 if err == t("Dieser Name ist schon vergeben.") else 400)
    return JSONResponse({"ok": True, "name": name, "url": BASE_URL + f"/@{name}"})


# =====================================================================================================
# Admin-Bereich: alle oeffentlichen Seiten, Auswahl der Startseite
# =====================================================================================================
def all_published():
    """Alle Veroeffentlichungen (fuer den Admin): Konto, @name, Titel, seit, erreichbar?"""
    with _db() as c:
        rows = c.execute(
            "SELECT p.note_id, p.uid, pn.name, n.title, p.slug, p.created, n.deleted_at, "
            "CASE WHEN n.is_template<>0 THEN 1 ELSE n.archived END, p.children, p.profile "
            "FROM note_publish p JOIN note n ON n.id=p.note_id AND n.uid=p.uid LEFT JOIN public_name pn ON pn.uid=p.uid "
            "WHERE p.active=1 ORDER BY p.created DESC LIMIT 2000").fetchall()
    return [{"note_id": r[0], "uid": r[1], "name": r[2], "title": r[3] or t("Unbenannt"), "slug": r[4], "created": r[5],
             "url": BASE_URL + _url(r[2], r[4]) if r[2] else "", "live": r[6] is None and r[7] is None and bool(r[2]),
             "children": bool(r[8]), "profile": bool(r[9])} for r in rows]


@router.api_route("/@{rest:path}", methods=GET_HEAD)
def public_other(rest: str, request: Request):
    """Alles andere unter /@ (zu viele Teile, Unsinn): dieselbe neutrale 404 statt der Standard-Fehlermeldung.
    Ausnahme (P10): "/" am Ende -> 301 ohne (und klein geschrieben), wenn der Rest wie eine gueltige Adresse aussieht."""
    if _limited(request):
        return _too_many()
    if rest.endswith("/") and rest.strip("/"):
        seg = rest.rstrip("/").split("/")
        if len(seg) <= 3 and all(seg):
            seg[0] = seg[0].lower()
            if len(seg) > 1:
                seg[1] = seg[1].lower()
            ok = NAME_RE.fullmatch(seg[0]) and (len(seg) < 2 or SLUG_RE.fullmatch(seg[1])) and \
                (len(seg) < 3 or re.fullmatch(r"[1-9][0-9]{0,17}", seg[2]))
            if ok:
                q = "?" + request.url.query if request.url.query else ""
                return RedirectResponse("/@" + "/".join(seg) + q, status_code=301)
    with _Lang(request):
        return not_found()
