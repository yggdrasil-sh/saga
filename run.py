"""Startet Saga: Hauptseite (PORT, Standard 8080) und Admin-Bereich (ADMIN_PORT, Standard 8081)
in einem Prozess - beide teilen sich Datenbank-Pool und Zwischenspeicher (z. B. wirkt "Konto sperren"
im Admin-Bereich sofort auf der Hauptseite)."""
import asyncio
import logging
import os
import re

import uvicorn


class _HideSecrets(logging.Filter):
    """Zugriffs-Log: Kopplungs-Code aus /app/pair?c=... und Bestaetigungs-Wert aus /app/handoff?go=... unkenntlich machen.
    Anrufe (/api/calls...): keine IP-Adresse im Log der Signalisierung (wer wann mit wem telefoniert, bleibt so unverknuepft)."""
    RX = re.compile(r"([?&](?:c|go)=)[^&\s]+")

    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            if "/app/" in record.args[2]:
                a = list(record.args)
                a[2] = self.RX.sub(r"\1***", a[2])
                record.args = tuple(a)
            elif record.args[2].startswith("/api/calls"):
                record.args = ("-",) + tuple(record.args[1:])
            elif record.args[2].startswith("/internal/media/"):     # Berechtigungsabfrage von saga-media (eigene Zeile dort)
                return False
        return True


def _config(app, host, port, lifespan, behind_media=False):
    # timeout_graceful_shutdown: offene Live-Verbindungen (SSE) halten das Beenden sonst beliebig lange auf
    trusted = os.environ.get("TRUSTED_PROXIES", "127.0.0.1")
    extra = {}
    if behind_media:
        # Hinter saga-media (2.4, media/): es haengt die echte Gegenstelle an X-Forwarded-For an und laesst
        # X-Forwarded-Proto nur von vertrauten Proxys durch - uvicorn vertraut daher zusaetzlich 127.0.0.1 (nur saga-media
        # erreicht diesen Port) und ermittelt den Client wie bisher. Keep-Alive laenger als bei saga-media (30 s).
        trusted = (trusted + ",127.0.0.1") if trusted.strip() != "*" else trusted
        extra["timeout_keep_alive"] = 75
    return uvicorn.Config(app, host=host, port=port, proxy_headers=True, lifespan=lifespan, forwarded_allow_ips=trusted,
                          server_header=False, log_level=os.environ.get("LOG_LEVEL", "info"), timeout_graceful_shutdown=5,
                          # WebSocket nur fuer die eigene Push-Verbindung der Apps (2.5, saga.push_stream): der Server pingt
                          # NIE selbst (jeder Ping weckt das Handy - die App waehlt den Abstand), kleine Rahmen
                          ws_ping_interval=None, ws_ping_timeout=None, ws_max_size=16384, ws_max_queue=8, **extra)


