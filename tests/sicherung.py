"""Backup und Zurueckspielen (db.backup / db.restore, "python run.py restore") von Anfang bis Ende - in einer eigenen
Wegwerf-Datenbank auf demselben Postgres-Server, die echte Datenbank bleibt unberuehrt.

Prueft vor allem: pg_restore aus dem Image (17) gegen den Server der Standard-Compose (postgres:16) - frueher Abbruch an
"SET transaction_timeout"; Inhalte, die wie Steuerzeilen aussehen, bleiben erhalten; ein Backup von vor 2.2 (ohne die
abgeleiteten Tabellen) laesst sich einspielen; ein kaputtes Backup aendert nichts; Rueckweg 2.5 -> 2.6 -> Backup von vor
dem Update -> 2.5 -> 2.6 mit der db.py von v2.5.0 (Bugs-DB 2454: keine 2.6-Tabellen bleiben stehen); ein fremdes
Archiv (Custom-Dump einer anderen Datenbank) wird vor dem Einspielen abgelehnt, nichts geaendert (Bugs-DB 2461)."""
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
    for name, blob in (("abgeschnitten", data[:len(data) * 2 // 3]), ("kein Backup", b"kein backup\n" * 50),
                       ("nur Kopf", data[:64]), ("leer", b"")):
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

    # Fremdes Archiv (Bugs-DB 2461): ein gueltiges Custom-Dump einer anderen Datenbank -> Abbruch VOR dem Einspielen,
    # nichts geaendert (ohne die Pruefung leerte _RESTORE_DROP alle Saga-Tabellen samt schema_version -> leere Instanz)
    NAMEF = NAME + "_fremd"
    URLF = make_conninfo(REAL, dbname=NAMEF)
    q(f"CREATE DATABASE {NAMEF}", url=REAL)
    try:
        q("CREATE TABLE kunde (id INT PRIMARY KEY, name TEXT)", url=URLF)
        q("INSERT INTO kunde VALUES (1, 'fremd')", url=URLF)
        db.DATABASE_URL = URLF
        fremd = db.backup("sicherung-fremd")
        made.append(fremd)
        q("CREATE TABLE note (id INT)", url=URLF)          # fast wie Saga: note und schema_version, sonst nichts
        q("CREATE TABLE schema_version (v INT)", url=URLF)
        halb = db.backup("sicherung-halb")
        made.append(halb)
    finally:
        db.DATABASE_URL = URL
        q(f"DROP DATABASE IF EXISTS {NAMEF} WITH (FORCE)", url=REAL)

    def ntab(url=None):
        return q("SELECT count(*) FROM pg_tables WHERE schemaname='public'", url=url)[0][0]
    t0, v0 = ntab(), db.schema_version()
    for name, f, want in (("andere Datenbank", fremd, "schema_version, users, setting, note"),
                          ("nur note/schema_version", halb, "users, setting")):
        try:
            db.restore(f)
            ok(f"Fremdes Archiv ({name}) -> Abbruch", False)
        except RuntimeError as ex:
            ok(f"Fremdes Archiv ({name}) -> Abbruch mit Meldung, nichts geaendert ({str(ex)[:90]}...)",
               "kein Saga-Backup" in str(ex) and "nichts geaendert" in str(ex) and want in str(ex) and state() == after
               and ntab() == t0 and db.schema_version() == v0 and q("SELECT to_regclass('public.kunde')")[0][0] is None)
    # Leere Ziel-Datenbank wie bei run.py backup-pruefen: fremdes Archiv abgelehnt (nichts angelegt), echtes geht
    NAMEL = NAME + "_leer"
    URLL = make_conninfo(REAL, dbname=NAMEL)
    q(f"CREATE DATABASE {NAMEL}", url=REAL)
    try:
        db.DATABASE_URL = URLL
        try:
            db.restore(fremd)
            okf = False
        except RuntimeError as ex:
            okf = "kein Saga-Backup" in str(ex)
        okf = okf and ntab(URLL) == 0
        db.restore(p)
        ok("Leere Datenbank (wie backup-pruefen): fremdes Archiv abgelehnt, Saga-Backup eingespielt",
           okf and q("SELECT count(*) FROM note", url=URLL)[0][0] == 3 and db.schema_version() == db.SCHEMA_VERSION)
    finally:
        db.DATABASE_URL = URL
        q(f"DROP DATABASE IF EXISTS {NAMEL} WITH (FORCE)", url=REAL)
    # Echtes Saga-Backup geht nach den Ablehnungen normal durch (danach wieder der Stand "nach dem Backup")
    db.restore(p)
    ok("Saga-Backup danach eingespielt", state() == before)
    q("UPDATE note SET title = 'nach dem Backup' WHERE id = 3")

    # _restore_tables: CREATE TABLE auch klein geschrieben, Name wie in Postgres (klein) - Bugs-DB 2461
    k = max(db.MIGRATIONS) + 1000
    db.MIGRATIONS[k] = "create table if not exists Sich_Klein_T (x int)"
    try:
        ok("_restore_tables erkennt klein geschriebenes CREATE TABLE", "sich_klein_t" in db._restore_tables())
    finally:
        del db.MIGRATIONS[k]

    # Backup von vor 2.2: ohne note_derived/note_link/note_stale - einspielbar auf einer Datenbank, die sie hat
    q("DROP TABLE note_link, note_stale, note_derived")
    for t in ("note_tseq_t ON note", "note_share_tseq_t ON note_share", "note_comment_tseq_t ON note_comment",
              "note_seen_tseq_t ON note_comment_seen"):
        q(f"DROP TRIGGER IF EXISTS {t}")       # vor 2.2 auch kein Seitenbaum-Abgleich
    q("DROP TABLE note_tseq, note_tseq_meta")
    old = db.backup("sicherung-vor-2-2")
    made.append(old)
    db.init_schema()                           # wie der Start einer 2.2: Tabellen wieder da, mit Fremdschluesseln auf note
    q("INSERT INTO note_derived (note_id, search) VALUES (1, 'x')")
    q("INSERT INTO note_link (src, dst) VALUES (1, 3)")
    db.restore(old)
    tabs = q("SELECT count(*) FROM information_schema.tables WHERE table_name IN ('note_link','note_stale','note_derived')")
    ok("Backup von vor 2.2 einspielbar (abgeleitete Tabellen vorher weg)", tabs[0][0] == 0
       and q("SELECT title FROM note WHERE id = 3")[0][0] == "nach dem Backup")
    ep0 = q("SELECT to_regclass('public.note_tseq'), to_regclass('public.note_tseq_meta')")[0]
    db.init_schema()
    ok("Naechster Start legt die abgeleiteten Tabellen wieder an", db.schema_version() == db.SCHEMA_VERSION
       and q("SELECT to_regclass('public.note_derived')")[0][0] is not None)
    # Seitenbaum-Abgleich (2.2): Stand nach dem Zurueckspielen weg, naechster Start legt ihn mit neuer Kennung an
    ep1 = q("SELECT v FROM note_tseq_meta WHERE k='epoch'")
    trg = q("SELECT count(*) FROM pg_trigger WHERE tgname = 'note_tseq_t'")[0][0]
    ok(f"Abgleich-Stand: nach Zurueckspielen weg, naechster Start legt ihn an (Kennung, Trigger) {ep0} {len(ep1)} {trg}",
       ep0 == (None, None) and len(ep1) == 1 and trg == 1)

    # Backup von vor 2.3 E8: ohne chat_invite (Fremdschluessel auf chat) - einspielbar auf einer Datenbank, die sie hat
    q("DROP TABLE chat_invite")
    old23 = db.backup("sicherung-vor-2-3-e8")
    made.append(old23)
    db.init_schema()
    ok("chat_invite nach dem Start da", q("SELECT to_regclass('public.chat_invite')")[0][0] is not None)
    try:
        db.restore(old23)
        okr = True
    except RuntimeError as ex:
        okr, _ = False, print(str(ex)[:300])
    db.init_schema()
    ok("Backup ohne chat_invite einspielbar, naechster Start legt sie an",
       okr and q("SELECT to_regclass('public.chat_invite')")[0][0] is not None)

    # Backup von vor 2.4 E4: ohne chat_call_leg/chat_call_invite (Fremdschluessel auf chat_call) - einspielbar auf einer
    # Datenbank, die sie (mit Zeilen) hat; der naechste Start legt sie wieder an
    q("DROP TABLE chat_call_leg, chat_call_invite")
    old24 = db.backup("sicherung-vor-2-4-e4")
    made.append(old24)
    db.init_schema()
    q("INSERT INTO chat (kind, created) VALUES ('dm', 0)")
    q("INSERT INTO chat_call (id, chat_id, caller, callee, caller_device, media, state, created) "
      "SELECT 'f' || repeat('0', 31), max(id), 'a', 'b', 'd', 'audio', 'active', 0 FROM chat")
    q("INSERT INTO chat_call_leg (call_id, device, who) VALUES ('f' || repeat('0', 31), 'd', 'a')")
    q("INSERT INTO chat_call_invite (call_id, who, state, created) VALUES ('f' || repeat('0', 31), 'b', 'ringing', 0)")
    try:
        db.restore(old24)
        okr = True
    except RuntimeError as ex:
        okr, _ = False, print(str(ex)[:300])
    gone = q("SELECT to_regclass('public.chat_call_leg'), to_regclass('public.chat_call_invite')")[0]
    db.init_schema()
    ok(f"Backup ohne chat_call_leg/chat_call_invite einspielbar (vorher weg {gone}), naechster Start legt sie an",
       okr and gone == (None, None) and q("SELECT to_regclass('public.chat_call_leg')")[0][0] is not None
       and q("SELECT count(*) FROM chat_call_leg")[0][0] == 0)

    # Backup von vor 2.4 (Migration 14): ohne login_lock - eine laufende Anmelde-Sperre der aktuellen Datenbank bleibt
    # nach dem Zurueckspielen nicht stehen (Sicherheitsrunde 2, R1)
    q("DROP TABLE login_lock")
    oldll = db.backup("sicherung-vor-2-4-sperre")
    made.append(oldll)
    db.init_schema()
    q("INSERT INTO login_lock (username, since, until, fails, ips) VALUES ('gesperrt', 0, 9999999999, 100, 10)")
    try:
        db.restore(oldll)
        okr = True
    except RuntimeError as ex:
        okr, _ = False, print(str(ex)[:300])
    gone = q("SELECT to_regclass('public.login_lock')")[0][0]
    db.init_schema()
    ok(f"Backup ohne login_lock: laufende Sperre danach weg (vorher {gone}), naechster Start legt die Tabelle leer an",
       okr and gone is None and q("SELECT count(*) FROM login_lock")[0][0] == 0)

    # Backup von 2.4 (vor Migration 15): ohne chat_reaction (Fremdschluessel auf chat_msg) und push_stream_msg
    # (Fremdschluessel auf push_sub) - einspielbar auf einer 2.5-Datenbank mit Zeilen darin (sonst bricht DROP chat_msg
    # bzw. push_sub ab); Reaktionen und abgelegte Mitteilungen der aktuellen Datenbank sind danach weg.
    # Pruefung gesamt-srv: ein echtes 2.4-Backup kennt auch die uebrigen 2.5-Tabellen mit Fremdschluessel auf 2.4-Tabellen
    # nicht (note_fresh, row_person, row_done, note_mention, synced_ref -> note; chat_hidden, chat_pin -> chat/chat_msg) -
    # fehlten sie hier nicht, raeumte pg_restore --clean sie selbst weg und der Rueckweg aus docs/UPGRADE-2.5.md (7.) bliebe
    # ungeprueft (dort brach er an "note_pkey ... andere Objekte haengen davon ab" ab)
    NEW25 = ("chat_reaction", "push_stream_msg", "note_fresh", "row_person", "row_done", "note_mention", "synced_ref",
             "chat_hidden", "chat_pin")
    q("DROP TABLE " + ", ".join(NEW25))
    old25 = db.backup("sicherung-vor-2-5")
    made.append(old25)
    db.init_schema()
    q("INSERT INTO chat (kind, created) VALUES ('dm', 0)")
    q("INSERT INTO chat_msg (id, chat_id, sender_device, ts, created, body_ct, sig) "
      "SELECT 'm-rx-' || max(id), max(id), 'd', 0, 0, 'x', 'x' FROM chat")
    q("INSERT INTO chat_reaction (msg_seq, reactor, dev, ts, ct, sig, at) SELECT max(seq), '~a@x', 'd', 0, 'x', 'x', 0 FROM chat_msg")
    q("INSERT INTO push_sub (uid, kind, endpoint, created) VALUES ('a', 'stream', 'stream:rx-test', 0)")
    q("INSERT INTO push_stream_msg (sub_id, body, created, expires) SELECT max(id), '\\x00'::bytea, 0, 1 FROM push_sub")
    try:
        db.restore(old25)
        okr = True
    except RuntimeError as ex:
        okr, _ = False, print(str(ex)[:300])
    gone = q("SELECT " + ", ".join(f"to_regclass('public.{t}')" for t in NEW25))[0]
    db.init_schema()
    ok(f"Backup von 2.4 ohne die neuen 2.5-Tabellen mit Fremdschluessel einspielbar (vorher weg {gone}), naechster Start legt sie leer an",
       okr and all(x is None for x in gone) and q("SELECT count(*) FROM chat_reaction")[0][0] == 0
       and q("SELECT count(*) FROM push_stream_msg")[0][0] == 0 and q("SELECT count(*) FROM note_fresh")[0][0] == 0)

    # Jede Tabelle, die eine laufende Saga anlegt (SCHEMA, Migrationen und Bereiche beim Start - deshalb die echte
    # Datenbank des Stacks, nur gelesen), entfernt das Zurueckspielen vorher (_RESTORE_DROP, Bugs-DB 2454): sonst blieben
    # Tabellen, die ein aelteres Backup nicht kennt, mit neueren Daten stehen
    live = {r[0] for r in q("SELECT tablename FROM pg_tables WHERE schemaname='public'", url=REAL)}
    miss = sorted(live - set(db._restore_tables()))
    ok(f"Alle {len(live)} Tabellen der laufenden Saga stehen in _RESTORE_DROP {miss}", live and not miss)
    ok("_RESTORE_DROP ohne CASCADE (Fremdes bricht ab statt mitzugehen)", b"CASCADE" not in db._RESTORE_DROP)

    # Kommandozeile wie in der Anleitung (python run.py restore <datei>)
    import subprocess
    q("UPDATE note SET title = 'per run.py weg' WHERE id = 1")
    env = dict(os.environ, DATABASE_URL=URL)
    r = subprocess.run([sys.executable, "run.py", "restore", p], cwd=os.path.dirname(os.path.abspath(db.__file__)),
                       env=env, capture_output=True, text=True, timeout=300)
    ok(f"python run.py restore ({r.returncode}) {(r.stderr or '').strip()[-120:]}", r.returncode == 0
       and "Zurueckgespielt" in r.stdout and q("SELECT title FROM note WHERE id = 1")[0][0] == "Seite 1")
    q("UPDATE note SET title = 'per run.py fremd' WHERE id = 1")
    r = subprocess.run([sys.executable, "run.py", "restore", fremd], cwd=os.path.dirname(os.path.abspath(db.__file__)),
                       env=env, capture_output=True, text=True, timeout=300)
    ok(f"python run.py restore <fremdes Archiv> ({r.returncode}) {(r.stderr or '').strip()[-120:]}", r.returncode != 0
       and "kein Saga-Backup" in r.stderr and "Traceback" not in r.stderr
       and q("SELECT title FROM note WHERE id = 1")[0][0] == "per run.py fremd")

    # 2.5 -> 2.6 -> Backup von vor dem Update zurueckspielen -> 2.5 startet -> wieder 2.6 (Bugs-DB 2454, Rueckweg aus
    # docs/UPGRADE-2.6.md Abschnitt 7) in einer zweiten Wegwerf-Datenbank. Die 2.5-Seite ist die echte db.py von v2.5.0
    # (git show v2.5.0:db.py oder SAGA_V250_DB=<pfad>): Schema anlegen wie ihr Start, SchemaTooNew, ihr restore.
    import importlib.util, subprocess, tempfile
    here = os.path.dirname(os.path.abspath(db.__file__))
    v25, v25_src = None, os.environ.get("SAGA_V250_DB") or ""
    with tempfile.TemporaryDirectory() as td:
        if not v25_src:
            r = subprocess.run(["git", "show", "v2.5.0:db.py"], cwd=here, capture_output=True, timeout=60)
            if r.returncode == 0:
                v25_src = os.path.join(td, "db_v250.py")
                with open(v25_src, "wb") as f:
                    f.write(r.stdout)
        if v25_src:
            spec = importlib.util.spec_from_file_location("db_v250", v25_src)
            v25 = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(v25)
    NEW26 = ("contact", "contact_addr", "job", "job_step", "job_wait", "job_event", "job_change", "user_status", "ai_units")
    COL26 = (("note", "updated_actor"), ("note_version", "actor_kind"), ("note_version", "actor"),
             ("note_version", "on_behalf_of"), ("notification", "actor_kind"), ("notification", "actor"),
             ("notification", "on_behalf_of"), ("chat_call", "hold"), ("push_sub", "topic"), ("chat_call", "lim"))
    tag = "v2.5.0" if v25 else "2.5 SIMULIERT (kein v2.5.0)"
    print(f"Rueckweg 2.6 -> 2.5 mit {tag}" + (f" ({v25.SCHEMA_VERSION})" if v25 else ""))
    NAME2 = NAME + "_rw"
    URL2 = make_conninfo(REAL, dbname=NAME2)
    q(f"CREATE DATABASE {NAME2}", url=REAL)
    try:
        def start25():
            if v25:
                v25.DATABASE_URL = URL2
                v25.init_schema()
                return
            # ohne v2.5.0: Stand 35 aus dem aktuellen Schema nachbauen (2.6-Tabellen und -Spalten weg)
            db.DATABASE_URL = URL2
            db.init_schema()
            q("DROP TABLE " + ", ".join(NEW26), url=URL2)
            for t, col in COL26:
                q(f"ALTER TABLE {t} DROP COLUMN {col}", url=URL2)
            q("DELETE FROM schema_version WHERE v > 35", url=URL2)

        def shape():
            """Aufbau der Datenbank ohne Daten: Spalten, Indizes, Constraints, Trigger, Sequenzen, Typen, Funktionen"""
            return tuple(frozenset(q(s, url=URL2)) for s in (
                "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema='public'",
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public'",
                "SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE connamespace='public'::regnamespace",
                "SELECT tgname, tgrelid::regclass::text FROM pg_trigger WHERE NOT tgisinternal",
                "SELECT sequencename FROM pg_sequences WHERE schemaname='public'",
                "SELECT typname, typtype FROM pg_type WHERE typnamespace='public'::regnamespace AND typrelid = 0 "
                "AND typtype <> 'b'",
                "SELECT proname FROM pg_proc WHERE pronamespace='public'::regnamespace"))

        def ver():
            return q("SELECT max(v) FROM schema_version", url=URL2)[0][0]

        def left26():
            return [t for t in NEW26 if q(f"SELECT to_regclass('public.{t}')", url=URL2)[0][0]]

        def fill26():
            """Daten in allem, was 2.6 neu hat (Tabellen der Migrationen 36-40 und neue Spalten)"""
            for s in ("INSERT INTO contact (uid, own_name, created, updated) VALUES ('rw-a', 'nach dem Update', 1, 1)",
                      "INSERT INTO contact_addr (contact_id, uid, kind, value, created) SELECT max(id), 'rw-a', 'email', "
                      "'x@example.org', 1 FROM contact",
                      "INSERT INTO job (uid, kind, state, created, updated, next_at) VALUES ('rw-a', 'test', 'wartet', 1, 1, 1)",
                      "INSERT INTO job_step (job_id, n, tool) SELECT max(id), 1, 'x' FROM job",
                      "INSERT INTO job_wait (job_id, step_n, on_kind, ref) SELECT max(id), 1, 'x', 'y' FROM job",
                      "INSERT INTO job_event (job_id, at, kind) SELECT max(id), 1, 'x' FROM job",
                      "INSERT INTO job_change (job_id, step_n, idem_key, target, at) SELECT max(id), 1, 'k', 't', 1 FROM job",
                      "INSERT INTO user_status (uid, state, since, updated) VALUES ('rw-a', 'dnd', 1, 1)",
                      "INSERT INTO ai_units (uid, day, pool, units) VALUES ('rw-a', '2026-11-26', 'ask', 7)",
                      "UPDATE note SET title='nach dem Update', updated_actor='svc:kollegin' WHERE id=1",
                      "UPDATE push_sub SET topic='org.example.saga'",
                      "INSERT INTO setting (k, v) VALUES ('contact_fill', '1') ON CONFLICT (k) DO NOTHING"):
                q(s, url=URL2)

        os.environ["SAGA_SKIP_BACKUP"] = "1"
        start25()
        q("INSERT INTO note (id, uid, title, created, updated) VALUES (1, 'rw-a', 'vor dem Update', 1, 1)", url=URL2)
        q("INSERT INTO push_sub (uid, kind, endpoint, created) VALUES ('rw-a', 'apns', 'apns:production:rw', 1)", url=URL2)
        shape25 = shape()
        ok(f"2.5 ({tag}): Schema-Stand 35, keine 2.6-Tabellen", ver() == 35 and not left26())

        # Update auf 2.6 wie beim Start: Backup vor-schema-40 VOR den Migrationen
        db.DATABASE_URL = URL2
        bdir = db.BACKUP_DIR
        pre = set(os.listdir(bdir)) if os.path.isdir(bdir) else set()
        os.environ.pop("SAGA_SKIP_BACKUP")
        try:
            db.init_schema()
        finally:
            os.environ["SAGA_SKIP_BACKUP"] = "1"
        new = sorted(f for f in set(os.listdir(bdir)) - pre if f.endswith(f"-vor-schema-{db.SCHEMA_VERSION}.dump"))
        made.extend(os.path.join(bdir, f) for f in new)
        bk = os.path.join(bdir, new[-1]) if new else None
        shape26 = shape()
        ok(f"Update 35 -> {db.SCHEMA_VERSION}: Backup {new[-1] if new else '-'} angelegt, 2.6-Tabellen da",
           bk and ver() == db.SCHEMA_VERSION and left26() == list(NEW26))
        fill26()
        if v25:
            try:
                v25.init_schema()
                ok("2.5 auf der 2.6-Datenbank -> SchemaTooNew", False)
            except v25.SchemaTooNew as ex:
                ok("2.5 auf der 2.6-Datenbank -> SchemaTooNew", "kennt nur 35" in str(ex))

        # Rueckweg wie in der Anleitung: mit 2.6 zurueckspielen, dann 2.5 starten
        db.restore(bk)
        ok(f"Mit 2.6 zurueckgespielt: Schema-Stand 35, keine 2.6-Tabellen (uebrig {left26()})",
           ver() == 35 and not left26())
        ok("Aufbau wieder genau wie 2.5 (Spalten, Indizes, Constraints, Trigger, Sequenzen, Typen, Funktionen)",
           shape() == shape25)
        ok("Daten wie vor dem Update", q("SELECT title FROM note WHERE id=1", url=URL2) == [("vor dem Update",)]
           and not q("SELECT 1 FROM setting WHERE k='contact_fill'", url=URL2))
        if v25:                                # simuliert gibt es keinen 2.5-Start (start25 baute nur den Stand nach)
            try:
                start25()
                okr = True
            except Exception as ex:            # SchemaTooNew o. ae.
                okr, _ = False, print(str(ex)[:300])
            ok("2.5 (v2.5.0) startet danach, Aufbau unveraendert", okr and ver() == 35 and shape() == shape25)

        # Wieder auf 2.6: Migrationen 36-40 laufen erneut, 2.6-Tabellen leer (kein Stand von vor dem Zurueckspielen)
        db.DATABASE_URL = URL2
        db.init_schema()
        ok(f"Wieder auf 2.6: Schema-Stand {db.SCHEMA_VERSION}, Aufbau wie beim ersten Update",
           ver() == db.SCHEMA_VERSION and shape() == shape26)
        ok("Wieder auf 2.6: 2.6-Tabellen leer, Seite wie vor dem Update",
           all(q(f"SELECT count(*) FROM {t}", url=URL2)[0][0] == 0 for t in NEW26)
           and q("SELECT title, updated_actor FROM note WHERE id=1", url=URL2) == [("vor dem Update", None)]
           and q("SELECT topic FROM push_sub", url=URL2) == [(None,)])

        if v25:
            # Mit 2.5 zurueckgespielt (gegen die Anleitung): klappt (keine Fremdschluessel neuer Tabellen auf alte), laesst
            # aber die 2.6-Tabellen samt Daten stehen - 2.5 kennt sie nicht. Nochmal mit 2.6 zurueckgespielt: weg.
            fill26()
            v25.DATABASE_URL = URL2
            try:
                v25.restore(bk)
                okr = True
            except RuntimeError as ex:
                okr, _ = False, print(str(ex)[:300])
            stay = left26()
            print(f"  (mit v2.5.0 zurueckgespielt: stehen geblieben {stay})")
            ok("Mit 2.5 zurueckgespielt: klappt, Schema-Stand 35 (2.6-Tabellen bleiben stehen - deshalb mit 2.6)",
               okr and ver() == 35 and stay == list(NEW26))
            db.DATABASE_URL = URL2
            db.restore(bk)
            ok("Danach mit 2.6 zurueckgespielt: 2.6-Tabellen weg, Aufbau wie 2.5", not left26() and shape() == shape25)
    finally:
        db.DATABASE_URL = URL
        q(f"DROP DATABASE IF EXISTS {NAME2} WITH (FORCE)", url=REAL)
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
