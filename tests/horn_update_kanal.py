"""Gjallarhorn: Ablage der Knoten-Binaerdateien je Kanal und Hinweis fuer zu alte Knoten (ohne Server, ohne Go).

Geprueft:
- release_path: Freigabe -> v<ver>/gjallarhorn-$ARCH, Vorabversion (testing) -> horn-testing/gjallarhorn-<ver>-$ARCH
  (wie gjallarhorn/update.go releaseURL und das CI-Vorab-Release horn-testing).
- systemd-Deploy-Skript fuer eine Vorabversion: Standardadresse horn-testing, laedt die Datei mit Version im Namen von
  einem lokalen Release-Server (Testschluessel, nie gespeichert), prueft die Signatur per openssl, legt Binaer und Unit an
  (Simulation mit HORN_ROOT, SYSTEMCTL=true); falsche Signatur bricht ab. Freigabe: v<ver>.
- upd_view: Ladefehler eines Knotens aelter als 2.5.0-rc.125 auf testing -> Code "old" (Admin-Hinweis "von Hand
  aktualisieren"); neuere Knoten, Freigaben und andere Fehler bleiben unveraendert."""
import base64
import hashlib
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

SK = Ed25519PrivateKey.generate()
PUB = base64.b64encode(SK.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
os.environ["SAGA_HORN_RELEASE_PUB"] = PUB

from saga import horn_ops as H  # noqa: E402

FAIL = []


def ok(name, cond):
    print(("OK   " if cond else "FAIL ") + name, flush=True)
    if not cond:
        FAIL.append(name)


def sign(ver, arch, data):
    msg = f"gjallarhorn-release/1\n{ver}\n{arch}\n{hashlib.sha256(data).hexdigest()}\n".encode()
    return base64.b64encode(SK.sign(msg)).decode() + "\n"


REL = {}


class Rel(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = REL.get(self.path)
        if b is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


def run_script(ver, base, root):
    old = H.VERSION
    H.VERSION = ver
    try:
        sc = H.systemd_script("horn-9.example.org", "hkh_test", udp=True)
    finally:
        H.VERSION = old
    p = os.path.join(root, "deploy.sh")
    os.makedirs(root, exist_ok=True)
    open(p, "w").write(sc)
    env = dict(os.environ, HORN_ROOT=root, HORN_RELEASE_BASE=base, SYSTEMCTL="true")
    return sc, subprocess.run(["sh", p], env=env, capture_output=True, text=True, timeout=60)


def main():
    ok("release_path Freigabe", H.release_path("2.5.0") == ("v2.5.0", "gjallarhorn-$ARCH"))
    ok("release_path Vorabversion", H.release_path("2.5.0-rc.184") == ("horn-testing", "gjallarhorn-2.5.0-rc.184-$ARCH"))

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Rel)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    tmp = tempfile.mkdtemp(prefix="horn-kanal-")
    arch = "linux-" + {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[os.uname().machine]
    try:
        ver = "2.5.0-rc.7"
        binb = b"#!/bin/sh\necho gjallarhorn\n"
        REL[f"/horn-testing/gjallarhorn-{ver}-{arch}"] = binb
        REL[f"/horn-testing/gjallarhorn-{ver}-{arch}.sig"] = sign(ver, arch, binb).encode()
        root = os.path.join(tmp, "rc")
        sc, r = run_script(ver, f"http://127.0.0.1:{port}/horn-testing", root)
        ok("Skript (Vorabversion): Standardadresse horn-testing",
           "/releases/download/horn-testing}" in sc and f'"$BASE/gjallarhorn-{ver}-$ARCH"' in sc and "/v2.5.0-rc.7" not in sc)
        exe = os.path.join(root, "opt/gjallarhorn/gjallarhorn")
        ok(f"Skript (Vorabversion): Binaer geladen, Signatur geprueft, Unit ({r.returncode} {r.stderr[-200:]})",
           r.returncode == 0 and os.access(exe, os.X_OK) and open(exe, "rb").read() == binb
           and os.path.exists(os.path.join(root, "etc/systemd/system/gjallarhorn.service")))
        REL[f"/horn-testing/gjallarhorn-{ver}-{arch}.sig"] = sign(ver + "-x", arch, binb).encode()
        root2 = os.path.join(tmp, "rc-bad")
        _, r2 = run_script(ver, f"http://127.0.0.1:{port}/horn-testing", root2)
        ok(f"Skript (Vorabversion): falsche Signatur bricht ab ({r2.returncode})",
           r2.returncode != 0 and "Signatur" in r2.stderr and not os.path.exists(os.path.join(root2, "opt/gjallarhorn/gjallarhorn")))

        rv = "2.5.1"
        REL[f"/v{rv}/gjallarhorn-{arch}"] = binb
        REL[f"/v{rv}/gjallarhorn-{arch}.sig"] = sign(rv, arch, binb).encode()
        root3 = os.path.join(tmp, "rel")
        sc3, r3 = run_script(rv, f"http://127.0.0.1:{port}/v{rv}", root3)
        ok(f"Skript (Freigabe): v<ver>/gjallarhorn-$ARCH ({r3.returncode} {r3.stderr[-200:]})",
           "/releases/download/v$VER}" not in sc3 and "/releases/download/v2.5.1}" in sc3 and r3.returncode == 0
           and os.access(os.path.join(root3, "opt/gjallarhorn/gjallarhorn"), os.X_OK))
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    e = {"state": "fehler", "code": "download", "want": "2.5.0-rc.184", "have": "2.5.0-rc.121", "err": "http 404"}
    ok("upd_view: alter Knoten auf testing -> old", H.upd_view(e, "")["code"] == "old")
    ok("upd_view: have aus der Knotenversion", H.upd_view(dict(e, have=""), "2.5.0-rc.99")["code"] == "old")
    ok("upd_view: Knoten ab rc.125 bleibt download", H.upd_view(dict(e, have="2.5.0-rc.125"), "")["code"] == "download")
    ok("upd_view: Freigabe bleibt download", H.upd_view(dict(e, want="2.5.0"), "")["code"] == "download")
    ok("upd_view: Signaturfehler bleibt sig", H.upd_view(dict(e, code="sig"), "")["code"] == "sig")
    ok("upd_view: leer", H.upd_view(None, "") == {})
    ok("upd_view: Eingabe unveraendert", e["code"] == "download")

    print("ALLES GRUEN" if not FAIL else f"FEHLER: {len(FAIL)}")
    print("Serverfehler: 0")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
