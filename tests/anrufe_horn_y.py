"""2.5 Grundsatz Y: "Alle Anrufe ueber Knoten (auch 1:1)" (calls.horn_all) ist ab Werk an (saga/horn.py horn_all_on).

Geprueft gegen die laufende Instanz (HK_TEST_BASE/HK_TEST_ADMIN, env.sh): ohne Einstellung an; Altwert von vor Y
(horn_all false ohne Merker horn_all_y) gilt als an - config.horn_dm, Admin-Seite (Haken gesetzt, neuer Hilfetext),
/api/calls/state; Admin-Formular "Gjallarhorn" speichert den Merker - danach bleibt aus aus und an an; Gjallarhorn
(calls.horn) bleibt ab Werk aus und Y wirkt erst mit ihm; "python run.py einstellen calls.horn_all=0|1" wirkt wie das
Formular und zeigt den wirksamen Wert; Foederation: Faehigkeit horn_req folgt dem wirksamen Wert. Wegwerf-Konten ca-hy*."""
import json
import os
import re
import secrets
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anrufe as A_  # noqa: E402
import auth  # noqa: E402

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok, req, res = A_.ok, A_.req, A_.res
CAPS = ["reneg", "want", "bye", "screen2", "multi", "horn"]
BASE = {"enabled": True, "stun": "stun:stun.example.org:3478", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "",
        "turn_password": "", "turn_ttl": 43200, "relay_only": False, "ring_s": 45}
ADMIN_ID = "ca-hy-adm"


def put(**kw):
    auth.save_setting("calls", dict(BASE, **kw))
    auth._settings_cache["v"] = None


def stored():
    return dict(auth.settings(fresh=True).get("calls") or {})


def wait_dm(p, want, secs=12):
    """config.horn_dm im Server (Einstellungen dort 5 s zwischengespeichert)."""
    t0, v = time.time(), None
    while time.time() - t0 < secs:
        v = req("GET", "/api/calls/config", p.k)[1].get("horn_dm")
        if bool(v) == want:
            return v
        time.sleep(0.5)
    return v


