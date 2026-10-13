"""Startet Saga: Hauptseite (PORT, Standard 8080) und Admin-Bereich (ADMIN_PORT, Standard 8081)
in einem Prozess - beide teilen sich Datenbank-Pool und Zwischenspeicher (z. B. wirkt "Konto sperren"
im Admin-Bereich sofort auf der Hauptseite)."""
import asyncio
import os

import uvicorn


def _config(app, host, port, lifespan):
    return uvicorn.Config(app, host=host, port=port, proxy_headers=True, lifespan=lifespan,
                          forwarded_allow_ips=os.environ.get("TRUSTED_PROXIES", "127.0.0.1"),
                          server_header=False, log_level=os.environ.get("LOG_LEVEL", "info"))


async def main():
    import app as hk          # prueft die Konfiguration (BASE_URL, SECRET_KEY ...)
    import admin

    servers = [uvicorn.Server(_config(hk.app, os.environ.get("HOST", "0.0.0.0"), int(os.environ.get("PORT", "8080")), "on"))]
    if os.environ.get("ADMIN_PORT", "8081") not in ("", "0", "off"):
        servers.append(uvicorn.Server(_config(admin.admin, os.environ.get("ADMIN_HOST", "0.0.0.0"),
                                              int(os.environ.get("ADMIN_PORT", "8081")), "off")))
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for s in servers:              # Signal kommt nur bei einem Server an -> beide beenden
        s.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


def reset_admin(name):
    """python run.py admin-passwort [benutzer]  - neues Zufallspasswort fuer ein Admin-Konto (Notfall)."""
    import auth
    import db
    db.wait_ready(30)
    db.init_schema()
    user, pw = auth.reset_admin(name)
    print(f"Admin-Konto „{user}“ – neues Passwort: {pw}")
    print(f"(steht auch in {auth.PW_FILE}; nach dem Anmelden im Admin-Bereich ändern)")


def do_backup():
    """python run.py backup  - Datenbank-Backup nach /data/backups (laeuft auch neben dem Server)."""
    import db
    db.wait_ready(30)
    print("Backup:", db.backup("manuell"))


def do_restore(path):
    """python run.py restore <datei>  - Backup zurueckspielen. Saga vorher stoppen:
    docker compose stop saga && docker compose run --rm saga python run.py restore /data/backups/<datei>"""
    import db
    db.wait_ready(30)
    db.restore(path)
    print(f"Zurueckgespielt: {path} (Schema-Stand {db.schema_version()})")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("admin-passwort", "reset-admin"):
        reset_admin(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_USERNAME") or "admin")
    elif cmd == "backup":
        do_backup()
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
