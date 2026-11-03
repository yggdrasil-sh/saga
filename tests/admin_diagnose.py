"""Admin -> Diagnose (2.5, saga/diagstore.py, Migration 32): Diagnose-Meldungen in der Datenbank.

Geprueft: Migration 32 auf frischer Datenbank und von Stand 31 (Tabelle diag_event, Indizes, idempotent), Meldung ueber
POST /api/client/diag -> Eintrag in diag_event (Inhalt = Log-Zeile, ref = Kennung, Konto nur als Hash, keine Namen/IP) und
auf der Admin-Seite, Suche nach D-Kennung (exakter Treffer oben, klein geschrieben auch), Filter (Art, Zeitraum), Detail
(JSON escaped: <script> aus dem Freitext erscheint nie roh), Download (JSONL der Filterung), Aufbewahrung/Obergrenze
(Formular, Grenzen; kuenstlich alte Eintraege -> purge entfernt; Deckel), "Alle loeschen" (CSRF), Schalter diag.clientlog
aus -> nichts gespeichert, Nicht-Admin bzw. ohne Anmeldung -> kein Zugriff (Weiterleitung zur Anmeldung), Hauptport kennt
/diag nicht, Datenbankfehler beim Schreiben (CHECK-Bedingung kurz gesetzt) -> /api/client/diag trotzdem 200.
Achtung: loescht im Lauf alle Diagnose-Meldungen der Test-Datenbank. Wegwerf-Konten ad-<zufall>-*; raeumt auf.
Aufruf: PYTHONPATH=tests/pgshim:. python tests/admin_diagnose.py (Server mit Ausgabe in $STATE_DIR/server.log)."""
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse

from itsdangerous import TimestampSigner
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import psycopg

import auth
import db
from db import connect

B_ = urllib.parse.urlparse(os.environ.get("HK_TEST_BASE", "http://127.0.0.1:8099"))
ADM = urllib.parse.urlparse(os.environ.get("HK_TEST_ADMIN", "http://127.0.0.1:8098"))
HOST = urllib.parse.urlparse(os.environ["BASE_URL"]).netloc
SECRET = os.environ["SESSION_SECRET"]
LOG = os.path.join(os.environ.get("STATE_DIR", "/tmp/hkstage"), "server.log")
WAIT = 5.6
RUN = secrets.token_hex(3)
PFX = f"ad-{RUN}-"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XSS = '<script>alert("xss")</script><img src=x onerror=alert(1)>'
res = []


def ok(n, c):
    res.append(bool(c))
    print(("OK  " if c else "FAIL"), n, flush=True)


def ck(u):
    info = auth.user_info(u, fresh=True)
    return TimestampSigner(SECRET).sign(base64.b64encode(json.dumps({"uid": u, "name": info["name"], "pwc": info["epoch"] or 0}).encode())).decode()


def req(m, p, cookie=None, body=None):
    c = http.client.HTTPConnection(B_.hostname, B_.port, timeout=30)
    h = {"Host": HOST, "Accept-Language": "de"}
    data = None
    if cookie:
        h["Cookie"] = "saga=" + cookie
        h["X-Saga"] = "1"
    if body is not None:
        data = json.dumps(body)
        h["Content-Type"] = "application/json"
    c.request(m, p, body=data, headers=h)
    r = c.getresponse()
    out = r.read()
    try:
        out = json.loads(out)
    except ValueError:
        out = out.decode("utf-8", "replace")
    c.close()
    return r.status, out


def adm_cookie(aid):
    sa = TimestampSigner(hashlib.sha256(("hk-admin|" + SECRET).encode()).hexdigest())
    return sa.sign(base64.b64encode(json.dumps({"aid": aid, "ep": time.time() + 5, "csrf": "x"}).encode())).decode()


def admin(m, path, aid=None, form=None):
    c = http.client.HTTPConnection(ADM.hostname, ADM.port, timeout=30)
    h = {"Accept-Language": "de"}
    if aid:
        h["Cookie"] = "hk_admin=" + adm_cookie(aid)
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form)
        h["Content-Type"] = "application/x-www-form-urlencoded"
    c.request(m, path, body=data, headers=h)
    r = c.getresponse()
    out = r.read().decode("utf-8", "replace")
    loc = r.getheader("Location") or ""
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, out, loc, hd


