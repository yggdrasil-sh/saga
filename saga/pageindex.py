"""Abgeleitete Daten der Seiten (2.2, schneller): Suchtext, Vorschaubild und Seitenverweise je Seite - in eigenen
Tabellen (db.py: note_derived, note_link, note_stale), damit Suche, Rueckverweise und Datenbank-Zeilen nicht mehr jeden
Inhalt laden und in Python parsen.

- Ein Trigger in Postgres traegt jede neue Seite und jede Aenderung an Titel, Inhalt, Format oder Tags in note_stale ein
  - egal, welcher Code (oder welche aeltere Version) speichert. Ein Hintergrund-Thread arbeitet die Eintraege ab.
- Abfragen nutzen die abgeleiteten Daten nur fuer Seiten OHNE Eintrag in note_stale; eingetragene (gerade geaenderte,
  noch nicht abgeglichene) Seiten pruefen sie wie bisher direkt am Inhalt - mit demselben Datenstand (eine Anweisung
  bzw. eine REPEATABLE-READ-Transaktion). Das Ergebnis haengt damit nie davon ab, wie weit der Abgleich ist.
- Abgeleitet wird mit genau den Funktionen, die bisher bei jeder Abfrage liefen (notes._note_text, notes._tags_load,
  databases._first_image, Verweis-Muster wie bisher instr(body, '/notes#<id>\\"'))."""
import re
import threading
import time
from db import connect as _db
from saga.databases import _first_image
from saga.notes import _note_text, _tags_load

SEP = "\x01"                       # trennt Titel, Text und Tags im Suchtext (Steuerzeichen, in Titeln/Tags nie erlaubt)
# Bisher: instr(body, '/notes#<id>\"') - im gespeicherten JSON steht nach der Zahl ein maskiertes Anfuehrungszeichen.
# Nur ASCII-Ziffern; fuehrende Null trifft keine ID (str(id) hat keine), > 19 Stellen gibt es nicht.
_LINK = re.compile(r'/notes#([0-9]{1,19})\\"')
BATCH = 100
VERSION = 2                         # Ableitung (derive) geaendert -> hochzaehlen: beim naechsten Start wird alles neu abgeglichen


def derive(title, body, fmt, tags):
    """-> (search, preview, tags, {verwiesene ids}) fuer eine Seite."""
    tl = [x.lower() for x in _tags_load(tags)]
    # Suchtext: Titel SEP Text SEP Tags; Tags allein: SEP tag1 SEP tag2 SEP - ein Tag-Filter sucht SEP + tag + SEP
    search = (title or "").lower() + SEP + _note_text(fmt, body).lower() + SEP + SEP.join(tl)
    links = set()
    if fmt == "blocks":
        for d in _LINK.findall(body or ""):
            if d[0] != "0" and int(d) < 2 ** 63:
                links.add(int(d))
    return search, _first_image(body, fmt), (SEP + SEP.join(tl) + SEP if tl else ""), links


def refresh(limit=BATCH, uids=None):
    """Bis zu `limit` veraltete Seiten neu ableiten -> Anzahl. Wartet nie auf andere: Seiten, die gerade jemand speichert
    (Sperre auf note_stale) oder loescht (Sperre auf note), bleiben fuer den naechsten Durchlauf stehen.
    Reihenfolge ist wichtig: ERST die Eintraege in note_stale sperren, DANN (eigene Anweisung, neuer Datenstand) die
    Inhalte lesen. Wer eine dieser Seiten ab jetzt aendert, haengt mit seinem Trigger an unserer Sperre und traegt die
    Seite nach unserem Commit neu ein - wir koennen also nie einen Stand ableiten, der aelter ist als der zuletzt
    bestaetigte, und dabei den Eintrag einer neueren Aenderung loeschen. (Inhalte in derselben Anweisung wie die Sperre zu
    lesen reicht nicht: FOR KEY SHARE liest bei einer gleichzeitig bestaetigten Aenderung den alten Stand.)"""
    with _db() as c, c:
        ids = [r[0] for r in c.execute("SELECT note_id FROM note_stale " + ("WHERE uid = ANY(?) " if uids else "")
                                       + "ORDER BY note_id LIMIT ? FOR UPDATE SKIP LOCKED",
                                       ((list(uids), limit) if uids else (limit,))).fetchall()]
        if not ids:
            return 0
        # KEY SHARE: wer eine Seite gerade loescht, wird uebersprungen; wer danach loeschen will, wartet auf uns
        rows = c.execute("SELECT id, title, body, format, tags FROM note WHERE id = ANY(?) FOR KEY SHARE SKIP LOCKED",
                         (ids,)).fetchall()
        der, links, done = [], [], []
        for nid, title, body, fmt, tags in rows:
            search, preview, tg, ln = derive(title, body, fmt, tags)
            der.append((nid, search, preview, tg))
            links += [(nid, d) for d in sorted(ln)]
            done.append(nid)
        if done:
            c.executemany("INSERT INTO note_derived (note_id, search, preview, tags) VALUES (?,?,?,?) "
                          "ON CONFLICT (note_id) DO UPDATE SET search=EXCLUDED.search, preview=EXCLUDED.preview, tags=EXCLUDED.tags", der)
            c.execute("DELETE FROM note_link WHERE src = ANY(?)", (done,))
            if links:
                c.executemany("INSERT INTO note_link (src, dst) VALUES (?,?) ON CONFLICT DO NOTHING", links)
            c.execute("DELETE FROM note_stale WHERE note_id = ANY(?)", (done,))
    return len(ids)


