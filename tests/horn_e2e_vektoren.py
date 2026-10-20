"""Frame-E2E Gjallarhorn (saga-horn/v1, docs/HORN-E2E.md): Testvektoren tests/vectors/horn_e2e.json gegen die Python-
Umsetzung (tools/horn_e2e_vectors.py, cryptography) und - wenn node da ist - gegen den Web-Kern static/frame-e2e.js
(tools/horn_e2e_vectors.mjs: Vektoren und Schluesselplan mit eingespeister Uhr). Im Browser: tests/browser_horn_e2e.js.
Ohne Server, ohne Datenbank."""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import horn_e2e_vectors as H  # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)


with open(H.PATH, encoding="utf-8") as f:
    V = json.load(f)
bad = H.check(V)
ok(f"Python: Vektoren ({len(V['frames'])} Rahmen, {len(V['cases'])} Empfaenger-Faelle, {len(V['runes'])} Runensiegel)", not bad)
for b in bad[:12]:
    print("  " + b)
ok("Format saga-horn/v1, Trailer 14 Byte, Ring 16, Fenster 128", V["format"] == "saga-horn/v1" and V["params"]["trailer_len"] == 14
   and V["params"]["kid_ring"] == 16 and V["params"]["window"] == 128)
node = shutil.which("node")
if node:
    r = subprocess.run([node, os.path.join(ROOT, "tools", "horn_e2e_vectors.mjs")], capture_output=True, text=True, timeout=300)
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    ok(f"node horn_e2e_vectors.mjs: {last}", r.returncode == 0 and "ALLES GRUEN" in last)
    for ln in r.stdout.splitlines():
        if ln.startswith("PERF") or ln.startswith("FAIL"):
            print("  " + ln)
else:
    print("(node fehlt - Gegenprobe laeuft im Browser-Test tests/browser_horn_e2e.js)")
print(f"\nHORN-E2E-VEKTOREN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
sys.exit(0 if all(res) else 1)
