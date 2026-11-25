"""Browser-Lauf 2.6 (Bugs-DB 2390/2391, tests/browser_status_fed.js): zwei verbundene Saga-Instanzen A und B (Geruest wie
tests/foederation_live.py, Adressbuch von B auf A), Anna auf A waehlt Bert von B in der Ausnahme-Suche unter
"Erreichbarkeit". Ports: FEDLIVE_PORTS="a,a-admin,b,b-admin" (Standard 8650-8653), Datenbanken FEDLIVE_DB (Standard
saga_e7) + a/b. NUR_BILDER=1 und SHOTS werden an den Browser-Lauf durchgereicht. Raeumt auf (Datenbanken geloescht).
Aufruf: NODE_PATH=$(npm root -g) PLAYWRIGHT=playwright NO_PROXY='*' python tests/browser_status_fed.py"""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_live as FL  # noqa: E402  (Ports, Instanzen, Umgebung)

T, F2 = FL.T, FL.F2
ok, sql, wait = T.ok, T.sql, T.wait


def run():
    try:
        F2.main()
        ok("A: Bert von B im Adressbuch", wait(lambda: sql("a", "SELECT 1 FROM fed_directory WHERE name='Bert Gast'"), 30))
        env = dict(os.environ, BASE_A=T.I["a"]["url"], CK_A=T.ck("a", "fa-anna").split("=", 1)[1], HB=FL.HB, FIND="bert",
                   FIND_NAME="Bert Gast")
        r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_status_fed.js")], env=env, timeout=600)
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
    print(f"\nBROWSER-STATUS-FED {sum(res)}/{len(res)} OK – " + ("ALLES GRUEN" if all(res) else "FEHLER"))
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    run()
