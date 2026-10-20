"""Chat-Krypto saga-chat/v2 (2.3): gemeinsame Testvektoren tests/vectors/chat_v2.json gegen PyNaCl (tests/chatlib.py), die
Server-Referenz (saga/chat_v2.py) und - wenn node da ist - libsodium.js + static/chat-crypto.js (wie im Browser).
Dazu: v1-Vektoren unveraendert gruen (Altbestand lokaler Chats), Gelesen-Bestaetigungen (2.4, tests/vectors/receipts.json) und
Chat-Stufe 3 (2.5: Reaktionen, Bearbeiten, Vertrauens-Uebernahme, tests/vectors/chat_v3.json, saga/chat_v3.py).
Ohne Server, ohne Datenbank."""
import os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import chat_vectors  # noqa: E402
import chat_vectors_v2  # noqa: E402
import chat_vectors_v3  # noqa: E402  (2.5: Stufe 3, tests/vectors/chat_v3.json)
import receipt_vectors  # noqa: E402  (2.4: Gelesen-Bestaetigungen, tests/vectors/receipts.json)

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


for n, good in chat_vectors_v2.check():
    ok("v2 " + n, good)
for n, good in chat_vectors_v3.check():
    ok("v3 " + n, good)
for n, good in chat_vectors.check():
    ok("v1 " + n, good)
for n, good in receipt_vectors.check():
    ok("gelesen " + n, good)
node = shutil.which("node")
if node:
    for script in ("chat_vectors_v2.mjs", "chat_vectors_v3.mjs", "chat_vectors.mjs", "receipt_vectors.mjs"):
        r = subprocess.run([node, os.path.join(ROOT, "tools", script)], capture_output=True, text=True, timeout=120)
        last = (r.stdout.strip().splitlines() or ["?"])[-1]
        ok(f"node {script}: {last}", r.returncode == 0 and "ALLES GRUEN" in last)
else:
    print("(node fehlt - Gegenprobe libsodium.js laeuft in den Browser-Tests)")
print(f"\n{sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
sys.exit(0 if all(res) else 1)
