"""Admin -> Netz (2.5, Bug-DB 1641, saga/netmap.py): Netz-Karte mit zwei eigenen, verbundenen Instanzen A und B (Testhilfen
tests/foederation.py + foederation2.py, Ports 8394-8397, eigene Datenbanken). Auf A zusaetzlich: ein gekoppelter
Gjallarhorn-Knoten als Attrappe (Steuerkanal ueber /api/horn/ctl + kleiner TLS-Echo-Dienst mit Knotenschluessel - so misst
die Karte echte Latenz), ein von B freigegebener Knoten (Einstellung horn_remote), Live-Verbindungen mit App-/Browser-
User-Agents und ein laufender Gruppenanruf ueber den Knoten mit einem Teilnehmer von B.

Geprueft: nur Admins (ohne Anmeldung 401, Admin-Cookie eines Nicht-Admins 401, /net leitet zur Anmeldung, Hauptport kennt
/api/net nicht); Struktur (self, peers, horns, clients, calls, edges, act); Partner B mit Zustand ok, Version und Latenz aus
der Hintergrund-Pruefung; Knoten mit Latenz, TCP, IPv4, Teilnehmerzahl, geteilt; Partner-Knoten; Clients je Grobklasse
(Android-App, iOS-App, Browser · Windows, Browser · macOS) nur mit Anzahlen; Anruf mit Knoten, Teilnehmern hier/ueber B,
Grobklasse der Anruf-Geraete; keine Namen, Konten, IP-Adressen, Schluessel, Fingerprints, Geraete- oder Anruf-Kennungen in
der Antwort; Zwischenspeicher (Stand 2 s, Messungen hoechstens alle SAGA_NET_PROBE s - gezaehlt an B: Abrufe von
/.well-known/saga); Nachrichtenzaehler der Foederation steigt bei einer Freigabe; Live-Kanal /api/net/events (erstes
Ereignis "net", ohne Anmeldung 401); Seite /net mit Tabellen (Server, Partner, Knoten, Clients, Anruf) und alle 5 Sprachen;
anschliessend die Browser-Reihe tests/browser_admin_netz.js (falls Playwright da). Keine Serverfehler.
Aufruf: POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_USER=saga PYTHONPATH=tests/pgshim:. python tests/admin_netz.py
Bildschirmfotos (Browser-Reihe): SHOTS=<ordner> (Standard /tmp/admin-netz-shots). Raeumt auf (Datenbanken geloescht)."""
import base64
import datetime
import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

os.environ["SAGA_NET_PROBE"] = "5"          # beide Instanzen: Messungen alle 5 s (Standard 60)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402

T.I["a"].update(port=8395, admin=8394, db="saga_netza", url="http://127.0.0.1:8395", host="127.0.0.1:8395", state="/tmp/netz-a", allow="localhost:8397")
T.I["b"].update(port=8397, admin=8396, db="saga_netzb", url="http://localhost:8397", host="localhost:8397", state="/tmp/netz-b", allow="127.0.0.1:8395")
ok, wait, api, sql, hreq, jd = T.ok, T.wait, T.api, T.sql, T.hreq, T.jd
APP = T.APP
sys.path.insert(0, APP)
from version import VERSION  # noqa: E402

NODE = "horn-netz.example.org"
UA = {"android": "Saga-Android/2.5.0", "ios": "Saga/250 CFNetwork/1568.100.1 Darwin/24.0.0",
      "win": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
      "mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"}
STOP = threading.Event()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- Attrappe eines Gjallarhorn-Knotens: TLS-Echo mit Knotenschluessel + Steuerkanal ------------------------------------
def echo_server(sk, port, hits):
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NODE)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2)).sign(k, hashes.SHA256()))
    d = tempfile.mkdtemp(prefix="netz-echo-")
    cp, kp = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    open(cp, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(kp, "wb").write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cp, kp)
    ls = socket.socket()
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", port))
    ls.listen(8)
    ls.settimeout(0.5)

    def one(cs):
        try:
            with ctx.wrap_socket(cs, server_side=True) as s:
                s.settimeout(3)
                buf = b""
                while b"\r\n\r\n" not in buf and len(buf) < 8192:
                    x = s.recv(4096)
                    if not x:
                        break
                    buf += x
                m = re.search(rb"GET /api/horn/echo\?n=([A-Za-z0-9_-]+)", buf)
                hits.append(time.time())
                if not m:
                    s.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
                    return
                body = json.dumps({"sig": base64.b64encode(sk.sign(f"horn-echo/1\n{m.group(1).decode()}\n{NODE}".encode())).decode()}).encode()
                s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                          + str(len(body)).encode() + b"\r\n\r\n" + body)
        except (OSError, ssl.SSLError):
            pass

    def loop():
        while not STOP.is_set():
            try:
                cs, _ = ls.accept()
            except OSError:
                continue
            threading.Thread(target=one, args=(cs,), daemon=True).start()
        ls.close()
        shutil.rmtree(d, ignore_errors=True)
    threading.Thread(target=loop, daemon=True).start()


