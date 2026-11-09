"""Gjallarhorn P10: Herkunftsliste (CORS) der Knoten nach jedem hello nachziehen (saga/fed_horn.py sync_origins).
Fall aus dem Testlabor: Knoten gekoppelt, DANACH ein Server verbunden - der Knoten kannte dessen Herkunft erst nach dem
naechsten Verbinden des Steuerkanals, die Browser des neuen Servers scheiterten am geteilten Knoten mit CORS.
Ohne Server und Knoten: origins() und horn.send sind ersetzt."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from saga import fed_horn as FH, horn  # noqa: E402

res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n + ("" if c or info is None else f" -> {info!r}"), flush=True)


sent, cur = [], {"l": []}
FH.origins = lambda: list(cur["l"])
horn.send = lambda nid, m: sent.append((nid, m))
horn._CONNS.clear()
horn._CONNS.update({"n1": object(), "n2": object()})
FH._ORIG_SENT = None
FH.hello_in(7, {})
ok("erstes hello nach dem Start: Liste geht an alle Knoten", [x[0] for x in sent] == ["n1", "n2"] and sent[0][1] == {"t": "origins", "list": []}, sent)
sent.clear()
FH.hello_in(7, {})
ok("gleiche Liste: nichts erneut geschickt", sent == [], sent)
cur["l"] = ["https://b.example.org"]
FH.hello_in(7, {"horn_shared": "kaputt"})
ok("neuer Server mit horn: Liste mit seiner Herkunft an alle Knoten (auch bei unbrauchbarem hello-Zusatz)",
   len(sent) == 2 and all(m == {"t": "origins", "list": ["https://b.example.org"]} for _, m in sent), sent)
sent.clear()
cur["l"] = []
ok("Server faellt weg: sync_origins meldet Aenderung, leere Liste geht raus", FH.sync_origins() is True and len(sent) == 2 and sent[0][1]["list"] == [], sent)
sent.clear()


def boom():
    raise RuntimeError("x")


FH.origins = boom
try:
    FH.hello_in(7, {})
    ok("Fehler beim Ermitteln der Liste bricht hello nicht ab", sent == [])
except Exception as ex:  # noqa: BLE001
    ok("Fehler beim Ermitteln der Liste bricht hello nicht ab", False, ex)
print(f"\nHORN-HERKUNFT {sum(res)}/{len(res)}" + (" - ALLES GRUEN" if all(res) else " - FEHLER"))
sys.exit(0 if all(res) else 1)
