"""Daten eines Kontos zaehlen und loeschen (Admin-Bereich)."""
import os
from db import connect as _db
from saga import bus, notify
from saga.calendars import _cal_cache_drop
from saga.files import _drop_thumbs
from saga.notes import FILES_DIR
from saga.sharing import delete_account, share_counts


# --- Konto loeschen (Admin-Bereich): ALLE Daten des Benutzers entfernen -------------------------------
# Wichtig: uid = Benutzername. Blieben Daten liegen, bekaeme ein spaeter neu angelegtes Konto mit
# gleichem Namen fremde Seiten, Kalender-Zugaenge usw.
USER_TABLES = ("task", "note", "note_version", "bookmark", "fav", "device_login", "app_token", "user_kv",
               "immich_key", "cal_account", "app_code", "pair_code", "note_publish", "public_name", "notification",
               "ai_history", "ai_usage")


def delete_user_data(uid):
    with _db() as c, c:
        fids = [r[0] for r in c.execute("SELECT id FROM file WHERE uid=?", (uid,)).fetchall()]
        c.execute("DELETE FROM file WHERE uid=?", (uid,))
        shares = delete_account(c, uid)          # Freigaben (eigene + an das Konto), Kommentare anonymisieren/loeschen
        notes_n = notify.delete_account(c, uid)  # Benachrichtigungen: eigene weg, als Ausloeser anonymisiert
        counts = {t: c.execute(f"DELETE FROM {t} WHERE uid=?", (uid,)).rowcount for t in USER_TABLES}
        counts["note_share"] = shares
        counts["notification"] += notes_n
        bus.emit_after(c, "account.deleted", {"uid": uid})     # Live-Verbindungen und Anwesenheit des Kontos beenden
    for fid in fids:
        try:
            os.remove(os.path.join(FILES_DIR, fid))
        except OSError:
            pass
        _drop_thumbs(fid)
    _cal_cache_drop(uid)
    counts["file"] = len(fids)
    return counts


def user_usage(uid):
    """Kurzuebersicht fuer den Admin-Bereich (nur Anzahlen, keine Inhalte)."""
    with _db() as c:
        q = lambda sql: c.execute(sql, (uid,)).fetchone()[0]  # noqa: E731
        return {"pages": q("SELECT COUNT(*) FROM note WHERE uid=? AND deleted_at IS NULL"),
                "tasks": q("SELECT COUNT(*) FROM task WHERE uid=? AND done=0"),
                "bookmarks": q("SELECT COUNT(*) FROM bookmark WHERE uid=?"),
                "files_mb": round(q("SELECT COALESCE(SUM(size),0) FROM file WHERE uid=?") / 1048576, 1),
                "devices": q("SELECT COUNT(*) FROM device_login WHERE uid=?"),
                "tokens": q("SELECT COUNT(*) FROM app_token WHERE uid=?"),
                "calendars": q("SELECT COUNT(*) FROM cal_account WHERE uid=?"),
                "immich": q("SELECT COUNT(*) FROM immich_key WHERE uid=?"),
                "shares": share_counts(c, uid)[0], "shared_with": share_counts(c, uid)[1]}


def revoke_user_logins(uid, keep_device=None):
    """Alle App-Anmeldungen und App-Schluessel widerrufen (Browser-Sitzungen enden ueber pw_changed).
    keep_device: diese Geraete-Anmeldung samt ihrem Widget-/Teilen-Schluessel behalten (eigene Passwortaenderung in der App)."""
    with _db() as c, c:
        if keep_device:
            a = c.execute("DELETE FROM device_login WHERE uid=? AND id<>?", (uid, keep_device)).rowcount
            b = c.execute("DELETE FROM app_token WHERE uid=? AND (device_id IS NULL OR device_id<>?)", (uid, keep_device)).rowcount
        else:
            a = c.execute("DELETE FROM device_login WHERE uid=?", (uid,)).rowcount
            b = c.execute("DELETE FROM app_token WHERE uid=?", (uid,)).rowcount
    return a + b
