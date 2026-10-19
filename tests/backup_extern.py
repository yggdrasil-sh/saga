"""Verschluesselte Backups auf WebDAV/Nextcloud (2.3, saga/backups.py) von Anfang bis Ende gegen ein nachgemachtes
Ziel (tests/fakedav.py: Nextcloud mit Login Flow v2/Chunking v2 und schlichtes WebDAV).

Laeuft in einer eigenen Wegwerf-Datenbank und einem eigenen STATE_DIR (die echte Test-Datenbank bleibt unberuehrt, kein
Zeitplan eines anderen Prozesses sieht die Einstellungen). Prueft: Verbinden (Login Flow v2, App-Passwort verschluesselt,
Trennen widerruft), Schluessel (erzeugen, pruefen, mehrere Empfaenger), Lauf (Inhalte mit beiden Testschluesseln
entschluesselt, nichts im Klartext im Ziel), Manifest + Pruefsummen, Chunking v2, Fehler (Ziel weg, voll, kaputte
Daten) -> Benachrichtigung der Admins + Webhook, 48 h ohne Erfolg, GFS mit Zeitsimulation, Zeitplan, schlichtes
WebDAV, Kommandozeile (restore --schluessel in einen frischen "Stack", backup-pruefen), SAGA_RESTORE_TEST.
Am Ende: Admin-Bereich (/backups) ueber HTTP auf der Test-Instanz (HK_TEST_ADMIN) - Einstellungen danach wie vorher."""
import base64
import hashlib
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
TMPROOT = tempfile.mkdtemp(prefix="bkx-", dir=os.environ.get("STATE_DIR") or None)
STATE = os.path.join(TMPROOT, "state")
os.environ["STATE_DIR"] = STATE                      # vor dem Import von db/saga (BACKUP_DIR, FILES_DIR ...)
os.environ["SAGA_SKIP_BACKUP"] = "1"
for d in ("files/ab", "files/thumbs", "chat", "svc-icons"):
    os.makedirs(os.path.join(STATE, d), exist_ok=True)

import psycopg                                        # noqa: E402
from psycopg.conninfo import make_conninfo           # noqa: E402
import db                                             # noqa: E402
import fakedav                                        # noqa: E402
import fakes3                                         # noqa: E402

res = []


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)


REAL = db.DATABASE_URL
NAME = "saga_bkx_" + secrets.token_hex(4)
URL = make_conninfo(REAL, dbname=NAME)
FRESH = "saga_bkr_" + secrets.token_hex(4)
FRESH_URL = make_conninfo(REAL, dbname=FRESH)
PB = int(os.environ.get("BKX_PORTS", "18301"))          # erster von 5 Ports (parallele Laeufe: anderen Bereich waehlen)
NCP, DVP, RTP, RTA, S3P = PB, PB + 1, PB + 2, PB + 3, PB + 4
NC = fakedav.start(NCP, True)
DV = fakedav.start(DVP, False)
NCB, DVB = f"http://127.0.0.1:{NCP}", f"http://127.0.0.1:{DVP}"
MARK = "BKX-KLARTEXT-" + secrets.token_hex(6)


