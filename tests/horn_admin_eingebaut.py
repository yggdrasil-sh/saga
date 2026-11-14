"""2.5 (horn-admin): eingebauter Gjallarhorn-Knoten sichtbar und verstaendlich - mit echtem saga-media vor Saga.

Teil 1 (Attrappe statt gjallarhorn, Verhalten per Datei): saga-media meldet Zustand und Grund (POST /internal/horn/state,
von aussen 404), Admin -> Anrufe zeigt die Zeile "Eingebaut" mit "Startet nicht" und Grund (kein Zertifikat mit Hinweis
und Link auf die Anleitung; Port belegt; sonstiger Abbruch mit bereinigter Fehlerzeile ohne Pfade/Kennungen), keine
Pfade/Geheimnisse in /api/calls/state; Log: Grund- und Knotenzeile nur einmal trotz mehrerer Fehlversuche; Speichern warnt
("startet so nicht") und startet trotz Backoff sofort neu; "Jetzt erneut versuchen"; laufender Knoten -> "Startet";
Schalter aus -> "Abgeschaltet". Teil 2: SAGA_HORN=off -> "Nicht verfuegbar" mit Grund.
Teil 3 (echtes gjallarhorn, HORN_NAME ohne Zertifikatsdateien, ACME nicht erreichbar): der eingebaute bekommt im welcome
die ACME-Art (dns01 mit Cloudflare-Token, nach "Token entfernen" alpn), meldet sein Zertifikat, gilt ohne Zertifikat NICHT
als bereit (Status nocert, kein Kandidat), Admin-Zeile mit Zertifikatszustand und Hinweis "TLS-ALPN-01 klappt nur ...:443";
UDP-Selbsttest wird nach dem Handshake nachgereicht (udp_check nicht "unbekannt").

  Umgebung wie die anderen Reihen (env.sh); Ports aus HK_TEST_BASE/HK_TEST_ADMIN (Standard 8461/8460), intern +10001, Knoten +10002.
  SAGA_MEDIA_BIN / GJALLARHORN_BIN, sonst go build aus media/ bzw. gjallarhorn/ (fehlt eins: FEHLER)."""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
# Ports aus HK_TEST_BASE/HK_TEST_ADMIN (wie die anderen Reihen), intern/Knoten = Saga-Port + 10001/10002
PORT = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8461")).port or 8461
ADMIN = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8460")).port or 8460
INTERNAL, HORN_PORT = PORT + 10001, PORT + 10002
STATE = os.environ.get("STATE_DIR", "/tmp/st-horn-admin")
SECRET = os.environ.get("SESSION_SECRET") or os.environ["SECRET_KEY"]
MODE = os.path.join(STATE, "fake-mode")
FAKE = os.path.join(STATE, "fake-gjallarhorn")
res = []
P = {"proc": None, "log": None}


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL") + " " + n + ("" if c or info is None else " -> " + str(info)[:400]), flush=True)


# --- Instanz -----------------------------------------------------------------------------------------------------------
def build(name, d, env_bin):
    b = os.environ.get(env_bin)
    if b:
        return b
    if not shutil.which("go"):
        return None
    out = os.path.join(STATE, name)
    r = subprocess.run(["go", "build", "-trimpath", "-o", out, "."], cwd=os.path.join(APP, d), capture_output=True, text=True,
                       env=dict(os.environ, CGO_ENABLED="0"))
    return None if r.returncode else out


def fake_knot():
    with open(FAKE, "w") as f:
        f.write(f"""#!/bin/sh
M=$(cat {MODE} 2>/dev/null || echo nocert)
echo x >> {MODE}.starts
case "$M" in
  nocert) echo "fehler: kein Zertifikat: HORN_TLS_CERT/HORN_TLS_KEY, /data/tls/fullchain.pem, HORN_TLS_SELF=1 oder HORN_NAME fuer ACME setzen" >&2; exit 1;;
  port) echo "fehler: listen tcp :8443: bind: address already in use" >&2; exit 1;;
  other) echo "fehler: open /data/horn/node.key: permission denied (abc0123456789abcdef0123456789abcdef)" >&2; exit 1;;
  run) exec sleep 600;;
esac
""")
    os.chmod(FAKE, 0o755)


