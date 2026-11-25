"""SQLite des Relais: servers, nonces (10 min), daily_stats (30 Tage). Keine Geraetetoken, keine Nutzlasten.

Der Dienst laeuft als EIN Prozess (Zaehler im Speicher); relayctl.py schreibt in dieselbe Datei (WAL), der Dienst liest
den Server bei jeder Anfrage neu - Freischalten/Sperren wirkt sofort."""
import base64
import json
import os
import secrets
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS servers (
  server_id TEXT PRIMARY KEY,
  pub TEXT NOT NULL UNIQUE,
  host TEXT NOT NULL,
  saga_version TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL,              -- active | pending | blocked
  stage TEXT NOT NULL DEFAULT 'neu', -- neu | aktiv | eigen
  limits TEXT NOT NULL DEFAULT '{}', -- Werte fuer stage=eigen
  verified INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  clean_since INTEGER NOT NULL,      -- seit wann ohne Sperre (neu -> aktiv nach 7 Tagen)
  last_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nonces (k TEXT PRIMARY KEY, exp INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS nonces_exp ON nonces(exp);
CREATE TABLE IF NOT EXISTS daily_stats (
  day TEXT NOT NULL, server_id TEXT NOT NULL, push_type TEXT NOT NULL, status INTEGER NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY (day, server_id, push_type, status)
);
"""
NONCE_TTL = 600
STATS_DAYS = 30
COLS = ("server_id", "pub", "host", "saga_version", "state", "stage", "limits", "verified", "note", "created", "updated",
        "clean_since", "last_seen")


def day(ts=None):
    return time.strftime("%Y-%m-%d", time.gmtime(ts or time.time()))


def new_server_id():
    return "s_" + base64.b32encode(secrets.token_bytes(10)).decode().lower()


class Store:
    def __init__(self, path=None):
        self.path = path or os.environ.get("RELAY_DB", "/data/relay.db")
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)

    def close(self):
        with self._lock:
            self.db.close()

    def _row(self, r):
        if not r:
            return None
        d = dict(zip(COLS, r))
        try:
            d["limits"] = json.loads(d["limits"] or "{}")
        except ValueError:
            d["limits"] = {}
        return d

    def get(self, server_id):
        with self._lock:
            return self._row(self.db.execute(f"SELECT {','.join(COLS)} FROM servers WHERE server_id=?", (server_id,)).fetchone())

    def by_pub(self, pub):
        with self._lock:
            return self._row(self.db.execute(f"SELECT {','.join(COLS)} FROM servers WHERE pub=?", (pub,)).fetchone())

    def all(self):
        with self._lock:
            return [self._row(r) for r in self.db.execute(f"SELECT {','.join(COLS)} FROM servers ORDER BY created").fetchall()]

    def host_blocked(self, host):
        with self._lock:
            return bool(self.db.execute("SELECT 1 FROM servers WHERE host=? AND state='blocked'", (host,)).fetchone())

    def create(self, pub, host, version, state, verified):
        now = int(time.time())
        with self._lock:
            for _ in range(5):
                sid = new_server_id()
                try:
                    self.db.execute("INSERT INTO servers (server_id, pub, host, saga_version, state, stage, verified, created, updated,"
                                    " clean_since, last_seen) VALUES (?,?,?,?,?,'neu',?,?,?,?,?)",
                                    (sid, pub, host, version, state, int(verified), now, now, now, now))
                    return sid
                except sqlite3.IntegrityError:
                    if self.db.execute("SELECT 1 FROM servers WHERE pub=?", (pub,)).fetchone():
                        raise
            raise RuntimeError("keine freie server_id")

    def update(self, server_id, **f):
        if not f:
            return
        f["updated"] = int(time.time())
        if "limits" in f and not isinstance(f["limits"], str):
            f["limits"] = json.dumps(f["limits"], separators=(",", ":"), sort_keys=True)
        keys = [k for k in f if k in COLS and k != "server_id"]
        with self._lock:
            self.db.execute(f"UPDATE servers SET {', '.join(k + '=?' for k in keys)} WHERE server_id=?",
                            [f[k] for k in keys] + [server_id])

    def delete(self, server_id):
        with self._lock:
            self.db.execute("DELETE FROM servers WHERE server_id=?", (server_id,))

    def seen(self, ids):
        """last_seen gesammelt nachtragen."""
        if not ids:
            return
        with self._lock:
            self.db.executemany("UPDATE servers SET last_seen=? WHERE server_id=?", [(t, s) for s, t in ids.items()])

    # --- Nonces (Replay) -------------------------------------------------------------------------------
    def nonce_fresh(self, keyid, nonce, now=None):
        """True beim ersten Mal; False = schon gesehen (Replay)."""
        now = int(now or time.time())
        with self._lock:
            try:
                self.db.execute("INSERT INTO nonces (k, exp) VALUES (?, ?)", (keyid + "|" + nonce, now + NONCE_TTL))
                return True
            except sqlite3.IntegrityError:
                return False

    # --- Statistik ---------------------------------------------------------------------------------------
    def add_stats(self, counts):
        """counts: {(tag, server_id, push_type, status): n}"""
        if not counts:
            return
        with self._lock:
            self.db.execute("BEGIN")
            try:
                for (d, sid, pt, st), n in counts.items():
                    self.db.execute("INSERT INTO daily_stats (day, server_id, push_type, status, n) VALUES (?,?,?,?,?)"
                                    " ON CONFLICT (day, server_id, push_type, status) DO UPDATE SET n = n + excluded.n",
                                    (d, sid, pt, int(st), int(n)))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def day_count(self, server_id, push_types, d=None):
        with self._lock:
            q = ",".join("?" * len(push_types))
            r = self.db.execute(f"SELECT COALESCE(SUM(n),0) FROM daily_stats WHERE day=? AND server_id=? AND push_type IN ({q})"
                                " AND status=200", [d or day(), server_id] + list(push_types)).fetchone()
            return int(r[0])

    def stats(self, server_id=None, days=7):
        since = day(time.time() - days * 86400)
        with self._lock:
            sql = "SELECT day, server_id, push_type, status, n FROM daily_stats WHERE day>=?"
            args = [since]
            if server_id:
                sql += " AND server_id=?"
                args.append(server_id)
            return self.db.execute(sql + " ORDER BY day, server_id, push_type, status", args).fetchall()

    def cleanup(self, now=None):
        now = int(now or time.time())
        with self._lock:
            self.db.execute("DELETE FROM nonces WHERE exp < ?", (now,))
            self.db.execute("DELETE FROM daily_stats WHERE day < ?", (day(now - STATS_DAYS * 86400),))
