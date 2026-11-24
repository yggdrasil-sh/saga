"""Testuhr und Takt (2.6, Bugs-DB 2265 K-T): /_test/clock|tick|flush nur mit SAGA_TEST_HOOKS=1 + SAGA_TEST_TOKEN, Kopf
X-Saga-Test, von 127.0.0.1/::1 und ohne Weiterleitungs-Koepfe - sonst 404 (jede Methode). Ohne Server und ohne Datenbank (FastAPI-TestClient, Absender je Anfrage gesetzt).
Aufruf (saga-test): PYTHONPATH=tests/pgshim:. python tests/testuhr.py"""
import os
import sys
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from saga import testclock as TC

res = []


def ok(n, c, info=""):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, "" if c else str(info)[:300], flush=True)


class From:
    """ASGI-Huelle: Absender-Adresse der Anfrage festlegen (TestClient meldet sonst 'testclient')."""

    def __init__(self, app):
        self.app, self.host = app, "127.0.0.1"

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, client=(self.host, 50000))
        return await self.app(scope, receive, send)


def main():
    app = FastAPI()
    app.include_router(TC.router)
    wrap = From(app)
    TOK = "t" * 40
    bare = TestClient(wrap)
    cl = TestClient(wrap, headers={"X-Saga-Test": TOK})
    old = {k: os.environ.pop(k, None) for k in ("SAGA_TEST_HOOKS", "SAGA_TEST_TOKEN")}
    try:
        paths = [("GET", "/_test/clock"), ("POST", "/_test/clock"), ("POST", "/_test/tick"), ("POST", "/_test/flush")]
        st = [cl.request(m, p, json={}).status_code for m, p in paths]
        ok(f"ohne SAGA_TEST_HOOKS: alles 404 ({st})", st == [404] * 4)
        wrong = [cl.request(m, p).status_code for m, p in (("GET", "/_test/tick"), ("DELETE", "/_test/clock"), ("GET", "/_test/x"))]
        ok(f"falsche Methoden und Pfade: 404, nie 405 ({wrong})", wrong == [404] * 3)
        os.environ["SAGA_TEST_HOOKS"] = "1"
        ok("ohne SAGA_TEST_TOKEN: aus", cl.get("/_test/clock").status_code == 404)
        os.environ["SAGA_TEST_TOKEN"] = "kurz"
        ok("Token unter 32 Zeichen: aus", TestClient(wrap, headers={"X-Saga-Test": "kurz"}).get("/_test/clock").status_code == 404)
        os.environ["SAGA_TEST_TOKEN"] = TOK
        os.environ["SAGA_TEST_HOOKS"] = "yes"
        ok("nur der Wert 1 schaltet ein", cl.get("/_test/clock").status_code == 404)
        os.environ["SAGA_TEST_HOOKS"] = "1"
        ok("ohne Kopf X-Saga-Test: 404", bare.get("/_test/clock").status_code == 404)
        ok("falscher Token: 404", TestClient(wrap, headers={"X-Saga-Test": "u" * 40}).get("/_test/clock").status_code == 404)
        fw = [cl.get("/_test/clock", headers={h: "127.0.0.1"}).status_code for h in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "Via")]
        ok(f"mit Weiterleitungs-Koepfen (Reverse-Proxy): 404 ({fw})", fw == [404] * 4)
        for h in ("10.0.0.5", "192.168.1.2", "::ffff:127.0.0.1", "testclient", ""):
            wrap.host = h
            st = [cl.request(m, p, json={}).status_code for m, p in paths]
            ok(f"von {h or '(leer)'}: 404 ({st})", st == [404] * 4)
        wrap.host = "::1"
        ok("von ::1: erlaubt", cl.get("/_test/clock").status_code == 200)
        wrap.host = "127.0.0.1"

        d = cl.get("/_test/clock").json()
        ok(f"Start: echte Zeit, nicht angehalten ({d})", abs(d["now"] - time.time()) < 2 and d["offset"] == 0 and not d["frozen"])
        ok("kein Cache", cl.get("/_test/clock").headers.get("cache-control") == "no-store")
        d = cl.post("/_test/clock", json={"advance": 3600}).json()
        ok(f"advance 1 h ({d['offset']})", abs(d["offset"] - 3600) < 0.01 and abs(TC.now() - time.time() - 3600) < 2)
        d = cl.post("/_test/clock", json={"freeze": True}).json()
        t1 = TC.now(); time.sleep(0.05)
        ok(f"freeze: Zeit steht ({d['frozen']})", d["frozen"] and TC.now() == t1)
        d = cl.post("/_test/clock", json={"advance": 60}).json()
        ok("advance im angehaltenen Zustand: genau +60", abs(TC.now() - t1 - 60) < 1e-6)
        d = cl.post("/_test/clock", json={"set": 1893456000}).json()           # 2030-01-01
        ok(f"set: feste Zeit ({d['now']})", TC.now() == 1893456000.0 and d["frozen"])
        d = cl.post("/_test/clock", json={"freeze": False}).json()
        ok(f"freeze aus: laeuft ab der gesetzten Zeit weiter ({d['now']})", abs(TC.now() - 1893456000) < 2 and not d["frozen"])
        bad = [cl.post("/_test/clock", json=b).json()["now"] for b in ({"set": "morgen"}, {"advance": True}, {"set": None})]
        ok("ungueltige Werte aendern nichts", all(abs(x - 1893456000) < 3 for x in bad))
        r = cl.post("/_test/clock", content=b"kein json", headers={"Content-Type": "application/json"})
        ok("kaputter Body: 200, nichts geaendert", r.status_code == 200 and abs(r.json()["now"] - 1893456000) < 3)
        d = cl.post("/_test/clock", json={"reset": True}).json()
        ok(f"reset: wieder echte Zeit ({d})", d["offset"] == 0 and not d["frozen"] and abs(TC.now() - time.time()) < 1)

        seen = []

        def a():
            seen.append("a")

        def boom():
            raise RuntimeError("geheim")

        TC.on_tick(a); TC.on_tick(a); TC.on_tick(boom)
        TC.on_flush(lambda: seen.append("f"))
        d = cl.post("/_test/tick").json()
        ok(f"tick: Haken einmal je Funktion, Fehler gezaehlt ({d}, {seen})", d == {"ran": 1, "errors": 1} and seen == ["a"])
        d = cl.post("/_test/flush").json()
        ok(f"flush: Puffer-Haken ({d}, {seen})", d == {"ran": 1, "errors": 0} and seen == ["a", "f"])
        ok("GET auf tick/flush gibt es nicht (404)", cl.get("/_test/tick").status_code == 404)
        for raw in (b'{"set": NaN}', b'{"advance": Infinity}', b'{"set": 1e400}', b'{"advance": 1e12}'):
            r = cl.post("/_test/clock", content=raw, headers={"Content-Type": "application/json"})
            ok(f"{raw.decode()}: abgewiesen, Uhr bleibt ({r.status_code})", r.status_code == 200 and r.json()["offset"] == 0)

        src = open(os.path.join(os.path.dirname(TC.__file__), "..", "app.py"), encoding="utf-8").read()
        ok("app.py bindet die Testuhr ein", "app.include_router(testclock.router)" in src)
    finally:
        TC._TICK.clear(); TC._FLUSH.clear(); TC.set_clock({"reset": True})
        for k, v in old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    print(f"\nTestuhr {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    return 0 if all(res) else 1


if __name__ == "__main__":
    sys.exit(main())
