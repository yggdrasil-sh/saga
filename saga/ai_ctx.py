"""Kontext-Ausweis (Saga 2.6, Bugs-DB 2266 K-A4, Konzept K8): EINE Stelle, die waehlt, was die KI zu lesen bekommt, und
belegt, was gelesen wurde ("Ich lese dafuer").

Quellen (SOURCES): pages (eigene und fuer mich freigegebene Seiten, saga.ai.gather), tasks (offene Aufgaben), calendar
(Termine der naechsten 14 Tage), chat (vom Client mitgeschickter Verlauf beim @-Zuruf). Eine Quelle wirkt nur, wenn der
Schalter der Anfrage (want) UND der Schalter der Person (prefs, user_kv 'ai_sources', Standard: alles an) an sind.

Wege (way): ask (Frag deine Seiten), page (Hilfen auf EINER Seite inkl. Zuordnen - die Person hat die Seite selbst
gewaehlt; die Personen-Schalter steuern die automatische Auswahl und gelten hier nicht), chat (@-Zuruf), job und idea
(Kollegin, ab K-B; heute wie ask).

Grenzen (immer, unabhaengig von Schaltern):
- Nur der Heimatserver: foederierte Inhalte bleiben draussen (Eigentuemer mit '@', negative Seiten-IDs), fremde
  Personen ('@name@host', Gaeste) bekommen gar keinen Kontext.
- Papierkorb, Archiv und Vorlagen nie (saga.ai.visible_docs, sharing.Access.tree), fremde Seiten nur mit Freigabe.
- Chat-Verlauf nur bei way == "chat" (RT-41: nie fuer Anrede, Stil oder andere Wege); der Chat-Weg bekommt nie Seiten,
  Aufgaben oder Kalender.
- read enthaelt NUR Kennungen, nie Text: note:<id>, tasks:open, cal:<datum>/<tage>, chat:<chat-id>:<anzahl>.
Der Datenrahmen (Markierungen, Entschaerfen) bleibt bei saga.ai_frame (2260) und den *_messages-Funktionen in saga.ai.

record(c, history_id=..., ctx=...) legt read zum Verlaufseintrag (nur wenn der Verlauf an ist - ai._save_history ruft es
in derselben Transaktion). Fuer Auftraege (job_id, Ereignis job_event 'read') kommt die Anbindung mit K-A2.

Importe: saga.ai wird erst beim Aufruf geladen (ai.py nutzt dieses Modul; so entsteht kein Import-Zyklus).
Tests: tests/kollegin_rechte.py, tests/ki_golden.py (Modell-Nachrichten unveraendert), Messung tools/perf/kollegin.py.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime

from db import connect as _db
from saga.core import TZ, _bad
from saga.sharing import is_remote

SOURCES = ("pages", "tasks", "calendar", "chat")
WAYS = ("ask", "page", "chat", "job", "idea")
KV = "ai_sources"
CAL_DAYS = 14                                    # wie saga.ai.extra_context


@dataclass
class Ctx:
    """Ergebnis von select: was das Modell bekommt und der Ausweis dazu."""
    text: str = ""                               # Auszuege aus Seiten (ask/job/idea)
    sources: tuple = ()                          # wirksame Quellen (Teilmenge von SOURCES)
    read: list = field(default_factory=list)     # nur Kennungen (siehe Kopf)
    origins: list = field(default_factory=list)  # Quellen fuer das Ereignis 'sources': [{n, id, title, owner?}]
    extra: str = ""                              # Aufgaben/Kalender (ask/job/idea)
    history: list = field(default_factory=list)  # Chat-Verlauf (nur way == "chat")
    page: object = None                          # way == "page": geladene Seite (Form je nach Aktion, siehe saga.ai)
    error: object = None                         # way == "page": Fehlerantwort (404/403/400) statt Inhalt


def _default():
    return {s: True for s in SOURCES}


def prefs(uid, conn=None):
    """Schalter der Person -> {quelle: bool} (Standard: alle an; unbekannte/kaputte Werte zaehlen als an)."""
    if conn is None:
        with _db() as c:
            return prefs(uid, c)
    out = _default()
    r = conn.execute("SELECT v FROM user_kv WHERE uid=? AND k=?", (uid, KV)).fetchone()
    try:
        d = json.loads(r[0]) if r and r[0] else {}
    except ValueError:
        d = {}
    if isinstance(d, dict):
        for s in SOURCES:
            if d.get(s) is False:
                out[s] = False
    return out


def valid(d):
    """Angabe fuer set_prefs pruefen: dict mit bekannten Quellen und bool-Werten."""
    return isinstance(d, dict) and all(k in SOURCES and isinstance(v, bool) for k, v in d.items())


def set_prefs(uid, d, conn=None):
    """Schalter der Person setzen (nur genannte Quellen aendern sich) -> neue Schalter. ValueError bei falscher Form.
    Alles an = Standard: der Eintrag faellt weg."""
    if not valid(d):
        raise ValueError("ai_sources")
    if conn is None:
        with _db() as c, c:
            return set_prefs(uid, d, c)
    cur = prefs(uid, conn)
    cur.update(d)
    if all(cur.values()):
        conn.execute("DELETE FROM user_kv WHERE uid=? AND k=?", (uid, KV))
    else:
        conn.execute("INSERT OR REPLACE INTO user_kv (uid, k, v) VALUES (?, ?, ?)",
                     (uid, KV, json.dumps({s: v for s, v in cur.items() if not v}, sort_keys=True)))
    return cur


def _home(src):
    """Quelle vom Heimatserver? (foederierte Seiten haben negative IDs; Eigentuemer mit '@' filtert schon visible_docs)"""
    nid = src.get("id")
    return isinstance(nid, int) and nid > 0


def select(uid, q, *, way, budget, want=None, note_id=None, chat_id=None, history=None):
    """Kontext fuer einen KI-Weg waehlen -> Ctx. want: Schalter der Anfrage {quelle: bool} (fehlend = aus); bei
    way == "page" stattdessen {"assign": (db_id, prop_id)} fuer das Zuordnen. history: bereinigter Verlauf (nur chat)."""
    if way not in WAYS:
        raise ValueError(f"way {way!r}")
    want = want or {}
    if not uid or is_remote(uid):                # Gaeste anderer Server: nie Kontext (die Kollegin handelt nur hier)
        return Ctx(error=_not_found() if way == "page" else None)
    from saga import ai                           # spaet: ai.py importiert dieses Modul
    if way == "page":
        return _page(ai, uid, note_id, want)
    pf = prefs(uid)

    def on(s):
        return bool(want.get(s)) and pf[s]
    if way == "chat":                             # nur der mitgeschickte Verlauf - keine Seiten, Aufgaben, Termine
        hist = list(history or []) if on("chat") else []
        read = [f"chat:{int(chat_id)}:{len(hist)}"] if hist and chat_id is not None else []
        return Ctx(sources=("chat",) if hist else (), read=read, history=hist)
    # ask / job / idea: nie Chat-Verlauf (RT-41), auch wenn einer mitkommt
    used, read, text, origins, extra = [], [], "", [], ""
    if on("pages"):
        text, origins = ai.gather(uid, q, budget)
        if not all(_home(s) for s in origins):     # Abwehr (visible_docs liefert nur Heim-Seiten): Text und Belege
            text, origins = "", []                 # passen nur zusammen - lieber gar keine Seiten als ein Auszug ohne Beleg
        if origins:
            used.append("pages")
            read += [f"note:{s['id']}" for s in origins]
    tk, cal = on("tasks"), on("calendar")
    if tk or cal:
        extra = ai.extra_context(uid, tk, cal)
        if tk:
            used.append("tasks")
            read.append("tasks:open")
        if cal:
            used.append("calendar")
            read.append(f"cal:{datetime.now(TZ).date().isoformat()}/{CAL_DAYS}")
    return Ctx(text=text, sources=tuple(used), read=read, origins=origins, extra=extra)


def _not_found():
    return _bad("not found", 404)


def _page(ai, uid, note_id, want):
    """Hilfen auf einer Seite: genau diese Seite (und ihre eingebetteten Datenbanken mit Zugriff)."""
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return Ctx(error=_not_found())
    if nid <= 0:                                  # foederierte Seite: die KI liest nur Seiten dieses Servers
        return Ctx(error=_not_found())
    asg = want.get("assign")
    if asg:
        data, err, ids = ai._assign_load(uid, nid, asg[0], asg[1])
    else:
        data, err, ids = ai._page_load(uid, nid)
    if err is not None:
        return Ctx(error=err)
    return Ctx(sources=("pages",), read=[f"note:{i}" for i in ids], page=data)


def record(c, *, job_id=None, history_id=None, ctx):
    """Ausweis ablegen (nur Kennungen). history_id: zum Verlaufseintrag (sources.read) - der Aufrufer ruft das nur bei
    eingeschaltetem Verlauf. job_id: Ereignis job_event 'read' - kommt mit K-A2 (saga/jobs.py), bis dahin ein Fehler,
    damit kein Ausweis stillschweigend verloren geht."""
    read = [str(x) for x in (ctx.read if ctx else [])]
    if job_id is not None:
        raise NotImplementedError("ai_ctx.record(job_id=...): job_event 'read' kommt mit K-A2")
    if history_id is None:
        return
    r = c.execute("SELECT sources FROM ai_history WHERE id=?", (history_id,)).fetchone()
    if not r:
        return
    try:
        meta = json.loads(r[0] or "{}")
    except ValueError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta["read"] = read
    c.execute("UPDATE ai_history SET sources=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), history_id))
