"""Daten eines Kontos zaehlen und loeschen (Admin-Bereich)."""
import importlib
import time
import os
from db import connect as _db
from saga import avatars, bus, chat, nextcloud, notify, push, userbackup
from saga.calendars import _cal_cache_drop
from saga.core import forget_device
from saga.files import _drop_thumbs
from saga.notes import FILES_DIR
from saga.sharing import delete_account, share_counts


# --- Konto loeschen (Admin-Bereich): ALLE Daten des Benutzers entfernen -------------------------------
# Wichtig: uid = Benutzername. Blieben Daten liegen, bekaeme ein spaeter neu angelegtes Konto mit
# gleichem Namen fremde Seiten, Kalender-Zugaenge usw.
USER_TABLES = ("task", "note", "note_version", "bookmark", "fav", "device_login", "app_token", "user_kv",
               "immich_key", "bookorbit_link", "cal_account", "app_code", "pair_code", "note_publish", "public_name", "notification",
               "chat_identity", "chat_device", "chat_link", "push_sub", "push_queue", "ai_history", "ai_usage", "ai_units", "chat_ai_reply",
               "mfa_passkey", "mfa_totp", "mfa_recovery", "mfa_challenge", "fed_handle", "chat_fed_cert", "chat_device_fed",
               "fed_chat_block", "chat_invite", "nc_account", "chat_seen", "chat_hidden", "note_fresh", "import_run", "task_source",
               # 2.6 (Bugs-DB 2264): verbundene Apps; oauth_code/oauth_refresh per CASCADE - vor api_token (Sperrreihenfolge
               # oauth_grant -> api_token wie saga/oauth.py, N5)
               "oauth_grant",
               "api_token", "webhook", "job",      # 2.6: job_step/job_wait/job_event/job_change gehen mit (ON DELETE CASCADE)
               "user_status",                      # 2.6 (Bugs-DB 2029): eigener Status; aus Ausnahmen anderer: user_status.drop_account
               "contact_addr", "contact",          # 2.6 (Bugs-DB 2027a): Adressbuch (bei anderen: contacts.drop_account)
               "ai_turn", "ai_thread", "ai_consent")   # 2.6 (Bugs-DB 2269 K-I): Gespraech im Ort "Saga", Einwilligungen