def ctl_node(sk, peers_n):
    """Steuerkanal zu A wie ein gekoppelter Knoten: hello, dann alle 3 s eine Lastmeldung (haelt ihn "bereit")."""
    from websockets.sync.client import connect as wsc
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    ws = wsc(f"ws://127.0.0.1:{T.I['a']['port']}/api/horn/ctl", additional_headers={"X-Saga": "1"},
             open_timeout=5, proxy=None)
    ch = json.loads(ws.recv(5))
    ws.send(json.dumps({"t": "hello", "proto": "horn-ctl/1", "name": NODE, "url": f"https://{NODE}", "pub": pub,
                        "sig": base64.b64encode(sk.sign(f"horn-ctl/1 hello\n{ch['nonce']}".encode())).decode(), "version": VERSION,
                        "udp": False, "max_legs": 48, "v4": "127.0.0.1", "cert": {"mode": "dns01", "state": "self"}}))
    welcome = json.loads(ws.recv(5))

    def loop():
        n = 0
        while not STOP.is_set():
            n += 1
            try:
                ws.send(json.dumps({"t": "load", "n": n, "load": {"cpu": 0.18, "up": 12.0, "down": 6.0, "legs": peers_n, "calls": 1,
                                                                 "peers": peers_n, "max_legs": 48, "state": "ok"}}))
                end = time.time() + 3
                while time.time() < end and not STOP.is_set():
                    try:
                        ws.recv(0.5)
                    except TimeoutError:
                        pass
            except Exception:  # noqa: BLE001
                return
        ws.close()
    threading.Thread(target=loop, daemon=True).start()
    return welcome


# --- Live-Verbindungen mit verschiedenen User-Agents (bleiben offen) -------------------------------------------------
def sse(uid, ua):
    def run():
        try:
            c = http.client.HTTPConnection("127.0.0.1", T.I["a"]["port"], timeout=60)   # Heartbeat alle 25 s
            c.request("GET", "/api/events", headers={"Host": T.I["a"]["host"], "Cookie": T.ck("a", uid), "User-Agent": ua,
                                                     "Accept": "text/event-stream"})
            r = c.getresponse()
            while not STOP.is_set():
                try:
                    if not r.read1(4096):
                        return
                except (TimeoutError, socket.timeout):
                    continue
            c.close()
        except OSError:
            pass
    threading.Thread(target=run, daemon=True).start()


