"""2.5 (Vorgabe 10.11.): Gjallarhorn als "Experimentell" gekennzeichnet - Admin (Anrufe, Netz, Allgemein) und Web-Anruf.

Gegen die laufende Instanz (HK_TEST_BASE/HK_TEST_ADMIN), am besten in der Testumgebung mit Knoten:
  UMG_NS=.. UMG_DB=.. tools/testumgebung/umgebung.sh lauf knoten <slot> horn_experimentell.py
Geprueft: Admin -> Anrufe in 5 Sprachen mit Marke und Hinweis-Kasten (Admin-Fassung mit "Größere Anrufe folgen nach 2.5."),
der Hilfetext "Alle Anrufe über Knoten" ohne eigenes "(in 2.5 experimentell)", Marke in der Legende der Netz-Karte und
neben dem Verweis auf die Seite Anrufe (Allgemein); danach tests/browser_horn_experimentell.js (Admin- und Anruf-Bilder,
Marke im Anrufkopf und Satz in der Teilnehmerliste). HX_VORHER=1: nur die Bilder (Stand ohne Marke), keine Pruefung.
Wegwerf-Konto um-hx-adm."""
import base64
import hashlib
import http.client
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse

import auth
import db
from itsdangerous import TimestampSigner

HERE = os.path.dirname(os.path.abspath(__file__))
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8001"))
VORHER = os.environ.get("HX_VORHER") == "1"
ADMIN_ID = "um-hx-adm"
SATZ = "Gjallarhorn ist in Saga 2.5 experimentell – getestet mit bis zu 4 Personen bzw. Geräten je Anruf. Probleme bitte über „Diagnose senden“ melden."
TAG = 'class="adm-tag exp"'
res = []


def ok(name, cond, info=None):
    res.append(bool(cond))
    print(("OK  " if cond else "FAIL") + " " + name + ("" if cond or info is None else " -> " + str(info)[:600]), flush=True)


def cookie():
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + auth.SECRET).encode()).hexdigest())
    return sa.sign(base64.b64encode(json.dumps({"aid": ADMIN_ID, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def adm(path, lang="de-DE"):
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    c.request("GET", path, headers={"Cookie": "hk_admin=" + cookie(), "Accept-Language": lang})
    r = c.getresponse()
    body = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, body


def main():
    db.wait_ready()
    db.init_schema()
    auth.get_user(ADMIN_ID) or auth.create_user(ADMIN_ID, secrets.token_urlsafe(12), "Admin HX", is_admin=True)
    if not VORHER:
        s, h = adm("/calls")
        ok(f"Admin 'Anrufe' (de): Marke an 'Gjallarhorn' und 'Knoten', Hinweis-Kasten mit Admin-Satz ({s})",
           s == 200 and h.count(TAG) >= 2 and 'class="horn-exp"' in h and SATZ in h and "Größere Anrufe folgen nach 2.5." in h)
        ok("Hilfetext 'Alle Anrufe über Knoten' ohne eigenes '(in 2.5 experimentell)'",
           "(in 2.5 experimentell)" not in h and "Wirkt nur, solange Gjallarhorn an ist. Ist kein" in h)
        for lg, w in (("en", ">Experimental<"), ("es", ">Experimental<"), ("ru", ">Экспериментально<"), ("th", ">ทดลอง<")):
            s, h = adm("/calls", lg)
            ok(f"Admin 'Anrufe' ({lg}): Marke {w} und Hinweis-Kasten ({s})", s == 200 and w in h and 'class="horn-exp"' in h and SATZ not in h)
        s, h = adm("/net")
        ok(f"Netz-Karte: Marke in der Legende bei 'Gjallarhorn-Knoten' ({s})",
           s == 200 and 'Gjallarhorn-Knoten <span class="adm-tag exp">Experimentell</span>' in h)
        s, h = adm("/settings")
        ok(f"Allgemein: Marke beim Verweis auf die Seite Anrufe ({s})", s == 200 and TAG in h and 'href="/calls"' in h)
        s, h = adm("/federation")
        ok(f"Föderation: Seite lädt ({s})", s == 200)
    env = dict(os.environ, SAGA_ADMIN=f"http://{ADM.hostname}:{ADM.port}", SAGA_ADMIN_COOKIE=cookie(),
               SHOTS=os.environ.get("HX_SHOTS") or os.environ.get("SHOTS") or "/tmp")
    r = subprocess.run(["node", os.path.join(HERE, "browser_horn_experimentell.js")], env=env, capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    print("\n".join("  [browser] " + x for x in out.splitlines() if x.strip()), flush=True)
    if not VORHER:
        ok(f"Browser: Admin- und Anruf-Pruefung (rc {r.returncode})", r.returncode == 0)
    n = sum(res)
    print(f"\nHORN-EXPERIMENTELL {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
    sys.exit(0 if n == len(res) else 1)


if __name__ == "__main__":
    main()
