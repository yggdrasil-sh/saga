"""Versionierung: /api/version, Datenbank-Migrationen (genau einmal, vorher Backup), Schutz vor zu neuer Datenbank."""
import http.client, json, os, sys, time, urllib.parse
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
    # 2.1: Migration 6 = Rueckweg-Sperre (2.0.x startet nicht auf einer 2.1-Datenbank). Update von einer 2.1-Testversion
    # (Stand 5, die neuen Tabellen gibt es schon) -> Backup "vor-schema-6", dann Stand 6; Migration 6 aendert nichts.
    ok("Schema 2.1: Migration 6 vorhanden", db.SCHEMA_VERSION >= 6 and 6 in db.MIGRATIONS)
    with psycopg.connect(db.DATABASE_URL) as c:
        c.execute("DELETE FROM schema_version WHERE v > 5")
        tabs = c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name IN "
                         "('mfa_totp','mfa_passkey','mfa_recovery','avatar','chat_call')").fetchone()[0]
    n1 = len(backups())
    time.sleep(1.1)                                # Backup-Name hat Sekunden - nicht den von eben ueberschreiben
    db.init_schema()
    new6 = [f for f in backups() if f not in before and "vor-schema-6" in f]
    ok(f"Update 2.1-testing (Stand 5) -> 6 mit Backup vorher ({len(new6)})", db.schema_version() == db.SCHEMA_VERSION
       and len(backups()) == n1 + 1 and new6 and tabs == 5)
    with psycopg.connect(db.DATABASE_URL) as c:
        c.execute(db.MIGRATIONS[6])
    ok("Migration 6 beliebig oft ausfuehrbar", True)
    real = db.SCHEMA_VERSION
    db.SCHEMA_VERSION = 5                          # so sieht 2.0.x / eine aeltere 2.1-Testversion die Datenbank
    try:
        db.init_schema()
        ok("Rueckweg: Stand-5-Version startet nicht", False)
    except db.SchemaTooNew as ex:
        ok(f"Rueckweg: Stand-5-Version verweigert den Start ({str(ex)[:60]}…)", "Schema-Stand 6" in str(ex) and "kennt nur 5" in str(ex))
    finally:
        db.SCHEMA_VERSION = real
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
