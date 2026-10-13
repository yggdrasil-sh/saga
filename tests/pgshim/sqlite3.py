"""NUR fuer die Tests: die alten Tests sprechen die Datenbank ueber sqlite3 an (Vorbereiten, Aufraeumen).
Dieser Ersatz leitet sqlite3.connect(...) auf PostgreSQL um (gleiche Uebersetzung wie db.py).
Wird ueber PYTHONPATH=tests/pgshim vor die echte Bibliothek gestellt."""
import psycopg

import db


class OperationalError(Exception):
    pass


class _Cur:
    def __init__(self, cur, lastrowid=None):
        self._c, self.lastrowid = cur, lastrowid
        self.rowcount = cur.rowcount if cur is not None else 0

    def fetchone(self):
        return db._fix_row(self._c.fetchone()) if self._c is not None and self._c.description else None

    def fetchall(self):
        return [db._fix_row(r) for r in self._c.fetchall()] if self._c is not None and self._c.description else []

    def __iter__(self):
        return iter(self.fetchall())


class _Conn:
    def __init__(self):
        self.pg = psycopg.connect(db.DATABASE_URL, autocommit=True)

    def execute(self, sql, args=()):
        q, skip, returning = db.translate(sql)
        if skip:
            return _Cur(None)
        try:
            cur = self.pg.cursor()
            cur.execute(q, tuple(db._arg(a) for a in args))
        except psycopg.errors.UndefinedTable as ex:
            raise OperationalError(str(ex))
        if returning:
            row = cur.fetchone()
            return _Cur(cur, row[0] if row else None)
        return _Cur(cur)

    def commit(self):
        pass

    def close(self):
        self.pg.close()

    def backup(self, other):
        pass


def connect(*_a, **_k):
    return _Conn()
