"""Websuche fuer die KI (2.1) ueber die vorhandene SearXNG-Anbindung (SEARXNG_URL, wie die Websuche im Dashboard).

Ablauf - modellunabhaengig, ohne Tool-Calls (auch kleine lokale Modelle): saga/ai.py fragt das Modell zuerst kurz, ob
eine Websuche noetig ist (Antwort NONE oder hoechstens zwei Zeilen "SEARCH: ..."). parse_decision filtert das streng.
Dann fragt search() SearXNG (Zeitlimit, wenige Treffer: Titel, URL, Ausschnitt) und fetch_text() holt optional den Text
der besten Treffer - nur oeffentliche Adressen (saga/netguard.py: einmal aufloesen, pruefen, genau diese IP verbinden,
auch bei Weiterleitungen), nur Port 80/443, nur text/html bzw. text/plain, mit Groessen- und Zeitlimit.
Die Treffer gehen als DATEN in den Prompt (ai.py, zwischen den Markierungen).

Schalter: Admin-Bereich -> KI-Assistent -> "Websuche erlauben" (Standard aus); wirksam nur mit SEARXNG_URL.
Datenschutz: Suchbegriffe gehen ueber SearXNG an Suchmaschinen; Logs enthalten nur Anzahl und Dauer.
Dieses Modul haengt nur von saga.core und saga.netguard ab (ai.py und ai_chat.py importieren es, nicht umgekehrt)."""
import re
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from saga.core import E, RESTORE_TEST, SEARCH_LANGUAGE, SEARCH_SAFESEARCH, SEARXNG_URL
from saga.netguard import guarded_client, is_public, request_checker

RESULTS = 6                      # Treffer je Antwort (alle Suchanfragen zusammen)
QUERIES = 2                      # Suchanfragen je Frage
FETCH = max(0, min(3, int(E.get("AI_WEB_FETCH", "2"))))      # Seiten, deren Text geholt wird (0 = nur Ausschnitte)
SEARCH_TIMEOUT = 8.0             # Sekunden je SearXNG-Anfrage
FETCH_TIMEOUT = 5.0              # Sekunden je Seitenabruf
FETCH_BUDGET = 9.0               # Sekunden fuer alle Seitenabrufe zusammen
FETCH_BYTES = 400_000            # hoechstens so viel HTML je Seite lesen
PAGE_CHARS = 1500                # Zeichen Seitentext je Treffer im Prompt
SNIPPET_CHARS = 500
TITLE_CHARS = 200
URL_CHARS = 500
Q_CHARS = 120                    # Zeichen je Suchanfrage
Q_WORDS = 14
RATE_N = max(1, int(E.get("AI_WEB_RATE", "10")))             # Websuchen je Person ...
RATE_PER = 600.0                                              # ... in 10 Minuten
_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_CTRL_NL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")         # wie _CTRL, Zeilenumbrueche bleiben
_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


def available():
    """SearXNG eingerichtet (Umgebung)?"""
    return bool(SEARXNG_URL)


def enabled(c):
    """Websuche fuer die KI wirksam: Admin-Schalter UND SearXNG eingerichtet."""
    return bool((c or {}).get("web")) and available()


# --- Rate-Limit je Person ------------------------------------------------------------------------------------------
_hits = {}
_lock = threading.Lock()


def rate_ok(uid):
    now = time.monotonic()
    with _lock:
        h = [x for x in _hits.get(uid, []) if now - x < RATE_PER]
        if len(h) >= RATE_N:
            _hits[uid] = h
            return False
        _hits[uid] = h + [now]
        if len(_hits) > 20000:
            _hits.clear()
        return True


# --- Entscheidung des Modells ---------------------------------------------------------------------------------------
_SEARCH = re.compile(r"^\s*(?:[-*•>]\s*|\d[.)]\s*)?\**\s*SEARCH\s*\**\s*[:：]\s*(.+?)\s*$", re.I)


