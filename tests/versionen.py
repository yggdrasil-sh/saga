"""Versionierung: /api/version, Datenbank-Migrationen (genau einmal, vorher Backup), Schutz vor zu neuer Datenbank."""
import http.client, json, os, sys, urllib.parse
import psycopg
import db
from version import VERSION, newer, parse
B = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8080"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


def get(p):
    c = http.client.HTTPConnection(B.hostname, B.port, timeout=20)
    c.request("GET", p, headers={"Host": HOST})
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"null")


def backups():
    try:
        return sorted(f for f in os.listdir(db.BACKUP_DIR) if f.endswith(".dump"))
    except OSError:
        return []


before = backups()
try:
    s, d = get("/api/version")
    ok(f"/api/version ohne Anmeldung ({s})", s == 200 and d["name"] == "Saga" and d["version"] == VERSION)
    ok("API-Stand und min_app", d["api"] >= 1 and parse(d["min_app"]) >= (1, 0, 0) and d["schema"] == db.SCHEMA_VERSION)
    ok("VERSION ist SemVer", parse(VERSION) >= (1, 0, 0) and VERSION.count(".") == 2)
    ok("Vergleich: 1.10.0 neuer als 1.9.3, v1.0.0 nicht neuer als 1.0.0", newer("1.10.0", "1.9.3") and not newer("v1.0.0", "1.0.0"))
    ok("Datenbank auf aktuellem Stand", db.schema_version() == db.SCHEMA_VERSION)
    p = db.backup("test")
    ok("Backup anlegen (pg_dump)", os.path.getsize(p) > 1000 and os.path.basename(p) in backups())
    os.remove(p)
    # Update simulieren: Stand zuruecksetzen -> init_schema migriert erneut (idempotent) und sichert vorher
    with psycopg.connect(db.DATABASE_URL) as c:
        c.execute("DELETE FROM schema_version WHERE v > 1")
    n0 = len(backups())
    db.init_schema()
    new = [f for f in backups() if f not in before]
    ok("Migration: wieder aktueller Stand", db.schema_version() == db.SCHEMA_VERSION)
    ok("Migration: vorher automatisch Backup", len(backups()) == n0 + 1 and any("vor-schema" in f for f in new))
    db.init_schema()
    ok("Zweiter Start: keine erneute Migration, kein Backup", len(backups()) == n0 + 1)
    with psycopg.connect(db.DATABASE_URL) as c:
        c.execute("INSERT INTO schema_version (v) VALUES (%s)", (db.SCHEMA_VERSION + 1,))
    try:
        db.init_schema()
        ok("Zu neue Datenbank -> Start verweigert", False)
    except db.SchemaTooNew as ex:
        ok("Zu neue Datenbank -> Start verweigert, mit Hinweis", "Backup" in str(ex))
finally:
    with psycopg.connect(db.DATABASE_URL) as c:
        c.execute("DELETE FROM schema_version WHERE v > %s", (db.SCHEMA_VERSION,))
    for f in backups():
        if f not in before:
            os.remove(os.path.join(db.BACKUP_DIR, f))
print(f"\nVERSIONEN {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
