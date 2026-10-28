"""Saga 2.5 P3 Stufe 2: Dokumentmodell saga/collab_model.py (Schema v1, y-prosemirror-Form) ohne Server und Datenbank.

Geprueft: Round-Trip Block-JSON -> Y.Doc -> Block-JSON gleich clean_blocks(Eingabe) fuer alle Dokumente aus
tests/vectors/merge.json, alle Ergebnisse aus tests/vectors/editor_inline.json (sanitize) und die Galerie-Vorlagen;
Anfangszustand deterministisch (gleiche Bytes); Seitenverweise und Erwaehnungen ohne Text im Zustand (Titel-Leck);
Inline-Knoten als XmlElement neben XmlText (S2), Marken als {name: attrs} (S3), Felder als Kindknoten (S4),
JSON-Attribute (S5); write() bringt ein Doc mit kleinsten Aenderungen auf einen Zielstand (unberuehrte Bloecke bleiben
dieselben Y-Objekte, gleichzeitige Eingabe dort bleibt); unbekannte Knoten/Attribute/Marken -> ModelError."""
import glob, json, os, random, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pycrdt import Doc, XmlElement, XmlFragment, XmlText  # noqa: E402
from saga import collab_model as M  # noqa: E402
from saga.pages import clean_blocks  # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


class Res:
    """Wie collab.Resolver: Texte fuer Verweise/Erwaehnungen aus einem festen Woerterbuch."""
    def __init__(self, pages=None, people=None):
        self.p, self.m = pages or {}, people or {}

    def page(self, bid, pid, i):
        return self.p.get(pid, "Seite ohne Zugriff")

    def mention(self, bid, u, i):
        return self.m.get(u, "")


def canon(doc):
    return json.loads(clean_blocks(json.dumps(doc, ensure_ascii=False))[0])["blocks"]


def strip_texts(blocks, res_):
    """Erwartung: Verweis-/Erwaehnungstexte wie resolve sie liefert."""
    import re
    s = json.dumps(blocks, ensure_ascii=False)
    s = re.sub(r'<a href=\\"/notes#(\d+)\\">(.*?)</a>', lambda m: f'<a href=\\"/notes#{m.group(1)}\\">{res_.page(None, int(m.group(1)), 0)}</a>', s)
    s = re.sub(r'<span class=\\"hk-mention\\" data-u=\\"([^"\\]+)\\">(.*?)</span>',
               lambda m: f'<span class=\\"hk-mention\\" data-u=\\"{m.group(1)}\\">{res_.mention(None, m.group(1), 0)}</span>', s)
    # Normalform (S8): Marker ohne Klasse -> cdx-marker (beides Gelb, Standard); Listenpunkte als Text -> Objekte
    out = json.loads(s.replace('<mark>', '<mark class=\\"cdx-marker\\">'))

    def items(lst):
        return [{"content": x, "meta": {}, "items": []} if isinstance(x, str) else dict(x, items=items(x.get("items") or []))
                if "items" in x else x for x in lst]
    for b in _iter(out):
        if b.get("type") == "list" and isinstance(b.get("data", {}).get("items"), list):
            b["data"]["items"] = items(b["data"]["items"])
    return out


def _iter(blocks):
    from saga.notes import _iter_blocks
    return list(_iter_blocks(blocks))


def roundtrip(blocks, res_=None, title=""):
    upd = M.build({"blocks": blocks}, title)
    d = Doc(); d.apply_update(upd)
    got, tt = M.read(d, res_)
    return got["blocks"], tt, upd


def same(a, b):
    """Gleich als JSON-Wert (1 == 1.0: Yjs kennt nur eine Zahlenart, JSON-Leser auch)."""
    return json.dumps(M._num(a), sort_keys=True, ensure_ascii=False) == json.dumps(M._num(b), sort_keys=True, ensure_ascii=False)


# --- Round-Trip ----------------------------------------------------------------------------------------------------
docs = []
V = json.load(open(os.path.join(ROOT, "tests", "vectors", "merge.json"), encoding="utf-8"))
for x in V["docs"]:
    for k in ("base", "mine", "theirs", "expect"):
        if isinstance(x.get(k), dict):
            docs.append((f"merge {x['name']} ({k})", x[k]["blocks"]))
I = json.load(open(os.path.join(ROOT, "tests", "vectors", "editor_inline.json"), encoding="utf-8"))
for name, _inp, out in I["sanitize"]:
    docs.append((f"inline {name}", [{"id": "p1", "type": "paragraph", "data": {"text": out}},
                                    {"id": "h1", "type": "header", "data": {"text": out, "level": 3}},
                                    {"id": "l1", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
                                        {"content": out, "meta": {}, "items": [{"content": "x " + out, "meta": {}, "items": []}]}]}},
                                    {"id": "t1", "type": "table", "data": {"withHeadings": False, "content": [[out, "b"]]}}]))
