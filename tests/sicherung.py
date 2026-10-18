"""Backup und Zurueckspielen (db.backup / db.restore, "python run.py restore") von Anfang bis Ende - in einer eigenen
Wegwerf-Datenbank auf demselben Postgres-Server, die echte Datenbank bleibt unberuehrt.

Prueft vor allem: pg_restore aus dem Image (17) gegen den Server der Standard-Compose (postgres:16) - frueher Abbruch an
"SET transaction_timeout"; Inhalte, die wie Steuerzeilen aussehen, bleiben erhalten; ein Backup von vor 2.2 (ohne die
abgeleiteten Tabellen) laesst sich einspielen; ein kaputtes Backup aendert nichts."""
import os, secrets, sys
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import db

res = []
def ok(n, c): res.append(bool(c)); print(("OK  " if c else "FAIL"), n)


REAL = db.DATABASE_URL
NAME = "saga_sich_" + secrets.token_hex(4)
URL = make_conninfo(REAL, dbname=NAME)
# Inhalte, die in der SQL-Liste von pg_restore wie Steuerzeilen aussehen (der Filter darf sie nicht anfassen)
TRICKY = ["SET transaction_timeout = 0;", "COMMIT;", "BEGIN;", "\\.", "Zeile 1\nSET transaction_timeout = 0;\nCOMMIT;",
          "Umlaute äöü ß – 😀", "x" * 5000]


def q(sql, args=(), url=None):
    with psycopg.connect(url or URL, autocommit=True) as c:
        cur = c.execute(sql, args)
        return cur.fetchall() if cur.description else None


def state():
    """Vergleichbarer Stand: Probe-Tabelle, Seiten, abgeleitete Daten"""
    return (q("SELECT id, txt FROM sich_probe ORDER BY id"), q("SELECT id, uid, title, body FROM note ORDER BY id"),
            q("SELECT note_id, search FROM note_derived ORDER BY note_id") if q("SELECT to_regclass('public.note_derived')")[0][0] else None)


