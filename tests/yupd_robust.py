"""Saga 2.5 P3-E7: saga/yupd.py (Werte in Yjs-Updates umschreiben) ohne Server - Pruefer integ-e7.

Geprueft: Identitaet byte-gleich; Umschreiben aendert nur Werte (gleicher Zustandsvektor, gleicher Aufbau) auch bei
ueberschriebenen Attributen, deren Vorgaenger zu einem langen geloeschten Eintrag zusammengefasst ist (Ursprung in der
Mitte, > 1024 Uhren); Speicher/Zeit bei vielen langen geloeschten Map-Eintraegen begrenzt; tief verschachteltes JSON
und riesige Zahlen -> ValueError (keine RecursionError/OverflowError); kurzer Zufallslauf mit pycrdt."""
import json, os, random, resource, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from pycrdt import Doc, Map, XmlElement, XmlFragment, XmlText  # noqa: E402
from saga import yupd  # noqa: E402
from saga.yupd import _vs, _vu  # noqa: E402

res = []


def ok(n, c, info=None):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, "" if c or info is None else str(info)[:300], flush=True)


def tag(v, key):
    if isinstance(v, str):
        return v + "~" + (f"{key[0]}.{key[1]}" if key and key[1] is not None else "-")
    return v


def attrs(d):
    f = d.get("prosemirror", type=XmlFragment)
    out = []
    for ch in f.children:
        if isinstance(ch, XmlElement):
            out.append((ch.tag, dict(ch.attributes)))
    return out


# 1. Langer geloeschter Vorgaenger, Ursprung in der Mitte (Zusammenfassung beim Kodieren)
for n in (500, 1100, 3000):
    d = Doc(client_id=5)
    e = d.get("prosemirror", type=XmlFragment).children.append(XmlElement("page", {"id": "s"}))
    for i in range(n):
        e.attributes["id"] = f"v{i}"
    full = d.get_update()
    out, opn = yupd.transform(full, tag, {})
    B = Doc(); B.apply_update(out)
    ok(f"Ursprung in langem geloeschtem Eintrag ({n}): Schluessel gefunden", opn == 0 and attrs(B) == [("page", {"id": f"v{n - 1}~page.id"})],
       (opn, attrs(B)))

# 2. Inkrementell: Gedaechtnis aus erstem Update, zweites haengt an der Mitte eines Bereichs
d = Doc(client_id=9)
e = d.get("prosemirror", type=XmlFragment).children.append(XmlElement("page", {"id": "a"}))
for i in range(2000):
    e.attributes["id"] = f"w{i}"
u1 = d.get_update()
keys = {}
yupd.key_scan(u1, keys)
sv = d.get_state()
e.attributes["id"] = "neu"
u2 = d.get_update(sv)
out, opn = yupd.transform(u2, tag, keys)
ok("inkrementell nach Gedaechtnis: Schluessel bekannt", opn == 0, opn)
ok("Gedaechtnis klein (Bereiche statt je Uhr)", len(keys) < 50, len(keys))

# 3. Speicher-/Zeitbombe: viele lange geloeschte Map-Eintraege in einem Update
N = 30000
items = b"".join(bytes([0x21]) + _vu(1) + _vs("m") + _vs("k") + _vu(5000) for _ in range(N))
u = _vu(1) + _vu(N) + _vu(7) + _vu(0) + items + b"\x00"
r0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
t = time.time()
k = {}
yupd.transform(u, lambda v, kk: v, k)
dt, dm = time.time() - t, (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - r0) // 1024
ok(f"240 KB mit {N} langen geloeschten Eintraegen: schnell und klein ({dt:.2f} s, +{dm} MB)", dt < 5 and dm < 300 and len(k) <= yupd.KEYS_MAX + 2)


# 4. Unlesbares JSON -> ValueError
def mk(js, ref):
    if ref == 5:
        it = bytes([5]) + _vu(1) + _vs("prosemirror") + _vs(js)
    else:
        it = bytes([6]) + _vu(1) + _vs("prosemirror") + _vs("link") + _vs(js)
    return _vu(1) + _vu(1) + _vu(3) + _vu(0) + it + b"\x00"


for name, js in (("tief", "[" * 5000 + "]" * 5000), ("grosse Zahl", "1" + "0" * 400), ("lange Ziffern", "9" * 5000)):
    for ref in (5, 6):
        try:
            yupd.transform(mk(js, ref), lambda v, kk: v, {})
            r = "ok"
        except ValueError:
            r = "ValueError"
        except Exception as ex:  # noqa: BLE001
            r = type(ex).__name__
        ok(f"JSON {name} (Inhalt {ref}) -> ValueError", r == "ValueError", r)

# 5. Kurzer Zufallslauf (pycrdt): Werte umgeschrieben, Aufbau gleich
rnd = random.Random(5)
bad = 0
for case in range(150):
    docs = [Doc(client_id=c) for c in rnd.sample([1, 2 ** 31 + 5, 2 ** 53 - 9, 77], 2)]
    seq = []
    for s in range(20):
        dd = rnd.choice(docs)
        sv = dd.get_state()
        f = dd.get("prosemirror", type=XmlFragment)
        m = dd.get("meta", type=Map)
        els = [x for x in f.children if isinstance(x, XmlElement)]
        kk = rnd.randrange(5)
        if kk == 0 or not els:
            f.children.append(XmlElement(rnd.choice(["page", "mention", "image"]), {"id": f"i{s}"}))
        elif kk == 1:
            rnd.choice(els).attributes[rnd.choice(["id", "u", "src"])] = rnd.choice(["x", "/files/1", 3, None, {"a": "b"}])
        elif kk == 2:
            el = rnd.choice(els)
            el.children.append(XmlText("t"))
        elif kk == 3:
            m[rnd.choice(["q", "z"])] = rnd.choice(["v", 1])
        else:
            el = rnd.choice(els)
            ts = [x for x in el.children if isinstance(x, XmlText)]
            if ts and len(ts[0]):
                ts[0].format(0, 1, {"link": {"href": "/files/2"}})
        seq.append(dd.get_update(sv))
        o = docs[0] if dd is docs[1] else docs[1]
        if rnd.random() < .4:
            o.apply_update(dd.get_update(o.get_state()))
    keys = {}
    A, B = Doc(), Doc()
    for u in seq:
        if yupd.transform(u, lambda v, k: v, {})[0] != u:
            bad += 1
        out, _ = yupd.transform(u, tag, keys)
        A.apply_update(u); B.apply_update(out)
    if A.get_state() != B.get_state():
        bad += 1
    want = [(t_, {k: tag(v, (t_, k)) for k, v in a.items()}) for t_, a in attrs(A)]
    if json.dumps(want, sort_keys=True) != json.dumps(attrs(B), sort_keys=True):
        bad += 1
ok("Zufallslauf 150 Faelle: Identitaet, Zustandsvektor, Werte", bad == 0, bad)

print(f"YUPD {sum(res)}/{len(res)}")
sys.exit(0 if all(res) else 1)
