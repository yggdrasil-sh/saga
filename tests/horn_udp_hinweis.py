"""2.5 A1 (Testlabor 2.11.: Knoten hinter Traefik nie per UDP erreicht): Saga merkt sich den UDP-Selbsttest des Knotens
(`udp_check` im hello des Steuerkanals) und zeigt in Admin -> Anrufe, wenn UDP aus oder nicht erreichbar ist - mit dem Port,
der freizugeben ist. Beim eingebauten Knoten ist das HORN_PORT/udp (nicht der Port der TLS-Durchreiche).

Geprueft: _clean_hello laesst nur bekannte Zustaende durch; udp_port (eingebaut: HORN_UDP_LISTEN/HORN_LISTEN/HORN_PORT,
Standard 8443; gekoppelt: sein Port); gekoppelter Knoten (Attrappe, eigener Schluessel) mit udp_check=fehlt -> Hinweis
"UDP nicht erreichbar" mit 443/udp; eingebauter Knoten (X-Saga-Horn-Key, braucht SAGA_HORN_KEY und HORN_PORT im Server) mit
udp_check=aus -> Hinweis "UDP ist am Knoten aus" mit HORN_PORT/udp und Hinweis auf tcp_notsent_lowat; mit udp_check=ok und
udp=true kein Hinweis. Wegwerf-Knoten ca-udp-*; raeumt auf.
  Server mit SAGA_HORN_KEY=<geheim> HORN_PORT=<port>; Test mit denselben Werten (HK_TEST_HORN_KEY, HK_TEST_HORN_PORT)."""
import base64
import hashlib
import http.client
import json
import os
import secrets
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import auth  # noqa: E402
from db import connect  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402
from saga import horn  # noqa: E402

BASE = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
SECRET = os.environ.get("SESSION_SECRET") or os.environ.get("SECRET_KEY", "")
HKEY = os.environ.get("HK_TEST_HORN_KEY", "")
HPORT = int(os.environ.get("HK_TEST_HORN_PORT", "8443"))
res = []


