"""Foederation (2.0): zwei eigene Saga-Instanzen (A und B) mit eigenen Datenbanken im selben Container.

A = http://127.0.0.1:8095 (Datenbank saga_feda), B = http://localhost:8097 (saga_fedb) - verschiedene Hosts, beide intern
(also nur ueber "Freigegebene interne Hosts" erreichbar - prueft netguard mit). Geprueft:
- Aus = nichts erreichbar; Handshake ueber den Admin-Bereich (Code, Fingerprint, beidseitige Bestaetigung), falscher Code,
  geaenderter Fingerprint, Code nur einmal.
- Signaturen: gueltig, Wiederholung (Replay), gefaelscht, falscher Schluessel, falsche Uhrzeit, falscher Digest, falscher Host,
  unbekannte Instanz, fehlende Komponenten, Antwort-Signatur; unsignierter WebFinger; zu grosse Anfrage.
- Teilen an name@host (Person) und gruppe@host, Personensuche, Lesen (Bilder, Verweise, verdeckte Titel, kein
  Anmeldename), Kommentieren, Bearbeiten (rev/409), Benachrichtigungen in beide Richtungen, Rollenwechsel, Entzug,
  Namenswechsel (Freigaben enden, Name gesperrt), Kontoloeschung, Trennen (sofort), SSRF.
Raeumt auf (Datenbanken werden geloescht). Umgebung wie die anderen Tests (POSTGRES_*, Container)."""
import base64, http.client, json, os, re, secrets, subprocess, sys, time, urllib.parse
import psycopg
from itsdangerous import TimestampSigner

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from saga import fedsig  # noqa: E402  (nur Signatur-Hilfen, ohne Saga-Konfiguration)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
ADMIN_PW = "Fed-Admin-Pass-1234"
I = {
    "a": {"port": 8095, "admin": 8094, "db": "saga_feda", "url": "http://127.0.0.1:8095", "host": "127.0.0.1:8095", "secret": secrets.token_hex(32),
          "state": "/tmp/fedtest-a", "allow": "localhost:8097"},
    "b": {"port": 8097, "admin": 8096, "db": "saga_fedb", "url": "http://localhost:8097", "host": "localhost:8097", "secret": secrets.token_hex(32),
          "state": "/tmp/fedtest-b", "allow": "127.0.0.1:8095"},
}
res = []
procs = {}


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


def env_of(k):
    i = I[k]
    e = {x: v for x, v in os.environ.items() if x not in ("DATABASE_URL", "PYTHONPATH", "PORT", "ADMIN_PORT")}
    e.update(POSTGRES_DB=i["db"], BASE_URL=i["url"], SECRET_KEY=i["secret"], SESSION_SECRET=i["secret"], PORT=str(i["port"]),
             ADMIN_PORT=str(i["admin"]), HOST="127.0.0.1", ADMIN_HOST="127.0.0.1", ADMIN_URL=f"http://127.0.0.1:{i['admin']}",
             FED_ALLOW_HTTP="1", CANONICAL_REDIRECT="false", STATE_DIR=i["state"], ADMIN_USERNAME="fedadmin", ADMIN_PASSWORD=ADMIN_PW,
             SAGA_SKIP_BACKUP="1", PYTHONPATH=APP, SAGA_UPDATE_CHECK="0")
    return e


def py(k, code):
    """Python-Code im Kontext einer Instanz (eigene Datenbank) -> letzte Zeile der Ausgabe als JSON."""
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", "import json\n" + code], env=env_of(k), cwd=APP, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-3000:], flush=True)
        raise RuntimeError("py fehlgeschlagen")
    lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
    return json.loads(lines[-1]) if lines else None


def pg_admin(sql):
    e = os.environ
    dsn = e.get("DATABASE_URL") or "postgresql://{u}:{p}@{h}:{port}/{d}".format(
        u=e.get("POSTGRES_USER", "saga"), p=e.get("POSTGRES_PASSWORD", ""), h=e.get("POSTGRES_HOST", "db"), port=e.get("POSTGRES_PORT", "5432"),
        d=e.get("POSTGRES_DB", "saga"))
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql)


def sql(k, q, args=()):
    return py(k, f"""
from db import connect
with connect() as c, c:
    cur = c.execute({q!r}, {tuple(args)!r})
    try:
        rows = cur.fetchall()
    except Exception:
        rows = []
print(json.dumps([list(r) for r in rows], default=str))""")


# --- HTTP ----------------------------------------------------------------------------------------------------
def hreq(k, m, p, body=None, hdr=None, admin=False, raw=None, cookie=None, timeout=40):
    i = I[k]
    port = i["admin"] if admin else i["port"]
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    h = {"Host": f"127.0.0.1:{port}" if admin else i["host"], "Accept-Language": "de"}
    if cookie:
        h["Cookie"] = cookie
    if body is not None:
        raw = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    h.update(hdr or {})
    c.request(m, p, body=raw, headers=h)
    r = c.getresponse(); data = r.read()
    return r.status, {k2.lower(): v for k2, v in r.getheaders()}, data


def jd(data):
    try:
        return json.loads(data)
    except ValueError:
        return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


def ck(k, uid):
    s = TimestampSigner(I[k]["secret"])
    return "saga=" + s.sign(base64.b64encode(json.dumps({"uid": uid, "name": uid, "pwc": time.time() + 5}).encode())).decode()


def api(k, uid, m, p, body=None):
    s, _, d = hreq(k, m, p, body=body, hdr={"X-Saga": "1"}, cookie=ck(k, uid))
    return s, jd(d)


