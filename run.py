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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ("admin-passwort", "reset-admin"):
        reset_admin(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ADMIN_USERNAME") or "admin")
    else:
        asyncio.run(main())