def delete_user_data(uid, drop_user=False):
    """drop_user=True: das Konto selbst in DERSELBEN Transaktion loeschen (Admin: Konto loeschen, BUG-042)."""
    userbackup.revoke_on_delete(uid)         # App-Passwort des persoenlichen Backup-Ziels in Nextcloud widerrufen
    nextcloud.revoke_on_delete(uid)          # Nextcloud-Dateien in Seiten: App-Passwort widerrufen, Vorschauen weg
    from saga import bookorbit
    bookorbit.revoke_on_delete(uid)          # BookOrbit: Sitzung von Saga dort abmelden (beste Muehe)
    with _db() as c, c:
        fids = [r[0] for r in c.execute("SELECT id FROM file WHERE uid=?", (uid,)).fetchall()]
        pub = c.execute("SELECT name FROM public_name WHERE uid=?", (uid,)).fetchone()
        # Foederation (saga.federation): andere Instanzen erfahren es, Freigaben dorthin/von dort enden, Name gesperrt
        importlib.import_module("saga.federation")          # Zuhoerer sicher angemeldet (auch ausserhalb der App)
        importlib.import_module("saga.chat_grp")            # 2.5: Meldungen/Anheftungen anonymisieren (haengt sich in chat ein)
        bus.fire("public_name.released", c, uid=uid, pub=pub[0] if pub else None, deleted=True)
        c.execute("DELETE FROM file WHERE uid=?", (uid,))
        shares = delete_account(c, uid)          # Freigaben (eigene + an das Konto), Kommentare anonymisieren/loeschen
        notes_n = notify.delete_account(c, uid)  # Benachrichtigungen: eigene weg, als Ausloeser anonymisiert
        chat_n = chat.delete_account(c, uid)     # Chat: Schluessel, Geraete, Mitgliedschaften, gesendete Nachrichten, Anhaenge
        c.execute("DELETE FROM chat_receipt WHERE reader=? OR viewer=?", (uid, uid))    # Gelesen-Bestaetigungen (2.4)
        pushed = push.delete_account(c, uid)     # Mitteilungs-Abos und -Auftraege weg, als Absender anonymisiert
        avatar_n = avatars.delete_account(c, uid)  # Profilbild und zwischengespeicherte Gravatar-/SSO-Bilder
        ub_n = userbackup.delete_account(c, uid)  # persoenliches Backup: Ziel, Schluessel, Verlauf (Backups im Ziel bleiben)
        c.execute("DELETE FROM note_base WHERE note_id IN (SELECT id FROM note WHERE uid=?)", (uid,))   # 2.5 (P3): Basisstaende
        c.execute("DELETE FROM note_crdt_log WHERE note_id IN (SELECT id FROM note WHERE uid=?)", (uid,))   # 2.5 (P3 Stufe 2)
        c.execute("DELETE FROM note_crdt WHERE note_id IN (SELECT id FROM note WHERE uid=?)", (uid,))
        from saga.people import drop_account     # 2.5: Personen-Werte anderer bleiben ("Geloeschte Person"), row_person nicht
        drop_account(c, uid)
        from saga import mentions                 # 2.5 (P6-5): Erwaehnungen der Person (Index) weg, Text im Inhalt bleibt
        mentions.drop_account(c, uid)
        from saga import user_status              # 2.6 (Bugs-DB 2029): aus den Status-Ausnahmen anderer entfernen
        user_status.drop_account(c, uid)
        c.execute("UPDATE synced_block SET gone_at=? WHERE owner=? AND gone_at IS NULL", (time.time(), uid))   # P6-6
        counts = {t: c.execute(f"DELETE FROM {t} WHERE uid=?", (uid,)).rowcount for t in USER_TABLES}
        counts["note_share"] = shares
        counts["notification"] += notes_n
        counts["chat_msg"] = chat_n
        counts["avatar"] = avatar_n
        counts["user_backup"] = ub_n
        for k, v in pushed.items():
            counts[k] += v
        bus.emit_after(c, "account.deleted", {"uid": uid})     # Live-Verbindungen und Anwesenheit des Kontos beenden
        if drop_user:
            c.execute("DELETE FROM users WHERE username=?", (uid,))
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
                "bookorbit": q("SELECT COUNT(*) FROM bookorbit_link WHERE uid=?"),
                "nextcloud": q("SELECT COUNT(*) FROM nc_account WHERE uid=?"),
                "shares": share_counts(c, uid)[0], "shared_with": share_counts(c, uid)[1], **chat.counts(c, uid),
                **_api_counts(c, uid)}


def _api_counts(c, uid):
    from saga import apitok                 # 2.5: aktive API-Tokens und Webhooks
    return apitok.counts(c, uid)


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
        # 2.6 (Bugs-DB 2264): verbundene Apps (OAuth) getrennt - ihre Refresh-Token bekommen keine neuen Zugriffstoken mehr.
        # Vor api_token: dieselbe Sperrreihenfolge wie saga/oauth.py (oauth_grant -> api_token, N5)
        c.execute("UPDATE oauth_grant SET revoked=?, why='logout' WHERE uid=? AND revoked IS NULL", (time.time(), uid))
        # 2.5 (Paket 1): API-Tokens (sga_) gelten ab sofort nicht mehr; Webhooks dieser Tokens ruhen
        b += c.execute("UPDATE api_token SET revoked=? WHERE uid=? AND revoked IS NULL", (time.time(), uid)).rowcount
        c.execute("UPDATE webhook SET active=0, last_error='token revoked' WHERE uid=? AND token_id IS NOT NULL AND active=1", (uid,))
    forget_device(uid)                   # Sitzungen der abgemeldeten Geraete enden sofort (BUG-002)
    # 2.6 (Bugs-DB 2266 K-A6): laufende Auftraege der Kollegin halten an (wartet_dich 'abgemeldet') - ein Schritt, der
    # gerade laeuft, schreibt nicht mehr (Stempel). Unabhaengig von pw_changed/logout_at, die hier nicht gesetzt werden.
    from saga import jobs
    jobs.pause_all(uid, "abgemeldet")
    return a + b
