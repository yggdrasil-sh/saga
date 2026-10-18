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
             "push_sub", "push_queue", "ai_history", "fed_invite", "fed_log", "fed_outbox"}
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
    gravatar INTEGER NOT NULL DEFAULT 1,              -- Person: Gravatar verwenden (der Admin-Schalter gilt zusaetzlich)
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


def restore(path):
    """Backup zurueckspielen (Saga vorher stoppen!). Ersetzt alle Tabellen durch den Stand im Backup."""
    import subprocess
    if not os.path.isfile(path):
        raise RuntimeError(f"Datei nicht gefunden: {path}")
    r = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", "--single-transaction",
                        "--dbname", _pg_env()["PGDATABASE"], path], env=_pg_env(), capture_output=True, timeout=3600)
    if r.returncode != 0:
        raise RuntimeError("Zurueckspielen fehlgeschlagen: " + r.stderr.decode("utf-8", "replace").strip()[:500])


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
        if cur is None:
            c.execute("INSERT INTO schema_version (v) VALUES (1)")
            cur = 1
        for v in range(cur + 1, SCHEMA_VERSION + 1):
            print(f"saga: Datenbank-Migration {v} ...", flush=True)
            c.execute(MIGRATIONS[v])
            c.execute("INSERT INTO schema_version (v) VALUES (%s)", (v,))
        c.commit()
