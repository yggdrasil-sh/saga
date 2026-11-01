"""Erzeugt tests/vectors/pm_schema.json (Saga-Schema v1, Tiptap-Umbau T1) aus dem Server-Modell saga/collab_model.py.

    python3 tools/pm_vectors_gen.py            # schreibt die Datei neu
    python3 tools/pm_vectors_gen.py --check    # nur vergleichen (Ausgang 1 bei Abweichung)

Je Fall: blocks (server-kanonisch: clean_blocks-fest), pm (collab_model.doc_to_pm), out (Ergebnis Block-JSON -> Y.Doc ->
Block-JSON mit dem Resolver der Datei, nur wenn != blocks), html_norm ([Eingabe, Normalform] je Textfeld), y (Y-Struktur
in fester Schreibweise, nur bei den eigenen Faellen), upgrade (Altformat-Toggle mit ctx.upgrade im Web-Adapter).
Quellen: tests/vectors/editor_inline.json (sanitize), tests/vectors/merge.json, gallery/*.json, Sonderformen aus
tests/crdt_modell.py und eigene Faelle je Knoten/Marke/Altformat/Grenzwert. Gepruefte Regeln: tests/pm_vectors.py,
tools/pm_vectors.mjs."""
import copy, glob, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pycrdt import Doc, XmlElement, XmlFragment, XmlText  # noqa: E402
from saga import collab_model as M  # noqa: E402
from saga.pages import clean_blocks  # noqa: E402

OUT = os.path.join(ROOT, "tests", "vectors", "pm_schema.json")
RESOLVE = {"pages": {"3": "Seite drei", "12": "Titel", "77": "Neu"},
           "people": {"~abcdefghijklmnop": "Anna", "@bob@b.example": "Bob"},
           "page_default": "Seite ohne Zugriff", "mention_default": ""}


class Res:
    def __init__(self, r=RESOLVE):
        self.r = r

    def page(self, bid, pid, i):
        return self.r["pages"].get(str(pid), self.r["page_default"])

    def mention(self, bid, u, i):
        return self.r["people"].get(u, self.r["mention_default"])


def stable(v):
    return json.dumps(M._num(v), sort_keys=True, ensure_ascii=False)


def canon(blocks):
    return json.loads(clean_blocks(json.dumps({"blocks": blocks}, ensure_ascii=False))[0])["blocks"]


def with_ids(blocks, prefix):
    """Fehlende/doppelte IDs fest vergeben (clean_blocks wuerde Zufalls-IDs setzen)."""
    seen, n = set(), [0]

    def walk(lst):
        for b in lst if isinstance(lst, list) else []:
            if not isinstance(b, dict):
                continue
            bid = b.get("id")
            if not isinstance(bid, str) or bid in seen:
                n[0] += 1
                b["id"] = f"{prefix}{n[0]}"
            seen.add(b["id"])
            d = b.get("data") if isinstance(b.get("data"), dict) else {}
            if b.get("type") == "columns":
                for c in d.get("cols") or []:
                    if isinstance(c, dict):
                        walk(c.get("blocks"))
            elif isinstance(d.get("blocks"), list):
                walk(d["blocks"])
    blocks = copy.deepcopy(blocks)
    walk(blocks)
    return blocks


def y_xml(frag):
    """Y-Struktur in fester Schreibweise (Attribute sortiert, Werte als JSON, Text je Lauf [text, marken])."""
    def el(x):
        if isinstance(x, XmlText):
            return "[" + ",".join(json.dumps([s, M._num(a) or {}], sort_keys=True, ensure_ascii=False) for s, a in x.diff()) + "]"
        a = " ".join(f"{k}={json.dumps(M._num(v), sort_keys=True, ensure_ascii=False)}" for k, v in sorted(x.attributes))
        return f"<{x.tag}{' ' + a if a else ''}>" + "".join(el(c) for c in x.children) + f"</{x.tag}>"
    return "".join(el(c) for c in frag.children)


def y_of(blocks):
    d = Doc()
    d.apply_update(M.build({"blocks": blocks}))
    return d


def upgrade_toggle(blocks):
    """Wie der Web-Adapter mit ctx.upgrade (und ToggleTool): Toggle {text} ohne blocks -> Titel + erster Absatz."""
    out = []
    for b in blocks:
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        if b.get("type") == "toggle" and "text" in d and not isinstance(d.get("blocks"), list):
            nd = {k: v for k, v in d.items() if k != "text"}
            nd.setdefault("title", "")
            nd["blocks"] = [{"type": "paragraph", "data": {"text": d["text"]}}] if isinstance(d["text"], str) and d["text"] else []
            b = dict(b, data=nd)
        out.append(b)
    return out