class Admin:
    """Admin-Bereich einer Instanz mit Anmeldung, Sitzungs-Cookie und CSRF."""

    def __init__(self, k):
        self.k, self.ck = k, {}

    def req(self, m, p, form=None):
        raw, hdr = None, {}
        if form is not None:
            raw = urllib.parse.urlencode(form).encode(); hdr["Content-Type"] = "application/x-www-form-urlencoded"
        if self.ck:
            hdr["Cookie"] = "; ".join(f"{a}={b}" for a, b in self.ck.items())
        s, h, d = hreq(self.k, m, p, admin=True, raw=raw, hdr=hdr)
        sc = h.get("set-cookie")
        if sc:
            a, b = sc.split(";")[0].split("=", 1)
            self.ck[a] = b
        return s, h.get("location", ""), d.decode("utf-8", "replace")

    def csrf(self, p="/federation"):
        m = re.search(r'name="csrf" value="([^"]+)"', self.req("GET", p)[2])
        return m.group(1) if m else ""

    def login(self):
        self.req("GET", "/login")
        s, loc, _ = self.req("POST", "/login", {"username": "fedadmin", "password": ADMIN_PW, "csrf": self.csrf("/login")})
        return s in (302, 303) and "/login" not in loc

    def post(self, form):
        f = dict(form); f["csrf"] = self.csrf()
        s, loc, html = self.req("POST", "/federation", f)
        if s == 303:
            html = self.req("GET", "/federation")[2]
        return s, html


def flash(html):
    m = re.search(r'class="adm-flash[^"]*"[^>]*>([^<]*)<', html)
    return m.group(1) if m else ""


def wait(fn, sec=25, step=0.5):
    end = time.time() + sec
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return fn()


def blocks(*bl):
    return json.dumps({"blocks": list(bl)})


def P(text, bid=None):
    b = {"type": "paragraph", "data": {"text": text}}
    if bid:
        b["id"] = bid
    return b


def upload(k, uid, note=None):
    bd = "fedtest"
    mp = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"t.png\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + PNG + f"\r\n--{bd}--\r\n".encode()
    s, _, d = hreq(k, "POST", "/api/files", raw=mp, hdr={"X-Saga": "1", "Content-Type": f"multipart/form-data; boundary={bd}"}, cookie=ck(k, uid))
    return (jd(d).get("file") or {}).get("url")


def ref(peer, rid):
    return -(rid * 1024 + peer)


# --- Instanzen ------------------------------------------------------------------------------------------------
SETUP = """
import auth, db
from saga import publish
db.wait_ready(); db.init_schema()
auth.save_setting("allowed_hosts", {allow!r})
for u, n, pub in {users!r}:
    auth.create_user(u, "Fed-User-Pass-123", n)
    if pub:
        print(publish.set_name(u, pub))
from db import connect
with connect() as c, c:
    for g, members in {groups!r}:
        gid = c.execute("INSERT INTO groups (name, description, created) VALUES (?, '', 0)", (g,)).lastrowid
        for m in members:
            c.execute("INSERT INTO user_group (user_id, group_id) VALUES (?, ?)", (auth.get_user(m)["id"], gid))
print(json.dumps(True))
"""


def start(k):
    i = I[k]
    os.makedirs(i["state"], exist_ok=True)
    log = open(os.path.join(i["state"], "server.log"), "w")
    procs[k] = subprocess.Popen([sys.executable, "run.py"], env=env_of(k), cwd=APP, stdout=log, stderr=subprocess.STDOUT)
    ok(f"{k.upper()}: gestartet", wait(lambda: _health(k), 40))


def _health(k):
    try:
        return hreq(k, "GET", "/healthz", timeout=3)[0] == 200
    except OSError:
        return False


def stop_all():
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def server_errors(k):
    try:
        txt = open(os.path.join(I[k]["state"], "server.log")).read()
    except OSError:
        return 0, ""
    n = txt.count("Traceback")
    return n, "\n".join(x for x in txt.splitlines() if "Traceback" in x or "Error" in x or "bus:" in x or "federation:" in x)[-3000:]


# --- Signaturen von Hand -------------------------------------------------------------------------------------
def signed(k_to, priv, keyid, m, path, query="", body=b"", actor=None, now=None, authority=None, tamper=None):
    hdr, nonce = fedsig.sign_request(priv, keyid, m, authority or I[k_to]["host"], path, query, body, actor, now=now)
    if tamper:
        tamper(hdr)
    s, h, d = hreq(k_to, m, path + ("?" + query if query else ""), raw=body or None, hdr=dict(hdr, **{"Content-Type": "application/json"}))
    return s, h, d, nonce, hdr


