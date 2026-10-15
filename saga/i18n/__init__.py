"""Mehrsprachigkeit: Kataloge (de/en/es als JSON neben dieser Datei), Sprachwahl und t().

- Schluessel = der deutsche Text (de.json enthaelt sie unveraendert), Platzhalter {name}. Gleicher deutscher Text mit
  anderer Bedeutung: Kontext hinter '##' im Schluessel ('Lesen##Rolle') - angezeigt wird nur der Teil davor.
- Python: t("Gespeichert.") bzw. t("Höchstens {n} Schlüssel", n=10) - die Sprache kommt aus der
  laufenden Anfrage (ContextVar, von der Middleware gesetzt). Ohne Anfrage: Englisch.
- Jinja: _("Anmelden") und lang()/locale() als Globale (install()).
- JavaScript: T('Speichern') aus static/common.js; das Woerterbuch liefert /i18n/<lang>.js -
  nur die Schluessel, die in static/*.js als T('...') vorkommen.
Sprachwahl (resolve): Konto-Einstellung > Standardsprache des Servers (Admin) > Accept-Language > Englisch.
"""
import contextvars
import glob
import hashlib
import json
import os
import re
from datetime import datetime

LANGS = ("de", "en", "es")
DEFAULT = "en"
NAMES = {"de": "Deutsch", "en": "English", "es": "Español"}
LOCALES = {"de": "de-DE", "en": "en-US", "es": "es-ES"}
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "static")
_PH = re.compile(r"\{(\w+)\}")
# T('...') / T("...") in den Skripten; nur Literale (keine zusammengesetzten Schluessel)
JS_CALL = re.compile(r"""\bT\(\s*(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)")""")


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _load():
    cat, h = {}, hashlib.sha256()
    for lang in LANGS:
        with open(os.path.join(_HERE, lang + ".json"), "rb") as f:
            raw = f.read()
        h.update(raw)
        cat[lang] = json.loads(raw.decode("utf-8"))
    return cat, h.hexdigest()[:10]


def js_keys(root=_STATIC):
    """Alle Schluessel, die das Front-End per T('...') benutzt."""
    keys = set()
    for p in sorted(glob.glob(os.path.join(root, "*.js"))):
        with open(p, encoding="utf-8") as f:
            for m in JS_CALL.finditer(f.read()):
                keys.add(_unescape(m.group(1) if m.group(1) is not None else m.group(2)))
    return keys


CATALOG, VERSION = _load()
_JS_KEYS = js_keys()
_cur = contextvars.ContextVar("saga_lang", default=DEFAULT)


def norm(v):
    """'de-AT' / 'DE' -> 'de'; Unbekanntes/leer -> '' (= automatisch)."""
    v = str(v or "").strip().lower().replace("_", "-").split("-")[0]
    return v if v in LANGS else ""


def from_header(header):
    """Accept-Language -> beste unterstuetzte Sprache oder ''."""
    best = []
    for i, part in enumerate(str(header or "").split(",")[:20]):
        bits = part.strip().split(";")
        q = 1.0
        for b in bits[1:]:
            b = b.strip()
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        lang = norm(bits[0])
        if lang and q > 0:
            best.append((-q, i, lang))
    return min(best)[2] if best else ""


def resolve(user="", server="", header=""):
    """Konto > Standardsprache des Servers > Browser (Accept-Language) > Englisch."""
    return norm(user) or norm(server) or from_header(header) or DEFAULT


def set_lang(lang):
    return _cur.set(norm(lang) or DEFAULT)


def get_lang():
    return _cur.get()


def locale():
    return LOCALES[_cur.get()]


def fill(s, params):
    if not params:
        return s
    return _PH.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), s)


def _ctx(s, key):
    """Kontext ('Lesen##Rolle') nur am Schluessel abschneiden (deutsch bzw. fehlende Uebersetzung) - wie T() im Browser;
    eine Uebersetzung, die selbst '##' enthaelt, bleibt unveraendert."""
    i = s.find("##") if s == key else -1
    return s[:i] if i > 0 else s


def t(key, **params):
    """Uebersetzung in der Sprache der laufenden Anfrage (fehlt sie: der deutsche Schluessel)."""
    return fill(_ctx(CATALOG[_cur.get()].get(key) or key, key), params)


_MSG = {}


def is_message(s):
    """Ist s ein Text aus dem Katalog (eigene Meldung, ggf. mit eingesetzten Platzhaltern) - und keine rohe
    Python-Fehlermeldung wie "invalid literal for int()"? (BUG-084)"""
    if not _MSG:
        plain, rx = set(), []
        for cat in CATALOG.values():
            for k, v in cat.items():
                for x in {_ctx(k, k), _ctx(v, k)}:
                    if _PH.search(x):
                        rx.append(re.compile(".+?".join(re.escape(part) for part in _PH.split(x)[::2]) + r"\Z", re.S))
                    else:
                        plain.add(x)
        _MSG.update(plain=plain, rx=rx)
    return s in _MSG["plain"] or any(r.match(s) for r in _MSG["rx"])


def t_lang(lang, key, **params):
    return fill(_ctx(CATALOG[norm(lang) or DEFAULT].get(key) or key, key), params)


def js_catalog(lang):
    """Woerterbuch fuer das Front-End: nur Schluessel aus static/*.js (und nur, wo der Text abweicht)."""
    cat = CATALOG[norm(lang) or DEFAULT]
    return {k: cat[k] for k in sorted(_JS_KEYS) if k in cat and cat[k] != k}


_MON_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def fmt_date(ts, tz=None, with_time=False):
    """Zeitstempel -> Datum (optional mit Uhrzeit) im ueblichen Format der Sprache; leer -> '–'."""
    if not ts:
        return "–"
    d = datetime.fromtimestamp(ts, tz)
    lang = _cur.get()
    if lang == "en":
        s = f"{_MON_EN[d.month - 1]} {d.day}, {d.year}"
    else:
        s = d.strftime("%d.%m.%Y" if lang == "de" else "%d/%m/%Y")
    return s + d.strftime(" %H:%M") if with_time else s


def install(env):
    """Jinja-Umgebung: _(), t(), lang(), locale() als Globale."""
    env.globals.update(_=t, t=t, lang=get_lang, locale=locale, LANG_NAMES=NAMES, i18n_v=VERSION)
