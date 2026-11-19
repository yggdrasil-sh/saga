"""Testumgebung: zwei verbundene Saga-Instanzen A und B fuer die Browser-Reihen tests/browser_fed*.js (die sonst zwei von
Hand verbundene Test-Stacks brauchen). Geruest aus tests/foederation2.py (Konten, Admin-Handshake, Adressbuch), eigene
Ports/DBs/Zustand; mit --knoten laeuft A hinter saga-media mit eingebautem Knoten (calls.horn + horn_all) fuer
tests/browser_fed_horn_dm.js. Schreibt die Umgebung der Browser-Reihen nach --env und laeuft, bis SIGTERM kommt; dann
Instanzen beenden und DBs loeschen.
  UMG_FED_PORTS="pa,aa,pb,ab" UMG_FED_DB=saga_umg_0f PYTHONPATH=tests/pgshim:. python tools/testumgebung/zwei_stacks.py --env x.env --state DIR
Knoten: UMG_BIN (saga-media, gjallarhorn), UMG_HORN_PORT, UMG_FED_MEDIA (interner Port hinter saga-media), UMG_CERT/UMG_KEY (Zertifikat fuer localhost aus der Test-CA)."""
import argparse, os, shlex, signal, subprocess, sys, time

APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(APP, "tests"))
import foederation as T  # noqa: E402
import foederation2 as F2  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--env", required=True)
ap.add_argument("--state", required=True)
ap.add_argument("--knoten", action="store_true")
A_ = ap.parse_args()
pa, aa, pb, ab = (int(x) for x in os.environ.get("UMG_FED_PORTS", "9011,9012,9013,9014").split(","))
DB = os.environ.get("UMG_FED_DB", "saga_umg_f")
T.I["a"].update(port=pa, admin=aa, db=DB + "a", url=f"http://127.0.0.1:{pa}", host=f"127.0.0.1:{pa}", state=os.path.join(A_.state, "a"),
                allow=f"localhost:{pb}")
T.I["b"].update(port=pb, admin=ab, db=DB + "b", url=f"http://localhost:{pb}", host=f"localhost:{pb}", state=os.path.join(A_.state, "b"),
                allow=f"127.0.0.1:{pa}")
PW = "Fed-User-Pass-123"                 # tests/foederation.py SETUP
HORN = int(os.environ.get("UMG_HORN_PORT", str(pa + 5)))


def start(k):
    i = T.I[k]
    os.makedirs(i["state"], exist_ok=True)
    log = open(os.path.join(i["state"], "server.log"), "a")
    env = F2.env_of(k)
    cmd = [sys.executable, "run.py"]
    if k == "a" and A_.knoten:
        b = os.environ["UMG_BIN"]
        env = {x: v for x, v in env.items() if not x.startswith("HORN_")}
        env.update(MEDIA_INTERNAL_PORT=os.environ.get("UMG_FED_MEDIA", str(pa + 4)), GJALLARHORN_BIN=os.path.join(b, "gjallarhorn"), HORN_PORT=str(HORN), HORN_NAME="localhost",
                   HORN_TLS_CERT=os.environ["UMG_CERT"], HORN_TLS_KEY=os.environ["UMG_KEY"], HORN_UDP="0", HORN_ICE_IPS="127.0.0.1")
        cmd = [os.path.join(b, "saga-media"), "--"] + cmd
    T.procs[k] = subprocess.Popen(cmd, env=env, cwd=APP, stdout=log, stderr=subprocess.STDOUT)
    T.ok(f"{k.upper()}: gestartet", T.wait(lambda: T._health(k), 40))


F2.start = T.start = start
# --knoten: Anruf-Einstellungen schon beim Anlegen (vor dem Verbinden - die Faehigkeiten horn/horn_dm gehen mit dem hello an
# die Gegenstelle): A calls.horn + horn_all (fuehrt 1:1 ueber den eigenen Knoten), B calls.horn ohne horn_all (meldet horn_dm,
# ohne eigenen Knoten - wie im Kopf von tests/browser_fed_horn_dm.js: "calls.horn_all=1 auf A")
CALLS = {"enabled": True, "stun": "", "turn_urls": "", "turn_mode": "rest", "turn_secret": "", "turn_user": "", "turn_password": "",
         "turn_ttl": 43200, "relay_only": False, "ring_s": 45, "horn": True}