# --- Faelle ------------------------------------------------------------------------------------------------------------
def P(i, t, **kw):
    return dict({"id": i, "type": "paragraph", "data": {"text": t}}, **kw)


def own_cases():
    c = []
    add = lambda name, blocks, **kw: c.append(dict(name=name, blocks=blocks, own=True, **kw))  # noqa: E731
    # Knoten
    add("Knoten paragraph", [P("p1", "Hallo Welt")])
    add("Knoten paragraph leer", [P("p1", "")])
    add("Knoten header 1-4", [{"id": f"h{n}", "type": "header", "data": {"text": f"H{n}", "level": n}} for n in (1, 2, 3, 4)])
    add("Knoten quote", [{"id": "q1", "type": "quote", "data": {"text": "Zitat <i>x</i>", "caption": "Quelle", "alignment": "center"}}])
    add("Knoten code", [{"id": "c1", "type": "code", "data": {"code": "if a < b && c:\n\treturn '<b>'", "language": "python"}}])
    add("Knoten code mit view und render", [{"id": "c1", "type": "code", "data": {"code": "graph TD; A-->B", "language": "mermaid",
                                                                              "view": "split"}}])
    add("Knoten code leer", [{"id": "c1", "type": "code", "data": {"code": "", "language": "plain"}}])
    add("Knoten delimiter", [{"id": "d1", "type": "delimiter", "data": {}}])
    add("Knoten callout", [{"id": "k1", "type": "callout", "data": {"emoji": "\u26a0\ufe0f", "text": "Achtung <b>wichtig</b>"}}])
    add("Knoten toggle mit Inhalt", [{"id": "t1", "type": "toggle", "data": {"title": "Mehr", "open": False, "blocks": [
        P("t2", "drin"), {"id": "t3", "type": "toggle", "data": {"title": "innen", "open": True, "level": 1, "blocks": [P("t4", "tief")]}}]}}])
    add("Knoten toggle leer", [{"id": "t1", "type": "toggle", "data": {"title": "", "open": True, "blocks": []}}])
    add("Knoten columns 2 mit Breiten", [{"id": "co", "type": "columns", "data": {"cols": [
        {"w": 0.4, "blocks": [P("c1", "links")]}, {"w": 0.6, "blocks": [P("c2", "rechts"), {"id": "c3", "type": "toggle", "data": {
            "title": "T", "open": True, "blocks": []}}]}]}}])
    add("Grenzwert columns 6", [{"id": "co", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", str(i))]} for i in range(6)]}}])
    add("Grenzwert columns 1 (Server laesst durch)", [{"id": "co", "type": "columns", "data": {"cols": [{"blocks": [P("c1", "a")]}]}}])
    add("Grenzwert columns 8 (Server laesst durch)", [{"id": "co", "type": "columns", "data": {"cols": [{"blocks": []} for _ in range(8)]}}])
    add("Grenzwert columns leer", [{"id": "co", "type": "columns", "data": {"cols": []}}])
    add("Knoten synced Original und Kopie", [
        {"id": "s1", "type": "synced", "data": {"sid": "AbCdEfGh12345678", "blocks": [P("s2", "geteilt"), {"id": "s3", "type": "toggle", "data": {
            "title": "x", "open": True, "blocks": []}}]}},
        {"id": "s4", "type": "synced", "data": {"sid": "ZyXwVuTs98765432"}}])
    add("Knoten list ungeordnet verschachtelt", [{"id": "l1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
        {"content": "eins", "meta": {}, "items": [{"content": "eins.a", "meta": {}, "items": [{"content": "tief", "meta": {}, "items": []}]}]},
        {"content": "<b>zwei</b>", "meta": {}, "items": []}]}}])
    add("Knoten list geordnet mit start", [{"id": "l1", "type": "list", "data": {"style": "ordered", "meta": {"start": 4, "counterType": "upper-roman"},
                                                                                 "items": [{"content": "vier", "meta": {}, "items": []}]}}])
    add("Knoten list Checkliste", [{"id": "l1", "type": "list", "data": {"style": "checklist", "meta": {}, "items": [
        {"content": "erledigt", "meta": {"checked": True}, "items": []}, {"content": "offen", "meta": {"checked": False}, "items": []}]}}])
    add("Grenzwert list leer", [{"id": "l1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": []}}])
    add("Knoten table", [{"id": "tb", "type": "table", "data": {"withHeadings": True, "headCol": True, "content": [["<b>A</b>", "B"], ["1", ""]],
                                                                "colWidths": [30, 70], "colors": {"r": {"0": "blue"}, "x": {"1,1": "red"}}, "stretched": True}}])
    add("Grenzwert table 20 Spalten", [{"id": "tb", "type": "table", "data": {"withHeadings": False, "content": [
        [f"z{r}s{s}" for s in range(20)] for r in range(2)], "colWidths": [5] * 20}}])
    add("Grenzwert table leer", [{"id": "tb", "type": "table", "data": {"withHeadings": False, "content": []}},
                                 {"id": "tc", "type": "table", "data": {"withHeadings": False, "content": [[]]}}])
    add("Knoten image", [{"id": "im", "type": "image", "data": {"file": {"url": "/api/files/1/a.png", "name": "a.png"}, "caption": "Bild <i>1</i>",
                                                                "withBorder": False, "withBackground": True, "stretched": False, "width": 60, "align": "center"}}])
    add("Knoten math", [{"id": "m1", "type": "math", "data": {"tex": "\\int_0^1 x^2 \\,dx < 1"}}])
    add("Knoten undurchsichtig (alle)", [{"id": f"o{i}", "type": t, "data": {"v": i, "nested": {"a": [1, 2.5, None, True]}, "html": "<b>x</b>"}}
                                         for i, t in enumerate(M.OPAQUE)])
    add("Knoten unknown (neuer Typ, altes checklist)", [{"id": "u1", "type": "neuertyp", "data": {"irgendwas": [1, {"a": "b"}]}},
                                                        {"id": "u2", "type": "checklist", "data": {"items": [{"text": "a", "checked": True}]}},
                                                        {"id": "u3", "type": "unknown", "data": {"x": 1}},
                                                        {"id": "u4", "type": "synced_ref", "data": {"sid": "x"}}])
    add("Tunes Farbe und Konflikt", [P("a", "rot", tunes={"hkstyle": {"c": "red", "bg": True}}),
                                     P("b", "", tunes={"hkconflict": {"by": "~aaaaaaaaaaaaaaaa", "name": "M", "at": 1.0, "of": "a"}})])
    add("unbekannte Felder bleiben (Regel 0)", [P("a", "x", ), {"id": "b", "type": "header", "data": {"text": "y", "level": 2, "neu": {"k": [1]}}},
                                                {"id": "c", "type": "columns", "data": {"cols": [{"w": 0.5, "blocks": [], "neu": 1}, {"w": 0.5, "blocks": []}]}},
                                                {"id": "d", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
                                                    {"content": "a", "meta": {}, "items": [], "neu": True}, {"content": "b", "meta": {}}]}}])
    add("fehlende Felder bleiben fehlend", [{"id": "a", "type": "paragraph", "data": {}}, {"id": "b", "type": "quote", "data": {"text": "x"}},
                                            {"id": "c", "type": "toggle", "data": {"open": True}}, {"id": "d", "type": "list", "data": {"style": "ordered"}},
                                            {"id": "e", "type": "columns", "data": {"cols": [{"w": 0.5}, {"w": 0.5, "blocks": []}]}},
                                            {"id": "f", "type": "table", "data": {}}, {"id": "g", "type": "image", "data": {"file": {"url": "/x"}}},
                                            {"id": "h", "type": "code", "data": {"language": "js"}}])
    add("falsche Feldtypen wandern nach extra", [{"id": "a", "type": "paragraph", "data": {"text": 5}}, {"id": "b", "type": "header", "data": {"text": None, "level": 2}},
                                                 {"id": "c", "type": "list", "data": {"style": "ordered", "meta": {}, "items": [{"content": 3}]}},
                                                 {"id": "d", "type": "table", "data": {"content": [["a", 1]]}},
                                                 {"id": "e", "type": "toggle", "data": {"title": "t", "blocks": "kaputt"}}])
    # Marken einzeln und kombiniert
    for name, html in (("b", "<b>x</b>"), ("i", "<i>x</i>"), ("u", "<u>x</u>"), ("s", "<s>x</s>"), ("icode", '<code class="inline-code">x()</code>'),
                       ("mark", '<mark class="cdx-marker">x</mark>'), ("bg", '<mark class="hk-bg-green">x</mark>'), ("color", '<span class="hk-c-purple">x</span>'),
                       ("link", '<a href="https://x.example/?a=1&amp;b=%22" target="_blank" rel="noopener noreferrer">x</a>'),
                       ("link mailto", '<a href="mailto:a@b.example" target="_blank" rel="noopener noreferrer">x</a>')):
        add(f"Marke {name}", [P("p", f"a {html} z")], html_norm=[[f"a {html} z", f"a {html} z"]])
    rank = ('<a href="https://x.example" target="_blank" rel="noopener noreferrer"><span class="hk-c-red"><mark class="hk-bg-blue"><b><i><u><s>'
            '<code class="inline-code">alle</code></s></u></i></b></mark></span></a>')
    add("Marken alle in Rangfolge", [P("p", rank)], html_norm=[[rank, rank]])
    for name, src, norm in (
            ("falsche Reihenfolge", "<i><b>q</b></i>", "<b><i>q</i></b>"),
            ("Farbe innen", '<b><span class="hk-c-red">r</span></b>', '<span class="hk-c-red"><b>r</b></span>'),
            ("Link innen", '<b><a href="https://x.example" target="_blank" rel="noopener noreferrer">l</a></b>',
             '<a href="https://x.example" target="_blank" rel="noopener noreferrer"><b>l</b></a>'),
            ("Laeufe teilen Marken", "<b>a<i>b</i>c</b>", "<b>a<i>b</i>c</b>"),
            ("Laeufe getrennt gleich", "<b>a</b><b>b</b>", "<b>ab</b>"),
            ("Farbe in Farbe", '<span class="hk-c-red">a<span class="hk-c-blue">b</span>c</span>',
             '<span class="hk-c-red">a</span><span class="hk-c-blue">b</span><span class="hk-c-red">c</span>'),
            ("bg schliesst mark aus", '<mark class="cdx-marker">a<mark class="hk-bg-red">b</mark></mark>',
             '<mark class="cdx-marker">a</mark><mark class="hk-bg-red">b</mark>'),
            ("strong em (Altformat)", "<strong>f</strong> <em>k</em>", "<b>f</b> <i>k</i>"),
            ("mark ohne Klasse (Altformat)", "<mark>m</mark>", '<mark class="cdx-marker">m</mark>'),
            ("geschuetzte Leerzeichen", "a\u00a0b &amp; &lt;c&gt;", "a\u00a0b &amp; &lt;c&gt;"),
            ("Zeilenumbruch am Ende", "a<br>", "a<br>"),
            ("Zeilenumbrueche fett", "<b>a<br>b</b><br>", "<b>a<br>b</b><br>"),
            ("Emoji und Thai", "\U0001F1E9\U0001F1EA \U0001F44D\U0001F3FD \u0e01\u0e34\u0e48\u0e07 \u00e9", "\U0001F1E9\U0001F1EA \U0001F44D\U0001F3FD \u0e01\u0e34\u0e48\u0e07 \u00e9")):
        add(f"Normalform {name}", [P("p", src)], html_norm=[[src, norm]])
    # Inline-Knoten
    inl = ('<span class="hk-mention" data-u="~abcdefghijklmnop">Anna</span> und <span class="hk-mention" data-u="@bob@b.example">Bob</span>, '
           '<a href="/notes#3">Seite drei</a>, <a href="/notes#999">Seite ohne Zugriff</a>, '
           '<time datetime="2026-10-10" class="hk-date">Sa. 10.10.</time>, <time datetime="2026-10-11T09:30" class="hk-remind">So. 9:30</time>, '
           '<code class="hk-math">x&lt;y</code>, a<br>b')
    add("Inline-Knoten alle", [P("p", inl)], html_norm=[[inl, inl]])
    marked = ('<span class="hk-c-blue"><a href="/notes#12">Titel</a> <b><span class="hk-mention" data-u="~abcdefghijklmnop">Anna</span></b> '
              '<code class="hk-math">e^x</code></span>')
    add("Inline-Knoten mit Marken", [P("p", marked)], html_norm=[[marked, marked]])
    add("Verweistext wird aufgeloest", [P("p", '<a href="/notes#12">alter Titel</a> <span class="hk-mention" data-u="~abcdefghijklmnop">alt</span>')])
    add("Inline in allen Textfeldern", [
        {"id": "q", "type": "quote", "data": {"text": "<b>t</b>", "caption": "<i>c</i> <a href=\"/notes#3\">Seite drei</a>", "alignment": "left"}},
        {"id": "t", "type": "toggle", "data": {"title": "<u>T</u>", "open": True, "blocks": []}},
        {"id": "l", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [{"content": "<s>x</s><br>y", "meta": {}, "items": []}]}},
        {"id": "tb", "type": "table", "data": {"withHeadings": False, "content": [["<code class=\"inline-code\">c</code>"]]}},
        {"id": "im", "type": "image", "data": {"file": {"url": "/x"}, "caption": "<mark class=\"cdx-marker\">m</mark>"}},
        {"id": "ca", "type": "callout", "data": {"emoji": "x", "text": "<time datetime=\"2026-01-01\" class=\"hk-date\">1.1.</time>"}}])
    # Altformate
    add("Altformat Listenpunkte als Text", [{"id": "l1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
        "alt <b>fett</b>", {"content": "neu", "meta": {}, "items": ["innen alt"]}]}}])
    add("Altformat Toggle {text}", [{"id": "t1", "type": "toggle", "data": {"text": "alter <b>Inhalt</b>", "open": True}}], upgrade=True)
    add("Altformat Toggle {text} mit Titel", [{"id": "t1", "type": "toggle", "data": {"title": "Kopf", "text": "rest"}}], upgrade=True)
    return c


