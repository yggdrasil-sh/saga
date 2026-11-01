"""Helfer fuer tools/pm_ytest.mjs (y-tiptap gegen pycrdt): spricht JSON ueber stdin/stdout, braucht pycrdt.

    pm_ytest.py build   -> {name: base64(M.build(blocks))} fuer alle Vektoren + R1-Ausgangslagen
    pm_ytest.py check   <- {name: {update: base64, base?: name}}  -> {name: {ok, blocks?, y?, y_server?, error?}}
                           update = vollstaendiger Zustand (oder Differenz zu base) aus JS; geprueft gegen das
                           Server-Modell: Y-Struktur wie M.build, M.read = out bzw. blocks."""
import base64, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("BASE_URL", "http://127.0.0.1:1")
os.environ.setdefault("SECRET_KEY", "pm-vectors-test-0123456789abcdef0123456789")
from pycrdt import Doc, XmlElement, XmlFragment  # noqa: E402
from saga import collab_model as M  # noqa: E402
import pm_vectors_gen as G  # noqa: E402

V = json.load(open(G.OUT, encoding="utf-8"))
R = G.Res(V["resolve"])
CASES = {c["name"]: c for c in V["cases"]}
B64 = lambda b: base64.b64encode(b).decode()  # noqa: E731


def r1_docs():
    """Zustaende, die ein Client mit NEUEREM Schema geschrieben haben koennte (Risiko R1)."""
    base = [{"id": "a", "type": "paragraph", "data": {"text": "eins"}}, {"id": "b", "type": "paragraph", "data": {"text": "zwei"}},
            {"id": "c", "type": "paragraph", "data": {"text": "drei"}}]
    out = {}
    for kind in ("node", "attr", "mark", "nodeattr_inline"):
        d = Doc(client_id=7)                    # feste Kennung: build und check erzeugen dieselben Eintraege
        d.apply_update(M.build({"blocks": base}))
        fr = d.get(M.FRAGMENT, type=XmlFragment)
        kids = list(fr.children)
        if kind == "node":                      # unbekannter Blocktyp zwischen a und b
            fr.children.insert(1, XmlElement("zukunft", {"id": "z"}))
        elif kind == "attr":                    # unbekanntes Attribut an a und b
            kids[0].attributes["zukunft"] = {"x": 1}
            kids[1].attributes["zukunft"] = {"x": 2}
        elif kind == "mark":                    # unbekannte Marke im Text von b
            list(kids[1].children)[0].format(0, 2, {"zukunft": {}})
        else:                                   # unbekanntes Attribut an einem Inline-Knoten (in c)
            el = kids[2].children.append(XmlElement("mention", {"u": "~abcdefghijklmnop", "zukunft": True}))
        out[f"R1 {kind}"] = {"update": B64(d.get_update()), "blocks": base}
    return out


def y_of_update(upd):
    d = Doc()
    d.apply_update(upd)
    return d


def main():
    mode = sys.argv[1]
    if mode == "build":
        out = {n: B64(M.build({"blocks": c["blocks"]})) for n, c in CASES.items()}
        out.update({k: v["update"] for k, v in r1_docs().items()})
        print(json.dumps(out))
        return
    req = json.load(sys.stdin)
    r1 = r1_docs()
    res = {}
    for name, x in req.items():
        try:
            d = Doc()
            src = x.get("base")
            if src:                              # Differenz auf einen Server-Zustand
                d.apply_update(base64.b64decode(r1[src]["update"]) if src in r1 else M.build({"blocks": CASES[src]["blocks"]}))
            d.apply_update(base64.b64decode(x["update"]))
            got, _ = M.read(d, R)
            r = {"blocks": got["blocks"], "y": G.y_xml(d.get(M.FRAGMENT, type=XmlFragment))}
            c = CASES.get(x.get("case") or name)
            if c is not None:
                srv = y_of_update(M.build({"blocks": c["blocks"]}))
                r["y_server"] = G.y_xml(srv.get(M.FRAGMENT, type=XmlFragment))
                want = x.get("want", c.get("out", c["blocks"]))
                r["ok"] = G.stable(got["blocks"]) == G.stable(want) and (x.get("base") or r["y"] == r["y_server"])
            else:
                r["ok"] = True
            res[name] = r
        except Exception as ex:  # noqa: BLE001
            res[name] = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