def adm_cookie(k, uid):
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + T.I[k]["secret"]).encode()).hexdigest())
    return "hk_admin=" + sa.sign(base64.b64encode(json.dumps({"aid": uid, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def net(adm):
    s, d = F2.admin_json(adm, "GET", "/api/net")
    return d if s == 200 else {}


def ips_in(obj, skip=()):
    """Alle IP-Adressen (v4/v6) in Texten der Antwort - ausser den Feldern skip (Host der Instanzen)."""
    found = []

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if k not in skip:
                    walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str):
            for m in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b|[0-9a-fA-F:]{2,}:[0-9a-fA-F:]+", o):
                try:
                    ipaddress.ip_address(m)
                    found.append((key, m))
                except ValueError:
                    pass
    walk(obj)
    return found


def wk_hits():
    try:
        return open(os.path.join(T.I["b"]["state"], "server.log")).read().count('"GET /.well-known/saga ')
    except OSError:
        return 0


def run_test():
    busy = [k for k in ("port", "admin") for i in T.I.values() if socket.socket().connect_ex(("127.0.0.1", i[k])) == 0]
    if busy:                                           # Ports 8394-8397 gehoeren gerade einem anderen Foederationstest
        ok("Ports 8394-8397 frei", False)
        return
    A, B, pa, pb = F2.main()
    # --- Zugriff -----------------------------------------------------------------------------------------------------
    s, _, _ = hreq("a", "GET", "/api/net", admin=True)
    ok(f"ohne Anmeldung: /api/net 401 ({s})", s == 401)
    s, _, _ = hreq("a", "GET", "/api/net/events", admin=True)
    ok(f"ohne Anmeldung: /api/net/events 401 ({s})", s == 401)
    s, h, _ = hreq("a", "GET", "/net", admin=True)
    ok(f"ohne Anmeldung: /net leitet zur Anmeldung ({s})", s in (302, 303, 307) and "/login" in h.get("location", ""))
    s, _, _ = hreq("a", "GET", "/api/net", admin=True, cookie=adm_cookie("a", "fa-anna"))
    ok(f"Admin-Cookie eines Nicht-Admins: 401 ({s})", s == 401)
    s, _, _ = hreq("a", "GET", "/api/net", cookie=T.ck("a", "fa-anna"), hdr={"X-Saga": "1"})
    ok(f"Hauptport kennt /api/net nicht ({s})", s == 404)

    # --- Testdaten auf A ---------------------------------------------------------------------------------------------
    F2.setting("a", "calls", {"enabled": True, "horn": True, "horn_builtin": False})
    F2.setting("a", "horn_remote", {str(pa): ["https://horn.partner.example"]})
    sk = Ed25519PrivateKey.generate()
    eport = free_port()
    hits = []
    echo_server(sk, eport, hits)
    nid = secrets.token_hex(8)
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    sql("a", "INSERT INTO horn_node (id, name, pub, builtin, url, v4, state, share, created, port) VALUES (?,?,?,0,?,?,'neu',1,?,?)",
        (nid, NODE, pub, f"https://{NODE}", "127.0.0.1", time.time(), eport))
    w = ctl_node(sk, 3)
    ok(f"Knoten-Attrappe verbunden ({w.get('t')})", w.get("t") == "welcome" and w.get("node_id") == nid)
    for uid, ua in (("fa-anna", UA["android"]), ("fa-anna", UA["win"]), ("fa-carl", UA["win"]), ("fa-carl", UA["mac"])):
        sse(uid, ua)
    # Gruppenanruf ueber den Knoten: zwei Geraete hier (iOS-App, Browser macOS), eines ueber B
    cid = secrets.token_hex(16)
    d1, d2, d3 = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16)
    now = time.time()
    chat = sql("a", "INSERT INTO chat (kind, created) VALUES ('free', ?) RETURNING id", (now,))[0][0]
    sql("a", "INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created, answered, kind, topo, v, peer_id) "
             "VALUES (?,?,?,?,?,'video','active',?,?,'group','horn',2,?)", (cid, chat, "fa-anna", "", d1, now, now, pa))
    for dev, who, peer in ((d1, "fa-anna", None), (d2, "fa-carl", None), (d3, "@bert@localhost:8397", pa)):
        sql("a", "INSERT INTO chat_member (chat_id, uid, joined) VALUES (?,?,?)", (chat, who, now))
        sql("a", "INSERT INTO chat_call_leg (call_id, device, who, peer_id, state, joined, seen, node) VALUES (?,?,?,?,'active',?,?,?)",
            (cid, dev, who, peer, now, now, nid))

    def keep_call():                                   # Lebenszeichen wie die Geraete (sonst beendet calls.tick den Anruf)
        while not STOP.is_set():
            for dev, uid, ua in ((d1, "fa-anna", UA["ios"]), (d2, "fa-carl", UA["mac"])):
                try:
                    hreq("a", "POST", f"/api/calls/{cid}/alive", body={"device": dev}, hdr={"X-Saga": "1", "User-Agent": ua}, cookie=T.ck("a", uid))
                except OSError:
                    pass
            try:
                sql("a", "UPDATE chat_call_leg SET seen=? WHERE call_id=? AND device=?", (time.time(), cid, d3))
            except Exception:  # noqa: BLE001
                pass
            STOP.wait(10)
    threading.Thread(target=keep_call, daemon=True).start()

    # --- Stand -------------------------------------------------------------------------------------------------------
    d = wait(lambda: (lambda x: x if x and any(p.get("rtt") is not None and p.get("version") for p in x.get("peers", []))
                      and any(h.get("rtt") is not None for h in x.get("horns", [])) and len(x.get("clients", [])) >= 4 else None)(net(A)), 30)
    d = d or net(A)
    ok("Struktur: self, peers, horns, clients, calls, edges, act", all(k in d for k in ("self", "peers", "horns", "clients", "calls", "edges", "act")))
    ok(f"dieser Server in der Mitte (Name, Host, Version {d.get('self', {}).get('version')})",
       d.get("self", {}).get("host") == T.I["a"]["host"] and d["self"].get("version") and d["self"].get("fed") is True and d["self"].get("horn") is True)
    p = next((x for x in d.get("peers", []) if x["host"] == T.I["b"]["host"]), {})
    ok(f"Partner B: Zustand ok, Version, Latenz aus der Hintergrund-Pruefung ({p.get('state')}, {p.get('version')}, {p.get('rtt')} ms)",
       p.get("state") == "ok" and p.get("version") == VERSION and isinstance(p.get("rtt"), int) and p.get("last_seen"))
    ok(f"Partner B: gemeinsame Faehigkeiten ({len(p.get('caps') or [])})", "v2" in (p.get("caps") or []))
    hn = next((x for x in d.get("horns", []) if x["name"] == NODE), {})
    ok(f"Knoten: bereit, geteilt, Latenz, TCP, IPv4, Teilnehmende ({hn.get('status')}, {hn.get('rtt')} ms, {hn.get('peers')})",
       hn.get("status") == "ready" and hn.get("state") == "ok" and hn.get("kind") == "shared" and isinstance(hn.get("rtt"), int)
       and hn.get("tcp") and hn.get("v4") is True and hn.get("v6") is False and hn.get("peers") == 3 and hn.get("version") == VERSION)
    rn = next((x for x in d.get("horns", []) if x["kind"] == "remote"), {})
    ok(f"Knoten des Partners (freigegeben von B) an B ({rn.get('name')}, {rn.get('owner')})",
       rn.get("name") == "horn.partner.example" and rn.get("owner") == p.get("id"))
    cls = {c["cls"]: c for c in d.get("clients", [])}
    ok(f"Clients je Grobklasse ({ {k: (v['live'], v['call']) for k, v in cls.items()} })",
       cls.get("android-app", {}).get("live") == 1 and cls.get("web-windows", {}).get("live") == 2 and cls.get("web-mac", {}).get("live") == 1
       and cls.get("ios-app", {}).get("call") == 1 and cls.get("web-mac", {}).get("call") == 1)
    ok("Clients: nur Anzahlen (Felder id, cls, live, push, call, n)", all(set(c) == {"id", "cls", "live", "push", "call", "n"} for c in d.get("clients", [])))
    k = (d.get("calls") or [{}])[0]
    ok(f"Anruf: ueber den Knoten, 2 hier, 1 ueber B, Grobklassen ({k.get('local')}, {k.get('remote')}, {k.get('os')})",
       len(d.get("calls", [])) == 1 and k.get("node") == hn.get("id") and k.get("local") == 2 and k.get("remote") == 1
       and k.get("peers") == [p.get("id")] and k.get("os") == {"ios-app": 1, "web-mac": 1} and k.get("media") == "video")
    kinds = {(e["a"], e["b"], e["kind"]) for e in d.get("edges", [])}
    ok("Kanten: Foederation, Server-Knoten, Freigabe, Clients-Server", ("self", p.get("id"), "fed") in kinds and ("self", hn.get("id"), "horn") in kinds
       and (p.get("id"), rn.get("id"), "share") in kinds and ("os:android-app", "self", "client") in kinds)
    fe = next((e for e in d.get("edges", []) if e["kind"] == "fed"), {})
    ok(f"Foederations-Kante: Zustand und Latenz ({fe.get('state')}, {fe.get('rtt')})", fe.get("state") == "ok" and fe.get("rtt") == p.get("rtt"))

    # --- Datenschutz -------------------------------------------------------------------------------------------------
    raw = json.dumps(d, ensure_ascii=False)
    fp = sql("a", "SELECT fingerprint, public_key FROM fed_peer")[0]
    bad = [x for x in ("fa-anna", "fa-carl", "Anna Berg", "Carl Ohne", "bert", "Bert Gast", cid, d1, d2, d3, nid, pub, fp[0], fp[1],
                       "hkd_", "hk_", "secret", "token", str(eport)) if x and x in raw]
    ok(f"keine Konten, Namen, Kennungen, Schluessel, Fingerprints ({bad})", not bad)
    ips = ips_in(d, skip=("host",))
    ok(f"keine IP-Adressen ausser den Hosts der Instanzen ({ips})", not ips)

    # --- Zwischenspeicher ---------------------------------------------------------------------------------------------
    d_a, d_b = net(A), net(A)
    ok(f"Stand 2 s zwischengespeichert (gleicher Zeitpunkt {d_a.get('at')} / {d_b.get('at')})", d_a.get("at") == d_b.get("at"))
    h0, e0 = wk_hits(), len(hits)
    end = time.time() + 12
    n = 0
    while time.time() < end:
        net(A)
        n += 1
        time.sleep(0.3)
    h1, e1 = wk_hits(), len(hits)
    ok(f"Messungen nur im Hintergrund, hoechstens alle 5 s ({n} Abrufe in 12 s -> {h1 - h0} Pruefungen an B, {e1 - e0} Echos am Knoten)",
       1 <= h1 - h0 <= 4 and 1 <= e1 - e0 <= 4)

    # --- Pulse: Nachrichtenzaehler der Foederation ------------------------------------------------------------------------
    a0 = (net(A).get("act") or {}).get(p.get("id"), {"in": 0, "out": 0})
    s, r = api("a", "fa-anna", "POST", "/api/notes", {"title": "Netz-Seite", "body": T.blocks(T.P("Hallo", "b1"))})
    s2, r2 = api("a", "fa-anna", "PUT", f"/api/notes/{r['id']}/shares", {"type": "remote", "target": f"@bert@{T.I['b']['host']}", "role": "view"})
    print("freigabe:", s, s2, str(r2)[:200], flush=True)
    a1 = wait(lambda: (lambda x: x if x.get("out", 0) > a0.get("out", 0) else None)((net(A).get("act") or {}).get(p.get("id"), {})), 20) or {}
    ok(f"Foederations-Nachrichten gezaehlt (aus {a0.get('out')} -> {a1.get('out')})", a1.get("out", 0) > a0.get("out", 0))

    # --- Live-Kanal ---------------------------------------------------------------------------------------------------
    c = http.client.HTTPConnection("127.0.0.1", T.I["a"]["admin"], timeout=10)
    c.request("GET", "/api/net/events", headers={"Cookie": "; ".join(f"{a}={b}" for a, b in A.ck.items()), "Host": f"127.0.0.1:{T.I['a']['admin']}"})
    r = c.getresponse()
    buf, t0 = b"", time.time()
    while b"event: net" not in buf or not buf.rstrip(b" ").endswith(b"\n\n") and time.time() - t0 < 8:
        x = r.read1(65536)
        if not x:
            break
        buf += x
        if b"event: net" in buf and buf.split(b"event: net", 1)[1].count(b"\n\n"):
            break
    c.close()
    m = re.search(rb"event: net\ndata: (.+)\n\n", buf)
    ev = json.loads(m.group(1)) if m else {}
    ok(f"Live-Kanal: text/event-stream, erstes Ereignis 'net' mit Stand ({r.status}, {len(ev.get('peers', []))} Partner)",
       r.status == 200 and "text/event-stream" in (r.getheader("content-type") or "") and ev.get("self", {}).get("host") == T.I["a"]["host"])

    # --- Seite ----------------------------------------------------------------------------------------------------------
    html = A.req("GET", "/net")[2]
    ok("Seite /net: Navigation, Karte, Tabellen mit Server, Partner, Knoten, Clients, Anruf",
       'href="/net" class="on"' in html and 'id="net-canvas"' in html and T.I["b"]["host"] in html and NODE in html
       and "Android-App" in html and "Browser · Windows" in html and "über Knoten" in html and "horn.partner.example" in html)
    ok("Seite /net: keine Konten/Namen im HTML", not any(x in html for x in ("fa-anna", "fa-carl", "Anna Berg", "Carl Ohne", cid, d1)))
    for lang, word in (("en", "Network"), ("es", "Red"), ("ru", "Сеть"), ("th", "เครือข่าย")):
        s, _, body = hreq("a", "GET", "/net", admin=True, hdr={"Cookie": "; ".join(f"{a}={b}" for a, b in A.ck.items()), "Accept-Language": lang})
        txt = body.decode("utf-8", "replace")
        ok(f"Seite /net auf {lang}: {word}", s == 200 and f"<h2>{word}</h2>" in txt)

    # --- Browser-Reihe ---------------------------------------------------------------------------------------------------
    js = os.path.join(APP, "tests", "browser_admin_netz.js")
    if shutil.which("node") and os.environ.get("PLAYWRIGHT"):
        env = dict(os.environ, SAGA_ADMIN=f"http://127.0.0.1:{T.I['a']['admin']}", SAGA_ADMIN_COOKIE=A.ck.get("hk_admin", ""),
                   SHOTS=os.environ.get("SHOTS", "/tmp/admin-netz-shots"), NET_PEER=T.I["b"]["host"], NET_NODE=NODE)
        r = subprocess.run(["node", js], env=env, capture_output=True, text=True, timeout=300)
        print(r.stdout[-6000:], r.stderr[-3000:], flush=True)
        ok("Browser-Reihe browser_admin_netz.js gruen", r.returncode == 0)
    else:
        print("(Browser-Reihe uebersprungen: node/PLAYWRIGHT fehlt)", flush=True)


def main():
    try:
        run_test()
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        STOP.set()
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler: {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\nADMIN-NETZ {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