def ok(n, c, info=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL") + " " + n + ("" if c or info is None else " -> " + str(info)[:300]), flush=True)


def admin(path):
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    cookie = sa.sign(base64.b64encode(json.dumps({"aid": "ca-udp-adm", "ep": time.time() + 5, "csrf": "x"}).encode())).decode()
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    c.request("GET", path, headers={"Cookie": "hk_admin=" + cookie, "Accept-Language": "de"})
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, data


def ctl(sk, name, hdr=None, **kw):
    """Steuerkanal wie ein Knoten: challenge -> hello -> welcome; Verbindung bleibt offen (Rueckgabe)."""
    from websockets.sync.client import connect as wsc
    ws = wsc(f"ws://{BASE.hostname}:{BASE.port}/api/horn/ctl", additional_headers=dict({"X-Saga": "1"}, **(hdr or {})), open_timeout=5, proxy=None)
    ch = json.loads(ws.recv(5))
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    ws.send(json.dumps(dict({"t": "hello", "proto": "horn-ctl/1", "name": name, "url": f"https://{name}", "pub": pub,
                             "sig": base64.b64encode(sk.sign(f"horn-ctl/1 hello\n{ch['nonce']}".encode())).decode(),
                             "version": "test", "udp": False, "max_legs": 48, "cert": {"mode": "", "state": ""}}, **kw)))
    w = json.loads(ws.recv(5))
    return ws, w


def row_of(html, name):
    i = html.find(f"<b>{name}</b>")
    return html[i:html.find("</tr>", i)] if i >= 0 else ""


def main():
    # --- Logik ohne Server --------------------------------------------------------------------------------------------
    base = {"name": "horn-x.example.org", "url": "https://horn-x.example.org"}
    ok("hello: udp_check fehlt/aus/ok/unbekannt uebernommen, anderes verworfen",
       [horn._clean_hello(dict(base, udp_check=v))["udp_check"] for v in ("fehlt", "aus", "ok", "unbekannt", "<b>", 3, None)]
       == ["fehlt", "aus", "ok", "unbekannt", "", "", ""])
    old = {k: os.environ.pop(k, None) for k in ("HORN_UDP_LISTEN", "HORN_LISTEN", "HORN_PORT")}
    try:
        p0 = horn.udp_port({"builtin": 1})
        os.environ["HORN_PORT"] = "9443"
        p1 = horn.udp_port({"builtin": 1})
        os.environ["HORN_UDP_LISTEN"] = ":3478"
        p2 = horn.udp_port({"builtin": 1})
        p3 = horn.udp_port({"builtin": 0, "port": 444})
        p4 = horn.udp_port({"builtin": 0})
    finally:
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    ok(f"udp_port: eingebaut 8443 / HORN_PORT 9443 / HORN_UDP_LISTEN 3478; gekoppelt 444 bzw. 443 ({p0},{p1},{p2},{p3},{p4})",
       (p0, p1, p2, p3, p4) == (8443, 9443, 3478, 444, 443))

    # --- Admin-Seite mit Knoten-Attrappen --------------------------------------------------------------------------
    auth.get_user("ca-udp-adm") or auth.create_user("ca-udp-adm", secrets.token_urlsafe(12), "Admin UDP", is_admin=True)
    calls0 = dict(auth.settings().get("calls") or {})
    auth.save_setting("calls", dict(calls0, enabled=True, horn=True))
    name = f"ca-udp-{secrets.token_hex(3)}.example.org"
    sk = Ed25519PrivateKey.generate()
    pub = base64.b64encode(sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    nid = secrets.token_hex(8)
    socks = []
    builtin0 = None
    try:
        with connect() as c, c:
            c.execute("INSERT INTO horn_node (id, name, pub, builtin, url, state, created, port) VALUES (?,?,?,0,?,'ok',?,443)",
                      (nid, name, pub, f"https://{name}", time.time()))
            r = c.execute("SELECT id, name, pub, url FROM horn_node WHERE builtin=1 AND removed IS NULL").fetchone()
            builtin0 = tuple(r) if r else None
        time.sleep(1.2)                                   # Einstellungen im Server (kurzer Zwischenspeicher)
        ws, w = ctl(sk, name, udp_check="fehlt")
        socks.append(ws)
        ok("gekoppelter Knoten verbunden (welcome)", w.get("t") == "welcome" and w.get("node_id") == nid, w)
        s, html = admin("/calls")
        rw = row_of(html, name)
        ok("Admin: 'UDP nicht erreichbar - Anrufe laufen ueber TCP/TLS. Port 443/udp freigeben' in der Zeile des Knotens",
           s == 200 and "UDP nicht erreichbar" in rw and "443/udp" in rw, (s, rw[:400]))
        ws.close(); socks.remove(ws)
        time.sleep(0.3)
        ws, w = ctl(sk, name, udp=True, udp_check="ok")
        socks.append(ws)
        s, html = admin("/calls")
        rw = row_of(html, name)
        ok("udp_check=ok und udp an: kein Hinweis", s == 200 and rw and "UDP nicht erreichbar" not in rw and "UDP ist am Knoten aus" not in rw, rw[:300])
        ws.close(); socks.remove(ws)

        if not HKEY:
            ok("eingebauter Knoten: HK_TEST_HORN_KEY fehlt (Server mit SAGA_HORN_KEY starten)", False)
        else:
            bname = f"ca-udp-b{secrets.token_hex(3)}.example.org"
            bsk = Ed25519PrivateKey.generate()
            ws, w = ctl(bsk, bname, hdr={"X-Saga-Horn-Key": HKEY}, udp_check="aus", builtin=True)
            socks.append(ws)
            ok("eingebauter Knoten verbunden (welcome)", w.get("t") == "welcome", w)
            s, html = admin("/calls")
            rw = row_of(html, bname)
            ok(f"Admin eingebaut: 'UDP ist am Knoten aus' mit {HPORT}/udp (HORN_PORT, nicht 443)",
               s == 200 and "UDP ist am Knoten aus" in rw and f"{HPORT}/udp" in rw, rw[:400])
            i = html.find(f'id="n-{w.get("node_id")}"')
            det = html[i:html.find("</details>", i)] if i >= 0 else ""
            ok(f"Port-Tabelle eingebaut: {HPORT}/UDP, Docker-Beispiel ohne 443:443/tcp, Hinweis tcp_notsent_lowat",
               f"{HPORT}/UDP" in det and f'"{HPORT}:{HPORT}/udp"' in det and '"443:443/tcp"' not in det
               and "tcp_notsent_lowat=16384" in det, det[:600])
    finally:
        for ws in socks:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.3)
        with connect() as c, c:
            c.execute("DELETE FROM horn_node WHERE name LIKE 'ca-udp-%'")
            if builtin0:                                  # vorhandenen eingebauten Knoten wiederherstellen (Name/Schluessel)
                c.execute("UPDATE horn_node SET name=?, pub=?, url=? WHERE id=?", (builtin0[1], builtin0[2], builtin0[3], builtin0[0]))
        auth.save_setting("calls", calls0)
    n = sum(res)
    print(f"\nHORN-UDP-HINWEIS {n}/{len(res)}" + (" - ALLES GRUEN" if all(res) else " - FEHLER"))
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