def adm(method, path, form=None):
    import urllib.parse
    ck = A_.TimestampSigner(A_.hashlib.sha256(("hk-admin|" + A_.SECRET).encode()).hexdigest()).sign(
        A_.base64.b64encode(json.dumps({"aid": ADMIN_ID, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    hd = {"Cookie": "hk_admin=" + ck, "X-Saga": "1", "Accept-Language": "de-DE"}
    body = None
    if form is not None:
        body = urllib.parse.urlencode(dict(form, csrf="x"))
        hd["Content-Type"] = "application/x-www-form-urlencoded"
    c = A_.http.client.HTTPConnection(A_.ADM.hostname, A_.ADM.port, timeout=30)
    c.request(method, path, body=body, headers=hd)
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, data


def box(html):
    """Haken "Alle Anrufe ueber Knoten" auf Admin -> Anrufe: (gefunden, gesetzt, Hilfetext)."""
    m = re.search(r'<input type="checkbox" name="horn_all" value="1"( checked)?><span>[^<]*<small>([^<]*)</small>', html)
    return (bool(m), bool(m and m.group(1)), m.group(2) if m else "")


def state_all():
    s, d = adm("GET", "/api/calls/state")
    return json.loads(d).get("horn_all") if s == 200 else None


def einstellen(*args):
    r = subprocess.run([sys.executable, "run.py", "einstellen", *args], cwd=APP, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, PYTHONPATH=APP))
    return r.returncode, r.stdout + r.stderr


def main():
    A_.cleanup()
    prev = auth.settings(fresh=True)
    keep = {k: prev.get(k) for k in ("chat_enabled", "calls")}
    auth.save_setting("chat_enabled", True)
    auth.create_user("ca-hya", A_.PW, "Anna Y")
    auth.get_user(ADMIN_ID) or auth.create_user(ADMIN_ID, secrets.token_urlsafe(12), "Admin Y", is_admin=True)
    try:
        run()
    finally:
        for k, v in keep.items():
            auth.save_setting(k, v if v is not None else ({} if k == "calls" else True))
        auth._settings_cache["v"] = None
        A_.cleanup()
    print(f"\nANRUFE-HORN-Y {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    from saga import federation as F, horn as HN
    A = A_.Person("ca-hya")
    req("PUT", "/api/calls/caps", A.k, {"device": A.dev["id"], "caps": CAPS})

    # --- Hilfsfunktion ------------------------------------------------------------------------------------------------
    ok("horn_all_on: fehlend = an, Altwert false ohne Merker = an, mit Merker gilt der Wert",
       HN.horn_all_on({}) is True and HN.horn_all_on({"horn_all": False}) is True and HN.horn_all_on({"horn_all": True}) is True
       and HN.horn_all_on({"horn_all": False, "horn_all_y": True}) is False and HN.horn_all_on({"horn_all": True, "horn_all_y": True}) is True
       and HN.horn_all_on({"horn_all_y": True}) is True)

    # --- Ab Werk: Gjallarhorn aus, Y wartet -------------------------------------------------------------------------
    put()
    ok("ab Werk: Gjallarhorn aus -> kein horn_dm (Y greift erst mit Gjallarhorn)", not wait_dm(A, False))
    st = stored()
    ok("ab Werk: horn_all wirksam an (Admin-Zustand), gespeichert ist nichts", state_all() is True and "horn_all" not in st and "horn_all_y" not in st)
    caps = F.caps2()
    ok(f"Foederation: ohne Gjallarhorn kein horn_req ({caps})", "horn_req" not in caps and "horn_dm" not in caps)

    # --- Gjallarhorn an, horn_all nie gesetzt -----------------------------------------------------------------------
    put(horn=True)
    ok("Gjallarhorn an, horn_all nie gesetzt -> horn_dm an", wait_dm(A, True) is True)
    ok("Foederation: horn_req gemeldet (Y)", "horn_req" in F.caps2() and "horn_dm" in F.caps2())

    # --- Altwert von vor Y ------------------------------------------------------------------------------------------
    put(horn=True, horn_builtin=True, horn_all=False, horn_rev=3)
    ok("Altwert horn_all false ohne Merker -> horn_dm an", wait_dm(A, True) is True)
    s, html = adm("GET", "/calls")
    found, on, hint = box(html)
    # 2.6 (2263-4): kein "experimentell" mehr (weder Kasten noch Marke noch Hilfetext) - statt dessen die Grenzen je Anruf
    ok(f"Admin-Seite: Haken gesetzt (wirksamer Wert), Hilfetext 'Standard an', ohne 'experimentell', mit Grenzen ({s}, {found}, {on})",
       s == 200 and found and on and hint.startswith("Standard an") and 'class="horn-exp-note"' not in html
       and "xperimentell" not in html and 'class="hk-hint horn-limits"' in html, )
    ok("Admin-Zustand: horn_all an", state_all() is True)
    ok("Foederation: horn_req auch beim Altwert", "horn_req" in F.caps2())

    # --- Admin-Formular: bewusst aus -> bleibt aus ------------------------------------------------------------------
    s, _ = adm("POST", "/calls", {"action": "horn", "horn": "1", "horn_builtin": "1"})
    st = stored()
    ok(f"Formular ohne Haken: gespeichert aus + Merker ({s}, {st.get('horn_all')}, {st.get('horn_all_y')}, rev {st.get('horn_rev')})",
       s in (302, 303) and st.get("horn_all") is False and st.get("horn_all_y") is True and st.get("horn_rev") == 4)
    # 2263-4 (Pruefung a63srev M1): Gjallarhorn unveraendert gespeichert -> noch kein Merker horn_on_y (sonst bliebe ein
    # altes "aus" beim Wechsel auf "Standard an" haengen); erst eine echte Aenderung setzt ihn
    ok(f"... Gjallarhorn unveraendert: kein Merker horn_on_y (2263-4, M1) ({st.get('horn')}, {st.get('horn_on_y')})",
       st.get("horn") is True and not st.get("horn_on_y"))
    ok("... horn_dm aus", wait_dm(A, False) is False)
    _, html = adm("GET", "/calls")
    ok("... Admin-Seite: Haken nicht gesetzt", box(html)[0] and not box(html)[1])
    ok("... Admin-Zustand aus, Foederation ohne horn_req", state_all() is False and "horn_req" not in F.caps2() and "horn_dm" in F.caps2())
    # weiteres Speichern (z. B. Grenzen) laesst es aus
    adm("POST", "/calls", {"action": "limits", "horn_from": "3"})
    st = stored()
    ok("anderes Formular speichern: bleibt aus (Merker bleibt)", st.get("horn_all") is False and st.get("horn_all_y") is True and wait_dm(A, False) is False)
    s, _ = adm("POST", "/calls", {"action": "horn", "horn": "1", "horn_builtin": "1", "horn_all": "1"})
    ok("Formular mit Haken: an", stored().get("horn_all") is True and wait_dm(A, True) is True)

    # --- run.py einstellen ------------------------------------------------------------------------------------------
    put(horn=True, horn_all=False)                               # Altwert ohne Merker
    rc, out = einstellen()
    ok(f"einstellen ohne Argumente: zeigt wirksamen Wert calls.horn_all=1 beim Altwert ({rc})", rc == 0 and "calls.horn_all=1" in out, )
    rc, out = einstellen("calls.horn_all=0")
    st = stored()
    ok(f"einstellen calls.horn_all=0: aus + Merker wie das Formular ({rc}, {st.get('horn_all')}, {st.get('horn_all_y')})",
       rc == 0 and "calls.horn_all=0" in out and st.get("horn_all") is False and st.get("horn_all_y") is True)
    ok("... horn_dm aus", wait_dm(A, False) is False)
    rc, out = einstellen("calls.horn_all=1")
    ok("einstellen calls.horn_all=1: an", rc == 0 and "calls.horn_all=1" in out and stored().get("horn_all") is True and wait_dm(A, True) is True)
    rc, out = einstellen("calls.horn=0")
    st = stored()
    ok(f"einstellen calls.horn=0: Gjallarhorn aus -> kein horn_dm, horn_all bleibt, Merker horn_on_y (2263-4) ({st.get('horn_on_y')})",
       rc == 0 and "calls.horn=0" in out and st.get("horn_all") is True and st.get("horn") is False and st.get("horn_on_y") is True
       and not wait_dm(A, False))
    m1(HN)


def m1(HN):
    """Pruefung a63irev (Auflage M1, auch mit dem Commit "Standard an"): der Merker horn_on_y entsteht nur bei einer echten
    Aenderung von Gjallarhorn (Admin-Formular, run.py einstellen). Geprueft wird der gespeicherte Stand und was er unter
    beiden Standards (HORN_DEFAULT aus = dieser Stand, an = "Standard an") bedeutet - laeuft vor und nach "Standard an"."""
    def under(st, d):
        old = HN.HORN_DEFAULT
        HN.HORN_DEFAULT = d
        try:
            return HN.horn_flag(st)
        finally:
            HN.HORN_DEFAULT = old

    def form(horn_on):
        f = {"action": "horn", "horn_builtin": "1", "horn_all": "1"}
        if horn_on:
            f["horn"] = "1"
        return adm("POST", "/calls", f)[0]

    # Altwert "aus" ohne Merker, Admin speichert nur andere Schalter (Gjallarhorn so, wie die Seite ihn zeigt)
    put(horn=False)
    s = form(HN.horn_flag(stored()))
    st = stored()
    ok(f"M1 Formular, Gjallarhorn unveraendert (Altwert aus): kein Merker, mit Standard an dann an ({s}, {st.get('horn')}, {st.get('horn_on_y')})",
       s in (302, 303) and not st.get("horn_on_y") and under(st, True) is True and under(st, False) is HN.horn_flag({"horn": False}))
    # Merker schon da: bleibt, auch wenn nur andere Schalter gespeichert werden
    put(horn=False, horn_on_y=True)
    s = form(False)
    st = stored()
    ok(f"M1 Formular mit vorhandenem Merker: Merker bleibt, aus bleibt aus ({s}, {st.get('horn_on_y')})",
       st.get("horn_on_y") is True and under(st, True) is False and under(st, False) is False)
    # Admin schaltet bewusst aus (vorher an) -> Merker, bleibt unter beiden Standards aus
    put(horn=True)
    s = form(False)
    st = stored()
    ok(f"M1 Formular bewusst aus: Merker, bleibt auch mit Standard an aus ({s}, {st.get('horn')}, {st.get('horn_on_y')})",
       st.get("horn") is False and st.get("horn_on_y") is True and under(st, True) is False and under(st, False) is False)
    # Admin schaltet bewusst an (vom Altwert aus) -> an unter beiden Standards
    put(horn=False)
    s = form(True)
    st = stored()
    ok(f"M1 Formular an: wirksam an unter beiden Standards ({s}, {st.get('horn')}, {st.get('horn_on_y')})",
       st.get("horn") is True and under(st, True) is True and under(st, False) is True
       and bool(st.get("horn_on_y")) is (HN.horn_flag({"horn": False}) is False))
    # run.py einstellen: unveraendert -> kein Merker; bewusst aus -> Merker
    put(horn=False)
    rc, out = einstellen("calls.horn=" + ("1" if HN.horn_flag(stored()) else "0"))
    st = stored()
    ok(f"M1 einstellen calls.horn unveraendert (Altwert aus): kein Merker, mit Standard an dann an ({rc}, {st.get('horn_on_y')})",
       rc == 0 and not st.get("horn_on_y") and under(st, True) is True)
    put(horn=True)
    rc, out = einstellen("calls.horn=0")
    st = stored()
    ok(f"M1 einstellen calls.horn=0 (vorher an): Merker, bleibt auch mit Standard an aus ({rc}, {st.get('horn_on_y')})",
       rc == 0 and st.get("horn") is False and st.get("horn_on_y") is True and under(st, True) is False and "calls.horn=0" in out)
    put(horn=True)
    rc, out = einstellen("calls.horn_all=0")
    st = stored()
    ok(f"M1 einstellen nur calls.horn_all: kein Merker horn_on_y ({rc}, {st.get('horn_on_y')})", rc == 0 and not st.get("horn_on_y"))


if __name__ == "__main__":
    main()