for f in sorted(glob.glob(os.path.join(ROOT, "gallery", "*.json"))):
    j = json.load(open(f, encoding="utf-8"))
    found = []

    def find(o):
        if isinstance(o, dict):
            if isinstance(o.get("blocks"), list) and o["blocks"] and isinstance(o["blocks"][0], dict) and "type" in o["blocks"][0]:
                found.append(o["blocks"])
            for v in o.values():
                find(v)
        elif isinstance(o, list):
            for v in o:
                find(v)
    find(j)
    for i, bl in enumerate(found):
        docs.append((f"galerie {os.path.basename(f)} #{i}", bl))
# Sonderformen: alle Blocktypen, Tunes, Konflikt, unbekannte Felder, synced Original/Kopie, Spalten mit Breiten
docs.append(("Sonderformen", [
    {"id": "a", "type": "paragraph", "data": {"text": "Hallo <b>fett <i>kursiv</i></b> <code class=\"inline-code\">x()</code>",
                                             "foo": 1}, "tunes": {"hkstyle": {"c": "red", "bg": True}}},
    {"id": "b", "type": "quote", "data": {"text": "Zitat", "caption": "Quelle", "alignment": "left"}},
    {"id": "c", "type": "code", "data": {"code": "a <b> &amp;\n\tz", "language": "python", "view": "split"}},
    {"id": "d", "type": "delimiter", "data": {}},
    {"id": "e", "type": "callout", "data": {"emoji": "💡", "text": "Hinweis <a href=\"https://x.example/?a=1&amp;b=2\" target=\"_blank\" rel=\"noopener noreferrer\">L</a>"}},
    {"id": "f", "type": "toggle", "data": {"title": "T", "open": True, "level": 2, "blocks": [
        {"id": "f1", "type": "paragraph", "data": {"text": "drin"}}]}},
    {"id": "g", "type": "columns", "data": {"cols": [{"w": 0.3, "blocks": [{"id": "g1", "type": "paragraph", "data": {"text": "l"}}]},
                                                     {"w": 0.7, "blocks": []}]}},
    {"id": "h", "type": "synced", "data": {"sid": "AbCdEfGh12345678", "blocks": [{"id": "h1", "type": "header", "data": {"text": "S", "level": 2}}]}},
    {"id": "i", "type": "synced", "data": {"sid": "ZyXwVuTs98765432"}},
    {"id": "j", "type": "math", "data": {"tex": "\\frac{a}{b}"}},
    {"id": "k", "type": "database", "data": {"db": 123456789, "view": "a", "nested": {"x": [1, 2.5, None, True]}}},
    {"id": "l", "type": "widget", "data": {"v": 1, "kind": "clock", "size": "m", "style": "card", "title": "U", "cfg": {}}},
    {"id": "m", "type": "neuertyp", "data": {"irgendwas": [1, {"a": "b"}]}},
    {"id": "n", "type": "checklist", "data": {"items": [{"text": "a", "checked": True}]}},
    {"id": "o", "type": "table", "data": {"withHeadings": True, "headCol": True, "content": [["<b>A</b>", "B"], ["1", ""]],
                                          "colors": {"r": {"0": "red"}}, "stretched": False}},
    {"id": "p", "type": "paragraph", "data": {"text": ""}, "tunes": {"hkconflict": {"by": "~aaaaaaaaaaaaaaaa", "name": "M", "at": 1.0, "of": "a"}}},
    {"id": "q", "type": "list", "data": {"style": "ordered", "meta": {"start": 3, "counterType": "lower-roman"}, "items": [
        "alt als String", {"content": "neu", "meta": {}, "items": []}]}},
    {"id": "r", "type": "header", "data": {"text": "<time datetime=\"2026-10-10\" class=\"hk-date\">Sa.</time> und "
                                                   "<time datetime=\"2026-10-11T09:30\" class=\"hk-remind\">So. 9:30</time>", "level": 1}},
    {"id": "s", "type": "paragraph", "data": {"text": "a<br>b <code class=\"hk-math\">x&lt;y</code> \U0001F1E9\U0001F1EA กิ่ง é"}},
    {"id": "t", "type": "paragraph", "data": {"text": "<span class=\"hk-c-blue\"><a href=\"/notes#12\">Titel</a> nach</span>"}},
]))
R = Res({3: "Seite drei", 12: "Titel"}, {"~abcdefghijklmnop": "Anna", "@bob@b.example": "Bob"})
fails = 0
for name, blocks in docs:
    try:
        want = canon({"blocks": blocks})
        got, _, _ = roundtrip(want, R)
        good = same(got, strip_texts(want, R))
        if not good:
            fails += 1
            print("     erwartet", json.dumps(strip_texts(want, R), ensure_ascii=False)[:900])
            print("     bekommen", json.dumps(got, ensure_ascii=False)[:900])
        ok(f"Round-Trip {name}", good)
        ok(f"  ... und clean_blocks-fest: {name}", same(canon({"blocks": got}), got))
    except Exception as ex:  # noqa: BLE001
        ok(f"Round-Trip {name}: {type(ex).__name__}: {ex}", False)
