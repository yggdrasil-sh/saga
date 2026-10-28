"""Saga 2.5 P6-3: Markenerhalt fuer aeltere Clients (ohne X-Saga-Caps inline2) und Foederations-Peers ohne inline2.
Farben (span.hk-c-*, mark.hk-bg-*), Erwaehnungen (span.hk-mention) und Inline-Mathe (code.hk-math) wandern aus dem
gespeicherten Stand auf die unveraenderten Zeichen der neuen Fassung (saga.merge.keep_marks, Zeichenfolgen-Diff wie beim
Merge); geaenderte Zeichen verlieren die Marke, Erwaehnung/Mathe sind unteilbar. Erst ohne Server (Funktionen), dann ueber
die API (PATCH mit und ohne inline2). Umgebung wie die anderen Tests (HK_TEST_BASE, BASE_URL, SESSION_SECRET, DATABASE_URL)."""
import base64, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from itsdangerous import TimestampSigner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from saga.merge import keep_marks, pairs  # noqa: E402
from saga.pages import clean_blocks, legacy_keep  # noqa: E402

B = os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099")
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
CK = S.sign(base64.b64encode(json.dumps({"uid": "hk-test-a", "pwc": time.time() + 5}).encode())).decode()
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


def P(i, t):
    return {"id": i, "type": "paragraph", "data": {"text": t}}


RED, BLUE = '<span class="hk-c-red">', '<mark class="hk-bg-blue">'
ME = "~abcdefghijklmnop"
MEN = f'<span class="hk-mention" data-u="{ME}">Anna</span>'
MATH = '<code class="hk-math">x^2</code>'

# --- Funktionen ----------------------------------------------------------------------------------------------------
ok("pairs: gleiche Stellen", pairs(list("abcd"), list("axcdd")) == [(0, 0), (2, 2), (3, 3)])
ok("unveraendert -> altes HTML", keep_marks(f"Hallo {RED}rote</span> Welt", "Hallo rote Welt") == f"Hallo {RED}rote</span> Welt")
ok("Text dahinter ergaenzt: Farbe bleibt", keep_marks(f"Hallo {RED}rote</span> Welt", "Hallo rote Welt, schoen") == f"Hallo {RED}rote</span> Welt, schoen")
ok("Zeichen in der Farbe geaendert: nur dieses ohne Farbe",
   keep_marks(f"Hallo {RED}rote</span> Welt", "Hallo rxte Welt") == f"Hallo {RED}r</span>x{RED}te</span> Welt")
ok("davor eingefuegt: Farbe wandert mit", keep_marks(f"{RED}rot</span>", "ganz rot") == f"ganz {RED}rot</span>")
ok("Hintergrund: zurueckgestuftes mark wird ersetzt", keep_marks(f"{BLUE}eins</mark> zwei", "<mark>eins</mark> zwei drei") == f"{BLUE}eins</mark> zwei drei")
ok("Hintergrund: cdx-marker wird ersetzt", keep_marks(f"{BLUE}eins</mark>", '<mark class="cdx-marker">eins</mark>!') == f"{BLUE}eins</mark>!")
ok("Hintergrund ohne mark beim Client: wieder gesetzt", keep_marks(f"a {BLUE}b</mark>", "a b") == f"a {BLUE}b</mark>")
ok("Erwaehnung als Text, dahinter geaendert -> Erwaehnung bleibt", keep_marks(f"{MEN} schau", "Anna schau mal") == f"{MEN} schau mal")
ok("Erwaehnung geaendert -> Text", keep_marks(f"{MEN} schau", "Anne schau") == "Anne schau")
ok("Erwaehnung zerrissen -> Text", keep_marks(f"{MEN} schau", "An na schau") == "An na schau")
ok("Mathe als code zurueck -> hk-math", keep_marks(f"a {MATH} b", "a <code>x^2</code> b und c") == f"a {MATH} b und c")
ok("Mathe als reiner Text -> hk-math", keep_marks(f"a {MATH} b", "a x^2 b") == f"a {MATH} b")
ok("Mathe geaendert -> Text", keep_marks(f"a {MATH} b", "a x^3 b") == "a x^3 b")
ok("eigene Formatierung des Clients bleibt", keep_marks(f"<b>fett {RED}rot</span></b>", "<b>fett rot</b> neu") == f"<b>fett {RED}rot</span></b> neu")
ok("neue Fett-Marke des Clients bleibt", keep_marks(f"a {RED}rot</span>", "a <b>rot</b>") == f"a <b>{RED}rot</span></b>")
ok("geloeschtes Wort: Marke weg", keep_marks(f"a {RED}rot</span> b", "a b") == "a b")
_h = keep_marks(f"Hallo {RED}rote</span> Welt", "Hallo rxte Welt")
ok("Ergebnis ist sanitizer-fest", json.loads(clean_blocks(json.dumps({"blocks": [P("a", _h)]}))[0])["blocks"][0]["data"]["text"] == _h)

