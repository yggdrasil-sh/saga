"""Startet Saga: Hauptseite (PORT, Standard 8080) und Admin-Bereich (ADMIN_PORT, Standard 8081)
in einem Prozess - beide teilen sich Datenbank-Pool und Zwischenspeicher (z. B. wirkt "Konto sperren"
im Admin-Bereich sofort auf der Hauptseite)."""
import asyncio
import logging
import os
import re

import uvicorn


class _HideSecrets(logging.Filter):
    """Zugriffs-Log: Kopplungs-Code aus /app/pair?c=... und Bestaetigungs-Wert aus /app/handoff?go=... unkenntlich machen."""
    RX = re.compile(r"([?&](?:c|go)=)[^&\s]+")

    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str) and "/app/" in record.args[2]:
            a = list(record.args)
            a[2] = self.RX.sub(r"\1***", a[2])
            record.args = tuple(a)
        return True


def _config(app, host, port, lifespan):
    # timeout_graceful_shutdown: offene Live-Verbindungen (SSE) halten das Beenden sonst beliebig lange auf
    return uvicorn.Config(app, host=host, port=port, proxy_headers=True, lifespan=lifespan,
                          forwarded_allow_ips=os.environ.get("TRUSTED_PROXIES", "127.0.0.1"),
                          server_header=False, log_level=os.environ.get("LOG_LEVEL", "info"), timeout_graceful_shutdown=5)


async def main():
    import app as hk          # prueft die Konfiguration (BASE_URL, SECRET_KEY ...)
    import admin

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


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("admin-passwort", "reset-admin"):
        reset_admin(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_USERNAME") or "admin")
    elif cmd == "backup":
        do_backup(with_files=any(a in ("--dateien", "--files") for a in sys.argv[2:]))
    elif cmd == "restore":
        if len(sys.argv) < 3:
            sys.exit("Aufruf: python run.py restore /data/backups/<datei>.dump")
        do_restore(sys.argv[2])
    elif cmd in ("version", "--version"):
        from version import VERSION
        import db
        print(f"Saga {VERSION} (Datenbank-Schema {db.SCHEMA_VERSION})")
    elif cmd:
        sys.exit(f"Unbekannter Befehl: {cmd} (moeglich: admin-passwort, backup, restore, version)")
    else:
        asyncio.run(main())
