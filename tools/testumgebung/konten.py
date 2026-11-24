"""Testumgebung: Schema, Testkonten und Test-Einstellungen in der DB des Laufs; schreibt export-Zeilen nach <datei>.
hk-test-a/b ohne Passwort (wie tools/stack-remote.sh), um-anna/um-ben/um-cora/um-dora mit Passwort UMG_PW.
UMG_ALLOWED = "Freigegebene interne Hosts" (Attrappen). UMG_MESH_MAX=3 senkt die
Mesh-Grenze (z. B. browser_meeting_gruppe ueber den Knoten: vier Personen > 3 -> Knoten statt Mesh-Rueckfall). Aufruf: PYTHONPATH=. python tools/testumgebung/konten.py <datei>"""
import base64, json, os, shlex, sys
from itsdangerous import TimestampSigner
import auth, db
from saga import core

db.wait_ready(); db.init_schema()
pw = os.environ["UMG_PW"]
for u in ("hk-test-a", "hk-test-b"):
    auth.get_user(u) or auth.create_user(u, None, u)
NAMES = (("um-anna", "Anna Ahorn"), ("um-ben", "Ben Birke"), ("um-cora", "Cora Celle"), ("um-dora", "Dora Distel"))
for u, n in NAMES:
    auth.get_user(u) or auth.create_user(u, pw, n)
# 2.6 (Bugs-DB 2031): Testkonten haben die Frage "Moechtest du eine kurze Einfuehrung?" schon beantwortet - sonst liegt der
# Dialog in Browser-Reihen ueber der Oberflaeche (eigene Reihe dafuer: tests/browser_leere_zustaende.js mit frischem Konto)
with db.connect() as c, c:
    for u in ("hk-test-a", "hk-test-b") + tuple(u for u, _ in NAMES):
        c.execute("INSERT INTO user_kv (uid, k, v) VALUES (?, 'first_steps', ?) ON CONFLICT (uid, k) DO NOTHING", (u, json.dumps({"state": "no"})))
auth.save_setting("allowed_hosts", os.environ.get("UMG_ALLOWED", "127.0.0.1:5232, 127.0.0.1:18283, 127.0.0.1:18284"))
if os.environ.get("UMG_HORN") == "1":          # Knoten-Laeufe: alle Anrufe ueber den eingebauten Knoten (calls.horn + horn_all)
    c = dict(auth.settings(fresh=True).get("calls") or {})
    c.update(enabled=True, horn=True, horn_all=True)
    auth.save_setting("calls", c)
if os.environ.get("UMG_MESH_MAX"):          # niedrigere Mesh-Grenze (calls.endpoints_video/audio): Gruppen ueber der Grenze laufen ueber
    c = dict(auth.settings(fresh=True).get("calls") or {})   # den Knoten, auch wenn Geraete-caps noch fehlen (_old_members)
    n = int(os.environ["UMG_MESH_MAX"])
    c.update(endpoints_video=n, endpoints_audio=n)
    auth.save_setting("calls", c)


def ck(u):
    i = auth.user_info(u, fresh=True)
    d = {"uid": u, "name": i["name"], "pwc": i["epoch"] or 0}
    return TimestampSigner(auth.SECRET).sign(base64.b64encode(json.dumps(d).encode())).decode()


out = {"CK": ck("hk-test-a"), "CK2": ck("hk-test-b"), "HANDLE1": core.local_ref("hk-test-a"), "HANDLE2": core.local_ref("hk-test-b"),
       "SAGA_USER": "um-anna", "SAGA_USER2": "um-ben", "SAGA_USER3": "um-cora", "SAGA_USER4": "um-dora",
       "SAGA_NAME2": NAMES[1][1], "SAGA_NAME3": NAMES[2][1], "SAGA_USERS": ",".join(u for u, _ in NAMES)}
for k in ("SAGA_PASS", "SAGA_PASS2", "SAGA_PASS3", "SAGA_PASS4"):
    out[k] = pw
with open(sys.argv[1], "w") as fh:     # Datei statt stdout: db/auth schreiben eigene Hinweise nach stdout
    fh.write("".join(f"export {k}={shlex.quote(v)}\n" for k, v in out.items()))
