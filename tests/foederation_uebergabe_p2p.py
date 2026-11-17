"""2.5 Feldfall 17.11.: "Hierher übernehmen" im DIREKTEN (Mesh-)1:1 ueber Servergrenzen (ohne Knoten). Zwei eigene
Saga-Instanzen A (Anna, fuehrt den Anruf) und B (Bert), verbunden; Einstellungen wie tests/foederation_anrufe_raum.py.

Browser-Lauf (BJS=1, tests/browser_fed_uebergabe_p2p.js): Anna ruft mit A1 Bert an, Bert nimmt an, Annas zweites Geraet
A2 uebernimmt ueber die Leiste. Gegenprobe wie im Feld: Bert und A2 wenden keine Kandidaten an (die Verbindung
zum Gegenueber kommt nie zustande) -> nach handover_s "Übernahme hat nicht geklappt", A1 laeuft weiter, und die
Anruf-Diagnose von A2 meldet je Verbindung den Zustand (ho-fail: Antwort, ICE-Zustand, Kandidaten je Typ). Ports 8881-8884, Datenbanken saga_uep2p_a/_b. Raeumt auf."""
import json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foederation_anrufe_raum as R  # noqa: E402  (Instanzen, Verbinden, Einstellungen)

T = R.T
T.I.pop("c", None)
T.I["a"].update(port=8881, admin=8882, db="saga_uep2p_a", url="http://127.0.0.1:8881", host="127.0.0.1:8881", state="/tmp/uebergabe-p2p/st-a",
                allow="localhost:8883")
T.I["b"].update(port=8883, admin=8884, db="saga_uep2p_b", url="http://localhost:8883", host="localhost:8883", state="/tmp/uebergabe-p2p/st-b",
                allow="127.0.0.1:8881")
ok, wait, sql = T.ok, T.wait, T.sql


def setup():
    for k in T.I:
        T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        T.pg_admin(f"CREATE DATABASE {T.I[k]['db']}")
        try:
            os.remove(os.path.join(T.I[k]["state"], "server.log"))
        except OSError:
            pass
    R.F2.py("a", T.SETUP.format(allow=T.I["a"]["allow"], users=[("fa-anna", "Anna Berg", "anna")], groups=[]))
    R.F2.py("b", T.SETUP.format(allow=T.I["b"]["allow"], users=[("fb-bert", "Bert Gast", "bert")], groups=[]))
    for k in T.I:
        R.F2.py(k, "import auth\nauth.save_setting('calls', {'enabled': True, 'stun': '', 'turn_urls': '', 'turn_mode': 'rest', "
                   "'turn_secret': '', 'turn_user': '', 'turn_password': '', 'turn_ttl': 43200, 'relay_only': False, 'ring_s': 45, 'horn': True, 'diag': True})\nprint(1)")
    for k in T.I:
        R.F2.start(k)
    adm = {k: T.Admin(k) for k in T.I}
    ok("Admins angemeldet", all(a.login() for a in adm.values()))
    for a in adm.values():
        a.post({"action": "enable", "on": "1"})
    R.connect("a", "b", adm["a"], adm["b"])


def main():
    rc = 1
    try:
        setup()
        ok("Faehigkeit calls3 ausgehandelt (A-B)", wait(lambda: R.has_cap("a", T.I["b"]["host"], "calls3") and R.has_cap("b", T.I["a"]["host"], "calls3"), 25))
        env = dict(os.environ, SAGA_A=T.I["a"]["url"], SAGA_B=T.I["b"]["url"], SAGA_USER_A="fa-anna", SAGA_USER_B="fb-bert", SAGA_PASS="Fed-User-Pass-123")
        r = subprocess.call(["node", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_fed_uebergabe_p2p.js")], env=env, timeout=600)
        ok(f"Browser-Lauf browser_fed_uebergabe_p2p.js (rc {r})", r == 0)
        rc = 0
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        ok(f"Ablauf ohne Ausnahme ({type(ex).__name__}: {ex})", False)
    finally:
        if os.environ.get("KEEP_LOGS"):
            for k in T.I:
                print(f"--- Log {k}: {T.I[k]['state']}/server.log")
        T.stop_all()
        for k in T.I:
            n, txt = T.server_errors(k)
            ok(f"{k.upper()}: Serverfehler {n}", n == 0)
            if n:
                print(txt[:3000])
        for k in T.I:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
    print(f"\nFOEDERATION-UEBERGABE-P2P {sum(T.res)}/{len(T.res)}", "- ALLES GRUEN" if all(T.res) and rc == 0 else "- FEHLER")
    sys.exit(0 if all(T.res) and rc == 0 else 1)


if __name__ == "__main__":
    json.dumps({})
    main()