made = []
q(f"CREATE DATABASE {NAME}", url=REAL)
try:
    db.DATABASE_URL = URL                      # backup/restore/init_schema arbeiten jetzt auf der Wegwerf-Datenbank
    os.environ["SAGA_SKIP_BACKUP"] = "1"
    db.init_schema()
    sv = int(q("SHOW server_version_num")[0][0])
    print(f"Server {sv // 10000}, Datenbank {conninfo_to_dict(URL)['dbname']}")
    ok("Wegwerf-Datenbank angelegt, Schema aktuell", db.schema_version() == db.SCHEMA_VERSION)
    q("CREATE TABLE sich_probe (id INT PRIMARY KEY, txt TEXT)")
    for i, t in enumerate(TRICKY):
        q("INSERT INTO sich_probe VALUES (%s, %s)", (i, t))
    q("CREATE TABLE sich_eine (txt TEXT)")      # eine Spalte: COPY-Zeile = genau der Text
    q("INSERT INTO sich_eine VALUES ('SET transaction_timeout = 0;'), ('COMMIT;')")
    # Funktionskoerper mit Steuerzeilen ausserhalb von COPY: SET darf nur im Kopf wegfallen, COMMIT nicht haengenbleiben
    FBODY = "\nBEGIN\nSET transaction_timeout = 0;\nCOMMIT;\nRETURN 1;\nEND "
    q(f"CREATE FUNCTION sich_f() RETURNS int LANGUAGE plpgsql AS $f${FBODY}$f$")
    for i in (1, 2, 3):
        q("INSERT INTO note (id, uid, title, body, created, updated) VALUES (%s, 'sich-a', %s, %s, 1, 1)",
          (i, f"Seite {i}", f"Text {i} [[{i + 1}]]"))
        q("INSERT INTO note_derived (note_id, search) VALUES (%s, %s)", (i, f"seite {i}"))
        q("INSERT INTO note_link (src, dst) VALUES (%s, %s)", (i, i + 1))
    before = state()

    p = db.backup("sicherung-test")
    made.append(p)
    with open(p, "rb") as f:
        head = f.read(8)
    ok(f"Backup angelegt (Archiv {head[5]}.{head[6]})", head[:5] == b"PGDMP" and os.path.getsize(p) > 1000)

    # Aendern: Zeilen loeschen, aendern, neue Seite, Probe-Tabelle weg
    q("DELETE FROM note WHERE id = 2")
    q("UPDATE note SET title = 'geaendert' WHERE id = 1")
    q("INSERT INTO note (id, uid, title, created, updated) VALUES (9, 'sich-a', 'neu', 1, 1)")
    q("INSERT INTO note_derived (note_id, search) VALUES (9, 'neu')")
    q("DROP TABLE sich_probe")
    db.restore(p)
    ok("Zurueckspielen (pg_restore 17 gegen diesen Server)", True)
    ok("Daten wieder wie beim Backup (Seiten, abgeleitete Daten, Verweise)", state() == before
       and q("SELECT count(*) FROM note_link")[0][0] == 3)
    ok("Steuerzeilen-aehnliche Inhalte unveraendert", q("SELECT txt FROM sich_probe ORDER BY id") == [(t,) for t in TRICKY]
       and q("SELECT txt FROM sich_eine ORDER BY txt") == [("COMMIT;",), ("SET transaction_timeout = 0;",)]
       and q("SELECT prosrc FROM pg_proc WHERE proname = 'sich_f'") == [(FBODY,)])
    ok("Schema-Stand wie vorher", db.schema_version() == db.SCHEMA_VERSION)
    db.restore(p)
    ok("Zweimal hintereinander zurueckspielen", state() == before)

    # Kaputtes Backup: Fehler, nichts geaendert (alles in einer Transaktion, COMMIT erst nach fehlerfreiem pg_restore)
    q("UPDATE note SET title = 'nach dem Backup' WHERE id = 3")
    after = state()
    with open(p, "rb") as f:
        data = f.read()
    for name, blob in (("abgeschnitten", data[:len(data) * 2 // 3]), ("kein Backup", b"kein backup\n" * 50)):
        bad = p + "." + name.replace(" ", "-")
        made.append(bad)
        with open(bad, "wb") as f:
            f.write(blob)
        try:
            db.restore(bad)
            ok(f"Kaputtes Backup ({name}) -> Fehler", False)
        except RuntimeError as ex:
            ok(f"Kaputtes Backup ({name}) -> Fehler, nichts geaendert", "nichts geaendert" in str(ex) and state() == after)
    try:
        db.restore(p + ".gibtsnicht")
        ok("Fehlende Datei -> Fehler", False)
    except RuntimeError as ex:
        ok("Fehlende Datei -> Fehler", "nicht gefunden" in str(ex))

    # Backup von vor 2.2: ohne note_derived/note_link/note_stale - einspielbar auf einer Datenbank, die sie hat
    q("DROP TABLE note_link, note_stale, note_derived")
    old = db.backup("sicherung-vor-2-2")
    made.append(old)
    db.init_schema()                           # wie der Start einer 2.2: Tabellen wieder da, mit Fremdschluesseln auf note
    q("INSERT INTO note_derived (note_id, search) VALUES (1, 'x')")
    q("INSERT INTO note_link (src, dst) VALUES (1, 3)")
    db.restore(old)
    tabs = q("SELECT count(*) FROM information_schema.tables WHERE table_name IN ('note_link','note_stale','note_derived')")
    ok("Backup von vor 2.2 einspielbar (abgeleitete Tabellen vorher weg)", tabs[0][0] == 0
       and q("SELECT title FROM note WHERE id = 3")[0][0] == "nach dem Backup")
    db.init_schema()
    ok("Naechster Start legt die abgeleiteten Tabellen wieder an", db.schema_version() == db.SCHEMA_VERSION
       and q("SELECT to_regclass('public.note_derived')")[0][0] is not None)

    # Kommandozeile wie in der Anleitung (python run.py restore <datei>)
    import subprocess
    q("UPDATE note SET title = 'per run.py weg' WHERE id = 1")
    env = dict(os.environ, DATABASE_URL=URL)
    r = subprocess.run([sys.executable, "run.py", "restore", p], cwd=os.path.dirname(os.path.abspath(db.__file__)),
                       env=env, capture_output=True, text=True, timeout=300)
    ok(f"python run.py restore ({r.returncode}) {(r.stderr or '').strip()[-120:]}", r.returncode == 0
       and "Zurueckgespielt" in r.stdout and q("SELECT title FROM note WHERE id = 1")[0][0] == "Seite 1")
finally:
    db.DATABASE_URL = REAL
    for f in made:
        try:
            os.remove(f)
        except OSError:
            pass
    q(f"DROP DATABASE IF EXISTS {NAME} WITH (FORCE)", url=REAL)
print(f"\nSICHERUNG {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
sys.exit(0 if res and all(res) else 1)
