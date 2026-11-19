"""Abgebrochene Browser-Anfragen (app.py canonical_host): bricht der Client ab, bevor die Antwort beginnt (Streaming-Antwort,
Starlette: "No response returned."), bleibt das leise - kein Fehler/Stacktrace. Antwortet die App bei offener Verbindung
gar nicht, bleibt es beim Fehler wie bisher; normale Anfragen (/healthz) und die Umleitung auf BASE_URL unveraendert.
Ohne Server: treibt die App direkt ueber ASGI (Testpfade nur in diesem Prozess). Umgebung wie die anderen Tests
(DATABASE_URL, BASE_URL); PYTHONPATH=tests/pgshim:. python tests/abbruch_leise.py"""
import asyncio
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.responses import StreamingResponse  # noqa: E402
import app as A  # noqa: E402

res = []


def ok(name, cond):
    res.append(bool(cond))
    print(("OK   " if cond else "FAIL ") + name, flush=True)


async def _slow():
    await asyncio.sleep(0.2)                       # erst rechnen, dann antworten
    yield b"x"


@A.app.get("/__test/stream")
async def _t_stream():
    return StreamingResponse(_slow(), media_type="text/plain")


async def _t_empty(scope, receive, send):
    return None                                    # antwortet nie (Programmfehler) - muss weiter auffallen


async def _t_gone(scope, receive, send):
    # wie StreamingResponse (uvicorn spec 2.3), deren Abbruch-Wache gewinnt, bevor die Antwort beginnt: wartet auf
    # http.disconnect und endet ohne Antwort
    while (await receive())["type"] != "http.disconnect":
        pass


A.app.mount("/__test/leer", _t_empty)
A.app.mount("/__test/weg", _t_gone)
HOSTNAME = urlparse(os.environ["BASE_URL"]).netloc


def drive(path, gone, host=HOSTNAME):
    """-> (gesendete Nachrichten, Ausnahme). gone: der Client ist nach dem (leeren) Anfragekoerper weg."""
    sent, first = [], [True]

    async def receive():
        if first[0]:
            first[0] = False
            return {"type": "http.request", "body": b"", "more_body": False}
        if gone:
            return {"type": "http.disconnect"}      # schon weg (wie uvicorn: danach immer http.disconnect)
        await asyncio.sleep(3600)

    async def send(m):
        sent.append(m)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": "GET",
             "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "", "query_string": b"",
             "headers": [(b"host", host.encode())], "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 80)}

    async def run():
        try:
            await asyncio.wait_for(A.app(scope, receive, send), 10)
            return None
        except Exception as ex:  # noqa: BLE001
            return ex
    ex = asyncio.run(run())
    return sent, ex


def status(sent):
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)


def main():
    sent, ex = drive("/__test/weg/x", True)
    ok(f"Abbruch vor Antwortbeginn (App endet ohne Antwort): keine Ausnahme ({type(ex).__name__ if ex else None}: {ex})", ex is None)
    sent, ex = drive("/__test/stream", True)
    ok(f"... ebenso Streaming-Antwort bei Abbruch ({type(ex).__name__ if ex else None}: {ex})", ex is None)
    sent, ex = drive("/__test/stream", False)
    ok(f"... ohne Abbruch beginnt die Antwort wie bisher ({status(sent)})", ex is None and status(sent) == 200)
    sent, ex = drive("/__test/leer/x", False)
    ok(f"App antwortet nicht, Verbindung offen: Fehler wie bisher ({ex!r})", isinstance(ex, RuntimeError) and "No response returned" in str(ex))
    sent, ex = drive("/healthz", False)
    ok(f"/healthz unveraendert 200 ({status(sent)}, {ex})", ex is None and status(sent) == 200)
    if A.CANONICAL:
        sent, ex = drive("/login", False, "anders.example")
        ok(f"Umleitung auf BASE_URL unveraendert 308 ({status(sent)})", status(sent) == 308)
    print(f"\nABBRUCH-LEISE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()
