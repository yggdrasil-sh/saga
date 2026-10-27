"""Werte von Datenbank-Eigenschaften als Text (2.5, Paket 2): eine Stelle fuer Markdown-Export (lesbar, Sprache des
Kontos) und CSV-Export (maschinenlesbar: Datum ISO, Zahl ohne Einheit, Haekchen true/false).

Erweiterungsstelle fuer neue Typen (Paket 1: Formel, Rollup, Person, Datei; Paket 5: Status): ein Modul meldet sich mit
register("person", fn) an. fn(p, v, ctx) -> str; ctx = Ctx (mode "md"|"csv", titles, tz, ...). Typen ohne Eintrag werden
allgemein als Text ausgegeben (Liste: mit ", " verbunden, Objekt: "name"/"title"/"value", sonst str) - der Export bricht
also nie an einem unbekannten Typ."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from saga import i18n
from saga.core import TZ, t
from saga.notes import AUTO_TYPES, _calc_value

_TYPES = {}


def register(typ, fn):
    """Neuen Eigenschaftstyp fuer Text-Ausgaben anmelden (fn(p, v, ctx) -> str)."""
    _TYPES[typ] = fn


class Ctx:
    """mode: "md" (lesbar) oder "csv" (maschinenlesbar). titles(id) -> Titel oder None (verknuepfte Eintraege, nur was der
    Betrachter sehen darf). decimal: Dezimalzeichen fuer CSV-Zahlen ("." oder ","). tz: Zeitzone fuer Zeitstempel."""

    def __init__(self, mode="md", titles=None, decimal=".", tz=None):
        self.mode, self.titles, self.decimal, self.tz = mode, titles or (lambda i: None), decimal, tz or TZ


def value_of(p, row_props, created=None, updated=None):
    """Rohwert einer Eigenschaft in einer Zeile (berechnet/automatisch eingeschlossen)."""
    typ = p.get("type")
    if typ == "calc":
        return _calc_value(p, row_props)
    if typ == "created":
        return created
    if typ == "edited":
        return updated
    return row_props.get(p.get("id"))


def num_text(v, decimal="."):
    """Zahl ohne Exponent und ohne unnoetige Nullen (0.1 -> "0.1", 3.0 -> "3", 1e15 -> "1000000000000000")."""
    if isinstance(v, bool):
        return ""
    if isinstance(v, int):
        s = str(v)
    else:
        try:
            s = format(Decimal(repr(float(v))), "f")
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            return ""
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        if s in ("-0", ""):
            s = "0"
    return s.replace(".", decimal) if decimal != "." else s


def _generic(v):
    if isinstance(v, list):
        return ", ".join(x for x in (_generic(x) for x in v) if x)
    if isinstance(v, dict):
        for k in ("name", "title", "value", "url"):
            if isinstance(v.get(k), (str, int, float)) and not isinstance(v.get(k), bool):
                return str(v[k])
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return num_text(v)
    return "" if v is None else str(v)


def prop_text(p, v, ctx=None):
    """Wert v der Eigenschaft p als Text."""
    ctx = ctx or Ctx()
    typ = p.get("type")
    csv = ctx.mode == "csv"
    if typ == "checkbox" and csv:
        return "true" if v else "false"
    if v is None or v == "" or v == []:
        return ""
    if typ in _TYPES:
        return _TYPES[typ](p, v, ctx)
    if typ in AUTO_TYPES:
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return ""
        if csv:
            return datetime.fromtimestamp(v, ctx.tz).strftime("%Y-%m-%d %H:%M")
        return i18n.fmt_date(v, ctx.tz, with_time=True)          # Datumsformat der Sprache
    if typ == "relation":
        if not isinstance(v, list):
            return ""
        names = (ctx.titles(x) for x in v)                      # None: nicht sichtbar bzw. nicht im Export
        return ", ".join((n or t("Unbenannt")) for n in names if n is not None)
    if typ in ("number", "calc"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return _generic(v)
        if csv:
            return num_text(v, ctx.decimal)
        v = round(v, 2) if isinstance(v, float) else v
        return f"{v} %" if p.get("fmt") == "percent" else f"{v} €" if p.get("fmt") == "euro" else str(v)
    if typ in ("select", "multi_select"):
        names = {o.get("id"): o.get("name", "") for o in p.get("options", []) if isinstance(o, dict)}
        return ", ".join(names.get(x, "") for x in (v if isinstance(v, list) else [v]) if not csv or names.get(x))
    if typ == "checkbox":
        return "✓" if v else ""
    if typ == "date" and isinstance(v, str) and not csv:     # 2.5: Uhrzeit/Zeitraum lesbar (CSV: ISO-Wert unveraendert)
        return i18n.date_value_str(v, ctx.tz) if len(v) > 10 else v
    return _generic(v)


def _fx_text(p, v, ctx):
    """Formel/Rollup (2.5, Paket 1): fertiger Wert aus saga.dbcalc (Zahl, Text, Haekchen, Datum 'JJJJ-MM-TT', Liste von
    Texten). Liste -> mit ", " verbunden; Zahl wie eine Zahl-Eigenschaft (fmt), Haekchen wie checkbox."""
    csv = ctx.mode == "csv"
    if isinstance(v, bool):
        return ("true" if v else "false") if csv else ("✓" if v else "")
    if isinstance(v, (int, float)):
        if csv:
            return num_text(v, ctx.decimal)
        v = round(v, 2) if isinstance(v, float) else v
        return f"{v} %" if p.get("fmt") == "percent" else f"{v} €" if p.get("fmt") == "euro" else str(v)
    if isinstance(v, list):
        return ", ".join(s for s in (_fx_text(p, x, ctx) if not isinstance(x, (list, dict)) else _generic(x) for x in v) if s)
    return _generic(v)


register("formula", _fx_text)
register("rollup", _fx_text)
