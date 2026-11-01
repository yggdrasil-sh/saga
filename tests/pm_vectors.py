"""Saga 2.5 Tiptap-Umbau T1: gemeinsame Vektoren tests/vectors/pm_schema.json gegen das Server-Modell saga/collab_model.py.

Ohne Server und Datenbank. Je Fall: blocks ist server-kanonisch (clean_blocks-fest); doc_to_pm(blocks) = pm;
Block-JSON -> Y.Doc (pycrdt) -> Block-JSON = out bzw. blocks und clean_blocks-fest; Y-Struktur = y (eigene Faelle);
html_norm; Altformat-Toggle (upgrade). Dazu die Ausgabe des Web-Adapters (node tools/pm_vectors.mjs --dump, ohne
npm-Pakete): = out bzw. blocks und Fixpunkt clean_blocks(Adapter-Ausgabe). Ohne Node faellt nur dieser Teil weg (Hinweis;
PM_VECTORS_NEED_NODE=1 macht daraus einen Fehler). Pruefsumme des Schemas = Vektor-Datei = tools/pm_vectors_gen.py --check."""
import json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BASE_URL", "http://127.0.0.1:1")              # nur fuer den Import von saga.pages (Konfiguration)
os.environ.setdefault("SECRET_KEY", "pm-vectors-test-0123456789abcdef0123456789")
from pycrdt import Doc, XmlFragment  # noqa: E402
from saga import collab_model as M  # noqa: E402
from saga.pages import clean_blocks  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pm_vectors_gen as G  # noqa: E402

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    if not c:
        print("FAIL", n, str(info)[:900], flush=True)


V = json.load(open(G.OUT, encoding="utf-8"))
R = G.Res(V["resolve"])
st = G.stable


def canon(blocks):
    return json.loads(clean_blocks(json.dumps({"blocks": blocks}, ensure_ascii=False))[0])["blocks"]


ok(f"Schema-Pruefsumme der Vektoren = Server ({M.SCHEMA_HASH})", V["schema_hash"] == M.SCHEMA_HASH and V["model"] == M.MODEL
   and V["schema_ver"] == M.SCHEMA_VER)
fresh = G.build()
ok("Vektoren aktuell (tools/pm_vectors_gen.py)", st(fresh) == st(V), "python3 tools/pm_vectors_gen.py")

for c in V["cases"]:
    n, blocks = c["name"], c["blocks"]
    want = c.get("out", blocks)
    try:
        ok(f"{n}: blocks server-kanonisch", st(canon(blocks)) == st(blocks))
        ok(f"{n}: doc_to_pm = pm", st(M.doc_to_pm({"blocks": blocks})) == st(c["pm"]))
        d = Doc()
        d.apply_update(M.build({"blocks": blocks}))
        got, _ = M.read(d, R)
        ok(f"{n}: Y-Round-Trip = out", st(got["blocks"]) == st(want), f"\n  erwartet {st(want)}\n  bekommen {st(got['blocks'])}")
        ok(f"{n}: Ergebnis clean_blocks-fest", st(canon(got["blocks"])) == st(got["blocks"]))
        ok(f"{n}: from pm = out", st(M.pm_to_doc(c["pm"], R)["blocks"]) == st(want))
        if "y" in c:
            ok(f"{n}: Y-Struktur", G.y_xml(d.get(M.FRAGMENT, type=XmlFragment)) == c["y"])
        for src, norm in c.get("html_norm") or []:
            ok(f"{n}: html_norm", M.inline_to_html(M.html_to_inline(src), R, "p1") == norm)
        if "upgrade" in c:
            ub = G.upgrade_toggle(blocks)
            ok(f"{n}: upgrade pm", st(M.doc_to_pm({"blocks": ub})) == st(c["upgrade"]["pm"]))
            # der neue Absatz hat noch keine ID (vergibt im Editor UniqueID, sonst fix_block_ids) - sonst fest
            noid = lambda bl: [dict({k: v for k, v in b.items() if k != "id"}, data=dict(b["data"], blocks=noid(b["data"]["blocks"])))  # noqa: E731
                               if isinstance(b.get("data", {}).get("blocks"), list) else {k: v for k, v in b.items() if k != "id"} for b in bl]
            ok(f"{n}: upgrade clean_blocks-fest (bis auf neue IDs)", st(noid(canon(c["upgrade"]["out"]))) == st(noid(c["upgrade"]["out"])))
        # Durchreichung im Server (read mit base): unveraenderte Bloecke byte-gleich wie im Web-Adapter
        g2, _ = M.read(d, R, {"blocks": blocks})
        ok(f"{n}: read(base) reicht unveraenderte Bloecke durch", all(json.dumps(g2["blocks"][i]) == json.dumps(b)
                                                                   for i, b in enumerate(blocks) if st(want[i]) == st(b)))
    except Exception as ex:  # noqa: BLE001
        ok(f"{n}: {type(ex).__name__}: {ex}", False)

# --- Web-Adapter-Ausgabe (Node) ---------------------------------------------------------------------------------------
node = os.environ.get("NODE") or shutil.which("node") or ("/opt/node22/bin/node" if os.path.exists("/opt/node22/bin/node") else None)
if node:
    p = subprocess.run([node, os.path.join(ROOT, "tools", "pm_vectors.mjs"), "--dump"], capture_output=True, text=True, timeout=120)
    ok("Adapter-Ausgabe (node tools/pm_vectors.mjs --dump)", p.returncode == 0, p.stderr[-500:])
    dump = json.loads(p.stdout) if p.returncode == 0 else {}
    for c in V["cases"]:
        a = dump.get(c["name"])
        ok(f"Adapter {c['name']}: = out", a is not None and st(a) == st(c.get("out", c["blocks"])), f"{st(a)}")
        ok(f"Adapter {c['name']}: clean_blocks(Adapter) aendert nichts", a is not None and st(canon(a)) == st(a))
else:
    print("HINWEIS: kein node gefunden - Adapter-Fixpunkt nicht geprueft")
    ok("node vorhanden (PM_VECTORS_NEED_NODE)", not os.environ.get("PM_VECTORS_NEED_NODE"))

print(f"PM-VEKTOREN {sum(res)}/{len(res)} ({len(V['cases'])} Faelle)", "ALLES GRUEN" if all(res) else "FEHLER")
sys.exit(0 if all(res) else 1)
