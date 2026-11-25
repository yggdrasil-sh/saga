"""Push-Relais (2.6, Bugs-DB 2262-2): der echte Dienst relay/sagarelay im Prozess, dazu ein Fake-APNs und ein Fake
fuer den Domain-Nachweis (/.well-known/saga-push-relay) - alles auf 127.0.0.1, ohne Saga-Server und ohne Datenbank.

Ports (eigene, aenderbar): RELAY_TEST_PORT (18474, Relais), +1 Fake-APNs (HTTP/1.1, prueft das ES256-Anbieter-Token je
Team), +2 Fake-Domain-Nachweis (Antwort je Host-Kopf). Geprueft (docs/PUSH-RELAY.md): /v1/info und Startseite;
Anmeldung mit/ohne Domain-Nachweis, idempotent, falscher Besitznachweis, Host-Regeln, SSRF-Schutz, Anmelde-Bremse;
Signatur: ohne, falsch, fremder Schluessel, Zeitfenster, Replay, Digest, Foederations-Tag, fremde @authority, unbekannte
keyid -> 401; pending/blocked -> 403 (relayctl approve/block/unblock), Host gesperrt; Rotation (Vektor + echt), Abmelden;
Erlaubnislisten (Freitext in aps, fremdes Topic, mdm/location/complication, Prioritaet, Rate, voip-aps, saga-Feld,
collapse_id, expiration, Groessen 8 KB/4096/5120); Koepfe und Koerper 1:1 fuer alle Vektor-Faelle, unbekannte Felder
nicht weitergereicht, Team je Topic; APNs-Fehler-Abbildung (410/BadDeviceToken/DeviceTokenNotForTopic -> 410,
ExpiredProviderToken erneuert, 403/5xx/Zeitueberschreitung -> 502, 429, 413, 400); Stufen neu/aktiv/eigen, Grenzen je
Server/Token/Tag fuer ring/alert/bg, "ring bleibt frei, wenn alert voll" (Topf und reservierte APNs-Plaetze);
Protokoll und SQLite ohne Token, Chiffrat, Kanarien."""
import base64, http.client, http.server, json, logging, os, secrets, subprocess, sys, tempfile, threading, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "relay"), ROOT]

from cryptography.hazmat.primitives import hashes, serialization                  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec                          # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.exceptions import InvalidSignature                              # noqa: E402

from saga import fedsig                                                           # noqa: E402

RP = int(os.environ.get("RELAY_TEST_PORT", "18474"))
AP, WP = RP + 1, RP + 2
NETLOC = f"127.0.0.1:{RP}"
B1, B2 = "org.example.saga", "org.example.saga2"
TEAM1, KEY1, TEAM2, KEY2 = "TEAMAAAA11", "KEYAAAAA11", "TEAMBBBB22", "KEYBBBBB22"
CANARY = ("Kanarie-Chattext-7Q", "Kanarie-Seitentitel-3Z", "Kanarie-Anzeigename-9X", "kanarie.anmeldename", "Kanarie-Auftrag-5W")
VEC = json.load(open(os.path.join(ROOT, "tests", "vectors", "push_e2e.json")))
res = []
TOKENS = set()           # alle benutzten Geraetetoken (duerfen nirgends stehen)
CIPHERS = set()          # Chiffrat-Ausschnitte


def ok(n, c):
    res.append(bool(c)); print(("OK  " if c else "FAIL"), n, flush=True)