def main():
    for k in I:
        pg_admin(f"DROP DATABASE IF EXISTS {I[k]['db']} WITH (FORCE)")
        pg_admin(f"CREATE DATABASE {I[k]['db']}")
    py("a", SETUP.format(allow=I["a"]["allow"], users=[("fa-anna", "Anna Eigner", "anna"), ("fa-carl", "Carl Ohne", None)], groups=[]))
    py("b", SETUP.format(allow=I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert"), ("fb-cleo", "Cleo Team", "cleo"),
                                                        ("fb-dora", "Dora Ohne", None), ("fb-eve", "Eve Aussen", "eve")],
                         groups=[("fbteam", ["fb-bert", "fb-cleo"])]))
    start("a"); start("b")
    A, B = Admin("a"), Admin("b")
    ok("Admin A und B angemeldet", A.login() and B.login())

    # --- Aus: nichts erreichbar ---------------------------------------------------------------------------------
    ok("aus: /.well-known/saga 404", hreq("a", "GET", "/.well-known/saga")[0] == 404)
    ok("aus: /fed/v1/status 404", hreq("a", "GET", "/fed/v1/status")[0] == 404)
    s, d = api("b", "fb-bert", "GET", "/api/fed/shared")
    ok("aus: /api/fed/shared leer", s == 200 and d["items"] == [])
    for adm in (A, B):
        adm.post({"action": "enable", "on": "1"})
    s, _, d = hreq("a", "GET", "/.well-known/saga")
    wk = jd(d)
    ok("an: /.well-known/saga mit Schluessel und Fingerprint", s == 200 and wk.get("key", {}).get("alg") == "ed25519" and wk["base_url"] == I["a"]["url"])
    fp_a = wk["fingerprint"]
    ok("Admin A zeigt denselben Fingerprint", fp_a in A.req("GET", "/federation")[2])
    wk_b = jd(hreq("b", "GET", "/.well-known/saga")[2])
    ok("unsignierter WebFinger -> 404", hreq("b", "GET", "/.well-known/webfinger?resource=acct%3Abert%40localhost%3A8097")[0] == 404)

    # --- SSRF / Adressen ------------------------------------------------------------------------------------------
    _, html = B.post({"action": "preview", "url": "http://10.255.255.1:9", "code": "x"})
    ok(f"SSRF: interne Adresse ohne Freigabe abgewiesen ({flash(html)[:60]})", "nicht erlaubt" in flash(html))
    _, html = B.post({"action": "preview", "url": "http://127.0.0.1:8099", "code": "x"})
    ok("SSRF: 127.0.0.1 (anderer Port) ohne Freigabe abgewiesen", "nicht erlaubt" in flash(html))
    _, html = B.post({"action": "preview", "url": I["a"]["url"] + "/pfad", "code": "x"})
    ok("Adresse mit Pfad abgewiesen", "Fehler" in flash(html))

    # --- Handshake ------------------------------------------------------------------------------------------------
    _, html = A.post({"action": "invite"})
    m = re.search(r'Code: <code class="fed-fp">([A-Za-z0-9_-]{20,})</code>', html)
    ok("A: Einladungscode erzeugt (einmal angezeigt)", bool(m))
    code = m.group(1) if m else ""
    ok("Code nur einmal angezeigt", code not in A.req("GET", "/federation")[2])
    _, html = B.post({"action": "preview", "url": I["a"]["url"], "code": code})
    ok("B: Vorschau zeigt Fingerprint von A", fp_a in html and 'name="fingerprint"' in html)
    _, html = B.post({"action": "connect", "url": I["a"]["url"], "code": "falsch-" + code[:10], "fingerprint": fp_a})
    ok(f"falscher Code abgewiesen ({flash(html)[:50]})", "ungültig" in flash(html))
    _, html = B.post({"action": "connect", "url": I["a"]["url"], "code": code, "fingerprint": "SHA256:AAAA"})
    ok("geaenderter Fingerprint abgewiesen", "geändert" in flash(html))
    _, html = B.post({"action": "connect", "url": I["a"]["url"], "code": code, "fingerprint": fp_a})
    ok(f"B: Anfrage gesendet ({flash(html)[:40]})", "Anfrage gesendet" in flash(html) and "Wartet auf die Gegenseite" in html)
    ha = A.req("GET", "/federation")[2]
    ok("A: Anfrage wartet auf Bestaetigung, mit Fingerprint von B", "Wartet auf deine Bestätigung" in ha and wk_b["fingerprint"] in ha)
    _, html = B.post({"action": "connect", "url": I["a"]["url"], "code": code, "fingerprint": fp_a})
    ok("Code ein zweites Mal: abgewiesen", "ungültig" in flash(html) or "verbunden" in flash(html))
    pa = sql("a", "SELECT id, status FROM fed_peer")
    pb = sql("b", "SELECT id, status FROM fed_peer")
    ok("Status: A pending_in, B pending_out", pa and pa[0][1] == "pending_in" and pb and pb[0][1] == "pending_out")
    peer_b_at_a, peer_a_at_b = pa[0][0], pb[0][0]
    _, html = A.post({"action": "confirm", "id": str(peer_b_at_a)})
    ok(f"A bestaetigt ({flash(html)[:40]})", "bestätigt" in flash(html))
    ok("beide aktiv", sql("a", "SELECT status FROM fed_peer")[0][0] == "active" and sql("b", "SELECT status FROM fed_peer")[0][0] == "active")
    _, html = B.post({"action": "refresh", "id": str(peer_a_at_b)})
    ok("B: Status pruefen -> active", "active" in flash(html))

    # --- Signaturen -----------------------------------------------------------------------------------------------
    keys = py("b", "from saga import federation, fedsig\npriv, pub = federation.own_key()\nprint(json.dumps(fedsig.b64u(priv)))")
    privb = fedsig.unb64u(keys)
    puba = fedsig.unb64u(wk["key"]["public"])
    kid = I["b"]["url"]
    s, h, d, nonce, hdr = signed("a", privb, kid, "GET", "/fed/v1/status")
    ok("Signatur gueltig -> 200", s == 200 and jd(d).get("status") == "active")
    try:
        fedsig.verify_response(puba, I["a"]["url"], s, h, d, nonce); good = True
    except fedsig.SigError:
        good = False
    ok("Antwort korrekt signiert (gepinnter Schluessel A, unsere Nonce)", good)
    try:
        fedsig.verify_response(puba, I["a"]["url"], s, h, d, "andere-nonce-1234567890"); bad = True
    except fedsig.SigError:
        bad = False
    ok("Antwort mit fremder Nonce abgewiesen", not bad)
    s2 = hreq("a", "GET", "/fed/v1/status", hdr=dict(hdr))[0]
    ok(f"Replay (gleiche Kopfzeilen) -> 401 ({s2})", s2 == 401)

    def flip(hh):
        sig = hh["Signature"]; i = 10
        hh["Signature"] = sig[:i] + ("A" if sig[i] != "A" else "B") + sig[i + 1:]
    ok("gefaelschte Signatur -> 401", signed("a", privb, kid, "GET", "/fed/v1/status", tamper=flip)[0] == 401)
    other, _ = fedsig.new_key()
    ok("fremder Schluessel mit keyid von B -> 401", signed("a", other, kid, "GET", "/fed/v1/status")[0] == 401)
    ok("Uhrzeit -10 min -> 401", signed("a", privb, kid, "GET", "/fed/v1/status", now=time.time() - 600)[0] == 401)
    ok("Uhrzeit +10 min -> 401", signed("a", privb, kid, "GET", "/fed/v1/status", now=time.time() + 600)[0] == 401)
    ok("falscher Host in der Signatur -> 401", signed("a", privb, kid, "GET", "/fed/v1/status", authority="evil.example")[0] == 401)
    ok("unbekannte Instanz (keyid) -> 401", signed("a", privb, "http://unbekannt.example", "GET", "/fed/v1/status")[0] == 401)
    body = json.dumps({"events": []}).encode()

    def dig(hh):
        hh["Content-Digest"] = fedsig.digest(b'{"events":[{"type":"x"}]}')
    ok("Digest passt nicht zum Koerper -> 401", signed("a", privb, kid, "POST", "/fed/v1/events", body=body, tamper=dig)[0] == 401)

    def fewer(hh):
        hh["Signature-Input"] = hh["Signature-Input"].replace(' "saga-actor"', "")
    ok("fehlende Komponente -> 401", signed("a", privb, kid, "GET", "/fed/v1/status", tamper=fewer)[0] == 401)
    ok("Pfad veraendert (signiert /fed/v1/status, gesendet /fed/v1/shared) -> 401",
       hreq("a", "GET", "/fed/v1/shared", hdr=fedsig.sign_request(privb, kid, "GET", I["a"]["host"], "/fed/v1/status", "", b"")[0])[0] == 401)
    ok("Akteur mit ungueltigem Namen -> 400",
       signed("a", privb, kid, "GET", "/fed/v1/shared", actor={"name": "Fa Anna!", "display": "x", "groups": []})[0] == 400)
    ok("Inhalte ohne Akteur -> 400", signed("a", privb, kid, "GET", "/fed/v1/notes/1")[0] == 400)
    big = http.client.HTTPConnection("127.0.0.1", I["a"]["port"], timeout=20)
    big.putrequest("POST", "/fed/v1/events"); big.putheader("Host", I["a"]["host"]); big.putheader("Content-Length", str(3 * 1024 * 1024))
    big.putheader("Content-Type", "application/json"); big.endheaders()
    s = big.getresponse().status
    ok(f"zu grosse Anfrage -> 413 ({s})", s == 413)
    logs = sql("a", "SELECT event FROM fed_log")
    ok("Pruefprotokoll A: abgewiesene Signaturen/Replay vermerkt", any(x[0] == "sig.rejected" for x in logs) and any(x[0] == "sig.replay" for x in logs))
    return privb, kid