if A_.knoten:
    _calls = lambda d: "\nauth.save_setting('calls', " + repr(d).replace("{", "{{").replace("}", "}}") + ")\n"   # noqa: E731
    # B: horn_all ausdruecklich aus (mit Merker horn_all_y, sonst gilt "ab Werk an" - dann verlangte B selbst Knoten, horn_req)
    F2.SETUP_B = T.SETUP.replace("print(json.dumps(True))", _calls(dict(CALLS, horn_all=False, horn_all_y=True)) + "print(json.dumps(True))")
    T.SETUP = T.SETUP.replace("print(json.dumps(True))", _calls(dict(CALLS, horn_all=True)) + "print(json.dumps(True))")
STOP = []
signal.signal(signal.SIGTERM, lambda *_: STOP.append(1))


def main():
    adm_a, _, _, _ = F2.main()
    if A_.knoten:
        ready = T.wait(lambda: (T.sql("a", "SELECT state FROM horn_node WHERE builtin=1 AND removed IS NULL") or [[None]])[0][0] == "ok", 40)
        T.ok("A: eingebauter Knoten bereit", ready)
        # Foederierte Anrufe ueber Knoten brauchen auf jeder Seite einen Knoten oder einen geteilten (saga/fed_horn.py fed_ok):
        # Admin A teilt den eingebauten Knoten mit verbundenen Servern (Admin -> Anrufe, wie im Testlabor)
        nid = T.sql("a", "SELECT id FROM horn_node WHERE builtin=1 AND removed IS NULL")[0][0]
        adm_a.req("POST", "/calls", {"action": "share", "id": str(nid), "csrf": adm_a.csrf("/calls")})
        T.ok("A: Knoten mit verbundenen Servern geteilt", T.sql("a", "SELECT share FROM horn_node WHERE id=?", (nid,))[0][0] in (1, True))
        time.sleep(3)                                  # hello mit den Knoten-Adressen an B (horn.HOOKS share_changed)
    T.ok("B: Adressbuch von A angekommen", T.wait(lambda: T.sql("b", "SELECT count(*) FROM fed_directory")[0][0] >= 1, 90))
    env = {"SAGA_A": T.I["a"]["url"], "SAGA_B": T.I["b"]["url"], "SAGA_USER_A": "fa-anna", "SAGA_PASS_A": PW, "SAGA_USER_B": "fb-bert",
           "SAGA_PASS_B": PW, "SAGA_PASS": PW, "SAGA_FIND_B": "Bert Gast", "SAGA_SHARE_B": f"@bert@{T.I['b']['host']}",
           "UMG_FED_OK": "1" if all(T.res) else "0"}
    with open(A_.env + ".tmp", "w") as fh:
        fh.write("".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items()))
    os.rename(A_.env + ".tmp", A_.env)
    print("zwei_stacks: bereit", env["SAGA_A"], env["SAGA_B"], flush=True)


try:
    main()
    while not STOP:
        time.sleep(0.5)
except Exception:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    T.res.append(False)
    if not os.path.exists(A_.env):
        open(A_.env, "w").write("export UMG_FED_OK=0\n")
finally:
    T.stop_all()
    for k in T.I:
        n, txt = T.server_errors(k)
        T.ok(f"{k.upper()}: Serverfehler {n}", n == 0)
        if n:
            print(txt, flush=True)
        try:
            T.pg_admin(f"DROP DATABASE IF EXISTS {T.I[k]['db']} WITH (FORCE)")
        except Exception as ex:  # noqa: BLE001
            print("aufraeumen:", ex)
    print(f"zwei_stacks: {sum(T.res)}/{len(T.res)}", flush=True)
