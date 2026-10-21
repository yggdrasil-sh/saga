"""Browser-Lauf Gjallarhorn ueber Servergrenzen (tests/browser_fed_horn.js): zwei Saga-Instanzen A (Heim) und B mit je
einem gekoppelten Knoten (wie tests/foederation_horn.py), je drei Personen. Das Skript ruft fuer den Fallwechsel diese
Steuerung (CTL) auf: POST /case-b leert den Knoten von B und schaltet "Teilen" am Knoten von A an (A schickt B die Adresse
per hello, B nimmt sie in die CSP), GET /state liefert Bruecken und Kontingente der Knoten. Raeumt auf."""
import json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_horn as FH  # noqa: E402  (Ports, Knoten, Hilfen)

T, F2 = FH.T, FH.F2
ok, wait = T.ok, T.wait
FH.USERS_A[:] = [("fa-anna", "Anna Berg", "anna"), ("fa-ada", "Ada Heim", "ada"), ("fa-alma", "Alma Heim", "alma")]
FH.USERS_B[:] = [("fb-bert", "Bert Gast", "bert"), ("fb-ben", "Ben Gast", "ben"), ("fb-bea", "Bea Gast", "bea")]
CTL_PORT = 18995
ST = {}


def state():
    adm = ST["adm"]
    na, nb = FH.node_of(adm["a"], "horn-a.test"), FH.node_of(adm["b"], "horn-b.test")
    pb = ST["pb"]
    q = FH.F2.py("a", f"from saga import horn\nprint(json.dumps(horn.quota_key({pb})))")
    qs = (na.get("quotas") or {}).get(q) or {}
    return {"a_up": sum(1 for v in (na.get("bridges") or {}).values() if v.get("st") == "up"),
            "b_up": sum(1 for v in (nb.get("bridges") or {}).values() if v.get("st") == "up"),
            "a_peers": na.get("peers"), "b_peers": nb.get("peers"), "quota_peers": qs.get("peers"), "a_util": na.get("util"), "a_cpu": na.get("cpu"),
            "b_cpu": nb.get("cpu"), "a_up_mbit": na.get("up"), "b_up_mbit": nb.get("up")}


def case_b():
    adm = ST["adm"]
    nb = FH.node_of(adm["b"], "horn-b.test")
    FH.calls_post(adm["b"], {"action": "drain", "id": nb["id"]})
    na = FH.node_of(adm["a"], "horn-a.test")
    FH.calls_post(adm["a"], {"action": "share", "id": na["id"]})
    good = wait(lambda: FH.node_of(adm["b"], "horn-b.test").get("status") in ("draining", "draining_upd") and FH.node_of(adm["a"], "horn-a.test").get("share"), 15)
    # A schickt B die Adresse seines geteilten Knotens per hello; B nimmt sie in connect-src (Zwischenspeicher bis 15 s)
    t0 = time.time()

    def csp():
        st, h, _ = T.hreq("b", "GET", "/chats", cookie=T.ck("b", "fb-bert"))
        return "https://127.0.0.1:8481" in (h.get("content-security-policy") or "")
    got = wait(csp, 40, 1)
    return {"ok": bool(good and got), "drain": bool(good), "hello_csp_s": round(time.time() - t0, 1) if got else None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _out(self, d):
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._out(state() if self.path == "/state" else {})

    def do_POST(self):
        self._out(case_b() if self.path == "/case-b" else {})


def main():
    rc = 1
    try:
        FH.BIN[0] = FH.gobuild()
        ok("gjallarhorn gebaut", bool(FH.BIN[0]))
        adm = FH.setup(horn_b=True)
        ST["adm"] = adm
        FH.calls_post(adm["a"], {"action": "caps"})
        ok("horn auf beiden Seiten", wait(lambda: FH.has_cap("a", T.I["b"]["host"], "horn") and FH.has_cap("b", T.I["a"]["host"], "horn"), 15))
        ST["pb"] = FH.sql("a", "SELECT id FROM fed_peer WHERE host=?", (T.I["b"]["host"],))[0][0]
        FH.node_start("a", "horn-a.test", 8481, share=False)
        FH.node_start("b", "horn-b.test", 8482)
        ok("Knoten A und B bereit", wait(lambda: FH.node_of(adm["a"], "horn-a.test").get("status") == "ready"
                                         and FH.node_of(adm["b"], "horn-b.test").get("status") == "ready", 30))
        srv = ThreadingHTTPServer(("127.0.0.1", CTL_PORT), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = dict(os.environ, SAGA_A=T.I["a"]["url"], SAGA_B=T.I["b"]["url"], SAGA_PASS="Fed-User-Pass-123", CTL=f"http://127.0.0.1:{CTL_PORT}",
                   SAGA_USERS_A=",".join(u for u, _, _ in FH.USERS_A), SAGA_USERS_B=",".join(u for u, _, _ in FH.USERS_B))
        r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_fed_horn.js")], env=env, timeout=1800)
        ok(f"Browser-Lauf browser_fed_horn.js (rc {r})", r == 0)
        srv.shutdown()
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        FH.node_stop_all()
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        for name in FH.NODES:
            lg = FH.node_log(name)
            print(f"--- Knoten {name} (Auszug)\n" + "\n".join(x for x in lg.splitlines() if "bifroest" in x or "kontingent" in x or "panic" in x)[-1500:])
            if "panic" in lg:
                ok(f"Knoten {name}: kein panic", False)
        for k in T.I:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        import shutil
        shutil.rmtree(FH.STATE, ignore_errors=True)
    print(f"\nBROWSER-FED-HORN-LAUF {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


if __name__ == "__main__":
    main()