def notes_of(k, uid):
    s, d = api(k, uid, "GET", "/api/notifications")
    return d.get("items", []) if isinstance(d, dict) else []


def part2(privb, kid):
    pa = sql("b", "SELECT id FROM fed_peer")[0][0]           # Nummer von A bei B (steckt in den negativen IDs)
    HA, HB = I["a"]["host"], I["b"]["host"]
    # --- A legt Seiten an ------------------------------------------------------------------------------------------
    img = upload("a", "fa-anna")
    img2 = upload("a", "fa-anna")
    ok("A: Bilder hochgeladen", bool(img and img2))
    s, x = api("a", "fa-anna", "POST", "/api/notes", {"title": "Geheim-X"})
    X = x["id"]
    s, p = api("a", "fa-anna", "POST", "/api/notes", {"title": "Projekt Föderation"})
    Pid = p["id"]
    s, sub = api("a", "fa-anna", "POST", "/api/notes", {"title": "Unterseite S", "parent_id": Pid})
    S = sub["id"]
    body = blocks(P("Hallo <a href=\"/notes#%d\">Unterseite S</a> und <a href=\"/notes#%d\">Geheim-X</a>" % (S, X), "b1"),
                  {"id": "b2", "type": "image", "data": {"file": {"url": img}, "caption": ""}})
    s, _ = api("a", "fa-anna", "PATCH", f"/api/notes/{Pid}", {"body": body, "format": "blocks"})
    ok("A: Seite mit Bild und Verweisen gespeichert", s == 200)
    rP, rS = ref(pa, Pid), ref(pa, S)

    # --- Suche und Teilen ----------------------------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "GET", "/api/share/targets?q=" + urllib.parse.quote(f"@bert@{HB}"))
    hits = d.get("remote", [])
    ok(f"Personensuche @bert@{HB}: gefunden mit Herkunft", any(h["target"] == f"@bert@{HB}" and "Bert Gast" in h["name"] and HB in h["name"] for h in hits))
    s, d = api("a", "fa-anna", "GET", "/api/share/targets?q=" + urllib.parse.quote(f"fbteam@{HB}"))
    ok("Personensuche gruppe@host: Gruppe gefunden", any(h["type"] == "remote_group" and h["target"] == f"fbteam@{HB}" for h in d.get("remote", [])))
    s, d = api("a", "fa-anna", "GET", "/api/share/targets?q=" + urllib.parse.quote(f"@niemand@{HB}"))
    ok("Personensuche: unbekannte Person -> kein Treffer", not d.get("remote"))
    s, d = api("a", "fa-anna", "GET", "/api/share/targets?q=" + urllib.parse.quote("@bert@unverbunden.example"))
    ok("Personensuche: nicht verbundene Instanz -> kein Treffer, keine Anfrage", not d.get("remote"))
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@dora@{HB}", "role": "view"})
    ok(f"Teilen an Person ohne oeffentlichen Namen -> 400 ({s})", s == 400)
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": "@bert@unverbunden.example", "role": "view"})
    ok("Teilen an nicht verbundene Instanz -> 400", s == 400 and "nicht verbunden" in d.get("error", ""))
    s, cp = api("a", "fa-carl", "POST", "/api/notes", {"title": "Carls Seite"})
    s, d = api("a", "fa-carl", "PUT", f"/api/notes/{cp['id']}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok("Teilen ohne eigenen oeffentlichen Namen -> 400", s == 400 and "öffentlichen Namen" in d.get("error", ""))
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok(f"A teilt mit @bert@{HB} (Lesen)", s == 200 and any(x["type"] == "remote" and x["target"] == f"@bert@{HB}" for x in d.get("shares", [])))
    n = wait(lambda: [x for x in notes_of("b", "fb-bert") if x["kind"] == "share.added"])
    ok("B: Benachrichtigung „hat geteilt“ (signiertes Ereignis)", bool(n) and n[0].get("title") == "Projekt Föderation" and "anna@" + HA in (n[0].get("actor") or {}).get("name", ""))
    ok("B: Benachrichtigung verweist auf die negative ID", bool(n) and n[0].get("url") == f"/notes#{rP}")

    s, d = api("b", "fb-bert", "GET", "/api/fed/shared")
    ids = {x["id"]: x for x in d.get("items", [])}
    ok("B: Mit mir geteilt enthaelt Seite und Unterseite (negative IDs)", rP in ids and rS in ids and ids[rP]["root"] and ids[rS]["parent_id"] == rP)
    ok("B: Eigentuemer mit Herkunft (Name@host)", ids.get(rP, {}).get("owner", {}).get("handle") == f"anna@{HA}")
    ok("B: Geheim-X nicht in der Liste", ref(pa, X) not in ids)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}")
    raw = json.dumps(d)
    ok("B: Seite lesen ueber den Proxy", s == 200 and d.get("title") == "Projekt Föderation" and d.get("role") == "view" and d.get("id") == rP)
    ok("B: Verweis auf Unterseite umgeschrieben", f"/notes#{rS}" in d.get("body", ""))
    ok("B: Titel nicht freigegebener Seite verdeckt (in der Sprache der Person)", "Geheim-X" not in raw and "Seite ohne Zugriff" in d.get("body", ""))
    ok("B: kein Anmeldename des Eigentuemers in der Antwort", "fa-anna" not in raw and d["owner"]["username"] == f"@anna@{HA}")
    fid = img.rsplit("/", 1)[1]
    ok("B: Bild ueber den Proxy umgeschrieben", f"/api/fed/files/{rP}/{fid}" in d.get("body", ""))
    s, h, data = hreq("b", "GET", f"/api/fed/files/{rP}/{fid}", cookie=ck("b", "fb-bert"))
    ok("B: Bild laden (gleiche Bytes, nosniff, sandbox)", s == 200 and data == PNG and h.get("x-content-type-options") == "nosniff" and "sandbox" in h.get("content-security-policy", ""))
    s, *_ = hreq("b", "GET", f"/api/fed/files/{rP}/{img2.rsplit('/', 1)[1]}", cookie=ck("b", "fb-bert"))
    ok("B: Bild, das die Seite nicht benutzt -> 404", s == 404)
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"title": "Hack"})
    ok(f"B: Lesen -> Speichern verboten ({s})", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rP}/comments", {"block_id": "b1", "body": "Hallo"})
    ok("B: Lesen -> Kommentieren verboten", s == 403)
    s, d = api("b", "fb-cleo", "GET", f"/api/notes/{rP}")
    ok("B: Cleo (nicht geteilt) -> 404", s == 404)
    s, d = api("b", "fb-dora", "GET", f"/api/notes/{rP}")
    ok("B: Dora ohne oeffentlichen Namen -> 403 mit Hinweis", s == 403 and "öffentlichen Namen" in d.get("error", ""))
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{ref(pa, X)}")
    ok("B: nicht geteilte Seite -> 404", s == 404)
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}/versions")
    ok("B: andere Endpunkte fuer fremde Seiten -> 404", s == 404)
    ok("B: Signierter Akteur kann sich nicht als A-Person ausgeben (Prinzipal immer @name@B-host)",
       signed("a", privb, kid, "GET", f"/fed/v1/notes/{Pid}", actor={"name": "anna", "display": "Anna", "groups": []})[0] == 404)

    # --- Kommentieren -------------------------------------------------------------------------------------------------
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "comment"})
    ok("A: Rolle -> Kommentieren", s == 200)
    ok("B: Benachrichtigung Rollenwechsel", bool(wait(lambda: [x for x in notes_of("b", "fb-bert") if x["kind"] == "share.role"])))
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rP}/comments", {"block_id": "b1", "body": "Frage von Bert"})
    ok("B: Kommentar ueber den Proxy", s == 200 and d.get("comment", {}).get("id", 0) < 0 and d["comment"]["author"]["me"])
    thread = d.get("thread_id")
    s, c = api("a", "fa-anna", "GET", f"/api/notes/{Pid}/comments")
    th = (c.get("threads") or [{}])[0]
    ok("A: Kommentar von Bert mit Herkunft", th.get("comments", [{}])[0].get("author", {}).get("name") == f"Bert Gast (bert@{HB})")
    na = wait(lambda: [x for x in notes_of("a", "fa-anna") if x["kind"] == "comment.new"], 5)
    ok("A: Benachrichtigung „hat kommentiert“ von Bert", bool(na) and HB in (na[0].get("actor") or {}).get("name", ""))
    s, d = api("a", "fa-anna", "POST", f"/api/notes/{Pid}/comments", {"thread_id": th.get("id"), "body": "Antwort von Anna"})
    ok("A: Antwort", s == 200)
    nb = wait(lambda: [x for x in notes_of("b", "fb-bert") if x["kind"] == "comment.reply"])
    ok("B: Benachrichtigung „hat geantwortet“ (Ereignis), ohne Kommentartext", bool(nb) and "Antwort von Anna" not in json.dumps(nb) and nb[0].get("url", "").startswith(f"/notes#{rP}"))
    s, c = api("b", "fb-bert", "GET", f"/api/notes/{rP}/comments")
    t0 = (c.get("threads") or [{}])[0]
    ok("B: Unterhaltung ueber den Proxy (IDs negativ, Namen mit Herkunft)", s == 200 and t0.get("id") == thread and len(t0.get("comments", [])) == 2
       and t0["comments"][1]["author"]["name"] == f"Anna Eigner (anna@{HA})" and t0["comments"][0]["author"]["me"])
    s, d = api("b", "fb-bert", "PATCH", f"/api/comments/{t0['comments'][0]['id']}", {"body": "Frage von Bert (bearbeitet)"})
    ok("B: eigenen Kommentar bearbeiten", s == 200 and d.get("comment", {}).get("body") == "Frage von Bert (bearbeitet)")
    s, d = api("b", "fb-bert", "PATCH", f"/api/comments/{t0['comments'][1]['id']}", {"body": "fremd"})
    ok("B: fremden Kommentar bearbeiten -> 403", s == 403)
    s, d = api("b", "fb-bert", "POST", f"/api/comments/{thread}/resolve", {"resolved": True})
    ok("B: Unterhaltung erledigt", s == 200 and d.get("thread_id") == thread)
    s, d = api("b", "fb-bert", "POST", f"/api/comments/{thread}/seen")
    ok("B: gelesen", s == 200)
    s, d = api("b", "fb-bert", "POST", f"/api/notes/{rP}/comments", {"thread_id": ref(pa + 1, 5), "body": "x"})
    ok("B: Antwort mit Unterhaltung einer anderen Instanz -> 404", s == 404)

    # --- Bearbeiten -----------------------------------------------------------------------------------------------------
    api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "edit"})
    s, d = api("b", "fb-bert", "GET", f"/api/notes/{rP}")
    rev = d.get("rev")
    s, bp = api("b", "fb-bert", "POST", "/api/notes", {"title": "Berts lokale Seite"})
    doc = json.loads(d["body"])
    doc["blocks"][0]["data"]["text"] = doc["blocks"][0]["data"]["text"].replace("Hallo", "Hallo von Bert")
    doc["blocks"].insert(0, P(f'Lokal <a href="/notes#{bp["id"]}">B-Seite</a> und <a href="/notes#{rS}">S</a>', "b3"))
    newbody = json.dumps(doc)
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"body": newbody, "format": "blocks", "rev": rev, "title": "Projekt Föderation (B)"})
    ok(f"B: Bearbeiten mit rev ({s})", s == 200 and d.get("rev") == rev + 1)
    s, a = api("a", "fa-anna", "GET", f"/api/notes/{Pid}")
    ok("A: Inhalt geaendert, zuletzt von Bert", "Hallo von Bert" in a.get("body", "") and a.get("updated_by", {}).get("name") == f"Bert Gast (bert@{HB})")
    links = set(re.findall(r"/notes#(-?[0-9]+)", a["body"]))
    ok(f"A: Verweis auf Unterseite wieder in A-Form, lokale B-Seite entschaerft ({sorted(links)})",
       str(S) in links and str(X) in links and not any(x.startswith("-") for x in links) and len(links) == 2 and "B-Seite" in a["body"])
    ok("A: Titel der verdeckten Seite wiederhergestellt, Bild bleibt A-Datei", "Geheim-X" in a["body"] and img in a["body"])
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"body": newbody, "format": "blocks", "rev": rev})
    ok(f"B: veraltete Revision -> 409 Konflikt ({s})", s == 409 and d.get("error") == "conflict")
    s, d = api("b", "fb-bert", "PATCH", f"/api/notes/{rP}", {"pinned": True, "parent_id": None})
    ok("B: andere Felder als Titel/Inhalt -> 403", s == 403)
    ok("A-Proxy laesst nur Titel/Inhalt durch (direkt signiert: pinned -> 400)",
       signed("a", privb, kid, "PATCH", f"/fed/v1/notes/{Pid}", body=b'{"pinned": true}', actor={"name": "bert", "display": "Bert", "groups": []})[0] == 400)
    evil_instance(privb, kid, pa)
    return pa, Pid, S, X, rP