def start(media, node, extra=None):
    env = {k: v for k, v in os.environ.items() if k not in ("PORT", "ADMIN_PORT", "PYTHONPATH", "SAGA_MEDIA_KEY", "SAGA_INTERNAL_PORT",
                                                             "SAGA_HORN_KEY", "HOST", "MEDIA_ACCEL", "SAGA_HORN") and not k.startswith("HORN_")}
    env.update(PORT=str(PORT), MEDIA_INTERNAL_PORT=str(INTERNAL), ADMIN_PORT=str(ADMIN), ADMIN_HOST="127.0.0.1", HOST="127.0.0.1",
               STATE_DIR=STATE, BASE_URL=f"http://127.0.0.1:{PORT}", CANONICAL_REDIRECT="false", SAGA_SKIP_BACKUP="1",
               SAGA_UPDATE_CHECK="0", PYTHONPATH=APP, SECRET_KEY=os.environ["SECRET_KEY"], SESSION_SECRET=SECRET,
               GJALLARHORN_BIN=node, HORN_PORT=str(HORN_PORT), SAGA_CF_API="http://127.0.0.1:9/client/v4")
    env.update(extra or {})
    P["log"] = open(os.path.join(STATE, "server.log"), "w")
    P["proc"] = subprocess.Popen([media, "--", sys.executable, "run.py"], env=env, cwd=APP, stdout=P["log"], stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            if req("GET", "/healthz")[0] == 200 and adm("GET", "/api/calls/state")[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def stop():
    p = P["proc"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(25)
        except subprocess.TimeoutExpired:
            p.kill()
    if P["log"]:
        P["log"].close()
    P["proc"] = None


def log_text():
    try:
        return open(os.path.join(STATE, "server.log"), encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def starts():
    try:
        return open(MODE + ".starts").read().count("x")
    except OSError:
        return 0


def req(method, path, body=None, headers=None, port=PORT):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request(method, path, body=body, headers=dict({"X-Saga": "1", "Accept-Language": "de-DE"}, **(headers or {})))
    r = c.getresponse()
    data = r.read()
    hs = dict(r.getheaders())
    sc = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
    c.close()
    return r.status, data.decode("utf-8", "replace"), hs, sc


JAR = {}


def _cookie():
    if "hk_admin" not in JAR:
        from itsdangerous import TimestampSigner
        sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
        JAR["hk_admin"] = sa.sign(base64.b64encode(json.dumps({"aid": "ca-hb-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    return JAR["hk_admin"]


def adm(method, path, form=None):
    """Admin-Bereich mit Sitzung (csrf x); merkt neue Sitzungs-Cookies (Hinweise nach dem Speichern)."""
    body, hd = None, {"Cookie": "hk_admin=" + _cookie()}
    if form is not None:
        body = urllib.parse.urlencode(dict(form, csrf="x"))
        hd["Content-Type"] = "application/x-www-form-urlencoded"
    s, data, hs, sc = req(method, path, body, hd, port=ADMIN)
    for v in sc:
        m = re.match(r"hk_admin=([^;]+)", v)
        if m and m.group(1) not in ("", '""', "null"):
            JAR["hk_admin"] = m.group(1)
    return s, data, hs


def state():
    s, d, _ = adm("GET", "/api/calls/state")
    return json.loads(d) if s == 200 else {}


def wait_bi(cond, secs=20):
    t0 = time.time()
    st = {}
    while time.time() - t0 < secs:
        st = state()
        if cond(st.get("bi") or {}, st):
            return st
        time.sleep(0.5)
    return st


def settings(**calls):
    import auth
    cur = auth.settings(fresh=True).get("calls") or {}
    auth.save_setting("calls", {**cur, **calls})


def section(html):
    i = html.find('id="horn"')
    return html[i:html.find("</section>", i)] if i >= 0 else ""


def builtin_node(st):
    return next((n for n in st.get("nodes") or [] if n.get("builtin")), None)


# --- Teile -------------------------------------------------------------------------------------------------------------
def teil1(media):
    fake_knot()
    open(MODE, "w").write("nocert")
    if os.path.exists(MODE + ".starts"):
        os.remove(MODE + ".starts")
    settings(enabled=True, horn=True, horn_builtin=True)
    ok("Teil 1: Instanz mit saga-media + Attrappe startet", start(media, FAKE))
    s, _, _, _ = req("POST", "/internal/horn/state", json.dumps({"state": "run"}), {"Content-Type": "application/json"})
    s2, _, _, _ = req("GET", "/internal/horn/conf")
    ok(f"/internal/horn/state und /conf von aussen gesperrt ({s}, {s2})", s == 404 and s2 == 404)
    st = wait_bi(lambda b, _: b.get("status") == "fail" and b.get("reason") == "nocert" and b.get("fails", 0) >= 1)
    bi = st.get("bi") or {}
    ok("Zeile Eingebaut: startet nicht, Grund kein Zertifikat, Port, Fehlversuche, naechster Versuch",
       bi.get("status") == "fail" and bi.get("reason") == "nocert" and bi.get("port") == HORN_PORT and bi.get("fails", 0) >= 1
       and bi.get("next") and not bi.get("real"), bi)
    raw = json.dumps(st)
    ok("/api/calls/state ohne Pfade/Geheimnisse", not re.search(r"fullchain|/data/|/tmp/|privkey|" + re.escape(SECRET[:12]), raw), raw[:300])
    s, html, _ = adm("GET", "/calls")
    sec = section(html)
    ok("Seite: Zeile Eingebaut (oben) mit Startet nicht, Hinweis HORN_NAME/Durchreiche/UDP und Anleitung",
       s == 200 and 'id="row-builtin"' in sec and sec.find('id="row-builtin"') < sec.find("</table>") and "Startet nicht" in sec
       and "HORN_NAME" in sec and "auf 443 an Port" in sec and "HORN_UDP=0" in sec and "ANLEITUNG.md#eingebauter-knoten-hinter-einem-l4-proxy" in sec
       and 'value="builtin_retry"' in sec and "fullchain" not in sec, sec[:600])
    # Backoff waechst (2, 4, 8, 16 s ...); Log nur einmal
    t0 = time.time()
    while starts() < 4 and time.time() - t0 < 40:
        time.sleep(0.5)
    time.sleep(1)
    lg = log_text()
    n_media, n_knot = lg.count("startet nicht: kein Zertifikat"), lg.count("fehler: kein Zertifikat")
    ok(f"Log nach {starts()} Fehlversuchen: Grund-Zeile {n_media}x, Knoten-Zeile {n_knot}x, 'gestartet' 1x",
       starts() >= 4 and n_media == 1 and n_knot == 1 and lg.count("eingebauter Knoten gestartet") == 1)
    # Speichern: Warnung + sofortiger Neustart trotz Backoff (naechster Versuch erst in >= 16 s)
    st = state()
    wait_s = (st.get("bi") or {}).get("next", 0) - time.time()
    n0 = starts()
    s, _, hs = adm("POST", "/calls", {"action": "horn", "horn": "1", "horn_builtin": "1"})
    t0 = time.time()
    while starts() == n0 and time.time() - t0 < 12:
        time.sleep(0.2)
    dt = time.time() - t0
    ok(f"Speichern startet sofort neu ({dt:.1f} s, Backoff waere noch {wait_s:.0f} s)", starts() > n0 and dt < 7 and wait_s > dt + 3,
       (starts(), n0, wait_s))
    s, html, _ = adm("GET", "/calls")
    ok("Speichern ohne Name/Zertifikat: Warnung 'startet so nicht' (HORN_NAME)", s == 200 and "startet so nicht – es fehlt ein eigener Name (HORN_NAME)" in html,
       html[html.find("flash"):][:300])
    time.sleep(1.5)
    lg = log_text()
    ok("nach dem Speichern steht der Grund noch einmal im Log", lg.count("startet nicht: kein Zertifikat") == 2, lg[-800:])
    # Port belegt
    open(MODE, "w").write("port")
    adm("POST", "/calls", {"action": "builtin_retry"})
    st = wait_bi(lambda b, _: b.get("reason") == "port")
    s, html, _ = adm("GET", "/calls")
    ok("Port belegt: Grund port, Text mit Port", (st.get("bi") or {}).get("reason") == "port"
       and f"Port {HORN_PORT} ist belegt" in section(html), st.get("bi"))
    # sonstiger Abbruch: bereinigte Fehlerzeile
    open(MODE, "w").write("other")
    adm("POST", "/calls", {"action": "builtin_retry"})
    st = wait_bi(lambda b, _: b.get("reason") == "exit")
    d = (st.get("bi") or {}).get("detail", "")
    s, html, _ = adm("GET", "/calls")
    ok(f"sonstiger Abbruch: Fehlerzeile ohne Pfad/Kennung ({d})", "permission denied" in d and "/data" not in d
       and "0123456789abcdef" not in d and "Bricht beim Start ab" in section(html) and "/data/horn" not in html)
    # laeuft -> startet
    open(MODE, "w").write("run")
    adm("POST", "/calls", {"action": "builtin_retry"})
    st = wait_bi(lambda b, _: b.get("status") == "starting" and not b.get("reason"))
    ok("Knoten laeuft (meldet sich noch nicht an): Startet …", (st.get("bi") or {}).get("status") == "starting", st.get("bi"))
    s, html, _ = adm("GET", "/calls")
    ok("Seite: Startet … mit Auffrischen (data-wait)", "Startet …" in section(html) and "data-wait" in section(html))
    # Schalter aus
    adm("POST", "/calls", {"action": "horn", "horn": "1"})
    st = wait_bi(lambda b, _: b.get("status") == "off")
    s, html, _ = adm("GET", "/calls")
    t0 = time.time()
    while "beende den eingebauten Knoten" not in log_text() and time.time() - t0 < 10:   # saga-media fragt alle 5 s
        time.sleep(0.3)
    lg = log_text()
    ok("Schalter aus: Zeile Eingebaut 'Abgeschaltet', Knoten beendet, keine Warnung",
       (st.get("bi") or {}).get("status") == "off" and "Abgeschaltet" in section(html) and "beende den eingebauten Knoten" in lg
       and "startet so nicht" not in html, ("Abgeschaltet" in section(html), "beende den eingebauten Knoten" in lg,
                                           "startet so nicht" not in html))
    stop()


def teil2(media):
    settings(enabled=True, horn=True, horn_builtin=True)
    ok("Teil 2: Instanz mit SAGA_HORN=off startet", start(media, FAKE, {"SAGA_HORN": "off"}))
    st = wait_bi(lambda b, _: b.get("status") == "unavail")
    s, html, _ = adm("GET", "/calls")
    ok("SAGA_HORN=off: Nicht verfuegbar mit Grund", (st.get("bi") or {}).get("reason") == "disabled"
       and "SAGA_HORN=off" in section(html) and "Nicht verfügbar" in section(html), st.get("bi"))
    stop()


def teil3(media, node):
    import auth
    from saga import horn
    settings(enabled=True, horn=True, horn_builtin=True)
    auth.save_setting("horn_cf", {"token": "test-" + secrets.token_hex(8), "zone_id": "z" * 32, "zone": "example.org"})
    data = os.path.join(STATE, "horn3")
    shutil.rmtree(data, ignore_errors=True)
    ok("Teil 3: Instanz mit echtem gjallarhorn (HORN_NAME, ohne Zertifikat) startet",
       start(media, node, {"HORN_NAME": "horn-0.example.org", "HORN_PUBLIC_URL": f"https://horn-0.example.org:{HORN_PORT}",
                           "HORN_DATA": data, "HORN_ACME_URL": "http://127.0.0.1:9/dir", "HORN_UDP": "1", "HORN_ICE_IPS": "127.0.0.1"}))
    st = wait_bi(lambda b, s: b.get("status") == "live" and (builtin_node(s) or {}).get("cert", {}).get("mode") == "dns01"
                 and (builtin_node(s) or {}).get("cert", {}).get("err"), 40)
    n = builtin_node(st) or {}
    ok("A: eingebauter bekommt dns01 (Cloudflare-Token in Saga), meldet Zertifikat fehlt mit Grund",
       n.get("cert", {}).get("mode") == "dns01" and n.get("cert", {}).get("state") == "fehlt" and n.get("cert", {}).get("err", "").startswith("dns01"),
       n.get("cert"))
    ok("B: ohne Zertifikat nicht bereit (Status nocert)", n.get("status") == "nocert", n.get("status"))
    from db import connect
    with connect() as c:
        rn = horn.ready_nodes(c)
    ok("B: kein bereiter Knoten (ready_nodes leer)", not [x for x in rn if x.get("builtin")], [x.get("name") for x in rn])
    s, html, _ = adm("GET", "/calls")
    sec = section(html)
    ok("C: Zeile des eingebauten zeigt Zertifikat fehlt + DNS-01-Text, keine Zeile 'Eingebaut' doppelt",
       "Zertifikat fehlt" in sec and "DNS-01" in sec and 'id="row-builtin"' not in sec and "horn-0.example.org" in sec, sec[:800])
    t0 = time.time()
    while time.time() - t0 < 15 and (builtin_node(st) or {}).get("udp_check") in ("", "unbekannt", None):
        time.sleep(0.5)
        st = state()
    n = builtin_node(st) or {}
    ok(f"UDP-Selbsttest nach dem Handshake nachgereicht (udp_check {n.get('udp_check')!r}, udp {n.get('udp')})",
       n.get("udp_check") in ("ok", "fehlt"), n)
    # Token entfernen -> alpn an den eingebauten
    s, _, _ = adm("POST", "/calls", {"action": "cf_clear"})
    st = wait_bi(lambda b, s: (builtin_node(s) or {}).get("cert", {}).get("mode") == "alpn"
                 and (builtin_node(s) or {}).get("cert", {}).get("err", "").startswith("alpn"), 40)
    n = builtin_node(st) or {}
    s, html, _ = adm("GET", "/calls")
    sec = section(html)
    ok("A: nach 'Token entfernen' alpn, weiter nicht bereit", n.get("cert", {}).get("mode") == "alpn" and n.get("status") == "nocert", n.get("cert"))
    ok("C: Hinweis 'TLS-ALPN-01 klappt nur, wenn …:443' (Knoten auf anderem Port)", "TLS-ALPN-01 klappt nur" in sec
       and "/data" not in json.dumps(n.get("cert")), sec[:600])
    stop()
    auth.save_setting("horn_cf", {})


def main():
    import auth
    import db
    db.wait_ready()
    db.init_schema()
    os.makedirs(STATE, exist_ok=True)
    auth.get_user("ca-hb-adm") or auth.create_user("ca-hb-adm", secrets.token_urlsafe(12), "Admin Horn", is_admin=True)
    with db.connect() as c, c:
        c.execute("DELETE FROM horn_node WHERE builtin=1")
    media = build("saga-media", "media", "SAGA_MEDIA_BIN")
    node = build("gjallarhorn", "gjallarhorn", "GJALLARHORN_BIN")
    ok("saga-media gebaut", media)
    keep = (auth.settings(fresh=True).get("calls") or {}).copy()
    try:
        teile = os.environ.get("HB_TEILE", "123")          # nur einzelne Teile (Gegenproben): z. B. HB_TEILE=3
        if media:
            if "1" in teile:
                teil1(media)
            if "2" in teile:
                teil2(media)
            if "3" in teile:
                ok("gjallarhorn vorhanden (GJALLARHORN_BIN oder go build)", node)
                if node:
                    teil3(media, node)
    finally:
        stop()
        auth.save_setting("calls", keep)
        with db.connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE builtin=1")
    n = sum(res)
    print(f"\n{n}/{len(res)} OK" + ("  ALLES GRUEN" if n == len(res) else ""))
    sys.exit(0 if n == len(res) else 1)


if __name__ == "__main__":
    main()