old = json.dumps({"blocks": [P("a", f"Hallo {RED}rote</span> Welt"),
                             {"id": "l", "type": "list", "data": {"style": "unordered", "items": [
                                 {"content": f"{BLUE}eins</mark>", "items": []}, {"content": f"zwei {MATH}", "items": []}]}},
                             {"id": "t", "type": "table", "data": {"content": [[f"{RED}A</span>", "B"]]}}]})
new = json.dumps({"blocks": [P("a", "Hallo rote Welt!"),
                             {"id": "l", "type": "list", "data": {"style": "unordered", "items": [
                                 {"content": "null", "items": []}, {"content": "eins", "items": []}, {"content": "zwei x^2", "items": []}]}},
                             {"id": "t", "type": "table", "data": {"content": [["A", "B2"]]}}]})
kept = {b["id"]: b for b in json.loads(legacy_keep(old, new, frozenset()))["blocks"]}
ok("Block: Absatz ergaenzt, Farbe bleibt", kept["a"]["data"]["text"] == f"Hallo {RED}rote</span> Welt!")
ok("Liste mit neuem Punkt vorn: Marken bleiben am richtigen Punkt",
   [x["content"] for x in kept["l"]["data"]["items"]] == ["null", f"{BLUE}eins</mark>", f"zwei {MATH}"])
ok("Tabelle: Zelle behaelt Farbe", kept["t"]["data"]["content"] == [[f"{RED}A</span>", "B2"]])
ok("inline2: nichts uebertragen", legacy_keep(old, new, frozenset(("inline2", "cols6"))) == new)
ok("Foederations-Peer ohne inline2 (Faehigkeiten wie peer_caps): Marken bleiben",
   json.loads(legacy_keep(old, new, frozenset(("merge1", "cols6"))))["blocks"][0]["data"]["text"] == f"Hallo {RED}rote</span> Welt!")
other = json.dumps({"blocks": [{"id": "a", "type": "header", "data": {"text": "Hallo rote Welt", "level": 2}}]})
ok("anderer Blocktyp: keine Uebertragung", legacy_keep(old, other, frozenset()) == other)

# --- API -----------------------------------------------------------------------------------------------------------
try:
    s, d = call("POST", "/api/notes", {"title": "P6-3 Markenerhalt"}); nid = d.get("id")
    ok("Seite angelegt", s == 200 and nid)
    from saga.core import local_ref
    me = local_ref("hk-test-a")
    men = f'<span class="hk-mention" data-u="{me}">Anna</span>'
    body = {"blocks": [P("a", f"Hallo {RED}rote</span> Welt"), P("b", f"{men}, schau mal {MATH}"),
                       {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", f"Spalte {i}")]} for i in range(6)]}}]}
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(body), "format": "blocks"})
    ok("Web speichert Farben, Erwaehnung, Mathe, 6 Spalten", s == 200)
    old_app = {"blocks": [P("a", "Hallo rxte Welt, neu"), P("b", "Anna, schau mal genau x^2"),
                          {"id": "k", "type": "columns", "data": {"cols": [{"blocks": [P(f"c{i}", f"Spalte {i}")]} for i in range(4)]}}]}
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps(old_app), "format": "blocks"}, {"X-Saga-Caps": "merge1"})
    got = {b["id"]: b for b in json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]}
    ok(f"Alte App ({s}): Farbe nur auf unveraenderten Zeichen", got["a"]["data"]["text"] == f"Hallo {RED}r</span>x{RED}te</span> Welt, neu")
    ok("Alte App: Erwaehnung und Mathe bleiben trotz Aenderung daneben", got["b"]["data"]["text"] == f"{men}, schau mal genau {MATH}")
    ok("Alte App: Spalten-Schutz (4 -> 6) bleibt", len(got["k"]["data"]["cols"]) == 6)
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": [P("a", "Hallo rxte Welt, neu"), P("b", "Anne, schau")]}), "format": "blocks"},
                {"X-Saga-Caps": "keine"})
    got = {b["id"]: b for b in json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]}
    ok("Alte App: geaenderte Erwaehnung wird Text", s == 200 and got["b"]["data"]["text"] == "Anne, schau")
    s, d = call("PATCH", f"/api/notes/{nid}", {"body": json.dumps({"blocks": [P("a", "Hallo rxte Welt, neu")]}), "format": "blocks"},
                {"X-Saga-Caps": "inline2,cols6"})
    got = json.loads(call("GET", f"/api/notes/{nid}")[1]["body"])["blocks"]
    ok("Neuer Client (inline2) darf Farben entfernen", s == 200 and got[0]["data"]["text"] == "Hallo rxte Welt, neu")
finally:
    from db import connect
    with connect() as c, c:
        c.execute("DELETE FROM note_version WHERE uid='hk-test-a'")
        c.execute("DELETE FROM note WHERE uid='hk-test-a'")
print(f"\nMARKENERHALT {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
