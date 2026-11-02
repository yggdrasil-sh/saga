"""2.5 (Sicherheit, Fund sec-web #1/V1/V2): collab1-Heim - Zustand eines boesartigen bzw. kompromittierten Eigentuemer-
Servers wird geprueft wie Stufe 1 (federation._clean_remote_body), bevor ihn lokale Clients sehen (saga.fed_collab._home_check).

Ohne Server und Datenbank (wie tests/pm_vectors.py): echte Umrechnung Eigentuemer -> Heim (_xform + _HomeMap.into), dann
_checked auf dem Ergebnis. Geprueft (abgelehnt -> Weiterleitung schliesst, Stufe 1):
- Bild mit /logout, /lang?l=th, fremder Adresse, Datei einer anderen fremden Seite (Proxy-Pfad einer anderen ID);
- Vorschaubild (Mathe/Mermaid render.png) mit /logout; Anhang (attaches) mit /logout;
- Link-Marke javascript:/data:, embed.url javascript:, nextcloud.inst javascript:, Nextcloud-Autor nicht als Platzhalter;
- unbekannte Wurzel, Inline-HTML ausserhalb der Positivliste (Erwaehnung mit kaputter Kennung).
Angenommen: eigene Dateien der Seite (/files/<id> -> Proxy), http(s)-/mailto-Links, Seitenverweise, Platzhalter-Autor,
Schaltflaechen mit Zielen derselben Instanz. Dazu: Awareness des Eigentuemers nur in der Form der lokalen Clients."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BASE_URL", "http://127.0.0.1:1")
os.environ.setdefault("SECRET_KEY", "fed-live-boese-0123456789abcdef0123456789")
from pycrdt import Doc, XmlFragment, XmlText  # noqa: E402
from saga import collab, collab_model as M, fed_collab as FC, federation as F  # noqa: E402

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL") + " " + n + ("" if c else f" -> {str(info)[:400]}"), flush=True)


PID, RID = 3, 77
NID = F.ref(PID, RID)
FID = "0123456789abcdef01234567"


class Relay:
    def __init__(self):
        self.pid, self.rid, self.nid, self.doc, self.keys = PID, RID, NID, None, {}
        self.map = FC._HomeMap(PID, RID, None)


def owner_update(blocks, title="T"):
    return M.build(json.dumps({"blocks": blocks}, ensure_ascii=False), title)


def home(blocks, relay=None):
    """Eigentuemer-Zustand (dessen Form) wie beim Heim umrechnen und pruefen -> (angenommen?, Grund)."""
    r = relay or Relay()
    u = FC._xform(owner_update(blocks), r.map.into(None), r.keys)
    try:
        FC._checked(r, u)
        return True, ""
    except FC._Unsafe as ex:
        return False, str(ex)


def P(i, t):
    return {"id": i, "type": "paragraph", "data": {"text": t}}


def img(url):
    return {"id": "i1", "type": "image", "data": {"file": {"url": url}, "caption": ""}}


good = [P("p1", 'Hallo <b>fett</b> <a href="https://x.example/?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">L</a> '
                '<a href="mailto:a@b.example" target="_blank" rel="noopener noreferrer">M</a> <a href="/notes#12"></a>'),
        img(f"/files/{FID}"),
        {"id": "m1", "type": "math", "data": {"tex": "x^2", "render": {"png": f"/files/{FID}", "h": "0123456789abcdef"}}},
        {"id": "n1", "type": "nextcloud", "data": {"inst": "https://cloud.example", "fileid": "5", "author": "~0123456789abcdef", "sig": "x"}},
        {"id": "b1", "type": "buttons", "data": {"buttons": [{"label": "Go", "target": 12, "sig": "s"}]}},
        {"id": "e1", "type": "embed", "data": {"url": "https://www.youtube.com/watch?v=abc", "kind": "youtube"}}]
a, why = home(good)
ok(f"Heim: normaler Inhalt (eigene Datei ueber Proxy, Links, Platzhalter-Autor, Schaltflaeche) angenommen ({why})", a)
r = Relay()
u = FC._xform(owner_update(good), r.map.into(None), r.keys)
d = Doc(); d.apply_update(u)
s = json.dumps(M.read(d)[0])
ok("... Datei als /api/fed/files/<seite>/<id>, Schaltflaechen-Ziel als negative ID",
   f"/api/fed/files/{NID}/{FID}" in s and f'"target": {F.ref(PID, 12)}' in s, s[:600])

bad = {
    "Bild /logout": [img("/logout")],
    "Bild /lang?l=th": [img("/lang?l=th&next=/")],
    "Bild fremde Adresse": [img("https://evil.example/x.png")],
    "Bild Datei einer anderen fremden Seite": [img(f"/api/fed/files/{F.ref(PID, 78)}/{FID}")],
    "Bild data:": [img("data:image/png;base64,AAAA")],
    "Anhang /logout": [{"id": "a1", "type": "attaches", "data": {"file": {"url": "/logout", "name": "x"}, "title": "x"}}],
    "Vorschaubild /logout": [{"id": "m1", "type": "math", "data": {"tex": "x", "render": {"png": "/logout", "h": "0123456789abcdef"}}}],
    "Mermaid-Vorschau /lang": [{"id": "c1", "type": "code", "data": {"code": "graph TD", "language": "mermaid",
                                                                    "render": {"svg": "/lang?l=th", "h": "0123456789abcdef"}}}],
    "Link javascript:": [P("p1", '<a href="javascript:alert(1)">x</a>')],
    "Link data:": [P("p1", '<a href="data:text/html,x">x</a>')],
    "Link /logout": [P("p1", '<a href="/logout">x</a>')],
    "Einbetten javascript:": [{"id": "e1", "type": "embed", "data": {"url": "javascript:alert(1)"}}],
    "Nextcloud inst javascript:": [{"id": "n1", "type": "nextcloud", "data": {"inst": "javascript:x", "fileid": "5", "author": "~0123456789abcdef"}}],
    "Nextcloud-Autor als Anmeldename": [{"id": "n1", "type": "nextcloud", "data": {"inst": "https://c.example", "fileid": "5", "author": "anna", "sig": "x"}}],
}
for name, bl in bad.items():
    a, why = home(bl)
    ok(f"Heim lehnt ab: {name} ({why})", not a)

# Link-Marke direkt im Y-Zustand (nicht ueber Block-JSON): javascript:-href als Marke
dd = Doc()
dd.apply_update(owner_update([P("p1", "x")]))
with dd.transaction():
    t = next(x for x in list(dd.get("prosemirror", type=XmlFragment).children)[0].children if isinstance(x, XmlText))
    t.format(0, 1, {"link": {"href": "javascript:alert(1)"}})
r = Relay()
try:
    FC._checked(r, dd.get_update())
    ok("Heim lehnt ab: Link-Marke javascript: im Y-Zustand", False)
except FC._Unsafe as ex:
    ok(f"Heim lehnt ab: Link-Marke javascript: im Y-Zustand ({ex})", True)
ev = M.new_doc()
ev.get("evil", type=XmlFragment).children.append(XmlText("boese"))
try:
    FC._checked(Relay(), ev.get_update())
    ok("Heim lehnt ab: unbekannte Wurzel", False)
except FC._Unsafe:
    ok("Heim lehnt ab: unbekannte Wurzel", True)

# Ein boeses Update auf einem guten Zustand: Doc des Heims bleibt unveraendert
r = Relay()
u = FC._xform(owner_update(good), r.map.into(None), r.keys)
FC._checked(r, u)
r.doc = M.new_doc(); r.doc.apply_update(u)
before = r.doc.get_update()
src = Doc(); src.apply_update(owner_update(good))
sv = src.get_state()
src_frag = src.get("prosemirror", type=XmlFragment)
with src.transaction():
    ti = next(x for x in list(src_frag.children)[0].children if isinstance(x, XmlText))
    ti.format(0, 3, {"link": {"href": "javascript:alert(2)"}})
bad_u = FC._xform(src.get_update(sv), r.map.into(None), r.keys)
try:
    FC._checked(r, bad_u)
    ok("Heim: boeses Folge-Update abgelehnt", False)
except FC._Unsafe:
    ok("Heim: boeses Folge-Update abgelehnt, Doc unveraendert", r.doc.get_update() == before)

# Awareness: Form wie lokale Clients (collab.aw_fields)
good_c = {"anchor": {"type": {"client": 1, "clock": 2}, "tname": None, "item": None, "assoc": 0},
          "head": {"type": None, "tname": "prosemirror", "item": {"client": 1, "clock": 5}, "assoc": -1}}
o = collab.aw_fields({"cursor": good_c, "block": "b1", "typing": False, "evil": 1})
ok(f"Awareness: gueltiger Cursor bleibt ({o})", o.get("cursor", {}).get("head", {}).get("item") == {"client": 1, "clock": 5}
   and o.get("block") == "b1" and o.get("typing") is False and "evil" not in o)
for x in ({"anchor": "nonsense", "head": "x"}, {"anchor": {}, "head": {}}, {"anchor": {"item": {"client": 1.5, "clock": 0}}, "head": good_c["head"]},
          {"anchor": {"item": {"client": True, "clock": 0}}, "head": good_c["head"]}, {"anchor": {"tname": "x" * 65}, "head": good_c["head"]},
          {"anchor": good_c["anchor"]}, [good_c], "x"):
    o = collab.aw_fields({"cursor": x, "sel": x, "block": "<b>", "typing": 1})
    ok(f"Awareness: ungueltig verworfen ({json.dumps(x)[:50]})", o == {})

# 2.5 (Pentest 2, V1): Verbindung getrennt -> offene Weiterleitungen dieser Instanz schliessen (PEER_GONE_HOOK)
class _Conn:
    def __init__(self):
        self.sent, self.subs = [], {}

    def send(self, f):
        self.sent.append(f)


class _Sub:
    def __init__(self, nid):
        self.conn, self.nid = _Conn(), nid
        self.conn.subs[nid] = self


rl, other = FC._Relay("u1", NID, PID, RID), FC._Relay("u2", F.ref(PID + 1, RID), PID + 1, RID)
for x in (rl, other):
    sb = _Sub(x.nid)
    x.subs[sb] = sb
    FC._H[(x.uid, x.nid)], FC._HW[x.w] = x, x
s1 = next(iter(rl.subs))
ok("V1: Hook in PEER_GONE_HOOKS registriert", FC._peer_gone in F.PEER_GONE_HOOKS)
hooked = []
FC._peer_gone(type("C", (), {"after_commit": lambda self, fn: hooked.append(fn)})(), {"id": PID})
ok("V1: Hook wirkt erst nach dem Commit", len(hooked) == 1 and not rl.closed)
FC._gone_now(PID)
ok("V1: Relais der getrennten Instanz geschlossen (bye an lokales Abo, aus _H/_HW)", rl.closed and rl.w not in FC._HW
   and (rl.uid, rl.nid) not in FC._H and s1.conn.sent and s1.conn.sent[-1][:1] == bytes([collab.T_BYE]) and not s1.conn.subs)
ok("V1: Relais anderer Instanzen bleiben", not other.closed and FC._HW.get(other.w) is other)
FC._close(other)

print(f"\nFOEDERATION-LIVE-BOESE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
sys.exit(0 if all(res) else 1)
