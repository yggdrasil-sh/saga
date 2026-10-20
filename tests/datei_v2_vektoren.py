"""Datei-Format saga-file/v2 (2.4, Videos im Chat): gemeinsame Testvektoren tests/vectors/file_v2.json gegen PyNaCl
(tests/chatlib.py), die Server-Kopfpruefung (saga/file_v2.py) und - wenn node da ist - libsodium.js + static/chat-crypto.js
(wie Browser und Service Worker). Ohne Server, ohne Datenbank."""
import os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import file_vectors_v2  # noqa: E402
from saga import file_v2 as FV  # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


for n, good in file_vectors_v2.check():
    ok(n, good)
# Server: Grenzen und Kopf-Faelle
ok("Server: Groesse 0 -> 64 Bytes", FV.cipher_size(0) == 64)
ok("Server: plain_max_for ist Umkehrung", all(FV.cipher_size(FV.plain_max_for(FV.cipher_size(s))) == FV.cipher_size(s)
                                              for s in (0, 1, FV.CS - 1, FV.CS, FV.CS + 1, 500 << 20)))
ok("Server: plain_max_for(500 MB + Kopf) passt", FV.cipher_size(FV.plain_max_for(500 << 20)) <= 500 << 20)
h = FV.header(10, b"\1" * 16)
ok("Server: Kopf mit falscher Groesse abgelehnt", FV.check_blob(h, FV.cipher_size(10) + 1) is None and FV.check_blob(h, FV.cipher_size(10)))
ok("Server: cs keine Zweierpotenz abgelehnt", FV.parse_header(FV.header(10, b"\1" * 16, cs=3 * 65536)) is None)
ok("Server: zu kurzer Kopf abgelehnt", FV.parse_header(h[:47]) is None)
node = shutil.which("node")
if node:
    r = subprocess.run([node, os.path.join(ROOT, "tools", "file_vectors_v2.mjs")], capture_output=True, text=True, timeout=120)
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    ok(f"node file_vectors_v2.mjs: {last}", r.returncode == 0 and "ALLES GRUEN" in last)
else:
    print("(node fehlt - Gegenprobe libsodium.js laeuft im Browser-Test tests/videos.py)")
print(f"\nDATEI-V2 {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
sys.exit(0 if all(res) else 1)