def q(sql, args=(), url=None):
    with psycopg.connect(url or URL, autocommit=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else None


def stats(base=NCB):
    return json.loads(urllib.request.urlopen(base + "/_stats", timeout=10).read())


def ctl(base=NCB, **kw):
    urllib.request.urlopen(base + "/_ctl?" + urllib.parse.urlencode(kw), timeout=10).read()


def fake_file(path, base=NCB):
    with urllib.request.urlopen(base + "/_get?" + urllib.parse.urlencode({"f": path}), timeout=30) as r:
        return r.read()


q(f"CREATE DATABASE {NAME}", url=REAL)
db.DATABASE_URL = URL
srv_proc = None
try:
    db.init_schema()
    import auth
    from saga import backups, notify
    from saga.core import SECRET
    ok("Wegwerf-Datenbank, Tabelle backup_run vorhanden", q("SELECT to_regclass('public.backup_run')")[0][0] is not None)
    ok("age im Image", backups.age_ok())
    with psycopg.connect(URL, autocommit=True) as lk:
        free_before = backups.running()
        lk.execute("SELECT pg_advisory_lock(%s)", (backups.LOCK_ID,))
        held = backups.running()
        took, _ = backups.run("test")                      # gesperrt -> "busy", ohne Verlaufszeile
        lk.execute("SELECT pg_advisory_unlock(%s)", (backups.LOCK_ID,))
    ok("running() erkennt die Sperre, ohne sie selbst zu nehmen", free_before is False and held is True and took is False
       and backups.running() is False and q("SELECT count(*) FROM backup_run")[0][0] == 0)
    auth.create_user("bkx-adm", "Bkx-Pass-12345", "Backup-Admin", is_admin=True)
    auth.create_user("bkx-user", "Bkx-Pass-12345", "Kein Admin")
    q("INSERT INTO note (uid, title, body, created, updated) VALUES ('bkx-user', %s, %s, 1, 1)", ("Probe", MARK))
    with open(os.path.join(STATE, "files/ab/bild.bin"), "wb") as f:
        f.write(os.urandom(3000) + MARK.encode())
    with open(os.path.join(STATE, "files/thumbs/t.bin"), "wb") as f:
        f.write(b"thumb")
    with open(os.path.join(STATE, "chat/c.bin"), "wb") as f:
        f.write(b"chat-anhang")
    with open(os.path.join(STATE, "svc-icons/i.png"), "wb") as f:
        f.write(b"icon")

    # --- GFS und Zeitplan (reine Logik, Zeitsimulation) ---
    from datetime import datetime, timedelta
    t0 = datetime(2026, 10, 19, 3, 20, tzinfo=backups.TZ)
    names = [(t0 - timedelta(days=i)).strftime("saga-%Y%m%d-%H%M%S") for i in range(400)]
    keep = backups.gfs_keep(names, 7, 4, 6)
    days = sorted(names, reverse=True)[:7]
    ok(f"GFS 7/4/6 ueber 400 Tage: {len(keep)} bleiben, die letzten 7 Tage dabei", set(days) <= keep and 7 < len(keep) <= 17)
    months = {(backups.run_time(n).year, backups.run_time(n).month) for n in keep}
    ok("GFS: 6 Monate abgedeckt, nichts aelter als 6 Monate", len(months) == 6
       and min(backups.run_time(n) for n in keep) > t0 - timedelta(days=200))
    two = [t0.strftime("saga-%Y%m%d-%H%M%S"), (t0 + timedelta(hours=5)).strftime("saga-%Y%m%d-%H%M%S")]
    ok("GFS: zwei Laeufe am selben Tag -> der neuere bleibt", backups.gfs_keep(two, 1, 0, 0) == {two[1]})
    cfg_t = {"enabled": True, "mode": "webdav", "url": "http://x", "recipients": ["age1" + "q" * 58], "time": "03:20",
             "since": (t0 - timedelta(days=3)).timestamp()}
    now = (t0 + timedelta(minutes=5)).timestamp()
    ok("Zeitplan: faellig kurz nach der Uhrzeit", backups.due(cfg_t, {}, now) == t0.timestamp())
    ok("Zeitplan: nicht zweimal fuer denselben Zeitpunkt", backups.due(cfg_t, {"last_attempt": t0.timestamp() + 1}, now) is None)
    ok("Zeitplan: vor der Uhrzeit nicht (Vortag schon gelaufen)",
       backups.due(cfg_t, {"last_attempt": (t0 - timedelta(days=1)).timestamp() + 1}, (t0 - timedelta(minutes=5)).timestamp()) is None)
    ok("Zeitplan: erst nach dem Einschalten", backups.due(dict(cfg_t, since=now - 60), {}, now) is None)
    nx = backups.next_run(dict(cfg_t, since=now - 60), {}, now)
    ok("Zeitplan: naechster Lauf morgen 03:20", abs(nx - (t0 + timedelta(days=1)).timestamp()) < 2)
    ok("Zeitplan aus -> kein Lauf", backups.due(dict(cfg_t, enabled=False), {}, now) is None)

    # --- Schluessel ---
    priv1, pub1 = backups.keygen()
    priv2, pub2 = backups.keygen()
    k1, k2 = os.path.join(TMPROOT, "k1.key"), os.path.join(TMPROOT, "k2.key")
    for p, v in ((k1, priv1), (k2, priv2)):
        with open(p, "w") as f:
            f.write(v)
    ok("Schluesselpaar erzeugt", pub1.startswith("age1") and "AGE-SECRET-KEY-1" in priv1 and pub1 != pub2)
    ok("Oeffentlichen Schluessel pruefen", backups.norm_recipient("  " + pub1 + " ") == pub1)
    ok("Privater Schluessel / Unsinn abgelehnt", backups.norm_recipient(priv1.strip().splitlines()[-1]) is None
       and backups.norm_recipient("age1nichtgueltig") is None)

    # --- Nextcloud: Login Flow v2 ---
    login = backups.flow_start(NCB, "Saga-Test/Backups")
    ok("Login Flow v2: Login-Link", login.startswith(NCB + "/login/v2/flow/") and backups.flow_state()["state"] == "wait")
    time.sleep(2.5)
    ok("Ohne Anmeldung: noch wartend", backups.flow_state()["state"] == "wait")
    urllib.request.urlopen(NCB + "/_grant").read()
    for _ in range(30):
        if backups.flow_state()["state"] != "wait":
            break
        time.sleep(0.5)
    cfg = backups.config()
    ok(f"Verbunden: Anmeldename + App-Passwort, Benutzer-ID fuer WebDAV ({backups.flow_state()})",
       backups.flow_state()["state"] == "ok" and not backups.flow_state()["code"] and cfg["mode"] == "nextcloud"
       and cfg["user"] == fakedav.LOGIN and cfg["dav_user"] == fakedav.UID and cfg["flow"] is True and cfg["password"].startswith("app-"))
    raw = q("SELECT v FROM setting WHERE k='backup'")[0][0]
    ok("App-Passwort verschluesselt gespeichert", cfg["password"] not in raw and '"password": "enc:' in raw)
    ok("Ordner im Ziel angelegt", f"/remote.php/dav/files/{fakedav.UID}/Saga-Test/Backups" in stats()["dirs"])
    app_pw = cfg["password"]
    from cryptography.fernet import Fernet
    alien = "enc:" + Fernet(Fernet.generate_key()).encrypt(b"https://hook.example.org/x").decode()
    v_ = json.loads(raw)
    v_["webhook"] = alien
    q("UPDATE setting SET v=%s WHERE k='backup'", (json.dumps(v_),))
    auth._settings_cache["v"] = None
    backups.save_config(backups.config())
    raw2 = json.loads(q("SELECT v FROM setting WHERE k='backup'")[0][0])
    ok("Nicht entschluesselbares Geheimnis (anderer SECRET_KEY) wird beim Speichern nicht geleert",
       raw2["webhook"] == alien and backups.config()["password"] == app_pw)
    raw2["webhook"] = ""
    q("UPDATE setting SET v=%s WHERE k='backup'", (json.dumps(raw2),))
    auth._settings_cache["v"] = None

    # --- Lauf ohne Schluessel -> Fehler ---
    good, err = backups.run("test")
    ok("Ohne Schluessel kein Backup", not good and err.code == "nokey")
    n_notif = q("SELECT count(*) FROM notification WHERE uid='bkx-adm' AND kind='backup.failed'")[0][0]
    ok("Fehler -> Benachrichtigung an Admins (nicht an andere)", n_notif == 1
       and q("SELECT count(*) FROM notification WHERE uid='bkx-user'")[0][0] == 0)

    # --- Lauf mit zwei Empfaengern, Chunking v2 ---
    cfg["recipients"] = [pub1, pub2]
    backups.save_config(cfg)
    backups.CHUNK_MIN, backups.CHUNK_SIZE = 1024 * 1024, 5 * 1024 * 1024
    with open(os.path.join(STATE, "files/ab/gross.bin"), "wb") as f:
        f.write(os.urandom(12 * 1024 * 1024))
    good, out = backups.run("test")
    ok(f"Lauf erfolgreich ({out if not good else out['name']})", good)
    root = f"/remote.php/dav/files/{fakedav.UID}/Saga-Test/Backups/{out['name']}" if good else ""
    st = stats()
    ok(f"Chunking v2 fuer grosse Datei (Teile {st['chunks']}, MOVE {st['moves']}, Teil < 5 MB nur am Ende)",
       good and out["chunked"] >= 1 and st["moves"] >= 1 and st["chunks"] >= 3 and st["uploads"] == 0)
    files = {k.rsplit("/", 1)[-1]: k for k in st["files"] if root and k.startswith(root + "/")}
    ok(f"Lauf-Ordner vollstaendig: {sorted(files)}", set(files) == {"db.dump.age", "dateien.tar.age", "geheimnisse.env.age",
                                                                   "manifest.json.age", "manifest.json"})
    man = json.loads(fake_file(files.get("manifest.json", "")) or b"{}")
    sums_ok = all(hashlib.sha256(fake_file(files[n])).hexdigest() == v["sha256"] and len(fake_file(files[n])) == v["size"]
                  for n, v in man.get("files", {}).items())
    ok("Manifest: Groessen und SHA-256 der verschluesselten Dateien stimmen", sums_ok and len(man.get("files", {})) == 4)
    mtxt = json.dumps(man)
    ok("Manifest ohne Hostnamen/Personendaten, mit Empfaenger-Fingerabdruecken", "127.0.0.1" not in mtxt and "bkx" not in mtxt
       and man.get("recipients") == [backups.fingerprint(pub1), backups.fingerprint(pub2)] and man.get("schema") == db.SCHEMA_VERSION)
    blob = b"".join(fake_file(p) for p in files.values())
    ok("Nichts im Klartext im Ziel (Seiteninhalt, Datei, SECRET_KEY)", MARK.encode() not in blob and SECRET.encode() not in blob
       and b"PGDMP" not in blob)
    ok("Verlauf: Lauf ok mit Groesse", q("SELECT ok, size > 1000, name FROM backup_run ORDER BY id DESC LIMIT 1")[0] == (1, True, out["name"]))

    # Entschluesseln mit BEIDEN Testschluesseln (wie ohne Saga: age -d -i)
    dl = os.path.join(TMPROOT, "dl")
    os.makedirs(dl)
    for n, p in files.items():
        with open(os.path.join(dl, n), "wb") as f:
            f.write(fake_file(p))
    for kf in (k1, k2):
        r = subprocess.run(["age", "-d", "-i", kf, os.path.join(dl, "geheimnisse.env.age")], capture_output=True)
        ok(f"geheimnisse.env mit Schluessel {os.path.basename(kf)} lesbar (SECRET_KEY)", r.returncode == 0
           and f"SECRET_KEY={SECRET}".encode() in r.stdout)
    subprocess.run(["age", "-d", "-i", k2, "-o", os.path.join(dl, "db.dump"), os.path.join(dl, "db.dump.age")], check=True)
    lr = subprocess.run(["pg_restore", "--list", os.path.join(dl, "db.dump")], capture_output=True)
    ok("db.dump entschluesselt, pg_restore --list liest es", lr.returncode == 0 and b"TABLE DATA public note" in lr.stdout)
    tp = subprocess.run(["age", "-d", "-i", k1, os.path.join(dl, "dateien.tar.age")], capture_output=True, check=True)
    with tarfile.open(fileobj=__import__("io").BytesIO(tp.stdout)) as tar:
        tn = set(tar.getnames())
    ok(f"dateien.tar: files/chat/svc-icons, ohne thumbs ({len(tn)} Eintraege)", {"files/ab/bild.bin", "files/ab/gross.bin", "chat/c.bin",
                                                                                "svc-icons/i.png"} <= tn and not any("thumbs" in x for x in tn))
    inner = json.loads(subprocess.run(["age", "-d", "-i", k1, os.path.join(dl, "manifest.json.age")], capture_output=True, check=True).stdout)
    ok("manifest.json.age: SHA-256 der Klartexte", inner["plain"]["dateien.tar.age"]["sha256"] == hashlib.sha256(tp.stdout).hexdigest()
       and inner["plain"]["db.dump.age"]["sha256"] == hashlib.sha256(open(os.path.join(dl, "db.dump"), "rb").read()).hexdigest())
    os.remove(os.path.join(STATE, "files/ab/gross.bin"))
    good_name = out["name"]

    # --- Fehlerfaelle -> Benachrichtigung + Webhook ---
    cfg = backups.config()
    cfg["webhook"] = NCB + "/_hook"
    backups.save_config(cfg)
    ok("Webhook verschluesselt gespeichert", "_hook" not in q("SELECT v FROM setting WHERE k='backup'")[0][0])
    time.sleep(1.1)
    ctl(down=1)
    good, err = backups.run("test")
    ctl(down=0)
    ok(f"Ziel weg -> Fehler ({getattr(err, 'code', err)} {getattr(err, 'p', '')})", not good and err.code == "http" and err.p.get("status") == 503)
    for _ in range(20):
        if stats()["hooks"]:
            break
        time.sleep(0.25)
    hooks = stats()["hooks"]
    ok(f"Webhook gerufen ({hooks[-1] if hooks else '-'})", hooks and hooks[-1]["title"] and "503" in hooks[-1]["body"])
    with db.connect() as c:
        rid = c.execute("SELECT id FROM notification WHERE uid='bkx-adm' AND kind='backup.failed' ORDER BY id DESC LIMIT 1").fetchone()[0]
        it = notify.item(c, "bkx-adm", rid)
    ok(f"Glocke: Text in der Sprache des Admins ({it['text']})", it["access"] and "503" in it["text"] and it["title"] == "Backups")
    from saga import push
    with db.connect() as c:
        msg = push.message(c, "bkx-adm", "backup.failed", ref=rid, sub_kind="ntfy", has_keys=False)
    ok(f"Push-Text ohne Inhalt, Link relativ ({msg})", msg and msg["body"] == "Backup failed" and msg["url"] == "/")
    time.sleep(1.1)
    ctl(quota=stats()["used"] + 20000)
    good, err = backups.run("test")
    ctl(quota=-3)
    ok(f"Ziel voll (quota-available-bytes) -> Fehler vor dem Hochladen ({getattr(err, 'code', err)})", not good and err.code == "full")
    time.sleep(1.1)
    ctl(corrupt=1)
    good, err = backups.run("test")
    ctl(corrupt=0)
    ok(f"Pruefsumme nach dem Upload erkennt kaputte Daten ({getattr(err, 'code', err)})", not good and err.code == "checksum")
    ok("Jeder Fehler gemeldet (4 Benachrichtigungen)", q("SELECT count(*) FROM notification WHERE uid='bkx-adm' AND kind='backup.failed'")[0][0] == 4)
    dav = backups.Dav(backups.config())
    runs = backups.list_runs(dav)
    ok("Abgebrochener Lauf bleibt als unvollstaendig stehen (kein manifest.json)", any(not r["complete"] for r in runs)
       and any(r["complete"] and r["name"] == good_name for r in runs))

    # --- 48 h ohne Erfolg ---
    cfg = backups.config()
    cfg.update(enabled=True, since=time.time() - 3 * 86400)
    backups.save_config(cfg)
    ok("Kein Hinweis, solange der letzte Erfolg frisch ist", backups.check_stale() is False)
    later = time.time() + 49 * 3600
    ok("49 h spaeter: Hinweis an Admins", backups.check_stale(later) is True and q(
        "SELECT count(*) FROM notification WHERE uid='bkx-adm' AND kind='backup.failed' AND data LIKE %s", ("%stale%",))[0][0] == 1)
    ok("... hoechstens einmal am Tag", backups.check_stale(later + 3600) is False)
    cfg.update(enabled=False)
    backups.save_config(cfg)

    # --- Geheimnisse abschaltbar ---
    cfg = backups.config()
    cfg.update(secrets=False, webhook="")
    backups.save_config(cfg)
    time.sleep(1.1)
    good, out2 = backups.run("test")
    names2 = [k.rsplit("/", 1)[-1] for k in stats()["files"] if good and f"/{out2['name']}/" in k]
    ok("Ohne Geheimnisse: kein geheimnisse.env.age", good and "geheimnisse.env.age" not in names2 and "db.dump.age" in names2)
    cfg.update(secrets=True)
    backups.save_config(cfg)

    # --- GFS im Ziel (Zeitsimulation): kuenstliche Laeufe ueber 60 Tage ---
    for i in range(60):
        n = (t0 - timedelta(days=40 + i)).strftime("saga-%Y%m%d-%H%M%S")
        dav.mkcol(dav.url(n))
        dav.req("PUT", dav.url(n, "manifest.json"), content=b"{}")
    old_inc = (t0 - timedelta(days=30)).strftime("saga-%Y%m%d-%H%M%S")
    dav.mkcol(dav.url(old_inc))
    dav.mkcol(dav.url("Fotos"))
    cfg = dict(backups.config(), keep_d=3, keep_w=2, keep_m=3)
    before = [r["name"] for r in backups.list_runs(dav) if r["complete"]]
    gone = backups.prune(dav, cfg, keep={good_name}, now=time.time())
    after = backups.list_runs(dav)
    an = {r["name"] for r in after}
    want = backups.gfs_keep(before, 3, 2, 3) | {good_name} | {n for n in before if time.time() - backups.run_time(n).timestamp() < 86400}
    ok(f"GFS im Ziel: {len(before)} -> {len([r for r in after if r['complete']])} vollstaendige", {r["name"] for r in after if r["complete"]} == want)
    ok("Unvollstaendig + alt -> geloescht, fremder Ordner bleibt", old_inc in gone and old_inc not in an
       and f"/remote.php/dav/files/{fakedav.UID}/Saga-Test/Backups/Fotos" in stats()["dirs"])
    dav.close()

    # --- Kommandozeile: backup-pruefen (Wegwerf-Datenbank) ---
    env = dict(os.environ, DATABASE_URL=URL, STATE_DIR=STATE, PYTHONPATH=APP)
    r = subprocess.run([sys.executable, "run.py", "backup-pruefen", "--schluessel", k2, good_name], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=600)
    ok(f"run.py backup-pruefen ({r.returncode}) {(r.stdout + r.stderr).strip()[-200:]}", r.returncode == 0
       and "Testwiederherstellung in Ordnung" in r.stdout and "Seiten" in r.stdout)
    ok("Wegwerf-Datenbank wieder weg, Zeitpunkt gemerkt", q("SELECT count(*) FROM pg_database WHERE datname LIKE %s", ("saga_pruefung_%",),
                                                             url=REAL)[0][0] == 0 and backups.state().get("last_check"))
    r = subprocess.run([sys.executable, "run.py", "backup-pruefen", "--schluessel", os.path.join(TMPROOT, "fehlt.key")], cwd=APP, env=env,
                       capture_output=True, text=True, timeout=120)
    ok("backup-pruefen ohne Schluesseldatei -> Fehler", r.returncode != 0)

    # --- Kommandozeile: restore in einen frischen "Stack" (eigene Datenbank + eigenes STATE_DIR, Ziel per Umgebung) ---
    q(f"CREATE DATABASE {FRESH}", url=REAL)
    fresh_state = os.path.join(TMPROOT, "frisch")
    os.makedirs(fresh_state)
    fenv = dict(os.environ, DATABASE_URL=FRESH_URL, STATE_DIR=fresh_state, PYTHONPATH=APP,
                SAGA_BACKUP_URL=f"{NCB}/remote.php/dav/files/{fakedav.UID}/Saga-Test/Backups", SAGA_BACKUP_USER=fakedav.LOGIN,
                SAGA_BACKUP_PASSWORD=app_pw)
    r = subprocess.run([sys.executable, "run.py", "backup-holen", "--liste"], cwd=APP, env=fenv, capture_output=True, text=True, timeout=120)
    ok(f"run.py backup-holen --liste (frischer Stack, Ziel per SAGA_BACKUP_URL) {r.stderr.strip()[-120:]}", r.returncode == 0 and good_name in r.stdout)
    r = subprocess.run([sys.executable, "run.py", "restore", "--schluessel", k1, good_name], cwd=APP, env=fenv,
                       capture_output=True, text=True, timeout=900)
    ok(f"run.py restore --schluessel ({r.returncode}) {(r.stdout + r.stderr).strip()[-300:]}", r.returncode == 0)
    ok("Daten wieder da (Seite mit Markierung, Konten)", q("SELECT count(*) FROM note WHERE body = %s", (MARK,), url=FRESH_URL)[0][0] == 1
       and q("SELECT count(*) FROM users WHERE username IN ('bkx-adm', 'bkx-user')", url=FRESH_URL)[0][0] == 2)
    same = all(open(os.path.join(STATE, p), "rb").read() == open(os.path.join(fresh_state, p), "rb").read()
               for p in ("files/ab/bild.bin", "chat/c.bin", "svc-icons/i.png"))
    ok("Dateien wieder da (files, chat, svc-icons), ohne thumbs", same and not os.path.exists(os.path.join(fresh_state, "files/thumbs/t.bin")))
    sec = os.path.join(fresh_state, "backups", f"{good_name}-geheimnisse.env")
    ok("Geheimnisse wieder da (Datei 0600 mit SECRET_KEY)", os.path.isfile(sec) and oct(os.stat(sec).st_mode & 0o777) == "0o600"
       and f"SECRET_KEY={SECRET}" in open(sec).read())
    ok("Zurueckgespielte Einstellungen lesbar (gleicher SECRET_KEY)", "enc:" in q("SELECT v FROM setting WHERE k='backup'", url=FRESH_URL)[0][0])
    r = subprocess.run([sys.executable, "run.py", "restore", "--schluessel", k1, "saga-19990101-000000"], cwd=APP, env=fenv,
                       capture_output=True, text=True, timeout=120)
    ok("restore mit unbekanntem Lauf -> Fehler, nichts geaendert", r.returncode != 0 and q("SELECT count(*) FROM note WHERE body = %s", (MARK,), url=FRESH_URL)[0][0] == 1)
    r = subprocess.run([sys.executable, "run.py", "restore", "--schluessel", k1, os.path.join(fresh_state, "backups", "extern", good_name)],
                       cwd=APP, env=dict(fenv, SAGA_BACKUP_URL=""), capture_output=True, text=True, timeout=900)
    ok(f"restore aus lokalem Lauf-Ordner (ohne Ziel) ({r.returncode})", r.returncode == 0)

    # --- Boesartiges Ziel: jeder kann mit dem oeffentlichen Schluessel verschluesseln, manifest.json ist Klartext ---
    import io
    evil = os.path.join(TMPROOT, "boese", "saga-20260101-000000")
    os.makedirs(evil)
    shutil.copy(os.path.join(dl, "db.dump.age"), os.path.join(evil, "db.dump.age"))
    tb = io.BytesIO()
    with tarfile.open(fileobj=tb, mode="w") as tar:
        def _add(name, data=b"x", **kw):
            ti = tarfile.TarInfo(name)
            for k, v in kw.items():
                setattr(ti, k, v)
            ti.size = len(data) if ti.type == tarfile.REGTYPE else 0
            tar.addfile(ti, io.BytesIO(data) if ti.type == tarfile.REGTYPE else None)
        _add("files/../../ausbruch1.txt")
        _add("/tmp/bkx-ausbruch2.txt")
        _add("files/link", type=tarfile.SYMTYPE, linkname="../../")
        _add("files/link/ausbruch3.txt")
        _add("nicht-erlaubt/x.txt")
        _add("files/ok-boese.txt", b"harmlos")
    subprocess.run(["age", "-e", "-r", pub1, "-o", os.path.join(evil, "dateien.tar.age")], input=tb.getvalue(), check=True)
    ev_files = {p: {"size": os.path.getsize(os.path.join(evil, p)), "sha256": backups.sha256_file(os.path.join(evil, p))}
                for p in ("db.dump.age", "dateien.tar.age")}
    with open(os.path.join(evil, "manifest.json"), "w") as f:
        json.dump({"name": "../../../boese-pfad", "files": ev_files}, f)
    r = subprocess.run([sys.executable, "run.py", "restore", "--schluessel", k1, evil], cwd=APP, env=dict(fenv, SAGA_BACKUP_URL=""),
                       capture_output=True, text=True, timeout=900)
    outside = [p for p in (os.path.join(TMPROOT, "ausbruch1.txt"), "/tmp/bkx-ausbruch2.txt", os.path.join(TMPROOT, "ausbruch3.txt"),
                           os.path.join(fresh_state, "ausbruch1.txt"), os.path.join(fresh_state, "nicht-erlaubt"),
                           ) if os.path.lexists(p)] + [p for p in (os.path.join(fresh_state, "files", "link"),) if os.path.islink(p)]
    ok(f"Boesartiges tar: nichts ausserhalb von files/chat/svc-icons, keine Links ({r.returncode} {outside})",
       r.returncode == 0 and not outside and os.path.isfile(os.path.join(fresh_state, "files", "ok-boese.txt")))
    ok("Name aus manifest.json wird nicht als Pfad benutzt", not any(os.path.exists(os.path.join(d_, "boese-pfad-extern.dump"))
                                                                    for d_ in ("/", TMPROOT, fresh_state, os.path.dirname(TMPROOT)))
       and os.path.isfile(os.path.join(fresh_state, "backups", "saga-20260101-000000-extern.dump")))

    # --- Login Flow: Login-Link nur http(s) ---
    ctl(loginlink="javascript:alert(1)")
    try:
        backups.flow_start(NCB, "x")
        ok("Login-Link javascript: abgelehnt", False)
    except backups.BackupError as ex:
        ok("Login-Link javascript: abgelehnt", ex.code == "http")
    ctl(loginlink="")

    # --- SAGA_RESTORE_TEST: zurueckgespielte Instanz startet ohne ausgehende Verbindungen ---
    with psycopg.connect(FRESH_URL, autocommit=True) as c:
        v = json.loads(c.execute("SELECT v FROM setting WHERE k='backup'").fetchone()[0])
        v.update(enabled=True, since=1, time="00:00")
        c.execute("UPDATE setting SET v=%s WHERE k='backup'", (json.dumps(v),))
        c.execute("INSERT INTO setting (k, v) VALUES ('fed_enabled', 'true') ON CONFLICT (k) DO UPDATE SET v='true'")
        c.execute("DELETE FROM setting WHERE k='backup_state'")
    r = subprocess.run([sys.executable, "-c", "from saga import sharing, push, backups, netguard, ai_web; import auth\n"
                        "import time\nprint(auth.settings(fresh=True)['fed_enabled'], sharing.fed_enabled(), push.send((1,'u','ntfy','https://ntfy.example.org/x','','',''), {})[1],"
                        " bool(backups.due(backups.config(), backups.state(), time.time())), backups.run('cli')[1].code,"
                        f" backups.nc_revoke('{NCB}', 'u', 'p'))\n"
                        "try:\n    netguard.guarded_client(lambda *a: True).get('" + NCB + "/_stats'); print('netguard offen')\n"
                        "except Exception as ex:\n    print('netguard', type(ex).__name__)\n"
                        "try:\n    ai_web.search(['x']); print('suche offen')\n"
                        "except Exception as ex:\n    print('suche', ex)\n"],
                       cwd=APP, env=dict(fenv, SAGA_RESTORE_TEST="1"), capture_output=True, text=True, timeout=60)
    ok(f"SAGA_RESTORE_TEST: Foederation aus, Push schickt nichts, Backup waere faellig, run.py backup-extern und Widerruf gesperrt,"
       f" Kalender/KI/Immich/Avatare (netguard) und Websuche gesperrt ({r.stdout.strip()} {r.stderr.strip()[-200:]})",
       r.stdout.strip().splitlines() == ["True False SAGA_RESTORE_TEST True restore_test False", "netguard ConnectError",
                                         "suche SAGA_RESTORE_TEST"])
    n0 = stats()["requests"]
    log = open(os.path.join(TMPROOT, "srv.log"), "w")
    srv_proc = subprocess.Popen([sys.executable, "run.py"], cwd=APP, stdout=log, stderr=subprocess.STDOUT,
                                env=dict(fenv, SAGA_RESTORE_TEST="1", BACKUP_TICK="1", PORT=str(RTP), ADMIN_PORT=str(RTA),
                                         HOST="127.0.0.1", ADMIN_HOST="127.0.0.1", SAGA_BACKUP_URL=""))
    up = False
    for _ in range(60):
        try:
            up = urllib.request.urlopen(f"http://127.0.0.1:{RTP}/healthz", timeout=2).read() == b"ok"
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    time.sleep(4)
    srv_proc.terminate()
    srv_proc.wait(timeout=20)
    srv_proc = None
    log.close()
    txt = open(os.path.join(TMPROOT, "srv.log")).read()
    extra = stats()["requests"] - n0 - 1                   # ohne diese Abfrage selbst
    ok(f"Zurueckgespielte Instanz startet mit SAGA_RESTORE_TEST=1 (Zeitplan faellig, aber kein Upload; {extra} Anfragen)",
       up and "SAGA_RESTORE_TEST=1" in txt and extra == 0 and "Traceback" not in txt)
    if "Traceback" in txt or not up:
        print(txt[-2000:])

    # --- Trennen widerruft das App-Passwort ---
    cfg = backups.config()
    ok("Widerruf App-Passwort", backups.nc_revoke(cfg["server"], cfg["user"], cfg["password"]) and stats()["revoked"] == 1)
    try:
        backups.test_target(cfg)
        ok("Widerrufenes App-Passwort gilt nicht mehr", False)
    except backups.BackupError as ex:
        ok("Widerrufenes App-Passwort gilt nicht mehr", ex.code == "auth")

    # --- Schlichtes WebDAV (keine Nextcloud): Benutzer/Passwort, PUT ohne Chunking ---
    ctl(DVB, pw="bk:dav-pass-123")
    info = backups.detect(DVB + "/dav")
    ok("Kein Nextcloud erkannt", info["nextcloud"] is False)
    wcfg = dict(backups.config(), mode="webdav", url=DVB + "/dav", server="", user="bk", password="dav-pass-123", flow=False,
                dav_user="", folder="saga")
    free = backups.test_target(wcfg)
    backups.save_config(wcfg)
    time.sleep(1.1)
    good, out3 = backups.run("test")
    ds = stats(DVB)
    ok(f"WebDAV-Lauf ({getattr(out3, 'code', '') if not good else out3['name']}), ohne Chunking", good and ds["chunks"] == 0 and ds["puts"] >= 5
       and free is None)
    try:
        backups.test_target(dict(wcfg, password="falsch"))
        ok("Falsches Passwort -> Anmeldung abgelehnt", False)
    except backups.BackupError as ex:
        ok("Falsches Passwort -> Anmeldung abgelehnt", ex.code == "auth")

    # =================================================================================================================
    # S3 (tests/fakes3.py: Signatur v4 und SHA-256 des Inhalts geprueft wie bei einem echten Anbieter)
    # =================================================================================================================
    E_ = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    AK, AS = "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    v1 = backups.sigv4("GET", "examplebucket.s3.amazonaws.com", "/test.txt", [], {"Range": "bytes=0-9", "x-amz-content-sha256": E_,
                       "x-amz-date": "20130524T000000Z"}, E_, AK, AS, "us-east-1", "20130524T000000Z")
    v2 = backups.sigv4("GET", "examplebucket.s3.amazonaws.com", "/", [("max-keys", "2"), ("prefix", "J")],
                       {"x-amz-content-sha256": E_, "x-amz-date": "20130524T000000Z"}, E_, AK, AS, "us-east-1", "20130524T000000Z")
    ok("SigV4 = Beispiele aus der AWS-Dokumentation (GET Object, ListObjects)",
       v1.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")
       and "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in v1
       and v2.endswith("Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"))
    try:                                                   # zusaetzlich gegen botocore, falls vorhanden (nicht im Image)
        from botocore.auth import S3SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        import datetime as _dtm
        from unittest import mock as _mock
        rq = AWSRequest("PUT", "http://127.0.0.1:9000/b/saga%20x/a~b.age?partNumber=2&uploadId=a%2Bb%3D",
                        headers={"x-amz-content-sha256": E_, "Content-Type": "application/octet-stream"}, data=b"")
        with _mock.patch("botocore.auth.datetime") as _d:
            _d.datetime.utcnow.return_value = _dtm.datetime(2026, 10, 19, 1, 2, 3)
            _d.datetime.now.return_value = _dtm.datetime(2026, 10, 19, 1, 2, 3, tzinfo=_dtm.timezone.utc)
            S3SigV4Auth(Credentials("k", "s"), "s3", "eu-test-1").add_auth(rq)
        mine = backups.sigv4("PUT", "127.0.0.1:9000", "/b/saga%20x/a~b.age", [("partNumber", "2"), ("uploadId", "a+b=")],
                             {"x-amz-content-sha256": E_, "Content-Type": "application/octet-stream", "x-amz-date": rq.headers["X-Amz-Date"]},
                             E_, "k", "s", "eu-test-1", rq.headers["X-Amz-Date"])
        ok("SigV4 = botocore (Pfad mit Leerzeichen, Abfrage mit + und =)",
           mine.split("Signature=")[1] == rq.headers["Authorization"].split("Signature=")[1])
    except ImportError:
        print("(botocore nicht installiert - Gegenprobe uebersprungen)")
    S3S = fakes3.start(S3P)
    S3B = f"http://127.0.0.1:{S3P}"

    def s3stats():
        return json.loads(urllib.request.urlopen(S3B + "/_stats", timeout=10).read())

    def s3ctl(**kw):
        urllib.request.urlopen(S3B + "/_ctl?" + urllib.parse.urlencode(kw), timeout=10).read()

    def s3raw(k):
        return urllib.request.urlopen(S3B + "/_get?" + urllib.parse.urlencode({"b": fakes3.BUCKET, "k": k}), timeout=30).read()
    try:
        scfg = dict(backups.config(), **backups.TARGET_CLEAR)
        scfg.update(mode="s3", s3_endpoint=S3B, s3_region=fakes3.REGION, s3_bucket=fakes3.BUCKET, s3_prefix="saga/backups",
                    s3_key=fakes3.KEY_ID, s3_secret=fakes3.SECRET, s3_style="auto")
        ok("S3: Ziel eingerichtet (has_target), path-style fuer eigene Endpunkte", backups.has_target(scfg)
           and not backups.S3(scfg).virtual and backups.S3(dict(scfg, s3_endpoint="https://s3.eu-central-1.amazonaws.com")).virtual
           and not backups.S3(dict(scfg, s3_endpoint="https://s3.eu-central-1.amazonaws.com", s3_bucket="mit.punkt")).virtual)
        free = backups.test_target(scfg)
        ok("S3: Verbindung pruefen (schreiben, lesen, loeschen), kein Platz bekannt", free is None and s3stats()["sigfail"] == 0
           and not s3stats()["objects"][fakes3.BUCKET])
        for bad, code in ((dict(scfg, s3_secret="falsch"), "auth"), (dict(scfg, s3_bucket="gibt-es-nicht"), "http")):
            try:
                backups.test_target(bad)
                ok(f"S3: falsche Angaben -> Fehler {code}", False)
            except backups.BackupError as ex:
                ok(f"S3: falsche Angaben -> Fehler {code} ({backups.text(ex.code, ex.p)})", ex.code == code
                   and (code != "http" or "NoSuchBucket" in str(ex.p)))
        ok("S3: falscher Secret-Key -> Signatur vom Server abgelehnt", s3stats()["sigfail"] >= 1)
        sf0 = s3stats()["sigfail"]
        backups.save_config(scfg)
        rawc = q("SELECT v FROM setting WHERE k='backup'")[0][0]
        ok("S3: Access-Key und Secret verschluesselt gespeichert", fakes3.SECRET not in rawc and fakes3.KEY_ID not in rawc
           and '"s3_secret": "enc:' in rawc and backups.config()["s3_secret"] == fakes3.SECRET)
        with open(os.path.join(STATE, "files/ab/gross.bin"), "wb") as f:
            f.write(os.urandom(12 * 1024 * 1024))
        time.sleep(1.1)
        good, s3out = backups.run("test")
        ss = s3stats()
        objs = ss["objects"][fakes3.BUCKET]
        sname = s3out["name"] if good else ""
        mine = {k.rsplit("/", 1)[-1]: k for k in objs if k.startswith(f"saga/backups/{sname}/")}
        ok(f"S3: Lauf erfolgreich ({sname or s3out}), alle Teile unter dem Praefix", good and set(mine) == {
            "db.dump.age", "dateien.tar.age", "geheimnisse.env.age", "manifest.json.age", "manifest.json"})
        ok(f"S3: Multipart fuer die grosse Datei (Teile {ss['parts']}, Abschluesse {ss['completes']}), keine Signaturfehler",
           good and s3out["chunked"] >= 1 and ss["completes"] >= 1 and ss["parts"] >= 3 and ss["sigfail"] == sf0 and ss["hashfail"] == 0
           and ss["uploads"] == 0)
        sman = json.loads(s3raw(mine.get("manifest.json", "x")) or b"{}")
        ok("S3: Manifest stimmt mit den Objekten ueberein", sman.get("files") and all(
            hashlib.sha256(s3raw(mine[n])).hexdigest() == v["sha256"] for n, v in sman["files"].items()))
        sblob = b"".join(s3raw(k) for k in mine.values())
        ok("S3: nichts im Klartext", MARK.encode() not in sblob and SECRET.encode() not in sblob and b"PGDMP" not in sblob)
        tp2 = subprocess.run(["age", "-d", "-i", k2], input=s3raw(mine["dateien.tar.age"]), capture_output=True)
        ok("S3: grosse Datei nach Multipart wieder lesbar (age + tar)", tp2.returncode == 0 and b"gross.bin" in tp2.stdout[:200000])
        # Fehler: Daten verfaelscht (Multipart und einzeln) -> Nachpruefung erkennt es; Server weg
        time.sleep(1.1)
        s3ctl(corrupt=1)
        g2, e2 = backups.run("test")
        s3ctl(corrupt=0)
        ok(f"S3: verfaelschte Daten erkannt ({getattr(e2, 'code', e2)})", not g2 and e2.code == "checksum")
        time.sleep(1.1)
        s3ctl(down=1)
        g3, e3 = backups.run("test")
        s3ctl(down=0)
        ok(f"S3: Ziel antwortet mit Fehler -> gemeldet ({getattr(e3, 'code', e3)} {getattr(e3, 'p', '')})", not g3 and e3.code == "http"
           and e3.p.get("status") == 503)
        # Abbruch mitten im Multipart-Upload -> AbortMultipartUpload
        orig = backups.S3._range_hash
        calls = {"n": 0}

        def boom(f, off, n):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(5, "Testfehler")
            return orig(f, off, n)
        backups.S3._range_hash = staticmethod(boom)
        ab0 = s3stats()["aborts"]
        sd = backups.S3(scfg)
        big = os.path.join(TMPROOT, "big.bin")
        with open(big, "wb") as f:
            f.write(os.urandom(11 * 1024 * 1024))
        try:
            sd.put_file(big, sd.url("abbruch.bin"))
            ok("S3: Abbruch im Multipart-Upload", False)
        except OSError:
            ok("S3: Abbruch im Multipart-Upload -> Upload verworfen (Abort)", s3stats()["aborts"] == ab0 + 1 and s3stats()["uploads"] == 0
               and "saga/backups/abbruch.bin" not in s3stats()["objects"][fakes3.BUCKET])
        finally:
            backups.S3._range_hash = staticmethod(orig)
            sd.close()
        # GFS im Bucket (Seiten zu je 2 Eintraegen: continuation-token), fremde Praefixe bleiben
        s3ctl(page=2)
        sd = backups.S3(scfg)
        for i in range(25):
            n = (t0 - timedelta(days=40 + i)).strftime("saga-%Y%m%d-%H%M%S")
            sd.put_bytes(b"{}", sd.url(n, "manifest.json"))
            sd.put_bytes(b"x", sd.url(n, "db.dump.age"))
        sd.put_bytes(b"x", sd.url("Fotos", "a.jpg"))
        sd.put_bytes(b"x", "anderes/saga-20200101-000000/manifest.json")
        before = [r["name"] for r in backups.list_runs(sd) if r["complete"]]
        gone = backups.prune(sd, dict(scfg, keep_d=3, keep_w=2, keep_m=3), keep={sname})
        after = {r["name"] for r in backups.list_runs(sd) if r["complete"]}
        want = backups.gfs_keep(before, 3, 2, 3) | {sname} | {n for n in before if time.time() - backups.run_time(n).timestamp() < 86400}
        left = s3stats()["objects"][fakes3.BUCKET]
        ok(f"S3: GFS ueber mehrere Listen-Seiten ({len(before)} -> {len(after)}, {len(gone)} geloescht)", len(before) >= 26
           and after == want and len(gone) >= 10 and not any(k.startswith(f"saga/backups/{g}/") for g in gone for k in left))
        ok("S3: fremde Objekte bleiben (anderer Ordner, anderes Praefix)", "saga/backups/Fotos/a.jpg" in left
           and "anderes/saga-20200101-000000/manifest.json" in left)
        sd.close()
        s3ctl(page=1000)
        # virtual-hosted: bucket.host (Namensaufloesung fuer *.localhost im Test auf 127.0.0.1)
        import socket as _sock
        _gai = _sock.getaddrinfo

        def gai(host, *a, **kw):
            return _gai("127.0.0.1" if str(host).endswith(".localhost") else host, *a, **kw)
        _sock.getaddrinfo = gai
        try:
            v0 = s3stats()["virtual"]
            vcfg = dict(scfg, s3_endpoint=f"http://localhost:{S3P}", s3_style="virtual")
            backups.test_target(vcfg)
            vd = backups.S3(vcfg)
            vruns = backups.list_runs(vd)
            vd.close()
            ok(f"S3 virtual-hosted (Host {fakes3.BUCKET}.localhost): pruefen und auflisten",
               s3stats()["virtual"] > v0 and any(r["name"] == sname and r["complete"] for r in vruns))
        finally:
            _sock.getaddrinfo = _gai
        # netguard: intern ist nur der eingetragene Host erlaubt (127.0.0.1 ueber einen anderen Namen -> gesperrt)
        try:
            with backups._client_for(("s3.example.org",)) as cl_:
                backups._net_call(lambda: cl_.get(S3B + "/_stats"))
            ok("S3: andere interne Adresse gesperrt (netguard)", False)
        except backups.BackupError as ex:
            ok(f"S3: andere interne Adresse gesperrt (netguard, {ex.code})", ex.code == "blocked")
        # Kommandozeile: frischer Stack, Ziel per Umgebung (S3)
        s3env = dict(fenv, SAGA_BACKUP_URL="", SAGA_BACKUP_S3_ENDPOINT=S3B, SAGA_BACKUP_S3_BUCKET=fakes3.BUCKET,
                     SAGA_BACKUP_S3_ACCESS_KEY=fakes3.KEY_ID, SAGA_BACKUP_S3_SECRET_KEY=fakes3.SECRET,
                     SAGA_BACKUP_S3_REGION=fakes3.REGION, SAGA_BACKUP_S3_PREFIX="saga/backups")
        r = subprocess.run([sys.executable, "run.py", "backup-holen", "--liste"], cwd=APP, env=s3env, capture_output=True, text=True, timeout=120)
        ok(f"S3: run.py backup-holen --liste per Umgebung {r.stderr.strip()[-120:]}", r.returncode == 0 and sname in r.stdout)
        r = subprocess.run([sys.executable, "run.py", "backup-pruefen", "--schluessel", k1, sname], cwd=APP, env=s3env,
                           capture_output=True, text=True, timeout=600)
        ok(f"S3: run.py backup-pruefen holt aus S3 und spielt in Wegwerf-Datenbank ({r.returncode}) {(r.stdout + r.stderr).strip()[-160:]}",
           r.returncode == 0 and "Testwiederherstellung in Ordnung" in r.stdout)
        os.remove(os.path.join(STATE, "files/ab/gross.bin"))
    finally:
        S3S.shutdown()
        S3S.server_close()

    # =================================================================================================================
    # Ordner im Container (SAGA_BACKUP_DIRS)
    # =================================================================================================================
    ZR = os.path.join(TMPROOT, "ziel-wurzel")
    os.makedirs(ZR)
    os.environ["SAGA_BACKUP_DIRS"] = ZR + ", /nicht/da"
    os.makedirs(os.path.join(TMPROOT, "draussen"))
    os.symlink(os.path.join(TMPROOT, "draussen"), os.path.join(ZR, "link-raus"))
    ok("Ordner: Pfade ausserhalb, mit .., relativ und ueber Symlinks nach draussen abgelehnt",
       backups.norm_folder("/etc") is None and backups.norm_folder(ZR + "/../draussen") is None and backups.norm_folder("ziel") is None
       and backups.norm_folder(os.path.join(ZR, "link-raus")) is None and backups.norm_folder(ZR + "/saga/") == ZR + "/saga"
       and backups.norm_folder("/") is None)
    try:
        backups.test_target({"mode": "folder", "path": "/etc"})
        ok("Ordner: /etc als Ziel abgelehnt", False)
    except backups.BackupError as ex:
        ok(f"Ordner: /etc als Ziel abgelehnt ({backups.text(ex.code, ex.p)})", ex.code == "folder_path" and ZR in backups.text(ex.code, ex.p))
    fcfg = dict(backups.config(), **backups.TARGET_CLEAR)
    fcfg.update(mode="folder", path=ZR + "/saga")
    free = backups.test_target(fcfg)
    ok("Ordner: angelegt, beschreibbar, freier Platz bekannt", isinstance(free, int) and free > 0 and os.path.isdir(ZR + "/saga")
       and not os.listdir(ZR + "/saga"))
    backups.save_config(fcfg)
    # Arbeitsordner: Reste mit Klartext werden aufgeraeumt; eine laufende Pruefung (eigener Ordner) bleibt
    tdir = backups.tmp_dir()
    for n_ in ("lauf-saga-20260101-000000", "pruefung-frisch", "pruefung-alt", "pruefung"):
        os.makedirs(os.path.join(tdir, n_), exist_ok=True)
        with open(os.path.join(tdir, n_, "db.dump"), "w") as f:
            f.write("PGDMP klartext")
    os.utime(os.path.join(tdir, "pruefung-alt"), (time.time() - 2 * 86400, time.time() - 2 * 86400))
    nclean = backups.cleanup_tmp()
    ok(f"Start raeumt Klartext-Reste auf ({nclean}), laufende Pruefung bleibt", sorted(os.listdir(tdir)) == ["pruefung-frisch"])
    time.sleep(1.1)
    good, fout = backups.run("test")
    fname = fout["name"] if good else ""
    fl = sorted(os.listdir(os.path.join(ZR, "saga", fname))) if good else []
    ok(f"Ordner: Lauf erfolgreich ({fname or fout}): {fl}", good and fl == sorted(["db.dump.age", "dateien.tar.age", "geheimnisse.env.age",
                                                                                  "manifest.json.age", "manifest.json"]))
    ok("Ordner: Lauf laesst den Ordner einer laufenden Pruefung stehen", os.path.isdir(os.path.join(tdir, "pruefung-frisch"))
       and not any(x.startswith("lauf-") for x in os.listdir(tdir)))
    shutil.rmtree(os.path.join(tdir, "pruefung-frisch"))
    fblob = b"".join(open(os.path.join(ZR, "saga", fname, x), "rb").read() for x in fl)
    ok("Ordner: nichts im Klartext, keine .part-Reste", MARK.encode() not in fblob and SECRET.encode() not in fblob
       and not any(x.endswith(".part") for x in fl))
    for i in range(20):
        n = (t0 - timedelta(days=40 + i)).strftime("saga-%Y%m%d-%H%M%S")
        os.makedirs(os.path.join(ZR, "saga", n))
        with open(os.path.join(ZR, "saga", n, "manifest.json"), "w") as f:
            f.write("{}")
    os.makedirs(os.path.join(ZR, "saga", "Fotos"))
    os.makedirs(os.path.join(TMPROOT, "draussen", "saga-20200101-000000"))
    with open(os.path.join(TMPROOT, "draussen", "saga-20200101-000000", "manifest.json"), "w") as f:
        f.write("{}")
    os.symlink(os.path.join(TMPROOT, "draussen", "saga-20200101-000000"), os.path.join(ZR, "saga", "saga-20200102-000000"))
    fd_ = backups.target(fcfg)
    gone = backups.prune(fd_, dict(fcfg, keep_d=2, keep_w=1, keep_m=2), keep={fname})
    rest = sorted(os.listdir(os.path.join(ZR, "saga")))
    ok(f"Ordner: GFS ({len(gone)} geloescht), fremder Ordner bleibt, Symlink nach draussen nicht verfolgt",
       len(gone) >= 15 and fname in rest and "Fotos" in rest and os.path.isfile(os.path.join(TMPROOT, "draussen", "saga-20200101-000000", "manifest.json")))
    for bad in ("../draussen", "saga-20200102-000000/../../draussen"):
        try:
            fd_.delete_run(bad)
            ok(f"Ordner: delete_run({bad}) abgelehnt", False)
        except backups.BackupError:
            ok(f"Ordner: delete_run({bad}) abgelehnt", os.path.isdir(os.path.join(TMPROOT, "draussen")))
    try:
        fd_.get_bytes("link-raus-gibts-nicht/../../x")
        ok("Ordner: Lesen mit .. abgelehnt", False)
    except backups.BackupError as ex:
        ok("Ordner: Lesen mit .. abgelehnt", ex.code == "folder_path")
    fenv2 = dict(fenv, SAGA_BACKUP_URL="", SAGA_BACKUP_PATH=ZR + "/saga", SAGA_BACKUP_DIRS=ZR)
    r = subprocess.run([sys.executable, "run.py", "backup-pruefen", "--schluessel", k2, fname], cwd=APP, env=fenv2,
                       capture_output=True, text=True, timeout=600)
    ok(f"Ordner: run.py backup-pruefen per SAGA_BACKUP_PATH ({r.returncode}) {(r.stdout + r.stderr).strip()[-160:]}",
       r.returncode == 0 and "Testwiederherstellung in Ordnung" in r.stdout and os.path.isdir(os.path.join(ZR, "saga", fname)))
    r = subprocess.run([sys.executable, "run.py", "backup-pruefen", "--schluessel", k2, os.path.join(ZR, "saga", fname)], cwd=APP,
                       env=dict(fenv, SAGA_BACKUP_URL=""), capture_output=True, text=True, timeout=600)
    ok(f"Ordner: Lauf-Ordner direkt pruefen (bleibt liegen) ({r.returncode})", r.returncode == 0
       and os.path.isfile(os.path.join(ZR, "saga", fname, "db.dump.age")))
    os.environ.pop("SAGA_BACKUP_DIRS", None)
