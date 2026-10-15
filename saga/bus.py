"""Ereignisse innerhalb von Saga (1.4) - unterste Schicht, ohne Abhaengigkeiten zu anderen Bereichen.

Zwei Wege:
- fire(name, c, ...): SYNCHRON in der laufenden Transaktion. Bereiche melden, was passiert ist (Freigabe geaendert,
  Kommentar geschrieben, Seite gespeichert ...); wer zuhoert (saga.notify: Benachrichtigungen anlegen, saga.live:
  Live-Ereignisse vormerken), haengt sich mit on(name, fn) an. So braucht sharing.py weder notify noch live zu kennen
  (keine zyklischen Importe). Fehler eines Zuhoerers brechen die eigentliche Aktion nie ab.
- emit(kind, payload): fuer den Live-Kanal. Thread-sicher; die Zustellung (wer darf was sehen) macht saga.live in der
  Event-Schleife. Ohne laufenden Live-Kanal (Kommandozeile, Tests im eigenen Prozess) passiert nichts.
  Meist ueber c.after_commit(...) aufgerufen: dann erst nach dem Commit, bei Rollback gar nicht.

Die Zuhoerer melden sich beim Import ihres Moduls an - app.py importiert saga.notify und saga.live."""
import threading
import traceback

_hooks = {}                 # name -> [fn]
_sink = None                # fn(kind, payload) - von saga.live gesetzt (thread-sicher)
_lock = threading.Lock()


def on(name, fn):
    """Zuhoerer anmelden: fn(c, **daten). Doppelte Anmeldung (Modul neu geladen) wird ignoriert."""
    with _lock:
        lst = _hooks.setdefault(name, [])
        if not any(getattr(f, "__qualname__", f) == getattr(fn, "__qualname__", fn) and getattr(f, "__module__", "") == getattr(fn, "__module__", "")
                   for f in lst):
            lst.append(fn)


def fire(name, c, **data):
    """Synchron alle Zuhoerer aufrufen (in der Transaktion von c). Fehler werden protokolliert, nicht weitergereicht."""
    sp = getattr(c, "savepoint", None)
    for fn in list(_hooks.get(name, ())):
        try:
            if sp is None:
                fn(c, **data)
            else:
                with sp():                   # Fehler eines Zuhoerers: nur dessen Teil zurueck, die Aktion bleibt (BUG-009)
                    fn(c, **data)
        except Exception as ex:  # noqa: BLE001 - eine Benachrichtigung darf Speichern/Teilen nie verhindern
            print(f"bus: {name} -> {getattr(fn, '__module__', '?')}.{getattr(fn, '__name__', '?')}: {type(ex).__name__}: {ex}", flush=True)
            traceback.print_exc()


def set_sink(fn):
    global _sink
    _sink = fn


def emit(kind, payload):
    """Live-Ereignis abschicken (aus jedem Thread). Ohne Live-Kanal: nichts."""
    s = _sink
    if s is not None:
        try:
            s(kind, payload)
        except Exception as ex:  # noqa: BLE001 - Event-Schleife beendet (Herunterfahren)
            print(f"bus: emit {kind}: {type(ex).__name__}: {ex}", flush=True)


def emit_after(c, kind, payload):
    """emit() erst nach dem Commit der Transaktion von c (bei Rollback nie)."""
    after = getattr(c, "after_commit", None)
    if after is None:
        emit(kind, payload)
    else:
        after(lambda: emit(kind, payload))
