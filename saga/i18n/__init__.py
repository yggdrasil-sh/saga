"""Mehrsprachigkeit: Kataloge (de/en/es/ru/th als JSON neben dieser Datei), Sprachwahl und t().

- Schluessel = der deutsche Text (de.json enthaelt sie unveraendert), Platzhalter {name}. Gleicher deutscher Text mit
  anderer Bedeutung: Kontext hinter '##' im Schluessel ('Lesen##Rolle') - angezeigt wird nur der Teil davor.
- Pluralformen (Zahl-Texte): in der Uebersetzung {n:form1|form2|...} neben {n} - die Formen in der Reihenfolge der
  CLDR-Kategorien der Sprache (PLURAL_FORMS: de/en/es one|other, ru one|few|many, th nur other). Beispiel ru:
  "{n} {n:новое сообщение|новых сообщения|новых сообщений}". Schluessel bleiben unveraendert; ohne Formen wie bisher.
- Python: t("Gespeichert.") bzw. t("Höchstens {n} Schlüssel", n=10) - die Sprache kommt aus der
  laufenden Anfrage (ContextVar, von der Middleware gesetzt). Ohne Anfrage: Englisch.
- Jinja: _("Anmelden") und lang()/locale() als Globale (install()).
- JavaScript: T('Speichern') aus static/common.js; das Woerterbuch liefert /i18n/<lang>.js -
  nur die Schluessel, die in static/*.js als T('...') vorkommen.
Sprachwahl (resolve): Konto-Einstellung > Sprach-Cookie (Umschalter auf der Anmeldeseite, COOKIE) > Standardsprache
des Servers (Admin) > Accept-Language > Englisch.
"""
import contextvars
import glob
import hashlib
import json
import os
import re
from datetime import datetime

LANGS = ("de", "en", "es", "ru", "th")
DEFAULT = "en"
NAMES = {"de": "Deutsch", "en": "English", "es": "Español", "ru": "Русский", "th": "ไทย"}
# Thai: gregorianischer Kalender (th-TH zaehlt sonst buddhistische Jahre, 2026 = 2569)
LOCALES = {"de": "de-DE", "en": "en-US", "es": "es-ES", "ru": "ru-RU", "th": "th-TH-u-ca-gregory"}
# Reihenfolge der Pluralformen je Sprache ({n:...} in Uebersetzungen); Thai kennt keine Pluralformen
PLURAL_FORMS = {"de": ("one", "other"), "en": ("one", "other"), "es": ("one", "other"), "ru": ("one", "few", "many"), "th": ("other",)}
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "static")
_PH = re.compile(r"\{(\w+)\}")
_PL = re.compile(r"\{(\w+):([^{}]*)\}")                 # Pluralformen {n:eins|mehrere}
_ANY = re.compile(r"\{\w+(?::[^{}]*)?\}")                 # Platzhalter oder Pluralformen (is_message)
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


def resolve(user="", server="", header="", cookie=""):
    """Konto > Umschalter auf diesem Geraet (Cookie) > Standardsprache des Servers > Browser (Accept-Language) > Englisch."""
    return norm(user) or norm(cookie) or norm(server) or from_header(header) or DEFAULT


# --- Sprach-Cookie: Umschalter vor der Anmeldung (fremder Rechner) ------------------------------------------------
# Nur die Sprache (de/en/es), kein Tracking; gilt fuer Anmelde-, 2FA-, App-Anmelde- und oeffentliche Fehlerseiten und
# nach der Anmeldung, solange das Konto "Automatisch" eingestellt hat.
COOKIE = "saga_lang"
COOKIE_MAX_AGE = 365 * 86400
# Sprache der App, die die Anmeldung/Kopplung im Browser oeffnet (?lang=): nur fuer diese Browser-Sitzung (ohne
# Ablaufdatum), schwaecher als Konto und Umschalter (saga.core.LangMiddleware)
HINT_COOKIE = "saga_lang_app"