finally:
    if srv_proc:
        srv_proc.kill()
    db.DATABASE_URL = REAL
    try:
        if db._pool is not None:
            db._pool.close(timeout=2)
    except Exception:  # noqa: BLE001
        pass
    db._pool = None
    for n in (NAME, FRESH):
        try:
            q(f"DROP DATABASE IF EXISTS {n} WITH (FORCE)", url=REAL)
        except Exception as ex:  # noqa: BLE001
            print("aufraeumen:", ex)
    shutil.rmtree(TMPROOT, ignore_errors=True)
    for s_ in (NC, DV):
        s_.shutdown()
        s_.server_close()

# --- Admin-Bereich ueber HTTP (Test-Instanz, echte Test-Datenbank; Einstellungen danach wie vorher) ---------------------
AD = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", ""))
if AD.hostname:
    from itsdangerous import TimestampSigner
    SA = TimestampSigner(hashlib.sha256(("hk-admin|" + os.environ["SESSION_SECRET"]).encode()).hexdigest())
    DV2 = fakedav.start(DVP, False)
    S3S2 = fakes3.start(S3P)
    S3B2 = f"http://127.0.0.1:{S3P}"
    NC2 = fakedav.start(NCP, True)
    DV2.store.pw["bk"] = "dav-pass-123"
    jar = {}

    def admin(path, form=None):
        tok = jar.setdefault("csrf", "bk-csrf-" + secrets.token_hex(6))
        if "cookie" not in jar:
            jar["cookie"] = "hk_admin=" + SA.sign(base64.b64encode(json.dumps({"aid": "bk-adm", "ep": time.time() + 5, "csrf": tok}).encode())).decode()
        c = http.client.HTTPConnection(AD.hostname, AD.port, timeout=60)
        h = {"Cookie": jar["cookie"], "Accept-Language": "de"}
        body = None
        if form is not None:
            body = urllib.parse.urlencode(dict(form, csrf=tok))
            h["Content-Type"] = "application/x-www-form-urlencoded"
        c.request("POST" if form is not None else "GET", path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        sc = r.getheader("set-cookie")
        if sc:
            jar["cookie"] = sc.split(";")[0]
        loc = r.getheader("location")
        c.close()
        if r.status == 303 and loc:
            return admin(loc.split("#")[0])
        return r.status, data.decode("utf-8", "replace"), r

    def raw(k):
        r = q("SELECT v FROM setting WHERE k=%s", (k,), url=REAL)
        return r[0][0] if r else None
    import auth as _a                                   # gegen die echte Test-Datenbank (Pool neu)
    prev = {k: raw(k) for k in ("backup", "backup_state")}
    maxid = q("SELECT coalesce(max(id), 0) FROM backup_run", url=REAL)[0][0]
    try:
        q("DELETE FROM setting WHERE k IN ('backup', 'backup_state')", url=REAL)     # Ausgangslage: nichts eingerichtet
        q("DELETE FROM users WHERE username='bk-adm'", url=REAL)
        _a._settings_cache["v"] = None
        _a.create_user("bk-adm", "Bk-Admin-Pass-1234", "Backup Admin", is_admin=True)
        s, html, _ = admin("/backups")
        ok(f"Admin: Seite Backups ({s})", s == 200 and "Verschlüsselung" in html and 'href="/backups"' in html)
        s, html, _ = admin("/backups", {"action": "connect", "url": NCB, "user": "", "password": "", "folder": "Saga"})
        ok("Admin: Nextcloud erkannt -> Login-Link, Seite aktualisiert sich", "In Nextcloud anmelden" in html
           and NCB + "/login/v2/flow/" in html and 'data-reload="4"' in html)
        urllib.request.urlopen(NCB + "/_grant").read()
        for _ in range(20):
            time.sleep(0.5)
            s, html, _ = admin("/backups")
            if "Nextcloud verbunden" in html:
                break
        ok("Admin: nach der Anmeldung in Nextcloud verbunden", "Nextcloud verbunden" in html and fakedav.LOGIN in html
           and "App-Passwort (Login Flow)" in html)
        s, html, _ = admin("/backups", {"action": "disconnect"})
        ok("Admin: Trennen widerruft das App-Passwort", "App-Passwort in Nextcloud widerrufen" in html
           and json.loads(urllib.request.urlopen(NCB + "/_stats").read())["revoked"] == 1)
        s, html, _ = admin("/backups", {"action": "connect", "url": DVB + "/dav", "user": "bk", "password": "dav-pass-123", "folder": "admin-test"})
        ok("Admin: WebDAV verbinden", "Verbunden (WebDAV)" in html and "admin-test" in html)
        s, html, _ = admin("/backups", {"action": "genkey"})
        import re as _re
        m = _re.search(r"(AGE-SECRET-KEY-1[0-9A-Z]+)", html)
        tokm = _re.search(r'/backups/schluessel/([A-Za-z0-9_-]+)', html)
        ok("Admin: Schluesselpaar erzeugt, privater Schluessel einmal angezeigt", bool(m and tokm))
        ok("Admin: privater Schluessel NICHT gespeichert", m and m.group(1) not in (raw("backup") or ""))
        s, keytxt, rr = admin("/backups/schluessel/" + tokm.group(1))
        ok("Admin: privater Schluessel als Datei", s == 200 and m.group(1) in keytxt and "attachment" in (rr.getheader("content-disposition") or ""))
        s, html, _ = admin("/backups", {"action": "confirm_key", "last6": "XXXXXX"})
        ok("Admin: falsche Bestaetigung abgelehnt", "stimmen nicht" in html and json.loads(raw("backup")).get("recipients") in (None, []))
        s, html, _ = admin("/backups", {"action": "confirm_key", "last6": m.group(1)[-6:].lower()})
        rec = json.loads(raw("backup")).get("recipients") or []
        ok("Admin: Schluessel nach Bestaetigung eingetragen", "Schlüssel eingetragen" in html and len(rec) == 1 and rec[0].startswith("age1"))
        s, _, _ = admin("/backups/schluessel/" + tokm.group(1))
        ok("Admin: Download danach weg", s == 404)
        s, html, _ = admin("/backups", {"action": "add_key", "key": "AGE-SECRET-KEY-1ABC"})
        ok("Admin: privater Schluessel als Empfaenger abgelehnt", "Kein gültiger öffentlicher Schlüssel" in html)
        s, html, _ = admin("/backups", {"action": "save", "time": "25:99", "keep_d": "7", "keep_w": "4", "keep_m": "6"})
        ok("Admin: ungueltige Uhrzeit abgelehnt", "Uhrzeit ungültig" in html)
        s, html, _ = admin("/backups", {"action": "save", "time": "04:10", "keep_d": "5", "keep_w": "3", "keep_m": "2", "secrets": "1"})
        cfg = json.loads(raw("backup"))
        ok("Admin: Zeitplan gespeichert (Zeitplan aus)", cfg["time"] == "04:10" and cfg["keep_d"] == 5 and cfg["enabled"] is False)
        s, html, _ = admin("/backups", {"action": "run"})
        ok("Admin: Jetzt sichern", "Backup gestartet" in html)
        fin = None
        for _ in range(120):
            fin = q("SELECT ok, finished, error FROM backup_run WHERE id > %s ORDER BY id DESC LIMIT 1", (maxid,), url=REAL)
            if fin and fin[0][1]:
                break
            time.sleep(1)
        ok(f"Admin: Lauf fertig ({fin})", fin and fin[0][0] == 1)
        s, html, _ = admin("/backups?ziel=1")
        ok("Admin: Backups im Ziel mit Fingerabdruck", "saga-" in html and "Datenbank, Dateien" in html)
        s, html, _ = admin("/backups", {"action": "disconnect"})
        ok("Admin: Trennen", "Ziel getrennt" in html and json.loads(raw("backup"))["mode"] == "")
        ok("Admin: Auswahl der Ziel-Art (S3, Ordner, WebDAV/Nextcloud)", 'value="s3"' in html and 'value="folder"' in html
           and 'value="dav"' in html and "Ordner im Container" in html)
        s, html, _ = admin("/backups", {"action": "connect", "kind": "folder", "path": "/etc"})
        ok("Admin: Ordner ausserhalb von SAGA_BACKUP_DIRS abgelehnt", "Ordner nicht erlaubt" in html and json.loads(raw("backup"))["mode"] == "")
        s, html, _ = admin("/backups", {"action": "connect", "kind": "s3", "s3_endpoint": S3B2, "s3_region": fakes3.REGION,
                                        "s3_bucket": fakes3.BUCKET, "s3_prefix": "admin", "s3_key": fakes3.KEY_ID, "s3_secret": "falsch"})
        ok("Admin: S3 mit falschem Secret abgelehnt", "Anmeldung am Ziel abgelehnt" in html and json.loads(raw("backup"))["mode"] == "")
        s, html, _ = admin("/backups", {"action": "connect", "kind": "s3", "s3_endpoint": S3B2 + "/pfad", "s3_bucket": fakes3.BUCKET,
                                        "s3_key": "k", "s3_secret": "s"})
        ok("Admin: S3-Endpunkt mit Pfad abgelehnt", "Endpunkt ungültig" in html)
        s, html, _ = admin("/backups", {"action": "connect", "kind": "s3", "s3_endpoint": S3B2, "s3_region": fakes3.REGION,
                                        "s3_bucket": fakes3.BUCKET, "s3_prefix": "/admin//test/", "s3_key": fakes3.KEY_ID,
                                        "s3_secret": fakes3.SECRET, "s3_style": "path"})
        cfg = json.loads(raw("backup"))
        ok("Admin: S3 verbunden, Angaben angezeigt, Geheimnisse verschluesselt", "Verbunden (S3)" in html and fakes3.BUCKET in html
           and "admin/test" in html and fakes3.SECRET not in html and cfg["mode"] == "s3" and cfg["s3_prefix"] == "admin/test"
           and cfg["s3_secret"].startswith("enc:") and cfg["s3_key"].startswith("enc:") and fakes3.SECRET not in raw("backup"))
        s, html, _ = admin("/backups", {"action": "test"})
        ok("Admin: S3-Verbindung pruefen", "Verbindung in Ordnung" in html)
        s, html, _ = admin("/backups", {"action": "disconnect"})
        cfg = json.loads(raw("backup"))
        bset = _a.settings(fresh=True)["backup"]
        ok("Admin: S3 trennen leert Zugangsdaten", "Ziel getrennt" in html and cfg["mode"] == "" and bset["s3_secret"] == ""
           and bset["s3_key"] == "" and bset["s3_endpoint"] == "")
    finally:
        with psycopg.connect(REAL, autocommit=True) as c:
            for k, v in prev.items():
                if v is None:
                    c.execute("DELETE FROM setting WHERE k=%s", (k,))
                else:
                    c.execute("UPDATE setting SET v=%s WHERE k=%s", (v, k))
            c.execute("DELETE FROM backup_run WHERE id > %s", (maxid,))
            c.execute("DELETE FROM notification WHERE uid='bk-adm'")
            c.execute("DELETE FROM users WHERE username='bk-adm'")
        for s_ in (DV2, NC2, S3S2):
            s_.shutdown()
            s_.server_close()
else:
    print("(Admin-Bereich: HK_TEST_ADMIN nicht gesetzt - uebersprungen)")

print(f"\n{sum(res)}/{len(res)} OK" + ("  ALLES GRUEN" if all(res) else "  FEHLER"))
sys.exit(0 if all(res) else 1)
