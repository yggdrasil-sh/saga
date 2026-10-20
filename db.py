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
             "service", "bookmark", "note_share", "note_comment", "notification", "chat",
             "push_sub", "push_queue", "ai_history", "fed_invite", "fed_log", "fed_outbox", "backup_run",
             "user_backup_run"}
# INSERT OR REPLACE: Konfliktspalten je Tabelle
UPSERT_KEYS = {"immich_key": ("uid",), "user_kv": ("uid", "k"), "fav": ("uid", "grp"), "setting": ("k",),
               "note_share": ("note_id", "target_type", "target"), "note_comment_seen": ("uid", "thread_id"),
               "chat_msg_key": ("msg_seq", "recipient"),
               "fed_person": ("principal",), "fed_inbound": ("peer_id", "note_id", "target_type", "target"),
               "fed_name_retired": ("name",)}


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


_BAD_CHARS = re.compile("[\x00\ud800-\udfff]")


def clean_str(s):
    """NUL entfernen, einzelne UTF-16-Surrogate (kaputte Emoji) durch U+FFFD ersetzen - Postgres/UTF-8 nehmen beides nicht."""
    return _BAD_CHARS.sub(lambda m: "" if m.group() == "\x00" else "\ufffd", s) if _BAD_CHARS.search(s) else s


# Einzeilige Anzeige-Texte (Titel, Namen): Steuerzeichen, Richtungs-Overrides/-Isolates (Spoofing wie "txt.exe" rueckwaerts)
# und unsichtbare Zeichen entfernen (BUG-038). ZWJ/ZWNJ (Emoji-Folgen, Persisch) und LRM/RLM bleiben.
_LINE_BAD = re.compile("[\x00-\x1f\x7f-\x9f\u200b\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\ud800-\udfff]")


def clean_line(s):
    s = str(s)
    if not _LINE_BAD.search(s):
        return s
    return _LINE_BAD.sub(lambda m: " " if m.group() in "\t\n\r\x0b\x0c\x85" else "\ufffd" if "\ud800" <= m.group() <= "\udfff" else "", s)


def _arg(a):
    if isinstance(a, str):
        return clean_str(a)                  # letzte Sicherung (BUG-007/008): sonst DataError/UnicodeEncodeError -> 500
    return int(a) if isinstance(a, bool) else a


_NUMERIC = 1700                      # Postgres-Typ numeric - nur daraus macht psycopg Decimal


