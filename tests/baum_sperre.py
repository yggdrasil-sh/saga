"""Seitenbaum ohne Selbstsperre (Bugs-DB 2636): GET /api/notes?delta=1 raeumt in EINER Anfrage zwei Dinge auf - die
Papierkorb-Frist (_purge_old, in der Transaktion der Anfrage; der Trigger TREE_SEQ_SQL sperrt dabei die note_tseq-Zeilen
der endgueltig geloeschten Seiten) und alte Grabsteine (treesync._prune, eigene Verbindung). Genau diese Grabsteine sind
alt (so lange wie die Seiten im Papierkorb); wartet _prune auf ihre Sperre, haengt die Anfrage fuer immer ("idle in
transaction", stuendlich ein neues Paar, bis der Pool voll ist).

Geprueft: in-Prozess (tree() direkt, wie in der Anfrage) und per HTTP (frischer Server, zweiter Durchgang eine Stunde
spaeter per Testuhr): Antwort < 5 s, danach keine haengende Transaktion und kein Warten auf Sperren; gesperrte Grabsteine
werden uebersprungen, die uebrigen entfernt (Grenze = groesste entfernte Transaktion); fremde Sperren hoechstens kurz
(lock_timeout); die Papierkorb-Frist aus dem Seitenbaum bleibt gespeichert. Haengt etwas (Gegenprobe auf dem alten Stand),
loest die Reihe die Sperre nach HANG s selbst (Abbruch der wartenden Anweisung) und meldet FAIL.
Wegwerf-Konten bs-*, raeumt auf.
Aufruf (saga-test): umgebung.sh lauf server <slot> baum_sperre.py (Server mit SAGA_TEST_HOOKS/SAGA_TEST_TOKEN, innen.sh)."""
import base64, http.client, json, os, socket, threading, time, urllib.parse
from itsdangerous import TimestampSigner
import auth
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
S = TimestampSigner(os.environ["SESSION_SECRET"])
TOK = os.environ.get("SAGA_TEST_TOKEN") or ""
A, BU, CU = "bs-a", "bs-b", "bs-c"
USERS = (A, BU, CU)
FAST, HANG = 5.0, 8.0                 # Antwort spaetestens nach FAST s; nach HANG s gilt sie als haengend (Sperre loesen)
OLD = 31 * 86400 + 60                 # aelter als TRASH_DAYS bzw. TOMB_DAYS
res = []
n500 = [0]


def ok(n, c, detail=None):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)
    if not c and detail is not None:
        print("     ", str(detail)[:1500], flush=True)


def ck(u):
    return S.sign(base64.b64encode(json.dumps({"uid": u, "name": u, "pwc": time.time() + 5}).encode())).decode()


