"""SVG bereinigen (2.5, Trilium-Import): ein SVG wird nur dann als Bild gespeichert, wenn es hier ueber eine Positivliste
neu aufgebaut werden konnte. Nur Standardbibliothek (xml.etree mit expat); keine neue Abhaengigkeit.

Regeln
  - Eingabe hoechstens MAX_BYTES, UTF-8/ASCII. DOCTYPE nur ohne internen Teil ([...]) und ohne ENTITY - er wird vor dem
    Parsen entfernt (keine Entitaeten, keine externen DTDs; expat laedt ohnehin nichts nach). Wurzel muss <svg> sein.
  - Elemente: nur die SVG-Grafikelemente aus ELEMENTS (Namensraum SVG oder ohne Namensraum). Fremde Namensraeume
    (Inkscape, sodipodi, RDF-Metadaten) fallen samt Inhalt weg, ebenso script, Animationen (animate/set ...), a (Inhalt
    bleibt als g), iframe/embed/object usw. foreignObject (HTML im SVG) nur innerhalb von <switch> - dann faellt es weg und
    die SVG-Alternative bleibt; sonst gilt das SVG als zweifelhaft (Inhalt ginge verloren) -> None.
  - Attribute: nur ATTRS (Geometrie/Darstellung), keine on*-Ereignisse, keine Namensraum-Attribute ausser xml:space und
    xlink:href. href/xlink:href nur als Verweis im Dokument (#id); bei <image> zusaetzlich data:image/(png|jpeg|gif|webp);
    base64 - alles andere (http:, javascript:, data:text/html, fremde Dateien) faellt weg (image ohne Ziel: weg).
    Werte mit url(...) nur als url(#id).
  - CSS: im style-Attribut fallen Deklarationen mit url() ausser url(#id), expression(, javascript:, behavior,
    -moz-binding, data:, Backslash-Escapes oder "<" weg. In <style> werden @import und @font-face entfernt und fremde
    url(...) zu none; bleibt danach etwas Verdaechtiges (oder eine @-Regel ausser @media/@keyframes): <style> ganz weg.
  - Kommentare, Verarbeitungsanweisungen, CDATA-Grenzen verschwinden beim Parsen; Text bleibt (beim Schreiben escaped).
  - Grenzen: MAX_NODES Elemente, MAX_DEPTH Verschachtelung - darueber None.
Ausgeliefert wird das Ergebnis wie jede Datei mit CSP "default-src 'none'; sandbox" + nosniff und im Editor nur per <img>
(dort laufen ohnehin keine Skripte und es wird nichts nachgeladen) - die Bereinigung ist die zweite Sicherung."""
import re
import xml.etree.ElementTree as ET

MAX_BYTES = 8 * 1024 * 1024
MAX_NODES = 60_000
MAX_DEPTH = 120
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

ELEMENTS = {
    "svg", "g", "defs", "symbol", "use", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "textPath", "title", "desc", "linearGradient", "radialGradient", "stop", "clipPath", "mask", "pattern", "marker", "image",
    "style", "switch", "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite", "feConvolveMatrix",
    "feDiffuseLighting", "feDisplacementMap", "feDistantLight", "feDropShadow", "feFlood", "feFuncA", "feFuncB", "feFuncG",
    "feFuncR", "feGaussianBlur", "feMerge", "feMergeNode", "feMorphology", "feOffset", "fePointLight", "feSpecularLighting",
    "feSpotLight", "feTile", "feTurbulence"}