def clean_query(s):
    """Suchanfrage aus der Antwort des Modells -> bereinigt oder '' (verworfen)."""
    s = _CTRL.sub(" ", str(s or ""))
    s = re.sub(r"<<<|>>>|SAGA_DATA", " ", s, flags=re.I)
    s = s.strip().strip("\"'`«»„“”‚‘’*_[]()").strip()
    s = re.sub(r"(?:^|\s)!+\S*", " ", s)                   # SearXNG-"Bangs" (!g, !!) waehlen Suchmaschinen: weg
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < 2 or not re.search(r"\w", s):
        return ""
    if len(s.split()) > Q_WORDS:
        return ""
    if len(s) > Q_CHARS:
        s = s[:Q_CHARS].rsplit(" ", 1)[0]
    return s


def parse_decision(text):
    """'NONE' oder bis zu QUERIES Zeilen 'SEARCH: anfrage' -> [anfragen] ([] = keine Websuche).
    Alles andere wird ignoriert (Erklaerungen kleiner Modelle); doppelte Anfragen zaehlen einmal."""
    out = []
    for ln in str(text or "").splitlines()[:20]:
        m = _SEARCH.match(ln)
        if not m:
            continue
        q = clean_query(m.group(1))
        if q and q.lower() not in [x.lower() for x in out]:
            out.append(q)
        if len(out) >= QUERIES:
            break
    return out


# --- SearXNG --------------------------------------------------------------------------------------------------------
def http_url(u):
    if not isinstance(u, str) or len(u) > URL_CHARS:
        return False
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


def _txt(s, n):
    return re.sub(r"\s+", " ", _CTRL.sub(" ", str(s or ""))).strip()[:n]


def search(queries, client=None):
    """SearXNG fragen -> ([{title, url, host, content}], [kurzantworten]). Wirft bei Fehlern (Aufrufer faengt)."""
    if RESTORE_TEST:                                      # Testwiederherstellung: nichts nach aussen (saga/backups.py)
        raise RuntimeError("SAGA_RESTORE_TEST")
    own = client is None
    cl = client or httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=False, headers={"User-Agent": "Saga/1"})
    results, answers, seen, errors = [], [], set(), 0
    qs = queries[:QUERIES]
    per = -(-RESULTS // max(1, len(qs)))                   # je Suchanfrage gleich viele Treffer
    try:
        for q in qs:
            try:
                r = cl.post(SEARXNG_URL + "/search", data={"q": q, "format": "json", "pageno": 1,
                                                          "language": SEARCH_LANGUAGE, "safesearch": SEARCH_SAFESEARCH})
                r.raise_for_status()
                d = r.json()
            except (httpx.HTTPError, ValueError):
                errors += 1
                continue
            for a in (d.get("answers") or [])[:2]:
                ans = a if isinstance(a, str) else (a.get("answer") if isinstance(a, dict) else None)
                if ans and _txt(ans, 300) not in answers:
                    answers.append(_txt(ans, 300))
            k = 0
            for x in d.get("results") or []:
                u = x.get("url") if isinstance(x, dict) else None
                if not http_url(u) or u in seen:
                    continue
                seen.add(u)
                results.append({"title": _txt(x.get("title") or urlparse(u).hostname, TITLE_CHARS), "url": u,
                                "host": urlparse(u).hostname or "", "content": _txt(x.get("content"), SNIPPET_CHARS)})
                k += 1
                if k >= per:
                    break
    finally:
        if own:
            cl.close()
    if errors and errors == len(qs):
        raise RuntimeError("searxng")
    return results[:RESULTS], answers[:3]


# --- Seitentext (optional, nur oeffentliche Adressen) ----------------------------------------------------------------
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head", "nav", "footer", "form", "iframe", "button",
            "select", "aside", "canvas", "object"}
    BLOCK = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "main", "header", "dd", "dt", "pre", "blockquote", "table", "ul", "ol"}
    VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "body":                                  # <head> ohne </head>: ab <body> wieder Text
            self.skip = 0
        elif tag in self.SKIP and tag not in self.VOID:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(html):
    """HTML -> Klartext-Absaetze (ohne Skripte, Stile, Navigation, Formulare)."""
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - kaputtes HTML: was bis dahin da ist
        pass
    lines = [re.sub(r"[ \t\r\f\v\xa0]+", " ", x).strip() for x in "".join(p.out).split("\n")]
    return "\n".join(x for x in lines if x)