def gather():
    cases = []
    I = json.load(open(os.path.join(ROOT, "tests", "vectors", "editor_inline.json"), encoding="utf-8"))
    for name, inp, out in I["sanitize"]:
        cases.append({"name": f"inline {name}", "blocks": [P("p1", out), {"id": "l1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
            {"content": out, "meta": {}, "items": []}]}}, {"id": "t1", "type": "table", "data": {"withHeadings": False, "content": [[out, "b"]]}}],
            "html_norm": [[out, None]]})
    V = json.load(open(os.path.join(ROOT, "tests", "vectors", "merge.json"), encoding="utf-8"))
    seen = set()
    for x in V["docs"]:
        for k in ("base", "mine", "theirs", "expect"):
            if isinstance(x.get(k), dict):
                key = stable(x[k]["blocks"])
                if key not in seen:
                    seen.add(key)
                    cases.append({"name": f"merge {x['name']} ({k})", "blocks": x[k]["blocks"]})
    for f in sorted(glob.glob(os.path.join(ROOT, "gallery", "*.json"))):
        found = []

        def find(o):
            if isinstance(o, dict):
                if isinstance(o.get("blocks"), list) and o["blocks"] and isinstance(o["blocks"][0], dict) and "type" in o["blocks"][0]:
                    found.append(o["blocks"])
                    return
                for v in o.values():
                    find(v)
            elif isinstance(o, list):
                for v in o:
                    find(v)
        find(json.load(open(f, encoding="utf-8")))
        for i, bl in enumerate(found):
            cases.append({"name": f"galerie {os.path.basename(f)} #{i}", "blocks": bl})
    cases += own_cases()
    return cases