def evil_instance(privb, kid, pa):
    """Boesartige verbundene Instanz (B mit eigenem Schluessel): Inhalt, Ereignisse, Anzeigenamen, Verschachtelung."""
    HA = I["a"]["host"]
    fid = "a" * 24
    evil = {"id": 77, "title": "Böse", "icon": "/logout", "cover": "/logout", "format": "blocks", "role": "owner",
            "owner": {"name": "X", "username": f"@x@{HA}", "me": False},
            "body": json.dumps({"blocks": [P('<img src=x onerror=alert(1)><a href="javascript:alert(1)">j</a> <b>fett</b>', "e1"),
                                           {"id": "e2", "type": "image", "data": {"file": {"url": "/logout"}}},
                                           {"id": "e3", "type": "image", "data": {"file": {"url": f"/files/{fid}"}}}]})}
    d = py("b", f"""
from saga import federation
from db import connect
with connect() as c:
    p = federation.peer_by(c, "id", {pa})
    d = federation._note_in(c, p, json.loads({json.dumps(evil)!r}), "fb-bert")
    d["_emoji"] = federation._safe_pic("📄", True)
print(json.dumps(d))""")
    body = d.get("body", "")
    ok("Heimatserver bereinigt fremden Inhalt (kein onerror, kein javascript:, <b> bleibt)",
       "onerror" not in body and "javascript:" not in body and "<img" not in body and "<b>fett</b>" in body)
    ok("Heimatserver: Bild-Bloecke nur mit Dateien der Seite (über den Proxy), /logout faellt weg",
       "/logout" not in body and f"/api/fed/files/{ref(pa, 77)}/{fid}" in body)
    ok("Heimatserver: Symbol/Titelbild nur Emoji oder Bild der Seite, Rolle nie owner",
       d.get("icon") == "" and d.get("cover") == "" and d.get("_emoji") == "📄" and d.get("role") == "view")

    def ev(*evs):
        return signed("a", privb, kid, "POST", "/fed/v1/events", body=json.dumps({"events": list(evs)}).encode())[0]
    share = {"type": "share.added", "to": {"type": "user", "name": "anna"}, "owner": {"name": "bert", "display": "Bert"},
             "note": {"id": 4242, "title": "Flut"}, "role": "view"}
    ok("Ereignis share.added doppelt (eine Anfrage + Wiederholung) -> 200", ev(share, share) == 200 and ev(share) == 200)
    n = [x for x in notes_of("a", "fa-anna") if x["kind"] == "share.added"]
    ok(f"A: unveraenderte Freigabe benachrichtigt nur einmal ({len(n)})", len(n) == 1)
    cm = {"type": "comment.new", "to": {"type": "user", "name": "anna"}, "note": {"id": 4242, "title": "Flut"},
          "comment": {"id": 1, "thread": 1, "block": ""}}
    ev(dict(cm, actor={"handle": "carol@dritte.example", "display": "Carol Echt"}))
    ev(dict(cm, actor={"handle": "carol@dritte.example", "display": "Admin"}))
    r = sql("a", "SELECT display FROM fed_person WHERE principal='@carol@dritte.example'")
    ok(f"B kann Personen dritter Instanzen nicht umbenennen ({r})", r == [["Carol Echt"]])
    deep = b'{"events":' + b"[" * 50000 + b"]" * 50000 + b"}"
    s = signed("a", privb, kid, "POST", "/fed/v1/events", body=deep)[0]
    ok(f"tief verschachtelter Koerper -> 400, kein Serverfehler ({s})", s == 400)
    n = py("b", f"""
from saga import sharing
from db import connect
with connect() as c, c:
    c.execute("INSERT INTO groups (name, description, created) VALUES ('fbweg', '', 0)")
    c.execute("INSERT INTO fed_inbound (peer_id, note_id, owner, target_type, target, role, created) VALUES (?,?,?,?,?,?,?)",
              ({pa}, 99, '@anna@{HA}', 'group', 'fbweg', 'view', 0))
    sharing.drop_group(c, 'fbweg')
    n = c.execute("SELECT COUNT(*) FROM fed_inbound WHERE target='fbweg'").fetchone()[0]
print(json.dumps(n))""")
    ok("B: Gruppe geloescht -> Eingangs-Verweise der Gruppe weg (keine Bestaetigung fuer spaetere gleichnamige Gruppe)", n == 0)