def excerpt(text, terms, n=PAGE_CHARS):
    """Die Absaetze mit den meisten Suchwoertern (in der Reihenfolge der Seite), zusammen hoechstens n Zeichen."""
    paras = [x for x in text.split("\n") if len(x) >= 25]
    if not paras:
        return text[:n]
    tw = {w for w in re.findall(r"\w{3,}", " ".join(terms).lower())}
    scored = sorted(range(len(paras)), key=lambda i: (-sum(w in paras[i].lower() for w in tw), i))
    pick, used = [], 0
    for i in scored:
        if pick and used + len(paras[i]) > n:
            continue
        pick.append(i)
        used += len(paras[i]) + 1
    out = "\n".join(paras[i] for i in sorted(pick))
    return out[:n]


def public_only(ip, _host, _port):
    return is_public(ip)


PORTS = (None, 80, 443)


def _checker(ports):
    """Jede Anfrage (auch nach Weiterleitung): nur http/https, nur Standard-Ports, keine Zugangsdaten in der URL."""
    return request_checker(ports)


def fetch_text(url, terms, ip_ok=public_only, timeout=FETCH_TIMEOUT, max_bytes=FETCH_BYTES, ports=PORTS):
    """Text einer Trefferseite -> Auszug oder None. ip_ok/ports nur fuer Tests austauschbar (Standard: nur oeffentliche
    IPs, nur Port 80/443). Die Pruefung gilt fuer jede Verbindung, auch nach Weiterleitungen (hoechstens 3)."""
    if not http_url(url):
        return None
    try:
        cl = guarded_client(ip_ok, follow_redirects=True, max_redirects=3, event_hooks={"request": [_checker(ports)]},
                            timeout=httpx.Timeout(timeout, connect=min(4.0, timeout)),
                            headers={"User-Agent": "Mozilla/5.0 (compatible; Saga)", "Accept": "text/html, text/plain;q=0.8",
                                     "Accept-Language": SEARCH_LANGUAGE or "en", "Accept-Encoding": "identity"})
    except RuntimeError:
        return None
    t0 = time.monotonic()
    try:
        with cl.stream("GET", url) as r:
            if r.status_code != 200:
                return None
            ct = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct not in _TYPES:
                return None
            # Nur unkomprimiert (Accept-Encoding: identity): httpx entpackt gzip/br/zstd sonst ohne Grenze je Stueck -
            # ein kleines Paket koennte so zu vielen MB/GB werden (Dekompressionsbombe). iter_raw entpackt nichts.
            if r.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                return None
            buf = bytearray()
            for part in r.iter_raw():
                buf += part
                if len(buf) >= max_bytes or time.monotonic() - t0 > timeout:
                    break
            enc = r.charset_encoding or "utf-8"
            try:
                raw = bytes(buf[:max_bytes]).decode(enc, errors="replace")
            except LookupError:
                raw = bytes(buf[:max_bytes]).decode("utf-8", errors="replace")
        text = raw if ct == "text/plain" else html_text(raw)
        text = _CTRL_NL.sub(" ", text)
        out = excerpt(text, terms)
        return out if len(out) >= 40 else None
    except (httpx.HTTPError, OSError, ValueError):
        return None
    finally:
        cl.close()


def run(queries, stop=lambda: False):
    """Suchen und (optional) Seitentexte holen -> {results, answers, pages, ms}. Wirft, wenn SearXNG nicht antwortet.
    Log: nur Anzahl und Dauer, nie Suchbegriffe oder Treffer."""
    t0 = time.monotonic()
    results, answers = search(queries)
    pages = 0
    for x in results[:FETCH]:
        if stop() or time.monotonic() - t0 > SEARCH_TIMEOUT + FETCH_BUDGET:
            break
        txt = fetch_text(x["url"], queries)
        if txt:
            x["page"] = txt
            pages += 1
    ms = int((time.monotonic() - t0) * 1000)
    print(f"ai-web: {len(queries)} Suchanfrage(n), {len(results)} Treffer, {pages} Seite(n) gelesen, {ms} ms", flush=True)
    return {"results": results, "answers": answers, "pages": pages, "ms": ms}