def pending(uids=None):
    """Anzahl noch nicht abgeglichener Seiten (alle bzw. dieser Konten)."""
    with _db() as c:
        if uids:
            return c.execute("SELECT COUNT(*) FROM note_stale WHERE uid = ANY(?)", (list(uids),)).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM note_stale").fetchone()[0]


def like_any(q):
    """Suchbegriff -> LIKE-Muster '%q%' (%, _ und \\ woertlich)."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# --- Hintergrund-Abgleich ------------------------------------------------------------------------------------------
_wake = threading.Event()
_thread = {"t": None, "stop": False}
IDLE = 2.0                          # Sekunden zwischen zwei Blicken auf note_stale, wenn nichts zu tun ist


def kick():
    """Abgleich sofort anstossen (z. B. wenn eine Abfrage veraltete Seiten am Inhalt pruefen musste)."""
    _wake.set()


def _check_version():
    """Stammen die gespeicherten Ableitungen von einer anderen derive()-Fassung, alles zum Abgleich vormerken."""
    with _db() as c, c:
        r = c.execute("SELECT v FROM setting WHERE k='pageindex'").fetchone()
        if r and r[0] == str(VERSION):
            return
        c.execute("INSERT INTO note_stale (note_id, uid) SELECT id, uid FROM note ON CONFLICT (note_id) DO UPDATE SET n = note_stale.n + 1")
        c.execute("INSERT OR REPLACE INTO setting (k, v) VALUES ('pageindex', ?)", (str(VERSION),))


def _loop():
    try:
        _check_version()
    except Exception as ex:  # noqa: BLE001
        print(f"pageindex: {type(ex).__name__}: {ex}", flush=True)
    burst, mark = 0, 2000
    while not _thread["stop"]:
        try:
            n = refresh()
            burst += n
            # Grosser Rueckstand (erster Start, Import): Statistik unterwegs (nach 2.000, 8.000, 32.000 ... Seiten) und am
            # Ende auffrischen - sonst plant Postgres die Suche bis zum naechsten Autovacuum mit leeren Tabellen
            if burst >= mark or (not n and burst >= 2000):
                mark *= 4
                with _db() as c, c:
                    c.execute("ANALYZE note_derived")
                    c.execute("ANALYZE note_link")
                    c.execute("ANALYZE note_stale")
        except Exception as ex:  # noqa: BLE001 - Abgleich darf nie den Server stoeren; naechster Versuch spaeter
            print(f"pageindex: {type(ex).__name__}: {ex}", flush=True)
            n = 0
        if n:
            time.sleep(0.01)                     # grosse Rueckstaende in Haeppchen: Anfragen kommen dazwischen dran
            continue
        burst, mark = 0, 2000
        _wake.wait(IDLE)
        _wake.clear()


def start():
    if _thread["t"] is None:
        _thread["stop"] = False
        _thread["t"] = threading.Thread(target=_loop, name="pageindex", daemon=True)
        _thread["t"].start()


def stop():
    _thread["stop"] = True
    _wake.set()
    _thread["t"] = None