def req(m, p, u=None, body=None, hdr=None, timeout=60):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=timeout)
    h = {"Host": HOST, "Accept-Language": "de", "X-Saga": "1", **(hdr or {})}
    if u:
        h["Cookie"] = "saga=" + ck(u)
    if body is not None:
        h["Content-Type"] = "application/json"
    c.request(m, p, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    if r.status >= 500:
        n500[0] += 1
    return r.status, raw


def clock(**b):
    return req("POST", "/_test/clock", body=b, hdr={"X-Saga-Test": TOK})


def cleanup():
    from saga.userdata import delete_user_data
    for u in USERS:
        if auth.get_user(u):
            delete_user_data(u)
    with connect() as c, c:
        c.execute("DELETE FROM note WHERE uid LIKE 'bs-%'")
        c.execute("DELETE FROM note_tseq WHERE uid LIKE 'bs-%'")
        c.execute("DELETE FROM users WHERE username LIKE 'bs-%'")


def trash(uid, n, tag):
    """n Seiten, seit OLD im Papierkorb, Grabsteine ebenso alt -> ids."""
    now = time.time()
    with connect() as c, c:
        ids = [c.execute("INSERT INTO note (uid, title, body, format, created, updated, deleted_at) VALUES (?,?,?,'blocks',?,?,?)",
                         (uid, f"{tag} {i}", '{"blocks": []}', now - OLD - 100, now - OLD - 50, now - OLD)).lastrowid
               for i in range(n)]
        c.execute("UPDATE note_tseq SET at=? WHERE uid=? AND id = ANY(?)", (now - OLD, uid, ids))
    return ids


def backdate(uid, ids):
    with connect() as c, c:
        c.execute("UPDATE note_tseq SET at = at - ? WHERE uid=? AND id = ANY(?)", (OLD, uid, ids))


def tombs(uid, ids):
    """{id: (gone, at)} der Grabsteine."""
    with connect() as c:
        return {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, gone, at FROM note_tseq WHERE uid=? AND id = ANY(?)", (uid, ids))}


def alive(uid, ids):
    with connect() as c:
        return c.execute("SELECT count(*) FROM note WHERE uid=? AND id = ANY(?)", (uid, ids)).fetchone()[0]


def waiting():
    """Anweisungen in dieser Datenbank, die auf eine Sperre warten -> [(pid, anweisung)]."""
    with connect() as c:
        return c.execute("SELECT pid, left(query, 120) FROM pg_stat_activity WHERE datname=current_database() "
                         "AND pid<>pg_backend_pid() AND wait_event_type='Lock'").fetchall()


def hanging(sec=2):
    """Verbindungen 'idle in transaction' seit mehr als sec Sekunden (ausser dieser) -> [(pid, letzte anweisung)]."""
    with connect() as c:
        return c.execute("SELECT pid, left(query, 120) FROM pg_stat_activity WHERE datname=current_database() "
                         "AND pid<>pg_backend_pid() AND state LIKE 'idle in transaction%' "
                         "AND now() - state_change > make_interval(secs => ?)", (sec,)).fetchall()


def rescue():
    """Haengt etwas (alter Stand): wartende Anweisungen abbrechen - dann laufen die Anfragen zu Ende."""
    w = waiting()
    with connect() as c:
        for pid, _ in w:
            c.execute("SELECT pg_cancel_backend(?)", (pid,))
    return w


def timed(fn):
    """fn() in einem Faden; -> (sekunden, ergebnis oder Ausnahme, hing). Hing er: Sperre loesen, zu Ende laufen lassen."""
    out = {}

    def run():
        try:
            out["r"] = fn()
        except BaseException as ex:  # noqa: BLE001 - Ergebnis des Fadens
            out["r"] = ex
    t0 = time.time()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(HANG)
    hung = th.is_alive()
    if hung:
        print("      haengt - wartend:", rescue(), flush=True)
        th.join(20)
    return time.time() - t0, out.get("r"), hung


def http_tree(uid, q="delta=1"):
    def go():
        try:
            return req("GET", "/api/notes?" + q, uid, timeout=HANG)
        except (socket.timeout, TimeoutError) as ex:
            return ex
    t0 = time.time()
    r = go()
    hung = isinstance(r, Exception)
    if hung:
        print("      haengt - wartend:", rescue(), flush=True)
    return time.time() - t0, r, hung


def quiet(n):
    time.sleep(2.5)                                   # Hintergrund-Arbeit des Servers darf kurz offen sein, nicht laenger
    h, w = hanging(), waiting()
    ok(f"{n}: keine haengende Transaktion, niemand wartet auf eine Sperre", not h and not w, (h, w))


def main():
    from saga.i18n import set_lang
    from saga import notes, treesync
    set_lang("de")
    cleanup()
    for u in USERS:
        auth.create_user(u, "Sperre-Pass-1234", u.upper())
    try:
        # --- A: in-Prozess genau wie die Anfrage (eine Transaktion, Papierkorb-Frist + Aufraeumen) ---------------------
        ids = trash(A, 3, "A")
        ok("A: Grabsteine der Papierkorb-Seiten alt (gone=1)", len(tombs(A, ids)) == 3 and all(
            g == 1 and at < time.time() - OLD + 5 for g, at in tombs(A, ids).values()), tombs(A, ids))
        notes._PURGED.pop(A, None)
        treesync._PRUNED[0] = 0.0

        def tree_a():
            with connect() as c, c:
                return treesync.tree(c, A, delta=True)
        sec, r, hung = timed(tree_a)
        ok(f"A: tree() mit Papierkorb-Frist und Aufraeumen in {sec:.1f} s (< {FAST:.0f} s)", not hung and sec < FAST, (sec, r))
        ok("A: Antwort ist ein Abgleich mit Stand", isinstance(r, dict) and r.get("full") is True and r.get("cursor"), r)
        ok("A: Papierkorb-Seiten endgueltig geloescht", alive(A, ids) == 0)
        t = tombs(A, ids)
        ok("A: ihre (gerade gesperrten) Grabsteine bleiben - neu, gone=1", len(t) == 3 and all(
            g == 1 and at > time.time() - 120 for g, at in t.values()), t)
        quiet("A")

        # --- B: per HTTP, frischer Server; zweiter Durchgang eine Stunde spaeter (Testuhr) ---------------------------
        have_clock = bool(TOK) and clock(reset=True)[0] == 200
        ok("B: Testuhr verfuegbar (SAGA_TEST_HOOKS/SAGA_TEST_TOKEN)", have_clock, TOK and "Token gesetzt, Server antwortet nicht")
        ids1 = trash(BU, 3, "B1")
        if have_clock:
            clock(advance=3700)                       # falls dieser Server schon aufgeraeumt hat: Drossel abgelaufen
        sec, r, hung = http_tree(BU)
        ok(f"B1: GET /api/notes?delta=1 in {sec:.1f} s (< {FAST:.0f} s)", not hung and sec < FAST and r[0] == 200, (sec, r))
        ok("B1: Papierkorb-Frist aus dem Seitenbaum gespeichert (Seiten weg)", alive(BU, ids1) == 0, alive(BU, ids1))
        quiet("B1")
        ids2 = trash(BU, 2, "B2")
        backdate(BU, ids1)                            # Grabsteine aus B1 jetzt alt, nicht gesperrt: muessen weg
        with connect() as c:
            x1 = c.execute("SELECT max(xid) FROM note_tseq WHERE uid=? AND id = ANY(?)", (BU, ids1)).fetchone()[0]
        if have_clock:
            clock(advance=3700)
        sec, r, hung = http_tree(BU, "since=x&rows=0")
        ok(f"B2 (eine Stunde spaeter): GET /api/notes?since=... in {sec:.1f} s (< {FAST:.0f} s)",
           not hung and sec < FAST and r[0] == 200, (sec, r))
        ok("B2: Papierkorb-Seiten weg", alive(BU, ids2) == 0)
        t1, t2 = tombs(BU, ids1), tombs(BU, ids2)
        ok("B2: alte, freie Grabsteine aufgeraeumt; gerade gesperrte uebersprungen", not t1 and len(t2) == 2, (t1, t2))
        with connect() as c:
            hz = c.execute("SELECT v FROM note_tseq_meta WHERE k='horizon'").fetchone()
        ok("B2: Grenze mindestens die groesste entfernte Transaktion", hz and x1 is not None and int(hz[0]) >= int(x1), (hz, x1))
        quiet("B2")
        ids3 = trash(CU, 2, "C")
        t0 = time.time()
        s, raw = req("GET", "/api/notes", CU, timeout=HANG)
        ok(f"B3: GET /api/notes (ohne Parameter) in {time.time() - t0:.1f} s, Papierkorb-Frist gespeichert",
           s == 200 and raw[:1] == b"[" and alive(CU, ids3) == 0, (s, alive(CU, ids3)))

        # --- C: _prune allein - gesperrte Zeilen und fremde Sperren --------------------------------------------------
        ids4 = trash(A, 2, "C")
        with connect() as c, c:                       # wie nach dem Loeschen: Seiten weg, Grabsteine bleiben (alt)
            c.execute("DELETE FROM note WHERE uid=? AND id = ANY(?)", (A, ids4))
        backdate(A, ids4)
        with connect() as c:
            xs = dict(c.execute("SELECT id, xid FROM note_tseq WHERE uid=? AND id = ANY(?)", (A, ids4)).fetchall())
        with connect() as lk:                         # andere Transaktion haelt den ersten Grabstein
            lk.execute("SELECT 1 FROM note_tseq WHERE uid=? AND id=? FOR UPDATE", (A, ids4[0]))
            treesync._PRUNED[0] = 0.0
            sec, r, hung = timed(treesync._prune)
            ok(f"C1: _prune bei gesperrtem Grabstein in {sec:.1f} s, ohne Fehler", not hung and sec < FAST and r is None, (sec, r))
            lk.rollback()
        t = tombs(A, ids4)
        ok("C1: gesperrter Grabstein bleibt, freier entfernt", set(t) == {ids4[0]}, t)
        with connect() as c:
            hz = c.execute("SELECT v FROM note_tseq_meta WHERE k='horizon'").fetchone()
        ok("C1: Grenze >= entfernte Transaktion", hz and int(hz[0]) >= int(xs[ids4[1]]), (hz, xs))
        with connect() as lk:                         # andere Transaktion haelt die Grenze: nur kurz warten, dann aufgeben
            lk.execute("UPDATE note_tseq_meta SET v=v WHERE k='horizon'")
            treesync._PRUNED[0] = 0.0
            sec, r, hung = timed(treesync._prune)
            ok(f"C2: _prune bei gesperrter Grenze gibt nach {sec:.1f} s auf (< {FAST:.0f} s, Sperr-Frist)",
               not hung and sec < FAST and getattr(r, "sqlstate", None) == "55P03", (sec, repr(r)))   # lock_not_available
            lk.rollback()
        ok("C2: dabei nichts entfernt (zurueckgerollt)", set(tombs(A, ids4)) == {ids4[0]}, tombs(A, ids4))
        treesync._PRUNED[0] = 0.0
        sec, r, hung = timed(treesync._prune)
        ok("C3: ohne fremde Sperre: auch der letzte alte Grabstein weg", not hung and r is None and not tombs(A, ids4), (r, tombs(A, ids4)))
        quiet("C")
        ok("Keine Serverfehler (HTTP 5xx) in dieser Reihe", n500[0] == 0, n500[0])
    finally:
        if TOK:
            try:
                clock(reset=True)
            except OSError:
                pass
        rescue()
        cleanup()
        with connect() as c, c:
            c.execute("DELETE FROM note_tseq_meta WHERE k='horizon'")
    n_ok = sum(res)
    print(f"\n{n_ok}/{len(res)} " + ("ALLES GRUEN" if n_ok == len(res) else "FEHLER"))


if __name__ == "__main__":
    main()