class Cursor:
    def __init__(self, cur=None, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid
        self.rowcount = cur.rowcount if cur is not None else 0
        self._rows = None

    def _fix(self):
        """Muessen Zeilen umgewandelt werden (Decimal -> int/float)? Nur, wenn eine Spalte numeric ist - sonst die Zeilen
        unveraendert durchreichen (grosse Listen: keine Python-Schleife ueber jeden Wert)."""
        return any(d.type_code == _NUMERIC for d in self._cur.description)

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        if self._cur is None or self._cur.description is None:
            return None
        r = self._cur.fetchone()
        return _fix_row(r) if self._fix() else r

    def fetchall(self):
        if self._rows is not None:
            r, self._rows = self._rows, []
            return r
        if self._cur is None or self._cur.description is None:
            return []
        rows = self._cur.fetchall()
        return [_fix_row(r) for r in rows] if self._fix() else rows

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

    @contextmanager
    def savepoint(self):
        """Teil einer Transaktion, der scheitern darf: bei Fehler nur bis hierher zurueck (die Transaktion bleibt nutzbar)."""
        cur = self.pg.cursor()
        cur.execute("SAVEPOINT saga_sp")
        try:
            yield self
        except BaseException:
            cur.execute("ROLLBACK TO SAVEPOINT saga_sp")
            cur.execute("RELEASE SAVEPOINT saga_sp")
            raise
        cur.execute("RELEASE SAVEPOINT saga_sp")

    def commit(self):
        if self.pg.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
            # Ein Fehler wurde abgefangen, die Transaktion ist aber kaputt: Postgres wuerde beim COMMIT still zurueckrollen
            # - dann darf der Aufrufer nicht "ok" melden (BUG-009)
            self.rollback()
            raise RuntimeError("Transaktion abgebrochen (vorheriger Datenbankfehler) - nichts gespeichert")
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


def _local_stamp():
    """Zeitstempel fuer Dateinamen in der Zeitzone des Servers (TIMEZONE, wie im Admin-Bereich) statt UTC (BUG-091)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Berlin"))
    except Exception:  # noqa: BLE001 - unbekannte Zone: wie bisher
        return time.strftime("%Y%m%d-%H%M%S")
    return datetime.now(tz).strftime("%Y%m%d-%H%M%S")


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
CREATE TABLE IF NOT EXISTS login_lock (              -- 2.4: harte Sperre nach sehr vielen Fehlversuchen ueber viele Adressen (auth.py)
    username TEXT PRIMARY KEY, since DOUBLE PRECISION NOT NULL, until DOUBLE PRECISION NOT NULL,
    fails INTEGER NOT NULL DEFAULT 0, ips INTEGER NOT NULL DEFAULT 0);

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
-- Papierkorb (Frist, Liste, Leeren): nur die wenigen geloeschten Zeilen. Reiner Zusatz-Index, SCHEMA laeuft bei jedem
-- Start - daher ohne Migration; aeltere Versionen ignorieren ihn.
CREATE INDEX IF NOT EXISTS note_trash ON note (uid, deleted_at) WHERE deleted_at IS NOT NULL;
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

-- Chat mit Ende-zu-Ende-Verschluesselung (1.5): der Server speichert nur oeffentliche Schluessel, das mit dem
-- Wiederherstellungsschluessel verschluesselte Schluessel-Buendel, Ciphertexte und versiegelte Inhaltsschluessel
-- (Format siehe saga/chat.py). Kein Klartext, keine privaten Schluessel.
CREATE TABLE IF NOT EXISTS chat_identity (
    uid TEXT PRIMARY KEY, ident_pub TEXT NOT NULL, backup_pub TEXT NOT NULL, backup_sig TEXT NOT NULL,
    bundle TEXT NOT NULL,                         -- JSON {v, kdf, ops, mem, salt, nonce, ct} (verschluesselt)
    created DOUBLE PRECISION NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS chat_device (
    id TEXT PRIMARY KEY, uid TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    enc_pub TEXT NOT NULL, sign_pub TEXT NOT NULL, sig TEXT NOT NULL,   -- sig = Zertifikat (vom Identitaetsschluessel signiert)
    created DOUBLE PRECISION NOT NULL, last_seen DOUBLE PRECISION, revoked_at DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS chat_device_uid ON chat_device (uid);
CREATE TABLE IF NOT EXISTS chat (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,                           -- dm | group (Saga-Gruppe) | free (freie Gruppe) | page (Seite)
    dm_key TEXT UNIQUE,                           -- dm: "a|b" (sortiert)
    group_name TEXT, note_id BIGINT,
    created_by TEXT, created DOUBLE PRECISION NOT NULL,
    retention_s BIGINT NOT NULL DEFAULT 0,        -- 0 = unbegrenzt (Admin-Hoechstwert gilt zusaetzlich)
    title_seq BIGINT,                             -- free: Nachricht (kind 'title') mit dem verschluesselten Namen
    last_seq BIGINT NOT NULL DEFAULT 0, last_at DOUBLE PRECISION);
CREATE UNIQUE INDEX IF NOT EXISTS chat_group_u ON chat (group_name) WHERE kind = 'group';
CREATE UNIQUE INDEX IF NOT EXISTS chat_page_u ON chat (note_id) WHERE kind = 'page';
CREATE TABLE IF NOT EXISTS chat_member (
    chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE, uid TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',          -- admin (freie Gruppe: Ersteller) | member
    joined DOUBLE PRECISION NOT NULL, left_at DOUBLE PRECISION,
    muted INTEGER NOT NULL DEFAULT 0, last_read BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, uid));
CREATE INDEX IF NOT EXISTS chat_member_uid ON chat_member (uid);
CREATE TABLE IF NOT EXISTS chat_msg (
    seq BIGSERIAL PRIMARY KEY, id TEXT NOT NULL UNIQUE,
    chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'msg',             -- msg | title
    sender_uid TEXT, sender_device TEXT NOT NULL, ts BIGINT NOT NULL,
    created DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION,
    body_ct TEXT NOT NULL, sig TEXT NOT NULL, reply_to TEXT, size INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS chat_msg_chat ON chat_msg (chat_id, seq);
CREATE INDEX IF NOT EXISTS chat_msg_sender ON chat_msg (sender_uid, created);
CREATE INDEX IF NOT EXISTS chat_msg_expires ON chat_msg (expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS chat_msg_key (
    msg_seq BIGINT NOT NULL REFERENCES chat_msg(seq) ON DELETE CASCADE,
    recipient TEXT NOT NULL,                      -- Geraete-id oder 'backup:' + uid
    sealed TEXT NOT NULL, PRIMARY KEY (msg_seq, recipient));
CREATE INDEX IF NOT EXISTS chat_msg_key_rcpt ON chat_msg_key (recipient);
CREATE TABLE IF NOT EXISTS chat_file (
    id TEXT PRIMARY KEY, chat_id BIGINT NOT NULL, uid TEXT NOT NULL, size BIGINT NOT NULL,
    created DOUBLE PRECISION NOT NULL, msg_seq BIGINT);
CREATE INDEX IF NOT EXISTS chat_file_msg ON chat_file (msg_seq);
CREATE TABLE IF NOT EXISTS chat_link (
    id TEXT PRIMARY KEY, uid TEXT NOT NULL, device_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    enc_pub TEXT NOT NULL, sign_pub TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, expires DOUBLE PRECISION NOT NULL,
    sealed TEXT, approved DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS chat_link_uid ON chat_link (uid);
-- Anrufe (2.1, saga/calls.py): nur Zustand und Signalisierung (versiegelt fuer das Zielgeraet, der Server liest nichts).
-- Beendete Anrufe verschwinden nach kurzer Zeit; im Chat bleibt nur ein Eintrag chat_msg kind='call' (Metadaten).
CREATE TABLE IF NOT EXISTS chat_call (
    id TEXT PRIMARY KEY, chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    caller TEXT NOT NULL, callee TEXT NOT NULL, caller_device TEXT NOT NULL, callee_device TEXT,
    media TEXT NOT NULL,                          -- audio | video
    state TEXT NOT NULL,                          -- ringing | active | ended
    reason TEXT, created DOUBLE PRECISION NOT NULL, answered DOUBLE PRECISION, ended DOUBLE PRECISION,
    seen_a DOUBLE PRECISION, seen_b DOUBLE PRECISION, nsig INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS chat_call_caller ON chat_call (caller, state);
CREATE INDEX IF NOT EXISTS chat_call_callee ON chat_call (callee, state);
CREATE TABLE IF NOT EXISTS chat_call_signal (
    seq BIGSERIAL PRIMARY KEY, call_id TEXT NOT NULL REFERENCES chat_call(id) ON DELETE CASCADE,
    from_device TEXT NOT NULL, to_device TEXT NOT NULL, sealed TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS chat_call_signal_to ON chat_call_signal (call_id, to_device, seq);
-- Raum-Modell (2.4 E4/E5, saga/calls.py): ein Endpunkt je Geraet (1:1 = zwei Endpunkte, dazu Begleit-/Uebernahme-Geraete,
-- Gruppenanrufe als Mesh) und die Einladungen eines Gruppenanrufs. Nur Metadaten (wer, welches Geraet, wann); beendete
-- Anrufe verschwinden samt Endpunkten nach KEEP_S. Spalten von chat_call (lead) kommen mit Migration 12.
CREATE TABLE IF NOT EXISTS chat_call_leg (
    call_id TEXT NOT NULL REFERENCES chat_call(id) ON DELETE CASCADE,
    device TEXT NOT NULL, who TEXT NOT NULL,      -- Konto bzw. Prinzipal '@~x@host'
    peer_id INTEGER,                              -- ueber diesen Server erreichbar (NULL = Geraet hier)
    role TEXT NOT NULL DEFAULT 'member',          -- member | companion | handover
    state TEXT NOT NULL DEFAULT 'active',         -- active | left
    replaces TEXT, caps TEXT NOT NULL DEFAULT '[]', name TEXT NOT NULL DEFAULT '',
    joined DOUBLE PRECISION, left_at DOUBLE PRECISION, seen DOUBLE PRECISION, reason TEXT,
    PRIMARY KEY (call_id, device));
CREATE INDEX IF NOT EXISTS chat_call_leg_who ON chat_call_leg (who, state);
CREATE TABLE IF NOT EXISTS chat_call_invite (
    call_id TEXT NOT NULL REFERENCES chat_call(id) ON DELETE CASCADE, who TEXT NOT NULL,
    state TEXT NOT NULL,                          -- ringing | declined | joined | old (braucht Saga 2.4) | missed
    created DOUBLE PRECISION NOT NULL, PRIMARY KEY (call_id, who));

-- Mitteilungen aufs Handy/Desktop (1.7): Abos je Geraet/Browser und Warteschlange (saga.push). Gespeichert werden nur
-- Kennungen; Text entsteht erst beim Zustellen (ohne Seitentitel/Inhalte). Auftraege hoechstens 24 h.
CREATE TABLE IF NOT EXISTS push_sub (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL,
    kind TEXT NOT NULL,                           -- webpush | ntfy | unifiedpush | apns
    endpoint TEXT NOT NULL UNIQUE,                -- Push-Adresse bzw. ntfy-Topic-URL; APNs: 'apns:<production|sandbox>:<token>'
    p256dh TEXT, auth TEXT,                       -- Schluessel des Empfaengers (Web Push, UnifiedPush mit Verschluesselung)
    secret TEXT,                                  -- ntfy-Zugangstoken (verschluesselt)
    name TEXT NOT NULL DEFAULT '',
    device_id BIGINT,                             -- Geraete-Anmeldung der App (Abo endet mit ihr)
    created DOUBLE PRECISION NOT NULL, last_ok DOUBLE PRECISION, last_err TEXT, fails INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS push_sub_uid ON push_sub (uid);
CREATE TABLE IF NOT EXISTS push_queue (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, sub_id BIGINT NOT NULL,
    kind TEXT NOT NULL,                           -- Art wie notification.kind, dazu chat.message
    ref BIGINT,                                   -- notification.id
    actor TEXT, data TEXT NOT NULL DEFAULT '{}',  -- nur Kennungen (Chat)
    created DOUBLE PRECISION NOT NULL, next_at DOUBLE PRECISION NOT NULL, tries INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS push_queue_due ON push_queue (next_at);
CREATE INDEX IF NOT EXISTS push_queue_uid ON push_queue (uid);
CREATE INDEX IF NOT EXISTS push_queue_sub ON push_queue (sub_id);

-- KI-Assistent (1.6): Anfragen je Person und Tag (Kontingent) und der optionale Verlauf (nur wenn die Person ihn einschaltet;
-- sources = JSON {ids: [Seiten], opts}, Titel werden beim Lesen mit der aktuellen Berechtigung ermittelt)
CREATE TABLE IF NOT EXISTS ai_usage (
    uid TEXT NOT NULL, day TEXT NOT NULL, n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (uid, day));
CREATE TABLE IF NOT EXISTS ai_history (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL,
    question TEXT NOT NULL, answer TEXT NOT NULL, sources TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS ai_history_uid ON ai_history (uid, id DESC);

-- Foederation (2.0, saga/federation.py): Saga-Instanzen teilen Seiten untereinander. Inhalte bleiben beim Eigentuemer;
-- hier stehen nur Schluessel, Gegenstellen, Protokoll und kleine Verweise (welche Instanz hat etwas mit wem geteilt).
CREATE TABLE IF NOT EXISTS fed_self (                 -- eigener Instanz-Schluessel (Ed25519), privat verschluesselt
    id INTEGER PRIMARY KEY, public_key TEXT NOT NULL, private_key TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS fed_peer (
    id INTEGER PRIMARY KEY,                           -- 1..1023 (steckt in den negativen IDs fremder Seiten)
    host TEXT NOT NULL UNIQUE, base_url TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    public_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,                             -- pending_in (wartet auf uns) | pending_out (wartet auf die Gegenseite) | active | disconnected
    created DOUBLE PRECISION NOT NULL, confirmed_at DOUBLE PRECISION, last_seen DOUBLE PRECISION, last_error TEXT);
CREATE TABLE IF NOT EXISTS fed_invite (
    id BIGSERIAL PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, created DOUBLE PRECISION NOT NULL, expires DOUBLE PRECISION NOT NULL,
    created_by TEXT, used_at DOUBLE PRECISION, peer_id INTEGER);
CREATE TABLE IF NOT EXISTS fed_nonce (                -- Replay-Schutz: gesehene Nonces (10 Minuten)
    peer TEXT NOT NULL, nonce TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, PRIMARY KEY (peer, nonce));
CREATE INDEX IF NOT EXISTS fed_nonce_created ON fed_nonce (created);
CREATE TABLE IF NOT EXISTS fed_log (                  -- Pruefprotokoll (nur Kennungen, keine Inhalte)
    id BIGSERIAL PRIMARY KEY, peer_id INTEGER, host TEXT, ts DOUBLE PRECISION NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS fed_log_ts ON fed_log (ts);
CREATE TABLE IF NOT EXISTS fed_person (               -- Anzeigename fremder Personen ('@name@host'), fuer Kommentare/Freigaben
    principal TEXT PRIMARY KEY, peer_id INTEGER NOT NULL, display TEXT NOT NULL DEFAULT '', updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS fed_inbound (              -- Heimatserver: welche fremde Instanz hat welche Seite mit wem geteilt
    peer_id INTEGER NOT NULL, note_id BIGINT NOT NULL, owner TEXT NOT NULL,      -- owner: '@name@host' des Eigentuemers
    target_type TEXT NOT NULL, target TEXT NOT NULL,  -- 'user' (lokaler Benutzername) | 'group' (lokale Gruppe)
    role TEXT NOT NULL, created DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (peer_id, note_id, target_type, target));
CREATE INDEX IF NOT EXISTS fed_inbound_target ON fed_inbound (target_type, target);
CREATE TABLE IF NOT EXISTS fed_outbox (               -- Ereignisse an andere Instanzen (Wiederholung bei Ausfall)
    id BIGSERIAL PRIMARY KEY, peer_id INTEGER NOT NULL, payload TEXT NOT NULL, created DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, next_try DOUBLE PRECISION NOT NULL, locked_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_error TEXT);
CREATE TABLE IF NOT EXISTS fed_name_retired (         -- oeffentliche Namen, die schon foederiert waren: nie an ein anderes Konto
    name TEXT PRIMARY KEY, uid TEXT, retired DOUBLE PRECISION NOT NULL);
-- Foederation 2.3 (Protokoll 2, saga/federation.py, saga/fed_dir.py, saga/fed_keys.py). Neue Spalten in fed_peer und
-- fed_outbox kommen mit Migration 7 (unten).
CREATE TABLE IF NOT EXISTS fed_handle (               -- stabiler, pseudonymer foederierter Handle je Konto ('~' + 16 Zeichen)
    uid TEXT PRIMARY KEY, handle TEXT NOT NULL UNIQUE, created DOUBLE PRECISION NOT NULL);
CREATE SEQUENCE IF NOT EXISTS fed_dir_seq;
CREATE TABLE IF NOT EXISTS fed_dir_local (            -- eigenes Adressbuch (Abgleich-Stand je Konto; visible=0 = Grabstein)
    uid TEXT PRIMARY KEY, handle TEXT NOT NULL, name TEXT NOT NULL DEFAULT '', pub TEXT NOT NULL DEFAULT '',
    av TEXT NOT NULL DEFAULT '', groups TEXT NOT NULL DEFAULT '[]', visible INTEGER NOT NULL DEFAULT 1,
    seq BIGINT NOT NULL, updated DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS fed_dir_local_seq ON fed_dir_local (seq);
CREATE TABLE IF NOT EXISTS fed_directory (            -- Adressbuecher verbundener Instanzen (nur Anzeigename, Handle, Bild-Stand)
    peer_id INTEGER NOT NULL, handle TEXT NOT NULL,   -- handle: '~xxxx' (ohne @host)
    name TEXT NOT NULL DEFAULT '', pub TEXT NOT NULL DEFAULT '', av TEXT NOT NULL DEFAULT '',
    updated DOUBLE PRECISION NOT NULL, PRIMARY KEY (peer_id, handle));
CREATE INDEX IF NOT EXISTS fed_directory_name ON fed_directory (lower(name));
CREATE TABLE IF NOT EXISTS fed_event_seen (           -- Protokoll 2: schon verarbeitete Ereignisse (idempotent, 7 Tage)
    peer_id INTEGER NOT NULL, ev_id TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL, PRIMARY KEY (peer_id, ev_id));
CREATE INDEX IF NOT EXISTS fed_event_seen_ts ON fed_event_seen (ts);
CREATE TABLE IF NOT EXISTS fed_chain_out (            -- Protokoll 2: Reihenfolge je Kette (Absender)
    peer_id INTEGER NOT NULL, chain TEXT NOT NULL, seq BIGINT NOT NULL, PRIMARY KEY (peer_id, chain));
CREATE TABLE IF NOT EXISTS fed_chain_in (             -- Protokoll 2: zuletzt verarbeitete Nummer je Kette (Empfaenger)
    peer_id INTEGER NOT NULL, chain TEXT NOT NULL, seq BIGINT NOT NULL, ts DOUBLE PRECISION NOT NULL, PRIMARY KEY (peer_id, chain));
CREATE TABLE IF NOT EXISTS fed_chat_keys (            -- Schluesselverzeichnis: Zwischenspeicher fremder Chat-Schluessel (saga-chat/v2)
    handle TEXT PRIMARY KEY, peer_id INTEGER NOT NULL, data TEXT NOT NULL, fetched DOUBLE PRECISION NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS chat_fed_cert (            -- Handle-Zertifikat (saga-chat/v2/handle, vom Identitaetsschluessel signiert)
    uid TEXT PRIMARY KEY, handle TEXT NOT NULL, ident_pub TEXT NOT NULL, sig TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS chat_device_fed (          -- Geraete-Zertifikat v2 (saga-chat/v2/device) je Geraet
    device_id TEXT PRIMARY KEY, uid TEXT NOT NULL, fed_sig TEXT NOT NULL, created DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS chat_device_fed_uid ON chat_device_fed (uid);
-- Foederierte Chats (2.3 E2/E3, saga/fed_chat.py, saga/fed_calls.py). Neue Spalten in chat, chat_member, chat_msg und
-- chat_call kommen mit Migration 8 (unten).
CREATE TABLE IF NOT EXISTS fed_chat_block (           -- Sperren je Person ('~x@host') bzw. je Instanz ('host') eines Kontos
    uid TEXT NOT NULL, target TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, PRIMARY KEY (uid, target));
CREATE TABLE IF NOT EXISTS chat_file_fed (            -- Anhang liegt beim Absender-Server (origin); hier ggf. Zwischenkopie
    id TEXT PRIMARY KEY, peer_id INTEGER NOT NULL, cached INTEGER NOT NULL DEFAULT 0);
-- Videos im Chat (2.4 E2, saga/chat.py): fortsetzbarer Upload im Format saga-file/v2. Die Bytes schreibt saga-media (bzw.
-- ohne saga-media Python) nach STATE_DIR/chat/up/<id>; hier nur Groesse und Verfall. Abschluss -> chat_file (fmt=2).
-- Neue Spalten chat_file.fmt und chat_file_fed.have (Blockbitmap der Zwischenkopie) kommen mit Migration 11.
CREATE TABLE IF NOT EXISTS chat_upload (
    id TEXT PRIMARY KEY, uid TEXT NOT NULL, chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    size BIGINT NOT NULL, received BIGINT NOT NULL DEFAULT 0, fmt SMALLINT NOT NULL, created DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS chat_upload_uid ON chat_upload (uid);
-- Teilen vollstaendig + Gruppen-/Seiten-Chats ueber Servergrenzen (2.3 E4-E6, saga/fed_pages.py, saga/fed_chat.py).
-- Neue Spalten in file (fed_by/fed_note: Upload einer Person einer anderen Instanz, Grenze je Person) mit Migration 9.
CREATE TABLE IF NOT EXISTS fed_name_handle (          -- Freigabe an '@name@host' (oeffentlicher Name) -> Handle '~x' der Person
    principal TEXT PRIMARY KEY, peer_id INTEGER NOT NULL, handle TEXT, name TEXT NOT NULL DEFAULT '', fetched DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS fed_group_members (        -- Mitglieder einer Gruppe einer anderen Instanz (nur wenn Ziel einer Freigabe)
    peer_id INTEGER NOT NULL, grp TEXT NOT NULL, members TEXT NOT NULL, fetched DOUBLE PRECISION NOT NULL, PRIMARY KEY (peer_id, grp));
CREATE TABLE IF NOT EXISTS chat_invite (              -- 2.3 E8: wer ein Konto hier in eine Gruppe ueber Servergrenzen eingeladen hat
    chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE, uid TEXT NOT NULL,       -- (Sperren trifft genau diese Person)
    by_uid TEXT NOT NULL, PRIMARY KEY (chat_id, uid));

-- Zweiter Faktor (2.1, saga/mfa.py): Passkeys (WebAuthn), Authenticator-App (TOTP), Wiederherstellungscodes.
-- Nur oeffentliche Schluessel, das TOTP-Geheimnis verschluesselt (wie Geheimnisse der Einstellungen), Codes nur als Hash.
CREATE TABLE IF NOT EXISTS mfa_passkey (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL,
    cred_id TEXT NOT NULL UNIQUE,                     -- Kennung des Passkeys (base64url)
    public_key TEXT NOT NULL,                         -- COSE-Schluessel (base64url)
    sign_count BIGINT NOT NULL DEFAULT 0, user_handle TEXT NOT NULL, transports TEXT NOT NULL DEFAULT '[]',
    name TEXT NOT NULL DEFAULT '', aaguid TEXT, backed_up INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL, last_used DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS mfa_passkey_uid ON mfa_passkey (uid);
CREATE TABLE IF NOT EXISTS mfa_totp (
    uid TEXT PRIMARY KEY, secret TEXT NOT NULL,       -- verschluesselt (auth._enc)
    confirmed DOUBLE PRECISION,                       -- NULL = Einrichtung noch nicht mit einem Code bestaetigt
    last_step BIGINT NOT NULL DEFAULT 0,              -- zuletzt angenommener Zeitschritt (derselbe Code gilt nur einmal)
    created DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS mfa_recovery (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, hash TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, used_at DOUBLE PRECISION);
CREATE INDEX IF NOT EXISTS mfa_recovery_uid ON mfa_recovery (uid);
CREATE TABLE IF NOT EXISTS mfa_challenge (            -- WebAuthn-Challenges: einmalig, 5 Minuten, an die Sitzung gebunden
    id TEXT PRIMARY KEY, uid TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL, challenge TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);

-- Profilbilder (2.1, saga/avatars.py): Zustand je Person (lokales Konto oder fremde Person '@name@host') und die neu kodierten
-- Bilder (eigenes, zwischengespeichertes Gravatar-/SSO-/Foederations-Bild; je 64/128/256 px als WebP und PNG/JPEG)
CREATE TABLE IF NOT EXISTS avatar (
    uid TEXT PRIMARY KEY,
    ver BIGINT NOT NULL DEFAULT 0,                    -- zaehlt bei jeder Aenderung hoch (?v= in der Adresse)
    upload INTEGER NOT NULL DEFAULT 0,                -- eigenes Bild vorhanden
    gravatar INTEGER NOT NULL DEFAULT 0,              -- Person: Gravatar verwenden (2.4: ab Werk aus; Admin-Schalter gilt zusaetzlich)
    public INTEGER NOT NULL DEFAULT 0,                -- auch ohne Anmeldung (veroeffentlichte Seiten)
    g_hash TEXT, g_checked DOUBLE PRECISION, g_found INTEGER NOT NULL DEFAULT 0,      -- Gravatar (bzw. fremde Instanz) zuletzt gefragt
    sso_kind TEXT, sso_ref TEXT,                      -- 'oidc' + Adresse aus "picture" | 'ldap' + SHA-256 der Bilddaten
    sso_checked DOUBLE PRECISION, sso_found INTEGER NOT NULL DEFAULT 0,
    updated DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS avatar_img (
    uid TEXT NOT NULL, src TEXT NOT NULL,             -- upload | sso | gravatar | fed
    size INTEGER NOT NULL, fmt TEXT NOT NULL,         -- 64/128/256, 'webp' | 'std' (PNG mit Transparenz, sonst JPEG)
    mime TEXT NOT NULL, data BYTEA NOT NULL, etag TEXT NOT NULL,
    PRIMARY KEY (uid, src, size, fmt));

-- KI im Chat (2.1, saga/ai_chat.py): "nur auf Zuruf". chat_ai = im Chat erlaubt (von wem, wann); chat_ai_reply = Beleg
-- fuer eine KI-Antwort: der Server speichert nur den SHA-256 der Antwort, nie Frage oder Antwort. Die App des Fragenden
-- verschluesselt die Antwort wie jede Nachricht; beim Senden verweist sie auf den Beleg (msg_id), die anderen Clients
-- vergleichen den Hash und zeigen die Nachricht dann als Antwort der KI.
CREATE TABLE IF NOT EXISTS chat_ai (
    chat_id BIGINT PRIMARY KEY REFERENCES chat(id) ON DELETE CASCADE,
    by_uid TEXT NOT NULL, at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS chat_ai_reply (
    id TEXT PRIMARY KEY, chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    uid TEXT NOT NULL, created DOUBLE PRECISION NOT NULL,
    q_msg TEXT,                                       -- Nachricht mit der Erwaehnung (optional)
    sha256 TEXT NOT NULL,                             -- Hex, ueber den UTF-8-Text der Antwort
    msg_id TEXT);                                     -- gesendete Chat-Nachricht mit dieser Antwort (einmalig)
CREATE INDEX IF NOT EXISTS chat_ai_reply_msg ON chat_ai_reply (msg_id);

-- Chats archivieren / "fuer mich loeschen" (2.1, saga/chat.py): Stand je Person und Chat. Eigene Tabelle statt neuer
-- Spalten in chat_member: kein neuer Schema-Stand noetig, aeltere Versionen ignorieren sie gefahrlos (die versiegelten
-- Schluessel der geloeschten Nachrichten sind fuer diese Person ohnehin entfernt).
CREATE TABLE IF NOT EXISTS chat_pref (
    chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE, uid TEXT NOT NULL,
    archived DOUBLE PRECISION,                        -- archiviert seit (NULL = nicht archiviert)
    cleared_seq BIGINT NOT NULL DEFAULT 0,            -- "fuer mich geloescht": Nachrichten bis hier nie mehr ausliefern
    cleared_at DOUBLE PRECISION,
    hidden INTEGER NOT NULL DEFAULT 0,                -- Direkt-Chat aus der Liste, bis eine neue Nachricht kommt
    PRIMARY KEY (chat_id, uid));
CREATE INDEX IF NOT EXISTS chat_pref_uid ON chat_pref (uid);

-- Schneller (2.2, saga/pageindex.py): aus dem Inhalt abgeleitete Daten je Seite, damit Suche, Rueckverweise und
-- Datenbank-Zeilen nicht mehr jeden Inhalt laden und parsen. Eigene Tabellen statt neuer Spalten in note: kein neuer
-- Schema-Stand, aeltere Versionen ignorieren sie (der Trigger arbeitet dort weiter). Alles ist jederzeit neu ableitbar.
--   note_derived: search = Titel, Klartext und Tags in Kleinbuchstaben (getrennt durch das Steuerzeichen U+0001; LIKE '%..%' mit
--                 Trigramm-Index, siehe db.init_search), preview = erstes eigenes Bild im Inhalt (Galerie/Widget),
--                 tags = die Tags allein (kurz, fuer den Tag-Filter ohne den langen Suchtext)
--   note_link:    Seitenverweise im Inhalt (src verweist auf dst), wie bisher am gespeicherten JSON erkannt
--   note_stale:   veraltet/noch nicht abgeleitet - der Trigger traegt jede neue Seite und jede Aenderung an Titel, Inhalt,
--                 Format oder Tags ein (n zaehlt mit), der Hintergrund-Abgleich arbeitet sie ab. Abfragen behandeln
--                 eingetragene Seiten wie frueher direkt am Inhalt - das Ergebnis haengt nie vom Stand des Abgleichs ab.
CREATE TABLE IF NOT EXISTS note_derived (
    note_id BIGINT PRIMARY KEY REFERENCES note(id) ON DELETE CASCADE,
    search TEXT NOT NULL DEFAULT '', preview TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '');
ALTER TABLE note_derived ADD COLUMN IF NOT EXISTS tags TEXT NOT NULL DEFAULT '';     -- Vorabstand ohne Spalte (testing)
CREATE TABLE IF NOT EXISTS note_link (
    src BIGINT NOT NULL REFERENCES note(id) ON DELETE CASCADE, dst BIGINT NOT NULL, PRIMARY KEY (src, dst));
CREATE INDEX IF NOT EXISTS note_link_dst ON note_link (dst);
CREATE INDEX IF NOT EXISTS note_updated ON note (uid, updated);      -- Suche: neueste zuerst, nach 50 Treffern Schluss
CREATE TABLE IF NOT EXISTS note_stale (
    note_id BIGINT PRIMARY KEY REFERENCES note(id) ON DELETE CASCADE, uid TEXT NOT NULL, n BIGINT NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS note_stale_uid ON note_stale (uid);
CREATE OR REPLACE FUNCTION saga_note_stale() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.title IS NOT DISTINCT FROM OLD.title AND NEW.body IS NOT DISTINCT FROM OLD.body
           AND NEW.format IS NOT DISTINCT FROM OLD.format AND NEW.tags IS NOT DISTINCT FROM OLD.tags
           AND NEW.uid IS NOT DISTINCT FROM OLD.uid THEN
            RETURN NULL;
        END IF;
    END IF;
    INSERT INTO note_stale (note_id, uid) VALUES (NEW.id, NEW.uid)
        ON CONFLICT (note_id) DO UPDATE SET n = note_stale.n + 1, uid = EXCLUDED.uid;
    RETURN NULL;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'note_stale_t' AND tgrelid = 'note'::regclass) THEN
        CREATE TRIGGER note_stale_t AFTER INSERT OR UPDATE OF title, body, format, tags, uid ON note
            FOR EACH ROW EXECUTE FUNCTION saga_note_stale();
    END IF;
END $$;
-- Verschluesselte Backups auf ein WebDAV-Ziel (2.3, saga/backups.py): Verlauf der Laeufe (Status im Admin-Bereich).
-- step/error: JSON {code, p} - der Text entsteht erst beim Anzeigen (Sprache des Admins); detail: Groessen, Name im Ziel
CREATE TABLE IF NOT EXISTS backup_run (
    id BIGSERIAL PRIMARY KEY, started DOUBLE PRECISION NOT NULL, finished DOUBLE PRECISION, trigger TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 0, size BIGINT NOT NULL DEFAULT 0,
    step TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS backup_run_started ON backup_run (started);
-- Persoenliche Backups je Konto (2.3, saga/userbackup.py): Ziel/Schluessel/Zeitplan/Auswahl (cfg, JSON ohne Passwort),
-- Passwort bzw. App-Passwort verschluesselt (secret), Zustand (state: letzter Versuch/Erfolg); Verlauf je Konto (30 Laeufe)
CREATE TABLE IF NOT EXISTS user_backup (
    uid TEXT PRIMARY KEY, cfg TEXT NOT NULL DEFAULT '{}', secret TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '{}',
    updated DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS user_backup_run (
    id BIGSERIAL PRIMARY KEY, uid TEXT NOT NULL, started DOUBLE PRECISION NOT NULL, finished DOUBLE PRECISION,
    trigger TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', ok INTEGER NOT NULL DEFAULT 0, size BIGINT NOT NULL DEFAULT 0,
    step TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS user_backup_run_uid ON user_backup_run (uid, id);
-- Nextcloud-Dateien in Seiten (2.3, saga/nextcloud.py): verknuepftes Konto je Person (Login Flow v2), App-Passwort
-- verschluesselt (secret, eigene Fernet-Ableitung); used = zuletzt genutzt (Anzeige)
CREATE TABLE IF NOT EXISTS nc_account (
    uid TEXT PRIMARY KEY, server TEXT NOT NULL, login TEXT NOT NULL, dav_user TEXT NOT NULL, secret TEXT NOT NULL,
    created DOUBLE PRECISION, used DOUBLE PRECISION);
-- Gelesen-Bestaetigungen und Online-Status (2.4, saga/chat_status.py, Migration 13). chat_receipt: je Chat, lesender Person
-- (reader: Benutzername oder Prinzipal '@~x@host') und Konto DIESES Servers (viewer) nur die NEUESTE Bestaetigung, versiegelt
-- fuer den Backup-Schluessel des viewer-Kontos (crypto_box_seal) und vom Geraet der lesenden Person signiert - der Server
-- sieht nur, DASS es eine gibt (und wann). chat_seen: "zuletzt online" je Konto, nur im 5-Minuten-Raster (kein Verlauf).
CREATE TABLE IF NOT EXISTS chat_receipt (
    chat_id BIGINT NOT NULL REFERENCES chat(id) ON DELETE CASCADE, reader TEXT NOT NULL, viewer TEXT NOT NULL,
    sealed TEXT NOT NULL, at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (chat_id, viewer, reader));
CREATE INDEX IF NOT EXISTS chat_receipt_reader ON chat_receipt (reader);
CREATE TABLE IF NOT EXISTS chat_seen (uid TEXT PRIMARY KEY, at DOUBLE PRECISION NOT NULL);
-- Bestand (erster Start, Backup von vor 2.2 zurueckgespielt): alles ohne abgeleitete Daten zum Abgleich vormerken
INSERT INTO note_stale (note_id, uid) SELECT n.id, n.uid FROM note n
 WHERE NOT EXISTS (SELECT 1 FROM note_derived d WHERE d.note_id = n.id) ON CONFLICT (note_id) DO NOTHING;
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
    # 2.1.0: Zweiter Faktor (mfa_*), Profilbilder (avatar*), Anrufe (chat_call*). Die Tabellen legt SCHEMA oben an
    # (CREATE TABLE IF NOT EXISTS - auf testing-Installationen gibt es sie schon); diese Migration hebt den Schema-Stand,
    # damit 2.0.x und aelter auf einer 2.1-Datenbank NICHT mehr starten (SchemaTooNew): sonst liefe z. B. 2.0.0 weiter
    # und liesse Konten mit 2FA nur mit dem Passwort hinein. Sie prueft nur, dass die Tabellen da sind (aendert nichts,
    # beliebig oft ausfuehrbar); vorher legt init_schema wie bei jeder Migration ein Backup an.
    6: """
SELECT 1 FROM mfa_passkey, mfa_totp, mfa_recovery, mfa_challenge, avatar, avatar_img, chat_call, chat_call_signal LIMIT 0;
""",
    # 2.3.0: Foederation Protokoll 2 - Faehigkeiten und Admin-Schalter je verbundener Instanz, Adressbuch-Stand,
    # Ausgangskorb mit Reihenfolge je Kette (chain) und Ereignis-Kennung (idempotent beim Empfaenger)
    7: """
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS protocol INTEGER NOT NULL DEFAULT 1;
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS caps TEXT NOT NULL DEFAULT '[]';
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS caps_at DOUBLE PRECISION;
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS opts TEXT NOT NULL DEFAULT '{}';
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS dir_seq BIGINT NOT NULL DEFAULT 0;
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS dir_cfg TEXT NOT NULL DEFAULT '';
ALTER TABLE fed_peer ADD COLUMN IF NOT EXISTS dir_at DOUBLE PRECISION;
ALTER TABLE fed_outbox ADD COLUMN IF NOT EXISTS chain TEXT;
ALTER TABLE fed_outbox ADD COLUMN IF NOT EXISTS ev_id TEXT;
CREATE INDEX IF NOT EXISTS fed_outbox_chain ON fed_outbox (peer_id, chain, id) WHERE chain IS NOT NULL;
CREATE INDEX IF NOT EXISTS fed_outbox_due ON fed_outbox (next_try);
""",
    # 2.3.0: Chats und Anrufe ueber Servergrenzen (saga/fed_chat.py, saga/fed_calls.py) - globale Chat-Kennung (ref) mit
    # Heimserver (home_peer, NULL = dieser Server), Reihenfolge des Heims (fseq), Zustand je Mitglied (Nachrichtenanfrage),
    # Absender-Handle und mitsignierter KI-Hash je Nachricht, Gegenstelle eines Anrufs, Epoche je Kette (nach Zurueckspielen
    # eines Backups neu) und die signierte Geraete-Liste (saga-chat/v2/devices). Lokale Chats (v=1) bleiben unveraendert.
    8: """
ALTER TABLE chat ADD COLUMN IF NOT EXISTS v INTEGER NOT NULL DEFAULT 1;
ALTER TABLE chat ADD COLUMN IF NOT EXISTS ref TEXT;
ALTER TABLE chat ADD COLUMN IF NOT EXISTS home_peer INTEGER;
ALTER TABLE chat ADD COLUMN IF NOT EXISTS fseq BIGINT NOT NULL DEFAULT 0;
ALTER TABLE chat ADD COLUMN IF NOT EXISTS fstate TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS chat_ref_u ON chat (ref) WHERE ref IS NOT NULL;
ALTER TABLE chat_member ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'ok';
ALTER TABLE chat_msg ADD COLUMN IF NOT EXISTS fseq BIGINT;
ALTER TABLE chat_msg ADD COLUMN IF NOT EXISTS sender_handle TEXT;
ALTER TABLE chat_msg ADD COLUMN IF NOT EXISTS ai TEXT;
ALTER TABLE chat_msg ADD COLUMN IF NOT EXISTS fstate TEXT;
CREATE INDEX IF NOT EXISTS chat_msg_fseq ON chat_msg (chat_id, fseq) WHERE fseq IS NOT NULL;
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS peer_id INTEGER;
ALTER TABLE chat ADD COLUMN IF NOT EXISTS fep TEXT NOT NULL DEFAULT '';
ALTER TABLE fed_chain_out ADD COLUMN IF NOT EXISTS epoch TEXT NOT NULL DEFAULT '';
ALTER TABLE fed_chain_in ADD COLUMN IF NOT EXISTS epoch TEXT NOT NULL DEFAULT '';
ALTER TABLE chat_fed_cert ADD COLUMN IF NOT EXISTS dset TEXT;
""",
    # 2.3.0: Teilen ueber Servergrenzen vollstaendig (saga/fed_pages.py) - Dateien, die eine Person einer anderen Instanz in
    # eine Seite hier hochlaedt: gehoeren dem Eigentuemer der Seite (zaehlen bei ihm), fed_by = Prinzipal '@name@host'
    # (eigene Grenze je fremder Person), fed_note = Seite (Vorschau gleich nach dem Hochladen, noch vor dem Speichern).
    9: """
ALTER TABLE file ADD COLUMN IF NOT EXISTS fed_by TEXT;
ALTER TABLE file ADD COLUMN IF NOT EXISTS fed_note BIGINT;
CREATE INDEX IF NOT EXISTS file_fed_by ON file (uid, fed_by) WHERE fed_by IS NOT NULL;
""",
    # 2.4.0 (Etappe E0, Anrufe - Grundgeruest): Raum-Fassung v (1 = 1:1 wie 2.1/2.3, 3 = Raum ab E4), Topologie topo
    # (p2p | mesh | spaeter sfu) und Art kind (dm | group) - in E0 bleiben alle Anrufe v=1/p2p/dm. Dazu die beim Anrufen
    # bzw. Annehmen vom Geraet gemeldeten Faehigkeiten (caps_a = anrufendes, caps_b = annehmendes Geraet; JSON-Liste, nur
    # Hinweis fuer Oberflaeche und Grenzen - massgeblich sind die signierten caps im versiegelten Body).
    # Erweiterung in E4 (Migration 12): Endpunkte je Geraet in einer EIGENEN Tabelle chat_call_leg (call_id, device, who,
    # peer_id, role, state, replaces, caps, joined, left_at, seen; PK (call_id, device), FK auf chat_call ON DELETE CASCADE)
    # und chat_call_invite; caps_a/caps_b werden dort als caps der beiden ersten Endpunkte uebernommen, caller/callee und
    # caller_device/callee_device bleiben fuer 2.3-Code und Foederation gefuellt. Neue Tabellen mit Fremdschluessel auf
    # chat_call muessen dann auch in _RESTORE_DROP (sonst scheitert das Zurueckspielen eines aelteren Backups an DROP chat_call).
    10: """
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS v SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS topo TEXT NOT NULL DEFAULT 'p2p';
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'dm';
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS caps_a TEXT NOT NULL DEFAULT '[]';
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS caps_b TEXT NOT NULL DEFAULT '[]';
""",
    # 2.4.0 (Etappe E2, Videos im Chat): Format je Anhang (1 = ein Block wie bis 2.3, 2 = saga-file/v2 blockweise mit
    # wahlfreiem Zugriff) und fuer Zwischenkopien von Anhaengen anderer Server (Range-Abruf, chatfile2) die Bitmap der schon
    # geholten 1-MiB-Einheiten (NULL = ganze Datei wie bis 2.3); Index je Konto fuer den Chat-Speicher (/api/chat/me "used").
    # Die Tabelle chat_upload legt SCHEMA an.
    11: """
ALTER TABLE chat_file ADD COLUMN IF NOT EXISTS fmt SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE chat_file_fed ADD COLUMN IF NOT EXISTS have BYTEA;
CREATE INDEX IF NOT EXISTS chat_file_uid ON chat_file (uid);
""",
    # 2.4.0 (Etappen E4/E5, Anrufe als Raum): Endpunkte je Geraet (chat_call_leg) und Einladungen eines Gruppenanrufs
    # (chat_call_invite) legt SCHEMA oben an. Hier: lead = Server, der den Anruf fuehrt (NULL = dieser; Direkt-Anruf: der
    # Server der anrufenden Person wie 2.3, Gruppenanruf: das Heim des Chats), Geraete-Faehigkeiten als Hinweis
    # (chat_device.caps, z. B. "multi" - wer keines hat, wird zu Gruppenanrufen nicht eingeladen) und die Endpunkte
    # laufender Anrufe aus caller_device/callee_device. Beliebig oft ausfuehrbar.
    12: """
ALTER TABLE chat_call ADD COLUMN IF NOT EXISTS lead INTEGER;
ALTER TABLE chat_device ADD COLUMN IF NOT EXISTS caps TEXT NOT NULL DEFAULT '[]';
UPDATE chat_call SET lead=peer_id WHERE lead IS NULL AND peer_id IS NOT NULL AND caller LIKE '@%' AND kind='dm';
INSERT INTO chat_call_leg (call_id, device, who, peer_id, role, state, caps, joined, seen)
  SELECT id, caller_device, caller, CASE WHEN caller LIKE '@%' THEN peer_id END, 'member', 'active', caps_a, created, seen_a
    FROM chat_call WHERE state<>'ended' ON CONFLICT DO NOTHING;
INSERT INTO chat_call_leg (call_id, device, who, peer_id, role, state, caps, joined, seen)
  SELECT id, callee_device, callee, CASE WHEN callee LIKE '@%' THEN peer_id END, 'member', 'active', caps_b, answered, seen_b
    FROM chat_call WHERE state='active' AND callee_device IS NOT NULL ON CONFLICT DO NOTHING;
""",
    # 2.4.0 (Gelesen-Bestaetigungen und Online-Status, saga/chat_status.py): die Tabellen chat_receipt und chat_seen legt
    # SCHEMA oben an (CREATE TABLE IF NOT EXISTS). Diese Migration hebt nur den Schema-Stand (wie 6) und prueft, dass sie da
    # sind - aeltere Versionen starten danach nicht mehr auf dieser Datenbank. Beim Zusammenfuehren von 11 (E2) und 12
    # (E4/E5) auf 13 umnummeriert (diese prueft nur, beliebig oft ausfuehrbar).
    13: """
SELECT 1 FROM chat_receipt, chat_seen LIMIT 0;
""",
    # 2.4.0 (Testrunde): Gravatar nur mit Einwilligung - neue Konten (ohne Zeile in avatar) beginnen mit "aus".
    # Bestehende Zeilen (die Wahl der Person bzw. ihr bisheriger Stand) bleiben unveraendert. Dazu die harte Kontosperre
    # nach sehr vielen Fehlversuchen ueber viele Adressen (Tabelle login_lock legt SCHEMA an) und Suchtext neu ableiten
    # fuer Seiten mit Bloecken, die bisher technische Werte lieferten (notes.SPECIAL_BLOCKS). Beliebig oft ausfuehrbar.
    14: """
ALTER TABLE avatar ALTER COLUMN gravatar SET DEFAULT 0;
SELECT 1 FROM login_lock LIMIT 0;
INSERT INTO note_stale (note_id, uid) SELECT id, uid FROM note WHERE format='blocks' AND (strpos(body, '"embed"') > 0
  OR strpos(body, '"buttons"') > 0 OR strpos(body, '"calendar"') > 0 OR strpos(body, '"database"') > 0
  OR strpos(body, '"attaches"') > 0 OR strpos(body, '"tasks"') > 0) ON CONFLICT (note_id) DO NOTHING;
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
    path = os.path.join(BACKUP_DIR, f"saga-{_local_stamp()}-{label}.dump")
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


# Abgeleitete Tabellen (2.2, saga/pageindex.py) verweisen per Fremdschluessel auf note. Ein Backup von vor 2.2 kennt sie
# nicht - pg_restore --clean koennte note dann nicht loeschen ("other objects depend on it") und bricht ab. Sie sind
# jederzeit neu ableitbar: vorher entfernen. Ein Backup ab 2.2 bringt sie mit, sonst legt der naechste Start sie an und
# gleicht alle Seiten neu ab. Seitenbaum-Abgleich (note_tseq*, kein Fremdschluessel): der Stand gehoert zur alten
# Datenbank - der naechste Start legt ihn leer an und wuerfelt eine neue Kennung (Clients laden einmal ganz).
# chat_invite (2.3, Fremdschluessel auf chat): ein Backup von davor kennt sie nicht (sonst bricht DROP chat ab); ein
# neueres bringt sie mit, sonst legt der naechste Start sie leer an (Sperren trifft dann wie bisher die Erstellerin).
# 2.4: Migration 10 aendert nur Spalten von chat_call (ein aelteres Backup legt chat_call ohne sie an, der naechste Start
# ergaenzt sie). Tabellen mit Fremdschluessel auf chat_call (E4/E5: chat_call_leg, chat_call_invite) gehoeren hierher: ein
# Backup von davor kennt sie nicht (sonst bricht DROP chat_call ab); ein neueres bringt sie mit, sonst legt der naechste
# Start sie leer an (laufende Anrufe sind nach dem Zurueckspielen ohnehin vorbei).
# chat_upload (2.4 E2, Fremdschluessel auf chat): laufende Uploads gehoeren zur alten Datenbank - weg (die Teil-Dateien unter
# STATE_DIR/chat/up raeumt der naechste purge() ab, ein Neustart des Uploads legt einen neuen an).
# 2.4 (Migration 13): chat_receipt (Fremdschluessel auf chat) und chat_seen - ein aelteres Backup kennt sie nicht; danach
# legt der naechste Start sie leer an (Bestaetigungen kommen mit dem naechsten Lesen neu, "zuletzt online" fehlt bis dahin).
_RESTORE_DROP = (b"DROP TABLE IF EXISTS public.note_link, public.note_stale, public.note_derived;\n"
                 b"DROP TABLE IF EXISTS public.note_tseq, public.note_tseq_meta;\n"
                 b"DROP TABLE IF EXISTS public.chat_invite;\n"
                 b"DROP TABLE IF EXISTS public.chat_call_leg, public.chat_call_invite;\n"
                 b"DROP TABLE IF EXISTS public.chat_upload;\n"
                 b"DROP TABLE IF EXISTS public.chat_receipt, public.chat_seen;\n")
_RESTORE_SET = re.compile(rb"^SET ([a-z_]+) = .*;\r?\n?$")


def _restore_filter(src, dst, known):
    """SQL-Text von pg_restore -> psql. Laesst SET-Zeilen fuer Parameter weg, die der Server nicht kennt (pg_restore 17
    schreibt "SET transaction_timeout = 0;", Postgres 16 bricht daran ab), setzt nach BEGIN das DROP der abgeleiteten
    Tabellen ein und haelt alles ab dem abschliessenden COMMIT zurueck. Zeilen in COPY-Daten bleiben unberuehrt.
    Rueckgabe: zurueckgehaltener Rest (bytes) oder None, wenn kein BEGIN kam (dann nie COMMIT schicken).
    SET-Zeilen fallen nur im Kopf weg (vor dem ersten Objekt) - spaeter koennten sie in Funktionskoerpern oder
    mehrzeiligen Texten stehen. Ein "COMMIT;" mitten im Text (z. B. in einer Prozedur) wird nicht festgehalten: Folgt
    noch eine echte Zeile (kein Kommentar/Leerzeile/psql-Befehl), geht es sofort weiter."""
    in_copy = begun = False
    head = True
    held = None
    for line in iter(src.readline, b""):
        if held is not None:
            s = line.strip()
            if not s or s.startswith(b"--") or s.startswith(b"\\"):
                held += line
                continue
            dst.write(held)                 # war nicht das abschliessende COMMIT
            held = None
        if head:
            s = line.strip()
            head = (not s or s.startswith(b"--") or s.startswith(b"\\") or s == b"BEGIN;" or s.startswith(b"SET ")
                    or s.startswith(b"SELECT pg_catalog.set_config("))
        if in_copy:
            if line in (b"\\.\n", b"\\.\r\n"):
                in_copy = False
        elif line.startswith(b"COPY ") and line.rstrip().endswith(b"FROM stdin;"):
            in_copy = True
        elif head and (m := _RESTORE_SET.match(line)) and m.group(1).decode() not in known:
            continue
        elif line.strip() == b"COMMIT;":
            held = line
            continue
        dst.write(line)
        if not begun and not in_copy and line.strip() == b"BEGIN;":
            begun = True
            dst.write(_RESTORE_DROP)
    return held if begun else None


def restore(path):
    """Backup zurueckspielen (Saga vorher stoppen!). Ersetzt alle Tabellen durch den Stand im Backup - alles oder nichts.

    Nicht pg_restore direkt gegen die Datenbank: Das Image bringt pg_restore 17 mit (liest Archive aller Versionen, auch
    die eigenen Backups im Format 1.16, die pg_restore 16 nicht lesen kann), Postgres 16 kennt aber das von pg_restore 17
    gesetzte transaction_timeout nicht. Deshalb: pg_restore erzeugt SQL, _restore_filter laesst unbekannte SET-Zeilen
    weg, psql spielt es in EINER Transaktion ein. COMMIT geht erst raus, wenn pg_restore fehlerfrei fertig ist - ein
    beschaedigtes Backup aendert nichts. Funktioniert so mit Postgres 16 und 17 (und neueren Clients)."""
    import shutil
    import subprocess
    import tempfile
    if not os.path.isfile(path):
        raise RuntimeError(f"Datei nicht gefunden: {path}")
    for tool in ("pg_restore", "psql"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} fehlt - Zurueckspielen nicht moeglich (im Saga-Image enthalten)")
    with psycopg.connect(DATABASE_URL, autocommit=True) as c:
        known = {r[0] for r in c.execute("SELECT name FROM pg_settings")}
    env = _pg_env()
    with tempfile.TemporaryFile() as e1, tempfile.TemporaryFile() as e2:
        rp = subprocess.Popen(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
                               "--single-transaction", "--file", "-", path], env=env, stdout=subprocess.PIPE, stderr=e1)
        sp = subprocess.Popen(["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "--no-password", "-o", os.devnull],
                              env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=e2)
        held = None
        try:
            held = _restore_filter(rp.stdout, sp.stdin, known)
            rc = rp.wait(timeout=3600)
            if rc == 0 and held is not None:
                sp.stdin.write(held)        # COMMIT (+ Schlusskommentar) nur nach fehlerfreiem pg_restore
        except BrokenPipeError:            # psql hat abgebrochen (ON_ERROR_STOP) - Fehlertext steht in e2
            pass
        finally:
            try:
                sp.stdin.close()            # ohne COMMIT: psql beendet sich, der Server rollt alles zurueck
            except BrokenPipeError:
                pass
            if rp.poll() is None:
                rp.kill()
            rp.wait()
            rp.stdout.close()
            src = sp.wait(timeout=3600)
        err = b""
        for f in (e1, e2):
            f.seek(0)
            err += f.read()
        if rp.returncode != 0 or src != 0 or held is None:
            msg = err.decode("utf-8", "replace").strip()[:500] or "pg_restore lieferte kein vollstaendiges Backup"
            raise RuntimeError("Zurueckspielen fehlgeschlagen (nichts geaendert): " + msg)


def init_search(c):
    """Trigramm-Index fuer die Suche (pg_trgm, im offiziellen Postgres-Image enthalten): LIKE '%teil%' ueber
    note_derived.search per Index - auch fuer Teilwoerter und Sprachen ohne Wortgrenzen (Thai). Fehlt die Erweiterung
    oder das Recht, sie anzulegen, sucht Saga mit demselben Ergebnis ohne Index (Durchlauf ueber die Suchtexte)."""
    c.execute("SAVEPOINT saga_trgm")
    try:
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        c.execute("CREATE INDEX IF NOT EXISTS note_derived_trgm ON note_derived USING gin (search gin_trgm_ops)")
        c.execute("CREATE INDEX IF NOT EXISTS note_derived_tags ON note_derived USING gin (tags gin_trgm_ops)")
        c.execute("RELEASE SAVEPOINT saga_trgm")
    except psycopg.Error as ex:
        c.execute("ROLLBACK TO SAVEPOINT saga_trgm")
        print(f"saga: Suche ohne Trigramm-Index (pg_trgm nicht verfuegbar: {str(ex).strip()[:200]})", flush=True)


# Seitenbaum-Abgleich (2.2, saga/treesync.py: GET /api/notes?since=...): je Seite die Transaktion (xid8 als BIGINT) der
# letzten Aenderung an etwas, das der Seitenbaum zeigt - Seite selbst (Titel, Symbol, Eltern, Reihenfolge, Tags, Favorit,
# Vorlage, Archiv, Sperre, Papierkorb, geaendert), ihre Freigaben (Symbol "geteilt") und Kommentare/Gelesen-Stand (Punkt
# "neue Kommentare"). Geloeschte Seiten bleiben als gone=1 stehen (Grabstein, nach TOMB_DAYS weg). Eigene Tabellen und
# Trigger: kein neuer Schema-Stand; aeltere Versionen ignorieren sie (die Trigger arbeiten dort weiter). Die Kennung
# "epoch" wird bei jedem Start neu gewuerfelt: nach Zurueckspielen eines Backups oder Wechsel des Datenbank-Servers
# passen alte Stände der Clients nicht mehr -> sie laden einmal den ganzen Baum.
TREE_SEQ_SQL = """
CREATE TABLE IF NOT EXISTS note_tseq (
    uid TEXT NOT NULL, id BIGINT NOT NULL, xid BIGINT NOT NULL, gone INTEGER NOT NULL DEFAULT 0,
    at DOUBLE PRECISION NOT NULL, PRIMARY KEY (uid, id));
CREATE INDEX IF NOT EXISTS note_tseq_xid ON note_tseq (uid, xid);
CREATE TABLE IF NOT EXISTS note_tseq_meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- Reihenfolge des Baums (sort NULLS FIRST, created, id) als Index: Vorgaenger einer geaenderten Seite ohne die ganze Liste
CREATE INDEX IF NOT EXISTS note_tree_order ON note (uid, (sort IS NOT NULL), (COALESCE(sort, 0)), created, id)
    WHERE deleted_at IS NULL;
CREATE OR REPLACE FUNCTION saga_tseq_bump(u TEXT, n BIGINT, g INTEGER) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF u IS NULL OR n IS NULL THEN RETURN; END IF;
    -- g NULL: Freigabe/Kommentar - Grabstein-Kennung nicht aendern
    INSERT INTO note_tseq (uid, id, xid, gone, at)
        VALUES (u, n, pg_current_xact_id()::text::bigint, COALESCE(g, 0), extract(epoch FROM clock_timestamp()))
        ON CONFLICT (uid, id) DO UPDATE SET xid = EXCLUDED.xid, at = EXCLUDED.at,
                                            gone = CASE WHEN g IS NULL THEN note_tseq.gone ELSE EXCLUDED.gone END;
END $$;
CREATE OR REPLACE FUNCTION saga_note_tseq() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Datenbank weg (oder Art geaendert): ihre Eintraege sind fuer "?rows=0/only" jetzt etwas anderes -> mit melden
    IF (TG_OP = 'DELETE' AND OLD.kind = 'database')
       OR (TG_OP = 'UPDATE' AND (NEW.kind IS DISTINCT FROM OLD.kind
                                 OR (OLD.kind = 'database' AND (NEW.uid IS DISTINCT FROM OLD.uid OR NEW.id IS DISTINCT FROM OLD.id)))) THEN
        PERFORM saga_tseq_bump(n.uid, n.id, NULL) FROM note n WHERE n.uid = OLD.uid AND n.parent_id = OLD.id;
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM saga_tseq_bump(OLD.uid, OLD.id, 1);
        RETURN NULL;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.id, NEW.uid, NEW.title, NEW.icon, NEW.parent_id, NEW.updated, NEW.tags, NEW.pinned, NEW.is_template, NEW.kind,
            NEW.archived, NEW.locked, NEW.deleted_at, NEW.sort, NEW.created)
           IS NOT DISTINCT FROM
           (OLD.id, OLD.uid, OLD.title, OLD.icon, OLD.parent_id, OLD.updated, OLD.tags, OLD.pinned, OLD.is_template, OLD.kind,
            OLD.archived, OLD.locked, OLD.deleted_at, OLD.sort, OLD.created) THEN
            RETURN NULL;
        END IF;
        IF NEW.uid IS DISTINCT FROM OLD.uid OR NEW.id IS DISTINCT FROM OLD.id THEN
            PERFORM saga_tseq_bump(OLD.uid, OLD.id, 1);
        END IF;
    END IF;
    PERFORM saga_tseq_bump(NEW.uid, NEW.id, CASE WHEN NEW.deleted_at IS NULL THEN 0 ELSE 1 END);
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION saga_share_tseq() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN PERFORM saga_tseq_bump(OLD.owner_uid, OLD.note_id, NULL); END IF;
    IF TG_OP <> 'DELETE' THEN PERFORM saga_tseq_bump(NEW.owner_uid, NEW.note_id, NULL); END IF;
    RETURN NULL;
END $$;
CREATE OR REPLACE FUNCTION saga_seen_tseq() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT owner_uid, note_id FROM note_comment
              WHERE id IN (CASE WHEN TG_OP = 'DELETE' THEN OLD.thread_id ELSE NEW.thread_id END,
                           CASE WHEN TG_OP = 'INSERT' THEN NEW.thread_id ELSE OLD.thread_id END) LOOP
        PERFORM saga_tseq_bump(r.owner_uid, r.note_id, NULL);
    END LOOP;
    RETURN NULL;
END $$;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'note_tseq_t' AND tgrelid = 'note'::regclass) THEN
        CREATE TRIGGER note_tseq_t AFTER INSERT OR UPDATE OR DELETE ON note FOR EACH ROW EXECUTE FUNCTION saga_note_tseq();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'note_share_tseq_t' AND tgrelid = 'note_share'::regclass) THEN
        CREATE TRIGGER note_share_tseq_t AFTER INSERT OR UPDATE OR DELETE ON note_share FOR EACH ROW EXECUTE FUNCTION saga_share_tseq();
    END IF;
    -- Kommentare: dieselbe Funktion wie Freigaben (owner_uid, note_id)
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'note_comment_tseq_t' AND tgrelid = 'note_comment'::regclass) THEN
        CREATE TRIGGER note_comment_tseq_t AFTER INSERT OR UPDATE OR DELETE ON note_comment FOR EACH ROW EXECUTE FUNCTION saga_share_tseq();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'note_seen_tseq_t' AND tgrelid = 'note_comment_seen'::regclass) THEN
        CREATE TRIGGER note_seen_tseq_t AFTER INSERT OR UPDATE OR DELETE ON note_comment_seen FOR EACH ROW EXECUTE FUNCTION saga_seen_tseq();
    END IF;
END $$;
"""


def init_tree_seq(c):
    """Abgleich-Stand fuer den Seitenbaum (siehe TREE_SEQ_SQL) anlegen und die Kennung neu wuerfeln. Ohne pg_current_xact_id
    (Postgres < 13) oder ohne Rechte: kein Abgleich - GET /api/notes?since=... liefert dann immer den ganzen Baum."""
    import secrets
    c.execute("SAVEPOINT saga_tseq")
    try:
        # Erst pruefen: plpgsql prueft Funktionsnamen erst beim Aufruf - ohne pg_current_xact_id (Postgres < 13) liessen
        # sich die Trigger anlegen und jede Aenderung an einer Seite scheiterte danach
        c.execute("SELECT pg_snapshot_xmin(pg_current_snapshot())")
        c.execute(TREE_SEQ_SQL)
        c.execute("INSERT INTO note_tseq_meta (k, v) VALUES ('epoch', %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v",
                  (secrets.token_hex(6),))
        # Stand aus einem anderen Datenbank-Server (Backup nach einem Umzug zurueckgespielt): Transaktionsnummern, die es
        # hier noch gar nicht gab, kaemen sonst bei jedem Abgleich mit bzw. die Grenze erzwaenge immer den ganzen Baum.
        # Mit der neuen Kennung braucht sie niemand mehr.
        c.execute("DELETE FROM note_tseq WHERE xid >= pg_snapshot_xmax(pg_current_snapshot())::text::bigint")
        c.execute("DELETE FROM note_tseq_meta WHERE k = 'horizon' AND v::bigint >= pg_snapshot_xmax(pg_current_snapshot())::text::bigint")
        c.execute("RELEASE SAVEPOINT saga_tseq")
    except psycopg.Error as ex:
        c.execute("ROLLBACK TO SAVEPOINT saga_tseq")
        print(f"saga: Seitenbaum-Abgleich (since) nicht verfuegbar: {str(ex).strip()[:200]}", flush=True)


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
        init_search(c)
        init_tree_seq(c)
        if cur is None:
            c.execute("INSERT INTO schema_version (v) VALUES (1)")
            cur = 1
        for v in range(cur + 1, SCHEMA_VERSION + 1):
            if v not in MIGRATIONS:          # Nummer einer parallel entwickelten Etappe (beim Zusammenfuehren belegt)
                continue
            print(f"saga: Datenbank-Migration {v} ...", flush=True)
            c.execute(MIGRATIONS[v])
            c.execute("INSERT INTO schema_version (v) VALUES (%s)", (v,))
        c.commit()
