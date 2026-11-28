"""Schattenmodus Phase 1 (2.6, Bugs-DB 2603, docs/MEMORY.md 12/13) - Stufe S2: nur die Schnittstelle (ab Werk aus).
Die Rechnung (Verknuepfungen, Ausbreitung, Spuren, Auswertung) folgt in S3."""

TRACE_KEEP = 30 * 86400


def running(uid):
    return False


def schedule_backfill(uid, delay=1.0):
    return None


def used(uid, ids):
    return {"ok": True, "schatten": False, "verstaerkt": 0, "neu": 0, "ignoriert": []}


def report(uid, tage, n):
    return {"schatten": False, "seit": None, "tage": tage}
