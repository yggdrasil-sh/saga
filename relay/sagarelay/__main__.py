"""Start: python -m sagarelay (RELAY_LISTEN, RELAY_PORT; Konfiguration siehe sagarelay/config.py).

Genau EIN Prozess (Ratenzaehler im Speicher) - nicht mit mehreren Workern starten."""
import os

import uvicorn

from sagarelay.app import create_app


def main():
    uvicorn.run(create_app(), host=os.environ.get("RELAY_LISTEN", "0.0.0.0"), port=int(os.environ.get("RELAY_PORT", "8080")),
                workers=1, access_log=False, server_header=False, proxy_headers=False, log_level="warning",
                timeout_keep_alive=30)


if __name__ == "__main__":
    main()
