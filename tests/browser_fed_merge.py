"""Browser-Lauf 2.5 P3-E3 (tests/browser_fed_merge.js): zwei Saga-Instanzen A und B (wie tests/foederation_merge.py),
Anna (A) teilt eine Seite mit Bert (B, Bearbeiten); beide bearbeiten sie gleichzeitig im Browser - Bert auf seinem Server
mit negativer ID. Ports: FEDMERGE_PORTS (Standard 8370-8373). Raeumt auf.
Aufruf: NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright PW_CHANNEL=chromium NO_PROXY='*' python tests/browser_fed_merge.py"""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_merge as FM  # noqa: E402  (Ports, Instanzen)

T, F2 = FM.T, FM.F2
ok, api, sql, wait = T.ok, T.api, T.sql, T.wait


def run():
    try:
        A, B, pa, pb = F2.main()
        an = "fa-anna"
        nid = api("a", an, "POST", "/api/notes", {"title": "Gemeinsam ueber Server"})[1]["id"]
        s, _ = api("a", an, "PATCH", f"/api/notes/{nid}", {"format": "blocks", "body": FM.doc(FM.P("a1", "Erster Absatz"), FM.P("b2", "Zweiter Absatz"),
                                                                                           FM.P("c3", "Hallo Welt"))})
        s, _ = api("a", an, "PUT", f"/api/notes/{nid}/shares", {"type": "remote", "target": f"@bert@{FM.HB}", "role": "edit"})
        ok(f"A: Seite mit Bert@B geteilt ({s})", s == 200)
        ok("B: Freigabe angekommen", wait(lambda: len(sql("b", "SELECT 1 FROM fed_inbound")) >= 1, 20))
        env = dict(os.environ, BASE_A=T.I["a"]["url"], BASE_B=T.I["b"]["url"], CK_A=T.ck("a", an).split("=", 1)[1],
                   CK_B=T.ck("b", "fb-bert").split("=", 1)[1], NID=str(nid), RID=str(FM.ref(pa, nid)))
        r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_fed_merge.js")], env=env, timeout=600)
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
    print(f"\nBROWSER-FED-MERGE {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