def head(**kw):
    b = {"v": 1, "app": "web", "ver": "2.5.0-rc.9", "os": "Linux; Chrome 130", "dev": "desktop", "sid": "s" + secrets.token_hex(8),
         "ev": [{"t": 10, "k": "net", "m": "GET", "p": "/api/notes/123", "s": 500, "ms": 40}, {"t": 20, "k": "view", "screen": "pages"}]}
    b.update(kw)
    return b


def rows(where="", args=()):
    with connect() as c:
        return c.execute("SELECT id, ts, kind, ref, acct, client, data FROM diag_event " + where, args).fetchall()


def nrows():
    with connect() as c:
        return c.execute("SELECT count(*) FROM diag_event").fetchone()[0]


def wait_rows(pred, sec=6):
    end = time.time() + sec
    while time.time() < end:
        r = pred()
        if r:
            return r
        time.sleep(0.3)
    return pred()


def logtxt():
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()
    except OSError:
        return None


def cleanup():
    with connect() as c, c:
        for u in [r[0] for r in c.execute("SELECT username FROM users WHERE username LIKE 'ad-%'").fetchall()]:
            c.execute("DELETE FROM user_kv WHERE uid=?", (u,))
            c.execute("DELETE FROM users WHERE username=?", (u,))


# --- Migration (Wegwerf-Datenbanken, eigener Prozess: der Pool dieses Prozesses bleibt bei der Test-Datenbank) ----------
def migration():
    real = db.DATABASE_URL
    base = conninfo_to_dict(real).get("dbname") or "saga"
    names = [f"{base}_adm32a_{RUN}", f"{base}_adm32b_{RUN}"]
    code = ("import db, os; db.init_schema(); c = db.psycopg.connect(db.DATABASE_URL); "
            "print(db.schema_version(c), c.execute(\"SELECT to_regclass('public.diag_event') IS NOT NULL\").fetchone()[0], "
            "sorted(r[0] for r in c.execute(\"SELECT indexname FROM pg_indexes WHERE tablename='diag_event'\").fetchall()))")

    def run(url):
        env = dict(os.environ, DATABASE_URL=url, SAGA_SKIP_BACKUP="1", PYTHONPATH=HERE)
        r = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
        return (r.stdout.strip().splitlines() or [""])[-1], r.stderr[-400:]

    try:
        with psycopg.connect(real, autocommit=True) as c:
            for n in names:
                c.execute(f'DROP DATABASE IF EXISTS "{n}"')
                c.execute(f'CREATE DATABASE "{n}"')
        want = f"{db.SCHEMA_VERSION} True ['diag_event_kind_ts', 'diag_event_pkey', 'diag_event_ref', 'diag_event_ts']"
        out, err = run(make_conninfo(real, dbname=names[0]))
        ok(f"Migration: frische Datenbank -> Stand {db.SCHEMA_VERSION}, diag_event mit Indizes ({out} {err if not out else ''})",
           db.SCHEMA_VERSION >= 32 and out == want)
        # Stand 31 nachbauen: Tabelle weg, Stand 32 aus schema_version
        u2 = make_conninfo(real, dbname=names[1])
        run(u2)
        with psycopg.connect(u2, autocommit=True) as c:
            c.execute("DROP TABLE diag_event")
            c.execute("DELETE FROM schema_version WHERE v >= 32")
            v31 = c.execute("SELECT max(v) FROM schema_version").fetchone()[0]
        out, err = run(u2)
        ok(f"Migration: von Stand {v31} -> {db.SCHEMA_VERSION}, diag_event angelegt ({out} {err if not out else ''})", v31 == 31 and out == want)
        with psycopg.connect(u2, autocommit=True) as c:
            c.execute(db.MIGRATIONS[32])
            c.execute(db.MIGRATIONS[32])
            n = c.execute("SELECT count(*) FROM schema_version WHERE v = 32").fetchone()[0]
        ok(f"Migration 32 beliebig oft ausfuehrbar, genau einmal eingetragen ({n})", n == 1)
    finally:
        with psycopg.connect(real, autocommit=True) as c:
            for n in names:
                c.execute(f'DROP DATABASE IF EXISTS "{n}"')