_EL_LOW = {e.lower(): e for e in ELEMENTS}
UNWRAP = {"a"}                                     # Element weg, Inhalt bleibt (als g)
ATTRS = {
    "id", "class", "style", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "fx", "fy", "fr", "width", "height",
    "d", "points", "transform", "viewBox", "preserveAspectRatio", "version", "fill", "fill-opacity", "fill-rule", "stroke",
    "stroke-width", "stroke-opacity", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray",
    "stroke-dashoffset", "opacity", "color", "display", "visibility", "overflow", "font-family", "font-size", "font-weight",
    "font-style", "font-variant", "font-stretch", "text-anchor", "dominant-baseline", "alignment-baseline", "baseline-shift",
    "letter-spacing", "word-spacing", "text-decoration", "writing-mode", "direction", "unicode-bidi", "clip-path", "clip-rule",
    "mask", "marker-start", "marker-mid", "marker-end", "markerWidth", "markerHeight", "markerUnits", "refX", "refY", "orient",
    "gradientUnits", "gradientTransform", "spreadMethod", "offset", "stop-color", "stop-opacity", "patternUnits",
    "patternContentUnits", "patternTransform", "maskUnits", "maskContentUnits", "clipPathUnits", "filter", "filterUnits",
    "primitiveUnits", "stdDeviation", "dx", "dy", "in", "in2", "result", "mode", "operator", "values", "type", "k1", "k2",
    "k3", "k4", "flood-color", "flood-opacity", "lighting-color", "color-interpolation", "color-interpolation-filters",
    "rotate", "textLength", "lengthAdjust", "startOffset", "method", "spacing", "vector-effect", "paint-order",
    "shape-rendering", "text-rendering", "image-rendering", "radius", "order", "kernelMatrix", "divisor", "bias", "targetX",
    "targetY", "edgeMode", "preserveAlpha", "surfaceScale", "diffuseConstant", "specularConstant", "specularExponent",
    "azimuth", "elevation", "pointsAtX", "pointsAtY", "pointsAtZ", "limitingConeAngle", "z", "scale", "xChannelSelector",
    "yChannelSelector", "baseFrequency", "numOctaves", "seed", "stitchTiles", "tableValues", "slope", "intercept",
    "amplitude", "exponent", "href", "media", "pathLength", "requiredFeatures", "requiredExtensions", "systemLanguage"}
_DOCTYPE = re.compile(r"<!DOCTYPE\s+svg[^\[>]*>", re.I)
_URL = re.compile(r"url\s*\(", re.I)
_URL_LOCAL = re.compile(r"""url\(\s*(['"]?)#[A-Za-z_][\w.:-]*\1\s*\)""", re.I)
_BAD_CSS = re.compile(r"expression\s*\(|javascript\s*:|vbscript\s*:|behavior\s*:|-moz-binding|@import|@font-face|@namespace"
                      r"|\\|<|&#|data\s*:|image-set\s*\(|cross-fade\s*\(|\bsrc\s*\(|\bimage\s*\(", re.I)
_DATA_IMG = re.compile(r"data:image/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]{1,8000000}", re.I)
_LOCAL_REF = re.compile(r"#[A-Za-z_][\w.:-]{0,200}")


class Doubt(Exception):
    """SVG laesst sich nicht sicher und vollstaendig uebernehmen."""


def looks_like_svg(head):
    """Kopf (erste Bytes) eines SVG: nach BOM/Leerraum <svg bzw. <?xml/Kommentar/DOCTYPE ... <svg."""
    if not head:
        return False
    s = head[:4096].decode("utf-8", "replace").lstrip("﻿ \t\r\n")
    if s.startswith("<svg"):
        return True
    return s.startswith(("<?xml", "<!--", "<!DOCTYPE", "<!doctype")) and re.search(r"<svg[\s>]", s) is not None


def _local_url_only(v):
    """Wert, in dem url(...) nur als url(#id) vorkommt."""
    return len(_URL.findall(v)) == len(_URL_LOCAL.findall(v))


def clean_css_decls(v):
    """style-Attribut -> nur unbedenkliche Deklarationen (oder '')."""
    out = []
    for part in str(v or "").split(";"):
        if ":" not in part:
            continue
        k, val = part.split(":", 1)
        k, val = k.strip().lower(), val.strip()
        if not re.fullmatch(r"-?[a-z][a-z-]{0,40}", k) or not val or len(val) > 2000:
            continue
        if _BAD_CSS.search(val) or not _local_url_only(val):
            continue
        out.append(f"{k}:{val}")
    return ";".join(out)


def clean_css_sheet(txt):
    """Inhalt von <style> -> derselbe Text oder None (verdaechtig: ganzes <style> weg)."""
    s = str(txt or "")
    s = re.sub(r"@import\b[^;{}]*;?", "", s, flags=re.I)                      # Nachladen: Regel weg
    s = re.sub(r"@font-face\s*\{[^{}]*\}", "", s, flags=re.I)
    s = re.sub(r"""url\(\s*(?!['"]?#)[^)]*\)""", "none", s, flags=re.I)      # fremde Adressen -> none
    if len(s) > 512 * 1024 or _BAD_CSS.search(s) or not _local_url_only(s) or "@" in re.sub(r"@(?:media|keyframes)\b", "", s):
        return None                                  # nur @media/@keyframes; @import/@font-face u. a. nie
    return s


