"""Testuhr und Takt (2.6, Bugs-DB 2265 K-T): eine Uhr fuer Auftraege, Fristen und Morgen-Laeufe der Kollegin, die Tests
vorstellen koennen - ohne zu warten. Im Betrieb ist now() einfach time.time().

Nur fuer Tests, dreifach gesichert (Pruefer K-T): die Endpunkte gibt es nur mit SAGA_TEST_HOOKS=1 UND einem Geheimnis
SAGA_TEST_TOKEN (mindestens 32 Zeichen) in der Umgebung; jede Anfrage braucht den Kopf "X-Saga-Test: <token>" (erzwingt
auch einen Preflight - kein Aufruf aus fremden Webseiten), kommt von 127.0.0.1/::1 und traegt keine Weiterleitungs-Koepfe
(Forwarded, X-Forwarded-*, X-Real-IP, Via - ein Reverse-Proxy auf demselben Host kommt so nicht durch). Sonst 404 fuer jede
Methode, als gaebe es sie nicht. Beim Start steht "TESTHAKEN AKTIV" im Log.

  GET  /_test/clock                    -> {now, offset, frozen}
  POST /_test/clock {set?: ts, advance?: sekunden, freeze?: bool, reset?: true}
  POST /_test/tick                     -> faellige Arbeit sofort anstossen (registrierte Takt-Haken, z. B. Auftraege)
  POST /_test/flush                    -> Puffer leeren (registrierte Haken, z. B. Mitteilungen, Zaehler)

Pakete haengen sich ein: testclock.on_tick(fn), testclock.on_flush(fn) - fn() ohne Argumente, Ausnahmen werden gezaehlt.
Tests: tests/testuhr.py
"""
import hmac
import math
import os
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()
_lock = threading.Lock()
_state = {"offset": 0.0, "frozen": None}
_TICK, _FLUSH = [], []


def now():
    """Aktuelle Zeit (Sekunden). Im Betrieb time.time(); in Tests verschoben bzw. angehalten."""
    f = _state["frozen"]
    return f if f is not None else time.time() + _state["offset"]


def on_tick(fn):
    if fn not in _TICK:
        _TICK.append(fn)
    return fn


def on_flush(fn):
    if fn not in _FLUSH:
        _FLUSH.append(fn)
    return fn


_FWD = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "via")
_MAX = 1e11                                   # Sekunden: Zeitpunkte und Spruenge bleiben endlich und plausibel


def _token():
    tok = os.environ.get("SAGA_TEST_TOKEN") or ""
    return tok if len(tok) >= 32 else ""


def enabled():
    return os.environ.get("SAGA_TEST_HOOKS") == "1" and bool(_token())


def _local(request):
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1")


def _guard(request):
    if not enabled() or not _local(request):
        return False
    if any(h in request.headers for h in _FWD):
        return False
    return hmac.compare_digest(request.headers.get("x-saga-test", "").encode(), _token().encode())


if enabled():
    print("testclock: TESTHAKEN AKTIV (/_test/clock|tick|flush, nur 127.0.0.1 mit X-Saga-Test) - nie im Betrieb", flush=True)


def _view():
    return {"now": now(), "offset": _state["offset"], "frozen": _state["frozen"] is not None}


def _run(hooks):
    n = err = 0
    for fn in list(hooks):
        try:
            fn()
            n += 1
        except Exception as ex:  # noqa: BLE001 - Test-Haken duerfen den Server nicht stoeren
            err += 1
            print(f"testclock: Haken {getattr(fn, '__name__', '?')} ({type(ex).__name__})", flush=True)
    return {"ran": n, "errors": err}


def _num(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    v = float(v)
    return v if math.isfinite(v) and abs(v) < _MAX else None


def set_clock(b):
    """Testuhr aendern (auch direkt aus Tests im selben Prozess) -> neuer Stand. Angehalten bleibt angehalten."""
    with _lock:
        if b.get("reset"):
            _state.update(offset=0.0, frozen=None)
        v = _num(b.get("set"))
        if v is not None:
            if _state["frozen"] is not None:
                _state["frozen"] = v
            else:
                _state["offset"] = v - time.time()
        v = _num(b.get("advance"))
        if v is not None:
            if _state["frozen"] is not None:
                _state["frozen"] += v
            else:
                _state["offset"] += v
        if "freeze" in b:
            if b.get("freeze") is True and _state["frozen"] is None:
                _state["frozen"] = now()
            elif b.get("freeze") is False and _state["frozen"] is not None:
                _state["offset"] = _state["frozen"] - time.time()   # laeuft ab der angehaltenen Zeit weiter
                _state["frozen"] = None
    return _view()


@router.get("/_test/clock")
def get_clock(request: Request):
    if not _guard(request):
        return Response(status_code=404)
    return JSONResponse(_view(), headers={"Cache-Control": "no-store"})


@router.post("/_test/clock")
async def post_clock(request: Request):
    if not _guard(request):
        return Response(status_code=404)
    try:
        b = await request.json()
    except Exception:  # noqa: BLE001
        b = {}
    return JSONResponse(set_clock(b if isinstance(b, dict) else {}), headers={"Cache-Control": "no-store"})


@router.post("/_test/tick")
def post_tick(request: Request):
    if not _guard(request):
        return Response(status_code=404)
    return JSONResponse(_run(_TICK), headers={"Cache-Control": "no-store"})


@router.post("/_test/flush")
def post_flush(request: Request):
    if not _guard(request):
        return Response(status_code=404)
    return JSONResponse(_run(_FLUSH), headers={"Cache-Control": "no-store"})


@router.api_route("/_test/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def _nothing(rest: str):
    """Alles andere unter /_test (auch falsche Methoden): 404 - nie 405, damit nichts verraet, dass es die Pfade gibt."""
    return Response(status_code=404)
