"""Probe fuer tests/graph_schatten_rig.py (2.6, Bugs-DB 2603): Rig fuer ein Wegwerf-Konto aufsetzen, dann ueber den
laufenden Server pruefen, was tests/browser_graph_schatten.js erwartet - GET /api/graph mem {core, snap, shadow, links,
links_total, hits24, rows mit "gerade bewusst"}, Einstellungen (snapshot, shadow), REST recall mit der Rig-Frage ->
Live-Ereignis memory.recalled mit schatten; Variante --aus ohne links/hits24; clean raeumt alles weg.
Aufruf (saga-test): tools/testumgebung/umgebung.sh lauf server <slot> graph_schatten_rig_probe.py"""
import base64
import http.client
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse

import db

db.wait_ready()
import auth  # noqa: E402
from itsdangerous import TimestampSigner  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
U = "gsr-" + secrets.token_hex(3)
res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:600], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5, "sec_at": int(time.time())}).encode())).decode()


def req(m, p, cookie=None, body=None, bearer=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    h = {"Host": HOST, "Content-Type": "application/json", "Accept": "application/json"}
    if cookie:
        h["Cookie"] = "saga=" + cookie
        h["X-Saga"] = "1"
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    d = r.read()
    c.close()
    try:
        return r.status, json.loads(d)
    except ValueError:
        return r.status, d


def rig(*a):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.join(HERE, "pgshim"), os.path.dirname(HERE)]))
    p = subprocess.run([sys.executable, os.path.join(HERE, "graph_schatten_rig.py"), *a], capture_output=True, text=True, timeout=120,
                       cwd=os.path.dirname(HERE), env=env)
    return p.returncode, p.stdout, p.stderr


def events(cookie, out, stop):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=60)
    c.request("GET", "/api/events", headers={"Host": HOST, "Accept": "text/event-stream", "Cookie": "saga=" + cookie})
    r = c.getresponse()
    cur = {}
    while not stop.is_set():
        line = r.readline()
        if not line:
            break
        line = line.decode().rstrip("\r\n")
        if line == "":
            if cur.get("event") == "memory.recalled":
                out.append(json.loads(cur.get("data") or "null"))
            cur = {}
        elif not line.startswith(":"):
            k, _, v = line.partition(":")
            cur[k] = v[1:] if v.startswith(" ") else v


def main():
    auth.create_user(U, "Gsr-Pass-12345", U)
    C = ck(U)
    stop = threading.Event()
    try:
        time.sleep(5.6)                                     # ai_embed (Rig setzt Trigramme) im Server zwischengespeichert
        rc, out, err = rig("setup", U)
        ok("Rig setup: rc 0, JSON", rc == 0 and out.strip().startswith("{"), (rc, out[-300:], err[-500:]))
        st = json.loads(out)
        time.sleep(5.6)
        s, d = req("GET", "/api/graph", C)
        m = d.get("mem") or {}
        rows = {r[0]: r for r in m.get("rows") or []}
        now = m.get("now") or time.time()
        ok("Graph: core, snap (3), shadow true", m.get("core") == st["core"] and m.get("snap") == sorted(st["snaps"]) and m.get("shadow") is True, m)
        ok("Graph: links = Rig-Links (beide Enden Knoten), links_total", sorted(x[:2] + x[3:] for x in m.get("links") or [])
           == sorted([x[0], x[1], x[3], x[4]] for x in st["links"]) and m.get("links_total") == len(st["links"]), (m.get("links"), st["links"]))
        h = {x[0]: x[1] for x in m.get("hits24") or []}
        ok("Graph: hits24 (Hochbeet 3, Packliste 1, Sauerteig 1)", h == {st["rows"]["Hochbeet"]: 3, st["rows"]["Packliste"]: 1,
                                                                         st["rows"]["Sauerteig"]: 1}, m.get("hits24"))
        aktiv = [i for i, r in rows.items() if r[2] is not None and now - r[2] <= 1800]
        ok("Graph: einige Zeilen gerade bewusst (letzter Abruf <= 30 min)", len(aktiv) >= 3, rows)
        s, d = req("GET", "/api/account/memory", C)
        ok("Einstellungen: snapshot (3, Option da), shadow an mit Links", s == 200 and d["snapshot"]["found"] and d["snapshot"]["count"] == 3
           and d["shadow"]["on"] and d["shadow"]["links"] == len(st["links"]) and d["columns"]["sensibel"]["ok"], (s, d.get("snapshot"), d.get("shadow")))
        ev = []
        threading.Thread(target=events, args=(C, ev, stop), daemon=True).start()
        time.sleep(1.0)
        s, d = req("POST", "/api/v1/memory/recall", body={"text": st["query"]}, bearer=st["token"])
        for _ in range(40):
            if ev:
                break
            time.sleep(0.1)
        ok("REST recall mit der Rig-Frage: Live-Ereignis mit schatten = [schatten_id]", s == 200 and ev and ev[0].get("schatten") == [st["schatten_id"]]
           and st["rows"]["Tomaten"] in ev[0]["ids"], (s, ev, d))
        rc, out, err = rig("clean", U)
        s, d = req("GET", "/api/graph", C)
        with db.connect() as c:
            left = c.execute("SELECT 1 FROM mem_link WHERE uid=?", (U,)).fetchall()
        ok("Rig clean: kein Gedaechtnis mehr", rc == 0 and "mem" not in d and not left, (rc, err[-300:]))
        rc, out, err = rig("setup", U, "--aus")
        st2 = json.loads(out) if rc == 0 else {}
        s, d = req("GET", "/api/graph", C)
        m = d.get("mem") or {}
        ok("Rig --aus: shadow false, keine links/hits24, Einstellungen shadow aus", rc == 0 and st2.get("shadow") is False
           and m.get("shadow") is False and "links" not in m and "hits24" not in m and not req("GET", "/api/account/memory", C)[1]["shadow"]["on"], m)
        rig("clean", U)
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok("Ablauf ohne Ausnahme", False, repr(ex))
    finally:
        stop.set()
        from saga.userdata import delete_user_data
        if auth.get_user(U):
            delete_user_data(U, drop_user=True)
    print(f"\nRIG-PROBE {sum(res)}/{len(res)}", "- ALLES GRUEN" if res and all(res) else "- FEHLER")
    return 0 if res and all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