async def main():
    import app as hk          # prueft die Konfiguration (BASE_URL, SECRET_KEY ...)
    import admin

    internal = os.environ.get("SAGA_INTERNAL_PORT", "")     # von saga-media gesetzt: nur intern lauschen, saga-media hat PORT
    if internal:
        servers = [uvicorn.Server(_config(hk.app, "127.0.0.1", int(internal), "on", behind_media=True))]
    else:
        servers = [uvicorn.Server(_config(hk.app, os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080")), "on"))]
    if os.environ.get("ADMIN_PORT", "8081") not in ("", "0", "off"):
        servers.append(uvicorn.Server(_config(admin.admin, os.environ.get("ADMIN_HOST", "0.0.0.0"),
                                              int(os.environ.get("ADMIN_PORT", "8081")), "off")))
    logging.getLogger("uvicorn.access").addFilter(_HideSecrets())   # nach uvicorn.Config (richtet das Logging ein)
    tasks = [asyncio.create_task(s.serve()) for s in servers]

    async def _end_live():
        """Beim Beenden zuerst die Live-Verbindungen schliessen ("bye") - uvicorn wartet sonst auf sie."""
        from saga import live
        while not any(s.should_exit for s in servers):
            await asyncio.sleep(0.5)
        live.stop()
        from saga import push_stream
        push_stream.stop()         # eigene Push-Verbindungen der Apps: 1012 (die App verbindet gleich neu)
    watcher = asyncio.create_task(_end_live())
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:              # Signal kommt nur bei einem Server an -> beide beenden
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)
    watcher.cancel()


def reset_admin(name):
    """python run.py admin-passwort [benutzer]  - neues Zufallspasswort fuer ein Admin-Konto (Notfall)."""
    import auth
    import db
    db.wait_ready(30)
    db.init_schema()
    user, pw = auth.reset_admin(name)
    print(f"Admin-Konto „{user}“ – neues Passwort: {pw}")
    print(f"(steht auch in {auth.PW_FILE}; nach dem Anmelden im Admin-Bereich ändern)")


def reset_mfa(name):
    """python run.py 2fa-zuruecksetzen <benutzer>  - zweiten Faktor eines Kontos entfernen (Notfall; Passwort bleibt).
    Verlangt die Richtlinie einen Faktor, fuehrt die naechste Anmeldung zur Einrichtung (auch im Admin-Bereich)."""
    import auth
    import db
    db.wait_ready(30)
    db.init_schema()
    u = auth.get_user(auth.norm_username(name) or "")
    if not u:
        sys.exit(f"Kein Konto „{name}“")
    n = auth.clear_mfa(u["username"], "cli")
    auth.clear_fails(u["username"])
    for area in ("mfa", "admin-mfa"):
        auth.clear_fails(u["username"], area)
    print(f"Zweiter Faktor von „{u['username']}“ zurückgesetzt ({n} Faktor(en) entfernt)." if n
          else f"„{u['username']}“ hatte keinen zweiten Faktor.")


def mfa_policy(value):
    """python run.py 2fa-richtlinie [none|admins|all]  - Richtlinie "2FA verlangen" anzeigen bzw. setzen (Notfall)."""
    import auth
    import db
    db.wait_ready(30)
    db.init_schema()
    cur = dict(auth.settings(fresh=True).get("mfa") or {})
    if value:
        if value not in ("none", "admins", "all"):
            sys.exit("Aufruf: python run.py 2fa-richtlinie none|admins|all")
        cur["require"] = value
        auth.save_setting("mfa", cur)
    print(f"2FA verlangen: {cur.get('require') or 'none'}" + (" (gespeichert, gilt im laufenden Saga nach spätestens 5 Sekunden)" if value else ""))


_SETTABLE = {"calls.horn": bool, "calls.horn_all": bool, "calls.horn_builtin": bool, "calls.horn_from": int,
             "chat_v3": bool}


def set_settings(args):
    """python run.py einstellen [schluessel=wert ...]  - ausgewaehlte Schalter ohne Admin-Oberflaeche setzen
    (Testlabor, Automatisierung). Ohne Argumente: aktuelle Werte anzeigen."""
    import auth
    import db
    db.wait_ready(30)
    db.init_schema()
    want = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep or k not in _SETTABLE:
            sys.exit("Aufruf: python run.py einstellen " + " ".join(f"{k}=..." for k in _SETTABLE))
        if _SETTABLE[k] is bool:
            if v.lower() not in ("1", "0", "true", "false", "an", "aus", "on", "off"):
                sys.exit(f"{k}: 1 oder 0 erwartet")
            want[k] = v.lower() in ("1", "true", "an", "on")
        else:
            try:
                want[k] = int(v)
            except ValueError:
                sys.exit(f"{k}: Zahl erwartet")
    s = auth.settings(fresh=True)
    calls = dict(s.get("calls") or {})
    for k, v in want.items():
        if k.startswith("calls."):
            calls[k[6:]] = v
        else:
            auth.save_setting(k, v)
    if any(k.startswith("calls.") for k in want):
        auth.save_setting("calls", calls)
    s = auth.settings(fresh=True)
    for k in _SETTABLE:
        v = (s.get("calls") or {}).get(k[6:]) if k.startswith("calls.") else s.get(k)
        print(f"{k}={'' if v is None else int(v) if isinstance(v, bool) else v}")
    if want:
        print("(gespeichert, gilt im laufenden Saga nach spätestens 5 Sekunden)")


def horn_pair(args):
    """python run.py horn-koppeln <name> [--teilen] [--ohne-udp]  - Kopplungs-Code fuer einen weiteren Knoten wie
    Admin -> Anrufe -> Erweitern; gibt das Docker-Deploy-Skript (mit dem einmaligen Code, 30 min gueltig) auf stdout aus."""
    import db
    names = [a for a in args if not a.startswith("--")]
    if len(names) != 1 or any(a not in ("--teilen", "--ohne-udp") for a in args if a.startswith("--")):
        sys.exit("Aufruf: python run.py horn-koppeln <name> [--teilen] [--ohne-udp]")
    db.wait_ready(30)
    db.init_schema()
    from saga import horn_ops
    import contextlib
    try:
        with contextlib.redirect_stdout(sys.stderr):        # Protokollzeilen nicht ins Skript
            r = horn_ops.pair_create(names[0], share="--teilen" in args, udp="--ohne-udp" not in args, by="cli")
    except horn_ops.PairError as e:
        sys.exit(f"Kopplung nicht möglich: {e}")
    sys.stdout.write(r["script_docker"])


def do_backup(with_files=False):
    """python run.py backup [--dateien]  - Datenbank-Backup nach /data/backups (laeuft auch neben dem Server).
    Die hochgeladenen Bilder/Dateien (/data/files) stecken NICHT im Datenbank-Backup - mit --dateien kommt ein
    tar.gz davon dazu (ohne Vorschaubilder, die entstehen neu)."""
    import db
    db.wait_ready(30)
    print("Backup:", db.backup("manuell"))
    if with_files:
        print("Dateien:", backup_files())
    else:
        print("Hinweis: Bilder und Dateien aus Seiten (/data/files) sind darin NICHT enthalten. "
              "Mitsichern: python run.py backup --dateien  (oder das Volume /data als Ganzes sichern).")


def backup_files():
    """STATE_DIR/files -> backups/saga-dateien-<zeit>.tar.gz (die neuesten BACKUP_KEEP bleiben)."""
    import tarfile
    import time
    import db
    state = os.environ.get("STATE_DIR", "/data")
    src = os.path.join(state, "files")
    os.makedirs(db.BACKUP_DIR, exist_ok=True)
    path = os.path.join(db.BACKUP_DIR, f"saga-dateien-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
    with tarfile.open(path + ".part", "w:gz") as tar:
        if os.path.isdir(src):
            tar.add(src, arcname="files", filter=lambda ti: None if ti.name.startswith("files/thumbs") else ti)
    os.replace(path + ".part", path)
    old = sorted(f for f in os.listdir(db.BACKUP_DIR) if f.startswith("saga-dateien-") and f.endswith(".tar.gz"))
    for f in old[:-db.BACKUP_KEEP] if db.BACKUP_KEEP > 0 else []:
        try:
            os.remove(os.path.join(db.BACKUP_DIR, f))
        except OSError:
            pass
    return path


def do_restore(path):
    """python run.py restore <datei>  - Backup zurueckspielen. Saga vorher stoppen:
    docker compose stop saga && docker compose run --rm saga python run.py restore /data/backups/<datei>"""
    import db
    db.wait_ready(30)
    db.restore(path)
    print(f"Zurueckgespielt: {path} (Schema-Stand {db.schema_version()})")
    print("Hinweis: Bilder/Dateien (/data/files) gehoeren nicht zum Datenbank-Backup. Passende Sicherung "
          "(saga-dateien-<zeit>.tar.gz) bei Bedarf so zurueckholen: tar -xzf /data/backups/<datei> -C /data")


def _opt(args, name):
    """--name wert aus der Liste nehmen -> (wert oder None, restliche Argumente)."""
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            sys.exit(f"{name} braucht einen Wert")
        return args[i + 1], args[:i] + args[i + 2:]
    return None, args


def _backups():
    import db
    db.wait_ready(30)
    db.init_schema()
    from saga import backups
    return backups


def do_backup_extern():
    """python run.py backup-extern  - verschluesseltes Backup jetzt ins eingerichtete Ziel (Admin -> Backups)."""
    bk = _backups()
    ok, res = bk.run("cli")
    if ok:
        print(f"Backup {res['name']} im Ziel ({bk.size_text(res['total'])}).")
    else:
        sys.exit("Backup fehlgeschlagen: " + (bk.text(res.code, res.p) if res else "laeuft schon"))


def do_backup_fetch(args):
    """python run.py backup-holen [<lauf>] [--liste] [--nach <ordner>]  - Lauf aus dem Ziel holen und Pruefsummen
    vergleichen (ohne Lauf: den neuesten). Frischer Stack ohne eingerichtetes Ziel: SAGA_BACKUP_URL (WebDAV-Adresse des
    Backup-Ordners), SAGA_BACKUP_USER, SAGA_BACKUP_PASSWORD."""
    bk = _backups()
    dest, args = _opt(args, "--nach")
    if "--liste" in args:
        dav = bk.cli_dav()
        try:
            for r in bk.list_runs(dav):
                print(r["name"], "" if r["complete"] else "(unvollstaendig)")
        except bk.BackupError as ex:
            sys.exit("Ziel: " + bk.text(ex.code, ex.p))
        finally:
            dav.close()
        return
    path = bk.fetch(args[0] if args else None, dest)
    print(f"Geholt und geprueft: {path}")


def do_backup_check(args):
    """python run.py backup-pruefen --schluessel <datei> [<lauf>|<ordner>]  - Testwiederherstellung in eine
    Wegwerf-Datenbank (danach geloescht); die echte Datenbank bleibt unberuehrt."""
    key, args = _opt(args, "--schluessel")
    if not key:
        sys.exit("Aufruf: python run.py backup-pruefen --schluessel <datei> [<lauf>]")
    _backups().check_cmd(key, args[0] if args else None)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("admin-passwort", "reset-admin"):
        reset_admin(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_USERNAME") or "admin")
    elif cmd in ("2fa-zuruecksetzen", "reset-2fa"):
        if len(sys.argv) < 3:
            sys.exit("Aufruf: python run.py 2fa-zuruecksetzen <benutzer>")
        reset_mfa(sys.argv[2])
    elif cmd in ("2fa-richtlinie", "2fa-policy"):
        mfa_policy(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "backup":
        do_backup(with_files=any(a in ("--dateien", "--files") for a in sys.argv[2:]))
    elif cmd == "restore":
        key, rest = _opt(sys.argv[2:], "--schluessel")
        if key:                                    # verschluesselter Lauf aus dem Backup-Ziel (saga/backups.py)
            if len(rest) > 1:
                sys.exit("Aufruf: python run.py restore --schluessel <datei> [<lauf>|<ordner>]")
            _backups().restore_cmd(key, rest[0] if rest else None)
        else:
            if len(sys.argv) < 3:
                sys.exit("Aufruf: python run.py restore /data/backups/<datei>.dump  |  restore --schluessel <datei> <lauf>")
            do_restore(sys.argv[2])
    elif cmd == "backup-extern":
        do_backup_extern()
    elif cmd == "backup-holen":
        do_backup_fetch(sys.argv[2:])
    elif cmd == "backup-pruefen":
        do_backup_check(sys.argv[2:])
    elif cmd == "einstellen":
        set_settings(sys.argv[2:])
    elif cmd == "horn-koppeln":
        horn_pair(sys.argv[2:])
    elif cmd in ("version", "--version"):
        from version import VERSION_LABEL
        import db
        print(f"Saga {VERSION_LABEL} (Datenbank-Schema {db.SCHEMA_VERSION})")
    elif cmd:
        sys.exit(f"Unbekannter Befehl: {cmd} (moeglich: admin-passwort, 2fa-zuruecksetzen, 2fa-richtlinie, backup, restore, backup-extern, backup-holen, backup-pruefen, einstellen, horn-koppeln, version)")
    else:
        asyncio.run(main())