def part3(privb, kid, pa, Pid, S, X, rP):
    HB = I["b"]["host"]
    # --- Gruppe ----------------------------------------------------------------------------------------------------
    s, p2 = api("a", "fa-anna", "POST", "/api/notes", {"title": "Team-Seite"})
    P2 = p2["id"]; r2 = ref(pa, P2)
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{P2}/shares", {"type": "remote_group", "target": f"fbteam@{HB}", "role": "view"})
    ok("A teilt mit Gruppe fbteam@B", s == 200 and any(x["type"] == "remote_group" for x in d.get("shares", [])))
    ok("B: Gruppenmitglied Cleo benachrichtigt", bool(wait(lambda: [x for x in notes_of("b", "fb-cleo") if x["kind"] == "share.added"])))
    s, d = api("b", "fb-cleo", "GET", "/api/fed/shared")
    ok("B: Cleo sieht die Team-Seite (Gruppe signiert bestaetigt)", any(x["id"] == r2 for x in d.get("items", [])))
    ok("B: Cleo liest die Team-Seite", api("b", "fb-cleo", "GET", f"/api/notes/{r2}")[0] == 200)
    ok("B: Eve (nicht in der Gruppe) -> 404", api("b", "fb-eve", "GET", f"/api/notes/{r2}")[0] == 404)
    ok("Gruppen, die A nicht als Ziel genannt hat, werden nicht mitgeschickt",
       sql("b", "SELECT COUNT(*) FROM fed_inbound WHERE target_type='group'")[0][0] == 1)
    ok("A: signierte Gruppe einer anderen Person zaehlt nur fuer diese (Eve mit fbteam vom echten B -> sieht)",
       signed("a", privb, kid, "GET", f"/fed/v1/notes/{P2}", actor={"name": "eve", "display": "Eve", "groups": ["fbteam"]})[0] == 200)

    # --- Entzug -------------------------------------------------------------------------------------------------------
    s, _, d = hreq("a", "DELETE", f"/api/notes/{Pid}/shares?type=remote&target=" + urllib.parse.quote(f"@bert@{HB}"),
                hdr={"X-Saga": "1"}, cookie=ck("a", "fa-anna"))
    ok("A: Freigabe an Bert entfernt", s == 200)
    ok("B: Bert -> sofort 404 (Rolle prueft der Eigentuemer)", api("b", "fb-bert", "GET", f"/api/notes/{rP}")[0] == 404)
    ok("B: Eingangs-Verweis per Ereignis entfernt", wait(lambda: not sql("b", "SELECT 1 FROM fed_inbound WHERE note_id=%d AND target='fb-bert'" % Pid)))
    s, d = api("b", "fb-bert", "GET", "/api/fed/shared")
    ok("B: Mit mir geteilt ohne die entzogene Seite", not any(x["id"] == rP for x in d.get("items", [])))

    # --- Namenswechsel / Kontoloeschung auf B ------------------------------------------------------------------------
    api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@eve@{HB}", "role": "view"})
    ok("A: an Eve geteilt", bool(sql("a", "SELECT 1 FROM note_share WHERE target=%r" % f"@eve@{HB}")))
    r = py("b", "from saga import publish\nprint(json.dumps(publish.set_name('fb-eve', 'eve-neu')))")
    ok("B: Eve aendert ihren oeffentlichen Namen", r[0] == "eve-neu")
    ok("A: Freigabe an den alten Namen endet (person.gone)", wait(lambda: not sql("a", "SELECT 1 FROM note_share WHERE target=%r" % f"@eve@{HB}")))
    r = py("b", "from saga import publish\nprint(json.dumps(publish.set_name('fb-dora', 'eve')))")
    ok("B: alter Name ist fuer andere Konten gesperrt", r[0] is None)
    s, d = api("b", "fb-dora", "PUT", "/api/account/public-name", {"name": "eve", "check": True})
    ok("B: Pruefung „frei?“ meldet den gesperrten Namen als vergeben", s == 200 and d.get("available") is False)
    r = py("b", "from saga import publish\nprint(json.dumps(publish.set_name('fb-eve', 'eve')))")
    ok("B: die eigene Person darf zurueck auf den alten Namen", r[0] == "eve")
    py("b", "from saga.userdata import delete_user_data\ndelete_user_data('fb-cleo')\nprint(json.dumps(True))")
    ok("A: Kontoloeschung auf B -> Gruppen-Freigabe bleibt, Person weg", wait(lambda: not sql("a", "SELECT 1 FROM fed_person WHERE principal=%r" % f"@cleo@{HB}")))
    ok("B: Name des geloeschten Kontos gesperrt (ohne Eigentuemer)", sql("b", "SELECT uid FROM fed_name_retired WHERE name='cleo'") == [[None]])

    # --- Trennen ------------------------------------------------------------------------------------------------------
    peer_b = sql("a", "SELECT id FROM fed_peer")[0][0]
    A = Admin("a"); A.login()
    _, html = A.post({"action": "disconnect", "id": str(peer_b)})
    ok("A: Verbindung getrennt", "getrennt" in flash(html))
    ok("A: Freigaben an B entfernt", not sql("a", "SELECT 1 FROM note_share WHERE target_type IN ('remote','remote_group')"))
    ok("B: Gegenseite hat getrennt (sofort)", sql("b", "SELECT status FROM fed_peer")[0][0] == "disconnected")
    ok("B: Eingangs-Verweise weg", not sql("b", "SELECT 1 FROM fed_inbound"))
    ok("B: Team-Seite -> 404", api("b", "fb-bert", "GET", f"/api/notes/{ref(pa, P2)}")[0] == 404)
    ok("Signierte Anfrage der getrennten Instanz -> 401", signed("a", privb, kid, "GET", "/fed/v1/status")[0] == 401)
    nb = [x for x in notes_of("b", "fb-bert") if x.get("remote")]
    ok("B: alte Benachrichtigungen ohne Titel (kein Zugriff mehr)", nb and all(not x["access"] and x["note"] is None for x in nb))
    s, d = api("a", "fa-anna", "PUT", f"/api/notes/{Pid}/shares", {"type": "remote", "target": f"@bert@{HB}", "role": "view"})
    ok("A: Teilen nach dem Trennen -> 400", s == 400)


def run():
    try:
        privb, kid = main()
        args = part2(privb, kid)
        part3(privb, kid, *args)
    except Exception:
        import traceback
        traceback.print_exc()
        res.append(False)
    finally:
        stop_all()
        for k in I:
            n, txt = server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in I:
            try:
                pg_admin(f"DROP DATABASE IF EXISTS {I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    print(f"\n{sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
