"""Akteur eines Schreibvorgangs (2.6, Bugs-DB 2266 K-A1, Konzept K22): wer gerade schreibt - die Person selbst oder ein
Dienst (svc:kollegin, svc:bridge, svc:bot) im Auftrag einer Person.

- Unterste Schicht: keine Importe aus saga, nur Standardbibliothek. Jeder Bereich darf saga.actor importieren.
- Gesetzt wird der Akteur ueber eine ContextVar (acting()), nicht ueber Parameter: Werkzeuge rufen die bestehenden
  Schreibwege (pages, tasks, api_v1 ...) mit eigenen Verbindungen auf - die sehen den Akteur trotzdem. Threads/Worker
  erben ihn nicht von selbst (neuer Thread = neuer Kontext); FastAPI-Endpunkte ohne acting() haben keinen.
- ACHTUNG Kontext-Kopien (Pruefer K-A1): asyncio.create_task(), loop.run_in_executor() mit contextvars.copy_context(),
  starlette.run_in_threadpool und asyncio.to_thread KOPIEREN den Kontext - eine Aufgabe/ein Thread, die unter acting()
  entsteht, schreibt sonst noch als Dienst, wenn der with-Block laengst zu Ende ist. Wer unter acting() etwas
  Langlebiges startet (Hintergrund-Task, eigener Thread mit kopiertem Kontext, Zeitgeber), setzt darin ausdruecklich
  acting(None) bzw. einen eigenen Akteur. saga.jobs setzt acting() nur im eigenen Worker-Thread je Schritt (with-Block,
  danach nichts Klebriges) und startet darunter weder Tasks noch Threads.
- Idempotenz (saga.jobs): traegt der Akteur idem (Schritt-Schluessel), stempelt saga.bus.fire() jede Wirkung in die
  Schreib-Transaktion (job_change); ohne Akteur bzw. ohne idem ist der Stempel ein reiner No-op.
- Ohne Akteur (Normalfall: die Person im Web, in der App, ueber API-Tokens) liefern current() None und cols() nur NULL -
  Bestand und Verhalten bleiben unveraendert. author/updated_by/actor_uid bleiben IMMER die Person; der Dienst steht
  zusaetzlich in den Spalten actor_kind/actor/on_behalf_of (note_version, notification) bzw. note.updated_actor.
- note.updated_actor haelt nur Dienste fest (note_actor()): wer den aktuellen Stand geschrieben hat, steht schon in
  note.updated_by. Beim Sichern einer Fassung (saga.notes._insert_version) wird daraus actor_kind 'service', actor und
  on_behalf_of (= note.updated_by, die Person, fuer die der Dienst schrieb)."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

KINDS = ("person", "service")
SERVICES = ("svc:kollegin", "svc:bridge", "svc:bot")


@dataclass(frozen=True)
class Actor:
    """kind: 'person' | 'service'; actor: Kennung (Dienst aus SERVICES bzw. uid der Person); on_behalf_of: uid der
    Person, fuer die ein Dienst handelt (None = niemand bzw. die Person selbst). job_id/step/idem: Auftrag, Schritt und
    Idempotenz-Schluessel (saga.jobs, K-A2), attempt: Kennung des einen Versuchs (Worker-Lauf) dieses Schritts - nur im
    Speicher, nicht in den Akteur-Spalten. on_behalf_of ist immer eine lokale Person: keine Foederations-Kennung
    (nichts mit "@") und kein Dienst ("svc:") - die Kollegin handelt nur auf dem Heimatserver (K8)."""
    kind: str
    actor: str
    on_behalf_of: str | None = None
    job_id: int | None = None
    step: int | None = None
    idem: str | None = None
    attempt: str | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unbekannte Akteur-Art: {self.kind!r}")
        if not isinstance(self.actor, str) or not self.actor:
            raise ValueError("Akteur ohne Kennung")
        if self.kind == "service" and self.actor not in SERVICES:
            raise ValueError(f"unbekannter Dienst: {self.actor!r}")
        if self.kind == "person" and self.actor.startswith("svc:"):
            raise ValueError("Person mit Dienst-Kennung")
        if self.on_behalf_of is not None and (not isinstance(self.on_behalf_of, str) or not self.on_behalf_of
                                              or self.on_behalf_of.startswith("svc:")):
            raise ValueError("on_behalf_of muss eine Person sein")
        if self.on_behalf_of is not None and ("@" in self.on_behalf_of or self.on_behalf_of != self.on_behalf_of.strip()):
            raise ValueError("on_behalf_of muss eine lokale Person sein (keine Foederations-Kennung)")
        if self.idem is not None and (not isinstance(self.idem, str) or not self.idem):
            raise ValueError("idem muss eine nicht leere Kennung sein")


_current: ContextVar = ContextVar("saga_actor", default=None)


def current():
    """Akteur des laufenden Kontexts oder None (= die angemeldete Person selbst)."""
    return _current.get()


@contextmanager
def acting(a):
    """Alles im with-Block schreibt als Akteur a (verschachtelt: der innere gilt, danach wieder der aeussere)."""
    if a is not None and not isinstance(a, Actor):
        raise TypeError("acting() erwartet einen Actor oder None")
    tok = _current.set(a)
    try:
        yield a
    finally:
        _current.reset(tok)


def cols():
    """(actor_kind, actor, on_behalf_of) fuer Akteur-Spalten - ohne Akteur (None, None, None)."""
    a = _current.get()
    return (a.kind, a.actor, a.on_behalf_of) if a is not None else (None, None, None)


def note_actor():
    """Wert fuer note.updated_actor: der Dienst, der gerade schreibt - None ohne Akteur bzw. fuer eine Person (die steht
    schon in note.updated_by)."""
    a = _current.get()
    return a.actor if a is not None and a.kind == "service" else None