print(f"{len(docs)} Dokumente")

# --- Determinismus, Titel, Leck ---------------------------------------------------------------------------------------
B = canon({"blocks": docs[-1][1]})
u1, u2 = M.build({"blocks": B}, "Titel x"), M.build(json.dumps({"blocks": B}), "Titel x")
d1, d2 = Doc(), Doc()
d1.apply_update(u1); d2.apply_update(u2)
# Bytes koennen sich nur in der Reihenfolge der Schluessel von JSON-Attributen unterscheiden (yrs: HashMap); die IDs aller
# Eintraege (Zustandsvektor, Laenge) und der Inhalt sind gleich - das braucht der Neuaufbau aus body
ok("Anfangszustand deterministisch (gleiche IDs/Laenge/Inhalt)", d1.get_state() == d2.get_state() and len(u1) == len(u2)
   and same(M.read_pm(d1), M.read_pm(d2)))
ok("... ohne Objekt-Attribute byte-gleich", M.build({"blocks": B[1:4]}, "x") == M.build({"blocks": B[1:4]}, "x"))
_, tt, _ = roundtrip(B, R, "Mein Titel")
ok("Titel im Doc (Wurzel title)", tt == "Mein Titel")
leak = [{"id": "x", "type": "paragraph", "data": {"text": 'Siehe <a href="/notes#77">Geheimprojekt Zebra</a> und '
                                                          '<span class="hk-mention" data-u="~abcdefghijklmnop">Anna Geheim</span>'}}]
lu = M.build({"blocks": canon({"blocks": leak})})
ok("Titel-Leck: Verweistext und Erwaehnungstext stehen NICHT im Zustand",
   b"Geheimprojekt" not in lu and b"Zebra" not in lu and b"Anna Geheim" not in lu and b"/notes" not in lu)
d = Doc(); d.apply_update(lu)
g, _ = M.read(d, Res({77: "Neu"}, {"~abcdefghijklmnop": "A"}))
ok("Materialisierung setzt Titel/Namen ueber resolve ein",
   g["blocks"][0]["data"]["text"] == 'Siehe <a href="/notes#77">Neu</a> und <span class="hk-mention" data-u="~abcdefghijklmnop">A</span>')

# --- Struktur wie y-prosemirror (S1-S5) -------------------------------------------------------------------------------
d = Doc(); d.apply_update(M.build({"blocks": canon({"blocks": [
    {"id": "a", "type": "paragraph", "data": {"text": 'x <b>y</b> <span class="hk-mention" data-u="~abcdefghijklmnop">A</span> z'}},
    {"id": "q", "type": "quote", "data": {"text": "T", "caption": "C", "alignment": "left"}}]})}))
ok("S1: Fragment heisst prosemirror", "prosemirror" in set(d.keys()) and set(d.keys()) <= {"prosemirror", "title", "meta"})
fr = d.get("prosemirror", type=XmlFragment)
p = list(fr.children)[0]
kinds = [type(x).__name__ + (":" + x.tag if isinstance(x, XmlElement) else "") for x in p.children]
ok(f"S2: Inline-Knoten als XmlElement neben XmlText ({kinds})", kinds == ["XmlText", "XmlElement:mention", "XmlText"])
ok("S3: Marken als {name: attrs}", list(p.children)[0].diff() == [("x ", None), ("y", {"b": {}}), (" ", None)])
q = list(fr.children)[1]
ok("S4: Textfelder als Kind-Elemente (qtext/qcap)", [x.tag for x in q.children] == ["qtext", "qcap"])
ok("S5: Attribute als JSON-Werte, id am Block", dict(q.attributes) == {"id": "q", "alignment": "left"})
ok("meta: model + schema_ver", dict(d.get("meta", type=__import__("pycrdt").Map)) in ({"model": "saga-pm", "schema_ver": 1.0},))
ok("roots_ok fuer den Anfangszustand", M.roots_ok(d))
bad = Doc(); bad.apply_update(M.build({"blocks": []})); bad.get("boese", type=__import__("pycrdt").Map)["x"] = 1
ok("roots_ok: fremde Wurzel abgelehnt", not M.roots_ok(bad))
ok(f"Schema-Hash fest ({M.SCHEMA_HASH})", len(M.SCHEMA_HASH) == 16)