def build():
    R = Res()
    out = []
    names = set()
    for i, c in enumerate(gather()):
        assert c["name"] not in names, c["name"]
        names.add(c["name"])
        blocks = canon(with_ids(c["blocks"], f"v{i}x"))
        v = {"name": c["name"], "blocks": blocks, "pm": M.doc_to_pm({"blocks": blocks})}
        d = y_of(blocks)
        got, _ = M.read(d, R)
        if stable(got["blocks"]) != stable(blocks):
            v["out"] = got["blocks"]
        if c.get("html_norm"):
            v["html_norm"] = [[a, b if b is not None else M.inline_to_html(M.html_to_inline(a), R, "p1")] for a, b in c["html_norm"]]
        if c.get("own"):
            v["y"] = y_xml(d.get(M.FRAGMENT, type=XmlFragment))
        if c.get("upgrade"):
            ub = upgrade_toggle(blocks)
            ug, _ = M.read(y_of(ub), R)
            v["upgrade"] = {"pm": M.doc_to_pm({"blocks": ub}), "out": ug["blocks"]}
        out.append(v)
    return {"_doc": "Saga-Schema v1 (Tiptap/ProseMirror <-> Block-JSON <-> Y.Doc), erzeugt von tools/pm_vectors_gen.py aus "
                    "saga/collab_model.py - nicht von Hand aendern. Pruefer: tools/pm_vectors.mjs (Web-Adapter static/pm-adapter.js), "
                    "tests/pm_vectors.py (Server-Modell), tools/pm_ytest.mjs (y-tiptap gegen pycrdt). Felder siehe docs/EDITOR-PM.md.",
            "model": M.MODEL, "schema_ver": M.SCHEMA_VER, "schema_hash": M.SCHEMA_HASH, "resolve": RESOLVE, "cases": out}


if __name__ == "__main__":
    data = build()
    txt = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"     # sortiert: clean_blocks ordnet nicht fest
    if "--check" in sys.argv:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else ""
        print("pm_schema.json aktuell" if cur == txt else "pm_schema.json VERALTET (python3 tools/pm_vectors_gen.py)")
        sys.exit(0 if cur == txt else 1)
    open(OUT, "w", encoding="utf-8").write(txt)
    print(f"{OUT}: {len(data['cases'])} Faelle")
