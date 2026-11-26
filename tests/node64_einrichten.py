"""Bugs-DB 2463: Labor-Node64 fuer tests/node64_labor.py einrichten (API-Key, Module) - Hilfsskript, keine Testreihe.

Node64 selbst startet dieses Skript nicht. Container (nur Test, nur an 127.0.0.1; rootful Podman mit SELinux braucht
--user 0:0 und --security-opt label=disable, sonst darf Node64 /data nicht beschreiben):
  podman run -d --name n64 --user 0:0 --security-opt label=disable \
      -p 127.0.0.1:<N64_UI>:8080 -p 127.0.0.1:<A3_BASE>:443 -v n64:/data \
      -e NODE64_ADMIN_USER=admin -e NODE64_ADMIN_PASSWORD="$(cat <Passwortdatei>)" ghcr.io/ipv64net/node64:1.7.13
  (fuer N64_LABELS=1 der Reihe zusaetzlich die Docker-API: systemctl start podman.socket und
   -v /run/podman/podman.sock:/var/run/docker.sock:ro; danach podman.socket wieder stoppen)
Oberflaeche und API liegen auf Port 8080 im Container (HTTP und HTTPS auf demselben Port, selbst signiert).

Ablauf (so verlangt es Node64 1.7.13): Anmeldeseite GET /login -> Cookie node64_csrf + Formularfeld _csrf; POST /login
(Formular username/password/_csrf/next, Kopf Origin) -> Sitzung; POST /api/keys {"name": ...} mit Sitzung, Kopf
X-CSRF-Token = Wert des Cookies node64_csrf und Kopf Origin = Adresse der Oberflaeche -> der Key steht nur in dieser
Antwort (Feld key; hint ist gekuerzt); danach mit "Authorization: Bearer <key>" POST /api/modules/cdn64/enable (PROXY64)
und /api/modules/healthchecks/enable. Ein schon vorhandener, gueltiger Key in N64_KEY_FILE wird weiter benutzt.

Umgebung: N64_URL (Standard https://127.0.0.1:18640), N64_ADMIN_USER (Standard admin), N64_ADMIN_PW_FILE (Datei mit dem
Passwort; oder N64_ADMIN_PASSWORD), N64_KEY_FILE (Pflicht: hierhin kommt der Key, Rechte 600). Gibt den Key nie aus.
  N64_URL=https://127.0.0.1:8939 N64_ADMIN_PW_FILE=... N64_KEY_FILE=... python tests/node64_einrichten.py"""
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

N64 = os.environ.get("N64_URL", "https://127.0.0.1:18640").rstrip("/")
CTX = ssl.create_default_context()
CTX.check_hostname, CTX.verify_mode = False, ssl.CERT_NONE        # nur Pruefstand (selbst signiert)


def fail(msg):
    print("FEHLER:", msg)
    sys.exit(1)


def api(method, path, key=None, body=None, hdr=None, opener=None):
    h = {"Content-Type": "application/json", **(hdr or {})}
    if key:
        h["Authorization"] = "Bearer " + key
    rq = urllib.request.Request(N64 + "/api" + path, data=None if body is None else json.dumps(body).encode(), method=method, headers=h)
    try:
        r = (opener.open if opener else lambda q: urllib.request.urlopen(q, context=CTX))(rq)
        d = r.read().decode()
        return r.status, (json.loads(d) if d.strip().startswith(("{", "[")) else d)
    except urllib.error.HTTPError as e:
        d = e.read().decode()
        return e.code, (json.loads(d) if d.strip().startswith(("{", "[")) else d[:300])


def new_key(user, pw):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX), urllib.request.HTTPCookieProcessor(cj))
    page = op.open(N64 + "/login").read().decode()
    m = re.search(r'name="_csrf" value="([^"]+)"', page)
    if not m:
        fail("Anmeldeseite ohne _csrf - ist N64_URL die Oberflaeche von Node64?")
    form = urllib.parse.urlencode({"_csrf": m.group(1), "username": user, "password": pw, "next": "/"}).encode()
    try:
        r = op.open(urllib.request.Request(N64 + "/login", data=form, headers={"Origin": N64}))
    except urllib.error.HTTPError as e:            # 1.7.13: falsches Passwort -> 401
        r = e
    if r.status != 200 or urllib.parse.urlparse(r.geturl()).path.startswith("/login"):
        fail("Anmeldung abgelehnt (Benutzer/Passwort wie NODE64_ADMIN_USER/NODE64_ADMIN_PASSWORD beim ersten Start?)")
    csrf = next((c.value for c in cj if c.name == "node64_csrf"), "")
    s, d = api("POST", "/keys", body={"name": "saga-labor"}, hdr={"X-CSRF-Token": csrf, "Origin": N64}, opener=op)
    if s not in (200, 201) or not isinstance(d, dict) or not d.get("key"):
        fail(f"POST /api/keys: {s} {d if not isinstance(d, dict) else {k: v for k, v in d.items() if k != 'key'}}")
    print(f"API-Key angelegt: id {d.get('id')}, hint {d.get('hint')}")
    return d["key"]


def main():
    kf = os.environ.get("N64_KEY_FILE")
    if not kf:
        fail("N64_KEY_FILE setzen (Datei fuer den API-Key)")
    key = open(kf).read().strip() if os.path.exists(kf) else ""
    if key and api("GET", "/status", key)[0] == 200:
        print("vorhandener API-Key aus N64_KEY_FILE gilt noch")
    else:
        pw = os.environ.get("N64_ADMIN_PASSWORD") or (open(os.environ["N64_ADMIN_PW_FILE"]).read().strip() if os.environ.get("N64_ADMIN_PW_FILE") else "")
        if not pw:
            fail("N64_ADMIN_PW_FILE oder N64_ADMIN_PASSWORD setzen")
        key = new_key(os.environ.get("N64_ADMIN_USER", "admin"), pw)
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key + "\n")
    for m in ("cdn64", "healthchecks"):
        s, _ = api("POST", f"/modules/{m}/enable", key)
        print(f"Modul {m} einschalten: {s}")
    s, mods = api("GET", "/modules", key)
    run = {x.get("name"): bool(x.get("enabled") and x.get("running")) for x in (mods if isinstance(mods, list) else [])}
    print("Module:", {m: run.get(m) for m in ("cdn64", "healthchecks")})
    if not (run.get("cdn64") and run.get("healthchecks")):
        fail("Module cdn64/healthchecks laufen nicht")
    s, st = api("GET", "/status", key)
    print("Node64 bereit:", s, (st or {}).get("version") if isinstance(st, dict) else "")


if __name__ == "__main__":
    main()
