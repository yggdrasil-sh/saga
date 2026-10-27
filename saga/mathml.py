"""TeX -> MathML fuer veroeffentlichte Seiten (2.5, P6-1). Veroeffentlichte Seiten laufen ohne JavaScript (CSP
script-src 'none'), Browser zeigen MathML nativ. Umwandlung mit latex2mathml (reines Python, MIT); die Ausgabe wird
NICHT uebernommen, sondern ueber eine Positivliste neu geschrieben: nur Praesentations-Elemente, wenige Attribute mit
harmlosen Werten (kein href, kein maction, keine Farben, kein style), Text immer escaped."""
import re
from html import escape, unescape

try:
    from latex2mathml.converter import convert_to_element
except ImportError:                       # Abhaengigkeit fehlt (alte Installation): Mathe bleibt Code
    convert_to_element = None

TEX_MAX = 4000
ELEMENTS = frozenset("math mrow mi mn mo ms mtext mspace msub msup msubsup mfrac msqrt mroot mover munder munderover "
                     "mtable mtr mtd mlabeledtr mstyle mpadded mphantom menclose merror mmultiscripts mprescripts none".split())
DROP = frozenset("script style annotation annotation-xml semantics maction foreignObject svg".split())   # samt Inhalt weg
ATTRS = frozenset("mathvariant stretchy fence separator lspace rspace accent accentunder movablelimits largeop symmetric "
                  "minsize maxsize linethickness columnalign rowalign columnspacing rowspacing columnlines rowlines frame "
                  "displaystyle scriptlevel notation width height depth form mathsize align".split())
_VAL = re.compile(r"[A-Za-z0-9 .%+#-]{0,60}")
_DEPTH = 60


def _tag(el):
    t = el.tag if isinstance(el.tag, str) else ""
    return t.rsplit("}", 1)[-1]


def _write(el, out, depth):
    tag = _tag(el)
    if depth > _DEPTH or tag in DROP:
        return
    if tag not in ELEMENTS:                   # Unbekanntes: Element weg, Kinder bleiben (in einer Gruppe)
        tag = "mrow"
    attrs = ""
    if tag != "math":
        for k, v in sorted(el.attrib.items()):
            k = k.rsplit("}", 1)[-1]
            if k in ATTRS and isinstance(v, str) and _VAL.fullmatch(v):
                attrs += f' {k}="{escape(v, quote=True)}"'
    out.append(f"<{tag}{attrs}>")
    if el.text:
        out.append(escape(unescape(el.text), quote=False))
    for ch in el:
        _write(ch, out, depth + 1)
        if ch.tail:
            out.append(escape(unescape(ch.tail), quote=False))
    out.append(f"</{tag}>")


def to_mathml(tex, block=False):
    """TeX -> bereinigtes MathML (<math>...</math>) oder None (leer, zu lang, nicht lesbar, Bibliothek fehlt)."""
    tex = str(tex or "").strip()
    if not tex or len(tex) > TEX_MAX or convert_to_element is None:
        return None
    try:
        root = convert_to_element(tex, display="block" if block else "inline")
    except Exception:  # noqa: BLE001 - fehlerhaftes TeX, Rekursion: Rueckfall auf Code
        return None
    out = []
    for ch in root:
        _write(ch, out, 1)
        if ch.tail:
            out.append(escape(unescape(ch.tail), quote=False))
    if not out:
        return None
    disp = ' display="block"' if block else ""
    return f'<math xmlns="http://www.w3.org/1998/Math/MathML"{disp}>' + "".join(out) + "</math>"
