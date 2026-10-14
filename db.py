"""Postgres-Zugriff mit der Schnittstelle, die der Rest des Codes von sqlite3 kennt.

    with _db() as c, c:                      # Verbindung aus dem Pool + Transaktion (commit/rollback)
        c.execute("SELECT ... WHERE uid=?", (uid,)).fetchone()
        cur = c.execute("INSERT INTO note (...) VALUES (?,?)", (...)); cur.lastrowid

Uebersetzt wird einmal pro SQL-Text: ? -> %s, instr() -> strpos(), INSERT OR REPLACE -> ON CONFLICT,
INSERT in Tabellen mit id -> RETURNING id. Schema-Befehle (CREATE/ALTER/PRAGMA) aus dem alten Code sind
wirkungslos: das Schema legt init_schema() beim Start an.
"""
import atexit
import os
import re
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from functools import lru_cache

import psycopg
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL") or (
    "postgresql://{u}:{p}@{h}:{port}/{d}".format(
        u=os.environ.get("POSTGRES_USER", "saga"), p=os.environ.get("POSTGRES_PASSWORD", ""),
        h=os.environ.get("POSTGRES_HOST", "db"), port=os.environ.get("POSTGRES_PORT", "5432"),
        d=os.environ.get("POSTGRES_DB", "saga")))

_pool = None
_pool_lock = threading.Lock()

# Tabellen mit Ganzzahl-Schluessel "id" -> INSERT liefert die neue id (cur.lastrowid)
ID_TABLES = {"task", "note", "note_version", "device_login", "cal_account", "app_token", "users", "groups",
             "service", "bookmark", "note_share", "note_comment", "notification", "ai_history"}
# INSERT OR REPLACE: Konfliktspalten je Tabelle
UPSERT_KEYS = {"immich_key": ("uid",), "user_kv": ("uid", "k"), "fav": ("uid", "grp"), "setting": ("k",),
               "note_share": ("note_id", "target_type", "target"), "note_comment_seen": ("uid", "thread_id")}


def pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL", "12")),
                                   kwargs={"autocommit": False}, open=True, timeout=15)
        return _pool


@atexit.register
def _close_pool():
    if _pool is not None:
        _pool.close(timeout=2)


def wait_ready(seconds=90):
    """Beim Start: warten, bis Postgres antwortet (Compose startet beide gleichzeitig)."""
    end = time.time() + seconds
    while True:
        try:
            with psycopg.connect(DATABASE_URL, connect_timeout=3) as c:
                c.execute("SELECT 1")
                return
        except psycopg.OperationalError as ex:
            if time.time() > end:
                raise SystemExit(f"Saga: Datenbank nicht erreichbar: {ex}")
            time.sleep(1.5)


_SKIP = re.compile(r"^\s*(CREATE\s|ALTER\s|PRAGMA\s|DROP\s+INDEX)", re.I)
_UPSERT = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]*)\)", re.I)
_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", re.I)
_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+(\w+)", re.I)


@lru_cache(maxsize=2048)
def translate(sql):
    """-> (pg_sql, skip, returning_id)"""
    if _SKIP.match(sql):
        return "", True, False
    s = sql
    s = re.sub(r"\binstr\(", "strpos(", s, flags=re.I)
    m = _UPSERT.match(s)
    if m:
        table, cols = m.group(1).lower(), [c.strip() for c in m.group(2).split(",")]
        keys = UPSERT_KEYS.get(table)
        if not keys:
            raise ValueError(f"INSERT OR REPLACE ohne bekannte Schluessel: {table}")
        upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in keys) or f"{keys[0]}=EXCLUDED.{keys[0]}"
        s = re.sub(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.I)
        s = s.rstrip().rstrip(";") + f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {upd}"
    elif _IGNORE.match(s):
        s = _IGNORE.sub("INSERT INTO ", s).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    returning = False
    m = _INSERT.match(s)
    if m and m.group(1).lower() in ID_TABLES and "returning" not in s.lower():
        s = s.rstrip().rstrip(";") + " RETURNING id"
        returning = True
    s = s.replace("%", "%%").replace("?", "%s")
    return s, False, returning