# --- Schluessel und Konfiguration ------------------------------------------------------------------------------
TMP = tempfile.mkdtemp(prefix="relay-test-", dir=os.environ.get("STATE_DIR") or None)
APNS_KEYS = {}
for team, kid in ((TEAM1, KEY1), (TEAM2, KEY2)):
    k = ec.generate_private_key(ec.SECP256R1())
    APNS_KEYS[kid] = (team, k.public_key())
    with open(os.path.join(TMP, kid + ".p8"), "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
CFG = {"teams": [{"team_id": TEAM1, "key_id": KEY1, "p8_file": KEY1 + ".p8", "bundles": [B1], "widgets": True},
                 {"team_id": TEAM2, "key_id": KEY2, "p8_file": KEY2 + ".p8", "bundles": [B2]}],
       "registration": "verify", "sandbox": True, "apns_concurrency": 6, "ring_slots": 4, "slot_wait": 0.5,
       "apns_timeout": 2, "reg_per_ip_hour": 5, "operator": "Beispiel-Betreiber, relay.example.org"}
with open(os.path.join(TMP, "relay.json"), "w") as f:
    json.dump(CFG, f)
DB = os.path.join(TMP, "relay.db")
os.environ.update(RELAY_CONFIG=os.path.join(TMP, "relay.json"), RELAY_DB=DB, RELAY_APNS_URL=f"http://127.0.0.1:{AP}",
                  RELAY_VERIFY_TEST_BASE=f"http://127.0.0.1:{WP}")

from sagarelay import app as rapp, auth as rauth, limits as rlimits, log as rlog, verify as rverify  # noqa: E402


class Tee:
    def __init__(self):
        self.lines, self._lock = [], threading.Lock()

    def write(self, s):
        with self._lock:
            self.lines.extend(x for x in s.splitlines() if x.strip())

    def flush(self):
        pass


LOG = Tee()
rlog.OUT = LOG


class ErrCount(logging.Handler):
    n = 0

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            ErrCount.n += 1
            print("SERVERFEHLER:", record.getMessage()[:200], flush=True)


logging.getLogger("uvicorn.error").addHandler(ErrCount())


# --- Fake-APNs ---------------------------------------------------------------------------------------------------
SEEN = []                # {tok, headers, body, team}
_first_jwt = {}


def _b64u_dec(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _jwt_team(auth):
    try:
        tok = auth.split(" ", 1)[1]
        h, c, s = tok.split(".")
        hd, cl = json.loads(_b64u_dec(h)), json.loads(_b64u_dec(c))
        team, pub = APNS_KEYS[hd["kid"]]
        raw = _b64u_dec(s)
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   (h + "." + c).encode(), ec.ECDSA(hashes.SHA256()))
        if hd.get("alg") != "ES256" or cl.get("iss") != team or abs(cl.get("iat", 0) - time.time()) > 3700:
            return None, tok
        return team, tok
    except (KeyError, ValueError, IndexError, InvalidSignature, AttributeError):
        return None, None


class FakeAPNs(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(raw)))
        if code == 200:
            self.send_header("apns-id", "00000000-0000-0000-0000-000000000000")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):        # Relais hat nach apns_timeout aufgegeben (Fall bbbb)
            pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        tok = self.path.rsplit("/", 1)[-1]
        team, jwt = _jwt_team(self.headers.get("authorization") or "")
        SEEN.append({"tok": tok, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body, "team": team,
                     "path": self.path})
        if not self.path.startswith("/3/device/"):
            return self._send(404, {"reason": "BadPath"})
        if not team:
            return self._send(403, {"reason": "InvalidProviderToken"})
        p = tok[:4]
        if p == "e0e0":
            first = _first_jwt.setdefault(tok, jwt)
            return self._send(403, {"reason": "ExpiredProviderToken"}) if jwt == first else self._send(200)
        table = {"dead": (410, "Unregistered"), "bad0": (400, "BadDeviceToken"), "f0e1": (400, "DeviceTokenNotForTopic"),
                 "e1e1": (403, "ExpiredProviderToken"), "4030": (403, "InvalidProviderToken"), "5030": (503, "ServiceUnavailable"),
                 "5000": (500, "InternalServerError"), "4290": (429, "TooManyRequests"), "4130": (413, "PayloadTooLarge"),
                 "4000": (400, "BadExpirationDate")}
        if p in table:
            code, reason = table[p]
            return self._send(code, {"reason": reason})
        if p == "aaaa":
            time.sleep(1.5)
        if p == "bbbb":
            time.sleep(3.0)
        return self._send(200)


WELL = {}                # host -> (status, body-bytes)


class FakeWell(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        st, raw = WELL.get((self.headers.get("host") or "").lower(), (404, b"{}"))
        if self.path != "/.well-known/saga-push-relay":
            st, raw = 404, b"{}"
        self.send_response(st)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):   # Abbruch durch das Relais ist gewollt
            super().handle_error(request, client_address)


for port, h in ((AP, FakeAPNs), (WP, FakeWell)):
    srv = QuietServer(("127.0.0.1", port), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

# --- Relais im Prozess ----------------------------------------------------------------------------------------
import uvicorn  # noqa: E402

USRV = uvicorn.Server(uvicorn.Config(rapp.create_app(), host="127.0.0.1", port=RP, log_level="error", access_log=False,
                                     lifespan="on"))
threading.Thread(target=USRV.run, daemon=True).start()
for _ in range(100):
    try:
        c = http.client.HTTPConnection("127.0.0.1", RP, timeout=2); c.request("GET", "/v1/info"); c.getresponse().read(); c.close()
        break
    except OSError:
        time.sleep(0.1)
RL = rapp.R["relay"]


# --- Helfer ----------------------------------------------------------------------------------------------------
def http_req(method, path, raw=b"", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", RP, timeout=15)
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    c.request(method, path, body=raw if raw else None, headers=h)
    r = c.getresponse(); data = r.read()
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    try:
        data = json.loads(data)
    except ValueError:
        data = data.decode("utf-8", "replace")
    return r.status, data, hd


def signed(method, path, body=None, priv=None, keyid=None, tag=fedsig.RELAY_TAG, now=None, netloc=NETLOC, raw=None, send_raw=None):
    raw = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    h, _ = fedsig.sign_request(priv, keyid, method, netloc, path, "", raw, now=now, tag=tag)
    return http_req(method, path, raw if send_raw is None else send_raw, h)


def key():
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return priv, fedsig.b64u(fedsig.public_of(priv))


class Srv:
    def __init__(self, host=None, good=True, reset=True):
        if reset:
            RL.reg.reset()
        self.priv, self.pub = key()
        self.host = host or f"s{secrets.token_hex(4)}.example.org"
        if good:
            WELL[self.host] = (200, json.dumps({"v": 1, "key": self.pub}).encode())
        self.st, self.d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": self.pub, "host": self.host, "saga_version": "2.6.0"},
                                    self.priv, "reg")
        self.id = self.d.get("server_id") if isinstance(self.d, dict) else None

    def call(self, method, path, body=None, **kw):
        return signed(method, path, body, self.priv, self.id, **kw)

    def push(self, **f):
        return self.call("POST", "/v1/push", msg(**f))


def tok(prefix=""):
    t = (prefix + secrets.token_hex(32))[:64]
    TOKENS.add(t)
    return t


def cipher(n=600):
    c = base64.urlsafe_b64encode(secrets.token_bytes(n)).decode().rstrip("=")[:4000]
    CIPHERS.add(c[:40])
    return c


def msg(**f):
    d = {"token": f.pop("token", None) or tok(), "env": "production", "topic": B1, "push_type": "alert", "priority": 10,
         "expiration": int(time.time()) + 3600, "collapse_id": "c" + secrets.token_urlsafe(16)[:22], "rate": "alert",
         "aps": {"alert": {"title-loc-key": "SAGA_PUSH_TITLE", "loc-key": "SAGA_PUSH_BODY"}, "mutable-content": 1, "sound": "default"},
         "saga": {"e2e": 1, "k": "rJ1YACNZ", "c": cipher()}}
    d.update(f)
    return {k: v for k, v in d.items() if v is not None or k not in ("collapse_id",)}


def voip(**f):
    return dict(dict(topic=B1 + ".voip", push_type="voip", rate="ring", aps={}, collapse_id=None), **f)


def ctl(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "relay", "relayctl.py"), "--db", DB] + list(args),
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr


def last_seen(tok_):
    return next((s for s in reversed(SEEN) if s["tok"] == tok_), None)


rc = 1
try:
    # --- Info und Startseite ----------------------------------------------------------------------------------------
    st, d, h = http_req("GET", "/v1/info")
    ok("info: 200, v [1]", st == 200 and d.get("v") == [1])
    ok("info: push_types", d.get("push_types") == ["alert", "background", "voip", "liveactivity", "widgets"])
    ok("info: topics = Bundles", d.get("topics") == [B1, B2])
    ok("info: tiers nach Vertrag", d.get("tiers", {}).get("neu", {}).get("alert") == {"per_min": 120, "per_token_min": 30, "per_day": 5000}
       and d["tiers"]["aktiv"]["ring"] == {"per_min": 120, "per_token_min": 10} and d["tiers"]["neu"]["bg"] == {"per_min": 60, "per_token_min": 6})
    ok("info: registration verify, max_payload 4096/5120", d.get("registration") == "verify" and d.get("max_payload") == 4096
       and d.get("max_payload_voip") == 5120)
    st, page, h = http_req("GET", "/")
    ok("Startseite: 200 mit Datenschutztext, Frist aus log_days (14) und CSP", st == 200 and "Datenschutz" in page
       and "Ger&auml;tetoken" in page and "nach 14 Tagen gel&ouml;scht" in page and "after 14 days" in page
       and "default-src 'none'" in h.get("content-security-policy", ""))
    st, d, _ = http_req("GET", "/gibtsnicht")
    ok("unbekannter Pfad -> 404 JSON", st == 404 and d == {"error": "not found"})

    # --- Anmeldung ----------------------------------------------------------------------------------------------------
    a = Srv()
    ok("Anmeldung mit Domain-Nachweis: 201 active, Stufe neu", a.st == 201 and a.d.get("state") == "active"
       and a.d.get("stage") == "neu" and a.d.get("verified") is True)
    ok("server_id: s_ + 16 Zeichen a-z2-7", isinstance(a.id, str) and len(a.id) == 18 and a.id.startswith("s_")
       and all(ch in "abcdefghijklmnopqrstuvwxyz234567" for ch in a.id[2:]))
    ok("Anmeldung: limits = tiers.neu, topics", a.d.get("limits") == RL.cfg.tiers["neu"] and a.d.get("topics") == [B1, B2])
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": a.pub, "host": a.host, "saga_version": "2.6.1"}, a.priv, "reg")
    ok("Anmeldung erneut: 200, dieselbe server_id", st == 200 and d.get("server_id") == a.id and d.get("state") == "active")
    p = Srv(good=False)
    WELL[p.host] = (200, json.dumps({"v": 1, "key": key()[1]}).encode())
    p2 = Srv(good=False)
    ok("ohne Nachweis (404): 201 pending", p.st == 201 and p.d.get("state") == "pending" and p2.d.get("state") == "pending"
       and p2.d.get("verified") is False and p2.d.get("verify_error") == "http 404")
    WELL[p.host] = (200, json.dumps({"v": 1, "key": key()[1]}).encode())
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": p.pub, "host": p.host, "saga_version": "2.6.0"}, p.priv, "reg")
    ok("falscher Schluessel im Nachweis: pending, verify_error key", st == 200 and d.get("state") == "pending" and d.get("verify_error") == "key")
    st, d, _ = p.push()
    ok("pending: push -> 403 pending", st == 403 and d == {"error": "pending"})
    st, d, _ = p.call("GET", "/v1/servers/me")
    ok("pending: /me zeigt Zustand", st == 200 and d.get("state") == "pending" and d.get("server_id") == p.id and isinstance(d.get("created"), int))
    rc_, out = ctl("approve", p.id)
    st, d, _ = p.push()
    ok("relayctl approve -> push 200", rc_ == 0 and st == 200 and d == {"ok": True})
    other_priv, other_pub = key()
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": other_pub, "host": "x1.example.org"}, a.priv, "reg")
    ok("Anmeldung mit fremdem Schluessel signiert -> 401", st == 401 and d.get("error") == "auth")
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": other_pub, "host": "x1.example.org"}, other_priv, "s_aaaaaaaaaaaaaaaa")
    ok("Anmeldung mit keyid != reg -> 401", st == 401)
    bad_hosts = ("127.0.0.1", "localhost", "http://saga.example.org", "saga.example.org/pfad", "[::1]", "a" * 260 + ".org", "")
    ok("Anmeldung: ungueltige Hosts -> 400 host", all(
        signed("POST", "/v1/servers", {"v": 1, "pub": other_pub, "host": hh}, other_priv, "reg")[:2] == (400, {"error": "bad", "reason": "host"})
        for hh in bad_hosts))
    st, d, _ = signed("POST", "/v1/servers", {"v": 2, "pub": other_pub, "host": "x1.example.org"}, other_priv, "reg")
    ok("Anmeldung v:2 -> 400", st == 400)
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": "kaputt", "host": "x1.example.org"}, other_priv, "reg")
    ok("Anmeldung pub kaputt -> 400 pub", st == 400 and d.get("reason") == "pub")
    # SSRF-Schutz des Nachweises (ohne Test-Basis)
    def verr(fn):
        try:
            fn(); return None
        except rverify.VerifyError as ex:
            return str(ex)
    ok("Nachweis: localhost loest auf Loopback auf -> abgelehnt", verr(lambda: rverify.public_addrs("localhost", 443)) == "address")
    ok("Nachweis: IP-Literal/ohne Punkt -> host", verr(lambda: rverify.norm_host("10.0.0.1")) == "host"
       and verr(lambda: rverify.norm_host("intern")) == "host" and rverify.norm_host("Saga.Example.org:8443") == ("saga.example.org", 8443))
    ok("Nachweis: Test-Basis nur Loopback/http", verr(lambda: rverify.fetch("saga.example.org", "http://10.1.2.3:80")) == "test base"
       and verr(lambda: rverify.fetch("saga.example.org", "https://127.0.0.1:1")) == "test base")
    ok("Nachweis: unbekannter Name -> dns", rverify.check("gibtsnicht.invalid", "x")[1] in ("dns", "address"))
    WELL["gross.example.org"] = (200, b"{" + b" " * 5000 + b"}")
    ok("Nachweis: Antwort > 4 KB -> size", rverify.check("gross.example.org", "x", f"http://127.0.0.1:{WP}") == (False, "size"))
    WELL["umleitung.example.org"] = (302, b"{}")
    ok("Nachweis: Weiterleitung nicht gefolgt", rverify.check("umleitung.example.org", "x", f"http://127.0.0.1:{WP}") == (False, "http 302"))
    # Anmelde-Bremse
    RL.reg.reset()
    runs = [Srv(reset=False) for _ in range(5)]
    six = Srv(reset=False)
    ok("Anmelde-Bremse: 5 je IP/Stunde, 6. -> 429 register", all(r.st == 201 for r in runs) and six.st == 429
       and six.d.get("scope") == "register" and 0 < six.d.get("retry_after", 0) <= 3600)
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": a.pub, "host": a.host, "saga_version": "2.6.1"}, a.priv, "reg")
    ok("Anmelde-Bremse: bekannter Server meldet sich trotzdem an", st == 200 and d.get("server_id") == a.id)
    RL.reg.reset()

    # --- Signatur ------------------------------------------------------------------------------------------------------
    body = msg()
    st, d, _ = http_req("POST", "/v1/push", json.dumps(body).encode())
    ok("ohne Signatur -> 401 auth", st == 401 and d.get("error") == "auth")
    st, d, _ = signed("POST", "/v1/push", msg(), key()[0], a.id)
    ok("fremder Schluessel -> 401 signature", st == 401 and d == {"error": "auth", "reason": "signature"})
    st, d, _ = signed("POST", "/v1/push", msg(), a.priv, a.id, now=int(time.time()) - 400)
    ok("Zeitfenster -400 s -> 401 time", st == 401 and d.get("reason") == "time")
    st, d, _ = signed("POST", "/v1/push", msg(), a.priv, a.id, now=int(time.time()) + 400)
    ok("Zeitfenster +400 s -> 401 time", st == 401 and d.get("reason") == "time")
    st, d, _ = signed("POST", "/v1/push", msg(), a.priv, a.id, tag=fedsig.REQ_TAG)
    ok("Foederations-Tag saga-fed/1 -> 401", st == 401)
    st, d, _ = signed("POST", "/v1/push", msg(), a.priv, a.id, netloc="relay.example.org")
    ok("fremde @authority -> 401", st == 401)
    st, d, _ = signed("POST", "/v1/push", msg(), a.priv, "s_aaaaaaaaaaaaaaaa")
    ok("unbekannte keyid -> 401 unknown", st == 401 and d.get("reason") == "unknown")
    raw = json.dumps(msg()).encode()
    st, d, _ = signed("POST", "/v1/push", None, a.priv, a.id, raw=raw, send_raw=raw.replace(b'"production"', b'"sandbox"   '))
    ok("Koerper veraendert (Digest) -> 401", st == 401)
    raw = json.dumps(msg()).encode()
    hh, _ = fedsig.sign_request(a.priv, a.id, "POST", NETLOC, "/v1/push", "", raw, tag=fedsig.RELAY_TAG)
    st1, _, _ = http_req("POST", "/v1/push", raw, hh)
    st2, d2, _ = http_req("POST", "/v1/push", raw, hh)
    ok("Replay: erste 200, gleiche Nonce -> 401 replay", st1 == 200 and st2 == 401 and d2 == {"error": "auth", "reason": "replay"})
    st, d, _ = a.call("POST", "/v1/push", raw=b"{kaputt")
    ok("kein JSON -> 400 json", st == 400 and d.get("reason") == "json")
    big = json.dumps(dict(msg(), pad="x" * 9000)).encode()
    st, d, _ = a.call("POST", "/v1/push", raw=big)
    ok("Koerper > 8 KB -> 413", st == 413 and d.get("error") == "bad")

    # --- Sperren, Host gesperrt -------------------------------------------------------------------------------------------
    bl = Srv()
    rc_, _ = ctl("block", bl.id, "--note", "Test")
    st, d, _ = bl.push()
    ok("relayctl block -> push 403 blocked", rc_ == 0 and st == 403 and d == {"error": "blocked"})
    st, d, _ = bl.call("GET", "/v1/servers/me")
    ok("blocked: /me state blocked, stage gesperrt", st == 200 and d.get("state") == "blocked" and d.get("stage") == "gesperrt")
    st, d, _ = signed("POST", "/v1/servers", {"v": 1, "pub": bl.pub, "host": bl.host}, bl.priv, "reg")
    ok("blocked: Neuanmeldung -> 403", st == 403 and d == {"error": "blocked"})
    st, d, _ = bl.call("DELETE", "/v1/servers/me")
    ok("blocked: Abmelden -> 403 (Sperre bleibt)", st == 403)
    same = Srv(host=bl.host)
    ok("gesperrter Host: neue Anmeldung pending", same.st == 201 and same.d.get("state") == "pending" and same.d.get("verify_error") == "host blocked")
    rc_, _ = ctl("unblock", bl.id)
    st, d, _ = bl.push()
    ok("relayctl unblock -> push 200", rc_ == 0 and st == 200)
    rc_, out = ctl("list")
    ok("relayctl list zeigt Server", rc_ == 0 and a.id in out and bl.id in out)
    rc_, out = ctl("show", a.id)
    ok("relayctl show mit Fingerabdruck", rc_ == 0 and '"fingerprint": "SHA256:' in out)
    rc_, out = ctl("limits", a.id, '{"alert": {"gibts": 1}}')
    ok("relayctl limits: Unsinn abgelehnt", rc_ == 1)

    # --- Rotation und Abmelden ------------------------------------------------------------------------------------------
    rv = VEC["rotate"]
    ok("Rotation: Vertrags-Vektor gueltig", rauth.rotate_proof_ok(rv["server_id"], rv["new_pub"], rv["proof"])
       and not rauth.rotate_proof_ok("s_bbbbbbbbbbbbbbbb", rv["new_pub"], rv["proof"]))
    r = Srv()
    npriv, npub = key()
    proof = fedsig.b64u(Ed25519PrivateKey.from_private_bytes(npriv).sign(f"saga-relay-rotate/1|{r.id}|{npub}".encode()))
    st, d, _ = r.call("POST", "/v1/servers/me/rotate", {"new_pub": npub, "proof": fedsig.b64u(b"\0" * 64)})
    ok("Rotation: falscher Nachweis -> 400 proof", st == 400 and d.get("reason") == "proof")
    st, d, _ = r.call("POST", "/v1/servers/me/rotate", {"new_pub": a.pub, "proof": proof})
    ok("Rotation: Schluessel eines anderen Servers -> 400", st == 400)
    st, d, _ = r.call("POST", "/v1/servers/me/rotate", {"new_pub": npub, "proof": proof})
    ok("Rotation: 200 ok", st == 200 and d.get("ok") is True)
    st, d, _ = r.push()
    ok("Rotation: alter Schluessel -> 401", st == 401)
    r.priv = npriv
    st, d, _ = r.push()
    ok("Rotation: neuer Schluessel -> 200, server_id gleich", st == 200)
    st, d, _ = r.call("DELETE", "/v1/servers/me")
    ok("Abmelden -> 200 ok", st == 200 and d == {"ok": True})
    st, d, _ = r.call("GET", "/v1/servers/me")
    ok("nach Abmelden: 401 unknown", st == 401 and d.get("reason") == "unknown")

    # --- Erlaubnislisten ------------------------------------------------------------------------------------------------
    e = Srv()
    BAD = [
        ("Freitext title in alert", dict(aps={"alert": {"title": CANARY[1], "body": CANARY[0]}}), "freitext"),
        ("alert als Text", dict(aps={"alert": CANARY[0]}), "freitext"),
        ("fremder Loc-Key", dict(aps={"alert": {"loc-key": "SAGA_PUSH_ANDERS"}}), "freitext"),
        ("loc-args", dict(aps={"alert": {"loc-key": "SAGA_PUSH_BODY", "loc-args": [CANARY[2]]}}), "freitext"),
        ("thread-id", dict(aps={"alert": {"loc-key": "SAGA_PUSH_BODY"}, "thread-id": "chat-12"}), "freitext"),
        ("badge", dict(aps={"badge": 3}), "freitext"),
        ("category", dict(aps={"category": "x"}), "freitext"),
        ("sound kritisch", dict(aps={"sound": {"critical": 1, "name": "x"}}), "aps"),
        ("mutable-content 2", dict(aps={"mutable-content": 2}), "aps"),
        ("interruption-level critical", dict(aps={"interruption-level": "critical"}), "aps"),
        ("fremdes Topic", dict(topic="org.example.other"), "topic"),
        ("alert mit .voip-Topic", dict(topic=B1 + ".voip"), "topic"),
        ("voip mit B-Topic", voip(topic=B1), "topic"),
        ("widgets ohne Freigabe (B2)", dict(push_type="widgets", topic=B2 + ".push-type.widgets", rate="bg", priority=5,
                                            aps={"content-available": 1}), "topic"),
        ("push_type mdm", dict(push_type="mdm"), "push_type"),
        ("push_type location", dict(push_type="location"), "push_type"),
        ("push_type complication", dict(push_type="complication"), "push_type"),
        ("background prio 10", dict(push_type="background", priority=10, rate="bg", aps={"content-available": 1}), "priority"),
        ("prio 7", dict(priority=7), "priority"),
        ("voip mit rate alert", voip(rate="alert"), "rate"),
        ("background mit rate ring", dict(push_type="background", priority=5, rate="ring", aps={"content-available": 1}), "rate"),
        ("alert mit rate bg", dict(rate="bg"), "rate"),
        ("voip mit aps", voip(aps={"alert": {"loc-key": "SAGA_PUSH_CALL"}}), "aps"),
        ("background mit alert", dict(push_type="background", priority=5, rate="bg", aps={"alert": {"loc-key": "SAGA_PUSH_BODY"}}), "aps"),
        ("saga mit Zusatzfeld", dict(saga={"e2e": 1, "k": "rJ1YACNZ", "c": "abc", "title": CANARY[0]}), "saga"),
        ("saga ohne", dict(saga=None), "saga"),
        ("saga k 7 Zeichen", dict(saga={"e2e": 1, "k": "rJ1YACN", "c": "abc"}), "saga"),
        ("saga e2e 2", dict(saga={"e2e": 2, "k": "rJ1YACNZ", "c": "abc"}), "saga"),
        ("saga c > 4000", dict(saga={"e2e": 1, "k": "rJ1YACNZ", "c": "A" * 4001}), "saga"),
        ("saga c kein Base64url", dict(saga={"e2e": 1, "k": "rJ1YACNZ", "c": "a+b/c="}), "saga"),
        ("collapse_id 65", dict(collapse_id="c" * 65), "collapse_id"),
        ("collapse_id mit Leerzeichen", dict(collapse_id="chat 12"), "collapse_id"),
        ("expiration > 30 Tage", dict(expiration=int(time.time()) + 31 * 86400), "expiration"),
        ("expiration Text", dict(expiration="morgen"), "expiration"),
        ("token kein Hex", dict(token="z" * 64), "token"),
        ("token zu kurz", dict(token="ab" * 10), "token"),
        ("env falsch", dict(env="dev"), "env"),
    ]
    for name, f, why in BAD:
        st, d, _ = e.push(**f)
        ok(f"400 {why}: {name}", st == 400 and d == {"error": "bad", "reason": why})
    st, d, _ = e.push(saga={"e2e": 1, "k": "rJ1YACNZ", "c": "A" * 4000})
    ok("APNs-Koerper > 4096 (alert) -> 413 size", st == 413 and d == {"error": "bad", "reason": "size"})
    t_v = tok()
    st, d, _ = e.push(**voip(token=t_v, saga={"e2e": 1, "k": "rJ1YACNZ", "c": "A" * 4000}))
    ok("voip mit groesstem Chiffrat (4000 Zeichen) -> 200", st == 200 and len(last_seen(t_v)["body"]) > 4000)
    st, d, _ = e.push(env="sandbox")
    ok("env sandbox erlaubt", st == 200)
    t_w = tok()
    st, d, _ = e.push(token=t_w, push_type="widgets", topic=B1 + ".push-type.widgets", rate="bg", priority=5, aps={"content-available": 1})
    ok("widgets mit Freigabe (B1) -> 200, Topic durchgereicht", st == 200 and last_seen(t_w)["headers"].get("apns-topic") == B1 + ".push-type.widgets")
    t_l = tok()
    st, d, _ = e.push(token=t_l, push_type="liveactivity", topic=B1 + ".push-type.liveactivity", rate="bg", priority=10, aps={})
    ok("liveactivity -> 200, Typ/Topic durchgereicht", st == 200 and last_seen(t_l)["headers"].get("apns-push-type") == "liveactivity")
    t_x = tok()
    st, d, _ = e.push(token=t_x, title=CANARY[1], body=CANARY[0], user=CANARY[3], thread="chat-12", extra={"a": CANARY[4]})
    s_x = last_seen(t_x)
    ok("unbekannte Anfragefelder ignoriert und nicht weitergereicht", st == 200 and set(json.loads(s_x["body"])) == {"aps", "saga"}
       and not any(c_.encode() in s_x["body"] for c_ in CANARY))

    # --- Koepfe und Koerper 1:1 (Vektor-Faelle) --------------------------------------------------------------------------
    now = int(time.time())
    for case in VEC["cases"]:
        t = tok()
        exp = now + case["ttl"]
        topic = B1 + ".voip" if case["push_type"] == "voip" else B1
        body = {"token": t, "env": "production", "topic": topic, "push_type": case["push_type"], "priority": case["priority"],
                "expiration": exp, "collapse_id": case["collapse_id"], "rate": case["rate"], "aps": case["apns_body"]["aps"],
                "saga": case["apns_body"]["saga"]}
        CIPHERS.add(case["apns_body"]["saga"]["c"][:40])
        st, d, _ = e.call("POST", "/v1/push", body)
        s_ = last_seen(t)
        want_h = {"apns-push-type": case["push_type"], "apns-topic": topic, "apns-priority": str(case["priority"]),
                  "apns-expiration": str(exp), "apns-collapse-id": case["collapse_id"]}
        got_h = {k: v for k, v in (s_ or {}).get("headers", {}).items() if k.startswith("apns-")}
        want_b = json.dumps(case["apns_body"], separators=(",", ":")).encode()
        if st != 200 or not s_ or got_h != want_h or s_["body"] != want_b:
            print("  diag", st, d if st != 200 else "", got_h if got_h != want_h else "", (s_ or {}).get("body", b"")[:80])
        ok(f"1:1 {case['name']}: 200, apns-* Koepfe genau, Koerper = apns_body ({len(want_b)} Byte), Team 1",
           st == 200 and s_ and got_h == want_h and s_["body"] == want_b
           and s_["team"] == TEAM1 and s_["path"] == "/3/device/" + t and s_["headers"].get("content-type") == "application/json")
    t = tok()
    st, d, _ = e.push(token=t, topic=B2, collapse_id=None)
    s_ = last_seen(t)
    ok("Topic B2 -> Anbieter-Token von Team 2, ohne collapse_id kein Kopf", st == 200 and s_["team"] == TEAM2
       and "apns-collapse-id" not in s_["headers"])

    # --- APNs-Fehler-Abbildung ----------------------------------------------------------------------------------------------
    MAP = [("dead", 410, {"error": "gone", "reason": "Unregistered"}), ("bad0", 410, {"error": "gone", "reason": "BadDeviceToken"}),
           ("f0e1", 410, {"error": "gone", "reason": "DeviceTokenNotForTopic"}),
           ("4030", 502, {"error": "upstream", "reason": "InvalidProviderToken"}),
           ("5030", 502, {"error": "upstream", "reason": "ServiceUnavailable"}),
           ("5000", 502, {"error": "upstream", "reason": "InternalServerError"}),
           ("e1e1", 502, {"error": "upstream", "reason": "ExpiredProviderToken"}),
           ("4130", 413, {"error": "bad", "reason": "PayloadTooLarge"}), ("4000", 400, {"error": "bad", "reason": "BadExpirationDate"}),
           ("bbbb", 502, {"error": "upstream", "reason": "timeout"})]
    for pfx, want_st, want_d in MAP:
        st, d, _ = e.push(token=tok(pfx))
        ok(f"APNs {pfx} -> {want_st} {want_d.get('reason')}", st == want_st and d == want_d)
    st, d, h = e.push(token=tok("4290"))
    ok("APNs 429 (TooManyRequests, je Token) -> 429 scope token mit Retry-After", st == 429 and d.get("scope") == "token"
       and d.get("retry_after") == 60
       and h.get("retry-after") == "60")
    t = tok("e0e0")
    st, d, _ = e.push(token=t)
    tries = [s_ for s_ in SEEN if s_["tok"] == t]
    ok("ExpiredProviderToken: neues Anbieter-Token, zweiter Versuch 200", st == 200 and len(tries) == 2
       and tries[0]["headers"]["authorization"] != tries[1]["headers"]["authorization"])
    old_url = RL.cfg.apns_url
    RL.cfg.apns_url = "http://127.0.0.1:9"          # nichts lauscht
    st, d, _ = e.push()
    RL.cfg.apns_url = old_url
    ok("APNs nicht erreichbar -> 502 connect", st == 502 and d == {"error": "upstream", "reason": "connect"})

    # --- Stufen und Grenzen (Uhr der Toepfe eingefroren: kein Nachfuellen waehrend des Zaehlens) ------------------------------
    frozen = time.monotonic()
    rlimits.CLOCK = lambda: frozen
    g = Srv()
    t = tok()
    sts = [g.push(token=t)[0] for _ in range(30)]
    st, d, h = g.push(token=t)
    ok("alert: 30 je Token/min, 31. -> 429 token", sts == [200] * 30 and st == 429 and d.get("scope") == "token"
       and d.get("tier") == "neu" and int(h.get("retry-after", 0)) >= 1)
    sts = [g.push(token=tok())[0] for _ in range(90)]
    st, d, _ = g.push()
    ok("alert: 120 je Server/min (neu), 121. -> 429 server", sts == [200] * 90 and st == 429 and d.get("scope") == "server")
    st_r, d_r, _ = g.push(**voip())
    st_b, d_b, _ = g.push(push_type="background", priority=5, rate="bg", aps={"content-available": 1})
    st_ra, _, _ = g.push(rate="ring", aps={"alert": {"title-loc-key": "SAGA_PUSH_TITLE", "loc-key": "SAGA_PUSH_CALL"},
                                           "mutable-content": 1, "sound": "default", "interruption-level": "time-sensitive"})
    ok("ring bleibt frei, wenn alert voll (voip und alert/ring), bg eigener Topf", st_r == 200 and st_ra == 200 and st_b == 200)
    t = tok()
    sts = [g.push(**voip(token=t))[0] for _ in range(10)]
    st, d, _ = g.push(**voip(token=t))
    ok("ring: 10 je Token/min, 11. -> 429 token", sts == [200] * 10 and st == 429 and d.get("scope") == "token")
    sts = [g.push(**voip())[0] for _ in range(18)]
    st, d, _ = g.push(**voip())
    ok("ring: 30 je Server/min (neu), 31. -> 429 server", sts == [200] * 18 and st == 429 and d.get("scope") == "server")
    t = tok()
    bgf = dict(push_type="background", priority=5, rate="bg", aps={"content-available": 1})
    sts = [g.push(token=t, **bgf)[0] for _ in range(6)]
    st, d, _ = g.push(token=t, **bgf)
    ok("bg: 6 je Token/min, 7. -> 429 token", sts == [200] * 6 and st == 429 and d.get("scope") == "token")
    # Tagesdeckel und Stufe eigen
    dd = Srv()
    rc_, _ = ctl("limits", dd.id, '{"alert": {"per_day": 3}}')
    st, d, _ = dd.call("GET", "/v1/servers/me")
    ok("relayctl limits -> Stufe eigen, Werte in /me", rc_ == 0 and d.get("stage") == "eigen" and d["limits"]["alert"]["per_day"] == 3
       and d["limits"]["alert"]["per_min"] == 1200)
    sts = [dd.push()[0] for _ in range(3)]
    st, d, _ = dd.push()
    ok("Tagesdeckel alert: 4. -> 429 server, retry_after <= 1 h", sts == [200] * 3 and st == 429 and d.get("scope") == "server"
       and 0 < d.get("retry_after", 0) <= 3600)
    st, d, _ = dd.push(**voip())
    ok("Tagesdeckel gilt nicht fuer ring", st == 200)
    RL.flush()
    RL.limiter.reset()
    sts = [dd.push()[0] for _ in range(1)]
    ok("Tagesdeckel ueberlebt Neustart der Zaehler (aus daily_stats)", sts == [429])
    # neu -> aktiv nach 7 Tagen
    pr = Srv()
    RL.store.update(pr.id, clean_since=int(time.time()) - 8 * 86400)
    st, d, _ = pr.call("GET", "/v1/servers/me")
    ok("Stufe neu -> aktiv nach 7 Tagen ohne Sperre", st == 200 and d.get("stage") == "aktiv" and d["limits"] == RL.cfg.tiers["aktiv"])
    rc_, _ = ctl("stage", pr.id, "neu")
    st, d, _ = pr.call("GET", "/v1/servers/me")
    ok("relayctl stage neu", rc_ == 0 and d.get("stage") == "neu")
    rlimits.CLOCK = time.monotonic

    # --- reservierte APNs-Plaetze fuer ring ----------------------------------------------------------------------------------
    c_ = Srv()
    out = {}

    def slow(i):
        out[i] = c_.push(token=tok("aaaa"))[0]
    th = [threading.Thread(target=slow, args=(i,)) for i in range(2)]
    for x in th:
        x.start()
    time.sleep(0.4)
    t0 = time.monotonic()
    st_a, d_a, _ = c_.push()
    t_a = time.monotonic() - t0
    t0 = time.monotonic()
    st_r, _, _ = c_.push(**voip())
    t_r = time.monotonic() - t0
    for x in th:
        x.join()
    ok("allgemeine APNs-Plaetze belegt: alert -> 429 apns nach slot_wait", st_a == 429 and d_a.get("scope") == "apns" and t_a < 1.4)
    ok(f"ring nimmt reservierten Platz sofort ({t_r:.2f} s)", st_r == 200 and t_r < 0.5 and out == {0: 200, 1: 200})

    # --- Protokoll und Speicher ohne Geheimes ----------------------------------------------------------------------------------
    RL.flush()
    lines = list(LOG.lines)
    recs = []
    try:
        recs = [json.loads(x) for x in lines]
        parsed = True
    except ValueError:
        parsed = False
    ok(f"Protokoll: {len(recs)} JSON-Zeilen, nur erlaubte Felder", parsed and len(recs) > 300
       and all(set(r_) <= set(rlog.ALLOWED) for r_ in recs))
    pushes = [r_ for r_ in recs if r_.get("ev") == "push" and "push_type" in r_]
    ok("Protokoll je Push: ts, server_id, push_type, rate, prio, status, ms", pushes and all(
        all(k in r_ for k in ("ts", "server_id", "push_type", "rate", "prio", "status", "ms")) for r_ in pushes))
    ok("Protokoll: apns_reason bei APNs-Fehlern", any(r_.get("apns_reason") == "Unregistered" for r_ in pushes))
    ok("Protokoll: tok_h nur bei Fehlern, 8 Hex", all(("tok_h" in r_) == (r_["status"] != 200) for r_ in pushes)
       and all(len(r_["tok_h"]) == 8 and int(r_["tok_h"], 16) >= 0 for r_ in pushes if "tok_h" in r_))
    blob = "\n".join(lines)
    low = blob.lower()
    ok(f"Protokoll: kein Token ({len(TOKENS)}), kein Chiffrat, keine Kanarien, keine Loc-Keys/aps",
       not any(t_.lower() in low for t_ in TOKENS) and not any(c_ in blob for c_ in CIPHERS)
       and not any(k_.lower() in low for k_ in CANARY) and "SAGA_PUSH" not in blob and "127.0.0.1" not in blob)
    RL.store.db.execute("PRAGMA wal_checkpoint(FULL)")
    dbraw = b""
    for sfx in ("", "-wal", "-shm"):
        if os.path.exists(DB + sfx):
            dbraw += open(DB + sfx, "rb").read()
    tabs = {r_[0] for r_ in RL.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ok("SQLite: nur servers, nonces, daily_stats", tabs == {"servers", "nonces", "daily_stats"})
    b64s = [base64.b64encode(t_.encode()) for t_ in list(TOKENS)[:50]]
    ok("SQLite: kein Token (auch nicht Base64), kein Chiffrat, keine Kanarien", not any(t_.encode() in dbraw.lower() for t_ in TOKENS)
       and not any(b_ in dbraw for b_ in b64s) and not any(c_.encode() in dbraw for c_ in CIPHERS)
       and not any(k_.encode() in dbraw for k_ in CANARY))
    n200 = RL.store.db.execute("SELECT COALESCE(SUM(n),0) FROM daily_stats WHERE status=200").fetchone()[0]
    ok(f"daily_stats zaehlt Zustellungen ({n200})", n200 > 150)
    rc_, out = ctl("stats", g.id)
    ok("relayctl stats je Server", rc_ == 0 and g.id in out and " 429 " in out)
    ok(f"Serverfehler: {ErrCount.n}", ErrCount.n == 0)
    rc = 0 if all(res) else 1
finally:
    USRV.should_exit = True
    time.sleep(0.5)
    print(f"\nRELAY-DIENST {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER", flush=True)
    import shutil
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(rc)
