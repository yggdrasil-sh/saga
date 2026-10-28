"""Saga 2.5 P6-0 (Vertrag Editor-Rest): Inline-Formen (Farben aus der Palette, Erwaehnung, Inline-Mathe), Blocktypen
math/synced, Mermaid-Felder, Spalten bis 6, Konfliktmarke hkconflict - Sanitizer, Veroeffentlichen und Markdown gegen die
gemeinsamen Vektoren tests/vectors/editor_inline.json; dazu ueber die API: Speichern, Markenerhalt fuer aeltere Apps
(ohne X-Saga-Caps inline2: Marken auf unveraenderten Zeichen bleiben, P6-3), Spalten-Schutz (cols6), Markdown-Export.
Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from saga import page_tools, publish  # noqa: E402
from saga.pages import clean_blocks, legacy_keep, sanitize_inline, src_hash  # noqa: E402
from saga import mathml  # noqa: E402

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
CK = S.sign(base64.b64encode(json.dumps({"uid": "hk-test-a", "pwc": time.time() + 5}).encode())).decode()
V = json.load(open(os.path.join(ROOT, "tests", "vectors", "editor_inline.json"), encoding="utf-8"))
res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def call(m, p, b=None, hdr=None):
    h = {"Host": HOST, "Content-Type": "application/json", "Cookie": "saga=" + CK, "X-Saga": "1", **(hdr or {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, data=json.dumps(b).encode() if b is not None else None,
                                                           method=m, headers=h), timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())
    except urllib.error.HTTPError as e:
        return e.code, {}


# --- Vektoren (ohne Server) -------------------------------------------------------------------------------------
for name, inp, want in V["sanitize"]:
    got = sanitize_inline(inp)
    ok(f"Sanitizer: {name} ({got!r})", got == want)
    ok(f"Sanitizer stabil: {name}", sanitize_inline(got) == got)
for name, inp, want in V["publish"]:
    got = publish.inline(inp)
    ok(f"Veroeffentlichen: {name} ({got!r})", got == want)
for name, inp, want in V["markdown"]:
    got = page_tools._md_inline(inp, lambda h: h)
    ok(f"Markdown: {name} ({got!r})", got == want)

# --- Blockvertrag (clean_blocks) ---------------------------------------------------------------------------------
P = lambda i, t: {"id": i, "type": "paragraph", "data": {"text": t}}       # noqa: E731
doc = {"blocks": [
    {"id": "m1", "type": "math", "data": {"tex": "a<b & \\frac{1}{2}" + "x" * 5000, "render": {"png": "/files/" + "a" * 24, "h": "0123456789abcdef", "w": 300, "hgt": "x"}}},
    {"id": "m2", "type": "math", "data": {"tex": "x", "render": {"png": "javascript:1", "h": "zz"}}},
    {"id": "c1", "type": "code", "data": {"code": "graph TD; A-->B", "language": "mermaid", "view": "split"}},
    {"id": "c2", "type": "code", "data": {"code": "x", "language": "mermaid", "view": "evil"}},
    {"id": "s1", "type": "synced", "data": {"sid": "AbCdEfGh12345678", "src": {"note": 1}, "blocks": [P("s1a", "<span class=\"hk-c-red\">innen</span>"),
                                                                                                 {"id": "sc", "type": "columns", "data": {"cols": [{"blocks": [P("x1", "a")]}, {"blocks": [P("x2", "b")]}]}}]}},
    {"id": "s2", "type": "synced", "data": {"sid": "kurz", "blocks": "nein"}},
    {"id": "k6", "type": "columns", "data": {"cols": [{"w": 1, "blocks": [P(f"k6{i}", str(i))]} for i in range(6)]}},
    {"id": "k7", "type": "columns", "data": {"cols": [{"w": 1, "blocks": []} for _ in range(7)]}},
    {"id": "hc", "type": "paragraph", "data": {"text": "Fassung"}, "tunes": {"hkconflict": {"by": "~abcdefghijklmnop", "name": "<b>Anna</b>", "at": 1.5, "of": "hc0", "x": 1}, "evil": {}}},
]}
out = {b["id"]: b for b in json.loads(clean_blocks(json.dumps(doc))[0])["blocks"]}
ok("math: TeX als Klartext (kein HTML-Escape), hoechstens 4000 Zeichen", out["m1"]["data"]["tex"].startswith("a<b & \\frac") and len(out["m1"]["data"]["tex"]) == 4000)
ok(f"math: render bereinigt, h passt nicht -> stale ({out['m1']['data'].get('render')})",
   out["m1"]["data"]["render"] == {"png": "/files/" + "a" * 24, "h": "0123456789abcdef", "w": 300, "stale": True})
ok("math: ungueltiges render faellt weg", "render" not in out["m2"]["data"])
ok("Mermaid: view split bleibt", out["c1"]["data"].get("view") == "split")
ok("Mermaid: unbekannte view faellt weg", "view" not in out["c2"]["data"])
sb = out["s1"]["data"]
ok("synced: sid bleibt, src (vom Server eingesetzt) wird nie gespeichert", sb.get("sid") == "AbCdEfGh12345678" and "src" not in sb)
ok("synced: Inhalt bereinigt, Spalten darin aufgeloest", [x["id"] for x in sb["blocks"]] == ["s1a", "x1", "x2"] and "hk-c-red" in sb["blocks"][0]["data"]["text"])
ok("synced: ungueltige sid und blocks fallen weg", "sid" not in out["s2"]["data"] and "blocks" not in out["s2"]["data"])
ok("6 Spalten mit Breiten", len(out["k6"]["data"]["cols"]) == 6 and all(c.get("w") for c in out["k6"]["data"]["cols"]))
ok("7 Spalten: keine Breiten (ueber COLS_MAX)", not any("w" in c for c in out["k7"]["data"]["cols"]))
ok(f"hkconflict bleibt bereinigt, fremde Tunes weg ({out['hc'].get('tunes')})",
   out["hc"].get("tunes") == {"hkconflict": {"by": "~abcdefghijklmnop", "name": "bAnna/b", "at": 1.5, "of": "hc0"}})

# --- P6-1: Vorschaubild (render.h/stale), MathML-Positivliste, Veroeffentlichen -----------------------------------------
ok("src_hash: SHA-256 des Quelltexts, 16 Zeichen", src_hash("x^2") == __import__("hashlib").sha256(b"x^2").hexdigest()[:16])
R = lambda h, extra=None: dict({"png": "/files/" + "b" * 24, "svg": "/files/" + "c" * 24, "h": h, "w": 120, "hgt": 40}, **(extra or {}))  # noqa: E731
doc2 = {"blocks": [{"id": "r1", "type": "math", "data": {"tex": "x^2", "render": R(src_hash("x^2"), {"stale": True})}},
                   {"id": "r2", "type": "math", "data": {"tex": "x^3", "render": R(src_hash("x^2"))}},
                   {"id": "r3", "type": "code", "data": {"code": "graph TD; A-->B", "language": "mermaid", "view": "preview", "render": R(src_hash("graph TD; A-->B"))}},
                   {"id": "r4", "type": "code", "data": {"code": "graph TD; A-->C", "language": "mermaid", "render": R(src_hash("graph TD; A-->B"))}},
                   {"id": "r5", "type": "math", "data": {"tex": "y", "render": {"png": "/files/" + "b" * 24, "h": src_hash("y"), "onload": "x", "svg": "data:x"}}}]}
o2 = {b["id"]: b["data"].get("render") for b in json.loads(clean_blocks(json.dumps(doc2))[0])["blocks"]}
ok("render aktuell: kein stale (auch wenn der Client es schickt)", "stale" not in o2["r1"] and o2["r1"]["w"] == 120 and o2["r1"]["hgt"] == 40)
ok("Mathe-Quelltext ohne neues Bild geaendert -> stale", o2["r2"].get("stale") is True)
ok("Mermaid aktuell / geaendert -> stale", "stale" not in o2["r3"] and o2["r4"].get("stale") is True)
ok(f"render: fremde Felder und data:-svg fallen weg ({o2['r5']})", o2["r5"] == {"png": "/files/" + "b" * 24, "h": src_hash("y")})
ML = [  # TeX -> MathML: nur Positivliste
    ("Bruch", r"\frac{a}{b}", "<mfrac>", None),
    ("href faellt weg", r"\href{javascript:alert(1)}{x}", "<mi>x</mi>", "href"),
    ("Skript im Text bleibt Text", r"\text{<script>alert(1)</script>}", "<mtext>", "<script"),
    ("Farbe/Stil-Attribute fallen weg", r"\color{red}{x}", "<mstyle>", "mathcolor"),
    ("toggle/maction gibt es nicht", r"\toggle{a}{b}", "<mi>", "maction"),
    ("Matrix", r"\begin{pmatrix}a&b\\c&d\end{pmatrix}", "<mtable>", None),
    ("Entity bleibt escaped", r"a<b", "&lt;", "<b"),
]
for name, tex, want, bad in ML:
    got = mathml.to_mathml(tex) or ""
    ok(f"MathML {name}: {got[:90]!r}", want in got and (bad is None or bad not in got) and got.startswith("<math"))
for el in ("maction", "semantics", "annotation", "href", "xlink", "style=", "onload", "script", "foreignObject"):
    ok(f"MathML-Positivliste: kein {el}", el not in mathml.ELEMENTS and el not in mathml.ATTRS)
ok("MathML: leer/zu lang/kaputt -> None", mathml.to_mathml("") is None and mathml.to_mathml("x" * 4001) is None and mathml.to_mathml("\\frac{") is None)
ok("MathML Block: display=block", (mathml.to_mathml("x", block=True) or "").startswith('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'))


class _Ctx:                                      # Veroeffentlichen ohne Datenbank: nur img()
    base, uid = "/@x/s", "u"

    def img(self, src, w=1280):
        return f"/@x/s/f/{src.rsplit('/', 1)[-1]}?w={w}" if src == "/files/" + "b" * 24 else None

    def url_for(self, nid):
        return None


pb = publish._block(_Ctx(), "math", {"tex": "\\href{javascript:alert(1)}{x}<script>"}, 0)
ok(f"Veroeffentlichen math-Block: MathML ohne href/script ({pb[:80]})", pb.startswith('<div class="pub-mathblock"><math') and "href" not in pb and "<script" not in pb)
pb = publish._block(_Ctx(), "code", {"code": "graph TD; A-->B", "language": "mermaid", "render": R(src_hash("graph TD; A-->B"))}, 0)
ok(f"Veroeffentlichen Mermaid: gespeichertes Bild ({pb[:90]})", '<img src="/@x/s/f/' + "b" * 24 + '?w=2000"' in pb and 'width="120" height="40"' in pb)
pb = publish._block(_Ctx(), "code", {"code": "graph TD; A-->B", "language": "mermaid", "render": dict(R("0" * 16), stale=True)}, 0)
ok("Veroeffentlichen Mermaid veraltet -> Code-Block", pb.startswith('<div class="pub-code">') and "graph TD" in pb and "<img" not in pb)
pb = publish._block(_Ctx(), "code", {"code": "<img src=x onerror=alert(1)>", "language": "mermaid"}, 0)
ok("Veroeffentlichen Mermaid ohne Bild -> escaped Code", "&lt;img" in pb and "<img" not in pb)

# --- Markenerhalt (ohne Server, P6-3; ausfuehrlich in tests/markenerhalt.py) ----------------------------------------------------------------------
old = json.dumps({"blocks": [P("a", 'Hallo <span class="hk-c-red">rote</span> Welt'), P("b", '<code class="hk-math">x^2</code> und mehr'),
                             {"id": "l", "type": "list", "data": {"style": "unordered", "items": [{"content": '<mark class="hk-bg-blue">eins</mark>', "items": []}]}},
                             {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", str(i))]} for i in range(6)]}}]})
new = json.dumps({"blocks": [P("a", "Hallo rote Welt"), P("b", "<code>x^2</code> und weniger"),
                             {"id": "l", "type": "list", "data": {"style": "unordered", "items": [{"content": "<mark>eins</mark>", "items": []}]}},
                             {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", str(i))]} for i in range(3)] + [{"blocks": [P("c3", "3"), P("c4", "4")]}]}}]})
kept = {b["id"]: b for b in json.loads(legacy_keep(old, new, frozenset()))["blocks"]}
ok("Alte App, Text unveraendert: Farbe bleibt", kept["a"]["data"]["text"] == 'Hallo <span class="hk-c-red">rote</span> Welt')
ok("Alte App, Text geaendert: Mathe bleibt, Rest neu", kept["b"]["data"]["text"] == '<code class="hk-math">x^2</code> und weniger')
ok("Alte App: Listenpunkt behaelt Hintergrund", kept["l"]["data"]["items"][0]["content"] == '<mark class="hk-bg-blue">eins</mark>')
ok("Alte App: Spalten 5-6 wieder angehaengt (ohne Doppelte)",
   [[x["id"] for x in c["blocks"]] for c in kept["k"]["data"]["cols"]] == [["c0"], ["c1"], ["c2"], ["c3", "c4"], [], ["c5"]])
ok("Neuer Client (inline2, cols6): nichts uebernommen", legacy_keep(old, new, frozenset(("inline2", "cols6"))) == new)

# --- API ---------------------------------------------------------------------------------------------------------
try:
    s, d = call("POST", "/api/notes", {"title": "P6-0 Inline"}); nid = d.get("id")
    ok("Seite angelegt", s == 200 and nid)
    from saga.core import local_ref
    ME = local_ref("hk-test-a")     # 2.5 (P6-5): Erwaehnung nur von Personen mit Zugriff - sonst wird sie zu Text
    body = {"blocks": [P("a", 'Hallo <span class="hk-c-red">rote</span> Welt'), P("b", f'<span class="hk-mention" data-u="{ME}">Anna</span> schau'),
                       {"id": "m", "type": "math", "data": {"tex": "e^{i\\pi}+1=0"}},
                       {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", f"Spalte {i}")]} for i in range(6)]}}]}
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(body), "format": "blocks"})
    ok("Web speichert alle Formen", s == 200)
    fremd = {"blocks": [{"id": "m", "type": "math", "data": {"tex": "x", "render": {"png": "/files/" + "e" * 24, "h": src_hash("x")}}}]}
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(fremd), "format": "blocks"})
    ok(f"Vorschaubild mit fremder/unbekannter Datei -> {s}", s == 400)
    got = {b["id"]: b for b in json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]}
    ok("Farbe, Erwaehnung, Mathe, 6 Spalten gespeichert", "hk-c-red" in got["a"]["data"]["text"] and f'data-u="{ME}"' in got["b"]["data"]["text"]
       and got["m"]["data"]["tex"] == "e^{i\\pi}+1=0" and len(got["k"]["data"]["cols"]) == 6)
    # aeltere App (Geraete-Kopf X-Saga-Caps ohne inline2/cols6): verliert span und Spalten 5-6
    old_app = {"blocks": [P("a", "Hallo rote Welt"), P("b", "Anna schau!"), {"id": "m", "type": "math", "data": {"tex": "e^{i\\pi}+1=0"}},
                          {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", f"Spalte {i}")]} for i in range(4)]}}]}
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(old_app), "format": "blocks"}, {"X-Saga-Caps": "keine"})
    got = {b["id"]: b for b in json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]}
    ok("Alte App: unveraenderter Absatz behaelt die Farbe", s == 200 and got["a"]["data"]["text"] == 'Hallo <span class="hk-c-red">rote</span> Welt')
    ok("Alte App: geaenderter Absatz behaelt die Erwaehnung", got["b"]["data"]["text"] == f'<span class="hk-mention" data-u="{ME}">Anna</span> schau!')
    ok("Alte App: Spalten 5-6 bleiben", len(got["k"]["data"]["cols"]) == 6 and got["k"]["data"]["cols"][5]["blocks"][0]["id"] == "c5")
    # neuer Client entfernt die Farbe bewusst
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": [P("a", "Hallo rote Welt")]}), "format": "blocks"}, {"X-Saga-Caps": "inline2,cols6"})
    got = json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]
    ok("Neuer Client (inline2) darf die Farbe entfernen", s == 200 and got[0]["data"]["text"] == "Hallo rote Welt")
    # Markdown-Export
    call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(body), "format": "blocks"})
    s, md = call("GET", f"/api/notes/{nid}/export")
    ok(f"Markdown-Export: @Name und $$TeX$$ ({s})", s == 200 and "@Anna schau" in md and "$$\ne^{i\\pi}+1=0\n$$" in md and "Spalte 5" in md)
finally:
    from db import connect
    with connect() as c, c:
        c.execute("DELETE FROM note_version WHERE uid='hk-test-a'")
        c.execute("DELETE FROM note WHERE uid='hk-test-a'")
print(f"\nEDITOR-INLINE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