def _fix(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _fix_row(r):
    if r is None:
        return None
    return tuple(_fix(v) for v in r) if any(isinstance(v, Decimal) for v in r) else r


def _arg(a):
    return int(a) if isinstance(a, bool) else a


class Cursor:
    def __init__(self, cur=None, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
        self.rowcount = cur.rowcount if cur is not None else 0
        self._rows = None

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        if self._cur is None or self._cur.description is None:
            return None
        return _fix_row(self._cur.fetchone())

    def fetchall(self):
        if self._rows is not None:
            r, self._rows = self._rows, []
            return r
        if self._cur is None or self._cur.description is None:
            return []
        return [_fix_row(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


class Conn:
    """sqlite3-aehnliche Verbindung. 'with c:' = Transaktion (commit bei Erfolg, sonst rollback)."""

    def __init__(self, pg):
        self.pg = pg
        self._after = []

    def after_commit(self, fn):
        """fn() nach dem naechsten erfolgreichen Commit aufrufen (Live-Ereignisse: erst senden, wenn die Aenderung
        wirklich gespeichert ist). Bei Rollback verworfen."""
        self._after.append(fn)

    def execute(self, sql, args=()):
        q, skip, returning = translate(sql)
        if skip:
            return Cursor()
        cur = self.pg.cursor()
        cur.execute(q, tuple(_arg(a) for a in args))
        if returning:
            row = cur.fetchone()
            c = Cursor(cur, row[0] if row else None)
            c.rowcount = 1
            return c
        return Cursor(cur)

    def executemany(self, sql, seq):
        q, skip, returning = translate(sql)
        if skip:
            return Cursor()
        if returning:
            q = q[: -len(" RETURNING id")]
        cur = self.pg.cursor()
        cur.executemany(q, [tuple(_arg(a) for a in args) for args in seq])
        return Cursor(cur)

    def commit(self):
        self.pg.commit()
        fns, self._after = self._after, []
        for fn in fns:
            try:
                fn()
            except Exception as ex:  # noqa: BLE001 - Nachlauf (z. B. Live-Ereignis) darf den Commit nicht nachtraeglich scheitern lassen
                print(f"db: after_commit: {type(ex).__name__}: {ex}", flush=True)

    def rollback(self):
        self._after = []
        self.pg.rollback()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is None:
            self.commit()
        else:
            self.rollback()
        return False


@contextmanager
def connect():
    with pool().connection() as pg:
        try:
            yield Conn(pg)
        finally:
            if pg.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                pg.rollback()                    # nicht bestaetigte Aenderungen verwerfen (wie sqlite ohne commit)


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL);

-- Benutzer, Gruppen, Einstellungen -------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,              -- = uid in allen Daten-Tabellen (klein geschrieben)
    display_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT,                          -- nur lokale Konten
    source TEXT NOT NULL DEFAULT 'local',        -- local | ldap | oidc
    ext_id TEXT,                                 -- LDAP-DN bzw. OIDC "sub"
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL,
    last_login DOUBLE PRECISION,
    pw_changed DOUBLE PRECISION,
    logout_at DOUBLE PRECISION                    -- Admin: "ueberall abmelden" (aeltere Sitzungen ungueltig)
);
CREATE TABLE IF NOT EXISTS groups (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,                   -- klein geschrieben, z. B. s_nextcloud
    description TEXT NOT NULL DEFAULT '',
    created DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS user_group (          -- lokale Mitgliedschaften (Admin-Bereich)
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id BIGINT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS user_ext_group (      -- aus LDAP/OIDC bei der Anmeldung uebernommen
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS setting (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_fail (key TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS login_fail_key ON login_fail (key, ts);

-- Dienste (Admin) und Lesezeichen (je Benutzer) --------------------------------------------
CREATE TABLE IF NOT EXISTS service (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,                   -- Gruppen-Kennung + slug = Zugangsgruppe (z. B. s_nextcloud)
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '',               -- selfh.st-Symbol
    category TEXT NOT NULL DEFAULT 'Dienste',
    extra_groups TEXT NOT NULL DEFAULT '[]',     -- weitere Gruppen (JSON-Liste)
    everyone INTEGER NOT NULL DEFAULT 0,         -- fuer alle angemeldeten Benutzer
    check_status INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS bookmark (
    id BIGSERIAL PRIMARY KEY,
    uid TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Lesezeichen',
    sort DOUBLE PRECISION NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS bookmark_uid ON bookmark (uid);
CREATE TABLE IF NOT EXISTS fav (uid TEXT NOT NULL, grp TEXT NOT NULL, ts DOUBLE PRECISION, PRIMARY KEY (uid, grp));

-- Aufgaben, Seiten, Dateien (wie bisher, je uid) -------------------------------------------
CREATE TABLE IF NOT EXISTS task (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0, due TEXT, created DOUBLE PRECISION NOT NULL, done_at DOUBLE PRECISION,
    prio INTEGER NOT NULL DEFAULT 2, repeat TEXT, rep_anchor TEXT, next_id BIGINT);
CREATE INDEX IF NOT EXISTS task_uid ON task (uid);
CREATE TABLE IF NOT EXISTS note (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '', created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL,
    icon TEXT, parent_id BIGINT, format TEXT NOT NULL DEFAULT 'text', sort DOUBLE PRECISION, tags TEXT,
    pinned INTEGER NOT NULL DEFAULT 0, deleted_at DOUBLE PRECISION, is_template INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'page', schema TEXT, props TEXT, cover TEXT, cover_pos DOUBLE PRECISION,
    wide INTEGER NOT NULL DEFAULT 0, rev INTEGER NOT NULL DEFAULT 0, archived DOUBLE PRECISION,
    locked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS note_uid ON note (uid);
CREATE INDEX IF NOT EXISTS note_parent ON note (uid, parent_id);
CREATE TABLE IF NOT EXISTS note_version (
    id BIGSERIAL PRIMARY KEY, note_id BIGINT NOT NULL, uid TEXT NOT NULL,
    title TEXT, body TEXT, format TEXT, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS note_version_note ON note_version (uid, note_id);
CREATE TABLE IF NOT EXISTS file (
    id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
    size BIGINT NOT NULL, is_image INTEGER NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS file_uid ON file (uid);

-- App-Anmeldung, Schluessel, Verbindungen ----------------------------------------------------
CREATE TABLE IF NOT EXISTS pair_code (            -- Anmeldung einer App per QR-Code (einmalig, 2 Minuten)
    hash TEXT PRIMARY KEY, pid TEXT NOT NULL UNIQUE, uid TEXT NOT NULL, name TEXT, created DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL, used_by TEXT, used_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS app_code (
    hash TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT, challenge TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS device_login (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, uname TEXT, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, last_used DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS app_token (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, last_used DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS user_kv (uid TEXT NOT NULL, k TEXT NOT NULL, v TEXT, PRIMARY KEY (uid, k));
CREATE TABLE IF NOT EXISTS immich_key (
    uid TEXT PRIMARY KEY, secret TEXT NOT NULL, name TEXT, created DOUBLE PRECISION, base_url TEXT);
CREATE TABLE IF NOT EXISTS cal_account (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, label TEXT, base_url TEXT NOT NULL, username TEXT NOT NULL,
    secret TEXT NOT NULL, calendars TEXT NOT NULL DEFAULT '[]', created DOUBLE PRECISION, updated DOUBLE PRECISION);

-- Veroeffentlichen (1.2): oeffentlicher Name je Konto (nie der Anmeldename) und veroeffentlichte Seiten -----------
CREATE TABLE IF NOT EXISTS public_name (
    uid TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, created DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS note_publish (
    note_id BIGINT PRIMARY KEY, uid TEXT NOT NULL, slug TEXT NOT NULL,
    children INTEGER NOT NULL DEFAULT 0,          -- Unterseiten mit veroeffentlichen
    indexable INTEGER NOT NULL DEFAULT 0,         -- Suchmaschinen erlauben (sonst noindex)
    profile INTEGER NOT NULL DEFAULT 0,           -- Profilseite unter /@name (hoechstens eine je Konto)
    created DOUBLE PRECISION, updated DOUBLE PRECISION,
    active INTEGER NOT NULL DEFAULT 1,            -- 0 = beendet/zurueckgezogen: Einstellungen und Adresse bleiben reserviert
    withdrawn_at DOUBLE PRECISION, withdrawn_by TEXT,   -- wann/von wem beendet ('user' oder 'admin')
    UNIQUE (uid, slug));

-- Teilen und Kommentare (1.3): Freigaben einer Seite an Konten/Gruppen (gelten fuer alle Unterseiten), Kommentare an Bloecken
CREATE TABLE IF NOT EXISTS note_share (
    id BIGSERIAL PRIMARY KEY, note_id BIGINT NOT NULL, owner_uid TEXT NOT NULL,
    target_type TEXT NOT NULL,                    -- 'user' (Benutzername) | 'group' (Gruppenname, lokal oder LDAP/OIDC)
    target TEXT NOT NULL, role TEXT NOT NULL,     -- 'view' | 'comment' | 'edit'
    created DOUBLE PRECISION, created_by TEXT,
    UNIQUE (note_id, target_type, target));
CREATE INDEX IF NOT EXISTS note_share_target ON note_share (target_type, target);
CREATE INDEX IF NOT EXISTS note_share_owner ON note_share (owner_uid);
CREATE TABLE IF NOT EXISTS note_comment (
    id BIGSERIAL PRIMARY KEY, note_id BIGINT NOT NULL, owner_uid TEXT NOT NULL,   -- owner_uid = Eigentuemer der Seite
    block_id TEXT NOT NULL DEFAULT '',            -- '' = Kommentar zur ganzen Seite
    thread_id BIGINT,                             -- NULL = Anfang einer Unterhaltung, sonst deren id
    uid TEXT,                                     -- Verfasser (NULL = Konto geloescht)
    body TEXT NOT NULL,                           -- Klartext, hoechstens 2000 Zeichen
    created DOUBLE PRECISION NOT NULL, edited DOUBLE PRECISION,
    resolved_at DOUBLE PRECISION, resolved_by TEXT);
CREATE INDEX IF NOT EXISTS note_comment_note ON note_comment (note_id, thread_id);
CREATE INDEX IF NOT EXISTS note_comment_uid ON note_comment (uid, created);
CREATE INDEX IF NOT EXISTS note_comment_owner ON note_comment (owner_uid, note_id);
CREATE INDEX IF NOT EXISTS note_comment_thread ON note_comment (thread_id);   -- Antworten je Unterhaltung (Ungelesen-Abfrage)
CREATE TABLE IF NOT EXISTS note_comment_seen (
    uid TEXT NOT NULL, thread_id BIGINT NOT NULL, seen_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (uid, thread_id));

-- Benachrichtigungen (1.4): je Empfaenger (uid); Titel/Namen/Auszuege werden erst beim Lesen und nur mit Zugriff
-- ermittelt (nichts davon steht hier). 90 Tage aufbewahrt (saga.notify.purge).
CREATE TABLE IF NOT EXISTS notification (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL,
    kind TEXT NOT NULL,                           -- share.added | share.role | comment.new | comment.reply | comment.mention | publish.withdrawn
    actor_uid TEXT,                               -- wer es ausgeloest hat (NULL = Konto geloescht / Admin)
    note_id BIGINT, comment_id BIGINT,            -- betroffene Seite, (neuester) Kommentar
    data TEXT NOT NULL DEFAULT '{}',              -- JSON: Rolle, Unterhaltung, Anzahl gebuendelter Kommentare ...
    created DOUBLE PRECISION NOT NULL, read_at DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS notification_uid ON notification (uid, id DESC);
CREATE INDEX IF NOT EXISTS notification_unread ON notification (uid) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS notification_created ON notification (created);

-- KI-Assistent (1.6): Anfragen je Person und Tag (Kontingent) und der optionale Verlauf (nur wenn die Person ihn einschaltet;
-- sources = JSON {ids: [Seiten], opts}, Titel werden beim Lesen mit der aktuellen Berechtigung ermittelt)
CREATE TABLE IF NOT EXISTS ai_usage (
    uid TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (uid, day));
CREATE TABLE IF NOT EXISTS ai_history (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL,
    question TEXT NOT NULL, answer TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ai_history_uid ON ai_history (uid, id DESC);
"""
# --- Schema-Versionen und Migrationen ----------------------------------------------------------------
# SCHEMA oben ist Stand 1 (plus neue Tabellen mit CREATE TABLE IF NOT EXISTS). Alles, was bestehende
# Tabellen oder Daten aendert, kommt als nummerierte Migration hierher - SCHEMA_VERSION mit erhoehen.
# Jede Migration laeuft genau einmal, in einer Transaktion, vorher legt Saga ein Backup an.
# Frische Installation: Stand 1 anlegen, dann alle Migrationen (ohne Backup - es gibt noch nichts).
MIGRATIONS = {
    # 1.0.0: App-Schluessel gehoert zu einer Geraete-Anmeldung (Widget/Teilen); aeltere automatische
    # Schluessel ("<Geraet> (automatisch)") ihrem Geraet zuordnen
    2: """
ALTER TABLE app_token ADD COLUMN IF NOT EXISTS device_id BIGINT;
UPDATE app_token t SET device_id = (SELECT d.id FROM device_login d WHERE d.uid = t.uid AND d.name = left(t.name, length(t.name) - 14)
                                    ORDER BY d.created DESC LIMIT 1)
 WHERE t.device_id IS NULL AND t.name LIKE '% (automatisch)';
""",
    # 1.2.0: Veroeffentlichen - Beenden/Zurueckziehen behaelt Adresse und Einstellungen (wieder veroeffentlichen = wie vorher)
    3: """
ALTER TABLE note_publish ADD COLUMN IF NOT EXISTS active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE note_publish ADD COLUMN IF NOT EXISTS withdrawn_at DOUBLE PRECISION;
ALTER TABLE note_publish ADD COLUMN IF NOT EXISTS withdrawn_by TEXT;
""",
    # 1.3.0: Teilen - wer eine Seite zuletzt geaendert hat (Hinweis beim Speicher-Konflikt "X hat diese Seite gerade geaendert")
    4: """
ALTER TABLE note ADD COLUMN IF NOT EXISTS updated_by TEXT;
""",
    # 1.3.0: Teilen - wer eine Seite angelegt hat ("Angelegt von"), wer eine gesicherte Fassung geschrieben hat (Versionen)
    5: """
ALTER TABLE note ADD COLUMN IF NOT EXISTS created_by TEXT;
ALTER TABLE note_version ADD COLUMN IF NOT EXISTS author TEXT;
""",
}
SCHEMA_VERSION = max([1, *MIGRATIONS])
BACKUP_DIR = os.path.join(os.environ.get("STATE_DIR", "/data"), "backups")
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "10"))


class SchemaTooNew(RuntimeError):
    pass


def schema_version(c=None):
    """Stand der Datenbank (None = noch leer)"""
    if c is None:
        with psycopg.connect(DATABASE_URL) as c2:
            return schema_version(c2)
    if c.execute("SELECT to_regclass('public.schema_version')").fetchone()[0] is None:
        return None
    return c.execute("SELECT max(v) FROM schema_version").fetchone()[0]


def _pg_env():
    """Verbindungsdaten fuer pg_dump/pg_restore als Umgebung (Passwort nicht in der Prozessliste)."""
    from psycopg.conninfo import conninfo_to_dict
    d = conninfo_to_dict(DATABASE_URL)
    env = dict(os.environ)
    for k, e in (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"), ("password", "PGPASSWORD"), ("dbname", "PGDATABASE")):
        if d.get(k):
            env[e] = str(d[k])
    return env


def backup(label="manuell"):
    """Datenbank-Backup (pg_dump, komprimiert) nach STATE_DIR/backups; die neuesten BACKUP_KEEP bleiben."""
    import subprocess
    os.makedirs(BACKUP_DIR, exist_ok=True)
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label)[:40] or "backup"
    path = os.path.join(BACKUP_DIR, f"saga-{time.strftime('%Y%m%d-%H%M%S')}-{label}.dump")
    tmp = path + ".part"
    try:
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", tmp],
                       env=_pg_env(), check=True, timeout=1800, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("pg_dump fehlt - Backup nicht moeglich (im Saga-Image enthalten)")
    except subprocess.CalledProcessError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("Backup fehlgeschlagen: " + (e.stderr or b"").decode("utf-8", "replace").strip()[:300])
    os.replace(tmp, path)
    old = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("saga-") and f.endswith(".dump"))
    for f in old[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except OSError:
            pass
    return path


def restore(path):
    """Backup zurueckspielen (Saga vorher stoppen!). Ersetzt alle Tabellen durch den Stand im Backup."""
    import subprocess
    if not os.path.isfile(path):
        raise RuntimeError(f"Datei nicht gefunden: {path}")
    r = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction",
                        "--dbname", _pg_env()["PGDATABASE"], path], env=_pg_env(), capture_output=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError("Zurueckspielen fehlgeschlagen: " + r.stderr.decode("utf-8", "replace").strip()[:500])


def init_schema():
    """Schema anlegen bzw. auf den Stand dieser Version bringen. Mehrere Prozesse gleichzeitig: Advisory-Lock."""
    with psycopg.connect(DATABASE_URL, autocommit=False) as c:
        c.execute("SELECT pg_advisory_xact_lock(424242)")
        cur = schema_version(c)
        if cur is not None and cur > SCHEMA_VERSION:
            raise SchemaTooNew(f"Die Datenbank hat Schema-Stand {cur}, diese Saga-Version kennt nur {SCHEMA_VERSION}. "
                               "Bitte die neuere Saga-Version verwenden oder das Backup von vor dem Update zurueckspielen "
                               "(siehe Anleitung, Abschnitt Aktualisieren).")
        if cur is not None and cur < SCHEMA_VERSION and os.environ.get("SAGA_SKIP_BACKUP") != "1":
            # VOR allen Aenderungen sichern (pg_dump nutzt eine eigene Verbindung; hier sind noch keine Sperren gesetzt)
            p = backup(f"vor-schema-{SCHEMA_VERSION}")
            print(f"saga: Backup vor dem Update: {p}", flush=True)
        c.execute(SCHEMA)
        if cur is None:
            c.execute("INSERT INTO schema_version (v) VALUES (1)")
            cur = 1
        for v in range(cur + 1, SCHEMA_VERSION + 1):
            print(f"saga: Datenbank-Migration {v} ...", flush=True)
            c.execute(MIGRATIONS[v])
            c.execute("INSERT INTO schema_version (v) VALUES (%s)", (v,))
        c.commit()