def cookie_from_header(raw, name=COOKIE):
    """Cookie-Kopfzeile -> gewaehlte Sprache oder '' (ohne http.cookies: ein kaputtes fremdes Cookie stoert nicht)."""
    for part in str(raw or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return norm(v.strip().strip('"'))
    return ""


# Laufende Anfrage: (Wahl des Umschalters bzw. des Kontos, '' = automatisch; aktuelle Adresse fuer den Ruecksprung)
_ui = contextvars.ContextVar("saga_lang_ui", default=("", "/"))


def set_ui(pref, here):
    return _ui.set((norm(pref), here or "/"))


def pref():
    """Ausdruecklich gewaehlte Sprache dieser Anfrage (Konto, sonst Cookie); '' = automatisch."""
    return _ui.get()[0]


def set_lang(lang):
    return _cur.set(norm(lang) or DEFAULT)


def get_lang():
    return _cur.get()


def locale():
    return LOCALES[_cur.get()]


def plural_cat(lang, n):
    """CLDR-Pluralkategorie (Kardinalzahl) fuer de/en/es/ru/th."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return "other"
    if lang == "th":
        return "other"
    if f != int(f):
        return "other"
    i = abs(int(f))
    if lang == "ru":
        if i % 10 == 1 and i % 100 != 11:
            return "one"
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return "few"
        return "many"
    return "one" if i == 1 else "other"


def plural(lang, n, forms):
    """Form fuer n aus 'a|b|c' (Reihenfolge PLURAL_FORMS[lang]); fehlt sie, die letzte."""
    forms = forms.split("|")
    order = PLURAL_FORMS.get(lang, ("one", "other"))
    cat = plural_cat(lang, n)
    i = order.index(cat) if cat in order else len(order) - 1
    return forms[min(i, len(forms) - 1)]


def fill(s, params, lang=None):
    if not params:
        return s
    if "{" in s and ":" in s:
        lang = lang or _cur.get()
        s = _PL.sub(lambda m: plural(lang, params[m.group(1)], m.group(2)) if m.group(1) in params else m.group(0), s)
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


def _msg_rx(x):
    """Muster fuer eine Meldung mit Platzhaltern: {name} = beliebiger Text, {n:a|b|c} = genau eine der Formen."""
    out, pos = [], 0
    for m in _ANY.finditer(x):
        out.append(re.escape(x[pos:m.start()]))
        forms = m.group(0)[1:-1].partition(":")[2]
        out.append("(?:" + "|".join(re.escape(f) for f in forms.split("|")) + ")" if ":" in m.group(0) else ".+?")
        pos = m.end()
    out.append(re.escape(x[pos:]))
    return "".join(out)


def is_message(s):
    """Ist s ein Text aus dem Katalog (eigene Meldung, ggf. mit eingesetzten Platzhaltern) - und keine rohe
    Python-Fehlermeldung wie "invalid literal for int()"? (BUG-084)"""
    if not _MSG:
        plain, rx = set(), []
        for cat in CATALOG.values():
            for k, v in cat.items():
                for x in {_ctx(k, k), _ctx(v, k)}:
                    if _ANY.search(x):
                        rx.append(re.compile(_msg_rx(x) + r"\Z", re.S))
                    else:
                        plain.add(x)
        _MSG.update(plain=plain, rx=rx)
    return s in _MSG["plain"] or any(r.match(s) for r in _MSG["rx"])


def t_lang(lang, key, **params):
    lang = norm(lang) or DEFAULT
    return fill(_ctx(CATALOG[lang].get(key) or key, key), params, lang)


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
    s = day_str(d, lang)
    return s + d.strftime(" %H:%M") if with_time else s


def day_str(d, lang=None):
    """date/datetime -> Datum im ueblichen Format der Sprache (Thai: gregorianisch, wie im Browser)."""
    lang = lang or _cur.get()
    if lang == "en":
        return f"{_MON_EN[d.month - 1]} {d.day}, {d.year}"
    if lang in ("de", "ru"):
        return d.strftime("%d.%m.%Y")
    if lang == "th":
        return f"{d.day}/{d.month}/{d.year}"
    return d.strftime("%d/%m/%Y")


# Zahlen wie Intl.NumberFormat im Browser (Fix27 #10): (Tausender, Dezimal, Tausender erst ab 5 Stellen, Prozent, Euro vorn)
_NUM = {"de": (".", ",", False, "\u00a0%", False), "en": (",", ".", False, "%", True), "es": (".", ",", True, "\u00a0%", False),
        "ru": ("\u00a0", ",", False, "\u00a0%", False), "th": (",", ".", False, "%", True)}


def fmt_num(v, fmt=None, lang=None):
    """Zahl in der Sprache der Person: Prozent ganzzahlig ("41 %"), Euro immer mit 2 Nachkommastellen ("1.430,50 €"),
    sonst hoechstens 2 Nachkommastellen. fmt: pct|percent, eur|euro, sonst Zahl. None -> '–'."""
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return "–"
    grp, dec, min5, pct, eur_front = _NUM.get(lang or _cur.get(), _NUM["en"])
    nd = 0 if fmt in ("pct", "percent") else 2
    s = f"{abs(v):.{nd}f}"
    whole, _, frac = s.partition(".")
    if fmt not in ("eur", "euro"):
        frac = frac.rstrip("0")
    if len(whole) > 3 and not (min5 and len(whole) < 5):
        whole = f"{int(whole):,}".replace(",", grp)
    out = ("-" if v < 0 and (whole.strip("0") or frac.strip("0")) else "") + whole + (dec + frac if frac else "")
    if fmt in ("pct", "percent"):
        return out + pct
    if fmt in ("eur", "euro"):
        return ("€" + out) if eur_front else (out + "\u00a0€")
    return out


def install(env):
    """Jinja-Umgebung: _(), t(), lang(), locale() als Globale (Sprachumschalter: saga.core.install_lang_ui)."""
    env.globals.update(_=t, t=t, lang=get_lang, locale=locale, LANG_NAMES=NAMES, i18n_v=VERSION)
