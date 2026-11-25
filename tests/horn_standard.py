"""2.6 (Bugs-DB 2263-4): Gjallarhorn ohne "experimentell" - Admin (Anrufe, Netz, Allgemein, Foederation) und der zentrale
Schalter horn.horn_on() mit Merker horn_on_y (frueher tests/horn_experimentell.py, 2.5 Vorgabe 10.11.).

Gegen die laufende Instanz (HK_TEST_BASE/HK_TEST_ADMIN), am besten in der Testumgebung mit Knoten:
  UMG_NS=.. UMG_DB=.. tools/testumgebung/umgebung.sh lauf knoten <slot> horn_standard.py
Geprueft: Admin -> Anrufe in 5 Sprachen ohne Marke "Experimentell" und ohne Hinweis-Kasten ("getestet mit bis zu 4",
"Groessere Anrufe folgen nach 2.5"), statt dessen die wirksamen Grenzen je Anruf (horn.limits, auch vom Admin gesenkt);
Netz-Karte, Allgemein und Foederation ohne Marke; Zeile "Geschaetzte Kapazitaet" erscheint ohne Messwerte nicht.
Merker: horn_flag/horn_on fuer fehlend, Altwert ohne Merker und mit Merker; calls.horn_on, Foederation (CAPS2) und
/api/calls/config folgen demselben Wert. Ab Werk (in diesem Schritt) weiter aus. Die Anruf-Bilder im Browser
(Marke im Anrufkopf) gehoeren zu tests/browser_horn_experimentell.js (Web, 2263-5). Wegwerf-Konto um-hs-adm."""
import base64
import hashlib
import http.client
import json
import os
import secrets
import sys
import time
import urllib.parse

import auth
import db
from itsdangerous import TimestampSigner

ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8001"))
ADMIN_ID = "um-hs-adm"
SATZ = "getestet mit bis zu 4 Personen"
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


def put_calls(**kw):
    cur = dict(auth.settings(fresh=True).get("calls") or {})
    for k, v in kw.items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    auth.save_setting("calls", cur)
    auth._settings_cache["v"] = None
    time.sleep(5.5)                                  # Einstellungen im Server 5 s zwischengespeichert


def merker():
    from saga import calls, federation as F, horn as HN
    ok("horn_flag: fehlend = aus (HORN_DEFAULT in diesem Schritt aus)", HN.HORN_DEFAULT is False and HN.horn_flag({}) is False)
    ok("horn_flag: Altwert ohne Merker gilt wie gespeichert (an bleibt an, aus bleibt aus)",
       HN.horn_flag({"horn": True}) is True and HN.horn_flag({"horn": False}) is False)
    ok("horn_flag: mit Merker gilt der gespeicherte Wert",
       HN.horn_flag({"horn": True, "horn_on_y": True}) is True and HN.horn_flag({"horn": False, "horn_on_y": True}) is False)
    ok("horn_on: nur mit eingeschalteten Anrufen (calls.enabled)", HN.horn_on({"horn": True, "enabled": False}) is False
       and HN.horn_on({"horn": True}) is True)
    keep = dict(auth.settings(fresh=True).get("calls") or {})
    try:
        for want, kw in ((False, {"horn": None, "horn_on_y": None}), (True, {"horn": True, "horn_on_y": None}),
                         (False, {"horn": False, "horn_on_y": True}), (True, {"horn": True, "horn_on_y": True})):
            cur = dict(keep)
            for k, v in kw.items():
                cur.pop(k, None) if v is None else cur.__setitem__(k, v)
            auth.save_setting("calls", cur)
            auth._settings_cache["v"] = None
            caps = F.caps2()
            ok(f"Lesestellen folgen horn_on {kw}: calls.horn_on, features, CAPS2 horn/horn_dm = {want}",
               calls.horn_on() is want and ("horn" in calls.features()) is want and ("horn" in caps) is want
               and ("horn_dm" in caps) is want, caps)
    finally:
        auth.save_setting("calls", keep)
        auth._settings_cache["v"] = None


def admin():
    from saga import horn as HN
    put_calls(horn=True, horn_on_y=True, horn_video=None, horn_audio=None)
    (vt, at), (vu, au) = HN.limits(None, udp=False), HN.limits(None, udp=True)
    s, h = adm("/calls")
    ok(f"Admin 'Anrufe' (de): keine Marke, kein Kasten, kein 'experimentell' ({s})",
       s == 200 and TAG not in h and 'class="horn-exp-note"' not in h and SATZ not in h and "Größere Anrufe folgen" not in h
       and "xperimentell" not in h)
    zeile = f"Grenzen je Anruf über Knoten: {vt} mit Video / {at} nur Ton über TCP bzw. Tunnel, {vu} / {au} mit UDP am Knoten."
    ok(f"Admin 'Anrufe' (de): Grenzen aus horn.limits ({vt}/{at}, {vu}/{au})", zeile in h, h[h.find("horn-limits") - 20:][:300])
    ok("Admin 'Anrufe': ohne Messwerte keine Zeile 'Geschätzte Kapazität'", "Geschätzte Kapazität" not in h and 'class="horn-cap"' not in h)
    ok("Hilfetext 'Alle Anrufe über Knoten' unveraendert", "Wirkt nur, solange Gjallarhorn an ist. Ist kein" in h)
    for lg, w in (("en", "Experimental"), ("es", "Experimental"), ("ru", "Экспериментально"), ("th", "ทดลอง")):
        s, h = adm("/calls", lg)
        ok(f"Admin 'Anrufe' ({lg}): ohne Marke {w}, mit Grenzen-Zeile ({s})",
           s == 200 and f">{w}<" not in h and TAG not in h and 'class="hk-hint horn-limits"' in h)
    put_calls(horn_video=6, horn_audio=9)
    s, h = adm("/calls")
    ok(f"Admin senkt: Grenzen-Zeile zeigt die gesenkten Werte ({s})",
       "Grenzen je Anruf über Knoten: 6 mit Video / 9 nur Ton über TCP bzw. Tunnel, 6 / 9 mit UDP am Knoten." in h)
    put_calls(horn_video=None, horn_audio=None)
    s, h = adm("/net")
    ok(f"Netz-Karte: Legende 'Gjallarhorn-Knoten' ohne Marke ({s})", s == 200 and "Gjallarhorn-Knoten</li>" in h and TAG not in h)
    s, h = adm("/settings")
    ok(f"Allgemein: Verweis auf die Seite Anrufe ohne Marke ({s})", s == 200 and TAG not in h and 'href="/calls"' in h)
    s, h = adm("/federation")
    ok(f"Föderation: Seite lädt, ohne Marke ({s})", s == 200 and TAG not in h)


def main():
    db.wait_ready()
    db.init_schema()
    auth.get_user(ADMIN_ID) or auth.create_user(ADMIN_ID, secrets.token_urlsafe(12), "Admin HS", is_admin=True)
    keep = auth.settings(fresh=True).get("calls")
    try:
        merker()
        admin()
    finally:
        auth.save_setting("calls", keep if keep is not None else {})
        auth._settings_cache["v"] = None
    n = sum(res)
    print(f"\nHORN-STANDARD {n}/{len(res)} - {'ALLES GRUEN' if n == len(res) else 'FEHLER'}")
    sys.exit(0 if res and n == len(res) else 1)


if __name__ == "__main__":
    main()