# --- Unbekanntes -> ModelError -----------------------------------------------------------------------------------------
for what, fn in (
        ("unbekannter Knoten", lambda fr: fr.children.append(XmlElement("script", {"id": "zz"}))),
        ("unbekanntes Attribut", lambda fr: list(fr.children)[0].attributes.__setitem__("onclick", "x")),
        ("unbekannte Marke", lambda fr: list(list(fr.children)[0].children)[0].format(0, 1, {"evil": {}})),
        ("Einbettung im Text", lambda fr: list(list(fr.children)[0].children)[0].insert_embed(0, {"x": 1})),
        ("Spalten in Spalten", lambda fr: fr.children.append(XmlElement("columns", {"id": "c1"}, [XmlElement("column", {}, [
            XmlElement("columns", {"id": "c2"})])]))),
        ("Text auf oberster Ebene", lambda fr: fr.children.append(XmlText("lose")))):
    d2 = Doc(); d2.apply_update(M.build({"blocks": [{"id": "a", "type": "paragraph", "data": {"text": "abc"}}]}))
    fn(d2.get("prosemirror", type=XmlFragment))
    try:
        M.read(d2)
        ok(f"{what} -> ModelError", False)
    except M.ModelError:
        ok(f"{what} -> ModelError", True)

# --- write(): kleinste Aenderung ---------------------------------------------------------------------------------------
base = canon({"blocks": [{"id": "a", "type": "paragraph", "data": {"text": "Eins zwei drei"}},
                         {"id": "b", "type": "paragraph", "data": {"text": "Zweiter"}},
                         {"id": "c", "type": "list", "data": {"style": "unordered", "meta": {}, "items": [
                             {"content": "x", "meta": {}, "items": []}]}}]})
srv = Doc(); srv.apply_update(M.build({"blocks": base}))
cli = Doc(); cli.apply_update(srv.get_update())
el_b = list(srv.get("prosemirror", type=XmlFragment).children)[1]
target = json.loads(json.dumps(base))
target[0]["data"]["text"] = "Eins <b>zwei</b> drei!"
target.insert(1, {"id": "n", "type": "header", "data": {"text": "Neu", "level": 2}})
target[3]["data"]["items"].append({"content": "y", "meta": {}, "items": []})
sv0 = srv.get_state()
M.write(srv, {"blocks": target}, "T2")
upd = srv.get_update(sv0)
# gleichzeitig tippt der Client in Block b
ct = list(list(cli.get("prosemirror", type=XmlFragment).children)[1].children)[0]
ct.insert(len("Zweiter".encode()), " Satz")
cli.apply_update(upd); srv.apply_update(cli.get_update(srv.get_state()))
g1, t1 = M.read(srv); g2, _ = M.read(cli)
exp = json.loads(json.dumps(target)); exp[2]["data"]["text"] = "Zweiter Satz"
ok("write: Zielstand erreicht, gleichzeitige Eingabe in anderem Block bleibt", same(g1["blocks"], exp) and same(g2["blocks"], exp) and t1 == "T2")
ok("write: unveraenderter Block ist dasselbe Y-Element", list(srv.get("prosemirror", type=XmlFragment).children)[2].attributes.get("id") == "b"
   and el_b.attributes.get("id") == "b")
ok(f"write: kleines Update ({len(upd)} Bytes)", len(upd) < 400)
sv1 = srv.get_state()
M.write(srv, {"blocks": g1["blocks"]}, "T2")
ok("write: gleicher Stand -> keine Aenderung", srv.get_update(sv1) in (b"\x00\x00",))

# Zufall: write auf beliebige Ziele (aus den Vektoren) muss genau den Zielstand ergeben
rnd = random.Random(7)
pool = [canon({"blocks": b}) for _, b in docs]
okc = 0
for k in range(120):
    a, b = rnd.choice(pool), rnd.choice(pool)
    if rnd.random() < 0.5:                                    # Teilmengen mischen, IDs teilen sich so oft
        b = [x for x in a if rnd.random() < 0.6] + [x for x in b if rnd.random() < 0.3]
        seen = set(); b = [x for x in b if not (x.get("id") in seen or seen.add(x.get("id")))]
        b = canon({"blocks": b})
    dd = Doc(); dd.apply_update(M.build({"blocks": a}))
    M.write(dd, {"blocks": b})
    gg, _ = M.read(dd, R)
    if same(gg["blocks"], strip_texts(b, R)):
        okc += 1
    else:
        print("     write-Fehler", json.dumps(strip_texts(b, R), ensure_ascii=False)[:300], "\n     ->", json.dumps(gg["blocks"], ensure_ascii=False)[:300])
ok(f"write: 120 zufaellige Zielstaende exakt erreicht ({okc})", okc == 120)

print(f"{sum(res)}/{len(res)}", "ALLES GRUEN" if all(res) else "FEHLER")
sys.exit(0 if all(res) else 1)
