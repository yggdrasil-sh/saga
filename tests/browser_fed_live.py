"""Browser-Lauf 2.5 P3-E7 (tests/browser_fed_live.js): zwei Saga-Instanzen A und B (wie tests/foederation_live.py) mit
notes.live und Tiptap, je ein Browser pro Server an derselben Seite (Bert auf B mit negativer ID). Ports: FEDLIVE_PORTS
(Standard 8650-8653). Raeumt auf.
Aufruf: NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium NO_PROXY='*' python tests/browser_fed_live.py"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_live as FL  # noqa: E402  (Ports, Instanzen, Umgebung)

T, F2 = FL.T, FL.F2
ok, api, sql, wait = T.ok, T.api, T.sql, T.wait


def run():
    try:
        A, B, pa, pb = F2.main()
        for k in ("a", "b"):
            F2.setting(k, "notes", {"live": True})
            F2.setting(k, "editor", {"engine": "choice"})
        time.sleep(5.3)
        FL.rehello(A, pa); FL.rehello(B, pb)
        cap = "collab1-" + FL.M.SCHEMA_HASH
        ok("collab1 ausgehandelt", wait(lambda: cap in FL.caps_of("a") and cap in FL.caps_of("b"), 15))
        an = "fa-anna"
        nid = api("a", an, "POST", "/api/notes", {"title": "Live ueber Server"})[1]["id"]
        api("a", an, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": json.dumps({"blocks": [FL.P("p1", "Hallo"), FL.P("p2", "Zwei")]})})
        bert = f"@bert@{FL.HB}"
        s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": bert, "role": "edit"})
        ok(f"A: Seite mit Bert@B geteilt ({s})", s == 200)
        ok("B: Freigabe angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
        env = dict(os.environ, BASE_A=T.I["a"]["url"], BASE_B=T.I["b"]["url"], CK_A=T.ck("a", an).split("=", 1)[1],
                   CK_B=T.ck("b", "fb-bert").split("=", 1)[1], NID=str(nid), RID=str(FL.ref(pa, nid)), BERT=bert)
        r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_fed_live.js")], env=env, timeout=600)
        ok(f"Browser-Lauf ({r})", r == 0)
    except Exception:
        import traceback
        traceback.print_exc()
        T.res.append(False)
    finally:
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt, flush=True)
        for k in T.I:
            try:
                T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
            except Exception as ex:  # noqa: BLE001
                print("aufraeumen:", ex)
    res = T.res
    print(f"\nBROWSER-FED-LIVE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