def main():
    cleanup()
    prev = auth.settings(fresh=True).get("diag")
    auth.save_setting("diag", {"clientlog": True, "keep_days": 14, "max_rows": 50000})
    for k in ("a", "b", "c"):
        auth.create_user(PFX + k, None, {"a": "Anna Probe", "b": "Bert Probe", "c": "Cora Probe"}[k])
    auth.create_user(PFX + "adm", None, "Admin Probe", is_admin=True)
    time.sleep(WAIT)
    try:
        migration()
        run()
    finally:
        try:
            with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:
                c.execute("ALTER TABLE diag_event DROP CONSTRAINT IF EXISTS diag_event_probe")
        except Exception as ex:  # noqa: BLE001
            print("Aufraeumen diag_event:", ex)
        auth.save_setting("diag", prev if isinstance(prev, dict) else {"clientlog": True})
        auth._settings_cache["v"] = None
        cleanup()
    print(f"\nADMIN-DIAGNOSE {sum(res)}/{len(res)}", "- ALLES GRUEN" if all(res) else "- FEHLER")
    sys.exit(0 if all(res) else 1)


def run():
    A, Bc, Cc = ck(PFX + "a"), ck(PFX + "b"), ck(PFX + "c")
    adm = PFX + "adm"
    ok("Standard: diag.keep_days 14, diag.max_rows 50000",
       auth.DEFAULTS["diag"].get("keep_days") == 14 and auth.DEFAULTS["diag"].get("max_rows") == 50000)

    # --- Meldung -> Tabelle ------------------------------------------------------------------------------------------
    s, _, _, _ = admin("POST", "/diag", adm, [("csrf", "x"), ("action", "clear")])
    ok(f"Vorher alles geloescht ({s}, {nrows()})", s == 303 and nrows() == 0)
    n0 = len(logtxt() or "")
    s, d = req("POST", "/api/client/diag", A, head(note="Speichern klappt nicht " + XSS))
    did = d.get("id") if isinstance(d, dict) else None
    ok(f"POST /api/client/diag -> 200 + Kennung ({s}, {did})", s == 200 and re.fullmatch(r"D-[0-9A-Z]{5}", did or ""))
    r = wait_rows(lambda: rows("WHERE ref = ?", (did,)))
    ok(f"Eintrag in diag_event (kind clientdiag, ref = Kennung) ({len(r)})", len(r) == 1 and r[0][2] == "clientdiag")
    e = r[0] if r else (None,) * 7
    data = e[6] if isinstance(e[6], dict) else json.loads(e[6] or "{}")
    line = next((ln for ln in (logtxt() or "")[n0:].splitlines() if ln.startswith("clientdiag ") and did and did in ln), "")
    lrec = json.loads(line[len("clientdiag "):]) if line else None
    ok("Inhalt exakt wie die Log-Zeile (Log-Zeile unveraendert vorhanden)", lrec is not None and lrec == data)
    ok(f"Konto nur als Hash, Client = App + Version ({e[4]}, {e[5]})",
       re.fullmatch(r"[0-9a-f]{10}", e[4] or "") and e[4] == data.get("acct") and e[5] == "web 2.5.0-rc.9")
    flat = json.dumps([list(map(str, e))], ensure_ascii=False)
    ok("keine Namen, kein Benutzername, keine IP-Adresse gespeichert",
       PFX not in flat and "Anna Probe" not in flat and not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", flat))
    with connect() as c:
        cols = [x[0] for x in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='diag_event' ORDER BY 1").fetchall()]
    ok(f"Spalten ohne IP/Name ({cols})", cols == ["acct", "client", "data", "id", "kind", "ref", "ts"])

    # zweite Meldung (anderes Konto), Fehlerbericht clientlog
    s2, d2 = req("POST", "/api/client/diag", Bc, head(app="android", ver="2.5.0", ev=[]))
    s3, _ = req("POST", "/api/client/log", Cc, {"v": 1, "app": "ios", "ver": "2.5.1", "sid": "s" + secrets.token_hex(8),
                                                  "items": [{"k": "error", "msg": "Boom " + XSS, "t": 5}]})
    ok(f"zweite Diagnose + Fehlerbericht angenommen ({s2}, {s3})", s2 == 200 and s3 == 200)
    wait_rows(lambda: nrows() >= 3)
    ok(f"drei Eintraege ({nrows()})", nrows() == 3)

    # --- Admin-Seite --------------------------------------------------------------------------------------------------
    s, page, _, hd = admin("GET", "/diag", adm)
    ok(f"Admin -> Diagnose: 200, Menuepunkt, Kennung in der Tabelle ({s})",
       s == 200 and 'href="/diag"' in page and did in page and "android 2.5.0" in page and "no-store" in hd.get("cache-control", ""))
    ok("Tabelle: <script> aus Client-Daten escaped", "<script>alert" not in page and "&lt;script&gt;" in page and "<img src=x" not in page)
    ok("Hinweistext: was gespeichert wird", "keine Inhalte, Titel, Namen oder IP-Adressen" in page)
    # Kennungssuche (auch klein geschrieben), exakter Treffer oben
    s, page, _, _ = admin("GET", "/diag?q=" + urllib.parse.quote(did.lower()), adm)
    trs = re.findall(r"<tr( class=\"hit\")?>\s*<td data-l=\"Zeit\">", page)
    ok(f"Suche D-Kennung (klein geschrieben): genau ein Treffer, markiert ({s}, {len(trs)})",
       s == 200 and len(trs) == 1 and trs[0] and did in page and (d2.get("id") or "-") not in page)
    s, page, _, _ = admin("GET", "/diag?q=D-ZZZZZ", adm)
    ok("Suche ohne Treffer: Keine Meldungen", s == 200 and "Keine Meldungen." in page)
    acct = e[4] or "-"
    s, page, _, _ = admin("GET", "/diag?q=" + acct, adm)
    ok("Suche nach Konto-Hash findet die Meldung", s == 200 and did in page)
    # Filter Art, Zeitraum
    s, page, _, _ = admin("GET", "/diag?kind=clientlog&since=all", adm)
    ok("Filter Art clientlog: nur der Fehlerbericht", s == 200 and "ios 2.5.1" in page and did not in page)
    with connect() as c, c:
        c.execute("UPDATE diag_event SET ts = ? WHERE ref = ?", (time.time() - 3 * 86400, d2.get("id")))
    s, page, _, _ = admin("GET", "/diag?since=24h", adm)
    ok("Filter Zeitraum 24 h: drei Tage alte Meldung fehlt", s == 200 and did in page and d2.get("id") not in page)
    s, page, _, _ = admin("GET", "/diag?since=7d", adm)
    ok("Filter Zeitraum 7 Tage: wieder da", s == 200 and d2.get("id") in page)
    s, page, _, _ = admin("GET", "/diag?kind=<script>&since=x&page=abc", adm)
    ok(f"unsinnige Filterwerte: 200, ignoriert ({s})", s == 200 and "<script>&" not in page)
    # Detail
    s, page, _, _ = admin("GET", f"/diag?id={e[0]}", adm)
    m = re.search(r'<pre class="adm-json">(.*?)</pre>', page, re.S)
    ok("Detail: formatiertes JSON mit Kennung und Freitext", s == 200 and m and did in m.group(1) and "user_note" in m.group(1))
    ok("Detail: <script> escaped", m and "<script>" not in m.group(1) and "&lt;script&gt;" in m.group(1) and "<script>alert" not in page)
    # Download
    s, body, _, hd = admin("GET", "/diag/export?since=all", adm)
    lines = [json.loads(x) for x in body.splitlines() if x.strip()] if s == 200 else []
    ok(f"Download: JSONL, Datei, alle drei ({s}, {len(lines)}, {hd.get('content-type')})",
       s == 200 and len(lines) == 3 and "attachment" in hd.get("content-disposition", "") and ".jsonl" in hd.get("content-disposition", "")
       and "ndjson" in hd.get("content-type", "") and {x["kind"] for x in lines} == {"clientdiag", "clientlog"})
    s, body, _, _ = admin("GET", "/diag/export?q=" + did, adm)
    lines = [json.loads(x) for x in body.splitlines() if x.strip()]
    ok("Download der Filterung (Kennung): genau diese Meldung, data wie gespeichert",
       len(lines) == 1 and lines[0]["ref"] == did and lines[0]["data"] == data)

    # --- Kein Zugriff -----------------------------------------------------------------------------------------------
    s, page, loc, _ = admin("GET", "/diag", PFX + "a")
    ok(f"Nicht-Admin: Weiterleitung zur Anmeldung, keine Daten ({s}, {loc})", s in (302, 303, 403) and did not in page)
    s, body, loc, _ = admin("GET", "/diag/export?since=all", PFX + "a")
    ok(f"Nicht-Admin: kein Download ({s})", s in (302, 303, 403) and did not in body)
    s, _, _, _ = admin("POST", "/diag", PFX + "a", [("csrf", "x"), ("action", "clear")])
    ok(f"Nicht-Admin: Loeschen wirkt nicht ({s}, {nrows()})", s in (302, 303, 403) and nrows() == 3)
    s, page, _, _ = admin("GET", "/diag")
    ok(f"ohne Anmeldung: kein Zugriff ({s})", s in (302, 303, 403) and did not in page)
    s, out = req("GET", "/diag", A)
    ok(f"Hauptport: keine Diagnose-Seite ({s})", did not in str(out))
    s, out = req("GET", "/diag/export", A)
    ok(f"Hauptport: kein Export ({s})", did not in str(out))

    # --- Aufbewahrung und Obergrenze --------------------------------------------------------------------------------
    s, _, _, _ = admin("POST", "/diag", adm, [("csrf", "falsch"), ("action", "clear")])
    ok(f"falsches CSRF-Token: nichts geloescht ({s}, {nrows()})", nrows() == 3)
    s, _, _, _ = admin("POST", "/diag", adm, [("csrf", "x"), ("action", "save"), ("keep_days", "500"), ("max_rows", "5")])
    st = auth.settings(fresh=True)["diag"]
    ok(f"Einstellung ausserhalb der Grenzen -> geklemmt (90 Tage, 1000 Zeilen) ({st})",
       s == 303 and st.get("keep_days") == 90 and st.get("max_rows") == 1000 and st.get("clientlog") is True)
    s, _, _, _ = admin("POST", "/diag", adm, [("csrf", "x"), ("action", "save"), ("keep_days", "2"), ("max_rows", "1000")])
    st = auth.settings(fresh=True)["diag"]
    ok(f"Aufbewahrung 2 Tage gespeichert; drei Tage alte Meldung sofort entfernt ({st.get('keep_days')}, {nrows()})",
       s == 303 and st.get("keep_days") == 2 and nrows() == 2 and not rows("WHERE ref = ?", (d2.get("id"),)))
    s, page, _, _ = admin("GET", "/diag", adm)
    ok("Formular zeigt die Werte", re.search(r'name="keep_days" value="2"', page) and re.search(r'name="max_rows" value="1000"', page))
    from saga import diagstore
    with connect() as c, c:
        c.execute("UPDATE diag_event SET ts = ? WHERE ref = ?", (time.time() - 2 * 86400 - 60, did))
    n = diagstore.purge()
    ok(f"Ablauf: kuenstlich alte Meldung -> purge entfernt ({n}, {nrows()})", n == 1 and nrows() == 1 and not rows("WHERE ref = ?", (did,)))
    now = time.time()
    with connect() as c, c:
        c.executemany("INSERT INTO diag_event (ts, kind, ref, acct, client, data) VALUES (?, 'calldiag', ?, NULL, 'web', '{}'::jsonb)",
                      [(now - 3600 + i, f"deckel{i:04d}") for i in range(1100)])
    n = diagstore.purge()
    oldest = rows("ORDER BY id ASC LIMIT 1")
    ok(f"Deckel: hoechstens 1000 Zeilen, die aeltesten fallen weg ({n}, {nrows()})",
       n == 101 and nrows() == 1000 and oldest and oldest[0][3] == "deckel0100")
    s, page, _, _ = admin("GET", "/diag?kind=calldiag", adm)
    ok("Seitenweise: 100 je Seite, Link auf aeltere", page.count('<td data-l="Art">') == 100 and "page=2" in page and "Seite 1 von 10" in page)
    s, page2, _, _ = admin("GET", "/diag?kind=calldiag&page=2", adm)
    ok("Seite 2: andere Eintraege, Link zurueck", page2.count('<td data-l="Art">') == 100 and "deckel1099" not in page2 and "deckel0999" in page2 and "Neuere" in page2)

    # --- Alle loeschen --------------------------------------------------------------------------------------------------
    s, page, _, _ = admin("GET", "/diag", adm)
    ok("Knopf 'Alle loeschen' mit Rueckfrage", "Alle löschen" in page and "data-confirm=" in page)
    s, _, _, _ = admin("POST", "/diag", adm, [("csrf", "x"), ("action", "clear")])
    ok(f"Alle loeschen ({s}, {nrows()})", s == 303 and nrows() == 0)

    # --- Schalter aus -> nichts gespeichert -------------------------------------------------------------------------------
    auth.save_setting("diag", {**auth.settings(fresh=True)["diag"], "clientlog": False})
    time.sleep(WAIT)
    s1, _ = req("POST", "/api/client/diag", Cc, head())
    s2, _ = req("POST", "/api/client/log", Cc, {"v": 1, "app": "web", "ver": "1", "sid": "s" + secrets.token_hex(8),
                                                 "items": [{"k": "error", "msg": "x", "t": 1}]})
    time.sleep(2.0)
    ok(f"diag.clientlog aus: 404, nichts gespeichert ({s1}, {s2}, {nrows()})", s1 == 404 and s2 == 404 and nrows() == 0)
    s, page, _, _ = admin("GET", "/diag", adm)
    ok("Seite zeigt: abgeschaltet", "Abgeschaltet" in page)
    auth.save_setting("diag", {**auth.settings(fresh=True)["diag"], "clientlog": True})
    time.sleep(WAIT)

    # --- Datenbankfehler beim Schreiben bricht die Anfrage nicht ab ---------------------------------------------------------
    n0 = len(logtxt() or "")
    with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:      # db.Conn ueberspringt ALTER
        c.execute("ALTER TABLE diag_event ADD CONSTRAINT diag_event_probe CHECK (kind <> 'clientdiag') NOT VALID")
    try:
        t0 = time.time()
        s, d = req("POST", "/api/client/diag", Cc, head())
        dt = time.time() - t0
        ok(f"Schreiben scheitert: /api/client/diag trotzdem 200 + Kennung ({s}, {dt:.2f} s)", s == 200 and str(d.get("id", "")).startswith("D-"))
        time.sleep(2.5)
        txt = (logtxt() or "")[n0:]
        ok("Server-Log: Meldung nicht gespeichert (kein Absturz), Log-Zeile clientdiag trotzdem da",
           "diagstore:" in txt and "nicht gespeichert" in txt and f'"id":"{d.get("id")}"' in txt and "Traceback" not in txt)
    finally:
        with psycopg.connect(db.DATABASE_URL, autocommit=True) as c:
            c.execute("ALTER TABLE diag_event DROP CONSTRAINT IF EXISTS diag_event_probe")
    s, d = req("POST", "/api/client/diag", Bc, head())
    r = wait_rows(lambda: rows("WHERE ref = ?", (d.get("id"),)))
    ok(f"danach wird wieder gespeichert ({s}, {len(r)})", s == 200 and len(r) == 1)
    admin("POST", "/diag", adm, [("csrf", "x"), ("action", "clear")])


if __name__ == "__main__":
    main()