def _name(tag):
    """'{ns}name' -> (ns, name)."""
    if tag.startswith("{"):
        ns, _, n = tag[1:].partition("}")
        return ns, n
    return "", tag


def clean(data):
    """SVG-Bytes -> bereinigte SVG-Bytes oder None (kein SVG, zu gross, zweifelhaft)."""
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_BYTES:
        return None
    try:
        txt = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        return None
    txt = txt.lstrip("﻿")
    txt = _DOCTYPE.sub("", txt, count=1)
    if re.search(r"<!DOCTYPE|<!ENTITY|<!ELEMENT|<!ATTLIST|<!NOTATION", txt, re.I):
        return None                                  # interner DTD-Teil/Entitaeten: nie
    try:
        root = ET.fromstring(txt)
    except (ET.ParseError, ValueError, RecursionError):
        return None
    ns, name = _name(root.tag)
    if name != "svg" or ns not in ("", SVG_NS):
        return None
    count = [0]
    try:
        out = _el(root, 0, count, False)
    except (Doubt, RecursionError):
        return None
    if out is None:
        return None
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    body = ET.tostring(out, encoding="unicode", short_empty_elements=True)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8")


def _el(src, depth, count, in_switch):
    """Element neu aufbauen -> neues Element oder None (faellt weg). Doubt: ganzes SVG zweifelhaft."""
    count[0] += 1
    if count[0] > MAX_NODES or depth > MAX_DEPTH:
        raise Doubt()
    ns, name = _name(src.tag)
    if ns not in ("", SVG_NS):
        return None                                  # Inkscape/sodipodi/RDF ...: samt Inhalt weg
    if name.lower() == "foreignobject":
        if in_switch:
            return None                              # Alternative im <switch> bleibt
        raise Doubt()
    unwrap = name in UNWRAP
    real = "g" if unwrap else _EL_LOW.get(name.lower())
    if real is None:
        return None                                  # script, animate, set, iframe, unbekannt: weg
    el = ET.Element(f"{{{SVG_NS}}}{real}")
    for k, v in src.attrib.items():
        an, av = _attr(real, k, v)
        if an is not None:
            el.set(an, av)
    if real == "image" and el.get(f"{{{XLINK_NS}}}href") is None and el.get("href") is None:
        return None
    if real == "style":
        css = clean_css_sheet(src.text)
        if css is None:
            return None
        el.text = css
        return el
    el.text = src.text
    for k in list(src):
        c = _el(k, depth + 1, count, in_switch or real == "switch")
        if c is not None:
            c.tail = k.tail
            el.append(c)
        elif k.tail:                                 # Text nach einem weggefallenen Element bleibt
            if len(el):
                el[-1].tail = (el[-1].tail or "") + k.tail
            else:
                el.text = (el.text or "") + k.tail
    return el


def _attr(el, k, v):
    """Attribut -> (name, wert) oder (None, None)."""
    ns, name = _name(k)
    v = str(v or "")
    if len(v) > 200_000 and name != "d" and name != "points" and not (el == "image" and name == "href"):
        return None, None
    if ns == XML_NS:
        return (f"{{{XML_NS}}}space", v) if name == "space" and v in ("default", "preserve") else (None, None)
    if ns == XLINK_NS:
        if name != "href":
            return None, None
        ok = _href_ok(el, v)
        return (f"{{{XLINK_NS}}}href", v.strip()) if ok else (None, None)
    if ns or name.lower().startswith("on") or name not in ATTRS:
        return None, None
    if name == "href":
        return ("href", v.strip()) if _href_ok(el, v) else (None, None)
    if name == "style":
        css = clean_css_decls(v)
        return ("style", css) if css else (None, None)
    low = v.lower()
    if "javascript:" in low or "vbscript:" in low or "data:" in low or "expression(" in low:
        return None, None
    if not _local_url_only(v):
        return None, None
    return name, v


def _href_ok(el, v):
    v = v.strip()
    if _LOCAL_REF.fullmatch(v):
        return el in ("use", "textPath", "linearGradient", "radialGradient", "pattern", "filter", "feImage", "mpath")
    return el == "image" and _DATA_IMG.fullmatch(v) is not None
